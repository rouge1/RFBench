#!/usr/bin/env python3
"""scripts/remote_tx.py and scripts/vsg_stream_file.py, with no radio and no network.

    python scripts/test_remote_tx.py

What it pins:

- the guards: no host, a VSG60 with no level, a level over the ceiling (raised only by
  --max-dbm, never past +10), --secs out of range, an N210 with no --power or --addr;
- without --go nothing runs, and the plan names the staging folder, timeout -k, the VSG lock
  and the rewritten paths;
- local files named in --set (bare, or inside a JSON list) become their copies on the host,
  numbered where two share a name; strings that are not files stay as they are;
- the staging folder that gets removed must look like /tmp/rftx-XXXXXX;
- the run itself, through a stand-in ``ssh`` that runs the command here: the remote PID is
  caught, "ON AIR" is said on CHECK radio, the exit status comes back; SIGTERM here stops the
  remote run by that PID within seconds, and one that ignores SIGTERM gets SIGKILL; a run
  whose radio never comes up is stopped;
- vsg_stream_file refuses a level over its ceiling, --go without --seconds, a clipping file,
  and a file that is not cf32, and --check reads a file without opening the radio.
"""
import contextlib
import io
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import remote_tx as rt          # noqa: E402

RESULTS = []


def report(name, ok, detail=''):
    RESULTS.append((name, ok))
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ('' if ok else f"  ({detail})"), flush=True)


def refused(argv):
    try:
        rt.parse(argv)
    except SystemExit as e:
        return e.code == 2
    return False


