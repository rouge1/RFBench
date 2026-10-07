#!/usr/bin/env python3
"""The fade files' bursts through a frequency-selective channel: a tapped delay line with Rayleigh paths.

    python scripts/bt_synth_multipath.py mp_p2b_fd300_snr24 --profile p2b --fd 300 --snr 24 --out DIR
    python scripts/bt_synth_multipath.py --set mp [--out DIR]

    from scripts import bt_synth_multipath
    iq, sidecar = bt_synth_multipath.synthesise_multipath('p3', snr_db=18.0, fd_hz=300.0, bursts=24)

For bluey-ox-walker's CRC-assisted bit correction (its batch 2, item 2, after the
flat fades of ``bt_synth_fade.py``). A real indoor channel is frequency
selective: a 1 Msym/s GFSK burst with 0.2 to 1 us of delay spread sees
inter-symbol interference as well as a fade. These files give the same bursts
as the ``fade/`` set a tapped delay line of 2 to 4 Rayleigh paths. Nothing is
transmitted. The plan is in [knowledge/bluey-test-signals.md](../knowledge/bluey-test-signals.md).

**What a file is.** The same 204 bursts, channels, payloads, clocks, timing
fractions, carrier phases and noise as ``fade_ref_snr18`` and ``fade_ref_snr24``
(seed 9101; the plan, the packets and the noise are ``bt_synth_fade``'s, called, not
copied: the sidecar's per-burst keys are the reference's first, then overwritten
where the channel changes them). The distortion replaces ``h(t) * s(t)`` of the
flat fade by::

    y(t) = A * sum_k a_k h_k(t) s(t - tau_k)

``s`` is the burst's baseband signal as the generator makes it (the GFSK burst with
its timing fraction, the burst phase, the carrier of its channel at the absolute
sample index), ``tau_k`` the tap delays, relative to the burst's own start, the
first at 0. ``s(t - tau_k)`` is the burst modulated with the extra delay: the
GFSK modulator takes a fractional-sample delay and evaluates its pulses where the
samples fall, so a delay of 0.3 us is exact and so would 0.35 be; the carrier is
delayed with it, ``exp(j 2 pi f_c (t - tau_k))``, which is the phase term
``exp(-j 2 pi f_c tau_k)`` of every delayed path. A burst's samples extend by the
longest delay at its end (the bursts are a slot apart at least). ``a_k`` are
the amplitudes of the tap powers normalised to ``sum a_k^2 = 1`` (the mean SNR of a
file is its stated one), ``h_k(t)`` independent unit-power Rayleigh gains: the
randomised Jakes sum of ``bt_synth_fade.fade_h`` (32 sinusoids,
``sqrt(1/32) * sum exp(j(2 pi fd cos((2 pi n + theta) / 32) t + p_n))``), with
``theta`` and ``p_n`` drawn per burst **and per tap** from ``default_rng([seed, 21,
burst number, tap])``, ``t = (n - start_sample) / fs`` (the burst's own time axis for
every tap, the delay is in ``s`` and not in ``h``). All paths of a file share ``fd``.
The noise is not faded and not delayed.

**Profiles** (delay in us / power in dB, relative): ``p2a`` 0, 0.2 / 0, -3;
``p2b`` 0, 1.0 / 0, -3; ``p3`` 0, 0.4, 1.0 / 0, -3, -6; ``p4`` 0, 0.3, 0.6, 1.0 /
0, -2, -4, -7. ``fd`` 100 and 300 Hz, mean SNR 18 and 24 dB: ``--set mp`` writes
the 16 files ``mp_<profile>_fd<fd>_snr<snr>``, each ``paired_with`` the reference of
its SNR. A **model**, not a measured channel.

**Truth per burst**, beside the keys of the ``fade/`` files (``fade_*`` are null here):

* ``mp_taps`` (``delay_us``, ``power_db`` as in the profile, ``amplitude``), ``mp_fd_hz``,
  ``mp_rms_delay_spread_us``: the rms delay spread of the (normalised) profile.
* ``mp_gain_db``: per symbol of the burst's air bits, access code included,
  ``20 log10 |G|`` at the sample ``start_sample + i * sps + sps // 2``, rounded to 0.01 dB,
  with ``G(t) = sum_k a_k h_k(t) exp(-j 2 pi f_c tau_k)``, the channel's frequency
  response at the burst's carrier ``f_c = (2402 + channel - 2441) MHz`` from the
  capture centre. **It is the narrow-band equivalent gain at the carrier: the ISI is
  not in it** (a burst is 1 MHz wide, the channel is not flat over it). ``mp_min_db``,
  ``mp_frac_below_10db``, ``mp_longest_run_below_10db`` (rounded gains under -10 dB) and
  ``snr_eff_db`` = ``snr_db + 10 log10`` of the mean of ``|G|^2`` at the symbol centres.
* ``mp_isi_db``: ``10 log10 ( mean_i sum_{k>=1} a_k^2 |h_k(t_i)|^2  /  mean_i a_0^2 |h_0(t_i)|^2 )``
  over the symbol centres ``t_i`` of the burst: the power in the delayed taps against the power
  in the first tap, each averaged over the burst before the ratio is taken (a mean of the
  instantaneous ratio does not exist for Rayleigh: it has a heavy tail where ``h_0`` is
  deep). Null for a one-tap profile.
"""
import argparse
import copy
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from scripts import bt_synth, bt_synth_hop as hop, bt_synth_fade as fade  # noqa: E402
from scripts.bt_synth_interf import NOISE_1MHZ, amplitude_for, noise_block  # noqa: E402

