#!/usr/bin/env python3
"""Bursts whose errors come in clusters: Rayleigh and Rician fading, and partial narrowband interference.

    python scripts/bt_synth_fade.py fade_ray_fd300_snr18 --kind rayleigh --fd 300 --snr 18 --out DIR
    python scripts/bt_synth_fade.py --set fade [--out DIR]

    from scripts import bt_synth_fade
    iq, sidecar = bt_synth_fade.synthesise_fade('rayleigh', snr_db=18.0, fd_hz=300.0, bursts=24)

For bluey-ox-walker's CRC-assisted bit correction (its batch 2, item 2). The
correction, which flips the 1 to 3 bits the CRC syndrome and the FHS sync
parity point at, works on white-noise files, where what is left after FEC 2/3
is a few independent bit errors. A real error is not independent: a fade or a
short collision wipes a run of symbols. These files have errors of that shape,
with the truth of where the damage is, so that it can see where the correction
stops working. Nothing is transmitted. The plan is in
[knowledge/bluey-test-signals.md](../knowledge/bluey-test-signals.md).

**What a file is.** The files of ``bt_synth_acl.py`` again - hopping on the
map 31-50 at 40 MS/s on 2441.0 MHz, master LAP ``0x112233`` UAP ``0x55``, one
constant noise floor, symbol phases 0 and 20 alternating, a random timing
fraction - but with four packet types: **204 bursts, 51 DM1 (each an LMP PDU, the
eleven kinds in a seeded cycle), 51 DM3 and 51 DM5 (LLID 2 data) and 51 FHS** (a
stand-alone page-response FHS as ``bt_synth_page.py`` builds it: the X-input
whitening, the paged device's UAP in the HEC and CRC, a fresh random ``clke``
and so a fresh X in each; no exchange around it), in a seeded random order.
The schedule is acl's: a burst on a master slot, ``slots + 1 + 2 * idle``
slots to the next, idle 0, 1 or 2. **Every file has the same bursts, channels,
payloads, clocks, timing fractions and noise, one seed (9101)**: a file is the
reference file with a distortion added to the signal, so every comparison is
paired. The seed names no file: every draw is from a generator of (seed, what,
burst number), never of the file's own parameters, so nothing about a burst
changes from file to file but its distortion.

**The FHS's random fields.** Paged device (the access code, and the UAP that
seeds the HEC and CRC): a random LAP outside ``0x9E8B00-0x9E8B3F`` and a random
UAP; the Central's own address in the payload is the file's master
(``0x112233``, ``0x55``, NAP ``0x1234``); a random 22-bit class-of-device with
format type 0, a random AM_ADDR 1-7, SR 0-2, train A or B, a random ``clke``.
CLK27-2 in the payload is the master's native clock at the burst (CLK1-0 = 0:
every burst is on a master slot); the channel is the adapted hop of that
clock, as for the other bursts, **not** the page-response sequence the text
mandates (``knowledge/bluey-test-signals.md`` has the departure). ``bursts[].lap`` and
``uap`` of an FHS are the paged device's (access code, HEC and CRC), those
of the master are in ``fhs.lap`` and ``fhs.uap``; ``clk`` is the master's
native clock and the whitening pseudo-clock is ``fhs.tx_clk``.

**The distortions** (``kind``). All multiply or add to the **signal** only: the
noise is the reference's, sample for sample.

* ``ref``: none. ``fade_ref_snr18`` and ``_snr24``.
* ``rayleigh`` / ``rician``: a flat fading gain ``h(t)`` multiplying the burst's
  complex baseband signal, ``E|h|^2 = 1`` (so the mean SNR is the file's
  stated one: the SNR of the *realisation* of a burst is ``snr_eff_db``). A
  sum of 32 sinusoids (the randomised Jakes model: ``sqrt(1/32) * sum
  exp(j(2 pi fd cos(a_n) t + p_n))``, ``a_n = (2 pi n + theta) / 32`` and the
  phases ``p_n`` and ``theta`` drawn per burst from ``default_rng([seed, 7,
  k])``, so a hop is a new channel and the bursts' processes are independent, and
  the Rician line-of-sight of K = 6 dB, ``sqrt(K / (K + 1))``, has a random
  phase and an arrival angle of its own). The same ``(seed, k)`` is used by every
  fading file, so the files differ by ``fd`` (the time axis) and by the line of
  sight only. ``h`` is a function of ``t = (n - start_sample) / fs``, sample-exact
  and continuous across the burst (the ramp samples included). **It models a flat
  Rayleigh or Rician gain; it is not a measured channel.**
* ``nb``: no fading; in a seeded half of the bursts (102 of 204) a **narrowband
  interferer**: constant envelope, FM with a +-50 kHz deviation and a 1 kHz
  sine modulation (``exp(j(2 pi f t + 50 sin(2 pi 1 kHz t + psi)))``), at a
  random offset in +-300 kHz of the burst's channel centre, on for a **random
  contiguous 10 % to 60 % of the burst's symbols** at a random place, the gate
  a raised cosine of 2 us whose half amplitude is on the symbol boundary (so the
  ramps are one microsecond inside and one outside it), at a signal-to-interferer
  ratio of 0, 6 or 12 dB (burst power ``A^2`` over the interferer's power on the
  plateau). **The interferer is invented**, not a measured one, and its width
  is that of the stated deviation - Carson's rule gives 102 kHz, not the 200 kHz
  of the request; the deviation and the modulation were taken as the
  specification. Hit, interval, offset and modulation phase are drawn from
  ``default_rng([seed, 11])`` and ``[seed, 13, k]``, never from the level, so the
  three levels share them. A hit set of 102 cannot hold equal numbers of four
  types: two seeded types have 26 hits and two 25 (``nb_hit_counts_by_type``).

**Truth per burst**, beside the keys of ``bt_synth_acl.py``: for a fade,
``fade_kind`` ('rayleigh', 'rician', null), ``fade_fd_hz``, ``fade_k_db``,
``fade_gain_db`` (per air-bit symbol, access code included: 20 log10 |h| at the
sample ``start_sample + i * sps + sps // 2``, rounded to 0.01 dB, the same ``h``
that multiplied the samples), ``fade_min_db``, ``fade_frac_below_10db`` and
``fade_longest_run_below_10db`` (symbols with a rounded gain under -10 dB), and
``snr_eff_db`` (``snr_db + 10 log10`` of the mean of ``|h|^2`` over those
symbol-centre samples); for the interferer ``nb_hit``, ``nb_start_symbol`` and
``nb_end_symbol`` (symbols from the burst's first, ``[start, end)``),
``nb_offset_khz``, ``nb_sir_db``, ``nb_in_band``. The keys that do not apply are
null. ``air_bits`` are in every sidecar (``air_bits_omitted`` false), as in the acl files.
"""
import argparse
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from apps import bt_fhs, bt_lmp  # noqa: E402
from scripts import bt_synth, bt_synth_hop as hop  # noqa: E402
from scripts.bt_synth_interf import NOISE_1MHZ, amplitude_for, noise_block  # noqa: E402
from scripts.bt_synth_acl import (AIR_BITS_NOTE, CLOCK_LOCK_NOTE, L2CAP_CID, L2CAP_MIN, LMP_NAMES,  # noqa: E402
                                  LMP_TABLE, bits_hex)

