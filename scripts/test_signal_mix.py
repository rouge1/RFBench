#!/usr/bin/env python3
"""Check scripts/signal_mix.py: its codes against published values, and its
signals against a decoder that is not ours.

multimon-ng decodes POCSAG, APRS, G3RUH 9600, DTMF and SAME from the FM
discriminator of each file, as it did before every round that used them. Its
lines are skipped, and say so, where multimon-ng is not installed.

    python scripts/test_signal_mix.py
"""
import os
import shutil
import subprocess
import sys

import numpy as np
from scipy.signal import resample_poly

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import signal_mix as sm  # noqa: E402

failures = []


def check(name, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: {got!r}" + ('' if ok else f" (want {want!r})"))
    if not ok:
        failures.append(name)


print("codes against published values")
check("CRC-16/CCITT-FALSE of '123456789'", hex(sm.crc16(b'123456789')), '0x29b1')
check("X.25 FCS of '123456789'", hex(sm.fcs16(b'123456789')), '0x906e')
frame = int('8D4840D6202CC371C32CE0576098', 16)
check("ADS-B identification for KLM1023 is the textbook frame",
      sm.adsb_bits(0x4840D6, 'KLM1023'), [(frame >> (111 - i)) & 1 for i in range(112)])
check("POCSAG idle word is a BCH codeword", hex(sm.bch(sm.IDLE >> 11)), hex(sm.IDLE))
check("so is the sync word", hex(sm.bch(sm.SYNC >> 11)), hex(sm.SYNC))

print("\ntiming")
FS = 500e3
burst = sm.hell({'text': 'CQ'}, FS, None)
cols = len(burst) / FS * 245 / 14
check("Feld-Hell sends whole 14-pixel columns at 17.5 a second", abs(cols - round(cols)) < 1e-3, True)
dot = 1.2 / 20
m = sm.morse({'text': 'E', 'wpm': 20}, FS, np.random.default_rng(1))
check("Morse 'E' at 20 wpm is one dot and one space", round(len(m) / FS / dot, 3), 2.0)

print("\nCSS symbols come back by dechirp and FFT")
SF, BW = 7, 125e3
syms = [0, 1, 64, 127, 5]
x = sm.css({'sf': SF, 'bw_hz': BW, 'symbols': syms}, 4 * BW, None)
n = 2 ** SF * 4
down = np.conj(x[:n])                       # the first preamble upchirp
start = int(12.25 * n)
got = [int(np.argmax(np.abs(np.fft.fft((x[start + k * n:start + (k + 1) * n] * down)[::4]))))
       for k in range(len(syms))]
check("symbols", got, syms)

print("\nbuild() refuses a sum over full scale")
try:
    sm.build({'fs': FS, 'length_s': 0.01, 'items': [
        {'kind': 'cw', 'amp': 0.6, 'offset_hz': 0}, {'kind': 'cw', 'amp': 0.6, 'offset_hz': 0}]})
    check("raised", False, True)
except ValueError:
    check("raised", True, True)


def multimon(items, mode, fs=FS, length_s=None):
    """FM-demodulate a built spec to 22050 Hz audio and hand it to multimon-ng."""
    spec = {'fs': fs, 'length_s': length_s, 'seed': 1, 'items': items}
    x, _ = sm.build(spec)
    rng = np.random.default_rng(2)
    x = x + 0.01 * (rng.normal(size=len(x)) + 1j * rng.normal(size=len(x)))
    disc = np.angle(x[1:] * np.conj(x[:-1]))
    audio = resample_poly(disc, 441, int(fs) // 50)        # to 22050 Hz
    audio = (audio / np.abs(audio).max() * 0.7 * 32767).astype(np.int16)
    r = subprocess.run(['multimon-ng', '-q', '-t', 'raw', '-a', mode, '-'],
                       input=audio.tobytes(), capture_output=True, timeout=120)
    return r.stdout.decode(errors='replace')


print("\nmultimon-ng decodes them")
if not shutil.which('multimon-ng'):
    print("  SKIP multimon-ng is not installed")
else:
    out = multimon([{'kind': 'pocsag', 'amp': 0.9, 'offset_hz': 0, 'start_s': 0.1, 'text': 'HELLO BENCH',
                     'address': 1234567, 'function': 3, 'baud': 1200, 'count': 1, 'every_s': 1}],
                   'POCSAG1200', length_s=2)
    check("POCSAG1200", 'Address: 1234567' in out and 'HELLO BENCH' in out, True)
    out = multimon([{'kind': 'aprs', 'amp': 0.9, 'offset_hz': 0, 'start_s': 0.1, 'src': 'N0CALL-9',
                     'dst': 'APRS', 'path': ['WIDE1-1'], 'info': '>bench test', 'dev_hz': 3000}],
                   'AFSK1200', length_s=2)
    check("AFSK1200 (APRS)", 'N0CALL-9' in out and 'bench test' in out, True)
    out = multimon([{'kind': 'g3ruh', 'amp': 0.9, 'offset_hz': 0, 'start_s': 0.05, 'src': 'N0CALL',
                     'dst': 'CQ', 'info': 'nine six', 'dev_hz': 3000}],
                   'FSK9600', length_s=0.5)
    check("FSK9600 (G3RUH)", 'N0CALL' in out and 'nine six' in out, True)
    out = multimon([{'kind': 'dtmf_fm', 'amp': 0.9, 'offset_hz': 0, 'start_s': 0.1, 'digits': '159#',
                     'tone_s': 0.1, 'gap_s': 0.1, 'dev_hz': 3000}], 'DTMF', length_s=1.2)
    check("DTMF", ''.join(l.split()[-1] for l in out.splitlines() if l.startswith('DTMF')), '159#')
    hdr = 'ZCZC-WXR-RWT-020103-020209+0030-3040858-KEAX/NWS-'
    out = multimon([{'kind': 'same', 'amp': 0.9, 'offset_hz': 0, 'start_s': 0.1, 'header': hdr,
                     'tone_s': 1.0, 'dev_hz': 3000}], 'EAS', length_s=12)
    check("EAS (SAME)", hdr in out, True)

print("RESULT:", "PASS" if not failures else f"FAIL ({', '.join(failures)})")
