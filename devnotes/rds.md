# RDS: the receiver and the FM + RDS transmitter

One of the notes in `devnotes/`. What applies everywhere, and which
file covers what, is in [CLAUDE.md](../CLAUDE.md).

## RDS Receiver

`rdsReceiver.py` tunes an FM broadcast station and decodes the data on its
57 kHz subcarrier: station ID (PI), program service name, RadioText, program
type and clock time.

- **It uses the radio chosen in Settings**, like every other app: the HackRF,
  a USRP picked from the configured addresses, or the BB60D. The VSG60
  transmits only, so with it selected the whole dialog is an error pointing
  at Settings, and closing it launches nothing.
- **Each radio brings its own sample rate**, because the BB60D's are a
  ladder with nothing at the 2 MS/s the HackRF path uses. `SAMPLE_RATES`
  gives it 2.5 MS/s, and the channel filter decimates by 10 rather than 8 —
  everything after it still runs at the same 250 kHz MPX rate, so nothing
  else in the chain changes. **Verified live on the BB60D**: 98.7 decoded
  as PI `0x16F2` confirmed WMZQ, program type Country, stereo pilot locked.
- **60% is the BB60D's best setting here, and both directions are worse** —
  measured on that station: 40% decodes nothing at all, 60% gives 80% of
  blocks good, 80% gives 68% and 100% gives 75%. That is the same shape as
  the ATSC finding (attenuator open, no RF amplification), and for the same
  reason: RF gain in a band as crowded as FM overloads the front end with
  everything *except* the station you want. 80% blocks good is usable but
  well short of the 96–99% a HackRF gets — the antenna on it is set up for
  UHF television, not the FM band.
- **It tunes 30 MHz to 6 GHz, not only 87.5-108**, so it can follow the FM +
  RDS Transmitter off the broadcast band - nothing in the chain after the
  radio depends on the carrier. The dialog's slider still sweeps only the
  broadcast band (`FM_BAND_MHZ`), as the transmitter's does; see there.
- **Gains are applied after `tb.start()`** (`main()` calls `tb.apply_gain()`).
  SoapyHackRF silently ignores the `AMP` stage when it is set before the stream
  is running - worth ~14 dB, which is the difference between decoding and not.

The decoding itself lives in `apps/rds_core.py`, deliberately free of GNU Radio
and Qt so it can be run against a recorded capture:

```sh
python scripts/test_rds_core.py <capture>   # capture path without .cfile
python scripts/test_rds_radiotext.py        # RadioText changes - no radio, no capture
python scripts/test_rds_clock.py            # clock time, received and sent - no radio
```

`RdsDemod` mixes the MPX down by the 57 kHz subcarrier and integrates each
biphase symbol; `RdsProtocol` syncs to the 26-bit block structure, corrects
error bursts up to 5 bits with the (26,16) code, and assembles the fields.
Three things that are easy to get wrong and cost real time here:

- **The 57 kHz subcarrier and the 1187.5 bit/s clock are both locked to the
  19 kHz stereo pilot** (x3 and /16). One PLL lock on the pilot supplies the
  carrier phase *and* the symbol timing, so no separate recovery loops.
- **The symbol timing offset is not a constant and must be measured.** The bit
  grid is anchored at whatever pilot phase the first sample had, so it differs
  every run and with any filter delay. `_pick_tau` measures it by scoring
  candidate offsets on how many valid offset words appear. A wrong value
  produces plenty of bits at exactly the right rate and decodes *nothing*.
