# DVB-T2: what it is, and how it differs from ATSC

One of the notes in `knowledge/`. What applies everywhere, and which
file covers what, is in [CLAUDE.md](../CLAUDE.md).

This note teaches the standard: why it exists, where it is used, how it is
built, and how it compares with the ATSC that `atscXmitter` and
`atscReceiver` speak ([atsc](atsc.md)). The DVB-T2 app is not built yet. Its
receiver comes from skinny-fm-receiver, whose `knowledge/dvbt2.md` goes block by
block through the receiver, with the clauses of the standard it rests on.
Its bench results are summed up at the end of this note.

Most of what follows comes from general knowledge of the standards. Where a
number was measured on this bench, or by skinny-fm, it says so. Clause numbers
are EN 302 755 V1.4.1's.

## The short version

- **DVB-T2** is Europe's second-generation digital terrestrial television:
  many carriers (OFDM), strong modern error correction (LDPC + BCH), and a
  long list of options to trade capacity against robustness.
- **ATSC 1.0**, what our ATSC apps do, is North America's first generation:
  one carrier (8VSB) and older error correction (Reed-Solomon + trellis), with
  one fixed mode.
- In the same conditions, DVB-T2 carries about twice what ATSC 1.0 does
  per megahertz. It also survives echoes, moving receivers and
  single-frequency networks that 8VSB handles badly.
- The proper comparison for DVB-T2 is **ATSC 3.0**, which came eight years
  later with the same ingredients (OFDM, LDPC, layered pipes).

## Why it was created

- **The DVB Project** is an industry consortium of broadcasters, manufacturers
  and regulators, started in 1993. Its first terrestrial standard, **DVB-T**
  (ETSI EN 300 744, 1997), was COFDM with 2k or 8k carriers, QPSK to 64QAM,
  and a convolutional code inside Reed-Solomon. Most of the world outside the
  Americas and East Asia adopted it.
- By the mid-2000s two pressures had appeared:
  - **HDTV.** An HD service takes several times the bits of an SD one, and a
    DVB-T multiplex (about 20-24 Mb/s in practice) held only two or three.
  - **The digital dividend.** Governments wanted UHF spectrum back for mobile
    broadband, first the 800 MHz band and later 700 MHz, so television had to
    fit more into fewer channels.
- **DVB-S2** (2005) had shown that LDPC codes run within a fraction of a dB of
  the Shannon limit. DVB-T2 took that coding and rebuilt the rest of the
  terrestrial system around it. The work started in 2006, the specification was
  published in 2008, and it became ETSI EN 302 755 in 2009.
- **The goal** was at least 30 % more capacity than DVB-T in the same channel,
  with the same transmitter sites and the same rooftop aerials. In practice it
  gives about 50 % more. The UK's switch from 64QAM DVB-T (about 24 Mb/s) to
  256QAM DVB-T2 (about 40 Mb/s) in one 8 MHz channel is the usual example.
- **T2-Lite** was added in V1.3.1 (2011): a subset for mobile and handheld
  reception, sent in the gaps (FEF parts) of an ordinary T2 signal.

## Where it is used

- **Europe:**
  - The UK (Freeview HD) was first to air, in December 2009.
  - Germany switched in 2017 with HEVC video.
  - Italy is moving to it, along with much of Eastern Europe.
  - Some countries, France among them, still run DVB-T with newer video
    codecs.
- **Elsewhere:** Russia and other former Soviet states; much of Africa (Kenya,
  Nigeria, South Africa and others); South-East Asia (Thailand, Vietnam,
  Malaysia, Singapore, Indonesia); India; and Colombia, the one large DVB
  country in Latin America.
- **Not used in:**
  - North America and South Korea, which use ATSC.
  - Japan and most of Latin America, which use ISDB-T.
  - China, which uses DTMB.
  - The US is moving voluntarily to ATSC 3.0 rather than to DVB-T2.
- **Channel plan:** in ITU Region 1, UHF channels are 8 MHz wide: channel N is
  centred on 306 + 8N MHz, for channels 21 to 69, though 49 and up have mostly
  gone to mobile. VHF Band III uses 7 MHz. DVB-T2 also defines 1.7, 5, 6 and
  10 MHz channels, so it fits 6 MHz countries too.

## How DVB-T2 is built

From the transport stream to the antenna, roughly in transmit order.

### Input and baseband frames

- **Input:** one or more MPEG transport streams (or GSE for IP). Each goes
  into its own **PLP**, a physical layer pipe.
