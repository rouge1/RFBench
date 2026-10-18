"""Tests of scripts/bt_synth_multislot.py (SET-N).

The plans of the four files are checked whole (traffic shares, slot rules, the AFH and the basic channel rule,
exchange ids, sender, the band, the opportunities recounted another way); short renders (0.31 s, a high level so a
burst's position can be refitted to a tenth of a sample) check the samples at 40 MS/s, after the decimation to
20 MS/s and with the interferer.

    python -B scripts/test_bt_synth_multislot.py
"""
import collections
import contextlib
import hashlib
import io
import json
import math
import os
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from apps import bt_hop  # noqa: E402
from scripts import bt_synth_interf as itf  # noqa: E402
from scripts import bt_synth_mixed as mx  # noqa: E402
from scripts import bt_synth_multislot as ms  # noqa: E402

RESULTS = []
HIGH = (40.0, 40.0)


def check(ok, what):
    RESULTS.append((bool(ok), what))
    print('  %s %s' % ('ok  ' if ok else 'FAIL', what), flush=True)


@contextlib.contextmanager
def quiet():
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def hex_bits(h, n):
    return [int(c) for c in bin(int(h, 16))[2:].zfill(len(h) * 4)[:n]]


def traffic(side):
    return [e for e in side['bursts'] + side['bursts_not_rendered'] if e.get('kind') == 'traffic']


# --- plans --------------------------------------------------------------------------------------

