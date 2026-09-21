import json
import os
import sys
import tempfile
import time
from PyQt5 import Qt  #type: ignore
from PyQt5.QtCore import (QObject, QEvent, QRect, QTimer,  #type: ignore
                          Qt as QtNs, pyqtSignal)

from apps import theme


# --- Buffers ----------------------------------------------------------------

#: GNU Radio allocates stream buffers in whole memory pages.
BUFFER_PAGE = 4096


def page_aligned_items(item_size, page=BUFFER_PAGE):
    """How many items of this size fill a whole number of pages.

    GNU Radio rounds every buffer up to a page boundary, and warns when the
    size it was asked for is not already there:

        buffer_double_mapped :warning: allocate_buffer: tried to allocate
        316 items of size 207. Due to alignment requirements 4096 were
        allocated.

    The ATSC chain trips this four times on every run, because a transport
    packet is 188 bytes and a Reed-Solomon one is 207 - neither a power of
    two - so the line is printed at WARN, four times, every time, about
    something nobody can do anything about and which is not a fault.

    Asking for the aligned count up front silences it without hiding
    anything: the allocation is identical, since that is what GNU Radio was
    going to allocate anyway. 207 needs 4096 items, 188 needs 1024.
    """
    from math import gcd
    return page // gcd(int(item_size), page)


def align_output_buffer(block, port, item_size):
    """Ask a block for a page-aligned output buffer. See above."""
    try:
        block.set_min_output_buffer(port, page_aligned_items(item_size))
    except Exception:
        # Never worth failing a flowgraph over a log line.
        pass


# --- Tuning -----------------------------------------------------------------

#: The step every frequency control moves by - an arrow key, a notch of
#: the slider. 0.1 MHz keeps the slider a manageable length.
FREQ_STEP_MHZ = 0.1

#: How finely a frequency is *held*: five decimals of a megahertz, 10 Hz.
#: A typed or saved value keeps every digit up to this, whatever the step.
#: Five because that is the finest any flowgraph window's own counter
#: takes - GNU Radio's ``Range`` gives its counter two decimals more than
#: its step, and the NTSC transmitter's steps in 0.001 - so a frequency
#: tuned in a window comes back in its dialog exactly
#: (`save_flowgraph_settings`). Held to 0.1 MHz, as it once was, 433.92
#: came back as 433.9.
FREQ_DECIMALS = 5


class TrimmedSpinBox(Qt.QDoubleSpinBox):
    """A spin box that shows only the decimals its value needs.

    All five always shown would put 533.00000 in front of anyone tuning a
    television channel; this shows 533.0, and 433.92 as 433.92. At least
    one decimal stays, as the box always had. Typing takes all five.
    """

    def textFromValue(self, value):
        text = super().textFromValue(value)
        point = self.locale().decimalPoint()
        if point in text:
            whole, _, fraction = text.partition(point)
            text = whole + point + (fraction.rstrip('0') or '0')
        return text

    def sizeHint(self):
        # Wide enough for every digit it can hold, not only the trimmed
        # maximum Qt measures - or a typed 433.92345 scrolls out of sight.
        hint = super().sizeHint()
        metrics = self.fontMetrics()
        full = self.locale().toString(self.maximum(), 'f', self.decimals())
        hint.setWidth(hint.width() + metrics.horizontalAdvance(full)
                      - metrics.horizontalAdvance(
                          self.textFromValue(self.maximum())))
        return hint


