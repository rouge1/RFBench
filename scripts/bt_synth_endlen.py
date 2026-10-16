#!/usr/bin/env python3
"""Packets of known on-air length, for bluey's burst-end classes: empty DH1/DM1, AUX1, full DH1/DM1, DM3..DH5.

    python scripts/bt_synth_endlen.py --set E_mix --out DIR      # E_mix_s1 E_mix_s2
    python scripts/bt_synth_endlen.py --set E_ladder --out DIR   # E_ladder_s1
    python scripts/bt_synth_endlen.py --set E_20 --out DIR       # E_mix_20msps_s1 E_ladder_20msps_s1
    python scripts/bt_synth_endlen.py --set E_long --out DIR     # E_long_s1
    python scripts/bt_synth_endlen.py --set all --out DIR
    python scripts/bt_synth_endlen.py --stem E_mix_s1 --duration 3 --out DIR

    from scripts import bt_synth_endlen as g
    iq, sidecar = g.synthesise('E_mix_s1', duration=0.5, snr_db=45)

bluey-ox-walker classes a burst by energy after bit 68 / 126 / 366 from a calibrated start and takes the end to be
the specification length.  It could not test the 126 against 150 (empty DH1) / 171 (empty DM1) / 134 (AUX1), nor
any burst longer than 366.  These files carry those packets with the **truth of the on-air length per burst**
(``onair_bits``, ``end_sample``, ``end_us``).  Nothing here transmits and nothing opens a radio.

**Conventions are those of bt_synth_slotb2** (its machinery, ``finish`` and the renderer, is imported, not copied):
one slot grid, TX slot ``k`` at file microsecond ``20000 + 50.25 + 1250 (k - 16)``, drift 0 (``--offset-us`` and
``--drift-ppm`` exist; **grid and drift are not the subject of these files**), one crystal, every burst 20 dB over
the one noise floor, noise through the whole file, 20 ms of lead-in, no ``air_bits``.  40 MS/s on 2439.0 MHz (window
12 MHz) or 20 MS/s on 2441.0 MHz (window 8 MHz), the centre channel never used.  The master's carrier offset
``master_cfo_hz`` is -30 kHz in the ``_s1`` files and +30 kHz in ``_s2``; the slave's is +30 kHz with a per-burst
timing offset N(0, 1.5) us clipped to +-3 us, as in slotb2.

**No tails.**  Every burst, ID or packet, ends as ``br.gfsk`` ends it: after its last bit the amplitude falls as a
raised cosine over 2 us.  Nothing is held.  ``end_sample`` / ``end_us`` are the end of the last bit,
``start_sample + timing_frac + onair_bits * sps`` (file microseconds for ``end_us``), BEFORE the ramp: the power
half-point of the falling edge is about 0.6 us later, the amplitude half-point 1 us.

**One LAP/UAP per file.**  The connection runs on LAP L; the page-train IDs of the E_mix files carry the *same* LAP L,
as bluey's real captures do.  That is not a spec-true page (a page uses the paged device's LAP, and a connection
the master's).  UAP never 0x00, 0xD6, 0x0B, 0xDD; LAP not in 0x9E8B00..0x9E8B3F.  No FHS, no page exchange.

**Connection traffic** is slotb's set A: master packet in a TX slot, the slave's answer one slot later on the same
fixed in-window channel (31 35 39 43 47 at 40 MS/s; 33 36 43 46 at 20 MS/s), the channel drawn per exchange from that
list.  The channel is NOT the basic hop sequence of the clock (that would put most packets out of the window): the
clock is the whitening clock only.  A multi-slot packet is on the channel of its first slot, its slave answer the
slot after its last (3-slot packet from slot s: answer in s + 3).

**Truth per burst** beside slotb2's keys: ``ptype``, ``body_bytes``, ``info_bytes``, ``payload_bits_pre_fec``,
``fec_pad_bits``, ``payload_bits_air``, ``onair_bits`` (72 + 54 + payload_bits_air; 68 for an ID), ``end_sample``,
``end_us``, ``crc``, ``fec`` ('none' or '2/3'), ``llid``.  AUX1 is not in ``br.PACKET_TYPES`` and is built here
from the module's lower level functions: type 0b1001, a one-byte payload header (LLID 0b10) and 0..29 body bytes,
no CRC and no FEC, whitened like any payload.  The sidecar's ``onair_counts`` maps ``"<ptype>:<onair_bits>"`` to
the number of rendered bursts.
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
from scripts import bt_synth  # noqa: E402
from scripts import bt_synth_slotb as sb  # noqa: E402
from scripts import bt_synth_slotb2 as s2  # noqa: E402

DEFAULT_OUT = '/tmp/sdr-p15'
K0 = sb.K0
OFFSET_US = 50.25
SLAVE_CFO_HZ = 30e3
TIMING_SIGMA_US, TIMING_CLIP_US = 1.5, 3.0
RESERVED_LAPS = sb.RESERVED_LAPS
AM_ADDR = 1
CONN_DENSITY = 0.5                      # share of master TX slots the E_mix connection takes (at most)
ID_SHARE = 20.0                         # percent of the master side's rendered bursts
TYPE_CODES = {'NULL': 0b0000, 'POLL': 0b0001, 'DM1': 0b0011, 'DH1': 0b0100, 'AUX1': 0b1001, 'DM3': 0b1010,
              'DH3': 0b1011, 'DM5': 0b1110, 'DH5': 0b1111}
LONGEST = {'DM1': 17, 'DH1': 27, 'AUX1': 29, 'DM3': 121, 'DH3': 183, 'DM5': 224, 'DH5': 339}
SLOTS = {'NULL': 1, 'POLL': 1, 'DM1': 1, 'DH1': 1, 'AUX1': 1, 'DM3': 3, 'DH3': 3, 'DM5': 5, 'DH5': 5}
#: master-side class mix, percent of the rendered bursts of the master side (IDs included)
MIX = {'ID': 20.0, 'NULL': 10.0, 'POLL': 5.0, 'DH1_0': 15.0, 'DM1_0': 15.0, 'AUX1': 10.0, 'DH1': 10.0,
       'DH1_full': 5.0, 'DM1': 5.0, 'DM1_full': 5.0}
MIX_TOLERANCE = 3.0
#: E_np (NULL/POLL-heavy connection, no IDs): master-side class mix and slave-answer mix, percent of each side.
#: NULL/POLL are 66 % of the master packets and 90 % of the answers: 78 % of ALL bursts of the LAP, answers counted.
NP_MIX = {'POLL': 33.0, 'NULL': 33.0, 'DH1_0': 8.5, 'DM1_0': 8.5, 'DH1': 8.5, 'DM1': 8.5}
NP_ANSWER = {'NULL': 90.0, 'DH1_0': 2.5, 'DM1_0': 2.5, 'DH1': 2.5, 'DM1': 2.5}
NP_TARGET_ALL, NP_TOLERANCE = 78.0, 3.0

SPECS = {}


def _add(stem, seed, kind, fs=40e6, center=2439.0, duration=12.0, **extra):
    SPECS[stem] = dict(stem=stem, seed=seed, kind=kind, fs=fs, center=center, duration=duration, **extra)


_add('E_mix_s1', 9601, 'mix', master_cfo_hz=-30e3)
_add('E_mix_s2', 9602, 'mix', master_cfo_hz=30e3)
_add('E_ladder_s1', 9603, 'ladder', master_cfo_hz=-30e3)
_add('E_mix_20msps_s1', 9604, 'mix', fs=20e6, center=2441.0, duration=10.0, master_cfo_hz=-30e3)
_add('E_long_s1', 9605, 'long', master_cfo_hz=-30e3)
_add('E_ladder_20msps_s1', 9606, 'ladder', fs=20e6, center=2441.0, duration=10.0, master_cfo_hz=-30e3)
_add('E_np_s1', 9607, 'np', master_cfo_hz=-30e3)
SETS = {'E_mix': ['E_mix_s1', 'E_mix_s2'], 'E_ladder': ['E_ladder_s1'],
        'E_20': ['E_mix_20msps_s1', 'E_ladder_20msps_s1'], 'E_long': ['E_long_s1']}
SETS['E_np'] = ['E_np_s1']
SETS['all'] = SETS['E_mix'] + SETS['E_ladder'] + SETS['E_20'] + SETS['E_long']      # the six delivered files; E_np is apart


# --- hooks: what the truth and the samples are each made with (the test's mutants patch these) -----------------

def type_code(ptype):
    """The TYPE field of the header (Table 6.2)."""
    return TYPE_CODES[ptype]


def two_byte_header(ptype):
    """Part B 6.6: a 2-byte payload header in the 3- and 5-slot types, 1 byte in DM1, DH1 and AUX1."""
    return ptype in ('DM3', 'DH3', 'DM5', 'DH5')


def aux1_has_crc():
    return False


def fec_pad(n_bits):
    """Zero tail bits to a multiple of 10 before the 2/3 FEC (Part B 7.5)."""
    return (-n_bits) % 10


def info_bytes_of(ptype, body_n):
    """Information bytes as the specification counts them: payload header + body."""
    return (2 if two_byte_header(ptype) else 1) + body_n


def end_extra_symbols():
    """Symbols added to the end of the last bit in the truth: none (the end is before the ramp)."""
    return 0.0


def mix_weights(w):
    return w


def np_weights(w):
    return w


def np_answer_weights(w):
    return w


def answer_ticks(slots):
    """Ticks (312.5 us) from a master packet's start to its answer: the slot after its last."""
    return 2 * slots


