#!/usr/bin/env python3
"""DM1/DM3/DM5 ACL traffic carrying LMP PDUs, at five SNR levels, and a DH5 control.

    python scripts/bt_synth_acl.py acl_dm_hop20_snr09p0 --snr 9 --out DIR
    python scripts/bt_synth_acl.py --set acl [--out DIR]
    python scripts/bt_synth_acl.py --sweep [--jobs N]        # writes scripts/bt_synth_acl_sweep.json

    from scripts import bt_synth_acl
    iq, sidecar = bt_synth_acl.synthesise_acl(snr_db=12.0, bursts=24)

For bluey-ox-walker's CRC-assisted bit correction of plain FEC 2/3 payloads
(its batch 1, item A), which it wants to try on DM1, DM3 and DM5 ACL traffic at
the SNRs where the payload yield falls from about 95 % to about 50 %, graded by
dB and stratified by packet type, with the on-air bits of every burst known so
that errors can be planted. No encryption: the CRC correction is for plain
payloads. Nothing is transmitted. The plan is in
[knowledge/bluey-test-signals.md](../knowledge/bluey-test-signals.md).

**The link.** One master, the identity of the page files: LAP ``0x112233``,
UAP ``0x55``, NAP ``0x1234``; ``lt_addr`` 1; master packets only (the slave's
answers are not in the files). An ACL link: FLOW 1 in the packet header and in
every payload header; ARQN 1; SEQN alternating on every burst (each is a new
payload). The hop is the adapted sequence of ``bt_synth_hop.afh_hop_fn`` over
the map 31-50 (2433 to 2452 MHz) at 40 MS/s on 2441.0 MHz, taken at the clock of
the packet's first slot and held for all of it (Core Vol 2 Part B 2.6: the
frequency of a multi-slot packet is that of its first slot).

**The schedule.** 204 bursts, 68 each of DM1, DM3 and DM5 in a seeded random
order (``type_counts`` in the sidecar). A burst starts on a master slot; the
next starts ``slots + 1 + 2 * idle`` slots later (the packet's own slots, the
slave's slot after them, and ``idle`` of 0, 1 or 2 further master-slot pairs,
uniform, seeded), so a DM5 is followed by at least one slot. The clock is the
native CLK27-0 of the burst's first slot, two ticks a slot. Symbol phases
alternate between 0 and ``sps / 2`` per burst; the timing fraction of each is
uniform in [0, 1), seeded.

**The payloads.**

* DM1: LLID 3 (LMP), a 1-byte payload header (FLOW 1, LENGTH of the PDU) and one
  real LMP PDU of ``apps/bt_lmp.py`` (2 to 17 bytes), then CRC-16 over header
  and body with the UAP seed and FEC 2/3 over the whole payload (Part B 6.5.4.1
  and 7.1.2). The 68 DM1 of a file go through the eleven PDUs in a seeded
  shuffled cycle, so each of the eleven (``LMP_name_req``, ``_name_res``,
  ``_accepted``, ``_not_accepted``, ``_detach``, ``_features_req``,
  ``_features_res``, ``_version_req``, ``_version_res``, ``_max_slot`` and
  ``_set_AFH``) is in every file at least six times.
* DM3 and DM5: the 2-byte payload header (Part B 6.6), LLID 2, an L2CAP start, with a
  basic L2CAP header (length, channel 0x0040) and random bytes, a body of a seeded length
  between 20 bytes and the packet's maximum (121, 224). **No LMP at all in a DM3 or DM5**:
  Core Vol 2 Part C Table 5.1 lists every one of the eleven PDUs as carried in DM1 (or
  DM1/DV), never in a longer packet. bluey-ox-walker asked for LMP "inside DM1/DM3/DM5"; the
  files give it LMP in the DM1 and ACL-U data in the DM3 and DM5, because an LMP PDU in a
  DM3 would not be a conforming packet.
* The control, ``acl_dh5_hop20_clean_snr20``: 204 DH5 at 20 dB, every one LLID 2
  with a body of 20 to 339 bytes, no FEC, the same seed and the same per-burst
  draws (gaps, timing fractions, phases) as the level files.

**One floor, one seed.** The noise is the floor of every file in this
repository: ``NOISE_1MHZ = AMPLITUDE**2 / 100`` in 1 MHz, a burst of SNR ``s`` dB
having amplitude ``sqrt(NOISE_1MHZ * 10**(s/10))``; block ``i`` of the noise is
``default_rng([seed, 1, i])``, and a file is a whole number of such blocks (up to 0.1 s of
trailing noise), so that two files of the same seed share every block of the shorter. Every draw about a burst - its gap, timing
fraction, carrier phase, payload - comes from a generator of that burst's number
alone, so the five level files have **the same schedule, clocks, channels,
payload bytes, timing fractions, phases and noise realisation, and differ only by
the signal's scale** (``file - noise = A * burst``), and the control, which has
other types, has the same gaps, timing fractions and phases. The seed is 8101.

**The levels** are the constants ``LEVELS_DB``, chosen from the sweep this file
runs with ``--sweep``: the stored result is ``scripts/bt_synth_acl_sweep.json``
and goes into every sidecar, with the receiver that measured it (``rx_burst``:
the position and channel are the sidecar's, the demodulation is a 0.8 MHz FIR,
a differential discriminator over one symbol and libbtbb as the decoder).
Another receiver's yield will differ; the grade is by dB. The sweep's pooled yield at the first four
levels is about 96, 80, 60 and 50 % (the curve is steep and the levels are rounded to 0.5 dB, so
the third is nearer 60 than 65), and about 12 % at the fifth, 3 dB below the fourth; the sidecar's
``levels_yield_note`` gives the measured figures.

**Truth per burst** (beside the keys of the other files): ``ptype``, ``lap``,
``uap``, ``channel``, ``start_sample`` and ``timing_frac``, ``clk``, ``snr_db``,
``llid``, ``payload_length`` (the bytes after the payload header),
``payload_hex``, ``lmp_opcode``, ``lmp_name``, ``lmp_tid`` (null if not LMP),
the header fields, and ``air_bits`` / ``body_crc_bits_hex`` - **the exception to
the usual "air_bits omitted"**: the whole packet as sent after whitening and FEC,
and the payload before them (payload header, body, CRC-16) in transmit order, as
hex with the first bit on air the most significant bit of the first byte, the
last byte zero-padded, and their lengths in bits in ``air_bits_length`` and
``body_crc_bits_length``. ``n_samples`` is the number of complex samples in the
file, whose size is ``8 * n_samples`` bytes.
"""
import argparse
import concurrent.futures
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from apps import bt_lmp  # noqa: E402
from scripts import bt_synth, bt_synth_hop as hop  # noqa: E402
from scripts.bt_synth_interf import NOISE_1MHZ, amplitude_for, noise_block  # noqa: E402

