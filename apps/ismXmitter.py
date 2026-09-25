#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# SPDX-License-Identifier: GPL-3.0
#
# ISM Transmitter - a 315/433/868/915 MHz sensor or remote, synthesised from
# its timings and sent as a real burst. The frames come from apps/ism_frame.py,
# which rtl_433 grades offline; this puts them on a radio.
#
# It is not a modulation demonstrator like askGenerator - those send random
# symbols continuously, with a carrier pedestal that never lets the envelope
# reach zero. This sends a specific payload, in bursts, with the deepest null
# the radio can manage, and the only question it answers is whether a receiver
# believes it.

if __name__ == '__main__':
    import ctypes
    import sys
    if sys.platform.startswith('linux'):
        try:
            x11 = ctypes.cdll.LoadLibrary('libX11.so')
            x11.XInitThreads()
        except Exception:
            print("Warning: failed to XInitThreads()")

import json
import os
import signal
import sys
import time

from gnuradio import blocks, filter, gr, qtgui, soapy, uhd  # type: ignore
from gnuradio.fft import window  # type: ignore
from gnuradio.qtgui import Range, RangeWidget  # type: ignore
from PyQt5 import Qt, QtCore  # type: ignore
try:                 # PyQt5 ships sip inside the package; some builds also
    import sip       # expose it at the top level.
except ImportError:  # pragma: no cover - depends on the PyQt5 build
    from PyQt5 import sip  # type: ignore

from apps import ism_frame
from apps.utils import (apply_dark_theme, apply_flowgraph_theme, radio_label,
                        read_settings, update_app_config, power_percent,
                        resolve_power_range, scale_power, SPECTRUM_Y_AXIS,
                        FrequencyChooser, TrimmedSpinBox, frequency_range)

#: The radio's rate, per radio. A HackRF gets 8 MS/s because Great Scott
#: Gadgets say not to run one below that - the MAX5864 is unspecified there
#: and the MAX2837's narrowest filter gives only 4 dB of rejection at +/-1 MHz
#: at 2 MS/s - and because 8 MS/s is exactly 8 samples per microsecond, so
#: every timing lands on a sample. The other two get 2 MS/s, which is still a
#: whole 2 samples per microsecond and costs a quarter of the memory: the
#: whole burst is rendered up front and held in a vector source.
SAMPLE_RATES = {'hackrf': 8e6, 'usrp': 2e6, 'vsg': 2e6}
DEFAULT_RATE = 8e6
#: How far below the target the radio is tuned, with the signal put back as a
#: baseband tone. Every radio here is direct-conversion and leaks its local
#: oscillator at I=Q=0, and an OOK receiver then sees carrier against carrier
#: rather than carrier against noise - every envelope slicer stops working.
#: rtl_433 captures 250 kHz wide, so 400 kHz puts the leak cleanly outside it.
#: hackrf_ook's 27 kHz and URH's 3.9 kHz are sized to dodge the *receiver's*
#: DC spike and are an order of magnitude too small for this.
#: [ism](../devnotes/ism.md#the-carrier-never-turns-off)
DEFAULT_OFFSET_KHZ = 400.0
OFFSET_MIN_KHZ = 50.0
OFFSET_MAX_KHZ = 900.0
#: Full scale would be the obvious amplitude and is the wrong one: it leaves
#: nothing for the ramped edges to overshoot into and asks the radio's own
#: filters for the last decibel. rtl_433 wants a peak at 25-50 % anyway.
AMPLITUDE = 0.4
FREQ_MIN_MHZ = 30.0
FREQ_MAX_MHZ = 6000.0
INTERVAL_MIN_S = 0.5
INTERVAL_MAX_S = 300.0
#: Refuse rather than swap to death. A Nexus burst at twelve repeats is about
#: a second long, 60 MB of complex64 at 8 MS/s; anything far past that is a
#: repeat count nobody meant to ask for.
MAX_BURST_MB = 384
#: How many points the envelope display gets. A whole burst at the
#: radio's rate is millions, and no time sink will draw those.
DISPLAY_POINTS = 32768

