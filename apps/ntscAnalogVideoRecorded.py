#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: NTSC Video Transmitter
# Author: student
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
from gnuradio import analog #type: ignore
from gnuradio import blocks #type: ignore
from gnuradio import filter #type: ignore
from gnuradio import gr #type: ignore
from gnuradio import qtgui #type: ignore
from gnuradio import soapy, uhd  # type: ignore
from gnuradio.fft import window #type: ignore
from gnuradio.filter import firdes #type: ignore
from gnuradio.qtgui import Range, RangeWidget #type: ignore
import pmt #type: ignore
from PyQt5 import Qt, QtCore #type: ignore
from PyQt5.QtCore import pyqtSlot #type: ignore
import sip #type: ignore

# Local imports
from fractions import Fraction

from apps.audio_file import AudioFileSource
from apps.media import AUDIO, choices
from apps.atsc_rx_core import channel_center_mhz, tv_channel_items
from apps.ntsc_source import (AudioTrack, TestPattern, VideoFile, dat_files,
                              dat_resample_ratio, has_audio, have_ffmpeg,
                              ntsc_source, video_files)
from apps.utils import (CONFIG_DIR, apply_dark_theme, apply_flowgraph_theme,
                        radio_label,
                        read_settings, update_app_config, power_percent,
                        resolve_power_range, scale_power, SPECTRUM_Y_AXIS,
                        FrequencyChooser, frequency_range)

# The channel this bench uses, as a default only.
DEFAULT_CHANNEL = 24            # 533 MHz

# --- System M modulation ----------------------------------------------------
#
# US television is *negative* modulation: the sync tip is peak carrier and
# white is nearly none of it. FCC 73.682 puts sync at 100%, blanking at 75%
# and peak white at 12.5%.
#
# The composite signal arrives with sync at 0.0 and white at 1.0, so
# carrier = 1 - 0.875*v maps the three straight onto each other - and lands
# blanking on 0.75 by itself, which is the check that the two scales agree.
CARRIER_AT_SYNC = 1.0
CARRIER_AT_WHITE = 0.125
#: Never let the carrier reach zero. Legal composite peaks at 120 IRE, which
#: maps to exactly zero, and a transmitter that switches its carrier off has
#: nothing for a receiver's sync or AGC to hold on to.
CARRIER_FLOOR = 0.02

#: Aural carrier amplitude against peak visual. FCC 73.682 sets aural power
#: at 10% of peak visual power, which is this in voltage. It was 0.8 here -
#: 8 dB too much sound, which steals headroom from the picture.
AURAL_AMPLITUDE = 0.316
#: Peak deviation of the aural carrier, and the visual/aural spacing.
AURAL_DEVIATION = 25e3
#: Sound is pre-emphasised like FM broadcast, and the receiver undoes it.
#: 75 us in the US; 50 us elsewhere.
AUDIO_PREEMPHASIS = 75e-6
#: Leave headroom: radios take 1.0 as full scale and clip above it.
BASEBAND_SCALE = 0.85

# --- where the carriers sit -------------------------------------------------
#
# All relative to the channel centre, which is what ``cf`` means here and in
# the ATSC apps. A 6 MHz channel puts the visual carrier 1.25 MHz above its
# lower edge - so 1.75 MHz below centre - and the aural carrier exactly
# 4.5 MHz above that.
VISUAL_CARRIER = -1.75e6
AURAL_CARRIER = 2.75e6
AURAL_SPACING = AURAL_CARRIER - VISUAL_CARRIER          # 4.5 MHz exactly

# Video is mixed onto its carrier in two steps with the vestigial-sideband
# filter between them, which looks like a pointless extra mixer and is not.
# The filter is a low pass at 2.475 MHz, and it does its work in the frame
# where the carrier sits at -1.725: it then passes 0.75 MHz below the
# carrier and 4.2 MHz above, which is the vestigial sideband and the full
# video bandwidth, exactly as System M specifies. Mixing straight to -1.75
# first would put both edges 25 kHz wrong.
VSB_MIX = -1.725e6
VSB_TRIM = VISUAL_CARRIER - VSB_MIX                     # the last -25 kHz
VESTIGIAL_BW = 2.475e6
VESTIGIAL_TRANSITION = 300e3

#: The whole signal is shifted down by this before the radio, so the
#: radio's own LO leakage at DC lands outside the channel instead of on top
#: of the colour subcarrier. The radio is then tuned this far above ``cf``.
LO_OFFSET = 6e6


