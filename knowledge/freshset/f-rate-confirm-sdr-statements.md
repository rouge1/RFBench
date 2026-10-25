# SDR's statements for bluey's F + rate confirmatory

What bluey-ox-walker's `knowledge/f-rate-confirm-reg.md` (section 4) asks SDR to state in one committed file
before its freeze A: the LAP pair, the shots, the bench's pins and how the bench behaves. Written 2026-10-25 on
branch `feat/frate-confirm-prep`. Nothing here was played or recorded: no radio was used.

## 1. The LAP pair

The rule is `scripts/bt_freshset2_select.py`, committed at `c5b854c` **before** its search was run. Its output,
committed at `6410115`, is `knowledge/freshset/selection2_output.txt`. The rule tried schedule seeds from 122349 up
(the earlier fresh-set search covered 100000-122348) and stopped at 123154, the first seed that completed a pair.

| | 0x13B6FA | 0x66C0A6 |
|---|---|---|
| schedule seed | 122626 | 123154 |
| ones in the 64 sync bits | 40 (positive) | 24 (negative) |
| preamble | 1010 | 0101 |
| sync-word distance to 0x885614, 0xEED5EF, 0x20C0AA, 0xDBB3FB | 38, 42, 30, 30 | 36, 32, 28, 38 |
| UAP | 0xE6 | 0xEA |

The two sync words are 30 bits apart, and the preambles are opposite. The bits were checked twice: with SDR's
`apps/bt_br_frame.access_code` and with bluey's `lib.detect.pipeline._generate_ac_bits`, which give the same 72 bits
for all six LAPs.

## 2. The shots

**Generator.** `scripts/bt_synth_slotb2.py --set FC` names the two source plans: the fresh set's `d23` cell (the LT
recipe: X_bda construction, ID share 87, first IDs only, CFO 0, offset 50.25 us, +23 ppm drift; 40 MS/s on 2439.0 MHz;
12 s). The stems are `F_13B6FA_d23_n3101` and `F_66C0A6_d23_n3102`, with schedule seeds 122626 and 123154 and plan
(noise and carrier-phase) seeds 3101 and 3102. The shots were rendered from commit `4bf3af8` with

    python -B scripts/bt_bench.py shot --lap 13B6FA --lap 66C0A6 --out /media/user/4TB/sdr-synth-tmp/bench/shots

`make_shot` renders the plan with no noise, scales it by 1 / `amplitude_for(20)` = 4.0 so that every burst has
|iq| = 1, and keeps the first 400,000,000 samples (10.0 s). The source file's noise is never generated. A burst is
**whole** (`in_shot` true) when it is rendered and clear of both ends of the shot by 120 samples.

| | bench_13B6FA_d23 | bench_66C0A6_d23 |
|---|---|---|
| `.cf32` sha256 | `d692b5a9f88f4539a2ad14e2e2cc05cdbdd92d5bed03278a86b4eb6810fd59b3` | `0eeae1cdb46bf1b239b5cda3c97aae26c3bbc12dd9b93d2ec5882c837811e654` |
| `.json` sha256 | `88f3642055f36f2d28c8a463362eb9b374188b3fdeef8d971644ae6f2053227b` | `b54efdb38b22f8d07e78af6a40e2e8bc8b4bfce9d6946d3c6f5d6f85892ed840` |
| samples, peak \|iq\| | 400,000,000, 1.0000001 (float32) | 400,000,000, 1.0000001 (float32) |
| whole bursts (minimum 4,500) | 4,948 | 6,524 |
| whole IDs (minimum 4,000) | 4,186 | 5,534 |
| masters / slaves | 381 / 381 | 495 / 495 |
| bursts cut by the shot's end | none | none |
| bursts on 2439 MHz | none | none |
| channels used | 14 | 17 |
| fewest bursts on one channel (minimum 80) | 130 (2437 MHz) | 176 (2445 MHz) |

Whole bursts per channel, in MHz:

- **13B6FA:** 2433: 160, 2434: 485, 2436: 569, 2437: 130, 2438: 490, 2440: 573, 2441: 166, 2442: 464, 2444: 381,
  2445: 158, 2446: 461, 2448: 385, 2449: 148, 2450: 378.
- **66C0A6:** 2428: 480, 2430: 424, 2432: 464, 2433: 200, 2434: 418, 2436: 450, 2437: 182, 2438: 527, 2440: 466,
  2441: 224, 2442: 525, 2444: 470, 2445: 176, 2446: 416, 2448: 478, 2449: 208, 2450: 416.

66C0A6 uses 2428 and 2430 MHz, 11 and 9 MHz below the centre. No earlier bench shot went below 2432 MHz; 2450 MHz,
11 MHz above the centre, has been used before.

