#!/usr/bin/env python3
"""Page responses, connection-first ID mixes and IDs at 20 MS/s, for bluey's UAP voter: the second slot-boundary set.

    python scripts/bt_synth_slotb2.py --set P1 --out DIR        # R_s1 R_s2 R_s3 (page exchanges, 40 MS/s)
    python scripts/bt_synth_slotb2.py --set P2 --out DIR        # the nine X_* files (connection and a page train)
    python scripts/bt_synth_slotb2.py --set P4 --out DIR        # C_20msps and R_20msps_s1
    python scripts/bt_synth_slotb2.py --set all --out DIR        # all fourteen
    python scripts/bt_synth_slotb2.py --stem R_s1 --duration 2 --out DIR

    from scripts import bt_synth_slotb2 as s2
    iq, sidecar = s2.synthesise('R_s1', duration=1.5, snr_db=35)

bluey-ox-walker's decoder feeds the 54 bit positions after any ID's sync word to its UAP voter as if they were a
header.  ``bt_synth_slotb`` held only the pager's IDs.  These files add (**P1**) the paged device's own ID
reply and ack, in whole page exchanges, with the sender in the truth; (**P2**) the connection's real headers
first and the page train on the *same LAP* later, with the share of IDs among the LAP's bursts stated, so that "IDs
corrupt the vote" can be told from "whatever arrives first locks the vote"; (**P4**) IDs at 20 MS/s.  Nothing here
transmits and nothing opens a radio.

**One grid, one crystal.**  Every burst starts where ``bt_synth_slotb``'s model puts it: TX slot ``k`` begins at
file microsecond ``20000 + offset + 1250 (k - 16) / (1 + drift e-6)``, with ``offset`` 50.25 us and ``drift`` 0
in every file (``--offset-us``, ``--drift-ppm`` exist as in slotb).  At 40 MS/s that puts first IDs at symbol phase
10 and second IDs at 30 (away from bluey's dead zone at 20), at 20 MS/s at 5 and 15.  Grid and drift are *not* the
subject of these files (slotb's are); each file has one grid throughout.  Per burst the truth keys of slotb
(``slot_start_us``, ``grid_offset_us``, ``grid_offset_unwrapped_us``, ``start_us``, ``start_mod_625_us``,
``header_bits_18``, ``whitening_clk6_1``, ``header_air_bits_54`` ...) are as there; ``slot_offset_us`` (0, 312.5,
625 or 937.5: the burst's half-slot in its parent TX slot, whose start ``slot_start_us`` is) and
``timing_offset_us`` (the slave's own timing error) are added, and ``start_us = slot_start_us + slot_offset_us +
timing_offset_us`` exactly.  ``air_bits`` are left out (``air_bits_omitted``).  Noise: the one floor of every file
(``bt_synth.AMPLITUDE ** 2 / 100`` in 1 MHz), through the whole file, every burst 20 dB over it, 20 ms of lead-in.

**No tails.**  Every ID ends as ``br.gfsk`` ends it, slotb's *C1* kind: after the last sync-word symbol the
carrier is cut with the raised-cosine amplitude ramp of 2 us.  There is no held tail anywhere in these files.
``id_end_sample`` / ``id_end_us`` on every ID are the true end of bit 68 = ``start_sample + timing_frac + 68 sps``
(file microseconds): the amplitude ramp then falls over 2 us and its half-amplitude point is 1 us (``sps``
samples) later.

**Roles.**  ``id_page`` (the pager's ID), ``id_response`` (the paged device's ID answering the heard ID, 625 us
later), ``id_ack`` (the paged device's ID after the FHS), ``fhs``, ``master``, ``slave`` (connection packets);
per burst ``sender`` (``master`` or ``slave`` *device*), ``ours`` true.

**P1: page exchanges (R_s1..3, R_20msps_s1).**  One paged device and one master per file (a device that is paged
again and again; a different LAP/UAP pair per file; UAPs never 0x00, 0xD6, 0x0B, 0xDD, the HEC aliases of 0x00
that bluey's voter confuses; LAPs not in the reserved 0x9E8B00..0x9E8B3F; the two sync words at least 8 bits apart).
An exchange is Core v6.0 Vol 2 Part B: the pager's two IDs per TX slot on the page sequence of the paged device's
LAP (A and B trains alternating per 1.28 s block of CLKE, ``bt_hop_substates.page``); the paged device's scan
frequency (``page_scan`` of its CLKN, constant for 1.28 s); **the heard ID is the first half-slot of the pager's
whose channel equals it**; the response 625 us after the heard ID on the Peripheral page response sequence
(EQ 5, N = 0, CLKN frozen); the FHS 1250 us after the first ID of the heard slot on the Central page response
sequence (EQ 6, CLKE frozen at the response, N = 1); the ack 625 us after the FHS start (EQ 5, N = 1); the POLL
1250 us after the FHS; then 6 to 12 master packets (POLL, then POLL or NULL; one per consecutive TX slot, no idle
slots) each answered by a NULL one slot (625 us) later on the **basic** hopping sequence of the master
(``bt_hop.hop_channel`` at each packet's own clock, the slave's at clk + 2).  The link then goes quiet for 100 to
400 TX slots and the next exchange follows.

*The IDs of a spec-true page carry the PAGED device's LAP, and so does the FHS's access code and its HEC and
CRC (the paged UAP): Core Vol 2 Part B 8.3.3.2 and 6.4.6, ``apps/bt_fhs.py``.  The MASTER's LAP, UAP and native
clock are in the FHS payload (``fhs`` per burst: lap, uap, nap, cod, am_addr, fhs_clk, whitening_x), and the POLL
and the connection carry the master's LAP.  Per burst ``lap`` and ``uap`` are those of the access code and header
actually used.*

*Clocks.*  The master's native clock is one count on the one grid (``clk = M + 4 (slot - 16) + tick``, ``clk %
4 == 0`` at every TX slot), so the FHS's CLK27-2 and every connection packet's whitening come from it.  The paged
device's CLKN and the master's CLKE (its estimate of the paged device's clock) are drawn **per exchange**, the
way ``bt_synth_conf.plan_conf`` draws them (``CLKN - CLKE`` by exchange): **the paged device's CLKN is not
continuous from one exchange to the next** (one continuous CLKN would give the scan frequency 1.28 s at a time,
about a third of the blocks in the window, and a handful of exchanges in a file), and each exchange's *start
slot* is drawn (the 100 to 400 slot gap) with them.  The exchange is chosen by rejection
(``exchanges[].draws``) so that the train lasts 20 to 300 ms before the heard ID (the scanner's frequency is
outside the train it started on, and comes inside at a CLKE block change: CLKN16-12 is constant from the start of
the train to the ack, as a frozen response clock requires), the heard ID, the response, the FHS and the ack are
all in the window (|f - centre| <= 12 MHz at 40 MS/s on 2439.0, <= 8 MHz at 20 MS/s on 2441.0), and no burst of
the exchange is on the centre channel.  The paged address of a file is chosen so that none of the 64 (X, Y1)
channels of its page, response and FHS sequences is the centre channel; the connection's hops are rejected
per exchange.  That is a selection of legal parameters, not a change of a kernel.

*The slave's own timing and carrier.*  The paged device's bursts (response, ack, connection answers) are
``slave_cfo_hz`` (+30 kHz, ``--slave-cfo-hz``) off the channel carrier, per burst ``cfo_hz``, and each is
``timing_offset_us`` ~ N(0, 1.5) us, clipped to +-3 us, from its nominal 625 us after the packet it answers
(``start_us`` is the real start).  **The specification allows a slave +-1 us of jitter** (instantaneous
timing error) **against its received timing; these are beyond that on purpose**, as the task asked, to look
like a poor crystal and receiver rather than a perfect one.  The master's bursts have timing offset 0.  The master/pager's own carrier offset
``master_cfo_hz`` (``--master-cfo-hz``; -30 kHz, +30 kHz or 0 by file, ``SPECS``) is added to its bursts' carriers
(id_page, fhs, master packets; per burst ``cfo_hz``); real devices here are mostly negative, a few tens of kHz.
In the X files the connection's slave answers carry the master's offset (one device pair); C has no slave.

**P2: X_first_idNN_sK, X_bda_idNN_s1.**  One LAP/UAP per file: the connection and the page train carry **the same
LAP** (which is what bluey's real capture looks like and is *not* a spec-true page of another device).  Connection
traffic is slotb's set A: single-slot exchanges on fixed in-window channels (31, 35, 39, 43, 47), master POLL, NULL,
DM1 or DH1, the slave's NULL, DM1 or DH1 625 us later, at random TX slots.  The train (spec-true page hop sequence
of the LAP from a legal clock, A/B per 1.28 s block, two IDs per TX slot) takes the TX slots **the connection does
not use**: nothing overlaps.  ``X_first``: the connection runs the whole file, the train from a third of it
(4 s of 12); the first connection header precedes the first ID.  ``X_bda``: the train runs the whole file, the
connection from a quarter (3 s) to three quarters (9 s).  The **share** of IDs among the LAP's rendered bursts
over the whole file is calibrated from the plan: the connection's slots are a seeded permutation of the slots of
its interval, the largest prefix whose share is still >= the target is taken, and the train then leaves a seeded
random part of the free slots idle until the share is the target (``train_fraction_taken``).  The sidecar has
``id_share_target``, ``id_share_measured`` (rendered IDs / rendered bursts) and ``id_share_measured_post_start``
(from the first ID on), and ``first_id_sample``, ``first_header_sample``, ``connection_start_sample``.
Per burst ``phase`` is ``page`` for IDs and ``connection`` otherwise.

**P4: C_20msps, R_20msps_s1.**  20 samples per symbol on 2441.0 MHz, window +-8 MHz, 10 s; the centre channel is
39.  ``C_20msps`` is the pager's IDs alone (the first train holds 43 and at least 4 in-window channels, and no
train has 39); ``R_20msps_s1`` is the P1 construction.

**P5: S_<LAP>_c<cfo>_p<phase>[_id10]_n<noise>** (``--set SL``, 29 files, bluey's s4 audit).  The X_bda construction on the
schedule (``seed``) of ``X_bda_id87_s1`` (LAP 0x0466AB, share 87) or of ``X_bda_id50_s1`` (LAP 0x662492, share 50): the
(-30 kHz, phase 10) cell is that file's schedule burst for burst.  Per file the **master/pager carrier offset** (``c0`` or
``cm30``), the **symbol phase** of the first IDs (``p``: ``--offset-us`` = 50 + p / 40, so ``(start_sample + timing_frac) %
40`` = p for every rendered connection burst and every first ID; a second ID is p + 20) and the **noise seed** (``n``: the
noise and each burst's carrier phase, separate from the schedule seed) are set.  Cells: both LAPs x CFO {0, -30 kHz} x
phase {0, 10} x noise {1, 2} (16), phase {20, 30} x noise 1 (8), the low-density cell (LAP 0x0466AB, -30 kHz, phase 10,
share target 10, ``_id10``), and the sweep p = 5, 15, 25, 35 (LAP 0x0466AB, -30 kHz).  Per burst ``cfo_hz``, ``start_sample``
and ``timing_frac`` (always 0 here: the grid is whole-sample) as in every file; a top-level ``cell`` block names the cell.
``--set SL`` also writes ``manifest.json`` and ``SHA256SUMS.txt``.

**P6: the LAP test, S_4073AD_*, S_2044EF_*, S_2626FE_*** (``--set LT``, 9 files, bluey's check of whether the sync word's
imbalance is in the 0x0466AB loss at negative CFO).  P5's construction and 0x0466AB's recipe (share 87, the train's second
IDs all out of the window, so first IDs only) on three schedules found by a plan-only search of the schedule seed.  0x0466AB
has 40 ones of 64 (mean of the +-1 word +0.25; halves +0.56 / -0.06) and IDs on -5..+11 MHz of the centre.  0x4073AD (seed
48957, UAP 0xCE): 24 ones, mean -0.25, halves -0.50 / 0.00, IDs on -7..+11 MHz.  0x2044EF (seed 10426, UAP 0x61): 24 ones,
mean -0.25, halves -0.31 / -0.19 (the overall mean reversed, the first half only partly), -7..+11 MHz.  0x2626FE (seed
81442, UAP 0x03), the control: 32 ones, halves 0 / 0, IDs on 0x0466AB's own channels.  A LAP is its page hop schedule, so it
decides which channels the IDs use; the search kept schedules whose ID channels lie on 0x0466AB's side of the window.
Cells per LAP: CFO 0 at phases 0 and 10, and -30 kHz at phase 10, noise seed 1.  The cell block also holds
``sync_word_ones``.  ``--set LT`` writes ``manifest.json`` and ``SHA256SUMS.txt``.

**P7: the carrier-threshold fresh set, F_<LAP>_<cell>[_h28|_h35]_n<noise>** (``--set FR``, 32 files, bluey's s4-carrier-thr
A1.13).  The LT recipe (X_bda, share 87, first IDs only, CFO 0) on six LAPs chosen by the mechanical rule of
``scripts/bt_freshset_select.py`` (committed before its search; its output in ``knowledge/freshset/``): 2 negative, 2
positive, 2 balanced (more isolated 1s / more isolated 0s).  Per LAP ``p0`` (offset 50 us), ``p10`` (50.25 us), ``p10b``
(p10's schedule, another noise realisation) and ``d23`` (+23 ppm drift: the symbol phase walks through all 40 samples);
for 0x4426BB and 0x885614 also h 0.28 and 0.35 at p10 and d23.  Every file has a noise seed of its own (3001.. in
``FR_CELLS`` order).  Opt-in spec keys, absent from every older spec so older files are unchanged: ``h`` (passed to
the renderer as ``job['h']``, recorded in ``modulation.h`` and the cell), ``drift_ppm`` (as ``--drift-ppm``) and
``exchange_ids`` (``exchange_id`` = the TX slot of a master packet and its answer; null on IDs).  The cell block holds
the class and the word statistics (ones, mean, halves, isolated 1s and 0s, longest run).
"""
import argparse
import json
import math
import os
import sys
import time
from fractions import Fraction

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from apps import bt_fhs  # noqa: E402
from apps import bt_hop  # noqa: E402
from apps import bt_hop_substates as hs  # noqa: E402
from scripts import bt_synth  # noqa: E402
from scripts import bt_synth_slotb as sb  # noqa: E402
from scripts.bt_synth_interf import NOISE_1MHZ, amplitude_for  # noqa: E402

