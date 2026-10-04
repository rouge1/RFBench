#!/usr/bin/env python3
"""Hold the stage 2 grader to a recording whose truth is planted.

    python scripts/test_bt_ota_check.py

``scripts/bt_ota_check.py`` grades a recording of the VSG60's burst train and
writes a truth sidecar for it. Nothing about a real recording is known to the
sample, so this builds one: the transmitter's loop, three times over, with a
sample clock off by a chosen number of ppm, so every burst starts at a
fraction of a sample the grader cannot see; two bursts silenced; noise at
25 dB in 1 MHz, about what the bench gave. It then checks what the grader
says against what was planted:

* the clock, and where every burst starts, to a fraction of a sample, and its
  symbol phase, which is what the sidecar is for;
* which bursts were found, and that the silenced ones are in the truth anyway;
* the clock count, and the bits, of every burst;
* the same answer at every chunk size, with a boundary on a burst;
* a recording too short to grade, a fit that is not a clock, and one with no
  whole burst, none of which may crash or hang.
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from scripts import bt_ota_check as ota  # noqa: E402
from scripts import bt_synth, bt_tx  # noqa: E402

FS = bt_tx.FS
failures = []


def check(ok, what):
    print('  %s %s' % ('ok  ' if ok else 'FAIL', what))
    if not ok:
        failures.append(what)


def plant(ppm, origin, loops=3, silent=((1, 3), (1, 7)), snr_db=25.0, seed=7, ptype='DH5'):
    """The loop ``loops`` times from ``origin`` (a float: a fraction of a
    sample), with the clock ``ppm`` off, the ``silent`` (loop, index) bursts
    left out, and noise. Returns the samples, the transmitter's sidecar, and
    every burst's true start."""
    packets, starts, _ = bt_tx.train(ptype)
    side = bt_tx.sidecar(packets, starts, ptype, 0.0, {})
    L = side['loop_samples']
    rng = np.random.default_rng(seed)
    total = int(origin + (loops - 0.4) * L)
    iq = np.zeros(total, dtype=np.complex64)
    truth = {}
    for m in range(loops):
        for k, (p, st) in enumerate(zip(packets, starts)):
            pos = origin + (1 + ppm * 1e-6) * (st + m * L)
            truth[(m, k)] = pos
            if (m, k) in silent:
                continue
            whole = int(np.floor(pos))
            burst, lead = br.gfsk(p.bits, FS, delay=pos - whole)
            lo = whole - lead
            if lo < 0 or lo + len(burst) > total:
                continue
            iq[lo:lo + len(burst)] += (bt_synth.AMPLITUDE * burst
                                       * np.exp(1j * rng.uniform(0, 2 * np.pi))).astype(np.complex64)
    n = np.arange(total)
    iq *= np.exp(2j * np.pi * (bt_tx.CHANNEL_MHZ - bt_tx.CENTER_MHZ) * 1e6 * n / FS).astype(np.complex64)
    sigma = np.sqrt(bt_synth.AMPLITUDE ** 2 / 10 ** (snr_db / 10) * FS / 1e6 / 2)
    iq += (rng.normal(0, sigma, total) + 1j * rng.normal(0, sigma, total)).astype(np.complex64)
    return iq, side, truth


