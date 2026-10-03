"""Bluetooth Basic Rate packet encoder - fields to the bits on air.

The transmit half of the test signals made for bluey-ox-walker, whose decoder
has never heard a plain Basic Rate multi-slot packet on air.
[knowledge/bluey-test-signals.md](../knowledge/bluey-test-signals.md) has the
plan and the file format, and [knowledge/bluetooth.md](../knowledge/bluetooth.md)
why a VSG60 can send this at all. Everything here is from the Core
specification v6.0, Vol 2 Part B (the baseband), and is held to that
specification's own sample data in Part G by::

    python scripts/test_bt_br_frame.py

Kept free of GNU Radio and Qt, like ``ism_frame``, so a packet can be built and
checked with nothing plugged in.

Every list of bits here is in **transmission order**: index 0 goes on air
first. The specification sends every field least significant bit first, and
writes its polynomials as D-transforms whose powers count in the same order, so
``bits[i]`` is also the coefficient of D^i. The one place that stops being true
is where a register is read out - the HEC and the CRC go out from their top
position down - and that is done inside the function that computes them.

A packet on air (§6.1), all at 1 Msym/s:

=============  =====  ===========================================
Access code    72     preamble 4, sync word 64, trailer 4 (§6.3)
Header         54     18 bits, whitened, then FEC 1/3 (§6.4)
Payload        any    payload header, body, CRC-16; whitened, then
                      FEC 2/3 in a DM packet only (§6.5.4)
=============  =====  ===========================================

Four things here look like mistakes until you know what they are for, and
bluey-ox-walker found each of them the hard way on the receiving side:

* **Whitening comes before the FEC, not after** (§7.2). The header's 18 bits
  are whitened and only then tripled, so every triplet on air is unanimous.
  Whiten after the FEC and the triplets disagree, which is exactly what a
  receiver doing it the right way round then sees as noise.
* **Whitening is seeded from the master's clock**, CLK6-1 (§7.2), not from
  the channel as in Bluetooth LE. Each burst is whitened with its own clock,
  so a train of them is a different bit stream every time even with the same
  payload. ``clk`` here is always the native CLK27-0, which counts 312.5 us
  half-slots; the whitening takes bits 6-1 of it.
* **The HEC and the CRC are initialised with the UAP**, not zero or ones
  (§7.1), so the same header gives a different HEC for every master. Both
  shift their data in least significant bit first and read their register out
  from the top position down.
* **A passing FEC proves nothing about the bits.** It only shows the same
  value was sent three times. Hence the tests here are all against the
  specification's numbers, never against a decoder of our own.

The 2-byte payload header's length field is 10 bits (Figure 6.13). DH3 and
DH5 never use the tenth, since their longest body, 339 bytes, fits in nine.
"""

import numpy as np

# --- packet types -------------------------------------------------------------

#: name: (TYPE code, slots, FEC 2/3 on the payload, 2-byte payload header,
#: longest body in bytes) - Table 6.2 and §6.5.4. NULL and POLL are an access
#: code and a header and nothing else (§6.5.1.2-3): no payload header, no
#: CRC, which their ``None`` marks.
PACKET_TYPES = {
    'NULL': (0b0000, 1, False, None, 0),
    'POLL': (0b0001, 1, False, None, 0),
    'DM1': (0b0011, 1, True, False, 17),
    'DH1': (0b0100, 1, False, False, 27),
    'DM3': (0b1010, 3, True, True, 121),
    'DH3': (0b1011, 3, False, True, 183),
    'DM5': (0b1110, 5, True, True, 224),
    'DH5': (0b1111, 5, False, True, 339),
}

#: One slot, 625 us, is two ticks of the native clock CLK27-0.
SLOT_US = 625.0

#: Basic Rate GFSK (Vol 2 Part A §3.1.1): BT 0.5, a modulation index of
#: 0.28-0.35, 1 Msym/s, a 1 the positive deviation.
SYMBOL_RATE = 1e6
GFSK_BT = 0.5
GFSK_H = 0.32


# --- bits ---------------------------------------------------------------------


def lsb_bits(value, width):
    """An integer as ``width`` bits, least significant first - the order every
    field but the access code's trailer pattern goes on air in."""
    value = int(value)
    if not 0 <= value < (1 << width):
        raise ValueError("%d does not fit in %d bits" % (value, width))
    return [(value >> i) & 1 for i in range(width)]


def bytes_bits(data):
    """Bytes as bits, each byte least significant bit first, in byte order."""
    return [(b >> i) & 1 for b in bytes(data) for i in range(8)]