DEFAULT_OUT = '/media/user/4TB/sdr-synth-tmp'
NOISE_BLOCK = sb.NOISE_BLOCK
LEAD_S, MARGIN_S = sb.LEAD_S, sb.MARGIN_S
K0 = sb.K0
TX_SLOT_US = 1250.0
TICK_US = 312.5
ID_BITS = 68
RAMP_US = 2.0
OFFSET_US = 50.25
AVOID_UAPS = (0x00, 0xD6, 0x0B, 0xDD)
RESERVED_LAPS = sb.RESERVED_LAPS
AM_ADDR = 1
SLAVE_CFO_HZ = 30e3
TIMING_SIGMA_US, TIMING_CLIP_US = 1.5, 3.0
MASTER_TYPES, SLAVE_TYPES = sb.MASTER_TYPES, sb.SLAVE_TYPES
X_CHANNELS = [31, 35, 39, 43, 47]
HEARD_MIN_TICK, HEARD_MAX_TICK = 64, 960          # 20 ms .. 300 ms of train before the heard ID
SEARCH_TICKS = 968
GAP_SLOTS = (100, 400)
FOLLOWUP = (6, 12)
CONN_MAX_FRAC = 0.7                 # of the slots of its interval, the connection takes at most this share
MAX_DRAWS = 40000                  # per exchange; an identity that needs more is dropped and drawn again


class Infeasible(Exception):
    """An identity whose page sequences cannot put heard ID, response, FHS and ack in the window together."""

#: name: spec
SPECS = {}


def _add(stem, seed, kind, fs=40e6, center=2439.0, duration=12.0, **extra):
    SPECS[stem] = dict(stem=stem, seed=seed, kind=kind, fs=fs, center=center, duration=duration, **extra)


for _i, _m in ((1, -30e3), (2, 30e3), (3, 0.0)):
    _add('R_s%d' % _i, 9500 + _i, 'R', master_cfo_hz=_m)
for _n, _share in enumerate((10, 10, 50, 50, 87, 87)):
    _add('X_first_id%d_s%d' % (_share, 1 + _n % 2), 9511 + _n, 'X', mode='first', share=_share,
         master_cfo_hz=-30e3 if _n % 2 == 0 else 30e3)
for _n, _share in enumerate((10, 50, 87)):
    _add('X_bda_id%d_s1' % _share, 9521 + _n, 'X', mode='bda', share=_share, master_cfo_hz=-30e3)
# P5, the same-LAP set for bluey's s4 audit: the X_bda construction on the schedule (seed) of X_bda_id87_s1 (LAP 0x0466AB,
# UAP 0x5F, ID share 87) and of X_bda_id50_s1 (LAP 0x662492, UAP 0x15, ID share 50), with the master's carrier offset, the
# symbol phase of the first IDs (``--offset-us`` = 50 + phase / 40, so start_sample % 40 = phase) and the noise seed set per
# file.  The schedule seed fixes the LAP, UAP, clock, connection and train; the noise seed (noise, carrier phase of each burst)
# is separate, so the cells of one LAP differ only in CFO, phase and noise.
SL_SCHEDULES = {'0466AB': dict(seed=9523, share=87), '662492': dict(seed=9522, share=50)}
SL_CELLS = []                      # (lap name, cfo_hz, phase, noise seed, share or None)
for _lap in SL_SCHEDULES:
    for _cfo in (0.0, -30e3):
        for _ph, _noises in ((0, (1, 2)), (10, (1, 2)), (20, (1,)), (30, (1,))):
            for _nz in _noises:
                SL_CELLS.append((_lap, _cfo, _ph, _nz, None))
SL_CELLS.append(('0466AB', -30e3, 10, 1, 10))                      # the low-density cell
for _ph in (5, 15, 25, 35):                                         # the sweep
    SL_CELLS.append(('0466AB', -30e3, _ph, 1, None))