def draw_uap(rng):
    while True:
        u = int(rng.integers(0, 256))
        if u not in s2.AVOID_UAPS:
            return u


def render_fs(fs):
    return fs


# --- packets ---------------------------------------------------------------------------------------------

def layout(ptype, body_n):
    """The lengths of a packet as the truth states them (from the rules of Table 6.2, 6.5.x, 7.5)."""
    if ptype == 'ID':
        return dict(onair=68, pre=0, pad=0, air=0, crc=False, fec='none', info=None)
    if ptype in ('NULL', 'POLL'):
        return dict(onair=126, pre=0, pad=0, air=0, crc=False, fec='none', info=None)
    crc = ptype != 'AUX1' or aux1_has_crc()
    pre = 8 * info_bytes_of(ptype, body_n) + (16 if crc else 0)
    fec = ptype.startswith('DM')
    pad = fec_pad(pre) if fec else 0
    air = (pre + pad) * 3 // 2 if fec else pre
    return dict(onair=126 + air, pre=pre, pad=pad, air=air, crc=crc, fec='2/3' if fec else 'none',
                info=info_bytes_of(ptype, body_n))


def build_bits(ptype, lap, uap, clk, body, llid, lt=AM_ADDR, flow=1, arqn=1, seqn=0):
    """The bits on air of any packet here, from the module's lower level functions (br.Packet has no AUX1)."""
    hdr = br.packet_header(lt, type_code(ptype), flow, arqn, seqn, uap)
    if ptype in ('NULL', 'POLL'):
        pl = []
    else:
        pl = br.payload_header(llid, 1, len(body), two_byte_header(ptype)) + br.bytes_bits(body)
        if ptype != 'AUX1' or aux1_has_crc():
            pl = pl + br.crc16(pl, uap)
    w = br.whitening(clk, len(hdr) + len(pl))
    hw = [a ^ b for a, b in zip(hdr, w)]
    pw = [a ^ b for a, b in zip(pl, w[len(hdr):])]
    if ptype.startswith('DM'):
        pw = br.fec23(pw)
    bits = br.access_code(lap) + br.fec13(hw) + pw
    return bits, hdr, (br.bits_bytes(pl).hex() if pl else '')


