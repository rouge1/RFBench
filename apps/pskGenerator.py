#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: PSK Signal Generator
# Author: instructor
# Description: User-selectable number of bits per symbol (2, 4, 8)
# GNU Radio version: 3.10.1.1

from packaging.version import Version as StrictVersion #type: ignore

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
import numpy as np #type: ignore
import sip #type: ignore
from gnuradio import analog #type: ignore
from gnuradio import blocks #type: ignore
from gnuradio import digital #type: ignore
from gnuradio import eng_notation #type: ignore
from gnuradio import filter #type: ignore
from gnuradio import gr #type: ignore
from gnuradio import qtgui #type: ignore
from gnuradio import soapy, uhd  # type: ignore
from gnuradio.fft import window #type: ignore
from gnuradio.filter import firdes #type: ignore
from gnuradio.qtgui import Range, RangeWidget #type: ignore
from PyQt5 import Qt #type: ignore
from PyQt5 import QtCore #type: ignore
from PyQt5.QtCore import pyqtSlot #type: ignore

# Local imports
from apps.utils import (apply_dark_theme, apply_flowgraph_theme, radio_label,
                        read_settings, update_app_config, power_percent,
                        resolve_power_range, scale_power, SPECTRUM_Y_AXIS,
                        FrequencyChooser, frequency_range)

