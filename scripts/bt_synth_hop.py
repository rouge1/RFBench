#!/usr/bin/env python3
"""A hopping Bluetooth Basic Rate train, as samples and a sidecar.

    python scripts/bt_synth_hop.py hop20_dh5 --ptype DH5 --bursts 100 --map 33-52 --seed 3001
    python scripts/bt_synth_hop.py hop20_change_dh5 --bursts 120 --map 33-52 \\
        --map-b 36-55 --change-at-burst 60
    python scripts/bt_synth_hop.py --set stage3 [--out DIR]

    from scripts import bt_synth_hop
    iq, sidecar = bt_synth_hop.synthesise_hop(hop_fn, 'DH5', bursts=100)
    iq, sidecar = bt_synth_hop.synthesise_afh([(0, range(33, 53))], 'DH5')

``scripts/bt_synth.py`` writes a train on one channel; this is the same
writer with the channel no longer fixed, for stage 3 of
[knowledge/bluey-test-signals.md](../knowledge/bluey-test-signals.md): the
master hopping inside one tuning of the VSG60 or the BB60D, every hop a
frequency shift in baseband. Nothing is transmitted. ``synthesise_hop`` takes
any ``hop_fn``; ``synthesise_afh`` and the command line bring the one stage 3
wants, the adapted sequence of ``apps/bt_hop.py`` over a map of 20 adjacent
channels, which ``afh_hop_fn`` builds from the maps and the master's address.

``hop_fn(clk)`` takes the native clock CLK[27:0] of a burst's first slot and
returns an RF channel, 0-78, at 2402 + channel MHz. What this adds to
``bt_synth.synthesise`` is only where each burst is put:

* **A packet stays on one channel for its whole length**, as the Core
  specification's hopping rule has it for 3- and 5-slot packets: a DH5 is
  five slots of the one carrier. Its channel is ``hop_fn`` of the clock of its
  first slot, and the next packet's is
  ``hop_fn`` of that packet's own first-slot clock, ``2 * (slots + 1)`` ticks
  later - the slave's slot after it is never heard here, so its clock never
  reaches ``hop_fn`` either.
* **Every burst is shifted with the absolute sample index.** The shift is
  ``exp(2j pi (f_channel - center + cfo) n / fs)`` for the sample's number n
  in the file, as ``bt_synth`` does for its one channel, so the carrier of all
  the bursts is one numerically controlled oscillator that retunes between
  them. A shift counted from the burst's own first sample would restart the
  carrier's phase at every hop. Each burst still has its own random starting
  phase, which is the transmitter's, not the oscillator's.
* **A channel the tuning cannot hold is refused**, not clipped: its occupied
  band, the channel +/- 1 MHz, must lie inside +/- 0.36 x ``fs`` of the
  centre, the 72 % of the sample rate over which the VSG60A stays flat. At
  40 MS/s that is +/- 14.4 MHz, so about 2431 to 2459 MHz round 2445.

**The adapted sequence.** ``afh_hop_fn(maps, lap, uap)`` is a ``hop_fn``:
``maps`` is a list of ``(instant, channels)`` in increasing ``instant``, an
instant being a CLK[27:1] value, and a clock uses the last map whose instant
is no later than its own CLK[27:1]. The channel is
``bt_hop.hop_channel(clk, uap << 24 | lap, mask)``, ``mask`` being that map's
79-bit used-channel mask. ``synthesise_afh`` takes the maps as ``(first_burst,
channels)`` and turns each ``first_burst`` into the instant of that burst's
clock, so a map starts exactly on a burst and the first, from burst 0, is in
force for the whole file: every burst is on an adapted sequence, which is what
bluey-ox-walker's variant-E kernel is asked to follow. ``--set stage3`` is the
six files that go to it: three packet types, a long DH1 train, impairments
(a carrier offset, a fraction of a sample, noise at 15 dB, a symbol phase) and
a map that changes under the train. Each ``--set`` file has its seed in
``STAGE3_SET``, and the same command twice gives the same bytes. They are big,
about 1 GB in all at 40 MS/s, and ``--out`` names one folder for both kinds of
file; without it they go where ``bt_synth.py``'s do, bluey-ox-walker's
``data/iq`` and ``data/sidecar``.

The timing is the master's, exactly as in ``bt_synth``: a burst every
``slots + 1`` slots of a 625 us grid, the grid half a slot off sample 0 and
noise from sample 0. At 40 MS/s a slot is 25,000 samples. ``snr_db`` is the
burst's power over the noise in 1 MHz, the same on every channel, since the
noise is added after the shift and is white across the whole file. ``None``
adds no noise, and the sidecar's ``snr_db`` is null. ``amplitude`` defaults
to ``bt_synth.AMPLITUDE``; any other number is the burst's amplitude, |iq|.

The sidecar is ``bt_synth``'s with the per-burst ``channel`` and
``channel_mhz`` added and ``hopping: true`` and ``hop_channels`` at the top.
``channel_mhz`` and ``bt_channel`` at the top are ``null``: a hopping file has
no one channel. ``symbol_phase`` is per burst here, ``start_sample`` modulo the
samples in a symbol, as well as the top-level one of the single-channel files.

``synthesise_afh`` adds the map to that: ``afh_map`` and ``afh_instant`` at the
top are the first map's channels and its CLK[27:1] instant, the two keys
bluey applies a map from; ``afh_maps`` is every map with its instant and the
burst it starts on, ``address_for_hop`` is the ``uap << 24 | lap`` the kernel
was given, and each burst carries ``afh_map_index``, the map that was in force
for it. ``clock_lock_note`` says what bluey's own clock lock returns on an
adapted map, which is not the true clock.
"""
import argparse
import bisect
import json
import operator
import warnings
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from apps import bt_hop  # noqa: E402
from scripts import bt_synth  # noqa: E402

