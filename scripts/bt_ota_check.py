#!/usr/bin/env python3
"""Stage 2's receive side: record the VSG60's burst train on the BB60D, and grade it.

    python scripts/bt_ota_check.py synth_ota_dh5_tx.json --record 3 --out ota.cf32
    python scripts/bt_ota_check.py synth_ota_dh5_tx.json --iq ota.cf32

``scripts/bt_tx.py`` sends a loop of known packets and writes its sidecar;
this records the BB60D at the same centre and rate (or reads a recording) and
says how the packets came through, burst by burst, against that truth:

* **found** - access codes matched, against the number the loop should have
  put in that time;
* **identified** - which burst of the loop each one is, by fewest bit errors;
* **bits** - raw errors from a plain discriminator sliced at fixed timing,
  with the threshold taken from the access code's known bits;
* **libbtbb** - headers taken at the true UAP and clock, payloads whose CRC
  passes, and payloads equal byte for byte to the truth;
* **the radios** - the carrier offset, the sample-clock offset in ppm across
  all identified bursts, the timing jitter left after it, and SNR in the
  channel.

The demodulator is deliberately plain, with no timing recovery, so the
numbers say what the link delivers rather than what a good receiver could
recover from it. The BB60D and the VSG60 must be on different machines
([knowledge/radios.md](../knowledge/radios.md#vsg60-notes)).
"""
import argparse
import collections
import ctypes
import ctypes.util
import json
import os
import sys
import time

import numpy as np
from scipy.signal import fftconvolve, firwin, lfilter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SPS = 20


def record(path, seconds, center_hz, fs, gain_percent):
    """Record the BB60D to a cf32 file through ``apps/bb60_source``."""
    from gnuradio import blocks, gr
    from apps import bb60_source as bb
    bb.ensure_plugin_path()
    bb.reset_overflows()
    src = bb.bb60_source(center_hz, fs, gain_percent=gain_percent)
    tb = gr.top_block()
    tb.connect(src, blocks.head(gr.sizeof_gr_complex, int(seconds * fs)),
               blocks.file_sink(gr.sizeof_gr_complex, path))
    t0 = time.time()
    tb.run()
    return {'recorded_s': seconds, 'record_start_unix': t0,
            'gain_percent': gain_percent, 'adc_overflows': bb.overflow_count()}


def libbtbb():
    path = ctypes.util.find_library('btbb')
    if not path:
        return None
    lib = ctypes.CDLL(path)
    vp = ctypes.c_void_p
    for name, res, args in (
            ('btbb_init', ctypes.c_int, [ctypes.c_int]),
            ('btbb_packet_new', vp, []),
            ('btbb_packet_unref', None, [vp]),
            ('btbb_packet_set_data', None, [vp, ctypes.POINTER(ctypes.c_char),
                                            ctypes.c_int, ctypes.c_uint8, ctypes.c_uint32]),
            ('btbb_packet_set_uap', None, [vp, ctypes.c_uint8]),
            ('btbb_packet_set_flag', None, [vp, ctypes.c_int, ctypes.c_int]),
            ('btbb_decode_header', ctypes.c_int, [vp]),
            ('btbb_decode_payload', ctypes.c_int, [vp]),
            ('btbb_packet_get_header_packed', ctypes.c_uint32, [vp]),
            ('btbb_get_payload_packed', ctypes.c_int, [vp, ctypes.POINTER(ctypes.c_char)])):
        fn = getattr(lib, name)
        fn.restype, fn.argtypes = res, args
    lib.btbb_init(2)
    return lib


def check_btbb(lib, bits, burst, uap):
    """(header taken, CRC passes, payload exact) for one burst's bits."""
    raw = bytes(bits[4:].tolist())
    pkt = lib.btbb_packet_new()
    try:
        lib.btbb_packet_set_data(pkt, ctypes.cast(ctypes.c_char_p(raw),
                                 ctypes.POINTER(ctypes.c_char)), len(raw), 0,
                                 ((burst['clk'] >> 1) & 0x3F) << 1)
        lib.btbb_packet_set_uap(pkt, uap)
        lib.btbb_packet_set_flag(pkt, 4, 1)          # CLK6 valid
        lib.btbb_packet_set_flag(pkt, 0, 1)          # whitened
        if (lib.btbb_decode_header(pkt) != 1 or
                lib.btbb_packet_get_header_packed(pkt) & 0x3FFFF != burst['header18']):
            return False, False, False
        if not burst['payload_full_hex']:
            return True, True, True
        if lib.btbb_decode_payload(pkt) != 10:
            return True, False, False
        want = bytes.fromhex(burst['payload_full_hex'])
        buf = (ctypes.c_char * (len(want) + 32))()
        lib.btbb_get_payload_packed(pkt, buf)
        return True, True, bytes(buf[:len(want)]) == want
    finally:
        lib.btbb_packet_unref(pkt)