def body_of(counter, n):
    """A body of ``n`` bytes, distinct per counter: ``br.seq_body`` from 17 bytes up (the 4-byte L2CAP header and the 13-byte SEQ text), else the counter's bytes
    (little-endian, the low ``n`` of 4) then the base64 alphabet."""
    if n == 0:
        return b''
    if n >= 17:
        return br.seq_body(counter, n)
    head = bytes((counter >> (8 * i)) & 0xFF for i in range(min(n, 4)))
    return head + bytes(br._B64[(counter + i) % 64] for i in range(n - len(head)))


def pkt_event(k, q, role, sender, ptype, channel, clk, lap, uap, body_n, counter, llid=None, timing=Fraction(0), **kw):
    body = body_of(counter, body_n)
    if llid is None:
        llid = 0b01 if (body_n == 0 and ptype in ('DM1', 'DH1')) else 0b10
    bits, hdr, full_hex = build_bits(ptype, lap, uap, clk, body, llid, seqn=counter & 1)
    ev = s2.event(k, q, role, sender, ptype, channel, clk, lap, uap, bits, header=hdr, wclk=clk, flow=1, arqn=1,
                  seqn=counter & 1, lt_addr=AM_ADDR, payload_hex=body.hex(), payload_full_hex=full_hex,
                  phase='connection', hop_clk=clk, hop_clk_kind='CLK', timing_us=timing, **kw)
    ev['_t'] = dict(body_bytes=body_n if ptype not in ('NULL', 'POLL') else None, llid=llid if full_hex else None,
                    lay=layout(ptype, body_n))
    return ev


def id_ev(ev):
    ev['_t'] = dict(body_bytes=None, llid=None, lay=layout('ID', 0))
    return ev


# --- identity and plans ------------------------------------------------------------------------------------

def centre_channel(center):
    return int(round(center - 2402))


def draw_ident(rng, fs, center, first_slot, last_slot, with_train):
    """(lap, uap, clke_k0 or None, table or None): the LAP not reserved, the UAP outside the confusion set; with a
    train the page sequence of the LAP has no centre channel over the file's slots and enough in-window channels."""
    ctr = centre_channel(center)
    tries = 0
    ks = np.arange(first_slot, last_slot)
    while True:
        tries += 1
        lap = int(rng.integers(1, 1 << 24))
        if lap in RESERVED_LAPS:
            continue
        uap = draw_uap(rng)
        if not with_train:
            return lap, uap, None, None, tries
        base = int(rng.integers(1, 1000))
        b0 = int(rng.integers(0, 16)) * 2
        r = int(rng.integers(0, 1000)) * 4
        clke = (base << 17) | (b0 << 12) | r
        table = s2.page_table(lap, uap)
        tk = np.stack([clke + 4 * (ks - first_slot), clke + 4 * (ks - first_slot) + 1])
        ch = s2.page_vec(table, tk)
        if (ch == ctr).any():
            continue
        train = set(int(c) for c in ch[:, :8].ravel())
        if sum(1 for c in train if s2.in_win(c, fs, center)) < 4:
            continue
        return lap, uap, clke - 4 * (first_slot - K0), table, tries


