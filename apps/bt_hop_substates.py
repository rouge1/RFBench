"""Bluetooth hop selection kernel, page and inquiry substates.

Core Specification v6.0, Vol 2 Part B, section 2.6: Figure 2.16 (the selection
box), Table 2.2 (the control word of each substate), Table 2.3 (the address
bits) and equations EQ 1 to EQ 8 (the X input), 79-hop system. The connection
state's basic and adapted sequences are ``apps/bt_hop.py``; this module is the
other seven sequences, and borrows only that module's permutation box, register
bank and bit helpers (Part G holds them to the specification, see below).

    page_scan(clkn, lap, uap)                        Table 2.2 (a)     X = CLKN16-12
    inquiry_scan(clkn, n=0)                          Table 2.2 (c)     X = Xir, EQ 8
    page(clke, lap, uap, koffset, knudge=0)          Table 2.2 (e)     X = Xp, EQ 2
    inquiry(clkn, koffset, knudge=0)                 Table 2.2 (f)     X = Xi, EQ 7
    peripheral_page_response(clkn_frozen, clkn, lap, uap, n)   (h)     X = Xprp, EQ 5
    central_page_response(clke_frozen, clke, lap, uap, koffset, knudge, n)   (g)   X = Xprc, EQ 6
    inquiry_response(clkn, n)                        Table 2.2 (k)     X = Xir, EQ 8

Every function returns the RF channel 0..78. The page, response and scan
sequences of one paged device all use the address of that device (Table 2.3: its
LAP and UAP3-0, 28 bits); every inquiry sequence uses the GIAC (0x9E8B33) with
the DCI (0x00) in A27-24. Only the X, Y1 and Y2 inputs change between the
substates, A to E being address bits (Table 2.2) and F zero.

Where the specification text is ambiguous or at odds with its own sample data:

1. **EQ 2, EQ 6 and EQ 7 as typeset** read ``[CLK16-12 + koffset + knudge + (CLK4-2,0
   - CLK16-12 + 32) mod 16] mod 32``, with CLK4-2,0 the 4-bit number of bits 4, 3,
   2 and 0 (most significant first: the same order the specification uses for
   "A8,6,4,2,0"); the extract has lost the brackets. The "+ 32" is a multiple of
   16 and changes nothing. This is what is implemented, unchanged. **Part G's page
   and inquiry sample table agrees with it in every one of its values once the
   train alternates A, B, A, B with each 0x1000 block**: CLKE16-12 = 0 and 2 are
   koffset 24 (the A train), 1 and 3 koffset 8 (the B train). That is the paging
   procedure itself: a train is repeated Npage times (Npage x 10 ms: 128 x 10 ms is
   one 1.28 s block, allowed for R1 by section 8.3.2) and the devices then switch
   trains (section 2.6.4.2 and EQ 3, 4). Part G does not state koffset for the page
   and inquiry table; it states 24 only for the Central response table, whose
   frozen clock is in block 0. There is no conflict between the data and the
   equation. ``scripts/test_bt_hop_substates.py`` reproduces all of the table's
   values this way, and shows that always-A fails on exactly the odd blocks.
2. **Y1 of the response substates.** Table 2.2 says Y1 is CLKN1 (Peripheral page
   response) or CLKE1 (Central page response) of the clock at the moment of
   transmission, not of the frozen clock: Part G's response tables, which
   alternate Y1 = 0 and 1 while their clock is frozen at 0x10 and 0x12, need
   exactly that. Only the 5 bits that make X are frozen. The inquiry response has
   Y1 = 1 always.
3. **The counter N.** EQ 5 and EQ 6: N runs "each time CLKN1 (CLKE1) is set to
   zero", which is the start of every master TX slot, every 4 ticks (1250 us).
   Part G's Peripheral response table starts at tick 0x12, one slot after the
   frozen 0x10, with N = 0, and N = 1 at 0x14 and 0x16, N = 2 at 0x18: so
   ``n_of(clock, frozen_slot_start)`` below is ``(clock - frozen_slot_start) // 4``
   for the Peripheral (N = 0 in the response to the page, 1 at the FHS slot and in
   the acknowledgement after it) and one more for the Central (N = 1 for the FHS).
4. **Inquiry scan.** Table 2.2 gives X of the inquiry scan as ``Xir`` (column c),
   and EQ 8 gives ``Xir = CLKN16-12 + N``, N the scanner's counter of FHS
   packets it has sent; Part G's scan table holds N = 0. ``inquiry_scan`` takes
   N, so a scanner that has answered scans one segment position on.
"""
from apps import bt_hop

