"""Bluetooth Basic Rate FHS packet - fields to the bits on air.

The FHS (frequency hop synchronisation) packet is what a master sends in a
page, to hand the paged device its address, class and clock, and what a device
sends back to an inquiry. bluey-ox-walker's receiver validates one by
brute-forcing the clock from the master's follow-up traffic and requiring it
to equal the clock carried here, so **a wrong bit in this file is a wrong
test of the receiver**. Built on ``bt_br_frame`` (the primitives held to the
Core specification's sample data) and checked by::

    python scripts/test_bt_fhs.py

against libbtbb, an independent decode written in the test, and - when it
exists - a golden FHS from bluey-ox-walker's own test.

Core specification v6.0, Vol 2 Part B (page numbers are the specification's):
§6.5.1.4 FHS packet, Figure 6.9 and Table 6.3 (pp. 523-524); §6.4.6 HEC
(p. 519 of the extract); §8.3.3.2 Central Page Response (pp. 563-564) and
§8.4.3 Inquiry Response (pp. 568-569). Type code 0b0010 (Table 6.2). On air,
all at 1 Msym/s, in transmission order:

=============  =====  ===========================================
Access code    72     the one in use: see below (§6.3.4: a trailer, as
                      a header follows, even in a DAC or IAC)
Header         54     LT_ADDR, TYPE=2, FLOW, ARQN, SEQN, HEC; FEC 1/3
Payload        240    144 bits + CRC-16 = 160, whitened, FEC 2/3
=============  =====  ===========================================

There is **no payload header** (§6.5.1.4). The 144 payload bits, each field
least significant bit first, are Figure 6.9:

=====================  =====  =====================================
parity bits            34     the first 34 bits of the sender's sync word
LAP                    24     the sender's
EIR                    1      an extended inquiry response follows (§8.4.3)
reserved               1
SR                     2      page scan repetition, Table 6.4
SP                     2      "shall be set to 0b10"
UAP                    8      the sender's
NAP                    16     the sender's
class of device        24
LT_ADDR                3      the address the recipient is to use (below)
CLK27-2                26     the sender's native clock, "sampled at the
                              beginning of the transmission of the access
                              code of this FHS packet"
previously used        3      zero (it was the page scan mode)
=====================  =====  =====================================

Where the specification fixes a choice, and what this module does about it:

* **LT_ADDR.** Table 6.3: the payload's LT_ADDR is the one the recipient is to
  use at connection set-up or role switch; a peripheral responding to a
  central, or a device responding to an inquiry, "shall include an all-zero
  LT_ADDR field" - that is the *payload* field, and it is what ``am_addr``
  is: the assigned address in a page, 0 in an inquiry response. The packet
  *header*'s LT_ADDR is fixed by the text only for the Central's page-response
  FHS (§8.3.3.2 p. 563-564: "shall be set to all-zeros"). For an inquiry
  response the extracts say nothing about the FHS header's LT_ADDR (§8.4.3 sets
  LT_ADDR to zero in the header of the *extended inquiry response* packet);
  0 is a model choice there, the same as the page case, and the default of
  ``lt_addr`` everywhere.
* **HEC and CRC initialisation.** §6.4.6: for an FHS in the Central Page
  Response substate "the Peripheral upper address part (UAP) shall be used"
  (the paged device's, not the sender's, which is in the payload); in the
  Inquiry Response substate "the default check initialization (DCI)", 0x00;
  "in all other cases the UAP of the Central". p. 524 repeats it for the
  CRC: "When initializing the HEC and CRC for the FHS packet of inquiry
  response, the UAP shall be the DCI." One value, ``header_uap``, starts both.
* **Access code.** A page's FHS carries the paged device's DAC
  (``access_lap`` its LAP); an inquiry response carries the IAC in use, the
  GIAC 0x9E8B33 for a general inquiry. In both a trailer follows (§6.3.4).
* **The clock.** ``clk`` is the sender's native CLK27-0 at the start of the
  transmission; the payload holds ``clk >> 2``. The FHS is sent on a slot
  boundary, so CLK1-0 are 0 in the sender's own slot. ``tx_clk`` seeds the
  whitening with its bits 6..1 (§7.2, which is not among the extracts
  checked here: the whitening rule is the one ``bt_br_frame`` implements
  for every packet, held to Part G's sample data). Which device's clock that
  is on air is the caller's to say; it defaults to ``clk``.
* **Whitening of an FHS in a response - the X-input.** §7.2 (p. 541-542): an
  ordinary packet's whitening register starts as CLK6-1 (CLK1 in position 0,
  CLK6 in position 5) and a 1 in position 6, but "exceptions are the FHS
  packet sent during inquiry response or Central page response ... Instead of
  the Central's clock, the X-input used in the inquiry or page response
  routine shall be used, see Table 2.2. The 5-bit value shall be extended
  with two MSBs of value 1 ... X0 written to position 0". So the register is
  [X0 X1 X2 X3 X4 1 1], which is ``bt_br_frame.whitening``'s seed for the
  pseudo-clock ``tx_clk = (X | 0x20) << 1`` (CLK6-1 = 32 + X). ``whitening_x``
  is that X (0..31): ``page_response`` and ``inquiry_response`` require it;
  ``FHS(..., whitening_x=None)`` seeds from CLK6-1 of ``tx_clk`` as for any
  other packet (the machinery check, and a golden packet that does so). The
  X itself is ``xprc`` (EQ 6, §2.6.4.4, p. 493) for the Central's page
  response and ``xir`` (EQ 8, §2.6.4.6, p. 494) for an inquiry response.
* **Not fixed by the text, defaulted:** FLOW, ARQN and SEQN of the FHS header
  are 0. This is a model choice: the text is silent for the FHS (§8.4.3 p. 569
  sets them to zero for the *extended inquiry response* packet, which is a
  different packet); zero is the neutral value and what an ID-less, ARQ-less
  link has before any traffic. ``previously_used`` (3 bits) is 0: the
  specification is silent beyond Figure 6.9, so it is defaulted.

``sp``, ``eir``, ``reserved``, ``sr`` and ``previously_used`` are parameters
so that a packet from an encoder that leaves them zero can be reproduced; the
defaults follow the specification (SP = 0b10).
"""

