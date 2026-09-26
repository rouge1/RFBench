#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: Subcarrier Transmitter with Recorded Audio
# Author: Gary Schafer
# GNU Radio version: 3.10.10.0

# Standard library imports
import json
import os
import signal
import sip  # type: ignore
import sys
import time
from math import pi  # Add this import

# Third party imports
import numpy as np # type: ignore  
from gnuradio import analog  # type: ignore
from gnuradio import blocks  # type: ignore
from gnuradio import filter  # type: ignore
from gnuradio.filter import firdes # type: ignore  
from gnuradio import gr  # type: ignore
from gnuradio import qtgui  # type: ignore
from gnuradio import soapy, uhd  # type: ignore
from gnuradio.fft import window  # type: ignore
from gnuradio.qtgui import Range, RangeWidget  # type: ignore
from packaging.version import Version as StrictVersion  # type: ignore
from PyQt5 import Qt  # type: ignore
from PyQt5 import QtCore  # type: ignore
from PyQt5.QtCore import pyqtSlot  # type: ignore

# Local imports
from apps.audio_file import AudioFileSource
from apps.media import AUDIO, choices
from apps.utils import (CONFIG_DIR, apply_dark_theme, apply_flowgraph_theme,
                        radio_label,
                        read_settings, update_app_config, power_percent,
                        resolve_power_range, scale_power, SPECTRUM_Y_AXIS, adopt_legacy_config,
                        FrequencyChooser, frequency_range)


