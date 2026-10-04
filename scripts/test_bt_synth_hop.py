#!/usr/bin/env python3
"""Hold the hopping writer to a recording whose truth is planted.

    python scripts/test_bt_synth_hop.py

``scripts/bt_synth_hop.py`` puts each burst of a Basic Rate train on the
channel a hop function gives it. Nothing about the result is known to the
samples, so this plants a train - 50 DH5 bursts over 25 channels, a carrier
offset, a fraction of a sample, noise at 40 dB in 1 MHz - and checks what is
there against what was asked:

* where the carrier is, burst by burst, measured on the raw samples and not
  after a filter tuned to where it should be, since a filter would leave
  noise for a burst on the wrong channel and noise can land anywhere;
* that every burst decodes from its channel - bit for bit, then through
  libbtbb to the header and payload bytes;
* that a DH5, a DH3 and a DM1 each keep one channel for their whole length;
* that the level is the same on every channel;
* that a seed is a recording, and a channel the tuning cannot hold is refused;
* that the sidecar says all of it, and ``start_sample`` is where the burst is.

The ``hop_fn`` here is a stand-in, not the real kernel. It moves one channel
every packet, so the test can tell a packet that stayed on its channel from
one that was moved at every slot.
"""
import json
import os
import sys

import numpy as np
from scipy.signal import fftconvolve, firwin, lfilter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from scripts import bt_ota_check as ota  # noqa: E402
from scripts import bt_synth, bt_synth_hop as hop  # noqa: E402

FS = 40e6
SPS = int(FS / br.SYMBOL_RATE)                   # 40 samples a symbol
CENTER = 2444.5
failures = []

#: The channels the stand-in hops over, 31-55: a permutation, 7i mod 25, so
#: that neighbouring packets are far apart. All inside the +/- 14.4 MHz the
#: tuning holds at 40 MS/s.
TABLE = [31 + (7 * i) % 25 for i in range(25)]

#: A 0.7 MHz low-pass on the 40 MS/s samples. The 101 taps of ``bt_ota_check``
#: are for 20 MS/s; at twice the rate the transition band must be as narrow in
#: hertz, so twice as many. Its group delay, ``GD`` samples, is what a burst's
#: correlation peak lies after its first sample.
TAPS = firwin(401, 0.7e6, fs=FS)
GD = len(TAPS) // 2

#: Samples taken either side of a burst when it is shifted down and sliced:
#: more than the filter's tail, less than the way to the next burst.
PAD = 3000


def check(ok, what):
    print('  %s %s' % ('ok  ' if ok else 'FAIL', what))
    if not ok:
        failures.append(what)


def standin(clk):
    """Table-driven: the packet's own slot pair, ``clk >> 2``, indexes TABLE.

    Master-slot clocks are multiples of 4 and a burst's clock moves 2 * (slots
    + 1) a time, so ``clk >> 2`` moves 3, 2 and 1 for DH5, DH3 and DM1 - all
    prime to 25, so every packet of a train lands on a new channel and 25 of
    them cover the table. (``clk >> 1`` moves by an even number, and half the
    channels would never be used.)"""
    return TABLE[(clk >> 2) % 25]


def standin_formula(clk):
    """The same idea as a formula: 33 to 52."""
    return 33 + (clk >> 2) % 20


def expected_channels(hop_fn, side):
    """The channels the truth says the train is on: ``hop_fn`` of the clock of
    each packet's first slot, worked out here from the planted ``clk0`` and
    packet type and not read from the sidecar's own channel entries."""
    slots = br.PACKET_TYPES[side['bursts'][0]['ptype']][1]
    return [hop_fn(side['clk'] + 2 * (slots + 1) * k) for k in range(len(side['bursts']))]


def air(entry):
    return np.array([int(c) for c in entry['air_bits']])


def burst_mean_freq(x, first, count):
    """Mean instantaneous frequency of ``x[first:first + count]``, in Hz. It is
    the sum of the one-sample phase steps, so it is the phase at the end minus
    the phase at the start: noise outside the ends hardly enters."""
    step = np.angle(x[first + 1:first + count + 1] * np.conj(x[first:first + count]))
    return step.mean() * FS / (2 * np.pi)