def bits_bytes(bits):
    """The inverse of ``bytes_bits``; the bit count must be a whole byte."""
    if len(bits) % 8:
        raise ValueError("%d bits is not a whole number of bytes" % len(bits))
    return bytes(sum(b << i for i, b in enumerate(bits[n:n + 8]))
                 for n in range(0, len(bits), 8))


def bits_int(bits):
    """Bits, least significant first, back to the integer they came from."""
    return sum(b << i for i, b in enumerate(bits))


def bit_string(bits):
    """``'0101...'`` in transmission order, as the sidecar's ``air_bits``."""
    return ''.join('1' if b else '0' for b in bits)


def _lfsr(bits, state, taps):
    """The register the HEC, the CRC and the FEC 2/3 all share (Figures 7.3,
    7.6 and 7.11): data in at the top, its feedback XORed into each tapped
    position on the way through. ``state`` is the starting register, position 0
    first; the result is read out from the top position down, which is the
    order all three go on air in."""
    r = list(state)
    top = len(r) - 1
    for d in bits:
        fb = d ^ r[top]
        r = [fb] + [r[k - 1] ^ (fb if k in taps else 0) for k in range(1, top + 1)]
    return r[::-1]


# --- access code (§6.3) -------------------------------------------------------

#: The PN overlay, p0 to p63 in transmission order (EQ 12).
_PN = [int(b) for b in format(0x3F2A33DD69B121C1, '064b')]

#: g(D) of the (64,30) expurgated block code (EQ 9), bit k the coefficient of D^k.
_SYNC_G = 0x585713DA9

#: The length-seven Barker extensions, keyed by the LAP's top bit (EQ 11).
_BARKER = {0: [0, 0, 1, 1, 0, 1], 1: [1, 1, 0, 0, 1, 0]}


def sync_word(lap):
    """The 64-bit sync word for a 24-bit LAP, by the five steps of §6.3.3.1."""
    a = lsb_bits(lap, 24)
    x = a + _BARKER[a[23]]                               # step 1
    x = [b ^ p for b, p in zip(x, _PN[34:])]             # step 2
    rem = bits_int(x) << 34                              # step 3: D^34 x mod g
    for k in range(63, 33, -1):
        if (rem >> k) & 1:
            rem ^= _SYNC_G << (k - 34)
    s = lsb_bits(rem, 34) + x                            # step 4
    return [b ^ p for b, p in zip(s, _PN)]               # step 5


def access_code(lap, trailer=True):
    """Preamble, sync word and (for any packet with a header) trailer.

    Both DC-free patterns are chosen to continue the alternation into the sync
    word's neighbouring bit (§6.3.2, §6.3.4): ``1010`` before a sync word that
    starts with 1, and ``1010`` after one whose last bit is 0.
    """
    s = sync_word(lap)
    pre = [1, 0, 1, 0] if s[0] else [0, 1, 0, 1]
    if not trailer:
        return pre + s
    return pre + s + ([0, 1, 0, 1] if s[63] else [1, 0, 1, 0])


# --- error checks and whitening (§7) ------------------------------------------


def hec(header10, uap):
    """The 8 HEC bits for 10 header bits (§7.1.1), in transmission order.

    g(D) = D^8 + D^7 + D^5 + D^2 + D + 1, the register preset with the UAP,
    UAP0 in position 0.
    """
    return _lfsr(header10, lsb_bits(uap, 8), {1, 2, 5, 7})


def crc16(bits, uap):
    """The CRC-16 over a payload header and body (§7.1.2), in transmission
    order. CRC-CCITT, D^16 + D^12 + D^5 + 1, the low eight positions preset
    with the UAP and the high eight with zero."""
    return _lfsr(bits, lsb_bits(uap, 8) + [0] * 8, {5, 12})


def whitening(clk, n):
    """``n`` bits of the whitening sequence for native clock ``clk`` (§7.2).

    D^7 + D^4 + 1, CLK1 in position 0 up to CLK6 in position 5, and a 1 in
    position 6. The output is position 6. The header and the payload use one
    run of it, with no reseeding between them.
    """
    p = [((clk >> (i + 1)) & 1) for i in range(6)] + [1]
    out = []
    for _ in range(n):
        o = p[6]
        out.append(o)
        p = [o, p[0], p[1], p[2], p[3] ^ o, p[4], p[5]]
    return out


def fec13(bits):
    """Rate 1/3: every bit three times (§7.4)."""
    return [b for b in bits for _ in range(3)]


