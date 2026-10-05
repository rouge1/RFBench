#!/usr/bin/env python3
"""Hold the hopping grader to a recording whose truth is planted.

    python scripts/test_bt_ota_hop_check.py

``scripts/bt_ota_hop_check.py`` finds one hopping shot inside a long
recording and writes the truth of it. Nothing about a real recording is
known to the sample, so this builds one the way ``test_bt_ota_check.py``
places a burst at a fraction of a sample: ``bt_tx_hop.make_shot`` with no
radio, each burst re-modulated with ``delay`` equal to its own fraction,
a clock a chosen number of ppm off, silenced bursts, a gain that ramps
6 dB from channel 31 to channel 50, and noise at 25 dB in 1 MHz. ``plant``
can also add what a real room and a real recorder do: an access point that
is always on, Wi-Fi frames before the shot, a burst on the wrong carrier, a
recording centred off the sidecar's centre, a clock that changes rate, and
(by editing the samples afterwards) samples lost or gained and a recording
that starts or ends inside the shot.

It then checks what the grader says against what was planted:

* the clock and every start, which bursts were found, the channel, the bits
  and the clock count, the per-channel levels and counts, the link flatness,
  the SNR in 1 MHz, the mean error of the start (the detector's offset) and
  the timing jitter, and the same answer at every chunk size with a boundary
  through a burst; clocks of 60, 99 and 150 ppm, 12 and 15 dB, a map change,
  a DH1 train, and a centre of 2440 MHz;
* that the shot is found, and every burst placed right, beside an access
  point, a frame, a weak or silent burst 0 and a recording cut inside the
  shot; and a short noisy shot is not refused for being noisy;
* that samples lost or gained inside the shot, a change of rate, scatter, a
  recording off-centre, steps in time and a shot that was never sent whole
  each give a problem and no truth, while samples lost before or after the
  shot do not;
* what is refused: a transmitter sidecar from a dry run or never sent, and a
  recording that is too short, empty, noise, a single burst or two;
* the command line, with ``--record`` run through ``main`` on a fake
  ``record`` that returns the counters, and no truth written when it says
  samples were lost.
"""
import io
import json
import os
import sys
import tempfile
import types

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from scripts import bt_ota_hop_check as ota  # noqa: E402
from scripts import bt_synth  # noqa: E402
from scripts import bt_synth_hop as hop  # noqa: E402
from scripts import bt_tx_hop as tx  # noqa: E402

FS = tx.FS
failures = []


def check(ok, what):
    print('  %s %s' % ('ok  ' if ok else 'FAIL', what))
    if not ok:
        failures.append(what)


def gain_db(channel):
    """6 dB from channel 31 to channel 50, linear in dB across the map."""
    return 6.0 * (int(channel) - 31) / 19.0


def true_pos(origin, ppm, start_sample):
    return origin + (1.0 + ppm * 1e-6) * start_sample


def side_of(ptype='DH5', bursts=40, **kw):
    """The transmitter's sidecar. The samples are not kept."""
    iq, side = tx.make_shot(ptype=ptype, bursts=bursts, **kw)
    del iq
    # bt_tx_hop.main marks a shot sent once send_waveform has returned.
    side['tx'].update(sent=True, state='sent')
    return side


def recording_len(side, ppm, origin, pad_after):
    """Samples that hold every burst, plus ``pad_after`` seconds of noise."""
    last = side['bursts'][-1]
    sps = int(round(side['sample_rate'] / side['modulation']['symbol_rate']))
    # The raised-cosine tail is a few dozen samples past the last bit.
    end = true_pos(origin, ppm, last['start_sample']) + len(last['air_bits']) * sps + 400
    return int(np.ceil(end + pad_after * FS))


def floor_1mhz(snr_db):
    """The noise in 1 MHz, for a burst of unit amplitude at ``snr_db``."""
    return 1.0 / 10 ** (snr_db / 10.0)


