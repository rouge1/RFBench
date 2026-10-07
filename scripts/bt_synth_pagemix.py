#!/usr/bin/env python3
"""Joiners among dense piconet traffic, and the same traffic with no joiner at all: two big files for bluey.

    python scripts/bt_synth_pagemix.py --set pagemix  [--out DIR]
    python scripts/bt_synth_pagemix.py --set connonly [--out DIR]
    python scripts/bt_synth_pagemix.py small_a --profile pagemix --seed 9301 --blocks 6 --joiners 4 --out DIR

    from scripts import bt_synth_pagemix as pm
    plan = pm.plan_file('pagemix', seed=9301, blocks=6, n_page=2, n_inquiry=2)
    iq = np.concatenate(list(pm.render_blocks(plan)))

bluey-ox-walker's paging stack (page-ID anchors, FHS recovery, clock lock) found none of the six joiner
exchanges of ``mixed/``: they were few, half of them at symbol phase 20 where its single-grid detector is blind,
and their IDs were mostly out of the window. It asked for a **positive** set with many joiner exchanges inside
realistic dense piconet traffic (``pagemix``, seed 9301) and a **negative** set with the same kind of dense
traffic and no joiner (``connonly``, seed 9302), on which every page or inquiry event its stack reports is false.
Both are 40 MS/s on 2441.0 MHz, exactly 40 samples per symbol (10 after its decimation by 4), the one constant noise
floor of every file, white.

The machinery is that of ``scripts/bt_synth_mixed.py``, imported and not edited: the piconets (own LAP and UAP,
``clk0``, slot grid, level, the adapted map 31-50, role-aware LMP, the slave's answer ``n`` slots after the master's
packet on the master's channel, a master packet and its answer shifted as a pair to symbol phase 0 or 20), the
joiners built by ``bt_synth_conf.plan_conf`` itself with the paged and master identities swapped in for the call
(``mock.patch``), the rendering (``render_blocks``, every sample the same as ``bt_synth_mixed`` gives for the
same burst), and the rule that the file is the linear sum of its bursts. What is new:

* **ten piconets** and a **traffic mix** by file. ``pagemix``: the master transmits in 0.4 to 0.7 of its master
  slots; of its packets about 80 % are NULL or POLL (a share per piconet, 75 to 85 %) and about 20 % data (DM1,
  with LMP in about half of them, DH1, DM3, DH3, DM5, DH5); the slave answers NULL, DM1 or DH1 as in ``mixed``.
  ``connonly``: 0.6 to 0.9 for six piconets, 0.88 to 0.95 for two busy ones, 0.25 to 0.45 for two sparse ones;
  about 10 % data; the slave answers NULL 97 % of the time. A NULL or POLL is 126 bits (126 us) and there are very many of
  them; some piconets answer 625 us after the master packet, which is the spacing of a page ID and its response:
  that is the point of the negative set.
  A connection's ordinary traffic has no ID packet (68 bits) and no FHS: the shortest packet is a NULL or a POLL
  (IDs and FHS also occur in paging, inquiry and, by Core Vol 2 Part B 6.5.1.1 and 6.5.1.4, role switch). ``connonly``
  has no paging, no inquiry and no role-switch traffic, so it has no joiner, no ID and no FHS anywhere;
* **twenty joiner exchanges** in ``pagemix`` (12 page, 8 inquiry), one in each twentieth of the file at a random time,
  each with its own identities (devices 10 to 29, ``kind`` ``joiner_page`` or ``joiner_inquiry``, ``ours``), all LAPs
  different from the piconets' and each other's (and every sync word 22 bits from every other, the GIAC's included);
  the exchange is ``bt_synth_conf.plan_conf``'s own (spec-true page, page response, inquiry and inquiry response
  hop sequences, the basic-sequence follow-up of 12 master packets for a page exchange, none for an inquiry), its
  SNR base **14 to 22 dB** per exchange (0.1 dB; the conf builder's SNR range is replaced for the call by
  ``mock.patch.object(conf, 'SNR_RANGE', (14, 22))``) and its burst jitter N(0, 1) clipped to +-3 dB;
* **the symbol phase of an exchange is random, 0 to 39**: the exchange is shifted as a whole to a random sample.
  A tick is 12500 samples, 12500 = 20 mod 40, so the second ID of a slot sits 20 samples further: the phases of one
  exchange are ``p`` and ``p + 20 (mod 40)``, and every burst has its own timing fraction as well;
* **the FHS is in the window (channels 27 to 51) in 16 of the 20 exchanges and out of it in 4**. The clocks of an
  exchange (CLKE, CLKN and the offsets ``plan_conf`` draws from its seed) are the free parameters; each exchange's
  seed is redrawn until the FHS falls where it is wanted. This is a *selection* of legal parameters, not a change of a
  hop kernel; the number of draws is ``exchanges[].n_draws`` and the summary ``joiner_selection``;
* the truth sidecar is a header (``synth_<name>.json``: the devices, the exchange table, the counts, the notes, the
  first 200 bursts) and the bursts, one JSON object per line, in ``synth_<name>.bursts.jsonl.gz``: sorted by
  ``start_sample + timing_frac``, ``index`` the line number, ``overlaps`` referring to indices. Read it:

      import gzip, json
      with gzip.open('synth_pagemix.bursts.jsonl.gz', 'rt') as f:
          for line in f:                                  # one line at a time: up to a million lines
              burst = json.loads(line)
              ...                                         # use it here, inside the with block

A piconet burst has no ``air_bits``: rebuild it with ``bt_br_frame.Packet(lap, uap, clk, ptype,
bytes.fromhex(payload_hex), lt_addr=lt_addr, flow=flow, arqn=arqn, seqn=seqn, llid=llid or 2, payload_flow=1)``
(its ``bits`` are the air bits; ``air_bits_length`` is their number). A joiner burst keeps ``air_bits`` when it is
in the window.
"""
import argparse
import gzip
import json
import math
import multiprocessing
import os
import sys
import time
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from scripts import bt_synth, bt_synth_conf as conf, bt_synth_hop as hop  # noqa: E402
from scripts import bt_synth_mixed as g  # noqa: E402
from scripts.bt_synth_interf import NOISE_1MHZ, amplitude_for, noise_block  # noqa: E402

FS = g.FS
CENTER_MHZ = g.CENTER_MHZ
SPS = g.SPS
SLOT = g.SLOT
BLOCK_SAMPLES = g.BLOCK_SAMPLES
MAP = g.MAP
MASK = g.MASK
GRID_BASE = g.GRID_BASE
FOLLOWUP = g.FOLLOWUP
LEVEL_RANGE = g.LEVEL_RANGE
JITTER_DB = g.JITTER_DB
MIN_SYNC_DISTANCE = g.MIN_SYNC_DISTANCE
GIAC = g.GIAC
WINDOW = g.WINDOW
JOINER_SNR_RANGE = (14.0, 22.0)
FHS_REQUIRED_IN = 16                                  # of 20; the others have their FHS out of the window
DEFAULT_OUT = '/media/user/4TB/sdr-synth-tmp'
RENDER_WORKERS = 6
BUSY_PARTIAL_MAX = 1 << 12

MASTER_TYPES = g.MASTER_TYPES                          # NULL POLL DM1 DH1 DM3 DH3 DM5 DH5
SLAVE_TYPES = g.SLAVE_TYPES

