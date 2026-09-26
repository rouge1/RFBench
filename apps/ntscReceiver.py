#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# SPDX-License-Identifier: GPL-3.0
#
# NTSC Receiver - tunes a 6 MHz television channel, detects the vestigial
# sideband the way a television does, and decodes the composite video back
# into pictures. The receiving half of ``ntscAnalogVideoRecorded``; the two
# share a tile in the launcher.

import json
import os
import shutil
import subprocess
import sys
import threading
import time

import numpy as np  # type: ignore
try:                 # PyQt5 ships sip inside the package; some builds also
    import sip       # expose it at the top level.
except ImportError:  # pragma: no cover - depends on the PyQt5 build
    from PyQt5 import sip  # type: ignore
from fractions import Fraction
from gnuradio import analog, audio, blocks, filter, gr, qtgui, soapy, uhd  # type: ignore
from gnuradio.fft import window  # type: ignore
from PyQt5 import Qt, QtCore  # type: ignore

from apps.atsc_rx_core import channel_center_mhz, channel_for_center, tv_channel_items
from apps.ntsc_decode import NtscDecoder, SYNC_DISCRIMINANT
from apps.ntsc_encode import FH, FRAME, IRE_SYNC, NTSC as NTSC_STANDARD
# The two ends of the link must agree about where the carriers sit, so the
# receiver takes those numbers from the transmitter rather than repeating
# them. Importing the app module is cheap - it only defines things.
from apps.ntscAnalogVideoRecorded import (AURAL_CARRIER, AURAL_DEVIATION,
                                          CARRIER_AT_SYNC, CARRIER_AT_WHITE,
                                          LO_OFFSET, VISUAL_CARRIER)
from apps.theme import TOKENS
from apps.utils import (CONFIG_DIR, apply_dark_theme, apply_flowgraph_theme,
                        radio_label,
                        read_settings, update_app_config, SPECTRUM_Y_AXIS,
                        FrequencyChooser, FREQ_DECIMALS)

# 20 MS/s, not 10. A 6 MHz channel will not fit either side of DC at 10, so
# the radio's own LO leakage would land inside the picture; at 20 the whole
# channel sits 6 MHz off DC with the sound carrier still inside the band.
# The composite is decimated back to 10 MS/s before the decoder, which is
# where the expensive work is.
SAMPLE_RATES = {'hackrf': 20e6, 'usrp': 20e6, 'bb60': 20e6}
COMPOSITE_DECIM = 2
#: The spectrum display runs at the transmitter's own 10 MS/s, so that the
#: two windows show the same span of the same channel at the same zoom.
SPECTRUM_DECIM = 2
DEFAULT_CHANNEL = 24            # 533 MHz, the channel this bench uses

ACTIVE_WIDTH = 640
ACTIVE_LINES = 240              # per field; a frame is twice this

#: How far the vestigial sideband reaches either side of the visual carrier,
#: and so how wide a receiver's Nyquist slope must be.
VESTIGIAL_SLOPE = 0.75e6
#: Where the picture band ends, measured from the visual carrier: the
#: vestige below, the full video bandwidth above.
VIDEO_BAND_LOW = -1.25e6
VIDEO_BAND_HIGH = 4.2e6
BAND_EDGE = 0.3e6
NYQUIST_TAPS = 257

#: Sound. System M puts it on its own FM carrier 4.5 MHz above the visual
#: one, so the picture chain's channel filter deliberately throws it away
#: (see nyquist_slope_taps) and this takes a second copy of the baseband.
AUDIO_RATE = 48000
#: What the sound carrier is brought down to before demodulating: 20 MS/s
#: decimated by 100, which is 25 audio samples for every 6 at 48 kHz.
SOUND_IF_RATE = 200e3
#: Carson's rule on 25 kHz of deviation and 15 kHz of audio: 2*(25+15).
SOUND_BANDWIDTH = 80e3
#: US de-emphasis. The rest of the world uses 50 us.
SOUND_DEEMPHASIS = 75e-6


