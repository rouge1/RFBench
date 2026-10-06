#!/usr/bin/env python3
"""Page and inquiry exchanges with an FHS, and the traffic that follows, as samples and a sidecar.

    python scripts/bt_synth_page.py page_exch_hop20 --kind page --exchanges 200 --followup 110 --seed 7001 --out DIR
    python scripts/bt_synth_page.py inq_exch_hop20 --kind inquiry --exchanges 200 --seed 7002 --out DIR
    python scripts/bt_synth_page.py --set page [--out DIR]

    from scripts import bt_synth_page
    iq, sidecar = bt_synth_page.synthesise_page(kind='page', exchanges=10, followup=20)

For bluey-ox-walker's paging stack (``scan_pages``), which groups ID packets
by LAP inside a window and then **validates an FHS by brute-forcing CLK[27:1]
from the (slot, channel) pairs of the packets on the master's LAP that follow
it**, requiring it to equal the clock the FHS carries: a master LAP with no
such follow-up traffic is a false positive. So every page exchange here is the
whole of the specification's start-up, ending in 110 master packets (POLL,
NULL, DH1) with the slave's answer in the slot after each, hopping on the
adapted sequence of the clock the FHS carries. Nothing is transmitted. The
plan is in [knowledge/bluey-test-signals.md](../knowledge/bluey-test-signals.md).

**What the specification says, and where** (Core v6.0 Vol 2 Part B; pages are
the specification's own; every rule is also in the sidecar's ``spec_notes``).

* **Two IDs per TX slot** - §8.3.2 p. 558 and §2.4.3 p. 476: "During each TX
  slot, the Central shall sequentially transmit on two different hop
  frequencies", the first "where CLK0 = 0" and the second "where CLK0 = 1",
  312.5 us later (Figure 2.7, p. 477). An ID is 68 bits (§6.5.1.1 p. 522);
  with the paged device's DAC in a page (Table 8.3 step 1, p. 560), the GIAC
  in an inquiry (Table 8.5 step 1, p. 569).
* **The first Peripheral page response** - §8.3.3.1 p. 562 and §2.4.4 p. 477:
  the paged device's own DAC, "625 us after the beginning of the received page
  message" - after the first ID of a slot, at the start of the RX slot, after
  the second, 937.5 us into the slot (Figures 2.8 and 2.9, pp. 478-479).
* **The FHS** - §8.3.3.2 pp. 563-564 and §2.4.4 p. 477: sent "at the
  beginning of the Central-to-Peripheral slot following the slot in which the
  Peripheral responded", "an exact 1250 us delay between the first page
  message and the Central page response packet" whichever ID was heard (Figures 2.8
  and 2.9). Access code: the paged device's (Table 8.3 step 3); HEC and CRC
  initialised with the paged device's UAP (§6.4.6 p. 519); header LT_ADDR all
  zero and payload LT_ADDR the one assigned (§8.3.3.2, Table 6.3); CLK27-2 the
  Central's native clock at the start of the FHS, "CLK0 and CLK1 reset to zero at
  the start of the FHS packet transmission" (§8.3.3.2 p. 564), so the FHS starts in
  a master slot, clk % 4 == 0.
* **The second Peripheral page response**, the acknowledgement - §2.4.4 p. 478:
  "625 us after the start of the Central page response packet"; the paged
  device's DAC (Table 8.3 step 4).
* **The connection** - §8.3.3.2 p. 564 and §8.5 p. 570: "the Central shall now send
  its first traffic packet ... This first packet shall be a POLL packet",
  "within newconnectionTO number of slots after reception of the FHS packet
  acknowledgment"; the Peripheral "may respond with a NULL, DM1, or DH1". On
  the Central's channel access code and the basic or adapted hop sequence.
  **The text is silent on the slot of the POLL**; here it is the next master
  slot after the acknowledgement's, 1250 us after the FHS (the ack is in the RX
  slot between), the earliest that fits. The Peripheral answers N x 625 us
  after the start of the packet it received, N odd (§2.2.5.1 p. 473): one slot,
  on the same channel as the Central's (§2.3.1 and the same channel mechanism,
  p. 485, and ``apps/bt_hop.py`` note 4).
* **Inquiry** - §8.4.2 p. 566 and §2.5.3 p. 480 (the TX/RX timing "the same as
  for paging"), §8.4.3 p. 568 and §2.5.4 p. 480: the inquiry response is an
  FHS "625 us after the inquiry message was received" - after the start of the
  ID, as the page response is - carrying the Peripheral's own BD_ADDR and
  native clock; the HEC and CRC use the DCI (§6.4.6, p. 524); the inquirer
  "shall not acknowledge", so there is no follow-up and the scanner returns to
  Standby. The payload's LT_ADDR is all zero (Table 6.3, p. 524); the header's
  LT_ADDR is 0 as a model choice (the extracts fix it only for the page-response
  FHS, §8.3.3.2); a response with no extended inquiry data has EIR 0 (§8.4.3 p. 568).

**What the specification does not fix, and what is done.**

* **HOP SEQUENCES THE TEXT MANDATES AND THESE FILES DO NOT FOLLOW.** The text
  is not silent here; the files depart from it on purpose, because the capture
  window cannot hold it (a 40 MS/s capture on 2441 MHz is flat over +/-14.4
  MHz; the page, inquiry and basic sequences spread over all 79 MHz), and the
  receiver's author agreed, validating the clock on an adapted 31-50 map.
  Departures: (1) the **page** sequence for the IDs (§8.3.2 p. 558, Table 8.3
  step 1 p. 561, §2.6.4.2 p. 492) and the **page response** sequence for the
  paged device's responses (steps 2 and 4; §2.6.4.3 p. 492-493), and the page
  sequence for the FHS (step 3; §2.6.4.4 p. 493); (2) the **inquiry** sequence
  for the IDs and the **inquiry response** sequence for the FHS (§8.4.2 p. 566,
  Table 8.5 p. 569, §2.6.4.5-2.6.4.6 p. 493-494); (3) the **basic channel**
  hopping sequence, all 79 channels, which the Central and the Peripheral change
  to after the acknowledgement, for the first POLL and the traffic until AFH is
  set up (§8.3.3.1 p. 563, §8.3.3.2 p. 564, Table 8.3 steps 5-6 and Figures 8.3
  and 8.4, §8.5 p. 570). What the files do instead: every page, response, FHS,
  acknowledgement and inquiry burst is on a free channel in 31-50 (some on the
  centre channel 39), and every follow-up packet, the first POLL included, is on
  the ADAPTED sequence over the map 31-50 for the clock the FHS carries. Not
  exercised by the files: a receiver that places a page ID in a page train
  (position in the 32-hop sequence, train A or B), that ties the response and
  FHS channels to the page response sequence, or that insists on the basic
  sequence for the first POLL (``spec_basic_channel_first_poll`` in each page
  exchange says what the basic kernel would give; it is not used for the
  samples). Listed in the sidecar's ``conformance`` and ``spec_channels_note``.
* **The train** is 3-8 TX slots (seeded) of two IDs each on distinct channels;
  the heard ID is the first or the second of the last slot (seeded). A
  real train is 16 slots long and repeats; this is the end of one.
* **After the exchange**: a page ends in the follow-up; an inquirer would go on
  sending IDs (its next TX slot starts before a half-slot response is over)
  and a real scanner would answer none for RAND slots (§8.4.3 p. 568); here
  the inquiry exchange ends with the FHS and an idle gap follows.
* **The clocks.** The paging or inquiring device's native clock at the first
  TX slot of exchange ``e`` is ``c0``, a seeded multiple of 4, different in
  every exchange and low enough that no clock wraps CLK27 (the follow-up is
  under 2,000 ticks). ``clk`` of a burst is that device's clock, ``c0 + tick``
  (tick = 312.5 us since the first TX slot; the second ID of a slot is at
  CLK0 = 1; the task text's "bit 1" is read as the spec's CLK0), for every
  burst of a page exchange, the paged device's responses included (its own
  clock is not what the specification offsets from the Central's until the
  FHS). In an inquiry, an FHS's ``clk`` is the *scanning* device's own clock,
  CLK1-0 = 10 + (second-ID?), a transmission in an odd slot, which the FHS
  carries as CLK27-2 (the whitening is the X-input's, below); ``time_clk`` is the
  inquirer's clock on the exchange's own time base.
* **FHS whitening** is §7.2's exception (p. 541-542): the register is [X0..X4 1 1]
  with X = Xprc (EQ 6, §2.6.4.4, p. 493) in a page response and X = Xir (EQ 8,
  §2.6.4.6, p. 494) in an inquiry response; see ``bt_fhs.py`` and the sidecar's
  ``spec_notes``, ``fhs.whitening_*`` and ``exchanges[].fhs_whitening``.
* **Symbol phase** alternates per exchange, 0 and sps/2; the exchange is
  shifted as a whole, so that every timing in it is exact. A tick is
  12.5 symbols at 40 MS/s, so within an exchange the half-slot bursts sit at
  the other phase; ``symbol_phase`` per burst is ``start_sample % sps``.

**The follow-up** is 40 MS/s's pair geometry of ``scripts/bt_synth_pairs.py``,
whose bookkeeping is copied: a master packet in a master slot (clk % 4 == 0),
its slave in the next slot, clk + 2, 25,000 samples later; both on
``hop_channel(master clk)`` over the map 31-50, ``address = uap << 24 | lap``
of the master, through ``bt_synth_hop.afh_hop_fn``; idle pair-periods (0, 1,
2: 0.85, 0.10, 0.05) between them.

**Level and noise.** One floor for every file, ``NOISE_1MHZ = AMPLITUDE**2 /
100`` in 1 MHz; a burst of SNR ``s`` has amplitude ``sqrt(NOISE_1MHZ *
10**(s/10))``. A file is built in blocks of 2**22 samples, noise from
``default_rng([seed, block])`` and written block by block; the same arguments
give the same bytes.
"""
import argparse
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from apps import bt_fhs  # noqa: E402
from apps import bt_hop  # noqa: E402
from scripts import bt_synth  # noqa: E402
from scripts import bt_synth_hop as hop  # noqa: E402

