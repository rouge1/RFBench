#!/usr/bin/env python3
"""Hold the second slot-boundary set (page responses, connection-first ID mixes, IDs at 20 MS/s) to its samples.

    python scripts/test_bt_synth_slotb2.py
    python scripts/test_bt_synth_slotb2.py --verify synth_slotb2_R_s1.cf32 synth_slotb2_R_s1.json

``scripts/bt_synth_slotb2.py`` writes 14 files for bluey-ox-walker, whose decoder feeds the 54 bit positions after
an ID's sync word to its UAP voter as if they were a header.  P1 (``R_s1..3``, ``R_20msps_s1``): repeated
spec-true page exchanges (the pager's IDs, the paged device's ID response, the FHS, the ID ack, a POLL and 6 to 12
connection packets), the paged device's three burst kinds on a carrier offset and with a timing offset of their
own.  P2 (``X_first_*``, ``X_bda_*``): a connection on a LAP and a page train on the *same* LAP, in the free master
slots, the connection first or in the middle, with a stated share of IDs.  P4 (``C_20msps``, ``R_20msps_s1``):
IDs at 20 MS/s.  The truth is worth only what it is against the *samples*, so the checks that can be made on
samples are (45 dB, 0.6 to 1.5 s renders, the starts to 0.1 sample) and the rules are written out here again,
never taken from the generator's own bookkeeping:

* **start / keys**: each burst's model, from the sidecar's own fields, is fitted to the samples at its channel (the
  repository's modulator, the sidecar's carrier offset in the model): ``start_sample + timing_frac`` within 0.1
  sample; ``start_us = slot_start_us + slot_offset_us + timing_offset_us`` and the offset keys recomputed;
  ``id_end_sample`` is the end of bit 68, and the falling edge of the envelope is its half-amplitude point one
  microsecond (``br.gfsk``'s raised-cosine ramp, 2 us) later;
* **decode**: libbtbb decodes every non-ID burst, FHS included, with the sidecar's UAP and whitening clock and
  does not at +/- 2 ticks; ``header_air_bits_54`` are the 54 bits demodulated;
* **P1 sequence**: the heard ID is the first half-slot of the pager's IDs on the paged device's scan frequency,
  the response is 625 us (+ its timing offset) after it, the FHS 1250 us after the first ID of the heard slot, the
  ack 625 us after the FHS start, the POLL 1250 us after the FHS; every channel is what ``bt_hop_substates`` and
  ``bt_hop`` give for the clocks; the paged LAP is on the IDs and the FHS's access code, the master's on POLL and
  the connection, and the FHS payload decodes (the spec's field layout, written out here) to the master's LAP,
  UAP, native clock and the LT_ADDR the connection uses; the slave's carrier offset is measured from the samples;
* **P2**: the share of IDs among the LAP's rendered bursts from the sidecar equals the stated one and the target
  within 3 points; the first header precedes the first ID (X_first); no ID in a slot the connection uses;
  IDs before, during and after (X_bda); **P4**: 20 samples per symbol, the 8 MHz window, phases 5 and 15;
* **no tail**: after an ID's last sync symbol the carrier is gone within the ramp, from the samples;
* mutants of the generator, each caught.

Needs libbtbb (``bt_ota_check.libbtbb``); the checks that need it are skipped, and said so, without it.
"""
import contextlib
import hashlib
import json
import math
import os
import sys
import time
from unittest import mock

os.environ.setdefault('OMP_NUM_THREADS', '4')
import numpy as np  # noqa: E402
from scipy.signal import fftconvolve, welch  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from apps import bt_fhs  # noqa: E402
from apps import bt_hop  # noqa: E402
from apps import bt_hop_substates as hs  # noqa: E402
from scripts import bt_synth  # noqa: E402
from scripts import bt_synth_slotb2 as g  # noqa: E402
from scripts import test_bt_synth_slotb as ts  # noqa: E402  (pure helpers only: fit, demod, btbb, shifted ...)
from scripts.bt_synth_interf import noise_block  # noqa: E402

NOISE = bt_synth.AMPLITUDE ** 2 / 100          # noise power in 1 MHz, the common floor
HIGH = 45.0
RESULTS = []
QUIET = [False]
SLOT_US = 625.0
_lib = ts._lib
AVOID_UAPS = {0x00, 0xD6, 0x0B, 0xDD}
RESERVED = range(0x9E8B00, 0x9E8B40)
ROLES = {'id_page', 'id_response', 'id_ack', 'fhs', 'master', 'slave'}
SENDER = {'id_page': 'master', 'id_response': 'slave', 'id_ack': 'slave', 'fhs': 'master', 'master': 'master',
          'slave': 'slave'}
AM_ADDR_LABEL = 'LT_ADDR the FHS assigns'


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


# --- the model of a burst: ts.fit uses ``ts.model_bits``; an FHS is not a br.Packet -------------------------

def model_bits(e):
    """The bits of a burst from the sidecar's own fields: an ID's 68, an FHS from ``bt_fhs`` and its payload
    fields, any other packet from ``br.Packet``."""
    if e['ptype'] == 'ID':
        return np.array(br.access_code(e['lap'], trailer=False), dtype=float)
    if e['ptype'] == 'FHS':
        f = e['fhs']
        p = bt_fhs.page_response(e['lap'], e['uap'], f['lap'], f['uap'], f['nap'], f['cod'], f['am_addr'], f['fhs_clk'],
                                 f['whitening_x'], sr=f['sr'])
        return np.array(p.bits, dtype=float)
    p = br.Packet(e['lap'], e['uap'], e['clk'], e['ptype'], bytes.fromhex(e['payload_hex']), lt_addr=e['lt_addr'],
                  flow=e['flow'], arqn=e['arqn'], seqn=e['seqn'])
    return np.array(p.bits, dtype=float)


ts.model_bits = model_bits
fit, demod, btbb, shifted, spread, rendered, circ = ts.fit, ts.demod, ts.btbb, ts.shifted, ts.spread, ts.rendered, ts.circ


def decode_clk(e):
    """The clock libbtbb is handed: whose CLK6-1 is the whitening seed (for an FHS the Xprc pseudo-clock)."""
    return int(e['whitening_clk6_1']) << 1


# --- the checks that need the samples -----------------------------------------------------------------------

REQUIRED = ['index', 'start_sample', 'timing_frac', 'symbol_phase', 'channel', 'channel_mhz', 'ptype', 'role',
            'sender', 'ours', 'half', 'clk', 'lt_addr', 'lap', 'uap', 'payload_hex', 'payload_full_hex', 'snr_db',
            'rendered', 'in_window', 'train', 'phase', 'tail', 'slot_start_us', 'grid_offset_us',
            'grid_offset_unwrapped_us', 'start_us', 'start_mod_625_us', 'slot_offset_us', 'timing_offset_us',
            'header_bits_18', 'whitening_clk6_1', 'header_air_bits_54', 'cfo_hz', 'exchange', 'tick',
            'id_end_sample', 'id_end_us', 'whitening_clk', 'fhs']
TOP = ['generator', 'generator_commit', 'lap', 'uap', 'sample_rate', 'center_mhz', 'snr_db', 'cfo_hz', 'clk_convention',
       'seed', 'grid_offset_us_first_slot', 'drift_ppm_signed', 'drift_law', 'grid_offset_definition',
       'slot_grid_note', 'symbol_phase_note', 'n_bursts', 'n_samples', 'per_burst_keys', 'notes', 'air_bits_omitted',
       'tail_notes', 'id_end_note', 'counts', 'selection_note']


def check_sidecar(side, label, off0=50.25, drift=0.0):
    """The sidecar's own arithmetic, from ``start_sample + timing_frac``, against the definitions."""
    b = side['bursts']
    fs = side['sample_rate']
    ups = fs / 1e6
    sps = int(round(ups))
    miss = [k for k in TOP if k not in side]
    check(not miss, '%s: top-level keys all there (missing %s)' % (label, miss))
    bad = [e['index'] for e in b if any(k not in e for k in REQUIRED)]
    check(not bad and [e['index'] for e in b] == list(range(len(b))), '%s: every burst has every key, index is the order' % label)
    check(side['air_bits_omitted'] is True and not any('air_bits' in e for e in b), '%s: air_bits are left out' % label)
    st = [e['start_sample'] + e['timing_frac'] for e in b]
    check(all(x <= y for x, y in zip(st, st[1:])), '%s: bursts are sorted by start' % label)
    check(all(abs(e['start_us'] - (e['start_sample'] + e['timing_frac']) / ups) < 1e-6 and 0 <= e['timing_frac'] < 1
              and e['symbol_phase'] == e['start_sample'] % sps for e in b),
          '%s: start_us = (start_sample + timing_frac) / %g us and symbol_phase = start_sample mod %d' % (label, ups, sps))
    check(all(abs(e['start_us'] - e['slot_start_us'] - e['slot_offset_us'] - e['timing_offset_us']) < 1e-6 for e in b),
          '%s: start_us = slot_start_us + slot_offset_us + timing_offset_us' % label)
    check(all(e['slot_offset_us'] in (0.0, 312.5, 625.0, 937.5) for e in b),
          '%s: slot_offset_us is 0, 312.5, 625 or 937.5 (the burst\'s half-slot in its TX slot)' % label)
    ok = all(0 <= e['grid_offset_us'] < SLOT_US and abs(circ(e['grid_offset_us'], e['slot_start_us'])) < 1e-6 and
             0 <= e['start_mod_625_us'] < SLOT_US and
             abs(circ(e['start_mod_625_us'], e['start_us'] % SLOT_US)) < 1e-6 for e in b)
    check(ok, '%s: grid_offset_us is slot_start_us mod 625 and start_mod_625_us is start_us mod 625' % label)
    # one grid throughout: slot starts on 20000 + off0 + 1250 j
    j = np.array([(e['slot_start_us'] - 20000.0 - off0) / 1250.0 for e in b])
    check(abs(drift) > 0 or (np.abs(j - np.round(j)).max() < 1e-6 and
                             all(abs(e['grid_offset_us'] - off0) < 1e-6 for e in b)),
          '%s: one grid throughout: every TX slot is 20000 + %.2f + 1250 j us, grid_offset_us %.2f for every burst' % (
              label, off0, off0))
    check(abs(side['drift_ppm_signed'] - drift) < 1e-9 and abs(circ(side['grid_offset_us_first_slot'], off0)) < 1e-9,
          '%s: the stated grid: offset %.2f us, drift %+g ppm, in the sidecar' % (label, off0, drift))
    ids = [e for e in b if e['ptype'] == 'ID']
    non = [e for e in b if e['ptype'] != 'ID']
    ok = all(e['id_end_sample'] is not None and
             abs(e['id_end_sample'] - (e['start_sample'] + e['timing_frac'] + 68 * sps)) < 1e-6 and
             abs(e['id_end_us'] - e['id_end_sample'] / ups) < 1e-6 for e in ids)
    check(ok and all(e['id_end_sample'] is None and e['id_end_us'] is None for e in non),
          '%s: id_end_sample = start_sample + timing_frac + 68 sps and id_end_us its microseconds, on every ID '
          '(None on the packets)' % label)
    ok = (all(e['role'] in ROLES and e['sender'] == SENDER[e['role']] and e['ours'] is True for e in b) and
          all(e['ptype'] == 'ID' for e in b if e['role'] in ('id_page', 'id_response', 'id_ack')) and
          all(e['ptype'] == 'FHS' for e in b if e['role'] == 'fhs') and
          all(e['ptype'] not in ('ID', 'FHS') for e in b if e['role'] in ('master', 'slave')))
    check(ok, '%s: role is one of %s, sender master/slave as the role says, ours true, ptype to match' % (
        label, sorted(ROLES)))
    check(all((e['header_bits_18'] is None) == (e['ptype'] == 'ID') for e in b) and
          all(len(e['header_air_bits_54']) == 54 for e in non), '%s: IDs have no header, every packet has 54 header bits' % label)
    for e in b:
        if e['ptype'] == 'ID' and e['tail']['variant'] != 'C1':
            check(False, '%s: burst %d is an ID with a tail %s' % (label, e['index'], e['tail']))
            break
    else:
        check(True, '%s: every ID is the C1 kind: no tail variant, no held tail' % label)
    check('no held tail' in side['tail_notes'].lower() or 'no tail' in side['tail_notes'].lower(),
          '%s: the notes say there is no held tail' % label)


