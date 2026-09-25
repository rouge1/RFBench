"""ISM-band frame encoder - fields to bits, bits to pulses, pulses to baseband.

The transmit half of the 315/433/868/915 MHz work. A sensor reading goes in and
a ``complex64`` burst comes out, with `rtl_433` as the referee rather than a
decoder of our own writing. [devnotes/ism.md](../devnotes/ism.md) has the band
rules, the bench setup and why the carrier never really turns off; this is the
encoder those notes describe.

Kept free of GNU Radio and Qt, so a frame can be built, rendered and graded
with nothing plugged in at all::

    python scripts/test_ism_frame.py

Three stages, each separately checkable, which is the whole reason for the
shape:

1. **Fields to bits.** Every profile packs its own fields and its own check
   byte. ``code()`` renders the result as the ``{36}b590bef47`` string
   ``rtl_433 -y`` takes, which grades the packing and the checksum with no DSP
   anywhere in the way.
2. **Bits to a pulse train** - a list of ``(mark_us, space_us)`` pairs in whole
   microseconds. Four codings cover nearly the whole rtl_433 corpus.
3. **Pulse train to baseband** - ramped edges, a lead-in long enough for the
   pulse detector to arm, and a final gap long enough to end the package.

Four things here look like mistakes until you know what they are for:

* **A row and a package are not the same thing.** ``bitbuffer_find_repeated_row``
  looks for identical rows *inside one package*, so a decoder wanting three of
  them needs the repeats separated by a gap longer than the device's
  ``gap_limit`` and shorter than its ``reset_limit``. Make that gap too long
  and every repeat becomes its own package holding one row, and a perfectly
  good frame does not decode.
* **A PPM frame carries one more pulse than it has bits.** The bit is the gap
  *after* a pulse, so with no trailing pulse the last bit runs into the frame
  gap and is swallowed.
* **The PWM slicer reads a short pulse as 1**, which is the opposite of most
  people's first guess, and two of the profiles here then have their whole
  bitbuffer inverted by the decoder on top of that. The bits these functions
  take are always what the *slicer* should produce - wire bits - never the
  logical frame.
* **A sync pulse can be a bit.** EV1527's sync gap is longer than its own
  ``reset_limit``, so the package rtl_433 assembles is 24 data bits plus the
  *next* frame's sync pulse, and the decoder insists on 25. One frame on its
  own never decodes.

Every profile below was checked against `rtl_433` 23.11 through ``-y`` before
any of it was rendered, and the expected line is carried on the frame so the
test can assert it.
"""

import numpy as np

# --- bits -------------------------------------------------------------------


def pack(fields):
    """``[(value, width), ...]`` to a list of bits, most significant first.

    Raises rather than truncating: a field that does not fit is a packing bug,
    and silently dropping its top bits would produce a frame that decodes as
    some other reading entirely.
    """
    bits = []
    for value, width in fields:
        value = int(value)
        if not 0 <= value < (1 << width):
            raise ValueError("%d does not fit in %d bits" % (value, width))
        bits += [(value >> i) & 1 for i in range(width - 1, -1, -1)]
    return bits


def invert(bits):
    """The complement, for the decoders that call ``bitbuffer_invert``."""
    return [b ^ 1 for b in bits]


def to_bytes(bits):
    """Bits to bytes, the last one zero-padded on the right as rtl_433 pads."""
    padded = bits + [0] * (-len(bits) % 8)
    return bytes(int(''.join(str(b) for b in padded[i:i + 8]), 2)
                 for i in range(0, len(padded), 8))


def code(bits, rows=1):
    """The ``{36}b590bef47`` string ``rtl_433 -y`` takes.

    ``rows`` repeats it, which is not cosmetic: Nexus-TH and LaCrosse both run
    ``bitbuffer_find_repeated_row`` and refuse a single row outright.
    """
    hex_bits = bits + [0] * (-len(bits) % 4)
    digits = ''.join('%x' % int(''.join(str(b) for b in hex_bits[i:i + 4]), 2)
                     for i in range(0, len(hex_bits), 4))
    one = '{%d}%s' % (len(bits), digits)
    return '/'.join([one] * rows)


