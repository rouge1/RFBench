#!/usr/bin/env python3
"""Summarise what a transmitter put on the air, from a BB60D capture.

    python scripts/bb60_tone.py 915e6 --rate 20e6 --secs 0.5 --att -20 --out cap.cfile
    python scripts/spectrum_report.py cap.cfile 20e6 915e6

Prints, for raw complex64 IQ at RATE tuned to CENTRE:

- the signal's power over the floor (dBFS), its power-weighted centre, and
  its 99 % occupied bandwidth with the two edges, relative to CENTRE;
- up to six peaks 15 dB or more over the floor - carriers, pilots, tones,
  LO leaks - each as an offset and a level;
- the envelope: its peak over its median, and how much of the capture sits
  above half-way between them - a burst transmitter (OOK, ISM) shows a high
  ratio and a small share, a constant-envelope one (FM, FSK) neither.

The outer 10 % each side is left out, where the BB60D's filter rolls off.
Levels are dBFS scaled for power (see bb60_tone.py), not dBm. What the
first run of every app on the N210 was judged by (knowledge/ettus.md): the
ATSC pilot at −2802.7 kHz rather than −2690.6 came from here.
"""
import sys

import numpy as np

NFFT = 1 << 16
#: Keep this fraction of the band: the BB60D's filter rolls off outside it.
KEEP = 0.9
BLOCK = 1000


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    x = np.fromfile(sys.argv[1], np.complex64)
    fs, fc = float(sys.argv[2]), float(sys.argv[3])
    seg = x[: len(x) // NFFT * NFFT].reshape(-1, NFFT)
    w = np.blackman(NFFT)
    p = np.mean(np.abs(np.fft.fftshift(np.fft.fft(seg * w, axis=1), axes=1)) ** 2,
                0) / (NFFT * np.sum(w ** 2))
    f = np.fft.fftshift(np.fft.fftfreq(NFFT, 1 / fs))
    inband = np.abs(f) < KEEP / 2 * fs
    floor = np.median(p[inband])
    s = np.clip(p - floor, 0, None) * inband
    total = s.sum()
    if total <= 0:
        print("nothing above the floor")
        return
    c = np.cumsum(s) / total
    lo, hi = f[np.searchsorted(c, 0.005)], f[np.searchsorted(c, 0.995)]
    centroid = (s * f).sum() / total
    print(f"signal {10*np.log10(total):.1f} dBFS above floor, centroid "
          f"{centroid/1e3:+.1f} kHz, 99% OBW {(hi-lo)/1e3:.1f} kHz "
          f"[{lo/1e3:+.1f} .. {hi/1e3:+.1f}] kHz re {fc/1e6:.3f} MHz")
    db = 10 * np.log10(p + 1e-30)
    fl = 10 * np.log10(floor)
    d = np.where(inband, db, -999.0)
    for _ in range(6):
        i = int(np.argmax(d))
        if d[i] < fl + 15:
            break
        print(f"  peak {f[i]/1e3:+10.2f} kHz  "
              f"{10*np.log10(p[max(i-3, 0):i+4].sum()):6.1f} dBFS  "
              f"{d[i]-fl:5.1f} dB over floor")
        d[max(i - 30, 0): i + 31] = -999
    env = np.abs(x[: len(x) // BLOCK * BLOCK]).reshape(-1, BLOCK).mean(1)
    med = np.median(env)
    on = env > med + 0.5 * (env.max() - med)
    print(f"  envelope: max/median {20*np.log10(env.max()/med):.1f} dB, "
          f"above half-way {100*on.mean():.1f}% of the time")


if __name__ == '__main__':
    main()
