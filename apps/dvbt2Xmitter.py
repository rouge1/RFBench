#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DVB-T2 Transmitter: a multiplex of several programmes on an 8 MHz channel.

Pick any number of videos from the media folder (and the built-in colour bars); each
becomes a programme of one DVB-T2 multiplex, named from its file. ffmpeg muxes them into
one transport stream as they play, and gr-dtv's DVB-T2 modulator sends it, at 10 MS/s on any
of the three transmitters - see ``apps/dvbt2_tx.py`` for the chain and why it is live, and
``knowledge/dvbt2.md`` for the standard.

The layout is the one tested over the air VSG60 to BB60D (16k FFT, extended carriers, guard
19/128, PP2, one PLP), which our receiver decodes. The dialog chooses the constellation and
code rate, so how much fits, and checks the programmes chosen do.
"""

from packaging.version import Version as StrictVersion

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

from PyQt5 import Qt, QtCore  # type: ignore
import sip  # type: ignore

from gnuradio import blocks, gr, qtgui, soapy, uhd  # type: ignore
from gnuradio.fft import window  # type: ignore
from gnuradio.qtgui import Range, RangeWidget  # type: ignore

from apps import dvbt2_tx
# The UHF plan, shared with the receiver's dialog.
from apps.dvbt2_tx import DEFAULT_CHANNEL, channel_center_mhz, channel_items
from apps.dvbt2 import t2_tx
from apps.ntsc_source import VIDEO_EXTENSIONS, have_ffmpeg, video_files
from apps.utils import (CONFIG_DIR, apply_dark_theme, apply_flowgraph_theme, radio_label,
                        read_settings, update_app_config, power_percent, resolve_power_range,
                        scale_power, SPECTRUM_Y_AXIS, FrequencyChooser, frequency_range)

#: The layout every constellation is sent in: what was tested over the air and what our
#: receiver decodes (16k or 32k, one PLP).
LAYOUT = dict(fft=16384, guard='19/128', pp=2, extended=True, bandwidth=8e6)

#: The constellations and code rates the dialog offers.
MODULATIONS = ('QPSK', '16QAM', '64QAM', '256QAM')
CODE_RATES = ('1/2', '3/5', '2/3', '3/4', '4/5', '5/6')

def plan_for(mod, rate):
    return t2_tx.frame_plan(mod=mod, rate=rate, **LAYOUT)


class ConfigDialog(Qt.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DVB-T2 Transmitter Configuration")
        self.layout = Qt.QVBoxLayout(self)
        self.config_dir = CONFIG_DIR
        self.config_file = os.path.join(self.config_dir, "dvbt2Xmitter_config.json")

        settings = read_settings()
        self.usrp_ip = settings.get('usrp_ip', '')
        self.radio_type = settings.get('radio_type', 'hackrf')
        self.media_dir = settings.get('media_directory', '')

        self.button_box = Qt.QDialogButtonBox(Qt.QDialogButtonBox.Ok | Qt.QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        self.layout.addWidget(Qt.QLabel(radio_label(self.radio_type, self.usrp_ip)))
        self.cf_chooser = FrequencyChooser(minimum=50.0, maximum=2200.0,
                                           value=channel_center_mhz(DEFAULT_CHANNEL),
                                           channels=channel_items())
        self.layout.addWidget(self.cf_chooser)
        self.create_power_control()
        self.create_mode_controls()
        self.create_programme_list()
        self.capacity_label = Qt.QLabel()
        self.capacity_label.setWordWrap(True)
        self.layout.addWidget(self.capacity_label)
        self.layout.addWidget(self.button_box)

        self.load_config()
        self.update_capacity()
        apply_dark_theme(self)

    def create_power_control(self):
        row = Qt.QHBoxLayout()
        self.pwr_slider = Qt.QSlider(QtCore.Qt.Horizontal)
        self.pwr_slider.setRange(0, 100)
        self.pwr_slider.setValue(50)
        self.pwr_label = Qt.QLabel("Power Level: 50%")
        self.pwr_slider.valueChanged.connect(lambda v: self.pwr_label.setText(f"Power Level: {v}%"))
        row.addWidget(self.pwr_label)
        row.addWidget(self.pwr_slider)
        self.layout.addLayout(row)

    def create_mode_controls(self):
        row = Qt.QHBoxLayout()
        row.addWidget(Qt.QLabel("Constellation:"))
        self.mod_combo = Qt.QComboBox()
        self.mod_combo.addItems(MODULATIONS)
        self.mod_combo.setCurrentText('16QAM')
        row.addWidget(self.mod_combo)
        self.layout.addLayout(row)
        row = Qt.QHBoxLayout()
        row.addWidget(Qt.QLabel("Code Rate:"))
        self.rate_combo = Qt.QComboBox()
        self.rate_combo.addItems(CODE_RATES)
        self.rate_combo.setCurrentText('3/5')
        row.addWidget(self.rate_combo)
        self.layout.addLayout(row)
        self.mod_combo.currentTextChanged.connect(lambda _: self.update_capacity())
        self.rate_combo.currentTextChanged.connect(lambda _: self.update_capacity())

    def create_programme_list(self):
        """Colour bars, then every video in the media folder, each with a tick box: each
        ticked one is a programme of the multiplex."""
        self.layout.addWidget(Qt.QLabel("Programmes (tick each one to send):"))
        self.programme_list = Qt.QListWidget()
        self.programme_list.setMinimumHeight(160)
        ffmpeg = have_ffmpeg()

        def add(text, path, enabled=True):
            item = Qt.QListWidgetItem(text)
            item.setData(QtCore.Qt.UserRole, path)
            flags = item.flags() | QtCore.Qt.ItemIsUserCheckable
            if not enabled:
                flags &= ~QtCore.Qt.ItemIsEnabled
            item.setFlags(flags)
            item.setCheckState(QtCore.Qt.Unchecked)
            self.programme_list.addItem(item)

        if not ffmpeg:
            add("DVB-T2 needs ffmpeg, which is not installed here", None, enabled=False)
        else:
            add("Colour bars and 1 kHz tone (built in)", dvbt2_tx.COLOUR_BARS)
            if self.media_dir and os.path.isdir(self.media_dir):
                try:
                    for name, path in video_files(self.media_dir, VIDEO_EXTENSIONS):
                        add(name, path)
                except Exception as e:
                    add(f"Could not read the media directory: {e}", None, enabled=False)
        self.programme_list.itemChanged.connect(lambda _: self.update_capacity())
        self.layout.addWidget(self.programme_list)

    def chosen(self):
        out = []
        for i in range(self.programme_list.count()):
            item = self.programme_list.item(i)
            path = item.data(QtCore.Qt.UserRole)
            if path and item.checkState() == QtCore.Qt.Checked:
                out.append(path)
        return out

    def plan(self):
        return plan_for(self.mod_combo.currentText(), self.rate_combo.currentText())

    def update_capacity(self):
        """Say what the multiplex carries and whether the ticked programmes fit; OK only when
        they do and the radio is ready."""
        plan = self.plan()
        n = len(self.chosen())
        most = dvbt2_tx.most_programmes(plan)
        text = (f"{plan['ts_bps'] / 1e6:.2f} Mb/s: room for up to {most} programme"
                f"{'s' if most != 1 else ''}. ")
        if n == 0:
            text += "Tick at least one."
        elif dvbt2_tx.fits(plan, n):
            text += (f"{n} ticked, each picture at "
                     f"{dvbt2_tx.video_bps(plan, n) / 1e6:.2f} Mb/s.")
        else:
            text += f"{n} ticked: too many for this constellation and rate."
        self.capacity_label.setText(text)
        ok = self.button_box.button(Qt.QDialogButtonBox.Ok)
        ready = (self.radio_type != 'usrp' or bool(self.usrp_ip)) and dvbt2_tx.fits(plan, n)
        ok.setEnabled(ready)
        if ready:
            ok.setGraphicsEffect(None)
        else:
            dim = Qt.QGraphicsOpacityEffect()
            dim.setOpacity(0.30)
            ok.setGraphicsEffect(dim)

    def load_config(self):
        if not os.path.exists(self.config_file):
            os.makedirs(self.config_dir, exist_ok=True)
            # Nothing saved: the bars, so the dialog opens ready to send.
            self._tick({dvbt2_tx.COLOUR_BARS})
            return
        try:
            with open(self.config_file) as f:
                config = json.load(f)
        except (OSError, ValueError):
            config = {}
        self.cf_chooser.setValue(config.get('center_freq', channel_center_mhz(DEFAULT_CHANNEL)))
        self.pwr_slider.setValue(power_percent(config.get('power_level'), 50))
        if config.get('modulation') in MODULATIONS:
            self.mod_combo.setCurrentText(config['modulation'])
        if config.get('code_rate') in CODE_RATES:
            self.rate_combo.setCurrentText(config['code_rate'])
        self._tick(set(config.get('programmes') or [dvbt2_tx.COLOUR_BARS]))

    def _tick(self, saved):
        """Tick what was saved: the exact path where it is still listed, else a clip of the
        same name (the media folder can move - see knowledge/media.md)."""
        names = {os.path.splitext(os.path.basename(p))[0] for p in saved if p != dvbt2_tx.COLOUR_BARS}
        exact = {os.path.normcase(p) for p in saved}
        for i in range(self.programme_list.count()):
            item = self.programme_list.item(i)
            path = item.data(QtCore.Qt.UserRole)
            if not path or not (item.flags() & QtCore.Qt.ItemIsEnabled):
                continue
            stem = os.path.splitext(os.path.basename(path))[0]
            if os.path.normcase(path) in exact or (path != dvbt2_tx.COLOUR_BARS and stem in names):
                item.setCheckState(QtCore.Qt.Checked)

    def save_config(self):
        update_app_config(self.config_file, {
            'center_freq': self.cf_chooser.value(),
            'power_level': self.pwr_slider.value(),
            'modulation': self.mod_combo.currentText(),
            'code_rate': self.rate_combo.currentText(),
            'programmes': self.chosen(),
        })

    def accept(self):
        self.save_config()
        super().accept()

    def get_values(self):
        return {
            'radio_type': self.radio_type,
            'ipXmitAddr': self.usrp_ip if self.radio_type == 'usrp' else '',
            'cf': self.cf_chooser.value(),
            'pwr': self.pwr_slider.value(),
            'modulation': self.mod_combo.currentText(),
            'code_rate': self.rate_combo.currentText(),
            'programmes': self.chosen() or [dvbt2_tx.COLOUR_BARS],
        }


class dvbt2Xmitter(gr.top_block, Qt.QWidget):
    # What this window's own controls change that its dialog should open on next time -
    # see apps/utils.py: save_flowgraph_settings.
    SAVED_SETTINGS = {'power_level': 'rfPwr', 'center_freq': 'cf'}

    def __init__(self, config_values=None):
        gr.top_block.__init__(self, "DVB-T2 Transmitter", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("DVB-T2 Transmitter")
        apply_flowgraph_theme(self)
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

        if config_values is None:
            dialog = ConfigDialog()
            if not dialog.exec_():
                sys.exit(0)
            values = dialog.get_values()
        else:
            values = config_values
        self.radio_type = radio_type = values.get('radio_type', 'hackrf')
        address = values.get('ipXmitAddr', '')
        self.cf = cf = float(values['cf'])
        self.rfPwr = rfPwr = values['pwr']
        self.plan = plan = plan_for(values.get('modulation', '16QAM'), values.get('code_rate', '3/5'))
        self.programmes = list(values.get('programmes') or [dvbt2_tx.COLOUR_BARS])
        if not dvbt2_tx.fits(plan, len(self.programmes)):
            raise ValueError(f"{len(self.programmes)} programmes do not fit "
                             f"{plan['mod']} {plan['rate']} ({plan['ts_bps'] / 1e6:.2f} Mb/s)")
        self.samp_rate = samp_rate = dvbt2_tx.OUT_RATE

        # -- controls
        self._rfPwr_range = Range(0, 100, 1, rfPwr, 200)
        self._rfPwr_win = RangeWidget(self._rfPwr_range, self.set_rfPwr, "RF Output Power (%)",
                                      "counter_slider", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._rfPwr_win, 1, 0, 1, 5)
        self._cf_range = frequency_range(50, 2200, 0.1, cf, 200)
        self._cf_win = RangeWidget(self._cf_range, self.set_cf, "Center Frequency (MHz)",
                                   "counter", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._cf_win, 0, 0, 1, 5)
        mode = Qt.QLabel(f"DVB-T2  {dvbt2_tx.describe(plan, self.programmes)}")
        mode.setWordWrap(True)
        self.top_grid_layout.addWidget(mode, 0, 5, 1, 5)
        names = ', '.join(f"{i + 1} {dvbt2_tx.service_name(p)}" for i, p in enumerate(self.programmes))
        self._programmes_label = Qt.QLabel(f"Programmes: {names}")
        self._programmes_label.setWordWrap(True)
        self.top_grid_layout.addWidget(self._programmes_label, 1, 5, 1, 5)
        self._status_label = Qt.QLabel("")
        self.top_grid_layout.addWidget(self._status_label, 2, 0, 1, 10)
        for c in range(10):
            self.top_grid_layout.setColumnStretch(c, 1)

        # -- the radio, at 10 MS/s on all three (the N210 as 100/10, an even ratio)
        self._power_range = resolve_power_range(radio_type)
        if radio_type == 'vsg':
            from apps.vsg_sink import vsg_sink
            self.radio_sink = vsg_sink(center_freq=cf * 1e6, sample_rate=samp_rate,
                                       level_dbm=scale_power(rfPwr, self._power_range))
        elif radio_type == 'usrp':
            self.radio_sink = uhd.usrp_sink(",".join(('addr=' + address, '')),
                                            uhd.stream_args(cpu_format="fc32", args='', channels=[0]), "")
            self.radio_sink.set_samp_rate(samp_rate)
            got = self.radio_sink.get_samp_rate()
            if abs(got - samp_rate) > 1:
                print(f"DVB-T2: the Ettus gave {got / 1e6:g} MS/s for {samp_rate / 1e6:g}", file=sys.stderr)
            self.radio_sink.set_time_now(uhd.time_spec(time.time()), uhd.ALL_MBOARDS)
            self.radio_sink.set_center_freq(cf * 1e6, 0)
            self.radio_sink.set_antenna("TX/RX", 0)
            self._power_range = resolve_power_range(radio_type, self.radio_sink)
            self.radio_sink.set_gain(scale_power(rfPwr, self._power_range), 0)
        else:
            self.radio_sink = soapy.sink('driver=hackrf', 'fc32', 1, '', '', [''], [''])
            self.radio_sink.set_sample_rate(0, samp_rate)
            self.radio_sink.set_frequency(0, cf * 1e6)
            self.radio_sink.set_gain(0, 'VGA', scale_power(rfPwr, self._power_range))
            self.radio_sink.set_gain(0, 'AMP', 0)

        # -- the spectrum
        self.freq_sink = qtgui.freq_sink_c(2048, window.WIN_BLACKMAN_hARRIS, cf * 1e6, samp_rate, "", 1, None)
        self.freq_sink.set_update_time(0.10)
        self.freq_sink.set_y_axis(*SPECTRUM_Y_AXIS)
        self.freq_sink.set_y_label('Relative Gain', 'dB')
        self.freq_sink.set_trigger_mode(qtgui.TRIG_MODE_FREE, 0.0, 0, "")
        self.freq_sink.enable_autoscale(False)
        self.freq_sink.enable_grid(True)
        self.freq_sink.set_fft_average(0.2)
        self.freq_sink.enable_axis_labels(True)
        self.freq_sink.enable_control_panel(False)
        self.freq_sink.disable_legend()
        self._freq_sink_win = sip.wrapinstance(self.freq_sink.qwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(self._freq_sink_win, 3, 0, 10, 10)
        for r in range(3, 13):
            self.top_grid_layout.setRowStretch(r, 1)

        # -- the multiplex and the modulator. Made after the radio has opened, so a radio that
        # fails to open leaves no ffmpeg behind.
        self.mux = dvbt2_tx.Multiplex(self.programmes, plan)
        self.ts_source = blocks.file_descriptor_source(gr.sizeof_char, self.mux.descriptor(), False)
        self.modulator = dvbt2_tx.modulator(plan)
        self.resampler = dvbt2_tx.resampler(plan, samp_rate)
        self.connect(self.ts_source, *self.modulator, self.resampler, self.radio_sink)
        self.connect(self.resampler, self.freq_sink)

        # ffmpeg stopping (a file it cannot read, say) starves the modulator silently: say so.
        self._watch = Qt.QTimer(self)
        self._watch.timeout.connect(self._check_mux)
        self._watch.start(1000)

    def _check_mux(self):
        mux = getattr(self, 'mux', None)
        if mux is None:
            return
        err = mux.error()
        if mux._proc is not None and mux._proc.poll() is not None:
            self._status_label.setText(f"ffmpeg stopped: {err[-300:] or 'no reason given'}")
            self._watch.stop()

    def closeEvent(self, event):
        self._watch.stop()
        self.stop()
        self.wait()
        self.close_stream()
        event.accept()

    def close_stream(self):
        """Stop the ffmpeg making the multiplex, once the flowgraph has stopped: nothing reads
        its pipe after that, and it would sit blocked on it as long as the launcher is open."""
        mux, self.mux = getattr(self, 'mux', None), None
        if mux is not None:
            mux.close()

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

    def get_cf(self):
        return self.cf

    def set_cf(self, cf):
        self.cf = cf
        self.freq_sink.set_frequency_range(self.cf * 1e6, self.samp_rate)
        if self.radio_type == 'usrp':
            self.radio_sink.set_center_freq(self.cf * 1e6, 0)
        else:
            self.radio_sink.set_frequency(0, self.cf * 1e6)

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.freq_sink.set_frequency_range(self.cf * 1e6, self.samp_rate)
        if self.radio_type == 'usrp':
            self.radio_sink.set_samp_rate(self.samp_rate)
        else:
            self.radio_sink.set_sample_rate(0, self.samp_rate)


def main(top_block_cls=dvbt2Xmitter, options=None, app=None, config_values=None):
    own_app = app is None
    if app is None:
        if StrictVersion("4.5.0") <= StrictVersion(Qt.qVersion()) < StrictVersion("5.0.0"):
            Qt.QApplication.setGraphicsSystem(gr.prefs().get_string('qtgui', 'style', 'raster'))
        app = Qt.QApplication(sys.argv)
        apply_dark_theme(app)

    tb = top_block_cls(config_values)
    tb.start()
    tb.show()

    def sig_handler(sig=None, frame=None):
        def _stop():
            tb.stop()
            tb.wait()
            tb.close_stream()
            app.quit()
        Qt.QTimer.singleShot(0, _stop)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)
    timer = Qt.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)
    if not own_app:
        return tb
    return app.exec_()


if __name__ == '__main__':
    main()