- **PLPs:**
  - Each PLP has its own modulation, code rate and time interleaving. One
    signal can carry a rugged QPSK pipe for portable receivers beside a 256QAM
    pipe for rooftop HD.
  - Most national networks use one PLP. Multi-PLP is used where a network
    wants different robustness per service.
  - A common PLP can carry the tables that several pipes share.
- **BBFRAMEs:** the stream is cut into baseband frames, each with an 80-bit
  header and scrambled with a PRBS (EN 5.1, 5.2). Null packets can be
  deleted on the way in and put back at the receiver, which saves capacity.

### Error correction

- **Outer BCH** (corrects up to 10 or 12 bit errors), inside **LDPC**
  codewords of 64,800 bits (normal) or 16,200 bits (short). The same family
  as DVB-S2.
- **Code rates:** 1/2, 3/5, 2/3, 3/4, 4/5 and 5/6 (EN 6.1).
- LDPC is what makes the system: it works within about 1 dB of capacity.
  Decoding it is also the main computing cost. A live receiver needs compiled
  SIMD code, as skinny-fm found: numpy alone is 10-40 times too slow.
- **Bit interleaving** (EN 6.1.3) spreads each codeword's bits across the
  constellation's more and less reliable bit positions.

### Modulation

- **QPSK, 16QAM, 64QAM and 256QAM** (EN 6.2). 256QAM is new in T2. DVB-T
  stopped at 64QAM.
- **Rotated constellations** (EN 6.3): the constellation is turned by a small
  angle and its Q part is sent in a different cell from its I part.
  - If a fade or an echo destroys one cell, the other still carries a
    projection of the same point.
  - It is worth roughly a dB or more in difficult channels, at no cost in
    capacity. A real receiver has to undo it.
- **Interleaving:** cell interleaving (EN 6.4) within a codeword, then **time
  interleaving** (EN 6.5) across up to one or more frames, which rides out
  impulsive noise and short fades.

### The frame

- **The superframe** is a run of T2-frames, with FEF parts optional between
  them (EN 8.2).
- **A T2-frame** is one P1 symbol, then 1-16 P2 symbols, then the data symbols
  (EN 8.3). A frame lasts at most 250 ms.
- **P1** is a short, fixed 1k symbol that any receiver can find blind
  (EN 9.8). It says whether the signal is T2 or something else, the FFT size,
  and roughly which guard interval.
- **P2** carries the **L1 signalling**:
  - **L1-pre:** fixed size, always BPSK, heavily protected. It gives the
    guard, the pilot pattern, how big L1-post is, and how L1-post is coded.
  - **L1-post:** per PLP, the modulation, code rate, interleaving and where in
    the frame the PLP sits.
  - A receiver can do nothing until L1-pre decodes. It decodes at about
    -4 dB SNR.

### OFDM

- **FFT sizes:** 1k, 2k, 4k, 8k, 16k and 32k carriers. 16k and 32k are new in T2.
  - Bigger FFTs mean longer symbols, so the same guard time is a smaller
    share of the symbol. That is how T2 affords long guards for large
    single-frequency networks.
  - The cost: carriers sit closer together, so the signal suffers more from
    Doppler (mobile reception) and from phase noise.
- **Extended carrier mode** (8k, 16k and 32k) uses more of the channel's edges
  for data, about 2 % more capacity.
- **Guard intervals:** seven, from 1/128 to 1/4 of the symbol (19/256 and
  19/128 are new). The guard absorbs echoes, including the "echoes" that are
  really other transmitters in the same network.
- **Pilot patterns PP1-PP8** (EN 9.2): scattered pilots placed at different
  densities. Denser pilots follow faster-changing channels and longer echoes.
  Sparser ones carry more data. The pattern is chosen with the guard.
- **The elementary period:** 7/64 µs at 8 MHz, so the native sample rate is
  64/7 = 9.142857 MS/s. Our radios run at 10 MS/s and resample.

### Network features

- **SFN (single-frequency network):** every transmitter sends the same signal
  on the same frequency, time-locked. The receiver sees the others as echoes,
  which the guard and the equaliser handle. A whole country can then use one
  channel per multiplex. That is a big part of the spectrum saving, and it is
  close to impossible with 8VSB.
- **MISO:** two transmitters send coded versions of each symbol (a modified
  Alamouti code). In an SFN this keeps two equal-strength paths from
  cancelling each other.
- **PAPR reduction:** OFDM has a high peak-to-average power ratio, which costs
  transmitter amplifier efficiency. T2 offers active constellation extension
  and tone reservation to reduce it. Receivers can ignore both.

