#!/usr/bin/env python3
"""Hold the f-offset bench's decision and allowed-keys files to the registration's rules, with no radio.

    python -B scripts/test_bt_foffset_decide.py

* **No re-level**: KEEP at any SNR (12 dB, 28 dB, a 6 dB spread over the channels) with the level at -69 dBm, and the
  level, ``relevels`` and ``capped`` unchanged through a whole session of keeps, retakes and absences.
* **Each fault path** (recorder, player, a fault logged before finish, snr refused) is ``RETAKE_SAME_LEVEL`` the first
  time in a slot and ``FAULT_ABSENT`` the second: one retake per slot, never NOT DECIDABLE for a fault; a retake that
  keeps closes the slot as KEEP; a closed slot (kept or absent) cannot be decided again.
* **Caller errors** raise and count no attempt: a fault with snr numbers, no fault without all three, a number that is
  NaN or a bool, a slot outside 0..14.
* **Halt and resume**: a resume at the same slot, level and sha256 is a retake, after which a fault leaves the slot
  absent; another slot, level or sha256, a second halt, a halt after the slot's retake, or a decision for another slot
  while a halt waits, is NOT DECIDABLE.
* **Through the session**: ``bt_bench_session``'s own replay (``python -I -B``, the file's bytes) gives the same steps,
  first level -69 and the file's sha256; ``check_order`` passes a log with a retake that keeps, a slot left absent and
  the next slot started after it.
* **Allowed keys**: only the operator view comes out (no carrier key, no residual, no reason), ``accepted`` from
  ``problem``, and the script run in ``python -I`` writes the same; the session's own carrier-key guard finds nothing.
"""
import json
import math
import os
import subprocess
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from scripts import bt_bench_session as bs  # noqa: E402

DECIDE = os.path.join(ROOT, 'scripts', 'bt_foffset_decide.py')
KEYS = os.path.join(ROOT, 'scripts', 'bt_foffset_allowed_keys.py')
FAULTS = ('recorder_fault', 'player_fault', 'exit_fault', 'snr_refused')
GOOD = dict(median=20.4, lo=19.9, hi=20.9)
failures = []


def check(ok, what):
    print('  %s %s' % ('ok  ' if ok else 'FAIL', what), flush=True)
    if not ok:
        failures.append(what)


def load(path):
    m = types.ModuleType(os.path.splitext(os.path.basename(path))[0])
    exec(compile(open(path, 'rb').read(), path, 'exec'), m.__dict__)
    return m


def raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


def unchanged(s):
    return s.level == -69.0 and s.relevels == 0 and s.capped is False