def fec23(bits):
    """Rate 2/3, the (15,10) shortened Hamming code (§7.5): ten data bits then
    five parity, the data padded with zeros to a whole number of blocks."""
    bits = list(bits) + [0] * (-len(bits) % 10)
    out = []
    for n in range(0, len(bits), 10):
        block = bits[n:n + 10]
        out += block + _lfsr(block, [0] * 5, {2, 4})
    return out


# --- the packet ---------------------------------------------------------------


def packet_header(lt_addr, ptype, flow, arqn, seqn, uap):
    """The 18 header bits before whitening: LT_ADDR, TYPE, FLOW, ARQN, SEQN
    and the HEC over those ten (§6.4)."""
    code = PACKET_TYPES[ptype][0] if isinstance(ptype, str) else ptype
    ten = (lsb_bits(lt_addr, 3) + lsb_bits(code, 4) + lsb_bits(flow, 1)
           + lsb_bits(arqn, 1) + lsb_bits(seqn, 1))
    return ten + hec(ten, uap)


def payload_header(llid, flow, length, two_byte):
    """LLID, FLOW and LENGTH (Figures 6.12 and 6.13): one byte with a 5-bit
    length, or two with a 10-bit length and three RFU zeros."""
    if two_byte:
        return lsb_bits(llid, 2) + lsb_bits(flow, 1) + lsb_bits(length, 10) + [0] * 3
    return lsb_bits(llid, 2) + lsb_bits(flow, 1) + lsb_bits(length, 5)


class Packet:
    """One Basic Rate ACL packet, built and kept with everything a grader needs.

    ``bits`` is what goes into the modulator, first preamble bit to the last
    payload bit. The other attributes are the same packet at the stages a
    receiver can check it at, named for the sidecar keys bluey-ox-walker grades
    against.
    """

    def __init__(self, lap, uap, clk, ptype, body=b'', lt_addr=1, flow=1, arqn=0,
                 seqn=0, llid=0b10, payload_flow=1, whiten=True, raw_payload=None):
        if ptype not in PACKET_TYPES:
            raise ValueError("no packet type %r; one of %s"
                             % (ptype, ', '.join(PACKET_TYPES)))
        _code, self.slots, fec, two_byte, longest = PACKET_TYPES[ptype]
        body = bytes(body)
        if len(body) > longest:
            raise ValueError("%s carries at most %d bytes, not %d"
                             % (ptype, longest, len(body)))
        self.lap, self.uap, self.clk, self.ptype = lap, uap, clk, ptype
        self.lt_addr, self.flow, self.arqn, self.seqn = lt_addr, flow, arqn, seqn
        self.body = body

        header = packet_header(lt_addr, ptype, flow, arqn, seqn, uap)
        #: The 18 header bits as one integer, LT_ADDR in bit 0 and the HEC in
        #: bits 10-17, before whitening and FEC.
        self.header18 = bits_int(header)

        if raw_payload is not None:
            # What an encrypted link looks like to a sniffer: E0 covers the
            # whole payload - its header, body and CRC - so the header above
            # checks out and these bytes, sent as they are, do not.
            if two_byte is None or body:
                raise ValueError("raw_payload replaces the body of a packet "
                                 "that has one")
            longest_raw = (2 if two_byte else 1) + longest + 2
            if not 0 < len(raw_payload) <= longest_raw:
                raise ValueError("%s's payload is 1 to %d bytes, not %d"
                                 % (ptype, longest_raw, len(raw_payload)))
            payload = bytes_bits(raw_payload)
        elif two_byte is None:                   # NULL and POLL
            payload = []
        else:
            payload = (payload_header(llid, payload_flow, len(body), two_byte)
                       + bytes_bits(body))
            payload += crc16(payload, uap)
        #: Payload header, body and CRC as bytes, before whitening; empty for
        #: a packet with no payload, and the raw bytes for an encrypted one.
        self.payload_full = bits_bytes(payload)
        #: Whether the payload's CRC holds under this UAP, as a plaintext
        #: payload's always does.
        self.payload_valid = raw_payload is None and two_byte is not None
        if raw_payload is not None:
            self.payload_valid = crc16(payload[:-16], uap) == payload[-16:]

        if whiten:
            w = whitening(clk, len(header) + len(payload))
            header = [b ^ x for b, x in zip(header, w)]
            payload = [b ^ x for b, x in zip(payload, w[len(header):])]
        if fec:
            payload = fec23(payload)
        self.bits = access_code(lap) + fec13(header) + payload

    @property
    def duration_us(self):
        """Time on air at 1 Msym/s."""
        return float(len(self.bits))

    def sidecar(self):
        """This packet's entry in the sidecar's ``bursts`` list, less the
        ``start_sample`` only the renderer knows."""
        return {
            'ptype': self.ptype,
            'clk': self.clk,
            'lt_addr': self.lt_addr,
            'flow': self.flow,
            'arqn': self.arqn,
            'seqn': self.seqn,
            'header18': self.header18,
            'payload_hex': self.body.hex(),
            'payload_full_hex': self.payload_full.hex(),
            'payload_valid': self.payload_valid,
            'air_bits': bit_string(self.bits),
        }


