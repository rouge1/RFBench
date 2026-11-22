"""DVB-T2 transmit (Round 32): a clip of DVB-T2 IQ for a signal generator to loop, from a
media file, a transport stream's programme, or built-in colour bars (with or without a
1 kHz tone). It was first used to send television from the VSG60 to the BB60D over the air
(``knowledge/dvbt2.md``, Over the air); ``tools/t2-clip`` makes one, ``tools/vsg-t2`` plays it.

``build_clip`` takes the transmitter's settings (bandwidth, FFT size, guard, constellation,
code rate, pilot pattern, carriers, rotation), works out the most FEC blocks a T2-frame of
at most 250 ms holds and the payload rate that gives (High Efficiency Mode, one PLP), has
ffmpeg make a transport stream at exactly that rate (H.264 with its parameter sets at every
keyframe, so a receiver joining the loop can start; AAC), lasting exactly the clip's frames,
and runs gr-dtv's DVB-T2 transmitter on it (``gr-dtv``'s own blocks, as its V&V flowgraphs
chain them). Whole T2-frames are cut from one P1 to the next and resampled (by FFT, which
treats the clip as one period, so it loops without a seam) to the output rate. The clip's
own L1 is read back with our receiver (``iqt2``) and returned with the rates.

Output rate: any. A generator must make it: 10 MS/s suits the VSG60 and the HackRF, and an
Ettus N210 (100 MHz divided by an even number: 10 MS/s is 100/10); the clip at 10 MS/s holds
an 8 MHz channel. Our receiver decodes 16k and 32k (one P2 symbol), single PLP.

Needs GNU Radio with gr-dtv, and ffmpeg. ``iqt2`` and ``iqt2_tables`` give the capacity.
"""

import json
import os
import shutil
import subprocess
import tempfile

import numpy as np

from . import iqt2, iqt2_tables as T

ELEMENTARY = {8e6: 64e6 / 7, 7e6: 8e6, 6e6: 48e6 / 7, 5e6: 40e6 / 7, 10e6: 80e6 / 7, 1.7e6: 131e6 / 71}
FFTS = {1024: '1K', 2048: '2K', 4096: '4K', 8192: '8K', 16384: '16K', 32768: '32K'}
GUARDS = {'1/128': 1 / 128, '1/32': 1 / 32, '1/16': 1 / 16, '19/256': 19 / 256, '1/8': 1 / 8,
          '19/128': 19 / 128, '1/4': 1 / 4}
MODS = {'QPSK': 2, '16QAM': 4, '64QAM': 6, '256QAM': 8}
RATES = {'1/2': 0, '3/5': 1, '2/3': 2, '3/4': 3, '4/5': 4, '5/6': 5}
KBCH = {0: 32208, 1: 38688, 2: 43040, 3: 48408, 4: 51648, 5: 53840}     # EN 302 755 Table 6(a)
MAX_FRAME_S = 0.250
TI_BLOCKS = 3


