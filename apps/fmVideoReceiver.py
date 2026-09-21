#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# SPDX-License-Identifier: GPL-3.0
#
# FM Video Receiver - the other end of ``fmVideoXmitter``, and the other
# side of its tile. It discriminates the carrier, takes the pre-emphasis
# back out, decodes the composite into pictures and demodulates the sound
# off the subcarriers riding above them.
#
# It is also a measuring instrument, on purpose. The FPV profile's video
# deviation and its "no pre-emphasis" are this project's guesses - neither
# RichWave datasheet gives either - so a receiver pointed at a real FPV
# transmitter has to be able to say what that transmitter actually does,
# rather than assume the guesses and show a picture that is subtly wrong.
# Everything under "Measured" is read off the signal against levels the
# television standard fixes, so none of it needs a test pattern or any
# knowledge of what is being televised.

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

import numpy as np  # type: ignore
try:                 # PyQt5 ships sip inside the package; some builds also
    import sip       # expose it at the top level.
except ImportError:  # pragma: no cover - depends on the PyQt5 build
    from PyQt5 import sip  # type: ignore
from gnuradio import analog, audio, blocks, filter, gr, qtgui, soapy, uhd  # type: ignore
from gnuradio.fft import window  # type: ignore
from PyQt5 import Qt, QtCore  # type: ignore

from apps.fm_video_core import (DEFAULT_PROFILE, PREEMPHASIS_CHOICES,
                                PROFILES, RECEIVE_CUTOFF, VIDEO_CENTRE,
                                click_threshold_hz,
                                compensation_band, lowest_subcarrier,
                                discriminator_compensation_taps,
                                fm_integrator_gain, preemphasis,
                                preemphasis_for, subcarrier_dbc,
                                subcarrier_index)
from apps.ntsc_encode import NTSC, PAL, STANDARDS
# Both ends of the link have to agree about the rates, so the receiver takes
# them from the transmitter rather than repeating them - as the NTSC
# receiver takes its carrier frequencies from the NTSC transmitter.
from apps.fmVideoXmitter import (DEVIATION_MAX_MHZ, DEVIATION_MIN_MHZ,
                                 FREQ_MAX_MHZ, FREQ_MIN_MHZ, RF_RATE,
                                 VIDEO_RATES)
# And the threaded frame decoder, the player and the HackRF's receive gain
# plan come from the NTSC receiver, which is where they were written.
from apps.ntscReceiver import CompositeFrameSink, find_player, rx_gain_plan
from apps.theme import TOKENS
from apps.utils import (apply_dark_theme, apply_flowgraph_theme, radio_label,
                        read_settings, update_app_config, SPECTRUM_Y_AXIS,
                        FrequencyChooser, FREQ_DECIMALS)

DEFAULT_FORMAT = NTSC.key
AUDIO_RATE = 48000
#: The top of the programme audio on a sound subcarrier.
AUDIO_TOP = 15e3
#: What a sound subcarrier is brought down to before it is demodulated.
#: Demodulating straight at the audio rate folds everything below it into
#: the audio band - the same trap the NTSC receiver's sound has.
SOUND_IF_RATE = 200e3
#: The USRP here has a WBX, which stops at 2.2 GHz and so cannot reach the
#: 5.8 GHz FPV band at all.
USRP_TOP_MHZ = 2200.0
#: Weaker than this and what the sound chain is demodulating is the noise in
#: an empty band, not a subcarrier. A real one is 20 to 30 dB under the
#: carrier; measured against a real FPV transmitter that sends no sound at
#: all, the empty 6.0 and 6.5 MHz bands read -52 dBc, and the receiver
#: reported that as a subcarrier carrying 30 kHz rms of programme.
NO_SUBCARRIER_DBC = -45.0
#: And below this there is nothing on a subcarrier worth calling sound. The
#: NTSC receiver uses 100 Hz on its main carrier; a subcarrier 27 dB down
#: measured 1.0-1.2 kHz rms of the demodulator's own noise on a 23 dB link
#: with silence going out, so 100 Hz here would never once say silent.
SILENT_HZ = 2000.0


