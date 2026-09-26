#!/usr/bin/env python3
"""Open every flowgraph window with no radio, and check it wears the theme.

    python scripts/test_flowgraph_windows.py                 # every app
    python scripts/test_flowgraph_windows.py amSineGenerator
    python scripts/test_flowgraph_windows.py --save /tmp/shots
    python scripts/test_flowgraph_windows.py --theme reading-room

The config dialogs have ``scripts/test_dialog_layout.py``; this is the same
for the windows that come up once OK is pressed. Each app is built the way
``launch_application`` builds it - its own ``ConfigDialog`` for the values
it would open with, then its real ``main()`` - with the radio swapped for a
stand-in, so the whole signal chain runs and the plots have something to
draw. A transmitter's radio becomes a throttle into nothing, at the rate
the app asked for; a receiver's becomes noise with a tone in it; a sound
card becomes nothing at all. Nothing opens a device, so it runs beside a
launcher, and with any radio plugged in.

What it checks, on the window as rendered:

- **It carries the flowgraph theme** (``apply_flowgraph_theme``), and
  almost nothing on it is still the light grey and white it used to be -
  or, on a light theme, near black, which is what something still
  painting a dark theme of its own shows as. Slate unless ``--theme``
  says otherwise, whatever the user has chosen: the settings the apps
  read are patched, as below.
- **Every plot's canvas is the well**, a sunken box with a rule round it.
- **Every trace can be seen against it.** The apps set their traces in
  GNU Radio's colours for a white canvas, and black is the commonest - on
  the well it would be invisible. Each line in use needs 3:1 against the
  canvas, the WCAG figure for a graphic that has to be made out.
- **Every label can be read**: 4.5:1 against what is behind it, including
  the receivers' own status colours.
- **Nothing in the window moves until it has been clicked.** A pointer
  passing over a slider, or a scroll wheel turning over any control, used
  to set it - see ``ClickToMove`` in ``apps/utils.py``. Dragging still
  has to work.
- **The power and frequency set in the window are what the dialog opens
  on next time** - gain, on a receiver, and deviation on FM video. The window's own controls are set
  as a user would set them, to their finest digit, and saved as the
  launchers save them on close: only what changed may be written, and a
  fresh dialog must read it back exactly. Its OK must then keep the
  window's position, which lives in the same file. Into a throwaway
  folder, never the real ``config/``.

Then every app again with no media folder, which is how a machine starts
before Settings has been opened: each must still build its flowgraph and
run. Its dialog greys out OK there, but ``apps/_run.py --config`` goes
straight to ``main()``, and a transmitter
handed no file once died on ``None`` - found only because this test ran on
a new machine. The windows are not looked at again; that is the first
pass's job.

Then, with no window from any app, that a window's position, size and
maximized flag go into the app's config and come back out of it - into a
throwaway folder, never the real ``config/``.

Each app runs in a process of its own, because ``main()`` installs signal
handlers and a flowgraph that goes wrong should take only itself down. None
of them is ever closed: an app's ``closeEvent`` writes its geometry into
the user's own ``QSettings``, and a test must not.
"""
import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from apps.theme import THEMES  # noqa: E402 - standard library only

#: Every app with a flowgraph window, in launcher order.
MODULES = [
    'askGenerator', 'fskGenerator', 'pskGenerator', 'amSineGenerator',
    'ppmookAudioXmitter', 'amAudioInternalGeneratorLive',
    'fmAudioRecordedGenerator', 'subcarrierRecordedAudio',
    'fmRdsTransmitter', 'rdsReceiver',
    'atscXmitter', 'atscReceiver', 'ntscAnalogVideoRecorded', 'ntscReceiver',
    'fmVideoXmitter', 'fmVideoReceiver',
    'ismXmitter', 'ismReceiver',
]

#: How long each flowgraph runs before it is looked at, so the plots have
#: drawn a few frames.
RUN_SECONDS = 2.5

#: With no media folder a window is not looked at, only run - long enough
#: for every block's work() to have been called.
NO_MEDIA_SECONDS = 1.0

#: The size each window is looked at - the 1366x768 laptop, less its frame.
SIZE = (1340, 700)

