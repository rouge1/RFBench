# Bluetooth Core specification: the sentences the synthetic files rest on

Where a generator or a test says "the spec has it so", this is the section and the words. Read from
**Core v6.0** (Vol 2 Part B, Baseband; "Version Date: 2024-08-27"), the PDF kept in
bluey-ox-walker's `knowledge/` folder and not copied here (the spec is not ours to republish), and checked
against v5.0, which has the same sentences with the older Master/Slave wording. The section numbers are the
document's own headings. Quotations are short; the rest is in the PDF.

Read with [bluey-test-signals.md](bluey-test-signals.md), which says what was built from it, and
[bluetooth.md](bluetooth.md).

## Reserved LAPs and the DCI - Part B 1.2.1 "Reserved addresses"

> The reserved LAP addresses are 0x9E8B00 to 0x9E8B3F. The general inquiry LAP is 0x9E8B33. [...]
> The default check initialization (DCI) is used as the UAP whenever one of the reserved LAP addresses is
> used. The DCI is defined to be 0x00 (hexadecimal).

So an inquiry LAP (the GIAC 0x9E8B33 or one of the 63 dedicated inquiry LAPs) **always has UAP 0x00**: it is
not a device's UAP, it is the value HEC and CRC are initialised with. A vote of 0x00 on such a LAP is
correct. A real device whose BD_ADDR has UAP 0x00 is also legal; the DCI only makes 0x00 common on
inquiry LAPs. None of the 64 reserved LAPs can be part of a user BD_ADDR, so a device LAP in a test file
must stay outside 0x9E8B00..0x9E8B3F (the reason `bt_synth_slotb` uses 0x635FCA and not the GIAC).

## The HEC/CRC initialisation in each substate - 6.4.6 "HEC" and 7.1 "Error checking"

6.4.6, the HEC: in the Central Page Response substate the **Peripheral's UAP** is used; "For FHS packets and
extended inquiry response packets sent in Inquiry Response substate, the default check initialization (DCI,
see Section 1.2.1) shall be used. In all other cases, the UAP of the Central shall be used."

7.1, the same rule for HEC and CRC:

> In the Inquiry Response substate, the DCI value shall be used for the FHS and Extended Inquiry Response
> packet.
> In the Central Page Response substate, the UAP of the Peripheral shall be used in the FHS packet.

6.5.1.4 "FHS packet": "When initializing the HEC and CRC for the FHS packet of inquiry response, the UAP
shall be the DCI."

What this means in a page: the paged device is the Peripheral, so the FHS of a page response is checked
with the **paged device's UAP**, not the master's; the master's own LAP, UAP, NAP and clock are in the
FHS payload. In an inquiry response the FHS is checked with 0x00.

## The access code of a page - 1.3 "Access codes"

> The device access code is used during Page, Page Scan, Central Page Response, and Peripheral Page
> Response substates and shall be derived from the paged device's BD_ADDR.