class FmVideoDemod(gr.hier_block2):
    """FM video at the radio's rate in; composite video and the baseband out.

    Output 0 is composite at the format's own video rate, sync tip near 0
    and peak white near 1, which is what ``CompositeDecoder`` reads. Output
    1 is the discriminated baseband at the radio's rate, *before*
    de-emphasis: the sound subcarriers ride up there, and so does everything
    worth measuring.

    **Discriminating is the whole of it.** A constant-envelope signal
    carries all of its information in the phase, so there is no carrier to
    recover and no gain control to settle - which is most of why FM video
    was the easy one to receive. What follows is only undoing what the
    transmitter did, in reverse order: the sampled modulator's own
    over-swing, then the pre-emphasis, then the band.

    **Neither a wrong deviation nor a mistuned carrier costs the picture,
    and it is worth knowing why.** ``CompositeDecoder`` reads sync tip and
    blanking off the signal and scales everything by the step between them,
    so a deviation setting that is out by a factor simply rescales what it
    is handed, and a carrier offset moves a level it measures rather than
    assumes. That is what makes this usable against a transmitter whose
    numbers nobody knows: get those two wrong and the picture still comes
    up, and the readout says by how much. Pre-emphasis is the one that does
    not forgive - the wrong curve tilts the picture's frequency response,
    which shows as wrong colour rather than as a wrong setting.
    """

    def __init__(self, profile, standard=NTSC, rf_rate=RF_RATE,
                 deviation_pp=None, preemphasis_key=None):
        gr.hier_block2.__init__(
            self, "fm_video_demod",
            gr.io_signature(1, 1, gr.sizeof_gr_complex),
            gr.io_signature(2, 2, gr.sizeof_float))
        self.profile = profile
        self.standard = standard
        self.rf_rate = float(rf_rate)
        self.video_rate = VIDEO_RATES[standard.key]
        self.deviation_pp = float(deviation_pp or profile.deviation_pp)
        self.preemphasis_key = preemphasis_for(
            profile.preemphasis if preemphasis_key is None else preemphasis_key,
            standard.lines)

        # Hertz of carrier swing, divided by the deviation, is composite.
        self.discriminator = analog.quadrature_demod_cf(
            self.rf_rate / (2 * pi * self.deviation_pp))
        self.connect(self, self.discriminator)
        self.connect(self.discriminator, (self, 1))

        # The transmitter's modulator sums phase a sample at a time, which
        # swings the carrier further than asked the higher a component sits;
        # differencing phase here is the same mistake in reverse and reads
        # it short again. The transmitter corrects its half, this corrects
        # ours, and between them a tone comes back the size it was sent.
        self.integrator_fix = filter.fir_filter_fff(
            1, discriminator_compensation_taps(
                self.rf_rate, compensation_band(standard.video_band)))
        # The de-emphasis is always in the chain, an identity when there is
        # none, so that the curve can be changed while it runs - which is
        # how an unknown transmitter's own pre-emphasis gets found.
        self.deemphasis = filter.iir_filter_ffd(*self._deemphasis_taps(), False)
        self.picture = filter.fft_filter_fff(
            self._picture_decimation(), self._picture_taps(), 1)
        self.centre = blocks.add_const_ff(VIDEO_CENTRE)
        self.connect(self.discriminator, self.integrator_fix, self.deemphasis,
                     self.picture, self.centre)
        tail = self.centre
        if self._picture_decimation() * self.video_rate != self.rf_rate:
            # PAL's 12.5 MS/s is 5/8 of the radio's 20, which no integer
            # decimation reaches. The sharp filtering has already been done
            # above, so this only has to carry the rate.
            ratio = Fraction(self.video_rate / self.rf_rate
                             ).limit_denominator(64)
            self.resample = filter.rational_resampler_fff(
                interpolation=ratio.numerator, decimation=ratio.denominator,
                taps=[], fractional_bw=0.45)
            self.connect(tail, self.resample)
            tail = self.resample
        self.connect(tail, (self, 0))

    # -- the picture filter ----------------------------------------------

    def _picture_decimation(self):
        """Integer decimation done inside the picture filter itself.

        NTSC's 10 MS/s is half the radio's, so its filter decimates as it
        goes. PAL's is not, so this is 1 and a resampler follows.
        """
        ratio = self.rf_rate / self.video_rate
        return int(ratio) if abs(ratio - round(ratio)) < 1e-9 else 1

    def _picture_taps(self):
        """Flat over the picture, and *gone* by the lowest sound subcarrier.

        This is the one filter in the receiver that has to be sharp, and it
        is not for the picture's sake. A sound subcarrier sits at a tenth of
        the picture's own swing, and decimating to NTSC's 10 MS/s folds
        6.0 MHz onto 4.0 - straight into the chroma. PAL is worse: its own
        band reaches 5.0 MHz, so there are 400 kHz to stop in. An FFT filter
        makes that affordable - measured, 12x real time at 20 MS/s where a
        plain FIR of the same taps was 8x.

        **It stops at 6.0 MHz whichever profile is selected, and that is
        deliberate.** Letting the transition widen when the sound sat higher
        - F.405 puts its subcarrier at 6.8 - is cheaper and looks harmless:
        21 taps instead of 35, still 34 dB down where the sound is. But a
        windowed sinc with a wide transition droops long before it, and that
        one read **0.68 dB low at the colour subcarrier**. The burst is what
        this receiver measures a transmitter's pre-emphasis *with*, so the
        instrument would have been reporting most of a decibel of its own
        filter as the transmitter's curve. Fixed at 6.0 it is 0.008 dB.
        """
        cutoff = RECEIVE_CUTOFF[self.standard.key]
        return filter.firdes.low_pass(1.0, self.rf_rate, cutoff,
                                      lowest_subcarrier() - cutoff)

    def _deemphasis_taps(self):
        curve = preemphasis(self.preemphasis_key)
        if curve is None:
            return [1.0], [1.0]
        b, a = curve.inverse_coefficients(self.rf_rate)
        return list(b), list(a)

    # -- live settings ---------------------------------------------------

    def set_deviation(self, deviation_pp):
        self.deviation_pp = float(deviation_pp)
        self.discriminator.set_gain(self.rf_rate / (2 * pi * self.deviation_pp))

    def set_preemphasis(self, key):
        """Change the curve while it runs, which is how an unknown one is found.

        Both taps go in together, so the filter never runs with one curve's
        numerator and another's denominator.
        """
        self.preemphasis_key = preemphasis_for(key, self.standard.lines)
        b, a = self._deemphasis_taps()
        self.deemphasis.set_taps(b, a)
        return self.preemphasis_key

    def dc_gain(self):
        """What the de-emphasis does to DC, which a carrier offset arrives as.

        The transmitter sends low frequencies ``A`` dB down and this puts
        them back, so an offset of the carrier comes out of the de-emphasis
        that much bigger than it was on the air. Undoing that is the
        difference between reading a real offset and reading three times it.
        """
        curve = preemphasis(self.preemphasis_key)
        return 10 ** (-curve.A / 20) if curve is not None else 1.0


class FmVideoSound(gr.hier_block2):
    """One sound subcarrier: the FM baseband in, audio at 48 kHz out.

    The sound on an FM video link is an FM station riding in the same
    baseband as the picture, above it - so this is an FM receiver whose
    aerial is the discriminator output of another one. It takes the
    baseband *before* de-emphasis, because the picture's pre-emphasis is
    applied to the picture alone and the subcarriers are added after it.

    Mixing down, filtering to Carson's bandwidth and decimating are one
    ``freq_xlating_fir_filter``, which costs a dot product per *output*
    sample - so 20 MS/s in costs what 200 kS/s costs.
    """

    def __init__(self, profile, freq, rf_rate=RF_RATE, audio_rate=AUDIO_RATE,
                 volume=0.5):
        gr.hier_block2.__init__(
            self, "fm_video_sound",
            gr.io_signature(1, 1, gr.sizeof_float),
            gr.io_signature(1, 1, gr.sizeof_float))
        self.profile = profile
        self.freq = float(freq)
        self.rf_rate = float(rf_rate)
        decimation = max(1, int(round(self.rf_rate / SOUND_IF_RATE)))
        self.if_rate = self.rf_rate / decimation
        # Carson's rule, and then the widest transition that still stops
        # before the decimated Nyquist - which is the cheapest filter that
        # folds nothing at all into the sound.
        carson = 2 * (profile.audio_deviation + AUDIO_TOP)
        cutoff = carson / 2
        transition = max(self.if_rate / 2 - cutoff, 0.2 * cutoff)
        self.channel = filter.freq_xlating_fir_filter_fcf(
            decimation,
            filter.firdes.low_pass(1.0, self.rf_rate, cutoff, transition),
            self.freq, self.rf_rate)
        # Scaled so full deviation is +-1.0, which makes the meter read in
        # kilohertz with no second constant.
        self.demod = analog.quadrature_demod_cf(
            self.if_rate / (2 * pi * profile.audio_deviation))
        self.audio_lpf = filter.fir_filter_fff(
            1, filter.firdes.low_pass(1.0, self.if_rate, AUDIO_TOP, 4e3))
        ratio = Fraction(int(audio_rate),
                         int(round(self.if_rate))).limit_denominator(1000)
        self.resamp = filter.rational_resampler_fff(
            interpolation=ratio.numerator, decimation=ratio.denominator,
            taps=[], fractional_bw=0)
        self.deemph = analog.fm_deemph(float(audio_rate), profile.audio_tau)
        self.gain = blocks.multiply_const_ff(float(volume))
        self.connect(self, self.channel, self.demod, self.audio_lpf,
                     self.resamp, self.deemph, self.gain, self)

        # Two meters, as in the NTSC receiver: the level says the subcarrier
        # is being transmitted at all, the deviation says something is
        # modulating it. A transmitter with no microphone sends the first
        # and not the second, which is a different thing from silence.
        self.level_rms = blocks.rms_cf(0.01)
        self.level_probe = blocks.probe_signal_f()
        self.connect(self.channel, self.level_rms, self.level_probe)
        self.deviation_rms = blocks.rms_ff(0.005)
        self.deviation_probe = blocks.probe_signal_f()
        self.connect(self.demod, self.deviation_rms, self.deviation_probe)

    def set_volume(self, value):
        self.gain.set_k(float(value))

    def sidebands_dbc(self, deviation_pp):
        """The subcarrier's first sideband against the carrier, in dB.

        That is the number a datasheet quotes and a spectrum analyser shows,
        so it is the one to compare a real transmitter against. Two
        corrections on the way: a real cosine mixes down to half its
        amplitude, and differencing phase reads a tone at this frequency
        short by the modulator's own integrator gain.
        """
        rms = self.level_probe.level()
        if rms <= 0:
            return None
        swing = (2.0 * rms * float(deviation_pp)
                 * fm_integrator_gain(self.freq, self.rf_rate))
        return subcarrier_dbc(swing / self.freq)

    def deviation_hz(self):
        """How hard the sound is modulating this subcarrier, hertz rms."""
        return self.deviation_probe.level() * self.profile.audio_deviation


