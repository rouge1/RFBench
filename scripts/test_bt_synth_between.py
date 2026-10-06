#!/usr/bin/env python3
"""Hold the between-channels tone writer to what its samples say.

    python scripts/test_bt_synth_between.py

``scripts/bt_synth_between.py`` writes the clean interferer train plus one tone
at +4.25, +4.5 or +4.75 MHz from the centre, with per-burst truth of how far the
tone is from each burst's channel. Checked from small runs (60 bursts, a small
block so there are many block seams), against the samples and the definitions,
never against the generator's own bookkeeping:

* the clean path reproduces ``bt_synth_interf``'s clean small run exactly;
* the pairing: file - clean - tone model is below 1e-6, the model fitted on a
  burst-free stretch; its frequency to 5 mHz and its power, from the burst-free
  stretches of the file itself, the stated dB over the noise in 1 MHz to 0.1 dB;
* per burst, offset / overlap / edge / power from an ``EXPECT`` table written
  here from the definition (tone minus channel, |offset| <= 0.5 inclusive), and for
  all five channels 41-45, and ``n_samples`` equal to the file's size;
* the delivered clean file's first 2**22 samples, when it is present;
* mutants, each of which a check above must catch.
"""
import contextlib
import json
import os
import sys
import tempfile
from unittest import mock

os.environ.setdefault('OMP_NUM_THREADS', '4')
import numpy as np  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import bt_synth, bt_synth_interf as gi, bt_synth_between as bb  # noqa: E402

FS = 40e6
NOISE = bt_synth.AMPLITUDE ** 2 / 100
BLOCK = 2 ** 17
CHANNELS = [39, 41, 42, 43, 44, 45]     # the small runs hop over these: the tone's neighbours and the centre
BURSTS = 80
LEAD = 60000                            # burst-free samples before the first burst
DELIVERED = '/media/user/4TB/sdr-synth-tmp/interf/synth_hop20_dh5_int_clean.cf32'
failures = []


def check(ok, what):
    print('  %s %s' % ('ok  ' if ok else 'FAIL', what))
    if not ok:
        failures.append(what)


def check_all(problems, what):
    check(not problems, what + ('' if not problems else ': ' + '; '.join(problems[:4])))


# From the definition, written by hand: tone offset (MHz from 2441.0) -> channel (2402 + c MHz) ->
# (signed offset, overlap, at_edge). Offset = tone - channel; overlap = |offset| <= 0.5; edge = |offset| == 0.5.
EXPECT = {
    4.25: {41: (2.25, False, False), 42: (1.25, False, False), 43: (0.25, True, False),
           44: (-0.75, False, False), 45: (-1.75, False, False)},
    4.5:  {41: (2.5, False, False), 42: (1.5, False, False), 43: (0.5, True, True),
           44: (-0.5, True, True), 45: (-1.5, False, False)},
    # an edge tone nudged 1e-6 MHz either way: no longer on the edge, and on one side not even in the band
    4.500001: {41: (2.500001, False, False), 42: (1.500001, False, False), 43: (0.500001, False, False),
               44: (-0.499999, True, False), 45: (-1.499999, False, False)},
    4.499999: {41: (2.499999, False, False), 42: (1.499999, False, False), 43: (0.499999, True, False),
               44: (-0.500001, False, False), 45: (-1.500001, False, False)},
    4.75: {41: (2.75, False, False), 42: (1.75, False, False), 43: (0.75, False, False),
           44: (-0.25, True, False), 45: (-1.25, False, False)},
}

CACHE = {}


def build(offset=None, power=None, seed=None, bursts=BURSTS, center=2441.0):
    key = (offset, power, seed, bursts, center)
    if key not in CACHE:
        CACHE[key] = bb.synthesise_between(offset, power, seed=seed, bursts=bursts, channels=CHANNELS,
                                           block_samples=BLOCK, center_mhz=center)
    return CACHE[key]