def frame_plan(fft=16384, guard='19/128', mod='QPSK', rate='3/5', pp=2, extended=True,
               bandwidth=8e6, nsyms=None, blocks=None):
    """The T2-frame these settings make: data symbols (as many as fit 250 ms, unless given),
    FEC blocks (as many as the data symbols hold, unless given), its length and payload rate.
    The P2 symbol's spare cells are not counted (a block or two fewer than the most)."""
    fs = ELEMENTARY[bandwidth]
    g = GUARDS[guard]
    sym = int(round(fft * (1 + g)))
    np2 = iqt2.NP2[fft]
    if nsyms is None:
        nsyms = int((MAX_FRAME_S * fs - 2048) // sym) - np2
    closing = pp in T.FRAME_CLOSING.get((fft, guard), ())
    key = (fft, bool(extended) if fft >= 8192 else False)
    cdata = T.CDATA[key][pp]
    cells = (nsyms - 1) * cdata + T.CFC[key][pp] if closing else nsyms * cdata
    per_block = 64800 // MODS[mod]
    most = cells // per_block
    blocks = most if blocks is None else int(blocks)
    if blocks > most:
        raise ValueError(f'{blocks} FEC blocks do not fit: at most {most}')
    blocks -= blocks % TI_BLOCKS if blocks >= TI_BLOCKS else 0     # whole TI-blocks
    samples = 2048 + (np2 + nsyms) * sym
    frame_s = samples / fs
    kbch = KBCH[RATES[rate]]
    payload = blocks * (kbch - 80) / frame_s
    return {'fft': fft, 'guard': guard, 'mod': mod, 'rate': rate, 'pp': pp, 'extended': bool(extended),
            'bandwidth': bandwidth, 'elementary_rate': fs, 'data_symbols': nsyms, 'frame_closing': closing,
            'blocks': blocks, 'blocks_most': most, 'frame_samples': samples, 'frame_s': frame_s,
            'payload_bps': payload, 'ts_bps': payload * 188 / 187,
            'ts_packets_per_frame': blocks * (kbch - 80) // (187 * 8)}


def make_ts(out, plan, frames, source='bars', program=None, ffmpeg=None, name='T2 TEST',
            provider='FM RECEIVER', video_bps=None):
    """A transport stream at the plan's TS rate lasting ``frames`` T2-frames: colour bars
    (``source`` 'bars'), bars with a 1 kHz tone ('tone'), a media file (re-encoded to H.264 and
    AAC), or a TS's ``program`` (copied as it is). Padded or cut to exactly the packets those
    frames carry, so the programme loops with the clip."""
    ffmpeg = ffmpeg or shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('ffmpeg not found')
    seconds = frames * plan['frame_s']
    mux = int(plan['ts_bps'])
    vbps = int(video_bps or max(300e3, min(8e6, 0.75 * mux - 200e3)))
    meta = ['-mpegts_service_id', '1', '-metadata', f'service_name={name}',
            '-metadata', f'service_provider={provider}']
    h264 = ['-c:v', 'libx264', '-preset', 'veryfast', '-b:v', str(vbps), '-maxrate', str(vbps),
            '-bufsize', str(vbps), '-g', '12', '-pix_fmt', 'yuv420p', '-x264-params', 'repeat-headers=1']
    base = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y']
    if source in ('bars', 'tone'):
        cmd = base + ['-f', 'lavfi', '-i', f'smptebars=size=720x576:rate=25:duration={seconds + 1}']
        if source == 'tone':
            cmd += ['-f', 'lavfi', '-i', f'sine=frequency=1000:sample_rate=48000:duration={seconds + 1}',
                    '-c:a', 'aac', '-b:a', '128k']
        cmd += h264
    elif program is not None:
        cmd = base + ['-i', source, '-map', f'0:p:{program}:v:0', '-map', f'0:p:{program}:a:0?', '-c', 'copy']
    else:
        cmd = base + ['-i', source, '-map', '0:v:0', '-map', '0:a:0?', '-vf', 'scale=-2:min(576\\,ih)'] + h264 + [
            '-c:a', 'aac', '-b:a', '128k']
    cmd += ['-t', f'{seconds:.3f}', '-f', 'mpegts', '-muxrate', str(mux)] + meta + [out]
    subprocess.run(cmd, check=True)
    want = frames * plan['ts_packets_per_frame'] * 188
    with open(out, 'r+b') as f:
        data = f.read()
        if len(data) >= want:
            data = data[:want]
        else:                                   # null packets to the full length
            null = b'\x47\x1f\xff\x10' + b'\xff' * 184
            data += null * ((want - len(data)) // 188)
        f.seek(0)
        f.write(data)
        f.truncate()
    return out


def run_gr_dtv(ts, out, plan, frames, rotation=False, l1mod='64QAM', gain=0.2):
    """gr-dtv's DVB-T2 transmitter on ``ts`` (repeated): ``frames`` + 1 T2-frames of cf32 at
    the elementary rate, the channel at 0 Hz."""
    from gnuradio import blocks, digital, dtv, gr
    t2, normal = dtv.STANDARD_DVBT2, dtv.FECFRAME_NORMAL
    rate = getattr(dtv, 'C' + plan['rate'].replace('/', '_'))
    mod = getattr(dtv, 'MOD_' + plan['mod'])
    fft = plan['fft']
    fftc = getattr(dtv, 'FFTSIZE_' + FFTS[fft] + ('_T2GI' if fft == 32768 and plan['guard'] in ('1/128', '19/256', '19/128') else ''))
    gi = getattr(dtv, 'GI_' + plan['guard'].replace('/', '_'))
    pp = getattr(dtv, f"PILOT_PP{plan['pp']}")
    ext = dtv.CARRIERS_EXTENDED if plan['extended'] else dtv.CARRIERS_NORMAL
    rot = dtv.ROTATION_ON if rotation else dtv.ROTATION_OFF
    bw = {8e6: 'BANDWIDTH_8_0_MHZ', 7e6: 'BANDWIDTH_7_0_MHZ', 6e6: 'BANDWIDTH_6_0_MHZ',
          5e6: 'BANDWIDTH_5_0_MHZ', 10e6: 'BANDWIDTH_10_0_MHZ', 1.7e6: 'BANDWIDTH_1_7_MHZ'}[plan['bandwidth']]
    nsyms, cp = plan['data_symbols'], int(round(fft * GUARDS[plan['guard']]))
    ver, siso = dtv.VERSION_111, dtv.PREAMBLE_T2_SISO
    tb = gr.top_block()
    chain = [
        blocks.file_source(gr.sizeof_char, ts, True),
        dtv.dvb_bbheader_bb(t2, normal, rate, dtv.RO_0_35, dtv.INPUTMODE_HIEFF, dtv.INBAND_OFF, 168, 4000000),
        dtv.dvb_bbscrambler_bb(t2, normal, rate),
        dtv.dvb_bch_bb(t2, normal, rate),
        dtv.dvb_ldpc_bb(t2, normal, rate, dtv.MOD_OTHER),
        dtv.dvbt2_interleaver_bb(normal, rate, mod),
        dtv.dvbt2_modulator_bc(normal, mod, rot),
        dtv.dvbt2_cellinterleaver_cc(normal, mod, plan['blocks'], TI_BLOCKS),
        dtv.dvbt2_framemapper_cc(normal, rate, mod, rot, plan['blocks'], TI_BLOCKS, ext, fftc, gi,
                                 getattr(dtv, f'L1_MOD_{l1mod}'), pp, 2, nsyms, dtv.PAPR_OFF, ver, siso,
                                 dtv.INPUTMODE_NORMAL, dtv.RESERVED_OFF, dtv.L1_SCRAMBLED_OFF, dtv.INBAND_OFF),
        dtv.dvbt2_freqinterleaver_cc(ext, fftc, pp, gi, nsyms, dtv.PAPR_OFF, ver, siso),
        dtv.dvbt2_pilotgenerator_cc(ext, fftc, pp, gi, nsyms, dtv.PAPR_OFF, ver, siso, dtv.MISO_TX1,
                                    dtv.EQUALIZATION_OFF, getattr(dtv, bw), fft),
        digital.ofdm_cyclic_prefixer(fft, fft + cp, 0, ''),
        dtv.dvbt2_p1insertion_cc(ext, fftc, gi, nsyms, siso, dtv.SHOWLEVELS_OFF, 3.3),
        blocks.multiply_const_cc(gain),
        blocks.head(gr.sizeof_gr_complex, (frames + 1) * plan['frame_samples']),
        blocks.file_sink(gr.sizeof_gr_complex, out, False),
    ]
    tb.connect(*chain)
    tb.run()
    return out


def resample(x, n):
    """``x`` (one period of a looping signal) to ``n`` samples by FFT: the spectrum kept, so a
    channel narrower than both rates is unchanged, and the result loops without a seam."""
    spec = np.fft.fftshift(np.fft.fft(x.astype(np.complex128)))
    out = np.zeros(n, complex)
    m = min(n, len(x))
    a, b = len(x) // 2 - m // 2, n // 2 - m // 2
    out[b:b + m] = spec[a:a + m]
    return np.fft.ifft(np.fft.ifftshift(out)) * (n / len(x))


def build_clip(out, source='bars', program=None, bandwidth=8e6, fft=16384, guard='19/128', mod='QPSK',
               rate='3/5', pp=2, extended=True, rotation=False, out_rate=10e6, frames=16, blocks=None,
               rms=0.2, keep_ts=None):
    """A looping cf32 clip of DVB-T2 at ``out_rate`` in ``out``; returns what it is (the plan,
    the rates, the clip's own L1 read back, its length and peak). ``source``: 'bars', 'tone'
    (bars and a 1 kHz tone), a media file or a TS (with ``program``)."""
    plan = frame_plan(fft, guard, mod, rate, pp, extended, bandwidth, blocks=blocks)
    fs = plan['elementary_rate']
    with tempfile.TemporaryDirectory() as tmp:
        ts = keep_ts or os.path.join(tmp, 'in.ts')
        make_ts(ts, plan, frames, source, program)
        el = os.path.join(tmp, 'el.cf32')
        run_gr_dtv(ts, el, plan, frames, rotation=rotation)
        x = np.fromfile(el, np.complex64)
    p1 = iqt2.find_p1(x[:2 * plan['frame_samples']], fs, t_s=1 / fs)
    if not p1:
        raise RuntimeError('no P1 in what gr-dtv made')
    start = p1[0]
    whole = x[start:start + frames * plan['frame_samples']]
    if len(whole) < frames * plan['frame_samples']:
        raise RuntimeError('gr-dtv made fewer frames than asked')
    card = {}
    try:
        l1 = iqt2.read_l1(x, fs, fft, guard, extended, start, t_s=1 / fs)
        card = iqt2.summary(l1, fft, guard, t_s=1 / fs)
    except Exception as exc:                    # say so; the clip may still be what was asked
        card = {'error': f'{type(exc).__name__}: {exc}'}
    n = int(round(len(whole) * out_rate / fs))
    clip = resample(whole, n) if out_rate != fs else whole.astype(complex)
    clip *= rms / np.sqrt(np.mean(np.abs(clip) ** 2))
    clip.astype(np.complex64).tofile(out)
    return {'clip': out, 'out_rate': out_rate, 'samples': n, 'seconds': n / out_rate, 'frames': frames,
            'peak': float(np.abs(clip).max()), 'rms': rms, 'plan': plan, 'l1': card,
            'payload_bps': plan['payload_bps'], 'ts_bps': plan['ts_bps']}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='A looping DVB-T2 IQ clip for a signal generator.')
    ap.add_argument('out', help='the clip (cf32)')
    ap.add_argument('--source', default='bars', help="'bars', 'tone' (bars and 1 kHz), a media file or a TS")
    ap.add_argument('--program', type=int, help="with a TS: the programme's number")
    ap.add_argument('--bandwidth', type=float, default=8.0, help='MHz (8, 7, 6, 5, 10, 1.7)')
    ap.add_argument('--fft', default='16k', choices=['1k', '2k', '4k', '8k', '16k', '32k'])
    ap.add_argument('--guard', default='19/128', choices=sorted(GUARDS))
    ap.add_argument('--mod', default='QPSK', choices=sorted(MODS))
    ap.add_argument('--rate', default='3/5', choices=sorted(RATES))
    ap.add_argument('--pp', type=int, default=2, choices=range(1, 9), help='pilot pattern PP1-PP8')
    ap.add_argument('--normal-carriers', action='store_true', help='normal, not extended, carriers')
    ap.add_argument('--rotation', action='store_true', help='rotated constellation')
    ap.add_argument('--out-rate', type=float, default=10e6, help='samples a second (default 10e6)')
    ap.add_argument('--frames', type=int, default=16, help='T2-frames in the clip (default 16)')
    ap.add_argument('--blocks', type=int, help='FEC blocks a frame (default: as many as fit)')
    ap.add_argument('--keep-ts', help='keep the transport stream here')
    a = ap.parse_args(argv)
    fft = {'1k': 1024, '2k': 2048, '4k': 4096, '8k': 8192, '16k': 16384, '32k': 32768}[a.fft]
    r = build_clip(a.out, a.source, a.program, a.bandwidth * 1e6, fft, a.guard, a.mod, a.rate, a.pp,
                   not a.normal_carriers, a.rotation, a.out_rate, a.frames, a.blocks, keep_ts=a.keep_ts)
    print(json.dumps(r, indent=1, default=float))
    return 0