class FrequencyChooser(Qt.QWidget):
    """Tune to an exact frequency: type it, pick a channel, or drag to it.

    **A plain QSlider cannot do this job, and it was not obvious why.**
    ``atscXmitter`` had one running 50 to 2200 in whole megahertz: 2150
    positions rendered across a few hundred pixels, so one pixel of mouse
    travel is about seven megahertz and most frequencies are not reachable
    at all. Asked for 533 MHz - the channel this bench uses - the nearest
    the mouse could get was 539, and nothing about the control said so.

    So there are three ways in, and they stay in step with each other:

    - a **spin box**, which is the only one that can be exact, and which
      takes a typed number to ``FREQ_DECIMALS`` places;
    - a **channel picker**, when the caller passes a channel plan, because
      "UHF 24" is how anyone actually thinks about a television channel;
    - a **slider** for sweeping, now stepping in tenths of a megahertz,
      with its page step set to one 6 MHz channel so PageUp and PageDown
      walk the band a channel at a time.

    ``valueChanged`` carries megahertz as a float and fires once per real
    change, whichever of the three caused it.
    """

    valueChanged = pyqtSignal(float)

    def __init__(self, minimum=50.0, maximum=2200.0, value=None, channels=None,
                 label="Center Frequency (MHz):", parent=None):
        super().__init__(parent)
        self._min = float(minimum)
        self._max = float(maximum)
        self._value = None

        layout = Qt.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.channel_combo = None
        if channels:
            row = Qt.QHBoxLayout()
            row.addWidget(Qt.QLabel("Channel:"))
            self.channel_combo = Qt.QComboBox()
            # A frequency between channels is a legitimate thing to want, so
            # the list says so rather than snapping to the nearest channel.
            self.channel_combo.addItem("(not on a channel)", None)
            for _number, centre, caption in channels:
                self.channel_combo.addItem(caption, float(centre))
            self.channel_combo.currentIndexChanged.connect(self._channel_picked)
            row.addWidget(self.channel_combo, 1)
            layout.addLayout(row)

        row = Qt.QHBoxLayout()
        row.addWidget(Qt.QLabel(label))
        self.spin = TrimmedSpinBox()
        self.spin.setDecimals(FREQ_DECIMALS)
        self.spin.setSingleStep(FREQ_STEP_MHZ)
        self.spin.setRange(self._min, self._max)
        # Without this the box emits on every keystroke, so typing "533"
        # tunes through 5 MHz and 53 MHz on the way.
        self.spin.setKeyboardTracking(False)
        self.spin.valueChanged.connect(self.setValue)
        row.addWidget(self.spin)
        row.addStretch()
        layout.addLayout(row)

        self.slider = Qt.QSlider(QtNs.Horizontal)
        self.slider.setRange(self._steps(self._min), self._steps(self._max))
        self.slider.setSingleStep(1)                  # an arrow key: 0.1 MHz
        self.slider.setPageStep(int(6.0 / FREQ_STEP_MHZ))   # a page: 6 MHz
        self.slider.valueChanged.connect(
            lambda steps: self.setValue(steps * FREQ_STEP_MHZ))
        layout.addWidget(self.slider)

        self.setValue(self._min if value is None else value)

    @staticmethod
    def _steps(mhz):
        return int(round(float(mhz) / FREQ_STEP_MHZ))

    def value(self):
        return self._value

    def setValue(self, mhz):
        try:
            mhz = round(min(max(float(mhz), self._min), self._max),
                        FREQ_DECIMALS)
        except (TypeError, ValueError):
            return
        # Half the finest digit, not half a step: 433.92 after 433.9 is a
        # change.
        if self._value is not None and \
                abs(mhz - self._value) < 0.5 * 10 ** -FREQ_DECIMALS:
            return
        self._value = mhz
        self._refresh()
        self.valueChanged.emit(mhz)

    def _refresh(self):
        """Put all three controls on the current value without feedback."""
        for widget, setter, new in (
                (self.spin, self.spin.setValue, self._value),
                (self.slider, self.slider.setValue, self._steps(self._value))):
            widget.blockSignals(True)
            setter(new)
            widget.blockSignals(False)
        if self.channel_combo is None:
            return
        index = 0
        for k in range(1, self.channel_combo.count()):
            centre = self.channel_combo.itemData(k)
            if centre is not None and abs(centre - self._value) < 0.05:
                index = k
                break
        self.channel_combo.blockSignals(True)
        self.channel_combo.setCurrentIndex(index)
        self.channel_combo.blockSignals(False)

    def _channel_picked(self, _index):
        centre = self.channel_combo.currentData()
        if centre is not None:
            self.setValue(centre)

    def set_channels(self, channels):
        """Swap in another channel plan, for a dialog whose standard changes.

        Only a chooser built with a plan has a channel row to fill; an empty
        plan greys the row out rather than removing it, so the dialog does
        not jump about as the standard changes.
        """
        if self.channel_combo is None:
            return
        self.channel_combo.blockSignals(True)
        self.channel_combo.clear()
        self.channel_combo.addItem(
            "(not on a channel)" if channels else "(no channel plan)", None)
        for _number, centre, caption in channels or ():
            self.channel_combo.addItem(caption, float(centre))
        self.channel_combo.setEnabled(bool(channels))
        self.channel_combo.blockSignals(False)
        self._refresh()


class DialogGeometryTracker(QObject):
    """Event filter that captures dialog geometry the moment it is hidden.

    Install before calling exec_() and read ``captured`` afterwards to get
    the last valid position/size regardless of how the dialog was closed
    (OK, Cancel, or window-close button).
    """
    def __init__(self, dialog):
        super().__init__(dialog)
        self.captured = None
        dialog.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Hide and self.captured is None:
            self.captured = {
                'x': obj.pos().x(),
                'y': obj.pos().y(),
                'width': obj.width(),
                'height': obj.height(),
            }
        return False


def centre_on(dialog, window, app=None):
    """Put a dialog in the middle of the window that opened it.

    This is where a dialog goes when nothing has been saved for it yet.
    The config dialogs are built with no parent - each app's
    ``ConfigDialog()`` takes none - so Qt has nothing to centre them on and
    would drop them in the middle of the *screen*, which on a wide desktop
    is nowhere near the launcher. The settings dialog does have a parent
    and Qt already centres that one.

    Called before the dialog is shown, so its size has to be asked for
    rather than read: ``adjustSize`` settles it against the layout
    ``tidy_dialog`` built and the minimum width ``apply_dark_theme`` sets.

    The result is clamped into the screen the launcher is actually on. A
    launcher parked against an edge, or one taller than the dialog's screen
    has room for, would otherwise centre part of the dialog off it - the
    same failure ``geometry_is_reachable`` guards the *restored* positions
    against.
    """
    dialog.adjustSize()
    size = dialog.size()
    centre = window.frameGeometry().center()
    x = centre.x() - size.width() // 2
    y = centre.y() - size.height() // 2

    instance = app or Qt.QApplication.instance()
    screen = None
    if instance is not None:
        at = getattr(instance, 'screenAt', None)
        screen = at(centre) if at else None
        if screen is None:
            screen = instance.primaryScreen()
    if screen is not None:
        area = screen.availableGeometry()
        if size.width() <= area.width():
            x = max(area.left(), min(x, area.right() - size.width() + 1))
        else:
            x = area.left()
        if size.height() <= area.height():
            y = max(area.top(), min(y, area.bottom() - size.height() + 1))
        else:
            y = area.top()
    dialog.move(x, y)
    return x, y


#: Where a flowgraph window's own geometry lives, in the same per-app file
#: as ``dialog_position`` and in the same shape - plus ``maximized``, as the
#: launcher keeps for its own window.
FLOWGRAPH_POSITION = 'flowgraph_position'


def _app_config_path(module_name, config_dir='config'):
    return os.path.join(config_dir, f"{module_name}_config.json")


