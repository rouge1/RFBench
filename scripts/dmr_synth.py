#!/usr/bin/env python3
"""A DMR voice call as a cf32 file, with a JSON sidecar of what is in it.

One press of the PTT on a handheld (MS-sourced, ETSI TS 102 361-1), on one slot:
  Voice LC Header bursts (data SYNC, slot type 'Voice LC Header', BPTC(196,96), the LC
  and its RS(12,9) parity masked with 969696), then voice superframes of six bursts -
  A with the voice SYNC, B-E with the EMB and the LC as embedded signalling (LCSS first,
  continuation, continuation, last), F with the EMB and the Null embedded message
  (clause 7.1.3.2: inbound burst F is always null) - then a Terminator with LC
  (mask 999999). The LC is Grp_V_Ch_Usr or UU_V_Ch_Usr (TS 102 361-2 Tables 7.1, 7.2).

Each burst is 27.5 ms (132 symbols) in its 30 ms slot and the transmitter is off for
the other slot: one burst every 60 ms, ramped on and off in the 1.25 ms guards. With
--sync direct1 or direct2 it uses the TDMA direct-mode SYNCs of that slot instead.

Voice: AMBE+2 frames from a Sceptre .ambe file (9 bytes a frame, as on the air, 18 to a
superframe), repeated or cut to --superframes; without one, every frame is the AMBE+2
silence frame. The codec is proprietary, so speech cannot be encoded here.

Every field's FEC is in scripts/dmr_fec.py, checked bit for bit against a real call
(knowledge/ptt-dmr.md). Checked by scripts/test_dmr_synth.py, which runs the file
back through scripts/dmr_demod.py.

    python scripts/dmr_synth.py out/call.cf32 --src 1001 --dst 9 --cc 1
    python scripts/dmr_synth.py out/call.cf32 --ambe some.ambe --fs 1e6 --offset 25e3 \\
        --snr 20 --cfo 150 --lead 0.5
"""
import argparse
import hashlib
import json
import os
import sys
from fractions import Fraction

import numpy as np
from scipy.signal import resample_poly

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dmr_demod as D  # noqa: E402
import dmr_fec as F  # noqa: E402

SILENCE = bytes.fromhex('B9E881526173002A6B')      # the AMBE+2 silence frame
SLOT_SYMS = 144                                     # 30 ms
BURST_SYMS = 132                                    # 27.5 ms
GUARD_SYMS = (SLOT_SYMS - BURST_SYMS) // 2          # 1.25 ms each side
RAMP_S = 0.5e-3                                     # PA ramp, inside the guard
HALF = D.SPS // 2                                   # half a symbol at 48 kS/s

SYNC_SETS = {'ms': ('MS_VOICE', 'MS_DATA'),
             'direct1': ('TDMA1_VOICE', 'TDMA1_DATA'),
             'direct2': ('TDMA2_VOICE', 'TDMA2_DATA')}
SOURCE = {'ms': 'MS-sourced', 'direct1': 'TDMA direct mode slot 1',
          'direct2': 'TDMA direct mode slot 2'}
DT = {name: i for i, name in enumerate(D.DATA_TYPES)}


