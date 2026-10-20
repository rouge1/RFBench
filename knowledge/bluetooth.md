# Bluetooth from a VSG60: what is possible, before any code

One of the notes in `knowledge/`. What applies everywhere, and which
file covers what, is in [CLAUDE.md](../CLAUDE.md).

Nothing here has been built or measured yet. It is what the Bluetooth
Core specification (v6.0, in `/data/python/bluey-ox-walker/knowledge/`) and
bluey-ox-walker's own notes say, set against what
[radios.md](radios.md#vsg60-notes) records about the VSG60. Spec
references are to Core v6.0, whose printed page numbers equal the PDF's.

## The short answer

- **Bluetooth Low Energy advertising: yes.** It is a one-way broadcast
  on three fixed channels, 1 Msym/s GFSK, 2 MHz wide - well inside what
  the VSG60 does. Two of the three channels fit in one tuning.
- **Classic Bluetooth (BR/EDR) as a real link: no.** It hops over 79 MHz
  1600 times a second, twice the VSG60's bandwidth and far faster than a
  retune, and a link needs both sides talking; a transmit-only radio can
  only ever be half of one.
- **Classic Bluetooth packets on one fixed channel: yes**, and
  bluey-ox-walker has asked for exactly that as a test signal - see
  [a test signal for bluey-ox-walker](#a-test-signal-for-bluey-ox-walker).

## What the VSG60 brings

From [radios.md](radios.md#vsg60-notes): 30 MHz - 6 GHz, 12.5 kS/s -
50 MS/s, a calibrated −120 to +10 dBm. The 2.4 GHz band, 2400-2483.5 MHz,
is well inside the range. Its instantaneous bandwidth is about 40 MHz,
so the 78 MHz from 2402 to 2480 MHz does not fit in one tuning.

## Bluetooth Low Energy

**Channels** (Vol 6 Part A §2; Vol 6 Part B §1.4.1, Table 1.3): 40
channels 2 MHz apart, 2402 to 2480 MHz. Three are the primary
advertising channels, and they are not numbered in frequency order:

| Index | Frequency |
|---|---|
| 37 | 2402 MHz |
| 38 | 2426 MHz |
| 39 | 2480 MHz |

Indices 0-36 fill in the rest (0 = 2404, 10 = 2424, 11 = 2428, ... 36 =
2478 MHz).

**Modulation** (Vol 6 Part A §3.1; Vol 1 Part A §3.2.2):

- LE 1M: GFSK, BT = 0.5, 1 Msym/s, modulation index 0.45-0.55 - so a
  deviation of about ±250 kHz. A 1 is the positive deviation.
- The deviation of a 1010 pattern has to be at least 80 % of a 00001111
  pattern's, and never under 185 kHz.
- Symbol timing is within ±50 ppm.
- LE 2M is the same at 2 Msym/s; LE Coded is 1 Msym/s with forward
  error correction, 125 or 500 kb/s. Every device has to receive LE 1M,
  so that is the one to send.

**Frequency tolerance** (§3.3, Table 3.5): the centre may be off by
±150 kHz over a packet, drifting less than 50 kHz within one. A
calibrated generator has no trouble here; the error that matters will
be ours - a wrong offset in the baseband.

**Power** (§3, Table 3.1): a device's maximum is somewhere between
−20 dBm and +20 dBm. The VSG60's +10 dBm is a Class 1.5 device's level.
A receiver has to take −10 dBm at its input (§4.5), and its sensitivity
is −70 dBm or better (§4).

**Spectrum** (§3.2, Table 3.3): at 1 Msym/s, −20 dBm at 2 MHz from the
centre and −30 dBm at 3 MHz and beyond. Gaussian shaping at BT = 0.5
does this by itself; the thing to watch is the VSG60's own images and LO.

### Two advertising channels at once, and the LO out of the way

Channels 37 and 38 are 24 MHz apart. Tuned to 2414 MHz, the VSG60 can
put one copy of the signal 12 MHz below and one 12 MHz above in the same
baseband, at 32 MS/s say. That also answers
[the carrier that never turns off](ism.md#the-carrier-never-turns-off):
the LO leak sits at 2414 MHz, on no Bluetooth channel at all.

Channel 39, at 2480 MHz, is 54 MHz from 38 and needs a tuning of its
own. It is not needed: a scanner listens on all three in turn, and an
advertiser heard on one is heard. A phone or a sniffer listening on 39
only will miss it, and that is the price.

### What goes in the packet

The packet itself - preamble, access address, PDU, CRC and whitening,
the advertising PDU types, the address, the advertising data and the
timing - is in [ble-packets.md](ble-packets.md), with a few lines of
Python that reproduce the spec's worked packet bit for bit. The short
of it: `ADV_NONCONN_IND` is the one advertisement that asks for no
reply, and the whitening depends on the channel index, so the copies
on 37 and 38 are not the same bits.

What we would advertise is our own: a name and data of our choosing,
and a made-up address in the range the spec sets aside for that. Copying
a real product's advertisement - a tracker, a lock, someone's phone -
is not something this bench does.

## Classic Bluetooth (BR/EDR)

From bluey-ox-walker's notes (`README.md`, `knowledge/architecture.md`,
`knowledge/pitfalls.md`, `knowledge/clk27.md`):

- 79 channels, 1 MHz apart, 2402-2480 MHz.
- 1 Msym/s GFSK for the access code and header always. The payload is
  GFSK for Basic Rate, π/4-DQPSK for EDR 2 Mb/s and 8-DPSK for EDR 3 Mb/s.
- 625 µs slots, 1600 hops a second. Each slot's channel comes from the
  master's address and its 27-bit clock.
- bluey's receivers see 19 channels in 20 MHz, and about 26 on a BB60D
  at 40 MS/s. The VSG60 is in the same position going the other way.
  bluey's notes call this "geometry, not a decode failure".

Basic Rate GFSK is BT = 0.5 with a modulation index of 0.28-0.35, a 1 the
positive deviation, and symbol timing within ±20 ppm (Vol 2 Part A
§3.1.1, p. 434). bluey's README once said BT = 0.35; that was the index
mistaken for BT, and bluey has corrected it.

A hopping transmitter would reach a receiver only on the channels both
can see. How fast the VSG60 retunes has not been measured; `vsgAbort` alone takes
0.1 s ([radios.md](radios.md#vsg60-notes)), so 625 µs is not going to happen.

Hopping without retuning is possible, though. Adaptive frequency hopping
(AFH) narrows the hop set to a map the master sends in `LMP_set_AFH`, and
the map may be as small as 20 channels (Nmin, Vol 2 Part B §2.3.1). With
20-40 adjacent channels, every hop falls inside one VSG60 tuning and is a
frequency shift in baseband. That is stage 3 of
[the plan with bluey](bluey-test-signals.md#stages).

### A test signal for bluey-ox-walker

`knowledge/architecture.md` in bluey-ox-walker names the gap: its decoder
handles the multi-slot Basic Rate packets (DM3, DH3, DM5, DH5) bit for
bit on synthetic data, but the only hardware it has heard, Cypress and
Broadcom chips, never sends them as plain Basic Rate. A synthetic
transmission is what it says would settle the on-air demodulation. A
VSG60 on one fixed channel, with no hopping, inside the window bluey is
tuned to, is that transmission.

What its notes say an encoder has to get right, each found the hard way:

- **The header is whitened first, then FEC 1/3 is applied** - the
  reverse order was its biggest decoding bug for months, and a correct
  FEC vote does not prove the bits are right.
- **Whitening in BR/EDR is seeded from the clock**, not from the channel
  as in LE.
- **The header's HEC polynomial is 0xA7**, and the header fields go out
  least significant bit first.
- The length field is 9 bits, except in 2-DH5 and 3-DH5, where it is 10.
- Its decoder wants a sharp channel filter before the discriminator, so
  a signal should sit on a channel centre with little frequency offset,
  or leakage puts it on the neighbouring channel.

Its verification chain can grade what we send without new receiver
code:

1. `apps/scan_live.py` decodes live.
2. `bin/export_pcap_btbb.py` writes a Wireshark capture.
3. `ubertooth-rx` cross-checks it independently.

## The bench

- **The band is unlicensed, but the equipment is not certified.** A
  VSG60 is a signal generator, not a certified 2.4 GHz device, so the
  same thinking as
  [ism.md](ism.md#the-bands-and-what-is-actually-legal) applies:
  go over a cable and a pad, not an antenna, unless the level is
  down to the point where nothing beyond the bench can hear it. Wi-Fi
  and every Bluetooth device in the building share this band.
- **Pad before any receiver.** +10 dBm into a HackRF is 15 dB past its
  −5 dBm limit; see
  [the bench](ism.md#the-bench-a-cable-and-a-pad-not-an-antenna).
  The VSG60A-to-BB60D bench for bluey has no pad (a bare SMA cable,
  2026-10-18): `scripts/bt_bench_play.py` refuses any level that would put
  more than −20 dBm into the BB60D (level minus the stated `--pad-db`),
  30 dB under its +10 dBm maximum input. A HackRF on that cable would
  still need a pad.
- **Calibrated on that bare cable (2026-10-19).** BB60D at 40 MS/s on
  2439.0 MHz, 60 % gain; VSG60A on the work laptop playing
  `bench_885614_d23` once at −90 dBm.
  - **The noise floor is −90.7 dBm in 1 MHz**, flat across ±13.5 MHz.
    A 20 s noise-only recording (VSG not opened) had no spur more than
    1.8 dB over the median, no DC spike, and no lost samples or ADC
    overflows.
  - **The BB60D's samples read in mW.** Received bursts were compared with
    the shot over the same time-frequency cells: a |iq| = 1 burst set to
    −90 dBm arrived at 7.1e-10, i.e. −91.5 dBm. That 1.5 dB is the cable
    plus both instruments' calibration, from one point.
  - **So −90 dBm gives −0.8 dB in 1 MHz, and 20 dB in 1 MHz needs about
    −69 dBm at the BB60D.** Through a pad, add its value at the VSG (30 dB
    would make it about −39 dBm) and re-measure, because a pad's real loss
    is rarely its label.
  - **`bt_bench.py align` cannot lock that low.** At −90 dBm it refused
    (best start's median correlation 0.17, under 0.40). The shot was found
    instead by per-channel energy: 1 MHz bins in 10 us steps,
    cross-correlated with the shot's own map, a 450-sigma peak.
    Calibration shots belong around −80 dBm or above.
- **Re-measured at −69 dBm the same afternoon: 19.0 dB, not 20.**
  `bt_bench.py snr` measures it: per channel, a burst's whole power
  (channel ±1 MHz, against the shot's own cells, so leakage cancels)
  over the noise in 1 MHz, the definition the synthetic files use. It
  read 18.96 dB (channels 18.6 to 19.4) here and −0.89 dB at −90 dBm,
  where `align` cannot lock; planted captures come back within 0.03 dB
  at 14 and 20 dB. The scratch measurement first quoted, 18.8, took
  only the channel's own bin and read 0.2 dB low.
  - **Both levels are below the VSG60A's specified −55 to +7 dBm**
    ([signal-hound-specs.md](signal-hound-specs.md)); `vsg_sink` takes
    −120 dBm, but that is the driver's range, not a calibrated one. It
    still stepped linearly: bursts arrived at −70.8 dBm, 0.3 dB from −90
    scaled by 21 dB. The SNR the BB60D measures is the figure to trust,
    not the level set.
  - **The noise floor rose 1.0 dB between morning and afternoon**, to
    −89.7 dBm in 1 MHz: flat across the band, constant over the
    recording, and there before the VSG was opened, so the BB60D's own
    (warm-up, probably; not established). Measure the SNR on every
    recording; at that floor 20 dB needs about −68 dBm.
  - **The chain has a group delay that differs by channel**: against
    one clock line, each channel's bursts sat at their own mean offset,
    −0.9 to +0.6 samples at 40 MS/s (±20 ns), not monotonic in
    frequency. It made `align`'s timing scatter 0.60 at 19.0 dB, where
    the dry runs gave 0.40 at 20 dB; with each channel's mean taken out
    it was 0.44. So the scatter is not a level check on this bench, and
    the line's truth for a burst is off by its channel's offset.
  - `locate_score` was 0.857, above the dry run's 0.841 at 20 dB: the
    BB60D's noise is band-limited, the dry run's AWGN is not, so dry-run
    scores do not carry over either.
  - **The per-channel spread is the BB60D's floor, not the signal path.**
    Across 2433-2450 MHz the received burst power was flat within 0.09 dB
    (−70.77 to −70.86 dBm); the noise in 1 MHz fell from −89.5 dBm at
    2433 to −90.3 at 2450, and that 0.8 dB is the SNR's range.
  - **A recording session** goes through `scripts/bt_bench_session.py`:
    `start` records at the session's level, the player is run by hand
    (so every transmission is its own command), `finish` measures the
    SNR and logs the registration's own decision (its `LevelSession`, run
    by path and replayed over every earlier attempt, so nobody types a
    level or picks an outcome), and `close` aligns only a KEEP - with the
    console to a log, since it prints carrier values bluey's registration
    keeps unread - and copies to the drive. Aligning after the decision
    means no aligner output exists when it is made. A copy is hashed from
    the disk with its cached pages dropped first; `sha256sum -c` straight
    after `cp` reads the page cache, not what reached the drive. Each line
    of `session.jsonl` carries the sha256 of the line before it, so the
    order of measurements and decisions is checkable afterwards: `verify`
    checks the chain, then the order the registration asserts (an
    alignment only after that recording's KEEP, one KEEP per slot, no
    slot recorded again once aligned, nothing after a decision but the
    close; a start only once the recording before is decided and on the
    drive; nothing after NOT DECIDABLE; the open retake slot next; the
    decision's numbers those of its own snr line). The replay reads the
    decision file's bytes, hashes them and compiles them itself: `python
    -B` only stops a `.pyc` being written, and an existing one whose
    source size and mtime match is still loaded - a file rewritten at the
    same length within the same second ran its old code. Every command
    refuses when the laptop's log and the drive's differ, so a write that
    reached one, or a fresh drive folder, cannot start a second session. A
    copy goes to the drive as `.partial`, is hashed from the disk, and is
    renamed only when every file matches; a mismatch leaves the partials
    for the user and blocks the next recording. The chain is unkeyed: it
    shows a line changed, dropped or moved, not a log rewritten from its
    first line, which needs its head hash kept somewhere else.
  - The two references held one relation: the carrier error (+887 Hz)
    was −2439 MHz × the clock error (−0.3628 ppm = +885 Hz), as it is when
    each instrument's LO and sample clock share one reference.
- **To use the BB60D as the receiver, put the VSG60 on another host** -
  for the bluey work, the work laptop (`ssh worklaptop1`), which has the
  VSG60, the `gnu` environment and this repo. The two will not stream on
  one host ([radios.md](radios.md#vsg60-notes)).
- **For LE, a phone is the plainest referee**: a BLE scanner app shows
  the advertisement, its name and its RSSI.
- **A spectrum average undersells a bursty signal.** Advertising is a few
  hundred microseconds every tens or hundreds of milliseconds; look at
  the envelope in time, as [atsc.md](atsc.md#atsc-transmitter) found.
- bluey-ox-walker tunes a HackRF to 2411 MHz so its own DC spur lands on
  no channel centre, and a BB60D to 2441 MHz to stay out of Wi-Fi
  channel 6. A receiver here should take the same care.

## Not done yet

- Measure how fast the VSG60 retunes.
