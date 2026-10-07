#!/usr/bin/env python3
"""Eight piconets and three joiners on the air at once, with every collision in the truth.

    python scripts/bt_synth_mixed.py mixed_blind_a --seed 9201 --blocks 60 --out DIR
    python scripts/bt_synth_mixed.py --set mixed [--out DIR]

    from scripts import bt_synth_mixed
    iq, sidecar = bt_synth_mixed.synthesise_mixed(seed=9201, blocks=3)

For bluey-ox-walker's blind operation: finding devices whose LAP and UAP it does not know, in a
band shared by several piconets, and not claiming the devices it is not meant to follow. Its
earlier files had one known piconet (or one LAP) at a time. Nothing is transmitted. The plan is in
[knowledge/bluey-test-signals.md](../knowledge/bluey-test-signals.md).

**The file.** 40 MS/s on 2441.0 MHz, the one constant noise floor of every file (``NOISE_1MHZ``,
block ``i`` of the noise from ``default_rng([seed, 1, i])``, as ``bt_synth_acl.py``), a whole number of
4194304-sample noise blocks (60: 251,658,240 samples, 6.29 s, 2 GB; the small runs of the tests are 3).

**Eight piconets** (devices 0 to 7), each a master and its one slave (LT_ADDR 1):

* an identity drawn from the seed: LAP (outside the reserved range and the GIAC, all different, and
  chosen so that no two **sync words** are nearer than ``MIN_SYNC_DISTANCE`` bits; the sidecar says
  the distance achieved), UAP, NAP;
* **6 are ours and 2 are not** (``ours``): the receiver's author tells his receiver which LAPs it
  follows and checks that it never claims the other two. The two are drawn from the seed;
* a master clock ``clk0`` drawn at random per piconet, so the hop sequences are unsynchronised;
* a **slot grid** offset ``slot_grid_offset_samples``, an integer 0 to 24999 plus a fraction, drawn per
  piconet and at least ``MIN_GRID_SEPARATION`` samples from every other's (circularly, mod a slot): the
  piconets are not slot aligned. Piconet slot ``s`` begins at ``GRID_BASE + offset + 25000 * s`` samples;
* a **level**, the SNR in 1 MHz, uniform 8 to 22 dB (0.1 dB) per device and, per burst, a jitter uniform in
  +-1 dB: ``snr_db`` of a burst is the level plus its jitter, over the noise floor of the file;
* a **hop map**: all eight hop on the **adapted 20-channel map 31-50** with their own address,
  ``bt_hop.hop_channel(clk, uap << 24 | lap, mask)``, as the other generators. The sequences differ
  because the addresses (and the clocks) differ; they do not differ because the maps do;
* a **traffic profile**: the probability ``p_master_tx`` (0.15 to 0.45) that the master transmits in
  one of its master slots (even slots, CLK1-0 = 00), the odds of its packet types (NULL, POLL, DM1, DH1,
  DM3, DH3, DM5, DH5, drawn per device) and of its slave's answer (NULL, DM1, DH1, or nothing).

**Slots.** A packet of ``n`` slots (1, 3, 5) starts in a master slot ``s`` and occupies slots ``s`` to
``s + n - 1``; the master does not transmit again before slot ``s + n + 1``. The slave answers in slot
``s + n`` (a single slot packet: NULL, DM1 or DH1) with the odds of its profile, and sometimes not at
all. **The slave's channel is the master's**: the same channel mechanism of the adapted sequence
(Core Vol 2 Part B 2.6.3 and Figure 2.15; ``apps/bt_hop.py`` note 4), so a slave burst's ``hop_clk`` is
the clock of the master packet that it answers, its channel ``hop_channel(hop_clk)``, and its ``clk``
(for the header's whitening) the clock of its own slot, ``hop_clk + 2 * n``. A piconet's own bursts
therefore never overlap in time.

**The symbol phases.** The task says the piconets are not slot aligned (a random 0 to 24999 samples
plus a fraction) and the repository's rule says a burst starts at symbol phase 0 or ``sps / 2``,
alternating. Both hold: a master packet and its answer (a *pair*) are shifted as a whole to the next
sample that is 0 (even pair) or 20 (odd pair) modulo 40, as ``bt_synth_pairs.py`` does, so a burst's
``start_sample`` is up to 39 samples after its slot's boundary, and ``timing_frac`` is uniform in
[0, 1) per burst. ``slot_grid_offset_samples`` is where the slot boundaries are; a burst begins at
``GRID_BASE + offset + 25000 * piconet_slot`` plus between -1 and +41 samples (``start_sample +
timing_frac`` less that). The piconets' relative alignment is not hidden by it: the offsets are at
least 800 samples apart.

**Three joiners** (devices 8, 9, 10, ``kind: joiner``, ``ours: true``): a new pair whose page
exchange (the paged device's ID packets, its response, the FHS, the acknowledgement, and 12 master
packets with the slave's answers) is built by ``bt_synth_conf.plan_conf`` itself, with its own plan and
renderer, unedited. The joiner's identities are made the way a module's globals allow: the two
dictionaries ``bt_synth_conf.PAGED`` and ``MASTER`` are replaced for the call (``mock.patch.object``)
with the joiner's own, so the three exchanges are three different pairs and not the one pair of that file;
and ``clock_solutions`` (a 2**27-entry search that is that file's own business and is not used here) is
stubbed for the call. **The joiners are on the specification's sequences, not the 31-50 map**: the
IDs, the response and the FHS on the page, page response and Central page response sequences
(``apps/bt_hop_substates.py``), the follow-up on the *basic* connection sequence with the master's
own clock, the slave's answer one slot later on that kernel at the slave's clock (a different channel
from the master's: the same channel mechanism belongs to the adapted sequence only). The bursts whose
channel is not 27 to 51 (``in_window`` false) are not in the samples: 27 to 51 is a conservative software gate (a 40 MS/s
complex capture on 2441.0 MHz nominally covers 2421 to 2461 MHz and channels 26 and 52 would fit in the BB60D's +-13.5 MHz,
but are deliberately not rendered, as a guard); they are in ``bursts_not_rendered``
with their truth and no ``air_bits``, and in no overlap. Each joiner has its own LAPs, the master's and the
paged device's (14 LAPs in all, distinct, the sync-word distance over all 14 in the sidecar). The
exchanges are placed at random times in three separate thirds of the file, with their own symbol phases
(0 or 20 at the start of the exchange; a tick is 12500 samples, so the odd ticks sit at the other).

**Collisions are in the truth.** The bursts of different devices overlap in time and often in
frequency; the signals add linearly (the same noise floor once), and a receiver's failure to decode an
overlapped burst is not a defect. A **burst's window** is the packet's air time, from its first preamble
sample to the end of its last bit, and nothing else: the real interval ``[start_sample + timing_frac,
start_sample + timing_frac + air_bits_length * 40)`` in samples (the modulator's own 2 us ramps are not
in it). Per burst ``overlaps`` lists, for every other burst that overlaps it in time by any amount
(even partly) and whose channel is within 1 (``channel_offset_mhz`` = its channel less this burst's:
-1, 0 or +1), ``{burst, device, overlap_samples, channel_offset_mhz, sir_db}``: ``burst`` is the other
burst's index in ``bursts``, ``overlap_samples`` the length of the intersection of the two windows (a
real number), ``sir_db`` this burst's ``snr_db`` less the other's (the two constant envelopes' power
ratio, jitter included). ``collision`` is true iff an overlap with offset 0 exists, ``overlap_frac`` the
fraction of this burst's window covered by the union of the same-channel overlaps. An adjacent-channel
overlap is listed and is not a collision.

**Truth per burst**, beside the keys of ``bt_synth_acl.py``: ``device``, ``role``, ``ours``,
``piconet_slot``, ``overlaps``, ``collision``, ``overlap_frac``, ``in_window``. Unlike the usual
rule these files **carry ``air_bits``** (as hex, the first bit on air the top bit of the first byte; with
``body_crc_bits_hex``), because the task asks for the keys of ``bt_synth_acl.py``: ``air_bits_omitted``
is false.

**Memory.** The file is made block by block: noise block ``i``, then the bursts whose span reaches
it, each rendered once and kept only while it is needed; the 2 GB file is written as it goes.
"""
import argparse
import json
import math
import os
import sys
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from apps import bt_hop, bt_lmp  # noqa: E402
from scripts import bt_synth, bt_synth_acl as acl, bt_synth_conf as conf, bt_synth_hop as hop  # noqa: E402
from scripts import bt_synth_pairs as pairs  # noqa: E402
from scripts.bt_synth_interf import NOISE_1MHZ, amplitude_for, noise_block  # noqa: E402

