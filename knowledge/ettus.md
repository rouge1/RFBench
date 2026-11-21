# The Ettus N210

The one network radio here, and the radio behind Settings' "Ettus USRP".
What it is, what it was measured to do over the air into the BB60D, and how
the three antennas tried on it compare. Everything below was measured on
2026-11-21 unless it says otherwise.

## What it is

| | |
|---|---|
| Motherboard | USRP N210 r4, FPGA 11.1 - `uhd_find_devices` calls it `type: usrp2` |
| Daughterboard | UBX-40 v2, rev 7, RX and TX: 10-6000 MHz, PGA0 gain 0-31.5 dB in 0.5 dB steps |
| Antenna ports | TX/RX (transmit and receive), RX2 (receive only), CAL |
| Link | gigabit Ethernet, through a USB Ethernet adapter on worklaptop1 - not USB |
| Host software | UHD 4.8.0, the conda `gnu` environment |

`apps/utils.py`'s USRP power range falls back to 0-31.5 dB, which is this
daughterboard's own range. **It used to carry a WBX**, which stops at
2.2 GHz; notes written before the swap say WBX, and `42e109e` corrected
CLAUDE.md, fm-video.md, rds.md and `fmVideoReceiver`'s `USRP_TOP_MHZ`
(now 6000) for the UBX-40. The X310 measured in ism.md is a different
radio.

**The host needs bigger socket buffers.** UHD asks for
`net.core.rmem_max=50000000`, and receiving at any real rate needs it.
worklaptop1 now holds it in `/etc/sysctl.d/60-uhd.conf`, with `wmem_max` too,
so it lasts over a reboot. Before that file it had 32 MB set by hand, which did
not last.

