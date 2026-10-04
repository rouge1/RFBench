#!/usr/bin/env python3
"""Write a Bluetooth Basic Rate test capture for bluey-ox-walker to grade.

    python scripts/bt_synth.py clean_dh5                     # stage 1's first file
    python scripts/bt_synth.py imp_dh5 --cfo 12e3 --timing-frac 0.37 --snr 15
    python scripts/bt_synth.py dh3 --ptype DH3 --out /tmp/bt

It writes ``synth_<name>.cf32``, as though a BB60D centred on 2441 MHz had
recorded a master sending a train of DH3 or DH5 packets on one channel, and
``synth_<name>.json`` beside it with the truth of every burst. By default both
go into bluey-ox-walker's ``data/iq/`` and ``data/sidecar/``, the two folders
its grader reads; the plan, the file format and every sidecar key are in
[knowledge/bluey-test-signals.md](../knowledge/bluey-test-signals.md).

Nothing is transmitted. The packets come from ``apps/bt_br_frame.py``, which
``scripts/test_bt_br_frame.py`` holds to the Core specification's sample data.

The timing is the master's, because bluey's receiver works out slots and
clocks from it and a train that broke the rules would look like a decoder
fault:

* Every burst starts on a master slot (CLK1 = 0) of a 625 us grid, and the
  clock advances two ticks a slot between them: a DH5 every 6 slots, a DH3
  every 4, the slot after each left for the slave that is not there.
* The grid sits half a slot off sample 0, so no burst starts near a slot
  boundary, where a few samples of jitter would move it into the next slot.
  ``--start-offset`` then moves every burst a few samples to set the
  symbol phase: bluey's detector turned out to miss every burst whose bits
  begin at sample 10 of the 20-sample grid, which is where every burst
  began until this option existed. ``symbol_phase`` in the sidecar is ``start_sample % 20``.
* Noise runs from sample 0, with more than a millisecond of it before the
  first burst: bluey sets its gain from the first 1000 samples.

``snr_db`` is the burst's power over the noise in 1 MHz, one channel's
worth, which the sidecar records as ``snr_bw_hz``. Over the whole 20 MHz the
noise is 13 dB more.
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402

BLUEY = '/data/python/bluey-ox-walker'

#: Samples of noise before the grid starts, and where on the grid burst 0
#: begins: half a slot in, after two whole slots - 1.56 ms at 20 MS/s.
LEAD_SLOTS = 2.5

#: Burst amplitude in the file. The scale is arbitrary; this keeps the peaks of
#: signal plus noise comfortably inside what any reader expects of cf32.
AMPLITUDE = 0.25


def commit():
    """The SDR repository's commit, so a file can be traced to its encoder,
    with ``-dirty`` when ``apps/`` or ``scripts/`` differ from it, and
    ``-unchecked`` when git could not say (``git status`` failed: an empty
    answer is not a clean tree). ``git describe --dirty`` alone misses
    untracked files, and a new encoder not yet committed is exactly that."""
    try:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        head = subprocess.run(['git', '-C', here, 'rev-parse', '--short', 'HEAD'],
                              capture_output=True, text=True, timeout=10)
        status = subprocess.run(['git', '-C', here, 'status', '--porcelain',
                                 '--', 'apps', 'scripts'],
                                capture_output=True, text=True, timeout=10)
        name = head.stdout.strip()
        if not name:
            return None
        if status.returncode != 0:
            return name + '-unchecked'
        return name + ('-dirty' if status.stdout.strip() else '')
    except (OSError, subprocess.SubprocessError):
        return None


def synthesise(ptype='DH5', bursts=40, lap=0x9E8B33, uap=0x47, clk0=0x0123400,
               lt_addr=1, channel_mhz=2445.0, center_mhz=2441.0, fs=20e6,
               cfo_hz=0.0, timing_frac=0.0, snr_db=30.0, h=br.GFSK_H, seed=1,
               start_offset=0):
    """The samples and the sidecar for one capture.

    ``start_offset`` moves every burst by that many whole samples, which is
    how a file gets a different symbol phase: with no offset, every burst
    starts at sample 10 of the 20-sample symbol grid at 20 MS/s. Keep it well
    inside half a slot, so no burst moves into a different slot.
    """
    if clk0 % 4:
        raise ValueError("clk0 must be a master slot's clock: CLK1-0 = 00")
    if not 0 <= timing_frac < 1:
        raise ValueError("timing_frac is a fraction of a sample, 0 to 1")
    slots = br.PACKET_TYPES[ptype][1]
    period = slots + 1                              # the slave's slot after it
    slot_samples = fs * br.SLOT_US * 1e-6
    if slot_samples != int(slot_samples):
        raise ValueError("%g S/s does not divide a 625 us slot" % fs)
    slot_samples = int(slot_samples)
    rng = np.random.default_rng(seed)

    if abs(start_offset) > slot_samples // 4:
        raise ValueError("start_offset must stay within a quarter slot")
    first = int(LEAD_SLOTS * slot_samples) + int(start_offset)
    total = first + bursts * period * slot_samples + slot_samples
    iq = np.zeros(total, dtype=np.complex64)
    shift = channel_mhz - center_mhz
    entries = []
    for k in range(bursts):
        clk = clk0 + 2 * period * k
        body = br.seq_body(k, br.PACKET_TYPES[ptype][4])
        p = br.Packet(lap, uap, clk, ptype, body, lt_addr=lt_addr, flow=1,
                      arqn=1, seqn=k & 1)
        start = first + k * period * slot_samples
        burst, lead = br.gfsk(p.bits, fs, h=h, delay=timing_frac)
        burst = burst * np.exp(1j * rng.uniform(0, 2 * np.pi))  # its own phase
        iq[start - lead:start - lead + len(burst)] += AMPLITUDE * burst
        entry = {'start_sample': start}
        entry.update(p.sidecar())
        entries.append(entry)

    n = np.arange(total)
    iq *= np.exp(2j * np.pi * (shift * 1e6 + cfo_hz) * n / fs).astype(np.complex64)
    noise_1mhz = AMPLITUDE ** 2 / 10 ** (snr_db / 10)
    sigma = np.sqrt(noise_1mhz * fs / 1e6 / 2)
    iq += (rng.normal(0, sigma, total) + 1j * rng.normal(0, sigma, total)).astype(np.complex64)

    sidecar = {
        'generator': 'SDR scripts/bt_synth.py',
        'generator_commit': commit(),
        'lap': lap,
        'uap': uap,
        'clk': clk0,
        'clk_convention': 'native CLK[27:0], at the first sample of the burst '
                          'it is given for; top-level clk is burst 0\'s',
        'channel_mhz': channel_mhz,
        'bt_channel': int(round(channel_mhz - 2402)),
        'sample_rate': fs,
        'center_mhz': center_mhz,
        'cfo_hz': cfo_hz,
        'timing_frac': timing_frac,
        'snr_db': snr_db,
        'snr_bw_hz': 1e6,
        'modulation': {'scheme': 'GFSK', 'bt': br.GFSK_BT, 'h': h,
                       'symbol_rate': br.SYMBOL_RATE},
        'slot_samples': slot_samples,
        'start_offset': int(start_offset),
        'symbol_phase': first % int(round(fs / br.SYMBOL_RATE)),
        'start_sample_meaning': 'the first sample of the access code\'s '
                                'preamble, before timing_frac is added',
        'bursts': entries,
    }
    return iq, sidecar


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('name', help='the file is synth_<name>.cf32')
    ap.add_argument('--ptype', default='DH5',
                    choices=['DH1', 'DM1', 'DH3', 'DM3', 'DH5', 'DM5'])
    ap.add_argument('--bursts', type=int, default=40)
    ap.add_argument('--channel-mhz', type=float, default=2445.0)
    ap.add_argument('--center-mhz', type=float, default=2441.0)
    ap.add_argument('--cfo', type=float, default=0.0, help='Hz')
    ap.add_argument('--timing-frac', type=float, default=0.0)
    ap.add_argument('--snr', type=float, default=30.0, help='dB in 1 MHz')
    ap.add_argument('--h', type=float, default=br.GFSK_H)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--start-offset', type=int, default=0,
                    help='samples to move every burst by - the symbol phase')
    ap.add_argument('--out', help='one folder for both files, instead of '
                    "bluey-ox-walker's data/iq and data/sidecar")
    args = ap.parse_args()

    iq, sidecar = synthesise(ptype=args.ptype, bursts=args.bursts,
                             channel_mhz=args.channel_mhz,
                             center_mhz=args.center_mhz, cfo_hz=args.cfo,
                             timing_frac=args.timing_frac, snr_db=args.snr,
                             h=args.h, seed=args.seed,
                             start_offset=args.start_offset)
    if args.out:
        iq_dir = side_dir = args.out
    else:
        iq_dir = os.path.join(BLUEY, 'data', 'iq')
        side_dir = os.path.join(BLUEY, 'data', 'sidecar')
    os.makedirs(iq_dir, exist_ok=True)
    os.makedirs(side_dir, exist_ok=True)
    iq_path = os.path.join(iq_dir, 'synth_%s.cf32' % args.name)
    side_path = os.path.join(side_dir, 'synth_%s.json' % args.name)
    iq.astype('<c8').tofile(iq_path)
    with open(side_path, 'w') as f:
        json.dump(sidecar, f, indent=1)
    print('%s  %d samples, %.1f ms, %d %s bursts' % (
        iq_path, len(iq), len(iq) / sidecar['sample_rate'] * 1e3,
        len(sidecar['bursts']), args.ptype))
    print(side_path)


if __name__ == '__main__':
    main()
