#!/usr/bin/env python3
"""Check scripts/dmr_fec.py and scripts/dmr_synth.py.

1. The BPTC(196,96) transmit order against Table E.1 of TS 102 361-1, read from the
   spec text when docs/ptt/dmr/text/ is present (it is git-ignored).
2. The codes against their own definitions: RS(12,9) codewords have a, a^2, a^3 as
   roots; every Hamming row and column of an encoded BPTC checks; the embedded LC and
   the EMB decode to what went in; the Null embedded message is all zeros (Table D.1).
3. Whole calls - MS-sourced and direct mode, clean, and at 1 MS/s off-channel with a
   carrier error and noise - demodulated by scripts/dmr_demod.py: every voice frame,
   the slot types, the header's and terminator's LC (RS check passing) and the
   embedded LC of every superframe must come back.
4. With --real DISC.npy (a 48 kS/s FM-discriminator output of a real DMR call, BS
   sourced, not in the repository): its headers, terminator and voice centre fields
   must re-encode bit for bit.

    python scripts/test_dmr_synth.py [--real DISC.npy]
"""
import argparse
import json
import os
import re
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dmr_demod as D  # noqa: E402
import dmr_fec as F  # noqa: E402
import dmr_synth as S  # noqa: E402

SPEC = os.path.join(HERE, '..', 'docs', 'ptt', 'dmr', 'text',
                    'ETSI_TS_102_361-1_v2.5.1_air_interface_protocol.txt')
failures = []


def check(name, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {name}"
          + ('' if ok else f": {repr(got)[:200]} (want {repr(want)[:200]})"))
    if not ok:
        failures.append(name)


def spec_table_e1():
    txt = open(SPEC).read()
    i = txt.index('Table E.1: Transmit bit order for BPTC general data burst with SYNC',
                  txt.index('Annex E'))
    seg = txt[i:txt.index('Table E.2', i)]
    cells = {(m[1], int(m[2])): (m[3], m[4])
             for m in re.finditer(r'\b([LR])(\d+)\s+(\S+)\s+(\S+)', seg)}
    order = [('L', n) for n in range(66, 0, -1)] + [('R', n) for n in range(1, 67)]
    names = [b for k in order for b in cells[k]]
    return names[:98] + names[166:]


print('BPTC(196,96) against Table E.1')
if os.path.exists(SPEC):
    check('all 196 bit positions', F.bptc196_layout(), spec_table_e1())
else:
    print('  skip (no spec text)')

print('codes')
rng = np.random.default_rng(7)
for t in range(20):
    lc = rng.integers(0, 2, 72).tolist()
    octs = F.lc_octets(lc)
    if F.rs129_syndromes(octs + F.rs129_parity(octs)) != [0, 0, 0]:
        check(f'RS(12,9) roots, trial {t}', 'nonzero', 'zero')
        break
    for dt in F.CRC_MASK:
        info = F.lc_info96(lc, dt)
        back, bad = F.bptc196_decode(F.bptc196_encode(info))
        got, ok = F.check_info96(back, dt)
        if (back, bad, got, ok) != (info, 0, lc, True):
            check(f'BPTC + RS round trip, {dt}, trial {t}', False, True)
        other = [k for k in F.CRC_MASK if k != dt][0]
        if F.check_info96(back, other)[1]:
            check(f'wrong mask fails, trial {t}', True, False)
    back, chk = F.embedded_lc_decode(F.embedded_lc(lc))
    if (back, chk) != (lc, {'bad_rows': 0, 'bad_cols': 0, 'checksum_ok': True}):
        check(f'embedded LC round trip, trial {t}', False, True)
check('RS, BPTC, masks and embedded LC on 20 random LCs', True, True)
check('EMB decodes to what went in',
      all(F.emb_decode(F.emb(cc, l, pi)) == (cc, pi, l, 0)
          for cc in range(16) for l in range(4) for pi in (0, 1)), True)
d_min = min(sum(a != b for a, b in zip(F._EMB_WORDS[u], F._EMB_WORDS[v]))
            for u in range(128) for v in range(u))
check('QR(16,7) minimum distance 6', d_min, 6)
check('Null embedded message is all zeros (Table D.1)', F.NULL_EMBEDDED, [0] * 32)


def demod_call(path, side, voice, data):
    y = np.fromfile(path, np.complex64)
    fs = side['sample_rate']
    y = y * np.exp(-2j * np.pi * side['offset_hz'] * np.arange(len(y)) / fs)
    d = D.discriminate(y, fs, squelch=0.25)
    calls = D.voice_calls(d, voice)
    frames = [f for _, fr in calls for f in fr]
    db = []
    sy = D.sync_symbols(data)
    for pos, sign in D.find_syncs(d, data, 0.6):
        if sign < 0:
            continue
        sc, off = D.fit_levels(d, pos, sy)
        if not D.sync_ok(d, pos, sy, sc, off):
            continue
        b = D.read_burst(d, pos, sc, off)
        cc, dt, err = D.burst_slot_type(b)
        lc, ok, bad = None, False, None
        if D.DATA_TYPES[dt] in F.CRC_MASK:
            info, bad = F.bptc196_decode(b[:98] + b[166:])
            lc, ok = F.check_info96(info, D.DATA_TYPES[dt])
        db.append((pos, cc, D.DATA_TYPES[dt], lc, ok, bad))
    emb_lcs = []
    vs = D.sync_symbols(voice)
    for pos, _ in calls:
        sc, off = D.fit_levels(d, pos, vs)
        cent = [D.read_burst(d, pos + k * 288 * D.SPS, sc, off)[108:156] for k in range(1, 6)]
        lc, chk = F.embedded_lc_decode([c[8:40] for c in cent[:4]])
        embs = [F.emb_decode(c[:8] + c[40:]) for c in cent]
        emb_lcs.append((lc, chk, embs, cent[4][8:40]))
    return d, calls, frames, db, emb_lcs