def quiet_segments(side, n=20000):
    segs = [(i, i + n) for i in range(0, LEAD - n + 1, n)]
    for e0, e1 in zip(side['bursts'], side['bursts'][1:]):
        a = e0['end_sample'] + 400
        if e1['start_sample'] - 400 - a >= n:
            segs.append((a, a + n))
    return segs


def tone_level_db(x, segs, f_hz):
    """The tone near ``f_hz`` over the noise in the 1 MHz about it, in dB, from the Hann-windowed
    FFTs of the burst-free stretches ``segs``: the 7 main-lobe bins less their noise, over the
    mean bin of the 1 MHz about it (main lobe's neighbourhood left out) scaled to 1 MHz."""
    n = segs[0][1] - segs[0][0]
    w = np.hanning(n)
    s2 = float(np.sum(w ** 2))
    p = np.mean([np.abs(np.fft.fft(x[a:b].astype(np.complex128) * w)) ** 2 for a, b in segs], axis=0)
    f = np.fft.fftfreq(n, 1 / FS)
    k0 = int(np.argmin(np.abs(f - f_hz)))
    dk = np.abs(((np.arange(n) - k0 + n // 2) % n) - n // 2)
    near = (np.abs(f - f_hz) < 0.5e6) & (dk > 8)
    mean_bin = float(p[near].mean())
    tone = (float(p[dk <= 3].sum()) - 7 * mean_bin) / (n * s2)
    noise_1mhz = mean_bin * 1e6 / (s2 * FS)
    return 10 * np.log10(max(tone, 1e-30) / noise_1mhz), noise_1mhz


def tone_fit(x, a, b, offset_hz):
    """Complex amplitude and frequency error (Hz) of the tone near ``offset_hz`` in ``x[a:b]``
    (file sample numbers), mixed down and averaged in boxes of 400 samples."""
    n = np.arange(a, b)
    cycles = offset_hz / FS * n
    y = x[a:b] * np.exp(-2j * np.pi * (cycles - np.floor(cycles)))
    m = (len(y) // 400) * 400
    z = y[:m].reshape(-1, 400).mean(axis=1)
    slope = np.polyfit(np.arange(len(z)) * 400, np.unwrap(np.angle(z)), 1)[0]
    return complex(z.mean()), float(slope * FS / (2 * np.pi))


def tone_model(d, offset_hz, a, b):
    n = np.arange(len(d))
    c, _ = tone_fit(d, a, b, offset_hz)
    cycles = offset_hz / FS * n
    return c * np.exp(2j * np.pi * (cycles - np.floor(cycles)))


def pairing_and_tone_failures(offset, power, iq, clean, side, center=2441.0):
    bad = []
    hz = (center + offset - center) * 1e6
    d = iq.astype(np.complex128) - clean.astype(np.complex128)
    a, b = 0, min(len(d), 2 ** 20)
    c, df = tone_fit(d, a, b, hz)
    if abs(df) > 0.005:
        bad.append('tone %.4f Hz off %.0f Hz' % (df, hz))
    want = np.sqrt(NOISE * 10 ** (power / 10))
    if abs(20 * np.log10(abs(c) / want)) > 0.02:
        bad.append('tone amplitude %.5f, wanted %.5f' % (abs(c), want))
    resid = np.abs(d - tone_model(d, hz, a, b)).max()
    if resid > 1e-6:
        bad.append('off-tone residual %.2e (file - clean - tone model)' % resid)
    level, noise = tone_level_db(iq, quiet_segments(side), hz)
    if abs(10 * np.log10(noise / NOISE)) > 0.3:
        bad.append('measured noise in 1 MHz is %.2f dB off the floor' % (10 * np.log10(noise / NOISE)))
    if abs(level - power) > 0.1:
        bad.append('tone is %.2f dB over the noise in 1 MHz, wanted %g' % (level, power))
    if tone_level_db(clean, quiet_segments(side), hz)[0] > -10:
        bad.append('the clean file has a tone there')
    return bad


def truth_failures(offset, power, side, iq, center=2441.0, paired=None):
    """Every burst's truth against the table (channels 41-45) and against the arithmetic of the
    definition (every channel)."""
    bad = []
    seen = set()
    for e in side['bursts']:
        c = e['channel']
        off = (center + offset) - (2402 + c)
        want_hit, want_edge = abs(off) <= 0.5 + 1e-12, abs(abs(off) - 0.5) < 1e-9
        if center == 2441.0 and c in EXPECT.get(offset, {}):
            seen.add(c)
            eo, eh, ee = EXPECT[offset][c]
            if abs(eo - off) > 1e-9 or (eh, ee) != (want_hit, want_edge):
                bad.append('the EXPECT table and the arithmetic disagree on channel %d' % c)
        if abs(e['interferer_offset_mhz'] - off) > 1e-9:
            bad.append('ch %d offset %r, want %r' % (c, e['interferer_offset_mhz'], off))
        if e['interferer_overlap'] is not want_hit:
            bad.append('ch %d overlap %r, want %r' % (c, e['interferer_overlap'], want_hit))
        if e['interferer_at_band_edge'] is not want_edge:
            bad.append('ch %d edge %r, want %r' % (c, e['interferer_at_band_edge'], want_edge))
        wp = power if want_hit else None
        if e['interferer_power_in_band_db'] != wp:
            bad.append('ch %d power %r, want %r' % (c, e['interferer_power_in_band_db'], wp))
    if center == 2441.0 and offset in EXPECT and seen != set(EXPECT[offset]):
        bad.append('the run never hopped to channels %s' % sorted(set(EXPECT[offset]) - seen))
    if side['n_samples'] != len(iq):
        bad.append('n_samples %r, file has %d' % (side['n_samples'], len(iq)))
    if side['tone_offset_mhz'] != offset or side['tone_power_db'] != power:
        bad.append('tone_offset_mhz / tone_power_db wrong')
    # the small runs (a channel map, a block size of their own) are not the delivered recipe: no pairing claim
    if side['paired_with'] is not None:
        bad.append('paired_with %r on a small run that is not the delivered recipe' % side['paired_with'])
    bad += count_failures(side, offset, center)
    return bad


def count_failures(side, offset, center):
    """The summary counts, the nested tone metadata and the noise floor against the bursts and the
    definitions, and every burst's keys against per_burst_keys."""
    bad = []
    tone = center + offset
    hits = [abs(tone - e['channel_mhz']) <= 0.5 + 1e-9 for e in side['bursts']]
    edges = [abs(abs(tone - e['channel_mhz']) - 0.5) <= 1e-9 for e in side['bursts']]
    if side['n_bursts_overlapped'] != sum(hits):
        bad.append('n_bursts_overlapped %r, bursts give %d' % (side['n_bursts_overlapped'], sum(hits)))
    if side['n_bursts_at_band_edge'] != sum(edges):
        bad.append('n_bursts_at_band_edge %r, bursts give %d' % (side['n_bursts_at_band_edge'], sum(edges)))
    t = side['interferers']
    if len(t) != 1 or t[0].get('kind') != 'cw' or abs(t[0]['frequency_mhz'] - tone) > 1e-9 \
            or abs(t[0]['offset_mhz'] - offset) > 1e-9 or t[0]['level_db'] != side['tone_power_db']:
        bad.append('interferers %r is not the tone at %g MHz' % (t, tone))
    if abs(side['noise_1mhz'] - NOISE) > 1e-12:
        bad.append('noise_1mhz %r, the floor is %r' % (side['noise_1mhz'], NOISE))
    for i, e in enumerate(side['bursts']):
        if sorted(e) != side['per_burst_keys']:
            bad.append('burst %d keys differ from per_burst_keys' % i)
            break
    return bad


def per_burst_samples_failures(offset, side, iq, clean):
    """The truth of the samples: the tone's power in the channel's 1 MHz during a burst that is
    classified, from file - clean (the tone alone, so no burst power in it), against the tone's
    own filter: a tone inside a channel band is in it, one outside is not."""
    bad = []
    d = iq - clean
    for e in side['bursts']:
        a, b = e['start_sample'] + 400, e['end_sample'] - 400
        x = d[a:b].astype(np.complex128)
        f = np.fft.fftfreq(len(x), 1 / FS) / 1e6
        p = np.abs(np.fft.fft(x * np.hanning(len(x)))) ** 2
        c_mhz = e['channel_mhz'] - 2441.0
        inband = p[np.abs(f - c_mhz) <= 0.5].sum() / p.sum()
        # a Hann main lobe (~0.01 MHz at these lengths) is much narrower than the margin
        if abs(abs(e['interferer_offset_mhz']) - 0.5) > 0.02:
            if (inband > 0.5) != e['interferer_overlap']:
                bad.append('burst ch %d: %.2f of the tone in band, overlap %r'
                           % (e['channel'], inband, e['interferer_overlap']))
    return bad


def sidecar_failures(side):
    bad = []
    for k in ('n_samples', 'tone_offset_mhz', 'tone_power_db', 'paired_with', 'per_burst_keys', 'air_bits_omitted',
              'noise_1mhz', 'interferer_note', 'interferer_overlap', 'interferer_offset_mhz',
              'interferer_at_band_edge', 'interferer_power_in_band_db'):
        if k not in side:
            bad.append('no %s' % k)
    for k in ('interferer_overlap', 'interferer_offset_mhz', 'interferer_at_band_edge',
              'interferer_power_in_band_db'):
        if side.get(k, 0) is not None:
            bad.append('top-level %s is not null' % k)
    note = side['interferer_note'].lower()
    for word in ('both edges inclusive', 'tone minus channel', 'interferer_at_band_edge',
                 'grading convention, not a measured band power', '6.99 db', '16.99 db'):
        if word not in note:
            bad.append('the note lacks %r' % word)
    if side['paired_with'] is None and ('same bursts, seed' in note or 'paired_with is null' not in note):
        bad.append('the note claims a pairing the sidecar does not have')
    if side['paired_with'] is not None and 'paired_with is null' in note:
        bad.append('the note denies a pairing the sidecar claims')
    return bad


def run_all(offset, power):
    """Every check on one small run; the list of what is wrong."""
    center = 2441.0
    iq, side = build(offset, power)
    clean, cside = build()
    return all_failures(offset, power, iq, side, clean)


def all_failures(offset, power, iq, side, clean, center=2441.0):
    bad = []
    bad += pairing_and_tone_failures(offset, power, iq, clean, side, center)
    bad += truth_failures(offset, power, side, iq, center)
    bad += per_burst_samples_failures(offset, side, iq, clean)
    bad += sidecar_failures(side)
    return bad


def test_clean_path():
    print('clean path')
    clean, side = build()
    ref, _ = gi.synthesise_interf('clean', bursts=BURSTS, channels=CHANNELS, block_samples=BLOCK)
    check(clean.dtype == ref.dtype and np.array_equal(clean, ref), 'the clean path is bt_synth_interf\'s clean run, exactly')
    iq, s2 = build(4.5, 20.0)
    again, _ = bb.synthesise_between(4.5, 20.0, bursts=BURSTS, channels=CHANNELS, block_samples=BLOCK)
    check(np.array_equal(iq, again), 'the same arguments make the same samples')
    other, _ = bb.synthesise_between(4.5, 20.0, seed=6102, bursts=BURSTS, channels=CHANNELS, block_samples=BLOCK)
    check(not np.array_equal(iq, other), 'another seed is another file')
    check(len(set(e['channel'] for e in side['bursts']) & {41, 42, 43, 44, 45}) == 5,
          'the small run meets channels 41-45')


def test_files():
    for key, (offset, power) in bb.BETWEEN.items():
        print(key)
        check_all(run_all(offset, power), 'tone, pairing, truth, samples and sidecar of %s' % key)


def test_edge_and_centre_and_pairing():
    print('edge tolerance, the centre argument, the pairing claim')
    for off in (4.500001, 4.499999):
        iq, side = build(off, 20.0)
        clean, _ = build()
        check_all(all_failures(off, 20.0, iq, side, clean), 'a tone at %.6f (1e-6 off the edge): not flagged edge' % off)
    iq, side = build(3.5, 20.0, center=2442.0)
    clean, _ = build(center=2442.0)
    check_all(all_failures(3.5, 20.0, iq, side, clean, center=2442.0),
              'centre 2442.0, tone +3.5 (the same 2445.5 MHz): truth from the centre given')
    check(any(e['interferer_at_band_edge'] for e in side['bursts']), '... and it has edge bursts')
    check(side['paired_with'] is None, 'a small run claims no pairing')
    iq6, side6 = bb.synthesise_between(4.5, 20.0, seed=6102, bursts=BURSTS, channels=CHANNELS, block_samples=BLOCK)
    check(side6['paired_with'] is None and not sidecar_failures(side6), 'seed 6102: paired_with null, the note says so')
    check(bb.is_canonical(6101, gi.BLOCK_SAMPLES, {}) and bb.is_canonical(6101, gi.BLOCK_SAMPLES, {'bursts': 800})
          and not bb.is_canonical(6102, gi.BLOCK_SAMPLES, {}) and not bb.is_canonical(6101, BLOCK, {})
          and not bb.is_canonical(6101, gi.BLOCK_SAMPLES, {'bursts': 801})
          and not bb.is_canonical(6101, gi.BLOCK_SAMPLES, {'channels': CHANNELS})
          and not bb.is_canonical(6101, gi.BLOCK_SAMPLES, {'center_mhz': 2442.0}),
          'only the delivered recipe is canonical (seed, bursts, channels, centre, block size)')


def test_delivered():
    print('the delivered clean file')
    if not os.path.exists(DELIVERED):
        print('  skip (no %s)' % DELIVERED)
        return
    n = 2 ** 22
    # the full-size run's first samples: bursts 800, channels 31-50, block 2**22, like the delivered file;
    # only the first 2**22 samples are needed, so make the whole train once
    iq, _ = bb.synthesise_between()
    ref = np.fromfile(DELIVERED, dtype='<c8', count=n)
    check(np.array_equal(iq[:n], ref), 'first 2**22 samples equal the delivered clean file')
    del iq


# --- mutants -------------------------------------------------------------------

def caught(name, mut_patches, offset=4.5, power=20.0, center=2441.0, **kw):
    """Run every check on the mutant; it must fail one."""
    with contextlib.ExitStack() as st:
        for p in mut_patches:
            st.enter_context(p)
        try:
            iq, side = bb.synthesise_between(offset, power, bursts=BURSTS, channels=CHANNELS, block_samples=BLOCK,
                                             center_mhz=center, **kw)
        except Exception as e:
            check(True, 'mutant %s: raised %s' % (name, type(e).__name__))
            return
    clean, _ = build(seed=kw.get('seed'), center=center)
    bad = all_failures(offset, power, iq, side, clean, center)
    check(bool(bad), 'mutant %s is caught%s' % (name, ': ' + bad[0] if bad else ''))


def wrapped(fn):
    """A patch of ``synthesise_between`` that lets ``fn(sidecar)`` corrupt the sidecar after it is made."""
    orig = bb.synthesise_between

    def inner(*a, **k):
        iq, side = orig(*a, **k)
        fn(side)
        return iq, side
    return mock.patch.object(bb, 'synthesise_between', inner)


def ungated_phase_break(iq, specs, frames, seed, fs, center, block_samples=gi.BLOCK_SAMPLES):
    """The tone with its phase restarted at every block (sample number from the block's start)."""
    if not specs:
        return
    s = specs[0]
    off = gi.tone_offset_hz(s['channel'], center)
    amp = float(np.sqrt(gi.NOISE_1MHZ * 10 ** (s['level_db'] / 10)))
    ph = float(np.random.default_rng([seed, 5, 0]).uniform(0, 2 * np.pi))
    for lo in range(0, len(iq), block_samples):
        hi = min(lo + block_samples, len(iq))
        n = np.arange(hi - lo)
        iq[lo:hi] += (amp * np.exp(1j * (2 * np.pi * off / fs * n + ph))).astype(np.complex64)


def test_mutants():
    print('mutants')
    caught('a tone 0.5 MHz off', [mock.patch.object(bb, 'tone_spec', lambda o, p, c=bb.CENTER_MHZ:
                                                    dict(kind='cw', channel=c - 2402.0 + o + 0.5, level_db=p))])
    caught('a tone 3 dB low', [mock.patch.object(bb, 'tone_spec', lambda o, p, c=bb.CENTER_MHZ:
                                                 dict(kind='cw', channel=c - 2402.0 + o, level_db=p - 3.0))])
    caught('offset sign flipped in the truth', [mock.patch.object(bb, 'truth_offset', lambda t, ch: ch - t)],
           offset=4.25)
    caught('the edge inclusive on one side only',
           [mock.patch.object(bb, 'overlaps', lambda off: -0.5 < off <= 0.5 + 1e-9)])
    caught('the edge exclusive', [mock.patch.object(bb, 'overlaps', lambda off: abs(off) < 0.5)])
    caught('a different seed', [mock.patch.object(bb, 'SEED', 6102)])
    caught('an ungated phase break at a block boundary', [mock.patch.object(gi, 'add_interferers', ungated_phase_break)])

    def bump(key, d):
        def f(side):
            side[key] += d
        return f

    def nested(side):
        side['interferers'][0]['frequency_mhz'] += 1.0

    def extra_key(side):
        side['bursts'][7]['stray'] = 1

    def zero_counts(side):
        side['n_bursts_overlapped'] = side['n_bursts_at_band_edge'] = 0
    caught('n_samples off by one', [wrapped(bump('n_samples', 1))])
    caught('a wrong overlap count', [wrapped(bump('n_bursts_overlapped', 1))])
    caught('a wrong edge count', [wrapped(bump('n_bursts_at_band_edge', -3))])
    caught('zeroed overlap and edge counts', [wrapped(zero_counts)])
    caught('the nested RF frequency 1 MHz off', [wrapped(nested)])
    caught('an extra key in a later burst only', [wrapped(extra_key)])
    caught('a doubled noise_1mhz', [wrapped(lambda side: side.__setitem__('noise_1mhz', side['noise_1mhz'] * 2))])
    caught('the note without the grading-convention caveat',
           [mock.patch.object(bb, 'NOTE', bb.NOTE.replace('GRADING CONVENTION, NOT A MEASURED BAND POWER', 'ok'))])
    caught('a pairing claimed for another seed', [mock.patch.object(bb, 'is_canonical', lambda *a: True)], seed=6102)
    caught('the offset truth ignoring the centre argument',
           [mock.patch.object(bb, 'tone_frequency_mhz', lambda c, o: 2441.0 + o)], offset=3.5, center=2442.0)
    caught('the edge tolerance 1e-5 (a tone 1e-6 from the edge flagged edge)',
           [mock.patch.object(bb, 'EDGE_TOL_MHZ', 1e-5)], offset=4.500001)
    caught('the edge tolerance 1e-5 (below the edge)',
           [mock.patch.object(bb, 'EDGE_TOL_MHZ', 1e-5)], offset=4.499999)


def main():
    test_clean_path()
    test_files()
    test_edge_and_centre_and_pairing()
    test_delivered()
    test_mutants()
    print('RESULT: PASS' if not failures else 'RESULT: FAIL (%d)' % len(failures))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
