#!/usr/bin/env python3
"""Hold the multi-device mixed-file writer to what its samples and the specification say.

    python scripts/test_bt_synth_mixed.py
    python scripts/test_bt_synth_mixed.py --blind FILE.cf32 FILE.json [--seconds 0.3]   # the blind check on a written file

``scripts/bt_synth_mixed.py`` writes 8 piconets (6 ours, 2 not) and 3 page-exchange joiners on the
air at once, with the truth of every collision, for bluey-ox-walker's blind operation. This checks
the sidecar against the *samples* from small runs (3 noise blocks, 0.31 s), and against the rules
written out again here, never against the generator's own bookkeeping:

* **the superposition**: the file less the noise (regenerated here from the seed) equals the sum of
  every burst rendered alone from the sidecar's ``air_bits``, ``snr_db``, ``burst_phase``,
  ``channel`` and ``start_sample + timing_frac`` with the repository's modulator, over the whole
  file and on the regions where bursts overlap on one channel, at the seams of 2**17-sample blocks
  as well: a collision is a linear sum;
* **the overlap truth** recomputed by an independent sweep (a sort and a bisect on the starts) from
  ``start_sample + timing_frac`` and ``air_bits_length`` symbols of 40 samples: ``overlaps`` (the
  other burst, its device, the length of the overlap, the channel offset, the SIR), ``collision`` and
  ``overlap_frac`` of EVERY burst; the counts and the fraction of collided bursts;
* **the samples alone**, at 30 dB and in a stretch with no other burst on the same or an adjacent
  channel: each burst's carrier (spectrum), start (a model of its bits fitted to a tenth of a sample),
  level, carrier phase and bits, and libbtbb decoding the headers and payloads at the device's UAP and
  clock; at the files' own levels (8 to 22 dB) the level of every device from collision-free bursts;
* **the slot rules**: no two bursts of one device overlap; a slave answer is in the slot after its master's
  packet (after its last slot, for 3 and 5 slots), exactly that many slots after it, on the master's
  channel by the kernel; the slot grids are not aligned (each device's offset, found from its bursts, is
  800 samples from every other's); master clocks match ``clk0 + 2 * slot``;
* **the hop sequences** recomputed from each device's address and clock with ``apps/bt_hop.py`` (the
  adapted map 31-50 for the piconets), and the joiners' page exchanges from ``apps/bt_hop_substates.py``
  and the basic kernel;
* **the identities**: 14 distinct LAPs, the sync-word distance stated and true, 6 ours and 2 not, each
  burst's ``ours`` its device's, counts per device and type, ``n_samples`` against the array and the size
  of the written file;
* **the blind check**: from the samples alone, without the sidecar, the access-code chain of
  ``bt_ota_check`` (FIR 0.7 MHz, a discriminator, the 72-bit template, normalised correlation above 0.5)
  on every channel 27 to 51 for the eight LAPs of the table and for LAPs not in it: each device's
  collision-free bursts are found, a LAP not in the table is never found;
* mutants of the generator, each caught.
"""
import contextlib
import ctypes
import importlib.util
import json
import os
import sys
import tempfile
import time
from bisect import bisect_right
from unittest import mock

os.environ.setdefault('OMP_NUM_THREADS', '4')
import numpy as np  # noqa: E402
from scipy.signal import fftconvolve, firwin, lfilter, upfirdn  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from apps import bt_hop, bt_hop_substates as hs  # noqa: E402
from scripts import bt_ota_check, bt_synth, bt_synth_mixed as g  # noqa: E402

FS = 40e6
SPS = 40
CENTER = 2441.0
SLOT = 25000
NOISE = bt_synth.AMPLITUDE ** 2 / 100
NOISE_POWER = NOISE * FS / 1e6
MASK = sum(1 << c for c in range(31, 51))
SEED = 9201
BLOCK = 2 ** 17
SRC = open(g.__file__).read()
RESULTS = []
QUIET = [False]
GD = 200
TAPS = firwin(401, 0.7e6, fs=FS)
HIGH = (28.0, 32.0)
_lib = bt_ota_check.libbtbb()


def check(ok, what):
    ok = bool(ok)
    RESULTS.append((ok, what))
    if not QUIET[0]:
        print('  %s %s' % ('ok  ' if ok else 'FAIL', what), flush=True)
    return ok


@contextlib.contextmanager
def quiet():
    """Collect the checks of a block without printing, yielding the list of those that failed."""
    QUIET[0] = True
    mark = len(RESULTS)
    failed = []
    try:
        yield failed
    finally:
        QUIET[0] = False
        failed.extend(w for ok, w in RESULTS[mark:] if not ok)
        del RESULTS[mark:]


# --- the specification's definitions, written again --------------------------------------------------

def window(e):
    """A burst's air time as a real interval: its first preamble sample plus the timing fraction, and the symbols
    at 40 samples."""
    s = e['start_sample'] + e['timing_frac']
    return s, s + e['air_bits_length'] * SPS


def independent_overlaps(bursts):
    """Overlaps of every burst by sorting the starts and a bisect for the candidates: ``{i: {j: overlap}}`` of bursts that
    overlap in time (by any amount) and are within 1 channel, plus the same-channel union fractions. A different
    algorithm from the generator's sweep over active windows."""
    n = len(bursts)
    win = [window(e) for e in bursts]
    order = sorted(range(n), key=lambda i: win[i][0])
    starts = [win[i][0] for i in order]
    longest = max(w[1] - w[0] for w in win)
    over = {i: {} for i in range(n)}
    for i in range(n):
        s, e_ = win[i]
        lo = bisect_right(starts, s - longest - 1)
        hi = bisect_right(starts, e_)
        for k in range(lo, hi):
            j = order[k]
            if j == i:
                continue
            ov = min(e_, win[j][1]) - max(s, win[j][0])
            if ov > 0 and abs(bursts[i]['channel'] - bursts[j]['channel']) <= 1:
                over[i][j] = ov
    frac = {}
    for i in range(n):
        s, e_ = win[i]
        pts = sorted((max(s, win[j][0]), min(e_, win[j][1])) for j in over[i]
                     if bursts[j]['channel'] == bursts[i]['channel'])
        covered, cur_lo, cur_hi = 0.0, None, None
        for a, b in pts:
            if cur_hi is None or a > cur_hi:
                if cur_hi is not None:
                    covered += cur_hi - cur_lo
                cur_lo, cur_hi = a, b
            else:
                cur_hi = max(cur_hi, b)
        if cur_hi is not None:
            covered += cur_hi - cur_lo
        frac[i] = covered / (e_ - s)
    return over, frac


def my_noise(seed, total, block):
    """The floor of a file from the task's convention: block i from default_rng([seed, 1, i]), I then Q, per-component
    sigma sqrt(NOISE * (fs / 1e6) / 2)."""
    out = np.empty(total, dtype=np.complex64)
    sigma = np.sqrt(NOISE * (FS / 1e6) / 2)
    for i, lo in enumerate(range(0, total, block)):
        hi = min(lo + block, total)
        rng = np.random.default_rng([seed, 1, i])
        out.real[lo:hi] = rng.normal(0, sigma, hi - lo)
        out.imag[lo:hi] = rng.normal(0, sigma, hi - lo)
    return out


def hex_bits(h, n):
    return [int(c) for c in bin(int(h, 16))[2:].zfill(len(h) * 4)[:n]] if h else []


def my_burst(e, total):
    """One burst alone, as the file holds it: ``(lo, samples)``, from the sidecar's own keys and the repository's modulator."""
    bits = hex_bits(e['air_bits'], e['air_bits_length'])
    burst, lead = br.gfsk(np.asarray(bits), FS, h=br.GFSK_H, delay=e['timing_frac'])
    amp = np.sqrt(NOISE * 10 ** (e['snr_db'] / 10))
    lo = e['start_sample'] - lead
    n = np.arange(lo, lo + len(burst))
    cycles = (e['channel'] + 2402 - CENTER) * 1e6 / FS * n
    out = amp * burst * np.exp(1j * e['burst_phase']) * np.exp(2j * np.pi * (cycles - np.floor(cycles)))
    return lo, out.astype(np.complex64)


# --- reading samples ---------------------------------------------------------------------------------------

def shifted(iq, lo, hi, mhz):
    n = np.arange(lo, hi)
    cycles = (mhz - CENTER) * 1e6 / FS * n
    return np.asarray(iq[lo:hi]).astype(np.complex128) * np.exp(-2j * np.pi * (cycles - np.floor(cycles)))


