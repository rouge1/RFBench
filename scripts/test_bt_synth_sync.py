#!/usr/bin/env python3
"""Hold the sync-error and noise-only generator to what its samples hold.

    python scripts/test_bt_synth_sync.py

``scripts/bt_synth_sync.py`` writes a hopping DH1 file whose bursts have bit
errors planted in the 64-bit sync word, and a long file of noise alone. The
sidecar's word for each is only worth what the samples say, so every check
here recovers a fact from the samples and sets it against the sidecar:

* each burst's start, found by correlating the access code on the channel
  the sidecar names, and its carrier, measured on the raw samples;
* its level, over the noise floor of the same file;
* the bits, demodulated from the samples: the positions where they differ from
  the correct packet are exactly ``4 + sync_error_bits``, in a burst of 0, 1
  and 2 errors alike, so the preamble, the trailer, the header and the payload
  are as the correct packet has them (a 40 dB variant, so that a wrong bit is
  the file's and not the noise's);
* that the three hopping files differ in nothing but the planted bits;
* for the noise file, that nothing in it is a burst, that its blocks join
  without a seam, that a block at a time is all the memory it takes, and that
  the same arguments give the same bytes.
"""
import filecmp
import json
import os
import subprocess
import sys
import tracemalloc

import numpy as np
from scipy.signal import fftconvolve, firwin, lfilter, welch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from apps import bt_hop  # noqa: E402
from scripts import bt_synth, bt_synth_sync as sync  # noqa: E402

FS = 40e6
SPS = 40
CENTER = 2441.0
failures = []
TAPS = firwin(401, 0.7e6, fs=FS)
GD = len(TAPS) // 2
PAD = 3000
TMP = '/tmp/sdr-p6/sync'


def check(ok, what):
    print('  %s %s' % ('ok  ' if ok else 'FAIL', what))
    if not ok:
        failures.append(what)


def correct_bits(side, k):
    """The packet burst k of a file is, built here from the sidecar's clock and
    the planted LAP and UAP - not read from anywhere in the generator."""
    e = side['bursts'][k]
    body = br.seq_body(k, br.PACKET_TYPES['DH1'][4])
    return np.array(br.Packet(side['lap'], side['uap'], e['clk'], 'DH1', body, lt_addr=1,
                              flow=1, arqn=1, seqn=k & 1).bits)


def sent_bits(side, k):
    """The bits the sidecar says went to the modulator: the correct packet with
    the named sync-word bits flipped."""
    b = correct_bits(side, k).copy()
    for p in side['bursts'][k]['sync_error_bits']:
        b[4 + p] ^= 1
    return b


def demod(iq, side, k):
    """Shift burst k down from its channel, filter, discriminate, and find it by
    correlating the CORRECT access code (a 2-error one still correlates); slice
    at the symbol centres with a threshold from the preamble and trailer only,
    which an error in the sync word cannot move. Returns (start, bits)."""
    e = side['bursts'][k]
    a = correct_bits(side, k)
    lo = max(0, e['start_sample'] - PAD)
    hi = min(len(iq), e['start_sample'] + len(a) * SPS + PAD)
    n = np.arange(lo, hi)
    cycles = (e['channel_mhz'] - side['center_mhz']) * 1e6 / FS * n
    bb = lfilter(TAPS, 1, iq[lo:hi] * np.exp(-2j * np.pi * (cycles - np.floor(cycles))))
    d = np.angle(bb[1:] * np.conj(bb[:-1]))
    a72 = a[:72] * 2 - 1
    tmpl = np.repeat(a72, SPS).astype(float)
    tmpl -= tmpl.mean()
    dm = d - np.convolve(d, np.ones(2001) / 2001, 'same')
    c = fftconvolve(dm, tmpl[::-1], 'valid')
    norm = np.sqrt(fftconvolve(dm ** 2, np.ones(len(tmpl)), 'valid')) * np.linalg.norm(tmpl)
    r = c / np.maximum(norm, 1e-12)
    p = int(np.argmax(r))
    if r[p] < 0.5:
        return None, None
    centres = p + (np.arange(len(a)) + 0.5) * SPS - 0.5
    f = np.interp(centres, np.arange(len(d)), d)
    edge = np.r_[0:4, 68:72]
    _, thr = np.polyfit(a72[edge], f[edge], 1)
    return lo + p - GD, (f > thr).astype(int)


