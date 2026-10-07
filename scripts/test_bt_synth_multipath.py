#!/usr/bin/env python3
"""Hold the multipath writer to what its samples say.

    python scripts/test_bt_synth_multipath.py                    # the tests, about two minutes
    python scripts/test_bt_synth_multipath.py --analyse DIR      # the written files of --set mp: what they do

``scripts/bt_synth_multipath.py`` writes the fade files' 204 bursts through a tapped delay line,
``y = A sum_k a_k h_k(t) s(t - tau_k)``. Every claim in the sidecar is checked here against the samples
from small runs (24 bursts, 6 of each type, noise in blocks of 2**17), with the model written again in this
file (the profile table, the Jakes sum, the tap loop, the normalisation, the delayed burst) and never
read back from the generator's own bookkeeping:

* the pairing: off the bursts the file is the regenerated reference, which is the regenerated noise,
  exactly; inside a burst ``file - noise`` is ``A sum_k a_k h_k s_k`` built here (own tap loop, own
  ``default_rng([seed, 21, burst, tap])`` Jakes sum, own normalisation, each delayed copy being the
  burst moved by a whole number of samples as these delays are) to float32 precision on every sample,
  the delayed tails included; a fractional delay (0.1234 us) the same way against a direct evaluation;
* the reduction: a one-tap profile whose tap is seeded as the flat fade's is ``bt_synth_fade``'s flat
  Rayleigh file, bit for bit, and a second tap 400 dB down changes nothing;
* the channel from the samples, at 30 dB, 120 bursts, every profile: ``file - noise`` is fitted by least
  squares on the delayed copies of the known reference burst ``reference - noise`` in blocks of 20 us, the
  taps at the profile's delays, and the mean ``|c_k|^2`` is the profile's power within 1.5 dB; a scan of
  the last tap's delay over a 1/4-sample grid has its residual minimum at the profile's delay; the
  Doppler of the first tap from its estimates 100 us apart, as in the flat test;
* the statistics of the paths over thousands of processes: ``E|h_k|^2 = 1`` for every tap, taps and
  bursts uncorrelated, the sum G Rayleigh with ``E|G|^2 = sum a_k^2 = 1`` and its level-crossing rate
  and fade duration those of Rayleigh at ``fd``;
* the truth keys against an independent recomputation, ``n_samples``, ``per_burst_keys``, the top level;
* that the ISI is real: a genie receiver's raw bit errors in the 24 dB, 1.0 us files are many times those
  of the flat fade of the same mean SNR and ``fd``, clustered, and where ``mp_gain_db`` is low;
* mutants of the generator, each of which the checks must catch.

**What the channel estimate cannot do.** A burst is 1 MHz wide: copies of it 0.3 us apart are nearly
parallel (a free least-squares fit on a 1/4-sample grid returns powers of order 1e13: cond(A) is only about
4e5 on an 800-sample block, but the normal equations A^H A, cond about 1e11, are singular in float32), so a power-delay profile is read at the profile's own
delays and the 1/4-sample grid is used for the scan of one delay, not for a blind estimate.
"""
import argparse
import contextlib
import importlib.util
import itertools
import json
import math
import os
import sys
import tempfile
import time

os.environ.setdefault('OMP_NUM_THREADS', '4')
import numpy as np  # noqa: E402
from scipy.special import j0  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from scripts import bt_synth_acl, bt_synth_fade as g, bt_synth_multipath as m  # noqa: E402
from scripts import test_bt_synth_acl as t  # noqa: E402
from scripts import test_bt_synth_fade as ft  # noqa: E402

FS = 40e6
SPS = 40
CENTER = 2441.0
SEED = 9101
BLOCK = 2 ** 17
NOISE = t.NOISE
LEAD = 81
FLOAT_ABS = 2e-6
SRC = open(m.__file__).read()
#: Blocks of 800 samples per burst the channel estimates use (the first 16000 samples of a burst: 400 us).
MAX_BLOCKS = 20
RESULTS = []
QUIET = [False]

#: The profiles of the task, written again here: (delay us, power dB).
PROFILES = {
    'p2a': ((0.0, 0.0), (0.2, -3.0)),
    'p2b': ((0.0, 0.0), (1.0, -3.0)),
    'p3': ((0.0, 0.0), (0.4, -3.0), (1.0, -6.0)),
    'p4': ((0.0, 0.0), (0.3, -2.0), (0.6, -4.0), (1.0, -7.0)),
}


def check(ok, what):
    ok = bool(ok)
    RESULTS.append((ok, what))
    if not QUIET[0]:
        print('  %s %s' % ('ok  ' if ok else 'FAIL', what))
    return ok


@contextlib.contextmanager
def quiet():
    QUIET[0] = True
    mark = len(RESULTS)
    failed = []
    try:
        yield failed
    finally:
        QUIET[0] = False
        failed.extend(w for ok, w in RESULTS[mark:] if not ok)
        del RESULTS[mark:]


_RUNS = {}


def run(profile='p2a', snr=18.0, fd=300.0, bursts=24, mod=m, seed=SEED):
    """A small multipath file, kept: ``(iq, sidecar)``."""
    key = (str(profile), snr, fd, bursts, id(mod), seed)
    if key not in _RUNS:
        _RUNS[key] = mod.synthesise_multipath(profile, snr_db=snr, fd_hz=fd, bursts=bursts, seed=seed, block_samples=BLOCK,
                                              paired_with='fade_ref_snr%d' % snr)
    return _RUNS[key]


_REFS = {}


def ref(snr=18.0, bursts=24, seed=SEED):
    key = (snr, bursts, seed)
    if key not in _REFS:
        _REFS[key] = g.synthesise_fade('ref', snr_db=snr, bursts=bursts, seed=seed, block_samples=BLOCK)
    return _REFS[key]


# --- the model, written again ------------------------------------------------------------------

def my_amps(profile):
    """Amplitudes of a profile, normalised to unit total power."""
    p = np.array([10 ** (pdb / 10) for _, pdb in PROFILES[profile]])
    return np.sqrt(p / p.sum())


def my_h(seed, k, tap, fd, tt):
    """One path's Jakes process: 32 sinusoids, ``theta`` then the phases from ``default_rng([seed, 21, k, tap])``."""
    rng = np.random.default_rng([seed, 21, k, tap])
    theta = rng.uniform(0, 2 * np.pi)
    ph = rng.uniform(0, 2 * np.pi, 32)
    n = np.arange(32)
    ang = (2 * np.pi * n + theta) / 32
    tt = np.asarray(tt, dtype=np.float64)
    return np.exp(1j * (2 * np.pi * fd * np.cos(ang)[:, None] * tt[None, :] + ph[:, None])).sum(0) / math.sqrt(32)


