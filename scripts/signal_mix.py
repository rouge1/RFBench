#!/usr/bin/env python3
"""Build a file of many signals at once, as complex baseband, from a JSON spec.

    python scripts/signal_mix.py SPEC.json OUT.cf32

Written for the blind receiver games on the cable (2026-10-29 and -30): each
round was one of these files looped on the VSG60 into the BB60D, which
`scripts/vsg_play_loop.py` will do for any of them. Everything is synthesised
here, so a file's truth is its spec.

SPEC is {"fs": .., "length_s": .., "seed": .., "items": [ {...}, ... ]}. Each
item has "kind", "amp" (linear, of full scale), "offset_hz" from the centre and
"start_s"; the bursty kinds repeat "count" times "every_s" apart. The sum must
stay under full scale; build() refuses a spec that does not.

  cw       a steady carrier
  morse    text, wpm, [jitter]           keyed carrier, 5 ms raised-cosine edges
  ook      payload, chip_rate            1010 preamble, raw-chip sync 2DD4, Manchester
                                         (0 -> 01, 1 -> 10) of len + payload + CRC16
  fsk      payload, baud, dev_hz, [bad_crc]  55x4 2DD4 len payload CRC16, CP-2FSK, 1 = +dev
  pocsag   text, address, function, baud     alphanumeric, 2FSK +-4.5 kHz, 1 = -dev
  adsb     callsigns, icaos              Mode S DF17 TC4 identification, 1 Mb/s PPM, CRC-24;
                                         one aircraft per burst, cycling the list
  aprs     src, dst, path, info, dev_hz  AX.25 UI frame, HDLC + NRZI, Bell 202 AFSK on NBFM
  g3ruh    src, dst, path, info, dev_hz  the same frame at 9600 Bd: G3RUH scrambler, direct FM
  ais      msgs                          HDLC frames, GMSK BT 0.4, 9600 Bd; each message a
                                         list of [value, nbits] or ["text", s, nchars]
  rtty     text, baud, shift_hz          ITA2, 1 start, 1.5 stop, mark = +shift/2
  bell103  text, [mode]                  300 Bd 8N1, originate 1270/1070 Hz (answer 2225/2025)
  dtmf_fm  digits, tone_s, gap_s, dev_hz DTMF on NBFM
  same     header, dev_hz, [tone_s]      NWR SAME header x3, 1050 Hz tone, NNNN x3, on NBFM
  sstv     image, dev_hz                 Martin M1 on NBFM
  sstv_s1  image, dev_hz                 Scottie S1 on NBFM
  apt      image_a, image_b, lines, dev_hz   NOAA APT: 2400 Hz AM subcarrier on FM
  hell     text                          Feld-Hell, 245 px/s
  chirp    width_s, bw_hz, pri_s, dur_s  LFM radar pulses, rising
  css      sf, bw_hz, symbols | text     LoRa-style chirps: 8 upchirps, sync symbols 8 and 16,
                                         2.25 downchirps, then the raw symbol values. Not LoRa:
                                         no whitening, Hamming, interleaving, Gray or header
  hopper   text, nch, spacing_hz, dwell_s, hop_seed, baud, dev_hz, dur_s, [guard_s]
                                         CP-2FSK packets (55 55 2DD4 seq, 8 text bytes, CRC16),
                                         one per dwell on a fixed pseudo-random channel order
  dsss     payload, chip_rate            the fsk frame as DBPSK, each bit spread by Barker-11
  qam16    rate, alpha, dur_s            16QAM, RRC, random data
  ofdm     text, df_hz                   64 bins, CP 16, BPSK on 48, pilots at +-7 and +-21

CRC16 is CCITT-FALSE (poly 0x1021, init 0xFFFF) over the length byte and the
payload; AX.25 and AIS use the X.25 FCS. `scripts/test_signal_mix.py` checks the
codes against published values and has multimon-ng decode the kinds it knows.
"""
import json
import sys

import numpy as np
from scipy.signal import oaconvolve

MORSE = {'A': '.-', 'B': '-...', 'C': '-.-.', 'D': '-..', 'E': '.', 'F': '..-.',
         'G': '--.', 'H': '....', 'I': '..', 'J': '.---', 'K': '-.-', 'L': '.-..',
         'M': '--', 'N': '-.', 'O': '---', 'P': '.--.', 'Q': '--.-', 'R': '.-.',
         'S': '...', 'T': '-', 'U': '..-', 'V': '...-', 'W': '.--', 'X': '-..-',
         'Y': '-.--', 'Z': '--..', '0': '-----', '1': '.----', '2': '..---',
         '3': '...--', '4': '....-', '5': '.....', '6': '-....', '7': '--...',
         '8': '---..', '9': '----.', '/': '-..-.', '=': '-...-', '.': '.-.-.-'}


def crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def bytes_to_bits(data):
    return [(b >> (7 - i)) & 1 for b in data for i in range(8)]


def frame(payload, bad=False):
    body = bytes([len(payload)]) + payload
    c = crc16(body) ^ (0x1D0F if bad else 0)
    return body + bytes([c >> 8, c & 0xFF])


def edges(keying, fs, rise_s):
    """Smooth a 0/1 keying with a raised-cosine of width rise_s."""
    n = max(int(rise_s * fs), 1)
    k = np.hanning(2 * n + 1)
    return oaconvolve(keying, k / k.sum(), mode='same')


def morse(it, fs, rng):
    dot = 1.2 / it['wpm']
    jit = it.get('jitter', 0.0)
    marks = []                      # (on, seconds)
    for w, word in enumerate(it['text'].upper().split(' ')):
        if w:
            marks.append((0, 4 * dot))          # 3 after the letter + 4 = 7
        for c, ch in enumerate(word):
            if c:
                marks.append((0, 2 * dot))      # 1 after the element + 2 = 3
            for e in MORSE[ch]:
                marks.append((1, dot if e == '.' else 3 * dot))
                marks.append((0, dot))
    out = []
    for on, d in marks:
        d *= 1 + jit * rng.uniform(-1, 1)
        out.append(np.full(int(round(d * fs)), float(on)))
    return edges(np.concatenate(out), fs, 0.005)


def fm_bits(bits, baud, dev, fs):
    sps = fs / baud
    n = int(round(len(bits) * sps))
    idx = np.minimum((np.arange(n) / sps).astype(int), len(bits) - 1)
    f = np.where(np.asarray(bits)[idx] > 0, dev, -dev)
    return np.exp(1j * 2 * np.pi * np.cumsum(f) / fs)


def ook(it, fs, rng):
    chips = [1, 0] * 24 + bytes_to_bits(b'\x2d\xd4')
    for b in bytes_to_bits(frame(it['payload'].encode())):
        chips += [0, 1] if b == 0 else [1, 0]
    sps = fs / it['chip_rate']
    n = int(round(len(chips) * sps))
    idx = np.minimum((np.arange(n) / sps).astype(int), len(chips) - 1)
    burst = edges(np.asarray(chips, float)[idx], fs, 0.1 / it['chip_rate'])
    return repeat(burst, it, fs)


def fsk(it, fs, rng):
    bits = bytes_to_bits(b'\x55' * 4 + b'\x2d\xd4' + frame(it['payload'].encode(), it.get('bad_crc', False)))
    burst = fm_bits(bits, it['baud'], it['dev_hz'], fs)
    return repeat(burst, it, fs)


def repeat(burst, it, fs):
    step = int(round(it['every_s'] * fs))
    out = np.zeros(step * (it['count'] - 1) + len(burst), complex)
    for k in range(it['count']):
        out[k * step:k * step + len(burst)] += burst
    return out


# --- POCSAG -----------------------------------------------------------------
SYNC, IDLE = 0x7CD215D8, 0x7A89C197


def bch(data21):
    """BCH(31,21) with g = 0x769, then even parity: a 32-bit codeword."""
    cw = data21 << 10
    for i in range(30, 9, -1):
        if cw & (1 << i):
            cw ^= 0x769 << (i - 10)
    cw = (data21 << 10) | cw
    cw = (cw << 1) | (bin(cw).count('1') & 1)
    return cw


def pocsag_codewords(address, function, text):
    bits = []
    for ch in text:
        v = ord(ch) & 0x7F
        bits += [(v >> i) & 1 for i in range(7)]          # LSB first
    bits += [(0x04 >> i) & 1 for i in range(7)]           # EOT
    while len(bits) % 20:
        bits.append(0)
    msg = [bch((1 << 20) | int(''.join(map(str, bits[i:i + 20])), 2))
           for i in range(0, len(bits), 20)]
    frame_no = address & 7
    words = [IDLE] * (2 * frame_no)
    words.append(bch(((address >> 3) << 2) | function))
    words += msg
    words.append(IDLE)
    out = []
    while words:
        batch, words = words[:16], words[16:]
        batch += [IDLE] * (16 - len(batch))
        out += [SYNC] + batch
    return out


def pocsag(it, fs, rng):
    bits = [1, 0] * 288                                   # 576-bit preamble
    for cw in pocsag_codewords(it['address'], it['function'], it['text']):
        bits += [(cw >> (31 - i)) & 1 for i in range(32)]
    # POCSAG sends a 1 as the lower frequency.
    burst = fm_bits([1 - b for b in bits], it['baud'], 4500.0, fs)
    return repeat(burst, it, fs)


def placed(burst, it, fs):
    return repeat(burst, it, fs) if it.get('count', 1) > 1 else burst


