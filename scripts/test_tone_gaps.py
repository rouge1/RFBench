#!/usr/bin/env python3
"""``tone_gaps`` finds planted faults in a recorded tone, and only those - no radio.

    python -B scripts/test_tone_gaps.py

Synthetic recordings at 20 MS/s, a tone at +1,000,037 Hz with noise, 1.6 s:

* clean: no dropout, no jump, the tone's frequency within 1 Hz;
* 437 samples lost at 0.90 s (21.85 cycles): a phase jump there;
* 2 ms zeroed at 1.20 s: dropouts there, and nothing else flagged elsewhere;
* a second, stronger tone 3 MHz away: ``--near`` measures the right one, clean;
* the same clean tone as UHD sc16: the same answer;
* the blind spot the docstring warns of: at the bench's own 1,000,037 Hz, 500 lost samples are 25.0009 cycles
  and no jump is seen - pinned here so a change that claims to see it is noticed.
"""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tone_gaps  # noqa: E402

R = 20e6
failures = []


def check(ok, what):
    print('  %s %s' % ('ok  ' if ok else 'FAIL', what), flush=True)
    if not ok:
        failures.append(what)


def tone(f, secs=1.6, amp=0.5, seed=1):
    rng = np.random.default_rng(seed)
    n = int(secs * R); t = np.arange(n) / R
    x = amp * np.exp(2j * np.pi * f * t) + 0.01 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    return x.astype(np.complex64)


def run(x, d, name, fmt='cf32', **kw):
    p = os.path.join(d, name)
    if fmt == 'cf32':
        x.astype(np.complex64).tofile(p)
    else:
        v = np.empty(2 * x.size, np.int16); v[0::2] = np.round(x.real * 20000); v[1::2] = np.round(x.imag * 20000)
        v.tofile(p)
    n, get = tone_gaps._reader(p, fmt)
    return tone_gaps.analyse(n, get, R, **kw)


def main():
    F = 1_000_037.0
    with tempfile.TemporaryDirectory() as d:
        print('clean')
        r = run(tone(F), d, 'clean.cf32')
        check(not r['dropouts_s'] and not r['jumps_s'], f"nothing flagged ({len(r['dropouts_s'])}, {len(r['jumps_s'])})")
        check(abs(r['tone_hz'] - F) < 1, f"tone at {r['tone_hz']:.2f} Hz of {F:.0f}")

        print('437 samples lost at 0.90 s')
        x = np.delete(tone(F), slice(int(0.9 * R), int(0.9 * R) + 437))
        r = run(x, d, 'lost.cf32')
        check(len(r['jumps_s']) >= 1 and all(abs(t - 0.9) < 0.001 for t in r['jumps_s']),
              f"a jump at 0.900 s and nowhere else: {[round(t, 4) for t in r['jumps_s']]}")
        check(not r['dropouts_s'], "no dropout")

        print('2 ms zeroed at 1.20 s')
        x = tone(F); x[int(1.2 * R): int(1.202 * R)] = 0
        r = run(x, d, 'gap.cf32')
        check(len(r['dropouts_s']) >= 30 and all(1.1995 < t < 1.2025 for t in r['dropouts_s']),
              f"{len(r['dropouts_s'])} dropout blocks, all in 1.200-1.202 s")
        check(all(1.1995 < t < 1.2025 for t in r['jumps_s']), "any jumps flagged are inside the gap too")

        print('a second, stronger tone 3 MHz away')
        x = tone(F) + tone(-2e6, amp=1.0, seed=2)
        r = run(x, d, 'two.cf32', near=1e6)
        check(abs(r['tone_hz'] - F) < 1 and not r['dropouts_s'] and not r['jumps_s'],
              f"--near measures {r['tone_hz']:.2f} Hz, clean")

        print('UHD sc16')
        r = run(tone(F), d, 'clean.sc16', fmt='sc16')
        check(abs(r['tone_hz'] - F) < 1 and not r['dropouts_s'] and not r['jumps_s'],
              f"tone at {r['tone_hz']:.2f} Hz, clean")

        print('the blind spot: 1,000,037 Hz, 500 samples lost')
        x = np.delete(tone(F), slice(int(0.9 * R), int(0.9 * R) + 500))
        r = run(x, d, 'blind.cf32')
        check(not r['jumps_s'], f"no jump seen, as documented ({len(r['jumps_s'])})")
    print()
    if failures:
        print(f"{len(failures)} FAILED: {', '.join(failures)}")
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