- **RadioText is buffered per A/B flag, and a page is kept when its flag comes
  back.** The flag means "new message", but it is a single bit in block B, so a
  corrupted one would wipe a good message; `RdsProtocol` keeps a buffer for
  each flag value, and a bad bit writes into the page nobody is displaying. The
  receiver once also cleared a page whenever its flag returned. A station
  rotating two messages under A and B - the FM + RDS transmitter does, with
  typed text and the song - then made it start every page from nothing on
  every turn: simulated at 88% blocks good, a new song's Now Playing never
  arrived in two minutes, and off air at 78% it did not either. Now a page is
  cleared only when a believed segment contradicts its text; straight after a
  change of flag one differing character is enough, so paragraph page "3/5"
  still replaces "1/5" at once. In the same simulation the next song now
  arrives in 24-37 s at 88% and 48-99 s at 84%, never with a wrong title, and
  `scripts/test_rds_radiotext.py` requires it within a minute at 88%. **Verified
  off air** at 87-89% blocks good, the transmitter window on the VSG60 against
  the receiver window on the Windows HackRF, typed text rotating with the song:
  RadioText exactly right on screen 85% of the time (was 4%, wrong 81%), and
  after Next Track the new song's Now Playing arrived in 62 s (was never), with
  no wrong title. A kept page keeps only what was believed, though: when its
  flag comes back, characters no believed segment has confirmed are dropped
  (`drop_provisional`). Without that a wrong repair landing past the end of a
  short page was never overwritten - the transmitter stops each page at its
  carriage return, so nothing sends those positions again - and off air it left
  "#    @8" after the song line for 20 s. Simulated, dropping them cut wrong
  RadioText from 20.4% of the time to 12.9% at 84% blocks good and from 5.7%
  to 4.4% at 88%, at the price of showing gaps instead (3.1% to 15.0% at 84%):
  what is on screen is incomplete rather than wrong, and Now Playing arrives no
  later. Off air again at 89-92% the tail was gone: RadioText wrong 1.5% of the
  time, and that only the song line still up a turn late rather than anything
  garbled, with the next song's Now Playing 37 s after Next Track. A cleared
  page is written with the same group that cleared it - clearing without
  writing loses the first four characters of every page, which is exactly what
  paged paragraphs once exposed. RT+ tags slice the page being sent, which can
  be a group or two ahead of the page on display, and must find every tagged
  character believed on that same page: checked against the page on display
  instead, the simulation stored titles like "Stereo Separatimn Test 1) z"
  from repairs never confirmed.
- **A new message is also detected from its content, because many stations
  never toggle the flag.** 98.7 rotates a slogan, the song and an advert through
  RadioText with A/B stuck at 0, so nothing ever cleared the buffer: each message
  overwrote the last segment by segment, the display showed splices like
  "98.7WMZQBest Country", and a car-dealer advert turned up inside the song
  name. A segment that contradicts characters the current message has already
  sent now starts a new message.
- **Only blocks that arrived clean may announce a new message.** The (26,16)
  code maps nearly every syndrome to *some* correction, so a block whose error
  burst is too long comes back wrong rather than rejected - 'Tyler' as 'Eyler',
  a space as '&'. Taking that for a new message blanked the display, and so did
  the clean pass that repaired it. Requiring two differing characters was not
  enough, because a bad block usually changes both of the characters it
  carries. Characters from a block that needed correcting are therefore
  provisional: one fills a position nothing has written yet and stays until a
  clean block replaces it or its page comes round again. It never overwrites a
  clean character - nor another provisional one, since a repair that came out
  right would otherwise be replaced by the next that came out wrong - never
  counts as a contradiction,
  and a corrected A/B flag cannot clear a buffer. Measured with encoded error
  bursts at 96.6% blocks good, the text had been wrong on screen nearly two
  thirds of the time and short or blank a fifth of it; afterwards both were
  zero at 96.6, 98.4 and 99.4%, and `scripts/test_rds_radiotext.py` now
  requires never. Off air, the VSG60 into the Windows laptop's HackRF at 96-97%
  blocks good: 114 wrong or blank displays in five minutes before, none in six
  and a half after.
- **But a repair that comes out the same twice is believed.** Refusing every
  repair was too strict on a weaker link: at 93% blocks good about six repairs
  in seven were right, yet two thirds of RadioText groups carried at least
  one, so the text after Next Track sat with gaps for 54 s, and Now Playing,
  which waits for every tagged character, took 58 s. `_believed()` accepts a
  segment whose repair matches the last repaired reception of that same
  segment, since a wrong repair almost never comes out the same way twice. PS
  goes through it too - taking every repair as it came, PS had flashed a wrong
  value 135 times in four minutes - and so do RT+ tag groups. **Verified off
  air**, the transmitter window on the VSG60 against the receiver window on the
  Windows HackRF, on a link that fell to 80-90% blocks good: PS wrong 0% of the
  time (was 24%, 135 flickers), and the Station Clock turned over within a
  second of every minute (was never shown). What remains is the link itself:
  after Next Track, at 80%, RadioText and Now Playing still took 44-47 s to
  arrive whole.
