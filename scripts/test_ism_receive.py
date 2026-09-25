#!/usr/bin/env python3
"""The ISM receiver with no radio: every device in, the right reading out.

    python scripts/test_ism_receive.py
    python scripts/test_ism_receive.py nexus_th

This runs the **app**, exactly as the launcher builds it, with the radio
swapped for a source of synthetic samples at the radio's rate - so what gets
graded is the whole chain: the channel shift and filter, the decimation to
rtl_433's rate, the pipe, rtl_433 reading it, the thread reading rtl_433, and
the table the window shows.

What it pins down:

- **Every profile the transmitter sends comes out of the table** as the right
  device with the right reading.
- **Both carriers that never turn off are kept out of the way.** The samples
  carry the receiver's own DC spike at its centre and the ISM Transmitter's
  leaked oscillator where it would really land - 100 kHz below the receiver's
  centre, since the transmitter tunes 400 kHz low and this 300 - each louder
  than the signal. Either inside rtl_433's channel would sit under every
  pulse and stop the envelope reaching zero.
- **Copies of one frame are one row.** A sensor sends its frame several times
  and some decoders report each; the table counts them instead.
- **Noise alone decodes as nothing.**

It needs `rtl_433` on the path and no radio at all.
"""
import argparse
import os
import shutil
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from gnuradio import blocks, gr, soapy  # noqa: E402
from PyQt5 import Qt  # noqa: E402

from apps import ism_frame  # noqa: E402

RTL_433 = shutil.which('rtl_433')
RATE = 2e6
NOISE = 0.005                  # per rail; the signal is ism_frame's 0.4
#: The two carriers, each louder than the signal is: a HackRF's DC spike
#: and the transmitter's leak through a cable are both easily that.
DC_SPIKE = 0.6
TX_LEAK = 0.6
#: What a clean decode measures, well clear of what a carrier inside the
#: channel leaves. A leak 20 kHz from the signal decodes as nothing at all; one
#: exactly on it still decodes, but at 4 dB rather than the 46 this gets - so a
#: decode alone does not prove the carriers were kept out, and this does.
MIN_SNR_DB = 30.0

EXPECTED = {
    'nexus_th': {'model': 'Nexus-TH', 'id': '181', 'reading': '19.0 °C'},
    'acurite_609txc': {'model': 'Acurite-609TXC', 'id': '202',
                       'reading': '26.2 °C'},
    'lacrosse_tx141th_bv2': {'model': 'LaCrosse-TX141THBv2', 'id': '231',
                             'reading': '20.0 °C'},
    'ev1527': {'model': 'Generic-Remote', 'id': '4660', 'reading': 'cmd 8'},
}


def samples(profile, bursts=2):
    """What a HackRF tuned for the receiver would hand it, ``bursts`` times.

    ``profile`` None is noise and the two carriers alone.
    """
    from apps.ismReceiver import LO_OFFSET
    from apps.ismXmitter import DEFAULT_OFFSET_KHZ, build_frame, default_fields
    gap = np.zeros(int(0.3 * RATE), np.complex64)
    parts = [gap]
    for _ in range(bursts if profile else 1):
        if profile:
            frame = build_frame(profile, default_fields(profile))
            parts.append(ism_frame.render(frame, fs=RATE,
                                          offset_hz=LO_OFFSET)
                         .astype(np.complex64))
        parts.append(gap)
    # Enough silence after the last burst for rtl_433 to see the package
    # end, and to fill the last of the buffers it reads its stdin in.
    parts.append(np.zeros(int(1.0 * RATE), np.complex64))
    iq = np.concatenate(parts)
    n = np.arange(len(iq))
    leak_hz = LO_OFFSET - DEFAULT_OFFSET_KHZ * 1e3
    rng = np.random.default_rng(433)
    iq = (iq + DC_SPIKE
          + TX_LEAK * np.exp(2j * np.pi * leak_hz / RATE * n)
          + NOISE * (rng.standard_normal(len(iq))
                     + 1j * rng.standard_normal(len(iq))))
    return iq.astype(np.complex64)


