#!/usr/bin/env python3
"""Matched-filter symbol decisions. Synthetic calls only.

discriminate(..., matched=False) is the length-10 boxcar. matched=True uses the
root-raised-cosine (alpha 0.2, span 8) on the discriminator output. The 101-tap
low-pass stays. One summary line per case, then PASS or FAIL.

    python scripts/test_dmr_matched.py
"""
import contextlib
import io
import json
import os
import re
import sys
import tempfile

import numpy as np
from scipy.signal import firwin, lfilter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dmr_demod as D  # noqa: E402
import dmr_synth as S  # noqa: E402

SPEC = os.path.join(HERE, '..', 'docs', 'ptt', 'dmr', 'text',
                    'ETSI_TS_102_361-1_v2.5.1_air_interface_protocol.txt')
SPEC_DIR = os.path.join(HERE, '..', 'docs', 'ptt', 'dmr', 'text')
# Clause 10.2.2.2 pins read from the text: F=1 edge, F=0 edge, the symbol
# period, and the absence of a printed h(t) or a numeric transition magnitude.
N_SPEC_PINS = 4
# Seeds 0-9 of an MS call, 2 superframes, 48 kS/s, 18 dB, matched=True,
# squelch 0.25. On this code those seeds have 0 voice-frame bit errors with
# the gate before the RRC. Moving the gate to after the convolution produced
# 6 on the same seeds. Seeds outside this set can drop a whole superframe on
# both orders, so they are not used. 4 sits above the observed 0 and below 6.
SQUELCH_SEEDS = range(10)
SQUELCH_BER_BOUND = 4
failures = []


def fail(name, detail=''):
    print(f'FAIL {name}' + (f': {detail}' if detail else ''), flush=True)
    failures.append(name)


def ok(name):
    print(f'ok   {name}', flush=True)


def front(x, fs):
    """101-tap low-pass and the discriminator, before either smoother."""
    if fs != D.FS_SYM:
        from fractions import Fraction
        from scipy.signal import resample_poly
        r = Fraction(D.FS_SYM / fs).limit_denominator(10000)
        x = resample_poly(x, r.numerator, r.denominator)
    if len(x) <= D.SPS:
        return np.zeros(max(len(x) - 1, 0))
    x = lfilter(firwin(101, 6000, fs=D.FS_SYM), 1, x)
    return np.angle(x[1:] * np.conj(x[:-1])) * D.FS_SYM / (2 * np.pi)


def rrc_ref(alpha, sps=10, span=8):
    """Square-root raised cosine, written here so it is not dmr_demod.rrc.

    The usual time-domain formula (unit-energy per symbol before the sum
    normalisation). alpha is the excess bandwidth.
    """
    t = np.arange(-span * sps, span * sps + 1) / float(sps)
    h = np.empty(t.shape, float)
    for i, x in enumerate(t):
        ax = abs(x)
        if ax < 1e-12:
            h[i] = (1.0 - alpha) + 4.0 * alpha / np.pi
        elif abs(4.0 * alpha * ax - 1.0) < 1e-8:
            h[i] = alpha / np.sqrt(2.0) * (
                (1.0 + 2.0 / np.pi) * np.sin(np.pi / (4.0 * alpha))
                + (1.0 - 2.0 / np.pi) * np.cos(np.pi / (4.0 * alpha)))
        else:
            num = (np.sin(np.pi * x * (1.0 - alpha))
                   + 4.0 * alpha * x * np.cos(np.pi * x * (1.0 + alpha)))
            den = np.pi * x * (1.0 - (4.0 * alpha * x) ** 2)
            h[i] = num / den
    return h / h.sum()


def _mag(h):
    nfft = 65536
    mag = np.abs(np.fft.rfft(h, n=nfft))
    mag = mag / mag[0]
    freq = np.fft.rfftfreq(nfft, 1.0 / D.FS_SYM)
    return freq, mag


def _at(freq, mag, hz):
    return float(mag[int(np.argmin(np.abs(freq - hz)))])