#: One sample in this many is enough for the envelope moments: they are an
#: average over a quarter of a second, which is still 625,000 samples at
#: 20 MS/s. Clicks are *not* decimated - a click is a handful of samples,
#: and skipping seven in eight would miss most of them.
MOMENT_STRIDE = 8


class FmLinkStatistics(gr.sync_block):
    """Carrier-to-noise and clicks, from three slow streams.

    **Neither is the input level**, which is what a receiver usually shows
    and which says nothing here: an FM signal's amplitude is constant
    whatever is modulating it, so a strong noise floor reads exactly like a
    strong carrier.

    **Carrier-to-noise** is the number FM lives by, because FM has a
    threshold. Above about 12 dB in this bandwidth every dB of carrier is a
    dB of picture; below it the picture does not fade into snow the way an
    AM one does, it breaks up. It comes from the second and fourth moments
    of the envelope power, which separate a constant-envelope signal from
    complex noise with no need to know where either is: for a carrier of
    power ``c`` in complex noise, ``m4 = 2 m2^2 - c^2``.

    **Clicks** are what arriving below that threshold looks like - the
    discriminator jumps and the picture takes a sparkle. Anything swinging
    further than the picture and the subcarriers together could have swung
    it is counted as one.

    **All of the per-sample work happens in C++, and that is the point.**
    This was one Python block reading the RF and the discriminator at the
    radio's full rate. It worked, and it held the interpreter lock that the
    frame decoder - also Python, on its own thread - needs to get a picture
    out. Measured at the radio's own pace: without it, every frame the
    buffer allows and none dropped; with it, 7 of 70 buffers dropped in
    NTSC and 13 of 58 in PAL. So `link_quality_chain` squares the envelope,
    keeps one sample in eight and sums in blocks; rectifies, compares and
    sums in blocks; and what arrives here is three streams at 100 S/s.
    """

    def __init__(self, samples_per_item, window_seconds=0.25,
                 items_per_second=100):
        gr.sync_block.__init__(
            self, name='fm_link_statistics',
            in_sig=[np.float32, np.float32, np.float32], out_sig=None)
        self.samples_per_item = int(samples_per_item)
        self._window = max(1, int(window_seconds * items_per_second))
        self._items = 0
        self._sum = 0.0
        self._sum_squares = 0.0
        self._counted = 0
        self.cnr_db = None
        self.clicks = 0
        self.samples = 0

    def clear(self):
        self.clicks = 0
        self.samples = 0

    def work(self, input_items, output_items):
        total, total_squares, clicks = input_items[:3]
        n = min(len(total), len(total_squares), len(clicks))
        if n <= 0:
            return 0
        self._sum += float(np.sum(total[:n], dtype=np.float64))
        self._sum_squares += float(np.sum(total_squares[:n], dtype=np.float64))
        self._counted += n * self.samples_per_item
        self.clicks += int(round(float(np.sum(clicks[:n], dtype=np.float64))))
        self.samples += n * self.samples_per_item * MOMENT_STRIDE
        self._items += n
        if self._items >= self._window:
            m2 = self._sum / self._counted
            m4 = self._sum_squares / self._counted
            carrier = np.sqrt(max(2 * m2 * m2 - m4, 0.0))
            noise = m2 - carrier
            self.cnr_db = (10 * np.log10(carrier / noise)
                           if carrier > 0 and noise > 1e-30 else None)
            self._items, self._sum, self._sum_squares = 0, 0.0, 0.0
            self._counted = 0
        return n


def link_quality_chain(flowgraph, rf, baseband, level, sample_rate=RF_RATE,
                       items_per_second=100):
    """Wire the carrier-to-noise and click measurement onto a flowgraph.

    ``rf`` is the radio's complex output and ``baseband`` the
    discriminator's float one. Returns ``(statistics, comparator)`` - the
    block the readout reads, and the one whose threshold moves when the
    deviation setting does.
    """
    per_item = max(1, int(round(sample_rate / MOMENT_STRIDE
                                / items_per_second)))
    power = blocks.complex_to_mag_squared(1)
    thin = blocks.keep_one_in_n(gr.sizeof_float, MOMENT_STRIDE)
    squares = blocks.multiply_ff(1)
    total = blocks.integrate_ff(per_item)
    total_squares = blocks.integrate_ff(per_item)
    flowgraph.connect(rf, power, thin)
    flowgraph.connect(thin, (squares, 0))
    flowgraph.connect(thin, (squares, 1))
    flowgraph.connect(thin, total)
    flowgraph.connect(squares, total_squares)

    swing = blocks.abs_ff(1)
    comparator = blocks.threshold_ff(level, level, 0.0)
    counted = blocks.integrate_ff(per_item * MOMENT_STRIDE)
    flowgraph.connect(baseband, swing, comparator, counted)

    statistics = FmLinkStatistics(per_item, items_per_second=items_per_second)
    flowgraph.connect(total, (statistics, 0))
    flowgraph.connect(total_squares, (statistics, 1))
    flowgraph.connect(counted, (statistics, 2))
    # The flowgraph owns the connections, but Python must keep these
    # wrappers alive or the scheduler ends up running on freed objects.
    statistics.keepalive = (power, thin, squares, total, total_squares,
                            swing, comparator, counted)
    return statistics, comparator


