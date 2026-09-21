"""The one place the launcher, the dialogs and the app windows get their
colours and type from.

The palette, the type scale and the faces live here, and every window
reads them - ``apply_launcher_theme``, ``apply_dark_theme`` and
``apply_flowgraph_theme`` in ``apps/utils.py`` through :func:`launcher_qss`,
:func:`dialog_qss` and :func:`flowgraph_qss`. So a dialog and the window
it opens read as one app, and a colour edited here moves in all of them.

**This module imports nothing but the standard library at module level**,
so ``scripts/test_theme.py`` can check every theme with no Qt and no
display; :func:`load_fonts` is the one function that needs Qt and it
imports inside itself.

Qt Style Sheets look like CSS but are not, and some of what the launcher
draws has no QSS equivalent at all - variables, cropping a picture to its
box, desaturating it, ``letter-spacing``, drop shadows and anything that
moves. Those are done in Python, in ``RFbenchToolkit.py``; everything that
*is* expressible lives here.
"""

import os

#: Where ``fonts/`` sits, relative to this file rather than to the working
#: directory - an app can be started from anywhere.
FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'fonts')

#: The type: the faces and the sizes. A theme may name faces of its own -
#: Reading Room and Walnut do - which are laid over these; the sizes are
#: the same in every theme. Sizes are in pixels, which is what Qt takes.
TYPE = {
    'f_num': 'Barlow Semi Condensed',
    'f_ui': 'Barlow',
    's_xs': 12, 's_sm': 13, 's_md': 15, 's_lg': 18, 's_xl': 24,
    # The weight the Qt stylesheets ask for the wordmark, the TRANSMIT line
    # and a plot's title. Qt 5 reads a stylesheet's font-weight divided by
    # 8, so 600 asks for Bold (75) - which Barlow has a real face for. A
    # face with no bold of its own is then thickened by FreeType instead:
    # measured, a third more ink on Archivo SemiBold and on Limelight. So
    # a theme whose faces stop short of bold asks 500, which is 62 to Qt
    # and matches the semibold, or Limelight's one weight, as drawn.
    'qss_bold': 600,
}