So IDs (the page, the paged device's response and its acknowledgement) **and the master's FHS** all carry the
paged device's access code. Only after the exchange, from the first POLL, does the channel access code of
the master's LAP appear. A paged LAP therefore carries IDs and one FHS per exchange, and a UAP claim on it
can come only from the FHS (an ID has no header).

## Where the rest of a page exchange is - 8.3.3.2 "Central Page Response"

The substate in which the master sends the FHS: 1250 us after the first ID of the heard slot, on the
Central page response sequence; the slave's ID acknowledgement 625 us after it; the first POLL after that,
then the basic channel hopping sequence of the master. Hop selection itself is 2.6 (`apps/bt_hop_substates.py`,
checked against Part G's sample data), the FHS packet 6.5.1.4 (`apps/bt_fhs.py`).

## How long a packet is on air - Table 6.2 and 6.5.1 to 6.5.4

Total bits = 72 (access code, trailer included) + 54 (header) + payload. Read from the Core v6.0 text
(Part B: Table 6.2 p.521; 6.5.1.1 to 6.5.1.4 p.522-523; 6.5.2 HV and DV; 6.5.3 EV; 6.5.4 ACL; 7.5 for the
2/3 FEC). Payload bits are as sent, after FEC. "Bytes" include the payload header (1 byte in DM1, DH1 and
AUX1; 2 bytes in the 3- and 5-slot types) and exclude the 16-bit CRC.

| Type | Section | Information bytes | Payload bits on air | Total bits |
|---|---|---|---|---|
| ID | 6.5.1.1 | - | 68 in all, no header, no trailer | 68 |
| NULL, POLL | 6.5.1.2, 6.5.1.3 | - | none | 126 |
| FHS | 6.5.1.4 | 144 bits + CRC | 240 fixed (2/3) | 366 |
| DM1 | 6.5.4.1 | 1 to 18, CRC, 2/3 | 45 to 240 | 171 to 366 |
| DH1 | 6.5.4.2 | 1 to 28, CRC | 24 to 240 | 150 to 366 |
| AUX1 | 6.5.4.7 | 1 to 30, no CRC | 8 to 240 | 134 to 366 |
| HV1, HV2, HV3 | 6.5.2.1 to 6.5.2.3 | 10, 20, 30; no CRC | 240 fixed (1/3, 2/3, none) | 366 |
| DV | 6.5.2.4 | 80 voice + 45 to 150 data | 125 to 230 | 251 to 356 |
| EV3 | 6.5.3.1 | 1 to 30, CRC | 24 to 256 | 150 to 382 |
| EV4 | 6.5.3.2 | 1 to 120, CRC, 2/3 | 45 to 1470 | 171 to 1596 |
| EV5 | 6.5.3.3 | 1 to 180, CRC | 24 to 1456 | 150 to 1582 |
| DM3 | 6.5.4.3 | 2 to 123, CRC, 2/3 | 60 to 1500 | 186 to 1626 |
| DH3 | 6.5.4.4 | 2 to 185, CRC | 32 to 1496 | 158 to 1622 |
| DM5 | 6.5.4.5 | 2 to 226, CRC, 2/3 | 60 to 2745 | 186 to 2871 |
| DH5 | 6.5.4.6 | 2 to 341, CRC | 32 to 2744 | 158 to 2870 |

What follows:

- **The ID is the only 68-bit packet.** Every other type has the 54-bit header after the access code, and the
  trailer is added only when a header follows (6.3.4). The shortest non-ID is NULL or POLL at 126.
- **366 is not the FHS's alone.** HV1, HV2, HV3, a full DM1 (18 bytes), a full DH1 (28 bytes) and a full AUX1
  (30 bytes) are 366 too, and so are EV3 and EV5 at 28 bytes, EV4 and DM3 at 18 bytes, DH3 at 28 bytes.
  Length cannot tell an FHS from them; the header TYPE code can (FHS 0010, DM1 0011, DH1 0100, HV1 0101,
  HV2 0110, HV3 0111, AUX1 1001, Table 6.2), then the payload CRC.
- **An empty data packet is not 156.** DH1 with the payload header and CRC only is 24 payload bits, 150 in
  all. DM1 is 171: 24 bits are padded with zero tail bits to a multiple of 10 before the 2/3 FEC (7.5), 30
  bits, three codewords, 45. The DV data field's own "45 - 150" (Figure 6.10) shows the same padding.
- **Not covered here:** encrypted links - the 32-bit AES-CCM MIC is listed apart from the
  information-byte limits and it was not checked whether it moves the maxima past 366. The Enhanced Data Rate
  types are in the next section.

## NULL and POLL, and Enhanced Data Rate on the air - Table 6.2, 2.2.5, 6.5.1.2-3, 6.6.1

Read from the Core v6.0 text (page = the page the text sits on).

- **NULL 0000, POLL 0001, both 126 bits** (Table 6.2, p.521; 6.5.1.2-3, p.522). Same access code, same
  54-bit header; only TYPE differs. The Central starts in even slots (CLK1=0), the Peripheral in odd
  (CLK1=1) (2.2.5, p.471). POLL is the Central's only ("Peripherals shall not transmit the POLL packet"),
  so a 126-bit burst in an odd slot is a NULL. A POLL must be answered in the next slot, by any packet type
  ("shall respond with a packet even when [it] does not have any information to send", unless it has
  scatternet commitments); NULL "does not require acknowledgment" and may carry ARQN or FLOW. POLL is not
  part of the ARQ scheme and does not touch ARQN or SEQN. A Peripheral need not answer a Central NULL: "the
  Peripheral is not forced to respond to the NULL packet from the Central. If the Central requires a response,
  it sends a POLL packet" (4.5.1.5 "Default packet types", p.504).
- **EDR guard time 4.75 to 5.25 us**, from the end of the last GFSK header symbol to the start of the sync
  reference symbol (6.6.1 item 1, p.530). **Sync sequence 11 us = 11 DPSK symbols**: a reference symbol
  (arbitrary phase) then ten, phase steps 3pi/4, -3pi/4, 3pi/4, -3pi/4, 3pi/4, -3pi/4, -3pi/4, 3pi/4, 3pi/4,
  3pi/4 (EQ 13, Fig 6.11); symbol timing within +-1/4 us of the header's last GFSK symbol (p.530-531).
  **Trailer: two all-zero symbols** after the payload ({00,00} pi/4-DQPSK, {000,000} 8DPSK) (p.531).
- **TYPE alone cannot show EDR.** The codes are reused and selected by LMP ptt (Table 6.2): 0100 DH1/2-DH1,
  0110 HV2/2-EV3, 0111 HV3/EV3/3-EV3, 1000 DV/3-DH1, 1010 DM3/2-DH3, 1011 DH3/3-DH3, 1100 EV4/2-EV5,
  1101 EV5/3-EV5, 1110 DM5/2-DH5, 1111 DH5/3-DH5. NULL, POLL, FHS, DM1 and AUX1 are the same packet on
  every link, and stay GFSK on an EDR link.
- **EDR on-air lengths (derived, not tabulated in the spec).** Total = 72 + 54 + 5 + 11 + payload symbols
  + 2 = 144 + payload symbols, in us (1 symbol = 1 us). Payload symbols = (8B+16)/2 for pi/4-DQPSK and
  (8B+16)/3 for 8DPSK, B = information bytes including the 2-byte payload header, CRC 16, no FEC. The spec
  text gives no 8DPSK padding rule; rounding up was assumed. Byte limits from 6.5.3-6.5.4 and Table 6.8-6.9.

  | type | B | total (us) | type | B | total (us) |
  |---|---|---|---|---|---|
  | 2-DH1 | 2..56 | 160..376 | 3-DH1 | 2..85 | 155..376 |
  | 2-DH3 | 2..369 | 160..1628 | 3-DH3 | 2..554 | 155..1627 |
  | 2-DH5 | 2..681 | 160..2876 | 3-DH5 | 2..1023 | 155..2878 |
  | 2-EV3 | 1..60 | 156..392 | 3-EV3 | 1..90 | 152..390 |
  | 2-EV5 | 1..360 | 156..1592 | 3-EV5 | 1..540 | 152..1590 |

- **Length does not separate BR from EDR**: the ranges overlap (2-DH1 160..376 against DH1 150..366, DM1
  171..366, EV3 150..382). A 2-DH1 length is 152 + 4B, a multiple of 4; a DH1 length is 142 + 8B, 2 mod 4,
  so those two never coincide; DM1 (141 + 15k) can. Only a length above BR's maximum for the slot count
  (EV3 382 single slot, DH3 1622, DM5 2871) is certainly EDR. What the burst does show is the DPSK payload
  after the guard and sync.
- **Not checked:** 8DPSK padding; the AES-CCM MIC's effect on the maxima (open, see the packet-type section below); the page of the Part A 3.2.1
  modulation text; the spec's own byte limits against a second source.

## Every packet type on the air, and the length classes - Table 6.2, 6.5, 6.7, 7, 8.4.3, 8.11 (2026-10-17)

Asked by bluey-ox-walker against its length classes: ID 68 | NP 126 | MID (126 < len < 366) | L366 (about 366,
here taken as 364..376) | LONG (above 376, the switch is at 377). Page = the page the text sits on; "derived"
= computed here, not read. Total bits = 72 + 54 + payload as sent (FEC padding as in 7.5); EDR in us.

| type | TYPE code, link | slots | total bits | FEC | payload CRC | payload header | classes it can fall in | cite |
|---|---|---|---|---|---|---|---|---|
| ID | none | 1 | 68 | - | - | - | ID | 6.5.1.1 p.522 |
| NULL, POLL | 0000, 0001 (POLL not on CPB) | 1 | 126 | - | - | - | NP | 6.5.1.2-3 p.522 |
| FHS | 0010, SCO and ACL | 1 | 366 | 2/3 | yes | none | L366 | 6.5.1.4 p.522-523 |
| DM1 | 0011, SCO, ACL, CPB | 1 | 171..366 (steps of 15) | 2/3 | yes | 1 | MID, L366 | 6.5.4.1 p.527 |
| DH1 | 0100, ACL, CPB | 1 | 150..366 (steps of 8) | none | yes | 1 | MID, L366 | 6.5.4.2 p.527 |
| HV1, HV2, HV3 | 0101, 0110, 0111, SCO | 1 | 366 | 1/3, 2/3, none | no | none | L366 | 6.5.2.1-3 p.525 |
| DV | 1000, SCO (in place of HV1) | 1 | 251..356 (derived) | 2/3 on the data field | data field only | 1, in the data field | MID | 6.5.2.4 p.525, Fig 6.10 |
| AUX1 | 1001, ACL, test mode only | 1 | 134..366 (steps of 8) | none | no | 1 | MID, L366 | 6.5.4.7 p.528 |
| EV3 | 0111, eSCO 1 Mb/s | 1 | 150..382 (steps of 8) | none | yes | none | MID, L366, LONG | 6.5.3.1 p.526 |
| EV4 | 1100, eSCO 1 Mb/s | up to 3 | 171..1596 (derived) | 2/3 | yes | none | MID, L366, LONG | 6.5.3.2 p.526 |
| EV5 | 1101, eSCO 1 Mb/s | up to 3 | 150..1582 | none | yes | none | MID, L366, LONG | 6.5.3.3 p.526 |
| DM3, DH3 | 1010, 1011 | 3 | 186..1626, 158..1622 | 2/3, none | yes | 2 | MID, L366, LONG | 6.5.4.3-4 p.528 |
| DM5, DH5 | 1110, 1111 | 5 | 186..2871, 158..2870 | 2/3, none | yes | 2 | MID, L366, LONG | 6.5.4.5-6 p.528 |
| synchronization train packet | DM3, LT_ADDR 0 | 3 | 516 fixed (derived) | 2/3 | yes | 2 | LONG | 8.11.2 p.603, Table 8.11 |

The EDR rows are in the section above (all ten types run MID through L366; all but 2-DH1 and 3-DH1 reach
LONG). Information bytes in the spec count the payload header ("1 to 18" for DM1); Table 6.8 p.535 lists the
user bytes without it (DM1 0-17, DH1 0-27, DM3 0-121, DH3 0-183, DM5 0-224, DH5 0-339).

- **TYPE codes by link** (Table 6.2, p.521): 0 NULL; 1 POLL; 2 FHS (SCO, ACL); 3 DM1 (SCO, ACL, CPB);
  4 DH1 / 2-DH1; 5 HV1; 6 HV2 / 2-EV3; 7 HV3 / EV3 / 3-EV3; 8 DV (SCO) / 3-DH1 (ACL ptt=1, CPB EDR);
  9 AUX1; 10 DM3 / 2-DH3; 11 DH3 / 3-DH3; 12 EV4 / 2-EV5; 13 EV5 / 3-EV5; 14 DM5 / 2-DH5; 15 DH5 / 3-DH5.
  The header TYPE alone is ambiguous between BR and EDR (4, 8, 10, 11, 14, 15), between SCO, eSCO and EDR
  eSCO (6, 7, 12, 13) and between DV and 3-DH1 (8): the link type (LMP ptt, the eSCO setup) or the DPSK part
  after the header decides. Slot count is fixed by the code (segments 1, 3, 5 slots, p.519-520).
- **CRC.** Present in FHS, DM1, DH1, DM3-DH5, all EDR ACL and eSCO types, EV3-EV5, and in DV for the data field
  only (6.5.2, p.524-525); absent in ID, NULL, POLL, HV1-3 and AUX1. In the Connection state the CRC is
  initialised with the Central's UAP (7.1 p.538; eSCO 6.6.1 item 4 p.531); in a page response with the paged
  device's, in an inquiry response (FHS, EIR) with the DCI (7.1 p.538, 6.4.6 p.519). A set of "CRC types" that
  names only 2, 3, 4, 10, 11, 14, 15 is the BR ACL types; EV3/EV4/EV5 (7, 12, 13), 2-EV3 (6), DV (8) and the
  EDR types also carry a CRC, but only a known link type says which packet a code is.
- **A full EV3 is 382 bits in one slot** (30 bytes + CRC, no payload header). EV3 at 29 bytes is 374, at 28
  bytes 366. So LONG is not "multi-slot": it holds EV3 at 30 bytes and 2-EV3, 3-EV3 above 376 (2-EV3 from
  B=57, 3-EV3 from B=86; derived). MID and L366 are not "one slot" either: DM3, DH3, DM5, DH5, EV4, EV5 and the
  EDR multi-slot types start at 158 to 186 bits, and their slot extent follows the TYPE, not the burst
  (p.519; Fig 2.14 p.484: the next packet's slot follows the type's slots).
- **EDR and 366.** All one-slot EDR types end at 376 (L366 band). The 3-x types reach exactly 366 at B=81 (222
  symbols, derived, rounding up assumed); the 2-x lengths are 152 + 4B (multiples of 4), never 366.
- **Other bursts** (BR/EDR; none beyond Table 6.2 and the ID was found):
  - Synchronization train packet: "a basic rate ACL packet with type DM3 and LT_ADDR of zero", body 28 bytes,
    2-byte payload header, HEC and CRC initialised with the Central's UAP, **no whitening, no encryption**
    (8.11.2 p.603; 7, p.537 forbids whitening them); channels 0, 24, 78 only (2.6.4.8 p.494); the scanner's
    correlator is matched to the Central's channel access code (8.11.1 p.602). Total 126 + 390 = 516 bits
    (derived). Receivers accept 28 to 121 bytes (p.604).
  - Extended inquiry response packet: DM1, DM3, DM5, DH1, DH3 or DH5, LT_ADDR 0, body at most 240 bytes, sent
    on the same frequency 1250 us after the start of the FHS, HEC and CRC initialised with the DCI, whitening
    as for the FHS (8.4.3 p.569).
  - CPB logical transport (Table 6.2 CPB columns): 1 Mb/s NULL, DM1, DH1, DM3, DH3, DM5, DH5; EDR NULL, DM1,
    2-DH1, 3-DH1, 2-DH3, 3-DH3, 2-DH5, 3-DH5. No POLL (6.5.1.3), FHS, AUX1, HV, EV or DV.
  - Role switch: ID and FHS (6.5.1.1 p.522). AUX1: test mode only, discarded otherwise (6.5.4.7).
- **Encryption.** With E0 "the entire payload shall be encrypted" (p.537, Fig 7.2: CRC, then encryption), so
  a passive receiver cannot check a CRC without the key (derived). With AES-CCM only the payload body and MIC
  are encrypted, the payload header and CRC are not (p.537), so the CRC is checkable. **Not found:** whether the
  32-bit MIC counts inside the information-byte limits (6.5.4 lists it apart; Table 6.8 footnote C.1). If it
  is on top, an encrypted DM1 could reach 426 and a DH1 398 bits (derived), LONG either way.

## Slots, channels, who answers, and whose clock - 1.3, 2.2.5, 2.4.3, 2.6.1, 4.5.1.5, 8.6 (2026-10-17)

Asked while answering bluey-ox-walker's questions on pairing a master packet with its answer (a slot-parity
filter, same-channel pairs, rows with no anchor). Page = the page the text sits on in the v6.0 PDF; Part B
unless marked Part A.

- **Slot parity.** "The Central transmission shall always start at even numbered time slots (CLK1=0) and the
  Peripheral transmission shall always start at odd numbered time slots (CLK1=1)"; with multi-slot packets
  "Central transmission may continue in odd numbered slots and Peripheral transmission may continue in even
  numbered slots" (2.2.5, p.471-472). After an
  L-slot master packet (L = 1, 3, 5) the answer starts at s + L, odd: parity holds with or without AFH.
- **Who answers.** "Only the Peripheral that is addressed by one of its LT_ADDRs (primary or secondary) may
  return a packet in the next Peripheral-to-Central slot. If no valid packet header is received, the
  Peripheral may only respond in its reserved SCO or eSCO Peripheral-to-Central slot. In the case of a
  broadcast message, no Peripheral shall return a packet" (8.6.1, p.571). On SCO the Peripheral "is allowed to
  transmit in the slot reserved for its SCO logical transport unless a packet with the correct access code was
  received in the preceding slot and the LT_ADDR did not address this Peripheral" (8.6.2, p.573). A NULL need
  not be answered (4.5.1.5, p.504; see the NULL and POLL section). So a true Peripheral burst can have no
  Central packet before it (SCO/eSCO reserved slots), and a Central packet can have no answer (NULL,
  broadcast).
- **The frequency of a multi-slot packet and of the slot after it.** "The RF frequency shall remain fixed for
  the duration of the packet. The RF frequency for the packet shall be derived from the Bluetooth clock value in
  the first slot of the packet. The RF frequency in the first slot after a multi-slot packet shall use the
  frequency as determined by the Bluetooth clock value for that slot" (2.6.1, p.484, Fig 2.14). Without AFH
  the answer to an L-slot packet is on f(k+L), not on the Central's channel.
- **The same channel mechanism is AFH's.** With the adapted sequence "the Peripheral responds with its packet
  on the same RF channel that was used by the Central to address that Peripheral ... This is called the same
  channel mechanism of AFH ... shall be used whenever the adapted channel hopping sequence is selected" (2.6.1,
  p.485, Fig 2.15); with adaptation disabled "the same channel mechanism shall not be used" (8.6.7, p.580). A
  rule that pairs a Central packet with an answer on the same channel is therefore an AFH rule.
