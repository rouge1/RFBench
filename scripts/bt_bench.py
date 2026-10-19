#!/usr/bin/env python3
"""The bench tier for bluey-ox-walker: a fresh-set file's bursts from the VSG60A into the BB60D over a cable and a pad,
and the truth of the capture from the capture itself.

    python scripts/bt_bench.py shot --out DIR                         # the two noise-free shots, here
    python scripts/bt_bench.py dryrun SHOT.cf32 --out CAP.cf32 [--frac 0.37 --ppm -0.37 --cfo-hz 57100
                                      --snr-db 20 --drop-at N --drop 40]  # a capture made from the shot, no radio
    python scripts/bt_bench.py record CAP.cf32 --seconds 20           # the BB60D, 40 MS/s on 2439.0 MHz
    python scripts/bt_bench.py align CAP.cf32 SHOT.cf32 [--tx TX.json] [--rec REC.json] --out DIR [--name NAME]

**Never radiated.** The VSG60A is a signal generator, not a certified 2.4 GHz device: the bench is a cable and a pad
(at least 20 dB), antennas off (``knowledge/bluetooth.md``, the bench). ``scripts/bt_bench_play.py`` is the only thing
that transmits, on worklaptop1, and only with ``--go``.

**The shots.** ``SHOTS`` names two files of the carrier-threshold fresh set (``bt_synth_slotb2 --set FR``), the mirror
pair 0x885614 (24 ones) and 0xEED5EF (40 ones), both ``d23`` (+23 ppm drift, so the symbol phase walks through all 40
samples). A shot is that file's own 12 s plan rendered with no noise, divided by ``amplitude_for(20)`` so a burst's
|iq| is 1, and cut at ``SHOT_SAMPLES`` (10.0 s: the VSG60A's one-shot buffer was proven to 10.24 s at 40 MS/s). The
plan is not re-made at 10 s, which would place the train and the connection differently: the shot is, burst for burst
and sample for sample, the first 10 s of the delivered file without its noise (the file = shot x amplitude_for(20) +
its noise, which the tests check). No burst of either shot crosses the cut, and no two bursts overlap (peak |iq| 1.0).
Its sidecar is the file's, with ``in_shot`` on every burst and a ``bench_shot`` block carrying the sha256 the player
checks before it plays.

**The capture's truth comes from the capture.** ``align`` finds the shot in the recording and every burst by complex
cross-correlation with its own noise-free samples, after taking off the carrier it expects (the VSG60A's tuning offset
from ``--tx`` plus the burst's planted carrier). The position of every burst found gives ``delay(x) = capture index -
shot index`` at shot index ``x``; a straight line through them is the clock: ``a`` samples and ``ppm`` = 1e6 x slope
(capture samples per shot sample, minus 1). The shot's own +23 ppm drift is inside the shot's index, so ``ppm`` is the
two instruments' clocks only (about -0.37 in stages 2 and 3). Every burst's truth start is its exact shot start mapped
through that line. The carrier of every burst is measured (``cfo_hz``, against the BB60D's 2439.0 MHz) beside the one
planted (``cfo_planted_hz``); their difference is the instruments' (VSG tuning plus reference error).

``align`` refuses, with a reason, to write a truth that could be wrong: the BB60D lost samples (``--rec``, required
with ``--tx``); the shot not found within +-``CFO_SEARCH_HZ`` of the carrier ``--tx`` puts it on; **any** burst found
more than ``OFF_LINE_SAMPLES`` off the clock line (every burst not found, or off it, is measured again over +-``W_WIDE``
about the line first: a loss near either end moves only the bursts past it, which a fit would leave out, and the
reviewed first version accepted a 10-sample loss before the last burst); fewer than ``MIN_FOUND`` of the bursts found,
or any of the first or last ``STEP_MIN_SIDE`` not found; a step in the timing (``STEP_SAMPLES``, a line-plus-step fit:
the stage 3 grader's first version wrote a confident wrong truth after a 40-sample drop); a bend (a quadratic departing
``BEND_SAMPLES`` from the line; a reference whose rate drifts 0.01 ppm over the 10 s would trip it); a timing scatter
over ``MAX_JITTER``. Known and accepted: a loss of 1-2 samples inside the first or last ``STEP_MIN_SIDE`` bursts
(about 40 ms) moves only those bursts by under ``OFF_LINE_SAMPLES`` and is not seen. A ``--tx`` that does not say the
shot was sent, or names another shot, or a ``--rec`` of another file, is refused before anything is read; so is a
capture name whose truth already exists. The instruments' own group delay is in every position (nothing here can see
it: the delay at sample 0 is the cable, the instruments and when the recording started, together).
"""
import argparse
import hashlib
import json
import math
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import bt_synth, bt_synth_slotb as sb, bt_synth_slotb2 as g  # noqa: E402
from scripts.bt_synth_interf import NOISE_1MHZ, amplitude_for  # noqa: E402

