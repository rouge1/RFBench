#!/usr/bin/env python3
"""Recover a voice superframe from ANY found SYNC: a CSBK, an Idle, the other slot.
Synthetic calls only.

`voice_calls(..., recover=True)` used to time a missing burst A from a neighbouring
superframe or from a voice LC header, a PI header or a terminator. Every burst of one
slot is a multiple of 288 symbols from every other burst of that slot, and the other
slot of a repeater's stream is 144 symbols away, so any found data or voice SYNC fixes
the burst grid; the EMB and LCSS rule chooses the phase (which of the grid positions
a superframe starts on). This file builds the cases that rule has to get right and,
as much, the ones it has to refuse. One summary line per case, then PASS or FAIL.

    python scripts/test_dmr_grid.py
"""
import os
import re
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dmr_demod as D  # noqa: E402
import dmr_fec as F  # noqa: E402
import dmr_synth as S  # noqa: E402
import test_dmr_recover as R  # noqa: E402

SPEC = R.SPEC
SPEC_DIR = R.SPEC_DIR
# check_spec compares these facts with the library: the slot length, the number of
# slots, and the CSBK and Idle data-type codes (Table 9.22).
N_SPEC_PINS = 3
DT = S.DT
failures = R.failures
ok = R.ok
fail = R.fail
RATES = [('48k', ['--fs', '48000']),
         ('1M +25 kHz +300 Hz', ['--fs', '1e6', '--offset', '25000', '--cfo', '300'])]


# --- spec pins ----------------------------------------------------------------------
def spec_slot_syms(txt):
    """Symbols in a timeslot, and how many slots there are, from the definition of the
    timeslot ("30 ms ... numbered "1" or "2"") and the symbol rate."""
    m = re.search(r'timeslot[^\n]*\n\s*\n\s*NOTE:\s+A timeslot has a length of (\d+) ms and '
                  r'will be numbered\s+"(\d)" or "(\d)"', txt)
    if not m:
        raise ValueError('the text does not define a timeslot as N ms numbered "1" or "2"')
    ms, lo, hi = int(m.group(1)), int(m.group(2)), int(m.group(3))
    rates = set(re.findall(r'(\d)\s+(\d{3})\s+symbols/s', txt))
    if len(rates) != 1:
        raise ValueError(f'symbol rate not a single figure in the text: {rates}')
    hi_r, lo_r = rates.pop()
    syms = int(hi_r + lo_r) * ms / 1000.0
    if syms != int(syms):
        raise ValueError(f'{ms} ms is not a whole number of symbols')
    return int(syms), hi - lo + 1


def spec_data_types(txt):
    """{name: code} from Table 9.22 (Data Type information element content)."""
    i = txt.index('Table 9.22: Data Type information element content')
    seg = txt[i:txt.index('9.3.7', i)]
    out = {}
    for m in re.finditer(r'\b([01]{4})2\s+([A-Za-z0-9 ½¾]+?)\s*(?:\n|$)', seg):
        out[m.group(2).strip()] = int(m.group(1), 2)
    return out


def check_spec():
    """Return how many spec pins were skipped. Zero when the text was read."""
    if not os.path.exists(SPEC):
        print(f'SKIPPED {N_SPEC_PINS} spec pins ({SPEC_DIR}/ not present)', flush=True)
        return N_SPEC_PINS
    txt = open(SPEC).read()
    try:
        slot_syms, n_slots = spec_slot_syms(txt)
        types = spec_data_types(txt)
        _sf, spacing, _n = R.spec_superframe_syms(txt)
    except (ValueError, KeyError) as e:
        fail('grid spec parse', str(e))
        return 0
    bad = []
    if D.SLOT_SYMS != slot_syms:
        bad.append(f'SLOT_SYMS {D.SLOT_SYMS} != {slot_syms}')
    # A burst of one slot is every n_slots-th slot: the 60 ms the superframe sentence gives.
    if D.BURST_SPACING_SYMS != n_slots * slot_syms or spacing != n_slots * slot_syms:
        bad.append(f'burst spacing {D.BURST_SPACING_SYMS} / {spacing} != {n_slots} x {slot_syms}')
    if (types.get('CSBK'), types.get('Idle')) != (D.DATA_TYPES.index('CSBK'),
                                                  D.DATA_TYPES.index('Idle')):
        bad.append(f'data types CSBK/Idle {types.get("CSBK"), types.get("Idle")} != '
                   f'{D.DATA_TYPES.index("CSBK"), D.DATA_TYPES.index("Idle")}')
    if types.get('CSBK') != 3 or types.get('Idle') != 9:
        bad.append(f'Table 9.22: CSBK {types.get("CSBK")} Idle {types.get("Idle")}')
    if D.RECOVER_GRID_WINDOW < D.SUPERFRAME_BURSTS - 1:
        bad.append('the grid window does not reach every phase of a superframe')
    if bad:
        fail('grid spec pins', '; '.join(bad))
    else:
        ok(f'spec pins: a timeslot is {slot_syms} symbols, {n_slots} slots so a slot\'s bursts are '
           f'{spacing} apart, CSBK is data type {types["CSBK"]} and Idle {types["Idle"]} '
           f'(Table 9.22), the window reaches all {D.SUPERFRAME_BURSTS} phases')
    return 0


