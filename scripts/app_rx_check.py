#!/usr/bin/env python3
"""Run one receiver app on a real radio, headless, and print what its window says.

    python scripts/app_rx_check.py rdsReceiver --radio bb60 --fkey frequency_mhz --freq 98.7
    python scripts/app_rx_check.py atscReceiver --radio usrp --addr ADDR \\
        --fkey center_mhz --freq 605 --gain 50 --secs 40

Run from the repo root on the machine the radio is on, under ``timeout -k``;
``scripts/app_pair.sh`` does that over ssh. Written for the first run of
every app on the Ettus N210 (knowledge/ettus.md).

The values come from the app's own ConfigDialog with Settings patched to
``--radio``, then frequency and gain are set explicitly, and sound is turned
off. Every 5 s it prints ``RX t=...`` with each of the window's status labels
(``self.lbl``), the ISM receiver's status line and every row of any table -
lock, level, pilot, MER, frames a second, decodes, whatever that app shows.
On the N210 it first prints ``RX radio`` with the centre, gain, sample rate
and antenna the radio really took, beside the rate the app believes - the
line that caught the ATSC receiver's 12 MS/s coming back as 12.5.

It never closes the window (its ``closeEvent`` saves), and nothing writes
``config/``. Overruns are UHD's ``O`` and ``D`` on stderr.
"""
import argparse
import importlib.util
import json
import os
import sys
import time

ROOT = os.getcwd()
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

REPORT_EVERY_S = 5


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('module')
    p.add_argument('--radio', choices=('bb60', 'usrp', 'hackrf'), required=True)
    p.add_argument('--addr', default='', help="the N210's address (usrp)")
    p.add_argument('--fkey', required=True, help="frequency key in the app's values")
    p.add_argument('--freq', type=float, required=True, help='MHz')
    p.add_argument('--gain', type=float, default=40.0, help='percent')
    p.add_argument('--secs', type=float, default=30.0)
    p.add_argument('--set', action='append', default=[], metavar='KEY=VALUE')
    a = p.parse_args()
    if a.radio == 'usrp' and not a.addr:
        p.error('--addr is needed for the N210')

    from PyQt5 import Qt
    app = Qt.QApplication([])
    import apps.utils as utils
    real = utils.read_settings

    def read_settings():
        s = real()
        s['radio_type'] = a.radio
        if a.radio == 'usrp':
            s['usrp_ip'] = a.addr
        return s
    utils.read_settings = read_settings

    spec = importlib.util.spec_from_file_location(a.module, f'apps/{a.module}.py')
    m = importlib.util.module_from_spec(spec)
    sys.modules[a.module] = m
    spec.loader.exec_module(m)
    d = m.ConfigDialog()
    values = d.get_values()
    d.deleteLater()
    if not isinstance(values.get(a.fkey), (int, float)):
        sys.exit(f"{a.fkey} is not a number in {sorted(values)}")
    values[a.fkey] = a.freq
    values['gain_percent'] = a.gain
    values['radio_type'] = a.radio
    if a.radio == 'usrp':
        values['ipXmitAddr'] = a.addr
    for k in ('sound', 'audio'):
        if k in values:
            values[k] = False
    for kv in a.set:
        k, v = kv.split('=', 1)
        try:
            v = json.loads(v)
        except ValueError:
            pass
        values[k] = v
    print('RX values', json.dumps(values, default=str), flush=True)

    tb = m.main(app=app, config_values=values)
    src = getattr(tb, 'radio_source', None)
    if a.radio == 'usrp' and src is not None:
        print('RX radio', json.dumps(dict(
            freq=src.get_center_freq(0), gain=src.get_gain(0),
            rate=src.get_samp_rate(), ant=src.get_antenna(0),
            app_rate=getattr(tb, 'samp_rate', None))), flush=True)

    def report(t):
        out = [f"{k}={w.text()}" for k, w in (getattr(tb, 'lbl', None) or {}).items()]
        st = getattr(tb, 'status', None)
        if isinstance(st, Qt.QLabel):
            out.append(f"status={st.text()}")
        for tab in tb.findChildren(Qt.QTableWidget):
            for r in range(tab.rowCount()):
                cells = [tab.item(r, c).text() if tab.item(r, c) else ''
                         for c in range(tab.columnCount())]
                out.append('row: ' + ' | '.join(cells))
        print(f"RX t={t:4.0f}s  " + '  ;  '.join(out), flush=True)

    t0 = time.time()
    nxt = REPORT_EVERY_S
    while time.time() - t0 < a.secs:
        app.processEvents()
        time.sleep(0.02)
        if time.time() - t0 >= nxt:
            report(time.time() - t0)
            nxt += REPORT_EVERY_S
    tb.stop()
    tb.wait()
    os._exit(0)


if __name__ == '__main__':
    main()
