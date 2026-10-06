#!/usr/bin/env python3
"""Master and slave DH1 pairs on one channel, hopping, as samples and a sidecar.

    python scripts/bt_synth_pairs.py pairs_dh1_hop20 --pairs 400 --seed 6001 --out DIR
    python scripts/bt_synth_pairs.py --set pairs [--out DIR]

    from scripts import bt_synth_pairs
    iq, sidecar = bt_synth_pairs.synthesise_pairs(pairs=40)

``scripts/bt_synth_hop.py`` writes the master's packets only. A receiver that
recovers the master's clock from a master-only stream cannot tell two adjacent
clocks apart (a tie), and cannot say which burst is whose. A slave's answer in
the next slot, on the same channel, breaks the tie: it is the second burst of
a pair, and **the lower slot of a pair is the master's**. This is that file,
for bluey-ox-walker's paging, role and pair stack, which must work with its DC
block on and off, so some pairs land on the centre channel, 39 at 2441 MHz.
Nothing is transmitted. The plan is in
[knowledge/bluey-test-signals.md](../knowledge/bluey-test-signals.md).

**40 MS/s on 2441.0 MHz, and not 20.** The map this file hops over is channels
31-50, 2433 to 2452 MHz, bands out to 2432 and 2453: +/-9 MHz of the centre.
At 20 MS/s the window the VSG60A and BB60D keep flat is +/-0.36 x fs = 7.2 MHz,
which holds only channels 36-45; 20 channels do not fit, so the rate is 40 and
a slot is 25,000 samples. A channel the window cannot hold is refused, as in
``bt_synth_hop.py``, whose ``afh_hop_fn``, ``hop_plan``-style window check
and ``channel_mhz`` are used here, not copied.

**A pair.** A master DH1 in a master slot (CLK1-0 = 00), and a slave DH1 in the
next slot, clock ``clk + 2``, exactly one slot (625 us, 25,000 samples) after
the master's first sample, before the timing fraction. Both are on the channel
``hop_channel(master clk)`` gives: on an adapted sequence the kernel takes CLK1
as 0, so the slave slot is on the master slot's channel (``apps/bt_hop.py``
note 4, and ``bt_synth_hop.py``'s note on the slave's slot). Both have the
same LAP, ``lt_addr`` 1, ``flow`` 1. The slave's packet is built with its own
clock, so its whitening and header use ``clk + 2``. Master ``arqn`` 1 and
``seqn`` ``k & 1`` as in the other files; the slave's ``arqn`` is 1 and its
``seqn`` is its own counter, which also runs ``k & 1``. The body is
``br.seq_body`` of a stream per role: the master's counter is ``k`` and the
slave's is ``0x80000000 | k``, so every payload differs from every other, the
slave's from its own master's included.

**Idle slots.** After a pair, ``g`` idle pair-periods (two slots each) pass
before the next, ``g`` = 0, 1 or 2 with probabilities 0.60, 0.25 and 0.15
(seeded), so the traffic has gaps and is not a metronome. The clock moves
``2 * slots`` ticks. The slot grid is half a slot off sample 0 as in the other
files: a burst begins 12,500 samples into its slot, so it ends in the next
one's first 2,200, and the slave never overlaps the master (366 us of a
625 us slot).

**What a receiver can and cannot rely on.** With ``g = 0`` pairs follow each
other slot for slot, so a slave and the next pair's master are also about one
slot apart, and on the same channel now and then (``same_channel_back_to_back``
counts them, out of ``back_to_back_couples``: in the named file 5 of 228, about
1 in 46, as the kernel gives ``hop(clk + 4) == hop(clk)`` for 12 of 400 master
clocks). Because consecutive pairs sit at alternate symbol phases, 0 and 20,
that second gap is a slot less or more 20 samples (half a symbol), so to the
sample it is not "exactly one slot", but a receiver that times a start to
better than half a symbol apart from a slot cannot tell them. The rule "bursts
one slot apart on the same channel, the earlier the master" is right in every
case if applied greedily from the left and **if every burst is detected** - a
chain of consecutive pairs on one channel starts on a master and has an even
length - and not otherwise: miss the master of a chain and greedy pairing
mislabels two bursts. ``chain_note`` says it in the sidecar.

**Symbol phases.** Pair ``k`` is shifted as a whole, master and slave together,
so that its master starts at phase 0 (even ``k``) or 20 (odd ``k``) of the
40-sample symbol (``--start-phase`` 20 swaps them): 20 is the half-symbol dead
zone of bluey's detector at 40 MS/s, 0 is where it sees. The timing
fraction is uniform and per burst.

**Level.** One noise floor for every file, ``NOISE_1MHZ = AMPLITUDE**2 / 100``
in 1 MHz, and a burst of SNR ``s`` dB has the amplitude ``sqrt(NOISE_1MHZ *
10**(s/10))``, so the same ``snr_db`` is the same amplitude at 20 and 40 MS/s
and across files. ``--snr`` is the master's (20 dB); ``--slave-offset-db`` is
the slave's minus the master's (0), and every burst has its own ``snr_db``.

**Memory.** A file is built in blocks of 2**22 samples, noise from
``default_rng([seed, block])``, then the bursts that cross the block; the
file is written block by block and never held whole. A burst's phase and timing
fraction are drawn in the plan, before any sample, so the same arguments give
the same bytes, however the blocks fall.

The sidecar carries the keys of ``bt_synth_hop.synthesise_afh``'s - the
``afh_*`` set (one map, from burst 0, ``afh_map_index`` 0 on every burst),
``clock_lock_note``, and the top-level ``start_offset``, so a stage 3 grader indexes it alike (``timing_frac``,
``symbol_phase`` and ``snr_db`` vary per burst and are null at the top, named in
``per_burst_keys``; ``master_snr_db`` is the master's) - with
``air_bits`` left out (``air_bits_omitted``) and, per burst, ``role``, ``pair``, ``partner``,
``slot_index`` (the absolute slot from the grid start; masters even, slaves
odd), ``clk`` (the native clock of that burst's slot), ``channel``,
``channel_mhz``, ``ptype`` and ``symbol_phase``; at the top ``pairs``, a list of
``{pair, master, slave, channel, slot_index, idle_pairs_before}``, ``n_pairs``,
``pair_note``, ``centre_channel`` 39 and ``pairs_on_centre_channel``.
"""
import argparse
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from scripts import bt_synth  # noqa: E402
from scripts import bt_synth_hop as hop  # noqa: E402

