#!/usr/bin/env python3
"""Stage 3's receive side: grade one hopping shot in a BB60D recording.

    python scripts/bt_ota_hop_check.py synth_ota_hop_dh5_tx.json --record 10 \\
        --capture ota_hop_dh5_0dbm_<stamp>
    python scripts/bt_ota_hop_check.py synth_ota_hop_dh5_tx.json --iq rec.cf32 \\
        [--capture NAME]

``scripts/bt_tx_hop.py`` plays the train once. The recording starts before
that and ends after it, so the shot sits at an offset nobody knows, in
noise. This finds it from the transmitter's sidecar and grades every burst
against that truth. A burst the demodulator misses stays in the truth,
placed by the clock fitted to the ones it did find, with ``found`` false and
a ``reason``. The sidecar must be the transmitter's own record of a shot
that was sent: ``tx.dry_run`` true, or ``tx.sent`` not true, is refused.

**Finding the shot.** The power in each of the shot's channels (the FFT bins
within ±0.35 MHz of the carrier, per block of 1000 samples) is compared with
*that channel's own* median, so an access point that is always on spoils its
own channels and no others. A block that is hot on several channels at once
is a Wi-Fi frame, not a Bluetooth burst, and is dropped. What is left, a
run about a burst long on one channel, votes for the shot's origin once for
every burst the transmitter put on that channel; the origin with the most
votes wins. A burst that is silent, a weak burst 0, a frame before the shot
and a recording that starts or ends inside the shot all leave the vote
standing. The access code, correlated on a burst's own channel, then places
a burst to a fraction of a sample, tracking outward from the best-supported
burst in both directions.

**Not writing a wrong truth.** A truth is written only when every one of
these holds, and each of them is a ``problem`` with no sidecar otherwise:
the recorder lost no samples (``record`` counts them: the BB60D returns
nothing on an overflow, so everything after it moves earlier and the file
keeps its length); one line fits the bursts that were read, and the same
line fits each half of them, which means the two halves meet at the join
(``MAX_STEP_SAMPLES``: a loss or a gain of samples) and have the same slope
(``MAX_SLOPE_STEP_PPM``: a change of rate with the clock continuous), and
the scatter about it is under ``MAX_JITTER_SAMPLES``; no more than one burst
with good bits is off that line; the shot's energy does not run on past the
last burst that was found, or begin before the first; the line is known to a
sample at the farthest burst it places; the carrier is where the sidecar
says (``MAX_CFO_HZ``); and the clock is within 100 ppm. A burst is
``found`` only with at most 50 bit errors, a carrier within ``MAX_CFO_HZ``
of its channel and a place within 3 samples of the line; any other burst is
``found`` false with its ``reason`` and is placed by the clock, and only
found bursts count towards the per-channel figures and the flatness. The
clock itself, and the gates above, are steered by every burst read well
enough (``bit_limit``: 50 bit errors or 5 % of the burst, whichever is more),
so that a link at 12 dB, where almost no burst has 50 bit errors or fewer,
is still placed right.

The centre has to be a whole number of MHz: bluey-ox-walker's channelizer
bins are 1 MHz wide and centred on the capture centre. That centre is the
sidecar's, not a constant here. The Bluetooth channel on that frequency is
where the VSG60A's carrier feedthrough, the BB60D's DC and the channel
coincide, so it should be read as the weak one. The link flatness this
reports is the whole link — the VSG60A, two antennas, the room and the
BB60D — and not the VSG60A on its own. A channel whose band lies outside
±13.5 MHz of the sidecar's centre is outside the band the BB60D can use:
the summary says so, and that channel is left out of the flatness, as is a
channel with fewer than three found bursts and one whose SNR is more than
10 dB below the others' (an interferer, or DC).
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import scipy.fft as spfft
from scipy.signal import fftconvolve, firwin, lfilter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import bt_synth  # noqa: E402
from scripts.bt_ota_check import check_btbb, libbtbb  # noqa: E402
from scripts.bt_tx_hop import BB60D_REACH_MHZ, outside_bb60d  # noqa: E402

#: At most this many samples are read at once. A recording of several
#: seconds at 40 MS/s does not have to fit in memory. A test may shrink it.
CHUNK = 40_000_000

#: Power is measured in blocks of this many samples: 25 us at 40 MS/s, and
#: the shot is located from those blocks. A test may change it.
LOCATE_BLOCK = 1000

#: The locate pass transforms at most this many samples at a time, so the
#: transform's own arrays stay a few tens of megabytes.
LOCATE_SLICE = 2_000_000

#: A channel's band, for the locate: its carrier ±this. A burst's power is
#: nearly all inside it; the next channel's band is 0.3 MHz away.
BAND_HALF_MHZ = 0.35

#: A block this many dB above its channel's own median is hot on that
#: channel. A burst 12 dB over the noise in 1 MHz is 13 dB over it in this
#: band; the noise itself wanders about ±1 dB, and a hot run has to be a
#: burst long.
HOT_DB = 4.0

#: A block hot on this many channels at once, each within
#: ``BROADBAND_WINDOW_DB`` of the loudest, is a wideband frame. A burst lights
#: one channel (a very strong one, its skirts at -35 dBc, lights its
#: neighbours too, but those are far below it).
BROADBAND_CHANNELS = 4
BROADBAND_WINDOW_DB = 15.0

#: A run of hot blocks on one channel may have this many blocks missing
#: and still be one run, and it counts as a burst's worth from this fraction
#: of a burst's length.
HOLE_BLOCKS = 2
MIN_RUN_FRACTION = 0.4

#: Votes for the shot's origin that are this many samples apart are the
#: same origin. A run's edge is only known to a block, and the clock moves
#: the far end of a shot by 1500 samples at 100 ppm.
VOTE_TOL = 3000

#: The shot has to be seen on at least this many separate runs.
MIN_RUNS = 2

#: At most this many bursts of one origin are tried as the first, in order
#: of how much of a burst was seen, and at most this many origins, best
#: first. A pair of runs that agree by chance can outvote a short real shot.
MAX_ANCHORS = 12
MAX_ORIGINS = 6

#: Search half-width, in samples, once a clock slope exists.
WINDOW = 200

#: Wider, until eight bursts have been found and a slope can be fitted.
WINDOW_OPEN = 2000

#: The first burst has only the locate to go on, which is a block or two
#: wide, and the clock moves the far end of a long shot.
WINDOW_FIRST = 4000

#: After the first pass, a burst that was not found but whose channel shows a
#: burst's worth of energy near the fitted line is searched for this far
#: either side of it. A loss of samples shows up as bursts found this far
#: off the line, instead of as bursts that quietly are not found.
RECOVER_WINDOW = 2500

#: 0.7 MHz low-pass, the cutoff ``bt_ota_check`` uses. At 40 MS/s that
#: filter needs twice the taps (and a bit) to keep the same transition in
#: hertz as the 101-tap filter at 20 MS/s, so 201 taps, group delay 100.
TAPS_N = 201

#: Samples from a burst's first preamble sample to the interpolated
#: correlation peak, which is what ``find_burst`` subtracts. It is the mean
#: of ``(lo + best + frac) - true start``, the quantity it is applied to, over
#: 5040 bursts of planted recordings: DH1, DH3 and DH5, 20, 25, 32 and 40 dB,
#: six seeds each, each with its own clock and its own fractions of a
#: sample. That mean was 99.545 with a standard error of 0.005. By type it is
#: 99.553 (DH1), 99.521 (DH3) and 99.552 (DH5); by SNR 99.552 at 20 dB down
#: to 99.541 at 40 dB, and by the fraction of a sample the burst starts at it
#: moves +-0.05 about that. The 99.47 this replaced was measured on the
#: integer peak and left a mean error of +0.075 sample on the interpolated
#: estimate that is used.
DETECT_OFFSET = 99.545

#: Quiet used for the SNR: 4000 samples ending 2000 before the preamble,
#: read in the filtered channel, as a median.
GAP_END = 2000
GAP_LEN = 4000

#: How far either side of a burst the slice extends, so the gap and the
#: filter's tail are both inside it.
GUARD = 8000

#: A fitted clock further than this from 1 is not a clock.
PPM_LIMIT = 1e-4

#: A burst is ``found`` with at most this many bit errors. A false lock has
#: about half its bits wrong and a real burst at 15 dB or better a handful,
#: but the plain discriminator reads 3 % of a DH5's bits wrong at 12 dB in
#: 1 MHz (median 90 of 2870, none under 50): at 12 dB the bursts are placed
#: right and none of them is found. 143 (5 % of a DH5) would find them.
FOUND_MAX_BIT_ERRORS = 50

#: Reading a burst well enough to steer the clock, to fit it and to be held
#: to the gates below is a looser test than ``found``, so that a weak link is
#: still placed: this many bit errors, or this fraction of the burst's bits if
#: that is more. 143 for a DH5, 50 for a DH1. A false lock is far above it.
MAX_BIT_ERRORS = 50
MAX_BIT_ERROR_FRACTION = 0.05

#: A found burst lies within this many samples of the fitted line. A burst
#: that is read but farther off than that is ``found`` false, "off the line".
OFF_LINE_SAMPLES = 3.0

#: An outlier that is dropped from the fit, and a step in time that is a
#: problem, are farther off the line than this many samples or this many times
#: the scatter of the others, whichever is more: at 12 dB the scatter of a
#: single burst's place is 1.7 samples, and 3 would call one in twelve of them
#: an outlier.
OFF_LINE_SIGMAS = 4.0

#: A burst with good bits this far off the line is a loss of samples next to it,
#: not an outlier: a problem, no truth.
MAX_GOOD_BITS_OFF_LINE_SAMPLES = 20.0

#: Two or more bursts with good bits this far off the line are a step in
#: time, not an outlier, and no truth is written.
OFF_LINE_ROWS = 2

#: The line fitted to the first half of the bursts and the line fitted to
#: the second half meet at the join within this many samples, and have
#: slopes within this many ppm of each other. A loss or a gain of samples
#: moves the join; a change of rate with the clock continuous moves the
#: slopes and not the join. Clean runs: see ``GATE_NOTE``.
#: These are floors. Where the scatter of single bursts is larger (a weak
#: link, a short shot) the limit is ``STEP_SIGMAS`` times what noise alone
#: would give for that difference, so a noisy recording is not refused for
#: being noisy, and a clean one is held to the floor. The slope's floor is
#: 0.5 ppm: a clean 100-burst shot differs by at most 0.21 ppm between its
#: halves, even at 12 dB, and a kink of ``d`` ppm leaves the one line ``d`` x
#: span / 8 samples wrong at its worst: 0.95 sample at 0.5 ppm on the 15 M
#: samples of 100 DH5 bursts, 1.9 on the 200 bursts of a default shot.
MAX_STEP_SAMPLES = 1.0
MAX_SLOPE_STEP_PPM = 0.5
MAX_KINK_SAMPLES = 1.0
STEP_SIGMAS = 4.0

#: A backstop on the whole fit: the scatter about the line is this many
#: samples at most. A clean link is 0.2-0.5 sample, and the real one may be
#: about 1 at 40 MS/s.
MAX_JITTER_SAMPLES = 3.0

#: The recording's carrier is the sidecar's centre. Free-running references
#: are within about 1 ppm, 2.4 kHz at 2.4 GHz, and stage 2 saw -290 to +157
#: Hz. A median measured offset beyond this means the recording is not
#: centred where the sidecar says; one burst beyond it is not found.
MAX_CFO_HZ = 50e3

#: Every burst of the shot that is whole in the recording is placed by the
#: fitted line, found or not. The line has to be known to this many samples
#: (one sigma, from the scatter and from where the found bursts are) at the
#: farthest burst it places: three bursts found in a row say little about
#: where the burst a hundred periods away is. The scatter is taken as at least
#: ``SIGMA_FLOOR``, since a clean link scatters 0.2-0.5 sample and a few bursts
#: can happen to agree better than that.
MAX_PLACEMENT_SAMPLES = 1.0
SIGMA_FLOOR = 0.3

#: The shot's energy runs on past the last burst found, or starts before the
#: first, by this many separate bursts' worth: the tracker lost the shot.
LOST_RUNS = 3

#: A channel whose median SNR is this much under the median of the others'
#: is left out of the flatness: an interferer is in it, or DC.
SNR_OUTLIER_DB = 10.0

#: Channels with fewer found bursts than this are left out of the flatness.
MIN_FLATNESS_BURSTS = 3

#: The SNR the sidecar format defines is over the noise in this bandwidth.
SNR_BW_HZ = 1e6

#: The whole link, said in the summary and in the sidecar.
LINK_NOTE = ('the whole link: the VSG60A, two antennas, the room and the '
             'BB60D, not the VSG60A on its own')

SNR_NOTE = ("burst power over the noise in snr_bw_hz (1 MHz), measured in the quiet "
            'before each burst: an SINR where the channel has an interferer in it')

CFO_MEANING = ("the median carrier offset MEASURED over the found bursts, not a planted "
               "impairment as it is in the synthetic hopping files")

CLK_CONVENTION = ("exact CLK[27:0] at start_sample, the first sample of the access "
                  "code's preamble (not the burst's own first, ramp, sample, 81 samples "
                  'earlier); a one-shot, so the higher bits are the truth and nothing wraps')

START_MEANING = ("the sample nearest the access code's preamble's first sample, counted "
                 "from the first sample of the capture's cf32; start_exact in each burst "
                 'is the fitted start, not the detector')

#: The ramp in front of a burst's preamble, in samples (``br.gfsk``'s lead).
RAMP_SAMPLES = 81


def bit_limit(nbits):
    """The most bit errors a burst of ``nbits`` bits may have and steer the clock."""
    return max(MAX_BIT_ERRORS, int(MAX_BIT_ERROR_FRACTION * nbits))


def require_whole_mhz(side):
    """Refuse a capture whose centre is not a whole number of MHz."""
    centre = side['center_mhz']
    if centre != round(centre):
        raise ValueError('center_mhz %s is not a whole number of MHz' % centre)


def require_sent(side):
    """Refuse a transmitter sidecar that is not the record of a shot sent.

    A failed or interrupted ``--go`` can leave an earlier dry run's file at
    the same path, and a dry run sent nothing.
    """
    tx = side.get('tx') or {}
    if tx.get('dry_run'):
        raise ValueError('the transmitter sidecar is from a dry run (tx.dry_run is true): '
                         'nothing was sent, so there is nothing in a recording to grade')
    if tx.get('sent') is not True:
        raise ValueError('the transmitter sidecar does not say the shot was sent '
                         '(tx.sent is %r, tx.state %r): a failed or interrupted --go, '
                         'or an older file at the same path' % (tx.get('sent'), tx.get('state')))


def record(path, seconds, center_hz, fs, gain_percent):
    """Record the BB60D to a cf32 file through ``apps/bb60_source``.

    As ``bt_ota_check.record`` does, but it keeps the source block, because
    the number that matters here is ``src.overflows``, the times the BB60D
    lost samples (it hands back none, so everything after moves earlier and
    the file is still the length asked for). ``adc_overflows`` is something
    else: the converter clipping, a gain problem that costs no samples.
    """
    from gnuradio import blocks, gr
    from apps import bb60_source as bb
    bb.ensure_plugin_path()
    bb.reset_overflows()
    src = bb.bb60_source(center_hz, fs, gain_percent=gain_percent)
    tb = gr.top_block()
    tb.connect(src, blocks.head(gr.sizeof_gr_complex, int(seconds * fs)),
               blocks.file_sink(gr.sizeof_gr_complex, path))
    t0 = time.time()
    tb.run()
    return {'recorded_s': seconds, 'record_start_unix': t0,
            'gain_percent': gain_percent, 'adc_overflows': bb.overflow_count(),
            'lost_sample_overflows': int(src.overflows)}


class _Recording:
    """A cf32 file read with ``fromfile`` and a count, never mapped whole.

    A capture of several seconds at 40 MS/s is a few gigabytes. Mapping it
    would pin the whole thing in the process once the locate pass touched
    it; a counted read keeps one chunk.
    """

    def __init__(self, path):
        self.path = os.fspath(path)
        self.n = os.path.getsize(self.path) // 8

    def __len__(self):
        return self.n

    def read(self, start, end):
        step = max(int(CHUNK), 1)
        parts = []
        with open(self.path, 'rb') as f:
            pos = int(start)
            while pos < end:
                nxt = min(pos + step, int(end))
                f.seek(pos * 8)
                parts.append(np.fromfile(f, dtype='<c8', count=nxt - pos))
                pos = nxt
        if len(parts) == 1:
            return parts[0]
        return np.concatenate(parts)


def read_span(iq, start, end):
    """``iq[start:end]`` read at most ``CHUNK`` samples at a time.

    A boundary that falls inside a burst has to come back the same samples
    a single read would, so the grader does not depend on ``CHUNK``.
    """
    start = max(0, int(start))
    end = min(len(iq), int(end))
    if end <= start:
        return np.zeros(0, dtype=np.complex64)
    if isinstance(iq, _Recording):
        return iq.read(start, end)
    step = max(int(CHUNK), 1)
    if end - start <= step:
        return np.asarray(iq[start:end], dtype=np.complex64)
    parts = []
    pos = start
    while pos < end:
        nxt = min(pos + step, end)
        parts.append(np.asarray(iq[pos:nxt], dtype=np.complex64))
        pos = nxt
    return np.concatenate(parts)


def band_powers(iq, n, block, fs, centre_mhz, channels_mhz):
    """Power in each channel's band, per block: ``(channels, blocks)`` float32.

    One pass. Each block of ``block`` samples, on the absolute sample grid
    whatever ``CHUNK`` is, is transformed, and the power in the bins within
    ``BAND_HALF_MHZ`` of each channel's carrier is summed. A short tail is
    dropped. Only a couple of million samples are transformed at a time.
    """
    nblocks = int(n) // int(block)
    out = np.zeros((len(channels_mhz), nblocks), dtype=np.float32)
    if nblocks == 0 or not channels_mhz:
        return out
    freqs = np.fft.fftfreq(block, 1.0 / fs)
    sel = np.zeros((block, len(channels_mhz)), dtype=np.float32)
    for i, mhz in enumerate(channels_mhz):
        sel[np.abs(freqs - (mhz - centre_mhz) * 1e6) <= BAND_HALF_MHZ * 1e6, i] = 1.0
    # Whole blocks, at most CHUNK samples a read and LOCATE_SLICE a transform.
    piece = max(1, min(int(CHUNK), int(LOCATE_SLICE)) // block) * block
    limit = nblocks * block
    pos = 0
    while pos < limit:
        seg = read_span(iq, pos, min(pos + piece, limit))
        m = len(seg) // block
        if m == 0:
            break
        spec = spfft.fft(seg[:m * block].reshape(m, block), axis=1)
        power = spec.real * spec.real + spec.imag * spec.imag
        first = pos // block
        out[:, first:first + m] = (power @ sel).T
        pos += m * block
    return out


def _runs(flags, hole):
    """``(first, last, count)`` of each run of True in ``flags``, where up to
    ``hole`` False values inside are still the same run."""
    idx = np.flatnonzero(flags)
    if len(idx) == 0:
        return []
    breaks = np.flatnonzero(np.diff(idx) > hole + 1) + 1
    return [(int(r[0]), int(r[-1]), len(r)) for r in np.split(idx, breaks)]


def locate(iq, side):
    """Where the shot is: ``(info, None)``, or ``(None, problem)``.

    ``info`` holds the runs of energy found on each channel, the origin
    they vote for, and the bursts to try first. See the module docstring.
    """
    fs = side['sample_rate']
    centre = side['center_mhz']
    bursts = side['bursts']
    block = max(int(LOCATE_BLOCK), 1)
    sps = int(round(fs / side['modulation']['symbol_rate']))
    mhz_list = sorted({b['channel_mhz'] for b in bursts})
    power = band_powers(iq, len(iq), block, fs, centre, mhz_list)
    if power.shape[1] == 0:
        return None, 'recording is too short to grade'
    noise = np.maximum(np.median(power, axis=1).astype(np.float64), 1e-30)
    excess = power / noise[:, None].astype(np.float32)
    lit = excess > np.float32(10 ** (HOT_DB / 10.0))
    # A wideband frame is hot on many channels at once, and about equally.
    top = excess.max(axis=0)
    near = excess >= top * np.float32(10 ** (-BROADBAND_WINDOW_DB / 10.0))
    wide = (lit & near).sum(axis=0) >= BROADBAND_CHANNELS
    wide[1:] |= wide[:-1].copy()
    wide[:-1] |= wide[1:].copy()
    lit[:, wide] = False
    del excess, near, power
    n_b = len(bursts[0]['air_bits']) * sps / float(block)
    need = max(2, int(MIN_RUN_FRACTION * n_b))
    runs = []
    for ci in range(len(mhz_list)):
        for first, last, count in _runs(lit[ci], HOLE_BLOCKS):
            if count >= need:
                runs.append((ci, first, last, count))
    runs.sort(key=lambda r: r[1])
    if not runs:
        return None, ('no shot: no run of a burst\'s length, hot against its own channel, '
                      'on any channel of the shot')
    by_chan = {}
    for k, b in enumerate(bursts):
        by_chan.setdefault(b['channel_mhz'], []).append(k)
    origins, weights, run_ids, ks, ests = [], [], [], [], []
    for rid, (ci, first, last, count) in enumerate(runs):
        weight = min(count, n_b) / n_b
        # The block holds the first of the burst's energy, which starts one
        # ramp before the preamble.
        est = first * block + block / 2.0 + RAMP_SAMPLES
        for k in by_chan[mhz_list[ci]]:
            origins.append(est - bursts[k]['start_sample'])
            weights.append(weight)
            run_ids.append(rid)
            ks.append(k)
            ests.append(est)
    origins = np.asarray(origins)
    weights = np.asarray(weights)
    order = np.argsort(origins, kind='stable')
    origins, weights = origins[order], weights[order]
    run_ids = np.asarray(run_ids)[order]
    ks = np.asarray(ks)[order]
    ests = np.asarray(ests)[order]
    lo = np.searchsorted(origins, origins - VOTE_TOL, side='left')
    hi = np.searchsorted(origins, origins + VOTE_TOL, side='right')
    left = weights.copy()
    clusters = []
    for _ in range(MAX_ORIGINS):
        cum = np.concatenate([[0.0], np.cumsum(left)])
        score = cum[hi] - cum[lo]
        best = int(np.argmax(score))
        members = np.arange(lo[best], hi[best])
        members = members[left[members] > 0]
        distinct = len(set(run_ids[members].tolist()))
        if distinct < MIN_RUNS:
            break
        # Weighted median of the members' origins.
        cw = np.cumsum(weights[members])
        origin = float(origins[members][int(np.searchsorted(cw, cw[-1] / 2.0))])
        cand = sorted(members.tolist(), key=lambda i: (-weights[i], abs(origins[i] - origin)))
        seen, candidates = set(), []
        for i in cand:
            if int(ks[i]) not in seen:
                seen.add(int(ks[i]))
                candidates.append((int(ks[i]), float(ests[i])))
        clusters.append({'origin': origin, 'candidates': candidates[:MAX_ANCHORS],
                         'agreeing_runs': distinct, 'weight': float(score[best])})
        # The next origin has to be a different one.
        gone = (origins > origin - 2 * VOTE_TOL) & (origins < origin + 2 * VOTE_TOL)
        left[gone] = 0.0
    if not clusters:
        return None, ('no train: %d runs of a burst\'s length, and no two of them agree on '
                      'where the shot is' % len(runs))
    return {'clusters': clusters, 'runs': runs, 'lit': lit, 'mhz': mhz_list, 'block': block,
            'n_blocks': int(lit.shape[1])}, None


def _taps(fs):
    n = TAPS_N if abs(fs - 40e6) < 1 else int(round(TAPS_N * fs / 40e6))
    if n % 2 == 0:
        n += 1
    return firwin(n, min(0.7e6, 0.45 * fs), fs=fs)


def _offset(fs):
    """The measured peak delay at 40 MS/s, scaled with the filter length."""
    if abs(fs - 40e6) < 1:
        return DETECT_OFFSET
    n = TAPS_N if abs(fs - 40e6) < 1 else int(round(TAPS_N * fs / 40e6))
    if n % 2 == 0:
        n += 1
    return n // 2 - 0.5


def enbw_hz(taps, fs):
    """The equivalent noise bandwidth of the channel filter, from its taps."""
    taps = np.asarray(taps, dtype=np.float64)
    return float(fs * np.sum(taps ** 2) / np.sum(taps) ** 2)


def find_burst(iq, pred, window, entry, fs, centre, taps, tmpl, tmpl_norm, offset, lib, uap):
    """The access code of ``entry`` near the predicted preamble ``pred``.

    The channel is shifted to baseband with the absolute sample index, then
    low-passed, and the 72-bit access code is correlated on the discriminator.
    Returns a row, or None when the access code is not in the window.
    """
    air = np.array([int(c) for c in entry['air_bits']])
    nbits = len(air)
    sps = len(tmpl) // 72
    lo = int(np.floor(pred - window - GUARD))
    hi = int(np.ceil(pred + nbits * sps + window + GUARD))
    seg = read_span(iq, lo, hi)
    if len(seg) < nbits * sps // 2 + len(tmpl):
        return None
    # lo may have been clipped at 0; the slice actually starts at max(lo, 0).
    lo = max(0, lo)
    n = lo + np.arange(len(seg))
    cycles = (entry['channel_mhz'] - centre) * 1e6 / fs * n
    mixed = seg * np.exp(-2j * np.pi * (cycles - np.floor(cycles)))
    bb = lfilter(taps, [1.0], mixed)
    del mixed
    # A sample of exact silence has no phase. Real recordings are noise;
    # this keeps a zeroed gap from poisoning the correlation.
    d = np.nan_to_num(np.angle(bb[1:] * np.conj(bb[:-1])))
    if len(d) < len(tmpl) + 10:
        return None
    dm = d - np.convolve(d, np.ones(2001) / 2001, 'same')
    corr = fftconvolve(dm, tmpl[::-1], 'valid')
    norm = np.sqrt(np.maximum(fftconvolve(dm * dm, np.ones(len(tmpl)), 'valid'), 0.0))
    norm *= tmpl_norm
    score = corr / np.maximum(norm, 1e-12)
    del corr, norm, dm
    best = None
    best_r = 0.5
    for p in np.where(score > 0.5)[0]:
        preamble = (lo + int(p)) - offset
        if abs(preamble - pred) <= window and score[p] > best_r:
            best = int(p)
            best_r = float(score[p])
    if best is None:
        return None
    # The winning bin is an integer. A burst does not start on one, and on a
    # train of a few dozen bursts that rounding is about a tenth of a ppm.
    frac = 0.0
    if 0 < best < len(score) - 1:
        y0, y1, y2 = float(score[best - 1]), float(score[best]), float(score[best + 1])
        denom = y0 - 2.0 * y1 + y2
        if denom < -1e-12:
            frac = 0.5 * (y0 - y2) / denom
            if frac < -0.5 or frac > 0.5:
                frac = 0.0
    del score
    centres = best + (np.arange(nbits) + 0.5) * sps - 0.5
    if centres[0] < 0 or centres[-1] > len(d) - 1:
        return None
    freq = np.interp(centres, np.arange(len(d)), d)
    a72 = air[:72] * 2 - 1
    _slope, thr = np.polyfit(a72, freq[:72], 1)
    bits = (freq > thr).astype(int)
    errors = int((bits != air).sum())
    env = bb.real.astype(np.float64) ** 2 + bb.imag.astype(np.float64) ** 2
    on = float(env[best:best + nbits * sps].mean()) if best + nbits * sps <= len(env) else float('nan')
    # The quiet ends 2000 samples before the preamble, not before the peak.
    # The peak sits DETECT_OFFSET later, which is the filter, not the gap.
    gap1 = int(np.floor((best - offset) - GAP_END))
    gap0 = gap1 - GAP_LEN
    snr = None
    if gap0 >= 0 and 0 < gap1 <= len(env) and on > 0:
        # |noise|^2 in the filter is exponential, whose median is ln 2 of its
        # mean, so the quiet's median is divided by that to be the noise's
        # power. The burst is what is left of ``on``, and the noise in 1 MHz
        # is that power over the filter's equivalent noise bandwidth, times 1
        # MHz.
        gap = float(np.median(env[gap0:gap1])) / np.log(2.0)
        if gap > 0:
            snr = float(10 * np.log10(max((on - gap) / gap, 1e-6)
                                      * enbw_hz(taps, fs) / SNR_BW_HZ))
    power = float(10 * np.log10(on)) if on > 0 else None
    header = crc = exact = False
    if lib is not None:
        header, crc, exact = check_btbb(lib, bits, entry, uap)
    return {
        'index': None,
        'peak': lo + best,
        'preamble': (lo + best + frac) - offset,
        'bit_errors': errors,
        'snr_db': snr,
        'power_dbfs': power,
        'cfo_hz': float(thr / (2 * np.pi) * fs),
        'libbtbb_header': bool(header),
        'libbtbb_crc': bool(crc),
        'libbtbb_exact': bool(exact),
        'correlation': best_r,
    }


def _timing_weight(rows):
    """``1/sigma`` for a clock fit. A louder burst is timed more tightly.

    ``numpy.polyfit`` applies ``w`` to the residual itself, so this is the
    square root of the burst's power, not the power. The link is not flat,
    and a quiet channel must not pull the clock as hard as a loud one.
    Missing powers mean every burst counts the same.
    """
    powers = [r.get('power_dbfs') for r in rows]
    if any(p is None or not np.isfinite(p) for p in powers):
        return None
    return 10 ** (np.asarray(powers, dtype=np.float64) / 20.0)


def _fit(rows, bursts):
    """``(slope, intercept)`` of preamble against the shot's start_sample,
    or None when there are fewer than three rows."""
    if len(rows) < 3:
        return None
    s = np.array([bursts[r['index']]['start_sample'] for r in rows], dtype=np.float64)
    p = np.array([r['preamble'] for r in rows], dtype=np.float64)
    slope, intercept = np.polyfit(s, p, 1, w=_timing_weight(rows))
    return float(slope), float(intercept)


def _residuals(rows, bursts, fit):
    s = np.array([bursts[r['index']]['start_sample'] for r in rows], dtype=np.float64)
    p = np.array([r['preamble'] for r in rows], dtype=np.float64)
    return p - (fit[0] * s + fit[1])


def track(iq, side, located):
    """Every burst of the truth that can be read, each searched near where
    the ones already found say it should be.

    It starts from the burst whose energy the locate saw best and works
    outward, later bursts and then earlier ones. The slope is 0 until eight
    bursts have been found, and the window is wider until then. A miss is not
    an error: the prediction for the next burst still comes from the nearest
    one that was found, so a dozen missing do not lose the rest of the
    train. When both ways are done, a burst that was not found but whose
    channel shows energy near the line is looked for again in a wide window.
    Returns ``(rows, problem)``, the rows in order of burst.
    """
    fs = side['sample_rate']
    centre = side['center_mhz']
    bursts = side['bursts']
    air0 = np.array([int(c) for c in bursts[0]['air_bits']])
    sps = int(round(fs / side['modulation']['symbol_rate']))
    tmpl = np.repeat(air0[:72] * 2 - 1, sps).astype(np.float64)
    tmpl -= tmpl.mean()
    tmpl_norm = float(np.linalg.norm(tmpl))
    taps = _taps(fs)
    offset = _offset(fs)
    lib = libbtbb()
    block = located['block']
    first_window = max(WINDOW_FIRST, 2 * block)

    def look(k, pred, window):
        row = find_burst(iq, pred, window, bursts[k], fs, centre, taps, tmpl, tmpl_norm,
                         offset, lib, side['uap'])
        if row is not None:
            row['index'] = k
            row['S'] = bursts[k]['start_sample']
        return row

    rows = {}
    steer = []
    anchor = None
    for cluster in located['clusters']:
        rows.clear()         # a row from an origin that did not pan out is not evidence
        for k, pred in cluster['candidates']:
            row = look(k, pred, first_window)
            if row is None:
                continue
            rows[k] = row
            if row['bit_errors'] <= bit_limit(len(bursts[k]['air_bits'])):
                steer.append(row)
                anchor = k
                break
        if anchor is not None:
            break
    if anchor is None:
        return [], ('the shot was located, but none of the bursts tried first could be read: '
                    'the access code was not there')

    def predict(s):
        frac, window = 0.0, WINDOW_OPEN
        if len(steer) >= 8:
            ss = np.array([r['S'] for r in steer], dtype=np.float64)
            pp = np.array([r['preamble'] for r in steer], dtype=np.float64)
            frac = float(np.polyfit(ss, pp, 1, w=_timing_weight(steer))[0] - 1.0)
            window = WINDOW
        near = min(steer, key=lambda r: abs(r['S'] - s))
        return near['preamble'] + (s - near['S']) * (1.0 + frac), window

    for step in (1, -1):
        k = anchor + step
        while 0 <= k < len(bursts):
            pred, window = predict(bursts[k]['start_sample'])
            row = look(k, pred, window)
            if row is not None:
                rows[k] = row
                # A wild false peak would drag the slope. Keep it as a row,
                # but do not steer by a burst whose bits are mostly wrong.
                if row['bit_errors'] <= bit_limit(len(bursts[k]['air_bits'])):
                    steer.append(row)
            k += step
    # Second look, at the missing bursts that have energy where the line
    # puts them: a loss of samples moves the bursts after it, out of the
    # narrow window, and they must show up as bursts that are off the line.
    fit = _fit(steer, bursts)
    if fit is not None:
        lit, blk = located['lit'], located['block']
        row_of = {m: i for i, m in enumerate(located['mhz'])}
        for k, entry in enumerate(bursts):
            if k in rows:
                continue
            pos = fit[0] * entry['start_sample'] + fit[1]
            span = len(entry['air_bits']) * sps
            a = int(max(0, (pos - RECOVER_WINDOW) // blk))
            b = int(min(lit.shape[1], (pos + span + RECOVER_WINDOW) // blk + 1))
            if b <= a:
                continue
            seen = int(lit[row_of[entry['channel_mhz']], a:b].sum())
            if seen < max(2, int(MIN_RUN_FRACTION * span / blk)):
                continue
            row = look(k, pos, RECOVER_WINDOW)
            if row is not None:
                row['recovered'] = True
                rows[k] = row
    return [rows[k] for k in sorted(rows)], None


def _halves(rows, bursts, residuals):
    """The line through the first rows against the line through the rest,
    at every split from a tenth to nine tenths of the way along (each side
    at least three rows): ``(step, slope_step_ppm, step_limit, slope_limit_ppm)``
    for the split that comes nearest to its limits. The step is where the two
    lines meet, the slope step the difference of their slopes, and the limits
    are what noise alone would allow each (``STEP_SIGMAS`` sigma) or the floor.
    None when no split has three rows a side.

    The slope floor scales with the shot: a change of rate ``d`` ppm at a
    fraction ``f`` of a shot ``span`` samples long leaves the one line wrong by
    up to ``d`` x span x f(1-f) / 2 samples, so the floor is the smaller of
    ``MAX_SLOPE_STEP_PPM`` and what keeps that under ``MAX_KINK_SAMPLES``.

    The noise is the scatter of single bursts' places, from the differences
    of neighbouring residuals, so a step in the middle of the shot is one
    large difference and does not raise it; and at least ``SIGMA_FLOOR``.
    """
    n = len(rows)
    s = np.array([bursts[r['index']]['start_sample'] for r in rows], dtype=np.float64)
    p = np.array([r['preamble'] for r in rows], dtype=np.float64)
    d = np.diff(np.asarray(residuals, dtype=np.float64))
    # No less than SIGMA_FLOOR: with eight bursts a median of seven differences
    # can be a fifth of the true scatter by chance.
    sigma = max(SIGMA_FLOOR, 1.4826 * float(np.median(np.abs(d - np.median(d)))) / np.sqrt(2.0))
    span = float(s[-1] - s[0]) if n > 1 else 0.0
    best = None
    for h in sorted({int(round(n * f / 10.0)) for f in range(1, 10)}):
        if h < 3 or n - h < 3:
            continue
        a = np.polyfit(s[:h], p[:h], 1)
        b = np.polyfit(s[h:], p[h:], 1)
        join = 0.5 * (s[h - 1] + s[h])
        sxx_a = float(np.sum((s[:h] - s[:h].mean()) ** 2))
        sxx_b = float(np.sum((s[h:] - s[h:].mean()) ** 2))
        gain = (1.0 / h + (join - s[:h].mean()) ** 2 / sxx_a
                + 1.0 / (n - h) + (join - s[h:].mean()) ** 2 / sxx_b)
        step_limit = max(MAX_STEP_SAMPLES, STEP_SIGMAS * sigma * np.sqrt(gain))
        frac = (join - s[0]) / span if span > 0 else 0.5
        scaled = 2.0 * MAX_KINK_SAMPLES / (span * max(frac * (1.0 - frac), 1e-3)) * 1e6 if span > 0 else 1e9
        slope_limit = max(min(MAX_SLOPE_STEP_PPM, scaled),
                          STEP_SIGMAS * sigma * np.sqrt(1.0 / sxx_a + 1.0 / sxx_b) * 1e6)
        step = float(abs(np.polyval(a, join) - np.polyval(b, join)))
        slope = float(abs(a[0] - b[0]) * 1e6)
        ratio = max(step / step_limit, slope / slope_limit)
        if best is None or ratio > best[0]:
            best = (ratio, step, slope, float(step_limit), float(slope_limit))
    return None if best is None else best[1:]


def _span_samples(entry, sps):
    return len(entry['air_bits']) * sps


def _unexplained(located, rows, edge_rows, bursts, sps, from_end, fit):
    """How many separate runs of energy past the last found burst (or before
    the first), that no burst accounts for, are the shot's: on a channel the
    schedule uses, at the time it puts a burst on that channel once the whole
    remainder is shifted by one common amount (a loss of samples shifts it all
    by the same). Runs vote for the shift as the locate votes for the origin,
    and the count is the most any one shift gets, within ``VOTE_TOL``. Ambient
    traffic on a channel is hot at times the schedule has no use for, so it
    does not agree with itself and does not count. A burst the demodulator
    could not read accounts for its own run."""
    blk = located['block']
    # A run much longer than a burst is not one of this shot's.
    # The rest of the shot cannot lie farther off than the shot is long.
    horizon = bursts[-1]['start_sample'] - bursts[0]['start_sample'] + 100000
    longest = 1.5 * len(bursts[0]['air_bits']) * sps / float(blk) + 3
    spans = [(r['preamble'] - RAMP_SAMPLES - blk,
              r['preamble'] + _span_samples(bursts[r['index']], sps) + blk) for r in rows]
    if not edge_rows:
        return 0
    if from_end:
        edge = max(r['preamble'] + _span_samples(bursts[r['index']], sps) for r in edge_rows)
        todo = [(ci, f * blk, (l + 1) * blk) for ci, f, l, c in located['runs']
                if edge - blk <= f * blk <= edge + horizon and c <= longest]
    else:
        edge = min(r['preamble'] for r in edge_rows) - RAMP_SAMPLES
        todo = [(ci, f * blk, (l + 1) * blk) for ci, f, l, c in located['runs']
                if edge - horizon <= (l + 1) * blk <= edge + blk and c <= longest]
    preds = {}
    for k, b in enumerate(bursts):
        preds.setdefault(b['channel_mhz'], []).append(fit[0] * b['start_sample'] + fit[1])
    deltas, ids = [], []
    for rid, (ci, a, b) in enumerate(todo):
        if any(lo < b and a < hi for lo, hi in spans):
            continue
        est = a + blk / 2.0 + RAMP_SAMPLES
        for pred in preds.get(located['mhz'][ci], ()):
            deltas.append(est - pred)
            ids.append(rid)
    if not deltas:
        return 0
    order = np.argsort(deltas)
    d = np.asarray(deltas)[order]
    ids = np.asarray(ids)[order]
    lo = np.searchsorted(d, d - VOTE_TOL, side='left')
    hi = np.searchsorted(d, d + VOTE_TOL, side='right')
    count = max(len(set(ids[a:b].tolist())) for a, b in zip(lo, hi))
    # Votes that land together by chance: ambient runs vote for a hundred
    # shifts each. A shift has to beat what chance gives over every window tried, at 1 in 1000.
    lam = len(d) * 2.0 * VOTE_TOL / max(float(d[-1] - d[0]), 2.0 * VOTE_TOL)
    from scipy.stats import poisson
    needed = max(LOST_RUNS, int(poisson.isf(1e-3 / len(d), lam)) + 1)
    return count if count >= needed else 0


def bt_period_samples(bursts, sps):
    """The spacing of one burst to the next, in samples, from the sidecar."""
    if len(bursts) < 2:
        return 0
    return int(bursts[1]['start_sample'] - bursts[0]['start_sample'])


def _per_channel(bursts, rows, center_mhz):
    """Per-channel counts and levels. The centre is the sidecar's.

    ``rows`` are the found bursts and nothing else. A channel whose band is
    outside ±13.5 MHz of that centre is marked ``outside_bb60d_band``. Left
    out of the link flatness, and marked ``excluded`` with why: that channel,
    a channel with fewer than three found bursts, and a channel whose SNR is
    more than 10 dB under the other channels' (an interferer is in it, or it
    is the DC bin).
    """
    sent = {}
    for b in bursts:
        sent[b['channel']] = sent.get(b['channel'], 0) + 1
    got = {}
    for r in rows:
        got.setdefault(bursts[r['index']]['channel'], []).append(r)
    per = {}
    for channel in sorted(sent):
        rs = got.get(channel, [])
        snr = [r['snr_db'] for r in rs if r['snr_db'] is not None]
        power = [r['power_dbfs'] for r in rs if r['power_dbfs'] is not None]
        info = {
            'bursts': sent[channel],
            'found': len(rs),
            'snr_db': None if not snr else float(np.median(snr)),
            'power_dbfs': None if not power else float(np.median(power)),
        }
        if outside_bb60d(channel, center_mhz):
            info['outside_bb60d_band'] = True
            info['excluded'] = 'outside the BB60D band'
        elif len(rs) < MIN_FLATNESS_BURSTS:
            info['excluded'] = 'fewer than %d found bursts' % MIN_FLATNESS_BURSTS
        per[str(channel)] = info
    usable = {c: i for c, i in per.items() if 'excluded' not in i and i['power_dbfs'] is not None}
    for c, info in usable.items():
        others = [i['snr_db'] for cc, i in usable.items() if cc != c and i['snr_db'] is not None]
        if info['snr_db'] is not None and len(others) >= 2 \
                and info['snr_db'] < float(np.median(others)) - SNR_OUTLIER_DB:
            info['excluded'] = ('median snr_db %.1f dB, more than %g dB under the other '
                                "channels' (an interferer, or DC)" % (info['snr_db'], SNR_OUTLIER_DB))
    levels = [i['power_dbfs'] for i in per.values()
              if 'excluded' not in i and i['power_dbfs'] is not None]
    flat = float(max(levels) - min(levels)) if len(levels) >= 2 else None
    return per, flat


def grade(iq, side):
    """Grade a recording of one hopping shot.

    ``iq`` is a path or an array. Returns ``(result, rows, fit)``. ``rows`` is
    every burst whose access code was read, each with ``found`` and, when it
    is not, a ``reason``. ``fit`` is ``(slope, intercept)`` of each found
    burst's preamble against its ``start_sample`` in the shot, or None, with
    ``result['problem']`` set, when the recording has no clock this will
    trust. See the module docstring for what is refused.
    """
    require_whole_mhz(side)
    require_sent(side)
    if isinstance(iq, (str, bytes, os.PathLike)):
        iq = _Recording(os.fspath(iq))
    bursts = side['bursts']
    sps = int(round(side['sample_rate'] / side['modulation']['symbol_rate']))
    result = {'sent': len(bursts), 'found': 0, 'graded': 0, 'ungraded': 0,
              'libbtbb_exact': 0, 'libbtbb_header': 0, 'libbtbb_crc': 0}

    def finish_per_channel(found_rows):
        result['per_channel'], result['link_flatness_db'] = _per_channel(
            bursts, found_rows, side['center_mhz'])
        result['link_flatness_note'] = LINK_NOTE

    located, problem = locate(iq, side)
    if problem is not None:
        result['problem'] = problem
        finish_per_channel([])
        return result, [], None
    rows, problem = track(iq, side, located)
    for r in rows:
        r['found'] = False
        r['steers'] = r['bit_errors'] <= bit_limit(len(bursts[r['index']]['air_bits']))
        r['reason'] = None if r['steers'] else 'bit errors'
    good = [r for r in rows if r['steers']]
    # Until the gates have passed this is only a count of bursts read, and
    # of those the ones that would be found; ``found`` is final at the end.
    result['read'] = len(good)
    _fill_stats(result, good, bursts)
    result['found'] = result['graded'] = sum(r['bit_errors'] <= FOUND_MAX_BIT_ERRORS for r in good)
    if problem is not None:
        result['problem'] = problem
        finish_per_channel([])
        return result, rows, None
    if len(good) < 3:
        result['problem'] = 'too short to grade: %d bursts found, a clock needs 3' % len(good)
        finish_per_channel([])
        return result, rows, None

    # The carrier. Free-running references are within a few kHz.
    cfo = float(np.median([r['cfo_hz'] for r in good]))
    result['cfo_hz_median'] = cfo
    if abs(cfo) > MAX_CFO_HZ:
        result['problem'] = ('the recording is not centred where the sidecar says: the median '
                             'carrier offset is %+.0f kHz (over %.0f kHz)' % (cfo / 1e3, MAX_CFO_HZ / 1e3))
        finish_per_channel([])
        return result, rows, None
    for r in good:
        if abs(r['cfo_hz']) > MAX_CFO_HZ:
            r['reason'] = 'carrier offset'
    use = [r for r in good if r['reason'] is None]
    fit = _fit(use, bursts)
    if fit is None:
        result['problem'] = 'too short to grade: %d bursts found, a clock needs 3' % len(use)
        finish_per_channel([])
        return result, rows, None
    if abs(fit[0] - 1) > PPM_LIMIT:
        result['fit_rejected_ppm'] = float((fit[0] - 1) * 1e6)
        result['problem'] = ('fit refused: %.1f ppm is not a clock (over 100 ppm)'
                             % result['fit_rejected_ppm'])
        finish_per_channel([])
        return result, rows, None

    # Every burst with good bits is in the line first. Any step, and any
    # bend, has to show before a burst is called an outlier and dropped.
    res = _residuals(use, bursts, fit)
    # The scatter of a single burst's place is 1 sample or less above 18 dB
    # and 1.7 at 12 dB, so an outlier is 3 samples off or four of that.
    scatter = 1.4826 * float(np.median(np.abs(res - np.median(res))))
    off_limit = max(OFF_LINE_SAMPLES, OFF_LINE_SIGMAS * scatter)
    # A burst whose bits were read well and which is this far off the line is
    # not noise: its place is where its bits are, and the line is wrong there.
    # The scatter of one burst is 1.7 samples at 12 dB, so 20 is 12 sigma.
    far = [(r, e) for r, e in zip(use, res) if abs(e) > max(MAX_GOOD_BITS_OFF_LINE_SAMPLES, off_limit)]
    if far:
        r, e = max(far, key=lambda t: abs(t[1]))
        result['problem'] = ('burst %d was read with %d bit errors and lies %+.0f samples off the '
                             'fitted clock (over %g): samples were lost or gained next to it, so '
                             'the line cannot place it' % (r['index'], r['bit_errors'], e,
                                                           MAX_GOOD_BITS_OFF_LINE_SAMPLES))
        finish_per_channel([])
        return result, rows, None
    off = [r for r, e in zip(use, res) if abs(e) > off_limit]
    result['off_line_bursts'] = len(off)
    if len(off) >= OFF_LINE_ROWS:
        result['problem'] = ('timing steps: %d bursts with good bits are more than %.1f samples '
                             'off the fitted clock, which is a loss or a gain of samples, not '
                             'noise; worst %.1f samples' % (len(off), off_limit, np.abs(res).max()))
        finish_per_channel([])
        return result, rows, None
    for r in off:
        r['reason'] = 'off the line'
    keep = [r for r in use if r['reason'] is None]
    fit = _fit(keep, bursts)
    if fit is None:
        result['problem'] = 'too short to grade: %d bursts found, a clock needs 3' % len(keep)
        finish_per_channel([])
        return result, rows, None
    res = _residuals(keep, bursts, fit)
    again = [r for r, e in zip(keep, res) if abs(e) > off_limit]
    if again:
        result['problem'] = ('timing steps: with the isolated outlier left out, %d more bursts '
                             'are more than %.1f samples off the line' % (len(again), off_limit))
        finish_per_channel([])
        return result, rows, None
    jitter = float(np.std(res))
    result['timing_jitter_samples'] = jitter
    result['clock_ppm'] = float((fit[0] - 1) * 1e6)
    halves = _halves(keep, bursts, res)
    if halves is not None:
        (result['step_samples'], result['slope_step_ppm'],
         result['step_limit_samples'], result['slope_step_limit_ppm']) = halves
    result['on_line_bursts'] = len(keep)
    why = None
    if jitter > MAX_JITTER_SAMPLES:
        why = ('the timing scatter about the clock is %.2f samples (over %g): the recording '
               'does not keep one clock' % (jitter, MAX_JITTER_SAMPLES))
    elif halves is not None and halves[0] > halves[2]:
        why = ('the clock steps by %.2f samples between the first half of the bursts and the '
               'second (over %.2f): samples were lost or gained' % (halves[0], halves[2]))
    elif halves is not None and halves[1] > halves[3]:
        why = ('the clock rate differs by %.2f ppm between the first half of the bursts and '
               'the second (over %.2f): it does not keep one clock' % (halves[1], halves[3]))
    if why is None:
        why = _lost_the_shot(located, rows, keep, fit, bursts, sps, len(iq))
    if why is None:
        sigma = _placement_sigma(keep, bursts, jitter, sps, len(iq), fit)
        result['placement_sigma_samples'] = sigma
        if sigma > MAX_PLACEMENT_SAMPLES:
            why = ('the clock fitted to %d found bursts is known only to %.1f samples at the '
                   'farthest burst it would place (over %g): too few bursts, too close together'
                   % (len(keep), sigma, MAX_PLACEMENT_SAMPLES))
    if why is not None:
        result['problem'] = why
        finish_per_channel([])
        return result, rows, None

    # Found is the strict reading: at most FOUND_MAX_BIT_ERRORS bit errors and
    # within OFF_LINE_SAMPLES of the line. The others were read well enough
    # to steer the clock, are placed by it, and say why they are not found.
    for r, e in zip(keep, res):
        if r['bit_errors'] > FOUND_MAX_BIT_ERRORS:
            r['reason'] = 'bit errors'
        elif abs(e) > OFF_LINE_SAMPLES:
            r['reason'] = 'off the line'
        else:
            r['found'] = True
            r['reason'] = None
    found_rows = [r for r in keep if r['found']]
    result['found'] = result['graded'] = len(found_rows)
    if found_rows:
        errors = np.array([r['bit_errors'] for r in found_rows], dtype=float)
        length = len(bursts[0]['air_bits'])
        result.update(bit_errors_median=float(np.median(errors)),
                      zero_error_bursts=int((errors == 0).sum()),
                      raw_ber=float(errors.sum() / (len(errors) * length)))
        snr = [r['snr_db'] for r in found_rows if r['snr_db'] is not None]
        if snr:
            result['snr_db_median'] = float(np.median(snr))
        result['cfo_hz_median'] = float(np.median([r['cfo_hz'] for r in found_rows]))
    result['libbtbb_header'] = int(sum(r['libbtbb_header'] for r in found_rows))
    result['libbtbb_crc'] = int(sum(r['libbtbb_crc'] for r in found_rows))
    result['libbtbb_exact'] = int(sum(r['libbtbb_exact'] for r in found_rows))
    reasons = {}
    for r in rows:
        if not r['found']:
            reasons[r['reason']] = reasons.get(r['reason'], 0) + 1
    if reasons:
        result['not_found_reasons'] = reasons
    finish_per_channel(found_rows)
    return result, rows, fit


def _placement_sigma(keep, bursts, jitter, sps, n_samples, fit):
    """The standard error, in samples, of the fitted line at the farthest of
    the bursts that would be placed (those whole in the recording)."""
    s = np.array([bursts[r['index']]['start_sample'] for r in keep], dtype=np.float64)
    sxx = float(np.sum((s - s.mean()) ** 2))
    xs = [e['start_sample'] for e in bursts
          if 0 <= fit[0] * e['start_sample'] + fit[1]
          and fit[0] * e['start_sample'] + fit[1] + _span_samples(e, sps) <= n_samples]
    if not xs or sxx <= 0:
        return float('inf')
    far = max(abs(x - s.mean()) for x in xs)
    return float(max(jitter, SIGMA_FLOOR) * np.sqrt(1.0 / len(s) + far ** 2 / sxx))


def _fill_stats(result, rows, bursts):
    """Bit-error and libbtbb counts over ``rows``, so that a grade that is
    refused still shows what it saw. A grade that stands overwrites them with
    the counts over the found bursts."""
    if not rows:
        return
    errors = np.array([r['bit_errors'] for r in rows], dtype=float)
    length = len(bursts[0]['air_bits'])
    result.update(bit_errors_median=float(np.median(errors)),
                  zero_error_bursts=int((errors == 0).sum()),
                  raw_ber=float(errors.sum() / (len(errors) * length)),
                  libbtbb_header=int(sum(r['libbtbb_header'] for r in rows)),
                  libbtbb_crc=int(sum(r['libbtbb_crc'] for r in rows)),
                  libbtbb_exact=int(sum(r['libbtbb_exact'] for r in rows)))


def _lost_the_shot(located, rows, keep, fit, bursts, sps, n_samples):
    """A problem text when the shot's energy runs on past (or begins before)
    the bursts that were found, so the tracker lost it, else None."""
    tail = _unexplained(located, rows, keep, bursts, sps, True, fit)
    head = _unexplained(located, rows, keep, bursts, sps, False, fit)
    placed = 0
    for entry in bursts:
        pos = fit[0] * entry['start_sample'] + fit[1]
        if 0 <= pos and pos + _span_samples(entry, sps) <= n_samples:
            placed += 1
    half = len(keep) < 0.5 * placed
    for name, count in (('after the last', tail), ('before the first', head)):
        if count >= LOST_RUNS or (count >= 1 and half):
            return ('tracking lost the shot: %d separate bursts of energy %s burst found '
                    '(%d of %d expected bursts found), which is a loss of samples or a shot '
                    'that was not sent whole' % (count, name, len(keep), placed))
    return None


def capture_sidecar(side, n_samples, rows, fit, result, info, name):
    """The truth of the recording, one entry per burst of the shot.

    Where a burst starts is the fitted clock, not the detector, so a burst
    that was missed is still placed. ``start_sample`` is the nearest sample
    of the capture's cf32. ``clk`` is the transmitter's, exact for a one-shot.
    ``shot_index`` is the burst's index in the transmitter's list, which is
    what ``afh_maps[].first_burst`` counts.
    """
    if fit is None:
        return None
    slope, intercept = fit
    fs = side['sample_rate']
    sps = int(round(fs / side['modulation']['symbol_rate']))
    by_index = {r['index']: r for r in rows}
    placed = []
    for k, src in enumerate(side['bursts']):
        pos = slope * src['start_sample'] + intercept
        span = len(src['air_bits']) * sps
        if 0 <= pos and pos + span <= n_samples:
            placed.append((k, src, float(pos)))
    if not placed:
        return None
    bursts = []
    for k, src, pos in placed:
        nearest = int(np.floor(pos + 0.5))
        entry = dict(src)
        entry.update(shot_index=k, start_sample=nearest, start_exact=round(pos, 3),
                     symbol_phase=nearest % sps, timing_frac=0.0)
        row = by_index.get(k)
        if row is not None and row.get('found'):
            entry.update(found=True, bit_errors=int(row['bit_errors']),
                         snr_db=None if row['snr_db'] is None else round(float(row['snr_db']), 2),
                         power_dbfs=None if row['power_dbfs'] is None else round(float(row['power_dbfs']), 2),
                         cfo_hz=round(float(row['cfo_hz']), 1),
                         libbtbb_header=bool(row['libbtbb_header']),
                         libbtbb_crc=bool(row['libbtbb_crc']),
                         libbtbb_exact=bool(row['libbtbb_exact']))
        else:
            entry.update(found=False, snr_db=None, power_dbfs=None,
                         reason=(row or {}).get('reason') or 'not detected')
            if row is not None:
                entry.update(bit_errors=int(row['bit_errors']), cfo_hz=round(float(row['cfo_hz']), 1))
        bursts.append(entry)
    per, flat = _per_channel(side['bursts'], [r for r in rows if r.get('found')], side['center_mhz'])
    out = {
        'generator': 'SDR scripts/bt_ota_hop_check.py, from scripts/bt_tx_hop.py over the air',
        'generator_commit': bt_synth.commit(),
        'tx_generator_commit': side.get('generator_commit'),
        'name': name,
        'over_the_air': True,
        'lap': side['lap'],
        'uap': side['uap'],
        'hopping': True,
        'hop_channels': side['hop_channels'],
        'afh_map': side['afh_map'],
        'afh_instant': side['afh_instant'],
        'afh_map_count': side.get('afh_map_count'),
        'afh_maps': side.get('afh_maps'),
        'afh_maps_first_burst_meaning': ("first_burst counts the transmitter's bursts; each "
                                         'burst here carries its own shot_index'),
        'afh_instant_meaning': side.get('afh_instant_meaning'),
        'hop_kernel': side.get('hop_kernel'),
        'address_for_hop': side.get('address_for_hop'),
        'clock_lock_note': side.get('clock_lock_note'),
        'channel_mhz': None,
        'bt_channel': None,
        'sample_rate': fs,
        'center_mhz': side['center_mhz'],
        'modulation': side['modulation'],
        'slot_samples': side['slot_samples'],
        'start_offset': side.get('start_offset'),
        'ptype': side.get('ptype', side['bursts'][0]['ptype']),
        'clk': bursts[0]['clk'],
        'clk_convention': CLK_CONVENTION,
        'timing_frac': 0.0,
        'symbol_phase': None,
        'symbol_phase_note': ('drifts with clock_ppm, so each burst has its own: '
                              'start_sample %% %d' % sps),
        'start_sample_meaning': START_MEANING,
        'clock_ppm': round(result['clock_ppm'], 4),
        'cfo_hz': round(result['cfo_hz_median'], 1) if 'cfo_hz_median' in result else None,
        'cfo_hz_meaning': CFO_MEANING,
        'snr_db': round(result['snr_db_median'], 2) if 'snr_db_median' in result else None,
        'snr_bw_hz': SNR_BW_HZ,
        'snr_db_meaning': SNR_NOTE,
        'link_flatness_db': None if flat is None else round(flat, 3),
        'link_flatness_note': LINK_NOTE,
        'per_channel': result.get('per_channel', per),
        'tx': side.get('tx'),
        'receiver': {'radio': 'BB60D',
                     'gain_percent': info.get('gain_percent'),
                     'adc_overflows': info.get('adc_overflows'),
                     'lost_sample_overflows': info.get('lost_sample_overflows'),
                     'record_start_unix': info.get('record_start_unix')},
        'measured': {k: v for k, v in result.items()
                     if k not in ('per_channel', 'link_flatness_note')},
        'bursts': bursts,
    }
    return out


def summary_lines(result):
    """The text the command prints."""
    lines = ['bursts sent %d, found %d, graded %d' % (
        result.get('sent', 0), result.get('found', 0), result.get('graded', 0))]
    if result.get('problem'):
        lines.append('PROBLEM: ' + result['problem'])
    if result.get('not_found_reasons'):
        lines.append('not found: ' + ', '.join('%d %s' % (n, r) for r, n in
                                                sorted(result['not_found_reasons'].items())))
    if 'libbtbb_exact' in result:
        lines.append('libbtbb exact %d' % result['libbtbb_exact'])
    if 'bit_errors_median' in result:
        lines.append('bit errors: median %.1f, raw ber %.3e' % (
            result['bit_errors_median'], result.get('raw_ber', float('nan'))))
    if 'clock_ppm' in result:
        lines.append('clock %+.4f ppm, timing jitter %.3f samples' % (
            result['clock_ppm'], result.get('timing_jitter_samples', float('nan'))))
    if 'snr_db_median' in result:
        lines.append('snr %.1f dB in 1 MHz (an SINR where the channel is not clean)'
                     % result['snr_db_median'])
    per = result.get('per_channel') or {}
    for channel in sorted(per, key=lambda c: int(c)):
        info = per[channel]
        snr = 'n/a' if info['snr_db'] is None else '%.1f dB' % info['snr_db']
        power = 'n/a' if info['power_dbfs'] is None else '%.2f dBFS' % info['power_dbfs']
        band = ', outside the BB60D band' if info.get('outside_bb60d_band') else ''
        left = ''
        if info.get('excluded') and not info.get('outside_bb60d_band'):
            left = ', left out of the flatness: %s' % info['excluded']
        lines.append('channel %s: %d bursts, %d found, snr %s, power %s%s%s' % (
            channel, info['bursts'], info['found'], snr, power, band, left))
    outside = [c for c in sorted(per, key=lambda c: int(c)) if per[c].get('outside_bb60d_band')]
    if outside:
        lines.append('outside the BB60D band (+-%.1f MHz of the centre): channel %s'
                     % (BB60D_REACH_MHZ, ', '.join(outside)))
    flat = result.get('link_flatness_db')
    if flat is None:
        lines.append('link flatness: not enough channels with 3 found bursts (%s)' % LINK_NOTE)
    else:
        lines.append('link flatness %.2f dB (%s)' % (flat, LINK_NOTE))
    return lines


def lost_samples_problem(info):
    """The text for a recording in which the BB60D lost samples, or None."""
    lost = info.get('lost_sample_overflows')
    if lost:
        return ('the BB60D lost samples %d time(s) while recording (bb60_source.overflows): '
                'everything after the first loss is earlier than it should be, so the '
                'recording cannot be placed' % lost)
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('sidecar', help="bt_tx_hop.py's synth_<name>_tx.json")
    ap.add_argument('--iq', help='grade this recording instead of making one')
    ap.add_argument('--record', type=float, help='seconds to record from the BB60D')
    ap.add_argument('--out', help='where --record writes its cf32 when --capture is not given')
    ap.add_argument('--gain', type=float, default=None,
                    help='BB60D gain, percent: 60 when recording; with --iq, stored in the truth')
    ap.add_argument('--capture', metavar='NAME',
                    help="write the recording's truth to bluey-ox-walker's data/sidecar/NAME.json; "
                         'with --record, the cf32 goes to data/iq/NAME.cf32 too')
    args = ap.parse_args(argv)
    if args.iq and args.record:
        ap.error('--iq and --record are two ways to get a recording: give one')
    if args.capture and not args.iq and not args.record:
        ap.error('--capture needs --iq or --record')
    side = json.load(open(args.sidecar))
    try:
        require_whole_mhz(side)
        require_sent(side)
    except ValueError as e:
        ap.error(str(e))
    info = {}
    path = args.iq
    if args.record:
        # record() opens the BB60D. Nothing here imports it before this line.
        if args.capture:
            path = os.path.join(bt_synth.BLUEY, 'data', 'iq', args.capture + '.cf32')
        else:
            path = args.out or 'ota_hop.cf32'
        os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
        info = record(path, args.record, side['center_mhz'] * 1e6, side['sample_rate'],
                      60.0 if args.gain is None else args.gain)
        print('recorded %.1f s at %.0f%% gain, ADC overflows %d, lost-sample overflows %s -> %s' % (
            args.record, info['gain_percent'], info['adc_overflows'],
            info.get('lost_sample_overflows'), path))
    if not path:
        ap.error('give --iq or --record')
    if args.gain is not None:
        info.setdefault('gain_percent', args.gain)
    try:
        result, rows, fit = grade(path, side)
    except ValueError as e:
        ap.error(str(e))
    lost = lost_samples_problem(info)
    if lost:
        # The grade is still printed, for what it says about the link, but
        # no truth is written from a recording that lost samples.
        result['lost_sample_overflows'] = info['lost_sample_overflows']
        result['problem'] = lost + ('; ' + result['problem'] if result.get('problem') else '')
        fit = None
    for line in summary_lines(result):
        print(line)
    if args.capture:
        if fit is None:
            print('%s: no truth sidecar written' % result.get('problem', 'no clock fit'))
            return result
        n_samples = os.path.getsize(path) // 8
        truth = capture_sidecar(side, n_samples, rows, fit, result, info, args.capture)
        if truth is None:
            print('no whole burst in the recording: no truth sidecar written')
            return result
        out_dir = os.path.join(bt_synth.BLUEY, 'data', 'sidecar')
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, args.capture + '.json')
        with open(out, 'w') as f:
            json.dump(truth, f, indent=1, allow_nan=False)
        print('%s: %d bursts sent, %d found' % (out, len(truth['bursts']),
                                                sum(b['found'] for b in truth['bursts'])))
    return result


if __name__ == '__main__':
    main()
