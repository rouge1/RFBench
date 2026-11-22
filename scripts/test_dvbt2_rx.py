#!/usr/bin/env python3
"""The DVB-T2 Receiver's own window and chain on a recording of the transmitter's: no radio.

    python scripts/test_dvbt2_rx.py                  # 16QAM 3/5, about a minute and a half
    python scripts/test_dvbt2_rx.py --mod QPSK

``scripts/test_dvbt2_tx.py``'s transmitter chain makes 8 s of a three-programme multiplex at
10 MS/s (colour bars, a clip with sound, one without), into a throwaway folder. Then
``dvbt2Receiver`` is built by its real ``main()``, offscreen, with only the radio replaced:
the recording, looped, through a throttle at the radio's 10 MS/s. What it pins:

- the decoder starts after the flowgraph (``start_decoder``), learns the signal, and the
  window's status says it is locked, with L1's mode;
- every decoded codeword is good, and frames keep coming at the air's pace (a frame at the
  loop's seam may be lost: the recording jumps there);
- the programme list fills from the PAT and the SDT, named from the files;
- Watch on the programme with no sound, then a change of programme while watching, each
  give a stand-in player (ffmpeg decoding to frame checksums, in place of ffplay) a steady
  run of pictures of that programme, written from the player thread;
- a burst of dropped samples shows "falling behind" and then clears once it is RECENT_S old;
  a collapse with drops still says "Breaking up";
- SIGTERM landing while the stream's lock is held (as refresh() holds it) does not hang: the
  handler defers to Qt, and the shutdown it runs stops the player, closes the recording and
  leaves no worker process behind.

Nothing writes ``config/`` or the user's ``QSettings``: the window is never closed (its
``closeEvent`` saves); the test stops it the way ``closeEvent`` would, without the save.
Needs GNU Radio with gr-dtv, ffmpeg, scipy, and ``scripts/build_dvbt2_native.py`` run once.
"""

import argparse
import faulthandler
import multiprocessing
import os
import signal
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

RESULTS = []


def report(name, ok, detail=''):
    RESULTS.append((name, ok))
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ('' if ok else f"  ({detail})"), flush=True)


