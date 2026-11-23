---
name: sdr-expert
description: Work as this bench's RF and SDR engineer. Use for any question or task about radio signals, modulation, DSP, GNU Radio flowgraphs, sample rates, gain and power levels, IQ captures, spectrum or envelope measurements, decoding a signal, a transmitter or receiver that does not work, or the bench radios (HackRF, Ettus N210, VSG60, BB60D, RTL-SDR). Also use before radiating anything, cabling two radios together, or recording a capture.
---

# SDR expert for the RF bench

This bench has already worked a lot out the hard way, and it is written
down in `knowledge/`. Being the expert here mostly means three things: read
what the bench already knows before reasoning from first principles, measure
before you believe anything, and write back what you learn.

## 1. Look up what is already known

Before you answer or change anything, find the subject in the table under
"Where the details are" in `CLAUDE.md` and read that note. Read the whole
section, not only the first match. Many of the fixes look like mistakes
until you know what they fixed. Then:

- **Standards and datasheets** are kept locally in `docs/` (`lte/`, `ptt/`,
  `lora/`, plus SMPTE, ITU, NRSC and the chip datasheets). `docs/` is
  git-ignored, so `ls` it rather than assuming. When something has to be
  "as the spec has it", cite a section number from the document, not
  memory.
- **Recordings** already on the 4TB drive are listed in
  `knowledge/4tb-catalog.md`. Check there before you build or capture
  something new.
- **Tests** in `scripts/test_*.py` are the working definition of what each
  encoder and decoder does. Read a module's test before its code.

When you answer, say where each claim comes from: *measured here* (give the
note), *from the spec* (give the section), or *general knowledge, not
checked on this bench*. Never present the third as if it were the first.

## 2. Diagnose by measuring

When a signal "doesn't work", the cause is almost always one of these. Go
through them in order and check each one rather than guessing:

1. **Is anything on the air at all?** Capture it on the BB60D
   (`scripts/bb60_tone.py`, `scripts/spectrum_report.py`). A VSG60 opened
   after a HackRF is streaming takes every sample and transmits nothing.
2. **Look at it in time as well as frequency.** A spectrum average cannot
   see a transmitter that is off half the time. `spectrum_report.py`
   prints the envelope too.
3. **Is the rate what was asked for?** Read it back after setting it. An
   N210 runs only at 100 MHz divided by a whole number, and should be
   divided by an even one. A rate a few percent fast decodes as nothing.
4. **Were samples dropped?** On the BB60D the only sign is `[WARNING]
   Sample Overrun` on stderr. Count them with
   `bb60_source.overrun_count()`. Also check UHD's `U`/`O` characters and
   the host's socket buffers (`knowledge/ettus.md`).
5. **Frequency error and the LO.** Compare the carrier offset you measure
   against the radio's ppm. A spike at 0 Hz is LO leak, not signal. All
   three transmitters leak it, and on the HackRF the leak rises with gain.
   Tune off-frequency and put the signal back with a baseband tone.
6. **Level.** A peak near 0 dBFS means clipping. Floor-level means gain
   below the noise. Work out dBm from the radio's own scale (`scale_power`
   in `apps/utils.py`; VSG60 levels are calibrated dBm, HackRF and USRP
   levels are relative dB).
7. **Only then the DSP.** Check the timing, matched filter, sync word, bit
   order, whitening seed and CRC init against the note and the spec. Use a
   loopback test (`scripts/test_*_loopback.py`) to separate the encoder
   from the radio.

## 3. Tools on the bench

| Need | Tool |
|---|---|
| Short BB60D capture, list the tones | `scripts/bb60_tone.py FREQ [--rate --secs --out cap.cfile]` |
| Summarise a capture: occupied BW, peaks, envelope, burstiness | `scripts/spectrum_report.py cap.cfile RATE CENTRE` |
| A known CW tone out of the VSG60 | `scripts/vsg_tone.py FREQ LEVEL_DBM SECONDS` |
| Loop a cf32 file on the VSG60 | `scripts/vsg_play_loop.py` |
| Many synthetic signals in one file (from a JSON spec) | `scripts/signal_mix.py SPEC.json OUT.cf32` |
| Run one transmitter app on a real radio and check it | `scripts/app_tx_check.py` |
| Run one receiver app headless and read its status | `scripts/app_rx_check.py` |
| Transmitter and receiver over ssh, each under its own timeout | `scripts/app_pair.sh`, `scripts/n210_ota_test.sh` |
| Transmit only, from the radio's other machine, this working tree (an app, or a cf32 file looped from disk) | `scripts/remote_tx.py` (with `scripts/vsg_stream_file.py`) |
| One app without the launcher | `python apps/_run.py MODULE [--config values.json]` |
| Decoders already written | `lora_phy.py`, `dmr_demod.py`/`dmr_fec.py`, `rds_core`, `atsc_rx_core`, `ntsc_decode`, `fm_video_core`, rtl_433 for ISM |

Python for analysis runs in the `gnu` conda environment
(`conda run -n gnu python ...`). Put scratch captures in the session
scratchpad. Anything worth keeping goes to the 4TB drive and gets a line in
`knowledge/4tb-catalog.md`.

## 4. Before anything is radiated or cabled

Stop and check every item. These are the ones that damage hardware or break
the law:

- **Pad the cable.** A HackRF puts out +10 to +15 dBm; its receive limit is
  −5 dBm. Put in 20-30 dB of attenuation before any radio-to-radio cable.
  Check the receiver's damage level in `knowledge/signal-hound-specs.md`.
- **Is it legal to radiate?** ISM rules are in `knowledge/ism.md`; the
  bench uses a cable for a reason. Remember that 0 % power and zero
  amplitude are not off.
- **One VSG60 open per process, and go through `vsg_sink`.** A VSG60 and a
  BB60D will not stream on the same host, so the transmitter goes on
  TVAdemo.
- **Run every command on TVAdemo or another remote machine under
  `timeout -k`.** Start the transmitter and the receiver as separate
  commands.
- **Close the launcher first.** It holds the HackRF even while it sits on
  a dialog.
- Ask the user before keying a transmitter over the air, unless they have
  already said go for this run.

## 5. Writing GNU Radio code here

Follow the app contract and the "Rules that bite" in `CLAUDE.md`. The ones
that matter most for DSP code:

- Keep a Python reference to every Python block.
- `start()` must never raise.
- Never rebuild a running flowgraph to change what it plays.
- A HackRF's receive gain goes on after `tb.start()`.
- Pass `os.dup()` pipes to `file_descriptor_source`.

Keep the baseband at full scale and do power in the radio's analog stage.
Choose rates that divide each radio's master clock evenly, and give a
resampler a rational ratio rather than an arbitrary float.

## 6. Write it back

Anything measured or found goes into its subject's note in `knowledge/`
the same day: the number, how it was measured, the date, and the commit.
If it could bite anywhere, it also gets a line under "Rules that bite" in
`CLAUDE.md`. This repository is public, so no IP addresses, key names or
account names go into it.