SEED = fade.SEED
FS = fade.FS
BURSTS = fade.BURSTS
BLOCK_SAMPLES = fade.BLOCK_SAMPLES
FADE_OSC = fade.FADE_OSC
DEEP_DB = fade.DEEP_DB
#: Second field of the per-burst, per-tap generator: ``default_rng([seed, TAP_TAG, burst number, tap])``.
TAP_TAG = 21

#: name -> tuple of (delay in us, relative power in dB).
PROFILES = {
    'p2a': ((0.0, 0.0), (0.2, -3.0)),
    'p2b': ((0.0, 0.0), (1.0, -3.0)),
    'p3': ((0.0, 0.0), (0.4, -3.0), (1.0, -6.0)),
    'p4': ((0.0, 0.0), (0.3, -2.0), (0.6, -4.0), (1.0, -7.0)),
}
FDS = (100.0, 300.0)
SNRS = (18.0, 24.0)

NOTES = [
    'The channel is a MODEL, not a measured one: independent Rayleigh paths (32-sinusoid randomised Jakes, one process per '
    'burst and tap, all paths of a file at the same fd) on a fixed delay profile; no shadowing, no frequency-dependent loss, '
    'the noise is neither faded nor delayed.',
    'mp_gain_db is the narrow-band equivalent gain at the burst\'s carrier, G = sum a_k h_k exp(-j 2 pi f_c tau_k); the ISI '
    'is not in it. mp_isi_db is the ratio of the mean power of the delayed taps to that of the first tap over the burst.',
    'The carrier phase of each path is in h_k and in exp(-j 2 pi f_c tau_k): the delayed copy of the passband signal is '
    'bb(t - tau) exp(j 2 pi f_c (t - tau)), the baseband sampled at the absolute sample index as in the fade files.',
    'To rebuild y(t) of burst k: for each tap j, h_j = jakes(default_rng([seed, 21, k, j]), fd, t) with t = (n - start_sample) / '
    'sample_rate (theta = rng.uniform(0, 2 pi) first, then 32 phases p_n = rng.uniform(0, 2 pi, 32)); the burst\'s samples '
    'are noise + A * sum_j a_j h_j(t) s_j(n), s_j the burst modulated with delay timing_frac + tau_j * sample_rate, times its '
    'burst_phase and its carrier, times exp(-j 2 pi f_c tau_j) for j > 0.',
    'E|h_k|^2 = 1 is the ensemble mean: one burst has a mean |G|^2 that scatters round 1, and G is Rayleigh only on average over '
    'the phases exp(-j 2 pi f_c tau_k) (its power is sum a_k^2 |h_k|^2 only when the paths do not add coherently); '
    'snr_eff_db is the realisation\'s.',
]


# --- the profile ------------------------------------------------------------------------------

def make_taps(profile):
    """``[{delay_us, power_db, amplitude}]`` of a profile (a name of PROFILES or a sequence of ``(delay_us, power_db)``),
    the amplitudes normalised so that ``sum amplitude^2 = 1``."""
    spec = PROFILES[profile] if isinstance(profile, str) else tuple(profile)
    lin = [10 ** (p / 10) for _, p in spec]
    total = sum(lin)
    return [dict(delay_us=float(d), power_db=float(p), amplitude=math.sqrt(x / total)) for (d, p), x in zip(spec, lin)]


def rms_delay_spread_us(taps):
    """The rms delay spread of a profile: sqrt of the second central moment of the delay, weighted by power."""
    p = np.array([t['amplitude'] ** 2 for t in taps])
    d = np.array([t['delay_us'] for t in taps])
    mean = float(np.sum(p * d) / np.sum(p))
    return float(math.sqrt(max(np.sum(p * d * d) / np.sum(p) - mean * mean, 0.0)))


