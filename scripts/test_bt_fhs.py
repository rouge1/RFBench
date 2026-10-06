#!/usr/bin/env python3
"""Hold the Bluetooth FHS encoder to libbtbb and to a golden packet.

    python scripts/test_bt_fhs.py

``apps/bt_fhs.py`` builds the FHS packet a page or an inquiry response is
made of, and bluey-ox-walker validates its clock against the clock inside
it, so a wrong bit is a wrong test of the receiver. Nothing here trusts a
decoder of our own. The references are:

* **libbtbb**, somebody else's decoder. It decodes an FHS in full: the
  header (HEC against a UAP the caller gives), the payload through FEC 2/3
  and the whitening, and the CRC-16, whose result it returns as 1000 for a
  good packet and 0 for a bad one. It also reads LAP, UAP, NAP and CLK27-2
  out of the payload itself (``lap_from_fhs`` and its siblings); what it does
  not read - class of device, AM_ADDR, SR, page scan mode - comes back from
  its de-whitened, de-FEC'd, CRC-checked bytes through the one field splitter
  of ``bt_fhs``, which the round trip below holds to the encoder.
* the packet's **structure** (366 = 72 + 54 + 240 bits, parity bits equal to
  the sync word's, ranges refused, whitening a function of CLK6-1 only);
* a **golden fixture**, ``/tmp/sdr-p7/fhs_golden.json``, an FHS from
  bluey-ox-walker's own public test, when one has been supplied;
* a second, **independent construction** of the packet written out here
  from ``bt_br_frame``'s primitives, so that the mutants below (each a
  single deliberate mistake) can be told apart from the real thing, and
  each is reported with the checks that catch it.

libbtbb's FHS decode does not verify the 34 parity bits against the LAP, so
that is checked here against ``bt_br_frame.sync_word`` (itself held to the
specification's sample data by ``test_bt_br_frame.py``).
"""
import ctypes
import json
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from apps import bt_fhs  # noqa: E402
from scripts import bt_ota_check  # noqa: E402

GOLDEN = '/tmp/sdr-p7/fhs_golden.json'
failures = []


def check(name, got, want):
    ok = got == want
    print('  %-62s %s' % (name, 'ok' if ok else 'FAIL'))
    if not ok:
        failures.append(name)
        print('      want %s\n      got  %s' % (want, got))


def raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except ValueError:
        return True
    return False


# --- libbtbb ------------------------------------------------------------------

_lib = bt_ota_check.libbtbb()
if _lib is not None:
    vp = ctypes.c_void_p
    for _n, _r, _a in (
            ('btbb_packet_get_type', ctypes.c_uint8, [vp]),
            ('btbb_packet_get_payload_length', ctypes.c_int, [vp]),
            ('btbb_packet_get_flag', ctypes.c_int, [vp, ctypes.c_int]),
            ('lap_from_fhs', ctypes.c_uint32, [vp]),
            ('uap_from_fhs', ctypes.c_uint8, [vp]),
            ('nap_from_fhs', ctypes.c_uint16, [vp]),
            ('clock_from_fhs', ctypes.c_uint32, [vp])):
        _f = getattr(_lib, _n)
        _f.restype, _f.argtypes = _r, _a


def btbb_decode(bits, uap, clk):
    """What libbtbb makes of an FHS's air bits (access code included) taken
    with a given UAP and clock: None if it refuses the header, else a dict of
    its payload verdict (1000 good CRC, 0 bad), the payload bytes and the
    fields it reads itself. The packet gets the clock's CLK6-1 only, as a
    sniffer would."""
    raw = bytes(int(b) for b in bits[4:])
    pkt = _lib.btbb_packet_new()
    try:
        _lib.btbb_packet_set_data(
            pkt, ctypes.cast(ctypes.c_char_p(raw), ctypes.POINTER(ctypes.c_char)),
            len(raw), 0, ((clk >> 1) & 0x3F) << 1)
        _lib.btbb_packet_set_uap(pkt, uap)
        _lib.btbb_packet_set_flag(pkt, 4, 1)          # CLK6 valid
        _lib.btbb_packet_set_flag(pkt, 0, 1)          # whitened
        if _lib.btbb_decode_header(pkt) != 1:
            return None
        out = {'header18': _lib.btbb_packet_get_header_packed(pkt) & 0x3FFFF,
               'type': _lib.btbb_packet_get_type(pkt)}
        out['verdict'] = _lib.btbb_decode_payload(pkt)
        buf = (ctypes.c_char * 64)()
        out['payload'] = bytes(buf[:0])
        if out['verdict'] == 1000:
            _lib.btbb_get_payload_packed(pkt, buf)
            out['payload'] = bytes(buf[:20])
            out['lap'] = _lib.lap_from_fhs(pkt)
            out['uap'] = _lib.uap_from_fhs(pkt)
            out['nap'] = _lib.nap_from_fhs(pkt)
            out['clk27_2'] = _lib.clock_from_fhs(pkt)
        return out
    finally:
        _lib.btbb_packet_unref(pkt)