def nyquist_slope_taps(sample_rate, ntaps=NYQUIST_TAPS):
    """The filter a television's IF applies, doing two jobs at once.

    **The slope.** Below 0.75 MHz from the visual carrier both sidebands
    survive and add; above it only one does. Rectify without compensating
    and luma comes back twice as strong as chroma - whites too bright,
    colours washed toward grey. It reads like a broken transmitter and is
    not one; measured, putting the slope in took the colour error from 0.43
    to 0.13. The response is half at the carrier, rising to one 0.75 MHz
    above and falling to nothing 0.75 MHz below, so it is asymmetric and
    the taps are complex - ``firdes`` has no shape like it.

    **And the channel.** This is also what keeps the *sound* out of the
    picture detector, and leaving that out cost a whole off-air test. The
    aural carrier sits 4.5 MHz above the visual one at nearly a third of
    its amplitude, and a detector fed both produces no recognisable sync at
    all. It never showed up in software, because the modulator under test
    carries only vision - the sound is added after it, on the way to the
    radio. A real set uses a SAW filter with a sound trap for exactly this.
    """
    freqs = np.fft.fftfreq(ntaps, 1.0 / sample_rate)
    response = np.clip(0.5 + freqs / (2 * VESTIGIAL_SLOPE), 0.0, 1.0)
    # Raised-cosine edges rather than a brick wall, which would ring.
    low = np.clip((freqs - VIDEO_BAND_LOW) / BAND_EDGE + 0.5, 0.0, 1.0)
    high = np.clip((VIDEO_BAND_HIGH - freqs) / BAND_EDGE + 0.5, 0.0, 1.0)
    response *= 0.5 * (1 - np.cos(np.pi * low))
    response *= 0.5 * (1 - np.cos(np.pi * high))
    taps = np.fft.ifft(response)
    taps = np.roll(taps, ntaps // 2) * np.hamming(ntaps)
    return taps.astype(np.complex64)


class NtscDemod(gr.hier_block2):
    """Complex baseband on a television channel in, composite video out.

    Shift the visual carrier to DC, apply the Nyquist slope, rectify, and
    turn the carrier amplitude back into a composite signal. Negative
    modulation means the envelope is upside down - sync is peak carrier -
    so the last step inverts it; the decoder finds its own levels from
    there, which is why no AGC is needed for the picture.
    """

    def __init__(self, sample_rate, center_offset=LO_OFFSET,
                 decimation=COMPOSITE_DECIM):
        gr.hier_block2.__init__(
            self, "ntsc_demod",
            gr.io_signature(1, 1, gr.sizeof_gr_complex),
            gr.io_signature(1, 1, gr.sizeof_float))
        self.sample_rate = float(sample_rate)
        self.composite_rate = self.sample_rate / decimation

        # The radio is tuned ``center_offset`` above the channel centre, so
        # the visual carrier sits this far from DC.
        carrier_at = VISUAL_CARRIER - center_offset
        self.rotator = blocks.rotator_cc(
            -2 * np.pi * carrier_at / self.sample_rate)
        self.slope = filter.fft_filter_ccc(
            1, nyquist_slope_taps(self.sample_rate).tolist(), 1)
        # **Synchronous detection, not a rectifier.** Envelope detection is
        # what a 1950s set did and it works, but rectifying a vestigial
        # sideband signal leaves quadrature distortion: measured, the colour
        # error is 0.139 that way against 0.120 locking to the carrier
        # first. The PLL earns its place twice over, because it also tracks
        # whatever the two radios' local oscillators disagree by - the same
        # job the AFC does in the ATSC receiver, for free.
        self.pll = analog.pll_carriertracking_cc(0.002, 0.05, -0.05)
        self.detector = blocks.complex_to_real(1)
        # Negative modulation: peak carrier is sync, so flip it. The scale
        # is arbitrary - NtscDecoder reads sync and blanking off whatever it
        # is given - which is what saves this from needing an AGC.
        self.invert = blocks.multiply_const_ff(-1.0)
        self.decimate = filter.fir_filter_fff(
            decimation,
            filter.firdes.low_pass(1.0, self.sample_rate, 4.5e6, 1.0e6))

        self.connect(self, self.rotator, self.slope, self.pll, self.detector,
                     self.invert, self.decimate, self)

    def set_center_offset(self, center_offset):
        self.rotator.set_phase_inc(
            -2 * np.pi * (VISUAL_CARRIER - center_offset) / self.sample_rate)


class NtscSound(gr.hier_block2):
    """The aural carrier: complex baseband in, audio out at 48 kHz.

    System M carries the sound on its own FM carrier 4.5 MHz above the
    visual one, deviating 25 kHz - a narrowband FM station riding beside
    the picture. So this is an FM receiver, and it is deliberately a
    *separate* path: the picture's Nyquist filter exists partly to throw
    the sound away, because a video detector fed both produces no
    recognisable sync at all.

    Everything before the demodulator is one `freq_xlating_fir_filter`,
    which mixes the sound carrier to zero, filters to Carson's 80 kHz and
    decimates by 100 in a single pass - a dot product per *output* sample,
    so 20 MS/s in costs what 200 kS/s costs.

    **Demodulate well above the audio rate and filter down afterwards.**
    Taking the instantaneous frequency straight to 48 kHz folds everything
    up to 60 kHz back into the audio band; measured while proving the
    transmitter, that read 0.087 correlation against the source on a link
    that was in fact carrying it at 0.999.
    """

    def __init__(self, sample_rate, center_offset=LO_OFFSET,
                 audio_rate=AUDIO_RATE, volume=0.5):
        gr.hier_block2.__init__(
            self, "ntsc_sound",
            gr.io_signature(1, 1, gr.sizeof_gr_complex),
            gr.io_signature(1, 1, gr.sizeof_float))
        self.sample_rate = float(sample_rate)
        decimation = max(1, int(round(self.sample_rate / SOUND_IF_RATE)))
        self.if_rate = self.sample_rate / decimation

        # The radio is tuned center_offset above the channel centre, so the
        # sound carrier sits this far from DC - as with the picture, but
        # 4.5 MHz further up.
        carrier_at = AURAL_CARRIER - center_offset
        self.channel = filter.freq_xlating_fir_filter_ccf(
            decimation,
            filter.firdes.low_pass(1.0, self.sample_rate, SOUND_BANDWIDTH,
                                   SOUND_BANDWIDTH),
            carrier_at, self.sample_rate)
        # Scaled so that full deviation is +-1.0, which makes the meter read
        # in kilohertz without another constant.
        self.demod = analog.quadrature_demod_cf(
            self.if_rate / (2 * np.pi * AURAL_DEVIATION))
        self.audio_lpf = filter.fir_filter_fff(
            1, filter.firdes.low_pass(1.0, self.if_rate, 15e3, 4e3))
        ratio = Fraction(int(audio_rate),
                         int(round(self.if_rate))).limit_denominator(1000)
        self.resamp = filter.rational_resampler_fff(
            interpolation=ratio.numerator, decimation=ratio.denominator,
            taps=[], fractional_bw=0)
        self.deemph = analog.fm_deemph(float(audio_rate), SOUND_DEEMPHASIS)
        self.gain = blocks.multiply_const_ff(float(volume))
        self.connect(self, self.channel, self.demod, self.audio_lpf,
                     self.resamp, self.deemph, self.gain, self)

        # Two meters. The carrier level says the sound is being transmitted
        # at all; the deviation says something is modulating it. A station
        # sending silence shows a strong carrier and no deviation, which is
        # a different fault from no carrier.
        self.carrier_rms = blocks.rms_cf(0.01)
        self.carrier_probe = blocks.probe_signal_f()
        self.connect(self.channel, self.carrier_rms, self.carrier_probe)
        self.deviation_rms = blocks.rms_ff(0.005)
        self.deviation_probe = blocks.probe_signal_f()
        self.connect(self.demod, self.deviation_rms, self.deviation_probe)

    def set_center_offset(self, center_offset):
        self.channel.set_center_freq(AURAL_CARRIER - center_offset)

    def set_volume(self, value):
        self.gain.set_k(float(value))

    def carrier_level(self):
        """Sound carrier, in dBFS, or None when there is nothing there."""
        rms = self.carrier_probe.level()
        return 20 * np.log10(rms) if rms > 0 else None

    def deviation_hz(self):
        """How hard the sound is modulating the carrier, in hertz rms."""
        return self.deviation_probe.level() * AURAL_DEVIATION


class ntsc_frame_sink(gr.sync_block):
    """Composite video in, decoded pictures out - on its own thread.

    Decoding a frame takes about 26 ms and a work() call covers well under
    a millisecond, so decoding inside work() would stall the radio. Samples
    are gathered into a buffer and handed to a worker; if the worker is
    still busy the buffer is dropped, which is the right thing for a live
    receiver - a dropped frame costs nothing, falling behind costs
    everything.
    """

    #: Enough signal that a whole frame is in there wherever it starts.
    #:
    #: A buffer lands at an arbitrary point, so the first vertical block in
    #: it can be anywhere up to half a frame in, and the picture then needs
    #: a full frame after that. 1.3 was not enough - about one buffer in
    #: three ran out part way down and the bottom quarter of the picture
    #: came back black.
    BUFFER_FRAMES = 1.7

    def __init__(self, sample_rate, width=None, active_lines=None,
                 standard=None):
        gr.sync_block.__init__(self, name='ntsc_frame_sink',
                               in_sig=[np.float32], out_sig=None)
        self.sample_rate = float(sample_rate)
        # The standard is a parameter because the FM video receiver decodes
        # PAL as well, off the same block - see `CompositeFrameSink` below.
        # Left out it is NTSC, whose width and lines per field are exactly
        # the constants this file used before, so nothing here changes.
        self.standard = standard or NTSC_STANDARD
        self.decoder = NtscDecoder(self.sample_rate, width, active_lines,
                                   standard=self.standard)
        self._need = int(self.BUFFER_FRAMES * self.standard.frame
                         * self.sample_rate)
        self._buf = np.empty(self._need, dtype=np.float32)
        self._fill = 0
        self._pending = None
        self._wake = threading.Event()
        self._running = threading.Event()
        self._lock = threading.Lock()
        self._thread = None

        self.frame = None            # most recent picture, RGB 0..1
        self.frames_decoded = 0
        self.frames_dropped = 0
        self.failures = 0
        self.status = {}
        self._player = None
        self._player_lock = threading.Lock()
        self._to_send = None
        self._send_wake = threading.Event()
        self._sending = False
        self._writer = None
        self.frames_unsent = 0

    # -- lifecycle -------------------------------------------------------

    def start(self):
        self._running.set()
        self._thread = threading.Thread(target=self._decode_loop, daemon=True,
                                        name='ntsc-decode')
        self._thread.start()
        return True

    def stop(self):
        self._running.clear()
        self._wake.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)
        self.stop_player()
        return True

    # -- streaming -------------------------------------------------------

    def work(self, input_items, output_items):
        data = input_items[0]
        taken = 0
        while taken < len(data):
            room = self._need - self._fill
            chunk = min(room, len(data) - taken)
            self._buf[self._fill:self._fill + chunk] = data[taken:taken + chunk]
            self._fill += chunk
            taken += chunk
            if self._fill >= self._need:
                with self._lock:
                    if self._pending is None:
                        self._pending = self._buf.copy()
                        self._wake.set()
                    else:
                        self.frames_dropped += 1
                self._fill = 0
        return len(data)

    def _decode_loop(self):
        while self._running.is_set():
            self._wake.wait(0.2)
            self._wake.clear()
            with self._lock:
                buf, self._pending = self._pending, None
            if buf is None:
                continue
            try:
                self._decode(buf.astype(np.float64))
            except Exception:
                # A capture with no sync in it raises; that is a weak signal,
                # not a bug, and the readout says so.
                self.failures += 1

    def _decode(self, x):
        decoder = self.decoder
        sync, blank = decoder.levels(x)
        starts, widths = decoder.find_pulses(x, sync, blank)
        status = {'sync': sync, 'blank': blank, 'pulses': starts.size}

        # The spacing of the full-width sync pulses is the line rate, and
        # how far it is from 15734.266 Hz is the transmitter's clock error -
        # the same thing the ATSC receiver reads off the pilot.
        horizontal = starts[widths > SYNC_DISCRIMINANT]
        status['line_rate'] = 0.0
        if horizontal.size > 20:
            gaps = np.diff(horizontal)
            # Average, not median. A line is 635.556 samples at 10 MS/s and
            # a pulse index is a whole number, so the gaps are a mixture of
            # 635 and 636 and the *median* is whichever is commoner - which
            # reads 15723.3 Hz or 15748.0 Hz and never the 15734.266 that is
            # actually there. Averaging recovers the fraction.
            # Within 10% of a line, so the half-line spacing of the vertical
            # block's broad pulses cannot drag the average down - which read
            # 15866 Hz when the window was wider.
            nominal = self.sample_rate / self.standard.line_rate
            one_line = gaps[(gaps > 0.9 * nominal) & (gaps < 1.1 * nominal)]
            if one_line.size > 10:
                period = float(one_line.mean()) / self.sample_rate
                status['line_rate'] = 1.0 / period if period > 0 else 0.0

        self.measure(x, starts, widths, sync, blank, status)
        frame = decoder.decode_frame(x, color=True)
        self.frame = frame
        self.frames_decoded += 1
        status['locked'] = True
        self.status = status
        self._to_player(frame)

    def measure(self, x, starts, widths, sync, blank, status):
        """Anything else worth reading off this buffer, into ``status``.

        Nothing here: a television receiver wants a picture. The FM video
        receiver overrides it, because an FM link's deviation and its
        pre-emphasis are numbers a transmitter's datasheet may not give and
        this is where they can be measured - the pulses have already been
        found by the time it is called, which is most of the work.
        """

    # -- handing the picture to a player ---------------------------------

    def start_player(self, argv):
        self.stop_player()
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        self._player = proc
        self._to_send = None
        self._sending = True
        self._send_wake.clear()
        self._writer = threading.Thread(target=self._write_loop, daemon=True,
                                        name='ntsc-player')
        self._writer.start()
        return proc

    def stop_player(self):
        # The writer is asked to stop *before* the pipe is taken away from
        # it, so that a picture still waiting goes out rather than being
        # thrown away on the last frame of every session.
        self._sending = False
        self._send_wake.set()
        writer, self._writer = self._writer, None
        if writer is not None:
            # A writer parked on a player that stopped reading is freed by
            # closing the pipe below, so do not wait long for it here.
            writer.join(timeout=1.0)
        proc, self._player = self._player, None
        self._to_send = None
        if proc is None:
            return
        try:
            proc.stdin.close()
        except Exception:
            pass
        if proc.poll() is None:
            proc.terminate()

    def player_alive(self):
        return self._player is not None and self._player.poll() is None

    def _to_player(self, frame):
        """Hand the newest picture to the writer thread, and drop the rest.

        NTSC has no container to pass on the way ATSC has a transport
        stream, so what goes to the player is the pictures themselves - and
        a picture is large: 640x480 in RGB is 921,600 bytes, PAL's is
        1,327,104.

        **That size is why this needs a thread of its own.** It used to
        write straight down a non-blocking pipe from here, once per decoded
        frame. A non-blocking write to a pipe delivers at most what fits in
        the pipe buffer - 65,536 bytes - so each frame handed over lost
        fourteen fifteenths of itself, and the remainder queued in memory
        against a cap that only applied in the branch that never ran.
        Measured against a real transmitter: of 214 pictures decoded in
        12 seconds, **7.1% of the bytes reached the player** - 1.25 pictures
        a second, which is what "the video is not updating" looks like -
        while the unsent backlog grew to **183 MB**.

        So the write is a blocking one on its own thread, and what is held
        for it is a single frame rather than a queue: a player that has
        fallen behind gets the newest picture, never a backlog of stale
        ones, which is what the old comment here claimed and did not do.
        """
        if self._player is None:
            return
        data = (np.clip(frame, 0, 1) * 255).astype(np.uint8).tobytes()
        with self._player_lock:
            if self._to_send is not None:
                self.frames_unsent += 1
            self._to_send = data
        self._send_wake.set()

    def _write_loop(self):
        while True:
            self._send_wake.wait(0.2)
            self._send_wake.clear()
            with self._player_lock:
                data, self._to_send = self._to_send, None
            proc = self._player
            if data is not None and proc is not None:
                try:
                    proc.stdin.write(data)
                    proc.stdin.flush()
                except (OSError, ValueError, AttributeError, BrokenPipeError):
                    return
            # Checked after the write, not before it, so that the picture
            # waiting when Stop was pressed is the one on screen.
            if not self._sending:
                return


