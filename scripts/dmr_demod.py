#!/usr/bin/env python3
"""DMR (ETSI TS 102 361-1) demodulator in numpy: syncs, slot types and voice frames.

From complex baseband to bursts: an FM discriminator at 10 samples a symbol, each
48-bit SYNC found by correlation, the four levels scaled from the SYNC's own 24 known
symbols, then per burst
  - a data/control burst: the 20-bit slot type around the SYNC, Golay(20,8) decoded to
    colour code and data type (clause 9.3, Table 9.22, Annex B.3.1);
  - a voice superframe: burst A has the voice SYNC, B-F follow every 288 symbols in the
    same slot; each burst's 216 payload bits are three 72-bit AMBE+2 frames
    (bits 0-71, 72-143 across the centre field, 144-215), as sent on the air.
It also builds and modulates bursts, for tests and synthetic files.

Checked against a real capture (knowledge/ptt-dmr.md): the voice frames of a whole
call came out bit for bit equal to Sceptre's own .ambe file, and the colour codes
agreed with Sceptre's. It does not deinterleave or FEC-decode the payloads (BPTC,
AMBE FEC), and it does not turn voice into audio.

    python scripts/dmr_demod.py REC.cdif --centre FC --chan F --from 6.5 --to 11.5
    python scripts/dmr_demod.py REC.cdif --chan ... --ambe-out call.ambe
    python scripts/dmr_demod.py REC.cdif --chan ... --compare sceptre.ambe

A .cdif is Sceptre's BLUE / X-Midas file (knowledge/ptt.md): 512-byte header, complex
int16 ('CI') or float32 ('CF') samples. It is read in chunks; run it under a memory cap
(systemd-run --user --scope -p MemoryMax=4G) on long spans.
"""
import argparse
import struct
import sys

import numpy as np
from scipy.signal import firwin, lfilter, resample_poly

SYMBOL_RATE = 4800
SPS = 10
FS_SYM = SYMBOL_RATE * SPS            # 48 kS/s
DEV = 648.0                           # Hz per symbol unit: +-1 -> 648, +-3 -> 1944 (h = 0.27)

# Table 9.2. Each data SYNC is the symbol-for-symbol negation of the voice SYNC beside it.
SYNCS = {
    'BS_VOICE': '755FD7DF75F7', 'BS_DATA': 'DFF57D75DF5D',
    'MS_VOICE': '7F7D5DD57DFD', 'MS_DATA': 'D5D7F77FD757',
    'RC': '77D55F7DFD77',
    'TDMA1_VOICE': '5D577F7757FF', 'TDMA1_DATA': 'F7FDD5DDFD55',
    'TDMA2_VOICE': '7DFFD5F55D5F', 'TDMA2_DATA': 'D7557F5FF7F5',
}

# Table 9.22
DATA_TYPES = ['PI Header', 'Voice LC Header', 'Terminator with LC', 'CSBK', 'MBC Header',
              'MBC Continuation', 'Data Header', 'Rate 1/2 Data', 'Rate 3/4 Data', 'Idle',
              'Rate 1 Data', 'Unified Single Block Data', 'Reserved 12', 'Reserved 13',
              'Reserved 14', 'Reserved 15']

# Table B.11, Golay(20,8) generator: rows are the 8 information bits, MSB first
GOLAY_G = np.array([[int(c) for c in r.split()] for r in """
1 0 0 0 0 0 0 0 0 0 1 1 1 1 0 1 1 0 1 0
0 1 0 0 0 0 0 0 1 1 0 1 1 0 0 1 1 0 0 1
0 0 1 0 0 0 0 0 0 1 1 0 1 1 0 0 1 1 0 1
0 0 0 1 0 0 0 0 0 0 1 1 0 1 1 0 0 1 1 1
0 0 0 0 1 0 0 0 1 1 0 1 1 1 0 0 0 1 1 0
0 0 0 0 0 1 0 0 1 0 1 0 1 0 0 1 0 1 1 1
0 0 0 0 0 0 1 0 1 0 0 1 0 0 1 1 1 1 1 0
0 0 0 0 0 0 0 1 1 0 0 0 1 1 1 0 1 0 1 1
""".strip().splitlines()], np.uint8)