#: GIAC with the DCI (0x00) above it, Table 2.3: A27-24 = DCI3-0 = 0.
GIAC = 0x9E8B33
GIAC_ADDRESS = GIAC

#: koffset of EQ 3: 24 for the A train, 8 for the B train.
KOFFSET = {'A': 24, 'B': 8}


def address(lap, uap=0):
    """The 28 address bits of Table 2.3: UAP3-0 above the 24 LAP bits."""
    return ((uap & 0xF) << 24) | (lap & 0xFFFFFF)


def control_word(x, y1, addr):
    """The control word of Table 2.2 for a page, inquiry, scan or response substate: X and Y1 as given,
    Y2 = 32 x Y1, A = A27-23, B = A22-19, C = A8,6,4,2,0, D = A18-10, E = A13,11,9,7,5,3,1, F = 0."""
    a = addr & 0xFFFFFFF
    return dict(X=x % 32, Y1=y1, Y2=32 * y1,
                A=bt_hop.span(a, 27, 23), B=bt_hop.span(a, 22, 19), C=bt_hop.field(a, 8, 6, 4, 2, 0),
                D=bt_hop.span(a, 18, 10), E=bt_hop.field(a, 13, 11, 9, 7, 5, 3, 1), F=0)


def select(x, y1, addr):
    """Figure 2.16 on the control word above (EQ 1: the substates differ only in X and Y1).
    Returns the RF channel 0..78."""
    w = control_word(x, y1, addr)
    z = ((w['X'] + w['A']) % 32) ^ w['B']           # first addition mod 32, then the XOR with the four LSBs
    p = w['D'] | (w['C'] ^ (31 * w['Y1'])) << 9     # P0-8 = D0-8, P(i+9) = Ci xor Y1
    perm5out = bt_hop.permute(z, p)
    return bt_hop.REGISTER_BANK[(perm5out + w['E'] + w['F'] + w['Y2']) % 79]


def clk16_12(clk):
    """CLK16-12, the five bits that make a scan's X input."""
    return (clk >> 12) & 0x1F


def clk4_2_0(clk):
    """CLK4-2,0 of EQ 2, 6 and 7: bits 4, 3, 2, 0 as a 4-bit number, bit 4 most significant."""
    return ((clk >> 4) & 1) << 3 | ((clk >> 3) & 1) << 2 | ((clk >> 2) & 1) << 1 | (clk & 1)


def x_train(clk, koffset, knudge=0):
    """EQ 2 (Xp, with CLKE), EQ 7 (Xi, with CLKN) and the first terms of EQ 6 (Xprc): the
    train position of a clock, ``[CLK16-12 + koffset + knudge + (CLK4-2,0 - CLK16-12) mod 16] mod 32``."""
    b = clk16_12(clk)
    return (b + koffset + knudge + (clk4_2_0(clk) - b) % 16) % 32


def x_page_scan(clkn, interlace_offset=0):
    """Table 2.2 (a) and (b): X = CLKN16-12, plus the interlace offset of (b), mod 32."""
    return (clk16_12(clkn) + interlace_offset) % 32


def x_peripheral_response(clkn_frozen, n):
    """EQ 5: Xprp = CLKN*16-12 + N, mod 32."""
    return (clk16_12(clkn_frozen) + n) % 32