def carrier_mhz(iq, side, entry, bit0, bits):
    """Where a burst's carrier is, in MHz, from ``bits`` of its symbols from
    symbol ``bit0``, measured on the raw samples.

    The mean frequency over a stretch of bits is the carrier plus the bits'
    own mean, which is not zero: it is 2.2 kHz for every bit more ones than
    zeros, and the last 72 bits of a DH5 are 9.6 kHz off. So the same
    stretch of the clean modulator's output for those bits, ``br.gfsk`` with
    no shift, is measured the same way and taken away, as is the planted
    ``cfo_hz``. Over the access code of the planted LAP the correction is
    0.2 kHz; the end of a packet is where it matters."""
    ref, lead = br.gfsk(air(entry), FS, delay=side['timing_frac'])
    mine = burst_mean_freq(iq, entry['start_sample'] + bit0 * SPS, bits * SPS)
    clean = burst_mean_freq(ref, lead + bit0 * SPS, bits * SPS)
    return side['center_mhz'] + (mine - clean - side['cfo_hz']) / 1e6


def demod(iq, side, entry, channel):
    """Shift a burst down from ``channel``, slice it as ``bt_ota_check`` does,
    and find where it starts by correlating its access code.

    Returns ``(start, bits, errors)``; ``start`` is ``None`` if the access code
    is not there. The discriminator is sampled at the symbol centres from the
    correlation peak, the threshold taken from the access code's known bits,
    with no timing recovery - the peak, ``GD`` samples after the burst's first,
    is the only timing there is."""
    a = air(entry)
    lo = max(0, entry['start_sample'] - PAD)
    hi = min(len(iq), entry['start_sample'] + len(a) * SPS + PAD)
    n = np.arange(lo, hi)
    cycles = (hop.channel_mhz(channel) - side['center_mhz']) * 1e6 / FS * n
    bb = lfilter(TAPS, 1, iq[lo:hi] * np.exp(-2j * np.pi * (cycles - np.floor(cycles))))
    d = np.angle(bb[1:] * np.conj(bb[:-1]))
    a72 = a[:72] * 2 - 1
    tmpl = np.repeat(a72, SPS).astype(float)
    tmpl -= tmpl.mean()
    dm = d - np.convolve(d, np.ones(2001) / 2001, 'same')
    c = fftconvolve(dm, tmpl[::-1], 'valid')
    norm = np.sqrt(fftconvolve(dm ** 2, np.ones(len(tmpl)), 'valid')) * np.linalg.norm(tmpl)
    r = c / np.maximum(norm, 1e-12)
    p = int(np.argmax(r))
    if r[p] < 0.5:
        return None, None, len(a)
    centres = p + (np.arange(len(a)) + 0.5) * SPS - 0.5
    f = np.interp(centres, np.arange(len(d)), d)
    _, thr = np.polyfit(a72, f[:72], 1)
    bits = (f > thr).astype(int)
    return lo + p - GD, bits, int((bits != a).sum())


def planted(ptype='DH5', bursts=50, hop_fn=standin, cfo=7e3, snr=40.0, **kw):
    kw.setdefault('timing_frac', 0.37)
    kw.setdefault('seed', 3)
    return hop.synthesise_hop(hop_fn, ptype, bursts=bursts, cfo_hz=cfo, snr_db=snr,
                              fs=FS, center_mhz=CENTER, **kw)


def check_stand_in(side):
    print('\nThe stand-in hop function')
    check(len(side['hop_channels']) >= 12, 'the planted train is on %d channels, 12 needed'
          % len(side['hop_channels']))
    for ptype in ('DH5', 'DH3'):
        slots = br.PACKET_TYPES[ptype][1]
        clks = [0x0123400 + 8 * (slots + 1) * k for k in range(30)]
        check(all(standin(c + 2 * (slots - 1)) != standin(c) for c in clks),
              '%s: the last slot of a packet would be on another channel if it hopped there' % ptype)


