#!/usr/bin/env python3
"""Hold the page, inquiry, scan and response hop kernels to the Core specification.

    python scripts/test_bt_hop_substates.py
    BT_SPEC_TEXT=core_v6_full.txt python scripts/test_bt_hop_substates.py     # every Part G value

``apps/bt_hop_substates.py`` is the hop selection of the page scan, inquiry scan,
page, inquiry, Peripheral page response, Central page response and inquiry
response substates, Core v6.0 Vol 2 Part B section 2.6 (Figure 2.16, Table 2.2,
Table 2.3, EQ 1 to EQ 8), written from the specification text. Three things are
checked, in this order:

* **Part G section 2**, the frequency hopping sample data, the only numbers in
  the specification for these substates: three addresses, and for each the page
  scan table, the page and inquiry table, the Peripheral response table and the
  Central response table. Every expected channel in ``PART_G`` below is copied
  from the specification and nothing is computed by a kernel of our own. The
  data is Bluetooth SIG's and is not kept whole here (a few rows of each table);
  ``BT_SPEC_TEXT`` names a file holding the specification's text, and then every
  value of the 12 tables is checked. **The page and inquiry table is EQ 2 unchanged
  with the train alternating A, B, A, B every 0x1000 block** (koffset 24 for even
  CLKE16-12, 8 for odd: a train repeated Npage x 10 ms = 1.28 s, then the other, section
  8.3.2 and 2.6.4.2; Part G does not state the offset for that table). All its
  values are reproduced this way, and the test shows an always-A kernel (the mutant)
  fails on exactly the odd blocks; see ``apps/bt_hop_substates.py`` note 1.
* **A second kernel written here**, from Table 2.1, Figure 2.16 and Table 2.2
  with its control words built from bit lists of the address and the clock, no
  helper of ``bt_hop`` in it. It agrees with the module on random clocks and
  addresses for every substate, and with Part G.
* **Properties the text states**: a train is 16 distinct frequencies; the 32
  frequencies of a page or inquiry sequence over 1.28 s are distinct and the A and
  B trains share none; they are the 32 frequencies the scan visits; every channel
  is 0..78; the response sequences advance one X per master TX slot; the Central
  response is the page sequence of the frozen clock; the scan frequency holds for
  1.28 s.
"""
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_hop_substates as hs  # noqa: E402

RESULTS = []


def check(ok, what):
    ok = bool(ok)
    RESULTS.append((ok, what))
    print('  %s %s' % ('ok  ' if ok else 'FAIL', what))
    return ok


