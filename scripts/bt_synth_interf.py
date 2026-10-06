#!/usr/bin/env python3
"""A hopping DH5 train with known interferers, and the same train without.

    python scripts/bt_synth_interf.py hop20_dh5_int_cw49_p20 --interferer cw49_p20 --seed 6101 --out DIR
    python scripts/bt_synth_interf.py --set interf [--out DIR]

For bluey-ox-walker's item 6 (see knowledge/bluey-test-signals.md, a section on
these files is still to be added). Its receiver loses bursts to real
interference in the room - a Wi-Fi access point constantly 20 to 28 dB over the
floor above 2452 MHz - and it needs files with *known* interferers and the truth
of which bursts they hit, each paired with a clean file of the same bursts, to
measure the loss and the wrong-channel rate with its DC block on and off.
Nothing is transmitted.

**The bursts.** 40 MS/s on 2441.0 MHz, one LAP, the master sending a DH5 every
master slot period (6 slots, 3.75 ms) on the adapted sequence of
``bt_synth_hop.afh_hop_fn`` over the map 31-50 (2433 to 2452 MHz), 800 bursts
(3 s) at 20 dB in 1 MHz. Some land on channel 39, the centre: the sidecar says
how many (``n_bursts_on_centre_channel``). Bursts alternate between symbol
phase 0 and 20 (``sps / 2``); the timing fraction is uniform per burst;
``air_bits`` is left out (``air_bits_omitted``). ``timing_frac``,
``symbol_phase`` are null at the top and live per burst (``per_burst_keys``);
``snr_db`` is the one constant, 20.0.

**The floor** is fixed for every file: ``NOISE_1MHZ = AMPLITUDE**2 / 100`` in
1 MHz, complex sample per-component sigma ``sqrt(NOISE_1MHZ * (fs / 1e6) / 2)``,
block ``i`` of it from ``default_rng([seed, 1, i])``, the bursts' phases and
timing from ``default_rng([seed, 2])``.

**One seed, 6101, for every file of the set**, so every interferer file is the
clean file plus the interferer, and nothing else: the same noise, bursts, timing
fractions and carrier phases. The interferer is added last, after the noise,
a block at a time, so where it is off the file is *bit for bit* the clean one,
and where it is on, ``file - clean`` is the interferer alone (to the rounding of
a float32 sum).

**The interferers** (``--interferer``, the rows of ``INTERFERERS``):

* ``cwCH_pL``: one continuous-wave tone *centred on channel CH* (49 = 2451 MHz,
  +10 MHz from the centre; 43 = 2445 MHz, +4 MHz), on for the whole file and
  phase-continuous across blocks. Its power is ``NOISE_1MHZ * 10**(L/10)``: L
  dB over the noise in 1 MHz, the tone being one frequency.
* ``wifi_const_p20``: Wi-Fi-like noise, complex Gaussian, flat between +11 and
  +20 MHz from the centre (2452 to 2461 MHz; the upper edge is the Nyquist
  limit, so nothing aliases), on for the whole file. Its power spectral density
  is ``10**(L/10)`` times the floor's, so in every 1 MHz of the band it is
  20 dB over the floor in that 1 MHz. It is white noise through a long FIR band-
  pass (``WIFI_TAPS`` Kaiser taps, passband edges ``WIFI_EDGE_MARGIN_MHZ`` outside
  the band, so the band itself is flat to 1 dB), done by overlap-save with the
  previous white samples carried across each block boundary, so there is no seam:
  the white noise of block ``i`` is ``default_rng([seed, 3, i])`` and the L - 1
  samples before sample 0 are ``default_rng([seed, 6])``.
* ``wifi_bursty_p20``: the same noise, same PSD while on, gated into frames of
  0.3 to 3 ms (length uniform, seeded) at 30 % duty, 5 us raised-cosine edges
  (the amplitude goes 0 to 1 as ``0.5 * (1 - cos(pi * (i + 0.5) / E))``). After
  each frame an idle gap of ``length * 0.7 / 0.3 * u``, ``u`` uniform in
  [0.5, 1.5], so the long-run duty is 0.30. The schedule is
  ``default_rng([seed, 4])`` and is written to the sidecar as
  ``interferer_frames: [{start_sample, end_sample}]``, the frame being every
  sample whose gate is above zero (the edges are inside it).
* ``both_p20``: the two tones (ch 49 and ch 43, +20 dB) and the bursty
  Wi-Fi noise at once.

**Per-burst truth** (``interferer_overlap``, ``interferer_power_in_band_db``;
``interferer_note`` in the sidecar states the definitions): a burst spans
``[start_sample, end_sample)``, ``end_sample = start_sample + bits * sps``, and
its channel band is the channel's centre +-0.5 MHz. The interferer overlaps it
if one is on during part of that span *and* its band reaches the channel band:
a tone only on its own channel; the Wi-Fi noise on every channel whose band
meets 2452..2461 MHz - of the map 31-50, **only channel 50** (2452 MHz, band
2451.5..2452.5, half of it in the Wi-Fi band, plus the filter's 0.05 MHz skirt); the bursty noise only while a
frame is on in the span. ``interferer_power_in_band_db`` is the time-averaged
interferer power in that 1 MHz band during the span, over the noise in 1 MHz, in
dB: a tone on its channel is its own level; the noise is ``level + 10 log10(MHz
of the channel's 1 MHz band, weighted by the FIR's actual squared response) + 10
log10(mean of the gate squared over the span)``; interferers on the same channel add in power; ``null`` where there
is no overlap.
"""
import argparse
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from scripts import bt_synth, bt_synth_hop as hop  # noqa: E402