def update_app_config(path, changes, remove=()):
    """Atomically merge keys into a JSON config, keeping every other one.

    Per-app files are shared by the config dialog, launcher and flowgraph
    window. The global settings file is shared by the launcher and Settings.
    A direct whole-file write in any of those places can throw away a key
    another window owns, so all callers use this merge-and-replace path. A
    file that cannot be read is started afresh rather than stopping the save.

    ``remove`` names keys to drop from the file, for retiring a setting the
    code no longer reads - ``ip_addresses`` and ``radio_mode``, left behind
    by the multi-radio launcher. A merge alone cannot delete a key, and the
    whole-file write that could would take another window's with it.
    """
    config = {}
    try:
        with open(path) as fh:
            config = json.load(fh)
        if not isinstance(config, dict):
            config = {}
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"Could not read {path}, so it is being started afresh: {exc}",
              file=sys.stderr)
        config = {}
    for key in remove:
        config.pop(key, None)
    config.update(changes)
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w') as fh:
        json.dump(config, fh, indent=4)
    os.replace(tmp, path)
    return config


def flowgraph_settings(window):
    """What the controls a flowgraph names in ``SAVED_SETTINGS`` stand at.

    Keyed by the dialog's config key. A float is rounded to 1 Hz of a
    megahertz, which clears the float noise a counter's arrows leave -
    433.93000000000006 - and one that is then whole is written as an int,
    as the dialog's own sliders write it.
    """
    values = {}
    for key, attribute in (getattr(window, 'SAVED_SETTINGS', None) or {}).items():
        value = getattr(window, attribute, None)
        if value is None:
            continue
        if isinstance(value, float):
            value = round(value, 6)
            if value.is_integer():
                value = int(value)
        values[key] = value
    return values


def save_flowgraph_settings(window, module_name, config_dir='config',
                            since=None):
    """Keep what a flowgraph window's own controls were left at.

    Every window has a power control of its own - gain, on a receiver - and
    a centre frequency, and a change made there used to last only as long
    as the window: the dialog opened next time on the value it had been
    given, and pressing OK put that back on the air. Each flowgraph class
    names what to keep in ``SAVED_SETTINGS``, from the dialog's config key
    to the attribute the window's setter keeps current -
    ``{'power_level': 'rfPwr', 'center_freq': 'cf'}`` on a transmitter - so
    the dialog, which already reads those keys, opens on the window's last
    values.

    ``since`` is ``flowgraph_settings`` as the window opened, and only
    what has changed from it is written. What was not touched is left as
    the dialog saved it - so two windows of one app open at once, in multi
    mode, cannot put back each other's unchanged values. Saved from the
    close hook both launchers put on the window, beside
    ``save_window_geometry``.
    """
    changes = flowgraph_settings(window)
    if since is not None:
        changes = {key: value for key, value in changes.items()
                   if since.get(key) != value}
    if not changes:
        return False
    try:
        update_app_config(_app_config_path(module_name, config_dir), changes)
        return True
    except Exception as exc:
        print(f"Could not save the window's settings for {module_name}: "
              f"{exc}", file=sys.stderr)
        return False


def normal_geometry(window):
    """Where a window sits when it is not maximized, as x, y, w, h.

    In the same terms as ``pos()`` and ``size()`` - the frame's corner and
    the inside's size - because that is what a restore hands back to
    ``move()`` and ``resize()``. Maximized, those two report the whole
    screen, so the size to come back to is Qt's ``normalGeometry()``, which
    measures the inside's corner instead and is moved out by the frame;
    saved as it stands, every maximized close would put the window a title
    bar lower. None when Qt does not know it, and whatever was saved last
    should then be kept.
    """
    if not window.isMaximized():
        return (window.pos().x(), window.pos().y(),
                window.width(), window.height())
    normal = window.normalGeometry()
    if not normal.isValid():
        return None
    frame = window.geometry().topLeft() - window.frameGeometry().topLeft()
    return (normal.x() - frame.x(), normal.y() - frame.y(),
            normal.width(), normal.height())


def when_exposed(window, action, timeout_ms=3000):
    """Run ``action`` once the window is really on the screen.

    ``show()`` only asks for a window; the window manager puts it up a
    little later, with its title bar round it. Two things done in between
    go wrong on GNOME, both measured:

    - **A move lands a title bar too high.** Until the frame is on, Qt does
      not know how thick it is, so ``move()`` puts the *inside* of the
      window where the frame's corner was meant to go. A flowgraph window
      restored straight after ``main()`` had shown it came back 37 px
      higher every time it was opened.
    - **A maximize is lost, some of the time.** Before the window is up, Qt
      asks for maximized by setting a property the window manager only
      reads when it maps the window; if it has already mapped it, the
      request is never seen. The same restore came back maximized on one
      run and normal on the next.

    Waiting for Qt to report the window exposed fixes both. A window that
    never is - minimized, or started over SSH on Windows, where session 0
    shows nothing - gets the action anyway after ``timeout_ms``.
    """
    deadline = time.monotonic() + timeout_ms / 1000

    def attempt():
        handle = window.windowHandle()
        if (handle is not None and handle.isExposed()) \
                or time.monotonic() > deadline:
            action()
        else:
            QTimer.singleShot(10, attempt)
    attempt()


def maximize_when_shown(window):
    """Maximize a window that is on its way onto the screen.

    **On GNOME a window cannot be maximized before it is on screen** - see
    ``when_exposed``. Qt's own ``showMaximized()`` fails the same way. The
    cost is a glimpse of the normal-sized window first.
    """
    when_exposed(window, lambda: window.setWindowState(
        window.windowState() | QtNs.WindowMaximized))


