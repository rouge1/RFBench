#!/usr/bin/env python3
"""Hold the Bluetooth Basic Rate encoder to the Core specification's own numbers.

    python scripts/test_bt_br_frame.py

Every expected value below is copied from the Bluetooth Core specification
v6.0, Vol 2 Part G, the baseband sample data, and nothing here is computed by
a decoder of our own: a round trip through one would pass with the encoder and
the decoder wrong in the same way, which is the trap
[knowledge/bluey-test-signals.md](../knowledge/bluey-test-signals.md) warns of.
bluey-ox-walker keeps the same Part G as ``knowledge/sample_data.md``.

Part G prints most of its numbers in transmission order, left to right, which
is the order ``apps/bt_br_frame.py`` keeps its bits in; where it does not -
the HEC and CRC values, written as numbers - the conversion is spelt out at
the check.

It checks, cheapest first:

* the whitening sequence, 128 bits from an all-ones register (Part G §8),
* the FEC 2/3 codeword of each of the ten single-bit inputs (§9),
* the access code - preamble, sync word and trailer - for all 130 LAPs (§2),
* the HEC and the whole 54-bit header for 20 UAP and header pairs (§4),
* the CRC of a 10-byte payload (§5),
* two complete packets, a DH1 and a DM1, header and payload (§6),

and then that a whitened DH5 and DH3 come out the length a 5- and 3-slot
packet should, with a payload that whitening actually changed.

Part G has no whitened packet, so the clock's way into the whitening
register is checked a second way, against libbtbb - the library
bluey-ox-walker's own decoder is built on, and the one place here that is
somebody else's decoder rather than the specification. It must take a
whitened packet of every type here at its true UAP and CLK6-1 for several clocks,
returning the same header and payload bytes, and must refuse each of them at
the clock one tick off. Skipped where libbtbb is not installed
(``apt install libbtbb1``).

Last, the modulator and the file writer: a burst through ``gfsk`` must come
back bit for bit from a plain discriminator at any fractional delay, a long
run must reach the full deviation, and a short synthesised capture must give
every burst's ``air_bits`` back from the samples alone at the sidecar's
``start_sample``.
"""
import ctypes
import ctypes.util
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from scripts import bt_synth  # noqa: E402

failures = []


def check(name, got, want):
    ok = got == want
    print('  %-52s %s' % (name, 'ok' if ok else 'FAIL'))
    if not ok:
        failures.append(name)
        print('      want %s\n      got  %s' % (want, got))


def bits(s):
    """A string of 0s and 1s, any whitespace ignored, to a list of bits."""
    return [int(c) for c in ''.join(s.split())]


def hex_bits(s):
    """Hex digits read left to right as transmission order, each digit most
    significant bit first - how Part G §2 prints an access code."""
    return [int(b) for c in s for b in format(int(c, 16), '04b')]


#: Part G §8: the output of the whitening register started from 1111111.
WHITENING_ALL_ONES = (
    '11100011101100010100101111101010100001011011110011100101011001100000'
    '110110101110100011001000100000010010011010011110111000011111')

#: Part G §9: data (hex) and its (15,10) codeword, in transmission order.
FEC23 = [
    (0x001, '1000000000 11010'), (0x002, '0100000000 01101'),
    (0x004, '0010000000 11100'), (0x008, '0001000000 01110'),
    (0x010, '0000100000 00111'), (0x020, '0000010000 11001'),
    (0x040, '0000001000 10110'), (0x080, '0000000100 01011'),
    (0x100, '0000000010 11111'), (0x200, '0000000001 10101'),
]