#: The palettes, in the order the disc in the header steps through them.
#: A theme is a palette, and may also name its own faces under ``type``.
#: The sizes, the radii and every rule are written once and read these,
#: so a theme can never move a control; the launcher measures its own size
#: from whichever faces are in force.
#:
#: Each colour keeps the same job in every theme - ``ink_2`` at least
#: 4.5:1 on a panel, ``ink_3`` at least 3:1, the status colours at least
#: 4.5:1 on the ground and the panel, because the receivers set their lock
#: line in them. ``scripts/test_theme.py`` holds each theme to that.
THEMES = {
    # The default, and the dark one: blue-black slate, with an orange that
    # means on air.
    'slate': {
        'scheme': 'dark',         # the title bar, on Windows
        'ground': '#10151a',      # the window behind everything
        'panel': '#1a2228',       # a tile, a card, a dialog
        'panel_2': '#212b32',     # a tile under the pointer, a plain button
        'well': '#0c1013',        # anything typed into
        'rule': '#2e3a43',        # a border that should be seen
        'rule_soft': '#222c33',   # one that should barely be
        'shade': '#000000',       # a shadow
        'heading': '#93a3ad',     # a bank's name - Signal generators, Audio
        'ink': '#e6ecef',         # body text
        'ink_0': '#ffffff',       # something already in ink, under the pointer
        'ink_2': '#93a3ad',       # labels, captions
        'ink_3': '#5d6d78',       # the quietest thing still meant to be read
        'tag': '#5d6d78',         # a tile's TRANSMIT or RECEIVE line
        'live': '#ff9b21',        # on air
        'warn': '#e8b04b',        # a banner that wants reading
        'good': '#6fcf97',        # a receiver that has locked
        'bad': '#f0716a',         # one that has lost it
        'trace': '#cfe0e8',       # a plotted signal, on the well
        # The tiles' shadows, their lift under the pointer and the pulse
        # down each bank's line, as Reading Room has them - see there. A
        # shadow on a near-black ground has to be far darker to be seen at
        # all, so the resting one is two to three times Reading Room's. In
        # the dark a lifted card reads as lit rather than as shadowed: it
        # keeps a dark contact shadow close under it, and gains a light of
        # the trace's pale ice, with no drop, so it is light and not a
        # white shadow - two halos, a tight bright ring round the edge and
        # a wide soft one, as Walnut's; see there. The pulse is the same
        # ice.
        'shadow': ((1, 2, 0.45), (4, 12, 0.40)),
        'shadow_hover': ((3, 6, 0.55), (0, 12, 0.85, 'trace'),
                         (0, 34, 0.55, 'trace')),
        'lift': 3,
        'pulse': '#cfe0e8',
    },
    # The light one: paper, from voice-summary. The panels sit lighter than
    # the ground, as they do in Slate - a sheet of paper on a desk - and the
    # ink is iron-gall blue-black rather than black.
    'reading-room': {
        'scheme': 'light',
        'ground': '#e6e5df',
        'panel': '#f7f6f2',
        'panel_2': '#edebe4',
        'well': '#fbfaf7',
        'rule': '#c8c5b9',
        'rule_soft': '#dcd9cf',
        'shade': '#1c2229',
        # The bank names in full ink: in ink_2 they were too faint to head
        # anything on paper.
        'heading': '#1c2229',
        'ink': '#1c2229',
        'ink_0': '#05080b',
        'ink_2': '#4f5966',
        'ink_3': '#7a818b',
        'tag': '#7a818b',
        'live': '#a13f0c',
        'warn': '#765a00',
        'good': '#2a6d32',
        'bad': '#a8261f',
        'trace': '#12657a',
        # A tile is a card on a desk, and a card on a desk has a shadow:
        # (drop, blur, opacity) for each layer, in ``shade``, the ink's
        # colour here, a contact shadow close under it and a soft one
        # further out. A fourth item names another colour for that layer -
        # Slate and Walnut light their lifted tiles that way.
        'shadow': ((1, 2, 0.12), (4, 12, 0.14)),
        # OK under the pointer inverts: it takes Cancel's look - the light
        # button, dark text - and pressed goes back to black. Stepping to
        # ink_0, as the dark themes do, was a darker near-black on a
        # near-black button, and the hover all but vanished. (A turn to the
        # pulse's blue came first, and gave way to this.)
        'ok_invert': True,
        # Under the pointer the card lifts: it rises ``lift`` pixels, and
        # its shadow darkens tight round its edge and drops further below
        # it, wider and darker. The first, (2, 4, .16) and (10, 26, .26)
        # with no rise, was found too faint, and so was the next, (3, 6,
        # .18) and (16, 34, .32) - on 2026-09-19, with the dark themes'
        # light, which became a tight ring and a wide one the same day.
        'shadow_hover': ((3, 6, 0.45), (2, 12, 0.55), (20, 40, 0.70)),
        'lift': 3,
        # A pulse of ultramarine runs along the line beside each bank's name,
        # one row after another, like a signal going down a line - the blue
        # of voice-summary's second voice on paper. It was the trace's teal
        # first, and was asked to be blue.
        'pulse': '#1a4fa0',
        # voice-summary's own faces for it: a modern library. Archivo, a
        # grotesque, for the wordmark, the TRANSMIT line and the headings;
        # Source Serif 4 for everything else, in its SmText cut, the one
        # drawn for small sizes. Slate is the modern sans already, and
        # Walnut the antique, so this is the third voice rather than a
        # second modern one.
        'type': {'f_num': 'Archivo', 'f_ui': 'Source Serif 4 SmText',
                 'qss_bold': 500},
    },
    # Brown, with tan the one highlight: a wooden radio's walnut cabinet
    # and the tan face of its dial. Tan is the tile outlines, the TRANSMIT
    # line, the headings and labels, a slider's fill, the trace and the
    # disc, and it sits well clear of the wood - ink_3 at 6:1 on a panel,
    # rule at 3:1, where Slate's are 3:1 and 1.4:1 - so it reads as trim
    # rather than as one more shade of brown. Its first tans were 4.5:1
    # and 2:1, and were found too close to the walnut. The rule is kept at
    # 3:1 and no lighter because selected text sits on it: cream on it is
    # 4.2:1 as it is. On air stays the orange of a valve's glow,
    # the one thing in the window that is not brown or tan. voice-summary's
    # Tape Room was a brown theme too, with amber and red for highlights,
    # and was found flat: the browns here are lighter and warmer than its
    # near-black, and there is one accent rather than three.
    'walnut': {
        'scheme': 'dark',
        'ground': '#231a13',
        'panel': '#33261c',
        'panel_2': '#3d2e22',
        'well': '#1a130e',
        'rule': '#8c6c48',
        'rule_soft': '#4d3a2b',
        'shade': '#0a0604',
        'heading': '#e8c896',
        'ink': '#f5ede0',
        'ink_0': '#fffaf2',
        'ink_2': '#e8c896',
        'ink_3': '#c4a070',
        # The TRANSMIT line is supplementary, but in ink_3's bright tan and
        # Limelight's heavy Deco it caught the eye before the app's name
        # did. Dimmed to 3.5:1 on a panel, near Slate's, below the name's
        # 12.6:1.
        'tag': '#927656',
        'live': '#f0874a',
        'warn': '#e6bd5c',
        'good': '#a8c97f',
        'bad': '#e8806c',
        'trace': '#f0cf98',
        # Shadows and lift as Slate's, in a near-black brown rather than
        # black, and a lifted card lit by the tan - lamplight. The pulse is
        # the tan too. The light was one halo, (0, 22, .32), and was found
        # too subtle (2026-09-19): the card covers the brightest of a blur,
        # so little of it showed past the edge. Now a tight bright ring
        # round the edge and a wide soft one beyond it, and Slate the same.
        'shadow': ((1, 2, 0.45), (4, 12, 0.40)),
        'shadow_hover': ((3, 6, 0.55), (0, 12, 0.85, 'trace'),
                         (0, 34, 0.55, 'trace')),
        'lift': 3,
        'pulse': '#f0cf98',
        # Old type for an old radio. The wordmark, the TRANSMIT line and
        # the headings are in Limelight, the Art Deco of a 1930s nameplate;
        # everything read at length is in Libre Caslon Text, a Caslon drawn
        # for screens, which reads at 13 px. Old-style book faces with
        # old-style figures - Fanwood, IM Fell - were tried and looked
        # older still, but their numerals drop below the line, and in an
        # app that is mostly frequencies that is the wrong kind of old.
        'type': {'f_num': 'Limelight', 'f_ui': 'Libre Caslon Text',
                 'qss_bold': 500},
    },
}