# --- building streams ---------------------------------------------------------------
def rand_frames(n_sf, seed):
    rng = np.random.default_rng(seed)
    return [rng.integers(0, 2, 72).tolist() for _ in range(18 * n_sf)]


def filler(rng):
    """A burst with no SYNC in it (constant symbols): occupies its place on the grid."""
    return ([0] * 264, {'kind': 'filler'})


def data(cc, kind, sync, seed):
    info = np.random.default_rng(seed).integers(0, 2, 196).tolist()
    return (D.data_burst(info, cc, DT[kind], sync), {'kind': 'data', 'data_type': kind})


def ms_file(tmp, bursts, mode, rate, snr=None, seed=1, slot=1):
    """Modulate a hand-made list of bursts the way dmr_synth does for --sync ms/direct1."""
    fs, off, cfo = rate
    x48, _env, starts = S.modulate_bursts(bursts, slot, 0.3, 0.3)
    y, ratio = S.render(x48, fs, off, cfo, snr, seed=seed)
    side = {'sample_rate': fs, 'offset_hz': off, 'sync': {'voice': S.SYNC_SETS[mode][0],
                                                          'data': S.SYNC_SETS[mode][1]}}
    return y, side, [round((s + 54 * D.SPS) * float(ratio)) for s in starts]


def rate_args(name):
    return {'48k': (48000.0, 0.0, 0.0), '1M': (1e6, 25e3, 300.0)}[name]


def a_pos(d, sample, k):
    """Index in d of the SYNC whose file sample is `sample` (the 101-tap filter's delay)."""
    return int(round(sample / k)) + 50


def exact(frames, ref):
    return frames is not None and len(frames) == len(ref) and \
        all(list(a) == list(b) for a, b in zip(frames, ref))


def old_recover(d, voice, **kw):
    """recover=True with the grid search taken out: what it returned before."""
    saved = D._recover_from_grid
    D._recover_from_grid = lambda *a, **k: None
    try:
        return D.voice_calls(d, voice, recover=True, **kw)
    finally:
        D._recover_from_grid = saved


# --- cases --------------------------------------------------------------------------
def case_bs_lone(tmp):
    """--sync bs, no header or terminator, burst A's SYNC blanked: the other slot's Idle
    bursts (and the CACH, which is not a SYNC) are all there is."""
    for rate_name, rate in RATES:
        for slot in (1, 2):
            for snr in (None, 18):
                snrargs = [] if snr is None else ['--snr', str(snr)]
                label = f'bs slot {slot} {rate_name} {"clean" if snr is None else "18 dB"}'
                n_exact = n_old = 0
                bad = ''
                for seed in range(3 if snr else 1):
                    y, side, ambe = R.synth(
                        tmp, 'bs', 1,
                        ['--sync', 'bs', '--cc', '3', '--slot', str(slot), '--headers', '0',
                         '--terminators', '0', '--superframes', '1', '--lead', '0.3',
                         '--tail', '0.3', '--seed', str(seed)] + rate + snrargs, seed=11 + seed)
                    d, voice = R.demod(y, side)
                    k = side['sample_rate'] / D.FS_SYM
                    a = [b for b in side['bursts'] if b.get('burst') == 'A'][0]
                    pos = a_pos(d, a['sync_sample'], k)
                    d_blank = R.blank_sync(d, pos)
                    types = {D.DATA_TYPES[dt] for _p, _c, dt, _e in
                             D.data_bursts(d_blank, side['sync']['data'])}
                    old = D.voice_calls(d_blank, voice, recover=False)
                    old_rec = old_recover(d_blank, voice)
                    new = D.voice_calls(d_blank, voice, recover=True)
                    if types - {'Idle'}:
                        bad = f'anchors were not all Idle: {types}'
                    elif old or old_rec:
                        bad = f'old found {len(old)}, old recover {len(old_rec)}'
                    elif len(new) == 1 and exact(new[0][1], ambe):
                        n_exact += 1
                        n_old += len(old_rec) == 0
                    else:
                        bad = f'new returned {len(new)} superframes, exact {[exact(f, ambe) for _, f in new]}'
                    if bad:
                        break
                if bad:
                    fail(label, bad)
                else:
                    ok(f'{label}: {n_exact} of {seed + 1} blanked A, old recover returned nothing, '
                       f'recover=True one bit-exact superframe from the other slot\'s Idle bursts')


