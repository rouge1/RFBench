#!/usr/bin/env python3
"""Collapse and expand every bank of the launcher, in every theme.

Each bank's heading is a button that wipes its tiles shut and open (see
``BankHeader`` and ``BankBody`` in RFbenchToolkit.py). A first attempt at
it crashed the launcher on every toggle, drew every shadow in the wrong
place and squashed the tiles flat as they went, so this checks what went
wrong then, a frame at a time:

- nothing raises while painting, frame by frame through a wipe - an
  exception in a paintEvent otherwise ends the program;
- the tiles are cut off, never squeezed: no tile is ever shorter than
  it needs to be. (Their heights may still change by a pixel or two: a
  bank shutting can take the scroll bar away, and the grid is laid out
  afresh for the width that gives back, as on any change of page
  height.)
- the chevron turns with the wipe, and a second press partway turns
  round from where it had got to;
- the shadows are under the tiles, and nowhere else;
- which banks are shut is saved, a new launcher opens them shut, and its
  first size is measured with every bank open;
- the name, the line and the keyboard toggle it too;
- nothing reads the settings file while the pulse runs;
- the chevron lights in the pulse's colour as the pulse reaches it, and
  only then.

The launcher runs in a temporary copy of the repository, so this never
touches the user's ``config/``:

    QT_QPA_PLATFORM=offscreen python scripts/test_bank_collapse.py
"""

import json
import os
import shutil
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

THEMES = ('slate', 'reading-room', 'walnut')

failures = []
raised = []


def check(label, ok, detail=''):
    print(f"  {'ok  ' if ok else 'FAIL'} {label}"
          + (f" - {detail}" if detail and not ok else ''))
    if not ok:
        failures.append(label)


def record_exception(kind, value, tb):
    # PyQt aborts on an exception raised in a virtual - a paintEvent - when
    # sys.excepthook is the default; with a hook of our own it calls that
    # instead. Stop there, saying what raised: carrying on is no use, as
    # the paint that raised never ended its QPainter, and Qt segfaults a
    # few frames later.
    raised.append(''.join(traceback.format_exception(kind, value, tb)))
    print("  FAIL an exception was raised while the launcher ran:\n"
          + raised[-1])
    sys.stdout.flush()
    os._exit(1)


