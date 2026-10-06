#!/usr/bin/env python3
"""The interferer train with one tone *between* channels, and where it sits.

    python scripts/bt_synth_between.py hop20_dh5_int_cwb4p5_p20 --tone cwb4p5_p20 --out DIR
    python scripts/bt_synth_between.py --set between [--out DIR]

For bluey-ox-walker (item C of its first batch). At 20 dB a tone *on* a channel
made the neighbours' bursts be reported one channel further from the tone; to
draw the verifier's response curve it wants the tone **between** channels. These
files are ``hop20_dh5_int_clean`` of ``scripts/bt_synth_interf.py`` (same bursts,
same seed 6101, same noise, same schedule) plus one continuous-wave tone at a
stated offset from the centre 2441.0 MHz, so ``file - clean`` is exactly the tone
(to the rounding of a float32 sum) and the clean file already delivered in the
``interf`` set is the pair of every one of them.

**How it is made.** ``bt_synth_interf.synthesise_interf`` makes the clean file
(this module does not copy or edit it), then ``bt_synth_interf.add_interferers``
adds the tone, after the noise, a block at a time, phase-continuous across blocks.
That function builds a tone from a *channel*, at ``(2402 + channel - centre)``
MHz, and takes a float as readily as an int, so the tone at ``+4.25`` MHz is
asked for as "channel" ``39 + 4.25``: the tone generator is the one of
``bt_synth_interf.py``, imported, not copied, and its initial phase is the same
``default_rng([seed, 5, 0])`` as theirs.

**The files** (``BETWEEN``): ``cwb4p25_p20`` (+4.25 MHz, 20 dB), ``cwb4p5_p10``,
``cwb4p5_p20`` (+4.5 MHz), ``cwb4p75_p20`` (+4.75 MHz); ``p`` is dB over
``noise_1mhz`` in 1 MHz, the tone's power being ``noise_1mhz * 10**(p/10)``.
Channel ``c`` is ``2402 + c`` MHz, so in the map 31-50 the tone at +4.5 MHz
(2445.5) is on the upper edge of channel 43 (2445) and the lower edge of channel 44
(2446).

**Per-burst truth**, new here ``interferer_offset_mhz`` and
``interferer_at_band_edge``: the signed distance in MHz *from the burst's channel
centre to the tone* (tone minus channel: tone at +4.5, burst on channel 43 at +4,
gives +0.5). ``interferer_overlap`` is true iff ``|offset| <= 0.5``, **both edges
inclusive**: a tone at +4.5 MHz is on the edge of channels 43 and 44 and is counted as
inside both; ``interferer_at_band_edge`` is true iff ``|offset|`` is 0.5 within 1e-9.
``interferer_power_in_band_db`` is the tone's level (dB over the noise in 1 MHz)
where ``interferer_overlap`` is true and null otherwise (the previous files'
convention; a tone MORE than half a MHz from a channel is not counted as power in its
band, though a receiver's filter would see some of it; at exactly 0.5 MHz it IS counted,
on both sides). **That figure is a grading convention, not a measured band power.** On
an edge burst (|offset| == 0.5) both neighbouring channels are labelled with the full
tone level, but a symmetric integration of the samples over each channel's +-0.5 MHz
band finds about half the tone's power in each: 3.01 dB lower (6.99 dB for p10, 16.99 dB
for p20). The labels are not changed for that. The tone is on for the whole file, so the
time overlap is always true.

``paired_with`` is ``hop20_dh5_int_clean`` only when the clean recipe is the delivered
one (seed 6101 and every other argument of ``synthesise_interf`` at its default); any
other seed, burst count, channel map, centre, block size, ... gives a different clean
train, and then ``paired_with`` is null: the file is still the clean train of ITS OWN
arguments plus the tone, and that train is ``synthesise_between()`` with no tone.
"""
import argparse
import inspect
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import bt_synth, bt_synth_hop as hop, bt_synth_interf as gi  # noqa: E402

SEED = gi.SEED
CENTER_MHZ = gi.CENTER_MHZ
FS = gi.FS
NOISE_1MHZ = gi.NOISE_1MHZ
#: ``interferer_at_band_edge`` is true when ``|offset|`` is half a channel within this.
EDGE_TOL_MHZ = 1e-9
HALF_BAND_MHZ = 0.5
PAIRED_WITH = 'hop20_dh5_int_clean'

#: The files of the set: key -> (tone offset from the centre in MHz, dB over the noise in 1 MHz).
BETWEEN = {
    'cwb4p25_p20': (4.25, 20.0),
    'cwb4p5_p10': (4.5, 10.0),
    'cwb4p5_p20': (4.5, 20.0),
    'cwb4p75_p20': (4.75, 20.0),
}

