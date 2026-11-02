#!/usr/bin/env python3
"""Check the CACH (TACT, Short LC, repeater stream) against the ETSI text.

Pins, read out of docs/ptt/dmr/text/ when it is present (the text is git-ignored):
  Table B.17 and G(x) = x^3 + x + 1     Hamming(7,4,3), the TACT parity
  Table B.13 and g(x) = x^5 + x^2 + 1   Hamming(17,12,3), the Short LC rows
  B.3.7 G8(x) = x^8 + x^2 + x + 1       the Short LC CRC, bit order included
  Figure B.9                            the 24-bit CACH interleave
  Figure B.6                            the BPTC column order of the four fragments
  Tables 9.20, 9.23, 9.24               LCSS, AT, TC
  TS 102 361-2 Tables 7.9, 7.10, B.3    Null Short LC and Activity Update
  Table D.2                             the Idle message in the unused slot

The text has no numerical worked example of a TACT, a Short LC or a CACH burst
(Annex D's worked bits are the Idle message and the Null embedded message, and
Annex E's tables E.1-E.12 do not include the CACH). Those parts are pinned to the
tables and the polynomials only.

Then a BS call from scripts/dmr_synth.py through scripts/dmr_demod.py: both slots,
the Null Short LC, an Activity Update Short LC, at 48 kS/s and at 1 MS/s with an
offset, a carrier error and 18 dB SNR.

    python scripts/test_dmr_cach.py
"""
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
SPEC2 = os.path.join(HERE, '..', 'docs', 'ptt', 'dmr', 'text',
                     'ETSI_TS_102_361-2_v2.4.1_voice_and_general_services.txt')
SPEC_DIR = os.path.join(HERE, '..', 'docs', 'ptt', 'dmr', 'text')
# check() calls while counting_spec is set: 24 in Part 1, 6 in Part 2.
# When the text is present the counts are compared with these, so the number
# printed for a clone without the docs cannot drift from the pins that ran.
N_SPEC_PINS_PART1 = 24
N_SPEC_PINS_PART2 = 6
# 1 MS/s, 18 dB, one superframe, seeds 0-4, both noisy cases below.
# Voice frames were bit-exact on every one of those seeds (and on a 5-seed
# pass of the +25 kHz case, 90 frames). Idle bursts are not: the -20 kHz
# case had 4 bursts off Table D.2 in seeds 0-19 (160 bursts), and at most
# 2 in any five consecutive seeds. 4 over a five-seed run (40 bursts) is
# above that and still rejects a run where the Idles are simply wrong.
SNR_SEEDS = range(5)
SNR_VOICE_BER_BOUND = 0
SNR_IDLE_BOUND = 4
failures = []
counting_spec = False
n_spec_ran = 0
n_skip = 0


def check(name, got, want):
    global n_spec_ran
    if counting_spec:
        n_spec_ran += 1
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {name}"
          + ('' if ok else f": {repr(got)[:240]} (want {repr(want)[:240]})"))
    if not ok:
        failures.append(name)


def poly_bits(text):
    """'x8 + x2 + x + 1' -> the integer with bit n set for x^n. The spec writes x8 for x^8."""
    v = 0
    for term in text.replace(' ', '').split('+'):
        if term == '1':
            v |= 1
        elif term == 'x':
            v |= 1 << 1
        elif re.fullmatch(r'x\d+', term):
            v |= 1 << int(term[1:])
        else:
            raise ValueError(f'not a polynomial term: {term!r} in {text!r}')
    return v


def spec_poly(txt, label):
    m = re.search(re.escape(label) + r'\s*=\s*([xX0-9+ ]+)', txt)
    if not m:
        raise ValueError(f'{label} not in the spec text')
    return poly_bits(m.group(1))


def systematic(info, n_parity, g):
    """m(x) * x^{n-k} + (m(x) * x^{n-k} mod g(x)), MSB of info first."""
    m = 0
    for b in info:
        m = (m << 1) | (int(b) & 1)
    v = m << n_parity
    deg = g.bit_length() - 1
    while v.bit_length() - 1 >= deg:
        v ^= g << (v.bit_length() - 1 - deg)
    n = len(info) + n_parity
    cw = (m << n_parity) | v
    return [(cw >> (n - 1 - i)) & 1 for i in range(n)]


def crc8_ref(bits, poly):
    """x^{8} M(x) mod poly, bits[0] the highest power, init 0, no inversion."""
    reg = 0
    for b in list(bits) + [0] * 8:
        reg = (reg << 1) | (int(b) & 1)
        if reg & 0x100:
            reg ^= poly
    return reg


