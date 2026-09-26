#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: ATSC Transmitter
# GNU Radio version: 3.10.1.1

from packaging.version import Version as StrictVersion

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
import math
import signal
import sys
import time
import os
import json

# Third party imports
from PyQt5 import Qt, QtCore # type: ignore
import sip # type: ignore # type: ignore
import pmt # type: ignore

from gnuradio import blocks, dtv, filter, gr, qtgui, soapy, uhd  # type: ignore
from gnuradio.filter import firdes # type: ignore
from gnuradio.fft import window # type: ignore
from gnuradio.qtgui import Range, RangeWidget # type: ignore

# Local imports
from apps.atsc_rx_core import channel_center_mhz, tv_channel_items
from apps.atsc_source import (COLOUR_BARS, TransportStream, atsc_video_files,
                              describe, needs_encoding)
from apps.ntsc_source import have_ffmpeg
from apps.utils import (CONFIG_DIR, apply_dark_theme, apply_flowgraph_theme,
                        radio_label,
                        read_settings, update_app_config, power_percent,
                        resolve_power_range, scale_power, SPECTRUM_Y_AXIS,
                        adopt_legacy_config, FrequencyChooser, frequency_range)

# The channel this bench uses. Only a default: the dialog remembers what was
# last set, and the receiver opens on the same one.
DEFAULT_CHANNEL = 24            # 533 MHz

# Baseband amplitude out of the modulator, before the radio's own level
# control. See the note where it is applied: 8VSB peaks ~8.7 dB above its rms,
# and the radios take 1.0 as I/Q full scale.
BASEBAND_SCALE = 0.85

