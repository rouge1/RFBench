#!/usr/bin/env python3
"""A Bluetooth Basic Rate train on the capture's centre channel, with and without a spur at DC.

    python scripts/bt_synth_dc.py dc_dh5_ch39_cw25 --ptype DH5 --channel 39 --spur cw --spur-db 25
    python scripts/bt_synth_dc.py dc_dh5_ch43_clean --channel 43 --seed 4001
    python scripts/bt_synth_dc.py dc40_dh5_ch39_snr12_cw25 --fs 40e6 --snr-steps 12 --spur cw
    python scripts/bt_synth_dc.py --set dc --out /media/user/4TB/sdr-synth-tmp/dc

    from scripts import bt_synth_dc
    iq, sidecar = bt_synth_dc.synthesise_dc(ptype='DH1', bursts=24, spacing_ms=5.0,
                                            snr_steps=[12, 20], spur='cw', spur_db=25)

Bluey-ox-walker's receiver has a "device-aware DC block", a 64-sample
block-mean subtraction, which deletes whatever sits at the capture centre:
channel 39 when the capture is centred on 2441.0 MHz. To settle whether the
block should be on, off or longer it needs bursts whose truth on the centre
channel is known, with and without a spur at DC, and the same bursts moved
off the centre as controls. Real captures have no truth. Nothing is
transmitted and no radio is opened.

**A file** is one LAP, one channel for the whole file and one packet type, 800
bursts a file in the named sets, one every 12.5 ms (``--spacing-ms``: 20
slots, so the clock advances 40 ticks a burst and every burst is on a master
slot; a file is about 10 s and a DH5 of 2.9 ms never comes near the next). At
least 20 ms of noise comes before burst 0 and at least 12 ms after the last
burst's end. The burst number k starts at ``first + k * spacing`` plus, for an
odd k, half a symbol: the symbol phases alternate between 0 and ``sps / 2``,
the two phases bluey's detector is and is not blind to; ``first`` is 25 ms in
and half a slot, a whole number of symbols, so no burst lies near a slot
boundary of the file's own grid.

**The noise floor never changes.** Noise in 1 MHz is ``NOISE_1MHZ =
bt_synth.AMPLITUDE ** 2 / 100`` in every file, at every sample rate; a burst of
SNR s dB has the constant-envelope amplitude ``sqrt(NOISE_1MHZ * 10 ** (s /
10))``. ``--snr-steps 8,12,16,20`` cuts the bursts into equal consecutive
blocks, one per step, and only the burst amplitude moves between them.

**The spur** (``--spur cw``) is a continuous-wave tone at 0 Hz, present for the
whole file, bursts or not, of constant complex amplitude and a random but
fixed phase. ``--spur-db`` is its level over the noise floor *in one bin of a
1024-point rectangular-window FFT*: the noise has ``sigma2 * 1024`` power in a
bin, with ``sigma2 = NOISE_1MHZ * fs / 1e6`` the complex noise power per
sample, and a tone of power P has ``1024 ** 2 * P`` in its bin, so
``P = sigma2 * 10 ** (db / 10) / 1024``. The noise floor is the mean bin
power; a median bin sits 1.6 dB below it (an exponential's median is ln 2 of
its mean), which a reader measuring with the median has to add back. The
sidecar's ``spur`` block says all of this. ``--spur-drift-hz`` raises the
tone's frequency linearly from 0 Hz to that over the file, phase-continuous,
so the tone is no longer in one bin of a long transform, and ``tone_hz_start``
and ``tone_hz_end`` record it. The carrier offset ``--cfo`` moves every burst
and not the tone, as in ``bt_synth.py``.

**Memory is bounded.** The file is built and written 2**22 samples at a time:
the noise of each block comes from ``default_rng([seed, 0, block])``, the tone
from the sample number, and a burst (``default_rng([seed, 1, k])`` gives its
phase and timing fraction) is added wherever it overlaps the block, the carrier
shift being one oscillator at the absolute sample index as in
``bt_synth_hop.py``. A burst cut by a block edge is generated again for the
next block, so a file is the same bytes however it is cut into the pieces it
is made of, apart from the noise, which belongs to the block. The same command
twice gives byte-identical files.

The sidecar is ``bt_synth.py``'s: ``air_bits`` is left out of every burst and
``air_bits_omitted`` says so, ``symbol_phases`` names the two phases, ``snr_db``
is per burst (``snr_steps`` is at the top, and there is no top-level ``snr_db``),
and ``offset_hz`` is a burst's carrier relative to the capture centre.
``--set dc`` is the 26 files, ``--set dc40`` the four 40 MS/s files.
"""
import argparse
import json
import math
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from scripts import bt_synth, bt_synth_hop as hop  # noqa: E402