def spec_matrix(txt, caption, nrows, ncols):
    i = txt.index(caption, txt.index('Annex B'))
    rows = []
    for ln in txt[i:].splitlines()[1:80]:
        t = ln.split()
        if len(t) == ncols and all(re.fullmatch(r'[01]', x) for x in t):
            rows.append([int(x) for x in t])
        elif rows:
            break
    if len(rows) != nrows:
        raise ValueError(f'{caption}: found {len(rows)} rows, want {nrows}')
    return rows


def spec_binary(txt, phrase):
    """The binary token glued to its radix mark, e.g. '012    First fragment' -> 0b01."""
    m = re.search(r'\b([01]+)2\s+' + phrase, txt)
    if not m:
        raise ValueError(f'no binary value before {phrase!r}')
    return int(m.group(1), 2)


def spec_cach_figure(txt):
    """Figure B.9: the line of 24 names above TX(23), and the TACT order above that."""
    i = txt.index('Figure B.9:')
    seg = txt[i - 1500:i]
    lines = [ln.strip() for ln in seg.splitlines() if 'AT' in ln and 'P(16)' in ln]
    if len(lines) < 2:
        raise ValueError(f'Figure B.9: expected the input row and the transmit row, got {lines}')
    tx = re.findall(r'AT|TC|LS\(\d\)|H\(\d\)|P\(\d+\)', lines[-1])
    pre = re.findall(r'AT|TC|LS\(\d\)|H\(\d\)|P\(\d+\)', lines[-2])
    if len(tx) != 24 or len(pre) != 24:
        raise ValueError(f'Figure B.9: {len(pre)} input names, {len(tx)} transmit names')
    return pre, tx


def spec_b6(txt):
    # The first "Figure B.6" is the sentence that introduces the figure. The
    # caption "Figure B.6:" sits under the four CACH rows.
    i = txt.index('Figure B.6:')
    seg = txt[txt.rfind('CACH 1', 0, i):i]
    out = []
    for n in range(1, 5):
        m = re.search(rf'CACH {n}\s+(.*)', seg)
        names = re.findall(r'(?:LC|CR|PC|H\d)\(\d+\)', m.group(1))
        if len(names) != 17:
            raise ValueError(f'CACH {n}: {len(names)} names {names}')
        out.append(names)
    return out


def ref_rows_to_frags(rows):
    flat = [rows[r][c] for c in range(17) for r in range(4)]
    return [flat[17 * i:17 * i + 17] for i in range(4)]


def ref_short_lc(info, G, poly):
    raw = list(info) + [(crc8_ref(info, poly) >> (7 - i)) & 1 for i in range(8)]
    rows = []
    for r in range(3):
        chunk = raw[12 * r:12 * r + 12]
        row = [0] * 17
        for bit, grow in zip(chunk, G):
            if bit:
                row = [(a + b) % 2 for a, b in zip(row, grow)]
        rows.append(row)
    rows.append([sum(rows[r][c] for r in range(3)) % 2 for c in range(17)])
    return ref_rows_to_frags(rows)


def ref_cach(names, tact7, payload17):
    src = {'AT': tact7[0], 'TC': tact7[1], 'LS(1)': tact7[2], 'LS(0)': tact7[3],
           'H(2)': tact7[4], 'H(1)': tact7[5], 'H(0)': tact7[6]}
    for i, b in enumerate(payload17):
        src[f'P({16 - i})'] = b
    return [src[name] for name in names]


print('spec text has no numerical CACH / TACT / Short LC example; '
      'Annex E tables stop at E.12 and none is a CACH')

if not os.path.exists(SPEC):
    print(f'  SKIPPED {N_SPEC_PINS_PART1} spec pins ({SPEC_DIR}/ part 1 not present)')
    n_skip += N_SPEC_PINS_PART1
    txt = None