LAP = 0x112233
UAP = 0x55
NAP = 0x1234
LT_ADDR = 1
AM_ADDR_RANGE = (1, 8)
CENTER_MHZ = 2441.0
MAP = list(range(31, 51))                  # 20 channels, 2433 to 2452 MHz
CLK0 = 0x0123400
FS = 40e6
SEED = 9101
BURSTS = 204
TYPES = ('DM1', 'DM3', 'DM5', 'FHS')
SLOTS = {'DM1': 1, 'DM3': 3, 'DM5': 5, 'FHS': 1}
LONGEST = {'DM1': 17, 'DM3': 121, 'DM5': 224}
RESERVED_LAPS = range(0x9E8B00, 0x9E8B40)
BLOCK_SAMPLES = 2 ** 22

#: Oscillators of the fading sum.
FADE_OSC = 32
#: A symbol's gain under this many dB (rounded) counts as deep in the truth.
DEEP_DB = -10.0

#: The narrowband interferer: peak deviation and modulating frequency (Hz), the offset range (kHz) in
#: which its centre is drawn, the share of a burst's symbols it covers, the edge in us, the SIRs.
NB_DEVIATION_HZ = 50e3
NB_MOD_HZ = 1e3
NB_OFFSET_KHZ = 300.0
NB_SHARE = (0.10, 0.60)
NB_EDGE_US = 2.0
NB_SIRS_DB = (0.0, 6.0, 12.0)

#: The thirteen files of ``--set fade``: (name, kind, snr, fd, K, SIR).
FADE_FILES = (
    [('fade_ref_snr%d' % s, 'ref', float(s), None, None, None) for s in (18, 24)]
    + [('fade_ray_fd%d_snr%d' % (fd, s), 'rayleigh', float(s), float(fd), None, None)
       for fd in (100, 300, 800) for s in (18, 24)]
    + [('fade_rice6_fd300_snr%d' % s, 'rician', float(s), 300.0, 6.0, None) for s in (18, 24)]
    + [('fade_nb_sir%d_snr24' % sir, 'nb', 24.0, None, None, float(sir)) for sir in (0, 6, 12)])

DISTORTION = {
    'ref': 'none: the reference file, white noise only',
    'rayleigh': 'flat Rayleigh fading gain h(t) on the signal, E|h|^2 = 1, 32-sinusoid randomised Jakes model, '
                'an independent process per burst',
    'rician': 'flat Rician fading gain h(t) on the signal, E|h|^2 = 1, K = %g dB line of sight on the same '
              '32-sinusoid diffuse part, an independent process per burst',
    'nb': 'no fading; in 102 of 204 bursts a constant-envelope FM interferer (+-50 kHz deviation, 1 kHz sine '
          'modulation) on 10-60 %% of the burst\'s symbols, 2 us raised-cosine edges, at SIR %g dB',
}

