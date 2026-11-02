#!/usr/bin/env python3
"""DMR (ETSI TS 102 361-1) link control and its FEC, encode and check, in plain Python.

Built from the tables of Annex B, not from another implementation:
  - BPTC(196,96), clause B.1.1: Hamming(15,11,3) rows (Table B.15), Hamming(13,9,3)
    columns (Table B.14), interleave index = index x 181 mod 196 (formula B.1);
  - Reed-Solomon (12,9) over GF(2^8), clause B.3.6: the generator matrix of Table B.18,
    field polynomial a^8 + a^4 + a^3 + a^2 + 1;
  - the Data Type CRC masks of Table B.21;
  - the embedded-signalling BPTC of clause B.2.1 / Figure B.3: Hamming(16,11,4) rows
    (Table B.16), a 5-bit checksum (B.3.11), even column parity, read out by columns;
  - the EMB's Quadratic Residue (16,7,6), Table B.12;
  - the Full LC PDU of clause 7.1 and TS 102 361-2 Table 7.1 (Grp_V_Ch_Usr) and 7.2
    (UU_V_Ch_Usr).

Bits are lists of 0/1, most significant (the spec's highest index) first, in the order
they go on the air. Used by scripts/dmr_synth.py; checked by scripts/test_dmr_synth.py.
"""
import numpy as np


def _matrix(text):
    return np.array([[int(c) for c in r.split()] for r in text.strip().splitlines()], np.uint8)


# Table B.14, B.15, B.16, B.12: systematic generator matrices, info bits first
H1393_G = _matrix("""
1 0 0 0 0 0 0 0 0 1 1 1 1
0 1 0 0 0 0 0 0 0 1 1 1 0
0 0 1 0 0 0 0 0 0 0 1 1 1
0 0 0 1 0 0 0 0 0 1 0 1 0
0 0 0 0 1 0 0 0 0 0 1 0 1
0 0 0 0 0 1 0 0 0 1 0 1 1
0 0 0 0 0 0 1 0 0 1 1 0 0
0 0 0 0 0 0 0 1 0 0 1 1 0
0 0 0 0 0 0 0 0 1 0 0 1 1
""")
H15113_G = _matrix("""
1 0 0 0 0 0 0 0 0 0 0 1 0 0 1
0 1 0 0 0 0 0 0 0 0 0 1 1 0 1
0 0 1 0 0 0 0 0 0 0 0 1 1 1 1
0 0 0 1 0 0 0 0 0 0 0 1 1 1 0
0 0 0 0 1 0 0 0 0 0 0 0 1 1 1
0 0 0 0 0 1 0 0 0 0 0 1 0 1 0
0 0 0 0 0 0 1 0 0 0 0 0 1 0 1
0 0 0 0 0 0 0 1 0 0 0 1 0 1 1
0 0 0 0 0 0 0 0 1 0 0 1 1 0 0
0 0 0 0 0 0 0 0 0 1 0 0 1 1 0
0 0 0 0 0 0 0 0 0 0 1 0 0 1 1
""")
H16114_G = _matrix("""
1 0 0 0 0 0 0 0 0 0 0 1 0 0 1 1
0 1 0 0 0 0 0 0 0 0 0 1 1 0 1 0
0 0 1 0 0 0 0 0 0 0 0 1 1 1 1 1
0 0 0 1 0 0 0 0 0 0 0 1 1 1 0 0
0 0 0 0 1 0 0 0 0 0 0 0 1 1 1 0
0 0 0 0 0 1 0 0 0 0 0 1 0 1 0 1
0 0 0 0 0 0 1 0 0 0 0 0 1 0 1 1
0 0 0 0 0 0 0 1 0 0 0 1 0 1 1 0
0 0 0 0 0 0 0 0 1 0 0 1 1 0 0 1
0 0 0 0 0 0 0 0 0 1 0 0 1 1 0 1
0 0 0 0 0 0 0 0 0 0 1 0 0 1 1 1
""")
QR167_G = _matrix("""
1 0 0 0 0 0 0 0 0 1 0 0 1 1 1 1
0 1 0 0 0 0 0 1 0 0 0 1 1 1 1 0
0 0 1 0 0 0 0 1 1 0 1 1 0 1 1 1
0 0 0 1 0 0 0 1 1 1 1 0 0 0 1 0
0 0 0 0 1 0 0 1 1 1 0 0 1 0 0 1
0 0 0 0 0 1 0 0 1 1 1 0 0 1 0 1
0 0 0 0 0 0 1 0 0 1 1 1 0 0 1 1
""")

