#!/usr/bin/env python3
"""Hold the interferer writer to what its samples say.

    python scripts/test_bt_synth_interf.py

``scripts/bt_synth_interf.py`` writes a hopping DH5 train under a known
interferer - a tone on a channel, Wi-Fi-like noise above +11 MHz, constant or
in frames - and the same train without, for bluey-ox-walker's item 6. Its
sidecar is the only truth that receiver gets, so this checks the sidecar against
the *samples*, from small runs (60 bursts and a small block, so there are many
blocks and seams), and never against the generator's own bookkeeping:

* the pairing: every interferer file is the clean file plus the interferer
  alone - ``file - clean`` is a pure tone, or band-limited noise, or exactly
  zero where a frame is off - and one file made from another seed is not;
* the tone: its frequency to 5 mHz and its power from a burst-free stretch of
  the file itself, 10 or 20 dB over the noise in the 1 MHz about it, and none
  in the clean file;
* the Wi-Fi noise: a 4096-point averaged spectrum flat within 1 dB over
  +11..+20 MHz, 20 dB over the floor, more than 40 dB down outside; and no seam
  at a block boundary, both statistically and against the same white noise
  filtered in one piece;
* the frames: lengths 0.3-3 ms, duty 0.30 +-0.03, raised-cosine edges, and the
  sidecar's schedule equal to the on/off the samples show;
* per burst, ``interferer_overlap`` and ``interferer_power_in_band_db``,
  recomputed from the sidecar's own frames and then from the samples' power in
  the channel's 1 MHz band during the burst;
* the bursts' own SNR is still 20 dB with the interferer taken away;
* a seed is a file, the named set is exactly the eight files, an unknown set is
  an error, and the sidecar has every key;
* the Bluetooth bursts themselves, from the samples of the clean file: each
  burst's carrier from the spectrum and the phase of its demodulated signal, its
  start from the rising edge of its envelope, against ``channel_mhz`` and
  ``start_sample + timing_frac``, so that a carrier a MHz off or a start 40
  samples late is not passed because the sidecar says the right thing;
* the sidecar's whole ``interferer_note`` (not phrases of it), the Wi-Fi
  filter's wrapped Nyquist skirt (disclosed, and measured to be there), and the
  required keys, ``afh_map_index`` among them;
* mutants of the generator, each of which one of the checks above must catch.
"""
import contextlib
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from unittest import mock

os.environ.setdefault('OMP_NUM_THREADS', '4')
import numpy as np  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from scripts import bt_synth, bt_synth_interf as g  # noqa: E402

PY = sys.executable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = tempfile.mkdtemp(prefix='test_interf_')   # the command-line checks write here
os.makedirs(SCRATCH, exist_ok=True)
SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bt_synth_interf.py')
FS = 40e6
NOISE = bt_synth.AMPLITUDE ** 2 / 100
BLOCK = 2 ** 17
MIX = [39, 43, 49, 50]            # the channels the small runs hop over, so every interferer is met
SEED = 6101
LEAD = 60000                      # burst-free samples before the first burst (it starts at 62480)
failures = []


def check(ok, what):
    print('  %s %s' % ('ok  ' if ok else 'FAIL', what))
    if not ok:
        failures.append(what)


def check_all(problems, what):
    """One line for a check function that returns a list of what is wrong."""
    check(not problems, what + ('' if not problems else ': ' + '; '.join(problems[:4])))


CACHE = {}


def build(key, bursts=60, channels=MIX, seed=SEED, block=BLOCK):
    k = (key, bursts, tuple(channels), seed, block)
    if k not in CACHE:
        CACHE[k] = g.synthesise_interf(key, bursts=bursts, channels=channels, seed=seed,
                                       block_samples=block)
    return CACHE[k]


def pair(key, **kw):
    """(file, clean, sidecar, d), d = file - clean in complex128."""
    iq, side = build(key, **kw)
    clean, _ = build('clean', **kw)
    return iq, clean, side, iq.astype(np.complex128) - clean.astype(np.complex128)


def band_power(x, f_lo, f_hi):
    """Mean-square of ``x`` in the bins between f_lo and f_hi (Hz), rectangular window."""
    n = len(x)
    X = np.fft.fft(x)
    f = np.fft.fftfreq(n, 1 / FS)
    m = (f >= f_lo) & (f < f_hi)
    return float(np.sum(np.abs(X[m]) ** 2)) / n ** 2


# --- the tone -------------------------------------------------------------------

#: What each named file is, written from the task and not from the sidecar or the
#: generator: its tones (channel, dB over the noise in 1 MHz), its Wi-Fi-like noise
#: (dB over the floor per MHz, gated or constant) and the channels of the map 31-50
#: it reaches (a tone: its own; the noise: channel 50 only).
EXPECT = {
    'clean': dict(tones=[], wifi=None, reach=set()),
    'cw49_p10': dict(tones=[(49, 10.0)], wifi=None, reach={49}),
    'cw49_p20': dict(tones=[(49, 20.0)], wifi=None, reach={49}),
    'cw43_p10': dict(tones=[(43, 10.0)], wifi=None, reach={43}),
    'cw43_p20': dict(tones=[(43, 20.0)], wifi=None, reach={43}),
    'wifi_const_p20': dict(tones=[], wifi=(20.0, False), reach={50}),
    'wifi_bursty_p20': dict(tones=[], wifi=(20.0, True), reach={50}),
    'both_p20': dict(tones=[(49, 20.0), (43, 20.0)], wifi=(20.0, True), reach={43, 49, 50}),
}

#: Phrases the sidecar's interferer_note has to carry, each a fact about the files.
#: The generator's own note and skirt text as imported, held to by whole string: a mutant that changes the
#: prose and keeps the phrases below, or the generator's constant patched under the test, is still caught.
NOTE_SNAPSHOT = g.INTERFERER_NOTE
SKIRT_SNAPSHOT = g.NYQUIST_SKIRT
NOTE_PHRASES = ['a cw tone only on its own channel', 'channel 50 only', '2452..2461',
                'end_sample = start_sample + bits * sps', 'ACTUAL squared frequency response',
                'null when there is no overlap', 'mean of the gate squared']


def tones_of(key):
    return [dict(channel=c, level_db=l) for c, l in EXPECT[key]['tones']]


def table_failures(key, side):
    """The sidecar's interferers, frames, note and per-burst reach against EXPECT."""
    bad = []
    want = EXPECT[key]
    got_tones = sorted((t['channel'], t['level_db']) for t in side['interferers'] if t['kind'] == 'cw')
    if got_tones != sorted(want['tones']):
        bad.append('tones in the sidecar %s, the table says %s' % (got_tones, sorted(want['tones'])))
    for t in side['interferers']:
        if t['kind'] == 'cw' and (t['channel_mhz'] != 2402 + t['channel'] or t['offset_mhz'] != 2402 + t['channel'] - 2441.0):
            bad.append('tone on ch %d has the wrong frequency in the sidecar' % t['channel'])
    wifi = [t for t in side['interferers'] if t['kind'] == 'wifi_noise']
    if (want['wifi'] is None) != (not wifi):
        bad.append('Wi-Fi noise present: %s, the table says %s' % (bool(wifi), want['wifi'] is not None))
    elif wifi:
        w = wifi[0]
        if (w['level_db'], w['bursty']) != want['wifi'] or w['band_mhz'] != [2452.0, 2461.0] \
                or w['band_offset_mhz'] != [11.0, 20.0]:
            bad.append('Wi-Fi noise in the sidecar is %s dB, bursty %s, band %s; the table says %s'
                       % (w['level_db'], w['bursty'], w['band_mhz'], want['wifi']))
    if ('interferer_frames' in side) != bool(want['wifi'] and want['wifi'][1]):
        bad.append('interferer_frames present only for the bursty noise')
    for phrase in NOTE_PHRASES + ['ENSEMBLE-EXPECTED', 'not a measurement of the realisation', '0.45 dB',
                                  'Nyquist', '-20..-19.95 MHz']:
        if phrase not in side['interferer_note']:
            bad.append('interferer_note lacks "%s"' % phrase)
    if side['interferer_note'] != NOTE_SNAPSHOT:
        bad.append('interferer_note is not the generator\'s own note, word for word')
    for t in wifi:
        if t.get('nyquist_skirt') != SKIRT_SNAPSHOT or '-20..-19.95 MHz' not in t['nyquist_skirt']:
            bad.append('the Wi-Fi description does not carry the Nyquist skirt text')
    for e in side['bursts']:
        should = e['channel'] in want['reach']
        if e['interferer_overlap'] and not should:
            bad.append('burst on ch %d is overlapped but the table does not reach that channel' % e['channel'])
            break
        always = e['channel'] in [c for c, _ in want['tones']] or (want['wifi'] and not want['wifi'][1])
        if should and always and not e['interferer_overlap']:
            bad.append('burst on ch %d is not overlapped but the table reaches it, always on' % e['channel'])
            break
    return bad