def case_bs_two_sf(tmp):
    """Both A SYNCs blanked, two superframes: found from the Idle bursts, then walked."""
    for rate_name, rate in RATES:
        y, side, ambe = R.synth(
            tmp, 'bs2', 2,
            ['--sync', 'bs', '--cc', '3', '--slot', '1', '--headers', '0', '--terminators', '0',
             '--superframes', '2', '--lead', '0.3', '--tail', '0.3'] + rate, seed=12)
        d, voice = R.demod(y, side)
        k = side['sample_rate'] / D.FS_SYM
        for b in side['bursts']:
            if b.get('burst') == 'A':
                d = R.blank_sync(d, a_pos(d, b['sync_sample'], k))
        old = old_recover(d, voice)
        new = D.voice_calls(d, voice, recover=True)
        got = [R.sf_of(f, ambe) for _, f in new]
        if not old and got == [0, 1]:
            ok(f'bs two superframes, both A blanked, {rate_name}: old nothing, new both bit-exact')
        else:
            fail(f'bs two superframes {rate_name}', f'old {len(old)} new {got}')


def case_csbk(tmp):
    """A CSBK is the only anchor. Direct and MS streams, up to five bursts of other data
    between it and burst A, and a CSBK after the superframe instead of before."""
    cc = 5
    lc = F.full_lc(9, 1001, 'group')
    for mode, vsync, dsync in (('ms', 'MS_VOICE', 'MS_DATA'), ('direct1', 'TDMA1_VOICE', 'TDMA1_DATA')):
        for rname in ('48k', '1M'):
            rate = rate_args(rname)
            results = []
            for layout in ('csbk,A', 'csbk,x,A', 'csbk,x,x,A', 'csbk,x,x,x,A', 'csbk,x,x,x,x,A',
                           'A,csbk', 'A,x,csbk', 'csbk,A,csbk'):
                frames = rand_frames(1, 31)
                voice = [b for b in S.build_call(lc, cc, frames, mode, 0, 0)]
                bursts = []
                for tok in layout.split(','):
                    if tok == 'csbk':
                        bursts.append(data(cc, 'CSBK', dsync, 7 + len(bursts)))
                    elif tok == 'x':
                        bursts.append(filler(None))
                    else:
                        bursts += voice
                y, side, _ = ms_file(tmp, bursts, mode, rate, snr=None)
                d, vs = R.demod(y, side)
                base = D.voice_calls(d, vs, recover=False)
                if len(base) != 1 or not exact(base[0][1], frames):
                    fail(f'csbk {mode} {rname} {layout} setup', f'clean found {len(base)}')
                    continue
                d_blank = R.blank_sync(d, base[0][0])
                old = old_recover(d_blank, vs)
                data_found = D.data_bursts(d_blank, dsync)
                new = D.voice_calls(d_blank, vs, recover=True)
                results.append((layout, len(old), len(data_found),
                                [exact(f, frames) for _p, f in new]))
            bad = [r for r in results if r[1] != 0 or r[2] < 1 or r[3] != [True]]
            if bad:
                fail(f'csbk anchor {mode} {rname}', str(bad))
            else:
                ok(f'csbk anchor {mode} {rname}: {len(results)} layouts (CSBK 0-4 bursts before A, '
                   f'or after F), old recover nothing, new one bit-exact superframe each')
    # 18 dB, 1 MS/s: one noisy layout, seeds 0-9. Count; the old path returns nothing.
    n_ok = n_old = n_extra = 0
    for seed in range(10):
        frames = rand_frames(1, 40 + seed)
        voice = S.build_call(lc, cc, frames, 'ms', 0, 0)
        bursts = [data(cc, 'CSBK', 'MS_DATA', seed), data(cc, 'CSBK', 'MS_DATA', seed + 50)] + voice
        y, side, apos = ms_file(tmp, bursts, 'ms', rate_args('1M'), snr=18, seed=seed)
        d, vs = R.demod(y, side)
        k = side['sample_rate'] / D.FS_SYM
        d_blank = R.blank_sync(d, a_pos(d, apos[2], k))
        n_old += len(old_recover(d_blank, vs))
        new = D.voice_calls(d_blank, vs, recover=True)
        n_ok += len(new) == 1 and exact(new[0][1], frames)
        n_extra += max(0, len(new) - 1)
    if n_old == 0 and n_extra == 0 and n_ok >= 8:
        ok(f'csbk anchor 1M 18 dB, 10 seeds: {n_ok} bit-exact, old recover {n_old}, extras {n_extra}')
    else:
        fail('csbk anchor 18 dB', f'exact {n_ok}/10 old {n_old} extras {n_extra}')


