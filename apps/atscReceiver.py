#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# SPDX-License-Identifier: GPL-3.0
#
# ATSC Receiver - tunes a 6 MHz television channel, demodulates 8VSB and
# recovers the MPEG-2 transport stream, reporting what the signal and the
# stream actually look like. The Watch button hands the recovered stream to
# a media player, so the picture is there on demand without this app having
# to contain a video decoder.
#
# It is the receiving half of ``atscXmitter``; the launcher tile flips
# between the two.

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime

import numpy as np  # type: ignore
try:                 # PyQt5 ships sip inside the package; some builds also
    import sip       # expose it at the top level.
except ImportError:  # pragma: no cover - depends on the PyQt5 build
    from PyQt5 import sip  # type: ignore
from gnuradio import analog, blocks, dtv, filter, gr, qtgui, soapy, uhd  # type: ignore
from gnuradio.fft import window  # type: ignore
from gnuradio.filter import firdes  # type: ignore
from PyQt5 import Qt, QtCore  # type: ignore

from apps.atsc_rx_core import (Afc, SYMBOL_RATE, TsAnalyzer,
                               channel_center_mhz, channel_for_center,
                               channels, mer_db, mer_quality, since,
                               tv_channel_items)
from apps.theme import TOKENS
from apps.utils import (CONFIG_DIR, apply_dark_theme, apply_flowgraph_theme,
                        radio_label,
                        read_settings, update_app_config, SPECTRUM_Y_AXIS,
                        FrequencyChooser, FREQ_DECIMALS, align_output_buffer)

# Each radio's own rate, chosen from what it will actually accept:
# SoapyHackRF takes whole megahertz only, and the BB60D's ladder is
# 40/20/10/5/2.5. The receive filter is an arbitrary resampler, so any of
# them reaches the 10.762237 MS/s symbol clock exactly.
SAMPLE_RATES = {'hackrf': 12e6, 'usrp': 12e6, 'bb60': 10e6}
# Samples per symbol into the demodulator. This one number sets how much
# work the receiver does: the arbitrary resampler that carries the radio's
# rate to the symbol clock *is* the bottleneck - measured, everything from
# the equalizer onward is free beside it - and it costs (2*8+1)*sps taps
# per output sample. Measured here at 12 MS/s: 1.5 runs at 1.16x real time
# for 24.0 dB MER, 1.2 at 1.42x for 23.5 dB, 1.1 at 1.57x for 21.4 dB. 1.2
# buys a fifth more headroom for half a decibel; 1.1 starts costing real
# margin. In steady state all of them decode a clean signal at 0.000% bad.
SPS = 1.2
DEFAULT_CHANNEL = 24            # 533 MHz, the channel this bench uses

# 45 ms of signal: at 12 MS/s that is a 22 Hz FFT bin before interpolation,
# which resolves a clock error far finer than the demodulator cares about.
AFC_WINDOW = 1 << 19
# Enough equalized symbols for a steady MER without averaging over history.
MER_WINDOW = 1 << 14

# 188 bytes a packet, and a drop has to take whole packets or the player
# loses sync. Four megabytes is about 1.7 s of stream.
TS_PACKET = 188
PLAYER_BACKLOG = 4 << 20


class sample_tap(gr.sync_block):
    """Keeps the most recent samples so the Qt thread can measure them.

    A ring rather than a queue: the AFC and the level meter both want "what
    is arriving now", and nothing must ever build up behind them.
    """

    def __init__(self, depth, dtype=np.complex64):
        gr.sync_block.__init__(self, name='sample_tap', in_sig=[dtype],
                               out_sig=None)
        self._depth = int(depth)
        self._buf = np.zeros(self._depth, dtype=dtype)
        self._pos = 0
        self._filled = 0
        self._lock = threading.Lock()

    def work(self, input_items, output_items):
        x = input_items[0]
        n = len(x)
        d = self._depth
        with self._lock:
            if n >= d:
                self._buf[:] = x[n - d:]
                self._pos = 0
            else:
                end = self._pos + n
                if end <= d:
                    self._buf[self._pos:end] = x
                    self._pos = end % d
                else:
                    k = d - self._pos
                    self._buf[self._pos:] = x[:k]
                    self._buf[:n - k] = x[k:]
                    self._pos = n - k
            self._filled = min(d, self._filled + n)
        return n

    def latest(self):
        """The buffer in order, oldest first. Empty until it has filled."""
        with self._lock:
            if self._filled < self._depth:
                return self._buf[:0].copy()
            return np.concatenate((self._buf[self._pos:], self._buf[:self._pos]))