def random_fhs(rng, style):
    """Random fields in a page's style (access code of the paged device,
    HEC and CRC from its UAP, which is not the sender's) or an inquiry
    response's (the GIAC, and the DCI, 0)."""
    f = dict(lap=rng.randrange(1 << 24), uap=rng.randrange(256),
             nap=rng.randrange(1 << 16), cod=rng.randrange(1 << 24),
             am_addr=rng.randrange(8), clk=rng.randrange(1 << 28),
             sr=rng.randrange(4), previously_used=rng.randrange(8),
             sp=rng.randrange(4), eir=rng.randrange(2),
             reserved=rng.randrange(2),
             lt_addr=rng.randrange(8), arqn=rng.randrange(2),
             seqn=rng.randrange(2), flow=rng.randrange(2))
    if style == 'page':
        f['access_lap'] = rng.randrange(1 << 24)
        f['header_uap'] = rng.randrange(256)
    else:
        f['access_lap'] = bt_fhs.GIAC
        f['header_uap'] = bt_fhs.DCI
        f['am_addr'] = 0                  # Table 6.3: all-zero in a response
        f['lt_addr'] = 0
    return f


def make(f):
    return bt_fhs.FHS(f['access_lap'], f['lap'], f['uap'], f['nap'], f['cod'],
                      f['am_addr'], f['clk'], lt_addr=f['lt_addr'],
                      flow=f['flow'], arqn=f['arqn'], seqn=f['seqn'],
                      header_uap=f['header_uap'], sr=f['sr'], sp=f['sp'],
                      eir=f['eir'], reserved=f['reserved'],
                      previously_used=f['previously_used'])


FIELD_KEYS = ('lap', 'uap', 'nap', 'cod', 'am_addr', 'sr', 'sp', 'eir', 'reserved',
              'previously_used')


def check_libbtbb():
    print('libbtbb')
    if _lib is None:
        print('  skip: libbtbb is not installed (apt install libbtbb1)')
        return
    rng = random.Random(0xF45)
    bad = collections = 0
    problems = []
    for i in range(200):
        f = random_fhs(rng, 'page' if i % 2 == 0 else 'inquiry')
        p = make(f)
        r = btbb_decode(p.bits, f['header_uap'], p.tx_clk)
        if r is None or r['verdict'] != 1000:
            problems.append((i, 'header/CRC refused', f))
            continue
        pf = bt_fhs.parse_fhs_payload(br.bytes_bits(r['payload'])[:144])
        errs = [k for k in FIELD_KEYS if pf[k] != f[k]]
        errs += ['clk27_2'] if pf['clk27_2'] != f['clk'] >> 2 else []
        errs += ['parity'] if not pf['parity_ok'] else []
        errs += ['header18'] if r['header18'] != p.header18 else []
        errs += ['type'] if r['type'] != 2 else []
        errs += ['payload bytes'] if r['payload'] != p.payload_full else []
        # what libbtbb itself reads out of the payload
        errs += [k for k in ('lap', 'uap', 'nap') if r[k] != f[k]]
        errs += ['btbb clk27_2'] if r['clk27_2'] != f['clk'] >> 2 else []
        if errs:
            problems.append((i, errs, f))
    check('200 random FHS (100 page, 100 inquiry): all fields, CRC, header',
          problems, [])

    # the negative controls, on every packet of a smaller set
    rng = random.Random(7)
    wrong_uap = wrong_clk = wrong_crc = 0
    n = 100
    for i in range(n):
        f = random_fhs(rng, 'page' if i % 2 == 0 else 'inquiry')
        p = make(f)
        wrong_uap += btbb_decode(p.bits, f['header_uap'] ^ (1 << rng.randrange(8)),
                                 p.tx_clk) is None
        # a clock that differs in one of CLK6-1 (bit 1..6) whitens differently
        r = btbb_decode(p.bits, f['header_uap'], p.tx_clk ^ (2 << rng.randrange(6)))
        wrong_clk += r is None or r['verdict'] != 1000
    check('wrong UAP: libbtbb refuses the header, %d of %d' % (wrong_uap, n),
          wrong_uap, n)
    check('wrong clock for whitening: refused, %d of %d' % (wrong_clk, n),
          wrong_clk, n)

    # a CRC made with another UAP than the HEC's: header passes, payload fails
    orig = br.crc16
    try:
        br.crc16 = lambda b, u: orig(b, u ^ 0x10)
        g = make(random_fhs(random.Random(3), 'page'))
    finally:
        br.crc16 = orig
    f = random_fhs(random.Random(3), 'page')
    r = btbb_decode(g.bits, f['header_uap'], g.tx_clk)
    check('CRC with the wrong UAP: header good, payload CRC rejected',
          (r is not None, r and r['verdict']), (True, 0))