class ConfigDialog(Qt.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FM Subcarrier Generator Configuration")
        self.layout = Qt.QVBoxLayout(self)
        self.config_dir = CONFIG_DIR
        self.config_file = os.path.join(self.config_dir, "subcarrierRecordedAudio_config.json")
        # Older builds saved this dialog under subcarrierRecorded_config.json;
        # fold that in once so nothing the user set is lost.
        adopt_legacy_config(self.config_dir, "subcarrierRecorded_config.json", self.config_file)
        
        settings = read_settings()
        self.usrp_ip = settings.get('usrp_ip', '')
        self.radio_type = settings.get('radio_type', 'hackrf')

        # Create button box FIRST before other controls that might need it
        self.button_box = Qt.QDialogButtonBox(
            Qt.QDialogButtonBox.Ok | Qt.QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        # Then create controls
        self.create_radio_label()
        self.create_frequency_control()
        self.create_power_control()
        self.create_audio_source_control()
        self.create_subcarrier_controls()
        self.create_noise_controls()
        
        # Add button box last
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
        # Not a whole-MHz slider, as it once was: the window tunes to two
        # decimals like every other app's, and what it was left at comes
        # back here (SAVED_SETTINGS) - see FrequencyChooser.
        self.cf_chooser = FrequencyChooser(minimum=50.0, maximum=2200.0,
                                           value=315.0)
        self.layout.addWidget(self.cf_chooser)

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

    def create_audio_source_control(self):
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

    def create_subcarrier_controls(self):
        # Subcarrier Modulation
        self.submod_combo = Qt.QComboBox()
        self.submod_combo.addItems(['FM', 'DSB', 'LSB', 'USB'])
        self.layout.addWidget(Qt.QLabel("Subcarrier Modulation:"))
        self.layout.addWidget(self.submod_combo)
        
        # Subcarrier Frequency with value label
        self.scfreq_layout = Qt.QHBoxLayout()
        self.scfreq_slider = Qt.QSlider(QtCore.Qt.Horizontal)
        self.scfreq_slider.setMinimum(10)
        self.scfreq_slider.setMaximum(100)
        self.scfreq_slider.setValue(20)
        self.scfreq_label = Qt.QLabel("Subcarrier Frequency: 20 kHz")
        self.scfreq_slider.valueChanged.connect(
            lambda v: self.scfreq_label.setText(f"Subcarrier Frequency: {v} kHz"))
        self.scfreq_layout.addWidget(self.scfreq_label)
        self.scfreq_layout.addWidget(self.scfreq_slider)
        self.layout.addLayout(self.scfreq_layout)
        
        # Carrier Condition - Modified to use radio buttons
        self.carrier_group = Qt.QGroupBox("Carrier Condition:")
        self.carrier_layout = Qt.QHBoxLayout()
        self.carrier_group.setLayout(self.carrier_layout)
        
        self.carrier_off_radio = Qt.QRadioButton("Carrier Off (Suppressed)")
        self.carrier_on_radio = Qt.QRadioButton("Carrier On (Full)")
        self.carrier_off_radio.setChecked(True)  # Default to off
        
        self.carrier_layout.addWidget(self.carrier_off_radio)
        self.carrier_layout.addWidget(self.carrier_on_radio)
        self.layout.addWidget(self.carrier_group)

    def create_noise_controls(self):
        # Noise On/Off
        self.noise_combo = Qt.QComboBox()
        self.noise_combo.addItems(['Noise Off', 'Noise On'])
        self.layout.addWidget(Qt.QLabel("Noise:"))
        self.layout.addWidget(self.noise_combo)
        
        # Noise Frequency with value label
        self.noisefreq_layout = Qt.QHBoxLayout()
        self.noisefreq_slider = Qt.QSlider(QtCore.Qt.Horizontal)
        self.noisefreq_slider.setMinimum(200)
        self.noisefreq_slider.setMaximum(2000)
        self.noisefreq_slider.setValue(1000)
        self.noisefreq_label = Qt.QLabel("Noise Frequency: 1000 Hz")
        self.noisefreq_slider.valueChanged.connect(
            lambda v: self.noisefreq_label.setText(f"Noise Frequency: {v} Hz"))
        self.noisefreq_layout.addWidget(self.noisefreq_label)
        self.noisefreq_layout.addWidget(self.noisefreq_slider)
        self.layout.addLayout(self.noisefreq_layout)

    def load_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                self.cf_chooser.setValue(config.get('center_freq', 315))
                self.pwr_slider.setValue(power_percent(config.get('power_level'), 50))
                self.submod_combo.setCurrentIndex(config.get('submod', 0))
                self.scfreq_slider.setValue(config.get('scfreq', 20))
                
                # Modified this part for carrier radio buttons
                if config.get('carrier', 0) == 1:
                    self.carrier_on_radio.setChecked(True)
                else:
                    self.carrier_off_radio.setChecked(True)
                    
                self.noise_combo.setCurrentIndex(config.get('noise_on', 0))
                self.noisefreq_slider.setValue(config.get('noise_freq', 1000))
                
                saved_audio = config.get('audio_file', '')
                if saved_audio:
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
            'power_level': self.pwr_slider.value(),
            'submod': self.submod_combo.currentIndex(),
            'scfreq': self.scfreq_slider.value(),
            'carrier': 1 if self.carrier_on_radio.isChecked() else 0,  # Modified this line
            'noise_on': self.noise_combo.currentIndex(),
            'noise_freq': self.noisefreq_slider.value(),
            'audio_file': self.audio_combo.currentData()
        }
        
        update_app_config(self.config_file, config)

    def accept(self):
        self.save_config()
        super().accept()

    def get_values(self):
        ipXmitAddr = self.usrp_ip if self.radio_type == 'usrp' else ''
        
        values = {
            'radio_type': self.radio_type,
            'ipXmitAddr': ipXmitAddr,
            'cf': self.cf_chooser.value(),
            'rfPwr': self.pwr_slider.value(),
            'audio_file': self.audio_combo.currentData(),
            'submod': self.submod_combo.currentIndex(),
            'scfreq': self.scfreq_slider.value(),
            'carrier': 1 if self.carrier_on_radio.isChecked() else 0,  # Modified
            'noise_on': self.noise_combo.currentIndex(),
            'noise_freq': self.noisefreq_slider.value()
        }
        return values

class subcarrierRecordedAudio(gr.top_block, Qt.QWidget):
    # What this window's own controls change that its dialog should
    # open on next time - see apps/utils.py: save_flowgraph_settings.
    SAVED_SETTINGS = {'power_level': 'rfPwr',
                      'center_freq': 'cf'}

    def __init__(self, config_values=None):
        if config_values is None:
            config_dialog = ConfigDialog()
            if not config_dialog.exec_():
                sys.exit(0)
            values = config_dialog.get_values()
        else:
            values = config_values
            
        gr.top_block.__init__(self, "FM Subcarrier Generator", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("FM Subcarrier Generator")
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

        self.settings = Qt.QSettings("GNU Radio", "subcarrierRecordedAudio")

        try:
            geometry = self.settings.value("geometry")
            if geometry:
                self.restoreGeometry(geometry)
        except BaseException as exc:
            print(f"Qt GUI: Could not restore geometry: {str(exc)}", file=sys.stderr)

        ##################################################
        # Variables
        ##################################################
        self.subModDefault = subModDefault = 0
        self.subCarrierDefault = subCarrierDefault = 0
        self.scFreqDefault = scFreqDefault = 20
        self.noiseMaskDefault = noiseMaskDefault = 1
        self.noiseFreqDefault = noiseFreqDefault = 1000
        self.radio_type = radio_type = values.get('radio_type', 'hackrf')
        self.cf = cf = values['cf']
        self.rfPwr = rfPwr = values.get('rfPwr', -50)
        self.usrpXmitIp = usrpXmitIp = values['ipXmitAddr']
        self.subMod = subMod = values['submod']
        self.scFreq = scFreq = values['scfreq']
        self.scCarrier = scCarrier = values['carrier']
        self.samp_rate = samp_rate = 2e6
        self.noiseOnOff = noiseOnOff = values['noise_on']
        self.noiseFreq = noiseFreq = values['noise_freq']

        # Center frequency control - move to first row
        self._cf_range = frequency_range(50, 2200, 0.01, values['cf'], 200)
        self._cf_win = qtgui.RangeWidget(self._cf_range, self.set_cf, 
            "Center Frequency (MHz)", "counter_slider", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._cf_win, 0, 0, 1, 10)
        
        # Subcarrier frequency control - move to second row
        self._scFreq_range = qtgui.Range(10, 100, 1, scFreqDefault, 200)
        self._scFreq_win = qtgui.RangeWidget(self._scFreq_range, self.set_scFreq, 
            "Subcarrier Frequency (kHz)", "counter_slider", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._scFreq_win, 1, 0, 1, 10)

        ##################################################
        # Blocks
        ##################################################

        # Create the options list
        self._subMod_options = [0, 1, 2, 3]
        # Create the labels list
        self._subMod_labels = ['FM', 'DSB', 'LSB', 'USB']
        # Create the combo box
        # Create the radio buttons
        self._subMod_group_box = Qt.QGroupBox("Subcarrier Modulation" + ": ")
        self._subMod_box = Qt.QHBoxLayout()
        class variable_chooser_button_group(Qt.QButtonGroup):
            def __init__(self, parent=None):
                Qt.QButtonGroup.__init__(self, parent)
            @pyqtSlot(int)
            def updateButtonChecked(self, button_id):
                self.button(button_id).setChecked(True)
        self._subMod_button_group = variable_chooser_button_group()
        self._subMod_group_box.setLayout(self._subMod_box)
        for i, _label in enumerate(self._subMod_labels):
            radio_button = Qt.QRadioButton(_label)
            self._subMod_box.addWidget(radio_button)
            self._subMod_button_group.addButton(radio_button, i)
        self._subMod_callback = lambda i: Qt.QMetaObject.invokeMethod(self._subMod_button_group, "updateButtonChecked", Qt.Q_ARG("int", self._subMod_options.index(i)))
        self._subMod_callback(self.subMod)
        self._subMod_button_group.buttonClicked[int].connect(
            lambda i: self.set_subMod(self._subMod_options[i]))
        self.top_grid_layout.addWidget(self._subMod_group_box, 2, 0, 1, 5) 

        # Create the options list for carrier condition
        self._scCarrier_options = [0, 1]
        self._scCarrier_labels = ['Carrier Off (Suppressed)', 'Carrier On (Full)']
        # Create the radio buttons
        self._scCarrier_group_box = Qt.QGroupBox("Subcarrier Carrier Condition (for AM)" + ": ")
        self._scCarrier_box = Qt.QHBoxLayout()
        class variable_chooser_button_group(Qt.QButtonGroup):
            def __init__(self, parent=None):
                Qt.QButtonGroup.__init__(self, parent)
            @pyqtSlot(int)
            def updateButtonChecked(self, button_id):
                self.button(button_id).setChecked(True)
        self._scCarrier_button_group = variable_chooser_button_group()
        self._scCarrier_group_box.setLayout(self._scCarrier_box)
        for i, _label in enumerate(self._scCarrier_labels):
            radio_button = Qt.QRadioButton(_label)
            self._scCarrier_box.addWidget(radio_button)
            self._scCarrier_button_group.addButton(radio_button, i)
        self._scCarrier_callback = lambda i: Qt.QMetaObject.invokeMethod(self._scCarrier_button_group, "updateButtonChecked", Qt.Q_ARG("int", self._scCarrier_options.index(i)))
        self._scCarrier_callback(self.scCarrier)
        self._scCarrier_button_group.buttonClicked[int].connect(
            lambda i: self.set_scCarrier(self._scCarrier_options[i]))
        self.top_grid_layout.addWidget(self._scCarrier_group_box, 2, 5, 1, 5)
        
      

        # Create the options list
        self._noiseOnOff_options = [0, 1]
        # Create the labels list
        self._noiseOnOff_labels = ['Noise Off', 'Noise On']
        # Create the combo box
        # Create the radio buttons
        self._noiseOnOff_group_box = Qt.QGroupBox("Noise On/Off" + ": ")
        self._noiseOnOff_box = Qt.QHBoxLayout()
        class variable_chooser_button_group(Qt.QButtonGroup):
            def __init__(self, parent=None):
                Qt.QButtonGroup.__init__(self, parent)
            @pyqtSlot(int)
            def updateButtonChecked(self, button_id):
                self.button(button_id).setChecked(True)
        self._noiseOnOff_button_group = variable_chooser_button_group()
        self._noiseOnOff_group_box.setLayout(self._noiseOnOff_box)
        for i, _label in enumerate(self._noiseOnOff_labels):
            radio_button = Qt.QRadioButton(_label)
            self._noiseOnOff_box.addWidget(radio_button)
            self._noiseOnOff_button_group.addButton(radio_button, i)
        self._noiseOnOff_callback = lambda i: Qt.QMetaObject.invokeMethod(self._noiseOnOff_button_group, "updateButtonChecked", Qt.Q_ARG("int", self._noiseOnOff_options.index(i)))
        self._noiseOnOff_callback(self.noiseOnOff)
        self._noiseOnOff_button_group.buttonClicked[int].connect(
            lambda i: self.set_noiseOnOff(self._noiseOnOff_options[i]))
        self.top_grid_layout.addWidget(self._noiseOnOff_group_box, 3, 0, 1, 5)
        for r in range(3, 4):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 5):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._noiseFreq_range = qtgui.Range(200, 2000, 100, noiseFreqDefault, 200)
        self._noiseFreq_win = qtgui.RangeWidget(self._noiseFreq_range, self.set_noiseFreq, "Noise Max Frequency (Hz)", "counter_slider", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._noiseFreq_win, 3, 5, 1, 5)
        for r in range(3, 4):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(5, 10):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._rfPwr_range = qtgui.Range(0, 100, 1, rfPwr, 200)
        self._rfPwr_win = qtgui.RangeWidget(self._rfPwr_range, self.set_rfPwr, "RF Output Power (%)", "counter_slider", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._rfPwr_win, 4, 0, 1, 10)
        self._power_range = resolve_power_range(radio_type)
        if radio_type == 'vsg':
            from apps.vsg_sink import vsg_sink
            self.radio_sink = vsg_sink(
                center_freq=cf*1e6,
                sample_rate=samp_rate,
                level_dbm=scale_power(rfPwr, self._power_range))
        elif radio_type == 'usrp':
            self.radio_sink = uhd.usrp_sink(
                ",".join(("addr="+usrpXmitIp, '')),
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
        self.rational_resampler_xxx_1 = filter.rational_resampler_fff(
                interpolation=25,
                decimation=3,
                taps=[],
                fractional_bw=0)
        self.rational_resampler_xxx_0_0 = filter.rational_resampler_fff(
                interpolation=5,
                decimation=1,
                taps=[],
                fractional_bw=0)
        self.rational_resampler_xxx_0 = filter.rational_resampler_ccc(
                interpolation=5,
                decimation=1,
                taps=[],
                fractional_bw=0)
        self.qtgui_freq_sink_x_1 = qtgui.freq_sink_c(
            4096, #size
            window.WIN_BLACKMAN_hARRIS, #wintype
            (cf*1e6), #fc
            samp_rate, #bw
            'RF Spectrum', #name
            1,
            None # parent
        )
        self.qtgui_freq_sink_x_1.set_update_time(0.01)
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
        alphas = [1.0, 1.0, 1.0, 1.0, 1.0]

        for i in range(1):
            if len(labels[i]) == 0:
                self.qtgui_freq_sink_x_1.set_line_label(i, "Data {0}".format(i))
            else:
                self.qtgui_freq_sink_x_1.set_line_label(i, labels[i])
            self.qtgui_freq_sink_x_1.set_line_width(i, widths[i])
            self.qtgui_freq_sink_x_1.set_line_color(i, colors[i])
            self.qtgui_freq_sink_x_1.set_line_alpha(i, alphas[i])

        self._qtgui_freq_sink_x_1_win = sip.wrapinstance(self.qtgui_freq_sink_x_1.qwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(self._qtgui_freq_sink_x_1_win, 5, 5, 10, 5)
        for r in range(5, 15):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(5, 10):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.qtgui_freq_sink_x_0 = qtgui.freq_sink_f(
            4096, #size
            window.WIN_BLACKMAN_hARRIS, #wintype
            0, #fc
            240e3, #bw
            'Baseband Spectrum', #name
            1,
            None # parent
        )
        self.qtgui_freq_sink_x_0.set_update_time(0.01)
        self.qtgui_freq_sink_x_0.set_y_axis(*SPECTRUM_Y_AXIS)
        self.qtgui_freq_sink_x_0.set_y_label('Relative Gain', 'dB')
        self.qtgui_freq_sink_x_0.set_trigger_mode(qtgui.TRIG_MODE_FREE, 0.0, 0, "")
        self.qtgui_freq_sink_x_0.enable_autoscale(True)
        self.qtgui_freq_sink_x_0.enable_grid(True)
        self.qtgui_freq_sink_x_0.set_fft_average(1.0)
        self.qtgui_freq_sink_x_0.enable_axis_labels(True)
        self.qtgui_freq_sink_x_0.enable_control_panel(False)
        self.qtgui_freq_sink_x_0.set_fft_window_normalized(False)

        self.qtgui_freq_sink_x_0.disable_legend()

        self.qtgui_freq_sink_x_0.set_plot_pos_half(not False)

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
                self.qtgui_freq_sink_x_0.set_line_label(i, "Data {0}".format(i))
            else:
                self.qtgui_freq_sink_x_0.set_line_label(i, labels[i])
            self.qtgui_freq_sink_x_0.set_line_width(i, widths[i])
            self.qtgui_freq_sink_x_0.set_line_color(i, colors[i])
            self.qtgui_freq_sink_x_0.set_line_alpha(i, alphas[i])

        self._qtgui_freq_sink_x_0_win = sip.wrapinstance(self.qtgui_freq_sink_x_0.qwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(self._qtgui_freq_sink_x_0_win, 5, 0, 10, 5)
        for r in range(5, 15):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 5):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.hilbert_fc_0 = filter.hilbert_fc(200, window.WIN_HAMMING, 6.76)
        self.filter_fft_low_pass_filter_0 = filter.fft_filter_fff(1, firdes.low_pass(1, 48000, noiseFreq, 200, window.WIN_HAMMING, 6.76), 1)
        self.fft_filter_xxx_0 = filter.fft_filter_fff(1, firdes.low_pass(1,48000,3500,500), 1)
        self.fft_filter_xxx_0.declare_sample_delay(0)
        # WAV or MP3, looping at 48 kHz - see apps/audio_file.py.
        audio_path = values.get('audio_file')
        self.audio_file = None
        if audio_path and os.path.exists(audio_path):
            self.audio_file = AudioFileSource(audio_path)
            self.blocks_wavfile_source_0 = self.audio_file.block
        else:
            # Create dummy source if no valid audio file
            self.blocks_wavfile_source_0 = blocks.null_source(gr.sizeof_float*1)
        self.blocks_selector_0 = blocks.selector(gr.sizeof_gr_complex*1,subMod,0)
        self.blocks_selector_0.set_enabled(True)
        self.blocks_multiply_xx_0 = blocks.multiply_vcc(1)
        self.blocks_multiply_const_vxx_0 = blocks.multiply_const_ff(noiseOnOff)
        self.blocks_float_to_complex_0 = blocks.float_to_complex(1)
        self.blocks_conjugate_cc_0 = blocks.conjugate_cc()
        self.blocks_complex_to_real_0 = blocks.complex_to_real(1)
        self.blocks_add_xx_0 = blocks.add_vff(1)
        self.blocks_add_const_vxx_0_0 = blocks.add_const_ff(scCarrier)
        self.blocks_add_const_vxx_0 = blocks.add_const_ff(scCarrier)
        self.analog_sig_source_x_0 = analog.sig_source_c(240e3, analog.GR_COS_WAVE, (scFreq*1e3), 0.5, 0, 0)
        self.analog_noise_source_x_0 = analog.noise_source_f(analog.GR_GAUSSIAN, 1, 0)
        self.analog_frequency_modulator_fc_1 = analog.frequency_modulator_fc((2*pi*scFreq*2500/samp_rate))
        self.analog_frequency_modulator_fc_0 = analog.frequency_modulator_fc((2*pi*10e3/48000))


        ##################################################
        # Connections
        ##################################################
        self.connect((self.analog_frequency_modulator_fc_0, 0), (self.blocks_selector_0, 0))
        self.connect((self.analog_frequency_modulator_fc_1, 0), (self.qtgui_freq_sink_x_1, 0))
        self.connect((self.analog_frequency_modulator_fc_1, 0), (self.radio_sink, 0))
        self.connect((self.analog_noise_source_x_0, 0), (self.filter_fft_low_pass_filter_0, 0))
        self.connect((self.analog_sig_source_x_0, 0), (self.blocks_multiply_xx_0, 1))
        self.connect((self.blocks_add_const_vxx_0, 0), (self.blocks_float_to_complex_0, 0))
        self.connect((self.blocks_add_const_vxx_0_0, 0), (self.hilbert_fc_0, 0))
        self.connect((self.blocks_add_xx_0, 0), (self.qtgui_freq_sink_x_0, 0))
        self.connect((self.blocks_add_xx_0, 0), (self.rational_resampler_xxx_1, 0))
        self.connect((self.blocks_complex_to_real_0, 0), (self.blocks_add_xx_0, 1))
        self.connect((self.blocks_conjugate_cc_0, 0), (self.blocks_selector_0, 2))
        self.connect((self.blocks_float_to_complex_0, 0), (self.blocks_selector_0, 1))
        self.connect((self.blocks_multiply_const_vxx_0, 0), (self.blocks_add_xx_0, 0))
        self.connect((self.blocks_multiply_xx_0, 0), (self.blocks_complex_to_real_0, 0))
        self.connect((self.blocks_selector_0, 0), (self.rational_resampler_xxx_0, 0))
        self.connect((self.blocks_wavfile_source_0, 0), (self.fft_filter_xxx_0, 0))
        self.connect((self.fft_filter_xxx_0, 0), (self.analog_frequency_modulator_fc_0, 0))
        self.connect((self.fft_filter_xxx_0, 0), (self.blocks_add_const_vxx_0, 0))
        self.connect((self.fft_filter_xxx_0, 0), (self.blocks_add_const_vxx_0_0, 0))
        self.connect((self.filter_fft_low_pass_filter_0, 0), (self.rational_resampler_xxx_0_0, 0))
        self.connect((self.hilbert_fc_0, 0), (self.blocks_conjugate_cc_0, 0))
        self.connect((self.hilbert_fc_0, 0), (self.blocks_selector_0, 3))
        self.connect((self.rational_resampler_xxx_0, 0), (self.blocks_multiply_xx_0, 0))
        self.connect((self.rational_resampler_xxx_0_0, 0), (self.blocks_multiply_const_vxx_0, 0))
        self.connect((self.rational_resampler_xxx_1, 0), (self.analog_frequency_modulator_fc_1, 0))


    def closeEvent(self, event):
        self.settings = Qt.QSettings("GNU Radio", "subcarrierRecordedAudio")
        self.settings.setValue("geometry", self.saveGeometry())
        self.stop()
        self.wait()
        # An MP3's ffmpeg would otherwise sit on a full pipe until the
        # launcher exits - see apps/audio_file.py.
        if getattr(self, 'audio_file', None) is not None:
            self.audio_file.close()

        event.accept()

    def get_subModDefault(self):
        return self.subModDefault

    def set_subModDefault(self, subModDefault):
        self.subModDefault = subModDefault
        self.set_subMod(int(self.subModDefault))

    def get_subCarrierDefault(self):
        return self.subCarrierDefault

    def set_subCarrierDefault(self, subCarrierDefault):
        self.subCarrierDefault = subCarrierDefault
        self.set_scCarrier(self.subCarrierDefault)

    def get_scFreqDefault(self):
        return self.scFreqDefault

    def set_scFreqDefault(self, scFreqDefault):
        self.scFreqDefault = scFreqDefault
        self.set_scFreq(self.scFreqDefault)

    def get_noiseMaskDefault(self):
        return self.noiseMaskDefault

    def set_noiseMaskDefault(self, noiseMaskDefault):
        self.noiseMaskDefault = noiseMaskDefault
        self.set_noiseOnOff(self.noiseMaskDefault)

    def get_noiseFreqDefault(self):
        return self.noiseFreqDefault

    def set_noiseFreqDefault(self, noiseFreqDefault):
        self.noiseFreqDefault = noiseFreqDefault
        self.set_noiseFreq(self.noiseFreqDefault)

    def get_cf(self):
        return self.cf

    def set_cf(self, cf):
        self.cf = cf
        self.qtgui_freq_sink_x_1.set_frequency_range((self.cf*1e6), self.samp_rate)
        if self.radio_type == 'usrp':
            self.radio_sink.set_center_freq(self.cf*1e6, 0)
        else:
            self.radio_sink.set_frequency(0, self.cf*1e6)

    def get_usrpXmitIp(self):
        return self.usrpXmitIp

    def set_usrpXmitIp(self, usrpXmitIp):
        self.usrpXmitIp = usrpXmitIp

    def get_subMod(self):
        return self.subMod

    def set_subMod(self, subMod):
        self.subMod = subMod
        self._subMod_callback(self.subMod)
        self.blocks_selector_0.set_input_index(self.subMod)

    def get_scFreq(self):
        return self.scFreq

    def set_scFreq(self, scFreq):
        self.scFreq = scFreq
        self.analog_frequency_modulator_fc_1.set_sensitivity((2*pi*self.scFreq*2500/self.samp_rate))
        self.analog_sig_source_x_0.set_frequency((self.scFreq*1e3))

    def get_scCarrier(self):
        return self.scCarrier

    def set_scCarrier(self, scCarrier):
        self.scCarrier = scCarrier
        self._scCarrier_callback(self.scCarrier)
        self.blocks_add_const_vxx_0.set_k(self.scCarrier)
        self.blocks_add_const_vxx_0_0.set_k(self.scCarrier)

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.analog_frequency_modulator_fc_1.set_sensitivity((2*pi*self.scFreq*2500/self.samp_rate))
        self.qtgui_freq_sink_x_1.set_frequency_range((self.cf*1e6), self.samp_rate)
        if self.radio_type == 'usrp':
            self.radio_sink.set_samp_rate(self.samp_rate)
        else:
            self.radio_sink.set_sample_rate(0, self.samp_rate)

    def get_noiseOnOff(self):
        return self.noiseOnOff

    def set_noiseOnOff(self, noiseOnOff):
        self.noiseOnOff = noiseOnOff
        self._noiseOnOff_callback(self.noiseOnOff)
        self.blocks_multiply_const_vxx_0.set_k(self.noiseOnOff)

    def get_rfPwr(self):
        return self.rfPwr

    def set_rfPwr(self, rfPwr):
        self.rfPwr = rfPwr
        if self.radio_type == 'vsg':
            self.radio_sink.set_level(scale_power(self.rfPwr, self._power_range))
        elif self.radio_type == 'usrp':
            self.radio_sink.set_gain(scale_power(self.rfPwr, self._power_range), 0)
        else:
            self.radio_sink.set_gain(0, 'VGA', scale_power(self.rfPwr, self._power_range))

    def get_noiseFreq(self):
        return self.noiseFreq

    def set_noiseFreq(self, noiseFreq):
        self.noiseFreq = noiseFreq
        self.filter_fft_low_pass_filter_0.set_taps(firdes.low_pass(1, 48000, self.noiseFreq, 200, window.WIN_HAMMING, 6.76))




def main(top_block_cls=subcarrierRecordedAudio, options=None, app=None, config_values=None):
    if app is None:
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
