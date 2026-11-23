#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The live DVB-T2 transmitter's parts, for ``dvbt2Xmitter``: several videos muxed by
ffmpeg into one transport stream, as programmes of one multiplex; gr-dtv's DVB-T2
modulator; and the resampler to the radio's rate.

Why live, not a clip: a two-minute programme at 10 MS/s is 9.6 GB of IQ, far past what a
VSG60 is known to loop from its memory (skinny-fm's 4 s, 320 MB, is the largest tried), and
a clip cannot be sent by the HackRF or the Ettus at all. gr-dtv's T2 chain runs at about
eight times the air on the 20-core bench (16k, QPSK to 64QAM, 2026-11-22), so the modulator
keeps up with room to spare. The radio paces everything: the flowgraph takes the transport
stream at exactly the rate the frames carry, and ffmpeg blocks on the full pipe, as the
ATSC transmitter's does (``atsc_source``).

**The rate has to be exact.** The frames carry ``plan['ts_bps']`` bit/s of transport stream
(``dvbt2.t2_tx.frame_plan``: the payload in High Efficiency Mode, times 188/187), and ffmpeg
is told to mux at that rate (``-muxrate``), padding with null packets. A stream muxed faster
would play slow on the air and ffmpeg's buffers would fill; slower, and the frames would
take packets ffmpeg has not made yet.

**One programme a video**, each its own service with its name in the SDT (from the file's
name), H.264 with its parameter sets at every keyframe (``repeat-headers``: a receiver
joining at any moment needs them; skinny-fm found that a looping clip without them never
showed a picture) and AAC. Each input loops inside ffmpeg (``-stream_loop -1``), so clips of
different lengths each start again at their own end.

The single PLP and the 16k or 32k FFT are what our receiver (``apps/dvbt2``) decodes.
"""

import os
import re
import subprocess
import tempfile

from apps.dvbt2 import t2_tx
from apps.ntsc_source import have_ffmpeg

#: In place of a file: built-in colour bars and a 1 kHz tone, as the ATSC transmitter's.
COLOUR_BARS = '<colour bars>'

#: Each programme's sound.
AUDIO_BPS = 128_000

#: The share of the multiplex the programmes' video and sound may fill; the rest is the
#: tables, the PES and TS headers, and slack for the encoder's peaks.
FILL = 0.85

#: The least a programme's picture is sent at: SD H.264 that holds up on a busy scene.
#: That gives QPSK 3/5 three programmes, 16QAM 3/5 six and 64QAM 3/5 nine.
MIN_VIDEO_BPS = 1_800_000

#: And the most: past this an SD picture looks no better, and the rest is null packets.
MAX_VIDEO_BPS = 6_000_000

#: Each picture: SD, 25 frames a second (DVB's countries), the shape kept.
VIDEO_FILTER = ('scale=iw*sar:ih,setsar=1,scale=720:576:force_original_aspect_ratio=decrease,'
                'pad=720:576:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25')

#: The baseband level the modulator's output is brought to, rms. skinny-fm's clips were made
#: at 0.2, and its over-the-air numbers (about 12 dB SNR at -10 dBm on the VSG60) are for
#: that; OFDM's peaks then stay under 1.0 (measured in ``scripts/test_dvbt2_tx.py``).
BASEBAND_RMS = 0.2

#: gr-dtv's chain out of its P1 insertion is at this rms: measured 1.004 on 16k PP2 16QAM
#: frames, 2026-11-22 (the test checks the result), so the multiplier is about 0.2. The peak
#: then measured 0.87 in 4 s: OFDM's 12-13 dB peak-to-average leaves little headroom above
#: 0.2 rms, which is why it is not raised.
GR_DTV_RMS = 1.004

#: The elementary rate is 64/7 MS/s at 8 MHz; the radios take 10 MS/s (an Ettus N210 as
#: 100/10, an even ratio, as it needs), 35/32 of it.
OUT_RATE = 10e6


def service_name(path):
    """What a receiver calls the programme: the file's name, dashes and underscores as spaces."""
    if path == COLOUR_BARS:
        return 'Colour Bars'
    stem = os.path.splitext(os.path.basename(path))[0]
    return re.sub(r'\s+', ' ', re.sub(r'[-_]+', ' ', stem)).strip()[:40] or 'Programme'


def video_bps(plan, programmes):
    """Each programme's video rate when ``programmes`` share the multiplex."""
    share = FILL * plan['ts_bps'] / max(1, programmes)
    return int(min(MAX_VIDEO_BPS, share - AUDIO_BPS))


def fits(plan, programmes):
    """Whether that many programmes each get at least MIN_VIDEO_BPS."""
    share = FILL * plan['ts_bps'] / max(1, programmes) - AUDIO_BPS
    return programmes >= 1 and share >= MIN_VIDEO_BPS


def most_programmes(plan):
    n = 1
    while fits(plan, n + 1):
        n += 1
    return n if fits(plan, n) else 0


def has_audio(path):
    """Whether the file has a sound stream (ffprobe); assumed so when it cannot be asked."""
    try:
        r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a', '-show_entries',
                            'stream=index', '-of', 'csv=p=0', path],
                           capture_output=True, text=True, timeout=20)
        return bool(r.stdout.strip()) if r.returncode == 0 else True
    except (OSError, subprocess.TimeoutExpired):
        return True


