#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Standard library imports
import sys
import os
import json
import math
import importlib.util

# Third party imports
from PyQt5.QtWidgets import ( # type: ignore
    QAbstractButton,
    QMainWindow,
    QWidget,
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QApplication,
    QFrame,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QDialog,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QMessageBox,
    QStyle,
    QStyleOption
)
from PyQt5.QtCore import (Qt, QEvent, QSize, QPoint, QPointF,  # type: ignore
                          QRectF, QElapsedTimer, QTimer,
                          QPropertyAnimation, QVariantAnimation, QEasingCurve,
                          pyqtProperty)
from PyQt5.QtGui import (QColor, QIcon, QImage, QPixmap, QFont,  # type: ignore
                         QFontMetrics, QLinearGradient, QRadialGradient,
                         QPainter,
                         QPainterPath, QPen)
from PyQt5.QtSvg import QSvgRenderer # type: ignore

# PIL and numpy prepare the tile pictures - see cover_crop and picture_pixmap.
from PIL import Image, ImageDraw, ImageFilter # type: ignore
import numpy as np # type: ignore

# Local imports 
from apps import theme
from apps.utils import (apply_launcher_theme, apply_dark_theme,
                        centre_on, DialogGeometryTracker,
                        geometry_is_reachable, maximize_when_shown,
                        flowgraph_settings, normal_geometry, read_settings,
                        restore_window_geometry, save_flowgraph_settings,
                        save_window_geometry, update_app_config,
                        use_saved_theme)
from apps.settings_dialog import SettingsDialog

# Which way each radio goes. Two of the four are one-way instruments, and
# the grid follows: choose the VSG60 and every tile turns to its
# transmitting side, choose the BB60D and they all turn to receive. A radio
# that does both leaves the tiles free to flip.
RADIO_DIRECTIONS = {
    'hackrf': {'tx', 'rx'},
    'usrp': {'tx', 'rx'},
    'vsg': {'tx'},          # Signal Hound VSG60 - a signal generator
    'bb60': {'rx'},         # Signal Hound BB60D - a spectrum analyser
}
RADIO_NAMES = {
    'hackrf': 'The HackRF One', 'usrp': 'The Ettus USRP',
    'vsg': 'The Signal Hound VSG60', 'bb60': 'The Signal Hound BB60D',
}
DIRECTION_WORDS = {'tx': 'transmit', 'rx': 'receive'}


# Every tile in the grid, declared rather than built by hand, as
# (row, column, [face, ...]) where a face is
# (label, module, icon, direction) and direction is 'tx' or 'rx'.
#
# A tile with more than one face is a *flip* tile: a badge in its corner
# turns it over, and the icon and the label both change with it. That is
# how the two ends of one standard share a square - the ATSC transmitter
# and receiver, the FM + RDS transmitter and the RDS receiver - instead of
# sitting apart as though they were unrelated apps.
#
# The direction is not decoration. It is what lets the grid arrange itself
# around whichever radio is selected, and what stops an app being launched
# against a radio that cannot possibly run it - which used to end in "no
# HackRF found", sending people to check a cable that was not the problem.
#
# Row 0 holds the title and the settings gear. Row 1 is the plain signal
# generators, row 2 audio, row 3 video. The gap at (2, 4) is where the RDS
# receiver used to sit before it became the back of the transmitter's tile.
#
# scripts/test_launcher_gui.py reads this table to find what to click, so
# keep it literal: it is parsed with ast, not imported.
APP_TILES = [
    (1, 0, [("AM Sine Generator", "amSineGenerator", "amSine.jpg", "tx")]),
    (1, 1, [("ASK Generator", "askGenerator", "ask.jpg", "tx")]),
    (1, 2, [("FSK Signal Generator", "fskGenerator", "fsk.jpg", "tx")]),
    (1, 3, [("PSK Signal Generator", "pskGenerator", "psk.jpg", "tx")]),
    (1, 4, [("PPM-OOK Generator", "ppmookAudioXmitter", "ppm-ook.png", "tx")]),
    (2, 0, [("AM Audio Generator", "amAudioInternalGeneratorLive",
             "amAudio.jpg", "tx")]),
    (2, 1, [("FM Audio Generator", "fmAudioRecordedGenerator",
             "fmAudio.png", "tx")]),
    (2, 2, [("FM Subcarrier Generator", "subcarrierRecordedAudio",
             "fmSubcarrier.jpg", "tx")]),
    (2, 3, [("FM + RDS Transmitter", "fmRdsTransmitter", "fmrds.jpg", "tx"),
            ("FM + RDS Receiver", "rdsReceiver", "rds.png", "rx")]),
    # atscXmit.jpg is the radiating one and atsc.jpg is a television set,
    # so they land this way round rather than the way the filenames read.
    (3, 0, [("ATSC Video Transmitter", "atscXmitter", "atscXmit.jpg", "tx"),
            ("ATSC Video Receiver", "atscReceiver", "atsc.jpg", "rx")]),
    (3, 1, [("NTSC Video Transmitter", "ntscAnalogVideoRecorded",
             "ntsc.jpg", "tx"),
            ("NTSC Video Receiver", "ntscReceiver", "ntscRx.jpg", "rx")]),
    # FM video replaced AM video, which matched nothing a real transmitter
    # sends; this one is what an analog FPV drone puts out on 5.8 GHz.
    (3, 2, [("FM Video Transmitter", "fmVideoXmitter", "fmVideo.jpg", "tx"),
            ("FM Video Receiver", "fmVideoReceiver", "fmVideoRx.jpg", "rx")]),
    # The icon is a real Nexus-TH frame drawn as its envelope: the gaps
    # between those pulses are what carries the bits.
    (4, 0, [("ISM Transmitter", "ismXmitter", "ism.png", "tx"),
            ("ISM Receiver", "ismReceiver", "ismRx.png", "rx")]),
]


#: What each grid row holds, shown as a heading above it. Row 0 was the
#: old title bar and no longer exists: the wordmark and the gear are in
#: the rail now. ``scripts/test_theme.py`` reads this table out of here
#: with ``ast``, as it reads APP_TILES, so keep it a plain literal.
BANK_NAMES = {1: 'Signal generators', 2: 'Audio', 3: 'Video',
              4: 'Short range'}

#: The word a tile shows above its name, which is the direction it goes.
DIRECTION_KICKER = {'tx': 'TRANSMIT', 'rx': 'RECEIVE'}


def face_directions():
    """module name -> 'tx' or 'rx', read off the tile table."""
    return {face[1]: face[3] for _row, _col, faces in APP_TILES
            for face in faces}

# The grid: tiles at least MIN_TILE wide, as many to a row as fit, inside
# a column no wider than MAX_CONTENT.
MIN_TILE = 150
MAX_TILE = 230
TILE_GAP = 10
GUTTER = 16
MAX_CONTENT = 1080
BANK_GAP = 26
RAIL_HEIGHT = 54

#: How wide a tile should be when the launcher chooses its own size - the
#: one number that is a matter of taste. The window's first size is worked
#: out from it (see ``natural_size``): the widest bank in one row of tiles
#: this wide, and exactly tall enough for every bank, measured on the
#: machine it is running on. 185 is what the 1000 px window chosen by hand
#: gave, and what was approved looking at it.
PREFERRED_TILE = 185
BADGE_SIZE = 27
BADGE_INSET = 6
FLIP_MS = 380

#: How long a bank takes to wipe all the way shut or open, its chevron
#: turning with it. A toggle reversed partway takes only the rest of it.
BANK_TOGGLE_MS = 220

#: The gap between a bank's heading and its tiles. It is inside the
#: bank's body rather than in the page, because the body clips what it
#: holds and a tile lifts ``lift`` px under the pointer: with the gap
#: outside, a lifted tile in the top row lost its top edge.
TILE_HEADROOM = 10

#: How often the pulse along each bank's line moves - 30 a second. Its
#: timing is ``theme.PULSE``.
PULSE_FRAME_MS = 33

#: How long a tile takes to lift off the page under the pointer, on a
#: theme whose shadow deepens then.
LIFT_MS = 150

#: A tile's picture is cover-cropped once at this width and scaled down to
#: whatever the tile currently is, so resizing the window costs a blit
#: rather than a fresh crop of eighteen photographs.
PICTURE_REF = 420


def cover_crop(path, width):
    """A tile's picture cropped ``object-fit: cover`` at 4:3, as RGB pixels.

    A Qt stylesheet has no such property, so the pixels are prepared here
    instead - crop to the tile's shape about the middle. Thirteen of the
    sixteen icons are already 4:3 and lose nothing; the three squarer ones
    give up a little top and bottom, so anything written near an edge of a
    new icon wants it exported at 4:3. Kept as pixels, so the picture
    under the pointer is only a recolouring - see :func:`picture_pixmap`.
    """
    height = int(round(width * 3 / 4))
    image = Image.open(path).convert('RGB')
    scale = max(width / image.width, height / image.height)
    image = image.resize((max(width, int(image.width * scale + 0.5)),
                          max(height, int(image.height * scale + 0.5))),
                         Image.LANCZOS)
    left = (image.width - width) // 2
    top = (image.height - height) // 2
    return np.asarray(image.crop((left, top, left + width, top + height)))


def picture_pixmap(pixels, saturation=0.82):
    """Pixels from :func:`cover_crop`, at 82% of their saturation.

    The colour is pulled back a little so the photographs sit down into
    the panel rather than shouting off it; under the pointer it comes back,
    at 1.0.
    """
    data = pixels.astype(np.float32)
    luma = (data * (0.299, 0.587, 0.114)).sum(axis=2, keepdims=True)
    data = luma + (data - luma) * saturation
    height, width = pixels.shape[:2]
    buffer = np.clip(data, 0, 255).astype(np.uint8).tobytes()
    # QImage does not own the buffer it is handed, and this one is a local:
    # copy() before the bytes go out of scope, or the picture is garbage.
    picture = QImage(buffer, width, height, 3 * width, QImage.Format_RGB888)
    return QPixmap.fromImage(picture.copy())