def save_window_geometry(window, module_name, config_dir='config',
                         key=FLOWGRAPH_POSITION):
    """Remember where a flowgraph window was, beside its dialog's position.

    Its position, its size and whether it was maximized. A maximized
    window's position and size are its *normal* ones (``normal_geometry``),
    so un-maximizing it after it comes back gives the size it had before.

    **Qt already does this and it does not survive a change of monitor.**
    Every app calls ``saveGeometry``/``restoreGeometry`` against
    ``QSettings("GNU Radio", <app>)``, and the saving half works - the
    stored blobs hold real geometries. The restoring half has two problems.
    It runs at the top of each ``__init__``, before any of the widgets
    exist, so the layout can overrule the size afterwards; and Qt 5's
    ``restoreGeometry`` compares the screen width it was saved on against
    the current one and **returns false without restoring anything** if
    they differ by more than a quarter. The saved blobs here were written
    on screens 2880 and 3840 wide, so that check had been firing.

    So the geometry is kept the way the launcher keeps its own window's and
    the config dialog's: plain x, y, width and height in the app's own
    JSON, applied after the window is up, and only when it would land
    somewhere still reachable.
    """
    path = _app_config_path(module_name, config_dir)
    try:
        position = {}
        if os.path.exists(path):
            with open(path) as fh:
                position = dict(json.load(fh).get(key) or {})
        normal = normal_geometry(window)
        if normal is not None:
            position.update(zip(('x', 'y', 'width', 'height'), normal))
        position['maximized'] = window.isMaximized()
        update_app_config(path, {key: position})
        return True
    except Exception as exc:
        print(f"Could not save the window position for {module_name}: {exc}",
              file=sys.stderr)
        return False


def restore_window_geometry(window, module_name, app=None,
                            config_dir='config', key=FLOWGRAPH_POSITION):
    """Put a flowgraph window back where it was. See `save_window_geometry`.

    Call it *after* the window has been shown: that is the whole point of
    doing this rather than leaving it to ``restoreGeometry`` at the top of
    ``__init__``. The geometry goes on once the window is exposed
    (``when_exposed``), which on a window that has only just been shown is
    a few event-loop turns later; a window left maximized is put at its
    normal geometry and then maximized, so un-maximizing gives that back.
    Returns whether there was anything saved to apply.
    """
    path = _app_config_path(module_name, config_dir)
    try:
        if not os.path.exists(path):
            return False
        with open(path) as fh:
            position = json.load(fh).get(key)
        if not position:
            return False
    except Exception as exc:
        print(f"Could not read the window position for {module_name}: "
              f"{exc}", file=sys.stderr)
        return False
    app = app or Qt.QApplication.instance()

    def apply():
        try:
            # Normal first, or the resize and move below would land on a
            # window that is still maximized - which Qt's own
            # restoreGeometry, at the top of the app's __init__, may
            # already have made it.
            window.setWindowState(window.windowState() & ~QtNs.WindowMaximized)
            # Size first, so the reachability test and the move both work on
            # the geometry the window will actually have - as the launcher
            # does.
            if 'width' in position and 'height' in position:
                width, height = int(position['width']), int(position['height'])
                if app is not None:
                    screen = app.primaryScreen().availableGeometry()
                    width = min(width, screen.width())
                    height = min(height, screen.height())
                window.resize(width, height)
            if 'x' in position and 'y' in position \
                    and geometry_is_reachable(app, position):
                window.move(int(position['x']), int(position['y']))
            if position.get('maximized'):
                window.setWindowState(window.windowState()
                                      | QtNs.WindowMaximized)
        except Exception as exc:
            print(f"Could not restore the window position for {module_name}: "
                  f"{exc}", file=sys.stderr)

    when_exposed(window, apply)
    return True


def geometry_is_reachable(app, position, minimum=(160, 40)):
    """True if a window restored at this saved geometry could still be grabbed.

    The point of validating a saved position is that the user can reach the
    title bar again, not that the window fits neatly. The per-site checks this
    replaces got three things wrong:

    - They measured against the dialog's ``width()`` *before* it was shown,
      which is Qt's 640x480 default and has nothing to do with the dialog - its
      real size hint is more like 515x631. On a 1366x768 laptop that made the
      test ``0 <= x <= 726``, so any dialog parked on the right half of the
      screen was judged invalid, reopened at the default position, and had that
      default saved back over the real one. It looked like the position was
      never being remembered.
    - They demanded the whole window fit inside the screen, throwing away a
      window deliberately parked against an edge.
    - They only looked at the primary screen, so a window on a second monitor
      was never valid - which is the normal case on a multi-head desk.

    Sizes come from the saved geometry, and ``availableGeometry`` is used so the
    taskbar does not count as usable space.
    """
    try:
        rect = QRect(int(position['x']), int(position['y']),
                     int(position.get('width') or minimum[0]),
                     int(position.get('height') or minimum[1]))
    except (KeyError, TypeError, ValueError):
        return False
    for screen in app.screens():
        overlap = screen.availableGeometry().intersected(rect)
        if overlap.width() >= minimum[0] and overlap.height() >= minimum[1]:
            return True
    return False


def adopt_legacy_config(config_dir, legacy_name, config_file):
    """Fold a config saved under an old filename into the module-named one.

    Four dialogs used to save under a name that did not match their module -
    fmAudioRecordedGenerator wrote fmAudioGenerator_config.json, atscXmitter
    wrote atsc_config.json, and so on - while the launcher has always written
    dialog_position to ``<module>_config.json``. Settings and window geometry
    therefore ended up in two separate files for those four. Renaming on its own
    would have orphaned whatever was already saved, so the old file is merged in
    on first run instead.

    Keys from the launcher-written file win, because that is the one that may
    already hold ``dialog_position``; the legacy file only ever held the
    dialog's own settings, so in practice they do not overlap. The old file is
    then renamed aside rather than deleted - that makes the migration a one
    shot, and leaves the original recoverable if a merge ever goes wrong.
    """
    legacy = os.path.join(config_dir, legacy_name)
    if not os.path.exists(legacy):
        return
    try:
        with open(legacy) as f:
            merged = json.load(f)
        if not isinstance(merged, dict):
            return
        if os.path.exists(config_file):
            with open(config_file) as f:
                current = json.load(f)
            if isinstance(current, dict):
                merged.update(current)
        update_app_config(config_file, merged)
        os.replace(legacy, legacy + '.migrated')
    except Exception as e:
        # A failed migration must never stop the dialog opening. The app falls
        # back to defaults and the legacy file is left where it is.
        print(f"Could not migrate {legacy_name}: {e}")


