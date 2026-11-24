#!/usr/bin/env python3
"""Record what Watch shows off a real radio, and compare it with the original clip.

    python scripts/tv_watch_compare.py record --system BG --freq 474 \\
        --secs 60 --out watched.nut                      # BB60D, headless
    python scripts/tv_watch_compare.py compare watched.nut CLIP.mp4

``record`` builds the PAL receiver's own chain on the BB60D - ``VsbDemod``,
``AuralSound`` with its ``AudioTap``, ``StreamingFrameSink`` - and gives
Watch's muxer an ffmpeg that writes the stream to a file instead of ffplay:
exactly what ffplay would have shown and played.

``compare`` lines that recording up against the clip it came from:

- **which frame of the original each received picture is**, found by
  matching small greyscale copies (the first broadly, then each next one
  near where it should be). Consecutive pictures should be consecutive
  frames; a repeat or a skip shows as a step of 0 or 2.
- **how close each picture is to its original** - PSNR against the original
  as the transmitter scaled it (letterboxed to 768x576), on matched frames.
- **where the sound is against the picture**: the received sound
  cross-correlated against the original's own track, as the transmitter
  conditions it (``ntsc_source.AudioTrack``), gives the clip time of the
  first received sound sample; the picture match gives the clip time of the
  first received picture. Both start together in the recording, so the
  difference is how far the sound is from the picture - transmitter and
  receiver together - against the original.
"""
import argparse
import os
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SMALL = (96, 72)


def record(a):
    from gnuradio import blocks, gr
    from apps.bb60_source import bb60_source, overrun_count
    from apps.tv_system import AuralSound, VsbDemod, pal_system
    from apps.tv_watch import AudioTap, StreamingFrameSink
    system = pal_system(a.system, a.freq)
    rate = 20e6
    tb = gr.top_block("record", catch_exceptions=True)
    src = bb60_source(center_freq=a.freq * 1e6 + system.lo_offset,
                      sample_rate=rate, gain_percent=a.gain)
    tap = AudioTap()
    demod = VsbDemod(rate, system)
    sound = AuralSound(rate, system, volume=0.0, tap=tap)
    sink = StreamingFrameSink(demod.composite_rate, standard=system.standard,
                              tap=tap)
    sink.volume = 1.0
    sink.play_cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
                     '-f', 'nut', '-i', 'pipe:0', '-c', 'copy', a.out]
    s2v = blocks.stream_to_vector(gr.sizeof_float, sink.VLEN)
    null = blocks.null_sink(gr.sizeof_float)
    tb.connect(src, demod, s2v, sink)
    tb.connect(src, sound, null)
    tb.start()
    time.sleep(3.0)                      # workers spawned, filters full
    o0 = overrun_count()
    sink.start_player(sink.player_argv('record'))
    time.sleep(a.secs)
    sink.stop_player()
    print(f"RECORD {a.secs:g} s: decoded {sink.frames_decoded}, dropped "
          f"{sink.frames_dropped}, failed {sink.failures}, repeated "
          f"{sink.frames_repeated}, BB60D overruns {overrun_count() - o0}",
          flush=True)
    tb.stop()
    tb.wait()
    time.sleep(1.0)
    return 0


