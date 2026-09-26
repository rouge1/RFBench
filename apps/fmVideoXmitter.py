#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# SPDX-License-Identifier: GPL-3.0
#
# FM Video Transmitter - composite video frequency-modulated onto a carrier,
# with the sound on FM subcarriers above the picture. That is what an analog
# FPV drone sends on 5.8 GHz, and what analog microwave relay and satellite
# links sent; the dialog's Standard picks between them, and its Format picks
# NTSC or PAL. It replaces the AM video transmitter, which matched no signal
# anything real sends.

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
from fractions import Fraction
from math import pi

from gnuradio import analog, blocks, filter, gr, qtgui, soapy, uhd  # type: ignore
from gnuradio.fft import window  # type: ignore
from gnuradio.qtgui import Range, RangeWidget  # type: ignore
from PyQt5 import Qt, QtCore  # type: ignore
try:                 # PyQt5 ships sip inside the package; some builds also
    import sip       # expose it at the top level.
except ImportError:  # pragma: no cover - depends on the PyQt5 build
    from PyQt5 import sip  # type: ignore

from apps.audio_file import AudioFileSource
from apps.media import AUDIO, choices
from apps.fm_video_core import (DEFAULT_PROFILE, PREEMPHASIS_CHOICES,
                                PROFILES, VIDEO_CENTRE, compensation_band,
                                fm_integrator_gain,
                                integrator_compensation_taps, preemphasis,
                                preemphasis_for, subcarrier_amplitude)
from apps.ntsc_encode import NTSC, STANDARDS
from apps.ntsc_source import (AudioTrack, TestPattern, VideoFile, has_audio,
                              have_ffmpeg, ntsc_source, video_files)
from apps.utils import (CONFIG_DIR, apply_dark_theme, apply_flowgraph_theme,
                        radio_label,
                        read_settings, update_app_config, power_percent,
                        resolve_power_range, scale_power, SPECTRUM_Y_AXIS,
                        FrequencyChooser, TrimmedSpinBox, frequency_range)

#: The rate each format's composite is encoded at, before it is interpolated
#: up to the radio's. NTSC at 10 MS/s: its encoder manages about twice real
#: time there on three threads, and would not keep up at 20. PAL needs more,
#: because its colour subcarrier sits at 4.43 MHz with sidebands reaching
#: past 5.7 - at 10 MS/s they would fold back onto the chroma itself - so
#: it is 12.5, which reaches 6.25 MHz and is 8/5 of the radio's 20.
VIDEO_RATES = {'ntsc': 10e6, 'pal': 12.5e6}
VIDEO_RATE = VIDEO_RATES['ntsc']
DEFAULT_FORMAT = NTSC.key
#: The radio's rate. FM video is wide: the picture swings the carrier
#: megahertz either way and the sound subcarriers sit up at 6-7 MHz, so it
#: needs far more than the 6 MHz channel NTSC fits in. 20 MS/s is also the
#: most a HackRF will take.
RF_RATE = 20e6
#: A constant-envelope signal never peaks above its average, so this is
#: headroom for the radio's own filters rather than for the signal.
BASEBAND_SCALE = 0.9
#: The interpolator's passband as a fraction of the video rate: flat to
#: 4.5 MHz of NTSC's 5, 5.6 of PAL's 6.25. GNU Radio's default, 0.4, is flat
#: only to 4 MHz at NTSC's rate, which trims the top of the chroma sidebands.
INTERPOLATOR_BW = 0.45
FREQ_MIN_MHZ = 30.0
FREQ_MAX_MHZ = 6000.0
DEVIATION_MIN_MHZ = 0.5
DEVIATION_MAX_MHZ = 20.0