- **Page timing.** "In a single TX slot interval, the paging device shall transmit on two different hop
  frequencies ... The first transmission starts where CLK0 = 0 and the second transmission starts where
  CLK0 = 1" (2.4.3, p.476): page IDs sit at 0 and +312.5 us, half a slot apart. Hop rate: at most 1600 hops/s in
  the Connection state, 3200 in Inquiry and Page (2.1, p.469).
- **Whose clock a LAP's bursts run on.** "The channel access code shall be derived from the LAP of the Central's
  BD_ADDR" (1.3, p.467), and the device access code of a page, including the Central's FHS in Central Page
  Response, is the paged device's (1.3; see the page section above). So one piconet = one Central's LAP and
  clock; a role switch moves the link to the new Central's LAP (a different LAP, derived); another piconet of a
  scatternet has its own LAP. The case that puts **two clocks under one LAP** is paging: a device X that is a
  Central of its own piconet, and a pager paging X, whose IDs and FHS carry X's LAP on the pager's timing
  (derived from 1.3).
- **Timing tolerances.** "The average timing of packet transmission shall not drift faster than 20 ppm relative
  to the ideal slot timing of 625 us. The instantaneous timing shall not deviate more than 1 us from the average
  timing" (2.2.5, p.472; Hold and Sniff use the LMP parameters instead). 20 ppm is about 0.64 slot in 20 s
  (derived).