def burst_mean_freq(x, first, count):
    step = np.angle(x[first + 1:first + count + 1] * np.conj(x[first:first + count]))
    return step.mean() * FS / (2 * np.pi)


def carrier_mhz(iq, side, k):
    """Carrier of burst k from its 72 access-code bits, the modulator's own mean
    for the bits really sent taken away."""
    e = side['bursts'][k]
    ref, lead = br.gfsk(sent_bits(side, k), FS, delay=e['timing_frac'])
    mine = burst_mean_freq(iq, e['start_sample'], 72 * SPS)
    clean = burst_mean_freq(ref, lead, 72 * SPS)
    return side['center_mhz'] + (mine - clean) / 1e6


def planted(errors, bursts=40, seed=7, snr=40.0):
    return sync.synthesise_sync(bursts=bursts, errors=errors, seed=seed, snr_db=snr)


def check_errors(errors):
    iq, side = planted(errors)
    n = len(side['bursts'])
    print('\nErrors "%s", %d bursts at 40 dB' % (errors, n))
    counts = [e['sync_errors'] for e in side['bursts']]
    check(set(counts) <= {0, 1, 2}, 'sync_errors is 0, 1 or 2: %s' % sorted(set(counts)))
    check(all(len(e['sync_error_bits']) == e['sync_errors']
              and len(set(e['sync_error_bits'])) == e['sync_errors']
              and all(type(b) is int and 0 <= b < 64 for b in e['sync_error_bits'])
              and e['sync_error_bits'] == sorted(e['sync_error_bits']) for e in side['bursts']),
          'sync_error_bits: that many distinct integers in 0..63, sorted')
    bad_diff = 0
    bad_start = 0
    bad_rest = 0
    worst_start = 0
    for k, e in enumerate(side['bursts']):
        start, bits = demod(iq, side, k)
        if start is None:
            bad_start += 1
            continue
        worst_start = max(worst_start, abs(start - e['start_sample']))
        right = correct_bits(side, k)
        diff = list(np.nonzero(bits != right[:len(bits)])[0])
        if diff != [4 + p for p in e['sync_error_bits']]:
            bad_diff += 1
        if len(bits) != len(right):
            bad_rest += 1
    check(bad_start == 0 and worst_start <= 1,
          'start_sample is where the access code is: all %d found, worst %d sample(s) off'
          % (n, worst_start))
    check(bad_diff == 0 and bad_rest == 0,
          'the demodulated bits differ from the correct packet at exactly 4 + sync_error_bits, '
          'in %d of %d bursts (preamble, trailer, header, payload as the correct packet has them)'
          % (n - bad_diff, n))
    return iq, side


def check_burst_zero():
    print('\nBurst 0 and the rest')
    _, m = planted('mixed', bursts=10)
    _, f = planted('first1', bursts=10)
    _, c = planted('none', bursts=10)
    check(m['bursts'][0]['sync_errors'] == 1 and f['bursts'][0]['sync_errors'] == 1,
          'burst 0 has exactly 1 error in mixed and in first1')
    check(all(e['sync_errors'] == 0 for e in f['bursts'][1:]), 'the rest of first1 have 0')
    check(all(e['sync_errors'] == 0 for e in c['bursts']), 'clean has no errors at all')
    plan = sync.error_plan('mixed', 800, 5001)
    cnt = np.bincount([len(p) for p in plan[1:]], minlength=3) / 799.0
    check(plan[0] and len(plan[0]) == 1 and len(plan) == 800 and (cnt > 0.25).all() and (cnt < 0.42).all(),
          'mixed over 800 bursts (seed 5001), bursts 1..: 0, 1, 2 errors in %.1f, %.1f, %.1f %%'
          % tuple(100 * cnt))
    pos = np.bincount([p for q in plan for p in q], minlength=64)
    check(pos.min() > 0 and pos.max() < 0.04 * pos.sum(),
          'the error positions cover all 64 sync bits, none more than 4 %% of the errors '
          '(%d..%d each)' % (pos.min(), pos.max()))