**Two messages that are harmless:** UHD's `Unable to set the thread
priority ... pthread_setschedparam` on every open (the process is not
real-time), and `[WARNING] [UDP] The recv buffer could not be resized` when
the sysctl above is missing. A third was seen once, at shutdown: `fifo ctrl
timed out looking for acks` while powering the converters down after
`SIGTERM`. The next open worked normally.

## Transmitting

Measured with UHD's `tx_waveforms` (1 MS/s, a tone 100 kHz above centre,
amplitude 0.3, TX/RX port) into the BB60D over the air (ATT 0, RF 0, 2.5 MS/s,
2 s at a time):

- **Gain is what it says.** Gain 0 to 15 dB raised the tone 15.1 dB at 915 MHz.
- **The LO leaks at centre, with no correction applied**: −36 to −38 dBc at
  915 MHz, −33 dBc at 433.92, −26 dBc at 2440, all against the 0.3 tone. As
  with every direct-conversion transmitter here, put the signal off centre.
- **Spurs at ±9.16 kHz and ±18.3 kHz around the tone**, −49 and −51 dBc. Not
  checked: they are probably `tx_waveforms`' wave table, not the radio.
- **Frequency, against the BB60D**: the N210 reads −0.16 to −0.25 ppm, the
  VSG60 +0.33 ppm - about 0.55 ppm between the two transmitters, normal for
  free-running clocks with no shared 10 MHz.
- **Underruns**: none in any `tx_waveforms` run, and none in eight of the nine
  FM + RDS runs (the longest 4¾ minutes). One `U` in a 50 s run.

**The FM + RDS Transmitter runs on it**, through `apps/_run.py` with
`--config` (`radio_type` `usrp`, `ipXmitAddr` the N210's address) - no
change to Settings needed. Off air into `rdsReceiver` on the BB60D see the
antenna table below and [rds](rds.md#rds-receiver).

## The antennas: three compared

Three antennas, numbered as they were swapped onto the N210's TX/RX port.
Every figure is the N210 at gain 10 dB, a tone at +100 kHz, received on the
BB60D (ATT 0, RF 0) with whatever antenna it had - over the air, so each figure
is antenna *and* position together. RDS is `fmRdsTransmitter` at 25 % power
into `rdsReceiver` at 60 %, at 101.1 MHz (an empty channel here), for 60 s.

| | ant 1, run a | ant 1, run b | ant 2 | ant 3 |
|---|---|---|---|---|
| 101.1 MHz | −73.6 dBFS | −81.1 | −50.3 | **−43.4** |
| 433.92 MHz | −51.2 | −44.7 | −44.4 | −53.8 |
| 915 MHz | −48.9 | −47.9 | **−33.6** | −36.7 |
| 2440 MHz | −49.7 | −48.4 | **−45.3** | −57.3 |
| RDS at 101.1 | **0 %** (no sync in 60 s) | 70.7 % (1956 blocks seen of ~2740) | 99.5 % | **99.8 %** |

- **Antenna 3 is the FM antenna**: 7 dB over antenna 2 at 101.1 MHz, and
  30-38 dB over antenna 1. It gives up about 10 dB to antenna 2 at 433 and
  2440 MHz.
- **Antenna 2 is the all-rounder**: the best at 915 and 2440 MHz, level with
  antenna 1 at 433, and good enough at FM to decode RDS clean.
- **Antenna 1 cannot carry FM.** Its two runs, minutes apart, decoded 0 % and
  71 %: at 101.1 MHz it sits at the FM threshold, where a few dB decides
  everything. Earlier the same day, with antenna 1 placed differently, it
  got 71-83 % and a carrier about 12 dB over the noise in 200 kHz.

**How much one run can be trusted.** Antenna 1's two runs agreed within
1.3 dB at 915 and 2440 MHz, and differed by 6.5-7.5 dB at 101.1 and 433.92.
The VSG60's tone at a fixed −40 dBm read −84.2 dBFS in the morning and −76.8
dBFS in the afternoon. So, over the air on this bench, treat a difference
under about 7 dB at FM or 433 MHz as unproven from a single run; the 915 and
2440 MHz figures hold better. A cable with a pad would remove all of that, at
the price of not testing the antenna.

**These are relative.** dBFS on the BB60D at one centre and gain compares
one antenna with another. The figures here are corrected: as first
measured, `bb60_tone.py` scaled its spectrum for a tone's peak and then summed
seven bins, which read every tone and floor 2.37 dB high (the Blackman window's
noise bandwidth); 2.4 dB has been taken off each. Differences between them were
never affected.

**A tone on the BB60D's exact centre comes through at full level** - it has
no blind spot there, unlike the HackRF - so an over-the-air check need not
keep a transmitter off the BB60D's centre. Measured cabled from the VSG60
the same day by the skinny-fm-receiver session, flat within 0.18 dB from
0 Hz to ±100 kHz at 100, 433.92, 915, 2440 and 5800 MHz; the BB60D's own
spike at centre at 5.8 GHz sits about 60 dB under a −50 dBm tone. That is in
the skinny-fm-receiver repository, `knowledge/signal-hound-calibration.md`,
"No blind spot at the BB60D's centre (2026-11-21)":
<https://github.com/rouge1/skinny-fm-receiver/blob/main/knowledge/signal-hound-calibration.md#no-blind-spot-at-the-bb60ds-centre-2026-11-21> It is not dBm, and nothing here measured antenna
gain.

## Running the tests again

All four are in `scripts/`. None writes `config/`, and each transmitter runs
under `timeout -k`.

```sh
# The whole antenna test: four tones, then a minute of RDS. The N210 is driven
# over ssh from TX_HOST, the machine on its Ethernet link; the BB60D is local.
TX_HOST=worklaptop1-fast USRP_ADDR=<the N210's address> scripts/n210_ota_test.sh ant2

python scripts/bb60_tone.py 915e6 --wait 4      # one BB60D capture; peaks in dBFS
python scripts/vsg_tone.py 915e6 -40 12         # on the VSG60's machine: a tone, 12 s
python scripts/rds_tau_sweep.py CAPTURE         # RDS from a capture, at every timing
```

`uhd_find_devices` on TX_HOST gives the N210's address. Tell any other
session using the N210 or the BB60D before starting; the VSG60 and the BB60D
cannot stream on one host at the same time, which is why the transmitters run
on worklaptop1.

## Not tested yet

- **Receiving on the N210**, and so whether it overruns (`O` or `D`) at any rate.
- Its highest sample rate over the gigabit link (about 25 MS/s at 16-bit
  samples is the usual figure for an N210; not measured here).
- Anything through a cable and pad, and any absolute level in dBm.
- Whether the ±9.16 kHz spurs come from `tx_waveforms` or the radio.
