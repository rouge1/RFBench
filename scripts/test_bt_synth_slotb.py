#!/usr/bin/env python3
"""Hold the slot-boundary file writer to what its samples and the definitions say.

    python scripts/test_bt_synth_slotb.py

``scripts/bt_synth_slotb.py`` writes files for bluey-ox-walker's slot tracker: single-slot
exchanges and page-train IDs whose transmitter slot grid sits at a stated offset from the file's own
625 us grid, drifts against it by 23 ppm (20 for the CFO file), and whose IDs end either cleanly (C1) or with a held tail
(C2). The truth of a file is only worth anything if it is the truth of its *samples*, so every check
here is made on small renders (0.25 to 0.6 s, a few noise blocks, 35 dB so that the start of a burst
can be fitted to a few hundredths of a sample) and measures the samples; the rules are written out
again here, never taken from the generator's own bookkeeping:

* **start**: each burst's model, built from the sidecar's own fields with the repository's
  modulator, is fitted to the samples at the burst's channel (a sub-sample scan around an
  integer matched filter); the fit is ``start_sample + timing_frac`` within 0.1 sample, and
  ``slot_start_us``, ``grid_offset_us``, ``start_us`` and ``start_mod_625_us`` are recomputed from the
  measured start and the burst's place in its slot;
* **drift**: a straight line through the measured TX-slot starts against the slot index gives
  ``drift_ppm_signed`` to within 1 ppm, and the crossing of 0 (or 625) found from that line is the
  sidecar's ``crossings``, in time and in the burst index the grid offset wraps at; the fixed files
  have one ``grid_offset_us``;
* **slot geometry**: a second ID is its first ID + 312.5 us, a slave answer its master + 625 us;
* **decode**: bits demodulated from the samples are the packet's; libbtbb decodes the header and
  payload at the sidecar's UAP and ``clk`` and does not at ``clk`` +/- 2 (one slot, the whitening
  clock); ``header_air_bits_54`` are the 54 bits demodulated;
* **IDs**: the 68 bits are the preamble and sync word of the LAP, a C1 burst is gone after its ramp,
  a C2 tail has the stated length, frequency (+160 kHz or +50 kHz) and level relative to the sync word;
* **plan**: the page channels are ``apps/bt_hop_substates``' for each ID's clock, the A and B trains
  alternate every 1.28 s block, 39 and 43 are in the first train, no centre channel anywhere, IDs out
  of the window are in the truth and not in the samples; the D files continue one grid and one clock;
* **file**: no two bursts overlap, sample counts, noise is the file's one white floor to the bit
  (the file less its bursts is the regenerated noise), byte-identical regeneration, the CFO file;
* mutants of the generator, each caught.

Needs libbtbb (``bt_ota_check.libbtbb``); the checks that need it are skipped, and said so, without it.
"""
import contextlib
import ctypes
import hashlib
import json
import math
import os
import sys
import time
from fractions import Fraction
from unittest import mock

os.environ.setdefault('OMP_NUM_THREADS', '4')
import numpy as np  # noqa: E402
from scipy.signal import fftconvolve, firwin, welch  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from apps import bt_hop_substates as hs  # noqa: E402
from scripts import bt_ota_check, bt_synth  # noqa: E402
from scripts import bt_synth_slotb as g  # noqa: E402
from scripts.bt_synth_interf import noise_block  # noqa: E402

NOISE = bt_synth.AMPLITUDE ** 2 / 100          # noise power in 1 MHz, the common floor
HIGH = 45.0                                    # dB: the level the fits are made at (0.02 sample at this level)
RESULTS = []
QUIET = [False]
_lib = bt_ota_check.libbtbb()
SLOT_US = 625.0


def check(ok, what):
    ok = bool(ok)
    RESULTS.append((ok, what))
    if not QUIET[0]:
        print('  %s %s' % ('ok  ' if ok else 'FAIL', what), flush=True)
    return ok


@contextlib.contextmanager
def quiet():
    """Collect the checks of a block without printing; yields the list of those that failed."""
    QUIET[0] = True
    mark = len(RESULTS)
    failed = []
    try:
        yield failed
    finally:
        QUIET[0] = False
        failed.extend(w for ok, w in RESULTS[mark:] if not ok)
        del RESULTS[mark:]


def circ(a, b, period=SLOT_US):
    """a - b on a circle of ``period``, in (-period/2, period/2]."""
    return (a - b + period / 2) % period - period / 2


# --- reading samples --------------------------------------------------------------------------------

def shifted(iq, lo, hi, mhz, center, fs, extra_hz=0.0):
    """Samples lo:hi taken down by the channel's carrier (and ``extra_hz``), the whole cycles removed first."""
    n = np.arange(lo, hi)
    cycles = ((mhz - center) * 1e6 + extra_hz) / fs * n
    return np.asarray(iq[lo:hi]).astype(np.complex128) * np.exp(-2j * np.pi * (cycles - np.floor(cycles)))


def model_bits(e):
    """The bits of the model of a burst, from the sidecar's fields: the 68 of an ID (and, for a C2 tail, the 50
    symbols of the stated deviation), a whole packet else."""
    if e['ptype'] == 'ID':
        bits = [float(b) for b in br.access_code(e['lap'], trailer=False)]
        if e['tail']['variant'] == 'C2':
            nrz = e['tail']['dev_khz'] * 1e3 / (br.GFSK_H / 2 * br.SYMBOL_RATE)
            bits += [(1 + nrz) / 2] * int(e['tail']['dur_us'])
        return np.array(bits, dtype=float)
    p = br.Packet(e['lap'], e['uap'], e['clk'], e['ptype'], bytes.fromhex(e['payload_hex']), lt_addr=e['lt_addr'],
                  flow=e['flow'], arqn=e['arqn'], seqn=e['seqn'])
    return np.array(p.bits, dtype=float)


def model_signal(e, bits, fs, delay):
    """The burst's unit-amplitude model from the sidecar's own fields, with the C2 step ramp (2 us raised cosine
    from the end of symbol 68 to level_db)."""
    m, lead = br.gfsk(bits, fs, h=br.GFSK_H, delay=delay)
    if e['ptype'] == 'ID' and e['tail']['variant'] == 'C2':
        t = (np.arange(-lead, -lead + len(m)) - delay) / (fs / 1e6)
        x = np.clip((t - 68.0) / e['tail']['ramp_us'], 0.0, 1.0)
        g_ = 10 ** (e['tail']['level_db'] / 20)
        m = m * (1 + (g_ - 1) * (0.5 - 0.5 * np.cos(np.pi * x)))
    return m, lead


def extra_hz_of(e):
    return float(e.get('cfo_hz') or 0.0)


def fit(iq, side, e, window=1500):
    """The burst's model fitted to its samples: start (a real sample position), amplitude, carrier phase and the
    carrier error in Hz. The search is +/- ``window`` samples around the sidecar's position, an integer matched
    filter first and then tenths of a sample with a parabola through the best three scores."""
    fs, center = side['sample_rate'], side['center_mhz']
    sps = fs / 1e6
    bits = model_bits(e)
    m0, lead = model_signal(e, bits, fs, 0.0)
    hint = e['start_sample']
    lo = max(0, hint - window - lead)
    hi = min(len(iq), hint + window + int(len(bits) * sps) + 2 * lead)
    z = shifted(iq, lo, hi, e['channel_mhz'], center, fs, extra_hz_of(e))
    r = fftconvolve(z, np.conj(m0[::-1]), 'valid')
    i0c = lo + int(np.argmax(np.abs(r))) + lead
    scores = {}
    for k in range(-6, 7):
        s = i0c + k * 0.1
        i0 = math.floor(s)
        m, ld = model_signal(e, bits, fs, s - i0)
        a = i0 - ld - lo
        seg = z[a:a + len(m)]
        if a < 0 or len(seg) < len(m):
            continue
        scores[k] = abs(np.vdot(m, seg)) / math.sqrt(np.vdot(m, m).real)
    kb = max(scores, key=scores.get)
    delta = 0.0
    if kb - 1 in scores and kb + 1 in scores:
        y0, y1, y2 = scores[kb - 1], scores[kb], scores[kb + 1]
        den = y0 - 2 * y1 + y2
        delta = 0.5 * (y0 - y2) / den if den else 0.0
    start = i0c + (kb + delta) * 0.1
    i0 = math.floor(start)
    m, ld = model_signal(e, bits, fs, start - i0)
    a = i0 - ld - lo
    seg = z[a:a + len(m)]
    gain = np.vdot(m, seg) / np.vdot(m, m)
    prod = seg * np.conj(m)
    lag = 400
    df = float(np.angle(np.sum(prod[lag:] * np.conj(prod[:-lag]))) / (2 * np.pi * lag) * fs)
    return dict(start=float(start), amp=float(abs(gain)), phase=float(np.angle(gain)), df=df)


def taps_for(fs):
    n = int(round(401 * fs / 40e6)) | 1
    return firwin(n, 0.7e6, fs=fs)


def demod(iq, side, e, start, nbits):
    """``nbits`` bits demodulated from the samples with the burst at ``start`` (a real position): the channel's
    carrier removed, a 0.7 MHz low pass, a discriminator sampled at the symbol centres, the threshold fitted on
    the 68 known bits of the access code."""
    fs, center = side['sample_rate'], side['center_mhz']
    sps = fs / 1e6
    lo = max(0, int(start) - 400)
    hi = min(len(iq), int(start + nbits * sps) + 400)
    z = shifted(iq, lo, hi, e['channel_mhz'], center, fs, extra_hz_of(e))
    bb = fftconvolve(z, taps_for(fs), 'same')
    d = np.angle(bb[1:] * np.conj(bb[:-1]))
    centres = start + (np.arange(nbits) + 0.5) * sps - 0.5 - lo
    f = np.interp(centres, np.arange(len(d)), d)
    a68 = np.array(br.access_code(e['lap'], trailer=False)) * 2.0 - 1.0
    slope, thr = np.polyfit(a68, f[:68], 1)
    return (f > thr).astype(np.uint8)


def btbb(bits, clk):
    """(header taken, header18, payload verdict, payload bytes) of libbtbb for a burst's demodulated bits at the
    given clock, the UAP being the caller's job (``set_uap`` is done with the one in ``btbb.uap``)."""
    raw = bytes(int(b) for b in bits[4:])
    pkt = _lib.btbb_packet_new()
    try:
        _lib.btbb_packet_set_data(pkt, ctypes.cast(ctypes.c_char_p(raw), ctypes.POINTER(ctypes.c_char)), len(raw), 0,
                                  ((clk >> 1) & 0x3F) << 1)
        _lib.btbb_packet_set_uap(pkt, btbb.uap)
        _lib.btbb_packet_set_flag(pkt, 4, 1)
        _lib.btbb_packet_set_flag(pkt, 0, 1)
        if _lib.btbb_decode_header(pkt) != 1:
            return False, None, None, b''
        hdr = _lib.btbb_packet_get_header_packed(pkt) & 0x3FFFF
        verdict = _lib.btbb_decode_payload(pkt)
        buf = (ctypes.c_char * 400)()
        n = _lib.btbb_get_payload_packed(pkt, buf)
        return True, hdr, verdict, bytes(buf[:max(0, n)])
    finally:
        _lib.btbb_packet_unref(pkt)