def carrier_of(iq, s0, s1):
    x = np.asarray(iq[s0 + 250:s1 - 250])
    nfft = 1024
    m = len(x) // nfft
    x = x[:m * nfft].reshape(m, nfft) * np.hanning(nfft)
    spec = np.sum(np.abs(np.fft.fft(x, axis=1)) ** 2, axis=0)
    freq = np.fft.fftfreq(nfft, 1 / FS)
    near = np.abs(freq - freq[int(np.argmax(spec))]) < 500e3
    mhz = CENTER + np.sum(freq[near] * spec[near]) / np.sum(spec[near]) / 1e6
    return mhz, int(round(mhz - 2402))


def coarse_demod(iq, lo_hint, hi_hint, mhz, nbits, lap):
    """The access code's position and the bits of a burst, from the samples and a hint of where it is (to a few hundred
    samples): shift down, a 0.7 MHz low-pass, a discriminator, the 68 known bits correlated, the threshold fitted on them."""
    lo, hi = max(0, lo_hint - 1500), min(len(iq), hi_hint + 2500)
    bb = lfilter(TAPS, 1, shifted(iq, lo, hi, mhz))
    d = np.angle(bb[1:] * np.conj(bb[:-1]))
    dm = d - np.convolve(d, np.ones(2001) / 2001, 'same')
    a68 = np.array(br.access_code(lap)[:68]) * 2.0 - 1.0
    tmpl = np.repeat(a68, SPS)
    tmpl -= tmpl.mean()
    r = fftconvolve(dm, tmpl[::-1], 'valid')
    a = max(0, lo_hint - lo - 600 + GD)
    p = a + int(np.argmax(r[a:lo_hint - lo + 600 + GD]))
    centres = p + (np.arange(nbits) + 0.5) * SPS - 0.5
    f = np.interp(centres, np.arange(len(d)), d)
    slope, thr = np.polyfit(a68, f[:68], 1)
    return lo + p - GD, (f > thr).astype(np.uint8)


_MODELS = {}


def model(bits, frac):
    key = (bytes(bits), round(frac, 6))
    if key not in _MODELS:
        if len(_MODELS) > 100:
            _MODELS.clear()
        _MODELS[key] = br.gfsk(np.asarray(bits), FS, h=br.GFSK_H, delay=frac)
    return _MODELS[key]


def fit_burst(iq, e, bits, coarse):
    """The burst's own modulation fitted to the samples: ``start`` (to a fraction of a sample), ``amp``, the carrier error
    ``df`` in Hz and the carrier phase."""
    z = shifted(iq, int(coarse) - 400, int(coarse) + len(bits) * SPS + 800, e['channel_mhz'])
    base = int(coarse) - 400
    scores = {}
    for i in range(-5, 6):
        for tenth in range(10):
            m, lead = model(bits, tenth / 10)
            lo = int(coarse) + i - lead - base
            seg = z[lo:lo + len(m)]
            if lo < 0 or len(seg) < len(m):
                continue
            scores[i + tenth / 10] = abs(np.vdot(m, seg))
    keys = sorted(scores)
    best = max(keys, key=scores.get)
    j = keys.index(best)
    delta = 0.0
    if 0 < j < len(keys) - 1:
        y0, y1, y2 = scores[keys[j - 1]], scores[best], scores[keys[j + 1]]
        den = y0 - 2 * y1 + y2
        delta = 0.1 * 0.5 * (y0 - y2) / den if den else 0.0
    start = int(coarse) + best + delta
    i0 = int(np.floor(start))
    m, lead = br.gfsk(np.asarray(bits), FS, h=br.GFSK_H, delay=start - i0)
    lo = i0 - lead - base
    seg = z[lo:lo + len(m)]
    gain = np.vdot(m, seg) / np.vdot(m, m)
    prod = seg * np.conj(m)
    lag = 400
    df = np.angle(np.sum(prod[lag:] * np.conj(prod[:-lag]))) / (2 * np.pi * lag) * FS
    return dict(start=start, amp=abs(gain), phase=float(np.angle(gain)), df=float(df))


def project(iq, e):
    """Level (dB over the floor) and carrier phase of a burst whose start is the sidecar's: the model at the sidecar's own
    timing against the samples, in a window with no other burst."""
    bits = hex_bits(e['air_bits'], e['air_bits_length'])
    m, lead = br.gfsk(np.asarray(bits), FS, h=br.GFSK_H, delay=e['timing_frac'])
    lo = e['start_sample'] - lead
    z = shifted(iq, lo, lo + len(m), e['channel_mhz'])
    gain = np.vdot(m, z) / np.vdot(m, m)
    return 10 * np.log10(abs(gain) ** 2 / NOISE), float(np.angle(gain))


def btbb(bits, e, uap):
    raw = bytes(int(b) for b in bits[4:])
    pkt = _lib.btbb_packet_new()
    try:
        _lib.btbb_packet_set_data(pkt, ctypes.cast(ctypes.c_char_p(raw), ctypes.POINTER(ctypes.c_char)), len(raw), 0,
                                  ((e['clk'] >> 1) & 0x3F) << 1)
        _lib.btbb_packet_set_uap(pkt, uap)
        _lib.btbb_packet_set_flag(pkt, 4, 1)
        _lib.btbb_packet_set_flag(pkt, 0, 1)
        if _lib.btbb_decode_header(pkt) != 1:
            return False, None, b''
        hdr = _lib.btbb_packet_get_header_packed(pkt) & 0x3FFFF
        if not e['payload_full_hex']:
            return hdr == e['header18'], 10, b''
        verdict = _lib.btbb_decode_payload(pkt)
        want = bytes.fromhex(e['payload_full_hex'])
        buf = (ctypes.c_char * (len(want) + 32))()
        _lib.btbb_get_payload_packed(pkt, buf)
        return hdr == e['header18'], verdict, bytes(buf[:len(want)])
    finally:
        _lib.btbb_packet_unref(pkt)


def free_bursts(side, margin=300, span=2):
    """Indices of the bursts with no other burst within ``margin`` samples in time on this or an adjacent channel (and none
    at all overlapping): the 'collision-free stretch'."""
    b = side['bursts']
    win = [window(e) for e in b]
    out = []
    for i, e in enumerate(b):
        ok = True
        for j in range(max(0, i - 40), min(len(b), i + 41)):
            if j != i and abs(b[j]['channel'] - e['channel']) <= span and win[j][0] < win[i][1] + margin and win[j][1] > win[i][0] - margin:
                ok = False
                break
        if ok:
            out.append(i)
    return out


# --- the sidecar --------------------------------------------------------------------------------------------

def check_keys(side, label):
    need = ['generator', 'generator_commit', 'lap', 'uap', 'sample_rate', 'center_mhz', 'modulation', 'slot_samples',
            'start_sample_meaning', 'clk_convention', 'snr_bw_hz', 'noise_1mhz', 'bursts', 'symbol_phases', 'per_burst_keys',
            'n_samples', 'devices', 'seed', 'air_bits_omitted', 'burst_window', 'conformance', 'window', 'notes',
            'counts_per_device', 'counts_per_type', 'n_collisions', 'fraction_collided', 'timing_frac', 'symbol_phase',
            'sync_word_min_distance_piconets', 'sync_word_min_distance_all', 'bursts_not_rendered', 'afh_map']
    miss = [k for k in need if k not in side]
    check(not miss, '%s: the sidecar has every key (%s)' % (label, miss))
    per = ['ptype', 'lap', 'uap', 'channel', 'start_sample', 'timing_frac', 'clk', 'snr_db', 'llid', 'payload_length',
           'payload_hex', 'lmp_opcode', 'lmp_name', 'lmp_tid', 'lt_addr', 'flow', 'arqn', 'seqn', 'air_bits',
           'air_bits_length', 'body_crc_bits_hex', 'channel_mhz', 'symbol_phase', 'device', 'role', 'ours', 'piconet_slot',
           'overlaps', 'collision', 'overlap_frac', 'in_window', 'header18']
    miss = [k for k in per if k not in side['per_burst_keys']]
    check(not miss, '%s: per_burst_keys has every per-burst truth key (%s)' % (label, miss))
    check(side['n_samples'] == side['n_noise_blocks'] * side['noise_block_samples'], '%s: n_samples is a whole number of noise blocks' % label)
    miss = [k for k in per if any(k not in e for e in side['bursts'])]
    check(not miss, '%s: every burst has every one of them (%s)' % (label, miss))
    check(side['lap'] is None and side['uap'] is None and side['timing_frac'] is None and side['symbol_phase'] is None
          and side['snr_bw_hz'] == 1e6 and side['noise_1mhz'] == NOISE and side['sample_rate'] == FS
          and side['center_mhz'] == CENTER and side['air_bits_omitted'] is False and side['symbol_phases'] == [0, 20],
          '%s: the per-burst values are null at the top, the floor, rate and centre are the repository\'s' % label)
    check('31-50' in side['conformance'] and 'joiner' in side['conformance'] and 'unsynchronised' in side['conformance'],
          '%s: the conformance text names the 31-50 map, the joiners\' sequences and the unsynchronised piconets' % label)
    check('air time' in side['burst_window'] and '40' in side['burst_window'], '%s: the burst window is defined' % label)


