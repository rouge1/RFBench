#!/usr/bin/env python3
"""Hold the DC-channel writer to what its samples say.

    python scripts/test_bt_synth_dc.py

``scripts/bt_synth_dc.py`` writes one channel's train at the centre of the
capture, with and without a continuous-wave spur exactly at DC, for
bluey-ox-walker to settle its DC block with. Its sidecar is the only truth
that receiver gets, so this checks the sidecar against the *samples*, from
small runs (24 bursts, a few ms apart), and never against the generator's own
bookkeeping:

* where each burst starts, found by correlating the known preamble and sync
  word, and that the symbol phases alternate, nothing overlaps, and the timing
  fractions lie in [0, 1);
* where its carrier is, from the spectrum of the raw burst: 0 Hz on channel
  39, +4 MHz on 43, with a carrier offset on top;
* its level: power in the burst less the noise, over the noise in 1 MHz,
  against ``snr_db``, in every SNR block, and the noise itself, the same per
  MHz at 20 and 40 MS/s;
* the spur, measured the way the sidecar says it is defined - a 1024-point
  rectangular FFT of a burst-free stretch - at 15, 25 and 35 dB over the
  noise; its frequency at the start and the end of a drifting run; and no spur
  when there is none;
* the controls: the same bursts on channels 38, 40 and 43 are the channel 39
  bursts moved, and everything else, the noise too, is identical;
* a seed is a file, the named sets are exactly what the task says, an unknown
  set is an error, and the sidecar has every key.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile

os.environ.setdefault('OMP_NUM_THREADS', '4')          # before numpy: small FFTs gain nothing from 20
import numpy as np  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from scripts import bt_synth, bt_synth_dc as dc  # noqa: E402

PY = sys.executable
SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bt_synth_dc.py')
failures = []


def check(ok, what):
    print('  %s %s' % ('ok  ' if ok else 'FAIL', what))
    if not ok:
        failures.append(what)


def packet(side, entry):
    """The packet of a sidecar entry, rebuilt from its LAP, UAP, clock, type
    and payload: its access code to correlate with and its length in bits."""
    return br.Packet(side['lap'], side['uap'], entry['clk'], entry['ptype'],
                     bytes.fromhex(entry['payload_hex']), lt_addr=entry['lt_addr'],
                     flow=entry['flow'], arqn=entry['arqn'], seqn=entry['seqn'])


def shifted_down(x, lo, offset_hz, fs):
    """x, which starts at sample ``lo`` of the file, mixed down by
    ``offset_hz`` with the file's own sample numbers."""
    n = np.arange(lo, lo + len(x))
    cycles = offset_hz / fs * n
    return x * np.exp(-2j * np.pi * (cycles - np.floor(cycles)))


def find_start(iq, side, entry):
    """Where the burst's first bit begins, as a fractional sample: the peak of
    the correlation with the whole packet's modulated bits (rebuilt from the
    sidecar's LAP, UAP, clock and payload, so a wrong payload would not
    correlate), over +/-1500 samples round the sidecar's start and over
    reference timing offsets from 0 to 0.75 of a sample in quarters, after mixing
    the burst's own carrier down. The access code alone is too short: its
    peak is only 1 % down a sample away, and at 12 dB it wanders by two
    samples. ``timing_frac`` is not used. The position is the integer lag plus
    the reference's offset."""
    fs = side['sample_rate']
    bits = packet(side, entry).bits
    lead = br.gfsk(bits[:8], fs)[1]
    n_ref = len(br.gfsk(bits, fs)[0])
    lo = entry['start_sample'] - 1500 - lead
    hi = entry['start_sample'] + 1500 + n_ref
    bb = shifted_down(iq[lo:hi].astype(np.complex128), lo, entry['offset_hz'], fs)
    ref0 = br.gfsk(bits, fs)[0].astype(np.complex128)
    size = 1 << int(np.ceil(np.log2(len(bb) + len(ref0))))
    c = np.abs(np.fft.ifft(np.fft.fft(bb, size) * np.conj(np.fft.fft(ref0, size))))
    i0 = int(np.argmax(c[:len(bb) - len(ref0) + 1]))
    best = (-1, 0)
    for d in np.arange(0, 1, 0.25):
        ref = br.gfsk(bits, fs, delay=d)[0].astype(np.complex128)
        for i in range(i0 - 2, i0 + 3):
            v = abs(np.vdot(ref, bb[i:i + len(ref)]))
            if v > best[0]:
                best = (v, lo + lead + i + d)
    return best[1]


def burst_span(side, entry):
    """First and last sample of the burst's bits, from its own packet."""
    sps = int(round(side['sample_rate'] / 1e6))
    n = len(packet(side, entry).bits)
    return entry['start_sample'], entry['start_sample'] + n * sps


def mean_power(iq, a, b):
    seg = iq[a:b].astype(np.complex128)
    return float(np.mean(seg.real ** 2 + seg.imag ** 2))


def noise_per_mhz(iq, side):
    """The noise power in 1 MHz, from the stretch before the first burst
    (with any spur taken out by subtracting the mean, which for a tone at DC is
    the tone; a drifting tone moves too little in that ms to matter here)."""
    fs = side['sample_rate']
    seg = iq[:int(0.015 * fs)].astype(np.complex128)       # 15 ms: no burst yet
    seg = seg - seg.mean()
    return float(np.mean(np.abs(seg) ** 2)) / (fs / 1e6)


