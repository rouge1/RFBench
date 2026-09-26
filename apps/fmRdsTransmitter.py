#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# SPDX-License-Identifier: GPL-3.0
#
# FM broadcast transmitter with RDS - plays audio from the media folder and
# carries a live-editable station name, RadioText and now-playing tags on the
# 57 kHz subcarrier, exactly as a real FM station does.
#
# Test it with the RDS Receiver app (or a car radio): everything this sends is
# something apps/rds_core.py can read back.

import json
import os
import signal
import sys
import threading
import time
import wave

import numpy as np  # type: ignore
try:
    import sip  # type: ignore
except ImportError:  # pragma: no cover - depends on the PyQt5 build
    from PyQt5 import sip  # type: ignore
from gnuradio import analog, blocks, filter, gr, qtgui, soapy, uhd  # type: ignore
from gnuradio.fft import window  # type: ignore
from gnuradio.filter import firdes  # type: ignore
from PyQt5 import Qt, QtCore  # type: ignore

from apps.audio_file import PcmReader, audio_channels, is_wav, track_tags
from apps.media import AUDIO, choices
from apps.rds_core import PTY_RBDS, clock_text
from apps.rds_encode import RdsEncoder, RdsSubcarrier, system_clock
from apps.utils import (CONFIG_DIR, apply_dark_theme, apply_flowgraph_theme,
                        radio_label,
                        power_percent, read_settings, update_app_config,
                        resolve_power_range, scale_power, SPECTRUM_Y_AXIS,
                        FREQ_DECIMALS, FREQ_STEP_MHZ, FrequencyChooser)

MPX_RATE = 200e3          # everything below 100 kHz fits comfortably
TX_RATE = 2e6             # MPX interpolated by 10
AUDIO_RATE = 48000        # what the media folder holds
MAX_DEVIATION = 75e3

#: Shares of peak deviation. Audio is kept well back from the limit so the
#: pilot and the RDS subcarrier fit underneath it.
AUDIO_LEVEL = 0.55
PILOT_LEVEL = 0.09        # 9% of 75 kHz, the standard pilot injection
RDS_INJECTION = 0.04

#: Anywhere the radios reach, not only the broadcast band: nothing in the
#: multiplex or the modulator depends on the carrier, so FM + RDS goes out
#: the same at 433.92 as at 101.3. 30 MHz is the VSG60's floor, 6 GHz the
#: HackRF's and the VSG60's ceiling.
FREQ_MIN_MHZ = 30.0
FREQ_MAX_MHZ = 6000.0

#: What the dialog's slider sweeps: the FM broadcast band. The box beside
#: it still takes anything above; using the slider brings the frequency
#: back into the band.
FM_BAND_MHZ = (87.5, 108.0)


def call_to_pi(call):
    """RBDS call letters -> PI code, or None if it is not a 4-letter K/W call."""
    call = (call or '').strip().upper()
    if len(call) != 4 or call[0] not in 'KW' or not call.isalpha():
        return None
    base = 4096 if call[0] == 'K' else 21672
    a, b, c = (ord(ch) - 65 for ch in call[1:])
    return base + a * 676 + b * 26 + c


def track_name(path):
    """A readable track name from a media filename."""
    if not path:
        return ''
    return os.path.splitext(os.path.basename(path))[0].replace('-', ' ')


def song(path):
    """``(artist, title)`` for RDS Now Playing.

    The file's own tags where it has them - ID3 in an MP3, INFO in a WAV -
    so a receiver shows "Bon Jovi" and "Livin' On A Prayer" as RT+ artist
    and title rather than the file name "01 Livin' On A Prayer", track
    number and all. The name stands in for a missing title, and no artist
    tag sends the title alone, as before. The encoder turns both into text
    RDS can carry (``rds_encode.rds_text``).
    """
    if not path or path in ('tone', 'silence'):
        return '', ''
    tags = track_tags(path)
    return tags.get('artist', ''), tags.get('title') or track_name(path)


def wav_channels(path):
    """Channel count of a WAV file, or 0 if it is not a readable WAV."""
    try:
        w = wave.open(path)
    except Exception:
        return 0
    try:
        return w.getnchannels()
    finally:
        w.close()


def wav_files(settings):
    """(label, path) for every WAV and MP3 in the media folder, subfolders too.

    The label carries the folder a track sits in; ``track_name`` does not,
    because that one goes out over the air as RDS Now Playing and a
    listener's radio should say the song, not where it is filed.
    """
    return choices(settings.get('media_directory', ''), AUDIO)


