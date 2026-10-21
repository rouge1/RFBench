#!/usr/bin/env python3
"""Hold ``scripts/vsg_play_loop.py`` to its limits, with no radio.

    python scripts/test_vsg_play_loop.py

* **Refusals touch no radio** (an import of the real ``apps.vsg_sink`` fails the test): neither --go nor --check, --go
  and --check together, --go without --pad-db or --seconds, more than -20 dBm into the BB60D, a level over 0 or nan, a
  rate or centre outside the VSG60A's, a loop over 10.24 s, a file of odd bytes, all zeros, a nan sample, a peak over 1.
* **--check** writes ``<name>_check.json`` with the file's sha256, peak and mean power, and opens nothing;
  ``--normalize`` scales the peak to 1.
* **--go**, with a fake sink: one open at the centre, rate and level given, the exact samples repeated (never sent
  once), the sidecar "not started" at the open and "transmitting" once the repeat is on, then "stopped"; the repeat
  stopped and the sink closed every time - at the end of --seconds, on a SIGTERM (``interrupted_by``), when the
  waveform stops on its own, and when the repeat raises ("error"). An existing sidecar is never overwritten.
"""
import contextlib
import io
import json
import math
import os
import shutil
import signal
import sys
import tempfile
import types

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import vsg_play_loop as loop  # noqa: E402

failures = []
RATE = 15.36e6


def check(ok, what):
    print('  %s %s' % ('ok  ' if ok else 'FAIL', what), flush=True)
    if not ok:
        failures.append(what)


def run(fn, argv):
    """``fn(argv)``, returning ``(code, stdout, stderr)``; a SystemExit is a code."""
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = fn(argv) or 0
        except SystemExit as e:
            code = e.code if e.code is not None else 0
    return code, out.getvalue(), err.getvalue()


class FakeSink:
    log = []
    side_path = None
    raise_on_repeat = False
    signal_on_repeat = False
    active_polls = None            # None: always active; n: inactive after n polls

    def __init__(self, center_freq=None, sample_rate=None, level_dbm=None):
        FakeSink.log.append(('open', center_freq, sample_rate, level_dbm, FakeSink.read()))

    @staticmethod
    def read():
        try:
            return json.load(open(FakeSink.side_path))
        except (OSError, ValueError):
            return None

    def get_serial(self):
        return 12345

    def send_waveform(self, iq):
        FakeSink.log.append(('send',))

    def repeat_waveform(self, iq):
        FakeSink.log.append(('repeat', iq.copy(), FakeSink.read()))
        if FakeSink.raise_on_repeat:
            raise RuntimeError('USB gone')
        if FakeSink.signal_on_repeat:
            os.kill(os.getpid(), signal.SIGTERM)

    def waveform_active(self):
        if FakeSink.active_polls is None:
            return True
        FakeSink.active_polls -= 1
        return FakeSink.active_polls > 0

    def stop_waveform(self):
        FakeSink.log.append(('stop_waveform',))

    def stop(self):
        FakeSink.log.append(('stop',))


class Guard:
    """Refuses, and records, an import of the real ``apps.vsg_sink``."""

    def __init__(self):
        self.imported = False

    def find_spec(self, fullname, path, target=None):
        if fullname == 'apps.vsg_sink':
            self.imported = True
            raise ImportError('test refused the real vsg_sink')
        return None


def lte_like(n, seed=1):
    """Noise-like samples with a peak of 0.8: an OFDM file's look, under 1."""
    rng = np.random.default_rng(seed)
    iq = (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64)
    return (iq * (0.8 / np.abs(iq).max())).astype(np.complex64)


