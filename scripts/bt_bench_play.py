#!/usr/bin/env python3
"""The bench tier's transmitter: play one bench shot once from the VSG60A, into the BB60D over a cable.

    python scripts/bt_bench_play.py SHOT.cf32 --check                         # the shot's hash and limits, no radio
    timeout -k 30 60 python scripts/bt_bench_play.py SHOT.cf32 --go --pad-db 0 --level -70 [--vsg-offset-khz 58]

**Never radiated.** Antennas off, an SMA cable from the VSG60A to the BB60D (``knowledge/bluetooth.md``, the bench).
The bench has no pad, so the limit is the level at the BB60D's input: ``--go`` needs ``--pad-db``, the attenuation
actually in the cable (0 for a bare cable; stated, never assumed), and refuses a level whose level minus pad is above
``MAX_RX_DBM`` (-20 dBm, what +10 dBm through the checklist's 30 dB pad would give: 30 dB under the BB60D's +10 dBm
reference maximum, 40 under its +20 dBm damage level). The level is also refused above ``MAX_LEVEL_DBM`` (0 dBm) or
when it is not a finite number (``bt_tx_hop.level_problem``: ``nan`` compares false against every limit). The
levels the bench needs are far lower: the calibration shot is -90 dBm at the BB60D and 20 dB in 1 MHz about -70.

The shot is ``scripts/bt_bench.py shot``'s, rendered once on the bench machine (numpy differs in the last bit between
versions, so it is never re-rendered here) and copied over. Before the radio is touched the file's sha256 must equal
its sidecar's ``bench_shot.sha256``, its length ``n_samples`` (at most ``MAX_SECONDS``) and its peak |iq| at most 1:
``bt_bench.py align`` writes truth only for the exact samples played, and refuses a ``--tx`` whose ``shot_sha256`` is
another shot's.

The VSG60A is tuned to 2439.0 MHz plus ``--vsg-offset-khz``: a real carrier offset from its own synthesiser, not a
rotation of the samples. The BB60D stays on 2439.0.

Everything about stopping is ``scripts/bt_tx_hop.py``'s, which two reviews hardened: SIGTERM, SIGHUP and SIGINT only
raise a flag; ``send_waveform`` blocks inside the C library and cannot be interrupted, so ``timeout -k`` must exceed the
shot plus 15 s (printed when ``--go`` starts; 30 for a 10 s shot); the sidecar is written before the open ("not
started"), before the send ("pending") and after (sent true, false, or "error"), so a failed run never leaves an older
file standing for it; every print once the sink is open goes through ``say``. A receiver takes only ``sent`` true.
"""
import argparse
import contextlib
import hashlib
import io
import json
import math
import os
import signal
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.bt_tx_hop import KILL_MARGIN_S, kill_after, level_problem, say  # noqa: E402

FS = 40e6
CENTER_MHZ = 2439.0
MAX_LEVEL_DBM = 0.0
#: The most a shot may put into the BB60D, level minus pad.
MAX_RX_DBM = -20.0
#: The longest one-shot buffer the VSG60A has been shown to take at 40 MS/s (stage 3 phase 5).
MAX_SECONDS = 10.24
MAX_OFFSET_KHZ = 200.0


