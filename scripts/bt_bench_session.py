#!/usr/bin/env python3
"""One bench recording at a time, in the order bluey-ox-walker's bench registration fixes; its level rule is run, not re-made.

    python scripts/bt_bench_session.py start NAME --slot 3 --lap 885614 --offset-khz -58 --decide DECIDE --dir LOCAL --drive DRIVE
    ssh worklaptop1-fast '... bt_bench_play.py ... --go ... --name NAME ...'          # printed by start; run by hand
    python scripts/bt_bench_session.py log NAME --event fault --reason 'player exit 1' --dir LOCAL --drive DRIVE  # if so
    python scripts/bt_bench_session.py finish NAME --decide DECIDE --dir LOCAL --drive DRIVE
    python scripts/bt_bench_session.py close NAME --allowed-keys KEYS --dir LOCAL --drive DRIVE
    python scripts/bt_bench_session.py verify DRIVE_FOLDER                             # SHA256SUMS.txt, read from the disk

``DECIDE`` is the registration's ``diag_s4_bench_decide.py`` and ``KEYS`` its ``s4_bench_allowed_keys.py``, both run by
path in a separate ``python -I`` (``-B`` for the decision file: no bytecode cache, which a rewrite within the same
second would leave stale, and none written beside it), their sha256 and commit logged.

**What it does not do.** It never transmits: ``start`` prints the player's command and a person (or the Claude session,
under its own narrow permission) runs it, so every transmission stays a separate, visible command. It makes no rule of
its own: keep, retake, discard and the next VSG level are the registration's ``LevelSession``, replayed from the session
log over every earlier attempt, so the operator neither types a level nor chooses an outcome.

**``start``** refuses a name already used here or on the drive, a recording while the one before has no decision, any
recording once the session is NOT DECIDABLE, a slot already kept, and a slot other than the one a retake or discard
left open. The level is the session's (``LevelSession`` replayed), never typed. It logs the cell and starts
``bt_bench.py record`` (``SECONDS`` s, the BB60D) in the background with its output in ``NAME.rec.log``.

**``log``** records an instrument fault before the decision (the player's non-zero exit, which only its console shows)
or a note. A fault is refused once the recording has a decision.

**``finish``**, in this order, each step a line in ``session.jsonl`` (here and on the drive, flushed to the disk before
the next step):
1. waits for the recorder, and reads ``lost_sample_overflows`` and ``adc_overflows`` (a recorder that wrote no record,
   or any loss or overflow, is a recorder fault);
2. fetches the player's sidecar ``NAME_tx.json`` (``--player-host``, ``--player-tx-dir``); missing, or ``sent`` not
   true, is a player fault; a sidecar of another shot or level than the session's is refused, undecided;
3. only with no fault: ``bt_bench.snr``, ``NAME.snr.json``, its summary printed;
4. the decision: ``LevelSession.decide`` over every earlier attempt and this one (an earlier action that does not
   replay the same refuses everything), logged with its inputs, the level used and the next.
Nothing aligns here, so no aligner output exists when the decision is made.

**``close``** needs that decision. A KEEP is aligned (``bt_bench.py align`` with ``--tx`` and ``--rec``, its whole
console to ``NAME.align.log``, which nothing here prints: on accept it ends with the carrier values the registration
keeps from every reader until its stage 1) and the allowed-keys script writes ``NAME.align_allowed.json``, printed.
Any other outcome is not aligned. Then every file of the recording is copied to the drive (never over an existing one),
each written to the disk and its cached pages dropped before it is hashed, so the hash is of what the disk holds, not
of the page cache; only when every file matches its source are the large ``.cf32`` files here deleted (the recording
and, for a KEEP, the aligner's crop); nothing on the drive is ever deleted. The hashes go into ``SHA256SUMS.txt``.

Every line of ``session.jsonl`` carries ``prev``, the sha256 of the line before it, and is fsynced before the next
step: the order of measurements and decisions, against the registration's freezes, is checkable afterwards.

**``verify``** re-checks a folder's ``SHA256SUMS.txt`` the same way, from the disk, and its session log: the chain,
then the order the registration asserts (``check_order``: an alignment only after that recording's KEEP, one KEEP per
slot, no slot recorded again once aligned, a fault or snr never after the decision, one decision per recording).
"""
import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import bt_bench as bb  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
SECONDS = 30.0
#: The recorder's longest run past its seconds before ``finish`` gives up waiting.
WAIT_MARGIN_S = 60.0
LOG = 'session.jsonl'
SUMS = 'SHA256SUMS.txt'
#: ``prev`` of a session log's first line.
GENESIS = '0' * 64
#: What ``log`` records: a fault only the operator's console shows, before the decision, or a note.
EVENTS = ('fault', 'note')
#: ``LevelSession``'s outcomes after which the same slot is recorded again.
AGAIN = ('RETAKE_SAME_LEVEL', 'DISCARD_STEP')
#: Run in ``python -I`` with the registration's decision file: replays the attempts read from stdin.
REPLAY = '''import importlib.util, json, sys
spec = importlib.util.spec_from_file_location('s4_bench_decide', sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
s = m.LevelSession()
out = []
for a in json.load(sys.stdin):
    before = s.level
    out.append(dict(action=s.decide(a['slot'], **a['inputs']), level_used=before, level_next=s.level,
                    relevels=s.relevels, capped=s.capped, state=s.state))
print(json.dumps(dict(first_level=m.FIRST_COMMAND_DBM, level=s.level, state=s.state, steps=out)))
'''
PLAYER = ("ssh {host} 'cd /data/python/SDR && timeout -k 30 60 ~/miniconda3/envs/gnu/bin/python "
          "scripts/bt_bench_play.py ~/rg/bench/shots/{shot}.cf32 --go --pad-db 0 --level {level:g} "
          "--vsg-offset-khz {offset:g} --name {name} --out ~/{txdir}'")


