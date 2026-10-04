#!/usr/bin/env python3
"""Hold the Bluetooth hop kernel to the Core specification's own hop data.

    python scripts/test_bt_hop.py
    BT_HOP_PARTG=part_g_2.txt python scripts/test_bt_hop.py     # every value

``apps/bt_hop.py`` is the connection-state hop selection kernel of Core v6.0
Vol 2 Part B section 2.6, written from the specification text alone. Every
expected channel below is copied from Part G section 2, the frequency hopping
sample data, and nothing here is computed by a kernel of our own: three
addresses, and for each the basic sequence and four adapted ones, fifteen
tables. Of each table's 512 values this keeps the first sixteen and the last
sixteen, 480 in all. The kernel was also held to all 7,680, and two kernels
written separately from the specification text agree with each other on
360,000 clocks and maps, but the full data is Bluetooth SIG's and is not kept
here: ``BT_HOP_PARTG`` names a file holding it as bluey-ox-walker's
``knowledge/sample_data.md`` does, and every table in it is checked.

Clocks start at CLK = 0x10 and run in steps of 2, a Central slot and the
Peripheral slot after it in turn, up to 0x40e. The adapted tables give both
slots of a pair the same channel, which the basic ones do not.

Then three properties that follow from the rules and not from the tables: a
channel is always 0 to 78; an adapted channel is always a used one; and where
the basic channel is used the adapted sequence keeps it.
"""
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_hop  # noqa: E402

failures = []

#: (address, used channels or None, the first 16 values from CLK 0x10 and the
#: last 16, ending at CLK 0x40e), copied from Part G section 2.
TABLES = [
    (0x0000000, None, [ 8, 66, 10, 70, 12, 19, 14, 23, 16,  1, 18,  5, 20, 33, 22, 37, 29, 65, 33,  2, 45, 18, 49, 34, 19,  4, 21,  8, 23, 20, 25, 24]),
    (0x0000000, 0x7fffffffffffffffffff, [ 8,  8, 10, 10, 12, 12, 14, 14, 16, 16, 18, 18, 20, 20, 22, 22, 29, 29, 33, 33, 45, 45, 49, 49, 19, 19, 21, 21, 23, 23, 25, 25]),
    (0x0000000, 0x7fffffffffffffc00000, [30, 30, 32, 32, 34, 34, 36, 36, 38, 38, 40, 40, 42, 42, 22, 22, 29, 29, 33, 33, 45, 45, 49, 49, 50, 50, 52, 52, 23, 23, 25, 25]),
    (0x0000000, 0x55555555555555555555, [ 8,  8, 10, 10, 12, 12, 14, 14, 16, 16, 18, 18, 20, 20, 22, 22, 26, 26, 30, 30, 42, 42, 46, 46, 16, 16, 18, 18, 20, 20, 22, 22]),
    (0x0000000, 0x2aaaaaaaaaaaaaaaaaaa, [ 9,  9, 11, 11, 13, 13, 15, 15, 17, 17, 19, 19, 21, 21, 23, 23, 29, 29, 33, 33, 45, 45, 49, 49, 19, 19, 21, 21, 23, 23, 25, 25]),
    (0x2a96ef25, None, [55, 26, 19, 20, 23, 22, 53, 40, 57, 42, 21, 36, 25, 38, 27, 63, 20,  9, 65, 78, 67,  7, 26,  5, 44, 29, 32, 23, 36, 25, 70, 43]),
    (0x2a96ef25, 0x7fffffffffffffffffff, [55, 55, 19, 19, 23, 23, 53, 53, 57, 57, 21, 21, 25, 25, 27, 27, 20, 20, 65, 65, 67, 67, 26, 26, 44, 44, 32, 32, 36, 36, 70, 70]),
    (0x2a96ef25, 0x7fffffffffffffc00000, [55, 55, 50, 50, 23, 23, 53, 53, 57, 57, 52, 52, 25, 25, 27, 27, 60, 60, 65, 65, 67, 67, 26, 26, 44, 44, 32, 32, 36, 36, 70, 70]),
    (0x2a96ef25, 0x55555555555555555555, [52, 52, 16, 16, 20, 20, 50, 50, 54, 54, 18, 18, 22, 22, 24, 24, 20, 20, 60, 60, 62, 62, 26, 26, 44, 44, 32, 32, 36, 36, 70, 70]),
    (0x2a96ef25, 0x2aaaaaaaaaaaaaaaaaaa, [55, 55, 19, 19, 23, 23, 53, 53, 57, 57, 21, 21, 25, 25, 27, 27, 27, 27, 65, 65, 67, 67, 33, 33, 51, 51, 39, 39, 43, 43, 77, 77]),
    (0x6587cba9, None, [20, 60, 53, 62, 55, 66,  6, 64,  8, 68, 57, 70, 59, 74, 10, 72, 76, 45, 34, 19, 38, 27, 66, 23, 11, 71,  5, 18,  7, 22, 13, 20]),
    (0x6587cba9, 0x7fffffffffffffffffff, [20, 20, 53, 53, 55, 55,  6,  6,  8,  8, 57, 57, 59, 59, 10, 10, 76, 76, 34, 34, 38, 38, 66, 66, 11, 11,  5,  5,  7,  7, 13, 13]),
    (0x6587cba9, 0x7fffffffffffffc00000, [29, 29, 53, 53, 55, 55, 72, 72, 74, 74, 57, 57, 59, 59, 76, 76, 76, 76, 34, 34, 38, 38, 66, 66, 29, 29, 23, 23, 25, 25, 31, 31]),
    (0x6587cba9, 0x55555555555555555555, [20, 20, 52, 52, 54, 54,  6,  6,  8,  8, 56, 56, 58, 58, 10, 10, 76, 76, 34, 34, 38, 38, 66, 66,  6,  6,  0,  0,  2,  2,  8,  8]),
    (0x6587cba9, 0x2aaaaaaaaaaaaaaaaaaa, [23, 23, 53, 53, 55, 55,  9,  9, 11, 11, 57, 57, 59, 59, 13, 13,  3,  3, 39, 39, 43, 43, 71, 71, 11, 11,  5,  5,  7,  7, 13, 13]),
]