class ConfigDialog(Qt.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FM + RDS Transmitter Configuration")
        self.layout = Qt.QVBoxLayout(self)
        self.config_dir = CONFIG_DIR
        self.config_file = os.path.join(self.config_dir,
                                        "fmRdsTransmitter_config.json")
        settings = read_settings()
        self.usrp_ip = settings.get('usrp_ip', '')
        self.radio_type = settings.get('radio_type', 'hackrf')

        self.button_box = Qt.QDialogButtonBox(
            Qt.QDialogButtonBox.Ok | Qt.QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        self.create_radio_label()
        self.create_frequency_control()
        self.create_power_control()
        self.create_audio_control(settings)
        self.create_rds_controls()

        self.layout.addWidget(self.button_box)
        self.load_config()
        apply_dark_theme(self)

    def create_radio_label(self):
        self.layout.addWidget(Qt.QLabel(radio_label(self.radio_type,
                                                    self.usrp_ip)))
        # An Ettus with no address in Settings has nothing to send to.
        ok_button = self.button_box.button(Qt.QDialogButtonBox.Ok)
        ready = self.radio_type != 'usrp' or bool(self.usrp_ip)
        ok_button.setEnabled(ready)
        if ready:
            ok_button.setGraphicsEffect(None)
        else:
            opacity_effect = Qt.QGraphicsOpacityEffect()
            opacity_effect.setOpacity(0.30)
            ok_button.setGraphicsEffect(opacity_effect)

    def create_frequency_control(self):
        self.cf_chooser = FrequencyChooser(
            minimum=FREQ_MIN_MHZ, maximum=FREQ_MAX_MHZ, value=101.3,
            label="Transmit Frequency (MHz):", slider_range=FM_BAND_MHZ)
        self.layout.addWidget(self.cf_chooser)
        warn = Qt.QLabel("Pick an empty frequency and keep power low - this is "
                         "a real transmitter. Only 87.5-108 MHz reaches an "
                         "ordinary FM radio; anywhere else, listen with the "
                         "FM + RDS Receiver.")
        warn.setWordWrap(True)
        self.layout.addWidget(warn)

    def create_power_control(self):
        row = Qt.QHBoxLayout()
        self.pwr_slider = Qt.QSlider(QtCore.Qt.Horizontal)
        self.pwr_slider.setRange(0, 100)
        self.pwr_slider.setValue(0)
        self.pwr_label = Qt.QLabel("Power Level: 0%")
        self.pwr_slider.valueChanged.connect(
            lambda v: self.pwr_label.setText(f"Power Level: {v}%"))
        row.addWidget(self.pwr_label)
        row.addWidget(self.pwr_slider)
        self.layout.addLayout(row)

    def create_audio_control(self, settings):
        self.layout.addWidget(Qt.QLabel("Audio Source:"))
        self.audio_combo = Qt.QComboBox()
        for label, path in wav_files(settings):
            self.audio_combo.addItem(label, path)
        self.audio_combo.addItem("1 kHz Tone", "tone")
        self.audio_combo.addItem("Silence (RDS only)", "silence")
        self.layout.addWidget(self.audio_combo)

    def create_rds_controls(self):
        box = Qt.QGroupBox("RDS")
        form = Qt.QFormLayout()
        box.setLayout(form)

        self.call_edit = Qt.QLineEdit("KTST")
        self.call_edit.setMaxLength(4)
        self.pi_label = Qt.QLabel()
        self.call_edit.textChanged.connect(self._update_pi_label)
        row = Qt.QHBoxLayout()
        row.addWidget(self.call_edit)
        row.addWidget(self.pi_label)
        holder = Qt.QWidget()
        holder.setLayout(row)
        form.addRow("Call sign:", holder)

        self.ps_edit = Qt.QLineEdit("GNURADIO")
        self.ps_edit.setMaxLength(8)
        form.addRow("Station name (8 chars):", self.ps_edit)

        self.rt_edit = Qt.QLineEdit("GNU Radio FM with RDS")
        self.rt_edit.setMaxLength(64)
        form.addRow("RadioText:", self.rt_edit)

        self.pty_combo = Qt.QComboBox()
        for i, name in enumerate(PTY_RBDS):
            self.pty_combo.addItem(f"{i} - {name}", i)
        self.pty_combo.setCurrentIndex(5)
        form.addRow("Program type:", self.pty_combo)

        self.track_check = Qt.QCheckBox("Put the track name in RadioText")
        self.track_check.setChecked(True)
        form.addRow(self.track_check)

        self.layout.addWidget(box)
        self._update_pi_label()

    def _update_pi_label(self):
        pi = call_to_pi(self.call_edit.text())
        self.pi_label.setText(f"PI {pi:#06x}" if pi else "PI - (need 4 letters)")

    def load_config(self):
        if not os.path.exists(self.config_file):
            os.makedirs(self.config_dir, exist_ok=True)
            return
        try:
            with open(self.config_file) as f:
                config = json.load(f)
        except Exception as exc:
            print(f"FM+RDS: could not read saved config: {exc}", file=sys.stderr)
            return
        for key, apply in (
            ('frequency_mhz', lambda v: self.cf_chooser.setValue(float(v))),
            ('power_percent', lambda v: self.pwr_slider.setValue(
                power_percent(v, 0))),
            ('call', lambda v: self.call_edit.setText(str(v))),
            ('ps', lambda v: self.ps_edit.setText(str(v))),
            ('radiotext', lambda v: self.rt_edit.setText(str(v))),
            ('pty', lambda v: self.pty_combo.setCurrentIndex(int(v))),
            ('track_in_rt', lambda v: self.track_check.setChecked(bool(v))),
            ('audio', lambda v: self._select_audio(v)),
        ):
            if key in config:
                try:
                    apply(config[key])
                except Exception as exc:
                    print(f"FM+RDS: ignoring saved {key!r}: {exc}",
                          file=sys.stderr)

    def _select_audio(self, value):
        for i in range(self.audio_combo.count()):
            if self.audio_combo.itemData(i) == value:
                self.audio_combo.setCurrentIndex(i)
                return

    def save_config(self):
        config = {
            'frequency_mhz': self.cf_chooser.value(),
            'power_percent': self.pwr_slider.value(),
            'call': self.call_edit.text(),
            'ps': self.ps_edit.text(),
            'radiotext': self.rt_edit.text(),
            'pty': self.pty_combo.currentIndex(),
            'track_in_rt': self.track_check.isChecked(),
            'audio': self.audio_combo.currentData(),
        }
        update_app_config(self.config_file, config)

    def accept(self):
        self.save_config()
        super().accept()

    def get_values(self):
        return {
            'radio_type': self.radio_type,
            'ipXmitAddr': self.usrp_ip if self.radio_type == 'usrp' else '',
            'frequency_mhz': self.cf_chooser.value(),
            'power_percent': self.pwr_slider.value(),
            'call': self.call_edit.text().strip().upper(),
            'ps': self.ps_edit.text(),
            'radiotext': self.rt_edit.text(),
            'pty': self.pty_combo.currentData(),
            'track_in_rt': self.track_check.isChecked(),
            'audio': self.audio_combo.currentData(),
            'audio_list': [self.audio_combo.itemData(i)
                           for i in range(self.audio_combo.count())
                           if self.audio_combo.itemData(i) not in ('tone', 'silence')],
        }


class rds_source(gr.sync_block):
    """Streams the subcarriers from apps/rds_encode.py.

    Output 0 is the 19 kHz pilot plus the 57 kHz RDS subcarrier. Output 1 is a
    bare 38 kHz carrier for the stereo difference signal, generated from the
    same sample counter so it is exactly twice the pilot.
    """

    def __init__(self, encoder, rate):
        gr.sync_block.__init__(self, name='rds_source', in_sig=None,
                               out_sig=[np.float32, np.float32])
        self.sub = RdsSubcarrier(encoder, rate, rds_injection=RDS_INJECTION,
                                 pilot_level=PILOT_LEVEL)

    def work(self, input_items, output_items):
        n = len(output_items[0])
        subcarriers, carrier38 = self.sub.generate_all(n)
        output_items[0][:] = subcarriers
        output_items[1][:] = carrier38
        return n


def _pcm_floats(raw, width):
    """Little-endian PCM bytes as floats in [-1, 1)."""
    if width == 1:
        return (np.frombuffer(raw, np.uint8).astype(np.float32) - 128) / 128
    if width == 2:
        return np.frombuffer(raw, '<i2').astype(np.float32) / 32768
    if width == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        return v.astype(np.float32) / (1 << 23)
    return np.frombuffer(raw, '<i4').astype(np.float32) / 2 ** 31


class audio_source(gr.sync_block):
    """Left and right audio at 48 kHz, from a WAV or MP3, a test tone or silence.

    The source switches in place, so Next Track never touches the rest of the
    flowgraph. Rebuilding it instead dropped the samples in transit: pilot and
    RDS jumped in time together, 0.4 of a pilot cycle, and a receiver that had
    already measured its RDS bit timing went on decoding junk, or nothing at
    all. A mono file plays the same on both sides, so the stereo difference is
    zero - on the air, exactly what a mono chain would send.

    An MP3 is decoded ahead on a thread (``apps/audio_file.PcmReader``),
    always to two channels, so ``work()`` never waits on ffmpeg: a wait
    there would hold up the pilot and RDS along with the music. ``channels``
    still reports what the file itself carries.
    """

    def __init__(self, choice='silence'):
        gr.sync_block.__init__(self, name='audio_source', in_sig=None,
                               out_sig=[np.float32, np.float32])
        self._lock = threading.Lock()
        self._wav = None
        self._pcm = None
        self._kind = 'silence'
        self._tone_n = 0
        self.channels = 1
        self.set_source(choice)

    def set_source(self, choice):
        """Play a WAV or MP3 path, 'tone' or 'silence' from the next sample on."""
        wav, pcm, kind, channels = None, None, 'silence', 1
        if choice == 'tone':
            kind = 'tone'
        elif choice and choice != 'silence' and os.path.exists(choice):
            try:
                if is_wav(choice):
                    wav = wave.open(choice)
                    kind, channels = 'wav', wav.getnchannels()
                else:
                    # Started, and a moment of it decoded, before the swap -
                    # so the new song does not open on a gap.
                    pcm = PcmReader(choice, channels=2)
                    pcm.wait_ready()
                    kind, channels = 'pcm', audio_channels(choice) or 2
            except (wave.Error, EOFError, OSError, RuntimeError) as exc:
                print(f"FM+RDS: cannot play {choice}: {exc}", file=sys.stderr)
        with self._lock:
            old_wav, self._wav = self._wav, wav
            old_pcm, self._pcm = self._pcm, pcm
            self._kind, self.channels = kind, channels
        if old_wav is not None:
            old_wav.close()
        if old_pcm is not None:
            old_pcm.close()

    def close(self):
        """Let go of the file - and an MP3's ffmpeg - once the flowgraph stops."""
        self.set_source('silence')

    def _wav_frames(self, n):
        """The next ``n`` frames, shape (n, channels), looping the file."""
        width, ch = self._wav.getsampwidth(), self._wav.getnchannels()
        parts, got = [], 0
        while got < n:
            raw = self._wav.readframes(n - got)
            if not raw:
                self._wav.rewind()
                raw = self._wav.readframes(n - got)
                if not raw:
                    break
            parts.append(_pcm_floats(raw, width))
            got += len(raw) // (width * ch)
        data = np.concatenate(parts) if parts else np.zeros(0, np.float32)
        data = data[:len(data) // ch * ch].reshape(-1, ch)
        if len(data) < n:
            data = np.vstack([data, np.zeros((n - len(data), ch), np.float32)])
        return data

    def work(self, input_items, output_items):
        n = len(output_items[0])
        with self._lock:
            if self._kind in ('wav', 'pcm'):
                data = (self._wav_frames(n) if self._kind == 'wav'
                        else self._pcm.read(n))
                left = data[:, 0]
                right = data[:, 1] if data.shape[1] > 1 else left
            elif self._kind == 'tone':
                t = (self._tone_n + np.arange(n)) / AUDIO_RATE
                left = right = (0.5 * np.cos(2 * np.pi * 1000 * t)).astype(np.float32)
                self._tone_n = (self._tone_n + n) % AUDIO_RATE
            else:
                left = right = np.zeros(n, np.float32)
        output_items[0][:] = left
        output_items[1][:] = right
        return n


class fmRdsTransmitter(gr.top_block, Qt.QWidget):
    # What this window's own controls change that its dialog should
    # open on next time - see apps/utils.py: save_flowgraph_settings.
    SAVED_SETTINGS = {'power_percent': 'power_percent',
                      'frequency_mhz': 'freq_mhz'}

    def __init__(self, config_values=None):
        gr.top_block.__init__(self, "FM + RDS Transmitter", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("FM + RDS Transmitter")
        apply_flowgraph_theme(self)
        try:
            self.setWindowIcon(Qt.QIcon.fromTheme('gnuradio-grc'))
        except BaseException as exc:
            print(f"Qt GUI: Could not set Icon: {exc}", file=sys.stderr)

        self.top_scroll_layout = Qt.QVBoxLayout()
        self.setLayout(self.top_scroll_layout)
        self.top_scroll = Qt.QScrollArea()
        self.top_scroll.setFrameStyle(Qt.QFrame.NoFrame)
        self.top_scroll_layout.addWidget(self.top_scroll)
        self.top_scroll.setWidgetResizable(True)
        self.top_widget = Qt.QWidget()
        self.top_scroll.setWidget(self.top_widget)
        self.top_layout = Qt.QVBoxLayout(self.top_widget)
        self.top_grid_layout = Qt.QGridLayout()
        self.top_layout.addLayout(self.top_grid_layout)

        self.settings = Qt.QSettings("GNU Radio", "fmRdsTransmitter")
        try:
            geometry = self.settings.value("geometry")
            if geometry:
                self.restoreGeometry(geometry)
        except BaseException as exc:
            print(f"Qt GUI: Could not restore geometry: {exc}", file=sys.stderr)

        if config_values is None:
            dialog = ConfigDialog()
            if not dialog.exec_():
                sys.exit(0)
            values = dialog.get_values()
        else:
            values = config_values

        self.radio_type = values.get('radio_type', 'hackrf')
        self.freq_mhz = float(values.get('frequency_mhz', 101.3))
        self.power_percent = float(values.get('power_percent', 0))
        self.usrp_ip = values.get('ipXmitAddr', '')
        self.track_in_rt = bool(values.get('track_in_rt', True))
        self.playlist = [p for p in values.get('audio_list', []) if p]
        self.audio_choice = values.get('audio', 'silence')
        self.track_index = (self.playlist.index(self.audio_choice)
                            if self.audio_choice in self.playlist else 0)

        self.call = (values.get('call', '') or '').strip().upper()
        pi = call_to_pi(self.call) or 0x4413
        self.encoder = RdsEncoder(
            pi=pi,
            ps=values.get('ps', 'GNURADIO'),
            radiotext=values.get('radiotext', ''),
            pty=int(values.get('pty', 5) or 0),
            clock=system_clock,
        )
        # Asked of the file once per track, for the air and for the window.
        self.song = song(self.audio_choice)
        if self.track_in_rt and self.audio_choice not in ('tone', 'silence'):
            self.encoder.set_now_playing(*self.song)

        self._build_controls()
        self._build_readout()
        self._build_flowgraph()
        self._build_spectrum()

        self.readout_timer = Qt.QTimer(self)
        self.readout_timer.timeout.connect(self.refresh_readout)
        self.readout_timer.start(500)
        self.refresh_readout()

    # ------------------------------------------------------------------ UI
    def _build_controls(self):
        grid = Qt.QGridLayout()
        holder = Qt.QWidget()
        holder.setLayout(grid)

        grid.addWidget(Qt.QLabel("<b>Frequency (MHz)</b>"), 0, 0)
        self.freq_spin = Qt.QDoubleSpinBox()
        self.freq_spin.setDecimals(FREQ_DECIMALS)
        self.freq_spin.setSingleStep(FREQ_STEP_MHZ)
        self.freq_spin.setRange(FREQ_MIN_MHZ, FREQ_MAX_MHZ)
        self.freq_spin.setValue(self.freq_mhz)
        # Tune on Enter, not on every keystroke: typing 915 would otherwise
        # put the carrier on 91 MHz, in the middle of the broadcast band, on
        # the way.
        self.freq_spin.setKeyboardTracking(False)
        self.freq_spin.valueChanged.connect(self.set_frequency)
        grid.addWidget(self.freq_spin, 0, 1)

        grid.addWidget(Qt.QLabel("<b>Power</b>"), 0, 2)
        self.pwr_slider = Qt.QSlider(QtCore.Qt.Horizontal)
        self.pwr_slider.setRange(0, 100)
        self.pwr_slider.setValue(int(self.power_percent))
        self.pwr_slider.valueChanged.connect(self.set_power)
        grid.addWidget(self.pwr_slider, 0, 3)
        self.pwr_value = Qt.QLabel(f"{int(self.power_percent)}%")
        grid.addWidget(self.pwr_value, 0, 4)

        grid.addWidget(Qt.QLabel("<b>PS</b>"), 1, 0)
        self.ps_edit = Qt.QLineEdit(self.encoder.snapshot()['ps'].strip())
        self.ps_edit.setMaxLength(8)
        grid.addWidget(self.ps_edit, 1, 1)

        grid.addWidget(Qt.QLabel("<b>RadioText</b>"), 1, 2)
        self.rt_edit = Qt.QLineEdit(self.encoder.snapshot()['radiotext'])
        self.rt_edit.setMaxLength(64)
        grid.addWidget(self.rt_edit, 1, 3)

        apply_btn = Qt.QPushButton("Send Text")
        apply_btn.clicked.connect(self.apply_text)
        grid.addWidget(apply_btn, 1, 4)

        self.track_label = Qt.QLabel("-")
        grid.addWidget(Qt.QLabel("<b>Track</b>"), 2, 0)
        grid.addWidget(self.track_label, 2, 1, 1, 3)
        next_btn = Qt.QPushButton("Next Track")
        next_btn.clicked.connect(self.next_track)
        grid.addWidget(next_btn, 2, 4)

        self.top_grid_layout.addWidget(holder, 0, 0, 1, 10)

    def apply_text(self):
        """Push the edited station name and RadioText on to the air."""
        self.encoder.set_ps(self.ps_edit.text())
        self.encoder.set_radiotext(self.rt_edit.text())
        self.refresh_readout()

    def _build_readout(self):
        """What is actually on the air, in the RDS Receiver's own words.

        The edit boxes above hold what was typed, which is not always what is
        being sent: Next Track rewrites RadioText and its RT+ tags, and a paged
        paragraph moves on by itself. So this reads the encoder's state rather
        than the boxes, with the same fields the receiver shows.
        """
        box = Qt.QGroupBox("On Air")
        grid = Qt.QGridLayout()
        box.setLayout(grid)
        big = Qt.QFont()
        big.setPointSize(15)
        big.setBold(True)
        mono = Qt.QFont("Monospace")
        mono.setStyleHint(Qt.QFont.TypeWriter)
        mono.setPointSize(13)

        self.lbl = {}
        fields = (('station', "Station", big),
                  ('ps', "Now showing (PS)", mono),
                  ('nowplaying', "Now Playing", big),
                  ('radiotext', "RadioText", mono),
                  ('clock', "Station Clock", mono))
        for row, (key, caption, font) in enumerate(fields):
            grid.addWidget(Qt.QLabel(f"<b>{caption}</b>"), row, 0)
            value = Qt.QLabel("-")
            value.setFont(font)
            # Typed text, so never let a stray '<' be taken for markup.
            value.setTextFormat(QtCore.Qt.PlainText)
            value.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            value.setWordWrap(True)
            grid.addWidget(value, row, 1)
            self.lbl[key] = value
        grid.setColumnStretch(1, 1)
        self.top_grid_layout.addWidget(box, 1, 0, 1, 10)

    def refresh_readout(self):
        snap = self.encoder.snapshot()
        full = snap['radiotext']
        self.lbl['station'].setText(
            f"{self.call}  (PI {snap['pi_hex']})" if call_to_pi(self.call)
            else f"PI {snap['pi_hex']}")
        self.lbl['ps'].setText(snap['ps'] if snap['ps'].strip() else '-')
        # The song on air, not a slice of whichever RadioText page is up: while
        # a typed message takes its turn, receivers keep the song as well.
        item = snap['now_playing'] or {}
        self.lbl['nowplaying'].setText(
            ' - '.join(x for x in (item.get('artist'), item.get('title')) if x)
            or '-')
        # A carriage return ends a short page; what follows it is not shown.
        self.lbl['radiotext'].setText(full.split('\r')[0].rstrip() or '-')
        # The last clock group sent, so it changes once a minute, not every tick.
        self.lbl['clock'].setText(clock_text(snap['clock']) or '-')

    # ----------------------------------------------------------- flowgraph
    def _build_audio(self):
        """Create the audio chain - once, and in stereo whatever the file.

        FM does not transmit left and right: it sends the sum (L+R)/2 as
        ordinary audio and the difference (L-R)/2 on the 38 kHz subcarrier,
        which is what keeps the signal listenable on a mono receiver. A mono
        file leaves audio_source the same on both sides, so its difference is
        zero and the subcarrier carries nothing - a mono signal on the air.
        """
        self.audio_src = audio_source(self.audio_choice)
        self.stereo = self.audio_src.channels == 2
        # 48 kHz media up to the 200 kHz multiplex rate: 200/48 = 25/6.
        def resampler():
            return filter.rational_resampler_fff(
                interpolation=25, decimation=6, taps=[], fractional_bw=0)

        def audio_shaping():
            # AUDIO_LEVEL rides in the filter gain, so peak deviation stays
            # within budget once the pilot and RDS are added on top.
            return (analog.fm_preemph(MPX_RATE, 75e-6),
                    filter.fir_filter_fff(
                        1, firdes.low_pass(AUDIO_LEVEL, MPX_RATE, 15e3, 2e3)))

        self.resamp_l, self.resamp_r = resampler(), resampler()
        self.lr_add, self.lr_sub = blocks.add_ff(1), blocks.sub_ff(1)
        self.mid_half = blocks.multiply_const_ff(0.5)
        self.side_half = blocks.multiply_const_ff(0.5)
        self.mid_preemph, self.mid_lpf = audio_shaping()
        self.side_preemph, self.side_lpf = audio_shaping()
        self.side_mix = blocks.multiply_ff(1)     # side x 38 kHz carrier
        self.audio_sum = blocks.add_vff(1)        # mid + modulated side

    def _connect_all(self):
        self.connect((self.audio_src, 0), self.resamp_l)
        self.connect((self.audio_src, 1), self.resamp_r)
        self.connect(self.resamp_l, (self.lr_add, 0))
        self.connect(self.resamp_r, (self.lr_add, 1))
        self.connect(self.resamp_l, (self.lr_sub, 0))
        self.connect(self.resamp_r, (self.lr_sub, 1))
        self.connect(self.lr_add, self.mid_half, self.mid_preemph,
                     self.mid_lpf, (self.audio_sum, 0))
        self.connect(self.lr_sub, self.side_half, self.side_preemph,
                     self.side_lpf, (self.side_mix, 0))
        self.connect((self.rds, 1), (self.side_mix, 1))
        self.connect(self.side_mix, (self.audio_sum, 1))
        self.connect(self.audio_sum, (self.mpx_sum, 0))
        self.connect((self.rds, 0), (self.mpx_sum, 1))
        self.connect(self.mpx_sum, self.mpx_resamp, self.modulator,
                     self.radio_sink)

    def _build_flowgraph(self):
        self.rds = rds_source(self.encoder, MPX_RATE)
        self.mpx_sum = blocks.add_vff(1)
        self._build_audio()
        # MPX up to the radio's rate, then frequency modulate. Doing it in this
        # order matters: the modulated signal is far wider than the multiplex.
        self.mpx_resamp = filter.rational_resampler_fff(
            interpolation=int(TX_RATE // MPX_RATE), decimation=1, taps=[],
            fractional_bw=0)
        self.modulator = analog.frequency_modulator_fc(
            2 * np.pi * MAX_DEVIATION / TX_RATE)

        self._power_range = resolve_power_range(self.radio_type)
        level = scale_power(self.power_percent, self._power_range)
        if self.radio_type == 'vsg':
            from apps.vsg_sink import vsg_sink
            self.radio_sink = vsg_sink(center_freq=self.freq_mhz * 1e6,
                                       sample_rate=TX_RATE, level_dbm=level)
        elif self.radio_type == 'usrp':
            self.radio_sink = uhd.usrp_sink(
                ",".join((f"addr={self.usrp_ip}", '')),
                uhd.stream_args(cpu_format="fc32", args='',
                                channels=list(range(0, 1))), "")
            self.radio_sink.set_samp_rate(TX_RATE)
            self.radio_sink.set_time_now(uhd.time_spec(time.time()),
                                         uhd.ALL_MBOARDS)
            self.radio_sink.set_center_freq(self.freq_mhz * 1e6, 0)
            self.radio_sink.set_antenna("TX/RX", 0)
            self._power_range = resolve_power_range(self.radio_type,
                                                    self.radio_sink)
            self.radio_sink.set_gain(
                scale_power(self.power_percent, self._power_range), 0)
        else:
            self.radio_sink = soapy.sink('driver=hackrf', 'fc32', 1, '', '',
                                         [''], [''])
            self.radio_sink.set_sample_rate(0, TX_RATE)
            self.radio_sink.set_frequency(0, self.freq_mhz * 1e6)

        self._connect_all()
        self._update_track_label()

    def _build_spectrum(self):
        self.mpx_sink = qtgui.freq_sink_f(
            2048, window.WIN_BLACKMAN_hARRIS, 0, MPX_RATE,
            'Transmitted baseband (MPX) - pilot 19 kHz, RDS 57 kHz', 1, None)
        self.mpx_sink.set_update_time(0.10)
        self.mpx_sink.set_y_axis(*SPECTRUM_Y_AXIS)
        self.mpx_sink.enable_grid(True)
        self.mpx_sink.enable_autoscale(True)
        self.mpx_sink.set_plot_pos_half(True)
        self.mpx_sink.disable_legend()
        self.top_grid_layout.addWidget(
            sip.wrapinstance(self.mpx_sink.qwidget(), Qt.QWidget), 2, 0, 6, 10)
        self.mpx_sink.set_line_label(0, 'MPX')
        self.connect(self.mpx_sum, self.mpx_sink)

    # ------------------------------------------------------------ controls
    def apply_power(self):
        """Set output power. On HackRF this must run after start()."""
        level = scale_power(self.power_percent, self._power_range)
        if self.radio_type == 'vsg':
            self.radio_sink.set_level(level)
        elif self.radio_type == 'usrp':
            self.radio_sink.set_gain(level, 0)
        else:
            # SoapyHackRF ignores the AMP stage before the stream is running;
            # keep it off here and drive level with the VGA alone.
            self.radio_sink.set_gain(0, 'VGA', level)
            self.radio_sink.set_gain(0, 'AMP', 0)

    def set_power(self, percent):
        self.power_percent = float(percent)
        self.pwr_value.setText(f"{int(percent)}%")
        self.apply_power()

    def set_frequency(self, mhz):
        self.freq_mhz = float(mhz)
        if self.radio_type == 'usrp':
            self.radio_sink.set_center_freq(self.freq_mhz * 1e6, 0)
        elif self.radio_type == 'vsg':
            self.radio_sink.set_frequency(0, self.freq_mhz * 1e6)
        else:
            self.radio_sink.set_frequency(0, self.freq_mhz * 1e6)

    def _update_track_label(self):
        if self.audio_choice in ('tone', 'silence'):
            name = self.audio_choice
        else:
            name = ' - '.join(part for part in self.song if part)
        self.track_label.setText(name or '-')

    def next_track(self):
        """Swap the audio file, and say so over RDS."""
        if not self.playlist:
            return
        self.track_index = (self.track_index + 1) % len(self.playlist)
        path = self.playlist[self.track_index]
        self.audio_choice = path
        # Swap the file inside the running source. Rebuilding the flowgraph
        # instead threw away the samples in transit, and the jump it put into
        # the pilot and RDS left receivers decoding junk or nothing at all.
        self.audio_src.set_source(path)
        self.stereo = self.audio_src.channels == 2
        self.song = song(path)
        self._update_track_label()
        if self.track_in_rt:
            self.encoder.set_now_playing(*self.song)
            # A typed message stays in the box; otherwise it shows the new song.
            snap = self.encoder.snapshot()
            self.rt_edit.setText(snap['message'] or snap['radiotext'])

    def closeEvent(self, event):
        self.readout_timer.stop()
        self.settings = Qt.QSettings("GNU Radio", "fmRdsTransmitter")
        self.settings.setValue("geometry", self.saveGeometry())
        self.stop()
        self.wait()
        self.audio_src.close()
        event.accept()


def main(top_block_cls=fmRdsTransmitter, options=None, app=None,
         config_values=None):
    if app is None:
        app = Qt.QApplication(sys.argv)

    tb = top_block_cls(config_values)
    tb.start()
    tb.apply_power()      # after start(), or the HackRF ignores part of it
    tb.show()

    def sig_handler(sig=None, frame=None):
        tb.stop()
        tb.wait()
        Qt.QApplication.quit()

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    timer = Qt.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    if app.instance():
        return tb
    return app.exec_()


if __name__ == '__main__':
    main()