#This function is called to apply the theme to the launcher
def apply_launcher_theme(widget):
    """Paint the launcher window from the shared design tokens.

    The palette, the type scale and the faces live in ``apps/theme.py``,
    which the dialogs and the flowgraph windows read too, so all of them
    move together when a colour is edited.
    """
    use_saved_theme()
    theme.load_fonts()
    widget.setStyleSheet(theme.launcher_qss())
    match_title_bar(widget)


def use_saved_theme():
    """Put the saved theme in force, and return its name.

    It is ``theme`` in ``config/window_settings.json``, which the disc in
    the launcher's header writes. Every ``apply_*_theme`` calls this
    first, so a dialog or a flowgraph window opened after a change wears
    the new one - including one started by ``apps/_run.py``, which is a
    process of its own that never saw the change happen. A window already
    open keeps what it was painted in.
    """
    return theme.use(read_settings().get('theme'))

# --- Dialog layout ----------------------------------------------------------

#: The rhythm every config dialog is laid out on: the gap around the edge,
#: and the gap between one control and the next.
DIALOG_MARGIN = 12
DIALOG_SPACING = 8


def _dialog_layout(widget):
    """The widget's layout, whether or not the method has been shadowed.

    Every ConfigDialog here does ``self.layout = Qt.QVBoxLayout(self)``, which
    replaces the ``layout()`` method with the layout object. Both spellings
    have to work.
    """
    attr = getattr(widget, 'layout', None)
    if isinstance(attr, Qt.QLayout):
        return attr
    try:
        return widget.layout()
    except TypeError:
        return None


#: Widgets that arrange their own insides. Do not reach into these at all:
#: a combo box's list is a view of its own, and a tool bar and a button box
#: lay themselves out.
_SELF_CONTAINED = (Qt.QAbstractScrollArea, Qt.QAbstractItemView,
                   Qt.QComboBox, Qt.QDialogButtonBox, Qt.QToolBar)


def _collect(layout, rows, captions, nested, group=False):
    """Find the labels and nested layouts under this one.

    A label that leads a ``QHBoxLayout`` is a *row* label - it names the
    control beside it. A label sitting on its own in a vertical layout is a
    *caption* - it names the control underneath it.

    Inside a group box only the layouts are collected. Its contents are
    indented from the dialog's own column by the frame, so pulling its rows
    into that column would push them back out of the box.
    """
    if isinstance(layout, Qt.QHBoxLayout) and not group:
        widgets = [layout.itemAt(i).widget() for i in range(layout.count())]
        widgets = [w for w in widgets if w is not None]
        if len(widgets) > 1 and isinstance(widgets[0], Qt.QLabel):
            rows.append(widgets[0])
    vertical = isinstance(layout, Qt.QVBoxLayout)
    for i in range(layout.count()):
        item = layout.itemAt(i)
        child = item.layout()
        if child is not None:
            nested.append(child)
            _collect(child, rows, captions, nested, group)
            continue
        w = item.widget()
        if w is None or isinstance(w, _SELF_CONTAINED):
            continue
        if vertical and i and isinstance(w, Qt.QLabel) and not group:
            captions.append(w)
            continue
        inner = _dialog_layout(w)
        if inner is not None:
            if isinstance(w, Qt.QGroupBox):
                # The box's own layout keeps its margins, or the frame cuts
                # through the text - but the rows inside it still get
                # straightened.
                _collect(inner, rows, captions, nested, group=True)
                continue
            # A plain QWidget used only to hold a row - the containers that
            # exist so an opacity effect has something to apply to - keeps
            # its layout's default 9 px margin otherwise, and that indents
            # the row it holds out of line with the rest of the dialog.
            nested.append(inner)
            _collect(inner, rows, captions, nested, group)


def tidy_dialog(dialog):
    """Straighten a hand-built dialog. Four fixes, all of them layout.

    The fifteen config dialogs are each assembled by hand out of
    ``QVBoxLayout`` and ``QHBoxLayout``, and they were all crooked in the
    same ways - which is what makes this worth doing centrally rather than
    in fifteen places.

    - **The label in a row sat four or five pixels below the control beside
      it.** The stylesheet gave every ``QLabel`` a 10 px top margin, to
      space a caption off whatever was above it. Inside a row that margin
      pushes the *text* down within the label's own rectangle while the spin
      box or slider next to it stays centred, so the box reads as sitting
      high - measured on every row of all fifteen dialogs. The margin is
      gone from the stylesheet and the gap comes from layout spacing now,
      which is what layout spacing is for.
    - **Sliders started at a different x in every row**, because each one
      began wherever its label's text happened to end: four different
      positions in one dialog. Every label that leads a row is given the
      width of the widest of them, so the controls line up in a column. It
      also stops the slider shifting sideways as a live value in the label
      changes width - "Power Level: 5%" to "Power Level: 100%" moved it.
    - **A nested row was indented**, since sub-layouts keep their own
      default margins - "Sine Frequency" sat ten pixels right of every
      other label in the same dialog.
    - **Short dialogs spread their contents out.** A forced 400 px minimum
      height left the receivers half empty, and a ``QVBoxLayout`` hands the
      slack to whatever can grow, which is the labels: the gaps between
      controls came out uneven. A stretch before the button box collects
      it in one place instead.
    """
    if not isinstance(dialog, Qt.QWidget):
        return                      # apply_dark_theme is also called on a
                                    # QApplication, which has no layout.
    top = _dialog_layout(dialog)
    if top is None:
        return
    top.setContentsMargins(DIALOG_MARGIN, DIALOG_MARGIN,
                           DIALOG_MARGIN, DIALOG_MARGIN)
    top.setSpacing(DIALOG_SPACING)

    rows, captions, nested = [], [], []
    _collect(top, rows, captions, nested)
    for layout in nested:
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(DIALOG_SPACING)
    if rows:
        width = max(label.sizeHint().width() for label in rows)
        for label in rows:
            label.setMinimumWidth(width)
            # Fixed, or the column still moves: a label is Preferred by
            # default, so in a row with a spin box - which is Expanding -
            # the two share the slack and that row's control starts 26 px
            # right of every other. Fixed caps the label at its own text,
            # and the minimum above brings the short ones up to the column.
            label.setSizePolicy(Qt.QSizePolicy.Fixed,
                                label.sizePolicy().verticalPolicy())
    for label in captions:
        # A caption belongs to the control below it, so give it more room
        # above than below. This is the grouping the old stylesheet margin
        # was after - it just applied it to every label, including the ones
        # in a row, which is what knocked those crooked.
        label.setContentsMargins(0, DIALOG_SPACING, 0, 0)

    if isinstance(top, Qt.QVBoxLayout):
        index = top.count()
        for i in range(top.count()):
            if isinstance(top.itemAt(i).widget(), Qt.QDialogButtonBox):
                index = i
                break
        top.insertStretch(index, 1)


