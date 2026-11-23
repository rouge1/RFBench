#!/usr/bin/env python3
"""Transmit from a radio on another machine: this working tree, run there, over ssh.

    # a transmitter app (any app_tx_check.py arguments), 10 minutes
    scripts/remote_tx.py --host worklaptop1-fast --secs 600 --go app dvbt2Xmitter \\
        --radio vsg --fkey cf --freq 594 --pkey pwr --level-dbm -10 --no-callbacks \\
        --set modulation=16QAM --set code_rate=3/5 \\
        --set 'programmes=["<colour bars>", "/data/media/SDR/Big-Buck-Bunny-Opening.mp4"]'

    # a recording, looped (scripts/vsg_stream_file.py)
    scripts/remote_tx.py --host worklaptop1-fast --secs 600 --go stream bkk36_10M.cf32 \\
        --rate 10e6 --freq 594 --level-dbm -4

Without ``--go`` it prints what it would run and stops: nothing is copied and nothing
transmitted. ``--host`` defaults to ``$TX_HOST``. The receiving end is yours to run (the
BB60D here, or another session's app); ``app_pair.sh`` is the one that judges a pair.

What it does, in order:

1. **Checks the host:** the radio is on its USB (the VSG60, 2817:0008), and nothing else is
   transmitting there: no ``app_tx_check``, ``app_rx_check``, ``_run``, ``vsg_stream_file``,
   ``tx_waveforms`` or launcher. If anything is found, it stops before copying.
2. **Stages this working tree** (``RFbenchToolkit.py``, ``apps/``, ``scripts/``, uncommitted
   changes included) into a new ``/tmp/rftx-XXXXXX`` on the host, and copies there any local
   file named in the arguments: each ``--set`` value, every string inside a JSON one, and the
   ``stream`` file. Their paths are rewritten to the copies. The host's own checkout is never
   touched.
3. **Runs it under ``timeout -k``** with ``VSG60_LOCK`` pointing at the host's checkout's lock
   (``--repo``, default ``/data/python/SDR``; ``/tmp/.vsg60.lock`` if it has no ``config/``).
   The staged copy, that host's launcher and any other run of this script then see each
   other (``apps/vsg_sink.py``). The output comes back line by line, and is also kept in
   ``--out``.
4. **Says when it is on the air** (the ``CHECK radio`` line) and until when. If that line has
   not come within ``START_S`` it stops the run.
5. **Stops cleanly:** Ctrl-C or SIGTERM here sends SIGTERM to the remote run, by its PID;
   ``app_tx_check.py`` and ``vsg_stream_file.py`` both end on it within a second. If it is
   still there ``STOP_WAIT_S`` later, its processes get SIGKILL.
   Afterwards it checks that nothing is left transmitting, then removes the staging folder
   (``--keep`` to leave it).

**It radiates.** ``--go`` is needed. A VSG60 level is required, ``--level-dbm`` or ``--power``,
and refused above ``--max-dbm`` (``MAX_LEVEL_DBM`` unless raised; never above +10). The N210's
own ceiling is ``app_tx_check.py``'s ``--max-power``. Every over-the-air run needs the user's
go for that run, and the other sessions using the radios get told first (CLAUDE.md).
"""
import argparse
import json
import os
import posixpath
import queue
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGE_PREFIX = '/tmp/rftx-'
STAGE_RE = re.compile(r'^/tmp/rftx-[A-Za-z0-9]{6,}$')
ENV = 'source ~/miniconda3/etc/profile.d/conda.sh && conda activate gnu'
#: A python running one of these, or tx_waveforms or the launcher, means the host is busy.
BUSY_PATTERN = (r'^[^ ]*python[0-9.]* [^ ]*(app_tx_check|app_rx_check|_run|vsg_stream_file'
                r'|RFbenchToolkit)\.py|^[^ ]*tx_waveforms')
USB_ID = {'vsg': '2817:0008'}
VSG_LO_DBM, VSG_HI_DBM = -120.0, 10.0
MAX_LEVEL_DBM = 0.0
MAX_SECS = 1800.0
#: How long the radio has to come up: dvbt2Xmitter on worklaptop1 takes about 6 s, and the first
#: play after a long idle has taken ~18 s (knowledge/bluetooth.md).
START_S = 120.0
KILL_MARGIN_S = 10
#: How long a stopped run has to end before its processes get SIGKILL from here.
STOP_WAIT_S = 2 * KILL_MARGIN_S


def say(*a):
    print('[remote_tx]', *a, flush=True)


# ------------------------------------------------------------------ arguments