def case_other_slot_voice(tmp):
    """A repeater with voice in BOTH slots, the same colour code, the same LCSS cycle
    and the second slot's superframe shifted by 0..5 bursts. Slot 1's A is blanked; slot
    2's is found. The only extra is slot 1's own superframe, and it is exact."""
    cc = 3
    lcA, lcB = F.full_lc(9, 1001, 'group'), F.full_lc(9, 2002, 'group')
    frA, frB = rand_frames(1, 51), rand_frames(1, 52)
    idle = (D.data_burst(F.bptc196_encode(F.IDLE_INFO96), cc, DT['Idle'], 'BS_DATA'),
            {'kind': 'data', 'data_type': 'Idle'})
    rows = []
    for shift in range(6):
        callA = S.build_call(lcA, cc, frA, 'bs', 0, 0)
        callB = [idle] * shift + S.build_call(lcB, cc, frB, 'bs', 0, 0)
        n = max(len(callA), len(callB))
        callA += [idle] * (n - len(callA))
        callB += [idle] * (n - len(callB))
        slots = []
        for i in range(n):
            for slot, call in ((1, callA), (2, callB)):
                tx = F.cach(F.tact(1, S.TC_OF_SLOT[slot], 0), [0] * 17)
                slots.append((call[i][0], tx, dict(call[i][1], slot=slot)))
        x48, _env, starts, _c = S.modulate_bs(slots, 0.3, 0.3)
        for rname in ('48k', '1M'):
            fs, off, cfo = rate_args(rname)
            y, ratio = S.render(x48, fs, off, cfo, None)
            side = {'sample_rate': fs, 'offset_hz': off, 'sync': {'voice': 'BS_VOICE'}}
            d, vs = R.demod(y, side)
            k = fs / D.FS_SYM
            base = D.voice_calls(d, vs, recover=False)
            posA = a_pos(d, round((starts[0] + 54 * D.SPS) * float(ratio)), k)
            posB = a_pos(d, round((starts[2 * shift + 1] + 54 * D.SPS) * float(ratio)), k)
            if sorted(exact(f, frA) or exact(f, frB) for _p, f in base) != [True, True]:
                rows.append((shift, rname, 'setup', len(base)))
                continue
            d_blank = R.blank_sync(d, posA)
            ref = old_recover(d_blank, vs)
            new = D.voice_calls(d_blank, vs, recover=True)
            got = sorted((p, 'A' if exact(f, frA) else 'B' if exact(f, frB) else '?')
                         for p, f in new)
            # slot 2's own superframe is found by SYNC; slot 1's is recovered, 144 symbols
            # earlier than slot 2's in the same burst number.
            want_gap = (posB - posA) % (D.BURST_SPACING_SYMS * D.SPS)
            near = abs(want_gap - D.SLOT_SYMS * D.SPS) <= 4 * D.SPS
            if [t for _p, t in got] == ['A', 'B'] or [t for _p, t in got] == ['B', 'A']:
                ok_ = near and len(ref) == 1
            else:
                ok_ = False
            if not ok_:
                rows.append((shift, rname, got, len(ref)))
    if rows:
        fail('other slot has voice', str(rows))
    else:
        ok('other slot has voice, same colour code, LCSS cycle shifted by 0-5 bursts, 48k and 1M: '
           'slot 2 (found by SYNC) and slot 1 (recovered, 144 symbols away) both bit-exact, '
           'nothing else, and the old recover returned only slot 2')