#: The colours every theme has to define. ``scheme`` is not one: it is a
#: word, ``dark`` or ``light``, not a colour.
PALETTE = ('ground', 'panel', 'panel_2', 'well', 'rule', 'rule_soft',
           'shade', 'heading', 'ink', 'ink_0', 'ink_2', 'ink_3', 'tag', 'live',
           'warn', 'good', 'bad', 'trace')

#: Things a theme may have that are not colours, which the launcher draws
#: from: ``shadow``, the layers of a tile's drop shadow, in ``shade``;
#: ``shadow_hover`` and ``lift``, the deeper one under the pointer and how
#: many pixels the tile rises off it; ``pulse``, the colour of the pulse
#: that runs along each bank's line; and ``ok_invert`` - see
#: :func:`ok_hover`. All three themes have the first four; a theme without
#: one draws none.
EXTRAS = ('shadow', 'shadow_hover', 'lift', 'pulse', 'ok_invert')

#: What the disc's tooltip calls each one.
NAMES = {'slate': 'Slate', 'reading-room': 'Reading Room', 'walnut': 'Walnut'}

DEFAULT = 'slate'

#: The pulse down each bank's line, in seconds. The heading charges - a
#: glow of the pulse's colour gathering round its name, faster as it goes -
#: for ``charge`` (0.7 s at first, found too short), then fires: the pulse
#: leaves the end of the name and crosses the line in ``sweep``, while the
#: name's glow dies away over ``decay``. The chevron at the far end of the
#: line catches it: it lights over ``catch`` as the pulse's bright head
#: reaches it, and dies away over ``land``. Each row ``stagger`` after the
#: one above, so it runs down the window as well as along it, every
#: ``period``. ``length`` is the pulse's, head and tail, in pixels.
PULSE = {'charge': 1.2, 'sweep': 1.6, 'decay': 0.35, 'catch': 0.08,
         'land': 0.5, 'stagger': 0.5, 'period': 4.5, 'length': 160}

#: The tokens of the theme in force in this process: one palette and the
#: type. It is one dict, changed in place by :func:`use`, so a module that
#: did ``from apps.theme import TOKENS`` still reads the theme in force -
#: **but only when it reads it**. A colour copied out of it at import time,
#: into a class attribute say, stays whatever theme was in force then.
TOKENS = {}