def load_frames(path, superframes):
    """-> list of 72-bit frames, a whole number of superframes."""
    if path:
        data = open(path, 'rb').read()
        if len(data) % 9:
            raise ValueError(f'{path}: {len(data)} bytes is not a whole number of 9-byte frames')
        frames = D.ambe_to_frames(data).tolist()
        if not frames:
            raise ValueError(f'{path}: no voice frames')
    else:
        frames = D.ambe_to_frames(SILENCE).tolist()
    n = 18 * superframes if superframes else 18 * max(1, len(frames) // 18)
    return [frames[i % len(frames)] for i in range(n)]


def build_call(lc, cc, frames, sync='ms', headers=1, terminators=1):
    """-> list of (264-bit burst, description dict), one burst per 60 ms."""
    v_sync, d_sync = SYNC_SETS[sync]
    out = []
    hdr = F.bptc196_encode(F.lc_info96(lc, 'Voice LC Header'))
    for _ in range(headers):
        out.append((D.data_burst(hdr, cc, DT['Voice LC Header'], d_sync),
                    {'kind': 'data', 'data_type': 'Voice LC Header'}))
    frags = F.embedded_lc(lc) + [F.NULL_EMBEDDED]
    lcss = [F.LCSS['first'], F.LCSS['continuation'], F.LCSS['continuation'],
            F.LCSS['last'], F.LCSS['single']]
    for sf in range(len(frames) // 18):
        for k in range(6):
            fr = frames[18 * sf + 3 * k:18 * sf + 3 * k + 3]
            if k == 0:
                centre = D.hex_bits(D.SYNCS[v_sync])
                desc = {'kind': 'voice', 'superframe': sf, 'burst': 'A'}
            else:
                centre = F.voice_centre(cc, lcss[k - 1], frags[k - 1])
                desc = {'kind': 'voice', 'superframe': sf, 'burst': 'ABCDEF'[k],
                        'lcss': lcss[k - 1]}
            out.append((D.voice_burst(fr, centre), desc))
    term = F.bptc196_encode(F.lc_info96(lc, 'Terminator with LC'))
    for _ in range(terminators):
        out.append((D.data_burst(term, cc, DT['Terminator with LC'], d_sync),
                    {'kind': 'data', 'data_type': 'Terminator with LC'}))
    return out


def modulate_bursts(bursts, slot=1, lead_s=0.2, tail_s=0.2):
    """-> (complex baseband at 48 kS/s, envelope, first symbol centre sample of every burst).
    Bursts go in alternate 30 ms slots; slot 1 is the first, slot 2 is 30 ms later."""
    lead = int(round(lead_s * D.SYMBOL_RATE))
    lead += (slot - 1) * SLOT_SYMS
    n_sym = lead + 2 * SLOT_SYMS * len(bursts) + int(round(tail_s * D.SYMBOL_RATE))
    syms = np.zeros(n_sym)
    on = np.zeros(n_sym)
    starts = []
    for i, (bits, _) in enumerate(bursts):
        s0 = lead + 2 * SLOT_SYMS * i + GUARD_SYMS
        syms[s0:s0 + BURST_SYMS] = D.bits_to_symbols(bits)
        on[s0:s0 + BURST_SYMS] = 1
        starts.append(s0 * D.SPS + HALF)
    # D.modulate puts each symbol's impulse at the start of its period; clause
    # 10.2.3.1.2 puts the first symbol's centre 65.5 symbol times before the slot
    # centre, half a symbol into the 27.5 ms window, so the waveform moves HALF later.
    x = D.modulate(syms)
    x = np.concatenate([np.full(HALF, x[0]), x[:-HALF]])
    env = np.repeat(on, D.SPS).astype(float)
    r = int(round(RAMP_S * D.FS_SYM))
    win = np.hanning(2 * r + 1)
    win /= win.sum()
    env = np.clip(np.convolve(env, win, mode='same') * 1.0, 0, 1)
    # the ramp is centred on the burst edge; widen the "on" so the burst itself is flat
    env = np.maximum(env, np.repeat(on, D.SPS))
    return x * env, env, starts


def render(x48, fs, offset_hz=0.0, cfo_hz=0.0, snr_db=None, amplitude=0.5, seed=1):
    """Resample to fs, shift to offset + cfo, scale, and add complex noise: snr_db is the
    signal power while a burst is on over the noise power in 12.5 kHz, the way a
    receiver's channel filter sees it."""
    r = Fraction(fs / D.FS_SYM).limit_denominator(100000)
    y = resample_poly(x48, r.numerator, r.denominator) if r != 1 else x48.copy()
    n = np.arange(len(y))
    y = y * np.exp(2j * np.pi * (offset_hz + cfo_hz) * n / fs) * amplitude
    if snr_db is not None:
        p_sig = amplitude ** 2
        p_noise_chan = p_sig / 10 ** (snr_db / 10)
        p_noise = p_noise_chan * fs / 12.5e3
        rng = np.random.default_rng(seed)
        y = y + np.sqrt(p_noise / 2) * (rng.standard_normal(len(y)) + 1j * rng.standard_normal(len(y)))
    return y.astype(np.complex64), r


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('out', help='output .cf32; the sidecar goes beside it as .json')
    ap.add_argument('--src', type=int, default=1001, help='source ID, 24 bits')
    ap.add_argument('--dst', type=int, default=9, help='talkgroup or unit ID, 24 bits')
    ap.add_argument('--call', choices=list(F.FLCO), default='group')
    ap.add_argument('--cc', type=int, default=1, help='colour code 0-15')
    ap.add_argument('--service-options', type=int, default=0)
    ap.add_argument('--sync', choices=list(SYNC_SETS), default='ms')
    ap.add_argument('--slot', type=int, choices=[1, 2], default=1)
    ap.add_argument('--ambe', help="a Sceptre .ambe file of DMR voice frames")
    ap.add_argument('--superframes', type=int, default=0,
                    help='voice superframes (360 ms each); default: as many as the .ambe '
                    'holds, or 10 of silence')
    ap.add_argument('--headers', type=int, default=1)
    ap.add_argument('--terminators', type=int, default=1)
    ap.add_argument('--fs', type=float, default=48000.0, help='output sample rate')
    ap.add_argument('--offset', type=float, default=0.0, help='channel offset from 0 Hz')
    ap.add_argument('--cfo', type=float, default=0.0,
                    help='carrier error, Hz, added to the offset but not written as the '
                    "channel's place in the sidecar's 'offset_hz'")
    ap.add_argument('--snr', type=float, help='dB in 12.5 kHz while on; omit for no noise')
    ap.add_argument('--lead', type=float, default=0.2, help='seconds before the call')
    ap.add_argument('--tail', type=float, default=0.2, help='seconds after the call')
    ap.add_argument('--seed', type=int, default=1)
    a = ap.parse_args(argv)

    for name, v, bits in (('--src', a.src, 24), ('--dst', a.dst, 24), ('--cc', a.cc, 4),
                          ('--service-options', a.service_options, 8)):
        if not 0 <= v < 1 << bits:
            ap.error(f'{name} {v} does not fit in {bits} bits')
    if a.superframes == 0 and not a.ambe:
        a.superframes = 10

    lc = F.full_lc(a.dst, a.src, a.call, service_options=a.service_options)
    frames = load_frames(a.ambe, a.superframes)
    bursts = build_call(lc, a.cc, frames, a.sync, a.headers, a.terminators)
    x48, env, starts = modulate_bursts(bursts, a.slot, a.lead, a.tail)
    y, ratio = render(x48, a.fs, a.offset, a.cfo, a.snr, seed=a.seed)

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    y.tofile(a.out)
    k = float(ratio)
    v_sync, d_sync = SYNC_SETS[a.sync]
    side = {
        'format': 'cf32 (complex64, interleaved I/Q float32, little-endian)',
        'sample_rate': a.fs, 'samples': len(y), 'duration_s': len(y) / a.fs,
        'offset_hz': a.offset, 'cfo_hz': a.cfo, 'snr_db_12k5': a.snr, 'seed': a.seed,
        'standard': f'ETSI TS 102 361-1/-2 DMR, {SOURCE[a.sync]} voice call, one slot',
        'symbol_rate': D.SYMBOL_RATE, 'deviation_hz_per_unit': D.DEV,
        'filter': 'root raised cosine, alpha 0.2', 'dibits': {'01': 3, '00': 1, '10': -1, '11': -3},
        'sync': {'voice': v_sync, 'voice_hex': D.SYNCS[v_sync],
                 'data': d_sync, 'data_hex': D.SYNCS[d_sync]},
        'slot': a.slot, 'colour_code': a.cc,
        'lc': dict(F.parse_lc(lc), hex=bytes(F.lc_octets(lc)).hex().upper()),
        'headers': a.headers, 'terminators': a.terminators,
        'superframes': len(frames) // 18, 'voice_frames': len(frames),
        'ambe_source': os.path.basename(a.ambe) if a.ambe else 'AMBE+2 silence frame',
        'ambe_sha256': hashlib.sha256(D.frames_to_ambe(frames)).hexdigest(),
        'burst_period_s': 0.06, 'burst_length_s': 0.0275,
        'bursts': [dict(desc, index=i, start_sample=round(s * k),
                        sync_sample=round((s + 54 * D.SPS) * k),
                        start_s=round(s * k) / a.fs)
                   for i, ((_, desc), s) in enumerate(zip(bursts, starts))],
        'made_by': 'scripts/dmr_synth.py',
    }
    with open(os.path.splitext(a.out)[0] + '.json', 'w') as f:
        json.dump(side, f, indent=1)
    with open(os.path.splitext(a.out)[0] + '.ambe', 'wb') as f:
        f.write(D.frames_to_ambe(frames))
    print(f'{a.out}: {len(bursts)} bursts, {len(frames) // 18} superframes, '
          f'{len(y) / a.fs:.2f} s at {a.fs:g} S/s')
    return 0


if __name__ == '__main__':
    sys.exit(main())