_DIBIT_TO_SYM = {(0, 1): 3, (0, 0): 1, (1, 0): -1, (1, 1): -3}
_SYM_TO_DIBIT = {v: k for k, v in _DIBIT_TO_SYM.items()}


# --- bits and symbols ---------------------------------------------------------------
def hex_bits(h):
    n = 4 * len(h)
    return [int(b) for b in bin(int(h, 16))[2:].zfill(n)]


def bits_to_symbols(bits):
    return np.array([_DIBIT_TO_SYM[(bits[i], bits[i + 1])] for i in range(0, len(bits), 2)])


def symbols_to_bits(syms):
    return [b for s in syms for b in _SYM_TO_DIBIT[int(s)]]


def sync_symbols(name):
    return bits_to_symbols(hex_bits(SYNCS[name]))


# --- Golay(20,8) --------------------------------------------------------------------
_CODEWORDS = np.array([(np.array([(v >> (7 - i)) & 1 for i in range(8)]) @ GOLAY_G) % 2
                       for v in range(256)], np.uint8)


def golay2008_encode(value):
    return _CODEWORDS[value & 0xFF].tolist()


def golay2008_decode(bits):
    """20 bits -> (8-bit value, bit errors corrected), by minimum distance."""
    d = np.count_nonzero(_CODEWORDS != np.asarray(bits, np.uint8), axis=1)
    v = int(np.argmin(d))
    return v, int(d[v])


def slot_type(cc, dtype):
    """Colour code and data type -> the 20 slot-type bits, first half then second."""
    return golay2008_encode(((cc & 0xF) << 4) | (dtype & 0xF))


# --- bursts (264 bits = 132 symbols) -----------------------------------------------
def voice_burst(frames, centre_bits):
    """Three 72-bit AMBE+2 frames and the 48 centre bits (a SYNC or EMB + embedded LC)."""
    pay = list(frames[0]) + list(frames[1]) + list(frames[2])
    assert len(pay) == 216 and len(centre_bits) == 48
    return pay[:108] + list(centre_bits) + pay[108:]


def data_burst(info196, cc, dtype, sync='BS_DATA'):
    st = slot_type(cc, dtype)
    info196 = list(info196)
    assert len(info196) == 196
    return info196[:98] + st[:10] + hex_bits(SYNCS[sync]) + st[10:] + info196[98:]


def voice_frames(burst_bits):
    pay = burst_bits[:108] + burst_bits[156:]
    return pay[:72], pay[72:144], pay[144:]


def burst_slot_type(burst_bits):
    v, err = golay2008_decode(burst_bits[98:108] + burst_bits[156:166])
    return v >> 4, v & 0xF, err


# --- modulation (for tests and synthetic files) ------------------------------------
def rrc(alpha=0.2, sps=SPS, span=8):
    """Square-root raised cosine (clause 10.2.2.2: flat to 1920 Hz, zero above 2880 Hz)."""
    t = np.arange(-span * sps, span * sps + 1) / sps
    h = np.zeros_like(t)
    for i, x in enumerate(t):
        if abs(x) < 1e-9:
            h[i] = 1 - alpha + 4 * alpha / np.pi
        elif abs(abs(4 * alpha * x) - 1) < 1e-9:
            h[i] = alpha / np.sqrt(2) * ((1 + 2 / np.pi) * np.sin(np.pi / (4 * alpha))
                                         + (1 - 2 / np.pi) * np.cos(np.pi / (4 * alpha)))
        else:
            h[i] = (np.sin(np.pi * x * (1 - alpha)) + 4 * alpha * x * np.cos(np.pi * x * (1 + alpha))) \
                / (np.pi * x * (1 - (4 * alpha * x) ** 2))
    return h / h.sum()


def modulate(symbols, fs=FS_SYM):
    """4FSK: impulses at 4800/s through the RRC, then FM at 648 Hz a unit. Complex baseband."""
    sps = int(round(fs / SYMBOL_RATE))
    up = np.zeros(len(symbols) * sps)
    up[::sps] = symbols
    f = np.convolve(up, rrc(sps=sps) * sps, mode='same') * DEV
    return np.exp(2j * np.pi * np.cumsum(f) / fs)