class FmVideoFrameSink(CompositeFrameSink):
    """The shared composite frame decoder, plus what an FM link wants measured.

    The pulses have already been found by the time ``measure`` is called,
    which is most of the work; everything here is read against levels the
    television standard fixes - the step from sync tip to blanking, and the
    colour burst - so none of it needs a test pattern or any knowledge of
    what is being televised.
    """

    def __init__(self, sample_rate, standard=NTSC, deviation_pp=5.0e6,
                 dc_gain=1.0):
        super().__init__(sample_rate, standard=standard)
        self.deviation_pp = float(deviation_pp)
        self.dc_gain = float(dc_gain)

    def measure(self, x, starts, widths, sync, blank, status):
        decoder = self.decoder
        std = self.standard
        tip = decoder.sync_tip_level(x, starts, widths, sync)
        porch = decoder.back_porch_level(x, starts, widths, blank)
        status['sync'], status['blank'] = tip, porch
        step = porch - tip
        if step <= 0:
            return
        # **The deviation, from the one step the standard fixes.** Sync tip
        # to blanking is 40 IRE of 140, or 300 mV of 1000, whatever the
        # picture is doing - so however big it comes back says how far the
        # transmitter really swings the carrier for a volt, against however
        # far this receiver was told it would. It does not depend on the
        # pre-emphasis: both levels are flat parts of the signal, so the
        # curve and its inverse cancel on them exactly.
        deviation = self.deviation_pp * step / std.blank
        status['deviation_pp'] = deviation
        # **And the carrier's offset, from where the sync tip landed** -
        # but measured against the deviation just found, not the one this
        # receiver was told. The tip sits half the swing below the carrier,
        # so its position carries both, and taking the set deviation for
        # the real one books the difference as tuning error: measured off
        # air against a transmitter swinging 8 MHz while the receiver
        # expected 5, that read the carrier as **1.5 MHz off** when it was
        # within a couple of kilohertz.
        #
        # De-emphasis lifts DC back up, so what is read here is bigger than
        # what was on the air by exactly that - see FmVideoDemod.dc_gain.
        #
        # Its precision follows the deviation's: an error of 1% in a 5 MHz
        # swing moves this by 25 kHz, so read it in tens of kilohertz and
        # not in parts per million.
        status['carrier_offset'] = self.dc_gain * (
            self.deviation_pp * (tip - VIDEO_CENTRE) + VIDEO_CENTRE * deviation)
        status['burst'] = decoder.burst_ratio(x, starts, widths, tip, porch)


def format_mismatch(line_rate, standard):
    """The other format's name when that is plainly what is arriving.

    An FPV camera sends NTSC or PAL and nothing on the air announces which,
    so the Format setting is a guess until there is a signal. The two line
    rates are 0.7% apart - far outside anything a crystal does - so a
    measured rate nearer the other standard's *is* the other standard, and
    saying so beats leaving someone with a picture that will not decode and
    no reason why.
    """
    if not line_rate:
        return None
    other = PAL if standard.key == NTSC.key else NTSC
    if (abs(line_rate - other.line_rate) < abs(line_rate - standard.line_rate)
            and abs(line_rate - other.line_rate) < 0.002 * other.line_rate):
        return other.key
    return None


def click_level(profile, deviation_pp):
    """`click_threshold_hz`, in the discriminator's own units.

    Its output is hertz of carrier swing divided by the deviation, so 1.0
    is the whole picture's swing and the threshold is a little over that.
    """
    return click_threshold_hz(profile, deviation_pp) / float(deviation_pp)


# --------------------------------------------------------------------------
# Configuration dialog
# --------------------------------------------------------------------------

