#!/usr/bin/env python3
"""Find the breaks in a recorded CW tone: dropouts and lost samples.

    python scripts/tone_gaps.py CAPTURE RATE [--format cf32|sc16] [--skip 0.5] [--near HZ] [--block 1000]

The check behind "no gap" in knowledge/two-radios-one-host.md and radio-use-cases.md: one radio sends a tone,
another records it, and any gap in the sending or the recording shows here. The tone is mixed to DC and averaged
in blocks of ``--block`` samples, then each block is checked for

* **a dropout**: amplitude under half the median - an underrun sends a gap, not missing samples;
* **a phase jump**: the step from the block before more than 0.5 rad (or ten times the median step) off the
  steady one - samples lost or repeated in the recording.

The tone's frequency is found, then refined by the phase slope. ``--near`` searches only within 100 kHz of an
offset, for a recording with more than one tone; DC and its neighbours are always skipped (an LO leak).

**The phase check cannot see every run of lost samples.** Losing k samples moves the phase by k·f/rate cycles, so
it is invisible whenever that is near a whole number - at exactly 1 MHz and 20 MS/s, every multiple of 20 samples.
No frequency escapes it for every k: 1,000,037 Hz, used on the bench, still hides 500 samples at 20 MS/s (25.0009
cycles). Choose one away from the drop sizes you expect, and for lost samples rely first on the radios' own
counters (UHD's ``O``/``D``, the BB60D's "Sample Overrun"); this check is strongest for dropouts. A burst of someone else's signal near the tone also trips both checks - look
at the band around the tone at a flagged moment before calling it a gap.

Prints one summary and up to ten of each kind of flag, and exits 1 if anything was flagged; ``analyse()``
returns them for a script.
"""
import argparse
import sys

import numpy as np


def _reader(path, fmt):
    if fmt == 'cf32':
        m = np.memmap(path, np.complex64, 'r')
        return m.size, lambda s, k: np.asarray(m[s:s + k], np.complex128)
    m = np.memmap(path, np.int16, 'r')

    def get(s, k):
        v = np.asarray(m[2 * s:2 * (s + k)], np.float64)
        return v[0::2] + 1j * v[1::2]
    return m.size // 2, get


def analyse(n, get, rate, skip=0.5, near=None, block=1000):
    """Return a dict: tone frequency, block amplitudes' median, SNR, and the dropout and jump times (s)."""
    s0 = int(skip * rate)
    if n - s0 < (1 << 20):
        raise ValueError(f"{(n - s0) / rate:.3f} s after the skip; at least {(1 << 20) / rate:.3f} s needed")
    x = get(s0, 1 << 20)
    X = np.abs(np.fft.fft(x * np.hanning(x.size)))
    fr = np.fft.fftfreq(x.size, 1 / rate)
    X[:50] = 0; X[-50:] = 0                                # DC and an LO leak
    if near is not None:
        X[np.abs(fr - near) > 100e3] = 0
    f = fr[int(np.argmax(X))]
    y = x * np.exp(-2j * np.pi * f * np.arange(x.size) / rate)
    ph = np.unwrap(np.angle(y[: y.size // block * block].reshape(-1, block).mean(1)))
    f += np.polyfit(np.arange(ph.size) * block / rate, ph, 1)[0] / (2 * np.pi)

    amps, steps = [], []
    chunk = int(rate // block) * block
    pos, last = s0, None
    while pos + block <= n:
        x = get(pos, int(min(chunk, (n - pos) // block * block)))
        t = (pos + np.arange(x.size)) / rate
        z = (x * np.exp(-2j * np.pi * f * t)).reshape(-1, block).mean(1)
        amps.append(np.abs(z))
        if last is not None:
            z = np.concatenate([[last], z])
            steps.append(np.angle(z[1:] * np.conj(z[:-1])))
        else:
            steps.append(np.concatenate([[0.0], np.angle(z[1:] * np.conj(z[:-1]))]))
        last = z[-1]
        pos += x.size
    amp = np.concatenate(amps); d = np.concatenate(steps)
    med = np.median(amp)
    dev = np.abs(np.angle(np.exp(1j * (d - np.median(d)))))
    low = np.flatnonzero(amp < 0.5 * med)
    jump = np.flatnonzero(dev > max(0.5, 10 * np.median(dev)))
    t_of = lambda i: s0 / rate + i * block / rate
    return {'tone_hz': f, 'blocks': int(amp.size), 'block': block, 'amp_median': float(med),
            'amp_snr_db': float(20 * np.log10(med / max(np.std(amp - med), 1e-12))),
            'step_noise_rad': float(np.median(dev)), 'worst_step_rad': float(dev.max()),
            'dropouts_s': [t_of(i) for i in low], 'dropout_amp': [float(amp[i] / med) for i in low],
            'jumps_s': [t_of(i) for i in jump], 'jump_rad': [float(dev[i]) for i in jump]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('capture'); ap.add_argument('rate', type=float)
    ap.add_argument('--format', choices=('cf32', 'sc16'), default='cf32', help='cf32, or UHD sc16 (short I/Q)')
    ap.add_argument('--skip', type=float, default=0.5, help='seconds to skip at the start')
    ap.add_argument('--near', type=float, help='look for the tone within 100 kHz of this offset, Hz')
    ap.add_argument('--block', type=int, default=1000, help='samples per block')
    a = ap.parse_args(argv)
    n, get = _reader(a.capture, a.format)
    r = analyse(n, get, a.rate, a.skip, a.near, a.block)
    print(f"{a.capture}: {n} samples = {n / a.rate:.3f} s at {a.rate / 1e6:g} MS/s; tone at "
          f"{r['tone_hz'] / 1e3:+.3f} kHz")
    print(f"  {r['blocks']} blocks of {r['block']}; amplitude SNR {r['amp_snr_db']:.1f} dB; phase step noise "
          f"{r['step_noise_rad']:.4f} rad (median), worst {r['worst_step_rad']:.3f} rad")
    print(f"  dropouts (amplitude under 50 %): {len(r['dropouts_s'])} blocks; phase jumps: {len(r['jumps_s'])} blocks")
    for t, v in list(zip(r['jumps_s'], r['jump_rad']))[:10]:
        print(f"    jump at {t:.4f} s: {v:.2f} rad")
    for t, v in list(zip(r['dropouts_s'], r['dropout_amp']))[:10]:
        print(f"    dropout at {t:.4f} s: amplitude {v:.2f}")
    return 1 if (r['dropouts_s'] or r['jumps_s']) else 0


if __name__ == '__main__':
    sys.exit(main())
