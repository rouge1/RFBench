#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: AM Audio Signal Generator from Internal Audio Card
# Author: Gary Schafer
# GNU Radio version: 3.10.1.1

from packaging.version import Version as StrictVersion # type: ignore

if __name__ == '__main__':
    import ctypes
    import sys
    if sys.platform.startswith('linux'):
        try:
            x11 = ctypes.cdll.LoadLibrary('libX11.so')
            x11.XInitThreads()
        except:
            print("Warning: failed to XInitThreads()")

# Standard library imports
import json
import os
import signal
import sip # type: ignore
import sys
import time

# Third party imports
from gnuradio import blocks  # type: ignore
from gnuradio import filter # type: ignore
from gnuradio import gr # type: ignore
from gnuradio import qtgui # type: ignore
from gnuradio import soapy, uhd  # type: ignore
from gnuradio.fft import window # type: ignore
from gnuradio.qtgui import Range, RangeWidget # type: ignore
from PyQt5 import Qt # type: ignore
from PyQt5 import QtCore # type: ignore
from PyQt5.QtCore import pyqtSlot # type: ignore

# Local imports
from apps.audio_file import AudioFileSource
from apps.media import AUDIO, choices
from apps.utils import (CONFIG_DIR, apply_dark_theme, apply_flowgraph_theme,
                        radio_label,
                        read_settings, update_app_config, power_percent,
                        resolve_power_range, scale_power, SPECTRUM_Y_AXIS,
                        FrequencyChooser, frequency_range)

def get_wav_files(settings):
    """Get list of WAV and MP3 files from media directory"""
    try:
        media_dir = settings.get('media_directory', '')
        if not media_dir or not os.path.exists(media_dir):
            return None
        # WAV and MP3, subfolders too, any case - see apps/media.py.
        wav_files = choices(media_dir, AUDIO)
        return wav_files if wav_files else []
    except:
        return None