def _ideal_srrc(f_hz, alpha, rate):
    """Magnitude of the root raised cosine: square root of the raised-cosine H(f)."""
    f = abs(f_hz)
    f_pass = (1.0 - alpha) * rate / 2.0
    f_stop = (1.0 + alpha) * rate / 2.0
    if f <= f_pass:
        return 1.0
    if f >= f_stop:
        return 0.0
    rc = 0.5 * (1.0 + np.cos(np.pi / (alpha * rate) * (f - f_pass)))
    return float(np.sqrt(rc))


def _pin_rrc(alpha, f_pass, f_stop, rate):
    """Compare dmr_demod.rrc() with the independent formula. Return an error string or ''.

    The extracted clause prints F=1 up to the pass edge and F=0 above the stop
    edge. Its transition cosine did not survive (the argument is pi*f/1920,
    which is -1 at the pass edge), and it prints no magnitude at 2880 Hz or at
    any transition-band frequency. 2160 Hz is the middle of the lower half of
    that transition, (f_pass + rate/2) / 2, where the ideal SRRC magnitude is
    cos(pi/8). A span-8 filter only approximates it; alpha 0.15 measures about
    0.961 here and alpha 0.2 about 0.916, so ±0.02 around cos(pi/8) keeps 0.2
    and drops 0.15.
    """
    h = D.rrc()
    ref = rrc_ref(alpha, sps=D.SPS, span=8)
    tap_err = float(np.max(np.abs(h - ref)))
    freq, mag = _mag(h)
    f_mid = 0.5 * (f_pass + rate / 2.0)
    got_mid = _at(freq, mag, f_mid)
    want_mid = float(np.cos(np.pi / 8.0))
    # The ideal value at the stop edge is 0. The finite filter is not; pin it
    # to the same independent taps, which moves when alpha does (0.051 at 0.2,
    # 0.014 at 0.15).
    freq_r, mag_r = _mag(ref)
    stop_err = abs(_at(freq, mag, f_stop) - _at(freq_r, mag_r, f_stop))
    ideal_stop = _ideal_srrc(f_stop, alpha, rate)
    ideal_mid = _ideal_srrc(f_mid, alpha, rate)
    bad = []
    if tap_err > 1e-9:
        bad.append(f'taps differ from alpha {alpha} by {tap_err:.3e}')
    if abs(got_mid - want_mid) > 0.02:
        bad.append(f'|H({f_mid:.0f})| {got_mid:.4f} not within 0.02 of cos(pi/8) {want_mid:.4f}')
    if abs(ideal_mid - want_mid) > 1e-9:
        bad.append(f'ideal |H({f_mid:.0f})| {ideal_mid:.4f} is not cos(pi/8)')
    if stop_err > 1e-6:
        bad.append(f'|H({f_stop:.0f})| differs from the reference filter by {stop_err:.3e}')
    if ideal_stop != 0.0:
        bad.append(f'ideal |H({f_stop:.0f})| is {ideal_stop}, the spec prints 0 above that edge')
    if not (len(h) == 2 * 8 * D.SPS + 1 and abs(float(h.sum()) - 1) < 1e-9
            and np.allclose(h, h[::-1])):
        bad.append('length, sum or symmetry')
    return '; '.join(bad)


