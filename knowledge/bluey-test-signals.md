# Bluetooth Classic test signals for bluey-ox-walker: the plan

One of the notes in `knowledge/`. What applies everywhere, and which
file covers what, is in [CLAUDE.md](../CLAUDE.md). Why the VSG60 can
send this at all, and what it cannot, is in [bluetooth.md](bluetooth.md).

Agreed with the bluey-ox-walker session on 2026-10-01. Stage 1 is built:
the encoder, its test and the file writer are described under [what
exists](#what-exists), and the first two files went to bluey on
2026-10-02.

## Why

bluey-ox-walker (`/data/python/bluey-ox-walker`) decodes Classic
Bluetooth from SDR captures. Its `knowledge/architecture.md`, lines
98-107, leaves the on-air demodulation of plain Basic Rate multi-slot
packets (DM3, DH3, DM5, DH5) unverified. The Cypress and Broadcom chips
it listens to never send those packet types as Basic Rate, only EDR.

A signal we generate, with every field known in advance, settles that,
and later tests its hop-sequence code too. The VSG60 cannot hold a link,
since it cannot hear the other side, but one side of a link is all a
decoder test needs.

## Who does what

| | SDR (this repo) | bluey-ox-walker |
|---|---|---|
| Encoder | the whole Basic Rate packet: access code, header, payload | - |
| Signal | GFSK, impairments, cf32 files, then the VSG60 | - |
| Grading | - | a grader that runs its pipeline over our cf32 and sidecar and reports hits against truth |
| Reference | - | `lib/hop/variant_e.c` and `lib/hop/hop_kernel.py`, its hop kernel, for stage 3 |

bluey's `tests/test_libbtbb_multi_slot_roundtrip.py` is **not** a full
encoder. It splices a synthesised payload
(`diag/diag_libbtbb_multi_slot_roundtrip.py`,
`build_multi_slot_payload_coded`) behind a real header taken from a
capture. The header is ours to build from scratch, checked against
libbtbb's decode.

## Stages

1. **cf32 files, no RF.** A fixed-channel train of DH3 and DH5 bursts,
   written as 20 MS/s complex float32 as if recorded by a BB60D centred
   on 2441 MHz. bluey replays it with
   `apps/scan_live.py --iq-file <file> --sample-rate 20e6`.
2. **The same train on air** from the VSG60, into bluey's BB60D. The BB60D
   and VSG60 cannot stream on one host, so the VSG60 goes on the work
   laptop, `ssh worklaptop1` ([radios.md](radios.md#vsg60-notes)). It goes over a cable with
   20-30 dB of pad, not an antenna
   ([bluetooth.md](bluetooth.md#the-bench)).
3. **Hopping under a narrow AFH map**: 20-40 adjacent channels, so every
   hop lands inside one VSG60 tuning and is a frequency shift in
   baseband. The master's address and clock are chosen, and the sequence
   comes from a hop kernel written here from the Core specification's text
   (`apps/bt_hop.py`), not from bluey's, so that the answer the files hold
   is not bluey's own. See [Stage 3](#stage-3-narrow-afh-hopping-2026-10-04).

## The file format bluey asked for

- **Rate and centre**: 20 MS/s cf32, centre 2441 MHz.
- **Never on the centre**: bluey's polyphase filterbank loses its centre
  bin (its `knowledge/hardware.md:29`). Put the signal on a channel
  centre a few MHz away - 2437 or 2445 MHz.
- **A JSON sidecar per file** with the ground truth:
  - LAP, UAP and CLK.
  - The channel and the packet type.
  - The payload bytes.
  - The true start offset of each burst, in samples.
- **Payload**: `SEQ=<hex counter> <base64 ramp>`, the counter in hex, as
  bluey's own rigs send.
- **Impairments**: a frequency offset, a fractional-sample timing offset,
  and noise. A clean synthetic burst is easier than anything on air and
  would prove too little.

### Where the files go, and the sidecar's keys

bluey's half of this plan is in its
`knowledge/synthetic-tx-plan.md`. Its grader will be
`bin/grade_synthetic_tx.py`. Files go into its tree, in two folders it
keeps out of git, with the same stem for both:

- `/data/python/bluey-ox-walker/data/iq/synth_<name>.cf32`
- `/data/python/bluey-ox-walker/data/sidecar/synth_<name>.json`

The keys it grades against, as agreed with bluey on 2026-10-02:

| Key | |
|---|---|
| `lap`, `uap` | integers |
| `clk`, `clk_convention` | `clk` is the native CLK27-0, at the start of the burst it belongs to; the top-level one is burst 0's |
| `channel_mhz`, `bt_channel` | |
| `sample_rate`, `center_mhz`, `slot_samples` | |
| `cfo_hz`, `timing_frac` | the impairments added; `timing_frac` delays every burst by that fraction of a sample |
| `snr_db`, `snr_bw_hz` | burst power over the noise in 1 MHz; over the full 20 MHz the noise is 13 dB more |
| `modulation` | scheme, BT, h and symbol rate |
| `generator_commit` | the SDR commit the file came from, `-dirty` if uncommitted |
| `bursts` | one entry per burst, below |
| `afh_map`, `afh_instant` | stage 3 only: the **first** map and its CLK[27:1] instant; bluey applies a map from `clk1_27 >= afh_instant`, as its variant E does. A file with a map change has more: see `afh_maps` under Stage 3 |

Each burst carries:

| Key | |
|---|---|
| `start_sample` | the first sample of the preamble, before `timing_frac` is added |
| `ptype`, `clk`, `lt_addr`, `flow`, `arqn`, `seqn` | |
| `header18` | the 18 header bits as one integer, `LT_ADDR \| TYPE<<3 \| FLOW<<7 \| ARQN<<8 \| SEQN<<9 \| HEC<<10`, before whitening and FEC - what libbtbb's `btbb_packet_get_header_packed` returns |
| `payload_hex` | the payload body: a 4-byte L2CAP header (length, CID `0x0040`), then `SEQ=%08X ` and a cyclic run of the base64 alphabet, as bluey's own rigs send |
| `payload_full_hex` | payload header, body and CRC before whitening - what libbtbb's `btbb_get_payload_packed` returns |
| `air_bits` | the bits into the modulator, first preamble bit to last CRC bit, as a `0`/`1` string. bluey feeds these to libbtbb before grading anything on the RF side, so an encoder fault shows up as one |

bluey's timing rules, which its blind UAP recovery depends on:

- **Every burst on a master slot** (CLK1 = 0) of the 625 us grid, the
  clock advancing 2 a slot: a DH5 every 6 slots, a DH3 every 4.
- **The grid half a slot off sample 0**, so `start_sample % 12500` is
  6250 at 20 MS/s. bluey maps a time to its slot with `int(t / 625 us)`,
  and a burst on a boundary flips slot on a few samples of jitter.
- **Noise from sample 0, and over 1 ms of it first.** bluey sets its gain
  from the first 1000 samples.
- **At least 30 bursts**, and the first file clean (no CFO or timing
  offset, 30 dB) so encoder faults are not mixed up with impairments.

A key we cannot produce gets left out, and bluey drops it from the
grading. Message bluey's session the path of each new file.

## The modulation

Basic Rate GFSK: 1 Msym/s, BT = 0.5, modulation index about 0.32
(0.28-0.35 allowed), from Vol 2 Part A §3.1.1, p. 434. bluey's README
once said BT = 0.35; that was wrong, and they have corrected it.

## What the encoder must get right

These are from bluey's `knowledge/pitfalls.md` and `lessons.md`, each
found the hard way:

- **The header is whitened first, then FEC 1/3 is applied**: 18 bits,
  then 54.
- **The HEC polynomial is 0xA7** (D^8 + D^7 + D^5 + D^2 + D + 1, its
  top term left off), preset with the UAP; header fields go out LSB first.
- **Whitening is seeded from the clock** (CLK6-1), not from the channel
  as in LE.
- **The length field of a 2-byte payload header is 10 bits**, then 3
  RFU (Vol 2 Part B Figure 6.13), for every multi-slot packet. bluey's
  note says 9 except in 2-DH5 and 3-DH5; DH3 and DH5 never need the
  tenth bit, so the two readings give the same bits for them.
- **A passing FEC vote does not prove the bits are right.** Grade
  against the sidecar, not against the decoder's own confidence.

## What exists

- **`apps/bt_br_frame.py`**, the encoder: access code, header, payload,
  CRC, whitening, FEC 1/3 and 2/3, for NULL, POLL, DM1, DH1, DM3, DH3,
  DM5 and DH5;
  each `Packet` gives its own sidecar entry. It also holds the GFSK
  modulator, `gfsk`, which evaluates the Gaussian pulse at each sample's
  own time, so a fractional timing offset is exact rather than
  interpolated.
- **`scripts/test_bt_br_frame.py`**, which holds it to the Core spec's
  Vol 2 Part G sample data (bluey keeps the same as
  `knowledge/sample_data.md`):
  - all 130 access codes;
  - the 20 HEC and header pairs;
  - the CRC;
  - 128 bits of whitening;
  - the FEC 2/3 codewords;
  - the complete DH1 and DM1 packets.

  Part G has no whitened packet, so libbtbb, where it is installed, has
  to decode a whitened packet of every type at six clocks to the same header
  and payload bytes, and refuse each one a clock tick off. That check is
  what proves CLK1 lands in the register's position 0. The test also
  holds the modulator to a discriminator and a short file to its
  `air_bits`.
- **`scripts/bt_synth.py`**, the file writer, into bluey's folders by
  default or `--out` anywhere. Options:
  - packet type: `--ptype`, DH1, DM1, DH3, DM3, DH5 or DM5;
  - number of bursts: `--bursts`;
  - impairments: `--cfo`, `--timing-frac`, `--snr`;
  - symbol phase: `--start-offset`, in whole samples;
  - `--h`, `--seed`.

  `scripts/bt_synth_multi.py` writes the many-device, header-only and
  noise-only files described below.
- **Delivered on 2026-10-02**, 40 DH5s each on 2445 MHz in a 2441 MHz
  file, LAP `0x9E8B33`, UAP `0x47`:
  - `synth_clean_dh5`: no impairments, 30 dB.
  - `synth_imp_dh5`: +12 kHz, 0.37 of a sample, 20 dB.

  All 114,800 bits of the clean file came back out of its samples equal
  to its `air_bits`.

  bluey graded both the same day:
  - **Every packet passed libbtbb at the truth bits**, 40 of 40, so the
    encoder and the sidecar held up.
  - **Its detector found none of them.** Each burst's bits began at
    sample 10 of the 20-sample symbol grid. A shift of 7 samples gave 40
    of 40, with headers exact at the true clock.
  - **Its payload timing loop drifted** about 1700 bits into each DH5.

  Both faults are on bluey's side.
- **Then, on request, eight more files.** All are at the same LAP, UAP and
  channel.
  - **DH5 at symbol phases 0, 5 and 15** (`--start-offset`), clean, plus
    an impaired one at phase 5. The first pair stays as the phase-10
    regression.
  - **DH1 and DM1**, clean and impaired, 40 each at phase 5. Both are 366
    air bits, which is the only length bluey's blind UAP recovery takes
    whole.
  - **The sidecar gained `start_offset` and `symbol_phase`.** Sample n of a
    burst is the pulse at `(n - start_sample - timing_frac) / 20` symbols,
    so bit k is centred on `start_sample + timing_frac + 20k + 10`.
  - bluey's results on these:
    - **DH1 and DM1 decoded end to end**, impaired files included, 40 of
      40 byte-exact. This was bluey's first plain Basic Rate payload
      demodulated from IQ.
    - **Its blind UAP recovery found `0x47`** after one or two packets.
    - **The DH5s** were detected at phases 0, 5 and 15. Their payloads
      still fail, from the timing loop and the 400-bit cap.
    - **The dead zone is phases 9-11 on a clean file**, and wider on an
      impaired one. bluey measured why: its channelizer's 1 MS/s
      samples fall on input samples ≡ 0 mod 20.
      - At phase 0 those samples sit on the bit boundaries, so each
        one-sample discriminator spans exactly one symbol.
      - At phase 10 they sit on the bit centres, so the discriminator
        straddles two symbols and partly cancels.

      I had guessed the reverse. bluey's `knowledge/pitfalls.md` has the
      detail.
- **Two sweeps, to test bluey's fixes.**
  - **`clean_dh1_ph08` to `ph12` and `ph18` to `ph02`**: the symbol phase
    is the only thing that changes. They cover the detector's dead zone
    and the zone of the half-symbol second grid it plans to add.
  - **`dh5_snr25`, `20`, `16`, `12` and `08`** at phase 5.37, +12 kHz:
    the SNR is the only thing that changes, with one noise seed scaled,
    to choose a timing-loop gain. `dh5_snr20` is `imp_dh5_p05` again.
    `dh5_snr20_s3`, `_s4` and `_s5`, plus `dh5_snr16_s3` and `_s4`, repeat
    the 20 and 16 dB files with other seeds. A seed changes each burst's
    carrier phase as well as the noise.
  - **What the sweep showed.** With perfect timing, a plain discriminator
    makes at most 2 errors in 114,800 bits at 20 dB, whatever the seed.
    So bluey's 35-37 of 40 there is its timing loop, not the noise.
- **On 2026-10-03, files for bluey's gate against a false UAP.** A device
  heard only a few times, on headers alone, can converge on the wrong UAP.
  bluey's iPhone LAP `C52DA1` settled on `0x41` when the truth was `0x1B`.
  One device per file can never show that, so these come from a second
  writer, `scripts/bt_synth_multi.py`:
  - **`md_a1` to `a3` and `md_b1` to `b3`.**
    - 6-8 masters per file, with 3 to 80 packets each, about 70% NULL
      and POLL.
    - Each master has its own SNR (6-20 dB), CFO, timing offset and
      LT_ADDR, its own slot grid and clock, and 3-5 channels it hops
      between at random. That is not a hop sequence.
    - Set A tunes the gate and set B is held out. They share no LAP, UAP
      or seed.
  - **`hdr_snr06` to `14`, seeds `s1` to `s3`**: one master, 60 NULL and
    POLL, a new LAP and UAP in every file. The carrier phases are fixed
    across files, so a seed changes only the noise and who the device
    is.
  - **`noise_s1` to `s3`**: 2 s of the same floor. Their seeds are
    1001-1003 so they share no noise with the `hdr` files.
  - **DH3, DM3 and DM5**, a clean and an impaired file of each, once
    bluey's 400-bit window was fixed.

  Each file has one noise floor, what makes a burst at
  `bt_synth.AMPLITUDE` 20 dB, and a device's SNR sets only its own
  amplitude. The `md` sidecars add `devices` and give every burst its
  `lap`, `uap`, `channel_mhz` and impairments. NULL and POLL are real
  header-only packets, 126 bits, which libbtbb takes at the true clock and
  refuses a tick off.

- **Then the cases a tracker gets wrong.** bluey's tracker never settled
  on a wrong UAP in any of those files. Its three known wrong ones, on
  real captures, all came from encrypted links and from devices heard on
  one channel or rarely. `bt_synth_multi.py hard` writes `hard_a1` to
  `a3` (tuning) and `hard_b1` and `b2` (hold-out). Every master has a
  role:
  - **A strong-weak pair on adjacent channels**, the weak one's bursts
    inside the strong one's.
  - **Thin masters**, on one channel.
  - **A pair that collides** 30-70 % on a shared channel.
  - **A DH3 every 4 slots.**
  - **Ordinary hoppers.**

  About half of every file's masters are encrypted-looking: a valid
  header over a payload of random bytes the CRC rejects, built with
  `Packet(raw_payload=...)`. Each burst says `payload_valid`, `collision`
  and `leakage`. Half the masters with their own slot grid sit within
  150 samples of half a slot.

  bluey's user chose stage 2, over the air, for the clock offset rather
  than a ppm offset in the synthesis. There is no 10 MHz reference cable
  yet. The VSG60 has BNC and the BB60D SMA, so the first run will be
  free-running.

## Stage 2: the first over-the-air runs, 2026-10-03

- **The rig.** `scripts/bt_tx.py` on the work laptop (`ssh worklaptop1`)
  plays a 256-slot loop of 42 DH5s on 2445 MHz from the VSG60 at
  2441 MHz, 20 MS/s, through an antenna. `scripts/bt_ota_check.py` on
  the bench machine records the BB60D at 2441 MHz, 20 MS/s and 60 %
  gain, through an antenna, and grades every burst against the loop's
  truth. The references are free-running; there is no 10 MHz cable yet.
- **The level.** At these antennas' spacing, -40 dBm was below the
  room's noise and -30 too weak to decode. -20 dBm gave about 9 dB in
  the channel and -10 dBm about 19 dB: the link is linear. The BB60D sat
  near -72 dBFS with no ADC overflow, so it was nowhere near overdriven.
  The earlier reading that the level did not scale from -30 to -20 came
  from a percentile that was mostly noise at those levels.
- **What came through at -10 dBm.**
  - Every burst sent was found and identified in order.
  - libbtbb took every header at the true UAP and clock.
  - About 57 % of DH5 payloads passed the CRC byte-exact.
  - The raw bit error rate was 1e-3, from a plain discriminator with no
    timing recovery, about what the synthetic files give at 16-17 dB.
    The errors are a few per burst, spread out, not interference.
  - The carrier offset was 70-280 Hz between runs.
  - The sample clocks differed by **-0.37 ppm**, the same in two
    separate runs, with half a sample of jitter left.
- **At 0 dBm**, the same antennas, about 30 dB in the channel and still
  no ADC overflow:
  - 97 % of payloads passed the CRC byte-exact, 498 of 512.
  - The raw bit error rate was 2e-4.
  - The carrier offset was +157 Hz.
  - The clock offset was -0.36 ppm again, with a third of a sample of
    jitter.

  That is the level for clean over-the-air data at this spacing.
- **Stopping.** `bt_tx.py` turns SIGTERM, SIGHUP and SIGINT into a flag,
  so a `timeout`, a dropped ssh session or Ctrl-C stops the repeating
  waveform and still writes the sidecar, marked `interrupted_by`. A
  repeat the VSG60 plays from its own memory must not outlive the
  script.

## Stage 2: the long captures for bluey, 2026-10-04

Two 30 s recordings at 0 dBm, the same antennas and the BB60D at 60 %
gain, with no ADC overflow in either. Both are in bluey's `data/iq`, each
with a truth sidecar in `data/sidecar` (34-37 MB, since every burst
carries its air bits):

| File | Packet | Bursts sent | Found | libbtbb exact |
|---|---|---|---|---|
| `ota_dh5_0dbm_20261004_070334` | DH5 | 7875 | 7838 | 7218 (92 %) |
| `ota_dh3_0dbm_20261004_070436` | DH3 | 11999 | 11922 | 10141 (85 %) |

`synth_ota_dh5_ref` and `synth_ota_dh3_ref` are the same two trains as
clean synthetic files, two loops each at 30 dB, for comparison.

- **The truth sidecar** lists every whole burst the loop sent while the
  recording ran, found or not, in the synthetic files' format. Where each
  one starts comes from the sample clock fitted to the bursts that were
  found, not from the detector, so a burst the plain demodulator missed
  is still in the truth with `found: false`. `clk` is exact in CLK6-1 and
  nominal above it: the VSG60 started its loop at no particular clock.
- **Where a burst starts.** `start_sample` is the sample nearest the
  fitted start, with `timing_frac` 0 for the file as in the synthetic
  files, and `start_exact` is the fit itself to a thousandth. `symbol_phase`
  is the burst's own `start_sample % 20`: it drifts with the clock, so the
  file has none and the top-level key is null. Checked on a planted
  recording, with a known -3 ppm clock and starts at fractions of a sample:
  the clock to 0.01 ppm, starts within 0.13 sample (a mean of +0.09), and
  `start_sample` within one sample of the truth. The offset between a burst's
  start and the detector's peak is 49.5 samples on average, not 50: the peak
  is a whole sample, and the first version of the grader took 50, which put
  every start half a sample early and every `symbol_phase` one low. The
  reference files, whose starts are all whole samples, cannot show this.
- **The clock is the same as before:** -0.380 ppm in both files, within
  0.02 of the -0.36 to -0.37 of the earlier runs. Over 30 s that walks the
  symbol phase through every value, about 228 samples, so the dead zone
  bluey found at phase 10 is crossed during each recording.
- **Weaker than the 20 s run in the same room:** about 22 dB in the
  channel, against 30, and 85-92 % of payloads exact against 97 %. The
  carrier offset was -220 to -290 Hz, against +157 in the 20 s run.
  Nothing about the rig changed that was written down; a difference in
  antenna position is the likely cause and is not confirmed.
- **The misses are in clusters, not spread evenly.**
  - DH5 missed 37 of 7875: 16 within 0.14 s at 5.8 s, 16 within 0.14 s at
    19.8 s, 3 between 21.5 and 21.9 s, and 2 on their own. The bursts that
    got through beside them read the normal 22 dB.
  - DH3 missed 77 of 11999: 17 over the first 1.9 s, 30 over the last 4 s
    (both at the normal 22 dB), 16 within 0.16 s at 13.7 s with the
    survivors at 19 dB, and a few of 3. In one 0.06 s patch at 14.8 s the
    survivors read 13 dB.

  Most of them are therefore not a fade in level. Something short-lived on
  the channel is the likely cause; nothing was identified.
- **A grader bug, found here and fixed.** The first grading dropped any
  burst in the 12,000 samples after a boundary between chunks (the files
  are graded 40 M samples at a time): the earlier chunk skipped it as not
  its own and the later one as too near its start. Seven DH5 bursts were
  lost that way, all of them just after a multiple of 40 M. Each chunk
  now starts 100,000 samples early. The 0 dBm and reference files from
  before give the same results in chunks as whole, and the reference
  file gives 84 of 84 whatever the chunk size, boundaries on bursts
  included.
- `scripts/test_bt_ota_check.py` holds the grader to a planted recording:
  a known clock and starts at fractions of a sample, bursts silenced, the
  same rows at every chunk size, and a recording too short or a fit too wild
  to grade. The reference files cannot do this, since every start in them
  is a whole sample.
- `scripts/bt_ota_check.py --record N --capture NAME` now does the whole
  thing: records into bluey's `data/iq` and writes the truth. These two
  were recorded with a scratch script and graded afterwards, so their
  `receiver` block carries `adc_overflows: 0` and no record start time.

## Stage 3: narrow-AFH hopping, 2026-10-04

A master hopping inside a map of 20 adjacent channels, as six files, from a hop
kernel written here. The files themselves are not on the air; the same kind of train
went over the air on its own map, see Stage 3 over the air below.

- **What is in bluey's `data/`:** `synth_hop20_*.cf32` and `.json`, 40 MS/s, centre
  **2445.0 MHz**, map channels 33-52 (2435-2454 MHz), address UAP `0x47` LAP
  `0x9e8b33`, first clock `0x0123400`, seeds 3001-3006, `generator_commit`
  `b344872`. 1.07 GB in all, written in 22 s by
  `python scripts/bt_synth_hop.py --set stage3`.
- **The first set was wrong, and was replaced the same day.** It was centred on
  2444.5 MHz, the middle of the map. bluey's channelizer has 1 MHz bins
  centred on the capture centre, so at 2444.5 every channel sat half a bin off
  a bin centre and channels 34 and 35 shared one; its detector found no bursts
  in any file until the samples were shifted by 0.5 MHz. **The centre of a
  capture for bluey must be a whole number of MHz** (stage 2's 2441 was, by
  luck). `bt_synth_hop.py` now warns when it is not. 2445 rather than 2444
  because the second map's top band edge, 2458 MHz, is 0.5 MHz inside the BB60D's
  27 MHz at 2445 and 0.5 MHz outside at 2444. The files kept their names,
  and `generator_commit` was `eb37d55` in the first set.
- **bluey's results on them,** on the first set (2026-10-04): with its fixed lock
  (an adapted array indexed by the true slot) `hop20_dh1_long` and `hop20_dh5`
  give the true CLK[27:1], `596480` at burst 0; the old array gave the truth
  minus one, as `clock_lock_note` says. The stream being master-only, the new
  array ties the true slot and the one after it on every burst and a tie-break
  returns the true one, so these files fix the sign of the lock and nothing
  more: two-sided traffic has to confirm the rest. On the detector, shifted by
  0.5 MHz, its single grid was blind at symbol phase 20 of 40 (the half-symbol
  dead zone) and its dual grid found 95-98 % of bursts, the misses being
  the channel at DC. With the whole-number centre the channel at DC is channel
  43 (2445 MHz), in the map, and so is the VSG60A's carrier feedthrough.

  | File | Packet | Bursts | Length | Channels used | Size |
  |---|---|---|---|---|---|
  | `hop20_dh5` | DH5 | 100 | 377 ms | 20 | 120.7 MB |
  | `hop20_dh3` | DH3 | 120 | 302 ms | 20 | 96.7 MB |
  | `hop20_dh1` | DH1 | 400 | 502 ms | 20 | 160.7 MB |
  | `hop20_dh1_long` | DH1 | 1000 | 1,252 ms | 20 | 400.7 MB |
  | `hop20_imp_dh5` | DH5 | 120 | 452 ms | 20 | 144.7 MB |
  | `hop20_change_dh5` | DH5 | 120 | 452 ms | 23 | 144.7 MB |

  `hop20_imp_dh5` carries 12 kHz of carrier offset, 0.37 of a sample of timing,
  15 dB in 1 MHz, and every burst moved 7 samples. `hop20_change_dh5` changes
  from channels 33-52 to 36-55 at burst 60 (clock `0x1236d0`, CLK[27:1]
  `0x91b68`); the others use the first map throughout, from CLK[27:1]
  `0x91a00`. The first burst of the others is on channel 35.
- **Regenerating them gives the same bytes only within one numpy build.** The
  files came from the project's `gnu` environment (numpy 2.2.4). The system
  Python's numpy 1.26.4 writes the same bursts on the same channels with the
  same sidecars, but its samples differ in the last bit of a float: 46 % of
  samples, by at most 3e-7 of the burst amplitude, against noise 100,000 times
  larger. Compare files from one environment.
- **The sidecar adds these keys** to the single-channel ones, and `channel_mhz`
  and `bt_channel` at the top level are null, since no one channel holds:

  | Key | |
  |---|---|
  | `hopping`, `hop_channels` | `true`, and the sorted channels actually used |
  | `afh_map`, `afh_instant` | the **first** map and its instant, CLK[27:1] |
  | `afh_map_count`, `afh_maps` | every map, in order: `{instant, first_burst, channels}`. **Grade a file with a map change against `afh_maps`**, and against each burst's `afh_map_index`; `afh_map` alone is wrong from burst 60 on `hop20_change_dh5` |
  | `afh_instant_meaning`, `hop_kernel`, `address_for_hop` | in words; `uap << 24 \| lap`, of which the kernel reads A27-0 |
  | `clock_lock_note` | see below |
  | per burst `channel`, `channel_mhz` | the RF channel, 0-78, and 2402 + channel MHz |
  | per burst `afh_map_index`, `symbol_phase` | which map applied; `start_sample` modulo 40 |

  Every burst is on an adapted sequence from the first, so the 79-channel
  sequence a real master uses before an AFH instant is not in the files: it
  would leave the window. The slave's slot after each packet is silent, as in
  stage 1, so the rule that gives both slots of a pair one channel is not on
  the air; a packet is on the channel of its first slot for its whole length.
- **What a lock should return.** bluey told us on 2026-10-04 that its CLK27 lock
  follows a hop array equal to the Part G rule shifted by one slot, so on an
  adapted map it returns the true CLK[27:1] **minus one slot** (two clock
  ticks); on a basic sequence there is no shift. It is fixing its pair roles,
  after which the lock should return the true clock. Accept either. The
  sidecar's `clock_lock_note` says this, dated.
- **The kernel, `apps/bt_hop.py`,** is the connection-state selection box of
  Core v6.0 Vol 2 Part B 2.6, basic and adapted, from the specification's text
  alone. Four places where the text is ambiguous are resolved in its docstring:
  the bit order of a field, the sense of a butterfly, the order of the register
  bank, and the adapted sequence giving both slots of a pair one channel.
  What checks it:
  - Part G section 2's connection-state tables, all 15 (three addresses; the
    basic sequence and four adapted maps each): 7,680 of 7,680. Two builders,
    Sonnet and Grok, each wrote one; neither saw more than one address of the
    three, and both passed the rest. They agree on 360,000 random clocks and
    maps at any slot.
  - **libbtbb's own basic kernel**, a separate C implementation: 15,000 random
    addresses and 28-bit clocks, 10,000 master slots at this address from this
    clock, and slave slots, with no difference. This matters because Part G stops
    at CLK `0x40e` and never sets address bits A1, A4, A6, A12, A19, A21 or A22,
    all of which these files use.
  - **bluey's kernel**: no difference on master slots over Part G and over
    210,000 random inputs, including 20-, 30- and 40-channel maps. At slave
    slots of an adapted sequence its `single_hop_spec` differs from Part G (3,040
    of 3,072 entries); bluey confirmed it, and its lock does not use that path.
  - `scripts/test_bt_hop.py` holds all of this: 480 values copied from Part G, 56
    pinned channels in the region Part G does not reach, a comparison with
    libbtbb, and all 7,680 when `BT_HOP_PARTG` names the data.
- **A Grok review** (34 minutes, $1.46) planted recordings and recovered the hop
  sequence from the samples alone: 44 of 44 bursts on the channel the sidecar
  names at 30 dB, across DH5, DH3, DH1 and the map change. It found the kernel
  right and the tests weaker than the kernel. Every finding was checked here
  before a change: the AFH tests graded the kernel with the kernel (now libbtbb
  and the pins; seven changes to the kernel that every test passed before now
  fail); `afh_map` on a change file is only the first map (now said, with a
  count); a map naming a channel the window cannot hold was refused only if the
  sequence landed on it (now refused at once); and `generator_commit` called a
  tree clean when `git status` failed (now `-unchecked`).
- **The window.** A burst's channel plus or minus 1 MHz must lie within 0.36
  of the sample rate either side of the centre: 72 % of the rate in all, over
  which the VSG60A stays flat
  ([signal-hound-specs.md](signal-hound-specs.md)). At 40 MS/s on 2445 MHz that
  is 2430.6 to 2459.4 MHz, channels 30 to 56. The first map's bands run from 11
  MHz below the centre to 10 above, the second's from 8 below to 13 above, and
  channel 55's band edge, 2458 MHz, is 0.5 MHz inside the BB60D's 27 MHz
  (2431.5 to 2458.5), so the second map sits at the edge of what the receiver
  holds.
- **Symbol phase.** Every clean file has its bursts at symbol phase 20 of 40
  (`--start-offset` 0), and bluey reports that this is the half-symbol dead zone
  of its single-grid detector: it finds nothing there, and its dual grid finds
  95-98 %. Only `hop20_imp_dh5` is elsewhere (phase 27). Say if you want a copy
  of a file at another phase: it is one command with `--start-offset`.
- **Not checked:** page, inquiry and the response hop sequences, which
  `bt_hop.py` does not implement; and any sample of these six files on a radio.

## Stage 3 over the air, 2026-10-04

A hopping train from the VSG60A into the BB60D, 40 MS/s, on its own map: not
the files' 33-52 at 2445, because of the room (below).

- **A one-shot, not a loop.** A hop sequence depends on the clock up to
  CLK27, so a loop that restarted would restart the channels while a
  receiver's clock kept running. `scripts/bt_tx_hop.py` plays the whole train
  once (`send_waveform`; the VSG60A took buffers up to 3.3 GB, 10 s, at
  40 MS/s) and the recording holds it once, with noise before and after. The
  truth's `clk` is therefore the exact CLK[27:0] of every burst, which the
  stage 2 loops could not give, and nothing wraps.
- **The tuning: centre 2441.0 MHz, map channels 31-50** (2433-2452 MHz, 20
  channels, the AFH minimum), the stage 2 centre. A scan of the room showed a
  source above 2452 MHz constantly 20-28 dB over the floor (a Wi-Fi access
  point on channel 11, probably) and the BB60D sees only +-13.5 MHz at 40
  MS/s; 2433-2451 was clean. At 2441 channel 52's band reaches 14 MHz, so the
  map-change shot is 31-50 then 32-51. The VSG60A's carrier feedthrough, the
  BB60D's DC and the middle channel coincide, at 2441: **channel 39**.
- **The tools.** `bt_tx_hop.py` (nothing transmits without `--go`; a level
  above 0 dBm or one that is not a finite number is refused; the sidecar
  says `tx.sent`; run under `timeout -k 30`, the `-k` must exceed the shot,
  since a signal cannot interrupt `send_waveform`) and `bt_ota_hop_check.py`
  (grades a recording against the shot's sidecar and writes the truth).
  The grader finds the shot by votes from per-channel energy runs, tracks
  every burst on its own channel, and **refuses to write a truth, with a
  reason, when it could be wrong**: samples lost or gained in the recording
  (a step in the clock fit, a rate kink, scatter, a burst read well but far
  off the line, energy of the shot beyond the bursts found, or the recorder's
  lost-sample counter), a carrier more than 50 kHz off, a centre not a whole
  MHz. Two reviewers (Opus, Sonnet) and three fix rounds got it there: the
  first version wrote a confident wrong truth after a 40-sample drop. Known
  and accepted: a 5-sample shift of only the first or last burst writes that
  burst `found: false`, reason `off the line`, placed 5 samples wrong; a 0.5
  ppm change of clock rate over a short shot passes.
- **The captures** (0 dBm, the same antennas as stage 2, BB60D 60 % gain, no
  lost samples; each is the shot plus 1 s before and after, cropped from a
  20 s recording, `start_sample` from the first sample of the cropped file):

| File (`ota_hop_*_0dbm_20261004_*`) | Bursts | Found | libbtbb exact | Clock | Jitter |
|---|---|---|---|---|---|
| `dh5` (200 DH5) | 200 | 200 | 192 | -0.385 ppm | 0.30 sample |
| `dh3` (240 DH3; 1 ADC overflow event) | 240 | 240 | 230 | -0.375 | 0.31 |
| `dh1` (600 DH1) | 600 | 599 | 594 | -0.375 | 0.33 |
| `dh1_long` (2400 DH1, 3 s) | 2400 | 2399 | 2369 | -0.378 | 0.31 |
| `change_dh5` (240 DH5, map 31-50 then 32-51 at burst 120) | 240 | 240 | 227 | -0.374 | 0.34 |

  About 32 dB in 1 MHz (an SINR where a channel is not clean), 10 dB less at
  -10 dBm (199 of 200 found). **Link flatness 6.5 dB** across the 20
  channels, a smooth rise with frequency in every shot: the whole link (VSG60A,
  two antennas, room, BB60D), not the VSG60A alone; channel 50 was not
  worse, so the access point did not hurt it this time. The clock, -0.37 to
  -0.39 ppm, is the stage 2 figure again, and a jitter of 0.3 sample means
  the one-shot played without a gap.
- **What a reader should know.** `clk` is exact. `found: false` bursts are
  placed from the fitted clock, with a `reason`. `start_sample` is the nearest
  sample, `start_exact` the fit; the detector offset is calibrated on the
  interpolated peak (99.545 samples), so it is right for the planted files and
  could be off by the VSG60A's or BB60D's own group delay, which nothing here
  can see. The top-level `cfo_hz` is the measured median, not a plant
  (`cfo_hz_meaning`). `snr_db` carries no more than the 1 MHz of the other
  files, and the burst's own `power_dbfs` is uncalibrated. The shots' symbol
  phase is whatever the BB60D's clock gave. The captures' `clock_lock_note` is
  the dated one from before bluey's lock fix: it now returns the true clock.
- **What bluey found in them** (graded after its roles fix; its write-up is
  `knowledge/ota-hop-grading.md` in its own repo). Its CLK27 lock returns
  exactly `clk >> 1` on all five, both segments of the map change included;
  detection is 99.6-100 % with its DC block off. **Channel 39, at DC, was
  detected 0 times with its default DC block on** (10/10, 12/12, 31/31, 110/110
  and 19/19 with it off): the block's 64-sample mean subtraction notches about
  fs/64 around DC, and it does the same on real BB60D captures, so that is a
  receiver bug and not the transmitter's. Maps of the wrong size do not
  lock; a map wrong by one channel keeps the clock at about 60 % match.
- **A trap in how the captures were cropped.** Each was cut 40 M samples
  (1 s) before its first burst, a whole number of slots (1600), so every burst
  starts within about 70 samples of a slot boundary of the file, and a receiver
  that takes `sample_offset // slot` splits its detections over two slots (8-18 %
  on three of the five files). Crop with a lead that is not a whole number of
  slots (add a few thousand samples that are not a multiple of 25,000), so
  the slot grid sits at an arbitrary offset, as a live capture's does.
- **Not done:** `--reference` (a clean synthetic copy of a shot, in the tool)
  was not run for these; no shot at another symbol phase; no impaired file
  (the real link is the impairment).

## Synthetic bursts for the DC block, sync-word errors and slave answers, 2026-10-05

bluey asked for known-truth bursts to settle three questions: whether its
device-aware DC block (a 64-sample block-mean subtraction) should delete the
capture-centre channel, how often it recognises a LAP whose sync word has bit
errors, and whether pairs of master and slave bursts recover the clock and the
roles. Three generators, built by Sonnet subagents from written tests and each
reviewed by Opus against the real files, write them; the files are not in git.

- **Where.** `/media/user/4TB/sdr-synth-tmp/{dc,dc40,sync,pairs}/`, as
  `synth_<name>.cf32` and `.json`; regenerate with `python scripts/bt_synth_dc.py
  --set dc|dc40 --out DIR`, `bt_synth_sync.py --set sync`, `bt_synth_pairs.py
  --set pairs`. Same arguments, same bytes (within one numpy build).
- **What every file shares.** One constant noise floor, 6.25e-4 in 1 MHz
  (a burst of amplitude 0.25 is 20 dB), the same at 20 and 40 MS/s; a burst's
  `snr_db` sets its amplitude, never the noise. Burst starts alternate between
  symbol phase 0 and the half-symbol dead zone (`sps/2`), a random
  `timing_frac` per burst. No `air_bits` (`air_bits_omitted`). **`timing_frac`,
  `symbol_phase` and `snr_db` are null at the top level where they vary per
  burst, and `per_burst_keys` lists the keys that live per burst**: a grader
  must read them from the bursts. Files of one set share a seed where the point
  is a paired comparison.
- **DC set (`dc`, 26 files, 33 GB; `dc40`, 4 files, 13 GB).** Channel 39 at the
  centre (2441 MHz, 0 Hz), DH5 and DH1, 800 bursts 12.5 ms apart, SNR stepping
  8, 12, 16, 20 dB in blocks of 200. `dc_<p>_ch39_clean`; `_cw15/25/35`, a
  continuous tone exactly at DC; `_drift15/25/35`, the tone rising 0 to +1 kHz
  over the file; controls `_ch38_clean`, `_ch40_clean`, `_ch43_clean`; and
  `_snr16_cfo0/cfopl30k/cfomi30k` (200 bursts). **A spur file is the clean file
  plus the tone**: same noise, bursts and timing, so the difference is exactly
  the tone; the controls are the clean file's bursts on another channel; the
  cfo files share one seed. The tone level is dB over the **mean** noise bin of
  a 1024-point rectangular FFT, excluding the noise in the tone's own bin (the
  raw reading is 10*log10(10**(db/10)+1): 15.13, 25.01, 35.00); a median reader
  reads 1.6 dB high. The same `spur_db` is 3 dB stronger in absolute terms at
  40 MS/s (the bin is twice as wide), so the sidecar gives
  `tone_over_noise_1mhz_db`. A 35 dB tone is 3 times the total noise power:
  a plain measurement of an 8 dB DH1 burst under it fails unless the tone is
  subtracted.
- **Sync-word errors (`sync`).** Hopping DH1 at 40 MS/s on the map 31-50
  (centre 2441), 800 bursts, 20 dB, one LAP; `hop20_dh1_syncerr_mixed`
  (burst 0 has exactly 1 error, the others 0, 1 or 2 about a third each:
  290/237/273), `_first1` (only burst 0, one error), `_clean` (the control),
  **all one seed, so they differ only in the planted bits**; per burst
  `sync_errors` and `sync_error_bits` (positions 0-63 within the 64-bit sync
  word; the preamble and trailer stay as the correct packet has them, as a bit
  error on air leaves them). Plus `noise_only_20msps_120s` (120 s, 19.2 GB, no
  bursts, `bursts: []`, the same noise per MHz, no `lap`/`uap`): measured over
  its whole length as white Gaussian noise with no seams, and a plain
  discriminator found no match to the LAP's sync word within 7 bits in 7.6e8
  windows.
- **Pairs (`pairs`).** `pairs_dh1_hop20`: 400 pairs, a master DH1 in a master
  slot and a slave DH1 in the next slot **on the same channel**
  (`hop(clk + 2) == hop(clk)` for all 400 on an adapted map, checked against the
  spec text), hopping on 31-50 at 40 MS/s, with idle pair-periods of 0, 1 or 2
  (probabilities 0.60, 0.25, 0.15). The slave is exactly one slot after its
  master in `start_sample` (25,000 samples, 24,999 to 25,001 in the samples
  with the timing fractions), whitened with its own clock `clk + 2`; per burst
  `role`, `pair`, `partner`, `slot_index` (the lower slot is the master's).
  16 pairs are on the centre channel 39. Fitting the clock from the channels,
  masters alone tie (j = 0 and j = +1 both score 1.000); adding the slaves
  breaks it (the wrong one scores 0.515). Five pairs are back-to-back on one
  channel (about 1 in 46 couples): greedy pairing from the left is right if
  every burst is detected, and dropping a chain's master mislabels two.
- **What the reviews changed.** The first versions had spur files and sync files
  on independent noise draws (now paired), top-level keys that would mislead a
  grader (now null), `generator_commit` naming a commit without the generator
  (regenerated from the committed code), and tests that missed a tone at 1 Hz, a
  repeated noise block or `timing_frac` applied twice (now caught).
- **Page and inquiry exchanges with an FHS (`page`, 2 files).**
  `synth_page_exch_hop20` (200 page exchanges, about 12 GB) and
  `synth_inq_exch_hop20` (200 inquiry exchanges, about 1.8 GB), 40 MS/s, 20 dB,
  map 31-50. A page exchange: two ID packets per master TX slot with the paged
  device's access code (68 bits, no trailer), its ID response, the master's FHS,
  the ID acknowledgement, then the master's first POLL and **110 master packets
  with slave answers** (NULL, DM1 or DH1) hop-true for the clock the FHS carries:
  the receiver's author validates an FHS by brute-forcing CLK[27:1] from those
  packets and requiring it to equal the FHS's clock. **That lock is not always
  unique**: over the whole 2^27 domain, 173 of the 200 page exchanges have one
  solution, 26 have two and one has three (exchange 94), the FHS's clock always
  among them (each exchange records `clock_solutions` and `clock_unique`; the
  aliases come from the 20-channel map). A receiver picks the solution equal to
  the FHS's clock. The old "minus one" allowance is withdrawn in these sidecars. An inquiry exchange: GIAC
  IDs and the scanning device's FHS response, no follow-up. Identities: paged LAP
  `0x6B1D3C` UAP `0x47`, master `0x112233` UAP `0x55` NAP `0x1234`.
  `apps/bt_fhs.py` is the FHS encoder, written **from the Core v6.0 text**
  (Vol 2 Part B 6.5.1.4, Figure 6.9, Table 6.3; HEC/CRC from the paged UAP in a
  page response and the DCI 0x00 in an inquiry response; the payload carries
  SP = 0b10) and checked against libbtbb, which decodes it fully (200 random FHS,
  wrong-UAP and wrong-whitening controls refused) and against the receiver's
  synthetic vector (bit for bit with SP = 0; that vector was written by the
  receiver's own side, so it checks the machinery and not the spec).
  **Whitening of an FHS in a Central page response or an inquiry response is
  seeded from the response's X-input, not CLK6-1** (7.2): the register is
  [X0..X4, 1, 1], with Xprc (EQ 6; a frozen estimate of the paged device's clock,
  the train offset, a counter N from 1) or Xir (EQ 8; the scanner's CLKN16-12
  plus a counter N); the first builds assumed CLK6-1 and an independent
  reviewer reading the spec caught it. A receiver that tries all 64 starting
  values still decodes it, with an implied "clock" of 32 + X. **What the files do
  not follow, stated in each sidecar's `conformance` key**: the page, page
  response, inquiry and inquiry response hop sequences (the channels are free
  choices in 31-50), the **basic 79-channel sequence the
  text mandates from the first POLL** (the follow-up runs on the adapted map
  31-50 from the first POLL, because 79 MHz does not fit a 40 MS/s capture;
  each exchange records `spec_basic_channel_first_poll`, which differs from the
  channel used in 159 of 200), the inquiry back-off, and the paged device's
  receiver timing, FHS retransmission, and a clock that continues across
  exchanges (a scanner identity repeats every 50 exchanges with an independent
  clock); a page train is 16 slots, 8 TX slots of two IDs, and an exchange of
  3-8 TX slots has ended early on a response, which is allowed. The page side
  always uses k_nudge 0 and N 1; EQ 6's grouping can be read two ways that
  differ in X on 99 of the 200 exchanges (both are recorded per exchange). A sidecar
  also lists what the text leaves silent (the first POLL's slot, the FHS header's
  FLOW/ARQN/SEQN, the inquiry FHS header's LT_ADDR) and what was chosen.
- **Interferers (`interf`, 8 files, 961 MB each).** `hop20_dh5_int_clean` and
  seven files that are that file plus an interferer, one seed, so the difference
  is exactly the interferer: continuous tones at 10 or 20 dB over the noise in
  1 MHz on channel 49 (+10 MHz) or 43 (+4 MHz); Wi-Fi-like noise band-limited
  to +11..+20 MHz (2452-2461 MHz, the part of a Wi-Fi channel 11 a capture at
  2441 sees), +20 dB per MHz, constant or in frames of 0.3-3 ms at 30 % duty;
  and the two tones with the bursty noise. Per burst `interferer_overlap` and
  `interferer_power_in_band_db` (null with no interferer power in the band);
  channel 50 is the only map channel the Wi-Fi band touches and only half of it,
  and its truth comes from the filter's actual response (17.33 dB), not the
  nominal band (16.99 dB), which two reviewers' measurements showed to be
  0.35 to 0.44 dB low. The per-burst power is an expectation (ensemble, time-
  averaged over the gate): a 2.87 ms burst on channel 50 reads up to 0.45 dB
  different in the realised noise. The Wi-Fi filter's upper edge sits past the
  capture's Nyquist, so a skirt, about 1.2 dB under the plateau, wraps to
  -20 MHz (2421 MHz), outside every channel of the map. The clock note in these
  sidecars is the current one: the lock equals `clk >> 1`.
- **How these were reviewed.** Builders were Sonnet 5.5 subagents; the p6 files
  were reviewed by Opus 5.5 against the real files, the p7 files by GPT-6.1
  Sol and the free Muse Spark 1.3 Contributor through the swarm's OpenCode
  driver, on slices of the files copied into read-only worktrees (OpenCode has
  no sandbox: nothing but public code and synthetic signals goes in). The two
  disagreed on the page generator and the spec text supported the stricter one,
  which had caught that the follow-up does not use the basic sequence. A second
  round of both, on the regenerated files, found that a clock search over the
  whole domain is not unique for 27 page exchanges, a stale clock note, and
  tests that missed a +1 MHz carrier and a +40 sample start in the interferer
  files; those were fixed (no sample changed, byte-compared).
- **DM/LMP traffic at five SNR levels (`acl`, 6 files).** `acl_dm_hop20_snr16p5`,
  `_snr15p0`, `_snr14p0`, `_snr13p5`, `_snr10p5` (dB over the noise in 1 MHz) and
  the control `acl_dh5_hop20_clean_snr20`, 40 MS/s, map 31-50, master packets of
  LAP `0x112233` UAP `0x55`. Each level file has **204 bursts: 68 DM1, 68 DM3,
  68 DM5 in a seeded order**, and is the same schedule, clocks, channels, payloads,
  timing and noise as every other level file, scaled in signal only (one seed,
  8101), so a level comparison is paired. **LMP PDUs travel in DM1 only**
  (Core Vol 2 Part C Table 5.1 lists DM1 or DM1/DV for every PDU used): all 68 DM1
  carry one of eleven real PDUs (name_req/res, accepted, not_accepted, detach,
  features_req/res, version_req/res, max_slot, set_AFH), DM3/DM5 carry LLID 2
  L2CAP-style data. bluey had asked for LMP in all three; the first build did it
  and the reviewers caught that Table 5.1 forbids it. Per burst the sidecar gives
  the header fields, LLID, length, `payload_hex`, `lmp_opcode`, `lmp_params`, and the
  exact bits: `air_bits` (after whitening and FEC, first transmitted bit is bit 7
  of the first byte, zero padded) and `body_crc_bits_hex` (payload header, body and
  CRC before FEC), the one exception to "no `air_bits`". The levels came from a
  sweep with a genie-aided reference receiver (start and channel from the sidecar,
  libbtbb as the decoder; the table is in `scripts/bt_synth_acl_sweep.json` and
  each sidecar): pooled yield about 96, 80, 60 and 50 % at the first four levels
  and about 12 % at the fifth. **Another receiver's yield differs a lot** (a second
  demodulator lost up to 20 points at about 13 dB), so grade by dB.
- **Tones between channels (`between`, 4 files).** `hop20_dh5_int_cwb4p25_p20`,
  `_cwb4p5_p10`, `_cwb4p5_p20`, `_cwb4p75_p20`: the clean interferer file of
  `interf` plus one tone at +4.25, +4.5 or +4.75 MHz, paired with
  `hop20_dh5_int_clean` (the file minus that file is exactly the tone). Per burst
  `interferer_offset_mhz` (tone minus the channel's centre), `interferer_overlap`
  (|offset| <= 0.5, both edges inclusive) and `interferer_at_band_edge`. At +4.5
  the tone is on the edge of channels 43 and 44 and both are labelled with the
  full tone level, a grading convention: a symmetric integration finds half the
  power (3 dB lower) in each. A run with a seed other than 6101 says it is not
  paired.
- **Page and inquiry exchanges on the spec's hop sequences, clipped to the capture
  window (`conf`, 2 files).** `synth_conf_page_hop_win` (200 page exchanges,
  12.9 GB, 46,414 bursts) and `synth_conf_inq_hop_win` (200 inquiry exchanges,
  2.2 GB, 2,042 bursts). Every channel comes from the spec's selection box
  (`apps/bt_hop_substates.py`, Core Vol 2 Part B 2.6, Table 2.2, EQ 1-8): the page
  IDs on the page sequence of the A or B train, the response on the peripheral
  page response sequence (Xprp), the FHS on the Central's (Xprc, N = 1, CLKE
  frozen), the acknowledgement on Xprp with N incremented, the follow-up from the
  first POLL on the **basic 79-channel sequence** of the master (a slave answer is
  on the channel the kernel gives at its own clock, not the master's). The heard
  slot is derived from the clocks: the first half-slot whose page frequency
  equals the paged device's page-scan frequency. **Only the bursts on channels
  27-51 (2429-2453 MHz, -12..+12 MHz, symmetric about 2441.0) are rendered**; the
  others are listed in `bursts` with `rendered: false` and their would-be start,
  and leave only noise. Of the 46,414 page bursts 14,808 are in the window, of the
  2,042 inquiry bursts 819; the page FHS is in the window in 88 of 200
  exchanges (the inquiry FHS in 72) and the heard ID in 84 (74). Random symbol
  phase per exchange (0..39 samples), per-exchange base SNR 10-20 dB with a
  per-burst jitter, no centre bias. The in-window clock lock (a full search over
  CLK[27:1] using only the rendered master-LAP packets) is unique for 159 of 200
  exchanges; the true clock is always among the solutions. **Part G's page table**
  (the spec's hop sample data) is reproduced by the equation unchanged with the
  train alternating A, B every 1.28 s (Npage = 128 repetitions): the odd 0x1000
  blocks are the B train. 106 of 200 page exchanges start on B: they are excerpts of
  a page procedure that had already run an A repetition. Not modelled: scan
  windows, back-off, interlaced scan, the train repetition count, knudge, FHS
  retransmission (the sidecar's `conformance` says so). libbtbb decodes the
  in-window FHS from about 16 dB; below that a slicer reads them only partly.
- **Clustered errors (`fade`, 13 files, 235 MB each).** 204 bursts per file (51
  DM1 with LMP, 51 DM3, 51 DM5, 51 stand-alone page-response FHS), the same
  bursts, channels, payloads and noise in all (seed 9101); a file is the AWGN
  reference plus a distortion on the signal. `fade_ref_snr18`, `_snr24`;
  Rayleigh `fade_ray_fd{100,300,800}_snr{18,24}` (Jakes, 32 sinusoids, an
  independent process per burst, E|h|^2 = 1 and the fade multiplies the signal,
  not the noise); Rician `fade_rice6_fd300_snr{18,24}` (K = 6 dB); and
  `fade_nb_sir{0,6,12}_snr24` (an FM-like narrowband signal about 100 kHz wide on
  a random half of the bursts, over a random 10-60 % of the burst, three SIRs,
  one hit set). Per burst `fade_gain_db` is the gain at every symbol centre,
  with `fade_min_db`, `fade_frac_below_10db`, the longest run and `snr_eff_db`;
  the NB files give `nb_start_symbol`, `nb_end_symbol`, offset and SIR. The
  fading is a flat Rayleigh/Rician model, not a measured channel; errors do
  cluster (index of dispersion 2.8 at 300 Hz and 7.2 at 800 Hz against about 1.2 for
  noise at the same error rate). Burst-level payload yield from the genie reference
  receiver: DM5 falls to 16 % at 800 Hz and 18 dB, 49 % at 24 dB.
- **Second seed of the clustered-error files (`fade2`, 13 files).** The same
  13 distortions at seed 9102 (`synth_fade_*_s2`): independent bursts, channels,
  payloads, FHS fields, noise, fades and narrowband hit sets, paired inside the set
  to the `_s2` references. Reviewed on the samples: the noise of the two seeds has no
  correlation (about 3e-4), the burst lists share nothing beyond chance.
- **Multipath (`mp`, 16 files).** `synth_mp_<profile>_fd<fd>_snr<snr>`: the
  bursts of `fade_ref_snr18/24` (seed 9101) with each burst replaced by
  `sum_k a_k h_k(t) s(t - tau_k)`, independent Rayleigh paths, one Doppler for the
  paths (100 or 300 Hz), profiles `p2a` (0, 0.2 us / 0, -3 dB), `p2b` (0, 1.0 us),
  `p3` (0, 0.4, 1.0 us / 0, -3, -6 dB), `p4` (0, 0.3, 0.6, 1.0 us / 0, -2, -4,
  -7 dB). Truth per burst: `mp_taps`, `mp_gain_db` (the narrow-band gain at the carrier,
  the ISI not in it), `mp_isi_db` (the delayed taps' power over the first tap's, ratio
  of burst means), `mp_rms_delay_spread_us`. A 1.0 us tap at an integer-MHz
  carrier has the phase factor 1, so p2b's gain list has no phase term. p2b is
  brutal (raw BER 0.08-0.15 at 24 dB against 0.004 for the flat fade). The free
  1/4-sample channel fit does not work (the normal equations are singular in
  float32); the power-delay profile is tested at the profile's own delays.
- **Mixed devices for blind tests (`mixed`, 2 files, 2.0 GB each).**
  `synth_mixed_blind_a` (seed 9201, 17,487 bursts, 1,452 collided = 8.3 %) and
  `_b` (9202, 18,389, 1,664 = 9.05 %), 6.29 s each: 8 unsynchronised piconets
  (6 ours, 2 not ours) with their own LAP/UAP (sync words at least 22 bits apart),
  clock, slot grid, level (8-22 dB) and hop sequence on the map 31-50, plus three
  page exchanges on the spec's hop sequences (the joiners, `ours`). The signals add
  linearly. Per burst: `device`, `role`, `ours`, `overlaps` (every burst
  overlapping in time within +-1 MHz: overlap length, channel offset, `sir_db`),
  `collision` (a same-channel overlap) and `overlap_frac`. `sir_db` is the ratio of
  total envelope powers: for an adjacent channel the in-band interference is 25-32 dB
  lower. `air_bits` hex is padded to a byte; use `air_bits_length`. A blind scan of
  the first 30 M samples finds every collision-free burst of all 8 devices and
  none of three LAPs not in the file.
- **Sample counts.** The files of these two sets carry `n_samples` at the top of
  the sidecar (the file is `8 * n_samples` bytes); the older sets do not, and
  their length is the file size divided by 8.

## Wrong clocks, wrong UAPs, and what libbtbb passes

bluey asked for an independent check of how a receiver ends up confirming
a wrong UAP. It had assumed a wrong CLK6-1 leaves the type and length
alone and the CRC then passes by chance, 2^-16. The encoder here was the
source and libbtbb the decoder: DM1, DH1 and DH3 at every clock, many
lengths, 20 random payloads each, 288,000 packets (2026-10-03).

- **One UAP per wrong clock.** At any wrong CLK6-1, exactly one UAP passes
  the HEC. A full 64 × 256 libbtbb sweep agrees. UAP' XOR UAP depends only
  on the true and the assumed clock, never on the header's fields or the
  UAP itself.
- **A schedule keeps a group of them.** A receiver whose clock is off by a
  constant shift sees one UAP' throughout only for some shifts. The group
  of consistent UAP' XOR UAP depends on the schedule:

  | Schedule | Shifts that agree throughout | UAP' XOR UAP |
  |---|---|---|
  | every slot | 0, 32 | 00, D6 |
  | every 2 or 6 slots (master slots, DH5) | 0, 1, 32, 33 | 00, 0B, D6, DD |
  | every 4 slots (DH3, DM3) | 8 shifts | 00, 0B, 59, 52, D6, DD, 8F, 84 |
  | every 8, 16, 32 slots | 16, 32, 64 | |

- **A wrong clock always changes the type or the length.** Counted from
  the whitening sequence: 0 of 4032 (clock, wrong clock) pairs leave both
  the TYPE bits and the payload length field unchanged, for 1-byte and
  2-byte payload headers alike. Only 4.8 % even keep the type. So the
  mechanism assumed above cannot happen at all.
- **The wrong passes come from libbtbb's types 7 and 13.**
  - Every wrong-clock payload pass that held for all 20 payloads of a
    class was one libbtbb had parsed as type 7 or 13. There were 45 of
    them, about 3e-3 of the (UAP, clock, length) classes.
  - libbtbb passes those two types about 6e-4 of the time per wrong clock
    even with random bits after the header, whether or not its transport
    is set to ACL.
  - What it does with them is not known here: there is no libbtbb source
    on this machine.
  - Another 47 classes had wrong passes for only some of their payloads,
    which do not repeat from packet to packet.
- **What bluey did with it.** It now counts CRC evidence only from types
  that carry a length: FHS, DM1/DH1, DM3/DH3, DM5/DH5. On its real
  captures, false passes had been only at parsed types 13 and 7. After
  the change it confirmed none of 4924 wrong claims and 103 of 176 true
  UAPs, refuting none.

Still open:

- **The FEC 2/3 tail zeros of a DM packet are not whitened.** Whitening
  comes before the FEC, and the encoder adds the tail. libbtbb, and so
  bluey, discards the tail, so nothing has checked those bits yet.
- **A file of the real clock offset** between a transmitter and the SDR.
- **The VSG60A on its own at 40 MS/s**: its flatness across 20 MHz is only
  known as the whole link's 6.5 dB; separating the two needs a cable and
  an attenuator; see Stage 3 over the air.
