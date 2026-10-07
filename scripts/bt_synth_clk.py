#!/usr/bin/env python3
"""The clean DH5 train as heard by a receiver whose sample clock is off by a known ppm.

    python scripts/bt_synth_clk.py hop20_dh5_clk_p100 --eps 100 --out DIR
    python scripts/bt_synth_clk.py hop20_dh5_clklo_p100 --eps 100 --lo --out DIR
    python scripts/bt_synth_clk.py --set clk    [--out DIR]     # 8 files, flavour 1
    python scripts/bt_synth_clk.py --set clklo  [--out DIR]     # 6 files, flavour 2

For bluey-ox-walker's symbol-timing loop, which wants to measure timing drift
against TRUTH: the bursts of ``hop20_dh5_int_clean`` of ``bt_synth_interf.py``
(the same 800: channels, payloads, clocks, ``timing_frac``, symbol phases, levels,
seed 6101) seen by a receiver whose sample clock runs at ``fs * (1 + eps * 1e-6)``,
fs = 40 MS/s. **eps > 0: the receiver's clock is FAST.** It takes more samples per
symbol (``40 * (1 + eps e-6)``), the file is longer by (1 + eps e-6), and the burst
that began at nominal sample ``s`` begins at ``s * (1 + eps e-6)``. The transmitter is
exact: 1 Msym/s, a 625 us slot. Output sample ``m`` is at nominal time
``m / (1 + eps e-6)`` samples. Nothing is transmitted.

**Flavour 1 (``clk``)**: the sample clock only. The receiver's local oscillator is not
offset, so a baseband carrier is the same number of true Hz as in the clean file: a
channel at +4 MHz is ``4e6 / (fs (1 + eps e-6))`` cycles per output sample.

**Flavour 2 (``clklo``)**: one crystal drives both the sample clock and the LO, so the
same eps also moves the LO, and every baseband carrier is shifted by
``lo_offset_hz = -eps e-6 * f_lo``, f_lo = 2441.0 MHz (-244.1 kHz at +100 ppm: a
receiver whose clock is fast has its LO high, so the signal appears LOWER). It is the
same shift for every burst, applied as one continuous phase
``exp(-j 2 pi eps e-6 f_lo t)`` over the whole file, ``t = m / fs_true`` seconds from
the file start, so no burst and no noise block boundary restarts it.

**How the samples are made: direct rendering, no resampling.** ``br.gfsk`` takes a
float sample rate (``sps = fs / 1e6``) and evaluates its Gaussian pulses and
raised-cosine ramps where the samples fall, so each burst is rendered at the
receiver's own rate ``fs_true = 40e6 * (1 + eps e-6)`` with the delay
``timing_frac`` of its output position. Nothing is interpolated, so there is no
interpolation error to state; ``test_bt_synth_clk.py`` also compares one burst with a
windowed-sinc resample of the clean file's burst (the residual, in dB, is in its
output). Per burst, in this order, as in ``bt_synth_interf.py``: the nominal position
``S = start_sample + timing_frac`` (the clean file's) goes to the output position
``S * (1 + eps e-6)``, whose integer part is ``start_sample`` and fraction
``timing_frac`` here; the burst is rendered at ``fs_true``, multiplied by its random
phase and by the channel's carrier at the OUTPUT sample times, ``exp(j 2 pi f_ch m /
fs_true)`` with ``m`` the absolute output sample number (whole cycles removed first),
and for flavour 2 by the LO's ``exp(j 2 pi lo_offset_hz m / fs_true)`` likewise.
At eps = 0, flavour 1, these are the very operations of ``bt_synth_interf.synthesise_interf``
and the samples are its clean file's byte for byte.

**The noise** is added after the bursts, white and unscaled: the very per-block
stream of the clean file, ``bt_synth_interf.noise_block(seed, block, n, 40e6)``, block
size and all (one exception: at eps != 0 the last block has another length, and ``noise_block``
draws all its real parts and then all its imaginary parts, so that block's imaginary parts are
another realisation; its real parts, and every other block, are the clean file's). It is NOT resampled and not scaled by eps: a receiver's noise is white
at its own sample rate, so its per-sample variance is the clean file's and its noise
per MHz, ``noise_1mhz``, is by true bandwidth; the receiver's bandwidth is wider by
eps e-6 (under 0.02 % at 200 ppm), which changes the noise per MHz by that much and
nothing visible.

**Length.** ``n_samples = round(clean_length * (1 + eps e-6))``; the file is
``8 * n_samples`` bytes.

**Per-burst truth** (beside the clean file's keys): ``start_sample`` (an integer) and
``timing_frac`` in OUTPUT time - ``start_sample + timing_frac`` is the burst's first
preamble sample position in the file; ``start_exact_nominal``, the clean file's
``start_sample + timing_frac``; ``symbol_phase_samples``, ``start_sample +
timing_frac`` modulo ``samples_per_symbol_true``; ``symbol_phase``, its integer part;
``carrier_offset_hz``, the burst's baseband carrier in true Hz, LO shift included;
``air_bits_length`` (``air_bits`` is left out: ``air_bits_omitted``).
"""
import argparse
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from scripts import bt_synth, bt_synth_between, bt_synth_hop as hop, bt_synth_interf as gi  # noqa: E402

