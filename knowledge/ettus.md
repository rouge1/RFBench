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
| Link | gigabit Ethernet through a USB Ethernet adapter - not USB. On worklaptop1 until 2026-11-21, then moved to the BB60D's machine with its adapter, under a NetworkManager profile of its own (manual IPv4, IPv6 off) |
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

## Every app on the N210 (2026-11-21)

The first time the apps were run on the Ettus: antenna 2 on RF1 (TX/RX),
a second antenna 2 on RF2 (RX2), the BB60D and the VSG60 on antennas.
RF1 and RF2 on the front panel are TX/RX and RX2. **Every transmitter
transmits on TX/RX and every receiver listens on RX2**, set in each app's
usrp branch - so a receiver needs an antenna on RF2 of its own.

Each app was built from its own dialog with Settings patched to `usrp`,
frequency and power set explicitly (the saved dialogs held 50-87 % power on
300 and 533 MHz), and the radio read back after `main()`: frequency, gain,
sample rate, antenna. Its power and frequency callbacks were then moved and
read back too. Transmitters at 915 MHz (FM video 5800), 10 % (UBX gain 3 dB).

### Transmitters

| App | On the N210 | Over the air into the BB60D |
|---|---|---|
| AM sine | ✅ LO 330 kHz low on purpose | carrier and 1 kHz sidebands at 915.000; LO leak −43 dBc at −330 kHz |
| ASK | ✅ | 1.6 MHz wide, centred - it runs unfiltered |
| FSK | ✅ | tones at ±50 kHz, 214 kHz wide |
| PSK | ✅ (1 `U`) | 110 kHz wide, centred |
| PPM-OOK | ✅ 20 MS/s (2 `U`) | 5.2 MHz pulses on a 40 kHz comb |
| AM audio | ✅ | follows the programme |
| FM audio | ✅ | 204 kHz wide for its 100 kHz deviation |
| Subcarrier | ✅ | carrier and the 20 kHz subcarrier |
| FM + RDS | ✅ | **`rdsReceiver` on the BB60D: 99 % blocks good**, PS, RadioText, Now Playing, stereo pilot |
| **ATSC** | ❌ **12 MS/s asked, 12.5 given** (fixed - below) | **pilot at −2802.7 kHz, not −2690.6; 5.74 MHz wide; `atscReceiver` on the BB60D: "pilot not found"** |
| **NTSC** | ✅ LO 6 MHz high on purpose; 20 MS/s | visual carrier −1750 kHz, aural +2750 kHz - exact; **but `ntscReceiver` on the BB60D: "picture, but only 0-2 frames a second"**, where the VSG60 decodes 17.9 (fixed at 25 MS/s - below) |
| ISM | ✅ LO 400 kHz low on purpose | **`ismReceiver` on the BB60D: every burst decoded**, Nexus-TH 19.0 °C 71 % as sent (at 433.92 MHz) |
| FM video | ✅ 20 MS/s at 5.8 GHz | **`fmVideoReceiver` on the BB60D: picture, 17.4-18.1 frames/s**, colour, line rate within 3 ppm, deviation read 7.87-7.94 MHz of 7.93 set - below FM threshold at 10 % (CNR 1-11 dB), so clicks |

**The ATSC apps cannot run on an N210 as written.** Its sample rates are
100 MHz over a whole number - 25, 20, 12.5, 10, 5, 2 MS/s - and 12 is
not one; UHD takes 12.5 without an error. Everything the transmitter sends
plays 4.17 % fast, and the receiver, told 12, misreads a real 12.5. The
pilot landed exactly where that predicts. Both apps ask for 12 MS/s for a
USRP (`SAMPLE_RATES` in `atscReceiver`, `samp_rate` in `atscXmitter`); 10
or 12.5 would be an N210 rate.

**Fixed the same day: 12.5 MS/s for a USRP in both.** The transmitter takes
its rate and its second resampler stage per radio from `RADIO_RATES`
(28.5 MS/s × 25/57 = 12.5 for the N210, × 8/19 = 12 for the HackRF, whose
SoapySDR module refuses 12.5, and for the VSG60); the receiver's
`SAMPLE_RATES` gives `usrp` 12.5. Then, on the air:

- **N210 transmitting**: pilot at −2690.73 kHz, 5.51 MHz wide, and
  `atscReceiver` on the BB60D **"Locked - clean stream", MER 22.6-23.3 dB,
  19.45 Mbps, 0 bad packets of 6408**, both programs' streams listed.