def check_plan(name, side):
    spec = ms.FILES[name]
    band = set(ms.BAND_20 if spec['fs'] == 20e6 else ms.WINDOW_40)
    tr = traffic(side)
    dev = {d['id']: d for d in side['devices']}
    masters = [e for e in tr if e['role'] == 'master']
    slaves = [e for e in tr if e['role'] == 'slave']
    # shares (all piconets pooled)
    ml = collections.Counter(e['slots'] for e in masters)
    sl = collections.Counter(e['slots'] for e in slaves)
    msh = {k: ml[k] / len(masters) for k in (1, 3, 5)}
    ssh = {k: sl[k] / len(slaves) for k in (1, 3, 5)}
    check(all(abs(msh[k] - p) < 0.015 for k, p in ms.SLOT_SHARES) and all(abs(ssh[k] - p) < 0.02 for k, p in ms.SLOT_SHARES),
          '%s: master L 1/3/5 at %.3f/%.3f/%.3f, answer L\' at %.3f/%.3f/%.3f (40/30/30 %%; %d and %d packets)' % (
              name, msh[1], msh[3], msh[5], ssh[1], ssh[3], ssh[5], len(masters), len(slaves)))
    check(all(e['ptype'] in ms.MASTER_BY_SLOTS[e['slots']] for e in masters) and
          all(e['ptype'] in ms.SLAVE_BY_SLOTS[e['slots']] for e in slaves) and
          all(e['slots'] == br.PACKET_TYPES[e['ptype']][1] for e in tr),
          '%s: types by slot count (masters NULL/POLL/DM1/DH1, DM3/DH3, DM5/DH5; answers NULL/DM1/DH1, DM3/DH3, DM5/DH5)' % name)
    # exchanges, slots, parity
    by_ex = collections.defaultdict(list)
    for e in tr:
        by_ex[e['exchange_id']].append(e)
    ok_ex = all(len(v) <= 2 and len({x['device'] for x in v}) == 1 and v[0]['exchange_id'] == v[0]['device'] * ms.EXCHANGE_BASE + v[0]['pair']
                for v in by_ex.values())
    ok_slots, ok_next, ok_parity = True, True, True
    per_dev = collections.defaultdict(list)
    for v in by_ex.values():
        m = [x for x in v if x['role'] == 'master']
        s = [x for x in v if x['role'] == 'slave']
        ok_ex = ok_ex and len(m) == 1 and len(s) <= 1
        if s:
            ok_slots = ok_slots and s[0]['piconet_slot'] == m[0]['piconet_slot'] + m[0]['slots']
        end = (s[0]['piconet_slot'] + s[0]['slots']) if s else (m[0]['piconet_slot'] + m[0]['slots'] + 1)
        per_dev[m[0]['device']].append((m[0]['piconet_slot'], end))
        ok_parity = ok_parity and m[0]['piconet_slot'] % 2 == 0 and (not s or s[0]['piconet_slot'] % 2 == 1)
    for d, rows in per_dev.items():
        rows.sort()
        ok_next = ok_next and all(b[0] >= a[1] and b[0] % 2 == 0 for a, b in zip(rows, rows[1:]))
    check(ok_ex, '%s: exchange_id = device * %d + pair, one master and at most one answer each, unique in the file (%d)' % (
        name, ms.EXCHANGE_BASE, len(by_ex)))
    check(ok_slots and ok_parity and ok_next,
          '%s: master in an even slot s, its answer in slot s + L (odd), the next master at s + L + L\' or later (s + L + 1 '
          'without an answer), even' % name)
    # channels: AFH same channel; basic f(k) and f(k + L)
    ok_afh, ok_basic, same_basic, n_basic = True, True, 0, 0
    hop_afh = {d: ms.channel_fn(dict(dev[d], afh=True)) for d in dev if dev[d]['kind'] == 'piconet' and dev[d]['afh']}
    for v in by_ex.values():
        m = [x for x in v if x['role'] == 'master'][0]
        s = [x for x in v if x['role'] == 'slave']
        d = dev[m['device']]
        addr = d['uap'] << 24 | d['lap']
        if d['afh']:
            ok_afh = ok_afh and m['channel'] == int(hop_afh[d['id']](m['clk'])) and m['channel'] in ms.MAP and \
                (not s or (s[0]['channel'] == m['channel'] and s[0]['hop_clk'] == m['clk']))
        else:
            ok_basic = ok_basic and m['channel'] == bt_hop.hop_channel(m['clk'], addr) and \
                (not s or (s[0]['channel'] == bt_hop.hop_channel(s[0]['clk'], addr) and
                           s[0]['clk'] == (m['clk'] + 2 * m['slots']) & 0x0FFFFFFF))
            if s:
                n_basic += 1
                same_basic += s[0]['channel'] == m['channel']
    check(ok_afh, '%s: AFH piconets: the master on the adapted sequence over 31-50, the answer on the master\'s channel' % name)
    check(ok_basic and n_basic > 100 and same_basic / n_basic < 0.05,
          '%s: the non-AFH piconet (device %d): the master on the basic sequence at its clock, the answer on the basic '
          'sequence at the clock of its own first slot (s + L); same channel in %d of %d exchanges' % (
              name, side['non_afh_device'], same_basic, n_basic))
    check(all(e['sender'] == e['role'] for e in tr) and
          all(e['sender'] in ('master', 'slave') for e in side['bursts'] + side['bursts_not_rendered']) and
          all(e['afh'] == dev[e['device']]['afh'] for e in side['bursts'] + side['bursts_not_rendered']),
          '%s: sender is master/slave on every burst (the role for a piconet, the exchange role for a joiner); afh is the '
          'device\'s' % name)
    # the band and the overlaps
    rend = side['bursts']
    check(all(e['channel'] in band and e['rendered'] for e in rend) and
          all(not e['rendered'] and (e['channel'] not in band or not e.get('in_window', True)) for e in side['bursts_not_rendered']) and
          all(e['index'] == i for i, e in enumerate(rend)) and
          all(0 <= o['burst'] < len(rend) for e in rend for o in e['overlaps']),
          '%s: rendered iff the channel is in the band (%d-%d): %d rendered, %d not; overlaps index rendered bursts only' % (
              name, min(band), max(band), len(rend), len(side['bursts_not_rendered'])))
    check(side['non_afh_burst_indices'] == [e['index'] for e in rend if e['device'] == side['non_afh_device']],
          '%s: non_afh_burst_indices lists the non-AFH piconet\'s rendered bursts (%d)' % (name, len(side['non_afh_burst_indices'])))
    # opportunities recounted from the exchanges
    opp = collections.Counter()
    slot_master = {(e['device'], e['piconet_slot']): e for e in rend if e.get('kind') == 'traffic' and e['role'] == 'master'}
    for e in rend:
        if e.get('kind') == 'traffic' and e['role'] == 'slave' and e['slots'] == 1:
            nxt = slot_master.get((e['device'], e['piconet_slot'] + 1))
            if nxt is not None and nxt['channel'] == e['channel']:
                opp['afh' if e['afh'] else 'basic'] += 1
    check(opp['afh'] == side['opportunities']['afh'] and opp['basic'] == side['opportunities']['basic'] and
          side['opportunities']['afh'] >= 70,
          '%s: %d wrong-role opportunities (AFH) and %d (non-AFH), recounted (%d collision-free)' % (
              name, opp['afh'], opp['basic'], side['opportunities']['afh_collision_free']))
    one = sum(1 for v in by_ex.values() if len(v) == 2 and sum(x['rendered'] for x in v) == 1)
    check(one == side['n_exchanges_one_member_rendered'], '%s: %d exchanges with one member rendered (the anchorless case)' % (name, one))


