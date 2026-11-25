#!/usr/bin/env python3
"""Join two simultaneous recordings of adjacent bands into one band about twice as wide.

    python scripts/band_stitch.py LOW.cf32 LOW_RATE LOW_FC  HIGH.cf32 HIGH_RATE HIGH_FC  OUT.cf32 \\
        [--low-half 13e6] [--high-half 10e6] [--secs 2.5] [--fixed-phase] [--df HZ]

Two receivers, one tuned below the other, recording at the same time, their bands overlapping by a few MHz -
the BB60D at 40 MS/s and an N210 at 25 MS/s on the bench (knowledge/radio-use-cases.md, "A wider band"). The
output is complex64 at LOW_RATE, centred midway between the two centres: the low receiver below a seam in the
middle of the overlap, the high one above it. ``OUT_lowonly.cf32`` and ``OUT_highonly.cf32`` are the two halves
alone, and ``OUT.json`` what was measured.

**It is blind**: everything is measured from the overlap, the stretch of band both receivers saw - nothing about
the signal is assumed known. In order:

1. The high recording resampled to LOW_RATE and both shifted to the common centre.
2. **Frequency offset** between the two radios, from narrowband lines both see in the overlap (a straight line
   through the differences). It needs at least two such lines; without them pass ``--df`` (0 on a shared clock).
3. **Coarse time**: the overlap band's power envelopes cross-correlated - silences or bursts set it.
4. **Fine time and drift**: GCC-PHAT on the overlap band, whitened *inside the overlap only* (plain correlation is
   ruled by strong narrow tones and gives flat, ambiguous peaks; whitening empty bins lets their noise win), in
   segments across the recording, then a straight line through the lags.
5. **Phase**: tracked every 2 ms from the overlap - two radios on their own crystals wander (24 rad in 2.5 s was
   measured) - or, with ``--fixed-phase``, one complex gain for the whole recording, which is right only on a
   shared 10 MHz reference.
6. **Residual delay and phase at the seam** from the cross-spectrum, and **level** from the strong lines either
   side of the seam - not from |cross-spectrum / power|, which noise in the high half biases low.
7. **Join** with complementary filters crossing at the seam (1 MHz transition).

``LOW_HALF`` and ``HIGH_HALF`` are each receiver's usable half-width (13 MHz for the BB60D at 40 MS/s, 10 for the
N210 at 25); the overlap is what lies within both.
"""
import argparse
import json
import sys
from fractions import Fraction

import numpy as np
from scipy import fft as sfft
from scipy import signal

WORKERS = 8
TRACK_BLOCK_S = 0.002


def _shift(x, df, rate):
    n = np.arange(x.size, dtype=np.float64)
    return (x * np.exp(2j * np.pi * df / rate * n)).astype(np.complex64)


def _bandpass(x, lo, hi, rate, taps=801):
    c = (lo + hi) / 2
    h = signal.firwin(taps, (hi - lo) / 2, fs=rate)
    h = (h * np.exp(2j * np.pi * c / rate * np.arange(taps))).astype(np.complex64)
    return signal.fftconvolve(x, h, mode='same').astype(np.complex64)


def _parabola(y, k):
    if 0 < k < y.size - 1:
        a, b, c = y[k - 1:k + 2]
        den = a - 2 * b + c
        return 0.5 * (a - c) / den if den else 0.0
    return 0.0


def _lines(x, start, rate, lo, hi, n=None):
    """Frequencies of the strong narrow lines in [lo, hi] (Hz) in ``n`` samples from ``start``."""
    n = n or int(min(rate, x.size - start))
    seg = x[start: start + n].astype(np.complex128)
    X = np.abs(sfft.fft(seg * np.hanning(seg.size), workers=WORKERS))
    fr = sfft.fftfreq(seg.size, 1 / rate)
    m = (fr > lo) & (fr < hi)
    if not m.any():
        return np.array([])
    Xm = np.where(m, X, 0)
    # A line must stand 30x over the floor and within 20 dB of the strongest one: on a clean signal a periodic
    # wideband component (a chirp repeating every 10 ms is a comb of lines 100 Hz apart) also clears the floor,
    # and its lines cannot be matched between receivers.
    thresh = max(30 * np.median(X[m]), 0.1 * Xm.max())
    guard = int(50e3 / (rate / seg.size))
    out = []
    for _ in range(64):
        k = int(np.argmax(Xm))
        if Xm[k] < thresh:
            break
        out.append(fr[k] + _parabola(np.log(X + 1e-30), k) * rate / seg.size)
        Xm[max(0, k - guard): k + guard] = 0
    return np.sort(np.array(out))