def case_same_slot_other_call(tmp):
    """A different call on the same slot. Call 1 (colour code 3, A blanked) is followed on
    the same burst grid by call 2 (colour code 5). Call 2's header and burst A anchor
    recover; call 1 is a real superframe but not of call 2's colour code, so it is NOT
    recovered from them. Only call 2's superframe comes back."""
    lc1, lc2 = F.full_lc(9, 1001, 'group'), F.full_lc(9, 2002, 'group')
    fr1, fr2 = rand_frames(1, 61), rand_frames(1, 62)
    call1 = S.build_call(lc1, 3, fr1, 'ms', 0, 0)
    call2 = S.build_call(lc2, 5, fr2, 'ms', 1, 1)
    bursts = call1 + call2
    out = []
    for rname in ('48k', '1M'):
        y, side, _a = ms_file(tmp, bursts, 'ms', rate_args(rname))
        d, vs = R.demod(y, side)
        base = D.voice_calls(d, vs, recover=False)
        if len(base) != 2:
            out.append((rname, 'setup', len(base)))
            continue
        d_blank = R.blank_sync(d, base[0][0])
        new = D.voice_calls(d_blank, vs, recover=True)
        out.append((rname, len(new), len(new) == 1 and exact(new[0][1], fr2)))
    if all(o[1] == 1 and o[2] for o in out):
        ok('another call on the same slot (colour code 3, then 5): the first, with A blanked, is '
           'not recovered from the second\'s anchors; the second comes back exact, 48k and 1M')
    else:
        fail('same slot, other call', str(out))


def case_shifted(tmp):
    """A superframe shifted by 1, 2 and 3 bursts (and 4, 5, and the same backwards) is not
    one. superframe_candidate at the true A plus k bursts of a three-superframe call:
    accepted only at 0 and at multiples of six."""
    lc = F.full_lc(9, 1001, 'group')
    frames = rand_frames(3, 71)
    bursts = S.build_call(lc, 3, frames, 'ms', 0, 0)
    spacing = D.BURST_SPACING_SYMS * D.SPS
    rows = []
    for rname in ('48k', '1M'):
        y, side, _a = ms_file(tmp, bursts, 'ms', rate_args(rname))
        d, vs = R.demod(y, side)
        base = D.voice_calls(d, vs, recover=False)
        if len(base) != 3:
            rows.append((rname, 'setup', len(base)))
            continue
        p1 = base[1][0]
        sc, off = D.fit_levels(d, p1, D.sync_symbols(vs))
        acc = {k for k in range(-5, 6)
               if D.superframe_candidate(d, p1 + k * spacing, sc, off, 3)[0]}
        # 6 bursts is a whole superframe, a real one; the 3-superframe call has it at +-6.
        acc6 = {k for k in (-6, 6)
                if D.superframe_candidate(d, p1 + k * spacing, sc, off, 3)[0]}
        if acc != {0} or acc6 != {-6, 6}:
            rows.append((rname, sorted(acc), sorted(acc6)))
    if rows:
        fail('shifted superframe', str(rows))
    else:
        ok('a superframe shifted by -5..+5 bursts: accepted at 0 only; at +-6 (a real neighbour) '
           'accepted; 48k and 1M')
    # And through voice_calls: anchors, but the superframe's own A SYNC is at k=1..5 bursts.
    # (case_csbk covers 0-4 bursts of other data in front of A.)


class Counter:
    """Wraps D.superframe_candidate to count what the grid search tries and what passes."""
    def __init__(self):
        self.n = self.ok = 0
        self.real = D.superframe_candidate
        D.superframe_candidate = self.call

    def call(self, *a, **k):
        r = self.real(*a, **k)
        self.n += 1
        self.ok += bool(r[0])
        return r

    def close(self):
        D.superframe_candidate = self.real