def tap_rng(seed, k, tap):
    return np.random.default_rng([seed, TAP_TAG, k, tap])


def jakes(rng, fd_hz, t):
    """The diffuse part of ``fade_h``, a complex array with ``E|h|^2 = 1``, from a generator: ``theta``, then 32 phases."""
    theta = rng.uniform(0, 2 * np.pi)
    phase = rng.uniform(0, 2 * np.pi, FADE_OSC)
    t = np.asarray(t, dtype=np.float64)
    w = 2 * np.pi * fd_hz
    h = np.zeros(len(t), dtype=np.complex128)
    for n in range(FADE_OSC):
        angle = (2 * np.pi * n + theta) / FADE_OSC
        h += np.exp(1j * (w * np.cos(angle) * t + phase[n]))
    return h * (1.0 / math.sqrt(FADE_OSC))


# --- the file ----------------------------------------------------------------------------------

_REF = {}


def reference_sidecar(snr_db, bursts, seed, lap, uap, clk0, fs, center_mhz, channels, block_samples):
    """The sidecar of the reference file of this SNR (its samples are dropped): the per-burst keys and the top level."""
    key = (snr_db, bursts, seed, lap, uap, clk0, fs, center_mhz, tuple(channels), block_samples)
    if key not in _REF:
        _, side = fade.synthesise_fade('ref', snr_db=snr_db, bursts=bursts, seed=seed, lap=lap, uap=uap, clk0=clk0, fs=fs,
                                       center_mhz=center_mhz, channels=channels, block_samples=block_samples)
        _REF[key] = side
    return copy.deepcopy(_REF[key])


def packet_of(b, lap, uap):
    """A planned burst's ``br.Packet``, built as ``synthesise_fade`` builds it."""
    if b['ptype'] == 'FHS':
        return b['fhs']
    return br.Packet(lap, uap, b['clk'], b['ptype'], b['body'], lt_addr=fade.LT_ADDR, flow=1, arqn=1, seqn=b['k'] & 1,
                     llid=b['llid'], payload_flow=1)


def tap_burst(bits, fs, frac, burst_phase, tau_samples, n_first, carrier_mhz_offset):
    """One path's unit-amplitude samples: ``(offset, complex samples)``. ``offset`` is the index of the first sample
    from the first sample of the undelayed burst (``m``, whole samples), the samples are the burst modulated with the delay
    ``frac + tau_samples`` and moved ``m`` samples so that the window holds the delayed tail, times ``exp(j burst_phase)``
    and the carrier at the absolute sample index ``n_first + offset + j`` (``n_first`` the first sample of the undelayed
    burst, ``start_sample - lead``), times ``exp(-j 2 pi f_c tau)`` for a delayed path."""
    m = int(math.ceil(tau_samples)) if tau_samples > 0 else 0
    burst, lead = br.gfsk(bits, fs, h=br.GFSK_H, delay=frac + tau_samples - m)
    burst = burst * np.exp(1j * burst_phase)
    n = np.arange(n_first + m, n_first + m + len(burst))
    cycles = carrier_mhz_offset * 1e6 / fs * n
    burst = burst * np.exp(2j * np.pi * (cycles - np.floor(cycles)))
    if tau_samples > 0:
        burst = burst.astype(np.complex128) * np.exp(-2j * np.pi * carrier_mhz_offset * 1e6 * tau_samples / fs)
    return m, burst, lead