def test_refusals(tmp, path):
    print('\nRefusals, with no radio')
    guard = Guard()
    sys.modules.pop('apps.vsg_sink', None)
    sys.meta_path.insert(0, guard)
    base = [path, '--rate', '15.36e6', '--center-mhz', '751', '--out', tmp]
    go = ['--go', '--pad-db', '0', '--seconds', '1']
    try:
        long_path = os.path.join(tmp, 'long.cf32')
        with open(long_path, 'wb') as f:
            f.truncate(8 * int(10.3 * 1e6))
        odd = os.path.join(tmp, 'odd.cf32')
        with open(odd, 'wb') as f:
            f.write(b'\0' * 12)
        zeros = os.path.join(tmp, 'zeros.cf32')
        np.zeros(1000, np.complex64).tofile(zeros)
        nan = os.path.join(tmp, 'nan.cf32')
        x = lte_like(1000)
        x[10] = np.nan
        x.tofile(nan)
        loud = os.path.join(tmp, 'loud.cf32')
        (lte_like(1000) * 2).astype(np.complex64).tofile(loud)
        for argv, what, code_want in (
                (base, 'neither --go nor --check sends nothing', 0),
                (base + ['--go', '--check', '--pad-db', '0', '--seconds', '1'], '--go and --check together', 2),
                (base + ['--go', '--seconds', '1'], '--go without --pad-db', 2),
                (base + ['--go', '--pad-db', '0'], '--go without --seconds', 2),
                (base + go[:3] + ['--seconds', '601'], '--seconds over 600', 2),
                (base + go + ['--level', '-19'], '-19 dBm with no pad (-19 at the BB60D)', 2),
                (base + ['--go', '--pad-db', '10', '--seconds', '1', '--level', '-5'], '-5 dBm through 10 dB', 2),
                (base + go + ['--level', '1'], 'a level of +1 dBm', 2),
                (base + go + ['--level', 'nan'], 'a nan level', 2),
                ([path, '--rate', '60e6', '--center-mhz', '751', '--check'], 'a rate of 60 MS/s', 2),
                ([path, '--rate', '15.36e6', '--center-mhz', '6100', '--check'], 'a centre of 6100 MHz', 2),
                ([path, '--rate', '15.36e6', '--center-mhz', '10', '--check'], 'a centre of 10 MHz', 2),
                ([long_path, '--rate', '1e6', '--center-mhz', '751', '--check'], 'a loop of 10.3 s', 2),
                ([odd, '--rate', '1e6', '--center-mhz', '751', '--check'], 'a file of 12 bytes', 2),
                ([zeros, '--rate', '1e6', '--center-mhz', '751', '--check'], 'a file of zeros', 2),
                ([nan, '--rate', '1e6', '--center-mhz', '751', '--check'], 'a nan sample', 2),
                ([loud, '--rate', '1e6', '--center-mhz', '751', '--check'], 'a peak of 1.6', 2)):
            code, text, err = run(loop.main, argv)
            check(code == code_want and not guard.imported, '%s: refused before any radio (%s)' % (
                what, (err.strip().split('\n')[-1] or text.strip())[:90]))

        code, text, _ = run(loop.main, base + ['--check', '--level', '-40'])
        side = json.load(open(os.path.join(tmp, 'lte_check.json')))
        iq = np.fromfile(path, dtype='<c8')
        mean = -40 + 10 * math.log10(float(np.mean(np.abs(iq) ** 2)))
        check(code == 0 and side['state'] == 'check: never sent' and side['vsg_center_mhz'] == 751.0
              and abs(side['file_peak'] - 0.8) < 1e-6 and abs(side['mean_power_dbm'] - mean) < 1e-6
              and side['level_at_receiver_dbm'] is None and not guard.imported,
              '--check: the file\'s peak 0.8, mean %.1f dBm, no radio' % mean)
        code, text, _ = run(loop.main, [loud, '--rate', '1e6', '--center-mhz', '751', '--check', '--normalize',
                                        '--out', tmp])
        side = json.load(open(os.path.join(tmp, 'loud_check.json')))
        check(code == 0 and abs(side['scale'] * side['file_peak'] - 1) < 1e-6 and 'scaled to 1' in text,
              '--normalize: a peak of %.2f scaled to 1' % side['file_peak'])
    finally:
        sys.meta_path.remove(guard)