def check_devices(side, label):
    devs = side['devices']
    pic = [d for d in devs if d['kind'] == 'piconet']
    joi = [d for d in devs if d['kind'] == 'joiner']
    check(len(pic) == 8 and len(joi) == 3 and [d['id'] for d in devs] == list(range(11)),
          '%s: devices 0-7 are piconets and 8-10 joiners' % label)
    check(sum(d['ours'] for d in pic) == 6 and all(d['ours'] for d in joi), '%s: 6 of the 8 piconets are ours, the 2 others not, and the joiners are ours' % label)
    check(side['not_ours_devices'] == [d['id'] for d in pic if not d['ours']] and len(side['not_ours_devices']) == 2,
          '%s: not_ours_devices names the two' % label)
    by = {d['id']: d for d in devs}
    check(all(e['ours'] == by[e['device']]['ours'] for e in side['bursts'] + side['bursts_not_rendered']),
          '%s: every burst\'s ours is its device\'s' % label)
    laps = [d['lap'] for d in devs] + [d['paged']['lap'] for d in joi]
    check(len(laps) == 14 and len(set(laps)) == 14, '%s: the 8 + 3 + 3 LAPs (masters and paged devices) are all different' % label)
    check(all(not 0x9E8B00 <= x < 0x9E8B40 and x != 0x9E8B33 for x in laps), '%s: no LAP is in the reserved range' % label)
    sw = {x: int(''.join(map(str, br.sync_word(x))), 2) for x in laps}
    dist = lambda xs: min(bin(sw[a] ^ sw[b]).count('1') for i, a in enumerate(xs) for b in xs[i + 1:])
    check(dist(laps[:8]) == side['sync_word_min_distance_piconets'] and dist(laps) == side['sync_word_min_distance_all'],
          '%s: the stated sync-word distances (%d for the 8 piconets, %d for all 14) are what the sync words give'
          % (label, side['sync_word_min_distance_piconets'], side['sync_word_min_distance_all']))
    check(side['sync_word_min_distance_all'] >= side['sync_word_distance_required'] >= 14,
          '%s: and none is nearer than the %d bits asked for' % (label, side['sync_word_distance_required']))
    for d in devs:
        others = [sw[x] for x in laps if x != d['lap']]
        if not all(bin(sw[d['lap']] ^ o).count('1') >= d['sync_word_min_distance'] for o in others):
            check(False, '%s: device %d sync_word_min_distance' % (label, d['id']))
            break
    else:
        check(True, '%s: every device\'s own sync_word_min_distance is true' % label)
    # the devices' hop addresses differ, and so do the clocks
    check(len({d['clk0'] for d in pic}) == 8 and all(d['clk0'] % 4 == 0 for d in pic),
          '%s: eight different master clocks, each a master slot\'s' % label)
    offs = sorted(d['slot_grid_offset_int'] for d in pic)
    gaps = [min((b - a), SLOT - (b - a)) for a, b in zip(offs, offs[1:] + [offs[0] + SLOT])]
    check(min(gaps) >= 800 and all(0 <= o < SLOT for o in offs) and len({d['slot_grid_offset_frac'] for d in pic}) == 8,
          '%s: slot grid offsets are 0..24999 with fractions, at least 800 samples apart (least gap %d)' % (label, min(gaps)))
    # each device's offset, found from its bursts alone (the table is not used)
    ok = True
    found = {}
    for d in pic:
        bs = [e for e in side['bursts'] if e['device'] == d['id']]
        r = [(e['start_sample'] - e['piconet_slot'] * SLOT - g.GRID_BASE) % SLOT for e in bs]
        lo = min(r)
        found[d['id']] = lo
        off = d['slot_grid_offset_int']
        ok &= all(off <= x <= off + 40 for x in r)
    check(ok, '%s: every burst of a piconet starts 0 to 40 samples after its slot boundary, which is at the table\'s slot_grid_offset_int' % label)
    f = sorted(found.values())
    fg = [min((b - a), SLOT - (b - a)) for a, b in zip(f, f[1:] + [f[0] + SLOT])]
    check(min(fg) >= 800, '%s: so the grids are NOT aligned: the offsets found from the bursts are 800 samples or more apart (%d)' % (label, min(fg)))
    check(all(d['level_db'] >= 8.0 and d['level_db'] <= 22.0 for d in pic) or side['level_range_db'] != [8.0, 22.0],
          '%s: each piconet\'s level is 8 to 22 dB' % label)


PINNED_NOT_OURS = {9201: [5, 7], 9202: [4, 5]}     # the delivered files' not-ours piconets


def rederive_not_ours(seed):
    """The not-ours pair of a seed from the task's recipe, written again: the stream default_rng([seed, 2**20]) gives
    14 LAPs (24-bit, not 0, not reserved, not the GIAC, not repeated, every sync word 22 bits from every earlier one), then 8 slot-grid
    offsets (0..24999, circular separation 800), then the choice of 2 of 8 without replacement."""
    rng = np.random.default_rng([seed, 1 << 20])
    syncs, laps = [], []
    while len(laps) < 14:
        x = int(rng.integers(1, 1 << 24))
        if x in laps or 0x9E8B00 <= x < 0x9E8B40:
            continue
        w = int(''.join(str(b) for b in br.sync_word(x)), 2)
        if any(bin(w ^ v).count('1') < 22 for v in syncs):
            continue
        laps.append(x)
        syncs.append(w)
    offs = []
    while len(offs) < 8:
        o = int(rng.integers(0, SLOT))
        if all(min((o - q) % SLOT, (q - o) % SLOT) >= 800 for q in offs):
            offs.append(o)
    return sorted(int(i) for i in rng.choice(8, size=2, replace=False))


def check_ours_pinned(side, label):
    """Which two piconets are not ours: re-derived, and equal to the delivered files' values."""
    seed = side['seed']
    got = [d['id'] for d in side['devices'] if d['kind'] == 'piconet' and not d['ours']]
    check(got == rederive_not_ours(seed), '%s: seed %d: the not-ours piconets %s are those the recipe draws' % (label, seed, got))
    if seed in PINNED_NOT_OURS:
        check(got == PINNED_NOT_OURS[seed], '%s: and they are the delivered values %s' % (label, PINNED_NOT_OURS[seed]))
    b = side['bursts'] + side['bursts_not_rendered']
    check(sorted({e['device'] for e in b if not e['ours']}) == got and side['not_ours_devices'] == got,
          '%s: the bursts flagged not ours are exactly those devices\' bursts' % label)


def check_kind_keys(side, label):
    k = side['per_burst_keys_by_kind']
    bad = []
    for e in side['bursts']:
        want = set(k['piconet'] if e['device'] < 8 else k['joiner']) | (set(k['fhs_extra']) if e['ptype'] == 'FHS' and e['device'] >= 8 else set())
        if set(e) != want:
            bad.append((e['index'], sorted(set(e) ^ want)))
    check(not bad, '%s: every burst has exactly the keys of its kind (per_burst_keys_by_kind) %s' % (label, bad[:2]))
    check(set(side['per_burst_keys']) == set(k['piconet']) | set(k['joiner']) | set(k['fhs_extra']) and 'UNION' in side['per_burst_keys_note'],
          '%s: per_burst_keys is the union and the note says so' % label)
    check(all(e['end_sample'] == e['start_sample'] + e['air_bits_length'] * SPS for e in side['bursts']),
          '%s: every burst, joiners included, has end_sample = start_sample + air_bits_length * 40' % label)
    check('sir_db is the ratio' in ' '.join(side['notes']) or 'ratio of the two bursts' in ' '.join(side['notes']), '%s: the notes say what sir_db is and warn about adjacent channels' % label)
    check('air_bits_length' in ' '.join(side['notes']) and 'padded' in ' '.join(side['notes']), '%s: the notes say the air_bits hex is padded: use air_bits_length' % label)