def ffmpeg_argv(paths, plan, ffmpeg='ffmpeg', provider='RF Bench'):
    """ffmpeg's command for the multiplex: one programme per path, on stdout, forever."""
    n = len(paths)
    vbps = video_bps(plan, n)
    argv = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin']
    for path in paths:
        if path == COLOUR_BARS:
            argv += ['-f', 'lavfi', '-i', 'smptebars=size=720x576:rate=25',
                     '-f', 'lavfi', '-i', 'sine=frequency=1000:sample_rate=48000,volume=0.8,pan=stereo|c0=c0|c1=c0']
        else:
            argv += ['-stream_loop', '-1', '-i', path]
    k, inputs = 0, []
    for path in paths:                         # each programme's video and sound input numbers
        if path == COLOUR_BARS:
            inputs.append((k, k + 1))
            k += 2
        else:
            inputs.append((k, k))
            k += 1
    filters = []
    for i, (v, _) in enumerate(inputs):
        filters.append(f'[{v}:v:0]{VIDEO_FILTER}[v{i}]')
    argv += ['-filter_complex', ';'.join(filters)]
    streams, st = [], 0
    for i, ((v, a), path) in enumerate(zip(inputs, paths)):
        argv += ['-map', f'[v{i}]']
        mine = [st]
        st += 1
        if path == COLOUR_BARS or has_audio(path):
            argv += ['-map', f'{a}:a:0']
            mine.append(st)
            st += 1
        streams.append(mine)
    argv += ['-c:v', 'libx264', '-preset', 'veryfast',
             '-b:v', str(vbps), '-maxrate', str(vbps), '-bufsize', str(vbps), '-g', '25',
             '-pix_fmt', 'yuv420p', '-x264-params', 'repeat-headers=1',
             '-c:a', 'aac', '-b:a', str(AUDIO_BPS), '-ac', '2', '-ar', '48000']
    for i, path in enumerate(paths):
        # Each programme names its own output streams: its picture, and its sound where the
        # file has any (a silent file maps none, so the numbers after it move up by one).
        sts = ''.join(f':st={k}' for k in streams[i])
        argv += ['-program', f'program_num={i + 1}:title={service_name(path)}{sts}']
        # The SDT's provider name comes from each programme's own metadata. The muxer
        # ignores a global -metadata service_provider once -program is used, and writes
        # "FFmpeg"; -program itself refuses the key. Found in CTF night three.
        argv += [f'-metadata:p:{i}', f'service_provider={provider}']
    argv += ['-mpegts_transport_stream_id', '1', '-mpegts_original_network_id', '1',
             '-muxrate', str(int(round(plan['ts_bps']))), '-f', 'mpegts', 'pipe:1']
    return argv


