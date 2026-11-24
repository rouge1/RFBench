# ATSC: the transmitter and the receiver

One of the notes in `knowledge/`. What applies everywhere, and which
file covers what, is in [CLAUDE.md](../CLAUDE.md).

## ATSC Transmitter

`atscXmitter.py` broadcasts an MPEG-2 transport stream as 8VSB on a 6 MHz
television channel, which a real TV can tune. The flowgraph is the stock GNU
Radio `file_atsc_tx` example block for block - same chain, same root-raised
cosine taps, same rotator - so the modulation was never the thing that needed
fixing. Three other things did:

- **It could not be launched at all.** The dialog read a `tsFileList.txt` from
  the working directory, a file that has never existed in this repo, so the
  combo box always fell into its except branch, OK stayed disabled at 30%
  opacity, and no amount of configuring got past the dialog. It scans the
  media directory now, like every other app - and for every video, not
  only `.ts` (below).
- **It could not run on a HackRF.** `samp_rate` was 12.5e6, and SoapyHackRF
  accepts only whole megahertz from 1 to 20 - it raises in the constructor,
  naming every rate it will take. So the app died before transmitting a
  sample on the one radio most likely to be attached. The second resampler is
  8/19 rather than 25/57 now, which lands on exactly 12.000000 MS/s from the
  same 28.5 MS/s intermediate stage and still carries the 6 MHz channel.
  **But not on an Ettus N210**, which cannot make 12 and quietly gave 12.5,
  so everything went out 4.17 % fast: the transmitter and the receiver both
  take 12.5 for a USRP now (`RADIO_RATES`, `SAMPLE_RATES`) - see
  [ettus](ettus.md#every-app-on-the-n210-2026-11-21). **Since 2026-11-23
  the transmitter runs at 20 MS/s (×40/57), and 25 on an N210**, to make
  room for its LO offset: see
  [the transmitter's own LO leak](#the-transmitters-own-lo-leak-2026-11-23).
- **It clipped.** 8VSB is noise-like: measured 8.7 dB peak-to-average, and the
  chain came out at 1.007 peak against radios that take 1.0 as I/Q full scale,
  so the loudest samples were flattened before the signal left the box - worse
  the longer it ran, as the Gaussian tail reaches further. `BASEBAND_SCALE` is
  0.85 now, leaving 10 dB of headroom; the analog stage sets actual power.

- **It could not be tuned to most frequencies.** The dialog's centre
  frequency was a bare `QSlider` running 50 to 2200 in *whole megahertz*:
  2151 positions rendered a few hundred pixels wide, which is about seven
  megahertz per pixel of mouse travel. Asked for 533 MHz - the channel this
  bench uses - the nearest it would go was 539, and nothing on screen said
  why. Both ATSC apps now share `FrequencyChooser` from `apps/utils.py`:
  type the number, pick the television channel, or drag a slider that moves
  in tenths of a megahertz and whose page step is exactly one 6 MHz channel,
  all three staying in step with each other. The running flowgraph window
  was never affected - its `RangeWidget` is a counter with a 0.1 step, so it
  always took a typed value.
- **It played only transport streams, which on the machine that transmits
  meant one.** The picker listed `.ts` files. Every clip exists as a `.ts`
  here, so the list looked complete - but TVAdemo was sent the fourteen
  clips as `.mp4` only, and its picker offered the test pattern and nothing
  else. It lists every video ffmpeg reads now, one entry per clip, and a
  clip with no `.ts` of its own is encoded as it plays by
  `apps/atsc_source.py`: ffmpeg, with the settings `VIDEO-CREDITS.txt`
  records the `.ts` files were made with, into a pipe the flowgraph reads
  through `file_descriptor_source`. That is cheap - 21x real time on the
  8-core bench and 31x on TVAdemo, measured on the busiest clip, and
  transmitting on TVAdemo it used 0.26 of a core beside the modulator's
  2.06. The radio paces it, since ffmpeg blocks when the pipe is full, and
  what comes through is the mux rate to 0.01% in whole 188-byte packets.
  - A clip that has its `.ts` still plays that file: it costs nothing, and
    it is the one checked through the loopback.
  - Without ffmpeg only the `.ts` files are offered, and the list says how
    many more need it rather than looking like an emptier folder.
  - A choice is remembered by clip name, not file name, so one saved here
    as a `.ts` reopens on TVAdemo as the `.mp4`.
  - `close_stream()` ends ffmpeg once the flowgraph has stopped. Left
    alone it would sit blocked on a full pipe for as long as the launcher
    stays open.
- **Closing its window took the launcher down with it.** Its `main()` was
  the one app's not written for the launcher: it called `app.exec_()`
  regardless, and replaced the window's `closeEvent` with one that called
  `app.quit()`. Inside the launcher the event loop is already running, so
  `exec_()` printed "The event loop is already running" and returned -1 at
  once. Handed -1 instead of a window, the launcher never hooked the close
  to show itself again - and closing the window then quit the launcher's
  own loop, so the tile grid never came back because the launcher had
  exited. It returns the window now, as every other app does, and closing
  goes through the class's `closeEvent`, which also stops ffmpeg; started
  on its own it still runs a loop of its own. Verified on TVAdemo with the
  real flowgraph on the VSG60: the launcher came back, its loop survived,
  ffmpeg exited and the VSG lock was released. `scripts/test_app_close.py`
  checks every app for this.
- **It had nothing to send without media.** The NTSC and FM video
  transmitters always offer built-in colour bars first. This one listed
  only the media folder, so where that held no video it could not start:
  OK stayed greyed out. The Windows laptop's folder holds only MP3s. Now
  **Colour bars (built in)** is first in its list. It sends the same seven
  75% bars as the other two (`ntsc_source.BAR_COLOURS`) with a 1 kHz tone
  at -20 dBFS in both channels, the SMPTE RP 155 level US stations put
  under bars. ffmpeg makes both from nothing (`colour_bars_input()` in
  `apps/atsc_source.py`) at 55x real time, into the same pipe a clip goes
  through.
  - The bars are built at the stream's own 704x480 with 10:11 pixels, and
    every bar edge falls on an even pixel. So no scaler rings on an edge,
    and each edge has a 4:2:0 chroma sample of its own. Decoded back,
    every bar is within 3 of 191. After the loopback's receiver, the
    stream still decodes to those bars and a 1000.0 Hz tone at -20.0 dBFS.
  - Without ffmpeg the line stays in the list, says it needs ffmpeg, and
    cannot be chosen; the dialog opens on the first `.ts` instead.
  - The choice is saved as `<colour bars>`, a name no Windows file can
    have. A config naming no video at all, from `_run.py` say, sends the
    bars.
  - `test_atsc_loopback.py bars 2` needs no media. It locked in 0.39 s,
    then 99.5-99.7% of packets came back byte-perfect here and 99.3% on the
    laptop under ffmpeg 9.
  - **It does not make the laptop an ATSC transmitter.** The laptop sent
    the bars on its HackRF, and the ATSC receiver here on the BB60D found
    the pilot at -32.5 dBFS but never decoded. The laptop was off the air
    46.1% of the time, in gaps of about 11 ms, 41 a second. That is the
    same limit measured before with a clip (see the [Windows notes](machines.md#running-on-windows)), so
    transmit ATSC from Linux. On the laptop the app starts, the window
    says what is on the air, and ffmpeg ends with it.

The transport stream must be **constant bit rate at exactly 19.392658 Mbps**,
since the flowgraph consumes it at a rate fixed by the symbol clock - mux it
any slower or faster and the picture plays at the wrong speed. ffmpeg builds
one with `-muxrate 19392658 -f mpegts`, MPEG-2 video and AC-3 audio.

```sh
python scripts/test_atsc_loopback.py <video> 2      # no radio
python scripts/test_atsc_loopback.py bars 2         # ... the built-in colour bars
python scripts/test_atsc_loopback.py <video> 2 15   # ... at 15 dB SNR
```

runs the transmit chain into GNU Radio's own ATSC receiver (`dtv.atsc_rx`)
with no radio: locks in 0.42 s, then 99.7% of packets come back byte-perfect,
holding above 98.5% down to about 15 dB SNR and collapsing below 14 - which is
where A/53 puts the cliff, and matching it is the best evidence the chain is
honest. The video can be anything the transmitter takes, and a non-`.ts` goes
through the same ffmpeg pipe; either way the score is against what actually
entered the chain, recorded on the way in. The Ford `.mp4` encoded as it
played came back 99.93% byte-perfect after locking in 0.42 s, beside 99.96%
for the test pattern's `.ts`.

**Verified off air**, the VSG60 transmitting from the TVAdemo laptop into the
BB60D here on RF channel 24 (533 MHz) at -9.5 dBm: **79,285 video packets
recovered with 209 flagged bad, 99.74% clean**, which is what the same stream
scores decoded purely in software. ffmpeg read it back as 704x480 MPEG-2 at
29.97 fps with AC-3 audio - exactly what went in - and rendered the test
pattern with its frame counter legible.

**And a clip encoded as it plays**, over the same link: TVAdemo transmitted
`Prelinger-Ford-1960-Wonderful-New-World.mp4`, a clip it has only as an
`.mp4`, through the app's own flowgraph. **0 bad packets of 759,936** over a
minute (0.000%), MER 22.0-23.1 dB, pilot +199 Hz, no BB60D overflows;
TVAdemo's modulator ran on 2.07 cores and its ffmpeg on 0.26, and ffmpeg was
gone the moment `close_stream()` ran. The recording read back as 704x480
MPEG-2 at 29.97 fps, 4:3, with AC-3 stereo, a frame of it rendered cleanly,
and its sound correlated 1.000 with the clip's own soundtrack at 48 kHz. Two
readings of that recording look like faults and are not:

- **Correlated at 8 kHz it scores 0.965** - and so does the clip's `.ts`
  twin, with no radio anywhere near it. That figure belongs to measuring at
  8 kHz, not to the link.
- **ffmpeg reports fourteen frames with no dimensions** whenever it opens
  the recording, even told to start a second in, because it reads the start
  of the file to find the streams. A recording begins partway through a
  15-frame group of pictures; the pristine `.ts` decodes with none, from its
  start or from 30 s in.

Four things cost real time getting there, none of them in the app:

- **A weak capture fails identically to a broken decoder.** `dtv.atsc_rx`
  contains an `agc_ff` creeping at 1e-5 per sample toward a reference of 4.0.
  A BB60D capture of a real station arrives around 0.0002 rms, so the loop
  needs a gain of ~20000 and half a minute of samples to reach it; on a
  five-second capture it never converged and decoded pure noise. Scale the
  capture to about unity rms first and it decodes immediately.
- **Measure that scale in the middle of the capture, not at the start.** A
  HackRF opens at whatever gain it had before the requested one is applied, so
  the first half second can sit 24 dB above the rest.
- **A free-running transmitter needs AFC, and twice over.** A HackRF put the
  pilot 7063 Hz from where A/53 places it - 13.3 ppm at 533 MHz, ordinary for
  that radio - which is far outside `atsc_fpll`'s pull-in, so the decode came
  back as noise while the spectrum looked perfect. Correcting the carrier
  alone changed nothing: the same 13.3 ppm scales the **symbol clock**, and
  correcting that took the decode from nothing to 81% of packets. The VSG60,
  being an instrument, needs neither correction, which is exactly why a
  capture of a real station decoded first time and ours did not.
- **The couple of hundred hertz everything reads is the BB60D's, not the
  transmitter's.** The VSG60 measured 158 and later 205 Hz off through this
  receiver, which looked like the VSG's own error until a *live broadcaster*
  on RF 36 read +194 Hz through the same path. Two unrelated transmitters
  cannot agree to within 11 Hz by accident: about 0.4 ppm of it is the
  BB60D's own reference. It makes no difference to decoding - at baseband
  the AFC cannot tell the two apart and corrects the sum, which is all that
  matters - but do not quote 200 Hz as a transmitter's accuracy.
- **A spectrum average hides a transmitter that is off half the time.** Asked
  to modulate 8VSB at 12 MS/s, the Windows laptop's HackRF was silent 45% of
  the time in gaps up to 10.9 ms, while its spectrum looked flat across the
  channel with clean shoulders - better than the real broadcaster's. Only the
  time-domain envelope showed it. Rendering the baseband on a fast machine and
  letting the slow one play the file back cured it completely (0.000% of time
  below threshold).

## ATSC Receiver

`atscReceiver.py` is the other end of `atscXmitter.py`, and shares its tile
in the launcher - the badge in the corner turns one into the other. It tunes
a 6 MHz television channel, demodulates 8VSB, recovers the MPEG-2 transport
stream and says what it found. It does not decode video itself: **Watch**
hands the recovered stream to `ffplay` (or `mpv`, or `vlc`) and **Record**
writes it into the media folder, where `atscXmitter` can pick it up and
transmit it again. With no media folder in Settings, Record is greyed out
rather than writing wherever the app happened to be started from.

```sh
python scripts/test_atsc_receiver.py <stream.ts>            # no radio
python scripts/test_atsc_receiver.py <stream.ts> --ppm 20   # a worse crystal
```

The arithmetic lives in `apps/atsc_rx_core.py`, free of GNU Radio and Qt
like the RDS and NTSC pairs, so the pilot measurement, the AFC, MER and the
transport-stream parsing can all be checked with no radio at all.

**Verified off air**, the VSG60 transmitting from TVAdemo into the BB60D
here on RF channel 24 (533 MHz) at −9.5 dBm, decoded live and in real time:
**0.00% bad packets sustained over 25 seconds**, 12,900 packets/s (which is
19.392658 Mbps exactly), pilot 205 Hz off — most of which is the receiver's
own reference, not the VSG's — MER 22.0–22.6 dB, no BB60D overflows. The program was read out of its own PAT and PMT as MPEG-2
video on PID 0x0100 with AC-3 audio on 0x0101, the recording came back
through ffprobe as 704×480 at 29.97 fps, and a frame piped to a player
showed the test pattern with its timecode and frame counter legible.

Five things worth knowing before changing it:

- **The chain is built out of `gr-dtv`'s blocks rather than by calling
  `dtv.atsc_rx`.** Same blocks, same order - but the hierarchical block
  makes them locals and throws away the two things a receiver app needs.
  One is the resampler, which is half of the AFC. The other is the
  telemetry: `atsc_rs_decoder` counts packets, unrecoverable packets and
  bytes corrected, which is what separates a weak signal from a mistuned
  one, and the equalizer's soft symbols give MER.
- **AFC is two corrections from one measurement, and half of it is worth
  nothing.** One crystal drives a transmitter's baseband clock and its
  local oscillator, so an error of a few parts per million moves the
  carrier *and* stretches the symbol rate. Measured at baseband the pilot
  shifts by `d*(f_rf + PILOT_OFFSET)` - the carrier carries it up, and the
  stretched baseband carries it back down by `d` times its own 2.69 MHz -
  which is the frequency `afc_correction` takes the ppm figure against.
  `test_atsc_receiver.py` injects 13.3 ppm, the figure a HackRF measured on
  this bench, and pins the result down: uncorrected **99.90% bad and never
  locks**, carrier corrected alone **99.91% bad and never locks**, both
  together **0.00% bad, locked in 0.65 s** - identical to a link that never
  broke. The VSG60, being an instrument, needs neither correction.
- **MER reads optimistically and must never be quoted against the cliff.**
  Slicing each symbol to its nearest 8VSB level is the same thing as
  assuming every symbol was decided correctly, so once noise pushes symbols
  past the halfway point the error to the *wrong* level gets measured
  instead. Against known noise it is right to 0.1 dB down to 22 dB, then
  reads 19.0 for a true 18, 17.7 for a true 15, 16.6 for a true 12 - it
  bottoms out near 16, which is *below* A/53's 15.2 dB threshold of
  visibility. So it cannot see the cliff at all. Read it as how open the
  eye is; read the bad-packet rate for whether the picture is intact. The
  app deliberately says no more than "Breaking up - N% of packets lost".
- **`sps` is the one knob that sets how much work the receiver does.** The
  arbitrary resampler carrying the radio's rate to the symbol clock *is*
  the bottleneck - measured, everything from the equalizer onward is free
  beside it - and it costs `(2*8+1)*sps` taps per output sample. At 12 MS/s
  on the 8-core bench: 1.5 runs at 1.16× real time for 24.0 dB MER, 1.2 at
  1.42× for 23.5 dB, 1.1 at 1.57× for 21.4 dB. It is 1.2. In steady state
  all of them decode a clean signal at 0.000% bad; the differences in the
  *totals* are acquisition time, not quality, which is why the test finds
  where the receiver locked and reports only what came after.
- **It printed four buffer warnings on every run, and they are fixable.**
  GNU Radio rounds every stream buffer up to a 4096-byte page and logs a
  WARN when the size it was asked for was not already there. A transport
  packet is 188 bytes and a Reed-Solomon one is 207, neither a power of
  two, so `atsc_viterbi_decoder`, `atsc_deinterleaver`, `atsc_rs_decoder`
  and `atsc_derandomizer` tripped it every time - four lines about
  something that is not a fault and that nobody can act on. Asking for the
  aligned count up front (`align_output_buffer` in `apps/utils.py`; 4096
  items for 207 bytes, 1024 for 188) silences them while allocating exactly
  what it was going to allocate anyway, and the decode is unchanged. The
  *transmitter* never had this - its items are 256 and 1024 bytes. The four
  that still appear from `scripts/test_atsc_loopback.py` come from
  `dtv.atsc_rx` inside it, whose blocks are locals and cannot be reached;
  that is deliberate, since the point of that test is to check our
  transmitter against GNU Radio's own receiver rather than our own.
- **Watch pipes to the player and drops bytes rather than blocking.** A
  blocking write into a player that has stalled or been closed would park
  the GNU Radio scheduler thread and take the whole receiver down, and
  there is no way to apply backpressure to the air. So the pipe is
  non-blocking, the backlog is capped, and what is discarded is discarded
  in whole 188-byte packets so the player resyncs on the next sync byte
  instead of hunting.

**The analog receivers' Watch is a different problem, and got this wrong.**
A transport stream is a river of small packets, so writing what fits and
dropping the rest works. A picture is one object of 921,600 bytes in NTSC
and 1,327,104 in PAL, where a pipe holds 65,536 - and `_to_player` wrote
once per decoded frame. A non-blocking write to a pipe delivers at most one
pipe buffer, so **each picture lost fourteen fifteenths of itself**, and the
remainder queued against a cap that only applied inside the branch that
never ran. Measured against a real FPV transmitter: of 214 pictures decoded
in 12 seconds, 7.1% of the bytes reached the player - **1.25 pictures a
second** - while the unsent backlog grew to **183 MB**. On screen that is a
picture that updates about once a second and falls further behind, which is
exactly what it was reported as.

`CompositeFrameSink` writes from a thread of its own now, blocking, and
holds a single frame rather than a queue: a player that has fallen behind
gets the newest picture and never a backlog, which is what the comment
above always claimed. Afterwards, 100% of decoded pictures arrive, whole,
at 17.57 a second with memory flat. Two details that matter:

- **The stop drains.** Asking the writer to finish before taking the pipe
  away from it is what stops the last picture of every session being
  thrown out; checked after the write rather than before it, the count is
  exactly the number decoded.
- **Tell the player the rate pictures actually arrive at**, which is one
  per buffer - 17.6 a second in NTSC, 14.7 in PAL - not the standard's
  29.97 or 25. A player told 29.97 and fed 17.6 starves between frames.

`scripts/test_fm_video_receive.py` checks the whole path now, because
nothing did: it runs the decoder at the radio's pace into a stand-in player
and requires every decoded picture to arrive whole. Against the old write
it reads 1.0 pictures of 14.

## The transmitter's own LO leak (2026-11-23)

Found in CTF night three's round 18, when skinny-fm's receiver lost 35-65 %
of packets from the VSG60 at −59 dBm on 5830 MHz until it notched a line
at the channel centre. Measured again over the cable into the BB60D here,
with `atscXmitter` sending the colour bars:

| VSG60 level | The line at the centre | `atscReceiver` (gain) |
|---|---|---|
| −20 dBm | −28.6 dBc | clean, MER 23 dB (40 %) |
| −40 dBm | −32.6 dBc | 100 % lost at 40 %; clean at 70 % and 100 % |
| −59 dBm | −19.8 dBc | not decoding at 40 %; 38-55 % lost at 100 % |

- **The line is the VSG60's LO.** The app tuned the radio to the channel
  centre, so the leak sat in the middle of the channel, carried +2.2 kHz
  by the VSG's frequency error at 5.8 GHz like the signal itself.
  - It does not fall with the level the way the signal does: 29 dB less
    from −20 to −59 dBm while the signal fell 38.
  - So it matters only at low levels, and only together with a channel
    already near the noise.
- **−40 dBm failing was not the leak but the gain.** At the receiver's
  default 40 % the BB60D's noise set MER at 17.5 dB, and every packet
  failed; at 70 % it decoded cleanly. A weak cabled signal needs the gain
  slider up before blaming anything else.
- **At −59 dBm the leak was the difference.** A full-gain capture decoded
  offline through `AtscDemod` lost 47 % of packets and never settled.
  Notching the line first took it to 0.1 % after lock, at MER 18.1 dB.
  - The same notch on the −20 dBm capture changed nothing (0 % bad, MER 23.3).
  - A synthetic −20 dBc carrier on a clean link costs 3 dB of MER (22.7
    to 19.5) but decodes. The real link was nearer the edge, and the
    VSG at −59 dBm brings more than white noise.

Three fixes, on branch `atsc-leak-fixes`:

1. **`atscXmitter` tunes the radio `LO_OFFSET` (4.5 MHz) above the
   channel** and shifts the signal down to match after the last
   resampler, as `ntscAnalogVideoRecorded` already did.
   - The radio rate went up to make room: 20 MS/s for the VSG60 and the
     HackRF (×40/57 from 28.5 MS/s), and 25 for the N210, an even divide
     of its 100 MHz.
   - The channel then spans −7.5 to −1.5 MHz at baseband, inside the
     VSG60's 80 % of 20 MS/s.
   - The chain's rms is 0.3696 at 12, 20 and 25 MS/s alike, peak 0.97, so
     `BASEBAND_SCALE` and the loopback's SNR arithmetic stand. The
     spectrum display still shows the channel at `cf`.
   - **Over the cable at −59 dBm** the VSG60 tuned to 5834.5 MHz and the
     centre line was gone. The leak turned up at **5834.669 MHz**, 169 kHz
     from where the radio was tuned (as [radios](radios.md#vsg60-notes)
     warns it can), −30.7 dBc and 1.7 MHz above the channel edge.
     `atscReceiver` at 100 % gain: "Locked - clean stream", 0.2 % bad,
     MER 18.2 dB. At −20 dBm and the default gain: 0 % bad, MER 22.5.
   - Run on the VSG60 only. The HackRF at 20 MS/s and the N210 at 25 are
     arithmetic, not yet transmitted. With the LO above the channel the
     pilot sits at −7.19 MHz, near the edge of what those radios pass at
     those rates: compare its height on their first run, and put the LO
     below the channel instead if it has sagged. The chain itself keeps
     up: into a null sink, 2.1× real time at 20 MS/s and 1.85× at 25,
     against 1.96× at the old 12 (on this 20-thread host, possibly with
     another session's tests running).
2. **`atscReceiver` notches the channel centre** after the AFC rotator,
   which has put the transmitter's carrier at DC: the input less a
   one-pole running mean (`CENTRE_NOTCH_LEN` = 1024 samples). At 10 MS/s it
   is −3 dB at ±1.5 kHz, −10 dB at ±500 Hz and −1.8 dB at ±2.2 kHz. It is there for a transmitter that leaks into its own channel,
   ours before the fix or anyone's.
   - `dc_blocker_cc` did the same job and took the receiver from 1.46× to
     0.91× real time; the one-pole keeps 1.53×.
   - `test_atsc_receiver.py` adds a −20 dBc leak: MER 19.5 dB unnotched,
     22.7 notched, 22.7 with no leak at all.
   - With AFC off the leak sits wherever the carrier error puts it - +2.2
     kHz from the VSG60 at 5.8 GHz - where the notch takes only 1.8 dB off
     it. So it works only with AFC on, the default.
   - Our own transmitter no longer needs it: the post-fix −59 dBm capture
     decodes the same with the notch off (MER 18.2 dB) as on (18.1).
3. **`atsc_source` names the programme in the SDT**: the clip's name as
   `dvbt2_tx` names its programmes, and provider `RF Bench`. Before, ffmpeg
   wrote its defaults, "Service01" from "FFmpeg".
   - These are DVB service-description tables. ffmpeg writes no ATSC PSIP
     (VCT, MGT), and `atscReceiver` lists programmes from the PAT and PMT.
   - A pre-made `.ts` plays as it is and keeps whatever tables it was made
     with. `scripts/test_atsc_source.py` reads the SDT back.

`test_atsc_loopback.py` follows the new chain: it shifts back by
`LO_OFFSET` before `dtv.atsc_rx` and takes an optional leak at the
radio's LO (`... bars 2 none -20`). Colour bars come back 99.56 %
byte-perfect clean, 99.60 % with a −20 dBc leak, 99.06 % at 15 dB SNR and
98.20 % at 14.

**Once, not explained:** one `atscReceiver` run, started within seconds of
the transmitter coming on air, reported 100 % of packets lost for all 55 s
with MER 23 dB and the eye wide open. A second run straight after, on the
same transmission, was clean from the first report, and so was every run
before and since. A decoder that locked to the wrong state and never let
go is the likeliest reading; restarting the receiver cleared it.

Chased afterwards, it did not come back:
- **Live:** three fresh keyings of the transmitter, and ten receiver starts
  across them, five on the branch (with the notch) and five on `main`
  (without). Three started within seconds of keying, one with the N210
  joining at 10 s as in the failed run. Every one was "Locked - clean
  stream", 0 bad packets.
- **Offline:** the −20 dBm capture decoded from 20 start points spread
  across one field, notch on and off: none above 14 % bad, which is only
  the acquisition inside a 2 s slice.

So it is once in about sixteen starts of the branch's receiver, not seen
on `main`'s in five. That is too few to call it either the notch or
pre-existing. If it returns, restart the receiver, and note how soon after
keying it started.

The −20 dBm MER-17 limit [ettus](ettus.md) recorded at 915 MHz over the
air, on both the BB60D and the N210, predates the offset. Whether the
leak was part of it has not been checked.