#: The two files. ``p_master_tx`` is a list of (count, low, high) groups, the devices in order.
PROFILES = {
    'pagemix': dict(
        seed=9301, blocks=300, n_piconets=10, n_not_ours=2, n_page=12, n_inquiry=8,
        p_master_tx=[(10, 0.4, 0.7)], null_poll_share=(0.75, 0.85), slave='mixed', p_slave_answer=(0.80, 0.95),
        negative_set=False, ids_min=4,
        text='NULL and POLL heavy: the master transmits in 0.4 to 0.7 of its master slots, about 80 % of its packets '
             'are NULL or POLL and 20 % data; the slave answers NULL, DM1 or DH1 as in mixed/'),
    'connonly': dict(
        seed=9302, blocks=600, n_piconets=10, n_not_ours=2, n_page=0, n_inquiry=0,
        p_master_tx=[(6, 0.6, 0.9), (2, 0.88, 0.95), (2, 0.25, 0.45)], null_poll_share=(0.88, 0.92), slave='null',
        p_slave_answer=(0.90, 0.99), negative_set=True, ids_min=0,
        text='NULL and POLL dense: the master transmits in 0.6 to 0.9 of its master slots for six piconets, 0.88 to 0.95 '
             'for two busy ones and 0.25 to 0.45 for two sparse ones, about 90 % of its packets are NULL or POLL, the '
             'slave answers NULL 97 % of the time, mostly 625 us after the master packet'),
}

SLAVE_NULL_PROBS = (0.97, 0.015, 0.015)
DATA_ALPHA = (3.0, 3.0, 2.0, 2.0, 1.5, 1.5)             # DM1 DH1 DM3 DH3 DM5 DH5 among the data packets

_CONF_PICONETS = (
    "The ten piconets' traffic is on the ADAPTED map 31-50 (the 20 channels 2433 to 2452 MHz) with bt_hop.hop_channel(clk, "
    "uap << 24 | lap, mask) at each piconet's own address and clock; their sequences differ because the addresses and clocks "
    "differ. The slave answers on its master's channel (the same channel mechanism of the adapted sequence, Core Vol 2 Part B "
    "2.6.3, Figure 2.15; apps/bt_hop.py note 4), one slot after the master's packet (after its last slot, for a 3- or 5-slot "
    "packet). The piconets are unsynchronised: random master clocks, random slot grids. ")
_CONF_TAIL = (
    "Conformant: the packet bits, headers, HEC, CRC, FEC, whitening, the hop kernels and the slot rules. Not modelled: real "
    "traffic patterns, power control, retransmission, flow control (SEQN and ARQN are plausible, not a protocol run), the "
    "slave's answer depending on what the master sent, a second slave, SCO or eSCO, sniff, any drift between the piconets' "
    "clocks.")
def conformance_positive(n_join, n_b_train, n_page):
    return (
        _CONF_PICONETS + "The %d joiner exchanges (page and inquiry) are the specification's own page, page response, inquiry and "
        "inquiry response sequences and, for a page exchange's follow-up, the basic connection sequence (bt_synth_conf.plan_conf, "
        "apps/bt_hop_substates.py, apps/bt_hop.py with no map): different from the 31-50 map of the piconets, and intended. "
        "EACH JOINER EXCHANGE IS AN EXCERPT, NOT A COMPLETE PROCEDURE: Core Vol 2 Part B 2.6.4.2 has paging start with the A "
        "train, and %d of the %d page exchanges here begin on the B train (exchanges[].train): each is an excerpt of a page "
        "procedure already running, after at least one 1.28 s repetition of the A train that did not reach the scanner, and "
        "those preceding repetitions are not in the file. Also not modelled in the joiners (as in bt_synth_conf): the train "
        "repetition count Npage and the knudge of EQ 4 (knudge 0), page scan and inquiry scan windows and interlaced scan, "
        "the RAND back-off of the inquiry scan and an inquirer's continued probing, a retransmitted FHS with an updated clock "
        "(every exchange acknowledges its first FHS), and the clock lock a receiver can make on a joiner's packets "
        "(bt_synth_conf's clock_solutions, a 2**27 search, is not run). Selected, not random: the exchanges' clocks are chosen "
        "(joiner_selection). " % (n_join, n_b_train, n_page) + _CONF_TAIL)


CONFORMANCE_NEGATIVE = _CONF_PICONETS + "There are no joiners in this file. " + _CONF_TAIL

SYMBOL_PHASE_TEXT = (
    "Piconet bursts: a master packet and its answer are shifted as a pair to the next sample that is 0 (even pair) or "
    "20 (odd pair) mod 40, so symbol_phase is 0 or 20. Joiner bursts: each exchange starts at a random sample, so its "
    "first burst has a random phase p in 0..39 (exchanges[].symbol_phase_start); a tick is 12500 samples and "
    "12500 = 20 (mod 40), so the second ID of a slot (and every odd tick) is at p + 20 (mod 40). Every burst has "
    "its own random timing_frac in [0, 1).")

WINDOW_TEXT = g.WINDOW_TEXT

READ_NOTE = ("How to read the bursts: import gzip, json; f = gzip.open('synth_<name>.bursts.jsonl.gz', 'rt'); "
             "bursts = (json.loads(line) for line in f)  # one burst per line, line number = index, sorted by start_sample + "
             "timing_frac, overlaps[].burst is an index; up to a million lines, so iterate and do not make a list.")

NO_ID_NOTE = ("There is no ID packet and no FHS packet in the connection state's ordinary traffic: an ID is 68 bits (the access "
              "code alone) and an FHS is 366; both occur in paging and inquiry and their responses, and (Core Vol 2 Part B 6.5.1.1 "
              "and 6.5.1.4) during role switch. The shortest packets of a connection are NULL and POLL, 126 bits (126 us, "
              "header and access code). This file has no paging, no inquiry and no role-switch traffic, so it has none of "
              "them: no ID, no FHS, no page, no inquiry, no joiner. Its dense NULL/POLL traffic answers "
              "625 us after the master packet on the master's channel, the spacing of a page ID and its response, so that "
              "every page or inquiry event a receiver reports on it is false.")