def check_geometry(iq, side):
    print('\nCarrier, level, phases, channels and window')
    n = len(side['bursts'])
    mask = sum(1 << c for c in range(31, 51))
    addr = side['uap'] << 24 | side['lap']
    want = [bt_hop.hop_channel(0x0123400 + 4 * k, addr, mask) for k in range(n)]
    check([e['channel'] for e in side['bursts']] == want,
          'every burst\'s channel is bt_hop.hop_channel over the 31-50 mask, %d of %d' % (
              sum(e['channel'] == w for e, w in zip(side['bursts'], want)), n))
    check(len(set(want)) >= 12, '%d different channels in %d bursts' % (len(set(want)), n))
    err = np.array([carrier_mhz(iq, side, k) - (2402 + want[k])
                    for k in range(n)]) * 1e3
    check(np.abs(err).max() < 5.0, 'carrier of each burst is (2402 + channel - 2441) MHz from '
          'the centre: all %d within 5 kHz, worst %.2f kHz' % (n, np.abs(err).max()))
    other = min(abs(carrier_mhz(iq, side, k) - (2402 + want[(k + 1) % n])) for k in range(n))
    check(other * 1e3 > 500 or len(set(want)) < 2, 'and at least %.0f kHz from the next burst\'s channel'
          % (other * 1e3))
    ph = [e['symbol_phase'] for e in side['bursts']]
    check(ph == [0 if k % 2 == 0 else 20 for k in range(n)]
          and all(e['start_sample'] % SPS == e['symbol_phase'] for e in side['bursts']),
          'symbol phases alternate 0, 20 and are start_sample %% %d' % SPS)
    check(all(abs(e['channel_mhz'] - 2441.0) + 1 <= 0.36 * 40 for e in side['bursts']),
          'every burst\'s band is inside +/- 14.4 MHz of the centre')
    check(all(0 <= e['timing_frac'] < 1 for e in side['bursts'])
          and len({e['timing_frac'] for e in side['bursts']}) > n // 2,
          'timing_frac differs from burst to burst, in [0, 1)')


def check_level():
    print('\nThe level and the noise floor at the default 20 dB')
    iq, side = sync.synthesise_sync(bursts=30, errors='mixed', seed=5001)
    quiet = np.mean(np.abs(iq[:60000]) ** 2)              # before burst 0, noise only
    want_noise = sync.NOISE_1MHZ * 40
    check(abs(10 * np.log10(quiet / want_noise)) < 0.1,
          'noise before the first burst: %.4f against %.4f over 40 MHz (noise_1mhz x 40)'
          % (quiet, want_noise))
    p = []
    for e in side['bursts']:
        s = e['start_sample']
        p.append(np.mean(np.abs(iq[s + 400:s + 72 * SPS]) ** 2) - quiet)
    snr = 10 * np.log10(np.mean(p) / sync.NOISE_1MHZ)
    check(abs(snr - 20.0) < 0.3 and abs(side['snr_bw_hz'] - 1e6) < 1 and
          all(e['snr_db'] == 20.0 for e in side['bursts']),
          'burst power over the noise in 1 MHz measures %.2f dB, the sidecar says 20' % snr)
    check(abs(side['noise_1mhz'] - bt_synth.AMPLITUDE ** 2 / 100) < 1e-12,
          'noise_1mhz is AMPLITUDE^2 / 100')


def hop_rows():
    return {n: spec for n, spec in sync.SETS['sync'] if not spec.get('noise_only')}