def synthesise_multipath(profile='p2a', snr_db=18.0, fd_hz=300.0, bursts=BURSTS, seed=SEED, lap=fade.LAP, uap=fade.UAP,
                         clk0=fade.CLK0, fs=FS, center_mhz=fade.CENTER_MHZ, channels=fade.MAP,
                         block_samples=BLOCK_SAMPLES, name=None, paired_with=None, profile_name=None, rng_for=None):
    """The samples and the sidecar of one file. ``profile`` is a name of PROFILES or a list of ``(delay_us, power_db)``;
    ``rng_for(seed, k, tap)`` replaces the per-tap generator (the test of the one-tap reduction uses it)."""
    taps = make_taps(profile)
    if profile_name is None:
        profile_name = profile if isinstance(profile, str) else 'custom'
    rng_for = rng_for or tap_rng
    side = reference_sidecar(snr_db, bursts, seed, lap, uap, clk0, fs, center_mhz, channels, block_samples)
    plan, _ = fade.plan_fade(bursts, seed, lap, uap, clk0, fs, center_mhz, channels)
    sps = int(round(fs / br.SYMBOL_RATE))
    total = side['n_samples']
    iq = np.zeros(total, dtype=np.complex64)
    for index, lo in enumerate(range(0, total, block_samples)):
        hi = min(lo + block_samples, total)
        iq[lo:hi] += noise_block(seed, index, hi - lo, fs)
    amp = amplitude_for(snr_db)
    amps = [t['amplitude'] for t in taps]
    taus = [t['delay_us'] * 1e-6 * fs for t in taps]
    spread = rms_delay_spread_us(taps)
    for b, entry in zip(plan, side['bursts']):
        k, channel = b['k'], b['channel']
        p = packet_of(b, lap, uap)
        nbits = len(p.bits)
        start = entry['start_sample']
        carrier = hop.channel_mhz(channel) - center_mhz
        lead = int(math.ceil(2.0e-6 * fs)) + 1            # br.gfsk's: samples before the first bit
        lo = start - lead
        built = [tap_burst(p.bits, fs, b['timing_frac'], b['burst_phase'], tau, lo, carrier) for tau in taus]
        assert built[0][2] == lead
        length = max(m + len(s) for m, s, _ in built)
        if lo + length > total:
            raise ValueError("burst %d ends at %d, past the end of the file at %d" % (k, lo + length, total))
        n_win = np.arange(lo, lo + length)
        t_win = (n_win - start) / fs
        y = np.zeros(length, dtype=np.complex128)
        hs = []
        for j, (m, s, _) in enumerate(built):
            h = jakes(rng_for(seed, k, j), fd_hz, t_win)
            hs.append(h)
            y[m:m + len(s)] += (amp * s) * (amps[j] * h[m:m + len(s)])
        iq[lo:lo + length] += y.astype(np.complex64)
        # the truth
        centres = start - lo + np.arange(nbits) * sps + sps // 2
        phases = [np.exp(-2j * np.pi * carrier * 1e6 * tau / fs) for tau in taus]
        g = sum(amps[j] * hs[j][centres] * phases[j] for j in range(len(taps)))
        gain_db = np.round(20 * np.log10(np.maximum(np.abs(g), 1e-12)), 2)
        gmin, frac_deep, run = fade.gain_truth(gain_db)
        mean_p = float(np.mean(np.abs(g) ** 2))
        if len(taps) > 1:
            delayed = float(np.mean(sum(amps[j] ** 2 * np.abs(hs[j][centres]) ** 2 for j in range(1, len(taps)))))
            first = float(np.mean(amps[0] ** 2 * np.abs(hs[0][centres]) ** 2))
            with np.errstate(divide='ignore'):
                isi = float(10 * np.log10(delayed / first))
        else:
            isi = None
        entry.update(fade_kind=None, fade_fd_hz=None, fade_k_db=None, fade_gain_db=None, fade_min_db=None,
                     fade_frac_below_10db=None, fade_longest_run_below_10db=None,
                     snr_eff_db=float(snr_db + 10 * np.log10(mean_p)),
                     mp_taps=copy.deepcopy(taps), mp_fd_hz=float(fd_hz), mp_gain_db=gain_db.tolist(), mp_min_db=gmin,
                     mp_frac_below_10db=frac_deep, mp_longest_run_below_10db=run, mp_isi_db=isi,
                     mp_rms_delay_spread_us=spread)
    per_burst = ['mp_taps', 'mp_fd_hz', 'mp_gain_db', 'mp_min_db', 'mp_frac_below_10db', 'mp_longest_run_below_10db',
                 'mp_isi_db', 'mp_rms_delay_spread_us']
    side.update(
        generator='SDR scripts/bt_synth_multipath.py', generator_commit=bt_synth.commit(), name=name,
        snr_meaning='the mean SNR of a burst over the noise in 1 MHz; with the channel the realisation\'s is '
                    'bursts[].snr_eff_db (narrow-band, at the carrier: the ISI is not in it)',
        snr_eff_db=None, n_samples=int(total), distortion_kind='multipath',
        distortion='tapped delay line, %d Rayleigh paths (profile %s), fd %g Hz, rms delay spread %.3f us'
                   % (len(taps), profile_name, fd_hz, spread),
        paired_with=paired_with,
        paired_note='every file of the set has the same bursts, channels, payloads, clocks, timing fractions, carrier phases '
                    'and noise (seed %d) as the reference named in paired_with, and file - noise = A * sum_k a_k h_k s_k as '
                    'the header of the generator says; the noise block i is default_rng([seed, 1, i]) of the full '
                    'noise_block_samples length, real rail then imaginary rail from the same generator; '
                    'A = sqrt(noise_1mhz * 10**(snr_db / 10))' % seed,
        fade=None, interferer=None,
        profile=profile_name, taps=copy.deepcopy(taps), fd_hz=float(fd_hz), fd_note='all paths of the file share fd_hz',
        rms_delay_spread_us=spread,
        mp_taps=copy.deepcopy(taps), mp_fd_hz=float(fd_hz), mp_rms_delay_spread_us=spread,
        multipath={'model': 'y(t) = A sum_k a_k h_k(t) s(t - tau_k); h_k independent unit-power Rayleigh, randomised Jakes, '
                            '32 sinusoids',
                   'rng': 'default_rng([seed, %d, burst number, tap])' % TAP_TAG,
                   'time_axis': 't = (n - start_sample) / sample_rate, the same for every tap',
                   'gain_sample': 'mp_gain_db[i] is 20 log10 |G| at sample start_sample + i * sps + sps // 2, rounded to '
                                  '0.01 dB, G = sum_k a_k h_k exp(-j 2 pi f_c tau_k), f_c = (channel_mhz - center_mhz) MHz',
                   'gain_meaning': 'the narrow-band equivalent gain at the carrier; the ISI is not in it',
                   'isi_definition': '10 log10( mean_i sum_{k>=1} a_k^2 |h_k|^2 / mean_i a_0^2 |h_0|^2 ), i over the '
                                     'symbol centres',
                   'deep_threshold_db': DEEP_DB},
        notes=list(NOTES))
    for key in per_burst:                         # the per-burst keys the top level does not carry stay null there
        if key not in ('mp_taps', 'mp_fd_hz', 'mp_rms_delay_spread_us'):
            side[key] = None
    side['per_burst_keys'] = sorted(side['bursts'][0])
    return iq, side