FS = 40e6
CENTER_MHZ = 2441.0
SPS = 40
SLOT = 25000                                   # samples in a 625 us slot at 40 MS/s
BLOCK_SAMPLES = 2 ** 22
BLOCKS = 60
MAP = list(range(31, 51))                      # the adapted 20-channel map, 2433 to 2452 MHz
MASK = sum(1 << c for c in MAP)
N_PICONETS = 8
N_NOT_OURS = 2
N_JOINERS = 3
FOLLOWUP = 12
GRID_BASE = 2 * SLOT                           # where piconet slot 0 begins, before its offset
LEVEL_RANGE = (8.0, 22.0)                      # dB over the noise in 1 MHz, per device
JITTER_DB = 1.0                                # per burst, uniform +-
P_MASTER_TX = (0.15, 0.45)
P_SLAVE_ANSWER = (0.80, 0.95)
MASTER_TYPES = ('NULL', 'POLL', 'DM1', 'DH1', 'DM3', 'DH3', 'DM5', 'DH5')
MASTER_ALPHA = (3.0, 2.0, 3.0, 3.0, 2.0, 2.0, 1.5, 1.5)
SLAVE_TYPES = ('NULL', 'DM1', 'DH1')
SLAVE_ALPHA = (4.0, 1.5, 2.0)
DM1_LMP_FRACTION = 0.5
L2CAP_CID = 0x0040
L2CAP_MIN = 8                                  # shortest body of a data packet (the 4-byte L2CAP header and 4)
MIN_SYNC_DISTANCE = 22                         # bits, between any two of the 14 sync words
MIN_GRID_SEPARATION = 800                      # samples, between two piconets' slot grids, circular
LT_ADDR = 1
LMP_NAMES = tuple(bt_lmp.PDUS)
RESERVED_LAPS = conf.RESERVED_LAPS
GIAC = conf.GIAC
WINDOW = conf.WINDOW                           # channels 27 to 51: what the capture holds

DEFAULT_OUT = '/media/user/4TB/sdr-synth-tmp'
SEEDS = {'mixed_blind_a': 9201, 'mixed_blind_b': 9202}

WINDOW_GATE_NOTE = (
    "A conservative software gate: a joiner burst is rendered iff its channel is 27 to 51 (2429 to 2453 MHz, +-12 MHz of the "
    "centre), by rule. A complex 40 MS/s capture centred on 2441 MHz nominally covers 2421 to 2461 MHz, and channels 26 and 52 "
    "(at +-13 MHz) also fit inside the BB60D's +-13.5 MHz, but are deliberately not rendered: the gate keeps a guard band so "
    "that every rendered burst's +-1 MHz lies inside +-13 MHz. It is not a statement that those channels cannot be captured. "
    "Bursts outside it are in bursts_not_rendered.")

WINDOW_TEXT = (
    "A burst's window is the packet's air time, from the first preamble sample to the end of its last bit and "
    "nothing else: the real interval [start_sample + timing_frac, start_sample + timing_frac + air_bits_length * 40) "
    "in samples at 40 MS/s (the modulator's 2 us power ramps before and after are not in it).")

CONFORMANCE = (
    "The eight piconets' traffic is on the ADAPTED map 31-50 (the 20 channels 2433 to 2452 MHz) with "
    "bt_hop.hop_channel(clk, uap << 24 | lap, mask) at each piconet's own address and clock; their sequences differ "
    "because the addresses and clocks differ. The slave answers on its master's channel (the same channel mechanism "
    "of the adapted sequence, Core Vol 2 Part B 2.6.3, Figure 2.15; apps/bt_hop.py note 4), one slot after the "
    "master's packet (after its last slot, for a 3- or 5-slot packet). The three joiners' page exchanges are the "
    "specification's own page, page response and basic connection sequences (bt_synth_conf.plan_conf, "
    "apps/bt_hop_substates.py, apps/bt_hop.py with no map): this is different from the 31-50 map of the "
    "others and is intended. The piconets are unsynchronised: random master clocks, random slot grids. "
    "Conformant: the packet bits, headers, HEC, CRC, FEC, whitening, the hop kernels and the slot rules. "
    "Not modelled: real traffic patterns, power control, retransmission, flow control (SEQN and ARQN are "
    "plausible, not a protocol run), the slave's answer depending on what the master sent, a second slave, "
    "SCO or eSCO, sniff, any drift between the piconets' clocks, and the clock lock a receiver can make on the "
    "joiners' packets (bt_synth_conf's clock_solutions, a 2**27 search, is not run).")

