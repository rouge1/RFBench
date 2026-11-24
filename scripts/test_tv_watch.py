#!/usr/bin/env python3
"""Watch, end to end in real time: every frame, and the sound in step.

    python scripts/test_tv_watch.py            # PAL G, 10 s
    python scripts/test_tv_watch.py --ntsc     # NTSC (System M)

No radio. A test signal - a white flash on one frame in 25 (30 in NTSC)
and a beep starting on the same frame - goes through the real transmit chain
(``VsbTransmitter``) at the radio's rate, paced
by a throttle as a radio would pace it, into the receiver's own blocks
(``VsbDemod``, the sound chain with its ``AudioTap``, and
``StreamingFrameSink``). Watch's player is swapped for an ffmpeg that
records exactly the stream ffplay would have been given, and the recording
is read back:

- **every frame arrives**: pictures a second against the standard's rate,
  and how many had to be repeated;
- **the flashes are a whole number of frames apart** - none skipped, none
  doubled;
- **picture and sound are in step**: each flash against the start of its
  beep, which ffplay would then play as recorded. The receiver this
  replaced had no relation between the two at all.
"""
import argparse
import os
import subprocess
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gnuradio import blocks, gr  # noqa: E402

from apps.ntsc_encode import NTSC, PAL, CompositeEncoder  # noqa: E402
from apps.tv_system import (SYSTEM_G, SYSTEM_M, AuralSound,  # noqa: E402
                            VsbDemod, VsbTransmitter)
from apps.tv_watch import AUDIO_RATE, AudioTap, StreamingFrameSink  # noqa: E402

failures = []