def frames_of(path, size, vf_prefix=''):
    w, h = size
    vf = (vf_prefix + ',' if vf_prefix else '') + f"scale={w}:{h},format=gray"
    p = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', path,
                        '-map', '0:v', '-vf', vf, '-f', 'rawvideo', '-'],
                       capture_output=True, check=True)
    v = np.frombuffer(p.stdout, np.uint8)
    return v[:v.size // (w * h) * w * h].reshape(-1, h, w).astype(np.float32)


def compare(a):
    from apps.ntsc_encode import PAL
    from apps.ntsc_source import AUDIO_RATE, AudioTrack
    std = PAL
    W, H = std.width, std.height
    # The transmitter's own fit to 768x576 (ntsc_source.VideoFile), at 25 a second.
    fit = (f"scale=iw*sar:ih,setsar=1,scale={W}:{H}:force_original_aspect_ratio="
           f"decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,fps={std.frame_rate}")
    print("reading the original and the recording ...", flush=True)
    ref = frames_of(a.clip, SMALL, fit)
    got = frames_of(a.recording, SMALL)
    print(f"       original {len(ref)} frames, recording {len(got)} pictures")

    def err(x, y):
        x = x - x.mean()
        y = y - y.mean()
        return float(((x - y) ** 2).mean())

    # **Where each picture is in the original.** One picture in 25 is
    # matched against a stretch of the clip around where it should be, and a
    # straight line is fitted through those: received picture k is original
    # frame first + k if nothing was skipped or repeated. Matching picture by
    # picture, each from the last, was tried first and failed: the clip opens
    # on a nearly still shot, and its 30-to-25 conversion left one frame in
    # five a near-copy of the one before (1,079 of 5,319 in the original
    # itself), so a step-by-step tracker stuck on the same frame.
    # The sound.
    starts = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                             'stream=codec_type,start_time', '-of', 'csv=p=0',
                             a.recording], capture_output=True, text=True).stdout.split()
    st = {k: float(t) for k, t in (x.split(',') for x in starts)}
    p = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i',
                        a.recording, '-map', '0:a', '-f', 'f32le', '-'],
                       capture_output=True, check=True)
    heard = np.frombuffer(p.stdout, np.float32).astype(np.float64)
    track = AudioTrack(a.clip)
    want = int(len(ref) / std.frame_rate * AUDIO_RATE) * 4
    raw = b''
    while len(raw) < want:
        piece = os.read(track.fileno(), 1 << 20)
        if not piece:
            break
        raw += piece
    track.close()
    orig = np.frombuffer(raw[:want], np.float32).astype(np.float64)
    seg = heard[AUDIO_RATE:AUDIO_RATE * 21]            # 20 s, a second in
    seg = seg - seg.mean()
    o = np.concatenate([orig, orig[:seg.size]])        # the clip loops
    n = 1 << int(np.ceil(np.log2(o.size + seg.size)))
    corr = np.fft.irfft(np.fft.rfft(o, n) * np.conj(np.fft.rfft(seg, n)), n)
    lag = int(np.argmax(corr[:orig.size]))
    win = o[lag:lag + seg.size]
    score = float((win - win.mean()) @ seg / np.sqrt(((win - win.mean()) ** 2).sum()
                                                    * (seg ** 2).sum()))
    t_sound = (lag - AUDIO_RATE) / AUDIO_RATE - st.get('audio', 0.0)
    # **The sound says where in the clip the recording is; the pictures are
    # searched for only near there.** The video's chorus repeats nearly the
    # same shots - the fence and the sunglasses at 13 s and again at 122 s -
    # and a search of the whole clip put a System I recording's first
    # picture at 122 s when the transmitter had been on for ten. The sound
    # has no such twin (it matches at 0.99), and three seconds either side
    # is far more than any sync error worth finding.
    near = int(round(t_sound * std.frame_rate))
    window = [(near + d) % len(ref) for d in range(-75, 76)]
    first = min(window, key=lambda c: err(got[0], ref[c]))
    picks = np.arange(0, len(got), 25)
    match = {}
    for k in picks:
        centre = first + k
        cands = [(centre + d) % len(ref) for d in range(-60, 61)]
        match[int(k)] = min(cands, key=lambda c: err(got[k], ref[c]))
    lead = np.array([((match[k] - k - first + len(ref) // 2) % len(ref))
                     - len(ref) // 2 for k in picks])
    print(f"       first picture is original frame {first} "
          f"({first / std.frame_rate:.2f} s into the clip)")
    print(f"       picture k against original frame {first} + k, every 25th: "
          f"{lead.tolist()}  (0 = on time; a duplicate in the source can "
          f"make it read +-1)")
    drift = int(np.median(lead[-5:]) - np.median(lead[:5]))
    print(f"       frames gained or lost across {len(got)} pictures: {drift}")
    match = np.array([match[k] for k in picks])
    # PSNR on a sample of matched frames at full size.
    full_got = frames_of(a.recording, (W, H))
    psnr = []
    for k, m in list(zip(picks, match))[::max(1, len(picks) // 12)]:
        t = m / std.frame_rate
        p = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error',
                            '-ss', f"{t:.4f}", '-i', a.clip, '-frames:v', '1',
                            '-vf', fit + ",format=gray", '-f', 'rawvideo', '-'],
                           capture_output=True, check=True)
        r = np.frombuffer(p.stdout, np.uint8)[:W * H].reshape(H, W).astype(np.float32)
        mse = ((full_got[k] - r) ** 2).mean()
        psnr.append(10 * np.log10(255 ** 2 / mse))
    print(f"       PSNR against the original (luma, 12 pictures): median "
          f"{np.median(psnr):.1f} dB, range {min(psnr):.1f}-{max(psnr):.1f}")

    # Clip time of the recording's first picture, against its first sound.
    t_picture = first / std.frame_rate - st.get('video', 0.0)
    clip_len = len(ref) / std.frame_rate
    off = ((t_sound - t_picture + clip_len / 2) % clip_len) - clip_len / 2
    print(f"       the sound matches the original at correlation {score:.4f}")
    print(f"       sound against picture, against the original: {off * 1e3:+.1f} ms "
          f"({'sound late' if off < 0 else 'sound early'}; one frame is 40 ms)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('record')
    r.add_argument('--system', default='BG')
    r.add_argument('--freq', type=float, default=474.0)
    r.add_argument('--gain', type=float, default=70.0)
    r.add_argument('--secs', type=float, default=60.0)
    r.add_argument('--out', required=True)
    c = sub.add_parser('compare')
    c.add_argument('recording')
    c.add_argument('clip')
    a = ap.parse_args()
    return record(a) if a.cmd == 'record' else compare(a)


if __name__ == '__main__':
    raise SystemExit(main())