# Table B.18, the parity columns (p2, p1, p0) of the RS(12,9) generator, rows m8..m0
RS_PARITY = [[0x1C, 0xBC, 0xFD], [0x89, 0x31, 0x08], [0xAD, 0x41, 0x36],
             [0x7D, 0x71, 0x16], [0xF3, 0xA6, 0x3A], [0x08, 0x83, 0x7B],
             [0x3F, 0x6F, 0x02], [0x6C, 0x0D, 0xA7], [0x0E, 0x38, 0x40]]

# Table B.21 (24-bit masks; the LC's RS parity is the "CRC portion" of these bursts)
CRC_MASK = {'Voice LC Header': 0x969696, 'Terminator with LC': 0x999999}

# Table 9.20
LCSS = {'single': 0b00, 'first': 0b01, 'last': 0b10, 'continuation': 0b11}

# Full LC Opcodes, TS 102 361-2 clause 7.1
FLCO = {'group': 0b000000, 'unit': 0b000011}


def int_bits(v, n):
    return [(v >> (n - 1 - i)) & 1 for i in range(n)]


def bits_int(bits):
    v = 0
    for b in bits:
        v = (v << 1) | int(b)
    return v


def _encode(g, info):
    return ((np.asarray(info, np.uint8) @ g) % 2).astype(np.uint8).tolist()


def _syndrome_ok(g, word):
    k = g.shape[0]
    return _encode(g, word[:k]) == list(word)


# --- GF(2^8) and RS(12,9) ------------------------------------------------------------
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D                  # a^8 + a^4 + a^3 + a^2 + 1 (B.14)
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def gf_mul(a, b):
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def rs129_parity(msg9):
    """Nine LC octets (LC_0 first) -> the three parity octets p2, p1, p0 (c = m x G)."""
    p = [0, 0, 0]
    for row, m in zip(RS_PARITY, msg9):
        for j in range(3):
            p[j] ^= gf_mul(m, row[j])
    return p


def rs129_syndromes(code12):
    """c(x) at a, a^2, a^3, with c_11 = LC_0 the highest power: all zero for a codeword."""
    out = []
    for r in (1, 2, 3):
        s = 0
        for c in code12:
            s = gf_mul(s, _EXP[r]) ^ c
        out.append(s)
    return out


# --- Full LC (72 bits) ------------------------------------------------------------
def full_lc(dst, src, call='group', fid=0, service_options=0, pf=0):
    """Grp_V_Ch_Usr or UU_V_Ch_Usr: PF, R, FLCO 6, FID 8, Service Options 8, dst 24, src 24."""
    return ([pf & 1, 0] + int_bits(FLCO[call], 6) + int_bits(fid, 8)
            + int_bits(service_options, 8) + int_bits(dst, 24) + int_bits(src, 24))


def parse_lc(lc72):
    flco = bits_int(lc72[2:8])
    call = {v: k for k, v in FLCO.items()}.get(flco, f'FLCO {flco}')
    return {'pf': lc72[0], 'flco': flco, 'call': call, 'fid': bits_int(lc72[8:16]),
            'service_options': bits_int(lc72[16:24]), 'dst': bits_int(lc72[24:48]),
            'src': bits_int(lc72[48:72])}


def lc_octets(lc72):
    return [bits_int(lc72[8 * i:8 * i + 8]) for i in range(9)]


def lc_info96(lc72, data_type):
    """LC + masked RS(12,9) parity: the 96 info bits I(95)..I(0) of a header or terminator."""
    p = rs129_parity(lc_octets(lc72))
    m = CRC_MASK[data_type]
    p = [p[0] ^ (m >> 16) & 0xFF, p[1] ^ (m >> 8) & 0xFF, p[2] ^ m & 0xFF]
    return list(lc72) + [b for v in p for b in int_bits(v, 8)]


def check_info96(info96, data_type):
    """-> (lc72, ok): unmask the parity and test the RS(12,9) syndromes."""
    m = CRC_MASK[data_type]
    octs = [bits_int(info96[8 * i:8 * i + 8]) for i in range(12)]
    octs[9] ^= (m >> 16) & 0xFF
    octs[10] ^= (m >> 8) & 0xFF
    octs[11] ^= m & 0xFF
    return list(info96[:72]), rs129_syndromes(octs) == [0, 0, 0]


# --- BPTC(196,96) ------------------------------------------------------------------
_TX_POS = [(m * 181) % 196 for m in range(196)]