def my_regions(side, maxtau):
    """Per burst the first sample and one past the last of its window, the delayed tail included."""
    out = []
    for e in side['bursts']:
        out.append((e['start_sample'] - LEAD, e['start_sample'] + e['air_bits_length'] * SPS + LEAD + int(math.ceil(maxtau)) + 4))
    return out


def my_burst(e, tau_samples):
    """Unit-amplitude samples of one path of burst ``e`` on the window ``[start - LEAD, ...)``: the burst of the sidecar's
    bits and timing fraction moved ``tau_samples`` later, its phase, its carrier at the absolute sample index and, for a
    delayed path, ``exp(-j 2 pi f_c tau)``. A whole number of samples is a shift of the array; a fraction is a modulation
    with the delay (window moved by ``ceil(tau)`` samples, which still holds the burst)."""
    bits = t.hex_bits(e['air_bits'], e['air_bits_length'])
    lo = e['start_sample'] - LEAD
    frac = e['timing_frac']
    if float(tau_samples).is_integer():
        base, lead = br.gfsk(bits, FS, h=br.GFSK_H, delay=frac)
        shift = int(tau_samples)
    else:
        shift = int(math.ceil(tau_samples))
        base, lead = br.gfsk(bits, FS, h=br.GFSK_H, delay=frac + tau_samples - shift)
    assert lead == LEAD
    n = np.arange(lo + shift, lo + shift + len(base))
    off = e['channel_mhz'] - CENTER
    cycles = off * 1e6 / FS * n
    s = base.astype(np.complex128) * np.exp(1j * e['burst_phase']) * np.exp(2j * np.pi * (cycles - np.floor(cycles)))
    if tau_samples:
        s = s * np.exp(-2j * np.pi * off * 1e6 * tau_samples / FS)
    return shift, s


def my_expected(side, profile, snr, fd, delays_samples=None, amps=None, h_of=None, seed=SEED):
    """``(regions, [complex128 window of A sum_k a_k h_k s_k])`` for every burst, from the model written here."""
    if delays_samples is None:
        delays_samples = [d * 1e-6 * FS for d, _ in PROFILES[profile]]
    if amps is None:
        amps = my_amps(profile)
    h_of = h_of or (lambda k, tap, tt: my_h(seed, k, tap, fd, tt))
    amp = math.sqrt(NOISE * 10 ** (snr / 10))
    regions = my_regions(side, max(delays_samples))
    out = []
    for k, (e, (a, b)) in enumerate(zip(side['bursts'], regions)):
        y = np.zeros(b - a, dtype=np.complex128)
        tt = (np.arange(a, b) - e['start_sample']) / FS
        for tap, tau in enumerate(delays_samples):
            shift, s = my_burst(e, tau)
            h = h_of(k, tap, tt)
            y[shift:shift + len(s)] += amps[tap] * h[shift:shift + len(s)] * s
        out.append(amp * y)
    return regions, out


def noise_of(side):
    return t.my_noise(side['seed'], side['n_samples'], block=side['noise_block_samples'])


# --- the checks ----------------------------------------------------------------------------------

def check_pairing_and_sum(iq, side, label, profile, snr, fd, expected=None, delays_samples=None):
    """Exact pairing off the bursts and the model's sum inside them."""
    ref_iq, ref_side = ref(snr, len(side['bursts']))
    noise = noise_of(side)
    check(len(iq) == len(ref_iq) == side['n_samples'], '%s: n_samples %d is the length of the array and of the reference'
          % (label, side['n_samples']))
    if len(iq) != len(ref_iq):
        return
    regions = my_regions(side, max(d * 1e-6 * FS for d, _ in PROFILES[profile]))
    inside = np.zeros(len(iq), bool)
    for a, b in regions:
        inside[a:b] = True
    check(np.array_equal(iq[~inside], ref_iq[~inside]) and np.array_equal(iq[~inside], noise[~inside]),
          '%s: off the bursts (%d samples) the file is the reference, which is the regenerated noise, exactly' % (label, (~inside).sum()))
    keys = ('clk', 'channel', 'ptype', 'payload_hex', 'air_bits', 'start_sample', 'timing_frac', 'symbol_phase', 'lmp_name',
            'burst_phase', 'fhs')
    same = len(side['bursts']) == len(ref_side['bursts']) and all(
        all(x[kk] == y[kk] for kk in keys) for x, y in zip(side['bursts'], ref_side['bursts']))
    check(same and side['seed'] == SEED and side['n_samples'] == ref_side['n_samples'],
          '%s: bursts, channels, payloads, clocks, timing fractions, phases and seed are the reference\'s' % label)
    regions, exp = my_expected(side, profile, snr, fd, delays_samples) if expected is None else expected
    worst, tail, ref_tail = 0.0, 0.0, 0.0
    for (a, b), y, e in zip(regions, exp, side['bursts']):
        d = iq[a:b].astype(np.complex128) - noise[a:b]
        worst = max(worst, float(np.abs(d - y).max()))
        cut = e['air_bits_length'] * SPS + 2 * LEAD + 1            # the undelayed burst's ramp ended 81 samples after its last bit
        tail = max(tail, float(np.abs(d[cut:]).max()))
        ref_tail = max(ref_tail, float(np.abs(ref_iq[a:b].astype(np.complex128)[cut:] - noise[a:b][cut:]).max()))
    check(worst < FLOAT_ABS, '%s: (file - noise) is A sum_k a_k h_k s_k, built here, on EVERY sample of %d bursts, ramps and '
          'delayed tails included: worst |diff| %.2e (limit %.0e)' % (label, len(regions), worst, FLOAT_ABS))
    check(tail > 1e-4 and ref_tail < 1e-6, '%s: past the end of the undelayed burst\'s ramp the reference is exactly noise (%.1e) and the file has the delayed tails (largest %.4f, noise-free)'
          % (label, ref_tail, tail))


