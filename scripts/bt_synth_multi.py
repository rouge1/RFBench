#!/usr/bin/env python3
"""Captures with many Bluetooth devices, or none, for bluey-ox-walker's UAP gate.

    python scripts/bt_synth_multi.py md        # six multi-device files, sets A and B
    python scripts/bt_synth_multi.py hdr       # header-only SNR x seed sweep, 15 files
    python scripts/bt_synth_multi.py noise     # three noise-only files, 2 s each
    python scripts/bt_synth_multi.py md --out /tmp/bt

``scripts/bt_synth.py`` writes one master on one channel. bluey-ox-walker also
needs to know when its blind UAP recovery settles on the *wrong* UAP - which
happens to a device heard only a few times, on headers alone - and a capture
of one device can never show that. These are what it asked for, written with
the same encoder, modulator and conventions, into the same folders:

* **md** - six files of 6-8 masters each, in two sets that share no LAP, UAP
  or seed: A to tune bluey's gate on, B held back to test it. Each master
  has its own clock, slot timing, SNR, CFO and timing offset, and hops over
  3-5 channels of its own. Packet counts run from a handful to 80, mostly
  NULL and POLL, the rest DM1 and DH1.
* **hdr** - one master per file sending 60 NULL and POLL packets, at 6-14 dB
  and three seeds, each file a different LAP and UAP.
* **noise** - the noise alone, made by the same code at the same floor.

The hopping is not a hop sequence: each burst takes one of its master's
channels at random. A real one, from bluey's kernel, is stage 3 of the plan.

Every random choice has its own generator, so changing one does not move the
others. A file's seed spawns four: who the devices are, when and where they
transmit, each burst's carrier phase, and the noise. In the hdr sweep the
carrier phases come from a fixed seed, so a new seed changes the noise and the
device's identity and nothing else.

The noise floor is the same in every file: what makes an AMPLITUDE burst
20 dB in 1 MHz. A device's SNR then sets only its own amplitude.
[knowledge/bluey-test-signals.md](../knowledge/bluey-test-signals.md) has the
plan and the sidecar keys.
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from scripts import bt_synth  # noqa: E402

FS = 20e6
CENTER_MHZ = 2441.0
SLOT = int(FS * br.SLOT_US * 1e-6)              # 12500 samples
SPS = int(FS / br.SYMBOL_RATE)                  # 20
#: Noise power in 1 MHz, the same in every file: an AMPLITUDE burst is 20 dB.
NOISE_1MHZ = bt_synth.AMPLITUDE ** 2 / 100
#: Channels a master may use: inside the 20 MHz, a MHz clear of either edge,
#: and never 2441 MHz, the centre bin bluey's channelizer loses.
CHANNELS = [k for k in range(31, 48) if k != 39]
#: The inquiry LAPs, 0x9E8B00-0x9E8B3F, are no device's address; and the one
#: master of every earlier file stays out of these, as does its UAP.
RESERVED_LAPS = set(range(0x9E8B00, 0x9E8B40))
OLD_UAP = 0x47
#: Guard either side of a burst, in samples, when keeping two off one channel.
GUARD = 400


def identities(seed, n):
    """``n`` distinct (LAP, UAP) pairs, with no LAP or UAP repeated."""
    rng = np.random.default_rng(seed)
    uaps = [u for u in rng.permutation(256).tolist() if u != OLD_UAP][:n]
    laps = []
    while len(laps) < n:
        lap = int(rng.integers(0, 1 << 24))
        if lap not in RESERVED_LAPS and lap not in laps:
            laps.append(lap)
    return list(zip(laps, uaps))


def burst_samples(packet):
    """How long a burst is on air in samples, ramps included."""
    return len(packet.bits) * SPS + 2 * (int(np.ceil(2e-6 * FS)) + 1)


def render(path_iq, n_samples, bursts, noise_rng, chunk=1 << 22):
    """Write the capture: noise everywhere, each burst added in place.

    A burst is ``(start, packet, channel_mhz, amplitude, cfo_hz, timing_frac,
    carrier_phase)``. Its frequency term counts from sample 0 of the file, so
    a device's CFO and channel are one continuous tone across its bursts and
    only the carrier phase is the burst's own. Written in chunks, so a
    two-second file needs no more memory than a short one.
    """
    sigma = np.sqrt(NOISE_1MHZ * FS / 1e6 / 2)
    rendered = []
    for start, p, ch, amp, cfo, frac, ph in bursts:
        iq, lead = br.gfsk(p.bits, FS, delay=frac)
        n = np.arange(start - lead, start - lead + len(iq))
        shift = (ch - CENTER_MHZ) * 1e6 + cfo
        iq = amp * iq * np.exp(1j * (2 * np.pi * shift * n / FS + ph))
        rendered.append((start - lead, iq.astype(np.complex64)))
    rendered.sort(key=lambda r: r[0])
    with open(path_iq, 'wb') as f:
        for lo in range(0, n_samples, chunk):
            hi = min(lo + chunk, n_samples)
            block = (noise_rng.normal(0, sigma, hi - lo)
                     + 1j * noise_rng.normal(0, sigma, hi - lo)).astype(np.complex64)
            for s0, iq in rendered:
                a, b = max(s0, lo), min(s0 + len(iq), hi)
                if a < b:
                    block[a - lo:b - lo] += iq[a - s0:b - s0]
            block.astype('<c8').tofile(f)


def sidecar_common(n_samples):
    return {
        'generator': 'SDR scripts/bt_synth_multi.py',
        'generator_commit': bt_synth.commit(),
        'sample_rate': FS,
        'center_mhz': CENTER_MHZ,
        'n_samples': n_samples,
        'clk_convention': 'native CLK[27:0] of the burst\'s own master, at '
                          'the first sample of that burst',
        'start_sample_meaning': 'the first sample of the access code\'s '
                                'preamble, before timing_frac is added',
        'slot_samples': SLOT,
        'snr_bw_hz': 1e6,
        'noise_power_1mhz': NOISE_1MHZ,
        'modulation': {'scheme': 'GFSK', 'bt': br.GFSK_BT, 'h': br.GFSK_H,
                       'symbol_rate': br.SYMBOL_RATE},
    }


def burst_entry(start, p, ch, dev):
    entry = {'start_sample': start, 'lap': p.lap, 'uap': p.uap,
             'channel_mhz': ch, 'bt_channel': int(round(ch - 2402)),
             'snr_db': dev['snr_db'], 'cfo_hz': dev['cfo_hz'],
             'timing_frac': dev['timing_frac']}
    entry.update(p.sidecar())
    return entry


def amplitude(snr_db):
    return float(np.sqrt(NOISE_1MHZ * 10 ** (snr_db / 10)))


def packet(rng, lap, uap, clk, ptype, lt_addr, seq):
    body = b''
    if ptype in ('DM1', 'DH1'):
        body = br.seq_body(seq, br.PACKET_TYPES[ptype][4])
    return br.Packet(lap, uap, clk, ptype, body, lt_addr=lt_addr, flow=1,
                     arqn=int(rng.integers(0, 2)), seqn=seq & 1)


# --- md: many masters ---------------------------------------------------------

#: Packets per master, by how many masters a file has: a few heard only a
#: handful of times, where a false UAP lives, and one heard 80 times.
COUNTS = {6: [3, 6, 10, 15, 40, 80],
          7: [3, 6, 10, 15, 25, 40, 80],
          8: [3, 5, 6, 10, 15, 25, 40, 80]}
SNRS = [20, 18, 16, 14, 12, 10, 8, 6]
MD_SLOTS = 320                                   # 200 ms


def multi_device(seed, ids, n_slots=MD_SLOTS):
    """One md file's bursts and device list, for ``len(ids)`` masters."""
    r_who, r_when, r_phase, r_noise = [np.random.default_rng(s) for s in
                                       np.random.SeedSequence(seed).spawn(4)]
    n_dev = len(ids)
    counts = r_who.permutation(COUNTS[n_dev]).tolist()
    snrs = r_who.permutation(SNRS)[:n_dev].tolist()
    devices = []
    for (lap, uap), n, snr in zip(ids, counts, snrs):
        devices.append({
            'lap': lap, 'uap': uap, 'n_bursts': int(n), 'snr_db': float(snr),
            'cfo_hz': float(round(r_who.choice([-1, 1]) * r_who.uniform(1e3, 12e3))),
            'timing_frac': float(round(r_who.uniform(0, 1), 3)),
            'lt_addr': int(r_who.integers(1, 8)),
            'channels_mhz': sorted(2402.0 + c for c in r_who.choice(
                CHANNELS, int(r_who.integers(3, 6)), replace=False)),
            # Its slot grid's offset from sample 0, kept well inside a slot
            # so no burst sits near a boundary of bluey's 625 us grid.
            'slot_offset': int(r_who.integers(SLOT // 4, 3 * SLOT // 4)),
            'clk_at_slot0': int(r_who.integers(0, 1 << 26)) * 4,
        })
    busy = {}                                    # channel -> [(lo, hi)]
    bursts, entries = [], []
    # The busiest first, so the thin devices fit round them, not the reverse.
    for d in sorted(devices, key=lambda d: -d['n_bursts']):
        slots = [k for k in range(2, n_slots - 2, 2)]   # master slots only
        r_when.shuffle(slots)
        placed = 0
        for k in slots:
            if placed == d['n_bursts']:
                break
            ch = float(r_when.choice(d['channels_mhz']))
            r = r_when.uniform()
            ptype = 'NULL' if r < 0.40 else 'POLL' if r < 0.70 else \
                    'DM1' if r < 0.85 else 'DH1'
            clk = (d['clk_at_slot0'] + 2 * k) & 0x0FFFFFFF
            p = packet(r_when, d['lap'], d['uap'], clk, ptype, d['lt_addr'], placed)
            start = k * SLOT + d['slot_offset']
            lo, hi = start - GUARD, start + burst_samples(p) + GUARD
            if any(a < hi and lo < b for a, b in busy.get(ch, [])):
                continue
            busy.setdefault(ch, []).append((lo, hi))
            bursts.append((start, p, ch, amplitude(d['snr_db']), d['cfo_hz'],
                           d['timing_frac'], r_phase.uniform(0, 2 * np.pi)))
            entries.append(burst_entry(start, p, ch, d))
            placed += 1
        if placed < d['n_bursts']:
            raise RuntimeError("LAP %06X: only %d of %d bursts fit"
                               % (d['lap'], placed, d['n_bursts']))
    entries.sort(key=lambda e: e['start_sample'])
    return n_slots * SLOT, bursts, entries, devices, r_noise


# --- hdr: one master, headers only --------------------------------------------

HDR_SNRS = [6, 8, 10, 12, 14]
HDR_SEEDS = [1, 2, 3]
HDR_BURSTS = 60
HDR_CHANNEL = 2445.0
HDR_CFO = 12e3
HDR_FRAC = 0.37
HDR_OFFSET = -5                                  # symbol phase 5, as before


def header_only(snr, seed, lap, uap):
    """One hdr file: 60 NULL and POLL every 2 slots on one channel."""
    r_noise = np.random.default_rng(np.random.SeedSequence(seed).spawn(4)[3])
    r_phase = np.random.default_rng(12345)       # the same in every file
    r_type = np.random.default_rng(54321)        # so is the NULL/POLL pattern
    clk0 = int(np.random.default_rng(lap).integers(0, 1 << 26)) * 4
    dev = {'lap': lap, 'uap': uap, 'n_bursts': HDR_BURSTS, 'snr_db': float(snr),
           'cfo_hz': HDR_CFO, 'timing_frac': HDR_FRAC, 'lt_addr': 1,
           'channels_mhz': [HDR_CHANNEL], 'clk_at_slot0': clk0}
    first = int(bt_synth.LEAD_SLOTS * SLOT) + HDR_OFFSET
    bursts, entries = [], []
    for k in range(HDR_BURSTS):
        slot = 2 + 2 * k
        clk = (clk0 + 2 * slot) & 0x0FFFFFFF
        ptype = 'NULL' if r_type.uniform() < 0.5 else 'POLL'
        p = packet(r_type, lap, uap, clk, ptype, 1, k)
        start = first + 2 * k * SLOT
        bursts.append((start, p, HDR_CHANNEL, amplitude(snr), HDR_CFO, HDR_FRAC,
                       r_phase.uniform(0, 2 * np.pi)))
        entries.append(burst_entry(start, p, HDR_CHANNEL, dev))
    n_samples = (2 + 2 * HDR_BURSTS + 2) * SLOT
    return n_samples, bursts, entries, [dev], r_noise


# --- hard: the cases a tracker gets wrong --------------------------------------

HARD_SLOTS = 320                                 # 200 ms
#: Packet mix: plaintext, then encrypted-looking (a payload the CRC rejects).
MIX_PLAIN = [('NULL', .25), ('POLL', .25), ('DM1', .2), ('DH1', .2), ('DH3', .1)]
MIX_ENC = [('NULL', .2), ('POLL', .2), ('DM1', .2), ('DH1', .2), ('DH3', .2)]
ROLES = {6: ['leak_strong', 'leak_weak', 'thin', 'coll_x', 'coll_y', 'periodic'],
         7: ['leak_strong', 'leak_weak', 'thin', 'thin', 'coll_x', 'coll_y',
             'periodic'],
         8: ['leak_strong', 'leak_weak', 'thin', 'thin', 'coll_x', 'coll_y',
             'periodic', 'hopper']}


def _pick(rng, mix):
    r, acc = rng.uniform(), 0.0
    for name, w in mix:
        acc += w
        if r < acc:
            return name
    return mix[-1][0]


def _raw_payload(rng, ptype, uap, full=False):
    """Random bytes the length of a real payload of this type, which the CRC
    under ``uap`` does not accept: an encrypted payload, as a sniffer sees it."""
    _c, _s, _fec, two_byte, longest = br.PACKET_TYPES[ptype]
    body = longest if full else int(rng.integers(1, longest + 1))
    n = (2 if two_byte else 1) + body + 2
    while True:
        raw = rng.integers(0, 256, n, dtype=np.uint8).tobytes()
        bits = br.bytes_bits(raw)
        if br.crc16(bits[:-16], uap) != bits[-16:]:
            return raw


def _overlap(a0, a1, b0, b1):
    return max(0, min(a1, b1) - max(a0, b0)) / min(a1 - a0, b1 - b0)


def hard_cases(seed, ids, n_slots=HARD_SLOTS):
    """One file of the cases a blind tracker gets wrong, with full truth.

    Every master has a role:

    * ``leak_strong`` and ``leak_weak`` - 30-40 dB and 6-12 dB on adjacent
      channels, each on that one channel, the weak one's bursts all inside
      the strong one's, so the strong LAP leaks into the weak one's channel;
    * ``thin`` - 10-40 bursts on one channel, the first of them 6-12 dB;
    * ``coll_x`` and ``coll_y`` - hoppers sharing a channel, a few of whose
      bursts collide on it, 30-70 % of a burst;
    * ``periodic`` - a DH3 every 4 slots, hopping;
    * ``hopper`` - random slots and channels, as in the md files.

    About half the masters are encrypted-looking, whatever their role. The
    strong leaker and the second thin master are periodic, every 2 slots;
    the rest send at random. Half the masters with a grid of their own have
    it within 150 samples of half a slot, the rest anywhere well inside it.
    """
    r_who, r_when, r_phase, r_noise = [np.random.default_rng(s) for s in
                                       np.random.SeedSequence(seed).spawn(4)]
    roles = ROLES[len(ids)]
    enc = set(r_who.choice(len(ids), len(ids) // 2, replace=False).tolist())

    # Channels: the leaking pair's two are theirs alone; the collision
    # channel is shared by the colliding pair and nobody else.
    pairs = [c for c in CHANNELS if c + 1 in CHANNELS]
    c_leak = int(r_who.choice(pairs))
    c_weak = c_leak + 1 if r_who.uniform() < 0.5 else c_leak
    c_strong = c_leak if c_weak != c_leak else c_leak + 1
    free = [c for c in CHANNELS if c not in (c_strong, c_weak)]
    c_coll = int(r_who.choice(free))
    others = [c for c in free if c != c_coll]

    # Half the masters whose grid is their own sit within 150 samples of half
    # a slot, where rounding a time to its slot is least certain, and half
    # anywhere well inside one. The two followers take theirs from a leader.
    leaders = [i for i, r in enumerate(roles) if r not in ('leak_weak', 'coll_y')]
    near = set(r_who.permutation(leaders)[:(len(leaders) + 1) // 2].tolist())

    def offset(i):
        if i in near:
            return int(r_who.integers(SLOT // 2 - 150, SLOT // 2 + 151))
        return int(r_who.integers(2000, SLOT - 2000))

    devices = []
    thin_seen = 0
    for i, ((lap, uap), role) in enumerate(zip(ids, roles)):
        d = {'lap': lap, 'uap': uap, 'role': role, 'encrypted': i in enc,
             'lt_addr': int(r_who.integers(1, 8)),
             'cfo_hz': float(round(r_who.choice([-1, 1]) * r_who.uniform(1e3, 12e3))),
             'timing_frac': float(round(r_who.uniform(0, 1), 3)),
             'clk_at_slot0': int(r_who.integers(0, 1 << 26)) * 4,
             'slot_offset': offset(i), 'periodic_slots': None}
        if role == 'leak_strong':
            d.update(snr_db=float(r_who.integers(30, 41)), n_bursts=int(r_who.integers(40, 61)),
                     channels_mhz=[2402.0 + c_strong], periodic_slots=2)
        elif role == 'leak_weak':
            d.update(snr_db=float(r_who.integers(6, 13)), n_bursts=int(r_who.integers(10, 26)),
                     channels_mhz=[2402.0 + c_weak])
        elif role == 'thin':
            thin_seen += 1
            low = thin_seen == 1
            d.update(snr_db=float(r_who.integers(6, 13) if low else r_who.integers(12, 21)),
                     n_bursts=int(r_who.integers(10, 41)),
                     channels_mhz=[2402.0 + float(r_who.choice(others))],
                     periodic_slots=None if low else 2)
        elif role in ('coll_x', 'coll_y'):
            chs = r_who.choice(others, int(r_who.integers(2, 4)), replace=False)
            d.update(snr_db=float(r_who.integers(8, 21)),
                     n_bursts=int(r_who.integers(20, 41) if role == 'coll_x' else r_who.integers(15, 31)),
                     channels_mhz=sorted([2402.0 + c_coll] + [2402.0 + c for c in chs]))
        else:
            chs = r_who.choice(others, int(r_who.integers(3, 6)), replace=False)
            d.update(snr_db=float(r_who.integers(8, 21)),
                     n_bursts=int(r_who.integers(20, 31)) if role == 'periodic'
                     else int(r_who.choice([3, 6, 10, 15, 25])),
                     channels_mhz=sorted(2402.0 + c for c in chs),
                     periodic_slots=4 if role == 'periodic' else None)
        devices.append(d)
    by_role = {}
    for d in devices:
        by_role.setdefault(d['role'], d)
    strong, weak = by_role['leak_strong'], by_role['leak_weak']
    cx, cy = by_role['coll_x'], by_role['coll_y']
    # The weak leaker sits just behind the strong one in every slot, and the
    # second collider 30-70 % of a 366-bit burst behind the first.
    # Neither delay may carry past the end of a slot: the follower's burst
    # would then sit in the slot before, a slave's, on the wrong clock.
    lag = int(r_who.integers(100, 1500))
    strong['slot_offset'] = min(strong['slot_offset'], SLOT - 500 - lag)
    weak['slot_offset'] = strong['slot_offset'] + lag
    frac = float(r_who.uniform(0.3, 0.7))
    lag = int(round((1 - frac) * 366 * SPS))
    cx['slot_offset'] = min(cx['slot_offset'], SLOT - 500 - lag)
    cy['slot_offset'] = cx['slot_offset'] + lag

    busy_ch = {}                                  # channel -> [(lo, hi, burst#)]
    busy_dev = {d['lap']: set() for d in devices}
    bursts, entries = [], []

    def free_slots(d, k, slots):
        return all(j not in busy_dev[d['lap']] for j in range(k, k + slots + 1))

    def clear(ch, lo, hi, allow=()):
        return all(not (a < hi and lo < b) or n in allow
                   for a, b, n in busy_ch.get(ch, []))

    def place(d, k, ch, ptype, seq, allow=(), full=False):
        clk = (d['clk_at_slot0'] + 2 * k) & 0x0FFFFFFF
        if d['encrypted'] and ptype not in ('NULL', 'POLL'):
            p = br.Packet(d['lap'], d['uap'], clk, ptype, lt_addr=d['lt_addr'],
                          flow=1, arqn=int(r_when.integers(0, 2)), seqn=seq & 1,
                          raw_payload=_raw_payload(r_when, ptype, d['uap'], full))
        else:
            p = packet(r_when, d['lap'], d['uap'], clk, ptype, d['lt_addr'], seq)
        start = k * SLOT + d['slot_offset']
        lo, hi = start - GUARD, start + burst_samples(p) + GUARD
        if not free_slots(d, k, p.slots) or not clear(ch, lo, hi, allow):
            return None
        busy_ch.setdefault(ch, []).append((lo, hi, len(entries)))
        busy_dev[d['lap']].update(range(k, k + p.slots + 1))
        bursts.append((start, p, ch, amplitude(d['snr_db']), d['cfo_hz'],
                       d['timing_frac'], r_phase.uniform(0, 2 * np.pi)))
        e = burst_entry(start, p, ch, d)
        e.update(collision=None, leakage=None)
        entries.append(e)
        return len(entries) - 1

    def mix(d):
        return MIX_ENC if d['encrypted'] else MIX_PLAIN

    def one_slot(d):
        while True:
            t = _pick(r_when, mix(d))
            if br.PACKET_TYPES[t][1] == 1:
                return t

    last = n_slots - 6
    # 1. The strong leaker: every 2 slots from a random start, one channel.
    k0 = int(r_when.integers(1, (last - 2 * strong['n_bursts']) // 2)) * 2
    strong_idx = []
    for n in range(strong['n_bursts']):
        strong_idx.append(place(strong, k0 + 2 * n, strong['channels_mhz'][0],
                                one_slot(strong), n))
    # 2. The weak leaker, in a random subset of the strong one's slots.
    for n, j in enumerate(sorted(r_when.choice(len(strong_idx), weak['n_bursts'], replace=False))):
        se = entries[strong_idx[j]]
        k = (se['start_sample'] - strong['slot_offset']) // SLOT
        i = place(weak, k, weak['channels_mhz'][0], one_slot(weak), n)
        if i is None:
            raise RuntimeError('weak leaker would not fit')
        sp, wp = bursts[strong_idx[j]][1], bursts[i][1]
        ov = _overlap(se['start_sample'], se['start_sample'] + len(sp.bits) * SPS,
                      entries[i]['start_sample'], entries[i]['start_sample'] + len(wp.bits) * SPS)
        entries[i]['leakage'] = {'with_lap': strong['lap'], 'with_uap': strong['uap'],
                                 'with_start_sample': se['start_sample'],
                                 'channel_offset_mhz': strong['channels_mhz'][0] - weak['channels_mhz'][0],
                                 'overlap_frac': round(ov, 3)}
        se['leakage'] = dict(entries[i]['leakage'], with_lap=weak['lap'], with_uap=weak['uap'],
                             with_start_sample=entries[i]['start_sample'],
                             channel_offset_mhz=-entries[i]['leakage']['channel_offset_mhz'])
    # 3. Collisions: 5-8 slots where both colliders send a 366-bit packet on
    #    the shared channel. The second's grid is behind the first's, so the
    #    same slot index puts them 30-70 % on top of each other.
    ch = 2402.0 + c_coll
    n_coll, done = int(r_when.integers(5, 9)), 0
    for k in r_when.permutation(range(2, last, 2)).tolist():
        if done == n_coll:
            break
        tx = 'DM1' if r_when.uniform() < 0.5 else 'DH1'
        ty = 'DM1' if r_when.uniform() < 0.5 else 'DH1'
        ix = place(cx, k, ch, tx, done, full=True)
        if ix is None:
            continue
        iy = place(cy, k, ch, ty, done, allow=(ix,), full=True)
        if iy is None:                            # undo x's and try elsewhere
            busy_ch[ch].pop()
            busy_dev[cx['lap']].difference_update(range(k, k + 2))
            bursts.pop()
            entries.pop()
            continue
        a, b = entries[ix], entries[iy]
        ov = _overlap(a['start_sample'], a['start_sample'] + len(a['air_bits']) * SPS,
                      b['start_sample'], b['start_sample'] + len(b['air_bits']) * SPS)
        a['collision'] = {'with_lap': cy['lap'], 'with_uap': cy['uap'],
                          'with_start_sample': b['start_sample'], 'overlap_frac': round(ov, 3)}
        b['collision'] = {'with_lap': cx['lap'], 'with_uap': cx['uap'],
                          'with_start_sample': a['start_sample'], 'overlap_frac': round(ov, 3)}
        done += 1
    if done < n_coll:
        raise RuntimeError('only %d of %d collisions fit' % (done, n_coll))
    # 4. The periodic ones that are left: every 2 or 4 slots, a channel each
    #    time from their own set.
    for d in devices:
        if d['periodic_slots'] and d['role'] != 'leak_strong':
            P, n = d['periodic_slots'], d['n_bursts']
            for _ in range(200):
                k0 = int(r_when.integers(1, (last - P * n) // 2)) * 2
                ok = True
                for m in range(n):
                    t = 'DH3' if d['role'] == 'periodic' else one_slot(d)
                    chs = r_when.permutation(d['channels_mhz']).tolist()
                    if not any(place(d, k0 + P * m, c, t, m) is not None for c in chs):
                        ok = False
                        break
                if ok:
                    break
                # Take this attempt back and try another start.
                keep = [i for i, e in enumerate(entries) if e['lap'] != d['lap']]
                bursts[:] = [bursts[i] for i in keep]
                entries[:] = [entries[i] for i in keep]
                busy_dev[d['lap']].clear()
                for c in busy_ch:
                    busy_ch[c] = [(a, b, n_) for a, b, n_ in busy_ch[c]
                                  if n_ < len(entries)]
            else:
                raise RuntimeError('periodic LAP %06X would not fit' % d['lap'])
    # 5. Everyone else's remaining bursts at random slots.
    for d in devices:
        have = sum(e['lap'] == d['lap'] for e in entries)
        for k in r_when.permutation(range(2, last, 2)).tolist():
            if have >= d['n_bursts']:
                break
            if place(d, k, float(r_when.choice(d['channels_mhz'])),
                     _pick(r_when, mix(d)), have) is not None:
                have += 1
        if have < d['n_bursts']:
            raise RuntimeError('LAP %06X: %d of %d bursts' % (d['lap'], have, d['n_bursts']))
    order = sorted(range(len(entries)), key=lambda i: entries[i]['start_sample'])
    return (n_slots * SLOT, [bursts[i] for i in order], [entries[i] for i in order],
            devices, r_noise)


# --- writing ------------------------------------------------------------------


def write(out_iq, out_side, stem, n_samples, bursts, entries, devices,
          r_noise, extra):
    iq_path = os.path.join(out_iq, 'synth_%s.cf32' % stem)
    side_path = os.path.join(out_side, 'synth_%s.json' % stem)
    render(iq_path, n_samples, bursts, r_noise)
    side = sidecar_common(n_samples)
    side.update(extra)
    side['devices'] = devices
    side['bursts'] = entries
    with open(side_path, 'w') as f:
        json.dump(side, f, indent=1)
    print('%s  %.1f ms, %d devices, %d bursts' % (
        iq_path, n_samples / FS * 1e3, len(devices), len(entries)), flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('what', choices=['md', 'hdr', 'noise', 'hard'])
    ap.add_argument('--out', help='one folder for both files, instead of '
                    "bluey-ox-walker's data/iq and data/sidecar")
    args = ap.parse_args()
    if args.out:
        out_iq = out_side = args.out
    else:
        out_iq = os.path.join(bt_synth.BLUEY, 'data', 'iq')
        out_side = os.path.join(bt_synth.BLUEY, 'data', 'sidecar')
    os.makedirs(out_iq, exist_ok=True)
    os.makedirs(out_side, exist_ok=True)

    # One pool of identities for both sets and the sweep, so none repeats.
    md_sizes = {'a1': 7, 'a2': 6, 'a3': 8, 'b1': 7, 'b2': 8, 'b3': 6}
    pool = identities(2026_10_03, sum(md_sizes.values()) + len(HDR_SNRS) * len(HDR_SEEDS))
    if args.what == 'md':
        seeds = {'a1': 101, 'a2': 102, 'a3': 103, 'b1': 201, 'b2': 202, 'b3': 203}
        i = 0
        for name, n in md_sizes.items():
            ids, i = pool[i:i + n], i + n
            write(out_iq, out_side, 'md_' + name, *multi_device(seeds[name], ids),
                  {'set': 'A (tuning)' if name[0] == 'a' else 'B (hold-out)',
                   'seed': seeds[name]})
    elif args.what == 'hdr':
        i = sum(md_sizes.values())
        for snr in HDR_SNRS:
            for seed in HDR_SEEDS:
                lap, uap = pool[i]
                i += 1
                write(out_iq, out_side, 'hdr_snr%02d_s%d' % (snr, seed),
                      *header_only(snr, seed, lap, uap),
                      {'seed': seed, 'snr_db': float(snr), 'lap': lap, 'uap': uap,
                       'symbol_phase': (int(bt_synth.LEAD_SLOTS * SLOT) + HDR_OFFSET) % SPS})
    elif args.what == 'hard':
        # New identities, past every one the md and hdr files used.
        used = len(pool)
        sizes = {'a1': 6, 'a2': 7, 'a3': 8, 'b1': 7, 'b2': 8}
        seeds = {'a1': 301, 'a2': 302, 'a3': 303, 'b1': 401, 'b2': 402}
        more = identities(2026_10_03, used + sum(sizes.values()))[used:]
        i = 0
        for name, n in sizes.items():
            ids, i = more[i:i + n], i + n
            write(out_iq, out_side, 'hard_' + name, *hard_cases(seeds[name], ids),
                  {'set': 'A (tuning)' if name[0] == 'a' else 'B (hold-out)',
                   'seed': seeds[name]})
    else:
        # Seeds of their own: seed 1's noise is already the noise of every
        # hdr file with seed 1, and a noise-only reference that shared it
        # would not be independent of the sweep.
        for n in (1, 2, 3):
            seed = 1000 + n
            r_noise = np.random.default_rng(np.random.SeedSequence(seed).spawn(4)[3])
            write(out_iq, out_side, 'noise_s%d' % n, int(2.0 * FS), [], [], [],
                  r_noise, {'seed': seed, 'note': 'noise only: no packets'})


if __name__ == '__main__':
    main()