#: The noise in 1 MHz, the one floor of every file: a burst of AMPLITUDE has 20 dB.
NOISE_1MHZ = bt_synth.AMPLITUDE ** 2 / 100

#: Samples a block of the file holds, so the float64 temporaries stay small.
BLOCK = 1 << 22

#: What the idle gaps are drawn from: ``g`` idle pair-periods with these odds.
IDLE_PROBS = (0.60, 0.25, 0.15)

PTYPE = 'DH1'

#: Where the files go without ``--out``.
DEFAULT_OUT = '/media/user/4TB/sdr-synth-tmp'
MAP_31_50 = list(range(31, 51))

#: The master's and the slave's bodies come from two streams of ``seq_body``.
SLAVE_STREAM = 0x80000000


def amplitude(snr_db):
    """|iq| of a burst of ``snr_db`` over the noise in 1 MHz."""
    return math.sqrt(NOISE_1MHZ * 10 ** (snr_db / 10))


PAIR_NOTE = ("A pair is a master DH1 in a master slot (clk % 4 == 0) and a slave DH1 in the next slot "
             "(clk + 2), exactly one slot (slot_samples) after the master's start_sample, before "
             "timing_frac, on the same channel: hop_channel(master clk) over the map, for both. "
             "The lower slot of a pair is the master's: slot_index is even for a master, odd for a "
             "slave. After a pair, idle_pairs_before of the next says how many idle pair-periods "
             "(2 slots each, 0 to 2) lie between. Both packets have lap, uap and lt_addr 1; the slave's "
             "whitening and header use its own clock; its arqn is 1; payloads differ (SEQ streams k "
             "and 0x80000000 | k). Each pair is shifted as a whole to symbol_phase 0 (even pair) or "
             "sps/2 (odd pair). snr_db is per burst: the master's, and the slave's = master's + "
             "slave_offset_db. start_sample is exactly one slot apart for a pair; in the samples the "
             "master-to-slave spacing is slot_samples + (slave timing_frac - master timing_frac), "
             "24,999 to 25,001 at 40 MS/s.")

