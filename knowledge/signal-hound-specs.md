# Signal Hound BB60D and VSG60A: what the manufacturer says

What the datasheet and manuals give for the two instruments here, the
transmitter being a VSG60A, set beside what has been measured on them.
[radios.md](radios.md) has how each is driven and what goes wrong; this is
the numbers. Read it before choosing a
sample rate, a bandwidth or a level.

Both tune to 6 GHz, and that is where the likeness ends: **6 GHz is the
tuning range, not the bandwidth.** What a capture or a waveform can hold at
one time is the instantaneous bandwidth, and it differs.

| | BB60D (receiver) | VSG60A (transmitter) |
|---|---|---|
| Tuning range | 9 kHz to 6 GHz | 50 MHz to 6 GHz specified, usable from 30 MHz |
| Instantaneous bandwidth | **27 MHz** of calibrated I/Q, amplitude corrected | **40 MHz** modulation bandwidth |
| I/Q sample rate | 40 MS/s, then 20, 10, 5, 2.5 and on down in halves | 12.5 kS/s to 51.2 MS/s, any value |
| Streaming | 80 MS/s of IF over USB 3.0 at 140 MB/s | the host streams I/Q to it; buffer size is not limited by the device |
| Level | input +10 dBm reference at most; **damage above +20 dBm peak** | −55 to +7 dBm specified, up to +10 dBm less the waveform's peak-to-average; **reverse power damage at +20 dBm** |
| Internal timebase | ±1 ppm a year | initial ±1 ppm, ±1 ppm a year; adjusted, better than 0.1 ppm |
| 10 MHz reference | input, **SMA**, a clean sine or square wave above 0 dBm (+13 dBm sine or 3.3 V CMOS recommended) | input, **BNC**, 0 to +13 dBm (down to −15 dBm works, with more phase noise) |
| Other connectors | rear SMA for a 1 PPS trigger, a 3.3 V logic output, or a **10 MHz output** | BNC trigger output, 3.3 V logic |
| RF connector | SMA, 50 Ω | SMA (female) |

The transmitter is a **VSG60A**: the label on the unit says so, and the
USB name it enumerates under is a plain "VSG". Its figures here are from the
VSG60A / VSG200 manual, which is the document for that model. `vsg_sink`
clamps it to 30 MHz to 6 GHz, 12.5 kS/s to 50 MS/s and −120 to +10 dBm, which
fits the manual: 30 MHz is the usable range, 50 MHz the specified one, and
51.2 MS/s the top rate. Elsewhere in the notes it is called the VSG60.
The BB60D datasheet is marked **preliminary** (20 May 2022).

## What matters for a wide capture or waveform

- **The BB60D's 27 MHz is at 40 MS/s only.** The manual gives the
  maximum and not a table by rate. Measured here: flat to ±8.5 MHz and gone
  by ±9.0 at 20 MS/s ([ntsc.md](ntsc.md#ntsc-receiver)), and an 8 MHz filter
  at 10 MS/s ([radios.md](radios.md#signal-hound-bb60d-as-a-receiver)). So
  at 20 MS/s a signal has about 17 MHz; the stage 2 runs, one channel,
  were comfortably inside it.
- **The VSG60 keeps flat to 72 % of its sample rate.** At 50 MS/s its
  digital filters start to roll off just past ±18 MHz and are 1 dB down by
  ±20 MHz; below 25 MS/s the API up-samples, with no penalty to 80 %. Past
  that, spurs appear. A modulation flatness of ±0.5 dB over 20 MHz and
  ±2.0 dB over 40 MHz is specified at a 1 GHz carrier.
- **The carrier feedthrough is −40 dBc** (CW, 0 dBm), a fixed tone at the
  centre frequency of the waveform. Over 20 MHz of bandwidth the
  image response is below −40 dBc.
- **Harmonics are below −30 dBc**, so a 2.4 GHz test is not clean at 4.8 GHz
  and 7.2 GHz, and the VSG60 is not certified for 2.4 GHz
  ([bluetooth.md](bluetooth.md#the-bench)).
- **The LO can hop in 200 µs** on a queued list, 80 ms when software
  controlled. A Bluetooth slot is 625 µs, so retuning per slot is not what
  stage 3 does: it shifts in baseband and keeps the LO still.
- **Recording more than 8 MHz of bandwidth wants a fast disk.** The BB60D
  datasheet asks for 250 MB/s sustained writing. At 40 MS/s a cf32 file is
  320 MB/s, so a 30 s capture is 9.6 GB. The 20 MS/s files here were
  160 MB/s.

## The clocks

Both instruments' own timebases are specified at ±1 ppm, and the stage 2
runs gave **−0.36 to −0.38 ppm** between them, which is what two such
timebases should do. That is a free-running pair. The two instruments can
share one clock: the BB60D's rear SMA can output 10 MHz, and the VSG60 takes
10 MHz on a BNC. There is no cable here for it (SMA to BNC), and stage 2
was run free on purpose, because bluey wanted a real clock offset. A
shared clock would give a zero-ppm control. Whether `bb60_source` can switch
the rear connector to a 10 MHz output has not been looked at.

## What it means for stage 3

This is inference from the numbers above, not a measurement.

- **A 20-channel map is 20 MHz wide**, about 19 MHz between its outer centre
  frequencies and a megahertz more for the signal at each end. Centred in the
  BB60D's 27 MHz window at 40 MS/s it has 3 to 4 MHz to spare at each side. At
  20 MS/s it does not fit: there is about 17 MHz. A map of 25 or 26 channels
  would sit at the edge of the BB60D's 27 MHz.
- **The VSG60 should run at 40 MS/s too**, to match the receiver: 72 %
  of 40 is 28.8 MHz, enough for 20 MHz, and a 625 µs slot is 25,000
  samples, a whole number. At 25 MS/s only 80 % of the rate, 20 MHz, is clean,
  which leaves no margin.
- **The carrier feedthrough lands in the map**, since the centre frequency
  has to be inside the window. Put it between two channels, not on one: that
  keeps it off a channel centre, and it is still a fixed tone at −40 dBc
  that bluey's receiver will see in every hop near it.

## Sources

- BB60D Real-Time Spectrum Analyzer datasheet, 20 May 2022, marked
  preliminary: <https://signalhound.com/sigdownloads/datasheets/BB60D_Preliminary_Datasheet_220601.pdf>
- BB60D user manual: <https://signalhound.com/sigdownloads/BB60C/BB60D-User-Manual.pdf>
  (front and rear panel, +20 dBm damage level, 27 MHz of usable IF)
- VSG60A / VSG200 product manual: <https://signalhound.com/sigdownloads/VSG60/VSG-Product-Manual.pdf>
  (bandwidth and flatness, timebase, the external reference, specifications)
- VSG60A product page: <https://signalhound.com/products/vsg60a-6-ghz-vector-signal-generator/>
