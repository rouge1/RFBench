"""Analogue broadcast television systems: the radiated signal, as a table.

``ntsc_encode.VideoStandard`` describes the *composite* signal - lines,
timing, colour - and is all the FM video apps need. A broadcast transmitter
then puts that composite on a carrier as vestigial-sideband AM with an FM
sound carrier beside it, and *that* is where the systems differ again: the
channel width, where the vision carrier sits in it, how much of the lower
sideband is left, how far up the sound is, how deep the modulation goes.
ITU-R BT.1701-1 Table 1 (``docs/itu/BT.1701-1-200508-I.pdf``) gives every
one of them, system by system, and this module is that table plus the three
blocks that use it:

- ``VsbModulator`` - composite in, the vision carrier out, at baseband;
- ``VsbDemod`` - baseband in, composite out, the way a television detects it;
- ``AuralSound`` - the FM sound carrier, demodulated to 48 kHz.

System M is here as well as the PAL ones. ``ntscAnalogVideoRecorded`` and
``ntscReceiver`` still carry their own copies of these blocks, untouched;
``scripts/test_pal_transmit.py`` checks that ``VsbModulator`` on System M
puts out what ``NtscModulator`` does, sample for sample to float32 rounding, which is
the proof that the table reads the same numbers the NTSC apps were built
and tested on.

Where the carriers are is given relative to the channel *centre*, which is
what ``cf`` means in every television app here.
"""

from dataclasses import dataclass
from fractions import Fraction

import numpy as np  # type: ignore
from gnuradio import analog, blocks, filter, gr  # type: ignore
from gnuradio.fft import window  # type: ignore
from gnuradio.filter import firdes  # type: ignore

from apps.ntsc_encode import NTSC, PAL, VideoStandard

#: Every system puts the vision carrier this far above the channel's lower
#: edge (Table 1 item 3: "nearest edge of channel relative to vision
#: carrier", -1.25 MHz in all eight columns).
VISION_ABOVE_EDGE = 1.25e6

#: The vestigial-sideband filter's transition, centred on each edge. The
#: NTSC transmitter's value; Table 1 item 6 then asks for 20 dB down
#: 0.5 MHz past the nominal vestige (B, G, M), which this reaches easily.
VSB_TRANSITION = 300e3

#: Leave headroom: radios take 1.0 as full scale and clip above it.
BASEBAND_SCALE = 0.85

#: Never let the carrier reach zero: a transmitter that switches its
#: carrier off has nothing for a receiver's sync or AGC to hold on to.
CARRIER_FLOOR = 0.02

AUDIO_RATE = 48000
#: The sound carrier is brought down to this before it is demodulated.
SOUND_IF_RATE = 200e3
#: Highest programme frequency on the sound carrier, for Carson's rule.
AUDIO_TOP = 15e3