class Multiplex:
    """Several videos (or :data:`COLOUR_BARS`) as one DVB-T2 transport stream on a pipe, at
    exactly the rate ``plan``'s frames carry, forever. Hand ``descriptor()`` (a duplicate:
    the block closes what it is given, and so does ``close``) to
    ``blocks.file_descriptor_source`` - see ``atsc_source.TransportStream.descriptor``."""

    def __init__(self, paths, plan):
        if not have_ffmpeg():
            raise RuntimeError("DVB-T2 needs ffmpeg on PATH to make its transport stream, "
                               "and it is not installed.")
        if not paths:
            raise ValueError("no programme to send")
        self.paths = list(paths)
        self.plan = plan
        self.argv = ffmpeg_argv(self.paths, plan)
        # Its complaints to a file, not a pipe: a pipe nobody reads fills, and ffmpeg then
        # stops writing the stream too.
        self._log = tempfile.TemporaryFile()
        self._proc = subprocess.Popen(self.argv, stdout=subprocess.PIPE, stderr=self._log)

    def fileno(self):
        return self._proc.stdout.fileno()

    def descriptor(self):
        return os.dup(self.fileno())

    def error(self):
        """ffmpeg's complaint, if it has stopped."""
        proc = self._proc
        if proc is None or proc.poll() is None:
            return ''
        try:
            self._log.seek(0)
            return self._log.read().decode('utf-8', 'replace').strip()
        except Exception:
            return ''

    def close(self):
        proc, self._proc = getattr(self, '_proc', None), None
        if proc is None:
            return
        try:
            proc.stdout.close()
        except Exception:
            pass
        if proc.poll() is None:
            # Blocked on a full pipe, ffmpeg can ignore SIGTERM (knowledge/media.md): kill.
            proc.kill()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        try:
            self._log.close()
        except Exception:
            pass


def modulator(plan, rotation=False):
    """gr-dtv's DVB-T2 transmitter, transport stream bytes in, the elementary-rate signal out
    at BASEBAND_RMS: a list of blocks to connect in order (keep it: every block must outlive
    the run). Its chain and arguments are skinny-fm's ``t2_tx.run_gr_dtv``, which its tests
    decode bit for bit."""
    from gnuradio import blocks, digital, dtv
    t2, normal = dtv.STANDARD_DVBT2, dtv.FECFRAME_NORMAL
    rate = getattr(dtv, 'C' + plan['rate'].replace('/', '_'))
    mod = getattr(dtv, 'MOD_' + plan['mod'])
    fft = plan['fft']
    fftc = getattr(dtv, 'FFTSIZE_' + t2_tx.FFTS[fft]
                   + ('_T2GI' if fft == 32768 and plan['guard'] in ('1/128', '19/256', '19/128') else ''))
    gi = getattr(dtv, 'GI_' + plan['guard'].replace('/', '_'))
    pp = getattr(dtv, f"PILOT_PP{plan['pp']}")
    ext = dtv.CARRIERS_EXTENDED if plan['extended'] else dtv.CARRIERS_NORMAL
    rot = dtv.ROTATION_ON if rotation else dtv.ROTATION_OFF
    bw = {8e6: 'BANDWIDTH_8_0_MHZ', 7e6: 'BANDWIDTH_7_0_MHZ', 6e6: 'BANDWIDTH_6_0_MHZ',
          5e6: 'BANDWIDTH_5_0_MHZ', 10e6: 'BANDWIDTH_10_0_MHZ',
          1.7e6: 'BANDWIDTH_1_7_MHZ'}[plan['bandwidth']]
    nsyms, cp = plan['data_symbols'], int(round(fft * t2_tx.GUARDS[plan['guard']]))
    ver, siso = dtv.VERSION_111, dtv.PREAMBLE_T2_SISO
    ti = t2_tx.TI_BLOCKS
    return [
        dtv.dvb_bbheader_bb(t2, normal, rate, dtv.RO_0_35, dtv.INPUTMODE_HIEFF, dtv.INBAND_OFF, 168, 4000000),
        dtv.dvb_bbscrambler_bb(t2, normal, rate),
        dtv.dvb_bch_bb(t2, normal, rate),
        dtv.dvb_ldpc_bb(t2, normal, rate, dtv.MOD_OTHER),
        dtv.dvbt2_interleaver_bb(normal, rate, mod),
        dtv.dvbt2_modulator_bc(normal, mod, rot),
        dtv.dvbt2_cellinterleaver_cc(normal, mod, plan['blocks'], ti),
        dtv.dvbt2_framemapper_cc(normal, rate, mod, rot, plan['blocks'], ti, ext, fftc, gi,
                                 dtv.L1_MOD_64QAM, pp, 2, nsyms, dtv.PAPR_OFF, ver, siso,
                                 dtv.INPUTMODE_NORMAL, dtv.RESERVED_OFF, dtv.L1_SCRAMBLED_OFF,
                                 dtv.INBAND_OFF),
        dtv.dvbt2_freqinterleaver_cc(ext, fftc, pp, gi, nsyms, dtv.PAPR_OFF, ver, siso),
        dtv.dvbt2_pilotgenerator_cc(ext, fftc, pp, gi, nsyms, dtv.PAPR_OFF, ver, siso, dtv.MISO_TX1,
                                    dtv.EQUALIZATION_OFF, getattr(dtv, bw), fft),
        digital.ofdm_cyclic_prefixer(fft, fft + cp, 0, ''),
        dtv.dvbt2_p1insertion_cc(ext, fftc, gi, nsyms, siso, dtv.SHOWLEVELS_OFF, 3.3),
        blocks.multiply_const_cc(BASEBAND_RMS / GR_DTV_RMS),
    ]