# --- demodulation ----------------------------------------------------------------
def discriminate(x, fs):
    """Complex baseband at fs, channel at 0 Hz -> instantaneous frequency in Hz at 48 kS/s."""
    if fs != FS_SYM:
        from fractions import Fraction
        r = Fraction(FS_SYM / fs).limit_denominator(10000)
        x = resample_poly(x, r.numerator, r.denominator)
    x = lfilter(firwin(101, 6000, fs=FS_SYM), 1, x)
    d = np.angle(x[1:] * np.conj(x[:-1])) * FS_SYM / (2 * np.pi)
    return np.convolve(d, np.ones(SPS) / SPS, mode='same')


def find_syncs(d, name, thr=0.6, min_gap=100):
    """Sample positions of the first symbol of each SYNC `name`, with sign:
    +1 for the pattern itself, -1 for its negation (the matching data or voice SYNC)."""
    sy = sync_symbols(name)
    tmpl = np.zeros(24 * SPS)
    tmpl[::SPS] = sy
    dz = d - np.convolve(d, np.ones(2001) / 2001, mode='same')
    c = np.correlate(dz, tmpl, mode='valid')
    ref = np.max(np.abs(c)) if len(c) else 0
    hits, i = [], 0
    while i < len(c):
        if ref and abs(c[i]) > thr * ref:
            j = i + int(np.argmax(np.abs(c[i:i + 4 * SPS])))
            hits.append((j, 1 if c[j] > 0 else -1))
            i = j + min_gap * SPS
        else:
            i += 1
    return hits


def fit_levels(d, pos, sync_syms):
    """Least-squares Hz per unit and offset from the 24 known SYNC symbols at `pos`."""
    v = d[pos + SPS * np.arange(24)]
    (scale, off), *_ = np.linalg.lstsq(np.vstack([sync_syms, np.ones(24)]).T, v, rcond=None)
    return scale, off


def sync_ok(d, pos, sync_syms, scale, off, max_rms=0.6):
    """A real SYNC fits its four levels closely; a chance correlation does not."""
    if not 300 < abs(scale) < 1000:          # Hz a unit; nominal 648
        return False
    v = (d[pos + SPS * np.arange(24)] - off) / scale
    return float(np.sqrt(np.mean((v - sync_syms) ** 2))) < max_rms


def read_symbols(d, start, n, scale, off):
    idx = start + SPS * np.arange(n)
    if idx[0] < 0 or idx[-1] >= len(d):
        return None
    v = (d[idx] - off) / scale
    return np.where(v > 2, 3, np.where(v > 0, 1, np.where(v > -2, -1, -3)))


def read_burst(d, sync_pos, scale, off):
    """264 bits of the burst whose centre field starts at sample `sync_pos`."""
    sy = read_symbols(d, sync_pos - 54 * SPS, 132, scale, off)
    return None if sy is None else symbols_to_bits(sy)


def voice_calls(d, voice='BS_VOICE', thr=0.6):
    """Voice superframes: list of (sample of burst A's SYNC, [18 frames of 72 bits])."""
    sy = sync_symbols(voice)
    out = []
    for pos, sign in find_syncs(d, voice, thr):
        if sign < 0:
            continue
        scale, off = fit_levels(d, pos, sy)
        if not sync_ok(d, pos, sy, scale, off):
            continue
        frames = []
        for k in range(6):
            b = read_burst(d, pos + k * 288 * SPS, scale, off)
            if b is None:
                break
            frames += voice_frames(b)
        if len(frames) == 18:
            out.append((pos, frames))
    return out


def data_bursts(d, data='BS_DATA', thr=0.6, max_err=2):
    """Data/control bursts: list of (sample, colour code, data type, Golay errors)."""
    sy = sync_symbols(data)
    out = []
    for pos, sign in find_syncs(d, data, thr):
        if sign < 0:
            continue
        scale, off = fit_levels(d, pos, sy)
        if not sync_ok(d, pos, sy, scale, off):
            continue
        b = read_burst(d, pos, scale, off)
        if b is None:
            continue
        cc, dt, err = burst_slot_type(b)
        if err <= max_err:
            out.append((pos, cc, dt, err))
    return out


