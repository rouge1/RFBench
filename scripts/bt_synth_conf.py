#!/usr/bin/env python3
"""Spec-conformant page and inquiry exchanges, clipped to the capture window: samples and a sidecar.

    python scripts/bt_synth_conf.py conf_page_hop_win --kind page --exchanges 200 --followup 110 --seed 9001 --out DIR
    python scripts/bt_synth_conf.py conf_inq_hop_win --kind inquiry --exchanges 200 --seed 9002 --out DIR
    python scripts/bt_synth_conf.py --set conf [--out DIR]

    from scripts import bt_synth_conf
    iq, sidecar = bt_synth_conf.synthesise_conf(kind='page', exchanges=10, followup=20)

For bluey-ox-walker, which asked for exchanges **as the specification has them, as a
40 MS/s capture would really see them**. ``scripts/bt_synth_page.py`` (the first
page/inquiry files) departed from the specification on purpose: free channels in
31-50, and a follow-up on an adapted map. This file does not. Every channel comes from
a hop kernel of Core v6.0 Vol 2 Part B section 2.6 (``apps/bt_hop_substates.py``: page,
page scan, Peripheral and Central page response, inquiry, inquiry scan, inquiry
response; ``apps/bt_hop.py`` for the connection state), from clocks that make the
exchange physically consistent, and only the bursts whose carrier lies inside the
window of the capture are in the samples. The rest are in the sidecar, with the
channel they were on, marked ``rendered: false``: a real receiver would never see
them either.

**The window.** 40 MS/s on 2441.0 MHz, a channel ``c`` at ``2402 + c`` MHz. A burst is
in the window iff its channel is 27 to 51 (2429 to 2453 MHz, -12 to +12 MHz around the
2441.0 MHz centre: symmetric about it, and with a burst's own +-1 MHz extent its edges
are at +-13 MHz, inside the BB60D's +-13.5 MHz). 25 of the 79 channels, so about a
third of an exchange's bursts are in the samples. How many of the paged device's page
and response frequencies (the 32 of its segment) the window holds decides how often the
FHS is in the samples; the counts are the sidecar's ``n_exchanges_fhs_in_window`` and
``n_exchanges_heard_id_in_window``.

**What an exchange is.** The paged device (the slave) sits in page scan, one frequency
per 1.28 s from its CLKN16-12 (Table 2.2 a). The master pages with its estimate CLKE of
that clock, an A or a B train (koffset 24 or 8, EQ 2, EQ 3), two ID packets (68 bits,
the paged device's DAC) per master TX slot 312.5 us apart. **The heard ID is the first
half-slot whose page frequency equals the scan frequency**, found by running the kernel
over the half-slots from the clocks: the exchange is built so that the scan frequency
is in the train (the offset between CLKN and CLKE is drawn from the train's own window
of CLKN16-12 - CLKE16-12, -8..7 for A, 8..23 for B), and then the heard slot, the
response and everything after follow from the clocks, nothing at random. Section
2.6.4.2 has paging start with the A train: an exchange on the B train is an excerpt of a page
procedure already running, after at least one 1.28 s A repetition, which is not in the file
(Part G's page table is this same equation with the train alternating A, B per 1.28 s block:
no conflict, see ``apps/bt_hop_substates.py``). The slave
answers 625 us after the heard ID on the Peripheral page response sequence (EQ 5,
N = 0, CLKN16-12 frozen); the master sends the FHS 1250 us after the first ID of the
heard slot on the Central page response sequence (EQ 6, CLKE frozen at the slave's
response, N = 1); the slave's ID acknowledgement 625 us after the FHS start is on the
Peripheral sequence at N = 1; the first POLL is 1250 us after the FHS start. From the
POLL the master hops on the **basic channel hopping sequence of the master** (79
channels: ``bt_hop.hop_channel(clk, address, None)``, the master's native clock, which
the FHS carried), and the slave's answer is one slot later on that **kernel at the
slave's clock** (clk + 2, CLK1 = 1): on the basic sequence it is a different channel,
not the master's. An inquiry exchange: the inquirer sends two GIAC IDs per TX slot on
the inquiry sequence (EQ 7), the scanner in inquiry scan (Table 2.2 c, X = Xir with its
counter N, EQ 8) hears the first half-slot on its frequency and answers 625 us later
with an FHS on the inquiry response sequence (EQ 8, Y1 = 1, N its counter).

**Randomisation.** Each exchange starts at a random sample (uniform symbol phase
0..39 at 40 MS/s) plus the bursts' own random ``timing_frac``; the exchange is shifted
as a whole, so within it a burst on an odd half-slot sits at the phase 20 samples
away (a tick is 312.5 symbols, 12500 samples: not a whole number of symbols). The SNR
is a per-exchange base, uniform 10 to 20 dB (0.1 dB), and a per-burst jitter N(0, 1)
clipped to +-3 dB, over the one constant noise floor of every file. The channels are
the kernels' own: nothing steers them toward the centre channel.

**The clock lock** (``clock_solutions``, ``clock_unique``) is the search over the whole
2**27 domain of CLK[27:1] that bluey runs on a master LAP's packets after an FHS, now
restricted to the packets a receiver of this window would actually have: the
in-window packets of the master's LAP (follow-up master packets and slave answers),
each as (slots since the first POLL, channel).

What is followed and what is not is in the sidecar's ``conformance``.
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from apps import bt_fhs  # noqa: E402
from apps import bt_hop  # noqa: E402
from apps import bt_hop_substates as hs  # noqa: E402
from scripts import bt_synth  # noqa: E402
from scripts import bt_synth_page as pg  # noqa: E402

NOISE_1MHZ = pg.NOISE_1MHZ
DEFAULT_OUT = pg.DEFAULT_OUT
PAGED, MASTER, AM_ADDR, GIAC = pg.PAGED, pg.MASTER, pg.AM_ADDR, pg.GIAC
MASTER_TYPES, SLAVE_TYPES, SLAVE_STREAM = pg.MASTER_TYPES, pg.SLAVE_TYPES, pg.SLAVE_STREAM
SCANNER_POOL, RESERVED_LAPS, IDLE_PROBS = pg.SCANNER_POOL, pg.RESERVED_LAPS, pg.IDLE_PROBS
Raw = pg.Raw

#: The window: channels whose carrier is within +-12 MHz of the 2441.0 MHz centre (symmetric; the burst's own +-1 MHz
#: puts its edges at +-13 MHz, inside the BB60D's +-13.5 MHz).
WINDOW = (27, 51)
WINDOW_MHZ = (2402 + WINDOW[0], 2402 + WINDOW[1])
WINDOW_HALF_WIDTH_MHZ = 12.0
#: SNR per exchange: base uniform in this range (rounded to 0.1 dB), burst jitter N(0, 1) clipped to +-3 dB.
SNR_RANGE = (10.0, 20.0)
JITTER_SIGMA, JITTER_CLIP = 1.0, 3.0
#: Idle time between exchanges.
GAP_MS = (20.0, 40.0)

KINDS_PAGE = ('id_page', 'id_response', 'fhs', 'id_ack', 'followup_master', 'followup_slave')
KINDS_INQUIRY = ('id_inquiry', 'fhs_inquiry_response')


def in_window(channel):
    return WINDOW[0] <= channel <= WINDOW[1]


# --- the basic sequence over the whole CLK[27:1] domain ---------------------------------

#: One table per address: ``table[c]`` is the basic hop of the clock ``c << 1`` (CLK[27:1] = c), c < 2**27. The
#: basic kernel takes CLK1 as an input, so unlike the adapted sequence a slot pair is two entries.
_TABLE_CACHE = {}


def basic_hop_vec(clks, address):
    """``bt_hop.hop_channel(clk, address, None)`` for an array of clocks, the same steps in the same order
    (Core v6.0 Vol 2 Part B s2.6.2); ``bt_hop`` supplies the butterflies and the register bank. The test holds
    it to the scalar kernel on random clocks and addresses."""
    clk = np.asarray(clks, dtype=np.int64) & 0x0FFFFFFF
    a = address & 0xFFFFFFF
    y1 = (clk >> 1) & 1
    y2 = 32 * y1
    x = (clk >> 2) & 31
    big_a = bt_hop.span(a, 27, 23) ^ ((clk >> 21) & 31)
    big_b = bt_hop.span(a, 22, 19)
    big_c = bt_hop.field(a, 8, 6, 4, 2, 0) ^ ((clk >> 16) & 31)
    big_d = bt_hop.span(a, 18, 10) ^ ((clk >> 7) & 0x1FF)
    big_e = bt_hop.field(a, 13, 11, 9, 7, 5, 3, 1)
    big_f = 16 * (clk >> 7) % 79
    z = ((x + big_a) % 32) ^ big_b
    p = big_d | (big_c ^ (31 * y1)) << 9
    bit = [(z >> i) & 1 for i in range(5)]
    for n in range(13, -1, -1):
        swap = ((p >> n) & 1).astype(bool)
        i, j = bt_hop.BUTTERFLY[n]
        bi, bj = bit[i], bit[j]
        bit[i], bit[j] = np.where(swap, bj, bi), np.where(swap, bi, bj)
    perm5out = sum(b << i for i, b in enumerate(bit))
    register = (perm5out + big_e + big_f + y2) % 79
    return np.array(bt_hop.REGISTER_BANK, dtype=np.int64)[register]


def basic_table(address, chunk=1 << 22):
    key = address & 0xFFFFFFF
    if key not in _TABLE_CACHE:
        out = np.empty(1 << 27, dtype=np.uint8)
        for lo in range(0, 1 << 27, chunk):
            c = np.arange(lo, min(lo + chunk, 1 << 27), dtype=np.int64)
            out[lo:lo + len(c)] = basic_hop_vec(c << 1, address)
        _TABLE_CACHE[key] = out
    return _TABLE_CACHE[key]


def clock_solutions(observations, address):
    """Every CLK[27:1] ``c`` of the whole 2**27 domain such that each ``(slot offset, channel)`` of
    ``observations`` is the basic hop of the clock ``(c + offset) << 1``, as a sorted list of ints. ``c`` is the
    clock at the slot the offsets are counted from (the first POLL). With no observation every clock fits and the
    caller is told so by an empty ``observations``, which raises."""
    if not observations:
        raise ValueError('no observation: every clock fits')
    table = basic_table(address)
    mask = (1 << 27) - 1
    first_off, first_ch = observations[0]
    cand = (np.flatnonzero(table == first_ch).astype(np.int64) - first_off) & mask
    for off, ch in observations[1:]:
        cand = cand[table[(cand + off) & mask] == ch]
        if not len(cand):
            break
    return sorted(int(c) for c in cand)


# --- the notes ------------------------------------------------------------------------

CLOCK_NOTE = ("Three clocks per page exchange, none of them equal: the master's CLKE (its estimate of the paged "
              "device's clock, what the page sequence and the Central page response read), the paged device's own "
              "CLKN (page scan, Peripheral page response) and the master's own native clock (what the FHS carries "
              "and the connection runs on). Each burst's 'hop_clk' is the clock its channel was computed from "
              "(hop_clk_kind CLKE, CLKN or CLK), 'clk' the native clock of the device that sent it (for the master's "
              "packets and its follow-up, the master's native clock; for the slave's, its CLKN; in the follow-up a "
              "slave answer carries the master's clock of its slot, as the whitening needs). All run on by one "
              "per 312.5 us from the exchange's first tick; exchanges[].clke0, paged_clkn0 and master_clkn0 are the "
              "three at tick 0. In an inquiry exchange: the inquirer's CLKN and the scanner's CLKN. The clocks are "
              "28 bits and none wraps in an exchange. 'hop' on a burst names the substate, the clock inputs and N "
              "its channel was computed from.")

SPEC_NOTES = [
    "Hop sequences: Core v6.0 Vol 2 Part B s2.6, Figure 2.16, Table 2.2, Table 2.3, EQ 1-EQ 8; the kernels are "
    "apps/bt_hop_substates.py (page, page scan, Peripheral and Central page response, inquiry, inquiry scan, "
    "inquiry response) and apps/bt_hop.py (connection state, basic). Part G s2 frequency hopping sample data holds "
    "them for the scan, response and page tables, all of their values (scripts/test_bt_hop_substates.py).",
    "PART G'S PAGE AND INQUIRY TABLE AND EQ 2: the table (CLKE16-12 = 0..3, 0x1000 blocks) is EQ 2 unchanged with the "
    "train alternating A, B, A, B per block: koffset 24 on even blocks, 8 on odd (a train is repeated Npage x 10 ms, "
    "128 x 10 ms = 1.28 s, allowed for R1 by s8.3.2, then the other: s2.6.4.2, EQ 3). Part G does not state koffset "
    "for that table (24 only for the Central response table, frozen clock in block 0). There is no conflict. This "
    "file uses the same equation and the same koffset rule: each exchange is one train, A or B, and exchanges[].train "
    "and each page burst's 'hop' record give the inputs.",
    "TRAIN HISTORY NOT IN THE FILE: s2.6.4.2 has the paging device start with the A train. About half of the page "
    "exchanges (exchanges[].train 'B') begin on the B train: each is an excerpt of a page procedure already running, "
    "after at least one 1.28 s repetition of the A train that did not reach the scanner, and those preceding "
    "repetitions are not in the file. The inquiry's choice of train is arbitrary (s2.6.4.5).",
    "Two IDs per master TX slot, CLK0 = 0 then 1, 312.5 us apart (s8.3.2 p.558, s2.4.3, Figure 2.7); 68 bits, the "
    "paged device's DAC (page) or the GIAC (inquiry). The paged device listens on its page scan frequency, one hop "
    "per 1.28 s (Table 2.2 a); the heard ID is the first TX half-slot whose page frequency equals it.",
    "The first Peripheral page response is the DAC, 625 us after the beginning of the received page message "
    "(s8.3.3.1, s2.4.4, Figures 2.8, 2.9), on the Peripheral page response sequence, X = CLKN*16-12 + N, N = 0 "
    "(EQ 5; N set to zero in the slot of the response, increased each time CLKN1 is zero), Y1 = CLKN1.",
    "The FHS is 1250 us after the first ID of the heard slot (s8.3.3.2, s2.4.4), on the Central page response "
    "sequence, X = Xprc of the CLKE and koffset frozen when the Peripheral's ID was received, N = 1 for the first FHS "
    "(EQ 6, s2.6.4.4: 'the first increment shall be done before sending the FHS'), Y1 = CLKE1. Its whitening is the "
    "same Xprc (s7.2, bt_fhs.py).",
    "The paged device's second response, the acknowledgement, is 625 us after the FHS start (s2.4.4), on the "
    "Peripheral page response sequence at N = 1 (one TX slot start, the FHS's, since the first response).",
    "After the acknowledgement the devices change to the Central's BASIC channel hopping sequence (s8.3.3.2 p.564; "
    "s8.5): 79 channels, the master's LAP and UAP3-0, the master's native clock which the FHS carried (CLK1-0 = 0 "
    "at the start of the FHS). The slave answers one slot later on that sequence at ITS clock (clk + 2, CLK1 = 1): "
    "NOT the channel of its master (the same channel mechanism is the adapted sequence's only, s2.6.1, bt_hop note 4).",
    "SILENT in the text: the slot of the first POLL after the acknowledgement (chosen: 1250 us after the FHS "
    "start, the next master slot); the FHS header's FLOW, ARQN, SEQN (0 chosen); the header LT_ADDR of an inquiry "
    "response FHS (0 chosen); the clock phase of a scanner relative to the inquirer (chosen: slot-aligned CLKN, "
    "CLKN1-0 = 2 or 3 at the FHS); the reading of CLKE4-2,0 in EQ 2, 6, 7 (bits 4, 3, 2, 0, most significant "
    "first: it reproduces Part G's even-CLKE16-12 rows).",
    "Inquiry: the scan frequency is X = Xir = CLKN16-12 + N of the scanner (Table 2.2 column c names Xir for the "
    "inquiry scan; N is the scanner's counter of FHS packets it has sent, which Part G's scan table holds at 0); "
    "its response is on the inquiry response sequence (Y1 = 1, EQ 8), N the same counter, and an FHS is not "
    "acknowledged by the inquirer (s8.4.2 p.566): no follow-up in an inquiry exchange.",
    "Clocks: the master's CLKE differs from the slave's CLKN by (CLKN16-12 - CLKE16-12) x 1.28 s plus under 1.1 s; the "
    "difference is drawn from the window of the train (A: -8..7, B: 8..23 in CLKN16-12 - CLKE16-12, mod 32), so "
    "the scan frequency is in the train the master sends. CLKE16-12 and the paged device's CLKN16-12 do not change "
    "through the paging, the response, the FHS and the acknowledgement. The master's NATIVE clock runs on through the "
    "whole follow-up and its CLK16-12 may change in it (exchanges[].master_clk16_12_changes_in_followup, "
    "n_exchanges_master_clk16_12_changes): the basic kernel takes the full clock, so nothing depends on it.",
    "The paging train is not repeated, the 2 x Npage repetition and the knudge (EQ 4) are not modelled (knudge 0), "
    "the heard slot is always in the first 16 slots of the train; the scan window and interlaced scan are not "
    "modelled; the RAND back-off and an inquirer's continued probing are not modelled; a retransmitted FHS with an "
    "updated clock is not modelled (every exchange acknowledges the first FHS).",
    "CLKE4-2,0 in EQ 6 is read as the 4-bit number of bits 4, 3, 2, 0 (MSB first), and (CLKE4-2,0 - CLKE16-12) mod 16 "
    "as in EQ 2 (the same grouping Part G's even-CLKE16-12 page rows follow). fhs_whitening records the inputs "
    "(clke_frozen, koffset, knudge, N) so another reading can be recomputed.",
]

CONFORMANCE = dict(
    followed=[
        "page sequence (A or B train, koffset 24 or 8, EQ 2) for the IDs: a kernel from the clocks (s8.3.2, s2.6.4.2)",
        "page scan frequency of the paged device from its CLKN16-12, constant during the exchange (Table 2.2 a)",
        "the heard ID is derived: the first TX half-slot whose page frequency equals the scan frequency",
        "Peripheral page response sequence for the response (N = 0) and the acknowledgement (N = 1), CLKN16-12 frozen "
        "(EQ 5, s2.6.4.3)",
        "Central page response sequence for the FHS (EQ 6, N = 1, CLKE, koffset and knudge frozen) (s2.6.4.4)",
        "inquiry sequence (EQ 7), inquiry scan (Xir, Table 2.2 c) and inquiry response (EQ 8, Y1 = 1) sequences",
        "two IDs per TX slot 312.5 us apart; response 625 us after the heard ID; FHS 1250 us after the first ID of the "
        "heard slot; acknowledgement 625 us after the FHS; POLL 1250 us after the FHS",
        "the basic channel hopping sequence (79 channels) from the first POLL, master and slave each at its own clock",
        "FHS content, HEC and CRC (paged UAP or DCI), whitening from Xprc / Xir (s7.2) as apps/bt_fhs.py",
        "only bursts whose channel is %d-%d (%d-%d MHz) are in the samples; every burst is in the sidecar" % (WINDOW + WINDOW_MHZ),
        "page and inquiry trains by EQ 2 / EQ 7 with koffset 24 (A) or 8 (B), as Part G's table alternates them"],
    not_followed=[
        "RAND back-off of the inquiry scan (s8.4.3 p.568) and an inquirer's continued probing after a response",
        "interlaced page and inquiry scan (Table 2.2 b, d)",
        "the train repetition count Npage and the knudge of EQ 4 (knudge 0; the exchange ends in the first train "
        "repetition); the exchanges that begin on the B train are excerpts of a page procedure already running, "
        "after at least one 1.28 s A repetition, and those preceding repetitions are not in the file",
        "a retransmitted FHS with an updated clock (s8.3.3.2): every exchange acknowledges the first FHS",
        "the paged device's receiver timing and newconnectionTO (s8.3.3.1 p.562-563): nothing to see in a "
        "transmit-only file",
        "the clock continuity of a scanning device across its appearances: scanner identities repeat every 50 "
        "exchanges, each exchange's clocks are drawn independently"],
    silent_in_text=[
        "the slot of the first POLL after the acknowledgement (chosen: the next master slot, 1250 us after the FHS)",
        "the FHS header's FLOW, ARQN and SEQN (0 chosen)",
        "the header LT_ADDR of an inquiry-response FHS (0 chosen)",
        "the scanner's clock phase relative to the inquirer's (slot aligned, CLKN1-0 = 2 or 3 at the FHS)",
        "the reading of CLKE4-2,0 (bits 4, 3, 2, 0 MSB first)"],
    reason="the specification's own hop sequences, clipped to a 40 MS/s capture on 2441.0 MHz: a receiver sees "
           "the bursts on channels %d-%d and none of the others." % WINDOW)

WINDOW_NOTE = ("channels %d to %d inclusive, %d to %d MHz, -%g to +%g MHz around the 2441.0 MHz centre of the 40 MS/s capture: "
               "symmetric about the centre, and with a burst's own +-1 MHz extent its edges are at +-%g MHz, inside the BB60D's "
               "+-13.5 MHz. A burst outside it is in 'bursts' with rendered false and is absent from the samples (the noise "
               "floor is there, as everywhere)." % (WINDOW + WINDOW_MHZ + (WINDOW_HALF_WIDTH_MHZ, WINDOW_HALF_WIDTH_MHZ, WINDOW_HALF_WIDTH_MHZ + 1)))

CLOCK_LOCK_NOTE_PAGE = (
    "clock_solutions lists, per page exchange, every CLK[27:1] of the whole 2**27 domain, as at the first POLL "
    "(first_followup_clk27_1), for which each (slots since the POLL, channel) of the IN-WINDOW packets on the "
    "master's access code (master packets and slave answers, rendered bursts only) is the basic hop of that clock "
    "advanced by the offset: what bluey's lock can recover from this capture. The true clock is always one of "
    "them. The lock is unique (clock_unique) for %d of %d exchanges; the others have %s. A list of more than %d "
    "solutions is not written (clock_solutions null, n_clock_solutions its length).")

CLOCK_LOCK_NOTE_INQUIRY = ("An inquiry exchange has no follow-up traffic, so there is no clock lock to make in this "
                           "file.")

MAX_LISTED_SOLUTIONS = 64


def amplitude(snr_db):
    return pg.amplitude(snr_db)


def draw_distinct(rng, used, low, high, multiple=1):
    return pg.draw_distinct(rng, used, low, high, multiple)


def window_offset(rng, train):
    """d = (CLKN16-12 - CLKE16-12) mod 32 inside the train's window: A holds X = b - 8 .. b + 7, B b + 8 .. b + 23."""
    return int(rng.integers(-8, 8)) if train == 'A' else int(rng.integers(8, 24))