def check_set_rows():
    print('\nThe rows of --set sync, as delivered')
    rows = hop_rows()
    modes = {n: s['errors'] for n, s in rows.items()}
    check(modes == {'hop20_dh1_syncerr_mixed': 'mixed', 'hop20_dh1_syncerr_first1': 'first1',
                    'hop20_dh1_syncerr_clean': 'none'},
          'each hopping file has the error mode its name says: %s' % modes)
    check(len({s['seed'] for s in rows.values()}) == 1 and {s['seed'] for s in rows.values()} == {5001}
          and all(s['bursts'] == 800 for s in rows.values()),
          'one shared seed, 5001, and 800 bursts, in all three')
    noise = [s for n, s in sync.SETS['sync'] if n == 'noise_only_20msps_120s'][0]
    check(noise == dict(noise_only=True, duration_s=120.0, fs=20e6, seed=5004),
          'the noise file is 120 s at 20 MS/s, seed 5004')
    want = {'mixed': (290, 237, 273), 'first1': (799, 1, 0), 'none': (800, 0, 0)}
    for mode, cnt in want.items():
        plan = sync.error_plan(mode, 800, 5001)
        got = tuple(sum(len(p) == n for p in plan) for n in (0, 1, 2))
        check(got == cnt and len(plan[0]) == (0 if mode == 'none' else 1),
              'error plan %s over 800 bursts at seed 5001: 0/1/2 errors %s' % (mode, got))
    plan_m = sync.error_plan('mixed', 800, 5001)
    plan_f = sync.error_plan('first1', 800, 5001)
    check(plan_m[0] == plan_f[0], 'mixed and first1 have the same burst-0 error')


def check_same_but_bits():
    print('\nThe three files of the set, rendered as delivered, differ in nothing but the planted bits')
    out = {}
    for name, spec in hop_rows().items():
        out[name] = sync.synthesise_sync(**spec)
    a, sa = out['hop20_dh1_syncerr_mixed']
    b, sb = out['hop20_dh1_syncerr_clean']
    c, sc = out['hop20_dh1_syncerr_first1']
    keys = ['start_sample', 'timing_frac', 'symbol_phase', 'clk', 'ptype', 'channel', 'header18',
            'payload_hex', 'payload_full_hex', 'payload_valid', 'lt_addr', 'seqn']
    same = all(all(x[k] == y[k] for k in keys) for x, y in zip(sa['bursts'], sb['bursts'])) and \
        all(all(x[k] == y[k] for k in keys) for x, y in zip(sc['bursts'], sb['bursts']))
    check(same, 'start, timing, phase, clock, channel, header and payload are the same in all three')
    for nm, x, sx in (('mixed', a, sa), ('first1', c, sc)):
        d = (x != b)
        win = np.zeros(len(b), bool)
        diff_bursts = 0
        quiet_ok = True
        for e in sx['bursts']:
            s0 = e['start_sample']
            seg = d[s0 - 100:s0 + 16000]
            if e['sync_errors']:
                diff_bursts += 1
                quiet_ok &= bool(seg.any())
            else:
                quiet_ok &= not seg.any()
            win[s0 - 100:s0 + 16000] = True
        n_err = sum(1 for e in sx['bursts'] if e['sync_errors'])
        check(not d[~win].any() and quiet_ok and diff_bursts == n_err,
              '%s against clean: outside the bursts bit for bit the same (noise), an unerrored burst '
              'the same, and exactly the %d errored bursts differ' % (nm, n_err))
    del a, b, c, out
    e0 = sa['bursts'][0]['sync_error_bits']
    check(e0 == sc['bursts'][0]['sync_error_bits'], 'burst 0 has the same flipped bit in mixed and first1')
    check(sa['seed'] == sb['seed'] == sc['seed'] == 5001, 'the sidecars all say seed 5001')