# --- ADS-B -------------------------------------------------------------------
def crc24(bits):
    g = 0x1FFF409
    d = list(bits) + [0] * 24
    for i in range(len(bits)):
        if d[i]:
            for j in range(25):
                d[i + j] ^= (g >> (24 - j)) & 1
    return d[-24:]


CHARSET = '#ABCDEFGHIJKLMNOPQRSTUVWXYZ##### ###############0123456789######'


def adsb_bits(icao, call):
    bits = [int(b) for b in '10001' + '101']           # DF17, CA5
    bits += [(icao >> (23 - i)) & 1 for i in range(24)]
    me = [int(b) for b in format(4, '05b') + '000']     # TC4, category 0
    for ch in call.ljust(8)[:8]:
        me += [int(b) for b in format(CHARSET.index(ch), '06b')]
    bits += me
    return bits + crc24(bits)


def adsb(it, fs, rng):
    half = int(round(0.5e-6 * fs))
    step = int(round(it['every_s'] * fs))
    out = np.zeros(step * (it['count'] - 1) + 240 * half, complex)
    for k in range(it['count']):
        j = k % len(it['callsigns'])
        chips = [1, 0, 1, 0, 0, 0, 0, 1, 0, 1, 0, 0, 0, 0, 0, 0]  # 8 us preamble
        for b in adsb_bits(it['icaos'][j], it['callsigns'][j]):
            chips += [1, 0] if b else [0, 1]
        burst = np.repeat(np.asarray(chips, float), half)
        out[k * step:k * step + len(burst)] += burst
    return out


# --- APRS / AX.25 -------------------------------------------------------------
def fcs16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


def ax25_addr(call, last):
    c, _, ssid = call.partition('-')
    a = bytes((ord(x) << 1) for x in c.ljust(6))
    return a + bytes([0x60 | (int(ssid or 0) << 1) | (1 if last else 0)])


def aprs(it, fs, rng):
    calls = [it['dst'], it['src']] + it.get('path', [])
    hdr = b''.join(ax25_addr(c, i == len(calls) - 1) for i, c in enumerate(calls))
    body = hdr + b'\x03\xf0' + it['info'].encode()
    f = fcs16(body)
    body += bytes([f & 0xFF, f >> 8])
    bits = []                                       # LSB first, bit stuffing
    ones = 0
    for byte in body:
        for i in range(8):
            b = (byte >> i) & 1
            bits.append(b)
            ones = ones + 1 if b else 0
            if ones == 5:
                bits.append(0); ones = 0
    flag = [0, 1, 1, 1, 1, 1, 1, 0]
    bits = flag * 40 + bits + flag * 3
    tones, level = [], 1                            # NRZI: 0 = change
    for b in bits:
        if b == 0:
            level ^= 1
        tones.append(level)
    sps = fs / 1200
    n = int(round(len(tones) * sps))
    idx = np.minimum((np.arange(n) / sps).astype(int), len(tones) - 1)
    af = np.where(np.asarray(tones)[idx] == 1, 1200.0, 2200.0)
    audio = np.sin(2 * np.pi * np.cumsum(af) / fs)
    burst = np.exp(1j * 2 * np.pi * it['dev_hz'] * np.cumsum(audio) / fs)
    return placed(burst, it, fs)


# --- RTTY ---------------------------------------------------------------------
LTRS = {c: i for i, c in enumerate('\x00E\nA SIU\rDRJNFCKTZLWHYPQOBG\x00MXV\x00')}
FIGS = {c: i for i, c in enumerate('\x003\n- \x0787\r$4\',!:(5")2#6019?&\x00./;\x00')}


def rtty(it, fs, rng):
    codes, mode = [31, 31, 31], 'L'
    for ch in it['text'].upper():
        if ch in LTRS and ch not in ' \r\n':
            if mode != 'L': codes.append(31); mode = 'L'
            codes.append(LTRS[ch])
        elif ch in FIGS and ch not in ' \r\n':
            if mode != 'F': codes.append(27); mode = 'F'
            codes.append(FIGS[ch])
        else:
            codes.append(LTRS[ch])
    units = []                                       # in half-bits: start 2, data 2 each, stop 3
    for c in codes:
        units += [0, 0] + [b for i in range(5) for b in [(c >> i) & 1] * 2] + [1, 1, 1]
    units = [1] * 20 + units + [1] * 20
    sps = fs / it['baud'] / 2
    n = int(round(len(units) * sps))
    idx = np.minimum((np.arange(n) / sps).astype(int), len(units) - 1)
    f = np.where(np.asarray(units)[idx] == 1, it['shift_hz'] / 2, -it['shift_hz'] / 2)
    burst = np.exp(1j * 2 * np.pi * np.cumsum(f) / fs)
    return placed(burst, it, fs)