def test_go(tmp, path):
    print('\n--go, with a fake sink')
    fake = types.ModuleType('apps.vsg_sink')
    fake.vsg_sink = FakeSink
    sys.modules['apps.vsg_sink'] = fake
    import apps
    old = getattr(apps, 'vsg_sink', None)
    apps.vsg_sink = fake
    iq = np.fromfile(path, dtype='<c8')
    base = [path, '--rate', '15.36e6', '--center-mhz', '751', '--level', '-40', '--pad-db', '0', '--go', '--out', tmp]
    old_handlers = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)}
    try:
        FakeSink.log = []
        FakeSink.side_path = os.path.join(tmp, 'go1_tx.json')
        code, text, _ = run(loop.main, base + ['--seconds', '0.5', '--name', 'go1'])
        side = json.load(open(FakeSink.side_path))
        opens = [e for e in FakeSink.log if e[0] == 'open']
        reps = [e for e in FakeSink.log if e[0] == 'repeat']
        check(code == 0 and len(opens) == 1 and abs(opens[0][1] - 751e6) < 1 and opens[0][2] == RATE
              and opens[0][3] == -40 and opens[0][4]['state'] == 'not started',
              'one open at 751 MHz, 15.36 MS/s, -40 dBm, the sidecar "not started" before it')
        check(len(reps) == 1 and np.array_equal(reps[0][1], iq) and ('send',) not in FakeSink.log,
              'the exact samples repeated once, never sent as a one-shot')
        check(side['state'] == 'stopped' and side['tx_stop_unix'] - side['tx_start_unix'] >= 0.5
              and side['vsg_serial'] == 12345 and FakeSink.log[-2:] == [('stop_waveform',), ('stop',)]
              and 'timeout -k 16' in text and 'transmitting' in text,
              'after --seconds: "stopped", the repeat stopped and the sink closed, timeout -k 16 printed')
        code, _, err = run(loop.main, base + ['--seconds', '0.5', '--name', 'go1'])
        check(code == 2 and 'already exists' in err, 'an existing sidecar is never overwritten')

        FakeSink.log = []
        FakeSink.signal_on_repeat = True
        FakeSink.side_path = os.path.join(tmp, 'go2_tx.json')
        code, text, _ = run(loop.main, base + ['--seconds', '30', '--name', 'go2'])
        side = json.load(open(FakeSink.side_path))
        check(code == 0 and side.get('interrupted_by') == 'SIGTERM' and side['state'] == 'stopped'
              and side['tx_stop_unix'] - side['tx_start_unix'] < 2
              and FakeSink.log[-2:] == [('stop_waveform',), ('stop',)],
              'a SIGTERM during a 30 s run: stopped at once, interrupted_by SIGTERM, the sink closed')
        FakeSink.signal_on_repeat = False

        FakeSink.log = []
        FakeSink.active_polls = 2
        FakeSink.side_path = os.path.join(tmp, 'go3_tx.json')
        code, _, _ = run(loop.main, base + ['--seconds', '30', '--name', 'go3'])
        side = json.load(open(FakeSink.side_path))
        check(code == 0 and side.get('stopped_on_its_own') and side['state'] == 'stopped'
              and FakeSink.log[-2:] == [('stop_waveform',), ('stop',)],
              'a waveform that stops on its own: noted, the sink closed')
        FakeSink.active_polls = None

        FakeSink.log = []
        FakeSink.raise_on_repeat = True
        FakeSink.side_path = os.path.join(tmp, 'go4_tx.json')
        try:
            run(loop.main, base + ['--seconds', '1', '--name', 'go4'])
            raised = False
        except RuntimeError:
            raised = True
        side = json.load(open(FakeSink.side_path))
        check(raised and side['state'] == 'error' and 'USB gone' in side['error'] and 'tx_start_unix' not in side
              and FakeSink.log[-2:] == [('stop_waveform',), ('stop',)],
              'a repeat that raises: "error", the error kept, the sink closed, the error raised')
    finally:
        FakeSink.raise_on_repeat = FakeSink.signal_on_repeat = False
        FakeSink.active_polls = None
        for s, h in old_handlers.items():
            signal.signal(s, h)
        sys.modules.pop('apps.vsg_sink', None)
        if old is None:
            delattr(apps, 'vsg_sink')
        else:
            apps.vsg_sink = old


def main():
    tmp = tempfile.mkdtemp(prefix='test_vsg_play_loop_')
    try:
        path = os.path.join(tmp, 'lte.cf32')
        lte_like(int(0.05 * RATE)).tofile(path)
        test_refusals(tmp, path)
        test_go(tmp, path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for w in failures:
        print('FAILED:', w)
    print('RESULT: PASS' if not failures else 'RESULT: FAIL (%d)' % len(failures))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