#: Part G section 2, "Frequency hopping sample data": a few rows of each table. Rows are
#: ``(first clock, values)``: the scan table steps 0x1000 along a row, the page and inquiry
#: table steps 1, the response tables step 2 (a Central or Peripheral slot a tick).
PART_G = [
    # first set, UAP/LAP 0x00000000
    dict(address=0x00000000,
         scan=[
             (0x0000000, [0, 2, 4, 6, 8, 10, 12, 14]),
             (0x0008000, [16, 18, 20, 22, 24, 26, 28, 30]),
             (0x0010000, [32, 34, 36, 38, 40, 42, 44, 46]),
             (0x0018000, [48, 50, 52, 54, 56, 58, 60, 62]),
             (0x0020000, [0, 2, 4, 6, 8, 10, 12, 14]),
             (0x0028000, [16, 18, 20, 22, 24, 26, 28, 30]),
             (0x0030000, [32, 34, 36, 38, 40, 42, 44, 46]),
             (0x0038000, [48, 50, 52, 54, 56, 58, 60, 62]),
         ],
         page=[
             (0x0000000, [48, 50, 9, 13, 52, 54, 41, 45, 56, 58, 11, 15, 60, 62, 43, 47]),
             (0x0000010, [0, 2, 64, 68, 4, 6, 17, 21, 8, 10, 66, 70, 12, 14, 19, 23]),
             (0x0001000, [48, 18, 9, 5, 20, 22, 33, 37, 24, 26, 3, 7, 28, 30, 35, 39]),
             (0x0001010, [32, 34, 72, 76, 36, 38, 25, 29, 40, 42, 74, 78, 44, 46, 27, 31]),
             (0x0002000, [16, 18, 1, 5, 52, 54, 41, 45, 56, 58, 11, 15, 60, 62, 43, 47]),
             (0x0002010, [0, 2, 64, 68, 4, 6, 17, 21, 8, 10, 66, 70, 12, 14, 19, 23]),
             (0x0003000, [48, 50, 9, 13, 52, 22, 41, 37, 24, 26, 3, 7, 28, 30, 35, 39]),
             (0x0003010, [32, 34, 72, 76, 36, 38, 25, 29, 40, 42, 74, 78, 44, 46, 27, 31]),
         ],
         periph=[
             (0x0000012, [64, 2, 68, 4, 17, 6, 21, 8, 66, 10, 70, 12, 19, 14, 23, 16]),
             (0x0000072, [9, 50, 13, 52, 41, 54, 45, 56, 11, 58, 15, 60, 43, 62, 47, 0]),
         ],
         central=[
             (0x0000014, [2, 68, 4, 17, 6, 21, 8, 66, 10, 70, 12, 19, 14, 23, 16, 1]),
             (0x0000074, [50, 13, 52, 41, 54, 45, 56, 11, 58, 15, 60, 43, 62, 47, 0, 64]),
         ],
         ),
    # second set, UAP/LAP 0x2a96ef25
    dict(address=0x2a96ef25,
         scan=[
             (0x0000000, [49, 13, 17, 51, 55, 19, 23, 53]),
             (0x0008000, [57, 21, 25, 27, 31, 74, 78, 29]),
             (0x0010000, [33, 76, 1, 35, 39, 3, 7, 37]),
             (0x0018000, [41, 5, 9, 43, 47, 11, 15, 45]),
             (0x0020000, [49, 13, 17, 51, 55, 19, 23, 53]),
             (0x0028000, [57, 21, 25, 27, 31, 74, 78, 29]),
             (0x0030000, [33, 76, 1, 35, 39, 3, 7, 37]),
             (0x0038000, [41, 5, 9, 43, 47, 11, 15, 45]),
         ],
         page=[
             (0x0000000, [41, 5, 10, 4, 9, 43, 6, 16, 47, 11, 18, 12, 15, 45, 14, 32]),
             (0x0000010, [49, 13, 34, 28, 17, 51, 30, 24, 55, 19, 26, 20, 23, 53, 22, 40]),
             (0x0001000, [41, 21, 10, 36, 25, 27, 38, 63, 31, 74, 65, 59, 78, 29, 61, 0]),
             (0x0001010, [33, 76, 2, 75, 1, 35, 77, 71, 39, 3, 73, 67, 7, 37, 69, 8]),
             (0x0002000, [57, 21, 42, 36, 9, 43, 6, 16, 47, 11, 18, 12, 15, 45, 14, 32]),
             (0x0002010, [49, 13, 34, 28, 17, 51, 30, 24, 55, 19, 26, 20, 23, 53, 22, 40]),
             (0x0003000, [41, 5, 10, 4, 9, 27, 6, 63, 31, 74, 65, 59, 78, 29, 61, 0]),
             (0x0003010, [33, 76, 2, 75, 1, 35, 77, 71, 39, 3, 73, 67, 7, 37, 69, 8]),
         ],
         periph=[
             (0x0000012, [34, 13, 28, 17, 30, 51, 24, 55, 26, 19, 20, 23, 22, 53]),
             (0x0000072, [10, 5, 4, 9, 6, 43, 16, 47, 18, 11, 12, 15, 14, 45]),
         ],
         central=[
             (0x0000014, [13, 28, 17, 30, 51, 24, 55, 26, 19, 20, 23, 22, 53, 40]),
             (0x0000074, [5, 4, 9, 6, 43, 16, 47, 18, 11, 12, 15, 14, 45, 32]),
         ],
         ),
    # third set, UAP/LAP 0x6587cba9
    dict(address=0x6587cba9,
         scan=[
             (0x0000000, [16, 65, 67, 18, 20, 53, 55, 6]),
             (0x0008000, [8, 57, 59, 10, 12, 69, 71, 22]),
             (0x0010000, [24, 73, 75, 26, 28, 45, 47, 77]),
             (0x0018000, [0, 49, 51, 2, 4, 61, 63, 14]),
             (0x0020000, [16, 65, 67, 18, 20, 53, 55, 6]),
             (0x0028000, [8, 57, 59, 10, 12, 69, 71, 22]),
             (0x0030000, [24, 73, 75, 26, 28, 45, 47, 77]),
             (0x0038000, [0, 49, 51, 2, 4, 61, 63, 14]),
         ],
         page=[
             (0x0000000, [0, 49, 36, 38, 51, 2, 42, 40, 4, 61, 44, 46, 63, 14, 50, 48]),
             (0x0000010, [16, 65, 52, 54, 67, 18, 58, 56, 20, 53, 60, 62, 55, 6, 66, 64]),
             (0x0001000, [0, 57, 36, 70, 59, 10, 74, 72, 12, 69, 76, 78, 71, 22, 3, 1]),
             (0x0001010, [24, 73, 5, 7, 75, 26, 11, 9, 28, 45, 13, 30, 47, 77, 34, 32]),
             (0x0002000, [8, 57, 68, 70, 51, 2, 42, 40, 4, 61, 44, 46, 63, 14, 50, 48]),
             (0x0002010, [16, 65, 52, 54, 67, 18, 58, 56, 20, 53, 60, 62, 55, 6, 66, 64]),
             (0x0003000, [0, 49, 36, 38, 51, 10, 42, 72, 12, 69, 76, 78, 71, 22, 3, 1]),
             (0x0003010, [24, 73, 5, 7, 75, 26, 11, 9, 28, 45, 13, 30, 47, 77, 34, 32]),
         ],
         periph=[
             (0x0000012, [52, 65, 54, 67, 58, 18, 56, 20, 60, 53, 62, 55, 66, 6, 64, 8]),
             (0x0000072, [36, 49, 38, 51, 42, 2, 40, 4, 44, 61, 46, 63, 50, 14, 48, 16]),
         ],
         central=[
             (0x0000014, [65, 54, 67, 58, 18, 56, 20, 60, 53, 62, 55, 66, 6, 64, 8, 68]),
             (0x0000074, [49, 38, 51, 42, 2, 40, 4, 44, 61, 46, 63, 50, 14, 48, 16, 52]),
         ],
         ),
]


