#!/usr/bin/env python3
"""Bluetooth bursts with bit errors in the sync word, and a file of noise alone.

    python scripts/bt_synth_sync.py syncerr --bursts 800 --errors mixed --seed 5001 --out DIR
    python scripts/bt_synth_sync.py quiet --noise-only --duration-s 120 --fs 20e6 --out DIR
    python scripts/bt_synth_sync.py quiet --noise-only --json-only --out DIR   # sidecar alone
    python scripts/bt_synth_sync.py --set sync [--out DIR]

For bluey-ox-walker's item 4 (see knowledge/bluey-test-signals.md, a section
on these files is still to be added). Its receiver recognises a packet by the
72-bit access code - 4 preamble bits, the 64-bit sync word that carries the
LAP, 4 trailer bits - and real air has bit errors in it. So there are two
kinds of file, and nothing is transmitted for either:

* **A hopping DH1 train with errors in the sync word.** 40 MS/s on
  2441.0 MHz, one LAP, the master sending a DH1 in every master slot pair (a
  burst every 2 slots), on the adapted sequence over the map 31-50 of
  ``bt_synth_hop.afh_hop_fn``, every burst at 20 dB in 1 MHz. A burst with ``n``
  errors has ``n`` of the 64 sync-word bits flipped *in the modulated bits*, at
  positions 4..67 of the 72 access-code bits: the preamble and trailer stay as
  the correct packet has them, as they do when a bit is wrong on air, and the
  header and payload are untouched. Each burst says ``sync_errors`` and
  ``sync_error_bits`` (0..63 within the sync word). ``--errors mixed`` is burst
  0 with exactly one error and every other burst 0, 1 or 2 with equal
  probability; ``first1`` is burst 0 with one and none after it - the "first
  sight" case, where the first burst of a LAP is the errored one and its LAP is
  not yet known; ``none`` is the control, the same bursts with no error.

* **Noise only.** The same noise per MHz as the bursts' files, no burst, for
  counting how often a discovery option invents a LAP. 20 MS/s on 2441.0 MHz,
  written to disk a block at a time, never held whole.

The noise floor is fixed for every file: ``NOISE_1MHZ = AMPLITUDE**2 / 100``
in 1 MHz, so a burst of ``bt_synth.AMPLITUDE`` is at 20 dB, and the complex
sample has per-component sigma ``sqrt(NOISE_1MHZ * (fs / 1e6) / 2)``, the same
convention as ``bt_synth.synthesise``. The noise is drawn block by block, the
block ``i`` from ``default_rng([seed, 1, i])`` and the bursts' phases and
timing from ``default_rng([seed, 2])`` and the errors from
``default_rng([seed, 3])``: one seed gives the same bytes every time, and the three
hopping files of ``--set sync`` all use seed 5001, so that mixed, first1 and
clean differ in the planted bits alone - the noise, the timing, the phases and
the carrier phases are the same - and a recall comparison between them is a
paired one.

Symbol phases alternate per burst between 0 and 20 (``sps / 2``): the
detector of bluey's is blind at the half-symbol, so a burst is at phase 0 and
the next at 20, and the sidecar says so in ``symbol_phases``. The timing
fraction is uniform in [0, 1) per burst. ``air_bits`` is left out of the
sidecar (``air_bits_omitted``). The top-level ``clk`` is burst 0's (the
format's convention); ``timing_frac`` and ``symbol_phase`` are null at the
top, as they differ per burst, and ``per_burst_keys`` lists what lives per
burst; ``snr_db`` is the one constant, 20.
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from scripts import bt_synth, bt_synth_hop as hop  # noqa: E402

LAP = 0x9E8B33
UAP = 0x47
CENTER_MHZ = 2441.0
MAP = list(range(31, 51))                  # 20 channels, 2433 to 2452 MHz
CLK0 = 0x0123400
FS = 40e6

#: Noise power in 1 MHz, the one floor of every file.
NOISE_1MHZ = bt_synth.AMPLITUDE ** 2 / 100

#: Samples in a block of noise. A float64 pair of blocks is 64 MB.
BLOCK_SAMPLES = 2 ** 22

SYNC_START = 4                             # the sync word within the access code
SYNC_BITS = 64
ERROR_MODES = ('mixed', 'first1', 'none')

SYNC_ERROR_NOTE = (
    "sync_errors bits of the 64-bit sync word are flipped in the modulated bits of the burst. "
    "The access code is 72 air bits: 4 preamble, 64 sync word, 4 trailer. sync_error_bits are "
    "positions 0..63 INSIDE the sync word, so the air bit is 4 + position; the preamble and the "
    "trailer are as the correct packet has them, and the header and payload are unchanged. "
    "burst 0 of a mixed or first1 file always has exactly one error: the first burst a receiver "
    "sees from this LAP is errored, which it cannot yet know is this LAP's.")


def amplitude_for(snr_db):
    """|iq| of a constant-envelope burst of ``snr_db`` over the floor in 1 MHz."""
    return float(np.sqrt(NOISE_1MHZ * 10 ** (snr_db / 10)))


def noise_block(seed, index, n, fs):
    """Block ``index`` of the file's noise: ``n`` complex64 samples, white across
    the whole sample rate, from a generator of that block alone."""
    rng = np.random.default_rng([seed, 1, index])
    sigma = np.sqrt(NOISE_1MHZ * fs / 1e6 / 2)
    out = np.empty(n, dtype=np.complex64)
    out.real = rng.normal(0, sigma, n)
    out.imag = rng.normal(0, sigma, n)
    return out


def add_noise(iq, seed, fs, block_samples=BLOCK_SAMPLES):
    """Add the file's noise to ``iq`` in place, a block at a time."""
    for index, lo in enumerate(range(0, len(iq), block_samples)):
        hi = min(lo + block_samples, len(iq))
        iq[lo:hi] += noise_block(seed, index, hi - lo, fs)


