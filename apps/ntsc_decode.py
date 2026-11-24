#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Composite video decoder, NTSC or PAL - the other half of ``ntsc_encode``.

Takes composite baseband samples and gives back pictures: finds sync, locks
the line and field structure, samples the active line into pixels, and
demodulates chroma against the colour burst.

It exists to *check* the encoder, the way ``rds_core`` checks ``rds_encode``.
``scripts/test_ntsc_loopback.py`` and ``scripts/test_pal_loopback.py`` run
frames through both and compare the picture that comes back with the one
that went in, so a timing or phase mistake shows up as a number rather than
as "the TV looked wrong". The receivers use it off the air too.

Free of GNU Radio and Qt, so it also runs against a recorded capture.

Three things worth knowing before changing it:

**Sync is found by pulse width, not by level.** Equalizing pulses, line
sync and the field-sync serrations all sit at exactly the same level, so a
threshold crossing says nothing about which is which. How long the signal
stays down is what separates the start of a field from the start of a line.

**Colour is measured against the burst, never against the sample clock.**
The burst is a few cycles of the subcarrier at a known phase, sent on every
line for exactly this purpose: it says where the subcarrier's zero crossing
is *on this line*, which is the only way to know what the chroma phase on
that line means. Decoding against a locally generated subcarrier instead
gets the hue wrong by however much the sampling origin happened to differ.

