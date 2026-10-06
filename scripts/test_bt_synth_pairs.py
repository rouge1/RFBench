#!/usr/bin/env python3
"""Hold the master-and-slave pair writer to a recording whose truth is planted.

    python scripts/test_bt_synth_pairs.py                  # a small run, about a minute
    python scripts/test_bt_synth_pairs.py --file DIR/synth_pairs_dh1_hop20.cf32
                                                           # the sample-side checks on a real file

``scripts/bt_synth_pairs.py`` writes 40 MS/s files in which every master DH1
is answered by a slave DH1 one slot later on the same channel. Nothing about
the result is known to the samples, so this makes a small run (40 pairs) and
measures what is there against what the sidecar says, from the samples alone:

* where each burst is, found from the power envelope and then by correlating
  the access code of the LAP on the channel the samples show;
* each burst's carrier, measured on the raw samples and rounded to a channel,
  and its level, the power in the burst less the planted noise;
* that the slave starts exactly one slot (25,000 samples) after its master,
  on the same carrier, before the timing fraction;
* that the idle slots hold the noise floor and nothing else;
* that every burst's bits, at 40 dB, are the packet the sidecar's clock builds
  - the header's HEC, type and whitening included, and that the slave's
  whitening is its own clock's and not the master's;
* that the channels are bt_hop's for the master's clock, and the clocks are
  master and slave slots;
* that a pair is recoverable from the file alone: bursts exactly one slot apart
  on the same channel, the earlier the master.

The checks that need only the plan (the idle-gap distribution, how many pairs
land on channel 39) run on the full 400 pairs, which costs no samples.
"""
import contextlib
import io
import json
import os
import sys
import tempfile

import numpy as np
from scipy.signal import fftconvolve, firwin, lfilter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from apps import bt_hop  # noqa: E402
from scripts import bt_synth, bt_synth_pairs as pairs  # noqa: E402

FS = 40e6
SPS = int(FS / br.SYMBOL_RATE)                   # 40 samples a symbol
SLOT = 25000                                     # 625 us at 40 MS/s
CENTER = 2441.0
LAP, UAP = 0x9E8B33, 0x47
MAP = list(range(31, 51))
MASK = sum(1 << c for c in MAP)
ADDRESS = UAP << 24 | LAP
CLK0 = 0x0123400
NOISE_POWER = bt_synth.AMPLITUDE ** 2 / 100 * FS / 1e6   # per sample, over all 40 MHz
DH1_BITS = 72 + 54 + 8 * (1 + 27 + 2)            # access code, header 1/3, payload header + body + CRC
failures = []

#: A 0.7 MHz low-pass at 40 MS/s, as test_bt_synth_hop.py: 401 taps, group delay GD.
TAPS = firwin(401, 0.7e6, fs=FS)
GD = len(TAPS) // 2


def check(ok, what):
    print('  %s %s' % ('ok  ' if ok else 'FAIL', what))
    if not ok:
        failures.append(what)


# --- reading a recording the way a receiver has to ----------------------------


def rough_bursts(iq):
    """Where the power envelope says the bursts are: ``(start, end)`` sample
    pairs, from the 200-sample mean of |iq|^2 against twice the planted noise
    power. Good to about 100 samples; the access code's correlation does the
    rest."""
    n = len(iq)
    p = np.empty(n)
    for lo in range(0, n, 1 << 22):                      # |iq|^2 in blocks
        x = iq[lo:lo + (1 << 22)]
        p[lo:lo + len(x)] = x.real.astype(np.float64) ** 2 + x.imag.astype(np.float64) ** 2
    c = np.concatenate([[0.0], np.cumsum(p)])
    del p
    w = 200
    mean = (c[w:] - c[:-w]) / w                          # mean over [i, i + w)
    up = mean > 2 * NOISE_POWER
    edges = np.flatnonzero(np.diff(up.astype(np.int8)))
    starts, ends = edges[::2] + 1 + w // 2, edges[1::2] + 1 + w // 2
    # noise on a ramp makes the mean chatter across the threshold: a crossing
    # within 500 samples of the last is the same edge, and a span under 5,000
    # samples (a DH1 is 14,640) is not a burst
    spans = []
    for a, b in zip(starts.tolist(), ends.tolist()):
        if spans and a - spans[-1][1] < 500:
            spans[-1][1] = b
        else:
            spans.append([a, b])
    return [(a, b) for a, b in spans if b - a > 5000]


