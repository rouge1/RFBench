#!/usr/bin/env python3
"""Hold the sample-clock-offset writer to what its samples say.

    python scripts/test_bt_synth_clk.py
    python scripts/test_bt_synth_clk.py --full          # also the whole delivered clean file, byte for byte
    python scripts/test_bt_synth_clk.py --real DIR      # check the written files in DIR (see ``real_main``)

``scripts/bt_synth_clk.py`` writes the 800-burst clean DH5 train as heard by a receiver whose sample
clock is off by eps ppm (flavour 1, ``clk``) and whose LO moves with it (flavour 2, ``clklo``).
Checked from small runs (24 bursts, a block size of 100003 so bursts straddle many noise-block seams),
against the samples and the definitions, never against the generator's own bookkeeping; eps and the
flavour are the test's own and every expected number is written here from the definition:

* eps = 0 is ``bt_synth_interf``'s clean run exactly, and the delivered clean file's first 2**22 samples;
* the symbol clock from the samples: each burst's start found by a matched filter on the known
  bits at the receiver's own rate (``detect``), within 0.3 sample of ``start_sample + timing_frac``;
  and the detections fitted against the clean file's ``start_exact_nominal`` give the sample-clock
  ratio to 1 ppm (the slope is 1 + eps e-6);
* the rate within a burst: the half-amplitude points of its two raised-cosine ramps, from the
  envelope, are (2870 + 2) us apart = ``114880 * (1 + eps e-6)`` samples;
* the carrier, from the phase slope of the burst against its ideal baseband: the channel's offset
  in true Hz (flavour 1), plus ``-eps e-6 * 2441e6`` in flavour 2, for every burst, with no phase
  step at a noise-block boundary;
* the noise: white, the clean file's variance whatever eps is, the very samples of the clean file
  where no burst is, and the burst SNR in 1 MHz from the samples;
* the bits: demodulated from the samples (carrier removed first) and decoded by libbtbb;
* the sidecar's keys against the samples; the interpolation accuracy against a windowed-sinc resample;
* mutants, each of which a check above must catch.
"""
import contextlib
import json
import math
import os
import sys
import tempfile
import time
from unittest import mock

os.environ.setdefault('OMP_NUM_THREADS', '1')
import numpy as np  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from scripts import bt_ota_check, bt_synth, bt_synth_clk as ck, bt_synth_interf as gi  # noqa: E402

FS = 40e6
LAP, UAP = 0x9E8B33, 0x47
F_LO = 2441.0e6
NOISE = bt_synth.AMPLITUDE ** 2 / 100
BLOCK = 100003                          # odd, so a seam is never at a round sample
BURSTS = 24
DELIVERED = '/media/user/4TB/sdr-synth-tmp/interf/synth_hop20_dh5_int_clean.cf32'
GD = 127                                # group delay of the 255-tap receiver filter
failures = []


def check(ok, what):
    print('  %s %s' % ('ok  ' if ok else 'FAIL', what))
    if not ok:
        failures.append(what)


def check_all(problems, what):
    check(not problems, what + ('' if not problems else ': ' + '; '.join(problems[:4])))


CACHE = {}


def build(eps=0.0, lo=False, bursts=BURSTS, block=BLOCK, snr=30.0, **kw):
    key = (eps, lo, bursts, block, snr, tuple(sorted(kw.items())))
    if key not in CACHE:
        CACHE[key] = ck.synthesise_clk(eps, lo, bursts=bursts, block_samples=block, snr_db=snr, **kw)
    return CACHE[key]


def clean_run(bursts=BURSTS, block=BLOCK, snr=30.0):
    key = ('clean', bursts, block, snr)
    if key not in CACHE:
        CACHE[key] = gi.synthesise_interf('clean', bursts=bursts, block_samples=block, snr_db=snr)
    return CACHE[key]


# --- the receiver the checks are made with ---------------------------------------------------

def taps_rx():
    from scipy.signal import firwin
    return firwin(255, 0.8e6, fs=FS)


TAPS = taps_rx()


def want_ratio(eps):
    return 1.0 + eps * 1e-6


def want_lo(eps, lo):
    return -eps * 1e-6 * F_LO if lo else 0.0


def want_carrier(e, eps, lo):
    """A burst's baseband carrier in true Hz, by the definition."""
    return (e['channel_mhz'] - 2441.0) * 1e6 + want_lo(eps, lo)


def mix_down(iq, a, b, f_hz, eps):
    """``iq[a:b]`` shifted down by ``f_hz`` true Hz, at the receiver's rate (the whole cycles of the
    absolute sample number removed first)."""
    m = np.arange(a, b)
    cycles = f_hz / (FS * want_ratio(eps)) * m
    return np.asarray(iq[a:b]).astype(np.complex128) * np.exp(-2j * np.pi * (cycles - np.floor(cycles)))


def packet_of(k, e):
    return br.Packet(LAP, UAP, e['clk'], 'DH5', br.seq_body(k, br.PACKET_TYPES['DH5'][4]), lt_addr=1, flow=1,
                     arqn=1, seqn=k & 1)


def detect(iq, e, bits, eps, lo, nb=400, search=150):
    """The burst's first preamble sample position (a real number, in file samples), found from the
    samples alone with a matched filter on the packet's first ``nb`` known bits at the receiver's own
    rate: the carrier is removed at the stated true Hz, the complex template's normalised correlation
    is peaked at an integer lag within ``search`` samples of the sidecar's start, and then a
    fractional delay is searched in 0.25-sample steps and refined by a parabola."""
    from scipy.signal import fftconvolve
    fst = FS * want_ratio(eps)
    sps = fst / 1e6
    a = e['start_sample'] - 1200
    b = e['start_sample'] + int((nb + 4) * sps) + 1200
    z = mix_down(iq, a, b, want_carrier(e, eps, lo), eps)
    tb = list(bits[:nb + 6])
    ref, lead = br.gfsk(tb, fst, delay=0.0)
    n = int(nb * sps) + lead
    ref = ref[:n].astype(np.complex128)
    num = np.abs(fftconvolve(z, np.conj(ref[::-1]), 'valid'))
    p2 = np.concatenate([[0], np.cumsum(np.abs(z) ** 2)])
    den = np.sqrt(p2[n:n + len(num)] - p2[:len(num)])
    cc = num / den
    ctr = e['start_sample'] - lead - a
    p = int(np.argmax(cc[ctr - search:ctr + search + 1])) + ctr - search
    ds = np.arange(-0.75, 0.7501, 0.25)
    w = z[p:p + n]
    norm = np.sqrt(np.sum(np.abs(w) ** 2))
    out = []
    for dl in ds:
        r, _ = br.gfsk(tb, fst, delay=dl)
        out.append(abs(np.vdot(r[:n].astype(np.complex128), w)) / norm)
    out = np.array(out)
    k = min(max(int(np.argmax(out)), 1), len(ds) - 2)
    y0, y1, y2 = out[k - 1:k + 2]
    fr = 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2)
    return a + p + lead + ds[k] + fr * 0.25