#: The noise in 1 MHz, the one floor of every file: a burst of AMPLITUDE has 20 dB.
NOISE_1MHZ = bt_synth.AMPLITUDE ** 2 / 100

#: Samples a block of the file holds, so the float64 temporaries stay small.
BLOCK = 1 << 22

DEFAULT_OUT = '/media/user/4TB/sdr-synth-tmp'
MAP_31_50 = list(range(31, 51))
CENTRE_CHANNEL = 39

#: The paged device and the master, fixed by the task. The paged LAP is outside
#: the reserved 0x9E8B00-0x9E8B3F, and is not 0x654903 or 0xC52DA1.
PAGED = dict(lap=0x6B1D3C, uap=0x47)
MASTER = dict(lap=0x112233, uap=0x55, nap=0x1234, cod=0x5A020C)
AM_ADDR = 1
GIAC = bt_fhs.GIAC
RESERVED_LAPS = range(0x9E8B00, 0x9E8B40)

#: Odds of 0, 1, 2 idle pair-periods between two follow-up pairs.
IDLE_PROBS = (0.85, 0.10, 0.05)

#: Distinct scanning devices of an inquiry file; exchange e is answered by device e % 50.
SCANNER_POOL = 50

#: The master's follow-up packet types and their odds.
MASTER_TYPES = ('POLL', 'NULL', 'DH1')
SLAVE_TYPES = ('NULL', 'DM1', 'DH1')    # s8.3.3.1/8.3.3.2: 'may respond with a NULL, DM1, or DH1'
SLAVE_STREAM = 0x80000000

#: Every kind of burst, in the order the plan makes them.
KINDS_PAGE = ('id_page', 'id_response', 'fhs', 'id_ack', 'followup_master', 'followup_slave')
KINDS_INQUIRY = ('id_inquiry', 'fhs_inquiry_response')


class Raw:
    """Bits and a name: what an ID packet is to the renderer."""

    def __init__(self, bits, ptype):
        self.bits, self.ptype = bits, ptype


def amplitude(snr_db):
    """|iq| of a burst of ``snr_db`` over the noise in 1 MHz."""
    return math.sqrt(NOISE_1MHZ * 10 ** (snr_db / 10))