class ConfigDialog(Qt.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ATSC Video Transmitter Configuration")
        self.layout = Qt.QVBoxLayout(self)
        self.config_dir = CONFIG_DIR
        self.config_file = os.path.join(self.config_dir, "atscXmitter_config.json")
        # Older builds saved this dialog under atsc_config.json;
        # fold that in once so nothing the user set is lost.
        adopt_legacy_config(self.config_dir, "atsc_config.json", self.config_file)
        
        # Read settings from window_settings.json
        settings = read_settings()
        self.usrp_ip = settings.get('usrp_ip', '')
        self.radio_type = settings.get('radio_type', 'hackrf')
        
        # Add OK/Cancel buttons
        self.button_box = Qt.QDialogButtonBox(
            Qt.QDialogButtonBox.Ok | Qt.QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        
        # Create widgets
        self.create_radio_label()
        self.create_frequency_control()
        self.create_power_control()
        self.create_file_selector()
        
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
        # This was a bare QSlider in whole megahertz, 50 to 2200. Rendered a
        # few hundred pixels wide that is about seven megahertz per pixel of
        # mouse travel, so most frequencies could not be reached at all -
        # asked for 533 MHz, the nearest it would go was 539. Type it, pick
        # the channel, or drag; see FrequencyChooser.
        self.cf_chooser = FrequencyChooser(
            minimum=50.0, maximum=2200.0,
            value=channel_center_mhz(DEFAULT_CHANNEL),
            channels=tv_channel_items())
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

    def create_file_selector(self):
        """Colour bars, then every video in the media folder, one per clip.

        This offered only ``.ts`` files, because the flowgraph plays a
        transport stream. That looked complete here, where every clip has a
        ``.ts`` twin, and gave TVAdemo - the machine that transmits, which
        has the clips only as ``.mp4`` - the test pattern and nothing else.
        A clip with no ``.ts`` is encoded as it plays now; see
        ``apps/atsc_source.py``.

        The bars come first and need no media at all, as in the NTSC and FM
        video transmitters. Before them an empty or unset media folder left
        OK greyed out, which on the Windows laptop, whose folder holds only
        MP3s, meant the app could not be started. ffmpeg makes the bars, so
        without it they are listed but cannot be chosen.
        """
        self.file_combo = Qt.QComboBox()
        ok_button = self.button_box.button(Qt.QDialogButtonBox.Ok)
        model = self.file_combo.model()

        def note(text):
            """A line that explains something and cannot be chosen."""
            self.file_combo.addItem(text)
            model.item(self.file_combo.count() - 1).setEnabled(False)

        # The media directory, like every other app. This used to read a
        # tsFileList.txt from the working directory, which does not exist in
        # the repo - so the combo always fell into the except branch, OK stayed
        # disabled, and the app could not be launched at all.
        settings = read_settings()
        self.media_dir = settings.get('media_directory', '')
        ffmpeg = have_ffmpeg()

        if ffmpeg:
            self.file_combo.addItem("Colour bars (built in)", COLOUR_BARS)
        else:
            note("Colour bars need ffmpeg, which is not installed here")

        if self.media_dir and os.path.isdir(self.media_dir):
            try:
                clips = atsc_video_files(self.media_dir)
                if clips:
                    self.file_combo.insertSeparator(self.file_combo.count())
                for display_name, path in clips:
                    self.file_combo.addItem(display_name, path)
                # Without ffmpeg only the .ts files can play. A list that just
                # left the rest out would look like clips missing from the
                # folder.
                if not ffmpeg:
                    unplayable = (len(atsc_video_files(self.media_dir,
                                                       encode=True))
                                  - len(clips))
                    if unplayable:
                        self.file_combo.insertSeparator(self.file_combo.count())
                        note(f"{unplayable} more need ffmpeg, which is not "
                             "installed here")
            except Exception as e:
                note(f"Could not read the media directory: {e}")

        # Open on the first thing that can actually be sent. A disabled line
        # can still be the current one, and would be sent as nothing.
        playable = [i for i in range(self.file_combo.count())
                    if self.file_combo.itemData(i)]
        if playable:
            self.file_combo.setCurrentIndex(playable[0])
            ok_button.setEnabled(self.radio_type in ('hackrf', 'vsg') or bool(self.usrp_ip))
            ok_button.setGraphicsEffect(None)
        else:
            ok_button.setEnabled(False)
            opacity_effect = Qt.QGraphicsOpacityEffect()
            opacity_effect.setOpacity(0.30)
            ok_button.setGraphicsEffect(opacity_effect)

        self.layout.addWidget(Qt.QLabel("Video Source:"))
        self.layout.addWidget(self.file_combo)

    def load_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                    
                # Saved as an int by every version before the frequency
                # control could express anything else; setValue takes both.
                self.cf_chooser.setValue(config.get('center_freq',
                                                    channel_center_mhz(DEFAULT_CHANNEL)))
                self.pwr_slider.setValue(power_percent(config.get('power_level'), 50))
                
                # Match by clip name, not by path or extension: the media
                # directory can move, and a clip saved where it was a .ts
                # should still be found where it is only a .mp4. 'ts_file' is
                # what this was saved as when a .ts was all it took.
                # The exact file wins if it is still offered: now that
                # subfolders are searched, two folders can each hold a clip
                # of the same name, and matching by name alone would take
                # whichever happened to be listed first.
                saved_file = config.get('video_file') or config.get('ts_file')
                if saved_file:
                    paths = [self.file_combo.itemData(i)
                             for i in range(self.file_combo.count())]
                    stem = os.path.splitext(os.path.basename(saved_file))[0]
                    match = next((i for i, p in enumerate(paths)
                                  if p and os.path.normcase(p)
                                  == os.path.normcase(saved_file)), None)
                    if match is None:
                        match = next((i for i, p in enumerate(paths)
                                      if p and os.path.splitext(
                                          os.path.basename(p))[0] == stem),
                                     None)
                    if match is not None:
                        self.file_combo.setCurrentIndex(match)
            except:
                pass
        else:
            os.makedirs(self.config_dir, exist_ok=True)

    def save_config(self):
        path = self.file_combo.currentData()

        config = {
            'center_freq': self.cf_chooser.value(),
            'power_level': self.pwr_slider.value(),
            'video_file': os.path.basename(path) if path else None
        }
        
        update_app_config(self.config_file, config)

    def accept(self):
        self.save_config()
        super().accept()

    def get_values(self):
        ipXmitAddr = self.usrp_ip if self.radio_type == 'usrp' else ''
        # rfGain/atten used to be computed here from the old dBm-labelled
        # slider. The slider is 0-100% now and the flowgraph goes through
        # scale_power(), so those two were dead the moment that changed.
        return {
            'radio_type': self.radio_type,
            'ipXmitAddr': ipXmitAddr,
            'cf': self.cf_chooser.value(),
            'pwr': self.pwr_slider.value(),
            'video_file': self.file_combo.currentData()
        }

class atscXmitter2(gr.top_block, Qt.QWidget):
    # What this window's own controls change that its dialog should
    # open on next time - see apps/utils.py: save_flowgraph_settings.
    SAVED_SETTINGS = {'power_level': 'rfPwr',
                      'center_freq': 'cf'}

    def __init__(self, config_values=None):
        gr.top_block.__init__(self, "ATSC Video Transmitter", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("ATSC Video Transmitter")
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

        self.settings = Qt.QSettings("GNU Radio", "atscXmitter2")

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
        
        # Assign all values
        radio_type = values.get('radio_type', 'hackrf')
        ipXmitAddr = values['ipXmitAddr']
        cf = values['cf']
        pwr = values['pwr']
        # Nothing named - a config from _run.py, say - sends the bars.
        self.video_file = (values.get('video_file') or values.get('ts_file')
                           or COLOUR_BARS)

        ##################################################
        # Variables
        ##################################################
        self.symbol_rate = symbol_rate = 4500000.0 / 286 * 684
        self.rfPwrDefault = rfPwrDefault = pwr
        self.cfDefault = cfDefault = cf
        self.atscFileName = atscFileName = self.video_file
        # 12 MS/s, not the 12.5 this once used: SoapyHackRF accepts only whole
        # megahertz between 1 and 20 and rejects anything else outright, so on
        # a HackRF the app died in the constructor with "Unsupported sample
        # rate" and could never transmit at all. 12 MS/s carries the 6 MHz
        # channel just as well and every backend here takes it.
        self.samp_rate = samp_rate = 12e6
        self.rfPwr = rfPwr = rfPwrDefault
        self.pilot_freq = pilot_freq = (6000000.0 - (symbol_rate / 2)) / 2
        self.outputIpAddr = outputIpAddr = ipXmitAddr
        self.modulation = modulation = 'ATSC'
        self.fileBeingBroadcast = fileBeingBroadcast = describe(atscFileName)
        self.cf = cf
        self.radio_type = radio_type

        ##################################################
        # Blocks
        ##################################################
        self._rfPwr_range = Range(0, 100, 1, rfPwrDefault, 200)
        self._rfPwr_win = RangeWidget(self._rfPwr_range, self.set_rfPwr, "RF Output Power (%)", "counter_slider", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._rfPwr_win, 1, 0, 1, 5)
        for r in range(1, 2):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 5):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._cf_range = frequency_range(50, 2200, 0.1, cfDefault, 200)
        self._cf_win = RangeWidget(self._cf_range, self.set_cf, "Center Frequency (MHz)", "counter", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._cf_win, 0, 0, 1, 5)
        for r in range(0, 1):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 5):
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
        # The first stage takes the 10.762237 MS/s symbol stream to exactly
        # 28.5 MS/s (x143/54); this one brings that to the radio rate. 8/19
        # lands on 12 MS/s exactly, where 25/57 landed on 12.5.
        self.rational_resampler_xxx_1 = filter.rational_resampler_ccc(
                interpolation=8,
                decimation=19,
                taps=[],
                fractional_bw=0)
        self.rational_resampler_xxx_0 = filter.rational_resampler_ccc(
                interpolation=143,
                decimation=54,
                taps=[],
                fractional_bw=0)
        self.qtgui_freq_sink_x_0 = qtgui.freq_sink_c(
            2048, #size
            window.WIN_BLACKMAN_hARRIS, #wintype
            cf*1e6, #fc
            samp_rate, #bw
            "", #name
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
        self.top_grid_layout.addWidget(self._qtgui_freq_sink_x_0_win, 3, 0, 10, 10)
        for r in range(3, 13):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 10):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._modulation_tool_bar = Qt.QToolBar(self)

        if None:
            self._modulation_formatter = None
        else:
            self._modulation_formatter = lambda x: str(x)

        self._modulation_tool_bar.addWidget(Qt.QLabel("Modulation: "))
        self._modulation_label = Qt.QLabel(str(self._modulation_formatter(self.modulation)))
        self._modulation_tool_bar.addWidget(self._modulation_label)
        self.top_grid_layout.addWidget(self._modulation_tool_bar, 0, 5, 1, 5)
        for r in range(0, 1):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(5, 10):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._fileBeingBroadcast_tool_bar = Qt.QToolBar(self)

        if None:
            self._fileBeingBroadcast_formatter = None
        else:
            self._fileBeingBroadcast_formatter = lambda x: str(x)

        self._fileBeingBroadcast_tool_bar.addWidget(Qt.QLabel("File Being Broadcast: "))
        self._fileBeingBroadcast_label = Qt.QLabel(str(self._fileBeingBroadcast_formatter(self.fileBeingBroadcast)))
        self._fileBeingBroadcast_tool_bar.addWidget(self._fileBeingBroadcast_label)
        self.top_grid_layout.addWidget(self._fileBeingBroadcast_tool_bar, 2, 0, 1, 5)
        for r in range(2, 3):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 5):
            self.top_grid_layout.setColumnStretch(c, 1)
        # 8VSB is noise-like: measured 8.7 dB peak-to-average, and the chain
        # came out at 1.007 peak against a radio that takes I/Q full scale as
        # 1.0 and clips above it - so the loudest samples were being flattened
        # before the signal ever left the box, and the longer it runs the
        # further into the Gaussian tail it reaches. Backing the baseband off
        # leaves 10 dB of headroom; the analog stage sets the actual power.
        self.blocks_multiply_const_vxx_0 = blocks.multiply_const_cc(BASEBAND_SCALE)
        self.fft_filter_xxx_0 = filter.fft_filter_ccc(1, firdes.root_raised_cosine(0.11, symbol_rate, symbol_rate/2, 0.1152, 200), 1)
        self.fft_filter_xxx_0.declare_sample_delay(0)
        self.dtv_dvbs2_modulator_bc_0 = dtv.dvbs2_modulator_bc(
            dtv.FECFRAME_NORMAL,
            dtv.C1_4,
            dtv.MOD_8VSB,
            dtv.INTERPOLATION_OFF)
        self.dtv_atsc_trellis_encoder_0 = dtv.atsc_trellis_encoder()
        self.dtv_atsc_rs_encoder_0 = dtv.atsc_rs_encoder()
        self.dtv_atsc_randomizer_0 = dtv.atsc_randomizer()
        self.dtv_atsc_pad_0 = dtv.atsc_pad()
        self.dtv_atsc_interleaver_0 = dtv.atsc_interleaver()
        self.dtv_atsc_field_sync_mux_0 = dtv.atsc_field_sync_mux()
        self.blocks_vector_to_stream_1 = blocks.vector_to_stream(gr.sizeof_char*1, 1024)
        self.blocks_rotator_cc_0 = blocks.rotator_cc(((-3000000.0 + pilot_freq) / symbol_rate) * (math.pi * 2), False)
        self.blocks_keep_m_in_n_0 = blocks.keep_m_in_n(gr.sizeof_char, 832, 1024, 4)
        if needs_encoding(atscFileName):
            # A clip with no .ts of its own, encoded by ffmpeg as it plays and
            # looped inside ffmpeg, so this never reaches the end of it. Made
            # after the radio has opened, so a radio that fails to open does
            # not leave an ffmpeg running behind it.
            self.ts_stream = TransportStream(atscFileName)
            self.blocks_file_source_0 = blocks.file_descriptor_source(
                gr.sizeof_char*1, self.ts_stream.descriptor(), False)
        else:
            self.ts_stream = None
            self.blocks_file_source_0 = blocks.file_source(gr.sizeof_char*1, atscFileName, True, 0, 0)
            self.blocks_file_source_0.set_begin_tag(pmt.PMT_NIL)


        ##################################################
        # Connections
        ##################################################
        self.connect((self.blocks_file_source_0, 0), (self.dtv_atsc_pad_0, 0))
        self.connect((self.blocks_keep_m_in_n_0, 0), (self.dtv_dvbs2_modulator_bc_0, 0))
        self.connect((self.blocks_multiply_const_vxx_0, 0), (self.radio_sink, 0))
        self.connect((self.blocks_rotator_cc_0, 0), (self.fft_filter_xxx_0, 0))
        self.connect((self.blocks_vector_to_stream_1, 0), (self.blocks_keep_m_in_n_0, 0))
        self.connect((self.dtv_atsc_field_sync_mux_0, 0), (self.blocks_vector_to_stream_1, 0))
        self.connect((self.dtv_atsc_interleaver_0, 0), (self.dtv_atsc_trellis_encoder_0, 0))
        self.connect((self.dtv_atsc_pad_0, 0), (self.dtv_atsc_randomizer_0, 0))
        self.connect((self.dtv_atsc_randomizer_0, 0), (self.dtv_atsc_rs_encoder_0, 0))
        self.connect((self.dtv_atsc_rs_encoder_0, 0), (self.dtv_atsc_interleaver_0, 0))
        self.connect((self.dtv_atsc_trellis_encoder_0, 0), (self.dtv_atsc_field_sync_mux_0, 0))
        self.connect((self.dtv_dvbs2_modulator_bc_0, 0), (self.blocks_rotator_cc_0, 0))
        self.connect((self.fft_filter_xxx_0, 0), (self.rational_resampler_xxx_0, 0))
        self.connect((self.rational_resampler_xxx_0, 0), (self.rational_resampler_xxx_1, 0))
        self.connect((self.rational_resampler_xxx_1, 0), (self.blocks_multiply_const_vxx_0, 0))
        self.connect((self.rational_resampler_xxx_1, 0), (self.qtgui_freq_sink_x_0, 0))


    def closeEvent(self, event):
        self.settings = Qt.QSettings("GNU Radio", "atscXmitter2")
        self.settings.setValue("geometry", self.saveGeometry())
        self.stop()
        self.wait()
        self.close_stream()

        event.accept()

    def close_stream(self):
        """Stop the ffmpeg encoding the clip, once the flowgraph has stopped.

        Nothing reads its pipe after that, so left alone it would sit blocked
        on a full pipe for as long as the launcher stays open.
        """
        stream, self.ts_stream = getattr(self, 'ts_stream', None), None
        if stream is not None:
            stream.close()

    def get_symbol_rate(self):
        return self.symbol_rate

    def set_symbol_rate(self, symbol_rate):
        self.symbol_rate = symbol_rate
        self.set_pilot_freq((6000000.0 - (self.symbol_rate / 2)) / 2)
        self.blocks_rotator_cc_0.set_phase_inc(((-3000000.0 + self.pilot_freq) / self.symbol_rate) * (math.pi * 2))
        self.fft_filter_xxx_0.set_taps(firdes.root_raised_cosine(0.11, self.symbol_rate, self.symbol_rate/2, 0.1152, 200))

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

    def get_atscFileName(self):
        return self.atscFileName

    def set_atscFileName(self, atscFileName):
        self.atscFileName = atscFileName
        self.set_fileBeingBroadcast(self.atscFileName)
        self.blocks_file_source_0.open(self.atscFileName, True)

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.qtgui_freq_sink_x_0.set_frequency_range(self.cf*1e6, self.samp_rate)
        if self.radio_type == 'usrp':
            self.radio_sink.set_samp_rate(self.samp_rate)
        else:
            self.radio_sink.set_sample_rate(0, self.samp_rate)

    def get_rfPwr(self):
        return self.rfPwr

    def set_rfPwr(self, rfPwr):
        self.rfPwr = rfPwr
        # Baseband stays put - power is an analog setting now (see CLAUDE.md,
        # "Output Power"). This used to re-assert set_k(1) on every move.
        if self.radio_type == 'vsg':
            self.radio_sink.set_level(scale_power(self.rfPwr, self._power_range))
        elif self.radio_type == 'usrp':
            self.radio_sink.set_gain(scale_power(self.rfPwr, self._power_range), 0)
        else:
            self.radio_sink.set_gain(0, 'VGA', scale_power(self.rfPwr, self._power_range))

    def get_pilot_freq(self):
        return self.pilot_freq

    def set_pilot_freq(self, pilot_freq):
        self.pilot_freq = pilot_freq
        self.blocks_rotator_cc_0.set_phase_inc(((-3000000.0 + self.pilot_freq) / self.symbol_rate) * (math.pi * 2))

    def get_outputIpAddr(self):
        return self.outputIpAddr

    def set_outputIpAddr(self, outputIpAddr):
        self.outputIpAddr = outputIpAddr

    def get_modulation(self):
        return self.modulation

    def set_modulation(self, modulation):
        self.modulation = modulation
        Qt.QMetaObject.invokeMethod(self._modulation_label, "setText", Qt.Q_ARG("QString", str(self._modulation_formatter(self.modulation))))

    def get_fileBeingBroadcast(self):
        return self.fileBeingBroadcast

    def set_fileBeingBroadcast(self, fileBeingBroadcast):
        self.fileBeingBroadcast = fileBeingBroadcast
        Qt.QMetaObject.invokeMethod(self._fileBeingBroadcast_label, "setText", Qt.Q_ARG("QString", str(self._fileBeingBroadcast_formatter(self.fileBeingBroadcast))))

    def get_cf(self):
        return self.cf

    def set_cf(self, cf):
        self.cf = cf
        self.qtgui_freq_sink_x_0.set_frequency_range(self.cf*1e6, self.samp_rate)
        if self.radio_type == 'usrp':
            self.radio_sink.set_center_freq(self.cf*1e6, 0)
        else:
            self.radio_sink.set_frequency(0, self.cf*1e6)

