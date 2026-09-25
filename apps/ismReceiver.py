#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# SPDX-License-Identifier: GPL-3.0
#
# ISM Receiver - listens on 315/433/868/915 MHz and lists what rtl_433
# decodes: weather sensors, remotes, doorbells, anything among its couple of
# hundred protocols. The radio is the one chosen in Settings, not only an
# RTL-SDR; rtl_433 is handed the samples on its stdin and never touches a
# device.
#
# It is the ISM Transmitter's other side, and the two were built as one
# instrument: the transmitter's frames are graded by rtl_433 offline, and this
# is the same referee on the air.

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
import signal
import subprocess
import sys
import threading
import time

from gnuradio import blocks, filter, gr, qtgui, soapy, uhd  # type: ignore
from gnuradio.fft import window  # type: ignore
from gnuradio.filter import firdes  # type: ignore
from PyQt5 import Qt, QtCore  # type: ignore
try:                 # PyQt5 ships sip inside the package; some builds also
    import sip       # expose it at the top level.
except ImportError:  # pragma: no cover - depends on the PyQt5 build
    from PyQt5 import sip  # type: ignore

from apps import ism_frame
from apps.rdsReceiver import rx_gain_plan
from apps.utils import (apply_dark_theme, apply_flowgraph_theme, radio_label,
                        read_settings, update_app_config, SPECTRUM_Y_AXIS,
                        FREQ_DECIMALS, FREQ_STEP_MHZ, FrequencyChooser)

#: Each radio's own rate, a whole multiple of the rate rtl_433 is fed, as in
#: the RDS receiver: the BB60D has nothing at 2 MS/s and takes 2.5.
SAMPLE_RATES = {'hackrf': 2e6, 'usrp': 2e6, 'bb60': 2.5e6}
#: What rtl_433 is handed. It is its own default rate, the one every decoder
#: is tuned and tested at, and plenty for OOK: the narrowest pulse any
#: profile here sends is 208 us, 52 samples.
DECODE_RATE = 250e3
#: The radio is tuned this far below the frequency asked for and the channel
#: is shifted back digitally, so the radio's own DC spike lands outside what
#: rtl_433 sees. A spike at the centre is a carrier to an envelope detector,
#: and it would sit under every pulse.
LO_OFFSET = 300e3
#: rtl_433's channel, either side of the centre. 250 kHz wide with room for
#: a cheap remote's crystal to be tens of kilohertz out.
CHANNEL_HZ = 110e3

FREQ_MIN_MHZ = 30.0
FREQ_MAX_MHZ = 6000.0

#: How many decodes the window keeps.
MAX_ROWS = 500
#: A sensor sends its frame several times, and some decoders report every
#: copy. The same reading again this soon is the same transmission.
SAME_WITHIN_S = 2.0
#: What the envelope's time axis spans: the longest frame here (a Nexus row
#: is ~60 ms) several times over, and a LaCrosse row's 208 us pulses still a
#: few points wide.
SCOPE_SPAN_S = 0.25
SCOPE_DECIM = 4
#: The trigger sits this far over the quietest the envelope has been lately,
#: so it follows the gain rather than being a number the user has to find.
TRIGGER_OVER_FLOOR = 4.0

RTL_433 = shutil.which('rtl_433')

#: What the table shows in a column of its own, and what it leaves out of the
#: reading because rtl_433 adds it to every row rather than the device
#: sending it.
_OWN_COLUMN = ('model', 'id', 'channel')
_NOT_THE_READING = ('time', 'mod', 'freq', 'freq1', 'freq2', 'rssi', 'snr',
                    'noise', 'mic')
_UNITS = {'temperature_C': ('%.1f °C', 'temperature'),
          'temperature_F': ('%.1f °F', 'temperature'),
          'humidity': ('%g %%', 'humidity'),
          'wind_avg_km_h': ('%.1f km/h', 'wind'),
          'rain_mm': ('%.1f mm', 'rain'),
          'pressure_hPa': ('%.1f hPa', 'pressure')}


def rtl_433_version():
    """rtl_433's own first line, or None when it is not installed."""
    if not RTL_433:
        return None
    try:
        out = subprocess.run([RTL_433, '-V'], capture_output=True, text=True,
                             timeout=5)
        text = (out.stderr or out.stdout).strip().splitlines()
        return text[0] if text else 'rtl_433'
    except Exception:
        return None


