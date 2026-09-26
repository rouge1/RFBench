#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: PPM-OOK Live Audio Xmitter
# Author: Gary Schafer
# GNU Radio version: 3.10.10.0

# Standard library imports
import json
import os
import signal
import sip  # type: ignore
import sys
import time

# Third party imports
import numpy as np # type: ignore  
from gnuradio import blocks # type: ignore
from gnuradio import digital # type: ignore
from gnuradio import eng_notation # type: ignore
from gnuradio import filter # type: ignore
from gnuradio.filter import firdes # type: ignore  
from gnuradio import gr # type: ignore
from gnuradio import qtgui # type: ignore
from gnuradio import soapy, uhd  # type: ignore
from gnuradio.fft import window  # type: ignore
from packaging.version import Version as StrictVersion # type: ignore
from PyQt5 import Qt # type: ignore
from PyQt5 import QtCore # type: ignore
from PyQt5.QtCore import QObject, pyqtSlot # type: ignore

# Local imports
from apps.audio_file import AudioFileSource
from apps.media import AUDIO, choices
from apps.utils import (CONFIG_DIR, apply_dark_theme, apply_flowgraph_theme,
                        radio_label,
                        read_settings, update_app_config, power_percent,
                        resolve_power_range, scale_power, SPECTRUM_Y_AXIS,
                        FrequencyChooser, frequency_range)

if __name__ == '__main__':
    import ctypes
    import sys
    if sys.platform.startswith('linux'):
        try:
            x11 = ctypes.cdll.LoadLibrary('libX11.so')
            x11.XInitThreads()
        except:
            print("Warning: failed to XInitThreads()")