class ConfigDialog(Qt.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FM Video Receiver Configuration")
        self.layout = Qt.QVBoxLayout(self)
        self.config_dir = "config"
        self.config_file = os.path.join(self.config_dir,
                                        "fmVideoReceiver_config.json")

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
        self.create_profile_selector()
        self.create_format_selector()
        self.create_frequency_control()
        self.create_gain_control()
        self.create_modulation_controls()
        self.create_sound_control()

        self.layout.addWidget(self.button_box)
        self.apply_profile(self.profile_combo.currentData())
        self.load_config()
        self.update_ok_state()
        apply_dark_theme(self)

    def create_cannot_receive(self):
        self.setWindowTitle("FM Video Receiver")
        row = Qt.QHBoxLayout()
        icon = Qt.QLabel()
        icon.setPixmap(self.style().standardIcon(
            Qt.QStyle.SP_MessageBoxWarning).pixmap(48, 48))
        icon.setAlignment(QtCore.Qt.AlignTop)
        row.addWidget(icon)
        message = Qt.QLabel(
            "<b>The Signal Hound VSG60 cannot receive.</b><br><br>"
            "It only transmits, so the FM Video Receiver has no radio to "
            "listen with. Choose the HackRF One, an Ettus USRP or the Signal "
            "Hound BB60D in Settings (the gear icon), then open it again.")
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

    def create_profile_selector(self):
        """The standard, which fills in the rest - as in the transmitter."""
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
        """NTSC or PAL - and the receiver says so if it picked wrong.

        An FPV camera sends one or the other and nothing on the air
        announces which, so this is a guess until there is a signal. The
        running window measures the line rate and names the other format
        if that is what is arriving.
        """
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
        key = preemphasis_for(self.preemph_combo.currentData(), self._lines())
        self.preemph_combo.setCurrentIndex(
            max(self.preemph_combo.findData(key), 0))

    def create_frequency_control(self):
        self.cf_chooser = FrequencyChooser(
            minimum=FREQ_MIN_MHZ, maximum=FREQ_MAX_MHZ,
            value=PROFILES[DEFAULT_PROFILE].default_mhz,
            channels=PROFILES[DEFAULT_PROFILE].channels)
        self.layout.addWidget(self.cf_chooser)
        # A USRP with a WBX cannot reach 5.8 GHz, and a receiver that simply
        # hears nothing looks exactly like a disconnected aerial - which has
        # cost this project whole afternoons more than once.
        self.reach_note = Qt.QLabel("")
        self.reach_note.setWordWrap(True)
        self.layout.addWidget(self.reach_note)
        self.cf_chooser.valueChanged.connect(lambda _v: self._check_reach())
        self._check_reach()

    def _check_reach(self):
        out_of_reach = (self.radio_type == 'usrp'
                        and self.cf_chooser.value() > USRP_TOP_MHZ)
        self.reach_note.setText(
            f"<b>This USRP cannot tune {self.cf_chooser.value():,.0f} MHz.</b> "
            f"The WBX daughterboard here stops at {USRP_TOP_MHZ:,.0f} MHz, so "
            "the 5.8 GHz FPV band is out of its reach - it would open and "
            "hear nothing at all. Use the HackRF One or the BB60D."
            if out_of_reach else "")

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

    def create_modulation_controls(self):
        row = Qt.QHBoxLayout()
        row.addWidget(Qt.QLabel("Deviation (MHz p-p):"))
        self.deviation_spin = Qt.QDoubleSpinBox()
        self.deviation_spin.setDecimals(2)
        self.deviation_spin.setSingleStep(0.25)
        self.deviation_spin.setRange(DEVIATION_MIN_MHZ, DEVIATION_MAX_MHZ)
        self.deviation_spin.setToolTip(
            "What the transmitter is expected to swing the carrier for a "
            "1 V picture. The decoder finds its own levels, so getting this "
            "wrong does not cost the picture - the running window measures "
            "what the transmitter actually does and says so.")
        row.addWidget(self.deviation_spin)
        row.addStretch()
        self.layout.addLayout(row)

        row = Qt.QHBoxLayout()
        row.addWidget(Qt.QLabel("Pre-emphasis:"))
        self.preemph_combo = Qt.QComboBox()
        for key, label in PREEMPHASIS_CHOICES:
            self.preemph_combo.addItem(label, key)
        self.preemph_combo.setToolTip(
            "The curve the transmitter used, which this takes back out. "
            "Unlike the deviation it does matter to the picture, and it can "
            "be changed while the receiver runs.")
        row.addWidget(self.preemph_combo, 1)
        self.layout.addLayout(row)

    def create_sound_control(self):
        self.sound_check = Qt.QCheckBox("Play the sound")
        self.sound_check.setChecked(True)
        self.sound_check.setToolTip(
            "The sound rides on FM subcarriers above the picture in the "
            "same baseband. Turning this off leaves the picture untouched - "
            "they are separate chains off the same discriminator.")
        self.layout.addWidget(self.sound_check)
        self.sound_label = Qt.QLabel("")
        self.sound_label.setWordWrap(True)
        self.layout.addWidget(self.sound_label)

    def apply_profile(self, key):
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
            print(f"FM video receiver: could not read saved config: {exc}",
                  file=sys.stderr)
            return

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

        # One setting that fails to restore must not take the rest with it.
        restore = [
            ('video_format', video_format),
            ('profile', profile),
            ('center_mhz', lambda: self.cf_chooser.setValue(
                float(config['center_mhz']))),
            ('gain_percent', lambda: self.gain_slider.setValue(
                int(config['gain_percent']))),
            ('deviation_mhz', lambda: self.deviation_spin.setValue(
                float(config['deviation_mhz']))),
            ('preemphasis', lambda: self.preemph_combo.setCurrentIndex(
                max(self.preemph_combo.findData(preemphasis_for(
                    config['preemphasis'], self._lines())), 0))),
            ('sound', lambda: self.sound_check.setChecked(
                bool(config['sound']))),
        ]
        for key, apply in restore:
            if key not in config:
                continue
            try:
                apply()
            except Exception as exc:
                print(f"FM video receiver: ignoring saved {key!r}: {exc}",
                      file=sys.stderr)
        self._check_reach()

    def save_config(self):
        config = {
            'radio_type': self.radio_type,
            'profile': self.profile_combo.currentData(),
            'video_format': self.format_combo.currentData(),
            'center_mhz': self.cf_chooser.value(),
            'gain_percent': self.gain_slider.value(),
            'deviation_mhz': self.deviation_spin.value(),
            'preemphasis': self.preemph_combo.currentData(),
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
            'profile': self.profile_combo.currentData(),
            'video_format': self.format_combo.currentData(),
            'center_mhz': self.cf_chooser.value(),
            'gain_percent': self.gain_slider.value(),
            'deviation_pp': self.deviation_spin.value() * 1e6,
            'preemphasis': self.preemph_combo.currentData(),
            'sound': self.sound_check.isChecked(),
        }


# --------------------------------------------------------------------------
# The receiver
# --------------------------------------------------------------------------

class fmVideoReceiver(gr.top_block, Qt.QWidget):
    # What this window's own controls change that its dialog should
    # open on next time - see apps/utils.py: save_flowgraph_settings.
    SAVED_SETTINGS = {'gain_percent': 'gain_percent',
                      'center_mhz': 'center_mhz'}

    def __init__(self, config_values=None):
        gr.top_block.__init__(self, "FM Video Receiver", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("FM Video Receiver")
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

        self.settings = Qt.QSettings("GNU Radio", "fmVideoReceiver")
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
        self.profile = PROFILES.get(values.get('profile'),
                                    PROFILES[DEFAULT_PROFILE])
        self.standard = STANDARDS.get(values.get('video_format'), NTSC)
        self.video_rate = VIDEO_RATES[self.standard.key]
        self.center_mhz = float(values.get('center_mhz',
                                           self.profile.default_mhz or 5800.0))
        self.gain_percent = float(values.get('gain_percent', 60))
        self.usrp_ip = values.get('ipXmitAddr', '')
        self.deviation_pp = float(values.get('deviation_pp')
                                  or self.profile.deviation_pp)
        self.preemphasis_key = preemphasis_for(
            values.get('preemphasis', self.profile.preemphasis),
            self.standard.lines)
        self.samp_rate = RF_RATE
        self.want_sound = bool(values.get('sound', True))
        self.volume = 0.5
        self.sounds = []
        self._last_decoded = 0
        self._last_time = None
        self._frame_rate = 0.0

        self._build_controls()
        self._build_flowgraph()
        self._build_readout()
        self._build_displays()

        self.status_timer = Qt.QTimer(self)
        self.status_timer.timeout.connect(self.refresh)
        self.status_timer.start(500)

    # ------------------------------------------------------------------ UI
    def _build_controls(self):
        row = Qt.QHBoxLayout()
        row.addWidget(Qt.QLabel("Channel:"))
        self.channel_combo = Qt.QComboBox()
        self.channel_combo.addItem("-", None)
        for name, centre, _caption in self.profile.channels:
            self.channel_combo.addItem(f"{name} ({centre:g})", centre)
        self.channel_combo.currentIndexChanged.connect(self._channel_picked)
        row.addWidget(self.channel_combo)

        row.addWidget(Qt.QLabel("MHz:"))
        self.freq_spin = Qt.QDoubleSpinBox()
        self.freq_spin.setDecimals(FREQ_DECIMALS)
        self.freq_spin.setSingleStep(0.1)
        self.freq_spin.setRange(FREQ_MIN_MHZ, FREQ_MAX_MHZ)
        self.freq_spin.setValue(self.center_mhz)
        self.freq_spin.valueChanged.connect(self.set_center)
        row.addWidget(self.freq_spin)

        row.addSpacing(12)
        row.addWidget(Qt.QLabel("RF Gain:"))
        self.gain_slider = Qt.QSlider(QtCore.Qt.Horizontal)
        self.gain_slider.setRange(0, 100)
        self.gain_slider.setValue(int(self.gain_percent))
        self.gain_slider.setMinimumWidth(110)
        self.gain_slider.valueChanged.connect(self.set_gain)
        row.addWidget(self.gain_slider)
        self.gain_value = Qt.QLabel(f"{int(self.gain_percent)}%")
        row.addWidget(self.gain_value)

        # Deviation and pre-emphasis are here rather than only in the dialog
        # because they are how an unknown transmitter gets matched: change
        # one, watch what Measured says, without losing the signal.
        row.addSpacing(12)
        row.addWidget(Qt.QLabel("Deviation:"))
        self.dev_spin = Qt.QDoubleSpinBox()
        self.dev_spin.setDecimals(2)
        self.dev_spin.setSingleStep(0.25)
        self.dev_spin.setRange(DEVIATION_MIN_MHZ, DEVIATION_MAX_MHZ)
        self.dev_spin.setValue(self.deviation_pp / 1e6)
        self.dev_spin.setSuffix(" MHz")
        self.dev_spin.valueChanged.connect(self.set_deviation)
        row.addWidget(self.dev_spin)

        row.addWidget(Qt.QLabel("Pre-emph:"))
        self.preemph_combo = Qt.QComboBox()
        for key, label in PREEMPHASIS_CHOICES:
            self.preemph_combo.addItem(label, key)
        self.preemph_combo.setCurrentIndex(
            max(self.preemph_combo.findData(self.preemphasis_key), 0))
        self.preemph_combo.activated.connect(
            lambda _i: self.set_preemphasis(self.preemph_combo.currentData()))
        row.addWidget(self.preemph_combo)

        if self.want_sound:
            row.addSpacing(12)
            self.mute_btn = Qt.QPushButton("Mute")
            self.mute_btn.setCheckable(True)
            self.mute_btn.setMaximumWidth(70)
            self.mute_btn.toggled.connect(self.set_muted)
            row.addWidget(self.mute_btn)
            self.volume_slider = Qt.QSlider(QtCore.Qt.Horizontal)
            self.volume_slider.setRange(0, 100)
            self.volume_slider.setValue(int(self.volume * 100))
            self.volume_slider.setMinimumWidth(80)
            self.volume_slider.valueChanged.connect(self.set_volume)
            row.addWidget(self.volume_slider)

        row.addStretch(1)
        self.watch_btn = Qt.QPushButton("Watch")
        self.watch_btn.setToolTip(
            "Hand the decoded pictures to a media player. An analog link "
            "has no transport stream to pass on, so what goes across is "
            "raw frames.")
        self.watch_btn.clicked.connect(self.toggle_watch)
        row.addWidget(self.watch_btn)
        self.clear_btn = Qt.QPushButton("Clear Statistics")
        self.clear_btn.clicked.connect(self.clear_stats)
        row.addWidget(self.clear_btn)

        holder = Qt.QWidget()
        holder.setLayout(row)
        self.top_grid_layout.addWidget(holder, 0, 0, 1, 10)
        self._sync_channel_combo()

    def _build_readout(self):
        big = Qt.QFont()
        big.setPointSize(14)
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

        def box(title, columns=2):
            group = Qt.QGroupBox(title)
            grid = Qt.QGridLayout()
            group.setLayout(grid)
            for c in range(columns):
                grid.setColumnStretch(c * 2 + 1, 1)
            grid.setHorizontalSpacing(12)
            return group, grid

        signal, sg = box("Signal")
        field(sg, 'lock', "Status", 0, 0, big, span=3)
        field(sg, 'level', "Input Level", 1, 0)
        field(sg, 'cnr', "Carrier / Noise", 1, 1)
        field(sg, 'clicks', "Clicks", 2, 0)
        field(sg, 'levels', "Sync / Blanking", 2, 1)
        self.top_grid_layout.addWidget(signal, 1, 0, 1, 5)

        picture, pg = box("Picture")
        field(pg, 'format', "Format", 0, 0, span=3)
        field(pg, 'line_rate', "Line Rate", 1, 0)
        field(pg, 'frames', "Frames Decoded", 1, 1)
        field(pg, 'dropped', "Dropped / Failed", 2, 0)
        field(pg, 'colour', "Colour", 2, 1)
        self.top_grid_layout.addWidget(picture, 1, 5, 1, 5)

        # What this receiver exists for: the numbers the FPV profile guesses
        # at, read off the signal against levels the standard fixes.
        measured, mg = box("Measured")
        field(mg, 'deviation', "Video Deviation", 0, 0)
        field(mg, 'offset', "Carrier Offset", 0, 1)
        field(mg, 'response', "Response at Subcarrier", 1, 0, span=3)
        self.top_grid_layout.addWidget(measured, 2, 0, 1, 5)

        sound, sdg = box("Sound")
        for k, freq in enumerate(self.profile.subcarriers):
            field(sdg, f'sc{k}', f"{freq / 1e6:g} MHz", k, 0)
            field(sdg, f'sd{k}', "Deviation", k, 1)
        if not self.profile.subcarriers:
            field(sdg, 'sc_none', "Sound", 0, 0, span=3)
            self.lbl['sc_none'].setText("this standard carries none")
        self.top_grid_layout.addWidget(sound, 2, 5, 1, 5)

        self.action_note = Qt.QLabel("")
        self.top_grid_layout.addWidget(self.action_note, 13, 0, 1, 10)
        for r in (0, 1, 2, 13):
            self.top_grid_layout.setRowStretch(r, 0)

    def _build_displays(self):
        """Two spectra and the composite - the instruments, not decoration.

        The **RF spectrum** is where the signal is and how wide, on the same
        20 MHz span and the same centre as the transmitter's own plot, so
        the two windows can be read side by side.

        The **baseband spectrum** is the one an FM video link needs and the
        transmitter has no equivalent of. It is what the discriminator
        recovers before de-emphasis, so the picture's own spectrum, the
        colour subcarrier and every *sound* subcarrier appear at their real
        baseband frequencies - which is how you find out where an unknown
        transmitter actually puts its sound, rather than assuming.

        The **composite** below them is the waveform itself: sync, porches,
        burst and picture, drawn exactly as the transmitter draws what it is
        sending.
        """
        self.rf_spectrum = qtgui.freq_sink_c(
            2048, window.WIN_BLACKMAN_hARRIS, self.center_mhz * 1e6,
            self.samp_rate, 'RF Spectrum', 1, None)
        for sink in (self.rf_spectrum,):
            sink.set_update_time(0.10)
            sink.set_y_axis(*SPECTRUM_Y_AXIS)
            sink.set_y_label('Relative Gain', 'dB')
            sink.enable_autoscale(False)
            sink.enable_grid(True)
            sink.set_fft_average(0.2)
            sink.enable_axis_labels(True)
            sink.enable_control_panel(False)
            sink.disable_legend()
        widget = sip.wrapinstance(self.rf_spectrum.qwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(widget, 3, 0, 5, 5)
        self.connect(self.radio_source, self.rf_spectrum)

        self.baseband_spectrum = qtgui.freq_sink_f(
            2048, window.WIN_BLACKMAN_hARRIS, 0.0, self.samp_rate,
            'Recovered Baseband - picture, colour subcarrier, and the sound '
            'above them', 1, None)
        self.baseband_spectrum.set_update_time(0.10)
        self.baseband_spectrum.set_y_axis(-140, 10)
        self.baseband_spectrum.set_y_label('Relative Gain', 'dB')
        self.baseband_spectrum.enable_autoscale(False)
        self.baseband_spectrum.enable_grid(True)
        self.baseband_spectrum.set_fft_average(0.2)
        self.baseband_spectrum.enable_axis_labels(True)
        self.baseband_spectrum.enable_control_panel(False)
        self.baseband_spectrum.disable_legend()
        # A real signal's spectrum is its own mirror image, so half the plot
        # would be the other half backwards.
        self.baseband_spectrum.set_plot_pos_half(True)
        widget = sip.wrapinstance(self.baseband_spectrum.qwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(widget, 3, 5, 5, 5)
        self.connect((self.demod, 1), self.baseband_spectrum)

        self.composite_sink = qtgui.time_sink_f(
            int(round(2.2 * self.standard.line * self.video_rate)),
            self.video_rate, 'Recovered Composite Video', 1, None)
        self.composite_sink.set_update_time(0.05)
        self.composite_sink.set_y_axis(-0.2, 1.2)
        self.composite_sink.set_y_label('Composite', "")
        self.composite_sink.enable_grid(True)
        self.composite_sink.enable_autoscale(False)
        self.composite_sink.enable_control_panel(False)
        self.composite_sink.disable_legend()
        widget = sip.wrapinstance(self.composite_sink.qwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(widget, 8, 0, 5, 10)
        self.connect((self.demod, 0), self.composite_sink)
        for r in range(3, 13):
            self.top_grid_layout.setRowStretch(r, 1)

    # ----------------------------------------------------------- flowgraph
    def _build_flowgraph(self):
        # **No local-oscillator offset, unlike the NTSC receiver.** That one
        # tunes 6 MHz above its channel so the radio's own leakage falls
        # outside a 6 MHz channel in a 20 MHz window. FM video has no such
        # room: the signal is 9 to 15 MHz wide and the radio's 20 MS/s is
        # all of it, so the carrier has to sit in the middle.
        tuned = self.center_mhz * 1e6
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

        self.demod = FmVideoDemod(self.profile, self.standard, self.samp_rate,
                                  self.deviation_pp, self.preemphasis_key)
        self.frames = FmVideoFrameSink(self.video_rate, self.standard,
                                       self.deviation_pp, self.demod.dc_gain())
        self.connect(self.radio_source, self.demod)
        self.connect((self.demod, 0), self.frames)

        self.level_probe = blocks.probe_signal_f()
        self.rms = blocks.rms_cf(0.01)
        self.connect(self.radio_source, self.rms, self.level_probe)

        self.quality, self.click_comparator = link_quality_chain(
            self, self.radio_source, (self.demod, 1),
            click_level(self.profile, self.deviation_pp), self.samp_rate)

        if self.want_sound:
            self._build_sound()

    def _build_sound(self):
        """Every subcarrier the standard has, out of the speakers.

        FPV puts the left channel on 6.0 MHz and the right on 6.5, so two
        subcarriers are played as the stereo pair they are. The transmitter
        here sends the same sound on both, which comes out as mono - a real
        transmitter with two channels does not.

        A missing audio device must not take the picture down, so a failure
        turns the sound off and says so.
        """
        if not self.profile.subcarriers:
            self.want_sound = False
            return
        self.sounds = [FmVideoSound(self.profile, freq, self.samp_rate,
                                    AUDIO_RATE, self.volume)
                       for freq in self.profile.subcarriers]
        try:
            self.audio_out = audio.sink(AUDIO_RATE, '', True)
        except Exception as exc:
            print(f"FM video receiver: no audio output ({exc})",
                  file=sys.stderr)
            self.want_sound = False
            self.sounds = []
            return
        for k, sound in enumerate(self.sounds):
            self.connect((self.demod, 1), sound)
            self.connect(sound, (self.audio_out, k))

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

    def set_deviation(self, mhz):
        self.deviation_pp = float(mhz) * 1e6
        self.demod.set_deviation(self.deviation_pp)
        self.frames.deviation_pp = self.deviation_pp
        level = click_level(self.profile, self.deviation_pp)
        self.click_comparator.set_lo(level)
        self.click_comparator.set_hi(level)

    def set_preemphasis(self, key):
        self.preemphasis_key = self.demod.set_preemphasis(key)
        self.frames.dc_gain = self.demod.dc_gain()
        index = self.preemph_combo.findData(self.preemphasis_key)
        if index >= 0 and index != self.preemph_combo.currentIndex():
            self.preemph_combo.blockSignals(True)
            self.preemph_combo.setCurrentIndex(index)
            self.preemph_combo.blockSignals(False)

    def set_volume(self, percent):
        self.volume = max(0.0, min(100.0, float(percent))) / 100.0
        if not self._muted():
            for sound in self.sounds:
                sound.set_volume(self.volume)

    def _muted(self):
        return hasattr(self, 'mute_btn') and self.mute_btn.isChecked()

    def set_muted(self, muted):
        # The chains keep running while muted, so both meters go on reading.
        for sound in self.sounds:
            sound.set_volume(0.0 if muted else self.volume)
        if hasattr(self, 'mute_btn'):
            self.mute_btn.setText("Unmute" if muted else "Mute")

    def _channel_picked(self, _index):
        centre = self.channel_combo.currentData()
        if centre is not None:
            self.set_center(float(centre))

    def _sync_channel_combo(self):
        index = 0
        for i in range(1, self.channel_combo.count()):
            data = self.channel_combo.itemData(i)
            if data is not None and abs(float(data) - self.center_mhz) < 0.05:
                index = i
                break
        self.channel_combo.blockSignals(True)
        self.channel_combo.setCurrentIndex(index)
        self.channel_combo.blockSignals(False)

    def set_center(self, mhz):
        mhz = float(mhz)
        if abs(mhz - self.center_mhz) < 1e-6:
            return
        self.center_mhz = mhz
        tuned = mhz * 1e6
        if self.radio_type == 'usrp':
            self.radio_source.set_center_freq(tuned, 0)
        elif self.radio_type == 'bb60':
            self.radio_source.set_center_freq(tuned)
        else:
            self.radio_source.set_frequency(0, tuned)
        self.rf_spectrum.set_frequency_range(tuned, self.samp_rate)
        self.freq_spin.blockSignals(True)
        self.freq_spin.setValue(mhz)
        self.freq_spin.blockSignals(False)
        self._sync_channel_combo()
        self.clear_stats()

    def clear_stats(self):
        self.frames.frames_decoded = 0
        self.frames.frames_dropped = 0
        self.frames.failures = 0
        self.quality.clear()
        self._last_decoded = 0
        self._last_time = None
        self._frame_rate = 0.0

    def toggle_watch(self):
        if self.frames.player_alive():
            self.frames.stop_player()
            self.watch_btn.setText("Watch")
            self.action_note.setText("")
            return
        std = self.standard
        # The rate pictures actually arrive at - one per buffer of
        # BUFFER_FRAMES frames - not the standard's own frame rate.
        argv = find_player(std.width, 2 * std.active_lines_per_field,
                           1.0 / (std.frame * self.frames.BUFFER_FRAMES),
                           'FM Video')
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
        std = self.standard
        rms = self.level_probe.level()
        self.lbl['level'].setText(
            f"{20 * np.log10(rms):.1f} dBFS" if rms > 0 else "-")

        # **No carrier at all is not a low carrier-to-noise.** The moments
        # of pure complex noise satisfy m4 = 2 m2^2 exactly, so the carrier
        # term comes out zero rather than small, and there is no ratio to
        # quote. Every sample of noise is also a phase jump, so the click
        # count on an empty channel is the sample rate - 190,012 a frame,
        # measured, which is arithmetic rather than information. Say what is
        # actually the case instead.
        cnr = self.quality.cnr_db
        seconds = self.quality.samples / self.samp_rate
        per_frame = (self.quality.clicks / (seconds / std.frame)
                     if seconds > std.frame else 0.0)
        if seconds < 0.5:
            self.lbl['cnr'].setText("-")
            self.lbl['clicks'].setText("-")
        elif cnr is None:
            self.lbl['cnr'].setText("no carrier")
            self.lbl['clicks'].setText("-")
        else:
            self.lbl['cnr'].setText(f"{cnr:.1f} dB in "
                                    f"{self.samp_rate / 1e6:g} MHz")
            self.lbl['clicks'].setText(
                "none" if self.quality.clicks == 0 else
                f"{self.quality.clicks:,}  ({per_frame:.1f} a frame)")

        status = self.frames.status
        decoded = self.frames.frames_decoded
        # Only update the rate over a decent interval: called twice in quick
        # succession, a sub-millisecond gap reads as 0 frames a second.
        if self._last_time is None:
            self._last_decoded, self._last_time = decoded, now
        elif now - self._last_time >= 0.4:
            self._frame_rate = ((decoded - self._last_decoded)
                                / (now - self._last_time))
            self._last_decoded, self._last_time = decoded, now
        rate = self._frame_rate

        line_rate = status.get('line_rate', 0.0)
        mismatch = format_mismatch(line_rate, std)
        if line_rate:
            ppm = (line_rate - std.line_rate) / std.line_rate * 1e6
            self.lbl['line_rate'].setText(
                f"{line_rate:,.1f} Hz  ({ppm:+,.0f} ppm)" if abs(ppm) < 5000
                else f"{line_rate:,.1f} Hz")
        else:
            self.lbl['line_rate'].setText("-")
        self.lbl['format'].setText(
            f"{std.key.upper()}, decoding at {self.video_rate / 1e6:g} MS/s"
            if not mismatch else
            f"set to {std.key.upper()} - but this is {mismatch.upper()}")

        if 'sync' in status and 'blank' in status:
            self.lbl['levels'].setText(
                f"{status['sync']:.3f} / {status['blank']:.3f}")
        else:
            self.lbl['levels'].setText("-")

        ceiling = 1.0 / (std.frame * self.frames.BUFFER_FRAMES)
        self.lbl['frames'].setText(
            f"{decoded:,}  ({rate:.1f}/s of {ceiling:.1f})" if decoded
            else "none")
        self.lbl['dropped'].setText(
            f"{self.frames.frames_dropped} / {self.frames.failures}")

        frame = self.frames.frame
        if frame is not None:
            saturation = float(np.abs(
                frame - frame.mean(axis=2, keepdims=True)).mean())
            self.lbl['colour'].setText(
                "colour" if saturation > 0.02 else "monochrome")
        else:
            self.lbl['colour'].setText("-")

        self._refresh_measured(status)
        self._refresh_sound()

        text, colour = self._status(decoded, rate, ceiling, cnr, per_frame,
                                    mismatch)
        self.lbl['lock'].setText(text)
        self.lbl['lock'].setStyleSheet(f"color: {colour};")

        if not self.frames.player_alive() and self.watch_btn.text() != "Watch":
            self.frames.stop_player()
            self.watch_btn.setText("Watch")
            self.action_note.setText("")

    def _refresh_measured(self, status):
        deviation = status.get('deviation_pp')
        if deviation:
            # Against what this receiver was told to expect, which is the
            # comparison that matters when the expectation is a guess.
            error = (deviation - self.deviation_pp) / self.deviation_pp * 100
            self.lbl['deviation'].setText(
                f"{deviation / 1e6:.2f} MHz p-p  ({error:+.1f}% of set)")
        else:
            self.lbl['deviation'].setText("-")

        offset = status.get('carrier_offset')
        if offset is not None and self.center_mhz > 0:
            ppm = offset / (self.center_mhz * 1e6) * 1e6
            self.lbl['offset'].setText(
                f"{offset / 1e3:+,.0f} kHz  ({ppm:+.1f} ppm)")
        else:
            self.lbl['offset'].setText("-")

        burst = status.get('burst')
        if burst is None or burst <= 0.01:
            self.lbl['response'].setText(
                "no colour burst - monochrome, or no colour arriving")
        else:
            db = 20 * np.log10(burst)
            curve = preemphasis(self.preemphasis_key)
            taking = (f"taking out {curve.label}" if curve
                      else "taking out no pre-emphasis")
            self.lbl['response'].setText(
                f"{db:+.2f} dB at {self.standard.subcarrier / 1e6:.2f} MHz "
                f"against DC, {taking}")

    def _refresh_sound(self):
        """Each subcarrier's level and how hard it is being modulated.

        **An empty band demodulates to something, and it is not sound.** A
        real FPV transmitter with no microphone sends no subcarriers at all;
        pointed at one, the 6.0 and 6.5 MHz chains read -52 dBc and 30 kHz
        rms, which on the face of it is a subcarrier carrying loud
        programme. Both numbers were the noise in an empty band. So the
        level decides whether there is anything there, and the deviation is
        only shown when there is.
        """
        for k, sound in enumerate(self.sounds):
            dbc = sound.sidebands_dbc(self.deviation_pp)
            if dbc is None or not np.isfinite(dbc):
                self.lbl[f'sc{k}'].setText("-")
                self.lbl[f'sd{k}'].setText("-")
                continue
            if dbc < NO_SUBCARRIER_DBC:
                self.lbl[f'sc{k}'].setText(f"nothing here ({dbc:.0f} dBc)")
                self.lbl[f'sd{k}'].setText("-")
                continue
            self.lbl[f'sc{k}'].setText(
                f"{dbc:.1f} dBc (sent {self.profile.subcarrier_dbc:g})")
            deviation = sound.deviation_hz()
            self.lbl[f'sd{k}'].setText(
                f"{deviation / 1e3:.1f} kHz rms" if deviation > SILENT_HZ
                else "silent")

    def _status(self, decoded, rate, ceiling, cnr, clicks_per_frame, mismatch):
        if mismatch:
            return (f"This is {mismatch.upper()} - reopen with Format set to "
                    f"{mismatch.upper()}", self.WARN)
        if not decoded:
            return ("No carrier on this channel" if cnr is None else
                    "Carrier, but no picture - nothing is syncing on it",
                    self.BAD)
        if clicks_per_frame > 10:
            return (f"Below FM threshold - {clicks_per_frame:.0f} clicks a "
                    "frame, the picture is breaking up", self.BAD)
        if clicks_per_frame > 0.5:
            return (f"Weak - {clicks_per_frame:.1f} clicks a frame", self.WARN)
        if cnr is not None and cnr < 12:
            return (f"Near FM threshold - {cnr:.0f} dB carrier to noise",
                    self.WARN)
        if rate < 0.7 * ceiling:
            return (f"Picture, but only {rate:.0f} frames a second",
                    self.WARN)
        return ("Locked - picture decoding", self.GOOD)

    def closeEvent(self, event):
        self.settings = Qt.QSettings("GNU Radio", "fmVideoReceiver")
        self.settings.setValue("geometry", self.saveGeometry())
        self.status_timer.stop()
        self.stop()
        self.wait()
        event.accept()


def main(top_block_cls=fmVideoReceiver, options=None, app=None,
         config_values=None):
    own_app = app is None
    if own_app:
        app = Qt.QApplication(sys.argv)

    tb = top_block_cls(config_values)
    tb.start()
    tb.apply_gain()
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

    # Inside the launcher its event loop is already running: hand the window
    # back for it to watch rather than starting a loop of our own.
    if not own_app:
        return tb
    return app.exec_()


if __name__ == '__main__':
    main()