FS = gi.FS
CENTER_MHZ = gi.CENTER_MHZ
SEED = gi.SEED
BURSTS = gi.BURSTS
SNR_DB = gi.SNR_DB
NOISE_1MHZ = gi.NOISE_1MHZ
BLOCK_SAMPLES = gi.BLOCK_SAMPLES
PAIRED_WITH = 'hop20_dh5_int_clean'

#: The files. Flavour 1 (``clk``) and flavour 2 (``clklo``): eps in ppm.
CLK_EPS = [20, -20, 50, -50, 100, -100, 200, -200]
CLKLO_EPS = [20, -20, 50, -50, 100, -100]


def eps_tag(eps):
    return ('p%g' if eps >= 0 else 'm%g') % abs(eps)


# --- the clock and the LO (the test patches these to make its mutants) -----------

def sample_ratio(eps_ppm):
    """``fs_true / fs``: what the SAMPLES are made with."""
    return 1.0 + eps_ppm * 1e-6


def truth_ratio(eps_ppm):
    """``fs_true / fs``: what the SIDECAR's positions and rates are made with."""
    return 1.0 + eps_ppm * 1e-6


def lo_offset_hz(eps_ppm, lo, f_lo_hz):
    """The shift of every baseband carrier: ``-eps e-6 * f_lo`` in flavour 2, else 0."""
    return -eps_ppm * 1e-6 * f_lo_hz if lo else 0.0


def carrier_cycles(f_hz, m, fs_true):
    """Cycles of a carrier of ``f_hz`` true Hz at output samples ``m``, whole cycles removed."""
    cycles = f_hz / fs_true * m
    return cycles - np.floor(cycles)


def lo_cycles(f_hz, m, fs_true, block_samples):
    """Cycles of the LO shift at output samples ``m``: one phase over the whole file.

    ``block_samples`` is deliberately unused: the LO phase must be continuous across noise
    blocks (``m`` is the absolute output sample number). Restarting it per block would break
    flavour 2; the test's phase-restart mutant is exactly that."""
    cycles = f_hz / fs_true * m
    return cycles - np.floor(cycles)


def burst_position(start, timing_frac, ratio):
    """``(start_sample, timing_frac)`` in output time of a burst the clean file has at
    ``start + timing_frac``. The integer start is multiplied apart from the fraction so
    that at ratio 1 the clean file's own pair comes back exactly."""
    a = start * ratio
    whole = math.floor(a)
    frac = (a - whole) + timing_frac * ratio
    if frac >= 1.0:
        whole += 1
        frac -= 1.0
    return int(whole), float(frac)


def output_length(total, ratio):
    return int(round(total * ratio))


def add_noise(iq, seed, fs, block_samples):
    """The clean file's floor, block by block, added after the bursts."""
    for index, lo in enumerate(range(0, len(iq), block_samples)):
        hi = min(lo + block_samples, len(iq))
        iq[lo:hi] += gi.noise_block(seed, index, hi - lo, fs)


# --- the file -------------------------------------------------------------------