NOTE_PAIRED = (
    "The file is paired_with's file plus the tone: the same bursts, seed, noise and schedule, so file minus "
    "that file is the tone alone (to a float32 rounding). ")
NOTE_UNPAIRED = (
    "paired_with is null: this file's seed or other arguments are not those of the delivered "
    "hop20_dh5_int_clean, so no delivered file is its pair. It is still its own clean train (made with the "
    "same arguments and no tone, by synthesise_between with no tone) plus the tone. ")
NOTE = (
    "One continuous-wave tone, on for the whole file, phase-continuous across blocks, at tone_offset_mhz "
    "from center_mhz and tone_power_db over noise_1mhz in 1 MHz (power noise_1mhz * 10**(tone_power_db/10)). "
    "%s"
    "interferer_offset_mhz, per burst, is the signed "
    "distance from the burst's channel centre (channel_mhz) to the tone: tone minus channel, so a tone at "
    "+4.5 MHz and a burst on channel 43 (+4) gives +0.5. interferer_overlap is true iff |offset| <= 0.5: "
    "the tone lies inside the channel's +-0.5 MHz band, BOTH EDGES INCLUSIVE, so a tone at +4.5 MHz is on the "
    "edge of channels 43 and 44 and is counted as inside both. interferer_at_band_edge is true iff |offset| "
    "equals 0.5 within 1e-9 (so those bursts are overlap true and edge true). "
    "interferer_power_in_band_db is tone_power_db where interferer_overlap is true, and null otherwise "
    "(a tone more than 0.5 MHz away is not counted, though a filter's skirt would pass some of it). "
    "THAT FIGURE IS A GRADING CONVENTION, NOT A MEASURED BAND POWER: on an edge burst (interferer_at_band_edge "
    "true) both neighbouring channels are labelled with the full tone level, but a symmetric integration of the "
    "samples over each channel's +-0.5 MHz band finds about half the tone's power in each, 3.01 dB lower "
    "(6.99 dB for a 10 dB tone, 16.99 dB for a 20 dB one). The labels are not changed for that.")


def is_canonical(seed, block_samples, kw):
    """Whether these arguments make the delivered ``hop20_dh5_int_clean``'s recipe: seed 6101,
    the default block size and every ``synthesise_interf`` argument at its default."""
    if seed != gi.SEED or block_samples != gi.BLOCK_SAMPLES:
        return False
    defaults = {k: v.default for k, v in inspect.signature(gi.synthesise_interf).parameters.items()}
    return all(k in defaults and v == defaults[k] for k, v in kw.items())


def tone_frequency_mhz(center_mhz, offset_mhz):
    """The tone's absolute frequency."""
    return center_mhz + offset_mhz


def tone_spec(offset_mhz, power_db, center_mhz=CENTER_MHZ):
    """The spec ``bt_synth_interf.add_interferers`` wants for a tone ``offset_mhz`` from the
    centre: its "channel" is a float, the tone being at ``(2402 + channel - centre)`` MHz."""
    return dict(kind='cw', channel=center_mhz - 2402.0 + offset_mhz, level_db=power_db)


def truth_offset(tone_mhz, channel_mhz):
    """Signed distance, MHz, from the channel's centre to the tone."""
    return tone_mhz - channel_mhz


def overlaps(offset_mhz):
    """The tone is inside the channel's band, both edges inclusive."""
    return abs(offset_mhz) <= HALF_BAND_MHZ + EDGE_TOL_MHZ


def at_edge(offset_mhz):
    return abs(abs(offset_mhz) - HALF_BAND_MHZ) <= EDGE_TOL_MHZ


