# dPMR (ETSI TS 102 490 / TS 102 658) and PMR446 - SDR bench note

Scope: what an engineer needs to build or decode dPMR / dPMR446 with an SDR.
Primary specs: TS 102 490 (Tier 1 peer-to-peer / dPMR446) and TS 102 658 (Modes 1/2/3).
Regulatory/equipment context: ECC Decision (15)05 and EN 303 405.
All facts below are from the texts in docs/ptt/dpmr/text/ unless under "From memory, not checked".

Sources on disk:

| Doc | Role |
|---|---|
| etsi_ts_102490_v010901.txt (V1.9.1, 2016-08) | Latest Tier 1 peer-to-peer spec used here; frame, sync, FEC, RF clauses |
| etsi_ts_102490_v010101/v010701/v010801.txt | Earlier Tier 1 editions; frame and colour-code structure match later editions per SOURCES |
| etsi_ts_102658_v020601.txt (V2.6.1, 2019-01) | Latest full dPMR spec used here; Modes 1/2/3, sync, FEC, call flows |
| etsi_ts_102658_v010101/v010201/v020401/v020501.txt | Earlier full-spec editions |
| etsi_en_303405_v010101.txt | Harmonised PMR446 equipment standard (analogue + digital) |
| cept_docdb_1491.txt | ECC Decision (15)05: 446.0-446.2 MHz rules, licence exemption |
| etsi_leaflet_digital_private_mobile_radio.txt | ETSI dPMR leaflet: tiers, 4FSK, test suites |
| etsi_en_300113_v020201/v030101.txt | Listed in SOURCES; not used for facts below |

## 1. What the system is, who uses it

- dPMR is an FDMA digital PMR system with 6.25 kHz RF carrier bandwidth [etsi_ts_102658_v020601.txt, cl.4.1/p.23; etsi_ts_102490_v010901.txt, cl.12.1.2/p.54].
- Tier 1 = low-cost licence-exempt "digital PMR446", defined by TS 102 490 [etsi_leaflet_digital_private_mobile_radio.txt, p.1].
- Tier 2 = licensed professional market: peer-to-peer (Mode 1), repeater (Mode 2), trunked/managed access (Mode 3); defined by TS 102 658 [etsi_leaflet_digital_private_mobile_radio.txt, p.1].
- Mode 1: peer-to-peer direct, MS ftx = MS frx = f1, no master/slave, asynchronous traffic channel [etsi_ts_102658_v020601.txt, cl.4.1.4.1/p.23].
- Mode 2: centralised repeater, MS ftx = BS frx = fuplink, MS frx = BS ftx = fdownlink, all traffic via BS, asynchronous [etsi_ts_102658_v020601.txt, cl.4.1.4.2/p.23-24].
- Mode 3: managed centralised repeater, master (BS) / slave (MS); beacon channel is synchronous and carries call set-up, traffic channel carries voice/data payload [etsi_ts_102658_v020601.txt, cl.4.1.4.3/p.24].
- PMR446 use: hand portable only, no base station or repeater, integral antenna only, short-range peer-to-peer simplex [cept_docdb_1491.txt, decides 9 / p.4; etsi_en_303405_v010101.txt, cl.4.1/p.8].
- PMR446 is collective shared spectrum, uncoordinated, single-frequency simplex (Tx and Rx same channel) [cept_docdb_1491.txt, p.1-2].

## 2. RF parameters