def rx_bits(iq, e, eps, lo, nbits=None, search=150):
    """The reference receiver's bits for one burst, from the samples: the known carrier (the channel's
    plus the LO's shift) removed BEFORE the 0.8 MHz FIR (the shift is up to 0.49 MHz at +-200 ppm and
    would otherwise sit on the filter's skirt), the access code found by correlating the
    discriminator with its 68 known bits at the receiver's rate (``sps = 40 (1 + eps e-6)``), every
    bit sliced from the phase change over one symbol at its centre at that rate, threshold from the
    access code. ``(bits, start)`` or ``(None, None)`` if the burst runs off the file."""
    from scipy.signal import fftconvolve, lfilter
    fst = FS * want_ratio(eps)
    sps = fst / 1e6
    nbits = e['air_bits_length'] if nbits is None else nbits
    a = e['start_sample'] - 1200
    b = int(e['start_sample'] + nbits * sps + 1200 + GD)
    if a < 0 or b > len(iq):
        return None, None
    z = mix_down(iq, a, b, want_carrier(e, eps, lo), eps)
    z = lfilter(TAPS, 1, z)[GD:]
    d1 = np.angle(z[1:] * np.conj(z[:-1]))
    a68 = np.array(br.access_code(LAP)[:68]) * 2.0 - 1.0
    t = np.arange(int(np.ceil(68 * sps)))
    tm = a68[np.minimum((t / sps).astype(int), 67)]
    tm -= tm.mean()
    c = fftconvolve(d1, tm[::-1], 'valid')
    centre = e['start_sample'] - a
    lo_i, hi_i = max(0, centre - search), min(len(c), centre + search + 1)
    p = lo_i + int(np.argmax(c[lo_i:hi_i]))
    frac = 0.0
    if 0 < p < len(c) - 1:
        y0, y1, y2 = c[p - 1], c[p], c[p + 1]
        den = y0 - 2 * y1 + y2
        frac = 0.5 * (y0 - y2) / den if den else 0.0
    s0 = p + frac
    idx = np.rint(s0 + (np.arange(nbits) + 0.5) * sps).astype(int)
    half = int(round(sps / 2))
    if idx[0] - half < 0 or idx[-1] + half >= len(z):
        return None, None
    f = np.angle(z[idx + half] * np.conj(z[idx - half]))
    ones = a68 > 0
    thr = 0.5 * (f[:68][ones].mean() + f[:68][~ones].mean())
    return (f > thr).astype(np.uint8), a + s0


# --- what a run must show ---------------------------------------------------------------------

def burst_free(side, n_lead=50000, margin=600, minlen=3000):
    """Sample ranges with no burst (its ramps included, with a margin)."""
    segs = [(2000, n_lead)]
    for e0, e1 in zip(side['bursts'], side['bursts'][1:]):
        a = int(e0['end_sample']) + margin
        b = int(e1['start_sample']) - margin
        if b - a >= minlen:
            segs.append((a, b))
    return segs


def pick_bursts(side, block, n_straddle=3, n_far=2):
    """Bursts to look at closely: a few that straddle a noise-block seam, and the farthest carriers."""
    ids = []
    for k, e in enumerate(side['bursts']):
        a, b = e['start_sample'], e['end_sample']
        if a // block != b // block and len(ids) < n_straddle:
            ids.append(k)
    far = sorted(range(len(side['bursts'])), key=lambda k: -abs(side['bursts'][k]['channel_mhz'] - 2441.0))
    for k in far:
        if k not in ids and len(ids) < n_straddle + n_far:
            ids.append(k)
    return sorted(ids)


def ref_burst(k, e, eps, lo):
    """The burst's ideal baseband at the receiver's rate, from the packet's bits and the sidecar's
    position (no carrier, unit amplitude): ``(samples, first file sample)``."""
    fst = FS * want_ratio(eps)
    r, lead = br.gfsk(packet_of(k, e).bits, fst, delay=e['timing_frac'])
    return r.astype(np.complex128), e['start_sample'] - lead


def carrier_failures(iq, side, eps, lo, block, ids):
    """Per picked burst: the carrier from the phase slope of ``burst * conj(ideal)`` against true time,
    to 100 Hz (200 Hz in flavour 2) of the stated channel offset + LO shift; and no phase step: the
    residual phase's 2000-sample means sit within 0.1 rad of the straight line (the stated carrier is mixed down first, so only the error is left to fit)."""
    bad = []
    fst = FS * want_ratio(eps)
    tol = 200.0 if lo else 100.0
    for k in ids:
        e = side['bursts'][k]
        r, a = ref_burst(k, e, eps, lo)
        z = np.asarray(iq[a:a + len(r)]).astype(np.complex128)
        keep = np.abs(r) > 0.99
        want = want_carrier(e, eps, lo)
        mm = np.arange(a, a + len(r))
        cyc = want / fst * mm
        # the ideal baseband divided out and the STATED carrier mixed down: what is left is the error
        q = (z * np.conj(r) * np.exp(-2j * np.pi * (cyc - np.floor(cyc))))[keep]
        t = (mm[keep]) / fst
        # boxes of 20 samples first: at 20 dB a single sample's phase is too noisy to unwrap
        nb = len(q) // 20 * 20
        q = q[:nb].reshape(-1, 20).mean(axis=1)
        t = t[:nb].reshape(-1, 20).mean(axis=1)
        ph = np.unwrap(np.angle(q))
        slope, icpt = np.polyfit(t, ph, 1)
        f_meas = want + slope / (2 * np.pi)
        if abs(f_meas - want) > tol:
            bad.append('burst %d (ch %d): carrier %.1f Hz, wanted %.1f' % (k, e['channel'], f_meas, want))
        res = ph - (slope * t + icpt)
        m = len(res) // 100 * 100
        steps = res[:m].reshape(-1, 100).mean(axis=1)
        if np.abs(steps).max() > 0.1:
            bad.append('burst %d: carrier phase %.2f rad off a straight line (a restart?)' % (k, np.abs(steps).max()))
    return bad