class ConfigDialog(Qt.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PPM-OOK Audio Xmitter Configuration")
        self.layout = Qt.QVBoxLayout(self)
        self.config_dir = CONFIG_DIR
        self.config_file = os.path.join(self.config_dir, "ppmookAudioXmitter_config.json")
        
        # Read settings from window_settings.json
        settings = read_settings()
        self.usrp_ip = settings.get('usrp_ip', '')
        self.radio_type = settings.get('radio_type', 'hackrf')
        
        # Add OK/Cancel buttons
        self.button_box = Qt.QDialogButtonBox(
            Qt.QDialogButtonBox.Ok | Qt.QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        
        # Create controls
        self.create_radio_label()
        self.create_frequency_control()
        self.create_audio_source_control()  # Add this line
        self.create_pulse_width_control()
        self.create_coherence_control()
        self.create_mod_level_control()
        self.create_power_control()

        self.layout.addWidget(self.button_box)
        
        # Load saved configuration
        self.load_config()
        
        # Apply dark theme
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
        # Not a whole-MHz slider: the window tunes far finer, and what it
        # was left at comes back here (SAVED_SETTINGS), so this has to
        # hold it exactly - see FrequencyChooser.
        self.cf_chooser = FrequencyChooser(minimum=30.0, maximum=2200.0,
                                           value=300.0)
        self.layout.addWidget(self.cf_chooser)

    def create_audio_source_control(self):
        self.audio_layout = Qt.QHBoxLayout()
        self.audio_combo = Qt.QComboBox()
        ok_button = self.button_box.button(Qt.QDialogButtonBox.Ok)
        
        # Read settings and get the audio files
        settings = read_settings()
        media_dir = settings.get('media_directory', '')
        
        try:
            if not media_dir or not os.path.exists(media_dir):
                raise FileNotFoundError("Error - Setup Media directory in Settings")
                
            # Every WAV and MP3 file, subfolders included and whatever the case of
            # its extension - see apps/media.py.
            wav_files = choices(media_dir, AUDIO)
            if not wav_files:
                raise FileNotFoundError("No WAV or MP3 files found in media directory")
                
            for display_name, wav_file in wav_files:
                self.audio_combo.addItem(display_name, wav_file)
                
            # Only enable OK button if we have both IP addresses and media files
            ok_button.setEnabled(self.radio_type in ('hackrf', 'vsg')
                                  or bool(self.usrp_ip))
            ok_button.setGraphicsEffect(None)
                
        except Exception as e:
            self.audio_combo.addItem(str(e))
            self.audio_combo.setEnabled(False)
            # Disable OK button and add opacity effect
            ok_button.setEnabled(False)
            opacity_effect = Qt.QGraphicsOpacityEffect()
            opacity_effect.setOpacity(0.30)
            ok_button.setGraphicsEffect(opacity_effect)
                    
        self.layout.addWidget(Qt.QLabel("Audio Source:"))
        self.layout.addWidget(self.audio_combo)

    def create_pulse_width_control(self):
        self.pulse_combo = Qt.QComboBox()
        self.pulse_combo.addItems(['10 usec', '20 usec', '40 usec'])
        self.pulse_combo.setCurrentIndex(1)  # Default 20 usec
        self.layout.addWidget(Qt.QLabel("Pulse Width:"))
        self.layout.addWidget(self.pulse_combo)

    def create_coherence_control(self):
        self.coherence_combo = Qt.QComboBox()
        self.coherence_combo.addItems(['Non-Coherent', 'Coherent'])
        self.layout.addWidget(Qt.QLabel("Coherence:"))
        self.layout.addWidget(self.coherence_combo)

    def create_mod_level_control(self):
        self.mod_layout = Qt.QHBoxLayout()
        self.mod_slider = Qt.QSlider(QtCore.Qt.Horizontal)
        self.mod_slider.setMinimum(1)
        self.mod_slider.setMaximum(50)
        self.mod_slider.setValue(3)
        self.mod_label = Qt.QLabel("Modulation Level: 0.3")
        self.mod_slider.valueChanged.connect(
            lambda v: self.mod_label.setText(f"Modulation Level: {v/10:.1f}"))
        self.mod_layout.addWidget(self.mod_label)
        self.mod_layout.addWidget(self.mod_slider)
        self.layout.addLayout(self.mod_layout)

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

    def load_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                    
                self.cf_chooser.setValue(config.get('center_freq', 300))
                self.pulse_combo.setCurrentIndex(config.get('pulse_width_index', 1))
                self.coherence_combo.setCurrentIndex(config.get('coherence', 0))
                self.mod_slider.setValue(int(config.get('mod_level', 0.3) * 10))
                self.pwr_slider.setValue(power_percent(config.get('power_level'), 50))

                # Load saved audio file selection
                saved_audio = config.get('audio_file', '')
                if saved_audio:
                    # Find and select the saved audio file
                    for i in range(self.audio_combo.count()):
                        if self.audio_combo.itemData(i) == saved_audio:
                            self.audio_combo.setCurrentIndex(i)
                            break
            except:
                pass
        else:
            os.makedirs(self.config_dir, exist_ok=True)

    def save_config(self):
        config = {
            'center_freq': self.cf_chooser.value(),
            'pulse_width_index': self.pulse_combo.currentIndex(),
            'coherence': self.coherence_combo.currentIndex(),
            'mod_level': self.mod_slider.value() / 10.0,
            'power_level': self.pwr_slider.value(),
            'audio_file': self.audio_combo.currentData()  # Save selected audio file path
        }
        
        update_app_config(self.config_file, config)

    def accept(self):
        self.save_config()
        super().accept()

    def get_values(self):
        pulse_widths = [10, 20, 40]
        ipXmitAddr = self.usrp_ip if self.radio_type == 'usrp' else ''
        
        values = {
            'radio_type': self.radio_type,
            'ipXmitAddr': ipXmitAddr,
            'cf': self.cf_chooser.value(),
            'pulse_width': pulse_widths[self.pulse_combo.currentIndex()],
            'coherence': self.coherence_combo.currentIndex(),
            'mod_level': self.mod_slider.value() / 10.0,
            'rfPwr': self.pwr_slider.value(),
            'audio_file': self.audio_combo.currentData()  # Add audio file to values
        }
        return values

class ppmookLiveAudioXmitter(gr.top_block, Qt.QWidget):
    # What this window's own controls change that its dialog should
    # open on next time - see apps/utils.py: save_flowgraph_settings.
    SAVED_SETTINGS = {'power_level': 'rfPwr',
                      'center_freq': 'cf'}

    def __init__(self, config_values=None):
        gr.top_block.__init__(self, "PPM-OOK Live Audio Xmitter", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("PPM-OOK Live Audio Xmitter")
        apply_flowgraph_theme(self)
        try:
            self.setWindowIcon(Qt.QIcon.fromTheme('gnuradio-grc'))
        except BaseException as exc:
            print(f"Qt GUI: Could not set Icon: {str(exc)}", file=sys.stderr)
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

        self.settings = Qt.QSettings("GNU Radio", "ppmookLiveAudioXmitter")

        try:
            geometry = self.settings.value("geometry")
            if geometry:
                self.restoreGeometry(geometry)
        except BaseException as exc:
            print(f"Qt GUI: Could not restore geometry: {str(exc)}", file=sys.stderr)

        # Use provided config values or get them from dialog
        if config_values is None:
            config_dialog = ConfigDialog()
            if not config_dialog.exec_():
                sys.exit(0)
            values = config_dialog.get_values()
        else:
            values = config_values

        # Get configuration values
        radio_type = values.get('radio_type', 'hackrf')
        ipXmitAddr = values['ipXmitAddr']
        cf = values['cf']
        pulseWidth = values['pulse_width']
        coherence = values['coherence']
        modLevel = values['mod_level']
        audio_file = values.get('audio_file')
        rfPwr = values.get('rfPwr', -30)

        ##################################################
        # Variables
        ##################################################
        self.sps = sps = 500
        self.samp_rate = samp_rate = 20e6
        self.rfPwrDefault = rfPwrDefault = rfPwr
        self.pulseWidthDefault = pulseWidthDefault = pulseWidth
        self.modLevelDefault = modLevelDefault = modLevel
        self.coherenceDefault = coherenceDefault = coherence
        self.cf = cf = cf
        self.rfPwr = rfPwr = rfPwrDefault
        self.pulseWidth = pulseWidth = pulseWidth
        self.pulsePeriod = pulsePeriod = (sps/samp_rate*1e6)
        self.modLevel = modLevel = modLevel
        self.coherence = coherence = coherence
        self.centerFrequency = centerFrequency = cf
        self.radio_type = radio_type

        ##################################################
        # Blocks
        ##################################################

        # Create the options list
        self._pulseWidth_options = [10, 20, 40]
        # Create the labels list
        self._pulseWidth_labels = ['10 usec', '20 usec', '40 usec']
        # Create the combo box
        # Create the radio buttons
        self._pulseWidth_group_box = Qt.QGroupBox("Pulse Duration" + ": ")
        self._pulseWidth_box = Qt.QHBoxLayout()
        class variable_chooser_button_group(Qt.QButtonGroup):
            def __init__(self, parent=None):
                Qt.QButtonGroup.__init__(self, parent)
            @pyqtSlot(int)
            def updateButtonChecked(self, button_id):
                self.button(button_id).setChecked(True)
        self._pulseWidth_button_group = variable_chooser_button_group()
        self._pulseWidth_group_box.setLayout(self._pulseWidth_box)
        for i, _label in enumerate(self._pulseWidth_labels):
            radio_button = Qt.QRadioButton(_label)
            self._pulseWidth_box.addWidget(radio_button)
            self._pulseWidth_button_group.addButton(radio_button, i)
        self._pulseWidth_callback = lambda i: Qt.QMetaObject.invokeMethod(self._pulseWidth_button_group, "updateButtonChecked", Qt.Q_ARG("int", self._pulseWidth_options.index(i)))
        self._pulseWidth_callback(self.pulseWidth)
        self._pulseWidth_button_group.buttonClicked[int].connect(
            lambda i: self.set_pulseWidth(self._pulseWidth_options[i]))
        self.top_grid_layout.addWidget(self._pulseWidth_group_box, 1, 0, 1, 5)
        for r in range(1, 2):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 5):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._modLevel_range = qtgui.Range(0.1, 5, 0.1, modLevel, 200)  # Use modLevel directly here
        self._modLevel_win = qtgui.RangeWidget(self._modLevel_range, self.set_modLevel, "Modulation Level", "counter_slider", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._modLevel_win, 0, 5, 1, 5)
        for r in range(0, 1):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(5, 10):
            self.top_grid_layout.setColumnStretch(c, 1)
        # Create the options list
        self._coherence_options = [0, 1]
        # Create the labels list
        self._coherence_labels = ['Non-Coherent', 'Coherent']
        # Create the combo box
        # Create the radio buttons
        self._coherence_group_box = Qt.QGroupBox("Coherence On/Off" + ": ")
        self._coherence_box = Qt.QHBoxLayout()
        class variable_chooser_button_group(Qt.QButtonGroup):
            def __init__(self, parent=None):
                Qt.QButtonGroup.__init__(self, parent)
            @pyqtSlot(int)
            def updateButtonChecked(self, button_id):
                self.button(button_id).setChecked(True)
        self._coherence_button_group = variable_chooser_button_group()
        self._coherence_group_box.setLayout(self._coherence_box)
        for i, _label in enumerate(self._coherence_labels):
            radio_button = Qt.QRadioButton(_label)
            self._coherence_box.addWidget(radio_button)
            self._coherence_button_group.addButton(radio_button, i)
        self._coherence_callback = lambda i: Qt.QMetaObject.invokeMethod(self._coherence_button_group, "updateButtonChecked", Qt.Q_ARG("int", self._coherence_options.index(i)))
        self._coherence_callback(self.coherence)
        self._coherence_button_group.buttonClicked[int].connect(
            lambda i: self.set_coherence(self._coherence_options[i]))
        self.top_grid_layout.addWidget(self._coherence_group_box, 2, 0, 1, 5)
        for r in range(2, 3):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 5):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._rfPwr_range = qtgui.Range(0, 100, 1, rfPwrDefault, 200)
        self._rfPwr_win = qtgui.RangeWidget(self._rfPwr_range, self.set_rfPwr, "RF Output Power (%)", "counter_slider", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._rfPwr_win, 2, 5, 1, 5)
        for r in range(2, 3):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(5, 10):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._power_range = resolve_power_range(radio_type)
        if radio_type == 'vsg':
            from apps.vsg_sink import vsg_sink
            self.radio_sink = vsg_sink(
                center_freq=cf*1e6,
                sample_rate=samp_rate,
                level_dbm=scale_power(rfPwr, self._power_range))
        elif radio_type == 'usrp':
            self.radio_sink = uhd.usrp_sink(
                ",".join((f'addr={ipXmitAddr}', '')),
                uhd.stream_args(cpu_format="fc32", args='', channels=list(range(0,1))),
                "",
            )
            self.radio_sink.set_samp_rate(samp_rate)
            self.radio_sink.set_time_now(uhd.time_spec(time.time()), uhd.ALL_MBOARDS)
            self.radio_sink.set_center_freq(cf*1e6, 0)
            self.radio_sink.set_antenna("TX/RX", 0)
            self._power_range = resolve_power_range(radio_type, self.radio_sink)
            self.radio_sink.set_gain(scale_power(rfPwr, self._power_range), 0)
        else:
            self.radio_sink = soapy.sink('driver=hackrf', 'fc32', 1, '', '', [''], [''])
            self.radio_sink.set_sample_rate(0, samp_rate)
            self.radio_sink.set_frequency(0, cf*1e6)
            self.radio_sink.set_gain(0, 'VGA', scale_power(rfPwr, self._power_range))
            self.radio_sink.set_gain(0, 'AMP', 0)
        self.rational_resampler_xxx_0 = filter.rational_resampler_fff(
                interpolation=(int(samp_rate/sps/1000)),
                decimation=48,
                taps=[],
                fractional_bw=0)
        self.qtgui_time_sink_x_0 = qtgui.time_sink_f(
            5000, #size
            samp_rate, #samp_rate
            'Baseband Pulse Stream', #name
            1, #number of inputs
            None # parent
        )
        self.qtgui_time_sink_x_0.set_update_time(0.10)
        self.qtgui_time_sink_x_0.set_y_axis(-1.5, 1.5)

        self.qtgui_time_sink_x_0.set_y_label('Amplitude', "")

        self.qtgui_time_sink_x_0.enable_tags(True)
        self.qtgui_time_sink_x_0.set_trigger_mode(qtgui.TRIG_MODE_NORM, qtgui.TRIG_SLOPE_POS, 0.5, 0, 0, "")
        self.qtgui_time_sink_x_0.enable_autoscale(False)
        self.qtgui_time_sink_x_0.enable_grid(True)
        self.qtgui_time_sink_x_0.enable_axis_labels(True)
        self.qtgui_time_sink_x_0.enable_control_panel(False)
        self.qtgui_time_sink_x_0.enable_stem_plot(False)

        self.qtgui_time_sink_x_0.disable_legend()

        labels = ['Baseband Pulse Stream', 'Signal 2', 'Signal 3', 'Signal 4', 'Signal 5',
            'Signal 6', 'Signal 7', 'Signal 8', 'Signal 9', 'Signal 10']
        widths = [1, 1, 1, 1, 1,
            1, 1, 1, 1, 1]
        colors = ['black', 'red', 'green', 'black', 'cyan',
            'magenta', 'yellow', 'dark red', 'dark green', 'dark blue']
        alphas = [1.0, 1.0, 1.0, 1.0, 1.0,
            1.0, 1.0, 1.0, 1.0, 1.0]
        styles = [1, 1, 1, 1, 1,
            1, 1, 1, 1, 1]
        markers = [-1, -1, -1, -1, -1,
            -1, -1, -1, -1, -1]


        for i in range(1):
            if len(labels[i]) == 0:
                self.qtgui_time_sink_x_0.set_line_label(i, "Data {0}".format(i))
            else:
                self.qtgui_time_sink_x_0.set_line_label(i, labels[i])
            self.qtgui_time_sink_x_0.set_line_width(i, widths[i])
            self.qtgui_time_sink_x_0.set_line_color(i, colors[i])
            self.qtgui_time_sink_x_0.set_line_style(i, styles[i])
            self.qtgui_time_sink_x_0.set_line_marker(i, markers[i])
            self.qtgui_time_sink_x_0.set_line_alpha(i, alphas[i])

        self._qtgui_time_sink_x_0_win = sip.wrapinstance(self.qtgui_time_sink_x_0.qwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(self._qtgui_time_sink_x_0_win, 3, 0, 5, 10)
        for r in range(3, 8):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 10):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.qtgui_freq_sink_x_1 = qtgui.freq_sink_c(
            8192, #size
            window.WIN_BLACKMAN_hARRIS, #wintype
            (cf*1e6), #fc
            samp_rate, #bw
            'RF Spectrum', #name
            1,
            None # parent
        )
        self.qtgui_freq_sink_x_1.set_update_time(0.10)
        self.qtgui_freq_sink_x_1.set_y_axis(*SPECTRUM_Y_AXIS)
        self.qtgui_freq_sink_x_1.set_y_label('Relative Gain', 'dB')
        self.qtgui_freq_sink_x_1.set_trigger_mode(qtgui.TRIG_MODE_FREE, 0.0, 0, "")
        self.qtgui_freq_sink_x_1.enable_autoscale(False)
        self.qtgui_freq_sink_x_1.enable_grid(True)
        self.qtgui_freq_sink_x_1.set_fft_average(1.0)
        self.qtgui_freq_sink_x_1.enable_axis_labels(True)
        self.qtgui_freq_sink_x_1.enable_control_panel(False)
        self.qtgui_freq_sink_x_1.set_fft_window_normalized(False)

        self.qtgui_freq_sink_x_1.disable_legend()


        labels = ['', '', '', '', '',
            '', '', '', '', '']
        widths = [1, 1, 1, 1, 1,
            1, 1, 1, 1, 1]
        colors = ["black", "red", "green", "black", "cyan",
            "magenta", "yellow", "dark red", "dark green", "dark blue"]
        alphas = [1.0, 1.0, 1.0, 1.0, 1.0,
            1.0, 1.0, 1.0, 1.0, 1.0]

        for i in range(1):
            if len(labels[i]) == 0:
                self.qtgui_freq_sink_x_1.set_line_label(i, "Data {0}".format(i))
            else:
                self.qtgui_freq_sink_x_1.set_line_label(i, labels[i])
            self.qtgui_freq_sink_x_1.set_line_width(i, widths[i])
            self.qtgui_freq_sink_x_1.set_line_color(i, colors[i])
            self.qtgui_freq_sink_x_1.set_line_alpha(i, alphas[i])

        self._qtgui_freq_sink_x_1_win = sip.wrapinstance(self.qtgui_freq_sink_x_1.qwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(self._qtgui_freq_sink_x_1_win, 8, 0, 5, 10)
        for r in range(8, 13):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 10):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._pulsePeriod_tool_bar = Qt.QToolBar(self)

        if None:
            self._pulsePeriod_formatter = None
        else:
            self._pulsePeriod_formatter = lambda x: eng_notation.num_to_str(x)

        self._pulsePeriod_tool_bar.addWidget(Qt.QLabel("Pulse Period (usec): "))
        self._pulsePeriod_label = Qt.QLabel(str(self._pulsePeriod_formatter(self.pulsePeriod)))
        self._pulsePeriod_tool_bar.addWidget(self._pulsePeriod_label)
        self.top_grid_layout.addWidget(self._pulsePeriod_tool_bar, 1, 5, 1, 5)
        for r in range(1, 2):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(5, 10):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.filter_fft_low_pass_filter_0 = filter.fft_filter_fff(1, firdes.low_pass(modLevel, 48000, 4000, 1000, window.WIN_HAMMING, 6.76), 1) # type: ignore
        self.fft_filter_xxx_0_0 = filter.fft_filter_fff(1, [1,]*pulseWidth, 1)
        self.fft_filter_xxx_0_0.declare_sample_delay(0)
        self.fft_filter_xxx_0 = filter.fft_filter_fff(1, (1,)*sps, 1)
        self.fft_filter_xxx_0.declare_sample_delay(0)
        self.digital_glfsr_source_x_0 = digital.glfsr_source_f(31, True, 0b1001000000000000000000000000000, 1)
        self.digital_binary_slicer_fb_1 = digital.binary_slicer_fb()
        self.digital_binary_slicer_fb_0 = digital.binary_slicer_fb()
        self._centerFrequency_range = frequency_range(30, 2200, 0.01, cf, 200)  # Use cf directly here
        self._centerFrequency_win = qtgui.RangeWidget(self._centerFrequency_range, self.set_cf, "Center Frequency (MHz)", "counter", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._centerFrequency_win, 0, 0, 1, 5)
        for r in range(0, 1):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 5):
            self.top_grid_layout.setColumnStretch(c, 1)
        
        # The selected file, WAV or MP3, looping at 48 kHz
        self.audio_file = None
        if audio_file and os.path.exists(audio_file):
            self.audio_file = AudioFileSource(audio_file)
            self.blocks_wavfile_source_0 = self.audio_file.block
        else:
            # Create dummy source if no valid audio file
            self.blocks_wavfile_source_0 = blocks.null_source(gr.sizeof_float*1)

        self.blocks_vector_source_x_0_0 = blocks.vector_source_f((0,)*10+(1,)*(sps-10), True, 1, [])
        self.blocks_vector_source_x_0 = blocks.vector_source_f(np.arange(1,-1,-2/sps), True, 1, []) # type: ignore
        self.blocks_stream_mux_0 = blocks.stream_mux(gr.sizeof_float*1, (int(sps-1),1))
        self.blocks_selector_0 = blocks.selector(gr.sizeof_float*1,coherence,0)
        self.blocks_selector_0.set_enabled(True)
        self.blocks_repeat_0 = blocks.repeat(gr.sizeof_float*1, int(sps))
        self.blocks_null_source_0_0 = blocks.null_source(gr.sizeof_float*1)
        self.blocks_null_source_0 = blocks.null_source(gr.sizeof_float*1)
        self.blocks_multiply_xx_2 = blocks.multiply_vff(1)
        self.blocks_multiply_xx_1 = blocks.multiply_vff(1)
        self.blocks_multiply_xx_0 = blocks.multiply_vff(1)
        self.blocks_multiply_const_vxx_1 = blocks.multiply_const_cc(0.9)
        self.blocks_multiply_const_vxx_0 = blocks.multiply_const_ff((-1))
        self.blocks_float_to_complex_0 = blocks.float_to_complex(1)
        self.blocks_delay_1 = blocks.delay(gr.sizeof_float*1, 1)
        self.blocks_char_to_float_1 = blocks.char_to_float(1, 1)
        self.blocks_char_to_float_0 = blocks.char_to_float(1, 1)
        self.blocks_add_xx_0 = blocks.add_vff(1)
        self.blocks_add_const_vxx_0 = blocks.add_const_ff((-0.5))
        self.blocks_add_const_glfsr = blocks.add_const_ff(1.0)
        self.blocks_multiply_const_glfsr = blocks.multiply_const_ff(0.5)

        # Keep only these callback updates
        self._pulseWidth_callback(pulseWidth)  
        self._coherence_callback(coherence)  

        ##################################################
        # Connections
        ##################################################
        self.connect((self.blocks_add_const_vxx_0, 0), (self.blocks_delay_1, 0))
        self.connect((self.blocks_add_const_vxx_0, 0), (self.blocks_multiply_xx_0, 1))
        self.connect((self.blocks_add_xx_0, 0), (self.digital_binary_slicer_fb_1, 0))
        self.connect((self.blocks_char_to_float_0, 0), (self.blocks_add_const_vxx_0, 0))
        self.connect((self.blocks_char_to_float_1, 0), (self.blocks_multiply_xx_2, 0))
        self.connect((self.blocks_delay_1, 0), (self.blocks_multiply_xx_0, 0))
        self.connect((self.blocks_float_to_complex_0, 0), (self.blocks_multiply_const_vxx_1, 0))
        self.connect((self.blocks_float_to_complex_0, 0), (self.qtgui_freq_sink_x_1, 0))
        self.connect((self.blocks_multiply_const_vxx_0, 0), (self.digital_binary_slicer_fb_0, 0))
        self.connect((self.blocks_multiply_const_vxx_1, 0), (self.radio_sink, 0))
        self.connect((self.blocks_multiply_xx_0, 0), (self.blocks_multiply_const_vxx_0, 0))
        self.connect((self.blocks_multiply_xx_1, 0), (self.blocks_selector_0, 0))
        self.connect((self.blocks_multiply_xx_2, 0), (self.blocks_multiply_xx_1, 1))
        self.connect((self.blocks_multiply_xx_2, 0), (self.blocks_selector_0, 1))
        self.connect((self.blocks_null_source_0, 0), (self.blocks_stream_mux_0, 0))
        self.connect((self.blocks_null_source_0_0, 0), (self.blocks_float_to_complex_0, 1))
        self.connect((self.blocks_repeat_0, 0), (self.blocks_multiply_xx_1, 0))
        self.connect((self.blocks_selector_0, 0), (self.fft_filter_xxx_0_0, 0))
        self.connect((self.blocks_stream_mux_0, 0), (self.fft_filter_xxx_0, 0))
        self.connect((self.blocks_vector_source_x_0, 0), (self.blocks_add_xx_0, 0))
        self.connect((self.blocks_vector_source_x_0_0, 0), (self.blocks_multiply_xx_2, 1))
        self.connect((self.blocks_wavfile_source_0, 0), (self.filter_fft_low_pass_filter_0, 0))
        self.connect((self.digital_binary_slicer_fb_0, 0), (self.blocks_char_to_float_1, 0))
        self.connect((self.digital_binary_slicer_fb_1, 0), (self.blocks_char_to_float_0, 0))
        self.connect((self.digital_glfsr_source_x_0, 0), (self.blocks_add_const_glfsr, 0))
        self.connect((self.blocks_add_const_glfsr, 0), (self.blocks_multiply_const_glfsr, 0))
        self.connect((self.blocks_multiply_const_glfsr, 0), (self.blocks_repeat_0, 0))
        self.connect((self.fft_filter_xxx_0, 0), (self.blocks_add_xx_0, 1))
        self.connect((self.fft_filter_xxx_0_0, 0), (self.blocks_float_to_complex_0, 0))
        self.connect((self.fft_filter_xxx_0_0, 0), (self.qtgui_time_sink_x_0, 0))
        self.connect((self.filter_fft_low_pass_filter_0, 0), (self.rational_resampler_xxx_0, 0))
        self.connect((self.rational_resampler_xxx_0, 0), (self.blocks_stream_mux_0, 1))


    def closeEvent(self, event):
        self.settings = Qt.QSettings("GNU Radio", "ppmookLiveAudioXmitter")
        self.settings.setValue("geometry", self.saveGeometry())
        self.stop()
        self.wait()
        # An MP3's ffmpeg would otherwise sit on a full pipe until the
        # launcher exits - see apps/audio_file.py.
        if getattr(self, 'audio_file', None) is not None:
            self.audio_file.close()

        event.accept()

    def get_sps(self):
        return self.sps

    def set_sps(self, sps):
        self.sps = sps
        self.set_pulsePeriod((self.sps/self.samp_rate*1e6))
        self.blocks_repeat_0.set_interpolation(int(self.sps))
        self.blocks_vector_source_x_0.set_data(np.arange(1,-1,-2/self.sps), []) # type: ignore
        self.blocks_vector_source_x_0_0.set_data((0,)*10+(1,)*(self.sps-10), [])
        self.fft_filter_xxx_0.set_taps((1,)*self.sps)

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.set_pulsePeriod((self.sps/self.samp_rate*1e6))
        self.qtgui_freq_sink_x_1.set_frequency_range((self.cf*1e6), self.samp_rate)
        self.qtgui_time_sink_x_0.set_samp_rate(self.samp_rate)
        if self.radio_type == 'usrp':
            self.radio_sink.set_samp_rate(self.samp_rate)
        else:
            self.radio_sink.set_sample_rate(0, self.samp_rate)

    def get_pulseWidthDefault(self):
        return self.pulseWidthDefault

    def set_pulseWidthDefault(self, pulseWidthDefault):
        self.pulseWidthDefault = pulseWidthDefault
        self.set_pulseWidth(self.pulseWidthDefault)

    def get_modLevelDefault(self):
        return self.modLevelDefault

    def set_modLevelDefault(self, modLevelDefault):
        self.modLevelDefault = modLevelDefault
        self.set_modLevel(self.modLevelDefault)

    def get_coherenceDefault(self):
        return self.coherenceDefault

    def set_coherenceDefault(self, coherenceDefault):
        self.coherenceDefault = coherenceDefault
        self.set_coherence(self.coherenceDefault)

    def get_cf(self):
        return self.cf

    def set_cf(self, cf):
        self.cf = cf
        self.set_centerFrequency(self.cf)
        self.qtgui_freq_sink_x_1.set_frequency_range((self.cf*1e6), self.samp_rate)
        if self.radio_type == 'usrp':
            self.radio_sink.set_center_freq(self.cf*1e6, 0)
        else:
            self.radio_sink.set_frequency(0, self.cf*1e6)

    def get_pulseWidth(self):
        return self.pulseWidth

    def set_pulseWidth(self, pulseWidth):
        self.pulseWidth = pulseWidth
        self._pulseWidth_callback(self.pulseWidth)
        self.fft_filter_xxx_0_0.set_taps([1,]*self.pulseWidth)

    def get_pulsePeriod(self):
        return self.pulsePeriod

    def set_pulsePeriod(self, pulsePeriod):
        self.pulsePeriod = pulsePeriod
        Qt.QMetaObject.invokeMethod(self._pulsePeriod_label, "setText", Qt.Q_ARG("QString", str(self._pulsePeriod_formatter(self.pulsePeriod))))

    def get_modLevel(self):
        return self.modLevel

    def set_modLevel(self, modLevel):
        self.modLevel = modLevel
        self.filter_fft_low_pass_filter_0.set_taps(firdes.low_pass(self.modLevel, 48000, 4000, 1000, window.WIN_HAMMING, 6.76)) # type: ignore

    def get_coherence(self):
        return self.coherence

    def set_coherence(self, coherence):
        self.coherence = coherence
        self._coherence_callback(self.coherence)
        self.blocks_selector_0.set_input_index(self.coherence)

    def get_rfPwrDefault(self):
        return self.rfPwrDefault

    def set_rfPwrDefault(self, rfPwrDefault):
        self.rfPwrDefault = rfPwrDefault
        self.set_rfPwr(self.rfPwrDefault)

    def get_rfPwr(self):
        return self.rfPwr

    def set_rfPwr(self, rfPwr):
        self.rfPwr = rfPwr
        self.blocks_multiply_const_vxx_1.set_k(0.9)
        if self.radio_type == 'vsg':
            self.radio_sink.set_level(scale_power(self.rfPwr, self._power_range))
        elif self.radio_type == 'usrp':
            self.radio_sink.set_gain(scale_power(self.rfPwr, self._power_range), 0)
        else:
            self.radio_sink.set_gain(0, 'VGA', scale_power(self.rfPwr, self._power_range))

    def get_centerFrequency(self):
        return self.centerFrequency

    def set_centerFrequency(self, centerFrequency):
        self.centerFrequency = centerFrequency




def main(top_block_cls=ppmookLiveAudioXmitter, options=None, app=None, config_values=None):
    if app is None:
        if StrictVersion("4.5.0") <= StrictVersion(Qt.qVersion()) < StrictVersion("5.0.0"):
            style = gr.prefs().get_string('qtgui', 'style', 'raster')
            Qt.QApplication.setGraphicsSystem(style)
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

    if app.instance():
        return tb
    else:
        return app.exec_()

if __name__ == '__main__':
    main()