def burst_snr(iq, side, entry):
    """The burst's SNR in 1 MHz from the samples: the mean power over its
    core (the 2 us ramps and the edge symbols left off) less the noise power
    per sample from the lead-in, over the noise in 1 MHz."""
    fs = side['sample_rate']
    sps = int(round(fs / 1e6))
    a, b = burst_span(side, entry)
    core = mean_power(iq, a + 8 * sps, b - 8 * sps)
    noise_sample = noise_per_mhz(iq, side) * (fs / 1e6)
    return 10 * np.log10((core - noise_sample) / (noise_per_mhz(iq, side)))


def centroid_hz(seg, fs):
    """The spectral centroid of ``seg``, in Hz: the peak of its spectrum
    smoothed over 0.2 MHz, then the centroid, noise floor taken away, in
    +/-0.7 MHz of that."""
    n = len(seg)
    p = np.abs(np.fft.fft(seg * np.hanning(n))) ** 2
    f = np.fft.fftfreq(n, 1 / fs)
    order = np.argsort(f)
    f, p = f[order], p[order]
    df = f[1] - f[0]
    k = max(1, int(0.2e6 / df))
    smooth = np.convolve(p, np.ones(k) / k, 'same')
    pk = f[np.argmax(smooth)]
    floor = p[np.abs(f - pk) > 2e6].mean()
    win = np.abs(f - pk) < 0.7e6
    w = np.clip(p[win] - floor, 0, None)
    return float((f[win] * w).sum() / w.sum())


def carrier_hz(iq, side, entry):
    """Where the burst's carrier is, in Hz from the capture centre, from the
    spectrum of its raw core. A burst's spectrum is not centred on its carrier:
    its bits are not half ones, and each extra one moves the mean frequency a
    few kHz, which for a DH1 is 8 kHz or more. So the same core of the clean
    modulator's output for the same bits (``br.gfsk``, no shift, no noise) is
    measured the same way and taken away."""
    fs = side['sample_rate']
    sps = int(round(fs / 1e6))
    a, b = burst_span(side, entry)
    mine = centroid_hz(iq[a + 8 * sps:b - 8 * sps].astype(np.complex128), fs)
    ref, lead = br.gfsk(packet(side, entry).bits, fs, delay=entry['timing_frac'])
    clean = centroid_hz(ref[lead + 8 * sps:lead + (b - a) - 8 * sps].astype(np.complex128), fs)
    return mine - clean


def spur_bins(iq, start, count, fft=1024):
    """The 1024-point rectangular-window power spectra of ``count``
    consecutive blocks from ``start``: an array (count, 1024), bin 0 = DC."""
    x = iq[start:start + count * fft].astype(np.complex128).reshape(count, fft)
    return np.abs(np.fft.fft(x, axis=1)) ** 2


def spur_db_literal(iq, start):
    """The spur level the way the sidecar defines it, from one FFT: the tone
    bin over the median of the other bins, corrected from the median of an
    exponentially-distributed noise bin to its mean (x ln 2)."""
    p = spur_bins(iq, start, 1)[0]
    noise_median = np.median(p[8:-8])
    return 10 * np.log10(p[0] / (noise_median / np.log(2)))


def spur_db_averaged(iq, start, count):
    """The same level from the mean over many FFTs, with the noise in the
    tone bin taken away: (tone bin / noise bin) - 1."""
    p = spur_bins(iq, start, count)
    ratio = p[:, 0].mean() / p[:, 8:-8].mean()
    return 10 * np.log10(max(ratio - 1, 1e-9))


def tone_hz(iq, start, fs, n=2 ** 17):
    """The frequency of the strongest tone in ``n`` samples from ``start``,
    from a zero-padded FFT and a parabolic fit to the peak, in Hz."""
    x = iq[start:start + n].astype(np.complex128)
    pad = 8 * n
    p = np.abs(np.fft.fft(x, pad)) ** 2
    i = int(np.argmax(p))
    a, b, d = p[i - 1], p[i], p[(i + 1) % pad]
    i = i + 0.5 * (a - d) / (a - 2 * b + d)
    f = i * fs / pad
    return f - fs if f > fs / 2 else f


#: How far a burst's measured start may be from start_sample + timing_frac, in
#: samples, by packet type and SNR step in 1 MHz. Measured worst over 3 seeds x
#: 6 bursts a step: DH5 0.38 / 0.15 / 0.13 / 0.15 and DH1 0.79 / 0.49 / 0.32 /
#: 0.24 at 8 / 12 / 16 / 20 dB; the limits are a little above. The estimator's
#: own noise is the limit, not the file's: a DH1 is a tenth of a DH5's energy.
START_TOL = {'DH5': {8: 0.5, 12: 0.3, 16: 0.3, 20: 0.3},
             'DH1': {8: 1.0, 12: 0.7, 16: 0.5, 20: 0.4}}


def start_errors(iq, side, entries):
    """(error, tolerance) per burst: measured start less start_sample +
    timing_frac, and START_TOL for its type and step."""
    return [(abs(find_start(iq, side, e) - e['start_sample'] - e['timing_frac']),
             START_TOL[e['ptype']][int(e['snr_db'])]) for e in entries]


def small(**kw):
    """A 24-burst run, 5 ms apart, two SNR blocks."""
    spec = dict(ptype='DH5', channel=39, bursts=24, spacing_ms=5.0, snr_steps=[12, 20],
                spur='none', seed=7)
    spec.update(kw)
    return dc.synthesise_dc(**spec)


# --- the bursts ---------------------------------------------------------------