@dataclass(frozen=True)
class TvSystem:
    """One column of BT.1701 Table 1, plus the rates this bench runs it at."""
    key: str
    label: str
    #: The composite standard it carries (lines, timing, colour).
    standard: VideoStandard
    #: Item 1: nominal channel bandwidth.
    channel_bw: float
    #: Item 2: sound carrier above the vision carrier.
    aural_spacing: float
    #: Item 4: nominal width of the main (upper) sideband.
    main_sideband: float
    #: Item 5: nominal width of the vestigial (lower) sideband.
    vestige: float
    #: Item 8: carrier at peak white, as a fraction of peak (sync) carrier.
    #: Sync is 100% in every negative-modulation system.
    carrier_at_white: float
    #: Item 10: peak deviation of the FM sound.
    deviation: float
    #: Item 11: sound pre-emphasis time constant.
    preemphasis: float
    #: Item 12: vision/sound power ratio, as the sound carrier's *voltage*
    #: against peak vision.
    aural_amplitude: float
    #: The rate the composite is built and decoded at. NTSC's 10 MS/s will
    #: not do for PAL: its chroma reaches 5.7 MHz and would fold back on
    #: itself (knowledge/fm-video.md), so PAL runs at 12.5, as the FM video
    #: apps do.
    composite_rate: float
    #: How far above the channel centre the radio is tuned, so its own LO
    #: leak lands outside the channel rather than on the picture.
    lo_offset: float
    carrier_at_sync: float = 1.0

    # -- where things are, relative to the channel centre -----------------

    @property
    def visual_carrier(self):
        return -self.channel_bw / 2 + VISION_ABOVE_EDGE

    @property
    def aural_carrier(self):
        return self.visual_carrier + self.aural_spacing

    # -- the vestigial-sideband filter ------------------------------------
    #
    # Composite is real, so its spectrum is symmetric. Mixed to ``vsb_mix``
    # and low-passed at ``vsb_cutoff``, it keeps ``vestige`` below the
    # carrier and ``main_sideband`` above it; a second mixer then moves it
    # the last ``vsb_trim`` onto the vision carrier. For System M that is
    # -1.725 MHz, 2.475 MHz and -25 kHz - the NTSC transmitter's own
    # constants, which these reproduce exactly.

    @property
    def vsb_cutoff(self):
        return (self.main_sideband + self.vestige) / 2

    @property
    def vsb_mix(self):
        return -(self.main_sideband - self.vsb_cutoff)

    @property
    def vsb_trim(self):
        return self.visual_carrier - self.vsb_mix

    # -- the sound --------------------------------------------------------

    @property
    def sound_bandwidth(self):
        """Carson's rule: 2 x (deviation + highest audio frequency)."""
        return 2 * (self.deviation + AUDIO_TOP)

    def blanking_carrier(self):
        """Where blanking lands on the carrier - Table 1 item 8's check."""
        std = self.standard
        v = std.blank
        return self.carrier_at_sync - (self.carrier_at_sync
                                       - self.carrier_at_white) * v


#: US System M, as ``ntscAnalogVideoRecorded`` sends it. Its white is the
#: FCC's 12.5% (73.682), inside BT.1701's 10-15; its sound 10% of peak
#: vision power, inside 10/1 to 5/1.
SYSTEM_M = TvSystem(
    key='M', label="System M - NTSC, 6 MHz (US)", standard=NTSC,
    channel_bw=6e6, aural_spacing=4.5e6, main_sideband=4.2e6, vestige=0.75e6,
    carrier_at_white=0.125, deviation=25e3, preemphasis=75e-6,
    aural_amplitude=0.316, composite_rate=10e6, lo_offset=6e6)

#: System B: PAL on VHF, 7 MHz channels. Peak white 10-15% (item 8) - 12.5,
#: which puts blanking at 73.75% against 75 +- 2.5. A PAL composite has
#: blanking at 30% of sync-to-white where NTSC's is 28.6%, so blanking and
#: white cannot both be nominal on a straight line from sync; both are
#: inside their tolerances. Sound 10/1, the loud end of B/G's 20/1 to 10/1.
SYSTEM_B = TvSystem(
    key='B', label="System B - PAL, 7 MHz (Europe, VHF)", standard=PAL,
    channel_bw=7e6, aural_spacing=5.5e6, main_sideband=5.0e6, vestige=0.75e6,
    carrier_at_white=0.125, deviation=50e3, preemphasis=50e-6,
    aural_amplitude=0.316, composite_rate=12.5e6, lo_offset=4.5e6)

#: System G: System B in an 8 MHz UHF channel - every other figure the same.
SYSTEM_G = TvSystem(
    key='G', label="System G - PAL, 8 MHz (Europe, UHF)", standard=PAL,
    channel_bw=8e6, aural_spacing=5.5e6, main_sideband=5.0e6, vestige=0.75e6,
    carrier_at_white=0.125, deviation=50e3, preemphasis=50e-6,
    aural_amplitude=0.316, composite_rate=12.5e6, lo_offset=4.5e6)

#: System I: PAL in the UK and Ireland. Sound at 5.9996 MHz (item 2, with
#: note 7's tolerance), a wider vestige and main sideband, and shallower
#: modulation - white at 20 +- 2% and blanking at 76 +- 2, which on PAL's
#: composite both land on exactly. Sound 10/1, the ratio note 24 gives.
SYSTEM_I = TvSystem(
    key='I', label="System I - PAL, 8 MHz (UK, Ireland)", standard=PAL,
    channel_bw=8e6, aural_spacing=5.9996e6, main_sideband=5.5e6,
    vestige=1.25e6, carrier_at_white=0.20, deviation=50e3, preemphasis=50e-6,
    aural_amplitude=0.316, composite_rate=12.5e6, lo_offset=4.5e6)

