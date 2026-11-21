#!/usr/bin/env python3
"""Run one transmitter app on a real radio and check what the radio really did.

    python scripts/app_tx_check.py amSineGenerator --radio usrp --addr ADDR \\
        --fkey cf --freq 915 --pkey pwr --power 10 --secs 40
    python scripts/app_tx_check.py ismXmitter --radio vsg --fkey cf --freq 433.92 \\
        --pkey pwr --level-dbm -30 --secs 40 --set interval_s=5

Run from the repo root, on the machine the radio is on (the N210's Ethernet
host, or the VSG60's USB host), under ``timeout -k``. ``scripts/app_pair.sh``
does that over ssh. Written for the first run of every app on the Ettus N210
(knowledge/ettus.md).

What it does, in order:

1. **Builds the values from the app's own ConfigDialog**, with Settings
   patched to ``--radio`` (and the N210's ``--addr``), as the launcher would.
   The dialog reads the app's saved config for its defaults - which held
   50-87 % power on 300 and 533 MHz the first time - and nothing is written.
2. **Sets frequency and power explicitly**, under the keys given: they differ
   per app (``cf``/``frequency_mhz``, ``pwr``/``rfPwr``/``power_percent``) and
   are not ``SAVED_SETTINGS``' keys. If either key is not a number in the
   values, it stops before ``main()`` - nothing is transmitted.
   ``--power`` above ``--max-power`` (25 %) is refused.
3. **Starts the app** (``main()``) and at once reads the radio back - centre,
   gain or level, sample rate, antenna. A centre more than 7 MHz from the one
   asked (the NTSC app tunes 6 MHz high on purpose) or a gain above what the
   power asks for **stops it within a second**.
4. **Reports, as ``CHECK`` lines**: the radio's settings beside the app's; a
   sample rate the radio did not give (``PROBLEM`` - an N210 makes only 100 MHz
   over a whole number, so 12 MS/s comes back as 12.5); a centre off the one
   asked (``NOTE`` - several apps tune off on purpose to keep their LO leak
   clear); an antenna other than TX/RX.
5. **Moves the window's power and frequency callbacks** half-way through,
   reads the radio back, and puts them back (``--no-callbacks`` to skip).
6. **Stops the flowgraph without closing the window** - an app's
   ``closeEvent`` saves - and prints ``CHECK RESULT <app> <problems>``.

Underruns are UHD's ``U`` on stderr; they can share a line with a ``CHECK``
line, so count them with ``sed 's/CHECK.*//'`` first. Exit codes: 0 ran,
2 a key was missing, 3 the radio could not be read, 4 wrong centre, 5 power.
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

#: A centre further than this from the one asked stops the run. The NTSC
#: transmitter tunes 6 MHz above its channel on purpose.
MAX_LO_OFFSET_HZ = 7e6


def say(*a):
    print('CHECK', *a, flush=True)


def parse():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('module')
    p.add_argument('--radio', choices=('usrp', 'vsg'), required=True)
    p.add_argument('--addr', default='', help="the N210's address (usrp)")
    p.add_argument('--fkey', required=True, help="frequency key in the app's values")
    p.add_argument('--freq', type=float, required=True, help='MHz')
    p.add_argument('--pkey', required=True, help="power key in the app's values")
    p.add_argument('--power', type=float, help='percent of the radio range')
    p.add_argument('--level-dbm', type=float,
                   help='VSG60 only: the level, turned into the percent the app takes')
    p.add_argument('--max-power', type=float, default=25.0)
    p.add_argument('--secs', type=float, default=40.0)
    p.add_argument('--set', action='append', default=[], metavar='KEY=VALUE',
                   help='any other value for the app (JSON where it parses)')
    p.add_argument('--no-callbacks', action='store_true')
    a = p.parse_args()
    if a.radio == 'usrp' and not a.addr:
        p.error('--addr is needed for the N210')
    if a.level_dbm is not None:
        if a.radio != 'vsg':
            p.error('--level-dbm is for the VSG60')
        lo, hi = -120.0, 10.0
        a.power = (a.level_dbm - lo) / (hi - lo) * 100
    elif a.power is None:
        p.error('give --power (or --level-dbm on the VSG60)')
    if a.radio == 'usrp' and a.power > a.max_power:
        p.error(f'--power {a.power} is over --max-power {a.max_power}')
    return a


def main():
    a = parse()
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
    module = importlib.util.module_from_spec(spec)
    sys.modules[a.module] = module
    spec.loader.exec_module(module)

    dialog = module.ConfigDialog()
    values = dialog.get_values()
    dialog.deleteLater()
    for k in (a.fkey, a.pkey):
        if not isinstance(values.get(k), (int, float)):
            say('ABORT key', k, 'not a number in values:', sorted(values))
            sys.exit(2)
    # Hz or MHz, as the dialog had it.
    values[a.fkey] = a.freq * 1e6 if values[a.fkey] > 1e5 else a.freq
    values[a.pkey] = a.power
    for kv in a.set:
        k, v = kv.split('=', 1)
        try:
            v = json.loads(v)
        except ValueError:
            pass
        values[k] = v
    values['radio_type'] = a.radio
    values['ipXmitAddr'] = a.addr if a.radio == 'usrp' else ''
    say('values', json.dumps({k: v for k, v in values.items() if k != 'audio_list'},
                             default=str)[:800])

    cls = next(v for v in vars(module).values()
               if isinstance(v, type) and getattr(v, 'SAVED_SETTINGS', None)
               and v.__module__ == a.module)
    saved = cls.SAVED_SETTINGS
    pattr = saved[next(k for k in saved if 'power' in k)]
    fattr = saved[next(k for k in saved if 'freq' in k
                       or ('mhz' in k and 'deviation' not in k))]

    tb = module.main(app=app, config_values=values)
    sink = getattr(tb, 'radio_sink', None)

    def radio():
        if a.radio == 'usrp':
            return dict(freq=sink.get_center_freq(0), gain=sink.get_gain(0),
                        rate=sink.get_samp_rate(), ant=sink.get_antenna(0))
        return dict(freq=sink._center_freq, level_dbm=sink._level_dbm,
                    rate=sink._sample_rate, ant='-')

    def stop(code):
        tb.stop()
        tb.wait()
        os._exit(code)

    try:
        r = radio()
    except Exception as e:
        say('ABORT cannot read the radio:', e)
        stop(3)
    say('radio', json.dumps(r), 'app', fattr, getattr(tb, fattr, None),
        pattr, getattr(tb, pattr, None), 'samp_rate', getattr(tb, 'samp_rate', None))
    asked = a.freq * 1e6
    if abs(r['freq'] - asked) > MAX_LO_OFFSET_HZ:
        say('ABORT radio tuned to', r['freq'], 'not near', asked)
        stop(4)
    if a.radio == 'usrp':
        top = utils.scale_power(a.power, tb._power_range) + 0.6
        if r['gain'] > top:
            say('ABORT radio gain', r['gain'], 'above', top)
            stop(5)
    elif r['level_dbm'] > utils.scale_power(a.power, (-120.0, 10.0)) + 0.6:
        say('ABORT radio level', r['level_dbm'], 'dBm above what was asked')
        stop(5)

    problems = []
    want_rate = app_rate = getattr(tb, 'samp_rate', None)
    if callable(getattr(tb, 'radio_rate', None)):
        # An app whose radio runs at a multiple of samp_rate says so.
        say(f"NOTE the app asks the radio for {tb.radio_rate():g} "
            f"(samp_rate {want_rate:g})")
        want_rate = tb.radio_rate()
    if want_rate and abs(r['rate'] - want_rate) > 1:
        if abs(r['rate'] - 2 * want_rate) < 1:
            say(f"NOTE radio runs at twice samp_rate ({r['rate']:g}), as the app asks")
        else:
            problems.append(f"sample rate asked {want_rate:g}, radio gives {r['rate']:g}")
    if abs(r['freq'] - asked) > 1:
        say(f"NOTE centre {r['freq']:.0f} Hz, {(r['freq'] - asked) / 1e3:+.1f} kHz "
            f"from the {asked:.0f} asked (the app tunes off on purpose?)")
    if a.radio == 'usrp' and r['ant'] != 'TX/RX':
        problems.append(f"antenna {r['ant']}")

    def pump(s):
        end = time.time() + s
        while time.time() < end:
            app.processEvents()
            time.sleep(0.02)

    pump(a.secs / 2)
    if not a.no_callbacks:
        try:
            cur = getattr(tb, fattr)
            step = 0.1e6 if cur > 1e5 else 0.1
            setp = getattr(tb, 'set_' + pattr, None) or getattr(tb, 'set_power')
            setf = getattr(tb, 'set_' + fattr, None) or getattr(tb, 'set_frequency')
            say('callbacks', setp.__name__, setf.__name__)
            setp(a.power + 5)
            setf(cur + step)
            r2 = radio()
            say('after set ->', json.dumps(r2))
            if abs(r2['freq'] - (r['freq'] + 0.1e6)) > 1:
                problems.append(f"{setf.__name__} moved the radio to {r2['freq']:.0f}, "
                                f"expected {r['freq'] + 0.1e6:.0f}")
            key = 'gain' if a.radio == 'usrp' else 'level_dbm'
            if r2[key] <= r[key] and a.power + 5 <= 100:
                problems.append(f"{setp.__name__} {a.power + 5}% left {key} at {r2[key]}")
            setp(a.power)
            setf(cur)
            # The app's own rate, not the radio's: set_samp_rate takes what
            # samp_rate holds, and an app whose radio runs at a multiple of
            # it (radio_rate) would otherwise ask the radio for that multiple
            # again - 62.5 MS/s, once, and a stream of underruns.
            if hasattr(tb, 'set_samp_rate') and app_rate:
                tb.set_samp_rate(app_rate)
            say('restored ->', json.dumps(radio()))
        except Exception as e:
            import traceback
            traceback.print_exc()
            problems.append(f"callback raised {type(e).__name__}: {e}")
    pump(a.secs / 2)
    for p in problems:
        say('PROBLEM', p)
    say('RESULT', a.module, len(problems))
    stop(0)


if __name__ == '__main__':
    main()