#: The Basic Rate channels, 2402 + n MHz.
CHANNELS = 79

#: The VSG60A is flat over this fraction of its sample rate, centred on its
#: tuning, so a burst's occupied band must lie inside +/- this x fs.
FLAT_FRACTION = 0.36

#: Half the occupied bandwidth of a Basic Rate burst, MHz: one channel wide,
#: 1 MHz either side of the carrier.
HALF_BAND_MHZ = 1.0


def channel_mhz(channel):
    """The centre frequency of an RF channel, in MHz."""
    return 2402.0 + channel


def outside_window(channel, fs, center_mhz):
    """Whether a channel's occupied band lies outside the window the sample rate
    and centre can hold: the channel +/- 1 MHz must be inside +/- FLAT_FRACTION
    x fs of the centre."""
    return abs(channel_mhz(channel) - center_mhz) + HALF_BAND_MHZ > FLAT_FRACTION * fs / 1e6


def hop_plan(hop_fn, bursts, clk0, period, fs, center_mhz):
    """Every burst's clock and channel, checked, before any sample is made.

    A channel that is not 0-78, or whose band is outside the window the sample
    rate and centre can hold, raises ``ValueError`` naming the burst and the
    channel, so a bad ``hop_fn`` fails at once and not after the noise.
    """
    reach = FLAT_FRACTION * fs / 1e6                 # MHz either side of the centre
    plan = []
    for k in range(bursts):
        clk = (clk0 + 2 * period * k) & 0x0FFFFFFF
        given = hop_fn(clk)
        try:
            channel = operator.index(given)
        except TypeError:
            raise ValueError("burst %d (clk %#x): hop_fn gave %r, not a channel number"
                             % (k, clk, given)) from None
        if not 0 <= channel < CHANNELS:
            raise ValueError("burst %d (clk %#x): channel %d is not 0-%d"
                             % (k, clk, channel, CHANNELS - 1))
        offset = channel_mhz(channel) - center_mhz
        if outside_window(channel, fs, center_mhz):
            raise ValueError(
                "burst %d (clk %#x) on channel %d, %g MHz: its band %g to %g MHz is outside "
                "+/-%g MHz (%g x %g MS/s) of the %g MHz centre"
                % (k, clk, channel, channel_mhz(channel),
                   channel_mhz(channel) - HALF_BAND_MHZ, channel_mhz(channel) + HALF_BAND_MHZ,
                   reach, FLAT_FRACTION, fs / 1e6, center_mhz))
        plan.append((clk, int(channel)))
    return plan