#: Half an extended-carrier channel's width, a little over (skinny-fm's ``dvbt2.PASS_HZ``).
PASS_HZ = {8e6: 3.90e6, 7e6: 3.41e6, 6e6: 2.93e6, 5e6: 2.44e6, 10e6: 4.88e6, 1.7e6: 0.80e6}


def resampler(plan, out_rate=OUT_RATE):
    """The elementary rate to ``out_rate`` (64/7 to 10 MS/s is 35/32), passing the channel flat."""
    return rational_resampler(plan['elementary_rate'], out_rate, PASS_HZ[plan['bandwidth']])


def rational_resampler(rate, out_rate, pass_hz):
    """``rate`` to ``out_rate``, passing +-``pass_hz`` flat: GNU Radio's polyphase rational
    resampler with its own low-pass (the default, 0.4 of the lower rate, would cut into an
    extended-carrier channel's 7.77 MHz). The ratio must be a small fraction: 35/32 out to
    the radio, 32/35 back from its 10 MS/s."""
    from fractions import Fraction
    from gnuradio import filter
    from gnuradio.fft import window
    from gnuradio.filter import firdes
    ratio = Fraction(out_rate / rate).limit_denominator(1000)
    if abs(float(ratio) - out_rate / rate) > 1e-9 * out_rate / rate:
        raise ValueError(f"{out_rate / 1e6:.6f} MS/s is no small ratio of {rate / 1e6:.6f}")
    up, down = ratio.numerator, ratio.denominator
    stop = min(rate, out_rate) / 2
    taps = firdes.low_pass(up, rate * up, (pass_hz + stop) / 2, max(stop - pass_hz, 0.05 * stop),
                           window.WIN_BLACKMAN_HARRIS)
    return filter.rational_resampler_ccc(up, down, [complex(t) for t in taps])


#: UHF in ITU Region 1, 8 MHz channels: N is centred on 306 + 8N MHz.
UHF_CHANNELS = range(21, 70)

#: Channel 36, 594 MHz: where the over-the-air tests ran. Only a default.
DEFAULT_CHANNEL = 36


def channel_center_mhz(channel):
    return 306.0 + 8.0 * int(channel)


def channel_items():
    """The 8 MHz UHF plan as ``(number, centre MHz, caption)``, for FrequencyChooser: the
    transmitter's dialog and the receiver's offer the same list."""
    return [(n, channel_center_mhz(n), f"UHF {n}  ({channel_center_mhz(n):g} MHz)")
            for n in UHF_CHANNELS]


def channel_for_center(mhz, tolerance=0.5):
    """The UHF channel centred within ``tolerance`` MHz of ``mhz``, or None."""
    n = round((float(mhz) - 306.0) / 8.0)
    return n if n in UHF_CHANNELS and abs(channel_center_mhz(n) - mhz) <= tolerance else None


def describe(plan, paths):
    """One line for the window: the layout and what it carries."""
    ext = ' extended' if plan['extended'] else ''
    return (f"{plan['fft'] // 1024}k{ext}, guard {plan['guard']}, PP{plan['pp']}, "
            f"{plan['mod']} {plan['rate']}: {plan['ts_bps'] / 1e6:.2f} Mb/s, "
            f"{len(paths)} programme{'s' if len(paths) != 1 else ''}")