else:
    counting_spec = True
    _part1_before = n_spec_ran
    txt = open(SPEC, encoding='latin1').read()
    annex_e = txt[txt.find('Annex E (normative)', txt.find('Annex E') + 10):]
    caps = re.findall(r'Table E\.\d+:[^\n]*', annex_e)
    check('Annex E has tables E.1 through E.12 and no CACH table',
          [any(c.startswith(f'Table E.{n}:') for c in caps) for n in range(1, 13)]
          + ['CACH' in annex_e[:5000]],
          [True] * 12 + [False])

    print('Hamming(7,4,3) and the TACT')
    i17 = txt.index('Table B.17:')
    g74 = spec_poly(txt[i17 - 600:i17], 'G(x)')
    check('G(x) = x^3 + x + 1 (B.8)', g74, 0b1011)
    b17 = spec_matrix(txt, 'Table B.17:', 4, 7)
    check('generator matrix Table B.17', b17, np.asarray(F.H743_G).tolist())
    check('each Table B.17 row is one systematic codeword of G(x)',
          b17, [systematic([1 if r == i else 0 for r in range(4)], 3, g74) for i in range(4)])
    tact_ok = all(F.tact(at, tc, lcss) == systematic(
        [at, tc, (lcss >> 1) & 1, lcss & 1], 3, g74)
        for at in (0, 1) for tc in (0, 1) for lcss in range(4))
    check('TACT parity of all 16 infos is the Hamming(7,4) of AT, TC, LCSS (Table 9.5 order)',
          tact_ok, True)
    weights = set()
    for v in range(1, 16):
        info = [(v >> (3 - i)) & 1 for i in range(4)]
        weights.add(sum(systematic(info, 3, g74)))
    check('Hamming(7,4,3) minimum distance 3', min(weights), 3)

    print('Hamming(17,12,3), the CRC-8, Figure B.6')
    i13 = txt.index('Table B.13:')
    g17 = spec_poly(txt[i13 - 700:i13], 'g(x)')
    check('g(x) = x^5 + x^2 + 1 (B.6)', g17, 0b100101)
    b13 = spec_matrix(txt, 'Table B.13:', 12, 17)
    check('generator matrix Table B.13', b13, np.asarray(F.H17123_G).tolist())
    check('each Table B.13 row is one systematic codeword of g(x)',
          b13, [systematic([1 if r == i else 0 for r in range(12)], 5, g17) for i in range(12)])
    w17 = min(sum(systematic([(v >> (11 - i)) & 1 for i in range(12)], 5, g17))
              for v in range(1, 4096))
    check('Hamming(17,12,3) minimum distance 3', w17, 3)

    g8 = spec_poly(txt[txt.index('8-bit CRC calculation', txt.index('Annex B')):], 'G8(x)')
    check('G8(x) = x^8 + x^2 + x + 1 (B.16)', g8, 0x107)
    # The contents list is the first "B.3.7". The clause itself is the heading.
    crc_sec = txt[txt.index('B.3.7         8-bit CRC calculation'):]
    crc_sec = crc_sec[:crc_sec.index('B.3.8')]
    # pdftotext breaks "no inversion polynomial" across a line.
    crc_flat = re.sub(r'\s+', ' ', crc_sec)
    check('B.3.7: no inversion, initial remainder 0',
          'no inversion polynomial' in crc_flat and '000000002' in crc_flat, True)
    check('B.3.7: bit 3 of octet 0 is x^27 and bit 0 of octet 3 is x^0',
          'bit 3 of octet 0 corresponding to the x27 term' in txt
          and 'bit 0 of octet 3 corresponding to the x0 term' in txt, True)
    check('B.3.7: CRC MSB is x^7',
          'MSB of the CRC corresponding to x7' in txt, True)
    rng = np.random.default_rng(1)
    crc_ok = True
    for nbits in (24, 28):
        for _ in range(30):
            bits = rng.integers(0, 2, nbits).tolist()
            if F.crc8(bits) != crc8_ref(bits, g8):
                crc_ok = False
    # the single high bit is x^27 of a Short LC, the case the bit-order sentences name
    crc_ok = crc_ok and F.crc8([1] + [0] * 27) == crc8_ref([1] + [0] * 27, g8)
    crc_ok = crc_ok and F.crc8([0] * 28) == 0
    check('CRC-8 of Short LC messages matches x^8 M(x) mod G8(x), high bit first', crc_ok, True)

    pre, tx_names = spec_cach_figure(txt)
    check('Figure B.9 input order is AT TC LS(1) LS(0) H(2) H(1) H(0) then P(16)..P(0)',
          pre, ['AT', 'TC', 'LS(1)', 'LS(0)', 'H(2)', 'H(1)', 'H(0)']
          + [f'P({16 - i})' for i in range(17)])
    check('Figure B.9 transmit order', tx_names, F.CACH_TX_NAMES)
    inter_ok = True
    for _ in range(20):
        t = rng.integers(0, 2, 7).tolist()
        p = rng.integers(0, 2, 17).tolist()
        if F.cach(t, p) != ref_cach(tx_names, t, p):
            inter_ok = False
        back_t, back_p = F.cach_split(ref_cach(tx_names, t, p))
        if back_t != t or back_p != p:
            inter_ok = False
    check('cach() places TACT and payload where Figure B.9 names them', inter_ok, True)

    check('Figure B.6 fragment bit names', spec_b6(txt), F.short_lc_layout())
    bptc_ok = True
    for _ in range(15):
        info = rng.integers(0, 2, 28).tolist()
        if F.short_lc_encode(info) != ref_short_lc(info, b13, g8):
            bptc_ok = False
        back, chk = F.short_lc_decode(F.short_lc_encode(info))
        if back != info or chk != {'bad_rows': 0, 'bad_cols': 0,
                                   'crc': crc8_ref(info, g8), 'crc_ok': True}:
            bptc_ok = False
    check('Short LC BPTC matches Table B.13, the CRC-8 and Figure B.6 column order', bptc_ok, True)

    print('LCSS, AT, TC, Idle message')
    lcss_first = spec_binary(txt, 'First fragment of LC signalling')
    lcss_cont = spec_binary(txt, 'Continuation fragment of LC or CSBK signalling')
    lcss_last = spec_binary(txt, 'Last fragment of LC or CSBK signalling')
    check('Table 9.20 LCSS first, continuation, last',
          (lcss_first, lcss_cont, lcss_last), (0b01, 0b11, 0b10))
    at_idle = spec_binary(txt, 'Inbound channel is idle')
    at_busy = spec_binary(txt, 'Inbound channel is busy')
    check('Table 9.23 AT idle, busy', (at_idle, at_busy), (0, 1))
    tc1 = spec_binary(txt, 'Following outbound burst is channel 1')
    tc2 = spec_binary(txt, 'Following outbound burst is channel 2')
    check('Table 9.24 TC channel 1, channel 2', (tc1, tc2), (0, 1))

    def named_bits(caption, stop):
        i = txt.index(caption, txt.index('Annex D'))
        seg = txt[i:txt.index(stop, i)]
        return {m.group(1): int(m.group(2))
                for m in re.finditer(r'\b([A-Z](?:_[A-Z]+\d+)?\(\d+\))\s+([01])\b', seg)}

    d2 = named_bits('Table D.2:', 'Figure D.1')
    check('IDLE_INFO96 is Table D.2, I(95) first',
          F.IDLE_INFO96, [d2[f'I({i})'] for i in range(95, -1, -1)])
    counting_spec = False
    _part1 = n_spec_ran - _part1_before
    if _part1 != N_SPEC_PINS_PART1:
        check('Part 1 spec-pin count', _part1, N_SPEC_PINS_PART1)