def token_font(size_token, family_token='f_ui', weight=None, spacing=None):
    """A QFont from the shared tokens, in pixels rather than points.

    The stylesheet sizes in ``px`` and a QFont sizes in points by default,
    so a font built the obvious way comes out about a third too big on a
    96 dpi screen. Letter spacing has to come through here whatever
    happens: a Qt stylesheet has no ``letter-spacing`` property at all.
    """
    font = QFont(theme.TOKENS[family_token])
    font.setPixelSize(theme.TOKENS[size_token])
    if weight is not None:
        font.setWeight(weight)
    if spacing is not None:
        font.setLetterSpacing(QFont.PercentageSpacing, spacing)
    return font


def centred_column(parent, column=None):
    """A column no wider than MAX_CONTENT, centred in ``parent``.

    CSS's ``max-width: 1080px; margin: 0 auto``, done by hand, for the
    rail's contents and the body alike. Without it a maximised
    launcher left its tiles in the leftmost 1080 px with the bank
    hairlines and the rail running on across the whole screen. Returns the
    column; lay its contents out on it.

    The column is given the stretch and the two spacers none: Qt hands
    space to stretch first, so the column fills the window until it
    reaches its maximum, and only then do the spacers take what is left,
    evenly - which is centring.
    """
    outer = QHBoxLayout(parent)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    column = column if column is not None else QWidget()
    column.setObjectName('column')
    column.setMaximumWidth(MAX_CONTENT + 2 * GUTTER)
    column.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    outer.addStretch(0)
    outer.addWidget(column, 1)
    outer.addStretch(0)
    return column


def svg_icon(markup, size):
    """Render inline SVG to a pixmap. QtSvg has no ``currentColor``."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(markup.encode()).render(painter)
    painter.end()
    return pixmap


#: Drawn shadows, by (width, height, theme): every tile in a grid is the
#: same size, so this is one picture per layout.
_SHADOWS = {}


def shadow_pixmap(width, height, hover=False):
    """The theme's drop shadow for a tile this size - the deeper one under
    the pointer, with ``hover`` - and how far it reaches past the tile on
    every side.

    A CSS ``box-shadow`` done by hand: each (drop, blur, opacity)
    layer is the tile's rectangle, moved down, blurred - a CSS blur of B
    is a Gaussian of B/2 - and the layers laid over one another, each in
    the theme's ``shade`` or the colour it names.
    """
    layers = theme.TOKENS.get('shadow_hover' if hover else 'shadow') or ()
    key = (width, height, theme.current(), hover)
    if key not in _SHADOWS:
        margin = max((layer[0] + layer[1] for layer in layers), default=0) + 2
        size = (width + 2 * margin, height + 2 * margin)
        # Premultiplied colour and coverage, each layer laid under the ones
        # before it: the first of a box-shadow's list is the one on top.
        colour = np.zeros(size[::-1] + (3,), dtype=np.float32)
        cover = np.zeros(size[::-1], dtype=np.float32)
        for layer in reversed(layers):
            drop, blur, alpha = layer[:3]
            tint = QColor(theme.TOKENS[layer[3] if len(layer) > 3 else 'shade'])
            mask = Image.new('L', size, 0)
            ImageDraw.Draw(mask).rectangle(
                (margin, margin + drop, margin + width - 1,
                 margin + height - 1 + drop), fill=255)
            mask = mask.filter(ImageFilter.GaussianBlur(blur / 2))
            a = np.asarray(mask, dtype=np.float32) / 255 * alpha
            rgb = np.array([tint.red(), tint.green(), tint.blue()],
                           dtype=np.float32)
            colour = rgb * a[..., None] + colour * (1 - a[..., None])
            cover = a + cover * (1 - a)
        rgba = np.zeros(size[::-1] + (4,), dtype=np.uint8)
        # Back from premultiplied to straight colour, for the image format.
        straight = colour / np.maximum(cover, 1e-6)[..., None]
        rgba[..., :3] = np.clip(straight, 0, 255).astype(np.uint8)
        rgba[..., 3] = np.clip(cover * 255, 0, 255).astype(np.uint8)
        data = rgba.tobytes()
        image = QImage(data, size[0], size[1], 4 * size[0],
                       QImage.Format_RGBA8888)
        _SHADOWS[key] = (QPixmap.fromImage(image.copy()), margin)
    return _SHADOWS[key]


class ShadowColumn(QWidget):
    """The column the tiles sit in, which paints their drop shadows.

    Under each tile, from here, rather than as a
    ``QGraphicsDropShadowEffect`` on the tile: the tile's two caption
    labels already carry an opacity effect each, for the flip, and
    effects do not nest predictably. Only a theme with a ``shadow`` has
    any - Reading Room, where a tile is a card on a desk. A tile under the
    pointer lifts: its ``lift`` runs 0 to 1, and the two shadows are faded
    across by it.
    """

    def paintEvent(self, event):
        painter = QPainter(self)
        # A QWidget subclass paints its stylesheet background only when
        # asked to.
        option = QStyleOption()
        option.initFrom(self)
        self.style().drawPrimitive(QStyle.PE_Widget, option, painter, self)
        if theme.TOKENS.get('shadow'):
            for body in self.findChildren(BankBody):
                if body.isVisible():
                    self._paint_shadows(painter, body)
        painter.end()

    def _paint_shadows(self, painter, body):
        """The shadows under one bank's tiles.

        The tiles sit in the bank's body, not in the column, so where each
        one is has to be mapped here - ``tile.pos()`` is inside the body.
        While the body is wiping open or shut it clips its tiles at its
        bottom edge, and their shadows are cut off along the same line;
        at rest they reach past it as they always have.
        """
        painter.save()
        if body.reveal < 1.0:
            painter.setClipRect(0, 0, self.width(), body.geometry().bottom() + 1)
        for tile in body.findChildren(FlipTile):
            # A tile with no area has no shadow - and asked for one,
            # shadow_pixmap raises, which in a paintEvent ends the program.
            if not tile.isVisible() or tile.width() < 1 or tile.height() < 1:
                continue
            at = tile.mapTo(self, QPoint(0, 0))
            lift = tile.lift if theme.TOKENS.get('shadow_hover') else 0.0
            # Drawn from where the tile is, risen or not, as a CSS
            # box-shadow moves with its box: the deeper drop of the
            # lifted shadow is what puts it further below the card.
            for hover, weight in ((False, 1.0 - lift), (True, lift)):
                if weight <= 0:
                    continue
                shadow, margin = shadow_pixmap(tile.width(), tile.height(),
                                               hover)
                painter.setOpacity(weight)
                painter.drawPixmap(at.x() - margin, at.y() - margin, shadow)
            painter.setOpacity(1.0)
        painter.restore()


def shadow_column(widget):
    """The ShadowColumn a widget sits in, however deep, or None."""
    while widget is not None and not isinstance(widget, ShadowColumn):
        widget = widget.parentWidget()
    return widget


class BankBody(QWidget):
    """A bank's tiles, which wipe shut and open under its heading.

    The grid is held at its full height in ``inner`` and this widget
    shows ``reveal`` of it, 0 to 1, from the top: the tiles below the
    line are clipped away, never squeezed. Letting a layout shrink the
    grid instead - a maximum height on a widget with the grid in its
    layout - pressed every tile flat as it went, the caption printed
    over the picture, and down to 0 px, where a tile's shadow cannot be
    drawn and the launcher died painting it.

    It is hidden outright once shut, so the page does not keep a
    zero-height row, and tabbing does not land on tiles nobody can see.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('bank-body')
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.inner = QWidget(self)
        self.inner.setObjectName('bank-grid')
        self.grid = QGridLayout(self.inner)
        self.grid.setSpacing(TILE_GAP)
        self.grid.setContentsMargins(0, TILE_HEADROOM, 0, 0)
        self.reveal = 1.0

    def full_height(self):
        """How tall the bank is when it is open."""
        return self.inner.sizeHint().height()

    def sizeHint(self):
        return QSize(self.inner.sizeHint().width(),
                     int(round(self.full_height() * self.reveal)))

    def minimumSizeHint(self):
        # The grid's own minimum width, as when it sat in the page itself.
        return QSize(self.inner.minimumSizeHint().width(),
                     self.sizeHint().height())

    def set_reveal(self, reveal):
        self.reveal = min(1.0, max(0.0, float(reveal)))
        self.updateGeometry()

    def refit(self):
        """Take a new height after the grid has been laid out afresh.

        Called by hand after ``_lay_out``, because the launcher measures
        the page straight afterwards and the grid's own request arrives
        only once the event loop has run.
        """
        self.updateGeometry()
        self._fit_inner()

    def _fit_inner(self):
        self.inner.setGeometry(0, 0, self.width(), self.full_height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_inner()

    def event(self, event):
        # The grid asking for more or less room - a caption rewrapped, a
        # tile added - reaches here as a LayoutRequest, since this widget
        # has no layout of its own to pass it to.
        if event.type() == QEvent.LayoutRequest:
            self.refit()
        return super().event(event)


class BankHeader(QAbstractButton):
    """A bank's heading - its name, its line, and a chevron at the end of
    the line - which is also the button that collapses the bank.

    The whole row takes the click. The name is the biggest target on it,
    and the line between name and chevron would otherwise be a gap that
    did nothing; both ignore the mouse, as a QLabel and a plain QWidget
    do, so a press on either comes here. The chevron is painted in the
    room the row keeps clear at its right end, pointing down while the
    bank is open and right while it is shut, and ``turn`` - 1 open, 0
    shut - moves it between the two on the animation that wipes the
    tiles. Under the pointer it gets the gear's hover ground; from the
    keyboard, Tab reaches it and Space toggles, and the ring shows only
    when Tab brought the focus, as on the theme disc.

    The chevron also catches the pulse. As the pulse's bright head
    reaches the end of the line, ``glow`` - set by the launcher's pulse
    timer, 0 to 1 - turns the chevron the pulse's colour, with a halo
    round the strokes and a haze behind them, and lets it die away. The
    glow is painted rather than a QGraphicsDropShadowEffect: the name in
    this row carries one already, and effects do not nest predictably.

    No tooltip: a chevron at the end of a heading says what it does, and
    the user found tooltips that only repeat the page clutter. A screen
    reader gets "Collapse Audio" or "Expand Audio" as its name.
    """

    #: Kept clear at the right end of the row for the chevron's square.
    CHEVRON_ROOM = 26

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.setObjectName('bank-header')
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setAttribute(Qt.WA_Hover, True)
        self._ring = False
        self._turn = 1.0
        self._glow = 0.0
        self.bank_name = name
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, self.CHEVRON_ROOM, 0)
        row.setSpacing(12)
        # The heading charges before it fires its line's pulse.
        self.label = ChargeLabel(name)
        self.label.setObjectName('bank-name')
        self.label.setFont(token_font('s_sm'))
        row.addWidget(self.label)
        # The hairline that runs off the end of the heading. In the page
        # that is a ::after with an empty content; Qt's :: are sub-controls
        # of a known widget, not pseudo-elements anyone can invent, so it
        # is a widget.
        self.line = PulseLine()
        row.addWidget(self.line, 1)
        self.describe(is_open=True)

    def describe(self, is_open):
        self.setAccessibleName(
            f"{'Collapse' if is_open else 'Expand'} {self.bank_name}")

    def focusInEvent(self, event):
        self._ring = event.reason() in (Qt.TabFocusReason,
                                        Qt.BacktabFocusReason)
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._ring = False
        super().focusOutEvent(event)

    def _get_turn(self):
        return self._turn

    def _set_turn(self, value):
        self._turn = min(1.0, max(0.0, float(value)))
        self.update()

    turn = pyqtProperty(float, _get_turn, _set_turn)

    def set_glow(self, glow):
        """Light the chevron in the pulse's colour, 0 to 1 - as
        ``ChargeLabel.set_charge`` lights the name."""
        pulse = theme.TOKENS.get('pulse')
        glow = min(1.0, max(0.0, glow)) if pulse else 0.0
        if abs(glow - self._glow) < 0.004 and glow not in (0.0, 1.0):
            return
        if glow == self._glow:
            return
        self._glow = glow
        self.update()

    def chevron_box(self):
        """The chevron's square, in this row's own coordinates: as tall
        as the heading, so the chevron adds nothing to the page's height -
        16 to 19 px across the three themes' type."""
        side = self.height()
        return QRectF(self.width() - side, 0, side, side)

    def paintEvent(self, event):
        colours = theme.TOKENS
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = self.chevron_box()
        hovered = self.underMouse() and self.isEnabled()
        if hovered:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(colours['panel']))
            painter.drawRoundedRect(box, 2, 2)
        glow = self._glow if colours.get('pulse') else 0.0
        hot = QColor(colours.get('pulse') or colours['heading'])
        if glow > 0:
            # A haze of the pulse's colour behind the chevron, as far as
            # the row reaches above and below it.
            haze = QRadialGradient(box.center(), box.height() / 2)
            for at, strength in ((0.0, 0.5), (0.5, 0.22), (1.0, 0.0)):
                colour = QColor(hot)
                colour.setAlphaF(strength * glow)
                haze.setColorAt(at, colour)
            painter.setPen(Qt.NoPen)
            painter.setBrush(haze)
            painter.drawEllipse(box.center(), box.height() / 2,
                                box.height() / 2)
        if self.hasFocus() and self._ring:
            painter.setPen(QPen(QColor(colours['ink']), 1.5))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(box.adjusted(0.75, 0.75, -0.75, -0.75),
                                    2, 2)
        # Two strokes, drawn pointing down and turned a quarter to the
        # right as the bank shuts.
        painter.translate(box.center())
        painter.rotate(-90.0 * (1.0 - self._turn))
        chevron = (QPointF(-4.0, -2.0), QPointF(0.0, 2.0), QPointF(4.0, -2.0))
        if glow > 0:
            # A wide faint stroke of the pulse's colour round the chevron,
            # as round the name's letters: the haze alone is too soft to
            # read as lit.
            halo = QColor(hot)
            halo.setAlphaF(0.35 * glow)
            painter.setPen(QPen(halo, 4.5, Qt.SolidLine, Qt.RoundCap,
                                Qt.RoundJoin))
            painter.drawPolyline(*chevron)
        rest = QColor(colours['ink' if hovered else 'heading'])
        painter.setPen(QPen(mix_colour(rest, hot, glow), 1.6, Qt.SolidLine,
                            Qt.RoundCap, Qt.RoundJoin))
        painter.drawPolyline(*chevron)
        painter.end()


