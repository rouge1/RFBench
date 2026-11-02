#!/usr/bin/env python3
"""Check scripts/lora_phy.py: pinned values, round trips, and the chirps.

The golden symbol lists were checked against gr-lora_sdr's own transmit chain
(672 of 672 symbols identical, see knowledge/lora.md), and the encoder against
captures of a real SX127x. Those need the `lora` conda env and docs/lora/, so
what runs here is only what needs numpy.

    python scripts/test_lora_phy.py
"""
import itertools
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lora_phy as L  # noqa: E402

failures = []


def check(name, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ('' if ok else f": {got!r} (want {want!r})"))
    if not ok:
        failures.append(name)


check('CRC-16/XMODEM "123456789"', L.crc16(b'123456789'), 0x31C3)
check('whitening starts 0xFF 0xFE', L.WHITEN[:2], [0xFF, 0xFE])
check('SF7 CR4/5 "hello lora"', L.encode(b'hello lora', 7, 1),
      [97, 9, 1, 49, 25, 97, 1, 121, 54, 125, 33, 71, 56, 11, 34, 120, 85, 125, 56, 87,
       108, 96, 100, 5, 4, 65, 1, 16])
check('SF9 CR4/8 "hello lora"', L.encode(b'hello lora', 9, 4),
      [353, 69, 61, 305, 145, 113, 253, 13, 334, 449, 25, 151, 406, 315, 10, 388, 448,
       182, 345, 379, 293, 450, 13, 34, 9, 505, 258, 1, 128, 192, 49, 56])

# every symbol is a bin, and decode inverts encode
rng = np.random.default_rng(1)
bad = 0
for sf, cr, crc, ldro in itertools.product(range(7, 13), (1, 2, 3, 4), (True, False), (False, True)):
    for n in (2, 5, 23):
        p = bytes(rng.integers(0, 256, n).tolist())
        syms = L.encode(p, sf, cr, has_crc=crc, ldro=ldro)
        got, info, ok = L.decode_symbols(syms, sf, ldro=ldro)
        if (got != p or not info['checksum_ok'] or (crc and not ok)
                or not all(0 <= s < 1 << sf for s in syms)):
            bad += 1
check('decode(encode(p)) over SF7-12, CR1-4, CRC, LDRO', bad, 0)

# implicit header: the receiver is told length, CR and CRC
p = b'implicit'
syms = L.encode(p, 8, 2, has_crc=True, implicit=True)
got, _, ok = L.decode_symbols(syms, 8, cr=2, has_crc=True, length=len(p), implicit=True)
check('implicit header round trip', (got, ok), (p, True))

# a flipped payload bit breaks the CRC check
syms = L.encode(b'abcdef', 7, 1)
syms[14] ^= 0x10
_, _, ok = L.decode_symbols(syms, 7)
check('a corrupted payload symbol fails the CRC', ok is True, False)

# padding: the chip's fixed nibbles change the last block only
base = L.encode(b'ab', 7, 1)
padded = L.encode(b'ab', 7, 1, pad=[3, 2, 0, 13, 5, 14, 6, 14, 14, 6, 2] * 4)
check('padding changes the symbols', base != padded, True)
check('padding keeps the length', len(base), len(padded))

# a modulated frame dechirps to its symbols
sf = 8
N = 1 << sf
syms = L.encode(b'chirp', sf, 1)
x = L.modulate(syms, sf, os=1)
down = np.conj(L.chirp(0, sf))
start = 8 * N + 2 * N + 2 * N + N // 4          # preamble, sync word, 2.25 down-chirps
got = [(int(np.argmax(np.abs(np.fft.fft(x[start + i * N:start + (i + 1) * N] * down))))) for i in range(len(syms))]
check('dechirp recovers the symbols', got, syms)

print('FAILED: ' + ', '.join(failures) if failures else 'all ok')
sys.exit(1 if failures else 0)
