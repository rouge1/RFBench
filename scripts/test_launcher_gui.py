#!/usr/bin/env python3
"""Drive the launcher through a real X session: click an app, run it, close it.

The other test scripts exercise the signal chain with no GUI at all. This one
covers the part they cannot: that ``linux/start_app.sh`` starts, that a button
press reaches ``launch_application``, that the config dialog accepts, that the
flowgraph window actually appears, and that closing it brings the launcher
back. Everything is driven with real X input through xdotool, so the app is
exercised exactly as a person would.

    python scripts/test_launcher_gui.py "FM + RDS Receiver"
    python scripts/test_launcher_gui.py "FM + RDS Transmitter" --hold 30
    python scripts/test_launcher_gui.py "ATSC Video Receiver"

An app on the back of a flip tile - the RDS receiver, the ATSC receiver -
is reached by asking for it by name like any other: the tile is turned over
before the launcher starts, through the same saved setting the badge
writes, and turned back afterwards. Clicking the badge in pixels would test
the animation rather than the app, and would mean finding a 32-pixel circle
in a screenshot.

WARNING: the transmitter really transmits. Point it at an empty channel.

Needs xdotool (``apt install xdotool``) and a display.
"""
import argparse
import ast
import contextlib
import json
import os
import subprocess
import sys
import time

import numpy as np
from PIL import Image
from scipy import ndimage  # type: ignore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS = os.path.join(ROOT, 'config', 'window_settings.json')
LAUNCHER_TITLE = 'RF Bench Toolkit'
# xdotool reports the frame geometry; the close button sits this far inside it.
CLOSE_DX, CLOSE_DY = 33, 30