#: WCAG 2.1: 3:1 for a graphic that has to be made out, 4.5:1 for text.
TRACE_CONTRAST = 3.0
TEXT_CONTRAST = 4.5

#: At most this share of a window may be near white - near black, on a
#: light theme. Text in the theme's ink is near white (black), so it is
#: not zero; an unthemed panel is far more.
BRIGHT_SHARE = 0.03

#: The window's control behind each attribute a ``SAVED_SETTINGS`` keeps -
#: a RangeWidget on the older windows, a plain slider or spin box on the
#: rest. The first of the names that the window has is the one.
WINDOW_CONTROL = {
    'rfPwr': ('_rfPwr_win',), 'power_percent': ('pwr_slider',),
    'gain_percent': ('gain_slider',),
    'cf': ('_cf_win', '_centerFrequency_win'),
    'centerFreq': ('_centerFreq_win',),
    'center_mhz': ('freq_spin',), 'freq_mhz': ('freq_spin',),
    'deviation_mhz': ('_dev_win',),
}

#: The dialog's control that reads each saved key back, the same way.
DIALOG_CONTROL = {
    'power_level': ('pwr_slider',), 'power_percent': ('pwr_slider',),
    'gain_percent': ('gain_slider',),
    'center_freq': ('cf_chooser',), 'center_mhz': ('cf_chooser',),
    'frequency_mhz': ('cf_chooser',), 'deviation_mhz': ('deviation_spin',),
}


def contrast(a, b):
    """WCAG contrast ratio between two QColors."""
    def lum(c):
        out = []
        for v in (c.redF(), c.greenF(), c.blueF()):
            out.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def install_stand_ins(media=None, theme_name='slate'):
    """Swap every radio and sound card for a block that opens nothing.

    ``media``, if given, replaces the media folder in the settings the apps
    read - ``''`` is a machine where Settings has never been opened. The
    theme is always replaced, so the user's choice cannot change what is
    being tested.
    """
    from gnuradio import analog, audio, blocks, gr, soapy
    import apps.utils as utils

    class _Quiet:
        """Any other setter the app calls on its radio does nothing.

        Only setters: the receivers ask ``getattr(source, 'adc_overflows')``
        to find out whether the radio is a BB60D, so a stand-in that
        answered everything would pass for one.
        """

        def __getattr__(self, attr):
            if attr.startswith('_'):
                raise AttributeError(attr)
            try:
                return gr.hier_block2.__getattr__(self, attr)
            except AttributeError:
                if attr.startswith('set_'):
                    return lambda *args, **kwargs: None
                raise

    class RadioSink(_Quiet, gr.hier_block2):
        def __init__(self, *args, **kwargs):
            gr.hier_block2.__init__(
                self, 'stand-in radio sink',
                gr.io_signature(1, 1, gr.sizeof_gr_complex),
                gr.io_signature(0, 0, 0))
            self._rate = 2e6
            self._throttle = blocks.throttle(gr.sizeof_gr_complex, self._rate)
            self._null = blocks.null_sink(gr.sizeof_gr_complex)
            self.connect(self, self._throttle, self._null)

        def set_sample_rate(self, channel, rate):
            self._rate = float(rate)
            self._throttle.set_sample_rate(self._rate)

        def get_sample_rate(self, channel=0):
            return self._rate

    class RadioSource(_Quiet, gr.hier_block2):
        def __init__(self, *args, **kwargs):
            gr.hier_block2.__init__(
                self, 'stand-in radio source',
                gr.io_signature(0, 0, 0),
                gr.io_signature(1, 1, gr.sizeof_gr_complex))
            self._rate = 2e6
            self._noise = analog.noise_source_c(analog.GR_GAUSSIAN, 0.01, 0)
            self._tone = analog.sig_source_c(self._rate, analog.GR_COS_WAVE,
                                             self._rate / 20, 0.1, 0)
            self._add = blocks.add_cc()
            self._throttle = blocks.throttle(gr.sizeof_gr_complex, self._rate)
            self.connect(self._noise, (self._add, 0))
            self.connect(self._tone, (self._add, 1))
            self.connect(self._add, self._throttle, self)

        def set_sample_rate(self, channel, rate):
            self._rate = float(rate)
            self._throttle.set_sample_rate(self._rate)
            self._tone.set_sampling_freq(self._rate)
            self._tone.set_frequency(self._rate / 20)

        def get_sample_rate(self, channel=0):
            return self._rate

    soapy.sink = RadioSink
    soapy.source = RadioSource
    audio.sink = lambda *args, **kwargs: blocks.null_sink(gr.sizeof_float)

    real = utils.read_settings

    def read_settings():
        settings = real()
        settings['radio_type'] = 'hackrf'
        settings['theme'] = theme_name
        if media is not None:
            settings['media_directory'] = media
        return settings
    utils.read_settings = read_settings