def slave_timing_us(t_rng):
    return Fraction(int(round(float(np.clip(t_rng.normal(0, TIMING_SIGMA_US), -TIMING_CLIP_US, TIMING_CLIP_US)) *
                              1000)), 1000)


def exchange(k, ptype, body_n, counter, ch, clk_m, lap, uap, answer_type, answer_body_n, t_rng, slave_cfo, llid=None):
    """A master packet at slot ``k`` (q 0) and the slave's answer after its last slot."""
    evs = [pkt_event(k, 0, 'master', 'master', ptype, ch, clk_m, lap, uap, body_n, counter, llid=llid)]
    t = answer_ticks(SLOTS[ptype])
    kk, q = k + t // 4, t % 4
    evs.append(pkt_event(kk, q, 'slave', 'slave', answer_type, ch, clk_m + t, lap, uap, answer_body_n, counter + 1,
                         timing=slave_timing_us(t_rng), cfo_hz=slave_cfo))
    return evs


def largest_remainder(total, weights):
    names = sorted(weights)
    s = sum(weights.values())
    raw = {n: total * weights[n] / s for n in names}
    out = {n: int(math.floor(raw[n])) for n in names}
    for n in sorted(names, key=lambda n: -(raw[n] - out[n]))[:total - sum(out.values())]:
        out[n] += 1
    return out