#: Part G §2: the LAP (6 hex digits) and then the 72-bit access code - the
#: preamble, the sync word and the trailer - as 18 hex digits in
#: transmission order.
ACCESS_CODES = [
    '00000057e7041e34000000d5', 'ffffffae758b5227ffffff2a', '9e8b335475c58cc73345e72a',
    '9e8b34528ed3c34cb345e72a', '9e8b36562337b641b345e72a', '9e8b39ac05747b9e7345e72a',
    '9e8b3d57084eab02f345e72a', '9e8b42564c86d2b90b45e72a', '9e8b48ae3c3725e04b45e72a',
    '9e8b4fa8c7216a6bcb45e72a', '9e8b57ab2f16c30fab45e72a', '9e8b60557bd3b22c1b45e72a',
    '9e8b6aad0b6245755b45e72a', '9e8b75a81843a39abb45e72a', '9e8b8150ca96681e0745e72a',
    '9e8b8eaaecd5a5c1c745e72a', '9e8b9c517453fbfce745e72a', '9e8babaf20968adf5745e72a',
    '9e8bbb5015f4a1ef7745e72a', '9e8bccad8c695a00cf45e72a', '9e8bde5614ef043def45e72a',
    '9e8bf1aba81ddc7a3f45e72a', '9e8c05564a7dc4f680c5e72a', '9e8c1a53595c221960c5e72a',
    '9e8c30acb35cc0d830c5e72a', '9e8c47512ac13b3788c5e72a', '9e8c5f52c2f69253e8c5e72a',
    '9e8c7853a351c84078c5e72a', '9e8c9257396d0f3124c5e72a', '9e8cad55b0fdfc46d4c5e72a',
    '9e8cc9aaea2eb38e4cc5e72a', '9e8ce65756dc6bc99cc5e72a', '9e8d045214cf934882c5e72a',
    '9e8d23537568c95b12c5e72a', '9e8d43572281560f0ac5e72a', '9e8d645643260c1c9ac5e72a',
    '9e8d86ae044f493986c5e72a', '9e8da953b8bd917e56c5e72a', '9e8dcdace26edeb6cec5e72a',
    '9e8df2ae6bfe2dc13ec5e72a', '9e8e18a82dcde3dc61c5e72a', '9e8e3fa94c6ab9cff1c5e72a',
    '9e8e67a969059a6799c5e72a', '9e8e90ac4dfccef425c5e72a', '9e8eba53a7fc2c3575c5e72a',
    '9e8ee555798540169dc5e72a', '9e8f1150ae2a363623c5e72a', '9e8f3ead12d8ee71f3c5e72a',
    '9e8f6c5547063a80dbc5e72a', '9e8f9b5063ff6e1367c5e72a', '9e8fcbac9bc5cfef4fc5e72a',
    '9e8ffc52cf00beccffc5e72a', '9e902ea8ec5052f5d025e72a', '9e906151074b15e61825e72a',
    '9e9095a9d59ede62a425e72a', '9e90caaf0be7b2414c25e72a', '9e9100510e10dd0c0225e72a',
    '9e9137af5ad5ac2fb225e72a', '9e916faf7fba8f87da25e72a', '9e91a852f490e5bc5625e72a',
    '9e91e2a9497998291e25e72a', '9e921d526cda4782e125e72a', '9e9259aaacb81dd26925e72a',
    '9e9296abfac7f5bda525e72a', '9e92d4ac9a7b0a7cad25e72a', '9e9313ac142bdde32325e72a',
    '616cec5586a491f0dcda18d5', '616ceb537db2de7b5cda18d5', '616ce957d056ab765cda18d5',
    '616ce6adf61566a99cda18d5', '616ce256fb2fb6351cda18d5', '616cdd5472bf4542ecda18d5',
    '616cd7ac020eb21bacda18d5', '616cd0aaf918fd902cda18d5', '616cc8a9112f54f44cda18d5',
    '616cbf5488b2af1bf4da18d5', '616cb5acf8035842b4da18d5', '616caaa9eb22bead54da18d5',
    '616c9eaa49cb5099e4da18d5', '616c91506f889d4624da18d5', '616c83abf70ec37b04da18d5',
    '616c74aed3f797e8b8da18d5', '616c6451e695bcd898da18d5', '616c53afb250cdfb28da18d5',
    '616c41542ad693c608da18d5', '616c2eaa5b7cc14dd0da18d5', '616c1aa9f9952f7960da18d5',
    '616c05aceab4c99680da18d5', '616befad403dddefdf5a18d5', '616bd85314f8accc6f5a18d5',
    '616bc050fccf05a80f5a18d5', '616ba7525030d577975a18d5', '616b8dadba3037b6c75a18d5',
    '616b7254439ce1713b5a18d5', '616b56a8d4172475ab5a18d5', '616b3956a5bd76fe735a18d5',
    '616b1b5592e8166b635a18d5', '616afc528609d46cfd5a18d5', '616adc551cb8c1f4ed5a18d5',
    '616abb57b047112b755a18d5', '616a9954871271be655a18d5', '616a76524bdc8c49b95a18d5',
    '616a52aedc57494d295a18d5', '616a2daf989f30f6d15a18d5', '616a0750729fd237815a18d5',
    '6169e0a8bf0ba4f81e5a18d5', '6169b8a89a648750765a18d5', '61698f56cea1f673c65a18d5',
    '61696552549d31029a5a18d5', '61693a548ae45d21725a18d5', '61690e57280db315c25a18d5',
    '6168e1ace1b9f3461c5a18d5', '6168b354b46727b7345a18d5', '616884aae0a25694845a18d5',
    '616854aea5fc5814a85a18d5', '616823533c61a3fb105a18d5', '6167f1ac49fb8c563f9a18d5',
    '6167be55a2e0cb45f79a18d5', '61678a5600092571479a18d5', '616755a86314e62eab9a18d5',
    '61671f53defd9bbbe39a18d5', '6166e8abff7e728c5d9a18d5', '6166b0abda115124359a18d5',
    '61667756513b3b1fb99a18d5', '61663dadecd2468af19a18d5', '616602af6542b5fd019a18d5',
    '6165c6adc44b49bd8e9a18d5', '616589542f500eae469a18d5', '61654babf2885e134a9a18d5',
    '61650caec4c69b54c29a18d5',
]