LAP = 0x9E8B33
UAP = 0x47
CENTER_MHZ = 2441.0
MAP = list(range(31, 51))                  # 20 channels, 2433 to 2452 MHz
CLK0 = 0x0123400
FS = 40e6
PTYPE = 'DH5'
SEED = 6101
BURSTS = 800
SNR_DB = 20.0

#: Noise power in 1 MHz, the one floor of every file.
NOISE_1MHZ = bt_synth.AMPLITUDE ** 2 / 100

#: Samples in a block. Float64 temporaries of a block are 64 MB each.
BLOCK_SAMPLES = 2 ** 22

#: The Wi-Fi-like noise: its band in MHz from the centre, its FIR, the margin by
#: which the filter's passband edges lie outside the band, its duty, frame
#: lengths and raised-cosine edge.
WIFI_BAND_MHZ = (11.0, 20.0)
WIFI_TAPS = 2049
WIFI_KAISER_BETA = 8.0
WIFI_EDGE_MARGIN_MHZ = 0.05
DUTY = 0.30
FRAME_MS = (0.3, 3.0)
EDGE_US = 5.0

INTERFERER_NOTE = (
    "A burst spans [start_sample, end_sample), end_sample = start_sample + bits * sps; its channel "
    "band is channel_mhz +-0.5 MHz. interferer_overlap is true if an interferer is ON during any "
    "part of that span AND its band reaches the channel band: a cw tone only on its own channel; "
    "the wifi noise on a channel whose band holds at least 0.001 MHz of its filter's squared "
    "response, which in the map 31-50 is channel 50 only (its band 2451.5..2452.5 is about half "
    "inside the 2452..2461 MHz band); the bursty wifi noise only while a frame "
    "(interferer_frames, edges included) overlaps the span. interferer_power_in_band_db is 10 "
    "log10 of the interferer power in the channel's 1 MHz band, time-averaged over the span, over "
    "noise_1mhz: a tone on its channel is its level_db; the wifi noise is level_db + 10 log10(MHz "
    "of the channel band, weighted by the ACTUAL squared frequency response of the FIR the file "
    "was made with, gain 1 in the band, integrated over the channel's 1 MHz) + 10 log10(mean of "
    "the gate squared over the span), the gate being 1 for the constant noise. The FIR's 0.1 MHz "
    "skirt at the lower band edge, which starts 0.05 MHz below +11 MHz, is therefore IN the "
    "figure: channel 50 is about 17.3 dB, not the 16.99 dB of a brick wall. Interferers in the "
    "same band add in power; null when there is no overlap.")


