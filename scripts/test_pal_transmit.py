#!/usr/bin/env python3
"""The PAL television transmit and receive chains, with no radio.

    python scripts/test_pal_transmit.py
    python scripts/test_pal_transmit.py --video clip.mp4

``test_pal_loopback.py`` checks the 625-line composite encoder against the
decoder. This checks what sits between them and the antenna on a broadcast
channel - ``apps/tv_system.py`` - against ITU-R BT.1701-1 Table 1
(``docs/itu/BT.1701-1-200508-I.pdf``):

- **the table itself**: spacings, edges, sidebands, levels, deviation,
  pre-emphasis and the vision/sound power ratio, column by column;
- **that it is the NTSC transmitter's table too**: ``VsbModulator`` on
  System M gives ``NtscModulator``'s samples (to float32 rounding), so the PAL apps are
  built on the same numbers the NTSC apps were proven on off air;
- **the carrier levels** each system puts on the air, read back off the
  envelope through a Nyquist slope as a television would;
- **the spectrum**: the vestige and the top of the main sideband
  attenuated as item 6 asks, and nothing out of the channel;
- **a picture and its sound through the whole chain** - ``VsbTransmitter``
  at the radio's own rate, shifted by the LO offset, into ``VsbDemod`` and
  ``AuralSound`` - for B/G and I, at 20 MS/s (HackRF, VSG60, BB60D) and
  25 (an Ettus N210).
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gnuradio import analog, blocks, gr  # noqa: E402
from gnuradio import filter as gr_filter  # noqa: E402

from apps.ntsc_decode import CompositeDecoder  # noqa: E402
from apps.ntsc_encode import NTSC, PAL, CompositeEncoder  # noqa: E402
from apps.ntsc_source import VideoFile, colour_bars, have_ffmpeg  # noqa: E402
from apps.tv_system import (AUDIO_RATE, SYSTEM_B, SYSTEM_G,  # noqa: E402
                            SYSTEM_I, SYSTEM_M, AuralSound, VsbDemod,
                            VsbModulator, VsbTransmitter, nyquist_slope_taps,
                            pal_channel_centre, pal_channel_for_centre,
                            pal_channels, pal_system)

failures = []


def check(name, ok, detail=''):
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{'  ' + detail if detail else ''}")
    if not ok:
        failures.append(name)


def close(name, got, want, tol):
    ok = abs(got - want) <= tol
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: {got:.6g}")
    if not ok:
        print(f"       wanted {want:.6g} +/- {tol:g}")
        failures.append(name)


def run(blocks_in, block, out_type='c'):
    """Push vectors through ``block`` and collect what comes out."""
    tb = gr.top_block("t", catch_exceptions=True)
    sink = blocks.vector_sink_c() if out_type == 'c' else blocks.vector_sink_f()
    srcs = [blocks.vector_source_f(np.asarray(v, np.float32).tolist())
            for v in blocks_in]
    for k, s in enumerate(srcs):
        tb.connect(s, (block, k))
    tb.connect(block, sink)
    tb.run()
    return np.array(sink.data())


def table():
    print("BT.1701 Table 1, column by column")
    rows = [
        # system, ch bw, sound, main, vestige, white range, blank range,
        # deviation, pre-emphasis, power ratio range
        (SYSTEM_M, 6, 4.5, 4.2, 0.75, (0.10, 0.15), (0.725, 0.775), 25, 75, (5, 10)),
        (SYSTEM_B, 7, 5.5, 5.0, 0.75, (0.10, 0.15), (0.725, 0.775), 50, 50, (10, 20)),
        (SYSTEM_G, 8, 5.5, 5.0, 0.75, (0.10, 0.15), (0.725, 0.775), 50, 50, (10, 20)),
        (SYSTEM_I, 8, 5.9996, 5.5, 1.25, (0.18, 0.22), (0.74, 0.78), 50, 50, (5, 20)),
    ]
    for s, bw, snd, main, vest, white, blank, dev, pre, ratio in rows:
        k = s.key
        close(f"{k}: item 1, channel width, MHz", s.channel_bw / 1e6, bw, 1e-9)
        close(f"{k}: item 2, sound above vision, MHz", s.aural_spacing / 1e6, snd, 1e-9)
        close(f"{k}: item 3, vision above the lower edge, MHz",
              (s.visual_carrier + s.channel_bw / 2) / 1e6, 1.25, 1e-9)
        close(f"{k}: item 4, main sideband, MHz", s.main_sideband / 1e6, main, 1e-9)
        close(f"{k}: item 5, vestige, MHz", s.vestige / 1e6, vest, 1e-9)
        check(f"{k}: item 8, peak white in {white}", white[0] <= s.carrier_at_white <= white[1],
              f"{s.carrier_at_white:.3f}")
        b = s.blanking_carrier()
        check(f"{k}: item 8, blanking in {blank}", blank[0] <= b <= blank[1], f"{b:.4f}")
        close(f"{k}: item 10, deviation, kHz", s.deviation / 1e3, dev, 1e-9)
        close(f"{k}: item 11, pre-emphasis, us", s.preemphasis * 1e6, pre, 1e-9)
        r = 1 / s.aural_amplitude ** 2
        check(f"{k}: item 12, vision/sound power {ratio[0]}/1 to {ratio[1]}/1",
              ratio[0] * 0.98 <= r <= ratio[1] * 1.02, f"{r:.1f}/1")
        top = s.aural_carrier + s.sound_bandwidth / 2
        check(f"{k}: the sound's Carson bandwidth stays inside the channel",
              top < s.channel_bw / 2, f"to {top / 1e6:.3f} of {s.channel_bw / 2e6:g} MHz")
        lo = s.lo_offset
        check(f"{k}: the radio's LO ({lo / 1e6:g} MHz up) is clear of the channel",
              lo > top + 0.5e6, f"{(lo - top) / 1e6:.2f} MHz above the sound")
    close("System M's VSB constants are the NTSC transmitter's: mix, MHz",
          SYSTEM_M.vsb_mix / 1e6, -1.725, 0)
    close("cutoff, MHz", SYSTEM_M.vsb_cutoff / 1e6, 2.475, 0)
    close("trim, kHz", SYSTEM_M.vsb_trim / 1e3, -25, 0)

    print("\nthe European channel plan")
    close("E21 centre, MHz (470-478)", pal_channel_centre('E21'), 474.0, 0)
    close("E69 centre, MHz (854-862)", pal_channel_centre('E69'), 858.0, 0)
    close("E5 centre, MHz (174-181)", pal_channel_centre('E5'), 177.5, 0)
    close("E2 vision carrier, MHz", pal_channel_centre('E2') + SYSTEM_B.visual_carrier / 1e6,
          48.25, 1e-9)
    close("E21 vision carrier, MHz", 474.0 + SYSTEM_G.visual_carrier / 1e6, 471.25, 1e-9)
    check("B/G is System B on a VHF channel", pal_system('BG', 177.5) is SYSTEM_B)
    check("and System G on a UHF one", pal_system('BG', 474.0) is SYSTEM_G)
    check("I is I anywhere", pal_system('I', 474.0) is SYSTEM_I)
    check("every channel is found again from its centre",
          all(pal_channel_for_centre(c) == n for n, c in pal_channels()))


def same_as_ntsc():
    print("\nVsbModulator on System M is the NTSC transmitter's modulator")
    from apps.ntscAnalogVideoRecorded import NtscModulator
    enc = CompositeEncoder(10e6, standard=NTSC)
    composite = np.concatenate([enc.encode_frame(colour_bars()) for _ in range(2)])
    ours = run([composite], VsbModulator(10e6, SYSTEM_M))
    theirs = run([composite], NtscModulator(10e6, 'negative'))
    again = run([composite], NtscModulator(10e6, 'negative'))
    check("the same number of samples", ours.size == theirs.size,
          f"{ours.size} and {theirs.size}")
    # Not bit for bit: NtscModulator does not match *itself* bit for bit
    # from one run to the next - GNU Radio's FFT filter rounds differently
    # depending on where the scheduler's buffers fall, by a few float32
    # steps (6.7e-8 measured). So the test is that the two differ by no
    # more than that: the same filter, mixers and levels, to the last bit
    # that is stable at all.
    own = np.abs(theirs - again).max()
    diff = np.abs(ours - theirs).max()
    print(f"       NtscModulator against itself: {own:.2g}; against ours: {diff:.2g}")
    check("and every one the same to float32 rounding", diff < 1e-6,
          f"largest difference {diff:.3g}")


def envelope(rf, rate, system):
    """Detect it as a television does, Nyquist slope and all."""
    n = rf.size
    base = rf * np.exp(-2j * np.pi * system.visual_carrier * np.arange(n) / rate)
    freqs = np.fft.fftfreq(n, 1.0 / rate)
    slope = np.clip(0.5 + freqs / (2 * 0.75e6), 0.0, 1.0)
    return 2.0 * np.abs(np.fft.ifft(np.fft.fft(base) * slope))


def levels(system):
    rate = system.composite_rate
    enc = CompositeEncoder(rate, color=False, standard=PAL)
    W, H = PAL.width, PAL.height
    flat = np.concatenate([enc.encode_frame(np.full((H, W, 3), v)) for v in (0.0, 1.0)])
    env = envelope(run([flat], VsbModulator(rate, system)), rate, system)
    body = env[env.size // 4:]
    at_sync = np.percentile(body, 99.9)
    at_white = np.percentile(body, 0.1)
    print(f"\n{system.key}: carrier levels, read off the envelope")
    print(f"       carrier runs {at_white:.3f} .. {at_sync:.3f}")
    close(f"{system.key}: sync at 100% of peak carrier", at_sync, 1.0, 0.03)
    close(f"{system.key}: peak white at {system.carrier_at_white:.1%}", at_white,
          system.carrier_at_white, 0.03)
    # Blanking: the most common level in a black frame's envelope.
    black = env[env.size // 8: env.size // 2]
    hist, edges = np.histogram(black, bins=400)
    centres = (edges[1:] + edges[:-1]) / 2
    upper = centres > 0.5
    blank = centres[upper][np.argmax(hist[upper])]
    close(f"{system.key}: blanking where item 8 puts it", blank,
          system.blanking_carrier(), 0.02)


def spectrum(system):
    """Item 6: the vestige and the top of the main sideband, attenuated."""
    rate = system.composite_rate
    rng = np.random.default_rng(1)
    # Noise across the whole video band stands in for every picture at once.
    video = np.clip(0.65 + 0.2 * rng.standard_normal(1 << 20), 0.3, 1.0)
    rf = run([video], VsbModulator(rate, system))[4096:]
    nfft = 4096
    seg = rf[:(rf.size // nfft) * nfft].reshape(-1, nfft) * np.hanning(nfft)
    psd = (np.abs(np.fft.fft(seg, axis=1)) ** 2).mean(axis=0)
    freqs = np.fft.fftfreq(nfft, 1 / rate)

    def at(offset_from_vision):
        f = system.visual_carrier + offset_from_vision
        near = np.abs(freqs - f) < 50e3
        return 10 * np.log10(psd[near].mean())

    ref = at(1.5e6)          # well inside the main sideband
    print(f"\n{system.key}: the spectrum, against the main sideband 1.5 MHz up")
    for off, want in {'B': [(-1.25e6, 20), (-3.0e6, 20)],
                      'G': [(-1.25e6, 20), (-3.0e6, 20)],
                      'I': [(-3.0e6, 20)]}[system.key]:
        down = ref - at(off)
        check(f"{system.key}: item 6, at least {want} dB down {off / 1e6:+g} MHz from vision",
              down >= want, f"{down:.1f} dB")
    flat = ref - at(system.main_sideband - 0.4e6)
    check(f"{system.key}: flat to near the top of the {system.main_sideband / 1e6:g} MHz sideband",
          abs(flat) < 1.0, f"{flat:+.2f} dB at {system.main_sideband / 1e6 - 0.4:g} MHz")
    top = ref - at(system.main_sideband + 0.4e6)
    check(f"{system.key}: closed 0.4 MHz past it, before the sound",
          top > 20, f"{top:.1f} dB down")
    # Above the channel, everything must be gone. Below it, System I's
    # vestige is meant to reach the edge (1.25 MHz below a carrier 1.25
    # above it) and item 6 only asks for 20 dB by -3.0 MHz, checked above;
    # so the lower bound is half a megahertz past each system's vestige.
    edge = system.channel_bw / 2
    lower = system.visual_carrier - system.vestige - 0.5e6
    outside = (((freqs > edge + 0.1e6) | (freqs < min(lower, -edge - 0.1e6)))
               & (np.abs(freqs) < rate / 2 - 0.2e6))
    worst = 10 * np.log10(psd[outside].max()) - ref
    check(f"{system.key}: nothing outside the {system.channel_bw / 1e6:g} MHz channel "
          "within 40 dB of the picture", worst < -40, f"{worst:.1f} dB")


def through_the_air(system, radio_rate, frame, label):
    """Picture and sound through VsbTransmitter, at the radio's rate, and back."""
    rate = system.composite_rate
    enc = CompositeEncoder(rate, standard=PAL)
    composite = np.concatenate([enc.encode_frame(frame) for _ in range(3)])
    n = composite.size
    t = np.arange(n) / rate
    # Two tones on the sound: one low, one high enough that pre-emphasis
    # matters. Pre-emphasised and railed as the app does it, at 48 kHz.
    a = AUDIO_RATE
    ta = np.arange(int(n / rate * a)) / a
    tones = 0.3 * np.sin(2 * np.pi * 1000 * ta) + 0.15 * np.sin(2 * np.pi * 7000 * ta)
    tb = gr.top_block("audio", catch_exceptions=True)
    src = blocks.vector_source_f(tones.astype(np.float32).tolist())
    pre = analog.fm_preemph(float(a), tau=system.preemphasis)
    rail = analog.rail_ff(-1.0, 1.0)
    snk = blocks.vector_sink_f()
    tb.connect(src, pre, rail, snk)
    tb.run()
    audio = np.array(snk.data(), np.float32)

    radio = run([composite, audio], VsbTransmitter(system, radio_rate))
    print(f"\n{system.key} at {radio_rate / 1e6:g} MS/s: {label}")
    peak = np.abs(radio).max()
    check(f"{system.key}: the radio's input stays inside full scale",
          peak < 1.0, f"peak {peak:.3f}")

    # Where the carriers landed, relative to the radio's LO.
    nfft = 1 << 16
    spec = np.abs(np.fft.fft(radio[nfft:2 * nfft] * np.hanning(nfft)))
    freqs = np.fft.fftfreq(nfft, 1 / radio_rate)
    vision = system.visual_carrier - system.lo_offset
    sound = system.aural_carrier - system.lo_offset
    near_v = np.abs(freqs - vision) < 30e3
    near_s = np.abs(freqs - sound) < 120e3
    fv = freqs[near_v][np.argmax(spec[near_v])]
    close(f"{system.key}: vision carrier, MHz from the LO", fv / 1e6, vision / 1e6, 0.001)
    sv = 20 * np.log10(spec[near_s].max() / spec[near_v].max())
    print(f"       sound carrier peaks {sv:.1f} dB under vision")

    # Back down: the receiver's own blocks.
    tbr = gr.top_block("rx", catch_exceptions=True)
    rsrc = blocks.vector_source_c(radio.astype(np.complex64).tolist())
    demod = VsbDemod(radio_rate, system)
    sound_rx = AuralSound(radio_rate, system, volume=1.0)
    vsnk = blocks.vector_sink_f()
    asnk = blocks.vector_sink_f()
    tbr.connect(rsrc, demod, vsnk)
    tbr.connect(rsrc, sound_rx, asnk)
    tbr.run()
    video = np.array(vsnk.data(), np.float64)
    close(f"{system.key}: the demodulator gives back the composite rate",
          video.size / (radio.size / radio_rate), rate, rate * 0.002)
    dec = CompositeDecoder(rate, standard=PAL)
    out = dec.decode_frame(video[video.size // 6:])
    check(f"{system.key}: a {PAL.width}x{PAL.height} picture comes back",
          out.shape == frame.shape, str(out.shape))
    if label == "colour bars":
        worst = 0.0
        names = ['white', 'yellow', 'cyan', 'green', 'magenta', 'red', 'blue']
        W = PAL.width
        for k, name in enumerate(names):
            col = (k * W // 7 + (k + 1) * W // 7) // 2
            g = out[200:300, col - 12:col + 12].mean(axis=(0, 1))
            want = frame[250, col]
            err = float(np.abs(g - want).max())
            worst = max(worst, err)
        # The NTSC chain reads 0.081 against a 0.10 limit (test_ntsc_transmit).
        close(f"{system.key}: worst colour error through the whole chain", worst, 0.0, 0.10)
    else:
        err = float(np.abs(out - frame).mean())
        close(f"{system.key}: mean picture error through the whole chain", err, 0.0, 0.05)

    heard = np.array(asnk.data(), np.float64)
    skip = int(0.02 * AUDIO_RATE)
    heard = heard[skip:]
    spec = np.abs(np.fft.rfft(heard * np.hanning(heard.size)))
    f = np.fft.rfftfreq(heard.size, 1 / AUDIO_RATE)

    def tone(hz):
        return spec[np.abs(f - hz) < 60].max()

    lo_db = 20 * np.log10(tone(1000) / 0.3)
    hi_db = 20 * np.log10(tone(7000) / 0.15)
    print(f"       sound: 1 kHz {lo_db:+.2f} dB, 7 kHz {hi_db:+.2f} dB relative")
    check(f"{system.key}: pre-emphasis and de-emphasis cancel (7 kHz against 1 kHz)",
          abs(hi_db - lo_db) < 1.0, f"{hi_db - lo_db:+.2f} dB")
    pk = f[np.argmax(spec * (f > 200))]
    close(f"{system.key}: the loudest thing heard is the 1 kHz tone, Hz", pk, 1000, 30)
    dev = sound_rx.deviation_hz()
    # 0.3 + 0.15 sine, pre-emphasised: rms deviation about 0.27 x 50 kHz.
    check(f"{system.key}: the deviation meter reads the programme",
          5e3 < dev < 30e3, f"{dev / 1e3:.1f} kHz rms")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--video', help='a video file to send as well as bars')
    args = ap.parse_args()

    table()
    same_as_ntsc()
    for s in (SYSTEM_G, SYSTEM_I):
        levels(s)
    for s in (SYSTEM_B, SYSTEM_G, SYSTEM_I):
        spectrum(s)
    bars = colour_bars(PAL.width, PAL.height)
    for s in (SYSTEM_G, SYSTEM_I):
        through_the_air(s, 20e6, bars, "colour bars")
    through_the_air(SYSTEM_G, 25e6, bars, "colour bars")
    through_the_air(SYSTEM_B, 20e6, bars, "colour bars")
    if args.video and have_ffmpeg():
        src = VideoFile(args.video, PAL.width, PAL.height, frame_rate=PAL.frame_rate)
        for _ in range(50):
            frame = src.next_frame()
        src.close()
        through_the_air(SYSTEM_G, 20e6, frame, os.path.basename(args.video))

    print()
    if failures:
        print(f"{len(failures)} FAILED: {', '.join(failures)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