# --- a second kernel, written here from the specification text -----------------------

#: Table 2.1: the butterfly of each control signal P0..P13, as the two Z bits it swaps.
TABLE_2_1 = {0: (0, 1), 1: (2, 3), 2: (1, 2), 3: (3, 4), 4: (0, 4), 5: (1, 3), 6: (0, 2),
             7: (3, 4), 8: (1, 4), 9: (0, 3), 10: (2, 4), 11: (1, 3), 12: (0, 3), 13: (1, 2)}


def bit_list(value, n):
    """The n low bits of value, element i being bit i."""
    return [(value >> i) & 1 for i in range(n)]


def num(bits_lsb_first):
    return sum(b << i for i, b in enumerate(bits_lsb_first))


def clk_field(clk, hi, lo):
    """CLKhi-lo as a number: CLKhi the most significant bit."""
    return num(bit_list(clk, 28)[lo:hi + 1])


def clk_4_2_0(clk):
    """CLK4-2,0: bits 4, 3, 2, 0, in that order from the most significant."""
    c = bit_list(clk, 28)
    return c[4] * 8 + c[3] * 4 + c[2] * 2 + c[0]


def control_words(substate, addr, **kw):
    """Table 2.2 and Table 2.3 for a substate, built from the bit lists of the address and the clocks.
    ``substate`` is a, c, e, f, g, h or k of the table's columns; the clock arguments are named
    clkn, clke, clkn_frozen, clke_frozen, n, koffset, knudge."""
    a = bit_list(addr, 28)
    ctl = dict(A=num(a[23:28]), B=num(a[19:23]),
               C=a[0] + 2 * a[2] + 4 * a[4] + 8 * a[6] + 16 * a[8],
               D=num(a[10:19]),
               E=a[1] + 2 * a[3] + 4 * a[5] + 8 * a[7] + 16 * a[9] + 32 * a[11] + 64 * a[13],
               F=0)
    n = kw.get('n', 0)
    ko, kn = kw.get('koffset', 24), kw.get('knudge', 0)
    if substate == 'a':                                  # page scan
        x, y1 = clk_field(kw['clkn'], 16, 12), 0
    elif substate == 'c':                                # inquiry scan: Xir
        x, y1 = (clk_field(kw['clkn'], 16, 12) + n) % 32, 0
    elif substate == 'e':                                # page, EQ 2
        c = kw['clke']
        x = (clk_field(c, 16, 12) + ko + kn + (clk_4_2_0(c) - clk_field(c, 16, 12) + 32) % 16) % 32
        y1 = bit_list(c, 28)[1]
    elif substate == 'f':                                # inquiry, EQ 7
        c = kw['clkn']
        x = (clk_field(c, 16, 12) + ko + kn + (clk_4_2_0(c) - clk_field(c, 16, 12) + 32) % 16) % 32
        y1 = bit_list(c, 28)[1]
    elif substate == 'h':                                # Peripheral page response, EQ 5
        x = (clk_field(kw['clkn_frozen'], 16, 12) + n) % 32
        y1 = bit_list(kw['clkn'], 28)[1]
    elif substate == 'g':                                # Central page response, EQ 6
        c = kw['clke_frozen']
        x = (clk_field(c, 16, 12) + ko + kn + (clk_4_2_0(c) - clk_field(c, 16, 12) + 32) % 16 + n) % 32
        y1 = bit_list(kw['clke'], 28)[1]
    elif substate == 'k':                                # inquiry response, EQ 8
        x, y1 = (clk_field(kw['clkn'], 16, 12) + n) % 32, 1
    else:
        raise ValueError(substate)
    ctl.update(X=x, Y1=y1, Y2=32 * y1)
    return ctl


