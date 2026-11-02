#!/usr/bin/env python3
"""Recover a voice superframe whose burst A was not found. Synthetic calls only.

Each case is built with scripts/dmr_synth.py, at 48 kS/s and at 1 MS/s with an
offset. One summary line per case, then PASS or FAIL.

    python scripts/test_dmr_recover.py
"""
import contextlib
import io
import json
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

SPEC = os.path.join(HERE, '..', 'docs', 'ptt', 'dmr', 'text',
                    'ETSI_TS_102_361-1_v2.5.1_air_interface_protocol.txt')
SPEC_DIR = os.path.join(HERE, '..', 'docs', 'ptt', 'dmr', 'text')
# check_spec compares six facts with the library. A clone with no spec text
# skips exactly these and still runs the synthetic cases.
N_SPEC_PINS = 6
# Same 20 seeds and 5 superframes as case_snr, both rates, on this code:
# the noisiest recovered superframe was 18 bit errors (1 MS/s; 48 kS/s was 4).
# 36 is twice that. A wrong voice frame is 72 bits and a random superframe
# is about half of 18*72, so both still fail. The boxcar frames themselves
# are not bit-exact at 12 dB (up to 16 bits off the source in that sweep);
# "stays bit exact" is recover=True reproducing those frames unchanged.
RECOVERED_BER_BOUND = 36
failures = []


def fail(name, detail=''):
    print(f'FAIL {name}' + (f': {detail}' if detail else ''), flush=True)
    failures.append(name)


def ok(name):
    print(f'ok   {name}', flush=True)


def synth(tmp, name, nsf, args, seed=1):
    amb = os.path.join(tmp, name + '.src.ambe')
    open(amb, 'wb').write(np.random.default_rng(seed).integers(
        0, 256, 9 * 18 * nsf, dtype=np.uint8).tobytes())
    out = os.path.join(tmp, name + '.cf32')
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = S.main([out, '--ambe', amb, '--src', '1001', '--dst', '9'] + args)
    if rc:
        raise SystemExit(f'synth {name} returned {rc}')
    side = json.load(open(os.path.join(tmp, name + '.json')))
    y = np.fromfile(out, np.complex64)
    ambe = D.ambe_to_frames(open(os.path.join(tmp, name + '.ambe'), 'rb').read()).tolist()
    return y, side, ambe


def demod(y, side):
    fs = side['sample_rate']
    n = np.arange(len(y))
    y = y * np.exp(-2j * np.pi * side['offset_hz'] * n / fs)
    return D.discriminate(y, fs, squelch=0.25), side['sync']['voice']


def blank_sync(d, pos, margin=2):
    """Replace burst A's SYNC with noise, leaving the payload symbol centres.

    The blank stops `margin` samples short of the payload centres on either
    side. Blanking the whole centre in the IQ file smears through the RRC into
    the voice frame that straddles the SYNC; doing it here, after the
    discriminator, removes the correlation peak and keeps those bits.
    """
    d = np.array(d, copy=True)
    a = int(pos) - D.SPS + margin
    b = int(pos) + 24 * D.SPS - (margin - 1)
    a = max(a, 0)
    b = min(b, len(d))
    d[a:b] = np.random.default_rng(0).normal(0.0, 900.0, b - a)
    return d


def sf_of(frames, ambe):
    """Index of the superframe these 18 frames are, or None if they are not exact."""
    if any(f is None for f in frames) or len(frames) != 18:
        return None
    for i in range(0, len(ambe), 18):
        if frames == ambe[i:i + 18]:
            return i // 18
    return None


