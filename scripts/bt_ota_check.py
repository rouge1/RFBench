#!/usr/bin/env python3
"""Stage 2's receive side: record the VSG60's burst train on the BB60D, and grade it.

    python scripts/bt_ota_check.py synth_ota_dh5_tx.json --record 3 --out ota.cf32
    python scripts/bt_ota_check.py synth_ota_dh5_tx.json --iq ota.cf32
    python scripts/bt_ota_check.py synth_ota_dh5_tx.json --record 30 --capture ota_dh5_0dbm

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

``--capture NAME`` records into bluey-ox-walker's ``data/iq`` and writes
``data/sidecar/NAME.json``: every burst sent while it ran, placed by the
clock fitted to the bursts found, in the synthetic files' format, so the
recording grades like one of them.

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
from apps import bt_br_frame as br  # noqa: E402
from scripts import bt_synth  # noqa: E402

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


#: Samples from a burst's first preamble sample to the correlation peak that
#: finds it, on average. The peak is an integer, the 101-tap filter's group
#: delay of 50 after the whole sample the burst starts in, so over starts whose
#: fractions are spread evenly it is half a sample less: 49.5. A fit over many
#: bursts removes the scatter, not this. (A 30 dB reference with every start on
#: a whole sample gives about 49.65.)
DETECT_DELAY = 49.5

#: Graded a chunk at a time, overlapping each side by more than a DH5 and the
#: noise before it, so a 30 s recording fits in memory.
CHUNK = 40_000_000
OVERLAP = 100_000


def _chunk(iq, base, side, air, lib, stats, rows, own_from, own_to):
    """Find and grade the bursts in one chunk, where ``iq`` starts at sample
    ``base`` of the recording; keep those whose peak lies in ``[own_from,
    own_to)``, so the overlap at each end counts every burst once."""
    fs = side['sample_rate']
    if len(iq) < SPS * len(air[0][:72]) + 2002:     # less than a template and its average
        return
    n = base + np.arange(len(iq))
    off = (side['channel_mhz'] - side['center_mhz']) * 1e6
    bb = lfilter(firwin(101, 0.7e6, fs=fs), 1,
                 iq * np.exp(-2j * np.pi * off * n / fs).astype(np.complex64))
    d = np.angle(bb[1:] * np.conj(bb[:-1]))
    env = np.abs(bb) ** 2
    del bb
    # Access codes, matched on the discriminator so carrier phase and a
    # modest frequency offset do not matter; every burst shares one.
    a72 = air[0][:72] * 2 - 1
    tmpl = np.repeat(a72, SPS).astype(float)
    tmpl -= tmpl.mean()
    dm = d - np.convolve(d, np.ones(2001) / 2001, 'same')
    c = fftconvolve(dm, tmpl[::-1], 'valid')
    nrm = np.sqrt(fftconvolve(dm ** 2, np.ones(len(tmpl)), 'valid')) * np.linalg.norm(tmpl)
    r = c / np.maximum(nrm, 1e-12)
    del c, nrm, dm
    peaks = []
    for j in np.where(r > 0.5)[0]:
        if peaks and j - peaks[-1] < side['slot_samples']:
            if r[j] > r[peaks[-1]]:
                peaks[-1] = j
        else:
            peaks.append(j)
    length = len(air[0])
    for p in peaks:
        if not own_from <= base + p < own_to:
            continue
        stats['found'] += 1
        cen = p + (np.arange(length) + 0.5) * SPS - 0.5
        if cen[-1] >= len(d) - 1 or p < 12000:
            stats['ungraded'] += 1
            continue
        f = np.interp(cen, np.arange(len(d)), d)
        slope, thr = np.polyfit(a72, f[:72], 1)
        bits = (f > thr).astype(int)
        errs = [int((bits != a).sum()) for a in air]
        k = int(np.argmin(errs))
        on = env[p:p + length * SPS].mean()
        gap = np.median(env[p - 12000:p - 5000])
        h = crc = exact = False
        if lib is not None:
            h, crc, exact = check_btbb(lib, bits, side['bursts'][k], side['uap'])
            stats['header'] += h
            stats['crc'] += crc
            stats['exact'] += exact
        rows.append((base + p, k, errs[k], thr / (2 * np.pi) * fs,
                     10 * np.log10(max(on / gap - 1, 1e-3)), h, crc, exact))


def grade(iq, side):
    """Grade a recording; returns the summary and every graded burst."""
    air = [np.array([int(c) for c in b['air_bits']]) for b in side['bursts']]
    if OVERLAP < 12000 + len(air[0]) * SPS:
        raise ValueError('OVERLAP must cover the 12000-sample guard and a whole burst')
    lib = libbtbb()
    stats = collections.Counter()
    rows = []
    for own_from in range(0, len(iq), CHUNK):
        # Start OVERLAP early, so a burst just past a boundary has the quiet
        # before it that the grader reads, and is not lost between chunks.
        base = max(0, own_from - OVERLAP)
        own_to = own_from + CHUNK
        _chunk(np.asarray(iq[base:own_to + OVERLAP]), base, side, air, lib,
               stats, rows, own_from, own_to)
    rows = np.array(rows, dtype=float)
    length = len(air[0])
    out = {'found': stats['found'], 'graded': len(rows), 'ungraded': stats['ungraded'],
           'expected': int(round(len(iq) / side['loop_samples'] * len(side['bursts'])))}
    if not len(rows):
        return out, rows, None
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
    fit = None
    good = rows[e <= 50]
    if len(good) > 2:
        starts = np.array([b['start_sample'] for b in side['bursts']])
        nom = starts[good[:, 1].astype(int)]
        loops = np.round((good[:, 0] - nom - (good[0, 0] - nom[0])) / side['loop_samples'])
        nominal = nom + loops * side['loop_samples']
        slope, icpt = np.polyfit(nominal, good[:, 0], 1)
        if abs(slope - 1) > 1e-4:       # 100 ppm: the VSG60 and BB60D are within a few
            out['fit_rejected_ppm'] = float((slope - 1) * 1e6)
            return out, rows, None
        out.update(clock_ppm=float((slope - 1) * 1e6),
                   timing_jitter_samples=float(np.std(good[:, 0] - (slope * nominal + icpt))))
        fit = (slope, icpt)
    return out, rows, fit


def capture_sidecar(side, n_samples, rows, fit, result, info, name):
    """The truth of a recording, burst by burst, in the files' format.

    Every whole burst the loop sent while the recording ran is listed,
    found or not - one cut off by either end is left out. Where it starts
    comes from the fitted clock, not from the detector, so a burst the plain
    demodulator here missed is still in the truth. ``start_sample`` is the
    nearest sample, so with ``timing_frac`` 0 the file reads like the
    synthetic ones; ``start_exact`` is the fitted start to a thousandth.
    ``clk`` counts loops from the first one in the recording; only CLK6-1
    matters on a fixed channel, and it is exact, but the higher bits are
    nominal - the VSG60 started its loop at no particular clock.
    """
    slope, icpt = fit
    L = side['loop_samples']
    starts = [b['start_sample'] for b in side['bursts']]
    graded = {}
    for row in rows:
        graded[int(round(row[0]))] = row
    span = len(side['bursts'][0]['air_bits']) * SPS
    first_loop = int(np.floor((-icpt / slope - max(starts)) / L))
    placed = []
    m = first_loop
    while slope * (m * L) + icpt - DETECT_DELAY <= n_samples:
        for k in range(len(starts)):
            pos = slope * (starts[k] + m * L) + icpt - DETECT_DELAY
            if 0 <= pos and pos + span <= n_samples:     # whole bursts only
                placed.append((m, k, pos))
        m += 1
    if not placed:
        return None
    m0 = min(m for m, _, _ in placed)
    bursts = []
    for m, k, pos in placed:
        b = side['bursts'][k]
        q = br.Packet(side['lap'], side['uap'],
                      (b['clk'] + side['clk_per_loop'] * (m - m0)) & 0x0FFFFFFF,
                      side['ptype'], bytes.fromhex(b['payload_hex']), lt_addr=b['lt_addr'],
                      flow=b['flow'], arqn=b['arqn'], seqn=b['seqn'])
        nearest = int(np.floor(pos + 0.5))
        entry = {'start_sample': nearest, 'start_exact': round(float(pos), 3),
                 'symbol_phase': nearest % SPS, 'loop_index': k}
        entry.update(q.sidecar())
        near = [g for g in graded if abs(g - pos - DETECT_DELAY) < 40]
        if near:
            row = graded[near[0]]
            entry.update(found=True, bit_errors=int(row[2]), cfo_hz=round(float(row[3]), 1),
                         snr_db=round(float(row[4]), 1), libbtbb_header=bool(row[5]),
                         libbtbb_crc=bool(row[6]), libbtbb_exact=bool(row[7]))
        else:
            entry['found'] = False
        bursts.append(entry)
    bursts.sort(key=lambda x: x['start_sample'])
    out = {k: side[k] for k in ('lap', 'uap', 'channel_mhz', 'bt_channel', 'sample_rate',
                                'center_mhz', 'modulation', 'slot_samples', 'ptype',
                                'vsg_level_dbm', 'vsg_serial', 'vsg_reference')
           if k in side}
    out.update({
        'generator': 'SDR scripts/bt_ota_check.py, from scripts/bt_tx.py over the air',
        'generator_commit': bt_synth.commit(),
        'tx_generator_commit': side.get('generator_commit'),
        'name': name,
        'over_the_air': True,
        'clk': bursts[0]['clk'] if bursts else None,
        'clk_convention': 'native CLK[27:0] at the first sample of the burst; '
                          'CLK6-1 is exact, the higher bits nominal',
        'timing_frac': 0.0,
        'start_sample_meaning': "the sample nearest the access code's preamble's first "
                                'sample, from the clock fitted to every identified burst; '
                                'start_exact in each burst is the fit itself',
        'cfo_hz': round(result['cfo_hz_median'], 1),
        'snr_db': round(result['snr_db_median'], 1),
        'snr_bw_hz': 1e6,
        'clock_ppm': round(result['clock_ppm'], 4),
        'symbol_phase': None,
        'symbol_phase_note': 'drifts with clock_ppm, so each burst has its own: start_sample % 20',
        'measured': {k: v for k, v in result.items()},
        'receiver': {'radio': 'BB60D', 'gain_percent': info.get('gain_percent'),
                     'adc_overflows': info.get('adc_overflows'),
                     'record_start_unix': info.get('record_start_unix')},
        'bursts': bursts,
    })
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('sidecar', help="bt_tx.py's synth_<name>_tx.json")
    ap.add_argument('--iq', help='grade this recording instead of making one')
    ap.add_argument('--record', type=float, help='seconds to record from the BB60D')
    ap.add_argument('--out', help='where --record writes its cf32')
    ap.add_argument('--gain', type=float, default=None,
                    help='BB60D gain, percent: 60 when recording; with --iq, the gain it was '
                         'recorded at, which goes into the truth only if given')
    ap.add_argument('--capture', metavar='NAME',
                    help="write the recording's truth to bluey-ox-walker's data/sidecar/"
                         "NAME.json; with --record, the recording goes to data/iq/NAME.cf32 too")
    args = ap.parse_args()
    side = json.load(open(args.sidecar))
    info = {}
    if args.iq and args.record:
        ap.error('--iq and --record are two ways to get a recording: give one')
    path = args.iq
    if args.record:
        path = (os.path.join(bt_synth.BLUEY, 'data', 'iq', args.capture + '.cf32')
                if args.capture else args.out or 'ota.cf32')
        info = record(path, args.record, side['center_mhz'] * 1e6,
                      side['sample_rate'], 60.0 if args.gain is None else args.gain)
        print('recorded %.1f s at %.0f%% gain, ADC overflows %d -> %s'
              % (args.record, info['gain_percent'], info['adc_overflows'], path))
    if not path:
        ap.error('give --iq or --record')
    iq = np.memmap(path, '<c8', 'r')
    result, rows, fit = grade(iq, side)
    if args.gain is not None:
        info.setdefault('gain_percent', args.gain)
    result.update(info)
    for k, v in result.items():
        print('  %-22s %s' % (k, round(v, 4) if isinstance(v, float) else v))
    if args.capture:
        if fit is None:
            print('no clock fit%s: no truth sidecar written'
                  % (' (%.0f ppm is not a clock)' % result['fit_rejected_ppm']
                     if 'fit_rejected_ppm' in result else ''))
            return result
        truth = capture_sidecar(side, len(iq), rows, fit, result, info, args.capture)
        if truth is None:
            print('no whole burst in the recording: no truth sidecar written')
            return result
        out = os.path.join(bt_synth.BLUEY, 'data', 'sidecar', args.capture + '.json')
        with open(out, 'w') as f:
            json.dump(truth, f, indent=1)
        print('%s: %d bursts sent, %d found' % (out, len(truth['bursts']),
                                                sum(b['found'] for b in truth['bursts'])))
    return result


if __name__ == '__main__':
    main()