# --- check bytes ------------------------------------------------------------


def add8(data):
    """An 8-bit additive checksum - Acurite's, over the first four bytes."""
    return sum(data) & 0xFF


def lfsr_digest8_reflect(data, gen, key):
    """LaCrosse's check byte. A digest, not a CRC, and reflected.

    Bytes are walked last to first and bits low to high, which is what the
    "reflect" means; get either direction wrong and it still returns a
    plausible-looking byte that the decoder rejects.
    """
    total = 0
    for byte in reversed(data):
        for i in range(8):
            if (byte >> i) & 1:
                total ^= key
            key = ((key << 1) ^ gen) & 0xFF if key & 0x80 else (key << 1) & 0xFF
    return total


def crc8(data, poly, init):
    """The plain MSB-first CRC-8 rtl_433 calls ``crc8``."""
    rem = init
    for byte in data:
        rem ^= byte
        for _ in range(8):
            rem = ((rem << 1) ^ poly) & 0xFF if rem & 0x80 else (rem << 1) & 0xFF
    return rem


def rubicson_collides(bits):
    """True if a 36-bit Nexus frame would be claimed by the Rubicson decoder.

    Rubicson accepts any row of 36 to 38 bits whose CRC-8 over a rearranged
    five bytes comes to zero, and it is tried first, so a frame that happens to
    satisfy it never reaches Nexus-TH at all. Measured over 703 frames here,
    3 collided - about 1 in 234, against the 1 in 256 chance the arithmetic
    predicts. The reading is not garbled and nothing reports an error; a
    different model simply comes out, which is far harder to spot than a
    failure would be. Anything choosing a sensor id should step past an id that
    trips this.
    """
    b = to_bytes(bits)
    if len(b) < 5:
        return False
    tmp = bytes([b[0], b[1], b[2], b[3] & 0xF0,
                 ((b[3] & 0x0F) << 4) | ((b[4] & 0xF0) >> 4)])
    return crc8(tmp, 0x31, 0x6C) == 0


# --- codings: bits to a pulse train -----------------------------------------
#
# A pulse train is [(mark_us, space_us), ...], always in whole microseconds so
# that a sample rate of an integer number of samples per microsecond renders it
# exactly. The bits going in are wire bits - what rtl_433's slicer should come
# out with - not the logical frame.


def ppm(bits, pulse_us, short_gap_us, long_gap_us, trailing_pulse=True):
    """Pulse-position: fixed pulse, and the gap *after* it carries the bit.

    ``trailing_pulse`` adds the extra pulse that ends the last bit's gap. It
    defaults on because without it the frame is one bit short and nothing says
    why.
    """
    train = [(pulse_us, long_gap_us if b else short_gap_us) for b in bits]
    if trailing_pulse:
        train.append((pulse_us, 0))
    return train


def pwm(bits, short_us, long_us, period_us=None):
    """Pulse-width: **short pulse = 1, long pulse = 0**, and the gap is ignored.

    The gap is still made to hold ``period_us`` constant, because real devices
    do and because a slicer that later gets a ``gap_limit`` needs the gaps to
    be sane. With no period given, the two widths simply swap roles.
    """
    if period_us is None:
        period_us = short_us + long_us
    train = []
    for b in bits:
        mark = short_us if b else long_us
        train.append((mark, period_us - mark))
    return train


def _to_train(segments):
    """``[(level, us), ...]`` to ``[(mark_us, space_us), ...]``.

    Runs of the same level merge, which is the whole point: it is what turns a
    string of PCM ones into one long mark, and what makes two adjacent
    Manchester halves at the same level a single edge. Leading silence is
    dropped, because the pulse detector cannot see it anyway and a train has to
    begin on a mark.
    """
    train = []
    mark = space = 0
    for level, us in segments:
        if level:
            if mark and space:
                train.append((mark, space))
                mark = space = 0
            space = 0
            mark += us
        elif mark:
            space += us
    if mark:
        train.append((mark, space))
    return train