| Parameter | Value | Source |
|---|---|---|
| Channel spacing (dPMR) | 6.25 kHz carrier bandwidth | [etsi_ts_102490_v010901.txt, cl.12.1.2/p.54], [etsi_ts_102658_v020601.txt, cl.14.1.2/p.263] |
| Modulation | Constant-envelope 4FSK | [etsi_ts_102490_v010901.txt, cl.12.2.2/p.54], [etsi_ts_102658_v020601.txt, cl.14.2.2/p.264] |
| Symbol rate | 2400 symbols/s, 2 bits/symbol = 4800 bit/s gross | [etsi_ts_102490_v010901.txt, cl.12.2.1/p.54], [etsi_ts_102658_v020601.txt, cl.14.2.1/p.263] |
| Deviation index h | 0.29 | [etsi_ts_102490_v010901.txt, cl.12.2.2.1/p.54], [etsi_ts_102658_v020601.txt, cl.14.2.2.1/p.264] |
| Symbol deviation (D = 3h/2T, T = 1/2400 s) | +/-1050 Hz outer, +/-350 Hz inner | [etsi_ts_102490_v010901.txt, Table 12.1/p.54], [etsi_ts_102658_v020601.txt, Table 14.1/p.263] |
| Baseband filter | Square-root raised cosine, alpha = 0.2, T = 1/2400, cascaded with FM modulator; matched Rx filter H(f) plus de-emphasis D(f) = pi.f.T / sin(pi.f.T) specified | [etsi_ts_102490_v010901.txt, cl.12.2.2.2-12.2.2.3/p.55-56], [etsi_ts_102658_v020601.txt, cl.14.2.2.2-14.2.2.3/p.264-265] |
| Tx frequency error | Within +/-625 Hz of carrier centre | [etsi_ts_102490_v010901.txt, cl.12.1.3/p.54], [etsi_ts_102658_v020601.txt, cl.14.1.3/p.263] |
| Timebase drift | +/-2 ppm max during a transmission item | [etsi_ts_102490_v010901.txt, cl.12.1.4/p.54], [etsi_ts_102658_v020601.txt, cl.14.1.4/p.263] |
| PMR446 e.r.p. | 500 mW max, integral antenna, hand portable | [cept_docdb_1491.txt, decides 9 / p.4], [etsi_en_303405_v010101.txt, cl.4.1/p.8] |
| Duplex | PMR446/Mode 1: simplex, one frequency. Modes 2/3: paired uplink/downlink frequencies | [cept_docdb_1491.txt, p.1-2], [etsi_ts_102658_v020601.txt, cl.4.1.4.2-4.1.4.3/p.23-24] |

Dibit to deviation mapping (both specs identical):

| Bit1 | Bit0 | Symbol | Deviation |
|---|---|---|---|
| 0 | 1 | +3 | +1050 Hz |
| 0 | 0 | +1 | +350 Hz |
| 1 | 0 | -1 | -350 Hz |
| 1 | 1 | -3 | -1050 Hz |

[etsi_ts_102490_v010901.txt, Table 12.1/p.54], [etsi_ts_102658_v020601.txt, Table 14.1/p.263]

PMR446 bands and channel plans:

| Use | Band | Lowest carrier | Spacing |
|---|---|---|---|
| Analogue PMR446 | 446.0-446.2 MHz | 446.00625 MHz | 12.5 kHz |
| Digital PMR446 (old plan) | 446.1-446.2 MHz | 446.103125 MHz (6.25 kHz); 446.10625 MHz (12.5 kHz) | 6.25 / 12.5 kHz |
| Digital PMR446 (from 2018-01-01) | 446.0-446.2 MHz | 446.003125 MHz (6.25 kHz); 446.00625 MHz (12.5 kHz) | 6.25 / 12.5 kHz |

[etsi_en_303405_v010101.txt, cl.4.1/p.8-9], [cept_docdb_1491.txt, decides 2-4 / p.3-4]

Notes:

- 6.25 kHz digital PMR446 is assumed compliant with TS 102 490 [etsi_en_303405_v010101.txt, cl.4.1/p.9].
- 12.5 kHz digital PMR446 equipment is assumed compliant with TS 102 361-1 (DMR), not dPMR [etsi_en_303405_v010101.txt, cl.4.1/p.9].
- TS 102 490 also lists 149.01875-149.11875 MHz (licence-exempt use) as a second frequency range [etsi_ts_102490_v010901.txt, cl.12.1.1/p.54].
- PMR446 with latched-PTT must apply a 180 s max Tx time-out; equipment without PTT uses 180 s time-out plus VOX control [etsi_en_303405_v010101.txt, cl.4.1/p.9], [cept_docdb_1491.txt, decides 10 / p.4].
- dPMR traffic-channel item timers: TV_Item 10 s to 60 s (voice), TD_Item 1 s to 60 s (packet data) [etsi_ts_102658_v020601.txt, timer table/p.257].

## 3. Frame, burst and slot structure

Bit-rate / timing backbone (derive from 4800 bit/s):

| Unit | Bits | Time |
|---|---|---|
| Payload frame | 384 | 80 ms |
| Superframe (4 payload frames) | 1536 | 320 ms |
| End frame | 96 (24 sync + 72 data) | 20 ms |
| Header frame | 384 + preamble (72 min) | 80 ms + preamble |
| Beacon message frame (Mode 3 downlink) | 264 | 55 ms |
| Appended-data frame | 264 (120 + 24 + 120) | 55 ms |
| Packet-data frame | 384 to 1536 depending on pdS | 80 ms x (pdS+1) |

