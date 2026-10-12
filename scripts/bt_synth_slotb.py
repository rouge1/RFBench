#!/usr/bin/env python3
"""Slot-boundary files for bluey's tracker: a grid offset, 20 ppm of drift, and page IDs with and without a held tail.

    python scripts/bt_synth_slotb.py --set priority1 --out DIR      # the D files (page train, then connection)
    python scripts/bt_synth_slotb.py --set all --out DIR            # every file below
    python scripts/bt_synth_slotb.py --stem B_down --out DIR        # one file
    python scripts/bt_synth_slotb.py --stem B_down --offset-us 5.5 --duration 2 --out DIR

    from scripts import bt_synth_slotb as sb
    iq, sidecar = sb.synthesise('C2m10_cross', duration=0.5, snr_db=35)

bluey-ox-walker's tracker takes ``slot = int(timestamp_us / 625)`` on the capture clock. On a real capture the
page IDs of one device sat within about 5 us of a multiple of 625 us, the slot index flipped between k-1 and k,
and libbtbb's UAP elimination converged on a wrong UAP; the 54 bit positions after an ID's sync word also
demodulated as all ones (a transmitter tail, or bluey's discriminator: unknown). These files give TRUTH for
both: where the transmitter's slot grid sits against the file's own 625 us grid, and whether a held tail is
present after an ID. Nothing here transmits and nothing opens a radio.

**The slot grid.** The transmitter's TX slots (master slots, CLK1 = 0, one every 1250 us of transmitter time)
follow from the first one used, TX slot 16, which begins at file microsecond ``20000 + offset0`` (so there are
20 ms of noise first and the first slot's ``grid_offset_us`` is exactly ``offset0``, the ``--offset-us``
parameter). TX slot ``j`` after it begins at ``t_j = 20000 + offset0 + 1250 j / (1 + drift_ppm e-6)`` us:
**drift_ppm +20 means the transmitter's clock runs FAST by 20 ppm**, its slots are 20 ppm SHORTER in file
time, and the offset DEcreases by 25 ns per TX slot (12.5 ns per 625 us), drifting DOWN. ``drift_ppm -20`` is
a slow transmitter, the offset increases. The unwrapped offset of a TX slot is **exactly**
``offset0 - drift_ppm e-6 * (t - t_first)`` in file time (the task's sweep law anchored at the first slot, not at
sample 0); it crosses 0 at ``t_first + offset0 / drift``: 0.17 s for 3 us and 20 ppm, 5.02 s for 100 us.
The wrapped offset is that mod 625. The first-order ``t = tau (1 - drift e-6)`` differs from this by
``drift^2 tau`` (0.4 sample at 26 s); the form that makes the sweep law exact was taken.

**What a burst's position is.** The TX slot's start is on that line. A second ID is exactly 312.5 us after
the first of its slot, and a slave answer exactly 625 us after its master packet (nominal, in file time): the
drift moves a TX slot against the next one, not an ID against its neighbour in the slot (a true 20 ppm clock
would shorten them by 0.25 and 0.5 sample; this is the stated model, and it is what makes "the second ID is
20 samples, half a symbol, after the first" exact). Symbols inside a burst stay at the nominal 1 Msym/s (a
DH1's 366 symbols at 20 ppm would be 0.3 sample off at the end; an ID's 68 much less). **Burst starts follow
the grid exactly: ``start_sample + timing_frac`` is whatever the grid gives**, 0 for a fixed offset that is
a multiple of 25 ns at 40 MS/s, fractional on drift files, never random. Per burst ``symbol_phase`` is
recorded all the same: at 40 MS/s a fixed 50.0 us offset puts every first ID at symbol phase 0 and 312.5 us at
phase 20 (bluey's single-grid detector is blind at 20), which is why the primary fixed controls are 50.25 us
and 312.75 us (phases 10 and 30, symmetric about 20) and the exact ones (``*_50p0``) are extras; the second
ID of a TX slot is always 20 samples (half a symbol) later than the first, modulo the symbol.

**The files** (``SPECS``; stems ``slotb_<name>``, written as ``synth_slotb_<name>.cf32`` and ``.json``):

* ``D_same_s1..s3``, ``D_diff_s1``: a page train with a C2 tail (-10 dB, +160 kHz, 56 us) for 6 s, then 20 s
  of single-slot connection traffic on channels of the train, one grid and one crystal throughout. ``D_diff_s1``
  is ``D_same_s1`` with another LAP and UAP for the connection, which sent no ID.
* ``C1_cross``, ``C2m10_cross``, ``C1_50p25``, ``C2m10_50p25`` (+ ``C2_0_cross``, ``C2m20_cross``,
  ``C2m10_f50k_cross``, ``C1_50p0``, ``C2m10_50p0``): page IDs only, 6 s. C1: the ID ends, the carrier is cut
  with ``br.gfsk``'s own raised-cosine amplitude ramp of 2.0 us. C2: after the last sync-word symbol the carrier
  is held for 56 us (``--tail-us``; the 54 header positions and 2 symbols more; bluey asked for about 50) at a constant +160 kHz deviation (the Gaussian modulator run on 56 more 1 symbols, phase
  continuous with the ID) at ``level_db`` relative to the sync word (a 2.0 us raised-cosine amplitude ramp
  from the sync word's level at the last symbol, and ``br.gfsk``'s 2.0 us ramp down after the 56 us). The
  +50 kHz file needs a tone that cannot be made from bits: it is the same modulator fed the NRZ level
  ``50/160`` (the bit value ``(1 + 50/160) / 2`` into ``br.gfsk``, which makes ``2 b - 1``), so the
  frequency pulse integrates it as it does a bit and the phase stays continuous.
* ``B_down``, ``B_up``, ``B_late``, ``B_down_cfo``, ``A_50p25``, ``A_312p75``, ``A_20msps``: single-slot exchanges
  on fixed channels, master POLL, NULL, DM1 or DH1 and the slave's NULL, DM1 or DH1 625 us after it, about 100
  masters per channel per 10 s, a random whole number of TX slots apart (CLK6-1 takes 32 values for the master
  packets and the other 32 for the answers). One exchange per TX slot in the whole file, so no two bursts
  overlap on any channel.

**Page train.** The spec-true page hop sequence, ``apps/bt_hop_substates.page(clk, LAP, UAP, koffset)``, two
IDs per TX slot 312.5 us apart (CLK0 = 0 then 1), a train of 16 channels repeated every 10 ms, koffset 24
(train A) in the even 1.28 s blocks of CLKE (``clke >> 12``) and 8 (train B) in the odd ones. **The file starts
on an A block and the start clock is chosen (see ``selection_note``) so that exactly four block boundaries
fall in the 6 s: the channel set changes four times.** Channels outside +/-12 MHz of the centre
(+/-8 at 20 MS/s) are in the truth with ``rendered: false`` and not in the samples.

**Selection.** The clock, and the paged LAP (the C files keep UAP 0x47; the D files draw the UAP too), are drawn
by rejection until the 16 channels of the first train contain channels 39 and 43 and at least 4 channels in the
window, the first block is an A train, exactly four block boundaries fall in the 6 s, and no train of the 6 s has
the centre channel. That is a selection of legal parameters, not a change of the kernel; the number of draws is
in ``selection_note`` (about one in 600 draws passes). It is not possible with the p6-common default LAP
0x9E8B33 (itself a reserved inquiry LAP, not a legal paged one): for every starting block, the 16 trains of
the next five blocks hold channel 37 or miss 39 or 43. All C files share one selection (one paged device, one
clock; ``select_seed`` 9405), so two of them differ in their tail or their grid and in nothing else; each D file
draws its own. The selection is made over the full 6 s, so a shortened test file is a prefix of the full one.

**The connection (D files).** Its master clock is the paging clock run on: the CLKE of TX slot ``k`` is
``clke0 + 4 (k - 16)``, and the connection simply continues that count (the paging device's CLKN is taken
equal to its CLKE: a legal master clock, ``clk % 4 == 0`` at every TX slot). Its channels are the in-window
channels of the first train (at most 6, 39 and 43 included), on the same grid, same drift.

**Noise and level.** The one floor of every file, ``bt_synth.AMPLITUDE ** 2 / 100`` in 1 MHz, white, running
through the whole file (bursts are added to it; there is no silence); every burst has the stated SNR (20 dB),
no jitter and no CFO except ``B_down_cfo``, where the transmitter's clock error also moves the carriers: every
carrier is ``f_ch * (1 + 20e-6)`` (absolute RF ``f_ch``), named ``cfo_hz`` per burst and at the top. The
carrier phase of a burst is random (seeded; ``carrier_phase_rad``), the carrier is one oscillator at the
absolute sample index, as in the neighbours. ``air_bits`` are left out (``air_bits_omitted``) but a non-ID
burst has ``header_bits_18`` (before whitening) and ``header_air_bits_54`` (after whitening and FEC 1/3,
air order, character 0 transmitted first), and everything needed to rebuild it with
``bt_br_frame.Packet``. A file is built in blocks of 2**22 samples (noise ``bt_synth_interf.noise_block``) and
written block by block.
"""
import argparse
import heapq
import json
import math
import os
import sys
import time
from fractions import Fraction

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from apps import bt_hop_substates as hs  # noqa: E402
from scripts import bt_synth  # noqa: E402
from scripts import bt_synth_hop as hop  # noqa: E402
from scripts.bt_synth_interf import NOISE_1MHZ, amplitude_for, noise_block  # noqa: E402