def fit_fraction(iq, side, k):
    """Where burst k really starts, to a twentieth of a sample, from the samples
    alone: scan the delay of the modulator's own output for the bits sent (the
    first 100 symbols) against the measured discriminator, both through the same
    filter. The offset from ``start_sample`` is what ``timing_frac`` claims."""
    e = side['bursts'][k]
    s0 = e['start_sample']
    lo = s0 - PAD
    n = np.arange(lo, s0 + 3000 + 4000)
    cycles = (e['channel_mhz'] - side['center_mhz']) * 1e6 / FS * n
    bb = lfilter(TAPS, 1, iq[n[0]:n[-1] + 1] * np.exp(-2j * np.pi * (cycles - np.floor(cycles))))
    dm = np.angle(bb[1:] * np.conj(bb[:-1]))
    bits = sent_bits(side, k)[:110]
    w0, w1 = PAD + 500, PAD + 100 * SPS      # past the filter and the ramp, where the burst is loud
    best = (None, None)
    for u in np.arange(-0.6, 1.6, 0.05):
        ref, lead = br.gfsk(bits, FS, delay=float(u))
        x = np.zeros(PAD + len(ref) + 10, complex)
        x[PAD - lead:PAD - lead + len(ref)] = ref
        r = lfilter(TAPS, 1, x)
        dr = np.angle(r[1:] * np.conj(r[:-1]))
        err = np.sum((dm[w0:w1] - dr[w0:w1]) ** 2)
        if best[0] is None or err < best[0]:
            best = (err, float(u))
    return best[1]


def check_fraction():
    print('\nThe fractional start: start_sample + timing_frac, from the samples')
    iq, side = planted('mixed', bursts=120, seed=5001)
    fr = np.array([e['timing_frac'] for e in side['bursts']])
    got = np.array([fit_fraction(iq, side, k) for k in range(120)])
    off = got - fr
    slope = np.polyfit(fr, got, 1)[0] - 1
    check(abs(off.mean()) < 0.1 and abs(slope) < 0.2 and np.abs(off).max() < 0.3,
          '120 bursts at 40 dB: fitted start minus (start_sample + timing_frac) has mean %+.3f, '
          'worst %.2f sample; slope against timing_frac minus 1 is %+.3f'
          % (off.mean(), np.abs(off).max(), slope))


def check_determinism():
    print('\nDeterminism')
    a, sa = sync.synthesise_sync(bursts=12, errors='mixed', seed=3)
    b, sb = sync.synthesise_sync(bursts=12, errors='mixed', seed=3)
    c, sc = sync.synthesise_sync(bursts=12, errors='mixed', seed=4)
    check(a.tobytes() == b.tobytes() and json.dumps(sa) == json.dumps(sb),
          'the same arguments give the same samples and the same sidecar')
    check(a.tobytes() != c.tobytes(), 'another seed gives other samples')