def main(top_block_cls=atscXmitter2, options=None, app=None, config_values=None):
    own_app = app is None
    if app is None:
        if StrictVersion("4.5.0") <= StrictVersion(Qt.qVersion()) < StrictVersion("5.0.0"):
            style = gr.prefs().get_string('qtgui', 'style', 'raster')
            Qt.QApplication.setGraphicsSystem(style)
        app = Qt.QApplication(sys.argv)
        
        # Apply dark theme to the application
        apply_dark_theme(app)

    tb = top_block_cls(config_values)
    tb.start()
    tb.show()

    def sig_handler(sig=None, frame=None):
        def _signal_handler():
            tb.stop()
            tb.wait()
            tb.close_stream()
            app.quit()

        # Use QTimer to handle the signal in the Qt event loop
        Qt.QTimer.singleShot(0, _signal_handler)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    timer = Qt.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    # Inside the launcher, hand the window back instead of running a loop of
    # our own. This used to call app.exec_() regardless and replace the
    # window's closeEvent with one that called app.quit(). The launcher's loop
    # is already running, so exec_() returned -1 at once; the launcher got -1
    # rather than a window, so it never hooked the close to show itself
    # again; and closing the window quit the launcher's own event loop, which
    # took the whole launcher down with it. Closing now goes through the
    # class's closeEvent, which stops the flowgraph and its ffmpeg, and the
    # launcher wraps that to come back - as it does for every other app.
    if not own_app:
        return tb
    return app.exec_()

if __name__ == '__main__':
    main()

