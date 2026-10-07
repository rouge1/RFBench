#!/usr/bin/env python3
"""Hold the two paging-mix files (``pagemix``, ``connonly``) to what their samples and the specification say.

    python scripts/test_bt_synth_pagemix.py                 # small runs, the full plans, the mutants
    python scripts/test_bt_synth_pagemix.py --quick         # without the full plans and the mutants that need them
    python scripts/test_bt_synth_pagemix.py --verify DIR NAME [--seconds 0.3]   # the written files (blind check, container)

``scripts/bt_synth_pagemix.py`` writes ``pagemix`` (10 piconets and 20 joiner exchanges, 12 page and 8 inquiry) and
``connonly`` (10 dense piconets, no joiner), each with ``synth_<name>.json`` (header) and
``synth_<name>.bursts.jsonl.gz`` (one burst per line). Nothing here reads the generator's own bookkeeping for a
check: the definitions are written again (the overlap sweep, the hop sequences, the LMP rules, the exchange table).

* **small runs** (6 noise blocks of 2**22, 0.63 s; 4 joiners): the superposition (the file less the regenerated noise
  equals the sum of every burst rendered alone, rebuilt from its header fields when it has no ``air_bits``) over the
  whole file and on the samples where two bursts share a channel, with noise blocks of 2**22 and of 2**17 (many
  seams); the noise white (lag 1 and 2) with the constant floor's variance; 40 samples per symbol; the container
  read back equal to the plan, sorted, index = line, deterministic bytes; the header's counts equal the lines;
  ``n_samples`` against the real size; the blind check;
* **a 30 dB plan** rendered window by window: carrier, start, level, phase and bits from the samples alone,
  libbtbb decoding headers and payloads of collision-free bursts of every packet type;
* **plan-level checks** (no samples) on the small plans and on the FULL plans of both files: the overlap truth of
  EVERY burst against an independent sweep, no two bursts of a piconet overlapping, the slave rule, LMP by role, the
  hops recomputed with ``bt_hop`` and ``bt_hop_substates``, LAPs and sync-word distances, the exchange table
  recomputed from the burst list, the FHS rule, the symbol phases, the SNR, ``connonly`` with no joiner, ID or FHS;
* **mutants** of the generator, each caught.
"""
import contextlib
import gzip
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import time
from bisect import bisect_right

os.environ.setdefault('OMP_NUM_THREADS', '4')
import numpy as np  # noqa: E402
from scipy.signal import fftconvolve, firwin, upfirdn  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from apps import bt_hop, bt_hop_substates as hs  # noqa: E402
from scripts import bt_synth, bt_synth_mixed as g, bt_synth_pagemix as pm  # noqa: E402
from scripts import test_bt_synth_mixed as tm  # noqa: E402
from scripts import test_bt_lmp as lmpt  # noqa: E402

FS = 40e6
SPS = 40
CENTER = 2441.0
SLOT = 25000
NOISE = bt_synth.AMPLITUDE ** 2 / 100
NOISE_POWER = NOISE * FS / 1e6
MASK = sum(1 << c for c in range(31, 51))
SEED_A, SEED_B = 9301, 9302
SRC = open(pm.__file__).read()
RESULTS = []
QUIET = [False]
HIGH = (28.0, 32.0)
N_PIC = 10
GIAC = 0x9E8B33
BLOCK_SMALL = 2 ** 17
CORES = os.cpu_count() or 4


def check(ok, what):
    ok = bool(ok)
    RESULTS.append((ok, what))
    if not QUIET[0]:
        print('  %s %s' % ('ok  ' if ok else 'FAIL', what), flush=True)
    return ok


@contextlib.contextmanager
def quiet():
    """Collect the checks of a block without printing, yielding the list of those that failed."""
    QUIET[0] = True
    mark = len(RESULTS)
    failed = []
    try:
        yield failed
    finally:
        QUIET[0] = False
        failed.extend(w for ok, w in RESULTS[mark:] if not ok)
        del RESULTS[mark:]


def hex_bits(h, n):
    return tm.hex_bits(h, n)


def bits_of(e):
    """A burst's on-air bits: from ``air_bits`` when it has them, else rebuilt from its header fields and payload."""
    if e.get('air_bits') is not None:
        return hex_bits(e['air_bits'], e['air_bits_length'])
    p = br.Packet(e['lap'], e['uap'], e['clk'], e['ptype'], bytes.fromhex(e['payload_hex']), lt_addr=e['lt_addr'], flow=e['flow'],
                  arqn=e['arqn'], seqn=e['seqn'], llid=e['llid'] if e['llid'] is not None else 0b10, payload_flow=1)
    assert p.header18 == e['header18'], 'rebuilt header differs'
    return [int(b) for b in p.bits]


def my_burst(e):
    """One burst alone as the file holds it, from the sidecar's keys and the repository's modulator."""
    bits = bits_of(e)
    assert len(bits) == e['air_bits_length']
    burst, lead = br.gfsk(np.asarray(bits), FS, h=br.GFSK_H, delay=e['timing_frac'])
    amp = np.sqrt(NOISE * 10 ** (e['snr_db'] / 10))
    lo = e['start_sample'] - lead
    n = np.arange(lo, lo + len(burst))
    cycles = (e['channel'] + 2402 - CENTER) * 1e6 / FS * n
    return lo, (amp * burst * np.exp(1j * e['burst_phase']) * np.exp(2j * np.pi * (cycles - np.floor(cycles)))).astype(np.complex64)


# --- plans and the written truth -----------------------------------------------------------------------

def small_plan(mod=pm, profile='pagemix', blocks=6, joiners=(2, 2), **kw):
    return mod.plan_file(profile, blocks=blocks, n_page=joiners[0], n_inquiry=joiners[1], workers=1, **kw)


def window(e):
    return tm.window(e)


# --- the definitions, written again ---------------------------------------------------------------------

def independent_overlaps(rendered):
    """Overlaps of every burst of a list (by a sort and a bisect on the starts): a different algorithm from the sweep over
    active windows. ``{i: {j: overlap}}`` over time overlaps within 1 channel, plus the same-channel union fractions."""
    return tm.independent_overlaps(rendered)


def check_keys(h, label, profile):
    need = ['generator', 'generator_commit', 'lap', 'uap', 'sample_rate', 'center_mhz', 'modulation', 'slot_samples',
            'start_sample_meaning', 'clk_convention', 'snr_bw_hz', 'noise_1mhz', 'symbol_phases', 'per_burst_keys',
            'n_samples', 'devices', 'seed', 'air_bits_omitted', 'burst_window', 'conformance', 'window', 'notes',
            'counts_per_device', 'counts_per_type', 'n_collisions', 'fraction_collided', 'timing_frac', 'symbol_phase',
            'sync_word_min_distance_piconets', 'sync_word_min_distance_all', 'afh_map', 'exchanges', 'n_joiners',
            'negative_set', 'joiners', 'bursts_file', 'n_bursts', 'bursts_format', 'bursts_head', 'indexing',
            'samples_per_symbol', 'joiner_symbol_phases', 'symbol_phase_meaning', 'per_burst_keys_by_kind']
    miss = [k for k in need if k not in h]
    check(not miss, '%s: the header has every key (%s)' % (label, miss))
    check(h['lap'] is None and h['uap'] is None and h['timing_frac'] is None and h['symbol_phase'] is None and h['snr_bw_hz'] == 1e6
          and h['noise_1mhz'] == NOISE and h['sample_rate'] == FS and h['center_mhz'] == CENTER and h['samples_per_symbol'] == 40
          and FS / br.SYMBOL_RATE == 40 and h['symbol_phases'] == [0, 20],
          '%s: the per-burst values are null at the top; floor, rate, centre and 40 samples per symbol are the repository\'s' % label)
    check(h['n_samples'] == h['n_noise_blocks'] * h['noise_block_samples'], '%s: n_samples is a whole number of noise blocks' % label)
    check(h['negative_set'] == (profile == 'connonly') and h['joiners'] == h['n_joiners'], '%s: negative_set and joiners at the top' % label)
    check(h['bursts_format'] == 'jsonl.gz' and 'line number' in h['indexing'], '%s: the container is named and described' % label)
    notes = ' '.join(h['notes'])
    check('ANSWER KEY' in notes and 'policy label' in notes and 'packet type' in notes and 'rebuild' in notes
          and 'expected_page_event_possible' in notes and 'receiver' not in h['window']['why'][:0] and 'conservative software gate' in h['window']['why'],
          '%s: the notes say answer key, policy label, how to rebuild a piconet burst and that the four-ID rule is the receiver\'s' % label)
    check('air time' in h['burst_window'] and '40' in h['burst_window'], '%s: the burst window is defined' % label)
    check("gzip.open('synth_<name>.bursts.jsonl.gz', 'rt')" in notes and 'json.loads(line)' in notes, '%s: the notes carry the read-it snippet' % label)
    no_id = ('There is no ID packet and no FHS packet' in notes and 'NULL and POLL, 126 bits' in notes and 'role switch' in notes
             and 'no role-switch traffic' in notes and 'exist only' not in notes)
    check(no_id == (profile == 'connonly'), '%s: the connonly notes say IDs do not exist in the connection state and NULL/POLL are the shortest packets (only there)' % label)
    check('"bursts_not_rendered"' not in json.dumps(h) and 'in bursts_not_rendered' not in json.dumps(h) and (profile == 'connonly') == ('There are no joiners' in h['conformance'])
          and (profile == 'connonly' or 'inquiry response' in h['conformance']), '%s: no stale bursts_not_rendered; conformance matches the profile' % label)
    if profile != 'connonly':
        n_pg = sum(r['kind'] == 'page' for r in h['exchanges'])
        n_b = sum(r['kind'] == 'page' and r['train'] == 'B' for r in h['exchanges'])
        c = h['conformance']
        check('%d of the %d page exchanges here begin on the B train' % (n_b, n_pg) in c and 'EXCERPT' in c and 'preceding repetitions are not in the file' in c
              and all(w in c for w in ('repetition count', 'interlaced scan', 'RAND back-off', 'retransmitted FHS')),
              '%s: the conformance text says %d of %d page exchanges start on the B train (computed from the plan), are excerpts, and lists the caveats' % (label, n_b, n_pg))
    check('at least 4 of its train' in notes and 'also drawn again' in notes and 'not in the task' in notes, '%s: the notes state both selection criteria' % label)


def all_laps(h):
    laps = [d['lap'] for d in h['devices']] + [d['paged']['lap'] for d in h['devices'] if d.get('paged')]
    return laps