def case_noise(n_stretch=300):
    """Noise after a real CSBK, so the grid search has an anchor and a window of candidates:
    300 stretches, half squelched at a SNR that leaves noise above the gate. No superframe
    may be accepted. The counter says how many candidates were really tried."""
    step = D.SUPERFRAME_SYMS * D.SPS
    csbk = data(7, 'CSBK', 'BS_DATA', 3)
    cnt = Counter()
    returned = 0
    anchors = 0
    try:
        for i in range(n_stretch):
            squelch = 0.25 if i % 2 else None
            snr = 8 if squelch else 18
            ms = i % 4 < 2
            sync = 'MS_DATA' if ms else 'BS_DATA'
            burst = data(7, 'CSBK', sync, 3)
            x48, _e, _s = S.modulate_bursts([burst], 1, 0.3, 0.3)
            tail = np.zeros(4 * step, complex)
            x48 = np.concatenate([x48, tail + x48[0]])
            y, _r = S.render(x48, 48000.0, 0.0, 0.0, snr, seed=1000 + i)
            d = D.discriminate(y, 48000.0, squelch=squelch)
            voice = 'MS_VOICE' if ms else 'BS_VOICE'
            anchors += len(D.data_bursts(d, sync))
            returned += len(D.voice_calls(d, voice, recover=True))
    finally:
        cnt.close()
    if returned == 0 and cnt.ok == 0 and anchors >= n_stretch // 2 and cnt.n > 5 * n_stretch:
        ok(f'noise after a CSBK, {n_stretch} stretches ({anchors} anchors found): '
           f'{cnt.n} candidates tried, {cnt.ok} accepted, 0 superframes returned')
    else:
        fail('noise', f'returned {returned} accepted {cnt.ok} of {cnt.n} anchors {anchors}')


def case_off_when_not_recover(tmp):
    """recover=False never reaches the grid search, and is the same call as the default."""
    lc = F.full_lc(9, 1001, 'group')
    frames = rand_frames(1, 81)
    bursts = [data(5, 'CSBK', 'MS_DATA', 1)] + S.build_call(lc, 5, frames, 'ms', 0, 0)
    y, side, _a = ms_file(tmp, bursts, 'ms', rate_args('48k'))
    d, vs = R.demod(y, side)
    base = D.voice_calls(d, vs)
    d_blank = R.blank_sync(d, base[0][0])
    saved = D._recover_from_grid

    def boom(*a, **k):
        raise AssertionError('grid search ran with recover=False')
    D._recover_from_grid = boom
    try:
        a = D.voice_calls(d_blank, vs)
        b = D.voice_calls(d_blank, vs, recover=False)
        c = D.voice_calls(d_blank, vs, recover=False, return_info=True)
    finally:
        D._recover_from_grid = saved
    if a == b == c == []:
        ok('recover=False: the grid search is not called and nothing is returned')
    else:
        fail('recover=False', f'{a} {b} {c}')


def sf_index(frames, ref):
    """Index of the superframe of `ref` these 18 frames are exactly, else None."""
    if frames is None or len(frames) != 18 or any(f is None for f in frames):
        return None
    for i in range(0, len(ref), 18):
        if exact(frames, ref[i:i + 18]):
            return i // 18
    return None


def case_walk(tmp):
    """A four-superframe call whose four A SYNCs are all blanked and whose only anchor is
    one CSBK, three filler bursts from the call: the A at 4 and 10 bursts from the CSBK
    are in the grid window, the A at 16 and 22 are not and only the walk reaches them.
    Forward: CSBK, call. Backward: call, CSBK (the A at 18 is the one in the window, the
    ones at 12, 6 and 0 come from the backward walk)."""
    cc = 5
    lc = F.full_lc(9, 1001, 'group')
    frames = rand_frames(4, 91)
    for direction in ('forward', 'backward'):
        voice = S.build_call(lc, cc, frames, 'ms', 0, 0)
        csbk = data(cc, 'CSBK', 'MS_DATA', 3)
        if direction == 'forward':
            bursts, a_idx = [csbk] + [filler(None)] * 3 + voice, [4, 10, 16, 22]
        else:
            bursts, a_idx = voice + [filler(None)] * 3 + [csbk], [0, 6, 12, 18]
        for rname, rate in (('48k', rate_args('48k')), ('1M', rate_args('1M'))):
            y, side, apos = ms_file(tmp, bursts, 'ms', rate)
            d, vs = R.demod(y, side)
            k = side['sample_rate'] / D.FS_SYM
            base = D.voice_calls(d, vs, recover=False)
            if len(base) != 4:
                fail(f'walk {direction} {rname} setup', f'clean found {len(base)}')
                continue
            for i in a_idx:
                d = R.blank_sync(d, a_pos(d, apos[i], k))
            old = old_recover(d, vs)
            new = D.voice_calls(d, vs, recover=True)
            got = [sf_index(f, frames) for _p, f in new]
            if not old and got == [0, 1, 2, 3]:
                ok(f'walk {direction} {rname}: four A blanked, one CSBK, two superframes outside the '
                   f'window; old recover nothing, new all four bit-exact in order')
            else:
                fail(f'walk {direction} {rname}', f'old {len(old)} new {got}')