SYSTEMS = {s.key: s for s in (SYSTEM_M, SYSTEM_B, SYSTEM_G, SYSTEM_I)}

#: What the PAL apps offer. "B/G" is one choice that becomes B or G by the
#: channel's width - which is how the two are defined: the same signal, in
#: 7 MHz VHF channels or 8 MHz UHF ones.
PAL_CHOICES = (('BG', "PAL B/G - Europe (B on VHF, G on UHF)"),
               ('I', "PAL I - UK and Ireland"))
#: Below this a B/G channel is a 7 MHz System B one.
BG_VHF_TOP_MHZ = 300.0


def pal_system(choice, centre_mhz):
    """The TvSystem for a PAL choice ('BG' or 'I') on a channel centre."""
    if choice == 'I':
        return SYSTEM_I
    return SYSTEM_B if centre_mhz < BG_VHF_TOP_MHZ else SYSTEM_G


# --- the European channel plan ---------------------------------------------
#
# CCIR channels: E2-E4 in Band I and E5-E12 in Band III, 7 MHz wide (System
# B), and E21-E69 in Bands IV and V, 8 MHz (G and I). Vision carriers at
# 48.25, 175.25 and 471.25 MHz on the first of each.

def pal_channels():
    """``[(name, centre MHz)]`` for E2-E12 and E21-E69."""
    out = []
    for n in range(2, 5):
        out.append((f"E{n}", 50.5 + 7.0 * (n - 2)))
    for n in range(5, 13):
        out.append((f"E{n}", 177.5 + 7.0 * (n - 5)))
    for n in range(21, 70):
        out.append((f"E{n}", 474.0 + 8.0 * (n - 21)))
    return out


def pal_channel_items():
    """The plan as ``(name, centre MHz, caption)``, for a FrequencyChooser."""
    return [(name, centre,
             f"{'VHF' if centre < BG_VHF_TOP_MHZ else 'UHF'} {name}  "
             f"({centre:g} MHz)")
            for name, centre in pal_channels()]


def pal_channel_centre(name):
    for n, centre in pal_channels():
        if n == name:
            return centre
    raise ValueError(f"{name} is not a European television channel")


def pal_channel_for_centre(mhz, tolerance=0.05):
    for n, centre in pal_channels():
        if abs(centre - mhz) <= tolerance:
            return n
    return None


# --- the blocks --------------------------------------------------------------

class VsbModulator(gr.hier_block2):
    """Composite video in (sync 0, white 1), the vision carrier out.

    Runs at the composite rate and puts the carrier at ``visual_carrier``
    from the channel centre - the NTSC transmitter's ``NtscModulator`` with
    its numbers taken from a ``TvSystem``. Negative modulation only: every
    system here is (Table 1 item 7, "C3F neg." for all but L).
    """

    def __init__(self, sample_rate, system, shift=0.0):
        gr.hier_block2.__init__(
            self, "vsb_modulator",
            gr.io_signature(1, 1, gr.sizeof_float),
            gr.io_signature(1, 1, gr.sizeof_gr_complex))
        self.sample_rate = float(sample_rate)
        self.system = system
        span = system.carrier_at_sync - system.carrier_at_white
        self.slope = blocks.multiply_const_ff(-span)
        self.offset = blocks.add_const_ff(system.carrier_at_sync)
        self.rail = analog.rail_ff(CARRIER_FLOOR, system.carrier_at_sync)
        self.to_complex = blocks.float_to_complex(1)
        self.zero = blocks.null_source(gr.sizeof_float)
        self.mix = blocks.multiply_vcc(1)
        self.carrier = analog.sig_source_c(self.sample_rate, analog.GR_COS_WAVE,
                                           system.vsb_mix, 1, 0, 0)
        self.vsb = filter.fft_filter_ccc(
            1, firdes.low_pass(1, self.sample_rate, system.vsb_cutoff,
                               VSB_TRANSITION, window.WIN_HAMMING, 6.76), 1)
        self.trim_mix = blocks.multiply_vcc(1)
        # ``shift`` moves the whole channel on the last mixer, which costs
        # nothing extra: VsbTransmitter uses it to put the channel where a
        # radio tuned ``lo_offset`` above its centre wants it.
        self.trim = analog.sig_source_c(self.sample_rate, analog.GR_COS_WAVE,
                                        system.vsb_trim + shift, 1, 0, 0)
        self.connect(self, self.slope, self.offset, self.rail,
                     (self.to_complex, 0))
        self.connect(self.zero, (self.to_complex, 1))
        self.connect(self.to_complex, (self.mix, 0))
        self.connect(self.carrier, (self.mix, 1))
        self.connect(self.mix, self.vsb, (self.trim_mix, 0))
        self.connect(self.trim, (self.trim_mix, 1))
        self.connect(self.trim_mix, self)