def check_sidecar():
    print('\nThe sidecar')
    _, side = sync.synthesise_sync(bursts=6, errors='mixed', seed=5)
    top = ['generator', 'generator_commit', 'lap', 'uap', 'sample_rate', 'center_mhz', 'modulation',
           'slot_samples', 'start_sample_meaning', 'clk_convention', 'snr_bw_hz', 'bursts', 'clk',
           'noise_1mhz', 'air_bits_omitted', 'per_burst_keys', 'symbol_phases', 'sync_error_note',
           'access_code_layout', 'hopping', 'hop_channels', 'afh_map', 'afh_instant']
    check(all(k in side for k in top), 'the top level has all %d keys' % len(top))
    per = ['start_sample', 'timing_frac', 'symbol_phase', 'clk', 'ptype', 'channel', 'channel_mhz',
           'snr_db', 'sync_errors', 'sync_error_bits', 'header18', 'payload_hex']
    check(side['timing_frac'] is None and side['symbol_phase'] is None and side['snr_db'] == 20.0
          and side['clk'] == side['bursts'][0]['clk'],
          'top level: timing_frac and symbol_phase null (they vary), snr_db 20.0, clk burst 0\'s')
    check(all(k in side['per_burst_keys'] for k in per) and 'air_bits' not in side['per_burst_keys']
          and all(set(side['per_burst_keys']) == set(e) for e in side['bursts']),
          'per_burst_keys names exactly the keys every burst carries (%d)' % len(side['per_burst_keys']))
    check(all(all(k in e for k in per) and 'air_bits' not in e for e in side['bursts']),
          'every burst has its %d keys and no air_bits' % len(per))
    check(side['air_bits_omitted'] is True and side['access_code_layout'] ==
          {'preamble': 4, 'sync_word': 64, 'trailer': 4}
          and side['sample_rate'] == 40e6 and side['center_mhz'] == 2441.0
          and side['slot_samples'] == 25000 and side['symbol_phases'] == [0, 20]
          and side['lap'] == 0x9E8B33 and side['uap'] == 0x47 and side['clk'] == 0x0123400,
          'layout, rate, centre, slot, phases, LAP, UAP and clock are as the task says')
    check(all(e['clk'] == 0x0123400 + 4 * k for k, e in enumerate(side['bursts']))
          and all(e['start_sample'] == side['bursts'][0]['start_sample'] + 50000 * k
                  + e['symbol_phase'] - side['bursts'][0]['symbol_phase']
                  for k, e in enumerate(side['bursts'])),
          'a DH1 every 2 slots: the clock 4 ticks, the start 50000 samples a burst')
    for bad in ({'bursts': 0}, {'errors': 'some'}):
        try:
            sync.synthesise_sync(**bad)
            check(False, '%r must raise' % bad)
        except ValueError:
            check(True, '%r raises ValueError' % bad)


# --- noise only ----------------------------------------------------------------

def read_cf32(path):
    return np.fromfile(path, '<c8')