def kernel(ctl):
    """Figure 2.16 from the control word: ADD mod 32, XOR with the four LSBs, PERM5 of seven stages
    (P13 P12 first), ADD mod 79 with E and Y2, the register bank (even channels, then odd)."""
    zp = (ctl['X'] + ctl['A']) % 32
    z = bit_list(zp, 5)
    for j in range(4):
        z[j] ^= bit_list(ctl['B'], 4)[j]
    p = bit_list(ctl['D'], 9) + [bit_list(ctl['C'], 5)[i] ^ ctl['Y1'] for i in range(5)]
    for stage in range(7):                               # stage s holds P(13-2s) and P(12-2s)
        for pn in (13 - 2 * stage, 12 - 2 * stage):
            if p[pn]:
                i, j = TABLE_2_1[pn]
                z[i], z[j] = z[j], z[i]
    reg = (num(z) + ctl['E'] + ctl['F'] + ctl['Y2']) % 79
    bank = [c for c in range(79) if c % 2 == 0] + [c for c in range(79) if c % 2 == 1]
    return bank[reg]


def independent(substate, lap, uap, **kw):
    return kernel(control_words(substate, hs.address(lap, uap) if substate in 'aeghd' else hs.GIAC, **kw))


# --- Part G ------------------------------------------------------------------------------

def split_address(a):
    return a & 0xFFFFFF, (a >> 24) & 0xF


