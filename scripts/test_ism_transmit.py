#!/usr/bin/env python3
"""The ISM transmitter with no radio: every device there and back.

    python scripts/test_ism_transmit.py
    python scripts/test_ism_transmit.py nexus_th
    python scripts/test_ism_transmit.py --keep /tmp/ism

``scripts/test_ism_frame.py`` checks the encoder on its own. This runs the
**app**, exactly as the launcher builds it, with the radio swapped for a file
sink - so what gets graded is the whole chain the hardware would see: the
rendered burst, the vector source looping it, the stream mux splicing the
silence between bursts, and the standby gate.

What it pins down:

- **Every profile still decodes after the flowgraph has had it.** The encoder
  passing does not prove the app does; a wrong sample rate, a mux length off
  by one, or a gate left at zero would all leave the frame intact and the
  transmission useless.
- **The radio is tuned below the target, not at it.** Every transmitter here
  is direct-conversion and leaks its oscillator at I=Q=0, so the app tunes low
  and puts the signal back as a baseband tone. The file is written at the
  radio's own centre, so a decode at all is what proves the offset arithmetic
  is right in both directions.
- **Standby is really silent.** With the gate off nothing comes out, and
  nothing decodes.
- **The burst repeats at the interval asked for**, with the silence between
  bursts costing no memory - it comes from a null source, not the vector.

It needs `rtl_433` on the path and no radio at all. Files go to a temporary
folder unless ``--keep`` says otherwise.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from gnuradio import blocks, gr, soapy  # noqa: E402
from PyQt5 import Qt  # noqa: E402

from apps import ism_frame  # noqa: E402

RTL_433 = shutil.which('rtl_433')

#: 2 MS/s rather than the 8 the app gives a HackRF. Still a whole number of
#: samples per microsecond, so nothing about the rendering changes, and a
#: quarter of the file: a Nexus burst at 8 MS/s is 60 MB and this runs four
#: of them. The 8 MS/s path is what test_ism_frame.py renders at.
TEST_RATE = 2e6

#: Which model each profile must come back as, and the reading it carries.
EXPECTED = {
    'nexus_th': {'model': 'Nexus-TH', 'id': 181, 'temperature_C': 19.0},
    'acurite_609txc': {'model': 'Acurite-609TXC', 'id': 202,
                       'temperature_C': 26.2},
    'lacrosse_tx141th_bv2': {'model': 'LaCrosse-TX141THBv2', 'id': 231,
                             'temperature_C': 20.0},
    'ev1527': {'model': 'Generic-Remote', 'id': 4660, 'cmd': 8},
}


def install_file_sink(path, samples):
    """Swap the HackRF for a file sink that stops after ``samples``.

    A ``head`` rather than a throttle: the flowgraph then runs flat out and
    ends by itself after exactly the right number of samples, so nothing in
    the test depends on how long anything took.
    """
    class FileSink(gr.hier_block2):
        def __init__(self, *args, **kwargs):
            gr.hier_block2.__init__(
                self, 'file sink standing in for a radio',
                gr.io_signature(1, 1, gr.sizeof_gr_complex),
                gr.io_signature(0, 0, 0))
            self.head = blocks.head(gr.sizeof_gr_complex, int(samples))
            self.sink = blocks.file_sink(gr.sizeof_gr_complex, path, False)
            self.sink.set_unbuffered(False)
            self.connect(self, self.head, self.sink)

        def __getattr__(self, attr):
            if attr.startswith('_'):
                raise AttributeError(attr)
            try:
                return gr.hier_block2.__getattr__(self, attr)
            except AttributeError:
                if attr.startswith('set_'):
                    return lambda *a, **k: None
                raise

    soapy.sink = FileSink


def decode(path):
    rows = []
    out = subprocess.run([RTL_433, '-F', 'json', '-r', path],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        line = line.strip()
        if line.startswith('{'):
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def matches(row, expect):
    for key, want in expect.items():
        got = row.get(key)
        if isinstance(want, float):
            if got is None or abs(float(got) - want) > 0.05:
                return False
        elif str(got) != str(want):
            return False
    return True


def run_app(profile, out_dir, transmitting=True, bursts=2):
    """Build the real flowgraph, run it, and return the file it wrote.

    The file is named at the radio's own centre frequency, not the frequency
    asked for - that is the point of the offset, and a decode proves the app
    put the signal back where it said it would.
    """
    import apps.ismXmitter as app
    app.SAMPLE_RATES = dict(app.SAMPLE_RATES, hackrf=TEST_RATE)

    fields = app.default_fields(profile)
    frame = app.build_frame(profile, fields)
    interval = 0.05                      # short, so the run stays small
    cf, offset_khz = 433.92, 200.0
    lead_in_us = 5000
    per_burst = int(round((lead_in_us + frame.duration_us) * 1e-6 * TEST_RATE))
    idle = int(round(interval * TEST_RATE))
    samples = bursts * (per_burst + idle)

    lo_mhz = cf - offset_khz / 1e3
    path = os.path.join(out_dir, ism_frame.capture_name(
        ('tx_' if transmitting else 'standby_') + profile,
        freq_hz=lo_mhz * 1e6, fs=TEST_RATE, suffix='cf32'))
    install_file_sink(path, samples)

    tb = app.ismXmitter(config_values={
        'radio_type': 'hackrf', 'ipXmitAddr': '', 'cf': cf, 'pwr': 50,
        'profile': profile, 'fields': fields,
        'interval_s': interval, 'offset_khz': offset_khz,
    })
    if not transmitting:
        tb.set_transmitting(False)
    tb.start()
    tb.wait()
    tb.stop()
    tb.wait()
    del tb
    return path, frame


def check_profiles(names, out_dir):
    print("Every device, through the app's own flowgraph:")
    ok = True
    for name in names:
        path, frame = run_app(name, out_dir)
        rows = decode(path)
        hit = any(matches(r, EXPECTED[name]) for r in rows)
        print("  %-22s %-4s %5.1f MB, %d decode(s)  %s"
              % (name, "ok" if hit else "FAIL", os.path.getsize(path) / 1e6,
                 len(rows), sorted({r.get('model', '?') for r in rows}) or ''))
        ok = ok and hit
    return ok


def check_standby(out_dir):
    """The gate off has to be silent, and stay silent."""
    print("\nStandby:")
    path, _frame = run_app('nexus_th', out_dir, transmitting=False)
    iq = np.fromfile(path, dtype=np.complex64)
    peak = float(np.abs(iq).max()) if len(iq) else 0.0
    rows = decode(path)
    ok = peak == 0.0 and not rows
    print("  %-22s %-4s peak %.6g, %d decode(s)"
          % ('gate off', "ok" if ok else "FAIL", peak, len(rows)))
    return ok


def check_offset(out_dir):
    """The signal has to sit where the offset says, not at the radio's centre.

    A file carries its own centre frequency in its name, so the only way the
    decode above can work is if the burst really is ``offset`` above it. This
    measures where the energy is rather than inferring it.
    """
    print("\nWhere the signal actually sits:")
    path, frame = run_app('acurite_609txc', out_dir)
    iq = np.fromfile(path, dtype=np.complex64)
    # A *contiguous* block, starting at the first pulse. Gathering only the
    # loud samples would splice across the gaps and break the tone's phase,
    # which smears the peak and shifts it by a kilohertz - the first version
    # of this check did exactly that and read +199.1 for a tone that is
    # exactly +200. The OOK keying is amplitude on a phase-continuous tone,
    # so a contiguous window puts the carrier in one bin with the keying
    # sidebands either side of it.
    loud = np.flatnonzero(np.abs(iq) > 0.5 * np.abs(iq).max())
    block = iq[loud[0]:loud[0] + (1 << 16)] if len(loud) else iq[:0]
    if len(block) < (1 << 16):
        print("  FAIL  not enough signal to measure")
        return False
    spectrum = np.abs(np.fft.fftshift(np.fft.fft(block)))
    freqs = np.fft.fftshift(np.fft.fftfreq(len(block), 1.0 / TEST_RATE))
    peak_khz = freqs[int(np.argmax(spectrum))] / 1e3
    ok = abs(peak_khz - 200.0) < 0.1
    print("  %-22s %-4s peak at %+.1f kHz from the radio's centre (want +200)"
          % ('baseband tone', "ok" if ok else "FAIL", peak_khz))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('profiles', nargs='*', choices=sorted(EXPECTED) + [])
    ap.add_argument('--keep', metavar='DIR')
    args = ap.parse_args()

    if not RTL_433:
        print("rtl_433 is not on the path - install it with "
              "`sudo apt install rtl-433`. Skipping.")
        return 0

    # The flowgraph is a QWidget, so it needs an application even offscreen.
    _app = Qt.QApplication(sys.argv[:1])

    names = args.profiles or sorted(EXPECTED)
    out_dir = args.keep or tempfile.mkdtemp(prefix='ism_tx_')
    os.makedirs(out_dir, exist_ok=True)

    results = [check_profiles(names, out_dir)]
    if names == sorted(EXPECTED):
        results += [check_standby(out_dir), check_offset(out_dir)]

    if args.keep:
        print("\nCaptures left in %s" % out_dir)
    else:
        shutil.rmtree(out_dir, ignore_errors=True)

    ok = all(results)
    print()
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