NOTES = [
    "Nothing in this file is real traffic: the piconets send random packet types at a random rate; the LMP "
    "PDUs are those of apps/bt_lmp.py in about half of the DM1 of the master AND of the slave, with random parameters; "
    "a slave sends only PDUs its Table 5.1 row allows from the Peripheral (every one of the eleven but LMP_set_AFH, which is "
    "Central to Peripheral only) and the transaction ID follows who started the transaction (Part C): the master's requests "
    "and the slave's answers carry TID 0, the slave's requests and the master's answers TID 1 (the acl files' rule: PDUs the "
    "master starts carry TID 0, its answers 1). The slave's PDUs are not an answer to the master's: no transaction is "
    "followed. The data packets "
    "carry an L2CAP-like header and random bytes. The SEQN of a packet is the count of the device's packets of that role "
    "and ARQN and FLOW are 1: plausible, not a protocol run. There is no retransmission, no power control and "
    "no flow control.",
    "The slave of each piconet uses the piconet's level (snr_db has its own jitter per burst).",
    "Every burst is a constant-envelope GFSK burst of amplitude sqrt(noise_1mhz * 10 ** (snr_db / 10)); the "
    "file minus the noise is the exact sum of the bursts, each placed at its own absolute sample numbers with one "
    "oscillator per burst, so that a collision is a linear sum (superposition) and no burst is changed by another.",
    "Joiner exchanges use bt_synth_conf's own SNR rule (a base per exchange uniform 10 to 20 dB, plus a per-burst "
    "jitter N(0, 1) clipped to +-3 dB); the piconets use a level per device (8 to 22 dB) and a jitter uniform in +-1 dB.",
    "A joiner's bursts outside channels 27 to 51 are listed in bursts_not_rendered with their truth but without "
    "air_bits (the builder does not keep their packets), and are in no overlap.",
    "SIR of a collision: sir_db is this burst's snr_db less the other's: positive when this burst is the stronger. It is the "
    "ratio of the two bursts' total envelope powers. For an adjacent-channel overlap (channel_offset_mhz +-1) the interference in the "
    "victim's own 1 MHz band is about 25 to 32 dB lower than sir_db suggests (measured by a reviewer), so use channel_offset_mhz "
    "together with sir_db.",
    "air_bits is hex padded with zero bits to a whole byte: use air_bits_length for the number of bits on air.",
    "This sidecar is an ANSWER KEY: devices[].lap, uap, nap, clk0, ours and every burst's identity, channel and bits are in "
    "it. A receiver under test must not be given it; the 'ours' flag is a policy label for the test (which LAPs the receiver is "
    "told to follow), not an RF property of the device. The blind scan of scripts/test_bt_synth_mixed.py supplies the eight "
    "true LAPs as templates: it checks that known LAPs are detected, not that unknown LAPs are discovered.",
    "air_bits_omitted is false: air_bits are in the sidecar, as in bt_synth_acl.py, because the task asks for its keys.",
    "The piconets' clocks do not drift and the sample clock is the file's; the slot grid of a piconet is exactly "
    "25000 samples a slot.",
]


class Bits:
    """The renderer's view of a packet: its on-air bits."""

    def __init__(self, bits):
        self.bits = bits


def bits_hex(bits):
    return acl.bits_hex(bits)


# --- the identities ------------------------------------------------------------------------

def sync_int(lap):
    return int(''.join('1' if b else '0' for b in br.sync_word(lap)), 2)


def hamming(a, b):
    return bin(a ^ b).count('1')


def draw_identities(rng, count):
    """``count`` LAPs, each outside the reserved range and not the GIAC or 0, with every pair of sync words at least
    MIN_SYNC_DISTANCE bits apart; and a UAP for each."""
    laps, syncs = [], []
    while len(laps) < count:
        lap = int(rng.integers(1, 1 << 24))
        if lap in RESERVED_LAPS or lap == GIAC or lap in laps:
            continue
        s = sync_int(lap)
        if any(hamming(s, t) < MIN_SYNC_DISTANCE for t in syncs):
            continue
        laps.append(lap)
        syncs.append(s)
    return laps, syncs


def draw_grid_offsets(rng, count):
    """``count`` integer slot-grid offsets 0..24999 that are at least MIN_GRID_SEPARATION apart (circular, mod a slot)."""
    out = []
    while len(out) < count:
        o = int(rng.integers(0, SLOT))
        if all(min((o - p) % SLOT, (p - o) % SLOT) >= MIN_GRID_SEPARATION for p in out):
            out.append(o)
    return out


# --- one piconet's traffic -------------------------------------------------------------------

def l2cap_body(rng, longest):
    length = int(rng.integers(L2CAP_MIN, longest + 1))
    data = bytes(rng.integers(0, 256, length - 4, dtype=np.uint8))
    return (length - 4).to_bytes(2, 'little') + L2CAP_CID.to_bytes(2, 'little') + data


#: Table 5.1's "Possible direction" of the eleven PDUs, from Core Vol 2 Part C: every one is C<->P (either side may send it)
#: except LMP_set_AFH, Central to Peripheral only.
LMP_FROM_PERIPHERAL = tuple(n for n in LMP_NAMES if n != 'LMP_set_AFH')

#: TID (Part C 2.4): 0 for a transaction the Central started, 1 for one the Peripheral started. A PDU that answers a
#: transaction (ANSWERS) carries the TID of the transaction it answers; any other PDU starts one.
LMP_ANSWERS = bt_lmp.ANSWERS


def lmp_tid(name, role):
    """The TID of a PDU sent by ``role``: the master's requests 0 and its answers 1; the slave's requests 1 and its answers 0."""
    answer = name in LMP_ANSWERS
    return int(answer) if role == 'master' else int(not answer)