if not os.path.exists(SPEC2):
    print(f'  SKIPPED {N_SPEC_PINS_PART2} spec pins ({SPEC_DIR}/ part 2 not present)')
    n_skip += N_SPEC_PINS_PART2
else:
    print('Null Short LC and Activity Update (Part 2)')
    counting_spec = True
    _part2_before = n_spec_ran
    t2 = open(SPEC2, encoding='latin1').read()
    b3 = t2[t2.index('Table B.3: SLCO'):]
    slco_null = spec_binary(b3, 'Null Message')
    slco_act = spec_binary(b3, 'Activity Update')
    check('Table B.3 SLCO Null, Activity Update', (slco_null, slco_act), (0b0000, 0b0001))
    i79 = t2.index('Table 7.9:')
    ntab = t2[i79:t2.index('7.1.3.2', i79)]
    check('Table 7.9: SLCO 0000 and the 24 reserved bits are 0',
          'Shall be set to 00002' in ntab and 'All bits shall be set to 02' in ntab
          and F.null_short_lc() == [0] * 28 and F.bits_int(F.null_short_lc()[:4]) == slco_null,
          True)
    check('Null Short LC is the all-zero codeword (linear code, CRC init 0)',
          F.short_lc_encode(F.null_short_lc()), [[0] * 17] * 4)
    i710 = t2.index('Table 7.10:')
    atab = t2[i710:t2.index('\n7.2', i710)]
    act_none = spec_binary(atab, 'No activity on BS time slot 1')
    act_group = spec_binary(atab, 'Group voice activity on BS time slot 1')
    act_unit = spec_binary(atab, 'Individual voice activity on BS time slot 1')
    check('Table 7.10 activity: none, group voice, individual voice',
          (act_none, act_group, act_unit),
          (F.ACT_NONE, F.ACT_GROUP_VOICE, F.ACT_INDIVIDUAL_VOICE))
    m = re.search(r'Short LC Opcode \(SLCO\)\s+4\s+([01]+)2', atab)
    check('Table 7.10 SLCO row is 0001', int(m.group(1), 2) if m else None, 0b0001)
    # Table 7.9 puts the value in the remark ("Shall be set to 00002"), not beside the length.
    m79 = re.search(r'Short LC Opcode \(SLCO\)\s+4\s+Shall be set to ([01]+)2', ntab)
    check('Table 7.9 SLCO row is 0000', int(m79.group(1), 2) if m79 else None, 0b0000)
    counting_spec = False
    _part2 = n_spec_ran - _part2_before
    if _part2 != N_SPEC_PINS_PART2:
        check('Part 2 spec-pin count', _part2, N_SPEC_PINS_PART2)


