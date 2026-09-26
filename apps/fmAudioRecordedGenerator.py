#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: FM Audio Signal Generator
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
import sys
import time
from math import pi

# Third party imports 
from PyQt5 import Qt, QtCore # type: ignore
from PyQt5.QtCore import pyqtSlot # type: ignore
import sip # type: ignore

from gnuradio import analog, blocks, filter, gr, qtgui, soapy, uhd  # type: ignore
from gnuradio.fft import window # type: ignore
from gnuradio.qtgui import Range, RangeWidget # type: ignore

# Local imports
from apps.audio_file import AudioFileSource
from apps.media import AUDIO, choices
from apps.utils import (CONFIG_DIR, apply_dark_theme, apply_flowgraph_theme,
                        radio_label,
                        read_settings, update_app_config, power_percent,
                        resolve_power_range, scale_power, SPECTRUM_Y_AXIS, adopt_legacy_config,
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
        self.setWindowTitle("FM Audio Generator Configuration")
        self.layout = Qt.QVBoxLayout(self)
        self.config_dir = CONFIG_DIR
        self.config_file = os.path.join(self.config_dir, "fmAudioRecordedGenerator_config.json")
        # Older builds saved this dialog under fmAudioGenerator_config.json;
        # fold that in once so nothing the user set is lost.
        adopt_legacy_config(self.config_dir, "fmAudioGenerator_config.json", self.config_file)
        
        # Read settings from window_settings.json
        settings = read_settings()
        self.usrp_ip = settings.get('usrp_ip', '')
        self.radio_type = settings.get('radio_type', 'hackrf')
        
        # Add OK/Cancel buttons
        self.button_box = Qt.QDialogButtonBox(
            Qt.QDialogButtonBox.Ok | Qt.QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        
        # Create containers for opacity effects
        self.sine_container = Qt.QWidget()
        self.dev_container = Qt.QWidget()
        
        # Create opacity effects
        self.sine_freq_opacity = Qt.QGraphicsOpacityEffect()
        self.freq_dev_opacity = Qt.QGraphicsOpacityEffect()
        
        # Apply effects to containers
        self.sine_container.setGraphicsEffect(self.sine_freq_opacity)
        self.dev_container.setGraphicsEffect(self.freq_dev_opacity)
        
        # Create all the input widgets
        self.create_radio_label()
        self.create_frequency_control()
        self.create_power_control()
        self.create_source_control()
        self.create_freq_dev_control()
        self.create_sine_frequency_control()
        
        self.layout.addWidget(self.button_box)
        
        # Connect source selection to control state updates
        self.source_combo.currentIndexChanged.connect(self.update_control_states)
        
        # Load saved configuration
        self.load_config()
        
        # Apply initial states
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

    def create_source_control(self):
        self.source_combo = Qt.QComboBox()
        ok_button = self.button_box.button(Qt.QDialogButtonBox.Ok)
        
        # Read media directory setting and get the audio files
        settings = read_settings()
        wav_files = get_wav_files(settings)
        
        try:
            if wav_files is None:
                raise FileNotFoundError("Error - Setup Media directory in Settings")
                
            if not wav_files:
                raise FileNotFoundError("No WAV or MP3 files found in media directory")
                
            # Add the audio files found
            for display_name, wav_file in wav_files:
                self.source_combo.addItem(display_name, wav_file)
                
            # Always add these options
            self.source_combo.addItem("Sinewave", "sinewave")
            self.source_combo.addItem("No Modulation", "none")
            
            # Only enable OK button if we have media files (and IP addresses when using USRP)
            ok_button.setEnabled(self.radio_type in ('hackrf', 'vsg')
                                  or bool(self.usrp_ip))
            ok_button.setGraphicsEffect(None)
                
        except Exception as e:
            self.source_combo.addItem(str(e))
            self.source_combo.setEnabled(False)
            # Disable OK button and add opacity effect
            ok_button.setEnabled(False)
            opacity_effect = Qt.QGraphicsOpacityEffect()
            opacity_effect.setOpacity(0.30)
            ok_button.setGraphicsEffect(opacity_effect)
                
        self.layout.addWidget(Qt.QLabel("Input Source:"))
        self.layout.addWidget(self.source_combo)

    def create_freq_dev_control(self):
        self.dev_layout = Qt.QHBoxLayout()
        self.dev_slider = Qt.QSlider(QtCore.Qt.Horizontal)
        self.dev_slider.setMinimum(0)
        self.dev_slider.setMaximum(1000)
        self.dev_slider.setValue(100)
        self.dev_label = Qt.QLabel("Frequency Deviation: 100 kHz")
        self.dev_slider.valueChanged.connect(
            lambda v: self.dev_label.setText(f"Frequency Deviation: {v} kHz"))
        self.dev_layout.addWidget(self.dev_label)
        self.dev_layout.addWidget(self.dev_slider)
        
        # Set layout to container widget
        self.dev_container.setLayout(self.dev_layout)
        self.layout.addWidget(self.dev_container)

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
        
        # Set layout to container widget
        self.sine_container.setLayout(self.sine_layout)
        self.layout.addWidget(self.sine_container)

    def update_control_states(self):
        """Update control states based on source selection"""
        current_data = self.source_combo.currentData()
        
        # For sine frequency control
        is_sine = current_data == "sinewave"
        self.sine_slider.setEnabled(is_sine)
        self.sine_label.setEnabled(is_sine)
        self.sine_freq_opacity.setOpacity(1.0 if is_sine else 0.3)
        
        # For frequency deviation control
        is_no_mod = current_data == "none"
        self.dev_slider.setEnabled(not is_no_mod)
        self.dev_label.setEnabled(not is_no_mod)
        self.freq_dev_opacity.setOpacity(0.3 if is_no_mod else 1.0)
        
        # If no modulation, also dim sine frequency
        if is_no_mod:
            self.sine_slider.setEnabled(False)
            self.sine_label.setEnabled(False)
            self.sine_freq_opacity.setOpacity(0.3)

    def load_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                    
                self.cf_chooser.setValue(config.get('center_freq', 300))
                self.pwr_slider.setValue(power_percent(config.get('power_level'), 50))
                self.dev_slider.setValue(config.get('freq_dev', 100))
                self.sine_slider.setValue(config.get('sine_freq', 1000))
                
                # Load source by saved identifier
                saved_source = config.get('source', 'sinewave')
                found = False
                
                # First try to find the saved source in current combo box items
                for i in range(self.source_combo.count()):
                    if (self.source_combo.itemData(i) == saved_source or 
                        (isinstance(saved_source, str) and os.path.basename(saved_source) == 
                         os.path.basename(str(self.source_combo.itemData(i))))):
                        self.source_combo.setCurrentIndex(i)
                        found = True
                        break
                
                # If not found and it's not a special source, select sinewave as fallback
                if not found and saved_source not in ['sinewave', 'none']:
                    # Find sinewave index
                    for i in range(self.source_combo.count()):
                        if self.source_combo.itemData(i) == 'sinewave':
                            self.source_combo.setCurrentIndex(i)
                            break
                
            except:
                # If loading fails, keep default values
                pass
        else:
            # Create config directory if it doesn't exist
            os.makedirs(self.config_dir, exist_ok=True)

    def save_config(self):
        config = {
            'center_freq': self.cf_chooser.value(),
            'power_level': self.pwr_slider.value(),
            'freq_dev': self.dev_slider.value(),
            'sine_freq': self.sine_slider.value(),
            'source': self.source_combo.currentData()
        }
        
        update_app_config(self.config_file, config)

    def accept(self):
        self.save_config()
        super().accept()

    def get_values(self):
        ipXmitAddr = self.usrp_ip if self.radio_type == 'usrp' else ''
        
        # Get selected source
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
            
        values = {
            'radio_type': self.radio_type,
            'ipXmitAddr': ipXmitAddr,
            'cf': self.cf_chooser.value(),
            'rfPwr': self.pwr_slider.value(),
            'sourceIndex': sourceIndex,
            'wavFile': wavFile,
            'freqDev': self.dev_slider.value(),
            'sineFreq': self.sine_slider.value()
        }
        return values

class fmAudioRecordedGenerator(gr.top_block, Qt.QWidget):
    # What this window's own controls change that its dialog should
    # open on next time - see apps/utils.py: save_flowgraph_settings.
    SAVED_SETTINGS = {'power_level': 'rfPwr',
                      'center_freq': 'centerFreq'}

    def __init__(self, config_values=None):
        gr.top_block.__init__(self, "FM Audio Signal Generator", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("FM Audio Signal Generator")
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

        self.settings = Qt.QSettings("GNU Radio", "fmAudioRecordedGenerator")

        try:
            geometry = self.settings.value("geometry")
            if geometry:
                self.restoreGeometry(geometry)
        except BaseException as exc:
            print(f"Qt GUI: Could not restore geometry: {str(exc)}", file=sys.stderr)

        ##################################################
        # Variables
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
        rfPwr = values.get('rfPwr', -50)
        sourceIndex = values['sourceIndex']
        freqDev = values['freqDev']
        sineFreq = values['sineFreq']
        self.wavFile = values.get('wavFile', None)

        # Continue with existing initialization...
        self.sineFreqDefault = sineFreq
        self.inputSelectDefault = sourceIndex
        self.freqDevDefault = freqDev
        self.cfDefault = cf
        self.rfPwr = rfPwr
        self.sineFreq = sineFreq
        self.samp_rate = samp_rate = 2e6
        self.modName = modName = 'FM'
        self.inputSelect = inputSelect = sourceIndex
        self.freqDev = freqDev = freqDev
        self.centerFreq = centerFreq = cf
        self.radio_type = radio_type

        ##################################################
        # Blocks
        ##################################################
        # Create the input selector radio buttons
        self._inputSelect_options = [0, 1, 2]
        self._inputSelect_labels = ['Audio', 'Sinewave', 'No Modulation']
        self._inputSelect_group_box = Qt.QGroupBox("Input Signal" + ": ")
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
        
        self.top_grid_layout.addWidget(self._inputSelect_group_box, 1, 0, 1, 5)

        # Create a right-aligned frequency deviation widget with fixed width
        self._freqDev_range = Range(0, 1000, 0.1, freqDev, 200)
        self._freqDev_win = RangeWidget(self._freqDev_range, self.set_freqDev, "Frequency Deviation (kHz)", "counter", float, QtCore.Qt.Horizontal)
        self._freqDev_win.setMinimumWidth(300)  # Set minimum width
        self._freqDev_win.setMaximumWidth(300)  # Set maximum width
        freqdev_container = Qt.QWidget()
        freqdev_layout = Qt.QHBoxLayout()
        freqdev_layout.addStretch()  # Add stretch to push widget to right
        freqdev_layout.addWidget(self._freqDev_win)
        freqdev_container.setLayout(freqdev_layout)
        self.top_grid_layout.addWidget(freqdev_container, 0, 6, 1, 4)
        
        # Create a right-aligned sine frequency widget with same width
        self._sineFreq_range = Range(0, 50000, 1, sineFreq, 200)
        self._sineFreq_win = RangeWidget(self._sineFreq_range, self.set_sineFreq, "Sinusoid Frequency (Hz)", "counter", float, QtCore.Qt.Horizontal)
        self._sineFreq_win.setMinimumWidth(300)  # Set minimum width
        self._sineFreq_win.setMaximumWidth(300)  # Set maximum width
        sinefreq_container = Qt.QWidget()
        sinefreq_layout = Qt.QHBoxLayout()
        sinefreq_layout.addStretch()  # Add stretch to push widget to right
        sinefreq_layout.addWidget(self._sineFreq_win)
        sinefreq_container.setLayout(sinefreq_layout)
        self.top_grid_layout.addWidget(sinefreq_container, 1, 5, 1, 5)

        self._centerFreq_range = frequency_range(50, 2200, 0.01, cf, 200)
        self._centerFreq_win = RangeWidget(self._centerFreq_range, self.set_centerFreq, "Center Frequency (MHz)", "counter", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._centerFreq_win, 0, 0, 1, 3)
        for r in range(0, 1):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 3):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._rfPwr_range = Range(0, 100, 1, rfPwr, 200)
        self._rfPwr_win = RangeWidget(self._rfPwr_range, self.set_rfPwr, "RF Output Power (%)", "counter_slider", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._rfPwr_win, 2, 0, 1, 10)
        self._power_range = resolve_power_range(radio_type)
        if radio_type == 'vsg':
            from apps.vsg_sink import vsg_sink
            self.radio_sink = vsg_sink(
                center_freq=centerFreq*1e6,
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
        self.rational_resampler_xxx_0_0 = filter.rational_resampler_fff(
                interpolation=40,
                decimation=1,
                taps=[],
                fractional_bw=0)
        self.qtgui_time_sink_x_0 = qtgui.time_sink_f(
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

        self.qtgui_time_sink_x_0.disable_legend()

        labels = ['Signal 1', 'Signal 2', 'Signal 3', 'Signal 4', 'Signal 5',
            'Signal 6', 'Signal 7', 'Signal 8', 'Signal 9', 'Signal 10']
        widths = [1, 1, 1, 1, 1,
            1, 1, 1, 1, 1]
        colors = ['black', 'red', 'green', 'black', 'cyan',
            'magenta', 'yellow', 'dark red', 'dark green', 'dark blue']
        alphas = [1.0, 1.0, 1.0, 1.0, 1.0]
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
        self.top_grid_layout.addWidget(self._qtgui_time_sink_x_0_win, 3, 0, 5, 7)
        for r in range(3, 8):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 7):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.qtgui_freq_sink_x_0 = qtgui.freq_sink_c(
            8192, #size
            window.WIN_BLACKMAN_hARRIS, #wintype
            centerFreq*1e6, #fc
            samp_rate, #bw
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
        self.top_grid_layout.addWidget(self._qtgui_freq_sink_x_0_win, 8, 0, 5, 10)
        for r in range(8, 13):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 10):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.qtgui_const_sink_x_0 = qtgui.const_sink_c(
            2500, #size
            "", #name
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
        self.top_grid_layout.addWidget(self._qtgui_const_sink_x_0_win, 3, 7, 5, 3)
        for r in range(3, 8):
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
        self.top_grid_layout.addWidget(self._modName_tool_bar, 0, 3, 1, 3)
        for r in range(0, 1):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(3, 6):
            self.top_grid_layout.setColumnStretch(c, 1)
        
        # The selected file, WAV or MP3, looping at 48 kHz
        self.audio_file = None
        if self.wavFile and os.path.exists(self.wavFile):
            self.audio_file = AudioFileSource(self.wavFile)
            self.blocks_wavfile_source_0 = self.audio_file.block
        else:
            # Create dummy source if no valid audio file
            self.blocks_wavfile_source_0 = blocks.null_source(gr.sizeof_float*1)

        self.blocks_selector_0 = blocks.selector(gr.sizeof_float*1,inputSelect,0)
        self.blocks_selector_0.set_enabled(True)
        self.blocks_null_source_0 = blocks.null_source(gr.sizeof_float*1)
        self.analog_sig_source_x_0 = analog.sig_source_f(samp_rate, analog.GR_COS_WAVE, sineFreq, 1, 0, 0)
        self.analog_frequency_modulator_fc_0 = analog.frequency_modulator_fc(2*pi*(freqDev*1000)/samp_rate)


        ##################################################
        # Connections
        ##################################################
        self.connect((self.analog_frequency_modulator_fc_0, 0), (self.qtgui_const_sink_x_0, 0))
        self.connect((self.analog_frequency_modulator_fc_0, 0), (self.qtgui_freq_sink_x_0, 0))
        self.connect((self.analog_frequency_modulator_fc_0, 0), (self.radio_sink, 0))
        self.connect((self.analog_sig_source_x_0, 0), (self.blocks_selector_0, 1))
        self.connect((self.blocks_null_source_0, 0), (self.blocks_selector_0, 2))
        self.connect((self.blocks_selector_0, 0), (self.analog_frequency_modulator_fc_0, 0))
        self.connect((self.blocks_selector_0, 0), (self.qtgui_time_sink_x_0, 0))
        self.connect((self.blocks_wavfile_source_0, 0), (self.rational_resampler_xxx_0_0, 0))
        self.connect((self.rational_resampler_xxx_0_0, 0), (self.blocks_selector_0, 0))


    def closeEvent(self, event):
        self.settings = Qt.QSettings("GNU Radio", "fmAudioRecordedGenerator")
        self.settings.setValue("geometry", self.saveGeometry())
        self.stop()
        self.wait()
        # An MP3's ffmpeg would otherwise sit on a full pipe until the
        # launcher exits - see apps/audio_file.py.
        if getattr(self, 'audio_file', None) is not None:
            self.audio_file.close()

        event.accept()

    def get_sineFreqDefault(self):
        return self.sineFreqDefault

    def set_sineFreqDefault(self, sineFreqDefault):
        self.sineFreqDefault = sineFreqDefault
        self.set_sineFreq(self.sineFreqDefault)

    def get_inputSelectDefault(self):
        return self.inputSelectDefault

    def set_inputSelectDefault(self, inputSelectDefault):
        self.inputSelectDefault = inputSelectDefault
        self.set_inputSelect(self.inputSelectDefault)

    def get_freqDevDefault(self):
        return self.freqDevDefault

    def set_freqDevDefault(self, freqDevDefault):
        self.freqDevDefault = freqDevDefault
        self.set_freqDev(self.freqDevDefault)

    def get_cfDefault(self):
        return self.cfDefault

    def set_cfDefault(self, cfDefault):
        self.cfDefault = cfDefault
        self.set_centerFreq(self.cfDefault)

    def get_sineFreq(self):
        return self.sineFreq

    def set_sineFreq(self, sineFreq):
        self.sineFreq = sineFreq
        self.analog_sig_source_x_0.set_frequency(self.sineFreq)

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.analog_frequency_modulator_fc_0.set_sensitivity(2*pi*(self.freqDev*1000)/self.samp_rate)
        self.analog_sig_source_x_0.set_sampling_freq(self.samp_rate)
        self.qtgui_freq_sink_x_0.set_frequency_range(self.centerFreq*1e6, self.samp_rate)
        self.qtgui_time_sink_x_0.set_samp_rate(self.samp_rate)
        if self.radio_type == 'usrp':
            self.radio_sink.set_samp_rate(self.samp_rate)
        else:
            self.radio_sink.set_sample_rate(0, self.samp_rate)

    def get_modName(self):
        return self.modName

    def set_modName(self, modName):
        self.modName = modName
        Qt.QMetaObject.invokeMethod(self._modName_label, "setText", Qt.Q_ARG("QString", str(self._modName_formatter(self.modName))))

    def get_inputSelect(self):
        return self.inputSelect

    def set_inputSelect(self, inputSelect):
        self.inputSelect = inputSelect
        self._inputSelect_callback(self.inputSelect)
        self.blocks_selector_0.set_input_index(self.inputSelect)

    def get_freqDev(self):
        return self.freqDev

    def set_freqDev(self, freqDev):
        self.freqDev = freqDev
        self.analog_frequency_modulator_fc_0.set_sensitivity(2*pi*(self.freqDev*1000)/self.samp_rate)

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

    def get_centerFreq(self):
        return self.centerFreq

    def set_centerFreq(self, centerFreq):
        self.centerFreq = centerFreq
        self.qtgui_freq_sink_x_0.set_frequency_range(self.centerFreq*1e6, self.samp_rate)
        if self.radio_type == 'usrp':
            self.radio_sink.set_center_freq(self.centerFreq*1e6, 0)
        else:
            self.radio_sink.set_frequency(0, self.centerFreq*1e6)




def main(top_block_cls=fmAudioRecordedGenerator, options=None, app=None, config_values=None):

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