#: Part G §4: UAP, the 10 header bits as hex, the HEC, and the whole 54-bit
#: header in octal, transmission order (each octal digit is one FEC triplet).
HEADERS = [
    (0x00, 0x123, 0xe1, '770007 007070 000777'),
    (0x47, 0x123, 0x06, '770007 007007 700000'),
    (0x00, 0x124, 0x32, '007007 007007 007700'),
    (0x47, 0x124, 0xd5, '007007 007070 707077'),
    (0x00, 0x125, 0x5a, '707007 007007 077070'),
    (0x47, 0x125, 0xbd, '707007 007070 777707'),
    (0x00, 0x126, 0xe2, '077007 007007 000777'),
    (0x47, 0x126, 0x05, '077007 007070 700000'),
    (0x00, 0x127, 0x8a, '777007 007007 070007'),
    (0x47, 0x127, 0x6d, '777007 007070 770770'),
    (0x00, 0x11b, 0x9e, '770770 007007 777007'),
    (0x47, 0x11b, 0x79, '770770 007070 077770'),
    (0x00, 0x11c, 0x4d, '007770 007070 770070'),
    (0x47, 0x11c, 0xaa, '007770 007007 070707'),
    (0x00, 0x11d, 0x25, '707770 007070 700700'),
    (0x47, 0x11d, 0xc2, '707770 007007 000077'),
    (0x00, 0x11e, 0x9d, '077770 007070 777007'),
    (0x47, 0x11e, 0x7a, '077770 007007 077770'),
    (0x00, 0x11f, 0xf5, '777770 007070 707777'),
    (0x47, 0x11f, 0x12, '777770 007007 007000'),
]


