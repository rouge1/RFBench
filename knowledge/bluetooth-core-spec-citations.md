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
- **Not covered:** the Enhanced Data Rate types (GFSK access code and header, then a guard, a sync sequence
  and a DPSK payload, so not comparable bit counts), and encrypted links - the 32-bit AES-CCM MIC is listed
  apart from the information-byte limits and it was not checked whether it moves the maxima past 366.

## What this settles, and what it does not

- The FHS-on-the-paged-LAP rule of `bt_synth_slotb2` and the DCI answer ("can an inquiry LAP have UAP 0x00:
  yes, always") rest on the sentences above, not on `apps/bt_fhs.py` alone.
- Not checked against the text: the +-1 us timing jitter a slave may have around its nominal answer (stated
  from memory in the `slotb2` notes), and whether v6.0 has newer LE or EIR wording beyond these sentences.
- A reviewer pack (`.swarm/specs/p1/`) holds hop selection and Part G only. A reviewer who is to judge a
  packet-format claim needs the sections above added to its pack.