def check_overlap_unit(mod, label='unit'):
    """compute_overlaps on hand-made windows: sub-symbol overlaps count, zero and touching ones do not."""
    # (start, end, channel, snr)
    w = [(0.0, 100.0, 10, 10.0), (99.36, 200.0, 10, 14.0),            # 0.64 samples, same channel
         (1000.0, 1100.0, 20, 10.0), (1079.3, 1200.0, 20, 12.0),      # 20.7 samples (about half a symbol)
         (2000.0, 2100.0, 30, 10.0), (2100.0, 2200.0, 30, 10.0),      # touching: no overlap
         (3000.0, 3100.0, 40, 10.0), (3100.0, 3200.0, 41, 10.0),      # touching on adjacent channels: none
         (4000.0, 4100.0, 5, 20.0), (4099.5, 4200.0, 6, 10.0),        # 0.5 samples, adjacent channel: listed, no collision
         (5000.0, 5100.0, 50, 10.0), (5050.0, 5150.0, 52, 10.0)]      # 2 channels apart: nothing
    st, en, ch, sn = [x[0] for x in w], [x[1] for x in w], [x[2] for x in w], [x[3] for x in w]
    ov, col, frac = mod.compute_overlaps(st, en, ch, sn, list(range(len(w))))
    want = {0: [(1, 0.64)], 1: [(0, 0.64)], 2: [(3, 20.7)], 3: [(2, 20.7)], 8: [(9, 0.5)], 9: [(8, 0.5)]}
    ok = True
    for i in range(len(w)):
        got = [(r['burst'], r['overlap_samples']) for r in ov[i]]
        exp = want.get(i, [])
        ok &= len(got) == len(exp) and all(a[0] == b_[0] and abs(a[1] - b_[1]) < 1e-9 for a, b_ in zip(got, exp))
    check(ok, '%s: compute_overlaps: overlaps of 0.64, 20.7 and 0.5 samples are found, 0 (touching) and 2-channel ones are not %s'
          % (label, [[(r['burst'], r['overlap_samples']) for r in o] for o in ov]))
    check(col == [True, True, True, True, False, False, False, False, False, False, False, False],
          '%s: only the same-channel ones are collisions (the 0.64 sample overlap is one)' % label)
    check(abs(frac[0] - 0.0064) < 1e-9 and abs(frac[3] - 20.7 / 120.7) < 1e-9 and frac[8] == 0.0,
          '%s: overlap_frac is the covered fraction of the burst\'s own length' % label)
    check(ov[8][0]['channel_offset_mhz'] == 1.0 and ov[9][0]['channel_offset_mhz'] == -1.0 and abs(ov[8][0]['sir_db'] - 10.0) < 1e-9
          and abs(ov[0][0]['sir_db'] + 4.0) < 1e-9, '%s: offsets are other minus this, SIR is this minus other' % label)


def check_counts(side, label):
    b = side['bursts']
    per = {}
    for e in b:
        per.setdefault(str(e['device']), {})
        per[str(e['device'])][e['ptype']] = per[str(e['device'])].get(e['ptype'], 0) + 1
    check(per == side['counts_per_device_and_type'] and {k: sum(v.values()) for k, v in per.items()} == side['counts_per_device'],
          '%s: counts per device and per type are what the bursts hold' % label)
    types = {}
    for e in b:
        types[e['ptype']] = types.get(e['ptype'], 0) + 1
    check(types == side['counts_per_type'] and side['n_bursts'] == len(b), '%s: counts per type and n_bursts are right' % label)
    check(all(per.get(str(d), {}) for d in range(8)), '%s: every piconet has bursts' % label)
    need = {'NULL', 'POLL', 'DM1', 'DH1', 'DM3', 'DH3', 'DM5', 'DH5'}
    check(need <= set(types), '%s: every packet type of the mix is in the file (missing %s)' % (label, need - set(types)))
    check(sum(1 for e in b if e['ptype'] == 'DM1' and e['llid'] == 3) > 0 and all(e['lmp_name'] is None or e['ptype'] == 'DM1' for e in b),
          '%s: LMP PDUs are in DM1 only, and there are some' % label)
    check(sum(e['collision'] for e in b) == side['n_collisions'] and abs(side['fraction_collided'] - side['n_collisions'] / len(b)) < 1e-12,
          '%s: n_collisions and fraction_collided are right' % label)
    check(0.01 < side['fraction_collided'] < 0.2,
          '%s: the fraction of collided bursts is %.1f %% (%d of %d), in the sane range of a few to ten percent' % (
              label, 100 * side['fraction_collided'], side['n_collisions'], len(b)))
    check(all(e['index'] == i for i, e in enumerate(b)) and all(window(a)[0] <= window(c)[0] for a, c in zip(b, b[1:])),
          '%s: bursts are sorted by start and indexed' % label)
    check(all(e['in_window'] and e['rendered'] and 27 <= e['channel'] <= 51 for e in b)
          and all((not e['in_window']) and not 27 <= e['channel'] <= 51 for e in side['bursts_not_rendered']),
          '%s: the bursts are the in-window ones, the others are in bursts_not_rendered' % label)