DEFAULT_PROFILE = 'nexus_th'


def build_frame(profile_key, fields):
    """One profile's frame, from the dialog's own field values."""
    builder = ism_frame.PROFILES[profile_key]
    return builder(**{k: v for k, v in fields.items()})


def default_fields(profile_key):
    info = ism_frame.PROFILE_INFO[profile_key]
    return {name: default for name, _l, _k, _lo, _hi, default in info['fields']}


class ConfigDialog(Qt.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ISM Transmitter Configuration")
        self.layout = Qt.QVBoxLayout(self)
        self.config_dir = "config"
        self.config_file = os.path.join(self.config_dir,
                                        "ismXmitter_config.json")

        settings = read_settings()
        self.usrp_ip = settings.get('usrp_ip', '')
        self.radio_type = settings.get('radio_type', 'hackrf')
        #: profile key -> its field values, so turning away from a profile and
        #: back comes back to what was typed rather than to the defaults.
        self._fields = {key: default_fields(key)
                        for key in ism_frame.PROFILE_INFO}
        self._spins = {}

        self.button_box = Qt.QDialogButtonBox(
            Qt.QDialogButtonBox.Ok | Qt.QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        self.create_radio_label()
        self.create_profile_selector()
        self.create_field_box()
        self.create_frequency_control()
        self.create_power_control()
        self.create_timing_controls()
        self.layout.addWidget(self.button_box)

        self.apply_profile(self.profile_combo.currentData())
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

    def create_profile_selector(self):
        row = Qt.QHBoxLayout()
        row.addWidget(Qt.QLabel("Device:"))
        self.profile_combo = Qt.QComboBox()
        for key, info in ism_frame.PROFILE_INFO.items():
            self.profile_combo.addItem(info['label'], key)
        self.profile_combo.setCurrentIndex(
            max(self.profile_combo.findData(DEFAULT_PROFILE), 0))
        self.profile_combo.activated.connect(lambda _i: self._profile_picked())
        row.addWidget(self.profile_combo, 1)
        self.layout.addLayout(row)

    def create_field_box(self):
        """The chosen device's own fields, rebuilt when the device changes.

        The fields live in ``ism_frame.PROFILE_INFO`` rather than here, so a
        fifth protocol is one edit in the module that knows about protocols
        and none at all in this one.
        """
        self.field_box = Qt.QGroupBox("Reading to send")
        self.field_form = Qt.QFormLayout(self.field_box)
        # Its own height and no more: a taller dialog puts the slack in the
        # stretch above the buttons, not between these rows.
        self.field_box.setSizePolicy(Qt.QSizePolicy.Preferred,
                                     Qt.QSizePolicy.Fixed)
        self.layout.addWidget(self.field_box)
        self.frame_label = Qt.QLabel("")
        self.frame_label.setWordWrap(True)
        self.layout.addWidget(self.frame_label)

    def _profile_picked(self):
        self.apply_profile(self.profile_combo.currentData())

    def apply_profile(self, key):
        """Rebuild the field rows, and move to this device's own band."""
        info = ism_frame.PROFILE_INFO[key]
        # removeRow, not takeAt: takeAt empties a row's items but leaves the
        # row itself behind, so every device picked added empty rows to the
        # form, and deleteLater left the old spin boxes drawn, unlabelled,
        # over the new ones until the event loop got round to them.
        while self.field_form.rowCount():
            self.field_form.removeRow(0)
        self._spins = {}
        saved = self._fields[key]
        for name, label, kind, low, high, default in info['fields']:
            if kind == 'float':
                spin = TrimmedSpinBox()
                spin.setDecimals(2)
                spin.setSingleStep(0.1)
            else:
                spin = Qt.QSpinBox()
            spin.setRange(low, high)
            spin.setValue(saved.get(name, default))
            spin.valueChanged.connect(lambda _v: self._fields_changed())
            self.field_form.addRow(label + ":", spin)
            self._spins[name] = spin
        if hasattr(self, 'cf_chooser'):
            self.cf_chooser.setValue(info['freq_mhz'])
            self.interval_spin.setValue(info['interval_s'])
        self._fields_changed()

    def current_fields(self):
        return {name: (spin.value() if isinstance(spin, Qt.QSpinBox)
                       else float(spin.value()))
                for name, spin in self._spins.items()}

    def _fields_changed(self):
        """Say what the frame will be, and why if it cannot be built.

        Building it here is cheap - it is arithmetic on a few dozen bits - and
        it catches the two hazards that would otherwise only show up as a
        decode under the wrong model name: an id that collides with Rubicson,
        and a Nexus humidity of zero.
        """
        key = self.profile_combo.currentData()
        fields = self.current_fields()
        self._fields[key] = dict(fields)
        try:
            frame = build_frame(key, fields)
        except Exception as exc:
            self.frame_label.setText("Cannot build this frame: %s" % exc)
            return
        notes = []
        if key == 'nexus_th' and ism_frame.rubicson_collides(frame.bits):
            notes.append("this id and reading satisfy Rubicson's CRC, so "
                         "Rubicson will claim the frame and Nexus-TH will "
                         "never see it - change the ID by one")
        self.frame_label.setText(
            "%d bits, %d rows, %.0f ms on the air.  rtl_433 -y '%s'%s"
            % (len(frame.bits), frame.rows, frame.duration_us / 1000.0,
               ism_frame.code(frame.bits),
               "\n\nWarning: " + "; ".join(notes) if notes else ""))

    def create_frequency_control(self):
        self.cf_chooser = FrequencyChooser(
            minimum=FREQ_MIN_MHZ, maximum=FREQ_MAX_MHZ, value=433.92,
            channels=ism_frame.BANDS, slider_range=(300.0, 960.0))
        self.layout.addWidget(self.cf_chooser)
        legal = Qt.QLabel(
            "433.92 MHz is not a US ISM band - only FCC 15.231 periodic "
            "operation makes a remote legal there, and it forbids continuous "
            "transmission at any power. Run this into a cable with 20-30 dB "
            "of attenuation, not an antenna.")
        legal.setWordWrap(True)
        self.layout.addWidget(legal)

    def create_power_control(self):
        self.pwr_layout = Qt.QHBoxLayout()
        self.pwr_slider = Qt.QSlider(QtCore.Qt.Horizontal)
        self.pwr_slider.setMinimum(0)
        self.pwr_slider.setMaximum(100)
        self.pwr_slider.setValue(50)
        self.pwr_label = Qt.QLabel("Power Level: 50%")
        self.pwr_slider.valueChanged.connect(
            lambda v: self.pwr_label.setText(f"Power Level: {v}%"))
        self.pwr_layout.addWidget(self.pwr_label)
        self.pwr_layout.addWidget(self.pwr_slider)
        self.layout.addLayout(self.pwr_layout)

    def create_timing_controls(self):
        row = Qt.QHBoxLayout()
        row.addWidget(Qt.QLabel("Send every (s):"))
        self.interval_spin = TrimmedSpinBox()
        self.interval_spin.setDecimals(2)
        self.interval_spin.setSingleStep(1.0)
        self.interval_spin.setRange(INTERVAL_MIN_S, INTERVAL_MAX_S)
        self.interval_spin.setValue(60.0)
        self.interval_spin.setToolTip(
            "Silence between bursts. The real sensors send about this often, "
            "and every one of these bands has a duty-cycle limit that a "
            "continuously looping burst would fail at any power.")
        row.addWidget(self.interval_spin)
        row.addSpacing(20)
        row.addWidget(Qt.QLabel("Tune below by (kHz):"))
        self.offset_spin = TrimmedSpinBox()
        self.offset_spin.setDecimals(1)
        self.offset_spin.setSingleStep(50.0)
        self.offset_spin.setRange(OFFSET_MIN_KHZ, OFFSET_MAX_KHZ)
        self.offset_spin.setValue(DEFAULT_OFFSET_KHZ)
        self.offset_spin.setToolTip(
            "The radio tunes this far below the frequency above and the "
            "signal is put back as a baseband tone, so the carrier the "
            "radio leaks at zero amplitude lands outside the receiver's "
            "window instead of on top of the signal.")
        row.addWidget(self.offset_spin)
        row.addStretch()
        self.layout.addLayout(row)

    def load_config(self):
        if not os.path.exists(self.config_file):
            os.makedirs(self.config_dir, exist_ok=True)
            return
        try:
            with open(self.config_file, 'r') as f:
                config = json.load(f)
        except Exception as exc:
            print(f"ISM: could not read {self.config_file}: {exc}",
                  file=sys.stderr)
            return

        # One setting that fails to restore must not take the rest with it.
        def restore(name, apply):
            try:
                apply()
            except Exception as exc:
                print(f"ISM: could not restore {name}: {exc}", file=sys.stderr)

        def fields():
            saved = config.get('fields') or {}
            for key, values in saved.items():
                if key in self._fields and isinstance(values, dict):
                    self._fields[key].update(
                        {k: v for k, v in values.items()
                         if k in self._fields[key]})

        def profile():
            index = self.profile_combo.findData(config.get('profile'))
            if index >= 0:
                self.profile_combo.setCurrentIndex(index)
            self.apply_profile(self.profile_combo.currentData())

        restore('the readings', fields)
        restore('the device', profile)
        restore('the frequency', lambda: self.cf_chooser.setValue(
            float(config['center_freq'])) if 'center_freq' in config else None)
        restore('the power', lambda: self.pwr_slider.setValue(
            int(power_percent(config.get('power_level'), 50))))
        restore('the interval', lambda: self.interval_spin.setValue(
            float(config['interval_s'])) if 'interval_s' in config else None)
        restore('the offset', lambda: self.offset_spin.setValue(
            float(config['offset_khz'])) if 'offset_khz' in config else None)

    def save_config(self):
        self._fields[self.profile_combo.currentData()] = self.current_fields()
        update_app_config(self.config_file, {
            'profile': self.profile_combo.currentData(),
            'fields': self._fields,
            'center_freq': self.cf_chooser.value(),
            'power_level': self.pwr_slider.value(),
            'interval_s': self.interval_spin.value(),
            'offset_khz': self.offset_spin.value(),
        })

    def accept(self):
        self.save_config()
        super().accept()

    def get_values(self):
        return {
            'radio_type': self.radio_type,
            'ipXmitAddr': self.usrp_ip if self.radio_type == 'usrp' else '',
            'cf': self.cf_chooser.value(),
            'pwr': self.pwr_slider.value(),
            'profile': self.profile_combo.currentData(),
            'fields': self.current_fields(),
            'interval_s': self.interval_spin.value(),
            'offset_khz': self.offset_spin.value(),
        }


class ismXmitter(gr.top_block, Qt.QWidget):
    # What this window's own controls change that its dialog should open on
    # next time - see apps/utils.py: save_flowgraph_settings.
    SAVED_SETTINGS = {'power_level': 'rfPwr', 'center_freq': 'cf'}

    def __init__(self, config_values=None):
        gr.top_block.__init__(self, "ISM Transmitter", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("ISM Transmitter")
        apply_flowgraph_theme(self)
        try:
            self.setWindowIcon(Qt.QIcon.fromTheme('gnuradio-grc'))
        except Exception:
            pass
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

        self.settings = Qt.QSettings("GNU Radio", "ismXmitter")
        try:
            geometry = self.settings.value("geometry")
            if geometry:
                self.restoreGeometry(geometry)
        except BaseException as exc:
            print(f"Qt GUI: Could not restore geometry: {str(exc)}",
                  file=sys.stderr)

        if config_values is None:
            config_dialog = ConfigDialog()
            if not config_dialog.exec_():
                sys.exit(0)
            values = config_dialog.get_values()
        else:
            values = config_values

        self.radio_type = radio_type = values.get('radio_type', 'hackrf')
        self.cf = cf = float(values.get('cf', 433.92))
        self.rfPwr = rfPwr = values.get('pwr', 50)
        self.profile_key = values.get('profile', DEFAULT_PROFILE)
        fields = values.get('fields') or default_fields(self.profile_key)
        self.offset_hz = float(values.get('offset_khz',
                                          DEFAULT_OFFSET_KHZ)) * 1e3
        self.interval_s = float(values.get('interval_s', 60.0))
        self.samp_rate = SAMPLE_RATES.get(radio_type, DEFAULT_RATE)
        ipXmitAddr = values.get('ipXmitAddr', '')

        ##################################################
        # The burst
        ##################################################
        self.frame = build_frame(self.profile_key, fields)
        megabytes = (self.frame.duration_us * 1e-6 * self.samp_rate * 8) / 1e6
        if megabytes > MAX_BURST_MB:
            raise ValueError(
                "this burst would be %.0f MB of samples at %.6g S/s; lower "
                "the repeat count" % (megabytes, self.samp_rate))
        # The whole burst is rendered up front rather than modulated live.
        # Every timing is then exact and nothing depends on when the scheduler
        # ran: the inter-frame gaps carry bits, and SoapyHackRF's MTU alone is
        # 131072 samples - about 16 ms at 8 MS/s - so a gate toggled from the
        # Qt thread could not place an edge inside a frame if it tried.
        self.burst = ism_frame.render(self.frame, fs=self.samp_rate,
                                      amplitude=AMPLITUDE,
                                      offset_hz=self.offset_hz)
        idle = max(1, int(round(self.interval_s * self.samp_rate)))
        print("ISM transmitter: %s, %d bits x %d rows, %.0f ms burst every "
              "%.2f s, %.6g MHz (radio at %.6g), %.6g S/s, %.0f MB"
              % (self.profile_key, len(self.frame.bits), self.frame.rows,
                 self.frame.duration_us / 1000.0, self.interval_s, cf,
                 cf - self.offset_hz / 1e6, self.samp_rate, megabytes))

        ##################################################
        # Controls
        ##################################################
        self._device_tool_bar = Qt.QToolBar(self)
        self._device_tool_bar.addWidget(Qt.QLabel(
            "Sending: %s   -   %s   -   every %.2f s   -   radio tuned "
            "%.0f kHz low, signal put back at baseband"
            % (ism_frame.PROFILE_INFO[self.profile_key]['label'],
               ism_frame.code(self.frame.bits), self.interval_s,
               self.offset_hz / 1e3)))
        self.top_grid_layout.addWidget(self._device_tool_bar, 0, 0, 1, 7)

        # Standby is a baseband gate, not an RF one, and that is enough here
        # only because of the offset: with the radio tuned 400 kHz low, the
        # carrier it leaks at zero amplitude lands outside a 433.92 MHz
        # receiver's window rather than on top of the signal. It is useless
        # between bits - the edges it can place are milliseconds wide - and
        # perfectly good between bursts, which is all it is asked for.
        self._transmit_box = Qt.QCheckBox("Transmitting")
        self._transmit_box.setChecked(True)
        self._transmit_box.toggled.connect(self.set_transmitting)
        self.top_grid_layout.addWidget(self._transmit_box, 0, 7, 1, 3)

        self._cf_range = frequency_range(FREQ_MIN_MHZ, FREQ_MAX_MHZ, 0.01, cf,
                                         200)
        self._cf_win = RangeWidget(self._cf_range, self.set_cf,
                                   "Center Frequency (MHz)", "counter", float,
                                   QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._cf_win, 1, 0, 1, 5)
        self._rfPwr_range = Range(0, 100, 1, rfPwr, 200)
        self._rfPwr_win = RangeWidget(self._rfPwr_range, self.set_rfPwr,
                                      "RF Output Power (%)", "counter_slider",
                                      float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._rfPwr_win, 1, 5, 1, 5)
        for c in range(0, 10):
            self.top_grid_layout.setColumnStretch(c, 1)

        ##################################################
        # Radio
        ##################################################
        lo_hz = cf * 1e6 - self.offset_hz
        self._power_range = resolve_power_range(radio_type)
        if radio_type == 'vsg':
            from apps.vsg_sink import vsg_sink
            self.radio_sink = vsg_sink(
                center_freq=lo_hz, sample_rate=self.samp_rate,
                level_dbm=scale_power(rfPwr, self._power_range))
        elif radio_type == 'usrp':
            self.radio_sink = uhd.usrp_sink(
                ",".join(('addr=' + ipXmitAddr, '')),
                uhd.stream_args(cpu_format="fc32", args='',
                                channels=list(range(0, 1))),
                "",
            )
            self.radio_sink.set_samp_rate(self.samp_rate)
            self.radio_sink.set_time_now(uhd.time_spec(time.time()),
                                         uhd.ALL_MBOARDS)
            self.radio_sink.set_center_freq(lo_hz, 0)
            self.radio_sink.set_antenna("TX/RX", 0)
            self._power_range = resolve_power_range(radio_type, self.radio_sink)
            self.radio_sink.set_gain(scale_power(rfPwr, self._power_range), 0)
        else:
            self.radio_sink = soapy.sink('driver=hackrf', 'fc32', 1, '', '',
                                         [''], [''])
            self.radio_sink.set_sample_rate(0, self.samp_rate)
            self.radio_sink.set_frequency(0, lo_hz)
            self.radio_sink.set_gain(0, 'VGA',
                                     scale_power(rfPwr, self._power_range))
            self.radio_sink.set_gain(0, 'AMP', 0)

        ##################################################
        # Signal
        ##################################################
        # The burst loops out of a vector source and the silence between
        # bursts comes from a null source, spliced by a stream mux. The idle
        # costs no memory that way, which matters: a minute of it at 8 MS/s
        # would be 3.8 GB if it were in the vector.
        self.source = blocks.vector_source_c(self.burst, True, 1, [])
        self.idle = blocks.null_source(gr.sizeof_gr_complex)
        self.mux = blocks.stream_mux(gr.sizeof_gr_complex,
                                     [len(self.burst), idle])
        self.gate = blocks.multiply_const_cc(1.0)
        self.connect((self.source, 0), (self.mux, 0))
        self.connect((self.idle, 0), (self.mux, 1))
        self.connect(self.mux, self.gate, self.radio_sink)

        ##################################################
        # Displays
        ##################################################
        # The envelope in time, and it is the display that matters: a spectrum
        # average cannot see a transmitter that is off most of the time, and
        # an OOK frame is mostly off. One burst plus a little, so a whole
        # frame fits on screen.
        self.envelope = blocks.complex_to_mag(1)
        self.connect(self.gate, self.envelope)
        # Decimated for the display only. A whole burst at the radio's rate is
        # millions of points and no time sink will draw that; a boxcar
        # decimator down to about 32k points draws instantly and a narrow
        # pulse still shows, lower but present, rather than aliasing away.
        # One repeat plus a little, not the whole burst: at a whole burst's
        # span twelve repeats of a 496 us pulse are a solid block, and the
        # gaps between the pulses are the entire point of the display.
        span = int(round(1.3 * self.frame.row_us * 1e-6 * self.samp_rate))
        decim = max(1, -(-span // DISPLAY_POINTS))
        self.display_rate = self.samp_rate / decim
        self.thin = filter.fir_filter_fff(decim, [1.0 / decim] * decim)
        self.connect(self.envelope, self.thin)
        self.qtgui_time_sink = qtgui.time_sink_f(
            max(256, span // decim), self.display_rate,
            'Envelope - the gaps are the message', 1, None)
        self.qtgui_time_sink.set_update_time(0.05)
        self.qtgui_time_sink.set_y_axis(-0.05, AMPLITUDE * 1.3)
        self.qtgui_time_sink.set_y_label('Amplitude', "")
        self.qtgui_time_sink.enable_grid(True)
        self.qtgui_time_sink.enable_autoscale(False)
        self.qtgui_time_sink.enable_control_panel(False)
        self.qtgui_time_sink.disable_legend()
        # Normal rather than auto, so the last frame stays on screen through
        # the silence between bursts instead of the trace going flat. With a
        # minute between bursts - which is how often the real sensor sends -
        # auto mode would show an empty display almost all the time.
        self.qtgui_time_sink.set_trigger_mode(qtgui.TRIG_MODE_NORM,
                                              qtgui.TRIG_SLOPE_POS,
                                              AMPLITUDE / 2.0, 0, 0, "")
        self._qtgui_time_sink_win = sip.wrapinstance(
            self.qtgui_time_sink.qwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(self._qtgui_time_sink_win, 2, 0, 5, 10)
        self.connect(self.thin, self.qtgui_time_sink)

        self.qtgui_freq_sink = qtgui.freq_sink_c(
            4096, window.WIN_BLACKMAN_hARRIS, lo_hz, self.samp_rate,
            "RF Spectrum - the signal sits beside the radio's own leak",
            1, None)
        self.qtgui_freq_sink.set_update_time(0.10)
        self.qtgui_freq_sink.set_y_axis(*SPECTRUM_Y_AXIS)
        self.qtgui_freq_sink.set_y_label('Relative Gain', 'dB')
        self.qtgui_freq_sink.enable_grid(True)
        self.qtgui_freq_sink.enable_autoscale(False)
        self.qtgui_freq_sink.set_fft_average(0.2)
        self.qtgui_freq_sink.enable_control_panel(False)
        self.qtgui_freq_sink.disable_legend()
        self._qtgui_freq_sink_win = sip.wrapinstance(
            self.qtgui_freq_sink.qwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(self._qtgui_freq_sink_win, 7, 0, 5, 10)
        self.connect(self.gate, self.qtgui_freq_sink)
        for r in range(2, 12):
            self.top_grid_layout.setRowStretch(r, 1)

    def closeEvent(self, event):
        self.settings = Qt.QSettings("GNU Radio", "ismXmitter")
        self.settings.setValue("geometry", self.saveGeometry())
        self.stop()
        self.wait()
        event.accept()

    def set_transmitting(self, on):
        self.gate.set_k(1.0 if on else 0.0)

    def set_rfPwr(self, rfPwr):
        self.rfPwr = rfPwr
        if self.radio_type == 'vsg':
            self.radio_sink.set_level(scale_power(self.rfPwr,
                                                  self._power_range))
        elif self.radio_type == 'usrp':
            self.radio_sink.set_gain(scale_power(self.rfPwr,
                                                 self._power_range), 0)
        else:
            self.radio_sink.set_gain(0, 'VGA',
                                     scale_power(self.rfPwr,
                                                 self._power_range))

    def set_cf(self, cf):
        """Retune. The radio always sits ``offset_hz`` below what is asked for,
        because the signal is put back on top of it at baseband."""
        self.cf = cf
        lo_hz = self.cf * 1e6 - self.offset_hz
        self.qtgui_freq_sink.set_frequency_range(lo_hz, self.samp_rate)
        if self.radio_type == 'usrp':
            self.radio_sink.set_center_freq(lo_hz, 0)
        else:
            self.radio_sink.set_frequency(0, lo_hz)


def main(top_block_cls=ismXmitter, options=None, app=None, config_values=None):
    own_app = app is None
    if own_app:
        app = Qt.QApplication(sys.argv)

    tb = top_block_cls(config_values)
    tb.start()
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

    # Inside the launcher its event loop is already running: hand the window
    # back for it to watch rather than starting a loop of our own.
    if not own_app:
        return tb
    return app.exec_()


if __name__ == '__main__':
    main()
