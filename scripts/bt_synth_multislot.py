#!/usr/bin/env python3
"""SET-N: busy multi-slot piconets in both directions, one of them without AFH, for bluey-ox-walker's part B2.

    python scripts/bt_synth_multislot.py --set setn [--out DIR]
    python scripts/bt_synth_multislot.py setn_a --out DIR

    from scripts import bt_synth_multislot as ms
    side, jobs, total = ms.plan_setn(seed=9701, blocks=3)

bluey's part B2 grades a one-slot-back scan that recovers a master packet from the slave answer after it: does
a slot-parity filter separate the rows that are really slave bursts, and does a dedupe fix keep its originals.
Its graded sets (``endlen``, ``mixed``) have no slave multi-slot answers, no link without AFH, and a channel design
(endlen's five fixed channels) that inflates the wrong-role rows. SET-N is the held-out set; its parameters were
fixed by bluey and the owner before bluey's gates were read here, and the free choices below were sent to bluey
as a design note before this was built. Nothing is transmitted.

**What it reuses.** ``scripts/bt_synth_mixed.py`` unchanged: its identities, packets (``make_packet``, LMP in DM1),
joiners (``plan_joiner``: page exchanges on the specification's sequences), overlap truth (``compute_overlaps``)
and renderer (``render_blocks``). This file plans the piconets' traffic itself and assembles the sidecar; nothing
in ``mixed`` or ``interf`` changes, so no delivered file of theirs can.

**The devices** (per file, drawn from the file's seed):

* ``N_PICONETS`` = 6 piconets, each a master and one slave, own LAP/UAP/NAP (sync words at least 22 bits apart,
  as in mixed), own clock, own slot grid (offsets at least 800 samples apart), level uniform 8-22 dB with a
  uniform +-1 dB jitter per burst; 2 of the 6 are not ``ours`` (a policy label, as in mixed);
* **5 piconets on AFH**: the adapted sequence over the 20-channel map 31-50 (2433-2452 MHz; 20 is the legal
  minimum, Core v6.0 Vol 2 Part B 2.3.1 p.475), same channel mechanism (2.6.1, p.485): the slave answers on the
  master's channel;
* **1 piconet without AFH** (``afh`` false, drawn from the seed): the basic sequence over 79 channels,
  ``bt_hop.hop_channel(clk, address)`` with no map (the kernel ``scripts/test_bt_hop.py`` checks against Part G).
  A packet's channel is that of the clock of its first slot, so the slave's answer to an L-slot packet is on
  f(clock of slot s + L), not the master's (2.6.1, p.484, Fig 2.14; 8.6.7, p.580);
* ``N_JOINERS`` = 3 page exchanges, as in mixed (paged device's IDs and FHS on the pager's clock, then
  follow-up on the basic sequence).

**The traffic.** A master transmits in a free master slot with probability ``p_master_tx`` (uniform 0.80-0.95 per
AFH piconet, 0.5 for the non-AFH one). Its packet has L = 1, 3, 5 slots at 40 / 30 / 30 % (L=1 NULL, POLL, DM1,
DH1; L=3 DM3, DH3; L=5 DM5, DH5, uniform within L). The slave answers with probability ``p_slave_answer``
(uniform 0.88-0.97) in slot s + L with L' = 1, 3, 5 at 40 / 30 / 30 % (L'=1 NULL, DM1, DH1; L'=3 DM3, DH3; L'=5
DM5, DH5). The master's next free slot is s + L + L' (s + L + 1 without an answer): even, since L and L' are
odd. The busy masters are the sizing bluey asked for: about 100 wrong-role opportunities per 40 MS/s file (a
one-slot slave answer followed at once by the next master packet on the same channel; ``opportunities`` in the
sidecar counts them from the plan).

**The files.** ``setn_a`` and ``setn_b``: 40 MS/s on 2441.0 MHz, seeds 9701 and 9702. ``setn_c``: seed 9703,
planned and rendered at 40 MS/s, then low-pass filtered (``DECIM_TAPS`` Kaiser taps, flat to about 8.6 MHz) and
decimated by 2 to **20 MS/s** (output sample n is input sample 2n: the FIR is odd and centred); bursts whose
channel is not 31-47 (2433-2449 MHz, +-8 MHz) are not rendered at all, and the truth's sample keys are halved
(``decimation`` in the sidecar). ``setn_d``: ``setn_a``'s plan, noise and bursts plus an interferer: bluey's
``interf`` set's bursty Wi-Fi-like noise (frames 0.3-3 ms at 30 % duty, 5 us edges, +20 dB over the floor per
MHz while on) moved from +11..+20 MHz to **+2..+11 MHz** (2443-2452 MHz, channels 41-50: half the map), from its
own seed; where no frame is on, ``setn_d`` equals ``setn_a`` sample for sample. Each file is 96 noise blocks of
4,194,304 samples at 40 MS/s: 402,653,184 samples, 10.07 s.

**Truth per burst**, beside mixed's keys: ``exchange_id`` (unique in the file: device * 100000 + the piconet's
own pair count, or + 0 for a joiner's one exchange; ``pair`` is kept and restarts at 0 per device), ``sender``
(``master`` / ``slave``; ``role`` for a piconet burst, the exchange role for a joiner burst), ``afh`` (the
device's), ``slots``, ``in_window`` / ``rendered`` (false for a burst outside the file's band: it stays in
``bursts_not_rendered`` with its truth, out of every overlap), and in ``setn_d`` ``interferer_overlap`` and
``interferer_power_in_band_db`` (as the interf set defines them).
"""
import argparse
import hashlib
import itertools
import json
import math
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from apps import bt_hop  # noqa: E402
from scripts import bt_synth  # noqa: E402
from scripts import bt_synth_interf as itf  # noqa: E402
from scripts import bt_synth_mixed as mx  # noqa: E402