### The numbers

- **Capacity in 8 MHz:** from about 7.5 Mb/s (QPSK 1/2) to about 50 Mb/s
  (256QAM 5/6, small guard, sparse pilots).
  - The UK's HD multiplexes: 32k, 256QAM 2/3, guard 1/128, PP7, about
    40 Mb/s.
  - The layout skinny-fm sent on this bench: 16k, guard 19/128, PP2,
    QPSK 3/5, 7.32 Mb/s.
- **Thresholds** (Gaussian noise, roughly). skinny-fm's synthetic tests agree:
  64QAM 3/5 decoded at 12.8 dB and 16QAM 3/5 at 8.5 dB.

  | Setting | About |
  |---|---|
  | QPSK 1/2 | 1 dB SNR |
  | QPSK 3/5 | 2-3 dB |
  | 16QAM 3/5 | 8-9 dB |
  | 64QAM 3/5 | 12-13 dB |
  | 256QAM 3/5 | 17 dB |
  | 256QAM 2/3 | about 19-20 dB |

  A real receiver wants a few dB of margin over these, more in multipath.

## How it differs from ATSC

ATSC 1.0 (A/53, 1995) is what `atscXmitter` and `atscReceiver` speak.

| | ATSC 1.0 | DVB-T2 |
|---|---|---|
| Carrier | one: 8VSB, vestigial-sideband 8-level | OFDM, 1k-32k carriers |
| Channel | 6 MHz only | 1.7, 5, 6, 7, 8 or 10 MHz |
| Error correction | Reed-Solomon (207,187) + 2/3 trellis | BCH + LDPC, six rates |
| Modulation | fixed: 8 levels | QPSK to 256QAM, rotated or not |
| Capacity | 19.39 Mb/s, fixed | about 7.5-50 Mb/s in 8 MHz, chosen per network |
| Threshold | about 15 dB SNR, fixed | about 1 to 20 dB, chosen per network |
| Echoes and SFN | an adaptive equaliser; short echoes only; SFN hard | the guard absorbs echoes; SFN is the normal way to build a network |
| Mobile reception | poor (ATSC-M/H was a later add-on) | fair, better with T2-Lite and a robust PLP |
| Sub-channels | one pipe; services multiplexed in the TS | up to 255 PLPs, each its own robustness |
| Service information | PSIP (MGT, VCT, EIT on their own PIDs) | DVB SI: SDT, EIT, NIT (EN 300 468) |
| Video in practice | MPEG-2 (H.264 allowed by A/72) | H.264 or HEVC |
| Audio in practice | AC-3 | MPEG-1 Layer II, AAC/HE-AAC, AC-3/E-AC-3 |
| Peak-to-average | lower, single carrier (measured 8.7 dB here: [atsc](atsc.md)) | high, OFDM, about 10-12 dB before PAPR reduction |
| Receiver complexity | modest | high: big FFTs, interleavers, LDPC |

What each of those means on this bench:

- **Same transport stream.** Both carry MPEG-2 transport streams of 188-byte
  packets. ffmpeg plays either, and `VideoPipe` already shows both. Only the
  service tables differ: PSIP against DVB SI.
- **A fixed mode against a chosen one.** An ATSC receiver needs nothing but
  the channel. A T2 receiver first has to find P1, read L1, and only then
  knows the FFT size, guard, pilots, modulation and rate. That is why
  skinny-fm's receiver "learns" for a second before it decodes.
- **Spectrum.** 8VSB has a pilot tone and a sharp-edged single-carrier
  spectrum. T2 is a flat block with vertical walls and no pilot tone. On a
  spectrum display, T2 looks like flat noise across about 7.6 MHz
  (7.77 MHz in extended mode).
- **Level.** Both are noise-like. A wide channel spreads its power, so on
  the BB60D an 8 MHz channel sits about 38 dB lower per bin than a tone of the
  same power. skinny-fm's -30 dBm test showed nothing for that reason, and
  -10 dBm gave about 12 dB SNR over the bench antennas.
- **Timing.** T2's long symbols tolerate a sample clock a few ppm off only if
  the receiver follows it. skinny-fm's receiver fits the P1 spacing and
  resamples. 8VSB's symbol-timing loop does the equivalent all the time.

ATSC 3.0 (A/322, 2017) answers DVB-T2 point for point: OFDM 8k-32k, LDPC +
BCH, QPSK to 4096QAM, layered division multiplexing, and a transport over IP
instead of MPEG-TS. It is not compatible with ATSC 1.0 receivers, which is
why the US move to it is slow and voluntary.