def parse(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--host', default=os.environ.get('TX_HOST'), help='ssh alias (default $TX_HOST)')
    p.add_argument('--secs', type=float, required=True, help='how long to transmit')
    p.add_argument('--go', action='store_true', help='transmit; without it, only print the plan')
    p.add_argument('--repo', default='/data/python/SDR', help="the host's checkout, for its VSG lock")
    p.add_argument('--max-dbm', type=float, default=MAX_LEVEL_DBM,
                   help=f'ceiling for the VSG60 level (default {MAX_LEVEL_DBM:g}, never above {VSG_HI_DBM:g})')
    p.add_argument('--out', help='folder for the log here (default a new temporary one)')
    p.add_argument('--keep', action='store_true', help='leave the staging folder on the host')
    sub = p.add_subparsers(dest='mode', required=True)

    ap = sub.add_parser('app', help='a transmitter app, through scripts/app_tx_check.py')
    ap.add_argument('module')
    ap.add_argument('--radio', choices=('vsg', 'usrp'), required=True)
    ap.add_argument('--addr', default='')
    ap.add_argument('--fkey', required=True)
    ap.add_argument('--freq', type=float, required=True, help='MHz')
    ap.add_argument('--pkey', required=True)
    ap.add_argument('--power', type=float, help='percent')
    ap.add_argument('--level-dbm', type=float, help='VSG60 level')
    ap.add_argument('--max-power', type=float, help="app_tx_check.py's ceiling for the N210")
    ap.add_argument('--set', action='append', default=[], metavar='KEY=VALUE')
    ap.add_argument('--no-callbacks', action='store_true')

    sp = sub.add_parser('stream', help='a cf32 recording, looped, through scripts/vsg_stream_file.py')
    sp.add_argument('file')
    sp.add_argument('--rate', type=float, required=True)
    sp.add_argument('--freq', type=float, required=True, help='MHz')
    sp.add_argument('--level-dbm', type=float, required=True)
    a = p.parse_args(argv)
    bad = problems(a)
    if bad:
        p.error('; '.join(bad))
    return a


def vsg_level(a):
    """The VSG60 level the run asks for, in dBm, or None if it is not a VSG60 run."""
    if a.mode == 'stream':
        return a.level_dbm
    if a.radio != 'vsg':
        return None
    if a.level_dbm is not None:
        return a.level_dbm
    if a.power is not None:
        return VSG_LO_DBM + a.power / 100 * (VSG_HI_DBM - VSG_LO_DBM)
    return None


def problems(a):
    out = []
    if not a.host:
        out.append('give --host or set TX_HOST (an ssh alias)')
    if not (0 < a.secs <= MAX_SECS):
        out.append(f'--secs must be more than 0 and at most {MAX_SECS:g}')
    radio = 'vsg' if a.mode == 'stream' else a.radio
    if radio == 'vsg':
        level = vsg_level(a)
        ceiling = min(a.max_dbm, VSG_HI_DBM)
        if level is None:
            out.append('the VSG60 needs a level: --level-dbm (or --power)')
        elif level > ceiling:
            out.append(f'a level of {level:g} dBm is over the ceiling of {ceiling:g} dBm (--max-dbm)')
    elif a.mode == 'app':
        if a.power is None:
            out.append('the N210 needs --power')
        if not a.addr:
            out.append('the N210 needs --addr')
    return out


# ------------------------------------------------------------------ the plan

def local_files(a):
    """Local files the run names, in order, without repeats: the stream file, every --set
    value that is a file, and every string inside a JSON --set value that is one."""
    found = []

    def add(v):
        if isinstance(v, str) and os.path.isfile(v) and v not in found:
            found.append(v)
    if a.mode == 'stream':
        if not os.path.isfile(a.file):
            raise FileNotFoundError(a.file)
        add(a.file)
    for kv in getattr(a, 'set', []):
        _, v = kv.split('=', 1)
        add(v)
        try:
            j = json.loads(v)
        except ValueError:
            continue
        for item in (j if isinstance(j, list) else j.values() if isinstance(j, dict) else []):
            add(item)
    return found


def remote_names(files, stage):
    """Each local file's path on the host: STAGE/media/<name>, numbered where two share a name."""
    out, used = {}, set()
    for f in files:
        name = os.path.basename(f)
        k = 1
        while name in used:
            stem, ext = os.path.splitext(os.path.basename(f))
            name = f'{stem}-{k}{ext}'
            k += 1
        used.add(name)
        out[f] = posixpath.join(stage, 'media', name)
    return out


def rewrite_set(kv, names):
    """A --set KEY=VALUE with its local file paths replaced by their copies on the host."""
    k, v = kv.split('=', 1)
    if v in names:
        return f'{k}={names[v]}'
    try:
        j = json.loads(v)
    except ValueError:
        return kv
    if isinstance(j, list):
        j = [names.get(x, x) if isinstance(x, str) else x for x in j]
    elif isinstance(j, dict):
        j = {kk: names.get(x, x) if isinstance(x, str) else x for kk, x in j.items()}
    else:
        return kv
    return f'{k}={json.dumps(j)}'


def remote_argv(a, names):
    """The command run on the host, from the staging folder, as a list."""
    if a.mode == 'stream':
        return ['python', 'scripts/vsg_stream_file.py', names[a.file], '--rate', repr(a.rate),
                '--center-mhz', repr(a.freq), '--level-dbm', repr(a.level_dbm),
                '--max-dbm', repr(min(a.max_dbm, VSG_HI_DBM)), '--seconds', repr(a.secs), '--go']
    argv = ['python', 'scripts/app_tx_check.py', a.module, '--radio', a.radio,
            '--fkey', a.fkey, '--freq', repr(a.freq), '--pkey', a.pkey, '--secs', repr(a.secs)]
    if a.addr:
        argv += ['--addr', a.addr]
    if a.level_dbm is not None:
        argv += ['--level-dbm', repr(a.level_dbm)]
    else:
        argv += ['--power', repr(a.power)]
    if a.max_power is not None:
        argv += ['--max-power', repr(a.max_power)]
    for kv in a.set:
        argv += ['--set', rewrite_set(kv, names)]
    if a.no_callbacks:
        argv.append('--no-callbacks')
    return argv


def run_command(a, stage, argv):
    """The shell line for ssh: it prints its PID, then becomes ``timeout``, so that PID is the
    one to signal; timeout passes SIGTERM on to the python under it."""
    # One command, no ';': each step runs only if the one before it worked.
    cfg = shlex.quote(a.repo + '/config')
    lock = f'export VSG60_LOCK="$( [ -d {cfg} ] && echo {cfg} || echo /tmp )/.vsg60.lock"'
    limit = int(a.secs) + int(START_S)
    return (f'cd {shlex.quote(stage)} && {ENV} && {lock} && echo "RFTX_PID $$" && '
            f'exec timeout -k {KILL_MARGIN_S} {limit} {shlex.join(argv)} 2>&1')


def safe_stage(stage):
    if not STAGE_RE.match(stage or ''):
        raise ValueError(f'refusing to treat {stage!r} as a staging folder')
    return stage


# ------------------------------------------------------------------ running

def ssh(host, command, timeout=60, check=True):
    r = subprocess.run(['ssh', '-n', '-o', 'ConnectTimeout=10', host, command],
                       capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f'ssh {host} {command!r} failed: {r.stderr.strip() or r.returncode}')
    return r


def host_problems(a):
    out = []
    radio = 'vsg' if a.mode == 'stream' else a.radio
    if radio in USB_ID and not ssh(a.host, f'lsusb -d {USB_ID[radio]}', check=False).stdout.strip():
        out.append(f'no {radio.upper()} ({USB_ID[radio]}) on {a.host}\'s USB')
    busy = ssh(a.host, f"pgrep -af {shlex.quote(BUSY_PATTERN)}", check=False).stdout.strip()
    if busy:
        out.append(f'already running on {a.host}: ' + '; '.join(busy.splitlines()))
    return out


def stage_tree(a, files):
    stage = safe_stage(ssh(a.host, f'mktemp -d {STAGE_PREFIX}XXXXXXXX').stdout.strip())
    ssh(a.host, f'mkdir -p {stage}/config {stage}/media')
    src = [os.path.join(ROOT, x) for x in ('RFbenchToolkit.py', 'apps', 'scripts')]
    subprocess.run(['rsync', '-a', '--exclude', '__pycache__', '--exclude', '*.so', '--exclude', 'config',
                    *src, f'{a.host}:{stage}/'], check=True, timeout=600)
    names = remote_names(files, stage)
    for f, dest in names.items():
        say(f'copying {f} ({os.path.getsize(f) / 1e6:.0f} MB)')
        subprocess.run(['rsync', '-a', f, f'{a.host}:{dest}'], check=True, timeout=3600)
    return stage, names


def transmit(a, stage, argv, log_path):
    """Run it, echo and keep its output, and stop it on Ctrl-C or SIGTERM here. Returns the
    remote exit status, or None if the radio never came up."""
    limit = int(a.secs) + int(START_S) + 2 * KILL_MARGIN_S
    proc = subprocess.Popen(['timeout', '-k', str(KILL_MARGIN_S), str(limit), 'ssh', '-o',
                             'ConnectTimeout=10', a.host, run_command(a, stage, argv)],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    state = {'pid': None, 'on_air': None, 'stopping': False}

    def stop_remote(reason):
        if state['stopping']:
            return
        state['stopping'] = True
        say(f'stopping the run on {a.host} ({reason})')
        if not state['pid']:
            return
        ssh(a.host, f'kill -TERM {state["pid"]}', check=False, timeout=30)

        # timeout -k on the host follows its SIGTERM with SIGKILL; this is the fallback if the
        # run is still there after that: the PID's children, then the PID.
        def escalate():
            time.sleep(STOP_WAIT_S)
            if proc.poll() is None:
                say(f'still running {STOP_WAIT_S:g} s after SIGTERM: SIGKILL')
                pid = state['pid']
                ssh(a.host, f'pkill -KILL -P {pid}; kill -KILL {pid}', check=False, timeout=30)
        threading.Thread(target=escalate, name='remote-kill', daemon=True).start()

    old = {s: signal.signal(s, lambda sig, f: stop_remote(signal.Signals(sig).name))
           for s in (signal.SIGINT, signal.SIGTERM)}
    # A thread reads the lines as they come. select() on the text pipe would see only new
    # bytes, and a second line delivered in one read would wait in the buffer, unseen: that
    # held a CHECK radio back until the start limit stopped a run which had been on the air
    # for minutes (2026-11-22).
    lines = queue.Queue()

    def reader():
        for ln in proc.stdout:
            lines.put(ln)
        lines.put(None)
    threading.Thread(target=reader, name='remote-output', daemon=True).start()
    started = time.time()
    try:
        with open(log_path, 'w') as log:
            while True:
                try:
                    line = lines.get(timeout=1.0)
                except queue.Empty:
                    line = ''
                if line is None:
                    break
                if line:
                    log.write(f'{time.time() - started:7.1f} {line}')
                    log.flush()
                    m = re.match(r'RFTX_PID (\d+)', line)
                    if m:
                        state['pid'] = int(m.group(1))
                        continue
                    print(line, end='', flush=True)
                    if state['on_air'] is None and line.startswith('CHECK radio'):
                        state['on_air'] = time.time()
                        say(f'ON AIR after {time.time() - started:.0f} s, at', time.strftime('%H:%M:%S'), 'until about',
                            time.strftime('%H:%M:%S', time.localtime(time.time() + a.secs)))
                if state['on_air'] is None and time.time() - started > START_S:
                    stop_remote(f'no CHECK radio within {START_S:g} s')
        code = proc.wait()
    finally:
        for s, h in old.items():
            signal.signal(s, h)
    say('off the air at', time.strftime('%H:%M:%S'), f'(exit {code})')
    return code if state['on_air'] else None


def finish(a, stage):
    for _ in range(30):
        if not ssh(a.host, f"pgrep -f {shlex.quote(BUSY_PATTERN)}", check=False).stdout.strip():
            break
        time.sleep(2)
    else:
        say(f'WARNING: something is still running on {a.host}; look before the next run')
    if stage and not a.keep:
        ssh(a.host, f'rm -rf {safe_stage(stage)}', check=False)
        say(f'removed {stage} on {a.host}')
    elif stage:
        say(f'kept {stage} on {a.host}')


def main(argv=None):
    a = parse(argv)
    files = local_files(a)
    plan_stage = STAGE_PREFIX + 'XXXXXXXX'
    names = remote_names(files, plan_stage)
    argv_remote = remote_argv(a, names)
    if not a.go:
        say('plan (nothing done without --go):')
        say(f'  host {a.host}; stage this tree into {plan_stage}')
        for f, d in names.items():
            say(f'  copy {f} -> {d}')
        say('  run: ' + run_command(a, plan_stage, argv_remote))
        return 0
    bad = host_problems(a)
    if bad:
        for b in bad:
            say('NOT STARTING:', b)
        return 1
    out = a.out or tempfile.mkdtemp(prefix='remote_tx-')
    os.makedirs(out, exist_ok=True)
    log_path = os.path.join(out, f'tx-{time.strftime("%Y%m%d-%H%M%S")}.log')
    stage = None
    try:
        stage, names = stage_tree(a, files)
        code = transmit(a, stage, remote_argv(a, names), log_path)
    finally:
        finish(a, stage)
    say(f'log: {log_path}')
    if code is None:
        say('the radio never came up')
        return 1
    return code


if __name__ == '__main__':
    sys.exit(main())
