"""A receiver's own frequency and clock error put on a delivered synthetic file (bluey-ox-walker, 2026-10-17).

bluey's carrier census reads about -58 kHz on one cluster of real bursts and cannot tell a transmitter's offset from a
term common to the receiver.  The synthetic files carry every offset on the transmitter side (per burst ``cfo_hz``).
This tool takes a delivered ``.cf32`` and its sidecar and makes the file a receiver with a wrong reference would have
recorded of the same air, so that an estimator and a detector can be graded on a receiver-side offset with truth.

**The model.**  The receiver's reference is ``rx_ppm`` parts per million FAST (``+`` = fast, the convention of
``drift_ppm_signed`` in the slotb files).  One reference drives the LO and the sample clock, so both are wrong by the same
fraction ``eps = rx_ppm * 1e-6``:

* its sample ``m`` is taken at true time ``m / (fs (1 + eps))`` (it thinks ``m / fs``), so a burst that starts at true
  sample position ``x`` is at receiver position ``x (1 + eps)``;
* its LO is ``fc (1 + eps)``, and it measures frequency against its own clock, so a carrier at RF ``f`` reads, relative
  to the nominal channel frequency ``f_ch`` it mixes with, ``(f - fc (1 + eps) + rx_offset_hz) / (1 + eps) - (f_ch - fc)``,
  which is ``cfo_hz + rx_offset_hz - eps f_ch`` to first order: **+23.8 ppm reads about -58.1 kHz at 2441 MHz**.

``rx_offset_hz`` is a frequency error NOT tied to the clock (an IF or tuning error, a wrong centre frequency in the
capture's metadata): a pure rotation.  With ``rx_ppm`` 0 the tool multiplies the file by ``exp(+j 2 pi rx_offset_hz n /
fs)`` with the absolute sample index ``n`` and nothing else: the operation bluey's ``oracle_shift`` performs.

**How.**  Output sample ``m`` is the input at position ``m / (1 + eps)``, by a windowed-sinc interpolator (``TAPS`` taps,
Kaiser beta ``BETA``, cutoff ``CUTOFF`` of the sample rate, ``PHASES`` quantised fractional positions, every phase's taps
summing to 1); then ``exp(+j 2 pi (rx_offset_hz - eps fc) t_m)`` with ``t_m = m / (fs (1 + eps))`` the true time.  The
interpolator keeps the band to +-``CUTOFF`` fs (18.8 MHz at 40 MS/s, 9.4 at 20) flat and rolls the white noise off above
it.  With ``rx_ppm`` 0 there is no interpolation.  An optional **DC spur** (``dc_spur_db`` over the noise in 1 MHz, a
constant added last) is the receiver's own LO leak: it stays at 0 Hz whatever the offset.

**The truth.**  The sidecar is the source's with: per burst ``start_sample`` / ``timing_frac`` (their sum scaled by ``1 +
eps``), every other per-burst and top-level key ending in ``_sample`` scaled (receiver samples), ``symbol_phase``
recomputed, and **``cfo_observed_hz``** added per burst (exact, the formula above).  ``cfo_hz`` stays the TRANSMITTER's
offset; a grader of a processed file must read ``cfo_observed_hz``.  Keys ending in ``_us`` stay in TRUE time, so under
``rx_ppm`` ``start_sample / fs`` is no longer ``start_us``.  ``n_samples`` is the new length and ``slot_samples`` the
receiver samples per slot; a ``receiver`` block records the settings, the source and its hash.  Only sidecars of
``bt_synth_slotb2.py`` and ``bt_synth_endlen.py`` are accepted: their keys are the ones this rewrite was tested on.

    python -B scripts/bt_rx_offset.py SRC.cf32 --out DIR --rx-ppm 23.8 [--rx-offset-hz HZ] [--dc-spur-db DB]
    python -B scripts/bt_rx_offset.py --manifest DIR      # manifest.json and SHA256SUMS.txt over DIR's files
"""
import argparse
import hashlib
import json
import math
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import bt_synth  # noqa: E402