def synthesise_between(offset_mhz=None, power_db=None, key=None, seed=None, block_samples=gi.BLOCK_SAMPLES,
                       **kw):
    """The samples and the sidecar of the clean interferer train plus the tone;
    ``offset_mhz`` None gives the clean train itself (what ``bt_synth_interf`` makes).
    ``kw`` goes to ``bt_synth_interf.synthesise_interf`` (bursts, channels, ...)."""
    seed = SEED if seed is None else seed
    center = kw.get('center_mhz', CENTER_MHZ)
    fs = kw.get('fs', FS)
    iq, sidecar = gi.synthesise_interf('clean', seed=seed, block_samples=block_samples, **kw)
    if offset_mhz is None:
        return iq, sidecar
    gi.add_interferers(iq, [tone_spec(offset_mhz, power_db, center)], [], seed, fs, center, block_samples)
    tone_mhz = tone_frequency_mhz(center, offset_mhz)
    paired = is_canonical(seed, block_samples, kw)
    for e in sidecar['bursts']:
        off = truth_offset(tone_mhz, e['channel_mhz'])
        hit = overlaps(off)
        e['interferer_offset_mhz'] = off
        e['interferer_overlap'] = bool(hit)
        e['interferer_at_band_edge'] = bool(at_edge(off))
        e['interferer_power_in_band_db'] = float(power_db) if hit else None
    keys = sorted(sidecar['bursts'][0])
    for k in ('interferer_name', 'interferers', 'interferer_note'):
        sidecar.pop(k, None)
    sidecar.update({
        'generator': 'SDR scripts/bt_synth_between.py',
        'generator_commit': bt_synth.commit(),
        'interferer_name': key or 'cwb%s_p%g' % (('%g' % offset_mhz).replace('.', 'p'), power_db),
        'interferers': [{'kind': 'cw', 'offset_mhz': offset_mhz, 'frequency_mhz': tone_mhz,
                         'level_db': power_db, 'duty': 1.0, 'frames': None,
                         'level_meaning': 'tone power over noise_1mhz, in dB: noise_1mhz * 10**(level_db/10)'}],
        'interferer_note': NOTE % (NOTE_PAIRED if paired else NOTE_UNPAIRED),
        'n_samples': int(len(iq)),
        'tone_offset_mhz': offset_mhz,
        'tone_power_db': float(power_db),
        'paired_with': PAIRED_WITH if paired else None,
        'n_bursts_overlapped': sum(e['interferer_overlap'] for e in sidecar['bursts']),
        'n_bursts_at_band_edge': sum(e['interferer_at_band_edge'] for e in sidecar['bursts']),
        'per_burst_keys': keys,
    })
    for k in ('interferer_offset_mhz', 'interferer_overlap', 'interferer_at_band_edge',
              'interferer_power_in_band_db'):
        sidecar[k] = None                                  # null at the top, per burst below
    sidecar['bursts'] = sidecar.pop('bursts')              # the long list last
    return iq, sidecar


# --- the files -----------------------------------------------------------------

BETWEEN_SET = [('hop20_dh5_int_%s' % key, dict(offset_mhz=o, power_db=p, key=key, seed=SEED))
               for key, (o, p) in BETWEEN.items()]
SETS = {'between': BETWEEN_SET}


def write_file(name, out_dir, **spec):
    """Make one file and write ``synth_<name>.cf32`` and ``.json`` into ``out_dir``."""
    os.makedirs(out_dir, exist_ok=True)
    iq_path = os.path.join(out_dir, 'synth_%s.cf32' % name)
    side_path = os.path.join(out_dir, 'synth_%s.json' % name)
    iq, sidecar = synthesise_between(**spec)
    iq.astype('<c8', copy=False).tofile(iq_path)
    print('%s  %d samples, %.2f s, %d bursts, %d overlapped, %d on a band edge' % (
        iq_path, len(iq), len(iq) / sidecar['sample_rate'], len(sidecar['bursts']),
        sidecar['n_bursts_overlapped'], sidecar['n_bursts_at_band_edge']), flush=True)
    with open(side_path, 'w') as f:
        json.dump(sidecar, f, indent=1)
    print(side_path)
    return iq_path, side_path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('name', nargs='?', help='the file is synth_<name>.cf32; not used with --set')
    ap.add_argument('--set', help='write the whole set, and no other option but --out; one of %s'
                    % ', '.join(sorted(SETS)))
    ap.add_argument('--tone', choices=sorted(BETWEEN), help='which tone (offset and level) the file has')
    ap.add_argument('--bursts', type=int, help='default %d' % gi.BURSTS)
    ap.add_argument('--seed', type=int, help='default %d' % SEED)
    ap.add_argument('--out', default='/media/user/4TB/sdr-synth-tmp', help='the folder of both files')
    args = ap.parse_args(argv)

    if args.set:
        if args.set not in SETS:
            ap.error('no set %r: the sets are %s' % (args.set, ', '.join(sorted(SETS))))
        if args.name is not None or any(getattr(args, o) is not None for o in ('tone', 'bursts', 'seed')):
            ap.error('--set takes everything from its table: no name and no option but --out')
        runs = SETS[args.set]
    else:
        if args.name is None or args.tone is None:
            ap.error('give the name of the file and --tone, or --set')
        o, p = BETWEEN[args.tone]
        runs = [(args.name, dict(offset_mhz=o, power_db=p, key=args.tone, bursts=args.bursts or gi.BURSTS,
                                 seed=SEED if args.seed is None else args.seed))]
    try:
        for name, spec in runs:
            write_file(name, args.out, **spec)
    except ValueError as e:
        ap.error(str(e))


if __name__ == '__main__':
    main()
