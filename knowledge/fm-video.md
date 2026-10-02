# FM video: the transmitter and the receiver

One of the notes in `knowledge/`. What applies everywhere, and which
file covers what, is in [CLAUDE.md](../CLAUDE.md).

## FM Video Transmitter

`fmVideoXmitter.py` frequency-modulates a carrier with composite video and
puts the sound on FM subcarriers above the picture, in the same baseband.
That is what an analog FPV drone sends on 5.8 GHz, and what analog
microwave relay and satellite links sent. It replaced the AM video
transmitter, which matched nothing a real transmitter sends.

**One app, with a Standard pull-down.** FPV and a microwave relay are the
same transmitter with different numbers, so the dialog's first choice fills
in the rest - channel plan, deviation, pre-emphasis - and leaves them
editable, so an FPV transmitter that turns out to deviate differently from
the datasheet's hint can be matched without touching code. The numbers live
in `apps/fm_video_core.py`, free of GNU Radio and Qt:

| | FPV drone (RTC6705) | Microwave relay / satellite (ITU-R F.405) |
|---|---|---|
| Deviation | 7.93 MHz p-p for 1 V, at every frequency - **measured off air**, not a datasheet figure | 8 MHz p-p for 1 V at the curve's crossover - 0.7616 MHz for 525 lines, 1.512 for 625 - which is 2.53 or 2.255 MHz at low frequencies |
| Picture pre-emphasis | none | F.405's shelf for the picture's line count: 525 lines, zero at 187 kHz, pole at 875 kHz, 10 dB down at DC; 625 lines, zero at 313 kHz, pole at 1.565 MHz, 11 dB down |
| Sound | FM subcarriers at 6.0 MHz (left) and 6.5 MHz (right), -27.5 dBc, +-25 kHz, 12 kHz corner | one at 6.8 MHz, -20 dBc, +-50 kHz, 75 us |
| Channels | 40, bands A, B, E, F and R | a typed frequency |
| 99% bandwidth, colour bars | 10.8 MHz in NTSC, 12.1 in PAL | 14.7 MHz in NTSC, 14.6 in PAL |