def amplitude_for(snr_db):
    """|iq| of a constant-envelope burst of ``snr_db`` over the floor in 1 MHz."""
    return float(np.sqrt(NOISE_1MHZ * 10 ** (snr_db / 10)))


def noise_block(seed, index, n, fs):
    """Block ``index`` of the file's floor: ``n`` complex64 samples, white across
    the sample rate, from a generator of that block alone."""
    rng = np.random.default_rng([seed, 1, index])
    sigma = np.sqrt(NOISE_1MHZ * fs / 1e6 / 2)
    out = np.empty(n, dtype=np.complex64)
    out.real = rng.normal(0, sigma, n)
    out.imag = rng.normal(0, sigma, n)
    return out


# --- the interferers -----------------------------------------------------------

#: The files' interferers: name -> list of specs. A tone is on a channel; the
#: noise has a level in dB over the floor per MHz and is gated or not.
INTERFERERS = {
    'clean': [],
    'cw49_p10': [dict(kind='cw', channel=49, level_db=10.0)],
    'cw49_p20': [dict(kind='cw', channel=49, level_db=20.0)],
    'cw43_p10': [dict(kind='cw', channel=43, level_db=10.0)],
    'cw43_p20': [dict(kind='cw', channel=43, level_db=20.0)],
    'wifi_const_p20': [dict(kind='wifi', level_db=20.0, bursty=False)],
    'wifi_bursty_p20': [dict(kind='wifi', level_db=20.0, bursty=True)],
    'both_p20': [dict(kind='cw', channel=49, level_db=20.0),
                 dict(kind='cw', channel=43, level_db=20.0),
                 dict(kind='wifi', level_db=20.0, bursty=True)],
}


def tone_offset_hz(channel, center_mhz):
    """A tone centred on ``channel``, in Hz from the capture's centre."""
    return (hop.channel_mhz(channel) - center_mhz) * 1e6


def wifi_taps(fs=FS):
    """The band-pass FIR of the Wi-Fi-like noise: complex, ``WIFI_TAPS`` Kaiser
    taps, passband from ``WIFI_BAND_MHZ[0]`` less the margin to its top plus the
    margin (the top is Nyquist, so the upper skirt wraps through it and
    reappears as the 0.05 MHz just above -20 MHz, outside every map channel),
    gain 1 at the band centre. The group delay is (taps - 1) / 2 samples, which noise does not care
    about."""
    lo, hi = WIFI_BAND_MHZ[0] - WIFI_EDGE_MARGIN_MHZ, WIFI_BAND_MHZ[1] + WIFI_EDGE_MARGIN_MHZ
    centre, half = (lo + hi) / 2 * 1e6, (hi - lo) / 2 * 1e6
    m = np.arange(WIFI_TAPS) - (WIFI_TAPS - 1) / 2
    proto = np.sinc(2 * half / fs * m) * np.kaiser(WIFI_TAPS, WIFI_KAISER_BETA)
    taps = proto * np.exp(2j * np.pi * centre / fs * m)
    # gain 1 at the centre of the band
    gain = abs(np.sum(taps * np.exp(-2j * np.pi * centre / fs * np.arange(WIFI_TAPS))))
    return taps / gain


def wifi_white_sigma(level_db, fs):
    """Per-component sigma of the white noise that, through ``wifi_taps`` (gain
    1 in the band), has ``level_db`` over the floor in every MHz of the band."""
    return float(np.sqrt(NOISE_1MHZ * 10 ** (level_db / 10) * fs / 1e6 / 2))


