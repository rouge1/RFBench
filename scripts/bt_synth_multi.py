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
    ap.add_argument('what', choices=['md', 'hdr', 'noise'])
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