DEFAULT_OUT = '/media/user/4TB/sdr-synth-tmp'
NOISE_BLOCK = 1 << 22                  # samples of noise (and of file) per block
LEAD_S = 0.02                          # noise before the first TX slot used
MARGIN_S = 0.002                       # noise after the last TX slot's end
K0 = 16                                # first TX slot used: 16 * 1250 us = 20 ms
TX_SLOT_US = 1250.0
RAMP_US = 2.0                          # br.gfsk's ramp, and the C2 step's
TAIL_US = 56.0                         # default C2 tail: the 54 header positions + 2 symbols
ID_BITS = 68
A_LAP, A_UAP = 0x635FCA, 0x47     # a legal, non-reserved LAP (not the GIAC 0x9E8B33), the C files' paged LAP
RESERVED_LAPS = range(0x9E8B00, 0x9E8B40)
WINDOW_MHZ = {40e6: 12.0, 20e6: 8.0}
SLOT_US = 625.0
MASTER_TYPES = ('POLL', 'NULL', 'DM1', 'DH1')
SLAVE_TYPES = ('NULL', 'DM1', 'DH1')
MAX_CONN_CHANNELS = 6

C2_M10 = dict(variant='C2', level_db=-10.0, dev_khz=160.0)

#: name: spec. ``kind`` A (single-slot exchanges), C (page IDs) or D (page train then connection).
SPECS = {}


def _add(stem, seed, kind, offset_us, drift_ppm, duration=10.0, tail=None, fs=40e6, center=2439.0, cfo_ppm=0.0, **extra):
    if kind == 'C':
        extra.setdefault('select_seed', 9405)       # one paged device and clock for every C file
    SPECS[stem] = dict(stem=stem, seed=seed, kind=kind, offset_us=offset_us, drift_ppm=drift_ppm, duration=duration,
                       tail=tail, fs=fs, center=center, cfo_ppm=cfo_ppm, **extra)


_add('D_same_s1', 9401, 'D', 3.0, 20.0, train_s=6.0, conn_s=20.0, tail=C2_M10, connection='same')
_add('D_same_s2', 9402, 'D', 3.0, 20.0, train_s=6.0, conn_s=20.0, tail=C2_M10, connection='same')
_add('D_same_s3', 9403, 'D', 3.0, 20.0, train_s=6.0, conn_s=20.0, tail=C2_M10, connection='same')
_add('D_diff_s1', 9404, 'D', 3.0, 20.0, train_s=6.0, conn_s=20.0, tail=C2_M10, connection='diff', plan_from='D_same_s1')
_add('C1_cross', 9405, 'C', 3.0, 20.0, 6.0, tail=dict(variant='C1', level_db=None, dev_khz=None))
_add('C2m10_cross', 9406, 'C', 3.0, 20.0, 6.0, tail=C2_M10)
_add('C1_50p25', 9407, 'C', 50.25, 0.0, 6.0, tail=dict(variant='C1', level_db=None, dev_khz=None))
_add('C2m10_50p25', 9408, 'C', 50.25, 0.0, 6.0, tail=C2_M10)
_add('B_down', 9409, 'A', 3.0, 20.0)
_add('B_up', 9410, 'A', 622.0, -20.0)
_add('A_50p25', 9411, 'A', 50.25, 0.0)
_add('A_312p75', 9412, 'A', 312.75, 0.0)
_add('C2_0_cross', 9413, 'C', 3.0, 20.0, 6.0, tail=dict(variant='C2', level_db=0.0, dev_khz=160.0))
_add('C2m20_cross', 9414, 'C', 3.0, 20.0, 6.0, tail=dict(variant='C2', level_db=-20.0, dev_khz=160.0))
_add('C2m10_f50k_cross', 9415, 'C', 3.0, 20.0, 6.0, tail=dict(variant='C2', level_db=-10.0, dev_khz=50.0))
_add('B_late', 9416, 'A', 100.0, 20.0)
_add('A_20msps', 9417, 'A', 3.25, 20.0, fs=20e6, center=2441.0)
_add('C1_50p0', 9418, 'C', 50.0, 0.0, 6.0, tail=dict(variant='C1', level_db=None, dev_khz=None))
_add('C2m10_50p0', 9419, 'C', 50.0, 0.0, 6.0, tail=C2_M10)
_add('B_down_cfo', 9420, 'A', 3.0, 20.0, cfo_ppm=20.0)