_current = None


def valid(name):
    """``name`` if it is a theme, else the default - a settings file may
    hold anything, or nothing."""
    return name if name in THEMES else DEFAULT


def ok_hover(chosen):
    """OK under the pointer, as (background, text, border).

    It steps to ``ink_0``, a brighter white on the dark themes' light
    button, unless the theme has ``ok_invert``: then it takes the look of
    a plain button - Cancel beside it - and pressed goes back to its own.
    """
    if chosen.get('ok_invert'):
        return chosen['panel_2'], chosen['ink'], chosen['rule']
    return chosen['ink_0'], chosen['ground'], chosen['ink_0']


def use(name):
    """Make ``name`` the theme in force in this process, and return it."""
    global _current
    _current = valid(name)
    chosen = THEMES[_current]
    TOKENS.clear()
    TOKENS.update({k: v for k, v in chosen.items() if k != 'type'})
    TOKENS.update(TYPE)
    TOKENS.update(chosen.get('type', {}))
    TOKENS['ok_hover'], TOKENS['ok_hover_ink'], TOKENS['ok_hover_edge'] = \
        ok_hover(chosen)
    return _current


def current():
    """The name of the theme in force."""
    return _current


def after(name):
    """The theme the disc goes to next."""
    order = list(THEMES)
    return order[(order.index(valid(name)) + 1) % len(order)]


use(DEFAULT)

#: What to fall back to before the vendored faces are loaded, or if they
#: cannot be.
FALLBACK = '"Helvetica Neue", Arial, sans-serif'


#: Registered families, once. ``addApplicationFont`` on the same file
#: twice hands back a second handle and registers the family again, which
#: is wasteful rather than wrong - but every apply_*_theme calls this, and
#: every dialog calls one of them.
_loaded = None


def load_fonts(directory=None, force=False):
    """Register the vendored faces with Qt, and say which arrived.

    No system install on any machine: ``fonts/`` travels with the repo,
    which is what makes Linux and Windows render the same. Returns the
    families actually registered, so a caller can notice rather than
    silently render in something else.
    """
    global _loaded
    if _loaded is not None and not force and directory is None:
        return _loaded
    from PyQt5.QtGui import QFontDatabase  # here, not at module level
    directory = directory or FONT_DIR
    families = set()
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        names = []
    for name in names:
        if not name.lower().endswith(('.ttf', '.otf')):
            continue
        handle = QFontDatabase.addApplicationFont(os.path.join(directory, name))
        if handle >= 0:
            families.update(QFontDatabase.applicationFontFamilies(handle))
    if directory == FONT_DIR:
        _loaded = families
    return families


# --- The launcher window ----------------------------------------------------