## Pros and cons

**Pros**

- **Capacity.** About 50 % more than DVB-T, and about twice ATSC 1.0 per MHz,
  in the same conditions.
- **Flexibility.** One standard covers rooftop HD, portable and mobile, all
  set per network, or per PLP within one signal.
- **SFN.** National networks on one frequency per multiplex, which frees
  spectrum.
- **Robustness.** Rotated constellations, long time interleaving and LDPC
  survive multipath, impulsive noise and fades that DVB-T and 8VSB do not.
- **Open.** The ETSI documents are free to download, and the DVB Project's
  patent pool licence is a modest per-device fee.
- **Reuses broadcast plant.** Same channels, same masts and same aerials as
  DVB-T. Only the transmitter's modulator and the viewers' receivers change.

**Cons**

- **Not backward compatible.** A DVB-T box cannot receive DVB-T2. Every
  country that switched had to replace receivers or set-top boxes.
- **Receiver cost and power.** The LDPC decoder and the 32k FFT's memory took
  years to get cheap. A software receiver needs a lot of CPU, as skinny-fm
  measured: six worker processes for 64QAM.
- **Slow to lock.** Find P1, read L1, then wait for time interleaving to
  fill. Channel changes are slower than on DVB-T.
- **Too many options.** Interoperability depends on national profiles, and
  receivers often support only the common subset (single PLP, no MISO, no
  T2-Lite).
- **High peak-to-average power,** which costs transmitter efficiency unless
  PAPR reduction is used.
- **Large FFTs dislike mobility.** 32k carriers are about 280 Hz apart at
  8 MHz, so a fast-moving receiver's Doppler spreads into its neighbours.
  Mobile reception needs smaller FFTs or T2-Lite.

## On this bench

The app is planned, not built. What is known so far:

- **Transmit:**
  - gr-dtv in our GNU Radio 3.10 has a complete DVB-T2 transmitter
    (`dtv.dvbt2_*` blocks), single PLP.
  - skinny-fm built test clips with it: an ffmpeg transport stream, then
    gr-dtv, resampled to 10 MS/s and cut from one P1 to another so the VSG60
    loops it from its own memory without a seam.
  - Looping H.264 needs its SPS and PPS repeated at every keyframe
    (`repeat-headers`), or a receiver that joins mid-loop never shows a
    picture.
- **Receive:** skinny-fm's receiver uses numpy, with a native LDPC built per
  machine from xdsopl/LDPC (0BSD). It decodes several programmes in one
  multiplex, with names and now/next from the SDT and EIT.
- **Over the air, VSG60 to BB60D at 594 MHz, by skinny-fm:**
  - At -30 dBm nothing was received.
  - At -10 dBm the signal arrived at about 12 dB SNR, with the clocks 0.4 ppm
    apart.
  - Colour bars, colour bars with a tone, and a recorded programme with
    HE-AAC sound all decoded at 99-100 % of codewords.
- **Rates:** 10 MS/s suits all three transmitters here. The Ettus N210
  makes it as 100/10, an even ratio: see
  [ettus](ettus.md#every-app-on-the-n210-2026-11-21).
- **Several channels:** a multiplex carries several programmes in one PLP,
  each named in the SDT.
  - At QPSK 3/5 (7.3 Mb/s) about three SD programmes fit. At 16QAM 3/5
    (about 15 Mb/s), five or six. At 64QAM 3/5 (about 22 Mb/s), eight or
    nine.
  - The thresholds above are what each needs at minimum, plus a few dB of
    margin. The 12 dB the bench gave at -10 dBm covers QPSK and 16QAM, but
    not 64QAM.
- **The public repo:** real broadcasts' L1 carries network identifiers
  (CELL_ID, NETWORK_ID, T2_SYSTEM_ID). They never go into this repo. Test
  with gr-dtv's synthetic signals.

## Sources

- ETSI EN 302 755 V1.4.1 (2015), the DVB-T2 standard; free from etsi.org.
- ETSI TS 102 831, implementation guidelines (receiver algorithms).
- ETSI EN 300 468, DVB service information (SDT, EIT, NIT, text encoding).
- ETSI EN 300 744, DVB-T; EN 302 307, DVB-S2 (the shared LDPC and BCH).
- ATSC A/53 (ATSC 1.0) and A/322 (ATSC 3.0 physical layer), free from atsc.org.
- skinny-fm-receiver's `knowledge/dvbt2.md`: the receiver block by block,
  with clause references and its measurements.
