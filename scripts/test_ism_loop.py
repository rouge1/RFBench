#!/usr/bin/env python3
"""The ISM transmitter into the ISM receiver, on a cable: VSG60 to HackRF.

    python scripts/test_ism_loop.py
    python scripts/test_ism_loop.py nexus_th --levels -60,-90,-100,-110
    python scripts/test_ism_loop.py --seconds 10
    python scripts/test_ism_loop.py nexus_th --transmit-only --seconds 10

Both apps, built as the launcher builds them, in one process: ``ismXmitter``
on the VSG60 and ``ismReceiver`` on a HackRF, joined by a cable. For each
profile the VSG steps down through ``--levels`` and the receiver counts how
many bursts rtl_433 decoded as the right device, with what SNR - so it says
both that the loop works and where it stops working.

**Cable it before running it**: VSG60 RF out, through 20-30 dB of pad, into
the HackRF's antenna port. The VSG's calibrated level is what makes this a
measurement, and even so this refuses anything above ``MAX_LEVEL_DBM`` - a
HackRF's receive input is damaged above -5 dBm and the VSG60 reaches +10. See
[ism](../devnotes/ism.md#the-bench-a-cable-and-a-pad-not-an-antenna).

**With the receiver on another machine**, ``--transmit-only`` runs just the
VSG half and prints the clock time each level starts; the receiver's Heard
column says which levels got through. That is how a BB60D is used, since it
cannot stream beside a VSG60 on one host: ``ismReceiver`` on TVAdemo with the
BB60D, the cable from the VSG here.

A VSG60 and a HackRF stream together on one host; it is a VSG60 and a BB60D
that do not. Close any running launcher first: it holds the HackRF. The
whole sweep takes about four minutes at the defaults; give a ``timeout`` room
for that, and it will still turn the VSG off if it fires.
"""
import argparse
import functools
import os
import signal
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5 import Qt  # noqa: E402

#: Each line as it happens: a sweep takes minutes, and a run cut short by a
#: timeout would otherwise say nothing at all about what it had measured.
print = functools.partial(print, flush=True)  # noqa: A001

#: The most the VSG is ever asked for. With no pad at all this is still 15 dB
#: under the HackRF's damage threshold. -30 was the first ceiling, and on a
#: cable the burst was visible there but small.
MAX_LEVEL_DBM = -20.0
DEFAULT_LEVELS = (-20, -30, -40, -60, -80, -90, -100, -110, -120)
#: Short, so a level takes seconds; a burst is at most about a second.
INTERVAL_S = 1.0
#: Decodes further apart than this are separate bursts.
BURST_GAP_S = 0.5

EXPECTED = {
    'nexus_th': ('Nexus-TH', 181),
    'acurite_609txc': ('Acurite-609TXC', 202),
    'lacrosse_tx141th_bv2': ('LaCrosse-TX141THBv2', 231),
    'ev1527': ('Generic-Remote', 4660),
}


def pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def radios_present():
    """Why the loop cannot run, or None."""
    from apps import vsg_sink
    problems = []
    if not vsg_sink.is_available() or not vsg_sink.find_devices():
        problems.append("no VSG60 found")
    elif vsg_sink.in_use():
        problems.append("the VSG60 is in use by another process")
    try:
        import SoapySDR
        if not SoapySDR.Device.enumerate('driver=hackrf'):
            problems.append("no HackRF found")
    except Exception as exc:
        problems.append("SoapySDR cannot look for a HackRF: %s" % exc)
    return '; '.join(problems) or None


def count(rows, model, ident):
    """Bursts heard as the right device, and the best SNR among them."""
    times = [t for t, r in rows
             if r.get('model') == model and str(r.get('id')) == str(ident)]
    snrs = [r['snr'] for _t, r in rows
            if r.get('model') == model and isinstance(r.get('snr'),
                                                       (int, float))]
    bursts = sum(1 for i, t in enumerate(times)
                 if i == 0 or t - times[i - 1] > BURST_GAP_S)
    others = sorted({r.get('model', '?') for _t, r in rows} - {model})
    return bursts, (max(snrs) if snrs else None), others