- **Now Playing is an RT+ item, kept until the song changes.** NRSC-G300-C
  section 6.10 has a receiver keep RT+ title, artist and the rest of content
  types 1-11 across RadioText messages for as long as the item toggle bit
  holds, and purge them when it flips. So the receiver keeps them through
  untagged messages and A/B page switches, and drops them when the toggle
  flips - or when a tag arrives from a different message, so that a title
  tagged in an advert never sits beside the artist of the song before it.
  Clearing them on every new message instead, as it once did, showed Now
  Playing as "-" whenever a station, or the transmitter here, sent any other
  RadioText while the song was still playing.
- **An RT+ tag may only slice characters the current message sent.** Tags that
  arrived while the next message was still filling in cut across both, welding
  "Dan +" from the new text to "ntry" from the tail of "Country". Stations repeat
  the tag group every second or two, so a refused tag lands on the next pass.
- **A clock group is a sync, the way a car radio treats it.** Stations that
  send group 4A do so about once a minute, and many send none: 98.7 sent no 4A
  in 12 minutes of 99.99% clean blocks, yet the receiver once displayed
  "2168-10-28 01:27 (UTC-9)" for it. A block B that the code corrects wrongly
  turns any group into a 4A - those same 12 minutes held one 1B and one 12B,
  types the station never sends - and its C and D then decode as a date. The
  first reading is therefore believed only once another 4A carries the same
  offset and a time that has moved on by as much as the bitstream has
  (`bits_in` at 1187.5 bit/s, so replaying a capture flat out behaves the
  same). From then on the clock runs by itself on that stream time: a lost
  group costs nothing, one group agreeing with the running clock re-syncs it,
  and the window says how long ago that was. Before this, a lost group left
  the display a minute or more behind. Garbage that disagrees is ignored, while
  a genuine change - the offset moving when daylight saving ends - takes two
  agreeing groups. The second witness may be any of the last few readings,
  not only the latest: off air at 93% blocks good, garbage clock groups made
  from repaired B blocks arrived twice in one minute, and remembering a single
  reading kept the clock off the screen for five minutes. Two identical
  readings cannot confirm each other, though: a group the station repeats every
  few seconds - a RadioText segment - carries the same blocks C and D each
  time, so a block B corrected into a 4A the same way twice gives the same
  time, and a time that has not moved at all sat well inside the 90 s of slack.
  Off air at 87% blocks good that put "2206-10-30 14:21 (UTC+9)" on screen for
  97 s, and the same pair could have displaced a running real clock. The second
  witness must be at least a minute earlier, so a station repeating one
  minute's group is confirmed by its next minute instead. That costs nothing
  off air: the clock still appeared 155 s after tuning in, as it did before the
  rule, and was right at every minute after. Repaired groups are
  still used, because the real ones are repaired too - the 19:53 and 19:54
  groups in that run both had block B repaired and carried the right time.
  A station whose clock is wrong but consistent still shows.
  It is shown as local time: the group carries UTC hours and minutes with the
  offset beside them, so printing those fields next to "(UTC-4)", as the
  receiver once did, reads four
  hours fast and can show tomorrow's date. `clock_text()` does the arithmetic
  for both the receiver and the transmitter's On Air box.
- **Do not average PS or RadioText over time.** US stations scroll messages
  through the 8-character PS field and often alternate two RadioText messages
  without toggling the A/B flag, so averaging blends them into gibberish. The
  live fields take characters as they arrive; the stable station name comes
  from the most common complete PS (`ps_history`).

