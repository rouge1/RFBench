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
    python scripts/dmr_demod.py REC.cdif --chan ... --recover
    python scripts/dmr_demod.py REC.cdif --chan ... --matched

A .cdif is Sceptre's BLUE / X-Midas file (knowledge/ptt.md): 512-byte header, complex
int16 ('CI') or float32 ('CF') samples. It is read in chunks; run it under a memory cap
(systemd-run --user --scope -p MemoryMax=4G) on long spans.
"""
import argparse
import struct
import sys

import numpy as np
from scipy.signal import firwin, lfilter, resample_poly

import dmr_fec as F

SYMBOL_RATE = 4800
SPS = 10
FS_SYM = SYMBOL_RATE * SPS            # 48 kS/s
DEV = 648.0                           # Hz per symbol unit: +-1 -> 648, +-3 -> 1944 (h = 0.27)

# One timeslot is 30 ms, so one logical channel's bursts are 60 ms apart:
# 4800 sym/s * 0.06 s = 288 symbols. A voice superframe is 6 of those, 360 ms.
BURST_SPACING_SYMS = 288
SUPERFRAME_BURSTS = 6
SUPERFRAME_SYMS = BURST_SPACING_SYMS * SUPERFRAME_BURSTS   # 1728

# Bursts B-F of a voice superframe (clause 7.1.3, Table 9.20): LC first,
# continuation, continuation, last, then a single-fragment Null or RC.
LCSS_BF = (0b01, 0b11, 0b11, 0b10, 0b00)

# Clause 5.1.2.2: the first burst A is immediately preceded by a voice LC
# header or a PI header. The burst immediately after F is a data burst
# (a terminator with LC on an inbound channel). Those two sit on this slot's
# grid, so they time a superframe whose own voice SYNC was not kept.
ANCHOR_HEADERS = ('Voice LC Header', 'PI Header')

# Centre field of a voice burst with embedded signalling (Table E.6):
# EMB(15)..EMB(8), 32 embedded bits, EMB(7)..EMB(0).
EMB_SPLIT = 8

# Recovery gates. Tuned on synthetic calls; see NOTES_decoder.md.
# A candidate needs 4 of 5 B-F EMBs within this many bits of a codeword, the
# neighbour's colour code, and the LCSS sequence above with at most one miss.
RECOVER_MAX_EMB_ERR = 1
RECOVER_MIN_EMB = 4
RECOVER_MAX_LCSS_MISMATCH = 1
# Decision-directed residual, in symbol units, after refitting the neighbour's
# scale and offset on bursts B-F. Also applied per voice frame. Measured on
# synthetic calls (NOTES_decoder.md): a clean superframe sits near 0.23, one
# frame of the 12 dB superframe this search recovered reached 0.48, and
# squelched noise refits to about 0.30-0.47. The gate does not separate those.
# It rejects a fit that never found the four levels. The EMB check rejects noise.
RECOVER_MAX_LEVEL_RMS = 0.75
# find_syncs' peak wanders by a sample, so a neighbour predicts burst A to
# within a few samples. The search keeps the alignment with the open eye.
RECOVER_TIMING_RADIUS = 4
# Symbol centres closer than this to either end of the discriminator array are
# not returned: the 101-tap channel filter and the 8-symbol RRC have no
# history there, so a cut file would otherwise quantize the edge to some level.
RECOVER_EDGE = 160

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


def emb_bits(burst_bits):
    """The 16 EMB bits of a voice burst: the 8 bits before the embedded field and the 8 after it."""
    centre = burst_bits[108:156]
    return centre[:EMB_SPLIT] + centre[-EMB_SPLIT:]


# Symbol spans of the three 72-bit voice frames inside a 132-symbol burst.
# Frame 2 is split by the 24-symbol centre (symbols 54..77).
_FRAME_SYMS = (((0, 36),), ((36, 54), (78, 96)), ((96, 132),))
_EMB_SYMS = ((54, 58), (74, 78))


def _quantize(v):
    return np.where(v > 2, 3, np.where(v > 0, 1, np.where(v > -2, -1, -3)))


def _symbol_index(sync_pos, a, b):
    """Sample index of symbols [a, b) of the burst whose centre starts at sync_pos."""
    return (sync_pos - 54 * SPS) + SPS * np.arange(a, b)


def _in_array(d, idx, edge=0):
    return len(idx) > 0 and idx[0] >= edge and idx[-1] < len(d) - edge


def _read_spans(d, sync_pos, spans, scale, off, edge=0):
    """(bits, rms) of those symbol spans, or None when one falls outside the array."""
    bits, chunks = [], []
    for a, b in spans:
        idx = _symbol_index(sync_pos, a, b)
        if not _in_array(d, idx, edge):
            return None
        v = (d[idx] - off) / scale
        sym = _quantize(v)
        bits += symbols_to_bits(sym)
        chunks.append(v - sym)
    resid = np.concatenate(chunks)
    return bits, float(np.sqrt(np.mean(resid ** 2)))


def read_emb_at(d, sync_pos, scale, off, edge=0):
    """16 EMB bits at a voice-burst centre, or None if those symbols are outside `d`."""
    got = _read_spans(d, sync_pos, _EMB_SYMS, scale, off, edge)
    return None if got is None else got[0]


def read_voice_frames_at(d, sync_pos, scale, off, max_rms=None, edge=0):
    """The three 72-bit frames at `sync_pos`. An entry is None when its symbols are
    outside the array, or (when max_rms is set) when they do not sit on the four levels."""
    frames = []
    for spans in _FRAME_SYMS:
        got = _read_spans(d, sync_pos, spans, scale, off, edge)
        if got is None or (max_rms is not None and got[1] > max_rms):
            frames.append(None)
        else:
            frames.append(got[0])
    return frames


def refit_levels(d, pos, scale, off):
    """(scale, offset, rms) from the in-range symbols of bursts B-F, or None.

    `pos` is the sample where burst A's centre would start. Two decision-directed
    passes, seeded with the neighbour's scale and offset. rms is in symbol units."""
    chunks = []
    for k in range(1, SUPERFRAME_BURSTS):
        idx = _symbol_index(pos + k * BURST_SPACING_SYMS * SPS, 0, 132)
        keep = idx[(idx >= RECOVER_EDGE) & (idx < len(d) - RECOVER_EDGE)]
        if len(keep):
            chunks.append(d[keep])
    if not chunks:
        return None
    v = np.concatenate(chunks)
    if len(v) < 64:
        return None
    sc, of = float(scale), float(off)
    sym = None
    for _ in range(2):
        if not 300 < sc < 1000:
            return None
        sym = _quantize((v - of) / sc).astype(float)
        (sc, of), *_ = np.linalg.lstsq(np.vstack([sym, np.ones(len(sym))]).T, v, rcond=None)
        sc, of = float(sc), float(of)
    if not 300 < sc < 1000:
        return None
    sym = _quantize((v - of) / sc).astype(float)
    resid = (v - of) / sc - sym
    return sc, of, float(np.sqrt(np.mean(resid ** 2)))