def child(name, save, no_media=False, theme_name='slate'):
    """One app, in this process. Prints result lines, then exits hard.

    With ``no_media`` the media folder is unset and the window is only run,
    not inspected.
    """
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    os.chdir(ROOT)
    sys.path.insert(0, ROOT)
    import importlib.util
    import time
    from PyQt5 import Qt

    app = Qt.QApplication([])
    install_stand_ins(media='' if no_media else None, theme_name=theme_name)

    spec = importlib.util.spec_from_file_location(name, f'apps/{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)

    dialog = module.ConfigDialog()
    values = dialog.get_values()
    dialog.deleteLater()
    values['radio_type'] = 'hackrf'

    tb = module.main(app=app, config_values=values)
    tb.resize(*SIZE)
    end = time.time() + (NO_MEDIA_SECONDS if no_media else RUN_SECONDS)
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)

    problems = [] if no_media else (inspect(Qt, tb, name, save)
                                    + click_to_move(Qt, tb)
                                    + settings_round_trip(Qt, module, tb, name))
    # A Record button with no media folder has nowhere to write.
    import apps.utils as utils
    record = getattr(tb, 'record_btn', None)
    has_media = os.path.isdir(utils.read_settings()['media_directory'])
    if record is not None and record.isEnabled() != has_media:
        problems.append(f"Record is {'en' if record.isEnabled() else 'dis'}"
                        f"abled with{'' if has_media else ' no'} media folder")
    try:
        tb.stop()
        tb.wait()
    except Exception:
        pass
    for problem in problems:
        print(f"PROBLEM {problem}", flush=True)
    print(f"RESULT {name} {len(problems)}", flush=True)
    sys.stdout.flush()
    os._exit(0)


def commonest(Qt, image, rect):
    """The colour covering most of this part of the image - its background,
    for a label or a canvas, since text and traces are thin."""
    counts = {}
    for y in range(rect.top(), rect.bottom() + 1, 2):
        for x in range(rect.left(), rect.right() + 1, 2):
            if 0 <= x < image.width() and 0 <= y < image.height():
                rgb = image.pixel(x, y) & 0xffffff
                counts[rgb] = counts.get(rgb, 0) + 1
    if not counts:
        return None
    return Qt.QColor(max(counts, key=counts.get))


def close_to(a, b, tolerance):
    return (abs(a.red() - b.red()) <= tolerance
            and abs(a.green() - b.green()) <= tolerance
            and abs(a.blue() - b.blue()) <= tolerance)