import numpy as np  # noqa: F401  (kept for neighbours' symmetry; bits are lists)

from apps import bt_br_frame as br

#: The default check initialisation of an inquiry (§7.1), the "UAP" an
#: inquiry response's HEC and CRC are started from.
DCI = 0x00

#: The general inquiry access code's LAP.
GIAC = 0x9E8B33

FHS_TYPE = 0b0010

#: (name, width) of the payload fields in transmission order. ``parity`` is
#: derived from the LAP and ``undef`` and ``rsvd`` are zero.
FIELDS = (('parity', 34), ('lap', 24), ('eir', 1), ('reserved', 1),
          ('sr', 2), ('sp', 2), ('uap', 8), ('nap', 16), ('cod', 24),
          ('am_addr', 3), ('clk27_2', 26), ('previously_used', 3))

PAYLOAD_BITS = 144            # then 16 of CRC: 160 bits, 240 after FEC 2/3
AIR_BITS = 72 + 54 + 240


def _check(name, value, width):
    value = int(value)
    if not 0 <= value < (1 << width):
        raise ValueError("%s %d does not fit in %d bits" % (name, value, width))
    return value


def fhs_payload_bits(lap, uap, nap, cod, am_addr, clk27_2, sr=0, sp=0b10,
                     eir=0, reserved=0, previously_used=0):
    """The 144 payload bits in transmission order. ``clk27_2`` is the 26-bit
    value of CLK27-2; the 34 parity bits are the sync word's, from ``lap``."""
    lap = _check('lap', lap, 24)
    bits = br.sync_word(lap)[:34]                       # 34 parity bits
    bits += br.lsb_bits(lap, 24)
    bits += br.lsb_bits(_check('eir', eir, 1), 1)
    bits += br.lsb_bits(_check('reserved', reserved, 1), 1)
    bits += br.lsb_bits(_check('sr', sr, 2), 2)
    bits += br.lsb_bits(_check('sp', sp, 2), 2)
    bits += br.lsb_bits(_check('uap', uap, 8), 8)
    bits += br.lsb_bits(_check('nap', nap, 16), 16)
    bits += br.lsb_bits(_check('cod', cod, 24), 24)
    bits += br.lsb_bits(_check('am_addr', am_addr, 3), 3)
    bits += br.lsb_bits(_check('clk27_2', clk27_2, 26), 26)
    bits += br.lsb_bits(_check('previously_used', previously_used, 3), 3)
    assert len(bits) == PAYLOAD_BITS
    return bits


def parse_fhs_payload(bits):
    """The inverse of ``fhs_payload_bits``: a dict of the fields, plus
    ``parity_ok`` - whether the 34 parity bits are those of the LAP."""
    bits = [int(b) for b in bits]
    if len(bits) != PAYLOAD_BITS:
        raise ValueError("an FHS payload is %d bits, not %d"
                         % (PAYLOAD_BITS, len(bits)))
    out, n = {}, 0
    for name, width in FIELDS:
        out[name] = br.bits_int(bits[n:n + width])
        n += width
    out['parity_ok'] = (br.lsb_bits(out['parity'], 34)
                        == br.sync_word(out['lap'])[:34])
    return out


def whitening_clk(x):
    """The pseudo-clock whose CLK6-1 seeds the whitening register with X
    extended by two MSBs of 1 (§7.2): CLK6-1 = 32 + X."""
    return ((int(x) & 0x1F) | 0x20) << 1