NOTE = (
    "Modelled: a CONSTANT sample-clock offset eps_ppm of the receiver (fs_true = 40e6 * (1 + eps_ppm * 1e-6)) against "
    "a transmitter that is exact (1 Msym/s, 625 us slots), the same offset for the whole file. eps_ppm > 0 means the "
    "receiver's clock is FAST: samples_per_symbol_true = 40 * (1 + eps_ppm * 1e-6) samples per symbol, the file is "
    "longer by that factor, and burst k begins at start_exact_nominal * (1 + eps_ppm * 1e-6). In clk files the "
    "receiver's LO is not offset (lo_offset_hz 0): every carrier is the clean file's, in true Hz. In clklo files one "
    "crystal drives both, so every baseband carrier of every burst is shifted by lo_offset_hz = -eps_ppm * 1e-6 * "
    "2441e6 Hz, the same for all bursts, as one continuous phase from the file start. Noise: the clean file's own "
    "white floor (block i of noise_block(seed, i, ...); the last block, whose length changes with eps, is the one "
    "place its stream differs from the clean file's), unscaled and not resampled (per-sample variance independent of eps). NOT modelled: any drift of the "
    "offset itself, jitter or phase noise of the clock, temperature, a transmitter offset, a Doppler. "
    "end_sample is an integer: the floor of the continuous endpoint start_sample + timing_frac + air_bits_length * "
    "samples_per_symbol_true. "
    "paired_with names the clean file whose bursts these are, only when the recipe is the delivered one.")

LO_FORMULA = ("lo_offset_hz = -eps_ppm * 1e-6 * f_lo, f_lo = 2441e6 Hz (clklo files; 0 in clk files); every baseband "
              "carrier is f_ch + lo_offset_hz in true Hz, applied as exp(j 2 pi lo_offset_hz * m / fs_true), m the "
              "output sample number from the file start (continuous, no restart at a noise block); a clock that is "
              "fast (eps_ppm > 0) has its LO high, so the signal appears LOWER in baseband")

RESAMPLING = ("none: each burst is rendered directly at the receiver's sample times with bt_br_frame.gfsk at "
              "fs_true = 40e6 * (1 + eps_ppm * 1e-6) (non-integer samples per symbol, the same Gaussian pulses and "
              "raised-cosine ramps evaluated where the samples fall) with delay = timing_frac in output time; the "
              "channel carrier and the LO shift are applied afterwards, analytically, at the output sample times; "
              "the noise is added last, unresampled")

APPARENT_RATE_NOTE = (
    "A receiver that takes the sample rate to be 40e6 sees a symbol last 40 * (1 + eps_ppm * 1e-6) samples = (1 + eps_ppm "
    "* 1e-6) us, i.e. 1e6 / (1 + eps_ppm * 1e-6) symbols per second: SLOWER when its clock is fast. "
    "apparent_symbol_rate_hz_if_fs_assumed_40e6 is that number")


