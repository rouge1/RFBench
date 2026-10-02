# Bluetooth LE advertising packets, bit by bit

One of the notes in `knowledge/`. What applies everywhere, and which
file covers what, is in [CLAUDE.md](../CLAUDE.md). Why an LE
advertisement is the Bluetooth the VSG60 can send, and the radio side of
it, is in [bluetooth.md](bluetooth.md).

Everything here is from the Bluetooth Core specification v6.0,
`/data/python/bluey-ox-walker/knowledge/Core_v6.0.pdf`, whose printed page
numbers equal the PDF's. Unless a reference says otherwise it is Vol 6
Part B, the Link Layer. Nothing has been transmitted yet. The encoding
has been checked, though: the functions under
[checked against the spec's own packet](#checked-against-the-specs-own-packet)
reproduce the spec's worked example bit for bit.

## The packet on air

LE 1M, Fig 2.1 (§2.1, p. 2934), sent left to right:

| Field | Size | Whitened |
|---|---|---|
| Preamble | 1 octet (2 on LE 2M) | no |
| Access address | 4 octets | no |
| PDU | 2-258 octets | yes |
| CRC | 3 octets | yes |

- **Preamble** (§2.1.1, p. 2934): alternating bits, and its first bit
  equals the access address's first bit on air. So for advertising it is
  `01010101` in transmission order.
- **Access address** (§2.1.2, p. 2935): every legacy advertising packet
  uses `0x8E89BED6`. On air it is `01101011 01111101 10010001 01110001`.
- **CRC** (§2.1.4): 24 bits over the PDU only.

**Bit order** (§1.2, pp. 2927-2928): every field is sent least
significant bit first, and a multi-octet field least significant octet
first - so a 48-bit address goes out from its lowest octet. **The CRC is
the exception**: it is sent most significant bit first. Getting this
wrong gives a packet whose every field looks plausible and that no
receiver accepts.

**Order of operations** (§3.1, Fig 3.1, pp. 3019-3020): CRC over the
PDU, then whitening over the PDU and CRC together. A receiver checks the
access address first and drops anything that does not match, then the
CRC.

## CRC

§3.1.1 (pp. 3019-3020):

- Polynomial x²⁴ + x¹⁰ + x⁹ + x⁶ + x⁴ + x³ + x + 1.
- Preset to `0x555555` for every advertising packet.
- The PDU bits are fed in transmission order, LSB first.
- The result is sent from position 23 down to position 0.

In the usual left-shifting form the feedback mask is `0x00065B` and the
register goes out from its top bit. That form is `crc24` below, and it
reproduces the spec's example.

## Whitening

§3.2 (pp. 3020-3021):

- A 7-bit LFSR, x⁷ + x⁴ + 1.
- Seeded for every packet: position 0 = 1, and positions 1-6 hold the
  channel **index** (not the frequency), most significant bit in
  position 1.
- Each data bit is XORed with position 6. Position 6 then feeds back into
  position 0 and is XORed into position 4.
- The same circuit de-whitens.

The seed is the channel index, so **one packet sent on 37 and 38 is two
different bit streams**. The dual-channel plan in
[bluetooth.md](bluetooth.md#two-advertising-channels-at-once-and-the-lo-out-of-the-way)
has to encode it twice.

First 64 bits of each advertising channel's sequence, from Vol 6 Part C
§4.1 (pp. 3244-3245), and reproduced by `whitening` below:

| Index | First 64 whitening bits, transmission order |
|---|---|
| 37 | `10110001 01001011 11101010 10000101 10111100 11100101 01100110 00001101` |
| 38 | `01101011 10100011 00100010 00000100 10011010 01111011 10000111 11110001` |
| 39 | `11111000 11101100 01010010 11111010 10100001 01101111 00111001 01011001` |

## The advertising PDU

§2.3 (pp. 2940-2941): a 16-bit header, then 1-255 octets of payload.

| Bits | Field | |
|---|---|---|
| 0-3 | PDU Type | table below |
| 4 | RFU | 0 |
| 5 | ChSel | channel-selection #2 support; RFU on `ADV_NONCONN_IND` |
| 6 | TxAdd | 0 = public address, 1 = random |
| 7 | RxAdd | only in PDUs addressed to someone |
| 8-15 | Length | payload octets |

The legacy PDUs on the primary channels (Table 2.3, p. 2941):

| Type | PDU | Response allowed (Table 4.2, pp. 3037-3038) |
|---|---|---|
| 0000 | `ADV_IND` | scan request, connect |
| 0001 | `ADV_DIRECT_IND` | connect, from one named device |
| 0010 | `ADV_NONCONN_IND` | **none** |
| 0011 | `SCAN_REQ` | - |
| 0100 | `SCAN_RSP` | - |
| 0101 | `CONNECT_IND` | - |
| 0110 | `ADV_SCAN_IND` | scan request |

**`ADV_NONCONN_IND` is the one for a transmit-only radio.** It is the
only advertisement that invites no reply (§2.3.1.3, p. 2944; Vol 3 Part C
§9.1.1, p. 1379, the Broadcaster role). With `ADV_IND` or
`ADV_SCAN_IND`, a phone will send a request we cannot hear or answer.

Its payload is `AdvA` (6 octets) and then `AdvData` (0-31 octets), so
Length runs from 6 to 37. The 31 is from the HCI command that sets the
data (Vol 4 Part E §7.8.7, p. 2497), and the 6-37 is Vol 1 Part A
§3.3.2.2.2 (p. 270). Longer data needs extended advertising, which is a
different and larger job.

## The address

Addresses are 48 bits (§1.3, p. 2928). A bench beacon has no public
address of its own - those are IEEE-assigned - so it uses a **random
static address**, and sets TxAdd = 1 (§1.3.2.1, p. 2929):

- The top two bits are `11`, which is what marks a random address as
  static (Table 1.2).
- The other 46 bits are random, with at least one 0 and at least one 1.
- It may change at power-up, and not otherwise.

The spec's own example uses `C1:A2:A3:A4:A5:A6`.

## Advertising data

Vol 3 Part C §11 (pp. 1432-1433) defines only the container: a run of
structures, each `[Length][AD type][Length-1 octets of data]`, with
Length never 0. What each type means is in the Core Specification
Supplement, Part A. The numbers themselves are in Assigned Numbers.
**Neither document is in the PDF.** What the Core spec does name is the
four Flags bits, and that a Broadcaster must set neither discoverable
flag (Vol 3 Part C §9.1.1.2, p. 1379).

The common types, from the Supplement and Assigned Numbers - **not
checked against those documents here**:

| Type | Meaning |
|---|---|
| `0x01` | Flags: bit 0 LE Limited Discoverable, bit 1 LE General Discoverable, bit 2 BR/EDR Not Supported |
| `0x08` / `0x09` | Shortened / Complete Local Name |
| `0x0A` | Tx Power Level, signed dBm (confirmed as signed by the spec's example, `0xD6` = −42 dBm, Vol 6 Part C §4.2.2) |
| `0xFF` | Manufacturer Specific Data: a 16-bit company identifier, least significant octet first, then anything |

A company identifier is assigned by the SIG (Vol 6 Part B §2.4.2.13,
p. 2974). `0xFFFF` is the value Assigned Numbers sets aside for testing,
and is the one for the bench. That too is from Assigned Numbers, not
from this PDF.

So a bench beacon's `AdvData` might be:

- `02 01 04` - Flags, BR/EDR Not Supported and neither discoverable bit
  set, as a Broadcaster should.
- `0A 09` followed by `RFbench01` - Complete Local Name, which is what a
  phone's scanner shows.

That is 3 + 11 = 14 octets, well inside the 31.

## Timing

- **Advertising events** (§4.4.2.2.1, p. 3039): every
  advInterval + advDelay. advInterval is a multiple of 0.625 ms from
  20 ms to 10,485 s. advDelay is re-drawn from 0-10 ms for every event,
  so advertisers that start together drift apart.
- **Within an event**: at most one packet on each advertising channel,
  in any order, which may change from event to event (§4.4.2.1,
  pp. 3038-3039). Successive packets start at most 10 ms apart
  (`ADV_NONCONN_IND`: §4.4.2.6, p. 3057).
  - For the VSG60's two channels at once, both copies go out in the same
    instant. That is allowed, since order and spacing are free.
- **Packet length**: at 1 Mb/s a bit is 1 µs. The beacon above is
  8 + 32 + (2 + 6 + 14 + 3) × 8 = 240 µs on air.
- **T_IFS** (§4.1.1, p. 3023) is 150 µs. It matters only for answering a
  request, which a non-connectable beacon never gets.

## Checked against the spec's own packet

Vol 6 Part C §4.2.1 (pp. 3245-3246) works one `ADV_NONCONN_IND` through
from fields to the bits on air:

- **Header:** type 2, TxAdd 1.
- **AdvA:** `C1A2A3A4A5A6`.
- **AdvData:** `01 02 03`.
- **Channel:** 38.
- **CRC**, in transmission order: `10110101 00101101 11010111`.
- **After whitening:**
  `00101001 00110011 01000111 10100001 10111111 10111110 11000010
  01110010 01011000 11100101 00110101 11110111 11110011 10100101`.

These functions reproduce each step exactly. They build the PDU from its
fields, compute the CRC, whiten, and produce the access address and
preamble. They also reproduce all three whitening rows above:

```python
def lsb_bits(octets):                       # §1.2: LSB first, lowest octet first
    return [(o >> i) & 1 for o in octets for i in range(8)]

def crc24(bits, init=0x555555):             # §3.1.1; returns bits in transmission order
    s = init
    for b in bits:
        fb = ((s >> 23) & 1) ^ b
        s = (s << 1) & 0xFFFFFF
        if fb:
            s ^= 0x00065B
    return [(s >> i) & 1 for i in range(23, -1, -1)]   # position 23 first

def whitening(channel_index, n):            # §3.2
    p = [1] + [(channel_index >> (5 - i)) & 1 for i in range(6)]
    out = []
    for _ in range(n):
        o = p[6]
        out.append(o)
        p = [o, p[0], p[1], p[2], p[3] ^ o, p[4], p[5]]
    return out

header = 0b0010 | (1 << 6)                  # ADV_NONCONN_IND, TxAdd = 1
pdu = [header, 9] + list((0xC1A2A3A4A5A6).to_bytes(6, 'little')) + [1, 2, 3]
bits = lsb_bits(pdu)
bits += crc24(bits)
air = [b ^ w for b, w in zip(bits, whitening(38, len(bits)))]
access = lsb_bits((0x8E89BED6).to_bytes(4, 'little'))
packet = [0, 1, 0, 1, 0, 1, 0, 1] + access + air   # then GFSK, 1 Msym/s, BT 0.5, h 0.5
```

An encoder in `apps/` should keep this example as its test, the way
`scripts/test_ism_frame.py` holds `ism_frame` to rtl_433.

## Still open

- The Supplement and Assigned Numbers themselves, to confirm the AD type
  numbers, the Flags bits and `0xFFFF`.
- The GFSK modulator: BT 0.5 and h 0.5 are in
  [bluetooth.md](bluetooth.md#bluetooth-low-energy). Whether GNU Radio's
  `gfsk_mod` or a hand-built Gaussian filter and phase accumulator should
  do it is not decided.
- Everything on air: the VSG60 sending it, and a phone or a receiver
  here decoding it.