def test_plans():
    print('== plans (the four files, whole)', flush=True)
    plans = {}
    for name in ms.FILES:
        spec = ms.FILES[name]
        side, _, _ = ms.plan_setn(spec['seed'], band=ms.BAND_20 if spec['fs'] == 20e6 else ms.WINDOW_40, name=name)
        plans[name] = side
        check_plan(name, side)
    a, d = plans['setn_a'], plans['setn_d']
    check(json.dumps(a['bursts']) == json.dumps(d['bursts']), 'setn_d is planned as setn_a (the same bursts)')
    check(a['non_afh_device'] != plans['setn_b']['non_afh_device'] or a['devices'][0]['lap'] != plans['setn_b']['devices'][0]['lap'],
          'setn_a and setn_b are different draws')
    # the basic kernel is the one test_bt_hop.py checks against Part G
    check(bt_hop.hop_channel.__module__ == 'apps.bt_hop', 'the non-AFH hop is apps.bt_hop.hop_channel with no map '
          '(Part G checked in scripts/test_bt_hop.py)')


# --- samples ------------------------------------------------------------------------------------

def channel_lp(fs, cutoff_hz=0.75e6):
    """A zero-phase (odd, centred) low-pass that keeps one channel after mixing it to 0 Hz: the other piconets, busy
    on other channels at the same time, are out of it."""
    half = int(round(4e-6 * fs))
    m = np.arange(-half, half + 1)
    h = np.sinc(2 * cutoff_hz / fs * m) * np.kaiser(2 * half + 1, 8.0)
    return h / h.sum()


def baseband(iq, lo, hi, mhz, fs, h):
    """Samples lo..hi of the channel at ``mhz``, mixed to 0 Hz and through ``h`` (zero phase)."""
    pad = (len(h) - 1) // 2
    a, b = lo - pad, hi + pad
    n = np.arange(a, b)
    z = iq[a:b] * np.exp(-2j * np.pi * mhz * 1e6 / fs * n)
    return np.convolve(z, h, 'same')[pad:pad + hi - lo]


def fit_start(iq, e, fs, center_mhz, n_bits=126, span=1.0, step=0.02):
    """The start of a burst refitted from the samples: the model of its first bits correlated at fractional delays
    around the stated position, both through the same zero-phase channel filter."""
    bits = hex_bits(e['air_bits'], e['air_bits_length'])[:n_bits]
    s0 = e['start_sample'] + e['timing_frac']
    h = channel_lp(fs)
    best, best_v = None, -1.0
    for d in np.arange(s0 - span, s0 + span + 1e-9, step):
        i0 = int(math.floor(d))
        m, lead = br.gfsk(bits, fs, delay=d - i0)
        lo = i0 - lead
        if lo - len(h) < 0 or lo + len(m) + len(h) > len(iq):
            return None
        seg = baseband(iq, lo, lo + len(m), e['channel_mhz'] - center_mhz, fs, h)
        v = abs(np.vdot(np.convolve(m, h, 'same'), seg))
        if v > best_v:
            best, best_v = d, v
    return best


def demod_bits(iq, e, fs, center_mhz, n_bits):
    sps = fs / 1e6
    s0 = e['start_sample'] + e['timing_frac']
    lo, hi = int(s0) - 2, int(s0 + n_bits * sps) + 2
    z = baseband(iq, lo, hi, e['channel_mhz'] - center_mhz, fs, channel_lp(fs))
    ph = np.unwrap(np.angle(z))
    out = []
    for k in range(n_bits):
        a = s0 + k * sps - lo
        out.append(int(np.interp(a + sps, np.arange(len(ph)), ph) - np.interp(a, np.arange(len(ph)), ph) > 0))
    return out