def quiet_segments(side, n=20000):
    """Burst-free stretches of ``n`` samples, from the sidecar's bursts: the
    lead-in, and the start of every gap between one burst's end and the next's
    start (400 samples of margin each side)."""
    segs = [(i, i + n) for i in range(0, LEAD - n + 1, n)]
    for e0, e1 in zip(side['bursts'], side['bursts'][1:]):
        a = e0['end_sample'] + 400
        if e1['start_sample'] - 400 - a >= n:
            segs.append((a, a + n))
    return segs


def tone_level_db(x, segs, f_hz):
    """The tone near ``f_hz`` over the noise in the 1 MHz about it, in dB, from
    Hann-windowed FFTs of the burst-free stretches ``segs`` of ``x``, their power
    spectra averaged: the 7 bins of the main lobe less their noise, over the mean
    bin of the 1 MHz (the main lobe's neighbourhood left out) scaled to 1 MHz.
    Also returns that noise in 1 MHz over ``NOISE``, in dB."""
    n = segs[0][1] - segs[0][0]
    w = np.hanning(n)
    s2 = float(np.sum(w ** 2))
    p = np.mean([np.abs(np.fft.fft(x[a:b].astype(np.complex128) * w)) ** 2 for a, b in segs], axis=0)
    f = np.fft.fftfreq(n, 1 / FS)
    k0 = int(np.argmin(np.abs(f - f_hz)))
    dk = np.abs(((np.arange(n) - k0 + n // 2) % n) - n // 2)
    near = (np.abs(f - f_hz) < 0.5e6) & (dk > 8)
    mean_bin = float(p[near].mean())
    main = float(p[dk <= 3].sum())
    tone = (main - 7 * mean_bin) / (n * s2)
    noise_1mhz = mean_bin * 1e6 / (s2 * FS)
    return 10 * np.log10(max(tone, 1e-30) / noise_1mhz), 10 * np.log10(noise_1mhz / NOISE)


def tone_fit(x, a, b, offset_hz):
    """The tone near ``offset_hz`` in ``x[a:b]`` (sample numbers ``a..b``):
    its complex amplitude and its frequency error in Hz. ``x`` is mixed down by
    the nominal frequency with the file's own sample numbers and averaged in boxes
    of 400 samples (10 us, a zero on every multiple of 100 kHz, so a tone 1, 6 or
    9 MHz away is gone); the amplitude is the mean of the boxes and the frequency
    the phase slope across them."""
    n = np.arange(a, b)
    cycles = offset_hz / FS * n
    y = x[a:b] * np.exp(-2j * np.pi * (cycles - np.floor(cycles)))
    m = (len(y) // 400) * 400
    z = y[:m].reshape(-1, 400).mean(axis=1)
    phase = np.unwrap(np.angle(z))
    slope = np.polyfit(np.arange(len(z)) * 400, phase, 1)[0]
    return complex(z.mean()), float(slope * FS / (2 * np.pi))


def quiet_stretch(side, total):
    """A stretch with no Wi-Fi frame in it, from the sidecar's own frames: the
    whole file when the noise is not gated, else the longest gap between frames,
    less 400 samples of edge each end, at most 2**20 long."""
    frames = side.get('interferer_frames')
    if frames is None:
        wifi = [t for t in side['interferers'] if t['kind'] == 'wifi_noise']
        return (0, min(total, 2 ** 20)) if not wifi else (0, 0)
    edges = [0] + [x for f in frames for x in (f['start_sample'], f['end_sample'])] + [total]
    gaps = list(zip(edges[::2], edges[1::2]))
    a, b = max(gaps, key=lambda ab: ab[1] - ab[0])
    return a + 400, min(b - 400, a + 400 + 2 ** 20)


def tone_model(d, tones, a, b):
    """The tones of ``tones`` over the whole of ``d``, their complex amplitudes
    fitted on the quiet stretch ``a..b`` (a tone's phase is fixed for the file)."""
    n = np.arange(len(d))
    out = np.zeros(len(d), dtype=np.complex128)
    for t in tones:
        offset = (2402 + t['channel'] - 2441.0) * 1e6
        c, _ = tone_fit(d, a, b, offset)
        cycles = offset / FS * n
        out += c * np.exp(2j * np.pi * (cycles - np.floor(cycles)))
    return out


def tone_failures(iq, clean, side, d, key):
    """What is wrong with the tones of a file: frequency and amplitude from
    ``file - clean`` on a quiet stretch, and the level from the burst-free lead-in
    of the file itself."""
    bad = []
    tones = tones_of(key)                       # the table's, not the sidecar's
    a, b = quiet_stretch(side, len(d))
    for t in tones:
        offset = (2402 + t['channel'] - 2441.0) * 1e6                  # the channel's own, not the sidecar's
        c, df = tone_fit(d, a, b, offset)
        want = np.sqrt(NOISE * 10 ** (t['level_db'] / 10))
        if abs(df) > 0.005:
            bad.append('tone on ch %d is %.4f Hz from %.0f Hz' % (t['channel'], df, offset))
        if abs(20 * np.log10(max(abs(c), 1e-30) / want)) > 0.02:
            bad.append('tone on ch %d amplitude %.5f, wanted %.5f' % (t['channel'], abs(c), want))
        level, floor = tone_level_db(iq, quiet_segments(side), offset)
        if abs(level - t['level_db']) > 0.2:
            bad.append('tone on ch %d is %.2f dB over the noise in 1 MHz, wanted %g'
                       % (t['channel'], level, t['level_db']))
        if abs(floor) > 0.3:
            bad.append('noise in 1 MHz is %.2f dB off the floor' % floor)
    return bad


def clean_tone_failures(clean, side):
    """No tone anywhere in the clean file (checked at both channels' places)."""
    bad = []
    for ch in (49, 43):
        level, _ = tone_level_db(clean, quiet_segments(side), (2402 + ch - 2441.0) * 1e6)
        if level > -10:
            bad.append('the clean file has a tone %.1f dB over the noise at channel %d' % (level, ch))
    return bad


# --- the pairing ----------------------------------------------------------------

def pairing_failures(iq, clean, side, d, clean_side, key):
    """``file - clean`` is the interferer alone: the same noise and bursts."""
    bad = []
    kinds = (['cw'] * len(EXPECT[key]['tones'])) + (['wifi_noise'] if EXPECT[key]['wifi'] else [])
    if not kinds:
        if np.any(d != 0):
            bad.append('a file with no interferer differs from the clean one')
        return bad
    tones = tones_of(key)
    a, b = quiet_stretch(side, len(d))
    model = tone_model(d, tones, a, b) if tones else np.zeros(len(d), dtype=np.complex128)
    if 'interferer_frames' in side:
        off = np.ones(len(d), dtype=bool)
        for fr in side['interferer_frames']:
            off[fr['start_sample']:fr['end_sample']] = False
        # off a frame the file is the clean file, apart from the tones
        r = np.max(np.abs(d[off] - model[off]))
        if r > 1e-6:
            bad.append('off the frames the file is not the clean one + the tones (max %.2e)' % r)
    elif 'wifi_noise' in kinds:
        # band-limited: the energy outside the band (0.25 MHz margin) is nothing
        # a Hann window: a rectangular one leaks a sharp band edge at -57 dB 0.25 MHz away
        X = np.fft.fft(d * np.hanning(len(d)))
        f = np.fft.fftfreq(len(d), 1 / FS) / 1e6
        out = (f < 10.75) & (f > -19.75)
        frac = float(np.sum(np.abs(X[out]) ** 2) / np.sum(np.abs(X) ** 2))
        if frac > 1e-8:
            bad.append('%.1e of file - clean is out of the Wi-Fi band' % frac)
    else:
        r = np.max(np.abs(d - model))
        if r > 1e-6:
            bad.append('file - clean is not a pure tone (max residual %.2e)' % r)
    # the bursts' sidecar is the clean file's, key for key but the interferer's
    for e0, e1 in zip(clean_side['bursts'], side['bursts']):
        for k in e0:
            if not k.startswith('interferer') and e0[k] != e1[k]:
                bad.append('burst key %s differs from the clean file\'s' % k)
                break
    return bad


# --- the Wi-Fi noise ------------------------------------------------------------

def welch_db(x, nseg):
    """Per-bin power spectral density of ``x`` in dB over NOISE per MHz, from
    non-overlapping 4096-point Hann segments (the rows of ``x``, (nseg, 4096))."""
    w = np.hanning(4096)
    s2 = float(np.sum(w ** 2))
    p = (np.abs(np.fft.fft(x * w, axis=1)) ** 2).mean(axis=0)
    f = np.fft.fftfreq(4096, 1 / FS) / 1e6
    return f, 10 * np.log10(p / (s2 * FS) * 1e6 / NOISE + 1e-300)


def wifi_psd_failures(d, side):
    """Flat within 1 dB over +11..+20 MHz at the level the sidecar claims, and
    more than 40 dB down outside (0.25 MHz of margin)."""
    bad = []
    if 'interferer_frames' in side:
        rows = []
        for fr in side['interferer_frames']:
            a, b = fr['start_sample'] + 400, fr['end_sample'] - 400     # the interior, edges left out
            for s in range(a, b - 4096, 4096):
                rows.append(d[s:s + 4096])
        x = np.array(rows)
    else:
        m = (len(d) // 4096) * 4096
        x = d[:m].reshape(-1, 4096)
    f, db = welch_db(x, len(x))
    inb = (f >= 11.03) & (f <= 19.97)
    outb = (f < 10.75) & (f > -19.75)
    lvl = 10 * np.log10(np.mean(10 ** (db[inb] / 10)))
    if abs(lvl - 20.0) > 0.15:
        bad.append('in-band level %.2f dB over the floor per MHz, wanted 20' % lvl)
    if len(x) > 1000:
        tol = 1.0
    else:
        tol = 1.0 + 1.5 * 4.34 / np.sqrt(len(x))
    k = db[inb]
    if np.max(np.abs(k - 20.0)) > tol:
        bad.append('the in-band spectrum is %.2f dB from flat at its worst' % np.max(np.abs(k - 20.0)))
    if np.max(db[outb]) > 20.0 - 40:
        bad.append('out of band the spectrum is only %.1f dB down' % (20 - np.max(db[outb])))
    return bad


def reference_wifi(side, n):
    """The same white noise as the file's recipe - block i from
    default_rng([seed, 3, i]), I then Q, history from default_rng([seed, 6]) -
    filtered in ONE convolution. The taps are the generator's."""
    seed, block = side['seed'], side['noise_block_samples']
    wifi = [t for t in side['interferers'] if t['kind'] == 'wifi_noise'][0]
    sigma = np.sqrt(NOISE * 10 ** (wifi['level_db'] / 10) * FS / 1e6 / 2)
    taps = g.wifi_taps()
    keep = len(taps) - 1

    def white(rng, m):
        z = np.empty(m, dtype=np.complex128)
        z.real = rng.normal(0, sigma, m)
        z.imag = rng.normal(0, sigma, m)
        return z
    parts = [white(np.random.default_rng([seed, 6]), keep)]
    for i, lo in enumerate(range(0, n, block)):
        parts.append(white(np.random.default_rng([seed, 3, i]), min(block, n - lo)))
    x = np.concatenate(parts)
    size = 1 << int(np.ceil(np.log2(len(x) + len(taps))))
    y = np.fft.ifft(np.fft.fft(x, size) * np.fft.fft(taps, size))
    return y[keep:keep + n]


def seam_failures(d, side):
    """No seam in a constant Wi-Fi file: it equals one filtering of the whole
    white noise, and the power just after a block boundary is the power elsewhere."""
    bad = []
    ref = reference_wifi(side, len(d))
    err = np.max(np.abs(d - ref))
    if err > 1e-6:
        bad.append('differs from one filtering of the whole white noise by %.2e' % err)
    block = side['noise_block_samples']
    p = np.abs(d) ** 2
    seams = np.arange(block, len(d) - 2048, block)
    after = np.mean([p[s:s + 1500].mean() for s in seams])
    around = np.mean([p[s - 1500:s].mean() for s in seams])
    allp = p.mean()
    if abs(after / allp - 1) > 0.06 or abs(around / allp - 1) > 0.06:
        bad.append('power after a seam is %.3f and before it %.3f of the mean' % (after / allp, around / allp))
    return bad


# --- the frames -----------------------------------------------------------------

def measured_frames(d):
    """The runs of nonzero samples of ``d`` (|d| above float32's rounding)."""
    on = (np.abs(d) > 3e-7).astype(np.int8)
    edges = np.flatnonzero(np.diff(np.concatenate([[0], on, [0]])))
    # a sample or two of a frame's edge can be under the threshold: bridge 4
    runs = []
    for a, b in zip(edges[::2], edges[1::2]):
        if runs and a - runs[-1][1] <= 4:
            runs[-1] = (runs[-1][0], int(b))
        else:
            runs.append((int(a), int(b)))
    return runs


def frame_failures(d, side):
    """Frame lengths and duty, raised-cosine edges, and the sidecar's schedule
    against the on/off of the samples."""
    bad = []
    frames = [(f['start_sample'], f['end_sample']) for f in side['interferer_frames']]
    lens = np.array([e - s for s, e in frames]) / FS * 1e3
    if lens.min() < 0.3 - 1e-6 or lens.max() > 3.0 + 1e-6:
        bad.append('frame lengths %.3f to %.3f ms are not within 0.3 to 3' % (lens.min(), lens.max()))
    runs = measured_frames(d)
    if len(runs) != len(frames):
        bad.append('the samples show %d frames and the sidecar %d' % (len(runs), len(frames)))
    else:
        worst = max(max(abs(a - s), abs(b - e)) for (a, b), (s, e) in zip(runs, frames))
        if worst > 3:
            bad.append('a frame\'s edge is %d samples from the sidecar\'s' % worst)
    duty_samples = float(np.sum(np.abs(d) > 3e-7)) / len(d)
    duty_sidecar = sum(e - s for s, e in frames) / len(d)
    if abs(duty_samples - 0.30) > 0.03:
        bad.append('duty from the samples is %.3f, wanted 0.30 +-0.03' % duty_samples)
    if abs(duty_sidecar - 0.30) > 0.03:
        bad.append('duty from the sidecar is %.3f' % duty_sidecar)
    if side['interferers'][-1]['duty'] is not None and abs(side['interferers'][-1]['duty'] - duty_sidecar) > 1e-9:
        bad.append('the sidecar\'s duty is not its frames\'')
    # outside the frames, exactly nothing
    off = np.ones(len(d), dtype=bool)
    for s, e in frames:
        off[s:e] = False
    if np.max(np.abs(d[off])) > 3e-7:
        bad.append('there is interferer outside the frames (%.2e)' % np.max(np.abs(d[off])))
    # raised-cosine edges: mean |d|^2 over the frames' first and last 400 samples, in boxes of 20
    inner = np.concatenate([d[s + 1000:e - 1000] for s, e in frames if e - s > 3000])
    p_on = float(np.mean(np.abs(inner) ** 2))
    rise = np.mean([np.abs(d[s:s + 400]) ** 2 for s, e in frames], axis=0)
    fall = np.mean([np.abs(d[e - 400:e][::-1]) ** 2 for s, e in frames], axis=0)
    got = (rise + fall) / 2 / p_on
    i = np.arange(400)
    want = (0.5 * (1 - np.cos(np.pi * (i + 0.5) / 200))) ** 2
    want[200:] = 1.0
    gb, wb = got.reshape(-1, 20).mean(axis=1), want.reshape(-1, 20).mean(axis=1)
    if np.max(np.abs(gb - wb)) > 0.15:
        bad.append('the edge is not a 5 us raised cosine (worst box %.2f off)' % np.max(np.abs(gb - wb)))
    return bad


# --- the truth per burst --------------------------------------------------------

def env2(frames, a, b):
    """Mean over samples a..b of the squared gate, written here again."""
    n = np.arange(a, b)
    tot = 0.0
    for s, e in frames:
        if e <= a or s >= b:
            continue
        lo, hi = max(s, a), min(e, b)
        m = n[lo - a:hi - a]
        r = np.minimum(m - s, e - 1 - m)
        gate = np.where(r >= 200, 1.0, 0.5 * (1 - np.cos(np.pi * (r + 0.5) / 200)))
        tot += float(np.sum(gate ** 2))
    return tot / (b - a)


def wifi_mhz_expected(channel):
    """MHz of the channel's 1 MHz band weighted by the squared response of the
    generator's FIR (gain 1 in the band), integrated here on a 2**16 grid, and not
    the generator's own table."""
    n = 1 << 16
    h = np.abs(np.fft.fft(g.wifi_taps(), n)) ** 2
    f = np.fft.fftfreq(n, 1 / FS) / 1e6
    c = channel + 2402 - 2441.0
    return float(np.sum(h[np.abs(f - c) < 0.5]) * (FS / n / 1e6))


def expected_truth(key, side, e):
    """(overlap, power dB) of a burst from the table and the sidecar's own frames,
    the Wi-Fi band integrated over the filter's actual response."""
    a, b = e['start_sample'], e['end_sample']
    frames = [(f['start_sample'], f['end_sample']) for f in side.get('interferer_frames', [])]
    hit, power = False, 0.0
    for ch, level in EXPECT[key]['tones']:
        if ch == e['channel']:
            hit, power = True, power + 10 ** (level / 10)
    if EXPECT[key]['wifi']:
        level, bursty = EXPECT[key]['wifi']
        mhz = wifi_mhz_expected(e['channel'])
        if mhz >= 1e-3 and (not bursty or any(s < b and en > a for s, en in frames)):
            hit, power = True, power + 10 ** (level / 10) * mhz * (env2(frames, a, b) if bursty else 1.0)
    return hit, (10 * np.log10(power) if hit else None)


def truth_failures(d, side, key):
    """Every burst's two keys: against the table and the sidecar's own frames
    (exactly), then against the power of ``file - clean`` in the channel's band
    during the burst, burst by burst and, for the Wi-Fi noise, summed over
    channel 50's bursts to 0.15 dB (more where the sum is too short to hold it)."""
    bad = []
    n_true = 0
    meas = exp = bt_sum = 0.0
    for k, e in enumerate(side['bursts']):
        hit, power = expected_truth(key, side, e)
        if e['interferer_overlap'] != hit:
            bad.append('burst %d (ch %d): overlap %s, from the table and frames %s' % (k, e['channel'], e['interferer_overlap'], hit))
            continue
        sp = e['interferer_power_in_band_db']
        if (sp is None) != (power is None) or (power is not None and abs(sp - power) > 5e-3):
            bad.append('burst %d (ch %d): power %s, from the table and frames %s' % (k, e['channel'], sp, power))
            continue
        # and from the samples
        off = (e['channel_mhz'] - 2441.0) * 1e6
        seg = d[e['start_sample']:e['end_sample']]
        p = band_power(seg, off - 0.5e6, off + 0.5e6) / NOISE
        if not hit:
            if p > 0.1:
                bad.append('burst %d (ch %d): stated no interferer, the samples have %.1f dB in its band'
                           % (k, e['channel'], 10 * np.log10(p)))
            continue
        n_true += 1
        cw = e['channel'] in [c for c, _ in EXPECT[key]['tones']]
        if not cw:
            meas += p * len(seg)
            exp += 10 ** (power / 10) * len(seg)
            g2 = env2([(f['start_sample'], f['end_sample']) for f in side.get('interferer_frames', [])],
                      e['start_sample'], e['end_sample']) if 'interferer_frames' in side else 1.0
            bt_sum += 0.55e6 * g2 * len(seg) / FS
        if sp < -5:
            continue
        if cw:
            tol = 0.1
        else:
            g2 = env2([(f['start_sample'], f['end_sample']) for f in side.get('interferer_frames', [])],
                      e['start_sample'], e['end_sample']) if 'interferer_frames' in side else 1.0
            tol = 0.1 + 4.34 * 3 / np.sqrt(max(0.55e6 * g2 * len(seg) / FS, 1.0))
        if p <= 0 or abs(10 * np.log10(p) - sp) > tol:
            bad.append('burst %d (ch %d): stated %.2f dB, the samples have %.2f dB (tol %.2f)'
                       % (k, e['channel'], sp, 10 * np.log10(max(p, 1e-30)), tol))
    if exp > 0:
        tol = max(0.15, 4.34 * 3.5 / np.sqrt(bt_sum))
        diff = 10 * np.log10(meas / exp)
        if abs(diff) > tol:
            bad.append('channel 50 summed over its bursts: the samples are %+.3f dB from the stated power '
                       '(tol %.2f)' % (diff, tol))
    return bad, n_true


# --- the bursts' own SNR ----------------------------------------------------------

def burst_snr_db(x, side, e):
    """The burst's power in 1 MHz over the noise in 1 MHz, from the samples:
    the core (8 symbols off each end) less the noise sample power from the
    burst-free lead-in, over the noise in 1 MHz."""
    sps = 40
    core = x[e['start_sample'] + 8 * sps:e['end_sample'] - 8 * sps]
    noise_sample = float(np.mean(np.abs(x[:LEAD] - x[:LEAD].mean()) ** 2))
    return 10 * np.log10((float(np.mean(np.abs(core) ** 2)) - noise_sample) / (noise_sample / 40))


def rendered(key, side, total, **kw):
    """The interferer alone, rendered by the generator's own function into zeros."""
    z = np.zeros(total, dtype=np.complex64)
    frames = [(f['start_sample'], f['end_sample']) for f in side.get('interferer_frames', [])]
    specs = [dict(kind='cw', channel=t['channel'], level_db=t['level_db']) if t['kind'] == 'cw'
             else dict(kind='wifi', level_db=t['level_db'], bursty=t['bursty']) for t in side['interferers']]
    g.add_interferers(z, specs, frames, side['seed'], FS, 2441.0, side['noise_block_samples'])
    return z


# --- the Bluetooth bursts, from the samples ---------------------------------------

RAMP = 80            # samples: the burst's raised-cosine amplitude ramp, 2 us at 40 MS/s, before its first bit
START_TOL = 25.0     # samples, one burst's start (the fit scatters by about 6 samples, 1 sigma, at 20 dB in 1 MHz)
START_MEAN_TOL = 3.0     # samples, the mean over the bursts
CARRIER_TOL_HZ = 30e3


def carrier_of(x, e):
    """The burst's carrier in Hz from the centre, from the samples alone: the peak of its smoothed spectrum
    names the channel (1 MHz apart), then the slope of the phase of the signal demodulated to that channel's
    centre, through a one-symbol boxcar, over the burst's core gives the offset from it."""
    a, b = e['start_sample'] + 8 * 40, e['end_sample'] - 8 * 40
    core = x[a:b].astype(np.complex128)
    n = len(core)
    size = 1 << 17
    spec = np.abs(np.fft.fft(core * np.hanning(n), size)) ** 2
    spec = np.convolve(np.concatenate([spec, spec[:1300]]), np.ones(1300) / 1300, 'valid')[:size]
    f = np.fft.fftfreq(size, 1 / FS)
    window = np.abs(f) < 15e6
    f_peak = f[window][int(np.argmax(spec[window]))] + 0.0
    channel_hz = round(f_peak / 1e6) * 1e6                       # a channel is 1 MHz
    y = core * np.exp(-2j * np.pi * channel_hz / FS * np.arange(n))
    z = np.convolve(y, np.ones(40) / 40, 'valid')
    phase = np.unwrap(np.angle(z))
    slope = np.polyfit(np.arange(len(phase)), phase, 1)[0]
    return channel_hz + slope * FS / (2 * np.pi)


def start_shift(x, e, noise_power):
    """How many samples later than ``start_sample + timing_frac`` the burst's envelope rises, from the samples
    alone: the power of the 400 samples about the start, less the noise, fitted by the raised-cosine power ramp
    of 80 samples that ends at the first bit (the burst's documented shape), over shifts of -60..+60."""
    a0 = e['start_sample']
    lo = a0 - 200
    n = np.arange(lo, lo + 400)
    p = np.abs(x[lo:lo + 400].astype(np.complex128)) ** 2
    core = x[a0 + 8 * 40:e['end_sample'] - 8 * 40].astype(np.complex128)
    sig = float(np.mean(np.abs(core) ** 2)) - noise_power
    best = None
    for shift in np.arange(-60, 60.01, 0.5):
        u = n - (a0 + e['timing_frac'] + shift) + RAMP
        env = np.where(u <= 0, 0.0, np.where(u >= RAMP, 1.0, 0.5 - 0.5 * np.cos(np.pi * np.clip(u, 0, RAMP) / RAMP)))
        cost = float(np.sum((p - noise_power - sig * env ** 2) ** 2))
        if best is None or cost < best[0]:
            best = (cost, float(shift))
    return best[1]


def burst_sample_failures(x, side):
    """Every burst of the file against its own samples: the carrier at ``channel_mhz`` (within 30 kHz, and no
    other channel), and the start where ``start_sample + timing_frac`` says (within 25 samples a burst and 3
    samples on average)."""
    bad = []
    noise_power = float(np.mean(np.abs(x[:LEAD] - x[:LEAD].mean()) ** 2))
    shifts = []
    worst_hz = 0.0
    for i, e in enumerate(side['bursts']):
        want_hz = (e['channel_mhz'] - side['center_mhz']) * 1e6
        got_hz = carrier_of(x, e)
        worst_hz = max(worst_hz, abs(got_hz - want_hz))
        if abs(got_hz - want_hz) > CARRIER_TOL_HZ:
            bad.append('burst %d is at %+.3f MHz in the samples, %+.3f in the sidecar (channel %d)'
                       % (i, got_hz / 1e6, want_hz / 1e6, e['channel']))
            if len(bad) > 3:
                break
        shifts.append(start_shift(x, e, noise_power))
    if shifts:
        if max(abs(v) for v in shifts) > START_TOL:
            bad.append('a burst starts %.1f samples from start_sample + timing_frac' % max(shifts, key=abs))
        if abs(float(np.mean(shifts))) > START_MEAN_TOL:
            bad.append('the bursts start %.2f samples from start_sample + timing_frac on average' % np.mean(shifts))
    return bad


def sidecar_key_failures(side):
    """The keys every sidecar has, at the top and on each burst, and the ones that say which map a burst hopped on."""
    top = ['generator', 'generator_commit', 'lap', 'uap', 'clk', 'clk_convention', 'hopping', 'hop_channels',
           'sample_rate', 'center_mhz', 'snr_db', 'snr_bw_hz', 'noise_1mhz', 'modulation', 'slot_samples',
           'per_burst_keys', 'start_sample_meaning', 'air_bits_omitted', 'interferer_name', 'interferers',
           'interferer_note', 'n_bursts_on_centre_channel', 'n_bursts_overlapped', 'afh_map', 'afh_instant',
           'afh_map_count', 'afh_maps', 'afh_instant_meaning', 'hop_kernel', 'address_for_hop', 'clock_lock_note',
           'seed', 'noise_block_samples', 'bursts']
    per = ['start_sample', 'end_sample', 'timing_frac', 'symbol_phase', 'clk', 'ptype', 'channel', 'channel_mhz',
           'snr_db', 'afh_map_index', 'interferer_overlap', 'interferer_power_in_band_db']
    bad = ['top-level key %s is missing' % k for k in top if k not in side]
    bad += ['per-burst key %s is missing from %d bursts' % (k, sum(k not in e for e in side['bursts']))
            for k in per if any(k not in e for e in side['bursts'])]
    maps = side.get('afh_maps') or []
    if not bad:
        if side['afh_map_count'] != len(maps) or not all(0 <= e['afh_map_index'] < len(maps) for e in side['bursts']):
            bad.append('afh_map_index of a burst names no map of afh_maps (%d)' % len(maps))
        elif any(e['channel'] not in maps[e['afh_map_index']]['channels'] for e in side['bursts']):
            bad.append('a burst is on a channel that is not in the map its afh_map_index names')
        if side['per_burst_keys'] != sorted(side['bursts'][0]):
            bad.append('per_burst_keys is not the keys of a burst')
    return bad


def truth_error_failures():
    """burst_truth's refusals: a ValueError that names the burst and the values, never a bare math domain error."""
    bad = []
    wifi = [dict(kind='wifi', level_db=20.0, bursty=False)]
    cases = [('an empty burst', wifi, 100, 100, 50, {50: 0.54}),
             ('a channel missing from the Wi-Fi table', wifi, 0, 100, 51, {50: 0.54}),
             ('a non-finite level', [dict(kind='cw', channel=50, level_db=float('nan'))], 0, 100, 50, {50: 0.54}),
             ('an infinite level', [dict(kind='cw', channel=50, level_db=float('inf'))], 0, 100, 50, {50: 0.54})]
    for name, specs, a, b, ch, table in cases:
        try:
            g.burst_truth(specs, [], a, b, ch, table)
            bad.append('%s was not refused' % name)
        except ValueError as e:
            if 'math domain' in str(e) or '[%d, %d)' % (a, b) not in str(e) or 'channel %d' % ch not in str(e):
                bad.append('%s: the error does not name the burst: %s' % (name, e))
        except Exception as e:                                 # noqa: BLE001
            bad.append('%s raised %s, not a ValueError' % (name, type(e).__name__))
    return bad


# --- the tests --------------------------------------------------------------------

KEYS = [k for k in g.INTERFERERS if k != 'clean']


def test_pairing_and_clean():
    print('the pairing: every file is the clean file plus its interferer alone')
    clean, side0 = build('clean')
    check(np.array_equal(clean, build('clean')[0]) and not side0['interferers']
          and side0['n_bursts_overlapped'] == 0, 'the clean file has no interferer')
    check_all(clean_tone_failures(clean, side0), 'no tone in the clean file')
    for key in KEYS:
        iq, cl, side, d = pair(key)
        check_all(pairing_failures(iq, cl, side, d, side0, key), '%s is the clean file plus its interferer' % key)
    # the clean noise and bursts are the same in every file: the same seed
    check(all(build(k)[1]['seed'] == SEED for k in KEYS), 'one seed, %d, for every file' % SEED)
    # a file made from another seed is not paired with the clean one
    other, _ = build('cw49_p20', seed=SEED + 1)
    iq, cl, side, _ = pair('cw49_p20')
    d = other.astype(np.complex128) - cl.astype(np.complex128)
    check(bool(pairing_failures(other, cl, side, d, side0, 'cw49_p20')), 'a file from another seed is not paired with the clean one')


def test_tone():
    print('the tone: frequency and power')
    for key in ('cw49_p10', 'cw49_p20', 'cw43_p10', 'cw43_p20', 'both_p20'):
        iq, cl, side, d = pair(key)
        check_all(tone_failures(iq, cl, side, d, key), '%s: tone frequency and level' % key)
    iq, cl, side, d = pair('cw49_p20')
    t = side['interferers'][0]
    check(t['duty'] == 1.0 and t['frames'] is None, 'the tone is always on')
    # a tone is on for the whole file: the same level at the end as at the start
    a = abs(tone_fit(d, 0, 2 ** 18, 10e6)[0])
    b = abs(tone_fit(d, len(d) - 2 ** 18, len(d), 10e6)[0])
    check(abs(a / b - 1) < 1e-6, 'the tone is constant over the file')


def test_wifi():
    print('the Wi-Fi noise: spectrum, level, seams')
    iq, cl, side, d = pair('wifi_const_p20')
    check_all(wifi_psd_failures(d, side), 'wifi_const: flat over +11..+20 MHz, 20 dB, 40 dB down outside')
    check_all(seam_failures(d, side), 'wifi_const: no seam at %d-sample blocks (%d seams)'
              % (side['noise_block_samples'], len(d) // side['noise_block_samples']))
    check(side['interferers'][0]['duty'] == 1.0, 'the constant noise is always on')
    # it is Gaussian: kurtosis of a component of the complex noise is 3
    m = d[:2 ** 20].real
    k = float(np.mean(m ** 4) / np.mean(m ** 2) ** 2)
    check(abs(k - 3) < 0.1, 'it is Gaussian (kurtosis %.2f)' % k)
    # the disclosed Nyquist skirt is really there: the filter's upper edge is +20.05 MHz, so about 1.2 dB below the
    # plateau, at -20..-19.95 MHz, outside the declared band
    m = (len(d) // 4096) * 4096
    f, db = welch_db(d[:m].reshape(-1, 4096), m // 4096)
    skirt = (f >= -20.0) & (f <= -19.96)
    lvl = 10 * np.log10(np.mean(10 ** (db[skirt] / 10)))
    check(15.0 < lvl < 21.0 and side['interferers'][0]['nyquist_skirt'] == SKIRT_SNAPSHOT
          and (f[skirt] < -19.9).all(),
          'the wrapped Nyquist skirt at -20..-19.95 MHz is %.1f dB over the floor per MHz (plateau 20), outside the declared '
          'band, and the sidecar says so' % lvl)


def test_frames():
    print('the frames of the bursty noise (a longer run)')
    iq, cl, side, d = pair('wifi_bursty_p20', bursts=160)
    check_all(frame_failures(d, side), 'frames: lengths, duty, edges and schedule against the samples (%d frames)'
              % len(side['interferer_frames']))
    check_all(wifi_psd_failures(d, side), 'wifi_bursty: the same PSD while on')
    bad, n = truth_failures(d, side, 'wifi_bursty_p20')
    check_all(bad, 'wifi_bursty (160 bursts): per-burst truth (%d bursts hit)' % n)
    again, _ = g.synthesise_interf('wifi_bursty_p20', bursts=160, channels=MIX, seed=SEED, block_samples=BLOCK)
    check(np.array_equal(again, iq), 'the schedule is seeded: the same file again')


def test_truth():
    print('per-burst truth in the small files: from the sidecar and from the samples')
    for key in KEYS:
        iq, cl, side, d = pair(key)
        bad, n = truth_failures(d, side, key)
        chans = {e['channel'] for e in side['bursts'] if e['interferer_overlap']}
        check_all(bad, '%s: %d of %d bursts hit, on channels %s' % (key, n, len(side['bursts']), sorted(chans)))
        check(side['n_bursts_overlapped'] == n, '%s: n_bursts_overlapped is %d' % (key, n))
    # which channels each kind reaches
    for key, want in (('cw49_p20', {49}), ('cw43_p10', {43}), ('wifi_const_p20', {50}),
                      ('both_p20', {43, 49, 50}), ('wifi_bursty_p20', {50})):
        side = build(key)[1]
        got = {e['channel'] for e in side['bursts'] if e['interferer_overlap']}
        on = {e['channel'] for e in side['bursts']} & want
        check(got <= want and (key.endswith('bursty_p20') or got == on) and got,
              '%s reaches channels %s' % (key, sorted(got)))
    # channel 50 under the constant noise: about half its band plus the filter's skirt
    side = build('wifi_const_p20')[1]
    p = [e['interferer_power_in_band_db'] for e in side['bursts'] if e['channel'] == 50]
    want = 20 + 10 * np.log10(wifi_mhz_expected(50))
    check(p and all(abs(x - want) < 5e-3 for x in p) and 17.0 < want < 17.6,
          'channel 50 under the constant noise is %.3f dB (the actual response over its band; a brick wall says 16.99)' % want)
    check(abs(wifi_mhz_expected(51) - 1.0) < 1e-3 and wifi_mhz_expected(49) < 1e-6,
          'a channel inside the band is 1 MHz of response and one just below it is none')
    # the default map: channel 50 is the only one of the map 31-50 the Wi-Fi band touches
    table = g.wifi_overlap_table(range(31, 51))
    check([c for c in range(31, 51) if table[c] >= g.WIFI_REACH_MHZ] == [50]
          and abs(table[50] - wifi_mhz_expected(50)) < 2e-3,
          'in the map 31-50 the Wi-Fi noise touches channel 50 only, %.4f MHz of it' % table[50])


def test_burst_samples():
    print('the Bluetooth bursts from the samples: carrier and start (clean file, 60 bursts over %d-sample blocks)' % BLOCK)
    clean, side0 = build('clean')
    check_all(burst_sample_failures(clean, side0),
              'every burst\'s carrier is its channel_mhz (within 30 kHz) and it starts at start_sample + timing_frac (%d bursts)'
              % len(side0['bursts']))
    seams = sum(1 for e in side0['bursts'] if e['start_sample'] // BLOCK != (e['end_sample'] - 1) // BLOCK)
    check(seams > 0, '%d of the bursts cross a block boundary' % seams)
    check_all(truth_error_failures(), 'burst_truth refuses an empty burst, an unknown channel and a non-finite power '
              'with a ValueError that names the burst')


def test_table():
    print('each named file against the table written from the task')
    check(sorted(EXPECT) == sorted(g.INTERFERERS), 'the generator has the eight names of the table')
    for key in EXPECT:
        check_all(table_failures(key, build(key)[1]), '%s: interferers, frames, note and reach are the table\'s' % key)


def test_snr():
    print('the bursts are still 20 dB with the interferer taken away')
    clean, side0 = build('clean')
    s0 = [burst_snr_db(clean.astype(np.complex128), side0, e) for e in side0['bursts'][:12]]
    check(abs(np.mean(s0) - 20) < 0.3, 'clean file: mean burst SNR %.2f dB' % np.mean(s0))
    for key in ('cw49_p20', 'wifi_bursty_p20', 'both_p20'):
        iq, side = build(key)
        x = iq.astype(np.complex128) - rendered(key, side, len(iq)).astype(np.complex128)
        s = [burst_snr_db(x, side, e) for e in side['bursts'][:12]]
        check(abs(np.mean(s) - 20) < 0.3 and max(abs(np.array(s) - 20)) < 1.0,
              '%s: mean burst SNR %.2f dB with the interferer subtracted' % (key, np.mean(s)))


def set_failures(rows):
    return ['%s has seed %s and %s bursts' % (n, s['seed'], s['bursts']) for n, s in rows
            if s['seed'] != 6101 or s['bursts'] != 800]


def test_sidecar_and_set():
    print('sidecar, determinism, the set and the command line')
    iq, side = build('both_p20')
    b = side['bursts']
    check(side['air_bits_omitted'] and all('air_bits' not in e for e in b), 'air_bits left out')
    check(side['timing_frac'] is None and side['symbol_phase'] is None and side['snr_db'] == 20.0
          and side['sample_rate'] == 40e6 and side['center_mhz'] == 2441.0 and side['noise_1mhz'] == NOISE
          and side['snr_bw_hz'] == 1e6, 'top-level nulls, snr_db 20.0, rate, centre and floor')
    check(side['per_burst_keys'] == sorted(b[0]) and all(sorted(e) == side['per_burst_keys'] for e in b),
          'per_burst_keys is every per-burst key')
    check_all(sidecar_key_failures(side), 'the top-level and per-burst keys are there, afh_map_index among them, and each '
              'burst\'s afh_map_index names a map that holds its channel')
    check([e['symbol_phase'] for e in b[:6]] == [0, 20, 0, 20, 0, 20] and all(e['ptype'] == 'DH5' for e in b),
          'DH5 bursts, symbol phases alternate 0 and 20')
    fr = [e['start_sample'] % 40 for e in b]
    check(fr == [e['symbol_phase'] for e in b], 'symbol_phase is start_sample % 40')
    gap = np.diff([e['start_sample'] for e in b])
    check(set(gap) <= {150000 - 20, 150000 + 20}, 'one burst every 6 slots (150000 samples, +-20 for the phase)')
    check(all(0 <= e['timing_frac'] < 1 for e in b), 'timing fractions in [0, 1)')
    check(side['n_bursts_on_centre_channel'] == sum(e['channel'] == 39 for e in b), 'n_bursts_on_centre_channel counted')
    check('interferer_note' in side and 'interferer_frames' in side
          and len(side['interferers']) == 3, 'both_p20: two tones and the noise, with the note and the frames')
    # the default map and 800 bursts are what the set means
    full = g.synthesise_interf('clean', bursts=3, block_samples=BLOCK)[1]
    check(full['afh_map'] == list(range(31, 51)), 'the default map is 31-50')
    # determinism
    again, _ = g.synthesise_interf('both_p20', bursts=60, channels=MIX, seed=SEED, block_samples=BLOCK)
    check(hashlib.md5(again.tobytes()).digest() == hashlib.md5(iq.tobytes()).digest(), 'the same arguments give the same bytes')
    # the set
    names = [n for n, _ in g.INTERF_SET]
    want = ['hop20_dh5_int_' + s for s in ('clean', 'cw49_p10', 'cw49_p20', 'cw43_p10', 'cw43_p20',
                                           'wifi_const_p20', 'wifi_bursty_p20', 'both_p20')]
    check(sorted(names) == sorted(want) and len(names) == 8, '--set interf is exactly the eight named files')
    check_all(set_failures(g.INTERF_SET), 'one seed, 6101, and 800 bursts in every row')
    none = os.path.join(SCRATCH, 'none')
    r = subprocess.run([PY, '-B', SCRIPT, '--set', 'nonesuch', '--out', none],
                       capture_output=True, text=True)
    check(r.returncode != 0 and not os.path.exists(none), 'an unknown set exits non-zero and writes nothing')
    r = subprocess.run([PY, '-B', SCRIPT, '--set', 'interf', '--bursts', '5', '--out', none],
                       capture_output=True, text=True)
    check(r.returncode != 0, '--set takes no per-file option')
    with tempfile.TemporaryDirectory(dir=SCRATCH) as out:
        r = subprocess.run([PY, '-B', SCRIPT, 'small', '--interferer', 'cw43_p10', '--bursts', '6',
                            '--block-samples', '131072', '--out', out], capture_output=True, text=True)
        path = os.path.join(out, 'synth_small.cf32')
        ok = r.returncode == 0 and os.path.exists(path)
        check(ok, 'a single run writes synth_small.cf32 and .json')
        if ok:
            direct, dside = g.synthesise_interf('cw43_p10', bursts=6, block_samples=131072)
            got = np.fromfile(path, dtype='<c8')
            check(np.array_equal(got, direct), 'the file written is the generator\'s samples (same code path)')
            with open(os.path.join(out, 'synth_small.json')) as f:
                check(json.load(f) == json.loads(json.dumps(dside)), 'and its sidecar')


@contextlib.contextmanager
def patched(*patches):
    with contextlib.ExitStack() as st:
        for p in patches:
            st.enter_context(p)
        yield


def caught(name, by, problems):
    check(bool(problems), 'mutant "%s" is caught by %s%s' % (name, by, '' if not problems else ' (%s)' % problems[0][:70]))


def test_mutants():
    print('mutants of the generator, each caught')
    kw = dict(bursts=20)
    # 1. a tone on the wrong channel: 1 MHz off, with the sidecar still saying the right one
    orig = g.tone_offset_hz
    with patched(mock.patch.object(g, 'tone_offset_hz', lambda ch, c: orig(ch, c) + 1e6)):
        iq, side = g.synthesise_interf('cw49_p20', channels=MIX, block_samples=BLOCK, **kw)
    cl, _ = build('clean', **kw)
    caught('tone at the wrong channel', 'the tone check', tone_failures(iq, cl, side, iq.astype(np.complex128) - cl, 'cw49_p20'))
    # 2. PSD 3 dB low
    orig = g.wifi_white_sigma
    with patched(mock.patch.object(g, 'wifi_white_sigma', lambda lv, fs: orig(lv - 3, fs))):
        iq, side = g.synthesise_interf('wifi_const_p20', channels=MIX, block_samples=BLOCK, **kw)
    d = iq.astype(np.complex128) - cl
    caught('PSD 3 dB low', 'the Wi-Fi level', wifi_psd_failures(d, side))
    # 3. band +10..+19
    with patched(mock.patch.object(g, 'WIFI_BAND_MHZ', (10.0, 19.0))):
        iq, side = g.synthesise_interf('wifi_const_p20', channels=MIX, block_samples=BLOCK, **kw)
    caught('band +10..+19', 'the Wi-Fi band', wifi_psd_failures(iq.astype(np.complex128) - cl, side))
    # 4. frames not gated
    with patched(mock.patch.object(g, 'gate_block', lambda frames, lo, hi: np.ones(hi - lo))):
        iq, side = g.synthesise_interf('wifi_bursty_p20', channels=MIX, block_samples=BLOCK, **kw)
    caught('frames not gated', 'the frame check', frame_failures(iq.astype(np.complex128) - cl, side))
    # 5. duty 0.5
    with patched(mock.patch.object(g, 'DUTY', 0.5)):
        iq, side = g.synthesise_interf('wifi_bursty_p20', channels=MIX, block_samples=BLOCK, **kw)
    caught('duty 0.5', 'the frame check', frame_failures(iq.astype(np.complex128) - cl, side))
    # 6. the interferer applied to the clean file too
    spec = [dict(kind='cw', channel=49, level_db=20.0)]
    with patched(mock.patch.dict(g.INTERFERERS, {'clean': spec})):
        bad_clean, bside = g.synthesise_interf('clean', channels=MIX, block_samples=BLOCK, **kw)
    iq, side = build('cw49_p20', **kw)
    problems = clean_tone_failures(bad_clean, bside) + tone_failures(
        iq, bad_clean, side, iq.astype(np.complex128) - bad_clean.astype(np.complex128), 'cw49_p20')
    caught('interferer on the clean file too', 'the clean-file and tone checks', problems)
    # 7. a seam in the block filter: each block filtered from silence
    def seamy(self, w):
        keep = len(self.taps) - 1
        x = np.concatenate([np.zeros(keep, dtype=np.complex128), w])
        size = 1 << int(np.ceil(np.log2(len(x) + len(self.taps))))
        return np.fft.ifft(np.fft.fft(x, size) * np.fft.fft(self.taps, size))[keep:keep + len(w)]
    with patched(mock.patch.object(g.WifiNoise, 'filter', seamy)):
        iq, side = g.synthesise_interf('wifi_const_p20', channels=MIX, block_samples=BLOCK, **kw)
    caught('seam in the block filter', 'the seam check', seam_failures(iq.astype(np.complex128) - cl, side))
    # 8 and 9 want bursts that the noise may or may not meet: all on channel 50
    kw50 = dict(bursts=30, channels=[50], block_samples=BLOCK)
    cl50, _ = build('clean', bursts=30, channels=[50])
    gain2 = g.mean_gain_squared
    with patched(mock.patch.object(g, 'frames_hit', lambda *a: True),
                 mock.patch.object(g, 'mean_gain_squared', lambda *a: max(gain2(*a), 0.02))):
        iq, side = g.synthesise_interf('wifi_bursty_p20', **kw50)
    caught('overlap ignoring time (always on)', 'the per-burst truth',
           truth_failures(iq.astype(np.complex128) - cl50.astype(np.complex128), side, 'wifi_bursty_p20')[0])
    with patched(mock.patch.object(g, 'mean_gain_squared', lambda *a: 1.0)):
        iq, side = g.synthesise_interf('wifi_bursty_p20', **kw50)
    caught('power-in-band ignoring the gating', 'the per-burst truth',
           truth_failures(iq.astype(np.complex128) - cl50.astype(np.complex128), side, 'wifi_bursty_p20')[0])
    # 10. a different seed for one file
    other, side = g.synthesise_interf('cw49_p20', channels=MIX, block_samples=BLOCK, seed=SEED + 1, **kw)
    cl_, side0 = build('clean', **kw)
    caught('a different seed for one file', 'the pairing check',
           pairing_failures(other, cl_, side, other.astype(np.complex128) - cl_.astype(np.complex128), side0, 'cw49_p20'))
    # 11 and 12. the named file moved, samples and sidecar together: ch 44, or 3 dB low
    for name, spec in (('cw43_p20 on channel 44', dict(kind='cw', channel=44, level_db=20.0)),
                       ('cw43_p20 3 dB low', dict(kind='cw', channel=43, level_db=17.0))):
        with patched(mock.patch.dict(g.INTERFERERS, {'cw43_p20': [spec]})):
            iq, side = g.synthesise_interf('cw43_p20', channels=MIX, block_samples=BLOCK, **kw)
        d = iq.astype(np.complex128) - cl
        caught(name + ' (samples and sidecar agree)', 'the tone check and the table',
               tone_failures(iq, cl, side, d, 'cw43_p20') + table_failures('cw43_p20', side))
    # 13. a note that lies
    with patched(mock.patch.object(g, 'INTERFERER_NOTE', g.INTERFERER_NOTE.replace(
            'a cw tone only on its own channel', 'a cw tone reaching every channel'))):
        side = g.synthesise_interf('cw43_p20', bursts=3, channels=MIX, block_samples=BLOCK)[1]
    caught('a false interferer_note', 'the table', table_failures('cw43_p20', side))
    # 14. one file of the set from another seed
    rows = [(n, dict(s, seed=6102) if n.endswith('cw49_p20') else s) for n, s in g.INTERF_SET]
    caught('a different seed for one file of the set', 'the set check', set_failures(rows))
    # 15. the Wi-Fi power truth from a brick wall: channel 50 at half a MHz
    with patched(mock.patch.object(g, 'wifi_overlap_table', lambda ch, fs=FS, c=2441.0: {x: 0.5 if x == 50 else 0.0 for x in ch})):
        iq, side = g.synthesise_interf('wifi_const_p20', channels=[50], bursts=30, block_samples=BLOCK)
    caught('Wi-Fi truth from a brick wall', 'the per-burst truth',
           truth_failures(iq.astype(np.complex128) - cl50.astype(np.complex128), side, 'wifi_const_p20')[0])


def test_mutants_bursts():
    print('mutants of the Bluetooth bursts, with the sidecar left saying the right thing')
    kw = dict(bursts=20, channels=MIX, block_samples=BLOCK)
    real_gfsk = g.br.gfsk

    def carrier_off(bits, fs, **k):
        burst, lead = real_gfsk(bits, fs, **k)
        return (burst * np.exp(2j * np.pi * 1e6 / fs * np.arange(len(burst)))).astype(np.complex64), lead

    def start_late(bits, fs, **k):
        burst, lead = real_gfsk(bits, fs, **k)
        return burst, lead - 40                  # the burst is placed 40 samples later; start_sample is unchanged

    for name, wrap in (('every Bluetooth burst +1 MHz in the samples (sidecar unchanged)', carrier_off),
                       ('every Bluetooth burst 40 samples late (sidecar unchanged)', start_late)):
        with patched(mock.patch.object(g.br, 'gfsk', wrap)):
            iq, side = g.synthesise_interf('clean', **kw)
        caught(name, 'the burst carrier and start check', burst_sample_failures(iq, side))
    # a note whose prose is changed while every phrase the table looks for stays
    with patched(mock.patch.object(g, 'INTERFERER_NOTE', g.INTERFERER_NOTE.replace('0.001 MHz', '0.5 MHz'))):
        side = g.synthesise_interf('wifi_const_p20', bursts=3, channels=MIX, block_samples=BLOCK)[1]
    caught('a note with its prose changed and its phrases kept', 'the whole-note comparison',
           table_failures('wifi_const_p20', side))
    with patched(mock.patch.object(g, 'NYQUIST_SKIRT', 'the filter is clean')):
        side = g.synthesise_interf('wifi_const_p20', bursts=3, channels=MIX, block_samples=BLOCK)[1]
    caught('no Nyquist skirt in the sidecar', 'the table', table_failures('wifi_const_p20', side))
    # keys: dropped or wrong, on a copy of a real sidecar
    side = json.loads(json.dumps(build('both_p20')[1]))
    check(not sidecar_key_failures(side), 'the real both_p20 sidecar has every required key')
    gone = json.loads(json.dumps(side))
    for e in gone['bursts']:
        del e['afh_map_index']
    caught('afh_map_index dropped from the bursts', 'the key check', sidecar_key_failures(gone))
    wrong = json.loads(json.dumps(side))
    wrong['bursts'][3]['afh_map_index'] = 1
    caught('afh_map_index naming no map', 'the key check', sidecar_key_failures(wrong))
    for k in ('afh_maps', 'afh_map_count', 'hop_kernel', 'clock_lock_note', 'interferer_note', 'noise_block_samples'):
        lost = json.loads(json.dumps(side))
        del lost[k]
        caught('%s dropped from the sidecar' % k, 'the key check', sidecar_key_failures(lost))


def main():
    for t in (test_pairing_and_clean, test_tone, test_wifi, test_frames, test_truth, test_table, test_snr,
              test_burst_samples, test_sidecar_and_set, test_mutants, test_mutants_bursts):
        t()
    print('RESULT: %s' % ('PASS' if not failures else 'FAIL (%d)' % len(failures)))
    for f in failures:
        print('  FAIL: ' + f)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