# --- radar chirp --------------------------------------------------------------
def chirp(it, fs, rng):
    n = int(round(it['width_s'] * fs)); pri = int(round(it['pri_s'] * fs))
    t = np.arange(n) / fs
    k = it['bw_hz'] / it['width_s']
    pulse = np.exp(1j * np.pi * k * (t - it['width_s'] / 2) ** 2)
    total = int(round(it['dur_s'] * fs))
    out = np.zeros(total, complex)
    for a in range(0, total - n, pri):
        out[a:a + n] = pulse
    return out


# --- LoRa-style CSS -------------------------------------------------------------
def css(it, fs, rng):
    sf, bw = it['sf'], it['bw_hz']
    M = 2 ** sf
    osr = fs / bw
    assert abs(osr - round(osr)) < 1e-9
    osr = int(round(osr)); n = M * osr
    t = np.arange(n) / fs; T = M / bw

    def up(s):
        f = (bw * ((s / M + t / T) % 1.0)) - bw / 2
        return np.exp(1j * 2 * np.pi * np.cumsum(f) / fs)
    down = np.conj(up(0))
    syms = it.get('symbols') or [ord(c) for c in it['text']]
    parts = [up(0)] * 8 + [up(8), up(16)] + [down, down, down[:n // 4]] + [up(s % M) for s in syms]
    burst = np.concatenate(parts)
    return placed(burst, it, fs)


# --- 16QAM ----------------------------------------------------------------------
def rrc(alpha, sps, span):
    t = np.arange(-span * sps, span * sps + 1) / sps
    h = np.zeros_like(t)
    for i, x in enumerate(t):
        if abs(x) < 1e-9:
            h[i] = 1 + alpha * (4 / np.pi - 1)
        elif alpha and abs(abs(4 * alpha * x) - 1) < 1e-9:
            h[i] = alpha / np.sqrt(2) * ((1 + 2 / np.pi) * np.sin(np.pi / 4 / alpha) + (1 - 2 / np.pi) * np.cos(np.pi / 4 / alpha))
        else:
            h[i] = (np.sin(np.pi * x * (1 - alpha)) + 4 * alpha * x * np.cos(np.pi * x * (1 + alpha))) / (np.pi * x * (1 - (4 * alpha * x) ** 2))
    return h / np.sqrt((h ** 2).sum())


def qam16(it, fs, rng):
    sps = int(round(fs / it['rate'])); nsym = int(it['dur_s'] * it['rate'])
    lv = np.array([-3, -1, 1, 3])
    s = (lv[rng.integers(0, 4, nsym)] + 1j * lv[rng.integers(0, 4, nsym)]) / np.sqrt(10)
    up = np.zeros(nsym * sps, complex); up[::sps] = s
    y = oaconvolve(up, rrc(it['alpha'], sps, 8), mode='same')
    return y / np.abs(y).max()


# --- DTMF on NBFM ---------------------------------------------------------------
DT = {'1': (697, 1209), '2': (697, 1336), '3': (697, 1477), 'A': (697, 1633),
      '4': (770, 1209), '5': (770, 1336), '6': (770, 1477), 'B': (770, 1633),
      '7': (852, 1209), '8': (852, 1336), '9': (852, 1477), 'C': (852, 1633),
      '*': (941, 1209), '0': (941, 1336), '#': (941, 1477), 'D': (941, 1633)}


def dtmf_fm(it, fs, rng):
    nt = int(it['tone_s'] * fs); ng = int(it['gap_s'] * fs)
    t = np.arange(nt) / fs
    parts = []
    for d in it['digits']:
        lo, hi = DT[d]
        parts += [0.5 * (np.sin(2 * np.pi * lo * t) + np.sin(2 * np.pi * hi * t)), np.zeros(ng)]
    audio = np.concatenate(parts)
    burst = np.exp(1j * 2 * np.pi * it['dev_hz'] * np.cumsum(audio) / fs)
    return placed(burst, it, fs)


# --- SSTV Martin M1 on NBFM -------------------------------------------------------
def sstv_m1_audio(img, fs):
    """img: 256x320x3 uint8 RGB. Returns audio (phase-continuous tones)."""
    segs = [(1900, .3), (1200, .01), (1900, .3), (1200, .03)]
    code = 44
    bits = [(code >> i) & 1 for i in range(7)]
    bits.append(sum(bits) & 1)
    segs += [(1100 if b else 1300, .03) for b in bits] + [(1200, .03)]
    px = 0.4576e-3
    for row in img:
        segs += [(1200, 4.862e-3), (1500, 0.572e-3)]
        for ch in (1, 2, 0):                     # G, B, R
            segs += [(1500 + 800 * float(v) / 255.0, px) for v in row[:, ch]]
            segs += [(1500, 0.572e-3)]
    f = []
    acc = 0.0
    for fr, d in segs:                            # sample-accurate durations
        acc += d * fs
        n = int(round(acc)) - len(f)
        f.extend([fr] * n)
    return np.sin(2 * np.pi * np.cumsum(np.asarray(f)) / fs)


def sstv(it, fs, rng):
    from PIL import Image
    img = np.asarray(Image.open(it['image']).convert('RGB').resize((320, 256)))
    afs = 50000.0
    a = sstv_m1_audio(img, afs)
    from scipy.signal import resample_poly
    up = int(round(fs / afs))
    assert abs(fs / afs - up) < 1e-9
    audio = resample_poly(a, up, 1)
    burst = np.exp(1j * 2 * np.pi * it['dev_hz'] * np.cumsum(audio) / fs)
    return placed(burst, it, fs)


# --- NWR SAME (EAS) on NBFM ---------------------------------------------------------
def same_audio(header, fs, tone_s=8.0):
    baud = 520.0 + 5.0 / 6.0
    def afsk(msg):
        data = b'\xab' * 16 + msg.encode()
        bits = [(b >> i) & 1 for b in data for i in range(8)]   # LSB first
        sps = fs / baud
        n = int(round(len(bits) * sps))
        idx = np.minimum((np.arange(n) / sps).astype(int), len(bits) - 1)
        f = np.where(np.asarray(bits)[idx] == 1, 4 * baud, 3 * baud)
        return np.sin(2 * np.pi * np.cumsum(f) / fs)
    gap = np.zeros(int(fs))
    parts = []
    for _ in range(3):
        parts += [afsk(header), gap]
    t = np.arange(int(tone_s * fs)) / fs
    parts += [0.9 * np.sin(2 * np.pi * 1050 * t), gap]
    for _ in range(3):
        parts += [afsk('NNNN'), gap]
    return np.concatenate(parts)


def same(it, fs, rng):
    afs = 50000.0
    a = same_audio(it['header'], afs, it.get('tone_s', 8.0))
    from scipy.signal import resample_poly
    up = int(round(fs / afs))
    assert abs(fs / afs - up) < 1e-9, 'fs must be a multiple of 50 kHz'
    audio = resample_poly(a, up, 1)
    burst = np.exp(1j * 2 * np.pi * it['dev_hz'] * np.cumsum(audio) / fs)
    return placed(burst, it, fs)


# --- frequency hopper ----------------------------------------------------------------
def hopper(it, fs, rng):
    """CP-2FSK packets, one per dwell, each on the next channel of a fixed pseudo-random
    order. Packet: 55 55, sync 2D D4, seq byte, 8 text bytes, CRC16 (ctf frame without len)."""
    text = it['text'].encode()
    chunks = [text[i:i + 8].ljust(8, b' ') for i in range(0, len(text), 8)]
    order = np.random.default_rng(it['hop_seed']).permutation(it['nch'])
    dwell = int(round(it['dwell_s'] * fs))
    total = int(round(it['dur_s'] * fs))
    out = np.zeros(total, complex)
    k = 0
    for a in range(0, total - dwell + 1, dwell):
        seq = k % len(chunks)
        body = bytes([seq]) + chunks[seq]
        c = crc16(body)
        bits = bytes_to_bits(b'\x55\x55\x2d\xd4' + body + bytes([c >> 8, c & 0xFF]))
        burst = fm_bits(bits, it['baud'], it['dev_hz'], fs)
        ch = order[k % it['nch']]
        f = (ch - (it['nch'] - 1) / 2) * it['spacing_hz']
        n = np.arange(len(burst))
        g = int(round(it.get('guard_s', 0) * fs))
        out[a + g:a + g + len(burst)] = burst * np.exp(2j * np.pi * f * n / fs)
        k += 1
    return out


# --- DSSS (Barker-11, DBPSK) -----------------------------------------------------------
BARKER11 = np.array([1, -1, 1, 1, -1, 1, 1, 1, -1, -1, -1], float)


def dsss(it, fs, rng):
    """Packets 55x4 2DD4 len payload CRC16 as DBPSK bits, each bit spread by Barker-11 at
    chip_rate; rectangular chips; continuous packets back to back with idle gap_s."""
    bits = bytes_to_bits(b'\x55' * 4 + b'\x2d\xd4' + frame(it['payload'].encode()))
    sym, ph = [], 1.0
    for b in bits:                        # differential: 1 = phase flip
        if b:
            ph = -ph
        sym.append(ph)
    chips = np.concatenate([s_ * BARKER11 for s_ in sym])
    spc = fs / it['chip_rate']
    assert abs(spc - round(spc)) < 1e-9
    burst = np.repeat(chips, int(round(spc))).astype(complex)
    return placed(burst, it, fs)


# --- simple OFDM ------------------------------------------------------------------------
def ofdm(it, fs, rng):
    """64-point OFDM at spacing df (fs_ofdm = 64*df), CP 16. Used bins -26..26 except 0;
    pilots at -21,-7,7,21 always +1; the 48 others carry BPSK bits (1 -> -1, 0 -> +1),
    MSB first, filled from bin -26 upward. Frame: 2 preamble symbols (all used bins +1),
    then text bytes, 6 per symbol, space-padded. Oversampled by zero-padded IFFT."""
    os_ = int(round(fs / (64 * it['df_hz'])))
    assert abs(fs / (64 * it['df_hz']) - os_) < 1e-9
    N = 64 * os_; cp = 16 * os_
    used = [k for k in range(-26, 27) if k != 0]
    pil = {-21, -7, 7, 21}
    data = [k for k in used if k not in pil]
    txt = it['text'].encode()
    txt += b' ' * (-len(txt) % 6)
    bits = bytes_to_bits(txt)
    syms = []
    for _ in range(2):
        X = np.zeros(N, complex)
        for k in used: X[k % N] = 1
        syms.append(X)
    for i in range(0, len(bits), 48):
        X = np.zeros(N, complex)
        for k in pil: X[k % N] = 1
        for k, b in zip(data, bits[i:i + 48]): X[k % N] = -1 if b else 1
        syms.append(X)
    out = []
    for X in syms:
        x = np.fft.ifft(X) * N / np.sqrt(52) / 2.5
        out.append(np.concatenate([x[-cp:], x]))
    burst = np.concatenate(out)
    return placed(burst, it, fs)


# --- AIS (GMSK 9600, HDLC) ---------------------------------------------------------------
def ais_sixbit(text, n):
    out = []
    for ch in text.upper().ljust(n, '@')[:n]:
        v = ord(ch)
        v = v - 64 if v >= 64 else v          # '@'=0 ... '_'=31, ' '=32 ... '?'=63
        out += [(v >> (5 - i)) & 1 for i in range(6)]
    return out


def ais_fields(fields):
    bits = []
    for val, n in fields:
        if isinstance(val, list):
            bits += val
        else:
            bits += [(val >> (n - 1 - i)) & 1 for i in range(n)]
    return bits


def ais(it, fs, rng):
    """One HDLC frame per message in it['msgs'] (each a list of [value, nbits] or
    ['text', s, nchars]); messages cycle one per burst. GMSK BT 0.4, 9600 Bd, NRZI."""
    frames = []
    for m in it['msgs']:
        fl = []
        for f in m:
            fl.append((ais_sixbit(f[1], f[2]), 0) if f[0] == 'text' else (f[0], f[1]))
        bits = ais_fields(fl)
        bits += [0] * (-len(bits) % 8)
        data = bytes(int(''.join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8))
        c = fcs16(data)
        data += bytes([c & 0xFF, c >> 8])
        hb, ones = [], 0
        for byte in data:                      # LSB first on air, bit stuffing
            for i in range(8):
                b = (byte >> i) & 1
                hb.append(b)
                ones = ones + 1 if b else 0
                if ones == 5:
                    hb.append(0); ones = 0
        flag = [0, 1, 1, 1, 1, 1, 1, 0]
        raw = [0, 1] * 12 + flag + hb + flag + [0] * 4
        lv, nrzi = 1, []
        for b in raw:
            if b == 0:
                lv ^= 1
            nrzi.append(lv)
        frames.append(nrzi)
    sps = fs / 9600
    bt = 0.4
    t = np.arange(-2 * int(round(sps)), 2 * int(round(sps)) + 1) / sps
    sig = np.sqrt(np.log(2)) / (2 * np.pi * bt)
    g = np.exp(-t ** 2 / (2 * sig ** 2)); g /= g.sum()
    step = int(round(it['every_s'] * fs))
    out = np.zeros(step * (it['count'] - 1) + int(len(frames[0]) * sps * 1.5) + 1000, complex)
    for k in range(it['count']):
        bits = frames[k % len(frames)]
        n = int(round(len(bits) * sps))
        idx = np.minimum((np.arange(n) / sps).astype(int), len(bits) - 1)
        nrz = np.where(np.asarray(bits)[idx] == 1, 1.0, -1.0)
        f = oaconvolve(nrz, g, mode='same') * 2400.0       # h = 0.5 -> dev 2400 Hz
        burst = np.exp(1j * 2 * np.pi * np.cumsum(f) / fs)
        out[k * step:k * step + n] += burst
    return out


# --- Feld-Hell ------------------------------------------------------------------------------
def hell(it, fs, rng):
    """Feld-Hell: text rendered 14 px high, sent column by column, bottom to top,
    245 px/s (17.5 columns/s), keyed carrier with 1 ms raised-cosine edges.

    The "122.5 baud" usually quoted counts the 7-row font's dots, each two of
    these pixels tall. Sent at 122.5 px/s it came out at half speed, and a
    receiver set for Feld-Hell drew the text slanted and stretched."""
    from PIL import Image, ImageDraw, ImageFont
    f = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf', 12)
    w = int(f.getlength(it['text'])) + 4
    im = Image.new('L', (w, 14), 0)
    ImageDraw.Draw(im).text((1, 0), it['text'], font=f, fill=255)
    a = np.asarray(im) > 100
    px = []
    for c in range(a.shape[1]):
        px += [1.0 if a[13 - r, c] else 0.0 for r in range(14)]
    sp = fs / 245.0
    n = int(round(len(px) * sp))
    idx = np.minimum((np.arange(n) / sp).astype(int), len(px) - 1)
    burst = edges(np.asarray(px)[idx], fs, 1e-3).astype(complex)
    return placed(burst, it, fs)


# --- NOAA APT ----------------------------------------------------------------------------
def apt(it, fs, rng):
    """APT: 2 lines/s, 2080 words/line at 4160 words/s, AM on a 2400 Hz subcarrier
    (depth: word 0..255 -> 0.13..1.0 envelope), then FM, dev_hz. image_a, image_b:
    909 px wide greyscale PNGs of equal height (lines)."""
    from PIL import Image
    A = np.asarray(Image.open(it['image_a']).convert('L').resize((909, it['lines'])), float)
    B = np.asarray(Image.open(it['image_b']).convert('L').resize((909, it['lines'])), float)
    syncA = [0] * 4 + [255, 255, 0, 0] * 7 + [0] * 7
    syncB = [255, 255, 255, 0, 0] * 7 + [0] * 4
    words = []
    for r in range(it['lines']):
        tel = 255 if (r // 8) % 2 else 40
        words += syncA + [0] * 47 + list(A[r]) + [tel] * 45
        words += syncB + [255] * 47 + list(B[r]) + [tel] * 45
    afs = 20800.0                                     # 5 samples per word
    env = np.repeat(np.asarray(words) / 255.0, 5)
    env = 0.13 + 0.87 * env
    t = np.arange(len(env)) / afs
    audio = env * np.sin(2 * np.pi * 2400 * t)
    from scipy.signal import resample_poly
    up = fs / afs
    assert abs(up - round(up)) < 1e-9
    audio = resample_poly(audio, int(round(up)), 1)
    burst = np.exp(1j * 2 * np.pi * it['dev_hz'] * np.cumsum(audio) / fs)
    return placed(burst, it, fs)


# --- Bell 103 (300 Bd AFSK, UART 8N1) as tones at RF ---------------------------------------
def bell103(it, fs, rng):
    """originate: mark 1270, space 1070 Hz (answer: 2225/2025), 300 Bd, 8N1, LSB first,
    complex tones at those offsets (as heard on USB tuned to the carrier offset)."""
    mark, space = (1270.0, 1070.0) if it.get('mode', 'orig') == 'orig' else (2225.0, 2025.0)
    bits = [1] * 150
    for ch in it['text'].encode():
        bits += [0] + [(ch >> i) & 1 for i in range(8)] + [1]
    bits += [1] * 60
    sps = fs / 300.0
    n = int(round(len(bits) * sps))
    idx = np.minimum((np.arange(n) / sps).astype(int), len(bits) - 1)
    f = np.where(np.asarray(bits)[idx] == 1, mark, space)
    burst = np.exp(1j * 2 * np.pi * np.cumsum(f) / fs)
    return placed(burst, it, fs)


# --- G3RUH 9600 packet ----------------------------------------------------------------------
def g3ruh(it, fs, rng):
    """AX.25 UI frame (as aprs), HDLC + bit stuffing, then G3RUH scrambler (x^17+x^12+1,
    self-synchronising, applied to NRZI output), direct 2-level FM +-dev, 9600 Bd,
    Gaussian-ish pulse shaping (BT 0.5)."""
    calls = [it['dst'], it['src']] + it.get('path', [])
    hdr = b''.join(ax25_addr(c, i == len(calls) - 1) for i, c in enumerate(calls))
    body = hdr + b'\x03\xf0' + it['info'].encode()
    f = fcs16(body)
    body += bytes([f & 0xFF, f >> 8])
    bits, ones = [], 0
    for byte in body:
        for i in range(8):
            b = (byte >> i) & 1
            bits.append(b)
            ones = ones + 1 if b else 0
            if ones == 5:
                bits.append(0); ones = 0
    flag = [0, 1, 1, 1, 1, 1, 1, 0]
    bits = flag * 64 + bits + flag * 8
    lv, nrzi = 0, []
    for b in bits:
        if b == 0:
            lv ^= 1
        nrzi.append(lv)
    sr = [0] * 17
    scr = []
    for b in nrzi:
        o = b ^ sr[11] ^ sr[16]
        scr.append(o)
        sr = [o] + sr[:-1]
    sps = fs / 9600
    n = int(round(len(scr) * sps))
    idx = np.minimum((np.arange(n) / sps).astype(int), len(scr) - 1)
    nrz = np.where(np.asarray(scr)[idx] == 1, 1.0, -1.0)
    t = np.arange(-2 * int(round(sps)), 2 * int(round(sps)) + 1) / sps
    sig = np.sqrt(np.log(2)) / (2 * np.pi * 0.5)
    g = np.exp(-t ** 2 / (2 * sig ** 2)); g /= g.sum()
    fdev = oaconvolve(nrz, g, mode='same') * it['dev_hz']
    burst = np.exp(1j * 2 * np.pi * np.cumsum(fdev) / fs)
    return placed(burst, it, fs)


# --- SSTV Scottie S1 ---------------------------------------------------------------------
def sstv_s1_audio(img, fs):
    segs = [(1900, .3), (1200, .01), (1900, .3), (1200, .03)]
    code = 60
    bits = [(code >> i) & 1 for i in range(7)]
    bits.append(sum(bits) & 1)
    segs += [(1100 if b else 1300, .03) for b in bits] + [(1200, .03)]
    px = 138.24e-3 / 320
    segs += [(1200, 9e-3)]                        # the one starting sync
    for row in img:
        segs += [(1500, 1.5e-3)]
        segs += [(1500 + 800 * float(v) / 255.0, px) for v in row[:, 1]]
        segs += [(1500, 1.5e-3)]
        segs += [(1500 + 800 * float(v) / 255.0, px) for v in row[:, 2]]
        segs += [(1200, 9e-3), (1500, 1.5e-3)]
        segs += [(1500 + 800 * float(v) / 255.0, px) for v in row[:, 0]]
    f, acc = [], 0.0
    for fr, d in segs:
        acc += d * fs
        f.extend([fr] * (int(round(acc)) - len(f)))
    return np.sin(2 * np.pi * np.cumsum(np.asarray(f)) / fs)


def sstv_s1(it, fs, rng):
    from PIL import Image
    from scipy.signal import resample_poly
    img = np.asarray(Image.open(it['image']).convert('RGB').resize((320, 256)))
    afs = 50000.0
    assert abs(fs / afs - round(fs / afs)) < 1e-9, 'fs must be a multiple of 50 kHz'
    audio = resample_poly(sstv_s1_audio(img, afs), int(round(fs / afs)), 1)
    burst = np.exp(1j * 2 * np.pi * it['dev_hz'] * np.cumsum(audio) / fs)
    return placed(burst, it, fs)


KINDS = {
    'morse': morse, 'ook': ook, 'fsk': fsk, 'pocsag': pocsag, 'adsb': adsb,
    'aprs': aprs, 'g3ruh': g3ruh, 'ais': ais, 'rtty': rtty, 'bell103': bell103,
    'dtmf_fm': dtmf_fm, 'same': same, 'sstv': sstv, 'sstv_s1': sstv_s1,
    'apt': apt, 'hell': hell, 'chirp': chirp, 'css': css, 'hopper': hopper,
    'dsss': dsss, 'qam16': qam16, 'ofdm': ofdm,
}


def build(spec):
    fs = float(spec['fs'])
    n = int(round(spec['length_s'] * fs))
    rng = np.random.default_rng(spec.get('seed', 1))
    out = np.zeros(n, np.complex64)
    t0 = np.arange(n) / fs
    for it in spec['items']:
        if it['kind'] == 'cw':
            sig = np.ones(n - int(it.get('start_s', 0) * fs))
        else:
            sig = KINDS[it['kind']](it, fs, rng)
        a = int(round(it.get('start_s', 0) * fs))
        b = min(n, a + len(sig))
        out[a:b] += (it['amp'] * sig[:b - a]
                     * np.exp(2j * np.pi * it['offset_hz'] * t0[a:b])).astype(np.complex64)
    peak = float(np.abs(out).max())
    if peak > 0.99:
        raise ValueError('peak %.3f is over full scale' % peak)
    return out, peak



def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__.split('\n\n')[1])
    with open(sys.argv[1]) as f:
        spec = json.load(f)
    try:
        out, peak = build(spec)
    except ValueError as e:
        sys.exit(str(e))
    out.tofile(sys.argv[2])
    print('wrote %s: %d samples, %.1f s, peak %.3f'
          % (sys.argv[2], len(out), len(out) / float(spec['fs']), peak))


if __name__ == '__main__':
    main()