def library_parsed():
    """Field values the call round trip expects. These are the library constants.

    The spec pins above compare those constants with the text when the text is
    in the tree. A clone without docs/ still runs the calls against this.
    """
    return {
        'lcss': list(F.SHORT_LC_LCSS),
        'at_idle': 0, 'at_busy': 1,
        'tc1': S.TC_OF_SLOT[1], 'tc2': S.TC_OF_SLOT[2],
        'slco_null': F.SLCO_NULL, 'slco_act': F.SLCO_ACTIVITY,
        'act_none': F.ACT_NONE, 'act_group': F.ACT_GROUP_VOICE,
        'act_unit': F.ACT_INDIVIDUAL_VOICE,
        'g8': F.CRC8_POLY,
    }


def pack_activity(slco, ts1, ts2, h1, h2):
    bits = []
    for v, n in ((slco, 4), (ts1, 4), (ts2, 4), (h1, 8), (h2, 8)):
        bits += [(int(v) >> (n - 1 - i)) & 1 for i in range(n)]
    return bits


def expected_info(slot, call, dst, kind, p):
    if kind == 'null':
        return [0] * 28
    ts = [p['act_none'], p['act_none']]
    hs = [0, 0]
    ts[slot - 1] = p['act_group'] if call == 'group' else p['act_unit']
    hs[slot - 1] = crc8_ref([(dst >> (23 - i)) & 1 for i in range(24)], p['g8'])
    return pack_activity(p['slco_act'], ts[0], ts[1], hs[0], hs[1])


def demod_bs(path, side):
    y = np.fromfile(path, np.complex64)
    fs = side['sample_rate']
    y = y * np.exp(-2j * np.pi * side['offset_hz'] * np.arange(len(y)) / fs)
    d = D.discriminate(y, fs, squelch=0.25)
    calls = D.voice_calls(d, 'BS_VOICE')
    db = D.data_bursts(d, 'BS_DATA')
    frames = [f for _, fr in calls for f in fr]
    # One scale from each real SYNC, then a CACH on every slot of the grid
    # anchored at the earliest SYNC. Voice bursts B-F have no SYNC of their own;
    # they still sit 288 symbols after burst A, and the other slot 144 symbols off that.
    hits = []
    for name in ('BS_VOICE', 'BS_DATA'):
        sy = D.sync_symbols(name)
        for pos, sign in D.find_syncs(d, name, 0.6):
            if sign < 0:
                continue
            sc, off = D.fit_levels(d, pos, sy)
            if D.sync_ok(d, pos, sy, sc, off):
                hits.append((pos, sc, off))
    hits.sort()
    return d, calls, frames, db, hits


def read_grid(d, hits, n_slots):
    """24-bit CACH and 264-bit burst for each of n_slots slots, earliest SYNC first."""
    pos0, sc0, off0 = hits[0]
    words, bursts, scales = [], [], []
    for i in range(n_slots):
        centre = pos0 + i * 144 * D.SPS
        near = min(hits, key=lambda h: abs(h[0] - centre))
        sc, off = near[1], near[2]
        burst_pos = centre - 54 * D.SPS
        words.append(D.read_cach(d, burst_pos, sc, off))
        bursts.append(D.read_burst(d, centre, sc, off))
        scales.append((sc, off))
    return words, bursts, scales


def burst_payload(bits):
    return bits[:98] + bits[166:]


CASES = [
    ('48 kS/s slot 1, Null Short LC',
     ['--sync', 'bs', '--slot', '1', '--short-lc', 'null', '--cc', '3', '--src', '1001',
      '--dst', '9', '--superframes', '2']),
    ('48 kS/s slot 2, Activity Update, group call',
     ['--sync', 'bs', '--slot', '2', '--short-lc', 'activity', '--cc', '1', '--src', '1001',
      '--dst', '9', '--superframes', '1']),
    ('48 kS/s slot 1, Activity Update, unit call',
     ['--sync', 'bs', '--slot', '1', '--short-lc', 'activity', '--call', 'unit', '--cc', '12',
      '--src', '7', '--dst', '42', '--superframes', '1']),
    ('1 MS/s slot 1, Null, +25 kHz, 300 Hz, 18 dB',
     ['--sync', 'bs', '--slot', '1', '--short-lc', 'null', '--cc', '7', '--src', '1001',
      '--dst', '9', '--superframes', '1', '--fs', '1e6', '--offset', '25e3', '--cfo', '300',
      '--snr', '18', '--lead', '0.5']),
    ('1 MS/s slot 2, Activity Update, -20 kHz, -150 Hz, 18 dB',
     ['--sync', 'bs', '--slot', '2', '--short-lc', 'activity', '--cc', '5', '--src', '1001',
      '--dst', '9', '--superframes', '1', '--fs', '1e6', '--offset=-20e3', '--cfo=-150',
      '--snr', '18', '--lead', '0.5']),
]


