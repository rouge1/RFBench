#!/usr/bin/env python3
"""``band_stitch`` joins two simulated receivers into one coherent band - no radio, no recording.

    python -B scripts/test_band_stitch.py

A known wideband signal at 40 MS/s, centred 915 MHz - 26 tones 902.5..927.5 MHz, a sawtooth chirp 903 -> 927 MHz
every 10 ms, a few 1 ms silences - seen by two simulated receivers as on the bench (knowledge/radio-use-cases.md):

* **low**: centred 909 MHz, 40 MS/s, its band only, noise;
* **high**: centred 921 MHz, its band only, then the things two radios on their own clocks do to each other - a
  170 Hz frequency offset, a random phase walk (a few radians a second), a 0.37-sample delay - resampled to
  25 MS/s, starting 0.11 s later, noise.

Checked, by compressing the chirp against its known shape in each chirp period:

* tracked (the default): the halves add in phase - their compressed peaks sum to the joined one within 5 %, the
  phase between them stays within 0.3 rad in every period - and the joined pulse is narrower than either half's;
  the tones lie within 2.5 dB of one another; the offset is measured as 170 Hz;
* ``fixed_phase`` on the same wandering input: the phase between the halves spreads over the periods - the
  control that shows the tracking is doing the work;
* ``fixed_phase`` with no wander (a shared clock): coherent again.
"""
import os
import sys

import numpy as np
from scipy import signal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from band_stitch import stitch  # noqa: E402

R, FC = 40e6, 915e6
DUR = 0.8
T_CHIRP = 0.010
failures = []


def check(ok, what):
    print('  %s %s' % ('ok  ' if ok else 'FAIL', what), flush=True)
    if not ok:
        failures.append(what)


def truth(rng):
    n = int(DUR * R); t = np.arange(n) / R
    x = np.zeros(n, np.complex128)
    tones = np.array([-12.5e6 + k * 1e6 + 37 * k for k in range(26)])
    for f in tones:
        x += np.exp(1j * (2 * np.pi * f * t + rng.uniform(0, 2 * np.pi)))
    tt = np.mod(t, T_CHIRP)
    x += np.exp(1j * 2 * np.pi * (-12e6 * tt + 24e6 / T_CHIRP * tt ** 2 / 2))
    for s in (0.07, 0.29, 0.41, 0.66):
        x[int(s * R): int((s + 0.001) * R)] = 0
    return (x / 26).astype(np.complex64), tones


def lowpass(x, half):
    h = signal.firwin(801, half, fs=R).astype(np.complex64)
    return signal.fftconvolve(x, h, mode='same').astype(np.complex64)


def receivers(x, rng, walk=True):
    t = np.arange(x.size) / R
    noise = lambda n: (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64) * 2e-4
    low = lowpass(x * np.exp(2j * np.pi * 6e6 * t).astype(np.complex64), 13.5e6)
    low = low + noise(low.size)
    hi = lowpass(x * np.exp(-2j * np.pi * 6e6 * t).astype(np.complex64), 11e6)
    phase = 2 * np.pi * 170.0 * t
    if walk:                                       # a random walk, a few rad/s, smooth on a ms scale
        steps = rng.standard_normal(int(DUR * 1000)) * 0.12
        phase = phase + np.interp(t, np.arange(steps.size) / 1000, np.cumsum(steps))
    hi = (hi * np.exp(1j * phase)).astype(np.complex64)
    X = np.fft.fft(hi); f = np.fft.fftfreq(hi.size)
    hi = np.fft.ifft(X * np.exp(-2j * np.pi * f * 0.37)).astype(np.complex64)   # 0.37 samples late
    hi = signal.resample_poly(hi, 5, 8).astype(np.complex64)                     # 25 MS/s
    hi = hi[int(0.11 * 25e6):]
    return low, hi + noise(hi.size)


