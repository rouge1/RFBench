#!/usr/bin/env python3
"""Hold the fading and narrowband-interference writer to what its samples say.

    python scripts/test_bt_synth_fade.py                     # the tests, about two minutes
    python scripts/test_bt_synth_fade.py --analyse DIR       # the written files of --set fade: what they do

``scripts/bt_synth_fade.py`` writes 204-burst files (DM1 with LMP, DM3, DM5 and
stand-alone FHS) in which a distortion is added to the signal of a reference
file: Rayleigh or Rician fading, or a narrowband interferer over part of a burst.
Every claim in the sidecar is checked here against the samples, from small runs (24
bursts, 6 of each type, noise in blocks of 2**17 so that there are many seams), never
against the generator's own bookkeeping:

* the packets: every burst's ``air_bits`` rebuilt from its fields by the CRC, HEC, FEC
  and whitening written again in ``test_bt_synth_acl.py`` (and, for an FHS, by the layout
  of its payload and the X-input register written here), the keys the same in every burst,
  the schedule, hops and symbol phases; at 30 dB the bits demodulated from the samples are
  ``air_bits`` and libbtbb decodes every burst (the FHS to its address, CLK27-2 and
  payload), refusing it at another UAP; the level and start from a fit of the burst's own
  model;
* the pairing: file minus reference is **exactly zero** off the distorted interval (the
  noise is regenerated here from the seed, and ``file - noise = h * (reference - noise)``
  in a fading burst);
* the fade: ``|h|`` measured from the samples (faded signal over reference signal, both
  with the regenerated noise taken off) against ``fade_gain_db`` at every symbol
  (0.05 dB), and against ``fade_h`` at every sample; ``fade_min_db``, the share below
  -10 dB, the longest run and ``snr_eff_db`` against an independent recomputation from
  the list and from the samples; ``E|h|^2`` (3 %), the autocorrelation at lags
  1/(2 fd) and 1/(4 fd) against J0, the level-crossing rate and the average fade duration
  against Rayleigh theory (15 %), the Rician K, and the independence of one burst's
  process from the next, over thousands of processes of the function; the Doppler
  (``1 - rho`` over a short lag) from the samples of each file;
* the interferer: the interval against the envelope of file minus reference, the SIR
  against the burst's power, the instantaneous frequency (centre, +-50 kHz swing,
  1 kHz), the 99 % bandwidth, the hit set the same in the three files;
* the clustering the files are for: in the demodulated bits of a faded file the
  errors are runs (index of dispersion, mean run length) far more than in an AWGN
  file of the same error rate, and sit where ``fade_gain_db`` is low;
* ``n_samples`` against the length of the array and of the written file;
* mutants of the generator, each of which the checks must catch.
"""
import argparse
import contextlib
import ctypes
import importlib.util
import itertools
import json
import math
import os
import sys
import tempfile
import time

os.environ.setdefault('OMP_NUM_THREADS', '4')
import numpy as np  # noqa: E402
from scipy.special import j0  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from apps import bt_hop  # noqa: E402
from scripts import bt_ota_check, bt_synth_acl, bt_synth_fade as g  # noqa: E402
from scripts import test_bt_lmp as lmpt  # noqa: E402
from scripts import test_bt_synth_acl as t  # noqa: E402

FS = 40e6
SPS = 40
CENTER = 2441.0
SLOT = 25000
LEAD = 81                                   # samples a burst has before its first symbol (2 us ramp)
SEED = 9101
BLOCK = 2 ** 17
NOISE = t.NOISE
LAP, UAP = 0x112233, 0x55
MASK = sum(1 << c for c in range(31, 51))
SRC = open(g.__file__).read()
GAIN_TOL_DB = 0.02
FLOAT_ABS = 2e-6        # float32 precision of file - noise at these levels (values to 0.5, eps 6e-8)
RESULTS = []
QUIET = [False]
_lib = bt_ota_check.libbtbb()


def check(ok, what):
    ok = bool(ok)
    RESULTS.append((ok, what))
    if not QUIET[0]:
        print('  %s %s' % ('ok  ' if ok else 'FAIL', what))
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


_RUNS = {}


def run(kind='ref', snr=18.0, fd=None, k_db=None, sir=None, bursts=24, seed=SEED, mod=g):
    """A small file, kept: ``(iq, sidecar)``."""
    key = (kind, snr, fd, k_db, sir, bursts, seed, id(mod))
    if key not in _RUNS:
        _RUNS[key] = mod.synthesise_fade(kind, snr_db=snr, fd_hz=fd, k_db=k_db, sir_db=sir, bursts=bursts, seed=seed,
                                         block_samples=BLOCK, paired_with=None if kind == 'ref' else 'fade_ref_snr%d' % snr)
    return _RUNS[key]


# --- the packets, rebuilt -------------------------------------------------------------------

def my_white(register, n):
    """Part B 7.2: D^7 + D^4 + 1 from a 7-bit register, position 0 the low bit, the output taken from position 6."""
    s, out = register, []
    for _ in range(n):
        o = (s >> 6) & 1
        out.append(o)
        s = ((s << 1) & 0x7F) | o
        if o:
            s ^= 1 << 4
    return out


def my_xprc(clke, koffset, knudge, n):
    """EQ 6 (Core 2.6.4.4), CLKE4-2,0 read as bits 4, 3, 2, 0 most significant first."""
    c16 = (clke >> 12) & 0x1F
    low = ((clke >> 4) & 1) << 3 | ((clke >> 3) & 1) << 2 | ((clke >> 2) & 1) << 1 | (clke & 1)
    return (c16 + koffset + knudge + (low - c16) % 16 + n) % 32


def fhs_air(e):
    """An FHS burst's air bits and the payload before whitening, from the sidecar's fields and the layout of Figure 6.9
    (parity 34, LAP 24, EIR 1, undefined 1, SR 2, SP 2, UAP 8, NAP 16, class 24, AM_ADDR 3, CLK27-2 26, page scan mode
    3), the CRC, HEC and FEC written in the acl test, and the whitening register [X0..X4 1 1]."""
    f = e['fhs']
    p = []
    p += br.sync_word(f['lap'])[:34]
    p += t.lsb(f['lap'], 24) + t.lsb(f['eir'], 1) + t.lsb(f['reserved'], 1) + t.lsb(f['sr'], 2) + t.lsb(f['sp'], 2)
    p += t.lsb(f['uap'], 8) + t.lsb(f['nap'], 16) + t.lsb(f['class_of_device'], 24) + t.lsb(f['am_addr'], 3)
    p += t.lsb(f['clk27_2'], 26) + t.lsb(f['previously_used'], 3)
    assert len(p) == 144
    p += t.my_crc(p, e['uap'])
    ten = t.lsb(f['lt_addr_header'], 3) + t.lsb(0b0010, 4) + [e['flow'], e['arqn'], e['seqn']]
    header = ten + t.my_hec(ten, e['uap'])
    x = f['whitening_x']
    w = my_white(x | 0x60, len(header) + len(p))
    h = [a ^ b for a, b in zip(header, w)]
    q = [a ^ b for a, b in zip(p, w[len(header):])]
    return list(br.access_code(e['lap'])) + [b for b in h for _ in range(3)] + t.my_fec23(q), p, header


def btbb_fhs(bits, uap, tx_clk):
    """What libbtbb makes of an FHS: None if it refuses the header, else its payload verdict (1000 good) and what it reads."""
    vp = ctypes.c_void_p
    for n, r in (('lap_from_fhs', ctypes.c_uint32), ('uap_from_fhs', ctypes.c_uint8), ('nap_from_fhs', ctypes.c_uint16),
                 ('clock_from_fhs', ctypes.c_uint32)):
        fn = getattr(_lib, n)
        fn.restype, fn.argtypes = r, [vp]
    raw = bytes(int(b) for b in bits[4:366])
    pkt = _lib.btbb_packet_new()
    try:
        _lib.btbb_packet_set_data(pkt, ctypes.cast(ctypes.c_char_p(raw), ctypes.POINTER(ctypes.c_char)), len(raw), 0,
                                  ((tx_clk >> 1) & 0x3F) << 1)
        _lib.btbb_packet_set_uap(pkt, uap)
        _lib.btbb_packet_set_flag(pkt, 4, 1)
        _lib.btbb_packet_set_flag(pkt, 0, 1)
        if _lib.btbb_decode_header(pkt) != 1:
            return None
        out = {'verdict': _lib.btbb_decode_payload(pkt)}
        if out['verdict'] == 1000:
            out.update(lap=_lib.lap_from_fhs(pkt), uap=_lib.uap_from_fhs(pkt), nap=_lib.nap_from_fhs(pkt),
                       clk27_2=_lib.clock_from_fhs(pkt))
        return out
    finally:
        _lib.btbb_packet_unref(pkt)