def audit(frames, ambe):
    """(superframe index or None, present frame indices, number of wrong frames).

    A present frame is wrong when it is not the reference frame at that index
    of one superframe. Every present frame must belong to the same superframe.
    """
    present = [(i, f) for i, f in enumerate(frames) if f is not None]
    if not present:
        return None, [], 0
    sfs, wrong = [], 0
    for i, f in present:
        hits = [j for j, ref in enumerate(ambe) if ref == f]
        if len(hits) == 1 and hits[0] % 18 == i:
            sfs.append(hits[0] // 18)
        else:
            wrong += 1
    if wrong or not sfs or any(s != sfs[0] for s in sfs):
        return None, [i for i, _ in present], wrong or 1
    return sfs[0], [i for i, _ in present], 0


def voice_of(side, sf):
    return [b for b in side['bursts']
            if b.get('kind') == 'voice' and b.get('superframe') == sf]


# --- constants pinned to the spec text ----------------------------------------------
def spec_lcss_bf(txt):
    """B-F LCSS from Table 9.20 and clause 7.1.3: first, cont, cont, last, single."""
    i = txt.index('Table 9.20:')
    seg = txt[i:txt.index('9.3.4', i)]
    val = {}
    for m in re.finditer(
            r'\b([01]{2})2\s+(Single fragment|First fragment|Last fragment|Continuation fragment)',
            seg):
        val[m.group(2)] = int(m.group(1), 2)
    # The body of 7.1.3, not the table-of-contents line. The PDF extract wraps
    # "first non-SYNC (burst B)" across a line break.
    start = txt.index('7.1.3         Embedded signalling')
    body = txt[start:txt.index('7.1.4', start)]
    if not re.search(r'first\s+non-SYNC \(burst B\)', body) or 'four bursts for LC' not in body:
        raise ValueError('clause 7.1.3 does not name burst B and four LC bursts')
    if 'Single Fragment' not in body:
        raise ValueError('clause 7.1.3 does not call the non-LC burst a single fragment')
    # Four LC bursts B-E, in order, then burst F which is not LC.
    order = ['First fragment', 'Continuation fragment', 'Continuation fragment',
             'Last fragment', 'Single fragment']
    return tuple(val[name] for name in order)


def spec_superframe_syms(txt):
    """Symbol count and burst spacing, parsed from the superframe sentence and the symbol rate.

    The text prints "6 bursts (360 ms)" and "4 800 symbols/s" (the space is the
    thousands separator). Nothing else is filled in.
    """
    bursts = set(re.findall(r'(\d+)\s+bursts\s+\((\d+)\s+ms\)', txt))
    if len(bursts) != 1:
        raise ValueError(f'superframe length not a single figure in the text: {bursts}')
    n_bursts, ms = (int(x) for x in bursts.pop())
    rates = set(re.findall(r'(\d)\s+(\d{3})\s+symbols/s', txt))
    if len(rates) != 1:
        raise ValueError(f'symbol rate not a single figure in the text: {rates}')
    hi, lo = rates.pop()
    rate = int(hi + lo)
    sf = int(round(rate * ms / 1000.0))
    if n_bursts <= 0 or sf % n_bursts:
        raise ValueError(f'{sf} symbols is not a whole number of {n_bursts} bursts')
    return sf, sf // n_bursts, n_bursts


def spec_emb_positions(txt):
    """Positions of the 16 EMB bits inside the 48-bit centre, from Table E.6."""
    i = txt.index('Table E.6:')
    seg = txt[i:txt.index('Table E.7:', i)]
    found = {}
    for m in re.finditer(r'\b([LR])(\d+)\s+(\S+)\s+(\S+)', seg):
        found[(m.group(1), int(m.group(2)))] = (m.group(3), m.group(4))
    names = []
    for n in range(12, 0, -1):
        names.extend(found[('L', n)])
    for n in range(1, 13):
        names.extend(found[('R', n)])
    want = ['CC(3)', 'CC(2)', 'CC(1)', 'CC(0)', 'PI', 'LCSS(1)', 'LCSS(0)', 'QR(8)',
            'QR(7)', 'QR(6)', 'QR(5)', 'QR(4)', 'QR(3)', 'QR(2)', 'QR(1)', 'QR(0)']
    return [names.index(n) for n in want]


def _header_name(words):
    """'voice LC header' -> 'Voice LC Header'. Tokens that already carry a capital stay."""
    out = []
    for w in words.split():
        out.append(w if any(c.isupper() for c in w) else w.capitalize())
    return ' '.join(out)


def spec_anchors(txt, n_bursts):
    """Header names and burst deltas, from the sentences that place them.

    Clause 5.1.2.2 names the two headers in words. It does not print a numeric
    offset: "immediately" is the one burst before A, and the terminator is the
    one burst after a superframe of `n_bursts`, so its delta back to A is
    -n_bursts. Both numbers are that reading of the text, not literals.
    """
    m = re.search(
        r'immediately\s+preceded\s+by\s+a\s+([A-Za-z][A-Za-z ]*?)\s+or\s+a\s+'
        r'([A-Za-z][A-Za-z ]*?)\.',
        txt)
    if not m:
        raise ValueError('clause 5.1.2.2 does not name the headers before burst A')
    if not re.search(
            r'burst\s+immediately\s+following\s+the\s+end\s+of\s+a\s+voice\s+superframe',
            txt):
        raise ValueError('the text does not put a data burst immediately after the superframe')
    one_burst = 1
    return (_header_name(m.group(1)), _header_name(m.group(2))), (one_burst, -n_bursts)


def check_spec():
    """Return how many spec pins were skipped. Zero when the text was read."""
    if not os.path.exists(SPEC):
        print(f'SKIPPED {N_SPEC_PINS} spec pins ({SPEC_DIR}/ not present)', flush=True)
        return N_SPEC_PINS
    txt = open(SPEC).read()
    try:
        seq = spec_lcss_bf(txt)
        sf, spacing, n_bursts = spec_superframe_syms(txt)
        emb_pos = spec_emb_positions(txt)
        headers, deltas = spec_anchors(txt, n_bursts)
    except (ValueError, KeyError) as e:
        fail('spec parse', str(e))
        return 0
    bad = []
    if seq != tuple(D.LCSS_BF):
        bad.append(f'LCSS_BF {D.LCSS_BF} != {seq}')
    if (sf, spacing) != (D.SUPERFRAME_SYMS, D.BURST_SPACING_SYMS):
        bad.append(f'spacing {(D.SUPERFRAME_SYMS, D.BURST_SPACING_SYMS)} != {(sf, spacing)}')
    # The second EMB half sits at the end of the centre, not just after the first.
    # A period-2 pattern is the same in both places and would not catch a slice
    # that reads the embedded field instead of EMB(7)..EMB(0).
    centre = [1 if i >= 40 else 0 for i in range(48)]
    burst = [0] * 108 + centre + [0] * 108
    got = D.emb_bits(burst)
    want = [centre[i] for i in emb_pos]
    if got != want:
        bad.append(f'emb_bits {got} != Table E.6 {want}')
    if emb_pos != list(range(8)) + list(range(40, 48)):
        bad.append(f'EMB positions {emb_pos} are not centre[:8]+centre[-8:]')
    if tuple(D.ANCHOR_HEADERS) != headers:
        bad.append(f'ANCHOR_HEADERS {D.ANCHOR_HEADERS} != {headers}')
    if D.anchor_delta_bursts() != deltas or deltas[1] != -n_bursts or deltas[0] != 1:
        bad.append(f'anchor deltas {D.anchor_delta_bursts()} != {deltas}')
    if len(bad) != 0:
        fail('spec pins', '; '.join(bad))
    else:
        ok(f'spec pins: LCSS B-F, {sf}/{spacing} symbols from {n_bursts} bursts, '
           'EMB bits at Table E.6, headers and deltas parsed from the text')
    return 0


def _typed(side, data_type):
    return [b for b in side['bursts'] if b.get('data_type') == data_type]


def case_lone(y, side, ambe, label):
    """One superframe, no neighbour. Blank A, or cut A away and keep one anchor."""
    d, voice = demod(y, side)
    base = D.voice_calls(d, voice, recover=False)
    if [sf_of(fr, ambe) for _, fr in base] != [0]:
        fail(label + ' setup', f'clean {[sf_of(fr, ambe) for _, fr in base]}')
        return
    d_blank = blank_sync(d, base[0][0])
    old = D.voice_calls(d_blank, voice, recover=False)
    new = D.voice_calls(d_blank, voice, recover=True)
    old_sfs = [sf_of(fr, ambe) for _, fr in old]
    new_sfs = [sf_of(fr, ambe) for _, fr in new]
    if old_sfs == [] and new_sfs == [0]:
        ok(f'{label} blanked A: old empty, recover=True bit-exact')
    else:
        fail(label + ' blanked A', f'old {old_sfs} new {new_sfs}')

    # Header only: drop the terminator, then blank A. The header is the anchor.
    term = _typed(side, 'Terminator with LC')
    if len(term) != 1:
        fail(label + ' header-only', f'{len(term)} terminators')
        return
    y_head = y[:int(term[0]['start_sample'])]
    d_head, voice = demod(y_head, side)
    found = D.voice_calls(d_head, voice, recover=False)
    if len(found) != 1:
        fail(label + ' header-only', f'uncut A count {len(found)}')
        return
    d_head = blank_sync(d_head, found[0][0])
    old = D.voice_calls(d_head, voice, recover=False)
    new = D.voice_calls(d_head, voice, recover=True)
    new_sfs = [sf_of(fr, ambe) for _, fr in new]
    if old == [] and new_sfs == [0]:
        ok(f'{label} header only: old empty, recover=True bit-exact')
    else:
        fail(label + ' header-only', f'old {len(old)} new {new_sfs}')

    # Terminator only: the file starts at burst B, so A and the header are gone.
    b0 = voice_of(side, 0)[1]['start_sample']
    y_tail = y[int(b0):]
    d_tail, voice = demod(y_tail, side)
    info = D.voice_calls(d_tail, voice, recover=True, return_info=True)
    plain = D.voice_calls(d_tail, voice, recover=True)
    old = D.voice_calls(d_tail, voice, recover=False)
    wrong = 0
    present = []
    for _p, fr in info:
        _sf, present, w = audit(fr, ambe)
        wrong += w
    if old:
        fail(label + ' terminator-only', f'old found {len(old)} with A cut off')
    elif wrong or len(present) < 12 or any(i < 3 for i in present):
        fail(label + ' terminator-only', f'wrong {wrong} frames {present}')
    elif any(sf_of(fr, ambe) is None for _p, fr in plain):
        fail(label + ' terminator-only', 'default output was not a complete exact superframe')
    else:
        ok(f'{label} terminator only: frames {present}; '
           f'default kept {len(plain)} complete')


def case_blank(y, side, ambe, label):
    d, voice = demod(y, side)
    base = D.voice_calls(d, voice, recover=False)
    base_sfs = [sf_of(fr, ambe) for _, fr in base]
    if base_sfs != list(range(side['superframes'])):
        fail(label, f'clean recover=False superframes {base_sfs}')
        return
    mid_i = len(base) // 2
    d2 = blank_sync(d, base[mid_i][0])
    old = D.voice_calls(d2, voice, recover=False)
    new = D.voice_calls(d2, voice, recover=True)
    old_sfs = [sf_of(fr, ambe) for _, fr in old]
    new_sfs = [sf_of(fr, ambe) for _, fr in new]
    detail = f'old {old_sfs} new {new_sfs}'
    if (mid_i not in old_sfs and sorted(new_sfs) == list(range(side['superframes']))
            and None not in new_sfs):
        ok(f'{label}: old lost sf {mid_i}, recover=True bit-exact on all {side["superframes"]}')
    else:
        fail(label, detail)


def case_cuts(y, side, ambe, where, cuts, label_prefix):
    """where is 'start' (keep the tail) or 'end' (keep the head)."""
    for name, sample in cuts:
        sample = int(sample)
        if where == 'start':
            piece = y[sample:]
        else:
            piece = y[:max(sample, 1)]
        side2 = dict(side)
        d, voice = demod(piece, side2)
        info = D.voice_calls(d, voice, recover=True, return_info=True)
        plain = D.voice_calls(d, voice, recover=True, return_info=False)
        wrong = 0
        parts = []
        for _p, fr in info:
            sf, present, w = audit(fr, ambe)
            wrong += w
            if len(present) != 18:
                parts.append(f'sf {sf} frames {present}')
        plain_bad = 0
        for _p, fr in plain:
            if sf_of(fr, ambe) is None:
                plain_bad += 1
        label = f'{label_prefix} {name}'
        if wrong or plain_bad:
            fail(label, f'wrong {wrong} plain_bad {plain_bad} partial {parts}')
        else:
            shown = ', '.join(parts) if parts else 'no partial superframe'
            ok(f'{label}: {shown}; default output {len(plain)} complete superframes')


def case_snr(nsf, args, seeds, label):
    old_sf = new_sf = mismatch = 0
    extra_ber = []
    with tempfile.TemporaryDirectory() as tmp:
        for seed in seeds:
            y, side, ambe = synth(
                tmp, 'snr', nsf,
                args + ['--snr', '12', '--seed', str(seed), '--lead', '0.25', '--tail', '0.25'],
                seed=1000 + seed)
            d, voice = demod(y, side)
            old = D.voice_calls(d, voice, recover=False)
            new = D.voice_calls(d, voice, recover=True)
            old_sf += len(old)
            new_sf += len(new)
            if len(new) < len(old):
                mismatch += 1
            for p, fr in old:
                hit = [f for q, f in new if abs(q - p) <= 4 * D.SPS]
                if len(hit) != 1 or hit[0] != fr:
                    mismatch += 1
            for p, fr in new:
                if any(abs(p - q) <= 4 * D.SPS for q, _ in old):
                    continue
                if any(f is None for f in fr) or len(fr) != 18:
                    mismatch += 1
                    continue
                bers = []
                for i in range(0, len(ambe), 18):
                    bits = sum(x != y for a, b in zip(fr, ambe[i:i + 18]) for x, y in zip(a, b))
                    bers.append(bits)
                extra_ber.append(min(bers))
    worst = max(extra_ber) if extra_ber else 0
    detail = (f'old {old_sf} new {new_sf} extra {len(extra_ber)} '
              f'max extra bit errors {worst} (bound {RECOVERED_BER_BOUND}) '
              f'mismatches {mismatch}')
    # mismatch counts a boxcar frame that recover=True did not keep bit for bit.
    if mismatch or new_sf < old_sf or worst > RECOVERED_BER_BOUND:
        fail(label, detail)
    else:
        ok(f'{label}: boxcar frames kept bit-exact; {detail}')


def case_noise_and_other_cc(tmp):
    # Noise only. A handheld demod squelches; try both, since recover must not invent.
    for squelch, tag in ((0.25, 'squelched'), (None, 'unsquelched')):
        iq = (np.random.default_rng(4).standard_normal(200_000)
              + 1j * np.random.default_rng(5).standard_normal(200_000)).astype(np.complex64)
        d = D.discriminate(iq, D.FS_SYM, squelch=squelch)
        n = len(D.voice_calls(d, 'MS_VOICE', recover=True))
        if n:
            fail(f'noise {tag}', f'{n} superframes')
        else:
            ok(f'noise {tag}: 0 superframes')

    y, side, ambe = synth(
        tmp, 'cc9', 4,
        ['--sync', 'ms', '--cc', '9', '--superframes', '4', '--fs', '48000',
         '--lead', '0.4', '--tail', '0.4', '--slot', '1'],
        seed=11)
    d, voice = demod(y, side)
    old = D.voice_calls(d, voice, recover=False)
    new = D.voice_calls(d, voice, recover=True)
    old_sfs = [sf_of(fr, ambe) for _, fr in old]
    new_sfs = [sf_of(fr, ambe) for _, fr in new]
    if old_sfs == new_sfs == list(range(4)):
        ok('other colour code cc 9 with noise lead/tail: 4 superframes, none invented')
    else:
        fail('other colour code', f'old {old_sfs} new {new_sfs}')


def case_false_counts(tmp):
    """200 clean superframes and 200 noise stretches. Also a wrong colour code.

    find_syncs can swallow one voice SYNC when a payload sidelobe exceeds the
    threshold inside its minimum gap (seen on this seed). That superframe is
    still in the file, so the false-reject count predicts every superframe from
    a neighbour that was found, and recover=True has to return all of them.
    """
    nsf = 200
    y, side, ambe = synth(
        tmp, 'long', nsf,
        ['--sync', 'ms', '--cc', '1', '--superframes', str(nsf), '--fs', '48000',
         '--lead', '0.3', '--tail', '0.3'],
        seed=21)
    d, voice = demod(y, side)
    calls = D.voice_calls(d, voice, recover=False)
    found = [(p, sf_of(fr, ambe)) for p, fr in calls]
    if any(sf is None for _, sf in found) or len({sf for _, sf in found}) != len(found):
        fail('false-count setup', 'a SYNC-found superframe was not bit-exact')
        return
    missed = [i for i in range(nsf) if i not in {sf for _, sf in found}]
    recovered = D.voice_calls(d, voice, recover=True)
    rec_idx = [sf_of(fr, ambe) for _, fr in recovered]
    if rec_idx != list(range(nsf)):
        fail('false-count setup', f'recover=True indexes {rec_idx[:8]}... missed by sync {missed}')
        return
    sy = D.sync_symbols(voice)
    step = D.SUPERFRAME_SYMS * D.SPS
    burst = D.BURST_SPACING_SYMS * D.SPS
    # One neighbour prediction per superframe. A found burst A at p stands in
    # for the superframe before it and the one after it.
    pred = {}
    for p, sf in found:
        if sf + 1 < nsf and (sf + 1) not in pred:
            pred[sf + 1] = (p + step, p)
        if sf - 1 >= 0 and (sf - 1) not in pred:
            pred[sf - 1] = (p - step, p)
    if len(pred) != nsf:
        fail('false-count setup', f'only {len(pred)} of {nsf} superframes have a found neighbour')
        return
    reject = wrong_cc = off_grid = 0
    for sf, (cpos, anchor) in pred.items():
        sc, off = D.fit_levels(d, anchor, sy)
        cc = D.superframe_colour(d, anchor, sc, off)
        ok_c, al, sc2, of2 = D.superframe_candidate(d, cpos, sc, off, cc)
        frames = []
        for k in range(6):
            frames += D.read_voice_frames_at(
                d, al + k * burst, sc2, of2, max_rms=D.RECOVER_MAX_LEVEL_RMS, edge=0)
        if not ok_c or frames != ambe[sf * 18:(sf + 1) * 18]:
            reject += 1
        ok_w, *_ = D.superframe_candidate(d, cpos, sc, off, (cc + 1) & 15)
        wrong_cc += bool(ok_w)
        ok_o, *_ = D.superframe_candidate(d, cpos + burst, sc, off, cc)
        off_grid += bool(ok_o)
    n_real = len(pred)

    iq = (np.random.default_rng(8).standard_normal(600_000)
          + 1j * np.random.default_rng(9).standard_normal(600_000)).astype(np.complex64) * 0.05
    dn = D.discriminate(iq, D.FS_SYM, squelch=0.25)
    fa = 0
    positions = np.random.default_rng(10).integers(step, len(dn) - step, 200)
    for p in positions:
        fa += bool(D.superframe_candidate(dn, int(p), 600.0, 0.0, 1)[0])
    detail = (f'false-accept {fa}/200 noise, false-reject {reject}/{n_real} real, '
              f'wrong-cc accept {wrong_cc}/{n_real}, off-grid accept {off_grid}/{n_real}, '
              f'sync missed {missed} and recover=True returned all {nsf}')
    if fa or reject or wrong_cc or off_grid:
        fail('thresholds', detail)
    else:
        ok(f'thresholds: {detail}')


def _flip_two_emb_bits(d, sync_pos, sym_index, scale, off):
    """Move one centre-field symbol by two dibit steps: both of its bits flip.

    QR(16,7) has distance 6, so two flipped EMB bits still decode to the same
    codeword with err=2. One such symbol is inside the EMB (symbols 54-57 or
    74-77), not in a voice frame.
    """
    idx = int(sync_pos - 54 * D.SPS + sym_index * D.SPS)
    v = (d[idx] - off) / scale
    cur = 3 if v > 2 else 1 if v > 0 else -1 if v > -2 else -3
    new = cur - 4 if cur > 0 else cur + 4
    d[idx] = new * scale + off


def _two_superframes(tmp, seed):
    """Clean MS call, no header and no terminator, so a neighbour is the only anchor."""
    y, side, ambe = synth(
        tmp, 'gate', 2,
        ['--sync', 'ms', '--slot', '1', '--cc', '3', '--superframes', '2',
         '--headers', '0', '--terminators', '0', '--fs', '48000',
         '--lead', '0.2', '--tail', '0.2'],
        seed=seed)
    d, voice = demod(y, side)
    base = D.voice_calls(d, voice, recover=False)
    if [sf_of(fr, ambe) for _, fr in base] != [0, 1]:
        return None
    return d, voice, ambe, base[0][0], base[1][0]


def case_emb_error_limit(tmp):
    """Two EMBs with 2 flipped bits must be rejected.

    One bad EMB would still leave 4 of 5 inside the limit, which both the
    real gate (1) and the loosened gate (3) accept. Two bad EMBs leave 3,
    so limit 1 rejects and limit 3 accepts. The LCSS mismatch counter rejects
    the same case; this still fails the limit-3 mutation because those two
    EMBs then count as good.
    """
    built = _two_superframes(tmp, seed=31)
    if built is None:
        fail('emb error limit', 'clean call was not two exact superframes')
        return
    d, voice, ambe, p0, p1 = built
    sc, off = D.fit_levels(d, p0, D.sync_symbols(voice))
    for k in (1, 2):
        _flip_two_emb_bits(d, p1 + k * D.BURST_SPACING_SYMS * D.SPS, 54, sc, off)
    d = blank_sync(d, p1)
    got = D.voice_calls(d, voice, recover=True)
    sfs = [sf_of(fr, ambe) for _, fr in got]
    if sfs == [0]:
        ok('EMB with 2 flipped bits in two bursts: the superframe is rejected')
    else:
        fail('emb error limit', f'recovered superframes {sfs}')


def case_min_emb(tmp):
    """An anchor with only 3 readable EMBs must not time its neighbour.

    superframe_colour asks for RECOVER_MIN_EMB votes and does not use the LCSS
    mismatch counter, which is why loosening 4 to 2 still recovers the neighbour.
    """
    built = _two_superframes(tmp, seed=32)
    if built is None:
        fail('min emb', 'clean call was not two exact superframes')
        return
    d, voice, ambe, p0, p1 = built
    sc, off = D.fit_levels(d, p0, D.sync_symbols(voice))
    for k in (1, 2):
        _flip_two_emb_bits(d, p0 + k * D.BURST_SPACING_SYMS * D.SPS, 54, sc, off)
    d = blank_sync(d, p1)
    got = D.voice_calls(d, voice, recover=True)
    sfs = [sf_of(fr, ambe) for _, fr in got]
    if sfs == [0]:
        ok('anchor with 3 readable EMBs: the neighbour is not recovered')
    else:
        fail('min emb', f'recovered superframes {sfs}')


def case_edge_guard(tmp):
    """A frame whose symbols are inside the array but inside the 160-sample guard is None.

    The cut puts burst B's first symbol about 100 samples into the discriminator,
    fully inside the array (the frame ends near sample 450) and inside RECOVER_EDGE.
    edge 160 returns None. edge 0 quantizes that transient and returns bits.
    """
    y, side, ambe = synth(
        tmp, 'edge', 2,
        ['--sync', 'ms', '--slot', '1', '--cc', '3', '--superframes', '2',
         '--headers', '0', '--terminators', '0', '--fs', '48000',
         '--lead', '0.3', '--tail', '0.3'],
        seed=4)
    d0, voice = demod(y, side)
    base = D.voice_calls(d0, voice, recover=False)
    if [sf_of(fr, ambe) for _, fr in base] != [0, 1]:
        fail('edge guard', 'clean call was not two exact superframes')
        return
    p0 = base[0][0]
    burst_b_sym0 = int(p0 + D.BURST_SPACING_SYMS * D.SPS - 54 * D.SPS)
    target = 100
    d = d0[burst_b_sym0 - target:]
    got = D.voice_calls(d, voice, recover=True, return_info=True)
    partial = [(p, fr) for p, fr in got if any(f is None for f in fr)]
    full = [(p, fr) for p, fr in got if all(f is not None for f in fr)]
    if len(partial) != 1 or len(full) != 1:
        fail('edge guard', f'partial {len(partial)} full {len(full)}')
        return
    p, fr = partial[0]
    sym0 = int(p + D.BURST_SPACING_SYMS * D.SPS - 54 * D.SPS)
    last = sym0 + 35 * D.SPS
    inside = 0 <= sym0 < D.RECOVER_EDGE and last < len(d)
    tail_ok = fr[4:] == ambe[4:18]
    refused = fr[:4] == [None, None, None, None]
    anchor_ok = full[0][1] == ambe[18:36]
    if inside and refused and tail_ok and anchor_ok:
        ok(f'edge guard: frame at sample {sym0} (inside the array, inside '
           f'{D.RECOVER_EDGE}) is None; the rest of the superframe is exact')
    else:
        fail('edge guard',
             f'inside {inside} sym0 {sym0} last {last} len {len(d)} '
             f'refused {refused} tail {tail_ok} anchor {anchor_ok}')


def case_timing_radius(tmp):
    """A neighbour whose SYNC peak is 2 to 4 samples off the prediction still recovers.

    Deleting a few samples in the gap between the superframes walks the later
    peak off the 17280-sample step. Radius 4 recentres on the open eye; radius
    0 stays on the wandered sample and the eye fails the residual.
    """
    built = _two_superframes(tmp, seed=33)
    if built is None:
        fail('timing radius', 'clean call was not two exact superframes')
        return
    d0, voice, ambe, p0, p1 = built
    step = D.SUPERFRAME_SYMS * D.SPS
    burst = D.BURST_SPACING_SYMS * D.SPS
    f_end = p0 + 5 * burst + (132 - 54) * D.SPS
    gap = (f_end + p1) // 2
    natural = (p1 - p0) - step
    shift = natural + 3
    if not 1 <= shift <= 8 or not f_end < gap < p1:
        fail('timing radius', f'cannot place the wander (natural {natural} gap {gap})')
        return
    d = np.concatenate([d0[:gap], d0[gap + shift:]])
    found = D.voice_calls(d, voice, recover=False)
    if len(found) != 2:
        fail('timing radius', f'after the cut, found {len(found)} superframes')
        return
    earlier, later = found[0][0], found[1][0]
    wander = later - (earlier + step)
    if not 2 <= abs(wander) <= 4:
        fail('timing radius', f'wander {wander} samples is outside 2..4')
        return
    blanked = blank_sync(d, later)
    got = D.voice_calls(blanked, voice, recover=True)
    sfs = [sf_of(fr, ambe) for _, fr in got]
    if sfs == [0, 1]:
        ok(f'timing radius: SYNC peak {wander} samples off the 17280-step prediction, '
           'both superframes recovered bit-exact')
    else:
        fail('timing radius', f'wander {wander} recovered {sfs}')


def case_residual_gate(tmp):
    """A fit that never lands on the four levels is rejected by the residual gate.

    Most symbol centres of bursts B-F sit 30 Hz either side of zero, alternating
    in sign: that is the boundary between the -1 and +1 levels, and no scale
    puts both signs on a level at once. A one-sided cloud does not work, because
    the fit slides the offset and the residual drops under 0.75. Gaussian noise
    does not work either: the fit grows the scale until the normalised residual
    is under the gate, or the scale leaves the 300-1000 Hz window and the
    candidate is rejected for that instead. The 40 EMB symbols are the real
    codeword at 648 Hz, so all 5 EMBs decode with 0 errors. On this draw the
    residual is about 0.90. The gate at 0.75 rejects the candidate; 99 accepts
    it. The EMB check would reject a purely random eye either way, so it cannot
    see this mutation on its own.
    """
    y, side, ambe = synth(
        tmp, 'resid', 1,
        ['--sync', 'ms', '--slot', '1', '--cc', '5', '--superframes', '1',
         '--headers', '0', '--terminators', '0', '--fs', '48000',
         '--lead', '0.2', '--tail', '0.2'],
        seed=8)
    d0, voice = demod(y, side)
    base = D.voice_calls(d0, voice, recover=False)
    if len(base) != 1 or sf_of(base[0][1], ambe) != 0:
        fail('residual gate', 'clean superframe missing')
        return
    p0 = base[0][0]
    sc0, off0 = D.fit_levels(d0, p0, D.sync_symbols(voice))
    emb_syms = list(range(54, 58)) + list(range(74, 78))
    ideal = {}
    for k in range(1, 6):
        pos = p0 + k * D.BURST_SPACING_SYMS * D.SPS
        for s in emb_syms:
            idx = int(pos - 54 * D.SPS + s * D.SPS)
            v = (d0[idx] - off0) / sc0
            ideal[(k, s)] = 3 if v > 2 else 1 if v > 0 else -1 if v > -2 else -3
    rng = np.random.default_rng(2)
    d = np.zeros_like(d0)
    sign = 1.0
    for k in range(1, 6):
        pos = p0 + k * D.BURST_SPACING_SYMS * D.SPS
        for s in range(132):
            idx = int(pos - 54 * D.SPS + s * D.SPS)
            if (k, s) in ideal:
                d[idx] = ideal[(k, s)] * 648.0
            elif rng.random() < 0.70:
                d[idx] = sign * 30.0
                sign = -sign
            else:
                d[idx] = float(rng.choice([-3.0, -1.0, 1.0, 3.0])) * 648.0
    fit = D.refit_levels(d, p0, 648.0, 0.0)
    if fit is None:
        fail('residual gate', 'the off-level fit did not return a scale')
        return
    sc, off = fit[0], fit[1]
    errs = []
    for k, expect_lcss in enumerate(D.LCSS_BF, start=1):
        bits = D.read_emb_at(d, p0 + k * D.BURST_SPACING_SYMS * D.SPS, sc, off)
        cc, _pi, lcss, err = F.emb_decode(bits)
        errs.append(err)
        if cc != side['colour_code'] or lcss != expect_lcss or err != 0:
            fail('residual gate', f'EMB {k} decoded cc {cc} lcss {lcss} err {err}')
            return
    accepted = D.superframe_candidate(d, p0, 648.0, 0.0, side['colour_code'])[0]
    if fit[2] > D.RECOVER_MAX_LEVEL_RMS and not accepted:
        ok(f'residual gate: rms {fit[2]:.3f} > {D.RECOVER_MAX_LEVEL_RMS}, '
           f'EMB errors {errs}, candidate rejected')
    else:
        fail('residual gate', f'rms {fit[2]:.3f} accepted {accepted} emb {errs}')


def main():
    n_skip = check_spec()
    rates = [
        ('48k', ['--fs', '48000']),
        ('1M +25 kHz', ['--fs', '1e6', '--offset', '25000']),
    ]
    configs = [
        ('ms slot 1', ['--sync', 'ms', '--slot', '1', '--cc', '3']),
        ('ms slot 2', ['--sync', 'ms', '--slot', '2', '--cc', '5']),
        ('direct2 slot 2', ['--sync', 'direct2', '--slot', '2', '--cc', '12']),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        for cfg_name, cfg in configs:
            for rate_name, rate in rates:
                y, side, ambe = synth(
                    tmp, 'b', 6,
                    cfg + rate + ['--superframes', '6', '--lead', '0.3', '--tail', '0.3'],
                    seed=3)
                case_blank(y, side, ambe, f'blanked middle A, {cfg_name}, {rate_name}')

        # One superframe has no neighbour A. A header or a terminator times it.
        lone_cfgs = [
            ('ms slot 1', ['--sync', 'ms', '--slot', '1', '--cc', '3']),
            ('direct2 slot 2', ['--sync', 'direct2', '--slot', '2', '--cc', '12']),
        ]
        for cfg_name, cfg in lone_cfgs:
            for rate_name, rate in rates:
                y, side, ambe = synth(
                    tmp, 'lone', 1,
                    cfg + rate + ['--superframes', '1', '--lead', '0.3', '--tail', '0.3'],
                    seed=7)
                case_lone(y, side, ambe, f'lone superframe, {cfg_name}, {rate_name}')

        # Cuts. One call per rate; several cut points from mid-A through mid-D,
        # and the same points on the way out of the last superframe.
        for rate_name, rate in rates:
            y, side, ambe = synth(
                tmp, 'cut', 5,
                ['--sync', 'ms', '--slot', '1', '--cc', '3', '--superframes', '5',
                 '--lead', '0.35', '--tail', '0.35'] + rate,
                seed=4)
            sf0 = voice_of(side, 0)
            sfL = voice_of(side, side['superframes'] - 1)
            fs = side['sample_rate']
            pre = max(0, sf0[0]['start_sample'] - int(round(40 * fs / D.SYMBOL_RATE)))
            starts = [('pre-A', pre),
                      ('mid-A', sf0[0]['sync_sample']),
                      ('start-B', sf0[1]['start_sample']),
                      ('mid-B', sf0[1]['sync_sample']),
                      ('mid-C', sf0[2]['sync_sample']),
                      ('mid-D', sf0[3]['sync_sample'])]
            ends = [('end-mid-A', sfL[0]['sync_sample']),
                    ('end-mid-C', sfL[2]['sync_sample']),
                    ('end-mid-F', sfL[5]['sync_sample'])]
            case_cuts(y, side, ambe, 'start', starts, f'start-cut {rate_name}')
            case_cuts(y, side, ambe, 'end', ends, f'end-cut {rate_name}')

        case_snr(5, ['--sync', 'ms', '--slot', '1', '--cc', '3', '--superframes', '5',
                     '--fs', '48000'], range(20), 'MS 12 dB, 20 seeds, 48k')
        case_snr(5, ['--sync', 'ms', '--slot', '1', '--cc', '3', '--superframes', '5',
                     '--fs', '1e6', '--offset', '25000'], range(20),
                 'MS 12 dB, 20 seeds, 1M +25 kHz')
        case_noise_and_other_cc(tmp)
        case_false_counts(tmp)
        case_emb_error_limit(tmp)
        case_min_emb(tmp)
        case_edge_guard(tmp)
        case_timing_radius(tmp)
        case_residual_gate(tmp)

    if failures:
        line = f'FAIL {len(failures)}: {failures}'
    elif n_skip:
        line = (f'PASS ({n_skip} spec pins skipped: docs/ptt/dmr/text/ not present)')
    else:
        line = 'PASS'
    print(line, flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