def sl_stem(lap, cfo, phase, noise, share=None):
    return 'S_%s_c%s_p%d%s_n%d' % (lap, 'm30' if cfo < 0 else '0', phase, '' if share is None else '_id%d' % share, noise)


# P6, the LAP test (bluey's check of whether the sync word's imbalance is in the 0x0466AB loss): 0x0466AB's recipe (share 87,
# first IDs only in the window) on three more schedules found by a plan-only seed search (90,000 seeds): 0x4073AD (24 ones of
# 64, mean of the +-1 word -0.25, halves -0.50 / 0.00 against 0x0466AB's +0.56 / -0.06; ID channels -7..+11 MHz), 0x2044EF
# (24 ones, halves -0.31 / -0.19; -7..+11 MHz) and the control 0x2626FE (32 ones, halves 0 / 0; ID channels -5..+11 MHz, 0x0466AB's
# own); CFO 0 at phases 0 and 10 (bluey shifts these digitally) and one rendered -30 kHz phase-10 file per LAP to check that a
# shift and a rendered offset agree.
LT_SCHEDULES = {'4073AD': dict(seed=48957, share=87), '2044EF': dict(seed=10426, share=87), '2626FE': dict(seed=81442, share=87)}
LT_CELLS = [(_lap, _cfo, _ph, 1, None) for _lap in LT_SCHEDULES for _cfo, _ph in ((0.0, 0), (0.0, 10), (-30e3, 10))]

for _lap, _cfo, _ph, _nz, _sh in SL_CELLS + LT_CELLS:
    _sched = SL_SCHEDULES.get(_lap) or LT_SCHEDULES[_lap]
    _cell = dict(lap_name=_lap, cfo_hz=_cfo, first_id_phase_samples=_ph, noise_seed=_nz, schedule_seed=_sched['seed'])
    if _lap in LT_SCHEDULES:
        _cell['sync_word_ones'] = sum(br.sync_word(int(_lap, 16)))
    _add(sl_stem(_lap, _cfo, _ph, _nz, _sh), _sched['seed'], 'X', mode='bda', share=_sched['share'] if _sh is None else _sh,
         master_cfo_hz=_cfo, offset_us=50.0 + _ph / 40.0, noise_seed=_nz, cell=_cell)

# P7, the carrier-threshold fresh set (bluey's s4-carrier-thr A1.13; LAPs by the mechanical rule of
# scripts/bt_freshset_select.py, committed before its search): per LAP p0, p10, p10 with a second noise realisation (p10b)
# and a +23 ppm drift copy (d23); h 0.28 / 0.35 for one balanced and one negative LAP (p10, d23); a noise seed of its own
# per file (FR_NOISE0 + its place in FR_CELLS); exchange_id on every connection burst.
FR_SCHEDULES = {  # lap name: (schedule seed, class)
    '885614': (115125, 'negative'), '20C0AA': (122348, 'negative'), 'EED5EF': (103366, 'positive'),
    'DBB3FB': (107603, 'positive'), '4426BB': (105619, 'balanced_1'), 'A9A2FB': (108529, 'balanced_0')}
FR_H_LAPS = ('4426BB', '885614')
FR_NOISE0 = 3001
FR_CELLS = []                       # (lap name, cell, offset us, drift ppm, h or None)
for _lap in FR_SCHEDULES:
    FR_CELLS += [(_lap, 'p0', 50.0, 0.0, None), (_lap, 'p10', 50.25, 0.0, None), (_lap, 'p10b', 50.25, 0.0, None),
                 (_lap, 'd23', 50.25, 23.0, None)]
for _lap in FR_H_LAPS:
    for _h in (0.28, 0.35):
        FR_CELLS += [(_lap, 'p10', 50.25, 0.0, _h), (_lap, 'd23', 50.25, 23.0, _h)]


def fr_stem(lap, cell, h, noise):
    return 'F_%s_%s%s_n%d' % (lap, cell, '' if h is None else '_h%d' % int(round(h * 100)), noise)


def word_stats(lap):
    """Ones, mean of the +-1 word, halves, isolated 1s and 0s (preamble / trailer as neighbours), the longest run."""
    ac = br.access_code(lap, trailer=True)
    s = ac[4:68]
    d = [2 * b - 1 for b in s]
    runs, c = [], 1
    for i in range(1, 64):
        if s[i] == s[i - 1]:
            c += 1
        else:
            runs.append(c)
            c = 1
    runs.append(c)
    return dict(sync_word_ones=sum(s), sync_mean=sum(d) / 64.0, sync_half1=sum(d[:32]) / 32.0, sync_half2=sum(d[32:]) / 32.0,
                isolated_1=sum(1 for k in range(64) if ac[4 + k] == 1 and ac[3 + k] == 0 and ac[5 + k] == 0),
                isolated_0=sum(1 for k in range(64) if ac[4 + k] == 0 and ac[3 + k] == 1 and ac[5 + k] == 1),
                longest_run=max(runs))


for _i, (_lap, _cell, _off, _drift, _h) in enumerate(FR_CELLS):
    _seed, _cls = FR_SCHEDULES[_lap]
    _nz = FR_NOISE0 + _i
    _extra = dict(h=_h) if _h is not None else {}
    _add(fr_stem(_lap, _cell, _h, _nz), _seed, 'X', mode='bda', share=87, master_cfo_hz=0.0, offset_us=_off,
         drift_ppm=_drift, noise_seed=_nz, exchange_ids=True, cell=dict(
             lap_name=_lap, cfo_hz=0.0, first_id_phase_samples=int(round((_off - 50.0) * 40)) if _drift == 0 else None,
             noise_seed=_nz, schedule_seed=_seed, lap_class=_cls, fr_cell=_cell, drift_ppm=_drift,
             h=_h if _h is not None else br.GFSK_H, **word_stats(int(_lap, 16))), **_extra)

_add('C_20msps', 9531, 'C', fs=20e6, center=2441.0, duration=10.0, master_cfo_hz=-30e3)
_add('R_20msps_s1', 9532, 'R', fs=20e6, center=2441.0, duration=10.0, master_cfo_hz=-30e3)

SETS = {'P1': ['R_s1', 'R_s2', 'R_s3'],
        'P2': [s for s in SPECS if s.startswith('X_')],
        'P4': ['C_20msps', 'R_20msps_s1']}
SETS['SL'] = [sl_stem(*c) for c in SL_CELLS]
SETS['LT'] = [sl_stem(*c) for c in LT_CELLS]
SETS['FR'] = [fr_stem(c[0], c[1], c[4], FR_NOISE0 + i) for i, c in enumerate(FR_CELLS)]
SETS['all'] = SETS['P1'] + SETS['P2'] + SETS['P4']


# --- hooks: what the truth and the samples are each made with (the test's mutants patch these) -------------

def response_tick(heard_tick):
    """The tick of the paged device's ID response: 625 us (two ticks) after the heard ID."""
    return heard_tick + 2


def sample_cfo_hz(cfo_hz, sender='master'):
    """The carrier offset the SAMPLES are made with (the truth states ``cfo_hz``)."""
    return cfo_hz


def ack_lap(paged_lap, master_lap):
    """The LAP of the ack ID: the paged device's."""
    return paged_lap


def role_label(role):
    return role


def sender_label(sender):
    return sender


def draw_uap(rng):
    """A UAP outside the voter's confusion set {0x00, 0xD6, 0x0B, 0xDD}."""
    while True:
        u = int(rng.integers(0, 256))
        if u not in AVOID_UAPS:
            return u


def connection_density(n):
    """Number of connection exchanges taken from the permutation: the calibrated one."""
    return n


def train_slot_ok(k, used):
    """Whether the train may take TX slot ``k`` given the set of slots the connection uses: only a free one."""
    return k not in used


def x_first_intervals(n_slots, split):
    """(train_lo, train_hi, conn_lo, conn_hi) in slots from the first, for X_first: the connection from the start,
    the train from ``split``."""
    return split, n_slots, 0, n_slots


def render_fs(fs):
    """The sample rate the SAMPLES are rendered at."""
    return fs


# --- kernels as tables ----------------------------------------------------------------------------------

def page_table(lap, uap):
    """``table[x][y1]``: the channel of the page, page response and central response sequences of an address
    for X in 0..31 and Y1 in 0, 1 (they differ only in X and Y1: ``hs.select``)."""
    addr = hs.address(lap, uap)
    return np.array([[hs.select(x, y1, addr) for y1 in (0, 1)] for x in range(32)], dtype=np.int64)


def page_vec(table, clk):
    """``hs.page(clk, lap, uap, koffset)`` for an array of CLKE values, train A on even 1.28 s blocks, B on odd."""
    clk = np.asarray(clk, dtype=np.int64)
    b = (clk >> 12) & 0x1F
    c = ((clk >> 4) & 1) << 3 | ((clk >> 3) & 1) << 2 | ((clk >> 2) & 1) << 1 | (clk & 1)
    ko = np.where(((clk >> 12) & 1) == 0, 24, 8)
    x = (b + ko + (c - b) % 16) % 32
    return table[x, (clk >> 1) & 1]


def in_win(channel, fs, center):
    return sb.in_window(channel, fs, center)


def centre_channel(center):
    return int(round(center - 2402))


