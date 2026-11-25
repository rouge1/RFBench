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
| **Two transmitters at once** | - | - | ❔ not tested - [planned below](#planned-two-transmitters-at-once) | - |
| **A wider band** than one radio's | - | - | ❔ not tested - [planned below](#planned-a-wider-band) | - |

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

## Planned: two transmitters at once

Not run yet. The VSG60 on worklaptop1 and the N210 on the BB60D's machine
transmitting together, the BB60D receiving both:

- **Raw first:** a CW tone from each, inside the BB60D's 27 MHz and inside
  the 902-928 MHz ISM band - the VSG60 at 909 + 1.000037 MHz, the N210 at
  921 + 1.000037 MHz, say - and the same gap check on both tones in one
  BB60D capture at 40 MS/s centred on 915 MHz, beside each transmitter's
  own `U`/underrun count. Two transmitters on two machines exercise the
  air, not a host; the N210 transmitting beside the BB60D is already shown
  clean.
- **Then two transmitter apps**, one per radio at different frequencies,
  each judged by the BB60D in turn or by a receiver app.
- **On one host**, the VSG60 and the N210 both transmitting, needs the
  N210 back on worklaptop1.

## Planned: a wider band

Not run yet. No radio here passes more than about 27 MHz (the BB60D at
40 MS/s; the VSG60 is flat to ±14.4 MHz at 40 MS/s; the N210's UBX-40 card
at 25 MS/s). Wider takes two radios side by side:

- **Receive:** the BB60D and the N210 on one machine (already shown to
  stream together), tuned to adjacent bands - for example the BB60D
  centred 13 MHz below a boundary and the N210 10 MHz above it - over a
  signal that crosses the boundary, then the two captures lined up in time
  and frequency and stitched. The open questions are lining them up (no
  shared clock or trigger: the N210's and the BB60D's frequency differ by
  about 0.2 ppm, and their start times by however long each takes to
  open) and the seam.
- **Transmit:** the VSG60 and the N210 on adjacent bands, one signal split
  across them.
- **The band:** 902-928 MHz is 26 MHz, about one radio's width. Over the
  air, anything wider leaves the ISM band, so a wider-band test either
  stays inside 26 MHz (two radios each covering half, to prove the
  stitching) or needs a cable and a pad - the decision is the user's.
