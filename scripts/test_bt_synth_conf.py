#!/usr/bin/env python3
"""Hold the conformant page and inquiry exchange writer to the Core specification and to its own samples.

    python scripts/test_bt_synth_conf.py                          # small runs, the full plan, mutants
    python scripts/test_bt_synth_conf.py --file DIR/synth_conf_page_hop_win.cf32 --exchanges 0,1
                                                                  # the sample-side checks on real exchanges
    python scripts/test_bt_synth_conf.py --file DIR/synth_conf_page_hop_win.json --plan   # the plan checks on a sidecar

``scripts/bt_synth_conf.py`` writes 40 MS/s files of page exchanges and inquiry
exchanges whose every channel is a hop kernel's, and only the bursts on channels 27 to 51
are in the samples. Nothing here trusts the generator's bookkeeping:

* **Every channel is recomputed** from the sidecar's own clocks by a kernel written here
  (``scripts/test_bt_hop_substates.py``: Table 2.1, Figure 2.16, Table 2.2 from bit
  lists; it is itself held to Part G), for the page IDs, the response, the FHS, the
  acknowledgement, the inquiry IDs and response, and, with the connection column of Table 2.2,
  the first POLL and every follow-up packet of the master and, at the slave's clock, of
  the slave.
* **The heard-slot derivation**: the paged device's scan frequency is the page frequency of
  the heard half-slot and of no earlier half-slot of the train, the scan frequency is in the
  train the master sent and in no other, the response, FHS and acknowledgement are at the
  intervals of the text and on Xprp (N = 0), Xprc (N = 1) and Xprp (N = 1).
* **The samples**: the bursts are found blind (a 500 kHz channelizer over channels 27-51, then
  the access code's correlation), and each one's start, carrier, level and length are compared
  with the sidecar's; the unrendered bursts are absent (their spans hold the noise floor and
  nothing else); the FHS and the follow-up headers decode through libbtbb at the
  right UAP and clock.
* **The clock lock**: the search over all 2**27 clocks of CLK[27:1], written here with a hop
  table built by another vectorisation of the kernel, on the in-window packets only; the
  sidecar's solutions and uniqueness must be the same set.
* **Mutants**: eleven single mistakes made in a copy of the generator, each caught.
"""
import argparse
import contextlib
import importlib.util
import json
import os
import sys

import numpy as np
from scipy.signal import fftconvolve, firwin, lfilter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from apps import bt_fhs  # noqa: E402
from apps import bt_hop  # noqa: E402
from apps import bt_hop_substates as hs  # noqa: E402
from scripts import bt_ota_check, bt_synth, bt_synth_conf as gen  # noqa: E402
from scripts import test_bt_hop_substates as kt  # noqa: E402
from scripts import test_bt_synth_page as old  # noqa: E402

FS = 40e6
SPS = 40
CENTER = 2441.0
#: -12..+12 MHz around the centre: symmetric, and with a burst's own +-1 MHz its edges are at +-13 MHz inside the BB60D's +-13.5 MHz.
WINDOW = (27, 51)
WINDOW_MHZ = [2402 + WINDOW[0], 2402 + WINDOW[1]]
HALF_WIDTH = 12.0
TICK = 12500
SLOT = 25000
PAGED_LAP, PAGED_UAP = 0x6B1D3C, 0x47
M_LAP, M_UAP, M_NAP, M_COD = 0x112233, 0x55, 0x1234, 0x5A020C
GIAC = 0x9E8B33
M_ADDRESS = M_UAP << 24 | M_LAP
NOISE_POWER = bt_synth.AMPLITUDE ** 2 / 100 * FS / 1e6
NOISE_1MHZ = bt_synth.AMPLITUDE ** 2 / 100

RESULTS = []
QUIET = [False]


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


nbits = old.nbits


# --- the connection column of Table 2.2, written here ---------------------------------

def basic_ctl(clk, address):
    """Table 2.2, 'Connection state': A = A27-23 xor CLK25-21, C = A8,6,4,2,0 xor CLK20-16, D = A18-10 xor
    CLK15-7, X = CLK6-2, Y1 = CLK1, F = 16 x CLK27-7 mod 79; from bit lists."""
    a, c = kt.bit_list(address, 28), kt.bit_list(clk, 28)
    return dict(A=kt.num(a[23:28]) ^ kt.num(c[21:26]), B=kt.num(a[19:23]),
                C=(a[0] + 2 * a[2] + 4 * a[4] + 8 * a[6] + 16 * a[8]) ^ kt.num(c[16:21]),
                D=kt.num(a[10:19]) ^ kt.num(c[7:16]),
                E=a[1] + 2 * a[3] + 4 * a[5] + 8 * a[7] + 16 * a[9] + 32 * a[11] + 64 * a[13],
                F=16 * kt.num(c[7:28]) % 79, X=kt.num(c[2:7]), Y1=c[1], Y2=32 * c[1])


def basic_channel(clk, address=M_ADDRESS):
    return kt.kernel(basic_ctl(clk, address))


def channel_from_hop(h):
    """A burst's channel from its ``hop`` record alone, by the test's kernel."""
    sub = h['substate']
    if sub == 'page':
        return kt.independent('e', PAGED_LAP, PAGED_UAP, clke=h['clke'], koffset=h['koffset'], knudge=h['knudge'])
    if sub == 'basic':
        return basic_channel(h['clk'])
    if sub == 'inquiry':
        return kt.independent('f', 0, 0, clkn=h['clkn'], koffset=h['koffset'], knudge=h['knudge'])
    if sub == 'inquiry_response':
        return kt.independent('k', 0, 0, clkn=h['clkn'], n=h['n'])
    if sub == 'peripheral_page_response':
        return kt.independent('h', PAGED_LAP, PAGED_UAP, clkn_frozen=h['clkn_frozen'], clkn=h['clkn'], n=h['n'])
    if sub == 'central_page_response':
        return kt.independent('g', PAGED_LAP, PAGED_UAP, clke_frozen=h['clke_frozen'], clke=h['clke'], koffset=h['koffset'],
                              knudge=h['knudge'], n=h['n'])
    raise ValueError(sub)