[etsi_ts_102658_v020601.txt, Fig.5.1-5.3/p.39-40, cl.4.2.2.2.1/p.28, cl.11.6/p.208-209], [etsi_ts_102490_v010901.txt, cl.4-5/p.17-19]

Traffic-channel frame set (Modes 1/2, and Mode 3 traffic):

| Frame | Figure | Content |
|---|---|---|
| Payload frame | P + (FS2 or CC) + 4x TCH(72) | Preamble only on first; 24-bit sync/code + 288 payload bits [etsi_ts_102658_v020601.txt, Fig.5.1/p.39, Table 5.1/p.40] |
| Message frame (header) | P + FS1(48) + MI0(120) + CC(24) + MI1(120) | MI0/MI1 are duplicates; preamble >= 72 bits except continuous beacon downlink [etsi_ts_102658_v020601.txt, Table 5.2/p.40-41] |
| Packet-data header | P + FS4(48) + HI0(120) + CC(24) + HI1(120) | Same FEC as message frame, different sync [etsi_ts_102658_v020601.txt, Fig.5.1/p.39] |
| End frame | FS3(24) + END data (72 = 36 repeated) | Shortened 96-bit frame [etsi_ts_102658_v020601.txt, Fig.5.1/p.39, cl.11.6/p.208-209] |

Sync words and preamble (on-air bit order as printed):

| Sync | Length | Binary | Hex | Used in |
|---|---|---|---|---|
| FS1 | 48 | 0101 0111 1111 1111 0101 1111 0111 0101 1101 0101 0111 0111 | 57 FF 5F 75 D5 77 | Non-packet header (H1), ACK |
| FS2 | 24 | 0101 1111 1111 0111 0111 1101 | 5F F7 7D | Superframe frames 1 and 3 |
| FS3 | 24 | 0111 1101 1101 1111 1111 0101 | 7D DF F5 | End frame |
| FS4 | 48 | 1111 1101 0101 0101 1111 0101 1101 1111 0111 1111 1101 1101 | FD 55 F5 DF 7F DD | Packet-data header (H2) |
| Preamble | >= 72 | repeating 0101 1111 | 5F repeated | Header / packet header / ACK |

[etsi_ts_102658_v020601.txt, cl.6.1.1-6.1.6/p.91-95], [etsi_ts_102490_v010901.txt, cl.6.1.1-6.1.6/p.27-29]

- FS4 is the symbol-wise complement of FS1; one correlator gives +peak for FS1 and equal -peak for FS4 [etsi_ts_102658_v020601.txt, cl.6.1.4/p.92].
- Preamble pattern is 5F hex (01011111), hold the repeat for preambles longer than 72 bits [etsi_ts_102658_v020601.txt, cl.6.1.6/p.95].

Channel code (TS 102 658, 24-bit, 64 values):

- Sits in superframe frames 2 and 4 and in message frames where FS2 sits in frames 1 and 3 [etsi_ts_102658_v020601.txt, cl.6.1.5/p.93, Fig.5.4/p.42].
- Mode 1/2 from frequency: 12.5 kHz raster CC = 64 x (f modulo 0.4); 6.25 kHz raster CC = [64 x (f modulo 0.4)] - 0.5; f in MHz; result 0-63 [etsi_ts_102658_v020601.txt, cl.6.1.5.1/p.93].
- Mode 3 adds a variant XORed with low 6 bits of System Identity Code [etsi_ts_102658_v020601.txt, cl.6.1.5.2/p.93].
- Full 64-entry bit/hex table is Table 6.1, e.g. CC0 = 0101 0111 0101 1111 0111 0111 (57 5F 77), CC63 = 1111 1101 1111 1101 0111 0111 (FD FD 77) [etsi_ts_102658_v020601.txt, Table 6.1/p.94-95].
- Channel codes may be assigned per channel for spectrum management or to split co-channel systems [etsi_ts_102658_v020601.txt, cl.8.3/p.100].

Channel code (TS 102 490 Tier 1, different scheme - trap):