def main():
    m = load(DECIDE)

    print('no re-level')
    s = m.LevelSession()
    check(m.FIRST_COMMAND_DBM == -69.0 and unchanged(s) and s.state is None, 'starts at -69 dBm, nothing re-levelled')
    acts = [s.decide(1, median=12.0, lo=11.0, hi=13.0), s.decide(2, median=28.0, lo=27.5, hi=28.5),
            s.decide(3, median=20.0, lo=17.0, hi=23.0)]
    check(acts == ['KEEP'] * 3 and unchanged(s), '12 dB, 28 dB and a 6 dB channel spread are each KEEP, level held')
    s.decide(4, snr_refused=True)
    s.decide(4, recorder_fault=True)
    s.decide(5, player_fault=True)
    s.decide(5, **GOOD)
    check(unchanged(s) and s.state is None and s.attempts == 7,
          'through retakes and an absence: level %g, relevels %d, capped %s, attempts %d'
          % (s.level, s.relevels, s.capped, s.attempts))

    print('\neach fault path: one retake per slot')
    for f in FAULTS:
        s = m.LevelSession()
        a1 = s.decide(7, **{f: True})
        a2 = s.decide(7, **{f: True})
        check(a1 == 'RETAKE_SAME_LEVEL' and a2 == 'FAULT_ABSENT' and s.state is None and unchanged(s),
              '%s: %s, then %s; the session goes on' % (f, a1, a2))
        check(raises(lambda: s.decide(7, **GOOD)) and s.decide(8, **GOOD) == 'KEEP',
              '%s: the absent slot cannot be decided again; the next slot keeps' % f)
    s = m.LevelSession()
    a = [s.decide(2, exit_fault=True), s.decide(2, snr_refused=True)]
    check(a == ['RETAKE_SAME_LEVEL', 'FAULT_ABSENT'], 'two different faults in one slot: retake, then absent')
    s = m.LevelSession()
    a = [s.decide(3, recorder_fault=True, player_fault=True), s.decide(3, **GOOD)]
    check(a == ['RETAKE_SAME_LEVEL', 'KEEP'] and s.closed == {3: 'KEEP'}, 'a fault then a good retake: KEEP')
    check(raises(lambda: s.decide(3, **GOOD)), 'a kept slot cannot be decided again')

    print('\ncaller errors count no attempt')
    s = m.LevelSession()
    bad = [lambda: s.decide(1, snr_refused=True, median=20.0, lo=None, hi=None),
           lambda: s.decide(1, median=20.0, lo=19.0, hi=None),
           lambda: s.decide(1),
           lambda: s.decide(1, median=float('nan'), lo=19.0, hi=21.0),
           lambda: s.decide(1, median=True, lo=19.0, hi=21.0),
           lambda: s.decide(15, **GOOD),
           lambda: s.decide(-1, **GOOD),
           lambda: s.decide(True, **GOOD)]
    check(all(raises(b) for b in bad) and s.attempts == 0 and s.state is None,
          'fault with numbers, missing or NaN or bool numbers, slot 15, -1 or True: ValueError, 0 attempts')
    check(s.decide(0, **GOOD) == 'KEEP' and s.decide(14, **GOOD) == 'KEEP', 'slots 0 (the start play) and 14 accepted')

    print('\nhalt and resume')
    sha = 'a' * 64
    s = m.LevelSession()
    a = [s.halt(4, -69.0, sha), s.resume(4, -69.0, sha), s.decide(4, recorder_fault=True)]
    check(a == ['HALT', 'RESUME_AS_RETAKE', 'FAULT_ABSENT'] and s.state is None,
          'halt, resume at the same slot/level/sha, then a fault: %s' % a)
    s = m.LevelSession()
    a = [s.halt(4, -69.0, sha), s.resume(4, -69.0, sha), s.decide(4, **GOOD)]
    check(a == ['HALT', 'RESUME_AS_RETAKE', 'KEEP'], 'halt, resume, keep')
    for what, fn in (('another slot', lambda s: s.resume(5, -69.0, sha)),
                     ('another level', lambda s: s.resume(4, -68.0, sha)),
                     ('another sha256', lambda s: s.resume(4, -69.0, 'b' * 64)),
                     ('a decision for another slot', lambda s: s.decide(5, **GOOD))):
        s = m.LevelSession()
        s.halt(4, -69.0, sha)
        check(fn(s) == 'NOT DECIDABLE' and s.state == 'NOT DECIDABLE'
              and s.decide(6, **GOOD) == 'NOT DECIDABLE', 'after a halt, %s: NOT DECIDABLE, and it stays' % what)
    s = m.LevelSession()
    s.decide(4, player_fault=True)
    check(s.halt(4, -69.0, sha) == 'NOT DECIDABLE', 'a halt in a slot that already had its retake: NOT DECIDABLE')
    s = m.LevelSession()
    s.halt(4, -69.0, sha)
    check(raises(lambda: s.decide(4, **GOOD)), 'a decision in the halted slot before the resume: ValueError')

    print('\nthrough the session\'s replay')
    attempts = [dict(slot=0, inputs=dict(recorder_fault=False, player_fault=False, exit_fault=False,
                                         snr_refused=False, **GOOD)),
                dict(slot=1, inputs=bs.AMEND_INPUTS),
                dict(slot=1, inputs=dict(bs.AMEND_INPUTS, snr_refused=False, recorder_fault=True)),
                dict(slot=2, inputs=dict(recorder_fault=False, player_fault=False, exit_fault=False,
                                         snr_refused=False, median=13.0, lo=12.0, hi=14.5)),
                dict(kind='halt', slot=3, level=-69.0, decide_sha=bs.sha256_of(DECIDE)),
                dict(kind='resume', slot=3, level=-69.0, decide_sha=bs.sha256_of(DECIDE)),
                dict(slot=3, inputs=dict(bs.AMEND_INPUTS, snr_refused=False, exit_fault=True))]
    got, why = bs._try_replay(DECIDE, attempts)
    check(why is None and [x['action'] for x in got['steps']] == [
        'KEEP', 'RETAKE_SAME_LEVEL', 'FAULT_ABSENT', 'KEEP', 'HALT', 'RESUME_AS_RETAKE', 'FAULT_ABSENT']
        and got['first_level'] == -69.0 and got['level'] == -69.0 and got['state'] is None
        and all(x['level_used'] == x['level_next'] == -69.0 and x['relevels'] == 0 and x['capped'] is False
                for x in got['steps'])
        and got['sha256'] == bs.sha256_of(DECIDE),
        'python -I -B replay: %s' % (why or [x['action'] for x in got['steps']]))

    print('\ncheck_order on a session log')
    def rec(name, slot, action, fault=None):
        snr = (dict(problem='refused', snr_db_sem=None) if fault == 'snr_refused' else
               dict(problem=None, snr_db=20.4, snr_db_min=19.9, snr_db_max=20.9) if fault is None else None)
        out = [(name, 'start', dict(slot=slot)), (name, 'recorder', {}), (name, 'tx', {})]
        if snr:
            out.append((name, 'snr', snr))
        inputs = dict(median=None, lo=None, hi=None, snr_refused=fault == 'snr_refused', exit_fault=False)
        if fault is None:
            inputs.update(median=20.4, lo=19.9, hi=20.9)
        out.append((name, 'decision', dict(slot=slot, action=action, state=None, inputs=inputs)))
        out.append((name, 'align', dict(exit=0) if action == 'KEEP' else dict(skipped='not aligned')))
        return out + [(name, 'copy', dict(files={}))]
    with tempfile.TemporaryDirectory() as tmp:
        lines = (rec('s00', 0, 'KEEP') + rec('r01', 1, 'RETAKE_SAME_LEVEL', 'snr_refused') + rec('r01b', 1, 'KEEP')
                 + rec('r02', 2, 'RETAKE_SAME_LEVEL', 'snr_refused') + rec('r02b', 2, 'FAULT_ABSENT', 'snr_refused')
                 + rec('r03', 3, 'KEEP'))
        for name, step, kw in lines:
            bs._log([tmp], name, step, **kw)
        bad = bs.check_order(bs._entries(tmp))
    check(bad == [], 'a retake that keeps, a slot left absent, the next slot after it: %s' % (bad or 'no breach'))

    print('\nallowed keys')
    k = load(KEYS)
    full = dict(problem=None, shot='bench_66C0A6_d23', capture='r01.cf32', capture_samples=420000000, ppm=-0.37,
                delay_at_0=12345.6, f_located_hz=1.0, cfo_instrument_hz=2.0, cfo_instrument_spread_hz=3.0,
                reference_error_hz=4.0, vsg_offset_hz_set=5.0, jitter_samples=0.4, step_samples=0.1,
                bend_samples=0.01, n_off_line=0, locate_score=0.8, n_bursts=10, n_found=10, rows={})
    v = k.allowed_view(full)
    check(set(v) == {'accepted', 'shot', 'capture', 'capture_samples', 'ppm', 'delay_at_0'} and v['accepted'] is True
          and not bs._carrier_keys(v), 'an accepted report: only the operator view, %s' % sorted(v))
    r = k.allowed_view(dict(problem='the shot was not found: 125 kHz', shot='x', capture='y', capture_samples=1))
    check(r == dict(shot='x', capture='y', capture_samples=1, accepted=False), 'a refusal: accepted false, no reason')
    check(k.allowed_view({})['accepted'] is False, 'no problem key at all: not accepted')
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = os.path.join(tmp, 'a.align.json'), os.path.join(tmp, 'a.align_allowed.json')
        json.dump(full, open(src, 'w'))
        p = subprocess.run([sys.executable, '-I', KEYS, src, dst], capture_output=True, text=True)
        check(p.returncode == 0 and json.load(open(dst)) == v, 'run in python -I, it writes the same view')

    print('RESULT: PASS' if not failures else 'RESULT: FAIL (%d)' % len(failures))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