def inspect(Qt, tb, name, save):
    """What is wrong with this window, as a list of sentences."""
    from apps import theme
    problems = []
    # Everything in the window, scrolled into view or not: at the laptop's
    # size most windows scroll, and a plot below the fold is still a plot.
    content = tb.top_scroll.widget()
    image = content.grab().toImage().convertToFormat(Qt.QImage.Format_RGB32)
    if save:
        os.makedirs(save, exist_ok=True)
        tb.grab().save(os.path.join(save, f'{name}.png'))
        image.save(os.path.join(save, f'{name}-whole.png'))
    well = Qt.QColor(theme.TOKENS['well'])

    sheet = tb.styleSheet()
    if theme.TOKENS['ground'] not in sheet or 'DisplayPlot' not in sheet:
        problems.append("the window does not carry the flowgraph theme - "
                        "apply_flowgraph_theme(self) is missing from __init__")

    plots = [w for w in content.findChildren(Qt.QWidget)
             if w.isVisible() and w.inherits('DisplayPlot')]
    if not plots:
        problems.append("no plot found")
    canvases = []
    for plot in plots:
        title = plot.metaObject().className()
        for label in plot.findChildren(Qt.QWidget, 'QwtPlotTitle'):
            if label.property('plainText'):
                title = label.property('plainText')
        canvas = next((w for w in plot.findChildren(Qt.QWidget)
                       if w.inherits('QwtPlotCanvas')), None)
        if canvas is None:
            continue
        rect = Qt.QRect(canvas.mapTo(content, Qt.QPoint(0, 0)), canvas.size())
        canvases.append(rect)
        # The well has to show, not dominate: a constellation drawn with
        # lines can cover most of its canvas, and that is the signal.
        inside = [image.pixel(x, y)
                  for y in range(rect.top() + 2, rect.bottom() - 1, 2)
                  for x in range(rect.left() + 2, rect.right() - 1, 2)]
        share = (sum(1 for c in inside if close_to(Qt.QColor(c), well, 3))
                 / max(len(inside), 1))
        if share < 0.10:
            behind = commonest(Qt, image, rect.adjusted(2, 2, -2, -2))
            problems.append(f"'{title}': the canvas is "
                            f"{behind.name() if behind else 'not drawn'}, "
                            f"not the well {well.name()}")
        # A plot reports black for a line it does not have, so only the
        # lines its legend lists are asked about - or just the first.
        lines = max(1, sum(1 for w in plot.findChildren(Qt.QWidget)
                           if w.inherits('QwtLegendLabel')))
        for i in range(1, lines + 1):
            colour = plot.property(f'line_color{i}')
            if isinstance(colour, Qt.QColor) and \
                    contrast(colour, well) < TRACE_CONTRAST:
                problems.append(f"'{title}': trace {i} is {colour.name()}, "
                                f"{contrast(colour, well):.1f}:1 on the well")
        for scale in plot.findChildren(Qt.QWidget):
            if scale.inherits('QwtScaleWidget') and scale.isVisible() and \
                    scale.font().family() != theme.TOKENS['f_ui']:
                problems.append(f"'{title}': axis in "
                                f"{scale.font().family()}, not "
                                f"{theme.TOKENS['f_ui']}")
                break

    # Almost nothing near white outside the plots. The old window was light
    # grey all over; the theme's ink is near white too, but only as text.
    # A trace may be as bright as it likes. On a light theme it is the
    # other way up: its ink is near black, and so is anything still
    # painting a dark theme of its own.
    light = theme.TOKENS['scheme'] == 'light'
    bright = total = 0
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            if any(r.contains(x, y) for r in canvases):
                continue
            c = image.pixel(x, y)
            total += 1
            channels = ((c >> 16) & 255, (c >> 8) & 255, c & 255)
            if (max(channels) < 55) if light else (min(channels) > 200):
                bright += 1
    if total and bright / total > BRIGHT_SHARE:
        far = 'black' if light else 'white'
        problems.append(f"{100 * bright / total:.1f}% of the window is near "
                        f"{far}, against {100 * BRIGHT_SHARE:.0f}% allowed - "
                        f"something is not wearing the theme")

    for label in content.findChildren(Qt.QLabel):
        if not label.isVisible() or not label.isEnabled() or \
                not label.text().strip() or label.width() < 4:
            continue
        rect = Qt.QRect(label.mapTo(content, Qt.QPoint(0, 0)), label.size())
        behind = commonest(Qt, image, rect)
        ink = label.palette().color(Qt.QPalette.WindowText)
        if behind is not None and contrast(ink, behind) < TEXT_CONTRAST:
            text = Qt.QTextDocumentFragment.fromHtml(label.text()).toPlainText()
            problems.append(f"label '{text[:40]}' is {ink.name()} on "
                            f"{behind.name()}, {contrast(ink, behind):.1f}:1")

    if Qt.QApplication.font().family() != theme.TOKENS['f_ui']:
        problems.append(f"the application font is "
                        f"{Qt.QApplication.font().family()}, so the plots' "
                        f"axis titles are not in {theme.TOKENS['f_ui']}")
    return problems


