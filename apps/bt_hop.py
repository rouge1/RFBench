"""Bluetooth hop selection kernel, connection state.

Core Specification v6.0, Vol 2 Part B, section 2.6: the basic channel hopping
sequence (2.6.1, 2.6.2) and the adapted one (2.6.3), 79-hop system.

    hop_channel(clk, address, used_channels=None) -> RF channel 0..78

``clk`` is CLK[27:0] of the Central. ``address`` is UAP[7:0] << 24 | LAP[23:0]
of the Central; the kernel uses A27..A0 only, that is UAP[3:0] and the LAP
(Table 2.3), so anything above bit 27 is ignored. ``used_channels`` is None for
the basic sequence, else a 79-bit mask with bit i set when RF channel i is used.

Where the specification text is ambiguous, and how it is resolved here:

1. Bit order of a field. The spec writes a field as "A22-19", "CLK6-2" or the
   list "A8,6,4,2,0", most significant bit first (2.6.4 says the MSBs of
   address and clock are XORed together, then the second MSBs, and so on).
   So X = CLK6-2 has CLK2 as X0, B = A22-19 has A19 as B0 and A22 as B3, C =
   A8,6,4,2,0 has A0 as C0, and E = A13,11,9,7,5,3,1 has A1 as E0. The XOR
   figure, 2.17, prints A22 A21 A20 A19 over Z'0 .. Z'4 in the extracted text,
   but its column alignment is lost, so the figure does not say which address
   bit meets which wire: the prose does, in 2.6.2.2 (the four LSBs of Z' with
   A22-19) and 2.6.4 (MSBs with MSBs). B reversed is wrong on 2,548 of Part G's
   7,680 values.
2. Butterflies (2.6.2.3). Table 2.1 gives the pairs and the text the stage
   order (P13 P12 first, P1 P0 last), but Figure 2.19, which says which
   multiplexer input a control value of 1 selects, is not in the text. A
   butterfly swaps its two bits when its P is 1 and passes them when it is 0.
3. Register bank (2.6.2.5). It says the "upper half" holds the even channels
   and the "lower half" the odd ones, but 2.6.2 and Figure 2.16 list all the
   even channels first (register 0 is channel 0, 1 is channel 2, ..., 40 is
   channel 1) and "upper" is only how the figure is drawn. The order is
   even-then-odd.
4. Same channel in the adapted sequence (2.6.3). The Peripheral answers on the
   channel the Central used, but the text does not say how the kernel does
   that, and Figure 2.20 still carries Y2 into k'. The sample data in Part G
   has the Central slot and the Peripheral slot after it (CLK1 = 0 and 1, same
   CLK27-2) on one channel, even with all 79 channels used, where the basic
   kernel would give two different ones. So the adapted sequence is computed
   with CLK1 taken as 0 (Y1 = Y2 = 0): the answer for the Peripheral slot is
   the answer for the Central slot before it. For a Peripheral packet that
   follows a multi-slot Central packet, the caller passes the clock of the
   Central slot that started it.
"""

# Table 2.1: the butterflies, P0..P13, as the pair of Z bits each one swaps.
BUTTERFLY = [(0, 1), (2, 3), (1, 2), (3, 4), (0, 4), (1, 3), (0, 2),   # P0..P6
             (3, 4), (1, 4), (0, 3), (2, 4), (1, 3), (0, 3), (1, 2)]   # P7..P13

# 2.6.2 and 2.6.2.5: the register bank holds every even channel, then every odd
# (note 3 above: the "upper half" wording of 2.6.2.5 is read as figure layout).
REGISTER_BANK = list(range(0, 79, 2)) + list(range(1, 79, 2))


def field(value, *positions):
    """Bits of value at the given positions, first position most significant:
    field(a, 8, 6, 4, 2, 0) is the spec's "A8,6,4,2,0"."""
    out = 0
    for pos in positions:
        out = out << 1 | (value >> pos) & 1
    return out


def span(value, high, low):
    """The spec's "A27-23": bits high down to low, as a number."""
    return field(value, *range(high, low - 1, -1))


def permute(z, p):
    """2.6.2.3: seven stages of two butterflies. Stage 1 is P13, P12 and stage 7
    is P1, P0; the two butterflies of a stage touch different bits. A butterfly
    swaps its bits when P = 1 (note 2 above: Figure 2.19 is not in the text)."""
    bit = [(z >> i) & 1 for i in range(5)]
    for n in range(13, -1, -1):
        if (p >> n) & 1:
            i, j = BUTTERFLY[n]
            bit[i], bit[j] = bit[j], bit[i]
    return sum(b << i for i, b in enumerate(bit))


def hop_channel(clk, address, used_channels=None):
    a = address & 0xFFFFFFF                      # A27..A0, Table 2.3
    if used_channels is not None:
        clk &= ~2                                # note 4 above: same channel

    # Control word: Table 2.2, "Connection state" column. Fields are read most
    # significant bit first (note 1 above); B, C and E are the ones that depend
    # on it for address bits.
    x = span(clk, 6, 2)                          # X  = CLK6-2
    y1 = span(clk, 1, 1)                         # Y1 = CLK1
    y2 = 32 * y1                                 # Y2 = 32 x Y1
    A = span(a, 27, 23) ^ span(clk, 25, 21)      # A27-23 xor CLK25-21
    B = span(a, 22, 19)                          # A22-19
    C = field(a, 8, 6, 4, 2, 0) ^ span(clk, 20, 16)   # A8,6,4,2,0 xor CLK20-16
    D = span(a, 18, 10) ^ span(clk, 15, 7)       # A18-10 xor CLK15-7
    E = field(a, 13, 11, 9, 7, 5, 3, 1)          # A13,11,9,7,5,3,1
    F = 16 * span(clk, 27, 7) % 79               # 16 x CLK27-7 mod 79

    # 2.6.2.1 first addition: add A to the phase X, mod 32.
    z_prime = (x + A) % 32
    # 2.6.2.2 XOR: the four LSBs with B; bit 4 is left unaltered.
    z = z_prime ^ B
    # 2.6.2.3 permutation: P0-8 = D0-8, P(i+9) = Ci xor Y1 for i = 0..4.
    p = D | (C ^ (31 * y1)) << 9
    perm5out = permute(z, p)
    # 2.6.2.4 second addition: PERM5out + E + F + Y2, mod 79.
    register = (perm5out + E + F + y2) % 79
    # 2.6.2.5 register bank.
    f_k = REGISTER_BANK[register]

    if used_channels is None:
        return f_k

    # 2.6.3.1 re-mapping: a used channel stays as it is, an unused one is
    # replaced by k' in the table of used channels, even ones first.
    if (used_channels >> f_k) & 1:
        return f_k
    table = [c for c in REGISTER_BANK if (used_channels >> c) & 1]
    n = len(table)
    if n == 0:
        raise ValueError('used_channels has no channel in 0..78')
    f_prime = 16 * span(clk, 27, 7) % n          # F' = 16 x CLK27-7 mod N
    return table[(perm5out + E + f_prime + y2) % n]
