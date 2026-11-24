#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# SPDX-License-Identifier: GPL-3.0
#
# PAL Video Transmitter - a 625-line PAL picture and its sound on a
# broadcast television channel, System B, G or I. The PAL counterpart of
# ``ntscAnalogVideoRecorded``: the same sources (the built-in bars, any
# clip ffmpeg reads, with its own sound or a file's), with the radiated
# signal taken from ITU-R BT.1701 Table 1 through ``apps/tv_system.py``.
# Its receiver is ``palReceiver``; the two share a tile in the launcher.

if __name__ == '__main__':
    import ctypes
    import sys
    if sys.platform.startswith('linux'):
        try:
            x11 = ctypes.cdll.LoadLibrary('libX11.so')
            x11.XInitThreads()
        except Exception:
            print("Warning: failed to XInitThreads()")

import os
import signal
import sys
import time
from fractions import Fraction

from gnuradio import analog, blocks, filter, gr, qtgui, soapy, uhd  # type: ignore
from gnuradio.fft import window  # type: ignore
from gnuradio.qtgui import Range, RangeWidget  # type: ignore
from PyQt5 import Qt, QtCore  # type: ignore
try:
    import sip  # type: ignore
except ImportError:  # pragma: no cover - depends on the PyQt5 build
    from PyQt5 import sip  # type: ignore

from apps.audio_file import AudioFileSource
from apps.media import AUDIO, choices
from apps.ntsc_encode import PAL
from apps.ntsc_source import (AudioTrack, TestPattern, VideoFile, has_audio,
                              have_ffmpeg, ntsc_source, video_files)
from apps.tv_system import (AUDIO_RATE, PAL_CHOICES, VsbTransmitter,
                            pal_channel_centre,
                            pal_channel_items, pal_system)
from apps.utils import (CONFIG_DIR, apply_dark_theme, apply_flowgraph_theme,
                        radio_label, read_settings, update_app_config,
                        power_percent, resolve_power_range, scale_power,
                        SPECTRUM_Y_AXIS, FrequencyChooser, frequency_range)

#: A free UHF channel as the default, and PAL B/G on it - System G.
DEFAULT_CHANNEL = 'E21'                 # 474 MHz, 470-478
DEFAULT_SYSTEM = 'BG'
FREQ_MIN_MHZ = 45.0
FREQ_MAX_MHZ = 2200.0

#: The radio's rate. The composite is built at PAL's 12.5 MS/s and
#: interpolated 8/5 to 20, which the HackRF and the VSG60 both take; an
#: Ettus N210 takes 25 instead - 100 MHz over 4, an even number, with its
#: halfband filter on. Its 20 is an odd interpolation that tilts the band
#: (knowledge/ettus.md), which is why the NTSC transmitter runs 25 there
#: too.
RADIO_RATES = {'hackrf': 20e6, 'vsg': 20e6, 'usrp': 25e6}


