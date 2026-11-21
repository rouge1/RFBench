#!/usr/bin/env python3
"""Decode an FM capture's RDS at every symbol timing, and measure the multiplex.

    python scripts/rds_tau_sweep.py CAPTURE        # CAPTURE.cfile + CAPTURE.json

The capture is what ``scripts/test_rds_core.py`` reads: raw complex64 IQ, and a
JSON sidecar with ``rate``, ``offset_hz`` (station minus tuned centre) and
``station_hz``. ``scripts/bb60_tone.py --out`` records one: tune 250 kHz below
the station and write ``offset_hz`` 250000.

Prints the multiplex levels against the pilot (audio, L-R, RDS), the RDS band
against the noise just beside it, then decodes the whole capture once as the
receiver does - ``RdsDemod`` picking ``tau`` itself - and once with each of the
16 candidate ``tau`` values forced. Every line gives blocks good *and blocks
seen*: a decoder that loses sync stops counting, so a pick can show a high
percentage over fewer blocks than were sent. Read the counts, not only the
percentage. About 45.7 blocks are sent per second.

What it answers: whether a link's block errors come from the timing pick
(some forced tau decodes far better than the receiver's own) or from the
signal (no tau does). Used for the BB60D off-air finding in knowledge/rds.md.
No radio; reads the capture only.
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps.rds_core import (  # noqa: E402
    RdsDemod, RdsProtocol, fm_demodulate, software_pilot_pll,
)

MPX_RATE = 250e3
CHUNK = 8192
#: RDS blocks per second: 1187.5 bit/s over 26 bits a block.
BLOCKS_PER_S = 1187.5 / 26


def levels(mpx, fs):
    n = 1 << 16
    seg = mpx[: len(mpx) // n * n].reshape(-1, n)
    p = np.mean(np.abs(np.fft.rfft(seg * np.hanning(n), axis=1)) ** 2, 0)
    f = np.fft.rfftfreq(n, 1 / fs)

    def band(a, b):
        return p[(f >= a) & (f < b)].sum()

    def dens(a, b):
        return p[(f >= a) & (f < b)].mean()

    pilot = band(18.9e3, 19.1e3)
    rds = band(54.6e3, 59.4e3)
    noise = ((dens(60e3, 63e3) + dens(51e3, 54e3)) / 2
             * ((f >= 54.6e3) & (f < 59.4e3)).sum())
    db = lambda x, y: 10 * np.log10(x / y)  # noqa: E731
    print(f"MPX re pilot: audio {db(band(30, 15e3), pilot):+.1f} dB, "
          f"L-R {db(band(23e3, 53e3), pilot):+.1f} dB, RDS {db(rds, pilot):+.1f} dB")
    print(f"RDS band (54.6-59.4 kHz) over the noise beside it: {db(rds, noise):.1f} dB")


def decode(mpx, ref, fs, tau, region):
    d = RdsDemod(fs, tau=tau)
    p = RdsProtocol(region=region)
    for i in range(0, len(mpx), CHUNK):
        bits = d.feed(mpx[i:i + CHUNK], ref[i:i + CHUNK])
        if len(bits):
            p.feed(bits)
    s = p.snapshot()
    ok, seen = s['blocks_ok'], s['blocks_seen']
    return (d.tau, f"{ok:5d}/{seen:5d} seen = {100 * ok / max(1, seen):5.1f}%",
            s['ps'])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('base', help='capture path without extension')
    ap.add_argument('--seconds', type=float, default=None)
    ap.add_argument('--region', default='RBDS')
    a = ap.parse_args()
    with open(a.base + '.json') as f:
        meta = json.load(f)
    count = int(meta['rate'] * a.seconds) if a.seconds else -1
    iq = np.fromfile(a.base + '.cfile', np.complex64, count=count)
    secs = len(iq) / meta['rate']
    print(f"capture: {meta['station_hz']/1e6:.3f} MHz, {secs:.1f} s at "
          f"{meta['rate']/1e6:g} MS/s - about {secs * BLOCKS_PER_S:.0f} blocks sent")
    mpx, fs = fm_demodulate(iq, meta['rate'], meta['offset_hz'], MPX_RATE)
    del iq
    ref = software_pilot_pll(mpx, fs)
    levels(mpx, fs)
    tau, res, ps = decode(mpx, ref, fs, None, a.region)
    print(f"receiver's own pick: tau {tau:.4f}  {res}  PS {ps!r}")
    for i in range(RdsDemod.TAU_STEPS):
        tau, res, ps = decode(mpx, ref, fs, i / RdsDemod.TAU_STEPS, a.region)
        print(f"  tau {tau:.4f}  {res}  PS {ps!r}")


if __name__ == '__main__':
    main()