def superframe_colour(d, pos, scale, off):
    """Colour code shared by bursts B-F, or None when fewer than 4 agree."""
    votes = []
    for k in range(1, SUPERFRAME_BURSTS):
        bits = read_emb_at(d, pos + k * BURST_SPACING_SYMS * SPS, scale, off)
        if bits is None:
            continue
        cc, _pi, _lcss, err = F.emb_decode(bits)
        if err <= RECOVER_MAX_EMB_ERR:
            votes.append(cc)
    if len(votes) < RECOVER_MIN_EMB:
        return None
    best = max(set(votes), key=votes.count)
    if votes.count(best) < RECOVER_MIN_EMB:
        return None
    return best


def superframe_candidate(d, pos, scale, off, cc, radius=RECOVER_TIMING_RADIUS):
    """Whether the superframe whose burst A would sit near `pos` is really there.

    A found neighbour is a sample or two off the symbol grid, so this tries
    `pos` and a few samples either side, keeps the alignment whose B-F symbols
    sit closest to the four levels, and accepts it only when that residual is
    small and at least 4 of the 5 EMBs decode to `cc` with at most one bit
    error and the LCSS sequence has at most one mismatch.
    Returns (ok, aligned position, scale, offset)."""
    best = None
    for dt in range(-radius, radius + 1):
        fit = refit_levels(d, pos + dt, scale, off)
        if fit is None:
            continue
        if best is None or fit[2] < best[3]:
            best = (pos + dt, fit[0], fit[1], fit[2])
    if best is None or best[3] > RECOVER_MAX_LEVEL_RMS:
        return False, pos, scale, off
    pos, sc, of, _rms = best
    n_ok = mismatches = 0
    for k, expect in enumerate(LCSS_BF, start=1):
        bits = read_emb_at(d, pos + k * BURST_SPACING_SYMS * SPS, sc, of)
        if bits is None:
            mismatches += 1
            continue
        ecc, _pi, lcss, err = F.emb_decode(bits)
        if not (err <= RECOVER_MAX_EMB_ERR and ecc == cc):
            mismatches += 1
            continue
        n_ok += 1
        if lcss != expect:
            mismatches += 1
    ok = n_ok >= RECOVER_MIN_EMB and mismatches <= RECOVER_MAX_LCSS_MISMATCH
    return ok, pos, sc, of


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
def discriminate(x, fs, squelch=None, matched=False):
    """Complex baseband at fs, channel at 0 Hz -> instantaneous frequency in Hz at 48 kS/s.

    squelch: zero the output wherever the channel power, averaged over a symbol, is
    below this fraction of its peak. Needed for a handheld's bursts (off half the time):
    on noise alone the discriminator swings by kilohertz, and find_syncs' threshold is
    relative to the largest correlation in the span. 0.25 works; None leaves it off.

    matched: the 4FSK frequency pulse is root-raised-cosine (clause 10.2.2.2), so the
    symbol decision can use that filter instead of the length-10 boxcar. Default False
    keeps the boxcar, and that output is unchanged. The RRC is 161 taps, odd and
    symmetric, and convolve(mode='same') centres it, so it adds no group delay on top
    of the 101-tap low-pass (50 samples at 48 kS/s). The array is still one sample
    shorter than the 48 kS/s baseband, which is what find_syncs, fit_levels and
    read_burst take."""
    if fs != FS_SYM:
        from fractions import Fraction
        r = Fraction(FS_SYM / fs).limit_denominator(10000)
        x = resample_poly(x, r.numerator, r.denominator)
    if len(x) <= SPS:                    # shorter than a symbol: nothing to read
        return np.zeros(max(len(x) - 1, 0))
    x = lfilter(firwin(101, 6000, fs=FS_SYM), 1, x)
    d = np.angle(x[1:] * np.conj(x[:-1])) * FS_SYM / (2 * np.pi)
    if matched:
        d = np.convolve(d, rrc(), mode='same')
    else:
        d = np.convolve(d, np.ones(SPS) / SPS, mode='same')
    if squelch is not None:
        p = np.convolve(np.abs(x) ** 2, np.ones(SPS) / SPS, mode='same')[1:]
        if np.max(p) > 0:
            d = np.where(p > squelch * np.max(p), d, 0.0)
    return d


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