def install_source(iq):
    class Source(gr.hier_block2):
        def __init__(self, *args, **kwargs):
            gr.hier_block2.__init__(
                self, 'recorded samples standing in for a radio',
                gr.io_signature(0, 0, 0),
                gr.io_signature(1, 1, gr.sizeof_gr_complex))
            self.src = blocks.vector_source_c(iq, False)
            self.connect(self.src, self)

        def __getattr__(self, attr):
            if attr.startswith('_'):
                raise AttributeError(attr)
            try:
                return gr.hier_block2.__getattr__(self, attr)
            except AttributeError:
                if attr.startswith('set_'):
                    return lambda *a, **k: None
                raise

    soapy.source = Source


def run(app, profile):
    """Build the app on these samples, run them through, and return the
    table's rows once rtl_433 has had its say."""
    import apps.ismReceiver as rx
    install_source(samples(profile))
    tb = rx.ismReceiver(config_values={
        'radio_type': 'hackrf', 'frequency_mhz': 433.92, 'gain_percent': 30})
    tb.start()
    tb.wait()                          # the samples end, and so does this
    end = time.time() + 8
    want = EXPECTED.get(profile)
    rows = []
    while time.time() < end:
        app.processEvents()
        tb.refresh()
        rows = [[tb.table.item(r, c).text()
                 for c in range(len(tb.COLUMNS))]
                for r in range(tb.table.rowCount())]
        if want and any(r[1] == want['model'] for r in rows):
            time.sleep(0.5)            # any copies still in the pipe
            tb.refresh()
            rows = [[tb.table.item(r, c).text()
                     for c in range(len(tb.COLUMNS))]
                    for r in range(tb.table.rowCount())]
            break
        time.sleep(0.1)
    decodes = tb.decodes
    tb.stop()
    tb.wait()
    tb.decoder.close()
    return rows, decodes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('profiles', nargs='*', choices=sorted(EXPECTED) + [])
    args = ap.parse_args()
    if not RTL_433:
        print("rtl_433 is not on the path - install it with "
              "`sudo apt install rtl-433`. Skipping.")
        return 0

    app = Qt.QApplication(sys.argv[:1])
    col = {name: i for i, name in enumerate(
        __import__('apps.ismReceiver', fromlist=['x']).ismReceiver.COLUMNS)}
    ok = True

    print("Every device, through the app, past its DC spike and the "
          "transmitter's leak:")
    names = args.profiles or sorted(EXPECTED)
    for name in names:
        want = EXPECTED[name]
        rows, decodes = run(app, name)
        hits = [r for r in rows if r[col['Device']] == want['model']
                and r[col['ID']] == want['id']
                and want['reading'] in r[col['Reading']]]
        good = bool(hits) and all(
            r[col['SNR']] and float(r[col['SNR']].split()[0]) >= MIN_SNR_DB
            for r in hits)
        print("  %-22s %-4s %d row(s) from %d decode(s): %s"
              % (name, "ok" if good else "FAIL", len(rows), decodes,
                 '; '.join('%s %s [%s] %s x%s' % (
                     r[col['Device']], r[col['ID']], r[col['Reading']],
                     r[col['SNR']], r[col['Copies']]) for r in rows) or '-'))
        ok = ok and good
        # Two bursts, 0.3 s apart: each is one transmission, so each is at
        # most one row however many copies rtl_433 reported.
        if good and len(hits) > 2:
            print("  %-22s FAIL  copies of one frame were not collapsed"
                  % '')
            ok = False

    if not args.profiles:
        print("\nNoise and the two carriers alone:")
        rows, decodes = run(app, None)
        quiet = not rows and decodes == 0
        print("  %-22s %-4s %d decode(s)"
              % ('nothing sent', "ok" if quiet else "FAIL", decodes))
        ok = ok and quiet

    print()
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
