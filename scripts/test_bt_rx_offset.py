"""Tests of scripts/bt_rx_offset.py: a receiver's frequency and clock error put on a delivered file.

Short files (0.5 s) of the slotb2 and endlen generators are written to a temporary folder at a high SNR and processed;
the checks read the samples back:

* identity: no error, no spur -> the same bytes, and ``cfo_observed_hz`` = ``cfo_hz``;
* rotation only: the output is the input times ``exp(+j 2 pi f n / fs)`` with the absolute index ``n`` (bluey's
  ``oracle_shift``), positions unchanged, ``cfo_observed_hz`` = ``cfo_hz`` + f;
* the sign: +23.8 ppm reads about -58.1 kHz at 2441 MHz;
* a clock error: every burst refitted from the samples sits at the rewritten ``start_sample + timing_frac`` (and a truth
  NOT rewritten fails the same check), the bits decode, the measured carrier is ``cfo_observed_hz`` (alone and paired
  against the source file), IDs end where ``id_end_sample`` says, endlen's ``end_sample`` is scaled;
* the noise: the same in band, the spur at the level asked for and at 0 Hz;
* a sidecar of another generator is refused; the manifest and the checksums.

    python -B scripts/test_bt_rx_offset.py
"""
import hashlib
import json
import math
import os
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import bt_rx_offset as rx  # noqa: E402
from scripts import bt_synth_endlen as ge  # noqa: E402
from scripts import bt_synth_slotb2 as g  # noqa: E402
from scripts import test_bt_synth_slotb2 as t2  # noqa: E402  (check, fit, measured_cfo, check_decode, check_ids)

check, quiet, RESULTS = t2.check, t2.quiet, t2.RESULTS
STEM = 'S_0466AB_c0_p0_n1'
DUR = 0.5


def observed_view(side):
    """The sidecar as the receiver's truth: each burst's carrier is ``cfo_observed_hz`` (what the fit models)."""
    v = json.loads(json.dumps(side))
    for b in v['bursts']:
        b['cfo_hz'] = b['cfo_observed_hz']
    return v


def load(path):
    return np.fromfile(path, dtype=np.complex64), json.load(open(path[:-5] + '.json'))


def inband_power(x, fs, half_bw):
    X = np.fft.fft(x)
    f = np.fft.fftfreq(len(x), 1 / fs)
    return float(np.sum(np.abs(X[np.abs(f) < half_bw]) ** 2) / len(x) ** 2)


def positions(iq, side, label, n=24, tol=0.1):
    """Refit bursts and compare with the truth.  Under a clock error a burst is also LONGER in receiver samples by
    ``eps`` times its length, and the fit's model is the unstretched burst: its best start lies about half the stretch
    late, so the tolerance carries ``eps * length / 2``."""
    eps = abs(rx.eps_of(side.get('receiver', {}).get('rx_ppm', 0.0)))
    sps = side['sample_rate'] / 1e6
    picks = t2.spread([e for e in t2.rendered(side) if e['ptype'] != 'AUX1'], n)
    errs, tols = [], []
    for e in picks:
        errs.append(t2.fit(iq, side, e)['start'] - (e['start_sample'] + e['timing_frac']))
        tols.append(tol + 0.5 * eps * len(t2.model_bits(e)) * sps)
    check(len(picks) >= 10 and all(abs(x) < t for x, t in zip(errs, tols)),
          '%s: %d bursts refitted from the samples sit at start_sample + timing_frac within %g sample plus half the burst\'s '
          'stretch (worst %.3f, largest allowance %.3f)' % (label, len(picks), tol, max(abs(x) for x in errs), max(tols)))


