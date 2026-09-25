#!/usr/bin/env python3
"""The ISM transmitter into the ISM receiver, on a cable: VSG60 to HackRF.

    python scripts/test_ism_loop.py
    python scripts/test_ism_loop.py nexus_th --levels -60,-90,-100,-110
    python scripts/test_ism_loop.py --seconds 10
    python scripts/test_ism_loop.py nexus_th --transmit-only --seconds 10
    python scripts/test_ism_loop.py --transmit-only --freq 915 \
        --timetable sweep.jsonl --label whole
    python scripts/test_ism_loop.py --grade rtl_433-*.jsonl --timetable sweep.jsonl

Both apps, built as the launcher builds them: ``ismReceiver`` on a HackRF in
this process, and ``ismXmitter`` on the VSG60 in a second one - this script
again, with ``--transmit-only``. Not one process: a VSG60 opened after a
HackRF in the same process transmits nothing at all. For each
profile the VSG steps down through ``--levels`` and the receiver counts how
many bursts rtl_433 decoded as the right device, with what SNR - so it says
both that the loop works and where it stops working.

**Connect the two first**: VSG60 RF out into the HackRF's antenna port
through 20-30 dB of pad, or an antenna on each. It has been run on two
antennas, which decoded to about −40 dBm; a cable is what makes the levels a
measurement. Either way this refuses anything above ``MAX_LEVEL_DBM`` - a
HackRF's receive input is damaged above -5 dBm and the VSG60 reaches +10. See
[ism](../devnotes/ism.md#the-bench-a-cable-and-a-pad-not-an-antenna).

**With the receiver on another machine**, ``--transmit-only`` runs just the
VSG half and prints the clock time each level starts; the receiver's Heard
column says which levels got through - or, with ``--timetable``, writes
each level's start and end as Unix times, and ``--grade`` later reads a log
of the receiver's decodes against them: rtl_433's own JSON lines, each with
the Unix time it was ``heard``, as fm-receiver's Log to file writes them.
That is how a BB60D is used, since it
cannot stream beside a VSG60 on one host: ``ismReceiver`` on TVAdemo with the
BB60D, the cable from the VSG here.

A VSG60 and a HackRF stream together on one host; it is a VSG60 and a BB60D
that do not. Close any running launcher first: it holds the HackRF. The
whole sweep takes about four minutes at the defaults; give a ``timeout`` room
for that, and it will still turn the VSG off if it fires.
"""
import argparse
import functools
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
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
    """Why the receiving half cannot run, or None.

    Only the HackRF: the VSG60's library is never loaded in this process,
    since the transmitter runs in a process of its own - see main().
    """
    problems = []
    try:
        import SoapySDR
        if not SoapySDR.Device.enumerate('driver=hackrf'):
            problems.append("no HackRF found")
    except Exception as exc:
        problems.append("SoapySDR cannot look for a HackRF: %s" % exc)
    return '; '.join(problems) or None


def transmit(args, name, levels):
    """This script again, as the VSG half alone, for one profile."""
    return subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), name, '--transmit-only',
         '--seconds', str(args.seconds), '--freq', str(args.freq),
         '--levels=' + ','.join('%g' % level for level in levels)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        bufsize=1)


_LEVEL_LINE = re.compile(r'^\s+\d\d:\d\d:\d\d\s+(-?[\d.]+) dBm')


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
    table = open(args.timetable, 'a') if args.timetable else None
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
                start = time.time()
                print("  %s  %7.1f dBm" % (time.strftime('%H:%M:%S'), level))
                pump(app, args.seconds)
                if table is not None:
                    table.write(json.dumps({
                        'profile': name, 'freq_mhz': args.freq,
                        'label': args.label, 'level_dbm': level,
                        'start': round(start, 3),
                        'end': round(time.time(), 3)}) + '\n')
                    table.flush()
        finally:
            transmitter.stop()
            transmitter.wait()
            del transmitter
        print("  %s  off" % time.strftime('%H:%M:%S'))
    if table is not None:
        table.close()
    return 0


