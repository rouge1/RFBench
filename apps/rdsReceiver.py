#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# SPDX-License-Identifier: GPL-3.0
#
# RDS / RBDS Receiver - tunes an FM broadcast station and decodes the data
# carried on its 57 kHz subcarrier (station ID, program service name,
# RadioText, program type, clock time).
#
# This is the only receiving application in the launcher. It uses the radio
# chosen in Settings, as the transmitters do - except that the Signal Hound
# VSG60 can only transmit, so with that selected the dialog just says so.

import json
import os
import signal
import sys
import threading

import numpy as np  # type: ignore
try:                 # PyQt5 ships sip inside the package; some builds also
    import sip       # expose it at the top level.
except ImportError:  # pragma: no cover - depends on the PyQt5 build
    from PyQt5 import sip  # type: ignore
from gnuradio import analog, audio, blocks, filter, gr, qtgui, soapy, uhd  # type: ignore
from gnuradio.fft import window  # type: ignore
from gnuradio.filter import firdes  # type: ignore
from PyQt5 import Qt, QtCore  # type: ignore

from apps.rds_core import RdsDemod, RdsProtocol, clock_text
from apps.utils import (CONFIG_DIR, apply_dark_theme, apply_flowgraph_theme,
                        radio_label,
                        read_settings, update_app_config, SPECTRUM_Y_AXIS,
                        FREQ_DECIMALS, FREQ_STEP_MHZ, FrequencyChooser)

MPX_RATE = 250e3          # everything after the channel filter runs here
# Each radio's own rate, chosen from what it will actually accept, and each
# an exact multiple of the MPX rate so the channel filter decimates by a
# whole number. The BB60D's rates are a ladder - 40/20/10/5/2.5 and on down
# in halves - with nothing at the 2 MS/s the HackRF path uses, so it takes
# 2.5 and decimates by 10 instead of 8.
SAMPLE_RATES = {'hackrf': 2e6, 'usrp': 2e6, 'bb60': 2.5e6}
LO_OFFSET = 300e3         # keep the station clear of the radio's DC spike
AUDIO_RATE = 48000

#: The same span as the FM + RDS Transmitter's, so it can be followed off
#: the broadcast band. 30 MHz to 6 GHz covers the HackRF and the BB60D.
FREQ_MIN_MHZ = 30.0
FREQ_MAX_MHZ = 6000.0
#: What the dialog's slider sweeps, as on the transmitter: the broadcast
#: band, with anything else typed into the box.
FM_BAND_MHZ = (87.5, 108.0)
MAX_DEVIATION = 75e3

#: The pilot's band, and the guard band either side of it: nothing is
#: broadcast from 15 to 23 kHz but the pilot, so what the guard holds is the
#: noise the pilot has to stand out from.
PILOT_BAND_HZ = (18.2e3, 19.8e3)
GUARD_OUTER_HZ = (16e3, 22e3)
GUARD_INNER_HZ = (18e3, 20e3)
#: How far the pilot must stand over that noise, in the same bandwidth, to
#: count as locked; it is let go only below the second. Noise reads within
#: 2 dB of 0, stations with a pilot 13 dB and up - see devnotes/rds.md.
PILOT_LOCK_DB = 10.0
PILOT_UNLOCK_DB = 6.0


def rx_gain_plan(percent, radio_type):
    """Map the 0-100% slider onto a receiver's own gain controls.

    HackRF splits its receive gain across three stages, so spread the slider
    over them: the preamp first (it improves the noise figure), then the LNA in
    its 8 dB steps, then the baseband VGA in 2 dB steps.

    How much gain is right depends almost entirely on the antenna, so the
    slider covers a wide span and both failure modes are worth recognising:
    a pilot that locks while RDS stays buried means too little gain, whereas
    samples pinned near full scale mean too much. On a modest antenna ~68 dB
    (preamp + LNA 24 + VGA 30) was needed; on a good one that clips, and
    ~54 dB (preamp + LNA 16 + VGA 24) is right.
    """
    percent = min(max(float(percent), 0.0), 100.0)
    if radio_type != 'hackrf':
        return {}
    amp = 14.0 if percent >= 20 else 0.0
    total = percent / 100.0 * 102.0
    lna = min(40.0, round(total * 0.45 / 8.0) * 8.0)
    vga = min(62.0, max(0.0, round((total - lna) / 2.0) * 2.0))
    return {'AMP': amp, 'LNA': lna, 'VGA': vga}