def timing_errors(iq, side, eps, lo, ids):
    """``(detected - (start_sample + timing_frac))`` per burst of ``ids``, and the detections."""
    errs, det, xs = [], [], []
    for k in ids:
        e = side['bursts'][k]
        d = detect(iq, e, packet_of(k, e).bits, eps, lo)
        errs.append(d - (e['start_sample'] + e['timing_frac']))
        det.append(d)
        xs.append(e['start_exact_nominal'])
    return np.array(errs), np.array(det), np.array(xs)


def timing_failures(iq, side, eps, lo, ids, tol=0.3):
    """Detected starts against ``start_sample + timing_frac`` (``tol`` sample) and the sample-clock ratio
    from the fit of the detections against the clean file's own ``start_exact_nominal`` (1 ppm)."""
    bad = []
    errs, det, xs = timing_errors(iq, side, eps, lo, ids)
    for k, d in zip(ids, errs):
        if abs(d) > tol:
            e = side['bursts'][k]
            bad.append('burst %d: detected %.3f, sidecar %.3f (%.2f off)' % (
                k, e['start_sample'] + e['timing_frac'] + d, e['start_sample'] + e['timing_frac'], d))
    slope = np.polyfit(xs, det, 1)[0]
    if abs((slope - 1.0) * 1e6 - eps) > 1.0:
        bad.append('the detections give a clock ratio of %+.3f ppm, eps is %g' % ((slope - 1) * 1e6, eps))
    return bad, (slope - 1) * 1e6


def end_to_end_failures(iq, side, eps, lo, ids):
    """The rate within a burst: the half-amplitude points of the two ramps. The burst's amplitude
    goes 0 to 1 over 2 us before the first bit and back over 2 us after the last, raised-cosine in
    amplitude, so its half points are 1 us before the first bit and 1 us after the last:
    (2870 + 2) us = 114880 nominal samples apart; at the receiver's rate 114880 * (1 + eps e-6)."""
    from scipy.signal import firwin, lfilter
    bad, ds = [], []
    fst = FS * want_ratio(eps)
    taps, gd = firwin(101, 1.0e6, fs=FS), 50              # symmetric: both edges are delayed alike
    for k in ids:
        e = side['bursts'][k]
        a = e['start_sample'] - 400
        b = int(e['start_sample'] + 2870 * fst / 1e6 + 400 + gd)
        z = lfilter(taps, 1, mix_down(iq, a, b, want_carrier(e, eps, lo), eps))[gd:]
        env = np.abs(z)
        level = np.median(env[int(0.2 * len(env)):int(0.8 * len(env))])
        up = np.argmax(env > 0.5 * level)
        down = len(env) - 1 - np.argmax(env[::-1] > 0.5 * level)

        # linear interpolation of the two crossings
        x_up = up - 1 + (0.5 * level - env[up - 1]) / (env[up] - env[up - 1])
        x_dn = down + (env[down] - 0.5 * level) / (env[down] - env[down + 1])
        ds.append(x_dn - x_up)
    want = 114880 * want_ratio(eps)
    mean = float(np.mean(ds))
    if abs(mean - want) > 0.1:
        bad.append('ramp half-points %.3f samples apart on average, wanted %.3f' % (mean, want))
    if np.max(np.abs(np.array(ds) - want)) > 0.3:
        bad.append('a burst has its half-points %.2f off %.2f' % (np.max(np.abs(np.array(ds) - want)), want))
    return bad, mean - want


def lag_corr(x, lag):
    return float(np.vdot(x[:-lag], x[lag:]).real / np.vdot(x, x).real)


def noise_failures(iq, side, eps, lo, clean, snr):
    """Burst-free samples: white (lag-1 and lag-2 correlations within 3 sigma), the clean file's per-sample
    variance (0.5 %) whatever eps is, the clean file's very samples before the first burst; and
    the burst SNR in 1 MHz from the samples, to 0.2 dB."""
    bad = []
    segs = burst_free(side)
    x = np.concatenate([np.asarray(iq[a:b]) for a, b in segs]).astype(np.complex128)
    var = float(np.mean(np.abs(x) ** 2))
    want = NOISE * (FS / 1e6)                              # complex variance per sample
    if abs(var / want - 1) > 0.005:
        bad.append('burst-free variance %.5g, the floor is %.5g' % (var, want))
    for lag in (1, 2):
        c = np.mean([lag_corr(np.asarray(iq[a:b]).astype(np.complex128), lag) for a, b in segs])
        sigma = 1 / np.sqrt(len(x))
        if abs(c) > 3 * sigma:
            bad.append('lag-%d correlation %.5f (3 sigma = %.5f): the noise is not white' % (lag, c, 3 * sigma))
    first = segs[0][1]
    if not np.array_equal(np.asarray(iq[:first]), np.asarray(clean[:first])):
        bad.append('the samples before the first burst are not the clean file\'s own noise')
    # burst power over the noise in 1 MHz, from the interior of the bursts
    amp2 = []
    for e in side['bursts'][::3]:
        a, b = e['start_sample'] + 800, e['end_sample'] - 800
        amp2.append(float(np.mean(np.abs(np.asarray(iq[a:b]).astype(np.complex128)) ** 2)) - want)
    got = 10 * np.log10(np.mean(amp2) / NOISE)
    if abs(got - snr) > 0.2:
        bad.append('burst SNR in 1 MHz is %.2f dB, wanted %g' % (got, snr))
    return bad