A station's PI need not match its call sign. 98.7 WMZQ transmits `0x16F2` where
the RBDS formula gives `0x76F2` - altered in the high nibble only, which US
stations do for traffic-data (TMC) services, shared-PI simulcasts and factory
defaults. Mapping `0x16F2` straight back yields the nonsense "KCQK".

`callsign_candidates()` therefore restores each possible high nibble, and
`RdsProtocol.confirmed_callsign()` picks the candidate that appears in the
station's own PS or RadioText - "98.7WMZQ" confirms WMZQ out of the nine
possibilities. The UI shows a confirmed call sign plainly and an
uncorroborated one as "maybe", with the PI itself always the real identifier.

Why the nibble changes, confirmed off-air against NRSC-G300-C section 5.1: the
RDS-TMC standard has a receiver match the PI's *country code* against its map
data, and the US was assigned country code 1, so stations carrying traffic data
set the high nibble to 1. All three local iHeartMedia stations do it - WMZQ
7→1, WIHT 6→1, WBIG 5→1 - and each restores exactly to its real call sign. Only
WMZQ actually transmits TMC (`has_tmc`, ODA AID 0xCD46 in group 8A); the others
appear to follow the group convention.

**RadioText+ (`RTPLUS_AID`, 0x4BD7).** The same NRSC guideline tells receivers
to read the RT+ StationName field rather than back-calculating a call sign, and
all three local stations carry RT+ in group 12A. It tags substrings of the
RadioText by class, so "Love Somebody" and "Morgan Wallen" arrive as separate
`title` and `artist` fields. Stations do ship offsets that do not match the text
they actually sent (99.5 tags two characters late, yielding "jured? Attorne"),
so `_parse_rtplus` drops any tag that would slice a word in half.

## FM + RDS Transmitter

`fmRdsTransmitter.py` plays audio from the media folder as a real FM broadcast
signal and carries a live-editable station name, RadioText and now-playing tags
on the 57 kHz subcarrier. The encoder is `apps/rds_encode.py`, again free of GNU
Radio and Qt so it can be tested without a radio:

```sh
python scripts/test_rds_loopback.py        # encoder -> decoder, no radio at all
python scripts/test_fm_rds_tx.py <wav>     # whole modulation chain -> decoded back
python scripts/test_fm_rds_next_track.py   # Next Track keeps pilot and RDS continuous
```

The multiplex is built at 200 kHz (everything up to 60 kHz fits), interpolated
to the 2 MS/s the radio runs at, and only then frequency modulated - doing it in
that order matters, because the modulated signal is far wider than the
multiplex. Levels are shares of the 75 kHz peak deviation: audio 0.55, pilot
0.09 (the standard 9 %), RDS 0.04. Measured output is ~48 kHz peak deviation
with music playing.

Things worth knowing before changing it:

- **It transmits anywhere from 30 MHz to 6 GHz**, 433.92 as readily as
  101.3: nothing in the multiplex or the modulator depends on the carrier,
  so the band limit it once had was only the spin box's. 30 MHz is the
  VSG60's floor and 6 GHz the HackRF's and the VSG60's ceiling; a USRP's
  daughterboard may stop short of either, as the WBX here does at 2.2 GHz.
  Off the broadcast band a car radio hears nothing, so the RDS Receiver,
  which tunes the same span, is the way to listen. The window's frequency
  box tunes on Enter rather than on every keystroke, because across that
  span typing 915 passed through 91 MHz, a broadcast channel, on the way.
  **The dialog's slider covers only 87.5-108** (`FM_BAND_MHZ`, the
  `slider_range` of `FrequencyChooser`), which the user asked for: across
  the whole 30-6000 the band would be a sliver of the slider. A frequency
  typed into the box outside it stands, with the slider resting at the
  nearer end, and using the slider at all - grabbing it, clicking its
  groove, an arrow key - brings the frequency back to where it sits,
  inside the band.

- **Keep a Python reference to every block.** `rds_source` is a Python block; if
  its wrapper is garbage collected while the C++ scheduler is running, the
  process segfaults with no Python frame in the traceback. The app stores blocks
  on `self`, and `scripts/test_fm_rds_tx.py` keeps a `tb.keepalive` tuple - a
  plain function that builds a flowgraph and returns only the top block will
  crash.
