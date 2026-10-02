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