- 12-bit code di-bit encoded to 24 bits on air (0 -> 01, 1 -> 11) [etsi_ts_102490_v010901.txt, cl.7.1/p.29].
- Tied to RF channel, not freely selectable; Group A for Initial Services, Group B for Configured Services [etsi_ts_102490_v010901.txt, cl.6.1.5/p.27].
- Explicit per-frequency tables: 446.003125-446.096875 group A excerpt, e.g. 446.003125 = 010101110111010101110111 (577577); 446.103125-446.196875 same code set reused; Group B codes start F7..., FF..., FD... [etsi_ts_102490_v010901.txt, Tables 6.1-6.2/p.27-29].

Superframe content (TS 102 658 traffic, frames 1-4):

| Frame | Sync/code | CCH carries | TCH |
|---|---|---|---|
| 1 | FS2 (24) | FN(2) + called ID upper 12 + M(3) + V(2) + F(2) + EP(1) + RSVD(1) + SLD(18) + CRC7 + FEC24 = 72 transfer | 4 x 72 = 288 |
| 2 | CC (24) | FN + called ID lower 12 + same M/V/F/EP/RSVD/SLD + CRC/FEC | 288 |
| 3 | FS2 (24) | FN + calling ID upper 12 + same | 288 |
| 4 | CC (24) | FN + calling ID lower 12 + same | 288 |

[etsi_ts_102658_v020601.txt, Tables 5.5-5.8/p.42-43], [etsi_ts_102490_v010901.txt, Tables 5.1a-d/p.17-18]

Header/message fields (TS 102 658): MT(4) + ID0+1 called/gateway(24) + ID2+3 calling/gateway(24) + M(3) + V(2) + F(2) + EP(1) + PM(1) + MI_TYPE(3) + MI_DET(8); + CRC8 + FEC40 per MI copy; MI0 = MI1 duplicate [etsi_ts_102658_v020601.txt, Table 5.2/p.40-41, cl.5.2.1/p.42].
Header fields (TS 102 490 Tier 1, simpler): HT(4) + ID0+1(24) + ID2+3(24) + M(3) + V(2) + F(2) + RES(2) + CI(11); CC is 12-bit di-bit coded to 24 [etsi_ts_102490_v010901.txt, Table 5.2/p.18-19].
Message/header types:

| TS 102 658 MT (4 b) | Meaning |
|---|---|
| 0000 | Communications_Start (superframe or END follows) |
| 0001 | Connection_Request (END or UDT follows) |
| 0010 | Disconnect_Request (END follows) |
| 0011 | ACK/NACK single frame |
| 0100/0101 | Traffic maintenance / system request / ACK reply |
| 0110 | System delivery |
| 1000-1011 | Status polling, BS command/access, broadcast |
| 1100 | Beacon ahoy / random access |
| 1110/1111 | UDT header / appended data |

[etsi_ts_102658_v020601.txt, Table 5.3/p.41]

| TS 102 490 HT (4 b) | Meaning |
|---|---|
| 0000 | Communication start (superframe follows) |
| 0001 | Connection request (END follows) |
| 0010 | Unconnect request (END follows) |
| 0011 | ACK (ACK/NACK by CI bits) |
| 0100-0110 | System request / ACK reply / system delivery |
| 0111/1000 | Status response / status request |

[etsi_ts_102490_v010901.txt, Table 5.11/p.25]

End frame fields: ET(2): 00 normal, 01 with status; ARQ(2): 00 no ACK, 01 ACK requested; WAIT(4): 0000 none, 0001 40 ms, 0010 80 ms, 0011 160 ms, 0100 320 ms; STAT(5); RES/RSVD(4) = 0000 [etsi_ts_102658_v020601.txt, cl.11.6/p.208-209], [etsi_ts_102490_v010901.txt, Tables 5.12-5.14/p.25-26].
17 info bits + CRC7 = 24, Hamming to 2x36, repeat to 72, scramble (scrambler re-initialised so the two 36-bit blocks are bit-exact copies), prefix FS3 [etsi_ts_102658_v020601.txt, cl.11.6/p.208-209].

## 4. PTT behaviour: start, floor, addressing, set-up, tear-down

Press-to-talk item (Mode 1 voice, the Tier 1 case):