class ts_sink(gr.sync_block):
    """The end of the chain: analyse, optionally record, optionally play.

    The player is fed through a *non-blocking* pipe and bytes are dropped
    when it will not keep up. That is deliberate: a blocking write into a
    player that has stalled or been killed would park the GNU Radio
    scheduler thread and take the whole receiver down with it, and there is
    no sensible way for a live receiver to apply backpressure to the air.
    """

    def __init__(self):
        gr.sync_block.__init__(self, name='ts_sink', in_sig=[np.uint8],
                               out_sig=None)
        self.analyzer = TsAnalyzer()
        self._lock = threading.Lock()
        self._player = None
        self._pending = bytearray()
        self._record = None
        self.record_path = None
        self.dropped = 0

    # -- the stream ------------------------------------------------------

    def work(self, input_items, output_items):
        data = input_items[0].tobytes()
        with self._lock:
            self.analyzer.feed(data)
            if self._record is not None:
                try:
                    self._record.write(data)
                except OSError as exc:
                    print(f"ATSC receiver: recording stopped: {exc}",
                          file=sys.stderr)
                    self._close_record()
            if self._player is not None:
                self._pending += data
                self._drain()
        return len(input_items[0])

    def _drain(self):
        """Push what the pipe will take; drop whole packets beyond the cap."""
        if len(self._pending) > PLAYER_BACKLOG:
            # Keep the newest, and cut on a packet boundary so the player
            # resyncs on the very next sync byte rather than hunting.
            excess = len(self._pending) - PLAYER_BACKLOG
            excess += (-excess) % TS_PACKET
            del self._pending[:excess]
            self.dropped += excess
        try:
            written = os.write(self._player.stdin.fileno(), self._pending)
            del self._pending[:written]
        except BlockingIOError:
            pass
        except (OSError, ValueError, AttributeError):
            self._close_player()

    # -- the player ------------------------------------------------------

    def start_player(self, argv):
        """Launch a media player reading the stream from its stdin."""
        self.stop_player()
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        fd = proc.stdin.fileno()
        os.set_blocking(fd, False)
        try:
            # A 64 kB pipe is 27 ms of transport stream, so the default
            # leaves the player no slack at all to decode a frame in.
            import fcntl
            fcntl.fcntl(fd, 1031, 1 << 20)     # F_SETPIPE_SZ
        except (ImportError, OSError):
            pass
        with self._lock:
            self._player = proc
            self._pending = bytearray()
            self.dropped = 0
        return proc

    def stop_player(self):
        with self._lock:
            self._close_player()

    def _close_player(self):
        proc, self._player = self._player, None
        self._pending = bytearray()
        if proc is None:
            return
        try:
            proc.stdin.close()
        except Exception:
            pass
        if proc.poll() is None:
            proc.terminate()

    def player_alive(self):
        with self._lock:
            return self._player is not None and self._player.poll() is None

    # -- recording -------------------------------------------------------

    def start_record(self, path):
        with self._lock:
            self._close_record()
            self._record = open(path, 'wb')
            self.record_path = path
        return path

    def stop_record(self):
        with self._lock:
            path = self.record_path
            self._close_record()
        return path

    def _close_record(self):
        rec, self._record = self._record, None
        if rec is not None:
            try:
                rec.close()
            except Exception:
                pass

    def recording(self):
        with self._lock:
            return self._record is not None

    # -- readout ---------------------------------------------------------

    def snapshot(self):
        with self._lock:
            return self.analyzer.snapshot()

    def programs(self):
        with self._lock:
            return self.analyzer.describe_programs()

    def reset(self):
        with self._lock:
            self.analyzer.reset()