def pcm(bits, short_us, long_us):
    """``short_us`` is the pulse and ``long_us`` the bit period.

    Equal widths are NRZ, a shorter pulse is return-to-zero. Runs of equal bits
    merge into one mark or one space, which is what makes a ``0xAAAA`` preamble
    a string of single-period toggles - the slicer calibrates its clock off at
    least twelve of them, after which clock error stops mattering.
    """
    segments = []
    for b in bits:
        if b:
            segments.append((1, short_us))
            if long_us > short_us:
                segments.append((0, long_us - short_us))
        else:
            segments.append((0, long_us))
    return _to_train(segments)


def manchester(bits, half_us):
    """Manchester with a zero bit hardcoded onto the front, as rtl_433 has it.

    Each bit is two half-periods: a 0 is low then high, a 1 is high then low,
    so the edge in the middle is rising for 0 and falling for 1. The leading
    zero cannot be turned off in the decoder - its whole purpose is that the
    first thing an OOK detector can see is a rising edge, which is the second
    half of a zero - so it is prepended here too, and every offset in the frame
    shifts by one because of it.
    """
    segments = []
    for b in [0] + list(bits):
        segments += [(1, half_us), (0, half_us)] if b \
            else [(0, half_us), (1, half_us)]
    return _to_train(segments)


# --- a frame ----------------------------------------------------------------


class Frame:
    """One complete burst: the bits, the whole pulse train, and what it is.

    ``train`` is the entire transmission including every repeat and the gaps
    between them, so ``render()`` has no protocol knowledge in it at all.
    """

    def __init__(self, name, bits, train, rows, expect, freq_hz=433.92e6,
                 row_us=None):
        self.name = name
        self.bits = bits
        self.train = train
        self.rows = rows
        self.expect = expect          # the model rtl_433 should name
        self.freq_hz = freq_hz
        # One repeat, for anything that wants to show or measure a single
        # frame rather than the whole burst. EV1527 has to say so: its
        # repeats are packages rather than rows, so ``rows`` is 1 however
        # many go out.
        self.row_us = (float(row_us) if row_us is not None
                       else self.duration_us / max(1, rows))

    @property
    def code(self):
        """The ``rtl_433 -y`` string for this frame, repeats included."""
        return code(self.bits, self.rows)

    @property
    def duration_us(self):
        return sum(mark + space for mark, space in self.train)

    @property
    def shortest_us(self):
        return min(min(m for m, _ in self.train),
                   min(s for _, s in self.train if s))

    def __repr__(self):
        return "<Frame %s %d bits, %d rows, %.1f ms>" % (
            self.name, len(self.bits), self.rows, self.duration_us / 1000.0)


def _repeat(train, rows, row_gap_us, package_gap_us):
    """Lay one row out ``rows`` times, then end the package.

    ``row_gap_us`` is silence *added* after each row, not a replacement for
    whatever gap the row already ends with. That distinction cost a debugging
    session: replacing it, with a protocol whose rows are broken by their own
    sync pulses and so want no extra gap at all, set the last bit's gap to
    zero. Its pulse then ran into the next row's sync pulse, the two merged
    into one long pulse, and the row came out 39 bits instead of 40 - which
    matched a *different variant* of the same sensor family and decoded with a
    plausible temperature and no humidity. Nothing anywhere said it was wrong.

    ``row_gap_us`` must leave the total longer than the device's ``gap_limit``
    and shorter than its ``reset_limit``, or the repeats do not land in one
    package and the decoders that count identical rows never see them.
    """
    out = []
    for i in range(rows):
        body = list(train)
        mark, space = body[-1]
        body[-1] = (mark, space + (package_gap_us if i == rows - 1
                                   else row_gap_us))
        out += body
    return out