# --- structure ----------------------------------------------------------------


def check_structure():
    print('structure')
    rng = random.Random(1)
    f = random_fhs(rng, 'page')
    p = make(f)
    check('air bits 366 = 72 + 54 + 240', len(p.bits), 72 + 54 + 240)
    check('payload before CRC is 144 bits',
          len(bt_fhs.fhs_payload_bits(f['lap'], f['uap'], f['nap'], f['cod'],
                                      f['am_addr'], f['clk'] >> 2)), 144)
    check('access code is the access_lap\'s', p.bits[:72],
          br.access_code(f['access_lap']))
    check('240 payload air bits are 16 blocks of 15', (len(p.bits) - 126) % 15, 0)

    ok = True
    for _ in range(100):
        f = random_fhs(rng, rng.choice(['page', 'inquiry']))
        pb = bt_fhs.fhs_payload_bits(f['lap'], f['uap'], f['nap'], f['cod'],
                                     f['am_addr'], f['clk'] >> 2, f['sr'],
                                     f['sp'], f['eir'], f['reserved'],
                                     f['previously_used'])
        got = bt_fhs.parse_fhs_payload(pb)
        want = {k: f[k] for k in FIELD_KEYS}
        want['clk27_2'] = f['clk'] >> 2
        ok &= all(got[k] == v for k, v in want.items()) and got['parity_ok']
        ok &= pb[:34] == br.sync_word(f['lap'])[:34]
        air = make(f)
        d = bt_fhs.parse_fhs_air_bits(air.bits, f['header_uap'], air.tx_clk)
        ok &= (d['crc_ok'] and d['header_ok'] and d['type'] == 2
               and all(d[k] == v for k, v in want.items())
               and d['lt_addr'] == f['lt_addr'] and d['arqn'] == f['arqn']
               and d['seqn'] == f['seqn'] and d['flow'] == f['flow'])
    check('100 random: payload and air round trip, 34 parity bits = sync word',
          ok, True)

    # field positions, bit by bit, from a payload of one field at a time
    pb = bt_fhs.fhs_payload_bits(0, 0, 0, 0, 0, 0, sp=0)
    base = br.sync_word(0)[:34]
    check('LAP 0, all else 0: 34 sync-word parity bits then zeros (SP=0)',
          pb, base + [0] * 110)
    pb = bt_fhs.fhs_payload_bits(0, 0, 0, 0, 0, 0, sp=0b10)
    check('SP 0b10 (the default): SP1 only, bit 63 (SP0 is 62)',
          [i for i, b in enumerate(pb) if b and i >= 34], [63])
    check('the default is SP = 0b10',
          bt_fhs.fhs_payload_bits(0, 0, 0, 0, 0, 0), pb)
    pb = bt_fhs.fhs_payload_bits(0, 0, 0, 0, 0, 0, sp=0, eir=1)
    check('EIR is bit 58', [i for i, b in enumerate(pb) if b and i >= 34], [58])
    pb = bt_fhs.fhs_payload_bits(0, 0, 0, 0, 0, 0, sp=0, sr=1)
    check('SR LSB is bit 60', [i for i, b in enumerate(pb) if b and i >= 34], [60])
    pb = bt_fhs.fhs_payload_bits(0, 1, 0, 0, 0, 0, sp=0)
    check('UAP LSB is payload bit 34+24+2+2+2 = 64',
          [i for i, b in enumerate(pb) if b and i >= 34], [64])
    pb = bt_fhs.fhs_payload_bits(0, 0, 0, 0, 0, 1, sp=0)
    check('CLK2 (clk27_2 bit 0) is bit 34+24+6+8+16+24+3 = 115',
          [i for i, b in enumerate(pb) if b and i >= 34], [115])
    pb = bt_fhs.fhs_payload_bits(0, 0, 0, 0, 0, 0, sp=0, previously_used=1)
    check('page scan mode LSB is bit 141',
          [i for i, b in enumerate(pb) if b and i >= 34], [141])
    pb = bt_fhs.fhs_payload_bits(0, 0, 0, 0, 1, 0, sp=0)
    check('AM_ADDR LSB is bit 112',
          [i for i, b in enumerate(pb) if b and i >= 34], [112])

    check('clk >= 2**28 refused', raises(bt_fhs.FHS, 1, 2, 3, 4, 5, 6, 1 << 28), True)
    check('clk27_2 >= 2**26 refused',
          raises(bt_fhs.fhs_payload_bits, 1, 2, 3, 4, 5, 1 << 26), True)
    check('2**28 - 1 accepted',
          bt_fhs.FHS(1, 2, 3, 4, 5, 6, (1 << 28) - 1).clk27_2, (1 << 26) - 1)
    for name, args in (('lap', dict(lap=1 << 24)), ('uap', dict(uap=256)),
                       ('nap', dict(nap=1 << 16)), ('cod', dict(cod=1 << 24)),
                       ('am_addr', dict(am_addr=8)), ('sr', dict(sr=4)),
                       ('previously_used', dict(previously_used=8)),
                       ('uap -1', dict(uap=-1))):
        a = dict(lap=1, uap=2, nap=3, cod=4, am_addr=5, clk27_2=6)
        a.update(args)
        check('%s out of range refused' % name,
              raises(bt_fhs.fhs_payload_bits, **a), True)
    check('access_lap, header_uap, tx_clk out of range refused',
          [raises(bt_fhs.FHS, 1 << 24, 1, 2, 3, 4, 5, 6),
           raises(bt_fhs.FHS, 1, 1, 2, 3, 4, 5, 6, header_uap=256),
           raises(bt_fhs.FHS, 1, 1, 2, 3, 4, 5, 6, tx_clk=1 << 28)],
          [True] * 3)

    # whitening is a function of CLK6-1 only: clocks equal in bits 6..1 give
    # equal air bits, whatever bit 0 and bits 7..27 hold. (clk27_2, in the
    # payload, is held fixed; it is the tx_clk that moves.)
    f = random_fhs(rng, 'page')
    a = bt_fhs.FHS(f['access_lap'], f['lap'], f['uap'], f['nap'], f['cod'],
                   f['am_addr'], 0x0123456, tx_clk=0x00000A4)
    same = [bt_fhs.FHS(f['access_lap'], f['lap'], f['uap'], f['nap'], f['cod'],
                       f['am_addr'], 0x0123456, tx_clk=c).bits == a.bits
            for c in (0x00000A5, 0x00000A4 | 0x80, 0x0FFFF00 | 0xA4,
                      0x8000000 | 0xA5)]
    check('tx_clk equal in bits 6..1: identical air bits', same, [True] * 4)
    diff = [bt_fhs.FHS(f['access_lap'], f['lap'], f['uap'], f['nap'], f['cod'],
                       f['am_addr'], 0x0123456, tx_clk=0xA4 ^ (2 << k)).bits
            != a.bits for k in range(6)]
    check('flip any of CLK6-1: different air bits', diff, [True] * 6)
    check('only the payload carries clk27_2: tx_clk alone leaves bits 0..71 alone',
          bt_fhs.FHS(1, 2, 3, 4, 5, 6, 0x400, tx_clk=0x0).bits[:72],
          bt_fhs.FHS(1, 2, 3, 4, 5, 6, 0x400, tx_clk=0x7E).bits[:72])
    check('tx_clk defaults to clk',
          bt_fhs.FHS(1, 2, 3, 4, 5, 6, 0x123454).bits,
          bt_fhs.FHS(1, 2, 3, 4, 5, 6, 0x123454, tx_clk=0x123454).bits)

    check('id_bits: 68 bits, preamble and sync word, no trailer',
          (len(bt_fhs.id_bits(0x9E8B33)),
           bt_fhs.id_bits(0x9E8B33) == br.access_code(0x9E8B33)[:68]), (68, True))

    # FEC 2/3 of the payload corrects one bit per 15-bit block
    f = random_fhs(rng, 'page')
    p = make(f)
    ok = True
    for blk in range(16):
        bits = list(p.bits)
        k = 126 + blk * 15 + rng.randrange(15)
        bits[k] ^= 1
        d = bt_fhs.parse_fhs_air_bits(bits, f['header_uap'], p.tx_clk)
        ok &= d['crc_ok'] and d['lap'] == f['lap'] and d['cod'] == f['cod']
    bits = list(p.bits)
    for blk in range(16):                            # one error in every block
        bits[126 + blk * 15 + rng.randrange(15)] ^= 1
    d = bt_fhs.parse_fhs_air_bits(bits, f['header_uap'], p.tx_clk)
    check('one bit error in a 15-bit block, each block in turn, corrected',
          ok, True)
    check('one error in every block at once, all corrected', d['crc_ok'], True)
    bits = list(p.bits)
    bits[126] ^= 1
    bits[127] ^= 1
    check('two errors in one block are not silently accepted',
          raises(bt_fhs.parse_fhs_air_bits, bits, f['header_uap'], p.tx_clk)
          or not bt_fhs.parse_fhs_air_bits(bits, f['header_uap'], p.tx_clk)['crc_ok'],
          True)

    s = p.sidecar()
    check('sidecar fields', (s['ptype'], s['fhs_clk'], s['clk27_2'], s['tx_clk'],
                             s['clk'], s['header_uap'], 'air_bits' in s),
          ('FHS', f['clk'], f['clk'] >> 2, f['clk'], f['clk'], f['header_uap'],
           False))