def check_identities(h, B, label, profile):
    devs = h['devices']
    pic = [d for d in devs if d['kind'] == 'piconet']
    joi = [d for d in devs if d['kind'] != 'piconet']
    check([d['id'] for d in devs] == list(range(len(devs))) and len(pic) == N_PIC and all(d['id'] < N_PIC for d in pic),
          '%s: devices 0-%d are the piconets, the joiners follow' % (label, N_PIC - 1))
    check(sum(d['ours'] for d in pic) == N_PIC - 2 and all(d['ours'] for d in joi) and len(h['not_ours_devices']) == 2
          and h['not_ours_devices'] == [d['id'] for d in pic if not d['ours']],
          '%s: 8 of the 10 piconets are ours, 2 not; the joiners are ours' % label)
    by = {d['id']: d for d in devs}
    check(all(e['ours'] == by[e['device']]['ours'] for e in B), '%s: every burst\'s ours is its device\'s' % label)
    laps = all_laps(h)
    check(len(set(laps)) == len(laps), '%s: all %d LAPs of piconets, masters, paged devices and scanners are different' % (label, len(laps)))
    check(all(not 0x9E8B00 <= x < 0x9E8B40 and x != GIAC and x != 0 for x in laps), '%s: no LAP is reserved, the GIAC or 0' % label)
    sw = {x: int(''.join(map(str, br.sync_word(x))), 2) for x in laps + [GIAC]}
    d_all = min(bin(sw[a] ^ sw[b]).count('1') for i, a in enumerate(laps + [GIAC]) for b in (laps + [GIAC])[i + 1:])
    d_pic = min(bin(sw[a] ^ sw[b]).count('1') for i, a in enumerate(laps[:N_PIC]) for b in laps[i + 1:N_PIC])
    check(d_all == h['sync_word_min_distance_all'] and d_pic == h['sync_word_min_distance_piconets'] and d_all >= 22
          and h['sync_word_distance_required'] == 22,
          '%s: sync words at least 22 bits apart over %d LAPs and the GIAC: least %d (piconets %d), as stated' % (label, len(laps), d_all, d_pic))
    check(all(min(bin(sw[d['lap']] ^ sw[x]).count('1') for x in laps + [GIAC] if x != d['lap']) == d['sync_word_min_distance'] for d in devs),
          '%s: every device\'s sync_word_min_distance is true' % label)
    check(len({d['clk0'] for d in pic}) == N_PIC and all(d['clk0'] % 4 == 0 for d in pic), '%s: ten different master clocks, each a master slot\'s' % label)
    offs = sorted(d['slot_grid_offset_int'] for d in pic)
    gaps = [min(b - a, SLOT - (b - a)) for a, b in zip(offs, offs[1:] + [offs[0] + SLOT])]
    check(min(gaps) >= 800 and all(0 <= o < SLOT for o in offs), '%s: slot grids 0..24999, at least 800 samples apart (least %d)' % (label, min(gaps)))
    check(all(8.0 <= d['level_db'] <= 22.0 for d in pic), '%s: piconet levels 8-22 dB' % label)
    check(profile != 'connonly' or (not joi and h['exchanges'] == [] and h['n_joiners'] == 0 and h['joiners'] == 0 and h['negative_set'] is True),
          '%s: connonly has no joiner device, an empty exchange list, joiners 0 and negative_set true' % label)


def check_counts_and_order(h, B, rendered, label):
    per = {}
    for e in B:
        per.setdefault(str(e['device']), {})
        per[str(e['device'])][e['kind']] = per[str(e['device'])].get(e['kind'], 0) + 1
    check(per == h['counts_per_device_and_type'] and {k: sum(v.values()) for k, v in per.items()} == h['counts_per_device'],
          '%s: counts per device and type are what the bursts hold' % label)
    types = {}
    for e in B:
        types[e['kind']] = types.get(e['kind'], 0) + 1
    check(types == h['counts_per_type'] and h['n_bursts'] == len(B) and h['n_bursts_rendered'] == len(rendered)
          and h['n_bursts_not_rendered'] == len(B) - len(rendered), '%s: counts per type, n_bursts and the rendered counts are right' % label)
    n_c = sum(e['collision'] for e in B)
    check(n_c == h['n_collisions'] and abs(h['fraction_collided'] - n_c / len(rendered)) < 1e-12,
          '%s: n_collisions %d and fraction_collided %.4f are right' % (label, n_c, h['fraction_collided']))
    check(all(e['index'] == i for i, e in enumerate(B)) and all(window_start(a) <= window_start(c) for a, c in zip(B, B[1:])),
          '%s: bursts are sorted by start_sample + timing_frac and index is the position' % label)
    check(all(e['in_window'] == e['rendered'] == (27 <= e['channel'] <= 51) for e in B), '%s: rendered iff the channel is 27-51' % label)
    check(all(e['end_sample'] == e['start_sample'] + e['air_bits_length'] * SPS for e in rendered)
          and all(e['symbol_phase'] == e['start_sample'] % SPS for e in B), '%s: end_sample and symbol_phase are right' % label)
    check(all(e['kind'] == e['ptype'] for e in B), '%s: kind is the packet type' % label)


def window_start(e):
    return e['start_sample'] + e['timing_frac']