def check_overlap_truth(side, label):
    b = side['bursts']
    over, frac = independent_overlaps(b)
    bad = []
    n_pairs = 0
    for i, e in enumerate(b):
        got = {r['burst']: r for r in e['overlaps']}
        want = over[i]
        n_pairs += len(want)
        if set(got) != set(want):
            bad.append((i, 'set'))
            continue
        for j, r in got.items():
            o = b[j]
            if (r['device'] != o['device'] or abs(r['overlap_samples'] - want[j]) > 1e-6
                    or r['channel_offset_mhz'] != o['channel'] - e['channel']
                    or abs(r['sir_db'] - (e['snr_db'] - o['snr_db'])) > 1e-3):
                bad.append((i, 'field', j))
        same = any(r['channel_offset_mhz'] == 0 for r in e['overlaps'])
        if e['collision'] != same or abs(e['overlap_frac'] - frac[i]) > 1e-9:
            bad.append((i, 'collision/frac'))
    check(not bad, '%s: overlaps, channel offset, overlap samples, SIR, collision and overlap_frac of EVERY one of the %d bursts equal the independent sweep (%d pairs) %s'
          % (label, len(b), n_pairs // 2, bad[:3]))
    # symmetry: if A lists B, B lists A, with the opposite offset and SIR
    sym = all(any(r2['burst'] == i and r2['channel_offset_mhz'] == -r['channel_offset_mhz'] and abs(r2['sir_db'] + r['sir_db']) < 1e-3
                  and abs(r2['overlap_samples'] - r['overlap_samples']) < 1e-9 for r2 in b[r['burst']]['overlaps'])
              for i, e in enumerate(b) for r in e['overlaps'])
    check(sym, '%s: every overlap is listed from both sides with the offset and the SIR reversed' % label)
    adj = sum(1 for e in b for r in e['overlaps'] if abs(r['channel_offset_mhz']) == 1)
    same = sum(1 for e in b for r in e['overlaps'] if r['channel_offset_mhz'] == 0)
    check(adj > 0 and same > 0, '%s: the run has both same-channel (%d) and adjacent-channel (%d) overlaps, so a list that left one out would show' % (label, same // 2, adj // 2))
    check(all(e['overlaps'] == [] and not e['collision'] and e['overlap_frac'] == 0 for e in side['bursts_not_rendered']),
          '%s: bursts that are not in the samples have no overlaps' % label)
    # ones that are overlapped at all in time on any channel: the total number of collided bursts
    check(any(e['collision'] and e['overlap_frac'] < 1 for e in b), '%s: some collided bursts are only partly overlapped' % label)


def check_slots_and_hops(side, label):
    b = side['bursts']
    devs = {d['id']: d for d in side['devices']}
    by = {}
    for e in b:
        by.setdefault(e['device'], []).append(e)
    # no two bursts of one device overlap, with 200 samples of margin for the ramps
    bad = []
    for d, bs in by.items():
        bs = sorted(bs, key=lambda e: window(e)[0])
        for a, c in zip(bs, bs[1:]):
            if window(a)[1] + 200 > window(c)[0] and d < 8:
                bad.append((d, a['index'], c['index']))
    check(not bad, '%s: no two bursts of one piconet overlap in time (nor are within 200 samples) %s' % (label, bad[:3]))
    # the slave follows its master
    n_pairs = n_multi = 0
    bad = []
    nslots = {t: v[1] for t, v in br.PACKET_TYPES.items()}
    for d in range(8):
        dv = devs[d]
        addr = dv['uap'] << 24 | dv['lap']
        bs = by.get(d, [])
        masters = {e['pair']: e for e in bs if e['role'] == 'master'}
        slaves = [e for e in bs if e['role'] == 'slave']
        for e in bs:
            # the clock of the slot, from the device's clk0 alone
            if e['role'] == 'master':
                clk_m = (dv['clk0'] + 2 * e['piconet_slot']) & 0x0FFFFFFF
                if e['clk'] != clk_m or e['hop_clk'] != clk_m or e['piconet_slot'] % 2 or clk_m % 4:
                    bad.append(('master clk', e['index']))
            else:
                m = masters.get(e['pair'])
                if m is None:
                    bad.append(('slave without master', e['index']))
                    continue
                n = nslots[m['ptype']]
                n_pairs += 1
                n_multi += n > 1
                clk_m = (dv['clk0'] + 2 * m['piconet_slot']) & 0x0FFFFFFF
                if (e['piconet_slot'] != m['piconet_slot'] + n or e['start_sample'] - m['start_sample'] != n * SLOT
                        or e['clk'] != (clk_m + 2 * n) & 0x0FFFFFFF or e['hop_clk'] != clk_m or e['channel'] != m['channel']
                        or e['ptype'] not in ('NULL', 'DM1', 'DH1')):
                    bad.append(('slave slot', e['index']))
            # the channel, from the address and the clock alone
            hc = (dv['clk0'] + 2 * (e['piconet_slot'] if e['role'] == 'master' else masters[e['pair']]['piconet_slot'])) & 0x0FFFFFFF \
                if e['role'] == 'master' or e['pair'] in masters else None
            if hc is None or e['channel'] != bt_hop.hop_channel(hc, addr, MASK) or not 31 <= e['channel'] <= 50:
                bad.append(('hop', e['index']))
            if e['channel_mhz'] != 2402 + e['channel']:
                bad.append(('mhz', e['index']))
        # a master's next packet is after its slave's slot
        ms = sorted(masters.values(), key=lambda e: e['piconet_slot'])
        for a, c in zip(ms, ms[1:]):
            if c['piconet_slot'] < a['piconet_slot'] + nslots[a['ptype']] + 1 or c['piconet_slot'] % 2:
                bad.append(('master spacing', c['index']))
        # the slot grid: where a burst sits against its slot
        for e in bs:
            x = e['start_sample'] + e['timing_frac'] - (g.GRID_BASE + dv['slot_grid_offset_samples'] + SLOT * e['piconet_slot'])
            if not -1 <= x < 41:
                bad.append(('grid', e['index'], x))
        # the traffic profile
        n_m = len(masters)
        if not 0.1 < n_m / (max(e['piconet_slot'] for e in bs) / 2 + 1) < 0.5:
            bad.append(('profile', d))
    check(not bad, '%s: slaves follow their master by n slots on the master\'s channel, clocks and hops recomputed from clk0 and the address, spacing and grid %s' % (label, bad[:3]))
    check(n_pairs > 100 and n_multi > 5, '%s: %d slave answers, %d of them to multi-slot packets' % (label, n_pairs, n_multi))
    # the packets' own rules: the bits are the packet of the sidecar's fields (header from the fields)
    bad = [e['index'] for e in b if e['device'] < 8 and (e['lt_addr'] != 1 or e['flow'] != 1 or e['arqn'] != 1 or e['lap'] != devs[e['device']]['lap']
                                                      or e['uap'] != devs[e['device']]['uap'])]
    check(not bad, '%s: every piconet burst has the device\'s LAP and UAP and LT_ADDR 1, FLOW 1, ARQN 1' % label)
    bad = []
    for e in b:
        if e['device'] < 8:
            p = br.Packet(e['lap'], e['uap'], e['clk'], e['ptype'], bytes.fromhex(e['payload_hex']), lt_addr=1, flow=1, arqn=1,
                          seqn=e['seqn'], llid=e['llid'] if e['llid'] is not None else 0b10, payload_flow=1)
            if hex_bits(e['air_bits'], e['air_bits_length']) != list(p.bits) or p.header18 != e['header18']:
                bad.append(e['index'])
    check(not bad, '%s: air_bits are the packet of the sidecar\'s own fields (%d bursts wrong)' % (label, len(bad)))
    # the slot occupancy: air time fits in the packet's slots
    check(all(window(e)[1] - window(e)[0] < SLOT * nslots[e['ptype']] - 40 for e in b if e['device'] < 8),
          '%s: a packet\'s air time fits inside its slots' % label)


def check_joiners(side, label):
    from apps import bt_fhs
    b = side['bursts'] + side['bursts_not_rendered']
    joiners = {d['id']: d for d in side['devices'] if d['kind'] == 'joiner'}
    bad = []
    n_followup = 0
    for d, dv in joiners.items():
        bs = sorted([e for e in b if e['device'] == d], key=lambda e: e['tick'])
        paged_lap, paged_uap = dv['paged']['lap'], dv['paged']['uap']
        ex = dv['exchange']
        master_addr = dv['uap'] << 24 | dv['lap']
        for e in bs:
            h = e['hop']
            if e['kind'] == 'id_page':
                ch = hs.page(h['clke'], paged_lap, paged_uap, h['koffset'], h['knudge'])
            elif e['kind'] == 'id_response' or e['kind'] == 'id_ack':
                ch = hs.peripheral_page_response(h['clkn_frozen'], h['clkn'], paged_lap, paged_uap, h['n'])
            elif e['kind'] == 'fhs':
                ch = hs.central_page_response(h['clke_frozen'], h['clke'], paged_lap, paged_uap, h['koffset'], h['knudge'], h['n'])
            else:
                ch = bt_hop.hop_channel(h['clk'], master_addr, None)
                n_followup += 1
                if e['clk'] != h['clk']:
                    bad.append(('clk', d, e['tick']))
            if ch != e['channel']:
                bad.append((e['kind'], d, e['tick']))
            # the burst's place in time: ticks of 12500 samples from the exchange's origin
            if e['start_sample'] != ex['origin_sample'] + e['tick'] * 12500 or ex['origin_sample'] % SPS:
                bad.append(('tick', d, e['tick']))
            # LAPs: IDs and the FHS carry the paged device's, the follow-up the master's
            want = master_addr & 0xFFFFFF if e['kind'].startswith('followup') else paged_lap
            if e['lap'] != want:
                bad.append(('lap', d, e['tick']))
        ch_master = [e for e in bs if e['kind'] == 'followup_master']
        if len(ch_master) != 12:
            bad.append(('followup count', d, len(ch_master)))
        ids = [e for e in bs if e['kind'] == 'id_page']
        fhs = [e for e in bs if e['kind'] == 'fhs']
        if len(fhs) != 1 or not ids or len([e for e in bs if e['kind'] == 'id_response']) != 1 or len([e for e in bs if e['kind'] == 'id_ack']) != 1:
            bad.append(('structure', d))
        # the slave answer is on the basic kernel at ITS clock, not its master's
        for e in bs:
            if e['kind'] == 'followup_slave' and e['clk'] % 4 != 2:
                bad.append(('slave clk', d))
        for e in bs:
            if e['kind'] == 'fhs' and e['rendered']:
                bits = hex_bits(e['air_bits'], e['air_bits_length'])
                x = bt_fhs.xprc(e['hop']['clke_frozen'], e['hop']['koffset'], e['hop']['knudge'], e['hop']['n'])
                f = bt_fhs.page_response(paged_lap, paged_uap, dv['lap'], dv['uap'], dv['nap'], dv['cod'], 1, e['clk'], x, sr=e['fhs']['sr'])
                if bits != list(f.bits):
                    bad.append(('fhs bits', d))
    check(not bad, '%s: joiners\' channels recomputed from hop_substates and the basic kernel, ticks, LAPs, structure %s' % (label, bad[:3]))
    check(n_followup > 0, '%s: the exchanges\' follow-up is in the file (%d bursts)' % (label, n_followup))
    # the exchanges do not overlap each other in time, are spread over the file, and each has some bursts outside the window
    o = sorted((d['exchange']['origin_sample'], d['id']) for d in joiners.values())
    check(all(b - a > 400000 for (a, _), (b, __) in zip(o, o[1:])) and o[0][0] < side['n_samples'] / 3 + 1,
          '%s: the three exchanges are in three separate parts of the file (at samples %s)' % (label, [x for x, _ in o]))
    nin = sum(d['exchange']['n_bursts_in_window'] for d in joiners.values())
    nall = sum(d['exchange']['n_bursts'] for d in joiners.values())
    check(nin == len([e for e in side['bursts'] if e['device'] >= 8]) and nall == nin + len(side['bursts_not_rendered']) and nin < nall,
          '%s: the joiners have %d bursts in the window of %d (the rest are in bursts_not_rendered)' % (label, nin, nall))
    check(all(27 <= e['channel'] <= 51 for e in side['bursts'] if e['device'] >= 8)
          and all(any(e['device'] == d for e in side['bursts']) for d in joiners), '%s: every joiner has bursts in the samples' % label)


# --- the samples -----------------------------------------------------------------------------------------------

def check_superposition(iq, side, label, seed, block):
    """The file less the noise against the sum of the bursts rendered alone."""
    total = len(iq)
    noise = my_noise(seed, total, block)
    rest = iq - noise
    exp = np.zeros(total, dtype=np.complex64)
    cover = np.zeros(total, dtype=np.int8)                    # bursts present at each sample (with their ramps)
    per_channel = {}
    for e in side['bursts']:
        lo, x = my_burst(e, total)
        exp[lo:lo + len(x)] += x
        cover[lo:lo + len(x)] += 1
        c = per_channel.setdefault(e['channel'], np.zeros(total, dtype=np.int8))
        c[lo:lo + len(x)] += 1
    same = np.maximum.reduce(list(per_channel.values()))     # most bursts at once on one channel
    amp_max = np.sqrt(NOISE * 10 ** (np.max([e['snr_db'] for e in side['bursts']]) / 10))
    err = np.abs(rest - exp)
    overl = same >= 2
    check(len(iq) == side['n_samples'], '%s: n_samples %d is the length of the array' % (label, side['n_samples']))
    check(err.max() < 1e-4 * amp_max + 1e-6, '%s: the file less the noise is the sum of the bursts rendered alone, worst difference %.2e (a burst has %.3f)' % (label, err.max(), amp_max))
    check(overl.sum() > 5000 and err[overl].max() < 1e-4 * amp_max + 1e-6,
          '%s: and in the %d samples where two bursts are on one channel at once, worst %.2e' % (label, overl.sum(), err[overl].max()))
    check(np.abs(rest[cover == 0]).max() < 1e-6, '%s: where no burst is, the file is the noise to the bit' % label)
    pw = np.mean(np.abs(iq[cover == 0]) ** 2)
    check(abs(10 * np.log10(pw / NOISE_POWER)) < 0.05, '%s: the noise floor is the constant one, %+.3f dB from the stated power' % (label, 10 * np.log10(pw / NOISE_POWER)))


def check_bursts_from_samples(iq, side, label, per_device=2):
    """Start, carrier, level, phase, bits and libbtbb from the samples of collision-free bursts."""
    b = side['bursts']
    free = set(free_bursts(side, span=99))          # nothing else on the air at all: the carrier is read from the whole spectrum
    picks = []
    for d in range(11):
        mine = [i for i in free if b[i]['device'] == d]
        # a variety of types: the first of each of several types
        seen, take = set(), []
        for i in mine:
            if b[i]['ptype'] not in seen and len(take) < (per_device if d < 8 else 1):
                seen.add(b[i]['ptype'])
                take.append(i)
        picks += take
    check(len(picks) >= 15 and len({b[i]['device'] for i in picks}) == 11,
          '%s: %d bursts with nothing else on the air chosen over %d devices' % (label, len(picks), len({b[i]['device'] for i in picks})))
    ok_ch, ok_bits, ok_btbb, ok_wrong = [], [], [], []
    starts, dfs, lvls, phases = [], [], [], []
    for i in picks:
        e = b[i]
        s0, s1 = int(window(e)[0]) - 300, int(window(e)[1]) + 300
        mhz, ch = carrier_of(iq, s0 + 300, s1 - 300)
        ok_ch.append(ch == e['channel'] and abs(mhz - e['channel_mhz']) < 0.15)
        nb = e['air_bits_length']
        want = hex_bits(e['air_bits'], nb)
        coarse, got = coarse_demod(iq, s0, s1, e['channel_mhz'], nb, e['lap'])
        exact = e['snr_db'] > 25
        ok_bits.append(list(got) == want if exact else True)
        fit = fit_burst(iq, e, got if exact else np.array(want), coarse)
        starts.append(fit['start'] - (e['start_sample'] + e['timing_frac']))
        dfs.append(fit['df'])
        lvls.append(10 * np.log10(fit['amp'] ** 2 / NOISE) - e['snr_db'])
        phases.append(fit['phase'] - e['burst_phase'])
        if exact and e['ptype'] != 'ID' and e['device'] < 8:
            hdr_ok, verdict, got_bytes = btbb(got, e, e['uap'])
            ok_btbb.append(hdr_ok and verdict == 10 and got_bytes == bytes.fromhex(e['payload_full_hex'] or ''))
            h2, v2, _ = btbb(got, e, e['uap'] ^ 0x10)
            ok_wrong.append(not (h2 and v2 == 10 and e['payload_full_hex']) if e['payload_full_hex'] else True)
    check(all(ok_ch), '%s: each burst\'s carrier, from the spectrum of the raw samples, is its channel to 150 kHz (%d of %d)' % (label, sum(ok_ch), len(ok_ch)))
    starts, dfs, lvls = np.array(starts), np.array(dfs), np.array(lvls)
    tol = np.array([0.5 if b[i]['snr_db'] > 25 else 1.0 for i in picks])
    check((np.abs(starts) <= tol).all() and abs(starts.mean()) < 0.15,
          '%s: start_sample + timing_frac is where the model of the burst fits: worst %.3f samples at 30 dB (limit 0.5), %.3f at the joiners\' 10-20 dB (limit 1), mean %+.3f'
          % (label, np.abs(starts[tol == 0.5]).max(), np.abs(starts[tol == 1.0]).max(), starts.mean()))
    check(np.abs(dfs).max() < 1500, '%s: carrier within 1.5 kHz of channel_mhz (phase slope against the burst\'s own model), worst %.1f Hz' % (label, np.abs(dfs).max()))
    check(np.abs(lvls).max() < 0.5, '%s: level from the samples is snr_db within 0.5 dB, worst %.3f' % (label, np.abs(lvls).max()))
    pw = np.angle(np.exp(1j * np.array(phases)))
    check(np.abs(pw).max() < 0.3, '%s: carrier phase fitted is the sidecar\'s burst_phase, worst %.3f rad' % (label, np.abs(pw).max()))
    check(all(ok_bits) and len(ok_bits) > 0, '%s: air_bits equal the bits demodulated from the samples at 30 dB (%d of %d)' % (label, sum(ok_bits), len(ok_bits)))
    check(len(ok_btbb) > 10 and all(ok_btbb), '%s: libbtbb decodes header and payload at the device\'s UAP and clock (%d of %d)' % (label, sum(ok_btbb), len(ok_btbb)))
    check(all(ok_wrong), '%s: and refuses the payloads at another UAP' % label)


def check_levels(iq, side, label, per_device=12):
    """Per device the level of collision-free bursts, from the samples."""
    b = side['bursts']
    free = free_bursts(side)
    bad = []
    rows = []
    for d in range(11):
        mine = [i for i in free if b[i]['device'] == d][:per_device]
        if not mine:
            bad.append(('none', d))
            continue
        lv = []
        for i in mine:
            level, _ = project(iq, b[i])
            lv.append(level - b[i]['snr_db'])
        mean_snr = np.mean([b[i]['snr_db'] for i in mine])
        rows.append((d, len(mine), float(np.mean(lv)), float(np.max(np.abs(lv))), mean_snr))
        if abs(np.mean(lv)) > 0.3 or np.max(np.abs(lv)) > 1.0:
            bad.append((d, rows[-1]))
    check(not bad, '%s: per device the level from the samples (collision-free bursts, up to %d each) is snr_db: mean error %s dB, worst %s %s'
          % (label, per_device, ', '.join('%+.2f' % r[2] for r in rows), '%.2f' % max(r[3] for r in rows), bad[:2]))
    dev = {d['id']: d for d in side['devices']}
    pic = [(r[4] - dev[r[0]]['level_db']) for r in rows if r[0] < 8]
    check(all(abs(x) < 1.0 for x in pic), '%s: the mean snr_db of each piconet is its level_db within the jitter (+-1 dB): %s' % (label, ['%+.2f' % x for x in pic]))
    snr = [e['snr_db'] - dev[e['device']]['level_db'] for e in b if e['device'] < 8]
    check(max(abs(np.array(snr))) <= 1.0 + 1e-9 and min(snr) < -0.7 and max(snr) > 0.7, '%s: the per-burst jitter is within +-1 dB and uses it (%.2f to %.2f)' % (label, min(snr), max(snr)))


# --- the blind check ------------------------------------------------------------------------------------------

DEC = 4


def blind_scan(iq, laps, channels, threshold=0.5, fs=FS, base=0):
    """The access-code chain of ``bt_ota_check._chunk`` without the sidecar, for each channel and LAP: shift to the channel, a
    0.7 MHz FIR (101 taps), a discriminator, a template of the 72 access-code bits, normalised correlation above ``threshold``.
    For speed the FIR output is decimated by 4 (the filter is 0.7 MHz wide), so the discriminator and the template are at 10
    samples a symbol. Returns ``{(lap, channel): [sample positions of the access code]}``; peaks closer than 3000 samples are one."""
    taps = firwin(101, 0.7e6, fs=fs)
    n = base + np.arange(len(iq))
    found = {(lap, ch): [] for lap in laps for ch in channels}
    tmpl = {}
    for lap in laps:
        a72 = np.array(br.access_code(lap)[:72]) * 2.0 - 1.0
        t = np.repeat(a72, SPS // DEC).astype(float)
        tmpl[lap] = (t - t.mean())
    for ch in channels:
        cycles = (ch + 2402 - CENTER) * 1e6 / fs * n
        z = np.asarray(iq).astype(np.complex128) * np.exp(-2j * np.pi * (cycles - np.floor(cycles)))
        y = upfirdn(taps, z, up=1, down=DEC)[(len(taps) - 1) // 2 // DEC:]
        d = np.angle(y[1:] * np.conj(y[:-1]))
        dm = d - np.convolve(d, np.ones(2001 // DEC) / (2001 // DEC), 'same')
        energy = np.cumsum(np.concatenate([[0.0], dm ** 2]))
        for lap in laps:
            t = tmpl[lap]
            c = fftconvolve(dm, t[::-1], 'valid')
            en = np.sqrt(np.maximum(energy[len(t):] - energy[:-len(t)], 1e-12)) * np.linalg.norm(t)
            r = c / np.maximum(en[:len(c)], 1e-12)
            peaks = []
            for j in np.flatnonzero(r > threshold):
                if peaks and j - peaks[-1] < 3000 // DEC:
                    if r[j] > r[peaks[-1]]:
                        peaks[-1] = j
                else:
                    peaks.append(j)
            found[(lap, ch)] = [base + DEC * p for p in peaks]
    return found


def check_chain_matches_reference(iq, side, label):
    """The fast scan finds what ``bt_ota_check._chunk`` finds, for one device's LAP on its channel."""
    b = side['bursts']
    free = set(free_bursts(side))
    d = next(i for i in range(8) if any(e['device'] == i and e['index'] in free and e['ptype'] != 'DH5' for e in b))
    e = next(e for e in b if e['device'] == d and e['index'] in free)
    lap = e['lap']
    seg_lo = max(0, e['start_sample'] - 40000)
    seg = iq[seg_lo:e['start_sample'] + 40000]
    stub = dict(sample_rate=FS, channel_mhz=e['channel_mhz'], center_mhz=CENTER, slot_samples=1000, bursts=[], uap=e['uap'])
    a72 = np.array(br.access_code(lap)[:72])
    rows, stats = [], {'found': 0, 'ungraded': 0, 'header': 0, 'crc': 0, 'exact': 0}
    import collections
    st = collections.Counter(stats)
    with mock.patch.object(bt_ota_check, 'SPS', SPS):
        bt_ota_check._chunk(seg, seg_lo, stub, [a72], None, st, rows, 0, len(iq))
    mine = blind_scan(seg, [lap], [e['channel']], base=seg_lo)[(lap, e['channel'])]
    ref = [r[0] for r in rows]
    near = [p for p in mine if abs(p - e['start_sample']) < 300]
    near_ref = [p for p in ref if abs(p - e['start_sample']) < 300]
    check(len(near) == 1 and len(near_ref) == 1 and abs(near[0] - (near_ref[0] - 50)) <= 8,
          '%s: the fast scan agrees with bt_ota_check._chunk (patched to 40 samples a symbol; its peak is the FIR\'s 50 samples late) on device %d: %s against %s - 50, burst at %.1f'
          % (label, d, near, near_ref, e['start_sample'] + e['timing_frac']))


def blind_check(iq, side, label, base=0, channels=range(27, 52), report=None):
    """From the samples without the sidecar's bursts: the LAPs of the table are scanned on every channel 27 to 51; each device's
    collision-free bursts are found; LAPs not in the table never."""
    laps = {d['id']: d['lap'] for d in side['devices'] if d['kind'] == 'piconet'}
    rng = np.random.default_rng([77, 1])
    table_laps = {d['lap'] for d in side['devices']} | {d['paged']['lap'] for d in side['devices'] if d['kind'] == 'joiner'}
    strangers = []
    while len(strangers) < 3:
        x = int(rng.integers(1, 1 << 24))
        if x not in table_laps and not 0x9E8B00 <= x < 0x9E8B40:
            strangers.append(x)
    t0 = time.time()
    found = blind_scan(iq, list(laps.values()) + strangers, list(channels), base=base)
    if report is not None:
        report['seconds'] = time.time() - t0
    n = len(iq)
    b = [e for e in side['bursts'] if window(e)[0] > base + 4000 and window(e)[1] < base + n - 4000]
    free = set(i for i in free_bursts(side) if side['bursts'][i] in b)
    rows = []
    for d, lap in laps.items():
        mine = [e for e in b if e['device'] == d and e['index'] in free]
        hit = 0
        for e in mine:
            pos = found[(lap, e['channel'])]
            hit += any(abs(p - window(e)[0]) < 150 for p in pos)
        all_mine = [e for e in b if e['device'] == d]
        detections = [(ch, p) for (l, ch), ps in found.items() if l == lap for p in ps]
        spurious = [x for x in detections if not any(abs(x[0] - e['channel']) <= 1 and abs(x[1] - window(e)[0]) < 150 for e in all_mine)]
        rows.append((d, len(mine), hit, len(detections), len(spurious), side['devices'][d]['level_db'], side['devices'][d]['ours']))
    stranger_hits = sum(len(ps) for (l, ch), ps in found.items() if l in strangers)
    if report is not None:
        report['rows'] = rows
        report['strangers'] = stranger_hits
    for d, nfree, hit, ndet, nsp, level, ours in rows:
        if level >= 12:
            check(nfree >= 3 and hit >= 0.9 * nfree and nsp <= max(1, 0.02 * ndet),
                  '%s: blind, device %d (%.1f dB, %s): %d of %d collision-free bursts found, %d detections, %d spurious' % (
                      label, d, level, 'ours' if ours else 'not ours', hit, nfree, ndet, nsp))
        else:
            check(nfree >= 3 and hit >= 0.5 * nfree and nsp <= max(1, 0.02 * ndet),
                  '%s: blind, device %d (%.1f dB, weak, %s): %d of %d collision-free bursts found (at least half), %d detections, %d spurious' % (
                      label, d, level, 'ours' if ours else 'not ours', hit, nfree, ndet, nsp))
    check(stranger_hits == 0, '%s: blind, 3 LAPs that are not in the table are found %d times in %d channels' % (label, stranger_hits, len(list(channels))))
    return rows


# --- the written file -----------------------------------------------------------------------------------------

def check_written_file(side_seed=SEED):
    with tempfile.TemporaryDirectory() as tmp:
        iq, side = g.synthesise_mixed(seed=side_seed, blocks=3, name='tmp')
        iq_path, side_path = g.write_mixed('tmp', tmp, seed=side_seed, blocks=3)
        size = os.path.getsize(iq_path)
        back = np.fromfile(iq_path, dtype='<c8')
        sd = json.load(open(side_path))
        check(size == 8 * sd['n_samples'] == 8 * len(iq) and np.array_equal(back, iq),
              'written file: its size %d is 8 * n_samples and it holds the samples of the in-memory run' % size)
        check(json.dumps(sd['bursts']) == json.dumps(json.loads(json.dumps(side['bursts']))), 'written sidecar: the same bursts as the in-memory run')
    # determinism of the plan, and of the first block's samples
    s1, j1, t1 = g.plan_mixed(SEED, 3)
    s2, j2, t2 = g.plan_mixed(SEED, 3)
    check(json.dumps(s1, default=str) == json.dumps(s2, default=str), 'determinism: the same arguments give the same sidecar')
    a = next(g.render_blocks(j1, t1, SEED))
    c = next(g.render_blocks(j2, t2, SEED))
    check(np.array_equal(a, c), 'determinism: the same arguments give the same samples')
    s3 = g.plan_mixed(SEED + 1, 3)[0]
    check([d['lap'] for d in s3['devices']] != [d['lap'] for d in s1['devices']] and
          [d['level_db'] for d in s3['devices']][:8] != [d['level_db'] for d in s1['devices']][:8],
          'another seed makes other devices, levels and offsets')
    check(s1['seed'] == SEED and g.SEEDS == {'mixed_blind_a': 9201, 'mixed_blind_b': 9202}, 'the seeds of the set are 9201 and 9202')
    sets = g.set_files()
    check([n for n, _ in sets] == ['mixed_blind_a', 'mixed_blind_b'] and all(sp['blocks'] == 60 for _, sp in sets),
          'the set is two files of 60 noise blocks (6.29 s, %d bytes each)' % (8 * 60 * 2 ** 22))
    sa = g.plan_mixed(9201, 60)[0]
    check(sa['n_samples'] == 60 * 2 ** 22 and sa['n_samples'] % 2 ** 22 == 0 and 6.2 < sa['n_samples'] / FS < 6.4,
          'full size: n_samples %d is 60 noise blocks, %.2f s' % (sa['n_samples'], sa['n_samples'] / FS))
    check(len(sa['bursts']) > 10000, 'full size: %d bursts, %.1f %% collided' % (len(sa['bursts']), 100 * sa['fraction_collided']))
    return sa


# --- mutants ---------------------------------------------------------------------------------------------------

def mutant_module(old, new):
    assert SRC.count(old) == 1, 'mutation target not unique: %r (%d)' % (old, SRC.count(old))
    spec = importlib.util.spec_from_file_location('bt_synth_mixed_mut', g.__file__)
    mod = importlib.util.module_from_spec(spec)
    exec(compile(SRC.replace(old, new), g.__file__, 'exec'), mod.__dict__)
    return mod


def sidecar_checks(side, label='mutant'):
    check_keys(side, label)
    check_devices(side, label)
    check_counts(side, label)
    check_overlap_truth(side, label)
    check_slots_and_hops(side, label)
    check_joiners(side, label)
    check_kind_keys(side, label)
    check_ours_pinned(side, label)


def check_mutants():
    print('\nMutants: one mistake each, and what catches it', flush=True)
    cases = [
        ('two devices with the same LAP', ("    offsets = draw_grid_offsets(rng, N_PICONETS)\n",
                                           "    offsets = draw_grid_offsets(rng, N_PICONETS)\n    laps[1] = laps[0]\n"), False),
        ('slot grids all aligned (offset 0)', ("    offsets = draw_grid_offsets(rng, N_PICONETS)\n",
                                               "    offsets = [0] * N_PICONETS\n"), False),
        ('a slave answer on the wrong channel', ("channel=channel, slots=br.PACKET_TYPES[ptype_b][1]",
                                                 "channel=channel if role == 'master' else (channel - 30) % 20 + 31, slots=br.PACKET_TYPES[ptype_b][1]"), False),
        ('a slave answer one slot late', ("('slave', s + n, clk_m + 2 * n, k_s)", "('slave', s + n + 2, clk_m + 2 * n + 4, k_s)"), False),
        ('the overlap list ignoring adjacent channels', ("abs(channels[i] - channels[j]) <= 1", "abs(channels[i] - channels[j]) == 0"), False),
        ('the overlap list with only adjacent channels', ("abs(channels[i] - channels[j]) <= 1", "abs(channels[i] - channels[j]) == 1"), False),
        ('a sub-symbol overlap lost (ov > 0 became ov >= 40)', ("if ov > 0 and abs(", "if ov >= 40 and abs("), False),
        ('ours swapped between an ours and a not-ours device (the 6/2 split kept)',
         ("ours=d not in not_ours, lap=laps[d]", "ours=(d not in not_ours) != (d in (0, 5)), lap=laps[d]"), False),
        ('SIR sign inverted', ("sir_db=round(snrs[i] - snrs[j], 4)", "sir_db=round(snrs[j] - snrs[i], 4)"), False),
        ('a burst not added linearly (the second overwrites the first)', ("out[a - b0:b - b0] += samples[a - lo:b - lo]", "out[a - b0:b - b0] = samples[a - lo:b - lo]"), True),
        ('ours flag swapped on one device', ("id=dev['id'], kind='piconet', ours=dev['ours'], lap=dev['lap']",
                                              "id=dev['id'], kind='piconet', ours=(not dev['ours']) if dev['id'] == 0 else dev['ours'], lap=dev['lap']"), False),
        ('a device\'s clock offset not applied to its hops', ("channel = int(hop_fn(clk_m))",
                                                              "channel = int(bt_hop.hop_channel(clk_m - dev['clk0'] + (1 << 27), dev['uap'] << 24 | dev['lap'], MASK))"), False),
        ('n_samples off by one', ("'n_samples': int(total),", "'n_samples': int(total) + 1,"), False),
        ('collision flag ignoring the channel', ("collision.append(bool(ivs))", "collision.append(bool(rows))"), False),
        ('overlap_frac counting every overlap (no union)', ("covered += b - a\n                hi = b", "covered += b - a\n                hi = b - 1000"), False),
    ]
    for name, (old, new), render in cases:
        with quiet() as failed:
            try:
                mod = mutant_module(old, new)
                if render:
                    iq, side = mod.synthesise_mixed(seed=SEED, blocks=3, block_samples=BLOCK * 32, level_range=HIGH)
                    check_superposition(iq, side, 'mutant', SEED, BLOCK * 32)
                else:
                    side = mod.plan_mixed(SEED, 3)[0]
                    sidecar_checks(side)
                    check_overlap_unit(mod, 'mutant')
            except Exception as e:                      # a crash is a catch too, said so
                failed.append('raised %s: %s' % (type(e).__name__, str(e)[:60]))
        short = sorted({w.split(':')[0][:30] + ':' + w.split(':')[-1][:60] for w in failed})
        check(len(failed) > 0, 'mutant "%s" is caught by %d checks, e.g. %s' % (name, len(failed), '; '.join(short[:2])))
    # the unmutated plan passes the same set (positive control)
    with quiet() as failed:
        sidecar_checks(g.plan_mixed(SEED, 3)[0], 'control')
    check(not failed, 'the unmutated sidecar passes every sidecar check (positive control) %s' % failed[:2])


# --- main -------------------------------------------------------------------------------------------------------

def main():
    if '--blind' in sys.argv:
        i = sys.argv.index('--blind')
        iq_path, side_path = sys.argv[i + 1], sys.argv[i + 2]
        seconds = float(sys.argv[sys.argv.index('--seconds') + 1]) if '--seconds' in sys.argv else 0.3
        side = json.load(open(side_path))
        n = int(seconds * FS)
        iq = np.fromfile(iq_path, dtype='<c8', count=n)
        rep = {}
        print('Blind check of the first %.2f s of %s' % (seconds, iq_path), flush=True)
        blind_check(iq, side, os.path.basename(iq_path), report=rep)
        print('scan time %.0f s; rows (device, free bursts, found, detections, spurious, level, ours):' % rep['seconds'])
        for r in rep['rows']:
            print('  ', r)
        print('stranger-LAP detections:', rep['strangers'])
        fails = [w for ok, w in RESULTS if not ok]
        print('RESULT: PASS' if not fails else 'RESULT: FAIL (%d)' % len(fails))
        sys.exit(1 if fails else 0)

    t0 = time.time()
    print('The files: the written file, determinism, the set\n', flush=True)
    full = check_written_file()
    check_keys(full, 'full')
    check_devices(full, 'full')

    print('\nThe default levels (8 to 22 dB), noise blocks of 2**22: 3 blocks, %.2f s' % (3 * 2 ** 22 / FS), flush=True)
    iq, side = g.synthesise_mixed(seed=SEED, blocks=3)
    check_keys(side, 'A')
    check_devices(side, 'A')
    check_counts(side, 'A')
    check_overlap_truth(side, 'A')
    check_slots_and_hops(side, 'A')
    check_joiners(side, 'A')
    check_kind_keys(side, 'A')
    check_ours_pinned(side, 'A')
    check_overlap_unit(g)
    check_ours_pinned(g.plan_mixed(9202, 3)[0], 'B-plan')
    print('\nThe samples of A', flush=True)
    check_superposition(iq, side, 'A', SEED, 2 ** 22)
    check_levels(iq, side, 'A')
    print('\nThe blind check of A (the sidecar is not read for the scan)', flush=True)
    check_chain_matches_reference(iq, side, 'A')
    rep = {}
    blind_check(iq[:2 ** 22], side, 'A, first 0.105 s', report=rep)
    print('  (scan of 8 + 3 LAPs on 25 channels took %.0f s; per device found/free: %s)' % (
        rep['seconds'], ' '.join('%d:%d/%d' % (r[0], r[2], r[1]) for r in rep['rows'])), flush=True)

    print('\nThe same plan at 30 dB, noise blocks of %d (many seams)' % BLOCK, flush=True)
    sd, jobs, total = g.plan_mixed(SEED, 3, level_range=HIGH)
    iq_s = np.concatenate(list(g.render_blocks(jobs, total, SEED, BLOCK)))
    check_superposition(iq_s, sd, 'B (blocks of %d)' % BLOCK, SEED, BLOCK)
    check_bursts_from_samples(iq_s, sd, 'B')
    del iq_s

    check_mutants()
    print('\n%.0f s' % (time.time() - t0))
    fails = [w for ok, w in RESULTS if not ok]
    print('RESULT: PASS' if not fails else 'RESULT: FAIL (%d)' % len(fails))
    for w in fails:
        print('  FAIL', w)
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