**And a Format pull-down: NTSC or PAL.** FPV cameras and goggles do either,
and a transmitter sends whatever its camera gives it. The format picks the
encoder's standard, the picture's size and rate (640x480 at 29.97, 768x576
at 25), the rate the picture is encoded at, and which of F.405's curves
applies; changing the format moves an F.405 choice onto the curve for the
new line count. The standard itself is described under [PAL](ntsc.md#pal-625-lines).

The sources are in `docs/`: `RTC6705-DST-001.pdf` and `RTC6715-DST-001.pdf`
(the transmitter and receiver chips nearly all analog FPV gear is built on)
and `R-REC-F.405-1-197007-W.pdf`. Two of the numbers are this project's
choices rather than a document's, and are worth measuring against real
equipment before they are trusted:

- **Neither RichWave datasheet gives the video deviation or any video
  pre-emphasis**, so both were this project's guesses until 2026-09-16,
  when a real FPV transmitter was measured on A3 (5825 MHz) into the BB60D
  with the FM video receiver's own readout - see its section for the whole
  set. The deviation was **7.93 MHz p-p** over 237 frames (spread
  7.89-7.96), not the 5.0 the datasheets imply: +-2.5 MHz is the RTC6715's
  *sensitivity test condition*, the only video deviation either mentions,
  and it under-deviates a real link by 4 dB. The profile carries the
  measured figure now, and the dialog's Deviation box is editable because
  one unit is one unit. The pre-emphasis guess was right: the colour burst
  came back +0.14 dB against DC (spread 0.08-0.18), so whatever R/C network
  that module has does nothing measurable at the colour subcarrier.
- **F.405 says nothing about sound.** One subcarrier at 6.8 MHz with 75 us
  is what analog C-band satellite channels commonly carried; its level and
  deviation are chosen.

```sh
python scripts/test_fm_video_transmit.py                  # both standards, no radio
python scripts/test_fm_video_transmit.py --video clip.mp4 # ... picture and sound
```

Things worth knowing before changing it:

- **A sampled FM modulator over-deviates the top of the baseband.**
  `frequency_modulator_fc` sums phase a sample at a time, and a running sum
  is not an integral: for a tone of w radians per sample it has
  w/(2 sin(w/2)) times the gain - +0.64 dB at 4.2 MHz of 20 MS/s, +1.72 dB
  at 6.8 MHz. A spectrum analyser, or a real goggle's discriminator, sees
  the true swing, so the subcarriers' sidebands read 0.5-1.3 dB high until
  the transmitter took it out: an FIR on the picture
  (`integrator_compensation_taps`), an exact correction in each
  subcarrier's level. Afterwards they measure -27.50 and -20.00 dBc, as
  specified. A receiver that differences phase reads the same amount
  *low*, which `discriminator_compensation_taps` puts back.
- **The subcarriers are added after the pre-emphasis.** F.405's curve is for
  the picture.
- **The picture is encoded slower than the radio runs and interpolated up.**
  The encoder cannot keep up at 20 MS/s. NTSC is encoded at 10 MS/s and
  interpolated by 2. PAL cannot be: its chroma sidebands reach 5.7 MHz,
  which at 10 MS/s would fold back onto the chroma itself, so it is encoded
  at 12.5 and interpolated by 8/5 (`VIDEO_RATES`). The interpolator's
  passband is 0.45 of the video rate (`INTERPOLATOR_BW`); GNU Radio's
  default 0.4 is flat only to 4 MHz at NTSC's rate.
- **20 MS/s for every radio**, the HackRF's ceiling, and both standards fit
  inside it in both formats. The USRP here has a WBX, which stops at
  2.2 GHz, so it cannot reach 5.8 GHz at all.
- **F.405's networks are bilinear transforms whose one free constant is
  searched for.** The transform keeps a network's gain at DC and at the top
  exactly and moves the frequency axis in between. With the textbook
  constant the 525-line curve came out 0.048 dB off at 4.2 MHz, 35% of the
  recommendation's tolerance - but the 625-line curve's pole is nearly twice
  as high, and it came out 0.145 dB off at 5 MHz, 101%, just outside.
  Pre-warping the zero and pole, the other textbook answer, doubled both.
  So `coefficients` tries the constants that match the curve exactly at
  each of 160 frequencies and keeps whichever lands the worst point furthest
  inside the tolerance: 32% for 525 lines, 90% for 625.
  `iir_filter_ffd(b, a, False)` reads the taps the way scipy writes them,
  checked to 4e-7.
- **FM has a threshold, and the test shows it.** With noise added to the FPV
  signal, every dB of carrier is a dB of picture down to about 12 dB
  carrier-to-noise in 20 MHz - 46.8 dB of picture at 30, 36.8 at 20, 28.7
  at 12 - with no clicks at all from 15 dB up. Below that the damage
  arrives as clicks, the sparkles of a weak FM picture: 88 a frame at
  10 dB, 708 at 8, 3,937 at 6, 13,608 at 4. An AM picture fades into snow
  instead. (Those picture figures are 4 dB better than they were before the
  deviation was measured, which is the whole of FM's trade in one line: the
  datasheet's 5 MHz p-p was throwing 4 dB away.)

Four things about measuring it cost time, and none of them was in the
transmitter:

- **Judge the link by waveform, not by decoded colour.** Decoded colour
  measures the decoder as well as the link, and at the time the decoder's
  colour depended on which sample a buffer started on - a bug, since fixed
  (see [NTSC composite video](ntsc.md#ntsc-composite-video)). It read as the FM
  chain being 0.168 wrong for both standards, to six figures - identical for
  two different modulations, which is what gave it away.
- **Compare against the composite through the same video filters, not the
  untouched original.** The encoder's bars switch in a single sample and
  carry energy up to the video rate's Nyquist that no real video path
  passes; against the original that alone reads 21 dB. Against the
  filters-only reference the FM chain is 118-136 dB transparent in NTSC and
  83-88 dB in PAL, whose 8/5 resampling is the difference.
- **Find the alignment on the luma, and not too far away.** Colour bars
  repeat every line and their chroma every other frame or so, so a match a
  whole line away scores nearly as well as the right one - and lands the
  chroma upside down. And at PAL's 12.5 MS/s the 4.43 MHz chroma is 2.8
  samples a cycle, so a full-band correlation swings from 0.30 to 0.94
  between neighbouring lags while the true delay, through 8/5 resampling, is
  a fraction of a sample no whole lag lands on: the best whole lag was two
  lines away, and PAL bars read 24 dB where they are 83. The lag is found on
  a copy low-passed to 1.5 MHz, whose peak is broad; searched only 200
  samples either way in software; tried three lines either side off the
  air, keeping the smallest residual; and the fraction is fitted from the
  phase below 3 MHz.
- **The median of the swing is not the carrier's offset.** Colour bars spend
  their time a little above the rest frequency and read as 80 kHz, 13.7 ppm,
  off. Sync tip and blanking are the levels the standard fixes, so the
  off-air analysis slices at its own sync-to-blanking step and reads both
  off the pulses - which the decoder itself now does too.

**Verified off air**, the VSG60 on TVAdemo transmitting on FPV channel F4
(5800 MHz) at -9.5 dBm into the BB60D here at 60% gain and 20 MS/s, the
app's own flowgraph run headless. A sweep of 5645-5945 MHz beforehand found
WiFi at 5742-5763 MHz and nothing else.

| | FPV, colour bars | FPV, Chevrolet clip with sound | F.405, colour bars |
|---|---|---|---|
| Carrier-to-noise in 20 MHz | 18.6 dB | 18.7 dB | 17.6 dB |
| Clicks | none in 90 frames | none | none |
| Frames decoded | 51 of 51 | 51 of 51 | 51 of 51 |
| Line rate | 15734.2-15734.3 Hz | the same | the same |
| Sync-to-blanking step, against sent | -2.9% | -0.4% | -0.6% |
| Picture, p-p over rms in 4.2 MHz | 29.0 dB | - | 35.4 dB |
| Sound | - | both subcarriers the clip's own (0.992, 0.991); sidebands -27.5 and -27.7 dBc | - |

The carrier sat 0.1-0.3 ppm from where it was tuned, both radios together,
which is the BB60D's own reference as before. At the same power F.405's
larger deviation and its pre-emphasis bought about 7 dB more picture than
FPV for 1.6 times the bandwidth, which is FM's whole trade in one line.
TVAdemo ran the transmitter on 4.3 cores with bars and 5.0 with the clip,
repeating a frame only while starting, and the no-radio test there held
the radio's 20 MS/s exactly with no repeats.

**And PAL**, the next morning over the same link at the same power, both
runs through the app's own flowgraph on its PAL format:

| | FPV, Chevrolet clip with sound | F.405 (625-line curve), colour bars |
|---|---|---|
| Carrier-to-noise in 20 MHz | 25.6 dB | 19.6 dB |
| Clicks | none in 75 frames | none |
| Frames decoded | 43 of 43 | 43 of 43 |
| Line rate | 15625.0 Hz | 15625.0 Hz |
| Sync-to-blanking step, against sent | +0.5% | +1.1% |
| Picture, p-p over rms across 5 MHz | - | 38.2 dB, colour bars within 0.036 |
| Sound | both subcarriers the clip's own (0.998); sidebands -27.5 and -27.6 dBc | - |

TVAdemo ran the PAL transmitter on 5.5 cores with the clip and 4.2 with
bars, repeating frames only while starting, and stopped it cleanly each
time; the no-radio test there held both formats at exactly the radio's rate.
The first analysis of the colour-bar capture failed in the alignment rather
than the link: off the air a 5 ms segment sits anywhere in a reference
several frames long, so its lag is far bigger than the segment, and a guard
written for software - skip any lag bigger than half the shorter signal -
skipped them all. It checks how much the two overlap now.

## FM Video Receiver

`fmVideoReceiver.py` is the other end of `fmVideoXmitter.py` and the other
side of its tile. It discriminates the carrier, takes the pre-emphasis back
out, decodes the composite into pictures and demodulates the sound off the
subcarriers riding above them. **Watch** hands the decoded pictures to
`ffplay` (or `mpv`), as the ATSC and NTSC receivers do; there is no
transport stream to pass on, so what goes across the pipe is raw frames.

**It is also a measuring instrument, which is why it was worth building
before the real FPV hardware arrived.** The FPV profile's 5 MHz deviation
and its "no pre-emphasis" are this project's guesses - neither RichWave
datasheet gives either - so a receiver pointed at a real FPV transmitter has
to be able to say what that transmitter actually does rather than assume the
guesses and show a picture that is quietly wrong. Everything under
**Measured** is read against levels the television standard fixes, so none
of it needs a test signal or any knowledge of what is being televised:

- **Video Deviation**, from the step between sync tip and blanking. That
  step is 40 IRE of 140, or 300 mV of 1000, whatever the picture is doing,
  so how big it comes back says how far the transmitter really swings the
  carrier for a volt. It does not depend on the pre-emphasis: both are flat
  parts of the signal, so the curve and its inverse cancel on them exactly.
- **Carrier Offset**, from where the sync tip landed, which is half the
  swing below the carrier.
- **Response at Subcarrier**, from the colour burst. Both standards make the
  burst exactly as big peak-to-peak as the sync-to-blanking step, so one is
  the path's gain at 3.58 or 4.43 MHz and the other its gain at DC: their
  ratio is the frequency response of everything in between, with no
  reference signal at all. 1.0 is flat and anything else is a lift that has
  not been undone - which is how an FPV module's own undocumented R/C
  pre-emphasis can be read off the air.

```sh
python scripts/test_fm_video_receive.py          # both standards, both formats
python scripts/test_fm_video_receive.py --quick  # FPV in NTSC, no sound
```

drives the app's own receiving blocks against the app's own transmitting
ones with no radio, and concentrates on those measurements rather than on
the picture alone.

**Getting the deviation wrong does not cost the picture. Getting the
pre-emphasis wrong does.** `CompositeDecoder` reads sync and blanking off
the signal and scales by the step between them, so a deviation setting that
is out by a factor simply rescales what it is handed - told half the real
deviation it decodes colour bars to the same 0.017 worst error. That is what
makes the instrument usable against a transmitter whose numbers nobody
knows. Pre-emphasis is the one that does not forgive: with F.405's curve on
the air and none taken out, the low frequencies arrive 10 dB down, which is
the sync pulses, and the picture will not decode at all.

**Verified off air**, the VSG60 on TVAdemo transmitting FPV colour bars in
NTSC on channel F4 (5800 MHz) at -9.5 dBm into the BB60D here at 60% and
20 MS/s, the app's own flowgraph run headless:

| | told the truth (5 MHz) | told 5 MHz, sending 8 |
|---|---|---|
| Status | Locked - picture decoding | Locked - picture decoding |
| Frames | 17.7-17.8 a second of 17.6, **0 dropped, 0 failed** | 17.8 a second, 0 dropped |
| BB60D overflows | 0 in 30 s | 0 |
| Carrier to noise | 23.2 dB in 20 MHz | 22.4 dB |
| Line rate | 15,734.3 Hz (0 to +2 ppm) | 15,734.2 Hz (-2 ppm) |
| **Video deviation** | **4.94-4.97 MHz p-p** of 5.00 sent | **8.01 MHz p-p** of 8.00 sent |
| **Carrier offset** | - | **+9 kHz (+1.5 ppm)** |
| **Response at 3.58 MHz** | -0.00 to +0.07 dB | -0.09 dB |
| Sound subcarriers | -27.7 to -28.2 dBc of -27.5 sent | - |

The second column is the one that matters: the receiver was told the FPV
profile's 5 MHz and the transmitter was swinging 8, and it read **8.01** -
which is the job it will have to do against a real FPV transmitter. Set to
PAL with NTSC on the air it said "This is NTSC - reopen with Format set to
NTSC", which is the other thing nothing on the air announces.

**And then against the real thing.** On 2026-09-16 an actual analog FPV
transmitter and camera were put on the bench, on channel A3 (5825 MHz),
into the BB60D at 60%. A sweep of 5645-5945 MHz found it first - centre of
mass 5824.83 MHz, 99% bandwidth 8.03 MHz, 24.2 dB carrier-to-noise in
20 MHz, no BB60D overflows - and then the receiver locked and decoded a
colour picture sharp enough to read a serial number off an instrument's
front panel. Over 60 seconds and 237 measured frames, with 1,059 pictures
decoded at 17.7 a second, **0 dropped, 0 failed and 0 overflows**:

| | the profile's guess | what it actually does |
|---|---|---|
| Video deviation | 5.0 MHz p-p | **7.93 MHz p-p**, spread 7.89-7.96 |
| Video pre-emphasis | none | **none** - burst +0.14 dB against DC, spread 0.08-0.18 |
| Sound subcarriers | 6.0 and 6.5 MHz at -27.5 dBc | **none sent at all** |
| Format | unknown until measured | **NTSC**, 15,734.52 Hz, +16.4 ppm |

Three things that came out of it:

- **The deviation was the guess that was wrong, and by 59%.** `FPV` in
  `fm_video_core` carries 7.93 MHz now. It is worth 4 dB of picture: the
  threshold table above moved from 42.8 dB at 30 dB carrier-to-noise to
  46.8, because the datasheet's figure was throwing that away.
- **The pre-emphasis guess was right**, and the burst reading is what says
  so - +0.14 dB with a spread of a tenth of a decibel, on a transmitter
  nobody has documented.
- **The receiver reported noise as sound, and that is now fixed.** The
  6.0 and 6.5 MHz chains read **-51.9 dBc and 30 kHz rms** - which reads as
  a subcarrier carrying loud programme - when the baseband above 4.25 MHz
  was in fact flat noise at -39.5 dB with no structure at either frequency.
  Both numbers were the empty band. A subcarrier weaker than
  `NO_SUBCARRIER_DBC` (-45 dBc) is reported as "nothing here" now and its
  deviation is not shown at all; and the "silent" threshold on one that
  *is* there went from 100 Hz to 2 kHz, because a real subcarrier 27 dB
  down measures 1.0-1.2 kHz rms of the demodulator's own noise on a 23 dB
  link with silence going out, so 100 Hz would never once have fired.

Its carrier sat **-399 kHz** from where it was tuned and wandered between
-222 and -521 kHz over the minute, which is a cheap VTX warming up and is
well outside anything the measurement's own precision could invent.

Six things worth knowing before changing it, five of them mistakes this made
first:

- **There is no local-oscillator offset, unlike the NTSC receiver.** That
  one tunes 6 MHz above its channel so the radio's own leakage falls outside
  a 6 MHz channel in a 20 MHz window. FM video has no such room: the signal
  is 9 to 15 MHz wide and the radio's 20 MS/s is all of it, so the carrier
  sits in the middle and the radio's DC lands on it. The BB60D centres its
  own IQ; a radio that does not would show it as a periodic distortion of
  the picture rather than as a spike in the spectrum.
- **The picture filter must not depend on the profile, because it is part of
  the instrument.** It has to be flat over the picture and *gone* by the
  lowest sound subcarrier - decimating to NTSC's 10 MS/s folds 6.0 MHz onto
  4.0, straight into the chroma, and PAL's own band reaches 5.0 MHz so there
  are 400 kHz to stop in. Letting the transition widen when the sound sat
  higher (F.405's is at 6.8) is cheaper and looks harmless: 21 taps instead
  of 35, still 34 dB down where the sound is. But a windowed sinc with a
  wide transition droops long before it, and that one read **0.68 dB low at
  the colour subcarrier** - so the receiver would have reported most of a
  decibel of its own filter as the transmitter's pre-emphasis. Fixed at
  6.0 MHz for every profile it is 0.008 dB. An FFT filter makes the sharp
  version affordable: 12x real time at 20 MS/s against a plain FIR's 8x.
- **The burst has to be windowed before it is averaged.** Mixing it down and
  taking the mean leaves a term at twice the subcarrier which only cancels
  over a whole number of cycles, and seven cycles of NTSC's subcarrier is
  19.56 samples at 10 MS/s. Rounded to 20, the leftover read a *perfect*
  loopback as 0.974 - a 0.23 dB lift that is not there. A Hann window puts
  it back to 1.0000 at 10, 12.5 and 20 MS/s in both standards.
- **The carrier offset has to be measured against the deviation just found,
  not the one the receiver was told.** The sync tip sits half the swing
  below the carrier, so its position carries both; taking the set deviation
  for the real one books the difference as tuning error. Off air against the
  transmitter swinging 8 MHz while the receiver expected 5, that read the
  carrier as **1.5 MHz off** when it was within a couple of kilohertz. Its
  precision follows the deviation's, though - 1% of a 5 MHz swing is
  25 kHz - so read it in tens of kilohertz, not in parts per million.
- **A click threshold has to be one a discriminator can actually reach.**
  Differencing phase cannot read further than half the sample rate, 10 MHz
  at 20 MS/s. Quoting the threshold against the *peak-to-peak* deviation
  rather than the peak puts it at 10.1 MHz for FPV, beyond that ceiling: the
  count then reads zero however badly the link is breaking up, and an empty
  channel and a perfect one look alike. Measured, that read **0 clicks a
  frame at 6 dB carrier-to-noise** where there should have been thousands.
  `click_threshold_hz` in `fm_video_core` is now the one definition, shared
  with the transmitter's own test, and the margin is 3 MHz where there is
  room and halfway to the ceiling where there is not - F.405 swings 5.4 of
  the 10 available and needs the second. With it right, 3,622 clicks a frame
  at 6 dB, against the 3,588 the transmit test measures independently.
- **The link statistics must not be a Python block at the radio's rate.**
  Carrier-to-noise and clicks were one `gr.sync_block` reading the RF and
  the discriminator at 20 MS/s. It worked, and it held the interpreter lock
  that the frame decoder - also Python, on its own thread - needs to get a
  picture out. Measured at the radio's own pace: without it, every frame the
  buffer allows and none dropped; with it, **7 buffers of 70 dropped in NTSC
  and 13 of 58 in PAL**. Squaring the envelope, keeping one sample in eight,
  rectifying and comparing are C++ blocks now, and what reaches Python is
  three streams at 100 S/s. Flat out that took the chain from 1.21x real
  time to 3.01x.

Carrier-to-noise is the number FM lives by, because FM has a threshold, and
it comes from the second and fourth moments of the envelope - for a carrier
of power `c` in complex noise, `m4 = 2 m2^2 - c^2`. Against known noise it
reads within 0.01 dB at 30, 20, 12 and 6 dB. **No carrier at all is not a
low carrier-to-noise**: pure noise satisfies `m4 = 2 m2^2` exactly, so the
carrier term comes out zero rather than small and there is no ratio to
quote. Every sample of noise is also a phase jump, so the click count on an
empty channel is the sample rate - 190,012 a frame, measured - which is
arithmetic rather than information. The readout says "no carrier" and leaves
the clicks blank instead.

The window shows the **RF spectrum** on the transmitter's own span and
centre, so the two can be read side by side; the **recovered baseband**,
which the transmitter has no equivalent of and which is where an unknown
transmitter's sound subcarriers appear at whatever frequency they really
are; and the **composite** itself. At the radio's rate with no displays the
chain costs 2.6 cores of the eight in NTSC and 3.4 in PAL, and decodes every
frame the buffer allows.

**The frame decoder is shared with the NTSC receiver**, as
`CompositeFrameSink` - the same threaded decode, drop-rather-than-fall-
behind buffer and non-blocking player pipe, with the video standard as a
parameter so it does PAL too. Its `measure()` hook is where the numbers
above are taken, with the sync pulses already found.