# --- what goes in the payload -------------------------------------------------

_B64 = b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'


def seq_body(counter, length, cid=0x0040):
    """An L2CAP basic frame carrying ``SEQ=<8 hex digits> `` and a cyclic run
    of the base64 alphabet - what bluey-ox-walker's own test rigs send, so a
    decoded payload can be told apart from noise at a glance.

    ``length`` is the whole body, the 4-byte L2CAP header included.
    """
    text = b'SEQ=%08X ' % (counter & 0xFFFFFFFF)
    n = length - 4 - len(text)
    if n < 0:
        raise ValueError("a body of %d bytes cannot hold the SEQ text" % length)
    text += bytes(_B64[(counter + i) % 64] for i in range(n))
    return (len(text)).to_bytes(2, 'little') + cid.to_bytes(2, 'little') + text


# --- bits to baseband ---------------------------------------------------------


def frequency_pulse(t, bt=GFSK_BT):
    """The Gaussian-filtered one-symbol pulse, ``t`` in symbols from the
    symbol's centre: a rectangle one symbol wide through a Gaussian filter of
    bandwidth BT. It reaches 1 only for a long run of equal bits, which is
    where the deviation is the full h/2 symbol rates."""
    from scipy.special import erf
    sigma = np.sqrt(np.log(2)) / (2 * np.pi * bt)
    k = 1 / (sigma * np.sqrt(2))
    return 0.5 * (erf(k * (t + 0.5)) - erf(k * (t - 0.5)))


def gfsk(bits, fs, h=GFSK_H, bt=GFSK_BT, delay=0.0, ramp_us=2.0):
    """One burst of GFSK at unit amplitude, as ``complex64``.

    Sample n is taken at time ``(n - delay) / fs`` from the start of the first
    bit, so ``delay`` is a fractional-sample timing offset that is exact: the
    pulses are evaluated where the samples fall rather than interpolated
    afterwards. The burst begins ``ramp_us`` before the first bit and ends as
    long after the last, with raised-cosine power ramps over that time, so
    the samples begin a little ahead of the first bit. Returns the samples
    and ``lead``, how many of them come before the first bit begins.

    A 1 is the positive deviation, so the phase turns anticlockwise - a
    discriminator ``imag(z[n] * conj(z[n - 1]))`` reads it as positive.
    """
    sps = fs / SYMBOL_RATE
    a = 2.0 * np.asarray(bits, dtype=np.float64) - 1.0
    lead = int(np.ceil(ramp_us * 1e-6 * fs)) + 1
    n = np.arange(-lead, int(np.ceil(len(a) * sps)) + lead)
    t = (n - delay) / sps                        # in symbols from bit 0's start
    # Each sample feels only the few symbols around it: BT 0.5 has spread to
    # nothing two symbols out.
    freq = np.zeros(len(t))
    span = 3
    centre = np.floor(t).astype(int)
    for k in range(-span, span + 1):
        idx = centre + k
        ok = (idx >= 0) & (idx < len(a))
        freq[ok] += a[idx[ok]] * frequency_pulse(t[ok] - idx[ok] - 0.5, bt)
    # Deviation h/2 symbol rates; phase is its running sum, in cycles per sample.
    phase = 2 * np.pi * np.cumsum(freq * (h / 2) / sps)
    env = np.ones(len(t))
    rise = ramp_us * sps
    edge_lo = t * sps + rise                     # samples into the ramp-up
    up = edge_lo < rise
    env[up] = 0.5 - 0.5 * np.cos(np.pi * np.clip(edge_lo[up], 0, rise) / rise)
    edge_hi = (len(a) - t) * sps + rise          # samples left of the ramp-down
    down = edge_hi < rise
    env[down] = 0.5 - 0.5 * np.cos(np.pi * np.clip(edge_hi[down], 0, rise) / rise)
    return (env * np.exp(1j * phase)).astype(np.complex64), lead

