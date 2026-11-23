#!/usr/bin/env python3
"""Stream a cf32 file of any length from the VSG60, looping, for a fixed time.

    python scripts/vsg_stream_file.py FILE.cf32 --rate 10e6 --center-mhz 594 --level-dbm -10 --check
    timeout -k 10 640 python scripts/vsg_stream_file.py FILE.cf32 --rate 10e6 --center-mhz 594 \\
        --level-dbm -10 --seconds 600 --go

Run on the VSG60's host, or from anywhere through ``scripts/remote_tx.py stream``, which copies
the file across and does the rest. Made for replaying a recording over the air: the Bangkok
DVB-T2 multiplex (``knowledge/dvbt2.md``) was 19.5 s at 10 MS/s, 1.56 GB.

**How it differs from ``vsg_play_loop.py``:** that script loops at most 10.24 s from the
VSG60's own memory, and only over a cable (``--pad-db``). This one streams from disk through
``vsg_sink``, so any length works. The cost is a jump where the file loops: a receiver sees
the signal break there once a pass.

**It radiates.** Nothing transmits without ``--go``, and ``--go`` needs ``--seconds`` (at most
``MAX_RUN_S``) and ``--level-dbm`` at most ``--max-dbm`` (``MAX_LEVEL_DBM`` unless raised; never
above the VSG60's +10). Every over-the-air run needs the user's go for that run, and the other
sessions using the radios get told first (CLAUDE.md).

``--level-dbm`` is ``vsg_sink``'s level, the same number the transmitter apps take. A file
scaled like ``dvbt2_tx``'s baseband (rms 0.2) at the same level matches the apps' power. A file
whose peak |iq| is over 1 would clip and is refused; scale it first. ``--check`` reads the file
and prints its rms and peak without opening the radio.

It prints ``CHECK radio`` once the stream is on and ``CHECK RESULT vsg_stream_file 0`` after
it stops, the same lines ``app_tx_check.py`` prints, so ``remote_tx.py`` waits on either the
same way. SIGTERM, SIGINT and SIGHUP stop it cleanly within 0.2 s.
"""
import argparse
import json
import math
import os
import signal
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

#: ``vsg_sink``'s clamps: the VSG60's tuning range and sample rates.
MIN_CENTER_MHZ, MAX_CENTER_MHZ = 30.0, 6000.0
MIN_RATE, MAX_RATE = 12.5e3, 50e6
#: The highest level without --max-dbm. -4 dBm is the most any over-the-air run has used.
MAX_LEVEL_DBM = 0.0
VSG_MAX_DBM = 10.0
#: The longest run.
MAX_RUN_S = 1800.0


def say(*a):
    print('CHECK', *a, flush=True)


def file_stats(path, chunks=40, chunk=200_000):
    """rms and peak |iq| over ``chunks`` pieces spread through the file, and its length in
    samples. A partial last sample is a problem: the file is not cf32."""
    size = os.path.getsize(path)
    if size % 8:
        raise ValueError(f"{path}: {size} bytes is not a whole number of cf32 samples")
    n = size // 8
    if n == 0:
        raise ValueError(f"{path} is empty")
    m = np.memmap(path, np.complex64, 'r')
    starts = np.linspace(0, max(n - chunk, 0), chunks).astype(int)
    power, peak = [], 0.0
    for s in starts:
        x = np.asarray(m[s:s + chunk])
        power.append(float(np.mean(np.abs(x) ** 2)))
        peak = max(peak, float(np.max(np.abs(x))))
    return math.sqrt(float(np.mean(power))), peak, n


def problems(a):
    """Everything wrong with the arguments, as sentences; empty when it may go."""
    out = []
    if not (MIN_RATE <= a.rate <= MAX_RATE):
        out.append(f"--rate {a.rate:g} is outside the VSG60's {MIN_RATE:g}-{MAX_RATE:g} S/s")
    if not (MIN_CENTER_MHZ <= a.center_mhz <= MAX_CENTER_MHZ):
        out.append(f"--center-mhz {a.center_mhz:g} is outside {MIN_CENTER_MHZ:g}-{MAX_CENTER_MHZ:g} MHz")
    ceiling = min(a.max_dbm, VSG_MAX_DBM)
    if not math.isfinite(a.level_dbm) or a.level_dbm > ceiling:
        out.append(f"--level-dbm {a.level_dbm:g} is over the ceiling of {ceiling:g} dBm (--max-dbm)")
    if a.go:
        if a.seconds is None:
            out.append("--go needs --seconds")
        elif not (0 < a.seconds <= MAX_RUN_S):
            out.append(f"--seconds must be more than 0 and at most {MAX_RUN_S:g}")
    return out


def parse(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('file', help='cf32 (complex64, little endian), looped')
    p.add_argument('--rate', type=float, required=True, help="the file's sample rate, S/s")
    p.add_argument('--center-mhz', type=float, required=True)
    p.add_argument('--level-dbm', type=float, required=True, help="vsg_sink's level, as the apps take it")
    p.add_argument('--max-dbm', type=float, default=MAX_LEVEL_DBM,
                   help=f'the ceiling for --level-dbm (default {MAX_LEVEL_DBM:g}; never above {VSG_MAX_DBM:g})')
    p.add_argument('--seconds', type=float, help='how long to transmit; required with --go')
    p.add_argument('--check', action='store_true', help='read the file and the limits; never open the radio')
    p.add_argument('--go', action='store_true', help='transmit')
    return p.parse_args(argv)


def main(argv=None):
    a = parse(argv)
    bad = problems(a)
    if bad:
        for b in bad:
            say('ABORT', b)
        return 2
    try:
        rms, peak, n = file_stats(a.file)
    except (OSError, ValueError) as e:
        say('ABORT', e)
        return 2
    say('file', json.dumps({'path': a.file, 'samples': n, 'seconds': round(n / a.rate, 3),
                            'rms': round(rms, 4), 'peak': round(peak, 4)}))
    if peak > 1.0:
        say('ABORT', f"peak |iq| {peak:.3f} is over 1 and would clip; scale the file first")
        return 2
    if a.check or not a.go:
        say('RESULT vsg_stream_file checked (nothing transmitted: no --go)')
        return 0

    from gnuradio import blocks, gr
    from apps.vsg_sink import vsg_sink
    tb = gr.top_block()
    src = blocks.file_source(gr.sizeof_gr_complex, a.file, True)
    sink = vsg_sink(center_freq=a.center_mhz * 1e6, sample_rate=a.rate, level_dbm=a.level_dbm)
    tb.connect(src, sink)
    stop = {'now': False}

    def handler(sig, frame):
        stop['now'] = True
    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(s, handler)
    tb.start()
    say('radio', json.dumps({'freq': a.center_mhz * 1e6, 'level_dbm': a.level_dbm, 'rate': a.rate,
                             'loop_s': round(n / a.rate, 3)}))
    end = time.time() + a.seconds
    while time.time() < end and not stop['now']:
        time.sleep(0.2)
    tb.stop()
    tb.wait()
    say('stopped', time.strftime('%H:%M:%S'), 'early (signal)' if stop['now'] else '')
    say('RESULT vsg_stream_file 0')
    return 0


if __name__ == '__main__':
    sys.exit(main())
