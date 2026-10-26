#!/usr/bin/env python3
"""One bench recording at a time, in the order bluey-ox-walker's bench registration fixes; its level rule is run, not re-made.

    python scripts/bt_bench_session.py start NAME --slot 3 --lap 885614 --offset-khz -58 --decide DECIDE --dir LOCAL --drive DRIVE
    ssh worklaptop1-fast '... bt_bench_play.py ... --go ... --name NAME ...'          # printed by start; run by hand
    python scripts/bt_bench_session.py log NAME --event fault --reason 'player exit 1' --dir LOCAL --drive DRIVE  # if so
    python scripts/bt_bench_session.py finish NAME --decide DECIDE --dir LOCAL --drive DRIVE
    python scripts/bt_bench_session.py close NAME --allowed-keys KEYS --dir LOCAL --drive DRIVE
    python scripts/bt_bench_session.py verify DRIVE_FOLDER                             # SHA256SUMS.txt, read from the disk

``DECIDE`` is the registration's ``diag_s4_bench_decide.py`` and ``KEYS`` its ``s4_bench_allowed_keys.py``, both run by
path in a separate ``python -I``, their sha256 and commit logged. The decision file's bytes are read once, hashed and
compiled by the replay itself, so no ``.pyc`` is ever loaded (``-B`` alone only stops one being written; an existing
cache whose source size and mtime match is still used) and the logged sha256 is of what ran.

Every command reads the session log on the drive and refuses unless the laptop's says the same, line for line: a write
that reached one and not the other, or a second, empty drive folder, stops the session instead of starting another.

**What it does not do.** It never transmits: ``start`` prints the player's command and a person (or the Claude session,
under its own narrow permission) runs it, so every transmission stays a separate, visible command. It makes no rule of
its own: keep, retake, discard and the next VSG level are the registration's ``LevelSession``, replayed from the session
log over every earlier attempt, so the operator neither types a level nor chooses an outcome.

**``start``** refuses a name already used here, on the drive or in the log, a recording while the one before has no
decision or is not yet on the drive, any recording once the session is NOT DECIDABLE, a slot already kept, and a slot
other than the one a retake or discard left open. After an UNDECIDED halt it refuses everything but ``--resume``, the
owner's resume of the halted slot; that resume is refused, with nothing logged, for another slot or another decision
file, and otherwise ruled by ``LevelSession.resume`` and logged under the halted recording before the start. The level
is the session's (``LevelSession`` replayed), never typed. It logs the cell and starts
``bt_bench.py record`` (``SECONDS`` s, the BB60D) in the background with its output in ``NAME.rec.log``.

**``amend``** (the F + rate amendment of 2026-10-26) re-rules one recording, already decided and copied, as refused by
the spread rule (``AMEND_INPUTS``): only a decision in ``AGAIN``, only the last recording started, once, before the next start.
The decision line stays as it was; an ``amend`` line after it carries the amendment file's sha256, the decision it
supersedes, and the replay with the new inputs, which from then on stands in for that decision in every replay. Every
replay still checks the original decision against its own inputs, and the amend against the amended ones.

**``log``** records an instrument fault (the player's non-zero exit, which only its console shows) or a note, both
refused once ``finish`` has begun for the recording: nothing the snr shows can bring a fault in after it.

**``finish``**, in this order, each step a line in ``session.jsonl`` (here and on the drive, flushed to the disk before
the next step):
1. waits for the recorder, and reads ``lost_sample_overflows`` and ``adc_overflows`` (a recorder that wrote no record,
   any loss or overflow, or a record whose ``overruns_counted`` is not true, is a recorder fault; the driver's
   "Sample Overrun" warnings, ``sample_overruns``, are logged and are not a fault by themselves - the snr spread
   below and the aligner judge the shot);
2. fetches the player's sidecar ``NAME_tx.json`` (``--player-host``, ``--player-tx-dir``); missing, ``sent`` not
   true or ``state`` not ``sent`` is a player fault; a sidecar of another shot (by name or sha256) or level than the
   session's is an UNDECIDED halt (``LevelSession.halt``, logged; nothing further is measured);
3. only with no fault: ``bt_bench.snr``, ``NAME.snr.json``, its summary printed; a spread ``snr_db_sem`` over
   ``SNR_MAX_SEM_DB``, or none, refuses it (``snr_refused``, its numbers under ``SPREAD_KEYS``);
4. the decision: ``LevelSession.decide`` over every earlier attempt and this one (an earlier decision, halt or resume
   that does not replay the same in action, levels, re-level count, cap and state refuses everything), logged with its
   inputs; a decision file that raises on this attempt is an UNDECIDED halt instead.
Nothing aligns here, so no aligner output exists when the decision is made.

**``close``** needs that decision or a halt. A KEEP is aligned (``bt_bench.py align`` with ``--tx`` and ``--rec``, its whole
console to ``NAME.align.log``, which nothing here prints: on accept it ends with the carrier values the registration
keeps from every reader until its stage 1) and the allowed-keys script writes ``NAME.align_allowed.json``, printed
unless it holds a carrier key (``CARRIER_KEYS``), which stops the close with nothing shown.
Any other outcome is not aligned. Then every file of the recording is copied to the drive (never over an existing one)
under a ``.partial`` name, written to the disk and its cached pages dropped before it is hashed, so the hash is of what
the disk holds, not of the page cache; only when every file matches its source are the copies renamed, their hashes
put in ``SHA256SUMS.txt``, and the large ``.cf32`` files here deleted (the recording and, for a KEEP, the aligner's
crop). A partial that does not match stays on the drive: nothing there is ever overwritten or deleted.

Every line of ``session.jsonl`` carries ``prev``, the sha256 of the line before it, and is fsynced before the next
step: the order of measurements and decisions, against the registration's freezes, is checkable afterwards.

**``verify``** re-checks a folder's session log - the chain, then the order the registration asserts (``check_order``),
then with ``--decide`` the replay every command runs (each decision, halt, resume and amend as logged) - and its
``SHA256SUMS.txt`` from the disk, and fails on a file the sums do not list. The chain is unkeyed: it shows a
line changed, dropped or moved, not a log rewritten from its first line, which needs its head hash kept elsewhere.
"""
import argparse
import hashlib
import json
import math
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
#: The session-log steps ``LevelSession`` rules on, in the order they were logged: a decision, an UNDECIDED halt and
#: the owner's resume after one.
RULED = ('decision', 'halt', 'resume')
#: What a replayed step must match of its logged decision.
STEP_KEYS = ('action', 'level_used', 'level_next', 'relevels', 'capped', 'state')
#: The F + rate amendment of 2026-10-26 (``knowledge/freshset/f-rate-amendment-2026-10-26.md``): a recording whose
#: ``bt_bench.snr`` spread over the shot's parts, ``snr_db_sem``, is over this, or missing, is refused (``snr_refused``:
#: dropped samples move part of the shot), its numbers logged under ``SPREAD_KEYS`` and not given to the decision.
SNR_MAX_SEM_DB = 0.10
SPREAD_KEYS = ('refused_snr_db', 'refused_snr_db_min', 'refused_snr_db_max')
#: The one input an ``amend`` gives a decision: its snr refused by the spread rule (no snr numbers).
AMEND_INPUTS = dict(recorder_fault=False, player_fault=False, exit_fault=False, snr_refused=True, median=None, lo=None,
                    hi=None)