def find_player(width=ACTIVE_WIDTH, height=2 * ACTIVE_LINES,
                frame_rate=None, title='NTSC Video'):
    """A player that will take raw RGB frames on stdin, or None."""
    size = f"{width}x{height}"
    rate = frame_rate or 1.0 / FRAME
    if shutil.which('ffplay'):
        return ['ffplay', '-hide_banner', '-loglevel', 'error',
                '-f', 'rawvideo', '-pixel_format', 'rgb24',
                '-video_size', size, '-framerate', f"{rate:.4f}",
                '-window_title', title, '-i', 'pipe:0']
    if shutil.which('mpv'):
        return ['mpv', f'--title={title}', '--demuxer=rawvideo',
                f'--demuxer-rawvideo-w={width}',
                f'--demuxer-rawvideo-h={height}',
                '--demuxer-rawvideo-mp-format=rgb24', '-']
    return None


#: The same block by a name that does not pretend it only does NTSC, as
#: ``CompositeDecoder`` is for ``NtscDecoder``. The FM video receiver uses
#: it for PAL as well.
CompositeFrameSink = ntsc_frame_sink


# --------------------------------------------------------------------------
# Configuration dialog
# --------------------------------------------------------------------------

class ConfigDialog(Qt.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("NTSC Video Receiver Configuration")
        self.layout = Qt.QVBoxLayout(self)
        self.config_dir = CONFIG_DIR
        self.config_file = os.path.join(self.config_dir,
                                        "ntscReceiver_config.json")

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
        self.create_sound_control()

        self.layout.addWidget(self.button_box)
        self.load_config()
        self.update_ok_state()
        apply_dark_theme(self)

    def create_cannot_receive(self):
        self.setWindowTitle("NTSC Video Receiver")
        row = Qt.QHBoxLayout()
        icon = Qt.QLabel()
        icon.setPixmap(self.style().standardIcon(
            Qt.QStyle.SP_MessageBoxWarning).pixmap(48, 48))
        icon.setAlignment(QtCore.Qt.AlignTop)
        row.addWidget(icon)
        message = Qt.QLabel(
            "<b>The Signal Hound VSG60 cannot receive.</b><br><br>"
            "It only transmits, so the NTSC Receiver has no radio to listen "
            "with. Choose the HackRF One, an Ettus USRP or the Signal Hound "
            "BB60D in Settings (the gear icon), then open it again.")
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
        self.cf_chooser = FrequencyChooser(
            minimum=50.0, maximum=2200.0,
            value=channel_center_mhz(DEFAULT_CHANNEL),
            channels=tv_channel_items())
        self.layout.addWidget(self.cf_chooser)

    def create_gain_control(self):
        row = Qt.QHBoxLayout()
        self.gain_slider = Qt.QSlider(QtCore.Qt.Horizontal)
        self.gain_slider.setRange(0, 100)
        default = 60 if self.radio_type == 'bb60' else 55
        self.gain_slider.setValue(default)
        self.gain_label = Qt.QLabel(f"RF Gain: {default}%")
        self.gain_slider.valueChanged.connect(
            lambda v: self.gain_label.setText(f"RF Gain: {v}%"))
        row.addWidget(self.gain_label)
        row.addWidget(self.gain_slider)
        self.layout.addLayout(row)

    def create_sound_control(self):
        self.sound_check = Qt.QCheckBox("Play the sound carrier")
        self.sound_check.setChecked(True)
        self.sound_check.setToolTip(
            "System M sends the sound on its own FM carrier 4.5 MHz above "
            "the picture. Turning this off leaves the picture untouched - "
            "the two are decoded by separate chains.")
        self.layout.addWidget(self.sound_check)

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
            print(f"NTSC receiver: could not read saved config: {exc}",
                  file=sys.stderr)
            return
        restore = [
            ('center_mhz', lambda v: self.cf_chooser.setValue(float(v))),
            ('gain_percent', lambda v: self.gain_slider.setValue(int(v))),
            ('sound', lambda v: self.sound_check.setChecked(bool(v))),
        ]
        for key, apply in restore:
            if key in config:
                try:
                    apply(config[key])
                except Exception as exc:
                    print(f"NTSC receiver: ignoring saved {key!r}: {exc}",
                          file=sys.stderr)

    def save_config(self):
        config = {
            'radio_type': self.radio_type,
            'center_mhz': self.cf_chooser.value(),
            'gain_percent': self.gain_slider.value(),
            'sound': self.sound_check.isChecked(),
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
            'sound': self.sound_check.isChecked(),
        }


# --------------------------------------------------------------------------
# The receiver
# --------------------------------------------------------------------------

def rx_gain_plan(percent, radio_type):
    """The HackRF's three receive stages, as in the other receivers."""
    percent = min(max(float(percent), 0.0), 100.0)
    if radio_type != 'hackrf':
        return {}
    amp = 14.0 if percent >= 25 else 0.0
    total = percent / 100.0 * 88.0
    lna = min(40.0, round(total * 0.45 / 8.0) * 8.0)
    vga = min(62.0, max(0.0, round((total - lna) / 2.0) * 2.0))
    return {'AMP': amp, 'LNA': lna, 'VGA': vga}


class ntscReceiver(gr.top_block, Qt.QWidget):
    # What this window's own controls change that its dialog should
    # open on next time - see apps/utils.py: save_flowgraph_settings.
    SAVED_SETTINGS = {'gain_percent': 'gain_percent',
                      'center_mhz': 'center_mhz'}

    def __init__(self, config_values=None):
        gr.top_block.__init__(self, "NTSC Video Receiver", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("NTSC Video Receiver")
        apply_flowgraph_theme(self)
        # The lock line's colours, from the theme the window was just
        # painted in - not taken at import, when the launcher may have had
        # another theme in force.
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

        self.settings = Qt.QSettings("GNU Radio", "ntscReceiver")
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
        self.samp_rate = SAMPLE_RATES.get(self.radio_type, 20e6)
        self.want_sound = bool(values.get('sound', True))
        self.volume = 0.5
        self.sound = None
        self._last_decoded = 0
        self._last_time = None

        self._build_controls()
        self._build_flowgraph()
        self._build_readout()
        self._build_spectrum()

        self.status_timer = Qt.QTimer(self)
        self.status_timer.timeout.connect(self.refresh)
        self.status_timer.start(500)

    # ------------------------------------------------------------------ UI
    def _build_controls(self):
        row = Qt.QHBoxLayout()
        row.addWidget(Qt.QLabel("Channel:"))
        self.channel_combo = Qt.QComboBox()
        for n, centre in [(n, c) for n, c, _ in tv_channel_items()]:
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

        if self.want_sound:
            row.addSpacing(15)
            self.mute_btn = Qt.QPushButton("Mute")
            self.mute_btn.setCheckable(True)
            self.mute_btn.setMaximumWidth(70)
            self.mute_btn.toggled.connect(self.set_muted)
            row.addWidget(self.mute_btn)
            self.volume_slider = Qt.QSlider(QtCore.Qt.Horizontal)
            self.volume_slider.setRange(0, 100)
            self.volume_slider.setValue(int(self.volume * 100))
            self.volume_slider.setMinimumWidth(90)
            self.volume_slider.valueChanged.connect(self.set_volume)
            row.addWidget(self.volume_slider)

        row.addStretch(1)
        self.watch_btn = Qt.QPushButton("Watch")
        self.watch_btn.setToolTip(
            "Hand the decoded pictures to a media player. NTSC has no "
            "transport stream to pass on, so what goes across is raw frames.")
        self.watch_btn.clicked.connect(self.toggle_watch)
        row.addWidget(self.watch_btn)
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
        self.lbl = {}

        def field(grid, key, caption, row, col, font=None, span=1):
            grid.addWidget(Qt.QLabel(f"<b>{caption}</b>"), row, col * 2)
            value = Qt.QLabel("-")
            if font:
                value.setFont(font)
            value.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            grid.addWidget(value, row, col * 2 + 1, 1, span)
            self.lbl[key] = value

        signal = Qt.QGroupBox("Signal")
        sg = Qt.QGridLayout()
        signal.setLayout(sg)
        field(sg, 'lock', "Status", 0, 0, big, span=3)
        field(sg, 'level', "Input Level", 1, 0)
        field(sg, 'levels', "Sync / Blanking", 1, 1)
        if self.want_sound:
            # Two fields, not one: a carrier with no deviation on it is a
            # station sending silence, which is a different thing from no
            # sound carrier at all.
            field(sg, 'sound', "Sound Carrier", 2, 0)
            field(sg, 'deviation', "Deviation", 2, 1)
        sg.setColumnStretch(1, 1)
        sg.setColumnStretch(3, 1)
        sg.setHorizontalSpacing(12)
        self.top_grid_layout.addWidget(signal, 1, 0, 1, 4)

        picture = Qt.QGroupBox("Picture")
        pg = Qt.QGridLayout()
        picture.setLayout(pg)
        field(pg, 'line_rate', "Line Rate", 0, 0)
        field(pg, 'frames', "Frames Decoded", 0, 1)
        field(pg, 'dropped', "Dropped / Failed", 1, 0)
        field(pg, 'colour', "Colour", 1, 1)
        pg.setColumnStretch(1, 1)
        pg.setColumnStretch(3, 1)
        pg.setHorizontalSpacing(12)
        self.top_grid_layout.addWidget(picture, 1, 4, 1, 6)

        self.action_note = Qt.QLabel("")
        self.top_grid_layout.addWidget(self.action_note, 9, 0, 1, 10)
        for r in (0, 1, 9):
            self.top_grid_layout.setRowStretch(r, 0)
        self.top_grid_layout.setRowStretch(2, 1)

    def _build_spectrum(self):
        """The television channel, drawn the way the transmitter draws it.

        The radio is tuned LO_OFFSET *above* the channel centre, so a sink
        fed straight off it is centred on the local oscillator rather than
        on the channel: 20 MHz wide with the channel squashed into the
        left third and two thirds of the plot empty air. The two windows
        then look nothing like each other even though they are showing the
        same signal, which is exactly what a side-by-side pair must not
        do. So the channel is mixed back to the centre and decimated to
        the transmitter's own 10 MS/s first. One `freq_xlating_fir_filter`
        does both, at a dot product per *output* sample.

        The filter is flat across the whole 6 MHz channel and stops at
        exactly the decimated Nyquist, 5 MHz, so nothing folds into the
        plot: a display that invents a signal is worse than no display.
        """
        self.spectrum_channel = filter.freq_xlating_fir_filter_ccf(
            SPECTRUM_DECIM,
            filter.firdes.low_pass(1.0, self.samp_rate, 3.6e6, 1.4e6),
            -LO_OFFSET, self.samp_rate)
        self.spectrum = qtgui.freq_sink_c(
            2048, window.WIN_BLACKMAN_hARRIS,
            self.center_mhz * 1e6, self.samp_rate / SPECTRUM_DECIM,
            'Received Channel - visual carrier 1.75 MHz below channel centre, '
            'sound 4.5 MHz above it', 1, None)
        self.spectrum.set_update_time(0.10)
        self.spectrum.set_y_axis(*SPECTRUM_Y_AXIS)
        self.spectrum.set_y_label('Relative Gain', 'dB')
        self.spectrum.set_trigger_mode(qtgui.TRIG_MODE_FREE, 0.0, 0, "")
        self.spectrum.enable_autoscale(False)
        self.spectrum.enable_grid(True)
        self.spectrum.set_fft_average(0.2)
        self.spectrum.enable_axis_labels(True)
        self.spectrum.enable_control_panel(False)
        self.spectrum.disable_legend()
        widget = sip.wrapinstance(self.spectrum.qwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(widget, 2, 0, 7, 10)
        self.connect(self.radio_source, self.spectrum_channel, self.spectrum)

    # ----------------------------------------------------------- flowgraph
    def _build_flowgraph(self):
        tuned = self.center_mhz * 1e6 + LO_OFFSET
        if self.radio_type == 'usrp':
            self.radio_source = uhd.usrp_source(
                ",".join((f"addr={self.usrp_ip}", '')),
                uhd.stream_args(cpu_format="fc32", args='',
                                channels=list(range(0, 1))),
            )
            self.radio_source.set_samp_rate(self.samp_rate)
            self.radio_source.set_center_freq(tuned, 0)
            self.radio_source.set_antenna("RX2", 0)
        elif self.radio_type == 'bb60':
            from apps.bb60_source import bb60_source
            self.radio_source = bb60_source(center_freq=tuned,
                                            sample_rate=self.samp_rate,
                                            gain_percent=self.gain_percent)
        else:
            self.radio_source = soapy.source('driver=hackrf', 'fc32', 1, '', '',
                                             [''], [''])
            self.radio_source.set_sample_rate(0, self.samp_rate)
            self.radio_source.set_frequency(0, tuned)
            self.radio_source.set_gain_mode(0, False)

        self.demod = NtscDemod(self.samp_rate)
        self.frames = ntsc_frame_sink(self.demod.composite_rate)
        self.level_probe = blocks.probe_signal_f()
        # 0.0001 was so slow the reading flickered between the signal and
        # the noise floor from one refresh to the next.
        self.rms = blocks.rms_cf(0.01)
        self.connect(self.radio_source, self.demod, self.frames)
        self.connect(self.radio_source, self.rms, self.level_probe)
        if self.want_sound:
            self._build_sound()

    def _build_sound(self):
        """The sound carrier, out of the speakers.

        Built off the *radio* rather than off the picture chain, because
        the picture chain's Nyquist filter exists partly to remove the
        sound. If there is no audio device the picture must still work, so
        a failure here turns sound off and says so rather than taking the
        receiver down with it.
        """
        self.sound = NtscSound(self.samp_rate, volume=self.volume)
        try:
            self.audio_out = audio.sink(AUDIO_RATE, '', True)
        except Exception as exc:
            print(f"NTSC receiver: no audio output ({exc})", file=sys.stderr)
            self.want_sound = False
            self.sound = None
            return
        self.connect(self.radio_source, self.sound, self.audio_out)

    # ------------------------------------------------------------ controls
    def apply_gain(self):
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
        for name, value in rx_gain_plan(self.gain_percent, 'hackrf').items():
            self.radio_source.set_gain(0, name, value)

    def set_gain(self, percent):
        self.gain_percent = float(percent)
        self.gain_value.setText(f"{int(percent)}%")
        self.apply_gain()

    def set_volume(self, percent):
        """Loudness, as a plain 0-100% like every other slider here."""
        self.volume = max(0.0, min(100.0, float(percent))) / 100.0
        if self.sound is not None and not self._muted():
            self.sound.set_volume(self.volume)

    def _muted(self):
        return hasattr(self, 'mute_btn') and self.mute_btn.isChecked()

    def set_muted(self, muted):
        # The sound chain keeps running while muted: the carrier level and
        # deviation meters go on reading, which is the point of having them.
        if self.sound is not None:
            self.sound.set_volume(0.0 if muted else self.volume)
        if hasattr(self, 'mute_btn'):
            self.mute_btn.setText("Unmute" if muted else "Mute")

    def set_center(self, mhz):
        mhz = float(mhz)
        if abs(mhz - self.center_mhz) < 1e-6:
            return
        self.center_mhz = mhz
        tuned = mhz * 1e6 + LO_OFFSET
        if self.radio_type == 'usrp':
            self.radio_source.set_center_freq(tuned, 0)
        elif self.radio_type == 'bb60':
            self.radio_source.set_center_freq(tuned)
        else:
            self.radio_source.set_frequency(0, tuned)
        # The axis follows the channel, not the radio - the mixer inside
        # spectrum_channel is a fixed offset from the radio and needs no
        # retuning.
        self.spectrum.set_frequency_range(mhz * 1e6,
                                          self.samp_rate / SPECTRUM_DECIM)
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
        self.clear_stats()

    def clear_stats(self):
        self.frames.frames_decoded = 0
        self.frames.frames_dropped = 0
        self.frames.failures = 0
        self._last_decoded = 0
        self._last_time = None

    def toggle_watch(self):
        if self.frames.player_alive():
            self.frames.stop_player()
            self.watch_btn.setText("Watch")
            self.action_note.setText("")
            return
        # The rate pictures actually arrive at, not the standard's frame
        # rate: a buffer holds BUFFER_FRAMES frames and yields one picture,
        # so they come 1.7 frames apart in real time. Telling a player
        # 29.97 when it is being fed 17.6 leaves it starved between frames.
        argv = find_player(frame_rate=1.0 / (FRAME * ntsc_frame_sink.BUFFER_FRAMES))
        if argv is None:
            Qt.QMessageBox.warning(
                self, "No Media Player",
                "Watching needs ffplay or mpv on PATH, and neither is "
                "installed.")
            return
        try:
            self.frames.start_player(argv)
        except Exception as exc:
            Qt.QMessageBox.warning(self, "Could Not Start Player", str(exc))
            return
        self.watch_btn.setText("Stop Watching")
        self.action_note.setText(
            f"{os.path.basename(argv[0])} started - it shows the decoded "
            "pictures as they arrive.")

    # ------------------------------------------------------------- readout
    def refresh(self):
        now = time.time()
        rms = self.level_probe.level()
        self.lbl['level'].setText(
            f"{20 * np.log10(rms):.1f} dBFS" if rms > 0 else "-")

        status = self.frames.status
        decoded = self.frames.frames_decoded
        # Only update the rate over a decent interval: called twice in quick
        # succession - which happens if anything refreshes the window as
        # well as the timer - a sub-millisecond gap reads as 0 frames/s.
        rate = getattr(self, '_frame_rate', 0.0)
        if self._last_time is None:
            self._last_decoded, self._last_time = decoded, now
        elif now - self._last_time >= 0.4:
            rate = (decoded - self._last_decoded) / (now - self._last_time)
            self._frame_rate = rate
            self._last_decoded, self._last_time = decoded, now

        line_rate = status.get('line_rate', 0.0)
        if line_rate:
            # How far the transmitter's line rate is from 15734.266 Hz, the
            # same clock error the ATSC receiver reads off the pilot.
            ppm = (line_rate - FH) / FH * 1e6
            self.lbl['line_rate'].setText(
                f"{line_rate:,.1f} Hz  ({ppm:+.0f} ppm)")
        else:
            self.lbl['line_rate'].setText("-")

        if status.get('sync') is not None and 'blank' in status:
            self.lbl['levels'].setText(
                f"{status['sync']:.3f} / {status['blank']:.3f}")
        else:
            self.lbl['levels'].setText("-")

        # A buffer holds 1.7 frames and yields one picture, so the ceiling
        # is 29.97/1.7 = 17.6 a second, not 29.97. Say what is actually
        # attainable rather than flagging the design as a shortfall.
        self.lbl['frames'].setText(
            f"{decoded:,}  ({rate:.1f}/s of {1.0 / FRAME / 1.7:.1f})"
            if decoded else "none")
        self.lbl['dropped'].setText(
            f"{self.frames.frames_dropped} / {self.frames.failures}")
        frame = self.frames.frame
        if frame is not None:
            # A monochrome signal decodes with the colour axes near zero.
            saturation = float(np.abs(frame - frame.mean(axis=2,
                                                         keepdims=True)).mean())
            self.lbl['colour'].setText(
                "colour" if saturation > 0.02 else "monochrome")
        else:
            self.lbl['colour'].setText("-")

        if self.sound is not None:
            level = self.sound.carrier_level()
            self.lbl['sound'].setText(
                f"{level:.1f} dBFS" if level is not None else "-")
            deviation = self.sound.deviation_hz()
            # Below about a hundred hertz rms there is nothing on the
            # carrier worth calling sound - it is the demodulator's own
            # noise floor, and reading "0.1 kHz" as if it were programme
            # invites hunting for a fault that is not there.
            self.lbl['deviation'].setText(
                f"{deviation / 1e3:.1f} kHz rms" if deviation > 100
                else "silent")

        text, colour = self._status(decoded, rate, line_rate)
        self.lbl['lock'].setText(text)
        self.lbl['lock'].setStyleSheet(f"color: {colour};")

        if not self.frames.player_alive() and self.watch_btn.text() != "Watch":
            self.frames.stop_player()
            self.watch_btn.setText("Watch")
            self.action_note.setText("")

    def _status(self, decoded, rate, line_rate):
        if not decoded:
            return ("No picture - nothing is syncing on this channel", self.BAD)
        if line_rate and abs(line_rate - FH) / FH > 0.01:
            return (f"Syncing, but the line rate is {line_rate:,.0f} Hz "
                    f"and should be {FH:,.0f}", self.WARN)
        if rate < 12:
            return (f"Picture, but only {rate:.0f} frames a second", self.WARN)
        return ("Locked - picture decoding", self.GOOD)

    def closeEvent(self, event):
        self.settings = Qt.QSettings("GNU Radio", "ntscReceiver")
        self.settings.setValue("geometry", self.saveGeometry())
        self.status_timer.stop()
        self.stop()
        self.wait()
        event.accept()


def main(top_block_cls=ntscReceiver, options=None, app=None, config_values=None):
    if app is None:
        app = Qt.QApplication(sys.argv)

    tb = top_block_cls(config_values)
    tb.start()
    tb.apply_gain()
    tb.show()

    import signal as _signal

    def sig_handler(sig=None, frame=None):
        tb.stop()
        tb.wait()
        Qt.QApplication.quit()

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