class FmVideoModulator(gr.hier_block2):
    """Composite video and sound in, FM video at the radio's rate out.

    Input 0 is composite at ``video_rate`` (sync tip 0, white 1); input 1 is
    the sound, already conditioned and at ``rf_rate``. Kept as a block of its
    own, like ``NtscModulator``, so ``scripts/test_fm_video_transmit.py`` can
    drive exactly what the app transmits with no radio attached.

    The order is the order a real FM video exciter works in: the picture is
    pre-emphasised on its own, the sound subcarriers are added after it -
    the recommendation's curve is for the video, not the subcarriers - and
    the sum is what swings the carrier.
    """

    def __init__(self, profile, rf_rate=RF_RATE, video_rate=VIDEO_RATE,
                 deviation_pp=None, preemphasis_key=None, standard=None):
        gr.hier_block2.__init__(
            self, "fm_video_modulator",
            gr.io_signature(2, 2, gr.sizeof_float),
            gr.io_signature(1, 1, gr.sizeof_gr_complex))
        self.profile = profile
        self.standard = standard or NTSC
        self.rf_rate = float(rf_rate)
        ratio = Fraction(self.rf_rate / float(video_rate)).limit_denominator(64)
        if ratio < 1 or abs(float(ratio) * video_rate - self.rf_rate) > 1:
            raise ValueError("the radio's rate must be a simple multiple of "
                             f"the video rate, not {self.rf_rate / video_rate:g}")
        self.deviation_pp = float(deviation_pp or profile.deviation_pp)
        self.preemphasis_key = preemphasis_for(
            profile.preemphasis if preemphasis_key is None else preemphasis_key,
            self.standard.lines)

        self.centre = blocks.add_const_ff(-VIDEO_CENTRE)
        self.connect((self, 0), self.centre)
        tail = self.centre
        if ratio != 1:
            self.upsample = filter.rational_resampler_fff(
                interpolation=ratio.numerator, decimation=ratio.denominator,
                taps=[], fractional_bw=INTERPOLATOR_BW)
            self.connect(tail, self.upsample)
            tail = self.upsample
        curve = preemphasis(self.preemphasis_key)
        if curve is not None:
            b, a = curve.coefficients(self.rf_rate)
            # oldstyle=False: the feedback taps are a denominator with
            # a[0] = 1, as scipy writes it. The test checks the two agree.
            self.emphasis = filter.iir_filter_ffd(b, a, False)
            self.connect(tail, self.emphasis)
            tail = self.emphasis
        # The FM modulator below sums phase a sample at a time, which swings
        # the carrier further than asked the higher a component sits in the
        # baseband (see fm_integrator_gain). The picture is shaped against
        # that here, as far up as this format's picture reaches; each
        # subcarrier is a single frequency, so it takes an exact correction
        # in its level instead.
        self.integrator_fix = filter.fir_filter_fff(
            1, integrator_compensation_taps(
                self.rf_rate, compensation_band(self.standard.video_band)))
        self.connect(tail, self.integrator_fix)
        tail = self.integrator_fix

        self.sum = blocks.add_ff(1)
        self.connect(tail, (self.sum, 0))
        self.audio_fm = analog.frequency_modulator_fc(
            2 * pi * profile.audio_deviation / self.rf_rate)
        self.connect((self, 1), self.audio_fm)
        self.subcarriers = []
        for k, freq in enumerate(profile.subcarriers, 1):
            osc = analog.sig_source_c(self.rf_rate, analog.GR_COS_WAVE, freq,
                                      1, 0, 0)
            mix = blocks.multiply_vcc(1)
            real = blocks.complex_to_real(1)
            level = blocks.multiply_const_ff(self._subcarrier_level(freq))
            self.connect(self.audio_fm, (mix, 0))
            self.connect(osc, (mix, 1))
            self.connect(mix, real, level, (self.sum, k))
            self.subcarriers.append((freq, osc, mix, real, level))
        if not self.subcarriers:
            self.audio_sink = blocks.null_sink(gr.sizeof_gr_complex)
            self.connect(self.audio_fm, self.audio_sink)

        self.fm = analog.frequency_modulator_fc(
            2 * pi * self.deviation_pp / self.rf_rate)
        self.scale = blocks.multiply_const_cc(BASEBAND_SCALE)
        self.connect(self.sum, self.fm, self.scale, self)

    def _subcarrier_level(self, freq):
        return (subcarrier_amplitude(self.profile, freq, self.deviation_pp)
                / fm_integrator_gain(freq, self.rf_rate))

    def set_deviation(self, deviation_pp):
        """Change the picture's deviation; the sound's level on air stays."""
        self.deviation_pp = float(deviation_pp)
        self.fm.set_sensitivity(2 * pi * self.deviation_pp / self.rf_rate)
        for freq, _osc, _mix, _real, level in self.subcarriers:
            level.set_k(self._subcarrier_level(freq))