def synthesise_hop(hop_fn, ptype='DH5', bursts=100, lap=0x9E8B33, uap=0x47, clk0=0x0123400,
                   fs=40e6, center_mhz=2445.0, cfo_hz=0.0, timing_frac=0.0, snr_db=30.0,
                   seed=1, start_offset=0, amplitude=None):
    """The samples and the sidecar for one hopping capture.

    ``hop_fn(clk)`` gives the channel of the packet whose first slot has the
    native clock ``clk``. ``start_offset`` moves every burst by that many whole
    samples, as in ``bt_synth.synthesise``; keep it well inside a quarter slot.
    ``amplitude`` None is ``bt_synth.AMPLITUDE``; any other number is |iq| of
    the burst. ``snr_db`` None adds no noise (the sidecar records null); a
    number is that amplitude over the noise in 1 MHz.
    """
    if clk0 % 4:
        raise ValueError("clk0 must be a master slot's clock: CLK1-0 = 00")
    if not 0 <= timing_frac < 1:
        raise ValueError("timing_frac is a fraction of a sample, 0 to 1")
    if ptype not in br.PACKET_TYPES:
        raise ValueError("no packet type %r" % (ptype,))
    if bursts < 1:
        raise ValueError("a capture needs at least one burst")
    slots = br.PACKET_TYPES[ptype][1]
    period = slots + 1                              # the slave's slot after it
    slot_samples = fs * br.SLOT_US * 1e-6
    if abs(slot_samples - round(slot_samples)) > 1e-6:
        raise ValueError("%g S/s does not divide a 625 us slot" % fs)
    slot_samples = int(round(slot_samples))
    sps = int(round(fs / br.SYMBOL_RATE))
    if abs(start_offset) > slot_samples // 4:
        raise ValueError("start_offset must stay within a quarter slot")

    if center_mhz != round(center_mhz):
        warnings.warn(
            "center_mhz %g is not a whole number of MHz: a receiver whose channelizer has "
            "1 MHz bins centred on the capture centre (bluey-ox-walker's) then sees every "
            "channel half a bin off, and two of them in one bin, and finds no bursts"
            % center_mhz, stacklevel=2)
    plan = hop_plan(hop_fn, bursts, clk0, period, fs, center_mhz)
    rng = np.random.default_rng(seed)
    # None keeps the historical amplitude, so a default call is the same
    # samples as before this argument existed.
    amp = bt_synth.AMPLITUDE if amplitude is None else amplitude
    first = int(bt_synth.LEAD_SLOTS * slot_samples) + int(start_offset)
    total = first + bursts * period * slot_samples + slot_samples
    iq = np.zeros(total, dtype=np.complex64)
    entries = []
    for k, (clk, channel) in enumerate(plan):
        body = br.seq_body(k, br.PACKET_TYPES[ptype][4])
        p = br.Packet(lap, uap, clk, ptype, body, lt_addr=1, flow=1, arqn=1, seqn=k & 1)
        start = first + k * period * slot_samples
        burst, lead = br.gfsk(p.bits, fs, h=br.GFSK_H, delay=timing_frac)
        burst = burst * np.exp(1j * rng.uniform(0, 2 * np.pi))    # its own phase
        lo = start - lead
        # The carrier of the whole file is one oscillator: this burst's slice
        # of it, at the absolute sample numbers. The whole cycles are dropped
        # before the cast to radians, which keeps the phase exact however far
        # into the file the burst lies.
        n = np.arange(lo, lo + len(burst))
        cycles = (channel_mhz(channel) - center_mhz) * 1e6 + cfo_hz
        cycles = cycles / fs * n
        burst = burst * np.exp(2j * np.pi * (cycles - np.floor(cycles)))
        iq[lo:lo + len(burst)] += (amp * burst).astype(np.complex64)
        entry = {'start_sample': start}
        entry.update(p.sidecar())
        entry.update(channel=channel, channel_mhz=channel_mhz(channel),
                     symbol_phase=start % sps)
        entries.append(entry)

    # None: leave the buffer as the bursts alone. No noise draw, so the
    # complex64 shot is not joined by two float64 copies of itself.
    if snr_db is not None:
        noise_1mhz = amp ** 2 / 10 ** (snr_db / 10)
        sigma = np.sqrt(noise_1mhz * fs / 1e6 / 2)
        iq += (rng.normal(0, sigma, total) + 1j * rng.normal(0, sigma, total)).astype(np.complex64)

    sidecar = {
        'generator': 'SDR scripts/bt_synth_hop.py',
        'generator_commit': bt_synth.commit(),
        'lap': lap,
        'uap': uap,
        'clk': plan[0][0],
        'clk_convention': 'native CLK[27:0], at the first sample of the burst '
                          'it is given for; top-level clk is burst 0\'s',
        'hopping': True,
        'hop_channels': sorted({channel for _, channel in plan}),
        'channel_mhz': None,
        'bt_channel': None,
        'sample_rate': fs,
        'center_mhz': center_mhz,
        'cfo_hz': cfo_hz,
        'timing_frac': timing_frac,
        'snr_db': snr_db,
        'snr_bw_hz': 1e6,
        'modulation': {'scheme': 'GFSK', 'bt': br.GFSK_BT, 'h': br.GFSK_H,
                       'symbol_rate': br.SYMBOL_RATE},
        'slot_samples': slot_samples,
        'start_offset': int(start_offset),
        'symbol_phase': first % sps,
        'start_sample_meaning': 'the first sample of the access code\'s '
                                'preamble, before timing_frac is added',
        'bursts': entries,
    }
    return iq, sidecar