def stitch(low, low_rate, low_fc, high, high_rate, high_fc, low_half=13e6, high_half=10e6, secs=2.5,
           fixed_phase=False, df=None, log=print):
    """Stitch two recordings; returns ``(joined, low_only, high_only, meta)``, all at ``low_rate``."""
    R = float(low_rate)
    FC = (low_fc + high_fc) / 2
    ratio = Fraction(R / high_rate).limit_denominator(1000)
    if abs(float(ratio) * high_rate - R) > 1:
        raise ValueError(f"no small ratio between {high_rate} and {R}")
    hi = signal.resample_poly(np.asarray(high, np.complex64), ratio.numerator, ratio.denominator).astype(np.complex64)
    lo = _shift(np.asarray(low, np.complex64), low_fc - FC, R)
    hi = _shift(hi, high_fc - FC, R)
    lo_ov = max(low_fc - low_half, high_fc - high_half) - FC
    hi_ov = min(low_fc + low_half, high_fc + high_half) - FC
    if hi_ov - lo_ov < 2e6:
        raise ValueError(f"the bands overlap by {(hi_ov - lo_ov)/1e6:.2f} MHz; at least 2 MHz is needed")
    seam = (lo_ov + hi_ov) / 2
    log(f"common centre {FC/1e6:.3f} MHz; overlap {(FC+lo_ov)/1e6:.3f}-{(FC+hi_ov)/1e6:.3f} MHz; "
        f"seam {(FC+seam)/1e6:.3f} MHz")

    # coarse time, from the overlap band's power envelopes (25 us blocks)
    ob, oh = _bandpass(lo, lo_ov, hi_ov, R), _bandpass(hi, lo_ov, hi_ov, R)
    B = 1000

    def env(x):
        e = (np.abs(x[: x.size // B * B].reshape(-1, B)) ** 2).mean(1)
        return e - e.mean()
    eb, eh = env(ob), env(oh)
    c = signal.fftconvolve(eb, eh[::-1], mode='full')
    L0 = (int(np.argmax(c)) - (eh.size - 1)) * B          # high[n] ~ low[n + L0]
    log(f"coarse offset {L0} samples ({L0/R*1e3:+.3f} ms): high[n] ~ low[n + offset]")

    # frequency offset, from the lines both see
    if df is None:
        s_lo = max(0, L0) + int(0.1 * R)
        n = int(min(R, lo.size - s_lo, hi.size - (s_lo - L0)))
        fl = _lines(lo, s_lo, R, lo_ov + 0.3e6, hi_ov - 0.3e6, n)
        fh = _lines(hi, s_lo - L0, R, lo_ov + 0.3e6, hi_ov - 0.3e6, n)
        pairs = [(f, fh[np.argmin(np.abs(fh - f))]) for f in fl if fh.size and np.min(np.abs(fh - f)) < 2e3]
        if len(pairs) < 2:
            raise ValueError(f"{len(pairs)} narrow lines seen by both in the overlap; pass --df")
        fp = np.array([p[0] for p in pairs]); d = np.array([p[1] - p[0] for p in pairs])
        ok = np.abs(d - np.median(d)) < 50.0            # a mismatched pair is off by far more than any clock
        if ok.sum() < 2:
            raise ValueError("the lines seen by both disagree about the offset; pass --df")
        fp, d = fp[ok], d[ok]
        fit = np.polyfit(fp, d, 1)
        df = float(fit[1])
        log(f"{len(pairs)} lines seen by both: high - low {d.min():+.1f}..{d.max():+.1f} Hz, "
            f"{fit[1]:+.2f} Hz at the centre, {fit[0]*1e6:+.3f} Hz/MHz")
    hi = _shift(hi, -df, R); oh = _shift(oh, -df, R)

    # fine time and drift: GCC-PHAT inside the overlap, a line through the segments' lags
    seg, Wl = 1 << 17, 2000
    n_end = min(lo.size, hi.size + L0) - seg - Wl - 512
    Nf = 1 << int(np.ceil(np.log2(2 * seg + 2 * Wl)))
    fq = sfft.fftfreq(Nf, 1 / R)
    inband = (fq > lo_ov + 0.5e6) & (fq < hi_ov - 0.5e6)
    lags, ts, q = [], [], []
    for s in np.linspace(max(0, L0) + Wl + 512, n_end, 30).astype(int):
        xb = ob[s: s + seg].astype(np.complex128)
        xh = oh[s - L0 - Wl: s - L0 + seg + Wl].astype(np.complex128)
        if xb.size < seg or xh.size < seg + 2 * Wl:
            continue
        C = sfft.fft(xh, Nf, workers=WORKERS) * np.conj(sfft.fft(xb, Nf, workers=WORKERS))
        r = np.abs(sfft.ifft(np.where(inband, C / (np.abs(C) + 1e-20), 0), workers=WORKERS))[: 2 * Wl + 1]
        k = int(np.argmax(r))
        lags.append(k - Wl + _parabola(r, k)); ts.append(s); q.append(r[k] / np.median(r))
    lags, ts, q = np.array(lags), np.array(ts), np.array(q)
    # A sharp peak is not a right one: a segment whose overlap holds only evenly spaced tones (a chirp elsewhere
    # in its sweep, say) correlates as well every 1/spacing - 40 samples for 1 MHz at 40 MS/s - and lands
    # anywhere, confidently. So the segments vote: the line through any two of them that the most others agree
    # with to a sample (drift makes it a line, not a constant), then a fit through those.
    cand = np.flatnonzero(q > 15)
    if cand.size < 3:
        raise ValueError(f"only {cand.size} of {lags.size} timing segments found a clear peak")
    best = None
    for i in range(cand.size):
        for k in range(i + 1, cand.size):
            a, b = cand[i], cand[k]
            slope = (lags[b] - lags[a]) / (ts[b] - ts[a])
            if abs(slope) > 5e-6:                         # more than 5 ppm apart is no pair of these radios
                continue
            inl = np.abs(lags[cand] - (lags[a] + slope * (ts[cand] - ts[a]))) < 1.0
            if best is None or inl.sum() > best.sum():
                best = inl
    if best is None or best.sum() < 3:
        raise ValueError("the timing segments agree on no line")
    keep = np.zeros(lags.size, bool); keep[cand[best]] = True
    lf = np.polyfit(ts[keep], lags[keep], 1)
    res = lags - np.polyval(lf, ts)
    log(f"fine timing: {keep.sum()} of {lags.size} segments; drift {lf[0]*1e6:+.3f} ppm; "
        f"residual {np.std(res[keep]):.3f} samples rms")

    # the high recording on the low one's sample grid: y[n] = high(n - L0 + lag(n))
    start = max(0, L0) + int(0.05 * R)
    N = min(int(secs * R), n_end - start)
    chunk, pad = 1 << 15, 1024
    y = np.zeros(N, np.complex64)
    for c0 in range(0, N, chunk):
        d = np.polyval(lf, start + c0 + chunk // 2)
        di = int(np.floor(d)); frac = d - di
        i0 = start + c0 - L0 + di - pad
        xs = hi[i0: i0 + chunk + 2 * pad].astype(np.complex128)
        if i0 < 0 or xs.size < chunk + 2 * pad:
            break
        xs = sfft.ifft(sfft.fft(xs) * np.exp(2j * np.pi * sfft.fftfreq(xs.size) * frac))
        m = min(chunk, N - c0)
        y[c0: c0 + m] = xs[pad: pad + m]
    x = lo[start: start + N]

    # phase: tracked every 2 ms, or one complex gain
    ox = _bandpass(x, lo_ov + 0.5e6, hi_ov - 0.5e6, R); oy = _bandpass(y, lo_ov + 0.5e6, hi_ov - 0.5e6, R)
    Bk = int(TRACK_BLOCK_S * R); nb = N // Bk
    gk = np.array([np.vdot(oy[i*Bk:(i+1)*Bk], ox[i*Bk:(i+1)*Bk]) / np.vdot(oy[i*Bk:(i+1)*Bk], oy[i*Bk:(i+1)*Bk])
                   for i in range(nb)])                    # ox ~ g * oy, per block
    walk = np.unwrap(np.angle(gk))
    if fixed_phase:
        g0 = np.vdot(oy, ox) / np.vdot(oy, oy)
        y = (y * g0).astype(np.complex64)
        log(f"one fixed gain {20*np.log10(abs(g0)):+.2f} dB, {np.angle(g0):+.3f} rad; the relative phase moved "
            f"{np.ptp(walk):.2f} rad over the recording (measured, not applied)")
    else:
        pw = np.array([np.vdot(oy[i*Bk:(i+1)*Bk], oy[i*Bk:(i+1)*Bk]).real for i in range(nb)])
        good = pw > 0.3 * np.median(pw)                   # blocks holding a silence are skipped
        tc = ((np.arange(nb) + 0.5) * Bk)[good]
        amp = np.convolve(np.abs(gk[good]), np.ones(5) / 5, 'same')
        ph = np.convolve(np.unwrap(np.angle(gk[good])), np.ones(3) / 3, 'same')
        nn = np.arange(N)
        y = (y * np.interp(nn, tc, amp) * np.exp(1j * np.interp(nn, tc, ph))).astype(np.complex64)
        log(f"phase tracked in {good.sum()} of {nb} blocks of {TRACK_BLOCK_S*1e3:g} ms; it walked "
            f"{np.ptp(ph):.1f} rad over {N/R:.2f} s")
    del ox, oy

    # residual delay and phase at the seam (cross-spectrum), then level from the lines either side
    nseg = int(min(R, N))
    f_, Pxy = signal.csd(x[:nseg], y[:nseg], fs=R, nperseg=1 << 12, return_onesided=False)
    _, Pyy = signal.welch(y[:nseg], fs=R, nperseg=1 << 12, return_onesided=False)
    near = np.abs(f_ - seam) < 2.0e6
    H = np.conj(Pxy[near]) / Pyy[near]                     # scipy's csd is conj(X)*Y; this is X/Y
    php = np.unwrap(np.angle(H)); pf = np.polyfit(f_[near], php, 1)
    res_delay = pf[0] / (2 * np.pi)
    Y = sfft.fft(y.astype(np.complex128), workers=WORKERS)
    y = (sfft.ifft(Y * np.exp(2j * np.pi * sfft.fftfreq(y.size, 1 / R) * res_delay), workers=WORKERS)
         * np.exp(1j * pf[1])).astype(np.complex64)
    del Y
    M = 1 << int(np.floor(np.log2(min(N, 1 << 22))))
    Xa = np.abs(sfft.fft(x[:M] * np.hanning(M), workers=WORKERS))
    Ya = np.abs(sfft.fft(y[:M] * np.hanning(M), workers=WORKERS))
    fm = sfft.fftfreq(M, 1 / R); near = np.abs(fm - seam) < 2.0e6
    strong = near & (Xa > 300 * np.median(Xa[near])) & (Ya > 300 * np.median(Ya[near]))
    level = float(np.median(Xa[strong] / Ya[strong])) if strong.sum() > 3 else 1.0
    y = (y * level).astype(np.complex64)
    log(f"seam: residual delay {res_delay*R:+.3f} samples, phase {pf[1]:+.3f} rad; level x{level:.3f} "
        f"from {strong.sum()} strong bins")

    # join
    taps, tw = 2001, 1.0e6
    lo_edge = low_fc - low_half - FC; hi_edge = high_fc + high_half - FC

    def band(v, a, b):
        h = signal.firwin(taps, (b - a) / 2, width=tw, fs=R)
        h = (h * np.exp(2j * np.pi * (a + b) / 2 / R * (np.arange(taps) - (taps - 1) / 2))).astype(np.complex64)
        return signal.fftconvolve(v, h, mode='same').astype(np.complex64)
    low_only = band(x, lo_edge, seam); high_only = band(y, seam, hi_edge)
    meta = {'rate': R, 'centre_hz': FC, 'seam_hz': FC + seam, 'band_hz': [FC + lo_edge, FC + hi_edge],
            'overlap_hz': [FC + lo_ov, FC + hi_ov], 'coarse_offset_samples': int(L0),
            'high_minus_low_hz': df, 'drift_ppm': float(lf[0] * 1e6), 'phase_walk_rad': float(np.ptp(walk)),
            'fixed_phase': bool(fixed_phase), 'samples': int(N)}
    return low_only + high_only, low_only, high_only, meta


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('low'); ap.add_argument('low_rate', type=float); ap.add_argument('low_fc', type=float)
    ap.add_argument('high'); ap.add_argument('high_rate', type=float); ap.add_argument('high_fc', type=float)
    ap.add_argument('out')
    ap.add_argument('--low-half', type=float, default=13e6, help='usable half-width of the low receiver, Hz')
    ap.add_argument('--high-half', type=float, default=10e6, help='usable half-width of the high receiver, Hz')
    ap.add_argument('--secs', type=float, default=2.5, help='how much to stitch')
    ap.add_argument('--fixed-phase', action='store_true', help='one complex gain, no tracking (a shared clock)')
    ap.add_argument('--df', type=float, help='high minus low frequency offset, Hz, instead of measuring it')
    a = ap.parse_args(argv)
    if a.low_fc >= a.high_fc:
        sys.exit("the low receiver's centre must be below the high one's")
    joined, lo, hi, meta = stitch(np.fromfile(a.low, np.complex64), a.low_rate, a.low_fc,
                                  np.fromfile(a.high, np.complex64), a.high_rate, a.high_fc,
                                  a.low_half, a.high_half, a.secs, a.fixed_phase, a.df)
    stem = a.out[:-5] if a.out.endswith('.cf32') else a.out
    joined.tofile(a.out); lo.tofile(stem + '_lowonly.cf32'); hi.tofile(stem + '_highonly.cf32')
    json.dump(meta, open(stem + '.json', 'w'), indent=1)
    print(f"wrote {a.out}: {meta['samples']} samples, {meta['band_hz'][0]/1e6:.1f}-{meta['band_hz'][1]/1e6:.1f} MHz")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