def chain_note(same, couples):
    return ("With 0 idle pair-periods a pair's slave and the next pair's master are also about one "
            "slot apart (exactly slot_samples -/+ 20, since consecutive pairs alternate symbol phase "
            "0 and 20), and on the same channel in %d of the %d back-to-back couples of this file "
            "(same_channel_back_to_back, back_to_back_couples). Pairing greedily from the left, the "
            "earliest unpaired burst a master, is right in every case IF EVERY BURST IS DETECTED; "
            "dropping the master of one such chain makes it mislabel two bursts. Pairing every "
            "burst with whatever is a slot after it is wrong." % (same, couples))


def plan_pairs(pairs=400, lap=0x9E8B33, uap=0x47, clk0=0x0123400, fs=40e6, center_mhz=2441.0,
               channels=None, snr_db=20.0, slave_offset_db=0.0, idle_probs=IDLE_PROBS,
               start_phase=0, seed=6001):
    """The sidecar and the render jobs of a pairs capture, no samples made.

    Returns ``(sidecar, jobs, total)``: ``jobs`` is a list, one per burst in
    time order, of what ``render_block`` needs (the packet, the start sample,
    the timing fraction, the carrier phase, the amplitude and the channel),
    ``total`` the number of samples in the file. A map the tuning cannot hold,
    an ``idle_probs`` that is not three odds summing to 1, or a ``start_phase``
    that is not 0 or half a symbol raises ``ValueError`` here, before any sample.
    """
    if clk0 % 4:
        raise ValueError("clk0 must be a master slot's clock: CLK1-0 = 00")
    if pairs < 1:
        raise ValueError("a capture needs at least one pair")
    slot = fs * br.SLOT_US * 1e-6
    sps = int(round(fs / br.SYMBOL_RATE))
    if abs(slot - round(slot)) > 1e-6 or int(round(slot)) % sps:
        raise ValueError("%g S/s does not divide a 625 us slot in whole symbols" % fs)
    slot = int(round(slot))
    if start_phase not in (0, sps // 2):
        raise ValueError("start_phase is 0 or %d, half a symbol" % (sps // 2))
    probs = [float(p) for p in idle_probs]
    if len(probs) != 3 or min(probs) < 0 or abs(sum(probs) - 1) > 1e-9:
        raise ValueError("idle_probs is three odds, for 0, 1 and 2 idle pair-periods, summing to 1")
    channels = hop.map_channels(MAP_31_50 if channels is None else channels)
    for channel in channels:
        if hop.outside_window(channel, fs, center_mhz):
            raise ValueError("channel %d, %g MHz, has its band outside +/-%g MHz (%g x %g MS/s) of "
                             "the %g MHz centre" % (channel, hop.channel_mhz(channel),
                                                    hop.FLAT_FRACTION * fs / 1e6, hop.FLAT_FRACTION,
                                                    fs / 1e6, center_mhz))
    centre_channel = int(round(center_mhz - 2402))
    hop_fn = hop.afh_hop_fn([(clk0 >> 1, channels)], lap, uap)

    gaps = np.random.default_rng([seed, 1 << 30]).choice(3, size=pairs, p=probs)
    gaps[0] = 0                                       # nothing before the first pair
    draw = np.random.default_rng([seed, (1 << 30) + 1])
    frac = draw.uniform(0, 1, size=2 * pairs)         # per burst
    carrier_phase = draw.uniform(0, 2 * np.pi, size=2 * pairs)

    first = int(bt_synth.LEAD_SLOTS * slot)           # half a slot off the slot grid
    amp_m, amp_s = amplitude(snr_db), amplitude(snr_db + slave_offset_db)
    jobs, entries, pair_list = [], [], []
    pair_period = 0                                   # the pair's place, in 2-slot periods
    for k in range(pairs):
        pair_period += int(gaps[k]) + (1 if k else 0)
        slot_m = 2 * pair_period                      # the master's slot, even
        clk_m = (clk0 + 2 * slot_m) & 0x0FFFFFFF
        channel = int(hop_fn(clk_m))
        phase = start_phase if k % 2 == 0 else (start_phase + sps // 2) % sps
        shift = (phase - first) % sps                 # a slot is a whole number of symbols
        for role, slot_index, clk, stream, amp, snr in (
                ('master', slot_m, clk_m, k, amp_m, snr_db),
                ('slave', slot_m + 1, clk_m + 2, SLAVE_STREAM | k, amp_s, snr_db + slave_offset_db)):
            body = br.seq_body(stream, br.PACKET_TYPES[PTYPE][4])
            p = br.Packet(lap, uap, clk, PTYPE, body, lt_addr=1, flow=1, arqn=1, seqn=k & 1)
            start = first + slot_index * slot + shift
            index = len(jobs)
            jobs.append(dict(packet=p, start=start, frac=float(frac[index]),
                             phase=float(carrier_phase[index]), amp=amp, channel=channel))
            entry = {'start_sample': start, 'timing_frac': float(frac[index]),
                     'symbol_phase': start % sps}
            entry.update(p.sidecar())
            del entry['air_bits']
            entry.update(channel=channel, channel_mhz=hop.channel_mhz(channel), snr_db=snr,
                         role=role, pair=k, partner=index + (1 if role == 'master' else -1),
                         slot_index=slot_index, afh_map_index=0)
            entries.append(entry)
        pair_list.append({'pair': k, 'master': 2 * k, 'slave': 2 * k + 1, 'channel': channel,
                          'slot_index': slot_m, 'idle_pairs_before': int(gaps[k])})
    total = first + (pair_list[-1]['slot_index'] + 2) * slot + slot
    couples = [(a, b) for a, b in zip(pair_list, pair_list[1:]) if b['slot_index'] == a['slot_index'] + 2]
    back_to_back = sum(1 for a, b in couples if a['channel'] == b['channel'])

    sidecar = {
        'generator': 'SDR scripts/bt_synth_pairs.py',
        'generator_commit': bt_synth.commit(),
        'lap': lap,
        'uap': uap,
        'clk': clk0,
        'clk_convention': 'native CLK[27:0] of the slot the burst starts in; a master burst has '
                          'clk % 4 == 0 and its slave burst clk + 2; top-level clk is burst 0\'s',
        'hopping': True,
        'hop_channels': sorted({p['channel'] for p in pair_list}),
        'channel_mhz': None,
        'bt_channel': None,
        'afh_map': channels,
        'afh_instant': clk0 >> 1,
        'afh_map_count': 1,
        'afh_maps': [{'instant': clk0 >> 1, 'first_burst': 0, 'channels': channels}],
        'afh_instant_meaning': hop.INSTANT_MEANING,
        'clock_lock_note': hop.CLOCK_LOCK_NOTE,
        'address_for_hop': uap << 24 | lap,
        'hop_kernel': hop.HOP_KERNEL,
        'sample_rate': fs,
        'center_mhz': center_mhz,
        'cfo_hz': 0.0,
        'timing_frac': None,
        'start_offset': 0,
        'symbol_phase': None,
        'snr_db': None,
        'master_snr_db': snr_db,
        'per_burst_keys': ['start_sample', 'timing_frac', 'symbol_phase', 'snr_db', 'clk', 'ptype',
                           'channel', 'channel_mhz', 'role', 'pair', 'partner', 'slot_index',
                           'afh_map_index'],
        'slave_offset_db': slave_offset_db,
        'snr_bw_hz': 1e6,
        'noise_1mhz': NOISE_1MHZ,
        'modulation': {'scheme': 'GFSK', 'bt': br.GFSK_BT, 'h': br.GFSK_H,
                       'symbol_rate': br.SYMBOL_RATE},
        'slot_samples': slot,
        'symbol_phases': [0, sps // 2],
        'symbol_phase_note': 'pair k, master and slave together, starts at phase %d (even k) or '
                             '%d (odd k) of %d' % (start_phase, (start_phase + sps // 2) % sps, sps),
        'start_sample_meaning': 'the first sample of the access code\'s preamble, before '
                                'timing_frac is added',
        'air_bits_omitted': True,
        'n_pairs': pairs,
        'pair_note': PAIR_NOTE,
        'pairs': pair_list,
        'idle_probs': probs,
        'centre_channel': centre_channel,
        'pairs_on_centre_channel': sum(1 for p in pair_list if p['channel'] == centre_channel),
        'same_channel_back_to_back': back_to_back,
        'back_to_back_couples': len(couples),
        'chain_note': chain_note(back_to_back, len(couples)),
        'bursts': entries,
    }
    return sidecar, jobs, total


def render_burst(job, fs, center_mhz):
    """One burst's samples at its absolute sample numbers: ``(lo, samples)``.

    The carrier is one oscillator at the absolute sample index, as in
    ``bt_synth_hop.py``: the whole cycles are dropped before the cast to
    radians, which keeps the phase exact however far into the file the burst is.
    """
    burst, lead = br.gfsk(job['packet'].bits, fs, h=br.GFSK_H, delay=job['frac'])
    burst = burst * np.exp(1j * job['phase'])
    lo = job['start'] - lead
    n = np.arange(lo, lo + len(burst))
    cycles = (hop.channel_mhz(job['channel']) - center_mhz) * 1e6 / fs * n
    burst = burst * np.exp(2j * np.pi * (cycles - np.floor(cycles)))
    return lo, (job['amp'] * burst).astype(np.complex64)


def burst_span(job, fs):
    """The first sample and one past the last that ``render_burst`` makes,
    worked out without making it."""
    sps = fs / br.SYMBOL_RATE
    lead = int(np.ceil(2.0e-6 * fs)) + 1             # gfsk's own, ramp_us = 2
    lo = job['start'] - lead
    return lo, lo + int(np.ceil(len(job['packet'].bits) * sps)) + 2 * lead


def render_blocks(sidecar, jobs, total, seed, block=BLOCK, noise=True):
    """Yield the file in blocks of ``block`` samples as ``complex64``.

    Each block starts as noise from ``default_rng([seed, block_index])``, the
    per-component sigma of ``bt_synth.synthesise`` for the one floor
    ``NOISE_1MHZ``, and then the bursts that reach it are added. A burst that
    crosses a boundary is rendered for each block it touches, from the same
    job, so where the boundaries fall changes nothing. ``noise=False`` leaves
    the bursts alone.
    """
    fs, center_mhz = sidecar['sample_rate'], sidecar['center_mhz']
    sigma = math.sqrt(NOISE_1MHZ * (fs / 1e6) / 2)
    spans = [burst_span(job, fs) for job in jobs]
    for index, b0 in enumerate(range(0, total, block)):
        b1 = min(total, b0 + block)
        if noise:
            rng = np.random.default_rng([seed, index])
            out = (rng.normal(0, sigma, b1 - b0) + 1j * rng.normal(0, sigma, b1 - b0)).astype(np.complex64)
        else:
            out = np.zeros(b1 - b0, dtype=np.complex64)
        for job, (lo, hi) in zip(jobs, spans):
            if hi <= b0 or lo >= b1:
                continue
            start, samples = render_burst(job, fs, center_mhz)
            a, b = max(start, b0), min(start + len(samples), b1)
            out[a - b0:b - b0] += samples[a - start:b - start]
        yield out


def synthesise_pairs(seed=6001, block=BLOCK, noise=True, **spec):
    """The samples and the sidecar of a pairs capture, in memory: for a small
    run. ``spec`` is ``plan_pairs``'s; a real file goes through ``write_capture``."""
    sidecar, jobs, total = plan_pairs(seed=seed, **spec)
    iq = np.concatenate(list(render_blocks(sidecar, jobs, total, seed, block, noise)))
    return iq, sidecar


def write_capture(name, out_dir, seed=6001, **spec):
    """Make one capture, write ``synth_<name>.cf32`` block by block and
    ``synth_<name>.json`` into ``out_dir``. Every file, the single and the set,
    comes through here. Returns the two paths."""
    sidecar, jobs, total = plan_pairs(seed=seed, **spec)
    os.makedirs(out_dir, exist_ok=True)
    iq_path = os.path.join(out_dir, 'synth_%s.cf32' % name)
    side_path = os.path.join(out_dir, 'synth_%s.json' % name)
    with open(iq_path, 'wb') as f:
        for out in render_blocks(sidecar, jobs, total, seed):
            out.astype('<c8', copy=False).tofile(f)
    with open(side_path, 'w') as f:
        json.dump(sidecar, f, indent=1)
    print('%s  %d samples, %.1f ms, %d pairs, %d on channel %d, %d channels: %s' % (
        iq_path, total, total / sidecar['sample_rate'] * 1e3, sidecar['n_pairs'],
        sidecar['pairs_on_centre_channel'], sidecar['centre_channel'], len(sidecar['hop_channels']),
        hop.channel_text(sidecar['hop_channels'])))
    print(side_path)
    return iq_path, side_path


#: What ``--set pairs`` writes, and nothing else, with its seed.
PAIRS_SET = [('pairs_dh1_hop20', dict(pairs=400, seed=6001))]

#: The sets ``--set`` knows.
SETS = {'pairs': PAIRS_SET}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('name', nargs='?', help='the file is synth_<name>.cf32; not used with --set')
    ap.add_argument('--set', choices=sorted(SETS), help='write the named set, with its own seed and '
                    'settings, and no other option but --out')
    ap.add_argument('--pairs', type=int, help='default 400')
    ap.add_argument('--map', type=hop.channel_list, help='the channels in use; default 31-50')
    ap.add_argument('--snr', type=float, help='the master\'s dB in 1 MHz; default 20')
    ap.add_argument('--slave-offset-db', type=float, help='the slave\'s snr minus the master\'s; default 0')
    ap.add_argument('--idle-probs', help='odds of 0, 1 and 2 idle pair-periods, e.g. 0.6,0.25,0.15')
    ap.add_argument('--start-phase', type=int, help='the symbol phase of pair 0, 0 or 20; the next '
                    'pair is the other; default 0')
    ap.add_argument('--seed', type=int, help='default 6001')
    ap.add_argument('--out', default=DEFAULT_OUT, help='one folder for both files; default ' + DEFAULT_OUT)
    args = ap.parse_args(argv)

    per_file = ['pairs', 'map', 'snr', 'slave_offset_db', 'idle_probs', 'start_phase', 'seed']
    if args.set:
        stray = [o for o in per_file if getattr(args, o) is not None]
        if args.name is not None or stray:
            ap.error('--set takes everything from its table: no name and no option but --out')
        runs = SETS[args.set]
    else:
        if args.name is None:
            ap.error('give the name of the file, or --set')
        spec = {}
        for key, value in (('pairs', args.pairs), ('channels', args.map), ('snr_db', args.snr),
                           ('slave_offset_db', args.slave_offset_db), ('start_phase', args.start_phase),
                           ('seed', args.seed)):
            if value is not None:
                spec[key] = value
        if args.idle_probs is not None:
            try:
                spec['idle_probs'] = [float(x) for x in args.idle_probs.split(',')]
            except ValueError:
                ap.error('--idle-probs is three numbers with commas')
        runs = [(args.name, spec)]
    try:
        for name, spec in runs:
            write_capture(name, args.out, **spec)
    except ValueError as e:
        ap.error(str(e))


if __name__ == '__main__':
    main()
