#!/usr/bin/env python3
"""Hold ``apps/bb60_source``'s count of the driver's "Sample Overrun" warnings to the driver's own text, with no radio.

    python -B scripts/test_bb60_overrun.py [REC_LOG ...]

* Through the real stderr filter (``capture_driver_output``: fd 2 taken, a pump thread): lines written to fd 2 the way
  the SoapySDR module writes them - its ANSI colours and ``[WARNING]`` included - are counted once each and still
  reach the real stderr; ``release_driver_output`` drains them all before it returns, so a count read after the
  flowgraph's ``stop`` is complete; ``reset_overflows`` clears it; an ADC overflow message counts there and not here.
* Through ``bt_ota_hop_check.record`` with a fake device in the source (GNU Radio's own scheduler, no radio): warnings
  the driver prints while streaming are in the returned ``sample_overruns``, read after ``tb.run()``, and not in
  ``lost_sample_overflows`` (readStream's count); ``overruns_counted`` is true.
* Each ``REC_LOG`` given (a bench recorder's ``NAME.rec.log``) fed through the filter's line handler counts exactly the
  "Sample Overrun" lines it holds.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bb60_source as bb  # noqa: E402

failures = []
LINE = b'\x1b[1m\x1b[33m[WARNING] Sample Overrun\x1b[0m\n'


def check(ok, what):
    print('  %s %s' % ('ok  ' if ok else 'FAIL', what), flush=True)
    if not ok:
        failures.append(what)


def main(argv):
    print('the stderr filter')
    real = tempfile.TemporaryFile()
    saved = os.dup(2)
    os.dup2(real.fileno(), 2)                  # what the filter passes on lands here
    try:
        bb.reset_overflows()
        bb.capture_driver_output()
        for _ in range(500):
            os.write(2, LINE)
        os.write(2, b'[ERROR] ADC overflow\n')
        os.write(2, b'[INFO] something else\n')
        bb.release_driver_output()
        n, adc = bb.overrun_count(), bb.overflow_count()
    finally:
        os.dup2(saved, 2)
        os.close(saved)
    real.seek(0)
    passed = real.read()
    check(n == 500, '500 warnings written to fd 2, %d counted after release' % n)
    check(adc == 1, 'the ADC overflow counted as an overflow, not an overrun (%d)' % adc)
    check(passed.count(b'Sample Overrun') == 500 and b'something else' in passed,
          'the warnings and other lines still reach the real stderr')
    bb.reset_overflows()
    check(bb.overrun_count() == 0, 'reset_overflows clears it')
    src = bb.bb60_source(2439e6, 40e6)
    check(src.overruns_counted is False, 'a source not yet opened has counted nothing')

    print('\nbt_ota_hop_check.record with a fake device')
    from scripts import bt_ota_hop_check as hc

    class Status:
        ret = 0

    class FakeSdr:
        reads = 0

        def readStream(self, stream, bufs, n, timeoutUs=0):
            FakeSdr.reads += 1
            if FakeSdr.reads % 10 == 0:
                os.write(2, LINE)              # the driver prints, and hands back the samples it still has
            bufs[0][:n] = 0
            st = Status()
            st.ret = n
            return st

        def deactivateStream(self, s):
            pass

        def closeStream(self, s):
            pass

    def fake_open(self):
        self._capturing = bb.capture_driver_output()
        self.overruns_counted = self._capturing
        bb.reset_overflows()
        self._sdr, self._stream = FakeSdr(), object()
    real_open = bb.bb60_source._open
    bb.bb60_source._open = fake_open
    out = tempfile.NamedTemporaryFile(suffix='.cf32', delete=False)
    saved = os.dup(2)
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 2)
    try:
        info = hc.record(out.name, 0.05, 2439e6, 40e6, 60.0)
    finally:
        os.dup2(saved, 2)
        os.close(saved)
        bb.bb60_source._open = real_open
        os.unlink(out.name)
    want = FakeSdr.reads // 10
    check(info['sample_overruns'] == want and want > 0 and info['lost_sample_overflows'] == 0
          and info['overruns_counted'] is True and info['readstream_overflows'] == 0,
          'record: %d warnings printed while streaming, %s returned' % (want, {k: info[k] for k in (
              'sample_overruns', 'lost_sample_overflows', 'readstream_overflows', 'overruns_counted')}))

    if argv:
        print('\nrecorder logs')
    sink = os.open(os.devnull, os.O_WRONLY)

    class Out:
        _saved = sink
    for path in argv:
        raw = open(path, 'rb').read()
        bb.reset_overflows()
        for line in raw.split(b'\n'):
            bb._DriverOutput._line(Out, line)
        check(bb.overrun_count() == raw.count(b'Sample Overrun'),
              '%s: %d counted, %d in the file' % (path, bb.overrun_count(), raw.count(b'Sample Overrun')))
    print('RESULT: PASS' if not failures else 'RESULT: FAIL (%d)' % len(failures))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