def check_helpers():
    print('inquiry response and page response helpers (spec text, libbtbb)')
    q = bt_fhs.inquiry_response(0x123456, 0x47, 0xABCD, 0x5A020C, 0x0123454, 9)
    d = bt_fhs.parse_fhs_air_bits(q.bits, 0x00, q.tx_clk)
    check('inquiry response: GIAC, DCI for HEC and CRC, zero LT_ADDRs, SP=0b10',
          (q.bits[:72] == br.access_code(0x9E8B33), d['crc_ok'], d['header_ok'],
           d['lt_addr'], d['am_addr'], d['sp'], d['lap']),
          (True, True, True, 0, 0, 2, 0x123456))
    if _lib is not None:
        r = btbb_decode(q.bits, 0x00, q.tx_clk)
        check('inquiry response: libbtbb takes it at the DCI, refuses the sender\'s UAP',
              (r['verdict'], btbb_decode(q.bits, 0x47, q.tx_clk)), (1000, None))
    g = bt_fhs.page_response(0x6B1D3C, 0x47, 0x112233, 0x55, 0x1234, 0x5A020C, 1,
                             0x155AA55 << 2, 21)
    d = bt_fhs.parse_fhs_air_bits(g.bits, 0x47, g.tx_clk)
    check('page response: paged UAP for HEC and CRC, header LT_ADDR 0, payload 1',
          (d['crc_ok'], d['header_ok'], d['lt_addr'], d['am_addr'], d['uap']),
          (True, True, 0, 1, 0x55))
    if _lib is not None:
        check('page response: libbtbb good at the paged UAP, refused at the central\'s',
              (btbb_decode(g.bits, 0x47, g.tx_clk)['verdict'],
               btbb_decode(g.bits, 0x55, g.tx_clk)), (1000, None))