def click_to_move(Qt, tb):
    """Nothing in the window moves until it has been clicked.

    Every slider, spin box and combo box is sent what a pointer passing
    over it sends - a move with no button down, and a turn of the wheel
    each way - and must stay where it was. Then one slider is pressed and
    dragged, which must still move it.
    """
    from PyQt5 import QtCore, QtGui
    app = Qt.QApplication.instance()
    problems = []

    def state(control):
        if isinstance(control, Qt.QComboBox):
            return control.currentIndex()
        return control.value() if hasattr(control, 'value') else control.text()

    def mouse(control, kind, fraction, buttons):
        x = int(control.width() * fraction)
        pos = QtCore.QPointF(x, control.height() / 2)
        button = QtCore.Qt.NoButton if kind == QtCore.QEvent.MouseMove \
            else QtCore.Qt.LeftButton
        app.sendEvent(control, QtGui.QMouseEvent(
            kind, pos, button, buttons, QtCore.Qt.NoModifier))

    def wheel(control, notch):
        centre = QtCore.QPointF(control.width() / 2, control.height() / 2)
        app.sendEvent(control, QtGui.QWheelEvent(
            centre, QtCore.QPointF(control.mapToGlobal(centre.toPoint())),
            QtCore.QPoint(0, 0), QtCore.QPoint(0, 120 * notch),
            QtCore.Qt.NoButton, QtCore.Qt.NoModifier, QtCore.Qt.NoScrollPhase,
            False))

    # Not the scroll bars: scrolling is what the wheel is for.
    controls = [w for w in tb.findChildren(Qt.QWidget) if w.isVisible()
                and isinstance(w, (Qt.QAbstractSlider, Qt.QAbstractSpinBox,
                                   Qt.QComboBox))
                and not isinstance(w, Qt.QScrollBar)]
    for control in controls:
        what = f"a {type(control).__name__} at {state(control)}"
        if control.focusPolicy() == QtCore.Qt.WheelFocus:
            problems.append(f"{what} takes focus from the wheel")
        control.clearFocus()
        before = state(control)
        if isinstance(control, Qt.QAbstractSlider):
            for fraction in (0.1, 0.9):
                mouse(control, QtCore.QEvent.MouseMove, fraction,
                      QtCore.Qt.NoButton)
            if state(control) != before:
                problems.append(f"{what} moved to {state(control)} with the "
                                f"pointer passing over it, nothing clicked")
                continue
        for notch in (1, -1):
            wheel(control, notch)
        if state(control) != before:
            problems.append(f"{what} moved to {state(control)} under the "
                            f"wheel without being clicked")

    sliders = [c for c in controls if isinstance(c, Qt.QAbstractSlider)]
    if sliders:
        slider = sliders[0]
        before = slider.value()
        low = before < (slider.minimum() + slider.maximum()) / 2
        start, end = (0.1, 0.9) if low else (0.9, 0.1)
        mouse(slider, QtCore.QEvent.MouseButtonPress, start,
              QtCore.Qt.LeftButton)
        mouse(slider, QtCore.QEvent.MouseMove, end, QtCore.Qt.LeftButton)
        mouse(slider, QtCore.QEvent.MouseButtonRelease, end,
              QtCore.Qt.NoButton)
        if slider.value() == before:
            problems.append(f"a slider at {before} did not move when pressed "
                            f"and dragged")
    return problems


def first_of(owner, names):
    return next((getattr(owner, n) for n in names if hasattr(owner, n)), None)


def nudged(Qt, control, current):
    """A value the control can take that differs from ``current`` down to
    its finest digit, so that a save or a dialog that rounds is caught."""
    if isinstance(control, Qt.QSlider):
        return 63 if round(current) == 37 else 37
    places = control.decimals()
    step = 10 ** -places if places else 1
    want = current + 1 + 7 * step
    if want > control.maximum():
        want = current - 1 - 7 * step
    return round(want, places)