- **N210 receiving**: radio and app agree at 12.5 MS/s, and the pilot is
  found where it was not - RF 36 off air (+300 Hz, 41 dB clear; too weak
  here to decode, as on the BB60D) and the VSG60 at 915 MHz (+493-550 Hz,
  the program list read). **A clean decode on the N210 is not shown yet**:
  with the VSG60's ATSC encoder and the N210 receiver both on worklaptop1,
  the receiver overran (31 `O`/`D`, `usrp_source` overflows every second)
  and MER sat at 14.5-17.7 dB. On the performance governor it did the same
  (23-40 `O`/`D`, MER 17.0-17.7), with the VSG60's ATSC transmitter on 3.4
  cores, the receiver on 3.1 and worklaptop1 83 % busy. So the cause is not
  separated: two heavy apps on one 8-core machine, or one stage of the
  receiver that cannot keep up at 12.5 MS/s - the off-air run alone had no
  overruns, but it found no pilot then, so its decoder sat idle.

**The ATSC transmitter also underran** - 4 to 20 `U` in the first 20 s of
each run, against none from the 2-10 MS/s apps. After the rate fix: 10 in
one run and 0 in every one since, cause unknown. The NTSC and FM video
transmitters' few `U` (1-6) came only at start-up.

**NTSC transmit was wrong on the Ettus side - fixed, see below.** The same
app, test pattern and receiver, with the VSG60 at −9.5 dBm, decoded
"locked - picture decoding", 17.9 frames a second in colour, at −52 dBFS
on the BB60D (floor −67 dBFS with nothing on). The N210 arrived stronger,
about −44 dBFS, and gave 0-2 frames a second with no line rate measured.
Its carriers are exactly placed, and FM video at the same 20 MS/s decodes
- but FM ignores amplitude and NTSC picture is amplitude. A guess, not
checked: 20 MS/s is an interpolation of 5 from the N210's 100 MHz, odd, and
UHD warns that the CIC then droops across the band; a vestigial-sideband
picture would show that and an FM one would not.