def part_g_checks(tables, label):
    """Each table of ``tables`` (the ``PART_G`` structure) against the module and the second kernel."""
    tot = dict(scan=[0, 0, 0], page=[0, 0, 0], page_mut=[0, 0, 0, 0], periph=[0, 0, 0], central=[0, 0, 0])
    for t in tables:
        lap, uap = split_address(t['address'])
        for row, vals in t['scan']:
            for i, v in enumerate(vals):
                clkn = row + 0x1000 * i
                tot['scan'][0] += 1
                tot['scan'][1] += hs.page_scan(clkn, lap, uap) != v
                tot['scan'][2] += independent('a', lap, uap, clkn=clkn) != v
        for row, vals in t['page']:
            for i, v in enumerate(vals):
                clk = row + i
                odd = (clk >> 12) & 1
                ko = 8 if odd else 24                    # the train alternates A, B, A, B with each 1.28 s block
                tot['page'][0] += 1
                tot['page'][1] += hs.page(clk, lap, uap, ko) != v
                tot['page'][2] += independent('e', lap, uap, clke=clk, koffset=ko) != v
                # the mutant: always the A train. It must be wrong on the odd blocks and right on the even
                wrong = hs.page(clk, lap, uap, 24) != v
                tot['page_mut'][0] += odd
                tot['page_mut'][1] += wrong and odd
                tot['page_mut'][2] += not odd
                tot['page_mut'][3] += wrong and not odd
        for row, vals in t['periph']:
            for i, v in enumerate(vals):
                clk = row + 2 * i
                n = hs.n_of(clk, 0x10)
                tot['periph'][0] += 1
                tot['periph'][1] += hs.peripheral_page_response(0x10, clk, lap, uap, n) != v
                tot['periph'][2] += independent('h', lap, uap, clkn_frozen=0x10, clkn=clk, n=n) != v
        for row, vals in t['central']:
            for i, v in enumerate(vals):
                clk = row + 2 * i
                n = hs.n_of(clk, 0x10)
                tot['central'][0] += 1
                tot['central'][1] += hs.central_page_response(0x12, clk, lap, uap, 24, 0, n) != v
                tot['central'][2] += independent('g', lap, uap, clke_frozen=0x12, clke=clk, koffset=24, knudge=0, n=n) != v
    print('\nPart G, %s (%d tables of 4 kinds)' % (label, len(tables)))
    s = tot['scan']
    check(s[0] > 0 and s[1] == 0 and s[2] == 0, 'page scan and inquiry scan table: %d values, module and second kernel agree '
          'with Part G (%d, %d wrong)' % (s[0], s[1], s[2]))
    s = tot['page']
    check(s[0] > 0 and s[1] == 0 and s[2] == 0, 'page and inquiry table: all %d values, EQ 2 unchanged with koffset 24 in the even 0x1000 '
          'blocks and 8 in the odd (the train alternates A, B every 1.28 s): module and second kernel agree with Part G (%d, %d wrong)'
          % (s[0], s[1], s[2]))
    s = tot['page_mut']
    check(s[0] > 0 and s[1] == s[0] and s[3] == 0,
          'mutant, always the A train: wrong on %d of %d odd-block values and on %d of %d even-block values (exactly the odd blocks)'
          % (s[1], s[0], s[3], s[2]))
    s = tot['periph']
    check(s[0] > 0 and s[1] == 0 and s[2] == 0, 'Peripheral response table (CLKN* 0x10, N = (clock - 0x10) // 4): %d values, %d '
          'and %d wrong' % (s[0], s[1], s[2]))
    s = tot['central']
    check(s[0] > 0 and s[1] == 0 and s[2] == 0, 'Central response table (CLKE* 0x12, offset 24, N = (clock - 0x10) // 4): %d '
          'values, %d and %d wrong' % (s[0], s[1], s[2]))