def check_carrier(iq, side):
    print('\nThe carrier of every burst, on the raw samples, cfo %.0f Hz taken away' % side['cfo_hz'])
    want = expected_channels(standin, side)
    err = []
    other = []
    for k, (e, ch) in enumerate(zip(side['bursts'], want)):
        mhz = carrier_mhz(iq, side, e, 0, 72)
        err.append(mhz - hop.channel_mhz(ch))
        other.append(mhz - hop.channel_mhz(want[(k + 1) % len(want)]))
    err = np.array(err) * 1e3                                       # kHz
    check(np.abs(err).max() < 5.0, 'all %d within 5 kHz of their channel, worst %.2f kHz'
          % (len(err), np.abs(err).max()))
    check(np.abs(np.array(other)).min() * 1e3 > 500,
          'the same measure puts every burst at least %.0f kHz from the next burst\'s channel'
          % (np.abs(np.array(other)).min() * 1e3))


def check_decode(iq, side, lib):
    print('\nEvery burst, shifted down from its channel and sliced')
    want = expected_channels(standin, side)
    errs, taken, starts = [], [], []
    for e, ch in zip(side['bursts'], want):
        start, bits, nerr = demod(iq, side, e, ch)
        errs.append(nerr)
        starts.append(start)
        if bits is not None and lib is not None:
            taken.append(ota.check_btbb(lib, bits, e, side['uap']))
    check(max(errs) == 0, '%d bursts, bit errors: %d in all, worst burst %d'
          % (len(errs), sum(errs), max(errs)))
    check(lib is not None, 'libbtbb is there')
    check(lib is not None and len(taken) == len(errs) and all(all(t) for t in taken),
          'libbtbb takes the header, the CRC and the payload byte for byte of %d of %d'
          % (sum(all(t) for t in taken), len(errs)))
    return starts


def check_train(ptype):
    iq, side = planted(ptype, bursts=24, cfo=5e3, timing_frac=0.8)
    slots = br.PACKET_TYPES[ptype][1]
    want = expected_channels(standin, side)
    first, last = [], []
    for e, ch in zip(side['bursts'], want):
        nbits = len(e['air_bits'])
        first.append(carrier_mhz(iq, side, e, 0, 72) - hop.channel_mhz(ch))
        last.append(carrier_mhz(iq, side, e, nbits - 72, 72) - hop.channel_mhz(ch))
    first, last = np.array(first) * 1e3, np.array(last) * 1e3
    on_air = max(len(e['air_bits']) for e in side['bursts'])
    check(len(side['hop_channels']) >= 12 and np.abs(first).max() < 5 and np.abs(last).max() < 5,
          '%s, %d us on air in %d slot%s, %d channels: carrier at the start within %.2f kHz of its '
          'channel, at the end within %.2f kHz' % (ptype, on_air, slots, 's' if slots > 1 else '',
                                                   len(side['hop_channels']), np.abs(first).max(),
                                                   np.abs(last).max()))
    check(np.abs(last - first).max() < 5, '%s: end minus start at most %.2f kHz'
          % (ptype, np.abs(last - first).max()))


def check_level(iq, side):
    print('\nThe level, channel by channel')
    per = {}
    for e in side['bursts']:
        s = e['start_sample']
        x = iq[s:s + len(e['air_bits']) * SPS]
        per.setdefault(e['channel'], []).append(np.mean(np.abs(x) ** 2))
    db = {ch: 10 * np.log10(np.mean(v)) for ch, v in per.items()}
    spread = max(db.values()) - min(db.values())
    check(spread < 0.5, '%d channels, %.3f to %.3f dB, %.3f dB apart'
          % (len(db), min(db.values()), max(db.values()), spread))
    ref = 10 * np.log10(bt_synth.AMPLITUDE ** 2)
    check(abs(np.mean(list(db.values())) - ref) < 0.5,
          'and it is bt_synth\'s amplitude, %.2f dB against %.2f' % (np.mean(list(db.values())), ref))