CASES = [
    ('ms, clean, 48 kS/s', ['--sync', 'ms', '--cc', '3', '--src', '1001', '--dst', '9',
                            '--superframes', '4', '--headers', '2']),
    ('direct2, unit call', ['--sync', 'direct2', '--slot', '2', '--call', 'unit', '--cc', '12',
                            '--src', '703710', '--dst', '42',
                            '--superframes', '3']),
    ('ms, 1 MS/s, +25 kHz, 300 Hz error, 18 dB', ['--sync', 'ms', '--cc', '7', '--fs', '1e6',
                                                  '--offset', '25e3', '--cfo', '300', '--snr',
                                                  '18', '--superframes', '5', '--lead', '0.5']),
]

with tempfile.TemporaryDirectory() as tmp:
    amb = os.path.join(tmp, 'v.ambe')
    with open(amb, 'wb') as f:                     # varied frames, so order is tested
        f.write(rng.integers(0, 256, 9 * 18 * 2, dtype=np.uint8).tobytes())
    for name, args in CASES:
        print(f'call: {name}')
        out = os.path.join(tmp, 'c.cf32')
        S.main([out, '--ambe', amb] + args)
        side = json.load(open(os.path.join(tmp, 'c.json')))
        voice, data = side['sync']['voice'], side['sync']['data']
        d, calls, frames, db, emb_lcs = demod_call(out, side, voice, data)
        want_frames = S.load_frames(amb, side['superframes'])
        check('voice superframes', len(calls), side['superframes'])
        check('every voice frame, in order', frames, want_frames)
        want_lc = F.full_lc(side['lc']['dst'], side['lc']['src'], side['lc']['call'])
        hdr = [x for x in db if x[2] == 'Voice LC Header']
        trm = [x for x in db if x[2] == 'Terminator with LC']
        check('header bursts', len(hdr), side['headers'])
        check('terminator bursts', len(trm), side['terminators'])
        check('every data burst: colour code', {x[1] for x in db}, {side['colour_code']})
        check('header and terminator: BPTC clean, RS passes, LC right',
              all(x[3] == want_lc and x[4] and x[5] == 0 for x in hdr + trm), True)
        check('embedded LC in every superframe',
              all(lc == want_lc and chk['checksum_ok'] and chk['bad_rows'] == 0
                  for lc, chk, _, _ in emb_lcs), True)
        cc = side['colour_code']
        check('EMB B-F: colour code and LCSS 01 11 11 10 00',
              all([e[:3] for e in embs] == [(cc, 0, 1), (cc, 0, 3), (cc, 0, 3), (cc, 0, 2), (cc, 0, 0)]
                  for _, _, embs, _ in emb_lcs), True)
        check('burst F: Null embedded message', all(f == F.NULL_EMBEDDED for *_, f in emb_lcs), True)
        k = side['sample_rate'] / D.FS_SYM
        got_syncs = sorted([round(p * k) for p, *_ in db] + [round(p * k) for p, _ in calls])
        want_syncs = sorted(b['sync_sample'] for b in side['bursts']
                            if b['kind'] == 'data' or b.get('burst') == 'A')
        delay = 50 * k                       # dmr_demod's 101-tap low-pass, at 48 kS/s
        check("sidecar sync_sample within 1 symbol of the SYNC, after the demod's delay",
              len(got_syncs) == len(want_syncs) and
              max(abs(g - w - delay) for g, w in zip(got_syncs, want_syncs)) <= k * D.SPS, True)

ap = argparse.ArgumentParser()
ap.add_argument('--real')
a = ap.parse_args()
if a.real:
    print('real call: re-encode bit for bit')
    d = np.load(a.real)
    n = n_ok = 0
    sy = D.sync_symbols('BS_DATA')
    for pos, sign in D.find_syncs(d, 'BS_DATA', 0.5):
        if sign < 0:
            continue
        sc, off = D.fit_levels(d, pos, sy)
        if not D.sync_ok(d, pos, sy, sc, off):
            continue
        b = D.read_burst(d, pos, sc, off)
        cc, dt, _ = D.burst_slot_type(b)
        name = D.DATA_TYPES[dt]
        if name not in F.CRC_MASK:
            continue
        lc, ok = F.check_info96(F.bptc196_decode(b[:98] + b[166:])[0], name)
        if ok:
            n += 1
            n_ok += D.data_burst(F.bptc196_encode(F.lc_info96(lc, name)), cc, dt) == b
    check('headers and terminators that pass RS', (n_ok, n > 0), (n, True))
    vs = D.sync_symbols('BS_VOICE')
    m = m_ok = 0
    for pos, cent_ok in D.voice_calls(d):
        sc, off = D.fit_levels(d, pos, vs)
        cent = [D.read_burst(d, pos + k * 288 * D.SPS, sc, off)[108:156] for k in range(1, 6)]
        cc = F.emb_decode(cent[0][:8] + cent[0][40:])[0]
        lc, _ = F.embedded_lc_decode([c[8:40] for c in cent[:4]])
        fr = F.embedded_lc(lc) + [F.NULL_EMBEDDED]
        m += 1
        m_ok += [F.voice_centre(cc, l, f) for l, f in zip([1, 3, 3, 2, 0], fr)] == cent
    check('voice centre fields B-F', (m_ok, m > 0), (m, True))

print('PASS' if not failures else f'FAIL: {len(failures)}: {failures}')
sys.exit(1 if failures else 0)