def main():
    os.environ.pop('TX_HOST', None)
    app = ['app', 'dvbt2Xmitter', '--radio', 'vsg', '--fkey', 'cf', '--freq', '594', '--pkey', 'pwr']
    report("no host is refused", refused(['--secs', '60', *app, '--level-dbm', '-10']))
    h = ['--host', 'bench']
    report("a VSG60 with no level is refused", refused([*h, '--secs', '60', *app]))
    report("-10 dBm is allowed", not refused([*h, '--secs', '60', *app, '--level-dbm', '-10']))
    report("+3 dBm is over the default ceiling", refused([*h, '--secs', '60', *app, '--level-dbm', '3']))
    report("+3 dBm passes with --max-dbm 5",
           not refused([*h, '--max-dbm', '5', '--secs', '60', *app, '--level-dbm', '3']))
    report("+12 dBm is refused even with --max-dbm 20",
           refused([*h, '--max-dbm', '20', '--secs', '60', *app, '--level-dbm', '12']))
    report("--power 100 on the VSG60 is +10 dBm, refused",
           refused([*h, '--secs', '60', *app, '--power', '100']))
    report("--secs 0 and 4000 are refused",
           refused([*h, '--secs', '0', *app, '--level-dbm', '-10'])
           and refused([*h, '--secs', '4000', *app, '--level-dbm', '-10']))
    usrp = ['app', 'fmRdsTransmitter', '--radio', 'usrp', '--fkey', 'frequency_mhz', '--freq', '915',
            '--pkey', 'power_percent']
    report("an N210 with no --power or --addr is refused",
           refused([*h, '--secs', '60', *usrp, '--addr', 'n210']) and refused([*h, '--secs', '60', *usrp, '--power', '10']))

    with tempfile.TemporaryDirectory() as tmp:
        a1 = os.path.join(tmp, 'clip.mp4')
        os.makedirs(os.path.join(tmp, 'b'))
        a2 = os.path.join(tmp, 'b', 'clip.mp4')
        for p in (a1, a2):
            open(p, 'w').write('x')
        sets = [f'programmes={json.dumps(["<colour bars>", a1, a2])}', f'audio={a1}', 'title=clip.mp4']
        a = rt.parse([*h, '--secs', '60', *app, '--level-dbm', '-10',
                      *sum((['--set', s] for s in sets), [])])
        files = rt.local_files(a)
        names = rt.remote_names(files, '/tmp/rftx-ABCDEFGH')
        report(f"the local files found, once each: {[os.path.relpath(f, tmp) for f in files]}",
               files == [a1, a2])
        report("two files with one name get two copies",
               names == {a1: '/tmp/rftx-ABCDEFGH/media/clip.mp4', a2: '/tmp/rftx-ABCDEFGH/media/clip-1.mp4'},
               names)
        argv = rt.remote_argv(a, names)
        got = [argv[i + 1] for i, x in enumerate(argv) if x == '--set']
        want = ['programmes=' + json.dumps(["<colour bars>", '/tmp/rftx-ABCDEFGH/media/clip.mp4',
                                            '/tmp/rftx-ABCDEFGH/media/clip-1.mp4']),
                'audio=/tmp/rftx-ABCDEFGH/media/clip.mp4', 'title=clip.mp4']
        report("--set paths rewritten, in a JSON list and bare; a non-file left alone", got == want, got)
        cmd = rt.run_command(a, '/tmp/rftx-ABCDEFGH', argv)
        report("the remote line: staging folder, PID first, timeout -k, the host's VSG lock",
               cmd.startswith('cd /tmp/rftx-ABCDEFGH && ') and 'echo "RFTX_PID $$"' in cmd
               and 'exec timeout -k 10 180 python scripts/app_tx_check.py' in cmd
               and 'export VSG60_LOCK="$( [ -d /data/python/SDR/config ] && echo /data/python/SDR/config '
               '|| echo /tmp )/.vsg60.lock"' in cmd and ';' not in cmd,
               cmd)

        out = subprocess.run([sys.executable, os.path.join(ROOT, 'scripts', 'remote_tx.py'),
                              *h, '--secs', '60', *app, '--level-dbm', '-10', '--set', f'audio={a1}'],
                             capture_output=True, text=True, timeout=60)
        report("without --go: a plan, exit 0, no ssh",
               out.returncode == 0 and 'nothing done without --go' in out.stdout
               and f'copy {a1} -> /tmp/rftx-XXXXXXXX/media/clip.mp4' in out.stdout, out.stdout + out.stderr)

    ok = True
    for bad in ('/tmp', '/tmp/rftx-', '/tmp/rftx-AB', '/home/user', '/tmp/rftx-ABCDEFGH/..', ''):
        try:
            rt.safe_stage(bad)
            ok = False
        except ValueError:
            pass
    report("only /tmp/rftx-XXXXXX is ever removed", ok and rt.safe_stage('/tmp/rftx-AbC123xy'))

    # The run, through an ssh that runs the command here (options and host dropped).
    with tempfile.TemporaryDirectory() as tmp:
        bindir = os.path.join(tmp, 'bin')
        os.makedirs(bindir)
        fake = os.path.join(bindir, 'ssh')
        open(fake, 'w').write('#!/bin/bash\nwhile [ "${1:0:1}" = "-" ]; do [ "$1" = -o ] && shift; shift; done\n'
                              'shift\nexec bash -c "$*"\n')
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
        os.environ['PATH'] = bindir + os.pathsep + os.environ['PATH']
        stage = tempfile.mkdtemp(prefix='rftx-', dir=tmp)

        def run(prog, secs=5, start_s=None, kill_after=None):
            a = SimpleNamespace(host='bench', secs=secs, repo=tmp)
            saved = rt.START_S
            if start_s is not None:
                rt.START_S = start_s
            if kill_after is not None:
                threading.Timer(kill_after, os.kill, (os.getpid(), signal.SIGTERM)).start()
            t0 = time.time()
            try:
                code = rt.transmit(a, stage, [sys.executable, '-c', prog], os.path.join(tmp, 'log'))
            finally:
                rt.START_S = saved
            return code, time.time() - t0, open(os.path.join(tmp, 'log')).read()

        code, took, log = run('import os; print("CHECK radio", os.environ["VSG60_LOCK"], flush=True)')
        report("the lock resolves to /tmp's when --repo has no config/",
               code == 0 and 'CHECK radio /tmp/.vsg60.lock' in log, log)
        os.makedirs(os.path.join(tmp, 'config'))
        code, took, log = run('import os; print("CHECK radio", os.environ["VSG60_LOCK"], flush=True)')
        report("and to the checkout's own when it has one",
               code == 0 and f'CHECK radio {tmp}/config/.vsg60.lock' in log, log)
        real_stage = stage
        stage = os.path.join(tmp, 'rftx-gone')
        code, took, log = run('print("CHECK radio ran anyway", flush=True)')
        report("a staging folder that is not there runs nothing", code is None and 'ran anyway' not in log, log)
        stage = real_stage
        code, took, log = run('print("CHECK radio {}", flush=True); print("CHECK RESULT x 0")')
        report(f"a run: PID caught, on the air, exit {code} in {took:.1f} s",
               code == 0 and 'RFTX_PID' in log and 'CHECK RESULT x 0' in log, log)
        code, took, log = run('import time; print("CHECK radio {}", flush=True); time.sleep(60)',
                              secs=60, kill_after=2)
        report(f"SIGTERM here stops the remote run: {took:.1f} s, exit {code}",
               took < 10 and code not in (0, None), log)
        saved = rt.KILL_MARGIN_S, rt.STOP_WAIT_S
        rt.KILL_MARGIN_S, rt.STOP_WAIT_S = 120, 2
        said = io.StringIO()
        try:
            with contextlib.redirect_stdout(said):
                code, took, log = run('import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); '
                                      'print("CHECK radio {}", flush=True); time.sleep(60)', secs=60, kill_after=2)
        finally:
            rt.KILL_MARGIN_S, rt.STOP_WAIT_S = saved
        report(f"a run that ignores SIGTERM gets SIGKILL: {took:.1f} s, exit {code}",
               took < 15 and code not in (0, None) and 'SIGKILL' in said.getvalue(), log + said.getvalue())
        code, took, log = run('import sys, time; sys.stdout.write("first\\nCHECK radio {}\\n"); '
                              'sys.stdout.flush(); time.sleep(4); print("CHECK RESULT x 0")', start_s=2)
        report(f"two lines in one write: CHECK radio seen at once, not stopped (exit {code})",
               code == 0 and 'stopping' not in log, log)
        code, took, log = run('import time; time.sleep(60)', secs=60, start_s=2)
        report(f"no CHECK radio: stopped after {took:.1f} s, reported as never up",
               code is None and took < 10, log)

    # vsg_stream_file: its guards and --check, with no radio.
    with tempfile.TemporaryDirectory() as tmp:
        good, loud, odd = (os.path.join(tmp, n) for n in ('good.cf32', 'loud.cf32', 'odd.cf32'))
        rng = np.random.default_rng(1)
        x = (rng.standard_normal(500_000) + 1j * rng.standard_normal(500_000)).astype(np.complex64) * 0.14
        x.tofile(good)
        (x * 10).tofile(loud)
        open(odd, 'wb').write(b'\0' * 12)
        base = ['--rate', '10e6', '--center-mhz', '594']

        def code(argv):
            p = subprocess.run([sys.executable, os.path.join(ROOT, 'scripts', 'vsg_stream_file.py'), *argv],
                               capture_output=True, text=True, timeout=60)
            return p.returncode, p.stdout
        c, o = code([good, *base, '--level-dbm', '-10', '--check'])
        report("stream --check on a good file: rms and peak, nothing transmitted",
               c == 0 and '"rms": 0.19' in o and 'nothing transmitted' in o, o)
        report("stream: +3 dBm refused by default", code([good, *base, '--level-dbm', '3', '--check'])[0] == 2)
        report("stream: --go without --seconds refused", code([good, *base, '--level-dbm', '-10', '--go'])[0] == 2)
        c, o = code([loud, *base, '--level-dbm', '-10', '--check'])
        report("stream: a file that would clip is refused", c == 2 and 'would clip' in o, o)
        c, o = code([odd, *base, '--level-dbm', '-10', '--check'])
        report("stream: a file that is not cf32 is refused", c == 2 and 'not a whole number' in o, o)

    bad = [n for n, ok in RESULTS if not ok]
    print(f"{len(RESULTS) - len(bad)} ok, {len(bad)} FAIL of {len(RESULTS)}")
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