def check_overlap_truth(B, rendered, label):
    """The truth of every burst against an independent sweep, over the rendered bursts (indices through the full list)."""
    pos = {e['index']: k for k, e in enumerate(rendered)}
    over, frac = independent_overlaps(rendered)
    bad, n_pairs = [], 0
    for k, e in enumerate(rendered):
        got = {pos[r['burst']]: r for r in e['overlaps']}
        want = over[k]
        n_pairs += len(want)
        if set(got) != set(want):
            bad.append((e['index'], 'set'))
            continue
        for j, r in got.items():
            o = rendered[j]
            if (r['device'] != o['device'] or abs(r['overlap_samples'] - want[j]) > 1e-6
                    or r['channel_offset_mhz'] != o['channel'] - e['channel'] or abs(r['sir_db'] - (e['snr_db'] - o['snr_db'])) > 1e-3):
                bad.append((e['index'], 'field', j))
        same = any(r['channel_offset_mhz'] == 0 for r in e['overlaps'])
        if e['collision'] != same or abs(e['overlap_frac'] - frac[k]) > 1e-9:
            bad.append((e['index'], 'collision/frac'))
    check(not bad, '%s: overlaps, channel offset, overlap samples, SIR, collision and overlap_frac of EVERY one of the %d rendered bursts '
          'equal the independent sweep (%d pairs) %s' % (label, len(rendered), n_pairs // 2, bad[:3]))
    sym = all(any(r2['burst'] == e['index'] and r2['channel_offset_mhz'] == -r['channel_offset_mhz'] and abs(r2['sir_db'] + r['sir_db']) < 1e-3
                  for r2 in B[r['burst']]['overlaps']) for e in rendered[:20000] for r in e['overlaps'])
    check(sym, '%s: every overlap is listed from both sides with the offset and the SIR reversed (first 20000 bursts)' % label)
    adj = sum(1 for e in rendered for r in e['overlaps'] if abs(r['channel_offset_mhz']) == 1)
    same = sum(1 for e in rendered for r in e['overlaps'] if r['channel_offset_mhz'] == 0)
    check(adj > 0 and same > 0, '%s: both same-channel (%d) and adjacent-channel (%d) overlaps exist, so a list that left one out would show' % (label, same // 2, adj // 2))
    check(all(e['overlaps'] == [] and not e['collision'] and e['overlap_frac'] == 0 and e['air_bits'] is None for e in B if not e['rendered']),
          '%s: bursts that are not in the samples have no overlaps and no air_bits' % label)


ANSWER_PDUS = tm.ANSWER_PDUS
CENTRAL_ONLY = tm.CENTRAL_ONLY


def check_lmp(B, label):
    bad, n = [], {'master': 0, 'slave': 0}
    for e in B:
        if e['device'] >= N_PIC:
            if e['lmp_name'] is not None or e['llid'] == 3:
                bad.append((e['index'], 'LMP on a joiner burst'))
            continue
        if e['llid'] != 3:
            if e['lmp_name'] is not None or e['lmp_opcode'] is not None or e['lmp_tid'] is not None or e['lmp_params'] is not None:
                bad.append((e['index'], 'lmp truth on a non-LMP burst'))
            continue
        if e['ptype'] != 'DM1':
            bad.append((e['index'], 'LMP outside a DM1'))
            continue
        try:
            d = lmpt.decode(bytes.fromhex(e['payload_hex']))
        except ValueError as x:
            bad.append((e['index'], str(x)))
            continue
        n[e['role']] += 1
        params = {k: (v.hex() if isinstance(v, bytes) else v) for k, v in d['fields'].items()}
        if (d['name'] != e['lmp_name'] or d['opcode'] != e['lmp_opcode'] or d['tid'] != e['lmp_tid'] or params != e['lmp_params']
                or lmpt.validate(d) or e['payload_length'] != d['length']):
            bad.append((e['index'], 'sidecar disagrees with the PDU', d['name']))
        if e['role'] == 'slave' and d['name'] in CENTRAL_ONLY:
            bad.append((e['index'], 'a Peripheral sends a Central-only PDU', d['name']))
        want_tid = int(d['name'] in ANSWER_PDUS) if e['role'] == 'master' else int(d['name'] not in ANSWER_PDUS)
        if d['tid'] != want_tid:
            bad.append((e['index'], 'TID', e['role'], d['name'], d['tid']))
    check(not bad, '%s: LMP only in piconet DM1; every PDU (%d master, %d slave) parsed from its payload equals lmp_*, is allowed from its '
          'sender (a slave never sends LMP_set_AFH) and has the TID of its role %s' % (label, n['master'], n['slave'], bad[:3]))
    check(n['slave'] >= 3 and n['master'] >= 3, '%s: both roles send LMP (%d master, %d slave)' % (label, n['master'], n['slave']))
    llid_bad = [e['index'] for e in B if e['ptype'] in ('NULL', 'POLL', 'ID', 'FHS') and e['llid'] is not None and e['device'] < N_PIC]
    check(not llid_bad, '%s: no llid on a packet without a payload header' % label)


def check_piconets(h, B, label, full):
    devs = {d['id']: d for d in h['devices']}
    by = {}
    for e in B:
        if e['device'] < N_PIC:
            by.setdefault(e['device'], []).append(e)
    nslots = {t: v[1] for t, v in br.PACKET_TYPES.items()}
    bad = []
    for d, bs in by.items():
        bs = sorted(bs, key=window_start)
        for a, c in zip(bs, bs[1:]):
            if window(a)[1] + 200 > window(c)[0]:
                bad.append((d, a['index'], c['index']))
    check(not bad, '%s: no two bursts of one piconet overlap in time (nor come within 200 samples) %s' % (label, bad[:3]))
    n_pairs = n_multi = 0
    bad = []
    for d in range(N_PIC):
        dv = devs[d]
        addr = dv['uap'] << 24 | dv['lap']
        bs = by[d]
        masters = {e['pair']: e for e in bs if e['role'] == 'master'}
        for e in bs:
            if e['role'] == 'master':
                clk_m = (dv['clk0'] + 2 * e['piconet_slot']) & 0x0FFFFFFF
                if e['clk'] != clk_m or e['hop_clk'] != clk_m or e['piconet_slot'] % 2 or clk_m % 4:
                    bad.append(('master clk', e['index']))
                hc = clk_m
            else:
                m = masters.get(e['pair'])
                if m is None:
                    bad.append(('slave without master', e['index']))
                    continue
                n = nslots[m['ptype']]
                n_pairs += 1
                n_multi += n > 1
                clk_m = (dv['clk0'] + 2 * m['piconet_slot']) & 0x0FFFFFFF
                if (e['piconet_slot'] != m['piconet_slot'] + n or e['start_sample'] - m['start_sample'] != n * SLOT
                        or e['clk'] != (clk_m + 2 * n) & 0x0FFFFFFF or e['hop_clk'] != clk_m or e['channel'] != m['channel']
                        or e['ptype'] not in ('NULL', 'DM1', 'DH1')):
                    bad.append(('slave slot', e['index']))
                hc = clk_m
            if e['channel'] != bt_hop.hop_channel(hc, addr, MASK) or not 31 <= e['channel'] <= 50 or e['channel_mhz'] != 2402 + e['channel']:
                bad.append(('hop', e['index']))
            x = e['start_sample'] + e['timing_frac'] - (g.GRID_BASE + dv['slot_grid_offset_samples'] + SLOT * e['piconet_slot'])
            if not -1 <= x < 41:
                bad.append(('grid', e['index'], x))
            if e['symbol_phase'] not in (0, 20) or e['lt_addr'] != 1 or e['flow'] != 1 or e['arqn'] != 1 or e['lap'] != dv['lap'] or e['uap'] != dv['uap']:
                bad.append(('fields', e['index']))
        ms = sorted(masters.values(), key=lambda e: e['piconet_slot'])
        for a, c in zip(ms, ms[1:]):
            if c['piconet_slot'] < a['piconet_slot'] + nslots[a['ptype']] + 1 or c['piconet_slot'] % 2:
                bad.append(('master spacing', c['index']))
    check(not bad, '%s: slaves follow their master by n slots on its channel; clocks and hops recomputed from clk0 and the address; grid, phase, fields %s'
          % (label, bad[:3]))
    check(n_pairs > 100 and n_multi > 3, '%s: %d slave answers, %d of them to multi-slot packets' % (label, n_pairs, n_multi))
    # the traffic profile: the master's transmit rate over its master slots
    prof = h['profile']
    p = pm.PROFILES[prof]
    ranges = []
    for count, lo, hi in p['p_master_tx']:
        ranges += [(lo, hi)] * count
    tol = 0.03 if full else 0.12
    bad = []
    for d in range(N_PIC):
        ms = [e for e in by[d] if e['role'] == 'master']
        used = sum(nslots[e['ptype']] + 1 for e in ms)
        span = max(e['piconet_slot'] for e in ms) + 2
        idle = (span - used) / 2
        meas = len(ms) / (len(ms) + max(idle, 0))
        want = devs[d]['traffic']['p_master_tx']
        if abs(meas - want) > tol or not ranges[d][0] <= want <= ranges[d][1]:
            bad.append((d, round(meas, 3), want))
    check(not bad, '%s: each master\'s measured transmit rate is its p_master_tx (+-%.2f) and p_master_tx is in the profile\'s range %s' % (label, tol, bad[:3]))
    mp = [e for e in B if e['device'] < N_PIC and e['role'] == 'master']
    share = sum(e['ptype'] in ('NULL', 'POLL') for e in mp) / len(mp)
    lo, hi = (0.70, 0.90) if prof == 'pagemix' else (0.85, 0.95)
    check(lo <= share <= hi, '%s: NULL and POLL are %.1f %% of the master packets (%s: %.0f-%.0f %%)' % (label, 100 * share, prof, 100 * lo, 100 * hi))
    if prof == 'pagemix':
        dm1 = [e for e in mp if e['ptype'] == 'DM1']
        check(0.3 < sum(e['llid'] == 3 for e in dm1) / len(dm1) < 0.7, '%s: LMP is in about half of the master DM1' % label)
    sp = [e for e in B if e['device'] < N_PIC and e['role'] == 'slave']
    if prof == 'connonly':
        check(sum(e['ptype'] == 'NULL' for e in sp) / len(sp) > 0.93, '%s: slave answers are NULL almost always (%.1f %%)' % (label, 100 * np.mean([e['ptype'] == 'NULL' for e in sp])))
    mpair = {(e['device'], e['pair']): e for e in mp}
    gap = [e['start_sample'] - mpair[(e['device'], e['pair'])]['start_sample'] for e in sp if mpair[(e['device'], e['pair'])]['ptype'] in ('NULL', 'POLL')]
    check(gap and set(gap) == {SLOT}, '%s: an answer to a NULL or POLL sits exactly %d samples (625 us, a page ID\'s response spacing) after it: %d answers' % (label, SLOT, len(gap)))
    kinds = {e['ptype'] for e in B if e['device'] < N_PIC}
    check(kinds <= {'NULL', 'POLL', 'DM1', 'DH1', 'DM3', 'DH3', 'DM5', 'DH5'} and {'NULL', 'POLL', 'DM1', 'DH1'} <= kinds,
          '%s: piconet packet types are connection-state types only: %s' % (label, sorted(kinds)))
    check(all(window(e)[1] - window(e)[0] < SLOT * nslots[e['ptype']] - 40 for e in B if e['device'] < N_PIC), '%s: a packet\'s air time fits inside its slots' % label)


def hop_of(e, dev_by_ex):
    h = e['hop']
    d = dev_by_ex
    if e['role'] == 'id_page':
        return hs.page(h['clke'], d['paged']['lap'], d['paged']['uap'], h['koffset'], h['knudge'])
    if e['role'] in ('id_response', 'id_ack'):
        return hs.peripheral_page_response(h['clkn_frozen'], h['clkn'], d['paged']['lap'], d['paged']['uap'], h['n'])
    if e['role'] == 'fhs':
        return hs.central_page_response(h['clke_frozen'], h['clke'], d['paged']['lap'], d['paged']['uap'], h['koffset'], h['knudge'], h['n'])
    if e['role'] == 'id_inquiry':
        return hs.inquiry(h['clkn'], h['koffset'], h['knudge'])
    if e['role'] == 'fhs_inquiry_response':
        return hs.inquiry_response(h['clkn'], h['n'])
    return bt_hop.hop_channel(h['clk'], d['uap'] << 24 | d['lap'], None)


def check_joiners(h, B, label, full):
    from apps import bt_fhs
    ex_rows = h['exchanges']
    n = len(ex_rows)
    check(n == h['n_joiners'] and [r['exchange'] for r in ex_rows] == list(range(n)), '%s: %d exchanges, numbered from 0' % (label, n))
    dev = {d['id']: d for d in h['devices']}
    bad, n_follow = [], 0
    for r in ex_rows:
        d = dev[r['device']]
        bs = [e for e in B if e['device'] == r['device']]
        origin = r['start_sample']
        for e in bs:
            if e['exchange'] != r['exchange'] or e['ours'] is not True:
                bad.append(('exchange/ours', e['index']))
            if hop_of(e, d) != e['channel']:
                bad.append(('channel', e['role'], e['index']))
            if e['start_sample'] != origin + e['tick'] * 12500 or e['symbol_phase'] != (origin + e['tick'] * 12500) % SPS:
                bad.append(('tick', e['index']))
            if e['role'].startswith('followup'):
                n_follow += 1
                want = d['lap']
                if e['clk'] != e['hop']['clk'] and e['role'] == 'followup_master':
                    bad.append(('clk', e['index']))
                if e['role'] == 'followup_slave' and e['clk'] % 4 != 2:
                    bad.append(('slave clk', e['index']))
            elif e['kind'] == 'ID' and r['kind'] == 'inquiry':
                want = GIAC
            elif e['role'] == 'fhs_inquiry_response':
                want = GIAC
            else:
                want = d['paged']['lap']
            if e['lap'] != want:
                bad.append(('lap', e['index']))
            if e['rendered'] and e['role'] in ('fhs', 'fhs_inquiry_response'):
                bits = hex_bits(e['air_bits'], e['air_bits_length'])
                if r['kind'] == 'page':
                    x = bt_fhs.xprc(e['hop']['clke_frozen'], e['hop']['koffset'], e['hop']['knudge'], e['hop']['n'])
                    f = bt_fhs.page_response(d['paged']['lap'], d['paged']['uap'], d['lap'], d['uap'], d['nap'], d['cod'], 1, e['clk'], x, sr=e['fhs']['sr'])
                    if bits != list(f.bits):
                        bad.append(('fhs bits', e['index']))
        roles = [e['role'] for e in bs]
        if r['kind'] == 'page':
            if (roles.count('fhs') != 1 or roles.count('id_response') != 1 or roles.count('id_ack') != 1 or roles.count('followup_master') != 12
                    or roles.count('followup_slave') != 12 or not roles.count('id_page')):
                bad.append(('structure', r['exchange']))
        else:
            if roles.count('fhs_inquiry_response') != 1 or not roles.count('id_inquiry') or any(x.startswith('followup') for x in roles):
                bad.append(('structure', r['exchange']))
    check(not bad, '%s: joiners\' channels recomputed from bt_hop_substates and the basic kernel, ticks, symbol phases, LAPs, structure %s' % (label, bad[:3]))
    check(sum(r['kind'] == 'page' for r in ex_rows) == h['n_page_joiners'] and sum(r['kind'] == 'inquiry' for r in ex_rows) == h['n_inquiry_joiners']
          and [d['kind'] for d in h['devices'] if d['id'] >= N_PIC] == ['joiner_' + r['kind'] for r in ex_rows],
          '%s: %d page and %d inquiry exchanges, devices of kind joiner_page / joiner_inquiry' % (label, h['n_page_joiners'], h['n_inquiry_joiners']))
    org = sorted(r['start_sample'] for r in ex_rows)
    check(all(b - a > 400000 for a, b in zip(org, org[1:])), '%s: the exchanges do not overlap each other in time' % label)
    ph = [r['start_sample'] % SPS for r in ex_rows]
    check(ph == [r['symbol_phase_start'] for r in ex_rows] == h['joiner_symbol_phases'] and not set(ph) <= {0, 20}
          and len(set(ph)) >= (10 if full else 2) and all(0 <= p < SPS for p in ph),
          '%s: symbol phases of the exchanges are random over 0..39 (%d distinct of %d: %s)' % (label, len(set(ph)), len(ph), sorted(ph)))
    odd = {(e['symbol_phase'] - r['symbol_phase_start']) % SPS for r in ex_rows for e in B if e['device'] == r['device']}
    check(odd <= {0, 20}, '%s: within an exchange the phases are p and p + 20 mod 40 (offsets %s)' % (label, sorted(odd)))
    snr_ok = all(14.0 <= r['snr_base_db'] <= 22.0 and abs(r['snr_base_db'] * 10 - round(r['snr_base_db'] * 10)) < 1e-6 for r in ex_rows)
    jit = [e['snr_db'] - next(r for r in ex_rows if r['device'] == e['device'])['snr_base_db'] for e in B if e['device'] >= N_PIC]
    check(snr_ok and max(abs(np.array(jit))) <= 3.0 + 1e-6, '%s: exchange SNR bases are 14-22 dB in tenths, burst jitter within +-3 dB (worst %.2f, sigma %.2f)'
          % (label, max(abs(np.array(jit))), np.std(jit)))
    n_in = sum(r['fhs_rendered'] for r in ex_rows)
    want_in = n - max(1, n // 5) if n >= 2 else n
    check(n_in == want_in and (not full or (n_in == 16 and n == 20)),
          '%s: the FHS is in the window in %d of %d exchanges (rule: %d)' % (label, n_in, n, 16 if full else want_in))
    check(all(r['n_draws'] >= 1 for r in ex_rows) and h['joiner_selection']['total_draws'] == sum(r['n_draws'] for r in ex_rows),
          '%s: the number of draws is reported (%d in all)' % (label, h['joiner_selection']['total_draws']))
    check(n_follow > 0 or not any(r['kind'] == 'page' for r in ex_rows), '%s: page follow-up is in the file (%d bursts)' % (label, n_follow))


def check_exchange_table(h, B, label):
    """The exchange rows recomputed from the burst list."""
    bad = []
    for r in h['exchanges']:
        bs = [e for e in B if e['device'] == r['device']]
        ren = [e for e in bs if e['rendered']]
        ids = [e for e in bs if e['role'] in ('id_page', 'id_inquiry')]
        ids_r = [e for e in ids if e['rendered']]
        ids_cf = [e for e in ids_r if not e['collision']]
        fhs = [e for e in bs if e['ptype'] == 'FHS']
        heard = [e for e in ids if e['tick'] == r['heard_tick']]
        pic = [e for e in ren if any(B[o['burst']]['device'] < N_PIC for o in e['overlaps'])]
        want = dict(n_bursts_total=len(bs), n_bursts_rendered=len(ren), n_id=len(ids), n_id_rendered=len(ids_r),
                    n_id_rendered_collision_free=len(ids_cf), heard_id_rendered=heard[0]['rendered'],
                    fhs_rendered=any(e['rendered'] for e in fhs), fhs_collided=any(e['rendered'] and e['collision'] for e in fhs),
                    n_followup_rendered=sum(e['rendered'] for e in bs if e['role'].startswith('followup')),
                    first_id_start_sample=min(e['start_sample'] for e in ids),
                    first_rendered_id_start_sample=min((e['start_sample'] for e in ids_r), default=None),
                    last_burst_end_sample=max(e['end_sample'] for e in ren), overlap_with_piconet_bursts=len(pic),
                    n_rendered_collided=sum(e['collision'] for e in ren),
                    expected_page_event_possible=len(ids_cf) >= 4)
        for k, v in want.items():
            if r[k] != v:
                bad.append((r['exchange'], k, r[k], v))
        if r['start_sample'] != r['first_id_start_sample'] or not r['clock_unique'] is None:
            bad.append((r['exchange'], 'start/clock'))
        if r['first_burst_index'] != bs[0]['index'] or r['last_burst_index'] != bs[-1]['index']:
            bad.append((r['exchange'], 'indices'))
    check(not bad, '%s: the exchange table (counts, FHS, first/last, expected_page_event_possible, overlap with piconet bursts) is what the burst list gives %s'
          % (label, bad[:3]))
    check(all(r['expected_page_event_possible'] == (r['n_id_rendered_collision_free'] >= 4) for r in h['exchanges'])
          and 'receiver' in ' '.join(h['notes']), '%s: expected_page_event_possible = (collision-free rendered IDs >= 4), a rule of the receiver' % label)


def check_selection(h, B, label):
    """The selection contract per exchange: the wanted flag equals the FHS's rendered status, wanted exchanges have >= 4 rendered
    train IDs, and the recorded draws are replayed: every attempt is rebuilt from its recorded seed and LAPs and must be rejected
    for the reason it gives, the last one accepted, and n_draws is their number."""
    bad, replayed = [], 0
    for r in h['exchanges']:
        bs = [e for e in B if e['device'] == r['device']]
        fhs_r = any(e['rendered'] and e['ptype'] == 'FHS' for e in bs)
        n_ids = sum(e['rendered'] and e['role'] in ('id_page', 'id_inquiry') for e in bs)
        if r['fhs_wanted_in_window'] != fhs_r:
            bad.append((r['exchange'], 'wanted flag', r['fhs_wanted_in_window'], fhs_r))
        if r['fhs_wanted_in_window'] and n_ids < 4:
            bad.append((r['exchange'], 'fewer than 4 rendered train IDs', n_ids))
        att = r['selection_attempts']
        if len(att) != r['n_draws'] or not att or att[-1]['reason'] is not None or any(a['reason'] is None for a in att[:-1]) \
                or att[-1]['conf_seed'] != r['conf_seed'] or len({a['conf_seed'] for a in att}) != len(att):
            bad.append((r['exchange'], 'attempts', len(att), r['n_draws']))
            continue
        for a in att:
            if a['reason'] == 'lap_conflict':
                continue
            ident = dict(r['selection_base'], lap=a['lap'], paged_lap=a['paged_lap'])
            ex, _, _ = pm.build_exchange(r['kind'], a['conf_seed'], ident)
            replayed += 1
            if bool(ex['fhs_in_window']) != r['fhs_wanted_in_window']:
                why = 'fhs_position'
            elif r['fhs_wanted_in_window'] and ex['id_in_window'] < 4:
                why = 'ids_min'
            else:
                why = None
            if why != a['reason']:
                bad.append((r['exchange'], 'replay', a['conf_seed'], a['reason'], why))
    tot = sum(r['n_draws'] for r in h['exchanges'])
    check(h['joiner_selection']['total_draws'] == tot and h['joiner_selection']['draws_per_exchange'] == [r['n_draws'] for r in h['exchanges']],
          '%s: the total and per-exchange draws in joiner_selection are the exchanges\' own' % label)
    check(not bad, '%s: selection per exchange: wanted flag = FHS rendered, wanted exchanges have >= 4 rendered train IDs, and %d recorded attempts replayed '
          'from their seeds are rejected for the reason given, n_draws = number of attempts %s' % (label, replayed, bad[:3]))


def check_connonly_absence(B, label):
    kinds = {e['kind'] for e in B}
    check(kinds <= {'NULL', 'POLL', 'DM1', 'DH1', 'DM3', 'DH3', 'DM5', 'DH5'} and 'ID' not in kinds and 'FHS' not in kinds
          and {e['role'] for e in B} <= {'master', 'slave'} and all(e['device'] < N_PIC and e['exchange'] is None and e['rendered'] for e in B),
          '%s: no ID, FHS or joiner burst anywhere; every kind is NULL/POLL/data, every role master or slave (%d bursts)' % (label, len(B)))


def plan_checks(plan, label, full=False, profile=None):
    """Every check that needs no samples."""
    h = plan.header
    profile = profile or h['profile']
    B = list(plan.bursts())
    rendered = [e for e in B if e['rendered']]
    check_keys(h, label, profile)
    check_identities(h, B, label, profile)
    check_counts_and_order(h, B, rendered, label)
    check_overlap_truth(B, rendered, label)
    check_lmp(B, label)
    check_piconets(h, B, label, full)
    if profile == 'connonly':
        check_connonly_absence(B, label)
    if h['n_joiners']:
        check_joiners(h, B, label, full)
        check_exchange_table(h, B, label)
        check_selection(h, B, label)
    return B


# --- the samples ---------------------------------------------------------------------------------

def my_noise(seed, total, block):
    return tm.my_noise(seed, total, block)


def superposition(iq, B, label, seed, block, plan_total):
    total = len(iq)
    noise = my_noise(seed, total, block)
    rest = iq - noise
    del noise
    exp = np.zeros(total, dtype=np.complex64)
    cover = np.zeros(total, dtype=np.int8)
    by_ch = {}
    rendered = [e for e in B if e['rendered']]
    spans = {}
    for e in rendered:
        lo, x = my_burst(e)
        exp[lo:lo + len(x)] += x
        cover[lo:lo + len(x)] += 1
        by_ch.setdefault(e['channel'], []).append((lo, lo + len(x)))
        spans[e['index']] = (lo, lo + len(x))
    same = np.zeros(total, dtype=np.int8)
    for ch, iv in by_ch.items():
        arr = np.zeros(total, dtype=np.int8)
        for lo, hi in iv:
            arr[lo:hi] += 1
        np.maximum(same, arr, out=same)
        del arr
    amp_max = np.sqrt(NOISE * 10 ** (max(e['snr_db'] for e in rendered) / 10))
    err = np.abs(rest - exp)
    overl = same >= 2
    check(len(iq) == plan_total, '%s: n_samples %d is the length of the array' % (label, plan_total))
    check(err.max() < 1e-4 * amp_max + 1e-6, '%s: the file less the noise is the sum of the %d bursts rendered alone (piconets rebuilt from their header fields), worst difference %.2e (a burst has %.3f)'
          % (label, len(rendered), err.max(), amp_max))
    check(overl.sum() > 5000 and err[overl].max() < 1e-4 * amp_max + 1e-6, '%s: and in the %d samples where two bursts are on one channel at once, worst %.2e' % (label, overl.sum(), err[overl].max()))
    check(np.abs(rest[cover == 0]).max() < 1e-6, '%s: where no burst is, the file is the noise to the bit' % label)
    return iq - exp, cover


def check_noise(rest, label):
    """The residual after the bursts is the floor: white (lag 1 and 2) with the constant variance."""
    z = rest.astype(np.complex128)
    p = np.mean(np.abs(z) ** 2)
    r1 = abs(np.vdot(z[:-1], z[1:])) / (len(z) - 1) / p
    r2 = abs(np.vdot(z[:-2], z[2:])) / (len(z) - 2) / p
    rr = abs(np.mean(z.real[:-1] * z.real[1:])) / (p / 2)
    check(abs(10 * np.log10(p / NOISE_POWER)) < 0.02 and r1 < 0.002 and r2 < 0.002 and rr < 0.002,
          '%s: the noise floor is white: power %+.4f dB from the stated %.3e, lag-1 %.4f, lag-2 %.4f, real lag-1 %.4f (1/sqrt(N) = %.4f)'
          % (label, 10 * np.log10(p / NOISE_POWER), NOISE_POWER, r1, r2, rr, 1 / np.sqrt(len(z))))


def check_40_per_symbol(B, label):
    """A packet's samples: 40 per symbol exactly, as the modulator makes them and as the file's geometry says."""
    e = next(e for e in B if e['rendered'] and e['kind'] == 'NULL')
    bits = bits_of(e)
    burst, lead = br.gfsk(np.asarray(bits), FS, h=br.GFSK_H)
    check(len(burst) == len(bits) * 40 + 2 * lead and lead == int(np.ceil(2e-6 * FS)) + 1 and br.SYMBOL_RATE == 1e6 and FS / br.SYMBOL_RATE == 40,
          '%s: the modulator makes 40 samples per symbol exactly (%d bits -> %d samples + 2 x %d of ramp)' % (label, len(bits), len(bits) * 40, lead))
    nxt = [x for x in B if x['rendered'] and x['device'] == e['device'] and x['index'] > e['index']][0]
    check(all(x['end_sample'] - x['start_sample'] == x['air_bits_length'] * 40 for x in B[:2000] if x['rendered']),
          '%s: every burst\'s air time is air_bits_length x 40 samples' % label)


def check_container(mod, plan, tmp, label):
    """The written truth read back equal to the plan; sorted; index = line; header counts = lines; deterministic bytes."""
    s1, b1 = mod.write_truth(plan, os.path.join(tmp, 'a'), 'x')
    s2, b2 = mod.write_truth(plan, os.path.join(tmp, 'b'), 'x')
    time.sleep(1.1)
    s3, b3 = mod.write_truth(plan, os.path.join(tmp, 'c'), 'x')
    md = lambda p: hashlib.md5(open(p, 'rb').read()).hexdigest()
    check(md(b1) == md(b2) == md(b3) and md(s1) == md(s2) == md(s3), '%s: the same plan written three times (one a second later, other folders) gives the same bytes' % label)
    raw = open(b1, 'rb').read(12)
    check(raw[:2] == b'\x1f\x8b' and raw[4:8] == b'\0\0\0\0' and raw[3] == 0, '%s: the gzip header has mtime 0 and no file name' % label)
    h = json.load(open(s1))
    lines = list(pm.read_bursts(b1))
    exp = [json.loads(json.dumps(b)) for b in plan.bursts()]
    check(lines == exp, '%s: the %d lines read back are the plan\'s bursts, key for key' % (label, len(lines)))
    check(all(b['index'] == i for i, b in enumerate(lines)) and all(window_start(a) <= window_start(c) for a, c in zip(lines, lines[1:])),
          '%s: index is the line number and the lines are sorted by start_sample + timing_frac' % label)
    check(h['n_bursts'] == len(lines) and h['n_bursts_rendered'] == sum(b['rendered'] for b in lines)
          and h['n_collisions'] == sum(b['collision'] for b in lines)
          and sum(h['counts_per_device'].values()) == len(lines) and sum(h['counts_per_type'].values()) == len(lines),
          '%s: the header\'s counts (bursts, rendered, collisions, per device, per type) equal the lines' % label)
    check(h['bursts_head'] == lines[:200] and h['bursts_file'] == 'synth_x.bursts.jsonl.gz' and h['bursts_format'] == 'jsonl.gz',
          '%s: bursts_head is the first 200 lines and bursts_file names the container' % label)
    keys = set().union(*(set(b) for b in lines))
    check(keys == set(h['per_burst_keys']), '%s: per_burst_keys is the union of the lines\' keys' % label)
    bk = h['per_burst_keys_by_kind']
    check(all(set(b) == set(bk['piconet'] if b['device'] < N_PIC else bk['joiner']) | (set(bk['fhs_extra']) if b['device'] >= N_PIC and b['kind'] == 'FHS' else set())
              for b in lines), '%s: every line has exactly the keys of its kind' % label)
    check(all(b.get('air_bits') is None and 'body_crc_bits_hex' not in b for b in lines if b['device'] < N_PIC) or
          all('air_bits' not in b for b in lines if b['device'] < N_PIC), '%s: piconet lines carry no air_bits' % label)
    return h, lines


# --- the blind check ---------------------------------------------------------------------------------

def scan(iq, laps, channels, nbits=72, threshold=0.5, base=0):
    """tm.blind_scan with a template of ``nbits`` access-code bits (68 for an ID packet, 72 when a trailer follows)."""
    dec = tm.DEC
    taps = firwin(101, 0.7e6, fs=FS)
    n = base + np.arange(len(iq))
    found = {(lap, ch): [] for lap in laps for ch in channels}
    tmpl = {}
    for lap in laps:
        a = np.array(br.access_code(lap)[:nbits]) * 2.0 - 1.0
        t = np.repeat(a, SPS // dec).astype(float)
        tmpl[lap] = t - t.mean()
    for ch in channels:
        cycles = (ch + 2402 - CENTER) * 1e6 / FS * n
        z = np.asarray(iq).astype(np.complex128) * np.exp(-2j * np.pi * (cycles - np.floor(cycles)))
        y = upfirdn(taps, z, up=1, down=dec)[(len(taps) - 1) // 2 // dec:]
        d = np.angle(y[1:] * np.conj(y[:-1]))
        dm = d - np.convolve(d, np.ones(2001 // dec) / (2001 // dec), 'same')
        energy = np.cumsum(np.concatenate([[0.0], dm ** 2]))
        for lap in laps:
            t = tmpl[lap]
            c = fftconvolve(dm, t[::-1], 'valid')
            en = np.sqrt(np.maximum(energy[len(t):] - energy[:-len(t)], 1e-12)) * np.linalg.norm(t)
            r = c / np.maximum(en[:len(c)], 1e-12)
            peaks = []
            for j in np.flatnonzero(r > threshold):
                if peaks and j - peaks[-1] < 3000 // dec:
                    if r[j] > r[peaks[-1]]:
                        peaks[-1] = j
                else:
                    peaks.append(j)
            found[(lap, ch)] = [base + dec * p for p in peaks]
    return found


def strangers_for(h, count=3):
    rng = np.random.default_rng([77, 1])
    table = set(all_laps(h)) | {GIAC}
    out = []
    while len(out) < count:
        x = int(rng.integers(1, 1 << 24))
        if x not in table and not 0x9E8B00 <= x < 0x9E8B40:
            out.append(x)
    return out


def blind_piconets(iq, B, h, label, base=0, report=None):
    """The ten piconets' LAPs (and three strangers) scanned on channels 27-51; each device's collision-free bursts are found."""
    laps = {d['id']: d['lap'] for d in h['devices'] if d['kind'] == 'piconet'}
    strangers = strangers_for(h)
    t0 = time.time()
    found = scan(iq, list(laps.values()) + strangers, list(range(27, 52)), base=base)
    n = len(iq)
    rend = [e for e in B if e['rendered']]
    inside = [e for e in rend if window_start(e) > base + 4000 and window(e)[1] < base + n - 4000]
    free = tm.free_bursts(dict(bursts=rend))
    free_ids = {rend[i]['index'] for i in free}
    rows = []
    for d, lap in laps.items():
        mine = [e for e in inside if e['device'] == d and e['index'] in free_ids]
        hit = sum(any(abs(p - window(e)[0]) < 150 for p in found[(lap, e['channel'])]) for e in mine)
        every = [e for e in inside if e['device'] == d]
        det = [(ch, p) for (l, ch), ps in found.items() if l == lap for p in ps]
        spurious = [x for x in det if not any(abs(x[0] - e['channel']) <= 1 and abs(x[1] - window(e)[0]) < 150 for e in every)]
        rows.append((d, len(mine), hit, len(det), len(spurious), h['devices'][d]['level_db'], h['devices'][d]['ours']))
    stranger_hits = sum(len(ps) for (l, ch), ps in found.items() if l in strangers)
    if report is not None:
        report.update(seconds=time.time() - t0, rows=rows, strangers=stranger_hits)
    for d, nfree, hit, ndet, nsp, level, ours in rows:
        weak = level < 12
        check(nfree >= 3 and hit >= (0.5 if weak else 0.9) * nfree and nsp <= max(1, 0.02 * ndet),
              '%s: blind, piconet %d (%.1f dB%s, %s): %d of %d collision-free bursts found, %d detections, %d spurious'
              % (label, d, level, ', weak' if weak else '', 'ours' if ours else 'not ours', hit, nfree, ndet, nsp))
    check(stranger_hits == 0, '%s: blind, 3 LAPs that are not in the table are found %d times on 25 channels' % (label, stranger_hits))


def blind_joiner(iq, B, h, ex, label, base):
    """Around one exchange: the paged LAP (page) or the GIAC (inquiry) as a 68-bit template on 25 channels; the exchange's
    collision-free IDs are found, a stranger LAP never."""
    d = next(x for x in h['devices'] if x['id'] == ex['device'])
    lap = GIAC if ex['kind'] == 'inquiry' else d['paged']['lap']
    strangers = strangers_for(h, 2)
    found = scan(iq, [lap] + strangers, list(range(27, 52)), nbits=68, base=base)
    n = len(iq)
    ids = [e for e in B if e['device'] == ex['device'] and e['role'] in ('id_page', 'id_inquiry') and e['rendered'] and not e['collision']
           and window_start(e) > base + 3000 and window(e)[1] < base + n - 3000]
    free = [e for e in ids if not any(B[o['burst']]['channel'] == e['channel'] for o in e['overlaps'])]
    hit = sum(any(abs(p - window(e)[0]) < 150 for p in found[(lap, e['channel'])]) for e in free)
    others = [e for e in B if e['rendered'] and e['lap'] == lap and e['device'] == ex['device']]
    det = [(ch, p) for (l, ch), ps in found.items() if l == lap for p in ps]
    spurious = [x for x in det if not any(abs(x[0] - e['channel']) <= 1 and abs(x[1] - window(e)[0]) < 150 for e in others)]
    stranger = sum(len(ps) for (l, ch), ps in found.items() if l in strangers)
    check(len(free) >= 1 and hit >= 0.8 * len(free), '%s: blind, exchange %d (%s, %.1f dB): %d of %d collision-free rendered IDs found by the %s template (%d detections, %d not an ID of the exchange)'
          % (label, ex['exchange'], ex['kind'], ex['snr_base_db'], hit, len(free), 'GIAC' if ex['kind'] == 'inquiry' else 'paged-LAP', len(det), len(spurious)))
    check(stranger == 0, '%s: blind, exchange %d: two stranger LAPs found %d times' % (label, ex['exchange'], stranger))


# --- windows of a plan, rendered alone ---------------------------------------------------------------

def window_samples(plan, lo, hi, only=None):
    """Samples ``lo`` to ``hi`` of a plan's file (noise blocks and the bursts that reach them), without making the rest.
    ``only`` is a job index: that burst alone on the noise."""
    out = np.zeros(hi - lo, dtype=np.complex64)
    bs = plan.block_samples
    for index in range(lo // bs, (hi - 1) // bs + 1):
        b0, b1 = index * bs, min(plan.total, (index + 1) * bs)
        blk = pm.noise_block(plan.seed, index, b1 - b0, FS)
        a, b = max(lo, b0), min(hi, b1)
        out[a - lo:b - lo] = blk[a - b0:b - b0]
    for k, job in enumerate(plan.jobs):
        if only is not None and k != only:
            continue
        s0, s1 = g.pairs.burst_span(job, FS)
        if s1 <= lo or s0 >= hi:
            continue
        start, samples = g.pairs.render_burst(job, FS, CENTER)
        a, b = max(start, lo), min(start + len(samples), hi)
        if b > a:
            out[a - lo:b - lo] += samples[a - start:b - start]
    return out


def check_decode(plan, B, label):
    """Collision-free bursts of every packet type and every joiner role, from the samples of the file as it is (the neighbours
    on the air): carrier, start, level and bits from the samples alone, libbtbb on header and payload. 'Collision-free' is: no
    overlap listed at all (so none within one channel), and no other burst within 300 samples on a channel within ``span`` of
    its own (``span`` 1 for a piconet burst at 30 dB; 3 for a joiner burst at 14-22 dB, which a 30 dB neighbour two channels
    away could trouble). The carrier is also read from the burst on its own on the noise (the whole spectrum is then its)."""
    rend = [e for e in B if e['rendered']]
    free1 = {rend[i]['index'] for i in tm.free_bursts(dict(bursts=rend), margin=300, span=1)}
    free3 = {rend[i]['index'] for i in tm.free_bursts(dict(bursts=rend), margin=300, span=3)}
    picks, seen = [], {}
    for e in rend:
        key = e['ptype'] if e['device'] < N_PIC else e['role']
        ok = e['overlaps'] == [] and not e['collision'] and e['index'] in (free1 if e['device'] < N_PIC else free3)
        if ok and seen.get(key, 0) < (1 if e['device'] < N_PIC else 5):
            seen[key] = seen.get(key, 0) + 1
            picks.append(e)
    types = {e['ptype'] for e in picks if e['device'] < N_PIC}
    check({'NULL', 'POLL', 'DM1', 'DH1', 'DM3', 'DH3', 'DM5', 'DH5'} <= types, '%s: a collision-free burst of each of the 8 connection packet types was found (have %s)' % (label, sorted(types)))
    jroles = {e['role'] for e in picks if e['device'] >= N_PIC}
    check({'id_page', 'id_inquiry', 'followup_master'} <= jroles and jroles & {'fhs', 'fhs_inquiry_response'},
          '%s: and a collision-free joiner burst of the roles %s' % (label, sorted(jroles)))
    ok_ch, ok_alone, ok_bits, ok_btbb, ok_wrong, starts, lvls, ber = [], [], [], [], [], [], [], []
    for e in picks:
        s0 = max(0, int(window(e)[0]) - 3000)
        s1 = min(plan.total, int(window(e)[1]) + 3000)
        off = int(window(e)[0]) - s0
        nb = e['air_bits_length']
        alone = window_samples(plan, s0, s1, only=plan.rendered_pos[e['index']])
        mhz, ch = tm.carrier_of(alone, off + 300, off + nb * SPS - 300)
        ok_alone.append(ch == e['channel'] and abs(mhz - e['channel_mhz']) < 0.15)
        iq = window_samples(plan, s0, s1)
        ee = dict(e, start_sample=e['start_sample'] - s0)
        want = bits_of(e)
        coarse, got = tm.coarse_demod(iq, off, off + nb * SPS, e['channel_mhz'], nb, e['lap'])
        errs = int(np.sum(np.asarray(got) != np.asarray(want)))
        ber.append(errs / nb)
        exact = e['snr_db'] > 25
        if exact:
            ok_bits.append(errs == 0)
        fit = tm.fit_burst(iq, ee, got if exact else np.array(want), coarse)
        starts.append(fit['start'] - (ee['start_sample'] + e['timing_frac']))
        lvls.append(10 * np.log10(fit['amp'] ** 2 / NOISE) - e['snr_db'])
        if exact and e['device'] < N_PIC:
            hdr_ok, verdict, got_bytes = tm.btbb(got, e, e['uap'])
            ok_btbb.append(hdr_ok and verdict == 10 and got_bytes == bytes.fromhex(e['payload_full_hex'] or ''))
            h2, v2, _ = tm.btbb(got, e, e['uap'] ^ 0x10)
            ok_wrong.append(not (h2 and v2 == 10 and e['payload_full_hex']) if e['payload_full_hex'] else True)
    starts, lvls = np.array(starts), np.array(lvls)
    hi = np.array([e['snr_db'] > 25 for e in picks])
    check(all(ok_alone), '%s: the carrier of %d chosen bursts, from the spectrum of each on its own, is the channel to 150 kHz (%d)' % (label, len(picks), sum(ok_alone)))
    sj = starts[~hi]
    check((np.abs(starts[hi]) <= 0.5).all() and abs(sj.mean()) < 0.3 and np.sqrt(np.mean(sj ** 2)) < 0.8 and np.abs(sj).max() < 2.0,
          '%s: start_sample + timing_frac is where the burst\'s model fits (neighbours on the air): worst %.3f samples at 30 dB (limit 0.5); the %d joiner bursts at 14-22 dB '
          '(estimator scatter grows as the SNR falls): mean %+.2f, rms %.2f, worst %.2f (limits 0.3, 0.8, 2.0)'
          % (label, np.abs(starts[hi]).max(), len(sj), sj.mean(), np.sqrt(np.mean(sj ** 2)), np.abs(sj).max()))
    check(np.abs(lvls).max() < 0.7, '%s: level from the samples is snr_db within 0.7 dB, worst %.3f' % (label, np.abs(lvls).max()))
    check(all(ok_bits) and ok_bits, '%s: bits demodulated from the samples at 30 dB equal the rebuilt bits (%d of %d) - the 40 samples per symbol hold' % (label, sum(ok_bits), len(ok_bits)))
    jb = [b for e, b in zip(picks, ber) if e['device'] >= N_PIC]
    jsnr = [e['snr_db'] for e in picks if e['device'] >= N_PIC]
    check(jb and max(jb) < 0.08 and np.mean(jb) < 0.02,
          '%s: joiner bursts, demodulated by a plain discriminator at their 11-25 dB in 1 MHz, are within 8 %% bit errors (worst %.3f, mean %.4f over %d at %.1f-%.1f dB; random bits would give 50 %%)'
          % (label, max(jb), np.mean(jb), len(jb), min(jsnr), max(jsnr)))
    check(len(ok_btbb) >= 8 and all(ok_btbb), '%s: libbtbb decodes header and payload at the device\'s UAP and clock, one collision-free burst of each of the 8 types (%d of %d)' % (label, sum(ok_btbb), len(ok_btbb)))
    check(all(ok_wrong), '%s: and refuses the payloads at another UAP' % label)
    return picks


def libbtbb_fhs():
    import ctypes
    lib = tm._lib
    for name, ret in (('btbb_packet_get_type', ctypes.c_uint8), ('lap_from_fhs', ctypes.c_uint32), ('uap_from_fhs', ctypes.c_uint8),
                      ('nap_from_fhs', ctypes.c_uint16), ('clock_from_fhs', ctypes.c_uint32)):
        fn = getattr(lib, name)
        fn.argtypes = [ctypes.c_void_p]
        fn.restype = ret
    return lib


def parse_fhs_payload(payload):
    """The 144 payload bits of an FHS (Core Vol 2 Part B Figure 6.9), written again: fields least significant bit first."""
    bits = [(byte >> i) & 1 for byte in payload for i in range(8)]
    f = lambda a, n: sum(bits[a + i] << i for i in range(n))
    return dict(lap=f(34, 24), uap=f(64, 8), nap=f(72, 16), class_of_device=f(88, 24), am_addr=f(112, 3), clk27_2=f(115, 26))


def check_fhs_decode(mod, label):
    """A small plan whose joiners are at 30-34 dB: every collision-free rendered FHS, of the page kind (paged UAP, X-input whitening)
    and of the inquiry kind (DCI 0x00 initialisation), demodulated from the samples and decoded by libbtbb (payload verdict 1000 = a
    valid FHS, CRC included); its parsed fields equal the sidecar's."""
    import ctypes
    from unittest import mock
    lib = libbtbb_fhs()
    with mock.patch.object(mod, 'JOINER_SNR_RANGE', (30.0, 34.0)):
        plan = small_plan(mod, blocks=12, joiners=(3, 3), level_range=HIGH)
    B = list(plan.bursts())
    rend = [e for e in B if e['rendered']]
    free = {rend[i]['index'] for i in tm.free_bursts(dict(bursts=rend), margin=300, span=3)}
    got_kinds, bad = set(), []
    for e in rend:
        if e['ptype'] != 'FHS' or e['overlaps'] or e['index'] not in free:
            continue
        kind = 'page' if e['role'] == 'fhs' else 'inquiry'
        s0, s1 = max(0, int(window(e)[0]) - 3000), min(plan.total, int(window(e)[1]) + 3000)
        off = int(window(e)[0]) - s0
        nb = e['air_bits_length']
        iq = window_samples(plan, s0, s1)
        coarse, bits = tm.coarse_demod(iq, off, off + nb * SPS, e['channel_mhz'], nb, e['lap'])
        f = e['fhs']
        pkt = lib.btbb_packet_new()
        raw = bytes(int(b) for b in bits[4:])
        try:
            lib.btbb_packet_set_data(pkt, ctypes.cast(ctypes.c_char_p(raw), ctypes.POINTER(ctypes.c_char)), len(raw), 0, ((f['tx_clk'] >> 1) & 0x3F) << 1)
            lib.btbb_packet_set_uap(pkt, f['header_uap'])
            lib.btbb_packet_set_flag(pkt, 4, 1)
            lib.btbb_packet_set_flag(pkt, 0, 1)
            if lib.btbb_decode_header(pkt) != 1:
                bad.append((e['index'], 'header'))
                continue
            verdict = lib.btbb_decode_payload(pkt)
            buf = (ctypes.c_char * 4096)()
            lib.btbb_get_payload_packed(pkt, buf)
            want = bytes.fromhex(e['payload_full_hex'])
            parsed = parse_fhs_payload(bytes(buf[:len(want)]))
            mine = dict(lap=f['lap'], uap=f['uap'], nap=f['nap'], class_of_device=f['class_of_device'], am_addr=f['am_addr'], clk27_2=f['clk27_2'])
            lb = (lib.lap_from_fhs(pkt), lib.uap_from_fhs(pkt), lib.nap_from_fhs(pkt), lib.clock_from_fhs(pkt)) if verdict == 1000 else None
            ok = (verdict == 1000 and parsed == mine and lb == (f['lap'], f['uap'], f['nap'], f['clk27_2'])
                  and (kind == 'inquiry') == (f['access_lap'] == GIAC) and (kind == 'page' or f['header_uap'] == 0))
            if not ok:
                bad.append((e['index'], kind, verdict, parsed == mine))
            else:
                got_kinds.add(kind)
        finally:
            lib.btbb_packet_unref(pkt)
    check(got_kinds == {'page', 'inquiry'} and not bad,
          '%s: libbtbb decodes the rendered FHS of a page exchange (paged UAP) and of an inquiry exchange (DCI 0) from the samples with verdict 1000, and the fields '
          'parsed from the payload equal the sidecar\'s (kinds decoded %s) %s' % (label, sorted(got_kinds), bad[:3]))


def check_doc_snippet(mod, label):
    """The read-it example of the module docstring runs against a written container."""
    import textwrap
    lines = mod.__doc__.split('\n')
    i = next(k for k, ln in enumerate(lines) if ln.strip() == 'import gzip, json')
    j = i
    while j < len(lines) and lines[j].strip():
        j += 1
    code = textwrap.dedent('\n'.join(lines[i:j]))
    plan = small_plan(blocks=6, joiners=(1, 1))
    with tempfile.TemporaryDirectory() as tmp:
        _, b = pm.write_truth(plan, tmp, 'pagemix')
        code = code.replace('synth_pagemix.bursts.jsonl.gz', b).replace('...', 'n += 1')
        ns = {'n': 0}
        try:
            exec(compile('n = 0\n' + code, 'docstring', 'exec'), ns)
            ok = ns['n'] == plan.header['n_bursts'] and ns['burst']['index'] == plan.header['n_bursts'] - 1
        except Exception as x:
            ok = False
            code = 'raised %s' % x
    check(ok, '%s: the docstring\'s read-it example runs and reads every line of a written container' % label)


# --- mutants ---------------------------------------------------------------------------------------------------

def mutant_module(old, new):
    """The generator with one mistake."""
    assert SRC.count(old) == 1, 'mutation target not unique: %r (%d)' % (old, SRC.count(old))
    spec = importlib.util.spec_from_file_location('bt_synth_pagemix_mut', pm.__file__)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod                        # so that the forked planners can pickle its functions by name
    exec(compile(SRC.replace(old, new), pm.__file__, 'exec'), mod.__dict__)
    return mod


def plain_module():
    spec = importlib.util.spec_from_file_location('bt_synth_pagemix_mut', pm.__file__)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    exec(compile(SRC, pm.__file__, 'exec'), mod.__dict__)
    return mod


def mutate_fhs(old, new):
    """apps/bt_fhs.py with one mistake, as a module."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'apps', 'bt_fhs.py')
    src = open(path).read()
    assert src.count(old) == 1, 'mutation target not unique in bt_fhs'
    spec = importlib.util.spec_from_file_location('bt_fhs_mut', path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    exec(compile(src.replace(old, new), path, 'exec'), mod.__dict__)
    return mod


def mutate(old, new, target='pm'):
    if target == 'pm':
        return mutant_module(old, new)
    mixed_src = open(g.__file__).read()
    assert mixed_src.count(old) == 1, 'mutation target not unique in mixed: %r' % old
    spec = importlib.util.spec_from_file_location('bt_synth_mixed_mut', g.__file__)
    mx = importlib.util.module_from_spec(spec)
    exec(compile(mixed_src.replace(old, new), g.__file__, 'exec'), mx.__dict__)
    mod = plain_module()
    mod.g = mx
    return mod


def check_mutants(full_pagemix_ok):
    print('\nMutants: one mistake each, and what catches it', flush=True)
    cases = [
        # (name, old, new, target, kind)  kind: plan (small pagemix), conn (small connonly), full (full pagemix), container
        ('a joiner in the connonly plan', "seed=9302, blocks=600, n_piconets=10, n_not_ours=2, n_page=0, n_inquiry=0,",
         "seed=9302, blocks=600, n_piconets=10, n_not_ours=2, n_page=2, n_inquiry=0,", 'pm', 'conn_joiner'),
        ('a joiner\'s FHS out of the window counted as rendered', "fhs_rendered=any(b['rendered'] for b in fhs),", "fhs_rendered=bool(fhs),", 'pm', 'plan'),
        ('the FHS selection dropped (no rejection)', "if bool(ex['fhs_in_window']) != need_fhs:", "if False:", 'pm', 'full'),
        ('symbol phases of all exchanges 0', "phase = int(jr.integers(0, SPS))", "phase = 0", 'pm', 'plan'),
        ('the exchange SNR out of 14-22', "JOINER_SNR_RANGE = (14.0, 22.0)", "JOINER_SNR_RANGE = (10.0, 20.0)", 'pm', 'plan'),
        ('the overlap list missing the adjacent channels', "abs(channels[i] - channels[j]) <= 1", "abs(channels[i] - channels[j]) == 0", 'pm', 'plan'),
        ('a slave sending LMP_SET_AFH', "tuple(n for n in LMP_NAMES if n != 'LMP_set_AFH')", "tuple(n for n in LMP_NAMES)", 'mixed', 'plan'),
        ('expected_page_event_possible computed with collided IDs', "expected_page_event_possible=len(ids_cf) >= 4,", "expected_page_event_possible=len(ids_r) >= 4,", 'pm', 'full'),
        ('the container not sorted', "items.sort(key=lambda t: (t[0]['start_sample'] + t[0]['timing_frac'], t[0]['device'], t[0]['channel']))",
         "items.sort(key=lambda t: (t[0]['device'], t[0]['start_sample'] + t[0]['timing_frac'], t[0]['channel']))", 'pm', 'plan'),
        ('a non-deterministic gzip mtime', "compresslevel=6, mtime=0)", "compresslevel=6, mtime=time.time_ns() % 2 ** 31)", 'pm', 'container'),
        ('an ours swap on one piconet', "device=dev['id'], role=b['role'], ours=dev['ours'],",
         "device=dev['id'], role=b['role'], ours=(not dev['ours']) if dev['id'] == 0 else dev['ours'],", 'pm', 'plan'),
        ('the 4-ID rule removed', "if need_fhs and ex['id_in_window'] < ids_min:", "if False:", 'pm', 'full'),
        ('the FHS-position rejection removed', "if bool(ex['fhs_in_window']) != need_fhs:", "if False:", 'pm', 'plan'),
        ('four n_draws inflated by 100 and the total by 400', "n_draws=draws, attempts=attempts,", "n_draws=draws + (100 if j < 4 else 0), attempts=attempts,", 'pm', 'plan'),
        ('the docstring read-it example iterating a closed file', "          for line in f:                                  # one line at a time: up to a million lines\n              burst = json.loads(line)\n              ...                                         # use it here, inside the with block\n",
         "      bursts = (json.loads(line) for line in f)\n\n      burst = next(bursts)\n", 'pm', 'doc'),
        ('the inquiry-FHS CRC initialised with 1 instead of 0', 'payload += br.crc16(payload, self.header_uap)',
         'payload += br.crc16(payload, self.header_uap ^ (1 if self.access_lap == GIAC else 0))', 'fhs', 'fhs_crc'),
        ('n_samples off by one', "'n_samples': int(plan.total),", "'n_samples': int(plan.total) + 1,", 'pm', 'plan'),
        ('piconet bursts carrying the wrong LAP in their header', "air_bits_length=len(bits), lap=dev['lap'], uap=dev['uap']",
         "air_bits_length=len(bits), lap=dev['lap'] ^ 1, uap=dev['uap']", 'pm', 'superpos'),
    ]
    full = full_pagemix_ok
    for name, old, new, target, kind in cases:
        with quiet() as failed:
            try:
                mod = mutate_fhs(old, new) if target == 'fhs' else mutate(old, new, target)
                if kind == 'conn_joiner':
                    plan = small_plan(mod, 'connonly', blocks=6, joiners=(2, 0))
                    plan_checks(plan, 'mutant', profile='connonly')
                elif kind == 'full':
                    plan = mod.plan_file('pagemix')
                    plan_checks(plan, 'mutant', full=True)
                elif kind == 'doc':
                    check_doc_snippet(mod, 'mutant')
                elif kind == 'fhs_crc':
                    from unittest import mock
                    with mock.patch.object(pm.conf, 'bt_fhs', mod):
                        check_fhs_decode(pm, 'mutant')
                elif kind == 'container':
                    plan = small_plan(mod)
                    with tempfile.TemporaryDirectory() as tmp:
                        check_container(mod, plan, tmp, 'mutant')
                elif kind == 'superpos':
                    plan = small_plan(mod, blocks=1, joiners=(0, 0))
                    iq = np.concatenate(list(mod.render_blocks(plan)))
                    B = list(plan.bursts())
                    superposition(iq, B, 'mutant', plan.seed, plan.block_samples, plan.total)
                else:
                    plan = small_plan(mod)
                    plan_checks(plan, 'mutant')
            except Exception as e:                       # a crash is a catch too, said so
                failed.append('raised %s: %s' % (type(e).__name__, str(e)[:70]))
        short = sorted({w.split(':')[0][:30] + ':' + w.split(':')[-1][:60] for w in failed})
        by_check = [w for w in failed if not w.startswith('raised')]
        check(len(by_check) > 0, 'mutant "%s" is caught by %d checks (%d crashes), e.g. %s' % (name, len(by_check), len(failed) - len(by_check), '; '.join(short[:2])))
    with quiet() as failed:
        plan_checks(small_plan(), 'control')
    check(not failed, 'the unmutated plan passes every plan check (positive control) %s' % failed[:2])


# --- the written files -------------------------------------------------------------------------------

def verify_written(directory, name, seconds):
    h = json.load(open(os.path.join(directory, 'synth_%s.json' % name)))
    iq_path = os.path.join(directory, 'synth_%s.cf32' % name)
    bpath = os.path.join(directory, h['bursts_file'])
    size = os.path.getsize(iq_path)
    check(size == 8 * h['n_samples'], '%s: the file is %d bytes = 8 x n_samples %d' % (name, size, h['n_samples']))
    n = 0
    last = -1.0
    ok_sorted, kinds, counts, coll = True, {}, {}, 0
    head = []
    B = []
    for i, b in enumerate(pm.read_bursts(bpath)):
        ok_sorted &= b['index'] == i and window_start(b) >= last
        last = window_start(b)
        kinds[b['kind']] = kinds.get(b['kind'], 0) + 1
        counts[b['device']] = counts.get(b['device'], 0) + 1
        coll += b['collision']
        if i < 200:
            head.append(b)
        n = i + 1
        if h['n_joiners'] or window_start(b) < seconds * FS + 400000:
            B.append(b)
    check(n == h['n_bursts'] and ok_sorted and head == h['bursts_head'] and kinds == h['counts_per_type']
          and {str(k): v for k, v in counts.items()} == h['counts_per_device'] and coll == h['n_collisions'],
          '%s: container read back: %d lines = n_bursts, sorted, index = line, head and counts equal the header, %d collided (%.2f %%)'
          % (name, n, coll, 100 * coll / h['n_bursts_rendered']))
    if h['negative_set']:
        check(not any(k in kinds for k in ('ID', 'FHS')) and h['exchanges'] == [] and h['joiners'] == 0 and h['n_joiners'] == 0
              and all(b['device'] < N_PIC and b['exchange'] is None for b in pm.read_bursts(bpath)),
              '%s: zero joiner, ID or FHS bursts in the %d lines; exchanges empty' % (name, n))
    count = int(seconds * FS)
    iq = np.fromfile(iq_path, dtype='<c8', count=count)
    rep = {}
    print('Blind check of the first %.2f s' % seconds, flush=True)
    first = [b for b in B if b['rendered']]
    # indices stay the file's: use the burst list with its own indices for overlap references
    Bfull = {b['index']: b for b in B}
    blind_piconets(iq, B if not h['n_joiners'] else B, h, name + ' first %.1f s' % seconds, report=rep)
    print('  scan time %.0f s; rows (device, free bursts, found, detections, spurious, level, ours): %s' % (rep['seconds'], rep['rows']))
    if h['n_joiners']:
        for kind in ('page', 'inquiry'):
            ex = next(r for r in h['exchanges'] if r['kind'] == kind and r['n_id_rendered_collision_free'] >= 1)
            lo = max(0, ex['first_id_start_sample'] - 30000)
            hi = min(h['n_samples'], ex['first_id_start_sample'] + 500000)
            seg = np.fromfile(iq_path, dtype='<c8', count=hi - lo, offset=8 * lo)
            # the bursts of the window with the file's indices
            win_b = [b for b in pm.read_bursts(bpath) if lo - 150000 < window_start(b) < hi]
            Bw = {b['index']: b for b in win_b}
            lst = [Bw[k] for k in sorted(Bw)]

            # blind_joiner reads B[o['burst']]: a mapping by the file's index
            Bmap = type('M', (), {'__getitem__': lambda self, k: Bw[k], '__iter__': lambda self: iter(lst)})()
            blind_joiner(seg, Bmap, h, ex, '%s exchange %d window' % (name, ex['exchange']), lo)
    return h


# --- main -----------------------------------------------------------------------------------------------------

def main():
    if '--verify' in sys.argv:
        i = sys.argv.index('--verify')
        directory, name = sys.argv[i + 1], sys.argv[i + 2]
        seconds = float(sys.argv[sys.argv.index('--seconds') + 1]) if '--seconds' in sys.argv else 0.3
        verify_written(directory, name, seconds)
        fails = [w for ok, w in RESULTS if not ok]
        print('RESULT: PASS' if not fails else 'RESULT: FAIL (%d)' % len(fails))
        sys.exit(1 if fails else 0)
    quick = '--quick' in sys.argv
    t0 = time.time()
    print('Small run A (pagemix, 6 noise blocks of 2**22, 4 joiners)', flush=True)
    plan = small_plan()
    B = plan_checks(plan, 'A')
    print('\nThe samples of A', flush=True)
    iq = np.concatenate(list(pm.render_blocks(plan, workers=CORES)))
    ref = np.concatenate(list(g.render_blocks(plan.jobs, plan.total, plan.seed, plan.block_samples)))
    check(np.array_equal(iq, ref), 'A: the parallel renderer gives the very samples of bt_synth_mixed.render_blocks on the same jobs (bitwise)')
    del ref
    noise_est, cover = superposition(iq, B, 'A', plan.seed, plan.block_samples, plan.total)
    check_noise(noise_est, 'A (the file less its bursts)')
    check_40_per_symbol(B, 'A')
    del noise_est, cover
    print('\nA with noise blocks of 2**17 (many seams)', flush=True)
    plan_s = small_plan(blocks=192, block_samples=BLOCK_SMALL)
    check(plan_s.total == plan.total, 'A2: the same total as A')
    iq_s = np.concatenate(list(pm.render_blocks(plan_s, workers=CORES)))
    B_s = list(plan_s.bursts())
    noise_est, cover = superposition(iq_s, B_s, 'A2', plan_s.seed, plan_s.block_samples, plan_s.total)
    check_noise(noise_est, 'A2 (the file less its bursts)')
    del noise_est, cover, iq_s

    print('\nThe blind check of A (the sidecar is not read for the scan)', flush=True)
    rep = {}
    n_first = 2 ** 22
    Bf = [e for e in B if e['rendered']]
    blind_piconets(iq[:n_first], B, plan.header, 'A first 0.105 s', report=rep)
    print('  (scan took %.0f s; per device found/free: %s)' % (rep['seconds'], ' '.join('%d:%d/%d' % (r[0], r[2], r[1]) for r in rep['rows'])), flush=True)
    for r in plan.header['exchanges']:
        if r['n_id_rendered_collision_free'] >= 1 and r['kind'] in ('page', 'inquiry'):
            pass
    shown = set()
    for r in plan.header['exchanges']:
        if r['kind'] in shown or r['n_id_rendered_collision_free'] < 1:
            continue
        shown.add(r['kind'])
        lo = max(0, r['first_id_start_sample'] - 30000)
        hi = min(plan.total, r['first_id_start_sample'] + 500000)
        blind_joiner(iq[lo:hi], B, plan.header, r, 'A exchange window', lo)
    del iq

    print('\nA 30 dB plan, rendered window by window', flush=True)
    plan_h = small_plan(blocks=12, joiners=(3, 3), level_range=HIGH)
    B_h = list(plan_h.bursts())
    check_decode(plan_h, B_h, 'H')

    check_fhs_decode(pm, 'F')
    check_doc_snippet(pm, 'D')

    print('\nThe container (small plans, pagemix and connonly)', flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        check_container(pm, plan, tmp, 'A')
    plan_c = small_plan(profile='connonly', blocks=6, joiners=(0, 0))
    plan_checks(plan_c, 'C')
    with tempfile.TemporaryDirectory() as tmp:
        check_container(pm, plan_c, tmp, 'C')
        pm.write_file('z', tmp, 'connonly', blocks=1, n_page=0, n_inquiry=0)
        check(os.path.getsize(os.path.join(tmp, 'synth_z.cf32')) == 8 * 2 ** 22 == 8 * json.load(open(os.path.join(tmp, 'synth_z.json')))['n_samples'],
              'C: the written cf32 is 8 x n_samples bytes')
    iq_c = np.concatenate(list(pm.render_blocks(plan_c, workers=CORES)))
    Bc = list(plan_c.bursts())
    noise_est, cover = superposition(iq_c, Bc, 'C', plan_c.seed, plan_c.block_samples, plan_c.total)
    check_noise(noise_est, 'C (the file less its bursts)')
    del iq_c, noise_est, cover

    if not quick:
        print('\nThe FULL plans (no samples)', flush=True)
        t1 = time.time()
        full_a = pm.plan_file('pagemix')
        print('  pagemix planned in %.0f s' % (time.time() - t1), flush=True)
        plan_checks(full_a, 'full pagemix', full=True)
        ha = full_a.header
        print('  pagemix: %d bursts, %d rendered, %.2f %% collided (piconets %.2f %%), counts per type %s' % (
            ha['n_bursts'], ha['n_bursts_rendered'], 100 * ha['fraction_collided'], 100 * ha['fraction_piconet_collided'], ha['counts_per_type']))
        print('  joiner table: exchange kind start phase snr ids/cf fhs exp_possible')
        for r in ha['exchanges']:
            print('   %2d %-7s %10d %2d %5.1f %d/%d %s %s' % (r['exchange'], r['kind'], r['start_sample'], r['symbol_phase_start'], r['snr_base_db'],
                                                         r['n_id_rendered'], r['n_id_rendered_collision_free'], r['fhs_rendered'], r['expected_page_event_possible']))
        del full_a
        t1 = time.time()
        full_c = pm.plan_file('connonly')
        print('  connonly planned in %.0f s' % (time.time() - t1), flush=True)
        plan_checks(full_c, 'full connonly', full=True)
        hc = full_c.header
        print('  connonly: %d bursts, %.2f %% collided, counts per type %s' % (hc['n_bursts'], 100 * hc['fraction_collided'], hc['counts_per_type']))
        check(0.10 <= hc['fraction_collided'] <= 0.30, 'full connonly: the collision fraction %.1f %% is in the target 10-30 %%' % (100 * hc['fraction_collided']))
        check(hc['n_samples'] == 600 * 2 ** 22 and ha['n_samples'] == 300 * 2 ** 22, 'full: 600 and 300 whole noise blocks (%d and %d samples)' % (hc['n_samples'], ha['n_samples']))
        check(hc['seed'] == SEED_B and ha['seed'] == SEED_A, 'full: seeds 9302 and 9301')
        del full_c
        check_mutants(True)
    else:
        print('\n(--quick: the full plans and the mutants are skipped)')
    print('\n%.0f s' % (time.time() - t0))
    fails = [w for ok, w in RESULTS if not ok]
    print('RESULT: PASS' if not fails else 'RESULT: FAIL (%d)' % len(fails))
    for w in fails:
        print('  FAIL', w)
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