SETS = {
    'priority1': ['D_same_s1', 'D_same_s2', 'D_same_s3', 'D_diff_s1'],
    'priority2': ['C1_cross', 'C2m10_cross', 'C1_50p25', 'C2m10_50p25'],
    'priority3': ['B_down', 'B_up', 'A_50p25', 'A_312p75'],
    'priority5': ['C2_0_cross', 'C2m20_cross', 'C2m10_f50k_cross', 'B_late', 'A_20msps', 'C1_50p0', 'C2m10_50p0',
                  'B_down_cfo'],
}
SETS['all'] = [s for k in ('priority1', 'priority2', 'priority3', 'priority5') for s in SETS[k]]


# --- what the samples and the truth are each made with (the test's mutants patch these) -------------------

def sample_scale(drift_ppm):
    """File time over transmitter time that the SAMPLES are placed with: ``1 / (1 + drift e-6)``."""
    return 1 / (1 + Fraction(str(drift_ppm)) * Fraction(1, 10 ** 6))


def truth_scale(drift_ppm):
    """File time over transmitter time that the SIDECAR's positions are made with."""
    return 1 / (1 + Fraction(str(drift_ppm)) * Fraction(1, 10 ** 6))


def timing_jitter(rng):
    """The random part of a burst's timing fraction, in samples: none. Every burst is where the grid puts it."""
    return 0.0


def half_label(half):
    """``first``/``second`` as the truth names a page ID's place in its TX slot."""
    return half


def truth_clk(clk, role=None):
    """The clock the sidecar states for a burst."""
    return clk


def truth_lap(lap):
    """The LAP the sidecar states for a burst."""
    return lap


def truth_n_samples(n):
    return n


def wrap_offset(x):
    """The grid offset in [0, 625)."""
    return x % 625


def tail_gain_db(level_db):
    """The level the SAMPLES' C2 tail is made at, relative to the sync word."""
    return level_db


def tail_dev_khz(dev_khz):
    """The frequency deviation the SAMPLES' C2 tail is made at."""
    return dev_khz


def a_channels(fs, center):
    """The channels of an exchange file: at least 4 in the window, 39 and 43 among them at 2439 MHz, never the
    centre channel; at 20 MS/s on 2441 MHz four others with 43 among them."""
    if fs > 30e6:
        return [31, 35, 39, 43, 47]
    return [33, 36, 43, 46]


def check_channels(channels, fs, center):
    """The channels must be in the window and none the centre channel."""
    centre_channel = int(round(center - 2402))
    half = WINDOW_MHZ[fs]
    for c in channels:
        if c == centre_channel:
            raise ValueError('channel %d is the centre channel' % c)
        if abs(2402 + c - center) > half:
            raise ValueError('channel %d (%d MHz) is outside +/-%g MHz of %g' % (c, 2402 + c, half, center))


# --- pieces -------------------------------------------------------------------------------------------

def in_window(channel, fs, center):
    return abs(2402 + channel - center) <= WINDOW_MHZ[fs] + 1e-9


def koffset_of(clk):
    """Train A on even 1.28 s blocks of the clock (CLK12 = 0), train B on odd ones."""
    return hs.KOFFSET['A'] if ((clk >> 12) & 1) == 0 else hs.KOFFSET['B']


def train_letter(clk):
    return 'A' if ((clk >> 12) & 1) == 0 else 'B'


def block_channels(block, lap, uap):
    """The 16 channels of one 1.28 s block of CLKE, in tick order (TX ticks only)."""
    clk0 = block << 12
    ko = koffset_of(clk0)
    return [hs.page(clk0 + t, lap, uap, ko) for t in range(32) if not (t >> 1) & 1]


def train_ok(lap, uap, clke0, n_slots, fs, center):
    """Whether (LAP, UAP, CLKE0) is an admissible start: the first block is an A train, exactly four block
    boundaries fall in ``n_slots`` TX slots, the first train has 39 and 43 and at least 4 channels in the window,
    and none of the trains has the centre channel."""
    if (clke0 >> 12) & 1:
        return False
    end = clke0 + 4 * n_slots
    blocks = list(range(clke0 >> 12, ((end - 1) >> 12) + 1))
    if len(blocks) - 1 != 4:
        return False
    centre_channel = int(round(center - 2402))
    sets = [block_channels(b, lap, uap) for b in blocks]
    first = set(sets[0])
    if 39 not in first or 43 not in first:
        return False
    if sum(1 for c in first if in_window(c, fs, center)) < 4:
        return False
    return all(centre_channel not in s for s in sets)