def octal_bits(s):
    return [int(b) for c in ''.join(s.split()) for b in format(int(c, 8), '03b')]


def _libbtbb():
    path = ctypes.util.find_library('btbb')
    if not path:
        return None
    lib = ctypes.CDLL(path)
    vp = ctypes.c_void_p
    for name, res, args in (
            ('btbb_init', ctypes.c_int, [ctypes.c_int]),
            ('btbb_packet_new', vp, []),
            ('btbb_packet_unref', None, [vp]),
            ('btbb_packet_set_data', None, [vp, ctypes.POINTER(ctypes.c_char),
                                            ctypes.c_int, ctypes.c_uint8,
                                            ctypes.c_uint32]),
            ('btbb_packet_set_uap', None, [vp, ctypes.c_uint8]),
            ('btbb_packet_set_flag', None, [vp, ctypes.c_int, ctypes.c_int]),
            ('btbb_decode_header', ctypes.c_int, [vp]),
            ('btbb_decode_payload', ctypes.c_int, [vp]),
            ('btbb_packet_get_header_packed', ctypes.c_uint32, [vp]),
            ('btbb_packet_get_payload_length', ctypes.c_int, [vp]),
            ('btbb_get_payload_packed', ctypes.c_int, [vp, ctypes.POINTER(ctypes.c_char)])):
        fn = getattr(lib, name)
        fn.restype, fn.argtypes = res, args
    lib.btbb_init(2)
    return lib


#: libbtbb's flag numbers, from btbb.h.
BTBB_WHITENED, BTBB_CLK6_VALID = 0, 4


def libbtbb_decode(lib, packet, clk6):
    """libbtbb's header and payload for a packet's bits at one CLK6-1.
    Returns ``(header18, payload bytes)``, or None where the HEC or the CRC
    fails."""
    raw = bytes(packet.bits[4:])                 # from the sync word on
    pkt = lib.btbb_packet_new()
    try:
        lib.btbb_packet_set_data(pkt, ctypes.cast(ctypes.c_char_p(raw),
                                 ctypes.POINTER(ctypes.c_char)),
                                 len(raw), 0, (clk6 & 0x3F) << 1)
        lib.btbb_packet_set_uap(pkt, packet.uap)
        lib.btbb_packet_set_flag(pkt, BTBB_CLK6_VALID, 1)
        lib.btbb_packet_set_flag(pkt, BTBB_WHITENED, 1)
        if lib.btbb_decode_header(pkt) != 1:
            return None
        header = lib.btbb_packet_get_header_packed(pkt) & 0x3FFFF
        if not packet.payload_full:              # NULL, POLL: a header only
            return header, b''
        if lib.btbb_decode_payload(pkt) != 10:   # 10 is a CRC pass
            return None
        n = lib.btbb_packet_get_payload_length(pkt)
        buf = (ctypes.c_char * (n + 16))()
        got = lib.btbb_get_payload_packed(pkt, buf)
        return header, bytes(buf[:max(n, got)])
    finally:
        lib.btbb_packet_unref(pkt)


def check_libbtbb():
    print('whitened packets through libbtbb')
    lib = _libbtbb()
    if lib is None:
        print('  libbtbb not installed - skipped')
        return
    for ptype in ('NULL', 'POLL', 'DH1', 'DM1', 'DH3', 'DM3', 'DH5', 'DM5'):
        bad, refused = [], 0
        clk6s = (0x00, 0x01, 0x15, 0x20, 0x2A, 0x3F)
        longest = br.PACKET_TYPES[ptype][4]
        for clk6 in clk6s:
            clk = 0x0123400 | (clk6 << 1)
            p = br.Packet(0x9E8B33, 0x47, clk, ptype,
                          br.seq_body(clk6, longest) if longest else b'')
            if libbtbb_decode(lib, p, clk6) != (p.header18, p.payload_full):
                bad.append('%02x' % clk6)
            off = libbtbb_decode(lib, p, clk6 ^ 1)
            refused += off is None or off != (p.header18, p.payload_full)
        check('%s at CLK6-1 %s' % (ptype, ' '.join('%02x' % c for c in clk6s)),
              bad, [])
        check('%s refused one clock tick off' % ptype, refused, len(clk6s))