def check(ok, what):
    print('  %s %s' % ('ok  ' if ok else 'FAIL', what))
    if not ok:
        failures.append(what)


def check_tables():
    print('Part G, section 2: the connection-state hop sequences')
    for address, used, values in TABLES:
        clks = [0x10 + 2 * i for i in range(16)] + [0x40e - 2 * i for i in range(15, -1, -1)]
        wrong = [(c, w, bt_hop.hop_channel(c, address, used)) for c, w in zip(clks, values)
                 if bt_hop.hop_channel(c, address, used) != w]
        what = '%s, address %07x%s: %d values' % (
            'basic' if used is None else 'adapted', address,
            '' if used is None else ', used 0x%x' % used, len(values))
        check(not wrong, what + ('' if not wrong else ', first CLK 0x%x wants %d got %d' % wrong[0]))


def parse_full(path):
    """Every connection-state table in a copy of Part G section 2."""
    tables, cur = [], None
    for line in open(path, encoding='utf-8', errors='replace'):
        s = line.strip()
        if re.match(r'Hop [Ss]equence \{k\} for Connection state', s):
            cur = {'address': None, 'used': None, 'values': {}}
            tables.append(cur)
            continue
        if re.match(r'Hop [Ss]equence \{k\} for (Page|Inquiry|Peripheral|Central)', s) \
                or re.match(r'\d\.\d\. ', s) or s.startswith('3. '):
            cur = None
        if cur is None:
            continue
        m = re.match(r'(?:ULAP|UAP/LAP):\s*0x([0-9a-fA-F]+)', s)
        if m:
            cur['address'] = int(m.group(1), 16)
        m = re.match(r'Used Channels:\s*0x([0-9a-fA-F]+)', s)
        if m:
            cur['used'] = int(m.group(1), 16)
        m = re.match(r'(0x[0-9a-fA-F]+):?\s+([0-9 |]+)$', s)
        if m:
            base = int(m.group(1), 16)
            for i, v in enumerate(int(v) for v in m.group(2).replace('|', ' ').split()):
                cur['values'][base + 2 * i] = v
    return tables


def check_full(path):
    print('\nEvery value of %s' % path)
    tables = parse_full(path)
    check(len(tables) == 15, '%d connection-state tables found' % len(tables))
    for n, t in enumerate(tables):
        wrong = sum(bt_hop.hop_channel(c, t['address'], t['used']) != w for c, w in t['values'].items())
        check(wrong == 0, 'table %d, address %07x, %d values, %d wrong' % (
            n + 1, t['address'], len(t['values']), wrong))


def check_properties():
    print('\nProperties of the rules')
    rng = random.Random(3)
    maps = [(1 << 79) - 1, sum(1 << i for i in range(33, 53)), sum(1 << i for i in range(0, 79, 2)),
            sum(1 << i for i in rng.sample(range(79), 25))]
    ok_range = ok_used = ok_keep = True
    for _ in range(3000):
        clk, address = rng.getrandbits(28), rng.getrandbits(28)
        used = rng.choice(maps)
        basic = bt_hop.hop_channel(clk, address)
        adapted = bt_hop.hop_channel(clk, address, used)
        ok_range &= 0 <= basic <= 78 and 0 <= adapted <= 78
        ok_used &= bool(used >> adapted & 1)
        ok_keep &= not used >> bt_hop.hop_channel(clk & ~2, address) & 1 or adapted == bt_hop.hop_channel(clk & ~2, address)
    check(ok_range, 'every channel is 0 to 78')
    check(ok_used, 'an adapted channel is always a used one')
    check(ok_keep, 'a basic channel that is used is kept by the adapted sequence')
    pair = [(bt_hop.hop_channel(c, 0x2a96ef25, maps[1]), bt_hop.hop_channel(c | 2, 0x2a96ef25, maps[1]))
            for c in range(0, 4000, 4)]
    check(all(a == b for a, b in pair), 'the adapted sequence gives both slots of a pair one channel')
    check(any(bt_hop.hop_channel(c, 0x2a96ef25) != bt_hop.hop_channel(c | 2, 0x2a96ef25)
              for c in range(0, 4000, 4)), 'the basic sequence does not')


def main():
    check_tables()
    path = os.environ.get('BT_HOP_PARTG')
    if path:
        check_full(path)
    check_properties()
    print('\nRESULT: %s' % ('FAIL (%d)' % len(failures) if failures else 'PASS'))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