def _complete_superframe(d, pos, scale, off):
    """18 voice frames starting at burst A, or None if any burst is outside `d`."""
    frames = []
    for k in range(SUPERFRAME_BURSTS):
        b = read_burst(d, pos + k * BURST_SPACING_SYMS * SPS, scale, off)
        if b is None:
            return None
        frames += voice_frames(b)
    return frames


def _near(pos, others, tol):
    return any(abs(pos - p) <= tol for p in others)


def anchor_delta_bursts():
    """Bursts from a control burst to burst A, on the same slot.

    A voice LC header or PI header is the one burst before the first A, so the
    delta is +1. The terminator is the one burst after F, and F is five bursts
    after A, so the delta is -6. Both numbers are the superframe geometry:
    6 bursts, header immediately before, terminator immediately after.
    """
    return 1, -SUPERFRAME_BURSTS


def _paired_data_sync(voice):
    """Data SYNC that belongs with this voice SYNC, or None."""
    tail = '_VOICE'
    if not voice.endswith(tail):
        return None
    data = voice[:-len(tail)] + '_DATA'
    return data if data in SYNCS else None


def _append_candidate(d, pred, scale, off, cc, have, out):
    """Accept one predicted burst A. False when it is not a new superframe."""
    if _near(pred, have, 4 * SPS):
        return False
    ok, aligned, sc, of = superframe_candidate(d, pred, scale, off, cc)
    if not ok or _near(aligned, have, 4 * SPS):
        return False
    frames = []
    burst = BURST_SPACING_SYMS * SPS
    for k in range(SUPERFRAME_BURSTS):
        frames += read_voice_frames_at(
            d, aligned + k * burst, sc, of,
            max_rms=RECOVER_MAX_LEVEL_RMS, edge=RECOVER_EDGE)
    if not any(f is not None for f in frames):
        return False
    out.append({'pos': int(aligned), 'frames': frames})
    have.append(int(aligned))
    return True