class ConfigDialog(Qt.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AM Audio Generator Configuration")
        self.layout = Qt.QVBoxLayout(self)
        
        # Add config file path setup
        self.config_dir = CONFIG_DIR
        self.config_file = os.path.join(self.config_dir, "amAudioInternalGeneratorLive_config.json")
        
        # Read settings from window_settings.json
        settings = read_settings()
        self.usrp_ip = settings.get('usrp_ip', '')
        self.radio_type = settings.get('radio_type', 'hackrf')

        # Add OK/Cancel buttons
        self.button_box = Qt.QDialogButtonBox(
            Qt.QDialogButtonBox.Ok | Qt.QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        
        # Create container + opacity effect for sine frequency control
        self.sine_container = Qt.QWidget()
        self.sine_freq_opacity = Qt.QGraphicsOpacityEffect()
        self.sine_container.setGraphicsEffect(self.sine_freq_opacity)

        # Create all controls
        self.create_radio_label()
        self.create_frequency_control()
        self.create_power_control()
        self.create_modulation_controls()
        self.create_source_control()
        self.create_sine_frequency_control()

        self.layout.addWidget(self.button_box)

        # Connect source selection to control state updates
        self.source_combo.currentIndexChanged.connect(self.update_control_states)

        # Load saved configuration
        self.load_config()

        # Apply initial control states
        self.update_control_states()

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
        self.cf_chooser = FrequencyChooser(minimum=50.0, maximum=2200.0,
                                           value=300.0)
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

    def create_modulation_controls(self):
        # Carrier condition (unchanged)
        self.carrier_combo = Qt.QComboBox()
        self.carrier_combo.addItems(["Suppressed Carrier", "Full Carrier"])
        self.layout.addWidget(Qt.QLabel("Carrier Condition:"))
        self.layout.addWidget(self.carrier_combo)

        # Sideband condition (unchanged)
        self.sideband_combo = Qt.QComboBox()
        self.sideband_combo.addItems(["Double Sideband", "Single Sideband"])
        self.layout.addWidget(Qt.QLabel("Sideband Selection:"))
        self.layout.addWidget(self.sideband_combo)

        # Replace combo with radio buttons
        self.sideband_type_widget = Qt.QGroupBox("Sideband Type")
        self.sideband_type_layout = Qt.QHBoxLayout()
        self.lower_sideband = Qt.QRadioButton("Lower")
        self.upper_sideband = Qt.QRadioButton("Upper")
        self.lower_sideband.setChecked(True)
        self.sideband_type_layout.addWidget(self.lower_sideband)
        self.sideband_type_layout.addWidget(self.upper_sideband)
        self.sideband_type_widget.setLayout(self.sideband_type_layout)
        self.layout.addWidget(self.sideband_type_widget)

        # Add opacity effect
        self.sideband_opacity = Qt.QGraphicsOpacityEffect()
        self.sideband_type_widget.setGraphicsEffect(self.sideband_opacity)
        
        def update_sideband_state(index):
            enabled = index == 1  # Enable for Single Sideband
            self.sideband_type_widget.setEnabled(enabled)
            self.sideband_opacity.setOpacity(1.0 if enabled else 0.5)
        
        # Initialize disabled and connect
        update_sideband_state(0)
        self.sideband_combo.currentIndexChanged.connect(update_sideband_state)

    def create_source_control(self):
        self.source_combo = Qt.QComboBox()
        ok_button = self.button_box.button(Qt.QDialogButtonBox.Ok)

        settings = read_settings()
        wav_files = get_wav_files(settings)

        try:
            if wav_files is None:
                raise FileNotFoundError("Error - Setup Media directory in Settings")
            if not wav_files:
                raise FileNotFoundError("No WAV or MP3 files found in media directory")

            for display_name, wav_file in wav_files:
                self.source_combo.addItem(display_name, wav_file)

            self.source_combo.addItem("Sinewave", "sinewave")
            self.source_combo.addItem("No Modulation", "none")

            ok_button.setEnabled(self.radio_type in ('hackrf', 'vsg')
                                  or bool(self.usrp_ip))
            ok_button.setGraphicsEffect(None)

        except Exception as e:
            self.source_combo.addItem(str(e))
            self.source_combo.setEnabled(False)
            ok_button.setEnabled(False)
            opacity_effect = Qt.QGraphicsOpacityEffect()
            opacity_effect.setOpacity(0.30)
            ok_button.setGraphicsEffect(opacity_effect)

        self.layout.addWidget(Qt.QLabel("Input Source:"))
        self.layout.addWidget(self.source_combo)

    def create_sine_frequency_control(self):
        self.sine_layout = Qt.QHBoxLayout()
        self.sine_slider = Qt.QSlider(QtCore.Qt.Horizontal)
        self.sine_slider.setMinimum(1)
        self.sine_slider.setMaximum(50000)
        self.sine_slider.setValue(1000)
        self.sine_label = Qt.QLabel("Sine Frequency: 1000 Hz")
        self.sine_slider.valueChanged.connect(
            lambda v: self.sine_label.setText(f"Sine Frequency: {v} Hz"))
        self.sine_layout.addWidget(self.sine_label)
        self.sine_layout.addWidget(self.sine_slider)
        self.sine_container.setLayout(self.sine_layout)
        self.layout.addWidget(self.sine_container)

    def update_control_states(self):
        current_data = self.source_combo.currentData()
        is_sine = current_data == "sinewave"
        self.sine_slider.setEnabled(is_sine)
        self.sine_label.setEnabled(is_sine)
        self.sine_freq_opacity.setOpacity(1.0 if is_sine else 0.3)

    def load_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    config = json.load(f)

                self.cf_chooser.setValue(config.get('center_freq', 300))
                self.pwr_slider.setValue(power_percent(config.get('power_level'), 50))
                self.carrier_combo.setCurrentIndex(config.get('carrier_index', 0))
                self.sideband_combo.setCurrentIndex(config.get('sideband_index', 0))
                if config.get('sideband_type', 'lower') == 'upper':
                    self.upper_sideband.setChecked(True)
                else:
                    self.lower_sideband.setChecked(True)
                self.sine_slider.setValue(config.get('sine_freq', 1000))

                saved_source = config.get('source', 'sinewave')
                found = False
                for i in range(self.source_combo.count()):
                    if (self.source_combo.itemData(i) == saved_source or
                            (isinstance(saved_source, str) and os.path.basename(saved_source) ==
                             os.path.basename(str(self.source_combo.itemData(i))))):
                        self.source_combo.setCurrentIndex(i)
                        found = True
                        break
                if not found and saved_source not in ['sinewave', 'none']:
                    for i in range(self.source_combo.count()):
                        if self.source_combo.itemData(i) == 'sinewave':
                            self.source_combo.setCurrentIndex(i)
                            break
            except:
                pass
        else:
            os.makedirs(self.config_dir, exist_ok=True)

    def save_config(self):
        config = {
            'center_freq': self.cf_chooser.value(),
            'power_level': self.pwr_slider.value(),
            'carrier_index': self.carrier_combo.currentIndex(),
            'sideband_index': self.sideband_combo.currentIndex(),
            'sideband_type': 'upper' if self.upper_sideband.isChecked() else 'lower',
            'source': self.source_combo.currentData(),
            'sine_freq': self.sine_slider.value(),
        }
        
        update_app_config(self.config_file, config)

    def accept(self):
        self.save_config()
        super().accept()

    def get_values(self):
        ipXmitAddr = self.usrp_ip if self.radio_type == 'usrp' else ''

        sidebandDefault = 1 if self.sideband_combo.currentIndex() == 1 else 0
        sidebandTypeDefault = (1 if self.upper_sideband.isChecked() else -1) if sidebandDefault == 1 else 1

        current_data = self.source_combo.currentData()
        if current_data == "sinewave":
            sourceIndex = 1
            wavFile = None
        elif current_data == "none":
            sourceIndex = 2
            wavFile = None
        else:
            sourceIndex = 0
            wavFile = current_data

        return {
            'radio_type': self.radio_type,
            'ipXmitAddr': ipXmitAddr,
            'cf': self.cf_chooser.value(),
            'pwr': self.pwr_slider.value(),
            'carrierDefault': self.carrier_combo.currentIndex(),
            'sidebandDefault': sidebandDefault,
            'sidebandTypeDefault': sidebandTypeDefault,
            'sourceIndex': sourceIndex,
            'wavFile': wavFile,
            'sineFreq': self.sine_slider.value(),
        }

class amAudioInternalGeneratorLive(gr.top_block, Qt.QWidget):
    # What this window's own controls change that its dialog should
    # open on next time - see apps/utils.py: save_flowgraph_settings.
    SAVED_SETTINGS = {'power_level': 'rfPwr',
                      'center_freq': 'centerFreq'}

    def __init__(self, config_values=None):
        gr.top_block.__init__(self, "AM Audio Signal Generator", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("AM Audio Signal Generator")
        apply_flowgraph_theme(self)
        try:
            self.setWindowIcon(Qt.QIcon.fromTheme('gnuradio-grc'))
        except:
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

        self.settings = Qt.QSettings("GNU Radio", "amAudioSignalGenerator")

        try:
            geometry = self.settings.value("geometry")
            if geometry:
                self.restoreGeometry(geometry)
        except BaseException as exc:
            print(f"Qt GUI: Could not restore geometry: {str(exc)}", file=sys.stderr)

        ##################################################
        # Variable Entry
        ##################################################
        
        # Use provided config values or get them from dialog
        if config_values is None:
            config_dialog = ConfigDialog()
            if not config_dialog.exec_():
                sys.exit(0)
            values = config_dialog.get_values()
        else:
            values = config_values
        
        # Assign all values
        radio_type = values.get('radio_type', 'hackrf')
        ipXmitAddr = values['ipXmitAddr']
        cf = values['cf']
        pwr = values['pwr']
        carrierDefault = values['carrierDefault']
        sidebandDefault = values['sidebandDefault']
        sidebandTypeDefault = values['sidebandTypeDefault']
        sourceIndex = values.get('sourceIndex', 1)
        self.wavFile = values.get('wavFile', None)
        sineFreq = values.get('sineFreq', 1000)

        ##################################################
        # Variables
        ##################################################
        self.sidebandTypeDefault = sidebandTypeDefault
        self.sidebandDefault = sidebandDefault
        self.rfPwrDefault = rfPwrDefault = pwr
        self.modIndexDefault = modIndexDefault = 1
        self.cfDefault = cfDefault = cf
        self.carrierDefault = carrierDefault
        self.sidebandType = sidebandType = sidebandTypeDefault
        self.sideband = sideband = sidebandDefault
        self.samp_rate = samp_rate = 2e6
        self.rfPwr = rfPwr = rfPwrDefault
        self.outputIpAddr = outputIpAddr = ipXmitAddr
        self.modName = modName = 'AM'
        self.modIndex = modIndex = modIndexDefault
        self.inputSelectDefault = inputSelectDefault = sourceIndex
        self.inputSelect = inputSelect = sourceIndex
        self.sineFreq = sineFreq
        self.centerFreq = centerFreq = cfDefault
        self.carrier = carrier = carrierDefault
        self.radio_type = radio_type

        ##################################################
        # Blocks
        ##################################################
        # Create the options list
        self._sidebandType_options = [-1, 1]
        # Create the labels list
        self._sidebandType_labels = ['Lower', 'Upper']
        # Create the combo box
        # Create the radio buttons
        self._sidebandType_group_box = Qt.QGroupBox("Lower / Upper Sideband" + ": ")
        self._sidebandType_box = Qt.QHBoxLayout()
        class variable_chooser_button_group(Qt.QButtonGroup):
            def __init__(self, parent=None):
                Qt.QButtonGroup.__init__(self, parent)
            @pyqtSlot(int)
            def updateButtonChecked(self, button_id):
                self.button(button_id).setChecked(True)
        self._sidebandType_button_group = variable_chooser_button_group()
        self._sidebandType_group_box.setLayout(self._sidebandType_box)
        for i, _label in enumerate(self._sidebandType_labels):
            radio_button = Qt.QRadioButton(_label)
            self._sidebandType_box.addWidget(radio_button)
            self._sidebandType_button_group.addButton(radio_button, i)
        self._sidebandType_callback = lambda i: Qt.QMetaObject.invokeMethod(self._sidebandType_button_group, "updateButtonChecked", Qt.Q_ARG("int", self._sidebandType_options.index(i)))
        self._sidebandType_callback(self.sidebandType)
        self._sidebandType_button_group.buttonClicked[int].connect(
            lambda i: self.set_sidebandType(self._sidebandType_options[i]))
        self.top_grid_layout.addWidget(self._sidebandType_group_box, 2, 7, 1, 3)
        for r in range(2, 3):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(7, 10):
            self.top_grid_layout.setColumnStretch(c, 1)
        # Create the options list
        self._sideband_options = [0, 1]
        # Create the labels list
        self._sideband_labels = ['Double', 'Single']
        # Create the combo box
        # Create the radio buttons
        self._sideband_group_box = Qt.QGroupBox("Single / Double Sideband" + ": ")
        self._sideband_box = Qt.QHBoxLayout()
        class variable_chooser_button_group(Qt.QButtonGroup):
            def __init__(self, parent=None):
                Qt.QButtonGroup.__init__(self, parent)
            @pyqtSlot(int)
            def updateButtonChecked(self, button_id):
                self.button(button_id).setChecked(True)
        self._sideband_button_group = variable_chooser_button_group()
        self._sideband_group_box.setLayout(self._sideband_box)
        for i, _label in enumerate(self._sideband_labels):
            radio_button = Qt.QRadioButton(_label)
            self._sideband_box.addWidget(radio_button)
            self._sideband_button_group.addButton(radio_button, i)
        self._sideband_callback = lambda i: Qt.QMetaObject.invokeMethod(self._sideband_button_group, "updateButtonChecked", Qt.Q_ARG("int", self._sideband_options.index(i)))
        self._sideband_callback(self.sideband)
        self._sideband_button_group.buttonClicked[int].connect(
            lambda i: self.set_sideband(self._sideband_options[i]))
        self.top_grid_layout.addWidget(self._sideband_group_box, 2, 4, 1, 3)
        for r in range(2, 3):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(4, 7):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._rfPwr_range = Range(0, 100, 1, rfPwrDefault, 200)
        self._rfPwr_win = RangeWidget(self._rfPwr_range, self.set_rfPwr, "RF Output Power (%)", "counter_slider", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._rfPwr_win, 1, 5, 1, 4)
        for r in range(1, 2):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(5, 9):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._modIndex_range = Range(0, 10, 0.01, modIndexDefault, 200)
        self._modIndex_win = RangeWidget(self._modIndex_range, self.set_modIndex, "Modulation Index", "counter_slider", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._modIndex_win, 1, 0, 1, 4)
        for r in range(1, 2):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 4):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._centerFreq_range = frequency_range(50, 2200, 0.01, cfDefault, 200)
        self._centerFreq_win = RangeWidget(self._centerFreq_range, self.set_centerFreq, "Center Frequency (MHz)", "counter", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._centerFreq_win, 0, 5, 1, 3)
        for r in range(0, 1):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(5, 8):
            self.top_grid_layout.setColumnStretch(c, 1)
        # Create the options list
        self._carrier_options = [0, 1]
        # Create the labels list
        self._carrier_labels = ['Suppressed', 'Full']
        # Create the combo box
        # Create the radio buttons
        self._carrier_group_box = Qt.QGroupBox("Carrier Condition" + ": ")
        self._carrier_box = Qt.QHBoxLayout()
        class variable_chooser_button_group(Qt.QButtonGroup):
            def __init__(self, parent=None):
                Qt.QButtonGroup.__init__(self, parent)
            @pyqtSlot(int)
            def updateButtonChecked(self, button_id):
                self.button(button_id).setChecked(True)
        self._carrier_button_group = variable_chooser_button_group()
        self._carrier_group_box.setLayout(self._carrier_box)
        for i, _label in enumerate(self._carrier_labels):
            radio_button = Qt.QRadioButton(_label)
            self._carrier_box.addWidget(radio_button)
            self._carrier_button_group.addButton(radio_button, i)
        self._carrier_callback = lambda i: Qt.QMetaObject.invokeMethod(self._carrier_button_group, "updateButtonChecked", Qt.Q_ARG("int", self._carrier_options.index(i)))
        self._carrier_callback(self.carrier)
        self._carrier_button_group.buttonClicked[int].connect(
            lambda i: self.set_carrier(self._carrier_options[i]))
        self.top_grid_layout.addWidget(self._carrier_group_box, 2, 0, 1, 4)
        for r in range(2, 3):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 4):
            self.top_grid_layout.setColumnStretch(c, 1)

        # Input source selector
        self._inputSelect_options = [0, 1, 2]
        self._inputSelect_labels = ['Audio File', 'Sinewave', 'No Modulation']
        self._inputSelect_group_box = Qt.QGroupBox("Input Signal: ")
        self._inputSelect_box = Qt.QHBoxLayout()
        class variable_chooser_button_group(Qt.QButtonGroup):
            def __init__(self, parent=None):
                Qt.QButtonGroup.__init__(self, parent)
            @pyqtSlot(int)
            def updateButtonChecked(self, button_id):
                self.button(button_id).setChecked(True)
        self._inputSelect_button_group = variable_chooser_button_group()
        self._inputSelect_group_box.setLayout(self._inputSelect_box)
        for i, _label in enumerate(self._inputSelect_labels):
            radio_button = Qt.QRadioButton(_label)
            self._inputSelect_box.addWidget(radio_button)
            self._inputSelect_button_group.addButton(radio_button, i)
        self._inputSelect_callback = lambda i: Qt.QMetaObject.invokeMethod(self._inputSelect_button_group, "updateButtonChecked", Qt.Q_ARG("int", self._inputSelect_options.index(i)))
        self._inputSelect_callback(self.inputSelect)
        self._inputSelect_button_group.buttonClicked[int].connect(
            lambda i: self.set_inputSelect(self._inputSelect_options[i]))
        self.top_grid_layout.addWidget(self._inputSelect_group_box, 3, 0, 1, 6)

        # Sine frequency range widget
        self._sineFreq_range = Range(1, 50000, 1, sineFreq, 200)
        self._sineFreq_win = RangeWidget(self._sineFreq_range, self.set_sineFreq, "Sinusoid Frequency (Hz)", "counter", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._sineFreq_win, 3, 6, 1, 4)

        self._power_range = resolve_power_range(radio_type)
        if radio_type == 'vsg':
            from apps.vsg_sink import vsg_sink
            self.radio_sink = vsg_sink(
                center_freq=centerFreq*1e6,
                sample_rate=samp_rate,
                level_dbm=scale_power(rfPwr, self._power_range))
        elif radio_type == 'usrp':
            self.radio_sink = uhd.usrp_sink(
                ",".join(('addr='+outputIpAddr, '')),
                uhd.stream_args(cpu_format="fc32", args='', channels=list(range(0,1))),
                "",
            )
            self.radio_sink.set_samp_rate(samp_rate)
            self.radio_sink.set_time_now(uhd.time_spec(time.time()), uhd.ALL_MBOARDS)
            self.radio_sink.set_center_freq(centerFreq*1e6, 0)
            self.radio_sink.set_antenna("TX/RX", 0)
            self._power_range = resolve_power_range(radio_type, self.radio_sink)
            self.radio_sink.set_gain(scale_power(rfPwr, self._power_range), 0)
        else:
            self.radio_sink = soapy.sink('driver=hackrf', 'fc32', 1, '', '', [''], [''])
            self.radio_sink.set_sample_rate(0, samp_rate)
            self.radio_sink.set_frequency(0, centerFreq*1e6)
            self.radio_sink.set_gain(0, 'VGA', scale_power(rfPwr, self._power_range))
            self.radio_sink.set_gain(0, 'AMP', 0)
        self.rational_resampler_xxx_1 = filter.rational_resampler_ccc(
                interpolation=1,
                decimation=10,
                taps=[],
                fractional_bw=0)
        self.rational_resampler_xxx_0 = filter.rational_resampler_ccf(
                interpolation=250,
                decimation=3,
                taps=[],
                fractional_bw=0)
        self.qtgui_time_sink_x_0 = qtgui.time_sink_c(
            25000, #size
            samp_rate, #samp_rate
            'Baseband Time Domain', #name
            1, #number of inputs
            None # parent
        )
        self.qtgui_time_sink_x_0.set_update_time(0.10)
        self.qtgui_time_sink_x_0.set_y_axis(-1, 1)

        self.qtgui_time_sink_x_0.set_y_label('Amplitude', "")

        self.qtgui_time_sink_x_0.enable_tags(True)
        self.qtgui_time_sink_x_0.set_trigger_mode(qtgui.TRIG_MODE_FREE, qtgui.TRIG_SLOPE_POS, 0.0, 0, 0, "")
        self.qtgui_time_sink_x_0.enable_autoscale(False)
        self.qtgui_time_sink_x_0.enable_grid(True)
        self.qtgui_time_sink_x_0.enable_axis_labels(True)
        self.qtgui_time_sink_x_0.enable_control_panel(False)
        self.qtgui_time_sink_x_0.enable_stem_plot(False)


        labels = ['Real', 'Imag', 'Signal 3', 'Signal 4', 'Signal 5',
            'Signal 6', 'Signal 7', 'Signal 8', 'Signal 9', 'Signal 10']
        widths = [1, 1, 1, 1, 1,
            1, 1, 1, 1, 1]
        colors = ['blue', 'red', 'green', 'black', 'cyan',
            'magenta', 'yellow', 'dark red', 'dark green', 'dark blue']
        alphas = [1.0, 1.0, 1.0, 1.0, 1.0,
            1.0, 1.0, 1.0, 1.0, 1.0]
        styles = [1, 1, 1, 1, 1,
            1, 1, 1, 1, 1]
        markers = [-1, -1, -1, -1, -1,
            -1, -1, -1, -1, -1]


        for i in range(2):
            if len(labels[i]) == 0:
                if (i % 2 == 0):
                    self.qtgui_time_sink_x_0.set_line_label(i, "Re{{Data {0}}}".format(i/2))
                else:
                    self.qtgui_time_sink_x_0.set_line_label(i, "Im{{Data {0}}}".format(i/2))
            else:
                self.qtgui_time_sink_x_0.set_line_label(i, labels[i])
            self.qtgui_time_sink_x_0.set_line_width(i, widths[i])
            self.qtgui_time_sink_x_0.set_line_color(i, colors[i])
            self.qtgui_time_sink_x_0.set_line_style(i, styles[i])
            self.qtgui_time_sink_x_0.set_line_marker(i, markers[i])
            self.qtgui_time_sink_x_0.set_line_alpha(i, alphas[i])

        self._qtgui_time_sink_x_0_win = sip.wrapinstance(self.qtgui_time_sink_x_0.qwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(self._qtgui_time_sink_x_0_win, 4, 0, 5, 7)
        for r in range(4, 9):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 7):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.qtgui_freq_sink_x_0 = qtgui.freq_sink_c(
            4096, #size
            window.WIN_BLACKMAN_hARRIS, #wintype
            centerFreq*1e6, #fc
            samp_rate/10, #bw
            'Modulated Spectrum', #name
            1,
            None # parent
        )
        self.qtgui_freq_sink_x_0.set_update_time(0.10)
        self.qtgui_freq_sink_x_0.set_y_axis(*SPECTRUM_Y_AXIS)
        self.qtgui_freq_sink_x_0.set_y_label('Relative Gain', 'dB')
        self.qtgui_freq_sink_x_0.set_trigger_mode(qtgui.TRIG_MODE_FREE, 0.0, 0, "")
        self.qtgui_freq_sink_x_0.enable_autoscale(False)
        self.qtgui_freq_sink_x_0.enable_grid(True)
        self.qtgui_freq_sink_x_0.set_fft_average(1.0)
        self.qtgui_freq_sink_x_0.enable_axis_labels(True)
        self.qtgui_freq_sink_x_0.enable_control_panel(False)
        self.qtgui_freq_sink_x_0.set_fft_window_normalized(False)

        self.qtgui_freq_sink_x_0.disable_legend()


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
        self.top_grid_layout.addWidget(self._qtgui_freq_sink_x_0_win, 9, 0, 5, 10)
        for r in range(9, 14):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 10):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.qtgui_const_sink_x_0 = qtgui.const_sink_c(
            2500, #size
            'IQ Polar Plot', #name
            1, #number of inputs
            None # parent
        )
        self.qtgui_const_sink_x_0.set_update_time(0.10)
        self.qtgui_const_sink_x_0.set_y_axis(-1.25, 1.25)
        self.qtgui_const_sink_x_0.set_x_axis(-1.25, 1.25)
        self.qtgui_const_sink_x_0.set_trigger_mode(qtgui.TRIG_MODE_FREE, qtgui.TRIG_SLOPE_POS, 0.0, 0, "")
        self.qtgui_const_sink_x_0.enable_autoscale(False)
        self.qtgui_const_sink_x_0.enable_grid(True)
        self.qtgui_const_sink_x_0.enable_axis_labels(True)

        self.qtgui_const_sink_x_0.disable_legend()

        labels = ['', '', '', '', '',
            '', '', '', '', '']
        widths = [1, 1, 1, 1, 1,
            1, 1, 1, 1, 1]
        colors = ["black", "red", "red", "red", "red",
            "red", "red", "red", "red", "red"]
        styles = [1, 0, 0, 0, 0,
            0, 0, 0, 0, 0]
        markers = [0, 0, 0, 0, 0,
            0, 0, 0, 0, 0]
        alphas = [1.0, 1.0, 1.0, 1.0, 1.0,
            1.0, 1.0, 1.0, 1.0, 1.0]

        for i in range(1):
            if len(labels[i]) == 0:
                self.qtgui_const_sink_x_0.set_line_label(i, "Data {0}".format(i))
            else:
                self.qtgui_const_sink_x_0.set_line_label(i, labels[i])
            self.qtgui_const_sink_x_0.set_line_width(i, widths[i])
            self.qtgui_const_sink_x_0.set_line_color(i, colors[i])
            self.qtgui_const_sink_x_0.set_line_style(i, styles[i])
            self.qtgui_const_sink_x_0.set_line_marker(i, markers[i])
            self.qtgui_const_sink_x_0.set_line_alpha(i, alphas[i])

        self._qtgui_const_sink_x_0_win = sip.wrapinstance(self.qtgui_const_sink_x_0.qwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(self._qtgui_const_sink_x_0_win, 4, 7, 5, 3)
        for r in range(4, 9):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(7, 10):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._modName_tool_bar = Qt.QToolBar(self)

        if None:
            self._modName_formatter = None
        else:
            self._modName_formatter = lambda x: str(x)

        self._modName_tool_bar.addWidget(Qt.QLabel("Modulation: "))
        self._modName_label = Qt.QLabel(str(self._modName_formatter(self.modName)))
        self._modName_tool_bar.addWidget(self._modName_label)
        self.top_grid_layout.addWidget(self._modName_tool_bar, 0, 1, 1, 2)
        for r in range(0, 1):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(1, 3):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.hilbert_fc_0 = filter.hilbert_fc(500, window.WIN_HAMMING, 6.76)
        self.blocks_selector_2 = blocks.selector(gr.sizeof_gr_complex*1,sideband,0)
        self.blocks_selector_2.set_enabled(True)
        self.blocks_multiply_const_vxx_3 = blocks.multiply_const_cc(0.45)
        self.blocks_multiply_const_vxx_2 = blocks.multiply_const_ff(modIndex)
        self.blocks_multiply_const_vxx_0 = blocks.multiply_const_ff(sidebandType)
        self.blocks_float_to_complex_0_0 = blocks.float_to_complex(1)
        self.blocks_float_to_complex_0 = blocks.float_to_complex(1)
        self.blocks_complex_to_float_0 = blocks.complex_to_float(1)
        self.blocks_add_const_vxx_0 = blocks.add_const_ff(carrier)

        # Audio file source, WAV or MP3 (48kHz → 24kHz via decimation by 2)
        self.audio_file = None
        if self.wavFile and os.path.exists(self.wavFile):
            self.audio_file = AudioFileSource(self.wavFile)
            self.blocks_wavfile_source_0 = self.audio_file.block
        else:
            self.blocks_wavfile_source_0 = blocks.null_source(gr.sizeof_float*1)
        self.rational_resampler_wav = filter.rational_resampler_fff(
            interpolation=1, decimation=2, taps=[], fractional_bw=0)

        # Sine source at 24 kHz
        from gnuradio import analog  # type: ignore
        self.analog_sig_source_x_0 = analog.sig_source_f(24000, analog.GR_COS_WAVE, sineFreq, 1, 0, 0)

        # Null source for no-modulation option
        self.blocks_null_source_1 = blocks.null_source(gr.sizeof_float*1)

        # Input selector: 0=wav, 1=sine, 2=none
        self.blocks_selector_0 = blocks.selector(gr.sizeof_float*1, sourceIndex, 0)
        self.blocks_selector_0.set_enabled(True)


        ##################################################
        # Connections
        ##################################################
        self.connect((self.blocks_wavfile_source_0, 0), (self.rational_resampler_wav, 0))
        self.connect((self.rational_resampler_wav, 0), (self.blocks_selector_0, 0))
        self.connect((self.analog_sig_source_x_0, 0), (self.blocks_selector_0, 1))
        self.connect((self.blocks_null_source_1, 0), (self.blocks_selector_0, 2))
        self.connect((self.blocks_selector_0, 0), (self.blocks_multiply_const_vxx_2, 0))
        self.connect((self.blocks_add_const_vxx_0, 0), (self.blocks_float_to_complex_0, 0))
        self.connect((self.blocks_add_const_vxx_0, 0), (self.hilbert_fc_0, 0))
        self.connect((self.blocks_complex_to_float_0, 0), (self.blocks_float_to_complex_0_0, 0))
        self.connect((self.blocks_complex_to_float_0, 1), (self.blocks_multiply_const_vxx_0, 0))
        self.connect((self.blocks_float_to_complex_0, 0), (self.blocks_selector_2, 0))
        self.connect((self.blocks_float_to_complex_0_0, 0), (self.qtgui_const_sink_x_0, 0))
        self.connect((self.blocks_float_to_complex_0_0, 0), (self.qtgui_time_sink_x_0, 0))
        self.connect((self.blocks_float_to_complex_0_0, 0), (self.rational_resampler_xxx_1, 0))
        self.connect((self.blocks_float_to_complex_0_0, 0), (self.radio_sink, 0))
        self.connect((self.blocks_multiply_const_vxx_0, 0), (self.blocks_float_to_complex_0_0, 1))
        self.connect((self.blocks_multiply_const_vxx_2, 0), (self.blocks_add_const_vxx_0, 0))
        self.connect((self.blocks_multiply_const_vxx_3, 0), (self.blocks_complex_to_float_0, 0))
        self.connect((self.blocks_selector_2, 0), (self.rational_resampler_xxx_0, 0))
        self.connect((self.hilbert_fc_0, 0), (self.blocks_selector_2, 1))
        self.connect((self.rational_resampler_xxx_0, 0), (self.blocks_multiply_const_vxx_3, 0))
        self.connect((self.rational_resampler_xxx_1, 0), (self.qtgui_freq_sink_x_0, 0))


    def closeEvent(self, event):
        self.settings = Qt.QSettings("GNU Radio", "amAudioSignalGenerator")
        self.settings.setValue("geometry", self.saveGeometry())
        self.stop()
        self.wait()
        # An MP3's ffmpeg would otherwise sit on a full pipe until the
        # launcher exits - see apps/audio_file.py.
        if getattr(self, 'audio_file', None) is not None:
            self.audio_file.close()

        event.accept()

    def get_sidebandTypeDefault(self):
        return self.sidebandTypeDefault

    def set_sidebandTypeDefault(self, sidebandTypeDefault):
        self.sidebandTypeDefault = sidebandTypeDefault
        self.set_sidebandType(self.sidebandTypeDefault)

    def get_sidebandDefault(self):
        return self.sidebandDefault

    def set_sidebandDefault(self, sidebandDefault):
        self.sidebandDefault = sidebandDefault
        self.set_sideband(self.sidebandDefault)

    def get_rfPwrDefault(self):
        return self.rfPwrDefault

    def set_rfPwrDefault(self, rfPwrDefault):
        self.rfPwrDefault = rfPwrDefault
        self.set_rfPwr(self.rfPwrDefault)

    def get_modIndexDefault(self):
        return self.modIndexDefault

    def set_modIndexDefault(self, modIndexDefault):
        self.modIndexDefault = modIndexDefault
        self.set_modIndex(self.modIndexDefault)

    def get_cfDefault(self):
        return self.cfDefault

    def set_cfDefault(self, cfDefault):
        self.cfDefault = cfDefault
        self.set_centerFreq(self.cfDefault)

    def get_carrierDefault(self):
        return self.carrierDefault

    def set_carrierDefault(self, carrierDefault):
        self.carrierDefault = carrierDefault
        self.set_carrier(self.carrierDefault)

    def get_sidebandType(self):
        return self.sidebandType

    def set_sidebandType(self, sidebandType):
        self.sidebandType = sidebandType
        self._sidebandType_callback(self.sidebandType)
        self.blocks_multiply_const_vxx_0.set_k(self.sidebandType)

    def get_sideband(self):
        return self.sideband

    def set_sideband(self, sideband):
        self.sideband = sideband
        self._sideband_callback(self.sideband)
        self.blocks_selector_2.set_input_index(self.sideband)

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.qtgui_freq_sink_x_0.set_frequency_range(self.centerFreq*1e6, self.samp_rate/10)
        self.qtgui_time_sink_x_0.set_samp_rate(self.samp_rate)
        if self.radio_type == 'usrp':
            self.radio_sink.set_samp_rate(self.samp_rate)
        else:
            self.radio_sink.set_sample_rate(0, self.samp_rate)

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

    def get_outputIpAddr(self):
        return self.outputIpAddr

    def set_outputIpAddr(self, outputIpAddr):
        self.outputIpAddr = outputIpAddr

    def get_modName(self):
        return self.modName

    def set_modName(self, modName):
        self.modName = modName
        Qt.QMetaObject.invokeMethod(self._modName_label, "setText", Qt.Q_ARG("QString", str(self._modName_formatter(self.modName))))

    def get_modIndex(self):
        return self.modIndex

    def set_modIndex(self, modIndex):
        self.modIndex = modIndex
        self.blocks_multiply_const_vxx_2.set_k(self.modIndex)

    def get_inputSelectDefault(self):
        return self.inputSelectDefault

    def set_inputSelectDefault(self, inputSelectDefault):
        self.inputSelectDefault = inputSelectDefault
        self.set_inputSelect(self.inputSelectDefault)

    def get_inputSelect(self):
        return self.inputSelect

    def set_inputSelect(self, inputSelect):
        self.inputSelect = inputSelect
        self._inputSelect_callback(self.inputSelect)
        self.blocks_selector_0.set_input_index(self.inputSelect)

    def get_sineFreq(self):
        return self.sineFreq

    def set_sineFreq(self, sineFreq):
        self.sineFreq = sineFreq
        self.analog_sig_source_x_0.set_frequency(self.sineFreq)

    def get_centerFreq(self):
        return self.centerFreq

    def set_centerFreq(self, centerFreq):
        self.centerFreq = centerFreq
        self.qtgui_freq_sink_x_0.set_frequency_range(self.centerFreq*1e6, self.samp_rate/10)
        if self.radio_type == 'usrp':
            self.radio_sink.set_center_freq(self.centerFreq*1e6, 0)
        else:
            self.radio_sink.set_frequency(0, self.centerFreq*1e6)

    def get_carrier(self):
        return self.carrier

    def set_carrier(self, carrier):
        self.carrier = carrier
        self._carrier_callback(self.carrier)
        self.blocks_add_const_vxx_0.set_k(self.carrier)




def main(top_block_cls=amAudioInternalGeneratorLive, options=None, app=None, config_values=None):

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