def check_seed():
    print('\nA seed is a recording')
    a, sa = planted('DH3', bursts=6, hop_fn=standin_formula, seed=5)
    b, sb = planted('DH3', bursts=6, hop_fn=standin_formula, seed=5)
    c, sc = planted('DH3', bursts=6, hop_fn=standin_formula, seed=6)
    check(a.tobytes() == b.tobytes() and json.dumps(sa) == json.dumps(sb),
          'the same seed gives the same samples, byte for byte, and the same sidecar')
    check(a.tobytes() != c.tobytes(), 'another seed gives other samples')
    check(sa['bursts'] == sc['bursts'], 'but the same bursts: the seed is noise and phase, not packets')


def check_window():
    print('\nA channel the tuning cannot hold')

    def refused(channel, at=0, **kw):
        try:
            hop.synthesise_hop(lambda clk: channel if (clk - 0x0123400) // 12 == at else 40,
                               'DH5', bursts=at + 1, snr_db=30, **kw)
        except ValueError as e:
            return str(e)
        return None

    for channel in (29, 56):
        why = refused(channel)
        check(why is not None and 'burst 0' in why and 'channel %d' % channel in why,
              'channel %d (%g MHz) raises ValueError: %s' % (channel, hop.channel_mhz(channel),
                                                             (why or 'it did not')[:70]))
    for channel in (30, 55):
        check(refused(channel) is None, 'channel %d (%g MHz) does not' % (channel, hop.channel_mhz(channel)))
    why = refused(56, at=3)
    check(why is not None and 'burst 3' in why and 'channel 56' in why,
          'a bad channel on burst 3 is named burst 3: %s' % (why or 'it did not raise')[:50])
    why = refused(33, fs=20e6, center_mhz=2441.0)
    check(why is None, 'at 20 MS/s on 2441 MHz channel 33 is inside (+/-7.2 MHz)')
    why = refused(32, fs=20e6, center_mhz=2441.0)
    check(why is not None, 'and channel 32 is not')
    for bad in (-1, 79, 33.5, None):
        try:
            hop.synthesise_hop(lambda clk, bad=bad: bad, 'DH5', bursts=2, fs=FS, center_mhz=CENTER)
            check(False, '%r is not a channel and must raise' % (bad,))
        except ValueError:
            check(True, 'hop_fn returning %r raises ValueError' % (bad,))
    # A numpy integer is a channel, and still makes a sidecar json can write.
    _, side = hop.synthesise_hop(lambda clk: np.int64(40), 'DH5', bursts=2, fs=FS, center_mhz=CENTER)
    check(json.dumps(side) is not None and type(side['bursts'][0]['channel']) is int,
          'a numpy integer channel comes out a plain int in the sidecar')


def check_single_channel():
    print('\nOne channel is bt_synth\'s single-channel file')
    kw = dict(bursts=8, fs=FS, center_mhz=CENTER, cfo_hz=7e3, timing_frac=0.37, snr_db=30.0, seed=3)
    a, side = hop.synthesise_hop(lambda clk: 47, 'DH5', **kw)
    b, ref = bt_synth.synthesise('DH5', channel_mhz=hop.channel_mhz(47), **kw)
    # Same seed, same draws, and a carrier counted from sample 0 of the file in
    # both: what is left is the float32 the samples are stored in. A carrier
    # counted from each burst's own first sample, a fresh phase at every hop,
    # is out by a different angle on every burst and shows as order 1.
    diff = np.abs(a.astype(np.complex128) - b).max() / bt_synth.AMPLITUDE
    check(diff < 1e-3, 'same seed, same samples to %.1e of the amplitude: the phase follows '
          'the absolute sample index' % diff)
    check(all(x['air_bits'] == y['air_bits'] and x['start_sample'] == y['start_sample']
              for x, y in zip(side['bursts'], ref['bursts'])), 'the same packets in the same places')