class WifiNoise:
    """The Wi-Fi-like noise, a block at a time, in order. The white noise of
    block ``i`` is ``default_rng([seed, 3, i])`` (I, then Q), scaled by
    ``wifi_white_sigma``; ``taps - 1`` white samples of history, carried from the
    block before (for block 0, ``default_rng([seed, 6])``), go in front of each
    block and the filter's output is the last ``n`` samples of the full
    convolution, so a block boundary is not a seam."""

    def __init__(self, seed, level_db, fs):
        self.seed = seed
        self.fs = fs
        self.sigma = wifi_white_sigma(level_db, fs)
        self.taps = wifi_taps(fs)
        self.index = 0
        warm = np.random.default_rng([seed, 6])
        n = len(self.taps) - 1
        self.history = self.white(warm, n)

    def white(self, rng, n):
        out = np.empty(n, dtype=np.complex128)
        out.real = rng.normal(0, self.sigma, n)
        out.imag = rng.normal(0, self.sigma, n)
        return out

    def block(self, n):
        """The next ``n`` samples of filtered noise, complex128."""
        w = self.white(np.random.default_rng([self.seed, 3, self.index]), n)
        out = self.filter(w)
        self.index += 1
        return out

    def filter(self, w):
        """Overlap-save: filter ``w`` with the history in front, keep the history."""
        keep = len(self.taps) - 1
        x = np.concatenate([self.history, w])
        size = 1 << int(np.ceil(np.log2(len(x) + len(self.taps))))
        y = np.fft.ifft(np.fft.fft(x, size) * np.fft.fft(self.taps, size))
        self.history = x[-keep:].copy()
        return y[keep:keep + len(w)]


def frame_envelope(frame, n):
    """The gate (amplitude, 0..1) of one frame ``(start, end)`` at sample
    numbers ``n``: raised-cosine edges of ``EDGE_US`` at both ends, zero outside."""
    start, end = frame
    edge = max(1, int(round(EDGE_US * 1e-6 * FS)))
    r = np.minimum(n - start, end - 1 - n)
    g = np.where(r >= edge, 1.0, 0.5 * (1 - np.cos(np.pi * (r + 0.5) / edge)))
    return np.where(r >= 0, g, 0.0)


def frame_schedule(seed, total, fs, duty=None):
    """The frames of the bursty noise, ``[(start, end)]`` in samples, ending
    inside the file. A frame's length is uniform in ``FRAME_MS``; the gap after
    it is ``length * (1 - duty) / duty * u`` with ``u`` uniform in [0.5, 1.5], so
    the long-run duty is ``duty``. The first frame starts after a gap's worth of
    quiet drawn the same way."""
    duty = DUTY if duty is None else duty
    rng = np.random.default_rng([seed, 4])
    frames = []
    pos = int(rng.uniform(0, FRAME_MS[1] * 1e-3 * fs))
    while True:
        length = int(round(rng.uniform(*FRAME_MS) * 1e-3 * fs))
        if pos + length > total:
            break
        frames.append((pos, pos + length))
        pos += length + int(round(length * (1 - duty) / duty * rng.uniform(0.5, 1.5)))
    return frames


def gate_block(frames, lo, hi):
    """The gate (amplitude) of samples ``lo..hi``: the frames that meet them."""
    g = np.zeros(hi - lo)
    n = np.arange(lo, hi)
    for frame in frames:
        if frame[1] <= lo:
            continue
        if frame[0] >= hi:
            break
        a, b = max(frame[0], lo), min(frame[1], hi)
        g[a - lo:b - lo] = frame_envelope(frame, n[a - lo:b - lo])
    return g