def prepare_workspace(folder, theme_name, collapsed=None):
    for name in ('apps', 'fonts', 'icons'):
        shutil.copytree(os.path.join(ROOT, name), os.path.join(folder, name),
                        ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(os.path.join(ROOT, 'RFbenchToolkit.py'),
                 os.path.join(folder, 'RFbenchToolkit.py'))
    os.makedirs(os.path.join(folder, 'config'))
    settings = {'media_directory': '', 'radio_type': 'hackrf',
                'theme': theme_name}
    if collapsed is not None:
        settings['collapsed_banks'] = collapsed
    write_settings(folder, settings)


def write_settings(folder, settings):
    with open(os.path.join(folder, 'config', 'window_settings.json'), 'w',
              encoding='utf-8') as fh:
        json.dump(settings, fh)


def saved(folder):
    with open(os.path.join(folder, 'config', 'window_settings.json'),
              encoding='utf-8') as fh:
        return json.load(fh)


def main():
    sys.excepthook = record_exception
    sys.path.insert(0, ROOT)
    from PyQt5 import QtCore, QtGui, QtWidgets
    from PyQt5.QtTest import QTest

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    old_cwd = os.getcwd()

    def pump(ms):
        clock = QtCore.QElapsedTimer()
        clock.start()
        while clock.elapsed() < ms:
            app.processEvents(QtCore.QEventLoop.AllEvents, 5)

    def open_launcher(folder):
        os.chdir(folder)
        sys.path.insert(0, folder)
        for name in [m for m in sys.modules
                     if m == 'RFbenchToolkit' or m.startswith('apps')]:
            del sys.modules[name]
        import RFbenchToolkit
        launcher = RFbenchToolkit.RFbenchToolkit(app)
        launcher.resize(1000, 820)
        launcher.show()
        pump(150)
        return RFbenchToolkit, launcher

    def close_launcher(launcher, folder):
        # Never close() it: its closeEvent saves into config/. It is in a
        # throwaway folder, but a test that closes windows is one habit
        # away from doing it in the real one.
        launcher._pulse_timer.stop()
        launcher.hide()
        launcher.deleteLater()
        app.processEvents()
        sys.path.remove(folder)
        os.chdir(old_cwd)

    def wipe(module, launcher, bank, frames):
        """Toggle a bank and follow it until it settles, grabbing every
        frame as a screen would paint it."""
        launcher._headers[bank].click()
        clock = QtCore.QElapsedTimer()
        clock.start()
        while clock.elapsed() < module.BANK_TOGGLE_MS + 150:
            app.processEvents(QtCore.QEventLoop.AllEvents, 5)
            launcher.grab()
            body = launcher._bodies[bank]
            squeezed = [t for _g, tiles in launcher._banks for t in tiles
                        if t.isVisible()
                        and t.height() < t.minimumSizeHint().height()]
            frames.append((body.reveal, launcher._headers[bank].turn,
                           body.height(), squeezed))

    for theme_name in THEMES:
        print(f"\n{theme_name}")
        with tempfile.TemporaryDirectory() as folder:
            prepare_workspace(folder, theme_name)
            module, launcher = open_launcher(folder)
            ground = QtGui.QColor(module.theme.TOKENS['ground'])
            rest = [t.height() for _g, tiles in launcher._banks for t in tiles]
            full = [b.full_height() for b in launcher._bodies]
            natural = launcher.natural_size()

            # Shut each bank in turn, frame by frame.
            for bank, header in enumerate(launcher._headers):
                name = header.bank_name
                frames = []
                wipe(module, launcher, bank, frames)
                body = launcher._bodies[bank]
                check(f"{name}: nothing raised while painting the wipe",
                      not raised, raised[-1].splitlines()[-1] if raised else '')
                check(f"{name}: no tile was squeezed",
                      not any(squeezed for *_x, squeezed in frames))
                check(f"{name}: the chevron turned with the wipe",
                      all(abs(reveal - turn) < 1e-9
                          for reveal, turn, *_x in frames)
                      and any(0.2 < turn < 0.8 for _r, turn, *_x in frames))
                heights = [h for _r, _t, h, _x in frames]
                check(f"{name}: its height ran down to nothing",
                      heights[0] > full[bank] * 0.5 and heights[-1] <= 1
                      and all(b <= a + 3 for a, b in zip(heights, heights[1:])),
                      f"{heights[:3]} ... {heights[-3:]}")
                check(f"{name}: hidden once shut, its tiles with it",
                      not body.isVisible() and body.reveal == 0.0
                      and not any(t.isVisible()
                                  for t in launcher._banks[bank][1]))
                check(f"{name}: saved as collapsed",
                      launcher._bank_rows[bank]
                      in saved(folder).get('collapsed_banks', []))
                check(f"{name}: a screen reader hears 'Expand'",
                      header.accessibleName() == f"Expand {name}")

            # Open the first again, and turn it round halfway.
            frames = []
            launcher._headers[0].click()
            pump(module.BANK_TOGGLE_MS // 2)
            halfway = launcher._bodies[0].reveal
            launcher._headers[0].click()
            app.processEvents()
            turned = launcher._bodies[0].reveal
            pump(module.BANK_TOGGLE_MS + 150)
            check("pressed again partway, it turns round from there",
                  0.1 < halfway < 0.9 and abs(turned - halfway) < 0.2
                  and launcher._bodies[0].reveal == 0.0,
                  f"halfway {halfway:.2f}, just after {turned:.2f}")

            # Open everything, by the name, the line and the keyboard.
            header = launcher._headers[0]
            QTest.mouseClick(header.label, QtCore.Qt.LeftButton)
            pump(module.BANK_TOGGLE_MS + 150)
            check("a click on the bank's name opens it",
                  launcher._bodies[0].reveal == 1.0)
            header = launcher._headers[1]
            QTest.mouseClick(header.line, QtCore.Qt.LeftButton)
            pump(module.BANK_TOGGLE_MS + 150)
            check("a click on the bank's line opens it",
                  launcher._bodies[1].reveal == 1.0)
            header = launcher._headers[2]
            header.setFocus(QtCore.Qt.TabFocusReason)
            QTest.keyClick(header, QtCore.Qt.Key_Space)
            pump(module.BANK_TOGGLE_MS + 150)
            check("Space on the focused heading opens it",
                  launcher._bodies[2].reveal == 1.0)
            check("every bank open is every bank saved open",
                  saved(folder).get('collapsed_banks') == [])
            check("open again, every tile is its old height",
                  [t.height() for _g, tiles in launcher._banks
                   for t in tiles] == rest)

            # The shadows: under each tile, and not piled at the top.
            launcher._pulse_timer.stop()
            for line in launcher._lines:
                line.phase = None
            for heading in launcher._headings:
                heading.set_charge(0.0)
            launcher._headers[1].click()
            pump(module.BANK_TOGGLE_MS + 150)
            image = launcher.grab().toImage()
            under, above = [], []
            for bank in (0, 2):
                for tile in launcher._banks[bank][1]:
                    at = tile.mapTo(launcher, QtCore.QPoint(0, 0))
                    x = at.x() + tile.width() // 2
                    under.append(QtGui.QColor(image.pixel(
                        x, at.y() + tile.height() + 2)))
            top = launcher._headers[0].mapTo(launcher, QtCore.QPoint(0, 0))
            for tile in launcher._banks[0][1]:
                at = tile.mapTo(launcher, QtCore.QPoint(0, 0))
                above.append(QtGui.QColor(image.pixel(
                    at.x() + tile.width() // 2, top.y() - 8)))
            check("every tile has a shadow under it",
                  all(c.lightness() < ground.lightness() for c in under),
                  f"{[c.name() for c in under]} against {ground.name()}")
            check("no shadow above the first bank's tiles",
                  all(c == ground for c in above),
                  f"{[c.name() for c in above]}")
            # Between two tiles is inside the bank's body, which paints
            # over the column's shadows unless it is transparent - the
            # launcher's stylesheet gives every plain QWidget the ground.
            between = []
            tiles = launcher._banks[0][1]
            for left, right in zip(tiles, tiles[1:]):
                a = left.mapTo(launcher, QtCore.QPoint(0, 0))
                b = right.mapTo(launcher, QtCore.QPoint(0, 0))
                between.append(QtGui.QColor(image.pixel(
                    (a.x() + left.width() + b.x()) // 2,
                    a.y() + left.height() - 6)))
            check("the shadows show between the tiles, through the body",
                  all(c.lightness() < ground.lightness() for c in between),
                  f"{[c.name() for c in between]} against {ground.name()}")
            check("the column paints each tile's lifted shadow",
                  all(module.shadow_column(t) is launcher._column
                      for _g, tiles in launcher._banks for t in tiles))

            # The pulse must not read the file.
            reads = []
            real = module.read_settings
            module.read_settings = lambda *a, **k: (reads.append(1),
                                                   real(*a, **k))[1]
            launcher._sync_pulse()
            pump(400)
            module.read_settings = real
            check("the pulse reads nothing from disk", not reads,
                  f"{len(reads)} reads in 0.4 s")

            # The chevron catches the pulse. Stop the timer and set the
            # clock by hand, to moments in the first bank's cycle.
            launcher._pulse_timer.stop()
            timing = module.theme.PULSE
            header = launcher._headers[0]
            arrives = launcher._pulse_arrives(header)
            real_clock = launcher._pulse_clock

            class Clock:
                def __init__(self, fired):
                    self.ms = int(round((timing['charge'] + fired) * 1000))

                def elapsed(self):
                    return self.ms

            def glow_at(fired):
                launcher._pulse_clock = Clock(fired)
                launcher._move_pulse()
                app.processEvents()
                return header._glow

            def nearest_to_pulse():
                pulse = QtGui.QColor(module.theme.TOKENS['pulse'])
                image = header.grab(header.chevron_box().toRect()).toImage()
                return min(sum((a - b) ** 2 for a, b in zip(
                    QtGui.QColor(image.pixel(x, y)).getRgb()[:3],
                    pulse.getRgb()[:3])) ** 0.5
                    for x in range(image.width())
                    for y in range(image.height()))

            dark = [glow_at(-0.5), glow_at(0.2),
                    glow_at(arrives - timing['catch'] - 0.05)]
            unlit = nearest_to_pulse()
            peak = glow_at(arrives)
            crossed = header.line.phase
            lit = nearest_to_pulse()
            half = glow_at(arrives + timing['land'] / 2)
            out = glow_at(arrives + timing['land'] + 0.05)
            check("the chevron is dark while the name charges and the pulse "
                  "crosses", dark == [0.0, 0.0, 0.0], f"{dark}")
            check("it lights as the pulse's head reaches it",
                  peak > 0.95 and crossed is not None and crossed > 0.7,
                  f"glow {peak:.2f}, the pulse {crossed} of the way along")
            check("lit, it takes the pulse's colour", lit < unlit * 0.5,
                  f"nearest pixel {lit:.0f} from the pulse's colour lit, "
                  f"{unlit:.0f} unlit")
            check("and dies away over 'land'",
                  0.4 < half < 0.6 and out == 0.0, f"{half:.2f}, {out:.2f}")
            glow_at(arrives)
            launcher._pulse_clock = real_clock
            launcher.hide()
            app.processEvents()
            check("hidden, the launcher puts the chevron's glow out",
                  all(h._glow == 0.0 for h in launcher._headers))
            launcher.show()
            pump(50)

            # A change of theme with a bank shut leaves it shut.
            launcher.next_theme()
            pump(100)
            check("a change of theme leaves a shut bank shut",
                  not launcher._bodies[1].isVisible()
                  and launcher._headers[1].turn == 0.0)
            close_launcher(launcher, folder)

            # A new launcher on that file opens the bank shut, and is as
            # big as it would be with everything open.
            write_settings(folder, {**saved(folder), 'theme': theme_name})
            module, launcher = open_launcher(folder)
            body, header = launcher._bodies[1], launcher._headers[1]
            check("a bank left shut opens shut, chevron and all",
                  not body.isVisible() and header.turn == 0.0
                  and header.accessibleName()
                  == f"Expand {header.bank_name}")
            check("its first size is measured with every bank open",
                  launcher.natural_size() == natural,
                  f"{launcher.natural_size()} against {natural}")
            frames = []
            wipe(module, launcher, 1, frames)
            check("it wipes open from nothing, no tile squeezed",
                  not raised and frames[0][2] < full[1] * 0.5
                  and not any(squeezed for *_x, squeezed in frames)
                  and body.reveal == 1.0
                  and body.height() == body.full_height(),
                  f"first frame {frames[0][2]} px, last {body.height()} "
                  f"of {body.full_height()}")
            close_launcher(launcher, folder)

    # A file somebody else wrote.
    print("\na collapsed_banks the launcher did not write")
    with tempfile.TemporaryDirectory() as folder:
        prepare_workspace(folder, 'slate', collapsed=[2, 9, 'x'])
        try:
            module, launcher = open_launcher(folder)
            check("junk in collapsed_banks is ignored, not fatal",
                  all(b.isVisible() for b in launcher._bodies))
            close_launcher(launcher, folder)
        except Exception as exc:
            check("junk in collapsed_banks is ignored, not fatal", False,
                  repr(exc))
        prepare_workspace_again = os.path.join(folder, 'again')
        os.makedirs(prepare_workspace_again)
        prepare_workspace(prepare_workspace_again, 'slate', collapsed=[2, 9])
        module, launcher = open_launcher(prepare_workspace_again)
        check("a row with no bank is ignored, a real one kept",
              [b.isVisible() for b in launcher._bodies] == [True, False, True])
        close_launcher(launcher, prepare_workspace_again)

    if raised:
        print("\nraised while running:\n" + raised[0])
    print(f"\n{'all checks passed' if not failures else f'{len(failures)} failed'}")
    return 1 if failures or raised else 0


if __name__ == '__main__':
    code = main()
    sys.stdout.flush()
    # Skip Qt's teardown of widgets deleted out of order; the result is in.
    os._exit(code)