#: The repository's icons, found from this file so that it does not matter
#: which directory the app was started from.
ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'icons')


def icon_url(name):
    """An icons/ path in the form a Qt stylesheet url() wants.

    Forward slashes on every platform - a Windows backslash is an escape
    character to the stylesheet parser, and the rule is dropped silently.
    """
    return os.path.join(ICON_DIR, name).replace('\\', '/')


def themed_icon_url(name, colour):
    """An icons/ picture redrawn in ``colour``, as :func:`icon_url` gives it.

    The spin arrows and the tick are pictures, because a stylesheet cannot
    draw them (see ``tidy_dialog``), and a picture has its colour baked
    in: light arrows vanish on Reading Room's paper, and the tick, drawn in
    the ground's colour to sit on an ink-filled box, vanishes on its dark
    one. So the shape is kept and the colour replaced, and the result is
    written once to a folder in the temp directory under a name that
    carries the colour - a palette edited later gets a new file rather
    than a stale one. If it cannot be written, the shipped picture is used
    as it is, which is right for Slate.
    """
    stem, ext = os.path.splitext(name)
    user = str(os.getuid()) if hasattr(os, 'getuid') else ''
    folder = os.path.join(tempfile.gettempdir(), 'rfbench-icons-' + user)
    target = os.path.join(folder, '%s-%s%s'
                          % (stem, colour.lstrip('#').lower(), ext))
    if not os.path.exists(target):
        image = Qt.QImage(os.path.join(ICON_DIR, name))
        if image.isNull():
            return icon_url(name)
        image = image.convertToFormat(Qt.QImage.Format_ARGB32_Premultiplied)
        painter = Qt.QPainter(image)
        # The picture's own alpha, with the new colour poured into it.
        painter.setCompositionMode(Qt.QPainter.CompositionMode_SourceIn)
        painter.fillRect(image.rect(), Qt.QColor(colour))
        painter.end()
        try:
            os.makedirs(folder, exist_ok=True)
            # Written aside and moved in, so two apps starting at once
            # cannot hand Qt a half-written file.
            partial = '%s.%d.tmp' % (target, os.getpid())
            if not image.save(partial, 'PNG'):
                return icon_url(name)
            os.replace(partial, target)
        except OSError:
            return icon_url(name)
    return target.replace('\\', '/')


def match_title_bar(window):
    """Have Windows draw this window's title bar in the theme.

    Windows 10 and 11 take a dark-or-light switch per window, and
    Windows 11 takes an exact colour for the caption, its text and the
    border as well. Here those are the theme's ground, ink and rule, so on
    the launcher the title bar runs straight on into the rail. Windows 10
    refuses the colours and keeps the switch.

    **Linux is left alone, because it cannot be done there.** On GNOME 46 -
    Ubuntu 24.04, which both Linux machines here run - title bars are drawn
    by ``mutter-x11-frames``, which follows the desktop's single light or
    dark setting and reads no hint from the window. Measured:
    ``_GTK_THEME_VARIANT=dark``, which older GNOME honoured, set on a
    window while it was up or before it was mapped, left the bar #e8e8e8.

    A window not yet created is done when it is first shown, rather than
    by asking for its handle now, which would create it early.
    """
    if not sys.platform.startswith('win') or \
            not isinstance(window, Qt.QWidget) or not window.isWindow():
        return
    if window.testAttribute(QtNs.WA_WState_Created):
        _paint_windows_title_bar(window)
    elif not window.property('_title_bar_follows'):
        window.setProperty('_title_bar_follows', True)
        window.installEventFilter(_TitleBarOnShow(window))


class _TitleBarOnShow(QObject):
    """Paint a window's title bar as it is shown - every time, since
    single mode hides the launcher and shows it again."""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Show:
            _paint_windows_title_bar(obj)
        return False