def grade(iq, side):
    fs = side['sample_rate']
    air = [np.array([int(c) for c in b['air_bits']]) for b in side['bursts']]
    n = np.arange(len(iq))
    off = (side['channel_mhz'] - side['center_mhz']) * 1e6
    bb = lfilter(firwin(101, 0.7e6, fs=fs), 1, iq * np.exp(-2j * np.pi * off * n / fs))
    d = np.angle(bb[1:] * np.conj(bb[:-1]))
    env = np.abs(bb) ** 2
    # Access codes, matched on the discriminator so carrier phase and a
    # modest frequency offset do not matter; every burst shares one.
    a72 = air[0][:72] * 2 - 1
    tmpl = np.repeat(a72, SPS).astype(float)
    tmpl -= tmpl.mean()
    dm = d - np.convolve(d, np.ones(2001) / 2001, 'same')
    c = fftconvolve(dm, tmpl[::-1], 'valid')
    nrm = np.sqrt(fftconvolve(dm ** 2, np.ones(len(tmpl)), 'valid')) * np.linalg.norm(tmpl)
    r = c / np.maximum(nrm, 1e-12)
    peaks = []
    for j in np.where(r > 0.5)[0]:
        if peaks and j - peaks[-1] < side['slot_samples']:
            if r[j] > r[peaks[-1]]:
                peaks[-1] = j
        else:
            peaks.append(j)

    lib = libbtbb()
    stats = collections.Counter()
    rows = []
    length = len(air[0])
    for p in peaks:
        cen = p + (np.arange(length) + 0.5) * SPS - 0.5
        if cen[-1] >= len(d) - 1 or p < 12000:
            continue
        f = np.interp(cen, np.arange(len(d)), d)
        slope, thr = np.polyfit(a72, f[:72], 1)
        bits = (f > thr).astype(int)
        errs = [int((bits != a).sum()) for a in air]
        k = int(np.argmin(errs))
        on = env[p:p + length * SPS].mean()
        gap = np.median(env[p - 12000:p - 5000])
        rows.append((p, k, errs[k], thr / (2 * np.pi) * fs, 10 * np.log10(max(on / gap - 1, 1e-3))))
        if lib is not None:
            h, crc, exact = check_btbb(lib, bits, side['bursts'][k], side['uap'])
            stats['header'] += h
            stats['crc'] += crc
            stats['exact'] += exact
    rows = np.array(rows)
    out = {'found': len(peaks), 'graded': len(rows),
           'expected': int(round(len(iq) / side['loop_samples'] * len(side['bursts'])))}
    if not len(rows):
        return out
    e = rows[:, 2]
    out.update(bit_errors_median=float(np.median(e)), zero_error_bursts=int((e == 0).sum()),
               raw_ber=float(e.sum() / (len(e) * length)),
               cfo_hz_median=float(np.median(rows[:, 3])),
               snr_db_median=float(np.median(rows[:, 4])))
    if lib is not None:
        out.update(libbtbb_header=stats['header'], libbtbb_crc=stats['crc'],
                   libbtbb_exact=stats['exact'])
    # The sample clock: where each identified burst landed against where the
    # loop put it, unwrapped across repeats.
    good = rows[e <= 50]
    if len(good) > 2:
        starts = np.array([b['start_sample'] for b in side['bursts']])
        nom = starts[good[:, 1].astype(int)]
        loops = np.round((good[:, 0] - nom - (good[0, 0] - nom[0])) / side['loop_samples'])
        nominal = nom + loops * side['loop_samples']
        slope, icpt = np.polyfit(nominal, good[:, 0], 1)
        out.update(clock_ppm=float((slope - 1) * 1e6),
                   timing_jitter_samples=float(np.std(good[:, 0] - (slope * nominal + icpt))))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('sidecar', help="bt_tx.py's synth_<name>_tx.json")
    ap.add_argument('--iq', help='grade this recording instead of making one')
    ap.add_argument('--record', type=float, help='seconds to record from the BB60D')
    ap.add_argument('--out', help='where --record writes its cf32')
    ap.add_argument('--gain', type=float, default=60.0, help='BB60D gain, percent')
    args = ap.parse_args()
    side = json.load(open(args.sidecar))
    info = {}
    path = args.iq
    if args.record:
        path = args.out or 'ota.cf32'
        info = record(path, args.record, side['center_mhz'] * 1e6,
                      side['sample_rate'], args.gain)
        print('recorded %.1f s at %.0f%% gain, ADC overflows %d -> %s'
              % (args.record, args.gain, info['adc_overflows'], path))
    if not path:
        ap.error('give --iq or --record')
    iq = np.fromfile(path, '<c8')
    result = grade(iq, side)
    result.update(info)
    for k, v in result.items():
        print('  %-22s %s' % (k, round(v, 4) if isinstance(v, float) else v))
    return result


if __name__ == '__main__':
    main()