# --- the files ---------------------------------------------------------------------------------

def set_files():
    """What ``--set mp`` writes: ``(name, kwargs)`` for the 16 files."""
    runs = []
    for profile in PROFILES:
        for fd in FDS:
            for snr in SNRS:
                runs.append(('mp_%s_fd%d_snr%d' % (profile, fd, snr),
                             dict(profile=profile, snr_db=snr, fd_hz=fd, paired_with='fade_ref_snr%d' % round(snr))))
    return runs


SETS = {'mp': set_files}


def write_file(name, out_dir, **spec):
    """Make one file and write ``synth_<name>.cf32`` and ``.json`` into ``out_dir``."""
    os.makedirs(out_dir, exist_ok=True)
    iq_path = os.path.join(out_dir, 'synth_%s.cf32' % name)
    side_path = os.path.join(out_dir, 'synth_%s.json' % name)
    iq, sidecar = synthesise_multipath(name=name, **spec)
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
    ap.add_argument('--set', help='write the whole set, everything from its table and no option but --out; one of %s'
                    % ', '.join(sorted(SETS)))
    ap.add_argument('--profile', choices=sorted(PROFILES), help='default p2a')
    ap.add_argument('--snr', type=float, help='dB over the noise in 1 MHz; default 18')
    ap.add_argument('--fd', type=float, help='maximum Doppler in Hz, default 300')
    ap.add_argument('--bursts', type=int, help='default %d' % BURSTS)
    ap.add_argument('--seed', type=int, help='default %d' % SEED)
    ap.add_argument('--out', default='/media/user/4TB/sdr-synth-tmp', help='the folder of the files')
    args = ap.parse_args(argv)
    per_file = ['profile', 'snr', 'fd', 'bursts', 'seed']
    if args.set:
        if args.set not in SETS:
            ap.error('no set %r: the sets are %s' % (args.set, ', '.join(sorted(SETS))))
        stray = [o for o in per_file if getattr(args, o) is not None]
        if args.name is not None or stray:
            ap.error('--set takes everything from its table: no name and no option but --out')
        runs = SETS[args.set]()
    else:
        if args.name is None:
            ap.error('give the name of the file, or --set')
        snr = 18.0 if args.snr is None else args.snr
        spec = dict(profile=args.profile or 'p2a', snr_db=snr, fd_hz=300.0 if args.fd is None else args.fd,
                    bursts=args.bursts or BURSTS, seed=SEED if args.seed is None else args.seed,
                    paired_with='fade_ref_snr%d' % round(snr))
        runs = [(args.name, spec)]
    try:
        for name, spec in runs:
            write_file(name, args.out, **spec)
    except ValueError as e:
        ap.error(str(e))


if __name__ == '__main__':
    main()