def frames_to_ambe(frames):
    """72-bit frames -> Sceptre .ambe bytes (9 bytes a frame, MSB first)."""
    return np.packbits(np.array(frames, np.uint8).reshape(-1)).tobytes()


def ambe_to_frames(data):
    return np.unpackbits(np.frombuffer(data, np.uint8)).reshape(-1, 72)


# --- Sceptre .cdif (BLUE) ------------------------------------------------------------
def read_blue_header(path):
    with open(path, 'rb') as f:
        h = f.read(512)
    if h[:4] != b'BLUE':
        raise ValueError(f'{path}: not a BLUE file')
    data_start, data_size = struct.unpack_from('<2d', h, 32)
    return {'data_start': int(data_start), 'data_size': int(data_size),
            'format': h[52:54].decode(), 'timecode': struct.unpack_from('<d', h, 56)[0],
            'xstart': struct.unpack_from('<d', h, 256)[0],
            'xdelta': struct.unpack_from('<d', h, 264)[0]}


def channelize_blue(path, centre_hz, chan_hz, t0, t1, chunk=4_000_000):
    """Mix one channel of a BLUE file to 0 Hz and decimate to about 1 MS/s, in chunks."""
    hd = read_blue_header(path)
    fs = 1.0 / hd['xdelta']
    dtype = {'CI': '<i2', 'CF': '<f4'}[hd['format']]
    mm = np.memmap(path, dtype=dtype, mode='r', offset=hd['data_start'],
                   shape=(hd['data_size'] // np.dtype(dtype).itemsize,))
    D = max(1, int(fs // 1e6))
    h = firwin(401, 300e3, fs=fs)
    n0, n1 = int(t0 * fs), int(t1 * fs)
    w = -2j * np.pi * (chan_hz - centre_hz) / fs
    tail, out = np.zeros(len(h) - 1, complex), []
    for a in range(n0, n1, chunk):
        b = min(a + chunk, n1)
        iq = mm[2 * a:2 * b].astype(np.float32)
        x = (iq[0::2] + 1j * iq[1::2]) * np.exp(w * np.arange(a, b))
        y = np.convolve(np.concatenate([tail, x]), h, mode='valid')
        tail = x[-(len(h) - 1):]
        out.append(y[(-(a - n0)) % D::D])
    return np.concatenate(out), fs / D


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('cdif')
    ap.add_argument('--chan', type=float, required=True, help='channel centre, Hz')
    ap.add_argument('--centre', type=float, help="the recording's centre, Hz (its RF_FREQ "
                    'keyword, which this script does not parse)')
    ap.add_argument('--from', dest='t0', type=float, default=0.0)
    ap.add_argument('--to', dest='t1', type=float, required=True)
    ap.add_argument('--ambe-out')
    ap.add_argument('--compare', help='a Sceptre .ambe file to compare the voice frames with')
    a = ap.parse_args(argv)
    if a.centre is None:
        ap.error('--centre is needed')
    x, fs = channelize_blue(a.cdif, a.centre, a.chan, a.t0, a.t1)
    d = discriminate(x, fs)
    db = data_bursts(d)
    counts = {}
    for _, cc, dt, _ in db:
        counts[(cc, DATA_TYPES[dt])] = counts.get((cc, DATA_TYPES[dt]), 0) + 1
    print(f'data/control bursts: {len(db)}')
    for (cc, name), n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f'  CC {cc:2d}  {name:28s} {n}')
    calls = voice_calls(d)
    frames = [f for _, fr in calls for f in fr]
    print(f'voice superframes: {len(calls)} ({len(frames)} frames)')
    if a.ambe_out:
        with open(a.ambe_out, 'wb') as f:
            f.write(frames_to_ambe(frames))
    if a.compare:
        ref = ambe_to_frames(open(a.compare, 'rb').read())
        got = np.array(frames, np.uint8).reshape(-1, 72) if frames else np.zeros((0, 72), np.uint8)
        best = [int(np.min(np.count_nonzero(ref != g, axis=1))) for g in got]
        exact = sum(b == 0 for b in best)
        print(f'compare: {exact} of {len(got)} frames found exactly in the {len(ref)} of {a.compare}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