# --- device profiles --------------------------------------------------------
#
# The timings are the r_device structs' own, in microseconds, with the measured
# figures from the corpus preferred where the two differ - a decoder's nominal
# 1000/2000 is a window, and real devices sit well inside it.


def nexus_th(sensor_id=181, channel=1, battery_ok=True, temp_c=19.0,
             humidity=71, rows=12):
    """Nexus-TH and the FreeTec/infactory clones - protocol 19, PPM, 36 bits.

    Nibbles: id, id, flags, temp, temp, temp, 0xF, humidity, humidity. The
    flags nibble's top bit reads back as ``Battery: 1`` when set, whatever the
    decoder's own comment says, and its bottom two bits are the channel one
    lower than the number reported.

    Needs at least three identical rows in one package. ``gap_limit`` is
    3000 us and ``reset_limit`` 5000, so the rows are spaced 4000 us apart -
    long enough to break the row, short enough to stay in the package.

    There is a 1-in-256 hazard worth knowing about: a frame whose bytes happen
    to satisfy Rubicson's CRC is claimed by that decoder instead, and comes
    back as a different model entirely. ``scripts/test_ism_frame.py`` sweeps
    for it rather than trusting arithmetic.
    """
    if not 1 <= channel <= 4:
        raise ValueError("channel is 1-4")
    raw = int(round(temp_c * 10))
    bits = pack([(sensor_id, 8),
                 (1 if battery_ok else 0, 1),
                 (0, 1),                        # the button-press flag
                 (channel - 1, 2),
                 (raw & 0xFFF, 12),             # signed, 12 bits, x10
                 (0xF, 4),                      # the constant nibble
                 (humidity, 8)])
    row = ppm(bits, 496, 964, 1944)
    return Frame("nexus_th", bits, _repeat(row, rows, 4000, 25000),
                 rows, "Nexus-TH")


def acurite_609txc(sensor_id=202, battery_low=False, temp_c=26.2, humidity=76,
                   rows=3):
    """Acurite 609TXC - protocol 11, PPM, 40 bits, additive checksum.

    id, a status nibble whose 0x8 bit is battery-low, 12-bit signed
    temperature x10, humidity, then the low byte of the sum of those four
    bytes.

    One row decodes on its own. The leading sync burst the real sensor sends
    is deliberately omitted: a sync burst *without* its ~8.9 ms gap merges
    into the data row and the length comes out wrong, and omitting it is the
    simplest thing that is correct.
    """
    raw = int(round(temp_c * 10)) & 0xFFF
    status = 0x8 if battery_low else 0x2
    body = [sensor_id & 0xFF,
            (status << 4) | (raw >> 8),
            raw & 0xFF,
            humidity & 0xFF]
    bits = pack([(b, 8) for b in body] + [(add8(body), 8)])
    row = ppm(bits, 504, 992, 1988)
    # reset_limit is 10000 us, so the rows sit 8900 apart to stay in one
    # package - which is also the gap the real sensor leaves.
    return Frame("acurite_609txc", bits, _repeat(row, rows, 8900, 25000),
                 rows, "Acurite-609TXC")