def _paint_windows_title_bar(window):
    try:
        import ctypes
        from ctypes import byref, c_int, c_uint, c_void_p, sizeof
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute.argtypes = [c_void_p, c_uint, c_void_p, c_uint]
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long
        hwnd = c_void_p(int(window.winId()))

        def colour(token):
            # A COLORREF is 0x00BBGGRR.
            value = theme.TOKENS[token].lstrip('#')
            r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
            return c_uint(b << 16 | g << 8 | r)

        dark = c_int(1 if theme.TOKENS['scheme'] == 'dark' else 0)
        # DWMWA_USE_IMMERSIVE_DARK_MODE: 20 from Windows 10 20H1, 19 before.
        if dwm.DwmSetWindowAttribute(hwnd, 20, byref(dark), sizeof(dark)) != 0:
            dwm.DwmSetWindowAttribute(hwnd, 19, byref(dark), sizeof(dark))
        # Windows 11's border, caption and caption text colours.
        for attribute, token in ((34, 'rule'), (35, 'ground'), (36, 'ink')):
            value = colour(token)
            dwm.DwmSetWindowAttribute(hwnd, attribute, byref(value),
                                      sizeof(value))
        if window.isVisible():
            # A title bar already on screen keeps its old colours until
            # something makes Windows redraw the frame.
            user32 = ctypes.windll.user32
            user32.SetWindowPos.argtypes = [c_void_p, c_void_p, c_int, c_int,
                                            c_int, c_int, c_uint]
            # NOSIZE | NOMOVE | NOZORDER | NOACTIVATE | FRAMECHANGED
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0037)
    except Exception as exc:
        # A title bar is not worth failing a window over.
        print(f"Could not colour the title bar: {exc}")


def _control_pictures():
    """The up arrow, the down arrow and the tick, in the theme in force.

    The arrows sit on a button or a well, so they are in ink; the tick sits
    on a box filled with ink, so it is in the ground's colour.
    """
    ink, ground = theme.TOKENS['ink'], theme.TOKENS['ground']
    return (themed_icon_url('spin-up.png', ink),
            themed_icon_url('spin-down.png', ink),
            themed_icon_url('check.png', ground))


#This function is called to apply the theme to the dialog
def apply_dark_theme(widget):
    """Paint a config dialog, and straighten its layout.

    The paint comes from the same tokens as the launcher window; the
    layout is ``tidy_dialog``, which is unchanged - a Qt stylesheet does
    no layout at all.

    The three image paths are absolute, worked out from this file rather
    than the working directory, because a stylesheet resolves ``url()``
    against the process's cwd and an app can be started from anywhere.
    """
    # A floor on the width only. There used to be one of 400 on the height
    # too, which made every short dialog too tall - see tidy_dialog.
    if isinstance(widget, Qt.QDialog):
        widget.setMinimumWidth(360)
    use_saved_theme()
    theme.load_fonts()
    widget.setStyleSheet(theme.dialog_qss(*_control_pictures()))
    match_title_bar(widget)
    tidy_dialog(widget)


def apply_flowgraph_theme(window):
    """Paint a running flowgraph window from the shared design tokens.

    Call it first thing in the window's ``__init__``, before any widget
    exists: it takes the place of GRC's ``qtgui.util.check_set_qss()``
    there. Two things depend on that timing.

    - **The plots' axis titles take the application font as it is when the
      plot is built**, and keep it. GNU Radio sets their size as a font of
      their own, copied from the application's, and Qt has no way to reach
      a Qwt title from Python afterwards. So the face is made the
      application's here, before any plot exists; set after, the axis
      titles stayed in the system face while everything round them was
      Barlow. It is set on the application rather than in the stylesheet
      because a stylesheet font beats ``setFont()``, and the receivers set
      fonts that mean something - monospace RadioText, a large lock status.
    - **The traces are recoloured when the window is first shown.** Every
      app sets its traces in GNU Radio's colours for a white canvas, black
      commonest, which on the well would be invisible. The stylesheet sets
      them again through the plots' own ``line_color`` properties, and a
      stylesheet's properties are applied when a widget is polished - on
      show, after the app's own ``set_line_color`` calls.

    It is paint only: every control and every plot is the app's own.
    """
    use_saved_theme()
    theme.load_fonts()
    app = Qt.QApplication.instance()
    if app is not None:
        font = Qt.QFont(theme.TOKENS['f_ui'])
        font.setPixelSize(theme.TOKENS['s_md'])
        app.setFont(font)
    # The window is a QWidget subclass, which paints a stylesheet
    # background only when asked to - without this the ground shows only
    # where the scroll area covers it.
    window.setAttribute(QtNs.WA_StyledBackground, True)
    window.setStyleSheet(theme.flowgraph_qss(*_control_pictures()))
    match_title_bar(window)
    window.installEventFilter(ClickToMove(window))


class ClickToMove(QObject):
    """A control in a flowgraph window moves only when it has been clicked.

    **The stylesheet made GNU Radio's sliders follow the mouse.** A Qt
    stylesheet with hover rules turns on mouse tracking, so a slider hears
    every movement of the pointer across it, button or not - measured, off
    with no stylesheet and on with the flowgraph one. GNU Radio's
    ``RangeWidget`` slider has its own ``mouseMoveEvent``, which jumps to
    wherever the pointer is without asking whether a button is down. So
    passing the mouse over a power slider set the power: 50 % to 89 % on
    the way across. Every window with a ``RangeWidget`` slider did it.

    **The wheel was the same thing by another route.** Most windows scroll
    on a laptop, and a scroll that passed over a slider, a spin box or a
    combo box moved that instead - power, frequency, deviation - and a spin
    box took focus from the wheel alone.

    So, on every slider, spin box and combo box in the window, a mouse
    move with no button down is dropped; a wheel turn over one that has
    not been clicked goes on to the scroll area behind it; and the wheel no
    longer gives focus. Clicking gives it, and dragging, typing and the
    wheel then work as they always did. The scroll bars are left alone:
    scrolling is what the wheel is for. Installed on the window by
    ``apply_flowgraph_theme``; it takes on the controls the first time the
    window is shown, when every one of them exists.
    """

    CONTROLS = (Qt.QAbstractSlider, Qt.QAbstractSpinBox, Qt.QComboBox)

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self._guarded = False

    def eventFilter(self, obj, event):
        kind = event.type()
        if obj is self._window:
            if kind == QEvent.Show and not self._guarded:
                self._guarded = True
                for control in self._window.findChildren(self.CONTROLS):
                    if isinstance(control, Qt.QScrollBar):
                        continue
                    if control.focusPolicy() == QtNs.WheelFocus:
                        control.setFocusPolicy(QtNs.StrongFocus)
                    control.installEventFilter(self)
            return False
        if kind == QEvent.MouseMove and not int(event.buttons()) \
                and isinstance(obj, Qt.QAbstractSlider):
            return True
        if kind == QEvent.Wheel and not obj.hasFocus():
            # Ignored and filtered: Qt then offers it to the parent, and
            # so on up to the scroll area.
            event.ignore()
            return True
        return False