def check_positions(iq, side, label, n=36, tol=0.1):
    ups = side['sample_rate'] / 1e6
    picks = spread(rendered(side), n)
    errs, rc = [], []
    for e in picks:
        f = fit(iq, side, e)
        errs.append(f['start'] - (e['start_sample'] + e['timing_frac']))
        slot_us = f['start'] / ups - e['slot_offset_us'] - e['timing_offset_us']
        rc.append((abs(circ(slot_us, e['slot_start_us'], 1e12)) * ups,
                   abs(circ(slot_us % SLOT_US, e['grid_offset_us'])) * ups,
                   abs(circ(f['start'] / ups, e['start_us'], 1e12)) * ups))
    errs = np.array(errs)
    rc = np.array(rc)
    check(len(picks) >= 10 and np.abs(errs).max() < tol,
          '%s: %d bursts fitted from the samples (carrier offset of the sender in the model): start_sample + '
          'timing_frac within %.1f sample, worst %.3f' % (label, len(picks), tol, np.abs(errs).max()))
    check(rc.max() < tol, '%s: slot_start_us, grid_offset_us, start_us recomputed from the measured starts and the '
          'slot and timing offsets, worst %s sample' % (label, ', '.join('%.3f' % x for x in rc.max(axis=0))))


def check_decode(iq, side, label, n=14):
    """Bits demodulated from the samples are the packet's; libbtbb decodes at the sidecar's UAP and whitening clock
    and not at +/- 2 ticks."""
    non = [e for e in rendered(side) if e['ptype'] != 'ID']
    by = {}
    for e in non:
        by.setdefault((e['role'], e['ptype']), []).append(e)
    picks = []
    for key in sorted(by):
        picks += by[key][:2]
    if len(picks) > n:
        picks = spread(picks, n)
    if not picks:
        return
    ok_bits, ok_air, ok_hdr, ok_wrong = [], [], [], []
    for e in picks:
        f = fit(iq, side, e)
        want = model_bits(e)
        got = demod(iq, side, e, f['start'], len(want))
        ok_bits.append(np.array_equal(got, want.astype(np.uint8)))
        ok_air.append(''.join(str(int(x)) for x in got[72:126]) == e['header_air_bits_54'])
        ts.btbb.uap = e['uap']
        taken, hdr, verdict, data = btbb(got, decode_clk(e))
        want_hdr = br.bits_int([int(c) for c in e['header_bits_18']])
        ok_hdr.append(taken and hdr == want_hdr)
        bad_clk = []
        for dc in (-2, 2):
            t2, h2, v2, d2 = btbb(got, decode_clk(e) + dc)
            flag = e['hec_alias_clk_plus2'] if dc > 0 else e['hec_alias_clk_minus2']
            bad_clk.append(t2 == bool(flag) and (not t2 or h2 != want_hdr or True))
        ok_wrong.append(all(bad_clk))
    check(len(picks) >= 4 and all(ok_bits), '%s: bits demodulated from the samples are the packet\'s (%d of %d, %d kinds, '
          'FHS included)' % (label, sum(ok_bits), len(ok_bits), len(by)))
    check(all(ok_air), '%s: header_air_bits_54 are the 54 bits after the access code, demodulated (%d of %d)' % (
        label, sum(ok_air), len(ok_air)))
    if _lib is None:
        print('  skip libbtbb is not installed: the decode checks are not made')
        return
    check(all(ok_hdr), '%s: libbtbb decodes the header at the sidecar\'s UAP and whitening clock (HEC passes) (%d of %d)' % (
        label, sum(ok_hdr), len(ok_hdr)))
    check(all(ok_wrong), '%s: at whitening clock +/- 2 ticks libbtbb accepts the header only where the HEC aliases '
          '(hec_alias_clk_minus2 / plus2)' % label)


def fit_id_end(iq, side, e):
    """Where the ID's amplitude ramp begins, from the samples: the end of bit 68. ``|z|^2`` of the carrier taken
    down to zero is fitted by floor + A^2 (raised-cosine amplitude ramp of 2 us starting at ``t_end``) over a window
    round the stated end, ``t_end`` searched in steps of 0.05 us. Returns the fitted ``t_end`` in samples (compare
    with ``id_end_sample``); the half-amplitude point of the falling edge is 1 us (``sps`` samples) after it."""
    fs, center = side['sample_rate'], side['center_mhz']
    ups = fs / 1e6
    s_end = e['id_end_sample']
    lo, hi = int(s_end - 45 * ups), int(s_end + 45 * ups)
    z = shifted(iq, lo, hi, e['channel_mhz'], center, fs, float(e['cfo_hz']))
    p = np.abs(z) ** 2
    x = np.arange(lo, hi) - s_end                           # samples from the stated end
    a2 = p[(x >= -40 * ups) & (x <= -5 * ups)].mean()
    floor = p[(x >= 6 * ups) & (x <= 40 * ups)].mean()
    sel = (x >= -12 * ups) & (x <= 14 * ups)
    best, best_err = None, None
    for shift in np.arange(-4.0, 4.0, 0.05):
        u = (x[sel] / ups - shift) / RAMP_US_T                # ramp position in [0, 1]
        env = np.where(u <= 0, 1.0, np.where(u >= 1, 0.0, 0.5 + 0.5 * np.cos(np.pi * np.clip(u, 0, 1))))
        err = float(np.sum((p[sel] - (floor + (a2 - floor) * env ** 2)) ** 2))
        if best_err is None or err < best_err:
            best, best_err = shift, err
    return s_end + best * ups


RAMP_US_T = 2.0


def check_ids(iq, side, label, n=10, edge_tol=1.0):
    """IDs: the 68 bits are the access code's, the carrier is gone after the ramp (no held tail), the falling
    edge of the envelope is where ``id_end_sample`` says (plus the ramp's 1 us half-way point)."""
    ids = [e for e in rendered(side) if e['ptype'] == 'ID']
    if not ids:
        return
    fs, center = side['sample_rate'], side['center_mhz']
    ups = fs / 1e6
    pn = NOISE * ups
    taps = ts.taps_for(fs)
    ok_bits, drops, edges, gone = [], [], [], []
    for e in spread(ids, n):
        f = fit(iq, side, e)
        s = f['start']
        got = demod(iq, side, e, s, 68)
        ok_bits.append(list(got) == list(br.access_code(e['lap'], trailer=False)))
        lo, hi = int(s) - 200, int(s + 200 * ups)
        zr = shifted(iq, lo, hi, e['channel_mhz'], center, fs, float(e['cfo_hz']))
        t = (np.arange(lo, hi) - s) / ups
        pr = np.abs(zr) ** 2                                 # unfiltered: nothing smears the edge
        drops.append(10 * np.log10(pr[(t >= 10) & (t <= 60)].mean() / pr[(t >= 68 + 4) & (t <= 68 + 40)].mean()))
        gone.append(10 * np.log10(pr[(t >= 68 + 12) & (t <= 68 + 40)].mean() / pr[(t >= 150) & (t <= 190)].mean()))
        edges.append(fit_id_end(iq, side, e) - e['id_end_sample'])
    check(all(ok_bits), '%s: the 68 bits of %d IDs, demodulated, are the preamble and sync word of the LAP' % (
        label, len(ok_bits)))
    check(min(drops) >= 15.0, '%s: no held tail: the carrier power drops by >= 15 dB within 4 us of the ID\'s end '
          '(worst %.1f dB over %d IDs)' % (label, min(drops), len(drops)))
    check(max(abs(x) for x in gone) < 0.5, '%s: and 12 to 40 us after it the power is the floor of 150 to 190 us '
          '(within %.2f dB)' % (label, max(abs(x) for x in gone)))
    check(max(abs(x) for x in edges) <= edge_tol, '%s: the falling edge of the envelope (a 2 us raised-cosine '
          'ramp, half-amplitude point 1 us on) starts at id_end_sample within %.1f sample, worst %.2f (%d IDs)' % (
              label, edge_tol, max(abs(x) for x in edges), len(edges)))