print('bursts: start, phase, carrier and level, from the samples')
for ptype in ('DH5', 'DH1'):
    iq, side = small(ptype=ptype)
    bursts = side['bursts']
    fs = side['sample_rate']
    sps = int(round(fs / 1e6))
    starts = np.array([e['start_sample'] for e in bursts])
    # find_start is where the reference's first bit lands; the burst's own is
    # timing_frac later, which the reference does not have, so the peak sits
    # timing_frac after start_sample.
    peak = np.array([find_start(iq, side, e) - e['start_sample'] for e in bursts])
    tf = np.array([e['timing_frac'] for e in bursts])
    errs = [(abs(a), START_TOL[ptype][int(e['snr_db'])]) for a, e in zip(peak - tf, bursts)]
    check(all(a < t for a, t in errs),
          '%s: every burst begins where start_sample + timing_frac says (worst %.2f samples, '
          'limit %s)' % (ptype, max(a for a, _ in errs), sorted({t for _, t in errs})))
    check(np.all((tf >= 0) & (tf < 1)) and tf.std() > 0.1,
          '%s: timing fractions lie in [0, 1) and are not all alike' % ptype)
    phases = [e['symbol_phase'] for e in bursts]
    check(phases == [(e['start_sample'] % sps) for e in bursts]
          and phases == [0 if k % 2 == 0 else sps // 2 for k in range(len(bursts))],
          '%s: symbol_phase is start_sample %% %d, alternating 0 and %d' % (ptype, sps, sps // 2))
    check(side['symbol_phases'] == [0, sps // 2], '%s: symbol_phases says both' % ptype)
    ends = np.array([burst_span(side, e)[1] for e in bursts])
    check(np.all(ends[:-1] < starts[1:]) and np.all(starts - starts[0] - np.arange(len(starts)) * 100000 == np.array(phases) - phases[0]),
          '%s: no overlaps, a burst every 5 ms (100000 samples) plus its half symbol' % ptype)
    check(starts[0] >= 0.020 * fs and len(iq) - ends[-1] >= 0.012 * fs,
          '%s: at least 20 ms of noise before burst 0 and 12 ms after the last' % ptype)
    clks = [e['clk'] for e in bursts]
    check(all(c % 4 == 0 for c in clks) and np.all(np.diff(clks) == 16) and clks[0] == 0x0123400,
          '%s: master-slot clocks, 16 ticks (8 slots) a burst from 0x0123400' % ptype)
    offs = np.array([carrier_hz(iq, side, e) for e in bursts])
    check(np.max(np.abs(offs)) < 8e3 and all(e['offset_hz'] == 0 for e in bursts),
          '%s: carrier at 0 Hz on channel 39 (worst %.1f kHz)' % (ptype, np.max(np.abs(offs)) / 1e3))
    snr = np.array([burst_snr(iq, side, e) for e in bursts])
    want = np.array([e['snr_db'] for e in bursts])
    check(np.max(np.abs(snr - want)) < 0.5 and set(want) == {12.0, 20.0}
          and list(want) == [12.0] * 12 + [20.0] * 12,
          '%s: level against the noise matches snr_db in both blocks (worst %.2f dB)'
          % (ptype, np.max(np.abs(snr - want))))
    nm = noise_per_mhz(iq, side)
    check(abs(10 * np.log10(nm / side['noise_1mhz'])) < 0.1
          and abs(side['noise_1mhz'] - bt_synth.AMPLITUDE ** 2 / 100) < 1e-12,
          '%s: noise in 1 MHz is NOISE_1MHZ (%.3f dB off)'
          % (ptype, 10 * np.log10(nm / side['noise_1mhz'])))

print('carrier on another channel, and the carrier offset')
iq, side = small(channel=43, cfo_hz=30e3)
offs = np.array([carrier_hz(iq, side, e) for e in side['bursts']])
check(np.max(np.abs(offs - 4.03e6)) < 10e3 and side['bursts'][0]['offset_hz'] == 4.03e6
      and side['bursts'][0]['channel_mhz'] == 2445.0 and side['bursts'][0]['channel'] == 43,
      'channel 43 with +30 kHz is at +4.03 MHz (worst error %.1f kHz)'
      % (np.max(np.abs(offs - 4.03e6)) / 1e3))
iq, side = small(channel=39, cfo_hz=-30e3)
offs = np.array([carrier_hz(iq, side, e) for e in side['bursts']])
check(np.max(np.abs(offs + 30e3)) < 10e3 and side['bursts'][0]['offset_hz'] == -30e3,
      'channel 39 with -30 kHz is at -30 kHz (worst error %.1f kHz)'
      % (np.max(np.abs(offs + 30e3)) / 1e3))
iq, side = small(channel=38)
offs = np.array([carrier_hz(iq, side, e) for e in side['bursts']])
check(np.max(np.abs(offs + 1e6)) < 10e3, 'channel 38 is at -1 MHz')
errs = start_errors(iq, side, side['bursts'][:8])
check(all(a < t for a, t in errs), 'channel 38: the correlation finds every start too')

print('40 MS/s: the same noise per MHz, and the phases')
iq20, side20 = small()
iq40, side40 = small(fs=40e6)
check(abs(10 * np.log10(noise_per_mhz(iq40, side40) / noise_per_mhz(iq20, side20))) < 0.1
      and side40['noise_1mhz'] == side20['noise_1mhz'] and side40['slot_samples'] == 25000,
      'noise per MHz is the same at 20 and 40 MS/s')
check(side40['symbol_phases'] == [0, 20]
      and [e['symbol_phase'] for e in side40['bursts'][:4]] == [0, 20, 0, 20]
      and [e['start_sample'] - side40['bursts'][0]['start_sample'] - k * 200000 for k, e in enumerate(side40['bursts'][:3])] == [0, 20, 0],
      '40 MS/s: phases 0 and 20, a burst every 5 ms (200000 samples)')
snr = np.array([burst_snr(iq40, side40, e) for e in side40['bursts']])
want = np.array([e['snr_db'] for e in side40['bursts']])
check(np.max(np.abs(snr - want)) < 0.5, '40 MS/s: level matches snr_db (worst %.2f dB)'
      % np.max(np.abs(snr - want)))
errs = start_errors(iq40, side40, side40['bursts'][:8])
check(all(a < t for a, t in errs), '40 MS/s: starts match (worst %.2f samples)'
      % max(a for a, _ in errs))

# --- the spur -----------------------------------------------------------------

print('the spur, measured as the sidecar defines it')
for db in (15, 25, 35):
    iq, side = small(spur='cw', spur_db=db)
    sp = side['spur']
    fs = side['sample_rate']
    lit = np.mean([spur_db_literal(iq, 1000 + 1024 * j) for j in range(8)])
    avg = spur_db_averaged(iq, 0, 400)
    check(abs(lit - db) < 1.0 and abs(avg - db) < 0.3,
          'cw %d dB: single FFTs say %.2f dB (mean of 8 dB values), the mean of 400 says %.2f dB' % (db, lit, avg))
    avg_tail = spur_db_averaged(iq, int(len(iq) - 200 * 1024), 200)
    check(abs(avg_tail - db) < 0.3, 'cw %d dB: still there in the last 12 ms (%.2f dB)' % (db, avg_tail))
    f0 = tone_hz(iq, 0, fs)
    check(abs(f0) < 2 and sp['tone_hz_start'] == 0 and sp['tone_hz_end'] == 0,
          'cw %d dB: at 0 Hz (%.1f Hz)' % (db, f0))
    sigma2 = bt_synth.AMPLITUDE ** 2 / 100 * fs / 1e6
    check(abs(sp['tone_power'] - sigma2 * 10 ** (db / 10) / 1024) < 1e-12
          and sp['bin_fft_size'] == 1024 and sp['window'] == 'rectangular'
          and sp['db_over_noise_in_one_bin'] == db and sp['kind'] == 'cw' and sp['definition'],
          'cw %d dB: the spur block states the definition and the power' % db)
    # tone power from the samples: the mean over the lead-in, squared
    tone = abs(iq[:400000].astype(np.complex128).mean()) ** 2
    check(abs(10 * np.log10(tone / sp['tone_power'])) < 0.2,
          'cw %d dB: the mean of the lead-in has the stated tone power (%.2f dB off)'
          % (db, 10 * np.log10(tone / sp['tone_power'])))

iq, side = small(spur='cw', spur_db=25)
snr = np.array([burst_snr(iq, side, e) for e in side['bursts']])
tone_p = side['spur']['tone_power']
fs = side['sample_rate']
sps = 20
resid = []
for e in side['bursts']:
    a, b = burst_span(side, e)
    core = mean_power(iq, a + 8 * sps, b - 8 * sps)
    nm = noise_per_mhz(iq, side)
    resid.append(10 * np.log10((core - nm * fs / 1e6 - tone_p) / nm) - e['snr_db'])
check(np.max(np.abs(resid)) < 0.6,
      'with a spur, the burst level is still snr_db once the tone is taken off (worst %.2f dB)'
      % np.max(np.abs(resid)))

iq, side = small(spur='none')
check(abs(spur_db_averaged(iq, 0, 400)) < 0.2 or spur_db_averaged(iq, 0, 400) < -5,
      'no spur: no tone in bin 0 (%.2f)' % spur_db_averaged(iq, 0, 400))
sp = side['spur']
check(sp['kind'] == 'none' and sp['tone_power'] == 0.0 and sp['tone_amplitude'] == 0.0
      and sp['tone_phase_rad'] is None and sp['tone_hz_start'] == 0.0 and sp['tone_hz_end'] == 0.0
      and isinstance(sp['tone_hz_end'], float) and 'tone is at DC' not in sp['definition'],
      'no spur: the block says none, with no tone text, float frequencies and no phase')

print('the drifting spur')
iq, side = small(spur='cw', spur_db=35, spur_drift_hz=1000.0)
fs = side['sample_rate']
dur = len(iq) / fs
n = 2 ** 17
f_start = tone_hz(iq, 0, fs)
f_end = tone_hz(iq, len(iq) - n, fs)
want_start = 1000 * (n / 2 / fs) / dur
want_end = 1000 * (len(iq) - n / 2) / len(iq)
check(abs(f_start - want_start) < 3 and abs(f_end - want_end) < 3
      and side['spur']['tone_hz_start'] == 0 and side['spur']['tone_hz_end'] == 1000.0,
      'drift 1000 Hz: %.0f Hz at the start (want %.0f), %.0f Hz at the end (want %.0f)'
      % (f_start, want_start, f_end, want_end))
check(abs(spur_db_averaged(iq, 0, 400) - 35) < 0.5,
      'drift: the level at the start is 35 dB (%.2f)' % spur_db_averaged(iq, 0, 400))

# --- the controls -------------------------------------------------------------

print('controls: the same bursts on channels 38, 40 and 43')
iq39, s39 = small()
fs = 20e6
b39 = None
for ch in (38, 40, 43):
    iqc, sc = small(channel=ch)
    same = all(
        all(e[k] == f[k] for k in ('clk', 'payload_hex', 'snr_db', 'timing_frac', 'start_sample',
                                    'symbol_phase', 'ptype', 'seqn'))
        for e, f in zip(s39['bursts'], sc['bursts']))
    check(same and len(iq39) == len(iqc) and sc['seed'] == s39['seed']
          and sc['bursts'][0]['channel'] == ch,
          'channel %d: payloads, clocks, SNR blocks, timing and starts equal channel 39\'s' % ch)
    # Outside the bursts the two files are the same samples, noise included.
    mask = np.ones(len(iq39), bool)
    for e in s39['bursts']:
        a, b = burst_span(s39, e)
        mask[a - 100:b + 100] = False
    check(np.array_equal(iq39[mask], iqc[mask]),
          'channel %d: everything outside the bursts is identical to channel 39\'s' % ch)
    # Inside: x_ch = N + b e^{j theta}, x_39 = N + b, so b follows from the difference.
    off = (2402 + ch - 2441) * 1e6
    worst = 0
    bs = []
    for e in s39['bursts'][:6]:
        a, b = burst_span(s39, e)
        a += 200
        b -= 200
        n_ = np.arange(a, b)
        rot = np.exp(2j * np.pi * (off / fs * n_ % 1))
        den = rot - 1
        ok = np.abs(den) > 0.25
        with np.errstate(invalid='ignore', divide='ignore'):
            bb = (iqc[a:b].astype(np.complex128) - iq39[a:b]) / den
        amp = np.sqrt(bt_synth.AMPLITUDE ** 2 / 100 * 10 ** (e['snr_db'] / 10))
        worst = max(worst, np.max(np.abs(np.abs(bb[ok]) - amp)))
        bs.append(bb[ok][:50])
    check(worst < 2e-4, 'channel %d: the burst is channel 39\'s moved, constant at its amplitude '
          '(worst %.1e)' % (ch, worst))


# --- pairing ------------------------------------------------------------------

print('pairing: a spur file is the clean file plus the tone; a cfo file is the cfo0 file moved')


def tone_ref(side, n):
    """The tone of a sidecar's spur block over samples 0..n, rebuilt from its
    amplitude, phase and drift: amplitude * exp(j (phase + pi * rate * t**2))."""
    sp = side['spur']
    fs = side['sample_rate']
    rate = sp['tone_hz_end'] / (side['total_samples'] / fs)
    t = np.arange(n) / fs
    cyc = 0.5 * rate * t * t
    return sp['tone_amplitude'] * np.exp(1j * (sp['tone_phase_rad'] + 2 * np.pi * (cyc - np.floor(cyc))))


def tone_fit(d, fs):
    """Fit phase = p0 + 2 pi (f0 t + r t**2 / 2) to the unwrapped phase of the
    complex samples ``d``, taken every 97th sample, and return (f0, r): the
    tone's frequency at t = 0 in Hz and its drift in Hz/s. A noiseless tone, so
    this is good to millihertz."""
    idx = np.arange(0, len(d), 97)
    t = idx / fs
    ph = np.unwrap(np.angle(d[idx]))
    c = np.polyfit(t, ph, 2)
    return c[1] / (2 * np.pi), c[0] / np.pi


for drift in (0.0, 1000.0):
    clean_iq, clean_side = small()
    spur_iq, spur_side = small(spur='cw', spur_db=25, spur_drift_hz=drift)
    d = spur_iq.astype(np.complex128) - clean_iq
    err = np.max(np.abs(d - tone_ref(spur_side, len(d))))
    check(len(spur_iq) == len(clean_iq) and err < 1e-6
          and [e['timing_frac'] for e in spur_side['bursts']] == [e['timing_frac'] for e in clean_side['bursts']],
          'drift %g Hz: spur file minus clean file is exactly the tone, bursts and noise alike '
          '(worst %.1e)' % (drift, err))
    f0, r = tone_fit(d, 20e6)
    want_r = drift / (len(d) / 20e6)
    check(abs(f0) < 0.005 and abs(r - want_r) <= 0.005 * max(want_r, 1),
          'drift %g Hz: tone fitted from its phase starts at %.4f Hz and drifts %.2f Hz/s '
          '(want 0 and %.2f)' % (drift, f0, r, want_r))

# A carrier offset moves the bursts and never the tone.
_, base_side = small(snr_steps=[16], cfo_hz=30e3)
iq_c, side_c = small(snr_steps=[16], cfo_hz=30e3)
iq_cs, side_cs = small(snr_steps=[16], cfo_hz=30e3, spur='cw', spur_db=25, spur_drift_hz=1000.0)
d = iq_cs.astype(np.complex128) - iq_c
err = np.max(np.abs(d - tone_ref(side_cs, len(d))))
f0, r = tone_fit(d, 20e6)
check(err < 1e-6 and abs(f0) < 0.005 and side_cs['spur']['tone_hz_start'] == 0.0,
      'cfo with a spur: the tone is still at 0 Hz and untouched by the +30 kHz (worst %.1e, f0 %.4f Hz)'
      % (err, f0))

iq0, s0 = small(snr_steps=[16], cfo_hz=0.0)
moved = {}
for cfo in (30e3, -30e3):
    iqc, sc = small(snr_steps=[16], cfo_hz=cfo)
    mask = np.ones(len(iq0), bool)
    for e in s0['bursts']:
        a, b = burst_span(s0, e)
        mask[a - 100:b + 100] = False
    same = np.array_equal(iq0[mask], iqc[mask]) and all(
        e['payload_hex'] == f['payload_hex'] and e['timing_frac'] == f['timing_frac']
        and e['start_sample'] == f['start_sample'] for e, f in zip(s0['bursts'], sc['bursts']))
    amp = np.sqrt(bt_synth.AMPLITUDE ** 2 / 100 * 10 ** 1.6)
    worst = 0
    pieces = []
    for e in s0['bursts'][:6]:
        a, b = burst_span(s0, e)
        n_ = np.arange(a + 200, b - 200)
        rot = np.exp(2j * np.pi * (cfo / 20e6 * n_ % 1))
        den = rot - 1
        ok = np.abs(den) > 0.25
        with np.errstate(invalid='ignore', divide='ignore'):
            bb = (iqc[a + 200:b - 200].astype(np.complex128) - iq0[a + 200:b - 200]) / den
        worst = max(worst, np.max(np.abs(np.abs(bb[ok]) - amp)))
        pieces.append((e['burst_index'], n_[ok], bb[ok]))
    moved[cfo] = pieces
    check(same and worst < 1e-3 and sc['bursts'][0]['offset_hz'] == cfo,
          'cfo %+g kHz: the cfo0 file\'s bursts shifted by the offset, all else identical '
          '(worst amplitude error %.1e)' % (cfo / 1e3, worst))
agree = 0.0
for (k, n1, b1), (k2, n2, b2) in zip(moved[30e3], moved[-30e3]):
    common, i1, i2 = np.intersect1d(n1, n2, return_indices=True)
    agree = max(agree, np.max(np.abs(b1[i1] - b2[i2])))
check(agree < 1e-3, '+30 kHz and -30 kHz files hold the same burst underneath (worst %.1e)' % agree)

print('blocks: a small block, so the noise and the tone span many')
old = dc.BLOCK
dc.BLOCK = 100003
try:
    blk_clean, bc_side = dc.synthesise_dc(ptype='DH5', bursts=12, spacing_ms=5.0, snr_steps=[12, 20],
                                          spur='none', seed=11)
    blk_spur, bs_side = dc.synthesise_dc(ptype='DH5', bursts=12, spacing_ms=5.0, snr_steps=[12, 20],
                                         spur='cw', spur_db=25, spur_drift_hz=1000.0, seed=11)
finally:
    dc.BLOCK = old
B = 100003
nblocks = len(blk_clean) // B + 1
lead_blocks = [blk_clean[i * B:(i + 1) * B].astype(np.complex128) for i in range(5)]
worst_c = 0.0
for i in range(5):
    for j in range(i + 1, 5):
        c = abs(np.vdot(lead_blocks[i], lead_blocks[j])) / (np.linalg.norm(lead_blocks[i])
                                                          * np.linalg.norm(lead_blocks[j]))
        worst_c = max(worst_c, c)
check(nblocks > 15 and worst_c < 0.02,
      'noise: %d blocks, and the first five (burst-free) are all different (worst correlation %.4f)'
      % (nblocks, worst_c))
d = blk_spur.astype(np.complex128) - blk_clean
err = np.max(np.abs(d - tone_ref(bs_side, len(d))))
seam = [np.angle(d[k * B] * np.conj(d[k * B - 1])) for k in range(1, nblocks - 1)]
step = np.angle(d[1000] * np.conj(d[999]))
check(err < 1e-6 and max(abs(x - step) for x in seam) < 0.01,
      'tone over %d block seams: continuous phase and drift, equal to the tone (worst %.1e; '
      'seam steps within %.1e rad of the rest)' % (nblocks - 2, err, max(abs(x - step) for x in seam)))
f0, r = tone_fit(d, 20e6)
want_r = 1000.0 / (len(d) / 20e6)
check(abs(f0) < 0.005 and abs(r - want_r) < 0.005 * want_r,
      'tone over the blocks: %.4f Hz at the start, %.2f Hz/s drift (want 0, %.2f)' % (f0, r, want_r))

# --- determinism, the sets, the sidecar ---------------------------------------

print('determinism, sets, sidecar')
with tempfile.TemporaryDirectory(dir='/tmp') as tmp:
    digests = []
    for run in range(2):
        out = os.path.join(tmp, 'r%d' % run)
        subprocess.run([PY, '-B', SCRIPT, 'det', '--bursts', '6', '--spacing-ms', '5',
                        '--snr-steps', '12,20', '--spur', 'cw', '--spur-db', '25',
                        '--spur-drift-hz', '1000', '--seed', '9', '--out', out],
                       check=True, capture_output=True)
        digests.append([hashlib.sha256(open(os.path.join(out, f), 'rb').read()).hexdigest()
                        for f in ('synth_det.cf32', 'synth_det.json')])
    check(digests[0] == digests[1], 'the same command twice: identical cf32 and json bytes')
    out = os.path.join(tmp, 'r0')
    raw = np.fromfile(os.path.join(out, 'synth_det.cf32'), dtype='<c8')
    side = json.load(open(os.path.join(out, 'synth_det.json')))
    ref_iq, ref_side = dc.synthesise_dc(ptype='DH5', channel=39, bursts=6, spacing_ms=5.0,
                                       snr_steps=[12, 20], spur='cw', spur_db=25,
                                       spur_drift_hz=1000.0, seed=9)
    check(np.array_equal(raw, ref_iq) and len(raw) == side['total_samples'],
          'the file written is what synthesise_dc returns, in the same blocks')
    # Bursts straddling the edge of a block are cut and joined: with a block of
    # 100003 samples nearly every burst does. Their starts and levels must
    # still be right, though the noise is a different draw.
    old = dc.BLOCK
    dc.BLOCK = 100003
    try:
        odd_iq, odd_side = dc.synthesise_dc(ptype='DH5', channel=39, bursts=24, spacing_ms=5.0,
                                           snr_steps=[12, 20], spur='none', seed=7)
    finally:
        dc.BLOCK = old
    errs = start_errors(odd_iq, odd_side, odd_side['bursts'][:8])
    err = max(a for a, _ in errs)
    snr = np.array([burst_snr(odd_iq, odd_side, e) for e in odd_side['bursts']])
    want = np.array([e['snr_db'] for e in odd_side['bursts']])
    check(all(a < t for a, t in errs) and np.max(np.abs(snr - want)) < 0.5,
          'bursts cut by a block edge (block 100003) are whole: starts %.2f, level %.2f dB'
          % (err, np.max(np.abs(snr - want))))

need_top = ['generator', 'generator_commit', 'lap', 'uap', 'sample_rate', 'center_mhz', 'modulation',
            'slot_samples', 'start_sample_meaning', 'clk_convention', 'snr_bw_hz', 'noise_1mhz',
            'bursts', 'air_bits_omitted', 'symbol_phases', 'snr_steps', 'spur', 'centre_channel',
            'centre_channel_note', 'cfo_hz', 'channel', 'channel_mhz', 'bt_channel', 'ptype',
            'spacing_ms', 'seed', 'total_samples', 'clk', 'snr_db', 'timing_frac', 'symbol_phase',
            'per_burst_keys', 'start_offset']
need_burst = ['start_sample', 'timing_frac', 'symbol_phase', 'clk', 'ptype', 'channel',
              'channel_mhz', 'snr_db', 'cfo_hz', 'burst_index', 'offset_hz', 'payload_hex',
              'lt_addr', 'header18']
need_spur = ['kind', 'db_over_noise_in_one_bin', 'bin_fft_size', 'window', 'tone_hz_start',
             'tone_hz_end', 'tone_power', 'definition', 'tone_amplitude', 'tone_phase_rad',
             'raw_bin0_over_mean_noise_bin_db', 'tone_over_noise_1mhz_db',
             'tone_over_burst_db_at_each_step']
check(all(k in side for k in need_top), 'sidecar: every top-level key (missing %s)'
      % [k for k in need_top if k not in side])
check(all(k in side['bursts'][0] for k in need_burst), 'sidecar: every per-burst key (missing %s)'
      % [k for k in need_burst if k not in side['bursts'][0]])
check(all(k in side['spur'] for k in need_spur), 'sidecar: every spur key (missing %s)'
      % [k for k in need_spur if k not in side['spur']])
check(side['air_bits_omitted'] is True and 'air_bits' not in side['bursts'][0],
      'sidecar: air_bits left out and said so')
check(side['centre_channel'] == 39 and '0 Hz' in side['centre_channel_note']
      and 'DC block' not in side['centre_channel_note']
      and [e['burst_index'] for e in side['bursts']] == list(range(6)),
      'sidecar: centre channel 39 and burst_index')
sp = side['spur']
check(side['timing_frac'] is None and side['symbol_phase'] is None and side['snr_db'] is None
      and all(k in side['per_burst_keys'] for k in ('timing_frac', 'symbol_phase', 'snr_db',
                                                    'start_sample', 'channel'))
      and all(k in side['bursts'][0] for k in side['per_burst_keys'])
      and isinstance(side['start_offset'], int) and side['start_offset'] == side['bursts'][0]['start_sample'] % side['slot_samples'],
      'sidecar: per-burst values are null at the top and listed in per_burst_keys; start_offset is an int')
check(abs(sp['tone_over_noise_1mhz_db'] - 10 * np.log10(sp['tone_power'] / side['noise_1mhz'])) < 1e-9
      and len(sp['tone_over_burst_db_at_each_step']) == 2
      and abs(sp['tone_over_burst_db_at_each_step'][0]
              - (10 * np.log10(sp['tone_power'] / side['noise_1mhz']) - side['snr_steps'][0])) < 1e-9
      and abs(sp['raw_bin0_over_mean_noise_bin_db'] - 10 * np.log10(10 ** 2.5 + 1)) < 1e-9
      and 'excluding the noise in the tone bin' in sp['definition'] and '3 dB stronger' in sp['definition'],
      'sidecar: the spur block gives the level over 1 MHz of noise and over each step, the raw reading, '
      'the definition with its exclusion and the 40 MS/s note')
check(side['symbol_phase_note'].count('symbol_phases') and '6250' in side['symbol_phase_note'],
      'sidecar: the symbol-phase note names symbol_phases and the 6240/6250 offsets')
check(side['generator_commit'] == bt_synth.commit(), 'sidecar: generator_commit is the repository\'s')

names = dc.set_runs('dc')
nn = [n for n, _ in names]
check(len(nn) == 26 and len(set(nn)) == 26, '--set dc is 26 distinct files')
exp = []
for p in ('dh5', 'dh1'):
    exp += ['dc_%s_%s' % (p, s) for s in ('ch39_clean', 'ch39_cw15', 'ch39_cw25', 'ch39_cw35',
                                          'ch39_drift15', 'ch39_drift25', 'ch39_drift35')]
for p in ('dh5', 'dh1'):
    exp += ['dc_%s_%s' % (p, s) for s in ('ch38_clean', 'ch40_clean', 'ch43_clean')]
for p in ('dh5', 'dh1'):
    exp += ['dc_%s_%s' % (p, s) for s in ('ch39_snr16_cfo0', 'ch39_snr16_cfopl30k',
                                          'ch39_snr16_cfomi30k')]
check(sorted(nn) == sorted(exp), '--set dc has exactly the names the task lists')
check(all(n.startswith('dc_dh5_') for n in nn[:7]) and all(n.startswith('dc_dh1_') for n in nn[7:14])
      and nn[14:17] == ['dc_dh5_ch38_clean', 'dc_dh5_ch40_clean', 'dc_dh5_ch43_clean']
      and nn[20:23] == ['dc_dh5_ch39_snr16_cfo0', 'dc_dh5_ch39_snr16_cfopl30k',
                        'dc_dh5_ch39_snr16_cfomi30k']
      and all('cfo' in n for n in nn[20:26]) and all('dh1' in n for n in nn[7:14]),
      '--set dc: groups in order, DH5 first then DH1 in each')
spec = dict(names)
# The table, written out here and not derived from the generator's own: name ->
# (ptype, channel, spur, spur_db, drift, cfo, bursts, seed).
want = {}
for ptype, p, seed, cseed in (('DH5', 'dh5', 4001, 4021), ('DH1', 'dh1', 4008, 4024)):
    want['dc_%s_ch39_clean' % p] = (ptype, 39, 'none', 25, 0.0, 0.0, 800, seed)
    for db in (15, 25, 35):
        want['dc_%s_ch39_cw%d' % (p, db)] = (ptype, 39, 'cw', db, 0.0, 0.0, 800, seed)
        want['dc_%s_ch39_drift%d' % (p, db)] = (ptype, 39, 'cw', db, 1000.0, 0.0, 800, seed)
    for ch in (38, 40, 43):
        want['dc_%s_ch%d_clean' % (p, ch)] = (ptype, ch, 'none', 25, 0.0, 0.0, 800, seed)
    for tag, cfo in (('cfo0', 0.0), ('cfopl30k', 30e3), ('cfomi30k', -30e3)):
        want['dc_%s_ch39_snr16_%s' % (p, tag)] = (ptype, 39, 'none', 25, 0.0, cfo, 200, cseed)
got = {n: (s_['ptype'], s_['channel'], s_['spur'], s_['spur_db'], s_['spur_drift_hz'],
           s_['cfo_hz'], s_['bursts'], s_['seed']) for n, s_ in names}
bad = [n for n in want if got.get(n) != want[n]]
check(got == want, '--set dc: every file\'s type, channel, spur kind/level/drift, cfo, burst count '
      'and seed as designed (differs: %s)' % bad)
check([spec[n]['channel'] for n in nn[14:20]] == [38, 40, 43, 38, 40, 43]
      and [spec[n]['ptype'] for n in nn[14:20]] == ['DH5'] * 3 + ['DH1'] * 3,
      '--set dc: the controls are on channels 38, 40, 43 for both types')
check(all(spec[n]['bursts'] == 800 and spec[n]['snr_steps'] == [8, 12, 16, 20]
          for n in nn if 'cfo' not in n)
      and all(spec[n]['bursts'] == 200 and spec[n]['snr_steps'] == [16] for n in nn if 'cfo' in n)
      and all(spec[n].get('fs', 20e6) == 20e6 for n in nn),
      '--set dc: SNR steps and sample rate as listed')
n40 = dc.set_runs('dc40')
check([n for n, _ in n40] == ['dc40_dh5_ch39_snr12_clean', 'dc40_dh5_ch39_snr20_clean',
                              'dc40_dh5_ch39_snr12_cw25', 'dc40_dh5_ch39_snr20_cw25']
      and all(s['fs'] == 40e6 and s['bursts'] == 800 and s['ptype'] == 'DH5' for _, s in n40)
      and [s['snr_steps'] for _, s in n40] == [[12], [20], [12], [20]]
      and [s['spur'] for _, s in n40] == ['none', 'none', 'cw', 'cw']
      and all(s['spur_db'] == 25 and s['spur_drift_hz'] == 0.0 and s['cfo_hz'] == 0.0
              and s['channel'] == 39 and s['seed'] == 4027 for _, s in n40),
      '--set dc40 is the four 40 MS/s files')
try:
    dc.set_runs('nonsense')
    bad = False
except ValueError:
    bad = True
check(bad, 'an unknown set is a ValueError')
r = subprocess.run([PY, '-B', SCRIPT, '--set', 'nonsense'], capture_output=True)
check(r.returncode != 0, 'an unknown set exits non-zero on the command line (%d)' % r.returncode)
r = subprocess.run([PY, '-B', SCRIPT, '--set', 'dc', '--bursts', '5'], capture_output=True)
check(r.returncode != 0, '--set with a stray option exits non-zero')

print('bad arguments')
for what, kw in (('a spacing that is not whole slots', dict(spacing_ms=5.1)),
                 ('a spacing too short for a DH5', dict(spacing_ms=2.5)),
                 ('an odd number of slots', dict(spacing_ms=4.375)),
                 ('drift with no spur', dict(spur='none', spur_drift_hz=1000.0)),
                 ('a channel out of the window', dict(channel=60)),
                 ('a channel that is not one', dict(channel=79))):
    try:
        small(**kw)
        ok = False
    except ValueError:
        ok = True
    check(ok, 'refused: %s' % what)

print()
if failures:
    print('RESULT: FAIL (%d)' % len(failures))
    sys.exit(1)
print('RESULT: PASS')