def parse_spec(path):
    """The four tables of each of the three sets of Part G section 2 out of the specification text,
    as the ``PART_G`` structure (every row, not a few)."""
    lines = open(path).read().split('\n')
    a = next(i for i, l in enumerate(lines) if l.startswith('2     FREQUENCY HOPPING SAMPLE DATA'))
    b = next(i for i, l in enumerate(lines) if i > a and l.startswith('3    ACCESS CODE SAMPLE DATA'))
    sets, cur, sec = [], None, None
    for l in lines[a:b]:
        if re.match(r'\s*2\.[123]\s+(First|Second|Third) set', l):
            cur = dict(address=None, scan=[], page=[], periph=[], central=[])
            sets.append(cur)
            sec = None
            continue
        m = re.match(r'\s*Hop [Ss]equence \{k\} for (.*)', l)
        if m:
            t = m.group(1)
            sec = ('scan' if t.startswith('Page Scan') else 'page' if t.startswith('Page and Inquiry')
                   else 'periph' if t.startswith('Peripheral Response') else 'central' if t.startswith('Central Response')
                   else None)
            continue
        m = re.match(r'\s*(UAP / LAP|ULAP):\s*0x([0-9a-f]+)', l)
        if m and sec and cur['address'] is None:
            cur['address'] = int(m.group(2), 16)
            continue
        m = re.match(r'\s*0x([0-9a-f]+):\s*(.*)$', l)
        if m and sec:
            cur[sec].append((int(m.group(1), 16), [int(x) for x in re.findall(r'\d+', m.group(2))]))
    return sets


# --- random agreement and the properties of the text -----------------------------------

def agreement(rng):
    print('\nThe module and the second kernel on random addresses and clocks')
    bad = {k: 0 for k in 'acefghk'}
    n = 3000
    for _ in range(n):
        lap, uap = rng.randrange(1 << 24), rng.randrange(256)
        clk, clk2 = rng.randrange(1 << 28), rng.randrange(1 << 28)
        ko, kn, nn = rng.choice((24, 8)), rng.choice((0, 2, 4, 30)), rng.randrange(0, 40)
        bad['a'] += hs.page_scan(clk, lap, uap) != independent('a', lap, uap, clkn=clk)
        bad['c'] += hs.inquiry_scan(clk, nn) != independent('c', 0, 0, clkn=clk, n=nn)
        bad['e'] += hs.page(clk, lap, uap, ko, kn) != independent('e', lap, uap, clke=clk, koffset=ko, knudge=kn)
        bad['f'] += hs.inquiry(clk, ko, kn) != independent('f', 0, 0, clkn=clk, koffset=ko, knudge=kn)
        bad['g'] += hs.central_page_response(clk, clk2, lap, uap, ko, kn, nn) != \
            independent('g', lap, uap, clke_frozen=clk, clke=clk2, koffset=ko, knudge=kn, n=nn)
        bad['h'] += hs.peripheral_page_response(clk, clk2, lap, uap, nn) != \
            independent('h', lap, uap, clkn_frozen=clk, clkn=clk2, n=nn)
        bad['k'] += hs.inquiry_response(clk, nn) != independent('k', 0, 0, clkn=clk, n=nn)
    for k, name in (('a', 'page scan'), ('c', 'inquiry scan'), ('e', 'page'), ('f', 'inquiry'),
                    ('g', 'Central page response'), ('h', 'Peripheral page response'), ('k', 'inquiry response')):
        check(bad[k] == 0, '%s: %d random cases, %d differ' % (name, n, bad[k]))
    # the control words of the module are Table 2.2's, bit for bit
    wrong = 0
    for _ in range(2000):
        addr = rng.randrange(1 << 28)
        x, y1 = rng.randrange(32), rng.randrange(2)
        w = hs.control_word(x, y1, addr)
        ref = control_words('a', addr, clkn=x << 12)
        ref.update(Y1=y1, Y2=32 * y1)
        wrong += w != ref
    check(wrong == 0, 'control_word(): A to F, X, Y1, Y2 of Table 2.2 and Table 2.3 on 2000 random addresses (%d wrong)' % wrong)


