#!/usr/bin/env python3
"""The LAP pair of bluey-ox-walker's F + rate confirmatory (its ``knowledge/f-rate-confirm-reg.md`` section 4, owner
decision D2): a mechanical rule, committed before the search is run.

**The rule** (fixed here; the first pair in seed order that meets it is taken, nothing is chosen by hand and no burst
goes through a detector):

* Schedule seeds are tried from ``SEED0`` = 122349 upward, one at a time, in increasing order (the fresh-set search of
  ``scripts/bt_freshset_select.py`` covered 100000-122348). Each seed is planned, not rendered, with the recipe of the
  bench shots: the fresh set's ``d23`` cell (``bt_synth_slotb2``'s LT recipe - the X_bda construction, ID share 87, first
  IDs only, CFO 0 - at offset 50.25 us and +23 ppm drift, 40 MS/s on 2439.0 MHz, 12 s), and its shot is the first
  ``bt_bench.SHOT_SAMPLES`` (10.0 s) with ``bt_bench.make_shot``'s rule for a whole burst (rendered, ``bt_bench.PAD``
  samples clear of both ends).
* A seed qualifies only if all of these hold:
  1. the LAP is none of ``EXCLUDED``: the four LAPs bluey has used (0x885614, 0xEED5EF, 0x20C0AA, 0xDBB3FB), the other
     fresh-set and LT LAPs, and ``bt_synth_slotb.RESERVED_LAPS``;
  2. its 64-bit sync word (``br.sync_word``) has 24 ones (``negative``) or 40 ones (``positive``);
  3. its sync word is at least ``HD_OLD`` = 24 bits from each of the four used LAPs';
  4. every rendered page ID is a first ID (no second ID in the window);
  5. in the shot: at least 4,500 whole bursts, at least 4,000 whole page IDs, at least 80 whole bursts on every channel
     a whole burst uses, no rendered burst cut by the shot's end, and no rendered burst on 2439 MHz.
* The pair: each qualifying seed is checked, as it is found, against every qualifying seed of the other class found
  before it, in increasing seed order. The first pair whose sync words are at least ``HD_PAIR`` = 30 bits apart and
  whose preambles (``br.access_code(lap)[:4]``) are opposite is taken, and the search stops.
* If no pair is found by ``SEED0 + LIMIT``, the rule reports that and takes nothing.

Run: ``python -B scripts/bt_freshset2_select.py`` prints every qualifying seed as it is found, then the pair, and writes
nothing.
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from scripts import bt_bench as bb  # noqa: E402
from scripts import bt_synth_slotb as sb  # noqa: E402
from scripts import bt_synth_slotb2 as g  # noqa: E402

SEED0 = 122349
LIMIT = 400000
#: A fresh-set d23 stem: its recipe is planned with another schedule seed (the stem's own LAP is not used).
RECIPE_STEM = 'F_885614_d23_n3004'
USED = (0x885614, 0xEED5EF, 0x20C0AA, 0xDBB3FB)
EXCLUDED = set(USED) | {int(k, 16) for k in g.FR_SCHEDULES} | {0x0466AB, 0x662492, 0x4073AD, 0x2044EF, 0x2626FE,
                                                               0x0AA176} | set(sb.RESERVED_LAPS)
HD_OLD = 24
HD_PAIR = 30
MIN_BURSTS, MIN_IDS, MIN_PER_CHANNEL = 4500, 4000, 80
FORBIDDEN_MHZ = 2439.0
N_SLOTS = int(12.0 * 1e6 / g.TX_SLOT_US)


def sync(lap):
    return np.array(br.sync_word(lap), dtype=np.int8)


def hd(a, b):
    return int((sync(a) != sync(b)).sum())


def preamble(lap):
    return ''.join(str(b) for b in br.access_code(lap)[:4])


def word_class(lap):
    return {24: 'negative', 40: 'positive'}.get(int(sync(lap).sum()))


def lap_ok(lap):
    """Conditions 1-3, on the LAP alone."""
    return lap not in EXCLUDED and word_class(lap) is not None and all(hd(lap, u) >= HD_OLD for u in USED)


def cannot_qualify(seed):
    """A pre-filter that only skips seeds which CANNOT meet conditions 1-3: the LAP is the first draw of the plan
    (``draw_train`` with ``default_rng([seed, 11])`` exactly as ``plan_x`` makes it for the bda mode)."""
    rng = np.random.default_rng([seed, 11])
    lap = g.draw_train(rng, 40e6, 2439.0, N_SLOTS, g.K0, g.K0 + N_SLOTS, need_43=False)[0]
    return not lap_ok(lap)


def shot_facts(side, n=bb.SHOT_SAMPLES):
    """The shot's burst facts, with ``bt_bench.make_shot``'s rule for a whole burst."""
    sps = side['samples_per_symbol']
    whole, cut, on_forbidden = [], [], []
    for e in side['bursts']:
        if not e['rendered'] or e['start_sample'] >= n:
            continue
        end = e['start_sample'] + e['air_bits_length'] * sps
        if e['channel_mhz'] == FORBIDDEN_MHZ:
            on_forbidden.append(e['index'])
        if e['start_sample'] - bb.PAD >= 0 and end + bb.PAD <= n:
            whole.append(e)
        else:
            cut.append(e['index'])
    per_ch = {}
    for e in whole:
        per_ch[e['channel_mhz']] = per_ch.get(e['channel_mhz'], 0) + 1
    roles = {}
    for e in whole:
        roles[e['role']] = roles.get(e['role'], 0) + 1
    return dict(n_whole=len(whole), n_ids=roles.get('id_page', 0), roles=roles, per_channel_mhz=dict(sorted(per_ch.items())),
                min_per_channel=min(per_ch.values()) if per_ch else 0, cut=cut, on_2439=on_forbidden)


def check_seed(seed):
    """``(qualifies, row)`` of a seed under the bench-shot recipe."""
    if cannot_qualify(seed):
        return False, None
    side = g.plan_file(RECIPE_STEM, seed=seed)['sidecar']
    lap = side['lap']
    ids = [e for e in side['bursts'] if e['role'] == 'id_page' and e['rendered']]
    f = shot_facts(side)
    row = dict(seed=seed, lap='0x%06X' % lap, uap='0x%02X' % side['uap'], cls=word_class(lap), preamble=preamble(lap),
               hd_to_used={'0x%06X' % u: hd(lap, u) for u in USED}, **f)
    ok = (lap_ok(lap) and all(e['half'] == 'first' for e in ids) and f['n_whole'] >= MIN_BURSTS and
          f['n_ids'] >= MIN_IDS and f['min_per_channel'] >= MIN_PER_CHANNEL and not f['cut'] and not f['on_2439'])
    return ok, row


def search(seed0=SEED0, limit=LIMIT):
    found = {'negative': [], 'positive': []}
    seed = seed0
    while seed < seed0 + limit:
        ok, row = check_seed(seed)
        if ok:
            print(json.dumps(row), flush=True)
            other = 'positive' if row['cls'] == 'negative' else 'negative'
            for prev in found[other]:
                a, b = int(prev['lap'], 16), int(row['lap'], 16)
                if hd(a, b) >= HD_PAIR and preamble(a) != preamble(b):
                    return (prev, row), seed
            found[row['cls']].append(row)
        seed += 1
    return None, seed


def main():
    pair, last = search()
    print('\nsearched seeds %d..%d' % (SEED0, last))
    if pair is None:
        print('NO PAIR meets the rule')
        return 1
    a, b = pair
    print('pair: %s (seed %d, %s) and %s (seed %d, %s); sync-word distance %d; preambles %s / %s' % (
        a['lap'], a['seed'], a['cls'], b['lap'], b['seed'], b['cls'], hd(int(a['lap'], 16), int(b['lap'], 16)),
        a['preamble'], b['preamble']))
    print(json.dumps([a, b], indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