def lacrosse_tx141th_bv2(sensor_id=0xE7, channel=0, battery_low=False,
                         temp_c=-20.0, humidity=10, rows=12):
    """LaCrosse TX141TH-Bv2 - protocol 73, PWM, 40 bits, LFSR digest.

    id, battery-low, a test bit, 2-bit channel, 12-bit *unsigned* temperature
    with an offset of 500 and a scale of 10, humidity, digest.

    Two inversions to keep straight. The slicer reads a short pulse as 1, and
    then the decoder inverts the whole bitbuffer, so the bits put on the wire
    here are the complement of the logical frame. Needs three identical rows,
    which it gets without any long gap at all: a pulse matching ``sync_width``
    breaks a row by itself, so the four sync cycles at the head of each packet
    do the job and every gap stays under the 1700 us ``reset_limit``. That is
    why the row gap here is zero, and why ``_repeat`` adds rather than
    replaces - see the note on it.
    """
    raw = int(round(temp_c * 10)) + 500
    if not 0 <= raw < 4096:
        raise ValueError("temperature is out of the 12-bit range")
    if rows == 4:
        # Measured, and not explained: at exactly four rows this comes back
        # as TFA-303221 and LaCrosse-TX141THBv2 never appears. Three, five
        # and twelve are all fine. Refusing beats returning a frame that
        # decodes under the wrong model with a plausible reading.
        raise ValueError(
            "exactly four rows decodes as TFA-303221 rather than "
            "LaCrosse-TX141THBv2 - use three, or five or more; the real "
            "sensor sends twelve. See devnotes/ism.md.")
    body = [sensor_id & 0xFF,
            ((1 if battery_low else 0) << 7) | ((channel & 0x3) << 4) | (raw >> 8),
            raw & 0xFF,
            humidity & 0xFF]
    logical = pack([(b, 8) for b in body]
                   + [(lfsr_digest8_reflect(body, 0x31, 0xF4), 8)])
    bits = invert(logical)
    sync = [(833, 833)] * 4
    row = sync + pwm(bits, 208, 417, period_us=625)
    return Frame("lacrosse_tx141th_bv2", bits, _repeat(row, rows, 0, 25000),
                 rows, "LaCrosse-TX141THBv2")


def ev1527(house_code=4660, command=8, frames=4):
    """EV1527 / PT2262 / SC226x remotes - protocol 30, PWM, 25 bits.

    A 16-bit house code and an 8-bit command, inverted by the decoder, so the
    wire carries their complement. There is no checksum anywhere in it.

    The shape is the trap. Sync is one short pulse followed by about 31 short
    periods of silence - 14.4 ms, far past the 1800 us ``reset_limit`` - so
    that gap ends the package. What rtl_433 assembles is therefore the 24 data
    bits plus the *next* frame's sync pulse, read as a 25th bit of 1, and the
    decoder accepts nothing else. So ``frames`` bursts need ``frames + 1`` sync
    pulses, and one frame on its own can never decode.
    """
    logical = pack([(house_code, 16), (command, 8)])
    wire = invert(logical)
    bits = wire + [1]                 # the 25th bit is the next sync pulse
    sync = (464, 14384)               # one short pulse, ~31 short periods idle
    train = []
    for _ in range(frames):
        train.append(sync)
        train += pwm(wire, 464, 1404, period_us=1868)
    train.append((464, 25000))        # the sync pulse that closes the last one
    one = sync[0] + sync[1] + sum(m + s for m, s in pwm(wire, 464, 1404,
                                                        period_us=1868))
    return Frame("ev1527", bits, train, 1, "Generic-Remote", row_us=one)


#: Every profile, by the name its frames carry.
PROFILES = {
    'nexus_th': nexus_th,
    'acurite_609txc': acurite_609txc,
    'lacrosse_tx141th_bv2': lacrosse_tx141th_bv2,
    'ev1527': ev1527,
}

#: The bands these devices use, as a channel plan for a frequency picker.
#: Only two of the four are ISM in the region that uses them - 433.92 MHz in
#: the United States is not, and is legal there only under the periodic
#: operation rule. What that means for a bench is in
#: [ism](../devnotes/ism.md#the-bands-and-what-is-actually-legal).
#: The caption is what a frequency picker shows, so it leads with the number.
BANDS = [
    ("315", 315.0, "315 MHz - US remotes and TPMS; FCC 15.231, not ISM"),
    ("433.92", 433.92, "433.92 MHz - ISM in Region 1; in the US, 15.231 only"),
    ("868.35", 868.35, "868.35 MHz - European SRD, ERC REC 70-03"),
    ("915", 915.0, "915 MHz - US ISM, FCC 15.249 or 15.247"),
]