NOTES = [
    "This sidecar is an ANSWER KEY: devices[], exchanges[] and every burst's identity, channel, clock and kind are in it. A "
    "receiver under test must not be given it. The 'ours' flag is a policy label (the LAPs the receiver is told to "
    "follow), not an RF property.",
    "kind is the packet type (ID, NULL, POLL, FHS, DM1, DH1, DM3, DH3, DM5, DH5); ptype holds the same value. role is, for "
    "a piconet burst, 'master' or 'slave'; for a joiner burst the exchange builder's own name: id_page, id_response, fhs, "
    "id_ack, followup_master, followup_slave (page exchange), id_inquiry, fhs_inquiry_response (inquiry exchange; the task's "
    "'inquiry_fhs'). exchange_role is the builder's master/slave/inquirer/scanner side. exchange is the joiner exchange "
    "index 0..n-1, null for a piconet burst.",
    "A piconet burst has no air_bits (hundreds of thousands of them): rebuild it from type, lt_addr, flow, arqn, seqn, clk, "
    "uap, llid and payload_hex with bt_br_frame.Packet(lap, uap, clk, ptype, bytes.fromhex(payload_hex), lt_addr=lt_addr, "
    "flow=flow, arqn=arqn, seqn=seqn, llid=llid or 2, payload_flow=1).bits. A joiner burst keeps air_bits (hex, padded "
    "with zero bits to a whole byte: use air_bits_length) when it is rendered. air_bits_omitted is therefore true for the "
    "piconets and false for the joiners; see air_bits_omitted_for.",
    "The burst window is the packet's air time (first preamble sample to the end of the last bit): [start_sample + "
    "timing_frac, start_sample + timing_frac + air_bits_length * 40). overlaps lists every other RENDERED burst that overlaps "
    "it in time by any amount and whose channel is within 1; collision is a same-channel overlap; overlap_frac the "
    "covered fraction. sir_db is this burst's snr_db less the other's, the ratio of the total envelope powers; for an "
    "adjacent channel the interference in the victim's 1 MHz is 25 to 32 dB lower. Bursts that are not rendered "
    "(a joiner burst outside channels 27-51) have no overlaps and no air_bits_length.",
    "expected_page_event_possible = n_id_rendered_collision_free >= 4. Four is bluey's stack's rule (it needs at least four "
    "IDs of the train), not the specification's. A burst is collision-free when its collision flag is false (no same-channel "
    "overlap; an adjacent-channel overlap does not count).",
    "clock_unique and clock_solutions are null: the search over 2**27 clocks (bt_synth_conf.clock_solutions) is the "
    "conf file's own business, builds a 2**27-entry table per master address, and is stubbed here, as in mixed/ (not timed).",
    "The joiners' SNR is a base per exchange, uniform 14 to 22 dB (0.1 dB), plus a jitter N(0, 1) clipped to +-3 dB per burst; "
    "the piconets' is a level per device (8 to 22 dB) plus a jitter uniform in +-1 dB.",
    "The FHS-in-window rule (16 of 20) is a selection among legal parameters, in two parts. (1) For each exchange the builder's "
    "seed (which draws CLKE, CLKN, the offset between them and the master's clock) is drawn again until the FHS lands where the "
    "plan wants it; for a PAGE exchange the master's and the paged device's LAPs are also drawn again with every seed (the paged "
    "LAP decides which channels the train visits). (2) A criterion that is not in the task: an exchange whose FHS is wanted in "
    "the window must also have at least 4 of its train's IDs in the window (before collisions), so that most joiners have the "
    "IDs a page event needs; the 4 exchanges whose FHS is wanted out of the window have no ID criterion. Without (2) a trial "
    "plan had 6 of 20 exchanges with expected_page_event_possible; with it 16 of 20 (the draws that did not meet it are "
    "discarded). No hop kernel is changed. exchanges[].n_draws counts the draws.",
    READ_NOTE,
    "Nothing in this file is real traffic: random packet types at a random rate, LMP PDUs (about half of the DM1) by role as in "
    "mixed/ (a slave never sends LMP_set_AFH, TID 0 for a transaction the Central started and 1 for one the Peripheral "
    "started), random data with an L2CAP-like header; SEQN is the count of the device's packets, ARQN and FLOW are 1.",
    "Every burst is a constant-envelope GFSK burst of amplitude sqrt(noise_1mhz * 10 ** (snr_db / 10)); the file minus the "
    "noise is the exact sum of the bursts, so a collision is a linear sum and no burst is changed by another. The noise "
    "is white, per-component sigma sqrt(noise_1mhz * 40 / 2), block i from default_rng([seed, 1, i]).",
    "The piconets' clocks do not drift and the sample clock is the file's; a slot is exactly 25000 samples.",
]


# --- the container -----------------------------------------------------------------------------

#: One line that re-gzips a container and checks it reproduces the bytes (it is also in the header's notes).
REGZIP = ("import gzip, io; p = 'synth_<name>.bursts.jsonl.gz'; raw = gzip.decompress(open(p, 'rb').read()); out = io.BytesIO(); "
          "g = gzip.GzipFile(filename='', mode='wb', fileobj=out, compresslevel=6, mtime=0); g.write(raw); g.close(); "
          "assert out.getvalue() == open(p, 'rb').read()")


def open_gzip_lines(path):
    """A deterministic gzip writer: compresslevel=6, mtime=0, no file name (filename=''), so the same lines give the same bytes.
    ``REGZIP`` is a one-line re-gzip that reproduces a written container."""
    raw = open(path, 'wb')
    return raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, compresslevel=6, mtime=0)


def read_bursts(path):
    """The lines of a ``.bursts.jsonl.gz`` as dicts, one at a time."""
    with gzip.open(path, 'rt') as f:
        for line in f:
            yield json.loads(line)


# --- the identities --------------------------------------------------------------------------------

def draw_laps(rng, count, taken_laps, taken_syncs):
    """``count`` new LAPs, outside the reserved range, not the GIAC, not taken, each sync word at least MIN_SYNC_DISTANCE bits
    from every taken one and each other. ``taken_laps`` and ``taken_syncs`` grow."""
    out = []
    while len(out) < count:
        lap = int(rng.integers(1, 1 << 24))
        if lap in g.RESERVED_LAPS or lap == GIAC or lap in taken_laps:
            continue
        s = g.sync_int(lap)
        if any(g.hamming(s, t) < MIN_SYNC_DISTANCE for t in taken_syncs):
            continue
        out.append(lap)
        taken_laps.append(lap)
        taken_syncs.append(s)
    return out


def split(rng, groups):
    """Per-device values from a list of (count, low, high) groups, in order."""
    out = []
    for count, lo, hi in groups:
        out.extend(round(float(rng.uniform(lo, hi)), 3) for _ in range(count))
    return out


def traffic_profile(rng, prof, p_tx):
    share = float(rng.uniform(*prof['null_poll_share']))
    null_frac = float(rng.uniform(0.35, 0.65))
    data = rng.dirichlet(DATA_ALPHA) * (1.0 - share)
    master = [share * null_frac, share * (1 - null_frac)] + [float(x) for x in data]
    if prof['slave'] == 'mixed':
        slave = [float(x) for x in rng.dirichlet(g.SLAVE_ALPHA)]
    else:
        slave = list(SLAVE_NULL_PROBS)
    return dict(p_master_tx=p_tx, p_slave_answer=round(float(rng.uniform(*prof['p_slave_answer'])), 3),
                master_types=list(MASTER_TYPES), master_probs=master, slave_types=list(SLAVE_TYPES), slave_probs=slave,
                dm1_lmp_fraction=g.DM1_LMP_FRACTION, null_poll_share=round(share, 4))


# --- one piconet's bursts (a worker may make these) -----------------------------------------------

def piconet_bursts(args):
    """Every burst of one piconet as ``(entry, job)`` pairs, no ``air_bits`` in the entry."""
    dev, seed, total = args
    out = []
    for b in g.plan_piconet(dev, seed, total, LEVEL_RANGE):
        p = b['packet']
        entry = {'start_sample': int(b['start']), 'timing_frac': b['timing_frac']}
        side = p.sidecar()
        del side['air_bits']
        entry.update(side)
        lmp = b['lmp']
        bits = np.array(p.bits, dtype=np.uint8)
        entry.update(
            air_bits_length=len(bits), lap=dev['lap'], uap=dev['uap'], llid=b['llid'], payload_length=len(b['body']),
            lmp_opcode=None if lmp is None else g.bt_lmp.PDUS[lmp['name']]['opcode'],
            lmp_name=None if lmp is None else lmp['name'], lmp_tid=None if lmp is None else lmp['tid'],
            lmp_params=None if lmp is None else {k: (v.hex() if isinstance(v, bytes) else v) for k, v in lmp['params'].items()},
            slots=b['slots'], end_sample=int(b['start'] + len(bits) * SPS), channel=b['channel'],
            channel_mhz=hop.channel_mhz(b['channel']), symbol_phase=int(b['start'] % SPS), snr_db=b['snr_db'],
            jitter_db=b['jitter_db'], burst_phase=b['burst_phase'], device=dev['id'], role=b['role'], ours=dev['ours'],
            kind=p.ptype, piconet_slot=b['slot'], pair=b['pair'], hop_clk=b['hop_clk'], exchange=None,
            in_window=True, rendered=True, slot_boundary_sample=b['boundary'])
        job = dict(packet=g.Bits(bits), start=int(b['start']), frac=b['timing_frac'], phase=b['burst_phase'],
                   amp=amplitude_for(b['snr_db']), channel=b['channel'])
        out.append((entry, job))
    return out


# --- the joiners -------------------------------------------------------------------------------------

class _FakeLock(list):
    def __contains__(self, item):
        return True