def _files(name):
    """Every file of a recording, as (path here, path on the drive), relative to the two folders."""
    return [name + '.cf32', name + '.cf32.rec.json', name + '.rec.log', name + '_tx.json', name + '.snr.json',
            name + '.align.json', name + '.align.log', name + '.align_allowed.json',
            os.path.join('align', name + '.cf32'), os.path.join('align', name + '.json')]


def _last_line(path):
    """The last line of ``path`` without its newline, or None for a missing or empty file."""
    if not os.path.exists(path):
        return None
    lines = open(path, 'rb').read().decode().splitlines()
    return lines[-1] if lines else None


def _line_sha(line):
    return hashlib.sha256(line.encode()).hexdigest() if line is not None else GENESIS


def _log(dirs, name, step, **kw):
    """One line in ``session.jsonl`` in each of ``dirs``, written through to the disk. Each line carries ``prev``, the
    sha256 of the line before it in that file (``GENESIS`` for the first), so a line edited, dropped or moved later
    breaks the chain ``verify`` checks; the two files' lines differ only in ``prev``."""
    entry = dict(time=time.strftime('%Y-%m-%dT%H:%M:%S%z'), unix=time.time(), name=name, step=step, **kw)
    for d in dirs:
        path = os.path.join(d, LOG)
        line = json.dumps(dict(entry, prev=_line_sha(_last_line(path))))
        with open(path, 'a') as f:
            f.write(line + '\n')
            f.flush()
            os.fsync(f.fileno())


def check_chain(path):
    """(lines, None) when every line of a session log names the sha256 of the line before it, else (line number,
    why)."""
    prev = None
    n = 0
    for n, line in enumerate(open(path, 'rb').read().decode().splitlines(), 1):
        try:
            got = json.loads(line).get('prev')
        except ValueError:
            return n, 'not JSON'
        if got != _line_sha(prev):
            return n, 'prev %s does not match the line before (%s)' % (str(got)[:16], _line_sha(prev)[:16])
        prev = line
    return n, None