def sidecar_failures(side, iq, eps, lo, clean, cside, block, bursts, snr):
    bad = []
    g = want_ratio(eps)
    if side['n_samples'] != len(iq) or len(iq) != int(round(len(clean) * g)):
        bad.append('n_samples %r, file %d, clean %d x %.6f' % (side['n_samples'], len(iq), len(clean), g))
    for k in ('eps_ppm', 'sample_clock', 'samples_per_symbol_true', 'symbol_rate_hz',
              'apparent_symbol_rate_hz_if_fs_assumed_40e6', 'slot_samples_true', 'lo_offset_hz', 'lo_offset_formula',
              'resampling', 'n_samples', 'paired_with', 'per_burst_keys', 'air_bits_omitted', 'noise_1mhz', 'clk_note',
              'flavour'):
        if k not in side:
            bad.append('no %s' % k)
    if bad:
        return bad
    if side['eps_ppm'] != eps:
        bad.append('eps_ppm %r, eps %g' % (side['eps_ppm'], eps))
    sc = side['sample_clock']
    if sc['nominal_hz'] != 40e6 or abs(sc['true_hz'] - 40e6 * g) > 1e-3 or sc['eps_ppm'] != eps \
            or 'fast' not in sc['meaning'] or 'eps > 0' not in sc['meaning']:
        bad.append('sample_clock %r' % sc)
    if abs(side['samples_per_symbol_true'] - 40 * g) > 1e-9:
        bad.append('samples_per_symbol_true %r, want %r' % (side['samples_per_symbol_true'], 40 * g))
    if side['symbol_rate_hz'] != 1e6:
        bad.append('symbol_rate_hz %r' % side['symbol_rate_hz'])
    if abs(side['apparent_symbol_rate_hz_if_fs_assumed_40e6'] - 1e6 / g) > 1e-6:
        bad.append('apparent symbol rate %r, a receiver taking fs = 40e6 sees 1e6 / (1 + eps e-6) = %r'
                   % (side['apparent_symbol_rate_hz_if_fs_assumed_40e6'], 1e6 / g))
    if abs(side['slot_samples_true'] - 25000 * g) > 1e-6:
        bad.append('slot_samples_true %r' % side['slot_samples_true'])
    if abs(side['cfo_hz'] - want_lo(eps, lo)) > 1e-6 or side['cfo_hz'] != side['lo_offset_hz']:
        bad.append('cfo_hz %r, wanted %r and equal to lo_offset_hz %r' % (side['cfo_hz'], want_lo(eps, lo), side['lo_offset_hz']))
    if abs(side['lo_offset_hz'] - want_lo(eps, lo)) > 1e-6:
        bad.append('lo_offset_hz %r, want %r' % (side['lo_offset_hz'], want_lo(eps, lo)))
    if side['flavour'] != ('clklo' if lo else 'clk'):
        bad.append('flavour %r' % side['flavour'])
    formula = side['lo_offset_formula'].lower()
    for word in ('-eps_ppm', '1e-6', '2441e6', 'continuous', 'lower'):
        if word not in formula:
            bad.append('lo_offset_formula lacks %r' % word)
    note = side['clk_note'].lower()
    for word in ('eps_ppm > 0', 'fast', 'constant', 'not modelled', 'jitter', 'drift', 'white', 'not resampled',
                 'lo_offset_hz', 'every burst'):
        if word not in note:
            bad.append('clk_note lacks %r' % word)
    if side['resampling'][:4] != 'none' or 'directly' not in side['resampling']:
        bad.append('resampling %r' % side['resampling'][:40])
    if side['paired_with'] is not None:
        bad.append('paired_with %r on a small run that is not the delivered recipe' % side['paired_with'])
    if side['air_bits_omitted'] is not True:
        bad.append('air_bits_omitted')
    for k in ('timing_frac', 'symbol_phase', 'channel_mhz', 'bt_channel'):
        if side.get(k, 0) is not None:
            bad.append('top-level %s is not null' % k)
    if len(side['bursts']) != bursts:
        bad.append('%d bursts' % len(side['bursts']))
    for k, (e, c) in enumerate(zip(side['bursts'], cside['bursts'])):
        if sorted(e) != side['per_burst_keys']:
            bad.append('burst %d keys differ from per_burst_keys' % k)
            break
        if e['start_exact_nominal'] != c['start_sample'] + c['timing_frac']:
            bad.append('burst %d start_exact_nominal %r, the clean file has %r' % (
                k, e['start_exact_nominal'], c['start_sample'] + c['timing_frac']))
        for key in ('channel', 'clk', 'ptype', 'header18', 'payload_hex', 'payload_full_hex', 'snr_db'):
            if e[key] != c[key]:
                bad.append('burst %d %s differs from the clean file\'s' % (k, key))
        pos = e['start_sample'] + e['timing_frac']
        if not 0 <= e['timing_frac'] < 1 or abs(pos - e['start_exact_nominal'] * g) > 1e-6:
            bad.append('burst %d: start_sample + timing_frac = %.6f, nominal %.6f x %.6f = %.6f' % (
                k, pos, e['start_exact_nominal'], g, e['start_exact_nominal'] * g))
        sp = pos % (40 * g)
        if abs(e['symbol_phase_samples'] - sp) > 1e-6 or e['symbol_phase'] != int(sp):
            bad.append('burst %d symbol_phase_samples %r, wanted %.6f' % (k, e['symbol_phase_samples'], sp))
        if abs(e['carrier_offset_hz'] - want_carrier(e, eps, lo)) > 1e-6:
            bad.append('burst %d carrier_offset_hz %r' % (k, e['carrier_offset_hz']))
        if e['air_bits_length'] != len(packet_of(k, e).bits) or 'air_bits' in e:
            bad.append('burst %d air_bits_length / air_bits' % k)
        want_end = math.floor(pos + e['air_bits_length'] * 40 * g)
        if e['end_sample'] != want_end:
            bad.append('burst %d end_sample %r, wanted floor(position + bits * 40 * ratio) = %d' % (k, e['end_sample'], want_end))
    for i, e in enumerate(side['bursts'][::5]):
        p = packet_of(i * 5, e)
        if p.header18 != e['header18'] or p.payload_full.hex() != e['payload_full_hex']:
            bad.append('burst %d is not the packet its sidecar says' % (i * 5))
    return bad