def plan_mix(spec, rng, t_rng, n_slots, fs, center, slave_cfo):
    chans = sb.a_channels(fs, center)
    lap, uap, clke_k0, table, tries = draw_ident(rng, fs, center, K0, K0 + n_slots, True)
    ks = np.arange(K0, K0 + n_slots)
    win = np.array([s2.in_win(c, fs, center) for c in range(79)])
    tk = np.stack([clke_k0 + 4 * (ks - K0), clke_k0 + 4 * (ks - K0) + 1])
    r = win[s2.page_vec(table, tk)].sum(axis=0)             # rendered IDs of a slot if the train takes it
    perm = rng.permutation(ks)
    cs = np.concatenate([[0], np.cumsum(r[perm - K0])])
    m = int(CONN_DENSITY * n_slots)
    id_per_pkt = ID_SHARE / (100.0 - ID_SHARE)
    while m > 0 and cs[-1] - cs[m] < math.ceil(id_per_pkt * m):
        m -= max(1, n_slots // 100)
    used = sorted(int(k) for k in perm[:m])
    free = perm[m:]
    target_ids = int(round(id_per_pkt * m))
    taken, got = [], 0
    for pos in rng.permutation(len(free)):
        if got >= target_ids:
            break
        taken.append(int(free[pos]))
        got += int(r[free[pos] - K0])
    w = mix_weights({k: v for k, v in MIX.items() if k != 'ID'})
    counts = largest_remainder(m, w)
    classes = [c for c in sorted(counts) for _ in range(counts[c])]
    classes = [classes[i] for i in rng.permutation(len(classes))]
    clk_start = int(rng.integers(1 << 18, 1 << 26)) * 4
    evs = []
    for n, (k, cls) in enumerate(zip(used, classes)):
        ch = int(chans[int(rng.integers(0, len(chans)))])
        ptype, body_n = {'NULL': ('NULL', 0), 'POLL': ('POLL', 0), 'DH1_0': ('DH1', 0), 'DM1_0': ('DM1', 0),
                         'AUX1': ('AUX1', int(rng.integers(0, 30))), 'DH1': ('DH1', int(rng.integers(1, 28))),
                         'DH1_full': ('DH1', 27), 'DM1': ('DM1', int(rng.integers(1, 18))),
                         'DM1_full': ('DM1', 17)}[cls]
        a_type = 'NULL'
        if rng.random() < 0.25:
            a_type = ('DH1', 'DM1')[int(rng.integers(0, 2))]
        evs += exchange(k, ptype, body_n, 2 * n, ch, clk_start + 4 * (k - K0), lap, uap, a_type, 0, t_rng, slave_cfo)
    for ev in s2.train_events(sorted(taken), clke_k0, lap, uap, table):
        evs.append(id_ev(ev))
    info = dict(lap=lap, uap=uap, tries=tries, n_conn=m, train_slots=len(taken), free_slots=len(free),
                conn_clk_start=clk_start, mix_counts_planned=counts, channels=chans, train_clke_at_slot16=clke_k0)
    return evs, info


def np_class(cls, rng):
    """(ptype, body bytes) of an E_np class: ``_0`` is empty (payload header + CRC), the others have a body."""
    return {'NULL': ('NULL', 0), 'POLL': ('POLL', 0), 'DH1_0': ('DH1', 0), 'DM1_0': ('DM1', 0),
            'DH1': ('DH1', int(rng.integers(1, 28))), 'DM1': ('DM1', int(rng.integers(1, 18)))}[cls]


def plan_np(spec, rng, t_rng, n_slots, fs, center, slave_cfo):
    """One connection of NULL/POLL-heavy traffic: no IDs, no page train; exact class counts on each side."""
    chans = sb.a_channels(fs, center)
    lap, uap, _, _, tries = draw_ident(rng, fs, center, K0, K0 + n_slots, False)
    ks = np.arange(K0, K0 + n_slots)
    m = int(CONN_DENSITY * n_slots)
    used = sorted(int(k) for k in rng.permutation(ks)[:m])
    counts = largest_remainder(m, np_weights(dict(NP_MIX)))
    acounts = largest_remainder(m, np_answer_weights(dict(NP_ANSWER)))
    classes = [c for c in sorted(counts) for _ in range(counts[c])]
    classes = [classes[i] for i in rng.permutation(len(classes))]
    answers = [c for c in sorted(acounts) for _ in range(acounts[c])]
    answers = [answers[i] for i in rng.permutation(len(answers))]
    clk_start = int(rng.integers(1 << 18, 1 << 26)) * 4
    evs = []
    for n, (k, cls, acls) in enumerate(zip(used, classes, answers)):
        ch = int(chans[int(rng.integers(0, len(chans)))])
        ptype, body_n = np_class(cls, rng)
        a_type, a_body = np_class(acls, rng)
        evs += exchange(k, ptype, body_n, 2 * n, ch, clk_start + 4 * (k - K0), lap, uap, a_type, a_body, t_rng,
                        slave_cfo)
    info = dict(lap=lap, uap=uap, tries=tries, n_conn=m, train_slots=0, free_slots=n_slots - m,
                conn_clk_start=clk_start, mix_counts_planned=counts, answer_counts_planned=acounts,
                channels=chans, train_clke_at_slot16=None)
    return evs, info


def plan_ladder(spec, rng, t_rng, n_slots, fs, center, slave_cfo, reps=None):
    chans = sb.a_channels(fs, center)
    lap, uap, _, _, tries = draw_ident(rng, fs, center, K0, K0 + n_slots, False)
    lens = [('DH1', b) for b in range(28)] + [('DM1', b) for b in range(18)] + [('AUX1', b) for b in range(30)]
    if reps is None:
        reps = max(20, int(0.6 * n_slots) // len(lens))
    items = [it for it in lens for _ in range(reps)]
    n = min(len(items), n_slots)
    items = [items[i] for i in rng.permutation(len(items))[:n]]
    slots = sorted(int(k) for k in K0 + rng.choice(n_slots, size=n, replace=False))
    clk_start = int(rng.integers(1 << 18, 1 << 26)) * 4
    evs = []
    for i, (k, (ptype, b)) in enumerate(zip(slots, items)):
        ch = int(chans[int(rng.integers(0, len(chans)))])
        evs += exchange(k, ptype, b, 2 * i, ch, clk_start + 4 * (k - K0), lap, uap, 'NULL', 0, t_rng, slave_cfo)
    return evs, dict(lap=lap, uap=uap, tries=tries, reps=reps, n_packets=n, channels=chans, conn_clk_start=clk_start)


def plan_long(spec, rng, t_rng, n_slots, fs, center, slave_cfo):
    chans = sb.a_channels(fs, center)
    lap, uap, _, _, tries = draw_ident(rng, fs, center, K0, K0 + n_slots, False)
    clk_start = int(rng.integers(1 << 18, 1 << 26)) * 4
    base = ['DM3', 'DH3', 'DM5', 'DH5', 'NULL', 'POLL']
    seen = {t: 0 for t in base}
    off = {t: float(rng.random()) for t in base}
    evs, k, n = [], K0, 0
    while True:
        for i in rng.permutation(len(base)):
            ptype = base[i]
            occ = SLOTS[ptype] // 2 + 1
            k += int(rng.integers(0, 3))
            if k + occ >= K0 + n_slots - 1:
                return evs, dict(lap=lap, uap=uap, tries=tries, n_packets=n, channels=chans,
                                 conn_clk_start=clk_start)
            j = seen[ptype]
            seen[ptype] += 1
            if ptype in LONGEST:
                top = LONGEST[ptype]
                body_n = top if j == 0 else 0 if j == 1 else int(((j * 0.6180339887498949 + off[ptype]) % 1.0) *
                                                                 (top + 1))
            else:
                body_n = 0
            ch = int(chans[int(rng.integers(0, len(chans)))])
            evs += exchange(k, ptype, body_n, 2 * n, ch, clk_start + 4 * (k - K0), lap, uap, 'NULL', 0, t_rng,
                            slave_cfo)
            n += 1
            k += occ


# --- the file ---------------------------------------------------------------------------------------------

def plan_file(stem, seed=None, duration=None, snr_db=20.0, offset_us=None, drift_ppm=None, slave_cfo_hz=None,
              master_cfo_hz=None, ladder_reps=None):
    """The sidecar and the render jobs of a file, no samples made."""
    spec = dict(SPECS[stem])
    if seed is not None:
        spec['seed'] = seed
    seed = spec['seed']
    fs, center = spec['fs'], spec['center']
    dur = spec['duration'] if duration is None else duration
    off = Fraction(str(OFFSET_US if offset_us is None else offset_us))
    drift = 0.0 if drift_ppm is None else drift_ppm
    cfo = SLAVE_CFO_HZ if slave_cfo_hz is None else slave_cfo_hz
    mcfo = spec['master_cfo_hz'] if master_cfo_hz is None else master_cfo_hz
    n_slots = int(math.floor(dur * 1e6 / s2.TX_SLOT_US + 1e-9))
    rng = np.random.default_rng([seed, 11])
    t_rng = np.random.default_rng([seed, 17])
    if spec['kind'] == 'mix':
        evs, info = plan_mix(spec, rng, t_rng, n_slots, fs, center, cfo)
    elif spec['kind'] == 'np':
        evs, info = plan_np(spec, rng, t_rng, n_slots, fs, center, cfo)
    elif spec['kind'] == 'ladder':
        evs, info = plan_ladder(spec, rng, t_rng, n_slots, fs, center, cfo, ladder_reps)
    else:
        evs, info = plan_long(spec, rng, t_rng, n_slots, fs, center, cfo)
    ctr = centre_channel(center)
    extra = dict(lap=info['lap'], uap=info['uap'], plan=info,
                 selection_note='LAP/UAP by %d draws: LAP not reserved, UAP outside {0x00, 0xD6, 0x0B, 0xDD}%s; '
                                'seed %d' % (info['tries'], ', page sequence without the centre channel %d' % ctr
                                             if spec['kind'] == 'mix' else '', seed),
                 channels=dict(connection=sorted(int(c) for c in info['channels']),
                               window=[c for c in range(79) if s2.in_win(c, fs, center)], centre_channel=ctr),
                 slave_cfo_hz=float(cfo))
    plan = s2.finish(spec, evs, None, None, [], seed, seed, fs, center, dur, snr_db, off, drift, cfo, 0, extra,
                     master_cfo=mcfo)
    side = plan['sidecar']
    plan['fs'] = render_fs(fs)
    sps = int(round(fs / 1e6))
    counts = {}
    for ev, e in zip(evs, side['bursts']):
        t = ev['_t']
        lay = t['lay']
        end = e['start_sample'] + e['timing_frac'] + (lay['onair'] + end_extra_symbols()) * sps
        e.update(body_bytes=t['body_bytes'], info_bytes=lay['info'], payload_bits_pre_fec=lay['pre'],
                 fec_pad_bits=lay['pad'], payload_bits_air=lay['air'], onair_bits=lay['onair'], end_sample=end,
                 end_us=end / sps, crc=bool(lay['crc']), fec=lay['fec'], llid=t['llid'])
        e['tail'] = dict(variant='C1', level_db=None, dev_khz=None, dur_us=0.0, ramp_us=s2.RAMP_US)
        if e['rendered']:
            key = '%s:%d' % (e['ptype'], lay['onair'])
            counts[key] = counts.get(key, 0) + 1
    side['per_burst_keys'] = sorted(side['bursts'][0]) if side['bursts'] else []
    master = [e for e in side['bursts'] if e['rendered'] and e['sender'] == 'master']
    allr = [e for e in side['bursts'] if e['rendered']]

    def cls(e):
        if e['ptype'] in ('DH1', 'DM1') and e['body_bytes'] == 0:
            return e['ptype'] + '_0'
        if spec['kind'] != 'np' and e['ptype'] in ('DH1', 'DM1') and e['body_bytes'] == LONGEST[e['ptype']]:
            return e['ptype'] + '_full'
        return e['ptype']
    shares, shares_all = {}, {}
    for e in master:
        shares[cls(e)] = shares.get(cls(e), 0) + 1
    for e in allr:
        shares_all[cls(e)] = shares_all.get(cls(e), 0) + 1
    side.update(
        generator='SDR scripts/bt_synth_endlen.py', name='endlen_' + spec['stem'], kind='endlen_' + spec['kind'],
        onair_counts=dict(sorted(counts.items())),
        mix_target_pct=dict(MIX) if spec['kind'] == 'mix' else dict(NP_MIX) if spec['kind'] == 'np' else None,
        mix_tolerance_points=MIX_TOLERANCE if spec['kind'] in ('mix', 'np') else None,
        mix_measured_pct={k: 100.0 * v / max(len(master), 1) for k, v in sorted(shares.items())},
        mix_measured_all_bursts_pct={k: 100.0 * v / max(len(allr), 1) for k, v in sorted(shares_all.items())},
        mix_note='measured over the rendered bursts of the MASTER side (page-train IDs and master packets); slave '
                 'answers are counted apart in mix_measured_all_bursts_pct. DH1_0 / DM1_0 are empty (body 0), '
                 'DH1_full / DM1_full 27 / 17 body bytes (366 bits), DH1 / DM1 the others',
        clk_convention='native CLK[27:0] in ticks of 312.5 us: the master\'s packet at a TX slot clk % 4 == 0, '
                       'a slave answer clk + 2 x slots of the packet it answers; whitening_clk6_1 = (clk >> 1) & '
                       '0x3F; IDs carry their CLKE (page train of the LAP, A/B per 1.28 s block)',
        header_bits_meaning=sb.header_bits_meaning(side['bursts'], info['uap']),
        ids_same_lap_note='the page-train IDs of E_mix carry the same LAP as the connection, as bluey\'s real '
                          'captures do; that is not a spec-true page',
        grid_note='grid (50.25 us, drift 0) and drift are not the subject of these files; slotb has them',
        tail_notes='There is no held tail anywhere in these files (no tails): every burst ends as br.gfsk ends it, '
                   'the raised-cosine amplitude ramp-down of 2 us after its last bit.',
        end_note='end_sample / end_us = start_sample + timing_frac + onair_bits * samples_per_symbol (end_us in file '
                 'microseconds), the end of the last bit, BEFORE the 2 us ramp-down. The power half-point of the '
                 'falling edge is later: 0.6 us by a fit of the 2 us raised-cosine ramp, about 0.8 us (spread '
                 '0.15 us) by a 1 us boxcar power estimate at 20 dB; the amplitude half-point is about 1 us later. '
                 'Read it as "about 0.5 to 1.0 us after end_us", whichever estimator. id_end_sample is the same '
                 'on an ID and None on a packet.',
        onair_note='onair_bits = 72 access code + 54 header + payload_bits_air; an ID is 68, NULL and POLL 126. '
                   'Payload: payload header (1 byte DM1/DH1/AUX1, 2 bytes DM3/DH3/DM5/DH5) + body + CRC-16 '
                   '(none for AUX1), zero tail bits to a multiple of 10 and FEC 2/3 for DM*. onair_counts and '
                   'mix_measured_* count RENDERED bursts only (the burst list also holds the out-of-window '
                   'page-train IDs, so onair_counts["ID:68"] is smaller than the number of ID entries).',
        notes=['Modelled: the master\'s TX slot grid at a fixed offset, one crystal, constant 20 dB SNR, white '
               'constant noise through the whole file, random carrier phase per burst, every burst cut by '
               'br.gfsk\'s ramp (no tail).',
               'Not modelled: drift (slotb has it), fading, other transmitters, FHS or page exchanges, hopping.',
               'Out-of-window bursts are in the truth with rendered false and are not in the samples.',
               'Connection channels are drawn per exchange from the file\'s fixed in-window list (channels.connection) '
               'and are NOT the basic hopping sequence of the clock; clk is the whitening clock only (hop is None '
               'on connection bursts); the page-train IDs follow the page hop sequence of the LAP.',
               'Body bytes 17 and longer are br.seq_body counters; bodies of 1 to 12 bytes are the low counter '
               'bytes then the base64 alphabet and are exactly recoverable from payload_hex, not from a counter.',
               'AUX1 (Part B 6.5.4.7, test mode only): 1 to 30 information bytes including the 1-byte payload '
               'header, no CRC, no FEC, TYPE 1001 (Table 6.2). Its LLID is 0b10 (also for a zero body). A '
               'zero-length DH1/DM1 carries LLID 0b01 (Table 6.5, continuation fragment): the Core text gives no '
               'rule for the LLID of an empty ACL payload, so this is a choice, not a spec requirement; do not '
               'make a decoder depend on it.',
               'Multi-slot packets (E_long): the slave answer row\'s slot_start_us / slot_offset_us name the '
               'master\'s LAST slot of the packet (offset 625 us); start_us is the absolute time and is the '
               'master start + 625 us x slots + timing_offset_us.'])
    if spec['kind'] == 'np':
        sl = [e for e in allr if e['sender'] == 'slave']
        for e in side['bursts']:
            e['crc_valid'] = bool(e['crc'])         # every CRC is the true one: nothing in the file is corrupted
        npn = lambda es: 100.0 * sum(e['ptype'] in ('NULL', 'POLL') for e in es) / max(len(es), 1)  # noqa: E731
        side.update(
            answer_target_pct=dict(NP_ANSWER),
            answer_measured_pct={k: 100.0 * v / max(len(sl), 1) for k, v in sorted(
                {c: sum(cls(e) == c for e in sl) for c in NP_ANSWER}.items())},
            np_share_target_all_pct=NP_TARGET_ALL, np_share_tolerance_points=NP_TOLERANCE,
            np_share_measured_master_pct=npn(master), np_share_measured_all_pct=npn(allr),
            np_share_measured_slave_pct=npn(sl), n_records_lap=len(allr),
            mix_note='E_np: the master side is POLL and NULL 1:1 (66 % together) and 34 % DH1/DM1 payload packets, '
                     'half empty (payload header + CRC) and half with a body (DH1 1..27 bytes, DM1 1..17 bytes); '
                     'the slave answers NULL 90 % of the time, the rest DH1/DM1 in the same four classes. NULL and '
                     'POLL are then 78 % of ALL bursts of the LAP (np_share_measured_all_pct), the payload packets '
                     '22 %. mix_measured_pct is over the rendered MASTER bursts, answer_measured_pct over the '
                     'slave\'s, mix_measured_all_bursts_pct over both. There are no IDs in this file and no page '
                     'train; every burst belongs to the one connection (one LAP, one UAP).',
            ids_same_lap_note='no IDs in this file',
            crc_note='crc is whether the type carries a CRC-16; crc_valid is the same: every CRC in the file is '
                     'correct (nothing is corrupted), computed with the connection\'s UAP')
        side['notes'] = [n for n in side['notes'] if 'page-train IDs follow' not in n]
        side['notes'].append('E_np has no IDs and no page train; connection channels are drawn per exchange from the '
                             'in-window list, as in E_mix.')
        side['per_burst_keys'] = sorted(side['bursts'][0])
    if spec['kind'] not in ('mix', 'np'):
        side.pop('mix_target_pct')
        side.pop('mix_tolerance_points')
    return plan


render_blocks = sb.render_blocks


def synthesise(stem, **kw):
    plan = plan_file(stem, **kw)
    iq = np.concatenate(list(render_blocks(plan)))
    return iq, plan['sidecar']


def write_file(stem, out_dir, **kw):
    t0 = time.time()
    plan = plan_file(stem, **kw)
    os.makedirs(out_dir, exist_ok=True)
    iq_path = os.path.join(out_dir, 'synth_endlen_%s.cf32' % stem)
    side_path = os.path.join(out_dir, 'synth_endlen_%s.json' % stem)
    sc = plan['sidecar']
    print('%s planned in %.0f s: %d bursts (%d rendered), counts %s' % (stem, time.time() - t0, sc['n_bursts'],
                                                                       sc['n_rendered'], sc['counts']), flush=True)
    print('  onair_counts', sc['onair_counts'], flush=True)
    with open(iq_path, 'wb') as f:
        for out in render_blocks(plan):
            out.astype('<c8', copy=False).tofile(f)
    with open(side_path, 'w') as f:
        json.dump(sc, f, separators=(',', ':'))
    print('%s  %d samples, %.2f s, %.0f s' % (iq_path, plan['total'], plan['total'] / sc['sample_rate'],
                                              time.time() - t0), flush=True)
    print(side_path, flush=True)
    return iq_path, side_path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--set', choices=sorted(SETS))
    ap.add_argument('--stem', choices=sorted(SPECS))
    ap.add_argument('--out', default=DEFAULT_OUT)
    ap.add_argument('--offset-us', type=float)
    ap.add_argument('--drift-ppm', type=float)
    ap.add_argument('--duration', type=float)
    ap.add_argument('--seed', type=int)
    ap.add_argument('--snr-db', type=float, default=20.0)
    ap.add_argument('--slave-cfo-hz', type=float)
    ap.add_argument('--master-cfo-hz', type=float)
    a = ap.parse_args(argv)
    stems = SETS[a.set] if a.set else ([a.stem] if a.stem else [])
    if not stems:
        ap.error('give --set or --stem')
    kw = dict(snr_db=a.snr_db)
    for k, v in (('offset_us', a.offset_us), ('drift_ppm', a.drift_ppm), ('duration', a.duration), ('seed', a.seed),
                 ('slave_cfo_hz', a.slave_cfo_hz), ('master_cfo_hz', a.master_cfo_hz)):
        if v is not None:
            kw[k] = v
    for stem in stems:
        write_file(stem, a.out, **kw)


if __name__ == '__main__':
    main()