class VsbTransmitter(gr.hier_block2):
    """Composite and sound in, the radio's baseband out - the whole channel.

    Input 0 is composite (sync 0, white 1) at the system's composite rate;
    input 1 the programme sound at ``audio_rate``, full deviation at +-1.0,
    already pre-emphasised and railed. Out comes the channel at
    ``radio_rate``, already shifted down by the system's ``lo_offset`` so
    that a radio tuned that far above the channel centre puts it in place,
    and its own LO leak outside it. The app and
    ``scripts/test_pal_transmit.py`` both build exactly this, so the test
    checks what goes on the air.

    **Built for what it costs, which the first version did not survive.**
    That one modulated everything at PAL's 12.5 MS/s and resampled the
    complex sum to the radio's 20: the 8/5 complex resampler took a whole
    core by itself and set the pace of the chain, the audio's 3125/12
    resampler to 12.5 MS/s and the FM modulator took most of two more, and
    on worklaptop1's eight cores, beside the encoder's three threads, the
    VSG60 ran dry - a picture torn by white lines and scratchy sound, with
    the receiver's input dropping 20 dB whenever it did (measured
    2026-11-24, 6.8 cores and a load of 10.5). So:

    - **the picture is resampled while it is still real** - real samples
      and real taps, a quarter of the arithmetic of the complex sum - and
      modulated at the radio's rate;
    - **the sound is frequency modulated at 200 kS/s**, where the FM
      carrier (130 kHz by Carson's rule) fits with room to spare, and
      interpolated to the radio's rate with real taps;
    - **every carrier is set where the radio wants it in the first place**
      (``vsb_trim`` and the sound's rotator both include the LO offset),
      so there is no separate mixer for the whole channel.

    The sum is scaled once so that peak vision plus the sound carrier is
    ``BASEBAND_SCALE`` of full scale.
    """

    #: The sound's own rate: 200 kS/s divides both radio rates (20 and 25).
    SOUND_RATE = 200e3

    def __init__(self, system, radio_rate, audio_rate=AUDIO_RATE):
        gr.hier_block2.__init__(
            self, "vsb_transmitter",
            gr.io_signature(2, 2, gr.sizeof_float),
            gr.io_signature(1, 1, gr.sizeof_gr_complex))
        self.system = system
        rate = system.composite_rate
        self.radio_rate = float(radio_rate)
        self.audio_rate = float(audio_rate)
        shift = -system.lo_offset

        # Picture: composite up to the radio's rate as floats, then the VSB
        # modulator there. The composite's own band ends at 5.5 MHz (I's
        # main sideband), well inside the 12.5 MS/s Nyquist; the filter
        # passes it flat and stops before the images at 12.5 - 6.25 MHz.
        ratio = Fraction(self.radio_rate / rate).limit_denominator(1000)
        up, down = ratio.numerator, ratio.denominator
        self.video_resampler = filter.rational_resampler_fff(
            interpolation=up, decimation=down,
            taps=firdes.low_pass(up, up * rate, 5.9e6, 0.6e6,
                                 window.WIN_HAMMING),
            fractional_bw=0)
        self.modulator = VsbModulator(self.radio_rate, system, shift)

        # Sound: to 200 kS/s, FM there, then up to the radio's rate and
        # onto its carrier.
        a = Fraction(int(self.SOUND_RATE), int(self.audio_rate))
        self.audio_resampler = filter.rational_resampler_fff(
            interpolation=a.numerator, decimation=a.denominator,
            taps=[], fractional_bw=0)
        self.fm = analog.frequency_modulator_fc(
            2 * np.pi * system.deviation / self.SOUND_RATE)
        self.aural_level = blocks.multiply_const_cc(system.aural_amplitude)
        factor = int(round(self.radio_rate / self.SOUND_RATE))
        self.sound_interp = filter.interp_fir_filter_ccf(
            factor, firdes.low_pass(factor, self.radio_rate,
                                    system.sound_bandwidth / 2 + 15e3,
                                    self.SOUND_RATE / 2, window.WIN_HAMMING))
        self.aural_mix = blocks.rotator_cc(
            2 * np.pi * (system.aural_carrier + shift) / self.radio_rate)

        self.add = blocks.add_vcc(1)
        self.scale = blocks.multiply_const_cc(
            BASEBAND_SCALE / (system.carrier_at_sync + system.aural_amplitude))
        self.connect((self, 0), self.video_resampler, self.modulator,
                     (self.add, 0))
        self.connect((self, 1), self.audio_resampler, self.fm,
                     self.aural_level, self.sound_interp, self.aural_mix,
                     (self.add, 1))
        self.connect(self.add, self.scale, self)


