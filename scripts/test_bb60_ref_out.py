#!/usr/bin/env python3
"""``bb60_source(ref_out=True)`` lends the BB60D's 10 MHz to an N210, through a start and a retune - on the radios.

    python scripts/test_bb60_ref_out.py --addr <the N210's address> [--pilot 602.309628e6]

Needs the BB60D, an N210 and an SMA cable from the BB60D's rear port 2 (*Trig/Sync In/Out*) to the N210's REF IN.
Nothing transmits: both radios receive the same off-air carrier at the same moment - by default the pilot of the ATSC
station on RF 36, 602.31 MHz, 40 dB clear on the BB60D here - and the carrier's frequency is compared between them.
On one clock they agree to a fraction of a hertz; free-running they differ by their clocks' ppm (0.19 ppm measured
here, about 115 Hz at 602 MHz).

**The N210's ``ref_locked`` sensor is not the yardstick.** With nothing on REF IN it reads True on some switches to
the external reference and False on others (2026-11-25), so it is printed, not checked.

* ``ref_out=True``, the N210 on its external reference: the carrier agrees within ``AGREE_HZ``; after
  ``set_center_freq`` and ``set_gain_percent`` (the SoapySDR module sets the ports back to off on a retune) it still
  agrees, and the output was applied again; samples flow at the rate asked throughout.
* ``ref_out`` off (the default), the N210 on its internal reference: the carrier differs by more than
  ``APART_HZ`` - the control that shows the measurement can tell the two apart.
"""
import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gnuradio import blocks, gr  # noqa: E402

from apps.bb60_source import bb60_source, overrun_count  # noqa: E402

RATE = 2.5e6                     # on both radios' ladders: 40/16 on the BB60D, 100/40 on the N210
NFFT = 1 << 21                   # 0.84 s at 2.5 MS/s, 1.2 Hz bins
AGREE_HZ = 2.0
APART_HZ = 20.0
failures = []


def check(ok, what):
    print('  %s %s' % ('ok  ' if ok else 'FAIL', what), flush=True)
    if not ok:
        failures.append(what)


def carrier(x, centre, pilot):
    """The carrier's frequency (Hz, RF) in a capture tuned to ``centre``: the FFT peak within 5 kHz of ``pilot``,
    refined by a parabola on the log magnitude."""
    x = np.asarray(x, np.complex128)[:NFFT]
    X = np.abs(np.fft.fft(x * np.hanning(x.size)))
    fr = np.fft.fftfreq(x.size, 1 / RATE)
    m = np.abs(centre + fr - pilot) < 5e3
    k = np.flatnonzero(m)[np.argmax(X[m])]
    a, b, c = np.log(X[k - 1:k + 2] + 1e-30)
    return centre + fr[k] + 0.5 * (a - c) / (a - 2 * b + c) * RATE / x.size


class Pair:
    """bb60_source streaming into a vector probe; the N210 read on demand at the same moment."""

    def __init__(self, usrp, centre, ref_out):
        self.usrp, self.centre = usrp, centre
        self.tb = gr.top_block('bb60 ref_out', catch_exceptions=True)
        self.src = bb60_source(centre, RATE, 60.0, ref_out=ref_out)
        self.s2v = blocks.stream_to_vector(gr.sizeof_gr_complex, NFFT)
        self.probe = blocks.probe_signal_vc(NFFT)
        self.tb.connect(self.src, self.s2v, self.probe)
        self.tb.start()

    def flowing(self, secs=3.0):
        """Samples written over ``secs`` against the rate asked, and the driver's overruns meanwhile. The count moves
        a whole probe vector (NFFT) at a time - the source can run only one vector ahead of the probe - so it is
        judged to within one vector."""
        n0, o0, t0 = self.src.nitems_written(0), overrun_count(), time.time()
        time.sleep(secs)
        n, dt = self.src.nitems_written(0) - n0, time.time() - t0
        ok = abs(n - RATE * dt) <= NFFT * 1.05 and overrun_count() == o0
        return ok, f"{n} samples in {dt:.2f} s against {RATE * dt:.0f} (one vector is {NFFT}), " \
                   f"{overrun_count() - o0} overruns"

    def measure(self, pilot):
        time.sleep(NFFT / RATE + 0.3)                    # a vector filled since the last change
        n2 = self.usrp.recv_num_samps(NFFT, self.centre, RATE, [0], 30.0)[0]
        bb = np.array(self.probe.level(), np.complex64)  # the vector that ended about when the N210's began
        return carrier(bb, self.src.center_freq, pilot), carrier(n2, self.centre, pilot)

    def close(self):
        self.tb.stop()
        self.tb.wait()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--addr', required=True, help="the N210's address (from uhd_find_devices)")
    ap.add_argument('--pilot', type=float, default=602.309628e6, help='an off-air carrier both can see, Hz')
    a = ap.parse_args()
    import uhd
    usrp = uhd.usrp.MultiUSRP(f"addr={a.addr}")
    usrp.set_rx_antenna('RX2', 0)
    centre = a.pilot + 0.7e6                             # the carrier 700 kHz below centre, clear of DC

    print('bb60_source(ref_out=True), the N210 on its external reference')
    usrp.set_clock_source('external')
    p = Pair(usrp, centre, True)
    time.sleep(1.5)
    ok, why = p.flowing()
    check(ok, f"samples flow at the rate asked: {why}")
    check(p.src.ref_out_applied == 1, f"the output applied once at start ({p.src.ref_out_applied})")
    fb, fn = p.measure(a.pilot)
    print(f"       carrier: BB60D {fb/1e6:.6f} MHz, N210 {fn/1e6:.6f} MHz; "
          f"ref_locked reads {usrp.get_mboard_sensor('ref_locked', 0).to_bool()} (printed, not checked)")
    check(abs(fn - fb) < AGREE_HZ, f"one clock: the two agree, N210 - BB60D = {fn - fb:+.2f} Hz")
    p.src.set_center_freq(centre - 0.4e6)                # carrier now 300 kHz below centre on the BB60D
    p.src.set_gain_percent(70.0)
    time.sleep(1.0)
    fb, fn = p.measure(a.pilot)
    check(p.src.ref_out_applied >= 2, f"after a retune and a gain change the output was applied again "
          f"({p.src.ref_out_applied}, vendor status {p.src.ref_out_status})")
    check(abs(fn - fb) < AGREE_HZ, f"and the two still agree: N210 - BB60D = {fn - fb:+.2f} Hz")
    ok, why = p.flowing()
    check(ok, f"samples still flow: {why}")
    p.close()

    print('bb60_source() - ref_out off, the default - and the N210 on its internal reference')
    usrp.set_clock_source('internal')
    p = Pair(usrp, centre, False)
    time.sleep(1.5)
    fb, fn = p.measure(a.pilot)
    check(p.src.ref_out_applied == 0, "nothing applied")
    check(abs(fn - fb) > APART_HZ, f"two clocks: the two differ, N210 - BB60D = {fn - fb:+.2f} Hz "
          f"({(fn - fb) / a.pilot * 1e6:+.3f} ppm)")
    p.close()

    print()
    if failures:
        print(f"{len(failures)} FAILED: {', '.join(failures)}")
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