class ConfigDialog(Qt.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FM + RDS Receiver Configuration")
        self.layout = Qt.QVBoxLayout(self)
        self.config_dir = CONFIG_DIR
        self.config_file = os.path.join(self.config_dir, "rdsReceiver_config.json")

        settings = read_settings()
        self.usrp_ip = settings.get('usrp_ip', '')
        # The radio is the one chosen in Settings, as for every other app.
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
        self.create_options()

        self.layout.addWidget(self.button_box)
        self.load_config()
        self.update_ok_state()
        apply_dark_theme(self)

    def create_cannot_receive(self):
        """The whole dialog, when Settings name a radio that cannot receive.

        The Signal Hound VSG60 is a signal generator. Rejecting here means the
        launcher never gets as far as opening a device.
        """
        self.setWindowTitle("FM + RDS Receiver")
        row = Qt.QHBoxLayout()
        icon = Qt.QLabel()
        icon.setPixmap(self.style().standardIcon(
            Qt.QStyle.SP_MessageBoxWarning).pixmap(48, 48))
        icon.setAlignment(QtCore.Qt.AlignTop)
        row.addWidget(icon)
        message = Qt.QLabel(
            "<b>The Signal Hound VSG60 cannot receive.</b><br><br>"
            "It only transmits, so the RDS Receiver has no radio to listen "
            "with. Choose the HackRF One, an Ettus USRP or the Signal Hound "
            "BB60D in Settings (the gear icon), then open the RDS Receiver "
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
        # Not only the broadcast band: the FM + RDS Transmitter can go out
        # anywhere its radio reaches, and this has to be able to follow it.
        self.cf_chooser = FrequencyChooser(
            minimum=FREQ_MIN_MHZ, maximum=FREQ_MAX_MHZ, value=98.7,
            label="Station Frequency (MHz):", slider_range=FM_BAND_MHZ)
        self.layout.addWidget(self.cf_chooser)

    def create_gain_control(self):
        row = Qt.QHBoxLayout()
        self.gain_slider = Qt.QSlider(QtCore.Qt.Horizontal)
        self.gain_slider.setRange(0, 100)
        # A BB60D wants its attenuator open and no RF amplification, which
        # is 60%; below that its own converter noise dominates and above it
        # the front end can be overdriven. The HackRF's three stages are a
        # different animal - see rx_gain_plan.
        default = 60 if self.radio_type == 'bb60' else 40
        self.gain_slider.setValue(default)
        self.gain_label = Qt.QLabel(f"RF Gain: {default}%")
        self.gain_slider.valueChanged.connect(
            lambda v: self.gain_label.setText(f"RF Gain: {v}%"))
        row.addWidget(self.gain_label)
        row.addWidget(self.gain_slider)
        self.layout.addLayout(row)

    def create_options(self):
        self.layout.addWidget(Qt.QLabel("Data Standard:"))
        self.region_combo = Qt.QComboBox()
        self.region_combo.addItem("RBDS (North America)", "RBDS")
        self.region_combo.addItem("RDS (Europe / rest of world)", "RDS")
        self.layout.addWidget(self.region_combo)

        self.audio_check = Qt.QCheckBox("Play station audio")
        self.audio_check.setChecked(True)
        self.layout.addWidget(self.audio_check)

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
            print(f"RDS receiver: could not read saved config: {exc}",
                  file=sys.stderr)
            return
        # Restore each setting independently: one unreadable value must not
        # discard everything saved after it. The radio type is not among them;
        # it comes from Settings.
        restore = [
            ('frequency_mhz', lambda v: self.cf_chooser.setValue(float(v))),
            ('gain_percent', lambda v: self.gain_slider.setValue(int(v))),
            ('region', lambda v: self.region_combo.setCurrentIndex(
                1 if v == 'RDS' else 0)),
            ('audio', lambda v: self.audio_check.setChecked(bool(v))),
        ]
        for key, apply in restore:
            if key in config:
                try:
                    apply(config[key])
                except Exception as exc:
                    print(f"RDS receiver: ignoring saved {key!r}: {exc}",
                          file=sys.stderr)

    def save_config(self):
        config = {
            'radio_type': self.radio_type,
            'frequency_mhz': self.cf_chooser.value(),
            'gain_percent': self.gain_slider.value(),
            'region': self.region_combo.currentData(),
            'audio': self.audio_check.isChecked(),
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
            'gain_percent': self.gain_slider.value(),
            'region': self.region_combo.currentData(),
            'audio': self.audio_check.isChecked(),
        }


def fm_front_end(samp_rate):
    """The channel filter and discriminator: the radio's IQ, with the station
    ``LO_OFFSET`` above the LO, in; the MPX at ``MPX_RATE`` out."""
    channel = filter.freq_xlating_fir_filter_ccf(
        int(samp_rate // MPX_RATE),
        firdes.low_pass(1.0, samp_rate, 100e3, 20e3),
        LO_OFFSET, samp_rate)
    demod = analog.quadrature_demod_cf(MPX_RATE / (2 * np.pi * MAX_DEVIATION))
    return channel, demod


def guard_taps():
    """A complex band-pass over the guard band, 16-18 and 20-22 kHz: the
    wide band less the narrow one. Symmetric about the pilot, so the
    discriminator's noise rising with frequency evens out."""
    wide = firdes.complex_band_pass(1.0, MPX_RATE, *GUARD_OUTER_HZ, 500)
    narrow = firdes.complex_band_pass(1.0, MPX_RATE, *GUARD_INNER_HZ, 500)
    assert len(wide) == len(narrow)          # the same transition, the same length
    return (np.asarray(wide) - np.asarray(narrow)).tolist()


class PilotMeter:
    """The pilot's band-pass, which the PLL takes, and whether the pilot
    stands out from the noise beside it.

    Not a level: with no station the discriminator turns noise into a
    multiplex full of noise, and the pilot's band then reads more than a
    real pilot does. A fixed level (1e-4, which this used) called every
    empty channel stereo. So the guard band beside the pilot is measured
    too, scaled to the pilot band's width, and the pilot must stand
    ``PILOT_LOCK_DB`` over it.
    """

    def __init__(self, tb, mpx_complex):
        taps = firdes.complex_band_pass(1.0, MPX_RATE, *PILOT_BAND_HZ, 500)
        self.bpf = filter.fir_filter_ccc(1, taps)
        self.pilot_mag = blocks.complex_to_mag_squared(1)
        self.pilot_avg = filter.single_pole_iir_filter_ff(1e-4)
        self.pilot_probe = blocks.probe_signal_f()
        tb.connect(mpx_complex, self.bpf, self.pilot_mag, self.pilot_avg,
                   self.pilot_probe)
        guard = guard_taps()
        self.guard_bpf = filter.fir_filter_ccc(1, guard)
        self.guard_mag = blocks.complex_to_mag_squared(1)
        self.guard_avg = filter.single_pole_iir_filter_ff(1e-4)
        self.guard_probe = blocks.probe_signal_f()
        tb.connect(mpx_complex, self.guard_bpf, self.guard_mag, self.guard_avg,
                   self.guard_probe)
        # What each passes of flat noise, to scale the guard to the pilot band.
        self._guard_to_pilot = (np.sum(np.abs(np.asarray(taps)) ** 2)
                                / np.sum(np.abs(np.asarray(guard)) ** 2))
        self._on = False

    def level(self):
        return self.pilot_probe.level()

    def snr_db(self):
        """How far the pilot's band stands over the noise beside it, in the
        same bandwidth: 0 dB is noise alone."""
        pilot = self.pilot_probe.level()
        noise = self.guard_probe.level() * self._guard_to_pilot
        if pilot <= 0:
            return -200.0
        if noise <= 0:
            return 200.0                          # a clean signal, no noise at all
        return float(10 * np.log10(pilot / noise))

    def locked(self):
        """A pilot of 2% or more (1e-4; a 9% one reads ~2e-3) standing out
        from the noise beside it."""
        need = PILOT_UNLOCK_DB if self._on else PILOT_LOCK_DB
        self._on = bool(self.level() > 1e-4 and self.snr_db() > need)
        return self._on

    def reset(self):
        self._on = False


class rds_sink(gr.sync_block):
    """Bridges the flowgraph to the RDS decoder in apps/rds_core.py.

    Takes the MPX baseband and the PLL's 19 kHz pilot reference. All the
    per-sample work is vectorised numpy, and the protocol layer only sees
    1187.5 bits per second, so this keeps up with real time comfortably.
    """

    def __init__(self, mpx_rate, region):
        gr.sync_block.__init__(self, name='rds_sink',
                               in_sig=[np.float32, np.complex64], out_sig=None)
        self.demod = RdsDemod(mpx_rate)
        self.proto = RdsProtocol(region=region)
        self._lock = threading.Lock()

    def work(self, input_items, output_items):
        n = min(len(input_items[0]), len(input_items[1]))
        # Hold the lock across both stages: retuning swaps them out from the Qt
        # thread, which must not happen midway through a chunk.
        with self._lock:
            bits = self.demod.feed(input_items[0][:n], input_items[1][:n])
            if len(bits):
                self.proto.feed(bits)
        return n

    def reset(self, mpx_rate, region):
        """Throw away decoded state and re-measure timing, after a retune."""
        with self._lock:
            self.demod = RdsDemod(mpx_rate)
            self.proto = RdsProtocol(region=region)

    def snapshot(self):
        with self._lock:
            return self.proto.snapshot()


class rdsReceiver(gr.top_block, Qt.QWidget):
    # What this window's own controls change that its dialog should
    # open on next time - see apps/utils.py: save_flowgraph_settings.
    SAVED_SETTINGS = {'gain_percent': 'gain_percent',
                      'frequency_mhz': 'freq_mhz'}

    def __init__(self, config_values=None):
        gr.top_block.__init__(self, "FM + RDS Receiver", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("FM + RDS Receiver")
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

        self.settings = Qt.QSettings("GNU Radio", "rdsReceiver")
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
        self.freq_mhz = float(values.get('frequency_mhz', 98.7))
        self.gain_percent = float(values.get('gain_percent', 40))
        self.region = values.get('region', 'RBDS')
        self.want_audio = bool(values.get('audio', True))
        self.usrp_ip = values.get('ipXmitAddr', '')
        self.samp_rate = SAMPLE_RATES.get(self.radio_type, 2e6)

        self._build_controls()
        self._build_flowgraph()
        self._build_readout()

        self.status_timer = Qt.QTimer(self)
        self.status_timer.timeout.connect(self.refresh_readout)
        self.status_timer.start(400)

    # ------------------------------------------------------------------ UI
    def _build_controls(self):
        row = Qt.QHBoxLayout()
        row.addWidget(Qt.QLabel("Station (MHz):"))
        self.freq_spin = Qt.QDoubleSpinBox()
        self.freq_spin.setDecimals(FREQ_DECIMALS)
        self.freq_spin.setSingleStep(FREQ_STEP_MHZ)
        self.freq_spin.setRange(FREQ_MIN_MHZ, FREQ_MAX_MHZ)
        self.freq_spin.setValue(self.freq_mhz)
        # Retune on Enter, not on every keystroke - each retune restarts
        # the decoder.
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
        self.reset_btn = Qt.QPushButton("Clear Decoded Data")
        self.reset_btn.clicked.connect(self.reset_decoder)
        row.addWidget(self.reset_btn)
        row.addStretch()

        holder = Qt.QWidget()
        holder.setLayout(row)
        self.top_grid_layout.addWidget(holder, 0, 0, 1, 10)

    def _build_readout(self):
        box = Qt.QGroupBox("Decoded Station Data")
        grid = Qt.QGridLayout()
        box.setLayout(grid)
        big = Qt.QFont()
        big.setPointSize(15)
        big.setBold(True)
        mono = Qt.QFont("Monospace")
        mono.setStyleHint(Qt.QFont.TypeWriter)
        mono.setPointSize(13)

        self.lbl = {}
        def add(key, caption, row, col, font=None, span=1):
            grid.addWidget(Qt.QLabel(f"<b>{caption}</b>"), row, col * 2)
            value = Qt.QLabel("-")
            if font:
                value.setFont(font)
            value.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            value.setWordWrap(True)
            grid.addWidget(value, row, col * 2 + 1, 1, span)
            self.lbl[key] = value

        add('station_name', "Station", 0, 0, big)
        add('pi', "Station ID (PI)", 0, 1)
        add('pty', "Program Type", 0, 2)
        add('ps', "Now showing (PS)", 1, 0, mono, span=5)
        add('nowplaying', "Now Playing", 2, 0, big, span=5)
        add('radiotext', "RadioText", 3, 0, mono, span=5)
        add('flags', "Flags", 4, 0)
        add('clock', "Station Clock", 4, 1)
        add('quality', "Decode Quality", 4, 2)
        self.top_grid_layout.addWidget(box, 1, 0, 1, 10)

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
            # gr-soapy cannot drive this device at all - every setter fails
            # with "setupStream: Invalid format" - so it goes through the
            # raw SoapySDR wrapper, same as the ATSC receiver.
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

        # Shift the station from the LO offset down to DC and decimate to MPX.
        self.channel, self.demod = fm_front_end(samp_rate)

        # Pilot reference: band-pass the 19 kHz tone and lock a PLL to it. Its
        # third harmonic is the RDS subcarrier and its 16th subharmonic is the
        # bit clock, so this one lock drives both. The meter also says
        # whether there is a pilot at all, for the stereo indicator.
        self.to_complex = blocks.float_to_complex(1)
        self.pilot = PilotMeter(self, self.to_complex)
        self.pilot_bpf = self.pilot.bpf
        self.pilot_pll = analog.pll_refout_cc(
            0.001,
            2 * np.pi * 19.2e3 / MPX_RATE,
            2 * np.pi * 18.8e3 / MPX_RATE)
        self.rds = rds_sink(MPX_RATE, self.region)

        self.connect(self.radio_source, self.channel, self.demod)
        self.connect(self.demod, self.to_complex)
        self.connect(self.pilot_bpf, self.pilot_pll)
        self.connect(self.demod, (self.rds, 0))
        self.connect(self.pilot_pll, (self.rds, 1))

        self._build_spectrum()

        if self.want_audio:
            self._build_audio()

    def _build_audio(self):
        # Mono audio is the 0-15 kHz part of the MPX.
        self.audio_lpf = filter.fir_filter_fff(
            1, firdes.low_pass(1.0, MPX_RATE, 15e3, 3e3))
        self.audio_resamp = filter.rational_resampler_fff(
            interpolation=24, decimation=125, taps=[], fractional_bw=0)
        self.deemph = analog.fm_deemph(AUDIO_RATE, 75e-6)
        self.volume = blocks.multiply_const_ff(0.4)
        try:
            self.audio_out = audio.sink(AUDIO_RATE, '', True)
        except Exception as exc:
            print(f"RDS receiver: audio output unavailable ({exc})",
                  file=sys.stderr)
            self.want_audio = False
            return
        self.connect(self.demod, self.audio_lpf, self.audio_resamp,
                     self.deemph, self.volume, self.audio_out)

    def _build_spectrum(self):
        self.mpx_sink = qtgui.freq_sink_f(
            2048, window.WIN_BLACKMAN_hARRIS, 0, MPX_RATE,
            'FM Baseband (MPX) - pilot 19 kHz, stereo 38 kHz, RDS 57 kHz', 1,
            None)
        self.mpx_sink.set_update_time(0.10)
        self.mpx_sink.set_y_axis(*SPECTRUM_Y_AXIS)
        self.mpx_sink.set_y_label('Relative Gain', 'dB')
        self.mpx_sink.set_trigger_mode(qtgui.TRIG_MODE_FREE, 0.0, 0, "")
        self.mpx_sink.enable_autoscale(True)
        self.mpx_sink.enable_grid(True)
        self.mpx_sink.set_fft_average(0.2)
        self.mpx_sink.enable_axis_labels(True)
        self.mpx_sink.enable_control_panel(False)
        self.mpx_sink.set_plot_pos_half(True)
        self.mpx_sink.disable_legend()
        widget = sip.wrapinstance(self.mpx_sink.qwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(widget, 2, 0, 6, 10)
        self.connect(self.demod, self.mpx_sink)

    # ------------------------------------------------------------ controls
    def apply_gain(self):
        """Set receive gain. Must run after start() - see below."""
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
        # SoapyHackRF silently ignores the AMP stage when it is set before the
        # stream is running, so gains are applied once the flowgraph is going.
        for name, value in rx_gain_plan(self.gain_percent, 'hackrf').items():
            self.radio_source.set_gain(0, name, value)

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
        self.reset_decoder()

    def reset_decoder(self):
        self.rds.reset(MPX_RATE, self.region)
        self.pilot.reset()                        # a new station earns its lock

    # ------------------------------------------------------------- readout
    def refresh_readout(self):
        snap = self.rds.snapshot()
        # NRSC-G300 recommends RT+ StationName over anything derived from the
        # PI, so prefer it when the station sends one.
        name = snap['station_short'] or snap['station_name'] or '(waiting)'
        self.lbl['station_name'].setText(name)

        title, artist = snap['title'], snap['artist']
        self.lbl['nowplaying'].setText(
            ' - '.join(x for x in (artist, title) if x) if (title or artist)
            else '-')

        pi_text = snap['pi_hex'] or '-'
        if snap['callsign_confirmed']:
            # Corroborated by the station's own PS/RadioText, so state it plainly.
            pi_text += f"  ({snap['callsign_confirmed']})"
        elif snap['callsign']:
            # Plenty of US stations use a PI that does not follow the call-sign
            # formula, so an uncorroborated mapping stays explicitly a guess.
            pi_text += f"  (maybe {snap['callsign']})"
        self.lbl['pi'].setText(pi_text)
        self.lbl['pty'].setText(snap['pty'] or '-')
        self.lbl['ps'].setText(snap['ps'] or '-')
        self.lbl['radiotext'].setText(snap['radiotext'] or '-')

        flags = []
        if snap['tp']:
            flags.append("Traffic Program")
        if snap['ta']:
            flags.append("Traffic Announcement")
        flags.append("Stereo pilot locked" if self.pilot.locked() else "No pilot")
        self.lbl['flags'].setText(", ".join(flags))

        clock = snap['clock']
        if clock:
            # The clock runs on between groups, so say how fresh its last sync
            # is: a station that stops sending leaves it running unchecked.
            ago = clock['synced_ago_s']
            ago = f"{ago:.0f} s" if ago < 120 else f"{ago / 60:.0f} min"
            self.lbl['clock'].setText(f"{clock_text(clock)}   synced {ago} ago")
        else:
            self.lbl['clock'].setText('-')

        seen = snap['blocks_seen']
        if seen:
            self.lbl['quality'].setText(
                f"{snap['groups']} groups, "
                f"{100*(1-(snap['block_error_rate'] or 0)):.0f}% blocks good")
        else:
            self.lbl['quality'].setText('searching...')

    def closeEvent(self, event):
        self.settings = Qt.QSettings("GNU Radio", "rdsReceiver")
        self.settings.setValue("geometry", self.saveGeometry())
        self.status_timer.stop()
        self.stop()
        self.wait()
        event.accept()


def main(top_block_cls=rdsReceiver, options=None, app=None, config_values=None):
    if app is None:
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
