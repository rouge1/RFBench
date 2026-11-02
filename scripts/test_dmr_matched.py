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


def check_spec():
    h = D.rrc()
    nfft = 8192
    mag = np.abs(np.fft.rfft(h, n=nfft))
    mag = mag / mag[0]
    freq = np.fft.rfftfreq(nfft, 1.0 / D.FS_SYM)
    passband = mag[freq <= 1920]
    stop = mag[freq >= 3400]
    shape_ok = (len(h) == 2 * 8 * D.SPS + 1 and abs(float(h.sum()) - 1) < 1e-9
                and np.allclose(h, h[::-1])
                and passband.min() > 0.98 and passband.max() < 1.02
                and stop.max() < 0.02)
    if not os.path.isfile(SPEC):
        print('  skip the spec text (not in the tree)')
        if shape_ok:
            ok('rrc() length 161, symmetric, passband to 1920 Hz and stop above 3400 Hz')
        else:
            fail('rrc shape', f'pass {passband.min():.3f}/{passband.max():.3f} stop {stop.max():.3f}')
        return
    txt = open(SPEC, encoding='utf-8', errors='replace').read()
    # The contents line also contains the clause number. The body heading is indented.
    start = txt.index('10.2.2.2           Square root raised cosine filter')
    end = txt.index('10.2.2.3', start)
    clause = txt[start:end]
    end3 = txt.index('10.2.3', end)
    clause3 = txt[end:end3]
    edges = (re.search(r'=\s*1\s+for\s+f\s*≤\s*1\s*920\s*Hz', clause) is not None
             and re.search(r'=\s*0\s+for\s+f\s*>\s*2\s*880\s*Hz', clause) is not None
             and re.search(r'\|f\|\s*<\s*2\s*880\s*Hz', clause) is not None)
    # Equation (14)'s cosine did not survive extraction: cos(pi f / 1920) is -1 at
    # 1920 Hz, so it cannot be the transition that continues F=1. No h(t) is printed.
    no_taps = ('h(t)' not in clause and 'h(t)' not in clause3
               and 'impulse response' not in clause.lower())
    if edges and no_taps and shape_ok:
        ok('spec 10.2.2.2 edges 1920/2880 Hz; no time-domain taps in 10.2.2.2 or '
           '10.2.2.3 (the printed cosine argument is not usable); rrc() matches the edges')
    else:
        fail('spec rrc', f'edges {edges} no_taps {no_taps} shape {shape_ok} '
             f'pass {float(passband.min()):.3f}/{float(passband.max()):.3f} '
             f'stop {float(stop.max()):.3f}')


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
    check_spec()
    check_identity()
    check_bs_stream()
    with tempfile.TemporaryDirectory() as tmp:
        check_clean(tmp)
    check_false_accept()
    print('PASS' if not failures else f'FAIL {len(failures)}: {failures}', flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