def check_bs_timing(name, path, side):
    """Burst timing against the waveform, clean 48 kS/s only.

    The demod delay is the 101-tap low-pass: 50 samples at 48 kS/s. On the
    matched path the integer SYNC peak sits one sample earlier than the boxcar
    (the fractional peak is about -0.89 of a sample, against -0.40 for the
    boxcar). Both still land inside one sample of sidecar sync_sample + 50,
    and the window is not widened past that.

    65.5 symbols is measured on the boxcar. The matched path's extra sample
    would push a burst that is already one sample early out to 657, past ±1
    of 655. The slot centre is not read from the sidecar. The carrier turns
    on, unshifted, at the first CACH symbol; six of those twelve symbols fall
    in this slot, and the slot centre is 72 symbols after that boundary, so
    it is 78 symbols after the turn-on. The first symbol centre is that point
    minus 655 samples. Removing the half-symbol shift in modulate_bs moves
    the waveform 5 samples earlier and this distance leaves ±1. Adding 7
    samples to every sidecar position leaves the distance alone and fails the
    peak-versus-sidecar and start/CACH-versus-waveform checks instead.
    """
    if side['sample_rate'] != D.FS_SYM or side['snr_db_12k5'] is not None:
        return
    y = np.fromfile(path, np.complex64)
    y = y * np.exp(-2j * np.pi * side['offset_hz'] * np.arange(len(y)) / side['sample_rate'])
    mag = np.abs(y)
    on = np.flatnonzero(np.abs(mag - 0.5) < 1e-4)
    one_run = len(on) > 0 and not np.any(np.diff(on) > 1)
    check(name + ': carrier is one flat run at amplitude 0.5', one_run, True)
    if not one_run:
        return
    env0 = int(on[0])
    flat = float(np.max(np.abs(mag[on] - 0.5)))
    check(name + ': carrier envelope flat between bursts (within 1e-4 of 0.5, ramps excluded)',
          flat < 1e-4, True)

    delay = 50
    sps = D.SPS

    def sync_peaks(d):
        found = []
        for sync_name in ('BS_VOICE', 'BS_DATA'):
            sy = D.sync_symbols(sync_name)
            for pos, sign in D.find_syncs(d, sync_name, 0.6):
                if sign < 0:
                    continue
                sc, off = D.fit_levels(d, pos, sy)
                if D.sync_ok(d, pos, sy, sc, off):
                    found.append((int(pos), sc, off))
        found.sort()
        return found

    sync_bursts = [(i, b) for i, b in enumerate(side['bursts'])
                   if b['kind'] == 'data' or b.get('burst') == 'A']
    d_box = D.discriminate(y, D.FS_SYM, squelch=0.25, matched=False)
    d_mat = D.discriminate(y, D.FS_SYM, squelch=0.25, matched=True)
    box = sync_peaks(d_box)
    mat = sync_peaks(d_mat)

    def peak_errs(found):
        if len(found) != len(sync_bursts):
            return None
        return [pos - (b['sync_sample'] + delay) for (pos, _, _), (_, b) in zip(found, sync_bursts)]

    box_err = peak_errs(box)
    mat_err = peak_errs(mat)
    check(name + ': boxcar SYNC peak within 1 sample of sidecar + the 50-sample delay',
          box_err is not None and max(abs(e) for e in box_err) <= 1, True)
    check(name + ': matched SYNC peak within 1 sample of sidecar + the 50-sample delay',
          mat_err is not None and max(abs(e) for e in mat_err) <= 1, True)
    if box_err is None:
        return

    first_at = {}
    for (pos, _sc, _off), (i, _b) in zip(box, sync_bursts):
        first_at[i] = (pos - delay) - 54 * sps
    ok_dist = True
    ok_side = True
    for i, b in enumerate(side['bursts']):
        j = min(first_at, key=lambda k: abs(k - i))
        first_iq = first_at[j] + (i - j) * 144 * sps
        dist = env0 + (78 + i * 144) * sps - first_iq
        if abs(dist - 655) > 1:
            ok_dist = False
        if (abs(first_iq - b['start_sample']) > 1
                or abs(first_iq - 12 * sps - b['cach_sample']) > 1):
            ok_side = False
    check(name + ': first symbol centre is 65.5 symbols (655 samples) before the slot centre',
          ok_dist, True)
    check(name + ': sidecar start and CACH match the waveform; CACH is the 12 symbols before the burst',
          ok_side, True)

    # Envelope-grid centres assume the half-symbol shift. The sync grid follows
    # the peak, so the two agree on a shifted waveform and disagree when the
    # shift is missing even if the sidecar moved with it.
    diffs = 0
    n_sym = 0
    half = sps // 2
    for (pos, sc, off), (i, _b) in zip(box, sync_bursts):
        sy_sync = D.read_symbols(d_box, pos - 54 * sps - 12 * sps, 12, sc, off)
        centres = env0 + i * 144 * sps + half + delay + sps * np.arange(12)
        if sy_sync is None or centres[0] < 0 or int(centres[-1]) >= len(d_box):
            diffs += 12
            n_sym += 12
            continue
        v = (d_box[centres] - off) / sc
        sy_env = np.where(v > 2, 3, np.where(v > 0, 1, np.where(v > -2, -1, -3)))
        diffs += int(np.sum(np.asarray(sy_sync) != sy_env))
        n_sym += 12
    check(name + ': CACH symbols on the envelope grid match the symbols read from the SYNC',
          diffs == 0 and n_sym == 12 * len(sync_bursts), True)