class AudioConditioner(gr.hier_block2):
    """Sound at its own rate in, ready for the subcarriers at ``rf_rate`` out.

    Pre-emphasis first, then the rail, then up to the radio's rate - the
    order the NTSC transmitter settled on, for the same reason: pre-emphasis
    lifts transients, and the limiter has to be what catches them.
    """

    def __init__(self, tau, rate, rf_rate=RF_RATE):
        gr.hier_block2.__init__(
            self, "fm_video_audio",
            gr.io_signature(1, 1, gr.sizeof_float),
            gr.io_signature(1, 1, gr.sizeof_float))
        self.preemph = analog.fm_preemph(float(rate), tau=tau)
        self.rail = analog.rail_ff(-1.0, 1.0)
        ratio = Fraction(int(rf_rate), int(rate)).limit_denominator(10000)
        self.resampler = filter.rational_resampler_fff(
            interpolation=ratio.numerator, decimation=ratio.denominator,
            taps=[], fractional_bw=0)
        self.connect(self, self.preemph, self.rail, self.resampler, self)


class ConfigDialog(Qt.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FM Video Transmitter Configuration")
        self.layout = Qt.QVBoxLayout(self)
        self.config_dir = CONFIG_DIR
        self.config_file = os.path.join(self.config_dir,
                                        "fmVideoXmitter_config.json")

        settings = read_settings()
        self.usrp_ip = settings.get('usrp_ip', '')
        self.radio_type = settings.get('radio_type', 'hackrf')
        self.media_dir = settings['media_directory']

        self.button_box = Qt.QDialogButtonBox(
            Qt.QDialogButtonBox.Ok | Qt.QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        self.create_radio_label()
        self.create_profile_selector()
        self.create_format_selector()
        self.create_frequency_control()
        self.create_power_control()
        self.create_modulation_controls()
        self.create_video_selector()
        self.create_audio_controls()
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
        """The standard, which fills in everything below it.

        FPV and a microwave relay are the same transmitter with different
        numbers, so picking one sets the channel plan, the deviation and the
        pre-emphasis to that standard's values. They stay editable after:
        an FPV transmitter whose deviation turns out to differ from the
        datasheet's hint should be matchable without editing code.
        """
        row = Qt.QHBoxLayout()
        row.addWidget(Qt.QLabel("Standard:"))
        self.profile_combo = Qt.QComboBox()
        for key, profile in PROFILES.items():
            self.profile_combo.addItem(profile.label, key)
        self.profile_combo.setCurrentIndex(
            max(self.profile_combo.findData(DEFAULT_PROFILE), 0))
        self.profile_combo.activated.connect(
            lambda _i: self.apply_profile(self.profile_combo.currentData()))
        row.addWidget(self.profile_combo, 1)
        self.layout.addLayout(row)

    def create_format_selector(self):
        """NTSC or PAL. FPV cameras and goggles do either, and an FPV
        transmitter sends whatever its camera gives it."""
        row = Qt.QHBoxLayout()
        row.addWidget(Qt.QLabel("Format:"))
        self.format_combo = Qt.QComboBox()
        for key, standard in STANDARDS.items():
            self.format_combo.addItem(standard.label, key)
        self.format_combo.setCurrentIndex(
            max(self.format_combo.findData(DEFAULT_FORMAT), 0))
        self.format_combo.activated.connect(lambda _i: self._format_changed())
        row.addWidget(self.format_combo, 1)
        self.layout.addLayout(row)

    def _lines(self):
        return STANDARDS.get(self.format_combo.currentData(), NTSC).lines

    def _format_changed(self):
        """Keep an F.405 pre-emphasis on the curve for the picture's lines."""
        key = preemphasis_for(self.preemph_combo.currentData(), self._lines())
        self.preemph_combo.setCurrentIndex(
            max(self.preemph_combo.findData(key), 0))

    def create_frequency_control(self):
        self.cf_chooser = FrequencyChooser(
            minimum=FREQ_MIN_MHZ, maximum=FREQ_MAX_MHZ,
            value=PROFILES[DEFAULT_PROFILE].default_mhz,
            channels=PROFILES[DEFAULT_PROFILE].channels)
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
        row = Qt.QHBoxLayout()
        row.addWidget(Qt.QLabel("Deviation (MHz p-p):"))
        # Four decimals, as many as the window's own deviation counter
        # takes, so what it is left at comes back here exactly
        # (SAVED_SETTINGS); trimmed, so 3.8 still reads 3.8.
        self.deviation_spin = TrimmedSpinBox()
        self.deviation_spin.setDecimals(4)
        self.deviation_spin.setSingleStep(0.25)
        self.deviation_spin.setRange(DEVIATION_MIN_MHZ, DEVIATION_MAX_MHZ)
        self.deviation_spin.setToolTip(
            "Peak-to-peak carrier swing for sync tip to peak white; with "
            "pre-emphasis, at the curve's crossover frequency")
        row.addWidget(self.deviation_spin)
        row.addStretch()
        self.layout.addLayout(row)

        row = Qt.QHBoxLayout()
        row.addWidget(Qt.QLabel("Pre-emphasis:"))
        self.preemph_combo = Qt.QComboBox()
        for key, label in PREEMPHASIS_CHOICES:
            self.preemph_combo.addItem(label, key)
        row.addWidget(self.preemph_combo, 1)
        self.layout.addLayout(row)

    def create_video_selector(self):
        self.video_combo = Qt.QComboBox()
        self.video_combo.addItem("Colour bars (built in)", ('pattern', None))
        if have_ffmpeg():
            clips = video_files(self.media_dir)
            if clips:
                self.video_combo.insertSeparator(self.video_combo.count())
            for display, path in clips:
                self.video_combo.addItem(display, ('video', path))
        else:
            self.video_combo.insertSeparator(self.video_combo.count())
            self.video_combo.addItem("Video clips need ffmpeg, which is not "
                                     "installed here", ('pattern', None))
        self.layout.addWidget(Qt.QLabel("Video Source:"))
        self.layout.addWidget(self.video_combo)

    def create_audio_controls(self):
        self.layout.addWidget(Qt.QLabel("Sound:"))
        self.audio_combo = Qt.QComboBox()
        self.audio_combo.addItem("From the video clip", ('clip', None))
        self.audio_combo.addItem("Silence - unmodulated subcarriers",
                                 ('silence', None))
        wavs = choices(self.media_dir, AUDIO)   # WAV and MP3, subfolders too
        if wavs:
            self.audio_combo.insertSeparator(self.audio_combo.count())
        for label, path in wavs:
            self.audio_combo.addItem(label, ('file', path))
        self.layout.addWidget(self.audio_combo)
        self.sound_label = Qt.QLabel("")
        self.layout.addWidget(self.sound_label)

        # As in the NTSC transmitter: "From the video clip" only means
        # something when a clip is the source, and a turn away from a clip
        # and back must come back to its sound.
        self._audio_forced = False
        self.audio_combo.activated.connect(self._audio_picked)
        self.video_combo.currentIndexChanged.connect(self._sync_audio_choice)
        self._sync_audio_choice()

    def _audio_picked(self, _index):
        self._audio_forced = False

    def _sync_audio_choice(self):
        kind = (self.video_combo.currentData() or ('pattern', None))[0]
        from_clip = kind == 'video'
        item = self.audio_combo.model().item(0)
        item.setEnabled(from_clip)
        item.setText("From the video clip" if from_clip else
                     "From the video clip - this source has no sound")
        if not from_clip and self.audio_combo.currentIndex() == 0:
            self.audio_combo.setCurrentIndex(1)
            self._audio_forced = True
        elif from_clip and self._audio_forced:
            self.audio_combo.setCurrentIndex(0)
            self._audio_forced = False

    def apply_profile(self, key):
        """Fill in a standard's values: channels, deviation, pre-emphasis."""
        profile = PROFILES.get(key, PROFILES[DEFAULT_PROFILE])
        self.cf_chooser.set_channels(profile.channels)
        on_plan = any(abs(centre - self.cf_chooser.value()) < 0.05
                      for _name, centre, _caption in profile.channels)
        if profile.default_mhz and not on_plan:
            self.cf_chooser.setValue(profile.default_mhz)
        self.deviation_spin.setValue(profile.deviation_pp / 1e6)
        self.preemph_combo.setCurrentIndex(max(self.preemph_combo.findData(
            preemphasis_for(profile.preemphasis, self._lines())), 0))
        self.sound_label.setText(profile.sound_summary())

    def load_config(self):
        if not os.path.exists(self.config_file):
            os.makedirs(self.config_dir, exist_ok=True)
            return
        try:
            with open(self.config_file, 'r') as f:
                config = json.load(f)
        except Exception as exc:
            print(f"FM video: could not read {self.config_file}: {exc}",
                  file=sys.stderr)
            return
        # One setting that fails to restore must not take the rest with it,
        # so each is restored on its own and a failure is said out loud.
        def restore(name, apply):
            try:
                apply()
            except Exception as exc:
                print(f"FM video: could not restore {name}: {exc}",
                      file=sys.stderr)

        def profile():
            index = self.profile_combo.findData(config.get('profile'))
            if index >= 0:
                self.profile_combo.setCurrentIndex(index)
                self.apply_profile(self.profile_combo.currentData())

        def video_format():
            index = self.format_combo.findData(config.get('video_format'))
            if index >= 0:
                self.format_combo.setCurrentIndex(index)
                self._format_changed()

        def video():
            saved = config.get('video_source')
            for i in range(self.video_combo.count()):
                data = self.video_combo.itemData(i)
                if saved and data and data[1] == saved:
                    self.video_combo.setCurrentIndex(i)
                    break

        def audio():
            kind, path = config.get('audio_kind'), config.get('audio_file')
            for i in range(self.audio_combo.count()):
                data = self.audio_combo.itemData(i)
                if kind and data and data[0] == kind and data[1] == path:
                    self.audio_combo.setCurrentIndex(i)
                    break
            self._sync_audio_choice()

        restore('the format', video_format)
        restore('the standard', profile)
        restore('the frequency', lambda: self.cf_chooser.setValue(
            float(config['center_freq'])) if 'center_freq' in config else None)
        restore('the power', lambda: self.pwr_slider.setValue(
            int(power_percent(config.get('power_level'), 50))))
        restore('the deviation', lambda: self.deviation_spin.setValue(
            float(config['deviation_mhz'])) if 'deviation_mhz' in config else None)
        restore('the pre-emphasis', lambda: self.preemph_combo.setCurrentIndex(
            max(self.preemph_combo.findData(preemphasis_for(
                config.get('preemphasis'), self._lines())), 0))
            if 'preemphasis' in config else None)
        restore('the video source', video)
        restore('the sound', audio)

    def save_config(self):
        kind, path = self.video_combo.currentData() or ('pattern', None)
        audio_kind, audio_path = self.audio_combo.currentData() or ('silence', None)
        config = {
            'profile': self.profile_combo.currentData(),
            'video_format': self.format_combo.currentData(),
            'center_freq': self.cf_chooser.value(),
            'power_level': self.pwr_slider.value(),
            'deviation_mhz': self.deviation_spin.value(),
            'preemphasis': self.preemph_combo.currentData(),
            'video_kind': kind,
            'video_source': path,
            'audio_kind': audio_kind,
            'audio_file': audio_path,
        }
        update_app_config(self.config_file, config)

    def accept(self):
        self.save_config()
        super().accept()

    def get_values(self):
        ipXmitAddr = self.usrp_ip if self.radio_type == 'usrp' else ''
        kind, path = self.video_combo.currentData() or ('pattern', None)
        audio_kind, audio_path = self.audio_combo.currentData() or ('silence', None)
        return {
            'radio_type': self.radio_type,
            'ipXmitAddr': ipXmitAddr,
            'cf': self.cf_chooser.value(),
            'pwr': self.pwr_slider.value(),
            'profile': self.profile_combo.currentData(),
            'video_format': self.format_combo.currentData(),
            'deviation_pp': self.deviation_spin.value() * 1e6,
            'preemphasis': self.preemph_combo.currentData(),
            'videoKind': kind,
            'videoFileName': path,
            'audioKind': audio_kind,
            'audioFileName': audio_path,
        }


class fmVideoXmitter(gr.top_block, Qt.QWidget):
    # What this window's own controls change that its dialog should
    # open on next time - see apps/utils.py: save_flowgraph_settings.
    SAVED_SETTINGS = {'power_level': 'rfPwr',
                      'center_freq': 'cf',
                      'deviation_mhz': 'deviation_mhz'}

    def __init__(self, config_values=None):
        gr.top_block.__init__(self, "FM Video Transmitter", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("FM Video Transmitter")
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

        self.settings = Qt.QSettings("GNU Radio", "fmVideoXmitter")
        try:
            geometry = self.settings.value("geometry")
            if geometry:
                self.restoreGeometry(geometry)
        except BaseException as exc:
            print(f"Qt GUI: Could not restore geometry: {str(exc)}", file=sys.stderr)

        if config_values is None:
            config_dialog = ConfigDialog()
            if not config_dialog.exec_():
                sys.exit(0)
            values = config_dialog.get_values()
        else:
            values = config_values

        self.radio_type = radio_type = values.get('radio_type', 'hackrf')
        self.cf = cf = float(values.get('cf', PROFILES[DEFAULT_PROFILE].default_mhz))
        self.rfPwr = rfPwr = values.get('pwr', 50)
        self.profile = profile = PROFILES.get(values.get('profile'),
                                              PROFILES[DEFAULT_PROFILE])
        self.standard = standard = STANDARDS.get(values.get('video_format'), NTSC)
        self.video_rate = VIDEO_RATES[standard.key]
        self.deviation_pp = float(values.get('deviation_pp') or profile.deviation_pp)
        # The same in the dialog's unit, for SAVED_SETTINGS.
        self.deviation_mhz = self.deviation_pp / 1e6
        self.preemphasis_key = preemphasis_for(
            values.get('preemphasis', profile.preemphasis), standard.lines)
        self.samp_rate = RF_RATE
        ipXmitAddr = values.get('ipXmitAddr', '')

        ##################################################
        # Controls
        ##################################################
        curve = preemphasis(self.preemphasis_key)
        self._standard_tool_bar = Qt.QToolBar(self)
        self._standard_tool_bar.addWidget(Qt.QLabel("Standard: "))
        self._standard_tool_bar.addWidget(Qt.QLabel(
            f"{profile.label}  -  {standard.key.upper()}  -  pre-emphasis: "
            f"{curve.label if curve else 'none'}"))
        self.top_grid_layout.addWidget(self._standard_tool_bar, 0, 0, 1, 5)

        self._cf_range = frequency_range(FREQ_MIN_MHZ, FREQ_MAX_MHZ, 0.1, cf, 200)
        self._cf_win = RangeWidget(self._cf_range, self.set_cf,
                                   "Center Frequency (MHz)", "counter", float,
                                   QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._cf_win, 0, 5, 1, 5)
        self._rfPwr_range = Range(0, 100, 1, rfPwr, 200)
        self._rfPwr_win = RangeWidget(self._rfPwr_range, self.set_rfPwr,
                                      "RF Output Power (%)", "counter_slider",
                                      float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._rfPwr_win, 1, 0, 1, 5)
        self._dev_range = Range(DEVIATION_MIN_MHZ, DEVIATION_MAX_MHZ, 0.05,
                                self.deviation_pp / 1e6, 200)
        self._dev_win = RangeWidget(self._dev_range, self.set_deviation,
                                    "Video Deviation (MHz p-p)", "counter_slider",
                                    float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._dev_win, 1, 5, 1, 5)
        for c in range(0, 10):
            self.top_grid_layout.setColumnStretch(c, 1)

        ##################################################
        # Radio
        ##################################################
        self._power_range = resolve_power_range(radio_type)
        if radio_type == 'vsg':
            from apps.vsg_sink import vsg_sink
            self.radio_sink = vsg_sink(
                center_freq=cf * 1e6, sample_rate=RF_RATE,
                level_dbm=scale_power(rfPwr, self._power_range))
        elif radio_type == 'usrp':
            self.radio_sink = uhd.usrp_sink(
                ",".join(('addr=' + ipXmitAddr, '')),
                uhd.stream_args(cpu_format="fc32", args='', channels=list(range(0, 1))),
                "",
            )
            self.radio_sink.set_samp_rate(RF_RATE)
            self.radio_sink.set_time_now(uhd.time_spec(time.time()), uhd.ALL_MBOARDS)
            self.radio_sink.set_center_freq(cf * 1e6, 0)
            self.radio_sink.set_antenna("TX/RX", 0)
            self._power_range = resolve_power_range(radio_type, self.radio_sink)
            self.radio_sink.set_gain(scale_power(rfPwr, self._power_range), 0)
        else:
            self.radio_sink = soapy.sink('driver=hackrf', 'fc32', 1, '', '', [''], [''])
            self.radio_sink.set_sample_rate(0, RF_RATE)
            self.radio_sink.set_frequency(0, cf * 1e6)
            self.radio_sink.set_gain(0, 'VGA', scale_power(rfPwr, self._power_range))
            self.radio_sink.set_gain(0, 'AMP', 0)

        ##################################################
        # Signal
        ##################################################
        self._build_video_source(values.get('videoKind', 'pattern'),
                                 values.get('videoFileName'))
        self._build_audio_source(values.get('audioKind', 'clip'),
                                 values.get('audioFileName'),
                                 values.get('videoKind', 'pattern'),
                                 values.get('videoFileName'))
        self.modulator = FmVideoModulator(profile, RF_RATE, self.video_rate,
                                          self.deviation_pp, self.preemphasis_key,
                                          standard=standard)
        self.connect(self.video_source, (self.modulator, 0))
        self.connect(self.audio_source, (self.modulator, 1))
        self.connect(self.modulator, self.radio_sink)

        self._source_tool_bar = Qt.QToolBar(self)
        self._source_tool_bar.addWidget(Qt.QLabel(
            f"Picture: {self.sourceDescription}    Sound: {self.soundDescription}"
            f"  -  {profile.sound_summary().lower()}"))
        self.top_grid_layout.addWidget(self._source_tool_bar, 2, 0, 1, 10)

        ##################################################
        # Displays
        ##################################################
        self.qtgui_time_sink = qtgui.time_sink_f(
            int(round(2.2 * standard.line * self.video_rate)), self.video_rate,
            'Composite Video (what swings the carrier)', 1, None)
        self.qtgui_time_sink.set_update_time(0.05)
        self.qtgui_time_sink.set_y_axis(-0.2, 1.2)
        self.qtgui_time_sink.set_y_label('Composite', "")
        self.qtgui_time_sink.enable_grid(True)
        self.qtgui_time_sink.enable_autoscale(False)
        self.qtgui_time_sink.enable_control_panel(False)
        self.qtgui_time_sink.disable_legend()
        self._qtgui_time_sink_win = sip.wrapinstance(
            self.qtgui_time_sink.qwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(self._qtgui_time_sink_win, 3, 0, 5, 10)
        self.connect(self.video_source, self.qtgui_time_sink)

        self.qtgui_freq_sink = qtgui.freq_sink_c(
            4096, window.WIN_BLACKMAN_hARRIS, cf * 1e6, RF_RATE, 'RF Spectrum',
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
        self.top_grid_layout.addWidget(self._qtgui_freq_sink_win, 8, 0, 5, 10)
        self.connect(self.modulator, self.qtgui_freq_sink)
        for r in range(3, 13):
            self.top_grid_layout.setRowStretch(r, 1)

    def _build_video_source(self, kind, path):
        std = self.standard
        if kind == 'video' and path:
            frames = VideoFile(path, std.width, std.height,
                               frame_rate=std.frame_rate)
        else:
            frames = TestPattern(std.width, std.height)
        # A Python block, so it must stay referenced or the scheduler
        # segfaults with no Python frame in the traceback.
        self.video_frames = ntsc_source(frames, self.video_rate, standard=std)
        self.video_source = self.video_frames
        self.sourceDescription = f"{frames.description} ({std.key.upper()})"

    def _build_audio_source(self, kind, audio_path, video_kind, video_path):
        """The programme sound, conditioned and at the radio's rate.

        The clip's own track by default, as in the NTSC transmitter; a
        ``.wav`` from the media folder for the sources that have none; or
        silence, which still sends the subcarriers - an FPV transmitter with
        no microphone does exactly that.
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
                    print(f"Could not open the clip's sound: {exc}", file=sys.stderr)
            else:
                print(f"{os.path.basename(video_path)} has no soundtrack; "
                      "the subcarriers go out unmodulated.", file=sys.stderr)
        if head is None and kind == 'file' and audio_path and os.path.exists(audio_path):
            # WAV or MP3; the rate is the file's own for a WAV, and 48 kHz
            # for an MP3, which ffmpeg resamples as it decodes.
            self.audio_file = AudioFileSource(audio_path)
            head = self.audio_file.block
            rate = self.audio_file.rate
            self.soundDescription = os.path.basename(audio_path)
        if head is None:
            self.audio_head = analog.sig_source_f(RF_RATE, analog.GR_CONST_WAVE,
                                                  0, 0, 0)
            self.audio_source = self.audio_head
            self.soundDescription = "silence"
            return
        self.audio_head = head
        self.audio_conditioner = AudioConditioner(self.profile.audio_tau, rate)
        self.connect(head, self.audio_conditioner)
        self.audio_source = self.audio_conditioner

    def close_sources(self):
        """End the sound's pipe - the clip's, or an MP3's - once stopped.

        The video source closes its own ffmpeg.
        """
        for source in (getattr(self, 'audio_track', None),
                       getattr(self, 'audio_file', None)):
            if source is not None:
                source.close()

    def closeEvent(self, event):
        self.settings = Qt.QSettings("GNU Radio", "fmVideoXmitter")
        self.settings.setValue("geometry", self.saveGeometry())
        self.stop()
        self.wait()
        self.close_sources()
        event.accept()

    def set_rfPwr(self, rfPwr):
        self.rfPwr = rfPwr
        if self.radio_type == 'vsg':
            self.radio_sink.set_level(scale_power(self.rfPwr, self._power_range))
        elif self.radio_type == 'usrp':
            self.radio_sink.set_gain(scale_power(self.rfPwr, self._power_range), 0)
        else:
            self.radio_sink.set_gain(0, 'VGA', scale_power(self.rfPwr, self._power_range))

    def set_cf(self, cf):
        self.cf = cf
        self.qtgui_freq_sink.set_frequency_range(self.cf * 1e6, RF_RATE)
        if self.radio_type == 'usrp':
            self.radio_sink.set_center_freq(self.cf * 1e6, 0)
        else:
            self.radio_sink.set_frequency(0, self.cf * 1e6)

    def set_deviation(self, mhz):
        self.deviation_mhz = float(mhz)
        self.deviation_pp = self.deviation_mhz * 1e6
        self.modulator.set_deviation(self.deviation_pp)


def main(top_block_cls=fmVideoXmitter, options=None, app=None, config_values=None):
    own_app = app is None
    if own_app:
        app = Qt.QApplication(sys.argv)

    tb = top_block_cls(config_values)
    tb.start()
    tb.show()

    def sig_handler(sig=None, frame=None):
        tb.stop()
        tb.wait()
        tb.close_sources()
        Qt.QApplication.quit()

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    timer = Qt.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    # Inside the launcher its event loop is already running: hand the window
    # back for it to watch, rather than starting a loop of our own - see the
    # ATSC transmitter's notes in devnotes/atsc.md for what that did.
    if not own_app:
        return tb
    return app.exec_()


if __name__ == '__main__':
    main()