# --- the adapted sequence ------------------------------------------------------

#: The two 20-channel maps of the stage 3 set. 33-52 is 2435 to 2454 MHz and
#: 36-55 is 2438 to 2457 MHz, bands out to 2434 and 2458, both inside the
#: 2430 to 2459 MHz that 40 MS/s on 2445 MHz holds.
MAP_A = list(range(33, 53))
MAP_B = list(range(36, 56))

#: What the sidecar says about the instant, and about bluey's clock lock, which
#: is a fact about bluey and not about the file: worked out on its side.
INSTANT_MEANING = ("CLK[27:1] from which a map applies. afh_map and afh_instant are the FIRST "
                   "map and its instant only: it applies from the first burst, so every burst of "
                   "the file is on an adapted sequence. A file with a map change has more than "
                   "one map: afh_map_count says how many, and afh_maps lists every one, in "
                   "order, with its instant and the burst it starts on. Grade against afh_maps "
                   "and each burst's afh_map_index, not against afh_map alone.")
CLOCK_LOCK_NOTE = ("As of 2026-10-04, before bluey-ox-walker's fix of its pair roles: its CLK27 "
                   "lock on an adapted map follows a hop array equal to the Part G rule shifted "
                   "by one slot, so it returns the true CLK[27:1] minus 1 (two clock ticks). "
                   "Accept the true clock or that; a lock fixed since should return the true "
                   "clock. A basic sequence has no shift.")
HOP_KERNEL = ("apps/bt_hop.py, from Core v6.0 Vol 2 Part B 2.6, written from the "
              "specification text; checked against Part G section 2")


def map_channels(channels):
    """A map's channels as a sorted list of ints, each 0-78, at least one.

    A ``ValueError`` names the channel that is not one; a channel the tuning
    cannot hold is not caught here but by ``synthesise_afh``, before any sample
    is made, and by ``hop_plan`` for a ``hop_fn`` that is not a map.
    """
    out = set()
    for given in channels:
        try:
            channel = operator.index(given)
        except TypeError:
            raise ValueError("%r in a map is not a channel number" % (given,)) from None
        if not 0 <= channel < CHANNELS:
            raise ValueError("channel %d in a map is not 0-%d" % (channel, CHANNELS - 1))
        out.add(channel)
    if not out:
        raise ValueError("a map needs at least one channel")
    return sorted(out)