def bptc196_encode(info96):
    """96 info bits (I(95) first) -> 196 bits in transmit order (L66 first, Table E.1)."""
    info = list(info96)
    assert len(info) == 96
    data = [0, 0, 0] + info                      # R(2), R(1), R(0), I(95) ... I(0)
    mat = np.zeros((13, 15), np.uint8)
    for r in range(9):
        mat[r] = _encode(H15113_G, data[11 * r:11 * r + 11])
    for c in range(15):
        mat[:, c] = _encode(H1393_G, mat[:9, c])
    flat = [0] + mat.reshape(-1).tolist()        # R(3) is index 0
    tx = [0] * 196
    for m, b in enumerate(flat):
        tx[_TX_POS[m]] = b
    return tx


def bptc196_decode(tx196):
    """-> (96 info bits, number of rows and columns whose Hamming check fails).
    No correction: a clean signal has none to make, and a count says how clean."""
    flat = [tx196[_TX_POS[m]] for m in range(196)]
    mat = np.array(flat[1:], np.uint8).reshape(13, 15)
    bad = sum(not _syndrome_ok(H15113_G, mat[r].tolist()) for r in range(9))
    bad += sum(not _syndrome_ok(H1393_G, mat[:, c].tolist()) for c in range(15))
    data = mat[:9, :11].reshape(-1).tolist()
    return data[3:], bad


def bptc196_layout():
    """The spec's bit names (R(3), I(95), H_R1(3), H_C1(3), ...) in transmit order,
    for comparison with Table E.1."""
    names = [['R(2)', 'R(1)', 'R(0)'] + [f'I({95 - i})' for i in range(8)]]
    k = 87
    for _ in range(8):
        names.append([f'I({k - i})' for i in range(11)])
        k -= 11
    grid = []
    for r in range(9):
        grid.append(names[r] + [f'H_R{r + 1}({3 - j})' for j in range(4)])
    for j in range(4):
        grid.append([f'H_C{c + 1}({3 - j})' for c in range(15)])
    flat = ['R(3)'] + [n for row in grid for n in row]
    tx = [None] * 196
    for m, n in enumerate(flat):
        tx[_TX_POS[m]] = n
    return tx


# --- embedded signalling (voice bursts B-E) ------------------------------------------
def checksum5(lc72):
    return sum(lc_octets(lc72)) % 31


def embedded_lc(lc72):
    """72-bit LC -> four 32-bit fragments for bursts B, C, D, E (Figure B.3)."""
    lc = list(lc72)
    cs = int_bits(checksum5(lc), 5)
    rows, k = [], 0
    for r in range(7):
        n = 11 if r < 2 else 10
        info = lc[k:k + n] + ([] if r < 2 else [cs[r - 2]])
        k += n
        rows.append(_encode(H16114_G, info))
    mat = np.array(rows, np.uint8)
    mat = np.vstack([mat, mat.sum(axis=0) % 2])  # even parity down every column
    bits = mat.T.reshape(-1).tolist()            # columns top to bottom, left to right
    return [bits[32 * i:32 * i + 32] for i in range(4)]


def embedded_lc_decode(frags):
    """Four 32-bit fragments -> (lc72, checks): failing Hamming rows, failing columns,
    and whether the checksum agrees."""
    bits = [b for f in frags for b in f]
    mat = np.array(bits, np.uint8).reshape(16, 8).T
    bad_rows = sum(not _syndrome_ok(H16114_G, mat[r].tolist()) for r in range(7))
    bad_cols = int(np.count_nonzero(mat.sum(axis=0) % 2))
    lc, cs = [], []
    for r in range(7):
        if r < 2:
            lc += mat[r, :11].tolist()
        else:
            lc += mat[r, :10].tolist()
            cs.append(int(mat[r, 10]))
    return lc, {'bad_rows': bad_rows, 'bad_cols': bad_cols,
                'checksum_ok': bits_int(cs) == checksum5(lc)}


NULL_EMBEDDED = [0] * 32                         # Table D.1


# --- EMB (16 bits around the embedded field) -----------------------------------------
def emb(cc, lcss, pi=0):
    return _encode(QR167_G, int_bits(cc, 4) + [pi & 1] + int_bits(lcss, 2))


_EMB_WORDS = {v: _encode(QR167_G, int_bits(v, 7)) for v in range(128)}


def emb_decode(bits16):
    """-> (cc, pi, lcss, bit errors), by minimum distance over the 128 codewords."""
    best = min(_EMB_WORDS, key=lambda v: sum(a != b for a, b in zip(_EMB_WORDS[v], bits16)))
    err = sum(a != b for a, b in zip(_EMB_WORDS[best], bits16))
    return best >> 3, (best >> 2) & 1, best & 3, err


def voice_centre(cc, lcss, frag32, pi=0):
    """The 48-bit centre field of voice bursts B-F: EMB(15..8), 32 embedded, EMB(7..0)
    (Table E.6)."""
    e = emb(cc, lcss, pi)
    return e[:8] + list(frag32) + e[8:]