def build_exchange(kind, conf_seed, ident):
    """One exchange by ``bt_synth_conf.plan_conf`` with this exchange's identities and the 14-22 dB SNR range, unshifted:
    ``(exchange record, entries, jobs)``."""
    paged = dict(lap=ident['paged_lap'], uap=ident['paged_uap'])
    master = dict(lap=ident['lap'], uap=ident['uap'], nap=ident['nap'], cod=ident['cod'])
    with mock.patch.object(conf, 'PAGED', paged), mock.patch.object(conf, 'MASTER', master), \
            mock.patch.object(conf, 'SNR_RANGE', JOINER_SNR_RANGE), \
            mock.patch.object(conf, 'clock_solutions', lambda obs, addr: _FakeLock([0])):
        side, jobs, _ = conf.plan_conf(kind=kind, exchanges=1, followup=FOLLOWUP if kind == 'page' else 0, seed=conf_seed)
    return side['exchanges'][0], side, jobs


def draw_exchange(rng, kind, need_fhs, taken_laps, taken_syncs, ids_min=0, max_draws=2000):
    """Draw identities and a builder seed until the FHS is (or is not) in the window as wanted: ``(ident, ex, side, jobs,
    n_draws)``. A page exchange's two LAPs are drawn again with each seed (the paged device's LAP decides which channels
    its train visits, so the window's share of the train depends on it); an inquiry exchange's scanner LAP comes from the
    builder and is checked against every LAP taken. ``taken_laps`` and ``taken_syncs`` grow by the exchange's own."""
    base = dict(uap=int(rng.integers(0, 256)), nap=int(rng.integers(0, 1 << 16)),
                cod=int(rng.choice([0x5A020C, 0x200404, 0x240408, 0x7A020C])), paged_uap=int(rng.integers(0, 256)))
    attempts = []
    for draws in range(1, max_draws + 1):
        ident = dict(base)
        laps, syncs = list(taken_laps), list(taken_syncs)
        if kind == 'page':
            ident['lap'], ident['paged_lap'] = draw_laps(rng, 2, laps, syncs)
        else:
            ident['lap'], ident['paged_lap'] = 0x123456, 0x654321         # unused by an inquiry exchange
        seed = int(rng.integers(1, 1 << 30))
        ex, side, jobs = build_exchange(kind, seed, ident)
        att = dict(conf_seed=seed, lap=ident['lap'], paged_lap=ident['paged_lap'], reason=None)
        attempts.append(att)
        if bool(ex['fhs_in_window']) != need_fhs:
            att['reason'] = 'fhs_position'
            continue
        if need_fhs and ex['id_in_window'] < ids_min:               # an exchange meant to be findable has IDs to find
            att['reason'] = 'ids_min'
            continue
        if kind == 'inquiry':
            sc = ex['identities']['scanner']
            s = g.sync_int(sc['lap'])
            if sc['lap'] in laps or any(g.hamming(s, t) < MIN_SYNC_DISTANCE for t in syncs):
                att['reason'] = 'lap_conflict'
                continue
            laps.append(sc['lap'])
            syncs.append(s)
        taken_laps[:] = laps
        taken_syncs[:] = syncs
        ident['conf_seed'] = seed
        ident['base'] = base
        return ident, ex, side, jobs, draws, attempts
    raise ValueError("no exchange with fhs_in_window=%s in %d draws" % (need_fhs, max_draws))


def place_exchange(ex, side, jobs, origin):
    """The exchange's entries and jobs shifted as a whole so that it begins at sample ``origin``: ``(entries, last_sample)``.
    The rendered entries are in the order of ``jobs``."""
    delta = origin - ex['start_sample']
    entries, ji, last = [], 0, 0
    for e in side['bursts']:
        e = dict(e)
        e['start_sample'] += delta
        e['symbol_phase'] = e['start_sample'] % SPS
        if e['rendered']:
            job = dict(jobs[ji])
            ji += 1
            job['start'] += delta
            assert job['start'] == e['start_sample']
            bits = [int(b) for b in job['packet'].bits]
            job['packet'] = g.Bits(np.array(bits, dtype=np.uint8))
            e['_job'], e['_bits'] = job, bits
            last = max(last, e['start_sample'] + len(bits) * SPS + 100)
        entries.append(e)
    assert ji == len(jobs)
    return entries, last


# --- the overlap truth -----------------------------------------------------------------------------

def overlap_pairs(starts, ends, channels):
    """Time overlaps (by any amount) of bursts within one channel of each other, by one sweep over the starts:
    ``over[i]`` the list of ``(j, overlap)`` for burst ``i``."""
    n = len(starts)
    order = sorted(range(n), key=lambda i: (starts[i], i))
    over = [[] for _ in range(n)]
    active = []
    for i in order:
        s = starts[i]
        active = [j for j in active if ends[j] > s]
        for j in active:
            ov = min(ends[i], ends[j]) - s                  # starts[j] <= s
            if ov > 0 and abs(channels[i] - channels[j]) <= 1:
                over[i].append((j, ov))
                over[j].append((i, ov))
        active.append(i)
    return over


def same_channel_fraction(i, over, starts, ends, channels):
    ivs = sorted((max(starts[i], starts[j]), min(ends[i], ends[j])) for j, _ in over[i] if channels[j] == channels[i])
    covered, hi = 0.0, -math.inf
    for a, b in ivs:
        a = max(a, hi)
        if b > a:
            covered += b - a
            hi = b
    return bool(ivs), covered / (ends[i] - starts[i])


class Plan:
    """The header, the bursts (without their overlaps, sorted), the render jobs, and the overlap truth in compact form."""

    def burst(self, i):
        """The complete truth of burst ``i``: its entry with ``index``, ``overlaps``, ``collision``, ``overlap_frac``."""
        e = dict(self.entries[i])
        e['index'] = i
        k = self.rendered_pos[i]
        if k is None:
            e['overlaps'], e['collision'], e['overlap_frac'] = [], False, 0.0
            return e
        rows = []
        for j, ov in sorted(self.over[k]):
            gj = self.rendered_ids[j]
            o = self.entries[gj]
            rows.append(dict(burst=gj, device=o['device'], overlap_samples=ov, channel_offset_mhz=float(o['channel'] - e['channel']),
                             sir_db=round(e['snr_db'] - o['snr_db'], 4)))
        e['overlaps'], e['collision'], e['overlap_frac'] = rows, self.collision[k], self.frac[k]
        return e

    def bursts(self):
        for i in range(len(self.entries)):
            yield self.burst(i)


# --- the plan ----------------------------------------------------------------------------------------