def train_letter(clk):
    return sb.train_letter(clk)


# --- events -----------------------------------------------------------------------------------------------

def event(k, q, role, sender, ptype, channel, clk, lap, uap, bits, header=None, wclk=None, flow=None, arqn=None,
          seqn=None, lt_addr=None, payload_hex='', payload_full_hex='', phase=None, train=None, half=None,
          exchange=None, tick=None, hop_clk=None, hop_clk_kind=None, hop=None, fhs=None, cfo_hz=0.0,
          timing_us=Fraction(0)):
    """One burst before positions: ``k`` the parent TX slot, ``q`` its half-slot in it (0..3)."""
    return dict(k=k, q=q, role=role, sender=sender, ptype=ptype, channel=int(channel), clk=int(clk), lap=int(lap),
                uap=int(uap), bits=np.array(bits, dtype=np.float64), header=header, wclk=wclk, flow=flow, arqn=arqn,
                seqn=seqn, lt_addr=lt_addr, payload_hex=payload_hex, payload_full_hex=payload_full_hex, phase=phase,
                train=train, half=half, exchange=exchange, tick=tick, hop_clk=hop_clk, hop_clk_kind=hop_clk_kind,
                hop=hop, fhs=fhs, cfo_hz=float(cfo_hz), timing=Fraction(timing_us))


def packet_event(k, q, role, sender, ptype, channel, clk, lap, uap, body, lt, flow, arqn, seqn, **kw):
    p = br.Packet(lap, uap, clk, ptype, body, lt_addr=lt, flow=flow, arqn=arqn, seqn=seqn)
    return event(k, q, role, sender, ptype, channel, clk, lap, uap, p.bits,
                 header=br.packet_header(lt, ptype, flow, arqn, seqn, uap), wclk=clk, flow=flow, arqn=arqn, seqn=seqn,
                 lt_addr=lt, payload_hex=body.hex(), payload_full_hex=p.payload_full.hex(), **kw)


def id_event(k, q, role, sender, channel, clk, lap, **kw):
    return event(k, q, role, sender, 'ID', channel, clk, lap, kw.pop('uap'), bt_fhs.id_bits(lap), **kw)


# --- the plan: P1 ----------------------------------------------------------------------------------------

def draw_identities(rng, fs, center):
    """The paged and the master address of a file: LAPs not reserved and 8 or more sync-word bits apart, UAPs outside
    the confusion set, and a page table that has the centre channel nowhere in it and enough in-window entries."""
    ctr = centre_channel(center)
    tries = 0
    while True:
        tries += 1
        plap = int(rng.integers(1, 1 << 24))
        mlap = int(rng.integers(1, 1 << 24))
        puap, muap = draw_uap(rng), draw_uap(rng)
        if plap in RESERVED_LAPS or mlap in RESERVED_LAPS or plap == mlap or sb.sync_distance(plap, mlap) < 8:
            continue
        table = page_table(plap, puap)
        if (table == ctr).any():
            continue
        # a page table rich enough in-window for heard ID, response, FHS and ack to meet there (above the average
        # share of in-window entries, in the scan entries (Y1 = 0) and in all 64)
        p_in = sum(in_win(c, fs, center) for c in range(79)) / 79.0
        n_scan = sum(in_win(int(c), fs, center) for c in table[:, 0])
        n_all = sum(in_win(int(c), fs, center) for c in table.ravel())
        if n_scan < math.ceil(0.72 * 32 * p_in) or n_all < math.ceil(0.72 * 64 * p_in):
            continue
        nap, cod = int(rng.integers(0, 1 << 16)), 0x5A020C
        return dict(paged_lap=plap, paged_uap=puap, master_lap=mlap, master_uap=muap, nap=nap, cod=cod, table=table,
                    tries=tries)


def plan_exchanges(spec, rng, ident, n_slots, slave_cfo, fs, center):
    """The events and the exchange table of a P1 file."""
    ctr = centre_channel(center)
    table = ident['table']
    plap, puap, mlap, muap = ident['paged_lap'], ident['paged_uap'], ident['master_lap'], ident['master_uap']
    maddr = ((muap & 0xF) << 24) | mlap
    m_k0 = 4 * int(rng.integers(1 << 20, 1 << 25))          # the master's native clock at TX slot K0
    t_rng = np.random.default_rng([spec['seed'], 17])       # the slave's timing offsets
    evs, exch = [], []
    k_end = K0                                              # slot after the last exchange's last burst
    first = True
    draws_total = 0
    while True:
        draws = 0
        while True:
            draws += 1
            if draws > MAX_DRAWS:
                raise Infeasible('no exchange in %d draws' % MAX_DRAWS)
            gap = 0 if first else int(rng.integers(GAP_SLOTS[0], GAP_SLOTS[1] + 1))
            start = k_end + gap
            if start + 270 > K0 + n_slots:
                start = None
                break
            m0 = m_k0 + 4 * (start - K0)
            s0 = 4 * int(rng.integers(1 << 19, 1 << 26))           # the paged device's CLKN: drawn per exchange
            err = 4 * int(rng.integers(-40000, 40000))
            e0 = s0 - err
            if not in_win(int(table[(s0 >> 12) & 0x1F, 0]), fs, center):
                continue                                   # the scan frequency itself must be in the window
            t = np.arange(SEARCH_TICKS, dtype=np.int64)
            ch = page_vec(table, e0 + t)
            scan = table[((s0 + t) >> 12) & 0x1F, 0]
            tx = (t & 2) == 0
            idx = np.flatnonzero(tx & (ch == scan))
            if not len(idx):
                continue
            ht = int(idx[0])
            if not HEARD_MIN_TICK <= ht <= HEARD_MAX_TICK:
                continue
            hslot = ht // 4
            rt = response_tick(ht)
            ft = 4 * hslot + 4
            at = ft + 2
            frozen = s0 + ht
            n_r = hs.n_of(s0 + rt, s0 + 4 * hslot)
            c_resp = hs.peripheral_page_response(frozen, s0 + rt, plap, puap, n_r)
            clke_fz = e0 + rt
            ko_fz = 24 if ((clke_fz >> 12) & 1) == 0 else 8
            c_fhs = hs.central_page_response(clke_fz, e0 + ft, plap, puap, ko_fz, 0, 1)
            n_a = hs.n_of(s0 + at, s0 + 4 * hslot)
            c_ack = hs.peripheral_page_response(frozen, s0 + at, plap, puap, n_a)
            c_heard = int(ch[ht])
            if not all(in_win(c, fs, center) for c in (c_heard, c_resp, c_fhs, c_ack)):
                continue
            if ctr in (c_resp, c_fhs, c_ack) or (ch[:4 * hslot + 2][tx[:4 * hslot + 2]] == ctr).any():
                continue
            if ((s0 >> 12) & 0x1F) != (((s0 + at) >> 12) & 0x1F):
                continue
            n_f = int(rng.integers(FOLLOWUP[0], FOLLOWUP[1] + 1))
            poll_tick = ft + 4
            cchan = []
            for i in range(n_f):
                clk = m0 + poll_tick + 4 * i
                cchan.append((int(bt_hop.hop_channel(clk, maddr, None)), int(bt_hop.hop_channel(clk + 2, maddr, None))))
            if any(ctr in pair for pair in cchan):
                continue
            break
        draws_total += draws
        if start is None:
            break
        first = False
        ei = len(exch)
        # --- the pager's IDs (up to and including the heard slot's second ID)
        for s in range(hslot + 1):
            for j in (0, 1):
                tk = 4 * s + j
                clk = e0 + tk
                c = int(page_vec(table, np.array([clk]))[0])
                assert c == hs.page(clk, plap, puap, 24 if ((clk >> 12) & 1) == 0 else 8)
                evs.append(id_event(start + s, j, 'id_page', 'master', c, m0 + tk, plap, uap=puap, phase='page',
                                    train=train_letter(clk), half='first' if j == 0 else 'second', exchange=ei,
                                    tick=tk, hop_clk=clk, hop_clk_kind='CLKE',
                                    hop=dict(substate='page', clke=clk, koffset=24 if ((clk >> 12) & 1) == 0 else 8,
                                             knudge=0)))

        def slave_timing():
            return Fraction(int(round(float(np.clip(t_rng.normal(0, TIMING_SIGMA_US), -TIMING_CLIP_US,
                                                   TIMING_CLIP_US)) * 1000)), 1000)
        # --- the paged device's ID response
        evs.append(id_event(start + rt // 4, rt % 4, 'id_response', 'slave', c_resp, s0 + rt, plap, uap=puap,
                            phase='page', exchange=ei, tick=rt, hop_clk=s0 + rt, hop_clk_kind='CLKN',
                            hop=dict(substate='peripheral_page_response', clkn_frozen=frozen, clkn=s0 + rt, n=n_r),
                            cfo_hz=slave_cfo, timing_us=slave_timing()))
        # --- the FHS: the master's native clock, the paged device's DAC and UAP, whitened from Xprc
        x = bt_fhs.xprc(clke_fz, ko_fz, 0, 1)
        assert x == hs.x_central_response(clke_fz, ko_fz, 0, 1)
        fhs_clk = m0 + ft
        sr = int(rng.integers(0, 3))
        f = bt_fhs.page_response(plap, puap, mlap, muap, ident['nap'], ident['cod'], AM_ADDR, fhs_clk, x, sr=sr)
        evs.append(event(start + ft // 4, ft % 4, 'fhs', 'master', 'FHS', c_fhs, fhs_clk, plap, puap, f.bits,
                         header=br.packet_header(0, bt_fhs.FHS_TYPE, 0, 0, 0, puap), wclk=f.tx_clk, flow=0, arqn=0,
                         seqn=0, lt_addr=0, payload_hex=f.payload_full.hex()[:36], payload_full_hex=f.payload_full.hex(),
                         phase='page', exchange=ei, tick=ft, hop_clk=e0 + ft, hop_clk_kind='CLKE',
                         hop=dict(substate='central_page_response', clke_frozen=clke_fz, clke=e0 + ft, koffset=ko_fz,
                                  knudge=0, n=1),
                         fhs=dict(lap=mlap, uap=muap, nap=ident['nap'], cod=ident['cod'], am_addr=AM_ADDR, sr=sr,
                                  sp=f.sp, fhs_clk=fhs_clk, clk27_2=f.clk27_2, whitening_x=x, header_uap=puap,
                                  tx_clk=f.tx_clk)))
        # --- the ack
        evs.append(id_event(start + at // 4, at % 4, 'id_ack', 'slave', c_ack, s0 + at, ack_lap(plap, mlap), uap=puap,
                            phase='page', exchange=ei, tick=at, hop_clk=s0 + at, hop_clk_kind='CLKN',
                            hop=dict(substate='peripheral_page_response', clkn_frozen=frozen, clkn=s0 + at, n=n_a),
                            cfo_hz=slave_cfo, timing_us=slave_timing()))
        # --- the connection: POLL, then POLL/NULL, the slave's NULL one slot later
        for i in range(n_f):
            tk = poll_tick + 4 * i
            clk = m0 + tk
            pt = 'POLL' if i == 0 else MASTER_TYPES[int(rng.integers(0, 2))]
            evs.append(packet_event(start + tk // 4, tk % 4, 'master', 'master', pt, cchan[i][0], clk, mlap, muap, b'',
                                    AM_ADDR, 1, 1, i & 1, phase='connection', exchange=ei, tick=tk, hop_clk=clk,
                                    hop_clk_kind='CLK', hop=dict(substate='basic', clk=clk)))
            evs.append(packet_event(start + tk // 4, (tk % 4) + 2, 'slave', 'slave', 'NULL', cchan[i][1], clk + 2, mlap,
                                    muap, b'', AM_ADDR, 1, 1, i & 1, phase='connection', exchange=ei, tick=tk + 2,
                                    hop_clk=clk + 2, hop_clk_kind='CLK', hop=dict(substate='basic', clk=clk + 2),
                                    cfo_hz=slave_cfo, timing_us=slave_timing()))
        last_slot = start + (poll_tick + 4 * (n_f - 1)) // 4
        k_end = last_slot + 1
        exch.append(dict(exchange=ei, start_slot=start, gap_slots_before=gap, draws=draws, clke0=e0, paged_clkn0=s0,
                         master_clkn0=m0, clkn_minus_clke=s0 - e0, heard_tick=ht, heard_slot=hslot,
                         heard_ms=ht * TICK_US * 1e-3, train=train_letter(e0), train_at_heard=train_letter(e0 + ht),
                         scan_channel=c_heard, response_tick=rt, fhs_tick=ft, ack_tick=at, poll_tick=poll_tick,
                         n_followup=n_f, n_id_slots=hslot + 1, clkn16_12_constant=True, fhs_clk=fhs_clk, xprc=x,
                         clke_frozen=clke_fz, koffset_frozen=ko_fz, paged_lap=plap, paged_uap=puap, master_lap=mlap,
                         master_uap=muap, end_slot=last_slot))
    return evs, exch, draws_total