def settings_round_trip(Qt, module, tb, name):
    """What the window's own controls were left at, back in its dialog."""
    import json
    import tempfile
    from apps.utils import (FLOWGRAPH_POSITION, flowgraph_settings,
                            save_flowgraph_settings)
    saved = getattr(tb, 'SAVED_SETTINGS', None)
    if not saved:
        return ["the window has no SAVED_SETTINGS, so a power change made "
                "in it is lost when it closes"]
    problems = []
    opened = flowgraph_settings(tb)
    moved = {}

    def move(key, attribute):
        control = first_of(tb, WINDOW_CONTROL.get(attribute, ()))
        if control is not None and \
                not isinstance(control, (Qt.QSlider, Qt.QDoubleSpinBox)):
            control = (control.findChild(Qt.QDoubleSpinBox)
                       or control.findChild(Qt.QSlider))
        if control is None:
            problems.append(f"no control in the window sets {attribute}")
            return
        want = nudged(Qt, control, float(getattr(tb, attribute)))
        control.setValue(want)
        if abs(float(getattr(tb, attribute)) - want) > 1e-9:
            problems.append(f"setting the window's control to {want} left "
                            f"{attribute} at {getattr(tb, attribute)}")
        moved[key] = want

    with tempfile.TemporaryDirectory() as folder:
        # Only what was changed in the window is saved: move the first,
        # and the rest must stay out of the file.
        (first, attribute), *rest = saved.items()
        move(first, attribute)
        partial = os.path.join(folder, 'partial')
        save_flowgraph_settings(tb, name, config_dir=partial, since=opened)
        with open(os.path.join(partial, f'{name}_config.json')) as fh:
            written = set(json.load(fh))
        if written != {first}:
            problems.append(f"with only {first} changed, the window saved "
                            f"{sorted(written)}")
        for key, attribute in rest:
            move(key, attribute)

        path = os.path.join(folder, f'{name}_config.json')
        position = {'x': 10, 'y': 20, 'width': 900, 'height': 600,
                    'maximized': True}
        with open(path, 'w') as fh:
            json.dump({FLOWGRAPH_POSITION: position}, fh)
        save_flowgraph_settings(tb, name, config_dir=folder, since=opened)
        with open(path) as fh:
            config = json.load(fh)
        for key, want in moved.items():
            if not isinstance(config.get(key), (int, float)) or \
                    abs(config[key] - want) > 1e-9:
                problems.append(f"the window saved {key} as "
                                f"{config.get(key)!r}, not {want}")

        dialog = module.ConfigDialog()
        dialog.config_dir, dialog.config_file = folder, path
        dialog.load_config()
        for key, want in moved.items():
            got = first_of(dialog, DIALOG_CONTROL.get(key, ())).value()
            if abs(float(got) - want) > 1e-9:
                problems.append(f"the dialog opened with {key} at {got}, "
                                f"not the window's {want}")
        dialog.save_config()
        with open(path) as fh:
            if json.load(fh).get(FLOWGRAPH_POSITION) != position:
                problems.append("the dialog's OK threw away the window's "
                                "position")
        dialog.deleteLater()
    return problems