def check_noise_only():
    print('\nA noise-only file, 2 s at 20 MS/s')
    fsn = 20e6
    path = os.path.join(TMP, 'noise.cf32')
    side = sync.write_noise_only(path, 2.0, fsn, seed=5004, block_samples=2 ** 18)
    x = read_cf32(path)
    check('lap' not in side and 'uap' not in side and 'modulation' not in side
          and side['noise_power_1mhz'] == side['noise_1mhz'] == sync.NOISE_1MHZ
          and 'default_rng([seed, 1, i])' in side['noise_note'] and 'partial' in side['noise_note'],
          'no lap, uap or modulation; noise_power_1mhz as noise_1mhz; the recipe is in noise_note')
    check(len(x) == 40_000_000 and side['bursts'] == [] and side['noise_only'] is True
          and side['duration_s'] == 2.0 and side['sample_rate'] == 20e6
          and side['air_bits_omitted'] is True,
          '%d samples, bursts [], noise_only true, duration_s 2' % len(x))
    p = np.mean(np.abs(x) ** 2) / (fsn / 1e6)
    check(abs(10 * np.log10(p / bt_synth.AMPLITUDE ** 2 * 100)) < 0.02 and
          abs(side['noise_1mhz'] - bt_synth.AMPLITUDE ** 2 / 100) < 1e-12,
          'power per MHz %.4e against the floor %.4e (%.3f dB)'
          % (p, bt_synth.AMPLITUDE ** 2 / 100, 10 * np.log10(p / (bt_synth.AMPLITUDE ** 2 / 100))))
    f, pxx = welch(x[:8_000_000], fs=fsn, nperseg=4096, return_onesided=False,
                  detrend=False)
    pxx = np.fft.fftshift(pxx)
    db = 10 * np.log10(pxx / pxx.mean())
    check(db.max() < 1.0 and db.min() > -1.0, 'spectrum flat across the 20 MHz band: %.2f to %.2f dB'
          % (db.min(), db.max()))
    win = 1000                                                   # 50 us
    w = np.mean(np.abs(x[:len(x) // win * win].reshape(-1, win)) ** 2, axis=1)
    sig = 1 / np.sqrt(win)
    z = (w / w.mean() - 1) / sig
    check(z.max() < 5.5 and z.min() > -5.5,
          'no 50 us stretch off the floor by more than 5.5 sigma: worst %+.2f / %+.2f sigma over %d windows'
          % (z.max(), z.min(), len(z)))
    # A 20 dB burst has bt_synth.AMPLITUDE**2 over 20 MHz of 20 x the floor in 1 MHz: 5 x the mean.
    check(w.max() < 1.2 * w.mean(),
          'the loudest 50 us is %.3f x the mean; a burst would be 5 x' % (w.max() / w.mean()))
    check(abs(np.std(x.real) / np.std(x.imag) - 1) < 0.002 and abs(np.mean(x.real)) < 1e-4
          and abs(np.mean(x.imag)) < 1e-4, 'I and Q have equal spread and no offset')

    # blocks join without a seam
    blk = 2 ** 18
    edges = np.arange(blk, len(x) - 1, blk)
    xc = np.abs(np.mean(x[edges - 1] * np.conj(x[edges]))) / np.mean(np.abs(x) ** 2)
    xin = np.abs(np.mean(x[edges - 5] * np.conj(x[edges - 4]))) / np.mean(np.abs(x) ** 2)
    pw = np.mean([np.mean(np.abs(x[b - 8:b + 8]) ** 2) for b in edges]) / np.mean(np.abs(x) ** 2)
    n_e = len(edges)
    check(xc < 5 / np.sqrt(n_e) and abs(pw - 1) < 5 / np.sqrt(16 * n_e),
          'across %d block joins: adjacent-sample correlation %.3f (inside a block %.3f; limit %.3f), '
          'power within +/-8 samples of a join %.3f x the mean'
          % (n_e, xc, xin, 5 / np.sqrt(n_e), pw))
    b0, b1 = x[:blk], x[blk:2 * blk]
    cc = np.abs(np.vdot(b0, b1)) / (np.linalg.norm(b0) * np.linalg.norm(b1))
    check(cc < 5 / np.sqrt(blk), 'neighbouring blocks are not the same noise: correlation %.4f' % cc)

    # Many joins: 0.5 s in blocks of 2**13, 1220 of them; the statistic's own spread is
    # 1/sqrt(joins), and a seam (a repeated or zeroed sample) would show as order 1.
    pathj = os.path.join(TMP, 'noisej.cf32')
    sync.write_noise_only(pathj, 0.5, fsn, seed=5004, block_samples=2 ** 13)
    y = read_cf32(pathj)
    os.remove(pathj)
    ej = np.arange(2 ** 13, len(y) - 1, 2 ** 13)
    pj = np.mean(np.abs(y) ** 2)
    xj = np.abs(np.mean(y[ej - 1] * np.conj(y[ej]))) / pj
    pwj = np.mean([np.mean(np.abs(y[b - 4:b + 4]) ** 2) for b in ej]) / pj
    check(xj < 4 / np.sqrt(len(ej)) and abs(pwj - 1) < 4 / np.sqrt(8 * len(ej)),
          'across %d joins of 8192-sample blocks: correlation %.3f (limit %.3f), power at the join '
          '%.3f x the mean' % (len(ej), xj, 4 / np.sqrt(len(ej)), pwj))

    path2 = os.path.join(TMP, 'noise2.cf32')
    sync.write_noise_only(path2, 2.0, fsn, seed=5004, block_samples=2 ** 18)
    check(filecmp.cmp(path, path2, shallow=False), 'the same arguments give identical bytes')
    sync.write_noise_only(path2, 2.0, fsn, seed=5005, block_samples=2 ** 18)
    check(not filecmp.cmp(path, path2, shallow=False), 'another seed gives other bytes')
    for p_ in (path, path2):
        os.remove(p_)

    # bounded memory: allocations traced while a 320 MB file is written
    path3 = os.path.join(TMP, 'noise3.cf32')
    tracemalloc.start()
    sync.write_noise_only(path3, 2.0, fsn, seed=1, block_samples=2 ** 16)
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    size = os.path.getsize(path3)
    os.remove(path3)
    check(peak < 20e6 and size > 300e6,
          'a %.0f MB file written with %.1f MB of peak allocations (block 2**16)'
          % (size / 1e6, peak / 1e6))


def check_cli():
    print('\nThe command line and the sets')
    names = [n for n, _ in sync.SETS['sync']]
    check(names == ['hop20_dh1_syncerr_mixed', 'hop20_dh1_syncerr_first1', 'hop20_dh1_syncerr_clean',
                    'noise_only_20msps_120s'], '--set sync has exactly these 4 names: %s' % names)
    here = os.path.dirname(os.path.abspath(__file__))
    r = subprocess.run([sys.executable, '-B', os.path.join(here, 'bt_synth_sync.py'), '--set', 'nope',
                        '--out', TMP], capture_output=True, text=True)
    check(r.returncode != 0, 'an unknown set exits non-zero (%d)' % r.returncode)
    r = subprocess.run([sys.executable, '-B', os.path.join(here, 'bt_synth_sync.py'), 'cli_t',
                        '--bursts', '6', '--errors', 'first1', '--seed', '9', '--out', TMP],
                       capture_output=True, text=True)
    p = os.path.join(TMP, 'synth_cli_t')
    ok = r.returncode == 0 and os.path.exists(p + '.cf32') and os.path.exists(p + '.json')
    if ok:
        side = json.load(open(p + '.json'))
        ok = len(side['bursts']) == 6 and side['bursts'][0]['sync_errors'] == 1 and \
            all(e['sync_errors'] == 0 for e in side['bursts'][1:]) and \
            os.path.getsize(p + '.cf32') == 8 * (62480 + 6 * 50000 + 25000)
        for ext in ('.cf32', '.json'):
            os.remove(p + ext)
    check(ok, 'the single-run command writes both files for first1 with 6 bursts')
    r = subprocess.run([sys.executable, '-B', os.path.join(here, 'bt_synth_sync.py'), 'cli_j',
                        '--noise-only', '--json-only', '--duration-s', '3', '--fs', '20e6',
                        '--out', TMP], capture_output=True, text=True)
    pj = os.path.join(TMP, 'synth_cli_j')
    ok = r.returncode == 0 and not os.path.exists(pj + '.cf32') and os.path.exists(pj + '.json')
    if ok:
        ok = json.load(open(pj + '.json')) == sync.noise_only_sidecar(3.0, 20e6, 5004)
        os.remove(pj + '.json')
    check(ok, '--noise-only --json-only writes the sidecar alone, the one the render would')
    r = subprocess.run([sys.executable, '-B', os.path.join(here, 'bt_synth_sync.py'), 'cli_n',
                        '--noise-only', '--duration-s', '0.1', '--fs', '20e6', '--out', TMP],
                       capture_output=True, text=True)
    p = os.path.join(TMP, 'synth_cli_n')
    ok = r.returncode == 0 and os.path.getsize(p + '.cf32') == 8 * 2_000_000
    if ok:
        side = json.load(open(p + '.json'))
        ok = side['noise_only'] is True and side['bursts'] == [] and side['duration_s'] == 0.1
        for ext in ('.cf32', '.json'):
            os.remove(p + ext)
    check(ok, 'the single-run --noise-only command writes 0.1 s: 2 M samples and a sidecar')


def main():
    os.makedirs(TMP, exist_ok=True)
    iq, side = check_errors('mixed')
    check_geometry(iq, side)
    check_errors('first1')
    check_errors('none')
    check_burst_zero()
    check_set_rows()
    check_fraction()
    check_level()
    check_same_but_bits()
    check_determinism()
    check_sidecar()
    check_noise_only()
    check_cli()
    print('\nRESULT: %s' % ('FAIL (%d)' % len(failures) if failures else 'PASS'))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