btbb.uap = 0


def rendered(side):
    return [e for e in side['bursts'] if e['rendered']]


def spread(items, n):
    """``n`` items of a list, evenly spread, in order."""
    if len(items) <= n:
        return list(items)
    idx = np.linspace(0, len(items) - 1, n).round().astype(int)
    return [items[i] for i in sorted(set(idx))]


def intra_us(e):
    """Where in its TX slot a burst starts, nominally: 0 (master packet, first ID), 312.5 (second ID), 625 (slave)."""
    if e['role'] == 'slave':
        return SLOT_US
    return 312.5 if e['half'] == 'second' else 0.0


# --- the checks ------------------------------------------------------------------------------------

REQUIRED = ['index', 'start_sample', 'timing_frac', 'symbol_phase', 'channel', 'channel_mhz', 'ptype', 'role', 'half',
            'clk', 'lt_addr', 'lap', 'uap', 'payload_hex', 'snr_db', 'rendered', 'in_window', 'train', 'phase', 'tail',
            'slot_start_us', 'grid_offset_us', 'grid_offset_unwrapped_us', 'start_us', 'start_mod_625_us',
            'header_bits_18', 'whitening_clk6_1', 'header_air_bits_54']
TOP = ['generator', 'generator_commit', 'lap', 'uap', 'sample_rate', 'center_mhz', 'snr_db', 'cfo_hz', 'clk_convention',
       'seed', 'grid_offset_us_first_slot', 'drift_ppm_signed', 'drift_law', 'grid_offset_definition',
       'slot_grid_note', 'symbol_phase_note', 'channels', 'selection_note', 'crossings', 'n_bursts', 'n_samples',
       'per_burst_keys', 'notes']