def main():
    t0 = time.time()
    with tempfile.TemporaryDirectory() as td:
        src_dir, out = os.path.join(td, 'src'), os.path.join(td, 'out')
        with quiet():
            src, _ = g.write_file(STEM, src_dir, duration=DUR, snr_db=t2.HIGH)
        x, side = load(src[:-5] + '.cf32' if src.endswith('.json') else src)
        fs, fc = side['sample_rate'], side['center_mhz'] * 1e6

        print('== identity and the sign', flush=True)
        p = rx.write(src, out)
        y, s = load(p)
        check(np.array_equal(x, y) and [b['start_sample'] for b in s['bursts']] == [b['start_sample'] for b in side['bursts']] and
              all(b['cfo_observed_hz'] == b['cfo_hz'] for b in s['bursts']) and s['n_samples'] == len(x),
              'identity: no error and no spur give the same bytes, the same positions, cfo_observed_hz = cfo_hz')
        o = rx.observed_cfo_hz(0.0, 2441.0, 2441.0, 23.8)
        check(abs(o + 58094.4) < 1.0 and rx.observed_cfo_hz(-30e3, 2441.0, 2439.0, 23.8) < -88e3,
              'sign: +23.8 ppm (receiver FAST) reads %.1f Hz at 2441 MHz; -30 kHz transmitter reads about -88 kHz' % o)

        print('== rotation only (-58 kHz)', flush=True)
        p = rx.write(src, out, rx_offset_hz=-58e3)
        y, s = load(p)
        n = np.arange(len(x), dtype=np.float64)
        ref = (x * np.exp(2j * np.pi * -58e3 * n / fs)).astype(np.complex64)
        check(len(y) == len(x) and float(np.abs(y - ref).max()) < 1e-5,
              'rotation: the output is the input times exp(+j 2 pi f n / fs), absolute n (bluey\'s oracle_shift), worst %.1e' %
              float(np.abs(y - ref).max()))
        check([(b['start_sample'], b['timing_frac']) for b in s['bursts']] == [(b['start_sample'], b['timing_frac']) for b in side['bursts']] and
              all(abs(b['cfo_observed_hz'] - (b['cfo_hz'] - 58e3)) < 1e-6 for b in s['bursts']) and
              all(b['cfo_hz'] == a['cfo_hz'] for a, b in zip(side['bursts'], s['bursts'])),
              'rotation: positions unchanged, cfo_observed_hz = cfo_hz - 58 kHz, cfo_hz unchanged')
        sv = observed_view(s)
        meas = [t2.measured_cfo(y, sv, e) for e in t2.spread(t2.rendered(sv), 8)]
        check(all(abs(m - e['cfo_observed_hz']) < 1000 for m, e in zip(meas, t2.spread(t2.rendered(sv), 8))),
              'rotation: the carrier measured from the samples of 8 bursts is cfo_observed_hz within 1 kHz')

        print('== a clock error (+23.8 ppm) and a DC spur', flush=True)
        p = rx.write(src, out, rx_ppm=23.8, dc_spur_db=10.0)
        y, s = load(p)
        e_ = 23.8e-6
        check(len(y) == rx.out_length(len(x), 23.8) == s['n_samples'] and len(y) > len(x),
              'ppm: %d samples from %d (the file is longer in receiver samples)' % (len(y), len(x)))
        sv = observed_view(s)
        positions(y, sv, 'ppm')
        stale = json.loads(json.dumps(sv))
        for a, b in zip(side['bursts'], stale['bursts']):
            b['start_sample'], b['timing_frac'] = a['start_sample'], a['timing_frac']
        late = dict(stale, bursts=[b for b in stale['bursts'] if b['start_sample'] > 0.3 * len(x)])
        with quiet() as failed:
            positions(y, late, 'stale truth')
        check(failed, 'ppm: the same check fails on the source\'s positions (late bursts move by up to %.0f samples)' % (len(x) * e_))
        picks = t2.spread(t2.rendered(sv), 8)
        src_side = observed_view(rx.rewrite_sidecar(side))
        m_y = [t2.measured_cfo(y, sv, e) for e in picks]
        m_x = [t2.measured_cfo(x, src_side, src_side['bursts'][e['index']]) for e in picks]
        want = [e['cfo_observed_hz'] - side['bursts'][e['index']]['cfo_hz'] for e in picks]
        check(all(abs(a - e['cfo_observed_hz']) < 1000 for a, e in zip(m_y, picks)) and
              all(abs((a - b) - w) < 300 for a, b, w in zip(m_y, m_x, want)),
              'ppm: the measured carrier is cfo_observed_hz within 1 kHz; against the source, the change is the predicted '
              '%.0f..%.0f Hz within 300 Hz' % (min(want), max(want)))
        t2.check_decode(y, sv, 'ppm', n=10)
        t2.check_ids(y, sv, 'ppm', n=8)
        lead = int(0.015 * fs)
        dc = complex(np.mean(y[:lead]))
        amp = math.sqrt(side['noise_1mhz'] * 10.0)
        check(abs(abs(dc) - amp) < 0.05 * amp,
              'spur: the mean of the noise-only lead-in is the spur, %.4g against %.4g (10 dB over noise_1mhz)' % (abs(dc), amp))
        pi_x = inband_power(x[:lead], fs, 0.4 * fs)
        pi_y = inband_power(y[:lead] - dc, fs, 0.4 * fs)
        check(abs(10 * math.log10(pi_y / pi_x)) < 0.05,
              'noise: the in-band (+-%.0f MHz) noise of the lead-in is the source\'s within 0.05 dB (%.3f dB)' % (
                  0.4 * fs / 1e6, 10 * math.log10(pi_y / pi_x)))
        check(s['receiver']['rx_ppm'] == 23.8 and s['receiver']['dc_spur_db'] == 10.0 and s['receiver']['interpolator']['taps'] == rx.TAPS and
              all(b['start_us'] == a['start_us'] for a, b in zip(side['bursts'], s['bursts'])) and
              abs(s['first_id_sample'] - side['first_id_sample'] * (1 + e_)) < 1e-6,
              'ppm sidecar: the receiver block, *_us unchanged (true time), top-level *_sample scaled')

        check(all(b['symbol_phase'] == int(b['start_sample'] % 40) for b in s['bursts']) and
              s['lead_in_samples'] == side['lead_in_samples'] * (1 + e_) and 'cfo_observed_hz' in s['per_burst_keys'] and
              s['id_end_note'].endswith('(1 + eps) times as many]'),
              'ppm sidecar: symbol_phase = int(start_sample % 40) as the generator defines it, lead_in_samples scaled, '
              'cfo_observed_hz listed, the source\'s formula notes marked')

        print('== the formula off the centre channel, measured on a long tone', flush=True)
        # an independent check of cfo_observed_hz where the eps (f_ch - fc) term is not zero: a pure tone at the
        # baseband frequency of channel 2449 MHz + cfo in a 2439 MHz capture, processed, its frequency measured from the
        # phase slope over 0.1 s, less the nominal channel offset
        f_ch, fc0, cfo, off_hz, ppm = 2449e6, 2439e6, -30e3, 5e3, 23.8
        nn = 4_000_000
        tone = np.exp(2j * np.pi * ((f_ch - fc0 + cfo) / 40e6) * np.arange(nn)).astype(np.complex64)
        yt = np.concatenate(list(rx.process(tone, ppm, off_hz, 40e6, fc0)))[1000:-1000]
        ph = np.unwrap(np.angle(yt.astype(np.complex128) * np.exp(-2j * np.pi * (f_ch - fc0) / 40e6 * np.arange(1000, 1000 + len(yt)))))
        meas = np.polyfit(np.arange(len(ph)) / 40e6, ph, 1)[0] / (2 * np.pi)
        want = rx.observed_cfo_hz(cfo, f_ch / 1e6, fc0 / 1e6, ppm, off_hz)
        naive = cfo + off_hz - ppm * 1e-6 * fc0            # what dropping the channel term would give
        check(abs(meas - want) < 2.0 and abs(want - naive) > 200,
              'formula: a tone at 2449 MHz in a 2439 MHz capture, +23.8 ppm, +5 kHz offset, reads %.2f Hz against %.2f from '
              'cfo_observed_hz (the eps (f_ch - fc) term is %.0f Hz of it)' % (meas, want, want - naive))

        print('== a SLOW receiver and an offset together (-23.8 ppm, +20 kHz)', flush=True)
        p = rx.write(src, out, rx_ppm=-23.8, rx_offset_hz=20e3)
        y, s = load(p)
        check(len(y) == rx.out_length(len(x), -23.8) < len(x), 'slow: %d samples from %d (shorter)' % (len(y), len(x)))
        sv = observed_view(s)
        positions(y, sv, 'slow + offset')
        picks = t2.spread(t2.rendered(sv), 8)
        check(all(abs(t2.measured_cfo(y, sv, e) - e['cfo_observed_hz']) < 1000 for e in picks) and
              all(abs(e['cfo_observed_hz'] - (side['bursts'][e['index']]['cfo_hz'] + 20e3 + 23.8e-6 * e['channel_mhz'] * 1e6)) < 5 for e in picks),
              'slow + offset: the measured carrier is cfo_observed_hz (about cfo + 20 kHz + 58 kHz) within 1 kHz')
        t2.check_decode(y, sv, 'slow + offset', n=8)

        print('== endlen, refusal, manifest', flush=True)
        with quiet():
            esrc, _ = ge.write_file('E_mix_s1', os.path.join(td, 'esrc'), duration=DUR, snr_db=t2.HIGH)
        ep = rx.write(esrc, out, rx_ppm=23.8)
        ey, es = load(ep)
        ex, eside = load(esrc)
        check(all(abs(b['end_sample'] - a['end_sample'] * (1 + e_)) < 1e-6 for a, b in zip(eside['bursts'], es['bursts'])),
              'endlen: end_sample is scaled with the start')
        # the slotb2 helper's model of an empty endlen DH1 is off by up to ~2 samples on the source file itself, so the
        # endlen positions are checked paired: the refit on the processed file against the same refit on the source, scaled
        ev, sv0 = observed_view(es), observed_view(rx.rewrite_sidecar(eside))
        picks = t2.spread([e for e in t2.rendered(ev) if e['ptype'] != 'AUX1'], 16)
        d = [t2.fit(ey, ev, e)['start'] - t2.fit(ex, sv0, sv0['bursts'][e['index']])['start'] * (1 + e_) for e in picks]
        allow = [0.1 + 0.5 * e_ * len(t2.model_bits(e)) * eside['sample_rate'] / 1e6 for e in picks]
        check(len(picks) >= 10 and all(abs(a) < b for a, b in zip(d, allow)),
              'endlen ppm: %d bursts refitted on the processed file sit where the same refit on the source puts them, scaled '
              'by 1 + eps (worst %.3f sample)' % (len(picks), max(abs(a) for a in d)))
        bad = dict(side, generator='SDR scripts/bt_synth_mixed.py')
        try:
            rx.rewrite_sidecar(bad, 23.8)
            refused = False
        except ValueError:
            refused = True
        check(refused, 'a sidecar of another generator (bt_synth_mixed) is refused')
        rx.write_manifest(out)
        man = json.load(open(os.path.join(out, 'manifest.json')))
        sums = dict(reversed(l.split('  ', 1)) for l in open(os.path.join(out, 'SHA256SUMS.txt')).read().split('\n') if l)
        check(len(man['files']) == 5 and len(sums) == 10 and
              all(sums[r['file']] == hashlib.sha256(open(os.path.join(out, r['file']), 'rb').read()).hexdigest() == r['sha256_cf32']
                  for r in man['files']),
              'manifest.json and SHA256SUMS.txt over the 5 processed files')
    failed = [w for ok, w in RESULTS if not ok]
    print('\n%d checks, %.0f s' % (len(RESULTS), time.time() - t0))
    for w in failed:
        print('FAILED:', w)
    print('RESULT: PASS' if not failed else 'RESULT: FAIL (%d)' % len(failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
