#!/usr/bin/env python3
"""The RDS receiver's stereo indicator: "Stereo pilot locked" only for a
pilot that stands out from the noise beside it.

It once said so on every empty channel. With no station the discriminator
turns noise into a multiplex full of noise, and the pilot's band read more
than a real pilot's - past the fixed level (1e-4) it was judged by.

Synthetic stations through the receiver's own channel filter and
discriminator (``fm_front_end``) and its ``PilotMeter``, polled as the window
polls it: noise alone and mono stations must never lock, stereo ones must
always. Pure software - no radio involved.

    python scripts/test_rds_pilot.py        # about 10 s
"""
import os
import sys
import time

import numpy as np
from gnuradio import blocks, gr  # type: ignore
from scipy import signal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps.rdsReceiver import (  # noqa: E402
    LO_OFFSET, MAX_DEVIATION, MPX_RATE, PilotMeter, fm_front_end,
)

RATE = 2e6                 # the HackRF's rate in the receiver
SECONDS = 3.0
PILOT_HZ = 19e3


def multiplex(pilot_level, seconds=SECONDS):
    """Left a 1 kHz tone, right a 2.5 kHz one, as shares of full deviation."""
    t = np.arange(int(seconds * MPX_RATE)) / MPX_RATE
    left, right = 0.8 * np.sin(2 * np.pi * 1e3 * t), 0.8 * np.sin(2 * np.pi * 2.5e3 * t)
    wp = 2 * np.pi * PILOT_HZ * t
    mid, side = (left + right) / 2, (left - right) / 2
    return 0.45 * mid + 0.45 * side * np.sin(2 * wp) + pilot_level * np.sin(wp)


def fm_iq(mpx, snr_db, seed):
    """The MPX frequency-modulated at ``RATE``, the station ``LO_OFFSET``
    above the LO as the receiver tunes it, in noise ``snr_db`` below it in
    a 200 kHz channel."""
    up = int(RATE // MPX_RATE)
    mpx_up = signal.resample_poly(mpx, up, 1)
    phase = 2 * np.pi * MAX_DEVIATION * np.cumsum(mpx_up) / RATE
    t = np.arange(len(mpx_up)) / RATE
    iq = 0.5 * np.exp(1j * (phase + 2 * np.pi * LO_OFFSET * t))
    return (iq + noise(len(iq), 0.25 / 10 ** (snr_db / 10) * (RATE / 200e3), seed)).astype(np.complex64)


def noise(n, power, seed):
    rng = np.random.default_rng(seed)
    return np.sqrt(power / 2) * (rng.standard_normal(n) + 1j * rng.standard_normal(n))


def readings(iq):
    """``iq`` through the receiver's front end and pilot meter as fast as it
    goes: (level, snr_db, locked) every few ms, from half a second in."""
    tb = gr.top_block()
    src = blocks.vector_source_c(iq, False)
    channel, demod = fm_front_end(RATE)
    to_complex = blocks.float_to_complex(1)
    meter = PilotMeter(tb, to_complex)
    sink = blocks.null_sink(gr.sizeof_gr_complex)
    tb.connect(src, channel, demod, to_complex)
    tb.connect(meter.bpf, sink)                    # where the PLL would be
    tb.start()
    out = []
    try:
        while demod.nitems_written(0) < 0.5 * MPX_RATE:
            time.sleep(0.002)
        while src.nitems_written(0) < len(iq) - 0.05 * RATE:
            out.append((meter.level(), meter.snr_db(), meter.locked()))
            time.sleep(0.005)
    finally:
        tb.stop()
        tb.wait()
    assert len(out) > 20, len(out)
    return out


def main():
    n = int(SECONDS * RATE)
    stereo, mono = multiplex(0.09), multiplex(0.0)
    cases = [
        ('noise alone', noise(n, 1e-4, 3).astype(np.complex64), False),
        ('mono, SNR 5 dB', fm_iq(mono, 5, 5), False),
        ('mono, SNR 35 dB', fm_iq(mono, 35, 35), False),
        ('stereo, SNR 35 dB', fm_iq(stereo, 35, 36), True),
        ('stereo, SNR 10 dB', fm_iq(stereo, 10, 10), True),
    ]
    failed = []
    for name, iq, want in cases:
        got = readings(iq)
        levels = [g[0] for g in got]
        snrs = [g[1] for g in got]
        locked = sum(g[2] for g in got)
        ok = locked == (len(got) if want else 0)
        print(f"{'PASS' if ok else 'FAIL'}  {name:18s} pilot level {min(levels):.1e}-{max(levels):.1e}, "
              f"{min(snrs):5.1f} to {max(snrs):5.1f} dB over the noise, locked {locked}/{len(got)}")
        if not ok:
            failed.append(name)
        if name == 'noise alone' and min(levels) <= 1e-3:
            # The check has teeth only if noise passes the old level test.
            print(f"FAIL  noise read {min(levels):.1e}: no longer past the old 1e-4 level")
            failed.append('teeth')
    print("pilot indicator: all checks passed" if not failed else f"FAILED: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