FS = 40e6
CENTER_MHZ = 2439.0
SHOT_SAMPLES = 400_000_000
#: lap name: the fresh-set stem a shot is made from.
SHOTS = {'885614': 'F_885614_d23_n3004', 'EED5EF': 'F_EED5EF_d23_n3012'}
#: Samples read at once from a file of several gigabytes.
CHUNK = 8_000_000
#: The envelope's decimation for finding the shot: 10 us bins.
DEC = 400
#: Samples either side of a burst in the template.
PAD = 120
#: Search half-widths: the candidates' first look, then every burst about the line.
W_FIRST = 600
W_TRACK = 24
#: The second look at a burst not found, or off the line: 100 us either side of the line.
W_WIDE = 4000
#: Bursts tried at each candidate start, spread over the shot.
N_PROBE = 24
#: The carrier search at a candidate, about the expected carrier.
CFO_SEARCH_HZ = 10e3
CFO_STEP_HZ = 1e3
#: A burst is found when its normalised correlation is at least this. Noise alone gives about 1/sqrt(length),
#: 0.01-0.02; a burst at 20 dB in 1 MHz about 0.85.
MIN_NORM = 0.4
#: At 20 dB in 1 MHz every burst is found; a few may be lost to a glitch, no more.
MIN_FOUND = 0.999
#: One burst's own timing scatters about 0.4 sample for an ID and 0.23 for a DM1 at 20 dB in 1 MHz (near the bound for
#: bursts that short; measured on planted captures), so a burst is off the line only past 3 samples, about 7 sigma. The
#: truth is the line through every burst, not a burst's own reading. A lost sample is the step test's to find.
OFF_LINE_SAMPLES = 3.0
#: A lost or gained sample is a whole one; a line-plus-step fit reads it as about 1.
STEP_SAMPLES = 0.5
#: The fewest bursts either side of a split the step test tries (about 40 ms of a shot). A loss of 1-2 samples inside
#: the first or last STEP_MIN_SIDE bursts moves only those bursts and is not seen (3 or more puts them off the line);
#: their truth is then out by that much. Known and accepted, as in the stage 3 grader.
STEP_MIN_SIDE = 20
BEND_SAMPLES = 0.3
MAX_JITTER = 0.8
#: Seconds of capture kept either side of the shot.
CROP_MARGIN_S = 0.25


# ---------------------------------------------------------------------------------------------------- the shots

def shot_name(lap):
    return 'bench_%s_d23' % lap


def make_shot(lap, out_dir, n_samples=SHOT_SAMPLES, first=0):
    """Write ``bench_<lap>_d23.cf32`` and ``.json``: the fresh-set file's plan with no noise, unit amplitude, the first
    ``n_samples``. Returns the two paths. ``first`` (tests only; a bench shot starts at 0) starts the shot at that
    sample of the file instead, and every sample index in the sidecar is moved by it: the first 3 s of a file are
    page IDs alone, whose train repeats every 10 ms, and a short shot of them has no unique place in a capture."""
    stem = SHOTS[lap]
    plan = g.plan_file(stem)
    side = plan['sidecar']
    if plan['fs'] != FS or side['center_mhz'] != CENTER_MHZ:
        raise RuntimeError('%s is not 40 MS/s on 2439.0 MHz' % stem)
    scale = 1.0 / amplitude_for(side['snr_db'])
    os.makedirs(out_dir, exist_ok=True)
    iq_path = os.path.join(out_dir, shot_name(lap) + '.cf32')
    side_path = os.path.join(out_dir, shot_name(lap) + '.json')
    sha = hashlib.sha256()
    pos, peak, src = 0, 0.0, 0
    with open(iq_path, 'wb') as f:
        for blk in sb.render_blocks(plan, noise=False):
            if pos >= n_samples:
                break
            skip = max(first - src, 0)
            src += len(blk)
            if skip >= len(blk):
                continue
            out = (blk[skip:skip + n_samples - pos] * np.float32(scale)).astype('<c8', copy=False)
            peak = max(peak, float(np.abs(out).max()) if len(out) else 0.0)
            data = out.tobytes()
            sha.update(data)
            f.write(data)
            pos += len(out)
    if pos != n_samples:
        raise RuntimeError('%s has %d samples, fewer than %d' % (stem, pos, n_samples))
    sps = side['samples_per_symbol']
    if first:
        for e in side['bursts']:
            e['start_sample'] -= first
            if e.get('id_end_sample') is not None:
                e['id_end_sample'] -= first
        for key in ('first_id_sample', 'first_header_sample', 'connection_start_sample', 'connection_end_sample',
                    'train_start_sample'):
            if side.get(key) is not None:
                side[key] -= first
    for e in side['bursts']:
        e['in_shot'] = bool(e['rendered'] and e['start_sample'] - PAD >= 0
                            and e['start_sample'] + e['air_bits_length'] * sps + PAD <= n_samples)
    cut = [e['index'] for e in side['bursts'] if e['rendered'] and not e['in_shot']
           and e['start_sample'] < n_samples]
    side['bench_shot'] = {
        'name': shot_name(lap), 'source_stem': stem, 'source_file': 'synth_slotb2_%s.cf32' % stem,
        'source_generator_commit': side['generator_commit'], 'generator': 'SDR scripts/bt_bench.py',
        'first_source_sample': int(first),
        'generator_commit': bt_synth.commit(), 'n_samples': int(n_samples), 'seconds': n_samples / FS,
        'scale': scale, 'peak_abs_iq': peak, 'sha256': sha.hexdigest(),
        'n_in_shot': sum(e['in_shot'] for e in side['bursts']), 'cut_bursts': cut,
        'meaning': ('the source file\'s own plan rendered with no noise, times scale = 1/amplitude_for(snr_db), so a '
                    'burst has |iq| 1; the first n_samples. Over those samples the source file = shot / scale + its '
                    'noise. A burst is in_shot when it is rendered and whole, with %d samples either side, inside '
                    'the shot. The VSG60A level is the power of a burst.' % PAD)}
    side['noise_note_bench'] = 'the shot has no noise; snr_db, noise_* describe the source file only'
    with open(side_path, 'w') as f:
        json.dump(side, f, separators=(',', ':'))
    return iq_path, side_path