_LAUNCHER_QSS = """
QMainWindow, QWidget { background: %(ground)s; color: %(ink)s;
    font-family: "%(f_ui)s", %(fallback)s; font-size: %(s_md)spx; }
QToolTip { background: %(panel_2)s; color: %(ink)s;
    border: 1px solid %(rule)s; padding: 5px 7px; }

/* The header rail. A child widget paints its own background over the
   border-bottom - the wordmark did, and so would the centred column its
   contents sit in - so everything inside the rail is transparent and the
   rule shows through across the full width. */
#rail { background: %(ground)s; border-bottom: 1px solid %(rule)s; }
#rail QWidget { background: transparent; }
#mark { font-family: "%(f_num)s", %(fallback)s; font-weight: %(qss_bold)s;
    font-size: %(s_lg)spx; color: %(ink)s; }
#radio-tag { font-size: %(s_xs)spx; color: %(ink_2)s; padding: 4px 9px;
    border: 1px solid %(rule)s; border-radius: 2px; background: transparent; }
#theme-label { font-size: %(s_sm)spx; color: %(ink_2)s; }
#gear { border: none; border-radius: 2px; background: transparent;
    padding: 0; min-width: 0; }
#gear:hover { background: %(panel)s; }

/* A bank heading. The hairline running off it is a PulseLine in
   RFbenchToolkit.py, which paints itself - a pulse runs along it on a
   theme that has one - and the chevron at its end is painted by the
   heading, a BankHeader, which is the button that collapses the bank. */
#bank-name { color: %(heading)s; font-size: %(s_sm)spx; background: transparent; }

/* A bank's tiles sit in a body that wipes open and shut, and a grid in
   that. Both are plain QWidgets, which the rule at the top would paint
   in the ground - over the tiles' shadows, which the column underneath
   draws. */
#bank-body, #bank-grid { background: transparent; }

/* A tile. Dimming is painted rather than set here: QSS has no opacity
   property, and a QGraphicsOpacityEffect on the tile would have to nest
   inside the one the caption already carries. */
#tile { background: %(panel)s; border: 1px solid %(rule)s;
    border-radius: 2px; padding: 0; text-align: left; }
#tile:hover { background: %(panel_2)s; border-color: %(ink_3)s; }
#tile:pressed { background: %(well)s; }
#tile:disabled { background: %(panel)s; border-color: %(rule)s; }
#tile QLabel { background: transparent; }
#dir { font-family: "%(f_num)s", %(fallback)s; font-weight: %(qss_bold)s;
    font-size: %(s_xs)spx; color: %(tag)s; }
#name { font-size: %(s_sm)spx; color: %(ink)s; }
/* A tile the radio cannot run. The picture is dimmed by the painter -
   see FlipTile._draw - and the caption here, because the two labels
   already carry an opacity effect each for the flip and effects do not
   nest predictably. */
#dir:disabled { color: %(rule)s; }
#name:disabled { color: %(ink_3)s; }
#flip { background: %(panel)s; border: 1px solid %(ink_3)s;
    border-radius: 2px; color: %(ink)s; font-size: %(s_sm)spx;
    padding: 0; min-width: 0; }
#flip:hover { background: %(well)s; }

QScrollArea { background: %(ground)s; border: none; }
QScrollBar:vertical { background: %(ground)s; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: %(rule)s; border-radius: 2px;
    min-height: 30px; }
QScrollBar::handle:vertical:hover { background: %(ink_3)s; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: %(ground)s; }

QMessageBox { background: %(panel)s; }
QMessageBox QLabel { color: %(ink)s; background: transparent; }
QMessageBox QPushButton { background: %(panel_2)s; color: %(ink)s;
    border: 1px solid %(rule)s; border-radius: 2px;
    padding: 7px 14px; min-width: 80px; }
QMessageBox QPushButton:hover { border-color: %(ink_3)s; }
"""


def launcher_qss():
    """The launcher window's stylesheet."""
    return _LAUNCHER_QSS % dict(TOKENS, fallback=FALLBACK)


# --- The config dialogs -----------------------------------------------------

# A panel card, a well for anything typed into, one primary button.
# tidy_dialog still does the layout - this is paint only.
_DIALOG_BASE_QSS = """
QDialog, QWidget { background: %(panel)s; color: %(ink)s;
    font-family: "%(f_ui)s", %(fallback)s; font-size: %(s_md)spx; }
QToolTip { background: %(panel_2)s; color: %(ink)s;
    border: 1px solid %(rule)s; padding: 5px 7px; }
QLabel { background: transparent; color: %(ink_2)s; font-size: %(s_sm)spx; }
QGroupBox { border: 1px solid %(rule_soft)s; border-radius: 2px;
    margin-top: 10px; padding-top: 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 9px; padding: 0 4px;
    color: %(ink_2)s; font-size: %(s_sm)spx; }
"""