def check_spec():
    """Return how many spec pins were skipped."""
    if not os.path.isfile(SPEC):
        # The text is what names 1920/2880. Without it, still pin the taps to
        # alpha 0.2, which is the excess bandwidth those edges are: (2880/2400)-1.
        err = _pin_rrc(0.2, 1920.0, 2880.0, 4800.0)
        print(f'SKIPPED {N_SPEC_PINS} spec pins ({SPEC_DIR}/ not present)', flush=True)
        if err:
            fail('rrc formula', err)
        else:
            ok('rrc() matches an independent alpha-0.2 SRRC, including |H(2160)| '
               'near cos(pi/8); spec edges not in the tree')
        return N_SPEC_PINS
    txt = open(SPEC, encoding='utf-8', errors='replace').read()
    start = txt.index('10.2.2.2           Square root raised cosine filter')
    end = txt.index('10.2.2.3', start)
    clause = txt[start:end]
    end3 = txt.index('10.2.3', end)
    clause3 = txt[end:end3]
    m_pass = re.search(r'=\s*1\s+for\s+f\s*≤\s*(\d)\s+(\d{3})\s*Hz', clause)
    m_stop = re.search(r'=\s*0\s+for\s+f\s*>\s*(\d)\s+(\d{3})\s*Hz', clause)
    m_rate = re.search(r'\(1/(\d)\s+(\d{3})\s*s\)', clause)
    # Equation (14)'s cosine did not survive extraction: the argument prints as
    # pi*f/1920, which is -1 at 1920 Hz, so it is not the transition that
    # continues F=1. No numeric magnitude is printed at 2880 Hz or in the
    # transition band, and no h(t) is printed. The magnitude pins below use the
    # SRRC formula instead of that cosine.
    no_taps = ('h(t)' not in clause and 'h(t)' not in clause3
               and 'impulse response' not in clause.lower())
    if not (m_pass and m_stop and m_rate and no_taps):
        fail('spec rrc', f'pass {m_pass is not None} stop {m_stop is not None} '
             f'rate {m_rate is not None} no_taps {no_taps}')
        return 0
    f_pass = int(m_pass.group(1) + m_pass.group(2))
    f_stop = int(m_stop.group(1) + m_stop.group(2))
    rate = int(m_rate.group(1) + m_rate.group(2))
    alpha = f_stop / (rate / 2.0) - 1.0
    edges_ok = (abs((1.0 - alpha) * rate / 2.0 - f_pass) < 1e-6
                and abs((1.0 + alpha) * rate / 2.0 - f_stop) < 1e-6
                and abs(alpha - 0.2) < 1e-9)
    err = _pin_rrc(alpha, float(f_pass), float(f_stop), float(rate))
    if edges_ok and not err:
        ok(f'spec 10.2.2.2: F=1 to {f_pass} Hz, F=0 above {f_stop} Hz, '
           f'symbol rate {rate}, alpha {alpha:.1f}; no printed transition magnitude '
           f'(cosine argument unusable); rrc() matches the formula, '
           f'|H(2160)| near cos(pi/8), |H({f_stop})| matches the reference filter')
    else:
        fail('spec rrc', f'edges {edges_ok} alpha {alpha} {err}')
    return 0


def check_identity():
    rng = np.random.default_rng(2)
    x = (rng.normal(size=8000) + 1j * rng.normal(size=8000)).astype(np.complex64)
    plain = D.discriminate(x, D.FS_SYM)
    off = D.discriminate(x, D.FS_SYM, matched=False)
    on = D.discriminate(x, D.FS_SYM, matched=True)
    raw = front(x, D.FS_SYM)
    want_box = np.convolve(raw, np.ones(D.SPS) / D.SPS, mode='same')
    want_rrc = np.convolve(raw, D.rrc(), mode='same')
    short = D.discriminate(x[:5], D.FS_SYM, matched=True)
    same = (np.array_equal(plain, off) and np.array_equal(off, want_box)
            and np.array_equal(on, want_rrc) and not np.array_equal(on, off)
            and short.shape == (4,) and np.all(short == 0))
    if same:
        ok('matched=False is the boxcar (identical to omitting it); matched=True is rrc(); '
           'a short input stays length-4 zeros')
    else:
        fail('identity', f'plain {np.array_equal(plain, off)} box {np.array_equal(off, want_box)} '
             f'rrc {np.array_equal(on, want_rrc)} differs {not np.array_equal(on, off)}')