# --- the X-input whitening of a response (section 7.2) -------------------------


def lfsr_independent(x, n):
    """The whitening sequence from the register [X0 X1 X2 X3 X4 1 1] (7.2),
    written out here: g(D) = D^7 + D^4 + 1, output from position 6."""
    r = [(x >> i) & 1 for i in range(5)] + [1, 1]
    out = []
    for _ in range(n):
        o = r[6]
        out.append(o)
        r = [o, r[0], r[1], r[2], r[3] ^ o, r[4], r[5]]
    return out


def xprc_independent(clke, koff, nudge, n):
    """EQ 6 from bit strings: CLKE16-12 as a number, CLKE4-2,0 as the bits
    4, 3, 2, 0 read as a binary string."""
    b = format(clke, '028b')[::-1]                    # b[i] is bit i
    c16_12 = int(b[16] + b[15] + b[14] + b[13] + b[12], 2)
    c4_2_0 = int(b[4] + b[3] + b[2] + b[0], 2)
    return (c16_12 + koff + nudge + ((c4_2_0 - c16_12) % 16) + n) % 32


def check_x_whitening():
    print('whitening from the X-input (7.2, EQ 6, EQ 8)')
    rng = random.Random(11)
    reg = all(bt_fhs.whitening_register(x) == ''.join(str(b) for b in ([(x >> i) & 1 for i in range(5)] + [1, 1]))
              and br.whitening(bt_fhs.whitening_clk(x), 40) == lfsr_independent(x, 40) for x in range(32))
    check('register [X0..X4, 1, 1]: bt_br_frame.whitening at (X|0x20)<<1 is the independent LFSR, all 32 X', reg, True)
    check('implied CLK6-1 is 32 + X', [(bt_fhs.whitening_clk(x) >> 1) & 0x3F for x in (0, 5, 31)], [32, 37, 63])
    ok = all(bt_fhs.xprc(c, k, n, N) == xprc_independent(c, k, n, N) for c, k, n, N in
             ((rng.randrange(1 << 28), rng.choice((24, 8)), rng.choice((0, 2, 4)), rng.randrange(1, 40)) for _ in range(2000)))
    check('xprc equals an independent bit-string computation of EQ 6 (2000 random)', ok, True)
    check('xir is (CLKN16-12 + N) mod 32', [bt_fhs.xir(0x1F000, 3), bt_fhs.xir(0x2000, 0), bt_fhs.xir(0x1000, 31)], [2, 2, 0])
    check('xprc, a worked case: CLKE = 0x0001B2D4, A-train, nudge 0, N 1',
          bt_fhs.xprc(0x0001B2D4, 24, 0, 1), xprc_independent(0x0001B2D4, 24, 0, 1))
    check('whitening_x and tx_clk together refused; X out of range refused',
          [raises(bt_fhs.FHS, 1, 2, 3, 4, 5, 6, 0x40, tx_clk=4, whitening_x=3),
           raises(bt_fhs.FHS, 1, 2, 3, 4, 5, 6, 0x40, whitening_x=32)], [True, True])
    try:
        bt_fhs.page_response(1, 2, 3, 4, 5, 6, 1, 0x40)
        req = False
    except TypeError:
        req = True
    check('page_response without whitening_x is a TypeError', req, True)
    if _lib is None:
        return
    diff_acc = diff_ref = good = 0
    for i in range(60):
        x = rng.randrange(32)
        clk = rng.randrange(1 << 26) << 2
        if i % 2:
            p = bt_fhs.page_response(0x6B1D3C, 0x47, 0x112233, 0x55, 0x1234, 0x5A020C, 1, clk, x)
            hu = 0x47
        else:
            p = bt_fhs.inquiry_response(0x2A9F31, 0x12, 0x3456, 0x5A020C, clk, x)
            hu = 0
        r = btbb_decode(p.bits, hu, bt_fhs.whitening_clk(x))
        good += bool(r and r['verdict'] == 1000 and r['clk27_2'] == clk >> 2)
        own = btbb_decode(p.bits, hu, clk)             # the plain CLK6-1 of the FHS's own clock
        if ((clk >> 1) & 0x3F) != 32 + x:
            diff_acc += 1
            diff_ref += own is None or own['verdict'] != 1000
        d = bt_fhs.parse_fhs_air_bits(p.bits, hu, whitening_x=x)
        good += bool(d['crc_ok'] and d['header_ok'])
    check('libbtbb and the parser accept 60 responses at tx_clk = (X|0x20)<<1: %d of 120' % good, good, 120)
    check('and libbtbb refuses them at the plain CLK6-1 of the FHS\'s own clock where it differs (%d of %d)'
          % (diff_ref, diff_acc), diff_ref, diff_acc)
    p = bt_fhs.page_response(0x6B1D3C, 0x47, 0x112233, 0x55, 0x1234, 0x5A020C, 1, 0x155AA54, 7)
    wrong = btbb_decode(p.bits, 0x47, (7 << 1))         # X without its two MSBs
    check('X written without the two MSBs of 1 does not decode', wrong is None or wrong['verdict'] != 1000, True)