class NtscModulator(gr.hier_block2):
    """Composite video in, vestigial-sideband RF baseband out.

    Kept as a block of its own so it can be driven with no radio attached -
    ``scripts/test_ntsc_transmit.py`` runs a picture through it and
    demodulates the result - and so the carrier arithmetic lives in one
    place rather than scattered through a flowgraph.
    """

    def __init__(self, sample_rate, polarity='negative'):
        gr.hier_block2.__init__(
            self, "ntsc_modulator",
            gr.io_signature(1, 1, gr.sizeof_float),
            gr.io_signature(1, 1, gr.sizeof_gr_complex))
        self.sample_rate = float(sample_rate)

        span = CARRIER_AT_SYNC - CARRIER_AT_WHITE
        negative = polarity != 'positive'
        # Composite (sync 0, white 1) -> carrier amplitude. Negative
        # modulation is a slope of -0.875 about an offset of 1.0, which puts
        # sync at 100%, blanking at 75% and white at 12.5% all at once - and
        # landing blanking on 75% by itself is the check that the composite
        # scale and the RF scale agree.
        self.slope = blocks.multiply_const_ff(-span if negative else span)
        self.offset = blocks.add_const_ff(
            CARRIER_AT_SYNC if negative else CARRIER_AT_WHITE)
        # Legal composite reaches 120 IRE, which maps to exactly zero
        # carrier. The rail stops the most saturated colour switching the
        # transmitter off, and catches any source that was not legalised.
        self.rail = analog.rail_ff(CARRIER_FLOOR, CARRIER_AT_SYNC)
        self.to_complex = blocks.float_to_complex(1)
        self.zero = blocks.null_source(gr.sizeof_float*1)

        self.mix = blocks.multiply_vcc(1)
        self.carrier = analog.sig_source_c(self.sample_rate, analog.GR_COS_WAVE,
                                           VSB_MIX, 1, 0, 0)
        self.vsb = filter.fft_filter_ccc(
            1, firdes.low_pass(1, self.sample_rate, VESTIGIAL_BW,
                               VESTIGIAL_TRANSITION, window.WIN_HAMMING, 6.76), 1)
        self.trim_mix = blocks.multiply_vcc(1)
        self.trim = analog.sig_source_c(self.sample_rate, analog.GR_COS_WAVE,
                                        VSB_TRIM, 1, 0, 0)

        self.connect(self, self.slope, self.offset, self.rail,
                     (self.to_complex, 0))
        self.connect(self.zero, (self.to_complex, 1))
        self.connect(self.to_complex, (self.mix, 0))
        self.connect(self.carrier, (self.mix, 1))
        self.connect(self.mix, self.vsb, (self.trim_mix, 0))
        self.connect(self.trim, (self.trim_mix, 1))
        self.connect(self.trim_mix, self)

    def set_polarity(self, polarity):
        """Both halves of the mapping move together.

        Changing only the slope - which the app used to do - leaves the
        carrier centred on the wrong level and drives it far past full
        scale. That is how positive modulation reached 1.79 against a radio
        that clips at 1.0.
        """
        span = CARRIER_AT_SYNC - CARRIER_AT_WHITE
        negative = polarity != 'positive'
        self.slope.set_k(-span if negative else span)
        self.offset.set_k(CARRIER_AT_SYNC if negative else CARRIER_AT_WHITE)