**Confirmed and fixed the same day.** Captured side by side, the N210 at
20 MS/s carried the colour subcarrier 4.8 dB and the sound carrier 6.0 dB
higher against the picture carrier than the VSG60 did - a tilt of about
6 dB, loudest nearest the LO, which is the CIC rolloff UHD warns of ("The
requested interpolation is odd; the user should expect CIC rolloff") for
every 20 MS/s transmit on the N210. The app now runs the N210 at 25 MS/s
(`RADIO_RATE_RATIO`: the picture's 10 MS/s × 5/2 rather than × 2, an
interpolation of 4 with the halfband on); the HackRF and the VSG60 keep 20.
At 25 the colour and sound carriers came within 1.4 dB of the VSG60's, and
`ntscReceiver` on the BB60D held **"Locked - picture decoding" for 40 s,
696 frames at 16-18 a second, 0 dropped, colour**, with 2 `U`.

It needs no change to the host: on the powersave governor, 25 MS/s with
no mid-run moves underran twice in a run and decoded the same. (Some runs
underran about 1.9 million times; that was `app_tx_check.py` restoring the
sample rate with the radio's rate, so the app asked for 62.5 MS/s - fixed,
and not the governor, as was first written here.) **Something does not
recover from the mid-run moves**: changing power, moving the transmitter
100 kHz and back, and setting the sample rate again on the streaming N210
left the receiver at "picture, but only 0 frames a second" for the rest of
the run. Whether the transmitter or the receiver stayed broken is not
separated - which is why `app_pair.sh` has `TX_NO_CALLBACKS=1`.
FM video and PPM-OOK also transmit at 20 MS/s on the N210 and got the same
UHD warning; FM video decoded and measured right regardless, since FM
ignores amplitude.

### Receivers on the N210

| App | Signal | Result |
|---|---|---|
| RDS | 98.7 WMZQ off air | ✅ **100 % blocks good**, PI, PS, PTY, RadioText, stereo pilot; 2 MS/s on RX2 |
| **ATSC** | RF 36 (605 MHz) off air | ❌ **radio at 12.5 MS/s, app at 12: "pilot not found"** (fixed - pilot found; see above) at −40 dBFS. The BB60D beside it found the pilot (+196 Hz, 38 dB clear) but could not decode - the station is weak here |
| ISM | VSG60 Nexus-TH at 433.92 | ✅ every burst decoded, 30-32 dB SNR; tunes 300 kHz low on purpose |
| FM video | VSG60 at 5800 | ✅ at −5 dBm: picture, colour, 13.5-14 frames/s, deviation read right - weak, CNR 3-4 dB (at −20 dBm: "no carrier") |
| NTSC | VSG60 at 915, −20 dBm | ❌ no sync at −36 to −46 dBFS, confounded: the receive dropped packets all through (`D`, and 3-4 `usrp_source` overflows a second) |

**Receiving again, VSG60 transmitting** (performance governor, no mid-run
moves): **FM video decodes on the N210** at −5 dBm - picture in
colour, 13.5-14 frames a second, line rate within 6 ppm, deviation read
7.89-7.90 MHz of 7.93 - though weak (CNR 3-4 dB, clicks), with 1 `O`/`D`.
**ATSC and NTSC still overran** - 40 and 134 `O`/`D`, the NTSC receiver
too starved to update its own status; ATSC found the pilot at MER 17.6 dB
but did not decode. Both ran beside the VSG60's ATSC or NTSC *encoder* on
the same 8 cores; whether that, or a receiver stage too slow at its rate,
is the cause is not separated (above).

**Receiving at 20 MS/s also takes an odd decimation of 5**, and UHD says so
for the NTSC and FM video receivers just as it does for the transmitters.
FM video does not care; NTSC may, as its transmitter did - but its receiver
decimates by 2 into a decoder built for a 10 MS/s composite, so 25 MS/s on
receive needs a resampler, not a table entry. Prove it is the rolloff, with
the receiver on a machine of its own, before changing it.

**With the two ends on separate machines** - the N210 moved to the BB60D's
machine, the VSG60 left on worklaptop1 - every receiver ran with **0
`O`/`D`**, so the earlier overruns were the two apps sharing worklaptop1.
Then (no mid-run moves):

- **FM video receives well**: 18 frames a second in colour, CNR 18.7 dB,
  1.3 clicks a frame, deviation read 7.88-7.92 MHz of 7.93.
- **ATSC receives clean**: from the VSG60 at −9.5 dBm, "Locked - clean
  stream", MER 20 dB, 0 bad packets of 6900, both programs listed. At
  −20 dBm it sat at MER 17.2 dB whatever the gain (9.5 or 28.5 dB) and every
  packet failed - but the BB60D read the same VSG60 at the same MER, 17.5,
  so that limit is the VSG60's path through the air here, not the N210.
- **NTSC does not receive**: no sync at −29 and −38 dBFS (gain 50 % and
  25 %), 0 overruns, while the VSG60's NTSC decodes on the BB60D at 17.9
  frames a second. That is the N210's side; its receive at 20 MS/s is the
  odd decimation of 5 that UHD warns of, the same rolloff that broke NTSC
  transmit. Not fixed yet: the receiver decimates by 2 into a 10 MS/s
  composite, so 25 MS/s needs a resampler.

**The link carries 20 MS/s; the NTSC receiver did not keep up with it.**
UHD's `benchmark_rate` received 20 MS/s for 20 s through worklaptop1's USB
Ethernet adapter with 0 dropped samples, 0 overruns and 0 sequence errors,
and the FM video receiver at the same rate dropped nothing. So the NTSC
receiver's drops are its own processing on worklaptop1, not the network.
Rerun it on a quieter or faster host before judging its decode.

A VSG60 control at −20 dBm decoded nothing either, but it arrived at −62
dBFS, barely over the BB60D's −67 floor, so it proved nothing; at −9.5 dBm
(the level [ntsc](ntsc.md) used) it decoded at once.

## RF1 into RF2 on the one radio

The N210 transmits on RF1 and receives on RF2 at once, but only from **one
process** - whichever opens it owns it - so two apps, a transmitter and its
receiver, cannot share it. UHD's `txrx_loopback_to_file` does both in one:

```sh
$CONDA_PREFIX/lib/uhd/examples/txrx_loopback_to_file \
    --tx-args addr=<N210> --rx-args addr=<N210> --tx-freq 915e6 --rx-freq 915e6 \
    --tx-rate 1e6 --rx-rate 1e6 --tx-gain 0 --rx-gain 0 --tx-ant TX/RX --rx-ant RX2 \
    --wave-type SINE --wave-freq 100e3 --ampl 0.3 --type float --nsamps 2000000 \
    --file loop.cf32
python scripts/spectrum_report.py loop.cf32 1e6 915e6
```

With antenna 2 on each port, centimetres apart, gain 0 both ways: the tone
at +100.0 kHz, **−14.0 dBFS** - strong; leave the gains low - its mirror at
−100 kHz −46 dBc (receive I/Q imbalance), spurs at ±9.16 and ±18.3 kHz
−46 dBc, the same ones `tx_waveforms` shows, which this tool's tone shares
(so the wave table, more likely than not), and no leak at centre in the top
peaks - transmit and receive share the frequency, so the leak lands at DC
where the receiver's own DC correction takes it.

## Running the tests again

All in `scripts/`. None writes `config/`, none closes an app's window, and
each transmitter runs under `timeout -k`.

```sh
# The whole antenna test: four tones, then a minute of RDS. The N210 is driven
# over ssh from TX_HOST, the machine on its Ethernet link; the BB60D is local.
USRP_ADDR=<the N210's address> scripts/n210_ota_test.sh ant2   # N210_HOST=... if not local

python scripts/bb60_tone.py 915e6 --wait 4      # one BB60D capture; peaks in dBFS
python scripts/vsg_tone.py 915e6 -40 12         # on the VSG60's machine: a tone, 12 s
python scripts/rds_tau_sweep.py CAPTURE         # RDS from a capture, at every timing
```

The app tests above. `app_pair.sh` runs one transmitter app on TX_HOST - on
the N210, or the VSG60 as a known-good control - and judges it by a receiver
app on the BB60D, by a receiver app on the N210, or by a BB60D spectrum:

```sh
export TX_HOST=worklaptop1-fast USRP_ADDR=<the N210's address>   # TX_HOST: the VSG60
# N210_HOST: where the N210 is - 'local' (the default) or an ssh alias
scripts/app_pair.sh usrp fmRdsTransmitter frequency_mhz 915 power_percent 10 \
    bb60 rdsReceiver frequency_mhz 915 40 30           # decoded on the BB60D
scripts/app_pair.sh usrp atscXmitter cf 915 pwr 10 \
    spectrum - - 915 20e6 0.5                          # pilot, width, peaks
scripts/app_pair.sh vsg ismXmitter cf 433.92 pwr -30dBm \
    usrp ismReceiver frequency_mhz 433.92 50 30 -- interval_s=5   # VSG60 into the N210
```

Underneath it, each runnable alone on the radio's own machine:
`app_tx_check.py` (one transmitter app: values from its own dialog,
frequency and power set and confirmed before anything goes out, the radio
read back, the callbacks moved), `app_rx_check.py` (one receiver app,
headless, printing its window's status every 5 s) and `spectrum_report.py`
(width, centre, peaks and envelope of a `bb60_tone.py --out` capture). The
frequency and power keys differ per app: `cf` (MHz) everywhere but the
FM + RDS Transmitter's `frequency_mhz`; `pwr`, `rfPwr` (PPM-OOK, FM audio,
subcarrier) or `power_percent`. Receivers take `frequency_mhz` (RDS, ISM)
or `center_mhz` (ATSC, NTSC, FM video). Run the transmitters at 10 % here:
at 0 dB of attenuation the BB60D a metre away overflows, so the spectrum
captures take 20.

`uhd_find_devices` on TX_HOST gives the N210's address. Tell any other
session using the N210 or the BB60D before starting; the VSG60 and the BB60D
cannot stream on one host at the same time, which is why the transmitters run
on worklaptop1.

## Not tested yet

- Receiving above 20 MS/s (20 MS/s is clean by `benchmark_rate`; about
  25 MS/s at 16-bit samples is the usual top for an N210), and transmitting
  at 20 MS/s with no app in the way.
- An NTSC picture received on the N210 - its 20 MS/s receive, an odd
  decimation, is the suspect, and the fix needs a resampler.
- Antennas 1 and 3 again from the N210's new place. Antenna 2 from there
  (`n210_ota_test.sh`, the N210 local, corrected dBFS): 101.1 MHz −44.7,
  433.92 −56.2, 915 −36.3, 2440 −41.0 (one ADC overflow), RDS 99.5 % - not
  comparable with the table above, the geometry having changed.
- Anything through a cable and pad, and any absolute level in dBm.
- Whether the ±9.16 kHz spurs come from `tx_waveforms` or the radio.