# --- the golden fixture -------------------------------------------------------


def check_golden():
    """The fixture is SYNTHETIC, written by the receiver's own side, so it
    shows the machinery (parity bits, CRC, whitening, FEC, header) agrees
    with the receiver's, not that either is right: libbtbb and the
    specification are the independent checks. It leaves SP, EIR and the
    reserved bits at 0, which the specification does not (SP = 0b10), so it
    is reproduced with ``sp=0`` and the default is checked beside it.

    It whitens from CLK6-1 = 17 (``whitening_x=None``), which is not what a
    response does (7.2: the X-input), so this stage is a MACHINERY CHECK of
    parity, CRC, FEC, header and the ordinary whitening seeding; the rule for
    a response is held by ``check_x_whitening``."""
    print('golden fixture')
    if not os.path.exists(GOLDEN):
        print('  skip: no golden fixture (%s)' % GOLDEN)
        return
    with open(GOLDEN) as fh:
        g = json.load(fh)
    fl, st = g['fields'], g['stages']
    lap, uap, nap = int(fl['master_lap'], 16), int(fl['master_uap'], 16), \
        int(fl['master_nap'], 16)
    cod, am, c27 = int(fl['class_of_device'], 16), fl['am_addr'], \
        int(fl['clk27_2'], 16)
    acc = int(g['access_code']['lap'], 16)
    huap = int(g['access_code']['uap_for_hec_and_crc'], 16)
    tx_clk = g['whitening']['clk6'] << 1          # CLK6-1 = 17 -> clk bits 6..1
    p = bt_fhs.FHS(acc, lap, uap, nap, cod, am, c27 << 2, tx_clk, header_uap=huap,
                   sp=0)
    pay = bt_fhs.fhs_payload_bits(lap, uap, nap, cod, am, c27, sp=0)
    check('golden: 144 payload bits before the CRC', pay,
          st['payload144_before_crc_whitening_fec'])
    crc = br.crc16(pay, huap)
    check('golden: CRC-16 value (bits as an int, either end first)',
          sorted([br.bits_int(crc), br.bits_int(crc[::-1])])
          .count(st['crc16_over_payload_bytes_with_paged_uap']) >= 1, True)
    check('golden: 160 bits with the CRC', pay + crc,
          st['payload160_with_crc_before_whitening'])
    hdr = br.packet_header(0, 2, 0, 0, 0, huap)
    check('golden: 18 header bits before whitening', hdr,
          st['header18_before_whitening'])
    check('golden: HEC byte (bits as an int)', br.bits_int(hdr[10:]),
          st['hec_byte'])
    w = br.whitening(tx_clk, 178)
    hw = [b ^ x for b, x in zip(hdr, w)]
    check('golden: header whitened', hw, st['header18_whitened'])
    pw = [b ^ x for b, x in zip(pay + crc, w[18:])]
    check('golden: payload whitened', pw, st['payload160_whitened'])
    check('golden: payload FEC 2/3', br.fec23(pw), st['payload240_fec23'])
    check('golden: header FEC 1/3', br.fec13(hw), st['header54_fec13'])
    check('golden: access code for the paged LAP', br.access_code(acc),
          st['access_code72_paged_lap'])
    check('golden: all 366 air bits, bit for bit', p.bits,
          st['full_air_stream_366'])
    d = bt_fhs.parse_fhs_air_bits(st['full_air_stream_366'], huap, tx_clk)
    check('golden: parser reads the fields and the checks hold',
          (d['lap'], d['uap'], d['nap'], d['cod'], d['am_addr'], d['clk27_2'],
           d['crc_ok'], d['header_ok'], d['parity_ok'], d['sp']),
          (lap, uap, nap, cod, am, c27, True, True, True, 0))
    if _lib is not None:
        r = btbb_decode(st['full_air_stream_366'], huap, tx_clk)
        check('golden: libbtbb accepts it (header, CRC) and reads LAP/UAP/NAP/clock',
              r and (r['verdict'], r['lap'], r['uap'], r['nap'], r['clk27_2']),
              (1000, lap, uap, nap, c27))
    q = bt_fhs.FHS(acc, lap, uap, nap, cod, am, c27 << 2, tx_clk, header_uap=huap)
    diffs = [i for i, (a, b) in enumerate(zip(p.bits, q.bits)) if a != b]
    check('default SP=0b10 differs from the fixture in the payload only',
          bool(diffs) and min(diffs) >= 126, True)
    check('default packet is SP=0b10 and its CRC and FEC are consistent',
          bt_fhs.parse_fhs_air_bits(q.bits, huap, tx_clk)['sp'], 2)