LAP = 0x112233
UAP = 0x55
NAP = 0x1234
LT_ADDR = 1
CENTER_MHZ = 2441.0
MAP = list(range(31, 51))                  # 20 channels, 2433 to 2452 MHz
CLK0 = 0x0123400
FS = 40e6
SEED = 8101
BURSTS = 204
DM_TYPES = ('DM1', 'DM3', 'DM5')
LMP_NAMES = tuple(bt_lmp.PDUS)             # the eleven
LMP_PACKET_TYPES = ('DM1',)                # Table 5.1: every PDU used here is DM1 (or DM1/DV)
L2CAP_MIN = 20                             # shortest L2CAP body of a DM3, DM5 or DH5
L2CAP_CID = 0x0040
CONTROL_SNR_DB = 20.0
CONTROL_NAME = 'acl_dh5_hop20_clean_snr20'

#: The five levels of ``--set acl``, dB over the noise in 1 MHz, chosen by the sweep of
#: ``bt_synth_acl_sweep.json``: the pooled payload yield of the sweep receiver is about
#: 96, 80, 60 and 50 % at the first four (picked for 95, 80, 65 and 50 %, to 0.5 dB), and the
#: fifth, about 12 %, is 3 dB below the 50 % point.
LEVELS_DB = [16.5, 15.0, 14.0, 13.5, 10.5]

SWEEP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bt_synth_acl_sweep.json')
SWEEP_SEEDS = (8101, 8102, 8103)
SWEEP_SNRS = tuple(float(s) for s in range(8, 21))

#: Samples in a block of noise. Float64 temporaries of a block are 32 MB each.
BLOCK_SAMPLES = 2 ** 22

#: The eleven LMP PDUs, with their opcodes, for the sidecar.
LMP_TABLE = {n: dict(v) for n, v in bt_lmp.PDUS.items()}

CLOCK_LOCK_NOTE = (
    "The clock of every burst is in bursts[].clk (native CLK[27:0]); a receiver's CLK[27:1] lock for a burst "
    "is expected to equal clk >> 1 with no offset. Over the 20-channel map (31-50) a different clock can "
    "reproduce the same hops for a short run of bursts, so a lock over few bursts may not be unique.")

AIR_BITS_NOTE = (
    "air_bits_omitted is false: this is the exception to the usual rule that air_bits are left out. Every burst "
    "carries air_bits, the whole packet as "
    "sent (access code, header, payload; after whitening and the FEC 1/3 of the header and, in a DM packet, "
    "the FEC 2/3 of the payload), and body_crc_bits_hex, the payload before whitening and FEC (payload "
    "header, body, CRC-16), both in transmit order. Both are hex strings of the bit sequence packed so that "
    "the first bit on air is the most significant bit of the first byte, the last byte zero-padded; "
    "air_bits_length and body_crc_bits_length are the lengths in bits. (payload_full_hex is the same payload "
    "as bytes in the usual way, each byte's least significant bit first on air.)")