def chirp_stats(joined, lo, hi):
    """Per chirp period: the compressed peaks of the halves against the joined one, and the phase between them."""
    n = int(T_CHIRP * R); tt = np.arange(n) / R
    tmpl = np.conj(np.exp(1j * 2 * np.pi * (-12e6 * tt + 24e6 / T_CHIRP * tt ** 2 / 2))[::-1])
    mj = signal.fftconvolve(joined, tmpl, mode='valid')
    ml = signal.fftconvolve(lo, tmpl, mode='valid'); mh = signal.fftconvolve(hi, tmpl, mode='valid')
    peaks = signal.find_peaks(np.abs(mj), distance=int(0.8 * n), height=0.5 * np.abs(mj).max())[0]
    ratio = np.array([(abs(ml[p]) + abs(mh[p])) / abs(mj[p]) for p in peaks])
    dphi = np.angle(np.array([mh[p] / ml[p] for p in peaks]))

    def width(m, p):
        a = np.abs(m); h = a[p] / np.sqrt(2); i = j = p
        while i > 0 and a[i] > h: i -= 1
        while j < a.size - 1 and a[j] > h: j += 1
        return j - i
    p = peaks[len(peaks) // 2]
    return ratio, dphi, width(mj, p), width(ml, p), width(mh, p)


def tone_spread(joined, tones):
    f, P = signal.welch(joined, fs=R, nperseg=1 << 16, return_onesided=False, scaling='spectrum')
    lev = np.array([10 * np.log10(P[np.abs(f - t) < 2e3].max()) for t in tones])
    return lev.max() - lev.min()


def main():
    rng = np.random.default_rng(20261125)
    x, tones = truth(rng)
    quiet = lambda *a: None

    print('two receivers on their own clocks, phase tracked')
    low, hi = receivers(x, rng, walk=True)
    j, lo, hh, meta = stitch(low, R, 909e6, hi, 25e6, 921e6, 13e6, 10e6, secs=0.5, log=quiet)
    ratio, dphi, wj, wl, wh = chirp_stats(j, lo, hh)
    check(abs(meta['high_minus_low_hz'] - 170) < 3, f"offset measured {meta['high_minus_low_hz']:+.2f} Hz of +170")
    check(meta['phase_walk_rad'] > 1.0, f"the phase walk was there to track: {meta['phase_walk_rad']:.1f} rad")
    check(np.all(np.abs(ratio - 1) < 0.05), f"halves sum to the joined peak in all {ratio.size} chirp periods "
          f"({ratio.min():.3f}-{ratio.max():.3f})")
    check(np.all(np.abs(dphi) < 0.3), f"phase between the halves within 0.3 rad in every period "
          f"(worst {np.abs(dphi).max():.3f})")
    check(wj < min(wl, wh), f"joined pulse {wj} samples, narrower than either half ({wl}, {wh})")
    sp = tone_spread(j, tones)
    check(sp < 2.5, f"26 tones within {sp:.2f} dB")

    print('the same input, one fixed phase (the control)')
    j, lo, hh, meta = stitch(low, R, 909e6, hi, 25e6, 921e6, 13e6, 10e6, secs=0.5, fixed_phase=True, log=quiet)
    ratio, dphi, *_ = chirp_stats(j, lo, hh)
    check(np.ptp(np.unwrap(dphi)) > 1.0, f"without tracking the phase between the halves spreads "
          f"{np.ptp(np.unwrap(dphi)):.2f} rad over the periods")

    print('a shared clock (no phase walk), one fixed phase')
    low, hi = receivers(x, rng, walk=False)
    j, lo, hh, meta = stitch(low, R, 909e6, hi, 25e6, 921e6, 13e6, 10e6, secs=0.5, fixed_phase=True, log=quiet)
    ratio, dphi, *_ = chirp_stats(j, lo, hh)
    check(np.all(np.abs(ratio - 1) < 0.05) and np.all(np.abs(dphi) < 0.3),
          f"coherent: halves sum {ratio.min():.3f}-{ratio.max():.3f}, phase worst {np.abs(dphi).max():.3f} rad")

    print()
    if failures:
        print(f"{len(failures)} FAILED: {', '.join(failures)}")
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