def properties(rng):
    print('\nWhat the specification says of the sequences')
    seen_all = True
    train_ok = split_ok = scan_ok = hold_ok = range_ok = True
    for _ in range(40):
        lap, uap = rng.randrange(1 << 24), rng.randrange(16)
        base = rng.randrange(1 << 28) & ~0xFFF           # a clock whose CLK11-0 is zero
        for native in (False, True):
            chans = {}
            for train, ko in (('A', 24), ('B', 8)):
                seq = []
                for t in range(0, 32):                  # 32 ticks = 10 ms, the 16 TX ticks (CLK1 = 0)
                    clk = base + t
                    if (clk >> 1) & 1:
                        continue
                    seq.append(hs.inquiry(clk, ko) if native else hs.page(clk, lap, uap, ko))
                chans[train] = seq
                train_ok = train_ok and len(seq) == 16 and len(set(seq)) == 16
                range_ok = range_ok and all(0 <= c < 79 for c in seq)
            # the 32 frequencies of 1.28 s: A and B do not overlap, and they are the scan's 32
            split_ok = split_ok and not set(chans['A']) & set(chans['B']) and len(set(chans['A']) | set(chans['B'])) == 32
            visited = {hs.inquiry_scan(base + (x << 12), 0) if native else hs.page_scan(base + (x << 12), lap, uap)
                       for x in range(32)}
            scan_ok = scan_ok and visited == set(chans['A']) | set(chans['B']) and len(visited) == 32
    for _ in range(40):
        lap, uap = rng.randrange(1 << 24), rng.randrange(16)
        clkn = rng.randrange(1 << 28) & ~0xFFF
        ch = {hs.page_scan(clkn + d, lap, uap) for d in range(0, 4096)}
        hold_ok = hold_ok and len(ch) == 1                  # the scan frequency holds for 1.28 s
    check(train_ok and range_ok, 'a train is 16 distinct frequencies over 10 ms, A and B, page and inquiry, every channel in 0..78')
    check(split_ok, 'the A train and the B train share no frequency and make 32 distinct (page and inquiry)')
    check(scan_ok, 'the 32 frequencies of the page (inquiry) sequence are the 32 the page (inquiry) scan visits, one per 1.28 s')
    check(hold_ok, 'a scan frequency holds for the 4096 ticks (1.28 s) of one CLKN16-12')
    # the A train is centred on the estimate: it contains the scan X of the same clock
    centred = all(hs.page_scan(c, 0x123456, 7) in {ch for _, ch in hs.train_channels(c & ~31, 0x123456, 7, 24)}
                  for c in (rng.randrange(1 << 28) & ~3 for _ in range(300)))
    check(centred, 'EQ 2 with koffset 24: the page train of a clock holds the page scan frequency of the same clock (300 clocks)')
    other = all(hs.page_scan(c, 0x123456, 7) not in {ch for _, ch in hs.train_channels(c & ~31, 0x123456, 7, 8)}
                for c in (rng.randrange(1 << 28) & ~3 for _ in range(300)))
    check(other, 'and the B train of that clock does not')
    # response sequences: one X more per master TX slot (every 4 ticks), N + 1
    adv = True
    for _ in range(200):
        lap, uap = rng.randrange(1 << 24), rng.randrange(16)
        c0 = rng.randrange(1 << 28) & ~3
        for n in range(6):
            x0 = hs.x_peripheral_response(c0, n)
            adv = adv and hs.x_peripheral_response(c0, n + 1) == (x0 + 1) % 32
            adv = adv and hs.x_central_response(c0, 24, 0, n + 1) == (hs.x_central_response(c0, 24, 0, n) + 1) % 32
            adv = adv and hs.x_inquiry_response(c0, n + 1) == (hs.x_inquiry_response(c0, n) + 1) % 32
    check(adv, 'Xprp, Xprc and Xir advance by one (mod 32) for each N')
    n_ok = [hs.n_of(0x10 + d, 0x10) for d in range(0, 12, 2)] == [0, 0, 1, 1, 2, 2] and hs.n_of(0x12, 0x10) == 0
    check(n_ok, 'n_of(): N = 0 at 0x12, 1 at 0x14 and 0x16, 2 at 0x18 and 0x1a for a slot frozen at 0x10')
    # the Peripheral's response sequence is the page sequence's two halves: Y1 = 0 slots are the page-scan like
    # segment positions X, X + 1, ... and the Central answers on the page sequence of the frozen clock
    same_seg = True
    for _ in range(100):
        lap, uap = rng.randrange(1 << 24), rng.randrange(16)
        c0 = rng.randrange(1 << 28) & ~3
        seg = {hs.page_scan(c0 + (x << 12), lap, uap) for x in range(32)}
        for n in range(8):
            same_seg = same_seg and hs.peripheral_page_response(c0, c0 + 4 * n, lap, uap, n) in seg \
                and hs.central_page_response(c0, c0 + 4 * n, lap, uap, 24, 0, n + 1) in seg
    check(same_seg, 'the response sequences (CLK1 = 0 slots) stay in the 32-frequency segment of the paged device')
    # Table 2.3: the paged device's LAP and UAP3-0; the GIAC and DCI for inquiry
    a = hs.address(0xABCDEF, 0x5C)
    check(a == 0xCABCDEF and hs.GIAC == 0x9E8B33 and hs.GIAC_ADDRESS == 0x9E8B33,
          'address(): UAP3-0 above the LAP; the inquiry address is the GIAC with the DCI (0x00) above it')
    check(hs.inquiry_scan(0x40000, 0) == hs.select(hs.clk16_12(0x40000), 0, 0x9E8B33), 'inquiry scan: GIAC, Y1 = 0, X = CLKN16-12 at N = 0')
    check(hs.inquiry_response(0x40000, 3) == hs.select((hs.clk16_12(0x40000) + 3) % 32, 1, 0x9E8B33),
          'inquiry response: GIAC, Y1 = 1, X = Xir')


