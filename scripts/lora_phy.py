#!/usr/bin/env python3
"""LoRa PHY in numpy, written from gr-lora_sdr's rules (docs/lora/code/gr-lora_sdr).

encode(payload, sf, cr, ...) -> symbol values; modulate(symbols, ...) -> IQ;
demod/decode a capture -> payload. Checked against real SX127x captures.
"""
import numpy as np


def lfsr_whitening(n):
    """x^8+x^6+x^5+x^4+1, seed 0xFF, shift toward the MSB."""
    reg, out = 0xFF, []
    for _ in range(n):
        out.append(reg)
        fb = ((reg >> 7) ^ (reg >> 5) ^ (reg >> 4) ^ (reg >> 3)) & 1
        reg = ((reg << 1) & 0xFE) | fb
    return out


WHITEN = lfsr_whitening(255)


def bits(v, n):                     # MSB first, like gr-lora_sdr's int2bool
    return [(v >> (n - 1 - i)) & 1 for i in range(n)]


def val(b):
    v = 0
    for x in b:
        v = (v << 1) | int(x)
    return v


def crc16(data):
    """CRC-16/CCITT, poly 0x1021, init 0, MSB first, no final XOR."""
    crc = 0
    for byte in data:
        for _ in range(8):
            if ((crc >> 8) ^ byte) & 0x80:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
            byte = (byte << 1) & 0xFF
    return crc


def payload_crc(p):
    """CRC over all but the last two bytes, those two XORed on (gr-lora_sdr add_crc)."""
    if len(p) < 2:
        return None
    c = crc16(p[:-2])
    return c ^ p[-1] ^ (p[-2] << 8)


def header_nibbles(length, cr, has_crc):
    h0, h1, h2 = length >> 4, length & 0xF, (cr << 1) | has_crc
    b = lambda v, k: (v >> k) & 1
    c4 = b(h0, 3) ^ b(h0, 2) ^ b(h0, 1) ^ b(h0, 0)
    c3 = b(h0, 3) ^ b(h1, 3) ^ b(h1, 2) ^ b(h1, 1) ^ b(h2, 0)
    c2 = b(h0, 2) ^ b(h1, 3) ^ b(h1, 0) ^ b(h2, 3) ^ b(h2, 1)
    c1 = b(h0, 1) ^ b(h1, 2) ^ b(h1, 0) ^ b(h2, 2) ^ b(h2, 1) ^ b(h2, 0)
    c0 = b(h0, 0) ^ b(h1, 1) ^ b(h2, 3) ^ b(h2, 2) ^ b(h2, 1) ^ b(h2, 0)
    return [h0, h1, h2, c4, (c3 << 3) | (c2 << 2) | (c1 << 1) | c0]


def hamming(nib, cr):
    d = bits(nib, 4)                # d[0] = MSB ... d[3] = LSB
    if cr == 1:
        p4 = d[0] ^ d[1] ^ d[2] ^ d[3]
        return (d[3] << 4 | d[2] << 3 | d[1] << 2 | d[0] << 1 | p4)
    p0 = d[3] ^ d[2] ^ d[1]
    p1 = d[2] ^ d[1] ^ d[0]
    p2 = d[3] ^ d[2] ^ d[0]
    p3 = d[3] ^ d[1] ^ d[0]
    w = d[3] << 7 | d[2] << 6 | d[1] << 5 | d[0] << 4 | p0 << 3 | p1 << 2 | p2 << 1 | p3
    return w >> (4 - cr)


