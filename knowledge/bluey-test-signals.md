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
   baseband. The master's address and clock are chosen, and bluey's
   variant-E kernel gives the sequence.

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
| `afh_map`, `afh_instant` | stage 3 only; bluey applies a map from `clk1_27 >= afh_instant`, as its variant E does |

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
  - The clock offset was -0.36 ppm again, with a third of a sample of
    jitter.

  That is the level for clean over-the-air data at this spacing.
- **Stopping.** `bt_tx.py` turns SIGTERM, SIGHUP and SIGINT into a flag,
  so a `timeout`, a dropped ssh session or Ctrl-C stops the repeating
  waveform and still writes the sidecar, marked `interrupted_by`. A
  repeat the VSG60 plays from its own memory must not outlive the
  script.

Still open:

- **The FEC 2/3 tail zeros of a DM packet are not whitened.** Whitening
  comes before the FEC, and the encoder adds the tail. libbtbb, and so
  bluey, discards the tail, so nothing has checked those bits yet.
- **A file of the real clock offset** between a transmitter and the SDR.
- **Stage 2**, the same train from the VSG60 on the work laptop.
- **Stage 3**, narrow-AFH hopping.