def _recover_from_control(d, voice, thr, have, out):
    """Time a missing burst A from a header or a terminator on the same slot.

    The colour code is the one Golay decoded from that control burst. The
    candidate still has to pass superframe_candidate, so a control burst that
    is not actually beside a voice superframe does not invent one.
    """
    data = _paired_data_sync(voice)
    if data is None:
        return
    sy = sync_symbols(data)
    spacing = BURST_SPACING_SYMS * SPS
    step = SUPERFRAME_SYMS * SPS
    hdr_n, term_n = anchor_delta_bursts()
    guard = 5 * spacing
    for pos, cc, dt, _err in data_bursts(d, data, thr=thr):
        kind = DATA_TYPES[dt]
        if kind in ANCHOR_HEADERS:
            delta, stride = hdr_n * spacing, step
        elif kind == 'Terminator with LC':
            delta, stride = term_n * spacing, -step
        else:
            continue
        sc, off = fit_levels(d, pos, sy)
        pred = pos + delta
        while -guard < pred < len(d) + guard:
            if _near(pred, have, 4 * SPS):
                pred += stride
                continue
            if not _append_candidate(d, pred, sc, off, cc, have, out):
                break
            pred += stride


def voice_calls(d, voice='BS_VOICE', thr=0.6, recover=False, return_info=False):
    """Voice superframes: list of (sample of burst A's centre, 18 frames of 72 bits).

    recover=False (the default) is the SYNC-only search: a superframe is returned
    only when burst A's voice SYNC is found and all 18 frames lie inside `d`.

    recover=True also tries the sample one superframe (1728 symbols) before and
    after each found burst A, including positions that fall off the start or end
    of `d`, and a burst A timed from a voice LC header, a PI header or a
    terminator on the same slot. A candidate is kept only when bursts B-F pass
    superframe_candidate against that anchor's colour code. With
    return_info=False a recovered superframe is kept only when all 18 frames
    were read, so existing callers still see a list of complete frames. With
    return_info=True a frame that was not fully inside the recording (or that
    fails the level fit) is None.
    """
    sy = sync_symbols(voice)
    found = []
    for pos, sign in find_syncs(d, voice, thr):
        if sign < 0:
            continue
        scale, off = fit_levels(d, pos, sy)
        if not sync_ok(d, pos, sy, scale, off):
            continue
        frames = _complete_superframe(d, pos, scale, off)
        if frames is not None:
            found.append((int(pos), frames, float(scale), float(off)))
    if not recover:
        return [(pos, frames) for pos, frames, _sc, _of in found]

    out = [{'pos': pos, 'frames': frames} for pos, frames, _sc, _of in found]
    have = [pos for pos, _frames, _sc, _of in found]
    step = SUPERFRAME_SYMS * SPS
    for pos, _frames, scale, off in found:
        cc = superframe_colour(d, pos, scale, off)
        if cc is None:
            continue
        for pred in (pos - step, pos + step):
            _append_candidate(d, pred, scale, off, cc, have, out)
    _recover_from_control(d, voice, thr, have, out)
    out.sort(key=lambda s: s['pos'])
    if return_info:
        return [(s['pos'], s['frames']) for s in out]
    return [(s['pos'], s['frames']) for s in out if all(f is not None for f in s['frames'])]


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
    ap.add_argument('--squelch', type=float, help="e.g. 0.25 for a handheld's bursts; see discriminate()")
    ap.add_argument('--recover', action='store_true',
                    help='also return a voice superframe whose burst A SYNC was missed, '
                         'when a neighbour superframe or a header or terminator '
                         'times it and bursts B-F agree')
    ap.add_argument('--matched', action='store_true',
                    help='root-raised-cosine (alpha 0.2, span 8) instead of the '
                         'length-10 boxcar, on the discriminator output')
    a = ap.parse_args(argv)
    if a.centre is None:
        ap.error('--centre is needed')
    x, fs = channelize_blue(a.cdif, a.centre, a.chan, a.t0, a.t1)
    d = discriminate(x, fs, a.squelch, matched=a.matched)
    db = data_bursts(d)
    counts = {}
    for _, cc, dt, _ in db:
        counts[(cc, DATA_TYPES[dt])] = counts.get((cc, DATA_TYPES[dt]), 0) + 1
    print(f'data/control bursts: {len(db)}')
    for (cc, name), n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f'  CC {cc:2d}  {name:28s} {n}')
    calls = voice_calls(d, recover=a.recover)
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