def xdo(*args, check=True):
    r = subprocess.run(['xdotool', *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"xdotool {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def windows(title):
    out = xdo('search', '--onlyvisible', '--name', title, check=False)
    return [w for w in out.splitlines() if w.strip()]


def wait_for(fn, timeout, what):
    end = time.time() + timeout
    while time.time() < end:
        got = fn()
        if got:
            return got
        time.sleep(0.5)
    raise TimeoutError(f"timed out after {timeout}s waiting for {what}")


def geometry(wid):
    vals = dict(line.split('=', 1)
                for line in xdo('getwindowgeometry', '--shell', wid).splitlines()
                if '=' in line)
    return (int(vals['X']), int(vals['Y']),
            int(vals['WIDTH']), int(vals['HEIGHT']))


def screenshot(path):
    """Grab the screen with Qt.

    Deliberately not scrot/import/maim: none of them are guaranteed present,
    while PyQt5 is already a hard dependency of the launcher itself.
    """
    from PyQt5 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.primaryScreen().grabWindow(0).save(path, 'PNG')
    return path


def button_grid(shot, rect):
    """Find the launcher's app buttons in a screenshot.

    xdotool needs screen coordinates and the grid metrics are not visible from
    outside the process - high-DPI scaling moves them - so locate the buttons
    by their own pixels. Each tile's picture is a large bright block on a dark
    window, which separates it cleanly from the captions, the rail and the
    gear: every part of the tile's own chrome is below the threshold, the
    panel at 0x28 and even its border at 0x43.

    The size window allows for a picture that is 4:3 rather than square, and
    for a tile as narrow as the grid will make one: 150 px, at the width
    where a fifth column has only just fitted (about 840 px of window),
    which leaves a picture 148 wide and 111 high. Narrower than that the
    grid drops to four columns and the tiles get wider again.
    """
    wx, wy, ww, wh = rect
    win = np.asarray(Image.open(shot).convert('RGB')).astype(np.int16)[
        wy:wy + wh, wx:wx + ww]
    lab, _ = ndimage.label(win.max(axis=2) > 70)
    cells = []
    for sl in ndimage.find_objects(lab):
        h, w = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if 100 <= h <= 400 and 120 <= w <= 400:      # picture-sized blobs only
            cells.append((sl[0].start + h // 2, sl[1].start + w // 2))
    if not cells:
        raise RuntimeError("no app buttons found in the launcher window")
    cells.sort()
    rows, cur = [], [cells[0]]
    for c in cells[1:]:
        if abs(c[0] - cur[0][0]) < 80:
            cur.append(c)
        else:
            rows.append(sorted(cur, key=lambda t: t[1]))
            cur = [c]
    rows.append(sorted(cur, key=lambda t: t[1]))
    return [[(wx + cx, wy + cy) for cy, cx in row] for row in rows]


def launcher_literal(name):
    """Read a module-level literal out of the launcher without importing it.

    Constructing the launcher opens a window, and this test exists to drive
    the one ``linux/start_app.sh`` starts - so its tables are read with ``ast``
    instead. That also copes with shapes a regular expression reads wrongly,
    such as a flip tile's nested list of faces.
    """
    src = open(os.path.join(ROOT, 'RFbenchToolkit.py')).read()
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, 'id', None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise RuntimeError(f"no {name} in RFbenchToolkit.py")


RADIO_DIRECTIONS = launcher_literal('RADIO_DIRECTIONS')


def registered_apps():
    """Read the launcher's own tile table: label -> where and which side.

    Reading the source beats hard-coding the layout: the grid is the thing
    under test, and a button that moves should move the click with it.
    """
    tiles = launcher_literal('APP_TILES')

    # Screenshot blobs come back packed left to right with no idea which
    # grid column each one is, so a tile's position among the tiles *that
    # row actually has* is what the click needs - not its column number.
    # Row 2 now ends at column 3, and before this it did not.
    occupied = {}
    for row, col, _faces in tiles:
        occupied.setdefault(row, []).append(col)
    for cols in occupied.values():
        cols.sort()

    found = {}
    for row, col, faces in tiles:
        key = faces[0][1]                 # the first module names the tile
        for index, face in enumerate(faces):
            label, module, _icon, direction = face
            found[label] = {'row': row, 'col': col, 'face': index, 'key': key,
                            'faces': len(faces), 'module': module,
                            'direction': direction,
                            'nth': occupied[row].index(col)}
    return found


def occupied_rows():
    """Grid row -> the columns it actually has a tile in."""
    rows = {}
    for row, col, _faces in launcher_literal('APP_TILES'):
        rows.setdefault(row, []).append(col)
    return rows


def selected_radio():
    """The radio Settings names, and the directions it can go."""
    try:
        with open(SETTINGS) as f:
            radio = json.load(f).get('radio_type', 'hackrf')
    except Exception:
        radio = 'hackrf'
    return radio, RADIO_DIRECTIONS.get(radio, {'tx', 'rx'})


@contextlib.contextmanager
def tile_showing(key, face):
    """Turn a flip tile to the wanted side for the duration of the test.

    This writes the same ``tile_faces`` setting the badge writes, so the
    launcher comes up already showing the app under test. Only that one key
    is put back afterwards: the launcher saves its window position into the
    same file while the test runs, and restoring the whole file wholesale
    would throw that away.
    """
    try:
        with open(SETTINGS) as f:
            original = json.load(f).get('tile_faces', {}).get(key)
    except Exception:
        original = None

    def write(value):
        settings = {}
        if os.path.exists(SETTINGS):
            with open(SETTINGS) as f:
                settings = json.load(f)
        faces = settings.setdefault('tile_faces', {})
        if value is None:
            faces.pop(key, None)
        else:
            faces[key] = value
        os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
        with open(SETTINGS, 'w') as f:
            json.dump(settings, f, indent=4)

    write(face)
    try:
        yield
    finally:
        write(original)


@contextlib.contextmanager
def setting_held(key, value):
    """Hold one launcher setting at ``value`` for the test, and put the
    user's back afterwards - the same one-key write and restore as
    ``tile_showing``, for the same reason."""
    def write(value):
        settings = {}
        if os.path.exists(SETTINGS):
            with open(SETTINGS) as f:
                settings = json.load(f)
        if value is None:
            settings.pop(key, None)
        else:
            settings[key] = value
        os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
        with open(SETTINGS, 'w') as f:
            json.dump(settings, f, indent=4)

    try:
        with open(SETTINGS) as f:
            original = json.load(f).get(key)
    except Exception:
        original = None
    write(value)
    try:
        yield
    finally:
        write(original)


def theme_held(name='slate'):
    """Run the launcher in a dark theme, and put the user's back afterwards.

    ``button_grid`` finds the tiles as bright pictures on a dark window,
    and on Reading Room's paper the whole window is bright - one blob, no
    tiles.
    """
    return setting_held('theme', name)


def banks_open():
    """Run the launcher with every bank open, and put the user's back.

    A collapsed bank has no tiles to find, and the rows found would then
    no longer line up with the rows of APP_TILES - every click after it
    would land a bank too high.
    """
    return setting_held('collapsed_banks', [])


def click(x, y):
    xdo('mousemove', '--sync', str(x), str(y))
    xdo('click', '1')


def close_window(wid):
    x, y, w, _ = geometry(wid)
    click(x + w - CLOSE_DX, y + CLOSE_DY)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('app', help='button label, e.g. "FM + RDS Receiver"')
    ap.add_argument('--hold', type=float, default=15.0,
                    help='seconds to leave the application running')
    ap.add_argument('--shots', default='/tmp', help='where to write screenshots')
    args = ap.parse_args()

    apps = registered_apps()
    if args.app not in apps:
        print(f"no button labelled {args.app!r}. Registered: "
              f"{', '.join(sorted(apps))}")
        return 2
    tile = apps[args.app]
    row, col = tile['row'], tile['nth']

    # The grid dims - and the launcher refuses to run - anything the
    # selected radio cannot do, so say that here rather than let the test
    # fail later looking like a broken button.
    radio, directions = selected_radio()
    if tile['direction'] not in directions:
        print(f"FAIL: {args.app!r} needs a radio that can "
              f"{ {'tx': 'transmit', 'rx': 'receive'}[tile['direction']] }, "
              f"and Settings has {radio!r}. Change radio_type in "
              f"config/window_settings.json first.")
        return 2

    if windows(LAUNCHER_TITLE):
        print("FAIL: a launcher is already running - close it first "
              "(it holds the radio, which blocks the app under test)")
        return 1

    if tile['faces'] > 1:
        print(f"{args.app!r} is face {tile['face'] + 1} of {tile['faces']} on "
              f"the {tile['key']} tile - turning it over first")
    with tile_showing(tile['key'], tile['face']), theme_held(), banks_open():
        return run(args, row, col)


def run(args, row, col):
    print("starting linux/start_app.sh")
    proc = subprocess.Popen([os.path.join(ROOT, 'linux', 'start_app.sh')], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    try:
        wid = wait_for(lambda: next(iter(windows(LAUNCHER_TITLE)), None), 40,
                       'the launcher window')
        print(f"  launcher window {wid} at {geometry(wid)}")

        shot = screenshot(os.path.join(args.shots, 'launcher.png'))
        grid = button_grid(shot, geometry(wid))
        counts = ', '.join(str(len(r)) for r in grid)
        print(f"  found {sum(len(r) for r in grid)} buttons (rows: {counts})")

        # The grid wraps now - it fits as many tiles to a row as the window
        # has room for - so a narrow launcher puts one bank on two rows and
        # every click after it lands on the wrong tile. Say so here, where
        # it is one line to fix, rather than let it look like a broken
        # button several steps later.
        expected = [len(cols) for _row, cols in sorted(occupied_rows().items())]
        if [len(r) for r in grid] != expected:
            print(f"FAIL: the launcher window is too narrow - its banks "
                  f"should be {expected} tiles and came out {counts}. Widen "
                  f"it (or delete window_position from "
                  f"config/window_settings.json) and run again.")
            return 1
        # The launcher's grid rows start at 1; row 0 holds the title. The
        # column here is the tile's position within its row, which is not
        # its grid column once a row has a gap in it.
        x, y = grid[row - 1][col]

        print(f"clicking {args.app!r} at {x},{y}")
        click(x, y)
        dlg = wait_for(lambda: next(iter(windows('Configuration$')), None), 20,
                       'the config dialog')
        print(f"  dialog {dlg} up")
        screenshot(os.path.join(args.shots, 'dialog.png'))

        before = set(windows('.'))
        # Return activates the dialog's default button, which is OK. Clicking
        # it would mean hunting for its pixels in yet another screenshot.
        xdo('windowactivate', '--sync', dlg)
        xdo('key', '--clearmodifiers', 'Return')
        app_wins = wait_for(
            lambda: [w for w in windows('.')
                     if w not in before and w != wid and w != dlg],
            25, 'the application window')
        title = xdo('getwindowname', app_wins[0])
        print(f"  application window {app_wins[0]}: {title!r}")

        print(f"holding {args.hold:g}s")
        time.sleep(args.hold)
        screenshot(os.path.join(args.shots, 'running.png'))

        print("closing the application")
        close_window(app_wins[0])
        wait_for(lambda: not any(w in windows('.') for w in app_wins), 20,
                 'the application to close')
        back = wait_for(lambda: windows(LAUNCHER_TITLE), 20,
                        'the launcher to come back')
        print(f"  launcher visible again ({len(back)} window(s))")
        screenshot(os.path.join(args.shots, 'back.png'))
        print("\nLAUNCHER GUI: PASS")
        return 0
    except Exception as e:
        print(f"\nLAUNCHER GUI: FAIL - {e}")
        return 1
    finally:
        for w in windows(LAUNCHER_TITLE):
            xdo('windowactivate', '--sync', w, check=False)
            xdo('key', '--clearmodifiers', 'alt+F4', check=False)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.terminate()


if __name__ == '__main__':
    raise SystemExit(main())