TAPS = 64
BETA = 8.6
CUTOFF = 0.47
PHASES = 4096
BLOCK = 1 << 16                     # output samples per interpolation block
ACCEPTED = ('SDR scripts/bt_synth_slotb2.py', 'SDR scripts/bt_synth_endlen.py')


def eps_of(rx_ppm):
    return float(rx_ppm) * 1e-6


def observed_cfo_hz(cfo_hz, channel_mhz, center_mhz, rx_ppm=0.0, rx_offset_hz=0.0):
    """The carrier offset a receiver ``rx_ppm`` fast (plus ``rx_offset_hz``) reads against the nominal channel."""
    e = eps_of(rx_ppm)
    f_ch, fc = channel_mhz * 1e6, center_mhz * 1e6
    return ((f_ch + cfo_hz) - fc * (1 + e) + rx_offset_hz) / (1 + e) - (f_ch - fc)


def interp_table(taps=TAPS, phases=PHASES, beta=BETA, cutoff=CUTOFF):
    """``h[p, k]``: the taps for fractional position ``p / phases``; output = sum_k h[p, k] x[n0 - taps/2 + 1 + k]."""
    p = np.arange(phases + 1)[:, None] / phases
    d = np.arange(taps)[None, :] - (taps // 2 - 1) - p              # tap position minus the wanted position
    w = np.i0(beta * np.sqrt(np.clip(1 - (d / (taps / 2)) ** 2, 0, None))) / np.i0(beta)
    h = 2 * cutoff * np.sinc(2 * cutoff * d) * w
    return (h / h.sum(axis=1, keepdims=True)).astype(np.float32)


def out_length(n_in, rx_ppm):
    return int(math.floor((n_in - 1) * (1 + eps_of(rx_ppm)))) + 1


def process(src, rx_ppm=0.0, rx_offset_hz=0.0, fs=None, fc=None, dc_amp=0.0, block=BLOCK):
    """Yield the output in blocks.  ``src`` is the input array (a memmap is fine)."""
    e = eps_of(rx_ppm)
    n_in = len(src)
    m_total = out_length(n_in, rx_ppm)
    f_rot = rx_offset_hz - e * fc
    table = interp_table() if e != 0.0 else None
    half = TAPS // 2 - 1
    for m0 in range(0, m_total, block):
        m = np.arange(m0, min(m0 + block, m_total), dtype=np.float64)
        if table is None:
            y = np.asarray(src[m0:m0 + len(m)], dtype=np.complex64).copy()
        else:
            x = m / (1 + e)
            n0 = np.floor(x).astype(np.int64)
            ph = np.rint((x - n0) * PHASES).astype(np.int64)
            lo = int(n0[0]) - half
            hi = int(n0[-1]) - half + TAPS + 1
            seg = np.zeros(hi - lo, dtype=np.complex64)
            a, b = max(lo, 0), min(hi, n_in)
            if b > a:
                seg[a - lo:b - lo] = src[a:b]
            idx = (n0 - half - lo)[:, None] + np.arange(TAPS)[None, :]
            y = np.einsum('ij,ij->i', seg[idx], table[ph]).astype(np.complex64)
        if f_rot != 0.0:
            cyc = np.mod(f_rot * m / (fs * (1 + e)), 1.0)
            y = (y * np.exp(2j * np.pi * cyc).astype(np.complex64)).astype(np.complex64)
        if dc_amp:
            y = (y + np.complex64(dc_amp)).astype(np.complex64)
        yield y


def rewrite_sidecar(side, rx_ppm=0.0, rx_offset_hz=0.0, dc_spur_db=None, n_out=None, source=None):
    """The processed file's sidecar (a new dict); refuses a generator whose keys this rewrite was not tested on."""
    if side.get('generator') not in ACCEPTED:
        raise ValueError('rx offset: sidecars of %s only, not %r' % (', '.join(ACCEPTED), side.get('generator')))
    e = eps_of(rx_ppm)
    k = 1 + e
    out = json.loads(json.dumps(side))
    sps = out['sample_rate'] / 1e6
    for b in out['bursts']:
        pos = (b['start_sample'] + b['timing_frac']) * k
        b['start_sample'] = int(math.floor(pos))
        b['timing_frac'] = pos - b['start_sample']
        for key, v in list(b.items()):
            if key.endswith('_sample') and key != 'start_sample' and isinstance(v, (int, float)) and v is not None:
                b[key] = v * k
        if 'symbol_phase' in b:
            b['symbol_phase'] = pos % sps
        ch = b.get('channel_mhz', 2402 + b['channel'])
        b['cfo_observed_hz'] = observed_cfo_hz(b['cfo_hz'], ch, out['center_mhz'], rx_ppm, rx_offset_hz)
    for key, v in list(out.items()):
        if key.endswith('_sample') and isinstance(v, (int, float)) and not isinstance(v, bool):
            out[key] = v * k
    if 'slot_samples' in out:
        out['slot_samples'] = out['slot_samples'] * k
    if n_out is not None:
        out['n_samples'] = n_out
    obs = [b['cfo_observed_hz'] for b in out['bursts']]
    out['receiver'] = dict(
        rx_ppm=float(rx_ppm), rx_ppm_sign='+ = the receiver reference (LO and sample clock) fast', rx_offset_hz=float(rx_offset_hz),
        dc_spur_db=dc_spur_db, dc_spur_note='a constant added after the rotation: the receiver\'s own LO leak, at 0 Hz in receiver '
        'coordinates; dc_spur_db is its power over noise_1mhz' if dc_spur_db is not None else None,
        cfo_observed_formula='(f_ch + cfo_hz - fc (1 + eps) + rx_offset_hz) / (1 + eps) - (f_ch - fc), eps = rx_ppm 1e-6, '
        'f_ch the burst\'s nominal channel, fc the centre',
        cfo_observed_range_hz=[min(obs), max(obs)] if obs else None,
        interpolator=None if e == 0.0 else dict(taps=TAPS, kaiser_beta=BETA, cutoff_of_fs=CUTOFF, phases=PHASES),
        sample_note='start_sample, timing_frac and every *_sample key are RECEIVER samples (scaled by 1 + eps); *_us keys are '
        'TRUE time and unchanged; cfo_hz is the TRANSMITTER\'s offset, cfo_observed_hz what this receiver reads',
        source=source, tool='SDR scripts/bt_rx_offset.py', tool_commit=bt_synth.commit())
    out['cfo_hz_meaning'] = (side.get('cfo_hz_meaning', '') + ' [processed by bt_rx_offset: per burst cfo_observed_hz is what the '
                             'receiver reads; cfo_hz is unchanged, the transmitter\'s]').strip()
    return out


def tag_of(rx_ppm, rx_offset_hz, dc_spur_db):
    f = lambda v: ('m' if v < 0 else 'p') + ('%g' % abs(v)).replace('.', 'p')
    t = []
    if rx_ppm:
        t.append('ppm' + f(rx_ppm))
    if rx_offset_hz:
        t.append('off' + f(rx_offset_hz / 1e3) + 'k')
    if dc_spur_db is not None:
        t.append('dc' + f(dc_spur_db))
    return '_'.join(t) or 'none'


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for blk in iter(lambda: f.read(1 << 26), b''):
            h.update(blk)
    return h.hexdigest()


def write(src_cf32, out_dir, rx_ppm=0.0, rx_offset_hz=0.0, dc_spur_db=None):
    t0 = time.time()
    side = json.load(open(src_cf32[:-len('.cf32')] + '.json'))
    raw = np.memmap(src_cf32, dtype=np.complex64, mode='r')
    n_out = out_length(len(raw), rx_ppm)
    src_sums = os.path.join(os.path.dirname(os.path.abspath(src_cf32)), 'SHA256SUMS.txt')
    src_hash = None
    if os.path.exists(src_sums):
        for line in open(src_sums):
            h, _, name = line.strip().partition('  ')
            if name == os.path.basename(src_cf32):
                src_hash = h
    source = dict(file=os.path.basename(src_cf32), sha256_cf32_from_SHA256SUMS=src_hash, n_samples=len(raw),
                  generator_commit=side.get('generator_commit'))
    new = rewrite_sidecar(side, rx_ppm, rx_offset_hz, dc_spur_db, n_out, source)
    dc_amp = 0.0 if dc_spur_db is None else math.sqrt(side['noise_1mhz'] * 10 ** (dc_spur_db / 10.0))
    stem = os.path.basename(src_cf32)[:-len('.cf32')] + '__rx_' + tag_of(rx_ppm, rx_offset_hz, dc_spur_db)
    os.makedirs(out_dir, exist_ok=True)
    out_cf32 = os.path.join(out_dir, stem + '.cf32')
    n = 0
    with open(out_cf32, 'wb') as f:
        for y in process(raw, rx_ppm, rx_offset_hz, side['sample_rate'], side['center_mhz'] * 1e6, dc_amp):
            y.astype('<c8', copy=False).tofile(f)
            n += len(y)
    assert n == n_out, (n, n_out)
    with open(os.path.join(out_dir, stem + '.json'), 'w') as f:
        json.dump(new, f, separators=(',', ':'))
    print('%s: %d -> %d samples, %.0f s' % (out_cf32, len(raw), n_out, time.time() - t0), flush=True)
    return out_cf32


def write_manifest(out_dir):
    """``manifest.json`` (per file: source, settings, cfo_observed range, size, sha256) and ``SHA256SUMS.txt``."""
    rows, sums = [], []
    for name in sorted(os.listdir(out_dir)):
        if not name.endswith('.cf32'):
            continue
        base = os.path.join(out_dir, name[:-len('.cf32')])
        side = json.load(open(base + '.json'))
        rx = side['receiver']
        row = dict(file=name, sidecar=os.path.basename(base) + '.json', source=rx['source'], rx_ppm=rx['rx_ppm'],
                   rx_offset_hz=rx['rx_offset_hz'], dc_spur_db=rx['dc_spur_db'], cfo_observed_range_hz=rx['cfo_observed_range_hz'],
                   n_samples=side['n_samples'], bytes=os.path.getsize(base + '.cf32'), tool_commit=rx['tool_commit'])
        for ext in ('.cf32', '.json'):
            hx = sha256_of(base + ext)
            sums.append('%s  %s' % (hx, os.path.basename(base) + ext))
            row['sha256' + ext.replace('.', '_')] = hx
        rows.append(row)
    with open(os.path.join(out_dir, 'manifest.json'), 'w') as f:
        json.dump(dict(set='rx_offset', tool='scripts/bt_rx_offset.py', files=rows), f, indent=1)
    with open(os.path.join(out_dir, 'SHA256SUMS.txt'), 'w') as f:
        f.write('\n'.join(sums) + '\n')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('src', nargs='*', help='source .cf32 files (each with its .json beside it)')
    ap.add_argument('--out', help='output folder')
    ap.add_argument('--rx-ppm', type=float, default=0.0, help='receiver reference error, + = FAST (+23.8 reads about -58 kHz at 2441 MHz)')
    ap.add_argument('--rx-offset-hz', type=float, default=0.0, help='a frequency error not tied to the clock (pure rotation)')
    ap.add_argument('--dc-spur-db', type=float, help='a constant at 0 Hz, its power over the noise in 1 MHz')
    ap.add_argument('--manifest', help='write manifest.json and SHA256SUMS.txt over the files in this folder')
    a = ap.parse_args(argv)
    if a.manifest:
        write_manifest(a.manifest)
        return
    if not a.src or not a.out:
        ap.error('give source files and --out, or --manifest DIR')
    for s in a.src:
        write(s, a.out, a.rx_ppm, a.rx_offset_hz, a.dc_spur_db)


if __name__ == '__main__':
    main()