def bs_slots(call_bursts, other_burst, call_slot=1):
    """Repeater timeline with `call_bursts` in the call's slot and the same no-SYNC
    `other_burst` in the other: the carrier and the CACH words stay as in build_bs_call."""
    frags = F.short_lc_encode(F.null_short_lc())
    slots = []
    k = 0
    for burst, desc in call_bursts:
        for slot in (1, 2):
            at = 1 if slot == call_slot else 0
            lcss = F.SHORT_LC_LCSS[k % 4]
            bits, used = (burst, desc) if slot == call_slot else other_burst
            slots.append((bits, F.cach(F.tact(at, S.TC_OF_SLOT[slot], lcss), frags[k % 4]),
                          dict(used, slot=slot)))
            k += 1
    return slots


def case_bs_same_slot(tmp):
    """A repeater stream whose other slot is no-SYNC filler: the only SYNCs are in the
    call's own slot, a CSBK two bursts before the superframe, whose A is blanked. The
    other slot's 144-symbol offset reaches nothing; offset 0 does."""
    cc = 3
    lc = F.full_lc(9, 1001, 'group')
    frames = rand_frames(1, 95)
    out = []
    for call_slot in (1, 2):
        voice = S.build_call(lc, cc, frames, 'bs', 0, 0)
        csbk = data(cc, 'CSBK', 'BS_DATA', 4)
        call = [csbk, filler(None)] + voice
        slots = bs_slots(call, filler(None), call_slot)
        x48, _env, starts, _c = S.modulate_bs(slots, 0.3, 0.3)
        for rname in ('48k', '1M'):
            fs, off, cfo = rate_args(rname)
            y, ratio = S.render(x48, fs, off, cfo, None)
            side = {'sample_rate': fs, 'offset_hz': off, 'sync': {'voice': 'BS_VOICE'}}
            d, vs = R.demod(y, side)
            k = fs / D.FS_SYM
            base = D.voice_calls(d, vs, recover=False)
            if len(base) != 1 or not exact(base[0][1], frames):
                out.append((call_slot, rname, 'setup', len(base)))
                continue
            posA = a_pos(d, round((starts[2 * 2 + call_slot - 1] + 54 * D.SPS) * float(ratio)), k)
            d_blank = R.blank_sync(d, posA)
            n_data = len(D.data_bursts(d_blank, 'BS_DATA'))
            old = old_recover(d_blank, vs)
            new = D.voice_calls(d_blank, vs, recover=True)
            if n_data != 1 or old or len(new) != 1 or not exact(new[0][1], frames):
                out.append((call_slot, rname, n_data, len(old), len(new)))
    if out:
        fail('bs same-slot anchor', str(out))
    else:
        ok('bs, other slot no-SYNC filler, CSBK in the call\'s own slot (slots 1 and 2, 48k and 1M): '
           'old recover nothing, new one bit-exact superframe from offset 0')


