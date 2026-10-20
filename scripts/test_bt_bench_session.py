#!/usr/bin/env python3
"""Hold ``bt_bench_session.py`` to a recording session made with no radio.

    python scripts/test_bt_bench_session.py

A stand-in for the registration's decision file (``LevelSession`` with the same rules) drives a session of fake
recordings made by ``make_capture`` from the short shot: the recorder is faked (the BB60D is never opened) and the
player's sidecar is written by hand. The keys stand-in returns the registration's v6 operator view.

* **start**: the level is the session's, printed in the one form the bench permission covers; refused while the
  recording before has no decision or is not on the drive, for a slot other than the one a retake left open, once the
  session is NOT DECIDABLE, when the laptop's and the drive's logs differ, and for a decision file that no longer
  replays the log (an action, a level or the re-level count).
* **finish**: a player that did not send, or whose state is not sent, is a fault (no snr, RETAKE_SAME_LEVEL); 20 dB is
  KEEP; 17 dB is DISCARD_STEP with the next level 3 dB up; a fault logged before finish makes a second non-keep in the
  slot NOT DECIDABLE; nothing is aligned by finish; a sidecar of another level or shot sha256 is refused, undecided.
* **close**: a KEEP is aligned with the console in ``NAME.align.log``, no carrier value on the console or in the log,
  only the operator view printed, and a keys script that returns a carrier key stops the close with nothing shown;
  anything else is not aligned; a copy that does not hash the same leaves its partials on the drive, nothing at the
  final names, nothing deleted here and no sum, and blocks the next recording until it is closed.
* **log**: a fault once finish has begun is refused. The replay runs the decision file's bytes, never a stale ``.pyc``.
* **verify**: chain, order and sums from the disk; a partial or any file the sums do not list fails, as do a missing
  sums file and a listed file that is gone. ``check_order`` catches each breach the registration names, and passes a
  discard then a kept retake, and a finish retried after a crash.
* if bluey's own v6 files are on this machine: its ``LevelSession`` replays the session step for step as the stand-in
  does, and its keys script returns the operator view with no carrier key.
"""
import contextlib
import io
import json
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import bt_bench as bb  # noqa: E402
from scripts import bt_bench_session as bs  # noqa: E402