def synth(tmp, name, nsf, args, seed):
    amb = os.path.join(tmp, name + '.src.ambe')
    open(amb, 'wb').write(np.random.default_rng(seed).integers(
        0, 256, 9 * 18 * nsf, dtype=np.uint8).tobytes())
    out = os.path.join(tmp, name + '.cf32')
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = S.main([out, '--ambe', amb, '--src', '1001', '--dst', '9'] + args)
    if rc:
        raise SystemExit(f'synth {name} returned {rc}')
    side = json.load(open(os.path.join(tmp, name + '.json')))
    y = np.fromfile(out, np.complex64)
    ambe = D.ambe_to_frames(open(os.path.join(tmp, name + '.ambe'), 'rb').read())
    return y, side, ambe


def tune(y, side):
    fs = side['sample_rate']
    n = np.arange(len(y))
    return y * np.exp(-2j * np.pi * side['offset_hz'] * n / fs), fs


def check_clean(tmp):
    cases = [
        ('ms 48k', ['--sync', 'ms', '--cc', '3', '--fs', '48000', '--superframes', '2',
                    '--lead', '0.2', '--tail', '0.2']),
        ('direct 48k', ['--sync', 'direct1', '--cc', '1', '--fs', '48000', '--superframes', '2',
                        '--lead', '0.2', '--tail', '0.2']),
        ('ms 1M +25 kHz +300 Hz', ['--sync', 'ms', '--cc', '7', '--fs', '1e6', '--offset', '25e3',
                                   '--cfo', '300', '--superframes', '2', '--lead', '0.25',
                                   '--tail', '0.25']),
    ]
    for name, args in cases:
        y, side, ambe = synth(tmp, 'c' + name.split()[0], 2, args, seed=5)
        y, fs = tune(y, side)
        voice, data = side['sync']['voice'], side['sync']['data']
        k = fs / D.FS_SYM
        want = sorted(b['sync_sample'] for b in side['bursts']
                      if b['kind'] == 'data' or b.get('burst') == 'A')
        delay = 50 * k
        for matched in (False, True):
            d = D.discriminate(y, fs, squelch=0.25, matched=matched)
            calls = D.voice_calls(d, voice)
            frames = [f for _, fr in calls for f in fr]
            db = D.data_bursts(d, data)
            got = sorted([p for p, _ in calls] + [p for p, *_ in db])
            exact = (len(frames) == len(ambe)
                     and np.array_equal(np.array(frames, np.uint8), ambe))
            # The RRC is centred by mode='same', so the delay stays the 101-tap's 50
            # samples. Tolerance is one 48 kS/s sample, the same bound as test_dmr_synth.
            aligned = (len(got) == len(want)
                       and max(abs(g * k - w - delay) for g, w in zip(got, want)) <= k)
            if exact and aligned and d.shape == (front(y, fs).shape[0],):
                ok(f'clean {name} matched {matched}: {len(ambe)}/{len(ambe)} frames, '
                   f'sync within 1 sample of the 50-sample delay')
            else:
                err = None
                if len(got) == len(want) and got:
                    err = max(abs(g * k - w - delay) / k for g, w in zip(got, want))
                fail(f'clean {name} matched {matched}',
                     f'exact {exact} syncs {len(got)}/{len(want)} err48k {err}')


def check_bs_stream():
    """A continuous two-slot stream. CACH is the 24 bits between bursts; nothing
    reads it on its own. voice_calls and data_bursts both take the one array."""
    rng = np.random.default_rng(6)
    frames = [rng.integers(0, 2, 72).tolist() for _ in range(18)]
    bursts = []
    for k in range(6):
        fr = frames[3 * k:3 * k + 3]
        centre = D.hex_bits(D.SYNCS['BS_VOICE']) if k == 0 else [0] * 48
        bursts.append(D.voice_burst(fr, centre))
        bursts.append(D.data_burst(rng.integers(0, 2, 196).tolist(), 1, 3))
    stream = []
    for bb in bursts:
        stream += [0] * 24 + bb
    stream += [0] * 200
    x = D.modulate(D.bits_to_symbols(stream), D.FS_SYM)
    good = True
    for matched in (False, True):
        d = D.discriminate(x, D.FS_SYM, matched=matched)
        calls = D.voice_calls(d, 'BS_VOICE')
        got = [f for _, fr in calls for f in fr]
        db = D.data_bursts(d, 'BS_DATA')
        if not (d.shape == (len(x) - 1,) and len(calls) == 1 and got == frames and len(db) == 6):
            good = False
            fail('bs stream', f'matched {matched} shape {d.shape} calls {len(calls)} '
                 f'data {len(db)} frames {got == frames}')
    if good:
        ok('BS stream with CACH gaps, matched False and True: 18/18 voice frames, 6 data bursts, '
           'array length is the baseband minus one sample')