1. PTT press starts TV_Item time-out countdown; Tx sends Communications_Start header then n superframes then END: H SF SF ... E [etsi_ts_102658_v020601.txt, cl.10.0/p.107-108, Fig.10.2/p.111].
2. Header for Mode 1 voice: MT = 0000, IDs, M = 000 (voice) or 001 (voice + slow data), V = 00 standard content, F = 01 peer-to-peer, EP priority; M/V/PM fields must not change after the first message that opened the connection [etsi_ts_102658_v020601.txt, Table 10.4/p.111].
3. Normal END: ET = 00, ARQ = 00, Tx_WAIT value, STAT N/A, RSVD 0000 [etsi_ts_102658_v020601.txt, Table 10.5/p.111].
4. PTT release before superframe end: finish the superframe with vocoder silence data in remaining TCH; voice+data case completes the frame with silence then remaining frames carry type-2 data [etsi_ts_102658_v020601.txt, cl.11.1/p.199-200].
5. Time-out expiry mid-voice: stop after end of current superframe plus END; must release PTT before re-Tx. Mid-data: stop immediately [etsi_ts_102658_v020601.txt, cl.10.0/p.107-108].
6. Optional called-party check first: Connection_Request (Called Party Check, M = 000-101) + END; callee answers ACK, then voice items flow [etsi_ts_102658_v020601.txt, cl.5.2.2.1/p.43, cl.4.2.3.8/p.31-33].
7. Tear-down: repeated Disconnect_Request header + END pairs. Individual call: either party may send it. Talkgroup call: only the caller may [etsi_ts_102658_v020601.txt, cl.10.1.2.4/p.111-112].

Floor and channel discipline:

- Mode 1/2 access is random ("polite" listen); Mode 3 beacon regulates access and can invite/poll [etsi_ts_102658_v020601.txt, cl.4.1.5.2-4.1.5.3/p.26-27].
- Mode 2: BS marks downlink when uplink is busy; gaps in MS uplink item are filled with downlink preservation frames; BS buffers uplink bits to re-Tx seamlessly [etsi_ts_102658_v020601.txt, cl.4.2.3.9/p.34, cl.8.4/p.101].
- END WAIT field implements break-in gap for pre-keyed stations [etsi_ts_102490_v010901.txt, Table 5.14/p.26].
- Late entry: superframe CCH repeats called/calling IDs every superframe so a receiver joining mid-call can sync [etsi_ts_102658_v020601.txt, cl.4.2.3.1/p.30, cl.11.1/p.199].

Talkgroup / addressing / floor identity:

- Air-interface station/gateway address is 24 bits [etsi_ts_102658_v020601.txt, cl.8.2/p.100].
- Standard UI: 7-digit dialling mapped into 24 bits; individual range 0000001-9999999 excluding n000000; multiple individual addresses per MS allowed [etsi_ts_102658_v020601.txt, Anx A.1.3.3.1/p.273].
- Wildcard talkgroups: up to four trailing "*" in last 4 digits (xxxxxx*, xxxxx**, xxxx***, xxx****); other placements invalid [etsi_ts_102658_v020601.txt, Anx A.1.3.3.2/p.273].
- All-MS: "*******#" (all units), "n******#" (prefix n) [etsi_ts_102658_v020601.txt, Anx A.1.3.3.3/p.273].
- All-numeric talkgroups also supported via a personalised talkgroup table in the sender [etsi_ts_102658_v020601.txt, Anx A.1.3.3.5.1/p.274].
- Comms Format F: Mode 1: 00 = Call ALL broadcast, 01 = peer-to-peer MS-MS [etsi_ts_102658_v020601.txt, Table 5.51/p.66]. Mode 2/3 traffic: 00 = BS downlink broadcast, 01 = BS uplink broadcast, 10 = BS uplink, 11 = BS downlink [etsi_ts_102658_v020601.txt, Tables 5.52-5.54/p.67].
- Comms Mode M: 000 voice, 001 voice+slow data, 010 T1 data no FEC, 011 T2 data with FEC, 100 T3 packet, 101 voice+appended T2, 110 defined by MI_TYPE, 111 cancel [etsi_ts_102658_v020601.txt, Table 5.55/p.67-68]; Tier 1 subset 000-101 [etsi_ts_102490_v010901.txt, Table 5.7/p.20].
- ACK frames exist only for individually addressed calls (CSF radios in Tier 1 wording); group calls are not ACKed [etsi_ts_102490_v010901.txt, cl.5.5/p.19-20].

## 5. FEC, interleaving, scrambling, CRC