def map_index(instants, clk):
    """Which map the clock ``clk`` is under: the last whose instant is not
    after CLK[27:1] of ``clk``. The one rule, used by the hop function and by
    the sidecar's ``afh_map_index``, so the two cannot disagree."""
    index = bisect.bisect_right(instants, clk >> 1) - 1
    if index < 0:
        raise ValueError("clk %#x (CLK[27:1] %#x) is before the first map's instant %#x"
                         % (clk, clk >> 1, instants[0]))
    return index


def afh_hop_fn(maps, lap, uap):
    """A ``hop_fn`` for ``synthesise_hop``: the adapted hop sequence of
    ``apps/bt_hop.py`` under changing maps.

    ``maps`` is a list of ``(instant, channels)`` in increasing ``instant``, an
    instant being a CLK[27:1] value, ``channels`` the RF channels in use from
    it. A clock takes the last map whose instant is not after its CLK[27:1]; a
    clock before the first instant raises ``ValueError`` when it is asked
    about. The address is ``uap << 24 | lap``, of which the kernel reads A27-0.
    A bad map - a channel outside 0-78, no channel, instants that do not
    increase - raises ``ValueError`` here and now, not at some later burst.
    """
    maps = list(maps)
    if not maps:
        raise ValueError("no map")
    instants = []
    for instant, _ in maps:
        instant = operator.index(instant)
        if not 0 <= instant < 1 << 27:
            raise ValueError("instant %d is not a CLK[27:1] value" % instant)
        instants.append(instant)
    if any(later <= earlier for earlier, later in zip(instants, instants[1:])):
        raise ValueError("the maps' instants must increase: %s" % (instants,))
    masks = [sum(1 << c for c in map_channels(channels)) for _, channels in maps]
    address = uap << 24 | lap

    def hop_fn(clk):
        return bt_hop.hop_channel(clk, address, masks[map_index(instants, clk)])
    return hop_fn


def synthesise_afh(maps_spec, ptype='DH5', bursts=100, lap=0x9E8B33, uap=0x47, clk0=0x0123400,
                   fs=40e6, center_mhz=2445.0, cfo_hz=0.0, timing_frac=0.0, snr_db=30.0,
                   seed=1, start_offset=0, amplitude=None):
    """The samples and the sidecar of a capture on an adapted hop sequence.

    ``maps_spec`` is a list of ``(first_burst, channels)``: the map that applies
    from that burst on, the first from burst 0. Each ``first_burst`` becomes the
    instant of that burst's own clock, ``(clk0 + 2 * period * first_burst) >>
    1``, so a map begins on a whole burst and never in the middle of one.
    A map naming a channel the tuning cannot hold is refused at once, whether
    or not the sequence would land on it: it would, sooner or later.
    ``snr_db`` and ``amplitude`` are ``synthesise_hop``'s: None adds no noise,
    and ``amplitude`` None is ``bt_synth.AMPLITUDE``.
    """
    if ptype not in br.PACKET_TYPES:
        raise ValueError("no packet type %r" % (ptype,))
    period = br.PACKET_TYPES[ptype][1] + 1          # the slave's slot after it
    spec = [(operator.index(first), map_channels(channels)) for first, channels in maps_spec]
    firsts = [first for first, _ in spec]
    if not spec or firsts[0] != 0:
        raise ValueError("the first map applies from burst 0, so that every burst is on an "
                         "adapted sequence")
    if any(later <= earlier for earlier, later in zip(firsts, firsts[1:])):
        raise ValueError("the maps' first bursts must increase: %s" % (firsts,))
    if firsts[-1] >= bursts:
        raise ValueError("a map starts at burst %d, and the capture has %d bursts"
                         % (firsts[-1], bursts))
    for number, (first, channels) in enumerate(spec):
        for channel in channels:
            if outside_window(channel, fs, center_mhz):
                raise ValueError(
                    "map %d (from burst %d) names channel %d, %g MHz, whose band is outside "
                    "+/-%g MHz (%g x %g MS/s) of the %g MHz centre"
                    % (number, first, channel, channel_mhz(channel),
                       FLAT_FRACTION * fs / 1e6, FLAT_FRACTION, fs / 1e6, center_mhz))
    maps = [(((clk0 + 2 * period * first) & 0x0FFFFFFF) >> 1, channels)
            for first, channels in spec]
    iq, sidecar = synthesise_hop(afh_hop_fn(maps, lap, uap), ptype, bursts=bursts, lap=lap,
                                 uap=uap, clk0=clk0, fs=fs, center_mhz=center_mhz,
                                 cfo_hz=cfo_hz, timing_frac=timing_frac, snr_db=snr_db,
                                 seed=seed, start_offset=start_offset, amplitude=amplitude)
    instants = [instant for instant, _ in maps]
    for entry in sidecar['bursts']:
        entry['afh_map_index'] = map_index(instants, entry['clk'])
    entries = sidecar.pop('bursts')                 # the long list stays last
    sidecar.update(
        afh_map=maps[0][1],
        afh_instant=instants[0],
        afh_map_count=len(maps),
        afh_maps=[{'instant': instant, 'first_burst': first, 'channels': channels}
                  for (instant, channels), first in zip(maps, firsts)],
        afh_instant_meaning=INSTANT_MEANING,
        hop_kernel=HOP_KERNEL,
        address_for_hop=uap << 24 | lap,
        clock_lock_note=CLOCK_LOCK_NOTE)
    sidecar['bursts'] = entries
    return iq, sidecar


