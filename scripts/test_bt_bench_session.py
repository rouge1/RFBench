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
  slot NOT DECIDABLE; nothing is aligned by finish; a sidecar of another level or shot sha256 is an UNDECIDED halt.
* **halt and resume**: a halt (another level's sidecar, or a decision file that raises after the snr) is logged with
  no decision, refuses finish and log again, is copied by close and not aligned, and refuses every start but
  ``--resume``; a resume of another slot or under another decision file is refused with nothing logged; the resume is
  logged under the halted recording at the same level, and the slot's next non-KEEP is NOT DECIDABLE.
* **close**: a KEEP is aligned with the console in ``NAME.align.log``, no carrier value on the console or in the log,
  only the operator view printed, and a keys script that returns a carrier key stops the close with nothing shown;
  anything else is not aligned; a copy that does not hash the same leaves its partials on the drive, nothing at the
  final names, nothing deleted here and no sum, and blocks the next recording until it is closed.
* **log**: a fault once finish has begun is refused. The replay runs the decision file's bytes, never a stale ``.pyc``.
* **verify**: chain, order and sums from the disk; a partial or any file the sums do not list fails, as do a missing
  sums file and a listed file that is gone. ``check_order`` catches each breach the registration names, and passes a
  discard then a kept retake, and a finish retried after a crash.
* if bluey's v10 decision file is on this machine (or at ``BENCH_DECIDE_V10``): its ``LevelSession`` replays the
  halt session step for step as the stand-in does.
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
RAISE_OVER = 1e9


class LevelSession:
    def __init__(self):
        self.level, self.relevels, self.state, self.capped, self.n = FIRST_COMMAND_DBM, 0, None, False, {}
        self.halts, self.info, self.pending = {}, {}, None

    def decide(self, slot, *, recorder_fault=False, player_fault=False, exit_fault=False, snr_refused=False,
               median=None, lo=None, hi=None):
        if self.state:
            return self.state
        if self.pending is not None:
            if slot != self.pending:
                self.state = "NOT DECIDABLE"
                return self.state
            raise ValueError("a halt is pending")
        if median is not None and median > RAISE_OVER:
            raise ValueError("a decision file that raises")
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

    def halt(self, slot, level, decide_sha):
        if self.state:
            return self.state
        prior = self.n.get(slot, 0)
        self.halts[slot] = self.halts.get(slot, 0) + 1
        self.n[slot] = prior + 1
        self.info[slot] = (level, decide_sha, prior >= 1)
        if prior >= 1 or self.halts[slot] >= 2:
            self.state = "NOT DECIDABLE"
            return self.state
        self.pending = slot
        return "HALT"

    def resume(self, slot, level, decide_sha):
        if self.state:
            return self.state
        if self.pending is None:
            raise ValueError("no halt is pending")
        lvl, sha, used = self.info[self.pending]
        if slot != self.pending or level != lvl or decide_sha != sha or used or self.halts[slot] != 1:
            self.state = "NOT DECIDABLE"
            return self.state
        self.pending = None
        return "RESUME_AS_RETAKE"
'''

#: bluey's own v6 files, where this machine has them.
REAL = '/data/python/bluey-swarm-ops/packets/s4-bench-reg-v6/'
#: bluey's v10 decision file (LevelSession with halt and resume), where this machine has it.
REAL10 = os.environ.get('BENCH_DECIDE_V10', '/data/python/bluey-ox-walker-s4benchreg/diag/diag_s4_bench_decide.py')


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


def recording(shots, side, name, here, snr_db=20.0, level=-68.0, sent=True, state=None, sha=None, overruns=0,
              counted=True):
    """A finished fake recording in ``here``: the capture, the recorder's json and the player's sidecar."""
    os.makedirs(here, exist_ok=True)
    cap = os.path.join(here, name + '.cf32')
    bb.make_capture(os.path.join(shots, 'bench_885614_d23.cf32'), cap, pre=0.1, post=0.1, frac=0.37, ppm=-0.37,
                    cfo_hz=58900.0, vsg_offset_hz=58000.0, snr_db=snr_db)
    json.dump(dict(recorded_s=1.0, adc_overflows=0, lost_sample_overflows=0, sample_overruns=overruns,
                   readstream_overflows=0, overruns_counted=counted, file=name + '.cf32', center_mhz=2439.0,
                   sample_rate=bb.FS), open(cap + '.rec.json', 'w'))
    open(os.path.join(here, name + '.rec.log'), 'w').write('fake recorder\n')
    json.dump(dict(name=name, shot='bench_885614_d23', shot_sha256=sha or side['bench_shot']['sha256'],
                   vsg_center_mhz=2439.058, vsg_offset_hz=58000.0, vsg_level_dbm=level, pad_db=0.0, sent=sent,
                   state=state or ('sent' if sent is True else 'error before the send')),
              open(os.path.join(here, name + '_tx.json'), 'w'))


def halted(name, slot, action='HALT'):
    """The session-log lines of a recording halted undecided, closed."""
    return [(name, 'start', dict(slot=slot)), (name, 'recorder', {}),
            (name, 'halt', dict(slot=slot, action=action, state='NOT DECIDABLE' if action != 'HALT' else None)),
            (name, 'align', dict(skipped='not aligned: halted undecided')), (name, 'copy', dict(files={}))]


def resumed(name, slot, action='RESUME_AS_RETAKE'):
    return [(name, 'resume', dict(slot=slot, action=action))]


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


def decision_of(folder, name):
    return [e for e in bs._entries(folder) if e['name'] == name and e['step'] == 'decision'][-1]


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

        for tag, kw, want in (('level', dict(level=-67.0), 'halted, undecided'),
                              ('sha', dict(sha='f' * 64), 'halted, undecided'),
                              ('state', dict(state='error in the send: part of the shot may have gone out'), None)):
            h, d = os.path.join(tmp, 'here_' + tag), os.path.join(tmp, 'drive_' + tag)
            os.makedirs(d)
            st_, fi_ = session(h, d)[:2]
            st_('q01', 1, offset='0')
            recording(shots, side, 'q01', h, **kw)
            code, _, err = fi_('q01')
            decided = [e for e in bs._entries(d) if e['step'] == 'decision']
            halts = [e for e in bs._entries(d) if e['step'] == 'halt']
            if want:
                check(code != 0 and want in err and not decided and len(halts) == 1 and halts[0]['action'] == 'HALT'
                      and halts[0]['slot'] == 1 and halts[0]['decide_sha'] == bs.sha256_of(decide),
                      'a sidecar of another %s: halted, undecided, the halt logged (%s)' % (tag, err.strip()[-120:]))
            else:
                check(code == 0 and decided and decided[0]['action'] == 'RETAKE_SAME_LEVEL'
                      and decided[0]['inputs']['player_fault'], 'sent true with a state that is not sent: a fault')

        print('\nan UNDECIDED halt, then the owner\'s resume')
        h, d = os.path.join(tmp, 'here_halt'), os.path.join(tmp, 'drive_halt')
        os.makedirs(d)
        st_, fi_, cl_ = session(h, d)[:3]

        def resume(name, slot, dec=decide):
            bs.subprocess.Popen = FakePopen
            try:
                return run(['start', name, '--slot', str(slot), '--lap', '885614', '--offset-khz', '0', '--resume',
                            '--decide', dec, '--dir', h, '--drive', d])
            finally:
                bs.subprocess.Popen = real_popen

        def hlines():
            return bs._entries(d)
        st_('h01', 1, offset='0')
        recording(shots, side, 'h01', h)
        code, _, err = fi_('h01')
        check(code == 0 and [e['action'] for e in hlines() if e['step'] == 'decision'] == ['KEEP'],
              'h01 kept (%s)' % err.strip()[-120:])
        cl_('h01')
        code, _, err = resume('h02', 2)
        check(code != 0 and 'no halt is waiting' in err, '--resume with no halt waiting is refused')
        st_('h02', 2, offset='0')
        recording(shots, side, 'h02', h, level=-67.0)
        code, out, err = fi_('h02')
        hl = [e for e in hlines() if e['step'] == 'halt']
        check(code != 0 and 'halted, undecided (HALT)' in err and len(hl) == 1 and hl[0]['name'] == 'h02'
              and not [e for e in hlines() if e['name'] == 'h02' and e['step'] in ('tx', 'snr', 'decision')],
              'a sidecar of another level: the halt logged, no tx, snr or decision line')
        code, _, err = fi_('h02')
        check(code != 0 and 'already has a decision or a halt' in err, 'finish again after a halt is refused')
        code, _, err = run(['log', 'h02', '--event', 'fault', '--reason', 'late', '--dir', h, '--drive', d])
        check(code != 0 and 'finish has begun' in err, 'a fault after a halt is refused')
        code, _, err = st_('h03', 2, offset='0')
        check(code != 0 and 'close it first' in err, 'no start before the halted recording is on the drive')
        code, out, err = cl_('h02')
        check(code == 0 and 'halted undecided' in out and os.path.exists(os.path.join(d, 'h02.cf32'))
              and not os.path.exists(os.path.join(d, 'h02.align.json')), 'close: a halt is not aligned, it is copied')
        code, _, err = st_('h03', 2, offset='0')
        check(code != 0 and 'halted undecided' in err and '--resume' in err,
              'a start after a halt without --resume is refused, the resume rule printed')
        code, _, err = resume('h03', 3)
        check(code != 0 and 'only it may be resumed' in err and not [e for e in hlines() if e['step'] == 'resume'],
              'a resume of another slot is refused, nothing logged')
        other = os.path.join(keydir, 'other', 'diag_s4_bench_decide.py')
        os.makedirs(os.path.dirname(other))
        open(other, 'w').write(DECIDE % '-68.0' + '# another file\n')
        code, _, err = resume('h03', 2, dec=other)
        check(code != 0 and 'needs the same file' in err and not [e for e in hlines() if e['step'] == 'resume'],
              'a resume under another decision file is refused, nothing logged')
        code, out, err = resume('h03', 2)
        rs = [e for e in hlines() if e['step'] == 'resume']
        check(code == 0 and len(rs) == 1 and rs[0]['name'] == 'h02' and rs[0]['action'] == 'RESUME_AS_RETAKE'
              and rs[0]['level_dbm'] == -68.0 and '--level -68 ' in out and hlines()[-1]['step'] == 'start',
              'the resume: logged under h02 as RESUME_AS_RETAKE at the same level, then h03 started (%s)'
              % err.strip()[-150:])
        recording(shots, side, 'h03', h, snr_db=17.0)
        code, out, err = fi_('h03')
        check(code == 0 and [e for e in hlines() if e['step'] == 'decision'][-1]['action'] == 'NOT DECIDABLE',
              'a non-KEEP after the resume: NOT DECIDABLE')
        cl_('h03')
        code, out, _ = run(['verify', d])
        check(code == 0 and 'order OK' in out, 'verify: a halt, its resume and the retake in order (%s)'
              % out.strip().splitlines()[-1])
        ruled = [bs._attempt(e) for e in bs._ruled(hlines())]
        if os.path.exists(REAL10):
            mine, theirs = bs._replay(decide, ruled), bs._replay(REAL10, ruled)
            check([[s.get(k) for k in bs.STEP_KEYS] for s in mine['steps']]
                  == [[s.get(k) for k in bs.STEP_KEYS] for s in theirs['steps']],
                  'bluey\'s v10 LevelSession replays the halt session step for step: %s'
                  % [s['action'] for s in theirs['steps']])

        print('\na decision file that raises: a halt')
        h, d = os.path.join(tmp, 'here_raise'), os.path.join(tmp, 'drive_raise')
        os.makedirs(d)
        raising = os.path.join(keydir, 'raising', 'diag_s4_bench_decide.py')
        os.makedirs(os.path.dirname(raising))
        open(raising, 'w').write((DECIDE % '-68.0').replace('RAISE_OVER = 1e9', 'RAISE_OVER = 25.0'))
        bs.subprocess.Popen = FakePopen
        try:
            run(['start', 'x01', '--slot', '1', '--lap', '885614', '--offset-khz', '0', '--decide', raising,
                 '--dir', h, '--drive', d])
        finally:
            bs.subprocess.Popen = real_popen
        recording(shots, side, 'x01', h, snr_db=30.0)
        code, out, err = run(['finish', 'x01', '--shots', shots, '--decide', raising, '--dir', h, '--drive', d])
        hl = [e for e in bs._entries(d) if e['step'] == 'halt']
        check(code != 0 and len(hl) == 1 and 'the decision file raised' in hl[0]['reason']
              and 'a decision file that raises' in hl[0]['reason'] and 'snr:' in out
              and not [e for e in bs._entries(d) if e['step'] == 'decision'],
              'the decision file raised on the attempt: a halt, after the snr line, no decision')

        print('\nthe amendment of 2026-10-26: overruns, the snr spread, and amend')
        h, d = os.path.join(tmp, 'here_amend'), os.path.join(tmp, 'drive_amend')
        os.makedirs(d)
        st_, fi_, cl_ = session(h, d)[:3]
        amendment = os.path.join(keydir, 'amendment.md')
        open(amendment, 'w').write('the amendment\n')

        def amend(name, amd=amendment):
            return run(['amend', name, '--decide', decide, '--amendment', amd, '--reason', 'test', '--dir', h,
                        '--drive', d])

        def alines(dd=None):
            return bs._entries(dd or d)
        st_('a01', 1)
        check('load_avg' in alines()[-1] and len(alines()[-1]['load_avg']) == 3, 'start logs the load average')
        recording(shots, side, 'a01', h, overruns=3)
        code, out, err = fi_('a01')
        rl = [e for e in alines() if e['name'] == 'a01' and e['step'] == 'recorder'][0]
        sl = [e for e in alines() if e['name'] == 'a01' and e['step'] == 'snr']
        check(code == 0 and not rl['fault'] and rl['sample_overruns'] == 3 and rl['overruns_counted'] is True
              and sl and sl[0]['snr_db_sem'] is not None and decision_of(d, 'a01')['action'] == 'KEEP',
              'Sample Overruns with a good sem: logged, no fault, KEEP (%s)' % err[-120:])
        cl_('a01')
        st_('a02', 2)
        recording(shots, side, 'a02', h, counted=False)
        fi_('a02')
        rl = [e for e in alines() if e['name'] == 'a02' and e['step'] == 'recorder'][0]
        check('could not count' in str(rl['fault']) and decision_of(d, 'a02')['action'] == 'RETAKE_SAME_LEVEL'
              and decision_of(d, 'a02')['inputs']['recorder_fault'],
              'a recorder that could not count overruns is a recorder fault: RETAKE_SAME_LEVEL')

        h, d = os.path.join(tmp, 'here_amend2'), os.path.join(tmp, 'drive_amend2')
        os.makedirs(d)
        st_, fi_, cl_ = session(h, d)[:3]
        st_('m01', 1)
        recording(shots, side, 'm01', h, snr_db=22.0)
        fi_('m01')
        cl_('m01')
        st_('m01b', 1)
        recording(shots, side, 'm01b', h, level=decision_of(d, 'm01')['level_next'])
        fi_('m01b')
        cl_('m01b')
        st_('m02', 2)
        recording(shots, side, 'm02', h, snr_db=17.0, level=decision_of(d, 'm01b')['level_next'])
        fi_('m02')
        d2 = decision_of(d, 'm02')
        check([decision_of(d, n)['action'] for n in ('m01', 'm01b', 'm02')] == ['DISCARD_STEP', 'KEEP', 'DISCARD_STEP']
              and d2['relevels'] == 2, 'r01/r01b/r02 as on the bench: discard, keep, discard (%s)'
              % [(decision_of(d, n)['action'], decision_of(d, n)['level_next']) for n in ('m01', 'm01b', 'm02')])
        code, _, err = amend('m02')
        check(code != 0 and 'close it first' in err and 'amend' not in [e['step'] for e in alines(d)],
              'amend before the copy is refused')
        cl_('m02')
        code, _, err = amend('m01b')
        check(code != 0 and 'only the last recording' in err, 'amend of an earlier recording is refused')
        code, _, err = amend('m02', amd=os.path.join(keydir, 'missing.md'))
        check(code != 0 and 'no amendment file' in err, 'amend with no amendment file is refused')
        lvl = decision_of(d, 'm01b')['level_next']
        code, out, err = amend('m02')
        am = [e for e in alines(d) if e['step'] == 'amend']
        check(code == 0 and len(am) == 1 and am[0]['action'] == 'RETAKE_SAME_LEVEL' and am[0]['level_next'] == lvl
              and am[0]['relevels'] == 1 and am[0]['superseded']['action'] == 'DISCARD_STEP'
              and am[0]['amendment']['sha256'] == bs.sha256_of(amendment) and am[0]['inputs'] == bs.AMEND_INPUTS,
              'amend: m02 refused by the spread rule, RETAKE_SAME_LEVEL at %g dBm, re-levels back to 1 (%s)' % (lvl, err[-160:]))
        code, _, err = amend('m02')
        check(code != 0 and 'already amended' in err, 'a second amend is refused')
        code, _, err = st_('m03', 3)
        check(code != 0 and 'left for a retake' in err, 'after the amend, slot 2 is left for its retake')
        code, out, err = st_('m02b', 2)
        check(code == 0 and '--level %g ' % lvl in out, 'the retake starts at the level before m02 (%g dBm) %s'
              % (lvl, err[-160:]))
        recording(shots, side, 'm02b', h, level=lvl)
        fi_('m02b')
        cl_('m02b')
        check(decision_of(d, 'm02b')['action'] == 'KEEP', 'the retake kept')
        code, out, _ = run(['verify', d, '--decide', decide])
        check(code == 0 and 'order OK' in out and 'chain OK' in out and 'replay OK' in out,
              'verify --decide: the amended session, chain, order and replay OK')

        def edited(src_h, src_d, edit, tag):
            """Copies of both session logs with one line edited and the chain re-made, as a careful forger would."""
            hh, dd = os.path.join(tmp, 'eh_' + tag), os.path.join(tmp, 'ed_' + tag)
            for src, dst in ((src_h, hh), (src_d, dd)):
                os.makedirs(dst)
                prev = None
                with open(os.path.join(dst, bs.LOG), 'w') as f:
                    for e in bs._entries(src):
                        e = edit(dict(e))
                        e['prev'] = bs._line_sha(prev)
                        prev = json.dumps(e)
                        f.write(prev + '\n')
            return hh, dd

        def finish_attempt(hh, dd):
            return run(['finish', 'm02b', '--shots', shots, '--decide', decide, '--dir', hh, '--drive', dd])
        for tag, edit, why in (
                ('amend_level', lambda e: dict(e, level_next=-60.0) if e['step'] == 'amend' else e,
                 'an amend line whose level was edited'),
                ('orig_relevels', lambda e: dict(e, relevels=1) if e['step'] == 'decision' and e['name'] == 'm02'
                 else e, 'the superseded decision edited'),
                ('amend_inputs', lambda e: dict(e, inputs=dict(bs.AMEND_INPUTS, snr_refused=False,
                                                               player_fault=True)) if e['step'] == 'amend' else e,
                 'an amend line with other inputs')):
            hh, dd = edited(h, d, edit, tag)
            at, broken = bs.check_chain(os.path.join(dd, bs.LOG))
            order = bs.check_order(bs._entries(dd))
            code, _, err = run(['amend', 'm02', '--decide', decide, '--amendment', amendment, '--reason', 'x',
                                '--dir', hh, '--drive', dd])
            refused = []
            try:
                bs._session(decide, bs._entries(dd))
            except SystemExit as e:
                refused.append(str(e))
            check(broken is None and (refused or order), '%s (chain re-made): refused by the replay or the order: %s'
                  % (why, (refused + order)[:1]))
            open(os.path.join(dd, bs.SUMS), 'w').close()
            code, out, _ = run(['verify', dd, '--decide', decide])
            check(code != 0 and ('replay BROKEN' in out or 'order BROKEN' in out),
                  '%s: verify --decide fails it' % why)

        print('\nthe snr spread')
        base = dict(problem=None, snr_db=20.0, snr_db_min=19.5, snr_db_max=20.5, snr_db_sem=0.01)
        check(bs.spread_refusal(base) == base, 'sem 0.01 dB: the snr unchanged')
        check(bs.spread_refusal(dict(base, snr_db_sem=0.10)) == dict(base, snr_db_sem=0.10), 'sem 0.10 dB: kept')
        for sem in (0.11, 2.5, None, float('nan')):
            r = bs.spread_refusal(dict(base, snr_db_sem=sem))
            check(r['problem'] and r.get('spread_problem') and 'snr_db' not in r and r['refused_snr_db'] == 20.0
                  and r['snr_db_sem'] == (sem if sem is not None and sem == sem else None),
                  'sem %r: refused, the numbers moved aside, NaN logged as None' % sem)
        r = bs.spread_refusal(dict(base, problem='the shot was not found'))
        check(r['problem'] == 'the shot was not found' and 'spread_problem' not in r, 'an snr already refused: as it was')
        h, d = os.path.join(tmp, 'here_spread'), os.path.join(tmp, 'drive_spread')
        os.makedirs(d)
        st_, fi_, cl_ = session(h, d)[:3]
        st_('s01', 1)
        recording(shots, side, 's01', h)
        real_snr = bs.bb.snr
        bs.bb.snr = lambda *a_: dict(real_snr(*a_), snr_db_sem=2.508)
        try:
            code, out, err = fi_('s01')
        finally:
            bs.bb.snr = real_snr
        sl = [e for e in bs._entries(d) if e['step'] == 'snr'][0]
        dec = decision_of(d, 's01')
        check(code == 0 and sl['problem'] and sl['snr_db'] is None and sl['refused_snr_db'] and sl['snr_db_sem'] == 2.508
              and dec['action'] == 'RETAKE_SAME_LEVEL' and dec['inputs']['snr_refused'] and dec['inputs']['median'] is None
              and not bs.check_order(bs._entries(d)),
              'finish with a spread of 2.5 dB: the snr refused, RETAKE_SAME_LEVEL, the order holds (%s)' % err[-160:])

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
        amd = dict(slot=2, action='RETAKE_SAME_LEVEL', inputs=bs.AMEND_INPUTS, amendment=dict(sha256='a' * 64))
        disc = events('b', 2, 'DISCARD_STEP', median=17.0, lo=16.8, hi=17.4)
        for what, lns, want in (
                ('an amend of a KEEP', events('b', 2, 'KEEP') + [('b', 'amend', amd)], 'amended after a KEEP'),
                ('an amend before the copy', disc[:-1] + [('b', 'amend', amd)], 'amended before it was copied'),
                ('a second amend', disc + [('b', 'amend', amd), ('b', 'amend', amd)], 'amended twice'),
                ('an amend after the next start', disc + [('c', 'start', dict(slot=2)), ('b', 'amend', amd)],
                 'amended after c was started'),
                ('an amend with snr numbers', disc + [('b', 'amend', dict(amd, inputs=dict(bs.AMEND_INPUTS,
                                                                                       median=17.0)))], 'amended with inputs'),
                ('an amend with no amendment sha', disc + [('b', 'amend', dict(amd, amendment=None))],
                 'no amendment sha256'),
                ('an amend of a halt', halted('b', 2) + [('b', 'amend', amd)], 'amended with no decision')):
            bad = order_of(tmp, lns)
            check(any(want in b for b in bad), '%s: %s' % (what, bad[:1]))
        bad = order_of(tmp, disc + [('b', 'amend', amd), ('c', 'start', dict(slot=3))])
        check(any('left for a retake' in b for b in bad), 'after an amend to a retake, another slot cannot start')
        bad = order_of(tmp, disc + [('b', 'amend', amd)] + events('b2', 2, 'KEEP'))
        check(not bad, 'a discard amended to a retake, then the retake kept: order OK %s' % bad)
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
                 + [('a', 'align', dict(skipped='x', exit=0))], 'aligned after'),
                ('a start after a halt with no resume', halted('a', 1) + events('b', 1, 'KEEP')[:1], 'no resume'),
                ('a resume of another slot', halted('a', 1) + resumed('a', 2), 'slot 2 resumed, slot 1 halted'),
                ('a resume with no halt', events('a', 1, 'KEEP') + resumed('a', 1), 'no halt waiting'),
                ('a decision after a halt', halted('a', 1)[:3] + events('a', 1, 'KEEP')[4:5], 'after its halt'),
                ('a halt then an alignment', halted('a', 1)[:3] + [('a', 'align', dict(exit=0))], 'no decision'),
                ('a resume before the halted recording is copied', halted('a', 1)[:4] + resumed('a', 1),
                 'before it was copied'),
                ('a start after a resume ruled NOT DECIDABLE', halted('a', 1) + resumed('a', 1, 'NOT DECIDABLE')
                 + events('b', 1, 'KEEP')[:1], 'NOT DECIDABLE')):
            bad = order_of(tmp, lns)
            check(any(want in b for b in bad), '%s: %s' % (what, bad[0] if bad else 'not caught'))
        crash = events('a', 1, 'KEEP')
        for what, lns in (('a discard, not aligned, then its retake kept and aligned',
                           events('a', 1, 'DISCARD_STEP', median=17.0) + events('b', 1, 'KEEP')),
                          ('a finish retried after a crash before its decision', crash[:4] + crash[1:]),
                          ('a halt, its resume, then the slot kept', halted('a', 1) + resumed('a', 1)
                           + events('b', 1, 'KEEP'))):
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