def check_channels(side, label):
    ctr = int(round(side['center_mhz'] - 2402))
    half = 12.0 if side['sample_rate'] > 30e6 else 8.0
    b = side['bursts']
    check(all(e['channel'] != ctr for e in b), '%s: no burst on the centre channel %d' % (label, ctr))
    ok = all(e['in_window'] == (abs(e['channel_mhz'] - side['center_mhz']) <= half + 1e-9) and
             e['rendered'] == e['in_window'] for e in b)
    check(ok, '%s: in_window is |channel MHz - centre| <= %g MHz and only those bursts are rendered' % (label, half))
    check(all(abs(e['channel_mhz'] - 2402 - e['channel']) < 1e-9 for e in b), '%s: channel_mhz = 2402 + channel' % label)


def check_overlap(side, label):
    """Nothing overlaps in time (one transmitter then the other, one at a time), ramps included."""
    b = side['bursts']
    allv = sorted((e['start_us'], e['start_us'] + e['air_bits_length'] + 2.0, e['index']) for e in b)
    bad = [(i, j) for (s0, e0, i), (s1, e1, j) in zip(allv, allv[1:]) if s1 < e0]
    check(not bad, '%s: no two bursts overlap in time (%d bursts, %d overlaps%s)' % (
        label, len(b), len(bad), ' e.g. %s' % bad[:2] if bad else ''))
    return len(bad)


def check_sizes(iq, side, label):
    check(side['n_samples'] == len(iq) and side['n_bursts'] == len(side['bursts']),
          '%s: n_samples %d is the array (%d), n_bursts %d the list (%d)' % (
              label, side['n_samples'], len(iq), side['n_bursts'], len(side['bursts'])))
    check(all(sorted(e) == side['per_burst_keys'] for e in side['bursts']),
          '%s: per_burst_keys are the keys of every burst (id_end_sample and id_end_us among them)' % label)
    check('id_end_sample' in side['per_burst_keys'] and 'id_end_us' in side['per_burst_keys'] and
          'id_end_sample' in side['id_end_note'], '%s: id_end_sample / id_end_us are in per_burst_keys and in the notes' % label)


def check_noise(iq, side, label):
    fs = side['sample_rate']
    ups = fs / 1e6
    seg = iq[:int(0.015 * fs)]
    power = np.mean(np.abs(seg.astype(np.complex128)) ** 2)
    check(abs(10 * np.log10(power / (NOISE * ups))) < 0.05, '%s: noise power in the lead-in is the common floor within '
          '0.05 dB (%+.3f dB)' % (label, 10 * np.log10(power / (NOISE * ups))))
    f, pxx = welch(seg, fs=fs, nperseg=2048, return_onesided=False)
    bands = np.fft.fftshift(pxx).reshape(8, -1).mean(axis=1)
    check(10 * np.log10(bands.max() / bands.min()) < 0.2, '%s: white across the whole band' % label)