#: What each profile takes, so a dialog can build itself out of this rather
#: than keep its own copy of every protocol's fields. A field is
#: ``(keyword, label, kind, low, high, default)`` with ``kind`` 'int' or
#: 'float'. ``interval_s`` is how often the real device sends, which is also
#: the only duty cycle any of these bands would tolerate on the air.
PROFILE_INFO = {
    'nexus_th': {
        'label': "Nexus-TH thermo-hygrometer (and FreeTec clones)",
        'freq_mhz': 433.92,
        'interval_s': 60,
        'fields': [
            ('sensor_id', "Sensor ID", 'int', 0, 255, 181),
            ('channel', "Sensor channel", 'int', 1, 4, 1),
            ('temp_c', "Temperature (C)", 'float', -50.0, 70.0, 19.0),
            # Humidity starts at 1: a Nexus frame with humidity 0 is reported
            # as Nexus-T, the temperature-only model, which looks exactly like
            # a decode failure and is not one.
            ('humidity', "Humidity (%)", 'int', 1, 99, 71),
            ('rows', "Repeats", 'int', 3, 24, 12),
        ],
    },
    'acurite_609txc': {
        'label': "Acurite 609TXC thermo-hygrometer",
        'freq_mhz': 433.92,
        'interval_s': 30,
        'fields': [
            ('sensor_id', "Sensor ID", 'int', 0, 255, 202),
            ('temp_c', "Temperature (C)", 'float', -40.0, 70.0, 26.2),
            ('humidity', "Humidity (%)", 'int', 0, 99, 76),
            ('rows', "Repeats", 'int', 1, 24, 3),
        ],
    },
    'lacrosse_tx141th_bv2': {
        'label': "LaCrosse TX141TH-Bv2 thermo-hygrometer",
        'freq_mhz': 433.92,
        'interval_s': 50,
        'fields': [
            ('sensor_id', "Sensor ID", 'int', 0, 255, 0xE7),
            ('channel', "Sensor channel", 'int', 0, 3, 0),
            ('temp_c', "Temperature (C)", 'float', -50.0, 70.0, 20.0),
            ('humidity', "Humidity (%)", 'int', 0, 99, 45),
            # Not 4 - see the guard in lacrosse_tx141th_bv2().
            ('rows', "Repeats", 'int', 5, 24, 12),
        ],
    },
    'ev1527': {
        'label': "EV1527 / PT2262 remote - a button press",
        'freq_mhz': 433.92,
        'interval_s': 5,
        'fields': [
            ('house_code', "House code", 'int', 0, 65535, 4660),
            ('command', "Command", 'int', 0, 255, 8),
            # One frame never decodes: the package rtl_433 assembles is this
            # frame's bits plus the *next* frame's sync pulse.
            ('frames', "Frames", 'int', 2, 24, 4),
        ],
    },
}


# --- rendering --------------------------------------------------------------