def transmit_only(app, args, levels):
    """The VSG half, with a timetable for whoever is watching the receiver."""
    import apps.ismXmitter as tx
    from apps import vsg_sink
    if not vsg_sink.is_available() or not vsg_sink.find_devices():
        print("Cannot transmit: no VSG60 found.")
        return 2
    print("VSG60 at %.2f MHz, %g s per level. Match these times to the "
          "receiver's Heard column.\n" % (args.freq, args.seconds))
    for name in args.profiles or sorted(EXPECTED):
        transmitter = tx.ismXmitter(config_values={
            'radio_type': 'vsg', 'cf': args.freq, 'pwr': 0, 'profile': name,
            'fields': tx.default_fields(name), 'interval_s': INTERVAL_S,
            'offset_khz': tx.DEFAULT_OFFSET_KHZ})
        print("%s (%s %s):" % ((name,) + EXPECTED[name]))
        try:
            transmitter.start()
            for level in levels:
                transmitter.radio_sink.set_level(level)
                print("  %s  %7.1f dBm" % (time.strftime('%H:%M:%S'), level))
                pump(app, args.seconds)
        finally:
            transmitter.stop()
            transmitter.wait()
            del transmitter
        print("  %s  off" % time.strftime('%H:%M:%S'))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('profiles', nargs='*', choices=sorted(EXPECTED) + [])
    ap.add_argument('--levels', default=','.join(map(str, DEFAULT_LEVELS)),
                    help="VSG levels in dBm, comma-separated")
    ap.add_argument('--seconds', type=float, default=6.0,
                    help="how long each level is listened to")
    ap.add_argument('--freq', type=float, default=433.92, help="MHz")
    ap.add_argument('--gain', type=float, default=30.0,
                    help="the receiver's RF gain, percent")
    ap.add_argument('--transmit-only', action='store_true',
                    help="the VSG half only, for a receiver on another "
                         "machine; prints when each level starts")
    args = ap.parse_args()

    levels = [float(x) for x in args.levels.split(',') if x.strip()]
    if any(level > MAX_LEVEL_DBM for level in levels):
        print("refusing: %s dBm is over this script's %.0f dBm ceiling - a "
              "HackRF's input is damaged above -5 dBm"
              % (max(levels), MAX_LEVEL_DBM))
        return 2

    # A timeout's SIGTERM has to come through the finally blocks below, which
    # are what turn the VSG's output off. This loop runs Python every 20 ms,
    # so the handler is not starved the way it is under an idle Qt loop.
    def stop(_sig, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)

    app = Qt.QApplication(sys.argv[:1])
    if args.transmit_only:
        return transmit_only(app, args, levels)
    why = radios_present()
    if why:
        print("Cannot run the loop: %s." % why)
        print("Cable the VSG60's RF out through 20-30 dB of pad into the "
              "HackRF's antenna port, plug both in, close any launcher, and "
              "run this again.")
        return 2

    import apps.ismReceiver as rx
    import apps.ismXmitter as tx
    if not rx.RTL_433:
        print("rtl_433 is not on the path. Skipping.")
        return 0

    print("VSG60 -> HackRF at %.2f MHz, receiver gain %g%%, "
          "%g s per level\n" % (args.freq, args.gain, args.seconds))
    receiver = rx.ismReceiver(config_values={
        'radio_type': 'hackrf', 'frequency_mhz': args.freq,
        'gain_percent': args.gain})
    receiver.start()
    receiver.apply_gain()
    pump(app, 1.0)

    ok = True
    try:
        for name in args.profiles or sorted(EXPECTED):
            model, ident = EXPECTED[name]
            fields = tx.default_fields(name)
            transmitter = tx.ismXmitter(config_values={
                'radio_type': 'vsg', 'cf': args.freq, 'pwr': 0,
                'profile': name, 'fields': fields,
                'interval_s': INTERVAL_S,
                'offset_khz': tx.DEFAULT_OFFSET_KHZ})
            burst_s = transmitter.frame.duration_us * 1e-6
            expected = args.seconds / (burst_s + INTERVAL_S + 0.005)
            transmitter.start()
            print("%s (%s), a %.0f ms burst every %.1f s - about %.0f per "
                  "level:" % (name, model, burst_s * 1e3, INTERVAL_S,
                              expected))
            heard_any = False
            try:
                for level in levels:
                    transmitter.radio_sink.set_level(level)
                    pump(app, 0.3)
                    receiver.decoder.take()          # nothing from before
                    pump(app, args.seconds)
                    bursts, snr, others = count(receiver.decoder.take(),
                                                model, ident)
                    heard_any = heard_any or bursts > 0
                    print("  %7.1f dBm  %2d burst(s) decoded%s%s"
                          % (level, bursts,
                             "  best SNR %.1f dB" % snr if snr is not None
                             else "",
                             "  also: " + ", ".join(others) if others else ""))
            finally:
                transmitter.stop()
                transmitter.wait()
                del transmitter
            ok = ok and heard_any
            print()
    finally:
        receiver.stop()
        receiver.wait()
        receiver.decoder.close()

    print("RESULT:", "PASS" if ok else "FAIL",
          "(every profile decoded at one level or more)" if ok else "")
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