def check_order(entries):
    """What the registration asserts of a session log, as a list of breaches (empty when it holds): per recording, the
    start first, a fault only before the decision, the snr before it, at most one decision; an alignment only after a
    KEEP decision of that recording; one KEEP per slot; no recording of a slot started after one of its recordings was
    aligned."""
    bad, slot_of, decided, kept, aligned_slots = [], {}, {}, {}, set()
    for n, e in enumerate(entries, 1):
        name, step = e.get('name'), e.get('step')
        if step != 'start' and name not in slot_of:
            bad.append('line %d: %s %s before its start' % (n, name, step))
            continue
        if step == 'start':
            if name in slot_of:
                bad.append('line %d: %s started twice' % (n, name))
            slot_of[name] = e.get('slot')
            if e.get('slot') in aligned_slots:
                bad.append('line %d: %s starts slot %s after a recording of it was aligned' % (n, name, e.get('slot')))
        elif step in ('fault', 'snr') and name in decided:
            bad.append('line %d: %s %s after its decision' % (n, name, step))
        elif step == 'decision':
            if name in decided:
                bad.append('line %d: %s decided twice' % (n, name))
            decided[name] = e.get('action')
            if e.get('action') == 'KEEP':
                if slot_of[name] in kept:
                    bad.append('line %d: a second KEEP in slot %s (%s, after %s)' % (n, slot_of[name], name,
                                                                                    kept[slot_of[name]]))
                kept[slot_of[name]] = name
        elif step == 'align' and 'skipped' not in e:
            if decided.get(name) != 'KEEP':
                bad.append('line %d: %s aligned %s' % (n, name, 'with no decision before it' if name not in decided
                                                       else 'after a %s decision' % decided[name]))
            aligned_slots.add(slot_of[name])
    return bad