def check_squelch_order(tmp):
    """The squelch gate is applied before the RRC, not after it.

    MS call, matched=True, squelch 0.25, 18 dB, 2 superframes, seeds 0-9.
    On this code those seeds have 0 voice-frame bit errors with the gate
    first and 6 with the gate after the convolution. Seeds that drop a whole
    superframe on both orders are outside this set. The bound 4 is above the
    observed 0 and below 6, so moving `np.where` back to after the convolution
    fails here.
    """
    total = 0
    n_frames = 0
    for seed in SQUELCH_SEEDS:
        y, side, ambe = synth(
            tmp, f'sq{seed}', 2,
            ['--sync', 'ms', '--cc', '3', '--fs', '48000', '--superframes', '2',
             '--lead', '0.2', '--tail', '0.2', '--snr', '18', '--seed', str(seed)],
            seed=seed)
        y, fs = tune(y, side)
        d = D.discriminate(y, fs, squelch=0.25, matched=True)
        calls = D.voice_calls(d, side['sync']['voice'])
        if len(calls) != side['superframes']:
            fail('squelch order', f'seed {seed}: {len(calls)} superframes')
            return
        frames = np.array([f for _, fr in calls for f in fr], np.uint8)
        if frames.shape != ambe.shape:
            fail('squelch order', f'seed {seed}: shape {frames.shape} vs {ambe.shape}')
            return
        total += int(np.sum(frames != ambe))
        n_frames += frames.shape[0]
    if total <= SQUELCH_BER_BOUND:
        ok(f'squelch before the RRC, MS 18 dB, seeds 0-9: {total} voice-frame bit '
           f'errors in {n_frames} frames (bound {SQUELCH_BER_BOUND})')
    else:
        fail('squelch order', f'{total} voice-frame bit errors, bound {SQUELCH_BER_BOUND}')


def check_false_accept():
    step = D.SUPERFRAME_SYMS * D.SPS
    counts = []
    for matched in (False, True):
        iq = (np.random.default_rng(8).standard_normal(600_000)
              + 1j * np.random.default_rng(9).standard_normal(600_000)).astype(np.complex64) * 0.05
        d = D.discriminate(iq, D.FS_SYM, squelch=0.25, matched=matched)
        n_calls = len(D.voice_calls(d, 'MS_VOICE', recover=True))
        positions = np.random.default_rng(10).integers(step, len(d) - step, 200)
        fa = sum(bool(D.superframe_candidate(d, int(p), 600.0, 0.0, 1)[0]) for p in positions)
        counts.append((matched, fa, n_calls))
    if all(fa == 0 and n == 0 for _, fa, n in counts) and counts[1][1] <= counts[0][1]:
        ok(f'false-accept matched False {counts[0][1]}/200, matched True {counts[1][1]}/200, '
           f'recover voice_calls 0 on both')
    else:
        fail('false-accept', str(counts))


def main():
    n_skip = check_spec()
    check_identity()
    check_bs_stream()
    with tempfile.TemporaryDirectory() as tmp:
        check_clean(tmp)
        check_squelch_order(tmp)
    check_false_accept()
    if failures:
        line = f'FAIL {len(failures)}: {failures}'
    elif n_skip:
        line = f'PASS ({n_skip} spec pins skipped: docs/ptt/dmr/text/ not present)'
    else:
        line = 'PASS'
    print(line, flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