def case_file_edge(tmp):
    """A CSBK inside the file times a superframe whose burst A is before the first sample
    (a file that starts mid-call): bursts B-F are there, so 15 frames are read, exact,
    and the three of A are None - this needs candidates up to `guard` before the start.
    The default (all 18 frames or nothing) returns nothing wrong. And a file that ends
    before burst A returns nothing."""
    cc = 5
    lc = F.full_lc(9, 1001, 'group')
    frames = rand_frames(1, 97)
    out = []
    for rname in ('48k', '1M'):
        rate = rate_args(rname)
        voice = S.build_call(lc, cc, frames, 'ms', 0, 0)
        y, side, apos = ms_file(tmp, voice + [data(cc, 'CSBK', 'MS_DATA', 5)] * 2, 'ms', rate)
        d, vs = R.demod(y, side)
        k = side['sample_rate'] / D.FS_SYM
        posA = a_pos(d, apos[0], k)
        # start: the file begins 700 samples after A's SYNC, so A's three frames are gone
        d_start = R.blank_sync(d, posA)[posA + 700:]
        info = D.voice_calls(d_start, vs, recover=True, return_info=True)
        dflt = D.voice_calls(d_start, vs, recover=True)
        good = len(info) == 1
        if good:
            fr = info[0][1]
            present = [i for i, f in enumerate(fr) if f is not None]
            good = (len(fr) == 18 and len(present) >= 12 and present == list(range(18 - len(present), 18))
                    and all(list(fr[i]) == list(frames[i]) for i in present))
        # end: the file stops before burst A's centre
        d_end = R.blank_sync(d, posA)[:posA - 20 * D.SPS]
        end_info = D.voice_calls(d_end, vs, recover=True, return_info=True)
        end_ok = all(all(f is None or list(f) == list(frames[i]) for i, f in enumerate(fr))
                     for _p, fr in end_info)
        if not (good and not dflt and end_ok):
            out.append((rname, [(p, [i for i, f in enumerate(fr) if f is not None])
                                for p, fr in info], len(dflt), end_ok))
    if out:
        fail('file edge', str(out))
    else:
        ok('file edge: a CSBK inside the file times a superframe whose burst A is before sample 0: '
           'the frames present are bit-exact, the default returns nothing incomplete; a file that '
           'ends before A returns nothing wrong; 48k and 1M')


def case_dedupe_offgrid(tmp):
    """A copy of a same-colour-code CSBK pasted four bursts before the real one and 0-40
    samples off the grid. superframe_candidate searches +-4 samples, so the stray anchor's
    prediction fails and must not stop the real CSBK from trying the same position."""
    cc = 5
    lc = F.full_lc(9, 1001, 'group')
    spacing = D.BURST_SPACING_SYMS * D.SPS
    rows = []
    for off_samples in (0, 5, 8, 12, 20, 40):
        for mode, vs, ds in (('ms', 'MS_VOICE', 'MS_DATA'), ('bs_like', 'BS_VOICE', 'BS_DATA')):
            frames = rand_frames(1, 31)
            voice = S.build_call(lc, cc, frames, 'ms' if mode == 'ms' else 'bs', 0, 0)
            bursts = [filler(None)] * 6 + [data(cc, 'CSBK', ds, 7)] + voice
            x48, _e, _s = S.modulate_bursts(bursts, 1, 0.3, 0.3)
            y, _r = S.render(x48, 48000.0, 0.0, 0.0, None)
            d = D.discriminate(y, 48000.0, squelch=0.25)
            base = D.voice_calls(d, vs)
            d = R.blank_sync(d, base[0][0])
            cpos = D.data_bursts(d, ds)[0][0]
            dst = cpos - 4 * spacing - off_samples
            d2 = d.copy()
            d2[dst - 60 * D.SPS:dst + 80 * D.SPS] = d[cpos - 60 * D.SPS:cpos + 80 * D.SPS]
            n_anchor = len(D.data_bursts(d2, ds))
            new = D.voice_calls(d2, vs, recover=True)
            if n_anchor != 2 or len(new) != 1 or not exact(new[0][1], frames):
                rows.append((off_samples, mode, n_anchor, len(new)))
    if rows:
        fail('dedupe off-grid anchor', str(rows))
    else:
        ok('a stray same-colour-code CSBK 4 bursts earlier, 0/5/8/12/20/40 samples off the grid, '
           'ms and bs-like: both anchors found, the real one still recovers exactly one superframe')


def main():
    n_skip = check_spec()
    with tempfile.TemporaryDirectory() as tmp:
        case_bs_lone(tmp)
        case_bs_two_sf(tmp)
        case_csbk(tmp)
        case_other_slot_voice(tmp)
        case_same_slot_other_call(tmp)
        case_shifted(tmp)
        case_off_when_not_recover(tmp)
        case_walk(tmp)
        case_bs_same_slot(tmp)
        case_file_edge(tmp)
        case_dedupe_offgrid(tmp)
    case_noise()
    if failures:
        line = f'FAIL {len(failures)}: {failures}'
    elif n_skip:
        line = f'PASS ({n_skip} spec pins skipped: docs/ptt/dmr/text/ not present)'
    else:
        line = 'PASS'
    print(line, flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