def decode_ok(bits, e):
    """The burst's bits through libbtbb: True if the header and the whole payload are the sidecar's (FHS: its address and
    CLK27-2, verdict 1000)."""
    if e['ptype'] == 'FHS':
        f = e['fhs']
        d = btbb_fhs(bits, f['header_uap'], f['tx_clk'])
        return bool(d and d['verdict'] == 1000 and d['lap'] == f['lap'] and d['uap'] == f['uap'] and d['nap'] == f['nap']
                    and d['clk27_2'] == f['clk27_2'])
    return all(bt_ota_check.check_btbb(_lib, bits, e, e['uap']))


def check_packets(side, label, full=False):
    """Every burst's air_bits and body_crc_bits_hex rebuilt, the header fields, the keys, the LMP PDUs."""
    bursts = side['bursts']
    keys = set(side['per_burst_keys'])
    bad = {k: [] for k in ('keys', 'air', 'body', 'canon', 'len', 'hdr', 'llid', 'lmp')}
    for i, e in enumerate(bursts):
        if set(e) != keys:
            bad['keys'].append(i)
        if e['ptype'] == 'FHS':
            air, payload, header = fhs_air(e)
            fec = True
            if (e['llid'] is not None or e['lmp_name'] is not None or e['payload_length'] != 18
                    or e['payload_hex'] != e['payload_full_hex'][:36] or e['fhs'] is None
                    or e['lt_addr'] != 0 or e['flow'] or e['arqn'] or e['seqn']
                    or e['fhs']['whitening_x'] != my_xprc(e['fhs']['clke_frozen'], e['fhs']['koffset'], e['fhs']['knudge'], e['fhs']['N'])
                    or e['fhs']['lap'] != LAP or e['fhs']['uap'] != UAP or e['fhs']['nap'] != 0x1234
                    or e['fhs']['clk27_2'] != e['clk'] >> 2 or e['clk'] % 4 or e['uap'] != e['fhs']['header_uap']
                    or e['lap'] == LAP):
                bad['llid'].append(i)
        else:
            payload, header = t.derive_packet(e)
            air = t.whiten_and_code(header, payload, e['clk'], True, e['lap'])
            if e['fhs'] is not None or e['lap'] != LAP or e['uap'] != UAP or e['lt_addr'] != 1 or e['flow'] != 1 \
                    or e['arqn'] != 1 or e['seqn'] != i & 1:
                bad['hdr'].append(i)
            n = e['payload_length']
            is_lmp = e['lmp_name'] is not None
            if (e['llid'] != (3 if is_lmp else 2) or n != len(bytes.fromhex(e['payload_hex'])) or (e['ptype'] == 'DM1') != is_lmp
                    or not (1 <= n <= 17 if e['ptype'] == 'DM1' else t.L2MIN <= n <= t.LONGEST[e['ptype']])):
                bad['llid'].append(i)
            if is_lmp:
                try:
                    d = lmpt.decode(bytes.fromhex(e['payload_hex']))
                    shown = {k: (v.hex() if isinstance(v, bytes) else v) for k, v in d['fields'].items()}
                    if (d['name'] != e['lmp_name'] or d['opcode'] != e['lmp_opcode'] or d['tid'] != e['lmp_tid']
                            or lmpt.validate(d) or shown != e['lmp_params']):
                        bad['lmp'].append(i)
                except ValueError:
                    bad['lmp'].append(i)
        if t.hex_bits(e['air_bits'], e['air_bits_length']) != air:
            bad['air'].append(i)
        if t.hex_bits(e['body_crc_bits_hex'], e['body_crc_bits_length']) != payload:
            bad['body'].append(i)
        for hx, ln in (('air_bits', 'air_bits_length'), ('body_crc_bits_hex', 'body_crc_bits_length')):
            h, nn = e[hx], e[ln]
            pad = 8 * (len(h) // 2) - nn
            if len(h) != 2 * -(-nn // 8) or (int(h, 16) & ((1 << pad) - 1)):
                bad['canon'].append(i)
        want = 72 + 54 + (240 if e['ptype'] == 'FHS' else -(-len(payload) // 10) * 15)
        if e['air_bits_length'] != want or e['end_sample'] - e['start_sample'] != want * SPS or \
                e['header18'] != sum(b << j for j, b in enumerate(header)):
            bad['len'].append(i)
    what = {'keys': 'every burst has the keys of per_burst_keys', 'air': 'air_bits is the packet rebuilt from the specification\'s rules',
            'body': 'body_crc_bits_hex is the payload (and CRC) rebuilt', 'canon': 'air_bits and body_crc_bits_hex are canonical hex',
            'len': 'air_bits_length, end_sample and header18 follow the packet', 'hdr': 'a DM burst is LT_ADDR 1, FLOW 1, ARQN 1, SEQN alternating, the master\'s LAP and UAP, no fhs block',
            'llid': 'LLID, length and type rules (DM1: LMP; DM3, DM5: LLID 2 data; FHS: 18 bytes, the master\'s address and clock, X = EQ 6 of the recorded inputs)',
            'lmp': 'the LMP PDUs decode (table-driven decoder) to the sidecar\'s name, opcode, TID and parameters'}
    for k, idx in bad.items():
        check(not idx, '%s: %s (%d of %d wrong%s)' % (label, what[k], len(idx), len(bursts), ' - first %s' % idx[:3] if idx else ''))


def check_plan(side, label, bursts):
    """Counts, schedule, clocks, hops, symbol phases, the top-level facts."""
    b = side['bursts']
    n = bursts // 4
    types = [e['ptype'] for e in b]
    check(len(b) == bursts and all(types.count(x) == n for x in g.TYPES) and side['type_counts'] == {x: n for x in sorted(g.TYPES)}
          and side['equal_counts'] is True and 'same number' in side['equal_counts_statement'],
          '%s: %d bursts, %d each of DM1, DM3, DM5 and FHS, the equal counts stated' % (label, bursts, n))
    check(types != sorted(types), '%s: the order of the types is shuffled' % label)
    lmp = [e['lmp_name'] for e in b if e['ptype'] == 'DM1']
    counts = [lmp.count(x) for x in lmpt.SPEC]
    check(None not in lmp and (bursts < 204 or min(counts) >= 4), '%s: every DM1 is LMP%s (%d..%d of each of the eleven)'
          % (label, ', all eleven kinds at least four times' if bursts >= 204 else '', min(counts), max(counts)))
    ok_sched = ok_clk = ok_hop = ok_ph = True
    for i, e in enumerate(b):
        slots = {'DM1': 1, 'DM3': 3, 'DM5': 5, 'FHS': 1}[e['ptype']]
        ok_ph &= e['start_sample'] % SPS == (0, 20)[i % 2] and 0 <= e['timing_frac'] < 1 and e['symbol_phase'] == (0, 20)[i % 2]
        ok_clk &= e['clk'] % 4 == 0
        ok_hop &= e['channel'] == bt_hop.hop_channel(e['clk'], UAP << 24 | LAP, MASK) and e['channel_mhz'] == 2402 + e['channel'] \
            and e['slots'] == slots and 31 <= e['channel'] <= 50
        if i + 1 < len(b):
            d = (b[i + 1]['start_sample'] - b[i + 1]['symbol_phase']) - (e['start_sample'] - e['symbol_phase'])
            idle, rem = divmod(d // SLOT - slots - 1, 2)
            ok_sched &= d % SLOT == 0 and rem == 0 and 0 <= idle <= 2 and b[i + 1]['clk'] - e['clk'] == d // SLOT * 2
    check(ok_ph, '%s: symbol phases alternate 0, 20 and the timing fractions are in [0, 1)' % label)
    check(ok_sched, '%s: each burst starts slots + 1 + 2 * idle slots after the last, idle 0-2, the clock two ticks a slot' % label)
    check(ok_clk and ok_hop, '%s: every burst is on a master slot and on the adapted hop of its clock (map 31-50), bt_hop directly' % label)
    check(len({e['timing_frac'] for e in b}) == len(b), '%s: the timing fractions differ' % label)
    check(side['seed'] == SEED and side['snr_bw_hz'] == 1e6 and side['noise_1mhz'] == NOISE and side['sample_rate'] == FS
          and side['center_mhz'] == CENTER and side['timing_frac'] is None and side['symbol_phase'] is None
          and side['symbol_phases'] == [0, 20] and side['air_bits_omitted'] is False and side['lap'] == LAP and side['uap'] == UAP,
          '%s: seed 9101, the floor, rate, centre, symbol phases and master identity are the repository\'s' % label)


# --- the samples of a reference file ---------------------------------------------------------------

def demod(iq, e, search=30):
    """The genie receiver of bt_synth_acl.rx_burst (channel and start from the sidecar) on one burst: ``(bits, start)``."""
    return bt_synth_acl.rx_burst(iq, e, e['lap'], FS, CENTER, search=search)


def check_reference_samples(iq, side, label):
    """At high SNR: the burst count from the envelope, the carriers, the bits demodulated from the samples equal to air_bits,
    libbtbb, the start and the level."""
    bursts = side['bursts']
    check(len(iq) == side['n_samples'], '%s: n_samples %d is the length of the array' % (label, side['n_samples']))
    spans = t.rough_bursts(iq)
    check(len(spans) == len(bursts), '%s: the envelope shows %d bursts, the sidecar has %d' % (label, len(spans), len(bursts)))
    if len(spans) != len(bursts):
        return
    ok_ch = [t.carrier_of(iq, a, b)[1] == e['channel'] for (a, b), e in zip(spans, bursts)]
    check(all(ok_ch), '%s: every burst\'s carrier, from the spectrum of the raw samples, is its channel' % label)
    taps = bt_synth_acl.rx_taps(FS)
    ok_bits, ok_btbb, ok_wrong, starts, lv = [], [], [], [], []
    for e in bursts:
        bits, s0 = bt_synth_acl.rx_burst(iq, e, e['lap'], FS, CENTER, taps)
        want = t.hex_bits(e['air_bits'], e['air_bits_length'])
        ok_bits.append(list(bits) == want)
        ok_btbb.append(decode_ok(bits, e))
        if e['ptype'] == 'FHS':
            f = e['fhs']
            ok_wrong.append(btbb_fhs(bits, f['header_uap'] ^ 0x10, f['tx_clk']) is None)
        else:
            ok_wrong.append(not all(bt_ota_check.check_btbb(_lib, bits, e, e['uap'] ^ 0x10)))
        fit = t.fit_burst(iq, e, np.array(want), s0)
        starts.append(fit['start'] - (e['start_sample'] + e['timing_frac']))
        lv.append(10 * np.log10(fit['amp'] ** 2 / NOISE) - e['snr_db'])
    check(all(ok_bits), '%s: air_bits equal the bits demodulated from the samples, every burst (%d of %d)' % (label, sum(ok_bits), len(bursts)))
    check(all(ok_btbb), '%s: libbtbb decodes every burst (DM: header and payload exact; FHS: verdict 1000 and its LAP, UAP, NAP, CLK27-2) (%d of %d)'
          % (label, sum(ok_btbb), len(bursts)))
    check(all(ok_wrong), '%s: libbtbb refuses every burst at another UAP' % label)
    check(max(abs(x) for x in starts) <= 0.5 and abs(np.mean(starts)) < 0.1,
          '%s: start_sample + timing_frac is where the burst\'s model fits, worst %.3f samples, mean %+.3f' % (label, max(abs(x) for x in starts), np.mean(starts)))
    check(max(abs(x) for x in lv) < 0.3, '%s: the level from the samples is snr_db within 0.3 dB, worst %.3f' % (label, max(abs(x) for x in lv)))


# --- pairing ----------------------------------------------------------------------------------------

def noise_of(side):
    return t.my_noise(side['seed'], side['n_samples'], block=side['noise_block_samples'])


def burst_region(e):
    """The first sample and one past the last of a burst's array."""
    return e['start_sample'] - LEAD, e['start_sample'] + e['air_bits_length'] * SPS + LEAD


def check_paired_keys(side, ref, label):
    keys = ('clk', 'channel', 'ptype', 'payload_hex', 'air_bits', 'start_sample', 'timing_frac', 'symbol_phase', 'lmp_name', 'burst_phase', 'fhs')
    same = len(side['bursts']) == len(ref['bursts']) and all(
        all(x[k] == y[k] for k in keys) for x, y in zip(side['bursts'], ref['bursts']))
    check(same and side['seed'] == ref['seed'] and side['seed'] in (SEED, 9102) and side['n_samples'] == ref['n_samples'],
          '%s: bursts, channels, payloads, clocks, timing fractions, phases, FHS fields and seed are the reference\'s' % label)


# --- the fade ----------------------------------------------------------------------------------------

def run_length(deep):
    best = 0
    for v, grp in itertools.groupby(deep):
        if v:
            best = max(best, len(list(grp)))
    return best


def check_fade_samples(iq, side, ref_iq, ref_side, label, fn=g.fade_h, strict=True):
    """The fade from the samples: exact pairing off the bursts, h = (file - noise) / (reference - noise) against the sidecar's
    gains at every symbol, against ``fn`` at every sample, the truth keys against an independent recomputation, and the
    Doppler."""
    check_paired_keys(side, ref_side, label)
    check(len(iq) == len(ref_iq) == side['n_samples'], '%s: n_samples and length' % label)
    if not len(iq) == len(ref_iq) == side['n_samples']:
        return np.zeros(0, complex)
    noise = noise_of(side)
    inside = np.zeros(len(iq), bool)
    for e in side['bursts']:
        a, b = burst_region(e)
        inside[a:b] = True
    check(np.array_equal(iq[~inside], ref_iq[~inside]) and np.array_equal(iq[~inside], noise[~inside]),
          '%s: off the bursts the file is the reference, which is the regenerated noise, exactly (%d samples)' % (label, (~inside).sum()))
    worst_gain, worst_h, worst_deep = 0.0, 0.0, 0.0
    bad_list, bad_truth, bad_eff, rho_num, rho_den, hm_all = [], [], [], 0.0, 0.0, []
    fd = side['fade']['fd_hz']
    delta = min(1 / (8 * fd), 100e-6)
    d = int(round(delta * FS))
    for k, e in enumerate(side['bursts']):
        a, b = burst_region(e)
        sf = iq[a:b].astype(np.complex128) - noise[a:b]
        sr = ref_iq[a:b].astype(np.complex128) - noise[a:b]
        amp = math.sqrt(NOISE * 10 ** (e['snr_db'] / 10))
        plateau = np.abs(sr) > 0.99 * amp
        hm = np.where(plateau, sf / np.where(plateau, sr, 1), 0)
        # h at every sample against the function of the burst's own process
        n = np.arange(a, b)
        ht = fn(side['seed'], k, e['fade_fd_hz'], (n - e['start_sample']) / FS, e['fade_k_db'])
        # the complex residual on EVERY sample of the burst's array, ramps and deep nulls included: file - noise = h * (reference - noise)
        worst_h = max(worst_h, float(np.abs(sf - ht * sr).max()))
        centres = e['start_sample'] - a + np.arange(e['air_bits_length']) * SPS + SPS // 2
        gl = np.array(e['fade_gain_db'])
        if len(gl) != e['air_bits_length']:
            bad_list.append(k)
            continue
        # every symbol, in amplitude (stays accurate in nulls): | |file - noise| - 10^(gain/20) |reference - noise| | against the
        # listed gain's rounding and tolerance (0.02 dB) plus 2e-6, the float32 precision of the subtraction
        want = 10 ** (gl / 20) * np.abs(sr[centres])
        err = np.abs(np.abs(sf[centres]) - want) - want * (10 ** (GAIN_TOL_DB / 20) - 1)
        worst_gain = max(worst_gain, float(err.max()))
        gmin, frac, run = e['fade_min_db'], e['fade_frac_below_10db'], e['fade_longest_run_below_10db']
        bad_truth += [k] if (gmin != gl.min() or abs(frac - np.mean(gl < -10)) > 1e-12 or run != run_length(gl < -10)) else []
        eff = e['snr_db'] + 10 * np.log10(np.mean(10 ** (gl / 10)))
        inner = np.arange(a + 100, b - 100)
        eff_samples = e['snr_db'] + 10 * np.log10(np.mean(np.abs(sf[inner - a]) ** 2) / np.mean(np.abs(sr[inner - a]) ** 2))
        bad_eff += [k] if abs(e['snr_eff_db'] - eff) > 0.01 or abs(e['snr_eff_db'] - eff_samples) > 0.05 else []
        h_in = hm[100:-100]
        if len(h_in) > 2 * d:
            rho_num += float(np.sum(np.abs(h_in[d:] - h_in[:-d]) ** 2))
            rho_den += float(np.sum(np.abs(h_in[:-d]) ** 2) + np.sum(np.abs(h_in[d:]) ** 2))
        hm_all.append(hm[centres])
    check(not bad_list, '%s: fade_gain_db has one entry per symbol of the air bits, access code included' % label)
    check(worst_gain <= FLOAT_ABS, '%s: |h| from the samples is fade_gain_db within %.2f dB at EVERY symbol, deep nulls included (excess over the bound, worst %.1e, limit %.0e)' % (label, GAIN_TOL_DB, worst_gain, FLOAT_ABS))
    check(worst_h < FLOAT_ABS, '%s: (file - noise) - fade_h * (reference - noise) is zero on EVERY sample of every burst, ramps included, worst |diff| %.2e (limit %.0e)' % (label, worst_h, FLOAT_ABS))
    check(not bad_truth, '%s: fade_min_db, fade_frac_below_10db and fade_longest_run_below_10db are the lists\' (independently recomputed)' % label)
    check(not bad_eff, '%s: snr_eff_db is snr_db + 10 log10 mean |h|^2, from the list (0.01 dB) and from the samples (0.05 dB)' % label)
    one_minus_rho = rho_num / rho_den             # sum |h(t+tau) - h(t)|^2 / (sum |h(t)|^2 + sum |h(t+tau)|^2) = 1 - rho(tau)
    theory = 1 - j0(2 * math.pi * fd * delta)
    check(0.6 < one_minus_rho / theory < 1.5, '%s: Doppler from the samples: 1 - rho(%.0f us) = %.4g against 1 - J0(2 pi fd tau) = %.4g (ratio %.2f, limit 0.6-1.5)'
          % (label, delta * 1e6, one_minus_rho, theory, one_minus_rho / theory))
    return np.concatenate(hm_all)


def kmoment(p):
    """The Rician K (linear) from the 2nd and 4th moments of |h|^2 of a unit-power process: m4 = (K^2 + 4K + 2) / (K + 1)^2."""
    m4 = np.mean(p ** 2) / np.mean(p) ** 2
    a, b, c = m4 - 1, 2 * m4 - 4, m4 - 2
    return max((-b + math.sqrt(max(b * b - 4 * a * c, 0))) / (2 * a), 1e-3)


def check_fade_function(mod, label, n=4000):
    """The statistics of the process over thousands of independent seeds."""
    h = lambda k, fd, tt, kdb=None: mod.fade_h(SEED, k, fd, tt, kdb)       # noqa: E731
    for fd in (100.0, 800.0):
        tt = np.linspace(0, 0.01, 12)
        p = np.concatenate([np.abs(h(k, fd, tt)) ** 2 for k in range(n)])
        sd = p.std() / math.sqrt(n * 3)
        check(abs(p.mean() - 1) < 0.03, '%s: Rayleigh E|h|^2 = %.4f at fd %g (limit 3 %%; about %.1f %% standard error)' % (label, p.mean(), fd, 100 * sd))
    kk = 10 ** 0.6
    p = np.concatenate([np.abs(h(k, 300.0, np.linspace(0, 0.01, 12), 6.0)) ** 2 for k in range(n)])
    check(abs(p.mean() - 1) < 0.03, '%s: Rician E|h|^2 = %.4f' % (label, p.mean()))
    kest = 10 * math.log10(kmoment(p))
    check(abs(kest - 6.0) < 0.6, '%s: Rician K from the 4th moment of |h|^2 over %d processes is %.2f dB (6.00 +- 0.6)' % (label, n, kest))
    rng = np.random.default_rng(3)
    for fd in (100.0, 300.0, 800.0):
        t0 = rng.uniform(0, 0.02, n)
        for frac, label_lag in ((0.5, '1/(2 fd)'), (0.25, '1/(4 fd)')):
            tau = frac / fd
            num = den = 0.0
            for k in range(n):
                v = h(k, fd, np.array([t0[k], t0[k] + tau]))
                num += float(np.real(v[0] * np.conj(v[1])))
                den += float(abs(v[0]) ** 2)
            want = float(j0(2 * math.pi * fd * tau))
            check(abs(num / den - want) < 0.06, '%s: autocorrelation at lag %s, fd %g: %.3f against J0 = %.3f' % (label, label_lag, fd, num / den, want))
    for fd in (100.0, 800.0):
        dur = 0.4 if fd == 100 else 0.05
        tt = np.arange(0, dur, 1 / (40 * fd))
        procs = [np.abs(h(10000 + k, fd, tt)) for k in range(150)]
        total = 150 * dur
        for rho in (0.3, 0.5, 1.0):
            crossings = sum(int(np.sum((r[:-1] < rho) & (r[1:] >= rho))) for r in procs)
            below = np.mean([np.mean(r < rho) for r in procs])
            lcr = crossings / total
            theory = math.sqrt(2 * math.pi) * fd * rho * math.exp(-rho ** 2)
            afd = below / lcr
            afd_t = (math.exp(rho ** 2) - 1) / (rho * fd * math.sqrt(2 * math.pi))
            check(abs(lcr / theory - 1) < 0.15 and abs(afd / afd_t - 1) < 0.15,
                  '%s: fd %g, rho %.1f: level crossing %.1f/s against %.1f (%d crossings), average fade %.3g ms against %.3g ms'
                  % (label, fd, rho, lcr, theory, crossings, afd * 1e3, afd_t * 1e3))
    a = np.array([h(k, 300.0, np.array([0.0]))[0] for k in range(6000)])
    b = np.array([h(k + 1, 300.0, np.array([0.0]))[0] for k in range(6000)])
    c = abs(np.mean(a * np.conj(b))) / math.sqrt(np.mean(abs(a) ** 2) * np.mean(abs(b) ** 2))
    check(c < 0.05, '%s: the processes of consecutive bursts are independent: |corr| = %.3f over 6000 pairs (limit 0.05)' % (label, c))
    same = np.array([h(k, 300.0, np.array([0.0]))[0] for k in range(6000)])
    check(np.array_equal(a, same), '%s: a burst\'s process is a function of (seed, burst) alone: the same twice' % label)
    # the draws are shared by every fading file: only the time axis differs
    x1 = h(5, 100.0, np.array([0.003]))[0]
    x3 = h(5, 300.0, np.array([0.001]))[0]
    check(abs(x1 - x3) < 1e-9, '%s: fd only scales the time axis: h(fd 100, 3 ms) = h(fd 300, 1 ms)' % label)


def check_k_samples(hm, label, k_db):
    p = np.abs(hm) ** 2
    est = 10 * math.log10(kmoment(p))
    check(abs(est - k_db) < 3.0, '%s: Rician K from the samples\' symbol-centre gains (%d of them, a few independent fades each) is %.1f dB against %g (limit 3: few fades)'
          % (label, len(p), est, k_db))


# --- the interferer ----------------------------------------------------------------------------------

def nb_measure(iq, ref_iq, side, k):
    """From the samples of file minus reference: the interferer of burst k, as ``(start symbol, end symbol, power ratio dB,
    d, region start)`` or None when there is none."""
    e = side['bursts'][k]
    a, b = burst_region(e)
    d = iq[a:b].astype(np.complex128) - ref_iq[a:b].astype(np.complex128)
    if not np.any(d):
        return None
    env = np.abs(d)
    top = env.max()
    above = np.flatnonzero(env > top / 2)
    x0 = a + above[0] - e['start_sample'] - e['timing_frac']
    x1 = a + above[-1] + 1 - e['start_sample'] - e['timing_frac']
    return x0 / SPS, x1 / SPS, d, a


def check_nb(iq, side, ref_iq, ref_side, label):
    """The interferer against the samples; returns {burst: (start, end)} measured."""
    check_paired_keys(side, ref_side, label)
    diff = iq.astype(np.complex128) - ref_iq.astype(np.complex128)
    support = np.zeros(len(iq), bool)
    hits = [k for k, e in enumerate(side['bursts']) if e['nb_hit']]
    for k in hits:
        e = side['bursts'][k]
        support[e['start_sample'] + e['nb_start_symbol'] * SPS - 45:e['start_sample'] + e['nb_end_symbol'] * SPS + 45] = True
    check(np.all(diff[~support] == 0), '%s: file minus reference is exactly zero off the hit bursts\' intervals (%d samples, %d nonzero outside)'
          % (label, (~support).sum(), int(np.count_nonzero(diff[~support]))))
    nb = side['interferer']
    n_hit = len(hits)
    per_type = {x: sum(1 for e in side['bursts'] if e['nb_hit'] and e['ptype'] == x) for x in g.TYPES}
    check(n_hit == len(side['bursts']) // 2 and max(per_type.values()) - min(per_type.values()) <= 1 and per_type == nb['nb_hit_counts_by_type']
          and not any(e['nb_hit'] is None for e in side['bursts']),
          '%s: %d of %d bursts hit, the types %s (equal to within the remainder, as the sidecar states)' % (label, n_hit, len(side['bursts']), per_type))
    measured, bad_iv, bad_sir, bad_f, bad_w, bad_in = {}, [], [], [], [], []
    for k in range(len(side['bursts'])):
        e = side['bursts'][k]
        m = nb_measure(iq, ref_iq, side, k)
        if not e['nb_hit']:
            if m is not None or any(e[x] is not None for x in ('nb_start_symbol', 'nb_end_symbol', 'nb_offset_khz', 'nb_sir_db', 'nb_in_band')):
                bad_iv.append(k)
            continue
        if m is None:
            bad_iv.append(k)
            continue
        s0, s1, d, a = m
        measured[k] = (s0, s1)
        share = (e['nb_end_symbol'] - e['nb_start_symbol']) / e['air_bits_length']
        if not (abs(s0 - e['nb_start_symbol']) <= 0.05 and abs(s1 - e['nb_end_symbol']) <= 0.05 and 0.09 <= share <= 0.61
                and 0 <= e['nb_start_symbol'] and e['nb_end_symbol'] <= e['air_bits_length']):
            bad_iv.append(k)
        # the power against the burst's, on the plateau (away from the 2 us edges)
        lo = int(e['start_sample'] + e['timing_frac'] + e['nb_start_symbol'] * SPS) + 80 - a
        hi = int(e['start_sample'] + e['timing_frac'] + e['nb_end_symbol'] * SPS) - 80 - a
        amp2 = NOISE * 10 ** (e['snr_db'] / 10)
        sir = 10 * np.log10(amp2 / np.mean(np.abs(d[lo:hi]) ** 2))
        if abs(sir - e['nb_sir_db']) > 0.1 or not e['nb_in_band'] or abs(e['nb_offset_khz']) > 300.0:
            bad_sir.append((k, round(float(sir), 2)))
        # the instantaneous frequency: centre, swing and the 1 kHz
        f = np.angle(d[lo + 1:hi] * np.conj(d[lo:hi - 1])) * FS / (2 * np.pi)
        f_c = (e['channel_mhz'] - CENTER) * 1e6 + e['nb_offset_khz'] * 1e3
        tt = np.arange(lo + 1, hi) / FS
        if len(f) > 0.5e-3 * FS:
            basis = np.stack([np.cos(2 * np.pi * 1e3 * tt), np.sin(2 * np.pi * 1e3 * tt), np.ones_like(tt)], 1)
            coef, *_ = np.linalg.lstsq(basis, f, rcond=None)
            resid = f - basis @ coef
            if not (abs(np.hypot(coef[0], coef[1]) - 50e3) < 2e3 and abs(coef[2] - f_c) < 2e3 and resid.std() < 2e3):
                bad_f.append((k, round(float(np.hypot(coef[0], coef[1])), 0), round(float(coef[2] - f_c), 0)))
        elif np.abs(f - f_c).max() > 52e3:
            bad_f.append((k, 'short'))
    check(not bad_iv, '%s: start and end symbol are where the envelope of file - reference crosses half its amplitude (0.05 symbol), 10-60 %% of the symbols, none off a miss (%d wrong %s)' % (label, len(bad_iv), bad_iv[:4]))
    check(not bad_sir, '%s: the SIR from the samples (burst power over the plateau\'s) is nb_sir_db within 0.1 dB, the offset within 300 kHz, nb_in_band true (%s)' % (label, bad_sir[:3]))
    check(not bad_f, '%s: from the instantaneous frequency of each interval over 0.5 ms (1 kHz fit): centre = channel + nb_offset_khz within 2 kHz, swing +-50 kHz within 2 kHz, rms residual under 2 kHz (%s)' % (label, bad_f[:3]))
    longest = max(hits, key=lambda k: side['bursts'][k]['nb_end_symbol'] - side['bursts'][k]['nb_start_symbol'])
    e = side['bursts'][longest]
    d = nb_measure(iq, ref_iq, side, longest)[2]
    lo = int(e['start_sample'] + e['timing_frac'] + e['nb_start_symbol'] * SPS) - burst_region(e)[0]
    hi = lo + (e['nb_end_symbol'] - e['nb_start_symbol']) * SPS
    seg = d[lo:hi] * np.hanning(hi - lo)
    spec = np.abs(np.fft.fftshift(np.fft.fft(seg, 1 << 18))) ** 2
    freq = np.fft.fftshift(np.fft.fftfreq(1 << 18, 1 / FS))
    cum = np.cumsum(spec) / spec.sum()
    lo99, hi99 = freq[np.searchsorted(cum, 0.005)], freq[np.searchsorted(cum, 0.995)]
    f_c = (e['channel_mhz'] - CENTER) * 1e6 + e['nb_offset_khz'] * 1e3
    check(80e3 < hi99 - lo99 < 125e3 and abs((hi99 + lo99) / 2 - f_c) < 4e3,
          '%s: the longest interval (burst %d, %d symbols) has 99 %% of its power in %.0f kHz centred %.1f kHz from the stated centre (Carson: 102 kHz)'
          % (label, longest, (hi - lo) // SPS, (hi99 - lo99) / 1e3, ((hi99 + lo99) / 2 - f_c) / 1e3))
    return measured


# --- clustering ----------------------------------------------------------------------------------------

def error_stats(iq, side, search=30):
    """Over every burst: error flags of the genie receiver's bits against air_bits. Returns per burst the 0/1 error arrays."""
    taps = bt_synth_acl.rx_taps(FS)
    out = []
    for e in side['bursts']:
        bits, _ = bt_synth_acl.rx_burst(iq, e, e['lap'], FS, CENTER, taps, search=search)
        want = np.array(t.hex_bits(e['air_bits'], e['air_bits_length']))
        out.append(None if bits is None else (bits != want).astype(np.int8))
    return out


def fano(errors, window=32):
    """Pooled index of dispersion of the error count in windows of ``window`` symbols (variance over mean; 1 for independent
    errors, under 1 when they are rare), and the mean run length of consecutive errors."""
    counts, runs = [], []
    for e in errors:
        if e is None:
            continue
        m = len(e) // window
        counts.append(e[:m * window].reshape(m, window).sum(1))
        runs += [len(list(gr)) for v, gr in itertools.groupby(e) if v]
    c = np.concatenate(counts)
    return float(c.var() / c.mean()) if c.mean() > 0 else float('nan'), float(np.mean(runs)) if runs else 0.0, \
        float(np.mean(np.concatenate([e for e in errors if e is not None])))


def check_clustering(label, kind, fd, k_db=None, snr=18.0, bursts=24):
    iq, side = run(kind, snr, fd, k_db, bursts=bursts)
    err = error_stats(iq, side)
    f_idx, f_run, f_ber = fano(err)
    # an AWGN file of about the same error rate: the SNR from a short scan
    best = None
    for s in (8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0):
        r_iq, r_side = run('ref', s, bursts=bursts)
        a_idx, a_run, a_ber = fano(error_stats(r_iq, r_side))
        if best is None or abs(math.log((a_ber + 1e-6) / (f_ber + 1e-6))) < abs(math.log((best[3] + 1e-6) / (f_ber + 1e-6))):
            best = (s, a_idx, a_run, a_ber)
    s, a_idx, a_run, a_ber = best
    check(f_ber > 0 and f_idx > 2 * a_idx and f_idx > 1.5 and f_run > a_run,
          '%s: errors are clustered: bit error rate %.4f, index of dispersion (32-symbol windows) %.1f, mean run %.2f; AWGN at %g dB (rate %.4f): %.1f, %.2f'
          % (label, f_ber, f_idx, f_run, s, a_ber, a_idx, a_run))
    # the errors sit in the fade
    in_fade = tot = err_fade = err_all = 0
    for e, bad in zip(side['bursts'], err):
        if bad is None:
            continue
        deep = np.array(e['fade_gain_db']) < -6
        in_fade += int(deep.sum())
        tot += len(deep)
        err_fade += int((bad.astype(bool) & deep).sum())
        err_all += int(bad.sum())
    check(err_all > 0 and err_fade / err_all > 0.6 and in_fade / tot < 0.3,
          '%s: %.0f %% of the errors are in the symbols the sidecar puts under -6 dB, which are %.0f %% of all symbols' % (label, 100 * err_fade / max(err_all, 1), 100 * in_fade / tot))


# --- the files ------------------------------------------------------------------------------------------

def check_file_size():
    with tempfile.TemporaryDirectory() as d:
        iq_path, side_path = g.write_file('fade_test', d, kind='rayleigh', snr_db=18.0, fd_hz=300.0, bursts=8,
                                          paired_with='fade_ref_snr18')
        side = json.load(open(side_path))
        check(os.path.getsize(iq_path) == 8 * side['n_samples'], 'a written file: its size %d bytes is 8 * n_samples = %d'
              % (os.path.getsize(iq_path), 8 * side['n_samples']))
        back = np.fromfile(iq_path, dtype='<c8')
        iq, _ = g.synthesise_fade('rayleigh', 18.0, 300.0, bursts=8)
        check(np.array_equal(back, iq) and side['paired_with'] == 'fade_ref_snr18' and side['bursts'][0]['air_bits'],
              'the written samples are the synthesised ones and the sidecar says what it is paired with')


def check_top(side, label, kind, snr):
    need = ['generator', 'generator_commit', 'lap', 'uap', 'sample_rate', 'center_mhz', 'modulation', 'slot_samples', 'start_sample_meaning',
            'clk_convention', 'snr_bw_hz', 'noise_1mhz', 'bursts', 'symbol_phases', 'per_burst_keys', 'n_samples', 'type_counts',
            'equal_counts_statement', 'air_bits_omitted', 'distortion', 'paired_with', 'notes', 'seed', 'snr_db']
    miss = [k for k in need if k not in side]
    check(not miss, '%s: the sidecar has every top-level key (%s)' % (label, miss))
    per = ['ptype', 'lap', 'uap', 'channel', 'start_sample', 'timing_frac', 'clk', 'snr_db', 'llid', 'payload_length', 'payload_hex',
           'lmp_opcode', 'lmp_name', 'lmp_tid', 'lmp_params', 'fhs', 'air_bits', 'air_bits_length', 'body_crc_bits_hex', 'fade_kind',
           'fade_fd_hz', 'fade_k_db', 'fade_gain_db', 'fade_min_db', 'fade_frac_below_10db', 'fade_longest_run_below_10db', 'snr_eff_db',
           'nb_hit', 'nb_start_symbol', 'nb_end_symbol', 'nb_offset_khz', 'nb_sir_db', 'nb_in_band']
    miss = [k for k in per if k not in side['per_burst_keys']]
    check(not miss, '%s: per_burst_keys has every per-burst truth key (%s)' % (label, miss))
    check(side['snr_db'] == snr and side['distortion_kind'] == kind and (kind == 'ref') == (side['paired_with'] is None)
          and side['paired_with'] in (None, 'fade_ref_snr%d' % snr), '%s: snr, kind and the reference it is paired with are stated' % label)
    check(any('NOT a measured channel' in n or 'NOT' in n for n in side['notes']) and any('invented' in n for n in side['notes']),
          '%s: the notes say the fade is a model and the interferer invented' % label)


# --- mutants ------------------------------------------------------------------------------------------------

def mutant_module(old, new):
    """The generator with one text replaced once, as a module of its own."""
    assert SRC.count(old) == 1, 'mutation target not unique: %r (%d)' % (old, SRC.count(old))
    spec = importlib.util.spec_from_file_location('bt_synth_fade_mut', g.__file__)
    mod = importlib.util.module_from_spec(spec)
    exec(compile(SRC.replace(old, new), g.__file__, 'exec'), mod.__dict__)
    return mod


def fade_checks(mod, kind, fd, k_db, label, function=True):
    """What a reader would check of a fading file made by ``mod``: the samples against the sidecar and against the true
    function of the original generator, and the statistics of the module's own process."""
    iq, side = run(kind, 18.0, fd, k_db, mod=mod)
    ref_iq, ref_side = run('ref', 18.0, mod=mod)
    check_fade_samples(iq, side, ref_iq, ref_side, label)
    if function:
        check_fade_function(mod, label, n=1500)


def nb_checks(mod, label, sir=6.0):
    iq, side = run('nb', 24.0, sir=sir, mod=mod)
    ref_iq, ref_side = run('ref', 24.0, mod=mod)
    check_nb(iq, side, ref_iq, ref_side, label)


def caught(name, failed):
    short = sorted({w.split(':')[0][:30] + ':' + w.split(':')[-1][:55] for w in failed})
    check(len(failed) > 0, 'mutant "%s" is caught by %d checks, e.g. %s' % (name, len(failed), '; '.join(short[:2])))


def check_mutants():
    print('\nMutants: one mistake each, and what catches it')
    cases = [
        ('the fade applied to the noise too', 'fade', ("iq[lo:lo + len(sig)] += sig",
         "iq[lo:lo + len(sig)] = (iq[lo:lo + len(sig)] + amp * burst) * (1 if h is None else h)")),
        ('E|h|^2 = 2', 'fade', ("scale = 1.0 / math.sqrt(FADE_OSC)", "scale = math.sqrt(2.0 / FADE_OSC)")),
        ('the gain list one symbol shifted', 'fade', ("centres = start - lo + np.arange(nbits) * sps + sps // 2",
         "centres = start - lo + np.arange(1, nbits + 1) * sps + sps // 2")),
        ('the Doppler wrong by a factor of 2', 'fade', ("w = 2 * np.pi * fd_hz", "w = 2 * np.pi * 2 * fd_hz")),
        ('the NB interval shifted by 5 symbols', 'nb', ("n0, itf = interferer(draws[k], sir_db, amp, start, b['timing_frac'], sps, nbits, interval,",
         "n0, itf = interferer(draws[k], sir_db, amp, start, b['timing_frac'], sps, nbits, (interval[0] + 5, interval[1] + 5),")),
        ('the interferer 3 dB off', 'nb', ("amp_i = amp * 10 ** (-sir_db / 20)", "amp_i = amp * 10 ** (-(sir_db + 3) / 20)")),
        ('every gain below -30 dB reported 20 dB lower', 'fade', ("gain_db = np.round(20 * np.log10(np.maximum(np.abs(h[centres]), 1e-12)), 2)",
         "gain_db = np.round(20 * np.log10(np.maximum(np.abs(h[centres]), 1e-12)), 2)\n            gain_db = np.where(gain_db < -30, gain_db - 20, gain_db)")),
        ('h doubled only on the ramps (n < start - 20 or n > end + 20)', 'fade', ("sig = (amp * burst * (1 if h is None else h)).astype(np.complex64)",
         "sig = (amp * burst * (1 if h is None else h * (1 + ((n < start - 20) | (n > start + nbits * sps + 20))))).astype(np.complex64)")),
        ('the gain sampled one sample late (sps // 2 + 1)', 'fade', ("centres = start - lo + np.arange(nbits) * sps + sps // 2",
         "centres = start - lo + np.arange(nbits) * sps + sps // 2 + 1")),
        ('n_samples off by one', 'size', ("'n_samples': int(total),", "'n_samples': int(total) + 1,")),
    ]
    for name, family, (old, new) in cases:
        mod = mutant_module(old, new)
        with quiet() as failed:
            try:
                if family == 'fade':
                    fade_checks(mod, 'rayleigh', 800.0, None, 'mutant')
                elif family == 'nb':
                    nb_checks(mod, 'mutant')
                else:
                    iq, side = run('rayleigh', 18.0, 300.0, mod=mod)
                    check(len(iq) == side['n_samples'], 'mutant: n_samples is the length of the array')
            except Exception as ex:                       # a crash is a catch too, said so
                failed.append('raised %s: %s' % (type(ex).__name__, str(ex)[:60]))
        caught(name, failed)
    # NB hit sets that differ between the files: the cross-file comparison of the hits
    mod = mutant_module("plan_hits(plan, seed) if kind == 'nb'", "plan_hits(plan, seed + int(sir_db)) if kind == 'nb'")
    with quiet() as failed:
        a, b = run('nb', 24.0, sir=0.0, mod=mod)[1], run('nb', 24.0, sir=6.0, mod=mod)[1]
        check_hit_sets([a, b], 'mutant')
    caught('different NB hit sets in the three files', failed)
    # a different seed for one fade file: the pairing (noise) must fail
    with quiet() as failed:
        iq, side = run('rayleigh', 18.0, 300.0, seed=9102)
        ref_iq, ref_side = run('ref', 18.0)
        check_fade_samples(iq, side, ref_iq, ref_side, 'mutant')
        check_paired_keys(side, ref_side, 'mutant')
    caught('a different seed for one fade file', failed)
    # and the unmutated files pass the same checks
    with quiet() as failed:
        fade_checks(g, 'rayleigh', 800.0, None, 'control', function=False)
        nb_checks(g, 'control')
    check(not failed, 'the unmutated generator passes the same checks (positive control)%s' % (' - failed: %s' % failed[:2] if failed else ''))


def check_hit_sets(sides, label):
    keys = ('nb_hit', 'nb_start_symbol', 'nb_end_symbol', 'nb_offset_khz')
    same = all(all(x[k] == y[k] for k in keys) for a, b in zip(sides, sides[1:]) for x, y in zip(a['bursts'], b['bursts']))
    differ = all(x['nb_sir_db'] != y['nb_sir_db'] for a, b in zip(sides, sides[1:]) for x, y in zip(a['bursts'], b['bursts']) if x['nb_hit'])
    check(same and differ, '%s: the files share the hit set, intervals and offsets burst by burst; only nb_sir_db differs' % label)


# --- the tests -----------------------------------------------------------------------------------------------

def check_second_seed():
    """``--set fade2``: the same 13 distortions at seed 9102, every name ending ``_s2``, independent of seed 9101."""
    print('\nThe second seed (--set fade2)')
    one, two = g.SETS['fade'](), g.SETS['fade2']()
    check(len(two) == len(one) == 13 and all(n.endswith('_s2') for n, _ in two) and not any(n.endswith('_s2') for n, _ in one),
          'fade2: 13 files, every name ends _s2; the fade set keeps its names')
    check(all(k['seed'] == 9102 for _, k in two) and all(k['seed'] == 9101 for _, k in one),
          'fade2 is seed 9102 and fade is seed 9101, every file')
    names = {n for n, _ in two}
    check(all(k['paired_with'] is None or k['paired_with'] in names for _, k in two)
          and all(k['paired_with'] is None or k['paired_with'] in {n for n, _ in one} for _, k in one),
          'every paired_with names a reference of its own set')
    sa = run('rayleigh', 18.0, 300.0, seed=9101)[1]
    sb = run('rayleigh', 18.0, 300.0, seed=9102)[1]
    same_ch = sum(x['channel'] == y['channel'] for x, y in zip(sa['bursts'], sb['bursts']))
    same_pl = sum(x['payload_hex'] == y['payload_hex'] for x, y in zip(sa['bursts'], sb['bursts']))
    same_fade = sum(x['fade_gain_db'] == y['fade_gain_db'] for x, y in zip(sa['bursts'], sb['bursts']))
    check(sb['seed'] == 9102 and same_ch < 12 and same_pl < 6 and same_fade == 0 and sa['bursts'][0]['timing_frac'] != sb['bursts'][0]['timing_frac'],
          'seed 9102 is independent of 9101: channels equal in %d of 24, payloads in %d, fade lists in %d, timing differs' % (same_ch, same_pl, same_fade))
    # every random stream of the plan, compared across the seeds (a stream that ignores the seed would pass the checks
    # above): the hit set, the narrowband draws and the FHS fields
    pa, pb = g.plan_fade(seed=9101)[0], g.plan_fade(seed=9102)[0]
    ha, da, _ = g.plan_hits(pa, 9101)
    hb, db, _ = g.plan_hits(pb, 9102)
    same_hit = sum(x == y for x, y in zip(ha, hb))
    same_off = sum(x['offset_khz'] == y['offset_khz'] for x, y in zip(da, db))
    same_mod = sum(x['mod_phase'] == y['mod_phase'] for x, y in zip(da, db))
    same_share = sum(x['share'] == y['share'] for x, y in zip(da, db))
    check(same_off == 0 and same_mod == 0 and same_share == 0 and same_hit < 140,
          'seeds 9101 and 9102: the narrowband draws are independent (offsets equal in %d, modulation phases in %d, shares in %d, '
          'of %d bursts; hit membership agrees in %d of %d, chance 102)' % (same_off, same_mod, same_share, len(pa), same_hit, len(pa)))
    fa = [e['fhs'] for e in sa['bursts'] if e.get('fhs')]
    fb = [e['fhs'] for e in sb['bursts'] if e.get('fhs')]
    check(fa and fb and not any(x['access_lap'] == y['access_lap'] for x in fa for y in fb) and not any(
        x['whitening_x'] == y['whitening_x'] and x['clk27_2'] == y['clk27_2'] for x in fa for y in fb),
          'seeds 9101 and 9102: the FHS fields are independent (%d and %d FHS bursts, no paged LAP in common)' % (len(fa), len(fb)))
    fuse = sum(1 for x, y in zip(sa['bursts'], sb['bursts']) if x['fade_gain_db'][:20] == y['fade_gain_db'][:20])
    check(fuse == 0, 'seeds 9101 and 9102: no burst has the same first 20 fade gains, whatever its length')
    ref2 = run('ref', 18.0, seed=9102)
    iq, side = run('rayleigh', 18.0, 300.0, seed=9102)
    check_fade_samples(iq, side, *ref2, 'ray fd300 snr18 at seed 9102')
    nz = noise_of(ref2[1])
    free = np.ones(len(nz), bool)
    for e in ref2[1]['bursts']:
        free[burst_region(e)[0]:burst_region(e)[1]] = False
    check(np.array_equal(ref2[0][free], nz[free]) and ref2[1]['seed'] == 9102,
          'seed 9102 reference: off the bursts it is the regenerated noise of default_rng([9102, 1, block])')
    bad = run('rayleigh', 18.0, 300.0, seed=9101)[1]
    check(sum(x['channel'] == y['channel'] for x, y in zip(sa['bursts'], bad['bursts'])) == 24,
          'mutant: a "second" set run at seed 9101 is not independent, and the check above would see it')


def tests():
    t0 = time.time()
    print('The full-size plan (204 bursts), sidecar only\n')
    iq, full = g.synthesise_fade('ref', 18.0, name='full')
    check(full['n_samples'] == len(iq) and len(iq) % g.BLOCK_SAMPLES == 0, 'full: %d samples, whole noise blocks' % len(iq))
    del iq
    check_plan(full, 'full', 204)
    check_packets(full, 'full', True)
    check_top(full, 'full', 'ref', 18.0)
    _, nb_full = g.synthesise_fade('nb', 24.0, sir_db=6.0)
    check(nb_full['interferer']['hits'] == 102 and sorted(nb_full['interferer']['nb_hit_counts_by_type'].values()) == [25, 25, 26, 26],
          'full: 102 of 204 bursts hit, 26 / 26 / 25 / 25 by type (a 102 cannot be split equally four ways)')
    print('\nSmall runs: 24 bursts, 6 of each type, noise blocks of %d' % BLOCK)
    clean_iq, clean = run('ref', 30.0)
    check_plan(clean, '30 dB', 24)
    check_packets(clean, '30 dB')
    check_top(clean, '30 dB', 'ref', 30.0)
    check_reference_samples(clean_iq, clean, '30 dB file')
    ref18 = run('ref', 18.0)
    check_top(ref18[1], 'ref 18', 'ref', 18.0)
    noise = noise_of(ref18[1])
    free = np.ones(len(noise), bool)
    for e in ref18[1]['bursts']:
        free[burst_region(e)[0]:burst_region(e)[1]] = False
    check(np.array_equal(ref18[0][free], noise[free]), 'ref 18: off the bursts it is the regenerated noise (default_rng([9101, 1, block]), blocks of %d), exactly' % BLOCK)
    iq2, _ = g.synthesise_fade('ref', 18.0, bursts=4)
    free = np.ones(len(iq2), bool)
    _, side3 = g.synthesise_fade('ref', 18.0, bursts=4)
    for e in side3['bursts']:
        free[burst_region(e)[0]:burst_region(e)[1]] = False
    check(np.array_equal(iq2[free], t.my_noise(SEED, len(iq2), block=4194304)[free]), 'a file at the shipped block size is the regenerated noise in blocks of 4194304 off the bursts')
    lvl = []
    for e in ref18[1]['bursts']:
        a, b = burst_region(e)
        s = ref18[0][a:b].astype(np.complex128) - noise[a:b]
        lvl.append(10 * np.log10(np.mean(np.abs(s[100:-100]) ** 2) / NOISE) - 18.0)
    check(max(abs(x) for x in lvl) < 0.05, 'ref 18: the burst power over the noise in 1 MHz from the samples is 18 dB, worst %.3f' % max(abs(x) for x in lvl))
    check_file_size()

    print('\nThe fade function: statistics over thousands of independent processes')
    check_fade_function(g, 'function')

    print('\nRayleigh and Rician files, from their samples')
    for fd in (100.0, 300.0, 800.0):
        iq, side = run('rayleigh', 18.0, fd)
        check_top(side, 'ray fd%g' % fd, 'rayleigh', 18.0)
        check_fade_samples(iq, side, *ref18, 'ray fd%g snr18' % fd)
        check(all(e['fade_kind'] == 'rayleigh' and e['fade_fd_hz'] == fd and e['fade_k_db'] is None and e['nb_hit'] is None for e in side['bursts']),
              'ray fd%g: the fade keys say rayleigh at %g Hz, no K, no interferer keys' % (fd, fd))
    iq, side = run('rayleigh', 24.0, 300.0)
    check_fade_samples(iq, side, *run('ref', 24.0), 'ray fd300 snr24')
    iq, side = run('rician', 18.0, 300.0, 6.0, bursts=48)
    ref48 = run('ref', 18.0, bursts=48)
    hm = check_fade_samples(iq, side, *ref48, 'rice6 fd300 snr18 (48 bursts)')
    check_k_samples(hm, 'rice6', 6.0)
    check(all(e['fade_kind'] == 'rician' and e['fade_k_db'] == 6.0 for e in side['bursts']), 'rice6: the fade keys say rician, K 6 dB')
    check_top(side, 'rice6', 'rician', 18.0)
    a = run('rayleigh', 18.0, 300.0)[1]
    b = run('rayleigh', 24.0, 300.0)[1]
    check(all(x['fade_gain_db'] == y['fade_gain_db'] for x, y in zip(a['bursts'], b['bursts'])) and a['bursts'][0]['snr_eff_db'] != b['bursts'][0]['snr_eff_db'],
          'the 18 and 24 dB files have the same h, burst by burst; only the level differs')

    print('\nThe narrowband interferer')
    sides = []
    ref24 = run('ref', 24.0)
    measured = []
    for sir in (0.0, 6.0, 12.0):
        iq, side = run('nb', 24.0, sir=sir)
        check_top(side, 'nb sir%d' % sir, 'nb', 24.0)
        measured.append(check_nb(iq, side, *ref24, 'nb sir%d' % sir))
        sides.append(side)
    check_hit_sets(sides, 'nb')
    check(measured[0].keys() == measured[1].keys() == measured[2].keys() and all(
        abs(measured[0][k][i] - measured[2][k][i]) < 0.05 for k in measured[0] for i in (0, 1)),
          'nb: the three files\' interferers, measured from the samples, have the same bursts and the same intervals')

    print('\nThe clustering')
    check_clustering('ray fd300 @ 18 dB', 'rayleigh', 300.0)
    check_clustering('ray fd800 @ 18 dB', 'rayleigh', 800.0)
    check_second_seed()
    check_mutants()
    failed = [w for ok, w in RESULTS if not ok]
    print('\n%.0f s' % (time.time() - t0))
    print('RESULT: %s' % ('FAIL (%d)' % len(failed) if failed else 'PASS'))
    return 1 if failed else 0


# --- what the written files do ------------------------------------------------------------------------------

def load(directory, name):
    side = json.load(open(os.path.join(directory, 'synth_%s.json' % name)))
    iq = np.memmap(os.path.join(directory, 'synth_%s.cf32' % name), dtype='<c8', mode='r')
    return iq, side


def analyse(directory):
    """For the written files: two of each kind against the reference over the first 0.3 s, the fade statistics over the whole file,
    and the error clustering and burst-level yield of every file with libbtbb."""
    names = [n[0] for n in g.FADE_FILES]
    for n in names:
        for ext in ('cf32', 'json'):
            assert os.path.exists(os.path.join(directory, 'synth_%s.%s' % (n, ext))), 'missing %s.%s' % (n, ext)
    n03 = int(0.3 * FS)
    print('Verification over the first 0.3 s (%d samples), against the reference at the same SNR' % n03)
    for name, kind, snr, fd, k_db, sir in g.FADE_FILES:
        iq, side = load(directory, name)
        check(os.path.getsize(os.path.join(directory, 'synth_%s.cf32' % name)) == 8 * side['n_samples'] == 8 * len(iq),
              '%s: file size is 8 * n_samples = %d' % (name, 8 * side['n_samples']))
    for name, kind, snr, fd, k_db, sir in g.FADE_FILES:
        if kind == 'ref' and snr == 24.0:
            continue
        iq, side = load(directory, name)
        ref_iq, ref_side = load(directory, 'fade_ref_snr%d' % snr)
        part = [e for e in side['bursts'] if e['end_sample'] + 200 < n03]
        sub = dict(side, bursts=part, n_samples=n03)
        rsub = dict(ref_side, bursts=[e for e in ref_side['bursts'] if e['end_sample'] + 200 < n03], n_samples=n03)
        a, b = np.asarray(iq[:n03]), np.asarray(ref_iq[:n03])
        noise = t.my_noise(side['seed'], side['n_samples'], block=side['noise_block_samples'])[:n03]
        if kind == 'ref':
            lv = [10 * np.log10(np.mean(np.abs(a[e['start_sample'] + 100:e['end_sample'] - 100].astype(np.complex128)
                                              - noise[e['start_sample'] + 100:e['end_sample'] - 100]) ** 2) / NOISE) - snr for e in part]
            check(max(abs(x) for x in lv) < 0.05, '%s: %d bursts, level from the samples is %g dB, worst %.3f' % (name, len(part), snr, max(abs(x) for x in lv)))
        elif kind in ('rayleigh', 'rician'):
            sub['fade'] = side['fade']
            sub['seed'] = side['seed']
            sub['noise_block_samples'] = side['noise_block_samples']
            sub['bursts'] = part
            check_fade_samples_part(a, b, noise, sub, name)
        else:
            diff = a.astype(np.complex128) - b.astype(np.complex128)
            support = np.zeros(n03, bool)
            for e in part:
                if e['nb_hit']:
                    support[e['start_sample'] + e['nb_start_symbol'] * SPS - 45:e['start_sample'] + e['nb_end_symbol'] * SPS + 45] = True
            check(np.all(diff[~support] == 0) and np.any(diff[support] != 0), '%s: file - reference is zero off the %d interferers\' intervals in the first 0.3 s' % (name, sum(1 for e in part if e['nb_hit'])))
    print('\nFade statistics over the whole file, from the sidecars\' per-symbol gains')
    stats = []
    for name, kind, snr, fd, k_db, sir in g.FADE_FILES:
        if kind not in ('rayleigh', 'rician'):
            continue
        side = json.load(open(os.path.join(directory, 'synth_%s.json' % name)))
        per_burst = np.array([np.mean(10 ** (np.array(e['fade_gain_db']) / 10)) for e in side['bursts']])
        pooled = np.mean(np.concatenate([10 ** (np.array(e['fade_gain_db']) / 10) for e in side['bursts']]))
        spread = per_burst.std() / math.sqrt(len(per_burst))
        row = {'name': name, 'e_h2': pooled, 'se': spread}
        for rho_db in (-3.0, -6.0, -10.0):
            rho = 10 ** (rho_db / 20)
            cross = dur = 0
            for e in side['bursts']:
                r = np.sqrt(10 ** (np.array(e['fade_gain_db']) / 10))
                cross += int(np.sum((r[:-1] < rho) & (r[1:] >= rho)))
                dur += len(r) * 1e-6
            theory = math.sqrt(2 * math.pi) * fd * rho * math.exp(-rho ** 2) if kind == 'rayleigh' else None
            row['lcr%g' % rho_db] = (cross / dur, theory, cross)
        stats.append(row)
        extra = ''.join('  LCR(%g dB) %.0f/s (%d up-crossings)%s' % (r, row['lcr%g' % r][0], row['lcr%g' % r][2],
                        '' if row['lcr%g' % r][1] is None else ' vs %.0f' % row['lcr%g' % r][1]) for r in (-3.0, -6.0, -10.0))
        print('  %-24s E|h|^2 = %.3f (expected spread +-%.3f)%s' % (name, pooled, spread, extra))
    print('\nWhat the files do: genie start and channel, 0.8 MHz FIR demodulator, libbtbb')
    print('  %-24s %8s %8s %7s | burst-level payload correct, DM1 / DM3 / DM5 / FHS' % ('file', 'BER', 'Fano32', 'run'))
    taps = bt_synth_acl.rx_taps(FS)
    for name, kind, snr, fd, k_db, sir in g.FADE_FILES:
        iq, side = load(directory, name)
        errors, ok = [], {x: [0, 0] for x in g.TYPES}
        for e in side['bursts']:
            bits, _ = bt_synth_acl.rx_burst(iq, e, e['lap'], FS, CENTER, taps, search=30)
            if bits is None:
                errors.append(None)
                ok[e['ptype']][0] += 1
                continue
            want = np.array(t.hex_bits(e['air_bits'], e['air_bits_length']))
            errors.append((bits != want).astype(np.int8))
            ok[e['ptype']][0] += 1
            ok[e['ptype']][1] += bool(decode_ok(bits, e))
        idx, run_len, ber = fano(errors)
        print('  %-24s %8.4f %8.1f %7.2f | %s' % (name, ber, idx, run_len, ' / '.join('%d/%d = %3.0f %%' % (ok[x][1], ok[x][0], 100 * ok[x][1] / ok[x][0]) for x in g.TYPES)), flush=True)
    failed = [w for okk, w in RESULTS if not okk]
    print('\nRESULT: %s' % ('FAIL (%d)' % len(failed) if failed else 'PASS'))
    return 1 if failed else 0


def check_fade_samples_part(a, b, noise, side, label):
    """check_fade_samples on arrays already cut to the first 0.3 s (the noise given)."""
    worst, worst_h = 0.0, 0.0
    for k, e in enumerate(side['bursts']):
        lo, hi = burst_region(e)
        sf = a[lo:hi].astype(np.complex128) - noise[lo:hi]
        sr = b[lo:hi].astype(np.complex128) - noise[lo:hi]
        amp = math.sqrt(NOISE * 10 ** (e['snr_db'] / 10))
        n = np.arange(lo, hi)
        ht = g.fade_h(side['seed'], k, e['fade_fd_hz'], (n - e['start_sample']) / FS, e['fade_k_db'])
        worst_h = max(worst_h, float(np.abs(sf - ht * sr).max()))
        centres = e['start_sample'] - lo + np.arange(e['air_bits_length']) * SPS + SPS // 2
        gl = np.array(e['fade_gain_db'])
        want = 10 ** (gl / 20) * np.abs(sr[centres])
        worst = max(worst, float((np.abs(np.abs(sf[centres]) - want) - want * (10 ** (GAIN_TOL_DB / 20) - 1)).max()))
    check(worst <= FLOAT_ABS and worst_h < FLOAT_ABS, '%s: %d bursts in the first 0.3 s: |h| from the samples against fade_gain_db (every symbol) excess over the 0.02 dB bound %.1e, complex residual against fade_h on every sample %.1e'
          % (label, len(side['bursts']), worst, worst_h))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--analyse', metavar='DIR', help='analyse the files of --set fade written in DIR instead of running the tests')
    args = ap.parse_args()
    return analyse(args.analyse) if args.analyse else tests()


if __name__ == '__main__':
    sys.exit(main())