#: The receiver's Nyquist slope runs this far either side of the vision
#: carrier. 0.75 MHz is the vestige B, G and M all transmit, and I's 1.25
#: covers it.
NYQUIST_SLOPE = 0.75e6
BAND_EDGE = 0.3e6
NYQUIST_TAPS = 257


def nyquist_slope_taps(sample_rate, system, ntaps=NYQUIST_TAPS):
    """A television IF's response, about the vision carrier at DC.

    Half at the carrier, rising to one ``NYQUIST_SLOPE`` above and falling
    to nothing that far below, so the two sidebands near the carrier add up
    to what one sideband gives higher up - and closed off past the main
    sideband, which is what keeps the sound carrier out of the picture
    detector. ``ntscReceiver.nyquist_slope_taps`` explains both at length;
    this is the same filter with the band edges taken from the system.
    """
    freqs = np.fft.fftfreq(ntaps, 1.0 / sample_rate)
    response = np.clip(0.5 + freqs / (2 * NYQUIST_SLOPE), 0.0, 1.0)
    band_low = -(NYQUIST_SLOPE + 0.5e6)
    band_high = system.main_sideband
    low = np.clip((freqs - band_low) / BAND_EDGE + 0.5, 0.0, 1.0)
    high = np.clip((band_high - freqs) / BAND_EDGE + 0.5, 0.0, 1.0)
    response *= 0.5 * (1 - np.cos(np.pi * low))
    response *= 0.5 * (1 - np.cos(np.pi * high))
    taps = np.fft.ifft(response)
    taps = np.roll(taps, ntaps // 2) * np.hamming(ntaps)
    return taps.astype(np.complex64)


class VsbDemod(gr.hier_block2):
    """Baseband from the radio in, composite video out at the system's rate.

    Vision carrier to DC, the Nyquist slope, a PLL locked to the carrier,
    the real part, inverted (negative modulation: sync is peak carrier),
    then down to the composite rate. That last step is a plain decimation
    when the rates divide (an N210's 25 MS/s to PAL's 12.5) and a rational
    resampler when they do not (20 to 12.5 is 5/8, as in the FM video
    receiver).
    """

    def __init__(self, sample_rate, system, center_offset=None):
        gr.hier_block2.__init__(
            self, "vsb_demod",
            gr.io_signature(1, 1, gr.sizeof_gr_complex),
            gr.io_signature(1, 1, gr.sizeof_float))
        self.sample_rate = float(sample_rate)
        self.system = system
        self.composite_rate = system.composite_rate
        offset = system.lo_offset if center_offset is None else center_offset
        self.rotator = blocks.rotator_cc(
            -2 * np.pi * (system.visual_carrier - offset) / self.sample_rate)
        self.slope = filter.fft_filter_ccc(
            1, nyquist_slope_taps(self.sample_rate, system).tolist(), 1)
        self.pll = analog.pll_carriertracking_cc(0.002, 0.05, -0.05)
        self.detector = blocks.complex_to_real(1)
        self.invert = blocks.multiply_const_ff(-1.0)
        video_top = system.main_sideband + 0.25e6
        ratio = Fraction(self.composite_rate / self.sample_rate
                         ).limit_denominator(1000)
        if ratio.numerator == 1:
            self.decimate = filter.fir_filter_fff(
                ratio.denominator,
                firdes.low_pass(1.0, self.sample_rate, video_top, 0.5e6))
        else:
            self.decimate = filter.rational_resampler_fff(
                interpolation=ratio.numerator, decimation=ratio.denominator,
                taps=[], fractional_bw=0.45)
        self.connect(self, self.rotator, self.slope, self.pll, self.detector,
                     self.invert, self.decimate, self)


class AuralSound(gr.hier_block2):
    """The FM sound carrier: baseband in, 48 kHz audio out.

    ``ntscReceiver.NtscSound`` with the carrier, the deviation, Carson's
    bandwidth and the de-emphasis taken from the system - 50 kHz and 50 us
    on PAL, against System M's 25 and 75. Two meters, as there: the carrier
    level says the sound is being sent, the deviation that something is on
    it.
    """

    def __init__(self, sample_rate, system, center_offset=None,
                 audio_rate=AUDIO_RATE, volume=0.5, tap=None):
        gr.hier_block2.__init__(
            self, "aural_sound",
            gr.io_signature(1, 1, gr.sizeof_gr_complex),
            gr.io_signature(1, 1, gr.sizeof_float))
        self.sample_rate = float(sample_rate)
        self.system = system
        offset = system.lo_offset if center_offset is None else center_offset
        decimation = max(1, int(round(self.sample_rate / SOUND_IF_RATE)))
        self.if_rate = self.sample_rate / decimation
        # Half Carson's bandwidth either side, and stopped before the
        # decimated Nyquist so nothing folds into it.
        half = system.sound_bandwidth / 2
        stop = 0.5 * self.if_rate
        self.channel = filter.freq_xlating_fir_filter_ccf(
            decimation,
            firdes.low_pass(1.0, self.sample_rate, half + 5e3,
                            max(10e3, stop - half - 5e3)),
            system.aural_carrier - offset, self.sample_rate)
        self.demod = analog.quadrature_demod_cf(
            self.if_rate / (2 * np.pi * system.deviation))
        self.audio_lpf = filter.fir_filter_fff(
            1, firdes.low_pass(1.0, self.if_rate, AUDIO_TOP, 4e3))
        ratio = Fraction(int(audio_rate),
                         int(round(self.if_rate))).limit_denominator(1000)
        self.resamp = filter.rational_resampler_fff(
            interpolation=ratio.numerator, decimation=ratio.denominator,
            taps=[], fractional_bw=0)
        self.deemph = analog.fm_deemph(float(audio_rate), system.preemphasis)
        self.gain = blocks.multiply_const_ff(float(volume))
        self.connect(self, self.channel, self.demod, self.audio_lpf,
                     self.resamp, self.deemph, self.gain, self)
        # ``tap`` (tv_watch.AudioTap) takes the sound before the volume, so
        # a synced player can have it while the speakers here are muted.
        self.tap = tap
        if tap is not None:
            self.tap_vector = blocks.stream_to_vector(gr.sizeof_float, tap.VLEN)
            self.connect(self.deemph, self.tap_vector, tap)
        self.carrier_rms = blocks.rms_cf(0.01)
        self.carrier_probe = blocks.probe_signal_f()
        self.connect(self.channel, self.carrier_rms, self.carrier_probe)
        self.deviation_rms = blocks.rms_ff(0.005)
        self.deviation_probe = blocks.probe_signal_f()
        self.connect(self.demod, self.deviation_rms, self.deviation_probe)

    def set_volume(self, value):
        self.gain.set_k(float(value))

    def carrier_level(self):
        rms = self.carrier_probe.level()
        return 20 * np.log10(rms) if rms > 0 else None

    def deviation_hz(self):
        return self.deviation_probe.level() * self.system.deviation
