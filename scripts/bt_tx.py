#!/usr/bin/env python3
"""Stage 2: send a Bluetooth Basic Rate burst train from the VSG60.

    python scripts/bt_tx.py --dry-run --out /tmp/bt          # no radio at all
    timeout -k 5 70 python scripts/bt_tx.py --seconds 60 --level -40

The same packets ``scripts/bt_synth.py`` writes into files, sent from the
VSG60 for bluey-ox-walker's BB60D to receive, so its decoder meets a real
transmitter's clock and frequency offsets - what no synthetic file has. The
plan is [knowledge/bluey-test-signals.md](../knowledge/bluey-test-signals.md);
the VSG60's traps are in [knowledge/radios.md](../knowledge/radios.md#vsg60-notes).

The train is one loop the VSG60 repeats out of its own memory
(``repeat_waveform``), so the timing on air is the VSG60's own clock, not the
host's, and nothing can underrun. A loop is exactly 256 slots, 160 ms: a
multiple of the 64 slots in which CLK6-1 comes round, so the whitening of
every burst stays what a master with a running clock would send, loop after
loop. Only the clock's higher bits and the payload's SEQ counter go back at
each repeat, and a single channel shows neither: there is no hopping.

So the truth is one loop. ``synth_<name>_tx.json`` gives each burst's start in
samples from the start of the loop, and ``clk`` for the first time it is sent;
in the n-th repeat it is ``clk + 512 n``. A capture starts at no particular
point in the loop, so a receiver finds where it is from the bursts themselves:
each burst's SEQ counter is its index in the loop.

``--dry-run`` writes the loop as cf32 instead of opening the radio, and
``--reference`` writes ``synth_<name>_ref`` - the same train with noise, at
the BB60D's rate and centre, two loops long - for a like-for-like comparison
of over the air against a file.

Nothing transmits without ``--seconds``, and the level starts low: −40 dBm
by default. The VSG60 is not certified for 2.4 GHz, so keep it on a cable and
pad, or on an antenna only at a level nothing beyond the bench can hear.
Run it with ``timeout -k`` (CLAUDE.md), since it holds a transmitter open.
"""
import argparse
import json
import os
import signal
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from scripts import bt_synth  # noqa: E402

FS = 20e6
SLOT = int(FS * br.SLOT_US * 1e-6)              # 12500 samples
LOOP_SLOTS = 256                                # 4 rounds of CLK6-1
CENTER_MHZ = 2441.0                             # the BB60D's centre too
CHANNEL_MHZ = 2445.0                            # BT channel 43
LAP, UAP = 0x9E8B33, 0x47


