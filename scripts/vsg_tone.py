#!/usr/bin/env python3
"""Loop a CW tone out of the VSG60 for a while, then turn it off.

    python scripts/vsg_tone.py 915e6 -40 12          # FREQ_HZ LEVEL_DBM SECONDS
    python scripts/vsg_tone.py 915e6 -40 12 --offset 0

The tone sits ``--offset`` (default +100 kHz) from the centre, so the VSG's own
LO leak, if any, stays apart from it in a receiver. It is 10000 samples at
1 MS/s - a whole number of cycles at 100 kHz, so the loop has no seam - played
from the device's own memory with ``repeat_waveform``; nothing streams over
USB while it plays. The IQ amplitude is 0.5, so what leaves the port is about
6 dB under LEVEL (not measured here; see knowledge/ettus.md).

Goes through ``apps/vsg_sink.py``, whose lock is what keeps a second open from
aborting the process (CLAUDE.md, Rules that bite). Run it on the machine the
VSG60 is plugged into, under ``timeout -k``; the VSG60 and a BB60D will not
stream on one host at the same time.
"""
import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'apps'))
import vsg_sink  # noqa: E402

RATE = 1e6
LENGTH = 10000


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument("freq", type=float, help="centre, Hz")
    p.add_argument("level", type=float, help="dBm, -120..+10")
    p.add_argument("secs", type=float)
    p.add_argument("--offset", type=float, default=100e3,
                   help="tone offset from centre, Hz; LENGTH/RATE must hold "
                        "a whole number of its cycles")
    a = p.parse_args()
    cycles = a.offset * LENGTH / RATE
    if abs(cycles - round(cycles)) > 1e-9:
        sys.exit(f"--offset {a.offset:g} Hz is not a whole number of cycles "
                 f"in {LENGTH} samples at {RATE:g} S/s; the loop would click.")
    n = np.arange(LENGTH)
    iq = (0.5 * np.exp(2j * np.pi * a.offset / RATE * n)).astype(np.complex64)
    s = vsg_sink.vsg_sink(center_freq=a.freq, sample_rate=RATE,
                          level_dbm=a.level)
    try:
        s.repeat_waveform(iq)
        print("playing", flush=True)
        time.sleep(a.secs)
    finally:
        s.stop_waveform()
        s._shutdown()
    print("done", flush=True)


if __name__ == "__main__":
    main()