class ConfigDialog(Qt.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PAL Video Transmitter Configuration")
        self.layout = Qt.QVBoxLayout(self)
        self.config_file = os.path.join(CONFIG_DIR,
                                        "palAnalogVideoRecorded_config.json")
        settings = read_settings()
        self.usrp_ip = settings.get('usrp_ip', '')
        self.radio_type = settings.get('radio_type', 'hackrf')
        self.media_dir = settings.get('media_directory', '')

        self.button_box = Qt.QDialogButtonBox(
            Qt.QDialogButtonBox.Ok | Qt.QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        self.layout.addWidget(Qt.QLabel(radio_label(self.radio_type,
                                                    self.usrp_ip)))
        self.create_system_control()
        self.cf_chooser = FrequencyChooser(
            minimum=FREQ_MIN_MHZ, maximum=FREQ_MAX_MHZ,
            value=pal_channel_centre(DEFAULT_CHANNEL),
            channels=pal_channel_items())
        self.layout.addWidget(self.cf_chooser)
        self.create_power_control()
        self.create_video_selector()
        self.create_audio_controls()
        self.system_note = Qt.QLabel("")
        self.system_note.setWordWrap(True)
        self.layout.addWidget(self.system_note)
        self.system_combo.currentIndexChanged.connect(self._describe_system)
        self.cf_chooser.valueChanged.connect(self._describe_system)
        self.layout.addWidget(self.button_box)
        self.load_config()
        self._describe_system()
        self.update_ok_state()
        apply_dark_theme(self)

    def create_system_control(self):
        self.layout.addWidget(Qt.QLabel("Television System:"))
        self.system_combo = Qt.QComboBox()
        for key, label in PAL_CHOICES:
            self.system_combo.addItem(label, key)
        self.system_combo.setToolTip(
            "B/G is most of Europe: 7 MHz channels on VHF (System B), 8 MHz "
            "on UHF (System G), sound 5.5 MHz above the picture. I is the "
            "UK and Ireland: 8 MHz, sound 6.0 MHz up, a wider vestige and "
            "shallower modulation.")
        self.layout.addWidget(self.system_combo)

    def _describe_system(self, *_):
        system = pal_system(self.system_combo.currentData() or DEFAULT_SYSTEM,
                            self.cf_chooser.value())
        vision = self.cf_chooser.value() + system.visual_carrier / 1e6
        self.system_note.setText(
            f"{system.label}: vision carrier {vision:.2f} MHz, sound "
            f"{vision + system.aural_spacing / 1e6:.4f} MHz.")

    def create_power_control(self):
        row = Qt.QHBoxLayout()
        self.pwr_slider = Qt.QSlider(QtCore.Qt.Horizontal)
        self.pwr_slider.setRange(0, 100)
        self.pwr_slider.setValue(50)
        self.pwr_label = Qt.QLabel("Power Level: 50%")
        self.pwr_slider.valueChanged.connect(
            lambda v: self.pwr_label.setText(f"Power Level: {v}%"))
        row.addWidget(self.pwr_label)
        row.addWidget(self.pwr_slider)
        self.layout.addLayout(row)

    def create_video_selector(self):
        """The built-in bars, then every clip in the media folder.

        The NTSC transmitter's ``.dat`` stills are not offered: they are
        525-line composite captures, and no resampling makes them 625.
        """
        self.video_combo = Qt.QComboBox()
        self.video_combo.addItem("PAL colour bars (built in)", ('pattern', None))
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
        """The clip's own sound by default, as in the NTSC transmitter."""
        self.layout.addWidget(Qt.QLabel("Sound:"))
        self.audio_combo = Qt.QComboBox()
        self.audio_combo.addItem("From the video clip", ('clip', None))
        self.audio_combo.addItem("Silence - sound carrier only",
                                 ('silence', None))
        files = choices(self.media_dir, AUDIO)
        if files:
            self.audio_combo.insertSeparator(self.audio_combo.count())
        for label, path in files:
            self.audio_combo.addItem(label, ('file', path))
        self.layout.addWidget(self.audio_combo)
        self._audio_forced = False
        self.audio_combo.activated.connect(self._audio_picked)
        self.video_combo.currentIndexChanged.connect(self._sync_audio_choice)
        self._sync_audio_choice()

    def _audio_picked(self, _index):
        self._audio_forced = False

    def _sync_audio_choice(self):
        """'From the video clip' only means something for a clip."""
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

    def update_ok_state(self):
        ok = self.button_box.button(Qt.QDialogButtonBox.Ok)
        ready = self.radio_type != 'usrp' or bool(self.usrp_ip)
        if self.radio_type == 'bb60':
            ready = False
        ok.setEnabled(ready)
        if ready:
            ok.setGraphicsEffect(None)
        else:
            dim = Qt.QGraphicsOpacityEffect()
            dim.setOpacity(0.30)
            ok.setGraphicsEffect(dim)

    def load_config(self):
        import json
        if not os.path.exists(self.config_file):
            os.makedirs(CONFIG_DIR, exist_ok=True)
            return
        try:
            with open(self.config_file) as f:
                config = json.load(f)
        except Exception as exc:
            print(f"PAL transmitter: could not read saved config: {exc}",
                  file=sys.stderr)
            return
        index = self.system_combo.findData(config.get('system', DEFAULT_SYSTEM))
        self.system_combo.setCurrentIndex(max(index, 0))
        self.cf_chooser.setValue(config.get(
            'center_freq', pal_channel_centre(DEFAULT_CHANNEL)))
        self.pwr_slider.setValue(power_percent(config.get('power_level'), 50))
        saved = config.get('video_source')
        if saved:
            for i in range(self.video_combo.count()):
                data = self.video_combo.itemData(i)
                if data and data[1] == saved:
                    self.video_combo.setCurrentIndex(i)
                    break
        kind, path = config.get('audio_kind'), config.get('audio_file')
        if kind:
            for i in range(self.audio_combo.count()):
                data = self.audio_combo.itemData(i)
                if data and data[0] == kind and data[1] == path:
                    self.audio_combo.setCurrentIndex(i)
                    break
            self._sync_audio_choice()

    def audio_choice(self):
        return self.audio_combo.currentData() or ('silence', None)

    def save_config(self):
        kind, path = self.video_combo.currentData() or ('pattern', None)
        audio_kind, audio_path = self.audio_choice()
        update_app_config(self.config_file, {
            'system': self.system_combo.currentData(),
            'center_freq': self.cf_chooser.value(),
            'power_level': self.pwr_slider.value(),
            'video_kind': kind,
            'video_source': path,
            'audio_kind': audio_kind,
            'audio_file': audio_path,
        })

    def accept(self):
        self.save_config()
        super().accept()

    def get_values(self):
        kind, path = self.video_combo.currentData() or ('pattern', None)
        audio_kind, audio_path = self.audio_choice()
        return {
            'radio_type': self.radio_type,
            'ipXmitAddr': self.usrp_ip if self.radio_type == 'usrp' else '',
            'system': self.system_combo.currentData(),
            'cf': self.cf_chooser.value(),
            'pwr': self.pwr_slider.value(),
            'videoKind': kind,
            'videoFileName': path,
            'audioKind': audio_kind,
            'audioFileName': audio_path,
        }


class palAnalogVideoRecorded(gr.top_block, Qt.QWidget):
    # What this window's own controls change that its dialog should open
    # on next time - see apps/utils.py: save_flowgraph_settings.
    SAVED_SETTINGS = {'power_level': 'rfPwr', 'center_freq': 'cf'}

    def __init__(self, config_values=None):
        gr.top_block.__init__(self, "PAL Video Transmitter",
                              catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("PAL Video Transmitter")
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

        self.settings = Qt.QSettings("GNU Radio", "palAnalogVideoRecorded")
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
        self.outputIpAddr = values.get('ipXmitAddr', '')
        self.cf = float(values['cf'])
        self.rfPwr = values['pwr']
        self.system = pal_system(values.get('system') or DEFAULT_SYSTEM, self.cf)
        self.samp_rate = self.system.composite_rate
        self._radio_rate = RADIO_RATES.get(self.radio_type, 20e6)
        self.lo_offset = self.system.lo_offset

        self._build_controls()
        self._build_radio()
        self._build_video_source(values.get('videoKind', 'pattern'),
                                 values.get('videoFileName'))
        self._build_audio_source(values.get('audioKind', 'clip'),
                                 values.get('audioFileName'),
                                 values.get('videoKind', 'pattern'),
                                 values.get('videoFileName'))
        self.transmitter = VsbTransmitter(self.system, self.radio_rate())
        self.connect(self.video_source, (self.transmitter, 0))
        self.connect(self.audio_source, (self.transmitter, 1))
        self.connect(self.transmitter, self.radio_sink)
        self._build_plots()

    # ------------------------------------------------------------ window
    def _build_controls(self):
        bar = Qt.QToolBar(self)
        bar.addWidget(Qt.QLabel("Signal Type: "))
        vision = self.cf + self.system.visual_carrier / 1e6
        self._signalType_label = Qt.QLabel(
            f"{self.system.label} - vision {vision:.2f} MHz, sound "
            f"{vision + self.system.aural_spacing / 1e6:.4f} MHz")
        bar.addWidget(self._signalType_label)
        self.top_grid_layout.addWidget(bar, 0, 0, 1, 5)
        self._rfPwr_range = Range(0, 100, 1, self.rfPwr, 200)
        self._rfPwr_win = RangeWidget(self._rfPwr_range, self.set_rfPwr,
                                      "RF Output Power (%)", "counter_slider",
                                      float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._rfPwr_win, 1, 0, 1, 5)
        self._cf_range = frequency_range(FREQ_MIN_MHZ, FREQ_MAX_MHZ, 0.01,
                                         self.cf, 200)
        self._cf_win = RangeWidget(self._cf_range, self.set_cf,
                                   "Center Frequency (MHz)", "counter", float,
                                   QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._cf_win, 0, 5, 1, 5)
        self.source_label = Qt.QLabel("")
        self.top_grid_layout.addWidget(self.source_label, 1, 5, 1, 5)
        for c in range(10):
            self.top_grid_layout.setColumnStretch(c, 1)

    def _build_radio(self):
        tuned = self.cf * 1e6 + self.lo_offset
        self._power_range = resolve_power_range(self.radio_type)
        if self.radio_type == 'vsg':
            from apps.vsg_sink import vsg_sink
            self.radio_sink = vsg_sink(
                center_freq=tuned, sample_rate=self.radio_rate(),
                level_dbm=scale_power(self.rfPwr, self._power_range))
        elif self.radio_type == 'usrp':
            self.radio_sink = uhd.usrp_sink(
                ",".join(('addr=' + self.outputIpAddr, '')),
                uhd.stream_args(cpu_format="fc32", args='',
                                channels=list(range(0, 1))), "")
            self.radio_sink.set_samp_rate(self.radio_rate())
            self.radio_sink.set_time_now(uhd.time_spec(time.time()),
                                         uhd.ALL_MBOARDS)
            self.radio_sink.set_center_freq(tuned, 0)
            self.radio_sink.set_antenna("TX/RX", 0)
            self._power_range = resolve_power_range(self.radio_type,
                                                    self.radio_sink)
            self.radio_sink.set_gain(
                scale_power(self.rfPwr, self._power_range), 0)
        else:
            self.radio_sink = soapy.sink('driver=hackrf', 'fc32', 1, '', '',
                                         [''], [''])
            self.radio_sink.set_sample_rate(0, self.radio_rate())
            self.radio_sink.set_frequency(0, tuned)
            self.radio_sink.set_gain(
                0, 'VGA', scale_power(self.rfPwr, self._power_range))
            self.radio_sink.set_gain(0, 'AMP', 0)

    def _build_plots(self):
        """The composite as it leaves the encoder, and what goes to the radio.

        The spectrum is the radio's whole band, labelled in RF: centred on
        where the radio is tuned, ``lo_offset`` above the channel, so the
        channel sits to the left of centre and the LO - where a radio leaks
        - to its right, outside it. The channel itself is built at the
        radio's rate (see VsbTransmitter), so there is no channel-rate
        signal to plot without paying to make one.
        """
        self.time_sink = qtgui.time_sink_f(
            int(round(2.2 * PAL.line * self.samp_rate)), self.samp_rate,
            'PAL Composite (two lines)', 1, None)
        self.time_sink.set_update_time(0.05)
        self.time_sink.set_y_axis(-0.1, 1.2)
        self.time_sink.set_y_label('Composite (sync 0, white 1)', "")
        self.time_sink.set_trigger_mode(qtgui.TRIG_MODE_FREE,
                                        qtgui.TRIG_SLOPE_POS, 0.0, 0, 0, "")
        self.time_sink.enable_autoscale(False)
        self.time_sink.enable_grid(True)
        self.time_sink.enable_axis_labels(True)
        self.time_sink.enable_control_panel(False)
        self.time_sink.disable_legend()
        widget = sip.wrapinstance(self.time_sink.qwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(widget, 2, 0, 5, 10)
        self.connect(self.video_source, self.time_sink)

        self.freq_sink = qtgui.freq_sink_c(
            8192, window.WIN_BLACKMAN_hARRIS, self.cf * 1e6 + self.lo_offset,
            self.radio_rate(),
            f'RF Spectrum - channel {self.cf:g} MHz, radio tuned '
            f'{self.lo_offset / 1e6:g} MHz above it', 1, None)
        self.freq_sink.set_update_time(0.10)
        self.freq_sink.set_y_axis(*SPECTRUM_Y_AXIS)
        self.freq_sink.set_y_label('Relative Gain', 'dB')
        self.freq_sink.set_trigger_mode(qtgui.TRIG_MODE_FREE, 0.0, 0, "")
        self.freq_sink.enable_autoscale(False)
        self.freq_sink.enable_grid(True)
        self.freq_sink.set_fft_average(1.0)
        self.freq_sink.enable_axis_labels(True)
        self.freq_sink.enable_control_panel(False)
        self.freq_sink.disable_legend()
        widget = sip.wrapinstance(self.freq_sink.qwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(widget, 7, 0, 5, 10)
        self.connect(self.transmitter.add, self.freq_sink)
        for r in range(2, 12):
            self.top_grid_layout.setRowStretch(r, 1)

    # ----------------------------------------------------------- sources
    def _build_video_source(self, kind, path):
        if kind == 'video' and path:
            frames = VideoFile(path, PAL.width, PAL.height,
                               frame_rate=PAL.frame_rate)
        else:
            frames = TestPattern(PAL.width, PAL.height)
        # A Python block: kept referenced, or the scheduler segfaults.
        # Four encoding threads where there are eight cores, not the three
        # NTSC gets: a PAL frame is 1.5 times the samples, and on
        # worklaptop1 three ran just short of real time.
        workers = 4 if (os.cpu_count() or 1) >= 8 else None
        self.pal_frames = ntsc_source(frames, self.samp_rate, standard=PAL,
                                      workers=workers)
        self.video_source = self.pal_frames
        self.sourceDescription = frames.description

    def _build_audio_source(self, kind, audio_path, video_kind, video_path):
        """The sound, pre-emphasised, railed and at 48 kHz.

        The clip's own track, a file from the media folder, or silence, each
        falling back to the next - ``ntscAnalogVideoRecorded`` explains why
        the clip's own is the default. The pre-emphasis is the system's:
        50 us on every PAL system (BT.1701 item 11), not System M's 75.
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
                      "transmitting a silent sound carrier.", file=sys.stderr)
        if head is None and kind == 'file' and audio_path \
                and os.path.exists(audio_path):
            self.audio_file = AudioFileSource(audio_path)
            head = self.audio_file.block
            rate = self.audio_file.rate
            self.soundDescription = os.path.basename(audio_path)
        if head is None:
            head = analog.sig_source_f(AUDIO_RATE, analog.GR_CONST_WAVE,
                                       0, 0, 0)
            rate = AUDIO_RATE
            self.soundDescription = "silence"
            kind = 'silence'
        self.audio_head = head
        tail = head
        if kind != 'silence':
            self.audio_preemph = analog.fm_preemph(
                float(rate), tau=self.system.preemphasis)
            self.audio_rail = analog.rail_ff(-1.0, 1.0)
            self.connect(tail, self.audio_preemph, self.audio_rail)
            tail = self.audio_rail
        # VsbTransmitter takes its sound at 48 kHz and does the rest at a
        # rate that suits FM; a file at another rate is brought to 48 here.
        if int(rate) == int(AUDIO_RATE):
            self.audio_source = tail
        else:
            ratio = Fraction(int(AUDIO_RATE), int(rate)
                             ).limit_denominator(10000)
            self.audio_resampler = filter.rational_resampler_fff(
                interpolation=ratio.numerator, decimation=ratio.denominator,
                taps=[], fractional_bw=0)
            self.connect(tail, self.audio_resampler)
            self.audio_source = self.audio_resampler
        self.source_label.setText(
            f"Picture: {self.sourceDescription}    Sound: "
            f"{self.soundDescription}")

    # ---------------------------------------------------------- controls
    def radio_rate(self):
        """The radio's rate - a method, as in the NTSC transmitter, which
        is how ``scripts/app_tx_check.py`` tells it from ``samp_rate``."""
        return self._radio_rate

    def set_rfPwr(self, rfPwr):
        self.rfPwr = rfPwr
        level = scale_power(self.rfPwr, self._power_range)
        if self.radio_type == 'vsg':
            self.radio_sink.set_level(level)
        elif self.radio_type == 'usrp':
            self.radio_sink.set_gain(level, 0)
        else:
            self.radio_sink.set_gain(0, 'VGA', level)

    def set_cf(self, cf):
        """Retune within the system the window opened on.

        B or G is decided by the channel the transmitter started on; a
        retune across 300 MHz keeps it, since the vision carrier's place in
        the channel is built into the flowgraph.
        """
        self.cf = float(cf)
        self.freq_sink.set_frequency_range(self.cf * 1e6 + self.lo_offset,
                                           self.radio_rate())
        tuned = self.cf * 1e6 + self.lo_offset
        if self.radio_type == 'usrp':
            self.radio_sink.set_center_freq(tuned, 0)
        else:
            self.radio_sink.set_frequency(0, tuned)
        vision = self.cf + self.system.visual_carrier / 1e6
        self._signalType_label.setText(
            f"{self.system.label} - vision {vision:.2f} MHz, sound "
            f"{vision + self.system.aural_spacing / 1e6:.4f} MHz")

    def closeEvent(self, event):
        self.settings = Qt.QSettings("GNU Radio", "palAnalogVideoRecorded")
        self.settings.setValue("geometry", self.saveGeometry())
        self.stop()
        self.wait()
        for source in (getattr(self, 'audio_track', None),
                       getattr(self, 'audio_file', None)):
            if source is not None:
                source.close()
        event.accept()


def main(top_block_cls=palAnalogVideoRecorded, options=None, app=None,
         config_values=None):
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
    return app.exec_()


if __name__ == '__main__':
    main()
