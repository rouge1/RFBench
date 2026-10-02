# NTSC and PAL: composite video, the NTSC transmitter and receiver

One of the notes in `knowledge/`. What applies everywhere, and which
file covers what, is in [CLAUDE.md](../CLAUDE.md).

## NTSC composite video

`apps/ntsc_encode.py` builds a 2:1 interlaced composite signal - sync,
blanking, equalizing pulses, serrations, colour burst and a
quadrature-modulated chroma subcarrier - and `apps/ntsc_decode.py` takes it
apart again: NTSC to SMPTE 170M-2004, and PAL to ITU-R BT.1700 Part B (see
[PAL](#pal-625-lines) below; the modules kept their names). Both are free of
GNU Radio and Qt, like the RDS pair, so they can be checked with no radio at
all:

```sh
python scripts/test_ntsc_loopback.py       # encoder -> decoder, no radio
python scripts/test_pal_loopback.py        # ... the same for PAL
python scripts/test_ntsc_transmit.py       # ... and through the modulator
python scripts/test_ntsc_transmit.py --video clip.mp4   # picture and sound
```

With `--video` the transmit test also checks the clip's soundtrack all the
way onto the aural carrier and back, and that the source keeps up when it is
paced at the rate a radio consumes.

All seven colour bars return with a worst error around 1e-11 and a full-scale
grey ramp within 0.002 - it was 0.0089 and 0.0092 until blanking started
coming off the back porch (below). `docs/S170m-2004.pdf` holds the timing
tables (2 and 3), the levels (table 1) and the encoding equations (annex A).

- **Sync and blanking come off the pulses, not off how the samples are
  distributed.** `levels()` makes a first guess and `decode_frame` refines
  it: blanking on the breezeway after each line sync, which is where a
  television clamps, and the sync tip in the middle of the pulses. The first
  guess has failed twice, both times silently.
  - It was the histogram's commonest level inside the 10-60% band of
    sync-to-white. On a dark scene the top of that range collapses toward
    blanking, which then sat outside the window: `find_pulses` found **no
    pulses at all**, and off air 17 frames in a row failed every time a clip
    reached a dark shot.
  - Widened to 5-98% it held until there was noise. Then the porches spread
    across many bins and any large flat area spread the same way over more
    samples: with noise of 0.05 of the swing the commonest level on a white
    picture was 2.5 times the sync-to-blanking step too high, and on colour
    bars off the FM link it was the white bar. The slicer cut at blanking
    level, every "pulse" was a whole blanking interval, and the picture came
    out wrong **with no error raised**.
  - Now it is two percentiles. Sync tips are about 8% of all samples and
    blanking-level samples about the next 16%, whatever the picture does,
    so the 4th percentile sits in the tips and the 20th in blanking. That
    decoded every picture tried - white, black, grey, dark, 90% white,
    saturated red and blue, clean and noisy, at 10 MS/s and 4x subcarrier -
    and read real off-air signals within 0.96-1.09 of their back porch.
  - The refinement matters as much. A low percentile of noisy samples sits
    below the real tip by however far the noise reaches - the FM off-air
    captures read 0.02-0.05 low - where the middle of the pulses reads
    within 0.001, and the tip is half of what sets the picture's scale.
  - **The breezeway is counted from where each pulse began plus the sync
    width, not from where the slicer says it ended.** That end moves with
    noise: on saturated red and blue a window hung off it slid onto the
    edge and read blanking 0.011 low, against 0.0026. The front porch is
    the obvious alternative and is worse where it matters - through the
    vestigial-sideband transmitter the picture rings into it, and colour
    came back 0.102 out against the breezeway's 0.081.

  Getting blanking exactly right is also what made the loopback exact.
  Re-slicing with the refined levels is not worth the fifth of a decode it
  costs - measured, 883 pulses either way - so it only happens if the first
  guesses were out by more than half the span.
- **Chroma is taken out of luma before its envelope is turned to the
  burst.** Decoded colour used to depend on which sample a buffer happened
  to start on: colour bars straight from the encoder, never near a radio,
  came back anywhere from 0.017 to 0.20 out as the first sample moved, and
  0.59 out at a single pixel. To subtract chroma from luma the decoder
  rebuilds the chroma waveform from its complex envelope, and it did that
  *after* rotating the envelope to line up with the burst - so the rebuilt
  chroma was out of phase with the real one by exactly that rotation, which
  is set by where the buffer starts. Most of the chroma stayed in luma as a
  dot pattern that a 40-row average mostly hid. `test_ntsc_loopback.py`
  decoded from sample 0 at 4x subcarrier, the one start where the rotation
  is zero, so it never showed; off air, where a buffer starts anywhere, it
  was every frame. Fixed, twelve different starts all read the same, and
  the NTSC transmit test's colour error - which had been put down to the
  vestigial sideband - fell from 0.13 to 0.081. Both loopbacks now decode
  from twelve starts.

- **Every sample of a frame is computed at once.** The encoder used to walk
  the 525 lines in a Python loop, picking each line's samples out with
  `line_in_frame == L` - a comparison across the whole frame, 525 times over,
  making the work O(samples x lines). At 10 MS/s that ran at **0.14x real
  time**, so it could never have fed a radio live however fast the machine.
  Whole-frame array operations, computing sin/cos only where the subcarrier
  is actually used, and restricting the vertical-interval arithmetic to the
  eighteen lines it applies to took it to **1.66x** in colour and 2.89x in
  monochrome - twelve times quicker, and bit-for-bit identical output at
  every sample rate tried.
- **Saturated colour has to be legalised or it reaches down to sync level.**
  Chroma adds to luma, so 100% saturated bars swing from 131 IRE down past
  -20, and a receiver then cannot tell picture from sync at all: the decode
  comes back sheared into diagonal noise. Broadcasters run a legalizer for
  exactly this; `NtscEncoder(legalize=True)` is one, clamping active video
  to -20..+120 IRE. It is a no-op on 75% bars, which is why 75% is the
  standard test signal. Real 1950s material peaks around 0.94-1.01 against
  the 1.143 ceiling and never touches it.

### PAL, 625 lines

The same encoder and decoder make and read 625-line PAL to ITU-R BT.1700
Part B (`docs/1700-e.pdf`), which the FM video transmitter offers because
FPV cameras send either. What differs between the standards is a table,
`VideoStandard`: timing, levels, colour axes, and which half-lines of the
frame carry equalizing pulses, broad pulses or nothing - the only
structural difference. **NTSC's output is bit for bit what it was before
PAL existed**, at every rate, colour and legalizer setting tried, and it
encodes exactly as fast (32.3 ms a frame against 32.5).

- **The numbers**, Tables 1-3: lines at 15,625 Hz, subcarrier
  (1135/4 + 1/625) x fH = 4,433,618.75 Hz, a 64 us line with 12 us blanked,
  sync 4.7 us, a ten-cycle burst from 5.6 us; sync -300 mV, blanking 0,
  white 700 mV and no set-up; 576 active lines, sampled into 768x576 square
  pixels at 25 frames a second. Each field-sync block is five equalizing,
  five broad and five equalizing pulses, two and a half lines each. The
  first field's block begins half-way through line 623, so its broad pulses
  start exactly on line 1; the second's begins on 311. `test_pal_loopback.py`
  finds 610 line syncs, 20 equalizing and 10 broad pulses in a frame.
- **The V switch.** V, and the V half of the burst, change sign every line.
  The encoder takes the sign from the absolute line count's parity, which
  with an odd 625 lines repeats every two frames, as the standard's
  eight-field sequence needs. The decoder cannot know it in advance: the
  burst sits 45 degrees either side of -U, so four neighbouring lines'
  bursts average to the reference axis, and each line's own burst, measured
  against that, gives its sign. Four lines is 256 us, too short for two
  radios' clocks to turn the subcarrier measurably. Reading the switch
  backwards turns green magenta, which the test checks.
- **The burst is left off the field-sync lines only.** BT.1700 blanks it on
  a sequence that moves from field to field (Figs. 8 and 9) so that the
  first burst after field sync always has the same phase. Here it is off on
  the 16 lines with equalizing or broad pulses and on the other 609. A
  receiver locked to the line-by-line swing does not need the sequence, and
  a decoder that reads each line's own burst would lose colour on any active
  line the sequence blanked.
- **The legalizer's floor is -180 mV, not NTSC's scaled.** PAL's own 75%
  bars swing down to -175 mV in red and blue, where NTSC's bottom out at
  -16 IRE inside a -20 floor. A floor scaled from NTSC's (-140 mV) clipped
  them, and bars came back 0.032 out; -180 passes them and stays above the
  decoder's sync slice at -195.
- **It costs more to encode**: a 768x576 frame at 12.5 MS/s takes 50.9 ms
  of its 40 on one thread, 0.79x real time against NTSC's 1.03x, in
  proportion to its 1.5 times as many samples.
- **Every row lands on its row** whichever field a buffer opens on: a
  six-row band decodes onto exactly rows 300-305 from buffers starting a
  quarter, a half and four fifths of the way into a frame.
- **A PAL frame is a whole number of samples, so frame boundaries are worked
  in samples.** The encoder found where each frame ends in seconds - floor
  the start time over the frame period, step one, ceil back to samples -
  which is harmless while a frame is never a whole number of samples, and
  NTSC's never is. PAL's is: 500,000 at 12.5 MS/s, 800,000 at 20. Frames
  came out a sample long, then a sample short, and at frame 30, 1.2 s in, a
  quotient that should have been a whole number came out a hair under it:
  that frame ended where it began, and so did every frame after it. The
  source block took an empty frame as the one to replay when the encoder
  fell behind, and looped on it without producing a sample - so the
  transmitter went quiet after 1.2 s and **could not be stopped**:
  `tb.wait()` never returned, and the FM video test sat on TVAdemo for ten
  hours with three threads spinning. Boundaries now snap to a whole sample
  when they are within rounding of one, a frame is never empty, and the
  source refuses one if it ever is. PAL then runs at exactly the radio's
  rate, repeats nothing after warm-up, and stops at once; NTSC's stream is
  bit for bit unchanged. The loopbacks never saw it because they encode two
  or three frames and the fault needed thirty, so `test_pal_loopback.py`
  now walks 100,000 frame boundaries.

## NTSC video sources

`apps/ntsc_source.py` turns a *stream* of frames into a continuous signal,
and settles what video format this project takes: **whatever ffmpeg reads**.
There is no bespoke format. A video file is decoded by an ffmpeg subprocess,
scaled to 640x480 and letterboxed if it is not 4:3, looping forever; there
is also a built-in colour-bar pattern so the app works with an empty media
folder.

- **Frames are encoded on their own threads - more than one of them.**
  Encoding inside work() would stall the radio, so it happens behind a
  queue. One encoder thread is not enough, though: a frame at 10 MS/s takes
  26-32 ms against a 33.4 ms budget, measured on both machines, so a single
  thread runs at 1.03-1.21x real time *before* it shares the processor with
  the modulator, the resamplers and the radio sink. Off air it lost. numpy
  releases the interpreter lock inside the large array operations this
  encoder is made of, so plain threads scale - measured 1.03x, 1.62x, 2.16x
  and 2.49x on one, two, three and four - and `encode_workers()` uses three
  on anything with eight cores. Frames are independent given where each one
  starts, and `NtscEncoder.frame_bounds()` says that without encoding
  anything, so the next frame can be dispatched while this one is still
  being computed; the results go into the queue in order.
- **A late frame must never take the transmitter off the air.** work() used
  to wait a tenth of a second for a frame before giving up, which turned a
  slow encoder into a *silent carrier*: nothing came out of the block, so
  nothing reached the radio. Measured on the VSG60 with a real clip: **74
  gaps in three seconds, the longest 18.6 ms, 25% of the air time missing**
  - and the picture still decoded at 17.4 frames a second with zero
  failures, so nothing but the envelope showed it. This is the same trap as
  the [HackRF ATSC case](atsc.md#atsc-transmitter), and the same lesson: a spectrum average
  cannot see a transmitter that is off half the time. The wait is two
  milliseconds now, and what fills a gap is the *previous frame* rather
  than blanking - a frozen picture keeps sync pulses and colour burst
  coming, where blanking is a level a receiver loses lock on. The first
  frame is encoded before the block reports itself started, so the
  transmitter never opens with blanking either. `repeats` and `starved`
  count both, and `scripts/test_ntsc_transmit.py` requires zero of each in
  steady state at the radio's own rate.
- **The `.dat` captures do not go through it.** They are already composite,
  at 18 MS/s, so they are played by a file source and resampled 5/9 - see
  `dat_resample_ratio`.
- **A pipe handed to `file_descriptor_source` must be a duplicate, or the
  *second* launch of the app goes out silent.** That block closes the
  descriptor it was given in its destructor, and `AudioTrack.close` - which
  `close_sources` calls on closeEvent - closes it too. Two owners, one
  number, closed twice. The second close lands on whatever was opened in
  between, and what opens in between is the next run of the same app: its
  pipe is handed the lowest free descriptor, which is the one just
  released. So launch, close, launch again, and the moment Python collects
  the first run's flowgraph the second run's sound dies with
  `file_descriptor_source: error: [read]: Bad file descriptor`. Reported
  from TVAdemo against a clip, reproduced exactly, and both descriptors
  read 3 in the reproduction. `AudioTrack.descriptor()` and
  `TransportStream.descriptor()` hand out `os.dup` of it now, so each owner
  closes its own; the NTSC transmitter, the FM video transmitter and the
  ATSC transmitter all went through `fileno()` and all had it.
  `scripts/test_fm_video_transmit.py` runs the launch-close-launch sequence
  in the order that used to break.
- **One clip, one entry in the picker.** The media folder holds each clip
  twice - a `.mp4` for here and a `.ts` of the same picture and sound, which
  the ATSC transmitter plays without having to encode it - so offering every
  readable file made the video list forty items long with every title in it
  twice, spelled identically, and nothing on screen saying which was which.
  `video_files()` collapses files that share a name to one, keeping the
  extension earliest in `VIDEO_EXTENSIONS`: the `.mp4` is 640x480 with
  square pixels, which is exactly what the encoder wants, where the `.ts`
  is 704x480 at 10:11 and would have to be stretched back. The three kinds
  of source - the built-in pattern, the clips, the `.dat` stills - are
  separated in the list, because a clip plays and loops where a `.dat` is
  one frame held on screen. `dat_files()` also drops the
  `-946x486-18M0FS` from those names: it is the same for every one of them,
  so it says nothing and hides the subject.
- **Pixels are squared before the picture is fitted.** ffmpeg's
  `force_original_aspect_ratio` works on the stored width and height, not
  the shape on screen, so a `.ts` at 704x480 with 10:11 pixels - the ATSC
  test pattern, or anything the ATSC receiver records - played 9% too
  short, with 22 black rows top and bottom. `VideoFile` scales to `iw*sar`
  first, as `apps/atsc_source.py` does. Square-pixel clips come out
  bit-identical to before. Measured: 10:11 and DVD 8:9 material now fills
  the frame, DVD 16:9 letterboxes to rows 60-419 as it should, and a file
  with no aspect ratio recorded still plays.
- **The clips themselves are public domain or CC BY**, and
  `media/VIDEO-CREDITS.txt` is the record of what each one is, where it came
  from, which segment was taken and how it was encoded. The Blender films
  are CC BY, whose terms *require* that credit wherever the clip is shown or
  passed on. Fourteen clips: seven Prelinger advertising reels (four of them
  black and white), three NASA (Apollo 11 launch and moonwalk, ISS Earth
  views), four Blender open movies. All fourteen `.mp4` are exactly 640x480
  square-pixel 29.97 progressive, so nothing is rescaled on the way in.

- **Generate every sample from absolute time, never from a count per line.** A
  line is 63.5556 us, which is 635.56 samples at 10 MS/s and not a whole
  number at any rate worth transmitting at. Rounding per line accumulates
  until the picture shears, so each sample works out which line it belongs to
  from `n / sample_rate`. Nothing drifts and any sample rate works.
- **Take the subcarrier phase from that same absolute time.** There are
  exactly 227.5 subcarrier cycles per line (clause 11.2), so the burst phase
  inverts line to line and the four-field colour sequence repeats by itself.
  Tracking it per line is bookkeeping that can only go wrong.
- **Decode colour against the burst, never against a local oscillator.** The
  burst is nine cycles at a known phase sent on every line for exactly this
  purpose: it says where the subcarrier's zero crossing falls *on this line*.
  Decoding against a locally generated subcarrier gets the hue wrong by
  however much the sampling origin happened to differ.
- **Find sync by pulse width, not by level.** Equalizing pulses (2.3 us),
  horizontal sync (4.7 us) and the vertical serrations all sit at exactly the
  same level, so a threshold crossing says nothing about which is which. How
  long the signal stays down is what separates a field from a line.

The encoder's output has the same geometry as the instructor's captures: at
18 MS/s a frame is 600600 samples of 1144, which is exactly what the
`*-946x486-18M0FS.dat` files in the media folder contain (one still frame
each, sync tip 0.034, white 0.877). Those are the only known-good composite
video in the repo and are worth keeping as a reference.

## NTSC Transmitter

`ntscAnalogVideoRecorded.py` puts composite video on a 6 MHz television
channel as System M: vestigial-sideband AM for the picture, an FM aural
carrier 4.5 MHz above the visual one. `cf` is the channel *centre*, as in
the ATSC apps, which puts the visual carrier 1.25 MHz above the lower edge.

**The RF architecture was already right and needed nothing.** Four other
things were wrong, and each of them alone stopped it working:

- **It could only send single still frames.** The dialog offered `.dat`
  files and nothing else, and those are one frame each. It takes any video
  file ffmpeg reads now, via `apps/ntsc_source.py`.
- **It played 18 MS/s captures at 10.** The `*-18M0FS.dat` files are
  composite sampled at 18 MS/s. Played out untouched at the flowgraph's
  10 MS/s, every timing in them is wrong by 1.8: a line lasts 114.4 us
  instead of 63.5556, which is a line rate of **8741 Hz where NTSC needs
  15734.266**. No television could have locked to what this transmitted.
  They are resampled 5/9 now.
- **The polarity defaulted to the wrong one.** US television is negative
  modulation - sync at peak carrier. The flowgraph's two options were
  labelled "Normal" and "Inverted", which says nothing about which a TV
  here can show, and the dialog's unticked default selected *positive*. It
  also drove the carrier to **1.79** against a radio that clips at 1.0.
  The options now say "Negative (US)" and "Positive", and negative is the
  default.
- **The modulation depth was approximate.** It was `1 - 0.9*v` with nothing
  bounding it. `NtscModulator` maps composite onto the carrier properly:
  sync 100%, blanking 75%, peak white 12.5%, per FCC 73.682 - and landing
  blanking on exactly 75% by itself is the check that the composite scale
  and the RF scale agree. A rail stops the most saturated colour switching
  the carrier off. The aural carrier came down from 0.8 to 0.316, which is
  the 10% of peak visual *power* the FCC asks for; it had been 8 dB too
  loud, stealing headroom from the picture.
- **The sound came from a different file than the picture.** The aural
  carrier was fed by a `.wav` picked separately in the dialog, which was
  all that was possible when the only video the app could send was a single
  still frame. Once it could send clips, a 1950s car advertisement went out
  with an unrelated cartoon soundtrack over it. The clip's own track is the
  default now - a second ffmpeg on the same file, decoded to 48 kHz mono
  and handed to `blocks.file_descriptor_source` - and the two stay in step
  for free, since GNU Radio consumes exactly one audio sample per composite
  sample and both are paced by the radio's clock. The dialog still offers a
  `.wav`, and offers silence, because the built-in pattern and the `.dat`
  stills have no sound of their own; "From the video clip" greys out when
  the source is not one, and comes back when it is.
- **Clip audio has to be conditioned or the carrier is 10 dB
  under-deviated.** The clips are loudness-normalised to -24 LKFS (ATSC
  A/85), which is deliberately quiet: measured across all fourteen, peaks
  ran 0.30 to 1.11 and rms 0.038 to 0.088, where full deviation is |1.0|.
  A fixed gain cannot fix it, because the loudness is uniform and the crest
  factor is not - 8 dB puts speech where it belongs and clips the two
  music-heavy Blender films on 0.17% of their samples. `AUDIO_FILTER`
  compresses and then lifts, which gives rms 0.091-0.210 and peaks
  0.64-1.54 across the whole set, with the worst needing the rail on
  0.0017% of samples. ffmpeg's own `loudnorm` is the obvious tool and the
  wrong one: its dynamic mode looks three seconds ahead, and since the
  picture comes from a *separate* ffmpeg on the same file, that delay lands
  as three seconds of lip-sync error.

Two things worth knowing before changing it:

- **The two-stage video mixer is not a redundant one.** Video is mixed to
  -1.725 MHz, filtered, then moved the last -25 kHz. The filter does its
  work in the first frame: a low pass at 2.475 MHz there passes 0.75 MHz
  below the carrier and 4.2 MHz above, which is exactly the vestigial
  sideband and the full video bandwidth. Mixing straight to -1.75 would put
  both edges 25 kHz wrong.
- **Ringing on sync is worse in negative modulation, and that is normal.**
  The sync pulse is the peak of the carrier there - the fastest, largest
  excursion in the signal - so the vestigial filter overshoots about 20% on
  it. What matters is the level reaching the radio, which after scaling the
  sum of vision and sound is 0.77 of full scale.

`scripts/test_ntsc_transmit.py` puts a picture through the modulator and
detects it the way a television does. **Include the Nyquist slope when you
do that**: a receiver's IF is at half response on the visual carrier, rising
to full 0.75 MHz above and falling to nothing 0.75 MHz below. Below that,
both sidebands survive and add; above it only one does. Rectifying without
the slope gives luma at twice chroma's strength - whites too bright, colours
washed toward grey - which reads as a broken modulator and is not one. It
took the measured colour error from 0.43 to 0.13 - and much of what was
left was the decoder, not the vestigial sideband: with its chroma
separation fixed (see [NTSC composite video](#ntsc-composite-video)) the
same test reads 0.081, and its tolerance came down from 0.15 to 0.10.

**Verified with real material**: 1950s Prelinger advertising and the Blender
open movies through the whole chain and back, **0.00002-0.0099 mean error**
(it was 0.078-0.088 until blanking came off the back porch and fixed the
IRE scale), the black-and-white spots clean and the colour ones looking
convincingly like period colour television.

**Verified off air, picture and sound**, the VSG60 transmitting
`Prelinger-Chevrolet-1955-Heres-Looking.mp4` from TVAdemo into the BB60D
here on RF channel 24 (533 MHz) at -9.5 dBm:

- **1,049 frames decoded, none dropped**, 17.4-17.9 a second against the
  17.6 the receiver's buffer allows, line rate 15,734.2-15,734.4 Hz against
  the 15,734.266 the standard specifies - a few parts per million, which is
  the NTSC equivalent of the ATSC pilot offset.
- **The carrier was present 100.00% of the time**, no gap at all, against
  25% of the air time missing before the encoder was given more threads.
- **The sound is the clip's own**: the aural carrier demodulated and
  cross-correlated against the same clip decoded locally scores **0.999**
  over the matching window, with the next-best alignment at 10% of that.
  Peak deviation 17.1 kHz of the 25 kHz System M allows, envelope
  std/mean 0.005 - a clean FM carrier.
The first run of this also found a fault in the *receiver*, which is worth
recording because of how it looked. About seventeen frames in a row failed,
once a run, always with the same exception - "could not find two fields - no
vertical sync?" - and the input level never moved, there were no BB60D
overflows and no dropped buffers. It looked like the receiver settling; it
was nothing of the kind. The failures were **exactly one buffer period
apart, seventeen of them, spanning one second**, and they landed at a
different time in every run. Transmitting the built-in colour bars for 150
seconds instead gave 2,616 frames and not one failure, which placed it in
the *picture*: the clip fades through a dark shot about once per 106-second
loop, and on a dark picture the blanking estimate collapsed onto the sync
tip. See the back-porch note under [NTSC composite
video](#ntsc-composite-video); `scripts/test_ntsc_loopback.py` now
reproduces it with no radio at all. Afterwards the same link ran **3,498
frames with none failed, none dropped and no overflows** over 200 seconds,
which is nearly two full passes of the clip and of its dark shot, and 4,183
frames with none failed over four minutes before that.

Two things about measuring this that wasted time, neither of them a fault
in the radio:

- **Demodulate well above the audio rate, then filter down.** Subsampling
  the instantaneous frequency straight from 20 MS/s to 48 kHz folds
  everything up to 60 kHz back into the audio band. That read 0.087
  correlation on a link that was in fact perfect.
- **Normalise a correlation over the window that matched**, not over the
  whole reference. Three seconds of recovered audio against a 106-second
  clip cannot score above sqrt(3/106) = 0.168 however exact it is, which
  reads as a failure and is arithmetic.
- **Search negative lags too.** GNU Radio does not prepend a filter's group
  delay to its output, so the first sample out of a chain corresponds to an
  input sample some way in and the recovered signal can *lead* the
  reference. A forward-only search finds a spurious peak: it scored a
  chain that was in fact carrying the sound at 0.947 as **0.03**, and cost
  an hour of looking for a fault that was not there. The alignment search
  in `test_ntsc_transmit.py` is checked against a signal delayed by a known
  amount before it is trusted.

## NTSC Receiver

`ntscReceiver.py` is the other end of `ntscAnalogVideoRecorded.py` and
shares its tile. The picture side is described above; the **sound** is a
second, separate chain off the same radio, because System M puts it on its
own FM carrier 4.5 MHz above the visual one and the picture's Nyquist
filter exists partly to throw it away (a video detector fed both produces
no recognisable sync at all).

- **It is an FM receiver.** `NtscSound` mixes the aural carrier to zero,
  filters to Carson's 80 kHz - 2 x (25 kHz deviation + 15 kHz audio) - and
  decimates 100:1, all in one `freq_xlating_fir_filter`, which costs a dot
  product per *output* sample so 20 MS/s in costs what 200 kS/s costs.
  Then quadrature demodulation, 15 kHz low pass, 6/25 to 48 kHz,
  de-emphasis, volume, `audio.sink`.
- **Demodulate well above the audio rate.** Taking the instantaneous
  frequency straight to 48 kHz folds everything up to 60 kHz into the audio
  band - see the measuring notes above.
- **A missing audio device must not take the picture down**, so the sink is
  built inside a try/except and a failure turns sound off and says so, the
  same shape as the RDS receiver.
- **Two meters, not one.** Sound Carrier says the sound is being
  transmitted at all; Deviation says something is modulating it. A strong
  carrier with no deviation is a station sending silence, which is a
  different fault from no carrier - and below about 100 Hz rms the readout
  says "silent" rather than quoting the demodulator's own noise floor as
  though it were programme.
- **Mute keeps the chain running**, so both meters go on reading. That is
  the point of having them.
- **The spectrum shows the channel, not the radio.** Fed straight off the
  radio the sink is centred on the *local oscillator*, which is
  `LO_OFFSET` - 6 MHz - above the channel centre: 20 MHz wide with the
  television channel squashed into the left third and two thirds of the
  plot empty air. Side by side with the transmitter's own 10 MHz
  channel-centred plot of the same signal, the two looked like different
  signals, and the obvious reading of the receiver's was that it had
  invented a second carrier. It had not - **both windows show two
  carriers, and both are right**: measured off air, visual at 531.2504
  MHz, aural at 535.7499 (visual + 4.4995 against the standard's 4.5) and
  the colour subcarrier at 534.8295 (visual + 3.5791 against 3.579545).
  The receiver now mixes the channel back to the centre and decimates to
  the transmitter's own 10 MS/s in one `freq_xlating_fir_filter`, and the
  transmitter's plot - which is called *RF* Spectrum and was labelled 0 Hz
  at the channel centre - is labelled in RF. The two windows now have the
  same span, the same centre and the same axis.
  - The filter is flat across the whole 6 MHz channel and stops at
    exactly 5 MHz, the decimated Nyquist, so nothing folds into the plot.
    A display that invents a signal is worse than no display - and the
    price of that is that the noise floor *outside* the channel is the
    filter's skirt rather than the air, so do not read adjacent-channel
    interference off this plot.
  - It costs about 0.7 of a core of the eight. Measured off air with it
    in and with it out, back to back on the same transmission: 1,764
    frames either way, 17.6 a second, **0 failed, 0 dropped, 0 BB60D
    overflows**, 3.83 cores against 3.13.
- **The BB60D's analog filter is flat to +-8.5 MHz and gone by +-9.0**
  (measured off its own noise floor at 20 MS/s: -0.7 dB at 8.5,
  -10.9 at 9.0, -70.7 at 9.5, where the decimation filter takes over).
  Tuned 6 MHz above the channel centre the channel occupies -9 to -3 MHz
  of that, so the bottom of the vestigial sideband - 0.75 MHz below the
  visual carrier, at -8.5 MHz - sits in the last half megahertz of flat
  response. It fits, and decoding is unaffected, but there is no margin
  below it: a larger receive offset would start cutting the vestige.

**The transmitter had no pre-emphasis, and adding the receiver is what
found it.** System M sound is 75 us pre-emphasised like FM broadcast, and a
receiver de-emphasises; the FM + RDS transmitter here has always done it,
and the NTSC one never did. Sent that way, every set rolls the treble off -
the sound is not wrong, just dull, which is the kind of fault nobody
reports and everybody hears. `fm_preemph` goes in ahead of the rail,
because pre-emphasis lifts transients by up to 18 dB on this material and
the limiter has to be the thing that catches them; that is the order a real
station processes in. Measured through both chains afterwards: the response
is flat to **0.03 dB from 200 Hz to 10 kHz**, and the rail holds 0.000% to
0.011% of samples at full deviation.

**Verified off air**, the VSG60 transmitting a clip from TVAdemo into the
BB60D here on RF 24: the sound carrier reads a steady -53.0 dBFS, deviation
tracks the programme between 1.7 and 6.5 kHz rms, the picture goes on
decoding at 17.6 frames a second beside it, and the recovered audio
correlates **0.999** with the same clip decoded locally, the next-best
alignment scoring 8% of that.

**And verified again on 2026-09-17, after the decoder changed underneath
it.** The blanking percentiles, the chroma separation and - most of all -
`CompositeFrameSink`, which this receiver now shares with the FM video
receiver and which grew a writer thread of its own for Watch, had all been
checked in software and none of them against real RF on this path. Same
link, same clip, the app's own flowgraph run headless for 220 seconds,
which is two full passes of the clip and of the dark shot that once broke
the blanking estimate:

- **3,868 pictures, 17.63 a second of the 17.63 the buffer allows, 0
  dropped, 0 failed, 0 BB60D overflows.** Line rate 15,734.2-15,734.5 Hz
  (-6 to +16 ppm), input -45 to -37 dBFS, colour throughout.
- **Watch delivered every picture, whole.** Over a 60-second window 1,059
  pictures were decoded and **1,059 arrived at the player - 975,974,400
  bytes, not one byte of a partial frame** - at 17.63 a second, with
  nothing superseded in the holder and the process's memory moving from
  497 to 522 MB across the minute. The same path with the old
  non-blocking write delivered 7.1% of the bytes and 183 MB of backlog.
- **The sound is the clip's own**: 0.9987-0.9997 at four points across the
  run, against the next-best alignment's 0.04-0.12. The clip offset it
  finds advances exactly with the clock at every one of them - 62.99 s at
  40 s in, 6.79 at 90, 56.79 at 140, 96.79 at 180 - which is the 106.206 s
  loop tracked for nearly four minutes without a slip. Sound carrier
  -48.0 to -47.2 dBFS, deviation 0.7-11.0 kHz rms.

One measuring trap, again nothing to do with the receiver: **an FFT
cross-correlation peaks at minus the offset.** `irfft(A * conj(B))` peaks
where `a[j] = b[j-m]`, so a slice taken at clip offset t puts the peak at
-t. Reading that index as the offset picks the wrong segment of the
reference to normalise against, and scored a recovery that was in fact
perfect at **0.007** - which looks exactly like sound that never arrived.
Delaying the slice by a known amount is what catches it: the reported
offset moved by 1234 samples the wrong way.