WHITENING_RULE = {
    'fhs': "s7.2 p.541-542: the register is [X0 X1 X2 X3 X4 1 1], X = Xprc (EQ 6, s2.6.4.4 p.493) = [CLKE*16-12 + "
           "k_offset + k_nudge + (CLKE*4-2,0 - CLKE*16-12) mod 16 + N] mod 32, N = 1; CLKE*4-2,0 read as the 4-bit "
           "number of bits 4,3,2,0, most significant first. tx_clk is the pseudo-clock (X | 0x20) << 1; "
           "implied_clk6_1 = 32 + X is not a clock.",
    'fhs_inquiry_response': "s7.2 p.541-542: the register is [X0 X1 X2 X3 X4 1 1], X = Xir (EQ 8, s2.6.4.6 p.494) = "
                            "(CLKN16-12 of the scanner at the FHS + N) mod 32, N the scanner's counter, any initial "
                            "value, +1 after each FHS it sent. tx_clk is the pseudo-clock (X | 0x20) << 1; "
                            "implied_clk6_1 = 32 + X is not a clock.",
}

SPEC_NOTES = [
    "Two ID packets per master TX slot, the first at CLK0 = 0 and the second at CLK0 = 1, 312.5 us "
    "later: s8.3.2 p.558, s2.4.3 p.476, Figure 2.7 p.477. An ID is 68 bits, access code without "
    "trailer: s6.5.1.1 p.522. Page IDs carry the paged device's DAC (Table 8.3 step 1, p.560); inquiry "
    "IDs the GIAC (Table 8.5 step 1, p.569).",
    "The first Peripheral page response is the paged device's DAC, 625 us after the beginning of the "
    "received page message and time aligned to it: s8.3.3.1 p.562, s2.4.4 p.477, Figures 2.8 and 2.9 "
    "pp.478-479. After the first ID of a slot that is the start of the RX slot, after the second 937.5 us "
    "into the slot.",
    "The FHS goes in the TX slot after the RX slot of the response, exactly 1250 us after the FIRST page "
    "message of the slot in which the response was heard, whichever ID was heard: s8.3.3.2 p.563-564, "
    "s2.4.4 p.477, Figures 2.8 and 2.9. It carries the paged device's access code, header LT_ADDR 0, "
    "payload LT_ADDR 1 (Table 6.3), HEC and CRC initialised with the paged UAP (s6.4.6 p.519), the "
    "Central's native clock with CLK1-0 = 0 at the start of the FHS (s8.3.3.2 p.564).",
    "The second Peripheral page response (the acknowledgement of the FHS) is the paged device's DAC, 625 "
    "us after the start of the FHS: s2.4.4 p.478, Table 8.3 step 4 p.560.",
    "The connection starts with a POLL from the Central (s8.3.3.2 p.564, s8.5 p.570) 'within "
    "newconnectionTO number of slots after reception of the FHS packet acknowledgment'; the Peripheral "
    "may answer NULL, DM1 or DH1. SILENT: the text does not say which slot. Chosen: the next master slot "
    "after the acknowledgement, 1250 us after the start of the FHS.",
    "The Peripheral's transmission follows N x 625 us (N odd) after the start of the packet it received "
    "(s2.2.5.1 p.473-474); here N = 1. On an adapted sequence it answers on the channel the Central used "
    "(same channel mechanism, s2.6 p.485; apps/bt_hop.py note 4).",
    "Inquiry: TX/RX timing the same as paging (s8.4.2 p.566, s2.5.3 p.480). The inquiry response is an FHS "
    "625 us after the received inquiry message (s8.4.3 p.568, s2.5.4 p.480, Figures 2.10 and 2.11): the "
    "start of the RX slot after the first ID of a slot, 937.5 us into it after the second. HEC and CRC "
    "initialised with the DCI (s6.4.6 p.519, p.524); payload LT_ADDR all zero (Table 6.3; the header LT_ADDR 0 is a model choice, the text fixes it only for the page-response FHS); EIR 0 because the "
    "responder has no extended inquiry data (s8.4.3 p.568).",
    "The inquirer does not acknowledge an inquiry response (s8.4.2 p.566), so an inquiry exchange has no "
    "follow-up traffic.",
    "NOT FOLLOWED (the text is not silent): the page, page response and Central page response hop sequences "
    "(s8.3.2 p.558, Table 8.3 p.561, s2.6.4.2-2.6.4.4 pp.492-493), the inquiry and inquiry response sequences "
    "(s8.4.2 p.566, Table 8.5 p.569, s2.6.4.5-2.6.4.6 pp.493-494) and the BASIC channel hopping sequence (79 "
    "channels) for the first POLL and the connection until AFH (s8.3.3.1 p.563, s8.3.3.2 p.564, Table 8.3 "
    "steps 5-6, Figures 8.3-8.4, s8.5 p.570). Reason: the capture window (40 MS/s on 2441 MHz holds +/-14.4 MHz, "
    "not 79 MHz) and the receiver author's agreement. Instead: free channels in 31-50 (some on 39) for every page/"
    "inquiry burst and the ADAPTED sequence over 31-50 for every follow-up packet from the first POLL on. Cannot "
    "be exercised: page-train position, response/FHS channel from the response sequences, a validator that "
    "insists on the basic sequence for the first POLL.",
    "SILENT in the text: the train length (the files end a train after 3-8 TX slots; a real one is 16 slots A or "
    "B repeated); the scanner's clock phase relative to the inquirer's beyond 625 us after the ID (CLK1-0 = 2 + "
    "1 if the second ID was heard, a transmission in an odd slot); the FHS header's FLOW, ARQN, SEQN (0 chosen) "
    "and, for an inquiry response, its header LT_ADDR (0 chosen; fixed by the text only for the page-response "
    "FHS); previously_used (0). After an FHS the inquirer would keep probing; the file does not.",
    "CLKE4-2,0 in EQ 6 is read as the 4-bit number of bits 4, 3, 2, 0 (MSB first). Other readings are "
    "possible (e.g. the bits taken least significant first, or the field read as CLKE4-2 alone) and would "
    "change Xprc. The inputs (clke_frozen, train, koffset, knudge, N) are "
    "recorded per exchange so a reader can recompute under any reading.",
    "FHS whitening, s7.2 p.541-542: ordinary packets seed the register with CLK6-1 and a 1 in position 6; "
    "'exceptions are the FHS packet sent during inquiry response or Central page response ... the X-input used "
    "in the inquiry or page response routine shall be used ... extended with two MSBs of value 1', X0 in "
    "position 0: the register is [X0 X1 X2 X3 X4 1 1], equal to CLK6-1 = 32 + X (implied_clk6_1, not a clock). "
    "Page FHS: X = Xprc, EQ 6, s2.6.4.4 p.493, from a seeded CLKE* (the Central's frozen estimate of the PAGED "
    "device's clock, not its own), train A (k_offset 24) or B (8), k_nudge 0, N = 1 (the first increment is done "
    "before the FHS); 'CLKE4-2,0' is read as the 4-bit number of bits 4, 3, 2, 0, most significant first (the "
    "text does not say; the spec's other bit lists, e.g. A8,6,4,2,0, run MSB first). Inquiry FHS: X = Xir, EQ 8, "
    "s2.6.4.6 p.494, (CLKN16-12 of the scanner at the FHS + N) mod 32, N a seeded initial value per scanning "
    "device (any, per the text), +1 for each FHS that device sent; 50 devices answer in turn.",
    "The ID's clock: the half-slot ID is clk with CLK0 set (bit 0), per s2.4.3 p.476 'the second "
    "transmission starts where CLK0 = 1'; the task text said 'bit 1', which is the slot bit.",
]