def check_sidecar(iq, side, starts):
    print('\nThe sidecar')
    want = expected_channels(standin, side)
    keys = ['start_sample', 'ptype', 'clk', 'lt_addr', 'flow', 'arqn', 'seqn', 'header18',
            'payload_hex', 'payload_full_hex', 'payload_valid', 'air_bits', 'channel',
            'channel_mhz', 'symbol_phase']
    check(all(all(k in e for k in keys) for e in side['bursts']),
          'every burst has all %d of its keys' % len(keys))
    top = ['generator', 'generator_commit', 'lap', 'uap', 'clk', 'clk_convention', 'sample_rate',
           'center_mhz', 'cfo_hz', 'timing_frac', 'snr_db', 'snr_bw_hz', 'modulation',
           'slot_samples', 'start_offset', 'symbol_phase', 'start_sample_meaning', 'bursts',
           'hopping', 'hop_channels', 'channel_mhz', 'bt_channel']
    check(all(k in side for k in top), 'the top level has all %d of its keys' % len(top))
    check(side['hopping'] is True and side['channel_mhz'] is None and side['bt_channel'] is None,
          'hopping is true, and the file has no one channel_mhz or bt_channel')
    check(side['center_mhz'] == CENTER and side['sample_rate'] == FS and side['clk'] == 0x0123400,
          'centre, sample rate and clock are what was asked')
    check([e['channel'] for e in side['bursts']] == want,
          'every burst\'s channel is hop_fn of its clock, %d of %d' % (
              sum(e['channel'] == w for e, w in zip(side['bursts'], want)), len(want)))
    check(all(e['channel_mhz'] == 2402 + e['channel'] for e in side['bursts']),
          'channel_mhz is 2402 + channel on every burst')
    check(side['hop_channels'] == sorted(set(want)),
          'hop_channels is the sorted set of channels used: %d' % len(side['hop_channels']))
    check(all(type(e['symbol_phase']) is int and e['symbol_phase'] == e['start_sample'] % SPS
              for e in side['bursts']), 'symbol_phase is start_sample %% %d, an integer, on every burst' % SPS)
    slot = side['slot_samples']
    check(all(e['start_sample'] % slot == slot // 2 for e in side['bursts'])
          and [e['clk'] for e in side['bursts']] == [side['clk'] + 12 * k for k in range(len(want))],
          'a DH5 every 6 slots on a grid half a slot off sample 0, the clock 12 ticks a burst')
    sigma = np.sqrt(bt_synth.AMPLITUDE ** 2 / 10 ** (side['snr_db'] / 10) * FS / 1e6)
    check(abs(np.sqrt(np.mean(np.abs(iq[:40000]) ** 2)) / sigma - 1) < 0.05,
          'noise from sample 0, %.4f rms in 1 ms before the first burst, %.4f planted'
          % (np.sqrt(np.mean(np.abs(iq[:40000]) ** 2)), sigma))
    found = [s for s in starts if s is not None]
    off = np.array([s - e['start_sample'] for s, e in zip(starts, side['bursts']) if s is not None])
    check(len(found) == len(starts) and np.abs(off).max() <= 1,
          'start_sample is where the access code is, by correlating it on its channel: '
          '%d of %d found, worst %.0f sample(s) off (timing_frac %.2f is the rest)'
          % (len(found), len(starts), np.abs(off).max(), side['timing_frac']))


def main():
    iq, side = planted()
    print('A planted recording: %d DH5 bursts, %d channels, %.1f MS/s, %.0f Hz offset, '
          '%.2f of a sample, %.0f dB' % (len(side['bursts']), len(side['hop_channels']), FS / 1e6,
                                         side['cfo_hz'], side['timing_frac'], side['snr_db']))
    check_stand_in(side)
    check_carrier(iq, side)
    starts = check_decode(iq, side, ota.libbtbb())
    print('\nA packet stays on its channel for its whole length')
    for ptype in ('DH5', 'DH3', 'DM1'):
        check_train(ptype)
    check_level(iq, side)
    check_seed()
    check_window()
    check_single_channel()
    check_sidecar(iq, side, starts)
    print('\nRESULT: %s' % ('FAIL (%d)' % len(failures) if failures else 'PASS'))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