- **0 % power is not off.** On the bench, a HackRF at the minimum VGA setting
  still put the signal 32.5 dB above the noise floor at the receiver several
  feet away, decoding at 100 %. Treat "lowest setting" as "still transmitting",
  pick an empty channel, and do not assume a low slider is harmless.
- The pilot and the RDS subcarrier are generated from one sample counter in
  `RdsSubcarrier`, so 57 kHz stays exactly three times the pilot and the bit
  clock exactly a sixteenth of it. Receivers depend on that lock.
- **Stereo is matrixed, not sent as left and right.** A 2-channel file becomes
  mid (L+R)/2 as ordinary audio plus side (L-R)/2 on a 38 kHz DSB-SC
  subcarrier, which is what keeps the signal listenable on a mono receiver.
  `rds_source` emits that 38 kHz carrier on its second output, from the same
  sample counter as the pilot so it stays exactly twice it - a separate
  oscillator would drift and lose stereo. The chain is always stereo: a mono
  file leaves `audio_source` the same on both sides, so its difference is zero
  and the subcarrier carries nothing - on the air, a mono signal. Verified
  off-air: 38 kHz subcarrier 24.8 dB
  out of the noise, 47 kHz peak deviation, RDS unaffected at 904/904 blocks.
  Test it with `scripts/test_fm_stereo.py <stereo.wav>`.
- **Next Track swaps the file inside a running source; it must never rebuild
  the flowgraph.** It once did - `lock()`, `disconnect_all()`, rebuild,
  `unlock()` - and that throws away every sample in transit, so pilot and RDS
  jumped 139 degrees of pilot phase at each press. A receiver measures its RDS
  bit timing once and then follows the pilot, which reveals only the fraction
  of a cycle that jumped, so its bit grid ended up off by whole sixteenths of a
  bit: decoding fell from 100% to 30-60% in software, and off air to nothing at
  all while the RF was plainly still there. A check that decodes a fresh
  capture never sees this, because it measures the timing anew - which is how
  "RDS unaffected" above was measured. `audio_source` plays WAV files, the tone
  or silence and switches in place; `scripts/test_fm_rds_next_track.py` records
  the multiplex across stereo-to-stereo and stereo-to-mono presses and requires
  the pilot to carry straight on and a frozen-timing decoder to stay at 95% or
  better.
- **The recovered side/mid ratio reads high off the air, and should.** Measured
  -5.8 dB against -8.7 dB in the source file; in a noiseless software run the
  gap is only 1.4 dB. FM noise grows with baseband frequency and the difference
  signal sits at 23-53 kHz where there is more of it. That is the same effect
  that makes stereo reception noisier than mono, not a fault in the chain.
- **Channel separation is the measurement that actually proves stereo, and it
  needs its phase fitted.** `scripts/test_fm_separation.py` drives one channel
  with a tone while the other stays silent and measures how much leaks across,
  reading only at the tone frequency so noise cannot flatter the result
  (validated to 0.1 dB against deliberately injected crosstalk). Measured
  34.3 dB through the chain and 32-33 dB off the air via a BB60D, which is
  normal for FM stereo. But the regenerated 38 kHz subcarrier has to be
  phase-aligned with the multiplex: the pilot reaches the PLL through a
  band-pass whose group delay the multiplex does not share, so assuming zero
  offset reads 18 dB and looks exactly like a broken transmitter. The delay
  arithmetic predicts the offset - a 401-tap band-pass delays 200 samples,
  0.2 of a cycle at 19 kHz, 72 degrees of pilot phase, doubled to ~144 degrees
  at 38 kHz - and the fit lands on 144 in software and 146 off the air.
- **The On Air box reads the encoder, not the edit boxes.** What was typed is
  not always what is being sent: Next Track rewrites RadioText and its RT+
  tags, and a paged paragraph moves on by itself. So Station (call letters and
  PI), PS, Now Playing (the song on air, whichever RadioText page is up), RadioText
  and Station Clock (the last group 4A sent) come from
  `RdsEncoder.snapshot()` on a 500 ms timer - the same fields the RDS Receiver
  shows, so the two windows can be compared side by side.