def reading_text(row):
    """What a decode says, less what the table shows in other columns."""
    parts = []
    for key, value in row.items():
        if key in _OWN_COLUMN or key in _NOT_THE_READING:
            continue
        if key in _UNITS:
            fmt, name = _UNITS[key]
            try:
                parts.append('%s %s' % (name, fmt % float(value)))
                continue
            except (TypeError, ValueError):
                pass
        if key == 'battery_ok':
            parts.append('battery ' + ('ok' if value else 'LOW'))
        else:
            parts.append('%s %s' % (key, value))
    return ', '.join(parts)


def same_transmission(row):
    """The part of a decode that is the device's, for spotting repeats: two
    copies of one frame differ only in rtl_433's timing and level."""
    return json.dumps({k: v for k, v in row.items()
                       if k not in _NOT_THE_READING}, sort_keys=True)


class Rtl433:
    """rtl_433, reading complex samples from a pipe, and what it decodes.

    It is given ``cf32`` on stdin and prints one JSON object per decode. A
    thread reads those as they come and queues them for the Qt thread; the
    window never waits on it. Nothing else of rtl_433's is used - no device,
    no tuning - so it runs the same with any radio in Settings.
    """

    def __init__(self, rate):
        self.rows = collections.deque(maxlen=MAX_ROWS)
        self.lock = threading.Lock()
        self.proc = None
        self.fd = None
        self.error = None
        if not RTL_433:
            self.error = "rtl_433 is not installed"
            return
        # -M level adds each decode's RSSI and SNR, which is the one thing a
        # table of readings cannot say by itself: whether it was close to
        # missing it.
        self.proc = subprocess.Popen(
            [RTL_433, '-F', 'json', '-M', 'level', '-s', str(int(rate)),
             '-r', 'cf32:-'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, bufsize=0)
        # A duplicate for the flowgraph's file sink, which closes whatever it
        # is handed; the pipe's own descriptor stays this object's to close.
        self.fd = os.dup(self.proc.stdin.fileno())
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self):
        proc = self.proc                  # close() sets self.proc to None
        for raw in proc.stdout:
            line = raw.decode('utf-8', 'replace').strip()
            if not line.startswith('{'):
                if line and not line.startswith(('rtl_433 version',
                                                 'Use "-F log"')):
                    print("rtl_433: " + line, file=sys.stderr)
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            with self.lock:
                self.rows.append((time.time(), row))
        code = proc.wait()
        if self.proc is not None and code != 0:
            self.error = "rtl_433 stopped (exit %s)" % code

    def take(self):
        """Every decode since the last call, oldest first."""
        with self.lock:
            rows = list(self.rows)
            self.rows.clear()
        return rows

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def close(self):
        if self.proc is None:
            return
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=2)
        except Exception:
            self.proc.kill()
        self.proc = None


