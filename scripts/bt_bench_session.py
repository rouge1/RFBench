#!/usr/bin/env python3
"""One bench recording at a time, in the order bluey-ox-walker's bench registration fixes, and nothing it decides.

    python scripts/bt_bench_session.py start NAME --lap 885614 --offset-khz -58 --level -68 --dir LOCAL --drive DRIVE
    ssh worklaptop1-fast '... bt_bench_play.py ... --go ... --name NAME ...'          # printed by start; run by hand
    python scripts/bt_bench_session.py finish NAME --dir LOCAL --drive DRIVE --allowed-keys PATH/s4_bench_allowed_keys.py
    python scripts/bt_bench_session.py verify DRIVE_FOLDER                             # SHA256SUMS.txt, read from the disk

**What it does not do.** It never transmits: ``start`` prints the player's command and a person (or the Claude session,
under its own narrow permission) runs it, so every transmission stays a separate, visible command. It never decides:
keeping a recording, discarding it for its level, retaking it and the next VSG level are the registration's rules,
applied by the operator from ``NAME.snr.json`` and written in the session log; this script only measures and records.

**``start``** refuses a name already used here or on the drive, writes a session-log line, and starts
``bt_bench.py record`` (``SECONDS`` s, the BB60D) in the background with its output in ``NAME.rec.log``.

**``finish``**, in this order, each step a line in ``session.jsonl`` (here and on the drive, flushed to the disk before
the next step):
1. waits for the recorder, and reads ``lost_sample_overflows`` (a loss is logged as an instrument fault);
2. fetches the player's sidecar ``NAME_tx.json`` from the player host (``--player-host``, ``--player-tx-dir``);
3. ``bt_bench.snr``: ``NAME.snr.json``, its summary printed;
4. ``bt_bench.py align`` with ``--tx`` and ``--rec``, its whole console to ``NAME.align.log``, which nothing here
   prints: on accept it ends with the carrier values the registration keeps from every reader until its stage 1;
5. the registration's own key-copying script (``--allowed-keys``, run by path, its commit logged), which writes
   ``NAME.align_allowed.json``, printed;
6. every file of the recording copied to the drive (never over an existing one), each written to the disk and its
   cached pages dropped before it is hashed, so the hash is of what the disk holds, not of the page cache; only when
   every file matches its source are the two large ``.cf32`` files here deleted (the recording and the aligner's
   crop); nothing on the drive is ever deleted. The hashes are appended to the drive's ``SHA256SUMS.txt``.

**``verify``** re-checks a folder's ``SHA256SUMS.txt`` the same way, from the disk.
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
PLAYER = ("ssh {host} 'cd /data/python/SDR && timeout -k 30 60 ~/miniconda3/envs/gnu/bin/python "
          "scripts/bt_bench_play.py ~/rg/bench/shots/{shot}.cf32 --go --pad-db 0 --level {level:g} "
          "--vsg-offset-khz {offset:g} --name {name} --out ~/{txdir}'")


def _files(name):
    """Every file of a recording, as (path here, path on the drive), relative to the two folders."""
    return [name + '.cf32', name + '.cf32.rec.json', name + '.rec.log', name + '_tx.json', name + '.snr.json',
            name + '.align.json', name + '.align.log', name + '.align_allowed.json',
            os.path.join('align', name + '.cf32'), os.path.join('align', name + '.json')]


def _log(dirs, name, step, **kw):
    """One line in ``session.jsonl`` in each of ``dirs``, written through to the disk."""
    line = json.dumps(dict(time=time.strftime('%Y-%m-%dT%H:%M:%S%z'), unix=time.time(), name=name, step=step, **kw))
    for d in dirs:
        with open(os.path.join(d, LOG), 'a') as f:
            f.write(line + '\n')
            f.flush()
            os.fsync(f.fileno())


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


def cmd_start(a):
    os.makedirs(a.dir, exist_ok=True)
    taken = _taken(a.name, a.dir, a.drive)
    if taken:
        sys.exit('refused: %s already exists (a name is used once)' % taken[0])
    cap = os.path.join(a.dir, a.name + '.cf32')
    shot = bb.shot_name(a.lap)
    _log([a.dir, a.drive], a.name, 'start', lap=a.lap, offset_khz=a.offset_khz, level_dbm=a.level, shot=shot,
         seconds=a.seconds)
    with open(os.path.join(a.dir, a.name + '.rec.log'), 'w') as out:
        p = subprocess.Popen([PY, os.path.join(ROOT, 'scripts', 'bt_bench.py'), 'record', cap, '--seconds',
                              '%g' % a.seconds], stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    with open(os.path.join(a.dir, a.name + '.rec.pid'), 'w') as f:
        f.write(str(p.pid))
    print('recording %s for %g s (pid %d); now play, within a few seconds:' % (cap, a.seconds, p.pid))
    print(PLAYER.format(host=a.player_host, shot=shot, level=a.level, offset=a.offset_khz, name=a.name,
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
    rec, why = _wait_recorder(a)
    if why:
        _log(dirs, name, 'recorder', fault=why)
        sys.exit('instrument fault: ' + why)
    lost = rec.get('lost_sample_overflows')
    _log(dirs, name, 'recorder', lost_sample_overflows=lost, adc_overflows=rec.get('adc_overflows'),
         recorded_s=rec.get('recorded_s'), fault=('lost samples' if lost else None))
    tx_path = os.path.join(here, name + '_tx.json')
    if not os.path.exists(tx_path):
        src = '%s:%s/%s_tx.json' % (a.player_host, a.player_tx_dir, name)
        r = subprocess.run(['scp', '-q', src, tx_path], capture_output=True, text=True)
        if r.returncode:
            _log(dirs, name, 'tx', fault='the player sidecar could not be fetched: ' + r.stderr.strip())
            sys.exit('instrument fault: no player sidecar (%s)' % r.stderr.strip())
    tx = json.load(open(tx_path))
    _log(dirs, name, 'tx', sent=tx.get('sent'), state=tx.get('state'), shot=tx.get('shot'),
         level_dbm=tx.get('vsg_level_dbm'), offset_hz=tx.get('vsg_offset_hz'),
         fault=(None if tx.get('sent') is True else 'the player did not send'))
    shot = os.path.join(a.shots, tx['shot'] + '.cf32')
    shot_side = bb._load(os.path.join(a.shots, tx['shot'] + '.json'))
    cap = os.path.join(here, name + '.cf32')

    t0 = time.time()
    snr = bb.snr(cap, shot, shot_side)
    with open(os.path.join(here, name + '.snr.json'), 'w') as f:
        json.dump(snr, f, indent=1)
    print('snr:', bb.snr_summary(snr), '(%.0f s)' % (time.time() - t0), flush=True)
    _log(dirs, name, 'snr', problem=snr['problem'], snr_db=snr.get('snr_db'), snr_db_min=snr.get('snr_db_min'),
         snr_db_max=snr.get('snr_db_max'), noise_1mhz_dbm=snr.get('noise_1mhz_dbm'), code=_commit(ROOT))

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
        # Refused on its command line (a sidecar not sent, say): no report, so nothing to copy keys from.
        view = None
        print('align wrote no report (exit %d): refused before reading the recording' % r.returncode, flush=True)
        _log(dirs, name, 'align', exit=r.returncode, allowed=None, fault='align wrote no report')
    else:
        k = subprocess.run([PY, '-I', a.allowed_keys, os.path.join(here, name + '.align.json'), allowed],
                           capture_output=True, text=True)
        if k.returncode:
            _log(dirs, name, 'align', exit=r.returncode,
                 fault='the allowed-keys script failed: ' + k.stderr.strip()[-300:])
            sys.exit('the allowed-keys script failed; nothing copied or deleted')
        view = json.load(open(allowed))
    if view is not None:
        _report_align(dirs, name, r.returncode, view, a.allowed_keys)

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


def _report_align(dirs, name, code, view, script):
    print('align (allowed keys only):', json.dumps(view), flush=True)
    _log(dirs, name, 'align', exit=code, allowed=view, allowed_keys_script=script,
         allowed_keys_commit=_commit(os.path.dirname(os.path.abspath(script))))


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


def cmd_verify(a):
    bad = 0
    n = 0
    for line in open(os.path.join(a.folder, SUMS)):
        h, f = line.rstrip('\n').split('  ', 1)
        got = sha256_from_disk(os.path.join(a.folder, f))
        n += 1
        ok = got == h
        bad += not ok
        print('%s: %s' % (f, 'OK (from the disk)' if ok else 'FAILED (%s)' % got[:16]), flush=True)
    print('%d of %d files match' % (n - bad, n))
    return 1 if bad else 0


def _commit(path):
    r = subprocess.run(['git', '-C', path, 'rev-parse', 'HEAD'], capture_output=True, text=True)
    return r.stdout.strip() or None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    for c in ('start', 'finish'):
        p = sub.add_parser(c)
        p.add_argument('name')
        p.add_argument('--dir', required=True, help='this laptop\'s folder for the recording')
        p.add_argument('--drive', required=True, help='the folder on the 4 TB drive the recording goes to')
        p.add_argument('--player-host', default='worklaptop1-fast')
        p.add_argument('--player-tx-dir', default='rg/bench/tx', help='the player\'s sidecar folder, under its home')
        if c == 'start':
            p.add_argument('--lap', required=True, choices=sorted(bb.SHOTS))
            p.add_argument('--offset-khz', type=float, required=True)
            p.add_argument('--level', type=float, required=True, help='dBm at the VSG60A, logged and put in the '
                           'printed player command; this script never transmits')
            p.add_argument('--seconds', type=float, default=SECONDS)
        else:
            p.add_argument('--shots', default='/media/user/4TB/sdr-synth-tmp/bench/shots')
            p.add_argument('--allowed-keys', required=True, help='the registration\'s s4_bench_allowed_keys.py')
    p = sub.add_parser('verify')
    p.add_argument('folder')
    a = ap.parse_args(argv)
    return {'start': cmd_start, 'finish': cmd_finish, 'verify': cmd_verify}[a.cmd](a)


if __name__ == '__main__':
    sys.exit(main())