| Function | Spec |
|---|---|
| CCH CRC | CRC7, poly X7 + X3 + 1 |
| MI/HI CRC | CRC8, poly X8 + X2 + X1 + 1 |
| Packet DATA CRC (CRC-D) | CRC16, poly X16 + X12 + X5 + 1 over data field incl. dummy bits |
| Block code | Shortened Hamming (12,8), generator poly X4 + X + 1, 8 identity + 4 parity bits per Table 7.2 |
| Scrambler | X9 + X5 + 1, preset all-1s |
| Interleavers | TCH 12x6 (72 bits); MI/HI/appended 12x10 (120 bits); Tx in columns, out rows; Rx inverse |

[etsi_ts_102658_v020601.txt, cl.7.1-7.4/p.95-97], [etsi_ts_102490_v010901.txt, cl.7.2-7.4/p.29-31]

Coding chains:

- Voice/data superframe CCH: 41 info bits + CRC7 = 48; split to 6 bytes; Hamming to 6x12; 12x6 interleave; scramble; prefix FS2 or CC; append 4x72 TCH vocoder blocks [etsi_ts_102658_v020601.txt, cl.7.5/p.98, cl.11.1/p.199].
- Header MI/HI: 72 info + CRC8 = 80; 10 bytes; Hamming to 10x12; 12x10 interleave; scramble; MI0/MI1 duplicates sent [etsi_ts_102658_v020601.txt, cl.7.6/p.98].
- END: 17 + CRC7 = 24; 3 bytes; Hamming to 3x12 = 36; repeat to 72; scramble with re-init per block [etsi_ts_102658_v020601.txt, cl.7.7/p.98].
- Appended data: 72 + CRC8 = 80; same 10x12 + 12x10 + scramble path [etsi_ts_102658_v020601.txt, cl.7.8/p.98].
- Type 2 data payload: shortened (12,8) Hamming + CRC7 per frame, 160 user bits per payload frame; Type 1: 288 user bits, no FEC [etsi_ts_102658_v020601.txt, cl.4.2.2.1/p.28].
- Packet frame PAR: N(3) frame number + LEN(8) valid bytes + DUMMY(14, zero) + CRC-D(16); DATA 288/672/1056/1440 bits for pdS 0-3; LEN limits 36/84/132/180 bytes [etsi_ts_102658_v020601.txt, Tables 9.2-9.6/p.104-105].

## 6. Voice codec

- Texts define only a "vocoder socket: 216 bits vocoder payload" and "4 x 72 bit blocks of Forward Error corrected vocoder data (TCH)" per payload frame [etsi_ts_102658_v020601.txt, glossary + cl.11.1/p.199], [etsi_ts_102490_v010901.txt, glossary/p.17].
- No codec name, rate, or algorithm is given in the searched texts (no AMBE/RALE/DVSI match in any text file).
- Silence data = vocoder output with no sound input, used to pad a released-PTT superframe/frame [etsi_ts_102658_v020601.txt, cl.11.1/p.199-200].
- Slow data rides the 18-bit SLD field per CCH (2 flag + 16 data bits; voice+slow uses continuation-flagged bytes) [etsi_ts_102490_v010901.txt, cl.5.9.1/p.21].

## 7. Encryption and authentication

- Air-interface scrambling (X9+X5+1) is a whitening scrambler, not encryption; the spec never calls it privacy [etsi_ts_102658_v020601.txt, cl.7.3/p.96].
- Only encryption bit found: a data flag "Data is not encrypted" (0) vs "Data is encrypted" (1) in the packet/UDT area [etsi_ts_102658_v020601.txt, p.103 table rows]. No cipher, key length, or key management is defined in the searched texts.
- Authentication appears only as procedures: MS/BS authentication service, registration/authentication, stun/revive with and without authentication, kill in two phases with an authentication check first [etsi_ts_102658_v020601.txt, cl.10.3.5.8.1/p.183, cl.12.3.11 refs/p.216+].
- Treat any "dPMR encryption" product claim as out-of-spec for these documents.

## 8. Detecting and decoding with SDR

What the texts support (cite these, not tool lore):

- Look for 6.25 kHz FDMA, 4FSK, 2400 symbols/s, +/-350/+/-1050 Hz lines; occupied energy fits one 6.25 kHz raster slot [citations in section 2].
- Frame cadence: 80 ms frames, 320 ms superframes, 20 ms END; preamble >= 72 bits of 0x5F before every header on traffic channels; beacon downlink is continuous so no preamble [etsi_ts_102658_v020601.txt, Fig.5.1-5.3/p.39-40].
- Correlate on FS1/FS2/FS3/FS4 bit patterns above; FS4 alarms on the negative of an FS1 correlator; CC slots (frames 2/4) will not match FS2 - use the per-frequency CC tables [citations in section 3].
- Call anatomy on one frequency (Mode 1 / PMR446): H [SF]* E; individual calls may add H+E check plus ACK; disconnect is repeated H+E [etsi_ts_102658_v020601.txt, Fig.10.2/p.111, cl.10.1.2.4/p.111-112].