# ---------------------------------------------------------------------------------------------------- files

class Cf32:
    """A cf32 file read with a count, never mapped whole (a capture is several gigabytes)."""

    def __init__(self, path):
        self.path = os.fspath(path)
        self.n = os.path.getsize(self.path) // 8

    def __len__(self):
        return self.n

    def read(self, start, end):
        """Samples ``start:end``; zeros where that runs off either end."""
        start, end = int(start), int(end)
        out = np.zeros(max(end - start, 0), dtype=np.complex64)
        a, b = max(start, 0), min(end, self.n)
        if b > a:
            with open(self.path, 'rb') as f:
                f.seek(a * 8)
                out[a - start:b - start] = np.fromfile(f, dtype='<c8', count=b - a)
        return out


def envelope(cf, dec=DEC):
    """Mean |x|^2 over every ``dec`` samples, the last partial bin dropped."""
    nb = len(cf) // dec
    out = np.empty(nb)
    step = (CHUNK // dec) * dec
    for b0 in range(0, nb * dec, step):
        b1 = min(b0 + step, nb * dec)
        x = cf.read(b0, b1)
        out[b0 // dec:b1 // dec] = (x.real.astype(np.float64) ** 2 + x.imag.astype(np.float64) ** 2).reshape(-1, dec).mean(1)
    return out


def _xcorr_valid(big, small):
    """``c[k] = sum_n big[k + n] conj(small[n])`` for k = 0 .. len(big) - len(small)."""
    n = len(big) + len(small)
    nf = 1 << (n - 1).bit_length()
    c = np.fft.ifft(np.fft.fft(big, nf) * np.conj(np.fft.fft(small, nf)))
    return c[:len(big) - len(small) + 1]


def _rotate(x, f_hz, first):
    """``x`` times exp(-j 2 pi f n / fs) for n = first, first + 1, ..."""
    n = np.arange(first, first + len(x), dtype=np.float64)
    return x * np.exp(-2j * np.pi * f_hz * n / FS).astype(np.complex64)


def _frac_shift(x, d):
    """``x`` delayed by ``d`` samples (|d| < 1), by a phase ramp: the bursts are 1 MHz wide at 40 MS/s, so the edges
    of a padded template are zero and nothing wraps."""
    nf = 1 << (len(x) - 1).bit_length()
    f = np.fft.fftfreq(nf)
    return np.fft.ifft(np.fft.fft(x, nf) * np.exp(-2j * np.pi * f * d))[:len(x)]


# ---------------------------------------------------------------------------------------------------- one burst

def burst_span(e, sps):
    """The shot samples of a burst's template: ``PAD`` either side of the air bits."""
    return e['start_sample'] - PAD, e['start_sample'] + e['air_bits_length'] * sps + PAD


def measure(cap, shot, e, sps, delay, w, f_hz):
    """One burst: the shot template against the capture about ``delay`` (capture index - shot index) +- ``w``, after
    taking ``f_hz`` off the capture. Returns (delay, norm, residual carrier Hz)."""
    lo, hi = burst_span(e, sps)
    src = shot.read(lo, hi).astype(np.complex128)
    d0 = int(round(delay))
    seg = cap.read(lo + d0 - w, hi + d0 + w).astype(np.complex128)
    seg = _rotate(seg, f_hz, lo + d0 - w)
    c = np.abs(_xcorr_valid(seg, src))
    k0 = int(np.argmax(c))
    if k0 < 3 or k0 > len(c) - 4:
        return d0 - w + k0, 0.0, 0.0                      # the peak is the window's edge: the burst is beyond it
    ks = np.arange(max(k0 - 3, 0), min(k0 + 4, len(c)))
    frac = 0.0
    if len(ks) >= 5:
        p = np.polyfit(ks - k0, c[ks], 2)
        if p[0] < 0:
            frac = float(np.clip(-p[1] / (2 * p[0]), -1.0, 1.0))
    part = seg[k0:k0 + len(src)]
    energy = math.sqrt(float(np.vdot(src, src).real) * float(np.vdot(part, part).real))
    norm = float(c[k0] / energy) if energy > 0 else 0.0
    z = part * np.conj(_frac_shift(src, frac))
    m = len(z) // 2
    resid = float(np.angle(np.sum(z[m:2 * m] * np.conj(z[:m]))) * FS / (2 * np.pi * m))
    return d0 - w + k0 + frac, norm, resid


# ---------------------------------------------------------------------------------------------------- the whole shot

def _probes(bursts, n=N_PROBE):
    """Connection bursts (else any) spread over the shot, for testing a candidate start."""
    conn = [e for e in bursts if e['role'] != 'id_page'] or bursts
    idx = np.linspace(0, len(conn) - 1, min(n, len(conn))).round().astype(int)
    return [conn[i] for i in sorted(set(idx))]


def locate(cap, shot, bursts, sps, f_expect):
    """The shot's start in the capture and the carrier left after ``f_expect``: candidates from the envelope (its slot
    pattern repeats, so the highest peak alone is not trusted), each tried on ``N_PROBE`` bursts with a carrier search.
    Returns (probe rows [(x, delay)], f_residual, score, why or None)."""
    if len(cap) < len(shot):
        return None, None, None, 'the recording (%d samples) is shorter than the shot (%d)' % (len(cap), len(shot))
    es, ec = envelope(shot), envelope(cap)
    c = _xcorr_valid(ec - ec.mean(), es - es.mean()).real
    order = np.argsort(c)[::-1]
    cands = []
    for k in order:
        if all(abs(int(k) - j) > 3 for j in cands):
            cands.append(int(k))
        if len(cands) == 8:
            break
    probes = _probes(bursts)
    best = None
    for k in cands:
        for f in np.arange(-CFO_SEARCH_HZ, CFO_SEARCH_HZ + 1, CFO_STEP_HZ):
            rows = [measure(cap, shot, e, sps, k * DEC, W_FIRST, f_expect(e) + f) for e in probes]
            score = float(np.median([r[1] for r in rows]))
            if best is None or score > best[2]:
                best = (rows, f, score, k)
    rows, f, score, k = best
    if score < MIN_NORM:
        return None, None, score, ('the shot was not found: the best start has a median correlation %.3f (under %.2f), '
                                   'searching +-%.0f kHz about the carrier --tx puts it on'
                                   % (score, MIN_NORM, CFO_SEARCH_HZ / 1e3))
    pairs = [(e['start_sample'] + e['timing_frac'], r[0]) for e, r in zip(probes, rows) if r[1] >= MIN_NORM]
    resid = float(np.median([r[2] for r in rows if r[1] >= MIN_NORM]))
    return pairs, float(f + resid), score, None


def _fit(x, d, w=None):
    p = np.polyfit(x, d, 1, w=w)
    return float(p[1]), float(p[0])


def step_test(x, d):
    """The largest step in the timing: at each split (``STEP_MIN_SIDE`` bursts at least each side) a line with one slope and a step
    ``c`` at the split is fitted to ``d``; returns ``(|c|, shot sample of the split)`` at the split with the largest.
    A plain line's residuals would hide much of a step in the slope (a 1-sample drop in a 0.4 s shot read as 0.27 and
    a rate 0.09 ppm off)."""
    n, m = len(d), STEP_MIN_SIDE
    if n < 2 * m + 10:
        return 0.0, None
    xc = (x - x.mean()) / max(np.ptp(x), 1.0)

    def step_at(i):
        a = np.column_stack([np.ones(n), xc, (np.arange(n) >= i).astype(float)])
        return abs(float(np.linalg.lstsq(a, d, rcond=None)[0][2]))

    coarse = np.unique(np.linspace(m, n - m, min(400, n - 2 * m)).round().astype(int))
    best = max(coarse, key=step_at)
    fine = range(max(m, best - 30), min(n - m, best + 31))
    best = max(fine, key=step_at)
    return step_at(best), float(x[best])


def align(cap_path, shot_path, shot_side, tx=None, rec=None):
    """The truth of a capture of one shot. Returns ``result``: ``problem`` (None when the truth may be written),
    the clock, the carriers, and ``rows`` (index -> dict) for every burst in the shot."""
    cap, shot = Cf32(cap_path), Cf32(shot_path)
    info = shot_side['bench_shot']
    result = dict(problem=None, shot=info['name'], capture=os.path.basename(cap_path), capture_samples=len(cap))
    if len(shot) != info['n_samples']:
        result['problem'] = 'the shot file has %d samples, its sidecar says %d' % (len(shot), info['n_samples'])
        return result
    lost = (rec or {}).get('lost_sample_overflows')
    if lost:
        result['problem'] = ('the BB60D lost samples %d time(s) while recording: every burst after a loss is early by '
                             'an unknown amount' % lost)
        return result
    lo_off = 0.0
    if tx is not None:
        lo_off = (float(tx['vsg_center_mhz']) - CENTER_MHZ) * 1e6
    result['vsg_offset_hz_set'] = lo_off
    sps = shot_side['samples_per_symbol']
    bursts = [e for e in shot_side['bursts'] if e.get('in_shot')]

    # The shot's samples carry each burst's planted carrier, so the template already has it: only the VSG60A's tuning
    # is taken off the capture, and the planted carrier is added back to what is measured.
    def f_expect(e):
        return lo_off

    pairs, f_res, score, why = locate(cap, shot, bursts, sps, f_expect)
    result.update(locate_score=score)
    if why:
        result['problem'] = why
        return result
    if not bursts:
        result['problem'] = 'the shot has no burst in it'
        return result
    # The first line is through the probes that agree with their median (spread over the shot, so the slope is
    # right from the start; a wrong early burst cannot steer it).
    px, pd = np.array([p[0] for p in pairs]), np.array([p[1] for p in pairs])
    agree = np.abs(pd - np.median(pd)) < 50
    a, slope = _fit(px[agree], pd[agree]) if agree.sum() >= 3 else (float(np.median(pd)), 0.0)
    rows = {}
    for e in bursts:
        x = e['start_sample'] + e['timing_frac']
        d, norm, resid = measure(cap, shot, e, sps, a + slope * x, W_TRACK, f_expect(e) + f_res)
        rows[e['index']] = dict(x=x, delay=d, norm=norm, cfo_resid=resid, found=norm >= MIN_NORM)
    found = [rows[e['index']] for e in bursts if rows[e['index']]['found']]
    if len(found) < 10:
        result['problem'] = 'only %d bursts were found about the located start' % len(found)
        return result
    xs, ds = np.array([r['x'] for r in found]), np.array([r['delay'] for r in found])
    a, slope = _fit(xs, ds)
    keep = np.abs(ds - (a + slope * xs)) <= OFF_LINE_SAMPLES
    a, slope = _fit(xs[keep], ds[keep])
    # Every burst is judged against that line: one not found, or off it, is measured again over W_WIDE about it. A
    # burst found off the line means samples lost or gained (or a burst in the wrong place) and refuses the capture,
    # however few there are: a loss near either end moves only the bursts past it, which a fit leaves out.
    off = []
    for e in bursts:
        row = rows[e['index']]
        pred = a + slope * row['x']
        if not row['found'] or abs(row['delay'] - pred) > OFF_LINE_SAMPLES:
            d, norm, resid = measure(cap, shot, e, sps, pred, W_WIDE, f_expect(e) + f_res)
            row.update(delay=d, norm=norm, cfo_resid=resid, found=norm >= MIN_NORM, remeasured=True)
        if row['found'] and abs(row['delay'] - pred) > OFF_LINE_SAMPLES:
            row['found'] = False
            row['reason'] = 'off the line by %+.1f samples' % (row['delay'] - pred)
            off.append(e['index'])
        elif not row['found']:
            row['reason'] = 'correlation %.3f under %.2f within +-%d samples of the line' % (row['norm'], MIN_NORM, W_WIDE)
    n_found = sum(rows[e['index']]['found'] for e in bursts)
    result.update(n_bursts=len(bursts), n_found=n_found, n_off_line=len(off))
    if off:
        first = rows[off[0]]
        result['problem'] = ('%d burst(s) found more than %.1f samples off the clock line (the first at shot sample %.0f, '
                             '%s): samples lost or gained in the recording' % (len(off), OFF_LINE_SAMPLES, first['x'],
                                                                             first['reason']))
        return result
    if n_found < MIN_FOUND * len(bursts):
        result['problem'] = 'only %d of %d bursts were found (under %.1f %%)' % (n_found, len(bursts), 100 * MIN_FOUND)
        return result
    ends = [e['index'] for e in bursts[:STEP_MIN_SIDE] + bursts[-STEP_MIN_SIDE:] if not rows[e['index']]['found']]
    if ends:
        result['problem'] = ('%d of the first or last %d bursts were not found: a loss there would not be seen'
                             % (len(ends), STEP_MIN_SIDE))
        return result
    found = [rows[e['index']] for e in bursts if rows[e['index']]['found']]
    xs, ds = np.array([r['x'] for r in found]), np.array([r['delay'] for r in found])
    a, slope = _fit(xs, ds)
    r = ds - (a + slope * xs)
    jitter = float(np.std(r))
    step, at = step_test(xs, ds)
    q = np.polyfit(xs, ds, 2)
    span = np.linspace(xs.min(), xs.max(), 201)
    bend = float(np.max(np.abs(np.polyval(q, span) - (a + slope * span))))
    result.update(delay_at_0=a, ppm=slope * 1e6, jitter_samples=jitter, step_samples=step, step_at_shot_sample=at,
                  bend_samples=bend)
    if step > STEP_SAMPLES:
        result['problem'] = ('a step of %.2f samples in the timing at shot sample %.0f: samples lost or gained in the '
                             'recording' % (step, at))
        return result
    if bend > BEND_SAMPLES:
        result['problem'] = 'the clock bends %.2f samples from a straight line over the shot' % bend
        return result
    if jitter > MAX_JITTER:
        result['problem'] = 'the timing scatters %.2f samples about the clock line (over %.2f)' % (jitter, MAX_JITTER)
        return result
    planted = {e['index']: float(e.get('cfo_hz') or 0.0) for e in bursts}
    inst = []
    for e in bursts:
        row = rows[e['index']]
        if row['found']:
            row['cfo_hz'] = lo_off + f_res + row['cfo_resid'] + planted[e['index']]
            inst.append(row['cfo_hz'] - planted[e['index']])
        else:
            row['cfo_hz'] = None
    result.update(f_located_hz=lo_off + f_res, cfo_instrument_hz=float(np.median(inst)),
                  cfo_instrument_spread_hz=float(np.percentile(inst, 90) - np.percentile(inst, 10)),
                  reference_error_hz=float(np.median(inst)) - lo_off, rows=rows)
    return result


def crop_bounds(result, n_cap, n_shot):
    """The capture samples kept: the shot and ``CROP_MARGIN_S`` either side."""
    a, slope = result['delay_at_0'], result['ppm'] * 1e-6
    m = int(CROP_MARGIN_S * FS)
    first = max(int(math.floor(a)) - m, 0)
    last = min(int(math.ceil(n_shot + a + slope * n_shot)) + m, n_cap)
    return first, last


#: Shot-time keys of a burst that the capture cannot carry: the grid and slot times of the source file's clock.
STALE_BURST_KEYS = ('slot_start_us', 'grid_offset_us', 'grid_offset_unwrapped_us', 'start_mod_625_us', 'id_end_us')
#: Top-level keys about the source file's noise and timing grid.
STALE_KEYS = ('noise_1mhz', 'noise_seed', 'noise_block_samples', 'noise_note', 'snr_bw_hz', 'lead_in_samples',
              'grid_offset_us_first_slot', 'first_tx_slot_start_us', 'crossings')


def capture_sidecar(shot_side, result, crop0, n_samples, name, tx=None, rec=None):
    """The truth of the cropped capture: the shot's sidecar with every in-shot burst placed in capture samples."""
    a, slope = result['delay_at_0'], result['ppm'] * 1e-6
    sps = shot_side['samples_per_symbol']

    def to_cap(x):
        return x + a + slope * x - crop0

    side = {k: v for k, v in shot_side.items() if k != 'bursts'}
    out = []
    for e in shot_side['bursts']:
        if not e.get('in_shot'):
            continue
        row = result['rows'][e['index']]
        b = dict(e)
        exact = to_cap(e['start_sample'] + e['timing_frac'])
        b['start_sample'] = int(math.floor(exact))
        b['timing_frac'] = exact - math.floor(exact)
        b['symbol_phase'] = int(b['start_sample'] % sps)
        b['start_exact'] = exact
        b['shot_start_sample'] = e['start_sample']
        b['shot_timing_frac'] = e['timing_frac']
        if e.get('id_end_sample') is not None:
            b['id_end_sample'] = to_cap(e['id_end_sample'])
        b['found'] = bool(row['found'])
        b['reason'] = row.get('reason')
        b['measured_start_exact'] = row['x'] + row['delay'] - crop0
        b['norm'] = row['norm']
        b['cfo_planted_hz'] = e.get('cfo_hz')
        b['cfo_hz'] = row['cfo_hz']
        b['start_us'] = exact / (FS / 1e6)
        for key in STALE_BURST_KEYS:
            b[key] = None
        b['carrier_phase_rad'] = None
        b['snr_db'] = None
        out.append(b)
    for key in ('first_id_sample', 'first_header_sample', 'connection_start_sample', 'connection_end_sample',
                'train_start_sample'):
        if side.get(key) is not None:
            side[key] = to_cap(side[key])
    side.update(
        name=name, kind='bench capture', n_samples=int(n_samples), n_bursts=len(out),
        n_rendered=len(out), capture_crop_first_sample=int(crop0),
        clock={'delay_at_shot_sample_0': a - crop0, 'ppm': result['ppm'], 'jitter_samples': result['jitter_samples'],
               'step_samples': result['step_samples'], 'bend_samples': result['bend_samples'],
               'meaning': ('capture index = shot index x (1 + ppm 1e-6) + delay_at_shot_sample_0. ppm is the two '
                           'instruments\' clocks: the shot\'s own drift (drift_ppm_signed) is inside the shot index.')},
        cfo={'vsg_offset_hz_set': result['vsg_offset_hz_set'], 'instrument_hz': result['cfo_instrument_hz'],
             'instrument_spread_hz': result['cfo_instrument_spread_hz'],
             'reference_error_hz': result['reference_error_hz'],
             'meaning': ('per burst cfo_hz is measured, against the BB60D centre center_mhz; cfo_planted_hz is the '
                         'carrier in the shot. instrument_hz = median of the difference (the VSG60A tuning offset '
                         'vsg_offset_hz_set plus the reference error).')},
        start_sample_meaning=('the truth: the burst\'s exact shot start mapped through the clock line, in capture '
                              'samples from the first sample of this file; start_exact = start_sample + timing_frac. '
                              'measured_start_exact is the burst\'s own correlation, for checking.'),
        found_meaning=('found: the burst\'s normalised correlation with its noise-free samples is at least %.2f and '
                       'it lies within %.1f samples of the line; a burst not found is still placed from the line, '
                       'with a reason' % (MIN_NORM, OFF_LINE_SAMPLES)),
        snr_db=None, snr_note='the capture\'s noise is the BB60D\'s own; no per-burst SNR is planted',
        aligner='SDR scripts/bt_bench.py', aligner_commit=bt_synth.commit(),
        tx=tx, recording=rec, dry_run=bool(tx is None or tx.get('dry_run')))
    side.pop('noise_note_bench', None)
    for key in STALE_KEYS:
        side.pop(key, None)
    side['stale_keys_note'] = ('removed from the shot\'s sidecar: %s (the source file\'s noise and grid); per burst set '
                               'null: %s; start_us = start_exact / 40' % (', '.join(STALE_KEYS), ', '.join(STALE_BURST_KEYS)))
    side['bursts'] = out
    return side


# ---------------------------------------------------------------------------------------------------- record, dryrun

def record(path, seconds, gain_percent=60.0):
    """The BB60D, 40 MS/s on 2439.0 MHz, through ``bt_ota_hop_check.record`` (which counts lost samples)."""
    from scripts import bt_ota_hop_check as hc
    info = hc.record(path, seconds, CENTER_MHZ * 1e6, FS, gain_percent)
    info.update(file=os.path.basename(path), center_mhz=CENTER_MHZ, sample_rate=FS)
    return info


#: The capture maker's interpolator: a Kaiser-windowed sinc of ``SINC_TAPS`` taps, tabulated at ``SINC_PHASES``
#: fractions. A cubic was up to 0.12 sample out on a burst 11 MHz off centre (the reviewer measured it); this is under
#: 0.002 there, so a test's tolerance can see an aligner bias of a few hundredths.
SINC_TAPS = 16
SINC_PHASES = 1024


def _sinc_table():
    k = np.arange(-SINC_TAPS // 2 + 1, SINC_TAPS // 2 + 1)                 # -7 .. 8
    mu = np.arange(SINC_PHASES + 1) / SINC_PHASES
    t = mu[:, None] - k[None, :]
    w = np.kaiser(4 * SINC_TAPS + 1, 8.0)
    win = np.interp(t, np.linspace(-SINC_TAPS / 2, SINC_TAPS / 2, len(w)), w)
    h = np.sinc(t) * win
    return k, h / h.sum(1, keepdims=True)


def make_capture(shot_path, out_path, pre=0.5, post=0.5, frac=0.0, ppm=0.0, cfo_hz=0.0, snr_db=None, drop_at=None,
                 drop=0, seed=7, block=1_000_000, vsg_offset_hz=0.0, bend=0.0):
    """A capture made from the shot with no radio: ``pre`` s before it, the shot moved by ``frac`` samples, its clock
    ``ppm`` (capture samples per shot sample, minus 1, x 1e6), a ``bend`` (samples the delay rises in the middle of the
    shot over the straight line, a parabola), rotated by ``cfo_hz``, noise at ``snr_db`` in 1 MHz for |iq| 1 (none when
    None), and ``drop`` samples taken out from capture sample ``drop_at``. ``vsg_offset_hz`` is only recorded: the
    tuning offset a player would have been given, which ``align`` takes as known (``cfo_hz`` minus it plays the
    reference error). Returns the planted truth: capture index = shot index x (1 + ppm 1e-6) + delay_at_0, before the
    drop."""
    shot = Cf32(shot_path)
    n_shot = len(shot)
    a = pre * FS + frac
    e = ppm * 1e-6
    total = int(round(pre * FS + n_shot * (1 + e) + post * FS)) + drop
    rng = np.random.default_rng(seed)
    sigma = None if snr_db is None else math.sqrt(10 ** (-snr_db / 10) * FS / 1e6 / 2)
    taps, table = _sinc_table()
    with open(out_path, 'wb') as f:
        for m0 in range(0, total, block):
            m1 = min(m0 + block, total)
            m = np.arange(m0, m1, dtype=np.float64)
            x = (m - a) / (1 + e)
            if bend:
                u = np.clip(x / n_shot, 0.0, 1.0)
                x = x - 4 * bend * u * (1 - u) / (1 + e)
            i = np.floor(x).astype(np.int64)
            mu = x - i
            out = np.zeros(m1 - m0, dtype=np.complex128)
            ok = (i + taps[0] >= 0) & (i + taps[-1] < n_shot)
            if ok.any():
                lo, hi = int(i[ok].min()) + taps[0], int(i[ok].max()) + taps[-1] + 1
                s = shot.read(lo, hi).astype(np.complex128)
                ph = np.rint(mu[ok] * SINC_PHASES).astype(np.int64)
                base = i[ok] - lo
                acc = np.zeros(int(ok.sum()), dtype=np.complex128)
                for t, k in enumerate(taps):
                    acc += s[base + k] * table[ph, t]
                out[ok] = acc
            if cfo_hz:
                out *= np.exp(2j * np.pi * cfo_hz * m / FS)
            if sigma is not None:
                out += sigma * (rng.standard_normal(len(out)) + 1j * rng.standard_normal(len(out)))
            if drop and drop_at is not None:
                out = out[~((m >= drop_at) & (m < drop_at + drop))]
            out.astype('<c8').tofile(f)
    return dict(delay_at_0=a, ppm=ppm, cfo_hz=cfo_hz, vsg_offset_hz=vsg_offset_hz, snr_db=snr_db, drop_at=drop_at,
                drop=drop, frac=frac, pre_s=pre, bend=bend)


# ---------------------------------------------------------------------------------------------------- main

def summary(result):
    if result.get('problem'):
        return 'REFUSED: %s' % result['problem']
    return ('found %d of %d bursts (%d off the line); clock %+.4f ppm, delay at shot sample 0 %.3f, jitter %.3f, '
            'step %.3f, bend %.3f samples; carrier: instruments %+.0f Hz (spread %.0f), VSG set %+.0f, reference '
            'error %+.0f Hz' % (result['n_found'], result['n_bursts'], result['n_off_line'], result['ppm'],
                                result['delay_at_0'], result['jitter_samples'], result['step_samples'],
                                result['bend_samples'], result['cfo_instrument_hz'],
                                result['cfo_instrument_spread_hz'], result['vsg_offset_hz_set'],
                                result['reference_error_hz']))


def _load(path):
    with open(path) as f:
        return json.load(f)


def write_crop(cap_path, first, last, out_path):
    cap = Cf32(cap_path)
    with open(out_path, 'wb') as f:
        for p in range(first, last, CHUNK):
            cap.read(p, min(p + CHUNK, last)).astype('<c8', copy=False).tofile(f)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('shot', help='write the two noise-free shots')
    p.add_argument('--out', required=True)
    p.add_argument('--lap', choices=sorted(SHOTS), action='append')
    p = sub.add_parser('dryrun', help='a capture made from the shot, no radio')
    p.add_argument('shot')
    p.add_argument('--out', required=True)
    p.add_argument('--frac', type=float, default=0.0)
    p.add_argument('--ppm', type=float, default=0.0)
    p.add_argument('--cfo-hz', type=float, default=0.0, help='the rotation: VSG offset plus a reference error')
    p.add_argument('--vsg-offset-hz', type=float, default=0.0, help='the tuning offset align is told')
    p.add_argument('--snr-db', type=float)
    p.add_argument('--drop-at', type=int)
    p.add_argument('--drop', type=int, default=0)
    p.add_argument('--bend', type=float, default=0.0)
    p = sub.add_parser('record', help='record the BB60D')
    p.add_argument('out')
    p.add_argument('--seconds', type=float, default=20.0)
    p.add_argument('--gain', type=float, default=60.0)
    p = sub.add_parser('align', help='the truth of a capture')
    p.add_argument('capture')
    p.add_argument('shot')
    p.add_argument('--tx', help="bt_bench_play.py's sidecar of the shot played")
    p.add_argument('--rec', help="the recorder's json")
    p.add_argument('--dry', help='a dry run: the planted truth json from dryrun (no --tx)')
    p.add_argument('--out', required=True, help='folder for the cropped capture and its truth')
    p.add_argument('--name', help='capture name (default: the capture file\'s)')
    args = ap.parse_args(argv)

    if args.cmd == 'shot':
        for lap in args.lap or sorted(SHOTS):
            t0 = time.time()
            paths = make_shot(lap, args.out)
            side = _load(paths[1])['bench_shot']
            print('%s  %d samples, peak %.4f, %d bursts in the shot, sha256 %s, %.0f s' % (
                paths[0], side['n_samples'], side['peak_abs_iq'], side['n_in_shot'], side['sha256'][:16],
                time.time() - t0), flush=True)
        return 0
    if args.cmd == 'dryrun':
        truth = make_capture(args.shot, args.out, frac=args.frac, ppm=args.ppm, cfo_hz=args.cfo_hz,
                             snr_db=args.snr_db, drop_at=args.drop_at, drop=args.drop, vsg_offset_hz=args.vsg_offset_hz,
                             bend=args.bend)
        with open(args.out + '.planted.json', 'w') as f:
            json.dump(truth, f, indent=1)
        print(args.out, json.dumps(truth))
        return 0
    if args.cmd == 'record':
        info = record(args.out, args.seconds, args.gain)
        with open(args.out + '.rec.json', 'w') as f:
            json.dump(info, f, indent=1)
        print(args.out, json.dumps(info))
        return 0 if not info['lost_sample_overflows'] else 1
    shot_side = _load(os.path.splitext(args.shot)[0] + '.json')
    tx = _load(args.tx) if args.tx else None
    rec = _load(args.rec) if args.rec else None
    if tx is None and not args.dry:
        ap.error('give --tx (the player\'s sidecar), or --dry for a dry run')
    if tx is not None:
        if tx.get('sent') is not True:
            ap.error('the player\'s sidecar does not say the shot was sent (sent %r, state %r)'
                     % (tx.get('sent'), tx.get('state')))
        if tx.get('shot_sha256') != shot_side['bench_shot']['sha256']:
            ap.error('the player played another shot (sha256 %s, this shot %s)'
                     % (str(tx.get('shot_sha256'))[:16], shot_side['bench_shot']['sha256'][:16]))
    if tx is not None and rec is None:
        ap.error('--tx needs --rec, the recorder\'s json: without it a loss of samples in the BB60D is not seen')
    if rec is not None and rec.get('file') != os.path.basename(args.capture):
        ap.error('the --rec is of %r, not of this capture' % rec.get('file'))
    name = args.name or os.path.splitext(os.path.basename(args.capture))[0]
    for ext in ('.cf32', '.json', '.align.json'):
        if os.path.exists(os.path.join(args.out, name + ext)):
            ap.error('%s already exists: give another --name or --out (an older truth is never left beside a new '
                     'result)' % os.path.join(args.out, name + ext))
    if tx is None:
        planted = _load(args.dry)
        tx = {'dry_run': True, 'planted': planted, 'vsg_center_mhz': CENTER_MHZ + planted['vsg_offset_hz'] / 1e6}
    t0 = time.time()
    result = align(args.capture, args.shot, shot_side, tx=tx, rec=rec)
    print(summary(result), '(%.0f s)' % (time.time() - t0), flush=True)
    os.makedirs(args.out, exist_ok=True)
    report = {k: v for k, v in result.items() if k != 'rows'}
    with open(os.path.join(args.out, name + '.align.json'), 'w') as f:
        json.dump(report, f, indent=1)
    if result['problem']:
        return 1
    first, last = crop_bounds(result, result['capture_samples'], shot_side['bench_shot']['n_samples'])
    side = capture_sidecar(shot_side, result, first, last - first, name, tx=tx, rec=rec)
    iq_path = os.path.join(args.out, name + '.cf32')
    if os.path.abspath(iq_path) == os.path.abspath(args.capture):
        ap.error('the cropped capture would overwrite the recording')
    write_crop(args.capture, first, last, iq_path)
    with open(os.path.join(args.out, name + '.json'), 'w') as f:
        json.dump(side, f, separators=(',', ':'))
    print(iq_path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