def frames_in(path):
    try:
        return sum(1 for line in open(path) if line and not line.startswith('#'))
    except OSError:
        return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--mod', default='16QAM')
    ap.add_argument('--rate', default='3/5')
    ap.add_argument('--seconds', type=float, default=8.0, help='length of the recording')
    a = ap.parse_args(argv)

    from apps.dvbt2 import iqt2_native, t2_tx
    if not iqt2_native.available():
        print("the native LDPC is not built: python scripts/build_dvbt2_native.py")
        return 1
    import test_dvbt2_tx as tx
    from gnuradio import blocks, gr
    from PyQt5 import Qt
    from apps import dvbt2Receiver as rx

    plan = t2_tx.frame_plan(16384, '19/128', a.mod, a.rate, 2, True)
    with tempfile.TemporaryDirectory() as tmp:
        paths = tx.make_clips(tmp)
        iq, ts_in = os.path.join(tmp, 'tx.cf32'), os.path.join(tmp, 'in.ts')
        took, err = tx.transmit(paths, plan, a.seconds, iq, ts_in)
        print(f"  {a.seconds:g} s of {a.mod} {a.rate} made in {took:.1f} s", flush=True)

        class Recorded(rx.dvbt2Receiver):
            def _make_radio(self):
                self.file_src = blocks.file_source(gr.sizeof_gr_complex, iq, True)
                self.throttle = blocks.throttle(gr.sizeof_gr_complex, rx.SAMPLE_RATE, True)
                self.connect(self.file_src, self.throttle)
                self.radio_source = self.throttle
                return self.throttle

            def apply_gain(self):
                pass

        players = []

        def stand_in(program, title):
            out = os.path.join(tmp, f'player{len(players)}-p{program}.framecrc')
            players.append(out)
            return ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', 'pipe:0',
                    '-map', f'0:p:{program}:v', '-flush_packets', '1', '-f', 'framecrc', out]
        rx.find_player = stand_in

        app = Qt.QApplication.instance() or Qt.QApplication([])
        values = {'radio_type': 'bb60', 'ipXmitAddr': '', 'center_mhz': 594.0, 'gain_percent': 60}
        tb = rx.main(top_block_cls=Recorded, app=app, config_values=values)

        def pump(seconds, until=None):
            end = time.time() + seconds
            while time.time() < end:
                app.processEvents()
                time.sleep(0.05)
                if until is not None and until():
                    return True
            return until() if until else True

        locked = pump(40, lambda: tb.lbl['lock'].text().startswith('Locked - clean')
                      and tb.program_combo.count() == 3)
        st = tb.live.state()
        report(f"the window says it is locked: {tb.lbl['lock'].text()!r}", locked, st)
        report(f"L1's mode shown: {tb.lbl['mode'].text()!r}",
               tb.lbl['mode'].text().startswith(f"{a.mod} {a.rate}, 16k ext"), tb.lbl['mode'].text())

        names = [tb.program_combo.itemText(i) for i in range(tb.program_combo.count())]
        want = ['1  Colour Bars', '2  Test Card One', '3  Silent Two']
        report(f"the programme list, named from the SDT: {names}", names == want, names)

        before = tb.live.state()
        pump(6)
        after = tb.live.state()
        made = after['decoded'] - before['decoded']
        report(f"frames keep coming at the air's pace: {made} decoded in 6 s "
               f"({6 / plan['frame_s']:.0f} sent)", made >= 0.8 * 6 / plan['frame_s'], (before, after))
        # The recording jumps where it loops, and the frame across the jump fails whole: no more
        # than one frame's codewords lost for each pass of the recording.
        cw, ok = after['codewords'], after['codewords_ok']
        passes = 1 + int(after['frames'] * plan['frame_s'] / a.seconds)
        report(f"every codeword good but at the loop's seam: {ok} of {cw} "
               f"({passes} passes, {plan['blocks']} a frame)",
               cw > 0 and cw - ok <= passes * plan['blocks'] and after['packets_errored'] == 0, after)

        tb.program_combo.setCurrentIndex(2)
        tb.toggle_watch()
        pump(8)
        silent = frames_in(players[-1]) if players else 0
        report(f"Watch on 'Silent Two' (no sound): {silent} pictures in 8 s",
               silent >= 100 and tb._watching == 3, players)
        tb.program_combo.setCurrentIndex(1)            # a change while watching
        pump(8)
        second = frames_in(players[-1]) if len(players) == 2 else 0
        report(f"a change to 'Test Card One' restarts the player on it: {second} pictures in 8 s",
               len(players) == 2 and players[-1].endswith('-p2.framecrc') and second >= 100
               and tb._watching == 2, players)
        report(f"the player kept up: {tb.ts.dropped} bytes dropped", tb.ts.dropped == 0)

        # One burst of dropped samples, as when the decoder's queue overflows once: the status
        # says so, then goes back to clean once the burst is older than RECENT_S.
        real_state, extra = tb.live.state, {'drops': 0}

        def state_with_drops():
            st = real_state()
            st['dropped_samples'] = st.get('dropped_samples', 0) + extra['drops']
            st['dropped_since_lock'] = st.get('dropped_since_lock', 0) + extra['drops']
            return st
        tb.live.state = state_with_drops
        extra['drops'] = 50000
        behind = pump(3, lambda: 'falling behind' in tb.lbl['lock'].text())
        report(f"a burst of dropped samples is shown: {tb.lbl['lock'].text()!r}", behind)
        cleared = pump(rx.RECENT_S + 6, lambda: tb.lbl['lock'].text().startswith('Locked - clean'))
        report(f"and clears once it is {rx.RECENT_S:g} s old: {tb.lbl['lock'].text()!r}", cleared)
        tb.live.state = real_state
        text, _ = tb._status({'phase': 'receiving'}, 100, 50, 1000)
        report(f"a collapse while dropping still says so: {text!r}", text.startswith('Breaking up'))

        # SIGTERM while the stream's lock is held, as refresh() holds it. Handled in place, as
        # it was, stop_player() would wait on that lock for ever: a hang is dumped and fails.
        rec = os.path.join(tmp, 'rec.ts')
        tb.ts.start_record(rec)
        pump(1)
        faulthandler.dump_traceback_later(60, exit=True)
        with tb.ts.lock:
            os.kill(os.getpid(), signal.SIGTERM)
            for _ in range(1000):                       # Python runs the handler here
                pass
        pump(5, lambda: tb._shut_down and not tb.ts.player_alive())
        faulthandler.cancel_dump_traceback_later()
        time.sleep(1)
        left = multiprocessing.active_children()
        report(f"SIGTERM with the lock held shuts down: player gone, recording closed "
               f"({os.path.getsize(rec)} bytes), {len(left)} worker processes left",
               tb._shut_down and not tb.ts.player_alive() and tb.ts.record is None
               and tb.live is None and os.path.getsize(rec) > 0 and not left, left)
    bad = [n for n, ok in RESULTS if not ok]
    print(f"{len(RESULTS) - len(bad)} ok, {len(bad)} FAIL of {len(RESULTS)}")
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