**PAL's burst says two things at once.** It sits 45 degrees either side of
the -U axis, the side swapping every line with the V switch. Averaged over
neighbouring lines the swing cancels and leaves the reference axis; each
line's own burst, measured against that, says which way its V was sent.
"""

import numpy as np

from apps.ntsc_encode import (
    ACTIVE_LEN, ACTIVE_START, B_Y_SCALE, BURST_AMPLITUDE, BURST_LEN,
    BURST_START, EQUALIZING_WIDTH, FSC, IRE_SETUP, IRE_SYNC, IRE_WHITE, KB,
    KG, KR, LINE, NTSC, ORDINARY, R_Y_SCALE, SETUP_GAIN, SYNC_WIDTH,
)

# A pulse longer than this is a horizontal sync rather than an equalizer.
# (NTSC's value; a decoder works out its own standard's the same way.)
SYNC_DISCRIMINANT = (EQUALIZING_WIDTH + SYNC_WIDTH) / 2
# Stay at sync level longer than this and it is a field-sync pulse.
VERTICAL_RUN = 10e-6

# Where to slice sync from vision, as a fraction of the way from sync tip to
# blanking. **Not the half-way point**, which is -20 IRE - and -20 IRE is
# exactly how far down a *legal* picture is allowed to swing, so saturated
# colour sat on that floor put false sync pulses in the middle of the
# active line. Since lines are numbered by counting pulses, one spurious
# pulse shifts every line after it and the picture comes out sheared. 0.35
# of the way up is -26 IRE, which leaves six IRE of daylight.
SYNC_SLICE = 0.35
# No sync pulse of any kind is shorter than an equalizing pulse's 2.3 us, so
# anything much shorter is ringing or noise crossing the slice level.
SYNC_MIN_WIDTH = 1.5e-6
#: How much averaging the sync separator does before slicing. Long enough to
#: bury the colour subcarrier and the noise, short enough to leave a 4.7 us
#: pulse its shape.
SYNC_SEPARATOR = 0.5e-6
#: Where `levels` reads its first estimates, as percentiles of the samples.
#: Sync tips are about 8% of all samples - 4.7 us of every line, plus the
#: field-sync blocks - and blanking-level samples (the porches, the burst
#: region, the blanked lines) about the next 16%, whatever the picture is
#: doing, because a legal picture never goes below -20 IRE. So the 4th
#: percentile sits inside the sync tips and the 20th inside blanking. They
#: are only first estimates: `decode_frame` refines both on the pulses.
SYNC_PERCENTILE = 4
BLANK_PERCENTILE = 20

# How many ordinary lines pass between the first one after a vertical block
# and the first line of active picture, for NTSC. They differ by one between
# the two fields - see decode_frame - which is what makes the interlace
# work. A decoder derives its own standard's from the half-line table.
FIELD1_LINE_OFFSET = 11         # block ends at line 9, picture starts at 21
FIELD2_LINE_OFFSET = 10         # block ends at line 272, picture starts at 283
# Annex A equation 10 again, as the decoder needs it: the encoder wrote
# 0.925*Y + 7.5 for luma and 0.925*100*(b-y, r-y) for chroma, in IRE.
CHROMA_GAIN = SETUP_GAIN * IRE_WHITE            # 92.5
LUMA_GAIN = SETUP_GAIN * IRE_WHITE              # 92.5, Y in 0..1


def _box_filter(a, span):
    """Row-wise moving average, matching ``np.convolve(row, box, 'same')``.

    A prefix sum rather than a convolution: the kernel is uniform, so every
    output is one subtraction, and it runs on the whole field of lines at
    once instead of once per line.
    """
    if span <= 1:
        return a.copy()
    n = a.shape[1]
    totals = np.concatenate(
        [np.zeros((a.shape[0], 1), dtype=a.dtype), np.cumsum(a, axis=1)],
        axis=1)
    # np.convolve's 'same' takes the middle of the full convolution, which
    # starts (span-1)//2 in.
    centre = np.arange(n) + (span - 1) // 2
    hi = np.minimum(n, centre + 1)
    lo = np.maximum(0, centre - span + 1)
    return (totals[:, hi] - totals[:, lo]) / span


def chroma_span(sample_rate, max_cycles=8, subcarrier=FSC):
    """Averaging length whose null lands on the colour subcarrier.

    Chroma is pulled out with a box filter, and a box of length L has its
    first null at ``sample_rate / L`` - so the length has to be chosen so
    that a null falls on the subcarrier, not merely near it.

    The obvious ``round(fs / fsc)`` only works when the sample rate happens
    to be a whole multiple of the subcarrier. At 4x subcarrier - which the
    loopback tests use - it is exact, and the colour comes back within 0.01.
    At 10 MS/s NTSC it gives 3 samples, whose null is at 3.333 MHz, 246 kHz
    adrift, and the error is fifteen times worse. Averaging over several
    subcarrier cycles instead lets the null land almost exactly: five cycles
    is 14 samples at 10 MS/s, which is 8 kHz out.

    The cost is horizontal chroma resolution, and neither standard has much
    to spare - but both already limit chroma to about 1.3 MHz, so a filter
    this wide is close to what the signal carries anyway.
    """
    best = None
    for cycles in range(1, max_cycles + 1):
        span = max(1, int(round(cycles * sample_rate / subcarrier)))
        error = abs(cycles * sample_rate / span - subcarrier)
        if best is None or error < best[0]:
            best = (error, span)
    return best[1]


def field_line_offsets(standard):
    """For each field: first active line minus the first ordinary line
    after its field-sync block, which is where `find_fields` starts counting.

    NTSC is 11 and 10; PAL is 17 and 17. Worked out from the standard's own
    half-line table rather than written down, so the two cannot disagree.
    """
    types = standard.half_line_types().reshape(standard.lines, 2)
    offsets = []
    for first in (standard.field1_first_line, standard.field2_first_line):
        line = first - 1
        while np.all(types[(line - 1) % standard.lines] == ORDINARY):
            line -= 1
        offsets.append(first - standard.first_ordinary_after(line))
    return tuple(offsets)


class NtscDecoder:
    """Decode composite baseband into frames - NTSC unless told otherwise."""

    #: Where to read blanking once the sync pulses are known: the breezeway,
    #: between the end of sync and the colour burst - counted from where the
    #: pulse began plus the standard's sync width, see `back_porch_level`.
    BREEZEWAY = (0.1e-6, 0.5e-6)

    def __init__(self, sample_rate, width=None, active_lines=None,
                 standard=None):
        self.standard = standard or NTSC
        std = self.standard
        self.sample_rate = float(sample_rate)
        self.width = int(width or std.width)
        self.active_lines = int(active_lines or std.active_lines_per_field)
        self.chroma_span = chroma_span(self.sample_rate,
                                       subcarrier=std.subcarrier)
        self.sync_discriminant = (std.equalizing_width + std.sync_width) / 2
        self.field_offsets = field_line_offsets(std)

    # -- levels ----------------------------------------------------------

    def levels(self, x):
        """First estimates of sync tip and blanking, read off the signal.

        A capture arrives at whatever scale the radio gave it, so nothing
        can be assumed about absolute level. Both come from how much of
        every signal sits at each: see `SYNC_PERCENTILE` and
        `BLANK_PERCENTILE`. `decode_frame` refines both once the pulses are
        known - blanking on the porches, sync in the middle of the pulses -
        which is what a television clamps on.

        **Blanking used to be the histogram's commonest level**, on the
        grounds that the porches are all exactly one value, and that is only
        true of a clean signal. Add noise and the porches spread across many
        bins while any large flat area spreads the same way over more
        samples: measured with noise of 0.05 of the sync-to-white swing, the
        commonest level on a white picture was 2.5 times the sync-to-
        blanking step too high, and on colour bars off an FM link it was
        the white bar. The slicer then cut at blanking level, every "pulse"
        was a whole blanking interval, and the picture came out wrong with
        no error raised. The 20th percentile decoded every picture tried -
        white, black, grey, dark, 90% white, fully saturated red and blue,
        each clean and with noise, at 10 MS/s and at 4x subcarrier - and
        read real off-air signals within 0.96 to 1.09 of their back porch.

        **Before that the histogram window was what failed.** It was the
        10-60% band of sync-to-white, and on a dark scene the top of that
        range collapses toward blanking, which then sat outside the window:
        off air, 17 consecutive frames failed every time the clip reached a
        dark shot. A percentile has no window to fall out of, and
        `scripts/test_ntsc_loopback.py` keeps both cases.
        """
        # Every fourth sample is plenty: a frame is a third of a million of
        # them, and np.percentile on the lot was a sixth of the whole decode.
        sample = x[::4] if x.size > 40000 else x
        sync, blank = np.percentile(sample, (SYNC_PERCENTILE, BLANK_PERCENTILE))
        return float(sync), float(blank)

    def back_porch_level(self, x, starts, widths, fallback):
        """Blanking, measured where a television measures it.

        Every real receiver clamps on the back porch, because it is the one
        part of the line whose level is fixed by the standard rather than
        by the picture. Once `find_pulses` has found the sync pulses, the
        breezeway just after each one gives blanking directly - no
        histogram, no assumption about how bright the scene is.

        **The window is counted from where the pulse began, not where the
        slicer says it ended.** The breezeway is only 0.6 us - four samples
        at 10 MS/s - hard against sync's trailing edge, and noise moves the
        slicer's idea of that edge: on fully saturated red and blue with
        noise, a window hung off the measured end slid onto the edge and
        read blanking 0.011 low. The start plus the nominal sync width does
        not move that way, and read 0.0026. The front porch looks like the
        obvious alternative and is worse where it matters: through the
        NTSC transmitter's vestigial-sideband filter the picture rings into
        it, and colour bars came back 0.102 out against 0.081 for the
        breezeway. Off an FM link all of them agree to within 0.002.

        Only full-width line sync counts: an equalizing pulse is too
        narrow and a field-sync pulse too wide, and neither is followed by
        a porch - what comes after them is more sync.
        """
        horizontal = ((widths > self.sync_discriminant)
                      & (widths < VERTICAL_RUN))
        if horizontal.sum() < 8:
            return fallback
        fs = self.sample_rate
        ends = starts[horizontal] + self.standard.sync_width * fs
        lo = (ends + self.BREEZEWAY[0] * fs).astype(np.int64)
        hi = (ends + self.BREEZEWAY[1] * fs).astype(np.int64)
        span = int(np.median(hi - lo))
        if span < 2:
            return fallback
        lo = lo[(lo >= 0) & (lo + span < x.size)]
        if lo.size < 8:
            return fallback
        return float(np.median(x[lo[:, None] + np.arange(span)[None, :]]))

    def sync_tip_level(self, x, starts, widths, fallback):
        """The sync tip, measured in the middle of the line sync pulses.

        A low percentile of the whole signal sits below the real tip by
        however far noise reaches, and the tip is half of what sets the
        picture's scale. The middle of each line sync pulse, clear of both
        edges, is at the tip by definition.
        """
        horizontal = ((widths > self.sync_discriminant)
                      & (widths < VERTICAL_RUN))
        if horizontal.sum() < 8:
            return fallback
        edge = int(round(1.0e-6 * self.sample_rate))
        span = int(round(self.standard.sync_width * self.sample_rate)) - 2 * edge
        if span < 2:
            return fallback
        lo = starts[horizontal] + edge
        lo = lo[lo + span < x.size]
        if lo.size < 8:
            return fallback
        return float(np.median(x[lo[:, None] + np.arange(span)[None, :]]))

    def burst_ratio(self, x, starts, widths, sync, blank):
        """How big the colour burst comes back, against the standard's own.

        Both standards make the burst exactly as big peak-to-peak as the
        step from sync tip to blanking - 40 IRE, or 300 mV. So one is the
        path's gain at the colour subcarrier and the other its gain at DC,
        and their ratio is the frequency response of everything in between
        measured against nothing but the signal itself: 1.0 is flat, and
        anything else is a lift that has not been undone.

        That is what makes it worth having. An FM video link pre-emphasises
        the picture before transmitting and the receiver takes it out
        again, and if the two curves do not match the picture is wrong in a
        way that looks like bad colour rather than like a wrong setting.
        Nobody publishes what an FPV transmitter module's own R/C network
        does, and this reads it off the air without a test signal and
        without knowing what is being televised.

        Returns None when there is no burst to measure - a monochrome
        signal has none, which is a fact about the signal and not a
        failure.
        """
        std = self.standard
        horizontal = starts[(widths > self.sync_discriminant)
                            & (widths < VERTICAL_RUN)]
        if horizontal.size < 8 or blank <= sync:
            return None
        level_scale = (std.blank_level - std.sync_level) / (blank - sync)
        # The same window decode_frame reads the burst phase in: a whole
        # cycle in from each end, clear of the ringing every filter leaves
        # on the edges of a real burst.
        cycle = 1.0 / std.subcarrier
        span = int(round((std.burst_cycles - 2) * cycle * self.sample_rate))
        if span < 4:
            return None
        at = horizontal[:, None] + int(round((std.burst_start + cycle)
                                             * self.sample_rate))
        at = at[(at[:, 0] >= 0) & (at[:, 0] + span < x.size)]
        if at.shape[0] < 8:
            return None
        at = at + np.arange(span)[None, :]
        burst = (x[at] - blank) * level_scale
        # **Window the burst before averaging it.** Mixing down and taking
        # the mean leaves a term at twice the subcarrier, which only cancels
        # when the window is a whole number of cycles - and it is not at any
        # ordinary sample rate. Seven cycles of NTSC's subcarrier is 19.56
        # samples at 10 MS/s: rounded to 20, the leftover read a *perfect*
        # loopback as 0.974, which is a 0.23 dB lift that is not there and
        # would be quoted as a transmitter's pre-emphasis. A Hann window
        # puts it back to 1.0000 at every rate tried - 10, 12.5 and 20 MS/s
        # in both standards.
        taper = np.hanning(span + 2)[1:-1]
        envelope = 2.0 * np.abs(
            np.sum(burst * taper
                   * np.exp(-2j * np.pi * std.subcarrier * at / self.sample_rate),
                   axis=1) / taper.sum())
        # The median over the lines, not the mean: a line whose burst was
        # hit by noise should not drag the reading, and half the lines of a
        # frame are enough to settle it.
        return float(np.median(envelope) / std.burst_amplitude)

    # -- sync ------------------------------------------------------------

    def find_pulses(self, x, sync, blank):
        """Start index and width in seconds of every excursion to sync level.

        **Slicing the signal as it arrives does not work off the air.** Sync
        is a low-frequency feature but the composite it sits in carries the
        colour subcarrier and whatever noise the radio added, and those
        cross the slice level over and over inside a single pulse. A real
        capture came back with its longest run at 9.8 us where the vertical
        block's are 27.1, chopped into fragments, and no field was ever
        found. Every television has a sync separator in front of its slicer
        for exactly this reason; half a microsecond of averaging is enough,
        and it recovers the 27.10 us runs exactly.
        """
        span = max(1, int(round(SYNC_SEPARATOR * self.sample_rate)))
        detect = (np.convolve(x, np.ones(span) / span, 'same') if span > 1
                  else x)
        thresh = sync + SYNC_SLICE * (blank - sync)
        low = detect < thresh
        edges = np.diff(low.astype(np.int8))
        starts = np.flatnonzero(edges == 1) + 1
        ends = np.flatnonzero(edges == -1) + 1
        if ends.size and starts.size and ends[0] < starts[0]:
            ends = ends[1:]
        n = min(starts.size, ends.size)
        starts, ends = starts[:n], ends[:n]
        widths = (ends - starts) / self.sample_rate
        # Drop anything too brief to be a sync pulse. Lines are numbered by
        # counting these, so a single spurious one shears everything after.
        keep = widths >= SYNC_MIN_WIDTH
        return starts[keep], widths[keep]

    def find_fields(self, starts, widths):
        """Index of the first line sync after each field-sync block.

        The field-sync block is the only place the signal sits at sync
        level for most of a half-line at a time. Those long runs mark a
        field; the first full-width sync after the post-equalizing pulses is
        the first ordinary line, from which the active picture is counted.
        """
        longs = np.flatnonzero(widths > VERTICAL_RUN)
        if longs.size == 0:
            return []
        groups = np.split(longs, np.flatnonzero(np.diff(longs) > 3) + 1)
        fields = []
        for g in groups:
            after = int(g[-1]) + 1
            while (after < widths.size
                   and widths[after] < self.sync_discriminant):
                after += 1                    # skip the post-equalizing pulses
            if after < widths.size:
                fields.append(after)
        return fields

    # -- picture ---------------------------------------------------------

    def decode_frame(self, x, color=True):
        """Decode the first whole frame in ``x`` into an RGB array in 0..1.

        **Every active line of a field is sampled at once.** This used to
        take one line at a time in a Python loop - 480 of them per frame,
        each with its own convolution and three interpolations - and ran at
        0.54x real time, 16 frames a second against the 29.97 a receiver
        needs. The lines are all the same length and all want the same
        operations, so they go into one 2-D array instead.
        """
        x = np.asarray(x, dtype=np.float64)
        std = self.standard
        sync, blank = self.levels(x)
        if blank <= sync:
            raise ValueError("no sync found: blanking is not above sync level")

        starts, widths = self.find_pulses(x, sync, blank)
        # Now that the pulses are known, take blanking off the porches and
        # the sync tip off the pulses themselves, rather than off the first
        # estimates. Those are guesses from how the samples are distributed;
        # the porch and the tip are the standard's own references and do
        # not move with the picture or the noise. They set the scale, and
        # getting blanking exactly right took the loopback colour error from
        # 0.0089 to about 1e-11.
        refined = self.back_porch_level(x, starts, widths, blank)
        tip = self.sync_tip_level(x, starts, widths, sync)
        if refined > tip:
            # Slicing again is almost never worth it, and costs a fifth of
            # the whole decode. The slice sits at 35% of the way from sync
            # to blanking, so it stays well inside the pulses even when the
            # first estimate was off by the 19% that black setup routinely
            # puts it out by - measured off air, 883 pulses either way.
            # This is only a guard for an estimate that was wildly wrong.
            moved = (abs(refined - blank) + abs(tip - sync)
                     > 0.5 * (blank - sync))
            sync, blank = tip, refined
            if moved:
                starts, widths = self.find_pulses(x, sync, blank)
        # (x - blank) * level_scale puts blanking at 0 and sync at the
        # standard's sync level, in its own units: -40 IRE, or -300 mV.
        level_scale = (std.blank_level - std.sync_level) / (blank - sync)

        fields = self.find_fields(starts, widths)
        if len(fields) < 2:
            raise ValueError("could not find two fields - no vertical sync?")

        # **Which field came first has to be worked out, not assumed.** A
        # capture starts wherever it starts, so the first field-sync block
        # in it is field one about half the time and field two the rest.
        # Put field two's lines on the even rows and the picture is offset
        # by a line - every edge grows a comb, and the error roughly trebles.
        #
        # The blocks are not evenly spaced: a field is half an odd number of
        # lines, so consecutive blocks are 263 lines apart and then 262 in
        # NTSC, 313 and then 312 in PAL. The longer gap to the next one
        # means this block is field one.
        lines_between = ((starts[fields[1]] - starts[fields[0]])
                         / (std.line * self.sample_rate))
        swap = 0 if lines_between > std.lines / 2 else 1
        #: Where in ``x`` the frame just decoded begins - the first line
        #: after its first field-sync block - for a caller that has to know
        #: when the picture happened: the streaming frame sink lines the
        #: sound up against it.
        self.last_frame_start = int(starts[fields[0]])
        #: Whether the first field in ``x`` was field two, so the picture
        #: just decoded paired it with field one of the *next* frame.
        self.last_swapped = bool(swap)

        rows = np.zeros((2 * self.active_lines, self.width, 3))
        for parity, f in enumerate(fields[:2]):
            parity = (parity + swap) % 2
            # **The two fields do not start the same number of lines after
            # their field-sync block** in NTSC: field one's block is lines
            # 1-9 and its picture starts at line 21, eleven lines on from the
            # first ordinary line; field two's block is lines 264-272 and
            # its picture starts at 283, which is ten. Using eleven for both
            # - which this once did - puts half the picture one line low, so
            # every horizontal edge grows a comb. `field_line_offsets` works
            # them out for either standard.
            k = np.arange(self.active_lines)
            pulse = f + self.field_offsets[parity] + k
            keep = pulse < starts.size
            k, pulse = k[keep], pulse[keep]
            if k.size == 0:
                continue
            y, u, v = self._sample_lines(x, starts[pulse], blank,
                                         level_scale, color)
            rows[2 * k + parity] = self._to_rgb(y, u, v)
        return np.clip(rows, 0, 1)

    def _sample_lines(self, x, h_refs, blank, level_scale, color):
        """Y and the two colour-difference signals for a field of lines.

        Returns three ``(lines, width)`` arrays.
        """
        std = self.standard
        fs = self.sample_rate
        seg_len = int(round(std.active_len * fs))
        start = h_refs + int(round(std.active_start * fs))
        # A line whose segment would run off the end of the capture reads as
        # black rather than wrapping round to the beginning.
        usable = (start >= 0) & (start + seg_len <= x.size)
        at = np.clip(start[:, None] + np.arange(seg_len)[None, :],
                     0, x.size - 1)
        seg = (x[at] - blank) * level_scale + std.blank_level
        seg[~usable] = std.blank_level

        # The same resampling positions for every line, so the interpolation
        # is two gathers and a blend rather than a call per line.
        pos = np.linspace(0, seg_len - 1, self.width)
        lo = np.floor(pos).astype(np.int64)
        hi = np.minimum(lo + 1, seg_len - 1)
        frac = pos - lo

        def resample(a):
            return a[:, lo] * (1.0 - frac) + a[:, hi] * frac

        gain = std.luma_gain * std.white_level      # 92.5 IRE, or 700 mV
        if not color:
            zero = np.zeros((h_refs.size, self.width))
            return (resample(seg) - std.black_level) / gain, zero, zero

        # Complex envelope: s(t) = Re{Z e^{jwt}}, so Z = 2*LPF{s e^{-jwt}}.
        carrier = np.exp(-2j * np.pi * std.subcarrier * at / fs)
        z = 2.0 * _box_filter(seg * carrier, self.chroma_span)

        # **Take chroma out of luma now, before the envelope is turned to
        # the burst.** Put back on the carrier it came off, the envelope
        # rebuilds the chroma exactly as it sits in these samples. It used
        # to be rebuilt *after* the rotation below, which put it out of
        # phase with the real chroma by the rotation itself - and the
        # rotation is set by which sample the buffer happens to start on.
        # So most of the chroma was left behind in luma as a dot pattern,
        # up to 0.59 at a single pixel: colour bars straight from the
        # encoder came back anywhere from 0.017 to 0.20 out as the first
        # sample moved, and off air, where a buffer starts anywhere, on
        # every frame. The loopback test decoded from the one start where
        # the rotation is zero, which is how it never showed.
        chroma = np.real(z * np.conj(carrier))          # back to a real signal
        luma = seg - chroma

        # The burst says what phase this line's subcarrier actually has.
        # **Read it a whole cycle in from each end.** The edges of a real
        # burst carry the ringing of every filter it has been through, and
        # the slicer places a pulse a sample or so late, so a window over
        # the whole burst takes in some of both. Measured against the whole
        # burst: through the vestigial-sideband transmitter colour bars came
        # back 0.081 out against 0.086, off the FM link 0.062 against 0.068
        # and 0.026 against 0.030, and PAL at 4x subcarrier went from 0.012
        # to exact - its burst, 45 degrees either side of -U, does not start
        # on a zero crossing the way NTSC's does. On clean synthetic signals
        # at rates that are not a multiple of the subcarrier the whole burst
        # was 0.002-0.009 better, which real signals outweigh.
        cycle = 1.0 / std.subcarrier
        b_len = int(round((std.burst_cycles - 2) * cycle * fs))
        b_at = np.clip(h_refs[:, None] + int(round((std.burst_start + cycle) * fs))
                       + np.arange(b_len)[None, :], 0, x.size - 1)
        burst = (x[b_at] - blank) * level_scale
        zb = 2.0 * np.mean(
            burst * np.exp(-2j * np.pi * std.subcarrier * b_at / fs), axis=1)
        found = np.abs(zb) > 1e-9

        if std.v_switch:
            u, v = self._pal_axes(z, zb, found, gain)
        else:
            # The NTSC encoder sent -20*sin(wt), whose complex envelope is
            # +20j, so the measured envelope divided by its own unit vector
            # and multiplied by j puts every line back on the same axis.
            rotate = np.ones(h_refs.size, dtype=np.complex128)
            rotate[found] = 1j * np.abs(zb[found]) / zb[found]
            z = z * rotate[:, None]
            # Z = 92.5*(r_y - j*b_y), from the encoder's sin/cos assignment.
            v = np.real(z) / gain
            u = -np.imag(z) / gain

        return ((resample(luma) - std.black_level) / gain,
                resample(u), resample(v))

    @staticmethod
    def _pal_axes(z, zb, found, gain, window=4):
        """U and V for a field of PAL lines, V un-switched.

        The encoder sent chroma as U sin(wt) + s V cos(wt), envelope
        ``-jU + sV``, and the burst as (-sin(wt) + s cos(wt))/sqrt(2), envelope
        at 45 degrees when s is +1 and 135 when it is -1 - both turned by
        the same unknown angle from where this buffer started. Four
        neighbouring lines hold two of each, so their bursts' mean points
        exactly between, 90 degrees round from that angle: that is the
        reference. Each line's own burst, 45 degrees to one side of it or
        the other, gives s. Four lines is 256 us, too short for the two
        radios' clocks to turn the subcarrier measurably, which a whole
        field's average would not be.
        """
        n = zb.size
        unit = np.zeros(n, dtype=np.complex128)
        unit[found] = zb[found] / np.abs(zb[found])
        totals = np.concatenate([[0], np.cumsum(unit)])
        lo = np.clip(np.arange(n) - window // 2 + 1, 0, max(n - window, 0))
        hi = np.minimum(lo + window, n)
        axis = totals[hi] - totals[lo]
        size = np.abs(axis)
        axis = np.where(size > 1e-9, axis / np.maximum(size, 1e-12), 1j)
        s = np.where(np.angle(unit * np.conj(axis)) < 0, 1.0, -1.0)
        z = z * (1j * np.conj(axis))[:, None]          # turn the reference to 90
        return -np.imag(z) / gain, s[:, None] * np.real(z) / gain

    def _to_rgb(self, y, u, v):
        """Undo the reduction factors and the luminance matrix."""
        bmy = u / self.standard.u_scale
        rmy = v / self.standard.v_scale
        b = bmy + y
        r = rmy + y
        g = (y - KR * r - KB * b) / KG
        return np.stack([r, g, b], axis=-1)


#: The same decoder, by a name that does not pretend it only does NTSC.
CompositeDecoder = NtscDecoder