CONFORMANCE = dict(
    followed=[
        "two IDs per TX slot, 312.5 us apart, CLK0 = 0 then 1 (s8.3.2 p.558, s2.4.3 pp.476-477)",
        "access codes of every step (Table 8.3 p.561, Table 8.5 p.569): paged DAC for page steps 1-4, master "
        "channel access code from the POLL, GIAC for inquiry",
        "response 625 us after the heard ID; FHS 1250 us after the first ID of that slot; acknowledgement 625 us "
        "after the FHS (s8.3.3.1-2, s2.4.4 pp.477-478)",
        "FHS content, HEC and CRC from the paged UAP (page) or the DCI (inquiry), payload LT_ADDR, CLK27-2 with "
        "CLK1-0 = 0 (page), SP 0b10 (s6.4.6, s6.5.1.4, Table 6.3)",
        "FHS whitening from the X-input Xprc (EQ 6) / Xir (EQ 8) with two MSBs of 1 (s7.2 pp.541-542)",
        "first connection packet a POLL, answered NULL, DM1 or DH1; slave one slot after its master on the "
        "master's channel (s8.3.3.2 p.564, s2.2.5.1, same channel mechanism p.485)",
        "no acknowledgement of an inquiry response, no follow-up in the inquiry file (s8.4.2 p.566)"],
    not_followed=[
        "page, page response and Central page response hopping sequences (s8.3.2 p.558; Table 8.3 steps 1-4 p.561; "
        "s2.6.4.2-2.6.4.4 pp.492-493): free channels in 31-50 instead",
        "inquiry and inquiry response hopping sequences (s8.4.2 p.566; Table 8.5 p.569; s2.6.4.5-2.6.4.6 "
        "pp.493-494): free channels in 31-50 instead",
        "the basic channel hopping sequence (79 channels) for the first POLL and the connection until AFH is "
        "established (s8.3.3.1 p.563, s8.3.3.2 p.564, Table 8.3 steps 5-6, s8.5 p.570): the adapted sequence over "
        "31-50 is used from the first POLL",
        "the 16-slot A/B page train and its repetition (s8.3.2 pp.558-559): 3-8 TX slots",
        "the inquiry RAND back-off and the inquirer's continued probing after a response (s8.4.3 p.568)",
        "the paged device's receiver timing (listening 312.5 us after its response) and newconnectionTO "
        "(s8.3.3.1 p.562-563): not modelled, nothing to see in a transmit-only file"],
    reason="the capture window: a 40 MS/s capture centred on 2441 MHz holds +/-14.4 MHz, and the page, inquiry and "
           "basic sequences use all 79 MHz; agreed with the receiver's author, who validates the clock on the adapted "
           "map. Not exercised by these files: page-train position, response/FHS channels from the response "
           "sequences, a validator that insists on the basic sequence for the first POLL.",
    silent_in_text=[
        "the slot of the first POLL after the acknowledgement (chosen: the next master slot, 1250 us after the FHS)",
        "the FHS header's FLOW, ARQN and SEQN (0 chosen)",
        "the header LT_ADDR of an inquiry-response FHS (0 chosen; fixed only for the page-response FHS)",
        "previously_used in the FHS payload (0)",
        "the scanner's clock phase relative to the inquirer's (CLK1-0 = 2 + 1 if the second ID was heard)",
        "the reading of CLKE4-2,0 in EQ 6 (bits 4, 3, 2, 0, MSB first; the inputs are recorded to recompute under another)",
        "the first value of the page-side N beyond 'a counter starting at one' (1)"])

SPEC_CHANNELS_NOTE = ("Every page, response, FHS, acknowledgement and inquiry burst of this exchange is on a free channel in "
                      "31-50, not on the page, page response or inquiry response sequence the text mandates; %s "
                      "See conformance.not_followed.")

PAGE_CHANNELS_NOTE = ("The page, page response, inquiry and inquiry response channels in this file are NOT a "
                      "specification page train or response sequence (s2.6.4): they are free choices in 31-50, "
                      "two distinct channels per TX slot, with the centre channel 39 on at least 8 %% of IDs "
                      "and on some FHS, so that the file fits a 40 MS/s capture on 2441 MHz and exercises "
                      "the DC block.")

FOLLOWUP_NOTE = ("Hop-true follow-up: every master packet of a page exchange is on hop_channel(clk, "
                 "master uap << 24 | master lap, map 31-50) of its own slot's clock, which runs on from the "
                 "FHS's clock (clk27_2 << 2) by 2 ticks a slot; the slave's answer is one slot later "
                 "(clk + 2) on the same channel. The master's channel access code and the master UAP "
                 "initialise HEC and CRC. A validator that brute-forces CLK27-1 from (slot, channel) of the "
                 "master packets must find the FHS's own clock advanced by the slots between.")

CLOCK_NOTE = ("clk is the native clock of the paging or inquiring device on the exchange's time base at the "
              "start of the burst (c0 + tick; see exchanges[].c0), CLK0 = 1 for a second-in-slot ID; for the "
              "paged device's id_response and id_ack it is that same time base (its own clock is not "
              "constrained by the text until the FHS); for an inquiry FHS it is the scanning device's own "
              "native clock, whose bits CLK27-2 the FHS carries (its whitening is the X-input's: fhs.whitening_x; tx_clk is the pseudo-clock of that register, not a clock). time_clk "
              "is always the time base. slot_index is tick // 2 from the exchange's first TX slot, a master "
              "TX slot even, an RX slot odd.")