def sha256_from_disk(path):
    """The file's sha256 as the disk holds it: written through, its clean cached pages dropped, then read."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        h = hashlib.sha256()
        while True:
            b = os.read(fd, 8 << 20)
            if not b:
                break
            h.update(b)
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        return h.hexdigest()
    finally:
        os.close(fd)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(8 << 20), b''):
            h.update(b)
    return h.hexdigest()


def _taken(name, here, drive):
    return [p for d in (here, drive) for p in (os.path.join(d, f) for f in _files(name)) if os.path.exists(p)]


def _entries(drive):
    path = os.path.join(drive, LOG)
    return [json.loads(ln) for ln in open(path).read().splitlines()] if os.path.exists(path) else []


def _decisions(entries):
    return [e for e in entries if e.get('step') == 'decision']


def _replay(decide, attempts):
    """``LevelSession`` over ``attempts`` ([{slot, inputs}], in order), in a separate ``python -I``."""
    r = subprocess.run([PY, '-I', '-B', '-c', REPLAY, decide], input=json.dumps(attempts), capture_output=True, text=True)
    if r.returncode:
        sys.exit('the decision file failed: ' + r.stderr.strip()[-400:])
    return json.loads(r.stdout)


def _session(decide, entries):
    """The session replayed: every logged decision must come out the same, else nothing goes on."""
    done = _decisions(entries)
    got = _replay(decide, [dict(slot=d['slot'], inputs=d['inputs']) for d in done])
    for d, g in zip(done, got['steps']):
        if (d['action'], d['level_used'], d['level_next']) != (g['action'], g['level_used'], g['level_next']):
            sys.exit('refused: %s was logged %s at %g dBm, the decision file now gives %s at %g: the log or the code '
                     'changed' % (d['name'], d['action'], d['level_used'], g['action'], g['level_used']))
    return got


def _code_id(path):
    return dict(path=os.path.abspath(path), sha256=sha256_of(path), commit=_commit(os.path.dirname(os.path.abspath(path))))


def cmd_start(a):
    os.makedirs(a.dir, exist_ok=True)
    taken = _taken(a.name, a.dir, a.drive)
    if taken:
        sys.exit('refused: %s already exists (a name is used once)' % taken[0])
    entries = _entries(a.drive)
    decided = {e['name'] for e in _decisions(entries)}
    open_ = [e['name'] for e in entries if e.get('step') == 'start' and e['name'] not in decided]
    if open_:
        sys.exit('refused: %s has no decision yet (finish it first)' % open_[-1])
    ses = _session(a.decide, entries)
    if ses['state'] == 'NOT DECIDABLE':
        sys.exit('refused: the session is NOT DECIDABLE; no more recordings')
    done = _decisions(entries)
    if any(d['slot'] == a.slot and d['action'] == 'KEEP' for d in done):
        sys.exit('refused: slot %d is already kept' % a.slot)
    if done and done[-1]['action'] in AGAIN and done[-1]['slot'] != a.slot:
        sys.exit('refused: slot %d was left for a retake (%s): record it again first' % (done[-1]['slot'],
                                                                                         done[-1]['action']))
    level = ses['level']
    cap = os.path.join(a.dir, a.name + '.cf32')
    shot = bb.shot_name(a.lap)
    _log([a.dir, a.drive], a.name, 'start', slot=a.slot, lap=a.lap, offset_khz=a.offset_khz, level_dbm=level,
         shot=shot, seconds=a.seconds, decide=_code_id(a.decide))
    with open(os.path.join(a.dir, a.name + '.rec.log'), 'w') as out:
        p = subprocess.Popen([PY, os.path.join(ROOT, 'scripts', 'bt_bench.py'), 'record', cap, '--seconds',
                              '%g' % a.seconds], stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    with open(os.path.join(a.dir, a.name + '.rec.pid'), 'w') as f:
        f.write(str(p.pid))
    print('slot %d at %g dBm (the session\'s level); recording %s for %g s (pid %d); now play, within a few seconds:'
          % (a.slot, level, cap, a.seconds, p.pid))
    print(PLAYER.format(host=a.player_host, shot=shot, level=level, offset=a.offset_khz, name=a.name,
                        txdir=a.player_tx_dir))
    return 0


def _wait_recorder(a):
    pid_path = os.path.join(a.dir, a.name + '.rec.pid')
    rec_path = os.path.join(a.dir, a.name + '.cf32.rec.json')
    pid = int(open(pid_path).read()) if os.path.exists(pid_path) else None
    t_end = time.time() + SECONDS + WAIT_MARGIN_S
    while pid is not None and time.time() < t_end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        try:
            if os.waitpid(pid, os.WNOHANG)[0]:
                break
        except ChildProcessError:
            pass
        time.sleep(1)
    else:
        if pid is not None:
            return None, 'the recorder (pid %d) is still running %g s after it should have ended' % (pid, WAIT_MARGIN_S)
    if not os.path.exists(rec_path):
        return None, 'the recorder wrote no %s (see %s.rec.log)' % (os.path.basename(rec_path), a.name)
    return json.load(open(rec_path)), None


def cmd_finish(a):
    here, drive, name = a.dir, a.drive, a.name
    dirs = [here, drive]
    entries = _entries(drive)
    start = [e for e in entries if e.get('step') == 'start' and e['name'] == name]
    if not start:
        sys.exit('refused: %s was never started' % name)
    if any(e['name'] == name for e in _decisions(entries)):
        sys.exit('refused: %s already has a decision' % name)
    start = start[-1]
    ses = _session(a.decide, entries)
    if start['level_dbm'] != ses['level']:
        sys.exit('refused: %s was started at %g dBm, the session is at %g' % (name, start['level_dbm'], ses['level']))

    rec, why = _wait_recorder(a)
    if why:
        rec = {}
    lost, adc = rec.get('lost_sample_overflows'), rec.get('adc_overflows')
    recorder_fault = why or ('lost samples or ADC overflows' if (lost or adc) else None)
    _log(dirs, name, 'recorder', lost_sample_overflows=lost, adc_overflows=adc, recorded_s=rec.get('recorded_s'),
         fault=recorder_fault)

    tx_path = os.path.join(here, name + '_tx.json')
    tx, player_fault = {}, None
    if not os.path.exists(tx_path):
        src = '%s:%s/%s_tx.json' % (a.player_host, a.player_tx_dir, name)
        r = subprocess.run(['scp', '-q', src, tx_path], capture_output=True, text=True)
        if r.returncode:
            player_fault = 'the player sidecar could not be fetched: ' + r.stderr.strip()
    if not player_fault:
        tx = json.load(open(tx_path))
        if tx.get('shot') != start['shot'] or tx.get('vsg_level_dbm') != start['level_dbm']:
            sys.exit('refused, undecided: the player played %s at %s dBm, the session started %s at %g dBm'
                     % (tx.get('shot'), tx.get('vsg_level_dbm'), start['shot'], start['level_dbm']))
        if tx.get('sent') is not True:
            player_fault = 'the player did not send (sent %r, state %r)' % (tx.get('sent'), tx.get('state'))
    _log(dirs, name, 'tx', sent=tx.get('sent'), state=tx.get('state'), shot=tx.get('shot'),
         level_dbm=tx.get('vsg_level_dbm'), offset_hz=tx.get('vsg_offset_hz'), fault=player_fault)
    exit_fault = [e['reason'] for e in entries if e['name'] == name and e.get('step') == 'fault']

    snr = {}
    if not (recorder_fault or player_fault or exit_fault):
        t0 = time.time()
        snr = bb.snr(os.path.join(here, name + '.cf32'), os.path.join(a.shots, start['shot'] + '.cf32'),
                     bb._load(os.path.join(a.shots, start['shot'] + '.json')))
        with open(os.path.join(here, name + '.snr.json'), 'w') as f:
            json.dump(snr, f, indent=1)
        print('snr:', bb.snr_summary(snr), '(%.0f s)' % (time.time() - t0), flush=True)
        _log(dirs, name, 'snr', problem=snr['problem'], snr_db=snr.get('snr_db'), snr_db_min=snr.get('snr_db_min'),
             snr_db_max=snr.get('snr_db_max'), noise_1mhz_dbm=snr.get('noise_1mhz_dbm'), code=_commit(ROOT))

    inputs = dict(recorder_fault=bool(recorder_fault), player_fault=bool(player_fault), exit_fault=bool(exit_fault),
                  snr_refused=bool(snr.get('problem')), median=snr.get('snr_db'), lo=snr.get('snr_db_min'),
                  hi=snr.get('snr_db_max'))
    done = _decisions(entries)
    got = _replay(a.decide, [dict(slot=d['slot'], inputs=d['inputs']) for d in done]
                  + [dict(slot=start['slot'], inputs=inputs)])
    step = got['steps'][-1]
    _log(dirs, name, 'decision', slot=start['slot'], action=step['action'], inputs=inputs,
         level_used=step['level_used'], level_next=step['level_next'], relevels=step['relevels'],
         capped=step['capped'], state=step['state'], decide=_code_id(a.decide))
    print('decision (the registration\'s LevelSession): %s; slot %d; the next recording at %g dBm%s'
          % (step['action'], start['slot'], step['level_next'],
             '; the session is NOT DECIDABLE' if step['state'] == 'NOT DECIDABLE' else ''))
    print('now: close %s' % name)
    return 0


def cmd_close(a):
    here, drive, name = a.dir, a.drive, a.name
    dirs = [here, drive]
    entries = _entries(drive)
    dec = [e for e in _decisions(entries) if e['name'] == name]
    if not dec:
        sys.exit('refused: %s has no decision (finish it first)' % name)
    if any(e['name'] == name and e.get('step') == 'copy' and not e.get('fault') for e in entries):
        sys.exit('refused: %s is already closed' % name)
    keep = dec[-1]['action'] == 'KEEP'
    if keep:
        _align(a, dirs)
    else:
        _log(dirs, name, 'align', skipped='not aligned: %s' % dec[-1]['action'])
        print('%s: not aligned (%s)' % (name, dec[-1]['action']), flush=True)
    copied, why = copy_verified(here, drive, [f for f in _files(name) if os.path.exists(os.path.join(here, f))])
    if why:
        _log(dirs, name, 'copy', fault=why)
        sys.exit('copy refused or failed, nothing deleted: ' + why)
    gone = []
    for f in (name + '.cf32', os.path.join('align', name + '.cf32')):
        if f in copied:
            os.remove(os.path.join(here, f))
            gone.append(f)
    if os.path.exists(os.path.join(here, name + '.rec.pid')):
        os.remove(os.path.join(here, name + '.rec.pid'))
    _log(dirs, name, 'copy', files=copied, deleted_here=gone)
    print('on the drive, verified from the disk: %d files; deleted here: %s' % (len(copied), ', '.join(gone) or 'none'))
    return 0


def _align(a, dirs):
    here, name = a.dir, a.name
    cap, tx_path = os.path.join(here, name + '.cf32'), os.path.join(here, name + '_tx.json')
    shot = os.path.join(a.shots, json.load(open(tx_path))['shot'] + '.cf32')
    # The aligner's console ends with carrier values on accept: it goes to a file nothing here reads.
    with open(os.path.join(here, name + '.align.log'), 'w') as out:
        r = subprocess.run([PY, os.path.join(ROOT, 'scripts', 'bt_bench.py'), 'align', cap, shot, '--tx', tx_path,
                            '--rec', os.path.join(here, name + '.cf32.rec.json'), '--out', os.path.join(here, 'align'),
                            '--name', name], stdout=out, stderr=subprocess.STDOUT)
    # align writes NAME.align.json into --out; it belongs beside the recording.
    aj = os.path.join(here, 'align', name + '.align.json')
    if os.path.exists(aj):
        os.replace(aj, os.path.join(here, name + '.align.json'))
    allowed = os.path.join(here, name + '.align_allowed.json')
    if not os.path.exists(os.path.join(here, name + '.align.json')):
        print('align wrote no report (exit %d): refused before reading the recording' % r.returncode, flush=True)
        _log(dirs, name, 'align', exit=r.returncode, allowed=None, fault='align wrote no report')
        return
    k = subprocess.run([PY, '-I', a.allowed_keys, os.path.join(here, name + '.align.json'), allowed],
                       capture_output=True, text=True)
    if k.returncode:
        _log(dirs, name, 'align', exit=r.returncode, fault='the allowed-keys script failed: ' + k.stderr.strip()[-300:])
        sys.exit('the allowed-keys script failed; nothing copied or deleted')
    view = json.load(open(allowed))
    print('align (allowed keys only):', json.dumps(view), flush=True)
    _log(dirs, name, 'align', exit=r.returncode, allowed=view, allowed_keys=_code_id(a.allowed_keys))


def copy_verified(here, drive, rel):
    """Copy ``rel`` (paths relative to ``here``) to ``drive``, refusing any that exists there, and hash every copy
    from the disk against its source. Returns ({path: sha256}, None) or (partial, why); the sums file is appended only
    when all match."""
    for f in rel:
        if os.path.exists(os.path.join(drive, f)):
            return {}, '%s already exists on the drive' % f
    sums = {}
    for f in rel:
        dst = os.path.join(drive, f)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(os.path.join(here, f), dst)
        want, got = sha256_of(os.path.join(here, f)), sha256_from_disk(dst)
        if want != got:
            return sums, '%s on the drive is %s, here %s' % (f, got[:16], want[:16])
        sums[f] = want
    with open(os.path.join(drive, SUMS), 'a') as s:
        for f, h in sums.items():
            s.write('%s  %s\n' % (h, f))
        s.flush()
        os.fsync(s.fileno())
    return sums, None


def cmd_log(a):
    entries = _entries(a.drive)
    if not any(e['name'] == a.name and e.get('step') == 'start' for e in entries):
        sys.exit('refused: %s was never started (no start line in %s)' % (a.name, os.path.join(a.drive, LOG)))
    if a.event == 'fault' and any(e['name'] == a.name for e in _decisions(entries)):
        sys.exit('refused: %s already has a decision; a fault counts only before it' % a.name)
    _log([a.dir, a.drive], a.name, a.event, reason=a.reason)
    print('logged %s %s: %s' % (a.name, a.event, a.reason))
    return 0


def cmd_verify(a):
    bad = 0
    n = 0
    log = os.path.join(a.folder, LOG)
    broken = False
    if os.path.exists(log):
        at, why = check_chain(log)
        broken = why is not None
        print('%s: %s' % (LOG, 'chain OK, %d lines' % at if why is None else 'chain BROKEN at line %d: %s' % (at, why)))
        if not broken:
            order = check_order(_entries(a.folder))
            broken = bool(order)
            print('%s: %s' % (LOG, 'order OK' if not order else 'order BROKEN: ' + '; '.join(order)))
    for line in open(os.path.join(a.folder, SUMS)):
        h, f = line.rstrip('\n').split('  ', 1)
        got = sha256_from_disk(os.path.join(a.folder, f))
        n += 1
        ok = got == h
        bad += not ok
        print('%s: %s' % (f, 'OK (from the disk)' if ok else 'FAILED (%s)' % got[:16]), flush=True)
    print('%d of %d files match' % (n - bad, n))
    return 1 if bad or broken else 0


def _commit(path):
    r = subprocess.run(['git', '-C', path, 'rev-parse', 'HEAD'], capture_output=True, text=True)
    return r.stdout.strip() or None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    for c in ('start', 'finish', 'close', 'log'):
        p = sub.add_parser(c)
        p.add_argument('name')
        p.add_argument('--dir', required=True, help='this laptop\'s folder for the recording')
        p.add_argument('--drive', required=True, help='the folder on the 4 TB drive the recording goes to')
        if c in ('start', 'finish'):
            p.add_argument('--decide', required=True, help='the registration\'s diag_s4_bench_decide.py')
            p.add_argument('--player-host', default='worklaptop1-fast')
            p.add_argument('--player-tx-dir', default='rg/bench/tx', help='the player\'s sidecar folder, under its home')
        if c == 'start':
            p.add_argument('--slot', type=int, required=True, help='the slot in the registration\'s recording order')
            p.add_argument('--lap', required=True, choices=sorted(bb.SHOTS))
            p.add_argument('--offset-khz', type=float, required=True)
            p.add_argument('--seconds', type=float, default=SECONDS)
        if c in ('finish', 'close'):
            p.add_argument('--shots', default='/media/user/4TB/sdr-synth-tmp/bench/shots')
        if c == 'close':
            p.add_argument('--allowed-keys', required=True, help='the registration\'s s4_bench_allowed_keys.py')
        if c == 'log':
            p.add_argument('--event', required=True, choices=EVENTS)
            p.add_argument('--reason', required=True)
    p = sub.add_parser('verify')
    p.add_argument('folder')
    a = ap.parse_args(argv)
    return {'start': cmd_start, 'finish': cmd_finish, 'close': cmd_close, 'log': cmd_log,
            'verify': cmd_verify}[a.cmd](a)


if __name__ == '__main__':
    sys.exit(main())