# Buttons, anything typed into, sliders and ticks - the same in a config
# dialog and in the flowgraph window it opens, so the two read as one app.
_CONTROLS_QSS = """
QPushButton { background: %(panel_2)s; color: %(ink)s;
    border: 1px solid %(rule)s; border-radius: 2px;
    padding: 8px 16px; min-width: 80px; font-size: %(s_sm)spx; }
QPushButton:hover { border-color: %(ink_3)s; }
QPushButton:pressed { background: %(well)s; }
/* OK, the one primary button. */
QPushButton:default { background: %(ink)s; color: %(ground)s;
    border-color: %(ink)s; }
QPushButton:default:hover { background: %(ok_hover)s; color: %(ok_hover_ink)s;
    border-color: %(ok_hover_edge)s; }
/* Pressed, it drops back to rest: without this the rule above for any
   pressed button lost to :default, and OK gave no sign of being clicked. */
QPushButton:default:pressed { background: %(ink)s; color: %(ground)s;
    border-color: %(ink)s; }
QPushButton:disabled { color: %(ink_3)s; border-color: %(rule_soft)s;
    background: %(panel)s; }

QLineEdit, QAbstractSpinBox, QComboBox { background: %(well)s; color: %(ink)s;
    border: 1px solid %(rule)s; border-radius: 2px; padding: 7px 9px;
    selection-background-color: %(rule)s; selection-color: %(ink)s; }
QLineEdit:hover, QAbstractSpinBox:hover, QComboBox:hover,
QLineEdit:focus, QAbstractSpinBox:focus, QComboBox:focus {
    border-color: %(ink_3)s; }
QLineEdit:disabled, QAbstractSpinBox:disabled, QComboBox:disabled {
    color: %(ink_3)s; border-color: %(rule_soft)s; }
QComboBox QAbstractItemView { background: %(well)s; color: %(ink)s;
    border: 1px solid %(rule)s; selection-background-color: %(panel_2)s;
    selection-color: %(ink)s; }

/* A spin box or combo that a stylesheet touches at all stops drawing its
   own arrows - they come out as empty rectangles - and Qt's CSS subset
   will not draw a triangle out of borders either. So they are images. */
QComboBox::drop-down { subcontrol-origin: padding;
    subcontrol-position: center right; width: 22px; border: none;
    background: transparent; }
QComboBox::down-arrow { image: url(%(down)s); width: 9px; height: 5px; }
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {
    subcontrol-origin: border; background: %(panel_2)s; border: none;
    width: 17px; }
QAbstractSpinBox::up-button { subcontrol-position: top right;
    margin: 1px 1px 0 0; border-top-right-radius: 1px; }
QAbstractSpinBox::down-button { subcontrol-position: bottom right;
    margin: 0 1px 1px 0; border-bottom-right-radius: 1px; }
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {
    background: %(rule)s; }
QAbstractSpinBox::up-arrow { image: url(%(up)s); width: 9px; height: 5px; }
QAbstractSpinBox::down-arrow { image: url(%(down)s); width: 9px; height: 5px; }

QSlider { background: transparent; }
QSlider::groove:horizontal { background: %(well)s;
    border: 1px solid %(rule)s; height: 4px; border-radius: 2px; }
QSlider::sub-page:horizontal { background: %(ink_3)s; border-radius: 2px; }
QSlider::handle:horizontal { background: %(ink)s; border: none; width: 12px;
    margin: -5px 0; border-radius: 2px; }
QSlider::handle:horizontal:hover { background: %(ink_0)s; }
QSlider::handle:horizontal:disabled { background: %(ink_3)s; }

QCheckBox { background: transparent; color: %(ink_2)s; spacing: 8px; }
QCheckBox::indicator { width: 14px; height: 14px; border-radius: 2px;
    border: 1px solid %(rule)s; background: %(well)s; }
QCheckBox::indicator:hover { border-color: %(ink_3)s; }
QCheckBox::indicator:checked { background: %(ink)s; border-color: %(ink)s;
    image: url(%(tick)s); }
QCheckBox:disabled { color: %(ink_3)s; }
"""

_DIALOG_QSS = _DIALOG_BASE_QSS + _CONTROLS_QSS


def dialog_qss(up, down, tick):
    """A config dialog's stylesheet.

    The three paths are the arrow and tick images, absolute, because a
    stylesheet resolves ``url()`` against the process's working directory
    and an app can be started from anywhere.
    """
    return _DIALOG_QSS % dict(TOKENS, fallback=FALLBACK,
                              up=up, down=down, tick=tick)


# --- The flowgraph windows --------------------------------------------------