def check_truth(iq, side, label, profile, snr, fd):
    """The truth keys against an independent recomputation from this file's own model."""
    amps = my_amps(profile)
    delays = [d for d, _ in PROFILES[profile]]
    nt = len(delays)
    top_ok = (side['profile'] == profile and side['fd_hz'] == fd and side['snr_db'] == snr
              and [x['delay_us'] for x in side['taps']] == delays and [x['power_db'] for x in side['taps']] == [p for _, p in PROFILES[profile]]
              and np.allclose([x['amplitude'] for x in side['taps']], amps, atol=1e-12))
    check(top_ok, '%s: the top level says the profile, its delays, powers and normalised amplitudes, fd and mean SNR' % label)
    check(abs(sum(x['amplitude'] ** 2 for x in side['taps']) - 1) < 1e-12, '%s: sum of a_k^2 = 1' % label)
    p = np.array([a * a for a in amps])
    dd = np.array(delays)
    rms = math.sqrt(float(np.sum(p * dd * dd) / p.sum() - (np.sum(p * dd) / p.sum()) ** 2))
    bad = {'taps': [], 'gain': [], 'rest': [], 'eff': [], 'isi': [], 'rms': []}
    worst_gain = 0.0
    for k, e in enumerate(side['bursts']):
        nb = e['air_bits_length']
        tt = (np.arange(nb) * SPS + SPS // 2) / FS
        off = e['channel_mhz'] - CENTER
        hs = [my_h(SEED, k, j, fd, tt) for j in range(nt)]
        G = sum(amps[j] * hs[j] * np.exp(-2j * np.pi * off * 1e6 * delays[j] * 1e-6) for j in range(nt))
        gl = 20 * np.log10(np.abs(G))
        want = np.array(e['mp_gain_db'])
        if len(want) != nb:
            bad['gain'].append(k)
            continue
        worst_gain = max(worst_gain, float(np.abs(want - gl).max()))
        bad['gain'] += [k] if np.abs(want - gl).max() > 0.0051 else []
        deep = want < -10
        bad['rest'] += [k] if (e['mp_min_db'] != want.min() or abs(e['mp_frac_below_10db'] - deep.mean()) > 1e-12
                               or e['mp_longest_run_below_10db'] != ft.run_length(deep)) else []
        eff = snr + 10 * np.log10(np.mean(np.abs(G) ** 2))
        bad['eff'] += [k] if abs(e['snr_eff_db'] - eff) > 0.01 else []
        if nt > 1:
            num = np.mean(sum(amps[j] ** 2 * np.abs(hs[j]) ** 2 for j in range(1, nt)))
            den = np.mean(amps[0] ** 2 * np.abs(hs[0]) ** 2)
            bad['isi'] += [k] if abs(e['mp_isi_db'] - 10 * np.log10(num / den)) > 1e-9 else []
        bad['rms'] += [k] if abs(e['mp_rms_delay_spread_us'] - rms) > 1e-12 else []
        bad['taps'] += [k] if (e['mp_taps'] != side['taps'] or e['mp_fd_hz'] != fd or e['fade_kind'] is not None
                               or e['fade_gain_db'] is not None) else []
    check(not bad['gain'], '%s: mp_gain_db is 20 log10 |sum_k a_k h_k exp(-j 2 pi f_c tau_k)| at every symbol centre, recomputed here '
          '(rounding 0.01 dB, worst %.4f dB)' % (label, worst_gain))
    check(not bad['rest'], '%s: mp_min_db, mp_frac_below_10db and mp_longest_run_below_10db are the list\'s' % label)
    check(not bad['eff'], '%s: snr_eff_db is snr_db + 10 log10 mean |G|^2 (0.01 dB)' % label)
    check(not bad['isi'], '%s: mp_isi_db is 10 log10 (mean delayed-tap power / mean first-tap power) over the symbol centres' % label)
    check(not bad['rms'], '%s: mp_rms_delay_spread_us is the profile\'s rms delay spread, %.4f us' % (label, rms))
    check(not bad['taps'], '%s: every burst states mp_taps (a copy of the profile) and mp_fd_hz, and the flat-fade keys are null' % label)
    check(abs(side['rms_delay_spread_us'] - rms) < 1e-12 and side['mp_rms_delay_spread_us'] == side['rms_delay_spread_us'],
          '%s: the top level carries the rms delay spread' % label)
    # the carrier-phase term matters: with it dropped the same h would give a different gain (a path of a whole number of
    # microseconds, p2b's, has exp(-j 2 pi f_c tau) = 1 at the integer-MHz channels, so p2b is exempt)
    if nt > 1:
        worst = 0.0
        for k in range(min(8, len(side['bursts']))):
            e = side['bursts'][k]
            nb = e['air_bits_length']
            tt = (np.arange(nb) * SPS + SPS // 2) / FS
            hs = [my_h(SEED, k, j, fd, tt) for j in range(nt)]
            no_phase = 20 * np.log10(np.abs(sum(amps[j] * hs[j] for j in range(nt))))
            worst = max(worst, float(np.abs(no_phase - np.array(e['mp_gain_db'])).max()))
        check(worst > 0.05 or profile == 'p2b',
              '%s: the phase term exp(-j 2 pi f_c tau_k) is in mp_gain_db (without it the list differs by up to %.2f dB)' % (label, worst))


def check_top(side, label, snr):
    need = ['generator', 'generator_commit', 'lap', 'uap', 'sample_rate', 'center_mhz', 'modulation', 'slot_samples',
            'start_sample_meaning', 'clk_convention', 'snr_bw_hz', 'noise_1mhz', 'bursts', 'symbol_phases', 'per_burst_keys',
            'n_samples', 'type_counts', 'air_bits_omitted', 'distortion', 'paired_with', 'notes', 'seed', 'snr_db', 'profile',
            'taps', 'fd_hz', 'multipath']
    miss = [k for k in need if k not in side]
    check(not miss, '%s: the sidecar has every top-level key (%s)' % (label, miss))
    per = ['ptype', 'lap', 'uap', 'channel', 'start_sample', 'timing_frac', 'clk', 'snr_db', 'llid', 'payload_length', 'payload_hex',
           'lmp_name', 'fhs', 'air_bits', 'air_bits_length', 'body_crc_bits_hex', 'snr_eff_db', 'mp_taps', 'mp_fd_hz', 'mp_gain_db',
           'mp_min_db', 'mp_frac_below_10db', 'mp_longest_run_below_10db', 'mp_isi_db', 'mp_rms_delay_spread_us']
    miss = [k for k in per if k not in side['per_burst_keys']]
    check(not miss and side['per_burst_keys'] == sorted(side['bursts'][0]) and all(sorted(e) == side['per_burst_keys'] for e in side['bursts']),
          '%s: per_burst_keys is every key of every burst, with the mp_ truth keys (%s)' % (label, miss))
    nulls = [k for k in ('mp_gain_db', 'mp_min_db', 'mp_frac_below_10db', 'mp_longest_run_below_10db', 'mp_isi_db', 'snr_eff_db',
                         'timing_frac', 'symbol_phase') if k in side and side[k] is not None]
    check(not nulls, '%s: the per-burst keys are null at the top level (%s)' % (label, nulls))
    check(side['paired_with'] == 'fade_ref_snr%d' % snr and side['distortion_kind'] == 'multipath' and side['snr_db'] == snr
          and side['air_bits_omitted'] is False and 'all paths of the file share fd_hz' in side['fd_note'],
          '%s: paired_with names the reference, all paths share fd' % label)
    text = ' '.join(side['notes']) + side['multipath']['gain_meaning']
    check('MODEL' in text and 'ISI is not in it' in text and 'default_rng([seed, 21, k, j])' in text,
          '%s: the notes say it is a model, that the ISI is not in mp_gain_db, and how to rebuild every h_k' % label)


def check_profile_table():
    ok = all(tuple(tuple(x) for x in m.PROFILES[k]) == PROFILES[k] for k in PROFILES) and set(m.PROFILES) == set(PROFILES)
    check(ok, 'the profiles of the generator are the task\'s four')
    names = [n for n, _ in m.set_files()]
    want = ['mp_%s_fd%d_snr%d' % (p, fd, s) for p in PROFILES for fd in (100, 300) for s in (18, 24)]
    paired = all(kw['paired_with'] == 'fade_ref_snr%d' % kw['snr_db'] for _, kw in m.set_files())
    check(names == want and len(names) == 16 and paired, '--set mp is the 16 files mp_<profile>_fd<fd>_snr<snr>, each paired with its reference')


def check_file_size():
    with tempfile.TemporaryDirectory() as d:
        iq_path, side_path = m.write_file('mp_test', d, profile='p3', snr_db=18.0, fd_hz=300.0, bursts=8, paired_with='fade_ref_snr18')
        side = json.load(open(side_path))
        check(os.path.getsize(iq_path) == 8 * side['n_samples'], 'a written file: its size %d bytes is 8 * n_samples = %d'
              % (os.path.getsize(iq_path), 8 * side['n_samples']))
        back = np.fromfile(iq_path, dtype='<c8')
        iq, _ = m.synthesise_multipath('p3', 18.0, 300.0, bursts=8)
        check(np.array_equal(back, iq), 'the written samples are the synthesised ones')


# --- reduction and fractional delay -----------------------------------------------------------------

def check_reduction():
    flat, _ = g.synthesise_fade('rayleigh', 18.0, 300.0, bursts=24, block_samples=BLOCK)
    flat_rng = lambda seed, k, tap: np.random.default_rng([seed, 7, k])  # noqa: E731
    one, _ = m.synthesise_multipath([(0.0, 0.0)], 18.0, 300.0, bursts=24, block_samples=BLOCK, rng_for=flat_rng)
    check(np.array_equal(one, flat), 'reduction: a one-tap profile seeded as the flat fade is bt_synth_fade\'s flat Rayleigh file, bit for bit')
    two, side = m.synthesise_multipath([(0.0, 0.0), (0.2, -400.0)], 18.0, 300.0, bursts=24, block_samples=BLOCK, rng_for=flat_rng)
    d = np.abs(two.astype(np.complex128) - flat).max()
    check(d < 1e-6, 'reduction: a second tap 400 dB down leaves the flat fade (first tap alone, a_0 = 1) to %.1e' % d)
    # the first tap alone, the others zeroed (their processes replaced by zero), is a_0 times the flat fade of fade/
    noise = t.my_noise(SEED, len(flat), block=BLOCK)
    real_jakes = m.jakes
    m.jakes = lambda rng, fd, tt: np.zeros(len(tt), dtype=np.complex128) if isinstance(rng, str) else real_jakes(rng, fd, tt)
    try:
        iq, side = m.synthesise_multipath('p3', 18.0, 300.0, bursts=24, block_samples=BLOCK,
                                          rng_for=lambda seed, k, tap: np.random.default_rng([seed, 7, k]) if tap == 0 else 'zero')
    finally:
        m.jakes = real_jakes
    a0 = my_amps('p3')[0]
    worst = max(float(np.abs(iq[a:b].astype(np.complex128) - noise[a:b] - a0 * (flat[a:b].astype(np.complex128) - noise[a:b])).max())
                for a, b in my_regions(side, 40))
    check(worst < FLOAT_ABS, 'reduction: the first tap alone (the others zeroed), a_0 = %.4f, is a_0 times the flat Rayleigh fade, worst %.1e' % (a0, worst))


def check_fractional(snr=18.0, fd=300.0):
    """A delay that is not a whole number of samples (0.1234 us = 4.936 samples, 0.35 us = 14 samples, 0.6107 us) is exact too."""
    prof = [(0.0, 0.0), (0.1234, -3.0), (0.6107, -6.0)]
    iq, side = m.synthesise_multipath(prof, snr, fd, bursts=24, block_samples=BLOCK)
    old = dict(PROFILES)
    PROFILES['frac'] = tuple(prof)
    try:
        taus = [d * 1e-6 * FS for d, _ in prof]
        exp = my_expected(side, 'frac', snr, fd, taus)
        noise = noise_of(side)
        worst = max(float(np.abs(iq[a:b].astype(np.complex128) - noise[a:b] - y).max()) for (a, b), y in zip(*exp))
    finally:
        PROFILES.clear()
        PROFILES.update(old)
    check(worst < FLOAT_ABS, 'fractional delays (4.936 and 24.428 samples): the file is the model built here with the burst modulated at '
          'that delay, worst %.2e' % worst)
    # and the delayed burst is the same burst: its envelope and phase are the undelayed burst's shifted by tau
    e = side['bursts'][0]
    bits = t.hex_bits(e['air_bits'], e['air_bits_length'])
    b0, _ = br.gfsk(bits, FS, delay=e['timing_frac'])
    shift = 5
    b1, _ = br.gfsk(bits, FS, delay=e['timing_frac'] + 4.936 - shift)
    # b1[j] is the burst at (j - frac - 4.936 + 5) samples: b0 evaluated 4.936 - 5 samples early; compare with interpolation of b0
    j = np.arange(200, 200 + SPS * 40)
    x = j.astype(float) + (shift - 4.936)           # b1[j] = b0(j - (4.936 - 5))... interpolate the unwrapped phase of b0
    ph0 = np.unwrap(np.angle(b0.astype(np.complex128)))
    interp = np.interp(x, np.arange(len(ph0)), ph0)
    err = np.abs(np.angle(np.exp(1j * (np.unwrap(np.angle(b1.astype(np.complex128)))[j] - interp))))
    check(err.max() < 0.02, 'a fractional delay is the burst moved by that fraction: phase against an interpolation of the undelayed burst, worst %.3f rad' % err.max())


# --- the channel from the samples ------------------------------------------------------------------------

def fshift(r, d):
    n = len(r)
    return np.fft.ifft(np.fft.fft(r) * np.exp(-2j * np.pi * np.fft.fftfreq(n) * d))


def channel_blocks(iq, ref_iq, noise, side, profile, block=800, scan=None):
    """Per burst, per block of ``block`` samples, the least-squares coefficients of ``file - noise`` on the known
    reference burst delayed to the profile's delays. Returns the list of arrays (blocks x taps), one per burst."""
    ds = [d * 1e-6 * FS for d, _ in PROFILES[profile]] if scan is None else scan
    out = []
    maxd = int(math.ceil(max(ds)))
    for e in side['bursts']:
        a = e['start_sample'] - LEAD - 64
        b = min(e['start_sample'] + e['air_bits_length'] * SPS + LEAD + 64 + maxd + 8, a + LEAD + 64 + 160 + MAX_BLOCKS * block + 400)
        x = iq[a:b].astype(np.complex128) - noise[a:b]
        r = ref_iq[a:b].astype(np.complex128) - noise[a:b]
        A = np.stack([fshift(r, d) for d in ds], 1)
        lo0 = LEAD + 64 + 160
        cs = []
        for lo in range(lo0, len(x) - block - 400, block):
            sl = slice(lo, lo + block)
            cs.append(np.linalg.lstsq(A[sl], x[sl], rcond=1e-10)[0])
        out.append(np.array(cs).reshape(-1, len(ds)))
    return out


def check_channel(profile, bursts=120, fd=300.0):
    iq, side = run(profile, 30.0, fd, bursts)
    ref_iq, _ = ref(30.0, bursts)
    noise = noise_of(side)
    cs = channel_blocks(iq, ref_iq, noise, side, profile)
    allc = np.concatenate([c for c in cs if len(c)])
    est = np.mean(np.abs(allc) ** 2, axis=0)
    want = my_amps(profile) ** 2
    err = 10 * np.log10(est / want)
    check(np.abs(err).max() < 1.5, '%s, %d bursts, %d blocks of 20 us at 30 dB: mean |c_k|^2 at the profile\'s delays is the profile\'s power within 1.5 dB: %s dB '
          '(estimate - profile)' % (profile, bursts, len(allc), ' '.join('%+.2f' % x for x in err)))
    # the delay scan: the last tap at 1/4-sample steps round the profile's delay, the others at the profile's delays, in blocks
    # of 20 us (a fit over a whole burst cannot tell 1/4 sample: the copies are parallel to float precision)
    ds = [d * 1e-6 * FS for d, _ in PROFILES[profile]]
    maxd = int(math.ceil(max(ds)))
    off = np.arange(-2.0, 2.01, 0.25)
    resid = np.zeros(len(off))
    for e in side['bursts'][:12]:
        a = e['start_sample'] - LEAD - 64
        b = min(e['start_sample'] + e['air_bits_length'] * SPS + LEAD + 64 + maxd + 8, a + LEAD + 64 + 160 + 15 * 800 + 400)
        x = iq[a:b].astype(np.complex128) - noise[a:b]
        r = ref_iq[a:b].astype(np.complex128) - noise[a:b]
        mats = [np.stack([fshift(r, d) for d in ds[:-1] + [ds[-1] + o]], 1) for o in off]
        for lo in range(LEAD + 64 + 160, len(x) - 800 - 400, 800):
            sl = slice(lo, lo + 800)
            for i, A in enumerate(mats):
                c = np.linalg.lstsq(A[sl], x[sl], rcond=1e-10)[0]
                resid[i] += float(np.sum(np.abs(x[sl] - A[sl] @ c) ** 2))
    i0 = int(np.argmin(resid))
    convex = bool(np.all(np.diff(resid[:i0 + 1]) < 0) and np.all(np.diff(resid[i0:]) > 0))
    check(abs(off[i0]) <= 0.25 and convex,
          '%s: delay scan of the last tap (1/4-sample steps over +-2 samples, 12 bursts): the residual is lowest at the profile\'s delay %+.2f '
          'sample and rises on both sides; the +-2 sample ends are %.2f and %.2f times the minimum' % (profile, off[i0], resid[0] / resid[i0], resid[-1] / resid[i0]))
    return cs


def check_doppler_first_tap(profile='p4', fd=300.0, bursts=120, cs=None):
    iq, side = run(profile, 30.0, fd, bursts)
    if cs is None:
        ref_iq, _ = ref(30.0, bursts)
        cs = channel_blocks(iq, ref_iq, noise_of(side), side, profile)
    lag = 5                                              # blocks: 5 * 800 samples = 100 us, the flat test's cap
    num = den = 0.0
    for c in cs:
        if len(c) > lag + 1:
            c0 = c[:, 0]
            num += float(np.sum(np.abs(c0[lag:] - c0[:-lag]) ** 2))
            den += float(np.sum(np.abs(c0[:-lag]) ** 2) + np.sum(np.abs(c0[lag:]) ** 2))
    got = num / den
    theory = 1 - j0(2 * math.pi * fd * 100e-6)
    check(0.6 < got / theory < 1.5, '%s fd %g: Doppler of the first tap from the samples: 1 - rho(100 us) = %.4g against 1 - J0 = %.4g (ratio %.2f, limit 0.6-1.5)'
          % (profile, fd, got, theory, got / theory))


# --- the processes -----------------------------------------------------------------------------------------

def check_paths(mod, label, n=4000, fd=300.0, profile='p4'):
    """E|h_k|^2 = 1 for every tap, taps independent, G Rayleigh with E|G|^2 = sum a_k^2 = 1 and Rayleigh's level-crossing rate."""
    tt = np.linspace(0, 0.01, 12)
    taps = mod.make_taps(profile)
    nt = len(taps)
    hs = np.array([[mod.jakes(mod.tap_rng(SEED, k, j), fd, tt) for j in range(nt)] for k in range(n)])      # n x taps x 12
    p = np.mean(np.abs(hs) ** 2, axis=(0, 2))
    se = np.sqrt(np.var(np.abs(hs) ** 2, axis=(0, 2)) / (n * 3))
    check(np.all(np.abs(p - 1) < 0.04), '%s: E|h_k|^2 = %s for the %d taps over %d processes (limit 4 %%; standard error about %.1f %%)'
          % (label, ' '.join('%.3f' % x for x in p), nt, n, 100 * se.max()))
    worst = 0.0
    for i, j in itertools.combinations(range(nt), 2):
        a, b = hs[:, i, 0], hs[:, j, 0]
        worst = max(worst, abs(np.mean(a * np.conj(b))) / math.sqrt(np.mean(abs(a) ** 2) * np.mean(abs(b) ** 2)))
    check(worst < 0.05, '%s: the taps of one burst are independent: worst |corr| of two taps %.3f over %d bursts (limit 0.05)' % (label, worst, n))
    c = abs(np.mean(hs[1:, 0, 0] * np.conj(hs[:-1, 0, 0]))) / math.sqrt(np.mean(abs(hs[:, 0, 0]) ** 2) ** 2)
    check(c < 0.05, '%s: the same tap of consecutive bursts is independent: |corr| = %.3f (limit 0.05)' % (label, c))
    again = mod.jakes(mod.tap_rng(SEED, 5, 1), fd, tt)
    check(np.array_equal(again, hs[5, 1]) and not np.allclose(hs[5, 0], hs[5, 1]), '%s: a tap\'s process is a function of (seed, burst, tap) alone and the taps differ' % label)
    # G = sum a_k h_k exp(-j phi_k) is a sum of independent circular Gaussians whatever the phases: Rayleigh with power sum a_k^2
    amps = np.array([x['amplitude'] for x in taps])
    phi = np.exp(-2j * np.pi * np.array([5.0e6 * x['delay_us'] * 1e-6 for x in taps]))
    G = np.einsum('ktn,t->kn', hs, amps * phi)
    pg = np.abs(G) ** 2
    kurt = np.mean(pg ** 2) / np.mean(pg) ** 2
    check(abs(np.mean(pg) - np.sum(amps ** 2)) < 0.04 and abs(kurt - 2) < 0.15,
          '%s: G is Rayleigh with E|G|^2 = sum a_k^2 = %.3f (%.3f) and E|G|^4 / E|G|^2^2 = %.2f (2 for Rayleigh; the phases exp(-j 2 pi f_c tau_k) '
          'do not matter for independent circular paths, they only move where the nulls fall)' % (label, np.sum(amps ** 2), np.mean(pg), kurt))
    for dur, rhos in ((0.05, (0.3, 0.5, 1.0)),):
        tt2 = np.arange(0, dur, 1 / (40 * fd))
        procs = []
        for k in range(150):
            Gk = sum(amps[j] * phi[j] * mod.jakes(mod.tap_rng(SEED, 10000 + k, j), fd, tt2) for j in range(nt))
            procs.append(np.abs(Gk))
        total = 150 * dur
        for rho in rhos:
            crossings = sum(int(np.sum((r[:-1] < rho) & (r[1:] >= rho))) for r in procs)
            below = np.mean([np.mean(r < rho) for r in procs])
            lcr = crossings / total
            theory = math.sqrt(2 * math.pi) * fd * rho * math.exp(-rho ** 2)
            afd = below / lcr
            afd_t = (math.exp(rho ** 2) - 1) / (rho * fd * math.sqrt(2 * math.pi))
            check(abs(lcr / theory - 1) < 0.15 and abs(afd / afd_t - 1) < 0.15,
                  '%s: |G|, fd %g, rho %.1f: level crossing %.1f/s against Rayleigh %.1f (%d crossings), average fade %.3g ms against %.3g ms'
                  % (label, fd, rho, lcr, theory, crossings, afd * 1e3, afd_t * 1e3))


# --- the ISI and the clustering --------------------------------------------------------------------------------

def errors_of(iq, side):
    return ft.error_stats(iq, side)


def check_isi(bursts=48, snr=24.0, fd=300.0):
    ref_iq, ref_side = ref(snr, bursts)
    e_ref = errors_of(ref_iq, ref_side)
    flat_iq, flat_side = g.synthesise_fade('rayleigh', snr, fd, bursts=bursts, block_samples=BLOCK)
    e_flat = errors_of(flat_iq, flat_side)
    flat_idx, flat_run, flat_ber = ft.fano(e_flat)
    _, _, ref_ber = ft.fano(e_ref)
    print('       reference BER %.5f, flat Rayleigh fd %g at %g dB: BER %.4f (%d bursts)' % (ref_ber, fd, snr, flat_ber, bursts))
    for profile, factor in (('p2b', 5.0), ('p4', 5.0), ('p3', 3.0)):
        iq, side = run(profile, snr, fd, bursts)
        err = errors_of(iq, side)
        idx, run_len, ber = ft.fano(err)
        check(ber > factor * max(flat_ber, 1e-4) and ber > 100 * max(ref_ber, 1e-5),
              '%s at %g dB, fd %g: raw bit errors %.4f are %.1f times the flat fade\'s %.4f (limit %.0f times) and %s the reference\'s %.5f'
              % (profile, snr, fd, ber, ber / max(flat_ber, 1e-9), flat_ber, factor, 'above' if ber > 100 * ref_ber else 'NOT above', ref_ber))
        # AWGN at the same error rate
        best = None
        for s in (3.0, 5.0, 7.0, 9.0, 11.0):
            r_iq, r_side = ref(s, bursts)
            a_idx, a_run, a_ber = ft.fano(errors_of(r_iq, r_side))
            if best is None or abs(math.log((a_ber + 1e-6) / (ber + 1e-6))) < abs(math.log((best[3] + 1e-6) / (ber + 1e-6))):
                best = (s, a_idx, a_run, a_ber)
        s, a_idx, a_run, a_ber = best
        check(idx > 2 * a_idx and idx > 1.5 and run_len > a_run,
              '%s: errors are clustered: index of dispersion (32-symbol windows) %.1f, mean run %.2f; AWGN at %g dB with rate %.4f: %.1f, %.2f'
              % (profile, idx, run_len, s, a_ber, a_idx, a_run))
        deep_n = deep_e = rest_n = rest_e = 0
        for e, bad in zip(side['bursts'], err):
            if bad is None:
                continue
            deep = np.array(e['mp_gain_db']) < -6
            deep_n += int(deep.sum())
            deep_e += int((bad.astype(bool) & deep).sum())
            rest_n += int((~deep).sum())
            rest_e += int((bad.astype(bool) & ~deep).sum())
        r_deep, r_rest = deep_e / max(deep_n, 1), rest_e / max(rest_n, 1)
        burst_ber = np.array([b.mean() for b in err if b is not None])
        eff = np.array([e['snr_eff_db'] for e, b in zip(side['bursts'], err) if b is not None])
        rank = lambda v: np.argsort(np.argsort(v))  # noqa: E731
        rho_s = float(np.corrcoef(rank(burst_ber), rank(eff))[0, 1])
        print('       %s: error rate under -6 dB (%d symbols) %.3f, elsewhere %.3f (%.1f times); rank correlation of a burst\'s error rate with snr_eff_db %+.2f'
              % (profile, deep_n, r_deep, r_rest, r_deep / max(r_rest, 1e-9), rho_s))
        check(rho_s < -0.1, '%s: bursts with a lower snr_eff_db (a deeper fade at the carrier) have more errors: rank correlation %+.2f' % (profile, rho_s))
    iq, side = run('p2a', snr, fd, bursts)
    err = errors_of(iq, side)
    deep_n = deep_e = rest_n = rest_e = 0
    for e, bad in zip(side['bursts'], err):
        if bad is None:
            continue
        deep = np.array(e['mp_gain_db']) < -6
        deep_n += int(deep.sum())
        deep_e += int((bad.astype(bool) & deep).sum())
        rest_n += int((~deep).sum())
        rest_e += int((bad.astype(bool) & ~deep).sum())
    r_deep, r_rest = deep_e / max(deep_n, 1), rest_e / max(rest_n, 1)
    check(r_deep > 3 * r_rest and deep_n > 100,
          'p2a (0.2 us, little ISI): errors sit where mp_gain_db is low: the error rate in the %d symbols under -6 dB is %.3f, elsewhere %.4f (%.0f times). '
          'For p2b and p4 the ISI errors are everywhere, and this ratio is about 1 (printed above): the gain at the carrier is a weak '
          'statistic of a 1 us channel' % (deep_n, r_deep, r_rest, r_deep / max(r_rest, 1e-9)))


# --- mutants ----------------------------------------------------------------------------------------------------

def mutant_module(old, new):
    assert SRC.count(old) == 1, 'mutation target not unique: %r (%d)' % (old, SRC.count(old))
    spec = importlib.util.spec_from_file_location('bt_synth_multipath_mut', m.__file__)
    mod = importlib.util.module_from_spec(spec)
    exec(compile(SRC.replace(old, new), m.__file__, 'exec'), mod.__dict__)
    return mod


def sample_checks(mod, label, profile='p3', fd=300.0):
    """What a reader would check of a file made by ``mod``."""
    iq, side = run(profile, 18.0, fd, bursts=12, mod=mod)
    check_pairing_and_sum(iq, side, label, profile, 18.0, fd)
    check_truth(iq, side, label, profile, 18.0, fd)
    check_top(side, label, 18.0)
    check_paths(mod, label, n=800, fd=fd, profile=profile)


def caught(name, failed):
    short = sorted({w.split(':')[0][:20] + ':' + w.split(':')[-1][:55] for w in failed})
    check(len(failed) > 0, 'mutant "%s" is caught by %d checks, e.g. %s' % (name, len(failed), '; '.join(short[:2])))


def check_mutants():
    print('\nMutants: one mistake each, and what catches it')
    cases = [
        ('the delays ignored (all taps at 0)', ("taus = [t['delay_us'] * 1e-6 * fs for t in taps]", "taus = [0.0 for t in taps]")),
        ('the same h for every tap', ("h = jakes(rng_for(seed, k, j), fd_hz, t_win)", "h = jakes(rng_for(seed, k, 0), fd_hz, t_win)")),
        ('tap powers not normalised (sum a^2 = 2)', ("amplitude=math.sqrt(x / total)", "amplitude=math.sqrt(2 * x / total)")),
        ('the delay sign reversed (taps arrive before the burst)', ("delay=frac + tau_samples - m)", "delay=frac - tau_samples - m)")),
        ('the phase term dropped from mp_gain_db', ("phases = [np.exp(-2j * np.pi * carrier * 1e6 * tau / fs) for tau in taus]", "phases = [1.0 for tau in taus]")),
        ('a Doppler wrong by a factor of 2', ("w = 2 * np.pi * fd_hz", "w = 2 * np.pi * 2 * fd_hz")),
        ('one tap\'s rng not seeded with the tap index', ("return np.random.default_rng([seed, TAP_TAG, k, tap])",
                                                         "return np.random.default_rng([seed, TAP_TAG, k, 1 if tap == 2 else tap])")),
        ('n_samples off by one', ("snr_eff_db=None, n_samples=int(total),", "snr_eff_db=None, n_samples=int(total) + 1,")),
        ('the reference noise on a different seed', ("iq[lo:hi] += noise_block(seed, index, hi - lo, fs)", "iq[lo:hi] += noise_block(seed + 1, index, hi - lo, fs)")),
        ('the delayed tap not extended past the burst end (tail truncated)', ("iq[lo:lo + length] += y.astype(np.complex64)",
         "iq[lo:lo + length] += np.where(np.arange(length) < len(built[0][1]), y, 0).astype(np.complex64)")),
        ('the tap delay in the wrong unit (ms for us)', ("taus = [t['delay_us'] * 1e-6 * fs for t in taps]", "taus = [t['delay_us'] * 1e-3 * fs for t in taps]")),
        ('the isi definition inverted', ("isi = float(10 * np.log10(delayed / first))", "isi = float(10 * np.log10(first / delayed))")),
    ]
    # p3 is load-bearing for the phase-term mutant: a 1.0 us tap at an integer-MHz carrier has exp(-j 2 pi f_c tau) = 1, so p2b
    # cannot see a dropped phase term. That mutant runs explicitly on p3 and p4 (0.4, 0.3 and 0.6 us taps); the others on p3.
    for name, (old, new) in cases:
        mod = mutant_module(old, new)
        for profile in (('p3', 'p4') if 'phase term' in name else ('p3',)):
            with quiet() as failed:
                try:
                    sample_checks(mod, 'mutant', profile=profile)
                except Exception as ex:
                    failed.append('raised %s: %s' % (type(ex).__name__, str(ex)[:60]))
            caught(name + ' [%s]' % profile, failed)
    with quiet() as failed:
        sample_checks(m, 'control')
    check(not failed, 'the unmutated generator passes the same checks (positive control)%s' % (' - failed: %s' % failed[:2] if failed else ''))


# --- the whole -------------------------------------------------------------------------------------------------------

def tests():
    t0 = time.time()
    mark = lambda w: print('       [%.0f s] %s' % (time.time() - t0, w), flush=True)  # noqa: E731
    print('The plan: the full-size sidecar and the table\n')
    check_profile_table()
    _, full = m.synthesise_multipath('p4', 24.0, 300.0, name='full', paired_with='fade_ref_snr24')
    ref_full = g.synthesise_fade('ref', 24.0)[1]
    check(full['n_samples'] == ref_full['n_samples'] and len(full['bursts']) == 204 and full['n_samples'] % m.BLOCK_SAMPLES == 0,
          'full: %d samples, the reference\'s, whole noise blocks, 204 bursts' % full['n_samples'])
    check(all(x['start_sample'] == y['start_sample'] and x['clk'] == y['clk'] and x['channel'] == y['channel'] and x['timing_frac'] == y['timing_frac']
              and x['air_bits'] == y['air_bits'] for x, y in zip(full['bursts'], ref_full['bursts'])),
          'full: the 204 bursts\' starts, clocks, channels, timing fractions and bits are the reference\'s')
    check_top(full, 'full p4', 24.0)
    check_file_size()

    mark('plan done')
    print('\nSmall runs: 24 bursts, 6 of each type, noise blocks of %d' % BLOCK)
    for profile in PROFILES:
        for snr, fd in ((18.0, 300.0), (24.0, 100.0)) if profile in ('p2b', 'p4') else ((18.0, 300.0),):
            label = '%s fd%g snr%g' % (profile, fd, snr)
            iq, side = run(profile, snr, fd)
            check_top(side, label, snr)
            check_pairing_and_sum(iq, side, label, profile, snr, fd)
            check_truth(iq, side, label, profile, snr, fd)
    mark('small runs done')
    check_reduction()
    check_fractional()
    mark('reduction, fractional done')

    print('\nThe channel from the samples (30 dB, 120 bursts)')
    cs_p4 = None
    for profile in PROFILES:
        cs = check_channel(profile)
        if profile == 'p4':
            cs_p4 = cs
    check_doppler_first_tap('p4', 300.0, 120, cs_p4)
    iq, side = run('p3', 30.0, 100.0, 120)
    cs = channel_blocks(iq, ref(30.0, 120)[0], noise_of(side), side, 'p3')
    check_doppler_first_tap('p3', 100.0, 120, cs)

    mark('channel done')
    print('\nThe paths: statistics over thousands of independent processes')
    for profile, fd in (('p4', 300.0), ('p2a', 100.0)):
        check_paths(m, 'paths %s fd%g' % (profile, fd), fd=fd, profile=profile)

    mark('paths done')
    print('\nThe ISI is real, and the errors cluster')
    check_isi()
    mark('isi done')
    check_mutants()
    failed = [w for ok, w in RESULTS if not ok]
    print('\n%.0f s' % (time.time() - t0))
    print('RESULT: %s' % ('FAIL (%d)' % len(failed) if failed else 'PASS'))
    return 1 if failed else 0


# --- what the written files do ---------------------------------------------------------------------------------------

def load(directory, name):
    side = json.load(open(os.path.join(directory, 'synth_%s.json' % name)))
    iq = np.memmap(os.path.join(directory, 'synth_%s.cf32' % name), dtype='<c8', mode='r')
    return iq, side


def analyse(directory, delivered='/media/user/4TB/sdr-synth-tmp/fade'):
    """For the written files: two against the delivered references over the first 0.3 s, the whole files' statistics,
    and the genie receiver's burst-level yield, BER and clustering of every file."""
    names = [n for n, _ in m.set_files()]
    for n in names:
        for ext in ('cf32', 'json'):
            assert os.path.exists(os.path.join(directory, 'synth_%s.%s' % (n, ext))), 'missing %s.%s' % (n, ext)
    n03 = int(0.3 * FS)
    print('Sizes, and the pairing with the delivered references (%s) over the first 0.3 s (%d samples)' % (delivered, n03))
    for n in names:
        iq, side = load(directory, n)
        check(os.path.getsize(os.path.join(directory, 'synth_%s.cf32' % n)) == 8 * side['n_samples'] == 8 * len(iq),
              '%s: file size is 8 * n_samples = %d' % (n, 8 * side['n_samples']))
    for n in ('mp_p2b_fd300_snr24', 'mp_p4_fd100_snr18'):
        iq, side = load(directory, n)
        snr = int(side['snr_db'])
        rv_iq = np.memmap(os.path.join(delivered, 'synth_fade_ref_snr%d.cf32' % snr), dtype='<c8', mode='r')
        check(side['n_samples'] == len(rv_iq), '%s: the same length as the delivered reference (%d)' % (n, len(rv_iq)))
        part = [e for e in side['bursts'] if e['end_sample'] + 400 < n03]
        noise = t.my_noise(side['seed'], side['n_samples'], block=side['noise_block_samples'])[:n03]
        a, b = np.asarray(iq[:n03]), np.asarray(rv_iq[:n03])
        profile = side['profile']
        maxtau = max(d for d, _ in PROFILES[profile]) * 1e-6 * FS
        regions = my_regions(dict(side, bursts=part), maxtau)
        inside = np.zeros(n03, bool)
        for lo, hi in regions:
            inside[lo:hi] = True
        check(np.array_equal(a[~inside], b[~inside]) and np.array_equal(a[~inside], noise[~inside]),
              '%s: off the %d bursts of the first 0.3 s the file is the delivered reference, exactly (%d samples)' % (n, len(part), (~inside).sum()))
        sub = dict(side, bursts=part)
        regions, exp = my_expected(sub, profile, snr, side['fd_hz'])
        worst = max(float(np.abs(a[lo:hi].astype(np.complex128) - noise[lo:hi] - y).max()) for (lo, hi), y in zip(regions, exp))
        ref_worst = 0.0
        for (lo, hi), e in zip(regions, part):
            ref_worst = max(ref_worst, float(np.abs(a[lo:hi].astype(np.complex128)[:LEAD - 40] - b[lo:hi].astype(np.complex128)[:LEAD - 40]).max()))
        check(worst < FLOAT_ABS, '%s: in those bursts file - noise is the model built here, worst %.1e' % (n, worst))
    print('\nThe whole files: E|G|^2 and the level-crossing rate of the sidecars\' gains, the ISI')
    for n in names:
        side = json.load(open(os.path.join(directory, 'synth_%s.json' % n)))
        gains = [10 ** (np.array(e['mp_gain_db']) / 10) for e in side['bursts']]
        per = np.array([x.mean() for x in gains])
        pooled = float(np.concatenate(gains).mean())
        fd = side['fd_hz']
        lcr = []
        for rho_db in (-6.0, -10.0):
            rho = 10 ** (rho_db / 20)
            cross = dur = 0
            for e in side['bursts']:
                r = 10 ** (np.array(e['mp_gain_db']) / 20)
                cross += int(np.sum((r[:-1] < rho) & (r[1:] >= rho)))
                dur += len(r) * 1e-6
            lcr.append('LCR(%g dB) %.0f/s vs %.0f' % (rho_db, cross / dur, math.sqrt(2 * math.pi) * fd * rho * math.exp(-rho ** 2)))
        isi = np.array([e['mp_isi_db'] for e in side['bursts']])
        print('  %-22s E|G|^2 = %.3f (se %.3f)  %s  | mp_isi_db median %.1f, mean %.1f' % (
            n, pooled, per.std() / math.sqrt(len(per)), '  '.join(lcr), np.median(isi), np.mean(isi)))
    print('\nWhat the files do: genie start and channel, 0.8 MHz FIR demodulator, libbtbb')
    print('  %-22s %8s %8s %7s | burst-level payload correct, DM1 / DM3 / DM5 / FHS' % ('file', 'BER', 'Fano32', 'run'))
    taps = bt_synth_acl.rx_taps(FS)
    rows = []
    for n in names:
        iq, side = load(directory, n)
        errors, ok = [], {x: [0, 0] for x in g.TYPES}
        for e in side['bursts']:
            bits, _ = bt_synth_acl.rx_burst(iq, e, e['lap'], FS, CENTER, taps, search=30)
            ok[e['ptype']][0] += 1
            if bits is None:
                errors.append(None)
                continue
            want = np.array(t.hex_bits(e['air_bits'], e['air_bits_length']))
            errors.append((bits != want).astype(np.int8))
            ok[e['ptype']][1] += bool(ft.decode_ok(bits, e))
        idx, run_len, ber = ft.fano(errors)
        print('  %-22s %8.4f %8.1f %7.2f | %s' % (n, ber, idx, run_len, ' / '.join('%d/%d = %3.0f %%' % (ok[x][1], ok[x][0], 100 * ok[x][1] / ok[x][0]) for x in g.TYPES)), flush=True)
        rows.append((n, ber))
    failed = [w for okk, w in RESULTS if not okk]
    print('\nRESULT: %s' % ('FAIL (%d)' % len(failed) if failed else 'PASS'))
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--analyse', metavar='DIR', help='analyse the files of --set mp written in DIR instead of running the tests')
    args = ap.parse_args()
    return analyse(args.analyse) if args.analyse else tests()


if __name__ == '__main__':
    sys.exit(main())