def run_case(name, args, amb, p, payload_exact=True):
    """One BS call. Returns voice bit errors, unclean Idles, and the two counts.

    payload_exact False is the 18 dB runs: voice frames are still required
    bit-exact per seed, and Idle bursts are only counted. The caller applies
    the Idle bound, because one seed at 18 dB flips an Idle on correct code.
    """
    print(f'call: {name}')
    out = os.path.join(os.path.dirname(amb), 'c.cf32')
    S.main([out] + args + ['--ambe', amb])
    side = json.load(open(os.path.splitext(out)[0] + '.json'))
    d, calls, frames, db, hits = demod_bs(out, side)
    n_slots = 2 * (side['headers'] + 6 * side['superframes'] + side['terminators'])
    words, bursts, _scales = read_grid(d, hits, n_slots)
    tacts, shorts = D.decode_cachs(words)

    want_frames = S.load_frames(amb, side['superframes'])
    check(name + ': voice_calls superframes', len(calls), side['superframes'])
    if len(frames) == len(want_frames):
        voice_ber = sum(x != y for a, b in zip(frames, want_frames) for x, y in zip(a, b))
        n_voice = len(frames)
    else:
        voice_ber = 72 * max(len(frames), len(want_frames), 1)
        n_voice = len(want_frames)
    check(name + ': every voice frame, in order, at the 288-symbol same-slot spacing',
          frames, want_frames)
    want_lc = F.full_lc(side['lc']['dst'], side['lc']['src'], side['lc']['call'],
                        service_options=side['lc']['service_options'])
    kinds = {}
    for _pos, cc, dt, err in db:
        kinds.setdefault(D.DATA_TYPES[dt], []).append((cc, err))
    check(name + ': data_bursts colour code', {cc for rows in kinds.values() for cc, _ in rows},
          {side['colour_code']})
    check(name + ': header count', len(kinds.get('Voice LC Header', [])), side['headers'])
    check(name + ': terminator count', len(kinds.get('Terminator with LC', [])), side['terminators'])
    check(name + ': Idle count (the other slot, one per call burst)',
          len(kinds.get('Idle', [])), n_slots // 2)
    check(name + ': slot type Golay clean',
          all(err == 0 for rows in kinds.values() for _, err in rows), True)

    headers, terms, idles = [], [], []
    data_cc = []
    for b in bursts:
        cc, dt, err = D.burst_slot_type(b)
        if err > 0:
            continue
        label = D.DATA_TYPES[dt]
        if label == 'Idle':
            idles.append(b)
        elif label == 'Voice LC Header':
            headers.append(b)
        elif label == 'Terminator with LC':
            terms.append(b)
        else:
            continue
        data_cc.append(cc)
    check(name + ': grid data bursts, colour code', set(data_cc), {side['colour_code']})
    lc_ok = True
    for b, label in [(b, 'Voice LC Header') for b in headers] + [(b, 'Terminator with LC') for b in terms]:
        info, bad = F.bptc196_decode(burst_payload(b))
        lc, ok = F.check_info96(info, label)
        lc_ok = lc_ok and lc == want_lc and ok and bad == 0
    check(name + ': header and terminator LC, BPTC clean, RS passes', lc_ok, True)
    idle_bits = [F.bptc196_decode(burst_payload(b)) for b in idles]
    idle_bad = sum(not (info == F.IDLE_INFO96 and bad == 0) for info, bad in idle_bits)
    n_idle = len(idles)
    if len(idles) != n_slots // 2:
        idle_bad += abs(len(idles) - n_slots // 2)
    if payload_exact:
        check(name + ': every Idle burst is Table D.2, BPTC clean',
              idle_bad == 0 and len(idles) == n_slots // 2, True)

    # embedded LC on the call slot, bursts B-F, 288 symbols apart
    emb_ok = True
    cc = side['colour_code']
    for pos, _fr in calls:
        sc, off = D.fit_levels(d, pos, D.sync_symbols('BS_VOICE'))
        cent = [D.read_burst(d, pos + k * 288 * D.SPS, sc, off)[108:156] for k in range(1, 6)]
        lc, chk = F.embedded_lc_decode([c[8:40] for c in cent[:4]])
        embs = [F.emb_decode(c[:8] + c[40:]) for c in cent]
        emb_ok = emb_ok and lc == want_lc and chk['checksum_ok'] and chk['bad_rows'] == 0
        emb_ok = emb_ok and [e[:3] for e in embs] == [
            (cc, 0, 1), (cc, 0, 3), (cc, 0, 3), (cc, 0, 2), (cc, 0, 0)]
        emb_ok = emb_ok and cent[4][8:40] == F.NULL_EMBEDDED
    check(name + ': embedded LC, EMB LCSS 01 11 11 10 00, burst F null', emb_ok, True)

    none_missing = all(w is not None for w in words)
    check(name + ': a CACH was read before every burst', none_missing, True)
    if not none_missing:
        return voice_ber, idle_bad, n_voice, n_idle
    seq = p['lcss']
    check(name + ': LCSS of the fragments is 01, 11, 11, 10, repeating',
          [t['lcss'] for t in tacts], seq * (n_slots // 4))
    check(name + ': TC alternates channel 1, channel 2, starting at channel 1',
          [t['tc'] for t in tacts], [p['tc1'], p['tc2']] * (n_slots // 2))
    want_at = []
    for i in range(n_slots):
        slot = 1 if i % 2 == 0 else 2
        want_at.append(p['at_busy'] if slot == side['slot'] else p['at_idle'])
    check(name + ': AT busy on the call slot, idle on the other',
          [t['at'] for t in tacts], want_at)
    check(name + ': every TACT is a Hamming(7,4) codeword',
          [t['errors'] for t in tacts], [0] * n_slots)

    kind = 'null' if '--short-lc' in args and args[args.index('--short-lc') + 1] == 'null' else 'activity'
    # cases that pass --short-lc explicitly, which all of these do
    info = expected_info(side['slot'], side['lc']['call'], side['lc']['dst'], kind, p)
    n_msg = n_slots // 4
    check(name + ': Short LC reassembled on every four fragments', len(shorts), n_msg)
    check(name + ': Short LC info, CRC octet and CRC check',
          [(s['info'], s['crc'], s['crc_ok'], s['bad_rows'], s['bad_cols'], s['slco']) for s in shorts],
          [(info, crc8_ref(info, p['g8']), True, 0, 0, p['slco_null'] if kind == 'null' else p['slco_act'])] * n_msg)

    # sidecar geometry: CACH is 12 symbols before the burst, bursts are 144 symbols apart
    k = side['sample_rate'] / D.FS_SYM
    starts = [b['start_sample'] for b in side['bursts']]
    cachs = [b['cach_sample'] for b in side['bursts']]
    step = 144 * D.SPS * k
    check(name + ': sidecar burst spacing is one 30 ms slot',
          all(abs((b - a) - step) <= 1 for a, b in zip(starts, starts[1:])), True)
    check(name + ': sidecar CACH is 12 symbols before its burst',
          all(abs((s - c) - 12 * D.SPS * k) <= 1 for s, c in zip(starts, cachs)), True)
    check_bs_timing(name, out, side)
    return voice_ber, idle_bad, n_voice, n_idle


with tempfile.TemporaryDirectory() as tmp:
    amb = os.path.join(tmp, 'v.ambe')
    rng = np.random.default_rng(3)
    with open(amb, 'wb') as f:
        f.write(rng.integers(0, 256, 9 * 18 * 2, dtype=np.uint8).tobytes())
    parsed = library_parsed()
    for name, args in CASES:
        if '--snr' in args:
            voice = idle_bad = n_voice = n_idle = 0
            for seed in SNR_SEEDS:
                v, ib, nv, ni = run_case(
                    f'{name} seed {seed}', args + ['--seed', str(seed)], amb, parsed,
                    payload_exact=False)
                voice += v
                idle_bad += ib
                n_voice += nv
                n_idle += ni
            print(f'  18 dB over seeds {list(SNR_SEEDS)}: {voice} voice-frame bit errors '
                  f'in {n_voice} frames, {idle_bad} Idle bursts off Table D.2 of {n_idle}')
            check(name + ': 18 dB voice-frame bit errors over 5 seeds',
                  voice <= SNR_VOICE_BER_BOUND, True)
            check(name + ': 18 dB Idle bursts off Table D.2 over 5 seeds',
                  idle_bad <= SNR_IDLE_BOUND, True)
        else:
            run_case(name, args, amb, parsed)

if failures:
    line = f'FAIL: {len(failures)}: {failures}'
elif n_skip:
    line = f'PASS ({n_skip} spec pins skipped: docs/ptt/dmr/text/ not present)'
else:
    line = 'PASS'
print(line)
sys.exit(1 if failures else 0)