failures = []
FIRST = 140_000_000
N_SHORT = 16_000_000
#: A stand-in for the registration's key-copying script: its v6 operator view.
STUB = '''import json, sys
d = json.load(open(sys.argv[1]))
v = {k: d[k] for k in ("shot", "capture", "capture_samples", "ppm", "delay_at_0") if k in d}
v["accepted"] = "problem" in d and d["problem"] is None
json.dump(v, open(sys.argv[2], "w"))
'''
#: A keys script that lets the carrier values through.
LEAKY = '''import json, sys
json.dump(json.load(open(sys.argv[1])), open(sys.argv[2], "w"))
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
#: bluey's own v6 files, where this machine has them.
REAL = '/data/python/bluey-swarm-ops/packets/s4-bench-reg-v6/'


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


def recording(shots, side, name, here, snr_db=20.0, level=-68.0, sent=True, state=None, sha=None):
    """A finished fake recording in ``here``: the capture, the recorder's json and the player's sidecar."""
    os.makedirs(here, exist_ok=True)
    cap = os.path.join(here, name + '.cf32')
    bb.make_capture(os.path.join(shots, 'bench_885614_d23.cf32'), cap, pre=0.1, post=0.1, frac=0.37, ppm=-0.37,
                    cfo_hz=58900.0, vsg_offset_hz=58000.0, snr_db=snr_db)
    json.dump(dict(recorded_s=1.0, adc_overflows=0, lost_sample_overflows=0, file=name + '.cf32', center_mhz=2439.0,
                   sample_rate=bb.FS), open(cap + '.rec.json', 'w'))
    open(os.path.join(here, name + '.rec.log'), 'w').write('fake recorder\n')
    json.dump(dict(name=name, shot='bench_885614_d23', shot_sha256=sha or side['bench_shot']['sha256'],
                   vsg_center_mhz=2439.058, vsg_offset_hz=58000.0, vsg_level_dbm=level, pad_db=0.0, sent=sent,
                   state=state or ('sent' if sent is True else 'error before the send')),
              open(os.path.join(here, name + '_tx.json'), 'w'))


def events(name, slot, action, median=20.0, lo=19.8, hi=20.2, aligned=None):
    """The session-log lines of one recording as the script writes them, for ``check_order``."""
    out = [(name, 'start', dict(slot=slot)), (name, 'recorder', {}), (name, 'tx', {}),
           (name, 'snr', dict(problem=None, snr_db=median, snr_db_min=lo, snr_db_max=hi)),
           (name, 'decision', dict(slot=slot, action=action,
                                   state='NOT DECIDABLE' if action == 'NOT DECIDABLE' else None,
                                   inputs=dict(median=median, lo=lo, hi=hi, snr_refused=False, exit_fault=False)))]
    aligned = action == 'KEEP' if aligned is None else aligned
    out.append((name, 'align', dict(exit=0) if aligned else dict(skipped='not aligned')))
    return out + [(name, 'copy', dict(files={}))]


def order_of(tmp, lines):
    folder = tempfile.mkdtemp(dir=tmp)
    for name, step, kw in lines:
        bs._log([folder], name, step, **kw)
    return bs.check_order(bs._entries(folder))


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
        keydir = os.path.join(tmp, 'keys')
        os.makedirs(keydir)
        keys, leaky = os.path.join(keydir, 's4_bench_allowed_keys.py'), os.path.join(keydir, 'leaky_keys.py')
        open(keys, 'w').write(STUB)
        open(leaky, 'w').write(LEAKY)
        decide = os.path.join(keydir, 'diag_s4_bench_decide.py')
        open(decide, 'w').write(DECIDE % '-68.0')
        here, drive = os.path.join(tmp, 'here'), os.path.join(tmp, 'drive')
        os.makedirs(drive)

        def session(h, d):
            def start(name, slot, offset='-58'):
                bs.subprocess.Popen = FakePopen
                try:
                    return run(['start', name, '--slot', str(slot), '--lap', '885614', '--offset-khz', offset,
                                '--decide', decide, '--dir', h, '--drive', d])
                finally:
                    bs.subprocess.Popen = real_popen

            def finish(name):
                return run(['finish', name, '--shots', shots, '--decide', decide, '--dir', h, '--drive', d])

            def close(name, k=keys):
                return run(['close', name, '--shots', shots, '--allowed-keys', k, '--dir', h, '--drive', d])

            def log(name, event, reason):
                return run(['log', name, '--event', event, '--reason', reason, '--dir', h, '--drive', d])
            return start, finish, close, log

        start, finish, close, log = session(here, drive)

        def lines():
            return [json.loads(ln) for ln in open(os.path.join(drive, bs.LOG))]

        def steps(name):
            return [e['step'] for e in lines() if e['name'] == name]

        def decision(name):
            return [e for e in lines() if e['name'] == name and e['step'] == 'decision'][-1]

        print('\nstart')
        code, out, err = start('r01', 1)
        rule = ("ssh worklaptop1-fast 'cd /data/python/SDR && timeout -k 30 60 ~/miniconda3/envs/gnu/bin/python "
                "scripts/bt_bench_play.py")
        cmd = [ln for ln in out.splitlines() if ln.startswith('ssh ')]
        check(code == 0 and launched and launched[0][2:4] == ['record', os.path.join(here, 'r01.cf32')]
              and '30' in launched[0], 'starts bt_bench.py record for 30 s (the recorder faked here) %s' % err[-200:])
        check(len(cmd) == 1 and cmd[0].startswith(rule) and '--go --pad-db 0 --level -68 --vsg-offset-khz -58 '
              '--name r01' in cmd[0], 'prints the player command at the session\'s first level, in the permitted form')
        first = lines()[-1]
        check(first['step'] == 'start' and first['level_dbm'] == -68.0 and first['slot'] == 1
              and first['decide']['sha256'] == bs.sha256_of(decide),
              'the cell, slot, level and the decision file\'s sha256 are logged before the recording')
        code, _, err = start('r02', 2)
        check(code != 0 and 'no decision yet' in err, 'a second recording while the first has no decision is refused')

        print('\nfinish and close: a player that did not send')
        recording(shots, side, 'r01', here, sent=False)
        code, out, err = finish('r01')
        d = decision('r01')
        check(code == 0 and d['action'] == 'RETAKE_SAME_LEVEL' and d['inputs']['player_fault']
              and not os.path.exists(os.path.join(here, 'r01.snr.json')) and d['level_next'] == -68.0,
              'a player fault: RETAKE_SAME_LEVEL at the same level, no snr run (%s)' % err.strip()[-200:])
        check(not os.path.exists(os.path.join(here, 'r01.align.json')), 'finish aligns nothing')
        code, _, err = log('r01', 'fault', 'late')
        check(code != 0 and 'finish has begun' in err, 'a fault logged once finish has begun is refused')
        code, _, err = start('r02', 1)
        check(code != 0 and 'close it first' in err, 'no recording before the one before it is on the drive')
        code, out, _ = close('r01')
        check(code == 0 and 'not aligned' in out and not os.path.exists(os.path.join(drive, 'r01.align.json'))
              and not os.path.exists(os.path.join(here, 'r01.cf32')) and os.path.exists(os.path.join(drive, 'r01.cf32')),
              'close: not aligned, the raw copied to the drive and deleted here')
        code, _, err = start('r02', 2)
        check(code != 0 and 'slot 1 was left for a retake' in err, 'the next recording must be the same slot')

        print('\nfinish and close: 20 dB kept')
        code, out, _ = start('r02', 1)
        check(code == 0 and '--level -68 ' in out, 'the retake at the same level')
        recording(shots, side, 'r02', here)
        code, out, err = finish('r02')
        d = decision('r02')
        check(code == 0 and d['action'] == 'KEEP' and abs(d['inputs']['median'] - 20.0) < 0.2
              and steps('r02') == ['start', 'recorder', 'tx', 'snr', 'decision']
              and not os.path.exists(os.path.join(here, 'r02.align.json')),
              'KEEP at %.2f dB; recorder, tx, snr, decision, and nothing aligned (%s)'
              % (d['inputs']['median'] or 0, err.strip()[-200:]))
        code, out, err = close('r02')
        check(code == 0 and steps('r02')[-2:] == ['align', 'copy'], 'close aligns a KEEP, then copies (%s)'
              % err.strip()[-200:])
        alog = open(os.path.join(drive, 'r02.align.log')).read()
        log_text = open(os.path.join(drive, bs.LOG)).read()
        check('reference error' in alog and 'reference error' not in out and 'instruments' not in out
              and not any(k in log_text for k in bs.CARRIER_KEYS if k != 'rows'),
              'the aligner\'s console in r02.align.log; no carrier value on the console or in the session log')
        shown = [ln for ln in out.splitlines() if ln.startswith('align (allowed keys only):')]
        view = json.loads(shown[0].split(':', 1)[1]) if shown else {}
        check(view.get('accepted') is True and 'ppm' in view and 'jitter_samples' not in view
              and 'problem' not in view, 'only the operator view is printed: %s' % sorted(view))
        sums = dict(reversed(ln.rstrip('\n').split('  ', 1)) for ln in open(os.path.join(drive, bs.SUMS)))
        check(os.path.join('align', 'r02.cf32') in sums and 'r02.cf32' in sums and 'r01.cf32' in sums
              and all(bs.sha256_of(os.path.join(drive, f)) == h for f, h in sums.items())
              and not os.path.exists(os.path.join(here, 'r02.cf32'))
              and not os.path.exists(os.path.join(here, 'align', 'r02.cf32'))
              and os.path.exists(os.path.join(here, 'r02.snr.json')),
              '%d files on the drive, each hash in %s; only the .cf32 deleted here' % (len(sums), bs.SUMS))

        print('\na level step, a copy that does not match, then a second non-keep in the slot')
        start('r03', 2)
        recording(shots, side, 'r03', here, snr_db=17.0)
        code, out, err = finish('r03')
        d = decision('r03')
        check(code == 0 and d['action'] == 'DISCARD_STEP' and d['level_next'] == -65.0 and d['relevels'] == 1,
              '17 dB: DISCARD_STEP, the next level -65.0 (%s)' % (d['inputs']['median'],))
        n_sums = len(open(os.path.join(drive, bs.SUMS)).readlines())
        real_hash = bs.sha256_from_disk
        bs.sha256_from_disk = lambda p: '0' * 64 if p.endswith('.snr.json' + bs.PARTIAL) else real_hash(p)
        try:
            code, out, err = close('r03')
        finally:
            bs.sha256_from_disk = real_hash
        partials = sorted(f for f in os.listdir(drive) if f.startswith('r03') and f.endswith(bs.PARTIAL))
        check(code != 0 and os.path.exists(os.path.join(here, 'r03.cf32')) and partials
              and not any(os.path.exists(os.path.join(drive, f[:-len(bs.PARTIAL)])) for f in partials)
              and len(open(os.path.join(drive, bs.SUMS)).readlines()) == n_sums,
              'a copy that hashes differently: partials left on the drive (%d), nothing at a final name, nothing '
              'deleted here, no sum appended' % len(partials))
        code, _, err = close('r03')
        check(code != 0 and bs.PARTIAL + ' already exists on the drive' in err
              and os.path.exists(os.path.join(here, 'r03.cf32')), 'a partial on the drive refuses the next copy')
        code, out, _ = run(['verify', drive])
        check(code == 1 and 'NOT IN' in out and bs.PARTIAL in out, 'verify fails on a partial the sums do not list')
        code, _, err = start('r04', 2)
        check(code != 0 and 'r03 is not on the drive yet' in err, 'and the next recording waits for it')
        for f in partials:      # this test's own folder: the user looks at a real one
            os.remove(os.path.join(drive, f))
        code, out, err = close('r03')
        check(code == 0 and os.path.exists(os.path.join(drive, 'r03.cf32')), 'the partials gone, the close goes through')

        open(decide, 'w').write((DECIDE % '-68.0').replace('self.relevels += 1', 'self.relevels += 0'))
        code, _, err = start('r04', 2)
        check(code != 0 and 'the log or the code changed' in err, 'a decision file whose re-level count no longer '
              'replays the log is refused, its actions and levels the same')
        open(decide, 'w').write(DECIDE % '-68.0')
        code, _, err = start('r04', 3)
        check(code != 0 and 'slot 2 was left' in err, 'a discard leaves its slot open too')
        code, out, _ = start('r04', 2)
        check(code == 0 and '--level -65 ' in out, 'the stepped level persists into the next recording')
        recording(shots, side, 'r04', here, level=-65.0)
        code, _, err = log('r04', 'fault', 'player exit 1')
        check(code == 0, 'a fault logged before finish: %s' % err.strip())
        code, out, err = finish('r04')
        d = decision('r04')
        check(code == 0 and d['inputs']['exit_fault'] and d['action'] == 'NOT DECIDABLE'
              and 'NOT DECIDABLE' in out, 'a second non-keep in slot 2: NOT DECIDABLE')
        close('r04')
        code, _, err = start('r05', 3)
        check(code != 0 and 'NOT DECIDABLE' in err, 'no recording after NOT DECIDABLE')

        print('\nthe replay runs the bytes it hashed')
        pyc = os.path.join(keydir, 'cache')
        os.makedirs(pyc)
        src = os.path.join(pyc, 'decide.py')
        open(src, 'w').write(DECIDE % '-68.0')
        py_compile.compile(src, cfile=os.path.join(pyc, '__pycache__', 'decide.cpython-%d%d.pyc'
                                                   % sys.version_info[:2]))
        st = os.stat(src)
        open(src, 'w').write(DECIDE % '-69.0')
        os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns))
        got = bs._replay(src, [])
        check(got['first_level'] == -69.0 and got['sha256'] == bs.sha256_of(src),
              'a .pyc of the old file, its size and mtime matching, is not what runs: %g' % got['first_level'])

        print('\nwhat stops a session')
        h2, d2 = os.path.join(tmp, 'here2'), os.path.join(tmp, 'drive2')
        shutil.copytree(here, h2)
        shutil.copytree(drive, d2)
        bs._log([d2], 'r04', 'note', reason='only on the drive')
        start2 = session(h2, d2)[0]
        code, _, err = start2('r05', 3)
        check(code != 0 and 'differ from line' in err, 'the laptop\'s and the drive\'s logs differ: refused')
        code, _, err = session(here, os.path.join(tmp, 'drive_new'))[0]('r05', 3)
        check(code != 0 and 'differ from line' in err, 'a second, empty drive folder does not start a new session')

        for tag, kw, want in (('level', dict(level=-67.0), 'undecided'),
                              ('sha', dict(sha='f' * 64), 'undecided'),
                              ('state', dict(state='error in the send: part of the shot may have gone out'), None)):
            h, d = os.path.join(tmp, 'here_' + tag), os.path.join(tmp, 'drive_' + tag)
            os.makedirs(d)
            st_, fi_ = session(h, d)[:2]
            st_('q01', 1, offset='0')
            recording(shots, side, 'q01', h, **kw)
            code, _, err = fi_('q01')
            decided = [e for e in bs._entries(d) if e['step'] == 'decision']
            if want:
                check(code != 0 and want in err and not decided, 'a sidecar of another %s: refused, undecided' % tag)
            else:
                check(code == 0 and decided and decided[0]['action'] == 'RETAKE_SAME_LEVEL'
                      and decided[0]['inputs']['player_fault'], 'sent true with a state that is not sent: a fault')

        print('\na keys script that lets a carrier value through')
        h, d = os.path.join(tmp, 'here_leak'), os.path.join(tmp, 'drive_leak')
        os.makedirs(d)
        st_, fi_, cl_ = session(h, d)[:3]
        st_('k01', 1)
        recording(shots, side, 'k01', h)
        fi_('k01')
        code, out, err = cl_('k01', k=leaky)
        log_text = open(os.path.join(d, bs.LOG)).read()
        check(code != 0 and 'carrier key' in err and 'reference_error_hz' not in out + err + log_text
              and os.path.exists(os.path.join(h, 'k01.cf32')), 'stops the close, nothing shown or logged or deleted')

        if os.path.exists(REAL + 'diag_s4_bench_decide.py'):
            attempts = [dict(slot=e['slot'], inputs=e['inputs']) for e in lines() if e['step'] == 'decision']
            mine, theirs = bs._replay(decide, attempts), bs._replay(REAL + 'diag_s4_bench_decide.py', attempts)
            row = [[s.get(k) for k in bs.STEP_KEYS] for s in mine['steps']]
            check(row == [[s.get(k) for k in bs.STEP_KEYS] for s in theirs['steps']] and theirs['first_level'] == -68.0,
                  'bluey\'s v6 LevelSession replays this session step for step: %s'
                  % [s['action'] for s in theirs['steps']])
        if os.path.exists(REAL + 's4_bench_allowed_keys.py'):
            out = os.path.join(tmp, 'v6_view.json')
            subprocess.run([sys.executable, '-I', REAL + 's4_bench_allowed_keys.py',
                            os.path.join(here, 'r02.align.json'), out], check=True)
            v6 = json.load(open(out))
            check(v6.get('accepted') is True and not bs._carrier_keys(v6) and 'jitter_samples' not in v6,
                  'bluey\'s v6 keys script on r02: the operator view, no carrier key: %s' % sorted(v6))

        print('\nverify')
        code, out, _ = run(['verify', drive])
        check(code == 0 and 'files match' in out and 'chain OK' in out and 'order OK' in out,
              'the session: chain and order OK, ' + out.strip().splitlines()[-1])
        open(os.path.join(drive, 'r02.snr.json'), 'a').write(' ')
        code, out, _ = run(['verify', drive])
        check(code == 1 and 'r02.snr.json: FAILED' in out, 'a changed file fails verify')
        empty = tempfile.mkdtemp(dir=tmp)
        bs._log([empty], 'a', 'start', slot=1)
        code, out, _ = run(['verify', empty])
        check(code == 1 and bs.SUMS + ': missing' in out, 'a folder with no sums fails, no traceback')
        open(os.path.join(empty, bs.SUMS), 'w').write('%s  gone.cf32\n' % ('0' * 64))
        code, out, _ = run(['verify', empty])
        check(code == 1 and 'gone.cf32: MISSING' in out, 'a listed file that is gone fails, no traceback')
        lines_here = open(os.path.join(here, bs.LOG)).read().splitlines()
        del lines_here[1]
        open(os.path.join(here, bs.LOG), 'w').write('\n'.join(lines_here) + '\n')
        at, why = bs.check_chain(os.path.join(here, bs.LOG))
        check(why is not None and at == 2, 'a session log with a line dropped breaks its chain at that line')

        print('\nthe order verify asserts')
        retake = events('a', 1, 'RETAKE_SAME_LEVEL', lo=17.0, hi=20.5)
        for what, lns, want in (
                ('an alignment after a retake decision', retake[:5] + [('a', 'align', dict(exit=0))], 'aligned after'),
                ('an alignment with no decision', retake[:4] + [('a', 'align', dict(exit=0))], 'no decision'),
                ('a second KEEP in a slot', events('a', 1, 'KEEP', aligned=False) + events('b', 1, 'KEEP')[:5],
                 'second KEEP'),
                ('a slot started again after an alignment', events('a', 1, 'KEEP') + events('b', 1, 'KEEP')[:1],
                 'after a recording of it was aligned'),
                ('a fault once finish began', retake[:2] + [('a', 'fault', dict(reason='late'))], 'after finish began'),
                ('a note once finish began', retake[:3] + [('a', 'note', dict(reason='x'))], 'after finish began'),
                ('a decision before its start', [('a', 'decision', dict(action='KEEP'))], 'before its start'),
                ('a start after NOT DECIDABLE', events('a', 1, 'NOT DECIDABLE', median=17.0)
                 + events('b', 2, 'KEEP')[:1], 'NOT DECIDABLE'),
                ('a slot other than the open retake', retake + events('b', 2, 'KEEP')[:1], 'left for a retake'),
                ('a start before the one before is copied', retake[:-1] + events('b', 1, 'KEEP')[:1],
                 'before a was copied'),
                ('a decision in another slot than its start', [ln if ln[1] != 'decision' else
                                                               (ln[0], ln[1], dict(ln[2], slot=2))
                                                               for ln in events('a', 1, 'KEEP')], 'decided in slot 2'),
                ('a decision on other numbers than its snr line', [ln if ln[1] != 'snr' else
                                                                   (ln[0], ln[1], dict(ln[2], snr_db=17.0))
                                                                   for ln in events('a', 1, 'KEEP')], 'its log says'),
                ('an align marked skipped that ran after a retake', retake[:5]
                 + [('a', 'align', dict(skipped='x', exit=0))], 'aligned after')):
            bad = order_of(tmp, lns)
            check(any(want in b for b in bad), '%s: %s' % (what, bad[0] if bad else 'not caught'))
        crash = events('a', 1, 'KEEP')
        for what, lns in (('a discard, not aligned, then its retake kept and aligned',
                           events('a', 1, 'DISCARD_STEP', median=17.0) + events('b', 1, 'KEEP')),
                          ('a finish retried after a crash before its decision', crash[:4] + crash[1:])):
            bad = order_of(tmp, lns)
            check(not bad, '%s: order OK %s' % (what, bad))
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
