#!/usr/bin/env python3
"""Check scripts/dmr_demod.py on synthetic DMR only (no recording is used here).

The Golay(20,8) code against its stated distance, the SYNC table's voice/data negation,
then a base-station stream - a voice call on one slot, CSBKs on the other, CACH between -
modulated, shifted off-channel with a carrier error and noise, and demodulated: every
voice frame and every slot type must come back.

    python scripts/test_dmr_demod.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dmr_demod as D  # noqa: E402

failures = []


def check(name, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ('' if ok else f": {got!r} (want {want!r})"))
    if not ok:
        failures.append(name)


# Golay(20,8): systematic, minimum distance 7, corrects 3
cw = D._CODEWORDS
check('Golay is systematic', all(cw[v][:8].tolist() == [(v >> (7 - i)) & 1 for i in range(8)]
                                 for v in range(256)), True)
dmin = min(int(np.count_nonzero(cw[a] != cw[b])) for a in range(256) for b in range(a + 1, 256))
check('Golay(20,8) minimum distance 8, counted from Table B.11 (the text says 7)', dmin, 8)
rng = np.random.default_rng(3)
bad = 0
for _ in range(500):
    v = int(rng.integers(256))
    c = np.array(D.golay2008_encode(v))
    e = rng.choice(20, int(rng.integers(0, 4)), replace=False)
    c[e] ^= 1
    if D.golay2008_decode(c) != (v, len(e)):
        bad += 1
check('Golay corrects up to 3 errors', bad, 0)

# every data SYNC is the negation of its voice SYNC
for a, b in (('BS_VOICE', 'BS_DATA'), ('MS_VOICE', 'MS_DATA'),
             ('TDMA1_VOICE', 'TDMA1_DATA'), ('TDMA2_VOICE', 'TDMA2_DATA')):
    check(f'{b} = -{a}', (D.sync_symbols(b) == -D.sync_symbols(a)).all(), True)

# bits <-> symbols
b = rng.integers(0, 2, 264).tolist()
check('dibit map round trip', D.symbols_to_bits(D.bits_to_symbols(b)), b)

# a base-station stream: slot 1 carries a voice call (2 superframes), slot 2 CSBKs, CC 7
CC = 7
frames = [rng.integers(0, 2, 72).tolist() for _ in range(36)]
bursts = []
for sf in range(2):
    for k in range(6):
        fr = frames[sf * 18 + 3 * k: sf * 18 + 3 * k + 3]
        centre = D.hex_bits(D.SYNCS['BS_VOICE']) if k == 0 else rng.integers(0, 2, 48).tolist()
        bursts.append(D.voice_burst(fr, centre))
        bursts.append(D.data_burst(rng.integers(0, 2, 196).tolist(), CC, 3))      # CSBK
lead = [D.data_burst(rng.integers(0, 2, 196).tolist(), CC, 9) for _ in range(4)]  # Idle
stream = []
for bb in lead + bursts + lead:
    stream += rng.integers(0, 2, 24).tolist() + bb          # 24-bit CACH, then the burst
stream += rng.integers(0, 2, 200).tolist()                 # the receiver needs a tail
syms = D.bits_to_symbols(stream)

FS1 = 960_000                  # a whole number of samples a symbol (200)
x = D.modulate(syms, FS1)
off, cfo = 120_000.0, 310.0
n = np.arange(len(x))
x = x * np.exp(2j * np.pi * (off + cfo) * n / FS1)
x = x + 0.5 * np.exp(2j * np.pi * -150_000 * n / FS1)                    # a carrier nearby
x = x + 0.05 * (rng.normal(size=len(x)) + 1j * rng.normal(size=len(x)))
x = x * np.exp(-2j * np.pi * off * n / FS1)                              # tune to the channel

d = D.discriminate(x, FS1)
calls = D.voice_calls(d)
got = [f for _, fr in calls for f in fr]
check('voice superframes found', len(calls), 2)
check('all 36 voice frames bit-exact', got == frames, True)
ambe = D.frames_to_ambe(got)
check('.ambe bytes round trip', D.ambe_to_frames(ambe).tolist() == [list(f) for f in got], True)
db = D.data_bursts(d)
check('data bursts found', len(db), 12 + 8)
check('colour codes', {cc for _, cc, _, _ in db}, {CC})
check('data types', sorted({D.DATA_TYPES[dt] for _, _, dt, _ in db}), ['CSBK', 'Idle'])

# the silence frame is a 72-bit frame in the .ambe byte form
sil = bytes.fromhex('B9E881526173002A6B')
check('silence frame is 72 bits', D.ambe_to_frames(sil).shape, (1, 72))

print('FAILED: ' + ', '.join(failures) if failures else 'all ok')
sys.exit(1 if failures else 0)
