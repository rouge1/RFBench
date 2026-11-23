#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DVB-T2 Receiver: tunes an 8 MHz channel, decodes the multiplex and plays a programme of it.

The receiving half of ``dvbt2Xmitter``; the launcher tile flips between the two. The decoder
is skinny-fm's (``apps/dvbt2``, see its ``__init__``): the radio's 10 MS/s is brought to the
channel's elementary rate (64/7 MS/s, 32/35), queued for ``iqt2_live.T2Live``, which learns
the signal from its own L1, follows the P1s and decodes each frame in worker processes, the
LDPC native (``scripts/build_dvbt2_native.py``, once per machine). The flowgraph's thread never
waits on any of it.

The transport stream that comes out is read for its programmes and their names (the SDT, and
now and next from the EIT, by ``dvb_si``), can be recorded into the media folder, and Watch
hands it to ffplay with the chosen programme's picture and sound (``-vst p:N:v -ast p:N:a``),
written from a thread of its own, as a frame's worth of stream arrives at once, four times a
second. See ``knowledge/dvbt2.md``.
"""

if __name__ == '__main__':
    import ctypes
    import sys
    if sys.platform.startswith('linux'):
        try:
            x11 = ctypes.cdll.LoadLibrary('libX11.so')
            x11.XInitThreads()
        except Exception:
            print("Warning: failed to XInitThreads()")

import collections
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime

import numpy as np  # type: ignore
try:
    import sip  # type: ignore
except ImportError:  # pragma: no cover - depends on the PyQt5 build
    from PyQt5 import sip  # type: ignore
from gnuradio import gr, qtgui, soapy, uhd  # type: ignore
from gnuradio.fft import window  # type: ignore
from PyQt5 import Qt, QtCore  # type: ignore

from apps import dvbt2_tx
from apps.atscReceiver import rx_gain_plan, sample_tap
from apps.dvbt2_tx import (DEFAULT_CHANNEL, channel_center_mhz, channel_for_center,
                           channel_items, UHF_CHANNELS)
from apps.theme import TOKENS
from apps.utils import (CONFIG_DIR, apply_dark_theme, apply_flowgraph_theme, radio_label,
                        read_settings, update_app_config, SPECTRUM_Y_AXIS, FrequencyChooser,
                        FREQ_DECIMALS)

#: Every radio runs at 10 MS/s: the BB60D's ladder has it (40/4), SoapyHackRF takes whole
#: megahertz, and an Ettus N210 makes it as 100/10, an even ratio (knowledge/ettus.md).
SAMPLE_RATE = 10e6
#: The elementary rate of an 8 MHz channel, 1/T = 64/7 MS/s; 10 MS/s is 35/32 of it.
ELEMENTARY_RATE = 64e6 / 7
BANDWIDTH = 8e6

TS_PACKET = 188
#: The status line judges the codewords of this many seconds.
RECENT_S = 4.0
#: Stream waiting for the player before the oldest is dropped: about 2 s at 16 Mb/s.
PLAYER_BACKLOG = 4 << 20
#: No transport stream for this long, then some again: taken as a new multiplex, a transmitter
#: restarted on the channel (``dvbt2Receiver._watch_stream``).
RENEW_S = 3.0


class t2_sink(gr.sync_block):
    """The end of the flowgraph: hands every sample to ``take`` (``T2Live.put``, which only
    queues a copy). Until the decoder is started, samples go nowhere. skinny-fm's
    ``dvbt2.t2_sink``, made able to start without its receiver."""

    def __init__(self):
        gr.sync_block.__init__(self, name='t2_sink', in_sig=[np.complex64], out_sig=None)
        self.take = None

    def work(self, input_items, output_items):
        x = input_items[0]
        take = self.take
        if take is not None and len(x):
            take(x)
        return len(x)


def find_player(program, title):
    """ffplay's command line for one programme of the multiplex, or None. A programme with no
    sound plays its picture: ffplay says the audio specifier matches nothing and goes on
    (checked on a pipe joined mid-stream)."""
    if not shutil.which('ffplay'):
        return None
    return ['ffplay', '-hide_banner', '-loglevel', 'error', '-autoexit',
            '-window_title', title, '-vst', f'p:{program}:v', '-ast', f'p:{program}:a',
            '-i', 'pipe:0']


class TsOut:
    """Where the transport stream goes: the analyser, a recording, and a player.

    ``feed`` is called on the decoder's thread with a whole frame's stream at a time (a
    quarter of a second of it, 0.5 MB at 16 Mb/s), more than a pipe holds. So the player is
    written by a thread of its own, blocking, from a queue; when the queue holds more than
    PLAYER_BACKLOG the oldest chunks are dropped (counted), whole packets, so a player that
    stalls never holds up the decoder or grows memory."""

    def __init__(self):
        from apps.dvbt2.dvb_si import DvbTsAnalyzer
        self.analyzer = DvbTsAnalyzer()
        self.lock = threading.Lock()
        self.record = None
        self.record_path = None
        self.player = None
        self.queue = collections.deque()
        self.queued = 0
        self.dropped = 0
        self.wake = threading.Condition(self.lock)
        self.writer = None

    def feed(self, data):
        with self.lock:
            self.analyzer.feed(data)
            if self.record is not None:
                try:
                    self.record.write(data)
                except OSError as exc:
                    print(f"DVB-T2 receiver: recording stopped: {exc}", file=sys.stderr)
                    self._close_record()
            if self.player is not None:
                self.queue.append(bytes(data))
                self.queued += len(data)
                while self.queued > PLAYER_BACKLOG and len(self.queue) > 1:
                    old = self.queue.popleft()
                    self.queued -= len(old)
                    self.dropped += len(old)
                self.wake.notify()

    # -- the player -------------------------------------------------------

    def start_player(self, argv):
        self.stop_player()
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        with self.lock:
            self.player = proc
            self.queue.clear()
            self.queued = 0
            self.dropped = 0
        self.writer = threading.Thread(target=self._write, args=(proc,), name='t2-player',
                                       daemon=True)
        self.writer.start()
        return proc

    def _write(self, proc):
        while True:
            with self.lock:
                while self.player is proc and not self.queue:
                    self.wake.wait(0.5)
                if self.player is not proc:
                    return
                chunk = self.queue.popleft()
                self.queued -= len(chunk)
            try:
                proc.stdin.write(chunk)
                proc.stdin.flush()
            except (OSError, ValueError):
                with self.lock:
                    if self.player is proc:
                        self.player = None
                        self.queue.clear()
                        self.queued = 0
                return

    def stop_player(self):
        with self.lock:
            proc, self.player = self.player, None
            self.queue.clear()
            self.queued = 0
            self.wake.notify_all()
        writer, self.writer = self.writer, None
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
        if writer is not None:
            writer.join(timeout=2)
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()

    def player_alive(self):
        with self.lock:
            return self.player is not None and self.player.poll() is None

    # -- recording --------------------------------------------------------

    def start_record(self, path):
        with self.lock:
            self._close_record()
            self.record = open(path, 'wb')
            self.record_path = path
        return path

    def stop_record(self):
        with self.lock:
            path = self.record_path
            self._close_record()
        return path

    def _close_record(self):
        rec, self.record = self.record, None
        if rec is not None:
            try:
                rec.close()
            except Exception:
                pass

    def recording(self):
        with self.lock:
            return self.record is not None

    # -- readout ----------------------------------------------------------

    def snapshot(self):
        with self.lock:
            return self.analyzer.snapshot()

    def services(self):
        """``[(number, name, kinds, now, next)]`` for every programme in the PAT, in order."""
        from apps.dvbt2.atsc_core import STREAM_TYPES
        with self.lock:
            a = self.analyzer
            out = []
            for n in sorted(a.programs):
                si = a.psip.services.get(n, {})
                kinds = sorted({STREAM_TYPES.get(t, f'type 0x{t:02x}')
                                for t in a.programs[n]['streams'].values()})
                now = (a.psip.present.get(n) or {}).get('name', '')
                nxt = (a.psip.following.get(n) or {}).get('name', '')
                out.append((n, si.get('name') or f"programme {n}", kinds, now, nxt))
            return out

    def reset(self):
        with self.lock:
            self.analyzer.reset()


# --------------------------------------------------------------------------
# Configuration dialog
# --------------------------------------------------------------------------

class ConfigDialog(Qt.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DVB-T2 Receiver Configuration")
        self.layout = Qt.QVBoxLayout(self)
        self.config_dir = CONFIG_DIR
        self.config_file = os.path.join(self.config_dir, "dvbt2Receiver_config.json")

        settings = read_settings()
        self.usrp_ip = settings.get('usrp_ip', '')
        self.radio_type = settings.get('radio_type', 'hackrf')
        if self.radio_type == 'vsg':
            self.create_cannot_receive()
            apply_dark_theme(self)
            return

        self.button_box = Qt.QDialogButtonBox(Qt.QDialogButtonBox.Ok | Qt.QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        self.layout.addWidget(Qt.QLabel(radio_label(self.radio_type, self.usrp_ip)))
        self.cf_chooser = FrequencyChooser(minimum=50.0, maximum=2200.0,
                                           value=channel_center_mhz(DEFAULT_CHANNEL),
                                           channels=channel_items())
        self.layout.addWidget(self.cf_chooser)
        self.create_gain_control()
        self.create_decoder_note()

        self.layout.addWidget(self.button_box)
        self.load_config()
        self.update_ok_state()
        apply_dark_theme(self)

    def create_cannot_receive(self):
        """The whole dialog, when Settings name a radio that cannot receive."""
        self.setWindowTitle("DVB-T2 Receiver")
        row = Qt.QHBoxLayout()
        icon = Qt.QLabel()
        icon.setPixmap(self.style().standardIcon(Qt.QStyle.SP_MessageBoxWarning).pixmap(48, 48))
        icon.setAlignment(QtCore.Qt.AlignTop)
        row.addWidget(icon)
        message = Qt.QLabel(
            "<b>The Signal Hound VSG60 cannot receive.</b><br><br>"
            "It only transmits, so the DVB-T2 Receiver has no radio to listen with. Choose the "
            "HackRF One, an Ettus USRP or the Signal Hound BB60D in Settings (the gear icon), "
            "then open the DVB-T2 Receiver again.")
        message.setWordWrap(True)
        message.setAlignment(QtCore.Qt.AlignTop)
        row.addWidget(message, 1)
        self.layout.addLayout(row)
        close = Qt.QDialogButtonBox(Qt.QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        self.layout.addWidget(close)

    def create_gain_control(self):
        row = Qt.QHBoxLayout()
        self.gain_slider = Qt.QSlider(QtCore.Qt.Horizontal)
        self.gain_slider.setRange(0, 100)
        # The BB60D's 60 %: attenuator open, no RF amplification, as the ATSC receiver found
        # best (knowledge/atsc.md); an OFDM signal peaks about 10 dB over its rms, so a HackRF
        # wants its middle.
        default = 60 if self.radio_type == 'bb60' else 55
        self.gain_slider.setValue(default)
        self.gain_label = Qt.QLabel(f"RF Gain: {default}%")
        self.gain_slider.valueChanged.connect(lambda v: self.gain_label.setText(f"RF Gain: {v}%"))
        row.addWidget(self.gain_label)
        row.addWidget(self.gain_slider)
        self.layout.addLayout(row)

    def create_decoder_note(self):
        """Without the native LDPC the decoder runs far slower than the air: say so here."""
        from apps.dvbt2 import iqt2_native
        if not iqt2_native.available():
            note = Qt.QLabel("The native LDPC decoder is not built on this machine, so "
                             "nothing will decode in time. Run "
                             "<tt>python scripts/build_dvbt2_native.py</tt> once.")
            note.setWordWrap(True)
            self.layout.addWidget(note)

    def update_ok_state(self):
        ok = self.button_box.button(Qt.QDialogButtonBox.Ok)
        enabled = self.radio_type != 'usrp' or bool(self.usrp_ip)
        ok.setEnabled(enabled)
        if enabled:
            ok.setGraphicsEffect(None)
        else:
            dim = Qt.QGraphicsOpacityEffect()
            dim.setOpacity(0.30)
            ok.setGraphicsEffect(dim)

    def load_config(self):
        if not os.path.exists(self.config_file):
            return
        try:
            with open(self.config_file) as f:
                config = json.load(f)
        except Exception as exc:
            print(f"DVB-T2 receiver: could not read saved config: {exc}", file=sys.stderr)
            return
        restore = [
            ('center_mhz', lambda v: self.cf_chooser.setValue(float(v))),
            ('gain_percent', lambda v: self.gain_slider.setValue(int(v))),
        ]
        for key, apply in restore:
            if key in config:
                try:
                    apply(config[key])
                except Exception as exc:
                    print(f"DVB-T2 receiver: ignoring saved {key!r}: {exc}", file=sys.stderr)

    def save_config(self):
        update_app_config(self.config_file, {
            'radio_type': self.radio_type,
            'center_mhz': self.cf_chooser.value(),
            'gain_percent': self.gain_slider.value(),
        })

    def accept(self):
        self.save_config()
        super().accept()

    def get_values(self):
        return {
            'radio_type': self.radio_type,
            'ipXmitAddr': self.usrp_ip if self.radio_type == 'usrp' else '',
            'center_mhz': self.cf_chooser.value(),
            'gain_percent': self.gain_slider.value(),
        }


# --------------------------------------------------------------------------
# The receiver
# --------------------------------------------------------------------------

class dvbt2Receiver(gr.top_block, Qt.QWidget):
    SAVED_SETTINGS = {'gain_percent': 'gain_percent', 'center_mhz': 'center_mhz'}

    def __init__(self, config_values=None):
        gr.top_block.__init__(self, "DVB-T2 Receiver", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("DVB-T2 Receiver")
        apply_flowgraph_theme(self)
        # Taken from the theme the window was just painted in, never at import.
        self.GOOD, self.WARN, self.BAD = TOKENS['good'], TOKENS['warn'], TOKENS['bad']
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

        self.settings = Qt.QSettings("GNU Radio", "dvbt2Receiver")
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
        self.center_mhz = float(values.get('center_mhz', channel_center_mhz(DEFAULT_CHANNEL)))
        self.gain_percent = float(values.get('gain_percent', 60))
        self.usrp_ip = values.get('ipXmitAddr', '')
        self.samp_rate = SAMPLE_RATE

        self.live = None                    # T2Live: start_decoder(), after tb.start()
        self.ts = TsOut()
        self._snaps = collections.deque()   # (time, packets, errors, discontinuities)
        self._seen_overflows = 0
        self._watching = None               # the programme number ffplay was started on
        self._recent = collections.deque()  # (time, codewords, good, dropped samples) for the status
        self._last_packets = None           # the analyser's packet count at the last look
        self._last_ts_at = None             # when it last grew
        self._shut_down = False

        self._build_controls()
        self._build_flowgraph()
        self._build_readout()
        self._build_spectrum()

        self.status_timer = Qt.QTimer(self)
        self.status_timer.timeout.connect(self.refresh)
        self.status_timer.start(500)

    # ------------------------------------------------------------------ UI

    def _build_controls(self):
        row = Qt.QHBoxLayout()
        row.addWidget(Qt.QLabel("Channel:"))
        self.channel_combo = Qt.QComboBox()
        self.channel_combo.addItem("-", None)
        for n in UHF_CHANNELS:
            self.channel_combo.addItem(f"{n} ({channel_center_mhz(n):g})", n)
        self._show_channel(self.center_mhz)
        self.channel_combo.currentIndexChanged.connect(self._channel_picked)
        row.addWidget(self.channel_combo)

        row.addWidget(Qt.QLabel("MHz:"))
        self.freq_spin = Qt.QDoubleSpinBox()
        self.freq_spin.setDecimals(FREQ_DECIMALS)
        self.freq_spin.setSingleStep(0.1)
        self.freq_spin.setRange(50.0, 2200.0)
        self.freq_spin.setValue(self.center_mhz)
        self.freq_spin.valueChanged.connect(self.set_center)
        row.addWidget(self.freq_spin)

        row.addSpacing(15)
        row.addWidget(Qt.QLabel("RF Gain:"))
        self.gain_slider = Qt.QSlider(QtCore.Qt.Horizontal)
        self.gain_slider.setRange(0, 100)
        self.gain_slider.setValue(int(self.gain_percent))
        self.gain_slider.setMinimumWidth(120)
        self.gain_slider.valueChanged.connect(self.set_gain)
        row.addWidget(self.gain_slider)
        self.gain_value = Qt.QLabel(f"{int(self.gain_percent)}%")
        row.addWidget(self.gain_value)

        row.addSpacing(15)
        row.addWidget(Qt.QLabel("Programme:"))
        self.program_combo = Qt.QComboBox()
        self.program_combo.setMinimumWidth(180)
        self.program_combo.setToolTip("Which programme of the multiplex Watch plays.")
        self.program_combo.currentIndexChanged.connect(self._program_picked)
        row.addWidget(self.program_combo)

        row.addStretch(1)
        self.watch_btn = Qt.QPushButton("Watch")
        self.watch_btn.setToolTip("Play the chosen programme's picture and sound in ffplay.")
        self.watch_btn.clicked.connect(self.toggle_watch)
        row.addWidget(self.watch_btn)

        self.record_btn = Qt.QPushButton("Record")
        media_dir = read_settings().get('media_directory', '')
        if os.path.isdir(media_dir):
            self.record_btn.setToolTip("Write the whole multiplex's transport stream into the "
                                       "media folder.")
        else:
            self.record_btn.setEnabled(False)
            self.record_btn.setToolTip("Set a media folder in Settings to record - that is "
                                       "where the recording goes.")
        self.record_btn.clicked.connect(self.toggle_record)
        row.addWidget(self.record_btn)

        self.clear_btn = Qt.QPushButton("Clear Statistics")
        self.clear_btn.clicked.connect(self.clear_stats)
        row.addWidget(self.clear_btn)

        holder = Qt.QWidget()
        holder.setLayout(row)
        self.top_grid_layout.addWidget(holder, 0, 0, 1, 10)

    def _build_readout(self):
        big = Qt.QFont()
        big.setPointSize(15)
        big.setBold(True)
        mono = Qt.QFont("Monospace")
        mono.setStyleHint(Qt.QFont.TypeWriter)
        self.lbl = {}

        def field(grid, key, caption, row, col, font=None, span=1, wrap=False):
            grid.addWidget(Qt.QLabel(f"<b>{caption}</b>"), row, col * 2)
            value = Qt.QLabel("-")
            if font:
                value.setFont(font)
            value.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            value.setWordWrap(wrap)
            grid.addWidget(value, row, col * 2 + 1, 1, span)
            self.lbl[key] = value

        signal = Qt.QGroupBox("Signal")
        sg = Qt.QGridLayout()
        signal.setLayout(sg)
        field(sg, 'lock', "Status", 0, 0, big, span=3, wrap=True)
        field(sg, 'snr', "SNR", 1, 0)
        field(sg, 'level', "Input Level", 1, 1)
        field(sg, 'codewords', "Codewords", 2, 0)
        field(sg, 'clock', "Clock", 2, 1)
        field(sg, 'mode', "Mode (L1)", 3, 0, span=3, wrap=True)
        sg.setColumnStretch(1, 1)
        sg.setColumnStretch(3, 1)
        sg.setHorizontalSpacing(12)
        self.top_grid_layout.addWidget(signal, 1, 0, 1, 4)

        stream = Qt.QGroupBox("Multiplex")
        tg = Qt.QGridLayout()
        stream.setLayout(tg)
        field(tg, 'packets', "Packet Rate", 0, 0)
        field(tg, 'errored', "Errored / Discont.", 0, 1)
        field(tg, 'dropped', "Frames Dropped", 1, 0)
        field(tg, 'network', "Network", 1, 1)
        field(tg, 'programs', "Programmes", 2, 0, mono, span=3, wrap=True)
        tg.setColumnStretch(1, 1)
        tg.setColumnStretch(3, 1)
        tg.setHorizontalSpacing(12)
        self.top_grid_layout.addWidget(stream, 1, 4, 1, 6)

        self.action_note = Qt.QLabel("")
        self.action_note.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.action_note.setWordWrap(True)
        self.top_grid_layout.addWidget(self.action_note, 9, 0, 1, 10)

        for row in (0, 1, 9):
            self.top_grid_layout.setRowStretch(row, 0)
        self.top_grid_layout.setRowStretch(2, 1)

    def _build_spectrum(self):
        self.spectrum = qtgui.freq_sink_c(
            2048, window.WIN_BLACKMAN_hARRIS, self.center_mhz * 1e6, self.samp_rate,
            'Received Channel - DVB-T2 fills 7.77 MHz flat', 1, None)
        self.spectrum.set_update_time(0.10)
        self.spectrum.set_y_axis(*SPECTRUM_Y_AXIS)
        self.spectrum.set_y_label('Relative Gain', 'dB')
        self.spectrum.set_trigger_mode(qtgui.TRIG_MODE_FREE, 0.0, 0, "")
        self.spectrum.enable_autoscale(False)
        self.spectrum.enable_grid(True)
        self.spectrum.set_fft_average(0.2)
        self.spectrum.enable_axis_labels(True)
        self.spectrum.enable_control_panel(False)
        self.spectrum.disable_legend()
        widget = sip.wrapinstance(self.spectrum.qwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(widget, 2, 0, 7, 10)
        self.connect(self.radio_source, self.spectrum)

    # ----------------------------------------------------------- flowgraph

    def _build_flowgraph(self):
        self.radio_source = self._make_radio()
        self.level_tap = sample_tap(1 << 16)
        self.resampler = dvbt2_tx.rational_resampler(self.samp_rate, ELEMENTARY_RATE,
                                                     dvbt2_tx.PASS_HZ[BANDWIDTH])
        self.sink = t2_sink()
        self.connect(self.radio_source, self.resampler, self.sink)
        self.connect(self.radio_source, self.level_tap)

    def _make_radio(self):
        """The radio's source at SAMPLE_RATE (a test puts a recording in its place)."""
        centre = self.center_mhz * 1e6
        if self.radio_type == 'usrp':
            self.radio_source = uhd.usrp_source(
                ",".join((f"addr={self.usrp_ip}", '')),
                uhd.stream_args(cpu_format="fc32", args='', channels=list(range(0, 1))))
            self.radio_source.set_samp_rate(self.samp_rate)
            # An N210 gives the nearest rate it can make without an error: read it back.
            got = self.radio_source.get_samp_rate()
            if abs(got - self.samp_rate) > 1:
                print(f"DVB-T2 receiver: the USRP runs at {got:g}, not {self.samp_rate:g}",
                      file=sys.stderr)
            self.radio_source.set_center_freq(centre, 0)
            self.radio_source.set_antenna("RX2", 0)
        elif self.radio_type == 'bb60':
            from apps.bb60_source import bb60_source
            self.radio_source = bb60_source(center_freq=centre, sample_rate=self.samp_rate,
                                            gain_percent=self.gain_percent)
        else:
            self.radio_source = soapy.source('driver=hackrf', 'fc32', 1, '', '', [''], [''])
            self.radio_source.set_sample_rate(0, self.samp_rate)
            self.radio_source.set_frequency(0, centre)
            self.radio_source.set_gain_mode(0, False)
        return self.radio_source

    def start_decoder(self):
        """Start the decoder's thread and its worker processes (spawned: a second or two),
        and only then hand it samples. Called by ``main()`` once the flowgraph runs."""
        from apps.dvbt2 import iqt2_live
        if self.live is None:
            self.live = iqt2_live.T2Live(ELEMENTARY_RATE, on_ts=self.ts.feed)
            self.sink.take = self.live.put

    def stop_decoder(self):
        self.sink.take = None
        live, self.live = self.live, None
        if live is not None:
            live.close()

    # ------------------------------------------------------------ controls

    def apply_gain(self):
        """Set receive gain. Must run after start() on a HackRF: SoapyHackRF ignores the AMP
        stage before it."""
        if self.radio_type == 'usrp':
            try:
                rng = self.radio_source.get_gain_range()
                low, high = float(rng.start()), float(rng.stop())
            except Exception:
                low, high = 0.0, 31.5
            self.radio_source.set_gain(low + (high - low) * self.gain_percent / 100.0, 0)
            return
        if self.radio_type == 'bb60':
            self.radio_source.set_gain_percent(self.gain_percent)
            return
        for name, value in rx_gain_plan(self.gain_percent, 'hackrf').items():
            self.radio_source.set_gain(0, name, value)

    def set_gain(self, percent):
        self.gain_percent = float(percent)
        self.gain_value.setText(f"{int(percent)}%")
        self.apply_gain()

    def _show_channel(self, mhz):
        self.channel_combo.blockSignals(True)
        index = self.channel_combo.findData(channel_for_center(mhz))
        self.channel_combo.setCurrentIndex(max(index, 0))
        self.channel_combo.blockSignals(False)

    def _channel_picked(self, _):
        n = self.channel_combo.currentData()
        if n is not None:
            self.set_center(channel_center_mhz(n))

    def set_center(self, mhz):
        mhz = float(mhz)
        if abs(mhz - self.center_mhz) < 1e-6:
            return
        self.center_mhz = mhz
        centre = mhz * 1e6
        if self.radio_type == 'usrp':
            self.radio_source.set_center_freq(centre, 0)
        elif self.radio_type == 'bb60':
            self.radio_source.set_center_freq(centre)
        else:
            self.radio_source.set_frequency(0, centre)
        self.spectrum.set_frequency_range(centre, self.samp_rate)
        self.freq_spin.blockSignals(True)
        self.freq_spin.setValue(mhz)
        self.freq_spin.blockSignals(False)
        self._show_channel(mhz)
        # A new channel: learn its signal again, and nothing of the old one goes on.
        if self.live is not None:
            self.live.restart()
        was_watching = self._watching is not None
        self.ts.stop_player()
        self._watching = None
        self.clear_stats()
        self.program_combo.clear()
        if was_watching:
            self.watch_btn.setText("Watch")
            self.action_note.setText("Retuned - press Watch again once the programmes are back.")

    def clear_stats(self):
        self.ts.reset()
        self._recent.clear()
        self._snaps.clear()
        self._last_packets = None
        self._last_ts_at = None

    def _watch_stream(self, now, packets):
        """The transport stream gone for ``RENEW_S`` or more and back: the multiplex may not
        be the one before (a transmitter restarted on the channel, its programmes, stream
        numbers and timestamps new). ffplay keeps the old timeline and limped at about 7
        pictures a second on the new one (2026-11-22, offline, 3 programmes then 9), and the
        analyser kept programmes that had gone. So both start afresh, as on a retune, and the
        programme being watched gets a new ffplay. A shorter fade keeps everything. Returns
        whether it started afresh."""
        if packets == self._last_packets:
            return False
        renew = self._last_ts_at is not None and now - self._last_ts_at >= RENEW_S
        if renew:
            self.ts.reset()
            self._snaps.clear()
            if self._watching is not None:
                self._start_player(self._watching)
            packets = self.ts.snapshot()['packets']
        self._last_packets, self._last_ts_at = packets, now
        return renew

    def _program_picked(self, _):
        """A different programme while watching: restart only the player."""
        n = self.program_combo.currentData()
        if self._watching is not None and n is not None and n != self._watching:
            self._start_player(n)

    def _start_player(self, n):
        title = f"DVB-T2 - {self.program_combo.currentText()}"
        argv = find_player(n, title)
        if argv is None:
            Qt.QMessageBox.warning(self, "No Media Player",
                                   "Watching needs ffplay on PATH, and it is not installed.\n\n"
                                   "Use Record instead and play the file afterwards.")
            return False
        try:
            self.ts.start_player(argv)
        except Exception as exc:
            Qt.QMessageBox.warning(self, "Could Not Start Player", str(exc))
            return False
        self._watching = n
        self.watch_btn.setText("Stop Watching")
        self.action_note.setText("ffplay started - a picture comes at the next keyframe, a "
                                 "second or two.")
        return True

    def toggle_watch(self):
        if self._watching is not None:
            self.ts.stop_player()
            self._watching = None
            self.watch_btn.setText("Watch")
            self.action_note.setText("")
            return
        n = self.program_combo.currentData()
        if n is None:
            self.action_note.setText("No programmes yet - wait for the multiplex to lock.")
            return
        self._start_player(n)

    def toggle_record(self):
        if self.ts.recording():
            path = self.ts.stop_record()
            self.record_btn.setText("Record")
            self.action_note.setText(f"Saved {path}")
            return
        directory = read_settings().get('media_directory', '')
        if not os.path.isdir(directory):
            Qt.QMessageBox.warning(self, "No Media Directory",
                                   "Set a media directory in Settings first - that is where "
                                   "the recording goes.")
            return
        name = f"dvbt2-{self.center_mhz:g}MHz-{datetime.now().strftime('%Y%m%d-%H%M%S')}.ts"
        try:
            path = self.ts.start_record(os.path.join(directory, name))
        except Exception as exc:
            Qt.QMessageBox.warning(self, "Could Not Record", str(exc))
            return
        self.record_btn.setText("Stop Recording")
        self.action_note.setText(f"Recording to {path}")

    # ------------------------------------------------------------- readout

    def refresh(self):
        samples = self.level_tap.latest()
        if samples.size:
            rms = float(np.sqrt(np.mean(np.abs(samples) ** 2)))
            self.lbl['level'].setText(f"{20 * np.log10(rms):.1f} dBFS" if rms > 0 else "-")

        st = self.live.state() if self.live is not None else {'phase': 'idle'}
        snr = st.get('snr_db')
        self.lbl['snr'].setText(f"{snr:.1f} dB" if snr is not None else "-")
        cw, ok = st.get('codewords', 0), st.get('codewords_ok', 0)
        self.lbl['codewords'].setText(f"{ok} of {cw}  ({100.0 * ok / cw:.1f}%)" if cw else "-")
        # The status goes by the last few seconds: the totals keep a fade from minutes ago.
        now = time.time()
        drops = st.get('dropped_samples', 0)
        self._recent.append((now, cw, ok, drops))
        while len(self._recent) > 2 and now - self._recent[0][0] > RECENT_S:
            self._recent.popleft()
        t0, cw0, ok0, drops0 = self._recent[0]
        if cw < cw0 or drops < drops0:                  # a new lock starts the counts again
            self._recent.clear()
            self._recent.append((now, cw, ok, drops))
            cw0, ok0, drops0 = cw, ok, drops
        ppm = st.get('clock_ppm')
        self.lbl['clock'].setText(f"{ppm:+.1f} ppm" if ppm is not None else "-")
        plps = st.get('plps') or []
        if plps and st.get('phase') == 'receiving':
            p = plps[0]
            ext = ' ext' if st.get('extended') else ''
            self.lbl['mode'].setText(
                f"{p.get('modulation')} {p.get('code_rate')}, {(st.get('fft') or 0) // 1024}k"
                f"{ext}, guard {st.get('guard')}, {st.get('pilot_pattern')}, "
                f"{p.get('blocks')} blocks a frame, {len(plps)} PLP")
        else:
            self.lbl['mode'].setText("-")
        self.lbl['dropped'].setText(f"{st.get('dropped_frames', 0)}  "
                                    f"({st.get('queued_s', 0):.1f} s queued)")

        # Over the last RECENT_S, as for the codewords: the stream comes a frame (a quarter of
        # a second) at a time, so between two half-second looks it read 7 or 21 Mb/s by turns.
        snap = self.ts.snapshot()
        if self._watch_stream(now, snap['packets']):
            snap = self.ts.snapshot()
        self._snaps.append((now, snap['packets'], snap['errors'], snap['discontinuities']))
        while len(self._snaps) > 2 and now - self._snaps[0][0] > RECENT_S:
            self._snaps.popleft()
        if len(self._snaps) > 1:
            t0, p0, e0, d0 = self._snaps[0]
            rate = (snap['packets'] - p0) / max(now - t0, 1e-3)
            self.lbl['packets'].setText(f"{rate:,.0f}/s  ({rate * TS_PACKET * 8 / 1e6:.2f} Mbps)"
                                        if rate else "-")
            self.lbl['errored'].setText(f"{snap['errors'] - e0} / {snap['discontinuities'] - d0}")

        services = self.ts.services()
        self._update_programs(services)
        lines = []
        for n, name, kinds, on_now, on_next in services:
            line = f"{n}  {name}  [{', '.join(kinds) or 'no PMT yet'}]"
            if on_now:
                line += f"\n     now: {on_now}" + (f"   next: {on_next}" if on_next else "")
            lines.append(line)
        self.lbl['programs'].setText("\n".join(lines) if lines else "no PAT yet")
        with self.ts.lock:
            network = self.ts.analyzer.psip.network
        self.lbl['network'].setText(network or "-")

        text, colour = self._status(st, cw - cw0 if cw > cw0 else cw, ok - ok0 if cw > cw0 else ok,
                                    drops - drops0)
        self.lbl['lock'].setText(text)
        self.lbl['lock'].setStyleSheet(f"color: {colour};")

        if self._watching is not None:
            if not self.ts.player_alive():
                self.ts.stop_player()
                self._watching = None
                self.watch_btn.setText("Watch")
                self.action_note.setText("")
            elif self.ts.dropped:
                self.action_note.setText(f"ffplay running - {self.ts.dropped // TS_PACKET} "
                                         "packets dropped because it could not keep up")

    def _update_programs(self, services):
        """Keep the programme list in step with the PAT and the SDT, keeping the choice."""
        want = [(n, f"{n}  {name}") for n, name, *_ in services]
        have = [(self.program_combo.itemData(i), self.program_combo.itemText(i))
                for i in range(self.program_combo.count())]
        if want == have:
            return
        current = self.program_combo.currentData()
        self.program_combo.blockSignals(True)
        self.program_combo.clear()
        for n, text in want:
            self.program_combo.addItem(text, n)
        index = self.program_combo.findData(current)
        self.program_combo.setCurrentIndex(max(index, 0))
        self.program_combo.blockSignals(False)

    def _status(self, st, cw, ok, drops=0):
        """What is happening, and a colour for how bad it is. A missing native LDPC looks just
        like a weak signal from the numbers, so it is named first. ``cw``, ``ok`` and ``drops``
        (samples the decoder had no room for) are over the last RECENT_S, not since the lock:
        one overflow a minute ago must not hold the line once the queue has drained, and a
        collapse must still show while samples are being dropped."""
        from apps.dvbt2 import iqt2_native
        if self._adc_overflows():
            return ("Input overloaded - turn the RF gain down", self.BAD)
        if not iqt2_native.available():
            return ("Native LDPC not built - run scripts/build_dvbt2_native.py", self.BAD)
        phase = st.get('phase')
        if st.get('error') and phase != 'receiving':
            return (f"Decoder: {st['error']}", self.BAD)
        if phase in ('idle', 'starting'):
            return ("Starting the decoder", self.WARN)
        if phase == 'no signal':
            return ("No DVB-T2 signal on this channel", self.BAD)
        if phase == 'learning':
            return ("Looking for a DVB-T2 signal", self.WARN)
        if not cw:
            return ("Locked to L1 - waiting for decoded frames", self.WARN)
        bad = 100.0 * (cw - ok) / cw
        if bad > 5:
            behind = " - the decoder is falling behind" if drops > 0 else ""
            return (f"Breaking up - {bad:.0f}% of codewords lost{behind}", self.BAD)
        if drops > 0:
            return ("Receiving - the decoder is falling behind and dropping samples", self.WARN)
        if bad > 0.1:
            return (f"Marginal - {bad:.1f}% of codewords lost", self.WARN)
        return ("Locked - clean multiplex", self.GOOD)

    def _adc_overflows(self):
        counter = getattr(self.radio_source, 'adc_overflows', None)
        if counter is None:
            return 0
        total = counter()
        fresh = total - self._seen_overflows
        self._seen_overflows = total
        return fresh

    def closeEvent(self, event):
        self.settings = Qt.QSettings("GNU Radio", "dvbt2Receiver")
        self.settings.setValue("geometry", self.saveGeometry())
        self.shutdown()
        event.accept()

    def shutdown(self):
        """Everything closing does but saving: radio, player, recording, decoder, in that
        order. Run on Qt's thread only - never from a signal handler, which may land while
        refresh() holds the stream's lock - and safe to run twice."""
        if self._shut_down:
            return
        self._shut_down = True
        self.status_timer.stop()
        self.stop()
        self.wait()
        self.ts.stop_player()
        self.ts.stop_record()
        self.stop_decoder()


def main(top_block_cls=dvbt2Receiver, options=None, app=None, config_values=None):
    if app is None:
        app = Qt.QApplication(sys.argv)

    tb = top_block_cls(config_values)
    tb.start()
    # Gains go on after the stream exists: the HackRF's preamp is ignored otherwise.
    tb.apply_gain()
    tb.start_decoder()
    tb.show()

    # A signal can land while refresh() holds the stream's lock, which is not reentrant: the
    # handler only asks Qt to shut down, from its own loop, once that refresh is done.
    def sig_handler(sig=None, frame=None):
        def _stop():
            tb.shutdown()
            Qt.QApplication.quit()
        Qt.QTimer.singleShot(0, _stop)

    import signal as _signal
    _signal.signal(_signal.SIGINT, sig_handler)
    _signal.signal(_signal.SIGTERM, sig_handler)

    timer = Qt.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    if app.instance():
        return tb
    return app.exec_()


if __name__ == '__main__':
    main()