### From memory, not checked

- Tool names (not in the texts): DSD+ / DSDcc with a dPMR build, SDRangel dPMR demodulator, fl2k/dumpvdl-style custom flowgraphs, GQRX/SDR#/SDR++ for spotting, inspectrum/baudline for burst measurement, GNU Radio for the 2400-baud 4FSK + RRC alpha 0.2 chain. Verify each against the sync words above before trusting a decode.
- Recording guide: sample 48 kHz or higher complex; keep at least 12.5 kHz RF bandwidth around the carrier so the full 6.25 kHz slot plus drift (+/-625 Hz) is captured; a 446.00625-446.196875 raster scan in 6.25 kHz steps covers the plans in section 2.
- Expected view: flat-ish 4FSK spectrum about 4-5 kHz wide at -20 dB with two inner/two outer deviation shoulders; 80 ms burst rhythm; preamble shows as alternating-bit (0x5F) preamble then FS1 correlation spike; voice items run seconds (TV_Item), data bursts are short H+E or H+D+E patterns.
- Unsure: exact vocoder bits-to-audio path (no codec named in texts), so a sync-locked bit dump is verifiable but audio recovery needs an out-of-spec codec.

## 9. Traps

1. FS1 vs FS4 polarity: same correlator, opposite sign. A naive FS1-only detector labels packet headers as inverted voice headers.
2. FS2 slots alternate with Channel Code: frames 1/3 carry FS2, frames 2/4 carry CC. Expecting FS2 every 80 ms fails.
3. Two different channel-code schemes: TS 102 490 uses 12-bit codes di-bit mapped to 24 bits with Group A/B per service class; TS 102 658 uses 24-bit codes 0-63 from a frequency formula. Do not mix the tables.
4. Comma decimals in the PDFs (446,003125; h = 0,29; alpha = 0,2). Copying them into code as-is breaks float parsing.
5. 12.5 kHz "digital PMR446" is DMR (TS 102 361-1), not dPMR. A 12.5 kHz-slot digital signal on 446 MHz is probably not this note.
6. 2018 band change: digital expands 446.1-446.2 down to 446.0-446.2; old scanners only covering 446.1+ miss half the plan.
7. 149 MHz Tier-1 range (149.01875-149.11875 MHz) exists in TS 102 490; a 6.25 kHz 4FSK signal there can be dPMR, not PMR446.
8. Scrambler re-initialises per END block: the two 36-bit END halves must decode bit-identical; a running scrambler across the END boundary is wrong.
9. END is only 20 ms (96 bits). Energy detectors tuned for 80 ms frames miss it.
10. ACKs apply to individual calls only; absence of ACK on a talkgroup call is normal, not packet loss.
11. Only the caller may disconnect a talkgroup call; a disconnect from a callee on a group call is suspect/spoofed.
12. Mode 2/3 gaps are filled (preservation frames, BS buffering delay). Silence on the downlink between uplink items does not mean end of call.
13. "Scrambling" is not encryption; the "encrypted data" flag has no cipher defined in these texts.
14. pdftotext layout scrambles wide tables (filter formulas, Hamming matrix, CC tables). Always cross-check bit counts: CCH 41/48/72, MI/HI 72/80/120, END 17/24/36/72.

## 10. What the documents did not give

- Voice codec name, bit rate, or decoder reference (only "vocoder socket 216 bits").
- Any voice/data cipher, key length, key management, or authentication crypto.
- Repeater uplink/downlink offsets or duplex spacing for Modes 2/3.
- Occupied-bandwidth mask numbers or adjacent-channel power limits (those live in EN 300 113 / EN 303 405 requirement clauses not mined here).
- SDR tooling, sample-rate recipes, or example recordings.
- The dPMR test suites TS 102 587 and TS 102 726 (named in the leaflet as free ETSI downloads, not fetched per SOURCES).
- Full EN 300 113 analogue-PMR detail (listed, not mined).
- Network planning, gateway/PABX/PSTN dialling detail beyond annex A addressing, and Mode 3 beacon power-save/frameset-count specifics beyond the cited clauses.