def measured_cfo(iq, side, e):
    """The carrier offset of a burst from the samples: the model at the stated start with NO offset against the
    samples, the mean phase advance per lag (the sync word's own mean deviation is in the model, not in the answer)."""
    fs, center = side['sample_rate'], side['center_mhz']
    f = fit(iq, side, e)
    bits = model_bits(e)
    i0 = math.floor(f['start'])
    m, ld = ts.model_signal(e, bits, fs, f['start'] - i0)
    z = shifted(iq, i0 - ld, i0 - ld + len(m), e['channel_mhz'], center, fs, 0.0)
    prod = z * np.conj(m)
    lag = min(int(round(5e-6 * fs)), len(prod) // 2)      # 5 us: unambiguous to +-100 kHz
    return float(np.angle(np.sum(prod[lag:] * np.conj(prod[:-lag]))) / (2 * np.pi * lag) * fs)


# --- the P1 plan checks (sidecar and kernels; no samples) ----------------------------------------------------

def koff(clk):
    return 24 if ((clk >> 12) & 1) == 0 else 8


def groups(side):
    out = {}
    for e in side['bursts']:
        if e['exchange'] is not None:
            out.setdefault(e['exchange'], []).append(e)
    return out


def check_p1(side, label):
    """The exchanges of a P1 file: sequence, timing, channels, laps, FHS."""
    plap, puap = side['paged_lap'], side['paged_uap']
    mlap, muap = side['master_lap'], side['master_uap']
    maddr = ((muap & 0xF) << 24) | mlap
    gs = groups(side)
    meta = side['exchanges']
    n_ex = len(meta)
    check(n_ex == len(gs) and n_ex >= 1, '%s: %d exchanges in the sidecar and in the bursts' % (label, n_ex))
    ok_t = {k: True for k in ('resp', 'fhs', 'ack', 'poll', 'win', 'heard', 'chan', 'clk', 'fhsx', 'order', 'n', 'conn')}
    why = {}

    def bad(k, i, msg):
        if ok_t[k]:
            why[k] = 'exchange %d: %s' % (i, msg)
        ok_t[k] = False

    for i, ex in enumerate(meta):
        B = gs[i]
        ids = [b for b in B if b['role'] == 'id_page']
        resp = [b for b in B if b['role'] == 'id_response']
        fhs = [b for b in B if b['role'] == 'fhs']
        ack = [b for b in B if b['role'] == 'id_ack']
        masters = [b for b in B if b['role'] == 'master']
        slaves = [b for b in B if b['role'] == 'slave']
        if not (len(resp) == len(fhs) == len(ack) == 1 and ids and masters):
            bad('n', i, 'roles %d/%d/%d/%d' % (len(resp), len(fhs), len(ack), len(masters)))
            continue
        resp, fhs, ack = resp[0], fhs[0], ack[0]
        e0, s0 = ex['clke0'], ex['paged_clkn0']
        heard = None
        for b in ids:
            if b['channel'] == hs.page_scan(s0 + b['tick'], plap, puap):
                heard = b
                break
        if heard is None or heard['tick'] != ex['heard_tick']:
            bad('heard', i, 'the heard ID is not the first half-slot on the scan frequency')
            continue
        hs_slot = heard['tick'] // 4
        first_of_slot = next(x for x in ids if x['tick'] == 4 * hs_slot)
        ok_t['heard'] &= (max(x['tick'] for x in ids) == 4 * hs_slot + 1 and
                          len({x['tick'] for x in ids}) == len(ids))
        if abs(resp['start_us'] - heard['start_us'] - 625.0 - resp['timing_offset_us']) > 1e-6:
            bad('resp', i, 'response %.4f us after the heard ID, timing_offset %.3f' % (
                resp['start_us'] - heard['start_us'], resp['timing_offset_us']))
        if abs(fhs['start_us'] - first_of_slot['start_us'] - 1250.0) > 1e-6 or fhs['timing_offset_us'] != 0:
            bad('fhs', i, 'FHS %.4f us after the first ID of the heard slot' % (fhs['start_us'] - first_of_slot['start_us']))
        if abs(ack['start_us'] - fhs['start_us'] - 625.0 - ack['timing_offset_us']) > 1e-6:
            bad('ack', i, 'ack %.4f us after the FHS' % (ack['start_us'] - fhs['start_us']))
        poll = masters[0]
        if abs(poll['start_us'] - fhs['start_us'] - 1250.0) > 1e-6 or poll['ptype'] != 'POLL':
            bad('poll', i, 'POLL %.4f us after the FHS (%s)' % (poll['start_us'] - fhs['start_us'], poll['ptype']))
        if not all(b['in_window'] and b['rendered'] for b in (heard, resp, fhs, ack)):
            bad('win', i, 'heard/response/FHS/ack not all in the window')
        # channels from the kernels, from the clocks
        for b in ids:
            clk = e0 + b['tick']
            if b['channel'] != hs.page(clk, plap, puap, koff(clk)) or b['clk'] != ex['master_clkn0'] + b['tick']:
                bad('chan', i, 'page channel of tick %d' % b['tick'])
                break
        frozen = s0 + heard['tick']
        n_r = hs.n_of(s0 + resp['tick'], s0 + 4 * hs_slot)
        if resp['tick'] != heard['tick'] + 2 or resp['channel'] != hs.peripheral_page_response(frozen, s0 + resp['tick'], plap, puap, n_r):
            bad('chan', i, 'response channel')
        cf = e0 + resp['tick']
        if (fhs['tick'] != 4 * hs_slot + 4 or
                fhs['channel'] != hs.central_page_response(cf, e0 + fhs['tick'], plap, puap, koff(cf), 0, 1)):
            bad('chan', i, 'FHS channel')
        n_a = hs.n_of(s0 + ack['tick'], s0 + 4 * hs_slot)
        if ack['tick'] != fhs['tick'] + 2 or ack['channel'] != hs.peripheral_page_response(frozen, s0 + ack['tick'], plap, puap, n_a):
            bad('chan', i, 'ack channel')
        # the FHS whitening X-input is EQ 6 of the frozen estimate
        if fhs['fhs']['whitening_x'] != bt_fhs.xprc(cf, koff(cf), 0, 1):
            bad('fhsx', i, 'FHS Xprc')
        # connection: the basic sequence at each packet's own clock, 6 to 12 masters, the answer one slot later
        if not (6 <= len(masters) <= 12 and len(slaves) == len(masters)):
            bad('conn', i, '%d masters and %d answers' % (len(masters), len(slaves)))
        for m, s in zip(masters, slaves):
            if (m['channel'] != bt_hop.hop_channel(m['clk'], maddr, None) or
                    s['channel'] != bt_hop.hop_channel(s['clk'], maddr, None) or s['clk'] != m['clk'] + 2 or
                    abs(s['start_us'] - m['start_us'] - 625.0 - s['timing_offset_us']) > 1e-6):
                bad('conn', i, 'connection pair clk %d' % m['clk'])
                break
        # the master's native clock runs on: fhs clk, poll clk, one grid
        mk = ex['master_clkn0']
        if fhs['clk'] != mk + fhs['tick'] or fhs['fhs']['fhs_clk'] != fhs['clk'] or poll['clk'] != mk + poll['tick']:
            bad('clk', i, 'master clocks')
        if [b['tick'] for b in B if b['role'] == 'id_page'] != sorted(b['tick'] for b in B if b['role'] == 'id_page'):
            bad('order', i, 'tick order')
    msgs = {'resp': 'the ID response is the heard ID + 625 us + its timing_offset_us',
            'fhs': 'the FHS is the first ID of the heard slot + 1250 us',
            'ack': 'the ack is the FHS + 625 us + its timing_offset_us',
            'poll': 'the first POLL is the FHS + 1250 us',
            'win': 'heard ID, response, FHS and ack are all in_window and rendered',
            'heard': 'the heard ID is the first half-slot on the paged device\'s scan frequency, the pager sends '
                     'no ID after the heard slot',
            'chan': 'every page, response, FHS and ack channel is bt_hop_substates\' for the clocks',
            'clk': 'the FHS carries and the POLL has the master\'s native clock, run on from the exchange',
            'fhsx': 'the FHS whitening X-input is bt_fhs.xprc of the frozen CLKE',
            'conn': '6 to 12 connection packets on bt_hop.hop_channel at their own clocks, the answer at clk + 2, '
                    'one slot after',
            'n': 'each exchange has one response, one FHS, one ack and a connection'}
    for k, v in msgs.items():
        check(ok_t[k], '%s: %s (%d exchanges)%s' % (label, v, n_ex, '' if ok_t[k] else '  [%s]' % why.get(k, '')))
    # the heard ID is 20 to 300 ms into the train
    ms = [ex['heard_tick'] * 0.3125 for ex in meta]
    check(ms and all(20.0 <= x <= 300.0 for x in ms), '%s: the train lasts %.1f to %.1f ms before the heard ID (20 to 300)'
          % (label, min(ms), max(ms)))
    # clocks: native master clock is one line over the file
    ks = [(ex['master_clkn0'] - 4 * ex['start_slot']) for ex in meta]
    check(len(set(ks)) == 1, '%s: the master\'s native clock is one continuous count on the one grid (CLK - 4 x slot is '
          'the same in every exchange)' % label)
    # the paged device's CLKN and CLKE are drawn per exchange (not continuous, stated in the notes)
    ce = {(ex['paged_clkn0'] - ex['clke0']) for ex in meta}
    check(len(ce) >= max(1, n_ex // 2) and all(ex['paged_clkn0'] % 4 == 0 and ex['clke0'] % 4 == 0 for ex in meta),
          '%s: the paged device\'s CLKN and the master\'s CLKE are drawn per exchange: CLKN - CLKE takes %d values '
          'in %d exchanges' % (label, len(ce), n_ex))
    check(all(ex['draws'] >= 1 for ex in meta), '%s: draws per exchange are in the sidecar (%s)' % (
        label, ', '.join(str(ex['draws']) for ex in meta[:8]) + (' ...' if n_ex > 8 else '')))
    check(all(ex['clkn16_12_constant'] for ex in meta), '%s: CLKN16-12 does not change from the train start to the ack' % label)


def check_laps(side, label):
    b = side['bursts']
    plap, puap = side['paged_lap'], side['paged_uap']
    mlap, muap = side['master_lap'], side['master_uap']
    ok_id = all(e['lap'] == plap and e['uap'] == puap for e in b if e['role'] in ('id_page', 'id_response', 'id_ack'))
    check(ok_id, '%s: every ID (pager, response, ack) carries the paged device\'s LAP 0x%06X' % (label, plap))
    ok_f = all(e['lap'] == plap and e['uap'] == puap and e['fhs']['lap'] == mlap and e['fhs']['uap'] == muap
               for e in b if e['role'] == 'fhs')
    check(ok_f, '%s: the FHS access code and HEC are the paged device\'s (spec: DAC, paged UAP); its payload holds the '
          'master\'s LAP 0x%06X UAP 0x%02X' % (label, mlap, muap))
    ok_c = all(e['lap'] == mlap and e['uap'] == muap for e in b if e['role'] in ('master', 'slave'))
    check(ok_c, '%s: POLL and the connection (both directions) carry the master\'s LAP' % label)
    laps = {plap, mlap}
    d = sum(a != c for a, c in zip(br.sync_word(plap), br.sync_word(mlap)))
    check(d >= 8 and not (laps & set(RESERVED)) and plap != mlap,
          '%s: LAPs not reserved, sync words %d bits apart (>= 8)' % (label, d))


def check_uaps(side, label):
    uaps = {e['uap'] for e in side['bursts']} | {side['uap']}
    for k in ('paged_uap', 'master_uap'):
        if k in side:
            uaps.add(side[k])
    check(not (uaps & AVOID_UAPS), '%s: no truth UAP in {0x00, 0xD6, 0x0B, 0xDD} (UAPs %s)' % (
        label, sorted('0x%02X' % u for u in uaps)))


def parse_fhs_fields(hexstr):
    """The 144 payload bits of an FHS by the spec's Figure 6.9 layout, written out here: each field LSB first."""
    bits = br.bytes_bits(bytes.fromhex(hexstr))
    pos = [0]

    def take(n):
        v = sum(bit << i for i, bit in enumerate(bits[pos[0]:pos[0] + n]))
        raw = bits[pos[0]:pos[0] + n]
        pos[0] += n
        return v, raw

    out = {}
    for name, n in (('parity', 34), ('lap', 24), ('eir', 1), ('rsvd', 1), ('sr', 2), ('sp', 2), ('uap', 8), ('nap', 16),
                    ('cod', 24), ('lt_addr', 3), ('clk27_2', 26), ('pu', 3)):
        out[name], raw = take(n)
        if name == 'parity':
            out['parity_bits'] = raw
    out['crc_bits'] = bits[144:160]
    out['payload_bits'] = bits[:144]
    return out


def check_fhs(side, label):
    fh = [e for e in side['bursts'] if e['role'] == 'fhs']
    gs = groups(side)
    meta = side['exchanges']
    ok = []
    for e in fh:
        f = parse_fhs_fields(e['payload_full_hex'])
        ex = meta[e['exchange']]
        conn = [b for b in gs[e['exchange']] if b['role'] in ('master', 'slave')]
        ok.append(f['lap'] == side['master_lap'] and f['uap'] == side['master_uap'] and
                  f['clk27_2'] == (ex['master_clkn0'] + e['tick']) >> 2 and f['sp'] == 2 and f['pu'] == 0 and
                  f['parity_bits'] == br.sync_word(side['master_lap'])[:34] and
                  f['lt_addr'] == e['fhs']['am_addr'] == side['am_addr'] and f['lt_addr'] != 0 and
                  all(c['lt_addr'] == f['lt_addr'] for c in conn) and e['lt_addr'] == 0 and
                  br.crc16(f['payload_bits'], side['paged_uap']) == f['crc_bits'] and
                  f['nap'] == e['fhs']['nap'] and f['cod'] == e['fhs']['cod'] and
                  e['payload_hex'] == e['payload_full_hex'][:36])
    check(fh and all(ok), '%s: the FHS payload of all %d FHS decodes (Figure 6.9 layout) to the master\'s LAP, UAP, '
          'parity bits, CLK27-2 = its native clock at the FHS, SP = 0b10, and the LT_ADDR the connection uses; the CRC '
          'is under the paged UAP; the header LT_ADDR is 0' % (label, len(fh)))


def check_slave_timing(side, label):
    sl = [e for e in side['bursts'] if e['sender'] == 'slave']
    ma = [e for e in side['bursts'] if e['sender'] == 'master']
    t = np.array([e['timing_offset_us'] for e in sl])
    cf = side['slave_cfo_hz']
    mc = side['master_cfo_hz']
    check(len(sl) >= 3 and np.abs(t).max() <= 3.0 + 1e-9 and (len(t) < 30 or 0.8 < t.std() < 2.2) and
          all(e['timing_offset_us'] == 0 for e in ma),
          '%s: the slave\'s %d bursts have timing_offset_us N(0, 1.5) clipped to +-3 us (std %.2f, max %.2f); the '
          'master\'s are 0' % (label, len(sl), t.std(), np.abs(t).max()))
    check(all(e['cfo_hz'] == cf for e in sl) and all(e['cfo_hz'] == mc for e in ma) and side['cfo_hz'] == mc,
          '%s: cfo_hz is slave_cfo_hz (%+g Hz) on the slave\'s bursts and master_cfo_hz (%+g Hz) on the master\'s' % (
              label, cf, mc))


def check_cfo_samples(iq, side, label, n=4):
    cf = side['slave_cfo_hz']
    mc = side['master_cfo_hz']
    kinds = {}
    for e in rendered(side):
        kinds.setdefault(e['role'], []).append(e)
    rows = []
    for role in ('id_response', 'id_ack', 'slave', 'id_page', 'master', 'fhs'):
        for e in spread(kinds.get(role, []), n):
            rows.append((role, e['sender'], measured_cfo(iq, side, e)))
    rows = [(r[0], r[1], r[2]) for r in rows]
    sl = [r for r in rows if r[1] == 'slave']
    ma = [r for r in rows if r[1] == 'master']
    ok_s = all(abs(r[2] - cf) < 2000 for r in sl)
    ok_m = all(abs(r[2] - mc) < 2000 for r in ma)
    check(len(sl) >= 3 and ok_s, '%s: the slave\'s carrier offset measured from the samples (%d bursts: response, ack, '
          'answers) is slave_cfo_hz %+g Hz within 2 kHz (%s)' % (
              label, len(sl), cf, ', '.join('%.0f' % r[2] for r in sl[:6])))
    check(len(ma) >= 3 and ok_m, '%s: the master\'s (pager IDs, FHS, POLL; %d bursts) is master_cfo_hz %+g Hz within '
          '2 kHz (worst error %.0f Hz)' % (label, len(ma), mc, max(abs(r[2] - mc) for r in ma)))
    for role in ('id_page', 'fhs', 'master', 'id_response', 'id_ack', 'slave'):
        rr = [r[2] for r in rows if r[0] == role]
        if rr:
            want = cf if SENDER[role] == 'slave' else mc
            check(all(abs(x - want) < 2000 for x in rr), '%s: role %s measured carrier offset %s Hz, stated %+g (within '
                  '2 kHz)' % (label, role, ', '.join('%.0f' % x for x in rr[:4]), want))


def check_cfo_plain(iq, side, label, n=4):
    """X and C files: every role's carrier offset measured from the samples is the master's (one device pair)."""
    mc = side['master_cfo_hz']
    out = []
    for role in sorted({e['role'] for e in rendered(side)}):
        for e in spread([x for x in rendered(side) if x['role'] == role], n):
            out.append((role, measured_cfo(iq, side, e)))
    check(side['cfo_hz'] == mc and all(e['cfo_hz'] == mc for e in side['bursts']) and
          all(abs(c - mc) < 2000 for _, c in out),
          '%s: carrier offset measured from the samples of %d bursts (%s) is master_cfo_hz %+g Hz within 2 kHz '
          '(worst error %.0f Hz)' % (label, len(out), ', '.join(sorted({r for r, _ in out})), mc,
                                      max(abs(c - mc) for _, c in out)))


# --- P2 --------------------------------------------------------------------------------------------------------

def check_x(side, label, target, kind):
    b = side['bursts']
    ids = [e for e in b if e['role'] == 'id_page']
    conn = [e for e in b if e['role'] in ('master', 'slave')]
    lap, uap = side['lap'], side['uap']
    check(ids and conn and all(e['lap'] == lap and e['uap'] == uap for e in b) and
          len({e['lap'] for e in b}) == 1,
          '%s: one LAP 0x%06X UAP 0x%02X: the IDs and the connection carry the same LAP (not a spec-true page of '
          'another device)' % (label, lap, uap))
    check(all(e['phase'] == 'page' for e in ids) and all(e['phase'] == 'connection' for e in conn) and
          all(e['sender'] == 'master' for e in ids) and all(e['ours'] for e in b),
          '%s: phase page for IDs, connection otherwise; the pager is the master' % label)
    ok = all(e['channel'] == hs.page(e['clk'], lap, uap, koff(e['clk'])) for e in ids)
    check(ok, '%s: the train\'s %d channels follow the page hop sequence of the LAP, A/B by 1.28 s block of the clock' % (
        label, len(ids)))
    ok = (all(e['clk'] % 4 == 0 for e in ids if e['half'] == 'first') and
          all(e['clk'] % 4 == 1 for e in ids if e['half'] == 'second') and
          all(abs(e['train'] == ('A' if koff(e['clk']) == 24 else 'B')) for e in ids))
    check(ok, '%s: two IDs per TX slot (CLK0 = 0 then 1), train A on even blocks, B on odd' % label)
    used = {e['slot_start_us'] for e in conn}
    overl = [e for e in ids if e['slot_start_us'] in used]
    check(not overl, '%s: IDs sit only in TX slots the connection does not use (%d connection slots, %d IDs in them)' % (
        label, len(used), len(overl)))
    ir = sum(e['rendered'] for e in ids)
    cr = sum(e['rendered'] for e in conn)
    share = 100.0 * ir / (ir + cr)
    check(abs(share - side['id_share_measured']) < 0.01,
          '%s: id_share_measured %.2f %% is rendered IDs / rendered bursts of the LAP from the sidecar (%d / %d)' % (
              label, side['id_share_measured'], ir, ir + cr))
    check(abs(share - target) <= 3.0 and side['id_share_target'] == target,
          '%s: the share %.2f %% is within 3 points of the target %g %%' % (label, share, target))
    fi = min(e['start_sample'] for e in ids if e['rendered'])
    fh = min(e['start_sample'] for e in conn)
    check(fi == side['first_id_sample'] and fh == side['first_header_sample'] and
          min(e['start_sample'] for e in conn) == side['connection_start_sample'],
          '%s: first_id_sample %d, first_header_sample %d, connection_start_sample %d are the sidecar\'s own bursts' % (
              label, fi, fh, side['connection_start_sample']))
    post = [e for e in b if e['rendered'] and e['start_sample'] >= fi]
    ip = sum(e['role'] == 'id_page' for e in post)
    sp = 100.0 * ip / len(post)
    check(abs(sp - side['id_share_measured_post_start']) < 0.01, '%s: the share over the period after the first ID is '
          '%.2f %% (sidecar %.2f)' % (label, sp, side['id_share_measured_post_start']))
    if kind == 'first':
        check(fh < fi, '%s: the connection\'s first header (sample %d) precedes the first ID (sample %d)' % (label, fh, fi))
        n_early = sum(1 for e in conn if e['start_sample'] < fi)
        check(n_early >= 10, '%s: %d connection packets arrive before the first ID' % (label, n_early))
    else:
        cs, ce = side['connection_start_sample'], side['connection_end_sample']
        before = sum(e['start_sample'] < cs for e in ids)
        during = sum(cs <= e['start_sample'] <= ce for e in ids)
        after = sum(e['start_sample'] > ce for e in ids)
        check(before > 0 and during > 0 and after > 0 and fi < fh,
              '%s: IDs before (%d), during (%d) and after (%d) the connection; the train is alone first' % (
                  label, before, during, after))
        check(all(cs <= e['start_sample'] <= ce + 50000 for e in conn), '%s: the connection lies between samples %d '
              'and %d' % (label, cs, ce))


# --- P4 ------------------------------------------------------------------------------------------------------

def check_p4(side, label, ids_only):
    b = side['bursts']
    check(side['sample_rate'] == 20e6 and side['center_mhz'] == 2441.0 and side['samples_per_symbol'] == 20,
          '%s: 20 MS/s on 2441.0 MHz, 20 samples per symbol' % label)
    ids = [e for e in b if e['role'] == 'id_page']
    ph = {(e['half'], e['symbol_phase']) for e in ids}
    check(ph == {('first', 5), ('second', 15)}, '%s: pager IDs at symbol phase 5 (first) and 15 (second) of 20: %s' % (
        label, sorted(ph)))
    check(5 == int(round(50.25 * 20)) % 20 and 15 == int(round((50.25 + 312.5) * 20)) % 20,
          '%s: 50.25 us at 20 MS/s is 1005 samples = phase 5, +312.5 us = 6250 samples = phase 15' % label)
    check(all(e['channel'] != 39 for e in b), '%s: no burst on the centre channel 39' % label)
    check(all(abs(e['channel_mhz'] - 2441.0) <= 8.0 for e in b if e['rendered']),
          '%s: rendered bursts are within 8 MHz of the centre' % label)
    if ids_only:
        first = [e for e in ids if e['clk'] >> 12 == ids[0]['clk'] >> 12]
        chs = {e['channel'] for e in first}
        inw = sorted(c for c in chs if abs(2402 + c - 2441) <= 8)
        check(len(b) == len(ids) and 43 in chs and len(inw) >= 4 and 39 not in {e['channel'] for e in b},
              '%s: IDs only; the first train holds 43 and %d in-window channels %s (39 is the centre channel here: '
              'none used)' % (label, len(inw), inw))
        check(all(e['channel'] == hs.page(e['clk'], side['lap'], side['uap'], koff(e['clk'])) for e in ids),
              '%s: page hop sequence of its LAP' % label)


# --- one file at a time ------------------------------------------------------------------------------------

FAST = dict(snr_db=HIGH)
_PLANS = {}


def plan_full(stem, **kw):
    key = (stem, tuple(sorted(kw.items())))
    if key not in _PLANS:
        t = time.time()
        _PLANS[key] = g.plan_file(stem, **kw)
        _PLANS[key]['secs'] = time.time() - t
    return _PLANS[key]


def make(stem, **kw):
    kw = dict(FAST, **kw)
    t = time.time()
    iq, side = g.synthesise(stem, **kw)
    return iq, side, time.time() - t


def counts_of(side):
    c = {}
    for e in side['bursts']:
        c[e['role']] = c.get(e['role'], 0) + 1
    return c


def core_samples(iq, side, label, n=24):
    check_sidecar(side, label)
    check_positions(iq, side, label, n=n)
    check_decode(iq, side, label, n=12)
    check_ids(iq, side, label, n=8)
    check_channels(side, label)
    check_sizes(iq, side, label)
    check_overlap(side, label)


def p1_all(iq, side, label):
    core_samples(iq, side, label)
    check_p1(side, label)
    check_laps(side, label)
    check_uaps(side, label)
    check_fhs(side, label)
    check_slave_timing(side, label)
    check_cfo_samples(iq, side, label)


def test_p1_samples():
    print('== P1: R_s1 small render (page exchanges), every check on the samples', flush=True)
    iq, side, secs = make('R_s1', duration=1.4)
    print('  (R_s1 1.4 s: %d bursts, %d rendered, %s, %.1f s)' % (len(side['bursts']), len(rendered(side)),
                                                                  counts_of(side), secs), flush=True)
    p1_all(iq, side, 'R_s1')
    check_noise(iq, side, 'R_s1')
    check(len(side['exchanges']) >= 2, 'R_s1: at least two exchanges in 1.4 s (%d)' % len(side['exchanges']))
    del iq
    print('== P1 at 20 MS/s: R_20msps_s1 small render', flush=True)
    iq, side, secs = make('R_20msps_s1', duration=1.4)
    print('  (R_20msps_s1 1.4 s: %d bursts, %d rendered, %s, %.1f s)' % (
        len(side['bursts']), len(rendered(side)), counts_of(side), secs), flush=True)
    p1_all(iq, side, 'R_20msps_s1')
    check_p4(side, 'R_20msps_s1', ids_only=False)
    check(side['sample_rate'] == 20e6 and len(iq) == side['n_samples'], 'R_20msps_s1: n_samples %d' % len(iq))
    del iq


def test_p1_plans():
    print('== P1: the full 12 s plans (no samples): R_s1, R_s2, R_s3', flush=True)
    sides = {}
    for stem in ('R_s1', 'R_s2', 'R_s3'):
        plan = plan_full(stem)
        side = plan['sidecar']
        sides[stem] = side
        c = counts_of(side)
        print('  (%s planned in %.1f s: %d exchanges, counts %s, draws %s)' % (
            stem, plan['secs'], len(side['exchanges']), c, [x['draws'] for x in side['exchanges']][:10]), flush=True)
        check(10 <= len(side['exchanges']) <= 30, '%s: %d exchanges in 12 s' % (stem, len(side['exchanges'])))
        check_sidecar(side, stem)
        check_p1(side, stem)
        check_laps(side, stem)
        check_uaps(side, stem)
        check_fhs(side, stem)
        check_slave_timing(side, stem)
        check_channels(side, stem)
        check_overlap(side, stem)
        n = len(side['bursts'])
        check(c.get('id_page', 0) > 0.5 * n, '%s: the pager\'s IDs are most of the bursts (%d of %d)' % (
            stem, c.get('id_page', 0), n))
        check(side['n_samples'] == int(round((0.02 + 12.0 + 0.002) * 40e6)) and side['crossings'] == [],
              '%s: n_samples %d, no crossing' % (stem, side['n_samples']))
        check(side['seed'] == 9500 + int(stem[-1]), '%s: seed %d' % (stem, side['seed']))
    pairs = {(s['paged_lap'], s['paged_uap'], s['master_lap'], s['master_uap']) for s in sides.values()}
    check(len(pairs) == 3, 'the three files have three different LAP/UAP pairs')
    for s in sides.values():
        n_per = {(e['paged_lap'], e['paged_uap'], e['master_lap'], e['master_uap']) for e in s['exchanges']}
        check(len(n_per) == 1, '%s: the same paged and master pair in every exchange' % s['stem'])
    print('== P4: R_20msps_s1 full plan, C_20msps full plan', flush=True)
    plan = plan_full('R_20msps_s1')
    side = plan['sidecar']
    print('  (R_20msps_s1 planned in %.1f s: %d exchanges, counts %s)' % (plan['secs'], len(side['exchanges']),
                                                                         counts_of(side)), flush=True)
    check_sidecar(side, 'R_20msps_s1', off0=50.25)
    check_p1(side, 'R_20msps_s1')
    check_laps(side, 'R_20msps_s1')
    check_uaps(side, 'R_20msps_s1')
    check_fhs(side, 'R_20msps_s1')
    check_p4(side, 'R_20msps_s1', ids_only=False)
    check_overlap(side, 'R_20msps_s1')
    check(side['n_samples'] == int(round((0.02 + 10.0 + 0.002) * 20e6)), 'R_20msps_s1: 10 s at 20 MS/s')
    plan = plan_full('C_20msps')
    side = plan['sidecar']
    print('  (C_20msps planned in %.1f s: %d IDs)' % (plan['secs'], len(side['bursts'])), flush=True)
    check_sidecar(side, 'C_20msps')
    check_p4(side, 'C_20msps', ids_only=True)
    check_uaps(side, 'C_20msps')
    check_overlap(side, 'C_20msps')
    check(side['lap'] not in RESERVED and side['seed'] == 9531, 'C_20msps: LAP 0x%06X not reserved, seed %d' % (
        side['lap'], side['seed']))
    check(side['bursts'][0]['symbol_phase'] in (5, 15) and
          {e['half'] for e in side['bursts']} == {'first', 'second'}, 'C_20msps: IDs, first and second')


def test_p4_samples():
    print('== P4: C_20msps small render', flush=True)
    iq, side, secs = make('C_20msps', duration=0.25)
    print('  (C_20msps 0.25 s: %d IDs, %d rendered, %.1f s)' % (len(side['bursts']), len(rendered(side)), secs), flush=True)
    check_sidecar(side, 'C_20msps')
    check_positions(iq, side, 'C_20msps', n=24)
    check_ids(iq, side, 'C_20msps', n=10)
    check_channels(side, 'C_20msps')
    check_sizes(iq, side, 'C_20msps')
    check_p4(side, 'C_20msps', ids_only=True)
    check_cfo_plain(iq, side, 'C_20msps')
    check_noise(iq, side, 'C_20msps')
    del iq


X_STEMS = {'X_first_id10_s1': (10, 'first'), 'X_first_id10_s2': (10, 'first'), 'X_first_id50_s1': (50, 'first'),
           'X_first_id50_s2': (50, 'first'), 'X_first_id87_s1': (87, 'first'), 'X_first_id87_s2': (87, 'first'),
           'X_bda_id10_s1': (10, 'bda'), 'X_bda_id50_s1': (50, 'bda'), 'X_bda_id87_s1': (87, 'bda')}


def test_p2_plans():
    print('== P2: the X files, plans (all nine at 6 s, two at the full 12 s)', flush=True)
    laps = set()
    for stem, (target, kind) in X_STEMS.items():
        plan = plan_full(stem, duration=6.0)
        side = plan['sidecar']
        print('  (%s 6 s planned in %.1f s: counts %s, share %.2f %%, post-start %.2f %%)' % (
            stem, plan['secs'], counts_of(side), side['id_share_measured'], side['id_share_measured_post_start']), flush=True)
        check_sidecar(side, stem)
        check_x(side, stem, target, kind)
        check_channels(side, stem)
        check_uaps(side, stem)
        check_overlap(side, stem)
        laps.add((side['lap'], side['uap']))
        ch = sorted({e['channel'] for e in side['bursts'] if e['role'] in ('master', 'slave')})
        check(len(ch) >= 4 and 39 in ch and 43 in ch, '%s: connection on %d fixed in-window channels %s, 39 and 43 in' % (
            stem, len(ch), ch))
        check(side['lap'] not in RESERVED, '%s: LAP 0x%06X is not reserved' % (stem, side['lap']))
    check(len(laps) == len(X_STEMS), 'every X file has its own LAP/UAP (%d of %d)' % (len(laps), len(X_STEMS)))
    for stem in ('X_first_id50_s1', 'X_bda_id87_s1'):
        plan = plan_full(stem)
        side = plan['sidecar']
        target, kind = X_STEMS[stem]
        print('  (%s full planned in %.1f s: counts %s, share %.2f %%)' % (stem, plan['secs'], counts_of(side),
                                                                       side['id_share_measured']), flush=True)
        check_x(side, stem, target, kind)
        check_overlap(side, stem)
        check(side['n_samples'] == int(round((0.02 + 12.0 + 0.002) * 40e6)), '%s: 12 s' % stem)
    sd = plan_full('X_first_id50_s1')['sidecar']
    check(abs(sd['first_id_sample'] / 40e6 - (0.02 + 4.0)) < 0.05 and sd['first_header_sample'] / 40e6 < 0.2,
          'X_first_id50_s1: the connection runs from the start, the train from 4 s (first ID at %.3f s)' % (
              sd['first_id_sample'] / 40e6))
    sb = plan_full('X_bda_id87_s1')['sidecar']
    check(abs(sb['connection_start_sample'] / 40e6 - 3.02) < 0.2 and abs(sb['connection_end_sample'] / 40e6 - 9.02) < 0.2,
          'X_bda_id87_s1: the connection runs 3 s to 9 s (%.3f to %.3f s)' % (
              sb['connection_start_sample'] / 40e6, sb['connection_end_sample'] / 40e6))


def test_p2_samples():
    print('== P2: X_first_id50_s1 and X_bda_id87_s1 small renders (samples)', flush=True)
    for stem, kw in (('X_first_id50_s1', dict(duration=0.5)), ('X_bda_id87_s1', dict(duration=0.5))):
        iq, side, secs = make(stem, **kw)
        print('  (%s: %d bursts, %d rendered, %s, %.1f s)' % (stem, len(side['bursts']), len(rendered(side)),
                                                              counts_of(side), secs), flush=True)
        check_sidecar(side, stem)
        check_positions(iq, side, stem, n=24)
        check_decode(iq, side, stem, n=10)
        check_ids(iq, side, stem, n=8)
        check_channels(side, stem)
        check_sizes(iq, side, stem)
        check_overlap(side, stem)
        target, kind = X_STEMS[stem]
        check_x(side, stem, target, kind)
        check_cfo_plain(iq, side, stem)
        fr = [e for e in rendered(side) if e['role'] == 'id_page']
        check(len({e['symbol_phase'] for e in fr if e['half'] == 'first'}) == 1 and
              {e['symbol_phase'] for e in fr if e['half'] == 'first'} == {10} and
              {e['symbol_phase'] for e in fr if e['half'] == 'second'} == {30},
              '%s: first IDs at symbol phase 10, second IDs at 30 (50.25 us at 40 MS/s)' % stem)
        del iq


def sl_keys(side):
    return [(e['role'], e['ptype'], e['channel'], e['clk'], e['lap'], e['uap'], e['rendered']) for e in side['bursts']]


def check_sl_cell(side, label, lap_name, cfo, phase, noise, share, other=None):
    """One same-LAP cell: LAP, CFO, phase of every rendered burst, noise seed, the share, the cell block; and, against
    ``other`` (a sidecar of the same LAP), the same schedule moved by exactly the phase difference."""
    cell = side['cell']
    check(side['lap'] == int(lap_name, 16) and cell['lap_name'] == lap_name, '%s: LAP 0x%s' % (label, lap_name))
    check(all(e['cfo_hz'] == cfo for e in side['bursts']) and side['master_cfo_hz'] == cfo and cell['cfo_hz'] == cfo,
          '%s: every burst has cfo_hz %g (one device pair)' % (label, cfo))
    ph = lambda e: (e['start_sample'] + e['timing_frac']) % 40
    rend = rendered(side)
    pk = [e for e in rend if e['role'] in ('master', 'slave')]
    ids = [e for e in rend if e['role'] == 'id_page']
    check(pk and all(ph(e) == phase for e in pk) and all(e['timing_offset_us'] == 0.0 for e in side['bursts']),
          '%s: %d connection bursts, all at (start_sample + timing_frac) %% 40 = %d, no timing error' % (label, len(pk), phase))
    check(ids and all(ph(e) == (phase + (20 if e['half'] == 'second' else 0)) % 40 for e in ids),
          '%s: %d IDs at the phase (first) or the phase + 20 (second)' % (label, len(ids)))
    check(cell['first_id_phase_samples'] == phase and abs(cell['offset_us'] - (50 + phase / 40.0)) < 1e-12 and
          side['grid_offset_us_param'] == cell['offset_us'], '%s: the cell block and the offset (50 + %d / 40 us)' % (label, phase))
    check(side['noise_seed'] == noise == cell['noise_seed'] and side['seed'] == cell['schedule_seed'],
          '%s: noise seed %d, schedule seed %d' % (label, noise, side['seed']))
    check(abs(side['id_share_measured'] - share) < 0.5 and cell['id_share_target'] == share,
          '%s: ID share %.2f %% (target %d)' % (label, side['id_share_measured'], share))
    if other is not None:
        d = {e['start_sample'] - o['start_sample'] for e, o in zip(side['bursts'], other['bursts'])}
        dp = cell['first_id_phase_samples'] - other['cell']['first_id_phase_samples']
        check(sl_keys(side) == sl_keys(other) and len(side['bursts']) == len(other['bursts']) and d == {dp},
              '%s: the same schedule as %s, every burst moved by %d samples' % (label, other['stem'], dp))
        check([e['carrier_phase_rad'] for e in side['bursts']] == [e['carrier_phase_rad'] for e in other['bursts']]
              if other['noise_seed'] == side['noise_seed'] else
              [e['carrier_phase_rad'] for e in side['bursts']] != [e['carrier_phase_rad'] for e in other['bursts']],
              '%s: carrier phases follow the noise seed' % label)


def test_samelap():
    print('== P5: the same-LAP set (29 files): cells, schedules, phase, CFO, noise seed, the share', flush=True)
    stems = g.SETS['SL']
    check(len(stems) == 29 and len(set(stems)) == 29 and all(s in g.SPECS for s in stems),
          'SL: 29 distinct stems (%d)' % len(stems))
    cells = [g.SPECS[s]['cell'] for s in stems]
    core = [c for c in cells if c['lap_name'] in ('0466AB', '662492')]
    n = lambda **kv: sum(all(c[k] == v for k, v in kv.items()) for c in cells)
    check(n(first_id_phase_samples=0) == 8 and n(first_id_phase_samples=10) == 9 and n(first_id_phase_samples=20) == 4 and
          n(first_id_phase_samples=30) == 4 and sum(c['first_id_phase_samples'] in (5, 15, 25, 35) for c in cells) == 4 and
          n(noise_seed=2) == 8 and n(lap_name='662492') == 12 and len(core) == 29,
          'SL: phases 0 x8, 10 x9 (8 + the low-density cell), 20 x4, 30 x4, sweep x4; noise seed 2 x8; 12 files on 662492')
    base = {}
    for lap, ref in (('0466AB', 'X_bda_id87_s1'), ('662492', 'X_bda_id50_s1')):
        r = plan_full(ref)['sidecar']
        base[lap] = r
        c = plan_full(g.sl_stem(lap, -30e3, 10, 1))['sidecar']
        check(sl_keys(c) == sl_keys(r) and [e['start_sample'] for e in c['bursts']] == [e['start_sample'] for e in r['bursts']] and
              c['x_plan']['conn_clk_start'] == r['x_plan']['conn_clk_start'] and c['uap'] == r['uap'] and c['id_share_measured'] == r['id_share_measured'],
              'SL %s: the (-30 kHz, phase 10) cell has the schedule of %s, burst for burst' % (lap, ref))
    for lap, share in (('0466AB', 87), ('662492', 50)):
        first = None
        for cfo in (0.0, -30e3):
            for ph, nz in ((0, 1), (0, 2), (10, 1), (10, 2), (20, 1), (30, 1)):
                st = g.sl_stem(lap, cfo, ph, nz)
                side = plan_full(st)['sidecar']
                check_sl_cell(side, st, lap, cfo, ph, nz, share, other=first)
                first = first or side
    for ph in (5, 15, 25, 35):
        st = g.sl_stem('0466AB', -30e3, ph, 1)
        check_sl_cell(plan_full(st)['sidecar'], st, '0466AB', -30e3, ph, 1, 87, other=plan_full(g.sl_stem('0466AB', -30e3, 10, 1))['sidecar'])
    low = plan_full(g.sl_stem('0466AB', -30e3, 10, 1, 10))['sidecar']
    check_sl_cell(low, 'SL low-density', '0466AB', -30e3, 10, 1, 10)
    check(low['uap'] == base['0466AB']['uap'] and low['seed'] == 9523 and low['x_plan']['clke_at_slot16'] == base['0466AB']['x_plan']['clke_at_slot16'],
          'SL low-density: the LAP, UAP and clock of the primary schedule (%.2f %% IDs)' % low['id_share_measured'])
    # the checker fails on a wrong cell
    with quiet() as failed:
        check_sl_cell(plan_full(g.sl_stem('0466AB', -30e3, 0, 1))['sidecar'], 'bad', '0466AB', -30e3, 10, 1, 87)
    check(failed, 'SL: the cell check fails on a phase-0 file claimed as phase 10 (%d checks failed)' % len(failed))
    # the manifest and the checksums of a set written to a folder (two short files)
    import hashlib
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        two = [g.sl_stem('0466AB', -30e3, 10, 1), g.sl_stem('662492', 0.0, 20, 1)]
        with quiet():
            for st_ in two:
                g.write_file(st_, td, duration=0.2)
        g.write_manifest(td, two, ['cmd'])
        man = json.load(open(os.path.join(td, 'manifest.json')))
        sums = dict(reversed(l.split('  ', 1)) for l in open(os.path.join(td, 'SHA256SUMS.txt')).read().split('\n') if l)
        rows = {r['stem']: r for r in man['files']}
        ok = set(rows) == set(two) and len(sums) == 4
        for st_, r in rows.items():
            f = os.path.join(td, r['file'])
            ok = ok and sums[r['file']] == hashlib.sha256(open(f, 'rb').read()).hexdigest() == r['sha256_cf32'] and \
                sums[r['sidecar']] == r['sha256_json'] and r['bytes'] == 8 * r['n_samples'] and r['snr_db'] == 20.0
        check(ok and rows[two[0]]['lap'] == '0x%06X' % json.load(open(os.path.join(td, rows[two[0]]['sidecar'])))['lap'] and rows[two[0]]['cfo_hz'] == -30e3 and
              rows[two[0]]['first_id_phase_samples'] == 10 and rows[two[0]]['schedule_seed'] == 9523 and
              rows[two[1]]['lap_name'] == '662492' and rows[two[1]]['offset_us'] == 50.5 and rows[two[1]]['noise_seed'] == 1 and
              rows[two[1]]['id_share_target'] == 50, 'SL: manifest.json and SHA256SUMS.txt (LAP, CFO, offset, seeds, share, sha256)')
    # samples: the noise follows the noise seed, the signal follows the cell
    print('  small renders (0.5 s)', flush=True)
    a, sa, _ = make(g.sl_stem('0466AB', -30e3, 0, 1), duration=0.5)
    b, sb_, _ = make(g.sl_stem('0466AB', -30e3, 0, 2), duration=0.5)
    c, sc, _ = make(g.sl_stem('0466AB', 0.0, 20, 1), duration=0.5)
    pre = int(0.015 * 40e6)
    check(np.array_equal(a[:pre], c[:pre]) and not np.array_equal(a[:pre], b[:pre]),
          'SL samples: the first 15 ms (noise only) are the same for noise seed 1 in two cells and differ for seed 2')
    for st_, iq, side, cfo, ph, nz in ((g.sl_stem('0466AB', -30e3, 0, 1), a, sa, -30e3, 0, 1),
                                       (g.sl_stem('0466AB', 0.0, 20, 1), c, sc, 0.0, 20, 1)):
        check_sidecar(side, st_, off0=50 + ph / 40.0)
        check_positions(iq, side, st_, n=24)
        check_decode(iq, side, st_, n=10)
        check_ids(iq, side, st_, n=8)
        check_x(side, st_, 87, 'bda')
        check_cfo_plain(iq, side, st_)
    del a, b, c


def test_regen():
    print('== regeneration and the noise', flush=True)
    for stem, kw in (('R_s1', dict(duration=0.5)), ('X_first_id50_s1', dict(duration=0.2))):
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
        worst = 0.0
        for index, lo in enumerate(range(0, len(iq), g.NOISE_BLOCK)):
            hi = min(lo + g.NOISE_BLOCK, len(iq))
            n = noise_block(a['sidecar']['noise_seed'], index, hi - lo, a['sidecar']['sample_rate'])
            worst = max(worst, float(np.abs(iq[lo:hi] - n - quiet_iq[lo:hi]).max()))
        check(worst < 1e-5, '%s: the file less the noise regenerated from the seed is the bursts alone (worst %.1e)' % (
            stem, worst))
        del iq, quiet_iq


# --- mutants ----------------------------------------------------------------------------------------------------

def mutant(name, patches, stem, kw, runner, expect_any, label):
    """Run the generator with ``patches`` and ``runner`` (checks on what it makes); the unpatched file must pass
    the same checks, and the mutant must trip at least one of ``expect_any`` (substrings of check texts)."""
    key = (stem, tuple(sorted(kw.items())), runner.__name__)
    if key not in _BASE:
        iq, side, _ = make(stem, **kw)
        with quiet() as failed:
            runner(iq, side, 'base')
        _BASE[key] = failed
        check(not failed, 'unmutated %s %s passes the mutant checks (%d fail %s)' % (stem, kw, len(failed), failed[:2]))
        del iq
    with contextlib.ExitStack() as st:
        for attr, repl in patches.items():
            st.enter_context(mock.patch.object(g, attr, repl))
        try:
            iq, side, _ = make(stem, **kw)
        except Exception as ex:                      # a generator that refuses a mutant has caught it too
            check(True, 'mutant %s is caught by the generator itself (%s: %s)' % (label, type(ex).__name__, str(ex)[:60]))
            return
    with quiet() as failed:
        runner(iq, side, name)
    hit = [w for w in failed if any(sub in w for sub in expect_any)]
    check(bool(hit), 'mutant %s is caught by "%s" (%d checks fail in all)' % (
        label, hit[0].split(': ', 1)[-1][:90] if hit else '-', len(failed)))
    del iq


_BASE = {}


def run_p1_mut(iq, side, label):
    check_sidecar(side, label)
    check_p1(side, label)
    check_laps(side, label)
    check_uaps(side, label)
    check_slave_timing(side, label)
    check_cfo_samples(iq, side, label, n=2)
    check_positions(iq, side, label, n=12)


def run_x_plan(iq, side, label, target=50, kind='first'):
    check_sidecar(side, label)
    check_x(side, label, target, kind)
    check_overlap(side, label)


def run_x_first(iq, side, label):
    run_x_plan(iq, side, label, 50, 'first')


def run_p4_mut(iq, side, label):
    check_sidecar(side, label)
    check_p4(side, label, ids_only=False)
    check_positions(iq, side, label, n=10)
    check_ids(iq, side, label, n=4)


def test_mutants():
    print('== mutants', flush=True)
    kp = dict(duration=1.0)
    mutant('p1', {'response_tick': lambda t: t + 4}, 'R_s1', kp, run_p1_mut,
           ['the ID response is the heard ID + 625'], 'ID response one slot late (heard + 1250 us)')
    mutant('p2', {'sample_cfo_hz': lambda c, s='master': 0.0 if s == 'slave' else c}, 'R_s1', kp, run_p1_mut,
           ['carrier offset measured from the samples', 'fitted from the samples'],
           'slave carrier offset ignored in the samples')
    mutant('p2b', {'sample_cfo_hz': lambda c, s='master': 0.0 if s == 'master' else c}, 'R_s1', kp, run_p1_mut,
           ['carrier offset measured from the samples', 'fitted from the samples', 'is master_cfo_hz'],
           'master carrier offset ignored in the samples')
    mutant('p3', {'ack_lap': lambda paged, master: master}, 'R_s1', kp, run_p1_mut,
           ['every ID (pager, response, ack) carries the paged'], 'ack on the master\'s LAP instead of the paged')
    mutant('p4', {'role_label': lambda r: {'id_response': 'id_ack', 'id_ack': 'id_response'}.get(r, r)}, 'R_s1', kp,
           run_p1_mut, ['ID response is the heard ID', 'every page, response', 'each exchange has one response',
                        'role is one of', 'every ID (pager'], 'roles of response and ack swapped')
    mutant('p5', {'sender_label': lambda s: {'master': 'slave', 'slave': 'master'}[s]}, 'R_s1', kp, run_p1_mut,
           ['sender master/slave as the role says', 'carrier offset', 'cfo_hz is slave_cfo_hz'], 'sender swapped')
    mutant('p6', {'draw_uap': lambda rng: 0x00}, 'R_s1', kp, run_p1_mut, ['no truth UAP in'], 'a UAP of 0x00')
    kx = dict(duration=3.0)
    mutant('x1', {'connection_density': lambda n: 2 * n}, 'X_first_id50_s1', kx, run_x_first,
           ['within 3 points of the target'], 'connection density x2: the share target missed')
    mutant('x2', {'train_slot_ok': lambda k, used: True}, 'X_first_id50_s1', kx, run_x_first,
           ['IDs sit only in TX slots the connection does not use', 'no two bursts overlap'],
           'IDs not confined to the slots the connection leaves free')

    def swapped(n_slots, split):
        return (0, n_slots, split, n_slots)
    mutant('x3', {'x_first_intervals': swapped}, 'X_first_id50_s1', kx, run_x_first,
           ['precedes the first ID', 'arrive before the first ID'], 'first ID before the first header in X_first')
    kq = dict(duration=1.0)
    mutant('q1', {'render_fs': lambda fs: 40e6}, 'R_20msps_s1', kq, run_p4_mut,
           ['fitted from the samples', 'falling edge of the envelope', 'n_samples'],
           'the 20 MS/s file rendered at the 40 MS/s sample rate')


# --- a written file -----------------------------------------------------------------------------------------------

def verify(cf32, json_path, n_pos=60, n_cfo=6, n_ids=60, n_dec=30):
    """The checks of a written file, from its samples (a memory map: nothing is held whole), as statistics: at the
    files' own 20 dB a start is good to a fraction of a sample, not to 0.1 of one per burst.

        python scripts/test_bt_synth_slotb2.py --verify synth_slotb2_R_s1.cf32 synth_slotb2_R_s1.json
    """
    side = json.load(open(json_path))
    iq = np.memmap(cf32, dtype='<c8', mode='r')
    fs = side['sample_rate']
    ups = fs / 1e6
    label = 'file'
    check(len(iq) == side['n_samples'] and os.path.getsize(cf32) == 8 * side['n_samples'] and
          side['n_bursts'] == len(side['bursts']),
          'file: %d samples, %d bytes, %d bursts (%d rendered): the sidecar\'s n_samples and n_bursts' % (
              len(iq), os.path.getsize(cf32), len(side['bursts']), len(rendered(side))))
    check_sidecar(side, label, off0=side.get('grid_offset_us_param', 50.25), drift=side['drift_ppm_signed'])
    check_channels(side, label)
    n_ov = check_overlap(side, label)
    print('  crossing none: %d crossings, %d overlaps of any two bursts' % (len(side['crossings']), n_ov))
    if 'exchanges' in side:
        check_p1(side, label)
        check_laps(side, label)
        check_fhs(side, label)
        check_slave_timing(side, label)
    check_uaps(side, label)
    if side['kind'] == 'X':
        check_x(side, label, side['id_share_target'], 'first' if side['stem'].startswith('X_first') else 'bda')
    if side['kind'] == 'C' or side['sample_rate'] == 20e6:
        check_p4(side, label, ids_only=side['kind'] == 'C')
    # noise: lead-in and the gaps between bursts early, in the middle, late and at the block seams
    rb = rendered(side)
    spans = sorted((int(e['start_sample']) - 100, int(e['start_sample'] + (e['air_bits_length'] + 6) * ups) + 100)
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
    third = max(1, len(gaps) // 3)
    parts = dict(lead_in=[(0, int(0.015 * fs))], early=gaps[:third], middle=gaps[third:2 * third], late=gaps[-third:])
    levels = {k: level(v) for k, v in parts.items() if v}
    check(all(abs(v[0]) < 0.1 for v in levels.values()), 'file: the noise floor against the common one: %s dB' % ', '.join(
        '%s %+.3f (%d samples)' % (k, v[0], v[1]) for k, v in levels.items()))
    # starts, as statistics
    picks = spread(rb, n_pos)
    errs = np.array([fit(iq, side, e)['start'] - (e['start_sample'] + e['timing_frac']) for e in picks])
    print('  start error over %d bursts: median |.| %.3f, 95th percentile %.3f, max %.3f sample, bias %+.3f' % (
        len(picks), np.median(np.abs(errs)), np.percentile(np.abs(errs), 95), np.abs(errs).max(), errs.mean()))
    check(np.median(np.abs(errs)) < 0.5 and np.abs(errs).max() < 2.0 and abs(errs.mean()) < 0.2,
          'file: start_sample + timing_frac at %g dB: median %.3f sample, max %.3f, bias %+.3f' % (
              side['snr_db'], np.median(np.abs(errs)), np.abs(errs).max(), errs.mean()))
    # decode a sample of packets
    non = spread([e for e in rb if e['ptype'] != 'ID'], n_dec)
    ok = 0
    for e in non:
        f = fit(iq, side, e)
        got = demod(iq, side, e, f['start'], len(model_bits(e)))
        if _lib is None:
            break
        ts.btbb.uap = e['uap']
        taken, hdr, verdict, data = btbb(got, decode_clk(e))
        ok += int(taken and hdr == br.bits_int([int(c) for c in e['header_bits_18']]))
    if non and _lib is not None:
        check(ok >= 0.9 * len(non), 'file: libbtbb decodes the header of %d of %d packets (FHS included) from the '
              'samples at %g dB (UAP and whitening clock of the sidecar)' % (ok, len(non), side['snr_db']))
    # IDs: no tail, id_end edge, carrier
    ids = spread([e for e in rb if e['ptype'] == 'ID'], n_ids)
    if ids:
        taps = ts.taps_for(fs)
        drops, edges, deg = [], [], []
        for e in ids:
            f = fit(iq, side, e)
            s0 = f['start']
            lo, hi = int(s0) - 100, int(s0 + 200 * ups)
            z = fftconvolve(shifted(iq, lo, hi, e['channel_mhz'], side['center_mhz'], fs, float(e['cfo_hz'])), taps, 'same')
            t = (np.arange(lo, hi) - s0) / ups
            p = np.abs(z) ** 2
            drops.append(10 * np.log10(p[(t >= 10) & (t <= 60)].mean() / p[(t >= 72) & (t <= 108)].mean()))
            edges.append(fit_id_end(iq, side, e) - e['id_end_sample'])
        print('  %d IDs: carrier drop %.1f dB median (min %.1f), fitted ramp start - id_end_sample: median %+.2f, '
              '95th percentile |.| %.2f sample' % (len(ids), np.median(drops), min(drops), np.median(edges),
                                                    np.percentile(np.abs(edges), 95)))
        check(np.median(drops) >= 15.0 and min(drops) >= 10.0, 'file: no tail: the carrier power drops by a median %.1f '
              'dB (min %.1f) within 4 us of the ID\'s end' % (np.median(drops), min(drops)))
        check(abs(np.median(edges)) <= 0.1 * ups and np.percentile(np.abs(edges), 95) < 0.5 * ups,
              'file: the envelope\'s falling edge (2 us ramp) starts at id_end_sample: median %+.2f sample, 95th '
              'percentile |.| %.2f (median <= 0.1 us, 95th < 0.5 us; raw samples at 20 dB, 4 dB per sample)' % (np.median(edges), np.percentile(np.abs(edges), 95)))
    if side.get('slave_cfo_hz') is not None and 'master_cfo_hz' in side:
        rows = []
        for role in ('id_response', 'id_ack', 'slave', 'id_page', 'master'):
            for e in spread([x for x in rb if x['role'] == role], n_cfo):
                rows.append((e['sender'], measured_cfo(iq, side, e)))
        sl = np.array([r[1] for r in rows if r[0] == 'slave'])
        ma = np.array([r[1] for r in rows if r[0] == 'master'])
        print('  carrier offset from the samples: slave median %.0f Hz (%d bursts, spread %.0f), master median %.0f Hz '
              '(%d bursts)' % (np.median(sl), len(sl), sl.std(), np.median(ma), len(ma)))
        check(abs(np.median(sl) - side['slave_cfo_hz']) < 2000 and abs(np.median(ma) - side['master_cfo_hz']) < 2000,
              'file: slave carrier offset %.0f Hz (stated %+g), master %.0f Hz (stated %+g), medians over the bursts '
              'at %g dB' % (np.median(sl), side['slave_cfo_hz'], np.median(ma), side['master_cfo_hz'], side['snr_db']))
    failed = [w for ok_, w in RESULTS if not ok_]
    print('RESULT: PASS' if not failed else 'RESULT: FAIL (%d)' % len(failed))
    return 1 if failed else 0


def main():
    t0 = time.time()
    steps = []
    for name, fn in (('p1_samples', test_p1_samples), ('p1_plans', test_p1_plans), ('p4', test_p4_samples),
                     ('p2_plans', test_p2_plans), ('p2_samples', test_p2_samples), ('samelap', test_samelap), ('regen', test_regen),
                     ('mutants', test_mutants)):
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