def whitening_register(x):
    """The 7 initial bits of the whitening register for an X-input, position 0
    first: X0 .. X4, 1, 1 (§7.2)."""
    return ''.join(str((int(x) >> i) & 1) for i in range(5)) + '11'


def xprc(clke, koffset, knudge, n):
    """The Central page response X-input, EQ 6 (§2.6.4.4, p. 493), from the
    frozen clock estimate ``clke`` (CLKE*), the frozen k_offset (24 for the
    A-train, 8 for the B-train) and k_nudge, and the counter N (1 for the
    first FHS). "CLKE4-2,0" is read as the 4-bit number of bits 4, 3, 2 and 0,
    most significant first, as the spec's other lists of bits are (§2.6.2,
    "A8,6,4,2,0"). Another reading (least significant first, say) would give a
    different Xprc; the inputs are recorded in the generator's sidecar so a
    reader can recompute under either. The term is ``(CLKE4-2,0 - CLKE16-12) mod 16`` as in EQ 2."""
    c16_12 = (clke >> 12) & 0x1F
    c4_2_0 = ((clke >> 4) & 1) << 3 | ((clke >> 3) & 1) << 2 | ((clke >> 2) & 1) << 1 | (clke & 1)
    return (c16_12 + koffset + knudge + (c4_2_0 - c16_12) % 16 + n) % 32


def xir(clkn, n):
    """The inquiry response X-input, EQ 8 (§2.6.4.6, p. 494): CLKN16-12 of the
    responding device at the FHS, plus its counter N, mod 32."""
    return (((clkn >> 12) & 0x1F) + n) % 32


def id_bits(lap):
    """The 68-bit ID packet: preamble and sync word, no trailer and no
    header (§6.5.1.1)."""
    return br.access_code(lap, trailer=False)


class FHS:
    """One FHS packet, built and kept with the truth a grader needs.

    ``access_lap`` is the LAP of the access code on air (the paged device's
    for a page, the GIAC's for an inquiry response); ``lap``, ``uap``,
    ``nap`` the *sender's* address, as carried in the payload. ``clk`` is the
    sender's CLK27-0 at the start of the transmission (28 bits; the payload
    carries ``clk >> 2``) and ``tx_clk`` the clock whose bits 6..1 whiten it,
    ``clk`` if not given; or ``whitening_x`` (0..31), the X-input of the
    response routine, which replaces the clock (see above) and sets ``tx_clk``
    to the pseudo-clock ``(X | 0x20) << 1`` that seeds the same register.
    ``header_uap`` initialises the HEC and the CRC (see
    the module's docstring), the payload's ``uap`` if not given.
    """

    def __init__(self, access_lap, lap, uap, nap, cod, am_addr, clk, tx_clk=None,
                 lt_addr=0, flow=0, arqn=0, seqn=0, header_uap=None, sr=0,
                 sp=0b10, eir=0, reserved=0, previously_used=0, whitening_x=None):
        clk = _check('clk', clk, 28)
        if whitening_x is not None:
            if tx_clk is not None:
                raise ValueError("give tx_clk or whitening_x, not both")
            tx_clk = whitening_clk(_check('whitening_x', whitening_x, 5))
        tx_clk = clk if tx_clk is None else _check('tx_clk', tx_clk, 28)
        self.whitening_x = whitening_x
        header_uap = uap if header_uap is None else header_uap
        self.access_lap = _check('access_lap', access_lap, 24)
        self.lap, self.uap, self.nap, self.cod = lap, uap, nap, cod
        self.am_addr, self.sr, self.sp = am_addr, sr, sp
        self.eir, self.reserved, self.previously_used = eir, reserved, previously_used
        self.clk, self.tx_clk = clk, tx_clk
        self.clk27_2 = clk >> 2
        self.lt_addr, self.flow, self.arqn, self.seqn = lt_addr, flow, arqn, seqn
        self.header_uap = _check('header_uap', header_uap, 8)

        header = br.packet_header(lt_addr, FHS_TYPE, flow, arqn, seqn,
                                  self.header_uap)
        #: The 18 header bits as one integer, before whitening and FEC.
        self.header18 = br.bits_int(header)
        payload = fhs_payload_bits(lap, uap, nap, cod, am_addr, self.clk27_2,
                                   sr, sp, eir, reserved, previously_used)
        payload += br.crc16(payload, self.header_uap)
        #: The 144 payload bits and the CRC, as bytes, before whitening.
        self.payload_full = br.bits_bytes(payload)
        w = br.whitening(tx_clk, len(header) + len(payload))
        header = [b ^ x for b, x in zip(header, w)]
        payload = [b ^ x for b, x in zip(payload, w[len(header):])]
        self.bits = br.access_code(self.access_lap) + br.fec13(header) \
            + br.fec23(payload)
        assert len(self.bits) == AIR_BITS

    @property
    def duration_us(self):
        return float(len(self.bits))

    def sidecar(self):
        """This packet's entry in a sidecar's ``bursts`` list, less the
        ``start_sample``; ``air_bits`` is left out."""
        return {
            'ptype': 'FHS',
            'clk': self.tx_clk,
            'access_lap': self.access_lap,
            'lap': self.lap,
            'uap': self.uap,
            'nap': self.nap,
            'cod': self.cod,
            'am_addr': self.am_addr,
            'sr': self.sr,
            'sp': self.sp,
            'eir': self.eir,
            'reserved': self.reserved,
            'previously_used': self.previously_used,
            'fhs_clk': self.clk,
            'clk27_2': self.clk27_2,
            'tx_clk': self.tx_clk,
            'whitening_x': self.whitening_x,
            'whitening_register': (whitening_register(self.whitening_x)
                                   if self.whitening_x is not None else None),
            'implied_clk6_1': (self.tx_clk >> 1) & 0x3F,
            'header_uap': self.header_uap,
            'lt_addr': self.lt_addr,
            'flow': self.flow,
            'arqn': self.arqn,
            'seqn': self.seqn,
            'header18': self.header18,
            'payload_full_hex': self.payload_full.hex(),
            'payload_valid': True,
        }