def expected_hop(E, b):
    """What a burst's hop record must be, from the exchange's clocks and counters derived here: the heard slot's start, N counted
    in master TX slots since it, the clocks frozen at the response, the train's koffset."""
    t = b['tick']
    if E['kind'] == 'page':
        e0, s0, m0 = E['clke0'], E['paged_clkn0'], E['master_clkn0']
        ko, kn = (24 if E['train'] == 'A' else 8), 0
        slot0 = 4 * E['heard_slot']
        k = b['kind']
        if k == 'id_page':
            return dict(substate='page', clke=e0 + t, koffset=ko, knudge=kn)
        if k in ('id_response', 'id_ack'):
            return dict(substate='peripheral_page_response', clkn_frozen=s0 + E['heard_tick'], clkn=s0 + t, n=(t - slot0) // 4)
        if k == 'fhs':
            return dict(substate='central_page_response', clke_frozen=e0 + E['heard_tick'] + 2, clke=e0 + t, koffset=ko, knudge=kn,
                        n=1 + (t - (slot0 + 4)) // 4)
        return dict(substate='basic', clk=m0 + t)
    i0, s0 = E['inquirer_clkn0'], E['scanner_clkn0']
    ko = 24 if E['train'] == 'A' else 8
    if b['kind'] == 'id_inquiry':
        return dict(substate='inquiry', clkn=i0 + t, koffset=ko, knudge=0)
    return dict(substate='inquiry_response', clkn=s0 + t, n=E['scanner_n'])


def hop_records_ok(E, items):
    """Every burst's complete hop record equals the independently derived one, and its channel is the kernel's on that record."""
    return all(b['hop'] == expected_hop(E, b) and b['channel'] == channel_from_hop(b['hop']) for _, b in items)


# a second vectorisation of the kernel, by lookup: PERM[p, z] for all 2**14 control words
_PERM = []


def perm_table():
    if not _PERM:
        t = np.zeros((1 << 14, 32), dtype=np.uint8)
        for p in range(1 << 14):
            pbits = kt.bit_list(p, 14)
            for z in range(32):
                zz = kt.bit_list(z, 5)
                for stage in range(7):
                    for pn in (13 - 2 * stage, 12 - 2 * stage):
                        if pbits[pn]:
                            i, j = kt.TABLE_2_1[pn]
                            zz[i], zz[j] = zz[j], zz[i]
                t[p, z] = kt.num(zz)
        _PERM.append(t)
    return _PERM[0]


def vec_channels(clks, address):
    """The connection-state kernel for an array of clocks by the PERM lookup: another way than the generator's."""
    t = perm_table()
    clk = np.asarray(clks, dtype=np.int64)
    a = kt.bit_list(address, 28)
    aa, bb = kt.num(a[23:28]), kt.num(a[19:23])
    cc = a[0] + 2 * a[2] + 4 * a[4] + 8 * a[6] + 16 * a[8]
    dd, ee = kt.num(a[10:19]), a[1] + 2 * a[3] + 4 * a[5] + 8 * a[7] + 16 * a[9] + 32 * a[11] + 64 * a[13]
    y1 = (clk >> 1) & 1
    z = (((clk >> 2) & 31) + (aa ^ ((clk >> 21) & 31))) % 32 ^ bb
    p = (dd ^ ((clk >> 7) & 0x1FF)) | (((cc ^ ((clk >> 16) & 31)) ^ (31 * y1)) << 9)
    reg = (t[p, z].astype(np.int64) + ee + 16 * (clk >> 7) % 79 + 32 * y1) % 79
    bank = np.array([c for c in range(79) if c % 2 == 0] + [c for c in range(79) if c % 2 == 1])
    return bank[reg]


_TABLE = {}


def domain_table(address):
    """hop of the clock c << 1 for every c < 2**27, by ``vec_channels``."""
    if address not in _TABLE:
        out = np.empty(1 << 27, dtype=np.uint8)
        for lo in range(0, 1 << 27, 1 << 22):
            c = np.arange(lo, lo + (1 << 22), dtype=np.int64)
            out[lo:lo + (1 << 22)] = vec_channels(c << 1, address)
        _TABLE[address] = out
    return _TABLE[address]


def full_domain_solutions(obs, address=M_ADDRESS):
    """Every CLK[27:1] c (the clock at the slot the offsets are counted from) of the whole 2**27 domain with
    ``table[(c + off) mod 2**27] == channel`` for each ``(off, channel)`` of ``obs``, sorted."""
    table = domain_table(address)
    mask = (1 << 27) - 1
    off, ch = obs[-1]
    cand = (np.flatnonzero(table == ch).astype(np.int64) - off) & mask
    for off, ch in obs[:-1]:
        cand = cand[table[(cand + off) & mask] == ch]
    return sorted(cand.tolist())


def check_kernels():
    print('\nThe kernels this test and the generator use')
    # the connection column held to Part G (set 1 and set 2, from the specification)
    g1 = [(0x10, 0, 8), (0x12, 0, 66), (0x14, 0, 10), (0x16, 0, 70), (0x18, 0, 12), (0x1a, 0, 19), (0x1c, 0, 14), (0x1e, 0, 23),
          (0x10, 0x2a96ef25, 55), (0x12, 0x2a96ef25, 26), (0x14, 0x2a96ef25, 19), (0x16, 0x2a96ef25, 20),
          (0x18, 0x2a96ef25, 23), (0x1a, 0x2a96ef25, 22), (0x1c, 0x2a96ef25, 53), (0x1e, 0x2a96ef25, 40)]
    check(all(basic_channel(c, a) == v for c, a, v in g1),
          'the test\'s connection-state control word and kernel give Part G\'s basic sequence (16 values, two addresses)')
    rng = np.random.default_rng(20261008)
    clks = [int(c) for c in rng.integers(0, 1 << 28, size=4000)]
    addr = int(rng.integers(0, 1 << 28))
    scal = [bt_hop.hop_channel(c, addr, None) for c in clks]
    check(all(basic_channel(c, addr) == s for c, s in zip(clks, scal)),
          'the test\'s kernel is bt_hop.hop_channel (basic sequence) on 4000 random clocks and a random address')
    check([int(v) for v in vec_channels(clks, addr)] == scal and [int(v) for v in gen.basic_hop_vec(clks, addr)] == scal,
          'the test\'s lookup vectorisation and the generator\'s vector kernel both equal the scalar kernel on them')
    ks = [0, (1 << 27) - 1] + [int(k) for k in rng.integers(0, 1 << 27, size=3000)]
    t_gen = gen.basic_table(M_ADDRESS)
    t_test = domain_table(M_ADDRESS)
    check(all(int(t_gen[k]) == int(t_test[k]) == bt_hop.hop_channel(k << 1, M_ADDRESS, None) for k in ks)
          and bool(np.array_equal(t_gen, t_test)),
          'the 2**27-entry hop table of the generator is the test\'s, entry for entry, and the scalar kernel on 3002 of them')


# --- the plan checks, on a sidecar ---------------------------------------------------

def by_exchange(side):
    out = {}
    for i, b in enumerate(side['bursts']):
        out.setdefault(b['exchange'], []).append((i, b))
    return out


def clk_frozen_same_16_12(c0, span):
    """CLK16-12 does not change from clock c0 through c0 + span."""
    return kt.clk_field(c0, 16, 12) == kt.clk_field(c0 + span, 16, 12)


def train_channels(clk0, lap, uap, koffset, native=False):
    """The 16 channels of the 16 TX half-slots of a train, from the test's own kernel (page: CLKE, inquiry: CLKN)."""
    out = []
    for t in range(32):
        clk = clk0 + t
        if (clk >> 1) & 1:
            continue
        out.append(kt.independent('f', 0, 0, clkn=clk, koffset=koffset) if native
                   else kt.independent('e', lap, uap, clke=clk, koffset=koffset))
    return out


def check_plan(side, kind, exchanges, followup, label, controls=True):
    print('\nThe plan of %s: %d %s exchanges' % (label, exchanges, kind))
    bursts = side['bursts']
    ex = by_exchange(side)
    check(side['file_kind'] == kind and side['n_exchanges'] == exchanges == len(side['exchanges']) == len(ex),
          '%d exchanges, kind %s' % (exchanges, kind))
    check(side['sample_rate'] == FS and side['center_mhz'] == CENTER and side['snr_bw_hz'] == 1e6
          and side['noise_1mhz'] == NOISE_1MHZ and side['slot_samples'] == SLOT and side['air_bits_omitted'] is True
          and all('air_bits' not in b for b in bursts), '40 MS/s, 2441.0 MHz, the one noise floor, no air_bits, air_bits_omitted')
    need = ['generator', 'generator_commit', 'lap', 'uap', 'sample_rate', 'center_mhz', 'modulation', 'slot_samples',
            'start_sample_meaning', 'clk_convention', 'snr_bw_hz', 'noise_1mhz', 'bursts', 'n_samples', 'conformance',
            'window', 'symbol_phases', 'spec_notes', 'exchanges', 'per_burst_keys', 'identities']
    check(all(k in side for k in need), 'the sidecar has its keys: %s' % (', '.join(k for k in need if k not in side) or 'all of them'))
    per = side['per_burst_keys']
    check(all(side[k] is None for k in per if k in side) and all(k in b for b in bursts for k in per),
          'per_burst_keys (%d) are null at the top where they appear and present on every burst' % len(per))
    w = side['window']
    check(w['channels'] == list(WINDOW) and w['center_mhz'] == CENTER and w['mhz'] == WINDOW_MHZ
          and w['half_width_mhz'] == HALF_WIDTH and WINDOW_MHZ[0] - CENTER == -HALF_WIDTH == -(WINDOW_MHZ[1] - CENTER)
          and '-%g to +%g MHz' % (HALF_WIDTH, HALF_WIDTH) in w['why'] and 'symmetric' in w['why'] and "+-13.5 MHz" in w['why']
          and 'rendered false' in w['why'],
          'the window is channels %d-%d (%d-%d MHz), -%g to +%g MHz around the centre, symmetric, fits the BB60D, and says why'
          % (WINDOW + tuple(WINDOW_MHZ) + (HALF_WIDTH, HALF_WIDTH)))
    c = side['conformance']
    txt = ' '.join(c['followed']) + ' ' + ' '.join(c['not_followed'])
    check(set(c) >= {'followed', 'not_followed', 'silent_in_text', 'reason'} and 'RAND back-off' in txt and 'interlaced' in txt
          and 'train repetition' in txt and 'retransmitted FHS' in txt and 'basic channel hopping sequence' in txt
          and 'heard ID is derived' in txt and 'already running' in txt and 'odd CLKE16-12' not in txt
          and 'erratum' not in txt.lower(),
          'conformance says plainly what is followed (hop sequences, response frequencies, basic follow-up, the derived heard '
          'slot) and not (RAND back-off, interlaced scan, retransmitted FHS, the train repetition count)')
    notes = ' '.join(side['spec_notes'])
    check("PART G'S PAGE AND INQUIRY TABLE AND EQ 2" in notes and 'alternating A, B, A, B' in notes and 'There is no conflict' in notes
          and 'TRAIN HISTORY NOT IN THE FILE' in notes and 'already running' in notes and 'SILENT' in notes
          and 'erratum' not in notes.lower() and 'PART COMPANY' not in notes,
          'spec_notes cite the text, say where it is silent, that Part G\'s table is EQ 2 with the train alternating, and that '
          'B-train exchanges are excerpts of a page procedure already running')
    # no overlap, time order, every start on the exchange's phase grid
    check(all(b2['start_sample'] >= b1['start_sample'] + nbits(b1) * SPS for b1, b2 in zip(bursts, bursts[1:])),
          'no burst overlaps the next in time (rendered or not)')
    check(all(b['symbol_phase'] == b['start_sample'] % SPS for b in bursts), 'symbol_phase is start_sample % 40')
    ph = [E['symbol_phase'] for E in side['exchanges']]
    check(ph == side['symbol_phases'] and all(bursts[E['first_burst']]['start_sample'] % SPS == E['symbol_phase'] and
                                              E['start_sample'] % SPS == E['symbol_phase'] for E in side['exchanges']),
          'symbol_phases is each exchange\'s, and its first burst starts at that phase')
    check(all(b['symbol_phase'] == (E['symbol_phase'] + (b['tick'] % 2) * (SPS // 2)) % SPS
              for E in side['exchanges'] for _, b in ex[E['exchange']]),
          'a burst is at its exchange\'s phase on an even tick and 20 samples away on an odd tick (a tick is 312.5 symbols)')
    if exchanges >= 8:
        check(len(set(ph)) >= min(exchanges // 2, 8) and any(p not in (0, 20) for p in ph),
              'the exchanges\' symbol phases are not all 0 and 20: %d distinct, e.g. %s' % (len(set(ph)), sorted(set(ph))[:8]))
    check(all(0 <= b['timing_frac'] < 1 for b in bursts) and len({round(b['timing_frac'], 6) for b in bursts}) > 0.9 * len(bursts),
          'timing_frac is in [0, 1) and differs per burst')
    # SNR
    bases = [E['snr_base_db'] for E in side['exchanges']]
    check(all(10.0 <= s <= 20.0 and abs(s * 10 - round(s * 10)) < 1e-9 for s in bases),
          'every exchange\'s base SNR is in 10 to 20 dB and a multiple of 0.1 (%.1f to %.1f)' % (min(bases), max(bases)))
    if exchanges >= 100:
        check(min(bases) < 10.6 and max(bases) > 19.4, 'the base SNRs spread over the range (%.1f to %.1f, %d exchanges)' % (min(bases), max(bases), exchanges))
    jit = np.array([b['snr_db'] - side['exchanges'][b['exchange']]['snr_base_db'] for b in bursts])
    check(np.abs(jit).max() <= 3.0 + 0.006, 'every burst\'s SNR is within +-3 dB of its exchange\'s base (worst %.3f)' % np.abs(jit).max())
    if len(bursts) >= 600:
        check(0.85 < jit.std() < 1.1 and abs(jit.mean()) < 0.15,
              'the per-burst jitter has sigma %.2f dB (N(0, 1) clipped at +-3) and mean %+.2f' % (jit.std(), jit.mean()))
    # the window
    check(all(b['in_window'] == (WINDOW[0] <= b['channel'] <= WINDOW[1]) and b['rendered'] == b['in_window']
              and b['channel_mhz'] == 2402 + b['channel'] for b in bursts),
          'in_window is %d <= channel <= %d, rendered equals it, channel_mhz is 2402 + channel' % WINDOW)
    nin = sum(b['rendered'] for b in bursts)
    check(side['n_bursts'] == len(bursts) and side['n_bursts_in_window'] == nin and side['n_bursts_not_rendered'] == len(bursts) - nin,
          'n_bursts, n_bursts_in_window (%d) and n_bursts_not_rendered are the bursts\' own counts' % nin)
    cnt, cin = {}, {}
    for b in bursts:
        cnt[b['kind']] = cnt.get(b['kind'], 0) + 1
        if b['rendered']:
            cin[b['kind']] = cin.get(b['kind'], 0) + 1
    check(cnt == side['kind_counts'] and cin == side['kind_counts_in_window'], 'kind_counts, in all and in the window: %s / %s' % (cnt, cin))
    fh_in = sum(any(b['ptype'] == 'FHS' and b['rendered'] for _, b in ex[e]) for e in ex)
    check(side['n_exchanges_fhs_in_window'] == fh_in and side['n_exchanges_heard_id_in_window'] ==
          sum(E['heard_id_in_window'] for E in side['exchanges']), 'n_exchanges_fhs_in_window is %d of %d' % (fh_in, exchanges))
    # idle gaps 20-40 ms between exchanges
    real = []
    for e in range(1, exchanges):
        last = bursts[side['exchanges'][e - 1]['last_burst']]
        first = bursts[side['exchanges'][e]['first_burst']]
        real.append((first['start_sample'] - (last['start_sample'] + nbits(last) * SPS)) / FS * 1e3)
    if real:
        check(all(20.0 - 0.1 <= g <= 40.0 + 0.1 for g in real) and all(abs(g - E['gap_before_ms']) < 0.1 for g, E in zip(real, side['exchanges'][1:])),
              'the idle gap between exchanges is %.1f to %.1f ms from the starts (20 to 40)' % (min(real), max(real)))
    check(max(b['clk'] for b in bursts) < (1 << 28) and max(b['hop_clk'] for b in bursts) < (1 << 28),
          'every clock is below 2**28: none wraps')
    # per-exchange counts
    bad = 0
    for e in range(exchanges):
        E, items = side['exchanges'][e], ex[e]
        win = [b for _, b in items if b['rendered']]
        ok = (E['n_bursts'] == len(items) and E['n_bursts_in_window'] == len(win) and E['fhs_in_window'] == any(b['ptype'] == 'FHS' for b in win)
              and E['id_in_window'] == sum(b['kind'] in ('id_page', 'id_inquiry') for b in win)
              and E['heard_id_in_window'] == any(b['tick'] == E['heard_tick'] and b['kind'] in ('id_page', 'id_inquiry') for b in win))
        if kind == 'page':
            ok = ok and E['id_page_in_window'] == E['id_in_window'] and E['n_followup_in_window'] == sum(
                b['kind'] in ('followup_master', 'followup_slave') for b in win) and E['first_poll_in_window'] == any(
                b['kind'] == 'followup_master' and b['tick'] == E['poll_tick'] for b in win)
        bad += not ok
    check(bad == 0, 'n_bursts_in_window, fhs_in_window, id_in_window / id_page_in_window, heard_id_in_window, first_poll_in_window and '
          'n_followup_in_window of every exchange are the bursts\' own (%d wrong)' % bad)
    if kind == 'page':
        check_page(side, ex, exchanges, followup, controls)
    else:
        check_inquiry(side, ex, exchanges)


def check_common_exchange(E, items, kind):
    """Shared per-exchange relations of the IDs (page or inquiry): the TX half-slots up to the heard slot, 312.5 us and 1250 us
    apart on the half-slot grid."""
    ids = [b for _, b in items if b['kind'] in ('id_page', 'id_inquiry')]
    s = [b['start_sample'] for b in ids]
    ticks = [b['tick'] for b in ids]
    n = E['n_id_slots']
    ok = (len(ids) == 2 * n and ticks == [4 * k + j for k in range(n) for j in (0, 1)] and n == E['heard_slot'] + 1
          and all(s[2 * k + 1] - s[2 * k] == TICK for k in range(n)) and all(s[2 * k + 2] - s[2 * k] == 2 * SLOT // 1 for k in range(n - 1))
          and all(ids[2 * k]['channel'] != ids[2 * k + 1]['channel'] for k in range(n)) and E['heard_tick'] == 4 * E['heard_slot'] + E['heard_id']
          and E['heard_id'] in (0, 1))
    return ids, ok


def check_page(side, ex, exchanges, followup, controls):
    bursts = side['bursts']
    failed = {k: 0 for k in ('ids', 'order', 'scan', 'train', 'clocks', 'resp', 'fhs', 'ack', 'followup', 'times', 'fields', 'hopdict')}
    first_bad = {}
    n_same = n_pairs = 0
    lock_rows = []
    for e in range(exchanges):
        E, items = side['exchanges'][e], ex[e]
        by = {}
        for _, b in items:
            by.setdefault(b['kind'], []).append(b)

        def bad(k, why=''):
            failed[k] += 1
            first_bad.setdefault(k, 'exchange %d %s' % (e, why))
        ids, ok = check_common_exchange(E, items, 'page')
        if not ok:
            bad('order', 'IDs not on the TX half-slot grid')
        kinds = [b['kind'] for _, b in items]
        n_id = len(ids)
        if kinds[:n_id] != ['id_page'] * n_id or kinds[n_id:n_id + 3] != ['id_response', 'fhs', 'id_ack'] or \
                set(kinds[n_id + 3:]) != {'followup_master', 'followup_slave'}:
            bad('order', 'kinds %s' % kinds[:n_id + 4])
            continue
        e0, s0, m0 = E['clke0'], E['paged_clkn0'], E['master_clkn0']
        ko, kn = E['koffset'], E['knudge']
        # the clocks: whole 4-tick multiples, no CLK16-12 change over the paging part, the train and koffset agree
        if not (e0 % 4 == 0 and s0 % 4 == 0 and m0 % 4 == 0 and clk_frozen_same_16_12(e0, 80) and clk_frozen_same_16_12(s0, 80)
                and ko == (24 if E['train'] == 'A' else 8) and kn == 0 and E['clkn_minus_clke'] == (s0 - e0) % (1 << 28)):
            bad('clocks')
        # the train window: (CLKN16-12 - CLKE16-12) mod 32 inside A's or B's
        dd = (kt.clk_field(s0, 16, 12) - kt.clk_field(e0, 16, 12)) % 32
        if not ((dd >= 24 or dd <= 7) if E['train'] == 'A' else 8 <= dd <= 23) or dd != E['d_clkn16_12_minus_clke16_12']:
            bad('train', 'd = %d, train %s' % (dd, E['train']))
        # the IDs: the page kernel at CLKE, the paged device's address
        if not all(b['channel'] == kt.independent('e', PAGED_LAP, PAGED_UAP, clke=e0 + b['tick'], koffset=ko, knudge=kn)
                   and b['hop_clk'] == e0 + b['tick'] and b['hop_clk_kind'] == 'CLKE' and b['lap'] == PAGED_LAP for b in ids):
            bad('ids', 'channel of an ID is not the page kernel at CLKE')
        # the scan frequency and the heard ID
        scan = kt.independent('a', PAGED_LAP, PAGED_UAP, clkn=s0)
        held = all(kt.independent('a', PAGED_LAP, PAGED_UAP, clkn=s0 + t) == scan for t in range(0, 80))
        heard_b = [b for b in ids if b['tick'] == E['heard_tick']]
        earlier = [b for b in ids if b['tick'] < E['heard_tick']]
        own = train_channels(e0, PAGED_LAP, PAGED_UAP, ko)
        other = train_channels(e0, PAGED_LAP, PAGED_UAP, 8 if ko == 24 else 24)
        if not (scan == E['scan_channel'] and held and len(heard_b) == 1 and heard_b[0]['channel'] == scan
                and not any(b['channel'] == scan for b in earlier) and scan in own and scan not in other
                and E['scanner_x'] == kt.clk_field(s0, 16, 12)):
            bad('scan', 'scan %s heard %s earlier-match %s in train %s in other %s' % (
                scan, [b['channel'] for b in heard_b], any(b['channel'] == scan for b in earlier), scan in own, scan in other))
        # the response
        resp, fhs, ack = by['id_response'][0], by['fhs'][0], by['id_ack'][0]
        t_heard = E['heard_tick']
        n0 = ((s0 + t_heard + 2) - (s0 + 4 * E['heard_slot'])) // 4
        want = kt.independent('h', PAGED_LAP, PAGED_UAP, clkn_frozen=s0 + t_heard, clkn=s0 + t_heard + 2, n=0)
        if not (n0 == 0 and resp['tick'] == t_heard + 2 and resp['channel'] == want and resp['hop_clk'] == s0 + t_heard + 2
                and resp['hop_clk_kind'] == 'CLKN' and resp['lap'] == PAGED_LAP):
            bad('resp', 'channel %s want %s' % (resp['channel'], want))
        # the FHS: the Central page response, CLKE frozen at the receipt of the response, N = 1
        clke_star = e0 + t_heard + 2
        n_f = 1
        want = kt.independent('g', PAGED_LAP, PAGED_UAP, clke_frozen=clke_star, clke=e0 + 4 * E['heard_slot'] + 4, koffset=ko, knudge=kn, n=n_f)
        wx = kt.control_words('g', 0, clke_frozen=clke_star, clke=0, koffset=ko, knudge=kn, n=n_f)['X']
        if not (fhs['tick'] == 4 * E['heard_slot'] + 4 and fhs['channel'] == want and E['fhs_tick'] == fhs['tick']
                and fhs['fhs']['whitening_x'] == wx == E['fhs_whitening']['xprc'] and E['fhs_whitening']['N'] == 1
                and E['fhs_whitening']['clke_frozen'] == clke_star and E['clke_frozen'] == clke_star
                and fhs['clk'] == m0 + fhs['tick'] and fhs['clk'] % 4 == 0 and fhs['fhs']['clk27_2'] == fhs['clk'] >> 2
                and E['fhs_clk'] == fhs['clk'] and fhs['hop_clk'] == e0 + fhs['tick']):
            bad('fhs', 'channel %s want %s X %s want %s' % (fhs['channel'], want, fhs['fhs']['whitening_x'], wx))
        # the acknowledgement: Peripheral page response, N = 1
        n_a = ((s0 + ack['tick']) - (s0 + 4 * E['heard_slot'])) // 4
        want = kt.independent('h', PAGED_LAP, PAGED_UAP, clkn_frozen=s0 + t_heard, clkn=s0 + ack['tick'], n=1)
        if not (n_a == 1 and ack['tick'] == fhs['tick'] + 2 and ack['channel'] == want and ack['hop_clk'] == s0 + ack['tick']):
            bad('ack', 'channel %s want %s' % (ack['channel'], want))
        # the text's intervals, in samples
        fm = by['followup_master']
        fsl = by['followup_slave']
        ids_s = [b['start_sample'] for b in ids]
        heard_s = heard_b[0]['start_sample'] if heard_b else None
        if not (heard_s is not None and resp['start_sample'] - heard_s == SLOT
                and fhs['start_sample'] - ids_s[2 * E['heard_slot']] == 2 * SLOT
                and ack['start_sample'] - fhs['start_sample'] == SLOT and fm[0]['start_sample'] - fhs['start_sample'] == 2 * SLOT
                and fm[0]['ptype'] == 'POLL' and fm[0]['tick'] == E['poll_tick'] == fhs['tick'] + 4):
            bad('times')
        # the follow-up: the basic sequence at the master's clock and, one slot later, at the slave's
        fok = len(fm) == len(fsl) == followup and fm[0]['clk'] == E['master_clk_at_connection'] == fhs['clk'] + 4
        pairs = []
        for m, sl in zip(fm, fsl):
            fok = fok and m['clk'] % 4 == 0 and m['clk'] == m0 + m['tick'] and sl['clk'] == m['clk'] + 2 and sl['tick'] == m['tick'] + 2
            fok = fok and sl['start_sample'] - m['start_sample'] == SLOT and (m['tick'] - E['poll_tick']) % 4 == 0
            cm = basic_channel(m['clk'], M_ADDRESS)
            cs = basic_channel(m['clk'] + 2, M_ADDRESS)
            fok = fok and m['channel'] == cm and sl['channel'] == cs and m['hop'] == dict(substate='basic', clk=m['clk']) \
                and sl['hop'] == dict(substate='basic', clk=sl['clk']) and m['hop_clk_kind'] == 'CLK'
            pairs.append([cm, cs])
            n_pairs += 1
            n_same += cm == cs
        fok = fok and E['followup_basic_channels'] == pairs
        if not fok:
            bad('followup')
        if not (all(m['lap'] == M_LAP and m['uap_for_hec'] == M_UAP and m['lt_addr'] == 1 for m in fm + fsl)
                and {m['ptype'] for m in fm} <= {'POLL', 'NULL', 'DH1'} and {s_['ptype'] for s_ in fsl} <= {'NULL', 'DM1', 'DH1'}
                and old.rebuild_fhs_ok(fhs, PAGED_UAP, PAGED_LAP) and fhs['fhs']['lap'] == M_LAP and fhs['fhs']['uap'] == M_UAP
                and fhs['fhs']['header_uap'] == PAGED_UAP and fhs['uap_for_hec'] == PAGED_UAP and fhs['fhs']['am_addr'] == 1):
            bad('fields')
        # every burst's hop record names the inputs its channel came from
        if not hop_records_ok(E, items):
            bad('hopdict')
        # the lock rows, from the in-window packets of the master's LAP only
        obs = [((b['tick'] - E['poll_tick']) // 2, b['channel']) for b in fm + fsl if b['rendered']]
        lock_rows.append((e, E, sorted(obs, key=lambda o: o[0])))
    lines = {'ids': 'every page ID is the page kernel (EQ 2, Y1 = CLKE1) of the paged device\'s address at CLKE, on the half-slot grid',
             'order': 'IDs, then response, FHS, acknowledgement, POLL and follow-up in that order; two IDs per TX slot 312.5 us apart on distinct channels',
             'scan': 'the scan frequency (Xps from CLKN16-12, constant) is the channel of the heard half-slot, of no earlier half-slot, in the train sent and not in the other',
             'train': 'the offset of the clocks is in the train\'s window of CLKN16-12 - CLKE16-12 mod 32 (A -8..7, B 8..23)',
             'clocks': 'clocks are multiples of 4, CLKE16-12 and CLKN16-12 constant through the paging, koffset is 24 for A and 8 for B, knudge 0',
             'resp': 'the response is 625 us after the heard ID on Xprp, N = 0, CLKN16-12 frozen',
             'fhs': 'the FHS is on Xprc (CLKE frozen at the response, koffset, knudge, N = 1), whitened from that X, carries the master\'s clock',
             'ack': 'the acknowledgement is 625 us after the FHS on Xprp, N = 1',
             'times': 'response 625 us after the heard ID, FHS 1250 us after the first ID of the heard slot, ack 625 us and POLL 1250 us after the FHS',
             'followup': 'every follow-up packet is on the basic kernel: the master at its clock, the slave one slot later at its own (clk + 2)',
             'fields': 'LAPs, UAPs, LT_ADDR, types and the FHS fields are the generator\'s encoder\'s',
             'hopdict': 'every burst\'s complete hop record (clock, frozen clocks, N, koffset, knudge) is the independently derived one and gives its channel'}
    for k, text in lines.items():
        check(failed[k] == 0, '%s (%d of %d exchanges wrong%s)' % (text, failed[k], exchanges, ', first: ' + first_bad[k] if k in first_bad else ''))
    check(n_pairs > 0 and n_same < 0.1 * n_pairs,
          'a slave answer is on a channel of its own at its own clock: it differs from its master\'s on %d of %d pairs (not the adapted rule)'
          % (n_pairs - n_same, n_pairs))
    # both trains and both heard IDs occur
    if exchanges >= 8:
        check({E['train'] for E in side['exchanges']} == {'A', 'B'} and {E['heard_id'] for E in side['exchanges']} == {0, 1},
              'both trains and both the first and the second ID of a slot are the heard one')
    # the master's native CLK16-12 may change over the follow-up (the page and response clocks do not): counted here, not pinned
    chg = 0
    for e in range(exchanges):
        E = side['exchanges'][e]
        last = max(b['clk'] for _, b in ex[e] if b['kind'] in ('followup_master', 'followup_slave'))
        chg += kt.clk_field(E['master_clkn0'], 16, 12) != kt.clk_field(last, 16, 12)
        assert E['master_clk16_12_changes_in_followup'] == (kt.clk_field(E['master_clkn0'], 16, 12) != kt.clk_field(last, 16, 12))
    check(side['n_exchanges_master_clk16_12_changes'] == chg and 'master_clk16_12_changes_in_followup' in ' '.join(side['spec_notes']),
          'the master\'s native CLK16-12 changes over the follow-up in %d of %d exchanges (counted here; the sidecar and its note say it '
          'may, and that CLKE16-12 and the paged CLKN16-12 do not change in the paging)' % (chg, exchanges))
    clock_lock(side, lock_rows)


def clock_lock(side, rows):
    """The in-window lock against a search written here."""
    print('\nThe clock lock on the in-window packets: the whole 2**27 domain, by the test\'s own table')
    check_ok = dict(true_in=0, same=0, unique=0, count=0, nolist=0)
    n_unique = 0
    sizes = []
    for e, E, obs in rows:
        if not obs:
            check_ok['same'] += E['clock_solutions'] is None and E['n_clock_solutions'] is None
            check_ok['true_in'] += 1
            check_ok['unique'] += E['clock_unique'] is False
            check_ok['count'] += E['n_clock_observations'] == 0
            continue
        sols = full_domain_solutions(obs)
        true = E['master_clk_at_connection'] >> 1
        check_ok['true_in'] += true in sols and E['first_followup_clk27_1'] == true
        listed = E['clock_solutions']
        check_ok['same'] += (listed == sols) if len(sols) <= gen.MAX_LISTED_SOLUTIONS else listed is None
        check_ok['count'] += E['n_clock_solutions'] == len(sols) and E['n_clock_observations'] == len(obs)
        check_ok['unique'] += E['clock_unique'] is (len(sols) == 1)
        n_unique += len(sols) == 1
        sizes.append(len(sols))
        check_ok['nolist'] += 1
    n = len(rows)
    check(all(v == n for k, v in check_ok.items() if k != 'nolist'),
          'for each of %d exchanges the in-window search finds the true CLK[27:1] among its solutions and the sidecar\'s clock_solutions, '
          'n_clock_solutions, clock_unique and n_clock_observations are the same set and numbers %s' % (n, check_ok))
    if sizes:
        obs_n = [len(o) for _, _, o in rows]
        check(side['clock_unique_exchanges'] == n_unique and side['clock_lock_exchanges'] == len(sizes),
              'clock_unique_exchanges is %d of %d (%d in-window observations on average per exchange; solutions per exchange: max %d)'
              % (n_unique, len(sizes), float(np.mean(obs_n)), max(sizes)))
        check('is unique' in ' '.join([side['clock_lock_note']]) or 'unique (clock_unique) for %d of %d' % (n_unique, side['n_exchanges']) in side['clock_lock_note'],
              'the sidecar\'s clock_lock_note says it is unique for %d of %d exchanges' % (n_unique, side['n_exchanges']))


def check_inquiry(side, ex, exchanges):
    bursts = side['bursts']
    failed = {k: 0 for k in ('ids', 'order', 'scan', 'train', 'clocks', 'resp', 'fields', 'hopdict')}
    first_bad = {}
    last_n = {}
    n_prog = True
    for e in range(exchanges):
        E, items = side['exchanges'][e], ex[e]

        def bad(k, why=''):
            failed[k] += 1
            first_bad.setdefault(k, 'exchange %d %s' % (e, why))
        ids, ok = check_common_exchange(E, items, 'inquiry')
        kinds = [b['kind'] for _, b in items]
        if not ok or kinds != ['id_inquiry'] * len(ids) + ['fhs_inquiry_response']:
            bad('order', 'kinds %s' % kinds[-3:])
            continue
        i0, s0 = E['inquirer_clkn0'], E['scanner_clkn0']
        ko, kn, n_c = E['koffset'], E['knudge'], E['scanner_n']
        f = items[-1][1]
        if not (i0 % 4 == 0 and s0 % 4 == 0 and clk_frozen_same_16_12(i0, 80) and clk_frozen_same_16_12(s0, 80)
                and ko == (24 if E['train'] == 'A' else 8) and kn == 0):
            bad('clocks')
        dd = (kt.clk_field(s0, 16, 12) + n_c - kt.clk_field(i0, 16, 12)) % 32
        if not ((dd >= 24 or dd <= 7) if E['train'] == 'A' else 8 <= dd <= 23) or dd != E['d_scanner_x_minus_inquirer_clkn16_12']:
            bad('train', 'd = %d' % dd)
        if not all(b['channel'] == kt.independent('f', 0, 0, clkn=i0 + b['tick'], koffset=ko, knudge=kn) and b['lap'] == GIAC
                   and b['hop_clk'] == i0 + b['tick'] for b in ids):
            bad('ids')
        scan = kt.independent('c', 0, 0, clkn=s0, n=n_c)
        heard_b = [b for b in ids if b['tick'] == E['heard_tick']]
        own = train_channels(i0, 0, 0, ko, native=True)
        other = train_channels(i0, 0, 0, 8 if ko == 24 else 24, native=True)
        held = all(kt.independent('c', 0, 0, clkn=s0 + t, n=n_c) == scan for t in range(0, 80))
        if not (scan == E['scan_channel'] and held and len(heard_b) == 1 and heard_b[0]['channel'] == scan
                and not any(b['channel'] == scan for b in ids if b['tick'] < E['heard_tick']) and scan in own and scan not in other):
            bad('scan', 'scan %s heard %s' % (scan, [b['channel'] for b in heard_b]))
        s_clk = s0 + E['heard_tick'] + 2
        want = kt.independent('k', 0, 0, clkn=s_clk, n=n_c)
        wx = kt.control_words('k', 0, clkn=s_clk, n=n_c)['X']
        if not (f['tick'] == E['heard_tick'] + 2 and f['channel'] == want and f['clk'] == s_clk and f['hop_clk'] == s_clk
                and f['fhs']['whitening_x'] == wx == E['fhs_whitening']['xir'] and f['fhs']['clk27_2'] == s_clk >> 2
                and s_clk % 4 in (2, 3) and f['start_sample'] - heard_b[0]['start_sample'] == SLOT):
            bad('resp', 'channel %s want %s' % (f['channel'], want))
        if not (f['lap'] == GIAC and f['uap_for_hec'] == 0 and f['fhs']['header_uap'] == 0 and f['fhs']['am_addr'] == 0
                and f['fhs']['lt_addr_header'] == 0 and f['fhs']['eir'] == 0 and f['fhs']['sp'] == 2
                and old.rebuild_fhs_ok(f, 0, GIAC)):
            bad('fields')
        if not hop_records_ok(E, items):
            bad('hopdict')
        idx = E['fhs_whitening']['scanner_index']
        if idx in last_n:
            n_prog = n_prog and n_c == last_n[idx] + 1
        last_n[idx] = n_c
    lines = {'ids': 'every inquiry ID is the inquiry kernel (EQ 7, GIAC, Y1 = CLKN1) at the inquirer\'s CLKN, on the half-slot grid',
             'order': 'IDs two per TX slot 312.5 us apart then one FHS response and nothing after',
             'scan': 'the scan frequency (Xir at the scanner\'s N, constant) is the channel of the heard half-slot, of no earlier one, in the train sent and not in the other',
             'train': 'the scanner\'s X is in the train\'s window against the inquirer\'s CLKN16-12',
             'clocks': 'clocks are multiples of 4, CLK16-12 constant, koffset 24 for A and 8 for B',
             'resp': 'the FHS is 625 us after the heard ID on Xir, Y1 = 1, N the scanner\'s counter, whitened from that Xir',
             'fields': 'the FHS fields, the GIAC and the DCI',
             'hopdict': 'every burst\'s complete hop record (clock, N, koffset, knudge) is the independently derived one and gives its channel'}
    for k, text in lines.items():
        check(failed[k] == 0, '%s (%d of %d exchanges wrong%s)' % (text, failed[k], exchanges, ', first: ' + first_bad[k] if k in first_bad else ''))
    check(n_prog, 'a scanning device that answers again has its counter N one higher (%d devices)' % len(last_n))
    check(side['clock_unique_exchanges'] is None, 'an inquiry file has no clock lock')


# --- reading a recording the way a receiver has to --------------------------------------

#: channel c at (c - 39) MHz from the centre; bins of an 80-sample frame are 500 kHz apart.
CHANNELS = list(range(WINDOW[0], WINDOW[1] + 1))
BINS = [(2 * (c - 39)) % 80 for c in CHANNELS]


def channel_power(iq, block=1 << 21):
    """Power of each channel in the window per 80-sample frame: the three 500 kHz bins around its carrier, ``(frames, 25)``."""
    n = len(iq) // 80
    out = np.empty((n, len(CHANNELS)), dtype=np.float32)
    for lo in range(0, n, block // 80):
        hi = min(n, lo + block // 80)
        x = np.asarray(iq[lo * 80:hi * 80]).reshape(-1, 80)
        p = np.abs(np.fft.fft(x, axis=1)) ** 2
        for k, b in enumerate(BINS):
            out[lo:hi, k] = p[:, (b - 1) % 80] + p[:, b] + p[:, (b + 1) % 80]
    return out


def blind_detect(iq):
    """The bursts a receiver of the window's channels would find: ``(first sample, last sample, channel)``, rough to a few hundred
    samples. Each channel's power is smoothed over 16 frames (32 us) and divided by its own noise level (the median of the
    chi-square of 6 degrees of freedom is 0.891 of its mean); a burst is where the best channel is over twice the noise for at
    least 12 frames."""
    p = channel_power(iq)
    z = p / (np.median(p, axis=0) / 0.8913)
    c = np.cumsum(np.vstack([np.zeros((1, z.shape[1]), dtype=np.float64), z]), axis=0)
    zs = (c[16:] - c[:-16]) / 16.0
    best = zs.max(axis=1)
    up = best > 2.0
    edges = np.flatnonzero(np.diff(up.astype(np.int8)))
    starts, ends = edges[::2] + 1, edges[1::2] + 1
    if len(up) and up[0]:
        starts = np.concatenate([[0], starts])
    segs = []
    for a, b in zip(starts.tolist(), ends.tolist()):
        if segs and a - segs[-1][1] < 8:
            segs[-1][1] = b
        else:
            segs.append([a, b])
    out = []
    for a, b in segs:
        if b - a < 12:
            continue
        ch = CHANNELS[int(np.argmax(zs[a:b].sum(axis=0)))]
        out.append((a * 80 + 8 * 80, b * 80 + 8 * 80, ch))
    return out


def noise_level(iq):
    """The noise power per sample from the file alone: the median of the mean power of 4096-sample blocks."""
    n = len(iq) // 4096
    x = np.asarray(iq[:n * 4096]).reshape(n, 4096)
    return float(np.median(np.mean(x.real.astype(np.float64) ** 2 + x.imag.astype(np.float64) ** 2, axis=1)))


def bits_from_samples(iq, start, ch, nb, expected_head):
    """The ``nb`` bits of a burst on ``ch`` that starts at sample ``start``: shifted to baseband, 600 kHz low-pass, the phase change
    over each symbol centred on it, sliced at the threshold the 68 access code bits give."""
    taps = firwin(401, 0.6e6, fs=FS)
    lo, hi = int(start) - 1500, int(start) + nb * SPS + 2500
    n = np.arange(lo, hi)
    cyc = (2402.0 + ch - CENTER) * 1e6 / FS * n
    x = lfilter(taps, 1, np.asarray(iq[lo:hi]) * np.exp(-2j * np.pi * (cyc - np.floor(cyc))))[200:]
    c = (start - lo) + (np.arange(nb) + 0.5) * SPS
    a, b = np.round(c - SPS / 2).astype(int), np.round(c + SPS / 2).astype(int)
    d = np.angle(x[b] * np.conj(x[a]))
    ref = np.array(expected_head) * 2 - 1
    _, thr = np.polyfit(ref, d[:len(ref)], 1)
    bits = (d > thr).astype(int)
    if np.mean(bits[:len(ref)] == np.array(expected_head)) < 0.5:
        bits = 1 - bits
    return bits


#: The correlation peak of ``find_access`` is at the burst's start sample plus its timing_frac plus this, on average: found
#: without noise on 235 bursts as -0.62 (std 0.29: the peak is a whole sample, the start a fraction).
DETECT_BIAS = -0.62
TAPS6 = firwin(401, 0.6e6, fs=FS)


def find_access(iq, rough0, rough1, ch, laps):
    """Where a burst's access code is, from the samples alone: shifted to baseband from ``ch``, 600 kHz low-pass, the phase change over
    40 samples centred on each sample correlated with what each candidate LAP's 68 bits would give. Returns ``(start, lap, r)``;
    the start is the correlation peak, a whole sample, in the sample numbers of the file."""
    lo, hi = max(0, rough0 - 1500), min(len(iq), rough1 + 2500)
    n = np.arange(lo, hi)
    cyc = (2402.0 + ch - CENTER) * 1e6 / FS * n
    x = lfilter(TAPS6, 1, np.asarray(iq[lo:hi]) * np.exp(-2j * np.pi * (cyc - np.floor(cyc))))[200:]
    d = np.zeros(len(x))
    d[20:-20] = np.angle(x[40:] * np.conj(x[:-40]))
    best = None
    for lap in laps:
        tm = np.convolve(np.repeat(np.array(bt_fhs.id_bits(lap)) * 2.0 - 1, SPS), np.ones(SPS) / SPS, 'same')
        tm = tm - tm.mean()
        c = fftconvolve(d, tm[::-1], 'valid')
        w = np.sqrt(fftconvolve(d ** 2, np.ones(len(tm)), 'valid')) * np.linalg.norm(tm)
        r = c / np.maximum(w, 1e-12)
        p = int(np.argmax(r))
        if best is None or r[p] > best[2]:
            best = (lo + p, lap, float(r[p]))
    return best


def measure_blind(iq, laps):
    """Find every burst blind and read its start, carrier channel and access code."""
    out = []
    for s0, s1, ch in blind_detect(iq):
        start, lap, r = find_access(iq, s0, s1, ch, laps)
        if r < 0.5:
            out.append(dict(rough=(s0, s1), channel=ch, start=None, lap=None, r=r, mhz=float('nan'), mhz_channel=-1))
            continue
        mhz, ch2 = old.carrier_of(iq, start - 250, start + 2720 - 250)
        out.append(dict(rough=(s0, s1), channel=ch, start=start - DETECT_BIAS, lap=lap, r=r, mhz=mhz, mhz_channel=ch2))
    return out


def burst_snr_db(iq, start, n, noise_power):
    """The burst's SNR in 1 MHz from its own samples: mean power of the middle less the noise floor, over the noise in 1 MHz."""
    x = np.asarray(iq[int(start) + 400:int(start) + n * SPS - 400])
    p = float(np.mean(x.real.astype(np.float64) ** 2 + x.imag.astype(np.float64) ** 2)) - noise_power
    return 10 * np.log10(max(p, 1e-30) / (noise_power / (FS / 1e6)))


def check_samples(iq, side, label):
    print('\nThe samples of %s: %d bursts, %d in the window' % (label, len(side['bursts']), side['n_bursts_in_window']))
    bursts = side['bursts']
    rendered = [b for b in bursts if b['rendered']]
    absent = [b for b in bursts if not b['rendered']]
    check(len(iq) == side['n_samples'], 'the file holds n_samples = %d samples' % len(iq))
    noise = noise_level(iq)
    check(abs(10 * np.log10(noise / NOISE_POWER)) < 0.1, 'the noise floor measured in the file is %+.3f dB from the one floor' % (10 * np.log10(noise / NOISE_POWER)))
    laps = sorted({PAGED_LAP, M_LAP, GIAC})
    got = measure_blind(iq, laps)
    check(len(got) == len(rendered), 'blind: %d bursts found on channels %d-%d, the sidecar has %d rendered' % ((len(got),) + WINDOW + (len(rendered),)))
    if len(got) != len(rendered) or any(g['start'] is None for g in got):
        check(False, 'an access code is found in every burst found (the rest is not read)')
        return None
    check(True, 'an access code of the three LAPs is found in every burst')
    check([g['lap'] for g in got] == [b['lap'] for b in rendered], 'the LAP the access code correlates best with is the sidecar\'s, for all %d' % len(rendered))
    check([g['channel'] for g in got] == [b['channel'] for b in rendered],
          'every burst\'s carrier channel (the channelizer\'s) is the sidecar\'s (%d of %d)'
          % (sum(g['channel'] == b['channel'] for g, b in zip(got, rendered)), len(rendered)))
    spec = [(g['mhz'] - b['channel_mhz']) * 1e3 for g, b in zip(got, rendered) if g['mhz_channel'] >= 0]
    check(len(spec) > 0 and np.abs(spec).max() < 200 and all(g['mhz_channel'] == b['channel'] for g, b in zip(got, rendered) if g['mhz_channel'] >= 0),
          'the spectrum\'s centroid of %d bursts is within %.0f kHz of the channel (a channel is 1000) and rounds to it' % (len(spec), np.abs(spec).max()))
    off = np.array([g['start'] - b['start_sample'] - b['timing_frac'] for g, b in zip(got, rendered)])
    sn = np.array([b['snr_db'] for b in rendered])
    check(np.abs(off).max() <= 5.0 and np.abs(off[sn >= 15]).max(initial=0) <= 3.0 and abs(off.mean()) <= 0.8,
          'start_sample + timing_frac is where the access code is, from the samples alone: worst %.1f samples off (%.1f at 15 dB and over), mean %+.2f'
          % (np.abs(off).max(), np.abs(off[sn >= 15]).max(initial=0), off.mean()))
    snr = np.array([burst_snr_db(iq, g['start'], nbits(b), noise) for g, b in zip(got, rendered)])
    err = snr - np.array([b['snr_db'] for b in rendered])
    # the power of a burst of n independent samples at per-sample SNR s has a relative spread sqrt((1 + 2 s) / n) / s of its signal part
    s_lin = 10 ** (np.array([b['snr_db'] for b in rendered]) / 10) / (FS / 1e6)
    n_s = np.array([nbits(b) * SPS - 800 for b in rendered])
    sigma = 4.343 * np.sqrt((1 + 2 * s_lin) / n_s) / s_lin
    z = err / sigma
    long_ = np.array([nbits(b) >= 126 for b in rendered])
    check(np.abs(z).max() < 4.5 and 0.6 < np.sqrt(np.mean(z ** 2)) < 1.4 and abs(err.mean()) < 0.15,
          'the levels from the samples (power less the noise floor) are the sidecar\'s snr_db to the noise of the measurement: worst %.2f dB '
          '(IDs), %.2f dB (longer packets), %.1f sigma at most (limit 4.5), rms %.2f sigma, mean %+.3f dB'
          % (np.abs(err[~long_]).max(initial=0), np.abs(err[long_]).max(initial=0), np.abs(z).max(), np.sqrt(np.mean(z ** 2)), err.mean()))
    length = np.array([g['rough'][1] - g['rough'][0] for g in got]) - np.array([nbits(b) * SPS for b in rendered])
    check(np.abs(length).max() < 1500, 'each burst\'s length in the channelizer is its packet\'s (68, 366, 126 or 126 + payload bits): worst %d samples off' % np.abs(length).max())
    # the unrendered bursts are absent: their spans hold the noise floor and nothing else
    worst = 0.0
    n_bad = 0
    for b in absent:
        n = nbits(b)
        x = np.asarray(iq[b['start_sample'] + 300:b['start_sample'] + n * SPS - 300])
        p = float(np.mean(x.real.astype(np.float64) ** 2 + x.imag.astype(np.float64) ** 2)) / NOISE_POWER
        sigma = 1 / np.sqrt(len(x))
        worst = max(worst, abs(p - 1) / sigma)
        n_bad += abs(p - 1) > 5 * sigma
    check(len(absent) > 0 and n_bad == 0, '%d unrendered bursts are absent: their spans hold the noise floor (worst %.1f sigma of its mean from 1, limit 5)'
          % (len(absent), worst))
    # the relations of the text on the measured starts
    relations(got, rendered, side)
    return got, rendered


def relations(got, rendered, side):
    """The intervals of the text between two bursts that are both in the window, measured."""
    idx = {id(b): k for k, b in enumerate(rendered)}
    byx = {}
    for k, b in enumerate(rendered):
        byx.setdefault((b['exchange'], b['tick'], b['kind']), k)
    rel = {'second ID 312.5 us after the first': [], 'response 625 us after the heard ID': [],
           'FHS 1250 us after the first ID of the heard slot': [], 'acknowledgement 625 us after the FHS': [],
           'POLL 1250 us after the FHS': [], 'slave answer 625 us after its master': [], 'inquiry response 625 us after the heard ID': [],
           'next slot\'s first ID 1250 us after': []}
    for E in side['exchanges']:
        e = E['exchange']
        kinds = ('id_page', 'id_inquiry')
        g = lambda tick, *ks: next((byx[(e, tick, k)] for k in ks if (e, tick, k) in byx), None)  # noqa: E731
        for s in range(E['n_id_slots']):
            a, b = g(4 * s, *kinds), g(4 * s + 1, *kinds)
            if a is not None and b is not None:
                rel['second ID 312.5 us after the first'].append((a, b, TICK))
            nxt = g(4 * s + 4, *kinds)
            if a is not None and nxt is not None:
                rel['next slot\'s first ID 1250 us after'].append((a, nxt, 2 * SLOT))
        heard = g(E['heard_tick'], *kinds)
        if E['kind'] == 'page':
            resp, fhs, ack = g(E['heard_tick'] + 2, 'id_response'), g(E['fhs_tick'], 'fhs'), g(E['fhs_tick'] + 2, 'id_ack')
            first = g(4 * E['heard_slot'], *kinds)
            if heard is not None and resp is not None:
                rel['response 625 us after the heard ID'].append((heard, resp, SLOT))
            if first is not None and fhs is not None:
                rel['FHS 1250 us after the first ID of the heard slot'].append((first, fhs, 2 * SLOT))
            if fhs is not None and ack is not None:
                rel['acknowledgement 625 us after the FHS'].append((fhs, ack, SLOT))
            poll = g(E['poll_tick'], 'followup_master')
            if fhs is not None and poll is not None:
                rel['POLL 1250 us after the FHS'].append((fhs, poll, 2 * SLOT))
            for k, b in enumerate(rendered):
                if b['exchange'] == e and b['kind'] == 'followup_master':
                    sl = g(b['tick'] + 2, 'followup_slave')
                    if sl is not None:
                        rel['slave answer 625 us after its master'].append((k, sl, SLOT))
        else:
            resp = g(E['heard_tick'] + 2, 'fhs_inquiry_response')
            if heard is not None and resp is not None:
                rel['inquiry response 625 us after the heard ID'].append((heard, resp, SLOT))
    for name, rows in rel.items():
        if not rows:
            continue
        d = np.array([got[j]['start'] - got[i]['start'] - nominal - (rendered[j]['timing_frac'] - rendered[i]['timing_frac'])
                      for i, j, nominal in rows])
        hi = np.array([min(rendered[i]['snr_db'], rendered[j]['snr_db']) >= 15 for i, j, nominal in rows])
        check(np.abs(d).max() <= 8 and np.abs(d[hi]).max(initial=0) <= 4.5 and (len(d) < 10 or abs(d.mean()) <= 0.8),
              '%s: %d measured, worst %+.2f samples off (%+.2f where both are at 15 dB or over), mean %+.2f'
              % (name, len(rows), d[np.argmax(np.abs(d))], d[hi][np.argmax(np.abs(d[hi]))] if hi.any() else 0.0, d.mean()))


#: The SNR above which a receiver made of a differential-phase slicer must read every FHS and every header (below it some fail:
#: the bits are the same, the noise is the SNR the task asked for, 10 to 20 dB in 1 MHz; the counts are printed).
FHS_DECODE_DB = 16.0
HDR_DECODE_DB = 12.5


def check_decodes(iq, side, got, rendered, label, noiseless):
    """FHS and follow-up headers through our parser and libbtbb, from the bits read off the samples."""
    fhs_i = [k for k, b in enumerate(rendered) if b['ptype'] == 'FHS']
    hdr_i = [k for k, b in enumerate(rendered) if b['ptype'] in ('POLL', 'NULL', 'DH1', 'DM1')]
    stats = dict(fhs_ok=0, fhs_lo=0, fhs_lo_ok=0, fhs_hi=0, hdr_ok=0, hdr_n=0, hdr_hi=0, hdr_hi_ok=0, pay_hi=0, pay_hi_ok=0)
    wrong_uap = 0
    bad_fhs = []
    for k in fhs_i:
        b, g = rendered[k], got[k]
        f = b['fhs']
        exp = old.expected_bits(b)
        bits = bits_from_samples(iq, g['start'], b['channel'], 366, exp[:68])
        try:
            r = bt_fhs.parse_fhs_air_bits(bits, f['header_uap'], f['tx_clk'])
        except ValueError:                                   # a FEC 2/3 block with two bits wrong
            r = dict(crc_ok=False, header_ok=False, lap=None, uap=None, nap=None, clk27_2=None)
        ours = (r['crc_ok'] and r['header_ok'] and r['lap'] == f['lap'] and r['uap'] == f['uap'] and r['nap'] == f['nap']
                and r['clk27_2'] == f['clk27_2'])
        d = old.btbb_fhs(list(bits), f['header_uap'], f['tx_clk']) if old._lib is not None else None
        theirs = d is not None and d['verdict'] == 1000 and d['lap'] == f['lap'] and d['uap'] == f['uap'] and d['clk27_2'] == f['clk27_2']
        ok = ours and (theirs or old._lib is None)
        hi = b['snr_db'] >= FHS_DECODE_DB or noiseless
        stats['fhs_hi'] += hi
        stats['fhs_ok'] += ok
        if hi and not ok:
            bad_fhs.append((k, b['snr_db']))
        if not hi:
            stats['fhs_lo'] += 1
            stats['fhs_lo_ok'] += ok
        if old._lib is not None and d is not None and d['verdict'] == 1000:
            other = f['uap'] if f['header_uap'] != f['uap'] else (f['uap'] ^ 0x10)
            wrong_uap += old.btbb_fhs(list(bits), other, f['tx_clk']) is not None
    if fhs_i:
        check(not bad_fhs, '%s: the %d in-window FHS decode through our parser and libbtbb (verdict ok), at their HEC/CRC UAP and whitening clock, '
              'to the sidecar\'s fields%s%s' % (label, len(fhs_i), (' (SNR >= %g dB only: %d of %d decode; below it %d of %d)' % (
                  FHS_DECODE_DB, stats['fhs_ok'] - stats['fhs_lo_ok'], stats['fhs_hi'], stats['fhs_lo_ok'], stats['fhs_lo'])) if not noiseless else '',
                                                                 '; failing: %s' % bad_fhs[:4] if bad_fhs else ''))
        if old._lib is not None:
            check(wrong_uap == 0, '... and libbtbb refuses each at another UAP (%d accepted)' % wrong_uap)
    if hdr_i and old._lib is not None:
        for k in hdr_i:
            b, g = rendered[k], got[k]
            exp = old.expected_bits(b)
            n = nbits(b)
            bits = bits_from_samples(iq, g['start'], b['channel'], n, exp[:68])
            res = bt_ota_check.check_btbb(old._lib, np.array(bits), b, b['uap_for_hec'])
            hi = b['snr_db'] >= HDR_DECODE_DB or noiseless
            stats['hdr_n'] += 1
            stats['hdr_ok'] += res[0]
            if hi:
                stats['hdr_hi'] += 1
                stats['hdr_hi_ok'] += res[0]
                if b['ptype'] in ('DH1', 'DM1'):
                    stats['pay_hi'] += 1
                    stats['pay_hi_ok'] += all(res)
            else:
                pass
        check(stats['hdr_hi_ok'] == stats['hdr_hi'] and (not noiseless or stats['pay_hi_ok'] == stats['pay_hi']),
              '%s: the %d follow-up packets in the window decode through libbtbb at the master UAP and their own clk: %d of %d headers%s; %d of %d DH1/DM1 payloads'
              % (label, stats['hdr_n'], stats['hdr_hi_ok'], stats['hdr_hi'], '' if noiseless else ' at >= %g dB (%d of %d at any SNR)' % (HDR_DECODE_DB, stats['hdr_ok'], stats['hdr_n']),
                 stats['pay_hi_ok'], stats['pay_hi']))
        wrong = sum(bt_ota_check.check_btbb(old._lib, np.array(bits_from_samples(iq, got[k]['start'], rendered[k]['channel'], nbits(rendered[k]),
                                                                          old.expected_bits(rendered[k])[:68])), rendered[k],
                                            rendered[k]['uap_for_hec'] ^ 1)[0] for k in hdr_i[:20])
        check(wrong == 0, 'and libbtbb refuses their headers at another UAP (%d of %d accepted)' % (wrong, min(20, len(hdr_i))))
    return stats


# --- determinism, arguments, the delivered set ----------------------------------------

def check_determinism():
    print('\nDeterminism, arguments and the command line')
    a, sa = gen.synthesise_conf(kind='page', exchanges=2, followup=6, seed=9301)
    b, sb = gen.synthesise_conf(kind='page', exchanges=2, followup=6, seed=9301)
    c, _ = gen.synthesise_conf(kind='page', exchanges=2, followup=6, seed=9302)
    check(a.tobytes() == b.tobytes() and json.dumps(sa) == json.dumps(sb), 'the same arguments give the same samples and sidecar')
    check(a.tobytes() != c.tobytes() and a.dtype == np.complex64, 'another seed gives another file; complex64')
    z1, _ = gen.synthesise_conf(kind='page', exchanges=2, followup=6, seed=9301, noise=False)
    z2, _ = gen.synthesise_conf(kind='page', exchanges=2, followup=6, seed=9301, noise=False, block=1 << 19)
    check(z1.tobytes() == z2.tobytes() and z1.any(), 'the bursts alone are the same bytes however the blocks fall')
    check([n for n, _ in gen.CONF_SET] == ['conf_page_hop_win', 'conf_inq_hop_win'] and [s['seed'] for _, s in gen.CONF_SET] == [9001, 9002]
          and [s['exchanges'] for _, s in gen.CONF_SET] == [200, 200], '--set conf is the two files, 200 exchanges each, seeds 9001 and 9002')
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        orig = gen.CONF_SET[:]
        gen.SETS['conf'][:] = [(n, dict(s, exchanges=2, followup=min(s['followup'], 6))) for n, s in orig]
        try:
            gen.main(['--set', 'conf', '--out', d])
        finally:
            gen.SETS['conf'][:] = orig
        names = sorted(os.listdir(d))
        check(names == ['synth_conf_inq_hop_win.cf32', 'synth_conf_inq_hop_win.json', 'synth_conf_page_hop_win.cf32',
                        'synth_conf_page_hop_win.json'], '--set conf writes exactly the two names: %s' % names)
        gen.main(['single', '--kind', 'page', '--exchanges', '2', '--followup', '6', '--seed', '9001', '--out', d])
        one = open(os.path.join(d, 'synth_single.cf32'), 'rb').read()
        two = open(os.path.join(d, 'synth_conf_page_hop_win.cf32'), 'rb').read()
        check(one == two, 'a single run with the same arguments is byte-identical to the set\'s file (one code path)')
        side = json.load(open(os.path.join(d, 'synth_conf_page_hop_win.json')))
        check(side['n_samples'] * 8 == os.path.getsize(os.path.join(d, 'synth_conf_page_hop_win.cf32')),
              'n_samples times 8 bytes is the .cf32\'s size')
    rc = 0
    try:
        with contextlib.redirect_stderr(open(os.devnull, 'w')):
            gen.main(['--set', 'nonsense'])
    except SystemExit as e:
        rc = e.code
    check(rc not in (0, None), 'an unknown set exits non-zero (%s)' % rc)
    for kw in (dict(kind='sideways'), dict(exchanges=0), dict(followup=0), dict(fs=20e6), dict(idle_probs=[0.5, 0.5, 0.5])):
        try:
            gen.plan_conf(**dict(dict(exchanges=2, followup=4), **kw))
            check(False, 'bad argument %r is refused' % kw)
        except ValueError:
            check(True, 'bad argument %r is refused with ValueError' % kw)


def check_statistics():
    print('\nStatistics of a larger plan: the channels are the kernels\' own')
    side, _, _ = gen.plan_conf(kind='page', exchanges=600, followup=2, seed=9401)
    seg = sorted({kt.independent('a', PAGED_LAP, PAGED_UAP, clkn=x << 12) for x in range(32)})
    fh = [b['channel'] for b in side['bursts'] if b['ptype'] == 'FHS']
    ids = [b['channel'] for b in side['bursts'] if b['kind'] == 'id_page']
    check(set(fh) <= set(seg) and set(ids) <= set(seg), 'every FHS and page ID channel lies in the paged device\'s 32-frequency segment (%d channels, %d in the window)'
          % (len(seg), sum(WINDOW[0] <= c <= WINDOW[1] for c in seg)))
    counts = np.array([fh.count(c) for c in seg])
    chi = float(np.sum((counts - len(fh) / 32) ** 2 / (len(fh) / 32)))
    bases = [E['snr_base_db'] for E in side['exchanges']]
    check(min(bases) < 10.2 and max(bases) > 19.8 and abs(np.mean(bases) - 15.0) < 0.5,
          'the base SNR of 600 exchanges covers 10 to 20 dB (%.1f to %.1f, mean %.2f)' % (min(bases), max(bases), np.mean(bases)))
    check(chi < 31 + 4 * np.sqrt(2 * 31), 'the %d FHS channels are uniform over that segment, as the kernel gives (chi-square %.1f, 31 dof, limit %.0f)'
          % (len(fh), chi, 31 + 4 * np.sqrt(2 * 31)))
    in_seg = [c for c in seg if WINDOW[0] <= c <= WINDOW[1]]
    centre = 39
    n_centre = fh.count(centre)
    exp_centre = len(fh) / 32 if centre in seg else 0
    check(abs(n_centre - exp_centre) < 4 * np.sqrt(max(exp_centre, 1)) + 1,
          'no centre-channel bias: channel 39 carries %d of %d FHS (the kernel\'s segment %s it, %.1f expected)'
          % (n_centre, len(fh), 'holds' if centre in seg else 'does not hold', exp_centre))
    fhs_in = sum(side['bursts'][E['fhs_burst']]['rendered'] for E in side['exchanges'])
    expect = len(in_seg) / 32
    check(abs(fhs_in / 600 - expect) < 4 * np.sqrt(expect * (1 - expect) / 600), 'the FHS is in the window in %d of 600 exchanges (%.1f %%, the segment says %.1f %%)'
          % (fhs_in, 100 * fhs_in / 600, 100 * expect))
    iside, _, _ = gen.plan_conf(kind='inquiry', exchanges=600, followup=0, seed=9402)
    gseg = sorted({kt.independent('c', 0, 0, clkn=x << 12) for x in range(32)})          # Y1 = 0: the IDs
    gseg1 = sorted({kt.independent('k', 0, 0, clkn=x << 12, n=0) for x in range(32)})    # Y1 = 1: the response
    ic = [b['channel'] for b in iside['bursts'] if b['ptype'] == 'FHS']
    iid = [b['channel'] for b in iside['bursts'] if b['ptype'] == 'ID']
    check(set(iid) <= set(gseg) and set(ic) <= set(gseg1),
          'every inquiry ID lies in the GIAC\'s 32-frequency segment of Y1 = 0 (%d channels, %d in the window) and every FHS in that of Y1 = 1 '
          '(%d channels, %d in the window)' % (len(gseg), sum(WINDOW[0] <= c <= WINDOW[1] for c in gseg), len(gseg1),
                                              sum(WINDOW[0] <= c <= WINDOW[1] for c in gseg1)))
    hist = np.array([ic.count(c) for c in gseg1])
    chi = float(np.sum((hist - len(ic) / 32) ** 2 / (len(ic) / 32)))
    check(chi < 31 + 4 * np.sqrt(2 * 31), 'the %d inquiry FHS channels are uniform over that segment (chi-square %.1f)' % (len(ic), chi))
    check(side['n_exchanges_fhs_in_window'] < 0.6 * 600 and iside['n_exchanges_fhs_in_window'] < 0.8 * 600,
          'the FHS is outside the window in %d %% of page and %d %% of inquiry exchanges' % (
              100 - round(100 * side['n_exchanges_fhs_in_window'] / 600), 100 - round(100 * iside['n_exchanges_fhs_in_window'] / 600)))


# --- mutants -------------------------------------------------------------------------

SRC = open(gen.__file__).read()


#: The generator's own assertion that the true clock is among its solutions: a mutant that breaks the follow-up would trip it, and
#: the test is to catch the mistake, not the assertion.
LOCK_ASSERT = "assert E['first_followup_clk27_1'] in lock, 'the true clock is not among its own solutions'"


def mutant_module(old_text, new_text, also=()):
    text = SRC
    for o, n in ((old_text, new_text),) + tuple(also):
        assert text.count(o) == 1, 'mutation target not unique: %r (%d)' % (o, text.count(o))
        text = text.replace(o, n)
    spec = importlib.util.spec_from_file_location('bt_synth_conf_mut', gen.__file__)
    mod = importlib.util.module_from_spec(spec)
    exec(compile(text, gen.__file__, 'exec'), mod.__dict__)
    mod._TABLE_CACHE = gen._TABLE_CACHE
    return mod


MUT_ID = "ch = hs.page(e0 + t, paged_lap, paged_uap, ko, knudge)"
MUT_FOLLOW = "ch = int(bt_hop.hop_channel(clk, master_addr, None))   # the kernel at this packet's own clock"
MUTANTS = [
    ('free channels for the page IDs, not the kernel', MUT_ID, "ch = 27 + (t * 5 + 3 * e) % 25", 'page', 'plan'),
    ('adapted map for the follow-up', MUT_FOLLOW,
     "ch = int(bt_hop.hop_channel(clk & ~2, master_addr, sum(1 << c for c in range(31, 51))))", 'page', 'plan', [(LOCK_ASSERT, 'pass')]),
    ('the heard slot one half-slot off', "heard_slot, heard = heard_tick // 4, heard_tick % 4\n            n_slots = heard_slot + 1\n            for s in range(n_slots):\n                for j in (0, 1):\n                    t = 4 * s + j\n                    ch = hs.page",
     "heard_tick += 1 if heard_tick % 4 == 0 else -1\n            heard_slot, heard = heard_tick // 4, heard_tick % 4\n            n_slots = heard_slot + 1\n            for s in range(n_slots):\n                for j in (0, 1):\n                    t = 4 * s + j\n                    ch = hs.page", 'page', 'plan'),
    ('the A train for a B-train exchange', MUT_ID, "ch = hs.page(e0 + t, paged_lap, paged_uap, 24, knudge)", 'page', 'plan'),
    ('page hop record clke altered (channel unchanged)', "hop=dict(substate='page', clke=e0 + t, koffset=ko, knudge=knudge)",
     "hop=dict(substate='page', clke=e0 + t + 4, koffset=ko, knudge=knudge)", 'page', 'plan'),
    ('first response N 0 -> 1', "n_resp = hs.n_of(s0 + resp_tick, s0 + 4 * heard_slot)", "n_resp = hs.n_of(s0 + resp_tick, s0 + 4 * heard_slot) + 1", 'page', 'plan'),
    ('Xprc with N = 0', "n_fhs = 1\n", "n_fhs = 0\n", 'page', 'plan'),
    ('a wrong CLKE offset in the heard-slot derivation', "s0 = (((hi_e + d) & 0xFFFF) << 12) | low12()", "s0 = (((hi_e + d + 2) & 0xFFFF) << 12) | low12()", 'page', 'plan'),
    ('the slave answer on the master\'s channel', MUT_FOLLOW, "ch = int(bt_hop.hop_channel(clk_m, master_addr, None))", 'page', 'plan',
     [(LOCK_ASSERT, 'pass')]),
    ('out-of-window bursts rendered', "        if win:\n            jobs.append(", "        if True:\n            jobs.append(", 'page', 'samples'),
    ('SNR one dB off', "amp=amplitude(snr),", "amp=amplitude(snr + 1.0),", 'page', 'samples'),
]


def check_mutants():
    print('\nMutants: one mistake each, in a copy of the generator, and what catches it')
    for name, old_text, new_text, kind, how, *rest in MUTANTS:
        mod = mutant_module(old_text, new_text, rest[0] if rest else ())
        with quiet() as failed:
            try:
                if how == 'plan':
                    side, _, _ = mod.plan_conf(kind=kind, exchanges=8, followup=24, seed=9501)
                    check_plan(side, kind, 8, 24, 'mutant', controls=False)
                else:
                    iq, side = mod.synthesise_conf(kind=kind, exchanges=3, followup=10, seed=9502)
                    check_samples(iq, side, 'mutant')
            except Exception as e:                              # a crash is also a catch, said so
                failed.append('raised %s: %s' % (type(e).__name__, str(e)[:60]))
        short = sorted({w.split(':')[0][:70] for w in failed})
        check(len(failed) > 0, 'mutant "%s" is caught by %d checks, e.g. %s' % (name, len(failed), '; '.join(short[:3])))


# --- a real file -----------------------------------------------------------------------

def run_file(path, exch):
    side = json.load(open(os.path.splitext(path)[0] + '.json'))
    mm = np.memmap(path, dtype='<c8', mode='r')
    print('%s: %d samples, %d bursts, %d exchanges' % (path, len(mm), side['n_bursts'], side['n_exchanges']))
    check(len(mm) == side['n_samples'], 'the file holds n_samples = %d samples' % len(mm))
    for e in exch:
        E = side['exchanges'][e]
        lo = max(0, E['start_sample'] - 20000)
        hi = min(len(mm), side['bursts'][E['last_burst']]['start_sample'] + 40000)
        iq = np.array(mm[lo:hi])
        bs = []
        for i in range(E['first_burst'], E['last_burst'] + 1):
            b = dict(side['bursts'][i])
            b['start_sample'] -= lo
            bs.append(b)
        cs = dict(side, bursts=bs, n_bursts=len(bs), n_bursts_in_window=sum(b['rendered'] for b in bs), n_samples=len(iq),
                  n_bursts_not_rendered=sum(not b['rendered'] for b in bs))
        res = check_samples(iq, cs, 'exchange %d of %s' % (e, os.path.basename(path)))
        if res:
            check_decodes(iq, cs, res[0], res[1], 'exchange %d' % e, noiseless=False)
        # every burst's complete hop record against the exchange's clocks, and its channel against the kernel, from the real sidecar
        items = [(i, side['bursts'][i]) for i in range(E['first_burst'], E['last_burst'] + 1)]
        check(hop_records_ok(E, items),
              'exchange %d: every burst\'s complete hop record, in or out of the window, is the independently derived one and gives its channel' % e)
    return side


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--file', help='a real .cf32 (or, with --plan, its .json)')
    ap.add_argument('--exchanges', default='0,1')
    ap.add_argument('--plan', action='store_true')
    ap.add_argument('--only', help='comma list of parts: plan, samples, determinism, stats, mutants (default all)')
    args = ap.parse_args()
    if args.file:
        check_kernels()
        if args.plan:
            side = json.load(open(args.file))
            check_plan(side, side['file_kind'], side['n_exchanges'], side['followup_per_exchange'], os.path.basename(args.file))
        else:
            run_file(args.file, [int(x) for x in args.exchanges.split(',')])
    else:
        only = set(args.only.split(',')) if args.only else None

        def want(part):
            return only is None or part in only
        check_kernels()
        if want('plan'):
            print('\nThe plan of a small page file, with the full 110-packet follow-up (plan only)')
            side, _, _ = gen.plan_conf(kind='page', exchanges=12, followup=110, seed=9101)
            check_plan(side, 'page', 12, 110, 'a page file of 12 exchanges (seed 9101)')
            iside, _, _ = gen.plan_conf(kind='inquiry', exchanges=12, followup=0, seed=9102)
            check_plan(iside, 'inquiry', 12, 0, 'an inquiry file of 12 exchanges (seed 9102)')
        if want('samples'):
            for kind, n, fu, seed in (('page', 14, 30, 9201), ('inquiry', 16, 0, 9202)):
                iq, side = gen.synthesise_conf(kind=kind, exchanges=n, followup=fu, seed=seed)
                check_plan(side, kind, n, fu, 'the small %s run to be sampled' % kind, controls=False)
                res = check_samples(iq, side, 'a small %s run (%d exchanges, %d follow-up), 10-20 dB' % (kind, n, fu))
                if res:
                    check_decodes(iq, side, res[0], res[1], 'with noise', noiseless=False)
                del iq
                # the same plan with no noise: every in-window burst decodes, whatever its SNR
                sidecar, jobs, total = gen.plan_conf(kind=kind, exchanges=n, followup=fu, seed=seed)
                clean = np.concatenate(list(gen.pg.render_blocks(sidecar, jobs, total, seed, noise=False)))
                rendered = [b for b in sidecar['bursts'] if b['rendered']]
                # the start the access code's correlation finds, on this noiseless file
                got = []
                for b in rendered:
                    s_, lap, r = find_access(clean, b['start_sample'] - 100, b['start_sample'] + nbits(b) * SPS, b['channel'],
                                             sorted({PAGED_LAP, M_LAP, GIAC}))
                    got.append(dict(start=s_ - DETECT_BIAS, lap=lap, r=r))
                e_ = np.array([g['start'] - b['start_sample'] - b['timing_frac'] for g, b in zip(got, rendered)])
                check(all(g['r'] > 0.9 and g['lap'] == b['lap'] for g, b in zip(got, rendered)) and np.abs(e_).max() <= 1.0 and abs(e_.mean()) < 0.3,
                      'noiseless, the correlation finds all %d in-window bursts at their sidecar start + timing_frac (worst %.2f, mean %+.2f samples: the '
                      'peak is a whole sample) and LAP' % (len(rendered), np.abs(e_).max(), e_.mean()))
                check_decodes(clean, sidecar, got, rendered, 'noiseless', noiseless=True)
                del clean
        if want('determinism'):
            check_determinism()
        if want('stats'):
            check_statistics()
        if want('mutants'):
            check_mutants()
    bad = [w for ok, w in RESULTS if not ok]
    print('\nRESULT: %s' % ('PASS' if not bad else 'FAIL (%d)' % len(bad)))
    for w in bad:
        print('  failed:', w[:200])
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