class AtscDemod(gr.hier_block2):
    """Complex baseband on a 6 MHz channel in, transport stream bytes out.

    This is ``gnuradio.dtv.atsc_rx`` built out of the same blocks rather
    than called, for two reasons, both of which the app needs:

    - **The resampler has to stay reachable.** It is half of the AFC. A
      transmitter whose clock is a few parts per million fast moves the
      pilot *and* stretches the symbol rate, and only this block can undo
      the second. ``atsc_rx`` builds it as a local and drops it.
    - **The decoder knows how well it is doing.** ``atsc_rs_decoder``
      counts packets, unrecoverable packets and bytes corrected, and the
      equalizer's soft symbols give MER. Those are the numbers that tell a
      weak signal from a mistuned one, and a hierarchical block hides them.
    """

    NFILTS = 16
    RRC_SYMS = 8
    EXCESS_BW = 0.1152

    def __init__(self, sample_rate, sps=SPS):
        gr.hier_block2.__init__(
            self, "atsc_demod",
            gr.io_signature(1, 1, gr.sizeof_gr_complex),
            gr.io_signature(1, 1, gr.sizeof_char))
        self.sample_rate = float(sample_rate)
        self.sps = float(sps)
        out_rate = SYMBOL_RATE * self.sps
        self.nominal_interp = out_rate / self.sample_rate

        # Everything before the demodulator: the tap the AFC measures, the
        # carrier correction, and a coarse level set.
        self.tap = sample_tap(AFC_WINDOW)
        self.rotator = blocks.rotator_cc(0.0)
        # The agc inside the chain creeps at 1e-5 per sample toward 4.0, so
        # a signal arriving at 0.0002 rms - which is what an off-air capture
        # looks like - needs half a minute of samples before it decodes
        # anything. Setting the level from a measurement puts it there at
        # once and leaves the agc only the fine work.
        self.level = blocks.multiply_const_cc(1.0)

        filter_rate = self.sample_rate * self.NFILTS
        ntaps = int((2 * self.RRC_SYMS + 1) * self.sps * self.NFILTS)
        rrc = firdes.root_raised_cosine(
            self.NFILTS * (SYMBOL_RATE / 2.0) / filter_rate,
            filter_rate, SYMBOL_RATE / 2.0, self.EXCESS_BW, ntaps)
        self.pfb = filter.pfb_arb_resampler_ccf(self.nominal_interp, rrc,
                                                self.NFILTS)

        self.fpll = dtv.atsc_fpll(out_rate)
        self.dcr = filter.dc_blocker_ff(4096)
        self.agc = analog.agc_ff(1e-5, 4.0)
        self.btl = dtv.atsc_sync(out_rate)
        self.fsc = dtv.atsc_fs_checker()
        self.equ = dtv.atsc_equalizer()
        self.vit = dtv.atsc_viterbi_decoder()
        self.dei = dtv.atsc_deinterleaver()
        self.rsd = dtv.atsc_rs_decoder()
        self.der = dtv.atsc_derandomizer()
        self.dep = dtv.atsc_depad()

        # The four blocks whose items are not a power of two: 207 bytes for
        # a Reed-Solomon packet, 188 for a transport one. Asking for the
        # page-aligned size is exactly what GNU Radio allocates anyway, and
        # it stops four WARN lines being printed on every single run.
        for blk in (self.vit, self.dei):
            align_output_buffer(blk, 0, 207)
        for blk in (self.rsd, self.der):
            align_output_buffer(blk, 0, 188)

        self.connect(self, self.tap)
        self.connect(self, self.rotator, self.level, self.pfb, self.fpll,
                     self.dcr, self.agc, self.btl, self.fsc)
        self.connect((self.fsc, 0), (self.equ, 0))
        self.connect((self.fsc, 1), (self.equ, 1))
        self.connect((self.equ, 0), (self.vit, 0))
        self.connect((self.equ, 1), (self.vit, 1))
        self.connect((self.vit, 0), (self.dei, 0))
        self.connect((self.vit, 1), (self.dei, 1))
        self.connect((self.dei, 0), (self.rsd, 0))
        self.connect((self.dei, 1), (self.rsd, 1))
        self.connect((self.rsd, 0), (self.der, 0))
        self.connect((self.rsd, 1), (self.der, 1))
        self.connect(self.der, self.dep, self)

        # The equalizer emits one 832-symbol data segment per item; flatten
        # it so MER can be taken over whatever the last few thousand were.
        self.symbols = sample_tap(MER_WINDOW, np.float32)
        self.flatten = blocks.vector_to_stream(gr.sizeof_float, 832)
        self.connect((self.equ, 0), self.flatten, self.symbols)

    # -- what the app reads ----------------------------------------------

    def input_samples(self):
        """The raw channel, before any correction - what the AFC measures."""
        return self.tap.latest()

    def mer(self):
        s = self.symbols.latest()
        return mer_db(s) if s.size else 0.0

    def rs_counters(self):
        return {'packets': self.rsd.num_packets(),
                'bad': self.rsd.num_bad_packets(),
                'corrected': self.rsd.num_errors_corrected()}

    def set_level(self, gain):
        self.level.set_k(float(gain))

    def apply_afc(self, afc):
        """Take both corrections from an ``Afc``, never just the one."""
        self.rotator.set_phase_inc(-2 * np.pi * afc.error_hz / self.sample_rate)
        self.pfb.set_rate(self.nominal_interp * afc.clock_ratio)


# --------------------------------------------------------------------------
# Configuration dialog
# --------------------------------------------------------------------------