def read_settings(settings_file=None):
    """What Settings holds - the media folder, the radio, and the Ettus
    USRP's IP address - with a default for anything never saved.

    It only reads. ``usrp_ip`` was ``ip_addresses``, a list, when the
    launcher was meant to drive several networked USRPs at once, which was
    never done; the first address in a file saved then is the one used.
    """
    if settings_file is None:
        settings_file = os.path.join("config", "window_settings.json")
    settings = {'media_directory': '', 'usrp_ip': '', 'radio_type': 'hackrf'}
    try:
        if os.path.exists(settings_file):
            with open(settings_file) as f:
                saved = json.load(f)
            settings.update(saved)
            if 'usrp_ip' not in saved and saved.get('ip_addresses'):
                settings['usrp_ip'] = str(saved['ip_addresses'][0]).strip()
    except Exception as e:
        print("Error reading settings:", e)
    settings.pop('ip_addresses', None)
    settings.pop('radio_mode', None)
    return settings


#: What each radio in Settings is called on the first line of an app's
#: dialog.
RADIO_NAMES = {
    'hackrf': "HackRF One (USB)",
    'usrp': "Ettus USRP (network)",
    'vsg': "Signal Hound VSG60 (USB)",
    'bb60': "Signal Hound BB60D (USB)",
}


def radio_label(radio_type, usrp_ip=''):
    """The first line of an app's dialog: which radio it will use.

    The radio, and the Ettus's address, are chosen in Settings, so the
    dialog only says what they are - and, for an Ettus with no address,
    where to put one. Its OK stays dimmed until there is one.
    """
    if radio_type == 'usrp':
        if usrp_ip:
            return f"Radio: Ettus USRP at {usrp_ip}"
        return "Radio: Ettus USRP - no IP address, add it in Settings"
    return "Radio: " + RADIO_NAMES.get(radio_type, RADIO_NAMES['hackrf'])


# --- Radio output power -----------------------------------------------------
#
# The power slider is a plain 0-100% control. Each radio has a different native
# gain unit and a different usable span, so the percentage is mapped onto that
# radio's own range: 0% is always its minimum and 100% always its maximum.
#
# This replaced an older scheme where the slider was labelled dBm but actually
# fed `(rfPwr+50)*(rfPwr>-50)` to HackRF/USRP - which capped both radios at
# 20 dB of gain and left the bottom 30 dB of the slider doing nothing at all.

RADIO_POWER_RANGE = {
    'hackrf': (0.0, 47.0),     # SoapySDR VGA gain, dB
    'usrp':   (0.0, 31.5),     # UHD gain, dB - replaced by the device's own
                               # range when it can be queried
    'vsg':    (-120.0, 10.0),  # Signal Hound calibrated output level, dBm
}


def resolve_power_range(radio_type, sink=None):
    """Return (min, max) for a radio, in that radio's own gain units.

    USRP gain range depends on the daughterboard, so query the device when a
    constructed sink is available and fall back to the table otherwise.
    """
    if radio_type == 'usrp' and sink is not None:
        try:
            rng = sink.get_gain_range()
            return (float(rng.start()), float(rng.stop()))
        except Exception:
            pass
    return RADIO_POWER_RANGE.get(radio_type, RADIO_POWER_RANGE['hackrf'])


def scale_power(percent, power_range):
    """Map a 0-100% slider position onto a radio's gain/level range."""
    low, high = power_range
    percent = min(max(float(percent), 0.0), 100.0)
    return low + (high - low) * percent / 100.0


def power_percent(value, default=50):
    """Sanitise a saved power setting, as an int.

    Configs written before the percentage change hold dBm-ish values such as
    -50, which would silently clamp to 0% (no output). Fall back to the default
    for anything outside 0-100.

    Returns an int because this feeds QSlider.setValue(), which rejects a float
    with a TypeError - and load_config() swallows exceptions, so a float here
    silently drops every setting restored after the power slider.
    """
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    if not 0.0 <= value <= 100.0:
        return default
    return int(round(value))


# Every spectrum plot opens on this vertical range, so a signal at a given
# level looks the same height in whichever app you open.
#
# Autoscale is on only for the baseband plots (freq_sink_f - the MPX views and
# the subcarrier's audio), and off for the RF ones (freq_sink_c). Autoscale fits
# the axis from the lowest bin to the highest, and the RF spectra of these
# synthesised signals have bins at numerical zero: fmAudioRecordedGenerator and
# amSineGenerator both stretched to about -380 dB, which left the carrier and
# the noise floor squeezed into the top third of the plot - the opposite of
# centring it. The baseband plots have audio or noise in every bin and fit
# well, fmRdsTransmitter offscreen and rdsReceiver live on 98.7 alike. RF plots
# therefore keep this measured fixed range.
#
# Top is 0 dB: that is digital full scale, and nothing can get above it. The
# strongest thing any of these flowgraphs can put on a plot is an unmodulated
# carrier - all of its power in one bin - and that measures about -10 dB. The
# old ceilings of +10 were showing 20 dB of space no signal could ever reach.
#
# Bottom is -170 dB because the FFT's own numerical noise floor sits near -160.
# The old floors of -120/-140 cut straight through it, so the skirts of a
# signal ended in a flat clipped band across the bottom of the plot instead of
# descending into the grass.
SPECTRUM_Y_AXIS = (-170, 0)