NOTES = [
    'The fade is a flat Rayleigh or Rician gain on the signal (a model), NOT a measured channel: no delay spread, no '
    'frequency selectivity, no shadowing; the noise is not faded.',
    'The narrowband interferer is an invented one (constant-envelope FM, +-50 kHz deviation, 1 kHz modulation, about '
    '102 kHz wide by Carson\'s rule, not the 200 kHz of the request), not a measured emission.',
    'E|h|^2 = 1 is the ensemble mean: one burst, or one file of 204, has a mean |h|^2 that scatters round 1 (at fd = 100 Hz '
    'a burst is short against the coherence time); snr_eff_db is the realisation\'s.',
    'The FHS bursts carry the master\'s clock and sit on the adapted hop of that clock, not on the page-response '
    'sequence of the specification; see the page files for the departure.',
]


def describe(kind, k_db, sir_db):
    """The sidecar's one-line description of a distortion."""
    if kind == 'rician':
        return DISTORTION[kind] % k_db
    if kind == 'nb':
        return DISTORTION[kind] % sir_db
    return DISTORTION[kind]


def level_name(snr_db):
    return 'snr%d' % round(snr_db)


# --- the fading gain ---------------------------------------------------------------------

def fade_h(seed, k, fd_hz, t, k_db=None):
    """The fading gain of burst ``k`` at the times ``t`` (seconds, an array), a complex array with ``E|h|^2 = 1``.

    32 sinusoids of equal amplitude: angles ``(2 pi n + theta) / 32`` and phases drawn from
    ``default_rng([seed, 7, k])``; with ``k_db`` the line of sight (power share ``K / (K + 1)``, an arrival angle and a phase
    of its own) is added to the diffuse part's remaining ``1 / (K + 1)``. The draws are the same whatever ``fd_hz`` and
    ``k_db``, so the files share them."""
    rng = np.random.default_rng([seed, 7, k])
    theta = rng.uniform(0, 2 * np.pi)
    phase = rng.uniform(0, 2 * np.pi, FADE_OSC)
    los_angle = rng.uniform(0, 2 * np.pi)
    los_phase = rng.uniform(0, 2 * np.pi)
    t = np.asarray(t, dtype=np.float64)
    w = 2 * np.pi * fd_hz
    scale = 1.0 / math.sqrt(FADE_OSC)
    diffuse = np.zeros(len(t), dtype=np.complex128)
    for n in range(FADE_OSC):
        angle = (2 * np.pi * n + theta) / FADE_OSC
        diffuse += np.exp(1j * (w * np.cos(angle) * t + phase[n]))
    diffuse *= scale
    if k_db is None:
        return diffuse
    kk = 10 ** (k_db / 10)
    los = np.exp(1j * (w * np.cos(los_angle) * t + los_phase))
    return math.sqrt(kk / (kk + 1)) * los + math.sqrt(1 / (kk + 1)) * diffuse


def gain_truth(gain_db):
    """From a burst's per-symbol gains (the rounded list): ``(min, fraction below -10, longest run below -10)``."""
    g = np.asarray(gain_db)
    deep = g < DEEP_DB
    run = best = 0
    for d in deep.tolist():
        run = run + 1 if d else 0
        best = max(best, run)
    return float(g.min()), float(deep.mean()), int(best)


# --- the plan -------------------------------------------------------------------------------