def draw_distinct(rng, used, low, high, multiple=1):
    """A value in [low, high) that is a multiple of ``multiple`` and not in ``used``."""
    while True:
        v = int(rng.integers(low // multiple, high // multiple)) * multiple
        if v not in used:
            used.add(v)
            return v


def plan_exchanges(kind='page', exchanges=200, followup=110, fs=40e6, center_mhz=2441.0,
                   channels=None, snr_db=20.0, idle_probs=IDLE_PROBS, start_phase=0, seed=7001):
    """The sidecar and the render jobs of a capture of ``exchanges`` page (or
    inquiry) exchanges, no samples made.

    Returns ``(sidecar, jobs, total)``: ``jobs`` is a list, one per burst in
    time order, of what ``render_blocks`` needs; ``total`` the number of samples.
    A bad argument or a channel the tuning cannot hold raises ``ValueError``.
    """
    if kind not in ('page', 'inquiry'):
        raise ValueError("kind is 'page' or 'inquiry'")
    if exchanges < 1:
        raise ValueError("a capture needs at least one exchange")
    if kind == 'page' and followup < 1:
        raise ValueError("a page exchange needs at least one follow-up packet")
    tick = fs * br.SLOT_US * 1e-6 / 2
    sps = int(round(fs / br.SYMBOL_RATE))
    if abs(tick - round(tick)) > 1e-6:
        raise ValueError("%g S/s does not divide a 312.5 us half slot" % fs)
    tick = int(round(tick))
    slot = 2 * tick
    if start_phase not in (0, sps // 2):
        raise ValueError("start_phase is 0 or %d, half a symbol" % (sps // 2))
    probs = [float(p) for p in idle_probs]
    if len(probs) != 3 or min(probs) < 0 or abs(sum(probs) - 1) > 1e-9:
        raise ValueError("idle_probs is three odds, for 0, 1 and 2 idle pair-periods, summing to 1")
    channels = hop.map_channels(MAP_31_50 if channels is None else channels)
    for channel in channels:
        if hop.outside_window(channel, fs, center_mhz):
            raise ValueError("channel %d, %g MHz, has its band outside +/-%g MHz (%g x %g MS/s) of the "
                             "%g MHz centre" % (channel, hop.channel_mhz(channel),
                                                hop.FLAT_FRACTION * fs / 1e6, hop.FLAT_FRACTION,
                                                fs / 1e6, center_mhz))
    centre_channel = int(round(center_mhz - 2402))
    hop_fn = hop.afh_hop_fn([(0, channels)], MASTER['lap'], MASTER['uap'])
    others = [c for c in channels if c != centre_channel]
    rng = np.random.default_rng([seed, 1 << 29])
    used_clk, used_lap = set(), {PAGED['lap'], MASTER['lap'], GIAC}
    amp = amplitude(snr_db)

    events = []          # time order across the file; each a dict, ``abs_tick`` since the exchange's origin
    exch = []
    first = int(bt_synth.LEAD_SLOTS * slot)
    cursor = first       # the earliest sample the next exchange may start at
    scanners = []
    slot_counter = 0     # TX slots so far, for the deterministic centre-channel IDs
    for e in range(exchanges):
        phase = start_phase if e % 2 == 0 else (start_phase + sps // 2) % sps
        c0 = draw_distinct(rng, used_clk, 1 << 21, (1 << 28) - (1 << 14), 4)
        n_slots = int(rng.integers(3, 9))
        heard = int(rng.integers(0, 2))          # the heard ID: first or second of the last slot
        gap_ms = float(rng.uniform(5.0, 40.0)) if e else 0.0
        origin = cursor + (int(round(gap_ms * 1e-3 * fs)) if e else 0)
        origin += (phase - origin) % sps          # the whole exchange at this symbol phase
        evs = []
        id_lap = PAGED['lap'] if kind == 'page' else GIAC
        id_kind = 'id_page' if kind == 'page' else 'id_inquiry'
        page_channels = []
        for s in range(n_slots):
            if slot_counter % 5 == 0 and centre_channel in channels:   # 10 % of IDs on channel 39
                other = int(rng.choice(others))
                pair = [centre_channel, other] if rng.integers(0, 2) else [other, centre_channel]
            else:
                pair = [int(c) for c in rng.choice(channels, size=2, replace=False)]
            slot_counter += 1
            for j in (0, 1):
                page_channels.append(pair[j])
                evs.append(dict(tick=4 * s + j, kind=id_kind, role='master' if kind == 'page' else 'inquirer',
                                channel=pair[j], packet=Raw(bt_fhs.id_bits(id_lap), 'ID'), lap=id_lap,
                                uap_for_hec=None, ptype='ID', time_clk=c0 + 4 * s + j,
                                clk=c0 + 4 * s + j))
        last = n_slots - 1
        heard_burst_tick = 4 * last + heard
        resp_tick = heard_burst_tick + 2                        # 625 us after the start of the heard ID
        fhs_info = None
        n_follow = 0
        conn_clk = None

        def pick(chans):
            return int(rng.choice(chans))

        if kind == 'page':
            resp_channel = pick(others if e % 4 else channels)
            evs.append(dict(tick=resp_tick, kind='id_response', role='slave', channel=resp_channel,
                            packet=Raw(bt_fhs.id_bits(PAGED['lap']), 'ID'), lap=PAGED['lap'],
                            uap_for_hec=None, ptype='ID', time_clk=c0 + resp_tick, clk=c0 + resp_tick))
            fhs_tick = 4 * last + 4                             # 1250 us after the first ID of the slot
            fhs_clk = c0 + fhs_tick
            assert fhs_clk % 4 == 0
            fhs_channel = centre_channel if (e % 3 == 0 and centre_channel in channels) else pick(others)
            sr = int(rng.integers(0, 3))
            # the Central's frozen estimate of the paged device's clock (CLKE*, not its own native clock),
            # the train and the nudge frozen with it; N = 1 for the first FHS (EQ 6, s2.6.4.4 p.493)
            clke = int(rng.integers(0, 1 << 28))
            train, koffset, knudge, n_ctr = ('A', 24, 0, 1) if rng.integers(0, 2) else ('B', 8, 0, 1)
            x = bt_fhs.xprc(clke, koffset, knudge, n_ctr)
            wx = dict(clke_frozen=clke, train=train, koffset=koffset, knudge=knudge, N=n_ctr, xprc=x)
            f = bt_fhs.page_response(PAGED['lap'], PAGED['uap'], MASTER['lap'], MASTER['uap'], MASTER['nap'],
                                     MASTER['cod'], AM_ADDR, fhs_clk, x, sr=sr)
            evs.append(dict(tick=fhs_tick, kind='fhs', role='master', channel=fhs_channel, packet=f,
                            lap=PAGED['lap'], uap_for_hec=PAGED['uap'], ptype='FHS',
                            time_clk=fhs_clk, clk=fhs_clk, fhs=f))
            ack_tick = fhs_tick + 2                             # 625 us after the start of the FHS
            evs.append(dict(tick=ack_tick, kind='id_ack', role='slave', channel=pick(others if e % 5 else channels),
                            packet=Raw(bt_fhs.id_bits(PAGED['lap']), 'ID'), lap=PAGED['lap'],
                            uap_for_hec=None, ptype='ID', time_clk=c0 + ack_tick, clk=c0 + ack_tick))
            poll_tick = fhs_tick + 4
            conn_clk = c0 + poll_tick
            gaps = rng.choice(3, size=followup, p=probs)
            gaps[0] = 0
            types = rng.choice(len(MASTER_TYPES), size=followup)
            types[0] = 0                                        # the first packet is a POLL
            answers = rng.choice(len(SLAVE_TYPES), size=followup)
            period = 0                                          # pair-periods since the POLL
            for k in range(followup):
                if k:
                    period += int(gaps[k]) + 1
                t_m = poll_tick + 4 * period
                clk_m = c0 + t_m
                channel = int(hop_fn(clk_m))
                for role, kk, clk, ptype, stream, off in (
                        ('master', 'followup_master', clk_m, MASTER_TYPES[types[k]], (e << 12) | k, 0),
                        ('slave', 'followup_slave', clk_m + 2, SLAVE_TYPES[answers[k]],
                         SLAVE_STREAM | (e << 12) | k, 2)):
                    body = br.seq_body(stream, br.PACKET_TYPES[ptype][4]) if br.PACKET_TYPES[ptype][4] else b''
                    p = br.Packet(MASTER['lap'], MASTER['uap'], clk, ptype, body, lt_addr=AM_ADDR, flow=1,
                                  arqn=1, seqn=k & 1)
                    evs.append(dict(tick=t_m + off, kind=kk, role=role, channel=channel, packet=p,
                                    lap=MASTER['lap'], uap_for_hec=MASTER['uap'], ptype=ptype,
                                    time_clk=clk, clk=clk))
            n_follow = followup
            fhs_info = dict(tick=fhs_tick, clk=fhs_clk, **wx)
            identities = dict(paged=dict(PAGED), master=dict(MASTER), am_addr=AM_ADDR)
        else:
            # a pool of up to 50 scanning devices; one that answers again has its counter N one higher
            # (EQ 8, s2.6.4.6 p.494: increased after each FHS it has transmitted)
            if e < SCANNER_POOL:
                while True:
                    s_lap = int(rng.integers(1, 1 << 24))
                    if s_lap not in RESERVED_LAPS and s_lap not in used_lap:
                        used_lap.add(s_lap)
                        break
                scanners.append(dict(lap=s_lap, uap=int(rng.integers(0, 256)), nap=int(rng.integers(0, 1 << 16)),
                                     cod=int(rng.choice([0x5A020C, 0x200404, 0x240408, 0x1F00, 0x7A020C])),
                                     n0=int(rng.integers(0, 1 << 16)), sent=0))
            sc = scanners[e % SCANNER_POOL]
            s_lap, s_uap, s_nap, s_cod = sc['lap'], sc['uap'], sc['nap'], sc['cod']
            n_ctr = sc['n0'] + sc['sent']
            sc['sent'] += 1
            # the scanner's own clock: CLK1-0 = 10 in an odd slot, plus one if it answered the second ID
            s_clk = draw_distinct(rng, used_clk, 1 << 21, (1 << 28) - (1 << 14), 4) + 2 + heard
            x = bt_fhs.xir(s_clk, n_ctr)
            wx = dict(scanner_index=e % SCANNER_POOL, N=n_ctr, clkn16_12=(s_clk >> 12) & 0x1F, xir=x)
            fhs_tick = resp_tick
            f = bt_fhs.inquiry_response(s_lap, s_uap, s_nap, s_cod, s_clk, x, access_lap=GIAC,
                                        sr=int(rng.integers(0, 3)))
            ch = centre_channel if (e % 3 == 0 and centre_channel in channels) else pick(others)
            evs.append(dict(tick=fhs_tick, kind='fhs_inquiry_response', role='scanner', channel=ch, packet=f,
                            lap=GIAC, uap_for_hec=bt_fhs.DCI, ptype='FHS', time_clk=c0 + fhs_tick,
                            clk=s_clk, fhs=f))
            fhs_info = dict(tick=fhs_tick, clk=s_clk, **wx)
            identities = dict(inquirer=None, scanner=dict(lap=s_lap, uap=s_uap, nap=s_nap, cod=s_cod, clk=s_clk))
        # absolute samples
        end = 0
        for ev in evs:
            ev['start'] = origin + ev['tick'] * tick
            ev['exchange'] = e
            ev['phase_carrier'] = None
            end = max(end, ev['start'] + len(ev['packet'].bits) * sps)
        cursor = end
        exch.append(dict(exchange=e, kind=kind, c0=c0, start_sample=origin, symbol_phase=origin % sps,
                         gap_before_ms=gap_ms, n_id_slots=n_slots, heard_id=heard,
                         heard_slot=last, page_channels=page_channels,
                         spec_channels_note=SPEC_CHANNELS_NOTE % ('The follow-up (first POLL included) is on the adapted sequence over 31-50, not the basic sequence.' if kind == 'page' else 'There is no follow-up.'),
                         spec_basic_channel_first_poll=(bt_hop.hop_channel(conn_clk, MASTER['uap'] << 24 | MASTER['lap'], None) if kind == 'page' else None),
                         first_burst=len(events), last_burst=len(events) + len(evs) - 1,
                         fhs_burst=len(events) + next(i for i, ev in enumerate(evs) if ev['kind'].startswith('fhs')),
                         fhs_clk27_2=(fhs_info['clk'] >> 2), fhs_tick=fhs_info['tick'],
                         fhs_whitening={k: v for k, v in fhs_info.items() if k not in ('tick', 'clk')},
                         master_clk_at_connection=conn_clk, n_followup=n_follow,
                         identities=identities))
        events.extend(evs)

    total = cursor + slot
    n = len(events)
    draw = np.random.default_rng([seed, (1 << 29) + 1])
    frac = draw.uniform(0, 1, size=n)
    carrier_phase = draw.uniform(0, 2 * np.pi, size=n)
    jobs, entries = [], []
    for i, ev in enumerate(events):
        jobs.append(dict(packet=ev['packet'], start=ev['start'], frac=float(frac[i]),
                         phase=float(carrier_phase[i]), amp=amp, channel=ev['channel']))
        entry = {'start_sample': ev['start'], 'timing_frac': float(frac[i]), 'symbol_phase': ev['start'] % sps}
        entry.update(kind=ev['kind'], lap=ev['lap'], uap_for_hec=ev['uap_for_hec'], exchange=ev['exchange'],
                     channel=ev['channel'], channel_mhz=hop.channel_mhz(ev['channel']), snr_db=snr_db,
                     role=ev['role'], ptype=ev['ptype'], clk=ev['clk'], time_clk=ev['time_clk'],
                     tick=ev['tick'], slot_index=ev['tick'] // 2)
        p = ev['packet']
        if ev['ptype'] == 'FHS':
            side = p.sidecar()
            for key in ('header18', 'payload_full_hex', 'payload_valid', 'lt_addr', 'flow', 'arqn', 'seqn'):
                entry[key] = side[key]
            entry['fhs'] = dict(lap=p.lap, uap=p.uap, nap=p.nap, class_of_device=p.cod, am_addr=p.am_addr,
                                lt_addr_header=p.lt_addr, clk27_2=p.clk27_2, sr=p.sr, sp=p.sp, eir=p.eir,
                                header_uap=p.header_uap, tx_clk=p.tx_clk, access_lap=p.access_lap,
                                whitening_x=p.whitening_x, whitening_register=(bt_fhs.whitening_register(p.whitening_x) if p.whitening_x is not None else None),
                                reserved=p.reserved, previously_used=p.previously_used,
                                implied_clk6_1=(p.tx_clk >> 1) & 0x3F, whitening_rule=WHITENING_RULE[ev['kind']])
        elif ev['ptype'] != 'ID':
            side = p.sidecar()
            del side['air_bits'], side['clk'], side['ptype']
            entry.update(side)
        entries.append(entry)
    counts = {}
    for en in entries:
        counts[en['kind']] = counts.get(en['kind'], 0) + 1
    centre_counts = {}
    for en in entries:
        if en['channel'] == centre_channel:
            centre_counts[en['kind']] = centre_counts.get(en['kind'], 0) + 1

    sidecar = {
        'generator': 'SDR scripts/bt_synth_page.py',
        'generator_commit': bt_synth.commit(),
        'lap': None,
        'uap': None,
        'clk': None,
        'ptype': None,
        'clk_convention': CLOCK_NOTE,
        'hopping': True,
        'hop_channels': sorted({en['channel'] for en in entries}),
        'channel_mhz': None,
        'bt_channel': None,
        'afh_map': channels,
        'afh_instant': 0,
        'afh_map_count': 1,
        'afh_maps': [{'instant': 0, 'first_burst': 0, 'channels': channels}],
        'afh_instant_meaning': hop.INSTANT_MEANING,
        'address_for_hop': MASTER['uap'] << 24 | MASTER['lap'],
        'hop_kernel': hop.HOP_KERNEL,
        'clock_lock_note': hop.CLOCK_LOCK_NOTE,
        'sample_rate': fs,
        'center_mhz': center_mhz,
        'cfo_hz': 0.0,
        'timing_frac': None,
        'start_offset': 0,
        'symbol_phase': None,
        'snr_db': None,
        'master_snr_db': snr_db,
        'per_burst_keys': ['start_sample', 'timing_frac', 'symbol_phase', 'snr_db', 'clk', 'time_clk', 'ptype',
                           'channel', 'channel_mhz', 'kind', 'lap', 'uap_for_hec', 'exchange', 'role', 'tick',
                           'slot_index'],
        'snr_bw_hz': 1e6,
        'noise_1mhz': NOISE_1MHZ,
        'modulation': {'scheme': 'GFSK', 'bt': br.GFSK_BT, 'h': br.GFSK_H, 'symbol_rate': br.SYMBOL_RATE},
        'slot_samples': slot,
        'tick_samples': tick,
        'symbol_phases': [0, sps // 2],
        'symbol_phase_note': 'exchange e is shifted as a whole to symbol phase %d (even e) or %d (odd e) of %d; '
                             'a tick is %g symbols, so bursts at an odd tick sit at the other phase'
                             % (start_phase, (start_phase + sps // 2) % sps, sps, tick / sps),
        'start_sample_meaning': "the first sample of the access code's preamble, before timing_frac is added",
        'air_bits_omitted': True,
        'file_kind': kind,
        'n_exchanges': exchanges,
        'followup_per_exchange': followup if kind == 'page' else 0,
        'idle_probs': probs,
        'exchanges': exch,
        'identities': (dict(paged=dict(PAGED), master=dict(MASTER), am_addr=AM_ADDR, giac=GIAC)
                       if kind == 'page' else
                       dict(inquirer=None, giac=GIAC, scanner='per exchange: exchanges[].identities.scanner')),
        'spec_notes': SPEC_NOTES,
        'page_channels_note': PAGE_CHANNELS_NOTE,
        'conformance': CONFORMANCE,
        'followup_note': (FOLLOWUP_NOTE if kind == 'page' else
                          'None: an inquiry response is not acknowledged and the scanner returns to Standby '
                          '(s8.4.2 p.566); this file has no follow-up traffic.'),
        'centre_channel': centre_channel,
        'n_bursts_on_centre_channel': sum(centre_counts.values()),
        'bursts_on_centre_channel_by_kind': centre_counts,
        'kind_counts': counts,
        'n_bursts': len(entries),
        'bursts': entries,
    }
    return sidecar, jobs, total


def render_burst(job, fs, center_mhz):
    """One burst's samples at its absolute sample numbers: ``(lo, samples)``.

    The carrier is one oscillator at the absolute sample index, as in
    ``bt_synth_hop.py``: the whole cycles are dropped before the cast to
    radians, which keeps the phase exact however far into the file the burst is.
    """
    burst, lead = br.gfsk(job['packet'].bits, fs, h=br.GFSK_H, delay=job['frac'])
    burst = burst * np.exp(1j * job['phase'])
    lo = job['start'] - lead
    n = np.arange(lo, lo + len(burst))
    cycles = (hop.channel_mhz(job['channel']) - center_mhz) * 1e6 / fs * n
    burst = burst * np.exp(2j * np.pi * (cycles - np.floor(cycles)))
    return lo, (job['amp'] * burst).astype(np.complex64)


def burst_span(job, fs):
    """The first sample and one past the last that ``render_burst`` makes,
    worked out without making it."""
    sps = fs / br.SYMBOL_RATE
    lead = int(np.ceil(2.0e-6 * fs)) + 1             # gfsk's own, ramp_us = 2
    lo = job['start'] - lead
    return lo, lo + int(np.ceil(len(job['packet'].bits) * sps)) + 2 * lead


def render_blocks(sidecar, jobs, total, seed, block=BLOCK, noise=True):
    """Yield the file in blocks of ``block`` samples as ``complex64``.

    Each block starts as noise from ``default_rng([seed, block_index])``, the
    per-component sigma of ``bt_synth.synthesise`` for the one floor
    ``NOISE_1MHZ``, and then the bursts that reach it are added. A burst that
    crosses a boundary is rendered for each block it touches, from the same
    job, so where the boundaries fall changes nothing. ``noise=False`` leaves
    the bursts alone.
    """
    fs, center_mhz = sidecar['sample_rate'], sidecar['center_mhz']
    sigma = math.sqrt(NOISE_1MHZ * (fs / 1e6) / 2)
    spans = np.array([burst_span(job, fs) for job in jobs], dtype=np.int64).reshape(-1, 2)
    for index, b0 in enumerate(range(0, total, block)):
        b1 = min(total, b0 + block)
        if noise:
            rng = np.random.default_rng([seed, index])
            out = (rng.normal(0, sigma, b1 - b0) + 1j * rng.normal(0, sigma, b1 - b0)).astype(np.complex64)
        else:
            out = np.zeros(b1 - b0, dtype=np.complex64)
        for j in np.flatnonzero((spans[:, 1] > b0) & (spans[:, 0] < b1)):
            start, samples = render_burst(jobs[j], fs, center_mhz)
            a, b = max(start, b0), min(start + len(samples), b1)
            out[a - b0:b - b0] += samples[a - start:b - start]
        yield out


def synthesise_page(seed=7001, block=BLOCK, noise=True, **spec):
    """The samples and the sidecar of a capture, in memory: for a small run.
    ``spec`` is ``plan_exchanges``'s; a real file goes through ``write_capture``."""
    sidecar, jobs, total = plan_exchanges(seed=seed, **spec)
    iq = np.concatenate(list(render_blocks(sidecar, jobs, total, seed, block, noise)))
    return iq, sidecar


def write_capture(name, out_dir, seed=7001, **spec):
    """Make one capture, write ``synth_<name>.cf32`` block by block and
    ``synth_<name>.json`` into ``out_dir``. Every file, the single and the set,
    comes through here. Returns the two paths."""
    sidecar, jobs, total = plan_exchanges(seed=seed, **spec)
    os.makedirs(out_dir, exist_ok=True)
    iq_path = os.path.join(out_dir, 'synth_%s.cf32' % name)
    side_path = os.path.join(out_dir, 'synth_%s.json' % name)
    with open(iq_path, 'wb') as f:
        for out in render_blocks(sidecar, jobs, total, seed):
            out.astype('<c8', copy=False).tofile(f)
    with open(side_path, 'w') as f:
        json.dump(sidecar, f, indent=1)
    print('%s  %d samples, %.1f ms, %d %s exchanges, %d bursts, %d on channel %d'
          % (iq_path, total, total / sidecar['sample_rate'] * 1e3, sidecar['n_exchanges'], sidecar['file_kind'],
             sidecar['n_bursts'], sidecar['n_bursts_on_centre_channel'], sidecar['centre_channel']))
    print(side_path)
    return iq_path, side_path


#: What ``--set page`` writes, and nothing else, with its seeds.
PAGE_SET = [('page_exch_hop20', dict(kind='page', exchanges=200, followup=110, seed=7001)),
            ('inq_exch_hop20', dict(kind='inquiry', exchanges=200, followup=0, seed=7002))]

#: The sets ``--set`` knows.
SETS = {'page': PAGE_SET}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('name', nargs='?', help='the file is synth_<name>.cf32; not used with --set')
    ap.add_argument('--set', choices=sorted(SETS), help='write the named set, with its own seeds and '
                    'settings, and no other option but --out')
    ap.add_argument('--kind', choices=['page', 'inquiry'], help='default page')
    ap.add_argument('--exchanges', type=int, help='default 200')
    ap.add_argument('--followup', type=int, help='master packets after each page FHS; default 110 '
                    '(an inquiry has none and ignores it)')
    ap.add_argument('--snr', type=float, help='dB in 1 MHz; default 20')
    ap.add_argument('--seed', type=int, help='default 7001')
    ap.add_argument('--out', default=DEFAULT_OUT, help='one folder for both files; default ' + DEFAULT_OUT)
    args = ap.parse_args(argv)

    per_file = ['kind', 'exchanges', 'followup', 'snr', 'seed']
    if args.set:
        stray = [o for o in per_file if getattr(args, o) is not None]
        if args.name is not None or stray:
            ap.error('--set takes everything from its table: no name and no option but --out')
        runs = SETS[args.set]
    else:
        if args.name is None:
            ap.error('give the name of the file, or --set')
        spec = {}
        for key, value in (('kind', args.kind), ('exchanges', args.exchanges), ('followup', args.followup),
                           ('snr_db', args.snr), ('seed', args.seed)):
            if value is not None:
                spec[key] = value
        if spec.get('kind') == 'inquiry':
            spec['followup'] = 0
        runs = [(args.name, spec)]
    try:
        for name, spec in runs:
            write_capture(name, args.out, **spec)
    except ValueError as e:
        ap.error(str(e))


if __name__ == '__main__':
    main()