def make_packet(dev, ptype, clk, counter, rng, allow_lmp, role='master', side_seed=None):
    """The packet and its truth: ``(Packet, extras)`` for one burst of a piconet.

    A master's DM1 carries one of the eleven PDUs in about half. A slave's does too, but only a PDU its Table 5.1 row allows
    from the Peripheral (not LMP_set_AFH) and with the TID of its own role. The draws from ``rng`` are those of the master's
    rule in both roles (the name from the eleven, then that PDU's parameters), so that a burst's timing, jitter and phase do not
    depend on the role's rule; a slave whose draw is LMP_set_AFH gets another PDU, from the stream ``side_seed``."""
    longest = br.PACKET_TYPES[ptype][4]
    lmp, llid, body = None, None, b''
    if ptype in ('NULL', 'POLL'):
        pass
    elif ptype == 'DM1' and allow_lmp and rng.random() < DM1_LMP_FRACTION:
        name = LMP_NAMES[int(rng.integers(0, len(LMP_NAMES)))]
        body, params, tid = bt_lmp.random_pdu(rng, name, clk)
        if role == 'slave':
            if name not in LMP_FROM_PERIPHERAL:
                name = LMP_FROM_PERIPHERAL[int(np.random.default_rng(side_seed).integers(0, len(LMP_FROM_PERIPHERAL)))]
                _, params, _ = bt_lmp.random_pdu(np.random.default_rng(side_seed + [1]), name, clk)
            tid = lmp_tid(name, 'slave')
            body = bt_lmp.encode(name, tid, **params)
        llid, lmp = 0b11, dict(name=name, tid=tid, params=params)
    else:
        body, llid = l2cap_body(rng, longest), 0b10
    kw = dict(llid=llid) if llid is not None else {}
    p = br.Packet(dev['lap'], dev['uap'], clk, ptype, body, lt_addr=LT_ADDR, flow=1, arqn=1, seqn=counter & 1,
                  payload_flow=1, **kw)
    return p, dict(llid=llid, body=body, lmp=lmp)