def render(frame, fs=250e3, amplitude=0.4, rise_us=None, lead_in_us=5000,
           offset_hz=0.0):
    """A pulse train to ``complex64`` baseband.

    Edges are placed by accumulating the frame's position in whole
    microseconds and rounding only at each boundary, so the error is never
    worse than half a sample and never accumulates. A rate giving a whole
    number of samples per microsecond - 8 MS/s, say - has no error at all and
    is what to transmit at; 250 kS/s, the rate rtl_433 captures and replays
    at, is 0.25 samples per microsecond and cannot place a 417 us LaCrosse
    pulse exactly. That is fine, and is what the decoders' tolerance windows
    are for, but it is the reason this rounds rather than refuses.

    What it does refuse is a frame whose shortest interval lands on fewer than
    ten samples: rtl_433's pulse detector cannot see one, and the failure is
    silent.

    ``rise_us`` shapes the edges. A hard-keyed rectangle has its first sidelobe
    only 13.3 dB down and decays as 1/f; a raised-cosine edge of width ``t_r``
    adds a breakpoint at ``1/(pi*t_r)`` and decays as 1/f^2 past it - about
    30 dB less energy at +/-1 MHz for a 500 us pulse with a 10 us ramp. The
    default is a thirtieth of the shortest interval in the frame, which is the
    middle of the useful 1/20 to 1/50 range. A symmetric ramp does not move the
    50 % crossing, so the decoder measures the same widths either way. On a
    VSG60 this is not optional: Signal Hound require the signal stay inside
    80 % of the sample rate.

    ``lead_in_us`` is silence in front. rtl_433 detects nothing until 1024 idle
    samples have gone by - 4.1 ms at 250 kS/s - so without it the first frame
    is simply lost.

    ``offset_hz`` moves the signal off the transmitter's own LO, which is the
    only defence against the carrier that never turns off. Leave it at zero for
    a file; the transmit app tunes the radio low and puts it back here.
    """
    sps = fs / 1e6                     # samples per microsecond, may be < 1
    if frame.shortest_us * sps < 10:
        raise ValueError(
            "%s's shortest interval is %g us, %.1f samples at %.6g S/s; "
            "rtl_433's pulse detector needs ten"
            % (frame.name, frame.shortest_us, frame.shortest_us * sps, fs))

    if rise_us is None:
        rise_us = max(1.0, frame.shortest_us / 30.0)

    lead = int(round(lead_in_us * sps))
    if lead < 1024:
        raise ValueError("lead-in is %d samples; rtl_433 arms after 1024"
                         % lead)

    env = np.zeros(lead + int(round(frame.duration_us * sps)) + 1,
                   dtype=np.float32)
    at_us = 0.0
    for mark, space in frame.train:
        start = lead + int(round(at_us * sps))
        env[start:lead + int(round((at_us + mark) * sps))] = 1.0
        at_us += mark + space

    taps = int(round(rise_us * sps))
    if taps > 1:
        # A step convolved with a normalised Hann window is a raised-cosine
        # edge; digital.burst_shaper_cc only shapes the ends of a tagged
        # burst, not every keying edge inside one, so it is no substitute.
        win = np.hanning(taps + 2)[1:-1].astype(np.float32)
        env = np.convolve(env, win / win.sum(), 'same').astype(np.float32)

    iq = (amplitude * env).astype(np.complex64)
    if offset_hz:
        t = np.arange(len(iq), dtype=np.float64) / fs
        iq *= np.exp(2j * np.pi * offset_hz * t).astype(np.complex64)
    return iq


def to_cu8(iq):
    """``complex64`` to rtl_433's native unsigned 8-bit, zero at 0x80.

    ``128 + round(127*x)``. The source carries two DC biases - 127 in the
    amplitude estimator and 128 in the magnitude one - which is unreconciled
    upstream and makes no practical difference.
    """
    out = np.empty(2 * len(iq), dtype=np.uint8)
    for part, offset in ((iq.real, 0), (iq.imag, 1)):
        out[offset::2] = np.clip(np.round(127.0 * part) + 128, 0, 255)
    return out


def capture_name(stem, freq_hz=433.92e6, fs=250e3, suffix='cu8'):
    """rtl_433 reads the centre frequency and rate out of the filename.

    A number followed by exactly ``M`` is megahertz and by exactly ``k`` is
    kilohertz, which is why the convention is ``g001_433.92M_250k.cu8``. Note
    that ``433920k`` would be read as a *sample rate*.
    """
    return "%s_%gM_%gk.%s" % (stem, freq_hz / 1e6, fs / 1e3, suffix)


def write_capture(path, iq):
    """Write ``.cu8``, or ``.cf32`` which is the array itself, unconverted."""
    if str(path).endswith('.cf32'):
        np.asarray(iq, dtype=np.complex64).tofile(str(path))
    else:
        to_cu8(iq).tofile(str(path))
    return path
