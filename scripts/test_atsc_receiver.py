#!/usr/bin/env python3
"""ATSC transmitter into ATSC receiver, no radio at all.

``test_atsc_loopback.py`` proves the transmit chain against GNU Radio's own
receiver. This proves *our* receiver - the one the app runs - and in
particular the thing that cost the most time getting ATSC on the air: a
transmitter whose clock is a few parts per million out puts the pilot a few
kilohertz from where A/53 says and stretches the symbol rate by the same
fraction, and correcting only the first leaves the decoder producing noise
from a signal whose spectrum looks perfect.

So the test deliberately breaks the transmitter's clock and requires the
receiver to put it back, carrier *and* symbol rate:

    python scripts/test_atsc_receiver.py <stream.ts>
    python scripts/test_atsc_receiver.py <stream.ts> --ppm 13.3

13.3 ppm is not an arbitrary number - it is what a HackRF measured on this
bench at 533 MHz.

The signal is rendered to temporary files first (about 190 MB per second of
signal, deleted afterwards) rather than generated into the receiver. That
costs disk but buys the measurement that matters most: with no transmit
chain sharing the machine, the decode speed printed for each run is the
receiver's own, and that is what says whether it can keep up with the air.
"""
import argparse
import math
import os
import sys
import tempfile
import time

import numpy as np
from gnuradio import blocks, dtv, filter, gr
from gnuradio.filter import firdes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps.atsc_rx_core import (  # noqa: E402
    PILOT_OFFSET, SYMBOL_RATE, TsAnalyzer, VSB_LEVELS, afc_correction,
    channel_center_mhz, channel_for_center, mer_db, pilot_offset_hz)
from apps.atscReceiver import AtscDemod, ts_sink  # noqa: E402
from apps.bb60_source import (gain_plan as bb60_gain_plan,  # noqa: E402
                              nearest_rate as bb60_nearest_rate)

RATE = 12e6
failures = []


def check(name, ok, detail=''):
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{'  ' + detail if detail else ''}")
    if not ok:
        failures.append(name)


def close(name, got, want, tol):
    ok = abs(got - want) <= tol
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: {got:.6g}")
    if not ok:
        print(f"       wanted {want:.6g} +/- {tol:g}")
        failures.append(name)


# --- the transmitter, and a crystal that is not quite right -----------------

def transmit_chain(tb, ts_path):
    """``atscXmitter``'s flowgraph, block for block. Returns its last block."""
    pilot = (6e6 - SYMBOL_RATE / 2) / 2
    src = blocks.file_source(gr.sizeof_char, ts_path, True, 0, 0)
    chain = [src, dtv.atsc_pad(), dtv.atsc_randomizer(), dtv.atsc_rs_encoder(),
             dtv.atsc_interleaver(), dtv.atsc_trellis_encoder(),
             dtv.atsc_field_sync_mux(),
             blocks.vector_to_stream(gr.sizeof_char, 1024),
             blocks.keep_m_in_n(gr.sizeof_char, 832, 1024, 4),
             dtv.dvbs2_modulator_bc(dtv.FECFRAME_NORMAL, dtv.C1_4,
                                    dtv.MOD_8VSB, dtv.INTERPOLATION_OFF),
             blocks.rotator_cc(((-3e6 + pilot) / SYMBOL_RATE) * math.pi * 2,
                               False),
             filter.fft_filter_ccc(1, firdes.root_raised_cosine(
                 0.11, SYMBOL_RATE, SYMBOL_RATE / 2, 0.1152, 200), 1),
             filter.rational_resampler_ccc(143, 54, [], 0),
             filter.rational_resampler_ccc(8, 19, [], 0),
             blocks.multiply_const_cc(0.85)]
    tb.connect(*chain)
    return chain[-1]


def clock_error(tb, src, ppm, center_hz):
    """Run the signal through a transmitter whose crystal is ``ppm`` fast.

    One crystal drives both the baseband clock and the local oscillator, so
    a fast one does two things at once: the whole baseband happens 1+d
    times faster, and the carrier lands d*f_rf high. Simulating only the
    second - which is what "frequency offset" usually means - would make
    this test pass with half a correction.
    """
    if not ppm:
        return src
    d = ppm * 1e-6
    # resamp_ratio is input samples per output sample, so 1+d produces
    # fewer samples: the same signal, finished sooner.
    squeeze = filter.mmse_resampler_cc(0.0, 1.0 + d)
    shift = blocks.rotator_cc(2 * math.pi * d * center_hz / RATE, False)
    tb.connect(src, squeeze, shift)
    return shift