def sha256_of(path, chunk=64 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def shot_problem(path, info):
    """The refusal text for a shot file that is not the one its sidecar describes (size and length only: the hash and
    the peak are taken from the samples once read, ``samples_problem``), or None."""
    n = os.path.getsize(path) // 8
    if os.path.getsize(path) % 8 or n != info['n_samples']:
        return 'the shot has %d bytes; its sidecar says %d samples' % (os.path.getsize(path), info['n_samples'])
    if n / FS > MAX_SECONDS:
        return 'the shot is %.2f s, longer than %.2f s' % (n / FS, MAX_SECONDS)
    return None


def samples_problem(iq, info, chunk=8_000_000):
    """The refusal text for samples whose sha256 is not the sidecar's or whose peak |iq| is over 1, or None. These
    are the samples that will be sent: the file is not read a second time."""
    got = hashlib.sha256(memoryview(iq).cast('B')).hexdigest()
    if got != info['sha256']:
        return 'the shot\'s sha256 is %s, its sidecar says %s: not the file that was rendered' % (got[:16], info['sha256'][:16])
    peak = max(float(np.abs(iq[p:p + chunk]).max()) for p in range(0, len(iq), chunk))
    if not peak <= 1.0 + 1e-6:
        return 'the shot\'s peak |iq| is %r, over 1' % peak
    return None


def pad_problem(pad, level):
    """The refusal text for a pad that is not stated or not a finite number of dB >= 0, or a level that would put more
    than ``MAX_RX_DBM`` into the BB60D through it, or None."""
    if pad is None:
        return '--go needs --pad-db, the attenuation actually in the cable (0 for a bare cable)'
    if not (math.isfinite(pad) and pad >= 0):
        return 'pad %s dB is not a finite number of dB, 0 or more' % pad
    if not level - pad <= MAX_RX_DBM:
        return ('level %.1f dBm through a %.1f dB pad puts %.1f dBm into the BB60D, over the %.0f dBm limit'
                % (level, pad, level - pad, MAX_RX_DBM))
    return None


def _write(path, side):
    text = json.dumps(side, indent=1, allow_nan=False)
    with open(path, 'w') as f:
        f.write(text)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('shot', help="bt_bench.py's bench_<lap>_d23.cf32 (its .json beside it)")
    ap.add_argument('--level', type=float, default=-60.0, help='dBm at the VSG60A output, a burst\'s power; default -60')
    ap.add_argument('--pad-db', type=float, help='the attenuation in the cable, required with --go (0 for a bare cable); '
                    'level minus pad is at most %.0f dBm' % MAX_RX_DBM)
    ap.add_argument('--vsg-offset-khz', type=float, default=0.0, help='VSG60A tuning minus 2439.0 MHz; default 0')
    ap.add_argument('--name', help='the capture this play is for (the sidecar is <name>_tx.json); default the shot\'s')
    ap.add_argument('--out', help='folder for the sidecar; default the shot\'s')
    ap.add_argument('--check', action='store_true', help='check the shot and the limits; never open the radio')
    ap.add_argument('--go', action='store_true', help='play the shot once on the VSG60A')
    args = ap.parse_args(argv)

    if args.go and args.check:
        ap.error('give --go or --check, not both')
    problem = level_problem(args.level)
    if problem:
        ap.error(problem)
    if not (math.isfinite(args.vsg_offset_khz) and abs(args.vsg_offset_khz) <= MAX_OFFSET_KHZ):
        ap.error('--vsg-offset-khz %s is not a finite number within +-%.0f' % (args.vsg_offset_khz, MAX_OFFSET_KHZ))
    if args.go:
        problem = pad_problem(args.pad_db, args.level)
        if problem:
            ap.error(problem)
    if not args.go and not args.check:
        print('nothing sent: give --go to transmit, or --check')
        return 0
    # From here a signal only raises a flag, looked at before the send: the 3.2 GB read and the hash come first.
    stop_now = []

    def on_signal(signum, _frame):
        stop_now.append(signal.Signals(signum).name)

    if args.go:
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            signal.signal(sig, on_signal)
    side_in = os.path.splitext(args.shot)[0] + '.json'
    with open(side_in) as f:
        info = json.load(f)['bench_shot']
    problem = shot_problem(args.shot, info)
    if problem:
        ap.error(problem)
    center_mhz = CENTER_MHZ + args.vsg_offset_khz / 1e3
    name = args.name or info['name']
    out = args.out or os.path.dirname(os.path.abspath(args.shot))
    os.makedirs(out, exist_ok=True)
    side_path = os.path.join(out, name + ('_tx.json' if args.go else '_check.json'))
    # A sidecar is never overwritten: an older one that says sent true would stand for a run that never happened.
    if args.go and os.path.exists(side_path):
        ap.error('%s already exists: give each play its own --name' % side_path)
    seconds = info['n_samples'] / FS
    side = {
        'name': name, 'shot': info['name'], 'shot_file': os.path.basename(args.shot), 'shot_sha256': info['sha256'],
        'shot_samples': info['n_samples'], 'shot_seconds': seconds, 'player': 'SDR scripts/bt_bench_play.py',
        'vsg_center_mhz': center_mhz, 'vsg_offset_hz': args.vsg_offset_khz * 1e3, 'vsg_sample_rate': FS,
        'vsg_level_dbm': args.level, 'pad_db': args.pad_db,
        'level_at_receiver_dbm': None if args.pad_db is None else args.level - args.pad_db,
        'mode': 'send_waveform: one shot', 'vsg_reference': 'internal (free-running)',
        'sent': False, 'state': 'not started'}
    iq = np.fromfile(args.shot, dtype='<c8')
    if len(iq) != info['n_samples']:
        ap.error('read %d samples, expected %d' % (len(iq), info['n_samples']))
    problem = samples_problem(iq, info)
    if problem:
        ap.error(problem)
    if args.check:
        side.update(state='check: never sent')
        _write(side_path, side)
        print('shot %s: %d samples (%.3f s), sha256 %s and peak OK; VSG60A would tune to %.6f MHz at %.1f dBm'
              % (info['name'], info['n_samples'], seconds, info['sha256'][:16], center_mhz, args.level))
        print(side_path)
        return 0
    say('this shot is %.3f s, so run it under timeout -k %d (the shot plus %d s): a SIGKILL inside the send leaves no '
        'RF off and no sidecar' % (seconds, kill_after(seconds), KILL_MARGIN_S))
    _write(side_path, side)
    # vsg_sink prints once the device is open, RF on; into a terminal that is gone that print raises and no sink
    # would come back to be stopped. Its prints go to a buffer here, and through say() after.
    held = io.StringIO()
    try:
        from apps import vsg_sink
        with contextlib.redirect_stdout(held):
            sink = vsg_sink.vsg_sink(center_freq=center_mhz * 1e6, sample_rate=FS, level_dbm=args.level)
    except Exception as e:                                    # noqa: BLE001
        say(held.getvalue().rstrip())
        side.update(sent=False, error='%s: %s' % (type(e).__name__, e),
                    state='not started: the VSG60A could not be opened')
        _write(side_path, side)
        raise
    failure = None
    send_started = False
    try:
        say(held.getvalue().rstrip())
        side['vsg_serial'] = sink.get_serial()
        side['tx_start_unix'] = time.time()
        side.update(sent='pending', state='pending')
        _write(side_path, side)
        say('transmitting %d samples (%.3f s) at %.1f dBm (%.1f dBm at the BB60D through %.0f dB), %.6f MHz, started %.3f' % (
            len(iq), seconds, args.level, args.level - args.pad_db, args.pad_db, center_mhz, side['tx_start_unix']))
        if stop_now:
            say('%s: stopping before the shot' % stop_now[0])
            side.update(sent=False, state='not sent: %s came before the shot' % stop_now[0])
        else:
            send_started = True
            sink.send_waveform(iq)
            side.update(sent=True, state='sent')
            if stop_now:
                say('%s: the shot had already started and could not be cut' % stop_now[0])
        if stop_now:
            side['interrupted_by'] = stop_now[0]
    except Exception as e:                                    # noqa: BLE001
        failure = e
        message = '%s: %s' % (type(e).__name__, e)
        if send_started:
            side.update(sent='error', error=message, state='error in the send: part of the shot may have gone out')
        else:
            side.update(sent=False, error=message, state='error before the send')
    finally:
        try:
            sink.stop_waveform()
        finally:
            sink.stop()
            side['tx_stop_unix'] = time.time()
            _write(side_path, side)
    say(side_path)
    if failure is not None:
        raise failure
    return 0


if __name__ == '__main__':
    sys.exit(main())