def mix_colour(rest, hot, amount):
    """``rest`` taken ``amount`` of the way to ``hot``, 0 to 1."""
    return QColor(*(int(round(a + (b - a) * amount)) for a, b in
                    zip(rest.getRgb()[:3], hot.getRgb()[:3])))


class ChargeLabel(QLabel):
    """A bank's name, which charges up before its line's pulse fires.

    ``charge`` runs 0 to 1: the text is painted that far from the heading
    colour toward the pulse's, and the glow round it - a
    ``QGraphicsDropShadowEffect`` with no offset - gathers with it. A glow
    alone barely showed on Reading Room's paper, dark text in a faint teal
    halo; the text itself taking the colour is what reads as lighting up.
    """

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.charge = 0.0
        self.glow = QGraphicsDropShadowEffect(self)
        self.glow.setOffset(0, 0)
        self.glow.setBlurRadius(0)
        self.glow.setColor(QColor(0, 0, 0, 0))
        self.setGraphicsEffect(self.glow)

    def set_charge(self, charge):
        pulse = theme.TOKENS.get('pulse')
        charge = min(1.0, max(0.0, charge)) if pulse else 0.0
        if abs(charge - self.charge) < 0.004 and charge not in (0.0, 1.0):
            return
        if charge == self.charge:
            return
        self.charge = charge
        colour = QColor(pulse) if pulse else QColor(0, 0, 0)
        colour.setAlphaF(charge)
        self.glow.setColor(colour)
        self.glow.setBlurRadius(6 + 24 * charge)
        self.update()

    def paintEvent(self, event):
        if self.charge <= 0:
            super().paintEvent(event)
            return
        rest = QColor(theme.TOKENS['heading'])
        hot = QColor(theme.TOKENS.get('pulse') or theme.TOKENS['heading'])
        mix = mix_colour(rest, hot, self.charge)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(self.font())
        # A stroke of the pulse's colour round the letters, fading in with
        # the charge. The glow effect blurs the shape it is given, and thin
        # text gives it little to spread: this is what makes it bright.
        rect = self.contentsRect()
        metrics = QFontMetrics(self.font())
        x = rect.x()
        y = rect.y() + (rect.height() + metrics.ascent() - metrics.descent()) / 2
        outline = QPainterPath()
        outline.addText(QPointF(x, y), self.font(), self.text())
        halo = QColor(hot)
        halo.setAlphaF(0.3 * self.charge)
        painter.strokePath(outline, QPen(halo, 1.6, Qt.SolidLine, Qt.RoundCap,
                                         Qt.RoundJoin))
        painter.setPen(mix)
        painter.drawText(rect, int(self.alignment()), self.text())
        painter.end()


class PulseLine(QWidget):
    """The hairline that runs off a bank's name, with a pulse going down it.

    It was a ``QFrame``; it is drawn now so that a theme with a ``pulse``
    colour can send a signal along it - a short bright head with a tail
    behind it, left to right, the launcher's timer setting where it is
    (``phase``, 0 to 1, or None between pulses). Three pixels tall, so the
    pulse can carry a faint glow either side of the one-pixel line.
    """

    #: How long the pulse is, head and tail, in pixels.
    LENGTH = theme.PULSE['length']

    #: Where along it, from the back, the pulse is brightest - its head.
    #: The chevron at the end of the line lights as this reaches it.
    BRIGHTEST = 0.8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('hairline')
        self.setFixedHeight(3)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.phase = None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(0, 1, self.width(), 1, QColor(theme.TOKENS['rule_soft']))
        pulse = theme.TOKENS.get('pulse')
        if pulse and self.phase is not None:
            head = -self.LENGTH + (self.width() + self.LENGTH) * self.phase
            for top, height, strength in ((0, 3, 0.3), (1, 1, 1.0)):
                colour = QColor(pulse)
                clear = QColor(pulse)
                clear.setAlpha(0)
                colour.setAlphaF(strength)
                gradient = QLinearGradient(head, 0, head + self.LENGTH, 0)
                gradient.setColorAt(0.0, clear)
                gradient.setColorAt(self.BRIGHTEST, colour)
                gradient.setColorAt(1.0, clear)
                painter.fillRect(QRectF(head, top, self.LENGTH, height),
                                 gradient)
        painter.end()


