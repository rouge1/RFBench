#!/usr/bin/env python3
"""The LAP selection of the carrier-threshold fresh set (bluey-ox-walker s4-carrier-thr A1.13): a mechanical rule,
committed before the search is run.

**The rule** (fixed here; the first seeds in increasing order that meet it are taken, nothing is chosen by hand and no
burst goes through a detector):

* Schedule seeds are tried from ``SEED0`` = 100000 upward, one at a time, in increasing order (the earlier searches for
  the LT set covered 9700-99999). Each seed is planned, not rendered, with ``bt_synth_slotb2``'s LT recipe: the X_bda
  construction, ID share 87, 40 MS/s on 2439.0 MHz, 12 s, CFO 0.
* A seed qualifies only if all of these hold:
  1. every rendered page ID is a first ID (no second ID in the window);
  2. the set of channels the rendered IDs use is exactly 0x0466AB's, {32, 34, ..., 48} (-5..+11 MHz), or that set plus
     channel 30 (-7 MHz), so every new LAP shares channels 32-48;
  3. the number of rendered IDs is 4,500 to 6,500 (comparable with the LT LAPs, 5,247-5,809);
  4. the LAP is none of 0x0466AB, 0x662492, 0x4073AD, 0x2044EF, 0x2626FE, 0x0AA176.
* The qualifying seeds are sorted into classes by the 64-bit sync word (``br.sync_word``):
  ``negative`` = 24 ones (mean of the +-1 word -0.25); ``positive`` = 40 ones (+0.25); ``balanced`` = 32 ones. Isolated
  bits are counted with the preamble and trailer as neighbours (``br.access_code(lap, trailer=True)``):
  ``balanced_1`` = balanced with isolated 1s >= isolated 0s + 3; ``balanced_0`` = balanced with isolated 0s >=
  isolated 1s + 3.
* Taken: the first 2 ``negative``, the first 2 ``positive``, the first ``balanced_1`` and the first ``balanced_0``.
* The h variants (0.28, 0.35) go to the first ``balanced_1`` and the first ``negative``.

Run: ``python -B scripts/bt_freshset_select.py`` prints every qualifying seed it meets until all six are found, then the
selection, and writes nothing.
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from scripts import bt_synth_slotb2 as g  # noqa: E402

SEED0 = 100000
CHANNELS_0466AB = set(range(32, 49, 2))
EXCLUDED = {0x0466AB, 0x662492, 0x4073AD, 0x2044EF, 0x2626FE, 0x0AA176}
WANT = {'negative': 2, 'positive': 2, 'balanced_1': 1, 'balanced_0': 1}


def word_stats(lap):
    ac = br.access_code(lap, trailer=True)
    s = np.array(ac[4:68])
    d = 2 * s - 1
    iso1 = sum(1 for k in range(64) if ac[4 + k] == 1 and ac[3 + k] == 0 and ac[5 + k] == 0)
    iso0 = sum(1 for k in range(64) if ac[4 + k] == 0 and ac[3 + k] == 1 and ac[5 + k] == 1)
    runs, c = [], 1
    for i in range(1, 64):
        if s[i] == s[i - 1]:
            c += 1
        else:
            runs.append(c)
            c = 1
    runs.append(c)
    return dict(ones=int(s.sum()), mean=float(d.mean()), half1=float(d[:32].mean()), half2=float(d[32:].mean()),
                iso1=iso1, iso0=iso0, longest_run=max(runs))


def classify(w):
    if w['ones'] == 24:
        return 'negative'
    if w['ones'] == 40:
        return 'positive'
    if w['ones'] == 32 and w['iso1'] >= w['iso0'] + 3:
        return 'balanced_1'
    if w['ones'] == 32 and w['iso0'] >= w['iso1'] + 3:
        return 'balanced_0'
    return None


N_SLOTS = int(12.0 * 1e6 / g.TX_SLOT_US)


def cannot_qualify(seed):
    """A pre-filter that only skips seeds which CANNOT meet the rule, from the first draw of the plan (the LAP and the
    page train, ``draw_train`` with ``default_rng([seed, 11])`` exactly as ``plan_x`` makes it): the word's class, the
    excluded LAPs, and a train whose in-window first IDs lack one of channels 32-48 (rendered IDs are a subset of
    them). Every other seed is planned in full."""
    rng = np.random.default_rng([seed, 11])
    lap, uap, clke_k0, table, _ = g.draw_train(rng, 40e6, 2439.0, N_SLOTS, g.K0, g.K0 + N_SLOTS, need_43=False)
    if lap in EXCLUDED or classify(word_stats(lap)) is None:
        return True
    ks = np.arange(g.K0, g.K0 + N_SLOTS)
    ch = g.page_vec(table, clke_k0 + 4 * (ks - g.K0))
    inwin = {int(c) for c in ch if g.in_win(int(c), 40e6, 2439.0)}
    return not CHANNELS_0466AB <= inwin


def check_seed(seed):
    """The plan of a seed under the LT recipe: ``(qualifies, row)``."""
    if cannot_qualify(seed):
        return False, None
    side = g.plan_file('S_0466AB_c0_p0_n1', seed=seed)['sidecar']
    ids = [e for e in side['bursts'] if e['role'] == 'id_page' and e['rendered']]
    chans = {e['channel'] for e in ids}
    w = word_stats(side['lap'])
    row = dict(seed=seed, lap='0x%06X' % side['lap'], uap='0x%02X' % side['uap'], n_ids=len(ids),
               n_conn=sum(e['role'] != 'id_page' for e in side['bursts'] if e['rendered']),
               id_channels=sorted(chans), id_offsets_mhz=sorted({int(round(e['channel_mhz'] - side['center_mhz'])) for e in ids}),
               **w, cls=classify(w))
    ok = (all(e['half'] == 'first' for e in ids) and chans in (CHANNELS_0466AB, CHANNELS_0466AB | {30}) and
          4500 <= len(ids) <= 6500 and side['lap'] not in EXCLUDED and row['cls'] is not None)
    return ok, row


def search(seed0=SEED0, limit=400000):
    taken = {k: [] for k in WANT}
    seed = seed0
    while any(len(taken[k]) < n for k, n in WANT.items()) and seed < seed0 + limit:
        ok, row = check_seed(seed)
        if ok and len(taken[row['cls']]) < WANT[row['cls']]:
            taken[row['cls']].append(row)
            print(json.dumps(row), flush=True)
        seed += 1
    return taken, seed


def main():
    taken, last = search()
    sel = taken['negative'] + taken['positive'] + taken['balanced_1'] + taken['balanced_0']
    print('\nsearched seeds %d..%d' % (SEED0, last - 1))
    print('h variants: %s (first balanced_1) and %s (first negative)' % (taken['balanced_1'][0]['lap'], taken['negative'][0]['lap']))
    print(json.dumps(sel, indent=1))


if __name__ == '__main__':
    main()