#: Keys that are carrier values (or carry them) and never reach the console or the session log before the stage-1
#: freeze, whatever the allowed-keys script returns.
CARRIER_KEYS = ('f_located_hz', 'cfo_instrument_hz', 'cfo_instrument_spread_hz', 'reference_error_hz', 'cfo_hz',
                'cfo_planted_hz', 'rows')
#: A copy is written under this suffix, hashed from the disk, and only then given its name.
PARTIAL = '.partial'
#: Run in ``python -I -B`` with the registration's decision file: replays the attempts read from stdin. It executes the
#: bytes it hashed, compiled here, so no ``.pyc`` beside the file is ever loaded (``-B`` alone stops only the writing of
#: one: an existing cache whose source size and mtime match is still used), and the sha256 it prints is of what ran.
REPLAY = '''import hashlib, json, sys, types
src = open(sys.argv[1], 'rb').read()
m = types.ModuleType('s4_bench_decide')
m.__file__ = sys.argv[1]
sys.modules[m.__name__] = m
exec(compile(src, sys.argv[1], 'exec'), m.__dict__)
s = m.LevelSession()
out = []
for a in json.load(sys.stdin):
    before = s.level
    if a.get('kind', 'decide') == 'decide':
        act = s.decide(a['slot'], **a['inputs'])
    else:
        act = dict(halt=s.halt, resume=s.resume)[a['kind']](a['slot'], a['level'], a['decide_sha'])
    out.append(dict(action=act, level_used=before, level_next=s.level, relevels=s.relevels, capped=s.capped,
                    state=s.state))
print(json.dumps(dict(first_level=m.FIRST_COMMAND_DBM, level=s.level, state=s.state, steps=out,
                 sha256=hashlib.sha256(src).hexdigest())))
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
    """What the registration asserts of a session log, as a list of breaches (empty when it holds). Per recording: a
    start with a slot, under a name used once; a fault or note only before ``finish`` began (its recorder line); at
    most one decision, with an action, in the slot of its start, whose inputs are the recording's own last snr line
    (or no numbers when there is none) and its fault lines; an alignment (an align line with an ``exit``) only after its
    KEEP. Across recordings: a start only once the recording before it is decided and copied; nothing after NOT
    DECIDABLE; the slot a retake or discard left open is the next one started; a kept slot never started again; one
    KEEP per slot; no slot started again once one of its recordings was aligned. An UNDECIDED halt stands in for a
    recording's decision (never both, nothing measured after it, never aligned); after one, nothing but a resume of
    that slot, logged under the halted recording, and then a start of that slot. An amend (the amendment of 2026-10-26)
    only of a decision in ``AGAIN``, once, after its copy and before the next start, with ``AMEND_INPUTS`` and the
    amendment's sha256; its action then stands for the decision's."""
    bad, slot_of, decided, kept, aligned_slots = [], {}, {}, {}, set()
    began, snr_of, faults, copied, amended = set(), {}, {}, set(), set()
    last, open_slot, ended, halted, pending = None, None, False, set(), None
    for n, e in enumerate(entries, 1):
        name, step = e.get('name'), e.get('step')
        if step != 'start' and name not in slot_of:
            bad.append('line %d: %s %s before its start' % (n, name, step))
            continue
        if step == 'start':
            slot = e.get('slot')
            if name in slot_of:
                bad.append('line %d: %s started twice' % (n, name))
            if slot is None:
                bad.append('line %d: %s starts with no slot' % (n, name))
            if ended:
                bad.append('line %d: %s starts after the session was NOT DECIDABLE' % (n, name))
            if pending is not None:
                bad.append('line %d: %s starts after the halt of slot %s with no resume' % (n, name, pending))
            if last is not None and (last not in decided and last not in halted or last not in copied):
                bad.append('line %d: %s starts before %s was %s' % (n, name, last,
                                                                     'copied' if last in decided or last in halted
                                                                     else 'decided'))
            if open_slot is not None and slot != open_slot:
                bad.append('line %d: %s starts slot %s, slot %s was left for a retake' % (n, name, slot, open_slot))
            if slot in kept:
                bad.append('line %d: %s starts slot %s, already kept' % (n, name, slot))
            if slot in aligned_slots:
                bad.append('line %d: %s starts slot %s after a recording of it was aligned' % (n, name, slot))
            slot_of[name], last, open_slot = slot, name, None
        elif step in ('fault', 'note'):
            if name in began or name in decided or name in halted:
                bad.append('line %d: %s %s after finish began' % (n, name, step))
            if step == 'fault':
                faults[name] = True
        elif step == 'recorder':
            began.add(name)
        elif step == 'snr':
            if name in decided or name in halted:
                bad.append('line %d: %s snr after its %s' % (n, name, 'decision' if name in decided else 'halt'))
            snr_of[name] = e
        elif step == 'decision':
            action = e.get('action')
            if name in decided or name in halted:
                bad.append('line %d: %s decided %s' % (n, name, 'twice' if name in decided else 'after its halt'))
            if action is None:
                bad.append('line %d: %s decision with no action' % (n, name))
            if e.get('slot') != slot_of[name]:
                bad.append('line %d: %s decided in slot %s, started in slot %s' % (n, name, e.get('slot'),
                                                                                   slot_of[name]))
            got, sn = e.get('inputs') or {}, snr_of.get(name, {})
            want = dict(median=sn.get('snr_db'), lo=sn.get('snr_db_min'), hi=sn.get('snr_db_max'),
                        snr_refused=bool(sn.get('problem')), exit_fault=bool(faults.get(name)))
            if any(got.get(k) != v for k, v in want.items()):
                bad.append('line %d: %s decided on %s, its log says %s' % (
                    n, name, {k: got.get(k) for k in want}, want))
            decided[name] = action
            if action == 'KEEP':
                if slot_of[name] in kept:
                    bad.append('line %d: a second KEEP in slot %s (%s, after %s)' % (n, slot_of[name], name,
                                                                                    kept[slot_of[name]]))
                kept[slot_of[name]] = name
            elif action in AGAIN:
                open_slot = slot_of[name]
            if action == 'NOT DECIDABLE' or e.get('state') == 'NOT DECIDABLE':
                ended = True
        elif step == 'halt':
            if name in decided or name in halted:
                bad.append('line %d: %s halted after its %s' % (n, name, 'decision' if name in decided else 'halt'))
            if e.get('slot') != slot_of[name]:
                bad.append('line %d: %s halted in slot %s, started in slot %s' % (n, name, e.get('slot'),
                                                                                  slot_of[name]))
            halted.add(name)
            open_slot = slot_of[name]
            if e.get('action') == 'HALT':
                pending = slot_of[name]
            if e.get('action') == 'NOT DECIDABLE' or e.get('state') == 'NOT DECIDABLE':
                ended = True
        elif step == 'resume':
            if pending is None or name not in halted:
                bad.append('line %d: %s resumed with no halt waiting' % (n, name))
            elif e.get('slot') != pending:
                bad.append('line %d: slot %s resumed, slot %s halted' % (n, e.get('slot'), pending))
            if name not in copied:
                bad.append('line %d: %s resumed before it was copied' % (n, name))
            pending = None
            if e.get('action') != 'RESUME_AS_RETAKE' or e.get('state') == 'NOT DECIDABLE':
                ended = True
        elif step == 'align' and ('exit' in e or 'skipped' not in e):
            if decided.get(name) != 'KEEP':
                bad.append('line %d: %s aligned %s' % (n, name, 'with no decision before it' if name not in decided
                                                       else 'after a %s decision' % decided[name]))
            aligned_slots.add(slot_of[name])
        elif step == 'amend':
            if name not in decided or name in halted:
                bad.append('line %d: %s amended with no decision' % (n, name))
            elif name in amended:
                bad.append('line %d: %s amended twice' % (n, name))
            elif decided[name] not in AGAIN:
                bad.append('line %d: %s amended after a %s decision' % (n, name, decided[name]))
            if name not in copied:
                bad.append('line %d: %s amended before it was copied' % (n, name))
            if name != last:
                bad.append('line %d: %s amended after %s was started' % (n, name, last))
            if e.get('slot') != slot_of[name]:
                bad.append('line %d: %s amended in slot %s, started in slot %s' % (n, name, e.get('slot'),
                                                                                   slot_of[name]))
            if e.get('inputs') != AMEND_INPUTS:
                bad.append('line %d: %s amended with inputs %s, not %s' % (n, name, e.get('inputs'), AMEND_INPUTS))
            if not (e.get('amendment') or {}).get('sha256'):
                bad.append('line %d: %s amended with no amendment sha256' % (n, name))
            amended.add(name)
            action = e.get('action')
            decided[name] = action
            open_slot = slot_of[name] if action in AGAIN else None
            if action == 'KEEP':
                kept.setdefault(slot_of[name], name)
            if action == 'NOT DECIDABLE' or e.get('state') == 'NOT DECIDABLE':
                ended = True
        elif step == 'copy' and not e.get('fault'):
            copied.add(name)
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


def _amended(entries, e):
    """A decision line as it stands: re-ruled by its recording's ``amend`` line, if there is one (the amend's inputs and
    outcome, the original kept under ``superseded_line``), else as logged."""
    if e.get('step') != 'decision':
        return e
    amend = [x for x in entries if x.get('step') == 'amend' and x.get('name') == e['name']]
    if not amend:
        return e
    return dict(e, inputs=amend[0]['inputs'], **{k: amend[0].get(k) for k in STEP_KEYS}, superseded_line=e)


def _decisions(entries):
    return [_amended(entries, e) for e in entries if e.get('step') == 'decision']


def _ruled(entries):
    return [_amended(entries, e) for e in entries if e.get('step') in RULED]


def _attempt(e):
    """A ruled session-log line as the replay takes it."""
    if e['step'] == 'decision':
        return dict(slot=e['slot'], inputs=e['inputs'])
    return dict(kind=e['step'], slot=e['slot'], level=e['level_dbm'], decide_sha=e['decide_sha'])


def _pending_halt(entries):
    """The halt line no resume has followed, or None."""
    ruled = _ruled(entries)
    return ruled[-1] if ruled and ruled[-1]['step'] == 'halt' and ruled[-1].get('action') == 'HALT' else None


def _agreed(here, drive):
    """The drive's session log, refused unless this laptop's says the same line for line (``prev`` aside): a write that
    reached one and not the other, or a second drive folder, stops the session instead of starting a second one."""
    a, b = _entries(here), _entries(drive)
    strip = [{k: v for k, v in e.items() if k != 'prev'} for e in a], [{k: v for k, v in e.items() if k != 'prev'}
                                                                         for e in b]
    if strip[0] != strip[1]:
        n = next((i for i, (x, y) in enumerate(zip(*strip), 1) if x != y), min(len(a), len(b)) + 1)
        sys.exit('refused: the session logs here (%d lines) and on the drive (%d lines) differ from line %d; nothing '
                 'goes on until they agree' % (len(a), len(b), n))
    return b


def _replay(decide, attempts):
    """``LevelSession`` over ``attempts`` ([{slot, inputs}] or [{kind, slot, level, decide_sha}], in order), in a
    separate ``python -I``."""
    got, why = _try_replay(decide, attempts)
    if why:
        sys.exit('the decision file failed: ' + why)
    if len(got['steps']) != len(attempts):
        sys.exit('refused: the decision file replayed %d of %d attempts' % (len(got['steps']), len(attempts)))
    return got


def _try_replay(decide, attempts):
    """(the replay, None), or (None, why) when the decision file raised."""
    r = subprocess.run([PY, '-I', '-B', '-c', REPLAY, decide], input=json.dumps(attempts), capture_output=True, text=True)
    if r.returncode:
        return None, r.stderr.strip()[-400:]
    return json.loads(r.stdout), None


def _session(decide, entries):
    """The session replayed: every logged decision, halt and resume must come out the same, else nothing goes on."""
    done = _ruled(entries)
    got = _replay(decide, [_attempt(d) for d in done])
    for d, g in zip(done, got['steps']):
        if [d.get(k) for k in STEP_KEYS] != [g.get(k) for k in STEP_KEYS]:
            sys.exit('refused: %s was logged %s, the decision file now gives %s: the log or the code changed'
                     % (d['name'], {k: d.get(k) for k in STEP_KEYS}, {k: g.get(k) for k in STEP_KEYS}))
    # An amended decision must still have been what its own inputs gave, over the session before it.
    for i, d in enumerate(done):
        if 'superseded_line' in d:
            orig = d['superseded_line']
            g = _replay(decide, [_attempt(x) for x in done[:i]] + [_attempt(orig)])['steps'][-1]
            if [orig.get(k) for k in STEP_KEYS] != [g.get(k) for k in STEP_KEYS]:
                sys.exit('refused: %s was decided %s before its amend, the decision file now gives %s: the log or the '
                         'code changed' % (d['name'], {k: orig.get(k) for k in STEP_KEYS},
                                           {k: g.get(k) for k in STEP_KEYS}))
    return got


def _code_id(path, sha256=None):
    """A script's identity in the log: its sha256 (for the decision file, of the bytes the replay ran), its path, and
    the commit its folder's checkout is at (which says nothing of uncommitted changes: the sha256 is the identity)."""
    return dict(path=os.path.abspath(path), sha256=sha256 or sha256_of(path),
                commit=_commit(os.path.dirname(os.path.abspath(path))))


def cmd_start(a):
    os.makedirs(a.dir, exist_ok=True)
    taken = _taken(a.name, a.dir, a.drive)
    if taken:
        sys.exit('refused: %s already exists (a name is used once)' % taken[0])
    entries = _agreed(a.dir, a.drive)
    if any(e.get('name') == a.name for e in entries):
        sys.exit('refused: %s is already in the session log (a name is used once)' % a.name)
    decided = {e['name'] for e in _decisions(entries) + [e for e in entries if e.get('step') == 'halt']}
    closed = {e['name'] for e in entries if e.get('step') == 'copy' and not e.get('fault')}
    starts = [e['name'] for e in entries if e.get('step') == 'start']
    if starts and starts[-1] not in decided:
        sys.exit('refused: %s has no decision yet (finish it first)' % starts[-1])
    if starts and starts[-1] not in closed:
        sys.exit('refused: %s is not on the drive yet (close it first)' % starts[-1])
    ses = _session(a.decide, entries)
    if ses['state'] == 'NOT DECIDABLE':
        sys.exit('refused: the session is NOT DECIDABLE; no more recordings')
    halt = _pending_halt(entries)
    if halt and not a.resume:
        sys.exit('refused: %s halted undecided (%s). The owner may resume slot %d once, at the same level with the same '
                 'decision file: start NAME --slot %d ... --resume; it is that slot\'s one retake and its next outcome '
                 'must be a KEEP' % (halt['name'], halt['reason'], halt['slot'], halt['slot']))
    if a.resume and not halt:
        sys.exit('refused: --resume, and no halt is waiting for one')
    if halt:
        if a.slot != halt['slot']:
            sys.exit('refused: slot %d halted; only it may be resumed (another slot would make the bench NOT '
                     'DECIDABLE, so nothing was logged)' % halt['slot'])
        if ses['sha256'] != halt['decide_sha']:
            sys.exit('refused: the decision file is %s, the halt was under %s; a resume needs the same file (nothing '
                     'was logged)' % (ses['sha256'][:16], halt['decide_sha'][:16]))
        resume = dict(step='resume', slot=a.slot, level_dbm=ses['level'], decide_sha=ses['sha256'])
        got = _replay(a.decide, [_attempt(d) for d in _ruled(entries)] + [_attempt(resume)])
        step = got['steps'][-1]
        _log([a.dir, a.drive], halt['name'], 'resume', slot=a.slot, level_dbm=ses['level'], decide_sha=got['sha256'],
             **{k: step[k] for k in STEP_KEYS}, decide=_code_id(a.decide, got['sha256']))
        if step['action'] != 'RESUME_AS_RETAKE':
            sys.exit('refused: the resume of slot %d was ruled %s; no more recordings' % (a.slot, step['action']))
        print('slot %d resumed after the halt of %s: this is its one retake, and its next outcome must be a KEEP'
              % (a.slot, halt['name']))
        entries = _agreed(a.dir, a.drive)
        ses = _session(a.decide, entries)
    done = _decisions(entries)
    if any(d['slot'] == a.slot and d['action'] == 'KEEP' for d in done):
        sys.exit('refused: slot %d is already kept' % a.slot)
    if not halt and done and done[-1]['action'] in AGAIN and done[-1]['slot'] != a.slot:
        sys.exit('refused: slot %d was left for a retake (%s): record it again first' % (done[-1]['slot'],
                                                                                         done[-1]['action']))
    level = ses['level']
    cap = os.path.join(a.dir, a.name + '.cf32')
    shot = bb.shot_name(a.lap)
    _log([a.dir, a.drive], a.name, 'start', slot=a.slot, lap=a.lap, offset_khz=a.offset_khz, level_dbm=level,
         shot=shot, seconds=a.seconds, decide=_code_id(a.decide, ses['sha256']), load_avg=list(os.getloadavg()))
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


def spread_refusal(snr):
    """``bt_bench.snr``'s result as the decision takes it (the amendment of 2026-10-26): with no problem but a spread
    ``snr_db_sem`` over ``SNR_MAX_SEM_DB``, or none (or not a number), it is refused - ``problem`` set, ``spread_problem``
    true, and ``snr_db``, ``snr_db_min`` and ``snr_db_max`` moved under ``SPREAD_KEYS``, since the decision file takes a
    refusal or numbers, never both. A NaN sem is logged as None."""
    if snr.get('problem'):
        return snr
    sem = snr.get('snr_db_sem')
    ok = isinstance(sem, (int, float)) and math.isfinite(sem)
    if ok and sem <= SNR_MAX_SEM_DB:
        return snr
    out = dict(snr, snr_db_sem=sem if ok else None, spread_problem=True,
               problem=('the level over the shot\'s parts spreads %s dB (snr_db_sem over %.2f dB, or none): samples '
                        'dropped in the recording' % ('%.3f' % sem if ok else 'by no number', SNR_MAX_SEM_DB)))
    for k, r in zip(('snr_db', 'snr_db_min', 'snr_db_max'), SPREAD_KEYS):
        out[r] = out.pop(k, None)
    return out


def cmd_finish(a):
    here, drive, name = a.dir, a.drive, a.name
    dirs = [here, drive]
    entries = _agreed(here, drive)
    start = [e for e in entries if e.get('step') == 'start' and e['name'] == name]
    if not start:
        sys.exit('refused: %s was never started' % name)
    if any(e['name'] == name and e.get('step') in ('decision', 'halt') for e in entries):
        sys.exit('refused: %s already has a decision or a halt' % name)
    start = start[-1]
    ses = _session(a.decide, entries)
    if start['level_dbm'] != ses['level']:
        sys.exit('refused: %s was started at %g dBm, the session is at %g' % (name, start['level_dbm'], ses['level']))

    rec, why = _wait_recorder(a)
    if why:
        rec = {}
    lost, adc = rec.get('lost_sample_overflows'), rec.get('adc_overflows')
    recorder_fault = why or ('lost samples or ADC overflows' if (lost or adc) else None)
    if not recorder_fault and rec.get('overruns_counted') is not True:
        recorder_fault = 'the recorder could not count the driver\'s Sample Overrun warnings'
    _log(dirs, name, 'recorder', lost_sample_overflows=lost, adc_overflows=adc, recorded_s=rec.get('recorded_s'),
         sample_overruns=rec.get('sample_overruns'), overruns_counted=rec.get('overruns_counted'), fault=recorder_fault)

    tx_path = os.path.join(here, name + '_tx.json')
    tx, player_fault = {}, None
    if not os.path.exists(tx_path):
        src = '%s:%s/%s_tx.json' % (a.player_host, a.player_tx_dir, name)
        r = subprocess.run(['scp', '-q', src, tx_path], capture_output=True, text=True)
        if r.returncode:
            player_fault = 'the player sidecar could not be fetched: ' + r.stderr.strip()
    shot_side = bb._load(os.path.join(a.shots, start['shot'] + '.json'))
    if not player_fault:
        tx = json.load(open(tx_path))
        if (tx.get('shot') != start['shot'] or tx.get('vsg_level_dbm') != start['level_dbm']
                or tx.get('shot_sha256') != shot_side['bench_shot']['sha256']):
            _halt(a, dirs, start, entries, 'the player played %s (sha256 %s) at %s dBm, the session started %s (%s) at '
                  '%g dBm' % (tx.get('shot'), str(tx.get('shot_sha256'))[:16], tx.get('vsg_level_dbm'),
                              start['shot'], shot_side['bench_shot']['sha256'][:16], start['level_dbm']))
        if tx.get('sent') is not True or tx.get('state') != 'sent':
            player_fault = 'the player did not send (sent %r, state %r)' % (tx.get('sent'), tx.get('state'))
    _log(dirs, name, 'tx', sent=tx.get('sent'), state=tx.get('state'), shot=tx.get('shot'),
         level_dbm=tx.get('vsg_level_dbm'), offset_hz=tx.get('vsg_offset_hz'), fault=player_fault)
    exit_fault = [e['reason'] for e in entries if e['name'] == name and e.get('step') == 'fault']

    snr = {}
    if not (recorder_fault or player_fault or exit_fault):
        t0 = time.time()
        snr = bb.snr(os.path.join(here, name + '.cf32'), os.path.join(a.shots, start['shot'] + '.cf32'), shot_side)
        with open(os.path.join(here, name + '.snr.json'), 'w') as f:
            json.dump(snr, f, indent=1)
        print('snr:', bb.snr_summary(snr), '(%.0f s)' % (time.time() - t0), flush=True)
        snr = spread_refusal(snr)
        if snr.get('spread_problem'):
            print('snr refused: %s' % snr['problem'], flush=True)
        _log(dirs, name, 'snr', problem=snr['problem'], snr_db=snr.get('snr_db'), snr_db_min=snr.get('snr_db_min'),
             snr_db_max=snr.get('snr_db_max'), snr_db_sem=snr.get('snr_db_sem'),
             **{k: snr[k] for k in SPREAD_KEYS if k in snr}, noise_1mhz_dbm=snr.get('noise_1mhz_dbm'),
             code=_commit(ROOT))

    inputs = dict(recorder_fault=bool(recorder_fault), player_fault=bool(player_fault), exit_fault=bool(exit_fault),
                  snr_refused=bool(snr.get('problem')), median=snr.get('snr_db'), lo=snr.get('snr_db_min'),
                  hi=snr.get('snr_db_max'))
    got, why = _try_replay(a.decide, [_attempt(d) for d in _ruled(entries)]
                           + [dict(slot=start['slot'], inputs=inputs)])
    if why:
        _halt(a, dirs, start, entries, 'the decision file raised: ' + why.splitlines()[-1])
    step = got['steps'][-1]
    _log(dirs, name, 'decision', slot=start['slot'], action=step['action'], inputs=inputs,
         level_used=step['level_used'], level_next=step['level_next'], relevels=step['relevels'],
         capped=step['capped'], state=step['state'], decide=_code_id(a.decide, got['sha256']))
    print('decision (the registration\'s LevelSession): %s; slot %d; the next recording at %g dBm%s'
          % (step['action'], start['slot'], step['level_next'],
             '; the session is NOT DECIDABLE' if step['state'] == 'NOT DECIDABLE' else ''))
    print('now: close %s' % name)
    return 0


def cmd_amend(a):
    """Re-rule ``a.name`` as refused by the spread rule, under the amendment ``a.amendment`` (see the module
    docstring)."""
    here, drive, name = a.dir, a.drive, a.name
    entries = _agreed(here, drive)
    dec = [e for e in entries if e['name'] == name and e.get('step') == 'decision']
    starts = [e['name'] for e in entries if e.get('step') == 'start']
    if not dec:
        sys.exit('refused: %s has no decision' % name)
    if any(e['name'] == name and e.get('step') == 'halt' for e in entries):
        sys.exit('refused: %s halted' % name)
    if starts[-1] != name:
        sys.exit('refused: %s was started after %s; only the last recording can be amended' % (starts[-1], name))
    if not any(e['name'] == name and e.get('step') == 'copy' and not e.get('fault') for e in entries):
        sys.exit('refused: %s is not on the drive yet (close it first)' % name)
    if any(e['name'] == name and e.get('step') == 'amend' for e in entries):
        sys.exit('refused: %s is already amended' % name)
    if dec[-1]['action'] not in AGAIN:
        sys.exit('refused: %s was decided %s; only %s can be amended' % (name, dec[-1]['action'], ' or '.join(AGAIN)))
    if not os.path.isfile(a.amendment):
        sys.exit('refused: no amendment file %s' % a.amendment)
    _session(a.decide, entries)
    ruled = _ruled(entries)
    if ruled[-1]['name'] != name or ruled[-1]['step'] != 'decision':
        sys.exit('refused: %s is not the last ruled step' % name)
    got = _replay(a.decide, [_attempt(x) for x in ruled[:-1]] + [dict(slot=dec[-1]['slot'], inputs=AMEND_INPUTS)])
    step = got['steps'][-1]
    _log([here, drive], name, 'amend', slot=dec[-1]['slot'], inputs=AMEND_INPUTS, reason=a.reason,
         superseded={k: dec[-1].get(k) for k in STEP_KEYS}, **{k: step[k] for k in STEP_KEYS},
         amendment=_code_id(a.amendment), decide=_code_id(a.decide, got['sha256']))
    print('%s amended (its snr refused by the spread rule): %s, was %s; slot %d; the next recording at %g dBm%s'
          % (name, step['action'], dec[-1]['action'], dec[-1]['slot'], step['level_next'],
             '; the session is NOT DECIDABLE' if step['state'] == 'NOT DECIDABLE' else ''))
    return 0


def _halt(a, dirs, start, entries, reason):
    """An UNDECIDED halt: ``LevelSession.halt`` (an attempt, and the slot's one non-KEEP) over the session so far,
    logged with why, and the end of ``finish``."""
    halt = dict(step='halt', slot=start['slot'], level_dbm=start['level_dbm'], decide_sha=sha256_of(a.decide))
    got = _replay(a.decide, [_attempt(d) for d in _ruled(entries)] + [_attempt(halt)])
    step = got['steps'][-1]
    _log(dirs, start['name'], 'halt', slot=start['slot'], level_dbm=start['level_dbm'], decide_sha=got['sha256'],
         reason=reason, **{k: step[k] for k in STEP_KEYS}, decide=_code_id(a.decide, got['sha256']))
    sys.exit('halted, undecided (%s): %s. Close it; then %s' % (
        step['action'], reason, 'the session is NOT DECIDABLE' if step['state'] == 'NOT DECIDABLE' else
        'only the owner\'s resume of slot %d (start --resume) goes on' % start['slot']))


def cmd_close(a):
    here, drive, name = a.dir, a.drive, a.name
    dirs = [here, drive]
    entries = _agreed(here, drive)
    dec = [e for e in entries if e['name'] == name and e.get('step') in ('decision', 'halt')]
    if not dec:
        sys.exit('refused: %s has no decision or halt (finish it first)' % name)
    if any(e['name'] == name and e.get('step') == 'copy' and not e.get('fault') for e in entries):
        sys.exit('refused: %s is already closed' % name)
    keep = dec[-1]['step'] == 'decision' and dec[-1]['action'] == 'KEEP'
    if keep:
        _align(a, dirs)
    else:
        what = dec[-1]['action'] if dec[-1]['step'] == 'decision' else 'halted undecided'
        _log(dirs, name, 'align', skipped='not aligned: %s' % what)
        print('%s: not aligned (%s)' % (name, what), flush=True)
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
    leak = _carrier_keys(view)
    if leak:
        _log(dirs, name, 'align', exit=r.returncode, allowed=None, allowed_keys=_code_id(a.allowed_keys),
             fault='the allowed-keys script returned carrier keys; not shown')
        sys.exit('the allowed-keys script returned %d carrier key(s), not shown or logged; nothing copied or deleted '
                 '(%s holds them: do not open it)' % (len(leak), os.path.basename(allowed)))
    print('align (allowed keys only):', json.dumps(view), flush=True)
    _log(dirs, name, 'align', exit=r.returncode, allowed=view, allowed_keys=_code_id(a.allowed_keys))


def _carrier_keys(view):
    """The carrier keys anywhere in ``view`` (dicts and lists walked)."""
    if isinstance(view, dict):
        return [k for k in view if k in CARRIER_KEYS] + [x for v in view.values() for x in _carrier_keys(v)]
    if isinstance(view, list):
        return [x for v in view for x in _carrier_keys(v)]
    return []


def copy_verified(here, drive, rel):
    """Copy ``rel`` (paths relative to ``here``) to ``drive``. Every file is written as NAME``PARTIAL``, written through
    and hashed from the disk against its source; only when all match are they renamed to their names (the folder
    fsynced) and their hashes appended to ``SUMS``, so a name on the drive always holds verified bytes. A file or a
    partial already there refuses the whole copy, and a partial that did not match is left where it is: nothing on the
    drive is ever overwritten or deleted. Returns ({path: sha256}, None) or ({}, why)."""
    for f in rel:
        for p in (os.path.join(drive, f), os.path.join(drive, f) + PARTIAL):
            if os.path.exists(p):
                return {}, '%s already exists on the drive' % os.path.relpath(p, drive)
    sums = {}
    for f in rel:
        dst = os.path.join(drive, f) + PARTIAL
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(os.path.join(here, f), dst)
        want, got = sha256_of(os.path.join(here, f)), sha256_from_disk(dst)
        if want != got:
            return {}, ('%s on the drive is %s, here %s; the partial copies stay on the drive for the user to look at'
                        % (f, got[:16], want[:16]))
        sums[f] = want
    for f in rel:
        os.rename(os.path.join(drive, f) + PARTIAL, os.path.join(drive, f))
    for d in {os.path.dirname(os.path.join(drive, f)) for f in rel}:
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    with open(os.path.join(drive, SUMS), 'a') as s:
        for f, h in sums.items():
            s.write('%s  %s\n' % (h, f))
        s.flush()
        os.fsync(s.fileno())
    return sums, None


def cmd_log(a):
    entries = _agreed(a.dir, a.drive)
    if not any(e['name'] == a.name and e.get('step') == 'start' for e in entries):
        sys.exit('refused: %s was never started (no start line in %s)' % (a.name, os.path.join(a.drive, LOG)))
    if any(e['name'] == a.name and e.get('step') in ('recorder', 'decision', 'halt') for e in entries):
        sys.exit('refused: finish has begun for %s; a %s counts only before it' % (a.name, a.event))
    _log([a.dir, a.drive], a.name, a.event, reason=a.reason)
    print('logged %s %s: %s' % (a.name, a.event, a.reason))
    return 0


def cmd_verify(a):
    """The session log's chain and order, then every file in ``SUMS`` hashed from the disk, then every file in the folder
    that ``SUMS`` does not list (a partial copy included): any of them fails."""
    broken = []
    log = os.path.join(a.folder, LOG)
    if os.path.exists(log):
        at, why = check_chain(log)
        print('%s: %s' % (LOG, 'chain OK, %d lines' % at if why is None else 'chain BROKEN at line %d: %s' % (at, why)))
        if why is None:
            order = check_order(_entries(a.folder))
            print('%s: %s' % (LOG, 'order OK' if not order else 'order BROKEN: ' + '; '.join(order)))
            broken += order
            if a.decide:
                try:
                    _session(a.decide, _entries(a.folder))
                    print('%s: replay OK (%s)' % (LOG, sha256_of(a.decide)[:16]))
                except SystemExit as e:
                    print('%s: replay BROKEN: %s' % (LOG, e.code))
                    broken.append('replay')
        else:
            broken.append(why)
    sums_path = os.path.join(a.folder, SUMS)
    listed, bad = set(), 0
    if not os.path.exists(sums_path):
        print('%s: missing' % SUMS)
        broken.append('no sums')
    else:
        for line in open(sums_path):
            h, f = line.rstrip('\n').split('  ', 1)
            listed.add(os.path.normpath(f))
            path = os.path.join(a.folder, f)
            got = sha256_from_disk(path) if os.path.exists(path) else None
            bad += got != h
            print('%s: %s' % (f, 'OK (from the disk)' if got == h else 'MISSING' if got is None
                              else 'FAILED (%s)' % got[:16]), flush=True)
    unlisted = sorted(os.path.relpath(os.path.join(d, f), a.folder) for d, _, fs in os.walk(a.folder) for f in fs
                      if os.path.relpath(os.path.join(d, f), a.folder) not in listed | {LOG, SUMS})
    for f in unlisted:
        print('%s: NOT IN %s' % (f, SUMS))
    n = len(listed)
    print('%d of %d files match%s' % (n - bad, n, '; %d not listed' % len(unlisted) if unlisted else ''))
    if not n:
        print('no files listed')
    return 1 if bad or broken or unlisted or not n else 0


def _commit(path):
    r = subprocess.run(['git', '-C', path, 'rev-parse', 'HEAD'], capture_output=True, text=True)
    return r.stdout.strip() or None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    for c in ('start', 'finish', 'close', 'log', 'amend'):
        p = sub.add_parser(c)
        p.add_argument('name')
        p.add_argument('--dir', required=True, help='this laptop\'s folder for the recording')
        p.add_argument('--drive', required=True, help='the folder on the 4 TB drive the recording goes to')
        if c in ('start', 'finish', 'amend'):
            p.add_argument('--decide', required=True, help='the registration\'s diag_s4_bench_decide.py')
            p.add_argument('--player-host', default='worklaptop1-fast')
            p.add_argument('--player-tx-dir', default='rg/bench/tx', help='the player\'s sidecar folder, under its home')
        if c == 'start':
            p.add_argument('--slot', type=int, required=True, help='the slot in the registration\'s recording order')
            p.add_argument('--lap', required=True, choices=sorted(bb.SHOTS))
            p.add_argument('--offset-khz', type=float, required=True)
            p.add_argument('--seconds', type=float, default=SECONDS)
            p.add_argument('--resume', action='store_true', help='the owner\'s resume of the slot a halt left: the '
                           'same slot, level and decision file; that slot\'s one retake')
        if c in ('finish', 'close'):
            p.add_argument('--shots', default='/media/user/4TB/sdr-synth-tmp/bench/shots')
        if c == 'close':
            p.add_argument('--allowed-keys', required=True, help='the registration\'s s4_bench_allowed_keys.py')
        if c == 'log':
            p.add_argument('--event', required=True, choices=EVENTS)
            p.add_argument('--reason', required=True)
        if c == 'amend':
            p.add_argument('--amendment', required=True, help='the dated amendment text bluey approved')
            p.add_argument('--reason', required=True)
    p = sub.add_parser('verify')
    p.add_argument('folder')
    p.add_argument('--decide', help='also replay every decision, halt, resume and amend with this decision file')
    a = ap.parse_args(argv)
    return {'start': cmd_start, 'finish': cmd_finish, 'close': cmd_close, 'log': cmd_log, 'amend': cmd_amend,
            'verify': cmd_verify}[a.cmd](a)


if __name__ == '__main__':
    sys.exit(main())