def carrier_of(iq, s0, s1):
    """The carrier of a burst, MHz, from the middle of its rough span: the
    centroid of its spectrum within 500 kHz of the spectrum's peak (a mean
    frequency from phase steps drowns in the noise of all 40 MHz), and the
    nearest channel to it."""
    x = np.asarray(iq[s0 + 2000:s1 - 2000])
    if len(x) < 4096:                                    # a span too short to measure: not a burst
        return float('nan'), -1
    x = x[:len(x) // 4096 * 4096].reshape(-1, 4096) * np.hanning(4096)
    spec = np.sum(np.abs(np.fft.fft(x, axis=1)) ** 2, axis=0)
    freq = np.fft.fftfreq(4096, 1 / FS)
    near = np.abs(freq - freq[int(np.argmax(spec))]) < 500e3
    mhz = CENTER + np.sum(freq[near] * spec[near]) / np.sum(spec[near]) / 1e6
    return mhz, int(round(mhz - 2402))


def shifted_down(iq, lo, hi, channel):
    n = np.arange(lo, hi)
    cycles = (2402.0 + channel - CENTER) * 1e6 / FS * n
    return lfilter(TAPS, 1, np.asarray(iq[lo:hi]) * np.exp(-2j * np.pi * (cycles - np.floor(cycles))))


def demod(iq, rough_start, channel, nbits=DH1_BITS):
    """Shift a burst down from ``channel``, find the access code of the LAP by
    correlating, and slice. Returns ``(start, bits)``, ``start`` the first
    sample of the preamble as the correlation puts it (good to a sample),
    ``None`` if the access code is not there."""
    lo = max(0, rough_start - 1500)
    hi = min(len(iq), rough_start + nbits * SPS + 3000)
    bb = shifted_down(iq, lo, hi, channel)
    d = np.angle(bb[1:] * np.conj(bb[:-1]))
    a72 = np.array(br.access_code(LAP)) * 2 - 1
    tmpl = np.repeat(a72, SPS).astype(float)
    tmpl -= tmpl.mean()
    dm = d - np.convolve(d, np.ones(2001) / 2001, 'same')
    c = fftconvolve(dm, tmpl[::-1], 'valid')
    norm = np.sqrt(fftconvolve(dm ** 2, np.ones(len(tmpl)), 'valid')) * np.linalg.norm(tmpl)
    r = c / np.maximum(norm, 1e-12)
    p = int(np.argmax(r))
    if r[p] < 0.5:
        return None, None
    centres = p + (np.arange(nbits) + 0.5) * SPS - 0.5
    f = np.interp(centres, np.arange(len(d)), d)
    _, thr = np.polyfit(a72, f[:72], 1)
    return lo + p - GD, (f > thr).astype(int)


def burst_power(iq, start):
    """Mean |iq|^2 over the middle of a DH1 burst, away from its 2 us ramps."""
    x = np.asarray(iq[start + 400:start + (DH1_BITS - 10) * SPS])
    return float(np.mean(x.real.astype(np.float64) ** 2 + x.imag.astype(np.float64) ** 2))


def measure(iq, side):
    """Everything read from the samples alone, per rough burst: a list of dicts
    with ``rough``, ``mhz``, ``channel``, ``start`` and ``bits``."""
    rough = rough_bursts(iq)
    out = []
    for s0, s1 in rough:
        mhz, channel = carrier_of(iq, s0, s1)
        start, bits = (None, None) if channel < 0 else demod(iq, s0, channel)
        out.append(dict(rough=(s0, s1), mhz=mhz, channel=channel, start=start, bits=bits))
    return out


# --- the checks ---------------------------------------------------------------


def expected_clk(side, slot_index):
    return side['clk'] + 2 * slot_index


def check_plan_only():
    print('\nThe full plan, 400 pairs, no samples')
    side = pairs.plan_pairs(pairs=400, seed=6001)[0]
    ps = side['pairs']
    gaps = [p['idle_pairs_before'] for p in ps]
    freq = [gaps[1:].count(g) / (len(gaps) - 1) for g in (0, 1, 2)]
    check(set(gaps) <= {0, 1, 2} and gaps[0] == 0, 'idle pair-periods are 0, 1 or 2 (pair 0 has none)')
    check(all(abs(f - q) < 0.07 for f, q in zip(freq, (0.60, 0.25, 0.15))),
          'gap frequencies %.3f %.3f %.3f against 0.60 0.25 0.15 over %d gaps'
          % (*freq, len(gaps) - 1))
    # the same gaps from the slot numbers, not from the field
    recomputed = [(b['slot_index'] - a['slot_index']) // 2 - 1 for a, b in zip(ps, ps[1:])]
    check(recomputed == gaps[1:], 'idle_pairs_before is what the slot numbers of consecutive pairs say')
    centre = sum(1 for p in ps if p['channel'] == 39)
    check(centre >= 5 and centre == side['pairs_on_centre_channel'],
          '%d of 400 pairs are on the centre channel, 39 (2441 MHz); the sidecar says %d'
          % (centre, side['pairs_on_centre_channel']))
    check(all(p['channel'] in MAP for p in ps), 'every pair is on the map 31-50')
    check(side['n_pairs'] == 400 and len(ps) == 400 and side['centre_channel'] == 39,
          'n_pairs 400, centre_channel 39')
    same = sum(1 for a, b in zip(ps, ps[1:]) if b['slot_index'] == a['slot_index'] + 2 and a['channel'] == b['channel'])
    print('       (consecutive pairs back to back on one channel: %d)' % same)
    return side


def check_signal(iq, side, got, label):
    """Burst by burst against the sidecar: count, carrier, level, start, bits."""
    print('\nThe samples of %s: %d pairs, %.0f dB, slave %+.1f dB' % (
        label, side['n_pairs'], side['master_snr_db'], side['slave_offset_db']))
    bursts = side['bursts']
    check(len(got) == len(bursts), 'the envelope shows %d bursts, the sidecar has %d' % (len(got), len(bursts)))
    if len(got) != len(bursts):
        return
    check(all(g['start'] is not None for g in got), 'the access code of the LAP is found in every burst')
    if any(g['start'] is None for g in got):
        return
    check([g['channel'] for g in got] == [b['channel'] for b in bursts],
          'every burst\'s carrier, measured on the raw samples, is its channel (%d of %d)'
          % (sum(g['channel'] == b['channel'] for g, b in zip(got, bursts)), len(bursts)))
    resid = np.array([g['mhz'] - b['channel_mhz'] for g, b in zip(got, bursts)]) * 1e3
    check(np.abs(resid).max() < 100, 'and within %.1f kHz of it (the data\'s own mean over ~300 bits is the rest; a channel is 1000)' % np.abs(resid).max())
    off = np.array([g['start'] - b['start_sample'] for g, b in zip(got, bursts)])
    # the correlation peak is good to a sample, the timing fraction (< 1) is
    # in the samples and not in start_sample
    # the correlation's own jitter: sigma 0.3 samples at 40 dB, 0.8 at 20 dB, from 40 pairs
    tol = 2 if side['master_snr_db'] >= 30 else 3
    check(np.abs(off).max() <= tol, 'start_sample is where the access code is: worst %d samples off (within %d)'
          % (np.abs(off).max(), tol))
    want_db = np.array([b['snr_db'] for b in bursts])
    p = np.array([burst_power(iq, b['start_sample']) for b in bursts]) - NOISE_POWER
    expect = bt_synth.AMPLITUDE ** 2 / 100 * 10 ** (want_db / 10)
    err = 10 * np.log10(p / expect)
    check(np.abs(err).max() < 0.5, 'levels from the samples (power less the noise) are within %.2f dB of '
          'the snr_db against the noise in 1 MHz' % np.abs(err).max())
    # the mean over pairs of the measured spacing less what the sidecar says it
    # is: a slave rendered half a sample off, or a timing_frac not applied in the
    # samples, moves it by 0.5 or the mean fraction; the common group delay cancels
    res = np.array([got[p['slave']]['start'] - got[p['master']]['start'] - SLOT
                    - (bursts[p['slave']]['timing_frac'] - bursts[p['master']]['timing_frac'])
                    for p in side['pairs']])
    lim = 0.2 if side['master_snr_db'] >= 30 else 0.5
    check(abs(res.mean()) <= lim, 'mean over %d pairs of (measured spacing - %d - timing_frac difference) is %+.3f '
          '+/- %.3f samples (within %.1f: catches a slave half a sample off or timing_frac left out)'
          % (len(res), SLOT, res.mean(), res.std() / np.sqrt(len(res)), lim))
    roles = greedy_roles([g['start'] for g in got], [g['channel'] for g in got], 25)
    check(roles == [b['role'] for b in bursts],
          'the naive rule on the MEASURED starts and carriers (within 25 samples of a slot, greedy from the '
          'left) labels all %d bursts as the sidecar does' % len(bursts))
    couples, same = derived_back_to_back([g['start'] for g in got], [g['channel'] for g in got],
                                         [b['pair'] for b in bursts], 25)
    check((couples, same) == (side['back_to_back_couples'], side['same_channel_back_to_back']),
          'and the measured starts give the sidecar\'s back-to-back counts, %d of %d' % (same, couples))
    for role in ('master', 'slave'):
        sel = [i for i, b in enumerate(bursts) if b['role'] == role]
        check(len(set(want_db[sel])) == 1, '%s snr_db %.1f on all %d' % (role, want_db[sel][0], len(sel)))
    check(side['slave_offset_db'] == want_db[1] - want_db[0],
          'slave_offset_db is the slave\'s snr minus the master\'s')
    # pairs, from the measured starts
    for pr in side['pairs']:
        m, s = pr['master'], pr['slave']
        d = got[s]['start'] - got[m]['start']
        fm, fs_ = bursts[m]['timing_frac'], bursts[s]['timing_frac']
        if abs(d - (SLOT + fs_ - fm)) > (1.5 if side['master_snr_db'] >= 30 else 4) or got[m]['channel'] != got[s]['channel']:
            check(False, 'pair %d: slave %d samples after master on %d and %d' % (
                pr['pair'], d, got[m]['channel'], got[s]['channel']))
            break
    else:
        d = np.array([got[p['slave']]['start'] - got[p['master']]['start'] for p in side['pairs']])
        check(True, 'measured on the samples, every slave is one slot after its master (%d to %d samples, '
              'timing fractions in the rest) on the same carrier' % (d.min(), d.max()))
    check(all(bursts[p['slave']]['start_sample'] - bursts[p['master']]['start_sample'] == SLOT for p in side['pairs']),
          'and start_sample says exactly %d, before timing_frac, for all pairs' % SLOT)


def check_noise(iq):
    print('\nThe noise floor, before the first burst')
    x = np.asarray(iq[:50000])
    power = float(np.mean(x.real.astype(np.float64) ** 2 + x.imag.astype(np.float64) ** 2))
    check(abs(10 * np.log10(power / NOISE_POWER)) < 0.2,
          'noise from sample 0: %.4f per sample, %+.2f dB against the one floor %.4f (AMPLITUDE^2/100 in 1 MHz x 40)'
          % (power, 10 * np.log10(power / NOISE_POWER), NOISE_POWER))


def check_idle(iq, side):
    print('\nThe idle slots hold the noise floor and nothing else')
    taken = {b['slot_index'] for b in side['bursts']}
    last = max(taken)
    lo = min(b['start_sample'] for b in side['bursts'])
    first = lo - min(b['slot_index'] for b in side['bursts']) * SLOT
    idle = [s for s in range(0, last) if s not in taken and (s - 1) not in taken and (s + 1) not in taken]
    levels = []
    for s in idle[:60]:
        # a slot nothing starts in, and neither does a neighbour: the whole slot
        # from its nominal burst start on, where a burst would have been
        a = first + s * SLOT
        x = np.asarray(iq[a:a + 15000])
        levels.append(np.mean(x.real.astype(np.float64) ** 2 + x.imag.astype(np.float64) ** 2))
    levels = np.array(levels)
    check(len(levels) > 0 and np.abs(levels / NOISE_POWER - 1).max() < 0.05,
          '%d idle slots: power %.4f to %.4f against the planted noise %.4f' % (
              len(levels), levels.min(), levels.max(), NOISE_POWER))
    starts = np.array([b['start_sample'] for b in side['bursts']])
    # a pair is moved as a whole by 0 or 20 samples, so the next pair's master
    # may be 20 samples closer to the last slave than a slot
    check(np.all(np.diff(starts) >= SLOT - 20), 'no two bursts start less than a slot (less the 20-sample phase shift) apart (%d)' % np.diff(starts).min())
    air = DH1_BITS * SPS
    check(np.all(np.diff(starts) > air + 200), 'so none overlaps the next: a DH1 is %d samples, the least gap is %d'
          % (air, np.diff(starts).min()))


def check_roles_and_channels(side):
    print('\nRoles, slots, clocks and channels')
    bursts = side['bursts']
    ps = side['pairs']
    check(all(bursts[p['master']]['role'] == 'master' and bursts[p['slave']]['role'] == 'slave'
              and p['slave'] == p['master'] + 1 for p in ps), 'master first in every pair, then its slave')
    check(all(bursts[b['partner']]['partner'] == i and bursts[b['partner']]['pair'] == b['pair']
              and bursts[b['partner']]['role'] != b['role'] for i, b in enumerate(bursts)),
          'partner points both ways, to the other role, in the same pair')
    check(all(b['slot_index'] % 2 == (0 if b['role'] == 'master' else 1) for b in bursts),
          'slot_index is even for a master, odd for a slave')
    check(all(bursts[p['slave']]['slot_index'] == bursts[p['master']]['slot_index'] + 1 for p in ps),
          'the lower slot of a pair is the master\'s: the slave is the next slot')
    # the slot grid from the samples' own start numbers, not the field
    first = SLOT * 5 // 2                                # LEAD_SLOTS = 2.5 slots, half a slot off sample 0
    shift = [(b['start_sample'] - first) % SLOT for b in bursts]
    check(all(s in (0, 20) for s in shift) and all(
        (b['start_sample'] - first - s) // SLOT == b['slot_index'] for b, s in zip(bursts, shift)),
          'slot_index is the slot the start_sample is in, on a grid half a slot off sample 0 '
          '(%d mod %d)' % (first % SLOT, SLOT))
    check(all(b['clk'] == expected_clk(side, b['slot_index']) for b in bursts),
          'clk is the first clock plus two ticks a slot')
    check(all(b['clk'] % 4 == (0 if b['role'] == 'master' else 2) for b in bursts),
          'a master\'s clk is a master slot (clk %% 4 == 0), a slave\'s is clk %% 4 == 2')
    chan = [bt_hop.hop_channel(bursts[p['master']]['clk'], ADDRESS, MASK) for p in ps]
    check(all(bursts[p['master']]['channel'] == c and bursts[p['slave']]['channel'] == c
              for p, c in zip(ps, chan)), 'both of every pair are on hop_channel(master clk) over the map')
    check(all(bt_hop.hop_channel(bursts[p['slave']]['clk'], ADDRESS, MASK) == bursts[p['slave']]['channel']
              for p in ps), 'and the kernel gives the slave\'s own clock that same channel')
    check(all(b['channel_mhz'] == 2402 + b['channel'] and b['ptype'] == 'DH1' for b in bursts),
          'channel_mhz is 2402 + channel; every packet is a DH1')
    ph = [b['symbol_phase'] for b in bursts]
    check(all(b['symbol_phase'] == b['start_sample'] % SPS for b in bursts), 'symbol_phase is start_sample %% %d' % SPS)
    check(all(bursts[p['master']]['symbol_phase'] == (0 if p['pair'] % 2 == 0 else 20)
              and bursts[p['slave']]['symbol_phase'] == bursts[p['master']]['symbol_phase'] for p in ps),
          'pair k starts at phase 0 (even) or 20 (odd), master and slave together: %s' % sorted(set(ph)))


def greedy_roles(starts, channels, tol):
    """The naive rule, greedily from the left: of bursts in time order, the
    earliest unpaired one is a master if the next is within ``tol`` samples of
    one slot after it on the same channel. Returns a role or None per burst."""
    roles = [None] * len(starts)
    order = sorted(range(len(starts)), key=lambda i: starts[i])
    i = 0
    while i < len(order):
        a = order[i]
        if i + 1 < len(order):
            b = order[i + 1]
            if abs(starts[b] - starts[a] - SLOT) <= tol and channels[a] == channels[b]:
                roles[a], roles[b] = 'master', 'slave'
                i += 2
                continue
        i += 1
    return roles


def derived_back_to_back(starts, channels, pair_of, tol):
    """(couples, same-channel couples) read off the start samples: a burst of
    one pair followed, within ``tol`` of a slot, by a burst of another."""
    order = sorted(range(len(starts)), key=lambda i: starts[i])
    couples = [(a, b) for a, b in zip(order, order[1:])
               if abs(starts[b] - starts[a] - SLOT) <= tol and pair_of[a] != pair_of[b]]
    return len(couples), sum(channels[a] == channels[b] for a, b in couples)


def check_naive_rule(side):
    print('\nA pair is recoverable from the file alone')
    bursts = side['bursts']
    order = sorted(range(len(bursts)), key=lambda i: bursts[i]['start_sample'])
    found = {}                                           # burst -> (role, partner)
    i = 0
    while i < len(order):
        a = bursts[order[i]]
        nxt = bursts[order[i + 1]] if i + 1 < len(order) else None
        if (nxt is not None and nxt['start_sample'] - a['start_sample'] == SLOT
                and nxt['channel'] == a['channel']):
            found[order[i]] = ('master', order[i + 1])
            found[order[i + 1]] = ('slave', order[i])
            i += 2
        else:
            found[order[i]] = (None, None)
            i += 1
    check(all(found[i][0] == b['role'] and found[i][1] == b['partner'] for i, b in enumerate(bursts)),
          'exactly one slot apart on the same channel, the earlier the master, reproduces role and '
          'partner of all %d bursts, from the start samples alone' % len(bursts))
    # the same rule with the tolerance a receiver's timing needs: within half a
    # symbol (20 samples) of a slot, the earlier unpaired burst a master
    tol = {}
    i = 0
    while i < len(order):
        a = bursts[order[i]]
        nxt = bursts[order[i + 1]] if i + 1 < len(order) else None
        if (nxt is not None and abs(nxt['start_sample'] - a['start_sample'] - SLOT) <= 20
                and nxt['channel'] == a['channel']):
            tol[order[i]], tol[order[i + 1]] = 'master', 'slave'
            i += 2
        else:
            i += 1
    check(all(tol.get(i) == b['role'] for i, b in enumerate(bursts)),
          'and the same, within 20 samples of a slot, greedily from the left')
    back = [(a, b) for a, b in zip(order, order[1:])
            if abs(bursts[b]['start_sample'] - bursts[a]['start_sample'] - SLOT) <= 20
            and bursts[a]['channel'] == bursts[b]['channel'] and bursts[a]['pair'] != bursts[b]['pair']]
    print('       (a slave a slot, give or take 20 samples, before the next pair\'s master on the same '
          'channel: %d; exactly a slot: %d)' % (len(back), sum(
              bursts[b]['start_sample'] - bursts[a]['start_sample'] == SLOT for a, b in back)))
    couples, same = derived_back_to_back([b['start_sample'] for b in bursts], [b['channel'] for b in bursts],
                                         [b['pair'] for b in bursts], 20)
    check((couples, same) == (side['back_to_back_couples'], side['same_channel_back_to_back']) and same >= 1,
          'the sidecar\'s back_to_back_couples %d and same_channel_back_to_back %d are what its own starts and '
          'channels give (%d, %d), and this run has at least one same-channel couple'
          % (side['back_to_back_couples'], side['same_channel_back_to_back'], couples, same))
    seq = [b['seqn'] for b in bursts if b['role'] == 'slave']
    check(all(x != y for x, y in zip(seq, seq[1:])) and all(b['arqn'] == 1 for b in bursts if b['role'] == 'slave'),
          'the slave\'s seqn toggles from pair to pair, its arqn is 1')
    ctr = [b for b in bursts if b['channel'] == side['centre_channel']]
    check(len(ctr) >= 4 and side['pairs_on_centre_channel'] * 2 == len(ctr),
          '%d bursts (%d pairs) are on the centre channel %d, so the DC-block case is in the run'
          % (len(ctr), side['pairs_on_centre_channel'], side['centre_channel']))
    gap = [(a, b) for a, b in zip(order, order[1:])
           if bursts[b]['slot_index'] - bursts[a]['slot_index'] > 1 and found[a][1] == b]
    check(not gap, 'no pair is made across an idle gap')


def check_bits(got, side):
    print('\nBits of every burst at 40 dB: the header, and whose clock whitened it')
    bursts = side['bursts']
    bad_bits, bad_hdr, wrong = [], [], []
    for g, b in zip(got, bursts):
        bits = g['bits']
        want = br.Packet(LAP, UAP, b['clk'], 'DH1', bytes.fromhex(b['payload_hex']), lt_addr=1, flow=1,
                         arqn=b['arqn'], seqn=b['seqn'])
        bad_bits.append(int((bits != np.array(want.bits)).sum()))
        # the header, from the samples: majority of each triple, dewhitened with
        # the sidecar's clk for this burst (check_roles_and_channels ties it to the
        # clock the slot grid gives), HEC checked
        triples = bits[72:126].reshape(18, 3).sum(axis=1) >= 2
        hdr = [int(x) ^ w for x, w in zip(triples, br.whitening(b['clk'], 18))]
        ok = br.hec(hdr[:10], UAP) == hdr[10:18]
        v = br.bits_int(hdr)
        ok = ok and (v & 7) == 1 and (v >> 3) & 15 == br.PACKET_TYPES['DH1'][0]
        ok = ok and (v >> 8) & 1 == b['arqn'] and (v >> 9) & 1 == b['seqn']
        bad_hdr.append(not ok)
        if b['role'] == 'slave':
            other = br.Packet(LAP, UAP, b['clk'] - 2, 'DH1', bytes.fromhex(b['payload_hex']), lt_addr=1,
                              flow=1, arqn=b['arqn'], seqn=b['seqn'])
            wrong.append(int((bits[72:126] != np.array(other.bits[72:126])).sum()))
    check(max(bad_bits) == 0, '%d bursts, bit errors in all: %d, worst %d' % (len(bursts), sum(bad_bits), max(bad_bits)))
    check(not any(bad_hdr), 'every header: LT_ADDR 1, type DH1, ARQN and SEQN as the sidecar says, HEC good '
          'under the UAP, whitened with that burst\'s own clock (%d bad)' % sum(bad_hdr))
    check(min(wrong) > 0, 'a slave\'s header is not the header the master\'s clock would give: at least %d of 54 '
          'air bits differ, so its whitening is its own clock\'s' % min(wrong))
    arqn = {b['role']: b['arqn'] for b in bursts}
    check(arqn['slave'] == 1 and all(b['arqn'] == 1 and b['lt_addr'] == 1 and b['flow'] == 1 for b in bursts),
          'slave arqn 1; lt_addr 1 and flow 1 throughout')
    pl = [(bursts[p['master']]['payload_hex'], bursts[p['slave']]['payload_hex']) for p in side['pairs']]
    check(all(m != s for m, s in pl), 'master and slave payloads differ in every pair (a different SEQ stream)')
    check(len({m for m, _ in pl} | {s for _, s in pl}) == 2 * len(pl), 'and no two of the %d payloads are alike' % (2 * len(pl)))
    check(all(len(bytes.fromhex(b['payload_hex'])) == 27 for b in bursts), 'every payload is a full DH1, 27 bytes')


def check_sidecar(side, iq=None):
    print('\nThe sidecar')
    top = ['generator', 'generator_commit', 'lap', 'uap', 'sample_rate', 'center_mhz', 'modulation',
           'slot_samples', 'start_sample_meaning', 'clk_convention', 'snr_bw_hz', 'bursts', 'clk',
           'air_bits_omitted', 'noise_1mhz', 'symbol_phases', 'pairs', 'n_pairs', 'pair_note',
           'centre_channel', 'pairs_on_centre_channel', 'hopping', 'hop_channels', 'afh_map',
           'afh_map_count', 'afh_maps', 'afh_instant', 'afh_instant_meaning', 'clock_lock_note', 'timing_frac',
           'start_offset', 'symbol_phase', 'back_to_back_couples', 'same_channel_back_to_back', 'chain_note']
    check(all(k in side for k in top), 'the top level has all %d of its keys' % len(top))
    check(side['sample_rate'] == FS and side['center_mhz'] == CENTER and side['lap'] == LAP
          and side['uap'] == UAP and side['slot_samples'] == SLOT and side['snr_bw_hz'] == 1e6
          and side['air_bits_omitted'] is True, '40 MS/s, 2441 MHz, the LAP and UAP, slot 25000, air_bits_omitted')
    check(side['noise_1mhz'] == bt_synth.AMPLITUDE ** 2 / 100 and side['symbol_phases'] == [0, 20],
          'noise_1mhz is AMPLITUDE^2 / 100 and symbol_phases [0, 20]')
    keys = ['start_sample', 'timing_frac', 'symbol_phase', 'clk', 'ptype', 'channel', 'channel_mhz', 'snr_db',
            'role', 'pair', 'partner', 'slot_index', 'lt_addr', 'flow', 'arqn', 'seqn', 'header18', 'payload_hex']
    check(all(all(k in b for k in keys) for b in side['bursts']), 'every burst has all %d of its keys' % len(keys))
    check(not any('air_bits' in b for b in side['bursts']), 'and no air_bits')
    check(all(0 <= b['timing_frac'] < 1 for b in side['bursts'])
          and len({b['timing_frac'] for b in side['bursts']}) > len(side['bursts']) // 2,
          'timing fractions are in [0, 1) and differ burst to burst')
    check(side['afh_map_count'] == 1 and side['afh_maps'] == [{'instant': side['clk'] >> 1, 'first_burst': 0,
                                                              'channels': MAP}]
          and side['afh_instant'] == side['clk'] >> 1 and all(b['afh_map_index'] == 0 for b in side['bursts'])
          and side['start_offset'] == 0,
          'afh_map_count/afh_maps/afh_instant, afh_map_index on every burst, start_offset 0 at the top')
    check(side['timing_frac'] is None and side['symbol_phase'] is None and side['snr_db'] is None
          and side['master_snr_db'] == side['bursts'][0]['snr_db'],
          'top-level timing_frac, symbol_phase and snr_db are null (they vary per burst); master_snr_db is the master\'s')
    check(isinstance(side.get('per_burst_keys'), list) and {'timing_frac', 'symbol_phase', 'snr_db', 'start_sample',
          'clk', 'channel', 'role'} <= set(side['per_burst_keys'])
          and all(k in b for b in side['bursts'] for k in side['per_burst_keys']),
          'per_burst_keys names %d keys, each present in every burst' % len(side.get('per_burst_keys', [])))
    check(all(side['pair_note'].count(w) for w in ('24,999 to 25,001', 'lower slot')) and 'EVERY BURST IS DETECTED' in side['chain_note'],
          'pair_note gives the sample spacing, chain_note the "every burst detected" caveat')
    check(all(set(p) >= {'pair', 'master', 'slave', 'channel', 'slot_index', 'idle_pairs_before'} for p in side['pairs']),
          'every pairs entry has pair, master, slave, channel, slot_index, idle_pairs_before')
    check(all(p['channel'] == side['bursts'][p['master']]['channel'] and p['slot_index'] == side['bursts'][p['master']]['slot_index']
              and side['bursts'][p['master']]['pair'] == p['pair'] for p in side['pairs']),
          'and they say what the bursts say')
    check(isinstance(side['pair_note'], str) and 'lower slot' in side['pair_note'], 'pair_note states the rules')
    json.dumps(side)


def check_determinism_and_files():
    print('\nA seed is a recording; the file path; the named set')
    kw = dict(pairs=8, seed=5)
    a, sa = pairs.synthesise_pairs(**kw)
    b, sb = pairs.synthesise_pairs(**kw)
    c, sc = pairs.synthesise_pairs(**dict(kw, seed=6))
    check(a.tobytes() == b.tobytes() and json.dumps(sa) == json.dumps(sb), 'the same arguments give the same bytes')
    check(a.tobytes() != c.tobytes() and sa['pairs'] != sc['pairs'], 'another seed gives other samples and other gaps')
    # bursts straddling blocks: the signal alone, in small blocks and in one
    big, _ = pairs.synthesise_pairs(pairs=8, seed=5, noise=False)
    small, _ = pairs.synthesise_pairs(pairs=8, seed=5, noise=False, block=1 << 14)
    check(np.array_equal(big, small), 'bursts that cross a block boundary come out the same in 16k-sample blocks')
    with tempfile.TemporaryDirectory() as d:
        iq_path, side_path = pairs.write_capture('t', d, **kw)
        on_disk = np.fromfile(iq_path, dtype='<c8')
        check(np.array_equal(on_disk, a) and json.load(open(side_path)) == json.loads(json.dumps(sa)),
              'write_capture writes the same samples and the same sidecar, blockwise, to disk')
        main_small = [('pairs_dh1_hop20', dict(pairs=6, seed=6001))]
        saved = pairs.SETS['pairs']
        try:
            pairs.SETS['pairs'] = main_small
            pairs.main(['--set', 'pairs', '--out', d])
        finally:
            pairs.SETS['pairs'] = saved
        names = sorted(os.listdir(d))
        check(names == ['synth_pairs_dh1_hop20.cf32', 'synth_pairs_dh1_hop20.json', 'synth_t.cf32', 'synth_t.json'],
              '--set pairs writes the one named file by the same path: %s' % names)
        pairs.main(['t2', '--pairs', '4', '--map', '31-50', '--snr', '25', '--slave-offset-db', '-3',
                    '--idle-probs', '1,0,0', '--start-phase', '20', '--out', d])
        s2 = json.load(open(os.path.join(d, 'synth_t2.json')))
        check(s2['bursts'][0]['symbol_phase'] == 20 and s2['bursts'][1]['snr_db'] == 22
              and all(p['idle_pairs_before'] == 0 for p in s2['pairs']),
              '--start-phase 20, --slave-offset-db -3, --idle-probs 1,0,0 do what they say')
    check([n for n, _ in pairs.SETS['pairs']] == ['pairs_dh1_hop20'] and list(pairs.SETS) == ['pairs'],
          'the set holds the one name, pairs_dh1_hop20')
    for argv in (['--set', 'nosuch'], ['--set', 'pairs', 'name'], ['--map', '1-5']):
        try:
            with tempfile.TemporaryDirectory() as d, contextlib.redirect_stderr(io.StringIO()):
                pairs.main(argv + ['--out', d])
            check(False, '%s must be refused' % argv)
        except SystemExit as e:
            check(e.code != 0, '%s is refused' % ' '.join(argv))
    try:                                    # a map the 40 MS/s tuning cannot hold
        pairs.plan_pairs(pairs=2, channels=list(range(20, 40)))
        check(False, 'channels outside the window must raise')
    except ValueError:
        check(True, 'a map with channels the tuning cannot hold raises ValueError')


def read_sidecar(path):
    with open(path) as f:
        return json.load(f)


def main(argv):
    if argv[:1] == ['--file']:
        iq = np.memmap(argv[1], dtype='<c8', mode='r')
        side = read_sidecar(argv[1][:-5] + '.json')
        print('A real file: %s, %d samples, %d bursts' % (argv[1], len(iq), len(side['bursts'])))
        got = measure(iq, side)
        check_signal(iq, side, got, 'the file')
        check_idle(iq, side)
        check_roles_and_channels(side)
        check_naive_rule(side)
        check_sidecar(side)
        errs = [int((g['bits'] != np.array(br.Packet(LAP, UAP, b['clk'], 'DH1', bytes.fromhex(b['payload_hex']),
                                                    lt_addr=1, flow=1, arqn=b['arqn'], seqn=b['seqn']).bits)).sum())
                for g, b in zip(got, side['bursts'])]
        print('       (bit errors from this plain slicer at %.0f dB: %d of %d bursts clean, worst %d)'
              % (side['master_snr_db'], errs.count(0), len(errs), max(errs)))
        print('\nRESULT: %s' % ('FAIL (%d)' % len(failures) if failures else 'PASS'))
        return 1 if failures else 0
    plan = check_plan_only()
    iq, side = pairs.synthesise_pairs(pairs=80, seed=6002, snr_db=20.0)
    print('\nA small run: 80 pairs, %d samples, %.1f ms' % (len(iq), len(iq) / FS * 1e3))
    check_sidecar(side)
    check_roles_and_channels(side)
    check_naive_rule(side)
    check_noise(iq)
    got = measure(iq, side)
    check_signal(iq, side, got, 'the 20 dB run')
    check_idle(iq, side)
    iq2, side2 = pairs.synthesise_pairs(pairs=80, seed=6003, snr_db=40.0, slave_offset_db=-6.0)
    check_noise(iq2)
    got2 = measure(iq2, side2)
    check_signal(iq2, side2, got2, 'the 40 dB run, slave 6 dB down')
    check(len(got2) == len(side2['bursts']) and all(g['start'] is not None for g in got2), 'the 40 dB run is all found')
    if len(got2) == len(side2['bursts']):
        check_bits(got2, side2)
    check_determinism_and_files()
    print('\nRESULT: %s' % ('FAIL (%d)' % len(failures) if failures else 'PASS'))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