def encode(payload, sf, cr, has_crc=True, implicit=False, ldro=False, crc_value=None, pad=None):
    """crc_value forces the CRC; pad maps a nibble position to the padding nibble sent there."""
    payload = bytes(payload)
    nibs = []
    for i, byte in enumerate(payload):
        w = byte ^ WHITEN[i]
        nibs += [w & 0xF, w >> 4]
    if not implicit:
        nibs = header_nibbles(len(payload), cr, int(has_crc)) + nibs
    if has_crc:
        c = payload_crc(payload) if crc_value is None else crc_value
        if c is None:
            raise ValueError('a CRC needs at least two payload bytes here')
        nibs += [c & 0xF, (c >> 4) & 0xF, (c >> 8) & 0xF, (c >> 12) & 0xF]
    if pad:
        head = 0 if implicit else 5
        body = len(nibs) - head
        first_body = max(sf - 2 - head, 0)
        rest = body - first_body
        per = (sf - 2) if ldro else sf
        for k in range((-rest) % per):
            nibs.append(pad[body + k])
    cws = [hamming(n, 4 if k < sf - 2 else cr) for k, n in enumerate(nibs)]
    words, k, first = [], 0, True
    while k < len(cws):
        sf_app = sf - 2 if (first or ldro) else sf
        cw_len = 8 if first else 4 + cr
        blk = cws[k:k + sf_app]
        cb = [bits(c, cw_len) for c in blk] + [[0] * cw_len] * (sf_app - len(blk))
        for i in range(cw_len):
            w = [0] * sf
            for j in range(sf_app):
                w[j] = cb[(i - j - 1) % sf_app][i]
            if first or ldro:
                w[sf_app] = sum(w[:sf_app]) % 2
            words.append(val(w))
        k += sf_app
        first = False
    N = 1 << sf
    out = []
    for w in words:
        y = w
        for j in range(1, sf):
            y ^= w >> j
        out.append((y + 1) % N)
    return out


def chirp(sym, sf, os=1):
    N = 1 << sf
    n = np.arange(N * os)
    fold = N * os - sym * os
    f = np.where(n < fold, sym / N - 0.5, sym / N - 1.5)
    return np.exp(2j * np.pi * (n * n / (2 * N) / os ** 2 + f * n / os))


def modulate(symbols, sf, sync=0x12, preamble=8, os=1):
    N = (1 << sf) * os
    up = chirp(0, sf, os)
    down = np.conj(up)
    s0, s1 = ((sync >> 4) & 0xF) << 3, (sync & 0xF) << 3
    parts = [up] * preamble + [chirp(s0, sf, os), chirp(s1, sf, os), down, down, down[:N // 4]]
    parts += [chirp(s, sf, os) for s in symbols]
    return np.concatenate(parts)


# --- receive -----------------------------------------------------------------
def gray(v):
    return v ^ (v >> 1)


def decode_symbols(syms, sf, cr=None, has_crc=None, length=None, implicit=False, ldro=False):
    """Symbol values (FFT bins) -> (payload, header dict, crc_ok)."""
    N = 1 << sf
    info = {}
    k, first, cws_nibs = 0, True, []
    cr_now = 4
    while k < len(syms):
        sf_app = sf - 2 if (first or ldro) else sf
        cw_len = 8 if first else 4 + cr_now
        blk = syms[k:k + cw_len]
        if len(blk) < cw_len:
            break
        rows = []
        for s in blk:
            v = (s - 1) % N
            if first or ldro:
                v //= 4
            rows.append(bits(gray(v), sf_app))
        cw = [[0] * cw_len for _ in range(sf_app)]
        for i in range(cw_len):
            for j in range(sf_app):
                cw[(i - j - 1) % sf_app][i] = rows[i][j]
        for c in cw:                # data bits are the first four, LSB first
            cws_nibs.append(c[3] << 3 | c[2] << 2 | c[1] << 1 | c[0])
        k += cw_len
        if first:
            if not implicit:
                h = cws_nibs[:5]
                length = (h[0] << 4) | h[1]
                cr = h[2] >> 1
                has_crc = h[2] & 1
                info = {'length': length, 'cr': cr, 'crc': has_crc,
                        'checksum_ok': header_nibbles(length, cr, has_crc)[3:] == h[3:5]}
                cws_nibs = cws_nibs[5:]
            cr_now = cr
            first = False
    need = 2 * length + (4 if has_crc else 0)
    nibs = cws_nibs[:need]
    if len(nibs) < need:
        return None, info, False
    p = bytes(((nibs[2 * i] | nibs[2 * i + 1] << 4) ^ WHITEN[i]) for i in range(length))
    ok = None
    if has_crc:
        c = nibs[2 * length:2 * length + 4]
        got = c[0] | c[1] << 4 | c[2] << 8 | c[3] << 12
        info['crc_rx'] = got
        ok = got == payload_crc(p)
    return p, info, ok