class ConfigDialog(Qt.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ATSC Video Receiver Configuration")
        self.layout = Qt.QVBoxLayout(self)
        self.config_dir = CONFIG_DIR
        self.config_file = os.path.join(self.config_dir,
                                        "atscReceiver_config.json")

        settings = read_settings()
        self.usrp_ip = settings.get('usrp_ip', '')
        self.radio_type = settings.get('radio_type', 'hackrf')
        if self.radio_type == 'vsg':
            self.create_cannot_receive()
            apply_dark_theme(self)
            return

        self.button_box = Qt.QDialogButtonBox(
            Qt.QDialogButtonBox.Ok | Qt.QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        self.create_radio_label()
        self.create_channel_control()
        self.create_gain_control()

        self.layout.addWidget(self.button_box)
        self.load_config()
        self.update_ok_state()
        apply_dark_theme(self)

    def create_cannot_receive(self):
        """The whole dialog, when Settings name a radio that cannot receive."""
        self.setWindowTitle("ATSC Video Receiver")
        row = Qt.QHBoxLayout()
        icon = Qt.QLabel()
        icon.setPixmap(self.style().standardIcon(
            Qt.QStyle.SP_MessageBoxWarning).pixmap(48, 48))
        icon.setAlignment(QtCore.Qt.AlignTop)
        row.addWidget(icon)
        message = Qt.QLabel(
            "<b>The Signal Hound VSG60 cannot receive.</b><br><br>"
            "It only transmits, so the ATSC Receiver has no radio to listen "
            "with. Choose the HackRF One, an Ettus USRP or the Signal Hound "
            "BB60D in Settings (the gear icon), then open the ATSC Receiver "
            "again.")
        message.setWordWrap(True)
        message.setAlignment(QtCore.Qt.AlignTop)
        row.addWidget(message, 1)
        self.layout.addLayout(row)
        close = Qt.QDialogButtonBox(Qt.QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        self.layout.addWidget(close)

    def create_radio_label(self):
        self.layout.addWidget(Qt.QLabel(radio_label(self.radio_type,
                                                    self.usrp_ip)))

    def create_channel_control(self):
        """Channel, exact frequency and a slider, all one control.

        The same ``FrequencyChooser`` the transmitter uses, so the two ends
        of the link are tuned the same way and offer the same channel list.
        """
        self.cf_chooser = FrequencyChooser(
            minimum=50.0, maximum=2200.0,
            value=channel_center_mhz(DEFAULT_CHANNEL),
            channels=tv_channel_items())
        self.layout.addWidget(self.cf_chooser)

    def create_gain_control(self):
        row = Qt.QHBoxLayout()
        self.gain_slider = Qt.QSlider(QtCore.Qt.Horizontal)
        self.gain_slider.setRange(0, 100)
        # 60% on a BB60D is its attenuator fully open with no RF
        # amplification: measured, that is within 2 dB of the best
        # signal-to-noise the device can reach, and the last 20 dB of RF
        # buys only those 2 dB while being what overdrives the converter on
        # a strong local signal. A HackRF wants its own middle, since 8VSB
        # peaks 8.7 dB above its rms and compression closes the eye.
        default = 60 if self.radio_type == 'bb60' else 55
        self.gain_slider.setValue(default)
        self.gain_label = Qt.QLabel(f"RF Gain: {default}%")
        self.gain_slider.valueChanged.connect(
            lambda v: self.gain_label.setText(f"RF Gain: {v}%"))
        row.addWidget(self.gain_label)
        row.addWidget(self.gain_slider)
        self.layout.addLayout(row)

    def update_ok_state(self):
        ok = self.button_box.button(Qt.QDialogButtonBox.Ok)
        enabled = self.radio_type != 'usrp' or bool(self.usrp_ip)
        ok.setEnabled(enabled)
        if enabled:
            ok.setGraphicsEffect(None)
        else:
            dim = Qt.QGraphicsOpacityEffect()
            dim.setOpacity(0.30)
            ok.setGraphicsEffect(dim)

    def load_config(self):
        if not os.path.exists(self.config_file):
            os.makedirs(self.config_dir, exist_ok=True)
            return
        try:
            with open(self.config_file) as f:
                config = json.load(f)
        except Exception as exc:
            print(f"ATSC receiver: could not read saved config: {exc}",
                  file=sys.stderr)
            return
        # One unreadable value must not discard everything saved after it.
        restore = [
            ('center_mhz', lambda v: self.cf_chooser.setValue(float(v))),
            ('gain_percent', lambda v: self.gain_slider.setValue(int(v))),
        ]
        for key, apply in restore:
            if key in config:
                try:
                    apply(config[key])
                except Exception as exc:
                    print(f"ATSC receiver: ignoring saved {key!r}: {exc}",
                          file=sys.stderr)

    def save_config(self):
        config = {
            'radio_type': self.radio_type,
            'center_mhz': self.cf_chooser.value(),
            'gain_percent': self.gain_slider.value(),
        }
        update_app_config(self.config_file, config)

    def accept(self):
        self.save_config()
        super().accept()

    def get_values(self):
        return {
            'radio_type': self.radio_type,
            'ipXmitAddr': self.usrp_ip if self.radio_type == 'usrp' else '',
            'center_mhz': self.cf_chooser.value(),
            'gain_percent': self.gain_slider.value(),
        }


# --------------------------------------------------------------------------
# The receiver
# --------------------------------------------------------------------------

def rx_gain_plan(percent, radio_type):
    """Map the 0-100% slider onto a receiver's own gain controls.

    The HackRF's three stages again, as in ``rdsReceiver`` - preamp first
    because it sets the noise figure, then the LNA in its 8 dB steps, then
    the baseband VGA in 2 dB steps. 8VSB peaks 8.7 dB above its rms, so
    this errs lower than the RDS plan does: an ATSC signal driven into
    compression loses the eye long before its spectrum looks wrong.
    """
    percent = min(max(float(percent), 0.0), 100.0)
    if radio_type != 'hackrf':
        return {}
    amp = 14.0 if percent >= 25 else 0.0
    total = percent / 100.0 * 88.0
    lna = min(40.0, round(total * 0.45 / 8.0) * 8.0)
    vga = min(62.0, max(0.0, round((total - lna) / 2.0) * 2.0))
    return {'AMP': amp, 'LNA': lna, 'VGA': vga}


def find_player():
    """The command line for whichever media player is installed, or None."""
    if shutil.which('ffplay'):
        return ['ffplay', '-hide_banner', '-loglevel', 'error', '-autoexit',
                '-window_title', 'ATSC Video', '-i', 'pipe:0']
    if shutil.which('mpv'):
        return ['mpv', '--title=ATSC Video', '--profile=low-latency', '-']
    if shutil.which('vlc'):
        return ['vlc', '--intf', 'dummy', '-']
    return None


class atscReceiver(gr.top_block, Qt.QWidget):
    # What this window's own controls change that its dialog should
    # open on next time - see apps/utils.py: save_flowgraph_settings.
    SAVED_SETTINGS = {'gain_percent': 'gain_percent',
                      'center_mhz': 'center_mhz'}

    def __init__(self, config_values=None):
        gr.top_block.__init__(self, "ATSC Video Receiver", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("ATSC Video Receiver")
        apply_flowgraph_theme(self)
        # The lock line's colours are the theme's, since the window wears
        # it: the old green and red were picked for Qt's light grey and read
        # about 3:1 on its panels. Taken here, from the theme the window was
        # just painted in, not at import - the launcher may have changed
        # theme since this module was loaded.
        self.GOOD, self.WARN, self.BAD = (TOKENS['good'], TOKENS['warn'],
                                          TOKENS['bad'])
        try:
            self.setWindowIcon(Qt.QIcon.fromTheme('gnuradio-grc'))
        except BaseException as exc:
            print(f"Qt GUI: Could not set Icon: {exc}", file=sys.stderr)

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

        self.settings = Qt.QSettings("GNU Radio", "atscReceiver")
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
        self.center_mhz = float(values.get('center_mhz',
                                           channel_center_mhz(DEFAULT_CHANNEL)))
        self.gain_percent = float(values.get('gain_percent', 60))
        self.usrp_ip = values.get('ipXmitAddr', '')
        self.samp_rate = SAMPLE_RATES.get(self.radio_type, 12e6)

        self.afc = Afc(self.center_mhz * 1e6)
        self.afc_enabled = True
        self._last_snapshot = None
        self._last_rs = None
        self._last_time = None
        self._seen_overflows = 0

        self._build_controls()
        self._build_flowgraph()
        self._build_readout()
        self._build_spectrum()

        self.status_timer = Qt.QTimer(self)
        self.status_timer.timeout.connect(self.refresh)
        self.status_timer.start(500)

    # ------------------------------------------------------------------ UI
    #
    # The window is four bands: everything you can change, what the radio
    # and the stream are doing, the spectrum, and a line for whatever just
    # happened. Only the spectrum stretches - the first arrangement let the
    # control rows share the slack and a third of the window was empty.

    def _build_controls(self):
        row = Qt.QHBoxLayout()
        row.addWidget(Qt.QLabel("Channel:"))
        self.channel_combo = Qt.QComboBox()
        for n, centre in channels():
            self.channel_combo.addItem(f"{n} ({centre:g})", n)
        index = self.channel_combo.findData(channel_for_center(self.center_mhz))
        if index >= 0:
            self.channel_combo.setCurrentIndex(index)
        self.channel_combo.currentIndexChanged.connect(
            lambda _: self.set_center(channel_center_mhz(
                self.channel_combo.currentData())))
        row.addWidget(self.channel_combo)

        row.addWidget(Qt.QLabel("MHz:"))
        self.freq_spin = Qt.QDoubleSpinBox()
        self.freq_spin.setDecimals(FREQ_DECIMALS)
        self.freq_spin.setSingleStep(0.1)
        self.freq_spin.setRange(50.0, 2200.0)
        self.freq_spin.setValue(self.center_mhz)
        self.freq_spin.valueChanged.connect(self.set_center)
        row.addWidget(self.freq_spin)

        row.addSpacing(15)
        row.addWidget(Qt.QLabel("RF Gain:"))
        self.gain_slider = Qt.QSlider(QtCore.Qt.Horizontal)
        self.gain_slider.setRange(0, 100)
        self.gain_slider.setValue(int(self.gain_percent))
        self.gain_slider.setMinimumWidth(120)
        self.gain_slider.valueChanged.connect(self.set_gain)
        row.addWidget(self.gain_slider)
        self.gain_value = Qt.QLabel(f"{int(self.gain_percent)}%")
        row.addWidget(self.gain_value)

        row.addSpacing(15)
        self.afc_check = Qt.QCheckBox("AFC")
        self.afc_check.setChecked(True)
        self.afc_check.setToolTip(
            "Correct the transmitter's clock error - carrier and symbol "
            "rate together. A few parts per million is enough to stop the "
            "decoder cold while the spectrum still looks perfect.")
        self.afc_check.toggled.connect(self.set_afc_enabled)
        row.addWidget(self.afc_check)

        # The buttons go on the right of the same row, where there was a
        # band of empty window before, rather than on a row of their own.
        row.addStretch(1)
        self.watch_btn = Qt.QPushButton("Watch")
        self.watch_btn.setToolTip(
            "Hand the recovered transport stream to a media player.")
        self.watch_btn.clicked.connect(self.toggle_watch)
        row.addWidget(self.watch_btn)

        # Record has nowhere to go without a media folder, and Settings
        # cannot change while this window is open, so it is decided here.
        self.record_btn = Qt.QPushButton("Record")
        media_dir = read_settings().get('media_directory', '')
        if os.path.isdir(media_dir):
            self.record_btn.setToolTip(
                "Write the recovered transport stream into the media folder, "
                "where the ATSC Transmitter can pick it up again.")
        else:
            self.record_btn.setEnabled(False)
            self.record_btn.setToolTip(
                "Set a media folder in Settings to record - that is where "
                "the recording goes, and where the ATSC Transmitter looks "
                "for transport streams.")
        self.record_btn.clicked.connect(self.toggle_record)
        row.addWidget(self.record_btn)

        self.clear_btn = Qt.QPushButton("Clear Statistics")
        self.clear_btn.clicked.connect(self.clear_stats)
        row.addWidget(self.clear_btn)

        holder = Qt.QWidget()
        holder.setLayout(row)
        self.top_grid_layout.addWidget(holder, 0, 0, 1, 10)

    def _build_readout(self):
        big = Qt.QFont()
        big.setPointSize(15)
        big.setBold(True)
        mono = Qt.QFont("Monospace")
        mono.setStyleHint(Qt.QFont.TypeWriter)

        self.lbl = {}

        def field(grid, key, caption, row, col, font=None, span=1, wrap=False):
            grid.addWidget(Qt.QLabel(f"<b>{caption}</b>"), row, col * 2)
            value = Qt.QLabel("-")
            if font:
                value.setFont(font)
            value.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            # Wrapping is off by default: a short value that wraps stacks
            # onto two lines and shoves the whole box about as the numbers
            # change. Only the genuinely long fields get it.
            value.setWordWrap(wrap)
            grid.addWidget(value, row, col * 2 + 1, 1, span)
            self.lbl[key] = value

        signal = Qt.QGroupBox("Signal")
        sg = Qt.QGridLayout()
        signal.setLayout(sg)
        field(sg, 'lock', "Status", 0, 0, big, span=3, wrap=True)
        field(sg, 'mer', "Signal Quality", 1, 0)
        field(sg, 'level', "Input Level", 1, 1)
        field(sg, 'pilot', "Pilot", 2, 0, span=3, wrap=True)
        sg.setColumnStretch(1, 1)
        sg.setColumnStretch(3, 1)
        sg.setHorizontalSpacing(12)
        self.top_grid_layout.addWidget(signal, 1, 0, 1, 4)

        stream = Qt.QGroupBox("Transport Stream")
        tg = Qt.QGridLayout()
        stream.setLayout(tg)
        field(tg, 'packets', "Packet Rate", 0, 0)
        field(tg, 'bad', "Bad Packets", 0, 1)
        field(tg, 'corrected', "Correction", 1, 0)
        field(tg, 'flagged', "Errors / Discont.", 1, 1)
        field(tg, 'programs', "Programs", 2, 0, mono, span=3, wrap=True)
        tg.setColumnStretch(1, 1)
        tg.setColumnStretch(3, 1)
        tg.setHorizontalSpacing(12)
        # Six columns to the Signal box's four: it carries more text, and
        # splitting the width evenly wrapped every value onto two lines.
        self.top_grid_layout.addWidget(stream, 1, 4, 1, 6)

        # A line at the foot for whatever just happened - a player started,
        # a recording saved, packets dropped.
        self.action_note = Qt.QLabel("")
        self.action_note.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.top_grid_layout.addWidget(self.action_note, 9, 0, 1, 10)

        # Only the spectrum grows. Left to themselves the control rows
        # shared the slack and a third of the window was empty.
        for row in (0, 1, 9):
            self.top_grid_layout.setRowStretch(row, 0)
        self.top_grid_layout.setRowStretch(2, 1)

    def _build_spectrum(self):
        self.spectrum = qtgui.freq_sink_c(
            2048, window.WIN_BLACKMAN_hARRIS, self.center_mhz * 1e6,
            self.samp_rate,
            'Received Channel - the pilot sits 2.69 MHz below centre', 1, None)
        self.spectrum.set_update_time(0.10)
        self.spectrum.set_y_axis(*SPECTRUM_Y_AXIS)
        self.spectrum.set_y_label('Relative Gain', 'dB')
        self.spectrum.set_trigger_mode(qtgui.TRIG_MODE_FREE, 0.0, 0, "")
        # Fixed, like the other RF plots, and for the same reason after all:
        # autoscale fits the axis from the lowest bin up, and a BB60D's
        # decimation filter skirts fall to -210 dB just outside the 8 MHz
        # it passes. Tried it - the axis became 120 dB tall with the
        # haystack and its pilot squashed into the top fifth, which is
        # exactly what the numerical-zero bins do to a transmitter's plot.
        self.spectrum.enable_autoscale(False)
        self.spectrum.enable_grid(True)
        self.spectrum.set_fft_average(0.2)
        self.spectrum.enable_axis_labels(True)
        self.spectrum.enable_control_panel(False)
        self.spectrum.disable_legend()
        widget = sip.wrapinstance(self.spectrum.qwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(widget, 2, 0, 7, 10)
        self.connect(self.radio_source, self.spectrum)

    # ----------------------------------------------------------- flowgraph
    def _build_flowgraph(self):
        centre = self.center_mhz * 1e6
        if self.radio_type == 'usrp':
            self.radio_source = uhd.usrp_source(
                ",".join((f"addr={self.usrp_ip}", '')),
                uhd.stream_args(cpu_format="fc32", args='',
                                channels=list(range(0, 1))),
            )
            self.radio_source.set_samp_rate(self.samp_rate)
            self.radio_source.set_center_freq(centre, 0)
            self.radio_source.set_antenna("RX2", 0)
        elif self.radio_type == 'bb60':
            from apps.bb60_source import bb60_source
            self.radio_source = bb60_source(center_freq=centre,
                                            sample_rate=self.samp_rate,
                                            gain_percent=self.gain_percent)
        else:
            self.radio_source = soapy.source('driver=hackrf', 'fc32', 1, '', '',
                                             [''], [''])
            self.radio_source.set_sample_rate(0, self.samp_rate)
            self.radio_source.set_frequency(0, centre)
            self.radio_source.set_gain_mode(0, False)

        self.demod = AtscDemod(self.samp_rate, SPS)
        self.ts = ts_sink()
        self.connect(self.radio_source, self.demod, self.ts)

    # ------------------------------------------------------------ controls
    def apply_gain(self):
        """Set receive gain. Must run after start() on a HackRF - see below."""
        if self.radio_type == 'usrp':
            try:
                rng = self.radio_source.get_gain_range()
                low, high = float(rng.start()), float(rng.stop())
            except Exception:
                low, high = 0.0, 76.0
            self.radio_source.set_gain(
                low + (high - low) * self.gain_percent / 100.0, 0)
            return
        if self.radio_type == 'bb60':
            self.radio_source.set_gain_percent(self.gain_percent)
            return
        # SoapyHackRF silently ignores the AMP stage when it is set before
        # the stream is running - worth 14 dB, which is the difference
        # between decoding and not.
        for name, value in rx_gain_plan(self.gain_percent, 'hackrf').items():
            self.radio_source.set_gain(0, name, value)

    def set_gain(self, percent):
        self.gain_percent = float(percent)
        self.gain_value.setText(f"{int(percent)}%")
        self.apply_gain()

    def set_center(self, mhz):
        mhz = float(mhz)
        if abs(mhz - self.center_mhz) < 1e-6:
            return
        self.center_mhz = mhz
        centre = mhz * 1e6
        if self.radio_type == 'usrp':
            self.radio_source.set_center_freq(centre, 0)
        elif self.radio_type == 'bb60':
            self.radio_source.set_center_freq(centre)
        else:
            self.radio_source.set_frequency(0, centre)
        self.spectrum.set_frequency_range(centre, self.samp_rate)

        # Keep the two tuning widgets and the AFC in step with each other.
        for widget, value in ((self.freq_spin, mhz),
                              (self.channel_combo, channel_for_center(mhz))):
            widget.blockSignals(True)
            if widget is self.freq_spin:
                widget.setValue(value)
            elif value is not None:
                index = widget.findData(value)
                if index >= 0:
                    widget.setCurrentIndex(index)
            widget.blockSignals(False)

        self.afc.retune(centre)
        self.demod.apply_afc(self.afc)
        self.clear_stats()

    def set_afc_enabled(self, on):
        self.afc_enabled = bool(on)
        if not on:
            self.afc.retune(self.center_mhz * 1e6)
            self.demod.apply_afc(self.afc)

    def clear_stats(self):
        self.ts.reset()
        self._last_snapshot = None
        self._last_rs = None
        self._last_time = None

    def toggle_watch(self):
        if self.ts.player_alive():
            self.ts.stop_player()
            self.watch_btn.setText("Watch")
            self.action_note.setText("")
            return
        argv = find_player()
        if argv is None:
            Qt.QMessageBox.warning(
                self, "No Media Player",
                "Watching needs ffplay, mpv or vlc on PATH, and none of "
                "them is installed.\n\nUse Record instead and play the "
                "file afterwards.")
            return
        try:
            self.ts.start_player(argv)
        except Exception as exc:
            Qt.QMessageBox.warning(self, "Could Not Start Player", str(exc))
            return
        self.watch_btn.setText("Stop Watching")
        self.action_note.setText(
            f"{os.path.basename(argv[0])} started - it needs a couple of "
            "seconds of clean stream before a picture appears.")

    def toggle_record(self):
        if self.ts.recording():
            path = self.ts.stop_record()
            self.record_btn.setText("Record")
            self.action_note.setText(f"Saved {path}")
            return
        directory = read_settings().get('media_directory', '')
        if not os.path.isdir(directory):
            Qt.QMessageBox.warning(
                self, "No Media Directory",
                "Set a media directory in Settings first - that is where "
                "the recording goes, and where the ATSC Transmitter looks "
                "for transport streams.")
            return
        name = f"atsc-{self.center_mhz:g}MHz-" \
               f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.ts"
        try:
            path = self.ts.start_record(os.path.join(directory, name))
        except Exception as exc:
            Qt.QMessageBox.warning(self, "Could Not Record", str(exc))
            return
        self.record_btn.setText("Stop Recording")
        self.action_note.setText(f"Recording to {path}")

    # ------------------------------------------------------------- readout
    def refresh(self):
        samples = self.demod.input_samples()
        now = time.time()

        if samples.size:
            rms = float(np.sqrt(np.mean(np.abs(samples) ** 2)))
            # Put the demodulator's input near unity so its own agc, which
            # creeps at 1e-5 per sample, has almost nothing left to do.
            if rms > 0:
                self.demod.set_level(min(1.0 / rms, 1e6))
            dbfs = 20 * np.log10(rms) if rms > 0 else -999
            self.lbl['level'].setText(f"{dbfs:.1f} dBFS")
            if self.afc_enabled and self.afc.update(samples, self.samp_rate):
                self.demod.apply_afc(self.afc)

        if self.afc.locked:
            self.lbl['pilot'].setText(
                f"{self.afc.error_hz:+.0f} Hz off ({self.afc.ppm:+.1f} ppm), "
                f"{self.afc.prominence_db:.0f} dB above the haystack"
                + ("" if self.afc_enabled else "  - AFC off, not corrected"))
        else:
            self.lbl['pilot'].setText("not found - no 8VSB signal on this channel")

        mer = self.demod.mer()
        self.lbl['mer'].setText(
            f"{mer:.1f} dB MER  ({mer_quality(mer)})" if mer else "-")

        snap = self.ts.snapshot()
        rs = self.demod.rs_counters()
        if self._last_snapshot is None:
            self._last_snapshot, self._last_rs, self._last_time = snap, rs, now
            return
        elapsed = max(now - self._last_time, 1e-3)
        window = since(self._last_snapshot, snap)
        packets = rs['packets'] - self._last_rs['packets']
        bad = rs['bad'] - self._last_rs['bad']
        corrected = rs['corrected'] - self._last_rs['corrected']
        self._last_snapshot, self._last_rs, self._last_time = snap, rs, now

        rate = packets / elapsed
        self.lbl['packets'].setText(
            f"{rate:,.0f}/s  ({rate * TS_PACKET * 8 / 1e6:.2f} Mbps)"
            if rate else "-")
        bad_pct = 100.0 * bad / packets if packets else 0.0
        self.lbl['bad'].setText(
            f"{bad} of {packets}  ({bad_pct:.2f}%)" if packets else "-")
        # Out of 10: that is what Reed-Solomon can fix in a 188-byte packet,
        # so the ratio says how much margin is left rather than just a rate.
        self.lbl['corrected'].setText(
            f"{corrected / packets:.2f} of 10 bytes" if packets else "-")
        # The caption already reads "Flagged / Discontinuity", so the value
        # does not need to say it again - and spelled out it wrapped onto
        # two lines and shoved the box about.
        self.lbl['flagged'].setText(
            f"{window['errors']} / {window['discontinuities']}")

        programs = self.ts.programs()
        self.lbl['programs'].setText("\n".join(programs) if programs
                                     else "no PAT yet")

        text, colour = self._status(packets, bad_pct)
        self.lbl['lock'].setText(text)
        self.lbl['lock'].setStyleSheet(f"color: {colour};")

        if self.ts.player_alive():
            dropped = self.ts.dropped
            if dropped:
                self.action_note.setText(
                    f"player running - {dropped // TS_PACKET} packets dropped "
                    "because it could not keep up")
        elif self.watch_btn.text() != "Watch":
            # The player exited or was closed by the user.
            self.ts.stop_player()
            self.watch_btn.setText("Watch")
            self.action_note.setText("")

    def _status(self, packets, bad_pct):
        """What is actually happening, and a colour for how bad it is.

        The failure modes look alike from the spectrum, so each gets named:
        the converter overdriven, no signal at all, a clock error the AFC
        is not correcting, a signal too weak to decode, and a picture.
        """
        # An overdriven converter is invisible in the samples - they arrive
        # filtered and decimated - so it can only come from the driver, and
        # it looks exactly like a weak signal if it is not called out.
        if self._adc_overflows():
            return ("Input overloaded - turn the RF gain down", self.BAD)
        if not packets:
            if not self.afc.locked:
                return ("No signal", self.BAD)
            if not self.afc_enabled and abs(self.afc.error_hz) > 500:
                return ("Pilot found, not decoding - try switching AFC on",
                        self.WARN)
            # The pilot is a narrow carrier and shows 40 dB out of the noise
            # on a signal far too weak to decode, so this is the normal
            # reading for a distant station, not a fault.
            return ("Pilot found, not decoding - too weak? Try a better "
                    "antenna", self.WARN)
        # Deliberately no "N dB from the cliff": MER is decision-directed
        # and stops telling the truth around 16 dB, which is below where
        # the cliff is. The loss rate is the honest measure of margin.
        if bad_pct > 20:
            return (f"Breaking up - {bad_pct:.0f}% of packets lost", self.BAD)
        if bad_pct > 0.5:
            return (f"Marginal - {bad_pct:.1f}% of packets lost", self.WARN)
        return ("Locked - clean stream", self.GOOD)

    def _adc_overflows(self):
        """New converter overflows since the last look, if the radio counts
        them. Only the BB60D does; the others return nothing."""
        counter = getattr(self.radio_source, 'adc_overflows', None)
        if counter is None:
            return 0
        total = counter()
        fresh = total - self._seen_overflows
        self._seen_overflows = total
        return fresh

    def closeEvent(self, event):
        self.settings = Qt.QSettings("GNU Radio", "atscReceiver")
        self.settings.setValue("geometry", self.saveGeometry())
        self.status_timer.stop()
        self.ts.stop_player()
        self.ts.stop_record()
        self.stop()
        self.wait()
        event.accept()


def main(top_block_cls=atscReceiver, options=None, app=None, config_values=None):
    if app is None:
        app = Qt.QApplication(sys.argv)

    tb = top_block_cls(config_values)
    tb.start()
    # Gains go on after the stream exists: the HackRF's preamp is ignored
    # otherwise.
    tb.apply_gain()
    tb.show()

    def sig_handler(sig=None, frame=None):
        tb.stop()
        tb.wait()
        Qt.QApplication.quit()

    import signal as _signal
    _signal.signal(_signal.SIGINT, sig_handler)
    _signal.signal(_signal.SIGTERM, sig_handler)

    timer = Qt.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    if app.instance():
        return tb
    return app.exec_()


if __name__ == '__main__':
    main()