def all_failures(eps, lo, iq, side, n_detect=8, block=BLOCK, snr=30.0, bursts=BURSTS):
    clean, cside = clean_run(bursts, block, snr)
    bad = sidecar_failures(side, iq, eps, lo, clean, cside, block, bursts, snr)
    if bad and 'no ' in bad[0]:
        return bad
    stride = max(1, len(side['bursts']) // n_detect)
    ids = list(range(0, len(side['bursts']), stride))
    bad += timing_failures(iq, side, eps, lo, ids)[0]
    bad += carrier_failures(iq, side, eps, lo, block, pick_bursts(side, block))
    bad += noise_failures(iq, side, eps, lo, clean, snr)
    return bad


# --- the tests -------------------------------------------------------------------------------

def test_zero_and_determinism():
    print('eps = 0 is the clean file')
    iq, side = build(0.0)
    clean, cside = clean_run()
    check(iq.dtype == clean.dtype and np.array_equal(iq, clean), 'eps = 0 reproduces bt_synth_interf\'s clean run exactly')
    check(all(e['start_sample'] == c['start_sample'] and e['timing_frac'] == c['timing_frac']
              and e['symbol_phase'] == c['symbol_phase'] for e, c in zip(side['bursts'], cside['bursts'])),
          'and its per-burst start_sample, timing_frac and symbol_phase are the clean file\'s')
    again, _ = ck.synthesise_clk(200.0, False, bursts=BURSTS, block_samples=BLOCK, snr_db=30.0)
    check(np.array_equal(again, build(200.0)[0]), 'the same arguments make the same samples')
    iq2, _ = ck.synthesise_clk(0.0, True, bursts=BURSTS, block_samples=BLOCK, snr_db=30.0)
    check(np.array_equal(iq2, clean), 'eps = 0 flavour 2 is also the clean file (no LO shift at eps 0)')
    if os.path.exists(DELIVERED):
        n = 2 ** 22
        d, _ = ck.synthesise_clk(0.0, False, bursts=30)
        ref = np.fromfile(DELIVERED, dtype='<c8', count=n)
        check(np.array_equal(d[:n], ref), 'first 2**22 samples equal the delivered clean file')
    else:
        print('  skip (no %s)' % DELIVERED)
    for eps in (20, -20, 200, -200):
        g, _ = build(float(eps))
        check(len(g) == int(round(len(clean) * (1 + eps * 1e-6))) and (len(g) > len(clean)) == (eps > 0),
              'eps %+d: the file is longer (shorter) by 1 + eps e-6: %d vs %d samples' % (eps, len(g), len(clean)))


def test_timing():
    print('the symbol clock, from the samples')
    cases = [(200.0, False), (-200.0, False), (50.0, False), (-50.0, False), (20.0, False), (-20.0, False),
             (100.0, True), (-100.0, True)]
    for eps, lo in cases:
        iq, side = build(eps, lo)
        ids = list(range(len(side['bursts'])))
        bad, ppm = timing_failures(iq, side, eps, lo, ids)
        check_all(bad, 'eps %+g%s: every burst\'s detected start is within 0.3 sample of start_sample + timing_frac, '
                       'and the fitted sample-clock ratio is %+.3f ppm (wanted %+g, +-1)'
                  % (eps, ' (flavour 2)' if lo else '', ppm, eps))


def test_symbol_rate_in_burst():
    print('the symbol rate within a burst')
    for eps in (0.0, 200.0, -200.0, 50.0, -20.0):
        iq, side = build(eps, False, snr=60.0)
        ids = list(range(0, len(side['bursts'])))
        bad, dev = end_to_end_failures(iq, side, eps, False, ids)
        check_all(bad, 'eps %+g: the ramp half-points of a DH5 are 114880 * (1 + eps e-6) samples apart (mean off by %+.3f)'
                  % (eps, dev))


def test_carrier():
    print('the carrier')
    for eps, lo in ((200.0, False), (-200.0, False), (100.0, True), (-100.0, True), (20.0, True)):
        iq, side = build(eps, lo)
        ids = pick_bursts(side, BLOCK)
        n_straddle = sum(side['bursts'][k]['start_sample'] // BLOCK != side['bursts'][k]['end_sample'] // BLOCK
                         for k in ids)
        bad = carrier_failures(iq, side, eps, lo, BLOCK, ids)
        check_all(bad, 'eps %+g%s: %d bursts, %d straddling a noise block, carrier = channel offset%s, no phase step'
                  % (eps, ' (flavour 2)' if lo else '', len(ids), n_straddle,
                     ' %+.1f Hz' % want_lo(eps, lo) if lo else ''))
    iq1, s1 = build(100.0, False)
    iq2, s2 = build(100.0, True)
    check(all(abs(e2['carrier_offset_hz'] - e1['carrier_offset_hz'] + 244100.0) < 1e-6
              for e1, e2 in zip(s1['bursts'], s2['bursts'])),
          'flavour 2 at +100 ppm is -244100 Hz below flavour 1 for every burst')


def test_noise():
    print('the noise')
    clean, cside = clean_run(snr=20.0)
    var0 = None
    for eps in (0.0, 200.0, -200.0):
        iq, side = build(eps, False, snr=20.0)
        check_all(noise_failures(iq, side, eps, False, clean, 20.0),
                  'eps %+g: white, the clean variance, the clean samples before the first burst, burst SNR 20 dB' % eps)
    iq, side = build(100.0, True, snr=20.0)
    check_all(noise_failures(iq, side, 100.0, True, clean, 20.0), 'flavour 2 +100 ppm: the same (the LO shift does not touch the noise)')


def test_bits():
    print('the bits, demodulated from the samples')
    lib = bt_ota_check.libbtbb()
    check(lib is not None, 'libbtbb is there')
    for eps, lo in ((200.0, False), (-200.0, False), (100.0, True), (-100.0, True)):
        iq, side = build(eps, lo)
        ids = pick_bursts(side, BLOCK, 3, 2)
        wrong, exact = 0, 0
        for k in ids:
            e = side['bursts'][k]
            bits, _ = rx_bits(iq, e, eps, lo)
            want = np.array(packet_of(k, e).bits, dtype=np.uint8)
            if bits is None or len(bits) != len(want):
                wrong += 1
                continue
            wrong += int(np.sum(bits != want) > 0)
            if lib is not None and bt_ota_check.check_btbb(lib, bits, e, UAP)[2]:
                exact += 1
        check(wrong == 0 and (lib is None or exact == len(ids)),
              'eps %+g%s: %d bursts demodulate to exactly their air bits and libbtbb takes the payload (%d exact)'
              % (eps, ' with the LO shift removed first' if lo else '', len(ids), exact))
    # the demodulator is not forgiving: one that takes fs to be 40e6 loses the bits at +200 ppm
    iq, side = build(200.0, False)
    e = side['bursts'][1]
    with mock.patch.object(sys.modules[__name__], 'want_ratio', lambda eps: 1.0):
        bits, _ = rx_bits(iq, e, 200.0, False)
    want = np.array(packet_of(1, e).bits, dtype=np.uint8)
    check(bits is None or np.sum(bits != want) > 100,
          'a receiver that assumes fs = 40e6 on the +200 ppm file loses the bits (the files really are off-rate)')


def test_sidecar():
    print('the sidecar against the samples')
    for eps, lo in ((200.0, False), (-50.0, False), (100.0, True), (0.0, False)):
        iq, side = build(eps, lo)
        clean, cside = clean_run()
        check_all(sidecar_failures(side, iq, eps, lo, clean, cside, BLOCK, BURSTS, 30.0),
                  'eps %+g%s: n_samples, per_burst_keys, nulls, rates, offsets, the clean file\'s own keys, the notes'
                  % (eps, ' flavour 2' if lo else ''))
    iq, side = build(100.0, True)
    check(abs(side['bursts'][4]['symbol_phase_samples'] - (side['bursts'][4]['start_sample'] + side['bursts'][4]['timing_frac']) % 40.004) < 1e-6,
          'symbol_phase_samples is the position modulo 40 * (1 + eps e-6) = 40.004')
    # the delivered recipe (canonical) claims its pair, another does not
    check(ck.bt_synth_between.is_canonical(6101, gi.BLOCK_SAMPLES, dict(bursts=800, channels=list(gi.MAP))),
          'the delivered recipe is the paired one')
    # a file is 8 bytes a sample and writes its own sidecar
    with tempfile.TemporaryDirectory() as d:
        spec = dict(eps_ppm=50.0, lo=True, bursts=6)
        p, q = ck.write_file('hop20_dh5_clklo_p50', d, **spec)
        with open(q) as f:
            js = json.load(f)
        check(os.path.getsize(p) == 8 * js['n_samples'] and js['flavour'] == 'clklo' and js['eps_ppm'] == 50.0,
              'write_file: the file is 8 * n_samples bytes, and the sidecar is its own')
    calls = []
    with mock.patch.object(ck, 'write_file', lambda name, out, **spec: calls.append((name, out, spec))):
        ck.main(['--set', 'clk', '--out', '/nowhere'])
        ck.main(['--set', 'clklo', '--out', '/nowhere'])
        ck.main(['hop20_dh5_clk_p20', '--eps', '20', '--out', '/nowhere'])
        ck.main(['hop20_dh5_clklo_m100', '--eps', '-100', '--lo', '--out', '/nowhere'])
    sets = calls[:14]
    check(len(calls) == 16 and all(o == '/nowhere' for _, o, _ in calls)
          and [(n, s['eps_ppm'], s['lo']) for n, _, s in sets[:8]]
          == [('hop20_dh5_clk_' + t, e, False) for t, e in (('p20', 20), ('m20', -20), ('p50', 50), ('m50', -50),
                                                          ('p100', 100), ('m100', -100), ('p200', 200), ('m200', -200))]
          and [(n, s['eps_ppm'], s['lo']) for n, _, s in sets[8:]]
          == [('hop20_dh5_clklo_' + t, e, True) for t, e in (('p20', 20), ('m20', -20), ('p50', 50), ('m50', -50),
                                                           ('p100', 100), ('m100', -100))]
          and calls[14][2]['eps_ppm'] == 20 and not calls[14][2]['lo']
          and calls[15][2]['eps_ppm'] == -100 and calls[15][2]['lo'],
          '--set clk / --set clklo / single-file mode: the 8 + 6 files and the options of each, as documented')
    check([n for n, _ in ck.SETS['clk']] == ['hop20_dh5_clk_%s' % t for t in
                                            ('p20', 'm20', 'p50', 'm50', 'p100', 'm100', 'p200', 'm200')]
          and [n for n, _ in ck.SETS['clklo']] == ['hop20_dh5_clklo_%s' % t for t in
                                                  ('p20', 'm20', 'p50', 'm50', 'p100', 'm100')],
          'the sets are the 8 flavour-1 and 6 flavour-2 files, named p<eps> / m<eps>')


def sinc_resample(bn, a0, u, half=24, beta=9.0):
    """``bn`` (samples at nominal positions ``a0 + j``) evaluated at positions ``u`` (reals) with a Kaiser
    windowed sinc of ``2 * half`` taps."""
    out = np.zeros(len(u), dtype=np.complex128)
    base = np.floor(u).astype(int)
    for k in range(-half + 1, half + 1):
        pos = base + k
        x = u - pos
        w = np.sinc(x) * np.i0(beta * np.sqrt(np.clip(1 - (x / half) ** 2, 0, None))) / np.i0(beta)
        j = pos - a0
        ok = (j >= 0) & (j < len(bn))
        out[ok] += bn[j[ok]] * w[ok]
    return out


def test_interpolation():
    print('the direct rendering against a windowed-sinc resample of the clean burst')
    # one burst: its nominal render (the clean file's) resampled to the receiver's grid with a Kaiser
    # windowed sinc, against the render made directly at the receiver's rate
    k = 5
    for eps in (200.0, -200.0):
        iq, side = build(eps, False)
        clean, cside = clean_run()
        e, c = side['bursts'][k], cside['bursts'][k]
        bits = packet_of(k, c).bits
        g = want_ratio(eps)
        bn, ln = br.gfsk(bits, FS, delay=c['timing_frac'])
        a0 = c['start_sample'] - ln
        bo, lo_ = br.gfsk(bits, FS * g, delay=e['timing_frac'])
        m0 = e['start_sample'] - lo_
        u = (m0 + np.arange(len(bo))) / g
        ref = sinc_resample(bn.astype(np.complex128), a0, u)
        ok = (u > a0 + 30) & (u < a0 + len(bn) - 30)
        x, y = bo[ok].astype(np.complex128), ref[ok]
        cplx = np.vdot(y, x) / np.vdot(y, y)                  # the arbitrary constant phase of a run's start
        res = x - cplx * y
        db = 10 * np.log10(np.sum(np.abs(res) ** 2) / np.sum(np.abs(x) ** 2))
        print('    eps %+g: residual %.1f dB (direct rendering vs a 48-tap Kaiser sinc resample of the clean burst; '
              'common phase %.2e rad)' % (eps, db, abs(np.angle(cplx))))
        check(db < -45, 'eps %+g: the direct render equals the resample of the clean burst to %.1f dB (< -45)' % (eps, db))
        # and the burst sits where the sidecar says: the envelope's half-amplitude point of the ramp-up
        env = np.abs(bo)
        half_pos = np.argmax(env > 0.5)
        x_up = half_pos - 1 + (0.5 - env[half_pos - 1]) / (env[half_pos] - env[half_pos - 1])
        want_up = (e['start_sample'] + e['timing_frac']) - m0 - 40 * g
        check(abs(x_up - want_up) < 0.05, 'eps %+g: the ramp-up half point is 1 us before the first bit (%.3f vs %.3f)'
              % (eps, x_up, want_up))


# --- mutants ----------------------------------------------------------------------------------

def caught(name, patches, eps=200.0, lo=False, wrap=None, bursts=12):
    """Run the mutated generator on a run and every check on it; one must fail. ``wrap`` corrupts the sidecar
    after it is made. The sample checks use a smaller set of bursts."""
    with contextlib.ExitStack() as st:
        for p in patches:
            st.enter_context(p)
        try:
            iq, side = ck.synthesise_clk(eps, lo, bursts=bursts, block_samples=BLOCK, snr_db=30.0)
        except Exception as e:
            check(True, 'mutant %s: raised %s' % (name, type(e).__name__))
            return
    if wrap:
        wrap(side)
    clean, cside = clean_run(bursts, BLOCK, 30.0)
    book = sidecar_failures(side, iq, eps, lo, clean, cside, BLOCK, bursts, 30.0)
    samples = []
    if not book or not any(b.startswith('no ') for b in book):
        ids = list(range(0, bursts, 2))
        samples += timing_failures(iq, side, eps, lo, ids)[0]
        samples += carrier_failures(iq, side, eps, lo, BLOCK, pick_bursts(side, BLOCK, 2, 1))
        samples += noise_failures(iq, side, eps, lo, clean, 30.0)
    bad = book + samples
    check(bool(bad), 'mutant %s is caught (%d sidecar, %d sample checks)%s' % (name, len(book), len(samples),
                                                                           ': ' + bad[0][:110] if bad else ''))


def resampled_noise(iq, seed, fs, block_samples):
    """The mutant: the floor made at the nominal rate and resampled by linear interpolation after the
    bursts, as a noise-before-resampling generator would have it (it is no longer white)."""
    n = len(iq)
    x = np.concatenate([gi.noise_block(seed, i, min(block_samples, n - lo), fs)
                        for i, lo in enumerate(range(0, n, block_samples))]).astype(np.complex128)
    y = 0.63 * x + 0.37 * np.concatenate([x[1:], x[:1]])
    iq += (y * np.sqrt(1 / (0.63 ** 2 + 0.37 ** 2))).astype(np.complex64)


def test_mutants():
    print('mutants')
    mod = sys.modules['scripts.bt_synth_clk']
    caught('eps sign flipped in the truth only', [mock.patch.object(ck, 'truth_ratio', lambda e: 1.0 - e * 1e-6)])
    caught('eps sign flipped in the samples only', [mock.patch.object(ck, 'sample_ratio', lambda e: 1.0 - e * 1e-6)])
    caught('the carrier applied before the resample (tone scales by 1 + eps)',
           [mock.patch.object(ck, 'carrier_cycles', lambda f, m, fst: (f / FS * m) - np.floor(f / FS * m))])
    caught('the LO offset applied in flavour 1', [mock.patch.object(ck, 'lo_offset_hz',
                                                                    lambda e, lo, f: -e * 1e-6 * f)])
    caught('the LO offset sign flipped in flavour 2', [mock.patch.object(ck, 'lo_offset_hz',
                                                                         lambda e, lo, f: e * 1e-6 * f if lo else 0.0)],
           eps=100.0, lo=True)
    caught('the LO offset not applied at all in flavour 2 (sidecar still states it)',
           [mock.patch.object(ck, 'lo_cycles', lambda f, m, fst, block: np.zeros(len(m)))], eps=100.0, lo=True)
    caught('the LO offset stated 0 in flavour 2 and not applied', [mock.patch.object(ck, 'lo_offset_hz',
                                                                                    lambda e, lo, f: 0.0)],
           eps=100.0, lo=True)
    caught('noise added before resampling (not white)', [mock.patch.object(ck, 'add_noise', resampled_noise)])
    caught('a phase restart of the LO at every noise block',
           [mock.patch.object(ck, 'lo_cycles', lambda f, m, fst, block: (f / fst * (m % block)) % 1.0)],
           eps=100.0, lo=True)

    def spsw(side):
        side['samples_per_symbol_true'] = 40.0
    caught('a wrong samples_per_symbol_true', [], wrap=spsw)

    def rounded(side):
        for e in side['bursts']:
            e['start_sample'] = int(round(e['start_sample'] + e['timing_frac']))
            e['timing_frac'] = 0.0
    caught('start_sample rounded without timing_frac', [], wrap=rounded)

    def off_by_one(side):
        side['n_samples'] += 1
    caught('n_samples off by one', [], wrap=off_by_one)

    def longer(side):
        side['lo_offset_hz'] = 0.0
    caught('lo_offset_hz stated 0 in flavour 2', [], eps=100.0, lo=True, wrap=longer)

    def clock(side):
        side['sample_clock']['true_hz'] = 40e6 * (1 - 200e-6)
    caught('sample_clock true_hz with the wrong sign', [], wrap=clock)

    def apparent(side):
        side['apparent_symbol_rate_hz_if_fs_assumed_40e6'] = 1e6 * (1 + 200e-6)
    caught('the apparent symbol rate with the other sign', [], wrap=apparent)

    def cfo(side):
        side['cfo_hz'] = side['lo_offset_hz'] + 1e6
    caught('a wrong top-level cfo_hz', [], wrap=cfo)

    def ceil_end(side):
        for e in side['bursts']:
            e['end_sample'] = math.ceil(e['start_sample'] + e['timing_frac']
                                        + e['air_bits_length'] * side['samples_per_symbol_true'])
    caught('end_sample ceil instead of floor', [], wrap=ceil_end)

    def plus_one(side):
        side['bursts'][7]['end_sample'] += 1
    caught('end_sample + 1 on one burst', [], wrap=plus_one)

    def extra(side):
        side['bursts'][5]['stray'] = 1
    caught('an extra key in a later burst only', [], wrap=extra)


# --- the real files -----------------------------------------------------------------------------

def real_main(folder, names, n_slope=80, n_yield=None):
    """Check written files: the fitted ratio (``n_slope`` bursts spread over the file), the carrier of
    a few bursts, the first burst's detected start against the sidecar; and the genie-reference payload
    yield with libbtbb (``n_yield`` bursts, all by default), start and channel from the sidecar.
    The delivered clean file (no ``eps_ppm`` in its sidecar) is eps = 0, flavour 1."""
    lib = bt_ota_check.libbtbb()
    for name in names:
        path = os.path.join(folder, 'synth_%s.cf32' % name)
        with open(os.path.join(folder, 'synth_%s.json' % name)) as f:
            side = json.load(f)
        eps = side.get('eps_ppm', 0.0)
        lo = side.get('flavour') == 'clklo'
        for k, e in enumerate(side['bursts']):
            e.setdefault('air_bits_length', len(packet_of(k, e).bits))
            e.setdefault('start_exact_nominal', e['start_sample'] + e['timing_frac'])
        iq = np.memmap(path, dtype='<c8', mode='r')
        print('%s: %d samples (sidecar %s), file %d bytes, eps %+g ppm, lo offset %.1f Hz'
              % (name, len(iq), side.get('n_samples', 'has none'), os.path.getsize(path), eps,
                 side.get('lo_offset_hz', 0.0)))
        ids = list(np.linspace(0, len(side['bursts']) - 1, n_slope).astype(int))
        errs, det, xs = timing_errors(iq, side, eps, lo, ids)
        ppm = (np.polyfit(xs, det, 1)[0] - 1) * 1e6
        e0 = side['bursts'][0]
        d0 = detect(iq, e0, packet_of(0, e0).bits, eps, lo)
        print('  fitted sample-clock ratio %+.4f ppm (eps %+g) from %d bursts; first burst: detected %.3f, sidecar %.3f; '
              'detected - sidecar over the %d bursts: mean %+.3f, std %.3f, max %.3f sample (20 dB: the detector\'s own '
              'scatter, the same on the delivered clean file)'
              % (ppm, eps, n_slope, d0, e0['start_sample'] + e0['timing_frac'], n_slope, errs.mean(), errs.std(),
                 np.abs(errs).max()))
        cb = carrier_failures(iq, side, eps, lo, gi.BLOCK_SAMPLES, [0, 100, 400, 799])
        print('  carrier of 4 bursts: %s' % ('all within tolerance of the stated channel offset%s'
                                            % (' + lo offset' if lo else '') if not cb else cb[:2]))
        ks = range(len(side['bursts'])) if n_yield is None else range(0, len(side['bursts']),
                                                                      len(side['bursts']) // n_yield)
        n = ok = hd = 0
        for k in ks:
            e = side['bursts'][k]
            bits, _ = rx_bits(iq, e, eps, lo)
            n += 1
            if bits is None:
                continue
            h, crc, exact = bt_ota_check.check_btbb(lib, bits, e, UAP)
            hd += bool(h)
            ok += bool(exact)
        print('  genie-reference yield (libbtbb, start and channel from the sidecar%s): header %.1f %%, payload exact '
              '%.1f %% of %d bursts' % (', LO shift removed' if lo else '', 100 * hd / n, 100 * ok / n, n))


def full_main():
    print('the whole delivered clean file, byte for byte')
    if not os.path.exists(DELIVERED):
        print('  skip')
        return
    t = time.time()
    iq, side = ck.synthesise_clk(0.0, False)
    ref = np.memmap(DELIVERED, dtype='<c8', mode='r')
    same = len(iq) == len(ref) and all(np.array_equal(iq[a:a + 2 ** 22], ref[a:a + 2 ** 22])
                                      for a in range(0, len(iq), 2 ** 22))
    check(same and side['paired_with'] == 'hop20_dh5_int_clean',
          'eps = 0 reproduces the delivered clean file byte for byte over all %d samples (%.0f s), paired_with set' % (len(iq), time.time() - t))


def main():
    args = sys.argv[1:]
    if args and args[0] == '--real':
        real_main(args[1], args[2:])
        return 0
    t0 = time.time()
    test_zero_and_determinism()
    test_timing()
    test_symbol_rate_in_burst()
    test_carrier()
    test_noise()
    test_bits()
    test_sidecar()
    test_interpolation()
    test_mutants()
    if '--full' in args:
        full_main()
    print('RESULT: PASS' if not failures else 'RESULT: FAIL (%d)' % len(failures))
    print('%.0f s' % (time.time() - t0))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