def shaped_noise(rng, n, lo_mhz, hi_mhz, power):
    """``n`` samples of noise confined to ``lo_mhz``..``hi_mhz`` from the
    centre, of total ``power``: an access point, or a Wi-Fi frame.

    Blocks of 65536 samples are shaped in the frequency domain and joined by
    overlapping sine windows, so the power is constant and nothing clicks
    where two blocks meet.
    """
    size = 65536
    freqs = np.fft.fftfreq(size, 1.0 / FS)
    mask = (freqs >= lo_mhz * 1e6) & (freqs <= hi_mhz * 1e6)
    scale = np.sqrt(power / (mask.sum() / size))
    win = np.sin(np.pi * (np.arange(size) + 0.5) / size)
    out = np.zeros(n + 2 * size, dtype=np.complex64)
    for a in range(0, n + size, size // 2):
        w = (rng.normal(size=size) + 1j * rng.normal(size=size)) / np.sqrt(2.0)
        out[a:a + size] += (np.fft.ifft(np.fft.fft(w) * mask) * scale * win).astype(np.complex64)
    return out[size:size + n]


def plant(side, ppm, origin, total, silent=(), snr_db=25.0, seed=7, ramp=True, path=None,
          gains=None, wrong=None, cfo_hz=0.0, interferer=None, frames=(), clock=None, corrupt=None):
    """Noise, then each burst at its own fractional sample.

    ``path`` writes a cf32 file and returns only the true starts. Otherwise
    returns ``(samples, truth)``. The noise is added a million samples at a
    time, so the recording is not built twice in float64.

    What can go wrong with a recording, each optional: ``gains`` is a dB per
    burst instead of the ramp; ``wrong`` moves bursts' carriers, ``{burst:
    MHz}``, with the right bits; ``cfo_hz`` moves every carrier, a recording
    centred off the sidecar's centre; ``interferer`` is ``(lo, hi, dB)``, noise
    from ``lo`` to ``hi`` MHz from the centre, ``dB`` over the floor, on for
    the whole recording; ``frames`` is ``[(start, length, lo, hi, dB)]``, the
    same for a stretch; ``clock`` is a function from a burst's start in the
    shot to where it is in the recording, for a clock that is not one line;
    ``corrupt`` is ``{burst: bit positions}`` to flip, a burst whose access
    code is right and whose payload is not.
    """
    rng = np.random.default_rng(seed)
    # 25 dB in 1 MHz, referred to unit amplitude. The ramp then moves each
    # channel's own SNR by its gain.
    n1 = floor_1mhz(snr_db)
    sigma = np.sqrt(n1 * FS / 1e6 / 2.0)
    if path:
        iq = np.memmap(path, dtype='<c8', mode='w+', shape=int(total))
    else:
        iq = np.zeros(int(total), dtype=np.complex64)
    for pos in range(0, int(total), 1_000_000):
        m = min(1_000_000, int(total) - pos)
        noise = rng.normal(0.0, sigma, m) + 1j * rng.normal(0.0, sigma, m)
        iq[pos:pos + m] = noise.astype(np.complex64)
    if interferer:
        lo, hi, over = interferer
        iq += shaped_noise(rng, int(total), lo, hi, n1 * 10 ** (over / 10.0) * (hi - lo))
    for start, length, lo, hi, over in frames:
        start, length = int(start), int(length)
        iq[start:start + length] += shaped_noise(rng, length, lo, hi, n1 * 10 ** (over / 10.0) * (hi - lo))
    truth = {}
    centre = side['center_mhz']
    silent = set(silent)
    wrong = wrong or {}
    for k, entry in enumerate(side['bursts']):
        if clock is not None:
            pos = clock(entry['start_sample'])
        else:
            pos = true_pos(origin, ppm, entry['start_sample'])
        truth[k] = float(pos)
        if k in silent:
            continue
        bits = [int(c) for c in entry['air_bits']]
        for i in (corrupt or {}).get(k, ()):
            bits[i] ^= 1
        whole = int(np.floor(pos))
        burst, lead = br.gfsk(bits, FS, delay=pos - whole)
        lo = whole - lead
        hi = lo + len(burst)
        if lo < 0 or hi > total:
            raise RuntimeError('burst %d at %.3f does not fit in %d samples' % (k, pos, total))
        if gains is not None:
            amp = 10 ** (float(gains[k]) / 20.0)
        else:
            amp = 10 ** ((gain_db(entry['channel']) if ramp else 0.0) / 20.0)
        phase = np.exp(1j * rng.uniform(0.0, 2.0 * np.pi))
        n = np.arange(lo, hi)
        offset_hz = (entry['channel_mhz'] + wrong.get(k, 0.0) - centre) * 1e6 + cfo_hz
        cycles = offset_hz / FS * n
        wave = burst * (amp * phase) * np.exp(2j * np.pi * (cycles - np.floor(cycles)))
        iq[lo:hi] = iq[lo:hi] + wave.astype(np.complex64)
    if path:
        iq.flush()
        del iq
        return truth
    return iq, truth


def lose(iq, truth, at, n, mode='delete'):
    """Samples lost (``delete``) or gained, as zeros (``insert``), at sample
    ``at``: the new recording and where every burst is in it."""
    at = int(at)
    if mode == 'delete':
        out = np.concatenate([iq[:at], iq[at + n:]])
        return out, {k: (v - n if v >= at + n else v) for k, v in truth.items()}
    out = np.concatenate([iq[:at], np.zeros(n, dtype=np.complex64), iq[at:]])
    return out, {k: (v + n if v >= at else v) for k, v in truth.items()}


def cut(iq, truth, a, b):
    """The recording ``iq[a:b]`` and where every burst is in it."""
    return iq[int(a):int(b)], {k: v - int(a) for k, v in truth.items()}


def graded(ptype, bursts, ppm, pad_before, pad_after, silent=(), ramp=True, seed=1, path=None, **kw):
    """Plant a shot and grade it. Returns the grader's triple plus the plant."""
    side = side_of(ptype, bursts, seed=seed, **kw)
    origin = pad_before * FS + 0.4
    total = recording_len(side, ppm, origin, pad_after)
    if path:
        truth = plant(side, ppm, origin, total, silent=silent, ramp=ramp, seed=seed, path=path)
        result, rows, fit = ota.grade(path, side)
    else:
        iq, truth = plant(side, ppm, origin, total, silent=silent, ramp=ramp, seed=seed)
        result, rows, fit = ota.grade(iq, side)
        del iq
    return result, rows, fit, side, truth, origin, total


def check_against(result, rows, fit, side, truth, total, silent, ppm, flat=False):
    """The clock, the starts, the bits and the channels of one plant."""
    silent = set(silent)
    sps = int(round(side['sample_rate'] / side['modulation']['symbol_rate']))
    check(fit is not None and abs(result.get('clock_ppm', 1e9) - ppm) < 0.05,
          'clock %.4f ppm, planted %.1f' % (result.get('clock_ppm', float('nan')), ppm))
    cap = ota.capture_sidecar(side, total, rows, fit, result, {}, 'planted') if fit else None
    check(cap is not None and len(cap['bursts']) == len(side['bursts']),
          'every burst is placed (%s)' % (None if cap is None else len(cap['bursts'])))
    if cap is None:
        return None
    found_idx = {r['index'] for r in rows if r['found']}
    err = []
    bad_found, bad_id, bad_phase, bad_nearest, bad_bits = [], [], [], [], []
    for k, b in enumerate(cap['bursts']):
        src = side['bursts'][k]
        pos = truth[k]
        err.append(b['start_exact'] - pos)
        if b['found'] is not (k not in silent):
            bad_found.append(k)
        if not (b['channel'] == src['channel'] and b['clk'] == src['clk']
                and b['air_bits'] == src['air_bits']):
            bad_id.append(k)
        if b['symbol_phase'] != b['start_sample'] % sps or b['timing_frac'] != 0.0:
            bad_phase.append(k)
        if abs(b['start_sample'] - pos) > 1.0:
            bad_nearest.append((k, b['start_sample'], pos))
        if k not in silent and (b.get('bit_errors') != 0 or b.get('libbtbb_exact') is not True):
            bad_bits.append((k, b.get('bit_errors'), b.get('libbtbb_exact')))
    err = np.array(err)
    check(not bad_found, 'found matches the plant, mismatches %s' % bad_found[:8])
    check(not bad_id, 'channel, clk and air bits match, mismatches %s' % bad_id[:8])
    check(not bad_phase, 'symbol_phase is start_sample %% %d, mismatches %s' % (sps, bad_phase[:8]))
    check(not bad_nearest, 'start_sample within one sample, worst %s' % (bad_nearest[:3],))
    check(not bad_bits, 'every found burst has no bit errors and libbtbb exact, worst %s'
          % (bad_bits[:3],))
    check(np.abs(err).max() < 0.3,
          'start_exact within %.3f sample (mean %+.4f)' % (np.abs(err).max(), err.mean()))
    # The detector's own offset, calibrated on the interpolated peak, leaves
    # no bias: the mean of the errors is the offset's error.
    check(abs(err.mean()) < 0.1, 'mean start_exact error %+.4f sample is within 0.1' % err.mean())
    per_found = {}
    for k, b in enumerate(cap['bursts']):
        if b['found']:
            per_found[b['channel']] = per_found.get(b['channel'], 0) + 1
    want = {}
    for k, b in enumerate(side['bursts']):
        if k not in silent:
            want[b['channel']] = want.get(b['channel'], 0) + 1
    check(per_found == want, 'per-channel found counts are the plant\'s, mismatches %s' % (
        {c: (per_found.get(c, 0), want.get(c, 0)) for c in set(per_found) | set(want)
         if per_found.get(c, 0) != want.get(c, 0)},))
    check(all(i['found'] == want.get(int(c), 0) for c, i in result['per_channel'].items()),
          'result[per_channel][c][found] is the plant\'s count on every channel')
    check(found_idx.isdisjoint(silent), 'no silenced burst was found')
    if flat:
        per = result['per_channel']
        residual = []
        for ch, info in per.items():
            if info['found'] >= 3 and info['power_dbfs'] is not None:
                residual.append(info['power_dbfs'] - gain_db(int(ch)))
        spread = max(residual) - min(residual) if residual else float('nan')
        check(len(residual) >= 2 and spread < 1.0,
              'per-channel power follows the 6 dB ramp, residual spread %.3f dB' % spread)
        check(abs(result['link_flatness_db'] - 6.0) < 1.0,
              'link flatness %.3f dB, planted 6' % result['link_flatness_db'])
        check(result['link_flatness_note'] == ota.LINK_NOTE
              and 'not the VSG60A' in result['link_flatness_note'],
              'flatness is labelled as the whole link')
    offs = [r['peak'] - truth[r['index']] for r in rows if r['found']]
    print('  detector offset mean %.3f samples (peak - true start), std %.3f, '
          'mean start error %+.4f' % (float(np.mean(offs)), float(np.std(offs)), float(err.mean())))
    return cap


def check_main(tmp):
    print('\n80 DH5 bursts, -3 ppm, 0.7 s in, a 6 dB ramp across 31-50, three silenced')
    # 0.7 s of noise before the shot and 1.1 s after it. Forty DH5 bursts on
    # 31-50 never put three bursts on both ends of the map, so the flatness
    # of channels with at least three found bursts would miss the ramp; eighty
    # reaches channel 31 and channel 49. The three silenced bursts are on
    # channel 47, which has several to spare, and burst 0 stays.
    path = os.path.join(tmp, 'main.cf32')
    silent = (1, 14, 18)
    result, rows, fit, side, truth, origin, total = graded(
        'DH5', 80, -3.0, 0.7, 1.1, silent=silent, seed=1, path=path)
    print('  recording %d samples (%.3f s), shot at %.3f s' % (total, total / FS, origin / FS))
    for line in ota.summary_lines(result):
        print('  | %s' % line)
    cap = check_against(result, rows, fit, side, truth, total, silent, -3.0, flat=True)
    if cap is None:
        return
    check(cap['hopping'] is True and cap['afh_map'] == list(range(31, 51))
          and cap['center_mhz'] == 2441.0 and cap['afh_maps'] == side['afh_maps']
          and cap['tx']['mode'] == 'send_waveform: one shot'
          and cap['symbol_phase'] is None and 'capture' in cap['start_sample_meaning']
          and 'fitted start' in cap['start_sample_meaning'],
          'sidecar: hopping, map 31-50, centre 2441.0, starts counted from the capture')
    check(all(not info.get('outside_bb60d_band') for info in result['per_channel'].values()),
          'every channel of 31-50 is inside the BB60D band')

    print('\nThe same answer at every chunk size, with a boundary on a burst')
    def key(res, got):
        return (round(res['clock_ppm'], 5), round(res['link_flatness_db'], 3), res['found'],
                tuple((r['index'], r['bit_errors'], r['peak']) for r in got))

    base = key(result, rows)
    # A boundary inside the first burst, a chunk that is not a round number
    # of blocks, and two locate-block sizes.
    boundary = int(truth[0]) + 1000
    saved_chunk, saved_block = ota.CHUNK, ota.LOCATE_BLOCK
    try:
        for label, chunk, block in (
                ('CHUNK %d (through burst 0)' % boundary, boundary, saved_block),
                ('CHUNK 1000003', 1_000_003, saved_block),
                ('LOCATE_BLOCK 500', saved_chunk, 500),
                ('LOCATE_BLOCK 2000', saved_chunk, 2000)):
            ota.CHUNK = chunk
            ota.LOCATE_BLOCK = block
            res, got, fit2 = ota.grade(path, side)
            check(fit2 is not None and key(res, got) == base, '%s matches' % label)
    finally:
        ota.CHUNK = saved_chunk
        ota.LOCATE_BLOCK = saved_block


def check_clocks():
    print('\n+60 ppm tracks; +150 ppm is found and refused')
    result, rows, fit, side, truth, origin, total = graded(
        'DH5', 30, 60.0, 0.15, 0.15, silent=(), seed=2)
    check_against(result, rows, fit, side, truth, total, (), 60.0, flat=False)

    result, rows, fit, side, _, _, _ = graded('DH5', 20, 150.0, 0.15, 0.15, silent=(), seed=3)
    rejected = result.get('fit_rejected_ppm', float('nan'))
    check(fit is None and abs(rejected - 150.0) < 1.0 and result['found'] >= len(side['bursts']) - 2,
          '150 ppm refused at %.2f ppm, found %d of %d (%s)' % (
              rejected, result['found'], len(side['bursts']), result.get('problem')))


def check_map_and_dh1():
    print('\nA map change, and a longer DH1 train')
    # 33-52 does not fit at 2441.0: channel 52's band is 2453-2455 MHz.
    # 32-51 is the nearest 20-channel map that stays inside ±13.5 MHz.
    map_b = list(range(32, 52))
    result, rows, fit, side, truth, origin, total = graded(
        'DH5', 24, -3.0, 0.15, 0.15, silent=(), seed=9, map_b=map_b, change_at_burst=12)
    check_against(result, rows, fit, side, truth, total, (), -3.0, flat=False)
    only_a = set(range(31, 51)) - set(map_b)
    only_b = set(map_b) - set(range(31, 51))
    before = {b['channel'] for b in side['bursts'][:12]}
    after = {b['channel'] for b in side['bursts'][12:]}
    check(before & only_a and after & only_b and not (before & only_b) and not (after & only_a),
          'the plant really does use both maps')
    found = {r['index'] for r in rows}
    on_b = [k for k, b in enumerate(side['bursts']) if b['channel'] in only_b]
    check(on_b and all(k in found and rows[found_row(rows, k)]['bit_errors'] == 0 for k in on_b),
          'bursts on map B\'s own channels are found with no bit errors')

    result, rows, fit, side, truth, origin, total = graded(
        'DH1', 80, -3.0, 0.08, 0.08, silent=(), seed=1)
    check_against(result, rows, fit, side, truth, total, (), -3.0, flat=True)


def found_row(rows, index):
    for i, r in enumerate(rows):
        if r['index'] == index:
            return i
    raise KeyError(index)


def check_outside_band():
    print('\nA channel outside ±13.5 MHz is left out of the link flatness')
    bursts, rows = [], []
    # 31 at 0 dB and 50 at 6 dB are the ends of the ramp. Channel 52 at
    # 40 dB would own the flatness if the grader counted it.
    power = {31: 0.0, 40: 3.0, 50: 6.0, 52: 40.0}
    for ch in (31, 40, 50, 52):
        for _ in range(3):
            bursts.append({'channel': ch, 'channel_mhz': 2402.0 + ch})
            rows.append({'index': len(bursts) - 1, 'snr_db': 25.0,
                         'power_dbfs': power[ch], 'bit_errors': 0})
    per, flat = ota._per_channel(bursts, rows, 2441.0)
    check(per['52'].get('outside_bb60d_band') is True
          and not per['31'].get('outside_bb60d_band')
          and not per['50'].get('outside_bb60d_band'),
          'channel 52 is outside the BB60D band, 31 and 50 are not')
    check(abs(flat - 6.0) < 1e-9, 'flatness %.3f dB ignores the channel the BB60D cannot see' % flat)
    text = '\n'.join(ota.summary_lines({
        'sent': len(bursts), 'found': len(rows), 'graded': len(rows),
        'per_channel': per, 'link_flatness_db': flat, 'link_flatness_note': ota.LINK_NOTE}))
    check('channel 52' in text and 'outside the BB60D band' in text,
          'the summary says channel 52 is outside the BB60D band')


def check_edges():
    print('\nToo short, noise only, and a single burst')
    result, _, fit, _, _, _, _ = graded('DH5', 2, 0.0, 0.05, 0.05, silent=(), seed=1, ramp=False)
    check(fit is None and result.get('problem') and 'too short' in result['problem'],
          'two bursts: %s' % result.get('problem'))

    side = side_of('DH5', 8, seed=1)
    rng = np.random.default_rng(1)
    sigma = np.sqrt(1.0 / 10 ** (25.0 / 10.0) * FS / 1e6 / 2.0)
    noise = (rng.normal(0.0, sigma, 200_000) + 1j * rng.normal(0.0, sigma, 200_000)).astype(np.complex64)
    result, _, fit = ota.grade(noise, side)
    check(fit is None and result.get('problem') and result['problem'].startswith('no shot'),
          'noise only: %s' % result.get('problem'))

    result, _, fit, _, _, _, _ = graded('DH5', 1, 0.0, 0.05, 0.05, silent=(), seed=1, ramp=False)
    check(fit is None and result.get('problem') and 'no train' in result['problem'],
          'one burst: %s' % result.get('problem'))

    try:
        result, _, fit = ota.grade(np.zeros(100, dtype=np.complex64), side)
        check(fit is None and result.get('problem'), '100 samples: %s' % result.get('problem'))
    except Exception as e:           # noqa: BLE001 - a crash is the failure
        check(False, '100 samples raised %r' % (e,))


def run_plant(ptype='DH5', bursts=60, ppm=-3.0, pad=0.05, after=0.05, seed=1, snr=25.0,
              side_kw=None, **kw):
    """A shot planted in noise: ``(samples, truth, side)``."""
    side = side_of(ptype, bursts, seed=seed, **(side_kw or {}))
    origin = pad * FS + 0.4
    total = recording_len(side, ppm, origin, after)
    iq, truth = plant(side, ppm, origin, total, snr_db=snr, seed=seed, **kw)
    return iq, truth, side


def grade_truth(iq, side, name='planted'):
    """The grader's answer and the truth it would write: ``(result, rows, fit, cap)``."""
    result, rows, fit = ota.grade(iq, side)
    cap = ota.capture_sidecar(side, len(iq), rows, fit, result, {}, name) if fit else None
    return result, rows, fit, cap


def start_errors(cap, truth):
    """Fitted start minus the true start, for every burst the truth places."""
    return np.array([b['start_exact'] - truth[b['shot_index']] for b in cap['bursts']])


def graded_right(name, iq, side, truth, silent=(), min_found=1.0, max_err=0.5):
    """A recording the grader must get right.

    Some truth is written; every burst it places is within ``max_err``
    samples of where it really is, found or not; no burst that was never
    sent is found; and at least ``min_found`` of the bursts that were sent and
    are whole in the recording are found.
    """
    result, rows, fit, cap = grade_truth(iq, side, name)
    check(fit is not None and cap is not None,
          '%s: a truth is written (%s)' % (name, result.get('problem')))
    if cap is None:
        return None
    err = start_errors(cap, truth)
    found = np.array([b['found'] for b in cap['bursts']])
    sent = np.array([b['shot_index'] not in set(silent) for b in cap['bursts']])
    check(np.abs(err).max() < max_err,
          '%s: every placed start is within %.1f sample, worst %.3f (found worst %.3f)' % (
              name, max_err, np.abs(err).max(), np.abs(err[found]).max() if found.any() else float('nan')))
    check(not np.any(found & ~sent), '%s: no burst that was never sent is found' % name)
    share = float(found[sent].mean()) if sent.any() else 0.0
    check(share >= min_found, '%s: %d of %d bursts that were sent are found (%.0f %%)' % (
        name, int(found[sent].sum()), int(sent.sum()), 100 * share))
    return result, cap, err, found


def check_numbers():
    print('\nThe numbers: SNR, the detector offset, jitter, per-channel counts')
    # Clean, no ramp: the SNR the planter says is the SNR in 1 MHz the
    # grader should read, whatever the burst count, and every burst is found
    # and counted on its own channel.
    for ptype, bursts, snr, seed in (('DH5', 40, 20.0, 5), ('DH5', 40, 25.0, 6), ('DH1', 100, 20.0, 7)):
        iq, truth, side = run_plant(ptype, bursts, -3.0, seed=seed, snr=snr, ramp=False)
        result, rows, fit, cap = grade_truth(iq, side)
        name = '%s at %g dB' % (ptype, snr)
        check(fit is not None and result['found'] == bursts and abs(result['clock_ppm'] + 3.0) < 0.1,
              '%s: all %d found, clock %+.3f ppm' % (name, bursts, result.get('clock_ppm', float('nan'))))
        if fit is None:
            continue
        got = result['snr_db_median']
        check(abs(got - snr) < 0.3, '%s: median snr_db %.2f, planted %.1f (within 0.3 dB)' % (name, got, snr))
        check(cap['snr_db'] == round(got, 2) and cap['snr_bw_hz'] == 1e6 and 'SINR' in cap['snr_db_meaning'],
              '%s: the truth has top-level snr_db %s, snr_bw_hz %s, and says it is an SINR where not clean' % (
                  name, cap['snr_db'], cap['snr_bw_hz']))
        err = start_errors(cap, truth)
        # One burst's place scatters 0.65 sample at 20 dB and 0.36 at 25, so
        # the mean of 40 bursts is good to 0.1 at 20 dB and 0.06 at 25: the
        # bound is two of those, and the offset's own error is 0.01.
        bound = 0.2 if snr < 25.0 else 0.1
        check(abs(err.mean()) < bound, '%s: mean start error %+.3f sample (within %.1f)' % (
            name, err.mean(), bound))
        check(result['timing_jitter_samples'] < 1.0,
              '%s: timing jitter %.3f sample (under 1.0)' % (name, result['timing_jitter_samples']))
        bad = {c: (i['bursts'], i['found']) for c, i in result['per_channel'].items()
               if i['found'] != i['bursts']}
        check(not bad, '%s: every channel has found == bursts, mismatches %s' % (name, bad))
        check(result['snr_db_median'] == result['snr_db_median'] and
              all(abs(b['snr_db'] - snr) < 4 for b in cap['bursts'] if b['found']),
              '%s: every burst\'s snr_db is within 4 dB of the plant' % name)


def check_12db():
    print('\n12 and 15 dB in 1 MHz: the weak end of what the room may give')
    # At 12 dB the plain discriminator reads 3 % of a DH5's bits wrong, a
    # median of 90 of 2870, so under the 50-bit rule almost no burst is
    # found. They are all still placed, by the clock the weak bursts steer.
    iq, truth, side = run_plant('DH5', 60, -3.0, seed=11, snr=12.0, ramp=False, pad=0.05)
    out = graded_right('12 dB', iq, side, truth, min_found=0.0, max_err=1.0)
    if out:
        result, cap, err, found = out
        check(abs(result['clock_ppm'] + 3.0) < 0.15,
              '12 dB: clock %+.3f ppm, planted -3.0 (within 0.15)' % result['clock_ppm'])
        check(result['read'] == 60 and result['on_line_bursts'] >= 57,
              '12 dB: %d of 60 bursts were read well enough to place the rest' % result['on_line_bursts'])
        check(all(b['bit_errors'] <= 50 for b in cap['bursts'] if b['found'])
              and all(b['reason'] in ('bit errors', 'off the line') for b in cap['bursts'] if not b['found']),
              '12 dB: found means at most 50 bit errors; the others say "bit errors" or "off the line"')
        check(all(i['found'] == 0 for i in result['per_channel'].values())
              and result['link_flatness_db'] is None and 'snr_db_median' not in result,
              '12 dB: nothing found, so no per-channel figure, no flatness and no snr (%s)'
              % sorted({i['found'] for i in result['per_channel'].values()}))
        errors = [b['bit_errors'] for b in cap['bursts'] if 'bit_errors' in b]
        print('  12 dB: %d of %d found under the %d-bit rule (median %d bit errors), placed to %.3f worst, '
              '%.3f rms samples' % (int(found.sum()), len(found), ota.FOUND_MAX_BIT_ERRORS,
                                    int(np.median(errors)), np.abs(err).max(),
                                    float(np.sqrt(np.mean(err ** 2)))))
    iq, truth, side = run_plant('DH5', 60, -3.0, seed=11, snr=15.0, ramp=False, pad=0.05)
    out = graded_right('15 dB', iq, side, truth, min_found=0.9, max_err=0.6)
    if out:
        check(abs(out[0]['clock_ppm'] + 3.0) < 0.1, '15 dB: clock %+.3f ppm' % out[0]['clock_ppm'])
        # Found is held to 3 samples of the line however noisy the link is,
        # though the outlier that is dropped from the fit is farther off.
        result, rows, fit = ota.grade(iq, side)
        starts = [side['bursts'][r['index']]['start_sample'] for r in rows if r['found']]
        off = [abs(r['preamble'] - (fit[0] * s_ + fit[1])) for r, s_ in
               zip([r for r in rows if r['found']], starts)]
        check(off and max(off) <= ota.OFF_LINE_SAMPLES and all(
              r['bit_errors'] <= ota.FOUND_MAX_BIT_ERRORS for r in rows if r['found']),
              '15 dB: every found burst is within %g samples of the line (worst %.2f) and has at most '
              '%d bit errors' % (ota.OFF_LINE_SAMPLES, max(off) if off else -1, ota.FOUND_MAX_BIT_ERRORS))


def check_found_strictness():
    print('\nFound is at most 50 bit errors and within 3 samples of the line, whatever steers the clock')
    # At 14 dB scatter is 1.4 samples, so a burst with good bits is now and
    # then 3.3 samples off the line: read well enough to steer the clock and
    # not an outlier, and not found. This plant has two of them.
    iq, truth, side = run_plant('DH5', 60, -3.0, seed=17, snr=14.0, ramp=False, pad=0.05)
    result, rows, fit, cap = grade_truth(iq, side)
    check(fit is not None, '14 dB: a truth is written (%s)' % result.get('problem'))
    if cap is None:
        return
    odd = [b for b in cap['bursts'] if not b['found']]
    check(len(odd) == 2 and all(b['reason'] == 'off the line' and b['bit_errors'] <= 50 for b in odd)
          and result['found'] == 58 and result['on_line_bursts'] == 60,
          '14 dB: 58 found; the other 2 have good bits, are 3 samples off the line (%s) and are '
          'still steering the clock' % [(b['shot_index'], b['bit_errors']) for b in odd])
    err = start_errors(cap, truth)
    check(np.abs(err).max() < 0.7, '14 dB: all 60 are placed, worst %.3f sample' % np.abs(err).max())
    check(sum(i['found'] for i in result['per_channel'].values()) == 58,
          '14 dB: per-channel found counts add up to the 58 found, not the 60 read')
    slope, intercept = fit
    check(all(abs(r['preamble'] - (slope * side['bursts'][r['index']]['start_sample'] + intercept)) <= 3.0
              for r in rows if r['found']),
          '14 dB: every found burst is within 3 samples of the line')


def check_noisy_short():
    print('\nA short shot at 12 dB is graded, not refused for being noisy')
    # Twenty bursts at 12 dB scatter 1.4 samples about the line, and the two
    # halves of ten bursts then disagree at the join by a sample or more, and
    # in slope by a ppm or more, with nothing wrong. Both seeds are over the
    # floor of the gate they are for; the limit has to follow the noise.
    for seed, floor_key, floor in ((40, 'step_samples', ota.MAX_STEP_SAMPLES),
                                   (38, 'slope_step_ppm', ota.MAX_SLOPE_STEP_PPM)):
        iq, truth, side = run_plant('DH5', 20, -3.0, seed=seed, snr=12.0, ramp=False)
        out = graded_right('20 bursts at 12 dB, seed %d' % seed, iq, side, truth, min_found=0.0, max_err=2.0)
        if out:
            result = out[0]
            check(result[floor_key] > floor and result[floor_key] < result[
                {'step_samples': 'step_limit_samples', 'slope_step_ppm': 'slope_step_limit_ppm'}[floor_key]],
                  '%s %.2f is over the floor %g and under the limit %.2f that the noise sets' % (
                      floor_key, result[floor_key], floor, result[
                          {'step_samples': 'step_limit_samples',
                           'slope_step_ppm': 'slope_step_limit_ppm'}[floor_key]]))


def check_interferer():
    print('\nAn access point that is always on, on the top of the band')
    # +11..+20 MHz from 2441 is 2452-2461 MHz: channel 50 sits on its edge.
    # 28 dB over the floor is stronger than the shot on that channel.
    for label, band, over in (('9 MHz, +28 dB', (11.0, 20.0), 28.0), ('3 MHz, +20 dB', (11.0, 14.0), 20.0)):
        iq, truth, side = run_plant('DH5', 40, -3.0, seed=12, snr=25.0, ramp=False,
                                    interferer=(band[0], band[1], over))
        result, rows, fit, cap = grade_truth(iq, side, label)
        check(fit is not None, '%s: the shot is found (%s)' % (label, result.get('problem')))
        if cap is None:
            continue
        err = start_errors(cap, truth)
        touched = [b['channel_mhz'] - 2441.0 >= band[0] - 1.0 for b in cap['bursts']]
        found = np.array([b['found'] for b in cap['bursts']])
        t = np.array(touched)
        check(found[~t].all(), '%s: all %d bursts on the channels it does not touch are found' % (
            label, int((~t).sum())))
        check(np.abs(err[found]).max() < 0.5 and np.abs(err).max() < 0.5,
              '%s: %d of %d on the touched channels are found, none wrong (worst %.3f)' % (
                  label, int(found[t].sum()), int(t.sum()), np.abs(err).max()))
        left = [c for c, i in result['per_channel'].items() if 'excluded' in i
                and 'fewer' not in i['excluded']]
        print('  %s: left out of the flatness for SNR: %s' % (label, left))


def check_frames():
    print('\nWi-Fi frames before the shot, and a burst weaker than the others at the start')
    probe = side_of('DH5', 40, seed=13)
    first = true_pos(0.12 * FS + 0.4, -3.0, probe['bursts'][0]['start_sample'])
    last_end = (true_pos(0.12 * FS + 0.4, -3.0, probe['bursts'][-1]['start_sample'])
                + len(probe['bursts'][-1]['air_bits']) * 40)
    ms = int(FS * 1e-3)
    for label, frames in (
            ('5 ms frame 50 ms before', [(first - 50 * ms, 5 * ms, -9.0, 9.0, 20.0)]),
            # Right against the shot, where the energy of a frame is twenty
            # separate runs, one on each channel, that no burst accounts for.
            ('1.5 ms frame 2 ms before burst 0', [(first - int(3.5 * ms), int(1.5 * ms), -9.0, 9.0, 20.0)]),
            ('1.5 ms frame 2 ms after the last burst', [(last_end + 2 * ms, int(1.5 * ms), -9.0, 9.0, 20.0)]),
            ('0.6 ms frame 20 ms before', [(first - 20 * ms, int(0.6 * ms), -9.0, 9.0, 20.0)]),
            ('a train of 0.3 ms frames, 20 ms before',
             [(first - 60 * ms + i * int(0.5 * ms), int(0.3 * ms), -9.0, 9.0, 20.0) for i in range(80)])):
        iq, truth, side = run_plant('DH5', 40, -3.0, seed=13, snr=25.0, ramp=False, pad=0.12, frames=frames)
        graded_right(label, iq, side, truth)
    gains = np.zeros(40)
    gains[0] = -9.0                         # burst 0 weak: it used to be the anchor
    iq, truth, side = run_plant('DH5', 40, -3.0, seed=13, snr=25.0, ramp=False, gains=gains)
    graded_right('burst 0 9 dB weak', iq, side, truth)


def check_silent_and_edges():
    print('\nSilent bursts, and a recording that starts or ends inside the shot')
    iq, truth, side = run_plant('DH5', 40, -3.0, seed=14, snr=25.0, ramp=False,
                                silent=set(range(0, 5)))
    graded_right('bursts 0-4 silent', iq, side, truth, silent=set(range(5)))
    iq, truth, side = run_plant('DH5', 40, -3.0, seed=14, snr=25.0, ramp=False,
                                silent=set(range(0, 20)))
    graded_right('bursts 0-19 silent', iq, side, truth, silent=set(range(20)))
    iq, truth, side = run_plant('DH5', 40, -3.0, seed=14, snr=25.0, ramp=False)
    inside = lambda k: int(truth[k]) + 50000          # in the middle of burst k
    for label, a, e, first in (
            ('starts inside burst 0', inside(0), len(iq), 1),
            ('starts inside burst 15', inside(15), len(iq), 16),
            ('ends inside burst 25', 0, inside(25), 0),
            ('starts inside 8 and ends inside 30', inside(8), inside(30), 9)):
        cropped, moved = cut(iq, truth, a, e)
        out = graded_right(label, cropped, side, moved, min_found=0.99)
        if out:
            cap = out[1]
            check(cap['bursts'][0]['shot_index'] == first and cap['bursts'][0]['start_sample'] >= 0
                  and abs(cap['bursts'][0]['start_exact'] - moved[first]) < 0.5,
                  '%s: %d whole bursts, the first is shot burst %d, counted from the capture\'s first sample'
                  % (label, len(cap['bursts']), cap['bursts'][0]['shot_index']))


def check_losses():
    print('\nSamples lost or gained inside the shot: a problem, never a truth')
    iq, truth, side = run_plant('DH5', 40, -0.4, seed=15, snr=25.0, ramp=False)
    gap = int(truth[17]) + 120000          # between bursts 17 and 18
    for n, mode in ((5, 'delete'), (40, 'delete'), (100, 'delete'), (199, 'delete'),
                    (250, 'delete'), (2000, 'delete'), (50, 'insert'), (2000, 'insert')):
        bad, moved = lose(iq, truth, gap, n, mode)
        result, rows, fit, cap = grade_truth(bad, side)
        check(fit is None and cap is None and result.get('problem'),
              '%d samples %s between bursts 17 and 18: no truth, "%s"' % (
                  n, 'lost' if mode == 'delete' else 'gained', (result.get('problem') or '')[:70]))
    # Exactly where the two halves of the bursts meet (burst 19 and 20): the
    # slopes agree and only the join moves.
    join = int(truth[19]) + 120000
    for n, mode in ((5, 'delete'), (4, 'insert')):
        bad, moved = lose(iq, truth, join, n, mode)
        result, rows, fit, cap = grade_truth(bad, side)
        check(fit is None and cap is None and 'steps by' in (result.get('problem') or ''),
              '%d samples %s at the join of the halves: %s' % (
                  n, 'lost' if mode == 'delete' else 'gained', (result.get('problem') or 'TRUTH')[:70]))
    # Nearly at the ends, where the shorter side has only two or three bursts.
    # 250 samples after burst 37 leaves two bursts out of the narrow window:
    # only the second look finds them, off the line.
    for k, n in ((2, 40), (37, 40), (2, 5), (37, 5), (37, 250), (2, 250)):
        bad, moved = lose(iq, truth, int(truth[k]) + 120000, n, 'delete')
        result, rows, fit, cap = grade_truth(bad, side)
        check(fit is None and cap is None, '%d samples lost after burst %d: no truth (%s)' % (
            n, k, (result.get('problem') or 'TRUTH WRITTEN')[:60]))
    # The shot stops being whole: a gap of 25000 samples, and one of a whole
    # period. Every burst after it is out of the window, so only the energy
    # that is still there says the tracker lost them.
    for n in (25000, 150000):
        bad, moved = lose(iq, truth, gap, n, 'delete')
        result, rows, fit, cap = grade_truth(bad, side)
        check(fit is None and cap is None and 'tracking lost' in (result.get('problem') or ''),
              '%d samples lost between bursts 17 and 18: %s' % (n, (result.get('problem') or 'TRUTH')[:70]))
    # Before the shot and after it nothing moves, and the grade is clean.
    bad, moved = lose(iq, truth, 1000, 2000, 'delete')
    graded_right('2000 samples lost before the shot', bad, side, moved)
    bad, moved = lose(iq, truth, len(iq) - 500, 300, 'delete')
    graded_right('300 samples lost after the shot', bad, side, moved)
    bad, moved = lose(iq, truth, len(iq) - 1000, 2000, 'insert')
    graded_right('2000 samples gained after the shot', bad, side, moved)


def check_end_shifts_and_far_kinks():
    print('\nA loss next to the first or last burst, and a change of rate far from the middle')
    iq, truth, side = run_plant('DH5', 40, -0.4, seed=15, snr=25.0, ramp=False)
    for k, last in ((0, 0), (38, 39)):
        for n in (40, 2000):
            for mode in ('delete', 'insert'):
                bad, moved = lose(iq, truth, int(truth[k]) + 120000, n, mode)
                result, rows, fit, cap = grade_truth(bad, side)
                check(fit is None and cap is None and ('burst %d was read' % last) in (result.get('problem') or ''),
                      '%d samples %s after burst %d: no truth, %s' % (
                          n, 'lost' if mode == 'delete' else 'gained', k, (result.get('problem') or 'TRUTH')[:60]))
    # 200 bursts is 30 M samples: a kink of d ppm at fraction f is wrong by d x span x f(1-f)/2.
    probe = side_of('DH5', 200, seed=16)
    origin = 0.05 * FS + 0.4
    for kb, d in ((100, 0.5), (20, 1.0)):
        split = probe['bursts'][kb]['start_sample'] - 75000
        at = origin + (1 - 3e-6) * split

        def clock(s, split=split, at=at, d=d):
            return origin + (1 - 3e-6) * s if s <= split else at + (1 + (d - 3.0) * 1e-6) * (s - split)
        total = recording_len(probe, 0.0, origin, 0.05)
        iq, truth = plant(probe, -3.0, origin, total, snr_db=25.0, seed=16, ramp=False, clock=clock)
        result, rows, fit, cap = grade_truth(iq, probe)
        check(fit is None and cap is None and 'clock' in (result.get('problem') or ''),
              '%.1f ppm change of rate at burst %d of 200: no truth (%s)' % (d, kb, (result.get('problem') or 'TRUTH')[:60]))
    iq, truth = plant(probe, -3.0, origin, total, snr_db=25.0, seed=16, ramp=False)
    graded_right('200 bursts, no kink', iq, probe, truth)


def check_ambient_tail():
    print('\nA whole DH1 shot, then 0.4 s of single-channel ambient bursts: a truth, with or without the last burst')
    probe = side_of('DH1', 600, seed=24)
    origin = 0.05 * FS + 0.4
    last_end = true_pos(origin, -3.0, probe['bursts'][-1]['start_sample']) + len(probe['bursts'][-1]['air_bits']) * 40
    rng = np.random.default_rng(5)
    ms = int(FS * 1e-3)
    frames, t = [], last_end + ms
    while t < last_end + 0.4 * FS:
        off = (2402 + int(rng.choice([33, 38, 44, 47])) - 2441.0)
        length = int(rng.uniform(0.2, 0.6) * ms)
        frames.append((int(t), length, off - 0.4, off + 0.4, 20.0))
        t += length + int(rng.uniform(1, 3) * ms)
    for label, silent in (('last burst present', ()), ('last burst deleted', {599})):
        iq, truth, side = run_plant('DH1', 600, -3.0, seed=24, snr=25.0, ramp=False, after=0.45,
                                    frames=frames, silent=silent)
        graded_right('ambient tail, %s (%d runs)' % (label, len(frames)), iq, side, truth, silent=silent)


def check_clock_rate():
    print('\nThe clock changes rate with no sample lost, and clean clocks still pass')
    # +8 ppm, then -30: continuous in time, so the two halves meet at the
    # join and only their slopes differ.
    probe = side_of('DH5', 40, seed=16)
    split = probe['bursts'][20]['start_sample'] - 75000
    origin = 0.05 * FS + 0.4

    def kink(first, second):
        at_split = origin + (1 + first * 1e-6) * split

        def clock(s):
            if s <= split:
                return origin + (1 + first * 1e-6) * s
            return at_split + (1 + second * 1e-6) * (s - split)
        return clock

    total = recording_len(probe, -30.0, origin, 0.05)
    iq, truth = plant(probe, 8.0, origin, total, snr_db=25.0, seed=16, ramp=False, clock=kink(8.0, -30.0))
    result, rows, fit, cap = grade_truth(iq, probe)
    check(fit is None and cap is None and result.get('problem'),
          '+8 ppm then -30 ppm, nothing lost: no truth (%s)' % (result.get('problem') or '')[:80])
    check('step_samples' in result and result['step_samples'] < result['step_limit_samples']
          and result['slope_step_ppm'] > result['slope_step_limit_ppm'],
          'the join agrees (%.2f samples) and the slopes do not (%.1f ppm over %.1f)' % (
              result.get('step_samples', -1), result.get('slope_step_ppm', -1),
              result.get('slope_step_limit_ppm', -1)))
    # A small change of rate: the scatter about one line stays under its
    # backstop and the join agrees, so only the slopes can say it. 1.5 ppm
    # is over the limit; 0.3 ppm is not, and is placed within a sample.
    for second, bad in ((-1.5, True), (-3.3, False)):
        total = recording_len(probe, -3.0, origin, 0.05)
        iq, truth = plant(probe, -3.0, origin, total, snr_db=25.0, seed=16, ramp=False,
                          clock=kink(-3.0, second))
        result, rows, fit, cap = grade_truth(iq, probe)
        kinked = abs(second + 3.0)
        if bad:
            check(fit is None and cap is None and 'clock rate differs' in (result.get('problem') or '')
                  and result['timing_jitter_samples'] < ota.MAX_JITTER_SAMPLES,
                  'a %.1f ppm change of rate: refused by the slopes alone, jitter %.2f samples (%s)' % (
                      kinked, result.get('timing_jitter_samples', -1), (result.get('problem') or '')[:50]))
        else:
            graded_right('a %.1f ppm change of rate' % kinked, iq, side_of('DH5', 40, seed=16), truth, max_err=1.0)
    # Scatter alone: every burst a few samples off a good line, no steps, no
    # slope. Nothing else is wrong, so the backstop is what refuses it.
    rng = np.random.default_rng(3)
    shake = rng.uniform(-7.0, 7.0, len(probe['bursts']))
    starts = [b['start_sample'] for b in probe['bursts']]
    total = recording_len(probe, -3.0, origin, 0.05) + 100
    iq, truth = plant(probe, -3.0, origin, total, snr_db=25.0, seed=16, ramp=False,
                      clock=lambda s: true_pos(origin, -3.0, s) + shake[starts.index(s)])
    result, rows, fit, cap = grade_truth(iq, probe)
    check(fit is None and cap is None and 'scatter' in (result.get('problem') or ''),
          'every burst shaken by up to 7 samples: refused for the scatter (%.2f samples)'
          % result.get('timing_jitter_samples', -1))
    for ppm in (-3.0, 99.0):
        iq, truth, side = run_plant('DH5', 40, ppm, seed=17, snr=25.0, ramp=False)
        out = graded_right('clean %+.0f ppm' % ppm, iq, side, truth)
        if out:
            check(abs(out[0]['clock_ppm'] - ppm) < 0.1, 'clock %+.3f ppm, planted %+.1f' % (
                out[0]['clock_ppm'], ppm))
            check(out[0]['step_samples'] < out[0]['step_limit_samples']
                  and out[0]['slope_step_ppm'] < ota.MAX_SLOPE_STEP_PPM,
                  'its halves agree: %.2f samples, %.2f ppm' % (out[0]['step_samples'], out[0]['slope_step_ppm']))


def check_carrier():
    print('\nA recording centred off the sidecar, one burst off its carrier, one on the wrong channel')
    for off in (300e3, -500e3):
        iq, truth, side = run_plant('DH5', 40, -3.0, seed=18, snr=25.0, ramp=False, cfo_hz=off)
        result, rows, fit, cap = grade_truth(iq, side)
        check(fit is None and cap is None and 'centred' in (result.get('problem') or ''),
              'centred %+.0f kHz off: %s' % (off / 1e3, (result.get('problem') or 'TRUTH')[:78]))
    iq, truth, side = run_plant('DH5', 40, -3.0, seed=18, snr=25.0, ramp=False, cfo_hz=30e3)
    out = graded_right('30 kHz off, as a pair of crystals might be', iq, side, truth)
    iq, truth, side = run_plant('DH5', 40, -3.0, seed=18, snr=25.0, ramp=False,
                                wrong={10: 0.5, 20: 2.0, 30: -0.1})
    out = graded_right('bursts 10, 20 and 30 off their carriers', iq, side, truth, silent={10, 20, 30},
                       min_found=0.9)
    if out:
        by = {b['shot_index']: b for b in out[1]['bursts']}
        check(not by[10]['found'] and by[10]['reason'] == 'carrier offset' and abs(by[10]['cfo_hz']) > 4e5,
              'burst 10, 0.5 MHz off: found false, reason %r, cfo %s' % (by[10].get('reason'), by[10].get('cfo_hz')))
        check(not by[20]['found'] and by[20]['reason'] == 'not detected',
              'burst 20, 2 MHz off (the wrong channel): found false, reason %r' % by[20].get('reason'))
        check(not by[30]['found'] and by[30]['reason'] == 'carrier offset',
              'burst 30, 100 kHz off: found false, reason %r' % by[30].get('reason'))
        check(all('reason' in b for b in out[1]['bursts'] if not b['found']),
              'every burst that is not found has a reason')


def check_off_the_line():
    print('\nOne burst displaced is an outlier, two are a step')
    probe = side_of('DH5', 40, seed=19)
    origin = 0.05 * FS + 0.4

    def moved(shift):
        def clock(s):
            k = [b['start_sample'] for b in probe['bursts']].index(s)
            return true_pos(origin, -3.0, s) + shift.get(k, 0.0)
        return clock

    total = recording_len(probe, -3.0, origin, 0.05) + 100
    iq, truth = plant(probe, -3.0, origin, total, snr_db=25.0, seed=19, ramp=False, clock=moved({20: 8.0}))
    result, rows, fit, cap = grade_truth(iq, probe)
    check(fit is not None, 'one burst 8 samples late: the rest are graded (%s)' % result.get('problem'))
    if cap is not None:
        by = {b['shot_index']: b for b in cap['bursts']}
        check(not by[20]['found'] and by[20]['reason'] == 'off the line',
              'it is found false, reason %r' % by[20].get('reason'))
        err = start_errors(cap, {k: v for k, v in truth.items()})
        others = [e for b, e in zip(cap['bursts'], err) if b['shot_index'] != 20]
        check(max(abs(e) for e in others) < 0.5 and result['found'] == 39,
              'the other 39 are found and placed right (worst %.3f)' % max(abs(e) for e in others))
    iq, truth = plant(probe, -3.0, origin, total, snr_db=25.0, seed=19, ramp=False,
                      clock=moved({20: 8.0, 21: 8.0, 12: -9.0}))
    result, rows, fit, cap = grade_truth(iq, probe)
    check(fit is None and cap is None and 'steps' in (result.get('problem') or ''),
          'three bursts displaced: no truth (%s)' % (result.get('problem') or '')[:70])
    # Two bursts with the right access code and a ruined payload, 8 samples
    # off: a false lock is not a step. They are read and set aside for their
    # bit errors, and do not steer the clock, so the truth stands.
    ruined = {10: range(200, 2800, 3), 25: range(200, 2800, 3)}
    iq, truth = plant(probe, -3.0, origin, total, snr_db=25.0, seed=19, ramp=False,
                      clock=moved({10: 8.0, 25: -8.0}), corrupt=ruined)
    result, rows, fit, cap = grade_truth(iq, probe)
    check(fit is not None, 'two ruined bursts, displaced: the truth is written (%s)' % result.get('problem'))
    if cap is not None:
        by = {b['shot_index']: b for b in cap['bursts']}
        check(all(not by[k]['found'] and by[k]['reason'] == 'bit errors' and by[k]['bit_errors'] > 300
                  for k in (10, 25)),
              'both are found false, reason "bit errors" (%s, %s errors)' % (
                  by[10].get('reason'), by[10].get('bit_errors')))
        err = start_errors(cap, {k: v for k, v in truth.items()})
        others = [e for b, e in zip(cap['bursts'], err) if b['shot_index'] not in (10, 25)]
        check(result['found'] == 38 and max(abs(e) for e in others) < 0.5,
              'the other 38 are found and placed right (worst %.3f)' % max(abs(e) for e in others))


def check_no_shot():
    print('\nNoise, a single burst, half a shot, a shot that was never sent whole: never a wrong truth')
    iq, truth, side = run_plant('DH5', 30, -3.0, seed=20, snr=25.0, ramp=False, silent=set(range(30)),
                                interferer=(9.5, 11.5, 20.0))
    result, rows, fit, cap = grade_truth(iq, side)
    check(fit is None and cap is None and result['problem'].startswith('no shot'),
          'noise and an interferer: %s' % (result.get('problem') or '')[:60])
    for keep in ({15}, {15, 16}, {15, 16, 17}):
        iq, truth, side = run_plant('DH5', 30, -3.0, seed=20, snr=25.0, ramp=False,
                                    silent=set(range(30)) - keep)
        result, rows, fit, cap = grade_truth(iq, side)
        check(fit is None and cap is None and result.get('problem'),
              '%d burst(s) of 30: no truth (%s)' % (len(keep), (result.get('problem') or '')[:70]))
    # Two bursts out of a hundred, in a shot where a pair of runs from other
    # bursts happens to agree on an origin too: the next origin is tried, and
    # the answer is that two bursts are too few, not that nothing can be read.
    iq, truth, side = run_plant('DH5', 100, -3.0, seed=21, snr=25.0, ramp=False,
                                silent=set(range(100)) - {40, 41})
    result, rows, fit, cap = grade_truth(iq, side)
    check(fit is None and cap is None and 'too short' in (result.get('problem') or ''),
          '2 bursts of 100: %s' % (result.get('problem') or '')[:70])
    # Half the shot never sent: the other 15 are there and graded right, or
    # nothing is written. Either is fine; a truth that says 30 is not.
    iq, truth, side = run_plant('DH5', 30, -3.0, seed=20, snr=25.0, ramp=False, silent=set(range(15, 30)))
    graded_right('bursts 15-29 never sent', iq, side, truth, silent=set(range(15, 30)))
    iq, truth, side = run_plant('DH5', 30, -3.0, seed=20, snr=25.0, ramp=False, silent=set(range(15)))
    graded_right('bursts 0-14 never sent', iq, side, truth, silent=set(range(15)))


def check_centre_2440():
    print('\nA centre of 2440 MHz, with the same map')
    iq, truth, side = run_plant('DH5', 40, -3.0, seed=21, snr=25.0, ramp=False,
                                side_kw={'center_mhz': 2440.0})
    out = graded_right('2440 MHz', iq, side, truth)
    if out:
        check(out[1]['center_mhz'] == 2440.0 and side['center_mhz'] == 2440.0
              and out[1]['tx']['vsg_center_mhz'] == 2440.0
              and not any(i.get('outside_bb60d_band') for i in out[0]['per_channel'].values()),
              'the truth says 2440.0 MHz and every channel of 31-50 is inside the band')
        check(abs(out[0]['snr_db_median'] - 25.0) < 0.3, 'snr %.2f dB' % out[0]['snr_db_median'])


def check_sidecar_refusals(tmp):
    print('\nA transmitter sidecar that was never sent is refused')
    side = side_of('DH5', 6, seed=22)
    rec = np.zeros(200_000, dtype=np.complex64)
    for label, tweak, text in (
            ('a dry run', lambda tx_: tx_.update(dry_run=True, sent=False), 'dry run'),
            ('sent false', lambda tx_: tx_.update(sent=False, state='not started'), 'not say the shot was sent'),
            ('sent pending', lambda tx_: tx_.update(sent='pending', state='pending'), 'not say the shot was sent'),
            ('sent error', lambda tx_: tx_.update(sent='error'), 'not say the shot was sent'),
            ('no sent key', lambda tx_: tx_.pop('sent'), 'not say the shot was sent')):
        bad = json.loads(json.dumps(side))
        tweak(bad['tx'])
        try:
            ota.grade(rec, bad)
            check(False, '%s: grade refused it' % label)
        except ValueError as e:
            check(text in str(e), '%s: grade refuses it: %s' % (label, str(e)[:70]))
        path = os.path.join(tmp, 'unsent.json')
        with open(path, 'w') as f:
            json.dump(bad, f)
        file_ = os.path.join(tmp, 'unsent.cf32')
        rec.tofile(file_)
        code, _, err = run([path, '--iq', file_])
        check(code not in (0, None) and text in err, '%s: the command refuses it' % label)
    no_tx = json.loads(json.dumps(side))
    del no_tx['tx']
    try:
        ota.grade(rec, no_tx)
        check(False, 'no tx block: refused')
    except ValueError:
        check(True, 'no tx block: refused')


def check_flatness_exclusions():
    print('\nThe flatness leaves out an interfered channel and a thin one, and says why')
    bursts, rows = [], []
    levels = {35: (0.0, 25.0, 4), 36: (1.0, 25.0, 4), 37: (2.0, 24.0, 4), 38: (3.0, 26.0, 4),
              39: (-30.0, 9.0, 4),                 # DC: 16 dB under the others
              40: (5.0, 25.0, 2),                  # two found bursts: too few
              41: (6.0, 25.0, 4)}
    for ch, (power, snr, n) in levels.items():
        for _ in range(n):
            bursts.append({'channel': ch, 'channel_mhz': 2402.0 + ch})
            rows.append({'index': len(bursts) - 1, 'snr_db': snr, 'power_dbfs': power, 'bit_errors': 0})
    per, flat = ota._per_channel(bursts, rows, 2441.0)
    check('excluded' in per['39'] and 'more than 10 dB' in per['39']['excluded']
          and 'interferer' in per['39']['excluded'],
          'channel 39 (9 dB against 25) is left out: %r' % per['39'].get('excluded'))
    check('excluded' in per['40'] and 'fewer than 3' in per['40']['excluded'],
          'channel 40 (two found) is left out: %r' % per['40'].get('excluded'))
    check(all('excluded' not in per[c] for c in ('35', '36', '37', '38', '41')),
          'the others are not left out')
    check(abs(flat - 6.0) < 1e-9, 'flatness %.3f dB is the spread of the five others (planted 6)' % flat)
    text = '\n'.join(ota.summary_lines({'sent': 1, 'found': 1, 'graded': 1, 'per_channel': per,
                                        'link_flatness_db': flat, 'link_flatness_note': ota.LINK_NOTE}))
    check('left out of the flatness' in text, 'the summary says so')


def run(argv):
    """``main`` with ``argv``, returning ``(code, stdout, stderr)``."""
    out, err = io.StringIO(), io.StringIO()
    old, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    code = 0
    try:
        try:
            ota.main(argv)
        except SystemExit as e:
            code = e.code if e.code is not None else 0
    finally:
        sys.stdout, sys.stderr = old, old_err
    return code, out.getvalue(), err.getvalue()


def check_cli(tmp):
    print('\nThe command line, with the radio never opened')
    side = side_of('DH5', 8, seed=4)
    origin = 0.05 * FS + 0.4
    total = recording_len(side, -3.0, origin, 0.05)
    rec = os.path.join(tmp, 'cli.cf32')
    plant(side, -3.0, origin, total, path=rec, seed=4)
    side_path = os.path.join(tmp, 'synth_cli_tx.json')
    with open(side_path, 'w') as f:
        json.dump(side, f)

    bad = dict(side)
    bad['center_mhz'] = 2444.5
    bad_path = os.path.join(tmp, 'bad_centre.json')
    with open(bad_path, 'w') as f:
        json.dump(bad, f)
    code, _, err = run([bad_path, '--iq', rec])
    check(code not in (0, None) and 'whole' in err,
          'a centre of 2444.5 MHz is refused before the file is graded')

    code, _, err = run([side_path, '--capture', 'needs-a-file'])
    check(code not in (0, None) and 'capture' in err,
          '--capture without --iq or --record is refused')

    # BLUEY is outside this worktree. The test points it at the temp dir
    # before --capture can write, and nothing here passes --record.
    old = bt_synth.BLUEY
    bluey = os.path.join(tmp, 'bluey')
    bt_synth.BLUEY = bluey
    try:
        code, text, err = run([side_path, '--iq', rec, '--capture', 'planted', '--gain', '40'])
        out = os.path.join(bluey, 'data', 'sidecar', 'planted.json')
        check(code == 0 and os.path.isfile(out) and not err.strip(),
              'capture writes the sidecar (%s)' % (err.strip() or 'no error'))
        check(not os.path.exists(os.path.join(bluey, 'data', 'iq')),
              '--iq does not write a cf32')
        got = json.load(open(out))
        burst = next(b for b in got['bursts'] if b['found'])
        check(got['hopping'] is True and got['hop_channels'] == side['hop_channels']
              and got['afh_instant'] == side['afh_instant'] and got['per_channel']
              and got['link_flatness_note'] == ota.LINK_NOTE
              and got['receiver']['radio'] == 'BB60D' and got['receiver']['gain_percent'] == 40
              and got['tx']['vsg_center_mhz'] == tx.CENTER_MHZ
              and got['timing_frac'] == 0.0 and got['symbol_phase'] is None
              and 'capture' in got['start_sample_meaning']
              and burst['timing_frac'] == 0.0 and 'snr_db' in burst and 'power_dbfs' in burst
              and burst['symbol_phase'] == burst['start_sample'] % 40,
              'the stage 3 fields and the over-the-air additions')
        check('link flatness' in text and 'not the VSG60A' in text,
              'the summary names the link flatness')
        check(got.get('snr_bw_hz') == 1e6 and isinstance(got.get('snr_db'), float)
              and abs(got['snr_db'] - 25.0) < 4.0 + abs(gain_db(35)),
              'top level: snr_db %s in snr_bw_hz %s' % (got.get('snr_db'), got.get('snr_bw_hz')))
        check(got.get('start_offset') == side['start_offset'] == 0,
              'top level: start_offset %s is the transmitter\'s' % got.get('start_offset'))
        check(isinstance(got.get('cfo_hz'), float) and 'MEASURED' in got.get('cfo_hz_meaning', '')
              and 'median' in got.get('cfo_hz_meaning', ''),
              'cfo_hz %s is measured, and cfo_hz_meaning says so' % got.get('cfo_hz'))
        conv = got['clk_convention']
        check('preamble' in conv and 'ramp' in conv and 'start_sample' in conv
              and 'first sample of every burst' not in conv,
              'clk_convention: the clock belongs to the preamble\'s first sample')
        check("first sample of the capture's cf32" in got['start_sample_meaning'],
              'start_sample_meaning counts from the first sample of the capture\'s cf32')
        check([b.get('shot_index') for b in got['bursts']] == list(range(len(got['bursts'])))
              and 'shot_index' in got.get('afh_maps_first_burst_meaning', ''),
              'every burst has its shot_index, which is what afh_maps[].first_burst counts')
        check('lost_sample_overflows' in got['receiver'] and got['receiver']['lost_sample_overflows'] is None
              and got['receiver']['adc_overflows'] is None,
              'with --iq the receiver block has no overflow counts to report')
        check(all(b.get('reason') for b in got['bursts'] if not b['found'])
              and all('reason' not in b for b in got['bursts'] if b['found']),
              'reason only on bursts that are not found')
    finally:
        bt_synth.BLUEY = old


def check_record_function():
    print('\nrecord() itself, on fakes of gnuradio and bb60_source: it keeps the source block')
    calls = {}

    class Source:
        overflows = 0                    # bb60_source counts a lost-sample overflow here

    source = Source()

    class TopBlock:
        def connect(self, *blocks):
            calls['connect'] = blocks

        def run(self):
            source.overflows = 4         # the loss happens while the flowgraph runs
            calls['run'] = True

    fake_gnuradio = types.ModuleType('gnuradio')
    fake_gnuradio.gr = types.SimpleNamespace(sizeof_gr_complex=8, top_block=TopBlock)
    fake_gnuradio.blocks = types.SimpleNamespace(
        head=lambda size, n: ('head', size, n), file_sink=lambda size, path: ('file_sink', size, path))
    fake_bb = types.ModuleType('apps.bb60_source')
    fake_bb.ensure_plugin_path = lambda: calls.setdefault('plugin', True)
    fake_bb.reset_overflows = lambda: calls.setdefault('reset', True)
    fake_bb.bb60_source = lambda centre, fs, gain_percent=None: (
        calls.update(source=(centre, fs, gain_percent)) or source)
    fake_bb.overflow_count = lambda: 7  # the ADC counter, a different thing
    saved = {name: sys.modules.get(name) for name in ('gnuradio', 'apps.bb60_source')}
    sys.modules['gnuradio'] = fake_gnuradio
    sys.modules['apps.bb60_source'] = fake_bb
    try:
        info = ota.record('/tmp/never_written.cf32', 2.0, 2441e6, 40e6, 55.0)
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
    check(info['lost_sample_overflows'] == 4 and info['adc_overflows'] == 7,
          'lost_sample_overflows is the source block\'s count after the run (%s), adc_overflows the ADC\'s (%s)'
          % (info.get('lost_sample_overflows'), info.get('adc_overflows')))
    check(calls.get('source') == (2441e6, 40e6, 55.0) and calls.get('run') and calls.get('reset')
          and calls['connect'][1] == ('head', 8, 80_000_000)
          and calls['connect'][2] == ('file_sink', 8, '/tmp/never_written.cf32')
          and info['gain_percent'] == 55.0 and info['recorded_s'] == 2.0,
          'it opens the source at the centre, rate and gain it was given and records 2 s to the path')


def check_record(tmp):
    print('\n--record through main with a fake record(): what the BB60D is told, what the truth keeps')
    side = side_of('DH5', 12, seed=23)
    origin = 0.05 * FS + 0.4
    total = recording_len(side, -3.0, origin, 0.05)
    side_path = os.path.join(tmp, 'synth_rec_tx.json')
    with open(side_path, 'w') as f:
        json.dump(side, f)
    calls = []

    def fake(adc, lost):
        def record(path, seconds, center_hz, fs, gain_percent):
            calls.append((path, seconds, center_hz, fs, gain_percent))
            plant(side, -3.0, origin, total, path=path, seed=23)
            return {'recorded_s': seconds, 'record_start_unix': 1.5e9, 'gain_percent': gain_percent,
                    'adc_overflows': adc, 'lost_sample_overflows': lost}
        return record

    old, old_rec = bt_synth.BLUEY, ota.record
    bluey = os.path.join(tmp, 'bluey_rec')
    bt_synth.BLUEY = bluey
    try:
        ota.record = fake(3, 0)
        code, text, err = run([side_path, '--record', '2', '--capture', 'fakerec', '--gain', '45'])
        out = os.path.join(bluey, 'data', 'sidecar', 'fakerec.json')
        check(code == 0 and len(calls) == 1 and calls[0] == (
            os.path.join(bluey, 'data', 'iq', 'fakerec.cf32'), 2.0, 2441.0e6, 40e6, 45.0),
              'record() was told the sidecar\'s centre 2441 MHz, 40 MS/s, gain 45 and the capture path: %s'
              % (calls[0][1:] if calls else None,))
        check(os.path.isfile(out), 'the truth was written')
        if os.path.isfile(out):
            got = json.load(open(out))
            check(got['receiver'] == {'radio': 'BB60D', 'gain_percent': 45.0, 'adc_overflows': 3,
                                      'lost_sample_overflows': 0, 'record_start_unix': 1.5e9},
                  'the truth\'s receiver block carries both counters: %s' % got['receiver'])
        check('lost-sample overflows 0' in text and 'ADC overflows 3' in text,
              'the summary prints both counters')
        # No --gain: the 60 % default.
        del calls[:]
        code, _, _ = run([side_path, '--record', '1', '--out', os.path.join(tmp, 'rec2.cf32')])
        check(len(calls) == 1 and calls[0][4] == 60.0 and calls[0][0].endswith('rec2.cf32'),
              'without --gain it records at 60 %% (%s)' % (calls[0][4] if calls else None,))
        # Lost samples: graded and summarised, never a truth.
        ota.record = fake(0, 2)
        os.remove(out)
        code, text, err = run([side_path, '--record', '2', '--capture', 'lostrec', '--gain', '45'])
        lost_out = os.path.join(bluey, 'data', 'sidecar', 'lostrec.json')
        check(code == 0 and not os.path.exists(lost_out) and 'lost samples 2 time' in text
              and 'no truth sidecar written' in text,
              'two lost-sample overflows: no truth sidecar, and the summary says why')
        check(ota.lost_samples_problem({'lost_sample_overflows': 0}) is None
              and ota.lost_samples_problem({}) is None
              and ota.lost_samples_problem({'lost_sample_overflows': 1}),
              'lost_samples_problem: 0 and unknown are fine, 1 is a problem')
    finally:
        bt_synth.BLUEY = old
        ota.record = old_rec


def main():
    with tempfile.TemporaryDirectory(prefix='bt_ota_hop_') as tmp:
        check_main(tmp)
        check_numbers()
        check_clocks()
        check_map_and_dh1()
        check_outside_band()
        check_edges()
        check_12db()
        check_found_strictness()
        check_noisy_short()
        check_interferer()
        check_frames()
        check_silent_and_edges()
        check_losses()
        check_end_shifts_and_far_kinks()
        check_ambient_tail()
        check_clock_rate()
        check_carrier()
        check_off_the_line()
        check_no_shot()
        check_centre_2440()
        check_flatness_exclusions()
        check_sidecar_refusals(tmp)
        check_cli(tmp)
        check_record_function()
        check_record(tmp)
    print('\nRESULT: %s' % ('FAIL (%d)' % len(failures) if failures else 'PASS'))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