#: The noise in 1 MHz of every file: a burst of ``bt_synth.AMPLITUDE`` has 20 dB.
NOISE_1MHZ = bt_synth.AMPLITUDE ** 2 / 100

#: Samples a block of the file is built from: float64 temporaries of this many
#: complex samples are about 70 MB each.
BLOCK = 2 ** 22

#: The FFT the spur's level is defined in.
SPUR_FFT = 1024

#: Seconds of noise before burst 0 (it must be at least 20 ms) and after the
#: last burst ends (at least 12 ms; an extra millisecond keeps clear of it).
LEAD_S = 0.025
TAIL_S = 0.013

SPUR_DEFINITION = ("Tone power P = sigma2 * 10**(db/10) / 1024, sigma2 = noise_1mhz * fs / 1e6 the "
                   "complex noise power per sample. In one bin of a 1024-point rectangular-window "
                   "FFT the tone has 1024**2 * P and the noise 1024 * sigma2 (its mean; a median "
                   "bin is 1.6 dB lower), so the tone is db_over_noise_in_one_bin dB over the "
                   "noise floor in that bin, excluding the noise in the tone bin itself. The raw "
                   "reading, bin 0 over the mean of the other bins, is therefore "
                   "10*log10(10**(db/10) + 1) dB: 15.12, 25.01 and 35.00 dB for 15, 25 and 35. "
                   "The tone is at DC, constant amplitude, present for the whole file; with a "
                   "drift its frequency rises linearly from tone_hz_start to tone_hz_end over "
                   "the file, phase-continuous. The same db is 3 dB stronger in absolute terms "
                   "at 40 MS/s than at 20, because the 1024-point bin is twice as wide there and "
                   "holds twice the noise.")
NO_SPUR_DEFINITION = "No spur: the file is bursts and noise only."
CENTRE_NOTE = ("Channel 39 (2441 MHz) sits at exactly 0 Hz of a capture centred on 2441.0 MHz. "
               "In the files with a carrier offset the bursts are at the offset (+-30 kHz) from "
               "it, as offset_hz says.")
PER_BURST_KEYS = ['start_sample', 'timing_frac', 'symbol_phase', 'snr_db', 'clk', 'ptype',
                  'channel', 'channel_mhz', 'cfo_hz', 'offset_hz', 'burst_index', 'lt_addr',
                  'flow', 'arqn', 'seqn', 'header18', 'payload_hex', 'payload_full_hex',
                  'payload_valid']


def burst_samples(plan, k):
    """Burst k's samples, as ``complex128``: unit-amplitude GFSK, its own
    random phase, scaled to its SNR, and shifted to its carrier with the
    absolute sample numbers. Returns ``(lo, samples)`` with ``lo`` the file's
    sample number of the first."""
    fs = plan['fs']
    b = plan['burst'][k]
    gen = np.random.default_rng([plan['seed'], 1, k])
    phase = gen.uniform(0, 2 * np.pi)
    gen.uniform()                                  # the timing fraction, drawn in plan_dc
    burst, lead = br.gfsk(b['packet'].bits, fs, h=br.GFSK_H, delay=b['timing_frac'])
    lo = b['start_sample'] - lead
    n = np.arange(lo, lo + len(burst))
    cycles = b['offset_hz'] / fs * n
    rot = np.exp(2j * np.pi * (cycles - np.floor(cycles)))
    return lo, burst.astype(np.complex128) * (b['amplitude'] * np.exp(1j * phase)) * rot