def plan_file(profile='pagemix', seed=None, blocks=None, n_page=None, n_inquiry=None, block_samples=BLOCK_SAMPLES,
              workers=None, name=None, level_range=LEVEL_RANGE):
    """Everything about a file but its samples: a ``Plan``. ``n_page`` and ``n_inquiry`` default to the profile's."""
    prof = PROFILES[profile]
    seed = prof['seed'] if seed is None else seed
    blocks = prof['blocks'] if blocks is None else blocks
    n_page = prof['n_page'] if n_page is None else n_page
    n_inquiry = prof['n_inquiry'] if n_inquiry is None else n_inquiry
    n_join = n_page + n_inquiry
    total = blocks * block_samples
    n_pic = prof['n_piconets']
    if total < 16 * SLOT:
        raise ValueError("%d samples are too few for the traffic and %d exchanges" % (total, n_join))

    # --- the piconets
    rng = np.random.default_rng([seed, 1 << 20])
    taken_laps, taken_syncs = [], [g.sync_int(GIAC)]
    laps = draw_laps(rng, n_pic, taken_laps, taken_syncs)
    offsets = g.draw_grid_offsets(rng, n_pic)
    not_ours = set(int(i) for i in rng.choice(n_pic, size=prof['n_not_ours'], replace=False))
    p_tx = split(rng, prof['p_master_tx'])
    devices = []
    for d in range(n_pic):
        level = round(float(rng.uniform(*level_range)), 1)
        frac = float(rng.uniform(0, 1))
        devices.append(dict(
            id=d, kind='piconet', ours=d not in not_ours, lap=laps[d], uap=int(rng.integers(0, 256)),
            nap=int(rng.integers(0, 1 << 16)), clk0=4 * int(rng.integers(0, (1 << 26) - (1 << 17))),
            grid_offset_int=offsets[d], grid_offset_frac=frac, grid_offset_samples=offsets[d] + frac, level_db=level,
            traffic=traffic_profile(rng, prof, p_tx[d])))
    tasks = [(dev, seed, total) for dev in devices]
    if workers is None:
        workers = min(n_pic, 10) if blocks >= 50 else 1
    if workers > 1:
        with multiprocessing.get_context('fork').Pool(workers) as pool:
            results = pool.map(piconet_bursts, tasks, chunksize=1)
    else:
        results = [piconet_bursts(t) for t in tasks]
    flat = [pair for res in results for pair in res]
    del results

    # --- the joiners: kinds shuffled, one exchange in each n-th of the file, FHS wanted in 16 of 20
    joiners, ex_infos = [], []
    j_entries = []
    if n_join:
        jr = np.random.default_rng([seed, 1 << 21])
        kinds = ['page'] * n_page + ['inquiry'] * n_inquiry
        order = [int(i) for i in jr.permutation(n_join)]
        kinds = [kinds[i] for i in order]
        n_out = max(1, n_join // 5) if n_join >= 2 else 0
        out_idx = set(int(i) for i in jr.choice(n_join, size=n_out, replace=False))
        seg = (total - 6 * SLOT) // n_join
        total_draws = 0
        for j in range(n_join):
            kind = kinds[j]
            need = j not in out_idx
            ident, ex, side, jobs, draws, attempts = draw_exchange(jr, kind, need, taken_laps, taken_syncs, prof['ids_min'])
            total_draws += draws
            # the last burst starts at max(start); the longest packet of the follow-up is a DH5 of under 3000 bits
            length = max(e['start_sample'] for e in side['bursts']) - ex['start_sample'] + 3000 * SPS
            room = seg - length - 2 * SLOT
            if room < 0:
                raise ValueError("a %d sample segment is too short for an exchange of %d samples" % (seg, length))
            origin = 3 * SLOT + j * seg + int(jr.integers(0, room + 1))
            phase = int(jr.integers(0, SPS))
            origin += (phase - origin) % SPS
            entries, last = place_exchange(ex, side, jobs, origin)
            if last > total - SLOT:
                raise ValueError("exchange %d ends at %d, past the file" % (j, last))
            did = n_pic + j
            if kind == 'page':
                dev = dict(id=did, kind='joiner_page', ours=True, lap=ident['lap'], uap=ident['uap'], nap=ident['nap'],
                           cod=ident['cod'], paged=dict(lap=ident['paged_lap'], uap=ident['paged_uap']))
            else:
                sc = ex['identities']['scanner']
                dev = dict(id=did, kind='joiner_inquiry', ours=True, lap=sc['lap'], uap=sc['uap'], nap=sc['nap'], cod=sc['cod'],
                           inquirer=dict(lap=GIAC, uap=None, note='the inquirer sends the GIAC; the device LAP here is the scanner\'s'))
            dev['conf_seed'] = ident['conf_seed']
            dev['exchange'] = j
            joiners.append(dev)
            ex_infos.append(dict(exchange=j, kind=kind, device=did, n_draws=draws, attempts=attempts, base=ident['base'], fhs_wanted_in_window=need, ex=ex,
                                 origin=origin, last=last))
            j_entries.append((dev, entries))
        joiner_selection = dict(
            n_exchanges=n_join, fhs_wanted_in_window=n_join - n_out, fhs_wanted_out_of_window=n_out,
            ids_in_window_min_for_fhs_in_window_exchanges=prof['ids_min'],
            total_draws=total_draws, draws_per_exchange=[x['n_draws'] for x in ex_infos],
            note='the builder\'s seed (CLKE, CLKN, their offset, the master\'s clock) is redrawn until the FHS is where the '
                 'plan wants it; a selection of legal parameters, not a change of any hop kernel. A SECOND criterion, not '
                 'in the task, applies to the exchanges whose FHS is wanted in the window: at least %d of the train\'s IDs '
                 'in the window (before collisions), so that most joiners have the IDs a page event needs; the exchanges '
                 'whose FHS is out of the window are drawn with no ID criterion' % prof['ids_min'])
    else:
        joiner_selection = dict(n_exchanges=0, total_draws=0)

    # --- every burst, sorted
    items = [(e, jb) for e, jb in flat]
    not_rendered = []
    for dev, entries in j_entries:
        for e in entries:
            if e['rendered']:
                entry = g.joiner_entry(e, dev, e['_bits'])
                job = dict(e['_job'])
                job['amp'] = amplitude_for(entry['snr_db'])
                entry['burst_phase'] = job['phase']
                items.append((finish_joiner(entry, dev, e), job))
            else:
                entry = g.joiner_entry(e, dev, None)
                entry['burst_phase'] = None
                items.append((finish_joiner(entry, dev, e), None))
    items.sort(key=lambda t: (t[0]['start_sample'] + t[0]['timing_frac'], t[0]['device'], t[0]['channel']))
    entries = [e for e, _ in items]
    jobs = [jb for _, jb in items if jb is not None]
    rendered_ids = [i for i, (_, jb) in enumerate(items) if jb is not None]
    rendered_pos = [None] * len(entries)
    for k, i in enumerate(rendered_ids):
        rendered_pos[i] = k
    del items, flat
    starts = [entries[i]['start_sample'] + entries[i]['timing_frac'] for i in rendered_ids]
    ends = [s + entries[i]['air_bits_length'] * SPS for s, i in zip(starts, rendered_ids)]
    if ends and max(ends) + 200 > total:
        raise ValueError("a burst ends at %g, past the end of the file at %d" % (max(ends), total))
    channels = [entries[i]['channel'] for i in rendered_ids]
    over = overlap_pairs(starts, ends, channels)
    collision, frac = [], []
    for k in range(len(rendered_ids)):
        c, f = same_channel_fraction(k, over, starts, ends, channels)
        collision.append(c)
        frac.append(f)

    plan = Plan()
    plan.profile, plan.seed, plan.blocks, plan.block_samples, plan.total = profile, seed, blocks, block_samples, total
    plan.entries, plan.jobs = entries, jobs
    plan.rendered_ids, plan.rendered_pos = rendered_ids, rendered_pos
    plan.over, plan.collision, plan.frac = over, collision, frac
    plan.devices, plan.joiners, plan.ex_infos, plan.n_pic = devices, joiners, ex_infos, n_pic
    plan.header = build_header(plan, prof, name, laps, taken_syncs, joiner_selection, n_page, n_inquiry)
    return plan


def finish_joiner(entry, dev, e):
    """A joiner burst in this file's keys: ``kind`` is the packet type, ``role`` the builder's name for it."""
    entry['kind'] = entry['ptype']
    entry['exchange'] = dev['exchange']
    entry['exchange_kind'] = 'page' if dev['kind'] == 'joiner_page' else 'inquiry'
    return entry


def exchange_rows(plan):
    """The exchange table, from the finished bursts (the counts are recomputed from them)."""
    rows = []
    by_ex = {}
    for i, e in enumerate(plan.entries):
        if e['exchange'] is not None:
            by_ex.setdefault(e['exchange'], []).append(i)
    for info, dev in zip(plan.ex_infos, plan.joiners):
        ex = info['ex']
        idx = by_ex[info['exchange']]
        bs = [plan.burst(i) for i in idx]
        ren = [b for b in bs if b['rendered']]
        ids = [b for b in bs if b['role'] in ('id_page', 'id_inquiry')]
        ids_r = [b for b in ids if b['rendered']]
        ids_cf = [b for b in ids_r if not b['collision']]
        fhs = [b for b in bs if b['kind'] == 'FHS']
        fol = [b for b in bs if b['role'] in ('followup_master', 'followup_slave')]
        pic = [b for b in ren if any(plan.entries[o['burst']]['device'] < plan.n_pic for o in b['overlaps'])]
        heard = [b for b in ids if b['tick'] == ex['heard_tick']]
        row = dict(
            exchange=info['exchange'], kind=info['kind'], device=info['device'], conf_seed=dev['conf_seed'],
            identities=dict(lap=dev['lap'], uap=dev['uap'], nap=dev['nap'], cod=dev['cod'], paged=dev.get('paged')),
            selection_attempts=info['attempts'], selection_base=info['base'],
            train=ex['train'], koffset=ex['koffset'], heard_tick=ex['heard_tick'], heard_slot=ex['heard_slot'],
            n_id_slots=ex['n_id_slots'], n_draws=info['n_draws'], fhs_wanted_in_window=info['fhs_wanted_in_window'],
            start_sample=info['origin'], symbol_phase_start=info['origin'] % SPS, snr_base_db=ex['snr_base_db'],
            n_bursts_total=len(bs), n_bursts_rendered=len(ren), n_id=len(ids), n_id_rendered=len(ids_r),
            n_id_rendered_collision_free=len(ids_cf), heard_id_rendered=bool(heard and heard[0]['rendered']),
            fhs_rendered=any(b['rendered'] for b in fhs), fhs_collided=any(b['rendered'] and b['collision'] for b in fhs),
            n_followup_rendered=sum(b['rendered'] for b in fol),
            first_id_start_sample=min(b['start_sample'] for b in ids),
            first_rendered_id_start_sample=min((b['start_sample'] for b in ids_r), default=None),
            last_burst_start_sample=max(b['start_sample'] for b in bs),
            last_burst_end_sample=max(b['end_sample'] for b in ren),
            n_rendered_collided=sum(b['collision'] for b in ren),
            overlap_with_piconet_bursts=len(pic),
            n_rendered_collided_with_piconet=sum(1 for b in ren if b['collision'] and any(
                o['channel_offset_mhz'] == 0 and plan.entries[o['burst']]['device'] < plan.n_pic for o in b['overlaps'])),
            expected_page_event_possible=len(ids_cf) >= 4,
            clock_unique=None, clock_solutions=None,
            first_burst_index=idx[0], last_burst_index=idx[-1])
        if info['kind'] == 'page':
            row['master_clkn0'] = ex['master_clkn0']
        rows.append(row)
    return rows


def computed_notes(plan, prof, ex_rows, all_laps, n_pic):
    """Notes whose numbers come from the plan: the LAP breakdown, the device-id warning, the determinism recipe and, for pagemix,
    what expected_page_event_possible is and is not."""
    n_masters = sum(r['kind'] == 'joiner_page' for r in plan.joiners)
    n_paged = sum(bool(r.get('paged')) for r in plan.joiners)
    n_scan = sum(r['kind'] == 'joiner_inquiry' for r in plan.joiners)
    out = [
        "LAPs on the air: %d, all different = %d piconet masters (devices 0-%d) + %d page-joiner masters + %d paged devices + %d "
        "inquiry-joiner scanners (an inquiry exchange's IDs carry the GIAC 0x9E8B33, which is not counted and is not a device "
        "LAP) = %d device LAPs in devices[] (the joiner rows' 'lap') plus the %d paged LAPs in devices[].paged. Every sync word "
        "is at least 22 bits from every other, the GIAC's included." % (
            len(all_laps), n_pic, n_pic - 1, n_masters, n_paged, n_scan, len(plan.devices) + len(plan.joiners), n_paged),
        "THE DEVICE IDS ARE NOT COMPARABLE ACROSS FILES: piconet device k (0-%d) has a different LAP, UAP, clock, level and hop "
        "sequence in pagemix, in connonly and in mixed/ (each file draws its own from its own seed). A grader must read "
        "devices[] of the file under test and never carry a LAP or an 'ours' flag from one file to another." % (n_pic - 1),
        "Determinism: the same arguments give the same bytes. The container is gzip with compresslevel=6, mtime=0 and no file "
        "name; the lines are json.dumps(burst, separators=(',', ':')) + newline. REGZIP: " + REGZIP,
    ]
    if not prof['negative_set']:
        ids_ok = [r['exchange'] for r in ex_rows if r['expected_page_event_possible']]
        fhs_in = [r['exchange'] for r in ex_rows if r['fhs_rendered']]
        cant = [r['exchange'] for r in ex_rows if r['fhs_rendered'] and not r['expected_page_event_possible']]
        fhs_out_ok = [r['exchange'] for r in ex_rows if not r['fhs_rendered'] and r['expected_page_event_possible']]
        out.append(
            "The 4-collision-free-ID rule (expected_page_event_possible) is separate from, and in addition to, the FHS-in-window "
            "rule; it is also the second selection criterion's consequence (at least 4 rendered train IDs before collisions for "
            "the exchanges whose FHS is wanted in the window). expected_page_event_possible is NOT 'the FHS is in the window': "
            "%d exchanges have it true (%s) and %d have the FHS rendered (%s)%s. Exchanges with the FHS rendered in the window but "
            "fewer than 4 collision-free IDs (a receiver needing four IDs cannot page them): %s. Exchanges with the FHS out of the "
            "window but 4 or more collision-free IDs (it can page, without an FHS): %s." % (
                len(ids_ok), ids_ok, len(fhs_in), fhs_in,
                ' - the same number only by coincidence' if len(ids_ok) == len(fhs_in) else ' - different numbers',
                cant or 'none', fhs_out_ok or 'none'))
    return out


def build_header(plan, prof, name, laps, taken_syncs, joiner_selection, n_page, n_inquiry):
    entries = plan.entries
    n = len(entries)
    n_pic = prof['n_piconets']
    n_join = n_page + n_inquiry
    sync_words = {}
    for dev in plan.devices:
        sync_words[dev['lap']] = g.sync_int(dev['lap'])
    for dev in plan.joiners:
        sync_words[dev['lap']] = g.sync_int(dev['lap'])
        if dev.get('paged'):
            sync_words[dev['paged']['lap']] = g.sync_int(dev['paged']['lap'])
    giac_sync = g.sync_int(GIAC)
    rows = []
    for dev in plan.devices:
        others = [t for lap, t in sync_words.items() if lap != dev['lap']] + [giac_sync]
        rows.append(dict(
            id=dev['id'], kind='piconet', ours=dev['ours'], lap=dev['lap'], uap=dev['uap'], nap=dev['nap'], clk0=dev['clk0'],
            slot_grid_offset_samples=dev['grid_offset_samples'], slot_grid_offset_int=dev['grid_offset_int'],
            slot_grid_offset_frac=dev['grid_offset_frac'], level_db=dev['level_db'], jitter_db=JITTER_DB,
            hop_map=dict(kind='adapted', channels=MAP, used_channels_mask_hex='%x' % MASK,
                         address_for_hop=dev['uap'] << 24 | dev['lap'], instant_clk27_1=dev['clk0'] >> 1),
            traffic=dev['traffic'], sync_word_hex='%016x' % sync_words[dev['lap']],
            sync_word_min_distance=min(g.hamming(sync_words[dev['lap']], t) for t in others)))
    ex_rows = exchange_rows(plan) if n_join else []
    for dev, ex in zip(plan.joiners, ex_rows):
        others = [t for lap, t in sync_words.items() if lap != dev['lap']] + [giac_sync]
        row = dict(
            id=dev['id'], kind=dev['kind'], ours=True, lap=dev['lap'], uap=dev['uap'], nap=dev['nap'], cod=dev['cod'],
            exchange=ex['exchange'], sync_word_hex='%016x' % sync_words[dev['lap']],
            sync_word_min_distance=min(g.hamming(sync_words[dev['lap']], t) for t in others),
            hop_map=dict(kind='specification', channels=None, used_channels_mask_hex=None,
                         note='page, page response and Central page response sequences (page) or inquiry and inquiry '
                              'response sequences (inquiry), then the BASIC connection sequence for a page exchange\'s '
                              'follow-up: not the 31-50 map of the piconets'))
        if dev.get('paged'):
            row['paged'] = dict(dev['paged'], sync_word_hex='%016x' % sync_words[dev['paged']['lap']])
        else:
            row['inquirer'] = dev['inquirer']
        rows.append(row)
    all_laps = [r['lap'] for r in rows] + [r['paged']['lap'] for r in rows if r.get('paged')]
    syncs = [sync_words[x] for x in all_laps] + [giac_sync]
    min_all = min(g.hamming(a, b) for i, a in enumerate(syncs) for b in syncs[i + 1:])
    pic_s = [sync_words[d['lap']] for d in plan.devices]
    min_pic = min(g.hamming(a, b) for i, a in enumerate(pic_s) for b in pic_s[i + 1:])
    per_dev, per_type = {}, {}
    n_coll = 0
    for i, e in enumerate(entries):
        per_dev.setdefault(e['device'], {})
        per_dev[e['device']][e['kind']] = per_dev[e['device']].get(e['kind'], 0) + 1
        per_type[e['kind']] = per_type.get(e['kind'], 0) + 1
    n_rendered = len(plan.rendered_ids)
    n_coll = sum(plan.collision)
    n_same = sum(1 for k, o in enumerate(plan.over) for j, _ in o if plan.entries[plan.rendered_ids[j]]['channel'] ==
                 plan.entries[plan.rendered_ids[k]]['channel']) // 2
    n_pairs = sum(len(o) for o in plan.over) // 2
    n_adj_only = sum(1 for k, o in enumerate(plan.over) if o and not any(
        plan.entries[plan.rendered_ids[j]]['channel'] == plan.entries[plan.rendered_ids[k]]['channel'] for j, _ in o))
    pic_idx = [i for i, e in enumerate(entries) if e['device'] < plan.n_pic]
    n_pic_b = len(pic_idx)
    n_pic_coll = sum(plan.collision[plan.rendered_pos[i]] for i in pic_idx)
    keys = [set(), set(), set()]
    for e in entries:
        k = 0 if e['device'] < plan.n_pic else (2 if e['kind'] == 'FHS' else 1)
        keys[k].update(e)
    keys[1].update(('overlaps', 'collision', 'overlap_frac', 'index'))
    keys[0].update(('overlaps', 'collision', 'overlap_frac', 'index'))
    keys[2].update(('overlaps', 'collision', 'overlap_frac', 'index'))
    by_kind = dict(piconet=sorted(keys[0]), joiner=sorted(keys[1]), fhs_extra=sorted(keys[2] - keys[1]))
    per_burst_keys = sorted(keys[0] | keys[1] | keys[2])
    nr = plan.rendered_pos
    header = {
        'generator': 'SDR scripts/bt_synth_pagemix.py',
        'generator_commit': bt_synth.commit(),
        'name': name, 'profile': plan.profile, 'profile_text': prof['text'],
        'negative_set': prof['negative_set'], 'joiners': n_join,
        'lap': None, 'uap': None, 'nap': None, 'clk': None, 'ptype': None, 'kind': None, 'channel': None, 'channel_mhz': None,
        'bt_channel': None, 'snr_db': None, 'timing_frac': None, 'symbol_phase': None,
        'clk_convention': 'native CLK[27:0] of the sender at the first sample of the burst it is given for '
                          '(each piconet has its own clock: devices[].clk0 at its slot 0); a slave burst\'s clk is the clock '
                          'of its own slot and its hop_clk the clock of the master slot that its channel is computed from; a '
                          'joiner burst\'s clk and hop_clk are the builder\'s (hop_clk_kind CLKE, CLKN or CLK)',
        'hopping': True, 'hop_channels': sorted({e['channel'] for e in entries}),
        'sample_rate': FS, 'center_mhz': CENTER_MHZ, 'cfo_hz': 0.0,
        'n_samples': int(plan.total),
        'n_samples_meaning': 'the number of complex samples in the file; its size is 8 * n_samples bytes',
        'n_noise_blocks': plan.blocks, 'noise_block_samples': plan.block_samples,
        'snr_bw_hz': 1e6, 'noise_1mhz': NOISE_1MHZ,
        'samples_per_symbol': SPS,
        'modulation': {'scheme': 'GFSK', 'bt': br.GFSK_BT, 'h': br.GFSK_H, 'symbol_rate': br.SYMBOL_RATE},
        'slot_samples': SLOT, 'start_offset': 0, 'grid_base_samples': GRID_BASE,
        'symbol_phases': [0, SPS // 2], 'joiner_symbol_phases': [r['symbol_phase_start'] for r in ex_rows],
        'symbol_phase_meaning': SYMBOL_PHASE_TEXT,
        'start_sample_meaning': 'the first sample of the access code\'s preamble, before timing_frac is added',
        'timing_frac_meaning': 'null at the top: every burst has its own, uniform in [0, 1)',
        'air_bits_omitted': True, 'air_bits_omitted_for': 'the piconet bursts (devices 0-%d); a rendered joiner burst keeps air_bits' % (n_pic - 1),
        'burst_window': WINDOW_TEXT,
        'window': dict(channels=list(WINDOW), mhz=list(conf.WINDOW_MHZ),
                       why=g.WINDOW_GATE_NOTE.replace('Bursts outside it are in bursts_not_rendered.',
                                                      'Bursts outside it are lines of the bursts file with rendered false.'),
                       text='a joiner burst is rendered iff its channel is %d to %d (the piconets are all on 31-50, inside)' % WINDOW),
        'conformance': CONFORMANCE_NEGATIVE if prof['negative_set'] else conformance_positive(n_join, sum(1 for r in ex_rows if r['kind'] == 'page' and r['train'] == 'B'), sum(1 for r in ex_rows if r['kind'] == 'page')),
        'notes': NOTES + ([NO_ID_NOTE] if prof['negative_set'] else []) + computed_notes(plan, prof, ex_rows, all_laps, n_pic),
        'seed': plan.seed,
        'devices': rows, 'n_devices': len(rows), 'n_piconets': n_pic, 'n_joiners': n_join,
        'n_page_joiners': n_page, 'n_inquiry_joiners': n_inquiry,
        'ours_devices': [r['id'] for r in rows if r['ours']], 'not_ours_devices': [r['id'] for r in rows if not r['ours']],
        'level_range_db': list(LEVEL_RANGE), 'jitter_db': JITTER_DB, 'joiner_snr_range_db': list(JOINER_SNR_RANGE),
        'joiner_snr_jitter': 'N(0, 1) dB clipped to +-3 dB per burst around exchanges[].snr_base_db',
        'joiner_selection': joiner_selection,
        'exchanges': ex_rows,
        'sync_word_min_distance_piconets': min_pic, 'sync_word_min_distance_all': min_all,
        'sync_word_distance_required': MIN_SYNC_DISTANCE,
        'sync_word_note': 'Hamming distance between the 64-bit sync words (br.sync_word) of the %d piconet LAPs and of all %d LAPs '
                          'on the air plus the GIAC (the piconets\', each page joiner\'s master and paged device, each '
                          'inquiry joiner\'s scanner); %d is the least any two are allowed' % (n_pic, len(all_laps), MIN_SYNC_DISTANCE),
        'afh_map': MAP, 'afh_map_note': 'the piconets only; the joiners are on the specification\'s sequences',
        'hop_kernel': hop.HOP_KERNEL,
        'multi_slot_channel_note': 'a multi-slot packet is on the channel of its first slot for its whole length; the slave\'s '
                                   'answer is on the same channel (same channel mechanism, Figure 2.15)',
        'n_bursts': n, 'n_bursts_rendered': n_rendered, 'n_bursts_not_rendered': n - n_rendered,
        'n_piconet_bursts': n_pic_b, 'n_joiner_bursts': n - n_pic_b,
        'counts_per_device': {str(d): sum(v.values()) for d, v in sorted(per_dev.items())},
        'counts_per_device_and_type': {str(d): v for d, v in sorted(per_dev.items())},
        'counts_per_type': dict(sorted(per_type.items())),
        'n_collisions': n_coll, 'fraction_collided': n_coll / n_rendered,
        'n_piconet_collisions': n_pic_coll, 'fraction_piconet_collided': n_pic_coll / n_pic_b,
        'n_overlap_pairs': n_pairs, 'n_same_channel_overlap_pairs': n_same,
        'n_bursts_overlapped_adjacent_channel_only': n_adj_only,
        'collision_note': 'a burst is collided iff another burst of any device overlaps it in time on the same channel; '
                          'fraction_collided is over the rendered bursts; n_collisions counts bursts (not pairs); '
                          'n_same_channel_overlap_pairs counts pairs',
        'per_burst_keys': per_burst_keys, 'per_burst_keys_by_kind': by_kind,
        'per_burst_keys_note': 'per_burst_keys is the UNION of the keys of all bursts; per_burst_keys_by_kind lists what a '
                               'piconet burst, a joiner burst and (in addition) a joiner FHS burst has. A burst that is not '
                               'rendered has the joiner keys with air_bits, air_bits_length, end_sample and the body keys null.',
        'indexing': 'the bursts file is sorted by start_sample + timing_frac (ties by device, channel); the line number of a '
                    'burst is its index; overlaps[].burst is an index; read it with gzip.open(path, "rt") and json.loads per line',
        'bursts_file': None, 'bursts_format': 'jsonl.gz', 'bursts_head': None,
    }
    return header


# --- the samples -----------------------------------------------------------------------------------

_STATE = {}


def _render_index(index):
    """Block ``index`` of the file: its noise, then every burst that reaches it in the order of the jobs (the order
    ``bt_synth_mixed.render_blocks`` adds them in, so the float sums are the same to the bit)."""
    plan = _STATE['plan']
    b0 = index * plan.block_samples
    b1 = min(plan.total, b0 + plan.block_samples)
    out = noise_block(plan.seed, index, b1 - b0, FS) if _STATE['noise'] else np.zeros(b1 - b0, dtype=np.complex64)
    lo, hi = _STATE['spans']
    for i in np.flatnonzero((lo < b1) & (hi > b0)):
        start, samples = g.pairs.render_burst(plan.jobs[i], FS, CENTER_MHZ)
        a, b = max(start, b0), min(start + len(samples), b1)
        if b > a:
            out[a - b0:b - b0] += samples[a - start:b - start]
    return out


def render_blocks(plan, noise=True, workers=1):
    """The file in blocks of ``plan.block_samples`` samples, each from its noise and the bursts that reach it. With
    ``workers`` above 1 the blocks are made by forked processes (a burst crossing a boundary is rendered by both blocks;
    its samples are the same). Same values as ``bt_synth_mixed.render_blocks`` on the same jobs."""
    spans = [g.pairs.burst_span(j, FS) for j in plan.jobs]
    _STATE.update(plan=plan, noise=noise, spans=(np.array([s[0] for s in spans]), np.array([s[1] for s in spans])))
    n_blocks = -(-plan.total // plan.block_samples)
    if workers > 1:
        with multiprocessing.get_context('fork').Pool(workers) as pool:
            yield from pool.imap(_render_index, range(n_blocks))
    else:
        for index in range(n_blocks):
            yield _render_index(index)


def write_truth(plan, out_dir, name):
    """Write ``synth_<name>.bursts.jsonl.gz`` (one burst per line, streamed) and ``synth_<name>.json`` (the header with the
    first 200 bursts): ``(side_path, bursts_path)``. The same plan gives the same bytes."""
    os.makedirs(out_dir, exist_ok=True)
    side_path = os.path.join(out_dir, 'synth_%s.json' % name)
    bursts_path = os.path.join(out_dir, 'synth_%s.bursts.jsonl.gz' % name)
    raw, gz = open_gzip_lines(bursts_path)
    head = []
    try:
        for i, b in enumerate(plan.bursts()):
            if i < 200:
                head.append(b)
            gz.write((json.dumps(b, separators=(',', ':')) + '\n').encode())
    finally:
        gz.close()
        raw.close()
    header = dict(plan.header, name=name, bursts_file=os.path.basename(bursts_path), bursts_head=head)
    with open(side_path, 'w') as f:
        json.dump(header, f, separators=(',', ':'))
    return side_path, bursts_path


def write_file(name, out_dir, profile, seed=None, blocks=None, n_page=None, n_inquiry=None, workers=None):
    """Make one file: ``synth_<name>.cf32``, ``.json`` and ``.bursts.jsonl.gz``."""
    t0 = time.time()
    plan = plan_file(profile, seed, blocks, n_page, n_inquiry, workers=workers, name=name)
    t_plan = time.time() - t0
    os.makedirs(out_dir, exist_ok=True)
    iq_path = os.path.join(out_dir, 'synth_%s.cf32' % name)
    print('%s: planned in %.0f s: %d bursts (%d rendered), %.1f %% collided' % (
        name, t_plan, plan.header['n_bursts'], plan.header['n_bursts_rendered'], 100 * plan.header['fraction_collided']), flush=True)
    t1 = time.time()
    side_path, bursts_path = write_truth(plan, out_dir, name)
    print('%s: truth written in %.0f s' % (name, time.time() - t1), flush=True)
    plan.entries = plan.over = plan.collision = plan.frac = plan.rendered_pos = plan.rendered_ids = None   # free before the fork
    t2 = time.time()
    with open(iq_path, 'wb') as f:
        for k, out in enumerate(render_blocks(plan, workers=RENDER_WORKERS)):
            out.astype('<c8', copy=False).tofile(f)
            if k % 25 == 0:
                print('%s: block %d of %d, %.0f s' % (name, k, plan.blocks, time.time() - t2), flush=True)
    print('%s: %s  %d samples, %.2f s, rendered in %.0f s, total %.0f s' % (
        name, iq_path, plan.total, plan.total / FS, time.time() - t2, time.time() - t0), flush=True)
    print(side_path)
    print(bursts_path)
    return iq_path, side_path, bursts_path


def set_files(which):
    prof = PROFILES[which]
    return [(which, dict(profile=which, seed=prof['seed'], blocks=prof['blocks']))]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('name', nargs='?', help='the file is synth_<name>.cf32; not used with --set')
    ap.add_argument('--set', choices=sorted(PROFILES), help='write one of the two files with its own seed and size; only --out')
    ap.add_argument('--profile', choices=sorted(PROFILES), default='pagemix')
    ap.add_argument('--seed', type=int)
    ap.add_argument('--blocks', type=int, help='noise blocks of 4194304 samples')
    ap.add_argument('--joiners', type=int, help='number of joiner exchanges (about 3/5 page, 2/5 inquiry)')
    ap.add_argument('--out', default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    try:
        if args.set:
            if args.name or args.seed is not None or args.blocks is not None or args.joiners is not None:
                ap.error('--set takes everything from its table: no name and no option but --out')
            for name, spec in set_files(args.set):
                write_file(name, args.out, **spec)
        else:
            if args.name is None:
                ap.error('give the name of the file, or --set')
            prof = PROFILES[args.profile]
            n_page = n_inquiry = None
            if args.joiners is not None:
                n_page = (args.joiners * 3 + 2) // 5 if prof['n_page'] else 0
                n_inquiry = args.joiners - n_page if prof['n_page'] else 0
            write_file(args.name, args.out, args.profile, args.seed, args.blocks, n_page, n_inquiry)
    except ValueError as e:
        ap.error(str(e))


if __name__ == '__main__':
    main()