def geometry_child():
    """Position, size and maximized go into the config and come back out."""
    import json
    import tempfile
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    sys.path.insert(0, ROOT)
    from PyQt5 import Qt
    from apps.utils import (FLOWGRAPH_POSITION, restore_window_geometry,
                            save_window_geometry)

    app = Qt.QApplication([])
    problems = []

    def settle():
        # Long enough for when_exposed's timer to have had its turn.
        import time
        end = time.monotonic() + 0.3
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    def saved(folder):
        with open(os.path.join(folder, 'demo_config.json')) as fh:
            return json.load(fh)

    with tempfile.TemporaryDirectory() as folder:
        # Another key in the same file, which saving must leave alone.
        with open(os.path.join(folder, 'demo_config.json'), 'w') as fh:
            json.dump({'dialog_position': {'x': 1, 'y': 2}}, fh)

        w = Qt.QWidget()
        w.resize(700, 500)
        w.move(120, 90)
        w.show()
        settle()
        save_window_geometry(w, 'demo', config_dir=folder)
        got = saved(folder)[FLOWGRAPH_POSITION]
        want = {'x': 120, 'y': 90, 'width': 700, 'height': 500,
                'maximized': False}
        if got != want:
            problems.append(f"normal window saved as {got}, not {want}")

        w.setWindowState(w.windowState() | Qt.Qt.WindowMaximized)
        settle()
        if not w.isMaximized() or w.width() == 700:
            problems.append("could not maximize a window here, so the "
                            "maximized case was not tested")
        save_window_geometry(w, 'demo', config_dir=folder)
        config = saved(folder)
        got = config[FLOWGRAPH_POSITION]
        want = dict(want, maximized=True)
        if got != want:
            problems.append(f"maximized window saved as {got}, not {want} - "
                            f"it must keep the size to come back to")
        if config.get('dialog_position') != {'x': 1, 'y': 2}:
            problems.append("saving lost the dialog's position")

        # Shown first, as main() does, then restored.
        w2 = Qt.QWidget()
        w2.show()
        restore_window_geometry(w2, 'demo', app, config_dir=folder)
        settle()
        if not w2.isMaximized():
            problems.append("a window saved maximized came back normal")
        w2.setWindowState(w2.windowState() & ~Qt.Qt.WindowMaximized)
        settle()
        got = (w2.pos().x(), w2.pos().y(), w2.width(), w2.height())
        if got != (120, 90, 700, 500):
            problems.append(f"un-maximized, it came back at {got}, not "
                            f"(120, 90, 700, 500)")

        # Saved before there was a flag: it comes back normal.
        with open(os.path.join(folder, 'demo_config.json'), 'w') as fh:
            json.dump({FLOWGRAPH_POSITION: {'x': 60, 'y': 70, 'width': 640,
                                            'height': 480}}, fh)
        w3 = Qt.QWidget()
        w3.setWindowState(Qt.Qt.WindowMaximized)
        w3.show()
        restore_window_geometry(w3, 'demo', app, config_dir=folder)
        settle()
        if w3.isMaximized() or (w3.width(), w3.height()) != (640, 480):
            problems.append("a window saved before the flag existed did not "
                            "come back normal at its saved size")

    for problem in problems:
        print(f"PROBLEM {problem}", flush=True)
    print(f"RESULT geometry {len(problems)}", flush=True)
    os._exit(0)


def run_child(argv_tail, label):
    """Run one check in a process of its own; its result, and what it said."""
    argv = [sys.executable, os.path.abspath(__file__)] + argv_tail
    try:
        out = subprocess.run(argv, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True,
                             timeout=180, cwd=ROOT).stdout
    except subprocess.TimeoutExpired:
        out = ''
    lines = out.splitlines()
    result = next((l for l in lines if l.startswith('RESULT ')), None)
    for line in lines:
        if line.startswith('PROBLEM '):
            print('      ' + line.split(' ', 1)[1])
    if result is None:
        print(f"  FAIL {label:30} no result - its output ended:")
        for line in lines[-8:]:
            print('       ' + line)
        return False
    if result.split()[-1] != '0':
        print(f"  FAIL {label:30} {result.split()[-1]} problem(s)")
        return False
    print(f"  ok   {label}")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('apps', nargs='*', help='only these modules')
    parser.add_argument('--save', metavar='DIR',
                        help='write a PNG of every window here')
    parser.add_argument('--child', help=argparse.SUPPRESS)
    parser.add_argument('--geometry', action='store_true',
                        help=argparse.SUPPRESS)
    parser.add_argument('--no-media', action='store_true',
                        help=argparse.SUPPRESS)
    parser.add_argument('--theme', default='slate',
                        choices=list(THEMES),
                        help='the theme to check the windows in')
    args = parser.parse_args()
    if args.geometry:
        geometry_child()
        return 0
    if args.child:
        child(args.child, args.save, args.no_media, args.theme)
        return 0

    failed = []
    print('where a window comes back')
    if not run_child(['--geometry'], 'position, size, maximized'):
        failed.append('geometry')

    print(f'\nthe windows, with no radio, in {args.theme}')
    names = args.apps or MODULES
    for name in names:
        tail = ['--child', name, '--theme', args.theme]
        if args.save:
            tail += ['--save', os.path.abspath(args.save)]
        if not run_child(tail, name):
            failed.append(name)

    print('\nthe windows, with no media folder')
    for name in names:
        if not run_child(['--child', name, '--no-media',
                          '--theme', args.theme], name):
            failed.append(f'{name} (no media)')
    print(f"\n{len(names)} windows checked, with media and without")
    if failed:
        print("RESULT: FAIL - " + ', '.join(failed))
        return 1
    print("all checks passed")
    return 0


if __name__ == '__main__':
    sys.exit(main())
