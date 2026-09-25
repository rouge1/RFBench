#!/usr/bin/env python
"""Open every config dialog and check it is laid out straight.

The dialogs are the part of this project every user meets first, and they
are the part with no test: ``scripts/test_launcher_gui.py`` drives one of
them through real X input, which is slow, needs a free radio and can only
cover one app at a time. This opens all fifteen offscreen, for each of the
four radios, and measures them.

    python scripts/test_dialog_layout.py
    python scripts/test_dialog_layout.py --radio vsg
    python scripts/test_dialog_layout.py --save /tmp/shots
    python scripts/test_dialog_layout.py --theme reading-room --save /tmp/shots

What it measures, and why each one was a real fault rather than a matter of
taste:

- **A row's label must be level with the control beside it.** The theme gave
  every ``QLabel`` a 10 px top margin, which inside a row pushes the text
  down within the label's own rectangle while the spin box next to it stays
  centred. Every row of all fifteen dialogs was four or five pixels out, and
  it read as the box sitting high.
- **The controls must line up in a column.** Each slider used to start
  wherever its label's text happened to end - four different positions in
  one dialog - and shifted sideways as a live value in the label changed
  width.
- **No combo box may list the same thing twice.** The media folder holds
  each clip as both a ``.mp4`` and a ``.ts``, so the NTSC video picker
  offered forty entries with every title in it twice, spelled identically.
- **A dialog must fit on the smallest screen here**, the 1366x768 laptop.
- **OK must work on every radio that can run the app** whenever it works
  on a HackRF, which runs everything. The four audio transmitters asked
  for "a HackRF, or an IP address", so on a VSG60 - no address, being USB -
  OK stayed grey whatever was chosen; only a USRP needs an address. The
  test gives the USRP one, and compares with the HackRF rather than
  requiring OK outright, so a machine with no media, where the audio
  dialogs grey it out on purpose, still passes.

It needs no radio and no display: Qt's offscreen platform renders the real
widgets, and ``--save`` writes the PNGs if you want to look.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

#: Every module in apps/ with a ConfigDialog, in launcher order.
MODULES = [
    'askGenerator', 'fskGenerator', 'pskGenerator', 'amSineGenerator',
    'fmAudioRecordedGenerator', 'amAudioInternalGeneratorLive',
    'ppmookAudioXmitter', 'subcarrierRecordedAudio',
    'fmVideoXmitter', 'fmVideoReceiver', 'ntscAnalogVideoRecorded',
    'ntscReceiver',
    'atscXmitter', 'atscReceiver', 'fmRdsTransmitter', 'rdsReceiver',
    'ismXmitter', 'ismReceiver',
]

RADIOS = ['hackrf', 'usrp', 'vsg', 'bb60']

#: How far out of level a row is allowed to be, in pixels. Qt rounds odd
#: leftovers, so one pixel means nothing; four is what the old margin cost.
LEVEL_TOLERANCE = 2

#: And how far the left edge of one row's control may sit from another's.
COLUMN_TOLERANCE = 2

#: The smallest screen this runs on is 1366x768, less the window frame.
MAX_WIDTH, MAX_HEIGHT = 1000, 700


def install_radio(radio, theme_name='slate'):
    """Make every dialog believe this radio is the one in Settings, and
    paint it in this theme rather than whichever the user has chosen.

    Patches the function rather than the settings file: a test must never
    write into the user's own configuration, and this one runs four times.
    """
    import apps.utils as utils
    original = getattr(utils, '_test_real_read_settings', utils.read_settings)
    utils._test_real_read_settings = original

    def read_settings():
        settings = original()
        settings['radio_type'] = radio
        settings['theme'] = theme_name
        if radio == 'usrp' and not settings.get('usrp_ip'):
            settings['usrp_ip'] = '192.168.10.2'
        return settings

    utils.read_settings = read_settings


def visual_rect(Qt, widget):
    """Where a widget actually paints, in its window's coordinates.

    A label paints its text inside its geometry, and may be given more
    height than the text needs, so its geometry is not where the text is -
    which is the whole point of the level check.
    """
    if isinstance(widget, Qt.QLabel):
        contents = widget.contentsRect()
        rect = Qt.QRect(widget.mapTo(widget.window(), contents.topLeft()),
                        contents.size())
        text = widget.fontMetrics().height()
        if rect.height() > text:
            align = widget.alignment()
            if align & Qt.Qt.AlignTop:
                top = rect.top()
            elif align & Qt.Qt.AlignBottom:
                top = rect.bottom() - text
            else:
                top = rect.top() + (rect.height() - text) // 2
            rect = Qt.QRect(rect.left(), top, rect.width(), text)
        return rect
    return Qt.QRect(widget.mapTo(widget.window(), widget.rect().topLeft()),
                    widget.size())


def find_rows(Qt, layout, rows):
    """Every horizontal row whose first widget is a label naming the rest."""
    if isinstance(layout, Qt.QHBoxLayout):
        widgets = [layout.itemAt(i).widget() for i in range(layout.count())]
        widgets = [w for w in widgets if w is not None and w.isVisible()]
        if len(widgets) > 1 and isinstance(widgets[0], Qt.QLabel):
            rows.append(widgets)
    for i in range(layout.count()):
        item = layout.itemAt(i)
        if item.layout() is not None:
            find_rows(Qt, item.layout(), rows)
            continue
        widget = item.widget()
        if widget is None or isinstance(widget, Qt.QComboBox):
            continue
        inner = widget.layout()
        if inner is not None:
            find_rows(Qt, inner, rows)


def find_combos(Qt, widget):
    return widget.findChildren(Qt.QComboBox)


def check(Qt, name, radio, dialog, save):
    """Measure one dialog. Returns a list of complaints."""
    from apps.utils import _dialog_layout

    problems = []
    top = _dialog_layout(dialog)
    rows = []
    if top is not None:
        find_rows(Qt, top, rows)

    columns = []
    for widgets in rows:
        rects = [visual_rect(Qt, w) for w in widgets]
        centres = [r.center().y() for r in rects]
        if max(centres) - min(centres) > LEVEL_TOLERANCE:
            problems.append(
                f"row '{widgets[0].text()}' is not level: "
                + ', '.join(f'{type(w).__name__} centre y={r.center().y()}'
                            for w, r in zip(widgets, rects)))
        # Where this row's first control starts, for the column check.
        if len(rects) > 1:
            columns.append((widgets[0].text(), rects[1].left()))

    if len(columns) > 1:
        lefts = [left for _text, left in columns]
        if max(lefts) - min(lefts) > COLUMN_TOLERANCE:
            problems.append(
                'controls do not line up in a column: '
                + ', '.join(f"'{t}' at x={x}" for t, x in columns))

    for combo in find_combos(Qt, dialog):
        seen = {}
        for i in range(combo.count()):
            text = combo.itemText(i)
            if not text:                      # a separator
                continue
            seen.setdefault(text, []).append(i)
        for text, where in seen.items():
            if len(where) > 1:
                problems.append(
                    f'a combo box lists {text!r} {len(where)} times '
                    f'(items {where})')

    if dialog.width() > MAX_WIDTH or dialog.height() > MAX_HEIGHT:
        problems.append(f'does not fit a small screen: '
                        f'{dialog.width()}x{dialog.height()}')

    if save:
        os.makedirs(save, exist_ok=True)
        dialog.grab().save(os.path.join(save, f'{radio}-{name}.png'))
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--radio', choices=RADIOS, action='append',
                        help='only this radio (may be repeated)')
    parser.add_argument('--app', action='append', help='only this module')
    parser.add_argument('--save', metavar='DIR',
                        help='write a PNG of every dialog here')
    parser.add_argument('--theme', default='slate',
                        help='paint them in this theme - the layout is the '
                             'same in all of them, so this is for --save')
    args = parser.parse_args()

    from PyQt5 import Qt
    import importlib

    app = Qt.QApplication(sys.argv[:1])
    failures = 0
    checked = 0
    ok_enabled = {}

    for radio in (args.radio or RADIOS):
        print(f'\nradio: {radio}')
        install_radio(radio, args.theme)
        for name in (args.app or MODULES):
            module = importlib.import_module('apps.' + name)
            importlib.reload(module)      # pick up the patched read_settings
            try:
                dialog = module.ConfigDialog()
            except Exception as exc:
                print(f'  {name:30s} FAILED to open: {exc}')
                failures += 1
                continue
            dialog.show()
            dialog.resize(dialog.sizeHint())
            app.processEvents()
            box = dialog.findChild(Qt.QDialogButtonBox)
            ok = box.button(Qt.QDialogButtonBox.Ok) if box else None
            ok_enabled[name, radio] = ok.isEnabled() if ok else None
            problems = check(Qt, name, radio, dialog, args.save)
            checked += 1
            size = f'{dialog.width()}x{dialog.height()}'
            if problems:
                failures += len(problems)
                print(f'  {name:30s} {size:>9s}  {len(problems)} problem(s)')
                for problem in problems:
                    print(f'      {problem}')
            else:
                print(f'  {name:30s} {size:>9s}  ok')
            dialog.close()
            dialog.deleteLater()
            app.processEvents()

    # OK on every radio that can run the app, if it is on a HackRF.
    radios = args.radio or RADIOS
    if 'hackrf' in radios:
        from RFbenchToolkit import RADIO_DIRECTIONS, face_directions
        directions = face_directions()
        print('\nOK on every radio that can run the app')
        bad = 0
        for name in (args.app or MODULES):
            for radio in radios:
                if radio == 'hackrf' or (name, radio) not in ok_enabled \
                        or directions.get(name) not in RADIO_DIRECTIONS[radio]:
                    continue
                if ok_enabled[name, radio] != ok_enabled.get((name, 'hackrf')):
                    bad += 1
                    print(f'  {name:30s} OK is '
                          f"{'on' if ok_enabled[name, radio] else 'off'} with "
                          f"{radio}, "
                          f"{'on' if ok_enabled.get((name, 'hackrf')) else 'off'}"
                          f' with hackrf')
        print('  ok' if not bad else f'  {bad} problem(s)')
        failures += bad

    print(f'\n{checked} dialogs checked')
    if failures:
        print(f'RESULT: FAIL - {failures} problem(s)')
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