def synthesise_clk(eps_ppm=0.0, lo=False, bursts=BURSTS, seed=SEED, block_samples=BLOCK_SAMPLES,
                   channels=gi.MAP, center_mhz=CENTER_MHZ, snr_db=SNR_DB, lap=gi.LAP, uap=gi.UAP,
                   clk0=gi.CLK0):
    """The samples and the sidecar of the clean train seen by a clock ``eps_ppm`` off
    (``lo``: the LO moves with it, flavour 2)."""
    fs = FS
    if bursts < 1:
        raise ValueError("a capture needs at least one burst")
    if clk0 % 4:
        raise ValueError("clk0 must be a master slot's clock: CLK1-0 = 00")
    ptype = gi.PTYPE
    slots = br.PACKET_TYPES[ptype][1]
    period = slots + 1
    slot_samples = int(round(fs * br.SLOT_US * 1e-6))
    sps = int(round(fs / br.SYMBOL_RATE))
    phases = [0, sps // 2]
    channels = hop.map_channels(channels)
    for channel in channels:
        if hop.outside_window(channel, fs, center_mhz):
            raise ValueError("channel %d is outside the window of %g MS/s on %g MHz" % (channel, fs / 1e6, center_mhz))
    instant = (clk0 & 0x0FFFFFFF) >> 1
    hop_fn = hop.afh_hop_fn([(instant, channels)], lap, uap)
    plan = hop.hop_plan(hop_fn, bursts, clk0, period, fs, center_mhz)

    g = sample_ratio(eps_ppm)                         # what the samples are made with
    gt = truth_ratio(eps_ppm)                         # what the sidecar says
    fs_true = fs * g
    f_lo = center_mhz * 1e6
    lo_hz = lo_offset_hz(eps_ppm, lo, f_lo)

    rng = np.random.default_rng([seed, 2])
    amp = gi.amplitude_for(snr_db)
    grid = int(bt_synth.LEAD_SLOTS * slot_samples)
    grid -= grid % sps
    total_nominal = grid + bursts * period * slot_samples + slot_samples
    total = output_length(total_nominal, g)
    iq = np.zeros(total, dtype=np.complex64)
    entries = []
    for k, (clk, channel) in enumerate(plan):
        body = br.seq_body(k, br.PACKET_TYPES[ptype][4])
        p = br.Packet(lap, uap, clk, ptype, body, lt_addr=1, flow=1, arqn=1, seqn=k & 1)
        phase = phases[k % 2]
        start_nom = grid + k * period * slot_samples + phase
        burst_phase = rng.uniform(0, 2 * np.pi)
        timing_frac_nom = float(rng.uniform(0, 1))
        start, timing_frac = burst_position(start_nom, timing_frac_nom, g)
        burst, lead = br.gfsk(p.bits, fs_true, h=br.GFSK_H, delay=timing_frac)
        burst = burst * np.exp(1j * burst_phase)
        lo_idx = start - lead
        m = np.arange(lo_idx, lo_idx + len(burst))
        f_ch = (hop.channel_mhz(channel) - center_mhz) * 1e6
        burst = burst * np.exp(2j * np.pi * carrier_cycles(f_ch, m, fs_true))
        if lo_hz:
            burst = burst * np.exp(2j * np.pi * lo_cycles(lo_hz, m, fs_true, block_samples))
        iq[lo_idx:lo_idx + len(burst)] += (amp * burst).astype(np.complex64)

        # the truth, from the ratio the sidecar states
        t_start, t_frac = burst_position(start_nom, timing_frac_nom, gt)
        sps_true = sps * gt
        pos = t_start + t_frac
        entry = {'start_sample': t_start, 'timing_frac': t_frac,
                 'start_exact_nominal': start_nom + timing_frac_nom}
        entry.update(p.sidecar())
        del entry['air_bits']
        entry.update(air_bits_length=len(p.bits), end_sample=int(math.floor(pos + len(p.bits) * sps_true)),
                     channel=int(channel), channel_mhz=hop.channel_mhz(channel),
                     symbol_phase_samples=float(pos % sps_true), symbol_phase=int(pos % sps_true),
                     carrier_offset_hz=f_ch + lo_offset_hz(eps_ppm, lo, f_lo),
                     snr_db=float(snr_db), afh_map_index=0, interferer_overlap=False,
                     interferer_power_in_band_db=None)
        entries.append(entry)
    add_noise(iq, seed, fs, block_samples)

    lo_truth = lo_offset_hz(eps_ppm, lo, f_lo)
    paired = bt_synth_between.is_canonical(seed, block_samples, dict(
        bursts=bursts, channels=list(channels), center_mhz=center_mhz, snr_db=snr_db, lap=lap, uap=uap, clk0=clk0))
    sidecar = {
        'generator': 'SDR scripts/bt_synth_clk.py',
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
        'cfo_hz': lo_truth,
        'cfo_hz_meaning': 'the same number as lo_offset_hz (0 in clk files), in bt_synth.py\'s sense: every burst\'s '
                          'baseband carrier is its channel offset + cfo_hz, in true Hz',
        'timing_frac': None,
        'timing_frac_meaning': 'null at the top: every burst has its own, in OUTPUT time, in [0, 1)',
        'snr_db': float(snr_db),
        'snr_bw_hz': 1e6,
        'noise_1mhz': NOISE_1MHZ,
        'modulation': {'scheme': 'GFSK', 'bt': br.GFSK_BT, 'h': br.GFSK_H, 'symbol_rate': br.SYMBOL_RATE},
        'slot_samples': slot_samples,
        'slot_samples_true': slot_samples * gt,
        'slot_samples_meaning': 'slot_samples is the nominal 25000; slot_samples_true is what the receiver counts '
                                'in one true 625 us slot',
        'start_offset': 0,
        'symbol_phase': None,
        'symbol_phases': phases,
        'symbol_phase_meaning': 'null at the top: per burst, symbol_phase_samples = (start_sample + timing_frac) '
                                'modulo samples_per_symbol_true, which is (0 or 20, + the clean timing_frac) * '
                                '(1 + eps e-6); symbol_phase is its integer part',
        'per_burst_keys': sorted(entries[0]),
        'start_sample_meaning': 'the first sample of the access code\'s preamble in the OUTPUT file, before '
                                'timing_frac is added; start_sample + timing_frac is a real sample position',
        'air_bits_omitted': True,
        'interferer_name': 'clean',
        'interferers': [],
        'n_bursts_on_centre_channel': sum(e['channel'] == 39 for e in entries),
        'n_bursts_overlapped': 0,
        'afh_map': channels,
        'afh_instant': instant,
        'afh_map_count': 1,
        'afh_maps': [{'instant': instant, 'first_burst': 0, 'channels': channels}],
        'afh_instant_meaning': hop.INSTANT_MEANING,
        'hop_kernel': hop.HOP_KERNEL,
        'address_for_hop': uap << 24 | lap,
        'clock_lock_note': gi.CLOCK_LOCK_NOTE_INTERF,
        'seed': seed,
        'noise_block_samples': block_samples,
        'flavour': 'clklo' if lo else 'clk',
        'eps_ppm': float(eps_ppm),
        'sample_clock': {'nominal_hz': fs, 'true_hz': fs * gt, 'eps_ppm': float(eps_ppm),
                         'meaning': 'eps > 0 = the receiver sample clock is fast: the true sample rate is '
                                    'nominal_hz * (1 + eps_ppm * 1e-6), more samples per symbol, a longer file'},
        'samples_per_symbol_true': sps * gt,
        'symbol_rate_hz': br.SYMBOL_RATE,
        'apparent_symbol_rate_hz_if_fs_assumed_40e6': br.SYMBOL_RATE / gt,
        'apparent_symbol_rate_note': APPARENT_RATE_NOTE,
        'lo_offset_hz': lo_truth,
        'lo_offset_formula': LO_FORMULA,
        'resampling': RESAMPLING,
        'n_samples': int(len(iq)),
        'paired_with': PAIRED_WITH if paired else None,
        'clk_note': NOTE,
    }
    sidecar['bursts'] = entries
    return iq, sidecar


# --- the files ------------------------------------------------------------------

def _set(prefix, lo, eps_list):
    return [('hop20_dh5_%s_%s' % (prefix, eps_tag(e)), dict(eps_ppm=e, lo=lo)) for e in eps_list]


SETS = {'clk': _set('clk', False, CLK_EPS), 'clklo': _set('clklo', True, CLKLO_EPS)}


def write_file(name, out_dir, **spec):
    """Make one file and write ``synth_<name>.cf32`` and ``.json`` into ``out_dir``."""
    os.makedirs(out_dir, exist_ok=True)
    iq_path = os.path.join(out_dir, 'synth_%s.cf32' % name)
    side_path = os.path.join(out_dir, 'synth_%s.json' % name)
    iq, sidecar = synthesise_clk(**spec)
    iq.astype('<c8', copy=False).tofile(iq_path)
    print('%s  %d samples, %.4f s, eps %+g ppm, lo offset %.1f Hz, %d bursts' % (
        iq_path, len(iq), len(iq) / sidecar['sample_clock']['true_hz'], sidecar['eps_ppm'],
        sidecar['lo_offset_hz'], len(sidecar['bursts'])), flush=True)
    with open(side_path, 'w') as f:
        json.dump(sidecar, f, indent=1)
    print(side_path)
    return iq_path, side_path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('name', nargs='?', help='the file is synth_<name>.cf32; not used with --set')
    ap.add_argument('--set', help='write the whole set and no other option but --out; one of %s'
                    % ', '.join(sorted(SETS)))
    ap.add_argument('--eps', type=float, help='the receiver clock offset in ppm (> 0: fast)')
    ap.add_argument('--lo', action='store_true', help='flavour 2: the LO moves with the clock')
    ap.add_argument('--bursts', type=int, help='default %d' % BURSTS)
    ap.add_argument('--seed', type=int, help='default %d' % SEED)
    ap.add_argument('--out', default='/media/user/4TB/sdr-synth-tmp', help='the folder of both files')
    args = ap.parse_args(argv)
    if args.set:
        if args.set not in SETS:
            ap.error('no set %r: the sets are %s' % (args.set, ', '.join(sorted(SETS))))
        if args.name is not None or args.eps is not None or args.lo or args.bursts or args.seed is not None:
            ap.error('--set takes everything from its table: no name and no option but --out')
        runs = SETS[args.set]
    else:
        if args.name is None or args.eps is None:
            ap.error('give the name of the file and --eps, or --set')
        runs = [(args.name, dict(eps_ppm=args.eps, lo=args.lo, bursts=args.bursts or BURSTS,
                                 seed=SEED if args.seed is None else args.seed))]
    try:
        for name, spec in runs:
            write_file(name, args.out, **spec)
    except ValueError as e:
        ap.error(str(e))


if __name__ == '__main__':
    main()