# --- the plan: P2 and P4 (the page train and the connection of one LAP) -------------------------------------

def draw_train(rng, fs, center, n_slots, first_slot, last_slot, need_43, need_first_in_window=4):
    """(lap, uap, clke0, table) by rejection: the train's blocks over [first_slot, last_slot) have no centre
    channel, and its first train holds 43 (if wanted) and at least ``need_first_in_window`` in-window channels."""
    ctr = centre_channel(center)
    tries = 0
    ks = np.arange(first_slot, last_slot)
    while True:
        tries += 1
        lap = int(rng.integers(1, 1 << 24))
        if lap in RESERVED_LAPS:
            continue
        uap = draw_uap(rng)
        base = int(rng.integers(1, 1000))
        b0 = int(rng.integers(0, 16)) * 2
        r = int(rng.integers(0, 1000)) * 4                        # CLKE11-0: the first train stays in one block
        clke_at_first = (base << 17) | (b0 << 12) | r
        table = page_table(lap, uap)
        ticks = np.stack([clke_at_first + 4 * (ks - first_slot), clke_at_first + 4 * (ks - first_slot) + 1])
        ch = page_vec(table, ticks)
        if (ch == ctr).any():
            continue
        train = set(int(c) for c in ch[:, :8].ravel())            # eight slots: one repetition of 16 channels
        if need_43 and 43 not in train:
            continue
        if sum(1 for c in train if in_win(c, fs, center)) < need_first_in_window:
            continue
        return lap, uap, clke_at_first - 4 * (first_slot - K0), table, tries


def conn_events(rng, slots, channels, lap, uap, clk_start, k_ref=K0):
    """Slotb's set A exchanges: master packet at TX slot ``k`` and the slave's answer 625 us later, same channel."""
    evs = []
    for n, k in enumerate(slots):
        clk_m = clk_start + 4 * (k - k_ref)
        ch = int(channels[int(rng.integers(0, len(channels)))])
        pt_m = MASTER_TYPES[int(rng.integers(0, 4))]
        pt_s = SLAVE_TYPES[int(rng.integers(0, 3))]
        for role, sender, ptype, clk, q in (('master', 'master', pt_m, clk_m, 0), ('slave', 'slave', pt_s, clk_m + 2, 2)):
            longest = br.PACKET_TYPES[ptype][4]
            nbytes = 0 if not longest else (17 if ptype == 'DM1' else int(rng.integers(17, longest + 1)))
            counter = 2 * n + (role == 'slave')
            body = br.seq_body(counter, nbytes) if nbytes else b''
            evs.append(packet_event(k, q, role, sender, ptype, ch, clk, lap, uap, body, 1, 1, 1, counter & 1,
                                    phase='connection', tick=None, hop_clk=clk, hop_clk_kind='CLK'))
    return evs


def train_events(slots, clke_at_k0, lap, uap, table):
    """The pager's two IDs in each of ``slots``: a legal clock running on from ``clke_at_k0`` at slot K0."""
    evs = []
    for k in slots:
        for j in (0, 1):
            clk = clke_at_k0 + 4 * (k - K0) + j
            c = int(page_vec(table, np.array([clk]))[0])
            evs.append(id_event(k, j, 'id_page', 'master', c, clk, lap, uap=uap, phase='page',
                                train=train_letter(clk), half='first' if j == 0 else 'second', hop_clk=clk,
                                hop_clk_kind='CLKE', hop=dict(substate='page', clke=clk,
                                                              koffset=24 if ((clk >> 12) & 1) == 0 else 8, knudge=0)))
    return evs


def plan_x(spec, rng, n_slots, duration, fs, center):
    """The events of an X file and the calibration numbers."""
    mode, share = spec['mode'], spec['share']
    frac_third = 1.0 / 3.0
    if mode == 'first':
        split = int(round(frac_third * n_slots))
        t_lo, t_hi, c_lo, c_hi = x_first_intervals(n_slots, split)
    else:
        t_lo, t_hi, c_lo, c_hi = 0, n_slots, int(round(0.25 * n_slots)), int(round(0.75 * n_slots))
    lap, uap, clke_k0, table, tries = draw_train(rng, fs, center, n_slots, K0 + t_lo, K0 + t_hi, need_43=False)
    ks_t = np.arange(K0 + t_lo, K0 + t_hi)
    tk = np.stack([clke_k0 + 4 * (ks_t - K0), clke_k0 + 4 * (ks_t - K0) + 1])
    r_t = np.array([[in_win(int(c), fs, center) for c in col] for col in page_vec(table, tk).T]).sum(axis=1)
    r_of = dict(zip(ks_t.tolist(), r_t.tolist()))
    conn_slots = np.arange(K0 + c_lo, K0 + c_hi)
    perm = rng.permutation(conn_slots)
    # share(n): rendered IDs of the free train slots over (those + 2 n connection bursts)
    r_perm = np.array([r_of.get(int(k), 0) for k in perm])
    total_r = int(r_t.sum())
    i_full = total_r - np.concatenate([[0], np.cumsum(r_perm)])
    n_arr = np.arange(len(perm) + 1)
    sh = i_full / np.maximum(i_full + 2 * n_arr, 1)
    ok = np.flatnonzero(sh >= share / 100.0)
    n_conn = int(ok[-1]) if len(ok) else 0
    n_conn = min(int(CONN_MAX_FRAC * len(perm)), max(0, n_conn))        # always leave free slots in the interval
    n_conn = min(len(perm), max(0, int(connection_density(n_conn))))
    used = set(int(k) for k in perm[:n_conn])
    free = [int(k) for k in ks_t if train_slot_ok(int(k), used)]
    i_now = sum(r_of[k] for k in free)
    i_target = int(round(share / (100.0 - share) * 2 * n_conn))
    order = rng.permutation(len(free))
    keep = np.ones(len(free), dtype=bool)
    cur = i_now
    for pos in order:
        if cur <= i_target:
            break
        keep[pos] = False
        cur -= r_of[free[pos]]
    taken = [k for k, kp in zip(free, keep) if kp]
    chans = X_CHANNELS
    crng = np.random.default_rng([spec['seed'], 23])
    clk_start = int(crng.integers(1 << 18, 1 << 26)) * 4
    evs = conn_events(rng, sorted(used), chans, lap, uap, clk_start)
    evs += train_events(taken, clke_k0, lap, uap, table)
    info = dict(lap=lap, uap=uap, tries=tries, n_conn=n_conn, n_perm=len(perm), free_slots=len(free),
                taken_slots=len(taken), fraction_taken=(len(taken) / len(free)) if free else None,
                t_lo=t_lo, t_hi=t_hi, c_lo=c_lo, c_hi=c_hi, i_full=int(i_now), i_target=i_target,
                clke_at_slot16=clke_k0, conn_clk_start=clk_start)
    return evs, info