FS = mx.FS
SPS = mx.SPS
SLOT = mx.SLOT
GRID_BASE = mx.GRID_BASE
CENTER_MHZ = mx.CENTER_MHZ
MAP = mx.MAP
BLOCK_SAMPLES = mx.BLOCK_SAMPLES
BLOCKS = 96
N_PICONETS = 6
N_NOT_OURS = 2
N_JOINERS = mx.N_JOINERS
P_MASTER_TX_AFH = (0.80, 0.95)
P_MASTER_TX_BASIC = 0.5
P_SLAVE_ANSWER = (0.88, 0.97)
SLOT_SHARES = ((1, 0.4), (3, 0.3), (5, 0.3))
MASTER_BY_SLOTS = {1: ('NULL', 'POLL', 'DM1', 'DH1'), 3: ('DM3', 'DH3'), 5: ('DM5', 'DH5')}
SLAVE_BY_SLOTS = {1: ('NULL', 'DM1', 'DH1'), 3: ('DM3', 'DH3'), 5: ('DM5', 'DH5')}
LEVEL_RANGE = mx.LEVEL_RANGE
WINDOW_40 = tuple(range(mx.WINDOW[0], mx.WINDOW[1] + 1))          # channels 27-51 at 40 MS/s, mixed's gate
BAND_20 = tuple(range(31, 48))                                     # 2433-2449 MHz, +-8 MHz of 2441 at 20 MS/s
EXCHANGE_BASE = 100000
DECIM_TAPS = 161
DECIM_CUTOFF_MHZ = 9.6
DECIM_BETA = 8.6
INTERF_BAND_MHZ = (2.0, 11.0)
INTERF_LEVEL_DB = 20.0
INTERF_SEED_OFFSET = 50
DEFAULT_OUT = '/media/user/4TB/sdr-synth-tmp/multislot'

FILES = {'setn_a': dict(seed=9701, fs=40e6, interferer=False),
         'setn_b': dict(seed=9702, fs=40e6, interferer=False),
         'setn_c': dict(seed=9703, fs=20e6, interferer=False),
         'setn_d': dict(seed=9701, fs=40e6, interferer=True)}
SETS = {'setn': list(FILES)}


# --- one piconet's traffic -------------------------------------------------------------------

def draw_slots(rng):
    u = rng.random()
    acc = 0.0
    for n, p in SLOT_SHARES:
        acc += p
        if u < acc:
            return n
    return SLOT_SHARES[-1][0]


def channel_fn(dev):
    """The device's hop: the adapted sequence over MAP (AFH) or the basic sequence over 79 channels."""
    if dev['afh']:
        return mx.hop.afh_hop_fn([(dev['clk0'] >> 1, MAP)], dev['lap'], dev['uap'])
    address = dev['uap'] << 24 | dev['lap']
    return lambda clk: bt_hop.hop_channel(clk, address)