def check_sidecar(side, expect, label):
    """The sidecar's own arithmetic, from ``start_sample + timing_frac``, against the definitions."""
    b = side['bursts']
    fs = side['sample_rate']
    ups = fs / 1e6
    miss = [k for k in TOP if k not in side]
    check(not miss, '%s: top-level keys all there (missing %s)' % (label, miss))
    bad = [e['index'] for e in b if any(k not in e for k in REQUIRED)]
    check(not bad and [e['index'] for e in b] == list(range(len(b))), '%s: every burst has every key, index is the order' % label)
    st = [e['start_sample'] + e['timing_frac'] for e in b]
    check(all(x <= y for x, y in zip(st, st[1:])), '%s: bursts are sorted by start' % label)
    ok = all(abs(e['start_us'] - (e['start_sample'] + e['timing_frac']) / ups) < 1e-6 and
             abs(circ(e['start_mod_625_us'], e['start_us'] % SLOT_US)) < 1e-6 and 0 <= e['start_mod_625_us'] < SLOT_US
             for e in b)
    check(ok, '%s: start_us = (start_sample + timing_frac) / (fs/1e6) us and start_mod_625_us is its mod 625' % label)
    ok = all(0 <= e['grid_offset_us'] < SLOT_US and abs(circ(e['grid_offset_us'], e['slot_start_us'])) < 1e-6 for e in b)
    check(ok, '%s: grid_offset_us is slot_start_us mod 625, in [0, 625)' % label)
    ok = all(abs((e['slot_start_us'] - e['grid_offset_unwrapped_us']) % SLOT_US) < 1e-6 or
             abs((e['slot_start_us'] - e['grid_offset_unwrapped_us']) % SLOT_US - SLOT_US) < 1e-6 for e in b)
    check(ok, '%s: grid_offset_unwrapped_us differs from slot_start_us by a whole number of 625 us' % label)
    ok = all(abs(e['start_us'] - e['slot_start_us'] - intra_us(e)) < 1e-6 for e in b)
    check(ok, '%s: start_us is slot_start_us + 0 (master, first ID), +312.5 (second ID) or +625 (slave answer)' % label)
    ok = all(e['symbol_phase'] == e['start_sample'] % int(round(ups)) and 0 <= e['timing_frac'] < 1 for e in b)
    check(ok, '%s: symbol_phase is start_sample mod samples per symbol (%d)' % (label, int(round(ups))))
    off0 = float(expect['offset_us'])
    d1 = expect['drift_ppm'] * 1e-6
    # the law is anchored at the first TX slot (file microsecond 20000 + offset0): its offset is offset0 exactly
    t_first = 20000.0 + off0
    check(abs(circ(side['grid_offset_us_first_slot'], off0)) < 1e-9 and abs(side['grid_offset_us_param'] - off0) < 1e-9 and
          abs(side['first_tx_slot_start_us'] - t_first) < 1e-6 and abs(side['drift_ppm_signed'] - expect['drift_ppm']) < 1e-9,
          '%s: the first TX slot (file %.3f us) is at offset %.4f us = the parameter, drift_ppm_signed %+.1f' % (
              label, side['first_tx_slot_start_us'], side['grid_offset_us_first_slot'], side['drift_ppm_signed']))
    for e in b:
        t = e['slot_start_us']
        if abs(circ(e['grid_offset_unwrapped_us'], off0 - d1 * (t - t_first), 1e12)) > 1e-6:
            check(False, '%s: burst %d is off the law offset0 - d (t - t_first)' % (label, e['index']))
            break
    else:
        check(True, '%s: every burst\'s unwrapped offset is offset0 - d (t - t_first) to 1e-6 us' % label)
    worst = 0.0
    # the offset law, from the parameters: u = offset0 - d * tau, tau the transmitter time of the TX slot
    worst = 0.0
    taus = {}
    for e in b:
        if e['half'] != 'second' and e['role'] != 'slave':
            taus.setdefault(e['clk'] // 4, e)
    ks = sorted(taus)
    if len(ks) > 3:
        k0 = ks[0]
        t0 = taus[k0]['slot_start_us']
        res = []
        for k in ks:
            res.append(taus[k]['slot_start_us'] - (t0 + 1250.0 * (k - k0) / (1 + d1)))
        worst = max(abs(circ(r, 0.0, 1e9)) for r in res)
        check(worst < 1e-5, '%s: TX slot starts are one line t0 + 1250 k / (1 + d): worst residual %.2e us' % (label, worst))
    return worst


def check_positions(iq, side, expect, label, n=40, window=1500):
    """The start of a sample of bursts fitted from the samples, and everything derived from it."""
    ups = side['sample_rate'] / 1e6
    tol = 0.1
    picks = spread(rendered(side), n)
    errs, recompute = [], []
    for e in picks:
        f = fit(iq, side, e, window)
        errs.append(f['start'] - (e['start_sample'] + e['timing_frac']))
        slot_us = f['start'] / ups - intra_us(e)
        recompute.append((abs(circ(slot_us, e['slot_start_us'])) * ups,
                          abs(circ(slot_us % SLOT_US, e['grid_offset_us'])) * ups,
                          abs(circ(f['start'] / ups, e['start_us'], 1e12)) * ups,
                          abs(circ((f['start'] / ups) % SLOT_US, e['start_mod_625_us'])) * ups))
    errs = np.array(errs)
    rc = np.array(recompute)
    check(len(picks) >= 10 and np.abs(errs).max() < tol,
          '%s: %d bursts fitted from the samples: start_sample + timing_frac within 0.1 sample, worst %.3f, mean %+.3f' % (
              label, len(picks), np.abs(errs).max(), errs.mean()))
    check(rc.max() < tol, '%s: slot_start_us, grid_offset_us, start_us, start_mod_625_us recomputed from the measured '
          'starts, worst %s sample' % (label, ', '.join('%.3f' % x for x in rc.max(axis=0))))
    return picks


def check_fixed(side, expect, label):
    off0 = float(expect['offset_us'])
    go = np.array([e['grid_offset_us'] for e in side['bursts']])
    check(np.abs(circ(go, off0)).max() < 1e-6 and not side['crossings'],
          '%s: the grid offset is %.4f us for every burst and there is no crossing' % (label, off0))


def expected_crossings(offset_us, drift_ppm, t_end_s):
    """Where u = offset0 - d * tau meets a multiple of 625, from the parameters alone: (file time s, boundary),
    after the first TX slot (tau = 20 ms: nothing before it is a crossing between bursts)."""
    d1 = Fraction(str(drift_ppm)) * Fraction(1, 10 ** 6)
    off = Fraction(str(offset_us))
    out = []
    if d1 == 0:
        return out
    t_first = 20000 + off
    for j in range(-40, 41):
        t = t_first + (off - 625 * j) / d1
        if t_first < t < t_end_s * 1e6:
            out.append((float(t) * 1e-6, 0 if d1 > 0 else 625))
    return sorted(out)


def check_drift(iq, side, expect, label, n=40):
    """The drift from the measured TX-slot starts: slope against the slot index, and the crossing."""
    ups = side['sample_rate'] / 1e6
    slots = [e for e in rendered(side) if e['role'] != 'slave' and e['half'] != 'second']
    picks = spread(slots, n)
    x = np.array([e['clk'] // 4 for e in picks], dtype=float)
    y = np.array([fit(iq, side, e)['start'] / ups for e in picks])
    b, a = np.polyfit(x, y, 1)
    drift = (1250.0 / b - 1) * 1e6
    check(abs(drift - expect['drift_ppm']) < 1.0 and abs(drift - side['drift_ppm_signed']) < 1.0,
          '%s: measured drift %+.3f ppm from %d TX-slot starts, the stated %+.1f (within 1 ppm; sidecar %+.1f)' % (
              label, drift, len(picks), expect['drift_ppm'], side['drift_ppm_signed']))
    t_end = side['n_samples'] / side['sample_rate']
    want = expected_crossings(expect['offset_us'], expect['drift_ppm'], t_end)
    got = side['crossings']
    check(len(got) == len(want) and all(abs(c['time_s'] - w[0]) < 2e-6 and c['boundary'] == w[1]
                                         for c, w in zip(got, want)),
          '%s: crossings %s are %s from the parameters' % (
              label, [(round(c['time_s'], 6), c['boundary']) for c in got], [(round(w[0], 6), w[1]) for w in want]))
    # the line's own crossing, measured from the samples: where the measured offset mod 625 wraps
    if want and got:
        slope = b - 1250.0
        xs = np.arange(x.min() - 8, x.max() + 8)
        wrapped = (a + slope * xs) % SLOT_US
        jumps = np.flatnonzero(np.abs(np.diff(wrapped)) > 312)
        t_line = (a + b * (xs[jumps[0]] + 0.5)) * 1e-6 if len(jumps) else None
        check(t_line is not None and abs(t_line - got[0]['time_s']) < 2 * 1.25e-3,
              '%s: the measured line wraps at %s s, the sidecar says %.4f s (within 2 TX slots)' % (
                  label, '%.4f' % t_line if t_line is not None else '-', got[0]['time_s']))
        c = got[0]['burst_index_first_after']
        pair = [side['bursts'][c - 1], side['bursts'][c]]
        u = [e['grid_offset_unwrapped_us'] for e in pair]
        down = got[0]['boundary'] == 0
        crossed = (u[0] >= 0 > u[1]) if down else (u[0] < 625 <= u[1])
        # the nearest rendered bursts either side (an ID out of the window is not in the samples)
        around = [e for e in side['bursts'][max(0, c - 60):c] if e['rendered']][-1:] + \
                 [e for e in side['bursts'][c:c + 60] if e['rendered']][:1]
        meas = [fit(iq, side, e)['start'] - (e['start_sample'] + e['timing_frac']) for e in around]
        check(crossed and abs(pair[1]['grid_offset_us'] - pair[0]['grid_offset_us']) > 312 and len(meas) == 2 and
              max(map(abs, meas)) < 0.1 and
              pair[1]['start_us'] > pair[0]['start_us'],
              '%s: bursts %d and %d are either side of the crossing: unwrapped offset %.5f -> %.5f us, mod 625 %.4f -> %.4f '
              '(the nearest rendered bursts %s fitted from the samples within 0.1 sample)' % (
                  label, c - 1, c, u[0], u[1], pair[0]['grid_offset_us'], pair[1]['grid_offset_us'],
                  [e['index'] for e in around]))


def check_pairs(iq, side, label, n=12):
    """A second ID is its first + 312.5 us, a slave answer its master + 625 us, measured."""
    ups = side['sample_rate'] / 1e6
    b = side['bursts']
    byclk = {}
    for e in b:
        if e['rendered'] and e['role'] in ('master', 'id_page'):
            byclk[(e['role'], e['clk'], e['half'])] = e
    pairs = []
    for e in rendered(side):
        if e['role'] == 'slave':
            m = byclk.get(('master', e['clk'] - 2, None))
            if m and m['channel'] == e['channel']:
                pairs.append((m, e, 625.0))
        elif e['half'] == 'second':
            f = byclk.get(('id_page', e['clk'] - 1, 'first'))
            if f:
                pairs.append((f, e, 312.5))
    if not pairs:
        check(False, '%s: no pair to measure' % label)
        return
    errs = []
    for m, s, want in spread(pairs, n):
        fm, fs_ = fit(iq, side, m), fit(iq, side, s)
        errs.append((fs_['start'] - fm['start']) / ups - want)
        errs[-1] = abs(errs[-1]) * ups
    check(max(errs) < 0.1, '%s: %d pairs measured: second ID = first + 312.5 us / slave = master + 625 us, worst '
          '%.3f sample' % (label, len(errs), max(errs)))
    kinds = {w for _, _, w in pairs}
    return kinds


def check_decode(iq, side, label, n=14):
    """Bits demodulated from the samples are the packet's; libbtbb decodes them at the sidecar's clock and not at +/- 2."""
    non_id = [e for e in rendered(side) if e['ptype'] != 'ID']
    by_type = {}
    for e in non_id:
        by_type.setdefault((e['role'], e['ptype']), []).append(e)
    picks = []
    for key in sorted(by_type):
        picks += by_type[key][:2]
    picks = spread(picks, n) if len(picks) > n else picks
    if not non_id:
        return                                      # an ID-only file has no header to decode
    ok_bits, ok_hdr, ok_air, ok_dec, ok_wrong, rejected, agree, total_wrong = [], [], [], [], [], 0, [], 0
    for e in picks:
        f = fit(iq, side, e)
        want = model_bits(e)
        nb = len(want)
        got = demod(iq, side, e, f['start'], nb)
        ok_bits.append(np.array_equal(got, want.astype(np.uint8)))
        air = ''.join(str(int(x)) for x in got[72:126])
        ok_air.append(air == e['header_air_bits_54'])
        btbb.uap = e['uap']
        taken, hdr, verdict, data = btbb(got, e['clk'])
        want_hdr = br.bits_int([int(c) for c in e['header_bits_18']])
        full = bytes.fromhex(e['payload_full_hex']) if e.get('payload_full_hex') else b''
        ok_hdr.append(taken and hdr == want_hdr)
        ok_dec.append(taken and hdr == want_hdr and (not full or (verdict == 10 and data[:len(full)] == full)))
        wrong = True
        rx = [int(x) for x in got[72:126:3]]                 # the whitened header, one bit of each triplet
        for dc in (-2, 2):
            t2, h2, v2, d2 = btbb(got, e['clk'] + dc)
            # what a HEC can say about it, from the bits alone: de-whiten with the wrong clock and check
            dw = [a_ ^ b_ for a_, b_ in zip(rx, br.whitening(e['clk'] + dc, 18))]
            predicted = br.hec(dw[:10], e['uap']) == dw[10:]
            flag = e['hec_alias_clk_plus2'] if dc > 0 else e['hec_alias_clk_minus2']
            agree.append(t2 == predicted == flag)
            total_wrong += 1
            rejected += 0 if t2 else 1
            if t2 and h2 == want_hdr and (not full or (v2 == 10 and d2[:len(full)] == full)):
                wrong = False
        ok_wrong.append(wrong)
    check(len(picks) >= 8 and all(ok_bits), '%s: bits demodulated from the samples are the packet\'s (%d of %d, %d types)' % (
        label, sum(ok_bits), len(ok_bits), len({(e['role'], e['ptype']) for e in picks})))
    check(all(ok_air), '%s: header_air_bits_54 are the 54 bits after the access code, demodulated, bit 0 first (%d of %d)' % (
        label, sum(ok_air), len(ok_air)))
    if _lib is None:
        print('  skip libbtbb is not installed: the decode checks are not made')
        return
    check(all(ok_dec), '%s: libbtbb decodes header and payload at the sidecar\'s UAP and clk (HEC and CRC pass) (%d of %d)' % (
        label, sum(ok_dec), len(ok_dec)))
    # A wrong whitening clock breaks the HEC except where the whitening difference happens to be a codeword of it
    # (about 3 % of the clock pairs); libbtbb must agree with the HEC computed here from the bits, and never return
    # the packet.
    check(all(agree) and all(ok_wrong) and rejected >= 0.8 * total_wrong,
          '%s: and at clk +/- 2 (one slot) %d of %d header decodes are refused by the HEC, exactly the ones the HEC '
          'computed here refuses, and none returns the packet' % (label, rejected, total_wrong))


def check_ids(iq, side, label, n=12):
    """IDs: the 68 bits, the end of a C1 burst, a C2 tail's length, frequency and level, from the samples."""
    ids = [e for e in rendered(side) if e['ptype'] == 'ID']
    if not ids:
        return None
    fs, center = side['sample_rate'], side['center_mhz']
    ups = fs / 1e6
    pn = NOISE * ups                     # noise power per sample
    ok_bits, tail_rows, ok_gone = [], [], []
    tails_seen = set()
    for e in spread(ids, n):
        f = fit(iq, side, e)
        s = f['start']
        got = demod(iq, side, e, s, 68)
        sync_from_lap = br.access_code(e['lap'], trailer=False)
        ok_bits.append(list(got) == list(sync_from_lap))
        lo = int(s) - 100
        hi = int(s + 340 * ups)
        z = shifted(iq, lo, hi, e['channel_mhz'], center, fs, extra_hz_of(e))
        t = (np.arange(lo, hi) - s) / ups              # us from the first access-code sample
        p = np.abs(z) ** 2
        a_sync = p[(t >= 6) & (t <= 62)].mean() - pn
        tail = e['tail']
        if tail['variant'] == 'C1':
            quiet_p = p[(t >= 74) & (t <= 300)].mean()
            ok_gone.append(abs(10 * np.log10(quiet_p / pn)) < 0.5)
        elif tail['variant'] == 'C2':
            win = (t >= 74) & (t <= 112)
            a_tail = p[win].mean() - pn
            level = 10 * np.log10(a_tail / a_sync)
            zl = fftconvolve(z, taps_for(fs), 'same')[win]
            dev = np.angle(np.sum(zl[1:] * np.conj(zl[:-1]))) / (2 * np.pi) * fs
            # length: the 3 dB extent of a 1 us smoothed envelope
            k = int(ups)
            env = np.convolve(p, np.ones(k) / k, 'same')
            half = (a_tail + pn) / 2.0
            inside = np.flatnonzero((t >= 66) & (t <= 140) & (env > half))
            dur = (t[inside[-1]] - 68.0) if len(inside) else 0.0
            tails_seen.add(e['tail']['dur_us'])
            after = p[(t >= 134) & (t <= 300)].mean()
            tail_rows.append((level - tail['level_db'], dev / 1e3 - tail['dev_khz'], dur - tail['dur_us'],
                              10 * np.log10(after / pn)))
    check(all(ok_bits), '%s: the 68 bits of %d IDs, demodulated, are the preamble and sync word of the LAP (%d of %d)' % (
        label, len(ok_bits), sum(ok_bits), len(ok_bits)))
    if ok_gone:
        check(all(ok_gone), '%s: C1: after the ramp (from 74 us to 300 us) nothing is on the air, only the floor (%d IDs)' % (
            label, len(ok_gone)))
    if tail_rows:
        r = np.array(tail_rows)
        check(np.abs(r[:, 0]).max() < 1.0, '%s: C2 tail level relative to the sync word is level_db within 1 dB, worst %.2f '
              '(%d IDs)' % (label, np.abs(r[:, 0]).max(), len(r)))
        check(np.abs(r[:, 1]).max() < 5.0, '%s: C2 tail frequency is dev_khz within 5 kHz, worst %.2f kHz' % (
            label, np.abs(r[:, 1]).max()))
        check(tails_seen == {56.0}, '%s: dur_us is 56 (the 54 header positions + 2 us)' % label)
        check(np.abs(r[:, 2]).max() < 3.0, '%s: C2 tail lasts dur_us within 3 us (3 dB extent of the envelope), worst %.2f us' % (
            label, np.abs(r[:, 2]).max()))
        check(np.abs(r[:, 3]).max() < 0.7, '%s: after the tail only the floor again (worst %+.2f dB)' % (
            label, r[:, 3][np.argmax(np.abs(r[:, 3]))]))
    return len(ids)


def check_channels(side, spec, label):
    """No centre channel, the window flags, the channel lists."""
    ctr = int(round(side['center_mhz'] - 2402))
    half = 12.0 if side['sample_rate'] > 30e6 else 8.0
    b = side['bursts']
    check(all(e['channel'] != ctr for e in b), '%s: no burst on the centre channel %d' % (label, ctr))
    ok = all(e['in_window'] == (abs(e['channel_mhz'] - side['center_mhz']) <= half) and e['rendered'] == e['in_window']
             for e in b)
    check(ok, '%s: in_window is |channel MHz - centre| <= %g MHz and only those bursts are rendered' % (label, half))
    check(all(abs(e['channel_mhz'] - 2402 - e['channel']) < 1e-9 for e in b), '%s: channel_mhz = 2402 + channel' % label)


def check_overlap(side, label):
    ups = side['sample_rate'] / 1e6
    by = {}
    for e in side['bursts']:
        tail = e['tail']['dur_us'] or 0.0 if e['tail'] else 0.0
        ramp = (e['tail']['ramp_us'] or 0.0) if e['tail'] else 0.0
        n = e['air_bits_length']
        end = e['start_us'] + n + tail + 2.0
        by.setdefault(e['channel'], []).append((e['start_us'], end, e['index']))
    bad = []
    for ch, lst in by.items():
        lst.sort()
        for (a0, a1, i), (b0, b1, j) in zip(lst, lst[1:]):
            if b0 < a1:
                bad.append((ch, i, j))
    allv = sorted((s, e_, i) for lst in by.values() for s, e_, i in lst)
    allbad = [(i, j) for (s0, e0, i), (s1, e1, j) in zip(allv, allv[1:]) if s1 < e0]
    check(not bad, '%s: no two bursts overlap in time on one channel (%d bursts, %d channels; %d overlaps on any channel)' % (
        label, len(side['bursts']), len(by), len(allbad)))
    return len(allbad)


def check_sizes(iq, side, label):
    keys_ok = all(sorted(e) == side['per_burst_keys'] for e in side['bursts'])
    check(side['n_samples'] == len(iq) and side['n_bursts'] == len(side['bursts']),
          '%s: n_samples %d is the array (%d), n_bursts %d the list (%d)' % (
              label, side['n_samples'], len(iq), side['n_bursts'], len(side['bursts'])))
    check(keys_ok, '%s: per_burst_keys are the keys of every burst' % label)
    ids = [e for e in side['bursts'] if e['ptype'] == 'ID']
    non = [e for e in side['bursts'] if e['ptype'] != 'ID']
    ok = (all(e['lt_addr'] is None and e['payload_hex'] == '' and e['header_bits_18'] is None and
              e['header_air_bits_54'] is None and e['whitening_clk6_1'] is None and e['role'] == 'id_page' and
              e['half'] in ('first', 'second') and e['train'] in ('A', 'B') for e in ids) and
          all(e['lt_addr'] is not None and e['half'] is None and e['train'] is None and
              e['header_bits_18'] is not None and len(e['header_air_bits_54']) == 54 and
              e['role'] in ('master', 'slave') for e in non))
    check(ok, '%s: nulls are where they belong (IDs have no header, packets no half and no train)' % label)


def check_noise(iq, side, label):
    """The floor: the file less its bursts is the regenerated noise; white; at the common level."""
    fs = side['sample_rate']
    ups = fs / 1e6
    lead = int(0.015 * fs)
    seg = iq[:lead]
    power = np.mean(np.abs(seg.astype(np.complex128)) ** 2)
    want = NOISE * ups
    check(abs(10 * np.log10(power / want)) < 0.05, '%s: noise power in the lead-in is the common floor within 0.05 dB (%+.3f dB)' % (
        label, 10 * np.log10(power / want)))
    f, pxx = welch(seg, fs=fs, nperseg=2048, return_onesided=False)
    pxx = np.fft.fftshift(pxx)
    bands = pxx.reshape(8, -1).mean(axis=1)
    check(10 * np.log10(bands.max() / bands.min()) < 0.15, '%s: white across the whole band: 8 bands within %.3f dB' % (
        label, 10 * np.log10(bands.max() / bands.min())))


def check_linear(side, plan_noise_off, iq, label):
    """The file is the bursts plus the regenerated noise, sample for sample: no artificial silence, a constant floor."""
    fs = side['sample_rate']
    worst = 0.0
    block = g.NOISE_BLOCK
    for index, lo in enumerate(range(0, len(iq), block)):
        hi = min(lo + block, len(iq))
        n = noise_block(side['noise_seed'], index, hi - lo, fs)
        diff = iq[lo:hi] - n - plan_noise_off[lo:hi]
        worst = max(worst, float(np.abs(diff).max()))
    check(worst < 1e-5, '%s: the file less the noise regenerated from the seed is the bursts alone (worst %.1e)' % (label, worst))


# --- plan checks (no samples) -------------------------------------------------------------------------

def check_pages(side, label, seconds):
    b = side['bursts']
    ids = [e for e in b if e['ptype'] == 'ID' and e['phase'] in (None, 'page')]
    lap, uap = side['lap'], side['uap']
    ok = all(e['channel'] == hs.page(e['clk'], lap, uap, hs.KOFFSET['A' if ((e['clk'] >> 12) & 1) == 0 else 'B'])
             for e in ids)
    check(ok, '%s: all %d IDs are on bt_hop_substates.page(clk, LAP, UAP, koffset) of their clock' % (label, len(ids)))
    ok = all(e['train'] == ('A' if ((e['clk'] >> 12) & 1) == 0 else 'B') for e in ids)
    check(ok, '%s: train is A on even 1.28 s blocks of the clock, B on odd' % label)
    firsts = [e for e in ids if e['half'] == 'first']
    seconds_ = [e for e in ids if e['half'] == 'second']
    ok = (all(e['clk'] % 4 == 0 for e in firsts) and all(e['clk'] % 4 == 1 for e in seconds_) and
          len(firsts) == len(seconds_))
    check(ok, '%s: two IDs per TX slot, first at CLK1-0 = 00, second at 01 (%d slots)' % (label, len(firsts)))
    blocks = sorted({e['clk'] >> 12 for e in ids})
    sets = [sorted({e['channel'] for e in ids if (e['clk'] >> 12) == k}) for k in blocks]
    check(len(set(map(tuple, sets))) == len(sets) and all(len(s) == 16 for s in sets[1:-1]),
          '%s: %d channel sets of 16 over %d blocks of 1.28 s, each differs from the next (%d changes in %.1f s)' % (
              label, len(sets), len(blocks), len(blocks) - 1, seconds))
    first_train = set(sets[0])
    ctr = int(round(side['center_mhz'] - 2402))
    half = 12.0 if side['sample_rate'] > 30e6 else 8.0
    inwin = sorted(c for c in first_train if abs(2402 + c - side['center_mhz']) <= half)
    check(39 in first_train and 43 in first_train and len(inwin) >= 4 and ctr not in {e['channel'] for e in ids},
          '%s: the first train has 39 and 43 and %d in-window channels %s, and no train ever has the centre channel %d' % (
              label, len(inwin), inwin, ctr))
    # the 10 ms repeat
    by = {}
    for e in ids:
        by.setdefault(e['clk'], e['channel'])
    rep = all(by[c] == by[c + 32] for c in by if c + 32 in by and (c >> 12) == ((c + 32) >> 12))
    check(rep, '%s: each channel of a train repeats every 32 ticks (10 ms) within a block' % label)
    return len(blocks) - 1


# --- one file at a time ------------------------------------------------------------------------------

FAST = dict(snr_db=HIGH)


def make(stem, **kw):
    kw = dict(FAST, **kw)
    t = time.time()
    iq, side = g.synthesise(stem, **kw)
    return iq, side, time.time() - t


def expect_of(stem, **kw):
    spec = g.SPECS[stem]
    return dict(offset_us=kw.get('offset_us', spec['offset_us']), drift_ppm=kw.get('drift_ppm', spec['drift_ppm']))


def run_core(iq, side, expect, label, n=14):
    """The checks the mutants are held to, on a file small enough to do all of them."""
    check_sidecar(side, expect, label)
    check_positions(iq, side, expect, label, n=n)
    if expect['drift_ppm']:
        check_drift(iq, side, expect, label, n=n)
    else:
        check_fixed(side, expect, label)
    check_pairs(iq, side, label, n=6)
    check_decode(iq, side, label, n=8)
    check_ids(iq, side, label, n=6)
    check_channels(side, g.SPECS, label)
    check_sizes(iq, side, label)
    check_overlap(side, label)


def test_files():
    print('== the exchange files (set A kind) with drift: B_down, B_up', flush=True)
    for stem in ('B_down', 'B_up'):
        iq, side, secs = make(stem, duration=0.45, n_per_10s=300)
        ex = expect_of(stem)
        lab = stem
        print('  (%s: %d bursts, %d rendered, %.1f s)' % (lab, len(side['bursts']), len(rendered(side)), secs))
        check_sidecar(side, ex, lab)
        check_positions(iq, side, ex, lab, n=36)
        check_drift(iq, side, ex, lab, n=36)
        check_pairs(iq, side, lab, n=12)
        check_decode(iq, side, lab, n=12)
        check_channels(side, g.SPECS, lab)
        check_sizes(iq, side, lab)
        check_overlap(side, lab)
        ch = sorted({e['channel'] for e in side['bursts']})
        check(len(ch) >= 4 and 39 in ch and 43 in ch, '%s: %d channels %s, 39 and 43 among them' % (lab, len(ch), ch))
        check(side['lap'] == 0x635FCA and side['uap'] == 0x47 and
              sum(a != b for a, b in zip(br.sync_word(0x635FCA), br.sync_word(0x9E8B33))) > 20 and
              0x635FCA not in range(0x9E8B00, 0x9E8B40),
              '%s: LAP 0x635FCA / UAP 0x47, not reserved, sync word %d bits from the GIAC\'s' % (
                  lab, sum(a != b for a, b in zip(br.sync_word(0x635FCA), br.sync_word(0x9E8B33)))))
        types = {(e['role'], e['ptype']) for e in side['bursts']}
        check({('master', t) for t in ('POLL', 'NULL', 'DM1', 'DH1')} <= types and
              {('slave', t) for t in ('NULL', 'DM1', 'DH1')} <= types, '%s: every master and slave packet type is there' % lab)
        w = {(e['whitening_clk6_1']) for e in side['bursts'] if e['role'] == 'master'}
        check(len(w) >= 12, '%s: CLK6-1 takes %d values over the master packets' % (lab, len(w)))
        if stem == 'B_down':
            check_noise(iq, side, lab)
        del iq

    print('== the fixed controls: A_50p25, A_312p75', flush=True)
    for stem, phases in (('A_50p25', {10}), ('A_312p75', {30})):
        iq, side, secs = make(stem, duration=0.3, n_per_10s=300)
        ex = expect_of(stem)
        check_sidecar(side, ex, stem)
        check_fixed(side, ex, stem)
        check_positions(iq, side, ex, stem, n=12)
        masters = [e for e in side['bursts'] if e['role'] == 'master']
        check({e['symbol_phase'] for e in masters} == phases and
              {e['symbol_phase'] for e in side['bursts'] if e['role'] == 'slave'} == phases,
              '%s: every master packet and slave answer sits at symbol phase %s' % (stem, sorted(phases)))
        check(all(e['timing_frac'] == 0.0 for e in side['bursts']), '%s: timing_frac is 0 for every burst (the grid gives it)' % stem)
        del iq

    print('== 20 MS/s, centre 2441 MHz: A_20msps', flush=True)
    iq, side, secs = make('A_20msps', duration=0.45, n_per_10s=300)
    ex = expect_of('A_20msps')
    check(side['sample_rate'] == 20e6 and side['center_mhz'] == 2441.0, 'A_20msps: 20 MS/s on 2441.0 MHz')
    check_sidecar(side, ex, 'A_20msps')
    check_positions(iq, side, ex, 'A_20msps', n=24)
    check_drift(iq, side, ex, 'A_20msps', n=24)
    check_channels(side, g.SPECS, 'A_20msps')
    check_sizes(iq, side, 'A_20msps')
    check_decode(iq, side, 'A_20msps', n=8)
    ch = sorted({e['channel'] for e in side['bursts']})
    check(len(ch) >= 4 and 41 not in ch and 43 in ch and 39 not in ch,
          'A_20msps: %d channels %s, 43 among them, none the centre channel 39' % (len(ch), ch))
    check(side['bursts'][0]['symbol_phase'] in range(20), 'A_20msps: 20 samples per symbol, symbol_phase in 0..19')
    del iq

    print('== the CFO file: B_down_cfo', flush=True)
    iq, side, secs = make('B_down_cfo', duration=0.3, n_per_10s=300)
    ex = expect_of('B_down_cfo')
    check_sidecar(side, ex, 'B_down_cfo')
    picks = spread(rendered(side), 8)
    errs, df_err = [], []
    for e in picks:
        want = e['channel_mhz'] * 1e6 * 20e-6
        errs.append(abs(e['cfo_hz'] - want))
        nominal = dict(e, cfo_hz=0.0)
        f = fit(iq, side, e)
        df_err.append(abs(f['df']))
    check(max(errs) < 1e-6 and 48e3 < side['cfo_hz'] < 49.5e3,
          'B_down_cfo: every burst has cfo_hz = f_ch x 20e-6 (%.0f to %.0f Hz) and the top level %.0f Hz' % (
              min(e['cfo_hz'] for e in side['bursts']), max(e['cfo_hz'] for e in side['bursts']), side['cfo_hz']))
    check(max(df_err) < 1500, 'B_down_cfo: the burst model with the sidecar\'s carrier fits: residual carrier error %.0f Hz at worst' % max(df_err))
    # the carriers themselves, from the samples: at the fitted start, the model at the bare channel (no cfo) sees
    # almost nothing of the burst, the model at channel + cfo_hz all of it
    ratios = []
    for e in picks[:5]:
        f = fit(iq, side, e)
        ups = side['sample_rate'] / 1e6
        bits = model_bits(e)
        i0 = math.floor(f['start'])
        m, ld = model_signal(e, bits, side['sample_rate'], f['start'] - i0)
        lo = i0 - ld
        amps = []
        for extra in (0.0, e['cfo_hz']):
            z = shifted(iq, lo, lo + len(m), e['channel_mhz'], side['center_mhz'], side['sample_rate'], extra)
            amps.append(abs(np.vdot(m, z)) / np.vdot(m, m).real)
        ratios.append(amps[0] / amps[1])
    check(max(ratios) < 0.2, 'B_down_cfo: a model at the bare channel sees %.0f %% of what a model at channel + cfo_hz does '
          '(the carriers are cfo_hz off)' % (100 * max(ratios)))
    del iq
    iq, side, _ = make('B_down', duration=0.25, n_per_10s=300)
    check(side['cfo_hz'] == 0 and all(e['cfo_hz'] == 0 for e in side['bursts']), 'B_down: cfo_hz is 0 (top level and every burst)')
    f = fit(iq, side, spread(rendered(side), 3)[1])
    check(abs(f['df']) < 1500, 'B_down: a burst\'s carrier is the channel\'s to 1.5 kHz (%.0f Hz)' % f['df'])
    del iq


def test_pages():
    print('== page trains: C1_cross, C2m10_cross, C2m10_50p25, C1_50p25, C2m20_cross, C2_0_cross, C2m10_f50k_cross', flush=True)
    for stem in ('C1_cross', 'C2m10_cross', 'C2m20_cross', 'C2_0_cross', 'C2m10_f50k_cross'):
        iq, side, secs = make(stem, duration=0.45)
        ex = expect_of(stem)
        lab = stem
        n_id = check_ids(iq, side, lab, n=10)
        check_sidecar(side, ex, lab)
        check_positions(iq, side, ex, lab, n=30)
        if stem in ('C1_cross', 'C2m10_cross'):
            check_drift(iq, side, ex, lab, n=30)
            check_pairs(iq, side, lab, n=12)
            check_channels(side, g.SPECS, lab)
            check_sizes(iq, side, lab)
            check_overlap(side, lab)
            ids = side['bursts']
            nr = [e for e in ids if not e['rendered']]
            ok = []
            ups = side['sample_rate'] / 1e6
            for e in spread(nr, 8):
                lo, hi = int(e['start_sample']) - 50, int(e['start_sample'] + 130 * ups)
                z = shifted(iq, lo, hi, e['channel_mhz'], side['center_mhz'], side['sample_rate'])
                ok.append(abs(10 * np.log10(np.mean(np.abs(z) ** 2) / (NOISE * ups))) < 0.4)
            check(len(nr) > 0 and all(ok), '%s: %d IDs out of the window are in the truth with rendered false and the samples '
                  'there are the floor (%d checked)' % (lab, len(nr), len(ok)))
            check_noise(iq, side, lab)
        del iq
    print('== quarter-symbol fixed files: C1_50p25, C2m10_50p25, and the exact 50p0 ones', flush=True)
    for stem, phases in (('C1_50p25', (10, 30)), ('C2m10_50p25', (10, 30)), ('C1_50p0', (0, 20)), ('C2m10_50p0', (0, 20))):
        iq, side, secs = make(stem, duration=0.25)
        ex = expect_of(stem)
        check_sidecar(side, ex, stem)
        check_fixed(side, ex, stem)
        check_positions(iq, side, ex, stem, n=16)
        b = side['bursts']
        check({e['symbol_phase'] for e in b if e['half'] == 'first'} == {phases[0]} and
              {e['symbol_phase'] for e in b if e['half'] == 'second'} == {phases[1]},
              '%s: first IDs at symbol phase %d, second IDs at %d' % (stem, phases[0], phases[1]))
        check(all(e['timing_frac'] == 0.0 for e in b), '%s: timing_frac 0 for every ID' % stem)
        check_ids(iq, side, stem, n=6)
        del iq


def test_plans():
    print('== the 6 s page plans (no samples): trains, A/B alternation, selection', flush=True)
    for stem in ('C1_cross', 'C2m10_50p25', 'C2m10_f50k_cross'):
        t = time.time()
        plan = g.plan_file(stem)
        side = plan['sidecar']
        n = check_pages(side, stem, 6.0)
        check(n == 4, '%s: the channel set changes %d times in 6 s (4 expected)' % (stem, n))
        check(side['selection_note'] and 'draw' in side['selection_note'], '%s: selection_note states the draws: %r' % (
            stem, side['selection_note'][:90]))
        check(side['uap'] == 0x47 and side['lap'] not in range(0x9E8B00, 0x9E8B40),
              '%s: UAP 0x47 and a paged LAP outside the reserved inquiry range (0x%06X)' % (stem, side['lap']))
        check(side['n_samples'] == int(round((0.02 + 6.0 + 0.002) * side['sample_rate'])),
              '%s: n_samples is lead-in 20 ms + 6 s + 2 ms (%d)' % (stem, side['n_samples']))
        ch = side['channels']
        check('train_A' in ch and 'train_B' in ch and len(ch['train_A']) == 16 and len(ch['train_B']) == 16,
              '%s: channels carries the A and B trains (16 each)' % stem)
        check_overlap(side, stem)
        if side['drift_ppm_signed']:
            check_phase_coverage(side, stem)           # a fixed grid sits at two phases by design
            check_crossing_fields(side, stem)
        print('  (%s planned in %.1f s)' % (stem, time.time() - t), flush=True)
    print('== the D files (plans)', flush=True)
    sides = {}
    for stem in ('D_same_s1', 'D_same_s2', 'D_same_s3', 'D_diff_s1'):
        plan = g.plan_file(stem)
        sides[stem] = plan['sidecar']
    sd = sides['D_same_s1']
    for stem, side in sides.items():
        b = side['bursts']
        page = [e for e in b if e['phase'] == 'page']
        conn = [e for e in b if e['phase'] == 'connection']
        check(page and conn and all(e['ptype'] == 'ID' for e in page) and all(e['ptype'] != 'ID' for e in conn),
              '%s: %d page IDs then %d connection packets' % (stem, len(page), len(conn)))
        check_pages(dict(side, bursts=page), stem, 6.0)
        check_phase_coverage(dict(side, bursts=page), stem + ' page part')
        check_crossing_fields(side, stem)
        check_header_strings(side, stem)
        first_c = conn[0]
        check(first_c['start_sample'] == side['connection_start_sample'] or
              first_c['start_sample'] >= side['connection_start_sample'],
              '%s: connection_start_sample %d, first connection burst at %d' % (stem, side['connection_start_sample'], first_c['start_sample']))
        check(max(e['start_sample'] for e in page) < side['connection_start_sample'],
              '%s: every ID is before connection_start_sample' % stem)
        # one grid: the TX slot starts of page and connection bursts are on one line
        d1 = side['drift_ppm_signed'] * 1e-6
        slots = {}
        for e in b:
            if e['role'] != 'slave' and e['half'] != 'second':
                slots[e['clk'] // 4] = e
        ks = sorted(slots)
        k0 = ks[0]
        res = [slots[k]['slot_start_us'] - (slots[k0]['slot_start_us'] + 1250.0 * (k - k0) / (1 + d1)) for k in ks]
        check(max(abs(r) for r in res) < 1e-5 and len(ks) > 500,
              '%s: page and connection TX slots are on one grid (offset law unbroken, worst %.1e us over %d slots)' % (
                  stem, max(abs(r) for r in res), len(ks)))
        last = max((e for e in page if e['half'] == 'first'), key=lambda e: e['clk'])
        conns = [e for e in conn if e['role'] == 'master']
        want_clk = [last['clk'] + 4 * round((e['slot_start_us'] - last['slot_start_us']) / (1250.0 / (1 + d1)))
                    for e in conns]
        check(all(e['clk'] == w and e['clk'] % 4 == 0 and e['clk'] > last['clk'] for e, w in zip(conns, want_clk)),
              '%s: the connection\'s clock is the paging clock run on, 4 ticks a TX slot (last ID slot clk %d, first '
              'master clk %d, %d masters)' % (stem, last['clk'], conns[0]['clk'], len(conns)))
        train_ch = set(side['channels']['in_window_first_train'])
        conn_ch = {e['channel'] for e in conn}
        check(conn_ch <= train_ch and len(conn_ch) >= 4 and 39 in conn_ch and 43 in conn_ch,
              '%s: connection channels %s are a subset of the first train\'s in-window channels, 39 and 43 in' % (
                  stem, sorted(conn_ch)))
        check(all(e['lap'] == side['lap'] for e in page), '%s: IDs carry the paged LAP' % stem)
        if stem.startswith('D_same'):
            check(all(e['lap'] == side['lap'] and e['uap'] == side['uap'] for e in conn),
                  '%s: connection packets have the paged LAP and its true UAP' % stem)
        else:
            c_lap = {e['lap'] for e in conn}
            check(len(c_lap) == 1 and side['lap'] not in c_lap and side['connection_lap'] in c_lap and
                  not any(e['lap'] in c_lap for e in page),
                  '%s: the connection LAP 0x%06X sent no ID (the IDs are 0x%06X)' % (stem, list(c_lap)[0], side['lap']))
            check(br.bits_int(br.sync_word(side['lap'])) != br.bits_int(br.sync_word(list(c_lap)[0])),
                  '%s: and its sync word is another one' % stem)
    laps = {(s['lap'], s['uap']) for k, s in sides.items() if k.startswith('D_same')}
    clks = {s['bursts'][0]['clk'] for k, s in sides.items() if k.startswith('D_same')}
    check(len(laps) == 3 and len(clks) == 3, 'D_same_s1..s3 differ in LAP/UAP (%d) and starting clock (%d)' % (len(laps), len(clks)))
    a, b = sides['D_same_s1'], sides['D_diff_s1']
    ia = [(e['start_sample'], e['channel'], e['clk']) for e in a['bursts'] if e['phase'] == 'page']
    ib = [(e['start_sample'], e['channel'], e['clk']) for e in b['bursts'] if e['phase'] == 'page']
    check(ia == ib and a['lap'] == b['lap'] and a['uap'] == b['uap'],
          'D_diff_s1 has the very train of D_same_s1 (%d IDs) and only the connection\'s LAP and UAP differ' % len(ia))
    ca = [(e['start_sample'], e['channel'], e['ptype'], e['clk']) for e in a['bursts'] if e['phase'] == 'connection']
    cb = [(e['start_sample'], e['channel'], e['ptype'], e['clk']) for e in b['bursts'] if e['phase'] == 'connection']
    check(ca == cb, 'and the same connection schedule (%d packets)' % len(ca))
    want_c = expected_crossings(3.0, 23.0, 26.0)
    check(len(a['crossings']) == 1 and abs(a['crossings'][0]['time_s'] - want_c[0][0]) < 1e-6,
          'D_same_s1: crossing at %.6f s (0.02 s + 3 us + 3 us / 23 ppm = %.6f)' % (a['crossings'][0]['time_s'], want_c[0][0]))


def check_phase_coverage(side, label):
    """Over the RENDERED IDs of the page part, every symbol phase occurs among first IDs and among second IDs."""
    sps = int(round(side['sample_rate'] / 1e6))
    pg = [e for e in side['bursts'] if e['ptype'] == 'ID' and e['rendered']]
    first = [e['symbol_phase'] for e in pg if e['half'] == 'first']
    second = [e['symbol_phase'] for e in pg if e['half'] == 'second']
    cf, cs = np.bincount(first, minlength=sps), np.bincount(second, minlength=sps)
    check(len(set(first)) == sps and len(set(second)) == sps,
          '%s: all %d symbol phases among the %d rendered first IDs (per phase %d..%d) and the %d second IDs (%d..%d)' % (
              label, sps, len(first), cf.min(), cf.max(), len(second), cs.min(), cs.max()))


def check_crossing_fields(side, label):
    fs = side['sample_rate']
    ok = True
    t_first = side['first_tx_slot_start_us']
    for c in side['crossings']:
        i, r = c['burst_index_first_after'], c['first_rendered_index_after']
        want = (t_first + side['grid_offset_us_param'] / (side['drift_ppm_signed'] * 1e-6)) * 1e-6
        # offset passes 0 downward (offset0 > 0 here) or 625 upward
        if side['drift_ppm_signed'] < 0:
            want = (t_first + (side['grid_offset_us_param'] - 625) / (side['drift_ppm_signed'] * 1e-6)) * 1e-6
        ok &= (abs(c['crossing_time_s'] - c['time_s']) < 1e-12 and abs(c['time_s'] - want) < 1e-6 and
               r is not None and r >= i and side['bursts'][r]['rendered'] and
               all(not b['rendered'] for b in side['bursts'][i:r]) and
               (c['offset_before_us'] >= 0 > c['offset_after_us'] if c['boundary'] == 0 else
                c['offset_before_us'] < 625 <= c['offset_after_us']) and
               side['bursts'][i]['grid_offset_unwrapped_us'] == c['offset_after_us'])
    check(ok and side['crossings'], '%s: crossings carry crossing_time_s = t_first + offset0/drift, first_rendered_index_after, '
          'and the offsets either side (%s)' % (label, [(round(c['offset_before_us'], 5), round(c['offset_after_us'], 5))
                                                       for c in side['crossings']]))


def check_header_strings(side, label, n=40):
    """header_bits_18 and header_air_bits_54 rebuilt from the fields with the repository's encoder: the stored strings
    are in transmission order, the HEC in register read-out order."""
    ok = True
    done = 0
    for e in side['bursts']:
        if e['header_bits_18'] is None:
            continue
        h = br.packet_header(e['lt_addr'], e['ptype'], e['flow'], e['arqn'], e['seqn'], e['uap'])
        w = br.whitening(e['clk'], 18)
        air = ''.join(str(b ^ x) * 3 for b, x in zip(h, w))
        ok &= (e['header_bits_18'] == ''.join(map(str, h)) and e['header_air_bits_54'] == air and
               e['whitening_clk6_1'] == (e['clk'] >> 1) & 0x3F and
               [int(c) for c in e['header_bits_18'][10:]] == br.hec(h[:10], e['uap']))
        done += 1
        if done >= n:
            break
    note = side['header_bits_meaning']
    check(ok and done > 0 and 'read out' in note and 'do not reverse' in note and 'Worked example' in note,
          '%s: header strings (%d bursts) are the encoder\'s, transmission order, HEC in register read-out order, '
          'explained in header_bits_meaning with a worked example' % (label, done))


class Window:
    """A stretch of a file's samples at its absolute positions, for the checks that look at a real late stretch of
    a long plan without the file: ``len`` is the file's, a slice must lie inside the stretch."""

    def __init__(self, base, arr, n):
        self.base, self.arr, self.n = base, arr, n

    def __len__(self):
        return self.n

    def __getitem__(self, sl):
        lo, hi = sl.start - self.base, sl.stop - self.base
        if lo < 0 or hi > len(self.arr):
            raise IndexError('slice %d:%d is outside the rendered stretch' % (sl.start, sl.stop))
        return self.arr[lo:hi]


def late_fits(plan, label, n_each=3):
    """Fit isolated bursts at REAL late positions of a full-length plan (3 s on, the end of the page train, the first
    connection packets, the last second), rendering only the stretch round each from the plan's jobs and the
    regenerated noise: the access-code fit against the truth. Returns the errors in samples."""
    side = plan['sidecar']
    fs = side['sample_rate']
    ups = fs / 1e6
    rb = [e for e in side['bursts'] if e['rendered']]
    picks = []
    late = [e for e in rb if e['start_sample'] >= 3 * fs]
    picks += late[:n_each]
    pages = [e for e in rb if e['phase'] == 'page']
    if pages:
        picks += pages[-n_each:]
    conn = [e for e in rb if e['phase'] == 'connection']
    if conn:
        picks += conn[:n_each] + conn[len(conn) // 2:len(conn) // 2 + n_each] + conn[-n_each:]
    picks += rb[-n_each:]
    seen, errs = set(), []
    for e in picks:
        if e['index'] in seen:
            continue
        seen.add(e['index'])
        lo = max(0, int(e['start_sample']) - 3200)
        hi = int(e['start_sample'] + e['air_bits_length'] * ups + (e['tail']['dur_us'] or 0) * ups) + 3200
        arr = g.render_range(plan, lo, hi)
        win = Window(lo, arr, plan['total'])
        errs.append(fit(win, side, e)['start'] - (e['start_sample'] + e['timing_frac']))
    return np.array(errs)


def test_late():
    print('== real late positions of the full-length plans (isolated stretches)', flush=True)
    plan_a = g.plan_file('B_down', snr_db=HIGH, n_per_10s=100)
    pl = g.plan_file('B_down', snr_db=HIGH, duration=0.2, n_per_10s=300)
    full = np.concatenate(list(g.render_blocks(pl)))
    s_ = pl['sidecar']
    r0, r1 = 3_000_000, 3_400_000
    check(np.abs(g.render_range(pl, r0, r1) - full[r0:r1]).max() == 0,
          'render_range gives exactly render_blocks\' samples over a stretch (%d samples)' % (r1 - r0))
    del full
    for stem in ('D_same_s1', 'C2m10_50p25'):
        plan = g.plan_file(stem, snr_db=HIGH)
        e = late_fits(plan, stem)
        last = max(b['start_sample'] for b in plan['sidecar']['bursts'] if b['rendered']) / plan['fs']
        check(len(e) >= 5 and np.abs(e).max() < 0.1,
              '%s: %d bursts fitted at late real positions (3 s ... %.2f s, the page/connection boundary at %.2f s): '
              'worst %.3f sample' % (stem, len(e), last, plan['sidecar'].get('connection_start_sample', 0) / plan['fs'],
                                     np.abs(e).max()))
    plan = g.plan_file('B_down', snr_db=HIGH)
    e = late_fits(plan, 'B_down')
    check(len(e) >= 3 and np.abs(e).max() < 0.1, 'B_down: %d bursts fitted at late real positions: worst %.3f sample' % (
        len(e), np.abs(e).max()))


def test_d_samples():
    print('== D files, small renders (page train, then connection)', flush=True)
    for stem in ('D_same_s1', 'D_diff_s1'):
        iq, side, secs = make(stem, train_s=0.25, conn_s=0.3)
        ex = expect_of(stem)
        check_sidecar(side, ex, stem)
        check_positions(iq, side, ex, stem, n=36)
        check_drift(iq, side, ex, stem, n=30)
        check_decode(iq, side, stem, n=10)
        check_ids(iq, side, stem, n=6)
        check_channels(side, g.SPECS, stem)
        check_sizes(iq, side, stem)
        check_overlap(side, stem)
        b = side['bursts']
        cs = side['connection_start_sample']
        pg = [e for e in b if e['phase'] == 'page' and e['rendered']][-4:]
        cn = [e for e in b if e['phase'] == 'connection' and e['rendered']][:4]
        errs_ = [fit(iq, side, e)['start'] - (e['start_sample'] + e['timing_frac']) for e in pg + cn]
        check(len(pg) == 4 and len(cn) == 4 and max(abs(x) for x in errs_) < 0.1,
              '%s: across the page/connection boundary (last %d IDs, first %d packets of the connection) the '
              'starts fit within 0.1 sample, worst %.3f' % (stem, len(pg), len(cn), max(abs(x) for x in errs_)))
        if stem == 'D_same_s1':
            check(side['n_samples'] == len(iq) == int(round((0.02 + 0.25 + 0.3 + 0.002) * 40e6)),
                  '%s: n_samples %d' % (stem, len(iq)))
        del iq


def test_regen():
    print('== regeneration and the noise', flush=True)
    for stem, kw in (('C2m10_cross', dict(duration=0.2)), ('B_down', dict(duration=0.2, n_per_10s=300))):
        a = g.plan_file(stem, **FAST, **kw)
        h1 = hashlib.sha256()
        for blk in g.render_blocks(a):
            h1.update(np.ascontiguousarray(blk).tobytes())
        b = g.plan_file(stem, **FAST, **kw)
        h2 = hashlib.sha256()
        for blk in g.render_blocks(b):
            h2.update(np.ascontiguousarray(blk).tobytes())
        check(h1.digest() == h2.digest() and json.dumps(a['sidecar'], sort_keys=True) == json.dumps(b['sidecar'], sort_keys=True),
              '%s: regenerating gives the same bytes (%s) and the same sidecar' % (stem, h1.hexdigest()[:12]))
        iq = np.concatenate(list(g.render_blocks(a)))
        quiet_iq = np.concatenate(list(g.render_blocks(a, noise=False)))
        check_linear(a['sidecar'], quiet_iq, iq, stem)
        # the quiet render has nothing but the bursts: at least one stretch of exact zeros between bursts
        z = np.flatnonzero(np.abs(quiet_iq) == 0)
        check(len(z) > len(iq) // 2, '%s: the noise-free render is zero between bursts (%d of %d samples)' % (stem, len(z), len(iq)))
        del iq, quiet_iq


# --- mutants --------------------------------------------------------------------------------------------

_BASELINES = {}


def mutant(name, patches, stem, kw, label, expect_any):
    """Run the generator with ``patches`` ({attribute: replacement}) and the core checks on what it makes. The same
    file made without the patch must pass every core check (once per stem and arguments), and the mutant must trip
    at least one of the checks named by ``expect_any`` (substrings of their texts): a mutant that is only caught by
    a side effect is not the catch it is there for."""
    key = (stem, tuple(sorted(kw.items())))
    ex = expect_of(stem, **{k: v for k, v in kw.items() if k in ('offset_us', 'drift_ppm')})
    if key not in _BASELINES:
        iq, side, _ = make(stem, **kw)
        with quiet() as failed:
            run_core(iq, side, ex, 'base', n=10)
        _BASELINES[key] = failed
        check(not failed, 'unmutated %s %s passes every core check (%d fail %s)' % (stem, kw, len(failed), failed[:2]))
        del iq
    with contextlib.ExitStack() as st:
        for attr, repl in patches.items():
            st.enter_context(mock.patch.object(g, attr, repl))
        iq, side, _ = make(stem, **kw)
    with quiet() as failed:
        run_core(iq, side, ex, name, n=10)
    hit = [w for w in failed if any(sub in w for sub in expect_any)]
    check(bool(hit), 'mutant %s is caught by "%s" (%d checks fail in all)' % (
        label, hit[0].split(': ', 1)[-1][:90] if hit else '-', len(failed)))
    del iq


def late_mutants():
    """Placement mutants that only show late in a long file: the unmutated full-length plan passes ``late_fits``,
    a shift of 40 samples after 1 s and a shift of the connection part alone do not."""
    base = late_fits(g.plan_file('D_same_s1', snr_db=HIGH), 'base')
    check(np.abs(base).max() < 0.1, 'unmutated D_same_s1 passes the late fits (worst %.3f sample)' % np.abs(base).max())
    for label, shift in (('every burst shifted +40 samples after 1 s (samples only)',
                          lambda pos, ph: 40 if pos > 40e6 else 0),
                         ('the connection part alone shifted +40 samples (samples only)',
                          lambda pos, ph: 40 if ph == 'connection' else 0)):
        with mock.patch.object(g, 'placement_shift', shift):
            e = late_fits(g.plan_file('D_same_s1', snr_db=HIGH), 'mut')
        check(np.abs(e).max() > 10, 'mutant %s is caught by the late fits (worst %.1f sample)' % (label, np.abs(e).max()))


def test_mutants():
    print('== mutants', flush=True)
    kb = dict(duration=0.3, n_per_10s=400)
    kc = dict(duration=0.25)
    ka = dict(duration=0.2, n_per_10s=400)
    mutant('m1', {'truth_scale': lambda d: 1 / (1 - Fraction(str(d)) * Fraction(1, 10 ** 6))}, 'B_down', kb,
           'drift sign flipped in the truth only', ['are the stated ones', 'fitted from the samples'])
    mutant('m2', {'sample_scale': lambda d: 1 / (1 - Fraction(str(d)) * Fraction(1, 10 ** 6))}, 'B_down', kb,
           'drift sign flipped in the samples only', ['fitted from the samples: start_sample', 'measured drift'])
    mutant('m3', {'half_label': lambda h: None if h is None else ('second' if h == 'first' else 'first')}, 'C2m10_cross', kc,
           'half swapped', ['start_us is slot_start_us', 'second ID = first', 'nulls are where'])
    mutant('m4', {'tail_gain_db': lambda db: db + 6.0}, 'C2m10_cross', kc, 'tail level off by 6 dB', ['C2 tail level'])
    mutant('m5', {'tail_dev_khz': lambda khz: 50.0}, 'C2m10_cross', kc, 'tail frequency +50 kHz instead of +160',
           ['C2 tail frequency'])
    mutant('m6', {'truth_clk': lambda clk, role=None: clk + 2}, 'B_down', kb,
           'whitening clock shifted one slot in the truth', ['libbtbb decodes header', 'bits demodulated'])
    mutant('m7', {'wrap_offset': lambda x: x}, 'B_down', kb, 'offset not wrapped', ['grid_offset_us is slot_start_us mod'])
    mutant('m8', {'truth_lap': lambda lap: lap ^ 0x000100}, 'B_down', kb, 'lap swapped in the truth',
           ['fitted from the samples: start_sample', 'bits demodulated', 'libbtbb decodes'])
    mutant('m9', {'truth_n_samples': lambda n: n + 1}, 'B_down', kb, 'n_samples off by one', ['n_samples'])
    mutant('m10', {'check_channels': lambda *a, **k: None,
                   'a_channels': lambda fs, center: [31, 35, 37, 39, 43, 47]}, 'A_50p25', ka,
           'the centre channel used', ['no burst on the centre channel'])
    rng_frac = np.random.default_rng(5)
    late_mutants()
    mutant('m11', {'timing_jitter': lambda rng: float(rng_frac.uniform(0, 1))}, 'A_50p25', ka,
           'random timing_frac (p6-common\'s default)', ['start_us is slot_start_us', 'the grid offset is'])


def verify(cf32, json_path, n_pos=50, n_slots=100, n_dec=30, n_ids=60):
    """The checks of a written file, from its samples (a memory map: nothing is held whole), as statistics: at the
    files' own 20 dB a start is good to a fraction of a sample, not to 0.1 of one per burst.

        python scripts/test_bt_synth_slotb.py --verify synth_slotb_B_down.cf32 synth_slotb_B_down.json
    """
    side = json.load(open(json_path))
    iq = np.memmap(cf32, dtype='<c8', mode='r')
    fs = side['sample_rate']
    ups = fs / 1e6
    ex = dict(offset_us=side['grid_offset_us_param'], drift_ppm=side['drift_ppm_signed'])
    check(len(iq) == side['n_samples'] and os.path.getsize(cf32) == 8 * side['n_samples'] and
          side['n_bursts'] == len(side['bursts']),
          'file: %d samples, %d bytes, %d bursts (%d rendered): the sidecar\'s n_samples and n_bursts' % (
              len(iq), os.path.getsize(cf32), len(side['bursts']), len(rendered(side))))
    check_sidecar(side, ex, 'file')
    check_channels(side, None, 'file')
    check_overlap(side, 'file')
    # the noise floor: in the lead-in, and in gaps between bursts early, in the middle, late and at block seams
    rb = rendered(side)
    spans = sorted((int(e['start_sample']) - 100,
                    int(e['start_sample'] + (e['air_bits_length'] + (e['tail']['dur_us'] or 0) + 6) * ups) + 100)
                   for e in rb)
    gaps, prev = [], 0
    for lo, hi in spans:
        if lo - prev >= 2000:
            gaps.append((prev + 50, lo - 50))
        prev = max(prev, hi)
    if len(iq) - prev >= 2000:
        gaps.append((prev + 50, len(iq)))
    want = NOISE * ups
    def level(gs):
        tot, n = 0.0, 0
        for lo, hi in gs:
            hi = min(hi, lo + 20000)
            seg = np.asarray(iq[lo:hi]).astype(np.complex128)
            tot += float(np.sum(np.abs(seg) ** 2))
            n += len(seg)
        return 10 * np.log10(tot / n / want), n
    lead = [(0, int(0.015 * fs))]
    third = max(1, len(gaps) // 3)
    parts = dict(lead_in=lead, early=gaps[:third], middle=gaps[third:2 * third], late=gaps[-third:])
    seam = [(lo, hi) for lo, hi in gaps if any(lo <= m * NOISE_BLOCK_SAMPLES and m * NOISE_BLOCK_SAMPLES < hi
                                              for m in range(1, len(iq) // NOISE_BLOCK_SAMPLES + 1))]
    parts['block_seams'] = [(max(lo, m * NOISE_BLOCK_SAMPLES - 2000), min(hi, m * NOISE_BLOCK_SAMPLES + 2000))
                            for lo, hi in seam for m in range(1, len(iq) // NOISE_BLOCK_SAMPLES + 1)
                            if lo <= m * NOISE_BLOCK_SAMPLES < hi]
    levels = {k: level(v) for k, v in parts.items() if v}
    check(all(abs(v[0]) < 0.1 for v in levels.values()),
          'file: the noise floor against the common one: %s dB' % ', '.join(
              '%s %+.3f (%d samples)' % (k, v[0], v[1]) for k, v in levels.items()))
    # starts, as statistics
    picks = spread(rb, n_pos)
    errs = np.array([abs(fit(iq, side, e)['start'] - (e['start_sample'] + e['timing_frac'])) for e in picks])
    print('  start error over %d bursts: median %.3f, 95th percentile %.3f, max %.3f sample' % (
        len(picks), np.median(errs), np.percentile(errs, 95), errs.max()))
    bias = np.mean([fit(iq, side, e)['start'] - (e['start_sample'] + e['timing_frac']) for e in picks[:30]])
    check(np.median(errs) < 0.5 and errs.max() < 2.0 and abs(bias) < 0.2,
          'file: start_sample + timing_frac at %g dB: median %.3f sample, max %.3f, bias %+.3f (30 bursts); 0.1 sample '
          'per burst is held at 45 dB by the unit test, here the noise is the limit' % (
              side['snr_db'], np.median(errs), errs.max(), bias))
    # the drift over the whole file
    slots = spread([e for e in rb if e['role'] != 'slave' and e['half'] != 'second'], n_slots)
    x = np.array([e['clk'] // 4 for e in slots], dtype=float)
    y = np.array([fit(iq, side, e)['start'] / ups for e in slots])
    b_, a_ = np.polyfit(x, y, 1)
    drift = (1250.0 / b_ - 1) * 1e6
    check(abs(drift - side['drift_ppm_signed']) < 1.0,
          'file: drift from %d TX slots across the whole file %+.4f ppm, the stated %+.1f' % (
              len(slots), drift, side['drift_ppm_signed']))
    resid = y - (a_ + b_ * x)
    print('  line residual: rms %.4f us (%.2f sample)' % (np.sqrt(np.mean(resid ** 2)), np.sqrt(np.mean(resid ** 2)) * ups))
    want_cross = expected_crossings(ex['offset_us'], ex['drift_ppm'], side['n_samples'] / fs)
    got = side['crossings']
    check(len(got) == len(want_cross) and all(abs(c['time_s'] - w[0]) < 2e-6 for c, w in zip(got, want_cross)),
          'file: crossings %s' % ([(round(c['time_s'], 6), c['burst_index_first_after']) for c in got],))
    for c in got:
        i = c['burst_index_first_after']
        around = [e for e in side['bursts'][max(0, i - 60):i] if e['rendered']][-1:] + \
                 [e for e in side['bursts'][i:i + 60] if e['rendered']][:1]
        meas = [fit(iq, side, e)['start'] - (e['start_sample'] + e['timing_frac']) for e in around]
        u = [side['bursts'][i - 1]['grid_offset_unwrapped_us'], side['bursts'][i]['grid_offset_unwrapped_us']]
        print('  crossing at %.6f s, first burst after is %d: unwrapped offset %.6f -> %.6f us, mod 625 %.4f -> %.4f; '
              'neighbours %s fitted within %s sample' % (
                  c['time_s'], i, u[0], u[1], side['bursts'][i - 1]['grid_offset_us'], side['bursts'][i]['grid_offset_us'],
                  [e['index'] for e in around], ['%.3f' % m for m in meas]))
    # decode a sample of packets
    non = spread([e for e in rb if e['ptype'] != 'ID'], n_dec)
    ok = 0
    for e in non:
        f = fit(iq, side, e)
        got_bits = demod(iq, side, e, f['start'], len(model_bits(e)))
        if _lib is None:
            break
        btbb.uap = e['uap']
        taken, hdr, verdict, data = btbb(got_bits, e['clk'])
        full = bytes.fromhex(e['payload_full_hex']) if e['payload_full_hex'] else b''
        ok += int(taken and hdr == br.bits_int([int(c) for c in e['header_bits_18']]) and
                  (not full or (verdict == 10 and data[:len(full)] == full)))
    if non and _lib is not None:
        check(ok >= 0.9 * len(non), 'file: libbtbb decodes header and payload of %d of %d packets from the samples at 20 dB '
              '(UAP and clk of the sidecar)' % (ok, len(non)))
    # the IDs: tails, averaged over many IDs after a low pass
    ids = spread([e for e in rb if e['ptype'] == 'ID'], n_ids)
    if ids:
        pn = NOISE * ups
        taps = taps_for(fs)
        rows = []
        for e in ids:
            f = fit(iq, side, e)
            s0 = f['start']
            lo, hi = int(s0) - 300, int(s0 + 340 * ups) + 300
            z = fftconvolve(shifted(iq, lo, hi, e['channel_mhz'], side['center_mhz'], fs, extra_hz_of(e)), taps, 'same')
            t = (np.arange(lo, hi) - s0) / ups
            p = np.abs(z) ** 2
            quiet_w = (t >= 200) & (t <= 300)            # nothing is on the air there: the floor, low-passed
            p_noise = p[quiet_w].mean()
            s_noise = np.mean(z[quiet_w][1:] * np.conj(z[quiet_w][:-1]))   # its lag-1 autocorrelation (real)
            a_sync = p[(t >= 6) & (t <= 62)].mean() - p_noise
            tail = e['tail']
            if tail['variant'] == 'C2':
                win = (t >= 74) & (t <= 112)
                zz = z[win]
                s_tail = np.mean(zz[1:] * np.conj(zz[:-1])) - s_noise      # the noise's own term taken out
                rows.append((10 * np.log10((p[win].mean() - p_noise) / a_sync) - tail['level_db'],
                             np.angle(s_tail) / (2 * np.pi) * fs / 1e3 - tail['dev_khz'],
                             10 * np.log10(p[(t >= 134) & (t <= 200)].mean() / p_noise)))
            else:
                rows.append((0.0, 0.0, 10 * np.log10(p[(t >= 74) & (t <= 200)].mean() / p_noise)))
        r = np.array(rows)
        variant = ids[0]['tail']['variant']
        print('  %s tails over %d IDs: level error mean %+.2f dB (std %.2f), frequency error mean %+.2f kHz (std %.2f), '
              'after the ramp the window is %+.2f dB from the floor' % (
                  variant, len(ids), r[:, 0].mean(), r[:, 0].std(), r[:, 1].mean(), r[:, 1].std(), r[:, 2].mean()))
        if variant == 'C2':
            check(abs(r[:, 0].mean()) < 1.0 and abs(r[:, 1].mean()) < 5.0,
                  'file: C2 tail mean level %+.2f dB and frequency %+.2f kHz from the stated, over %d IDs' % (
                      r[:, 0].mean(), r[:, 1].mean(), len(ids)))
        # nothing after the ramp: against the same window far from any burst
        check(abs(r[:, 2].mean()) < 0.5, 'file: %s: after the %s the carrier is gone: the window is %+.2f dB from '
              'the floor (%d IDs)' % (variant, 'ramp' if variant == 'C1' else 'tail', r[:, 2].mean(), len(ids)))
    # across the page / connection boundary (D files)
    if 'connection_start_sample' in side:
        pg = [e for e in rb if e['phase'] == 'page'][-4:]
        cn = [e for e in rb if e['phase'] == 'connection'][:4]
        errs_ = [abs(fit(iq, side, e)['start'] - (e['start_sample'] + e['timing_frac'])) for e in pg + cn]
        print('  across the boundary (sample %d): last %d IDs and first %d packets of the connection fitted within %s sample'
              % (side['connection_start_sample'], len(pg), len(cn), ['%.3f' % x for x in errs_]))
        check(len(errs_) == 8 and max(errs_) < 1.5 and np.median(errs_) < 0.3,
              'file: the grid continues across the page/connection boundary: 8 bursts fitted at 20 dB, median %.3f, '
              'max %.3f sample (the same noise-limited statistic as the starts above)' % (np.median(errs_), max(errs_)))
    if 'C2' in {e['tail']['variant'] for e in side['bursts']} or 'C1' in {e['tail']['variant'] for e in side['bursts']}:
        ph = [e['symbol_phase'] for e in rb if e['half'] == 'first']
        ph2 = [e['symbol_phase'] for e in rb if e['half'] == 'second']
        print('  symbol phases of the RENDERED IDs: %d distinct among %d first IDs, %d distinct among %d second IDs' % (
            len(set(ph)), len(ph), len(set(ph2)), len(ph2)))
    failed = [w for ok_, w in RESULTS if not ok_]
    print('RESULT: PASS' if not failed else 'RESULT: FAIL (%d)' % len(failed))
    return 1 if failed else 0


NOISE_BLOCK_SAMPLES = g.NOISE_BLOCK


def main():
    t0 = time.time()
    steps = []
    for name, fn in (('files', test_files), ('pages', test_pages), ('plans', test_plans), ('d_samples', test_d_samples),
                     ('late', test_late), ('regen', test_regen), ('mutants', test_mutants)):
        t = time.time()
        fn()
        steps.append((name, time.time() - t))
    failed = [w for ok, w in RESULTS if not ok]
    print('\n%d checks, %.0f s (%s)' % (len(RESULTS), time.time() - t0, ', '.join('%s %.0f' % s for s in steps)))
    for w in failed:
        print('FAILED:', w)
    print('RESULT: PASS' if not failed else 'RESULT: FAIL (%d)' % len(failed))
    return 1 if failed else 0


if __name__ == '__main__':
    if len(sys.argv) >= 4 and sys.argv[1] == '--verify':
        sys.exit(verify(sys.argv[2], sys.argv[3]))
    sys.exit(main())