def check_planted():
    print('\nA planted recording: -3 ppm, starts at fractions of a sample')
    ppm, origin = -3.0, 50000.4
    iq, side, truth = plant(ppm, origin)
    L, span = side['loop_samples'], len(side['bursts'][0]['air_bits']) * ota.SPS
    result, rows, fit = ota.grade(iq, side)
    check(fit is not None and abs(result['clock_ppm'] - ppm) < 0.05,
          'clock %.4f ppm, planted %.1f' % (result.get('clock_ppm', float('nan')), ppm))
    truth_sidecar = ota.capture_sidecar(side, len(iq), rows, fit, result, {}, 'planted')
    bursts = truth_sidecar['bursts']
    whole = {key: pos for key, pos in truth.items() if 0 <= pos and pos + span <= len(iq)}
    check(len(bursts) == len(whole), '%d whole bursts listed, %d planted' % (len(bursts), len(whole)))

    by_index = {}
    for b in bursts:
        by_index.setdefault(b['loop_index'], []).append(b)
    ordered = sorted(whole.items(), key=lambda kv: kv[1])
    err = np.array([b['start_exact'] - pos for b, (_, pos) in zip(bursts, ordered)])
    check(np.abs(err).max() < 0.25, 'start_exact within %.2f sample of the true start (mean %+.3f)'
          % (np.abs(err).max(), err.mean()))
    check(all(b['start_sample'] == int(np.floor(b['start_exact'] + 0.5)) for b in bursts),
          'start_sample is the sample nearest start_exact')
    check(max(abs(b['start_sample'] - pos) for b, (_, pos) in zip(bursts, ordered)) <= 1.0,
          'start_sample is within one sample of the true start')
    check(all(b['symbol_phase'] == b['start_sample'] % 20 for b in bursts),
          'symbol_phase is start_sample % 20 on every burst')
    check(len({b['symbol_phase'] for b in bursts}) > 1, 'it is a number per burst, not one for the file')

    silent = {(1, 3), (1, 7)}
    found = {key: b['found'] for (key, _), b in zip(ordered, bursts)}
    check(all(not found[k] for k in silent if k in found), 'the silenced bursts are in the truth, found: false')
    check(all(f for k, f in found.items() if k not in silent), 'every other burst is found')

    m_first = min(m for m, _ in whole)
    check(all(b['clk'] == (side['bursts'][k]['clk'] + side['clk_per_loop'] * (m - m_first)) & 0x0FFFFFFF
              for (( m, k), _), b in zip(ordered, bursts)), 'clk is the loop clock plus 512 a repeat')
    check(all(b['air_bits'] == side['bursts'][k]['air_bits'] for ((_, k), _), b in zip(ordered, bursts)),
          'the air bits are the loop index\'s')
    exact = sum(b.get('libbtbb_exact', False) for b in bursts)
    check(exact >= 0.9 * sum(found.values()), 'libbtbb takes %d of %d found payloads byte for byte'
          % (exact, sum(found.values())))
    check(truth_sidecar['timing_frac'] == 0.0 and truth_sidecar['symbol_phase'] is None,
          'timing_frac 0 for the file, no symbol phase for the file')

    print('\nThe same answer at every chunk size, with a boundary on a burst')
    base = [tuple(r[:3]) for r in rows]
    peak = int(rows[7][0])
    saved = ota.CHUNK
    try:
        for chunk in (1_000_003, peak - 20, peak, peak + 20):
            ota.CHUNK = chunk
            res, got, _ = ota.grade(iq, side)
            check([tuple(r[:3]) for r in got] == base and res['found'] == result['found'],
                  'CHUNK %d: %d found, %d graded, the same rows' % (chunk, res['found'], res['graded']))
    finally:
        ota.CHUNK = saved


def check_edges():
    print('\nA recording that cannot be graded, and a fit that is not a clock')
    packets, starts, _ = bt_tx.train('DH5')
    side = bt_tx.sidecar(packets, starts, 'DH5', 0.0, {})
    for n in (0, 100, 2001, 2500):
        try:
            res, _, fit = ota.grade(np.zeros(n, dtype=np.complex64), side)
            check(res['found'] == 0 and fit is None, '%d samples: nothing found, no crash' % n)
        except Exception as e:           # noqa: BLE001 - any exception is the failure
            check(False, '%d samples raised %r' % (n, e))
    check(ota.capture_sidecar(side, 100, np.zeros((0, 8)), (1.0, 0.0), {}, {}, 'x') is None,
          'no whole burst in 100 samples: no truth sidecar, no crash')

    # 500 ppm is five times what the grader will call a clock: the bursts are
    # found and identified, and the fit through them is refused, not placed.
    iq, side2, _ = plant(500.0, 30000.0, loops=2, silent=())
    res, _, fit = ota.grade(iq, side2)
    check(fit is None and abs(res.get('fit_rejected_ppm', 0) - 500) < 1,
          'a 500 ppm fit is refused (%.1f ppm)' % res.get('fit_rejected_ppm', float('nan')))


def main():
    check_planted()
    check_edges()
    print('\nRESULT: %s' % ('FAIL (%d)' % len(failures) if failures else 'PASS'))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