def inquiry_response(lap, uap, nap, cod, clk, whitening_x, access_lap=GIAC,
                     **kw):
    """An inquiry response (§8.4.3): the responder's own address and clock,
    the IAC's access code, the DCI for HEC and CRC, an all-zero LT_ADDR in
    the payload and the header, whitened from the X-input (§7.2): required."""
    return FHS(access_lap, lap, uap, nap, cod, 0, clk, lt_addr=0,
               header_uap=DCI, whitening_x=whitening_x, **kw)


def page_response(paged_lap, paged_uap, lap, uap, nap, cod, am_addr, clk,
                  whitening_x, **kw):
    """The central's FHS in its Page Response substate (§8.3.3.2): the paged
    device's DAC and UAP for the access code, the HEC and the CRC; the
    central's own address and clock in the payload; ``am_addr`` the
    LT_ADDR it assigns; the header's LT_ADDR all zero; whitened from the
    X-input Xprc (§7.2, EQ 6): required."""
    return FHS(paged_lap, lap, uap, nap, cod, am_addr, clk, lt_addr=0,
               header_uap=paged_uap, whitening_x=whitening_x, **kw)


def fhs_air_bits(access_lap, lap, uap, nap, cod, am_addr, clk, tx_clk=None,
                 **kw):
    """The 366 air bits of an FHS; see ``FHS`` for the arguments."""
    return FHS(access_lap, lap, uap, nap, cod, am_addr, clk, tx_clk, **kw).bits


def _fec23_fix(block):
    """One 15-bit block to its 10 data bits, a single error corrected."""
    data = block[:10]
    if br.fec23(data) == list(block):
        return data
    for i in range(15):
        t = list(block)
        t[i] ^= 1
        if br.fec23(t[:10]) == t:
            return t[:10]
    raise ValueError("more than one bit wrong in a FEC 2/3 block")


def parse_fhs_air_bits(bits, header_uap, tx_clk=None, whitening_x=None):
    """Decode 366 air bits (or the 294 after the access code) given the
    initialisation UAP and the whitening clock, or the X-input of a response: the payload's fields, plus
    ``crc_ok`` and ``header_ok`` (HEC) and the header's fields."""
    if whitening_x is not None:
        tx_clk = whitening_clk(whitening_x)
    bits = [int(b) for b in bits]
    if len(bits) == AIR_BITS:
        bits = bits[72:]
    if len(bits) != 54 + 240:
        raise ValueError("FHS after the access code is 294 bits")
    h = bits[:54]
    hdr = []
    for i in range(0, 54, 3):
        hdr.append(1 if sum(h[i:i + 3]) >= 2 else 0)
    pay = []
    for n in range(54, len(bits), 15):
        pay += _fec23_fix(bits[n:n + 15])
    w = br.whitening(tx_clk, 18 + 160)
    hdr = [b ^ x for b, x in zip(hdr, w)]
    pay = [b ^ x for b, x in zip(pay, w[18:])]
    out = parse_fhs_payload(pay[:144])
    out['crc_ok'] = br.crc16(pay[:144], header_uap) == pay[144:160]
    out['header_ok'] = br.hec(hdr[:10], header_uap) == hdr[10:]
    out.update(lt_addr=br.bits_int(hdr[0:3]), type=br.bits_int(hdr[3:7]),
               flow=hdr[7], arqn=hdr[8], seqn=hdr[9])
    return out