def clean(side, n):
    """Up to ``n`` rendered collision-free bursts, spread over devices and roles."""
    pool = [e for e in side['bursts'] if not e['overlaps'] and e['start_sample'] > 2000]
    by = collections.defaultdict(list)
    for e in pool:
        by[(e['device'], e['sender'])].append(e)
    out = []
    while len(out) < n and any(by.values()):
        for k in sorted(by):
            if by[k]:
                out.append(by[k].pop(len(by[k]) // 2))
    return out[:n]


def check_samples(iq, side, label, n=24):
    """A sanity check from the samples, on a busy band (other piconets on other channels at the same time), at mixed's
    own standard of 0.5 sample: piconet bursts only (here at 40 dB; the joiners keep bt_synth_conf's 10-20 dB and are
    covered by the exact rebuild); the access-code bits exact."""
    fs, c = side['sample_rate'], side['center_mhz']
    picks = [e for e in clean(side, 3 * n) if e['device'] < ms.N_PICONETS][:n]
    errs = [fit_start(iq, e, fs, c, span=2.0) - (e['start_sample'] + e['timing_frac']) for e in picks]
    check(len(picks) >= 12 and max(abs(x) for x in errs) <= 0.5,
          '%s: %d collision-free piconet bursts (devices %s) refitted at start_sample + timing_frac within 0.5 sample (a sanity '
          'check; the exact checks are the rebuilds): worst %.3f' % (
              label, len(picks), sorted({e['device'] for e in picks}), max(abs(x) for x in errs)))
    strong = [e for e in picks if e['snr_db'] > 25]
    ok = [demod_bits(iq, e, fs, c, 72) == hex_bits(e['air_bits'], 72) for e in strong]
    check(len(strong) >= 10 and sum(ok) == len(ok),
          '%s: the 72 access-code bits demodulated from the samples are the burst\'s, every burst above 25 dB (%d of %d)' % (
              label, sum(ok), len(ok)))


def check_truth_is_samples(side, jobs, label, n=60):
    """Exact: each burst rebuilt from its sidecar keys alone (air_bits, start_sample, timing_frac, channel, burst_phase,
    snr_db) is the renderer's samples for it, so the truth describes the file completely."""
    worst, k = 0.0, 0
    pairs_ = list(zip(side['bursts'], jobs))
    pick = pairs_[::max(1, len(jobs) // n)] + [p for p in pairs_ if p[0]['device'] >= ms.N_PICONETS][::7]
    for e, job in pick:
        bits = hex_bits(e['air_bits'], e['air_bits_length'])
        burst, lead = br.gfsk(np.asarray(bits), ms.FS, h=br.GFSK_H, delay=e['timing_frac'])
        lo = e['start_sample'] - lead
        idx = np.arange(lo, lo + len(burst))
        cyc = (e['channel_mhz'] - side['center_mhz']) * 1e6 / ms.FS * idx
        mine = itf.amplitude_for(e['snr_db']) * burst * np.exp(1j * e['burst_phase']) * np.exp(2j * np.pi * (cyc - np.floor(cyc)))
        lo2, theirs = mx.pairs.render_burst(job, ms.FS, side['center_mhz'])
        worst = max(worst, float(np.abs(mine - theirs).max())) if lo2 == lo and len(theirs) == len(mine) else math.inf
        k += 1
    check(worst < 1e-5, '%s: %d bursts rebuilt from their sidecar keys alone equal the rendered samples (worst %.1e)' % (label, k, worst))


def test_samples():
    print('== samples: 40 MS/s, 20 MS/s, the interferer (0.31 s, level 40 dB)', flush=True)
    seed = ms.FILES['setn_a']['seed']
    side, jobs, total = ms.plan_setn(seed, blocks=3, level_range=HIGH, name='t')
    iq = np.concatenate(list(mx.render_blocks(jobs, total, seed)))
    quiet_iq = np.concatenate(list(mx.render_blocks(jobs, total, seed, noise=False)))
    noise = np.concatenate([itf.noise_block(seed, i, ms.BLOCK_SAMPLES, ms.FS) for i in range(3)])
    check(float(np.abs(iq - noise - quiet_iq).max()) < 1e-4, '40 MS/s: the file less the noise of its seed is the bursts alone')
    check_truth_is_samples(side, jobs, '40 MS/s')
    check_samples(iq, side, '40 MS/s')
    # 20 MS/s
    side20, jobs20, total20 = ms.plan_setn(ms.FILES['setn_c']['seed'], blocks=3, level_range=HIGH, band=ms.BAND_20, name='t20')
    y = np.concatenate(list(ms.decimate_blocks(mx.render_blocks(jobs20, total20, ms.FILES['setn_c']['seed']), total20)))
    h20 = ms.halve_truth(side20)
    check(len(y) == h20['n_samples'] == total20 // 2 and h20['sample_rate'] == 20e6, '20 MS/s: %d samples, half the plan\'s' % len(y))
    # exact: the 20 MS/s file is the 40 MS/s file through the FIR at the even samples (an independent numpy convolution)
    x40 = np.concatenate(list(mx.render_blocks(jobs20, total20, ms.FILES['setn_c']['seed'])))
    ref = np.convolve(x40, ms.decim_taps(), 'same')[::2]
    check(float(np.abs(ref - y).max()) < 1e-5,
          '20 MS/s: the file is the 40 MS/s render through the decimation FIR, at input sample 2n (numpy convolve; worst %.1e)'
          % float(np.abs(ref - y).max()))
    del x40, ref
    # exact up to one phase: an isolated burst rebuilt at 20 MS/s from the HALVED truth is the decimated render of it alone
    h = ms.decim_taps()
    worst, k = 0.0, 0
    trip = list(zip(side20['bursts'], h20['bursts'], jobs20))
    for e40, e20, job in trip[::max(1, len(trip) // 60)]:
        if abs(e20['channel_mhz'] - h20['center_mhz']) > 6:
            continue
        lo, s40 = mx.pairs.render_burst(job, ms.FS, side20['center_mhz'])
        pad = len(h)
        full = np.zeros(len(s40) + 2 * pad, np.complex128)
        full[pad:pad + len(s40)] = s40
        dec = np.convolve(full, h, 'same')
        base = lo - pad
        first = base + (base % 2)                       # the first even absolute index
        theirs = dec[first - base::2]
        idx20 = np.arange(first // 2, first // 2 + len(theirs))
        bits = hex_bits(e20['air_bits'], e20['air_bits_length'])
        b20, lead20 = br.gfsk(np.asarray(bits), 20e6, h=br.GFSK_H, delay=e20['timing_frac'])
        lo20 = e20['start_sample'] - lead20
        cyc = (e20['channel_mhz'] - h20['center_mhz']) * 1e6 / 20e6 * np.arange(lo20, lo20 + len(b20))
        mine_full = np.zeros(len(theirs), np.complex128)
        a = lo20 - idx20[0]
        if a < 0 or a + len(b20) > len(theirs):
            continue
        mine_full[a:a + len(b20)] = itf.amplitude_for(e20['snr_db']) * b20 * np.exp(2j * np.pi * (cyc - np.floor(cyc)))
        g = np.vdot(mine_full, theirs) / np.vdot(mine_full, mine_full)
        worst = max(worst, float(np.linalg.norm(g * mine_full - theirs) / np.linalg.norm(theirs)))
        k += 1
    check(k >= 20 and worst < 0.02,
          '20 MS/s: %d isolated bursts rebuilt at 20 MS/s from the halved truth equal their decimated render to %.2f %% RMS '
          '(one constant phase fitted)' % (k, 100 * worst))
    check_samples(y, h20, '20 MS/s')
    check(all(e['channel'] in ms.BAND_20 for e in h20['bursts']) and
          all(e['symbol_phase'] == int(e['start_sample'] % 20) for e in h20['bursts']),
          '20 MS/s: every rendered burst is on 31-47; symbol_phase = int(start_sample % 20)')
    # alignment and passband of the decimator on a tone
    t = np.exp(2j * np.pi * 7.3e6 / ms.FS * np.arange(1 << 16)).astype(np.complex64)
    yt = np.concatenate(list(ms.decimate_blocks([t[:30000], t[30000:]], len(t))))
    err = float(np.abs(yt[200:-200] - t[::2][200:-200]).max())
    check(err < 2e-3, 'decimator: a 7.3 MHz tone comes out as input sample 2n (worst error %.1e, across a block boundary)' % err)
    # noise in the passband
    lead = y[200:12000]
    f = np.fft.fftfreq(len(lead), 1 / 20e6)
    p = np.abs(np.fft.fft(lead)) ** 2 / len(lead) / 20e6 * 1e6      # power per MHz
    inband = float(np.mean(p[np.abs(f) < 7e6]))
    check(abs(10 * math.log10(inband / side20['noise_1mhz'])) < 0.3,
          '20 MS/s: the noise-only lead-in has %.2f dB of noise_1mhz per MHz in +-7 MHz' % (10 * math.log10(inband / side20['noise_1mhz'])))
    # the interferer
    iseed = seed + ms.INTERF_SEED_OFFSET
    d = np.concatenate(list(ms.add_interferer(mx.render_blocks(jobs, total, seed), total, iseed)))
    frames = itf.frame_schedule(iseed, total, ms.FS)
    gate = itf.gate_block(frames, 0, total)
    off = gate == 0
    check(off.any() and (~off).any() and np.array_equal(d[off], iq[off]),
          'interferer: where no frame is on, setn_d equals setn_a sample for sample (%.0f %% of the samples)' % (100 * off.mean()))
    on = np.flatnonzero(gate == 1.0)
    seg = (d - iq)[on[:65536]]
    fq = np.fft.fftfreq(len(seg), 1 / ms.FS)
    ps = np.abs(np.fft.fft(seg)) ** 2 / len(seg) / ms.FS * 1e6
    lvl = 10 * math.log10(np.mean(ps[(fq > 3e6) & (fq < 10e6)]) / side['noise_1mhz'])
    out = 10 * math.log10(np.mean(ps[(fq < -3e6) & (fq > -10e6)]) / side['noise_1mhz'])
    check(abs(lvl - ms.INTERF_LEVEL_DB) < 1.0 and out < -30,
          'interferer: %.1f dB over the floor per MHz in +3..+10 MHz while on, %.1f dB in -10..-3 MHz' % (lvl, out))
    sd = ms.interferer_truth(json.loads(json.dumps(side)), iseed)
    low = [e for e in sd['bursts'] if e['channel_mhz'] - ms.CENTER_MHZ <= 1.0]
    high = [e for e in sd['bursts'] if 3.0 <= e['channel_mhz'] - ms.CENTER_MHZ <= 10.0]
    hit_ok = all(e['interferer_overlap'] == itf.frames_hit(frames, int(e['start_sample']), int(e['start_sample'] + e['air_bits_length'] * 40) + 1)
                 for e in high)
    check(not any(e['interferer_overlap'] for e in low) and hit_ok and any(e['interferer_overlap'] for e in high),
          'interferer truth: no burst at or below +1 MHz is overlapped; a burst on +3..+10 MHz is overlapped iff a frame '
          'is on during it (%d of %d)' % (sum(e['interferer_overlap'] for e in high), len(high)))


def test_manifest():
    print('== manifest and checksums', flush=True)
    with tempfile.TemporaryDirectory() as td:
        names = ['setn_a', 'setn_c']
        with quiet():
            for n_ in names:
                ms.write_file(n_, td, blocks=3)
        ms.write_manifest(td, names, ['cmd'])
        man = json.load(open(os.path.join(td, 'manifest.json')))
        sums = dict(reversed(l.split('  ', 1)) for l in open(os.path.join(td, 'SHA256SUMS.txt')).read().split('\n') if l)
        rows = {r['name']: r for r in man['files']}
        ok = set(rows) == set(names) and len(sums) == 4
        for r in rows.values():
            ok = ok and sums[r['file']] == hashlib.sha256(open(os.path.join(td, r['file']), 'rb').read()).hexdigest() == r['sha256_cf32'] \
                and r['bytes'] == 8 * r['n_samples']
        check(ok and rows['setn_c']['sample_rate'] == 20e6 and rows['setn_a']['non_afh_device'] is not None,
              'manifest.json and SHA256SUMS.txt (sha256, sizes, rate, the non-AFH device and its bursts, opportunities)')


def main():
    t0 = time.time()
    for fn in (test_plans, test_samples, test_manifest):
        fn()
    failed = [w for ok, w in RESULTS if not ok]
    print('\n%d checks, %.0f s' % (len(RESULTS), time.time() - t0))
    for w in failed:
        print('FAILED:', w)
    print('RESULT: PASS' if not failed else 'RESULT: FAIL (%d)' % len(failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