def select_train(spec, fs, center, nominal_slots):
    """Draw (LAP, UAP, CLKE0) by rejection. Returns ``(lap, uap, clke0, draws)``.

    The C files draw the LAP and the clock and keep UAP 0x47 (the p6-common default LAP 0x9E8B33 is a reserved
    inquiry LAP, and with it, or with any LAP tried, 39 and 43 in the first train, no centre channel in the
    other trains of the 6 s is the rare case: about 1 draw in 600); all C files share one selection
    (``select_seed``) so that two of them differ in their tail or their grid and in nothing else. The D files
    draw all three from their own seed."""
    seed = spec.get('select_seed', spec.get('plan_seed', spec['seed']))
    rng = np.random.default_rng([seed, 5])
    draws = 0
    while True:
        draws += 1
        lap = int(rng.integers(1, 1 << 24))
        uap = A_UAP if spec['kind'] == 'C' else int(rng.integers(0, 256))
        base = int(rng.integers(1, 1000))
        b0 = int(rng.integers(0, 16)) * 2
        r = int(rng.integers(0, 1280 // 4)) * 4
        clke0 = (base << 17) | (b0 << 12) | r
        if lap in RESERVED_LAPS:
            continue
        if train_ok(lap, uap, clke0, nominal_slots, fs, center):
            return lap, uap, clke0, draws


def exchange_slots(rng, channels, k_lo, k_hi, n_per_10s):
    """One master packet per wanted slot per channel, a random whole number of TX slots apart, greedily on
    free TX slots (a TX slot is taken by one exchange, whatever the channel). Returns ``[(k, channel)]`` sorted."""
    mean_gap = max(2, int(round(8000.0 / n_per_10s)))
    wanted = [(k_lo + int(rng.integers(0, mean_gap)), c) for c in channels]
    heapq.heapify(wanted)
    taken = set()
    out = []
    while wanted:
        k, c = heapq.heappop(wanted)
        while k in taken:
            k += 1
        if k >= k_hi:
            continue
        taken.add(k)
        out.append((k, c))
        heapq.heappush(wanted, (k + int(rng.integers(1, 2 * mean_gap)), c))
    return sorted(out)


def hec_alias(c, dc):
    """Whether a header whitened with CLK6-1 = ``c`` still passes its HEC when de-whitened with ``c + dc``: the HEC
    is affine in its data (``hec(x ^ w, uap) = hec(x, uap) ^ hec(w, 0)``), so this is a property of the pair of
    clocks alone, no header and no UAP in it: the difference of the two whitening sequences, 18 bits, is a
    codeword of the HEC code or it is not."""
    w = [a ^ b for a, b in zip(br.whitening(c << 1, 18), br.whitening(((c + dc) & 63) << 1, 18))]
    return br.hec(w[:10], 0) == w[10:]


def hec_alias_note():
    """The sidecar's statement of which clocks one slot away (clk +/- 2: CLK6-1 +/- 1) pass the HEC."""
    pairs = [(c, dc) for c in range(64) for dc in (-1, 1) if hec_alias(c, dc)]
    return ('A wrong whitening clock breaks the HEC except for these pairs (CLK6-1 sent, CLK6-1 assumed), %d of 128 '
            '(one slot away, clk -2 / +2): %s. Per burst, hec_alias_clk_minus2 and hec_alias_clk_plus2 say whether '
            'that burst\'s header passes its HEC at clk - 2 and clk + 2 (with its own UAP, a different header '
            'decoded); the payload CRC then fails (probability 1 - 2^-16).' % (
                len(pairs), ', '.join('%d->%d' % (c, (c + dc) & 63) for c, dc in pairs)))


def sync_distance(a, b):
    sa, sb = br.sync_word(a), br.sync_word(b)
    return sum(x != y for x, y in zip(sa, sb))


# --- the plan -----------------------------------------------------------------------------------------

def plan_file(stem, seed=None, duration=None, train_s=None, conn_s=None, snr_db=20.0, offset_us=None, drift_ppm=None,
              n_per_10s=100, tail_us=TAIL_US):
    """The sidecar and the render jobs of a file, no samples made.

    Returns ``{'sidecar', 'jobs', 'total', 'fs', 'center', 'noise_seed'}``. ``duration`` (or ``train_s`` and
    ``conn_s`` for a D file) shortens a file for a test; the plan of the first part is that of the full file.
    """
    spec = dict(SPECS[stem])
    if seed is not None:
        spec['seed'] = seed
    if 'plan_from' in spec:
        spec['plan_seed'] = SPECS[spec['plan_from']]['seed']
    plan_seed = spec.get('plan_seed', spec['seed'])
    kind = spec['kind']
    fs, center = spec['fs'], spec['center']
    ups = fs / 1e6
    sps = int(round(ups))
    off = Fraction(str(spec['offset_us'] if offset_us is None else offset_us))
    drift = spec['drift_ppm'] if drift_ppm is None else drift_ppm
    cfo_ppm = spec['cfo_ppm']
    if kind == 'D':
        t_train = spec['train_s'] if train_s is None else train_s
        t_conn = spec['conn_s'] if conn_s is None else conn_s
        content = t_train + t_conn
    else:
        content = spec['duration'] if duration is None else duration
        t_train, t_conn = (content, 0.0) if kind == 'C' else (0.0, content)
    n_train = int(math.floor(t_train * 1e6 / TX_SLOT_US + 1e-9))
    n_conn = int(math.floor(t_conn * 1e6 / TX_SLOT_US + 1e-9))
    total = int(round((LEAD_S + content + MARGIN_S) * fs))
    s_scale, t_scale = sample_scale(drift), truth_scale(drift)

    def slot_pos(k, scale):
        """The TX slot ``k``'s start, in samples (exact)."""
        return (20000 + off + Fraction(1250) * (k - K0) * scale) * Fraction(ups).limit_denominator(1000)

    rng = np.random.default_rng([plan_seed, 7])
    carrier = np.random.default_rng([plan_seed, 9])
    tail = spec['tail']
    bursts = []                       # entries: dict(kind-specific) in plan order
    selection_note = 'none: the parameters are fixed (LAP %06X, UAP %02X)' % (A_LAP, A_UAP)
    channels_info = {}
    lap = uap = None
    notes_page = None

    def add(k, intra_us, role, half, ptype, channel, clk, lap_, uap_, packet=None, bits=None, phase=None, train=None,
            tail_spec=None, extra=None):
        bursts.append(dict(k=k, intra=intra_us, role=role, half=half, ptype=ptype, channel=channel, clk=clk,
                           lap=lap_, uap=uap_, packet=packet, bits=bits, phase=phase, train=train, tail=tail_spec,
                           extra=extra or {}))

    # ---- the page train
    if kind in ('C', 'D'):
        nominal = int(math.floor(6.0 * 1e6 / TX_SLOT_US + 1e-9))
        lap, uap, clke0, draws = select_train(spec, fs, center, nominal)
        selection_note = ('%d draw%s of (%s CLKE0) by rejection until the 16 channels of the first train contain '
                          '39 and 43 and at least 4 in-window channels, no train of the 6 s has the centre '
                          'channel %d and four block boundaries fall in it: a selection of legal parameters, '
                          'not a change of the kernel; LAP %06X, UAP %02X, CLKE0 %d (block %d)' % (
                              draws, '' if draws == 1 else 's', 'LAP, UAP and' if kind == 'D' else 'LAP and',
                              int(round(center - 2402)), lap, uap, clke0, clke0 >> 12))
        for i in range(n_train):
            clk = clke0 + 4 * i
            for j in (0, 1):
                c = clk + j
                ch = hs.page(c, lap, uap, koffset_of(c))
                add(K0 + i, 312.5 * j, 'id_page', 'first' if j == 0 else 'second', 'ID', ch, c, lap, uap,
                    train=train_letter(c), phase='page' if kind == 'D' else None, tail_spec=tail)
        first_blocks = block_channels(clke0 >> 12, lap, uap)
        blocks = sorted({(clke0 + 4 * i) >> 12 for i in range(max(n_train, 1))})
        nominal_blocks = sorted({(clke0 + 4 * i) >> 12 for i in range(nominal)})
        block_rows = [dict(block=b, train=train_letter(b << 12), channels=block_channels(b, lap, uap))
                      for b in nominal_blocks]
        channels_info.update(
            train_A=next(r['channels'] for r in block_rows if r['train'] == 'A'),
            train_B=next((r['channels'] for r in block_rows if r['train'] == 'B'), None),
            blocks=block_rows,
            in_window_first_train=sorted({c for c in first_blocks if in_window(c, fs, center)}),
            train_changes_in_6s=len(nominal_blocks) - 1,
            note='the channel set changes at every 1.28 s block (CLKE12 flips the train, CLKE16-12 moves its window): '
                 'in 6 s, %d blocks and 4 changes' % len(nominal_blocks))

    # ---- the exchanges
    if kind in ('A', 'D'):
        if kind == 'A':
            ch_list = a_channels(fs, center)
            conn_lap, conn_uap = A_LAP, A_UAP
        else:
            first_in = channels_info['in_window_first_train']
            pri = [c for c in (39, 43) if c in first_in]
            rest = [c for c in first_in if c not in pri]
            ch_list = sorted((pri + rest)[:MAX_CONN_CHANNELS])
            if spec['connection'] == 'same':
                conn_lap, conn_uap = lap, uap
            else:
                crng = np.random.default_rng([spec['seed'], 21])
                while True:
                    conn_lap = int(crng.integers(1, 1 << 24))
                    conn_uap = int(crng.integers(0, 256))
                    if conn_lap not in RESERVED_LAPS and sync_distance(conn_lap, lap) >= 20:
                        break
        check_channels(ch_list, fs, center)
        if kind == 'A':
            crng = np.random.default_rng([plan_seed, 3])
            clk_start = int(crng.integers(1 << 18, 1 << 26)) * 4
            k_lo, k_hi = K0, K0 + n_conn
        else:
            clk_start = clke0
            k_lo, k_hi = K0 + n_train, K0 + n_train + n_conn
        sched = exchange_slots(rng, ch_list, k_lo, k_hi, n_per_10s)
        for n, (k, ch) in enumerate(sched):
            clk_m = clk_start + 4 * (k - K0)
            pt_m = MASTER_TYPES[int(rng.integers(0, 4))]
            pt_s = SLAVE_TYPES[int(rng.integers(0, 3))]
            for role, ptype, clk, intra in (('master', pt_m, clk_m, 0.0), ('slave', pt_s, clk_m + 2, 625.0)):
                longest = br.PACKET_TYPES[ptype][4]
                nbytes = 0 if not longest else (17 if ptype == 'DM1' else int(rng.integers(17, longest + 1)))
                counter = 2 * n + (role == 'slave')
                body = br.seq_body(counter, nbytes) if nbytes else b''
                seqn, flow, arqn, lt = counter & 1, 1, 1, 1
                pkt = br.Packet(conn_lap, conn_uap, clk, ptype, body, lt_addr=lt, flow=flow, arqn=arqn, seqn=seqn)
                add(k, intra, role, None, ptype, ch, clk, conn_lap, conn_uap, packet=pkt,
                    phase='connection' if kind == 'D' else None,
                    extra=dict(flow=flow, arqn=arqn, seqn=seqn, lt_addr=lt, body=body))
        channels_info.update(exchange_channels=sorted(ch_list))
        if kind == 'D':
            channels_info['connection_channels'] = sorted(ch_list)

    # ---- positions (samples from sample_scale; the truth from truth_scale) and the sidecar entries
    amp = amplitude_for(snr_db)
    # order: by TX slot, then intra-slot offset
    bursts.sort(key=lambda b: (b['k'], b['intra']))
    n = len(bursts)
    phases = carrier.uniform(0, 2 * np.pi, size=n)
    jit_rng = np.random.default_rng([plan_seed, 13])
    entries, jobs, unwrapped_exact = [], [], []
    ups_frac = Fraction(ups).limit_denominator(1000)
    for idx, b in enumerate(bursts):
        k = b['k']
        j = Fraction(timing_jitter(jit_rng))
        intra_samples = Fraction(str(b['intra'])) * Fraction(ups).limit_denominator(1000)
        pos_s = slot_pos(k, s_scale) + intra_samples + j
        pos_t = slot_pos(k, t_scale) + intra_samples + j
        start_s = math.floor(pos_s)
        frac_s = float(pos_s - start_s)
        start_t = math.floor(pos_t)
        frac_t = float(pos_t - start_t)
        slot_start_us = 20000 + off + Fraction(1250) * (k - K0) * t_scale
        unwrapped = slot_start_us - 1250 * k
        unwrapped_exact.append(unwrapped)
        grid_off = float(wrap_offset(slot_start_us))
        start_us = float(pos_t / ups_frac)
        window_ok = in_window(b['channel'], fs, center)
        cfo_hz = (2402 + b['channel']) * 1e6 * cfo_ppm * 1e-6
        e = {
            'index': idx,
            'start_sample': int(start_t),
            'timing_frac': frac_t,
            'symbol_phase': int(start_t % sps),
            'channel': int(b['channel']),
            'channel_mhz': float(2402 + b['channel']),
            'ptype': b['ptype'],
            'role': b['role'],
            'half': half_label(b['half']),
            'clk': int(truth_clk(b['clk'], b['role'])),
            'lt_addr': None,
            'lap': int(truth_lap(b['lap'])),
            'uap': int(b['uap']),
            'payload_hex': '',
            'payload_full_hex': '',
            'snr_db': float(snr_db),
            'rendered': bool(window_ok),
            'in_window': bool(window_ok),
            'train': b['train'],
            'phase': b['phase'],
            'tail': dict(variant=None, level_db=None, dev_khz=None, dur_us=None, ramp_us=None),
            'slot_start_us': float(slot_start_us),
            'grid_offset_us': grid_off,
            'grid_offset_unwrapped_us': float(unwrapped),
            'start_us': start_us,
            'start_mod_625_us': float((pos_t / ups_frac) % 625),
            'header_bits_18': None,
            'whitening_clk6_1': None,
            'header_air_bits_54': None,
            'hec_alias_clk_minus2': None,
            'hec_alias_clk_plus2': None,
            'flow': None, 'arqn': None, 'seqn': None,
            'cfo_hz': cfo_hz,
            'carrier_phase_rad': float(phases[idx]),
        }
        if b['ptype'] == 'ID':
            variant = b['tail']['variant']
            e['air_bits_length'] = ID_BITS
            if variant == 'C1':
                e['tail'] = dict(variant='C1', level_db=None, dev_khz=None, dur_us=0.0, ramp_us=RAMP_US)
            else:
                e['tail'] = dict(variant='C2', level_db=float(b['tail']['level_db']), dev_khz=float(b['tail']['dev_khz']),
                                 dur_us=float(tail_us), ramp_us=RAMP_US)
            access = [float(x) for x in br.access_code(b['lap'], trailer=False)]
            if variant == 'C2':
                nrz = tail_dev_khz(b['tail']['dev_khz']) * 1e3 / (br.GFSK_H / 2 * br.SYMBOL_RATE)
                bits = np.array(access + [(1 + nrz) / 2] * int(tail_us), dtype=np.float64)
                gain = 10 ** (tail_gain_db(b['tail']['level_db']) / 20)
                step_at = float(ID_BITS)
            else:
                bits = np.array(access, dtype=np.float64)
                gain, step_at = 1.0, None
        else:
            p = b['packet']
            side = p.sidecar()
            ex = b['extra']
            hdr18 = br.packet_header(ex['lt_addr'], b['ptype'], ex['flow'], ex['arqn'], ex['seqn'], b['uap'])
            e.update(lt_addr=ex['lt_addr'], payload_hex=ex['body'].hex(), payload_full_hex=p.payload_full.hex(),
                     header_bits_18=''.join(str(x) for x in hdr18),
                     whitening_clk6_1=int((truth_clk(b['clk'], b['role']) >> 1) & 0x3F),
                     header_air_bits_54=''.join(str(int(x)) for x in p.bits[72:126]),
                     hec_alias_clk_minus2=hec_alias(int((truth_clk(b['clk'], b['role']) >> 1) & 0x3F), -1),
                     hec_alias_clk_plus2=hec_alias(int((truth_clk(b['clk'], b['role']) >> 1) & 0x3F), 1),
                     flow=ex['flow'], arqn=ex['arqn'], seqn=ex['seqn'], air_bits_length=len(p.bits))
            bits = np.array(p.bits, dtype=np.float64)
            gain, step_at = 1.0, None
        entries.append(e)
        if window_ok:
            jobs.append(dict(index=idx, bits=bits, start=int(start_s), frac=frac_s, phase=float(phases[idx]), amp=amp,
                             mhz=2402.0 + b['channel'], cfo_hz=cfo_hz, gain=gain, step_at=step_at))

    # ---- the offset law and the crossings
    d1 = 1 / t_scale - 1                     # the truth's drift as a fraction (+20e-6)
    crossings = []
    if entries:
        prev = None
        for e, u in zip(entries, unwrapped_exact):
            w = math.floor(u / 625)
            if prev is not None and w != prev[0]:
                down = w < prev[0]
                j_cross = prev[0] if down else w            # the multiple of 625 that was passed
                # u = offset0 - d * (t - t_first) in file time: it meets 625 j at t_first + (offset0 - 625 j) / d
                t_c = float(20000 + off + (off - 625 * j_cross) / d1) * 1e-6 if d1 != 0 else None
                crossings.append(dict(time_s=t_c, burst_index_first_after=e['index'], boundary=0 if down else 625,
                                      direction='down: the offset passes 0 and wraps to 625' if down else
                                      'up: the offset passes 625 and wraps to 0'))
            prev = (w, e['index'])

    cfo_top = float(center * 1e6 * cfo_ppm * 1e-6)
    ids = [e for e in entries if e['ptype'] == 'ID']
    counts = {}
    for e in entries:
        counts[(e['phase'] or '-') + ':' + e['role'] + ':' + e['ptype']] = counts.get(
            (e['phase'] or '-') + ':' + e['role'] + ':' + e['ptype'], 0) + 1
    drift_truth = float(1e6 * d1)
    sidecar = {
        'generator': 'SDR scripts/bt_synth_slotb.py',
        'generator_commit': bt_synth.commit(),
        'name': 'slotb_' + stem,
        'stem': stem,
        'lap': int(truth_lap(lap if lap is not None else A_LAP)),
        'uap': int(uap if uap is not None else A_UAP),
        'sample_rate': fs,
        'center_mhz': center,
        'samples_per_symbol': sps,
        'modulation': {'scheme': 'GFSK', 'bt': br.GFSK_BT, 'h': br.GFSK_H, 'symbol_rate': br.SYMBOL_RATE},
        'snr_db': float(snr_db),
        'snr_bw_hz': 1e6,
        'noise_1mhz': NOISE_1MHZ,
        'noise_seed': plan_seed,
        'noise_block_samples': NOISE_BLOCK,
        'cfo_hz': cfo_top,
        'cfo_ppm': cfo_ppm,
        'cfo_hz_meaning': 'per burst cfo_hz = f_ch(RF, Hz) * cfo_ppm * 1e-6, added to the burst\'s carrier; the top '
                          'level is that at the centre frequency. 0 in every file but B_down_cfo',
        'clk_convention': 'native CLK[27:0] in ticks of 312.5 us; for a page ID the paging device\'s CLKE at the '
                          'first sample of the ID (CLK0 = 1 for the second ID of a TX slot, CLK1 = 0); for a '
                          'connection packet the master\'s clock at its TX slot, clk % 4 == 0, and the slave '
                          'answer clk + 2; whitening_clk6_1 = (clk >> 1) & 0x3F',
        'seed': spec['seed'],
        'plan_seed': plan_seed,
        'grid_offset_us_first_slot': float(wrap_offset(20000 + off)),
        'first_tx_slot_start_us': float(20000 + off),
        'grid_offset_us_param': float(off),
        'drift_ppm_signed': drift_truth,
        'drift_law': 'The law is anchored at the FIRST TX slot (slot %d, 20 ms after the file start): its start is '
                     'file microsecond 20000 + offset0, so its grid_offset_us is exactly offset0. TX slot j after it '
                     'begins at t_j = 20000 + offset0 + 1250 j / (1 + drift_ppm * 1e-6) us (the transmitter clock '
                     'is FAST by drift_ppm); the unwrapped offset of a TX slot (slot_start_us - 1250 k) is exactly '
                     'offset0 - drift_ppm * 1e-6 * (t - t_first), grid_offset_us that mod 625; drift_ppm_signed +20 '
                     'means slots 20 ppm shorter in file time, the offset decreasing 25 ns per TX slot (12.5 ns per '
                     '625 us). The offset crosses 0 at t_first + offset0 / drift (0.17 s for 3 us and 20 ppm). '
                     'grid_offset_us_first_slot is that of the first TX slot even when the first burst of an '
                     'exchange file comes later. The first-order form t = tau (1 - drift e-6) differs by '
                     'drift^2 * tau (0.4 sample at 26 s)' % K0,
        'grid_offset_definition': 'grid_offset_us = (the file microsecond at which the burst\'s TX slot (the master '
                                  'slot, CLK1 = 0) begins) mod 625, file microsecond 0 being sample 0; for a slave '
                                  'answer the TX slot of its master. start_us is the burst\'s own first access-code '
                                  'sample, (start_sample + timing_frac) / (fs / 1e6); start_mod_625_us is that mod '
                                  '625: grid_offset_us for a master packet and a first ID, + 312.5 for a second '
                                  'ID, + 625 (= the same mod 625) for a slave answer',
        'slot_grid_note': 'The second ID of a slot is exactly 312.5 us (12500 samples at 40 MS/s) after the first and a '
                          'slave answer exactly 625 us after its master packet in file time: the drift moves one TX '
                          'slot against the next (25 ns per TX slot at 20 ppm), not a burst within its slot (a true '
                          '20 ppm clock would put them 0.25 and 0.5 sample earlier). Symbols inside a burst are '
                          'nominal 1 Msym/s: at 20 ppm a DH1\'s 366 symbols would be 0.3 sample off at their end '
                          '(366 x %d x 20e-6) and an ID\'s 68 about 0.05 sample.' % sps,
        'slot_samples': int(round(SLOT_US * ups)),
        'slot_samples_meaning': 'nominal samples in 625 us, one slot; the grid\'s real slots are 20 ppm (drift_ppm) '
                                'shorter or longer in file time',
        'header_bits_meaning': 'header_bits_18 is the 18 header bits before whitening (LT_ADDR 3, TYPE 4, FLOW, ARQN, '
                               'SEQN, HEC 8, each field least significant bit first), character 0 first; '
                               'header_air_bits_54 is the header as sent, after whitening with CLK6-1 '
                               '(whitening_clk6_1) and FEC 1/3, in air transmission order: character 0 (bit 0) is '
                               'transmitted FIRST and is the first symbol after the 72-bit access code, 54 symbols',
        'symbol_phase_note': 'symbol_phase = start_sample %% %d, before timing_frac; the grid gives start_sample + '
                             'timing_frac, never a random fraction. At %g MS/s a fixed 50.0 us offset puts every '
                             'first ID at symbol phase 0 and its second ID at %d, and 312.5 us puts the first at %d '
                             '(bluey\'s single-grid detector is blind at %d): the primary fixed controls are '
                             '50.25 us (phases %d and %d) and 312.75 us (phases %d and %d), symmetric about it, '
                             'and the exact ones (*_50p0, phases 0 and %d) are extras. The second ID of a TX slot '
                             'is always %d samples (half a symbol) after the first modulo the symbol.' % (
                                 sps, ups, sps // 2, int(round(312.5 * ups)) % sps, sps // 2,
                                 int(round(50.25 * ups)) % sps, int(round(50.25 * ups + 312.5 * ups)) % sps,
                                 int(round(312.75 * ups)) % sps, int(round(312.75 * ups + 312.5 * ups)) % sps,
                                 sps // 2, sps // 2),
        'hec_alias_note': hec_alias_note(),
        'channels': channels_info,
        'selection_note': selection_note,
        'crossings': crossings,
        'n_bursts': len(entries),
        'n_samples': int(truth_n_samples(total)),
        'n_rendered': len(jobs),
        'noise_note': 'n_samples = round((0.02 + content + 0.002) * fs): 20 ms of noise before the first TX slot '
                      'used, 2 ms after the last',
        'lead_in_samples': int(round(LEAD_S * fs)),
        'start_sample_meaning': 'the first sample of the access code\'s preamble; start_sample + timing_frac is its '
                                'real position in the file',
        'air_bits_omitted': True,
        'tx_slot_us': TX_SLOT_US,
        'first_tx_slot_index': K0,
        'kind': {'A': 'exchanges', 'C': 'page IDs', 'D': 'page IDs then exchanges'}[kind],
        'counts': counts,
        'tail_notes': ('C1: the ID ends and br.gfsk ramps the amplitude down as a raised cosine over %g us. C2: '
                       'after the last sync-word symbol the carrier is held for %g us at a constant frequency '
                       'deviation (the Gaussian modulator run on %d more symbols, phase continuous), its amplitude '
                       'stepping from the sync word\'s to level_db below it over a %g us raised-cosine ramp from '
                       'the end of the last symbol, then br.gfsk\'s %g us ramp down. The +50 kHz tone is the same '
                       'modulator fed the NRZ level 50/160 (bit value (1 + 50/160)/2 through br.gfsk).'
                       % (RAMP_US, tail_us, int(tail_us), RAMP_US, RAMP_US)
                       + ' The default tail is 56 us, not the 50 us bluey asked for ("about 50 us"): bluey saw the 54 '
                       'bit positions after an ID\'s sync word (where a header would be) demodulate as all ones, and '
                       '50 us would cover only 50 of them; 56 us is carrier at +dev_khz over the whole 54-bit header '
                       'window and 2 symbols more.'),
        'notes': ['Modelled: the TX slot grid at a stated offset, linear drift between TX slots, one crystal for '
                  'slots and symbols (symbols nominal inside a burst), constant SNR per burst, white constant noise '
                  'through the whole file, random carrier phase per burst.',
                  'Timing inside a burst is nominal: a 20 ppm clock would shift the end of a DH1 (366 symbols) by '
                  '0.3 sample and the end of an ID (68) by 0.05 sample; the grid error is in the slot starts.',
                  'Not modelled: CFO (except in B_down_cfo, where every carrier is f_ch * (1 + 20e-6)), '
                  'jitter of the slot grid or of the level, a clock error moving the symbol rate, fading, other '
                  'transmitters, RX slots of the paged device, the page response and FHS.',
                  'IDs outside the window are in the truth with rendered false and are not in the samples.',
                  'The offset law is anchored at the first TX slot (20 ms after file start): its grid_offset_us is '
                  'exactly the offset parameter; crossings are at 20 ms + offset0 / drift.',
                  'The exchange files (A, B kinds) use LAP 0x635FCA, UAP 0x47, the C files\' paged LAP: a legal '
                  'non-reserved LAP, 34 bits from the GIAC\'s sync word, not in 0x9E8B00-0x9E8B3F.'],
        'per_burst_keys': sorted(entries[0]) if entries else [],
        'bursts': entries,
    }
    if kind == 'D':
        sidecar['connection_start_sample'] = int(math.floor(slot_pos(K0 + n_train, t_scale)))
        sidecar['connection_lap'] = int(truth_lap(conn_lap))
        sidecar['connection_uap'] = int(conn_uap)
        sidecar['page_lap'] = int(lap)
        sidecar['page_uap'] = int(uap)
    return dict(sidecar=sidecar, jobs=jobs, total=total, fs=fs, center=center, noise_seed=plan_seed)


# --- the samples ----------------------------------------------------------------------------------------

def render_burst(job, fs, center):
    """One burst at its absolute sample numbers: ``(lo, samples)``. The carrier is one oscillator at the absolute
    sample index with the whole cycles dropped before the cast to radians; a C2 tail's amplitude step is a
    raised-cosine ramp from the end of symbol 68."""
    sps = fs / 1e6
    burst, lead = br.gfsk(job['bits'], fs, h=br.GFSK_H, delay=job['frac'])
    lo = job['start'] - lead
    n = np.arange(lo, lo + len(burst))
    if job['step_at'] is not None and job['gain'] != 1.0:
        t = (np.arange(-lead, -lead + len(burst)) - job['frac']) / sps          # symbols from the first bit
        x = np.clip((t - job['step_at']) / RAMP_US, 0.0, 1.0)
        burst = burst * (1 + (job['gain'] - 1) * (0.5 - 0.5 * np.cos(np.pi * x)))
    burst = burst * np.exp(1j * job['phase'])
    cycles = ((job['mhz'] - center) * 1e6 + job['cfo_hz']) / fs * n
    burst = burst * np.exp(2j * np.pi * (cycles - np.floor(cycles)))
    return lo, (job['amp'] * burst).astype(np.complex64)


def burst_span(job, fs):
    sps = fs / 1e6
    lead = int(np.ceil(RAMP_US * 1e-6 * fs)) + 1
    lo = job['start'] - lead
    return lo, lo + int(np.ceil(len(job['bits']) * sps)) + 2 * lead


def render_blocks(plan, block=NOISE_BLOCK, noise=True):
    """Yield the file in blocks of ``block`` samples as complex64: noise block ``i`` (``noise_block(seed, i)``),
    then every burst that reaches the block, rendered once and dropped when its last block is done."""
    jobs, total, fs, center, seed = plan['jobs'], plan['total'], plan['fs'], plan['center'], plan['noise_seed']
    spans = [burst_span(j, fs) for j in jobs]
    order = sorted(range(len(jobs)), key=lambda i: spans[i][0])
    nxt = 0
    active = {}
    for index, b0 in enumerate(range(0, total, block)):
        b1 = min(total, b0 + block)
        out = noise_block(seed, index, b1 - b0, fs) if noise else np.zeros(b1 - b0, dtype=np.complex64)
        while nxt < len(order) and spans[order[nxt]][0] < b1:
            i = order[nxt]
            if spans[i][1] > b0:
                active[i] = render_burst(jobs[i], fs, center)
            nxt += 1
        for i in sorted(active):
            lo, samples = active[i]
            a, b = max(lo, b0), min(lo + len(samples), b1)
            if b > a:
                out[a - b0:b - b0] += samples[a - lo:b - lo]
        for i in [i for i in active if active[i][0] + len(active[i][1]) <= b1]:
            del active[i]
        yield out


def synthesise(stem, **kw):
    """The samples and the sidecar of a file, in memory: for a small run."""
    plan = plan_file(stem, **kw)
    iq = np.concatenate(list(render_blocks(plan)))
    return iq, plan['sidecar']


def write_file(stem, out_dir, **kw):
    """Make one file: ``synth_slotb_<stem>.cf32`` block by block and ``synth_slotb_<stem>.json``."""
    t0 = time.time()
    plan = plan_file(stem, **kw)
    os.makedirs(out_dir, exist_ok=True)
    iq_path = os.path.join(out_dir, 'synth_slotb_%s.cf32' % stem)
    side_path = os.path.join(out_dir, 'synth_slotb_%s.json' % stem)
    with open(iq_path, 'wb') as f:
        for out in render_blocks(plan):
            out.astype('<c8', copy=False).tofile(f)
    with open(side_path, 'w') as f:
        json.dump(plan['sidecar'], f, separators=(',', ':'))
    sc = plan['sidecar']
    print('%s  %d samples, %.2f s, %d bursts (%d rendered), %.0f s' % (
        iq_path, plan['total'], plan['total'] / plan['fs'], sc['n_bursts'], sc['n_rendered'], time.time() - t0), flush=True)
    print(side_path, flush=True)
    return iq_path, side_path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--set', choices=sorted(SETS), help='write a whole set (priority1 = the D files; all)')
    ap.add_argument('--stem', choices=sorted(SPECS), help='write one file')
    ap.add_argument('--out', default=DEFAULT_OUT)
    ap.add_argument('--offset-us', type=float, help='the first-slot grid offset in us; the file\'s default otherwise')
    ap.add_argument('--drift-ppm', type=float, help='+20 = the transmitter fast, the offset decreasing')
    ap.add_argument('--duration', type=float, help='seconds of content (not for D files)')
    ap.add_argument('--train-s', type=float)
    ap.add_argument('--conn-s', type=float)
    ap.add_argument('--seed', type=int)
    ap.add_argument('--snr-db', type=float, default=20.0)
    ap.add_argument('--tail-us', type=float, default=TAIL_US, help='C2 tail length in us (default 56)')
    ap.add_argument('--n-per-10s', type=int, default=100, help='master packets per channel per 10 s')
    a = ap.parse_args(argv)
    stems = SETS[a.set] if a.set else ([a.stem] if a.stem else [])
    if not stems:
        ap.error('give --set or --stem')
    kw = dict(snr_db=a.snr_db, n_per_10s=a.n_per_10s, tail_us=a.tail_us)
    for k, v in (('offset_us', a.offset_us), ('drift_ppm', a.drift_ppm), ('duration', a.duration),
                 ('train_s', a.train_s), ('conn_s', a.conn_s), ('seed', a.seed)):
        if v is not None:
            kw[k] = v
    for stem in stems:
        write_file(stem, a.out, **kw)


if __name__ == '__main__':
    main()