- **The clock is the computer's own, sent as group 4A** once as the stream
  starts and then at the start of every minute, as NRSC-4-A Annex M asks. The
  hour and minute go out as UTC with the local offset beside them, and
  `system_clock()` is read afresh each minute so daylight saving follows the
  OS. The encoder checks the minute ahead of every group, so the group leaves
  within 88 ms of the edge. A receiver needs two groups before it trusts one,
  so the Station Clock appears one to two minutes after tuning in. An
  `RdsEncoder` built without a `clock` sends none, which is what NRSC-G300-C
  section 5.8 asks of a station with no reliable time source. `docs/` holds
  those rules and the Annex G date formulas, but not the bit layout itself:
  that is Part I section 3.1.5.6, and the NRSC-4-A PDF there is Part II only.
  **Verified off-air**, the VSG60 on the Linux box at 102.1 MHz and -42 dBm
  into the Windows laptop's HackRF: every group left 39-89 ms after its
  minute, and the receiver showed the right local minute within a second of
  it. With the encoder told to stop sending after two groups, the receiver's
  clock went on turning over within half a second of each minute for the
  remaining four.
- **Tests drive the clock from the bitstream, not the wall.** Handing the
  encoder `clock=lambda: start + timedelta(seconds=enc.bits_sent / 1187.5)`
  makes a minute edge arrive on schedule however fast the stream is produced,
  so `scripts/test_rds_clock.py` runs minutes of transmission - daylight saving
  ending, UTC already in the new year - in under a second.
- RT+ offsets are computed from the very RadioText string that gets sent
  (`set_now_playing` does both together), which is precisely what 99.5 locally
  gets wrong.
- **Typed RadioText takes turns with the song, and Now Playing stays on it.**
  Now Playing is not text of its own: it is RT+ tags pointing into RadioText,
  so replacing RadioText used to end it, and Send Text showed Now Playing as
  "-" on both windows. With a song on air, `set_radiotext()` now rotates the
  typed message with the song's own line, three passes each (about 6-10 s,
  depending on length). During the message the RT+ group still goes out, with
  empty tags and the item toggle bit unchanged, which tells receivers to keep
  the song (NRSC-G300-C 6.10); a receiver tuning in later learns the song on
  its turn. The toggle flips only in `set_now_playing()`, i.e. on Next Track,
  and a typed message stays through Next Track. Sending the song's own line,
  or nothing, goes back to the song alone. Pages shorter than 64 characters
  stop at their carriage return instead of sending padding, so each turn gets
  across sooner. An RT+ tag goes out only once every segment it points into
  has gone out, starting from the first segment of the new text: Next Track
  part way through a pass once sent padding first, identical in the old and
  new lines, and a receiver that had seen nothing change sliced "Stereo" out of
  "Stereo B" with the new song's tag - caught by the Next Track test on the
  slower Windows laptop, and now reproduced on purpose in
  `test_rds_radiotext.py`. Waiting for the whole 64-character line instead
  cost 3.4 s per song, and on that laptop, which ran the test flowgraph at
  about 70% of real time, Now Playing never arrived at all.
- **Messages longer than RadioText are paged.** `set_paragraph()` splits text on
  word boundaries into 64-character pages, sends each one complete, then toggles
  the A/B flag so receivers clear before the next. Pages advance on segments
  sent rather than a clock, so a page is never replaced halfway out. Budget
  ~4.2 s per page at the standard group mix: a 250-character paragraph is five
  pages and takes about 21 s to deliver in full.
- **Paged text is numbered "2/5 " for a reason.** The cycle repeats forever, so
  a receiver tuning in mid-paragraph gets every page but *rotated*, not in
  reading order - measured off-air, a capture began at page 2 and wrapped to
  page 1 last. The prefix is what allows reassembly without waiting to spot
  where the cycle wraps; it also costs page width, which can add a page and so
  widen the prefix, which `set_paragraph` settles by iterating.