class Render(gr.top_block):
    """Transmit to a file, so the pilot can be measured off the samples."""

    def __init__(self, ts_path, seconds, ppm, center_hz, out):
        gr.top_block.__init__(self, "render", catch_exceptions=True)
        tail = clock_error(self, transmit_chain(self, ts_path), ppm, center_hz)
        self.connect(tail,
                     blocks.head(gr.sizeof_gr_complex, int(RATE * seconds)),
                     blocks.file_sink(gr.sizeof_gr_complex, out, False))


class Decode(gr.top_block):
    """Receive a rendered file with a chosen correction, and nothing else.

    No transmit chain: the point of reading from a file is that the wall
    clock then measures the receiver alone.
    """

    def __init__(self, path, carrier_hz=0.0, clock_ratio=1.0, centre_notch=True):
        gr.top_block.__init__(self, "decode", catch_exceptions=True)
        src = blocks.file_source(gr.sizeof_gr_complex, path, False, 0, 0)
        self.demod = AtscDemod(RATE, centre_notch=centre_notch)
        self.demod.rotator.set_phase_inc(-2 * math.pi * carrier_hz / RATE)
        self.demod.pfb.set_rate(self.demod.nominal_interp * clock_ratio)
        self.ts = ts_sink()
        self.connect(src, self.demod, self.ts)

#: A stream at 19.392658 Mbps carries this many 188-byte packets a second.
PACKETS_PER_SECOND = 19392658 / (188 * 8)
#: A sampling interval in which more than this fraction went bad is still
#: acquisition, not steady state.
ACQUIRING = 0.01
ZERO = {'packets': 0, 'bad': 0, 'corrected': 0}