def plan_fade(bursts=BURSTS, seed=SEED, lap=LAP, uap=UAP, clk0=CLK0, fs=FS, center_mhz=CENTER_MHZ, channels=MAP):
    """Everything about the bursts but their level and distortion: a list of dicts, one a burst, and a dict of
    facts about the schedule. Each draw is from a generator of (seed, what, k) alone."""
    if bursts % len(TYPES):
        raise ValueError("%d bursts do not split equally over %s" % (bursts, ', '.join(TYPES)))
    if clk0 % 4:
        raise ValueError("clk0 must be a master slot's clock: CLK1-0 = 00")
    sps = int(round(fs / br.SYMBOL_RATE))
    channels = hop.map_channels(channels)
    for channel in channels:
        if hop.outside_window(channel, fs, center_mhz):
            raise ValueError("channel %d, %g MHz, is outside the window of %g MS/s on %g MHz"
                             % (channel, hop.channel_mhz(channel), fs / 1e6, center_mhz))
    instant = (clk0 & 0x0FFFFFFF) >> 1
    hop_fn = hop.afh_hop_fn([(instant, channels)], lap, uap)
    n = bursts // len(TYPES)
    types = [t for t in TYPES for _ in range(n)]
    np.random.default_rng([seed, 10]).shuffle(types)
    dm1_total = types.count('DM1')
    cycle = []
    order_rng = np.random.default_rng([seed, 5])
    while len(cycle) < dm1_total:
        cycle += [LMP_NAMES[i] for i in order_rng.permutation(len(LMP_NAMES))]
    cycle = cycle[:dm1_total]

    plan, slot, dm1_seen = [], 0, 0
    for k, ptype in enumerate(types):
        slots = SLOTS[ptype]
        clk = (clk0 + 2 * slot) & 0x0FFFFFFF
        t_rng = np.random.default_rng([seed, 2, k])
        idle = int(t_rng.integers(0, 3))
        timing_frac = float(t_rng.uniform(0, 1))
        burst_phase = float(t_rng.uniform(0, 2 * np.pi))
        p_rng = np.random.default_rng([seed, 4, k])
        entry = dict(k=k, ptype=ptype, clk=clk, channel=int(hop_fn(clk)), slot=slot, slots=slots,
                     phase=(0, sps // 2)[k % 2], timing_frac=timing_frac, burst_phase=burst_phase,
                     llid=None, body=None, lmp=None, fhs=None)
        if ptype == 'FHS':
            f_rng = np.random.default_rng([seed, 6, k])
            while True:
                paged_lap = int(f_rng.integers(1, 1 << 24))
                if paged_lap not in RESERVED_LAPS and paged_lap != lap:
                    break
            paged_uap = int(f_rng.integers(0, 256))
            cod = int(f_rng.integers(0, 1 << 22)) << 2
            am_addr = int(f_rng.integers(*AM_ADDR_RANGE))
            sr = int(f_rng.integers(0, 3))
            clke = int(f_rng.integers(0, 1 << 28))
            train, koffset = ('A', 24) if f_rng.integers(0, 2) else ('B', 8)
            x = bt_fhs.xprc(clke, koffset, 0, 1)
            entry['fhs'] = bt_fhs.page_response(paged_lap, paged_uap, lap, uap, NAP, cod, am_addr, clk, x, sr=sr)
            entry['fhs_meta'] = dict(clke_frozen=clke, train=train, koffset=koffset, knudge=0, N=1, xprc=x,
                                     xprc_other_reading=bt_fhs.xprc_other_reading(clke, koffset, 0, 1))
        else:
            longest = LONGEST[ptype]
            if ptype == 'DM1':
                lmp_name = cycle[dm1_seen]
                dm1_seen += 1
                body, params, tid = bt_lmp.random_pdu(p_rng, lmp_name, clk)
                entry.update(llid=0b11, lmp=dict(name=lmp_name, tid=tid, params=params))
            else:
                length = int(p_rng.integers(L2CAP_MIN, longest + 1))
                data = bytes(p_rng.integers(0, 256, length - 4, dtype=np.uint8))
                body = (length - 4).to_bytes(2, 'little') + L2CAP_CID.to_bytes(2, 'little') + data
                entry['llid'] = 0b10
            entry['body'] = body
        plan.append(entry)
        slot += slots + 1 + 2 * idle
    return plan, dict(afh_instant=instant, afh_map=channels, slots_total=slot)


def plan_hits(plan, seed=SEED):
    """The narrowband interferer's hit set and draws, from ``seed`` alone: the hit set is half the bursts, equal numbers of
    each type except for the remainder (seeded), and every burst has its interval, offset and phases drawn whether hit or
    not. Returns ``(hits, per_burst, counts)``: a list of booleans, a list of dicts and the hit count of each type."""
    types = sorted({b['ptype'] for b in plan})
    total = len(plan) // 2
    base, extra = divmod(total, len(types))
    rng = np.random.default_rng([seed, 11])
    bonus = set(rng.permutation(len(types))[:extra].tolist())
    counts = {t: base + (i in bonus) for i, t in enumerate(types)}
    hits = [False] * len(plan)
    for t in types:
        idx = [b['k'] for b in plan if b['ptype'] == t]
        for j in rng.permutation(len(idx))[:counts[t]]:
            hits[idx[int(j)]] = True
    per = []
    for b in plan:
        r = np.random.default_rng([seed, 13, b['k']])
        share = float(r.uniform(*NB_SHARE))
        per.append(dict(share=share, u_start=float(r.uniform(0, 1)), offset_khz=float(r.uniform(-NB_OFFSET_KHZ, NB_OFFSET_KHZ)),
                        mod_phase=float(r.uniform(0, 2 * np.pi)), carrier_phase=float(r.uniform(0, 2 * np.pi))))
    return hits, per, counts


def nb_interval(draw, nbits):
    """``(start_symbol, end_symbol)`` of an interferer, ``[start, end)``, from its draws and the burst's symbols."""
    length = max(1, int(round(draw['share'] * nbits)))
    start = int(draw['u_start'] * (nbits - length + 1))
    return start, start + length


def interferer(draw, sir_db, amp, start, frac, sps, nbits, interval, channel_mhz, center_mhz, fs):
    """The interferer's samples for one burst: ``(first_sample, complex128 samples)``, absolute sample numbers.

    ``A_i = amp * 10**(-sir/20)``, constant envelope, the gate a raised cosine whose half amplitude is on the symbol
    boundary ``a`` and ``b`` (samples after the burst's first bit, ``start + frac``), 2 us wide, exactly zero outside it."""
    s0, s1 = interval
    edge = NB_EDGE_US * 1e-6 * fs
    a, b = s0 * sps, s1 * sps
    n0 = start + int(math.floor(frac + a - edge / 2)) - 1
    n1 = start + int(math.ceil(frac + b + edge / 2)) + 2
    n = np.arange(n0, n1)
    x = n - start - frac
    rise = 0.5 * (1 - np.cos(np.pi * np.clip((x - (a - edge / 2)) / edge, 0, 1)))
    fall = 0.5 * (1 - np.cos(np.pi * np.clip(((b + edge / 2) - x) / edge, 0, 1)))
    f_hz = (channel_mhz + draw['offset_khz'] * 1e-3 - center_mhz) * 1e6
    cycles = f_hz / fs * n
    phase = 2 * np.pi * (cycles - np.floor(cycles)) + draw['carrier_phase']
    phase += (NB_DEVIATION_HZ / NB_MOD_HZ) * np.sin(2 * np.pi * NB_MOD_HZ / fs * n + draw['mod_phase'])
    amp_i = amp * 10 ** (-sir_db / 20)
    return n0, amp_i * rise * fall * np.exp(1j * phase)


# --- the file ---------------------------------------------------------------------------------

def synthesise_fade(kind='ref', snr_db=18.0, fd_hz=None, k_db=None, sir_db=None, bursts=BURSTS, seed=SEED,
                    lap=LAP, uap=UAP, clk0=CLK0, fs=FS, center_mhz=CENTER_MHZ, channels=MAP,
                    block_samples=BLOCK_SAMPLES, name=None, paired_with=None):
    """The samples and the sidecar of one file; ``kind`` is ``'ref'``, ``'rayleigh'``, ``'rician'`` or ``'nb'``."""
    if kind not in DISTORTION:
        raise ValueError("kind is one of %s, not %r" % (', '.join(DISTORTION), kind))
    if kind in ('rayleigh', 'rician') and not fd_hz:
        raise ValueError("a fading file needs fd_hz")
    if kind == 'rician' and k_db is None:
        raise ValueError("a Rician file needs k_db")
    if kind == 'nb' and sir_db is None:
        raise ValueError("an interference file needs sir_db")
    plan, info = plan_fade(bursts, seed, lap, uap, clk0, fs, center_mhz, channels)
    slot_samples = fs * br.SLOT_US * 1e-6
    if abs(slot_samples - round(slot_samples)) > 1e-6:
        raise ValueError("%g S/s does not divide a 625 us slot" % fs)
    slot_samples = int(round(slot_samples))
    sps = int(round(fs / br.SYMBOL_RATE))
    amp = amplitude_for(snr_db)
    grid = int(bt_synth.LEAD_SLOTS * slot_samples)
    grid -= grid % sps
    total = grid + (info['slots_total'] + 1) * slot_samples
    total = -(-total // block_samples) * block_samples
    iq = np.zeros(total, dtype=np.complex64)
    for index, lo in enumerate(range(0, total, block_samples)):
        hi = min(lo + block_samples, total)
        iq[lo:hi] += noise_block(seed, index, hi - lo, fs)
    hits, draws, hit_counts = plan_hits(plan, seed) if kind == 'nb' else (None, None, None)
    entries = []
    for b in plan:
        k, ptype, clk, channel = b['k'], b['ptype'], b['clk'], b['channel']
        if ptype == 'FHS':
            p = b['fhs']
        else:
            p = br.Packet(lap, uap, clk, ptype, b['body'], lt_addr=LT_ADDR, flow=1, arqn=1, seqn=k & 1,
                          llid=b['llid'], payload_flow=1)
        nbits = len(p.bits)
        start = grid + b['slot'] * slot_samples + b['phase']
        burst, lead = br.gfsk(p.bits, fs, h=br.GFSK_H, delay=b['timing_frac'])
        burst = burst * np.exp(1j * b['burst_phase'])
        lo = start - lead
        n = np.arange(lo, lo + len(burst))
        cycles = (hop.channel_mhz(channel) - center_mhz) * 1e6 / fs * n
        burst = burst * np.exp(2j * np.pi * (cycles - np.floor(cycles)))
        if lo + len(burst) > total:
            raise ValueError("burst %d ends at %d, past the end of the file at %d" % (k, lo + len(burst), total))
        fade, nb = {}, {}
        h = None
        if kind in ('rayleigh', 'rician'):
            h = fade_h(seed, k, fd_hz, (n - start) / fs, k_db if kind == 'rician' else None)
            centres = start - lo + np.arange(nbits) * sps + sps // 2
            gain_db = np.round(20 * np.log10(np.maximum(np.abs(h[centres]), 1e-12)), 2)
            gmin, frac_deep, run = gain_truth(gain_db)
            mean_p = float(np.mean(np.abs(h[centres]) ** 2))
            fade = dict(fade_kind=kind, fade_fd_hz=float(fd_hz), fade_k_db=None if kind == 'rayleigh' else float(k_db),
                        fade_gain_db=gain_db.tolist(), fade_min_db=gmin, fade_frac_below_10db=frac_deep,
                        fade_longest_run_below_10db=run, snr_eff_db=float(snr_db + 10 * np.log10(mean_p)))
        sig = (amp * burst * (1 if h is None else h)).astype(np.complex64)
        if kind == 'nb':
            hit = hits[k]
            nb = dict(nb_hit=bool(hit), nb_start_symbol=None, nb_end_symbol=None, nb_offset_khz=None, nb_sir_db=None,
                      nb_in_band=None)
            if hit:
                interval = nb_interval(draws[k], nbits)
                n0, itf = interferer(draws[k], sir_db, amp, start, b['timing_frac'], sps, nbits, interval,
                                     hop.channel_mhz(channel), center_mhz, fs)
                sig[n0 - lo:n0 - lo + len(itf)] += itf.astype(np.complex64)
                nb.update(nb_start_symbol=interval[0], nb_end_symbol=interval[1],
                          nb_offset_khz=draws[k]['offset_khz'], nb_sir_db=float(sir_db),
                          nb_in_band=bool(abs(draws[k]['offset_khz']) <= 500.0))
        iq[lo:lo + len(sig)] += sig
        payload_bits = br.bytes_bits(p.payload_full)
        entry = {'start_sample': int(start), 'timing_frac': b['timing_frac']}
        entry.update(p.sidecar())
        if ptype == 'FHS':
            side = p.sidecar()
            entry = {'start_sample': int(start), 'timing_frac': b['timing_frac'], 'ptype': 'FHS', 'clk': clk}
            for key in ('header18', 'payload_full_hex', 'payload_valid', 'lt_addr', 'flow', 'arqn', 'seqn'):
                entry[key] = side[key]
            entry.update(
                payload_hex=p.payload_full[:18].hex(), lap=p.access_lap, uap=p.header_uap, llid=None,
                payload_length=18, lmp_opcode=None, lmp_name=None, lmp_tid=None, lmp_params=None,
                fhs=dict(lap=p.lap, uap=p.uap, nap=p.nap, class_of_device=p.cod, am_addr=p.am_addr,
                         lt_addr_header=p.lt_addr, clk27_2=p.clk27_2, sr=p.sr, sp=p.sp, eir=p.eir,
                         header_uap=p.header_uap, tx_clk=p.tx_clk, access_lap=p.access_lap,
                         whitening_x=p.whitening_x, whitening_register=bt_fhs.whitening_register(p.whitening_x),
                         reserved=p.reserved, previously_used=p.previously_used,
                         implied_clk6_1=(p.tx_clk >> 1) & 0x3F, **b['fhs_meta']))
        else:
            entry.update(
                lap=lap, uap=uap, llid=b['llid'], payload_length=len(b['body']),
                lmp_opcode=None if b['lmp'] is None else bt_lmp.PDUS[b['lmp']['name']]['opcode'],
                lmp_name=None if b['lmp'] is None else b['lmp']['name'],
                lmp_tid=None if b['lmp'] is None else b['lmp']['tid'],
                lmp_params=None if b['lmp'] is None else {
                    key: (v.hex() if isinstance(v, bytes) else v) for key, v in b['lmp']['params'].items()},
                fhs=None)
        entry.update(
            air_bits=bits_hex(p.bits), air_bits_length=nbits,
            body_crc_bits_hex=bits_hex(payload_bits), body_crc_bits_length=len(payload_bits),
            slots=b['slots'], end_sample=int(start + nbits * sps), channel=channel,
            channel_mhz=hop.channel_mhz(channel), symbol_phase=int(start % sps), snr_db=float(snr_db),
            afh_map_index=0, burst_phase=b['burst_phase'])
        entry.update(fade_kind=None, fade_fd_hz=None, fade_k_db=None, fade_gain_db=None, fade_min_db=None,
                     fade_frac_below_10db=None, fade_longest_run_below_10db=None, snr_eff_db=float(snr_db),
                     nb_hit=None, nb_start_symbol=None, nb_end_symbol=None, nb_offset_khz=None, nb_sir_db=None,
                     nb_in_band=None)
        entry.update(fade)
        entry.update(nb)
        entries.append(entry)

    counts = {t: sum(e['ptype'] == t for e in entries) for t in sorted({e['ptype'] for e in entries})}
    lmp_counts = {name_: sum(e['lmp_name'] == name_ for e in entries) for name_ in LMP_NAMES}
    sidecar = {
        'generator': 'SDR scripts/bt_synth_fade.py',
        'generator_commit': bt_synth.commit(),
        'name': name,
        'lap': lap,
        'uap': uap,
        'nap': NAP,
        'master': {'lap': lap, 'uap': uap, 'nap': NAP, 'lt_addr': LT_ADDR},
        'clk': plan[0]['clk'],
        'clk_convention': 'native CLK[27:0], at the first sample of the burst it is given for; top-level clk is '
                          'burst 0\'s; for an FHS the master\'s native clock too (the whitening pseudo-clock is '
                          'bursts[].fhs.tx_clk)',
        'link': 'ACL, master packets only; FHS bursts stand alone (no exchange around them)',
        'hopping': True,
        'hop_channels': sorted({b['channel'] for b in plan}),
        'channel_mhz': None,
        'bt_channel': None,
        'sample_rate': fs,
        'center_mhz': center_mhz,
        'cfo_hz': 0.0,
        'n_samples': int(total),
        'n_samples_meaning': 'the number of complex samples in the file; its size is 8 * n_samples bytes',
        'timing_frac': None,
        'timing_frac_meaning': 'null at the top: every burst has its own, uniform in [0, 1)',
        'snr_db': float(snr_db),
        'snr_meaning': 'the mean SNR of a burst over the noise in 1 MHz; with fading the realisation\'s is '
                       'bursts[].snr_eff_db',
        'snr_bw_hz': 1e6,
        'noise_1mhz': NOISE_1MHZ,
        'modulation': {'scheme': 'GFSK', 'bt': br.GFSK_BT, 'h': br.GFSK_H, 'symbol_rate': br.SYMBOL_RATE},
        'slot_samples': slot_samples,
        'start_offset': 0,
        'symbol_phase': None,
        'symbol_phases': [0, sps // 2],
        'symbol_phase_meaning': 'null at the top: alternating per burst, even bursts at phase 0, odd at sps/2; '
                                'each burst has its own',
        'per_burst_keys': sorted(entries[0]),
        'start_sample_meaning': 'the first sample of the access code\'s preamble, before timing_frac is added',
        'air_bits_omitted': False,
        'air_bits_note': AIR_BITS_NOTE,
        'type_counts': counts,
        'equal_counts': len(set(counts.values())) == 1,
        'equal_counts_statement': 'every packet type has the same number of bursts, %d each (%s)'
                                  % (bursts // len(TYPES), ', '.join(TYPES)),
        'lmp_pdus': LMP_TABLE,
        'lmp_counts': lmp_counts,
        'lmp_carrier_note': 'LMP PDUs are in the DM1 only (Core Vol 2 Part C Table 5.1); DM3 and DM5 carry LLID 2 data; '
                            'FHS bursts carry the page-response FHS payload',
        'fhs_note': 'a page-response FHS as scripts/bt_synth_page.py builds it, stand-alone; bursts[].lap and uap are '
                    'the paged device\'s (the access code and the seed of HEC and CRC); the master\'s own address, the '
                    'clke and X that whitened it are in bursts[].fhs; the channel is the adapted hop of the master\'s '
                    'clock, not the page-response sequence of the specification',
        'afh_map': info['afh_map'],
        'afh_instant': info['afh_instant'],
        'afh_map_count': 1,
        'afh_maps': [{'instant': info['afh_instant'], 'first_burst': 0, 'channels': info['afh_map']}],
        'afh_instant_meaning': hop.INSTANT_MEANING,
        'hop_kernel': hop.HOP_KERNEL,
        'address_for_hop': uap << 24 | lap,
        'clock_lock_note': CLOCK_LOCK_NOTE,
        'multi_slot_channel_note': 'a multi-slot packet is on the channel of its first slot for its whole length',
        'schedule_note': 'a burst starts on a master slot; the next starts slots + 1 + 2 * idle slots later, idle in '
                         '{0, 1, 2} uniform and seeded (default_rng([seed, 2, k])); the clock advances two ticks a slot',
        'seed': seed,
        'noise_block_samples': block_samples,
        'distortion_kind': kind,
        'distortion': describe(kind, k_db, sir_db),
        'paired_with': paired_with,
        'paired_note': 'every file of the set has the same bursts, channels, payloads, clocks, timing fractions, '
                       'carrier phases and noise (seed %d); file - noise = A * burst * h(t) for a fading file, and for '
                       'an interference file file - reference = the interferer, exactly zero off its intervals; '
                       'the noise block i is default_rng([seed, 1, i]) of the full noise_block_samples length, real rail then imaginary rail from the same generator, so regenerating it must span the file\'s true n_samples in full blocks and then cut (a short last block gives the wrong imaginary rail), A = sqrt(noise_1mhz * 10**(snr_db / 10))' % seed,
        'fade': None if kind not in ('rayleigh', 'rician') else {
            'model': 'randomised Jakes: sqrt(1/32) * sum_n exp(j(2 pi fd cos((2 pi n + theta) / 32) t + p_n)); '
                     'Rician adds sqrt(K/(K+1)) of a line of sight and scales the diffuse part by sqrt(1/(K+1))',
            'oscillators': FADE_OSC, 'fd_hz': float(fd_hz), 'k_db': None if kind == 'rayleigh' else float(k_db),
            'mean_power': 1.0, 'time_axis': 't = (n - start_sample) / sample_rate',
            'rng': 'default_rng([seed, 7, burst number])',
            'gain_sample': 'fade_gain_db[i] is 20 log10 |h| at sample start_sample + i * sps + sps // 2, rounded to 0.01 dB',
            'deep_threshold_db': DEEP_DB},
        'interferer': None if kind != 'nb' else {
            'model': 'constant-envelope FM: A_i exp(j(2 pi f t + (dev / fm) sin(2 pi fm t + psi))) times a raised-cosine gate',
            'deviation_hz': NB_DEVIATION_HZ, 'modulation_hz': NB_MOD_HZ, 'carson_width_hz': 2 * (NB_DEVIATION_HZ + NB_MOD_HZ),
            'offset_range_khz': [-NB_OFFSET_KHZ, NB_OFFSET_KHZ], 'symbol_share': list(NB_SHARE),
            'edge_us': NB_EDGE_US, 'sir_db': float(sir_db),
            'sir_meaning': 'burst power A^2 over the interferer\'s power on the plateau, A_i = A * 10**(-sir / 20)',
            'gate': 'raised cosine 2 us wide, half amplitude on the symbol boundaries nb_start_symbol and nb_end_symbol '
                    '(start_sample + timing_frac + symbol * sps), exactly zero outside it',
            'rng': 'default_rng([seed, 11]) for the hit set and [seed, 13, burst number] for the rest, never the level',
            'hits': int(sum(hits)), 'nb_hit_counts_by_type': hit_counts,
            'hit_count_note': '102 of 204 cannot be split equally over four types: two types (seeded) have 26 hits '
                              'and two 25',
            'in_band_note': 'nb_in_band (|offset| <= 500 kHz) is true for every hit, the offset being within 300 kHz'},
        'notes': NOTES,
    }
    sidecar['bursts'] = entries
    return iq, sidecar


# --- the files ------------------------------------------------------------------------------

def set_files():
    """What ``--set fade`` writes: ``(name, kwargs)``."""
    runs = []
    for name, kind, snr, fd, k_db, sir in FADE_FILES:
        paired = None if kind == 'ref' else 'fade_ref_%s' % level_name(snr)
        runs.append((name, dict(kind=kind, snr_db=snr, fd_hz=fd, k_db=k_db, sir_db=sir, paired_with=paired)))
    return runs


SETS = {'fade': set_files}


def write_file(name, out_dir, **spec):
    """Make one file and write ``synth_<name>.cf32`` and ``.json`` into ``out_dir``."""
    os.makedirs(out_dir, exist_ok=True)
    iq_path = os.path.join(out_dir, 'synth_%s.cf32' % name)
    side_path = os.path.join(out_dir, 'synth_%s.json' % name)
    iq, sidecar = synthesise_fade(name=name, **spec)
    iq.astype('<c8', copy=False).tofile(iq_path)
    print('%s  %d samples, %.2f s, %s' % (iq_path, len(iq), len(iq) / sidecar['sample_rate'],
                                          ', '.join('%d %s' % (v, k) for k, v in sidecar['type_counts'].items())),
          flush=True)
    with open(side_path, 'w') as f:
        json.dump(sidecar, f, indent=1)
    print(side_path)
    return iq_path, side_path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('name', nargs='?', help='the file is synth_<name>.cf32; not used with --set')
    ap.add_argument('--set', help='write the whole set, everything from its table and no option but --out; one of %s'
                    % ', '.join(sorted(SETS)))
    ap.add_argument('--kind', choices=tuple(DISTORTION), help='default ref')
    ap.add_argument('--snr', type=float, help='dB over the noise in 1 MHz; default 18')
    ap.add_argument('--fd', type=float, help='maximum Doppler in Hz of a fading file')
    ap.add_argument('--k', type=float, help='the Rician K in dB')
    ap.add_argument('--sir', type=float, help='the narrowband interferer\'s signal-to-interferer ratio in dB')
    ap.add_argument('--bursts', type=int, help='default %d' % BURSTS)
    ap.add_argument('--seed', type=int, help='default %d' % SEED)
    ap.add_argument('--out', default='/media/user/4TB/sdr-synth-tmp', help='the folder of the files')
    args = ap.parse_args(argv)
    per_file = ['kind', 'snr', 'fd', 'k', 'sir', 'bursts', 'seed']
    if args.set:
        if args.set not in SETS:
            ap.error('no set %r: the sets are %s' % (args.set, ', '.join(sorted(SETS))))
        stray = [o for o in per_file if getattr(args, o) is not None]
        if args.name is not None or stray:
            ap.error('--set takes everything from its table: no name and no option but --out')
        runs = SETS[args.set]()
    else:
        if args.name is None:
            ap.error('give the name of the file, or --set')
        spec = dict(kind=args.kind or 'ref', snr_db=18.0 if args.snr is None else args.snr, fd_hz=args.fd,
                    k_db=args.k, sir_db=args.sir, bursts=args.bursts or BURSTS,
                    seed=SEED if args.seed is None else args.seed)
        runs = [(args.name, spec)]
    try:
        for name, spec in runs:
            write_file(name, args.out, **spec)
    except ValueError as e:
        ap.error(str(e))


if __name__ == '__main__':
    main()