def x_central_response(clke_frozen, koffset, knudge, n):
    """EQ 6: Xprc = EQ 2 of the frozen clock estimate, frozen koffset and knudge, plus N, mod 32."""
    return (x_train(clke_frozen, koffset, knudge) + n) % 32


def x_inquiry_response(clkn, n):
    """EQ 8: Xir = CLKN16-12 + N, mod 32 (the clock is not frozen)."""
    return (clk16_12(clkn) + n) % 32


def n_of(clock, frozen_slot_start):
    """The counter N of EQ 5 and EQ 6 at ``clock``: the number of master TX slot starts (CLK1 set to
    zero) since the slot that began at ``frozen_slot_start`` (a clock with CLK1-0 = 0), that slot's own
    start not counted. Part G's table: N = 0 at 0x12 (frozen 0x10), 1 at 0x14 and 0x16, 2 at 0x18."""
    return (clock - frozen_slot_start) // 4


# --- the seven sequences ------------------------------------------------------------

def page_scan(clkn, lap, uap, interlace_offset=0):
    """The page scan channel of the paged device (LAP, UAP of the device being scanned for = its own)
    at its native clock; Y1 = 0, so the channel holds for 1.28 s."""
    return select(x_page_scan(clkn, interlace_offset), 0, address(lap, uap))


def inquiry_scan(clkn, n=0, interlace_offset=0):
    """The inquiry scan channel: GIAC, X = Xir (Table 2.2 column c), Y1 = 0."""
    return select((x_inquiry_response(clkn, n) + interlace_offset) % 32, 0, GIAC_ADDRESS)


def page(clke, lap, uap, koffset, knudge=0):
    """The page channel at the paging device's clock estimate CLKE: X = Xp (EQ 2), Y1 = CLKE1."""
    return select(x_train(clke, koffset, knudge), (clke >> 1) & 1, address(lap, uap))


def inquiry(clkn, koffset, knudge=0):
    """The inquiry channel at the inquirer's native clock: X = Xi (EQ 7), Y1 = CLKN1, GIAC."""
    return select(x_train(clkn, koffset, knudge), (clkn >> 1) & 1, GIAC_ADDRESS)


def peripheral_page_response(clkn_frozen, clkn, lap, uap, n):
    """The paged device's response channel: X = Xprp (EQ 5) from the frozen CLKN*, Y1 = CLKN1 of ``clkn``,
    the clock at the moment of the response."""
    return select(x_peripheral_response(clkn_frozen, n), (clkn >> 1) & 1, address(lap, uap))


def central_page_response(clke_frozen, clke, lap, uap, koffset, knudge, n):
    """The paging device's FHS channel: X = Xprc (EQ 6) from the frozen CLKE*, koffset, knudge and N,
    Y1 = CLKE1 of ``clke``, the clock estimate at the moment of the transmission."""
    return select(x_central_response(clke_frozen, koffset, knudge, n), (clke >> 1) & 1, address(lap, uap))


def inquiry_response(clkn, n):
    """The inquiry response channel of the scanner: GIAC, X = Xir (EQ 8), Y1 = 1 always (Table 2.2 k)."""
    return select(x_inquiry_response(clkn, n), 1, GIAC_ADDRESS)


def train_channels(clk0, lap, uap, koffset, knudge=0, native=False, count=32):
    """The channels of the half-slots of ``count`` ticks from ``clk0`` (a multiple of 4), page (CLKE) or, with
    ``native``, inquiry (CLKN): one channel per tick, ``(tick, channel)`` for the TX ticks only (CLK1 = 0)."""
    out = []
    for t in range(count):
        clk = clk0 + t
        if (clk >> 1) & 1:
            continue
        out.append((t, inquiry(clk, koffset, knudge) if native else page(clk, lap, uap, koffset, knudge)))
    return out