class ConfigDialog(Qt.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("NTSC Video Transmitter Configuration")
        self.layout = Qt.QVBoxLayout(self)
        self.config_dir = CONFIG_DIR
        self.config_file = os.path.join(self.config_dir, "ntscAnalogVideoRecorded_config.json")
        
        # Read settings from window_settings.json
        settings = read_settings()
        self.usrp_ip = settings.get('usrp_ip', '')
        self.radio_type = settings.get('radio_type', 'hackrf')
        self.media_dir = settings['media_directory']
        
        # Add OK/Cancel buttons
        self.button_box = Qt.QDialogButtonBox(
            Qt.QDialogButtonBox.Ok | Qt.QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        
        # Create all controls
        self.create_radio_label()
        self.create_frequency_control()
        self.create_power_control()
        self.create_video_selector()
        self.create_video_invert_control() 
        self.create_audio_controls()
        
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
        # A television channel, a typed frequency or a slider, all in step -
        # the same control the ATSC apps use. This was a bare slider in whole
        # megahertz, which could not reach most frequencies at all.
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

    def create_video_selector(self):
        """Three kinds of source, in three groups of one list.

        This used to offer only ``.dat`` files, which are single still
        frames, so the app could never transmit moving pictures at all. Now
        any video file ffmpeg can read is offered too, and there is a
        built-in pattern so it works with an empty media folder.

        The groups are separated because they behave differently - a clip
        plays and loops, a ``.dat`` is one frame held on screen - and
        because the list is long enough that a wall of names is no use.
        """
        self.video_combo = Qt.QComboBox()
        settings = read_settings()
        self.media_dir = settings.get('media_directory', '')

        # Always first, and always available.
        self.video_combo.addItem("Colour bars (built in)", ('pattern', None))

        if have_ffmpeg():
            clips = video_files(self.media_dir)
            if clips:
                self.video_combo.insertSeparator(self.video_combo.count())
            for display, path in clips:
                self.video_combo.addItem(display, ('video', path))
        else:
            # TVAdemo has no ffmpeg, and a picker that simply showed nothing
            # would look like an empty media folder.
            self.video_combo.insertSeparator(self.video_combo.count())
            self.video_combo.addItem("Video clips need ffmpeg, which is not "
                                     "installed here", ('pattern', None))

        # The instructor's captures: one composite frame each, at 18 MS/s.
        stills = dat_files(self.media_dir)
        if stills:
            self.video_combo.insertSeparator(self.video_combo.count())
        for display, path in stills:
            self.video_combo.addItem(f"{display}  (still frame)",
                                     ('still', path))

        ok_button = self.button_box.button(Qt.QDialogButtonBox.Ok)
        ok_button.setEnabled(self.radio_type in ('hackrf', 'vsg')
                             or bool(self.usrp_ip))
        ok_button.setGraphicsEffect(None)

        self.layout.addWidget(Qt.QLabel("Video Source:"))
        self.layout.addWidget(self.video_combo)

    def create_video_invert_control(self):
        """Modulation polarity, which was previously wrong by default.

        US television is negative modulation - sync is peak carrier. The
        old checkbox was unticked by default and that selected *positive*,
        which is System L (France) and nothing a TV here can show; it also
        drove the carrier to 1.79 against a radio that clips at 1.0.
        """
        self.layout.addWidget(Qt.QLabel("Modulation Polarity:"))
        self.polarity_combo = Qt.QComboBox()
        self.polarity_combo.addItem(
            "Negative - sync at peak carrier (US, System M)", 'negative')
        self.polarity_combo.addItem(
            "Positive - inverted (not a US standard)", 'positive')
        self.layout.addWidget(self.polarity_combo)

    def create_audio_controls(self):
        """What the aural carrier carries - the clip's own sound by default.

        This used to be a `.wav` picked separately from the media folder,
        which was all there was when the only video the app could send was
        a single still frame. Once it could send clips, that left a 1950s
        car advertisement going out with an unrelated cartoon soundtrack
        over it - the picture from one file and the sound from another.
        The clip's own track is the default now, and a separate file is
        still offered for the sources that have no sound of their own: the
        built-in pattern and the `.dat` stills.
        """
        self.layout.addWidget(Qt.QLabel("Sound:"))
        self.audio_combo = Qt.QComboBox()
        self.audio_combo.addItem("From the video clip", ('clip', None))
        self.audio_combo.addItem("Silence - aural carrier only",
                                 ('silence', None))

        wavs = choices(self.media_dir, AUDIO)   # WAV and MP3, subfolders too
        if wavs:
            self.audio_combo.insertSeparator(self.audio_combo.count())
        for label, path in wavs:
            self.audio_combo.addItem(label, ('file', path))

        self.layout.addWidget(self.audio_combo)
        # The first entry only means something when a clip is selected, so
        # it follows the video picker rather than sitting there offering
        # sound that does not exist.
        self._audio_forced = False
        self.audio_combo.activated.connect(self._audio_picked)
        self.video_combo.currentIndexChanged.connect(self._sync_audio_choice)
        self._sync_audio_choice()

    def _audio_picked(self, _index):
        """The user chose for themselves, so stop overriding the choice."""
        self._audio_forced = False

    def _sync_audio_choice(self):
        """Grey out 'From the video clip' when the source is not a clip.

        A turn away from a clip and back must come back to the clip's own
        sound: silence was substituted *for* the user, not chosen by them,
        and leaving it there means picking a clip and silently transmitting
        no sound with it. ``_audio_forced`` is what tells the two apart.
        """
        kind = (self.video_combo.currentData() or ('pattern', None))[0]
        from_clip = kind == 'video'
        item = self.audio_combo.model().item(0)
        item.setEnabled(from_clip)
        item.setText("From the video clip" if from_clip else
                     "From the video clip - this source has no sound")
        if not from_clip and self.audio_combo.currentIndex() == 0:
            self.audio_combo.setCurrentIndex(1)      # silence
            self._audio_forced = True
        elif from_clip and self._audio_forced:
            self.audio_combo.setCurrentIndex(0)
            self._audio_forced = False

    def load_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                    
                self.cf_chooser.setValue(config.get(
                    'center_freq', channel_center_mhz(DEFAULT_CHANNEL)))
                self.pwr_slider.setValue(power_percent(config.get('power_level'), 50))
                # Match the saved source by path, not by index: the list
                # changes shape as media comes and goes.
                saved = config.get('video_source')
                if saved:
                    for i in range(self.video_combo.count()):
                        data = self.video_combo.itemData(i)
                        if data and data[1] == saved:
                            self.video_combo.setCurrentIndex(i)
                            break
                # Matched the same way, and by kind as well, since two of
                # the three choices have no path. A config from before the
                # sound came off the clip holds an 'audio_index' into a
                # list that no longer has that shape; it is ignored, and
                # the default below stands.
                self._restore_audio(config.get('audio_kind'),
                                    config.get('audio_file'))
                index = self.polarity_combo.findData(
                    config.get('polarity', 'negative'))
                self.polarity_combo.setCurrentIndex(max(index, 0))
            except:
                # If loading fails, keep default values
                pass
        else:
            # Create config directory if it doesn't exist
            os.makedirs(self.config_dir, exist_ok=True)

    def _restore_audio(self, kind, path):
        """Put the sound picker back on a saved choice, by kind and path."""
        if not kind:
            return
        for i in range(self.audio_combo.count()):
            data = self.audio_combo.itemData(i)
            if data and data[0] == kind and data[1] == path:
                self.audio_combo.setCurrentIndex(i)
                break
        self._sync_audio_choice()

    def audio_choice(self):
        """(kind, path) for the sound, as ('clip'|'silence'|'file', path)."""
        return self.audio_combo.currentData() or ('silence', None)

    def save_config(self):
        kind, path = self.video_combo.currentData() or ('pattern', None)
        audio_kind, audio_path = self.audio_choice()
        config = {
            'center_freq': self.cf_chooser.value(),
            'power_level': self.pwr_slider.value(),
            'video_kind': kind,
            'video_source': path,
            'audio_kind': audio_kind,
            'audio_file': audio_path,
            'polarity': self.polarity_combo.currentData(),
        }

        update_app_config(self.config_file, config)

    def accept(self):
        self.save_config()
        super().accept()

    def get_values(self):
        ipXmitAddr = self.usrp_ip if self.radio_type == 'usrp' else ''
        
        kind, path = self.video_combo.currentData() or ('pattern', None)
        audio_kind, audio_path = self.audio_choice()
        return {
            'radio_type': self.radio_type,
            'ipXmitAddr': ipXmitAddr,
            'cf': self.cf_chooser.value(),
            'pwr': self.pwr_slider.value(),
            'videoKind': kind,
            'videoFileName': path,
            'audioKind': audio_kind,
            'audioFileName': audio_path,
            'polarity': self.polarity_combo.currentData(),
        }

class ntscAnalogVideoRecorded(gr.top_block, Qt.QWidget):
    # What this window's own controls change that its dialog should
    # open on next time - see apps/utils.py: save_flowgraph_settings.
    SAVED_SETTINGS = {'power_level': 'rfPwr',
                      'center_freq': 'cf'}

    def __init__(self, config_values=None):
        gr.top_block.__init__(self, "NTSC Video Transmitter", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("NTSC Video Transmitter")
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

        self.settings = Qt.QSettings("GNU Radio", "ntscAnalogVideoRecorded")

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
        videoFileName = values['videoFileName']
        videoKind = values.get('videoKind', 'pattern')
        audioFileName = values.get('audioFileName')
        audioKind = values.get('audioKind', 'clip')
        polarity = values.get('polarity', 'negative')
        videoInvert = -1 if polarity == 'negative' else 1

        ##################################################
        # Variables
        ##################################################
        self.rfPwrDefault = rfPwrDefault = pwr
        self.cfDefault = cfDefault = cf
        self.videoInvert = videoInvert
        self.polarity = polarity
        self.videoKind = videoKind
        self.videoFileName = videoFileName
        self.signalType = signalType = 'NTSC Video - Recorded'
        self.samp_rate = samp_rate = 10e6
        self.rfPwr = rfPwr = rfPwrDefault
        self.outputIpAddr = outputIpAddr = ipXmitAddr
        self.cf = cf = cfDefault
        self.audioFileName = audioFileName
        self.radio_type = radio_type

        ##################################################
        # Blocks
        ##################################################
        # Create the options list
        self._videoInvert_options = [-1, 1]
        # Named for what they are. These used to read 'Normal' and
        # 'Inverted', which said nothing about which one a television here
        # can actually show - and the dialog's default picked the other one.
        self._videoInvert_labels = ['Negative (US)', 'Positive']
        # Create the combo box
        # Create the radio buttons
        self._videoInvert_group_box = Qt.QGroupBox("Video Inversion" + ": ")
        self._videoInvert_box = Qt.QHBoxLayout()
        class variable_chooser_button_group(Qt.QButtonGroup):
            def __init__(self, parent=None):
                Qt.QButtonGroup.__init__(self, parent)
            @pyqtSlot(int)
            def updateButtonChecked(self, button_id):
                self.button(button_id).setChecked(True)
        self._videoInvert_button_group = variable_chooser_button_group()
        self._videoInvert_group_box.setLayout(self._videoInvert_box)
        for i, _label in enumerate(self._videoInvert_labels):
            radio_button = Qt.QRadioButton(_label)
            self._videoInvert_box.addWidget(radio_button)
            self._videoInvert_button_group.addButton(radio_button, i)
        self._videoInvert_callback = lambda i: Qt.QMetaObject.invokeMethod(self._videoInvert_button_group, "updateButtonChecked", Qt.Q_ARG("int", self._videoInvert_options.index(i)))
        self._videoInvert_callback(self.videoInvert)
        self._videoInvert_button_group.buttonClicked[int].connect(
            lambda i: self.set_videoInvert(self._videoInvert_options[i]))
        self.top_grid_layout.addWidget(self._videoInvert_group_box, 1, 5, 1, 5)
        for r in range(1, 2):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(5, 10):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._rfPwr_range = Range(0, 100, 1, rfPwrDefault, 200)
        self._rfPwr_win = RangeWidget(self._rfPwr_range, self.set_rfPwr, "RF Output Power (%)", "counter_slider", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._rfPwr_win, 1, 0, 1, 5)
        for r in range(1, 2):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 5):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._cf_range = frequency_range(50, 2200, 0.01, cfDefault, 200)
        self._cf_win = RangeWidget(self._cf_range, self.set_cf, "Center Frequency (MHz)", "counter", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._cf_win, 0, 5, 1, 5)
        for r in range(0, 1):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(5, 10):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._power_range = resolve_power_range(radio_type)
        if radio_type == 'vsg':
            from apps.vsg_sink import vsg_sink
            self.radio_sink = vsg_sink(
                center_freq=cf*1e6+6e6,
                sample_rate=samp_rate*2,
                level_dbm=scale_power(rfPwr, self._power_range))
        elif radio_type == 'usrp':
            self.radio_sink = uhd.usrp_sink(
                ",".join(('addr='+outputIpAddr, '')),
                uhd.stream_args(cpu_format="fc32", args='', channels=list(range(0,1))),
                "",
            )
            self.radio_sink.set_samp_rate(samp_rate*2)
            self.radio_sink.set_time_now(uhd.time_spec(time.time()), uhd.ALL_MBOARDS)
            self.radio_sink.set_center_freq(cf*1e6+6e6, 0)
            self.radio_sink.set_antenna("TX/RX", 0)
            self._power_range = resolve_power_range(radio_type, self.radio_sink)
            self.radio_sink.set_gain(scale_power(rfPwr, self._power_range), 0)
        else:
            self.radio_sink = soapy.sink('driver=hackrf', 'fc32', 1, '', '', [''], [''])
            self.radio_sink.set_sample_rate(0, samp_rate*2)
            self.radio_sink.set_frequency(0, cf*1e6+6e6)
            self.radio_sink.set_gain(0, 'VGA', scale_power(rfPwr, self._power_range))
            self.radio_sink.set_gain(0, 'AMP', 0)
        self._signalType_tool_bar = Qt.QToolBar(self)

        if None:
            self._signalType_formatter = None
        else:
            self._signalType_formatter = lambda x: str(x)

        self._signalType_tool_bar.addWidget(Qt.QLabel("Signal Type: "))
        self._signalType_label = Qt.QLabel(str(self._signalType_formatter(self.signalType)))
        self._signalType_tool_bar.addWidget(self._signalType_label)
        self.top_grid_layout.addWidget(self._signalType_tool_bar, 0, 1, 1, 4)
        for r in range(0, 1):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(1, 5):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.rational_resampler_xxx_2 = filter.rational_resampler_ccc(
                interpolation=2,
                decimation=1,
                taps=[],
                fractional_bw=0)
        self.rational_resampler_xxx_0 = filter.rational_resampler_fff(
                interpolation=9,
                decimation=10,
                taps=[],
                fractional_bw=0)
        self.qtgui_time_sink_x_0 = qtgui.time_sink_f(
            286*5, #size
            9e6, #samp_rate
            'NTSC Baseband Time Domain', #name
            1, #number of inputs
            None # parent
        )
        self.qtgui_time_sink_x_0.set_update_time(0.05)
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
        self.top_grid_layout.addWidget(self._qtgui_time_sink_x_0_win, 2, 0, 5, 7)
        for r in range(2, 7):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 7):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.qtgui_freq_sink_x_0 = qtgui.freq_sink_c(
            8192, #size
            window.WIN_BLACKMAN_hARRIS, #wintype
            cf*1e6, #fc - the channel centre, so a plot called 'RF Spectrum'
                    # is labelled in RF. It read 0 Hz at the channel centre,
                    # which is not a frequency anything is transmitting on,
                    # and made the receiver's plot of the same signal
                    # impossible to compare with.
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
        self.top_grid_layout.addWidget(self._qtgui_freq_sink_x_0_win, 7, 0, 5, 10)
        for r in range(7, 12):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 10):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.qtgui_const_sink_x_0 = qtgui.const_sink_c(
            1250, #size
            'NTSC Video Constellation', #name
            1, #number of inputs
            None # parent
        )
        self.qtgui_const_sink_x_0.set_update_time(0.10)
        self.qtgui_const_sink_x_0.set_y_axis(-1, 1)
        self.qtgui_const_sink_x_0.set_x_axis(-1, 1)
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
        markers = [-1, 0, 0, 0, 0,
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
        self.top_grid_layout.addWidget(self._qtgui_const_sink_x_0_win, 2, 7, 5, 3)
        for r in range(2, 7):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(7, 10):
            self.top_grid_layout.setColumnStretch(c, 1)
        # The vestigial-sideband filter, the video mixers and the composite
        # scaling all moved into NtscModulator; what is left here is the
        # aural carrier, the sum, and the shift down to the radio.
        self.blocks_multiply_xx_1 = blocks.multiply_vcc(1)
        self.blocks_multiply_xx_0_0_0 = blocks.multiply_vcc(1)
        # Everything that follows sums to at most visual peak plus aural, so
        # scale once, here, rather than with the two unexplained constants
        # (0.25 then 0.95) that used to sit in the chain.
        self.blocks_multiply_const_vxx_2 = blocks.multiply_const_cc(
            BASEBAND_SCALE / (CARRIER_AT_SYNC + AURAL_AMPLITUDE))
        self._build_video_source(samp_rate, videoKind, videoFileName)
        self._build_audio_source(samp_rate, audioKind, audioFileName,
                                 videoKind, videoFileName)
        self.modulator = NtscModulator(samp_rate, polarity)
        self.blocks_add_xx_0 = blocks.add_vcc(1)
        self.analog_sig_source_x_1 = analog.sig_source_c(samp_rate*2, analog.GR_COS_WAVE, -LO_OFFSET, 1, 0, 0)
        self.analog_sig_source_x_0_0_0 = analog.sig_source_c(samp_rate, analog.GR_COS_WAVE, AURAL_CARRIER, AURAL_AMPLITUDE, 0, 0)
        self.analog_frequency_modulator_fc_0 = analog.frequency_modulator_fc(2*pi*AURAL_DEVIATION/samp_rate)


        ##################################################
        # Connections
        ##################################################
        self.connect((self.analog_frequency_modulator_fc_0, 0), (self.blocks_multiply_xx_0_0_0, 0))
        self.connect((self.analog_sig_source_x_0_0_0, 0), (self.blocks_multiply_xx_0_0_0, 1))
        self.connect((self.analog_sig_source_x_1, 0), (self.blocks_multiply_xx_1, 1))
        self.connect((self.video_source, 0), (self.modulator, 0))
        self.connect((self.modulator, 0), (self.blocks_add_xx_0, 0))
        self.connect((self.blocks_multiply_xx_0_0_0, 0), (self.blocks_add_xx_0, 1))
        self.connect((self.video_source, 0), (self.rational_resampler_xxx_0, 0))
        self.connect((self.blocks_add_xx_0, 0), (self.blocks_multiply_const_vxx_2, 0))
        self.connect((self.blocks_add_xx_0, 0), (self.qtgui_const_sink_x_0, 0))
        self.connect((self.blocks_add_xx_0, 0), (self.qtgui_freq_sink_x_0, 0))
        self.connect((self.blocks_multiply_const_vxx_2, 0), (self.rational_resampler_xxx_2, 0))
        self.connect((self.blocks_multiply_xx_1, 0), (self.radio_sink, 0))
        self.connect((self.rational_resampler_xxx_0, 0), (self.qtgui_time_sink_x_0, 0))
        self.connect((self.audio_source, 0), (self.analog_frequency_modulator_fc_0, 0))
        self.connect((self.rational_resampler_xxx_2, 0), (self.blocks_multiply_xx_1, 0))

    def _build_video_source(self, samp_rate, kind, path):
        """Whichever kind of source was chosen, ending in composite floats.

        Two quite different paths meet here. A video file or the built-in
        pattern is *encoded* to composite at the flowgraph's own rate. A
        ``.dat`` capture already is composite - but at 18 MS/s, and playing
        it at any other rate without resampling makes every timing in it
        wrong by that ratio: at 10 MS/s a line lasts 114.4 us instead of
        63.5556, a line rate of 8741 Hz where NTSC needs 15734.266. That is
        what this app used to put on the air, and no television could have
        locked to it.
        """
        if kind == 'still' and path:
            self.blocks_file_source_0 = blocks.file_source(
                gr.sizeof_float*1, path, True, 0, 0)
            self.blocks_file_source_0.set_begin_tag(pmt.PMT_NIL)
            interp, decim = dat_resample_ratio(samp_rate)
            self.dat_resampler = filter.rational_resampler_fff(
                interpolation=interp, decimation=decim, taps=[], fractional_bw=0)
            self.connect(self.blocks_file_source_0, self.dat_resampler)
            self.video_source = self.dat_resampler
            self.sourceDescription = os.path.basename(path) + " (still)"
            return

        if kind == 'video' and path:
            frames = VideoFile(path)
        else:
            frames = TestPattern()
        # A Python block, so it must be kept referenced or the scheduler
        # segfaults with no Python frame in the traceback.
        self.ntsc_frames = ntsc_source(frames, samp_rate)
        self.video_source = self.ntsc_frames
        self.sourceDescription = frames.description

    def _build_audio_source(self, samp_rate, kind, audio_path,
                            video_kind, video_path):
        """What the aural carrier carries, ending in floats at ``samp_rate``.

        **The sound should come from the same file as the picture.** It used
        to come from a ``.wav`` chosen separately in the dialog - the only
        thing possible when the app could send nothing but a single still
        frame - so once it could send clips, a 1950s car advertisement went
        out with an unrelated cartoon soundtrack over it.

        Three sources, and each one falls back to the next if it cannot be
        had: the clip's own track, a ``.wav`` from the media folder, and
        silence. Silence is a real choice rather than an absence - System M
        transmits the aural carrier whether or not there is anything on it,
        and the built-in pattern and the ``.dat`` stills have no sound of
        their own.

        The resampling ratio is worked out from the source's own rate. It
        was a fixed 625/3, right only for a 48 kHz file: every wav in the
        media folder happens to be one, but a 44.1 kHz file would have
        played 8.8% fast and taken the aural deviation with it.
        """
        head, rate = None, None
        if kind == 'clip' and video_kind == 'video' and video_path:
            if has_audio(video_path):
                try:
                    self.audio_track = AudioTrack(video_path)
                    head = blocks.file_descriptor_source(
                        gr.sizeof_float, self.audio_track.descriptor())
                    rate = self.audio_track.rate
                    self.soundDescription = self.audio_track.description
                except Exception as exc:
                    print(f"Could not open the clip's sound: {exc}",
                          file=sys.stderr)
            else:
                print(f"{os.path.basename(video_path)} has no soundtrack; "
                      "transmitting a silent aural carrier.", file=sys.stderr)

        if head is None and kind == 'file' and audio_path \
                and os.path.exists(audio_path):
            # WAV or MP3; the rate is the file's own for a WAV, and 48 kHz
            # for an MP3, which ffmpeg resamples as it decodes.
            self.audio_file = AudioFileSource(audio_path)
            head = self.audio_file.block
            rate = self.audio_file.rate
            self.soundDescription = os.path.basename(audio_path)

        if head is None:
            # A constant zero at the flowgraph's own rate, so it needs no
            # resampling at all - the aural carrier goes out unmodulated,
            # which is what an off-air silent channel looks like.
            head = analog.sig_source_f(samp_rate, analog.GR_CONST_WAVE,
                                       0, 0, 0)
            rate = samp_rate
            self.soundDescription = "silence"

        self.audio_head = head
        tail = head
        if kind != 'silence':
            # **Pre-emphasis, because the receiver de-emphasises.** System M
            # sound is 75 us pre-emphasised like FM broadcast (the FM + RDS
            # transmitter here does the same), and a receiver undoes it. Send
            # without it and every set rolls the treble off instead: the
            # sound is not wrong, just dull, which is the kind of fault
            # nobody reports and everybody hears.
            self.audio_preemph = analog.fm_preemph(float(rate),
                                                   tau=AUDIO_PREEMPHASIS)
            self.connect(tail, self.audio_preemph)
            tail = self.audio_preemph
            # Full deviation is |1.0|, so anything past it over-deviates the
            # aural carrier and splatters into the next channel. The clip
            # conditioning in AUDIO_FILTER keeps it there on all but one
            # sample in sixty thousand, and pre-emphasis lifts the treble on
            # top of that; this is the guarantee, at 48 kHz where it costs
            # nothing, rather than a hope. It goes *after* the boost, which
            # is the order a real station limits in.
            self.audio_rail = analog.rail_ff(-1.0, 1.0)
            self.connect(tail, self.audio_rail)
            tail = self.audio_rail

        if int(rate) == int(samp_rate):
            self.audio_source = tail
            return
        ratio = Fraction(int(samp_rate), int(rate)).limit_denominator(10000)
        self.audio_resampler = filter.rational_resampler_fff(
            interpolation=ratio.numerator, decimation=ratio.denominator,
            taps=[], fractional_bw=0)
        self.connect(tail, self.audio_resampler)
        self.audio_source = self.audio_resampler

    def closeEvent(self, event):
        self.settings = Qt.QSettings("GNU Radio", "ntscAnalogVideoRecorded")
        self.settings.setValue("geometry", self.saveGeometry())
        self.stop()
        self.wait()
        # The video source closes its own ffmpeg in the block's stop(); the
        # audio one is a plain pipe into file_descriptor_source, so it is
        # shut down here rather than left for garbage collection.
        for source in (getattr(self, 'audio_track', None),
                       getattr(self, 'audio_file', None)):
            if source is not None:
                source.close()

        event.accept()

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

    def get_videoInvert(self):
        return self.videoInvert

    def set_videoInvert(self, videoInvert):
        """Switch modulation polarity while running.

        Both halves of the mapping have to move together: the slope and the
        offset. Changing only the slope - which is what this did - leaves
        the carrier centred on the wrong level and drives it far past full
        scale, which is how positive modulation used to reach 1.79.
        """
        self.videoInvert = videoInvert
        self.polarity = 'negative' if videoInvert < 0 else 'positive'
        self._videoInvert_callback(self.videoInvert)
        self.modulator.set_polarity(self.polarity)

    def get_videoFileName(self):
        return self.videoFileName

    def set_videoFileName(self, videoFileName):
        self.videoFileName = videoFileName
        # Only a still has a file source behind it; a video or the built-in
        # pattern is generated, and swapping those means rebuilding.
        if hasattr(self, 'blocks_file_source_0'):
            self.blocks_file_source_0.open(self.videoFileName, True)

    def get_signalType(self):
        return self.signalType

    def set_signalType(self, signalType):
        self.signalType = signalType
        Qt.QMetaObject.invokeMethod(self._signalType_label, "setText", Qt.Q_ARG("QString", str(self._signalType_formatter(self.signalType))))

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        """Only the radio's rate is really changeable while running.

        The video source and the modulator are both built around the rate
        they were constructed with - the encoder generates every sample
        from absolute time at that rate - so changing it properly means
        rebuilding, which nothing here does.
        """
        self.samp_rate = samp_rate
        self.analog_frequency_modulator_fc_0.set_sensitivity(2*pi*AURAL_DEVIATION/self.samp_rate)
        self.analog_sig_source_x_0_0_0.set_sampling_freq(self.samp_rate)
        self.analog_sig_source_x_1.set_sampling_freq(self.samp_rate*2)
        self.qtgui_freq_sink_x_0.set_frequency_range(self.cf*1e6, self.samp_rate)
        if self.radio_type == 'usrp':
            self.radio_sink.set_samp_rate(self.samp_rate*2)
        else:
            self.radio_sink.set_sample_rate(0, self.samp_rate*2)

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

    def get_cf(self):
        return self.cf

    def set_cf(self, cf):
        self.cf = cf
        # The spectrum is baseband, but it is labelled in RF, so retuning
        # has to move its axis with the radio.
        self.qtgui_freq_sink_x_0.set_frequency_range(self.cf*1e6,
                                                     self.samp_rate)
        if self.radio_type == 'usrp':
            self.radio_sink.set_center_freq(self.cf*1e6+6e6, 0)
        else:
            self.radio_sink.set_frequency(0, self.cf*1e6+6e6)

    def get_audioFileName(self):
        return self.audioFileName

    def set_audioFileName(self, audioFileName):
        self.audioFileName = audioFileName




def main(top_block_cls=ntscAnalogVideoRecorded, options=None, app=None, config_values=None):

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