def add_interferers(iq, specs, frames, seed, fs, center_mhz, block_samples=BLOCK_SAMPLES):
    """Add the interferers of ``specs`` to ``iq`` in place, a block at a time,
    after the noise. ``frames`` are the bursty noise's schedule."""
    if not specs:
        return
    tones = [(tone_offset_hz(s['channel'], center_mhz),
              float(np.sqrt(NOISE_1MHZ * 10 ** (s['level_db'] / 10))),
              float(np.random.default_rng([seed, 5, i]).uniform(0, 2 * np.pi)))
             for i, s in enumerate(specs) if s['kind'] == 'cw']
    wifi = [s for s in specs if s['kind'] == 'wifi']
    noise = WifiNoise(seed, wifi[0]['level_db'], fs) if wifi else None
    for lo in range(0, len(iq), block_samples):
        hi = min(lo + block_samples, len(iq))
        x = np.zeros(hi - lo, dtype=np.complex128)
        n = np.arange(lo, hi)
        for offset, amp, phase in tones:
            # the whole cycles go before the cast to radians: exact at any sample
            cycles = offset / fs * n
            x += amp * np.exp(1j * (2 * np.pi * (cycles - np.floor(cycles)) + phase))
        if noise is not None:
            y = noise.block(hi - lo)
            if wifi[0]['bursty']:
                y *= gate_block(frames, lo, hi)
            x += y
        iq[lo:hi] += x.astype(np.complex64)


# --- the truth, per burst ------------------------------------------------------

def frames_hit(frames, a, b):
    """Whether a frame is on during any part of samples ``a..b``."""
    return any(s < b and e > a for s, e in frames)


def mean_gain_squared(frames, a, b):
    """The mean of the gate squared over samples ``a..b``."""
    n = np.arange(a, b)
    total = 0.0
    for frame in frames:
        if frame[1] <= a:
            continue
        if frame[0] >= b:
            break
        lo, hi = max(frame[0], a), min(frame[1], b)
        total += float(np.sum(frame_envelope(frame, n[lo - a:hi - a]) ** 2))
    return total / (b - a)


#: A channel is reached by the Wi-Fi noise if this many MHz of its squared
#: response fall in the channel's 1 MHz band (a tone's skirts at -80 dB are not).
WIFI_REACH_MHZ = 1e-3


def wifi_overlap_table(channels, fs=FS, center_mhz=CENTER_MHZ):
    """``{channel: MHz}``: the squared frequency response of the FIR in use
    (gain 1 in the band), integrated over the channel's 1 MHz band. A full
    in-band channel is 1.0; channel 50, which the band edge cuts, about 0.55."""
    n = 1 << 18
    h = np.abs(np.fft.fft(wifi_taps(fs), n)) ** 2
    f = np.fft.fftfreq(n, 1 / fs) / 1e6
    df = fs / n / 1e6
    out = {}
    for channel in channels:
        c = hop.channel_mhz(channel) - center_mhz
        out[int(channel)] = float(np.sum(h[np.abs(f - c) < 0.5]) * df)
    return out


def burst_truth(specs, frames, start, end, channel, wifi_mhz):
    """``(interferer_overlap, interferer_power_in_band_db)`` of one burst."""
    hit = False
    power = 0.0                                # over the noise in 1 MHz, linear
    for s in specs:
        if s['kind'] == 'cw':
            if s['channel'] == channel:
                hit = True
                power += 10 ** (s['level_db'] / 10)
        else:
            mhz = wifi_mhz[channel]
            if mhz < WIFI_REACH_MHZ:
                continue
            if s['bursty']:
                if not frames_hit(frames, start, end):
                    continue
                g2 = mean_gain_squared(frames, start, end)
            else:
                g2 = 1.0
            hit = True
            power += 10 ** (s['level_db'] / 10) * mhz * g2
    if hit and power <= 0:
        raise ValueError("burst [%d, %d) on channel %d: the interferer overlaps it but its gated "
                         "energy is zero: overlap and gate disagree" % (start, end, channel))
    return hit, (10 * math.log10(power) if hit else None)


# --- the file ------------------------------------------------------------------