def plan_piconet(dev, seed, total, level_range):
    """Every burst of one piconet as a list of dicts, before the samples: the pairs in time order."""
    d = dev['id']
    rng = np.random.default_rng([seed, 100 + d])
    hop_fn = hop.afh_hop_fn([(dev['clk0'] >> 1, MAP)], dev['lap'], dev['uap'])
    tr = dev['traffic']
    s_last = (total - GRID_BASE - dev['grid_offset_int'] - 8 * SLOT) // SLOT        # last slot a pair may end in
    out = []
    s, k_m, k_s = 0, 0, 0
    while s + 6 <= s_last:
        if rng.random() >= tr['p_master_tx']:
            s += 2
            continue
        ptype = MASTER_TYPES[int(rng.choice(len(MASTER_TYPES), p=tr['master_probs']))]
        n = br.PACKET_TYPES[ptype][1]
        clk_m = (dev['clk0'] + 2 * s) & 0x0FFFFFFF
        channel = int(hop_fn(clk_m))
        phase = (0, SPS // 2)[k_m % 2]
        pair = []
        for role, slot, clk, counter in (('master', s, clk_m, k_m), ('slave', s + n, clk_m + 2 * n, k_s)):
            if role == 'slave':
                if rng.random() >= tr['p_slave_answer']:
                    break
                ptype_b = SLAVE_TYPES[int(rng.choice(len(SLAVE_TYPES), p=tr['slave_probs']))]
            else:
                ptype_b = ptype
            b_rng = np.random.default_rng([seed, 300 + d, 2 * (k_m if role == 'master' else k_s) + (role == 'slave')])
            p, ex = make_packet(dev, ptype_b, clk & 0x0FFFFFFF, counter, b_rng, True, role,
                                [seed, 700 + d, k_s])
            jitter = float(b_rng.uniform(-JITTER_DB, JITTER_DB))
            pair.append(dict(
                device=d, role=role, ptype=ptype_b, packet=p, slot=slot, clk=clk & 0x0FFFFFFF, hop_clk=clk_m,
                channel=channel, slots=br.PACKET_TYPES[ptype_b][1], timing_frac=float(b_rng.uniform(0, 1)),
                burst_phase=float(b_rng.uniform(0, 2 * np.pi)), snr_db=round(dev['level_db'] + jitter, 2),
                jitter_db=round(jitter, 4), pair=k_m, symbol_phase_target=phase, **ex))
            if role == 'slave':
                k_s += 1
        # the pair, shifted as a whole to its symbol phase
        s0 = GRID_BASE + dev['grid_offset_int'] + SLOT * s
        shift = (phase - s0) % SPS
        for b in pair:
            b['start'] = GRID_BASE + dev['grid_offset_int'] + SLOT * b['slot'] + shift
            b['boundary'] = GRID_BASE + dev['grid_offset_samples'] + SLOT * b['slot']
        out.extend(pair)
        k_m += 1
        s += n + 1                                  # n is odd: the next master slot, after the slave's slot
    return out


# --- the joiners ---------------------------------------------------------------------------

class _FakeLock(list):
    """What bt_synth_conf's clock search is replaced by: everything is a solution, so its own assertion holds."""

    def __contains__(self, item):
        return True


def plan_joiner(j, seed, ident, origin_target, snr_hint=None):
    """One page exchange by ``bt_synth_conf.plan_conf``, with this joiner's identities, shifted to start at
    ``origin_target`` (a multiple of 40): ``(info, entries, jobs, last_sample)``."""
    paged = dict(lap=ident['paged_lap'], uap=ident['paged_uap'])
    master = dict(lap=ident['lap'], uap=ident['uap'], nap=ident['nap'], cod=ident['cod'])
    with mock.patch.object(conf, 'PAGED', paged), mock.patch.object(conf, 'MASTER', master), \
            mock.patch.object(conf, 'clock_solutions', lambda obs, addr: _FakeLock([0])):
        side, jobs, _ = conf.plan_conf(kind='page', exchanges=1, followup=FOLLOWUP, seed=ident['conf_seed'])
    ex = side['exchanges'][0]
    delta = origin_target - ex['start_sample']
    assert delta % SPS == (-ex['start_sample']) % SPS
    entries = []
    ji = 0
    last = 0
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
            job['packet'] = Bits(np.array(bits, dtype=np.uint8))
            e['_job'] = job
            e['_bits'] = bits
            last = max(last, e['start_sample'] + len(bits) * SPS + 100)
        entries.append(e)
    assert ji == len(jobs)
    info = dict(train=ex['train'], koffset=ex['koffset'], heard_slot=ex['heard_slot'], heard_id=ex['heard_id'],
                n_id_slots=ex['n_id_slots'], snr_base_db=ex['snr_base_db'], n_bursts=ex['n_bursts'],
                n_bursts_in_window=ex['n_bursts_in_window'], fhs_in_window=ex['fhs_in_window'],
                id_in_window=ex['id_in_window'], first_poll_in_window=ex['first_poll_in_window'],
                n_followup_in_window=ex['n_followup_in_window'], clke0=ex['clke0'], paged_clkn0=ex['paged_clkn0'],
                master_clkn0=ex['master_clkn0'], scan_channel=ex['scan_channel'], tick_samples=side['tick_samples'],
                origin_sample=ex['start_sample'] + delta, fhs_clk=ex['fhs_clk'], poll_tick=ex['poll_tick'],
                followup_basic_channels=ex['followup_basic_channels'], n_followup=FOLLOWUP)
    return info, entries, last


def joiner_entry(e, dev, bits):
    """A joiner burst, from the builder's entry, in this file's keys."""
    out = dict(e)
    out.pop('_job', None)
    out.pop('_bits', None)
    side = e['role']
    out['exchange_role'] = side
    out['role'] = e['kind']
    out['device'] = dev['id']
    out['ours'] = dev['ours']
    out['piconet_slot'] = e['slot_index']
    out['uap'] = e['uap_for_hec']
    full = e.get('payload_full_hex')
    for key in ('llid', 'payload_length', 'payload_hex', 'lmp_opcode', 'lmp_name', 'lmp_tid', 'lmp_params', 'lt_addr',
                'flow', 'arqn', 'seqn', 'header18', 'payload_full_hex', 'payload_valid'):
        out.setdefault(key, None)
    if e['kind'] in ('followup_master', 'followup_slave') and e['ptype'] in ('DH1', 'DM1'):
        out['llid'] = 0b10
    if e.get('payload_hex') is not None:
        out['payload_length'] = len(bytes.fromhex(e['payload_hex']))
    if e['ptype'] in ('NULL', 'POLL'):
        out['payload_length'] = 0
    if bits is not None:
        out['air_bits'] = bits_hex(bits)
        out['air_bits_length'] = len(bits)
        out['end_sample'] = int(e['start_sample'] + len(bits) * SPS)
        body = bytes.fromhex(full) if full else b''
        if body:
            pbits = br.bytes_bits(body)
            out['body_crc_bits_hex'] = bits_hex(pbits)
            out['body_crc_bits_length'] = len(pbits)
        else:
            out['body_crc_bits_hex'], out['body_crc_bits_length'] = None, None
    else:
        out['air_bits'] = out['air_bits_length'] = out['body_crc_bits_hex'] = out['body_crc_bits_length'] = None
        out['end_sample'] = None
    return out


# --- the overlap truth ----------------------------------------------------------------------

def compute_overlaps(starts, ends, channels, snrs, devices):
    """For every burst, its overlaps (time overlap and channel within 1), by one sweep over the starts.

    ``starts`` and ``ends`` are the real windows. Returns ``(overlaps, collision, overlap_frac)`` lists, the overlaps of a
    burst sorted by the other's index."""
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
    overlaps, collision, frac = [], [], []
    for i in range(n):
        rows, ivs = [], []
        for j, ov in sorted(over[i]):
            off = channels[j] - channels[i]
            rows.append(dict(burst=j, device=devices[j], overlap_samples=ov, channel_offset_mhz=float(off),
                             sir_db=round(snrs[i] - snrs[j], 4)))
            if off == 0:
                ivs.append((max(starts[i], starts[j]), min(ends[i], ends[j])))
        covered, hi = 0.0, -math.inf
        for a, b in sorted(ivs):
            a = max(a, hi)
            if b > a:
                covered += b - a
                hi = b
        overlaps.append(rows)
        collision.append(bool(ivs))
        frac.append(covered / (ends[i] - starts[i]))
    return overlaps, collision, frac


# --- the plan --------------------------------------------------------------------------------

def plan_mixed(seed=9201, blocks=BLOCKS, block_samples=BLOCK_SAMPLES, level_range=LEVEL_RANGE, name=None):
    """The sidecar and the render jobs of a file, no samples made: ``(sidecar, jobs, total)``. ``jobs`` hold the
    rendered bursts, in the order of ``bursts``."""
    fs = FS
    total = blocks * block_samples
    if total < 12 * SLOT + 3 * 3 * 12 * SLOT:
        raise ValueError("%d samples are too few for the traffic and three exchanges" % total)
    rng = np.random.default_rng([seed, 1 << 20])
    n_ids = N_PICONETS + 2 * N_JOINERS                         # the piconets' LAPs, and a master's and a paged LAP per joiner
    laps, syncs = draw_identities(rng, n_ids)
    offsets = draw_grid_offsets(rng, N_PICONETS)
    not_ours = set(int(i) for i in rng.choice(N_PICONETS, size=N_NOT_OURS, replace=False))
    devices = []
    for d in range(N_PICONETS):
        level = round(float(rng.uniform(*level_range)), 1)
        frac = float(rng.uniform(0, 1))
        pm = rng.dirichlet(MASTER_ALPHA)
        ps = rng.dirichlet(SLAVE_ALPHA)
        dev = dict(
            id=d, kind='piconet', ours=d not in not_ours, lap=laps[d], uap=int(rng.integers(0, 256)),
            nap=int(rng.integers(0, 1 << 16)), clk0=4 * int(rng.integers(0, (1 << 26) - (1 << 17))),
            grid_offset_int=offsets[d], grid_offset_frac=frac, grid_offset_samples=offsets[d] + frac,
            level_db=level,
            traffic=dict(p_master_tx=round(float(rng.uniform(*P_MASTER_TX)), 3),
                         p_slave_answer=round(float(rng.uniform(*P_SLAVE_ANSWER)), 3),
                         master_types=list(MASTER_TYPES), master_probs=[float(x) for x in pm],
                         slave_types=list(SLAVE_TYPES), slave_probs=[float(x) for x in ps],
                         dm1_lmp_fraction=DM1_LMP_FRACTION))
        devices.append(dev)
    # the joiners: a master and a paged device each
    joiners = []
    for j in range(N_JOINERS):
        joiners.append(dict(
            id=N_PICONETS + j, kind='joiner', ours=True, lap=laps[N_PICONETS + 2 * j], uap=int(rng.integers(0, 256)),
            nap=int(rng.integers(0, 1 << 16)), cod=int(rng.choice([0x5A020C, 0x200404, 0x240408, 0x7A020C])),
            paged_lap=laps[N_PICONETS + 2 * j + 1], paged_uap=int(rng.integers(0, 256)),
            conf_seed=int(rng.integers(1, 1 << 30))))

    # --- the piconets' bursts
    recs = []
    for dev in devices:
        for b in plan_piconet(dev, seed, total, level_range):
            recs.append(('piconet', dev, b))

    # --- the joiners' exchanges, each in a third of the file
    third = total // N_JOINERS
    j_infos, j_entries, j_last = [], [], []
    for j, jd in enumerate(joiners):
        lo = j * third + SLOT
        # an exchange is shorter than 60 ms; room for it in its third
        room = third - 2 * SLOT - 2_400_000
        if room < 0:
            raise ValueError("a third of the file is too short for an exchange")
        target = SPS * int((lo + rng.uniform(0, 1) * room) // SPS)
        info, entries, last = plan_joiner(j, seed, jd, target)
        j_infos.append(info)
        j_entries.append(entries)
        j_last.append(last)
        if last > total - SLOT:
            raise ValueError("exchange %d ends at %d, past the file" % (j, last))
    # --- the entries of every rendered burst
    flat = []
    for kind, dev, b in recs:
        p = b['packet']
        entry = {'start_sample': int(b['start']), 'timing_frac': b['timing_frac']}
        entry.update(p.sidecar())
        entry.update(
            air_bits=bits_hex(p.bits), air_bits_length=len(p.bits), lap=dev['lap'], uap=dev['uap'],
            llid=b['llid'], payload_length=len(b['body']),
            lmp_opcode=None if b['lmp'] is None else bt_lmp.PDUS[b['lmp']['name']]['opcode'],
            lmp_name=None if b['lmp'] is None else b['lmp']['name'],
            lmp_tid=None if b['lmp'] is None else b['lmp']['tid'],
            lmp_params=None if b['lmp'] is None else {
                key: (v.hex() if isinstance(v, bytes) else v) for key, v in b['lmp']['params'].items()},
            slots=b['slots'], end_sample=int(b['start'] + len(p.bits) * SPS), channel=b['channel'],
            channel_mhz=hop.channel_mhz(b['channel']), symbol_phase=int(b['start'] % SPS), snr_db=b['snr_db'],
            jitter_db=b['jitter_db'], burst_phase=b['burst_phase'], device=dev['id'], role=b['role'], ours=dev['ours'],
            kind='traffic', piconet_slot=b['slot'], pair=b['pair'], hop_clk=b['hop_clk'], in_window=True, rendered=True,
            slot_boundary_sample=b['boundary'])
        if p.ptype in ('NULL', 'POLL'):
            entry.update(body_crc_bits_hex=None, body_crc_bits_length=None)
        else:
            pb = br.bytes_bits(p.payload_full)
            entry.update(body_crc_bits_hex=bits_hex(pb), body_crc_bits_length=len(pb))
        job = dict(packet=Bits(np.array(p.bits, dtype=np.uint8)), start=int(b['start']), frac=b['timing_frac'],
                   phase=b['burst_phase'], amp=amplitude_for(b['snr_db']), channel=b['channel'])
        flat.append((entry, job))
    not_rendered = []
    for jd, entries in zip(joiners, j_entries):
        for e in entries:
            if e['rendered']:
                entry = joiner_entry(e, jd, e['_bits'])
                job = dict(e['_job'])
                job['amp'] = amplitude_for(entry['snr_db'])
                entry['burst_phase'] = job['phase']
                flat.append((entry, job))
            else:
                entry = joiner_entry(e, jd, None)
                not_rendered.append(entry)
    flat.sort(key=lambda t: (t[0]['start_sample'] + t[0]['timing_frac'], t[0]['device'], t[0]['channel']))
    entries = [e for e, _ in flat]
    jobs = [j for _, j in flat]
    starts = [e['start_sample'] + e['timing_frac'] for e in entries]
    ends = [s + e['air_bits_length'] * SPS for s, e in zip(starts, entries)]
    if max(ends) + 200 > total:
        raise ValueError("a burst ends at %g, past the end of the file at %d" % (max(ends), total))
    channels = [e['channel'] for e in entries]
    overlaps, collision, frac = compute_overlaps(starts, ends, channels, [e['snr_db'] for e in entries],
                                                 [e['device'] for e in entries])
    for i, e in enumerate(entries):
        e['index'] = i
        e['overlaps'], e['collision'], e['overlap_frac'] = overlaps[i], collision[i], frac[i]
    for e in not_rendered:
        e['overlaps'], e['collision'], e['overlap_frac'] = [], False, 0.0

    # --- the device table
    dev_rows = []
    sync_words = {}
    for lap in laps:
        sync_words[lap] = sync_int(lap)
    for dev in devices:
        others = [t for lap, t in sync_words.items() if lap != dev['lap']]
        dev_rows.append(dict(
            id=dev['id'], kind='piconet', ours=dev['ours'], lap=dev['lap'], uap=dev['uap'], nap=dev['nap'],
            clk0=dev['clk0'], slot_grid_offset_samples=dev['grid_offset_samples'],
            slot_grid_offset_int=dev['grid_offset_int'], slot_grid_offset_frac=dev['grid_offset_frac'],
            level_db=dev['level_db'], jitter_db=JITTER_DB,
            hop_map=dict(kind='adapted', channels=MAP, used_channels_mask_hex='%x' % MASK,
                         address_for_hop=dev['uap'] << 24 | dev['lap'], instant_clk27_1=dev['clk0'] >> 1,
                         note='bt_hop.hop_channel(clk, address_for_hop, mask); the 8 piconets share the map and differ '
                              'in address and clock'),
            traffic=dev['traffic'], sync_word_hex='%016x' % sync_words[dev['lap']],
            sync_word_min_distance=min(hamming(sync_words[dev['lap']], t) for t in others)))
    for jd, info in zip(joiners, j_infos):
        others = [t for lap, t in sync_words.items() if lap != jd['lap']]
        dev_rows.append(dict(
            id=jd['id'], kind='joiner', ours=True, lap=jd['lap'], uap=jd['uap'], nap=jd['nap'], cod=jd['cod'],
            paged=dict(lap=jd['paged_lap'], uap=jd['paged_uap'],
                       sync_word_hex='%016x' % sync_words[jd['paged_lap']]),
            clk0=info['master_clkn0'], slot_grid_offset_samples=info['origin_sample'] % SLOT,
            level_db=info['snr_base_db'], jitter_db='N(0, 1) clipped to +-3 per burst (bt_synth_conf)',
            hop_map=dict(kind='specification', channels=None, used_channels_mask_hex=None,
                         address_for_hop=jd['uap'] << 24 | jd['lap'],
                         note='page, page response and Central page response sequences for the exchange, then the '
                              'BASIC connection sequence (79 channels, no map): not the 31-50 map of the piconets'),
            exchange=info, sync_word_hex='%016x' % sync_words[jd['lap']],
            sync_word_min_distance=min(hamming(sync_words[jd['lap']], t) for t in others)))
    all_s = list(sync_words.values())
    pic_s = [sync_words[dev['lap']] for dev in devices]
    min_all = min(hamming(a, b) for i, a in enumerate(all_s) for b in all_s[i + 1:])
    min_pic = min(hamming(a, b) for i, a in enumerate(pic_s) for b in pic_s[i + 1:])

    n = len(entries)
    n_coll = sum(collision)
    per_dev, per_type = {}, {}
    for e in entries:
        per_dev.setdefault(e['device'], {})
        per_dev[e['device']][e['ptype']] = per_dev[e['device']].get(e['ptype'], 0) + 1
        per_type[e['ptype']] = per_type.get(e['ptype'], 0) + 1
    per_dev_total = {d: sum(v.values()) for d, v in per_dev.items()}
    n_pairs = sum(len(o) for o in overlaps) // 2
    n_same = sum(1 for o in overlaps for r in o if r['channel_offset_mhz'] == 0) // 2
    n_adj_only = sum(1 for o in overlaps if o and not any(r['channel_offset_mhz'] == 0 for r in o))
    per_burst_keys = sorted(set(entries[0]).union(*(set(e) for e in entries)))
    k_pic = [set(e) for e in entries if e['device'] < N_PICONETS]
    k_joi = [set(e) for e in entries if e['device'] >= N_PICONETS and e['ptype'] != 'FHS']
    k_fhs = [set(e) for e in entries if e['device'] >= N_PICONETS and e['ptype'] == 'FHS']
    base_pic = set.union(*k_pic)
    base_joi = set.union(*k_joi)
    per_burst_keys_by_kind = dict(
        piconet=sorted(base_pic), joiner=sorted(base_joi),
        fhs_extra=sorted(set.union(*k_fhs) - base_joi) if k_fhs else [])
    sidecar = {
        'generator': 'SDR scripts/bt_synth_mixed.py',
        'generator_commit': bt_synth.commit(),
        'name': name,
        'lap': None, 'uap': None, 'nap': None, 'clk': None, 'ptype': None, 'channel': None, 'channel_mhz': None,
        'bt_channel': None, 'snr_db': None, 'timing_frac': None, 'symbol_phase': None,
        'clk_convention': 'native CLK[27:0] of the sender at the first sample of the burst it is given for '
                          '(each device has its own clock: devices[].clk0 at its slot 0); a slave burst\'s clk is the clock of its '
                          'own slot and its hop_clk the clock of the master slot that its channel is computed from; a joiner '
                          'burst\'s clk and hop_clk are the builder\'s (bt_synth_conf: hop_clk_kind CLKE, CLKN or CLK)',
        'hopping': True,
        'hop_channels': sorted({e['channel'] for e in entries}),
        'sample_rate': fs, 'center_mhz': CENTER_MHZ, 'cfo_hz': 0.0,
        'n_samples': int(total),
        'n_samples_meaning': 'the number of complex samples in the file; its size is 8 * n_samples bytes',
        'n_noise_blocks': blocks, 'noise_block_samples': block_samples,
        'snr_bw_hz': 1e6, 'noise_1mhz': NOISE_1MHZ,
        'modulation': {'scheme': 'GFSK', 'bt': br.GFSK_BT, 'h': br.GFSK_H, 'symbol_rate': br.SYMBOL_RATE},
        'slot_samples': SLOT, 'start_offset': 0, 'grid_base_samples': GRID_BASE,
        'symbol_phases': [0, SPS // 2],
        'symbol_phase_meaning': 'null at the top: a master packet and its answer are shifted as a pair to the next '
                                'sample that is 0 (even pair) or 20 (odd pair) mod 40; a joiner exchange starts at a '
                                'multiple of 40 and its odd ticks (12500 samples) sit at 20',
        'start_sample_meaning': 'the first sample of the access code\'s preamble, before timing_frac is added',
        'timing_frac_meaning': 'null at the top: every burst has its own, uniform in [0, 1)',
        'air_bits_omitted': False,
        'air_bits_note': acl.AIR_BITS_NOTE + ' The joiner bursts that are not rendered have none.',
        'burst_window': WINDOW_TEXT,
        'window': dict(channels=list(WINDOW), mhz=list(conf.WINDOW_MHZ), why=WINDOW_GATE_NOTE,
                       text='a joiner burst is rendered iff its channel is %d to %d (the piconets are all on 31-50, inside)' % WINDOW),
        'conformance': CONFORMANCE,
        'notes': NOTES,
        'seed': seed,
        'devices': dev_rows,
        'n_devices': len(dev_rows), 'n_piconets': N_PICONETS, 'n_joiners': N_JOINERS,
        'ours_devices': [r['id'] for r in dev_rows if r['ours']],
        'not_ours_devices': [r['id'] for r in dev_rows if not r['ours']],
        'level_range_db': list(level_range), 'jitter_db': JITTER_DB,
        'sync_word_min_distance_piconets': min_pic, 'sync_word_min_distance_all': min_all,
        'sync_word_distance_required': MIN_SYNC_DISTANCE,
        'sync_word_note': 'Hamming distance between the 64-bit sync words (br.sync_word) of the %d piconet LAPs and of all %d LAPs on '
                          'the air (the piconets\', each joiner\'s master and paged device); %d is the least any two are '
                          'allowed' % (N_PICONETS, n_ids, MIN_SYNC_DISTANCE),
        'afh_map': MAP, 'afh_map_note': 'the piconets only; the joiners are on the specification\'s sequences',
        'hop_kernel': hop.HOP_KERNEL,
        'multi_slot_channel_note': 'a multi-slot packet is on the channel of its first slot for its whole length; the slave\'s '
                                   'answer is on the same channel (same channel mechanism, Figure 2.15)',
        'n_bursts': n, 'n_bursts_not_rendered': len(not_rendered),
        'counts_per_device': {str(d): per_dev_total.get(d, 0) for d in sorted(per_dev_total)},
        'counts_per_device_and_type': {str(d): per_dev[d] for d in sorted(per_dev)},
        'counts_per_type': dict(sorted(per_type.items())),
        'n_collisions': n_coll, 'fraction_collided': n_coll / n,
        'n_overlap_pairs': n_pairs, 'n_same_channel_overlap_pairs': n_same,
        'n_bursts_overlapped_adjacent_channel_only': n_adj_only,
        'collision_note': 'a burst is collided iff another burst of any device overlaps it in time on the same channel; '
                          'n_collisions counts the bursts (not the pairs); n_same_channel_overlap_pairs counts the pairs',
        'per_burst_keys': per_burst_keys,
        'per_burst_keys_by_kind': per_burst_keys_by_kind,
        'per_burst_keys_note': 'per_burst_keys is the UNION of the keys of all bursts. A piconet burst (devices 0-7) lacks the joiner-only '
                               'keys exchange, exchange_role, hop, hop_clk_kind, slot_index, tick, uap_for_hec (and fhs); a joiner burst '
                               '(devices 8-10) lacks the piconet-only keys jitter_db, pair, slot_boundary_sample and slots. '
                               'per_burst_keys_by_kind lists exactly the keys a burst of each kind has in bursts: piconet, joiner, and '
                               'fhs_extra, the keys a joiner FHS burst has in addition to the joiner keys. bursts_not_rendered entries '
                               'are joiner entries without index and burst_phase.',
        'indexing': 'bursts is sorted by start_sample + timing_frac; bursts[i].index == i; overlaps[].burst is an index into bursts',
        'bursts': entries,
        'bursts_not_rendered': not_rendered,
    }
    return sidecar, jobs, total


# --- the samples -----------------------------------------------------------------------------

def render_blocks(jobs, total, seed, block=BLOCK_SAMPLES, fs=FS, center_mhz=CENTER_MHZ, noise=True):
    """Yield the file in blocks of ``block`` samples as complex64: noise block ``i`` (``noise_block``), then every burst
    that reaches the block, each rendered once and dropped when its last block is done."""
    spans = [pairs.burst_span(j, fs) for j in jobs]
    order = sorted(range(len(jobs)), key=lambda i: spans[i][0])
    nxt = 0
    active = {}
    for index, b0 in enumerate(range(0, total, block)):
        b1 = min(total, b0 + block)
        out = noise_block(seed, index, b1 - b0, fs) if noise else np.zeros(b1 - b0, dtype=np.complex64)
        while nxt < len(order) and spans[order[nxt]][0] < b1:
            i = order[nxt]
            if spans[i][1] > b0:
                active[i] = pairs.render_burst(jobs[i], fs, center_mhz)
            nxt += 1
        for i in sorted(active):
            lo, samples = active[i]
            a, b = max(lo, b0), min(lo + len(samples), b1)
            if b > a:
                out[a - b0:b - b0] += samples[a - lo:b - lo]
        for i in [i for i in active if active[i][0] + len(active[i][1]) <= b1]:
            del active[i]
        yield out


def synthesise_mixed(seed=9201, blocks=3, block_samples=BLOCK_SAMPLES, level_range=LEVEL_RANGE, noise=True, name=None):
    """The samples and the sidecar of a file, in memory: for a small run."""
    sidecar, jobs, total = plan_mixed(seed, blocks, block_samples, level_range, name)
    iq = np.concatenate(list(render_blocks(jobs, total, seed, block_samples, noise=noise)))
    return iq, sidecar


def write_mixed(name, out_dir, seed=9201, blocks=BLOCKS, **spec):
    """Make one file, write ``synth_<name>.cf32`` block by block and ``synth_<name>.json``."""
    sidecar, jobs, total = plan_mixed(seed, blocks, name=name, **spec)
    os.makedirs(out_dir, exist_ok=True)
    iq_path = os.path.join(out_dir, 'synth_%s.cf32' % name)
    side_path = os.path.join(out_dir, 'synth_%s.json' % name)
    with open(iq_path, 'wb') as f:
        for out in render_blocks(jobs, total, seed, spec.get('block_samples', BLOCK_SAMPLES)):
            out.astype('<c8', copy=False).tofile(f)
    with open(side_path, 'w') as f:
        json.dump(sidecar, f, separators=(',', ':'))
    print('%s  %d samples, %.2f s, %d bursts, %d collided (%.1f %%), %d joiner bursts not rendered' % (
        iq_path, total, total / FS, sidecar['n_bursts'], sidecar['n_collisions'], 100 * sidecar['fraction_collided'],
        sidecar['n_bursts_not_rendered']), flush=True)
    print(side_path)
    return iq_path, side_path


def set_files():
    return [(n, dict(seed=s, blocks=BLOCKS)) for n, s in SEEDS.items()]


SETS = {'mixed': set_files}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('name', nargs='?', help='the file is synth_<name>.cf32; not used with --set')
    ap.add_argument('--set', choices=sorted(SETS), help='write the whole set, with its seeds and no other option but --out')
    ap.add_argument('--seed', type=int, help='default 9201')
    ap.add_argument('--blocks', type=int, help='noise blocks of 4194304 samples; default %d' % BLOCKS)
    ap.add_argument('--out', default=DEFAULT_OUT, help='the folder; default ' + DEFAULT_OUT)
    args = ap.parse_args(argv)
    if args.set:
        if args.name is not None or args.seed is not None or args.blocks is not None:
            ap.error('--set takes everything from its table: no name and no option but --out')
        runs = SETS[args.set]()
    else:
        if args.name is None:
            ap.error('give the name of the file, or --set')
        runs = [(args.name, dict(seed=9201 if args.seed is None else args.seed, blocks=args.blocks or BLOCKS))]
    try:
        for name, spec in runs:
            write_mixed(name, args.out, **spec)
    except ValueError as e:
        ap.error(str(e))


if __name__ == '__main__':
    main()