def error_plan(errors, bursts, seed):
    """Per burst, the sorted sync-word positions to flip. ``mixed``: burst 0 one,
    the others 0, 1 or 2 with equal probability; ``first1``: burst 0 one, no
    other; ``none``: no error. Positions are uniform over the 64 bits and
    distinct within a burst."""
    if errors not in ERROR_MODES:
        raise ValueError("errors is one of %s, not %r" % (', '.join(ERROR_MODES), errors))
    rng = np.random.default_rng([seed, 3])
    plan = []
    for k in range(bursts):
        if errors == 'none' or (errors == 'first1' and k > 0):
            n = 0
        elif k == 0:
            n = 1
        else:
            n = int(rng.integers(0, 3))
        plan.append(sorted(int(p) for p in rng.choice(SYNC_BITS, n, replace=False)) if n else [])
    return plan


def flip_sync(bits, positions):
    """The packet's air bits with the sync-word ``positions`` (0..63) flipped."""
    bits = list(bits)
    for p in positions:
        bits[SYNC_START + p] ^= 1
    return bits


def synthesise_sync(bursts=800, errors='mixed', seed=5001, snr_db=20.0, lap=LAP, uap=UAP,
                    clk0=CLK0, fs=FS, center_mhz=CENTER_MHZ, channels=MAP,
                    block_samples=BLOCK_SAMPLES):
    """The samples and the sidecar of a hopping DH1 train with sync-word errors."""
    if bursts < 1:
        raise ValueError("a capture needs at least one burst")
    if clk0 % 4:
        raise ValueError("clk0 must be a master slot's clock: CLK1-0 = 00")
    plan_errors = error_plan(errors, bursts, seed)
    ptype = 'DH1'
    slots = br.PACKET_TYPES[ptype][1]
    period = slots + 1                              # the slave's slot after it: 2
    slot_samples = fs * br.SLOT_US * 1e-6
    if abs(slot_samples - round(slot_samples)) > 1e-6:
        raise ValueError("%g S/s does not divide a 625 us slot" % fs)
    slot_samples = int(round(slot_samples))
    sps = int(round(fs / br.SYMBOL_RATE))
    phases = [0, sps // 2]
    channels = hop.map_channels(channels)
    for channel in channels:
        if hop.outside_window(channel, fs, center_mhz):
            raise ValueError("channel %d, %g MHz, is outside the window of %g MS/s on %g MHz"
                             % (channel, hop.channel_mhz(channel), fs / 1e6, center_mhz))
    # The map applies from burst 0, as in synthesise_afh: the instant is the
    # CLK[27:1] of burst 0's clock.
    instant = (clk0 & 0x0FFFFFFF) >> 1
    hop_fn = hop.afh_hop_fn([(instant, channels)], lap, uap)
    plan = hop.hop_plan(hop_fn, bursts, clk0, period, fs, center_mhz)

    rng = np.random.default_rng([seed, 2])
    amp = amplitude_for(snr_db)
    # The grid is half a slot off sample 0, as in bt_synth; burst k then sits at
    # symbol phase 0 or sps/2 by parity, which is the grid less half a symbol
    # on the even bursts.
    grid = int(bt_synth.LEAD_SLOTS * slot_samples)
    grid -= grid % sps
    total = grid + bursts * period * slot_samples + slot_samples
    iq = np.zeros(total, dtype=np.complex64)
    entries = []
    for k, (clk, channel) in enumerate(plan):
        body = br.seq_body(k, br.PACKET_TYPES[ptype][4])
        p = br.Packet(lap, uap, clk, ptype, body, lt_addr=1, flow=1, arqn=1, seqn=k & 1)
        phase = phases[k % 2]
        start = grid + k * period * slot_samples + phase
        burst_phase = rng.uniform(0, 2 * np.pi)
        timing_frac = float(rng.uniform(0, 1))
        bits = flip_sync(p.bits, plan_errors[k])
        burst, lead = br.gfsk(bits, fs, h=br.GFSK_H, delay=timing_frac)
        burst = burst * np.exp(1j * burst_phase)
        lo = start - lead
        # The one oscillator at the absolute sample index, as bt_synth_hop does.
        n = np.arange(lo, lo + len(burst))
        cycles = (hop.channel_mhz(channel) - center_mhz) * 1e6 / fs * n
        burst = burst * np.exp(2j * np.pi * (cycles - np.floor(cycles)))
        iq[lo:lo + len(burst)] += (amp * burst).astype(np.complex64)
        entry = {'start_sample': int(start), 'timing_frac': timing_frac}
        entry.update(p.sidecar())
        del entry['air_bits']
        entry.update(channel=int(channel), channel_mhz=hop.channel_mhz(channel),
                     symbol_phase=int(start % sps), snr_db=float(snr_db),
                     sync_errors=len(plan_errors[k]), sync_error_bits=plan_errors[k],
                     afh_map_index=0)
        entries.append(entry)
    add_noise(iq, seed, fs, block_samples)

    sidecar = {
        'generator': 'SDR scripts/bt_synth_sync.py',
        'generator_commit': bt_synth.commit(),
        'lap': lap,
        'uap': uap,
        'clk': plan[0][0],
        'clk_convention': 'native CLK[27:0], at the first sample of the burst '
                          'it is given for; top-level clk is burst 0\'s',
        'hopping': True,
        'hop_channels': sorted({channel for _, channel in plan}),
        'channel_mhz': None,
        'bt_channel': None,
        'sample_rate': fs,
        'center_mhz': center_mhz,
        'cfo_hz': 0.0,
        'timing_frac': None,
        'timing_frac_meaning': 'null at the top: every burst has its own, uniform in [0, 1)',
        'snr_db': float(snr_db),
        'snr_bw_hz': 1e6,
        'noise_1mhz': NOISE_1MHZ,
        'modulation': {'scheme': 'GFSK', 'bt': br.GFSK_BT, 'h': br.GFSK_H,
                       'symbol_rate': br.SYMBOL_RATE},
        'slot_samples': slot_samples,
        'start_offset': 0,
        'symbol_phase': None,
        'symbol_phases': phases,
        'symbol_phase_meaning': 'null at the top: alternating per burst, even bursts at phase 0, '
                                'odd at sps/2; each burst has its own',
        'per_burst_keys': sorted(entries[0]),
        'start_sample_meaning': 'the first sample of the access code\'s '
                                'preamble, before timing_frac is added',
        'air_bits_omitted': True,
        'grid_note': 'the master slot grid is NOT exactly half a slot off sample 0: even bursts '
                     'start at sample 62480 + 50000 k (slot offset 12480, the grid rounded down to '
                     'a whole symbol so phase 0 is a whole symbol), odd bursts 20 samples later '
                     '(slot offset 12500, exactly half a slot, symbol phase 20)',
        'access_code_layout': {'preamble': 4, 'sync_word': 64, 'trailer': 4},
        'sync_error_mode': errors,
        'sync_error_note': SYNC_ERROR_NOTE,
        'afh_map': channels,
        'afh_instant': instant,
        'afh_map_count': 1,
        'afh_maps': [{'instant': instant, 'first_burst': 0, 'channels': channels}],
        'afh_instant_meaning': hop.INSTANT_MEANING,
        'hop_kernel': hop.HOP_KERNEL,
        'address_for_hop': uap << 24 | lap,
        'clock_lock_note': hop.CLOCK_LOCK_NOTE,
        'seed': seed,
        'bursts': entries,
    }
    return iq, sidecar


def noise_only_sidecar(duration_s=120.0, fs=20e6, seed=5004, block_samples=BLOCK_SAMPLES,
                       center_mhz=CENTER_MHZ):
    """The sidecar of a noise-only file, without rendering it."""
    total = int(round(duration_s * fs))
    if total < 1:
        raise ValueError("duration_s %g is no samples at %g S/s" % (duration_s, fs))
    slot_samples = fs * br.SLOT_US * 1e-6
    return {
        'generator': 'SDR scripts/bt_synth_sync.py',
        'generator_commit': bt_synth.commit(),
        'sample_rate': fs,
        'center_mhz': center_mhz,
        'noise_only': True,
        'duration_s': duration_s,
        'n_samples': total,
        'noise_1mhz': NOISE_1MHZ,
        'noise_power_1mhz': NOISE_1MHZ,
        'snr_bw_hz': 1e6,
        'noise_block_samples': block_samples,
        'noise_note': 'complex Gaussian noise, per-component sigma = sqrt(noise_1mhz * (fs / 1e6) / 2) '
                      'in float64: the same noise per MHz as the 20 dB hopping files. Recipe: block i '
                      'of noise_block_samples samples is numpy default_rng([seed, 1, i]); I is drawn '
                      'first as normal(0, sigma, n), then Q as normal(0, sigma, n), and the pair is '
                      'cast to complex64. The last block is partial and draws its own n, so a '
                      'shorter file is not a prefix of a longer one. No burst; there is no LAP or UAP.',
        'slot_samples': int(round(slot_samples)),
        'start_sample_meaning': 'there are no bursts',
        'air_bits_omitted': True,
        'seed': seed,
        'bursts': [],
    }


def write_noise_only(path, duration_s=120.0, fs=20e6, seed=5004, block_samples=BLOCK_SAMPLES,
                     center_mhz=CENTER_MHZ):
    """Write ``duration_s`` of noise alone to ``path`` (cf32), a block at a time,
    and return its sidecar. Nothing but one block is ever in memory."""
    sidecar = noise_only_sidecar(duration_s, fs, seed, block_samples, center_mhz)
    total = sidecar['n_samples']
    with open(path, 'wb') as f:
        for index, lo in enumerate(range(0, total, block_samples)):
            n = min(block_samples, total - lo)
            noise_block(seed, index, n, fs).astype('<c8', copy=False).tofile(f)
    return sidecar


# --- the files -----------------------------------------------------------------

#: What ``--set sync`` writes, and nothing else. The three hopping files share
#: one seed: they are a paired set, differing only in the planted bits. A burst file is the arguments
#: of ``synthesise_sync``; the noise file is ``write_noise_only``'s.
SYNC_SET = [
    ('hop20_dh1_syncerr_mixed', dict(errors='mixed', bursts=800, seed=5001)),
    ('hop20_dh1_syncerr_first1', dict(errors='first1', bursts=800, seed=5001)),
    ('hop20_dh1_syncerr_clean', dict(errors='none', bursts=800, seed=5001)),
    ('noise_only_20msps_120s', dict(noise_only=True, duration_s=120.0, fs=20e6, seed=5004)),
]
SETS = {'sync': SYNC_SET}


def write_file(name, out_dir, noise_only=False, json_only=False, **spec):
    """Make one file and write ``synth_<name>.cf32`` and ``.json`` into ``out_dir``."""
    os.makedirs(out_dir, exist_ok=True)
    iq_path = os.path.join(out_dir, 'synth_%s.cf32' % name)
    side_path = os.path.join(out_dir, 'synth_%s.json' % name)
    if noise_only and json_only:
        sidecar = noise_only_sidecar(**spec)
        print('%s  (sidecar only, %d samples, %.1f s of noise not rendered)' % (
            side_path, sidecar['n_samples'], sidecar['duration_s']))
    elif noise_only:
        sidecar = write_noise_only(iq_path, **spec)
        print('%s  %d samples, %.1f s, noise only' % (iq_path, sidecar['n_samples'],
                                                      sidecar['duration_s']))
    else:
        iq, sidecar = synthesise_sync(**spec)
        iq.astype('<c8', copy=False).tofile(iq_path)
        counts = [sum(e['sync_errors'] == n for e in sidecar['bursts']) for n in (0, 1, 2)]
        print('%s  %d samples, %.1f ms, %d DH1 bursts, errors 0/1/2: %d/%d/%d' % (
            iq_path, len(iq), len(iq) / sidecar['sample_rate'] * 1e3, len(sidecar['bursts']),
            *counts))
    with open(side_path, 'w') as f:
        json.dump(sidecar, f, indent=1)
    print(side_path)
    return iq_path, side_path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('name', nargs='?', help='the file is synth_<name>.cf32; not used with --set')
    ap.add_argument('--set', choices=sorted(SETS), help='write the whole set, with the seeds and '
                    'settings of its table, and no other option but --out')
    ap.add_argument('--bursts', type=int, help='default 800')
    ap.add_argument('--errors', choices=ERROR_MODES, help='default mixed')
    ap.add_argument('--seed', type=int, help='default 5001 (5004 for --noise-only)')
    ap.add_argument('--snr', type=float, help='dB in 1 MHz; default 20')
    ap.add_argument('--clk0', type=lambda text: int(text, 0), help='default 0x0123400')
    ap.add_argument('--noise-only', action='store_true', help='no bursts: noise alone')
    ap.add_argument('--duration-s', type=float, help='with --noise-only; default 120')
    ap.add_argument('--fs', type=float, help='with --noise-only; default 20e6')
    ap.add_argument('--json-only', action='store_true', help='with --noise-only: write the sidecar '
                    'alone, without rendering the samples')
    ap.add_argument('--block-samples', type=int, help='samples of noise per block; default 2**22')
    ap.add_argument('--out', default='/media/user/4TB/sdr-synth-tmp', help='the folder of both files')
    args = ap.parse_args(argv)

    per_file = ['bursts', 'errors', 'seed', 'snr', 'clk0', 'duration_s', 'fs', 'block_samples']
    if args.set:
        stray = [o for o in per_file if getattr(args, o) is not None]
        if args.name is not None or args.noise_only or args.json_only or stray:
            ap.error('--set takes everything from its table: no name and no option but --out')
        runs = SETS[args.set]
    else:
        if args.name is None:
            ap.error('give the name of the file, or --set')
        block = args.block_samples or BLOCK_SAMPLES
        if args.noise_only:
            if args.bursts is not None or args.errors or args.snr is not None or args.clk0 is not None:
                ap.error('--noise-only has no bursts, errors, snr or clock')
            spec = dict(noise_only=True, duration_s=args.duration_s or 120.0, fs=args.fs or 20e6,
                        seed=5004 if args.seed is None else args.seed, block_samples=block,
                        json_only=args.json_only)
        else:
            if args.duration_s is not None or args.fs is not None or args.json_only:
                ap.error('--duration-s, --fs and --json-only are for --noise-only')
            spec = dict(bursts=args.bursts or 800, errors=args.errors or 'mixed',
                        seed=5001 if args.seed is None else args.seed, block_samples=block)
            if args.snr is not None:
                spec['snr_db'] = args.snr
            if args.clk0 is not None:
                spec['clk0'] = args.clk0
        runs = [(args.name, spec)]
    try:
        for name, spec in runs:
            write_file(name, args.out, **spec)
    except ValueError as e:
        ap.error(str(e))


if __name__ == '__main__':
    main()