class ConfigDialog(Qt.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PSK Signal Generator Configuration")
        self.layout = Qt.QVBoxLayout(self) 
        self.config_dir = "config"
        self.config_file = os.path.join(self.config_dir, "pskGenerator_config.json")
        
        # Read settings from window_settings.json
        settings = read_settings()
        self.usrp_ip = settings.get('usrp_ip', '')
        self.radio_type = settings.get('radio_type', 'hackrf')

        # Add OK/Cancel buttons
        self.button_box = Qt.QDialogButtonBox(
            Qt.QDialogButtonBox.Ok | Qt.QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        
        # Create all controls
        self.create_radio_label()
        self.create_frequency_control()
        self.create_power_control()
        self.create_modulation_controls()
        
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
        # PSK Mode selector
        self.psk_combo = Qt.QComboBox()
        self.psk_combo.addItems(["BPSK", "QPSK", "8PSK"])
        self.layout.addWidget(Qt.QLabel("PSK Mode:"))
        self.layout.addWidget(self.psk_combo)
        
        # Symbol rate control
        self.sym_rate = Qt.QSpinBox()
        self.sym_rate.setRange(1, 500)
        self.sym_rate.setValue(100)
        self.sym_rate.setSuffix(" kHz")
        self.layout.addWidget(Qt.QLabel("Symbol Rate:"))
        self.layout.addWidget(self.sym_rate)

    # Add new methods for config handling
    def load_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                    
                self.cf_chooser.setValue(config.get('center_freq', 300))
                self.pwr_slider.setValue(power_percent(config.get('power_level'), 50))
                self.psk_combo.setCurrentIndex(config.get('psk_mode', 0))
                self.sym_rate.setValue(config.get('symbol_rate', 100))
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
            'psk_mode': self.psk_combo.currentIndex(),
            'symbol_rate': self.sym_rate.value()
        }
        
        update_app_config(self.config_file, config)

    # Modify accept method to save config
    def accept(self):
        self.save_config()
        super().accept()

    def get_values(self):
        ipXmitAddr = self.usrp_ip if self.radio_type == 'usrp' else ''
        
        return {
            'radio_type': self.radio_type,
            'ipXmitAddr': ipXmitAddr,
            'cf': self.cf_chooser.value(),
            'pwr': self.pwr_slider.value(),
            'pskMode': self.psk_combo.currentIndex() + 1,  # 1=BPSK, 2=QPSK, 3=8PSK
            'symRate': self.sym_rate.value()
        }

class pskGenerator(gr.top_block, Qt.QWidget):
    # What this window's own controls change that its dialog should
    # open on next time - see apps/utils.py: save_flowgraph_settings.
    SAVED_SETTINGS = {'power_level': 'rfPwr',
                      'center_freq': 'cf'}

    def __init__(self, config_values=None):
        gr.top_block.__init__(self, "PSK Signal Generator", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("PSK Signal Generator")
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

        self.settings = Qt.QSettings("GNU Radio", "pskGenerator")

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
            
        # Assign configuration values
        radio_type = values.get('radio_type', 'hackrf')
        ipXmitAddr = values['ipXmitAddr']
        cf = values['cf']
        pwr = values['pwr']
        pskMode = values['pskMode']
        symRate = values['symRate']

        # Calculate bits per symbol based on PSK mode
        bitsPerSym = pskMode if pskMode <= 2 else 3  # BPSK=1, QPSK=2, 8PSK=3
        
        # Set modulation name
        modNameDefault = f"{2**bitsPerSym}PSK"

        ##################################################
        # Variables
        ##################################################
        self.symRate = symRate 
        self.samp_rate = samp_rate = 5e6
        self.rfPwrDefault = rfPwrDefault = pwr
        self.cfDefault = cfDefault = cf
        self.bitsPerSym = bitsPerSym
        self.alphaDefault = alphaDefault = 0.35  # Add default alpha value
        self.rrcOption = rrcOption = 1  # Add default RRC filter option
        self.actualSymRate = actualSymRate = samp_rate/int(samp_rate/symRate/1000)/1000
        self.sps = sps = samp_rate/actualSymRate/1000
        self.rfPwr = rfPwr = rfPwrDefault
        self.outputIpAddr = outputIpAddr = ipXmitAddr
        self.modName = modName = modNameDefault
        self.cf = cf
        self.bitRate = bitRate = samp_rate/int(samp_rate/symRate/1000)/1000*bitsPerSym
        self.alphaVal = alphaVal = alphaDefault
        self.radio_type = radio_type

        ##################################################
        # Blocks
        ##################################################
        self._symRate_tool_bar = Qt.QToolBar(self)
        self._symRate_tool_bar.addWidget(Qt.QLabel("Symbol Rate (kHz)" + ": "))
        self._symRate_line_edit = Qt.QLineEdit(str(self.symRate))
        self._symRate_tool_bar.addWidget(self._symRate_line_edit)
        self._symRate_line_edit.returnPressed.connect(
            lambda: self.set_symRate(eng_notation.str_to_num(str(self._symRate_line_edit.text()))))
        self.top_grid_layout.addWidget(self._symRate_tool_bar, 2, 0, 1, 3)
        for r in range(2, 3):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 3):
            self.top_grid_layout.setColumnStretch(c, 1)
        # Create the options list
        self._rrcOption_options = [0, 1]
        # Create the labels list
        self._rrcOption_labels = ['Filter Off', 'Filter On']
        # Create the combo box
        # Create the radio buttons
        self._rrcOption_group_box = Qt.QGroupBox("RRC Filter On / Off" + ": ")
        self._rrcOption_box = Qt.QHBoxLayout()
        class variable_chooser_button_group(Qt.QButtonGroup):
            def __init__(self, parent=None):
                Qt.QButtonGroup.__init__(self, parent)
            @pyqtSlot(int)
            def updateButtonChecked(self, button_id):
                self.button(button_id).setChecked(True)
        self._rrcOption_button_group = variable_chooser_button_group()
        self._rrcOption_group_box.setLayout(self._rrcOption_box)
        for i, _label in enumerate(self._rrcOption_labels):
            radio_button = Qt.QRadioButton(_label)
            self._rrcOption_box.addWidget(radio_button)
            self._rrcOption_button_group.addButton(radio_button, i)
        self._rrcOption_callback = lambda i: Qt.QMetaObject.invokeMethod(self._rrcOption_button_group, "updateButtonChecked", Qt.Q_ARG("int", self._rrcOption_options.index(i)))
        self._rrcOption_callback(self.rrcOption)
        self._rrcOption_button_group.buttonClicked[int].connect(
            lambda i: self.set_rrcOption(self._rrcOption_options[i]))
        self.top_grid_layout.addWidget(self._rrcOption_group_box, 0, 7, 1, 3)
        for r in range(0, 1):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(7, 10):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._rfPwr_range = Range(0, 100, 1, rfPwrDefault, 200)
        self._rfPwr_win = RangeWidget(self._rfPwr_range, self.set_rfPwr, "RF Output Power (%)", "counter_slider", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._rfPwr_win, 1, 0, 1, 4)
        for r in range(1, 2):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 4):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._cf_range = frequency_range(50, 2100, 0.01, cfDefault, 200)
        self._cf_win = RangeWidget(self._cf_range, self.set_cf, "Center Frequency (MHz)", "counter", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._cf_win, 0, 0, 1, 5)
        for r in range(0, 1):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 5):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._alphaVal_range = Range(0.01, 1, 0.01, alphaDefault, 200)
        self._alphaVal_win = RangeWidget(self._alphaVal_range, self.set_alphaVal, "RRC Alpha Value", "counter", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._alphaVal_win, 2, 8, 1, 2)
        for r in range(2, 3):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(8, 10):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._actualSymRate_tool_bar = Qt.QToolBar(self)

        if None:
            self._actualSymRate_formatter = None
        else:
            self._actualSymRate_formatter = lambda x: eng_notation.num_to_str(x)

        self._actualSymRate_tool_bar.addWidget(Qt.QLabel("Actual Symbol Rate (kHz): "))
        self._actualSymRate_label = Qt.QLabel(str(self._actualSymRate_formatter(self.actualSymRate)))
        self._actualSymRate_tool_bar.addWidget(self._actualSymRate_label)
        self.top_grid_layout.addWidget(self._actualSymRate_tool_bar, 2, 3, 1, 3)
        for r in range(2, 3):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(3, 6):
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
                ",".join(('addr='+outputIpAddr, '')),
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
        self.qtgui_time_sink_x_0 = qtgui.time_sink_c(
            5000, #size
            samp_rate, #samp_rate
            'IQ Time-Domain', #name
            1, #number of inputs
            None # parent
        )
        self.qtgui_time_sink_x_0.set_update_time(0.10)
        self.qtgui_time_sink_x_0.set_y_axis(-1, 1)

        self.qtgui_time_sink_x_0.set_y_label('Amplitude', "")

        self.qtgui_time_sink_x_0.enable_tags(True)
        self.qtgui_time_sink_x_0.set_trigger_mode(qtgui.TRIG_MODE_FREE, qtgui.TRIG_SLOPE_POS, 0.0, 0, 0, "")
        self.qtgui_time_sink_x_0.enable_autoscale(False)
        self.qtgui_time_sink_x_0.enable_grid(False)
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
        self.top_grid_layout.addWidget(self._qtgui_time_sink_x_0_win, 3, 0, 5, 7)
        for r in range(3, 8):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 7):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.qtgui_freq_sink_x_0 = qtgui.freq_sink_c(
            4096, #size
            window.WIN_BLACKMAN_hARRIS, #wintype
            cf*1e6, #fc
            samp_rate, #bw
            'RF Spectrum', #name
            1,
            None # parent
        )
        self.qtgui_freq_sink_x_0.set_update_time(0.10)
        self.qtgui_freq_sink_x_0.set_y_axis(*SPECTRUM_Y_AXIS)
        self.qtgui_freq_sink_x_0.set_y_label('Relative Gain', 'dB')
        self.qtgui_freq_sink_x_0.set_trigger_mode(qtgui.TRIG_MODE_FREE, 0.0, 0, "")
        self.qtgui_freq_sink_x_0.enable_autoscale(False)
        self.qtgui_freq_sink_x_0.enable_grid(True)
        self.qtgui_freq_sink_x_0.set_fft_average(0.1)
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
            4000, #size
            'RF Constellation', #name
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
        self.top_grid_layout.addWidget(self._modName_tool_bar, 0, 5, 1, 2)
        for r in range(0, 1):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(5, 7):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.filter_fft_rrc_filter_0 = filter.fft_filter_ccc(1, firdes.root_raised_cosine(1, samp_rate, actualSymRate*1000, alphaVal, int(11*sps)), 1)
        self.digital_glfsr_source_x_0 = digital.glfsr_source_b(31, True, 0b1100000000010000000000010000000, 1001)
        self.blocks_uchar_to_float_0 = blocks.uchar_to_float()
        self.blocks_selector_0 = blocks.selector(gr.sizeof_gr_complex*1,int(rrcOption),0)
        self.blocks_selector_0.set_enabled(True)
        self.blocks_repeat_0 = blocks.repeat(gr.sizeof_float*1, int(samp_rate/symRate/1000))
        self.blocks_pack_k_bits_bb_0 = blocks.pack_k_bits_bb(bitsPerSym)
        self.blocks_multiply_const_vxx_2 = blocks.multiply_const_cc(0.95)
        self.blocks_multiply_const_vxx_1 = blocks.multiply_const_cc(0.9*np.exp(1j*pi/(2**(bitsPerSym-0))))
        self.blocks_multiply_const_vxx_0 = blocks.multiply_const_cc(1/np.sqrt(2))
        self._bitRate_tool_bar = Qt.QToolBar(self)

        if None:
            self._bitRate_formatter = None
        else:
            self._bitRate_formatter = lambda x: eng_notation.num_to_str(x)

        self._bitRate_tool_bar.addWidget(Qt.QLabel("Bit Rate (kHz): "))
        self._bitRate_label = Qt.QLabel(str(self._bitRate_formatter(self.bitRate)))
        self._bitRate_tool_bar.addWidget(self._bitRate_label)
        self.top_grid_layout.addWidget(self._bitRate_tool_bar, 2, 6, 1, 2)
        for r in range(2, 3):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(6, 8):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.analog_phase_modulator_fc_0 = analog.phase_modulator_fc(2*pi/(2**bitsPerSym))


        ##################################################
        # Connections
        ##################################################
        self.connect((self.analog_phase_modulator_fc_0, 0), (self.blocks_selector_0, 0))
        self.connect((self.analog_phase_modulator_fc_0, 0), (self.filter_fft_rrc_filter_0, 0))
        self.connect((self.blocks_multiply_const_vxx_0, 0), (self.blocks_multiply_const_vxx_2, 0))
        self.connect((self.blocks_multiply_const_vxx_0, 0), (self.qtgui_freq_sink_x_0, 0))
        self.connect((self.blocks_multiply_const_vxx_0, 0), (self.qtgui_time_sink_x_0, 0))
        self.connect((self.blocks_multiply_const_vxx_1, 0), (self.blocks_multiply_const_vxx_0, 0))
        self.connect((self.blocks_multiply_const_vxx_1, 0), (self.qtgui_const_sink_x_0, 0))
        self.connect((self.blocks_multiply_const_vxx_2, 0), (self.radio_sink, 0))
        self.connect((self.blocks_pack_k_bits_bb_0, 0), (self.blocks_uchar_to_float_0, 0))
        self.connect((self.blocks_repeat_0, 0), (self.analog_phase_modulator_fc_0, 0))
        self.connect((self.blocks_selector_0, 0), (self.blocks_multiply_const_vxx_1, 0))
        self.connect((self.blocks_uchar_to_float_0, 0), (self.blocks_repeat_0, 0))
        self.connect((self.digital_glfsr_source_x_0, 0), (self.blocks_pack_k_bits_bb_0, 0))
        self.connect((self.filter_fft_rrc_filter_0, 0), (self.blocks_selector_0, 1))


    def closeEvent(self, event):
        self.settings = Qt.QSettings("GNU Radio", "pskGenerator")
        self.settings.setValue("geometry", self.saveGeometry())
        self.stop()
        self.wait()

        event.accept()

    def get_symRate(self):
        return self.symRate

    def set_symRate(self, symRate):
        self.symRate = symRate
        self.set_actualSymRate(self.samp_rate/int(self.samp_rate/self.symRate/1000)/1000)
        self.set_bitRate(self.samp_rate/int(self.samp_rate/self.symRate/1000)/1000*self.bitsPerSym)
        Qt.QMetaObject.invokeMethod(self._symRate_line_edit, "setText", Qt.Q_ARG("QString", eng_notation.num_to_str(self.symRate)))
        self.blocks_repeat_0.set_interpolation(int(self.samp_rate/self.symRate/1000))

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.set_actualSymRate(self.samp_rate/int(self.samp_rate/self.symRate/1000)/1000)
        self.set_bitRate(self.samp_rate/int(self.samp_rate/self.symRate/1000)/1000*self.bitsPerSym)
        self.set_sps(self.samp_rate/self.actualSymRate/1000)
        self.blocks_repeat_0.set_interpolation(int(self.samp_rate/self.symRate/1000))
        self.filter_fft_rrc_filter_0.set_taps(firdes.root_raised_cosine(1, self.samp_rate, self.actualSymRate*1000, self.alphaVal, int(11*self.sps)))
        self.qtgui_freq_sink_x_0.set_frequency_range(self.cf*1e6, self.samp_rate)
        self.qtgui_time_sink_x_0.set_samp_rate(self.samp_rate)
        if self.radio_type == 'usrp':
            self.radio_sink.set_samp_rate(self.samp_rate)
        else:
            self.radio_sink.set_sample_rate(0, self.samp_rate)

    def get_rfPwrDefault(self):
        return self.rfPwrDefault

    def set_rfPwrDefault(self, rfPwrDefault):
        self.rfPwrDefault = rfPwrDefault
        self.set_rfPwr(self.rfPwrDefault)

    def get_cfDefault(self):
        return self.cfDefault

    def set_cfDefault(self, cfDefault):
        self.cfDefault = cfDefault
        self.set_cf(self.cfDefault)

    def get_bitsPerSym(self):
        return self.bitsPerSym

    def set_bitsPerSym(self, bitsPerSym):
        self.bitsPerSym = bitsPerSym
        self.set_bitRate(self.samp_rate/int(self.samp_rate/self.symRate/1000)/1000*self.bitsPerSym)
        self.analog_phase_modulator_fc_0.set_sensitivity(2*pi/(2**self.bitsPerSym))
        self.blocks_multiply_const_vxx_1.set_k(0.9*np.exp(1j*pi/(2**(self.bitsPerSym-0))))

    def get_alphaDefault(self):
        return self.alphaDefault

    def set_alphaDefault(self, alphaDefault):
        self.alphaDefault = alphaDefault
        self.set_alphaVal(self.alphaDefault)

    def get_actualSymRate(self):
        return self.actualSymRate

    def set_actualSymRate(self, actualSymRate):
        self.actualSymRate = actualSymRate
        Qt.QMetaObject.invokeMethod(self._actualSymRate_label, "setText", Qt.Q_ARG("QString", str(self._actualSymRate_formatter(self.actualSymRate))))
        self.set_sps(self.samp_rate/self.actualSymRate/1000)
        self.filter_fft_rrc_filter_0.set_taps(firdes.root_raised_cosine(1, self.samp_rate, self.actualSymRate*1000, self.alphaVal, int(11*self.sps)))

    def get_sps(self):
        return self.sps

    def set_sps(self, sps):
        self.sps = sps
        self.filter_fft_rrc_filter_0.set_taps(firdes.root_raised_cosine(1, self.samp_rate, self.actualSymRate*1000, self.alphaVal, int(11*self.sps)))

    def get_rrcOption(self):
        return self.rrcOption

    def set_rrcOption(self, rrcOption):
        self.rrcOption = rrcOption
        self._rrcOption_callback(self.rrcOption)
        self.blocks_selector_0.set_input_index(int(self.rrcOption))

    def get_rfPwr(self):
        return self.rfPwr

    def set_rfPwr(self, rfPwr):
        self.rfPwr = rfPwr
        self.blocks_multiply_const_vxx_2.set_k(0.95)
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

    def get_cf(self):
        return self.cf

    def set_cf(self, cf):
        self.cf = cf
        self.qtgui_freq_sink_x_0.set_frequency_range(self.cf*1e6, self.samp_rate)
        if self.radio_type == 'usrp':
            self.radio_sink.set_center_freq(self.cf*1e6, 0)
        else:
            self.radio_sink.set_frequency(0, self.cf*1e6)

    def get_bitRate(self):
        return self.bitRate

    def set_bitRate(self, bitRate):
        self.bitRate = bitRate
        Qt.QMetaObject.invokeMethod(self._bitRate_label, "setText", Qt.Q_ARG("QString", str(self._bitRate_formatter(self.bitRate))))

    def get_alphaVal(self):
        return self.alphaVal

    def set_alphaVal(self, alphaVal):
        self.alphaVal = alphaVal
        self.filter_fft_rrc_filter_0.set_taps(firdes.root_raised_cosine(1, self.samp_rate, self.actualSymRate*1000, self.alphaVal, int(11*self.sps)))




def main(top_block_cls=pskGenerator, options=None, app=None, config_values=None):
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