def synthesise_interf(interferer='clean', bursts=BURSTS, seed=SEED, snr_db=SNR_DB, lap=LAP,
                      uap=UAP, clk0=CLK0, fs=FS, center_mhz=CENTER_MHZ, channels=MAP,
                      specs=None, block_samples=BLOCK_SAMPLES):
    """The samples and the sidecar of a hopping DH5 train under ``interferer``
    (a key of ``INTERFERERS``, or ``specs``, a list of such specs)."""
    if specs is None:
        if interferer not in INTERFERERS:
            raise ValueError("interferer is one of %s, not %r" % (', '.join(INTERFERERS), interferer))
        specs = INTERFERERS[interferer]
    specs = [dict(s) for s in specs]
    if bursts < 1:
        raise ValueError("a capture needs at least one burst")
    if clk0 % 4:
        raise ValueError("clk0 must be a master slot's clock: CLK1-0 = 00")
    if block_samples < 2 * WIFI_TAPS:
        raise ValueError("a block of %d samples is shorter than the filter's history" % block_samples)
    slots = br.PACKET_TYPES[PTYPE][1]
    period = slots + 1                              # the slave's slot after it: 6
    slot_samples = fs * br.SLOT_US * 1e-6
    if abs(slot_samples - round(slot_samples)) > 1e-6:
        raise ValueError("%g S/s does not divide a 625 us slot" % fs)
    slot_samples = int(round(slot_samples))
    sps = int(round(fs / br.SYMBOL_RATE))
    phases = [0, sps // 2]
    channels = hop.map_channels(channels)
    for channel in channels:
        if hop.outside_window(channel, fs, center_mhz):
            raise ValueError("channel %d, %g MHz, is outside the window of %g MS/s on %g MHz"
                             % (channel, hop.channel_mhz(channel), fs / 1e6, center_mhz))
    instant = (clk0 & 0x0FFFFFFF) >> 1
    hop_fn = hop.afh_hop_fn([(instant, channels)], lap, uap)
    plan = hop.hop_plan(hop_fn, bursts, clk0, period, fs, center_mhz)

    rng = np.random.default_rng([seed, 2])
    amp = amplitude_for(snr_db)
    grid = int(bt_synth.LEAD_SLOTS * slot_samples)
    grid -= grid % sps
    total = grid + bursts * period * slot_samples + slot_samples
    iq = np.zeros(total, dtype=np.complex64)
    entries = []
    for k, (clk, channel) in enumerate(plan):
        body = br.seq_body(k, br.PACKET_TYPES[PTYPE][4])
        p = br.Packet(lap, uap, clk, PTYPE, body, lt_addr=1, flow=1, arqn=1, seqn=k & 1)
        phase = phases[k % 2]
        start = grid + k * period * slot_samples + phase
        burst_phase = rng.uniform(0, 2 * np.pi)
        timing_frac = float(rng.uniform(0, 1))
        burst, lead = br.gfsk(p.bits, fs, h=br.GFSK_H, delay=timing_frac)
        burst = burst * np.exp(1j * burst_phase)
        lo = start - lead
        n = np.arange(lo, lo + len(burst))
        cycles = (hop.channel_mhz(channel) - center_mhz) * 1e6 / fs * n
        burst = burst * np.exp(2j * np.pi * (cycles - np.floor(cycles)))
        iq[lo:lo + len(burst)] += (amp * burst).astype(np.complex64)
        entry = {'start_sample': int(start), 'timing_frac': timing_frac}
        entry.update(p.sidecar())
        del entry['air_bits']
        entry.update(end_sample=int(start + len(p.bits) * sps), channel=int(channel),
                     channel_mhz=hop.channel_mhz(channel), symbol_phase=int(start % sps),
                     snr_db=float(snr_db), afh_map_index=0)
        entries.append(entry)
    for index, lo in enumerate(range(0, total, block_samples)):
        hi = min(lo + block_samples, total)
        iq[lo:hi] += noise_block(seed, index, hi - lo, fs)
    wifi = [s for s in specs if s['kind'] == 'wifi']
    if len({s['bursty'] for s in wifi}) > 1 or len(wifi) > 1:
        raise ValueError("one Wi-Fi-like noise per file")
    frames = frame_schedule(seed, total, fs) if wifi and wifi[0]['bursty'] else []
    add_interferers(iq, specs, frames, seed, fs, center_mhz, block_samples)

    wifi_mhz = wifi_overlap_table(channels, fs, center_mhz)
    for entry in entries:
        hit, power = burst_truth(specs, frames, entry['start_sample'], entry['end_sample'],
                                 entry['channel'], wifi_mhz)
        entry['interferer_overlap'] = bool(hit)
        entry['interferer_power_in_band_db'] = power

    described = []
    for s in specs:
        if s['kind'] == 'cw':
            described.append({
                'kind': 'cw', 'channel': s['channel'], 'channel_mhz': hop.channel_mhz(s['channel']),
                'offset_mhz': tone_offset_hz(s['channel'], center_mhz) / 1e6,
                'level_db': s['level_db'], 'level_meaning':
                    'tone power over noise_1mhz, in dB: the tone is one frequency, its power is '
                    'noise_1mhz * 10**(level_db/10)',
                'duty': 1.0, 'frames': None})
        else:
            on = sum(e - s0 for s0, e in frames)
            described.append({
                'kind': 'wifi_noise', 'band_offset_mhz': list(WIFI_BAND_MHZ),
                'band_mhz': [center_mhz + WIFI_BAND_MHZ[0], center_mhz + WIFI_BAND_MHZ[1]],
                'level_db': s['level_db'], 'level_meaning':
                    'power spectral density over the floor\'s: every 1 MHz of the band is level_db '
                    'over noise_1mhz while on',
                'bursty': s['bursty'], 'duty_target': DUTY if s['bursty'] else 1.0,
                'duty': on / total if s['bursty'] else 1.0,
                'frames': 'interferer_frames' if s['bursty'] else None,
                'frame_ms': list(FRAME_MS) if s['bursty'] else None,
                'edge_us': EDGE_US if s['bursty'] else None,
                'filter': {'taps': WIFI_TAPS, 'kaiser_beta': WIFI_KAISER_BETA,
                           'edge_margin_mhz': WIFI_EDGE_MARGIN_MHZ},
                'recipe': 'white complex Gaussian, per-component sigma sqrt(noise_1mhz * 10**(level_db/10)'
                          ' * (fs/1e6) / 2), block i from default_rng([seed, 3, i]) (I then Q), history '
                          'of taps-1 samples before sample 0 from default_rng([seed, 6]), through the '
                          'complex band-pass FIR of scripts/bt_synth_interf.py wifi_taps() (overlap-'
                          'save), times the gate, added to the float32 samples after the floor'})
    sidecar = {
        'generator': 'SDR scripts/bt_synth_interf.py',
        'generator_commit': bt_synth.commit(),
        'lap': lap,
        'uap': uap,
        'clk': plan[0][0],
        'clk_convention': 'native CLK[27:0], at the first sample of the burst '
                          'it is given for; top-level clk is burst 0\'s',
        'hopping': True,
        'hop_channels': sorted({channel for _, channel in plan}),
        'channel_mhz': None,
        'bt_channel': None,
        'sample_rate': fs,
        'center_mhz': center_mhz,
        'cfo_hz': 0.0,
        'timing_frac': None,
        'timing_frac_meaning': 'null at the top: every burst has its own, uniform in [0, 1)',
        'snr_db': float(snr_db),
        'snr_bw_hz': 1e6,
        'noise_1mhz': NOISE_1MHZ,
        'modulation': {'scheme': 'GFSK', 'bt': br.GFSK_BT, 'h': br.GFSK_H,
                       'symbol_rate': br.SYMBOL_RATE},
        'slot_samples': slot_samples,
        'start_offset': 0,
        'symbol_phase': None,
        'symbol_phases': phases,
        'symbol_phase_meaning': 'null at the top: alternating per burst, even bursts at phase 0, '
                                'odd at sps/2; each burst has its own',
        'per_burst_keys': sorted(entries[0]),
        'start_sample_meaning': 'the first sample of the access code\'s '
                                'preamble, before timing_frac is added',
        'air_bits_omitted': True,
        'interferer_name': interferer,
        'interferers': described,
        'interferer_note': INTERFERER_NOTE,
        'n_bursts_on_centre_channel': sum(e['channel'] == 39 for e in entries),
        'n_bursts_overlapped': sum(e['interferer_overlap'] for e in entries),
        'afh_map': channels,
        'afh_instant': instant,
        'afh_map_count': 1,
        'afh_maps': [{'instant': instant, 'first_burst': 0, 'channels': channels}],
        'afh_instant_meaning': hop.INSTANT_MEANING,
        'hop_kernel': hop.HOP_KERNEL,
        'address_for_hop': uap << 24 | lap,
        'clock_lock_note': hop.CLOCK_LOCK_NOTE,
        'seed': seed,
        'noise_block_samples': block_samples,
    }
    if any(s['kind'] == 'wifi' and s['bursty'] for s in specs):
        sidecar['interferer_frames'] = [{'start_sample': int(s0), 'end_sample': int(e)}
                                        for s0, e in frames]
    sidecar['bursts'] = entries                      # the long list last
    return iq, sidecar


# --- the files -----------------------------------------------------------------

#: What ``--set interf`` writes, and nothing else. One seed for all: the files
#: are a paired set, every one the clean file plus its interferer.
INTERF_SET = [('hop20_dh5_int_%s' % key, dict(interferer=key, bursts=BURSTS, seed=SEED))
              for key in INTERFERERS]
SETS = {'interf': INTERF_SET}


def write_file(name, out_dir, **spec):
    """Make one file and write ``synth_<name>.cf32`` and ``.json`` into ``out_dir``."""
    os.makedirs(out_dir, exist_ok=True)
    iq_path = os.path.join(out_dir, 'synth_%s.cf32' % name)
    side_path = os.path.join(out_dir, 'synth_%s.json' % name)
    iq, sidecar = synthesise_interf(**spec)
    iq.astype('<c8', copy=False).tofile(iq_path)
    print('%s  %d samples, %.2f s, %d DH5 bursts, %d on channel 39, %d overlapped' % (
        iq_path, len(iq), len(iq) / sidecar['sample_rate'], len(sidecar['bursts']),
        sidecar['n_bursts_on_centre_channel'], sidecar['n_bursts_overlapped']), flush=True)
    with open(side_path, 'w') as f:
        json.dump(sidecar, f, indent=1)
    print(side_path)
    return iq_path, side_path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('name', nargs='?', help='the file is synth_<name>.cf32; not used with --set')
    ap.add_argument('--set', help='write the whole set, with the seeds and settings of its '
                    'table, and no other option but --out; one of %s' % ', '.join(sorted(SETS)))
    ap.add_argument('--interferer', choices=sorted(INTERFERERS), help='default clean')
    ap.add_argument('--bursts', type=int, help='default %d' % BURSTS)
    ap.add_argument('--seed', type=int, help='default %d' % SEED)
    ap.add_argument('--block-samples', type=int, help='samples per block; default 2**22')
    ap.add_argument('--out', default='/media/user/4TB/sdr-synth-tmp', help='the folder of both files')
    args = ap.parse_args(argv)

    per_file = ['interferer', 'bursts', 'seed', 'block_samples']
    if args.set:
        if args.set not in SETS:
            ap.error('no set %r: the sets are %s' % (args.set, ', '.join(sorted(SETS))))
        stray = [o for o in per_file if getattr(args, o) is not None]
        if args.name is not None or stray:
            ap.error('--set takes everything from its table: no name and no option but --out')
        runs = SETS[args.set]
    else:
        if args.name is None:
            ap.error('give the name of the file, or --set')
        spec = dict(interferer=args.interferer or 'clean', bursts=args.bursts or BURSTS,
                    seed=SEED if args.seed is None else args.seed,
                    block_samples=args.block_samples or BLOCK_SAMPLES)
        runs = [(args.name, spec)]
    try:
        for name, spec in runs:
            write_file(name, args.out, **spec)
    except ValueError as e:
        ap.error(str(e))


if __name__ == '__main__':
    main()
