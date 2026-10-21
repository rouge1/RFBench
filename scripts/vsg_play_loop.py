#!/usr/bin/env python3
"""Loop any cf32 file from the VSG60A, into the BB60D over a cable.

    python scripts/vsg_play_loop.py FILE.cf32 --rate 15.36e6 --center-mhz 751 --level -40 --check
    timeout -k 15 75 python scripts/vsg_play_loop.py FILE.cf32 --rate 15.36e6 --center-mhz 751 \
        --level -40 --pad-db 0 --seconds 60 --go

**Never radiated.** Antennas off, an SMA cable from the VSG60A to the BB60D (``knowledge/bluetooth.md``, the bench),
whatever the band: a test signal for any service goes over the cable. The limit is ``bt_bench_play.py``'s, the level
at the BB60D's input: ``--go`` needs ``--pad-db``, the attenuation actually in the cable (0 for a bare cable), and
refuses a level whose level minus pad is above ``MAX_RX_DBM`` (-20 dBm); a level over 0 dBm or not a finite number is
refused too (``bt_tx_hop.level_problem``).

``--level`` is the power of a sample of |iq| 1, as everywhere the VSG60A is driven here: the bench shots are scaled so
a burst's |iq| is 1 and their level is the burst's power. A file whose peak |iq| is over 1 is refused, since the VSG60A
would clip it; ``--normalize`` scales the file so its peak is 1 instead. Either way the file's mean power,
``--level`` plus 10 log10 of the mean |iq|^2, is printed and kept in the sidecar: for an OFDM file at peak 1 it is
some 10 dB under the level. The VSG60A's level is specified only from -55 to +7 dBm (CW, +-2 dB); the level a
receiver measures is the one to trust.

The file is one loop, repeated from the VSG60A's own memory (``repeat_waveform``, as ``bt_tx.py``), so the timing is
the VSG60A's clock and nothing can underrun. A file at most ``MAX_SECONDS`` long, at a rate the VSG60A takes (12.5 kS/s
to 50 MS/s, ``vsg_sink``'s clamp) on a centre from 30 MHz to 6 GHz. The repeat starts within about 5 s of the open,
and the first play after a long idle has taken about 18 s (``knowledge/bluetooth.md``), so start a capture once
"transmitting" is printed, not before.

Nothing transmits without ``--go``, and ``--go`` needs ``--seconds`` (at most ``MAX_RUN_S``). SIGTERM, SIGHUP and
SIGINT only raise a flag that the loop looks at every 0.2 s, and then the repeat is stopped and the sidecar written,
because a repeat outlives a process killed outright; ``timeout -k`` gives it ``KILL_MARGIN_S`` more than
``--seconds``. The sidecar, ``<name>_tx.json`` beside the file (or in ``--out``), is written before the open ("not
started"), once the repeat is on ("transmitting") and after the stop ("stopped", or "error"), never over an older one.
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
from scripts.bt_bench_play import pad_problem  # noqa: E402
from scripts.bt_tx_hop import KILL_MARGIN_S, level_problem, say  # noqa: E402

#: ``vsg_sink``'s clamps: the VSG60A's usable tuning range and sample rates.
MIN_CENTER_MHZ, MAX_CENTER_MHZ = 30.0, 6000.0
MIN_RATE, MAX_RATE = 12.5e3, 50e6
#: A loop longer than this is a typing error; a 0.5 s LTE frame set is 7.7 M samples at 15.36 MS/s.
MAX_SECONDS = 10.24
#: The longest run, under ``timeout -k``.
MAX_RUN_S = 600.0


def _write(path, side):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(side, f, indent=1)
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('file', help='a cf32 file (complex64, little endian): one loop')
    ap.add_argument('--rate', type=float, required=True, help='the file\'s sample rate, S/s')
    ap.add_argument('--center-mhz', type=float, required=True, help='the VSG60A\'s centre, MHz')
    ap.add_argument('--level', type=float, default=-40.0, help='dBm for a sample of |iq| 1; default -40')
    ap.add_argument('--pad-db', type=float, help='the attenuation in the cable, required with --go (0 for a bare cable)')
    ap.add_argument('--normalize', action='store_true', help='scale the file so its peak |iq| is 1')
    ap.add_argument('--seconds', type=float, help='how long to transmit; required with --go')
    ap.add_argument('--name', help='the sidecar is <name>_tx.json; default the file\'s name')
    ap.add_argument('--out', help='folder for the sidecar; default the file\'s')
    ap.add_argument('--check', action='store_true', help='check the file and the limits; never open the radio')
    ap.add_argument('--go', action='store_true', help='loop the file on the VSG60A for --seconds')
    args = ap.parse_args(argv)

    if args.go and args.check:
        ap.error('give --go or --check, not both')
    problem = level_problem(args.level)
    if problem:
        ap.error(problem)
    if not (math.isfinite(args.rate) and MIN_RATE <= args.rate <= MAX_RATE):
        ap.error('--rate %s is not from %g to %g S/s' % (args.rate, MIN_RATE, MAX_RATE))
    if not (math.isfinite(args.center_mhz) and MIN_CENTER_MHZ <= args.center_mhz <= MAX_CENTER_MHZ):
        ap.error('--center-mhz %s is not from %g to %g' % (args.center_mhz, MIN_CENTER_MHZ, MAX_CENTER_MHZ))
    if args.go or args.pad_db is not None:
        problem = pad_problem(args.pad_db, args.level)
        if problem:
            ap.error(problem)
    if args.go and not (args.seconds is not None and math.isfinite(args.seconds) and 0 < args.seconds <= MAX_RUN_S):
        ap.error('--go needs --seconds, from 0 to %.0f' % MAX_RUN_S)
    if not args.go and not args.check:
        print('nothing sent: give --go to transmit, or --check')
        return 0

    stop_now = []

    def on_signal(signum, _frame):
        stop_now.append(signal.Signals(signum).name)

    if args.go:
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            signal.signal(sig, on_signal)

    size = os.path.getsize(args.file)
    if size % 8 or not size:
        ap.error('%s has %d bytes, not a whole number of cf32 samples' % (args.file, size))
    if size // 8 / args.rate > MAX_SECONDS:
        ap.error('the file is %.2f s at %g S/s, longer than %.2f s' % (size // 8 / args.rate, args.rate, MAX_SECONDS))
    iq = np.fromfile(args.file, dtype='<c8')
    sha = hashlib.sha256(memoryview(iq).cast('B')).hexdigest()
    if not np.isfinite(iq.view(np.float32)).all():
        ap.error('the file holds a sample that is not a finite number')
    peak = float(np.abs(iq).max())
    if not peak > 0:
        ap.error('the file is all zeros')
    scale = 1.0
    if args.normalize:
        scale = 1.0 / peak
        iq = (iq * scale).astype(np.complex64)
    elif peak > 1.0 + 1e-6:
        ap.error('the file\'s peak |iq| is %.4g, over 1: the VSG60A would clip it (give --normalize)' % peak)
    mean_dbm = args.level + 10 * math.log10(float(np.mean(np.abs(iq) ** 2)))
    seconds = len(iq) / args.rate

    name = args.name or os.path.splitext(os.path.basename(args.file))[0]
    out = args.out or os.path.dirname(os.path.abspath(args.file))
    os.makedirs(out, exist_ok=True)
    side_path = os.path.join(out, name + ('_tx.json' if args.go else '_check.json'))
    if args.go and os.path.exists(side_path):
        ap.error('%s already exists: give each run its own --name' % side_path)
    side = {
        'name': name, 'file': os.path.basename(args.file), 'file_sha256': sha, 'samples': len(iq),
        'loop_seconds': seconds, 'player': 'SDR scripts/vsg_play_loop.py', 'vsg_center_mhz': args.center_mhz,
        'vsg_sample_rate': args.rate, 'vsg_level_dbm': args.level, 'file_peak': peak, 'scale': scale,
        'mean_power_dbm': mean_dbm, 'pad_db': args.pad_db,
        'level_at_receiver_dbm': None if args.pad_db is None else args.level - args.pad_db,
        'mode': 'repeat_waveform: the file loops from the VSG60A\'s memory', 'vsg_reference': 'internal (free-running)',
        'state': 'not started'}
    summary = ('%s: %d samples (%.3f s a loop), peak |iq| %.4g%s; mean %.1f dBm at %.1f dBm level, %.6f MHz, %g S/s'
               % (side['file'], len(iq), seconds, peak, ', scaled to 1' if args.normalize else '', mean_dbm,
                  args.level, args.center_mhz, args.rate))
    if args.check:
        side.update(state='check: never sent')
        _write(side_path, side)
        print(summary)
        print(side_path)
        return 0

    say(summary)
    say('run it under timeout -k %d (the seconds plus %d): a repeat outlives a process killed outright'
        % (int(math.ceil(args.seconds + KILL_MARGIN_S)), KILL_MARGIN_S))
    _write(side_path, side)
    # vsg_sink prints once the device is open; into a terminal that is gone that print would raise with the radio on.
    held = io.StringIO()
    try:
        from apps import vsg_sink
        with contextlib.redirect_stdout(held):
            sink = vsg_sink.vsg_sink(center_freq=args.center_mhz * 1e6, sample_rate=args.rate, level_dbm=args.level)
    except Exception as e:                                    # noqa: BLE001
        say(held.getvalue().rstrip())
        side.update(error='%s: %s' % (type(e).__name__, e), state='not started: the VSG60A could not be opened')
        _write(side_path, side)
        raise
    say(held.getvalue().rstrip())
    try:
        side['vsg_serial'] = sink.get_serial()
        if not stop_now:
            sink.repeat_waveform(iq)
            side.update(tx_start_unix=time.time(), state='transmitting')
            _write(side_path, side)
            say('transmitting %.1f s at %.1f dBm, %.6f MHz, started %.3f' % (
                args.seconds, args.level, args.center_mhz, side['tx_start_unix']))
            end = time.time() + args.seconds
            while time.time() < end and not stop_now:
                time.sleep(0.2)
                if not sink.waveform_active():
                    side['stopped_on_its_own'] = True
                    say('the waveform stopped on its own')
                    break
        if stop_now:
            side['interrupted_by'] = stop_now[0]
            say('%s: stopping early' % stop_now[0])
        side['state'] = 'stopped'
    except Exception as e:                                    # noqa: BLE001
        side.update(error='%s: %s' % (type(e).__name__, e), state='error')
        raise
    finally:
        sink.stop_waveform()
        sink.stop()
        side['tx_stop_unix'] = time.time()
        _write(side_path, side)
        say('stopped')
        say(side_path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