def train(ptype='DH5', clk0=0x0123400, offset=SLOT // 2):
    """One loop: the packets, each burst's start, and the samples, no noise.

    A burst every slots + 1 slots, from slot 0, as many as fit in the loop
    with the slave's slot after the last one still inside it. ``offset``
    places the slot grid in the loop, half a slot in by default, as in the
    files.
    """
    period = br.PACKET_TYPES[ptype][1] + 1
    n = LOOP_SLOTS // period
    iq = np.zeros(LOOP_SLOTS * SLOT, dtype=np.complex64)
    shift = np.exp(2j * np.pi * (CHANNEL_MHZ - CENTER_MHZ) * 1e6
                   * np.arange(len(iq)) / FS).astype(np.complex64)
    packets, starts = [], []
    for k in range(n):
        clk = (clk0 + 2 * period * k) & 0x0FFFFFFF
        body = br.seq_body(k, br.PACKET_TYPES[ptype][4])
        p = br.Packet(LAP, UAP, clk, ptype, body, lt_addr=1, flow=1, arqn=1,
                      seqn=k & 1)
        start = k * period * SLOT + offset
        burst, lead = br.gfsk(p.bits, FS)
        lo = start - lead
        hi = lo + len(burst)
        if hi > len(iq):
            raise ValueError("burst %d runs past the end of the loop" % k)
        iq[lo:hi] += burst
        packets.append(p)
        starts.append(start)
    return packets, starts, iq * shift


def sidecar(packets, starts, ptype, level_dbm, extra):
    s = {
        'generator': 'SDR scripts/bt_tx.py',
        'generator_commit': bt_synth.commit(),
        'lap': LAP, 'uap': UAP,
        'channel_mhz': CHANNEL_MHZ,
        'bt_channel': int(round(CHANNEL_MHZ - 2402)),
        'sample_rate': FS,
        'center_mhz': CENTER_MHZ,
        'clk_convention': 'native CLK[27:0] at the first sample of the burst, '
                          'the first time it is sent; add 512 for each '
                          'repeat of the loop',
        'start_sample_meaning': 'samples from the start of the loop to the '
                                'first sample of the preamble',
        'loop_slots': LOOP_SLOTS,
        'loop_samples': LOOP_SLOTS * SLOT,
        'clk_per_loop': 2 * LOOP_SLOTS,
        'slot_samples': SLOT,
        'modulation': {'scheme': 'GFSK', 'bt': br.GFSK_BT, 'h': br.GFSK_H,
                       'symbol_rate': br.SYMBOL_RATE},
        'ptype': ptype,
        'bursts': [dict({'start_sample': st}, **p.sidecar())
                   for p, st in zip(packets, starts)],
    }
    s.update(extra)
    return s


def reference(packets, starts, loop, snr_db=30.0, loops=2, seed=1):
    """The same train as a synthetic capture: ``loops`` repeats with the
    clock running on, noise from sample 0, a lead-in of two slots."""
    rng = np.random.default_rng(seed)
    lead = 2 * SLOT
    total = lead + loops * len(loop) + SLOT
    iq = np.zeros(total, dtype=np.complex64)
    entries = []
    amp = bt_synth.AMPLITUDE
    for m in range(loops):
        for p, st in zip(packets, starts):
            q = br.Packet(p.lap, p.uap, (p.clk + 2 * LOOP_SLOTS * m) & 0x0FFFFFFF,
                          p.ptype, p.body, lt_addr=p.lt_addr, flow=p.flow,
                          arqn=p.arqn, seqn=p.seqn)
            burst, blead = br.gfsk(q.bits, FS)
            s0 = lead + m * len(loop) + st
            n = np.arange(s0 - blead, s0 - blead + len(burst))
            iq[n] += amp * burst * np.exp(1j * (2 * np.pi * (CHANNEL_MHZ - CENTER_MHZ)
                                                * 1e6 * n / FS + rng.uniform(0, 2 * np.pi)))
            entries.append(dict({'start_sample': int(s0)}, **q.sidecar()))
    sigma = np.sqrt(amp ** 2 / 10 ** (snr_db / 10) * FS / 1e6 / 2)
    iq += (rng.normal(0, sigma, total) + 1j * rng.normal(0, sigma, total)).astype(np.complex64)
    return iq, entries


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--name', default='ota_dh5')
    ap.add_argument('--ptype', default='DH5', choices=['DH1', 'DM1', 'DH3', 'DM3', 'DH5', 'DM5'])
    ap.add_argument('--level', type=float, default=-40.0, help='dBm at the VSG60 output')
    ap.add_argument('--seconds', type=float, help='transmit for this long; nothing is sent without it')
    ap.add_argument('--dry-run', action='store_true', help='write the loop as cf32; never open the radio')
    ap.add_argument('--reference', action='store_true',
                    help="also write synth_<name>_ref into bluey's folders (or --out)")
    ap.add_argument('--out', help="folder for the files, instead of bluey-ox-walker's data/")
    args = ap.parse_args()

    packets, starts, loop = train(args.ptype)
    peak = float(np.abs(loop).max())
    print('loop: %d %s bursts, %d samples (%.0f ms), peak |iq| %.3f'
          % (len(packets), args.ptype, len(loop), len(loop) / FS * 1e3, peak))
    if args.out:
        iq_dir = side_dir = args.out
    else:
        iq_dir = os.path.join(bt_synth.BLUEY, 'data', 'iq')
        side_dir = os.path.join(bt_synth.BLUEY, 'data', 'sidecar')
    os.makedirs(side_dir, exist_ok=True)

    if args.reference:
        iq, entries = reference(packets, starts, loop)
        s = sidecar(packets, starts, args.ptype, None, {
            'snr_db': 30.0, 'snr_bw_hz': 1e6, 'cfo_hz': 0.0, 'timing_frac': 0.0,
            'note': 'the stage 2 train as a synthetic file, two loops, for '
                    'comparison with the over-the-air capture'})
        s['bursts'] = entries
        s['start_sample_meaning'] = 'the first sample of the access code\'s preamble'
        s['clk_convention'] = 'native CLK[27:0] at the first sample of the burst'
        os.makedirs(iq_dir, exist_ok=True)
        iq.astype('<c8').tofile(os.path.join(iq_dir, 'synth_%s_ref.cf32' % args.name))
        with open(os.path.join(side_dir, 'synth_%s_ref.json' % args.name), 'w') as f:
            json.dump(s, f, indent=1)
        print('wrote synth_%s_ref' % args.name)

    tx = {'vsg_center_mhz': CENTER_MHZ, 'vsg_sample_rate': FS,
          'vsg_level_dbm': args.level, 'vsg_reference': 'internal (free-running)',
          'mode': 'repeat_waveform: the loop repeats from the VSG60\'s memory'}
    if args.dry_run:
        os.makedirs(iq_dir, exist_ok=True)
        loop.astype('<c8').tofile(os.path.join(iq_dir, 'synth_%s_txloop.cf32' % args.name))
        tx['dry_run'] = True
    elif args.seconds is None:
        print('nothing sent: give --seconds to transmit, or --dry-run')
        return

    if not args.dry_run:
        # timeout sends SIGTERM and a dropped ssh session SIGHUP, and both
        # end the process by default with nothing after them run - and a
        # repeat the VSG60 plays from its own memory would then outlive this
        # script. So they only raise a flag, and the loop below stops the
        # device and writes the sidecar, as it does at the end of a full run.
        stop_now = []

        def on_signal(signum, _frame):
            stop_now.append(signal.Signals(signum).name)

        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            signal.signal(sig, on_signal)
        from apps import vsg_sink
        sink = vsg_sink.vsg_sink(center_freq=CENTER_MHZ * 1e6, sample_rate=FS,
                                 level_dbm=args.level)
        tx['vsg_serial'] = sink.get_serial()
        tx['tx_start_unix'] = time.time()
        try:
            sink.repeat_waveform(loop)
            print('transmitting %.1f s at %.1f dBm, %.3f MHz' % (
                args.seconds, args.level, CHANNEL_MHZ), flush=True)
            end = time.time() + args.seconds
            while time.time() < end and not stop_now:
                time.sleep(0.2)
                if not sink.waveform_active():
                    print('the waveform stopped on its own')
                    break
            if stop_now:
                tx['interrupted_by'] = stop_now[0]
                print('%s: stopping early' % stop_now[0])
        finally:
            sink.stop_waveform()
            sink.stop()
            tx['tx_stop_unix'] = time.time()
        print('stopped')

    s = sidecar(packets, starts, args.ptype, args.level, tx)
    path = os.path.join(side_dir, 'synth_%s_tx.json' % args.name)
    with open(path, 'w') as f:
        json.dump(s, f, indent=1)
    print(path)


if __name__ == '__main__':
    main()