# The window on the ground, each group of controls a panel card, each
# plot a well with a rule round it, traces in the trace colour.
#
# **No font-family or font-size on QWidget, QLabel or the plots' own
# frames**, unlike the dialog. A stylesheet font beats setFont(), and the
# receivers set fonts that mean something - the RadioText and the program
# list in monospace, so a gap or a stray character shows where it is, and
# the lock status large. The face comes from the application font instead
# (see apply_flowgraph_theme), which a widget's own setFont() still
# overrides. Rules below that do set a face are for widgets no app sets
# one on.
_FLOWGRAPH_BASE_QSS = """
QWidget { background: %(ground)s; color: %(ink)s; }
QToolTip { background: %(panel_2)s; color: %(ink)s;
    border: 1px solid %(rule)s; padding: 5px 7px; }
QLabel, QRadioButton, QCheckBox, QSlider, QToolBar { background: transparent; }

/* GRC puts every window inside a scroll area. */
QScrollArea { background: %(ground)s; border: none; }
QScrollBar:vertical { background: %(ground)s; width: 10px; margin: 0; }
QScrollBar:horizontal { background: %(ground)s; height: 10px; margin: 0; }
QScrollBar::handle { background: %(rule)s; border-radius: 2px; }
QScrollBar::handle:vertical { min-height: 30px; }
QScrollBar::handle:horizontal { min-width: 30px; }
QScrollBar::handle:hover { background: %(ink_3)s; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: %(ground)s; }

/* A group of controls, or a receiver's readout, is a panel card. What it
   holds sits on the card rather than painting the ground over it. */
QGroupBox { background: %(panel)s; border: 1px solid %(rule)s;
    border-radius: 2px; margin-top: 22px; padding: 6px 8px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left;
    left: 1px; top: 0; padding: 0 0 5px 0; background: transparent;
    color: %(ink_2)s; font-family: "%(f_ui)s", %(fallback)s;
    font-size: %(s_sm)spx; }
QGroupBox > QWidget { background: transparent; }

/* A control's name, from GNU Radio's RangeWidget and GRC's labelled tool
   bars, is quieter than the value beside it. */
RangeWidget QLabel { color: %(ink_2)s; }
QToolBar { border: none; spacing: 4px; padding: 0; }
QToolBar QLabel { color: %(ink_2)s; }
QToolBar::handle { width: 0; height: 0; image: none; }

/* GRC's choosers are radio buttons in a group box. */
QRadioButton { color: %(ink_2)s; spacing: 8px; }
QRadioButton:checked { color: %(ink)s; }
QRadioButton:disabled { color: %(ink_3)s; }
QRadioButton::indicator { width: 14px; height: 14px; border-radius: 8px;
    border: 1px solid %(rule)s; background: %(well)s; }
QRadioButton::indicator:hover { border-color: %(ink_3)s; }
QRadioButton::indicator:checked { border-color: %(ink)s;
    background: qradialgradient(cx: 0.5, cy: 0.5, radius: 0.5,
        fx: 0.5, fy: 0.5, stop: 0 %(ink)s, stop: 0.42 %(ink)s,
        stop: 0.52 %(well)s, stop: 1 %(well)s); }

/* A plot's right-click menus, and the little dialogs they open. */
QMenu { background: %(panel_2)s; color: %(ink)s; border: 1px solid %(rule)s;
    padding: 4px 0; }
QMenu::item { padding: 5px 18px; background: transparent; }
QMenu::item:selected { background: %(rule)s; }
QMenu::separator { height: 1px; background: %(rule_soft)s; margin: 4px 0; }
QDialog { background: %(panel)s; }

/* A plot is a well with a rule round it. */
DisplayPlot { background: %(well)s; border: 1px solid %(rule)s;
    border-radius: 2px;
    qproperty-palette_color: %(well)s;
    qproperty-zoomer_color: %(ink)s;
    qproperty-axes_label_font_size: 10;
    qproperty-line_color1: %(trace)s;
    qproperty-line_color2: %(ink_3)s;
    qproperty-line_color3: %(live)s;
    qproperty-line_color4: %(good)s;
    qproperty-line_color5: %(warn)s;
    qproperty-line_color6: %(bad)s;
    qproperty-line_color7: %(ink_2)s;
    qproperty-line_color8: %(rule)s;
    qproperty-line_color9: %(ink)s; }
TimeDomainDisplayPlot { qproperty-tag_text_color: %(ink)s;
    qproperty-tag_background_color: %(panel_2)s; }
/* Not marker_peak_amplitude_color: setting it at all segfaults GNU Radio
   3.10.12's frequency plot, with no Python frame to say why. */
FrequencyDisplayPlot { qproperty-max_fft_color: %(ink_2)s;
    qproperty-min_fft_color: %(ink_3)s;
    qproperty-marker_lower_intensity_color: %(ink_3)s;
    qproperty-marker_upper_intensity_color: %(warn)s;
    qproperty-marker_noise_floor_amplitude_color: %(ink_3)s;
    qproperty-marker_CF_color: %(rule)s; }
QwtPlotCanvas { background: %(well)s; border: 1px solid %(rule)s;
    border-radius: 0; }
DisplayPlot QWidget { background: transparent; }
DisplayPlot QwtPlotCanvas { background: %(well)s; }
QwtTextLabel#QwtPlotTitle { color: %(ink)s; padding: 6px 0 2px 0;
    font-family: "%(f_ui)s", %(fallback)s; font-size: %(s_sm)spx;
    font-weight: %(qss_bold)s; }
QwtScaleWidget { color: %(ink_3)s; font-family: "%(f_ui)s", %(fallback)s;
    font-size: %(s_xs)spx; }
QwtLegendLabel { color: %(ink_2)s; font-size: %(s_sm)spx; }
"""