# --- the files -----------------------------------------------------------------

#: What ``--set stage3`` writes, and nothing else: each file's name and what
#: ``synthesise_afh`` is called with. Everything not named is its default, 40
#: MS/s on 2445 MHz. The seeds are fixed, so the set can be written again.
STAGE3_SET = [
    ('hop20_dh5', dict(ptype='DH5', bursts=100, maps_spec=[(0, MAP_A)], seed=3001)),
    ('hop20_dh3', dict(ptype='DH3', bursts=120, maps_spec=[(0, MAP_A)], seed=3002)),
    ('hop20_dh1', dict(ptype='DH1', bursts=400, maps_spec=[(0, MAP_A)], seed=3003)),
    ('hop20_dh1_long', dict(ptype='DH1', bursts=1000, maps_spec=[(0, MAP_A)], seed=3004)),
    ('hop20_imp_dh5', dict(ptype='DH5', bursts=120, maps_spec=[(0, MAP_A)], seed=3005,
                           cfo_hz=12e3, timing_frac=0.37, snr_db=15.0, start_offset=7)),
    ('hop20_change_dh5', dict(ptype='DH5', bursts=120, maps_spec=[(0, MAP_A), (60, MAP_B)],
                              seed=3006)),
]

#: The sets ``--set`` knows.
SETS = {'stage3': STAGE3_SET}


def channel_list(text):
    """``--map`` as a list of channels: ``33-52``, ``33,35,40`` or ``33-40,50``."""
    channels = []
    for piece in text.split(','):
        found = re.fullmatch(r'(\d+)(?:-(\d+))?', piece.strip())
        if not found:
            raise argparse.ArgumentTypeError(
                "%r is not a channel or a range of channels such as 33-52" % piece)
        low = int(found.group(1))
        high = int(found.group(2) or low)
        if high < low:
            raise argparse.ArgumentTypeError("%r runs backwards" % piece)
        channels.extend(range(low, high + 1))
    return channels


def channel_text(channels):
    """A sorted list of channels as ``33-52`` or ``33-35,40``."""
    runs = []
    for channel in channels:
        if runs and channel == runs[-1][1] + 1:
            runs[-1][1] = channel
        else:
            runs.append([channel, channel])
    return ','.join(str(a) if a == b else '%d-%d' % (a, b) for a, b in runs)


