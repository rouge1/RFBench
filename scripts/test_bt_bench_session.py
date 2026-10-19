#!/usr/bin/env python3
"""Hold ``bt_bench_session.py`` to a recording made with no radio.

    python scripts/test_bt_bench_session.py

* **start**: refuses a name used here or on the drive; otherwise logs the cell, starts the recorder (a fake here: the
  BB60D is never opened) and prints the player's command in exactly the form the bench's one permission covers.
* **finish**, on a capture ``make_capture`` made from the short shot at 20 dB with a 58 kHz VSG offset, and a player
  sidecar saying it was sent: the session log's steps in order (recorder, tx, snr, align, copy); the snr read; the
  aligner's console in ``NAME.align.log`` and none of its carrier values on the console or in the log; only allowed
  keys printed; every file on the drive and its hash appended; the two ``.cf32`` files gone from here, nothing else.
* a copy that does not hash the same leaves everything here and appends no sum; a name already on the drive is
  refused before anything is copied; ``verify`` reads the sums back from the disk.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import bt_bench as bb  # noqa: E402
from scripts import bt_bench_session as bs  # noqa: E402

failures = []
FIRST = 140_000_000
N_SHORT = 16_000_000
#: A stand-in for the registration's key-copying script: the same keys.
STUB = '''import json, sys
ALLOWED = ("problem", "jitter_samples", "step_samples", "bend_samples", "delay_at_0", "ppm", "locate_score", "n_bursts",
           "n_found", "n_off_line", "vsg_offset_hz_set", "shot", "capture", "capture_samples")
d = json.load(open(sys.argv[1]))
json.dump({k: d[k] for k in ALLOWED if k in d}, open(sys.argv[2], "w"))
'''


def check(ok, what):
    print('  %s %s' % ('ok  ' if ok else 'FAIL', what), flush=True)
    if not ok:
        failures.append(what)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = bs.main(argv) or 0
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
            err.write(str(e.code))
    return code, out.getvalue(), err.getvalue()


def recording(tmp, shots, side, name, here, sent=True):
    """A finished fake recording in ``here``: the capture, the recorder's json and the player's sidecar."""
    os.makedirs(here, exist_ok=True)
    cap = os.path.join(here, name + '.cf32')
    planted = bb.make_capture(os.path.join(shots, 'bench_885614_d23.cf32'), cap, pre=0.1, post=0.1, frac=0.37,
                              ppm=-0.37, cfo_hz=58900.0, vsg_offset_hz=58000.0, snr_db=20.0)
    json.dump(dict(recorded_s=1.0, adc_overflows=0, lost_sample_overflows=0, file=name + '.cf32', center_mhz=2439.0,
                   sample_rate=bb.FS), open(cap + '.rec.json', 'w'))
    open(os.path.join(here, name + '.rec.log'), 'w').write('fake recorder\n')
    json.dump(dict(name=name, shot='bench_885614_d23', shot_sha256=side['bench_shot']['sha256'],
                   vsg_center_mhz=2439.058, vsg_offset_hz=58000.0, vsg_level_dbm=-68.0, pad_db=0.0, sent=sent,
                   state='sent' if sent else 'error'), open(os.path.join(here, name + '_tx.json'), 'w'))
    return planted


def main():
    t0 = time.time()
    tmp = tempfile.mkdtemp(prefix='test_bt_bench_session_')
    try:
        shots = os.path.join(tmp, 'shots')
        _, side_path = bb.make_shot('885614', shots, n_samples=N_SHORT, first=FIRST)
        side = json.load(open(side_path))
        stub = os.path.join(tmp, 'keys', 's4_bench_allowed_keys.py')
        os.makedirs(os.path.dirname(stub))
        open(stub, 'w').write(STUB)
        here, drive = os.path.join(tmp, 'here'), os.path.join(tmp, 'drive')
        os.makedirs(drive)
        common = ['--dir', here, '--drive', drive]

        print('\nstart')
        launched = []

        class FakePopen:
            def __init__(self, argv, **kw):
                launched.append(argv)
                self.pid = 999999

        real = bs.subprocess.Popen
        bs.subprocess.Popen = FakePopen
        try:
            code, out, _ = run(['start', 'r01', '--lap', '885614', '--offset-khz', '-58', '--level', '-68'] + common)
        finally:
            bs.subprocess.Popen = real
        rule = ("ssh worklaptop1-fast 'cd /data/python/SDR && timeout -k 30 60 ~/miniconda3/envs/gnu/bin/python "
                "scripts/bt_bench_play.py")
        cmd = [ln for ln in out.splitlines() if ln.startswith('ssh ')]
        check(code == 0 and launched and launched[0][2:4] == ['record', os.path.join(here, 'r01.cf32')]
              and '30' in launched[0], 'starts bt_bench.py record for 30 s (the recorder faked here)')
        check(len(cmd) == 1 and cmd[0].startswith(rule) and '--go --pad-db 0 --level -68 --vsg-offset-khz -58 '
              '--name r01' in cmd[0], 'prints the player command in the one form the permission covers')
        log = [json.loads(ln) for ln in open(os.path.join(drive, bs.LOG))]
        check(log[-1]['step'] == 'start' and log[-1]['level_dbm'] == -68.0 and log[-1]['offset_khz'] == -58.0,
              'the cell is logged on the drive before the recording')
        open(os.path.join(here, 'r01.cf32'), 'w').close()
        code, _, err = run(['start', 'r01', '--lap', '885614', '--offset-khz', '-58', '--level', '-68'] + common)
        check(code != 0 and 'already exists' in err, 'a name already used is refused')
        os.remove(os.path.join(here, 'r01.cf32'))

        print('\nfinish')
        name = 'r02'
        recording(tmp, shots, side, name, here)
        code, out, err = run(['finish', name, '--shots', shots, '--allowed-keys', stub] + common)
        steps = [e['step'] for e in (json.loads(ln) for ln in open(os.path.join(drive, bs.LOG))) if e['name'] == name]
        check(code == 0 and steps == ['recorder', 'tx', 'snr', 'align', 'copy'],
              'finish runs recorder, tx, snr, align, copy, in that order (%s; %s)' % (steps, err.strip()[-200:]))
        snr = json.load(open(os.path.join(drive, name + '.snr.json')))
        check(abs(snr['snr_db'] - 20.0) < 0.2, 'the level: %s' % bb.snr_summary(snr))
        alog = open(os.path.join(drive, name + '.align.log')).read()
        session = open(os.path.join(drive, bs.LOG)).read()
        check('reference error' in alog and 'found' in alog,
              'the aligner\'s console, carrier values included, went to %s.align.log' % name)
        check('reference error' not in out and 'instruments' not in out and 'reference_error' not in session
              and 'cfo_instrument' not in session and 'f_located' not in session,
              'no carrier value on the console or in the session log')
        shown = [ln for ln in out.splitlines() if ln.startswith('align (allowed keys only):')]
        view = json.loads(shown[0].split(':', 1)[1]) if shown else {}
        check(view.get('problem', 'x') is None and 'jitter_samples' in view and not set(view) - set(
            json.load(open(os.path.join(drive, name + '.align.json')))) and 'reference_error_hz' not in view,
              'only the allowed keys are printed, from an accepted alignment')
        sums = dict(reversed(ln.rstrip('\n').split('  ', 1)) for ln in open(os.path.join(drive, bs.SUMS)))
        on_drive = all(os.path.exists(os.path.join(drive, f)) for f in sums)
        check(on_drive and os.path.join('align', name + '.cf32') in sums and name + '.cf32' in sums
              and all(bs.sha256_of(os.path.join(drive, f)) == h for f, h in sums.items()),
              '%d files on the drive, each hash in %s' % (len(sums), bs.SUMS))
        check(not os.path.exists(os.path.join(here, name + '.cf32'))
              and not os.path.exists(os.path.join(here, 'align', name + '.cf32'))
              and os.path.exists(os.path.join(here, name + '.snr.json'))
              and os.path.exists(os.path.join(here, name + '_tx.json')),
              'only the two .cf32 files are deleted here, after the copy')

        print('\nwhen a copy is not what it should be')
        name = 'r03'
        recording(tmp, shots, side, name, here, sent=False)
        n_sums = len(open(os.path.join(drive, bs.SUMS)).readlines())
        real_hash = bs.sha256_from_disk
        bs.sha256_from_disk = lambda p: '0' * 64 if p.endswith('.snr.json') else real_hash(p)
        try:
            code, out, err = run(['finish', name, '--shots', shots, '--allowed-keys', stub] + common)
        finally:
            bs.sha256_from_disk = real_hash
        check('align wrote no report' in out, 'a sidecar not sent: align refuses before reading, and finish says so')
        check(code != 0 and os.path.exists(os.path.join(here, name + '.cf32'))
              and len(open(os.path.join(drive, bs.SUMS)).readlines()) == n_sums,
              'a copy that hashes differently: nothing deleted here, no sum appended (%s)' % err.strip()[-120:])
        code, _, err = run(['finish', name, '--shots', shots, '--allowed-keys', stub] + common)
        check(code != 0 and 'already exists on the drive' in err and os.path.exists(os.path.join(here, name + '.cf32')),
              'a file already on the drive: refused, nothing overwritten or deleted')

        print('\nverify')
        code, out, _ = run(['verify', drive])
        check(code == 0 and 'files match' in out, out.strip().splitlines()[-1])
        open(os.path.join(drive, 'r02.snr.json'), 'a').write(' ')
        code, out, _ = run(['verify', drive])
        check(code == 1 and 'r02.snr.json: FAILED' in out, 'a changed file fails verify')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('\n%.0f s' % (time.time() - t0))
    for w in failures:
        print('FAILED:', w)
    print('RESULT: PASS' if not failures else 'RESULT: FAIL (%d)' % len(failures))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
