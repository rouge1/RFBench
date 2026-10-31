#!/usr/bin/env python3
"""Programme Type and Traffic Programme survive a wrongly corrected block B.

Both live in every group's block B. They were taken from each group as it
came, repaired or not, so one block B that error correction got wrong put a
different PTY on screen until the next good group: off the cable on
2026-10-30 a station sending PTY 5 read 4, and one sending 15 read 14. They
now go through the same test as the text fields - an intact block B is
believed, a repaired one only when it repairs the same way twice running.

    python scripts/test_rds_pty.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps.rds_core import RdsProtocol, PTY_RBDS  # noqa: E402
from apps.rds_encode import RdsEncoder  # noqa: E402

failures = []


def check(name, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: {got!r}" + ('' if ok else f" (want {want!r})"))
    if not ok:
        failures.append(name)


def group_0a(proto, pty, tp=0, corrected=()):
    b = (0 << 12) | (0 << 11) | (tp << 10) | (pty << 5)
    proto._decode_group({'A': 0x16F2, 'B': b, 'C': 0xE0E0, 'D': 0x2020},
                        corrected=frozenset(corrected))


print("groups handed over directly")
p = RdsProtocol()
group_0a(p, 5, tp=1)
check("an intact block B sets PTY and TP", (p.snapshot()['pty'], p.tp), (PTY_RBDS[5], 1))
group_0a(p, 4, tp=0, corrected=('B',))
check("one repaired the wrong way does not move them", (p.snapshot()['pty'], p.tp), (PTY_RBDS[5], 1))
group_0a(p, 5, tp=1)
group_0a(p, 4, tp=0, corrected=('B',))
group_0a(p, 4, tp=0, corrected=('B',))
check("one repaired the same way twice running does", (p.snapshot()['pty'], p.tp), (PTY_RBDS[4], 0))
group_0a(p, 15, tp=1)
check("a real change on an intact block B shows at once", p.snapshot()['pty'], PTY_RBDS[15])
q = RdsProtocol()
group_0a(q, 9, corrected=('B',))
check("nothing is shown from a single repaired group", q.snapshot()['pty'], None)


def bursty(seed, pty, bursts_per_block=0.10, seconds=120):
    """Groups that left a wrong PTY or TP on display, once the right one was read."""
    rng = random.Random(seed)
    enc = RdsEncoder(pi=0x16F2, ps='98.7WMZQ', pty=pty, tp=1)
    enc.set_now_playing('Dan + Shay', 'Say So')
    proto = RdsProtocol()
    seen, bad = False, 0
    for _ in range(int(seconds * 1187.5 / 104)):
        bits = enc.next_bits()
        for blk in range(4):
            if rng.random() < bursts_per_block:
                # As in test_rds_radiotext: up to 10 bits, always detected,
                # half of them too long to repair.
                n = rng.randint(1, 10)
                start = blk * 26 + rng.randint(0, 26 - n)
                for i in range(n):
                    if i in (0, n - 1) or rng.random() < 0.5:
                        bits[start + i] ^= 1
        proto.feed(bits)
        right = proto.snapshot()['pty'] == PTY_RBDS[pty] and proto.tp == 1
        if seen:
            bad += not right
        seen = seen or right
    return bad


print("\na marginal signal: error bursts in the bitstream itself")
for seed in range(4):
    for pty in (5, 15):
        check(f"seed {seed}, PTY {pty}: groups showing the wrong PTY or TP", bursty(seed, pty), 0)

print("RESULT:", "PASS" if not failures else f"FAIL ({', '.join(failures)})")