def discriminate(iq):
    """Instantaneous frequency in cycles per sample, between n and n + 1."""
    return np.angle(iq[1:] * np.conj(iq[:-1])) / (2 * np.pi)


def check_modulator():
    print('GFSK')
    fs, sps = 20e6, 20
    p = br.Packet(0x9E8B33, 0x47, 0x0123400, 'DH5', br.seq_body(1, 339))
    for delay in (0.0, 0.37, 0.9):
        iq, lead = br.gfsk(p.bits, fs, delay=delay)
        f = discriminate(iq)
        centres = lead + delay + (np.arange(len(p.bits)) + 0.5) * sps - 0.5
        got = (np.interp(centres, np.arange(len(f)), f) > 0).astype(int).tolist()
        check('DH5 back from a discriminator, delay %.2f sample' % delay, got, p.bits)
    iq, lead = br.gfsk([1] * 32, fs)
    peak = discriminate(iq)[lead + 10 * sps] * fs
    check('a run of ones at +h/2 symbol rates (160 kHz)',
          round(peak / 1e3, 1), round(br.GFSK_H / 2 * br.SYMBOL_RATE / 1e3, 1))

    print('a synthesised capture')
    iq, side = bt_synth.synthesise(bursts=3, snr_db=40, timing_frac=0.25)
    off = (side['channel_mhz'] - side['center_mhz']) * 1e6
    n = np.arange(len(iq))
    f = discriminate(iq * np.exp(-2j * np.pi * off * n / side['sample_rate']))
    # 40 dB in 1 MHz is still noise across 20 MHz: average each bit's middle.
    ok = []
    for b in side['bursts']:
        want = [int(c) for c in b['air_bits']]
        start = b['start_sample'] + side['timing_frac']
        mids = (start + (np.arange(len(want)) + 0.5) * sps).astype(int)
        got = [int(f[m - 5:m + 5].sum() > 0) for m in mids]
        ok.append(got == want)
    check('3 DH5s, air_bits back at start_sample', ok, [True] * 3)
    check('bursts on master slots, half a slot in',
          [(b['clk'] % 4, b['start_sample'] % side['slot_samples'])
           for b in side['bursts']], [(0, side['slot_samples'] // 2)] * 3)
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'x.cf32')
        iq.astype('<c8').tofile(path)
        check('cf32 on disk is interleaved little-endian float32',
              np.array_equal(np.fromfile(path, '<f4')[:4],
                             np.array([iq[0].real, iq[0].imag,
                                       iq[1].real, iq[1].imag], '<f4')), True)


def main():
    print('whitening (Part G section 8)')
    # An all-ones register is CLK6-1 = 111111, i.e. clk = 0b1111110.
    check('128 bits from 1111111', br.whitening(0x7E, 128), bits(WHITENING_ALL_ONES))

    print('FEC 2/3 (Part G section 9)')
    for data, word in FEC23:
        check('data 0x%03x' % data, br.fec23(br.lsb_bits(data, 10)), bits(word))

    print('access codes (Part G section 2)')
    bad = [row[:6] for row in ACCESS_CODES
           if br.access_code(int(row[:6], 16)) != hex_bits(row[6:])]
    check('%d LAPs, preamble + sync word + trailer' % len(ACCESS_CODES), bad, [])
    check('no trailer: the 68-bit ID packet form',
          br.access_code(0x9E8B33, trailer=False),
          hex_bits(ACCESS_CODES[2][6:])[:68])

    print('HEC and packet header (Part G section 4)')
    for uap, data, hec, header in HEADERS:
        ten = br.lsb_bits(data, 10)
        got = br.hec(ten, uap)
        # Part G writes the HEC as a number whose least significant bit goes
        # on air first, the same as every other field.
        check('UAP 0x%02x data 0x%03x: HEC 0x%02x' % (uap, data, hec), got,
              br.lsb_bits(hec, 8))
        check('UAP 0x%02x data 0x%03x: 54-bit header' % (uap, data),
              br.fec13(ten + got), octal_bits(header))

    print('CRC (Part G section 5)')
    data = bytes([0x4e, 1, 2, 3, 4, 5, 6, 7, 8, 9])
    # "Over the air each byte in the codeword is sent with the LSB first",
    # and the CRC goes as the two bytes 6d d2.
    check('10 bytes, UAP 0x47: CRC 6d d2', br.crc16(br.bytes_bits(data), 0x47),
          br.bytes_bits([0x6d, 0xd2]))

    print('complete packets (Part G section 6), UAP 0x47, no whitening')
    body = bytes([1, 2, 3, 4, 5])
    dh1 = br.Packet(lap=0, uap=0x47, clk=0, ptype='DH1', body=body, lt_addr=3,
                    flow=0, arqn=1, seqn=0, whiten=False)
    check('DH1 header', dh1.bits[72:126], bits(
        '111111000 000000111000 000111000 000111111000000000000000'))
    check('DH1 payload', dh1.bits[126:], bits(
        '01110100 10000000 01000000 11000000 00100000 10100000'
        '1110110000110110'))
    dm1 = br.Packet(lap=0, uap=0x47, clk=0, ptype='DM1', body=body, lt_addr=3,
                    flow=0, arqn=1, seqn=0, whiten=False)
    check('DM1 header', dm1.bits[72:126], bits(
        '111111000 111111000000 000111000 111000000111111111111000'))
    check('DM1 payload, FEC 2/3 and 6 padding zeros', dm1.bits[126:], bits(
        '0111010010 11001  0000000100 01011  0000110000 11110  0000100000 00111'
        '1010000011 01100  1011000011 00010  0110000000 10001'))

    print('header-only packets')
    for ptype in ('NULL', 'POLL'):
        p = br.Packet(0x9E8B33, 0x47, 0x0123400, ptype)
        check('%s is 126 bits, no payload (section 6.5.1)' % ptype,
              (len(p.bits), p.payload_full), (126, b''))

    print('whitened multi-slot packets')
    for ptype, slots in (('DH3', 3), ('DH5', 5)):
        longest = br.PACKET_TYPES[ptype][4]
        p = br.Packet(lap=0x9E8B33, uap=0x47, clk=0x1234567 & ~1, ptype=ptype,
                      body=br.seq_body(7, longest))
        plain = br.Packet(lap=0x9E8B33, uap=0x47, clk=0x1234567 & ~1,
                          ptype=ptype, body=br.seq_body(7, longest), whiten=False)
        # 72 + 54 + 8 * (2 + body + 2) bits, which must fit the slots the
        # packet "may occupy" (section 6.5.4).
        check('%s of %d bytes: %d bits' % (ptype, longest, 72 + 54 + 8 * (longest + 4)),
              len(p.bits), 72 + 54 + 8 * (longest + 4))
        check('%s fits %d slots' % (ptype, slots),
              p.duration_us <= slots * br.SLOT_US, True)
        check('%s whitening changed header and payload' % ptype,
              p.bits[:72] == plain.bits[:72] and p.bits[72:] != plain.bits[72:], True)
        check('%s header18 is the unwhitened header' % ptype,
              br.lsb_bits(p.header18, 18)[:10],
              br.lsb_bits(1, 3) + br.lsb_bits(br.PACKET_TYPES[ptype][0], 4) + [1, 0, 0])

    check_libbtbb()
    check_modulator()

    print('\nRESULT: %s' % ('FAIL (%d)' % len(failures) if failures else 'PASS'))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