def grade(args):
    """A receiver's decode log against a ``--timetable``, one table per
    frequency and label.

    Over the air the VSG's dBm is the path as much as the receiver, and the
    path is different at every frequency, so each cell also carries what
    does not depend on it: rtl_433's best SNR and the level the burst
    arrived at. Where decoding stops is compared in those, not in dBm.

    The first ``--guard`` seconds of each level are not counted. A decode is
    logged when its burst ends, a second after it began and later still
    through the pipe, so a burst sent at one level is logged in the first
    moments of the next - on a BB60D, one decode 10 dB below where decoding
    stopped, at exactly the previous level's dBFS.
    """
    slots = [json.loads(line) for line in open(args.timetable)
             if line.strip()]
    rows = []
    for path in args.grade:
        for line in open(path):
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg.get('heard'), (int, float)):
                rows.append((msg['heard'] + args.clock_offset, msg))
    if not slots:
        print("Nothing in %s." % args.timetable)
        return 2
    print("%d decodes in %d log(s), %d levels in the timetable, receiver "
          "clock %+.2f s, the first %g s of each level not counted\n"
          % (len(rows), len(args.grade), len(slots), args.clock_offset,
             args.guard))
    groups = {}
    for slot in slots:
        groups.setdefault((slot['freq_mhz'], slot.get('label') or ''),
                          []).append(slot)
    used = set()
    ok = True
    for (freq, label), group in sorted(groups.items()):
        profiles = sorted({s['profile'] for s in group})
        levels = sorted({s['level_dbm'] for s in group}, reverse=True)
        print("%.2f MHz%s - bursts decoded, best SNR, arrival level"
              % (freq, "  [%s]" % label if label else ""))
        print("  %-9s" % "VSG" + "".join("  %-24s" % p for p in profiles))
        lowest = {}
        for level in levels:
            cells = []
            for name in profiles:
                model, ident = EXPECTED[name]
                cell = "-"
                for slot in group:
                    if slot['profile'] != name or slot['level_dbm'] != level:
                        continue
                    used.update(id(r) for t, r in rows
                                if slot['start'] <= t < slot['end'])
                    inside = [(t, r) for t, r in rows
                              if slot['start'] + args.guard <= t
                              < slot['end']]
                    bursts, snr, others = count(inside, model, ident)
                    levels_in = [r['level_dbfs'] for _t, r in inside
                                 if r.get('model') == model
                                 and isinstance(r.get('level_dbfs'),
                                                (int, float))]
                    cell = "%d" % bursts
                    if bursts:
                        cell += ", %.0f dB" % snr if snr is not None else ""
                        if levels_in:
                            cell += ", %.0f dBFS" % max(levels_in)
                        lowest[name] = (level, snr)
                    if others:
                        cell += " +" + ",".join(others)
                cells.append("  %-24s" % cell)
            print("  %5.0f dBm" % level + "".join(cells))
        for name in profiles:
            if name in lowest:
                level, snr = lowest[name]
                print("  %s: last decoded at %.0f dBm%s" % (
                    name, level, ", %.1f dB SNR" % snr if snr is not None
                    else ""))
            else:
                ok = False
                print("  %s: never decoded" % name)
        print()
    # In the few milliseconds between one level's end and the next's start
    # is a handover too, like the guard, not a decode from nowhere.
    stray = [r for t, r in rows if id(r) not in used
             and not any(0 <= t - slot['end'] < args.guard for slot in slots)]
    if stray:
        print("%d decode(s) outside every level: %s" % (
            len(stray), ", ".join(sorted({str(r.get('model')) for r in stray}))))
    print("RESULT:", "PASS" if ok else "FAIL",
          "(every profile decoded at every frequency)" if ok else "")
    return 0 if ok else 1


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
    ap.add_argument('--timetable', metavar='JSONL',
                    help="with --transmit-only, add each level's start and "
                         "end to this file; with --grade, read them from it")
    ap.add_argument('--label', default='',
                    help="with --timetable, a name for this run - the "
                         "receiver's setting, say - to group it by")
    ap.add_argument('--grade', nargs='+', metavar='LOG',
                    help="grade these rtl_433 JSON logs against "
                         "--timetable; transmits nothing")
    ap.add_argument('--clock-offset', type=float, default=0.0,
                    metavar='S', help="with --grade, added to every heard "
                                      "time: the transmitter's clock minus "
                                      "the receiver's")
    ap.add_argument('--guard', type=float, default=1.5, metavar='S',
                    help="with --grade, how long after a level starts its "
                         "decodes are still the last level's")
    args = ap.parse_args()
    if args.grade:
        if not args.timetable:
            ap.error("--grade needs --timetable")
        return grade(args)

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

    # The VSG runs in a process of its own. Opened in this one after the
    # HackRF is streaming, it takes every sample and transmits none of them
    # - measured, and not understood; see devnotes/radios.md. Its timetable
    # comes back on its stdout, and each line is stamped as it arrives.
    ok = True
    proc = None
    try:
        for name in args.profiles or sorted(EXPECTED):
            model, ident = EXPECTED[name]
            burst_s = tx.build_frame(name, tx.default_fields(name)) \
                .duration_us * 1e-6
            print("%s (%s), a %.0f ms burst every %.1f s - about %.0f per "
                  "level:" % (name, model, burst_s * 1e3, INTERVAL_S,
                              args.seconds / (burst_s + INTERVAL_S + 0.005)))
            lines = queue.Queue()
            proc = transmit(args, name, levels)
            threading.Thread(
                target=lambda p=proc: [lines.put((time.time(), line))
                                       for line in p.stdout],
                daemon=True).start()
            receiver.decoder.take()                  # nothing from before
            marks, rows = [], []
            while proc.poll() is None or not lines.empty():
                pump(app, 0.1)
                rows += receiver.decoder.take()
                while not lines.empty():
                    at, line = lines.get()
                    match = _LEVEL_LINE.match(line)
                    if match:
                        marks.append((at, float(match.group(1))))
                    elif line.strip().endswith(' off'):
                        marks.append((at, None))
                    elif line.startswith('Cannot'):
                        print("  " + line.strip())
            pump(app, 1.0)                           # the last package's end
            rows += receiver.decoder.take()
            proc = None
            heard_any = False
            for i, (start, level) in enumerate(marks):
                if level is None:
                    continue
                end = marks[i + 1][0] if i + 1 < len(marks) else float('inf')
                bursts, snr, others = count(
                    [(t, r) for t, r in rows if start <= t < end],
                    model, ident)
                heard_any = heard_any or bursts > 0
                print("  %7.1f dBm  %2d burst(s) decoded%s%s"
                      % (level, bursts,
                         "  best SNR %.1f dB" % snr if snr is not None
                         else "",
                         "  also: " + ", ".join(others) if others else ""))
            ok = ok and heard_any
            print()
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()                         # it turns the VSG off
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
        receiver.stop()
        receiver.wait()
        receiver.shutdown()

    print("RESULT:", "PASS" if ok else "FAIL",
          "(every profile decoded at one level or more)" if ok else "")
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