No burst can be on 2439 MHz by construction: `draw_train` refuses any page train with the centre channel 37 in it,
and the connection channels are fixed at 31, 35, 39, 43 and 47 (`X_CHANNELS`). Both shot sidecars confirm it.

**Roles.** The sidecars carry exactly three `role` strings: `id_page` (the ID), `master` and `slave`. The string for an
ID is `id_page`, not `id`.

**Sidecar generator commit.** Both sidecars record `generator_commit` `4bf3af8`, a clean commit: the shots were
rendered after it was committed.

`bt_bench_play.py SHOT --check --pad-db 0 --level -68` passes for both shots. It opens no radio.

## 3. Pins

| File | sha256 |
|---|---|
| `scripts/bt_bench.py` (the aligner is its `align` command; this version adds the two SHOTS entries) | `e0cd0501fbe275674fb95453da07764f3b28f266d8f23410100310f12ed8cc19` |
| `scripts/bt_bench_play.py` (unchanged since `968c4ff`; the copy on worklaptop1, which plays, has the same sha256, checked 2026-10-25) | `7ca87aeb5064505d7e13a7cf8c36ccc9830c608df04605f340ab6758dafee761` |
| `scripts/bt_bench_session.py` (unchanged since `8028c4f`) | `0ca31485f08c8cb9cfe027de84f987c090bbd7f53bcac4102f53f0e2039b892f` |
| `scripts/bt_synth_slotb2.py` (the plans; adds `--set FC`) | `423eafad75c1e0d22b7d41131298bf0d837f93401136ea1c01258ce8d123175b` |
| `scripts/bt_synth_slotb.py` (the renderer) | `dc4c9d612cd0fc131f0725c3fd329c042fc8663e7588c2ea69a03e6fb813b06e` |
| `scripts/bt_synth.py` | `9c15d81ae80e0fc91cd552a8640ef0f9ea0627986c8fb36db2ebb7b13f88e14c` |
| `scripts/bt_synth_interf.py` (`amplitude_for`) | `974c4a7c71bbb051960921b5bed3bf1f040276a924a539f1017258d121e683e4` |
| `scripts/bt_ota_hop_check.py` (the recorder) | `8d1def95f8caa8fb7799cabbce02d37c2cd4386fd7e48b0d6b6d6e907e9ab23b` |
| `scripts/bt_tx_hop.py` (the player's stop handling) | `390c968f22301040d2cc4fad3c0bbed898a2ebdea9a843d4499064e66dc305b7` |
| `apps/bb60_source.py` | `f0021948226d3b73217df50e00162cfb727ef520085b8f937e09ccfe8e3e1806` |
| `apps/vsg_sink.py` | `493e0c19ff26dd6850041ab67f58135a2b9b629a64bae8f2580c36d51b1ec1a2` |
| `apps/bt_br_frame.py` (sync words, access codes) | `aa8e8aabb53698249c8b47ee71ccef8e637a2874f69842f854cef7c908ec6c9b` |
| `scripts/bt_freshset2_select.py` (the LAP rule) | `5623df303507e7e86434f854d422ce9cd3ee008bf5f220c74668438f29fabc11` |
| `knowledge/freshset/selection2_output.txt` | `0351cae795d99eed81a0b15e1730c6d422c7d58e97cafbfe15f8db4a251e2aa8` |

The decision file `diag/diag_s4_bench_decide.py` and the allowed-keys script are bluey's, and pinned there.

**The aligner's accept rules** (constants in `bt_bench.py`):

- It searches for the shot within ±10 kHz (`CFO_SEARCH_HZ`, 1 kHz steps) of the carrier the player's sidecar says it was
  put on.
- A burst is found at a normalised correlation of at least 0.4 (`MIN_NORM`) and within 3.0 samples of the clock line
  (`OFF_LINE_SAMPLES`). A burst not found or off the line is measured again over ±4,000 samples (`W_WIDE`).
- It refuses the recording when:
  - fewer than 99.9 % of the whole bursts are found (`MIN_FOUND`);
  - any of the first or last 20 bursts is not found (`STEP_MIN_SIDE`);
  - any burst is off the line;
  - a line-plus-step fit finds a step of 0.5 sample or more (`STEP_SAMPLES`);
  - a quadratic departs 0.3 sample from the line (`BEND_SAMPLES`);
  - the timing scatter is over 0.8 sample (`MAX_JITTER`);
  - the BB60D's record shows any lost samples;
  - the player's sidecar does not say `sent`, or names another shot's sha256;
  - the recorder's record names another file.
- The crop keeps 0.25 s either side of the shot (`CROP_MARGIN_S`).

## 4. The crop and the sidecar frame

**The crop is a pure sample-range cut.** `write_crop` copies capture samples `[first, last)` byte for byte as
complex64 (`<c8`): no derotation, no resampling, no frequency or phase correction, and no residual written into the
samples. `first` and `last` are the aligner's clock line at the shot's first and last sample, widened by 0.25 s each
way. The capture's samples are the BB60D's own complex64 output at 40e6 on 2439.0 MHz, written unchanged.

**The 0.25 s margin is not enforced.** `crop_bounds` clamps it to the capture's ends, and the aligner does not refuse
a crop whose margin was clamped. A shot running past the capture's end should be refused, because the last 20 bursts
must be found, but that path has not been tested. On the bench it has never been close. By the two hosts' clocks (NTP-synchronised, a few ms apart), the shot
started 7.3-7.5 s into the 30 s capture in the stage 4 sitting and 10.3-15.5 s in on 2026-10-23, and ended 17.3-25.5 s
in. That leaves at least 4.5 s of capture either side.

**The sidecar frame.** In the crop's sidecar (`align/NAME.json`), every burst's `start_sample` is an integer index
into the crop file at 40e6 samples per second, counted from the crop's first sample:
`start_sample = floor(start_exact)`, with `timing_frac = start_exact - start_sample`. `start_exact` is the burst's exact
shot start mapped through the aligner's clock line, and `start_us = start_exact / 40`. `shot_start_sample` keeps the
burst's index in the shot. `capture_crop_first_sample` gives where the crop starts in the full recording. Only whole
bursts (`in_shot`) are listed. Their roles are `id_page`, `master` and `slave` (section 2).

## 5. Offsets, draws, clocks, warm-up

- **Offset sign.** The VSG60A is tuned to 2439.0 MHz + `--vsg-offset-khz`, from its own synthesiser, not by rotating
  the samples. The BB60D stays on 2439.0 MHz. A **positive** offset puts the bursts **above** the BB60D's centre, so
  they appear in the capture at +offset Hz. The player's sidecar records it as `vsg_offset_hz`.
- **The draw rule.** Draws of a cell play the same shot file at the same commanded offset. Nothing is randomised
  between them. The start time is when the player is run after the recorder starts: the session tool starts the 30 s
  recording, and the player is run by hand straight after. Earlier sittings put the shot 7-16 s into the capture. The
  reference error is whatever the two free-running references are at that moment. It is neither set nor chosen.
- **Reference clocks: not locked.** Both instruments run on their internal references; the player's sidecar records
  `vsg_reference` "internal (free-running)". There is no 10 MHz cable between them: the BB60D's rear SMA can output
  10 MHz and the VSG60A takes 10 MHz on a BNC, but no SMA-to-BNC cable is on the bench. The bench has measured -0.36 to
  -0.38 ppm between them, with the carrier error equal to -2439 MHz times the clock error, as when each instrument's LO
  and sample clock share its own reference (`knowledge/signal-hound-specs.md`, `knowledge/bluetooth.md`).
- **Warm-up.** Proposed, pending the user's confirmation for the hardware: both instruments powered and connected for
  at least 30 minutes before the first play, then one throw-away start (the first VSG60A play after idle took about 18 s to start). Signal Hound
  specifies no warm-up for measuring; its field calibration asks for 15 minutes. The BB60D recalibrates when it is
  opened, which happens at every recording.

## 6. What the VSG60A does during a recording

Each scored recording runs in this order:

1. **Before the player is run:** the VSG60A is closed, with its RF output off. The last close set RF off and the level
   to -120 dBm.
2. **Opening:** the player opens it, programs the rate and the frequency at -120 dBm, sets the level, then turns RF on.
3. **From RF on to the send:** RF is on with no waveform loaded. Like any direct-conversion transmitter, it may then
   emit its LO feedthrough at the tuned frequency (`CLAUDE.md`: amplitude zero is not RF off). This interval was never
   measured; it lasts no longer than the device takes to start the send.
4. **The shot:** played **once** (`vsgOutputWaveform`, a one-shot), 10.0 s at 40 MS/s.
5. **After the shot:** the player aborts, turns RF off, sets -120 dBm and closes the device.

`tx_start_unix` (taken just before the send) and `tx_stop_unix` (taken after the close) bracket the shot by the host
clock. The capture runs on for the rest of its 30 s with RF off.

**The noise-only recording:** the cable stays connected, the player is not run, the VSG60A stays closed with RF off,
and the BB60D records 30 s at 40e6 on 2439.0 MHz. The session tool has no noise-only step: its `start` needs a LAP, a
slot and the level rule. So that file would be made with `bt_bench.py record NOISE.cf32 --seconds 30` (gain 60 %, the
session's recorder), then hashed and copied to the drive beside the sitting with a sums file of its own, outside the
session log's chain. Putting it inside the chain would take a change to `bt_bench_session.py`, which is pinned above.

## 7. The schemas

- **cf32:** interleaved little-endian float32 I and Q (numpy `<c8`), no header. Shots are 400,000,000 samples.
  Recordings are 30 s at 40e6, 1,200,000,000 samples (9.6 GB). Crops run from the shot -0.25 s to the shot +0.25 s,
  about 420,000,000 samples.
- **Shot sidecar** (`bench_<LAP>_d23.json`): the source plan's sidecar, with `in_shot` on every burst and a
  `bench_shot` block (`name`, `source_stem`, `n_samples`, `scale`, `peak_abs_iq`, `sha256`, `n_in_shot`,
  `cut_bursts`, `generator_commit`, `meaning`). Per burst: `index`, `start_sample`, `timing_frac`, `symbol_phase`,
  `channel`, `channel_mhz`, `ptype`, `role`, `sender`, `half`, `clk`, `lap`, `uap`, `rendered`, `in_window`,
  `air_bits_length`, `cfo_hz` (planted), `id_end_sample`, `exchange_id` and the header fields.
- **Player sidecar** (`NAME_tx.json`): `name`, `shot`, `shot_file`, `shot_sha256`, `shot_samples`, `shot_seconds`,
  `vsg_center_mhz`, `vsg_offset_hz`, `vsg_level_dbm`, `pad_db`, `level_at_receiver_dbm`, `vsg_sample_rate`,
  `vsg_reference`, `vsg_serial`, `mode`, `sent`, `state`, `tx_start_unix`, `tx_stop_unix`, `player`.
- **Recorder record** (`NAME.cf32.rec.json`): `file`, `center_mhz`, `sample_rate`, `gain_percent`,
  `record_start_unix`, `recorded_s`, `lost_sample_overflows`, `adc_overflows`.
- **Crop sidecar** (`align/NAME.json`): the shot sidecar's keys, less the source file's noise and grid keys. Whole
  bursts only, in the crop frame (section 4), each with `found`, `reason`, `measured_start_exact`, `norm`,
  `cfo_planted_hz` and `cfo_hz`. Top level: `capture_crop_first_sample`, `clock` (`delay_at_shot_sample_0`, `ppm`,
  `jitter_samples`, `step_samples`, `bend_samples`), `cfo` (`vsg_offset_hz_set`, `instrument_hz`,
  `instrument_spread_hz`, `reference_error_hz`), `tx`, `recording`, `aligner_commit`.

## 8. The dry run (no radio)

For each shot, a capture was made with no noise, no clock error and no carrier offset: 0.5 s of silence, the shot,
then 0.5 s more. The commands were

    python -B scripts/bt_bench.py dryrun SHOT.cf32 --out dry_LAP.cf32
    python -B scripts/bt_bench.py align dry_LAP.cf32 SHOT.cf32 --dry dry_LAP.cf32.planted.json --out align

| | found | off the line | accepted | step, bend, jitter (samples) |
|---|---|---|---|---|
| bench_13B6FA_d23 | 4,948 of 4,948 | 0 | yes (exit 0) | 0.000, 0.000, 0.000 |
| bench_66C0A6_d23 | 6,524 of 6,524 | 0 | yes (exit 0) | 0.000, 0.000, 0.000 |

Each crop was compared with its capture: crop = capture samples [10,000,000, 430,000,000) for 13B6FA and
[9,999,999, 430,000,000) for 66C0A6, byte for byte (`cmp`). The scratch captures and crops were deleted afterwards.

## 9. Estimates for the sitting

Planned: one throw-away start (not recorded), 2 calibration plays, 20 scored recordings and 1 noise-only recording.

- **Time:** about 4 minutes per recording: on 2026-10-23, 16 recordings took 64 minutes, start to close. So 23
  recordings take about 1 h 35 min. With the 30-minute warm-up and the throw-away play, the sitting is about 2 h 10 min.
  Each retake adds about 4 minutes.
- **Disk on the drive:** a recording is 9.6 GB and an accepted one's crop 3.4 GB, about 13.0 GB together.
  - 2 calibrations and 20 scored recordings, all accepted: about 286 GB.
  - The noise recording: 9.6 GB.
  - Total: **about 296 GB**. Every retake adds 9.6-13 GB, so the worst case, all 20 retakes, is **about 556 GB**.
  - The drive has 1.5 TB free.
- **Disk on this laptop:** the session tool deletes each recording's large files once their copy on the drive is
  verified, so the laptop never holds more than about 13 GB at a time. It has 89 GB free.

## 10. The smoke on a calibration crop

SDR can run a command bluey supplies on each calibration crop and report PASS or FAIL per LAP, and nothing else. It
would run on this laptop, against the crop on the drive, from a bluey checkout at a commit bluey names. SDR reads the
command and what it runs before running it; it must open no radio and write nothing outside a scratch folder. The
calibration crops and their sidecars stay with SDR.