# --- modulated ----------------------------------------------------------------


def check_modulated():
    print('modulated')
    fs, sps = 40e6, 40
    rng = random.Random(5)
    for style in ('page', 'inquiry'):
        p = make(random_fhs(rng, style))
        for delay in (0.0, 0.41):
            iq, lead = br.gfsk(p.bits, fs, delay=delay)
            noise = np.random.default_rng(1).normal(size=(2, len(iq))) * 0.002
            iq = iq + (noise[0] + 1j * noise[1]).astype(np.complex64)
            f = np.angle(iq[1:] * np.conj(iq[:-1]))
            centres = (lead + delay + (np.arange(len(p.bits)) + 0.5) * sps - 0.5)
            got = (np.interp(centres, np.arange(len(f)), f) > 0).astype(int).tolist()
            check('%s FHS through gfsk and a discriminator, delay %.2f'
                  % (style, delay), got, p.bits)


# --- mutants ------------------------------------------------------------------


def build(f, mut=None):
    """A second construction of the packet, from ``bt_br_frame``'s primitives
    and the field table of the specification, with one deliberate mistake."""
    lap, uap, nap, cod = f['lap'], f['uap'], f['nap'], f['cod']
    huap = f['header_uap']
    clk27_2 = (f['clk'] >> 2) + (1 if mut == 'clk_off_by_one' else 0)
    tx_clk = f['clk'] ^ (0x4 if mut == 'whiten_clk' else 0)
    parity = br.sync_word(lap ^ 1 if mut == 'parity_lap' else lap)[:34]
    clkbits = br.lsb_bits(clk27_2, 26)
    if mut == 'clk_msb_first':
        clkbits = clkbits[::-1]
    fields = [parity, br.lsb_bits(lap, 24), [f['eir']], [f['reserved']],
              br.lsb_bits(f['sr'], 2), br.lsb_bits(f['sp'], 2),
              br.lsb_bits(uap, 8), br.lsb_bits(nap, 16),
              br.lsb_bits(cod, 24), br.lsb_bits(f['am_addr'], 3), clkbits,
              br.lsb_bits(f['previously_used'], 3)]
    if mut == 'swap_fields':
        fields[6], fields[7] = br.lsb_bits(nap & 0xFF, 8), br.lsb_bits(
            (nap >> 8) | (uap << 8), 16)               # UAP and NAP swapped
    payload = [b for fld in fields for b in fld]
    assert len(payload) == 144
    payload += br.crc16(payload, huap ^ (1 if mut == 'crc_uap' else 0))
    header = br.packet_header(f['lt_addr'], 2, f['flow'], f['arqn'], f['seqn'],
                              huap ^ (1 if mut == 'hec_uap' else 0))
    w = br.whitening(tx_clk, len(header) + len(payload))
    header = [b ^ x for b, x in zip(header, w)]
    payload = [b ^ x for b, x in zip(payload, w[len(header):])]
    payload = payload if mut == 'no_fec23' else br.fec23(payload)
    return br.access_code(f['access_lap']) + br.fec13(header) + payload