def plan_piconet(dev, seed, total):
    """Every burst of one piconet, before the samples: the pairs in time order."""
    d = dev['id']
    rng = np.random.default_rng([seed, 100 + d])
    hop_fn = channel_fn(dev)
    tr = dev['traffic']
    s_last = (total - GRID_BASE - dev['grid_offset_int'] - 12 * SLOT) // SLOT
    out = []
    s, k_m, k_s = 0, 0, 0
    while s + 10 <= s_last:
        if rng.random() >= tr['p_master_tx']:
            s += 2
            continue
        n = draw_slots(rng)
        ptype = MASTER_BY_SLOTS[n][int(rng.integers(0, len(MASTER_BY_SLOTS[n])))]
        clk_m = (dev['clk0'] + 2 * s) & 0x0FFFFFFF
        ch_m = int(hop_fn(clk_m))
        answered = rng.random() < tr['p_slave_answer']
        n_s = draw_slots(rng) if answered else None
        ptype_s = SLAVE_BY_SLOTS[n_s][int(rng.integers(0, len(SLAVE_BY_SLOTS[n_s])))] if answered else None
        phase = (0, SPS // 2)[k_m % 2]
        pair = []
        members = [('master', s, clk_m, k_m, ptype, ch_m, clk_m)]
        if answered:
            clk_s = (clk_m + 2 * n) & 0x0FFFFFFF
            # AFH: the same channel mechanism; basic: the channel of the answer's own first slot
            ch_s, hop_clk_s = (ch_m, clk_m) if dev['afh'] else (int(hop_fn(clk_s)), clk_s)
            members.append(('slave', s + n, clk_s, k_s, ptype_s, ch_s, hop_clk_s))
        for role, slot, clk, counter, pt, ch, hop_clk in members:
            b_rng = np.random.default_rng([seed, 300 + d, 2 * counter + (role == 'slave')])
            p, ex = mx.make_packet(dev, pt, clk, counter, b_rng, True, role, [seed, 700 + d, k_s])
            jitter = float(b_rng.uniform(-mx.JITTER_DB, mx.JITTER_DB))
            pair.append(dict(
                device=d, role=role, ptype=pt, packet=p, slot=slot, clk=clk, hop_clk=hop_clk, channel=ch,
                slots=br.PACKET_TYPES[pt][1], timing_frac=float(b_rng.uniform(0, 1)),
                burst_phase=float(b_rng.uniform(0, 2 * np.pi)), snr_db=round(dev['level_db'] + jitter, 2),
                jitter_db=round(jitter, 4), pair=k_m, symbol_phase_target=phase, **ex))
        if answered:
            k_s += 1
        s0 = GRID_BASE + dev['grid_offset_int'] + SLOT * s
        shift = (phase - s0) % SPS
        for b in pair:
            b['start'] = GRID_BASE + dev['grid_offset_int'] + SLOT * b['slot'] + shift
            b['boundary'] = GRID_BASE + dev['grid_offset_samples'] + SLOT * b['slot']
        out.extend(pair)
        k_m += 1
        s += n + (n_s if answered else 1)
    return out


# --- the plan --------------------------------------------------------------------------------

def count_opportunities(entries, not_rendered, band):
    """Wrong-role opportunities, from plan indices: a rendered one-slot slave burst, then the same piconet's next
    master starting at its piconet_slot + 1 on the same channel, also rendered."""
    by_slot = {}
    for e in entries:
        if e.get('kind') == 'traffic' and e['role'] == 'master':
            by_slot[(e['device'], e['piconet_slot'])] = e
    out = dict(afh=0, afh_collision_free=0, basic=0, basic_collision_free=0)
    for e in entries:
        if e.get('kind') != 'traffic' or e['role'] != 'slave' or e['slots'] != 1:
            continue
        m = by_slot.get((e['device'], e['piconet_slot'] + 1))
        if m is None or m['channel'] != e['channel']:
            continue
        key = 'afh' if e['afh'] else 'basic'
        out[key] += 1
        if not e['collision'] and not m['collision']:
            out[key + '_collision_free'] += 1
    return out


def plan_setn(seed=9701, blocks=BLOCKS, block_samples=BLOCK_SAMPLES, level_range=LEVEL_RANGE, band=WINDOW_40,
              name=None):
    """The sidecar (40 MS/s sample positions) and the render jobs of a file: ``(sidecar, jobs, total)``. ``band`` is
    the set of channels a burst is rendered on (``WINDOW_40``, or ``BAND_20`` for the 20 MS/s file)."""
    fs = FS
    total = blocks * block_samples
    if total < 12 * SLOT + 3 * 3 * 12 * SLOT:
        raise ValueError("%d samples are too few for the traffic and three exchanges" % total)
    band = set(band)
    rng = np.random.default_rng([seed, 1 << 20])
    n_ids = N_PICONETS + 2 * N_JOINERS
    laps, syncs = mx.draw_identities(rng, n_ids)
    offsets = mx.draw_grid_offsets(rng, N_PICONETS)
    not_ours = set(int(i) for i in rng.choice(N_PICONETS, size=N_NOT_OURS, replace=False))
    basic_id = int(rng.integers(0, N_PICONETS))
    devices = []
    for d in range(N_PICONETS):
        afh = d != basic_id
        level = round(float(rng.uniform(*level_range)), 1)
        frac = float(rng.uniform(0, 1))
        p_tx = round(float(rng.uniform(*P_MASTER_TX_AFH)), 3) if afh else P_MASTER_TX_BASIC
        p_ans = round(float(rng.uniform(*P_SLAVE_ANSWER)), 3)
        devices.append(dict(
            id=d, kind='piconet', ours=d not in not_ours, afh=afh, lap=laps[d], uap=int(rng.integers(0, 256)),
            nap=int(rng.integers(0, 1 << 16)), clk0=4 * int(rng.integers(0, (1 << 26) - (1 << 17))),
            grid_offset_int=offsets[d], grid_offset_frac=frac, grid_offset_samples=offsets[d] + frac, level_db=level,
            traffic=dict(p_master_tx=p_tx, p_slave_answer=p_ans, slot_shares={str(n): p for n, p in SLOT_SHARES},
                         master_types={str(k): list(v) for k, v in MASTER_BY_SLOTS.items()},
                         slave_types={str(k): list(v) for k, v in SLAVE_BY_SLOTS.items()},
                         dm1_lmp_fraction=mx.DM1_LMP_FRACTION)))
    joiners = []
    for j in range(N_JOINERS):
        joiners.append(dict(
            id=N_PICONETS + j, kind='joiner', ours=True, lap=laps[N_PICONETS + 2 * j], uap=int(rng.integers(0, 256)),
            nap=int(rng.integers(0, 1 << 16)), cod=int(rng.choice([0x5A020C, 0x200404, 0x240408, 0x7A020C])),
            paged_lap=laps[N_PICONETS + 2 * j + 1], paged_uap=int(rng.integers(0, 256)),
            conf_seed=int(rng.integers(1, 1 << 30))))

    recs = []
    for dev in devices:
        for b in plan_piconet(dev, seed, total):
            recs.append((dev, b))
    third = total // N_JOINERS
    j_infos, j_entries = [], []
    for j, jd in enumerate(joiners):
        lo = j * third + SLOT
        room = third - 2 * SLOT - 2_400_000
        if room < 0:
            raise ValueError("a third of the file is too short for an exchange")
        target = SPS * int((lo + rng.uniform(0, 1) * room) // SPS)
        info, entries, last = mx.plan_joiner(j, seed, jd, target)
        j_infos.append(info)
        j_entries.append(entries)
        if last > total - SLOT:
            raise ValueError("exchange %d ends at %d, past the file" % (j, last))

    flat, not_rendered = [], []
    for dev, b in recs:
        p = b['packet']
        entry = {'start_sample': int(b['start']), 'timing_frac': b['timing_frac']}
        entry.update(p.sidecar())
        inside = b['channel'] in band
        entry.update(
            air_bits=mx.bits_hex(p.bits), air_bits_length=len(p.bits), lap=dev['lap'], uap=dev['uap'],
            llid=b['llid'], payload_length=len(b['body']),
            lmp_opcode=None if b['lmp'] is None else mx.bt_lmp.PDUS[b['lmp']['name']]['opcode'],
            lmp_name=None if b['lmp'] is None else b['lmp']['name'],
            lmp_tid=None if b['lmp'] is None else b['lmp']['tid'],
            lmp_params=None if b['lmp'] is None else {
                key: (v.hex() if isinstance(v, bytes) else v) for key, v in b['lmp']['params'].items()},
            slots=b['slots'], end_sample=int(b['start'] + len(p.bits) * SPS), channel=b['channel'],
            channel_mhz=mx.hop.channel_mhz(b['channel']), symbol_phase=int(b['start'] % SPS), snr_db=b['snr_db'],
            jitter_db=b['jitter_db'], burst_phase=b['burst_phase'], device=dev['id'], role=b['role'],
            sender=b['role'], ours=dev['ours'], afh=dev['afh'], kind='traffic', piconet_slot=b['slot'], pair=b['pair'],
            exchange_id=dev['id'] * EXCHANGE_BASE + b['pair'], hop_clk=b['hop_clk'], in_window=inside,
            rendered=inside, slot_boundary_sample=b['boundary'])
        if p.ptype in ('NULL', 'POLL'):
            entry.update(body_crc_bits_hex=None, body_crc_bits_length=None)
        else:
            pb = br.bytes_bits(p.payload_full)
            entry.update(body_crc_bits_hex=mx.bits_hex(pb), body_crc_bits_length=len(pb))
        if inside:
            job = dict(packet=mx.Bits(np.array(p.bits, dtype=np.uint8)), start=int(b['start']), frac=b['timing_frac'],
                       phase=b['burst_phase'], amp=itf.amplitude_for(b['snr_db']), channel=b['channel'])
            flat.append((entry, job))
        else:
            entry['burst_phase'] = None
            not_rendered.append(entry)
    for jd, entries in zip(joiners, j_entries):
        for e in entries:
            inside = e['rendered'] and e['channel'] in band
            entry = mx.joiner_entry(e, jd, e['_bits'] if e['rendered'] else None)
            entry.update(sender=e['role'], afh=False, exchange_id=jd['id'] * EXCHANGE_BASE + 0)
            if inside:
                job = dict(e['_job'])
                job['amp'] = itf.amplitude_for(entry['snr_db'])
                entry['burst_phase'] = job['phase']
                flat.append((entry, job))
            else:
                if e['rendered']:                      # inside mixed's gate, outside this file's band
                    entry['rendered'] = False
                    entry['in_window'] = False
                    entry['outside_band'] = True
                entry['burst_phase'] = None
                not_rendered.append(entry)
    flat.sort(key=lambda t: (t[0]['start_sample'] + t[0]['timing_frac'], t[0]['device'], t[0]['channel']))
    entries = [e for e, _ in flat]
    jobs = [j for _, j in flat]
    starts = [e['start_sample'] + e['timing_frac'] for e in entries]
    ends = [s + e['air_bits_length'] * SPS for s, e in zip(starts, entries)]
    if max(ends) + 200 > total:
        raise ValueError("a burst ends at %g, past the end of the file at %d" % (max(ends), total))
    overlaps, collision, frac = mx.compute_overlaps(starts, ends, [e['channel'] for e in entries],
                                                    [e['snr_db'] for e in entries], [e['device'] for e in entries])
    for i, e in enumerate(entries):
        e['index'] = i
        e['overlaps'], e['collision'], e['overlap_frac'] = overlaps[i], collision[i], frac[i]
    for e in not_rendered:
        e['overlaps'], e['collision'], e['overlap_frac'] = [], False, 0.0
    not_rendered.sort(key=lambda e: (e['start_sample'] + e['timing_frac'], e['device'], e['channel']))

    sync_words = {lap: mx.sync_int(lap) for lap in laps}
    dev_rows = []
    for dev in devices:
        others = [t for lap, t in sync_words.items() if lap != dev['lap']]
        hop_map = (dict(kind='adapted', channels=MAP, used_channels_mask_hex='%x' % mx.MASK,
                        address_for_hop=dev['uap'] << 24 | dev['lap'], instant_clk27_1=dev['clk0'] >> 1,
                        note='bt_hop.hop_channel(clk, address_for_hop, mask), the same channel mechanism')
                   if dev['afh'] else
                   dict(kind='basic', channels=None, used_channels_mask_hex=None,
                        address_for_hop=dev['uap'] << 24 | dev['lap'],
                        note='bt_hop.hop_channel(clk, address_for_hop) with no map: the basic sequence over 79 channels; '
                             'a packet is on the channel of the clock of its first slot, so the answer to an L-slot packet '
                             'is on f(clock of slot s + L) (Core v6.0 Vol 2 Part B 2.6.1 p.484, 8.6.7 p.580)'))
        dev_rows.append(dict(
            id=dev['id'], kind='piconet', ours=dev['ours'], afh=dev['afh'], lap=dev['lap'], uap=dev['uap'],
            nap=dev['nap'], clk0=dev['clk0'], slot_grid_offset_samples=dev['grid_offset_samples'],
            slot_grid_offset_int=dev['grid_offset_int'], slot_grid_offset_frac=dev['grid_offset_frac'],
            level_db=dev['level_db'], jitter_db=mx.JITTER_DB, hop_map=hop_map, traffic=dev['traffic'],
            sync_word_hex='%016x' % sync_words[dev['lap']],
            sync_word_min_distance=min(mx.hamming(sync_words[dev['lap']], t) for t in others)))
    for jd, info in zip(joiners, j_infos):
        others = [t for lap, t in sync_words.items() if lap != jd['lap']]
        dev_rows.append(dict(
            id=jd['id'], kind='joiner', ours=True, afh=False, lap=jd['lap'], uap=jd['uap'], nap=jd['nap'], cod=jd['cod'],
            paged=dict(lap=jd['paged_lap'], uap=jd['paged_uap'], sync_word_hex='%016x' % sync_words[jd['paged_lap']]),
            clk0=info['master_clkn0'], slot_grid_offset_samples=info['origin_sample'] % SLOT,
            level_db=info['snr_base_db'], jitter_db='N(0, 1) clipped to +-3 per burst (bt_synth_conf)',
            hop_map=dict(kind='specification', channels=None, used_channels_mask_hex=None,
                         address_for_hop=jd['uap'] << 24 | jd['lap'],
                         note='page, page response and Central page response sequences, then the basic connection sequence'),
            exchange=info, sync_word_hex='%016x' % sync_words[jd['lap']],
            sync_word_min_distance=min(mx.hamming(sync_words[jd['lap']], t) for t in others)))
    all_s = list(sync_words.values())
    pic_s = [sync_words[dev['lap']] for dev in devices]
    n = len(entries)
    n_coll = sum(collision)
    per_type = {}
    for e in entries:
        per_type[e['ptype']] = per_type.get(e['ptype'], 0) + 1
    exchanges = {}
    for e in entries + not_rendered:
        if e.get('kind') == 'traffic':
            exchanges.setdefault(e['exchange_id'], []).append(e['rendered'])
    one_rendered = sum(1 for v in exchanges.values() if len(v) == 2 and sum(v) == 1)
    basic = [d for d in devices if not d['afh']][0]
    opp = count_opportunities(entries, not_rendered, band)
    sidecar = {
        'generator': 'SDR scripts/bt_synth_multislot.py',
        'generator_commit': bt_synth.commit(),
        'name': name, 'set': 'setn', 'seed': seed,
        'lap': None, 'uap': None, 'clk': None, 'ptype': None, 'channel': None, 'channel_mhz': None, 'snr_db': None,
        'timing_frac': None, 'symbol_phase': None,
        'hopping': True, 'hop_channels': sorted({e['channel'] for e in entries}),
        'sample_rate': fs, 'center_mhz': CENTER_MHZ, 'cfo_hz': 0.0,
        'n_samples': int(total),
        'n_samples_meaning': 'the number of complex samples in the file; its size is 8 * n_samples bytes',
        'n_noise_blocks': blocks, 'noise_block_samples': block_samples,
        'snr_bw_hz': 1e6, 'noise_1mhz': mx.NOISE_1MHZ,
        'modulation': {'scheme': 'GFSK', 'bt': br.GFSK_BT, 'h': br.GFSK_H, 'symbol_rate': br.SYMBOL_RATE},
        'slot_samples': SLOT, 'grid_base_samples': GRID_BASE, 'symbol_phases': [0, SPS // 2],
        'start_sample_meaning': 'the first sample of the access code\'s preamble, before timing_frac is added',
        'burst_window': mx.WINDOW_TEXT,
        'band_channels': sorted(band),
        'band_note': 'a burst is rendered iff its channel is in band_channels; the others are in bursts_not_rendered '
                     'with their truth (rendered false), in no overlap and in no sample',
        'devices': dev_rows, 'n_devices': len(dev_rows), 'n_piconets': N_PICONETS, 'n_joiners': N_JOINERS,
        'afh_devices': [d['id'] for d in devices if d['afh']], 'non_afh_device': basic['id'],
        'non_afh_burst_indices': [e['index'] for e in entries if e['device'] == basic['id']],
        'ours_devices': [r['id'] for r in dev_rows if r['ours']],
        'not_ours_devices': [r['id'] for r in dev_rows if not r['ours']],
        'level_range_db': list(level_range), 'jitter_db': mx.JITTER_DB,
        'sync_word_min_distance_piconets': min(mx.hamming(a, b) for i, a in enumerate(pic_s) for b in pic_s[i + 1:]),
        'sync_word_min_distance_all': min(mx.hamming(a, b) for i, a in enumerate(all_s) for b in all_s[i + 1:]),
        'afh_map': MAP, 'hop_kernel': mx.hop.HOP_KERNEL,
        'traffic_note': 'L = 1/3/5 at 40/30/30 %% for the master and for the answer; p_master_tx %s (AFH) and %s (non-AFH); '
                        'p_slave_answer %s; the next master slot is s + L + L\' (s + L + 1 without an answer)'
                        % (list(P_MASTER_TX_AFH), P_MASTER_TX_BASIC, list(P_SLAVE_ANSWER)),
        'channel_note': 'AFH piconets: the slave answers on the master\'s channel (same channel mechanism). The non-AFH '
                        'piconet: the master on f(clk of slot s), the answer on f(clk of slot s + L), its own first slot',
        'exchange_id_note': 'exchange_id = device * %d + pair for a piconet (pair counts the piconet\'s master packets from '
                            '0), device * %d + 0 for a joiner\'s one exchange: unique in the file; a master packet and its '
                            'answer share it' % (EXCHANGE_BASE, EXCHANGE_BASE),
        'sender_note': 'master or slave: the role for a piconet burst, the exchange role for a joiner burst',
        'opportunities': opp,
        'opportunities_note': 'wrong-role opportunities from the plan: a rendered one-slot slave burst followed by the same '
                              'piconet\'s next master packet at piconet_slot + 1 on the same channel, also rendered; '
                              '_collision_free: neither of the two collided',
        'n_exchanges_one_member_rendered': one_rendered,
        'n_bursts': n, 'n_bursts_not_rendered': len(not_rendered),
        'counts_per_type': dict(sorted(per_type.items())),
        'n_collisions': n_coll, 'fraction_collided': n_coll / n,
        'indexing': 'bursts is sorted by start_sample + timing_frac; bursts[i].index == i; overlaps[].burst is an index into bursts',
        'notes': [
            'An answer key: a receiver under test must not be given it.',
            'Nothing here is real traffic: random packet types at a set rate; LMP PDUs in about half of the DM1 (mixed\'s rule); '
            'SEQN/ARQN plausible, not a protocol run; no retransmission, power control, SCO/eSCO, sniff, drift or role switch.',
            'The piconets\' clocks do not drift; a piconet slot is exactly 25000 samples at 40 MS/s.',
        ],
        'bursts': entries,
        'bursts_not_rendered': not_rendered,
    }
    return sidecar, jobs, total


# --- 20 MS/s ----------------------------------------------------------------------------------

def decim_taps():
    """The low-pass FIR of the decimation by 2: odd length, centred, DC gain 1."""
    m = np.arange(DECIM_TAPS) - (DECIM_TAPS - 1) // 2
    h = np.sinc(2 * DECIM_CUTOFF_MHZ * 1e6 / FS * m) * np.kaiser(DECIM_TAPS, DECIM_BETA)
    return h / h.sum()


def decimate_blocks(blocks_40, total):
    """Yield the 20 MS/s file in blocks from the 40 MS/s blocks: y[n] = sum_k h[k] x[2n - k], k centred, zeros
    beyond the file's ends."""
    h = decim_taps()
    half = (DECIM_TAPS - 1) // 2
    buf = np.zeros(half, dtype=np.complex64)            # samples before index 0 are zero
    base = -half                                        # absolute index of buf[0]
    next_out = 0                                        # next output n (input index 2n)
    for blk in itertools.chain(blocks_40, [np.zeros(half + 2, dtype=np.complex64)]):
        buf = np.concatenate([buf, blk.astype(np.complex64)])
        last_in = base + len(buf) - 1
        n_hi = min((last_in - half) // 2, (total - 1) // 2)
        if n_hi >= next_out:
            idx = 2 * np.arange(next_out, n_hi + 1) - base
            y = np.zeros(len(idx), dtype=np.complex128)
            for k in range(DECIM_TAPS):
                y += h[k] * buf[idx - (k - half)]
            yield y.astype(np.complex64)
            next_out = n_hi + 1
        keep = 2 * next_out - half - base
        buf = buf[keep:]
        base += keep


def halve_truth(side):
    """The 40 MS/s plan's sidecar as the 20 MS/s file's: sample positions halved, symbol phases at 20 sps."""
    out = json.loads(json.dumps(side))
    sps20 = SPS // 2
    for e in out['bursts'] + out['bursts_not_rendered']:
        pos = (e['start_sample'] + e['timing_frac']) / 2.0
        e['start_sample'] = int(math.floor(pos))
        e['timing_frac'] = pos - e['start_sample']
        e['symbol_phase'] = int(e['start_sample'] % sps20)
        for key in ('end_sample', 'slot_boundary_sample'):
            if e.get(key) is not None:
                e[key] = e[key] / 2.0
        for o in e.get('overlaps', []):
            o['overlap_samples'] = o['overlap_samples'] / 2.0
    for d in out['devices']:
        for key in ('slot_grid_offset_samples', 'slot_grid_offset_int', 'slot_grid_offset_frac'):
            if key in d:
                d[key] = d[key] / 2.0
        if d.get('exchange'):
            for key in ('origin_sample', 'tick_samples'):
                d['exchange'][key] = d['exchange'][key] / 2.0
    out['sample_rate'] = FS / 2
    out['n_samples'] = side['n_samples'] // 2
    out['slot_samples'] = SLOT / 2
    out['grid_base_samples'] = GRID_BASE / 2
    out['symbol_phases'] = [0, sps20 // 2]
    out['burst_window'] = out['burst_window'].replace('* 40', '* 20').replace('40 MS/s', '20 MS/s')
    out['decimation'] = dict(
        rendered_at_sample_rate=FS, factor=2, taps=DECIM_TAPS, kaiser_beta=DECIM_BETA, cutoff_mhz=DECIM_CUTOFF_MHZ,
        alignment='output sample n is input sample 2n (odd, centred FIR, zeros beyond the file)',
        truth='start_sample + timing_frac, end_sample, slot_boundary_sample, overlap_samples, the devices\' slot grid '
              'offsets and the joiners\' origin_sample and tick_samples are halved; symbol_phase = int(start_sample % 20); '
              'noise_block_samples is the 40 MS/s render\'s (the noise is made at 40 MS/s and filtered)',
        noise='the floor is white at 40 MS/s and filtered with DC gain 1: noise_1mhz holds in the passband; above about '
              '8.6 MHz it rolls off')
    return out


# --- the interferer (setn_d) ---------------------------------------------------------------------

def interf_taps(fs=FS, band_mhz=INTERF_BAND_MHZ):
    """bt_synth_interf's Wi-Fi FIR (its taps, beta and margin) for another band."""
    lo, hi = band_mhz[0] - itf.WIFI_EDGE_MARGIN_MHZ, band_mhz[1] + itf.WIFI_EDGE_MARGIN_MHZ
    centre, half = (lo + hi) / 2 * 1e6, (hi - lo) / 2 * 1e6
    m = np.arange(itf.WIFI_TAPS) - (itf.WIFI_TAPS - 1) / 2
    proto = np.sinc(2 * half / fs * m) * np.kaiser(itf.WIFI_TAPS, itf.WIFI_KAISER_BETA)
    taps = proto * np.exp(2j * np.pi * centre / fs * m)
    gain = abs(np.sum(taps * np.exp(-2j * np.pi * centre / fs * np.arange(itf.WIFI_TAPS))))
    return taps / gain


class BandWifiNoise(itf.WifiNoise):
    """The interf set's Wi-Fi-like noise (same white noise streams, filter length and overlap-save) in INTERF_BAND_MHZ."""

    def __init__(self, seed, level_db, fs):
        super().__init__(seed, level_db, fs)
        self.taps = interf_taps(fs)


def interf_overlap_table(channels, fs=FS, center_mhz=CENTER_MHZ):
    n = 1 << 18
    h = np.abs(np.fft.fft(interf_taps(fs), n)) ** 2
    f = np.fft.fftfreq(n, 1 / fs) / 1e6
    df = fs / n / 1e6
    return {int(c): float(np.sum(h[np.abs(f - (mx.hop.channel_mhz(c) - center_mhz)) < 0.5]) * df) for c in channels}


def add_interferer(blocks, total, iseed, fs=FS):
    """Yield the blocks with the bursty band noise added (frames from ``iseed``)."""
    frames = itf.frame_schedule(iseed, total, fs)
    noise = BandWifiNoise(iseed, INTERF_LEVEL_DB, fs)
    lo = 0
    for blk in blocks:
        hi = lo + len(blk)
        y = noise.block(hi - lo) * itf.gate_block(frames, lo, hi)
        yield (blk + y.astype(np.complex64)).astype(np.complex64)
        lo = hi


def interferer_truth(side, iseed, fs=FS):
    frames = itf.frame_schedule(iseed, side['n_samples'], fs)
    chans = sorted({e['channel'] for e in side['bursts']})
    table = interf_overlap_table(chans, fs)
    spec = [dict(kind='wifi', level_db=INTERF_LEVEL_DB, bursty=True)]
    for e in side['bursts']:
        a = int(math.floor(e['start_sample'] + e['timing_frac']))
        b = int(math.ceil(e['start_sample'] + e['timing_frac'] + e['air_bits_length'] * SPS))
        e['interferer_overlap'], e['interferer_power_in_band_db'] = itf.burst_truth(spec, frames, a, b, e['channel'], table)
    side['interferer'] = dict(
        kind='wifi_noise', bursty=True, level_db=INTERF_LEVEL_DB, band_offset_mhz=list(INTERF_BAND_MHZ),
        band_mhz=[CENTER_MHZ + INTERF_BAND_MHZ[0], CENTER_MHZ + INTERF_BAND_MHZ[1]], seed=iseed,
        frames=[list(f) for f in frames], duty=itf.DUTY, frame_ms=list(itf.FRAME_MS), edge_us=itf.EDGE_US,
        taps=itf.WIFI_TAPS, kaiser_beta=itf.WIFI_KAISER_BETA, edge_margin_mhz=itf.WIFI_EDGE_MARGIN_MHZ,
        reach_mhz=itf.WIFI_REACH_MHZ, overlap_table_mhz={str(k): v for k, v in table.items()},
        note='bt_synth_interf\'s bursty Wi-Fi-like noise (its white streams, filter length, beta, frames and gate) moved to '
             '+2..+11 MHz; interferer_overlap / interferer_power_in_band_db as the interf set defines them; where no frame '
             'is on, this file equals setn_a sample for sample',
        n_bursts_overlapped=sum(1 for e in side['bursts'] if e['interferer_overlap']))
    side['per_burst_interferer_keys'] = ['interferer_overlap', 'interferer_power_in_band_db']
    return side


# --- the files ------------------------------------------------------------------------------------

def make(name, blocks=BLOCKS, level_range=LEVEL_RANGE):
    """``(sidecar, block iterator)`` of one file of the set."""
    spec = FILES[name]
    band = BAND_20 if spec['fs'] == 20e6 else WINDOW_40
    side, jobs, total = plan_setn(spec['seed'], blocks, level_range=level_range, band=band, name=name)
    blocks_40 = mx.render_blocks(jobs, total, spec['seed'])
    if spec['interferer']:
        iseed = spec['seed'] + INTERF_SEED_OFFSET
        side = interferer_truth(side, iseed)
        return side, add_interferer(blocks_40, total, iseed)
    if spec['fs'] == 20e6:
        return halve_truth(side), decimate_blocks(blocks_40, total)
    return side, blocks_40


def write_file(name, out_dir, blocks=BLOCKS):
    t0 = time.time()
    side, gen = make(name, blocks)
    os.makedirs(out_dir, exist_ok=True)
    iq_path = os.path.join(out_dir, 'synth_%s.cf32' % name)
    n = 0
    with open(iq_path, 'wb') as f:
        for out in gen:
            out.astype('<c8', copy=False).tofile(f)
            n += len(out)
    assert n == side['n_samples'], (n, side['n_samples'])
    with open(os.path.join(out_dir, 'synth_%s.json' % name), 'w') as f:
        json.dump(side, f, separators=(',', ':'))
    print('%s: %d samples at %g MS/s, %d bursts (%d not rendered), %d collided (%.1f %%), opportunities %s, %.0f s' % (
        iq_path, n, side['sample_rate'] / 1e6, side['n_bursts'], side['n_bursts_not_rendered'], side['n_collisions'],
        100 * side['fraction_collided'], side['opportunities'], time.time() - t0), flush=True)
    return iq_path


def write_manifest(out_dir, names, commands):
    rows, sums = [], []
    for name in names:
        base = os.path.join(out_dir, 'synth_%s' % name)
        side = json.load(open(base + '.json'))
        row = dict(name=name, file=os.path.basename(base) + '.cf32', sidecar=os.path.basename(base) + '.json',
                   seed=side['seed'], sample_rate=side['sample_rate'], interferer='interferer' in side,
                   n_samples=side['n_samples'], n_bursts=side['n_bursts'], n_bursts_not_rendered=side['n_bursts_not_rendered'],
                   fraction_collided=side['fraction_collided'], non_afh_device=side['non_afh_device'],
                   non_afh_burst_indices=side['non_afh_burst_indices'], opportunities=side['opportunities'],
                   n_exchanges_one_member_rendered=side['n_exchanges_one_member_rendered'],
                   bytes=os.path.getsize(base + '.cf32'), generator_commit=side['generator_commit'])
        for ext in ('.cf32', '.json'):
            h = hashlib.sha256()
            with open(base + ext, 'rb') as f:
                for blk in iter(lambda: f.read(1 << 26), b''):
                    h.update(blk)
            sums.append('%s  %s' % (h.hexdigest(), os.path.basename(base) + ext))
            row['sha256' + ext.replace('.', '_')] = h.hexdigest()
        rows.append(row)
    with open(os.path.join(out_dir, 'manifest.json'), 'w') as f:
        json.dump(dict(set='setn', generator='scripts/bt_synth_multislot.py', commands=commands, files=rows), f, indent=1)
    with open(os.path.join(out_dir, 'SHA256SUMS.txt'), 'w') as f:
        f.write('\n'.join(sums) + '\n')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('name', nargs='?', choices=sorted(FILES), help='one file of the set')
    ap.add_argument('--set', choices=sorted(SETS), help='the whole set, with manifest.json and SHA256SUMS.txt')
    ap.add_argument('--plan-only', action='store_true', help='print each file\'s plan counts and opportunities; write nothing')
    ap.add_argument('--out', default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    names = SETS[a.set] if a.set else ([a.name] if a.name else [])
    if not names:
        ap.error('give a file name or --set')
    if a.plan_only:
        for name in names:
            spec = FILES[name]
            side, _, _ = plan_setn(spec['seed'], band=BAND_20 if spec['fs'] == 20e6 else WINDOW_40, name=name)
            print(name, json.dumps(dict(n_bursts=side['n_bursts'], not_rendered=side['n_bursts_not_rendered'],
                                        collided=round(side['fraction_collided'], 4), opportunities=side['opportunities'],
                                        one_member_rendered=side['n_exchanges_one_member_rendered'],
                                        non_afh_device=side['non_afh_device'])), flush=True)
        return
    for name in names:
        write_file(name, a.out)
    if a.set:
        write_manifest(a.out, names, ['python -B scripts/bt_synth_multislot.py --set %s --out %s' % (a.set, a.out)])


if __name__ == '__main__':
    main()