_FLOWGRAPH_QSS = _FLOWGRAPH_BASE_QSS + _CONTROLS_QSS


def flowgraph_qss(up, down, tick):
    """A running flowgraph window's stylesheet. The paths are as for
    :func:`dialog_qss`."""
    return _FLOWGRAPH_QSS % dict(TOKENS, fallback=FALLBACK,
                                 up=up, down=down, tick=tick)


# --- The faces --------------------------------------------------------------

#: The faces that ship in ``fonts/``, as (family, file). :func:`load_fonts`
#: registers every file there; this says which family each one is, so
#: ``scripts/test_theme.py`` can check a theme names only faces that ship,
#: each with its licence.
#:
#: Every face is a static file, one weight to a file: Qt 5 cannot choose a
#: weight from a variable one. The text faces run to a real bold, because
#: the receivers set their status and captions bold in whatever the
#: application font is. Limelight has one weight, and Qt would fake a bold
#: from it - see ``qss_bold`` in :data:`TYPE`.
FACES = [
    ('Barlow', 'Barlow-Regular.ttf'),
    ('Barlow', 'Barlow-Medium.ttf'),
    ('Barlow', 'Barlow-SemiBold.ttf'),
    ('Barlow Semi Condensed', 'BarlowSemiCondensed-Medium.ttf'),
    ('Barlow Semi Condensed', 'BarlowSemiCondensed-SemiBold.ttf'),
    ('Barlow Semi Condensed', 'BarlowSemiCondensed-Bold.ttf'),
    ('Archivo', 'Archivo-SemiBold.ttf'),
    ('Source Serif 4 SmText', 'SourceSerif4SmText-Regular.ttf'),
    ('Source Serif 4 SmText', 'SourceSerif4SmText-Semibold.ttf'),
    ('Source Serif 4 SmText', 'SourceSerif4SmText-Bold.ttf'),
    ('Limelight', 'Limelight-Regular.ttf'),
    ('Libre Caslon Text', 'LibreCaslonText-Regular.ttf'),
    ('Libre Caslon Text', 'LibreCaslonText-SemiBold.ttf'),
    ('Libre Caslon Text', 'LibreCaslonText-Bold.ttf'),
]

#: The SIL OFL each family is under, with its own copyright line. The
#: licence has to travel with the fonts, so ``fonts/`` goes everywhere
#: whole.
LICENCES = {
    'Barlow': 'OFL.txt',
    'Barlow Semi Condensed': 'OFL.txt',
    'Archivo': 'OFL-Archivo.txt',
    'Source Serif 4 SmText': 'OFL-SourceSerif.txt',
    'Limelight': 'OFL-Limelight.txt',
    'Libre Caslon Text': 'OFL-LibreCaslon.txt',
}


#: The settings glyph: three faders, which says "settings" without a
#: photograph of a cog. It is inline SVG rather than a file because QtSvg
#: has no ``currentColor``, so the ink has to be put in before it is
#: rendered.
_GEAR = """<svg xmlns="http://www.w3.org/2000/svg" width="17" height="17"
 viewBox="0 0 17 17" fill="none">
<path d="M2 4h5M10 4h5M2 8.5h9M14 8.5h1M2 13h3M8 13h7"
 stroke="%(ink)s" stroke-width="1.4" stroke-linecap="round"/>
<circle cx="8.5" cy="4" r="1.7" stroke="%(ink)s" stroke-width="1.4"/>
<circle cx="12.5" cy="8.5" r="1.7" stroke="%(ink)s" stroke-width="1.4"/>
<circle cx="6.5" cy="13" r="1.7" stroke="%(ink)s" stroke-width="1.4"/>
</svg>"""


def gear_svg(colour=None):
    return _GEAR % {'ink': colour or TOKENS['ink_2']}