- **Carrier tolerances** (Part A, Radio). "The transmitted initial center frequency shall be within +-75 kHz
  from Fc", drift not included (3.1.3, p.436); maximum drift +-25 kHz in a one-slot packet, +-40 kHz in a three-
  or five-slot packet, maximum drift rate 400 Hz/us "anywhere in a packet" (p.436); for EDR the initial
  accuracy is +-75 kHz and "the maximum excursion from Fc (frequency offset plus drift) shall not exceed +-75 kHz"
  (3.2.3, p.440). A -58 kHz carrier offset is within the BR transmitter specification.
- **What follows for a receiver that pairs packets (derived, as sent to bluey-ox-walker on 2026-10-17):** a
  master-slot parity filter is sound in the Connection state; a same-channel pair rule finds no true pairs on
  a non-AFH link (and finds chance coincidences there, about 1/79); rows with no anchor are legitimate (SCO/eSCO,
  a missed or out-of-window Central packet, or on a non-AFH link a Central channel outside the capture while the
  answer's channel is inside); a one-slot-back scan cannot recover a multi-slot Central packet (one slot before
  its answer is the packet's own last slot, where there is no access code); a per-LAP slot-grid estimate must
  leave out IDs (half-slot offsets) and FHS packets (another device's clock).

## What this settles, and what it does not

- The FHS-on-the-paged-LAP rule of `bt_synth_slotb2` and the DCI answer ("can an inquiry LAP have UAP 0x00:
  yes, always") rest on the sentences above, not on `apps/bt_fhs.py` alone.
- The +-1 us timing jitter of the `slotb2` notes is now checked: 2.2.5, p.472 (the slots section above).
  Not checked against the text: whether v6.0 has newer LE or EIR wording beyond these sentences.
- A reviewer pack (`.swarm/specs/p1/`) holds hop selection and Part G only. A reviewer who is to judge a
  packet-format claim needs the sections above added to its pack.