def sensitivity(rng):
    """The Part G check is not vacuous: simple wrong kernels fail it."""
    print('\nThe tables are sensitive: wrong readings fail')
    t = PART_G[1]
    lap, uap = split_address(t['address'])

    def count(fn):
        bad = 0
        for row, vals in t['page']:
            if (row >> 12) & 1:
                continue
            for i, v in enumerate(vals):
                bad += fn(row + i) != v
        return bad
    good = count(lambda c: hs.page(c, lap, uap, 24))
    check(good == 0, 'the right reading: 0 of the even-CLKE16-12 page values wrong')
    check(count(lambda c: hs.page(c, lap, uap, 8)) > 40, 'koffset 8 instead of 24 fails the page table')
    check(count(lambda c: hs.select(hs.x_train(c, 24), 0, hs.address(lap, uap))) > 20, 'Y1 forced to 0 (the RX-slot ticks) fails the page table')
    check(count(lambda c: hs.select((hs.clk16_12(c) + 24 + (hs.clk4_2_0(c) ^ 1)) % 32, (c >> 1) & 1, hs.address(lap, uap))) > 40,
          'CLKE4-2,0 with its two LSBs swapped fails the page table')
    swapped = sum(hs.select(hs.x_train(c, 24), (c >> 1) & 1, hs.address(uap, lap)) != v
                  for row, vals in t['page'] if not (row >> 12) & 1 for c, v in zip(range(row, row + 16), vals))
    check(swapped > 40, 'UAP and LAP swapped in the address fails the page table')


def main():
    rng = random.Random(20261007)
    part_g_checks(PART_G, 'the rows kept in this file')
    spec = os.environ.get('BT_SPEC_TEXT', '/tmp/sdr-p7/core_v6_full.txt')
    if os.path.exists(spec):
        tables = parse_spec(spec)
        n = sum(len(v) for t in tables for k, v in t.items() if k != 'address')
        part_g_checks(tables, 'every row of the specification text %s (%d rows)' % (spec, n))
        check(len(tables) == 3 and [t['address'] for t in tables] == [0, 0x2a96ef25, 0x6587cba9],
              'the three sets of Part G section 2 were read')
    else:
        print('  (no BT_SPEC_TEXT: only the kept rows)')
    agreement(rng)
    properties(rng)
    sensitivity(rng)
    bad = [w for ok, w in RESULTS if not ok]
    print('\nRESULT: %s' % ('PASS' if not bad else 'FAIL (%d)' % len(bad)))
    for w in bad:
        print('  failed:', w[:160])
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
