#!/usr/bin/env python3
"""Hold ``bt_bench_session.py`` to a recording session made with no radio.

    python scripts/test_bt_bench_session.py

A stand-in for the registration's decision file (``LevelSession`` with the same rules) drives a session of fake
recordings made by ``make_capture`` from the short shot: the recorder is faked (the BB60D is never opened) and the
player's sidecar is written by hand.

* **start**: the level is the session's, printed in the one form the bench permission covers; refused while the
  recording before has no decision, for a name used, for a slot other than the one a retake left open, and once the
  session is NOT DECIDABLE; a decision file that no longer replays the log is refused.
* **finish**: a player that did not send is a fault (no snr, RETAKE_SAME_LEVEL); 20 dB is KEEP; 17 dB is DISCARD_STEP
  with the next level 3 dB up; a fault logged before the decision makes a second non-keep in the slot NOT DECIDABLE;
  nothing is aligned by finish; a sidecar of another level is refused, undecided.
* **close**: a KEEP is aligned with the console in ``NAME.align.log``, no carrier value on the console or in the log,
  only the allowed keys printed; anything else is not aligned; every file copied and hashed, the ``.cf32`` deleted
  here; a copy that does not hash the same deletes nothing and appends no sum.
* **log**: a fault after the decision is refused; **verify** reads the sums back from the disk and checks the chain.
* if bluey's own decision file is on this machine, it replays the session's attempts exactly as the stand-in does.
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
#: A stand-in for the registration's decision file: ``LevelSession`` with its rules, written again.
DECIDE = '''import math
FIRST_COMMAND_DBM = %s


class LevelSession:
    def __init__(self):
        self.level, self.relevels, self.state, self.capped, self.n = FIRST_COMMAND_DBM, 0, None, False, {}

    def decide(self, slot, *, recorder_fault=False, player_fault=False, exit_fault=False, snr_refused=False,
               median=None, lo=None, hi=None):
        if self.state:
            return self.state
        fault = recorder_fault or player_fault or exit_fault or snr_refused or (lo is not None and hi - lo > 2.0)
        if not fault and abs(median - 20.0) <= 1.0:
            return "KEEP"
        self.n[slot] = self.n.get(slot, 0) + 1
        if self.n[slot] >= 2 or (not fault and self.relevels >= 2):
            self.state = "NOT DECIDABLE"
            return self.state
        if fault:
            return "RETAKE_SAME_LEVEL"
        self.relevels += 1
        self.level = math.floor(2 * (self.level + 20.0 - median) + 0.5) / 2
        return "DISCARD_STEP"
'''
#: bluey's own decision file, where this machine has it.
REAL = '/data/python/bluey-swarm-ops/packets/s4-bench-reg-v5/diag_s4_bench_decide.py'


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


def recording(shots, side, name, here, snr_db=20.0, level=-68.0, sent=True):
    """A finished fake recording in ``here``: the capture, the recorder's json and the player's sidecar."""
    os.makedirs(here, exist_ok=True)
    cap = os.path.join(here, name + '.cf32')
    bb.make_capture(os.path.join(shots, 'bench_885614_d23.cf32'), cap, pre=0.1, post=0.1, frac=0.37, ppm=-0.37,
                    cfo_hz=58900.0, vsg_offset_hz=58000.0, snr_db=snr_db)
    json.dump(dict(recorded_s=1.0, adc_overflows=0, lost_sample_overflows=0, file=name + '.cf32', center_mhz=2439.0,
                   sample_rate=bb.FS), open(cap + '.rec.json', 'w'))
    open(os.path.join(here, name + '.rec.log'), 'w').write('fake recorder\n')
    json.dump(dict(name=name, shot='bench_885614_d23', shot_sha256=side['bench_shot']['sha256'],
                   vsg_center_mhz=2439.058, vsg_offset_hz=58000.0, vsg_level_dbm=level, pad_db=0.0, sent=sent,
                   state='sent' if sent else 'error before the send'), open(os.path.join(here, name + '_tx.json'), 'w'))


def main():
    t0 = time.time()
    tmp = tempfile.mkdtemp(prefix='test_bt_bench_session_')
    real_popen = bs.subprocess.Popen
    launched = []

    class Fake:
        pid = 999999

    def FakePopen(argv, **kw):
        """The recorder is never started; every other process (the decision file's replay) runs."""
        if argv[2:3] != ['record']:
            return real_popen(argv, **kw)
        launched.append(argv)
        return Fake()

    try:
        shots = os.path.join(tmp, 'shots')
        _, side_path = bb.make_shot('885614', shots, n_samples=N_SHORT, first=FIRST)
        side = json.load(open(side_path))
        keys = os.path.join(tmp, 'keys', 's4_bench_allowed_keys.py')
        os.makedirs(os.path.dirname(keys))
        open(keys, 'w').write(STUB)
        decide = os.path.join(tmp, 'keys', 'diag_s4_bench_decide.py')
        open(decide, 'w').write(DECIDE % '-68.0')
        here, drive = os.path.join(tmp, 'here'), os.path.join(tmp, 'drive')
        os.makedirs(drive)
        common = ['--dir', here, '--drive', drive]
        dec = ['--decide', decide]
        fin = ['--shots', shots] + dec + common
        clo = ['--shots', shots, '--allowed-keys', keys] + common

        def start(name, slot):
            bs.subprocess.Popen = FakePopen
            try:
                return run(['start', name, '--slot', str(slot), '--lap', '885614', '--offset-khz', '-58'] + dec + common)
            finally:
                bs.subprocess.Popen = real_popen

        def log():
            return [json.loads(ln) for ln in open(os.path.join(drive, bs.LOG))]

        def steps(name):
            return [e['step'] for e in log() if e['name'] == name]

        def decision(name):
            return [e for e in log() if e['name'] == name and e['step'] == 'decision'][-1]

        print('\nstart')
        code, out, err = start('r01', 1)
        rule = ("ssh worklaptop1-fast 'cd /data/python/SDR && timeout -k 30 60 ~/miniconda3/envs/gnu/bin/python "
                "scripts/bt_bench_play.py")
        cmd = [ln for ln in out.splitlines() if ln.startswith('ssh ')]
        check(code == 0 and launched and launched[0][2:4] == ['record', os.path.join(here, 'r01.cf32')]
              and '30' in launched[0], 'starts bt_bench.py record for 30 s (the recorder faked here) %s' % err[-200:])
        check(len(cmd) == 1 and cmd[0].startswith(rule) and '--go --pad-db 0 --level -68 --vsg-offset-khz -58 '
              '--name r01' in cmd[0], 'prints the player command at the session\'s first level, in the permitted form')
        check(log()[-1]['step'] == 'start' and log()[-1]['level_dbm'] == -68.0 and log()[-1]['slot'] == 1,
              'the cell, slot and level are logged on the drive before the recording')
        code, _, err = start('r02', 2)
        check(code != 0 and 'no decision yet' in err, 'a second recording while the first has no decision is refused')

        print('\nfinish and close: a player that did not send')
        recording(shots, side, 'r01', here, sent=False)
        code, out, err = run(['finish', 'r01'] + fin)
        d = decision('r01')
        check(code == 0 and d['action'] == 'RETAKE_SAME_LEVEL' and d['inputs']['player_fault']
              and not os.path.exists(os.path.join(here, 'r01.snr.json')) and d['level_next'] == -68.0,
              'a player fault: RETAKE_SAME_LEVEL at the same level, no snr run (%s)' % err.strip()[-200:])
        check(not os.path.exists(os.path.join(here, 'r01.align.json')), 'finish aligns nothing')
        code, _, err = run(['log', 'r01', '--event', 'fault', '--reason', 'late'] + common)
        check(code != 0 and 'already has a decision' in err, 'a fault logged after the decision is refused')
        code, out, _ = run(['close', 'r01'] + clo)
        check(code == 0 and 'not aligned' in out and not os.path.exists(os.path.join(drive, 'r01.align.json'))
              and not os.path.exists(os.path.join(here, 'r01.cf32')) and os.path.exists(os.path.join(drive, 'r01.cf32')),
              'close: not aligned, the raw copied to the drive and deleted here')
        code, _, err = start('r02', 2)
        check(code != 0 and 'slot 1 was left for a retake' in err, 'the next recording must be the same slot')

        print('\nfinish and close: 20 dB kept')
        code, out, _ = start('r02', 1)
        check(code == 0 and '--level -68 ' in out, 'the retake at the same level')
        recording(shots, side, 'r02', here)
        code, out, err = run(['finish', 'r02'] + fin)
        d = decision('r02')
        check(code == 0 and d['action'] == 'KEEP' and abs(d['inputs']['median'] - 20.0) < 0.2
              and steps('r02') == ['start', 'recorder', 'tx', 'snr', 'decision']
              and not os.path.exists(os.path.join(here, 'r02.align.json')),
              'KEEP at %.2f dB; recorder, tx, snr, decision, and nothing aligned (%s)'
              % (d['inputs']['median'] or 0, err.strip()[-200:]))
        code, out, err = run(['close', 'r02'] + clo)
        check(code == 0 and steps('r02')[-2:] == ['align', 'copy'], 'close aligns a KEEP, then copies (%s)'
              % err.strip()[-200:])
        alog = open(os.path.join(drive, 'r02.align.log')).read()
        session = open(os.path.join(drive, bs.LOG)).read()
        check('reference error' in alog and 'reference error' not in out and 'instruments' not in out
              and 'reference_error' not in session and 'cfo_instrument' not in session and 'f_located' not in session,
              'the aligner\'s console in r02.align.log; no carrier value on the console or in the session log')
        shown = [ln for ln in out.splitlines() if ln.startswith('align (allowed keys only):')]
        view = json.loads(shown[0].split(':', 1)[1]) if shown else {}
        check(view.get('problem', 'x') is None and 'jitter_samples' in view and 'reference_error_hz' not in view,
              'only the allowed keys are printed, from an accepted alignment')
        sums = dict(reversed(ln.rstrip('\n').split('  ', 1)) for ln in open(os.path.join(drive, bs.SUMS)))
        check(os.path.join('align', 'r02.cf32') in sums and 'r02.cf32' in sums and 'r01.cf32' in sums
              and all(bs.sha256_of(os.path.join(drive, f)) == h for f, h in sums.items())
              and not os.path.exists(os.path.join(here, 'r02.cf32'))
              and not os.path.exists(os.path.join(here, 'align', 'r02.cf32'))
              and os.path.exists(os.path.join(here, 'r02.snr.json')),
              '%d files on the drive, each hash in %s; only the .cf32 deleted here' % (len(sums), bs.SUMS))

        print('\na level step, then a second non-keep in the slot')
        start('r03', 2)
        recording(shots, side, 'r03', here, snr_db=17.0)
        code, out, err = run(['finish', 'r03'] + fin)
        d = decision('r03')
        check(code == 0 and d['action'] == 'DISCARD_STEP' and d['level_next'] == -65.0,
              '17 dB: DISCARD_STEP, the next level -65.0 (%s)' % (d['inputs']['median'],))
        n_sums = len(open(os.path.join(drive, bs.SUMS)).readlines())
        real_hash = bs.sha256_from_disk
        bs.sha256_from_disk = lambda p: '0' * 64 if p.endswith('.snr.json') else real_hash(p)
        try:
            code, out, err = run(['close', 'r03'] + clo)
        finally:
            bs.sha256_from_disk = real_hash
        check(code != 0 and os.path.exists(os.path.join(here, 'r03.cf32'))
              and len(open(os.path.join(drive, bs.SUMS)).readlines()) == n_sums,
              'a copy that hashes differently: nothing deleted here, no sum appended')
        code, _, err = run(['close', 'r03'] + clo)
        check(code != 0 and 'already exists on the drive' in err and os.path.exists(os.path.join(here, 'r03.cf32')),
              'a file already on the drive: refused, nothing overwritten or deleted')
        open(decide, 'w').write(DECIDE % '-69.0')
        code, _, err = start('r04', 2)
        check(code != 0 and 'the log or the code changed' in err, 'a decision file that no longer replays the log')
        open(decide, 'w').write(DECIDE % '-68.0')
        code, _, err = start('r04', 3)
        check(code != 0 and 'slot 2 was left' in err, 'a discard leaves its slot open too')
        code, out, _ = start('r04', 2)
        check(code == 0 and '--level -65 ' in out, 'the stepped level persists into the next recording')
        recording(shots, side, 'r04', here, level=-65.0)
        code, _, _ = run(['log', 'r04', '--event', 'fault', '--reason', 'player exit 1'] + common)
        code, out, err = run(['finish', 'r04'] + fin)
        d = decision('r04')
        check(code == 0 and d['inputs']['exit_fault'] and d['action'] == 'NOT DECIDABLE'
              and 'NOT DECIDABLE' in out, 'a fault logged before the decision, a second non-keep in slot 2: '
              'NOT DECIDABLE')
        code, _, err = start('r05', 3)
        check(code != 0 and 'NOT DECIDABLE' in err, 'no recording after NOT DECIDABLE')

        print('\na sidecar of another level')
        drive2 = os.path.join(tmp, 'drive2')
        os.makedirs(drive2)
        bs.subprocess.Popen = FakePopen
        try:
            run(['start', 'q01', '--slot', '1', '--lap', '885614', '--offset-khz', '0'] + dec
                + ['--dir', here, '--drive', drive2])
        finally:
            bs.subprocess.Popen = real_popen
        recording(shots, side, 'q01', here, level=-67.0)
        code, _, err = run(['finish', 'q01', '--shots', shots] + dec + ['--dir', here, '--drive', drive2])
        check(code != 0 and 'undecided' in err and not [e for e in map(json.loads, open(
            os.path.join(drive2, bs.LOG))) if e['step'] == 'decision'], 'refused, and no decision logged')

        if os.path.exists(REAL):
            attempts = [dict(slot=e['slot'], inputs=e['inputs']) for e in log() if e['step'] == 'decision']
            mine, theirs = bs._replay(decide, attempts), bs._replay(REAL, attempts)
            check([s['action'] for s in mine['steps']] == [s['action'] for s in theirs['steps']]
                  and mine['level'] == theirs['level'] and theirs['first_level'] == -68.0,
                  'bluey\'s LevelSession replays this session the same: %s'
                  % [s['action'] for s in theirs['steps']])
        else:
            print('  (bluey\'s decision file is not on this machine; the stand-in only)')

        print('\nverify')
        code, out, _ = run(['verify', drive])
        check(code == 0 and 'files match' in out and 'chain OK' in out, out.strip().splitlines()[-1])
        open(os.path.join(drive, 'r02.snr.json'), 'a').write(' ')
        code, out, _ = run(['verify', drive])
        check(code == 1 and 'r02.snr.json: FAILED' in out, 'a changed file fails verify')
        lines = open(os.path.join(here, bs.LOG)).read().splitlines()
        del lines[1]
        open(os.path.join(here, bs.LOG), 'w').write('\n'.join(lines) + '\n')
        at, why = bs.check_chain(os.path.join(here, bs.LOG))
        check(why is not None and at == 2, 'a session log with a line dropped breaks its chain at that line')
    finally:
        bs.subprocess.Popen = real_popen
        shutil.rmtree(tmp, ignore_errors=True)
    print('\n%.0f s' % (time.time() - t0))
    for w in failures:
        print('FAILED:', w)
    print('RESULT: PASS' if not failures else 'RESULT: FAIL (%d)' % len(failures))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