class ThemeDisc(QAbstractButton):
    """The theme in force, as a disc, and a click moves on to the next.

    voice-summary's picker, carried across: the disc *is* the theme - its
    ground, ringed in its rule, with the colour its plots draw a signal in
    at the centre. There is no name on it, because the window round it is
    the theme and would say the same thing; the name is in the tooltip,
    and the accessible name also says where a click goes, since a control
    that cycles otherwise gives no clue.

    Painted rather than styled: a stylesheet has no circle, only a rounded
    rectangle, and the dot would have to be a widget of its own.
    """

    #: The disc is 22 px, to sit level with the radio tag beside it; the
    #: square that takes the click is the gear's 32.
    DIAMETER = 22
    DOT = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(32, 32)
        self.setCursor(Qt.PointingHandCursor)
        # Focus from the keyboard only, and the ring only when Tab brought
        # it - CSS's :focus-visible. It is the first thing in the
        # window that takes focus, so Qt hands it focus as the window
        # opens, and a ring then would sit there from the start.
        self.setFocusPolicy(Qt.TabFocus)
        self._ring = False
        # Repaint on the pointer arriving and leaving, for the hover growth.
        self.setAttribute(Qt.WA_Hover, True)
        self.describe()

    def focusInEvent(self, event):
        self._ring = event.reason() in (Qt.TabFocusReason,
                                        Qt.BacktabFocusReason)
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._ring = False
        super().focusOutEvent(event)

    def describe(self):
        """Say which theme this is and which comes next; repaint."""
        now = theme.current()
        name = theme.NAMES[now]
        # Pointing at the disc already says it is the theme; the tooltip
        # only has to say which. A screen reader has no pointer to say so,
        # which is why the accessible name keeps the word.
        self.setToolTip(name)
        self.setAccessibleName(f"Theme: {name}. Activate for "
                               f"{theme.NAMES[theme.after(now)]}.")
        self.update()

    def paintEvent(self, event):
        colours = theme.TOKENS
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        centre = QPointF(self.width() / 2, self.height() / 2)
        grow = 1.12 if self.underMouse() else 1.0
        radius = self.DIAMETER * grow / 2
        painter.setPen(QPen(QColor(colours['rule']), 1))
        painter.setBrush(QColor(colours['ground']))
        painter.drawEllipse(centre, radius - 0.5, radius - 0.5)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(colours['trace']))
        painter.drawEllipse(centre, self.DOT * grow / 2, self.DOT * grow / 2)
        if self.hasFocus() and self._ring:
            painter.setPen(QPen(QColor(colours['ink']), 1.5))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(centre, radius + 3, radius + 3)
        painter.end()