def bits_hex(bits):
    """Bits in transmit order as hex: the first bit the top bit of the first byte, zero-padded to a byte."""
    s = ''.join('1' if b else '0' for b in bits)
    s += '0' * (-len(s) % 8)
    return '%0*x' % (len(s) // 4, int(s, 2)) if s else ''


def level_name(snr_db):
    """``snr09p0`` for 9.0 dB: the level in a file's name."""
    return 'snr' + ('%04.1f' % snr_db).replace('.', 'p')


# --- the plan: every draw about a burst, from the burst's own generator --------------

def plan_acl(kind='dm', bursts=BURSTS, seed=SEED, lap=LAP, uap=UAP, clk0=CLK0, fs=FS,
             center_mhz=CENTER_MHZ, channels=MAP):
    """Everything about the bursts but their level, before any sample is made.

    ``kind`` is ``'dm'`` (DM1, DM3 and DM5 in equal numbers, in a seeded order) or ``'dh5'``
    (the control). Returns a list of dicts, one a burst: ``k``, ``ptype``, ``clk``, ``channel``,
    ``slot`` (the master slot the burst starts in), ``phase`` (0 or ``sps / 2``), ``timing_frac``,
    ``burst_phase``, ``llid``, ``body``, ``lmp`` (None, or name, tid and params).
    """
    if kind not in ('dm', 'dh5'):
        raise ValueError("kind is 'dm' or 'dh5', not %r" % (kind,))
    if kind == 'dm' and bursts % len(DM_TYPES):
        raise ValueError("%d bursts do not split equally over %s" % (bursts, ', '.join(DM_TYPES)))
    if clk0 % 4:
        raise ValueError("clk0 must be a master slot's clock: CLK1-0 = 00")
    sps = int(round(fs / br.SYMBOL_RATE))
    channels = hop.map_channels(channels)
    for channel in channels:
        if hop.outside_window(channel, fs, center_mhz):
            raise ValueError("channel %d, %g MHz, is outside the window of %g MS/s on %g MHz"
                             % (channel, hop.channel_mhz(channel), fs / 1e6, center_mhz))
    instant = (clk0 & 0x0FFFFFFF) >> 1
    hop_fn = hop.afh_hop_fn([(instant, channels)], lap, uap)

    if kind == 'dm':
        n = bursts // len(DM_TYPES)
        types = [t for t in DM_TYPES for _ in range(n)]
        np.random.default_rng([seed, 10]).shuffle(types)
    else:
        types = ['DH5'] * bursts
    # the DM1 go through the eleven PDUs in a shuffled cycle, so each is in every file
    dm1_total = types.count('DM1')
    cycle = []
    order_rng = np.random.default_rng([seed, 5])
    while len(cycle) < dm1_total:
        cycle += [LMP_NAMES[i] for i in order_rng.permutation(len(LMP_NAMES))]
    cycle = cycle[:dm1_total]

    plan, slot, dm1_seen = [], 0, 0
    for k, ptype in enumerate(types):
        slots, longest = br.PACKET_TYPES[ptype][1], br.PACKET_TYPES[ptype][4]
        clk = (clk0 + 2 * slot) & 0x0FFFFFFF
        # the burst's own timing draws: the same in every file, whatever its type
        t_rng = np.random.default_rng([seed, 2, k])
        idle = int(t_rng.integers(0, 3))
        timing_frac = float(t_rng.uniform(0, 1))
        burst_phase = float(t_rng.uniform(0, 2 * np.pi))
        # the payload
        p_rng = np.random.default_rng([seed, 4, k])
        lmp = None
        if ptype == 'DM1':
            lmp_name = cycle[dm1_seen]
            dm1_seen += 1
        else:
            lmp_name = None
        if lmp_name is not None:
            body, params, tid = bt_lmp.random_pdu(p_rng, lmp_name, clk)
            llid, lmp = 0b11, dict(name=lmp_name, tid=tid, params=params)
        else:
            length = int(p_rng.integers(L2CAP_MIN, longest + 1))
            data = bytes(p_rng.integers(0, 256, length - 4, dtype=np.uint8))
            body = (length - 4).to_bytes(2, 'little') + L2CAP_CID.to_bytes(2, 'little') + data
            llid = 0b10
        channel = int(hop_fn(clk))
        plan.append(dict(k=k, ptype=ptype, clk=clk, channel=channel, slot=slot, slots=slots, phase=(0, sps // 2)[k % 2],
                         timing_frac=timing_frac, burst_phase=burst_phase, llid=llid, body=body, lmp=lmp))
        slot += slots + 1 + 2 * idle
    return plan, dict(afh_instant=instant, afh_map=channels, slots_total=slot)


# --- the file --------------------------------------------------------------------------

def synthesise_acl(kind='dm', snr_db=15.0, bursts=BURSTS, seed=SEED, lap=LAP, uap=UAP, clk0=CLK0, fs=FS,
                   center_mhz=CENTER_MHZ, channels=MAP, block_samples=BLOCK_SAMPLES, sweep=None,
                   levels=None, name=None):
    """The samples and the sidecar of one file: ``kind`` ``'dm'`` (DM1/DM3/DM5 with LMP) or ``'dh5'``
    (the control) at ``snr_db`` over the noise in 1 MHz."""
    plan, info = plan_acl(kind, bursts, seed, lap, uap, clk0, fs, center_mhz, channels)
    slot_samples = fs * br.SLOT_US * 1e-6
    if abs(slot_samples - round(slot_samples)) > 1e-6:
        raise ValueError("%g S/s does not divide a 625 us slot" % fs)
    slot_samples = int(round(slot_samples))
    sps = int(round(fs / br.SYMBOL_RATE))
    amp = amplitude_for(snr_db)
    grid = int(bt_synth.LEAD_SLOTS * slot_samples)
    grid -= grid % sps
    total = grid + (info['slots_total'] + 1) * slot_samples
    # a whole number of noise blocks: block i of the noise is a function of its length (the Q draws follow the
    # I draws), so a file whose last block is partial would differ in it from a longer file of the same seed
    total = -(-total // block_samples) * block_samples
    iq = np.zeros(total, dtype=np.complex64)
    entries = []
    for b in plan:
        k, ptype, clk, channel = b['k'], b['ptype'], b['clk'], b['channel']
        p = br.Packet(lap, uap, clk, ptype, b['body'], lt_addr=LT_ADDR, flow=1, arqn=1, seqn=k & 1,
                      llid=b['llid'], payload_flow=1)
        start = grid + b['slot'] * slot_samples + b['phase']
        burst, lead = br.gfsk(p.bits, fs, h=br.GFSK_H, delay=b['timing_frac'])
        burst = burst * np.exp(1j * b['burst_phase'])
        lo = start - lead
        n = np.arange(lo, lo + len(burst))
        cycles = (hop.channel_mhz(channel) - center_mhz) * 1e6 / fs * n
        burst = burst * np.exp(2j * np.pi * (cycles - np.floor(cycles)))
        if lo + len(burst) > total:
            raise ValueError("burst %d ends at %d, past the end of the file at %d" % (k, lo + len(burst), total))
        iq[lo:lo + len(burst)] += (amp * burst).astype(np.complex64)
        payload_bits = br.bytes_bits(p.payload_full)
        entry = {'start_sample': int(start), 'timing_frac': b['timing_frac']}
        entry.update(p.sidecar())
        entry.update(
            air_bits=bits_hex(p.bits), air_bits_length=len(p.bits),
            body_crc_bits_hex=bits_hex(payload_bits), body_crc_bits_length=len(payload_bits),
            lap=lap, uap=uap,
            llid=b['llid'], payload_length=len(b['body']),
            lmp_opcode=None if b['lmp'] is None else bt_lmp.PDUS[b['lmp']['name']]['opcode'],
            lmp_name=None if b['lmp'] is None else b['lmp']['name'],
            lmp_tid=None if b['lmp'] is None else b['lmp']['tid'],
            lmp_params=None if b['lmp'] is None else {
                key: (v.hex() if isinstance(v, bytes) else v) for key, v in b['lmp']['params'].items()},
            slots=b['slots'], end_sample=int(start + len(p.bits) * sps), channel=channel,
            channel_mhz=hop.channel_mhz(channel), symbol_phase=int(start % sps), snr_db=float(snr_db),
            afh_map_index=0, burst_phase=b['burst_phase'])
        entries.append(entry)
    for index, lo in enumerate(range(0, total, block_samples)):
        hi = min(lo + block_samples, total)
        iq[lo:hi] += noise_block(seed, index, hi - lo, fs)

    counts = {t: sum(e['ptype'] == t for e in entries) for t in sorted({e['ptype'] for e in entries})}
    lmp_counts = {n: sum(e['lmp_name'] == n for e in entries) for n in LMP_NAMES}
    sidecar = {
        'generator': 'SDR scripts/bt_synth_acl.py',
        'generator_commit': bt_synth.commit(),
        'name': name,
        'lap': lap,
        'uap': uap,
        'nap': NAP,
        'master': {'lap': lap, 'uap': uap, 'nap': NAP, 'lt_addr': LT_ADDR},
        'clk': plan[0]['clk'],
        'clk_convention': 'native CLK[27:0], at the first sample of the burst '
                          'it is given for; top-level clk is burst 0\'s',
        'link': 'ACL, master packets only (no slave answers in the file); packet header FLOW 1, ARQN 1, '
                'SEQN = burst number & 1 (every burst a new payload); payload header FLOW 1',
        'hopping': True,
        'hop_channels': sorted({b['channel'] for b in plan}),
        'channel_mhz': None,
        'bt_channel': None,
        'sample_rate': fs,
        'center_mhz': center_mhz,
        'cfo_hz': 0.0,
        'n_samples': int(total),
        'n_samples_meaning': 'the number of complex samples in the file; its size is 8 * n_samples bytes',
        'timing_frac': None,
        'timing_frac_meaning': 'null at the top: every burst has its own, uniform in [0, 1)',
        'snr_db': float(snr_db),
        'snr_bw_hz': 1e6,
        'noise_1mhz': NOISE_1MHZ,
        'modulation': {'scheme': 'GFSK', 'bt': br.GFSK_BT, 'h': br.GFSK_H, 'symbol_rate': br.SYMBOL_RATE},
        'slot_samples': slot_samples,
        'start_offset': 0,
        'symbol_phase': None,
        'symbol_phases': [0, sps // 2],
        'symbol_phase_meaning': 'null at the top: alternating per burst, even bursts at phase 0, '
                                'odd at sps/2; each burst has its own',
        'per_burst_keys': sorted(entries[0]),
        'start_sample_meaning': 'the first sample of the access code\'s preamble, before timing_frac is added',
        'air_bits_omitted': False,
        'air_bits_note': AIR_BITS_NOTE,
        'kind': kind,
        'type_counts': counts,
        'equal_counts': len(set(counts.values())) == 1 and kind == 'dm',
        'equal_counts_statement': (
            'every packet type has the same number of bursts, %d each (%s)'
            % (bursts // len(DM_TYPES), ', '.join(DM_TYPES)) if kind == 'dm'
            else 'the control: all %d bursts are DH5' % bursts),
        'lmp_pdus': LMP_TABLE,
        'lmp_counts': lmp_counts,
        'lmp_carrier_note': (
            'LMP PDUs are in the DM1 only: Core Vol 2 Part C Table 5.1 lists every one of the eleven PDUs as DM1 '
            '(or DM1/DV), never DM3 or DM5. bluey-ox-walker asked for LMP inside DM1, DM3 and DM5; the DM3 and DM5 '
            'carry ACL-U data (LLID 2, L2CAP-like) only, because an LMP PDU in them would not be a conforming '
            'packet. Every DM1 is LMP, so the 68 DM1 of a file cycle through all eleven PDUs.'),
        'l2cap_note': 'LLID 2 bodies: an L2CAP basic header (length = body - 4, channel 0x%04X), then random '
                      'bytes; length uniform in %d to the packet\'s maximum' % (L2CAP_CID, L2CAP_MIN),
        'tid_note': 'LMP transaction ID, the least significant bit of the first PDU byte (Part C 2.4): 0 for a '
                    'PDU the master starts, 1 for its answer to a transaction the slave started '
                    '(name_res, accepted, not_accepted, features_res, version_res)',
        'afh_map': info['afh_map'],
        'afh_instant': info['afh_instant'],
        'afh_map_count': 1,
        'afh_maps': [{'instant': info['afh_instant'], 'first_burst': 0, 'channels': info['afh_map']}],
        'afh_instant_meaning': hop.INSTANT_MEANING,
        'hop_kernel': hop.HOP_KERNEL,
        'address_for_hop': uap << 24 | lap,
        'clock_lock_note': CLOCK_LOCK_NOTE,
        'multi_slot_channel_note': 'a multi-slot packet is on the channel of its first slot for its whole length',
        'schedule_note': 'a burst starts on a master slot; the next starts slots + 1 + 2 * idle slots later, idle '
                         'in {0, 1, 2} uniform and seeded (default_rng([seed, 2, k])); the clock advances two ticks '
                         'a slot',
        'paired_set': {
            'seed': seed,
            'note': 'the level files and the control share seed, noise realisation, gaps, timing fractions and '
                    'phases; the level files (kind dm) also share types, schedule, clocks, channels and payload '
                    'bytes and differ only by the scale of the signal: file - noise = A(snr_db) * burst, with '
                    'noise block i = default_rng([seed, 1, i]) and A = sqrt(noise_1mhz * 10**(snr_db / 10))',
            'levels_db': list(LEVELS_DB) if levels is None else list(levels),
            'control': CONTROL_NAME,
        },
        'levels_db': list(LEVELS_DB) if levels is None else list(levels),
        'sweep': sweep,
        'levels_yield_note': levels_yield_note(sweep, levels or LEVELS_DB),
        'sweep_note': ('the sweep that chose levels_db: the payload yield of the reference receiver of '
                       'scripts/bt_synth_acl.py (rx_burst) against SNR; ANOTHER RECEIVER\'S YIELD WILL DIFFER and '
                       'the grade is by dB') if sweep else None,
        'seed': seed,
        'noise_block_samples': block_samples,
    }
    sidecar['bursts'] = entries                      # the long list last
    return iq, sidecar


def levels_yield_note(sweep, levels):
    """The measured pooled yields at the levels, as a sentence built from the sweep (None without one)."""
    if not sweep or 'levels_check' not in sweep:
        return None
    pooled = {r['snr_db']: r['pooled']['yield'] for r in sweep['levels_check']['all_seeds']}
    own = {r['snr_db']: r['pooled']['yield'] for r in sweep['levels_check']['file_seed']}
    return ('pooled payload yield of the reference receiver at levels_db: %s dB; over three seeds %s %%, '
            'for this file\'s own seed %s %%; the levels were picked for 95, 80, 65 and 50 %% and rounded to '
            '0.5 dB, and the fifth is 3 dB below the fourth'
            % (', '.join('%g' % v for v in levels), ', '.join('%.1f' % (100 * pooled[v]) for v in levels),
               ', '.join('%.1f' % (100 * own[v]) for v in levels)))


# --- the sweep's receiver -----------------------------------------------------------------

RX_TAPS_N = 255
RX_CUTOFF_HZ = 0.8e6


def rx_taps(fs=FS):
    from scipy.signal import firwin
    return firwin(RX_TAPS_N, RX_CUTOFF_HZ, fs=fs)


def rx_burst(iq, entry, lap=LAP, fs=FS, center_mhz=CENTER_MHZ, taps=None, search=150):
    """The reference receiver, for one burst: its bits, from the samples.

    The channel and a start to within ``search`` samples are the sidecar's (the receiver is told where
    the burst is; the yield is conditional on detection). It shifts the burst down from its carrier,
    filters with a 0.8 MHz FIR, finds the access code by correlating the discriminator with the 68 known
    bits, slices every bit from the phase change over its symbol with the threshold the access code
    gives, and returns ``(bits, start_estimate)`` for the packet's length. Returns ``(None, None)`` if
    the burst runs off the file."""
    from scipy.signal import fftconvolve, lfilter
    taps = rx_taps(fs) if taps is None else taps
    sps = int(round(fs / br.SYMBOL_RATE))
    gd = (len(taps) - 1) // 2
    nbits = entry['air_bits_length']
    lo = entry['start_sample'] - 1200
    hi = entry['start_sample'] + nbits * sps + 1200 + gd
    if lo < 0 or hi > len(iq):
        return None, None
    n = np.arange(lo, hi)
    cycles = (entry['channel_mhz'] - center_mhz) * 1e6 / fs * n
    z = np.asarray(iq[lo:hi]).astype(np.complex128) * np.exp(-2j * np.pi * (cycles - np.floor(cycles)))
    z = lfilter(taps, 1, z)[gd:]                       # index i is the sample lo + i again
    d1 = np.angle(z[1:] * np.conj(z[:-1]))
    a68 = np.array(br.access_code(lap)[:68]) * 2.0 - 1.0
    tmpl = np.repeat(a68, sps)
    tmpl -= tmpl.mean()
    c = fftconvolve(d1, tmpl[::-1], 'valid')
    centre = entry['start_sample'] - lo
    a, b = max(0, centre - search), min(len(c), centre + search + 1)
    p = a + int(np.argmax(c[a:b]))
    frac = 0.0
    if 0 < p < len(c) - 1:
        y0, y1, y2 = c[p - 1], c[p], c[p + 1]
        den = y0 - 2 * y1 + y2
        frac = 0.5 * (y0 - y2) / den if den else 0.0
    s0 = p + frac                                       # the access code's first sample, in this window
    idx = np.rint(s0 + (np.arange(nbits) + 0.5) * sps).astype(int)
    half = sps // 2
    if idx[0] - half < 0 or idx[-1] + half >= len(z):
        return None, None
    f = np.angle(z[idx + half] * np.conj(z[idx - half]))
    ones = a68 > 0
    thr = 0.5 * (f[:68][ones].mean() + f[:68][~ones].mean())
    return (f > thr).astype(np.uint8), lo + s0


def rx_yield(iq, sidecar, lib, lap=LAP, uap=UAP):
    """Per burst, ``(header_ok, crc_ok, payload_exact)`` of the reference receiver with libbtbb; None
    where the burst could not be demodulated."""
    from scripts import bt_ota_check
    taps = rx_taps(sidecar['sample_rate'])
    out = []
    for entry in sidecar['bursts']:
        bits, _ = rx_burst(iq, entry, lap, sidecar['sample_rate'], sidecar['center_mhz'], taps)
        if bits is None:
            out.append(None)
            continue
        out.append(bt_ota_check.check_btbb(lib, bits, entry, uap))
    return out


def _sweep_cell(args):
    snr, seed = args
    from scripts import bt_ota_check
    lib = bt_ota_check.libbtbb()
    iq, side = synthesise_acl('dm', snr_db=snr, seed=seed)
    got = rx_yield(iq, side, lib)
    rows = []
    for e, g in zip(side['bursts'], got):
        rows.append((e['ptype'], g is not None and bool(g[0]), g is not None and bool(g[1]),
                     g is not None and bool(g[2])))
    return snr, seed, rows


def decoder_id():
    """What decoded the sweep: the libbtbb package and library, as far as this machine says."""
    import ctypes.util
    import subprocess
    out = {'library': ctypes.util.find_library('btbb')}
    try:
        r = subprocess.run(['dpkg-query', '-W', '-f', '${Package} ${Version}', 'libbtbb1'], capture_output=True,
                           text=True, timeout=10)
        out['package'] = r.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        out['package'] = None
    return out


RX_PARAMS = dict(fir_taps=RX_TAPS_N, fir_cutoff_hz=RX_CUTOFF_HZ, start_search_samples=150,
                 discriminator='angle(z[c+20] * conj(z[c-20])) over one symbol at 40 MS/s',
                 timing='access-code correlation of the 1-sample discriminator, parabolic peak',
                 threshold='mean of the mean 1s and mean 0s of the 68 access-code bits',
                 decoder='libbtbb btbb_decode_header then btbb_decode_payload')


def run_sweep(snrs=SWEEP_SNRS, seeds=SWEEP_SEEDS, jobs=8):
    """Payload yield against SNR: for each SNR in ``snrs``, the DM files of every seed in ``seeds``, every
    burst demodulated and decoded by ``rx_yield``. Returns the table that goes in the sidecars."""
    tasks = [(s, seed) for s in snrs for seed in seeds]
    cells = {}
    with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as pool:
        for snr, seed, rows in pool.map(_sweep_cell, tasks):
            cell = cells.setdefault(snr, {})
            for ptype, hdr, crc, exact in rows:
                c = cell.setdefault(ptype, [0, 0, 0, 0])
                c[0] += 1
                c[1] += hdr
                c[2] += crc
                c[3] += exact
            print('sweep %.1f dB seed %d done' % (snr, seed), flush=True)
    table = []
    for snr in sorted(cells):
        row = {'snr_db': snr, 'per_type': {}}
        tot = [0, 0, 0, 0]
        for ptype in sorted(cells[snr]):
            n, hdr, crc, exact = cells[snr][ptype]
            row['per_type'][ptype] = {'bursts': n, 'header_ok': hdr, 'crc_ok': crc, 'payload_exact': exact,
                                      'yield': exact / n}
            tot = [a + b for a, b in zip(tot, (n, hdr, crc, exact))]
        row['pooled'] = {'bursts': tot[0], 'header_ok': tot[1], 'crc_ok': tot[2], 'payload_exact': tot[3],
                         'yield': tot[3] / tot[0]}
        table.append(row)
    return {
        'what': 'payload yield against SNR for the reference receiver of scripts/bt_synth_acl.py (rx_burst), '
                'DM1/DM3/DM5 with LMP, seeds %s, %d bursts per type per cell' % (list(seeds), len(seeds) * (BURSTS // 3)),
        'decoder': 'a genie-aided demodulator: channel and start (to within 150 samples) from the sidecar, shift '
                   'to baseband, 255-tap 0.8 MHz FIR, access-code correlation on the 68 known bits with parabolic '
                   'peak refinement, one bit per symbol from the phase change over the symbol, threshold from the '
                   'access code; then libbtbb (btbb_decode_header at the master UAP and CLK6-1, then '
                   'btbb_decode_payload: FEC 2/3 and CRC-16). A burst counts as a yield if the header '
                   'passes, the CRC passes and the payload bytes equal the truth.',
        'seeds': list(seeds),
        'receiver': RX_PARAMS,
        'decoder_id': decoder_id(),
        'caveat': 'ANOTHER RECEIVER\'S YIELD WILL DIFFER (a blind detector, a better demodulator, a different '
                  'timing recovery); the grade is by dB, not by yield. The levels are those of this genie-aided '
                  'reference receiver, which is told each burst\'s channel and start. An independent demodulator '
                  '(a centre-sample discriminator, same start and channel, same libbtbb) measured by a reviewer on '
                  'an earlier version of these files was up to 20 percentage points lower in yield (at about '
                  '13 dB) than the reference receiver.',
        'table': table,
    }


def pick_levels(sweep):
    """The four SNRs where the pooled yield is nearest 95, 80, 65 and 50 %, and 3 dB below the last, to
    0.5 dB, from a sweep table (interpolating between its points)."""
    snr = np.array([r['snr_db'] for r in sweep['table']])
    y = np.array([r['pooled']['yield'] for r in sweep['table']])
    # a yield that is not monotone in the noise is smoothed to the running maximum from the low end
    y = np.maximum.accumulate(y)
    out = []
    for target in (0.95, 0.80, 0.65, 0.50):
        out.append(round(float(np.interp(target, y, snr)) * 2) / 2)
    out.append(out[-1] - 3.0)
    if len(set(out)) != len(out):
        raise ValueError('the picked levels are not distinct: %s' % out)
    return out


# --- the files ---------------------------------------------------------------------------

def set_files():
    """What ``--set acl`` writes: ``(name, kwargs)``."""
    runs = [('acl_dm_hop20_%s' % level_name(s), dict(kind='dm', snr_db=s)) for s in LEVELS_DB]
    runs.append((CONTROL_NAME, dict(kind='dh5', snr_db=CONTROL_SNR_DB)))
    return runs


SETS = {'acl': set_files}


def load_sweep():
    with open(SWEEP_PATH) as f:
        return json.load(f)


def write_file(name, out_dir, **spec):
    """Make one file and write ``synth_<name>.cf32`` and ``.json`` into ``out_dir``."""
    os.makedirs(out_dir, exist_ok=True)
    iq_path = os.path.join(out_dir, 'synth_%s.cf32' % name)
    side_path = os.path.join(out_dir, 'synth_%s.json' % name)
    iq, sidecar = synthesise_acl(name=name, **spec)
    iq.astype('<c8', copy=False).tofile(iq_path)
    print('%s  %d samples, %.2f s, %s' % (iq_path, len(iq), len(iq) / sidecar['sample_rate'],
                                          ', '.join('%d %s' % (v, k) for k, v in sidecar['type_counts'].items())),
          flush=True)
    with open(side_path, 'w') as f:
        json.dump(sidecar, f, indent=1)
    print(side_path)
    return iq_path, side_path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('name', nargs='?', help='the file is synth_<name>.cf32; not used with --set')
    ap.add_argument('--set', help='write the whole set, with the levels and seed of its table, and no other '
                    'option but --out; one of %s' % ', '.join(sorted(SETS)))
    ap.add_argument('--sweep', action='store_true', help='run the SNR sweep and write %s' % SWEEP_PATH)
    ap.add_argument('--jobs', type=int, default=8, help='processes of the sweep')
    ap.add_argument('--kind', choices=('dm', 'dh5'), help='default dm')
    ap.add_argument('--snr', type=float, help='dB over the noise in 1 MHz; default 15')
    ap.add_argument('--bursts', type=int, help='default %d' % BURSTS)
    ap.add_argument('--seed', type=int, help='default %d' % SEED)
    ap.add_argument('--out', default='/media/user/4TB/sdr-synth-tmp', help='the folder of both files')
    args = ap.parse_args(argv)

    if args.sweep:
        result = run_sweep(jobs=args.jobs)
        result['levels_chosen'] = pick_levels(result)
        # the chosen levels themselves, which are not all on the 1 dB grid: all three seeds, and the one
        # seed the files are made from
        levels = sorted(set(result['levels_chosen']))
        result['levels_check'] = {
            'what': 'the same receiver at the levels of levels_chosen: first all sweep seeds pooled, then the '
                    'seed of the files as written (%d, %d bursts per type)' % (SEED, BURSTS // 3),
            'all_seeds': run_sweep(levels, SWEEP_SEEDS, args.jobs)['table'],
            'file_seed': run_sweep(levels, (SEED,), args.jobs)['table'],
        }
        with open(SWEEP_PATH, 'w') as f:
            json.dump(result, f, indent=1)
        print('levels chosen by the sweep:', result['levels_chosen'])
        print(SWEEP_PATH)
        return
    per_file = ['kind', 'snr', 'bursts', 'seed']
    if args.set:
        if args.set not in SETS:
            ap.error('no set %r: the sets are %s' % (args.set, ', '.join(sorted(SETS))))
        stray = [o for o in per_file if getattr(args, o) is not None]
        if args.name is not None or stray:
            ap.error('--set takes everything from its table: no name and no option but --out')
        sweep = load_sweep()
        runs = [(n, dict(spec, sweep=sweep)) for n, spec in SETS[args.set]()]
    else:
        if args.name is None:
            ap.error('give the name of the file, or --set, or --sweep')
        spec = dict(kind=args.kind or 'dm', snr_db=15.0 if args.snr is None else args.snr,
                    bursts=args.bursts or BURSTS, seed=SEED if args.seed is None else args.seed)
        runs = [(args.name, spec)]
    try:
        for name, spec in runs:
            write_file(name, args.out, **spec)
    except ValueError as e:
        ap.error(str(e))


if __name__ == '__main__':
    main()