def run(name, seconds, **kwargs):
    """Decode a rendered file and report the steady state, not the totals.

    Totals are dominated by acquisition: on a three second run a flawless
    decode still reads 15% bad, which says nothing about the link. Rather
    than guess how long acquisition takes, the counters are sampled all the
    way through and the last interval in which packets were still being
    lost marks where lock was reached - everything after it is the steady
    state. That also gives the acquisition time for free, which is the
    other number worth knowing about a receiver.
    """
    tb = Decode(**kwargs)
    t0 = time.time()
    tb.start()
    history, last, still = [ZERO], -1, 0
    while True:
        time.sleep(0.05)
        rs = tb.demod.rs_counters()
        history.append(rs)
        still = still + 1 if rs['packets'] == last else 0
        last = rs['packets']
        if still >= 6:                   # 300 ms with nothing new: it is done
            break
    mer = tb.demod.mer()
    programs = tb.ts.programs()
    tb.stop()
    tb.wait()
    wall = time.time() - t0
    end = tb.demod.rs_counters()
    history.append(end)

    base = ZERO
    for a, b in zip(history, history[1:]):
        gap = b['packets'] - a['packets']
        if gap and (b['bad'] - a['bad']) / gap > ACQUIRING:
            base = b
    acquired = (end['packets'] - base['packets']) > 0.1 * end['packets']
    if not acquired:
        base = ZERO                      # it never settled; report everything

    packets = end['packets'] - base['packets']
    bad = end['bad'] - base['bad']
    r = {
        'acquired': acquired,
        'acquired_after': base['packets'],
        'packets': packets,
        'bad': bad,
        'bad_pct': 100.0 * bad / packets if packets else 100.0,
        'corrected': (end['corrected'] - base['corrected']) / max(packets, 1),
        'mer': mer,
        'programs': programs,
        'speed': seconds / wall if wall else 0.0,
    }
    lock = (f"locked after {base['packets']} packets "
            f"({base['packets'] / PACKETS_PER_SECOND:.2f} s)" if acquired
            else "never locked")
    print(f"  {name:28s} {r['bad_pct']:6.2f}% bad of {r['packets']:6d}, "
          f"{r['corrected']:5.2f} bytes/pkt corrected, MER {r['mer']:5.1f} dB, "
          f"{r['speed']:.2f}x real time, {lock}")
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('stream', help='a constant-bit-rate MPEG-2 transport stream')
    ap.add_argument('--ppm', type=float, default=13.3,
                    help='transmitter clock error to inject (default: the '
                         '13.3 ppm a HackRF measured on this bench)')
    ap.add_argument('--channel', type=int, default=24,
                    help='RF channel the link runs on (default 24, 533 MHz)')
    ap.add_argument('--seconds', type=float, default=3.0,
                    help='signal to render and decode; about 190 MB of '
                         'temporary space per second, twice over')
    args = ap.parse_args()

    if not os.path.exists(args.stream):
        print(f"no such transport stream: {args.stream}")
        return 2
    centre = channel_center_mhz(args.channel) * 1e6

    print("the channel plan (both apps tune to the channel centre)")
    close("RF 24 centre, MHz", channel_center_mhz(24), 533.0, 1e-9)
    close("RF 14 centre, MHz", channel_center_mhz(14), 473.0, 1e-9)
    close("RF 7 centre, MHz", channel_center_mhz(7), 177.0, 1e-9)
    check("533 MHz is recognised as channel 24", channel_for_center(533.0) == 24)
    check("533.5 MHz is not a channel centre",
          channel_for_center(533.5) is None)

    print("\nMER against symbols with a known amount of noise on them")
    grid = np.tile(VSB_LEVELS, 4000)
    check("perfect symbols read as no error at all", mer_db(grid) > 90,
          f"{mer_db(grid):.0f} dB")
    rng = np.random.default_rng(0)
    got = {}
    for want in (30.0, 25.0, 22.0, 18.0, 15.0, 12.0):
        noisy = grid + rng.normal(
            0, math.sqrt(np.mean(VSB_LEVELS ** 2) / 10 ** (want / 10)),
            grid.size)
        got[want] = mer_db(noisy)
    for want in (30.0, 25.0, 22.0):
        close(f"{want:g} dB of noise reads back", got[want], want, 0.5)
    # Below that it reads high, and must be allowed to: slicing to the
    # nearest level is the same thing as assuming every symbol was decided
    # right, so once noise pushes symbols past the halfway point the error
    # to the *wrong* level gets measured instead. Every receiver's MER does
    # this. The test pins the behaviour down rather than pretending it away,
    # because the app must not quote MER against the 15.2 dB cliff - it
    # cannot see that far.
    for want in (18.0, 15.0, 12.0):
        check(f"{want:g} dB of noise reads high, as a slicer must "
              f"({got[want]:.1f} dB)", got[want] > want)
    check("and it bottoms out near 16 dB however bad things get",
          15.5 < got[12.0] < 17.5, f"{got[12.0]:.1f} dB at 12 dB of noise")
    check("readings stay in order all the way down",
          all(got[a] > got[b] for a, b in
              zip([30.0, 25.0, 22.0, 18.0, 15.0], [25.0, 22.0, 18.0, 15.0, 12.0])))

    print("\nthe BB60D's own arithmetic (no device needed)")
    close("12 MS/s snaps to a rate the hardware has",
          bb60_nearest_rate(12e6), 10e6, 1.0)
    close("2 MS/s snaps to the rate below it, not above",
          bb60_nearest_rate(2e6), 2.5e6, 1.0)
    plans = {p: bb60_gain_plan(p) for p in (0, 50, 60, 100)}
    for percent, plan in plans.items():
        print(f"       {percent:3d}%  ATT {plan['ATT']:+6.1f}  "
              f"RF {plan['RF']:+5.1f}  = {plan['ATT'] + plan['RF']:+6.1f} dB")
    check("0% is the attenuator all the way in and no RF gain",
          plans[0] == {'ATT': -30.0, 'RF': 0.0})
    check("100% is no attenuation and all the RF gain",
          plans[100] == {'ATT': 0.0, 'RF': 20.0})
    check("the attenuator comes out before RF gain goes in",
          plans[50]['RF'] == 0.0 and plans[60]['ATT'] == 0.0)
    check("and the total is monotone across the slider",
          all(sum(plans[a].values()) < sum(plans[b].values())
              for a, b in zip([0, 50, 60], [50, 60, 100])))

    print("\nthe transport stream announces its own programs")
    analyzer = TsAnalyzer()
    with open(args.stream, 'rb') as f:
        analyzer.feed(f.read(4 << 20))
    described = analyzer.describe_programs()
    for line in described:
        print(f"       {line}")
    check("a program was found in the PAT and PMT", bool(described))
    check("no packet was flagged bad in a file that never went on the air",
          analyzer.errors == 0, f"{analyzer.packets} packets")

    tmp = tempfile.mkdtemp(prefix='atsc_rx_test_')
    right = os.path.join(tmp, 'clock_right.cf32')
    fast = os.path.join(tmp, 'clock_fast.cf32')
    noisy = os.path.join(tmp, 'noisy.cf32')
    leaky = os.path.join(tmp, 'leaky.cf32')
    try:
        print(f"\nrendering {args.seconds:g}s of signal twice, into {tmp}")
        Render(args.stream, args.seconds, 0.0, centre, right).run()
        Render(args.stream, args.seconds, args.ppm, centre, fast).run()

        print(f"\nthe pilot, with the transmitter's clock {args.ppm:g} ppm fast")
        # A quarter of a second is plenty: the pilot is a bare spike.
        x = np.fromfile(fast, dtype=np.complex64, count=int(RATE * 0.25))
        measured, prominence = pilot_offset_hz(x, RATE)
        # The carrier carries the pilot up by d*f_rf and squeezing the
        # baseband carries it back down by d times its own 2.69 MHz offset,
        # so this - not d*f_rf - is what a receiver actually sees.
        expected = args.ppm * 1e-6 * (centre + PILOT_OFFSET)
        print(f"       measured {measured:+.0f} Hz, {prominence:.0f} dB above "
              f"the haystack")
        close("pilot offset is where the arithmetic says", measured, expected,
              max(30.0, abs(expected) * 0.01))
        check("the pilot stands clear of the data", prominence > 8,
              f"{prominence:.0f} dB")
        clean_error, _ = pilot_offset_hz(
            np.fromfile(right, dtype=np.complex64, count=int(RATE * 0.25)),
            RATE)
        check("and a correct clock measures as no error at all",
              abs(clean_error) < 30, f"{clean_error:+.1f} Hz")

        carrier_hz, clock_ratio = afc_correction(measured, centre)
        close("the clock correction recovers the ppm figure",
              (clock_ratio - 1) * 1e6, args.ppm, 0.2)

        print("\nwhat each half of the AFC is worth, receiver alone")
        clean = run("a clock that is right", args.seconds, path=right)
        nothing = run(f"{args.ppm:g} ppm, no correction", args.seconds,
                      path=fast)
        carrier = run(f"{args.ppm:g} ppm, carrier only", args.seconds,
                      path=fast, carrier_hz=carrier_hz)
        both = run(f"{args.ppm:g} ppm, carrier + clock", args.seconds,
                   path=fast, carrier_hz=carrier_hz, clock_ratio=clock_ratio)

        # A transmitter's own LO leak at the carrier, 20 dB under the
        # channel: what a VSG60 at -59 dBm measured (knowledge/atsc.md).
        # 30 dB of noise on both, so the leak is what differs.
        print("\nthe transmitter's LO leak at the channel centre, -20 dBc")
        x = np.fromfile(right, dtype=np.complex64)
        p = float(np.mean(np.abs(x[len(x) // 3:]) ** 2))
        rng = np.random.default_rng(7)
        x = x + (np.sqrt(p * 1e-3 / 2) * (rng.normal(size=x.size)
                 + 1j * rng.normal(size=x.size))).astype(np.complex64)
        x.tofile(noisy)
        (x + np.complex64(np.sqrt(p * 1e-2))).astype(np.complex64).tofile(leaky)
        del x
        no_leak = run("no leak", args.seconds, path=noisy)
        unnotched = run("leak, notch off", args.seconds, path=leaky,
                        centre_notch=False)
        notched = run("leak, notch on", args.seconds, path=leaky)
    finally:
        for path in (right, fast, noisy, leaky):
            if os.path.exists(path):
                os.remove(path)
        os.rmdir(tmp)

    print()
    check("a correct clock decodes perfectly", clean['bad_pct'] < 0.1,
          f"{clean['bad_pct']:.3f}% bad")
    check("an uncorrected clock error decodes nothing",
          nothing['bad_pct'] > 90 and not nothing['acquired'],
          f"{nothing['bad_pct']:.1f}% bad")
    check("correcting only the carrier does not rescue it",
          carrier['bad_pct'] > 60 and not carrier['acquired'],
          f"{carrier['bad_pct']:.1f}% bad")
    check("correcting carrier and clock together does, completely",
          both['bad_pct'] < 0.1,
          f"{both['bad_pct']:.3f}% bad against {clean['bad_pct']:.3f}% clean")
    check("and the picture comes back with it",
          both['programs'] == clean['programs'] and bool(both['programs']),
          '; '.join(both['programs']))
    check("the corrected link is as good as one that never broke",
          abs(both['mer'] - clean['mer']) < 0.5,
          f"MER {both['mer']:.1f} against {clean['mer']:.1f} dB")
    check("the receiver keeps up with the air, with room to spare",
          both['speed'] > 1.2, f"{both['speed']:.2f}x real time")
    check("unnotched, a -20 dBc leak costs the eye more than 2 dB",
          no_leak['mer'] - unnotched['mer'] > 2,
          f"MER {unnotched['mer']:.1f} against {no_leak['mer']:.1f} dB")
    check("the centre notch gives it back, to within 0.5 dB",
          abs(notched['mer'] - no_leak['mer']) < 0.5 and notched['bad_pct'] < 0.1,
          f"MER {notched['mer']:.1f} against {no_leak['mer']:.1f} dB, "
          f"{notched['bad_pct']:.3f}% bad")
    check("and the notch costs the receiver no speed worth having",
          notched['speed'] > 1.2, f"{notched['speed']:.2f}x real time")

    print()
    if failures:
        print(f"{len(failures)} FAILED: {', '.join(failures)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