def plan_c(spec, rng, n_slots, fs, center):
    lap, uap, clke_k0, table, tries = draw_train(rng, fs, center, n_slots, K0, K0 + n_slots, need_43=True)
    evs = train_events(range(K0, K0 + n_slots), clke_k0, lap, uap, table)
    return evs, dict(lap=lap, uap=uap, tries=tries, clke_at_slot16=clke_k0)


# --- positions, entries, jobs --------------------------------------------------------------------------------

def finish(spec, evs, ident, info, exch, plan_seed, seed, fs, center, duration, snr_db, off, drift, slave_cfo,
           draws_total, extra_top, master_cfo=0.0):
    ups = int(round(fs / 1e6))
    sps = ups
    s_scale, t_scale = sb.sample_scale(drift), sb.truth_scale(drift)
    ups_f = Fraction(ups)
    total = int(round((LEAD_S + duration + MARGIN_S) * fs))
    amp = amplitude_for(snr_db)
    rng_ph = np.random.default_rng([plan_seed, 9])
    evs.sort(key=lambda e: (e['k'] * 4 + e['q']) * TICK_US + float(e['timing']))
    phases = rng_ph.uniform(0, 2 * np.pi, size=len(evs))
    entries, jobs, unwrapped = [], [], []
    rfs = render_fs(fs)
    for ev in evs:
        ev['cfo_hz'] = float(slave_cfo if ev['sender'] == 'slave' else master_cfo)
    for idx, ev in enumerate(evs):
        k = ev['k']
        intra = Fraction(625, 2) * ev['q']
        shift = (intra + ev['timing']) * ups_f

        def slot_pos(scale):
            return (20000 + off + Fraction(1250) * (k - K0) * scale) * ups_f
        pos_s = slot_pos(s_scale) + shift
        pos_t = slot_pos(t_scale) + shift
        start_s, start_t = math.floor(pos_s), math.floor(pos_t)
        frac_s, frac_t = float(pos_s - start_s), float(pos_t - start_t)
        slot_start_us = 20000 + off + Fraction(1250) * (k - K0) * t_scale
        unwrapped.append(slot_start_us - 1250 * k)
        start_us = float(pos_t / ups_f)
        window_ok = in_win(ev['channel'], fs, center)
        is_id = ev['ptype'] == 'ID'
        wc = None if ev['wclk'] is None else int((ev['wclk'] >> 1) & 0x3F)
        e = {
            'index': idx, 'start_sample': int(start_t), 'timing_frac': frac_t, 'symbol_phase': int(start_t % sps),
            'channel': ev['channel'], 'channel_mhz': float(2402 + ev['channel']), 'ptype': ev['ptype'],
            'role': role_label(ev['role']), 'sender': sender_label(ev['sender']), 'ours': True,
            'half': ev['half'], 'clk': ev['clk'], 'lt_addr': ev['lt_addr'], 'lap': ev['lap'], 'uap': ev['uap'],
            'payload_hex': ev['payload_hex'], 'payload_full_hex': ev['payload_full_hex'], 'snr_db': float(snr_db),
            'rendered': bool(window_ok), 'in_window': bool(window_ok), 'train': ev['train'], 'phase': ev['phase'],
            'tail': dict(variant='C1' if is_id else None, level_db=None, dev_khz=None,
                         dur_us=0.0 if is_id else None, ramp_us=RAMP_US if is_id else None),
            'slot_start_us': float(slot_start_us), 'grid_offset_us': float(sb.wrap_offset(slot_start_us)),
            'grid_offset_unwrapped_us': float(unwrapped[-1]), 'start_us': start_us,
            'start_mod_625_us': float((pos_t / ups_f) % 625), 'slot_offset_us': float(intra),
            'timing_offset_us': float(ev['timing']),
            'header_bits_18': None, 'whitening_clk6_1': wc, 'whitening_clk': ev['wclk'],
            'header_air_bits_54': None, 'hec_alias_clk_minus2': None, 'hec_alias_clk_plus2': None,
            'flow': ev['flow'], 'arqn': ev['arqn'], 'seqn': ev['seqn'], 'cfo_hz': ev['cfo_hz'],
            'carrier_phase_rad': float(phases[idx]), 'air_bits_length': len(ev['bits']),
            'exchange': ev['exchange'], 'tick': ev['tick'], 'hop_clk': ev['hop_clk'],
            'hop_clk_kind': ev['hop_clk_kind'], 'hop': ev['hop'], 'fhs': ev['fhs'],
            'id_end_sample': (int(start_t) + frac_t + ID_BITS * sps) if is_id else None,
            'id_end_us': ((int(start_t) + frac_t + ID_BITS * sps) / ups) if is_id else None,
        }
        if not is_id:
            e.update(header_bits_18=''.join(str(int(x)) for x in ev['header']),
                     header_air_bits_54=''.join(str(int(x)) for x in ev['bits'][72:126]),
                     hec_alias_clk_minus2=sb.hec_alias(wc, -1), hec_alias_clk_plus2=sb.hec_alias(wc, 1))
        entries.append(e)
        if window_ok:
            jobs.append(dict(index=idx, bits=ev['bits'], start=int(start_s), frac=frac_s, phase=float(phases[idx]),
                             amp=amp, mhz=2402.0 + ev['channel'], cfo_hz=float(sample_cfo_hz(ev['cfo_hz'], ev['sender'])),
                             gain=1.0, step_at=None))
            if 'h' in spec:
                jobs[-1]['h'] = float(spec['h'])
    if spec.get('exchange_ids'):
        # SET-N's keys: a connection exchange (the master packet at TX slot k and its answer in the same TX slot) shares
        # exchange_id = k; page IDs have none
        for e, ev in zip(entries, evs):
            e['exchange_id'] = int(ev['k']) if e['role'] in ('master', 'slave') else None
    # the offset law and the crossings (none for the fixed grid: kept for --drift-ppm)
    d1 = 1 / t_scale - 1
    crossings = []
    prev = None
    for e, u in zip(entries, unwrapped):
        w = math.floor(u / 625)
        if prev is not None and w != prev and d1 != 0:
            down = w < prev
            j_cross = prev if down else w
            t_c = float(20000 + off + (off - 625 * j_cross) / d1) * 1e-6
            crossings.append(dict(time_s=t_c, boundary=0 if down else 625, burst_index_first_after=e['index'],
                                  direction='down' if down else 'up'))
        prev = w
    counts = {}
    for e in entries:
        counts[e['role']] = counts.get(e['role'], 0) + 1
    rendered_counts = {}
    for e in entries:
        if e['rendered']:
            rendered_counts[e['role']] = rendered_counts.get(e['role'], 0) + 1
    side = {
        'generator': 'SDR scripts/bt_synth_slotb2.py', 'generator_commit': bt_synth.commit(),
        'name': 'slotb2_' + spec['stem'], 'stem': spec['stem'], 'kind': spec['kind'],
        'lap': int(extra_top.get('lap')), 'uap': int(extra_top.get('uap')),
        'sample_rate': fs, 'center_mhz': center, 'samples_per_symbol': sps,
        'modulation': {'scheme': 'GFSK', 'bt': br.GFSK_BT, 'h': float(spec.get('h', br.GFSK_H)), 'symbol_rate': br.SYMBOL_RATE},
        'snr_db': float(snr_db), 'snr_bw_hz': 1e6, 'noise_1mhz': NOISE_1MHZ, 'noise_seed': plan_seed,
        'noise_block_samples': NOISE_BLOCK, 'cfo_hz': float(master_cfo),
        'master_cfo_hz': float(master_cfo), 'slave_cfo_hz': float(slave_cfo) if spec['kind'] != 'C' else None,
        'cfo_hz_meaning': 'per burst cfo_hz is the carrier offset actually used, Hz: carrier = channel + cfo_hz. '
                          'master_cfo_hz on the master/pager\'s bursts (id_page, fhs, master packets), slave_cfo_hz on '
                          'the paged device\'s (id_response, id_ack, slave answers); in the X files the slave answers '
                          'use the master_cfo_hz too (one device pair); C files have no slave. Top-level cfo_hz is '
                          'master_cfo_hz. Real devices here are mostly negative, a few tens of kHz (bluey\'s capture '
                          'statistics); the offset is <= 30 kHz and in_window is by channel, as before',
        'clk_convention': 'native CLK[27:0] in ticks of 312.5 us: for a pager ID its CLKE; for the response and the '
                          'ack the paged device\'s CLKN; for the FHS, POLL and connection packets the master\'s native '
                          'clock (clk % 4 == 0 at a TX slot), a slave answer clk + 2; whitening_clk6_1 = '
                          '(whitening_clk >> 1) & 0x3F where whitening_clk is clk for a packet and the Xprc '
                          'pseudo-clock ((X | 0x20) << 1) of an FHS. Per-burst phase: page for id_page, id_response, id_ack and '
                          'fhs (an FHS is NOT an ID: it has a header, on the paged LAP), connection for POLL, NULL and the '
                          'connection packets; key on ptype or role to tell an ID from a header-bearing packet',
        'seed': spec['seed'], 'plan_seed': plan_seed,
        'grid_offset_us_first_slot': float(sb.wrap_offset(20000 + off)),
        'first_tx_slot_start_us': float(20000 + off), 'grid_offset_us_param': float(off),
        'drift_ppm_signed': float(1e6 * d1),
        'drift_law': 'as bt_synth_slotb: TX slot k begins at file microsecond 20000 + offset + 1250 (k - 16) / (1 + '
                     'drift e-6); %g us and %g ppm in every file here (one grid throughout; grid and drift are not '
                     'the subject of these files, slotb\'s are)' % (float(off), float(1e6 * d1)),
        'grid_offset_definition': 'grid_offset_us = (the file microsecond at which the burst\'s parent TX slot '
                                  '(master slot, CLK1 = 0) begins) mod 625; start_us = slot_start_us + '
                                  'slot_offset_us + timing_offset_us, slot_offset_us 0 / 312.5 (the second ID) / 625 '
                                  '(a slave answer, a response to a first ID, an ack) / 937.5 (a response to a '
                                  'second ID); start_us is the burst\'s own first access-code sample, '
                                  '(start_sample + timing_frac) / (fs / 1e6)',
        'slot_grid_note': 'the second ID of a TX slot is exactly 312.5 us after the first; the paged device\'s bursts '
                          'are 625 us (+ timing_offset_us) after the packet they answer; symbols are nominal '
                          '1 Msym/s in a burst',
        'slot_samples': int(round(625.0 * ups)),
        'header_bits_meaning': sb.header_bits_meaning(entries, next((e['uap'] for e in entries if e['header_bits_18']),
                                                                    extra_top.get('uap'))) +
        ' For an FHS whitening_clk6_1 is 32 + X (the Xprc input, bits 5..0 of the pseudo-clock (X|0x20) << 1 >> 1), '
        'not a clock; libbtbb is handed whitening_clk6_1 << 1.',
        'symbol_phase_note': 'symbol_phase = start_sample %% %d, before timing_frac. offset %g us puts first IDs at '
                             'phase %d and second IDs at %d of %d (bluey\'s detector is blind at %d); a slave burst '
                             'carries its own timing offset, so its phase is whatever that gives' % (
                                 sps, float(off), int(round(float(off) * ups)) % sps,
                                 int(round(float(off) * ups + 312.5 * ups)) % sps, sps, sps // 2),
        'hec_alias_note': sb.hec_alias_note(),
        'channels': extra_top.get('channels', {}), 'selection_note': extra_top['selection_note'],
        'crossings': crossings, 'n_bursts': len(entries), 'n_samples': total, 'n_rendered': len(jobs),
        'noise_note': 'n_samples = round((0.02 + duration + 0.002) * fs): 20 ms of noise before the first TX slot '
                      '(slot 16), 2 ms after the last',
        'lead_in_samples': int(round(LEAD_S * fs)),
        'start_sample_meaning': 'the first sample of the access code\'s preamble; start_sample + timing_frac is its '
                                'real position in the file',
        'air_bits_omitted': True, 'tx_slot_us': TX_SLOT_US, 'first_tx_slot_index': K0,
        'counts': counts, 'counts_rendered': rendered_counts,
        'tail_notes': 'There is no held tail anywhere in these files: every ID ends as br.gfsk ends it (slotb\'s C1 '
                      'kind): after the last sync-word symbol the carrier is cut with br.gfsk\'s raised-cosine '
                      'amplitude ramp of 2 us. The 54 bit positions after the sync word are noise (a receiver sees '
                      'what its discriminator makes of the floor), not a carrier.',
        'id_end_note': 'id_end_sample (and id_end_us, the same in file microseconds) on every ID burst is the true end '
                       'of bit 68: start_sample + timing_frac + 68 * samples_per_symbol, BEFORE br.gfsk\'s ramp-down; '
                       'the amplitude then falls as a raised cosine over 2 us, so its half-amplitude point is '
                       'id_end_sample + samples_per_symbol (1 us) and its power half-point about 0.3 us earlier than '
                       'that. None on a packet.',
        'window': dict(center_mhz=center, half_width_mhz=12.0 if fs > 30e6 else 8.0,
                       centre_channel=centre_channel(center)),
        'notes': [
            'Modelled: the master\'s TX slot grid at a fixed offset, one crystal, constant 20 dB SNR, white constant '
            'noise through the whole file, random carrier phase per burst, every ID cut by br.gfsk\'s ramp (no tail).',
            'Not modelled: drift (slotb has it), fading, other transmitters, RX slots of the master, the paged '
            'device\'s receiver, a retransmitted FHS, the train repetition count Npage and the knudge of EQ 4 (0).',
            'Out-of-window bursts are in the truth with rendered false and are not in the samples.',
            'Only the heard ID, the response, the FHS and the ack of a P1 exchange are required to be in the window: the '
            'first POLL and the slave answers may be out of it (rendered false, absent from the samples), so a '
            '"POLL is 1250 us after the FHS" check must skip them.',
            'Per burst lap and uap are those of the access code and header actually used.',
        ],
        'bursts': entries,
    }
    side.update({k: v for k, v in extra_top.items() if k not in ('lap', 'uap', 'channels', 'selection_note')})
    side['per_burst_keys'] = sorted(entries[0]) if entries else []
    plan = dict(sidecar=side, jobs=jobs, total=total, fs=rfs, center=center, noise_seed=plan_seed)
    return plan


# --- the file --------------------------------------------------------------------------------------------

def plan_file(stem, seed=None, duration=None, snr_db=20.0, offset_us=None, drift_ppm=None, slave_cfo_hz=None,
              master_cfo_hz=None, noise_seed=None):
    """The sidecar and the render jobs of a file, no samples made. ``duration`` shortens a file for a test (the
    plan of its first part is that of the full file for the P1 and C files)."""
    spec = dict(SPECS[stem])
    if seed is not None:
        spec['seed'] = seed
    seed = spec['seed']
    plan_seed = spec.get('noise_seed', seed) if noise_seed is None else noise_seed      # noise and carrier phases
    fs, center = spec['fs'], spec['center']
    dur = spec['duration'] if duration is None else duration
    off = Fraction(str(spec.get('offset_us', OFFSET_US) if offset_us is None else offset_us))
    drift = spec.get('drift_ppm', 0.0) if drift_ppm is None else drift_ppm
    cfo = SLAVE_CFO_HZ if slave_cfo_hz is None else slave_cfo_hz
    n_slots = int(math.floor(dur * 1e6 / TX_SLOT_US + 1e-9))
    rng = np.random.default_rng([seed, 11])
    extra = {}
    exch = []
    draws_total = 0
    if spec['kind'] == 'R':
        for attempt in range(1, 60):
            ident = draw_identities(rng, fs, center)
            try:
                evs, exch, draws_total = plan_exchanges(spec, rng, ident, n_slots, cfo, fs, center)
                break
            except Infeasible:
                continue
        else:
            raise RuntimeError('no feasible identity in 59 attempts')
        ident['attempts'] = attempt
        ctr = centre_channel(center)
        extra.update(
            lap=ident['paged_lap'], uap=ident['paged_uap'], paged_lap=ident['paged_lap'], paged_uap=ident['paged_uap'],
            master_lap=ident['master_lap'], master_uap=ident['master_uap'], am_addr=AM_ADDR, slave_cfo_hz=float(cfo),
            slave_timing=dict(distribution='N(0, %g) us clipped to +-%g us' % (TIMING_SIGMA_US, TIMING_CLIP_US),
                              sigma_us=TIMING_SIGMA_US, clip_us=TIMING_CLIP_US, spec_tolerance_us=1.0),
            exchanges=exch, n_exchanges=len(exch), draws_total=draws_total,
            selection_note='identities after %d draws and %d attempts (LAPs not reserved, 8+ sync bits apart, UAPs outside {0x00, '
                           '0xD6, 0x0B, 0xDD}, a page table without the centre channel %d and 72 %% of the window share of in-window page entries); each exchange by rejection (draws in exchanges[].draws, %d in '
                           'all, counting the terminating round of the identity search): heard ID '
                           '20 to 300 ms into the train, heard/response/FHS/ack in the window, no burst on the '
                           'centre channel, CLKN16-12 constant to the ack: a selection of legal clocks, not a '
                           'change of a kernel' % (ident['tries'], ident['attempts'], ctr, draws_total),
            channels=dict(window=[c for c in range(79) if in_win(c, fs, center)], centre_channel=ctr))
    elif spec['kind'] == 'X':
        evs, info = plan_x(spec, rng, n_slots, dur, fs, center)
        extra.update(
            lap=info['lap'], uap=info['uap'], x_plan=info,
            selection_note='LAP/UAP by %d draws: no centre channel in any train of the file, UAP outside {0x00, '
                           '0xD6, 0x0B, 0xDD}; connection %d exchanges of %d candidate slots, the train %d of %d free '
                           'slots' % (info['tries'], info['n_conn'], info['n_perm'], info['taken_slots'],
                                      info['free_slots']),
            channels=dict(connection=sorted(X_CHANNELS), window=[c for c in range(79) if in_win(c, fs, center)],
                          centre_channel=centre_channel(center)))
    else:
        evs, info = plan_c(spec, rng, n_slots, fs, center)
        extra.update(
            lap=info['lap'], uap=info['uap'],
            selection_note='LAP/UAP/clock by %d draws: the first train holds channel 43 and at least 4 in-window '
                           'channels, no train of the file has the centre channel 39 (39 is the centre channel '
                           'at 2441.0 MHz; the brief\'s "39 and 43" is not possible with "none the centre")' %
                           info['tries'],
            channels=dict(window=[c for c in range(79) if in_win(c, fs, center)], centre_channel=centre_channel(center)))
    mcfo = spec['master_cfo_hz'] if master_cfo_hz is None else master_cfo_hz
    if spec['kind'] != 'R':
        cfo = mcfo                              # X: the slave answers use the master's offset; C: no slave
    if 'cell' in spec:
        extra['cell'] = dict(spec['cell'], schedule_seed=seed, noise_seed=plan_seed, offset_us=float(off),
                             master_cfo_hz=float(spec['master_cfo_hz'] if master_cfo_hz is None else master_cfo_hz),
                             id_share_target=spec['share'])
    plan = finish(spec, evs, None, None, exch, plan_seed, seed, fs, center, dur, snr_db, off, drift, cfo, draws_total, extra,
                  master_cfo=mcfo)
    side = plan['sidecar']
    if spec['kind'] == 'X':
        ids = [e for e in side['bursts'] if e['role'] == 'id_page']
        conn = [e for e in side['bursts'] if e['role'] in ('master', 'slave')]
        ir, cr = sum(e['rendered'] for e in ids), sum(e['rendered'] for e in conn)
        rend = [e for e in ids if e['rendered']]
        fi = min((e['start_sample'] for e in rend), default=None)
        post = [e for e in side['bursts'] if e['rendered'] and fi is not None and e['start_sample'] >= fi]
        info = side['x_plan']
        side.update(
            id_share_target=spec['share'], id_share_measured=100.0 * ir / max(ir + cr, 1),
            id_share_measured_post_start=100.0 * sum(e['role'] == 'id_page' for e in post) / max(len(post), 1),
            id_share_note='rendered IDs / (rendered IDs + rendered connection bursts) of the LAP, percent, over the '
                          'whole file; _post_start the same from the first rendered ID on',
            first_id_sample=fi, first_header_sample=min((e['start_sample'] for e in conn), default=None),
            connection_start_sample=min((e['start_sample'] for e in conn), default=None),
            connection_end_sample=max((e['start_sample'] for e in conn), default=None),
            train_start_sample=min((e['start_sample'] for e in ids), default=None),
            train_fraction_taken=info['fraction_taken'], mode=spec['mode'])
    return plan


# --- the samples ------------------------------------------------------------------------------------------

render_blocks = sb.render_blocks
render_range = sb.render_range
render_burst = sb.render_burst


def synthesise(stem, **kw):
    """The samples and the sidecar of a file, in memory: for a small run."""
    plan = plan_file(stem, **kw)
    iq = np.concatenate(list(render_blocks(plan)))
    return iq, plan['sidecar']


def write_file(stem, out_dir, **kw):
    """Make one file: ``synth_slotb2_<stem>.cf32`` block by block and ``synth_slotb2_<stem>.json``."""
    t0 = time.time()
    plan = plan_file(stem, **kw)
    os.makedirs(out_dir, exist_ok=True)
    iq_path = os.path.join(out_dir, 'synth_slotb2_%s.cf32' % stem)
    side_path = os.path.join(out_dir, 'synth_slotb2_%s.json' % stem)
    sc = plan['sidecar']
    print('%s planned in %.0f s: %d bursts (%d rendered), counts %s' % (stem, time.time() - t0, sc['n_bursts'],
                                                                       sc['n_rendered'], sc['counts']), flush=True)
    with open(iq_path, 'wb') as f:
        for out in render_blocks(plan):
            out.astype('<c8', copy=False).tofile(f)
    with open(side_path, 'w') as f:
        json.dump(sc, f, separators=(',', ':'))
    print('%s  %d samples, %.2f s, %.0f s' % (iq_path, plan['total'], plan['total'] / sc['sample_rate'], time.time() - t0),
          flush=True)
    print(side_path, flush=True)
    return iq_path, side_path


def write_manifest(out_dir, stems, commands, set_name='SL'):
    """``manifest.json`` (per file: LAP, UAP, CFO, offset, phase, seeds, ID share, size, sha256) and ``SHA256SUMS.txt``
    for the files of a set already written to ``out_dir``."""
    import hashlib
    rows, sums = [], []
    for stem in stems:
        base = os.path.join(out_dir, 'synth_slotb2_%s' % stem)
        side = json.load(open(base + '.json'))
        row = dict(stem=stem, file=os.path.basename(base) + '.cf32', sidecar=os.path.basename(base) + '.json',
                   lap='0x%06X' % side['lap'], uap='0x%02X' % side['uap'], **side['cell'],
                   id_share_measured=side['id_share_measured'], n_bursts=side['n_bursts'], n_rendered=side['n_rendered'],
                   n_samples=side['n_samples'], snr_db=side['snr_db'], bytes=os.path.getsize(base + '.cf32'),
                   generator_commit=side['generator_commit'])
        for ext in ('.cf32', '.json'):
            h = hashlib.sha256()
            with open(base + ext, 'rb') as f:
                for blk in iter(lambda: f.read(1 << 26), b''):
                    h.update(blk)
            sums.append('%s  %s' % (h.hexdigest(), os.path.basename(base) + ext))
            row['sha256' + ext.replace('.', '_')] = h.hexdigest()
        rows.append(row)
    with open(os.path.join(out_dir, 'manifest.json'), 'w') as f:
        json.dump(dict(set=set_name, generator='scripts/bt_synth_slotb2.py', commands=commands, files=rows), f, indent=1)
    with open(os.path.join(out_dir, 'SHA256SUMS.txt'), 'w') as f:
        f.write('\n'.join(sums) + '\n')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--set', choices=sorted(SETS), help='write a whole set (P1, P2, P4, all)')
    ap.add_argument('--stem', choices=sorted(SPECS), help='write one file')
    ap.add_argument('--out', default=DEFAULT_OUT)
    ap.add_argument('--offset-us', type=float, help='grid offset in us (default 50.25)')
    ap.add_argument('--drift-ppm', type=float, help='+ = the transmitter fast (default 0)')
    ap.add_argument('--duration', type=float, help='seconds of content (a shortened file for a test)')
    ap.add_argument('--seed', type=int, help='the schedule seed (LAP, clock, connection, train, slave timing)')
    ap.add_argument('--noise-seed', type=int, help='the noise and the carrier phase of each burst (default: the file\'s own)')
    ap.add_argument('--snr-db', type=float, default=20.0)
    ap.add_argument('--slave-cfo-hz', type=float, help='the paged device\'s carrier offset (default +30000)')
    ap.add_argument('--master-cfo-hz', type=float, help='the master/pager\'s carrier offset (default per file: -30000, '
                    '+30000 or 0, see SPECS)')
    a = ap.parse_args(argv)
    stems = SETS[a.set] if a.set else ([a.stem] if a.stem else [])
    if not stems:
        ap.error('give --set or --stem')
    kw = dict(snr_db=a.snr_db)
    for k, v in (('offset_us', a.offset_us), ('drift_ppm', a.drift_ppm), ('duration', a.duration), ('seed', a.seed), ('noise_seed', a.noise_seed),
                 ('slave_cfo_hz', a.slave_cfo_hz), ('master_cfo_hz', a.master_cfo_hz)):
        if v is not None:
            kw[k] = v
    for stem in stems:
        write_file(stem, a.out, **kw)
    if a.set in ('SL', 'LT', 'FR') and not kw.keys() - {'snr_db'}:
        write_manifest(a.out, stems, ['python -B scripts/bt_synth_slotb2.py --set %s --out %s' % (a.set, a.out)], a.set)


if __name__ == '__main__':
    main()