def check(name, ok, detail=''):
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{'  ' + detail if detail else ''}")
    if not ok:
        failures.append(name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ntsc', action='store_true')
    ap.add_argument('--secs', type=float, default=10.0)
    ap.add_argument('--stall', action='store_true',
                    help='freeze the player for 6 s half-way, as a stalled ffplay')
    a = ap.parse_args()
    system = SYSTEM_M if a.ntsc else SYSTEM_G
    std = system.standard
    rate = system.composite_rate
    radio_rate = 20e6
    period = 30 if a.ntsc else 25           # frames between flashes
    W, H = std.width, std.height

    print(f"building {period} frames of {std.key.upper()} with a flash and a beep")
    enc = CompositeEncoder(rate, standard=std)
    grey = np.full((H, W, 3), 0.25)
    white = np.ones((H, W, 3))
    frames = [enc.encode_frame(white if k == 0 else grey) for k in range(period)]
    composite = np.concatenate(frames).astype(np.float32)
    n_audio = int(round(composite.size / rate * AUDIO_RATE))
    t = np.arange(n_audio) / AUDIO_RATE
    beep = (0.5 * np.sin(2 * np.pi * 1000 * t) * (t < 0.2)).astype(np.float32)

    tb = gr.top_block("watch test", catch_exceptions=True)
    vsrc = blocks.vector_source_f(composite.tolist(), True)
    asrc = blocks.vector_source_f(beep.tolist(), True)
    # System M through VsbTransmitter is the NTSC transmitter's modulator to
    # float32 rounding (test_pal_transmit.py), so one chain serves both.
    tx = VsbTransmitter(system, radio_rate)
    pace = blocks.throttle(gr.sizeof_gr_complex, radio_rate)
    tap = AudioTap()
    demod = VsbDemod(radio_rate, system)
    sound = AuralSound(radio_rate, system, volume=0.0, tap=tap)
    sink = StreamingFrameSink(demod.composite_rate, standard=std, tap=tap)
    sink.volume = 1.0
    out = os.path.join(tempfile.mkdtemp(prefix='tvwatch-'), 'watched.nut')
    sink.play_cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
                     '-f', 'nut', '-i', 'pipe:0', '-c', 'copy', out]
    null = blocks.null_sink(gr.sizeof_float)
    tb.connect(vsrc, (tx, 0))
    tb.connect(asrc, (tx, 1))
    tb.connect(tx, pace)
    s2v = blocks.stream_to_vector(gr.sizeof_float, sink.VLEN)
    tb.connect(pace, demod, s2v, sink)
    tb.connect(pace, sound, null)

    print(f"running {a.secs:g} s in real time ({sink.workers} decode threads)")
    tb.start()
    time.sleep(1.0)                         # let the filters fill
    sink.start_player(sink.player_argv('test'))
    t0 = time.time()
    d0 = sink.frames_decoded
    if a.stall:
        import signal as _signal
        time.sleep(a.secs / 2 - 3)
        rec = sink._player_procs[-1]
        rec.send_signal(_signal.SIGSTOP)
        time.sleep(6.0)
        rec.send_signal(_signal.SIGCONT)
        time.sleep(a.secs / 2 - 3)
    else:
        time.sleep(a.secs)
    decoded = sink.frames_decoded - d0
    wall = time.time() - t0
    sink.stop_player()
    tb.stop()
    tb.wait()
    time.sleep(1.0)
    fps = decoded / wall
    print(f"       decoded {decoded} frames in {wall:.1f} s, {fps:.2f} a second; "
          f"{sink.frames_dropped} windows dropped, {sink.failures} failed, "
          f"{sink.frames_repeated} repeated, {sink.audio_short} short of sound, {sink.queue_dropped} lost in the queue to the player")
    check("every frame is decoded, at the standard's rate",
          fps > 0.97 * std.frame_rate, f"{fps:.2f} of {std.frame_rate:.2f}")
    check("next to none had to be repeated",
          sink.frames_repeated <= 2, str(sink.frames_repeated))

    # Read the recording back: one brightness per frame, and the sound.
    probe = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error',
                            '-i', out, '-map', '0:v', '-f', 'rawvideo',
                            '-pix_fmt', 'gray', '-'], capture_output=True)
    video = np.frombuffer(probe.stdout, np.uint8)
    nframes = video.size // (W * H)
    level = video[:nframes * W * H].reshape(nframes, -1).mean(axis=1)
    probe = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error',
                            '-i', out, '-map', '0:a', '-f', 'f32le', '-'],
                           capture_output=True)
    audio = np.frombuffer(probe.stdout, np.float32)
    # **Each stream's own start time counts.** ffmpeg drops the first few
    # tens of milliseconds of sound while it starts and labels what it keeps
    # from where it really is (45 ms in, measured); ffplay plays by those
    # timestamps. Reading the raw sound as though it began with the picture
    # put every beep 46 ms early when the stream itself was in step.
    starts = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                             'stream=codec_type,start_time', '-of', 'csv=p=0',
                             out], capture_output=True, text=True).stdout.split()
    start = {kind: float(t) for kind, t in (x.split(',') for x in starts)}
    audio_lead = start.get('audio', 0.0) - start.get('video', 0.0)
    print(f"       sound stream starts {audio_lead * 1e3:+.1f} ms after the picture's")
    print(f"       recorded {nframes} pictures and {audio.size / AUDIO_RATE:.2f} s of sound")
    check("the recording has as much sound as picture",
          abs(audio.size / AUDIO_RATE + audio_lead - nframes * std.frame)
          < 2 * std.frame,
          f"{audio.size / AUDIO_RATE:.3f} s against {nframes * std.frame:.3f}")

    flashes = np.flatnonzero(level > (level.max() + np.median(level)) / 2)
    gaps = np.diff(flashes)
    print(f"       flashes on pictures {flashes.tolist()}")
    if a.stall:
        # Frames a stalled player missed are gone from both streams, so
        # one gap is short by however many; the rest are a cycle.
        check("the flashes come a cycle apart, but for the stall",
              gaps.size >= 3 and np.sum(gaps != period) <= 1, str(gaps.tolist()))
        print(f"       {sink.queue_dropped} frames dropped, picture and sound together")
    else:
        check("the flashes come exactly a cycle apart - nothing skipped or doubled",
              gaps.size >= 3 and np.all(gaps == period), str(gaps.tolist()))

    # The beep's start, in recorded-picture time.
    env = np.abs(audio)
    win = int(0.002 * AUDIO_RATE)
    smooth = np.convolve(env, np.ones(win) / win, 'same')
    onsets = np.flatnonzero((smooth[1:] > 0.15) & (smooth[:-1] <= 0.15)) + 1
    onset_t = onsets / AUDIO_RATE + audio_lead
    offsets = []
    for f in flashes:
        ft = f * std.frame
        # A flash before the sound stream's first sample has its beep cut
        # off at the start, and the "onset" found would be the stream's.
        if ft < audio_lead + 0.25:
            continue
        if onset_t.size:
            k = np.argmin(np.abs(onset_t - ft))
            offsets.append((onset_t[k] - ft) * 1e3)
    print(f"       sound against picture, ms: {np.round(offsets, 1).tolist()}")
    check("each beep starts within a quarter of a frame of its flash",
          len(offsets) >= 3 and max(abs(o) for o in offsets) < 1000 * std.frame / 4,
          f"worst {max(abs(o) for o in offsets):.1f} ms" if offsets else "none")
    check("and stays there - no drift across the run",
          len(offsets) >= 3 and np.ptp(offsets) < 10, f"{np.ptp(offsets):.1f} ms spread"
          if offsets else "")

    import shutil
    shutil.rmtree(os.path.dirname(out), ignore_errors=True)

    print()
    if failures:
        print(f"{len(failures)} FAILED: {', '.join(failures)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