def plan_dc(ptype='DH5', channel=39, bursts=800, spacing_ms=12.5, clk0=0x0123400,
            snr_steps=(8, 12, 16, 20), spur='none', spur_db=25.0, spur_drift_hz=0.0,
            cfo_hz=0.0, fs=20e6, center_mhz=2441.0, seed=4001, lap=0x9E8B33, uap=0x47):
    """Everything about a file but its samples: the sidecar, and what the
    blocks are made from. Returns a plan dict whose ``sidecar`` is complete."""
    if ptype not in br.PACKET_TYPES or br.PACKET_TYPES[ptype][1] not in (1, 3, 5):
        raise ValueError("no data packet type %r" % (ptype,))
    if clk0 % 4:
        raise ValueError("clk0 must be a master slot's clock: CLK1-0 = 00")
    if spur not in ('none', 'cw'):
        raise ValueError("--spur is none or cw, not %r" % (spur,))
    if spur == 'none' and spur_drift_hz:
        raise ValueError("a drift needs a spur")
    if bursts < 1:
        raise ValueError("a file needs at least one burst")
    steps = [float(s) for s in snr_steps]
    if not steps or bursts < len(steps):
        raise ValueError("a file needs a burst for every SNR step")
    if not 0 <= channel < hop.CHANNELS:
        raise ValueError("channel %d is not 0-%d" % (channel, hop.CHANNELS - 1))
    if hop.outside_window(channel, fs, center_mhz):
        raise ValueError("channel %d is outside +/-%g MHz (%g x %g MS/s) of the %g MHz centre"
                         % (channel, hop.FLAT_FRACTION * fs / 1e6, hop.FLAT_FRACTION, fs / 1e6,
                            center_mhz))
    slot = fs * br.SLOT_US * 1e-6
    sps = fs / br.SYMBOL_RATE
    if abs(slot - round(slot)) > 1e-6 or abs(sps - round(sps)) > 1e-9 or int(round(sps)) % 2:
        raise ValueError("%g S/s does not divide a slot and a symbol into whole, even samples" % fs)
    slot, sps = int(round(slot)), int(round(sps))
    slots_in_spacing = spacing_ms * 1000.0 / br.SLOT_US
    if abs(slots_in_spacing - round(slots_in_spacing)) > 1e-6:
        raise ValueError("--spacing-ms %g is not a whole number of 625 us slots" % spacing_ms)
    slots_in_spacing = int(round(slots_in_spacing))
    packet_slots = br.PACKET_TYPES[ptype][1]
    if slots_in_spacing % 2:
        raise ValueError("--spacing-ms %g is %d slots: every burst must be on a master slot, "
                         "an even number of slots apart" % (spacing_ms, slots_in_spacing))
    if slots_in_spacing < packet_slots + 1:
        raise ValueError("--spacing-ms %g is too short for a %s, which needs %d slots"
                         % (spacing_ms, ptype, packet_slots + 1))
    spacing = slots_in_spacing * slot
    clk_step = 2 * slots_in_spacing
    first = int(round(LEAD_S * fs)) + (slot // 2) // sps * sps
    first -= first % sps                                  # a whole number of symbols
    offset_hz = (hop.channel_mhz(channel) - center_mhz) * 1e6 + cfo_hz

    entries, info = [], []
    for k in range(bursts):
        clk = (clk0 + clk_step * k) & 0x0FFFFFFF
        step = k * len(steps) // bursts
        snr = steps[step]
        gen = np.random.default_rng([seed, 1, k])
        gen.uniform(0, 2 * np.pi)                         # the phase, drawn in burst_samples
        frac = float(gen.uniform())
        start = first + k * spacing + (0 if k % 2 == 0 else sps // 2)
        body = br.seq_body(k, br.PACKET_TYPES[ptype][4])
        p = br.Packet(lap, uap, clk, ptype, body, lt_addr=1, flow=1, arqn=1, seqn=k & 1)
        entry = {'start_sample': start}
        entry.update(p.sidecar())
        entry.pop('air_bits')
        entry.update(timing_frac=frac, symbol_phase=start % sps, channel=channel,
                     channel_mhz=hop.channel_mhz(channel), snr_db=snr, cfo_hz=cfo_hz,
                     offset_hz=offset_hz, burst_index=k)
        entries.append(entry)
        info.append({'packet': p, 'start_sample': start, 'timing_frac': frac,
                     'amplitude': math.sqrt(NOISE_1MHZ * 10 ** (snr / 10)),
                     'offset_hz': offset_hz})
    last = info[-1]
    lead = int(np.ceil(2.0e-6 * fs)) + 1                  # what br.gfsk puts before bit 0
    last_end = last['start_sample'] + int(np.ceil(len(last['packet'].bits) * sps)) + lead
    total = last_end + int(round(TAIL_S * fs))
    duration = total / fs

    sigma2 = NOISE_1MHZ * fs / 1e6
    if spur == 'cw':
        power = sigma2 * 10 ** (spur_db / 10) / SPUR_FFT
        phase0 = float(np.random.default_rng([seed, 2]).uniform(0, 2 * np.pi))
        spur_block = {'kind': 'cw', 'db_over_noise_in_one_bin': spur_db,
                      'raw_bin0_over_mean_noise_bin_db': 10 * math.log10(10 ** (spur_db / 10) + 1),
                      'bin_fft_size': SPUR_FFT, 'window': 'rectangular',
                      'tone_hz_start': 0.0, 'tone_hz_end': float(spur_drift_hz),
                      'tone_power': power, 'tone_amplitude': math.sqrt(power),
                      'tone_phase_rad': phase0,
                      'tone_over_noise_1mhz_db': 10 * math.log10(power / NOISE_1MHZ),
                      'tone_over_burst_db_at_each_step': [
                          10 * math.log10(power / (NOISE_1MHZ * 10 ** (st / 10))) for st in steps],
                      'definition': SPUR_DEFINITION}
    else:
        power = phase0 = 0.0
        spur_block = {'kind': 'none', 'db_over_noise_in_one_bin': None,
                      'raw_bin0_over_mean_noise_bin_db': None,
                      'bin_fft_size': SPUR_FFT, 'window': 'rectangular',
                      'tone_hz_start': 0.0, 'tone_hz_end': 0.0, 'tone_power': 0.0,
                      'tone_amplitude': 0.0, 'tone_phase_rad': None,
                      'tone_over_noise_1mhz_db': None, 'tone_over_burst_db_at_each_step': None,
                      'definition': NO_SPUR_DEFINITION}
    sidecar = {
        'generator': 'SDR scripts/bt_synth_dc.py',
        'generator_commit': bt_synth.commit(),
        'lap': lap,
        'uap': uap,
        'clk': clk0,
        'clk_convention': 'native CLK[27:0], at the first sample of the burst '
                          'it is given for; top-level clk is burst 0\'s',
        'channel': channel,
        'channel_mhz': hop.channel_mhz(channel),
        'bt_channel': channel,
        'centre_channel': 39,
        'centre_channel_note': CENTRE_NOTE,
        'ptype': ptype,
        'sample_rate': fs,
        'center_mhz': center_mhz,
        'cfo_hz': cfo_hz,
        'snr_steps': steps,
        'snr_db': None,
        'timing_frac': None,
        'symbol_phase': None,
        'per_burst_keys': PER_BURST_KEYS,
        'per_burst_note': 'snr_db, timing_frac and symbol_phase vary burst by burst and are null here: '
                          'read them from each burst',
        'start_offset': first % slot,
        'start_offset_note': 'burst 0 begins this many samples into a slot of the file\'s own grid',
        'snr_bw_hz': 1e6,
        'noise_1mhz': NOISE_1MHZ,
        'spur': spur_block,
        'modulation': {'scheme': 'GFSK', 'bt': br.GFSK_BT, 'h': br.GFSK_H,
                       'symbol_rate': br.SYMBOL_RATE},
        'slot_samples': slot,
        'spacing_ms': spacing_ms,
        'spacing_samples': spacing,
        'seed': seed,
        'total_samples': total,
        'symbol_phases': [0, sps // 2],
        'symbol_phase_note': 'burst k starts at a whole number of symbols plus 0 for even k and '
                             'the second entry of symbol_phases for odd k, before timing_frac; '
                             'even bursts start at slot offset start_offset (6240 at 20 MS/s) '
                             'and odd ones 10 samples later (6250), so not exactly mid-slot',
        'start_sample_meaning': 'the first sample of the access code\'s '
                                'preamble, before timing_frac is added',
        'air_bits_omitted': True,
        'bursts': entries,
    }
    return {'sidecar': sidecar, 'burst': info, 'fs': fs, 'seed': seed, 'total': total,
            'sigma': math.sqrt(sigma2 / 2), 'spur_amp': math.sqrt(power), 'spur_phase': phase0,
            'drift_rate': spur_drift_hz / duration, 'spur': spur, 'sps': sps,
            'cfo_hz': cfo_hz}


def blocks(plan):
    """The file, ``BLOCK`` samples at a time, as ``complex64``."""
    fs, total, bursts = plan['fs'], plan['total'], plan['burst']
    # Where each burst lies, from its start and its length: ``br.gfsk`` puts
    # ``lead`` samples before the first bit and as many after the last.
    lead = int(np.ceil(2.0e-6 * fs)) + 1
    spans = [(b['start_sample'] - lead,
              b['start_sample'] + int(np.ceil(len(b['packet'].bits) * plan['sps'])) + lead)
             for b in bursts]
    cache = {}
    nxt = 0                                            # first burst that may still overlap
    for index, lo in enumerate(range(0, total, BLOCK)):
        hi = min(lo + BLOCK, total)
        m = hi - lo
        gen = np.random.default_rng([plan['seed'], 0, index])
        x = plan['sigma'] * (gen.normal(0, 1, m) + 1j * gen.normal(0, 1, m))
        if plan['spur'] == 'cw':
            t = np.arange(lo, hi) / fs
            cycles = 0.5 * plan['drift_rate'] * t * t       # the integral of the frequency
            x += plan['spur_amp'] * np.exp(1j * (plan['spur_phase']
                                                 + 2 * np.pi * (cycles - np.floor(cycles))))
        while nxt < len(bursts) and spans[nxt][1] <= lo:
            cache.pop(nxt, None)
            nxt += 1
        k = nxt
        while k < len(bursts) and spans[k][0] < hi:
            if k not in cache:
                cache[k] = burst_samples(plan, k)
            blo, s = cache[k]
            a, b = max(lo, blo), min(hi, blo + len(s))
            if a < b:
                x[a - lo:b - lo] += s[a - blo:b - blo]
            k += 1
        yield x.astype(np.complex64)


def synthesise_dc(**spec):
    """The whole file in memory, and its sidecar: for a small run. The command
    line and the sets go through ``write_capture`` and never hold a file."""
    plan = plan_dc(**spec)
    iq = np.concatenate(list(blocks(plan)))
    return iq, plan['sidecar']


def write_capture(name, out_dir, **spec):
    """Make one file and write ``synth_<name>.cf32`` and ``synth_<name>.json``
    into ``out_dir``, block by block. Both a single run and every file of a
    set come through here. Returns the two paths."""
    t0 = time.time()
    plan = plan_dc(**spec)
    os.makedirs(out_dir, exist_ok=True)
    iq_path = os.path.join(out_dir, 'synth_%s.cf32' % name)
    side_path = os.path.join(out_dir, 'synth_%s.json' % name)
    with open(iq_path, 'wb') as f:
        for block in blocks(plan):
            f.write(block.astype('<c8', copy=False).tobytes())
    with open(side_path, 'w') as f:
        json.dump(plan['sidecar'], f, indent=1)
    side = plan['sidecar']
    print('%s  %d samples, %.2f s, %d %s bursts on channel %d, spur %s, %.1f s to make' % (
        iq_path, plan['total'], plan['total'] / plan['fs'], len(side['bursts']), side['ptype'],
        side['channel'], side['spur']['kind'], time.time() - t0), flush=True)
    return iq_path, side_path


# --- the sets ------------------------------------------------------------------

#: What the 20 MS/s set holds per packet type, in order: the spur family, the
#: controls and the carrier offsets.
STEPS = [8, 12, 16, 20]


def set_runs(name):
    """``(file name, spec)`` of every file of a named set, in order. The seed is
    the type's clean channel 39 file's (4001 for DH5, 4008 for DH1) for every
    800-burst file, so payloads, SNR blocks, timing fractions, phases and noise
    are the same and a spur or control file differs from the clean one only by
    its tone or its carrier; the carrier-offset files of a type share the seed
    of its cfo0 file (4021 DH5, 4024 DH1); the four dc40 files share 4027."""
    if name not in ('dc', 'dc40'):
        raise ValueError("no set %r: dc or dc40" % (name,))
    runs = []
    if name == 'dc':
        for ptype in ('DH5', 'DH1'):
            p = ptype.lower()
            runs.append(('dc_%s_ch39_clean' % p, dict(ptype=ptype)))
            for db in (15, 25, 35):
                runs.append(('dc_%s_ch39_cw%d' % (p, db), dict(ptype=ptype, spur='cw', spur_db=db)))
            for db in (15, 25, 35):
                runs.append(('dc_%s_ch39_drift%d' % (p, db),
                             dict(ptype=ptype, spur='cw', spur_db=db, spur_drift_hz=1000.0)))
        for ptype in ('DH5', 'DH1'):
            p = ptype.lower()
            for ch in (38, 40, 43):
                runs.append(('dc_%s_ch%d_clean' % (p, ch), dict(ptype=ptype, channel=ch)))
        for ptype in ('DH5', 'DH1'):
            p = ptype.lower()
            for tag, cfo in (('cfo0', 0.0), ('cfopl30k', 30e3), ('cfomi30k', -30e3)):
                runs.append(('dc_%s_ch39_snr16_%s' % (p, tag),
                             dict(ptype=ptype, snr_steps=[16], bursts=200, cfo_hz=cfo)))
        out = []
        # One seed per packet type for every 800-burst file: the clean file's, so
        # a spur or control file has the clean file's noise, timing fractions,
        # symbol phases and carrier phases and differs from it only by the tone
        # or the carrier. The 200-burst carrier-offset files share one seed per
        # type, that of the type's cfo0 file, and differ only by the offset.
        clean_seed, cfo_seed = {}, {}
        for i, (n, spec) in enumerate(runs):
            if n.endswith('_ch39_clean'):
                clean_seed[spec['ptype']] = 4001 + i
            if n.endswith('_cfo0'):
                cfo_seed[spec['ptype']] = 4001 + i
        for i, (n, spec) in enumerate(runs):
            spec = dict(spec)
            spec.setdefault('channel', 39)
            spec.setdefault('bursts', 800)
            spec.setdefault('snr_steps', list(STEPS))
            spec.setdefault('spur', 'none')
            spec.setdefault('spur_db', 25)
            spec.setdefault('spur_drift_hz', 0.0)
            spec.setdefault('cfo_hz', 0.0)
            spec['seed'] = cfo_seed[spec['ptype']] if spec['bursts'] == 200 \
                else clean_seed[spec['ptype']]
            out.append((n, spec))
        return out
    for n, snr, spur in ([('dc40_dh5_ch39_snr12_clean', 12, 'none'),
                                        ('dc40_dh5_ch39_snr20_clean', 20, 'none'),
                                        ('dc40_dh5_ch39_snr12_cw25', 12, 'cw'),
                                        ('dc40_dh5_ch39_snr20_cw25', 20, 'cw')]):
        runs.append((n, dict(ptype='DH5', channel=39, bursts=800, snr_steps=[snr], spur=spur,
                             spur_db=25, spur_drift_hz=0.0, cfo_hz=0.0, fs=40e6, seed=4027)))
    return runs


def snr_list(text):
    return [float(s) for s in text.split(',')]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('name', nargs='?', help='the file is synth_<name>.cf32; not used with --set')
    ap.add_argument('--set', help='write a whole named set, dc (26 files) or dc40 (4), each with '
                    'its own seed and settings, and no other option but --out')
    ap.add_argument('--ptype', choices=['DH1', 'DM1', 'DH3', 'DM3', 'DH5', 'DM5'],
                    help='default DH5')
    ap.add_argument('--channel', type=int, help='RF channel, 2402 + n MHz; default 39, the '
                    'centre channel')
    ap.add_argument('--bursts', type=int, help='default 800')
    ap.add_argument('--spacing-ms', type=float, help='between bursts: an even number of 625 us '
                    'slots; default 12.5')
    ap.add_argument('--clk0', type=lambda text: int(text, 0), help='the clock of burst 0, '
                    'CLK1-0 = 00; default 0x0123400')
    ap.add_argument('--snr-steps', type=snr_list, help='dB in 1 MHz, one per equal block of '
                    'bursts; default 8,12,16,20')
    ap.add_argument('--spur', choices=['none', 'cw'], help='a tone exactly at DC; default none')
    ap.add_argument('--spur-db', type=float, help='its level over the noise in one bin of a '
                    '1024-point rectangular FFT; default 25')
    ap.add_argument('--spur-drift-hz', type=float, help='the tone rises from 0 Hz to this over '
                    'the file; default 0')
    ap.add_argument('--cfo', type=float, help='Hz, every burst and not the tone; default 0')
    ap.add_argument('--fs', type=float, help='sample rate; default 20e6')
    ap.add_argument('--center-mhz', type=float, help='default 2441.0')
    ap.add_argument('--seed', type=int, help='default 4001')
    ap.add_argument('--out', default='/media/user/4TB/sdr-synth-tmp', help='the folder for both '
                    'files; default %(default)s')
    args = ap.parse_args(argv)

    per_file = ['ptype', 'channel', 'bursts', 'spacing_ms', 'clk0', 'snr_steps', 'spur',
                'spur_db', 'spur_drift_hz', 'cfo', 'fs', 'center_mhz', 'seed']
    if args.set:
        stray = [o for o in per_file if getattr(args, o) is not None]
        if args.name is not None or stray:
            ap.error('--set takes everything from its table: no name and no option but --out')
        try:
            runs = set_runs(args.set)
        except ValueError as e:
            ap.error(str(e))
    else:
        if args.name is None:
            ap.error('give the name of the file, or --set')
        spec = {}
        for key, value in (('ptype', args.ptype), ('channel', args.channel),
                           ('bursts', args.bursts), ('spacing_ms', args.spacing_ms),
                           ('clk0', args.clk0), ('snr_steps', args.snr_steps),
                           ('spur', args.spur), ('spur_db', args.spur_db),
                           ('spur_drift_hz', args.spur_drift_hz), ('cfo_hz', args.cfo),
                           ('fs', args.fs), ('center_mhz', args.center_mhz),
                           ('seed', args.seed)):
            if value is not None:        # what is not given is plan_dc's own default
                spec[key] = value
        runs = [(args.name, spec)]

    try:
        for name, spec in runs:
            write_capture(name, args.out, **spec)
    except ValueError as e:
        ap.error(str(e))


if __name__ == '__main__':
    main()