def plan_conf(kind='page', exchanges=200, followup=110, fs=40e6, center_mhz=2441.0, seed=9001, idle_probs=IDLE_PROBS):
    """The sidecar and the render jobs of a capture of ``exchanges`` conformant page (or inquiry) exchanges, no
    samples made. Returns ``(sidecar, jobs, total)``: ``jobs`` holds the IN-WINDOW bursts only, in time order."""
    if kind not in ('page', 'inquiry'):
        raise ValueError("kind is 'page' or 'inquiry'")
    if exchanges < 1:
        raise ValueError("a capture needs at least one exchange")
    if kind == 'page' and followup < 1:
        raise ValueError("a page exchange needs at least one follow-up packet")
    if fs != 40e6 or center_mhz != 2441.0:
        raise ValueError("the window (channels %d-%d) is that of 40 MS/s on 2441.0 MHz" % WINDOW)
    tick = int(round(fs * br.SLOT_US * 1e-6 / 2))
    sps = int(round(fs / br.SYMBOL_RATE))
    slot = 2 * tick
    probs = [float(p) for p in idle_probs]
    if len(probs) != 3 or min(probs) < 0 or abs(sum(probs) - 1) > 1e-9:
        raise ValueError("idle_probs is three odds, for 0, 1 and 2 idle pair-periods, summing to 1")
    master_addr = MASTER['uap'] << 24 | MASTER['lap']
    paged_lap, paged_uap = PAGED['lap'], PAGED['uap']
    rng = np.random.default_rng([seed, 1 << 29])
    used_clk, used_lap = set(), {PAGED['lap'], MASTER['lap'], GIAC}

    events, exch, scanners = [], [], []
    first = int(bt_synth.LEAD_SLOTS * slot)
    cursor = first
    for e in range(exchanges):
        phase = int(rng.integers(0, sps))
        gap_ms = float(rng.uniform(*GAP_MS)) if e else 0.0
        snr_base = round(float(rng.uniform(*SNR_RANGE)), 1)
        origin = cursor + (int(round(gap_ms * 1e-3 * fs)) if e else 0)
        origin += (phase - origin) % sps
        train = 'A' if rng.integers(0, 2) else 'B'
        ko = hs.KOFFSET[train]
        knudge = 0
        evs = []

        def low12():
            return 4 * int(rng.integers(150, 850))          # CLK11-0 in 600..3396: no CLK16-12 change in an exchange

        if kind == 'page':
            d = window_offset(rng, train)
            hi_e = int(rng.integers(64, (1 << 16) - 64))
            e0 = hi_e << 12 | low12()                        # CLKE at tick 0 (a TX slot: CLKE1-0 = 0)
            s0 = (((hi_e + d) & 0xFFFF) << 12) | low12()      # the paged device's CLKN at tick 0
            m0 = draw_distinct(rng, used_clk, 1 << 19, (1 << 27) - (1 << 12), 4)   # the master's native clock
            scan_ch = hs.page_scan(s0, paged_lap, paged_uap)
            heard_tick = next(t for t in range(0, 36)
                              if not t & 2 and hs.page(e0 + t, paged_lap, paged_uap, ko, knudge) == scan_ch)
            heard_slot, heard = heard_tick // 4, heard_tick % 4
            n_slots = heard_slot + 1
            for s in range(n_slots):
                for j in (0, 1):
                    t = 4 * s + j
                    ch = hs.page(e0 + t, paged_lap, paged_uap, ko, knudge)
                    evs.append(dict(tick=t, kind='id_page', role='master', channel=ch,
                                    packet=Raw(bt_fhs.id_bits(paged_lap), 'ID'), lap=paged_lap, uap_for_hec=None,
                                    ptype='ID', clk=m0 + t, hop_clk=e0 + t, hop_clk_kind='CLKE',
                                    hop=dict(substate='page', clke=e0 + t, koffset=ko, knudge=knudge)))
            # the Peripheral's response: CLKN16-12 frozen at the slot where its access code was detected
            clkn_frozen = s0 + heard_tick
            resp_tick = heard_tick + 2
            n_resp = hs.n_of(s0 + resp_tick, s0 + 4 * heard_slot)
            evs.append(dict(tick=resp_tick, kind='id_response', role='slave',
                            channel=hs.peripheral_page_response(clkn_frozen, s0 + resp_tick, paged_lap, paged_uap, n_resp),
                            packet=Raw(bt_fhs.id_bits(paged_lap), 'ID'), lap=paged_lap, uap_for_hec=None, ptype='ID',
                            clk=s0 + resp_tick, hop_clk=s0 + resp_tick, hop_clk_kind='CLKN',
                            hop=dict(substate='peripheral_page_response', clkn_frozen=clkn_frozen,
                                     clkn=s0 + resp_tick, n=n_resp)))
            # the FHS: the master's CLKE frozen at the receipt of the Peripheral's ID, N = 1
            fhs_tick = 4 * heard_slot + 4
            clke_frozen = e0 + resp_tick
            n_fhs = 1
            x = bt_fhs.xprc(clke_frozen, ko, knudge, n_fhs)
            assert x == hs.x_central_response(clke_frozen, ko, knudge, n_fhs)
            fhs_clk = m0 + fhs_tick
            assert fhs_clk % 4 == 0
            sr = int(rng.integers(0, 3))
            f = bt_fhs.page_response(paged_lap, paged_uap, MASTER['lap'], MASTER['uap'], MASTER['nap'],
                                     MASTER['cod'], AM_ADDR, fhs_clk, x, sr=sr)
            evs.append(dict(tick=fhs_tick, kind='fhs', role='master',
                            channel=hs.central_page_response(clke_frozen, e0 + fhs_tick, paged_lap, paged_uap, ko, knudge, n_fhs),
                            packet=f, lap=paged_lap, uap_for_hec=paged_uap, ptype='FHS', clk=fhs_clk,
                            hop_clk=e0 + fhs_tick, hop_clk_kind='CLKE', fhs=f,
                            hop=dict(substate='central_page_response', clke_frozen=clke_frozen, clke=e0 + fhs_tick,
                                     koffset=ko, knudge=knudge, n=n_fhs)))
            ack_tick = fhs_tick + 2
            n_ack = hs.n_of(s0 + ack_tick, s0 + 4 * heard_slot)
            evs.append(dict(tick=ack_tick, kind='id_ack', role='slave',
                            channel=hs.peripheral_page_response(clkn_frozen, s0 + ack_tick, paged_lap, paged_uap, n_ack),
                            packet=Raw(bt_fhs.id_bits(paged_lap), 'ID'), lap=paged_lap, uap_for_hec=None, ptype='ID',
                            clk=s0 + ack_tick, hop_clk=s0 + ack_tick, hop_clk_kind='CLKN',
                            hop=dict(substate='peripheral_page_response', clkn_frozen=clkn_frozen,
                                     clkn=s0 + ack_tick, n=n_ack)))
            poll_tick = fhs_tick + 4
            conn_clk = m0 + poll_tick
            gaps = rng.choice(3, size=followup, p=probs)
            gaps[0] = 0
            types = rng.choice(len(MASTER_TYPES), size=followup)
            types[0] = 0
            answers = rng.choice(len(SLAVE_TYPES), size=followup)
            period = 0
            fchannels = []
            for k in range(followup):
                if k:
                    period += int(gaps[k]) + 1
                t_m = poll_tick + 4 * period
                clk_m = m0 + t_m
                pair = []
                for role, kk, clk, ptype, stream, off in (
                        ('master', 'followup_master', clk_m, MASTER_TYPES[types[k]], (e << 12) | k, 0),
                        ('slave', 'followup_slave', clk_m + 2, SLAVE_TYPES[answers[k]], SLAVE_STREAM | (e << 12) | k, 2)):
                    body = br.seq_body(stream, br.PACKET_TYPES[ptype][4]) if br.PACKET_TYPES[ptype][4] else b''
                    p = br.Packet(MASTER['lap'], MASTER['uap'], clk, ptype, body, lt_addr=AM_ADDR, flow=1, arqn=1,
                                  seqn=k & 1)
                    ch = int(bt_hop.hop_channel(clk, master_addr, None))   # the kernel at this packet's own clock
                    pair.append(ch)
                    evs.append(dict(tick=t_m + off, kind=kk, role=role, channel=ch, packet=p, lap=MASTER['lap'],
                                    uap_for_hec=MASTER['uap'], ptype=ptype, clk=clk, hop_clk=clk, hop_clk_kind='CLK',
                                    hop=dict(substate='basic', clk=clk)))
                fchannels.append(pair)
            fhs_info = dict(tick=fhs_tick, clk=fhs_clk, clke_frozen=clke_frozen, train=train, koffset=ko,
                            knudge=knudge, N=n_fhs, xprc=x, xprc_other_reading=bt_fhs.xprc_other_reading(clke_frozen, ko, knudge, n_fhs))
            identities = dict(paged=dict(PAGED), master=dict(MASTER), am_addr=AM_ADDR)
            clocks = dict(clke0=e0, paged_clkn0=s0, master_clkn0=m0, scanner_x=hs.x_page_scan(s0),
                          scan_channel=scan_ch, clkn_frozen=clkn_frozen, clke_frozen=clke_frozen,
                          clkn_minus_clke=(s0 - e0) % (1 << 28), d_clkn16_12_minus_clke16_12=(d % 32))
        else:
            # one of a pool of 50 scanning devices; a device that answers again has its counter N one higher
            if e < SCANNER_POOL:
                while True:
                    s_lap = int(rng.integers(1, 1 << 24))
                    if s_lap not in RESERVED_LAPS and s_lap not in used_lap:
                        used_lap.add(s_lap)
                        break
                scanners.append(dict(lap=s_lap, uap=int(rng.integers(0, 256)), nap=int(rng.integers(0, 1 << 16)),
                                     cod=int(rng.choice([0x5A020C, 0x200404, 0x240408, 0x1F00, 0x7A020C])),
                                     n0=int(rng.integers(0, 1 << 16)), sent=0))
            sc = scanners[e % SCANNER_POOL]
            n_ctr = sc['n0'] + sc['sent']
            sc['sent'] += 1
            d = window_offset(rng, train)
            hi_i = int(rng.integers(64, (1 << 16) - 64))
            i0 = hi_i << 12 | low12()                        # the inquirer's CLKN at tick 0
            # the scanner's X = CLKN16-12 + N must sit d = X - CLKN16-12(inquirer) inside the train's window
            b_s = ((hi_i + d - n_ctr) % 32)
            hi_s = int(rng.integers(64, (1 << 11) - 64)) << 5 | b_s      # CLKN27-12: bits 16-12 are b_s
            s0 = hi_s << 12 | low12()
            scan_ch = hs.inquiry_scan(s0, n_ctr)
            heard_tick = next(t for t in range(0, 36) if not t & 2 and hs.inquiry(i0 + t, ko, knudge) == scan_ch)
            heard_slot, heard = heard_tick // 4, heard_tick % 4
            n_slots = heard_slot + 1
            for s in range(n_slots):
                for j in (0, 1):
                    t = 4 * s + j
                    evs.append(dict(tick=t, kind='id_inquiry', role='inquirer',
                                    channel=hs.inquiry(i0 + t, ko, knudge), packet=Raw(bt_fhs.id_bits(GIAC), 'ID'),
                                    lap=GIAC, uap_for_hec=None, ptype='ID', clk=i0 + t, hop_clk=i0 + t,
                                    hop_clk_kind='CLKN', hop=dict(substate='inquiry', clkn=i0 + t, koffset=ko, knudge=knudge)))
            resp_tick = heard_tick + 2
            s_clk = s0 + resp_tick                              # the scanner's own clock at the FHS: CLK1-0 = 2 or 3
            x = bt_fhs.xir(s_clk, n_ctr)
            assert x == hs.x_inquiry_response(s_clk, n_ctr)
            f = bt_fhs.inquiry_response(sc['lap'], sc['uap'], sc['nap'], sc['cod'], s_clk, x, access_lap=GIAC,
                                        sr=int(rng.integers(0, 3)))
            evs.append(dict(tick=resp_tick, kind='fhs_inquiry_response', role='scanner',
                            channel=hs.inquiry_response(s_clk, n_ctr), packet=f, lap=GIAC, uap_for_hec=bt_fhs.DCI,
                            ptype='FHS', clk=s_clk, hop_clk=s_clk, hop_clk_kind='CLKN', fhs=f,
                            hop=dict(substate='inquiry_response', clkn=s_clk, n=n_ctr)))
            fhs_tick = resp_tick
            fhs_info = dict(tick=fhs_tick, clk=s_clk, scanner_index=e % SCANNER_POOL, N=n_ctr,
                            clkn16_12=(s_clk >> 12) & 0x1F, xir=x)
            identities = dict(inquirer=None, scanner=dict(lap=sc['lap'], uap=sc['uap'], nap=sc['nap'], cod=sc['cod'],
                                                          clk=s_clk))
            conn_clk, fchannels, poll_tick = None, [], None
            clocks = dict(inquirer_clkn0=i0, scanner_clkn0=s0, scanner_x=hs.x_inquiry_response(s0, n_ctr),
                          scan_channel=scan_ch, scanner_n=n_ctr, d_scanner_x_minus_inquirer_clkn16_12=d % 32)
        # absolute samples
        end = 0
        for ev in evs:
            ev['start'] = origin + ev['tick'] * tick
            ev['exchange'] = e
            end = max(end, ev['start'] + len(ev['packet'].bits) * sps)
        cursor = end
        exch.append(dict(exchange=e, kind=kind, start_sample=origin, symbol_phase=origin % sps, gap_before_ms=gap_ms,
                         snr_base_db=snr_base, train=train, koffset=ko, knudge=knudge, n_id_slots=n_slots,
                         heard_id=heard, heard_slot=heard_slot, heard_tick=heard_tick,
                         first_burst=len(events), last_burst=len(events) + len(evs) - 1,
                         fhs_burst=len(events) + next(i for i, ev in enumerate(evs) if ev['kind'].startswith('fhs')),
                         fhs_tick=fhs_info['tick'], fhs_clk=fhs_info['clk'], fhs_clk27_2=fhs_info['clk'] >> 2,
                         fhs_whitening={k: v for k, v in fhs_info.items() if k not in ('tick', 'clk')},
                         master_clk_at_connection=conn_clk, poll_tick=poll_tick, n_followup=followup if kind == 'page' else 0,
                         followup_basic_channels=fchannels, identities=identities, **clocks))
        events.extend(evs)

    total = cursor + slot
    n = len(events)
    draw = np.random.default_rng([seed, (1 << 29) + 1])
    frac = draw.uniform(0, 1, size=n)
    carrier_phase = draw.uniform(0, 2 * np.pi, size=n)
    jitter = np.clip(draw.normal(0, JITTER_SIGMA, size=n), -JITTER_CLIP, JITTER_CLIP)
    jobs, entries = [], []
    for i, ev in enumerate(events):
        snr = round(exch[ev['exchange']]['snr_base_db'] + float(jitter[i]), 2)
        win = in_window(ev['channel'])
        entry = {'start_sample': ev['start'], 'timing_frac': float(frac[i]), 'symbol_phase': ev['start'] % sps}
        entry.update(kind=ev['kind'], lap=ev['lap'], uap_for_hec=ev['uap_for_hec'], exchange=ev['exchange'],
                     channel=ev['channel'], channel_mhz=2402 + ev['channel'], snr_db=snr, in_window=win, rendered=win,
                     role=ev['role'], ptype=ev['ptype'], clk=ev['clk'], hop_clk=ev['hop_clk'],
                     hop_clk_kind=ev['hop_clk_kind'], hop=ev['hop'], tick=ev['tick'], slot_index=ev['tick'] // 2)
        p = ev['packet']
        if ev['ptype'] == 'FHS':
            side = p.sidecar()
            for key in ('header18', 'payload_full_hex', 'payload_valid', 'lt_addr', 'flow', 'arqn', 'seqn'):
                entry[key] = side[key]
            entry['fhs'] = dict(lap=p.lap, uap=p.uap, nap=p.nap, class_of_device=p.cod, am_addr=p.am_addr,
                                lt_addr_header=p.lt_addr, clk27_2=p.clk27_2, sr=p.sr, sp=p.sp, eir=p.eir,
                                header_uap=p.header_uap, tx_clk=p.tx_clk, access_lap=p.access_lap,
                                whitening_x=p.whitening_x,
                                whitening_register=bt_fhs.whitening_register(p.whitening_x),
                                reserved=p.reserved, previously_used=p.previously_used,
                                implied_clk6_1=(p.tx_clk >> 1) & 0x3F, whitening_rule=pg.WHITENING_RULE[ev['kind']])
        elif ev['ptype'] != 'ID':
            side = p.sidecar()
            del side['air_bits'], side['clk'], side['ptype']
            entry.update(side)
        entries.append(entry)
        if win:
            jobs.append(dict(packet=p, start=ev['start'], frac=float(frac[i]), phase=float(carrier_phase[i]),
                             amp=amplitude(snr), channel=ev['channel']))

    # per exchange: what is in the window, and the clock lock a receiver of the window can make
    n_unique = n_solved = 0
    for E in exch:
        bs = entries[E['first_burst']:E['last_burst'] + 1]
        win = [b for b in bs if b['rendered']]
        E['n_bursts'] = len(bs)
        E['n_bursts_in_window'] = len(win)
        E['fhs_in_window'] = any(b['ptype'] == 'FHS' and b['rendered'] for b in bs)
        E['id_in_window'] = sum(b['ptype'] == 'ID' and b['kind'] in ('id_page', 'id_inquiry') and b['rendered'] for b in bs)
        E['id_page_in_window'] = E['id_in_window'] if kind == 'page' else None
        E['id_inquiry_in_window'] = E['id_in_window'] if kind == 'inquiry' else None
        E['n_id_bursts'] = sum(b['kind'] in ('id_page', 'id_inquiry') for b in bs)
        E['heard_id_in_window'] = next(b for b in bs if b['tick'] == E['heard_tick'] and b['kind'] in ('id_page', 'id_inquiry'))['rendered']
        if kind == 'page':
            E['id_response_in_window'] = next(b for b in bs if b['kind'] == 'id_response')['rendered']
            E['id_ack_in_window'] = next(b for b in bs if b['kind'] == 'id_ack')['rendered']
            fol = [b for b in bs if b['kind'] in ('followup_master', 'followup_slave')]
            E['first_poll_in_window'] = next(b for b in bs if b['kind'] == 'followup_master')['rendered']
            E['n_followup_in_window'] = sum(b['rendered'] for b in fol)
            E['master_clk16_12_changes_in_followup'] = (E['master_clkn0'] >> 12) & 0x1F != (max(b['clk'] for b in fol) >> 12) & 0x1F
            obs = [((b['tick'] - E['poll_tick']) // 2, b['channel']) for b in fol if b['rendered']]
            E['n_clock_observations'] = len(obs)
            E['first_followup_clk27_1'] = E['master_clk_at_connection'] >> 1
            if obs:
                lock = clock_solutions(obs, master_addr)
                assert E['first_followup_clk27_1'] in lock, 'the true clock is not among its own solutions'
                E['n_clock_solutions'] = len(lock)
                E['clock_solutions'] = lock if len(lock) <= MAX_LISTED_SOLUTIONS else None
                E['clock_unique'] = len(lock) == 1
                n_solved += 1
                n_unique += len(lock) == 1
            else:
                E['n_clock_solutions'] = None
                E['clock_solutions'] = None
                E['clock_unique'] = False
    counts = {}
    for b in entries:
        counts[b['kind']] = counts.get(b['kind'], 0) + 1
    in_counts = {}
    for b in entries:
        if b['rendered']:
            in_counts[b['kind']] = in_counts.get(b['kind'], 0) + 1
    hist = np.bincount([b['channel'] for b in entries], minlength=79).tolist()
    sizes = [E['n_clock_solutions'] for E in exch if kind == 'page' and E['n_clock_solutions']]
    if kind == 'page':
        more = [s for s in sizes if s > 1]
        lock_note = CLOCK_LOCK_NOTE_PAGE % (n_unique, exchanges,
                                            ('%d to %d solutions' % (min(more), max(more)) if more else 'no ambiguity'),
                                            MAX_LISTED_SOLUTIONS)
        if exchanges - n_solved:
            lock_note += ' %d exchanges have no in-window packet on the master LAP at all (no lock possible).' % (exchanges - n_solved)
    else:
        lock_note = CLOCK_LOCK_NOTE_INQUIRY
    sidecar = {
        'generator': 'SDR scripts/bt_synth_conf.py',
        'generator_commit': bt_synth.commit(),
        'lap': None, 'uap': None, 'clk': None, 'ptype': None,
        'clk_convention': CLOCK_NOTE,
        'hopping': True,
        'hop_channels': sorted({b['channel'] for b in entries}),
        'hop_channels_in_window': sorted({b['channel'] for b in entries if b['rendered']}),
        'channel_mhz': None, 'bt_channel': None,
        'address_for_hop': master_addr,
        'hop_kernel': 'apps/bt_hop_substates.py (page, page scan, responses, inquiry) and apps/bt_hop.py (basic sequence): '
                      'Core v6.0 Vol 2 Part B s2.6, Table 2.2, Table 2.3, EQ 1-8',
        'clock_lock_note': lock_note,
        'clock_unique_exchanges': n_unique if kind == 'page' else None,
        'clock_lock_exchanges': n_solved if kind == 'page' else None,
        'sample_rate': fs, 'center_mhz': center_mhz, 'cfo_hz': 0.0, 'timing_frac': None, 'start_offset': 0,
        'symbol_phase': None, 'snr_db': None,
        'snr_range_db': list(SNR_RANGE), 'snr_jitter': 'N(0, %g) dB clipped to +-%g dB, per burst, around exchanges[].snr_base_db' % (JITTER_SIGMA, JITTER_CLIP),
        'per_burst_keys': ['start_sample', 'timing_frac', 'symbol_phase', 'snr_db', 'clk', 'hop_clk', 'hop_clk_kind', 'hop',
                           'ptype', 'channel', 'channel_mhz', 'kind', 'lap', 'uap_for_hec', 'exchange', 'role', 'tick',
                           'slot_index', 'in_window', 'rendered'],
        'snr_bw_hz': 1e6, 'noise_1mhz': NOISE_1MHZ,
        'modulation': {'scheme': 'GFSK', 'bt': br.GFSK_BT, 'h': br.GFSK_H, 'symbol_rate': br.SYMBOL_RATE},
        'slot_samples': slot, 'tick_samples': tick,
        'symbol_phases': [E['symbol_phase'] for E in exch],
        'symbol_phase_note': 'random per exchange, uniform 0..%d: exchange e starts at sample exchanges[e].start_sample, '
                             'whose phase (mod %d) is symbol_phases[e]; a tick is %g symbols, so a burst on an odd tick '
                             'sits %d samples away; each burst has its own random timing_frac' % (sps - 1, sps, tick / sps, sps // 2),
        'start_sample_meaning': "the first sample of the access code's preamble, before timing_frac is added",
        'air_bits_omitted': True,
        'file_kind': kind, 'n_exchanges': exchanges, 'followup_per_exchange': followup if kind == 'page' else 0,
        'idle_probs': probs, 'gap_ms': list(GAP_MS),
        'exchanges': exch,
        'identities': (dict(paged=dict(PAGED), master=dict(MASTER), am_addr=AM_ADDR, giac=GIAC) if kind == 'page' else
                       dict(inquirer=None, giac=GIAC, scanner='per exchange: exchanges[].identities.scanner')),
        'window': dict(channels=list(WINDOW), mhz=list(WINDOW_MHZ), center_mhz=center_mhz,
                       half_width_mhz=WINDOW_HALF_WIDTH_MHZ, why=WINDOW_NOTE),
        'spec_notes': SPEC_NOTES, 'conformance': CONFORMANCE,
        'n_bursts': len(entries), 'n_bursts_in_window': sum(b['rendered'] for b in entries),
        'n_bursts_not_rendered': sum(not b['rendered'] for b in entries),
        'kind_counts': counts, 'kind_counts_in_window': in_counts,
        'channel_histogram_all': hist,
        'n_exchanges_fhs_in_window': sum(E['fhs_in_window'] for E in exch),
        'n_exchanges_heard_id_in_window': sum(E['heard_id_in_window'] for E in exch),
        'n_exchanges_master_clk16_12_changes': (sum(E['master_clk16_12_changes_in_followup'] for E in exch) if kind == 'page' else None),
        'n_samples': total,
        'bursts': entries,
    }
    return sidecar, jobs, total


def synthesise_conf(seed=9001, block=pg.BLOCK, noise=True, **spec):
    """The samples and the sidecar of a capture, in memory: for a small run."""
    sidecar, jobs, total = plan_conf(seed=seed, **spec)
    iq = np.concatenate(list(pg.render_blocks(sidecar, jobs, total, seed, block, noise)))
    return iq, sidecar


def write_conf(name, out_dir, seed=9001, **spec):
    """Make one capture and write ``synth_<name>.cf32`` block by block and ``synth_<name>.json``."""
    sidecar, jobs, total = plan_conf(seed=seed, **spec)
    os.makedirs(out_dir, exist_ok=True)
    iq_path = os.path.join(out_dir, 'synth_%s.cf32' % name)
    side_path = os.path.join(out_dir, 'synth_%s.json' % name)
    with open(iq_path, 'wb') as f:
        for out in pg.render_blocks(sidecar, jobs, total, seed):
            out.astype('<c8', copy=False).tofile(f)
    with open(side_path, 'w') as f:
        json.dump(sidecar, f, separators=(',', ':'))
    print('%s  %d samples, %.1f ms, %d %s exchanges, %d bursts, %d in the window, FHS in window in %d exchanges'
          % (iq_path, total, total / sidecar['sample_rate'] * 1e3, sidecar['n_exchanges'], sidecar['file_kind'],
             sidecar['n_bursts'], sidecar['n_bursts_in_window'], sidecar['n_exchanges_fhs_in_window']))
    print(side_path)
    return iq_path, side_path


CONF_SET = [('conf_page_hop_win', dict(kind='page', exchanges=200, followup=110, seed=9001)),
            ('conf_inq_hop_win', dict(kind='inquiry', exchanges=200, followup=0, seed=9002))]
SETS = {'conf': CONF_SET}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('name', nargs='?', help='the file is synth_<name>.cf32; not used with --set')
    ap.add_argument('--set', choices=sorted(SETS), help='write the named set, with its own seeds and settings')
    ap.add_argument('--kind', choices=['page', 'inquiry'], help='default page')
    ap.add_argument('--exchanges', type=int, help='default 200')
    ap.add_argument('--followup', type=int, help='master packets after each page FHS; default 110')
    ap.add_argument('--seed', type=int, help='default 9001')
    ap.add_argument('--out', default=DEFAULT_OUT, help='folder; default ' + DEFAULT_OUT)
    args = ap.parse_args(argv)
    per_file = ['kind', 'exchanges', 'followup', 'seed']
    if args.set:
        stray = [o for o in per_file if getattr(args, o) is not None]
        if args.name is not None or stray:
            ap.error('--set takes everything from its table: no name and no option but --out')
        runs = SETS[args.set]
    else:
        if args.name is None:
            ap.error('give the name of the file, or --set')
        spec = {k: v for k, v in (('kind', args.kind), ('exchanges', args.exchanges), ('followup', args.followup),
                                  ('seed', args.seed)) if v is not None}
        if spec.get('kind') == 'inquiry':
            spec['followup'] = 0
        runs = [(args.name, spec)]
    try:
        for name, spec in runs:
            write_conf(name, args.out, **spec)
    except ValueError as e:
        ap.error(str(e))


if __name__ == '__main__':
    main()
