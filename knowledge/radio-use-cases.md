# Radio use cases: what runs together, and where

One of the notes in `knowledge/`. What applies everywhere, and which file
covers what, is in [CLAUDE.md](../CLAUDE.md).

This is the chart: every way of using two of the bench's radios at once
that has been tried, what happened, and what is still to try. The
measurements behind each row are in the note it links to, most of them in
[two radios on one host](two-radios-one-host.md). Keep this file a
summary - a new result goes in its subject's note first, then a row here.

The machines: **worklaptop1**, 8 cores, holds the VSG60; **the BB60D's
machine**, an i7-12700H with 6 performance cores (12 threads) and 8
efficiency cores, holds the BB60D. The N210 is on a USB Ethernet adapter
and moves between them (on the BB60D's machine since 2026-11-25). Every
radio is on an antenna, so anything that transmits is over the air and
needs the user's go for that run.

## The radios alone: raw streaming, no apps

| Use case | Host | Rates | Result | Where |
|---|---|---|---|---|
| **Two receivers**: BB60D + N210 | BB60D's machine | 40 + 25 MS/s | ✅ 0 drops on either | [two radios](two-radios-one-host.md#bb60d-and-n210-together-2026-11-23), 2026-11-23 |
| **Transmit and receive our own signal**: VSG60 → N210 | worklaptop1 | 20 and 40 → 25 MS/s | ✅ no gap, no drop | [two radios](two-radios-one-host.md#vsg60-and-n210-together-2026-11-25), 2026-11-25 |
| **Transmit and receive our own signal**: N210 → BB60D | BB60D's machine | 25 → 5 MS/s | ✅ 0 `U`, no gap | [two radios](two-radios-one-host.md#the-n210-transmitting-beside-the-bb60d-2026-11-25), 2026-11-25 |
| VSG60 + BB60D | either | - | ❌ will not stream together on one host | [radios](radios.md#vsg60-notes) |
| VSG60 + HackRF | either | - | ⚠️ only in two processes | [radios](radios.md#vsg60-notes) |
| N210 RF1 → its own RF2 | either | - | ⚠️ one process only (`txrx_loopback_to_file`) | [ettus](ettus.md#rf1-into-rf2-on-the-one-radio) |
| **Two transmitters at once**: VSG60 + N210, both into the BB60D | two machines | 20 + 25 MS/s | ✅ both tones in one capture, no gap on either, 0 `U`, 0 overruns | [below](#two-transmitters-at-once-2026-11-25), 2026-11-25 |
| **A wider band**: BB60D + N210 on adjacent bands, stitched | BB60D's machine | 40 + 25 MS/s | ✅ one coherent 26 MHz band; the halves add in phase - free-running with the phase tracked every 2 ms, or on a **shared 10 MHz** with one fixed phase | [below](#a-wider-band-two-receivers-stitched-2026-11-25), 2026-11-25 |

How "no gap" was judged: a CW tone, mixed to DC in 1,000-sample blocks,
checked for any block under half the median amplitude and any phase step
over 0.5 rad, plus UHD's `O`/`D`/`U` and the BB60D's overrun count. The
tone sits at 1,000,037 Hz so that losing a short run of samples moves its
phase (at exactly 1 MHz, 500 samples at 20 MS/s are whole cycles and
invisible). Details in
[two radios](two-radios-one-host.md#vsg60-and-n210-together-2026-11-25).

## ATSC: a transmitter app and a receiver app

| Use case | Host | Transmitter | Receiver | Result |
|---|---|---|---|---|
| Two machines | separate | live app | app | ✅ "Locked - clean stream", MER 20-23 dB ([ettus](ettus.md#every-app-on-the-n210-2026-11-21)) |
| VSG60 → N210, both live | worklaptop1 | live, 3.3 cores | 2-3 overflows a second | ❌ never decoded, MER 15-17 dB |
| VSG60 → N210, transmitter streamed from a rendered file | worklaptop1 | 0.9 core | about one overflow a second once decoding | ❌ the receiver alone is short of 8 cores (MER 23 dB, locked in places) |
| N210 → BB60D, both live, `powersave` | BB60D's machine | 21,033 `U` | 0 overruns | ❌ one clean 5 s window of 12 |
| the same, `performance` governor | BB60D's machine | 6,595 `U` | 0 overruns | ❌ |
| the same, each app pinned (`taskset`) | BB60D's machine | 662 `U` | 3 BB60D overruns | ❌ |
| **N210 → BB60D, transmitter streamed from a rendered file** | **BB60D's machine** | **0 `U`** | **0 overruns** | ✅ **"Locked - clean stream" in 10 of 12 windows**, MER 18.3-18.8 dB |

Details: [worklaptop1](two-radios-one-host.md#then-the-apps-atsc-both-ends-on-worklaptop1-same-day),
[the BB60D's machine](two-radios-one-host.md#the-n210-transmitting-beside-the-bb60d-2026-11-25).

## Where each machine runs out

| Machine | Cores | What runs out first |
|---|---|---|
| worklaptop1 | 8 | the ATSC **receiver**: about 4 cores, falls behind once it is locked and decoding |
| the BB60D's machine | 6 P + 8 E (20 threads) | the live ATSC **transmitter**: its chain renders at only about 1.6 times real time at 25 MS/s on 2.7 threads, before ffmpeg; beside the receiver it lands off the P-cores and underruns |

So the radios share either machine at full rate; two heavy apps on one
machine is CPU. A heavy transmitter app can be taken off the machine by
rendering its own block chain to a file and streaming that
(`vsg_stream_file.py` on the VSG60, UHD's `tx_samples_from_file --repeat`
on the N210) - how the one-machine ATSC decode above was done. CPUs on the
BB60D's machine are numbered P-cores first: 0-11 the P-cores' two threads
each, 12-19 the E-cores.

## Two transmitters at once (2026-11-25)

Over the air (user's go): the VSG60 on worklaptop1 streaming a CW tone at
910.000037 MHz (−30 dBm, 20 MS/s) and the N210 on the BB60D's machine
sending one at about 922.0007 MHz (`tx_waveforms`, 25 MS/s, gain 0), the
BB60D recording both at 40 MS/s centred 915 MHz, ATT −20, for 12 s.

- **Both tones were in the one capture**, 0 BB60D overruns, 0 `U` on the
  N210. The N210's tone: no gap, no phase step, 41.6 dB block SNR. The
  VSG60's (19 dB weaker at that attenuation): no gap of its own.
- **What the gap finder did flag was someone else**: three 2 ms bursts
  within 200 kHz of 910 MHz, 20-25 dB over the floor, exactly 0.648 s
  apart - another device in the ISM band. Before calling a flag a gap,
  look at the band around the tone at that moment.
- Not tried yet: two transmitter **apps** at once, and both transmitters
  on one host (that needs the N210 back on worklaptop1).

## A wider band: two receivers stitched (2026-11-25)

Over the air (user's go), inside 902-928 MHz. The VSG60 on worklaptop1
streamed a 4 s test file at 40 MS/s, centred 915 MHz, −20 dBm: 26 tones
(902.5-927.5 MHz, whole Hz), a sawtooth sweep 903 → 927 MHz every 10 ms,
and twelve 1 ms silences at seeded random times. On the BB60D's machine,
started together, **the BB60D took the lower half** (40 MS/s centred
909 MHz) **and the N210 the upper** (25 MS/s centred 921 MHz, RX2, gain
25) for 6 s each; the two overlap at 911-922 MHz. 0 overruns, no `O`.

The stitch used only the two captures (nothing from the file), then was
graded against the file. In order:

1. N210 to 40 MS/s (×8/5); both shifted to one frame centred 915 MHz.
2. **Frequency**: the 12 tones both saw put the N210 174.5 Hz above the
   BB60D at 915 MHz, rising 0.178 Hz/MHz - one radio's clock 0.19 ppm off
   the other's.
3. **Time, coarse**: power envelopes of the overlap band, cross-correlated
   on the silences: the N210's capture started 1.0412 s after the BB60D's.
4. **Time, fine**: GCC-PHAT on the overlap band in 3.3 ms segments, a line
   through the lags: drift −0.193 ppm (agreeing with step 2), residual
   0.11 samples (3 ns). Two traps: plain cross-correlation is ruled by the
   strong tones and gives flat, ambiguous peaks (second best 0.92-1.00 of
   the best); and whitening must be limited to the overlap band, or the
   empty bins' noise wins and every lag lands on the window's edge.
5. **Phase, tracked**: the radios' relative phase **wanders** - 24 rad in
   2.5 s, by as much as 1.4 Hz over a quarter second, the same at every
   frequency - because each runs on its own reference oscillator. A fixed
   gain and phase left the halves 1.37 rad apart and 10 dB adrift; the
   complex gain is measured from the overlap band every 2 ms and applied
   smoothly in time. A 10 MHz reference shared by both radios would remove
   this; neither is wired to one.
6. **Residual delay and phase** at the seam from the cross-spectrum, and
   **the level** from the strong lines either side of the seam - not from
   |cross-spectrum / power|, which noise in the N210's half biases 4 dB low.
7. **Join** at 916.5 MHz (mid-overlap) with complementary filters, 1 MHz
   transition.

| Graded against the file | Result |
|---|---|
| The 26 tones, 902.5-927.5 MHz | all present, within 3.5 dB: a tilt of +1.7 to −1.7 dB across the band, no step at the seam; the tone on the seam 1.1 dB low |
| The sweep, compressed against its known shape | stitched peak 1.00; the BB60D's half alone 0.59, the N210's 0.42 (13.5 and 10.5 of 24 MHz predict 0.5625 and 0.4375); the halves sum to 1.008 and sit 0.26 rad apart |
| Pulse width (−3 dB) | stitched 50 ns; either half 100 ns; 1/24 MHz is 42 ns |
| The stitched frame against the VSG60 | +0.376 ppm (the BB60D's own offset) |

So two receivers on adjacent bands make one band about twice as wide,
coherent across the seam, from one machine - with the phase tracked,
since the clocks are not shared (but see the next section). Over the air that stays inside 26 MHz;
anything wider than the ISM band needs a cable and a pad. The scripts
(generator, stitch, grade) were throwaways in the session scratchpad.

## The same on one clock: a shared 10 MHz reference (2026-11-25)

An SMA cable from the BB60D's rear **port 2** (*Trig/Sync In/Out*) to the
N210's **REF IN**, the BB60D switched to output its 10 MHz through the
vendor library (SoapySDR cannot - how, in
[radios](radios.md#signal-hound-bb60d-as-a-receiver)), the N210 on
`--ref external`. Checked first with no RF: the N210 on its internal clock
reports `ref_locked` True; on external with the BB60D not outputting,
False; with the BB60D outputting, True; and False again once the BB60D
closes. Then the same test file over the air (user's go), the same tunings
and gains, both captures on the one clock, stitched the same way.

| | Free-running (above) | **Shared 10 MHz** |
|---|---|---|
| Frequency between the two radios, from the tones both see | +174.6 Hz | **−0.18 Hz** (constant; the tuners' steps) |
| Clock drift between them | −0.193 ppm | **−0.002 ppm** |
| Relative phase on a tone both see, its constant offset removed, 2.5 s | wanders 11.2 rad | **0.05 rad** (rms 0.007) |
| Stitched with **one fixed complex gain**, no tracking: the halves' compressed sweeps | 1.00 rad apart, peak 0.40 of their sum | **0.03 rad apart, peak 0.98** |
| Tone spread, 902.5-927.5 MHz | 4.3 dB | 4.1 dB |

So on a shared reference one phase, measured once in the overlap, holds
for the whole capture: the 2 ms tracking, and the need for signal in the
overlap all the time, go away. It still takes the overlap to find that
one phase and the time offset - the captures start whenever each radio
opens; a trigger shared through the same port 2 (it is also a trigger
input) or the N210's PPS input is the next step for that, not tried. One
thing not explained: the fine-timing residual was 0.87 samples on the
shared clock against 0.11 free-running, though the stitch came out
coherent.

The VSG60 can join the same clock through its BNC 10 MHz input (an SMA
to BNC adapter); not tried.