def write_capture(name, iq_dir, side_dir, **spec):
    """Make one capture from ``synthesise_afh``'s arguments and write
    ``synth_<name>.cf32`` into ``iq_dir`` and ``synth_<name>.json`` into
    ``side_dir``. Both the single file and every file of a set come through
    here. Returns the two paths."""
    iq, sidecar = synthesise_afh(**spec)
    os.makedirs(iq_dir, exist_ok=True)
    os.makedirs(side_dir, exist_ok=True)
    iq_path = os.path.join(iq_dir, 'synth_%s.cf32' % name)
    side_path = os.path.join(side_dir, 'synth_%s.json' % name)
    iq.astype('<c8', copy=False).tofile(iq_path)
    with open(side_path, 'w') as f:
        json.dump(sidecar, f, indent=1)
    print('%s  %d samples, %.1f ms, %d %s bursts, %d channels used: %s' % (
        iq_path, len(iq), len(iq) / sidecar['sample_rate'] * 1e3, len(sidecar['bursts']),
        sidecar['bursts'][0]['ptype'], len(sidecar['hop_channels']),
        channel_text(sidecar['hop_channels'])))
    print(side_path)
    return iq_path, side_path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('name', nargs='?', help='the file is synth_<name>.cf32; not used with --set')
    ap.add_argument('--set', choices=sorted(SETS), help='write a whole set of files, each with '
                    'the seed and settings of its row in the table, and no other option')
    ap.add_argument('--ptype', choices=['DH1', 'DM1', 'DH3', 'DM3', 'DH5', 'DM5'],
                    help='default DH5')
    ap.add_argument('--bursts', type=int, help='default 100')
    ap.add_argument('--map', type=channel_list, help='the channels in use: a range, 33-52, or '
                    'a comma list; default 33-52')
    ap.add_argument('--map-b', type=channel_list, help='the map from --change-at-burst on')
    ap.add_argument('--change-at-burst', type=int, help='the burst --map-b starts on')
    ap.add_argument('--cfo', type=float, help='Hz; default 0')
    ap.add_argument('--timing-frac', type=float, help='default 0')
    ap.add_argument('--snr', type=float, help='dB in 1 MHz; default 30')
    ap.add_argument('--seed', type=int, help='default 1')
    ap.add_argument('--start-offset', type=int, help='samples to move every burst by - the '
                    'symbol phase; default 0')
    ap.add_argument('--clk0', type=lambda text: int(text, 0), help='the master\'s clock at '
                    'burst 0, CLK1-0 = 00; default 0x0123400')
    ap.add_argument('--out', help='one folder for both files, instead of '
                    "bluey-ox-walker's data/iq and data/sidecar")
    args = ap.parse_args(argv)

    if args.out:
        iq_dir = side_dir = args.out
    else:
        iq_dir = os.path.join(bt_synth.BLUEY, 'data', 'iq')
        side_dir = os.path.join(bt_synth.BLUEY, 'data', 'sidecar')

    per_file = ['ptype', 'bursts', 'map', 'map_b', 'change_at_burst', 'cfo', 'timing_frac',
                'snr', 'seed', 'start_offset', 'clk0']
    if args.set:
        stray = [o for o in per_file if getattr(args, o) is not None]
        if args.name is not None or stray:
            ap.error('--set takes everything from its table: no name and no option but --out')
        runs = SETS[args.set]
    else:
        if args.name is None:
            ap.error('give the name of the file, or --set')
        if (args.map_b is None) != (args.change_at_burst is None):
            ap.error('--map-b and --change-at-burst go together')
        maps_spec = [(0, args.map if args.map is not None else MAP_A)]
        if args.map_b is not None:
            maps_spec.append((args.change_at_burst, args.map_b))
        spec = dict(maps_spec=maps_spec)
        for key, value in (('ptype', args.ptype), ('bursts', args.bursts), ('cfo_hz', args.cfo),
                           ('timing_frac', args.timing_frac), ('snr_db', args.snr),
                           ('seed', args.seed), ('start_offset', args.start_offset),
                           ('clk0', args.clk0)):
            if value is not None:        # what is not given is synthesise_afh's own default
                spec[key] = value
        runs = [(args.name, spec)]

    try:
        for name, spec in runs:
            write_capture(name, iq_dir, side_dir, **spec)
    except ValueError as e:
        ap.error(str(e))


if __name__ == '__main__':
    main()