def check_mutants():
    print('mutants: the test that catches each')
    rng = random.Random(11)
    f = random_fhs(rng, 'page')
    f['clk'] &= ~3                                    # a master slot
    p = make(f)
    check('the independent construction equals the encoder, bit for bit',
          build(f), p.bits)
    for style in ('page', 'inquiry'):
        g = random_fhs(rng, style)
        check('... and for a %s packet' % style, build(g), make(g).bits)

    for mut in ('swap_fields', 'parity_lap', 'crc_uap', 'whiten_clk',
                'no_fec23', 'clk_off_by_one', 'clk_msb_first', 'hec_uap'):
        bad = build(f, mut)
        caught = []
        if bad != p.bits:
            caught.append('bit-for-bit vs encoder')
        if len(bad) != AIR:
            caught.append('structure (length %d)' % len(bad))
        else:
            d = bt_fhs.parse_fhs_air_bits(bad, f['header_uap'], p.tx_clk) \
                if _parses(bad, f, p) else None
            if d is None:
                caught.append('parse round trip (FEC/HEC)')
            else:
                if not d['crc_ok']:
                    caught.append('parse: CRC')
                if not d['header_ok']:
                    caught.append('parse: HEC')
                if not d['parity_ok']:
                    caught.append('parse: parity vs LAP')
                if [d[k] for k in FIELD_KEYS] != [f[k] for k in FIELD_KEYS] \
                        or d['clk27_2'] != f['clk'] >> 2:
                    caught.append('parse: fields')
        if _lib is not None and len(bad) == AIR:
            r = btbb_decode(bad, f['header_uap'], p.tx_clk)
            if r is None:
                caught.append('libbtbb: header')
            elif r['verdict'] != 1000:
                caught.append('libbtbb: CRC')
            else:
                pf = bt_fhs.parse_fhs_payload(br.bytes_bits(r['payload'])[:144])
                if [pf[k] for k in FIELD_KEYS] != [f[k] for k in FIELD_KEYS] \
                        or pf['clk27_2'] != f['clk'] >> 2:
                    caught.append('libbtbb: fields')
                if (r['lap'], r['uap'], r['nap'], r['clk27_2']) != (
                        f['lap'], f['uap'], f['nap'], f['clk'] >> 2):
                    caught.append('libbtbb: its own FHS readout')
        print('      %-16s caught by: %s' % (mut, '; '.join(caught) or 'NOTHING'))
        check('mutant %s is caught' % mut, bool(caught), True)
        # the mutants libbtbb must catch on its own, with nothing of ours
        if mut in ('crc_uap', 'whiten_clk', 'hec_uap', 'parity_lap') and _lib:
            check('mutant %s: one of libbtbb\'s own checks fires' % mut
                  if mut != 'parity_lap' else
                  'mutant parity_lap: libbtbb alone cannot see it (documented)',
                  any(c.startswith('libbtbb') for c in caught),
                  mut != 'parity_lap')


AIR = bt_fhs.AIR_BITS


def _parses(bad, f, p):
    try:
        bt_fhs.parse_fhs_air_bits(bad, f['header_uap'], p.tx_clk)
        return True
    except ValueError:
        return False


if __name__ == '__main__':
    check_structure()
    check_libbtbb()
    check_helpers()
    check_x_whitening()
    check_golden()
    check_modulated()
    check_mutants()
    if failures:
        print('RESULT: FAIL (%d)' % len(failures))
        sys.exit(1)
    print('RESULT: PASS')