class ConfigDialog(Qt.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ISM Receiver Configuration")
        self.layout = Qt.QVBoxLayout(self)
        self.config_dir = "config"
        self.config_file = os.path.join(self.config_dir,
                                        "ismReceiver_config.json")

        settings = read_settings()
        self.usrp_ip = settings.get('usrp_ip', '')
        self.radio_type = settings.get('radio_type', 'hackrf')
        if self.radio_type == 'vsg':
            self.create_cannot_receive()
            apply_dark_theme(self)
            return

        self.button_box = Qt.QDialogButtonBox(
            Qt.QDialogButtonBox.Ok | Qt.QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        self.create_radio_label()
        self.create_frequency_control()
        self.create_gain_control()
        self.create_decoder_note()

        self.layout.addWidget(self.button_box)
        self.load_config()
        self.update_ok_state()
        apply_dark_theme(self)

    def create_cannot_receive(self):
        """The whole dialog, when Settings name a radio that cannot receive."""
        self.setWindowTitle("ISM Receiver")
        row = Qt.QHBoxLayout()
        icon = Qt.QLabel()
        icon.setPixmap(self.style().standardIcon(
            Qt.QStyle.SP_MessageBoxWarning).pixmap(48, 48))
        icon.setAlignment(QtCore.Qt.AlignTop)
        row.addWidget(icon)
        message = Qt.QLabel(
            "<b>The Signal Hound VSG60 cannot receive.</b><br><br>"
            "It only transmits, so the ISM Receiver has no radio to listen "
            "with. Choose the HackRF One, an Ettus USRP or the Signal Hound "
            "BB60D in Settings (the gear icon), then open the ISM Receiver "
            "again.")
        message.setWordWrap(True)
        message.setAlignment(QtCore.Qt.AlignTop)
        row.addWidget(message, 1)
        self.layout.addLayout(row)
        close = Qt.QDialogButtonBox(Qt.QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        self.layout.addWidget(close)

    def create_radio_label(self):
        self.layout.addWidget(Qt.QLabel(radio_label(self.radio_type,
                                                    self.usrp_ip)))

    def create_frequency_control(self):
        self.cf_chooser = FrequencyChooser(
            minimum=FREQ_MIN_MHZ, maximum=FREQ_MAX_MHZ, value=433.92,
            channels=ism_frame.BANDS, slider_range=(300.0, 960.0))
        self.layout.addWidget(self.cf_chooser)

    def create_gain_control(self):
        row = Qt.QHBoxLayout()
        self.gain_slider = Qt.QSlider(QtCore.Qt.Horizontal)
        self.gain_slider.setRange(0, 100)
        # Lower than the RDS receiver's 40: the usual source here is the ISM
        # Transmitter on a cable through a pad, not a broadcast off an
        # antenna, and an OOK envelope clipped flat still decodes where one
        # buried in noise does not - but a HackRF can be damaged, and this
        # is the bench the damage would happen on.
        default = 60 if self.radio_type == 'bb60' else 30
        self.gain_slider.setValue(default)
        self.gain_label = Qt.QLabel(f"RF Gain: {default}%")
        self.gain_slider.valueChanged.connect(
            lambda v: self.gain_label.setText(f"RF Gain: {v}%"))
        row.addWidget(self.gain_label)
        row.addWidget(self.gain_slider)
        self.layout.addLayout(row)

    def create_decoder_note(self):
        version = rtl_433_version()
        if version:
            text = ("Decoded by %s, from this radio's samples - rtl_433 "
                    "opens no device of its own." % version)
        else:
            text = ("<b>rtl_433 is not installed</b>, so nothing will be "
                    "decoded - the spectrum and envelope still work. On "
                    "Linux: <code>sudo apt install rtl-433</code>.")
        note = Qt.QLabel(text)
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
            os.makedirs(self.config_dir, exist_ok=True)
            return
        try:
            with open(self.config_file) as f:
                config = json.load(f)
        except Exception as exc:
            print(f"ISM receiver: could not read saved config: {exc}",
                  file=sys.stderr)
            return
        restore = [
            ('frequency_mhz', lambda v: self.cf_chooser.setValue(float(v))),
            ('gain_percent', lambda v: self.gain_slider.setValue(int(v))),
        ]
        for key, apply in restore:
            if key in config:
                try:
                    apply(config[key])
                except Exception as exc:
                    print(f"ISM receiver: ignoring saved {key!r}: {exc}",
                          file=sys.stderr)

    def save_config(self):
        update_app_config(self.config_file, {
            'radio_type': self.radio_type,
            'frequency_mhz': self.cf_chooser.value(),
            'gain_percent': self.gain_slider.value(),
        })

    def accept(self):
        self.save_config()
        super().accept()

    def get_values(self):
        return {
            'radio_type': self.radio_type,
            'ipXmitAddr': self.usrp_ip if self.radio_type == 'usrp' else '',
            'frequency_mhz': self.cf_chooser.value(),
            'gain_percent': self.gain_slider.value(),
        }


class ismReceiver(gr.top_block, Qt.QWidget):
    # What this window's own controls change that its dialog should
    # open on next time - see apps/utils.py: save_flowgraph_settings.
    SAVED_SETTINGS = {'gain_percent': 'gain_percent',
                      'frequency_mhz': 'freq_mhz'}

    COLUMNS = ("Heard", "Device", "ID", "Channel", "Reading", "SNR", "Copies")

    def __init__(self, config_values=None):
        gr.top_block.__init__(self, "ISM Receiver", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("ISM Receiver")
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

        self.settings = Qt.QSettings("GNU Radio", "ismReceiver")
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
        self.freq_mhz = float(values.get('frequency_mhz', 433.92))
        self.gain_percent = float(values.get('gain_percent', 30))
        self.usrp_ip = values.get('ipXmitAddr', '')
        self.samp_rate = SAMPLE_RATES.get(self.radio_type, 2e6)
        self.decoder = Rtl433(DECODE_RATE)
        self.decodes = 0
        self._floors = collections.deque(maxlen=20)
        self._trigger = None

        self._build_controls()
        self._build_flowgraph()
        self._build_table()

        self.status_timer = Qt.QTimer(self)
        self.status_timer.timeout.connect(self.refresh)
        self.status_timer.start(250)

    # ------------------------------------------------------------------ UI
    def _build_controls(self):
        row = Qt.QHBoxLayout()
        row.addWidget(Qt.QLabel("Frequency (MHz):"))
        self.freq_spin = Qt.QDoubleSpinBox()
        self.freq_spin.setDecimals(FREQ_DECIMALS)
        self.freq_spin.setSingleStep(FREQ_STEP_MHZ)
        self.freq_spin.setRange(FREQ_MIN_MHZ, FREQ_MAX_MHZ)
        self.freq_spin.setValue(self.freq_mhz)
        self.freq_spin.setKeyboardTracking(False)
        self.freq_spin.valueChanged.connect(self.set_frequency)
        row.addWidget(self.freq_spin)

        row.addSpacing(20)
        row.addWidget(Qt.QLabel("RF Gain:"))
        self.gain_slider = Qt.QSlider(QtCore.Qt.Horizontal)
        self.gain_slider.setRange(0, 100)
        self.gain_slider.setValue(int(self.gain_percent))
        self.gain_slider.valueChanged.connect(self.set_gain)
        row.addWidget(self.gain_slider)
        self.gain_value = Qt.QLabel(f"{int(self.gain_percent)}%")
        row.addWidget(self.gain_value)

        row.addSpacing(20)
        self.clear_btn = Qt.QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear)
        row.addWidget(self.clear_btn)
        row.addStretch()

        holder = Qt.QWidget()
        holder.setLayout(row)
        self.top_grid_layout.addWidget(holder, 0, 0, 1, 10)

        self.status = Qt.QLabel()
        self.top_grid_layout.addWidget(self.status, 1, 0, 1, 10)

    def _build_table(self):
        box = Qt.QGroupBox("Decoded by rtl_433 - newest first")
        layout = Qt.QVBoxLayout(box)
        self.table = Qt.QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(Qt.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(Qt.QAbstractItemView.SelectRows)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        for col in range(len(self.COLUMNS)):
            header.setSectionResizeMode(col, Qt.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COLUMNS.index("Reading"),
                                    Qt.QHeaderView.Stretch)
        # Room for a dozen rows: the plots would otherwise take every pixel,
        # and this is what the window is for.
        self.table.setMinimumHeight(260)
        layout.addWidget(self.table)
        self.top_grid_layout.addWidget(box, 12, 0, 6, 10)
        for r in range(2, 18):
            self.top_grid_layout.setRowStretch(r, 1)

    # ----------------------------------------------------------- flowgraph
    def _build_flowgraph(self):
        lo_hz = self.freq_mhz * 1e6 - LO_OFFSET
        samp_rate = self.samp_rate
        if self.radio_type == 'usrp':
            self.radio_source = uhd.usrp_source(
                ",".join((f"addr={self.usrp_ip}", '')),
                uhd.stream_args(cpu_format="fc32", args='',
                                channels=list(range(0, 1))),
            )
            self.radio_source.set_samp_rate(samp_rate)
            self.radio_source.set_center_freq(lo_hz, 0)
            self.radio_source.set_antenna("RX2", 0)
        elif self.radio_type == 'bb60':
            # gr-soapy cannot drive this device - see bb60_source.
            from apps.bb60_source import bb60_source
            self.radio_source = bb60_source(center_freq=lo_hz,
                                            sample_rate=samp_rate,
                                            gain_percent=self.gain_percent)
        else:
            self.radio_source = soapy.source('driver=hackrf', 'fc32', 1, '', '',
                                             [''], [''])
            self.radio_source.set_sample_rate(0, samp_rate)
            self.radio_source.set_frequency(0, lo_hz)
            self.radio_source.set_gain_mode(0, False)

        # The channel back to the centre, filtered and down to rtl_433's own
        # rate. The radio's DC spike is LO_OFFSET away by then, outside the
        # filter.
        self.channel = filter.freq_xlating_fir_filter_ccf(
            int(round(samp_rate / DECODE_RATE)),
            firdes.low_pass(1.0, samp_rate, CHANNEL_HZ, 30e3),
            LO_OFFSET, samp_rate)
        self.connect(self.radio_source, self.channel)
        if self.decoder.fd is not None:
            self.to_rtl_433 = blocks.file_descriptor_sink(
                gr.sizeof_gr_complex, self.decoder.fd)
            self.connect(self.channel, self.to_rtl_433)
        else:
            self.to_rtl_433 = blocks.null_sink(gr.sizeof_gr_complex)
            self.connect(self.channel, self.to_rtl_433)

        self._build_envelope()
        self._build_spectrum(lo_hz)

    def _build_envelope(self):
        # The envelope in time is the display that shows an OOK burst at all:
        # a spectrum average cannot see a transmitter that is off most of the
        # time, and a sensor is off for all but a second a minute.
        self.envelope = blocks.complex_to_mag(1)
        self.thin = filter.fir_filter_fff(SCOPE_DECIM,
                                          [1.0 / SCOPE_DECIM] * SCOPE_DECIM)
        scope_rate = DECODE_RATE / SCOPE_DECIM
        # The floor the trigger is set from: a slow average of the envelope,
        # of which only the lowest recent reading counts - a burst lifts the
        # average for a moment and the minimum ignores it.
        self.floor_avg = filter.single_pole_iir_filter_ff(1e-4)
        self.floor_probe = blocks.probe_signal_f()
        self.connect(self.channel, self.envelope, self.thin)
        self.connect(self.thin, self.floor_avg, self.floor_probe)

        self.scope = qtgui.time_sink_f(
            int(SCOPE_SPAN_S * scope_rate), scope_rate,
            'Envelope - held on the last burst', 1, None)
        self.scope.set_update_time(0.05)
        # In normal trigger mode nothing is drawn until the first burst, and
        # until then the plot shows its own defaults: +/-2, which an envelope
        # never goes below zero of, and a time axis of 16 ms that only the
        # first capture corrects - setting the rate again does not.
        self.scope.set_y_axis(0.0, 1.0)
        self.scope.set_y_label('Amplitude', "")
        self.scope.enable_grid(True)
        self.scope.enable_autoscale(True)
        self.scope.enable_control_panel(False)
        self.scope.disable_legend()
        # Normal, so a burst stays on screen through the minute of silence
        # after it. The level is set from the noise floor as it is measured.
        self.scope.set_trigger_mode(qtgui.TRIG_MODE_NORM, qtgui.TRIG_SLOPE_POS,
                                    1.0, 0.02, 0, "")
        widget = sip.wrapinstance(self.scope.qwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(widget, 2, 0, 5, 10)
        self.connect(self.thin, self.scope)

    def _build_spectrum(self, lo_hz):
        self.spectrum = qtgui.freq_sink_c(
            4096, window.WIN_BLACKMAN_hARRIS, lo_hz, self.samp_rate,
            "RF Spectrum - the channel is %.0f kHz right of the radio's centre"
            % (LO_OFFSET / 1e3), 1, None)
        self.spectrum.set_update_time(0.10)
        self.spectrum.set_y_axis(*SPECTRUM_Y_AXIS)
        self.spectrum.set_y_label('Relative Gain', 'dB')
        self.spectrum.enable_grid(True)
        self.spectrum.enable_autoscale(False)
        self.spectrum.set_fft_average(0.2)
        # Max hold: a burst is a fraction of a second, and this keeps where
        # it was on screen after it has gone.
        self.spectrum.enable_max_hold(True)
        self.spectrum.enable_control_panel(False)
        self.spectrum.disable_legend()
        widget = sip.wrapinstance(self.spectrum.qwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(widget, 7, 0, 5, 10)
        self.connect(self.radio_source, self.spectrum)

    # ------------------------------------------------------------ controls
    def apply_gain(self):
        """Set receive gain. Must run after start() - see rdsReceiver."""
        if self.radio_type == 'usrp':
            try:
                rng = self.radio_source.get_gain_range()
                low, high = float(rng.start()), float(rng.stop())
            except Exception:
                low, high = 0.0, 76.0
            self.radio_source.set_gain(
                low + (high - low) * self.gain_percent / 100.0, 0)
            return
        if self.radio_type == 'bb60':
            self.radio_source.set_gain_percent(self.gain_percent)
            return
        for name, value in rx_gain_plan(self.gain_percent, 'hackrf').items():
            self.radio_source.set_gain(0, name, value)
        self._floors.clear()

    def set_gain(self, percent):
        self.gain_percent = float(percent)
        self.gain_value.setText(f"{int(percent)}%")
        self.apply_gain()

    def set_frequency(self, mhz):
        self.freq_mhz = float(mhz)
        lo_hz = self.freq_mhz * 1e6 - LO_OFFSET
        if self.radio_type == 'usrp':
            self.radio_source.set_center_freq(lo_hz, 0)
        else:
            self.radio_source.set_frequency(0, lo_hz)
        self.spectrum.set_frequency_range(lo_hz, self.samp_rate)
        self._floors.clear()

    def clear(self):
        self.table.setRowCount(0)
        self.decodes = 0

    # ------------------------------------------------------------- readout
    def refresh(self):
        self._follow_floor()
        for heard, row in self.decoder.take():
            self._add_row(heard, row)
        if self.decoder.error:
            text = self.decoder.error + " - nothing is being decoded."
        elif not self.decoder.running():
            text = "rtl_433 is not running - nothing is being decoded."
        else:
            text = ("Listening at %.2f MHz.  %d decode%s."
                    % (self.freq_mhz, self.decodes,
                       '' if self.decodes == 1 else 's'))
        self.status.setText(text)

    def _follow_floor(self):
        level = self.floor_probe.level()
        if level <= 0:
            return
        self._floors.append(level)
        trigger = TRIGGER_OVER_FLOOR * min(self._floors)
        # Only a real change: every call restarts the scope's capture.
        if self._trigger is None or abs(trigger / self._trigger - 1) > 0.25:
            self._trigger = trigger
            self.scope.set_trigger_mode(qtgui.TRIG_MODE_NORM,
                                        qtgui.TRIG_SLOPE_POS, trigger,
                                        0.02, 0, "")

    def _add_row(self, heard, row):
        self.decodes += 1
        key = same_transmission(row)
        top = self.table.item(0, 0) if self.table.rowCount() else None
        if top is not None:
            last_key, last_heard, copies = top.data(QtCore.Qt.UserRole)
            if last_key == key and heard - last_heard < SAME_WITHIN_S:
                top.setData(QtCore.Qt.UserRole, (key, heard, copies + 1))
                self.table.item(0, self.COLUMNS.index("Copies")).setText(
                    str(copies + 1))
                return
        snr = row.get('snr')
        cells = (time.strftime('%H:%M:%S', time.localtime(heard)),
                 str(row.get('model', '?')),
                 str(row.get('id', '')),
                 str(row.get('channel', '')),
                 reading_text(row),
                 '%.1f dB' % snr if isinstance(snr, (int, float)) else '',
                 '1')
        self.table.insertRow(0)
        for col, text in enumerate(cells):
            item = Qt.QTableWidgetItem(text)
            self.table.setItem(0, col, item)
        self.table.item(0, 0).setData(QtCore.Qt.UserRole, (key, heard, 1))
        if self.table.rowCount() > MAX_ROWS:
            self.table.setRowCount(MAX_ROWS)

    def closeEvent(self, event):
        self.settings = Qt.QSettings("GNU Radio", "ismReceiver")
        self.settings.setValue("geometry", self.saveGeometry())
        self.status_timer.stop()
        self.stop()
        self.wait()
        self.decoder.close()
        event.accept()


def main(top_block_cls=ismReceiver, options=None, app=None, config_values=None):
    own_app = app is None
    if own_app:
        app = Qt.QApplication(sys.argv)

    tb = top_block_cls(config_values)
    tb.start()
    # Gains go on after the stream exists: the HackRF's preamp is ignored
    # otherwise.
    tb.apply_gain()
    tb.show()

    def sig_handler(sig=None, frame=None):
        tb.stop()
        tb.wait()
        tb.decoder.close()
        Qt.QApplication.quit()

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    timer = Qt.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    if not own_app:
        return tb
    return app.exec_()


if __name__ == '__main__':
    main()