class FlipTile(QPushButton):
    """One tile of the launcher grid, which may have more than one face.

    Its anatomy: the picture full width across
    the top at 4:3, then the direction in condensed letter-spaced caps,
    then the app's name, both ranged left. Clicking it launches whichever
    app is showing; clicking the badge in its corner turns the tile over,
    animated as a card being flipped rather than simply swapped, so it is
    obvious that this is one tile with two sides and not a button that
    mysteriously rearranged itself.

    The turn is drawn by squeezing the picture horizontally through zero
    width and back - ``cos`` of the animation phase - and swapping the face
    at the moment it is edge on, which is exactly when nothing of it is
    visible. The caption fades on the same curve; squeezing text instead
    just looks like a rendering fault.

    Which side is up is remembered in ``window_settings.json``, so a tile
    left showing the receiver is still showing it next time.
    """

    def __init__(self, launcher, faces, width=MIN_TILE):
        super().__init__()
        self.setObjectName('tile')
        self.launcher = launcher
        self.faces = list(faces)
        self.key = self.faces[0][1]      # the first module names the tile
        self.face = 0
        self._target = 0
        self._flip = 0.0
        self._width = None
        self._picture_size = QSize(width, int(round(width * 3 / 4)))
        # Which faces the selected radio can actually run, and whether the
        # tile can be used at all. Both are set by ``set_directions``.
        self.allowed = list(range(len(self.faces)))
        self.usable = True
        self._remember_turn = True
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self._crops = [cover_crop(f"icons/{face[2]}", PICTURE_REF)
                       for face in self.faces]
        self._pixmaps = self._lit = []
        self._fraction = 1.0
        self._hover = False
        # How far the tile has lifted off the page under the pointer, 0 to
        # 1, for a theme whose shadow deepens then - see ShadowColumn.
        self._lift = 0.0
        # Where the layout put the tile, while it is risen off it, and where
        # the tile last moved itself to.
        self._rest = None
        self._placed = None
        self._lift_animation = QPropertyAnimation(self, b'lift', self)
        self._lift_animation.setDuration(LIFT_MS)
        self._lift_animation.setEasingCurve(QEasingCurve.OutCubic)
        self.load_pictures()

        layout = QVBoxLayout(self)
        # One pixel in from every edge, for the stylesheet's border: a Qt
        # layout does not know a stylesheet drew one, so at 0 the picture
        # sat on the tile's top and left border and left a strip of panel
        # between itself and the right one.
        layout.setContentsMargins(1, 1, 1, 12)
        layout.setSpacing(0)

        self.picture = QLabel()
        self.picture.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.picture)

        self.dir_label = QLabel()
        self.dir_label.setObjectName('dir')
        self.dir_label.setFont(token_font('s_xs', 'f_num', QFont.DemiBold, 112))
        self.dir_label.setContentsMargins(11, 9, 11, 0)
        layout.addWidget(self.dir_label)

        self.text_label = QLabel()
        self.text_label.setObjectName('name')
        self.text_label.setFont(token_font('s_sm'))
        self.text_label.setWordWrap(True)
        self.text_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.text_label.setContentsMargins(11, 1, 11, 0)
        layout.addWidget(self.text_label)
        layout.addStretch(1)

        # One effect per label rather than one on a container holding both.
        # Effects do not nest predictably, and a container would also need
        # a background rule of its own to stop the blanket QWidget colour
        # painting a rectangle over the tile.
        self._fades = []
        for label in (self.dir_label, self.text_label):
            fade = QGraphicsOpacityEffect(label)
            fade.setOpacity(1.0)
            label.setGraphicsEffect(fade)
            self._fades.append(fade)

        self.badge = None
        if len(self.faces) > 1:
            self._build_badge()

        self.clicked.connect(self._launch)
        self.animation = QPropertyAnimation(self, b'flip_phase', self)
        self.animation.setDuration(FLIP_MS)
        self.animation.setEasingCurve(QEasingCurve.InOutQuad)

        self.set_width(width)
        self.set_face(launcher.saved_face(self.key, len(self.faces)))

    # -- size ------------------------------------------------------------

    def sizeHint(self):
        """The layout's size, not a button's.

        A QPushButton sizes itself from its own text and icon and ignores
        any layout it holds; a tile has neither, so its own hint is a small
        empty button. On screen that never showed, because showing the
        window activates each tile's layout and that pushes the real height
        on as a minimum size. But the launcher measures how tall it wants to
        be *before* it is shown (``natural_size``), and asked then every
        tile claimed to be a few pixels high: the window came out at its
        600 px minimum with 217 px of grid to scroll.
        """
        return self.layout().sizeHint()

    def minimumSizeHint(self):
        return self.layout().minimumSize()

    def caption_height(self, width):
        """How tall this tile's name needs to be, wrapped to ``width``.

        The grid takes the largest across every tile and gives them all
        that, so one long name cannot make its own tile taller than the
        rest of its row.
        """
        metrics = QFontMetrics(self.text_label.font())
        inner = max(1, width - 2 - 22)      # the border and the 11px sides
        lines = 1
        for face in self.faces:
            box = metrics.boundingRect(0, 0, inner, 10000,
                                       Qt.TextWordWrap, face[0])
            lines = max(lines, max(1, round(box.height() / metrics.lineSpacing())))
        return lines * metrics.lineSpacing() + 2

    def set_width(self, width):
        """Take the width the grid has worked out for this row."""
        width = int(width)
        if self._width == width:
            return
        self._width = width
        inner = width - 2                   # inside the 1px border
        self._picture_size = QSize(inner, int(round(inner * 3 / 4)))
        self.setFixedWidth(width)
        self.picture.setFixedSize(self._picture_size)
        if self.badge is not None:
            self.badge.move(inner - BADGE_SIZE - BADGE_INSET + 1,
                            BADGE_INSET + 1)
        self._draw(abs(math.cos(math.pi * self._flip)))

    def set_caption_height(self, height):
        self.text_label.setFixedHeight(int(height))

    # -- the badge -------------------------------------------------------

    def _build_badge(self):
        self.badge = QPushButton("↻", self)
        self.badge.setObjectName('flip')
        self.badge.setFixedSize(BADGE_SIZE, BADGE_SIZE)
        self.badge.setCursor(Qt.PointingHandCursor)
        self.badge.clicked.connect(self.flip)
        self.badge.raise_()

    # -- faces -----------------------------------------------------------

    def current(self):
        return self.faces[self.face]

    def set_face(self, index, remember=False):
        """Show a face at once, with no animation."""
        self.face = index % len(self.faces)
        self._target = self.face
        self._flip = 0.0
        self._show_face()
        self._draw(1.0)
        for fade in self._fades:
            fade.setOpacity(1.0)
        if remember:
            self.launcher.remember_face(self.key, self.face)

    def _next_allowed(self):
        """The face after this one, among those the radio can run."""
        if len(self.allowed) < 2:
            return self.face
        here = (self.allowed.index(self.face) if self.face in self.allowed
                else -1)
        return self.allowed[(here + 1) % len(self.allowed)]

    def _show_face(self):
        name, _module, _icon, direction = self.current()
        self.text_label.setText(name)
        self.dir_label.setText(DIRECTION_KICKER.get(direction, ''))
        # A screen reader's name for the tile; a button with no text of its
        # own has none otherwise.
        self.setAccessibleName(name)
        if not self.usable:
            return
        # No tooltip on a tile that can be used: it would only repeat the
        # name printed on it. The badge keeps one - it is a glyph that does
        # not say what it does - and so does a dimmed tile, which says why.
        self.setToolTip('')
        if self.badge is not None and len(self.allowed) > 1:
            nxt = self.faces[self._next_allowed()][0]
            self.badge.setToolTip(f"Flip to {nxt}")

    def flip(self, remember=True):
        """Turn to the next face the radio can run."""
        if (len(self.allowed) < 2
                or self.animation.state() == QPropertyAnimation.Running):
            return
        self.turn_to(self._next_allowed(), remember=remember)

    def turn_to(self, index, remember=True):
        """Animate round to a particular face."""
        if index == self.face or self.animation.state() == QPropertyAnimation.Running:
            return
        self._target = index
        self._remember_turn = remember
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.start()

    # -- following the radio ---------------------------------------------

    def set_directions(self, directions, radio_type, animate=True):
        """Arrange the tile around what the selected radio can do.

        A tile whose only app the radio cannot run is dimmed and refuses
        clicks, and says why: a greyed-out square that explains itself beats
        an app that opens and then fails on a device it was never going to
        be able to use. A tile with one usable face loses its badge - there
        is nothing to flip to. Only a tile with two usable faces stays
        flippable, which on a HackRF is all of them.

        The *saved* face is left alone throughout. Switching to a BB60D and
        back to a HackRF puts every tile back the way it was, because the
        turns a radio forces are deliberately not remembered.
        """
        self.allowed = [i for i, face in enumerate(self.faces)
                        if face[3] in directions]
        self.usable = bool(self.allowed)
        self.setEnabled(self.usable)

        if not self.usable:
            wants = DIRECTION_WORDS.get(self.faces[0][3], self.faces[0][3])
            radio = RADIO_NAMES.get(radio_type, 'The selected radio')
            self.setToolTip(f"{self.faces[0][0]} needs a radio that can "
                            f"{wants}. {radio} cannot.")
            if self.badge is not None:
                self.badge.setVisible(False)
            self._show_face()
            self._draw(1.0)
            return

        if self.badge is not None:
            self.badge.setVisible(len(self.allowed) > 1)

        # Prefer whichever side was last left showing, if this radio can
        # run it; otherwise the only one it can.
        want = self.launcher.saved_face(self.key, len(self.faces))
        if want not in self.allowed:
            want = self.allowed[0]
        if want != self.face:
            if animate:
                self.turn_to(want, remember=False)
            else:
                self.set_face(want)
        else:
            self._show_face()
            self._draw(1.0)

    # -- the turn --------------------------------------------------------

    def _get_flip(self):
        return self._flip

    def _set_flip(self, value):
        self._flip = float(value)
        # 1 at the start, 0 edge on, -1 fully round: the sign is what says
        # which face should be showing.
        squeeze = math.cos(math.pi * self._flip)
        if squeeze < 0 and self.face != self._target:
            self.face = self._target
            self._show_face()
            # A turn the radio forced is not a preference, so it is not
            # saved: put a two-way radio back and the tile returns to
            # whichever side the user actually chose.
            if self._remember_turn:
                self.launcher.remember_face(self.key, self.face)
        self._draw(abs(squeeze))
        for fade in self._fades:
            fade.setOpacity(abs(squeeze))

    # Named for what it is - how far through the turn - and not ``flip``,
    # which is the method that starts one. QPropertyAnimation looks the
    # property up by this attribute name.
    flip_phase = pyqtProperty(float, _get_flip, _set_flip)

    def load_pictures(self):
        """Colour the pictures from the crops, and redraw.

        The colour is pulled back a little, and brought out again under
        the pointer.
        """
        self._pixmaps = [picture_pixmap(crop) for crop in self._crops]
        self._lit = [picture_pixmap(crop, saturation=1.0)
                     for crop in self._crops]
        # Not while the tile is still being built: set_face draws it then.
        if getattr(self, 'picture', None) is not None:
            self._draw(self._fraction)

    def enterEvent(self, event):
        self._hover = True
        self._draw(self._fraction)
        self._lift_to(1.0 if self.usable else 0.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self._draw(self._fraction)
        self._lift_to(0.0)
        super().leaveEvent(event)

    def _lift_to(self, target):
        """Fade the tile's shadow toward lifted (1) or resting (0)."""
        if not theme.TOKENS.get('shadow_hover'):
            self._lift = 0.0
            return
        self._lift_animation.stop()
        self._lift_animation.setStartValue(self._lift)
        self._lift_animation.setEndValue(float(target))
        self._lift_animation.start()

    def _get_lift(self):
        return self._lift

    def _set_lift(self, value):
        self._lift = float(value)
        self._rise()
        # The shadow is painted by the column the tile sits in - not by
        # the tile's parent, which is its bank's grid.
        column = shadow_column(self)
        if column is not None:
            column.update()

    def _rise(self):
        """Move the tile up off its place by as much as it is lifted.

        By hand, off the place the grid layout gave it, which is remembered
        to come back down to.
        """
        rise = int(round(self._lift * (theme.TOKENS.get('lift') or 0)))
        if rise and self._rest is None:
            self._rest = self.pos()
        if self._rest is not None:
            self._placed = QPoint(self._rest.x(), self._rest.y() - rise)
            self.move(self._placed)
            if not rise:
                self._rest = self._placed = None

    def moveEvent(self, event):
        """A move the tile did not make itself is the grid placing it.

        A layout pass puts every tile back in its cell, and one can land
        while a tile is lifted - the grid is laid out afresh after a change
        of theme, and applied a moment later. That cell is where the tile
        rests now, so it rises from there; remembering the old one instead
        sent the tile back to a place that no longer existed when the
        pointer left.
        """
        super().moveEvent(event)
        if self._rest is not None and self.pos() != self._placed:
            self._rest = self.pos()
            self._rise()

    def settle(self):
        """Back on the page at once: the grid is about to place it again."""
        self._lift_animation.stop()
        rest, self._rest, self._placed = self._rest, None, None
        if rest is not None:
            self.move(rest)
        self._lift = 0.0

    lift = pyqtProperty(float, _get_lift, _set_lift)

    def _draw(self, width_fraction):
        """Paint the current picture squeezed to a fraction of its width."""
        self._fraction = width_fraction
        size = self._picture_size
        target = QPixmap(size)
        target.fill(Qt.transparent)
        width = max(1, int(round(size.width() * width_fraction)))
        painter = QPainter(target)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        # Dimmed here rather than with a QGraphicsOpacityEffect on the tile,
        # because the two caption labels already carry one each and effects
        # do not nest predictably. The caption dims through the stylesheet's
        # own :disabled colours instead.
        if not self.usable:
            painter.setOpacity(0.34)
        lit = self._hover and self.usable
        picture = (self._lit if lit else self._pixmaps)[self.face]
        painter.drawPixmap((size.width() - width) // 2, 0,
                           picture.scaled(width, size.height(),
                                          Qt.IgnoreAspectRatio,
                                          Qt.SmoothTransformation))
        painter.end()
        self.picture.setPixmap(target)

    def _launch(self):
        self.launcher.launch_application(self.current()[1])


class RFbenchToolkit(QMainWindow):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app  # Store reference to QApplication
        self.setWindowTitle("RF Bench Toolkit")
        self.setMinimumSize(800, 600)
        
        # Create config directory if it doesn't exist
        self.config_dir = "config"
        self.settings_file = os.path.join(self.config_dir, "window_settings.json")
        os.makedirs(self.config_dir, exist_ok=True)
        
        # Before anything is built: the gear and the theme disc are drawn
        # in the theme's colours as they are made.
        use_saved_theme()
        theme.load_fonts()

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_rail())

        # The banks scroll and the rail does not. The old grid had no
        # scroll area at all, so on a 768-high laptop the video row sat
        # below the bottom edge with no way to reach it.
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        self._column = ShadowColumn()
        self._body = QVBoxLayout(centred_column(body, self._column))
        self._body.setContentsMargins(GUTTER, 0, GUTTER, 40)
        self._body.setSpacing(0)
        self._scroll.setWidget(body)
        outer.addWidget(self._scroll, 1)
        # The viewport is what the tiles have to fit inside, and it changes
        # width without the window being resized - the scroll bar appearing
        # is enough. Watching the window instead left the grid laid out for
        # whatever width it happened to have before it was first shown.
        self._scroll.viewport().installEventFilter(self)

        # The tiles, from the table at the top of this file.
        self.tiles = {}
        self._banks = []
        self._lines = []
        self._headings = []
        # Per bank, in order: its heading, its body, the animation that
        # wipes it, and its row in APP_TILES. Which rows are collapsed is
        # read once here and written on each toggle - never read back
        # from the file, which the pulse would otherwise do 30 times a
        # second.
        self._headers = []
        self._bodies = []
        self._bank_anims = []
        self._bank_rows = []
        self._collapsed = self.saved_collapsed()
        self._layout_at = None
        self._build_banks()

        # One timer moves every line's pulse. It runs only while the
        # window is on screen and the theme has a pulse - see _sync_pulse -
        # so while an app runs, with the launcher hidden, it costs the app
        # nothing.
        self._pulse_clock = QElapsedTimer()
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(PULSE_FRAME_MS)
        self._pulse_timer.timeout.connect(self._move_pulse)

        # Arrange them around whichever radio is selected. No animation on
        # the way up - there is nothing to show a turn away from yet.
        self.apply_radio_directions(animate=False)

        apply_launcher_theme(self)

        # Load last position or center if none exists
        self.load_window_position()
        self._relayout()

    # ------------------------------------------------------------ the page
    def _build_rail(self):
        """The header: wordmark, which radio is selected, the theme, and
        the gear."""
        rail = QWidget()
        rail.setObjectName('rail')
        rail.setFixedHeight(RAIL_HEIGHT)
        # The rule under the rail runs the full width; what sits on it
        # lines up with the tiles below.
        row = QHBoxLayout(centred_column(rail))
        row.setContentsMargins(GUTTER, 0, GUTTER, 0)
        row.setSpacing(14)

        mark = QLabel("RF Bench Toolkit")
        mark.setObjectName('mark')
        mark.setFont(token_font('s_lg', 'f_num', QFont.DemiBold))
        row.addWidget(mark)
        row.addStretch(1)

        self.radio_tag = QLabel("—")
        self.radio_tag.setObjectName('radio-tag')
        self.radio_tag.setFont(token_font('s_xs'))
        self.radio_tag.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        row.addWidget(self.radio_tag, 0, Qt.AlignVCenter)

        # The theme picker, as voice-summary has it: the word, then the
        # disc. Held closer together than the rail's own spacing, since
        # they are one control.
        picker = QHBoxLayout()
        picker.setSpacing(4)
        label = QLabel("Themes")
        label.setObjectName('theme-label')
        label.setFont(token_font('s_sm'))
        picker.addWidget(label, 0, Qt.AlignVCenter)
        self.theme_disc = ThemeDisc()
        self.theme_disc.clicked.connect(self.next_theme)
        picker.addWidget(self.theme_disc)
        row.addLayout(picker)

        self.gear = QPushButton()
        self.gear.setObjectName('gear')
        self.gear.setFixedSize(32, 32)
        self.gear.setCursor(Qt.PointingHandCursor)
        self.gear.setIcon(QIcon(svg_icon(theme.gear_svg(), 17)))
        self.gear.setIconSize(QSize(17, 17))
        self.gear.setToolTip("Settings")
        self.gear.clicked.connect(self.show_settings)
        row.addWidget(self.gear)
        return rail

    def next_theme(self):
        """Move on to the next theme, keep it, and repaint in it.

        Kept in ``window_settings.json``, beside the radio, rather than
        anywhere of the launcher's own: the dialogs and the flowgraph
        windows read it from there when they open.
        """
        self.save_setting('theme', theme.after(theme.current()))
        apply_launcher_theme(self)
        # The two things drawn in the theme's colours rather than styled.
        self.gear.setIcon(QIcon(svg_icon(theme.gear_svg(), 17)))
        self.theme_disc.describe()
        # A theme may bring its own faces, and every caption's height is
        # measured from the face in force - so lay the grid out afresh, and
        # again once the event loop has run: a stylesheet's fonts reach the
        # labels by posted events, so measured at once the page still had
        # the last theme's type, and left Slate after Walnut with 183 px
        # tiles and 10 px to spare where 185 fit.
        self._layout_at = None
        self._relayout()
        QTimer.singleShot(0, self._relayout_afresh)
        # The shadows under the tiles, and the pulse, come and go with it.
        for tile in self.tiles.values():
            tile.settle()
        self._column.update()
        self._sync_pulse()


    def save_setting(self, key, value):
        """Merge one setting into ``window_settings.json``."""
        try:
            update_app_config(self.settings_file, {key: value})
        except Exception as e:
            print(f"Error saving {key}: {e}")

    def _build_banks(self):
        """A heading and a body of tiles for each row of APP_TILES.

        The heading is a button that collapses the bank - see BankHeader
        and BankBody. A bank the launcher was left with collapsed comes
        up collapsed, with nothing to animate.
        """
        rows = {}
        for row, col, faces in APP_TILES:
            rows.setdefault(row, []).append((col, faces))

        for row in sorted(rows):
            self._body.addSpacing(BANK_GAP)

            header = BankHeader(BANK_NAMES.get(row, f"Row {row}"))
            self._headings.append(header.label)
            self._lines.append(header.line)
            self._body.addWidget(header)

            body = BankBody()
            tiles = [self.create_tile(faces) for _col, faces in sorted(rows[row])]
            self._body.addWidget(body)
            self._banks.append((body.grid, tiles))

            bank = len(self._bodies)
            # One animation per bank drives both the wipe and the turn of
            # the chevron, so the two cannot drift apart.
            anim = QVariantAnimation(self)
            anim.setEasingCurve(QEasingCurve.InOutQuad)
            anim.valueChanged.connect(
                lambda value, bank=bank: self._show_bank(bank, value))
            anim.finished.connect(
                lambda bank=bank: self._bank_settled(bank))
            header.clicked.connect(
                lambda _checked=False, bank=bank: self.toggle_bank(bank))
            self._headers.append(header)
            self._bodies.append(body)
            self._bank_anims.append(anim)
            self._bank_rows.append(row)
            if row in self._collapsed:
                header.describe(is_open=False)
                self._show_bank(bank, 0.0)
                self._bank_settled(bank)

        self._body.addStretch(1)

    # ------------------------------------------------ collapsing the banks
    def saved_collapsed(self):
        """The rows the launcher was last left with collapsed."""
        rows = {row for row, _col, _faces in APP_TILES}
        saved = read_settings(self.settings_file).get('collapsed_banks')
        try:
            return {int(row) for row in saved or ()} & rows
        except (TypeError, ValueError):
            print(f"Ignoring a collapsed_banks that is not a list of rows: "
                  f"{saved!r}")
            return set()

    def toggle_bank(self, bank):
        """Collapse bank ``bank`` if it is open, open it if it is not.

        Pressed again partway, it turns round from wherever it has got to,
        over only the time the rest of the way takes - the animation is
        stopped and started afresh, never queued.
        """
        row = self._bank_rows[bank]
        shutting = row not in self._collapsed
        if shutting:
            self._collapsed.add(row)
        else:
            self._collapsed.discard(row)
        self.save_setting('collapsed_banks', sorted(self._collapsed))
        self._headers[bank].describe(is_open=not shutting)

        body, anim = self._bodies[bank], self._bank_anims[bank]
        target = 0.0 if shutting else 1.0
        start = body.reveal
        anim.stop()
        if start == target:
            self._show_bank(bank, target)
            self._bank_settled(bank)
            return
        body.show()
        # Quietly: a stopped animation works its value out afresh when its
        # ends are changed, and says so - which, stopped at the end of the
        # last toggle, flung the bank straight to the new end before the
        # wipe had begun.
        anim.blockSignals(True)
        anim.setStartValue(start)
        anim.setEndValue(target)
        anim.setDuration(max(1, int(round(
            BANK_TOGGLE_MS * abs(target - start)))))
        anim.blockSignals(False)
        anim.start()

    def _show_bank(self, bank, reveal):
        """Open bank ``bank`` this far, 0 shut to 1 open, chevron and all."""
        self._bodies[bank].set_reveal(reveal)
        self._headers[bank].turn = reveal
        # The shadows are the column's, and cut off with the tiles.
        self._column.update()

    def _bank_settled(self, bank):
        """Hide a bank that has finished shutting, so it takes no room."""
        body = self._bodies[bank]
        body.setVisible(body.reveal > 0.0)
        self._column.update()

    def _relayout(self):
        """Fit as many tiles to a row as the window has room for.

        CSS would say ``repeat(auto-fill, minmax(150px, 1fr))`` inside a
        column capped at 1080; this is that arithmetic done by hand,
        because a Qt stylesheet does no layout at all. Nothing moves unless
        the answer actually changed - a resize otherwise re-adds every tile
        to its grid on every pixel of drag.

        **It never makes more columns than the widest bank has tiles.** No
        bank has more than five, so a sixth column was always empty - and
        worse, it arrived right at the default window width: 980 wide gave
        five tiles of 181 px, 1000 gave six of 153, three pixels off the
        minimum, with the sixth slot holding nothing in any bank. Capped,
        a wider window makes the five tiles wider instead. Taken from the
        table rather than written down as 5, so a bank that gains a sixth
        tile gets a sixth column without anyone remembering to raise it.
        """
        if not self._banks:
            return
        # The width is decided from the window alone, never from whether
        # the scroll bar happens to be showing. Lay out as though there were
        # none; if the page then comes out taller than the viewport, the bar
        # will appear, so lay out for the width it leaves straight away.
        # Taken from the bar's state instead, the grid could not settle
        # when the page sat within a caption's wrap of fitting: Walnut at
        # Slate's window size was 3 px too tall with 185 px tiles, which
        # brought the bar, which made them 183, which rewrapped a caption,
        # which let the page fit, which took the bar away - for ever.
        viewport = self._scroll.viewport()
        bar = self._scroll.verticalScrollBar()
        whole = viewport.width() + (bar.width() if bar.isVisible() else 0)
        self._lay_out(*self._grid_for(whole))
        self._body.invalidate()
        if self._body.sizeHint().height() > viewport.height():
            self._lay_out(*self._grid_for(whole - bar.sizeHint().width()))

    def _relayout_afresh(self):
        self._layout_at = None
        self._relayout()

    def _grid_for(self, viewport_width):
        """(columns, tile width) for a viewport this wide."""
        available = viewport_width - 2 * GUTTER
        content = max(MIN_TILE, min(MAX_CONTENT, available))
        columns = max(1, (content + TILE_GAP) // (MIN_TILE + TILE_GAP))
        columns = min(columns, self._widest_bank())
        width = min(MAX_TILE, (content - TILE_GAP * (columns - 1)) // columns)
        return columns, width

    def _widest_bank(self):
        return max(len(tiles) for _grid, tiles in self._banks)

    def _lay_out(self, columns, width):
        """Put every tile in its cell at this many columns of this width."""
        if (columns, width) == self._layout_at:
            return
        self._layout_at = (columns, width)

        caption = max(tile.caption_height(width)
                      for _grid, tiles in self._banks for tile in tiles)
        for grid, tiles in self._banks:
            for tile in tiles:
                tile.settle()
                grid.removeWidget(tile)
            for index, tile in enumerate(tiles):
                tile.set_width(width)
                tile.set_caption_height(caption)
                grid.addWidget(tile, index // columns, index % columns,
                               Qt.AlignTop | Qt.AlignLeft)
            # One spare column on the right soaks up the slack, so a bank
            # with fewer tiles than columns still starts at the left.
            for column in range(columns + 1):
                grid.setColumnStretch(column, 1 if column == columns else 0)
        # Each bank's body takes its new height now, not once the grid's
        # own request has come round the event loop: _relayout and
        # natural_size measure the page straight after this.
        for body in self._bodies:
            body.refit()
        # A shadow reaches past its tile, so a tile that has moved leaves
        # some behind outside the patch Qt repaints for it.
        self._column.update()

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.Resize
                and obj is self._scroll.viewport()):
            self._relayout()
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    def center_window(self):
        """Open at the default size, centred, and save that.

        This is the no-saved-geometry path, so it is also where the default
        size belongs - every caller is a case of there being nothing to
        restore.
        """
        self.resize_to_default()
        # Get the screen geometry
        screen = self.app.primaryScreen().geometry()
        # Calculate center position
        window_geometry = self.frameGeometry()
        center_point = screen.center()
        window_geometry.moveCenter(center_point)
        self.move(window_geometry.topLeft())
        
        # Save position to config file
        self.save_window_position()
        
    def natural_size(self):
        """The size the launcher wants: every bank in view, none wrapped.

        Wide enough for the widest bank's tiles in one row at
        PREFERRED_TILE, and tall enough that nothing has to scroll. The
        height is *measured* - the grid is laid out at that width and the
        page asked how tall it came out - rather than worked out, because
        it depends on how tall this machine sets a line of text. A size
        written down by hand was 1000x820, which fitted here by 2 px and
        would have shown a scroll bar on a machine whose fonts run a pixel
        taller; measured, it is right wherever it is measured.

        The stylesheet has to have reached every label first, since it is
        what sets their type size, and Qt only applies it when a widget is
        polished - which otherwise happens when it is first shown.
        """
        self.ensurePolished()
        columns = self._widest_bank()
        self._lay_out(columns, PREFERRED_TILE)
        width = (columns * PREFERRED_TILE + (columns - 1) * TILE_GAP
                 + 2 * GUTTER)
        # The column's own layout, not the scroll area's widget: Qt
        # invalidates a layout by *posting* an event, so before the window
        # has run its event loop the outer widget still answers from its
        # cache - 143x196 against a real 997x766, measured.
        self._body.invalidate()
        height = RAIL_HEIGHT + self._body.sizeHint().height()
        # Measured with every bank open, whichever are collapsed, so the
        # window opening one later does not bring a scroll bar the default
        # size would not have had. A hidden body is not in the page's
        # measure at all; a part-open one is in it at its part.
        for body in self._bodies:
            shown = 0 if body.isHidden() else body.sizeHint().height()
            height += body.full_height() - shown
        return width, height

    def resize_to_default(self):
        """The natural size, or as much of it as the screen has room for.

        Measured against *available* geometry rather than the whole screen,
        so a panel or a dock does not leave part of the window under it. A
        screen too short for all three banks - the 1366x768 laptop is one -
        gets a window that scrolls, which is better than one that does not
        fit.

        Before any of this there was no default at all: with nothing saved
        the window fell back to its 800x600 minimum, which wrapped the first
        bank onto two rows on a screen with room to spare.
        """
        width, height = self.natural_size()
        available = self.app.primaryScreen().availableGeometry()
        self.resize(min(width, available.width()),
                    min(height, available.height()))

    def save_window_position(self):
        """Save the window's position, size and whether it is maximized."""
        try:
            # The normal geometry even when maximized, so un-maximizing after
            # a restart gives back the size the window had before.
            position = dict(read_settings(self.settings_file).get('window_position') or {})
            normal = normal_geometry(self)
            if normal is not None:
                position.update(zip(('x', 'y', 'width', 'height'), normal))
            position['maximized'] = self.isMaximized()
            self.save_setting('window_position', position)
        except Exception as e:
            print(f"Error saving window position: {e}")
            
    def load_window_position(self):
        """Put the window back as it was left, or centre it if nothing was.

        A window left maximized comes back maximized. It is placed at its
        normal geometry first and only maximized once it is on screen (see
        ``showEvent``), so un-maximizing after a restart gives back the
        size it had rather than whatever Qt defaults to.
        """
        maximized = False
        # Normal first, or the move and resize below would be applied to a
        # window that is still maximized - which is the state it is hidden
        # in while an app runs.
        self.setWindowState(self.windowState() & ~Qt.WindowMaximized)
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r') as f:
                    settings = json.load(f)
                    if 'window_position' in settings:
                        position = settings['window_position']
                        maximized = bool(position.get('maximized'))
                        screen = self.app.primaryScreen().availableGeometry()

                        # Size first, so the reachability test and the move both
                        # work on the geometry the window will actually have.
                        if 'width' in position and 'height' in position:
                            width = min(max(position['width'], 800), screen.width())
                            height = min(max(position['height'], 600), screen.height())
                            self.resize(width, height)

                        if geometry_is_reachable(self.app, position):
                            self.move(QPoint(position['x'], position['y']))
                        else:
                            self.center_window()
                    else:
                        self.center_window()
            else:
                self.center_window()
        except Exception as e:
            print(f"Error loading window position: {e}")
            self.center_window()
        self._maximize_on_show = maximized

    def _sync_pulse(self):
        """Run the pulse if the theme has one and the window is showing."""
        wanted = bool(theme.TOKENS.get('pulse')) and self.isVisible()
        if wanted and not self._pulse_timer.isActive():
            self._pulse_clock.start()
            self._pulse_timer.start()
        elif not wanted and self._pulse_timer.isActive():
            self._pulse_timer.stop()
        if not wanted:
            for line in self._lines:
                if line.phase is not None:
                    line.phase = None
                    line.update()
            for heading in self._headings:
                heading.set_charge(0.0)
            for header in self._headers:
                header.set_glow(0.0)

    def _move_pulse(self):
        """Charge each heading, fire its pulse down the line, and light
        the chevron at the end of it as the pulse arrives.

        Per row, ``into`` seconds through its own cycle: charging for
        ``charge``, the glow gathering faster as it goes; then the pulse
        crosses in ``sweep`` while the glow dies away over ``decay``; then
        rest until ``period``. Each row ``stagger`` after the one above.
        The chevron lights over ``catch`` up to the moment the pulse's
        bright head reaches it, and dies away over ``land``.
        """
        timing = theme.PULSE
        seconds = self._pulse_clock.elapsed() / 1000
        for row, (line, heading, header) in enumerate(zip(
                self._lines, self._headings, self._headers)):
            into = (seconds - row * timing['stagger']) % timing['period']
            fired = into - timing['charge']
            if fired < 0:
                phase, charge = None, (into / timing['charge']) ** 2
            elif fired < timing['sweep']:
                phase = fired / timing['sweep']
                charge = max(0.0, 1.0 - fired / timing['decay'])
            else:
                phase, charge = None, 0.0
            if phase is not None or line.phase is not None:
                line.phase = phase
                line.update()
            heading.set_charge(charge)
            since = fired - self._pulse_arrives(header)
            if fired < 0 or since < -timing['catch']:
                glow = 0.0
            elif since < 0:
                glow = (1.0 + since / timing['catch']) ** 2
            else:
                glow = max(0.0, 1.0 - since / timing['land'])
            header.set_glow(glow)

    @staticmethod
    def _pulse_arrives(header):
        """How far into its sweep, in seconds, a pulse's bright head
        reaches the chevron at the end of this heading's line.

        Worked out from the line as it is laid out, so it stays true at
        any window width: PulseLine draws the head at
        ``(width + LENGTH) * phase - (1 - BRIGHTEST) * LENGTH`` along the
        line, and the chevron is a little past its end.
        """
        line = header.line
        width, length = line.width(), PulseLine.LENGTH
        chevron = header.chevron_box().center().x() - (line.x() + width)
        return theme.PULSE['sweep'] * (
            (width + chevron + (1 - PulseLine.BRIGHTEST) * length)
            / (width + length))

    def hideEvent(self, event):
        super().hideEvent(event)
        self._sync_pulse()

    def showEvent(self, event):
        """Maximize here, not before, if the window was left maximized -
        see ``maximize_when_shown`` in apps/utils.py for why."""
        super().showEvent(event)
        self._sync_pulse()
        if getattr(self, '_maximize_on_show', False):
            self._maximize_on_show = False
            maximize_when_shown(self)

    def create_tile(self, faces):
        """Make one tile. Two or more faces makes it a flip tile.

        Where it lands is ``_relayout``'s business, not this one's: which
        column a tile sits in depends on how wide the window is.
        """
        tile = FlipTile(self, faces)
        self.tiles[tile.key] = tile
        return tile

    def create_app_button(self, name, module_name, icon_name, direction):
        """One app, one tile - the single-face case of ``create_tile``."""
        return self.create_tile([(name, module_name, icon_name, direction)])

    # ------------------------------------------------- following the radio
    def apply_radio_directions(self, animate=True):
        """Turn every tile to a side the selected radio can actually run.

        Called on startup and again whenever Settings closes, so choosing
        the VSG60 turns the grid to transmit and the BB60D turns it to
        receive, in front of you. A HackRF or USRP allows both and leaves
        the tiles as the user left them.
        """
        radio_type = read_settings().get('radio_type', 'hackrf')
        directions = RADIO_DIRECTIONS.get(radio_type, {'tx', 'rx'})
        for tile in self.tiles.values():
            tile.set_directions(directions, radio_type, animate=animate)
        # The rail says which radio the grid is arranged around, so a
        # dimmed row has its reason on screen rather than only in a
        # tooltip nobody hovers.
        self.radio_tag.setText(RADIO_NAMES.get(radio_type, radio_type)
                               .replace('The ', ''))
        return radio_type

    # ---------------------------------------------------------- flip state
    def saved_face(self, key, count):
        """Which side of a tile was showing when the launcher last closed."""
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r') as f:
                    faces = json.load(f).get('tile_faces', {})
                return int(faces.get(key, 0)) % max(count, 1)
        except Exception as e:
            print(f"Error loading tile faces: {e}")
        return 0

    def remember_face(self, key, index):
        try:
            faces = dict(read_settings(self.settings_file).get('tile_faces') or {})
            faces[key] = int(index)
            self.save_setting('tile_faces', faces)
        except Exception as e:
            print(f"Error saving tile face: {e}")

    def launch_application(self, module_name):
        try:
            # The grid already dims what the radio cannot run, so this is
            # the backstop for anything that reaches here another way.
            radio_type = read_settings().get('radio_type', 'hackrf')
            directions = RADIO_DIRECTIONS.get(radio_type, {'tx', 'rx'})
            wanted = face_directions().get(module_name)
            if wanted is not None and wanted not in directions:
                radio = RADIO_NAMES.get(radio_type, 'The selected radio')
                word = DIRECTION_WORDS.get(wanted, wanted)
                QMessageBox.warning(
                    self, f"This Radio Cannot {word.capitalize()}",
                    f"{radio} cannot {word}, so it cannot run this "
                    f"application.\n\nChoose a different radio in Settings "
                    f"(the gear icon), or use a tile that this one can run."
                )
                return

            # Import the module
            module_path = os.path.join('apps', f"{module_name}.py")
            spec = importlib.util.spec_from_file_location(module_name, module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Create configuration dialog (no need to pass parameters)
            config_dialog = module.ConfigDialog()
            
            # Load saved dialog position/size from per-app config file
            app_config_file = os.path.join(self.config_dir, f"{module_name}_config.json")

            # With nothing saved, a dialog opens in the middle of the
            # launcher - where the eye already is, having just clicked a
            # tile. It used to be the launcher's top-left corner plus fifty
            # pixels, which on a wide window put it well off to one side of
            # what was clicked.
            try:
                position = {}
                if os.path.exists(app_config_file):
                    with open(app_config_file, 'r') as f:
                        position = json.load(f).get('dialog_position') or {}
                if position and geometry_is_reachable(self.app, position):
                    # Size first: moving then resizing can push the dialog
                    # somewhere the saved position never meant.
                    if 'width' in position and 'height' in position:
                        config_dialog.resize(position['width'], position['height'])
                    config_dialog.move(QPoint(position['x'], position['y']))
                else:
                    centre_on(config_dialog, self, self.app)
            except Exception as e:
                print(f"Error loading dialog position: {e}")
                centre_on(config_dialog, self, self.app)

            # Track geometry before the dialog is hidden (covers OK, Cancel, and window close)
            tracker = DialogGeometryTracker(config_dialog)

            # Show dialog and wait for user response
            result = config_dialog.exec_()

            # Save dialog position/size to per-app config file
            try:
                pos = tracker.captured or {
                    'x': config_dialog.pos().x(),
                    'y': config_dialog.pos().y(),
                    'width': config_dialog.width(),
                    'height': config_dialog.height(),
                }
                update_app_config(app_config_file, {'dialog_position': pos})
            except Exception as e:
                print(f"Error saving dialog position: {e}")

            if result == QDialog.Accepted:
                config_values = config_dialog.get_values()

                # Validate HackRF is present before launching
                if config_values.get('radio_type') == 'hackrf':
                    try:
                        import SoapySDR
                        devices = SoapySDR.Device.enumerate({'driver': 'hackrf'})
                        if not devices:
                            raise RuntimeError("no hackrf device found")
                    except Exception:
                        QMessageBox.warning(
                            self, "HackRF Not Found",
                            "No HackRF One was detected on USB.\n\n"
                            "Please connect your HackRF One and try again, "
                            "or change the radio type to USRP in Settings."
                        )
                        return

                # Validate the Signal Hound BB60D is present before launching
                elif config_values.get('radio_type') == 'bb60':
                    from apps.bb60_source import (find_devices as find_bb60,
                                                  holders as bb60_holders,
                                                  is_available as bb60_software)
                    if not bb60_software():
                        QMessageBox.warning(
                            self, "Signal Hound BB60D Software Not Found",
                            "The SoapySDR module for the BB60D could not be "
                            "found, so it cannot be used on this machine.\n\n"
                            "It is a system module - normally "
                            "/usr/local/lib/SoapySDR/modules0.8/"
                            "libSignalHoundBB60.so - and is not part of this "
                            "conda environment."
                        )
                        return
                    if not find_bb60():
                        QMessageBox.warning(
                            self, "Signal Hound BB60D Not Found",
                            "No Signal Hound BB60D was detected on USB.\n\n"
                            "Check it is connected."
                        )
                        return
                    # A BB60D another program has open is still listed, and
                    # the open that then fails is inside the flowgraph.
                    busy = bb60_holders()
                    if busy:
                        QMessageBox.warning(
                            self, "Signal Hound BB60D In Use",
                            "The Signal Hound BB60D is already open in another "
                            "program:\n\n" + "\n".join(busy) + "\n\n"
                            "Close it first, then try again."
                        )
                        return

                # Validate the Signal Hound VSG is present before launching
                elif config_values.get('radio_type') == 'vsg':
                    try:
                        from apps.vsg_sink import (find_devices, in_use, is_available,
                                                   library_error)
                        # A missing vendor library is a software install
                        # problem, not an absent device - reporting it as
                        # "not detected on USB" sends people to check cables.
                        if not is_available():
                            QMessageBox.warning(
                                self, "Signal Hound VSG Software Not Found",
                                "The Signal Hound VSG API library could not be "
                                "loaded, so the VSG60 cannot be used on this "
                                f"machine.\n\n{library_error()}"
                            )
                            return
                        if not find_devices():
                            raise RuntimeError("no VSG device found on USB")
                        # The vendor library aborts the process on a second
                        # open, so refuse before we get anywhere near it.
                        if in_use():
                            QMessageBox.warning(
                                self, "Signal Hound VSG In Use",
                                "The Signal Hound VSG60 is already being used "
                                "by another running flowgraph.\n\n"
                                "Close that application first - opening the VSG "
                                "twice crashes both and can leave the device "
                                "needing a USB reset."
                            )
                            return
                    except Exception as e:
                        QMessageBox.warning(
                            self, "Signal Hound VSG Not Found",
                            "No Signal Hound VSG60 was detected on USB.\n\n"
                            f"{e}\n\n"
                            "Please connect your VSG60 and try again, "
                            "or change the radio type in Settings."
                        )
                        return

                # The launcher steps aside while an app runs, and comes
                # back when its window closes.
                self.save_window_position()
                self.hide()

                # Start the GNU Radio application
                tb = module.main(app=self.app, config_values=config_values)

                # Put the flowgraph window back where it was left, the same
                # way the launcher and the config dialog are - and *after*
                # main() has shown it. Each app also calls Qt's own
                # restoreGeometry, but at the top of its __init__ before the
                # widgets exist, and Qt refuses to restore at all once the
                # screen width has changed by more than a quarter. See
                # apps/utils.py: restore_window_geometry.
                if hasattr(tb, 'move'):
                    restore_window_geometry(tb, module_name, self.app)

                if hasattr(tb, 'closeEvent'):
                    original_close_event = tb.closeEvent
                    # What the window's power and frequency start at, so
                    # only what is changed in it gets saved.
                    opened_with = flowgraph_settings(tb)
                    def new_close_event(event):
                        # Read the geometry before the app's own closeEvent,
                        # which stops the flowgraph and accepts the event -
                        # and what its own controls were left at, so the
                        # dialog opens on that next time.
                        save_window_geometry(tb, module_name)
                        save_flowgraph_settings(tb, module_name,
                                                since=opened_with)
                        original_close_event(event)
                        self.load_window_position()
                        self.show()
                    tb.closeEvent = new_close_event

        except Exception as e:
            error_dialog = QMessageBox()
            if 'vsg' in str(e).lower() or 'signal hound' in str(e).lower():
                error_dialog.setIcon(QMessageBox.Warning)
                error_dialog.setWindowTitle("Signal Hound VSG Not Found")
                error_dialog.setText("Signal Hound VSG60 not detected on USB.")
                error_dialog.setInformativeText(
                    "Please connect your VSG60 and try again, "
                    "or change the radio type in Settings."
                )
            elif 'hackrf' in str(e).lower():
                error_dialog.setIcon(QMessageBox.Warning)
                error_dialog.setWindowTitle("HackRF Not Found")
                error_dialog.setText("HackRF One not detected on USB.")
                error_dialog.setInformativeText(
                    "Please connect your HackRF One and try again, "
                    "or change the radio type to USRP in Settings."
                )
            else:
                error_dialog.setIcon(QMessageBox.Critical)
                error_dialog.setWindowTitle("Error")
                error_dialog.setText(f"Error launching {module_name}")
                error_dialog.setInformativeText(str(e))
            error_dialog.exec_()

    def show_settings(self):
        settings_dialog = SettingsDialog(self.settings_file, parent=self)
        apply_dark_theme(settings_dialog)
        tracker = DialogGeometryTracker(settings_dialog)
        settings_dialog.exec_()
        # The radio may have changed, which changes what every tile can do.
        self.apply_radio_directions()
        if tracker.captured:
            self.save_setting('settings_dialog_position', tracker.captured)

    def closeEvent(self, event):
        """Save window position when closing the application"""
        self.save_window_position()
        super().closeEvent(event)

if __name__ == '__main__':
    # Enable high DPI scaling for 4K displays
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)
    
    launcher = RFbenchToolkit(app)
    launcher.show()
    sys.exit(app.exec_())
