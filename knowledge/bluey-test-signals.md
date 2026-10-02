# Bluetooth Classic test signals for bluey-ox-walker: the plan

One of the notes in `knowledge/`. What applies everywhere, and which
file covers what, is in [CLAUDE.md](../CLAUDE.md). Why the VSG60 can
send this at all, and what it cannot, is in [bluetooth.md](bluetooth.md).

Agreed with the bluey-ox-walker session on 2026-10-01. Nothing is built
yet; work starts the evening of 2026-10-01.

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

The keys it grades against:

| Key | |
|---|---|
| `lap`, `uap`, `clk` | say whether `clk` is CLK27 or CLK1-27, and at which burst |
| `channel_mhz` | |
| `sample_rate`, `center_mhz` | |
| `cfo_hz`, `timing_frac`, `snr_db` | the impairments added |
| `bursts` | a list of `{start_sample, ptype, payload_hex}`; `start_sample` is the first sample of the access code |
| `afh_map`, `afh_instant` | stage 3 only; bluey applies a map from `clk1_27 >= afh_instant`, as its variant E does |

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
- **The HEC polynomial is 0xA7**; header fields go out LSB first.
- **Whitening is seeded from the clock** (CLK6-1), not from the channel
  as in LE.
- **The length field is 9 bits**, except in 2-DH5 and 3-DH5, where it
  is 10. DH3 and DH5 are 9.
- **A passing FEC vote does not prove the bits are right.** Grade
  against the sidecar, not against the decoder's own confidence.

The encoder goes in `apps/` with a test in `scripts/`, the way
`ism_frame` and `scripts/test_ism_frame.py` work. The Core spec's Vol 2
Part G sample data, which bluey keeps as `knowledge/sample_data.md`,
gives vectors to test the access code, HEC, whitening and CRC against.
