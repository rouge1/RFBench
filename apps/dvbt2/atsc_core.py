#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The parts of ATSC reception that are arithmetic rather than flowgraph.

Copied from the RF bench toolkit (``/data/python/SDR/apps/atsc_rx_core.py``);
its ``knowledge/atsc.md`` explains why the AFC is two corrections and why MER
cannot see the cliff. Changes made for this app are marked "FM receiver":
the PSIP tables (channel names, now and next) after ``TsAnalyzer``.

``atscReceiver`` wires GNU Radio's ``gr-dtv`` blocks together; everything
that can be decided with numpy lives here, free of GNU Radio and Qt so it
can be tested without a radio - the same split as ``rds_core`` against
``rdsReceiver``.

Four jobs:

**Find the pilot, and say how far it is from where A/53 puts it.** 8VSB
carries a small residual carrier 309.441 kHz above the lower edge of the
channel, which is 2.6906 MHz below the centre a receiver tunes to. It is a
bare spike in a flat haystack, so a windowed FFT finds it to a few hertz.

**Turn that one number into two corrections.** Two free-running crystals do
not agree, and a clock that is fast by some parts per million moves the
carrier *and* stretches the symbol clock by the same fraction. Correcting
only the carrier leaves the symbol clock wrong, and the decoder then
produces noise from a signal whose spectrum looks perfect - this cost real
time to find, and is why ``afc_correction`` returns both.

**Measure how open the eye is.** ``gr-dtv``'s equalizer emits soft symbols
on the ideal 8VSB grid, so the distance from each to its nearest level is
the error, and the ratio of signal power to that is MER.

**Say what is in the recovered stream.** A transport stream announces its
own programs in the PAT and PMT, so a few hundred bytes of parsing turns
"packets are arriving" into "two programs, MPEG-2 video on PID 0x0100".
"""

import time

import numpy as np

# A/53 part 2. Everything descends from the symbol rate.
SYMBOL_RATE = 4.5e6 / 286 * 684       # 10.762237 MS/s
CHANNEL_BW = 6.0e6
# The pilot sits 309.441 kHz above the lower channel edge, which is this far
# below the centre frequency. Same expression as the transmitter's rotator.
PILOT_OFFSET = -3e6 + (CHANNEL_BW - SYMBOL_RATE / 2) / 2      # -2.6906 MHz
# A/53 puts the threshold of visibility at 15.2 dB SNR.
THRESHOLD_OF_VISIBILITY_DB = 15.2
# The eight 8VSB levels, after the equalizer has normalised them.
VSB_LEVELS = np.array([-7.0, -5.0, -3.0, -1.0, 1.0, 3.0, 5.0, 7.0])


# --- the television channel plan -------------------------------------------
#
# Only the channel *centre* matters here: both this receiver and
# ``atscXmitter`` are tuned to it, not to the visual carrier.

def channel_center_mhz(channel):
    """Centre frequency of a US television channel, in MHz.

    Raises ValueError for the numbers that are not television channels -
    there is no channel 1, and 37 is reserved for radio astronomy.
    """
    n = int(channel)
    if 2 <= n <= 4:
        return 57.0 + 6.0 * (n - 2)          # 54-72 MHz
    if 5 <= n <= 6:
        return 79.0 + 6.0 * (n - 5)          # 76-88 MHz
    if 7 <= n <= 13:
        return 177.0 + 6.0 * (n - 7)         # 174-216 MHz
    if 14 <= n <= 36:
        return 473.0 + 6.0 * (n - 14)        # 470-608 MHz
    raise ValueError(f"{channel} is not a US television channel")


def channels():
    """Every valid channel as (number, centre MHz), low to high."""
    out = []
    for n in list(range(2, 14)) + list(range(14, 37)):
        out.append((n, channel_center_mhz(n)))
    return out


def channel_for_center(mhz, tolerance=0.05):
    """The channel whose centre this is, or None for a frequency off-plan."""
    for n, centre in channels():
        if abs(centre - mhz) <= tolerance:
            return n
    return None


def tv_channel_items():
    """The channel plan as ``(number, centre MHz, caption)``, for a combo box.

    Here rather than in either app so the transmitter and the receiver offer
    the same list, and free of Qt like the rest of this module.
    """
    return [(n, centre, f"{'VHF' if n <= 13 else 'UHF'} {n}  ({centre:g} MHz)")
            for n, centre in channels()]


# --- automatic frequency control -------------------------------------------

def pilot_offset_hz(samples, sample_rate, search=75e3):
    """How far the pilot is from where the standard puts it, in Hz.

    Returns ``(error_hz, prominence_db)``. The prominence is how far the
    peak stands above the median of the search window, which is what tells
    a pilot from the noise where there is no station: 8VSB data is flat, so
    a real pilot stands 10 dB or more proud of it.

    ``samples`` are complex baseband centred on the channel, as the radio
    delivers them. ``search`` bounds how large a clock error can be found:
    75 kHz is 140 ppm at 533 MHz, far past any crystal that is working at
    all, and narrow enough that data either side cannot be mistaken for the
    pilot.
    """
    x = np.asarray(samples, dtype=np.complex64)
    if x.size < 4096:
        return 0.0, 0.0
    n = 1 << int(np.floor(np.log2(x.size)))
    x = x[:n]
    # A window matters more than length here: the pilot is a bare spike a
    # few tens of dB above a flat haystack, and an unwindowed FFT's
    # sidelobes smear the haystack right across it.
    spec = np.abs(np.fft.fftshift(np.fft.fft(x * np.hanning(n)))) ** 2
    freqs = np.fft.fftshift(np.fft.fftfreq(n, 1.0 / sample_rate))

    band = np.abs(freqs - PILOT_OFFSET) < search
    if band.sum() < 8:
        return 0.0, 0.0
    f, s = freqs[band], spec[band]
    j = int(np.argmax(s))
    peak = f[j]
    if 0 < j < len(s) - 1:
        # Parabolic interpolation on the log magnitudes, so the answer is
        # not quantised to the bin spacing - at 12 MS/s and 2^21 samples a
        # bin is 5.7 Hz, and the fit gets well inside that.
        a, b, c = (np.log(s[j - 1] + 1e-30), np.log(s[j] + 1e-30),
                   np.log(s[j + 1] + 1e-30))
        denom = a - 2 * b + c
        if denom != 0:
            peak = f[j] + 0.5 * (a - c) / denom * (f[1] - f[0])
    median = float(np.median(s))
    prominence = 10 * np.log10(s[j] / median) if median > 0 else 0.0
    return float(peak - PILOT_OFFSET), float(prominence)


def afc_correction(error_hz, center_hz):
    """Turn a measured pilot error into the two corrections it implies.

    Returns ``(rotator_hz, clock_ratio)``: shift the baseband by
    ``rotator_hz`` and multiply the resampler's rate by ``clock_ratio``.

    The two are not independent, which is the whole point. A transmitter
    whose crystal is fast by a fraction d puts its carrier d*f_rf high
    *and* clocks its symbols d faster. Measured at baseband the pilot moves
    by d*(f_rf + PILOT_OFFSET) - the carrier carries it up, and stretching
    the baseband carries it back down by d times its own 2.69 MHz - so that
    is the frequency the ppm figure has to be taken against. Correcting the
    carrier alone leaves the symbol clock d fast, and the equalizer never
    converges: the spectrum looks perfect and the output is noise.
    """
    pilot_hz = float(center_hz) + PILOT_OFFSET
    if pilot_hz <= 0:
        return 0.0, 1.0
    ppm_fraction = float(error_hz) / pilot_hz
    return float(error_hz), 1.0 + ppm_fraction


class Afc:
    """Holds the correction and only moves it when a measurement earns it.

    The pilot measurement is good to a few hertz, so the loop does not need
    to creep: one measurement is enough to jump to. What it does need is to
    refuse to act on nonsense, because a receiver pointed at an empty
    channel measures the largest noise bin in the search window and would
    otherwise chase it around.
    """

    #: Below this the "pilot" is just the biggest bin in a flat window.
    #:
    #: Measured on a BB60D across eight UHF channels: empty ones read 11-15
    #: dB with the peak landing anywhere in the +-75 kHz window, a live
    #: broadcaster on RF 36 read 44.2 dB and the VSG60 on RF 24 read 46 dB.
    #: The gap is that wide because the FFT bin is 22 Hz: a carrier lands
    #: entirely in one bin while data and noise spread across all of them,
    #: which is worth about 54 dB and barely depends on how strong the
    #: signal is. So a decodable station reads 40-plus however weak it is,
    #: and 30 sits clear of both sides. At 8, which this was first, an empty
    #: channel reported a pilot and the app said "Pilot found, not
    #: decoding" where it should have said there was no signal at all.
    MIN_PROMINENCE_DB = 30.0
    #: Do not disturb a working lock for less than this.
    DEADBAND_HZ = 40.0

    def __init__(self, center_hz):
        self.center_hz = float(center_hz)
        self.error_hz = 0.0
        self.raw_hz = 0.0
        self.clock_ratio = 1.0
        self.prominence_db = 0.0
        self.locked = False

    def retune(self, center_hz):
        """Start again on a new channel; the old correction means nothing."""
        self.center_hz = float(center_hz)
        self.error_hz = 0.0
        self.clock_ratio = 1.0
        self.prominence_db = 0.0
        self.locked = False

    def update(self, samples, sample_rate):
        """Measure and take the correction. True if it changed."""
        raw, prominence = pilot_offset_hz(samples, sample_rate)
        self.prominence_db = prominence
        self.raw_hz = raw              # FM receiver: the reading, whatever was applied
        if prominence < self.MIN_PROMINENCE_DB:
            self.locked = False
            return False
        # The measurement is of the *uncorrected* stream, so it is the total
        # error every time rather than a residual to accumulate.
        self.locked = True
        if abs(raw - self.error_hz) < self.DEADBAND_HZ:
            return False
        self.error_hz, self.clock_ratio = afc_correction(raw, self.center_hz)
        return True

    @property
    def ppm(self):
        return (self.clock_ratio - 1.0) * 1e6


# --- how open the eye is ---------------------------------------------------

#: Below about this, a decision-directed MER stops telling the truth - see
#: ``mer_db``. Measured on synthetic symbols, not guessed.
MER_FLOOR_DB = 16.0


def mer_db(symbols):
    """Modulation error ratio of equalized 8VSB soft symbols, in dB.

    ``gr-dtv``'s equalizer emits symbols already on the ideal grid (mean
    absolute value 4.0, which is what equiprobable +-1, +-3, +-5, +-7
    gives), so each symbol's error is its distance to the nearest level and
    MER is 21 - the mean square of those levels - over the mean square
    error.

    **It reads optimistically once the eye starts closing, and cannot be
    compared against the 15.2 dB cliff.** Slicing to the nearest level is
    what every receiver's MER does, and it is the same thing as assuming
    every symbol was decided correctly; once noise starts pushing symbols
    past the halfway point the error to the *wrong* level is measured
    instead, which is smaller. Measured against known noise, this reads
    within 0.1 dB down to 22 dB, 19.0 for a true 18, 17.7 for a true 15 and
    16.6 for a true 12 - so it bottoms out around 16 whatever is really
    happening. Read it as "how open is the eye", and read the bad-packet
    rate for whether the picture is intact.
    """
    x = np.asarray(symbols, dtype=np.float64)
    if x.size == 0:
        return 0.0
    err = x - VSB_LEVELS[np.argmin(np.abs(x[:, None] - VSB_LEVELS[None, :]),
                                   axis=1)]
    mse = float(np.mean(err ** 2))
    if mse <= 0:
        return 99.0
    return float(10 * np.log10(float(np.mean(VSB_LEVELS ** 2)) / mse))


def mer_quality(mer):
    """A word for an MER reading, honest about where it stops meaning much."""
    if mer <= 0:
        return ''
    # Pitched against what this receiver actually reads, not against the
    # theoretical cliff: a software loopback with no noise at all tops out
    # near 24 dB, and off air a link running at 0.00% bad packets reads
    # 19.5-22.5. Calling that "closing" alarms people about a perfect
    # picture, which an earlier version of this did.
    if mer >= 21:
        return 'eye wide open'
    if mer >= 18:
        return 'eye open'
    return (f'near the floor - the slicer cannot see past about '
            f'{MER_FLOOR_DB:.0f} dB')


# --- what is in the stream -------------------------------------------------

STREAM_TYPES = {
    0x01: 'MPEG-1 video', 0x02: 'MPEG-2 video', 0x03: 'MPEG-1 audio',
    0x04: 'MPEG-2 audio', 0x06: 'private data', 0x0f: 'AAC audio',
    0x1b: 'H.264 video', 0x24: 'HEVC video', 0x81: 'AC-3 audio',
    0x86: 'SCTE-35', 0x87: 'E-AC-3 audio',
}

PID_PAT = 0x0000
PID_NULL = 0x1fff


class TsAnalyzer:
    """Counts packets and reads the programs out of a transport stream.

    Fed whatever bytes the decoder produces, in whatever sized pieces. It
    keeps no more than one packet of leftover, so it can run on a live
    stream forever.

    Everything it reports is cumulative; ``since()`` turns two snapshots
    into the rates that actually matter live, because a receiver's totals
    are dominated by however long it spent acquiring lock.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self._buf = b''
        self.packets = 0
        self.errors = 0          # transport_error_indicator set
        self.discontinuities = 0
        self.pids = {}           # pid -> packet count
        self.programs = {}       # program number -> {'pmt_pid', 'streams'}
        self._pmt_pids = {}      # pmt pid -> program number
        self._continuity = {}    # pid -> last continuity counter
        self._synced = False
        self.psip = Psip()       # FM receiver: names, now and next

    # -- feeding ---------------------------------------------------------

    def feed(self, data):
        """Take a chunk of transport stream bytes."""
        buf = self._buf + bytes(data)
        i = 0
        n = len(buf)
        while i + 188 <= n:
            if buf[i] != 0x47:
                # Resynchronise: look for the next sync byte that also has
                # one 188 bytes further on, so a 0x47 inside payload does
                # not drag the parser out of step.
                j = self._find_sync(buf, i, n)
                if j < 0:
                    i = max(i, n - 187)
                    break
                i = j
                continue
            self._packet(buf[i:i + 188])
            i += 188
        self._buf = buf[i:]

    @staticmethod
    def _find_sync(buf, start, end):
        i = start
        while True:
            i = buf.find(0x47, i + 1, end)
            if i < 0:
                return -1
            if i + 376 > end or buf[i + 188] == 0x47:
                return i

    def _packet(self, pkt):
        self.packets += 1
        pid = ((pkt[1] & 0x1f) << 8) | pkt[2]
        self.pids[pid] = self.pids.get(pid, 0) + 1
        if pkt[1] & 0x80:                      # transport_error_indicator
            self.errors += 1
            return                             # its contents are not to be trusted
        self._check_continuity(pid, pkt)
        if pid == PID_PAT:
            self._section(pkt, self._parse_pat)
        elif pid in self._pmt_pids:
            self._section(pkt, self._parse_pmt)
        elif pid in self.psip.pids:
            self.psip.packet(pid, pkt)

    def _check_continuity(self, pid, pkt):
        # Null packets are exempt - A/53 stuffing does not carry a counter,
        # and packets with no payload do not advance it either.
        if pid == PID_NULL or not (pkt[3] & 0x10):
            return
        cc = pkt[3] & 0x0f
        last = self._continuity.get(pid)
        if last is not None and cc != (last + 1) % 16:
            self.discontinuities += 1
        self._continuity[pid] = cc

    def _section(self, pkt, parse):
        """Hand a table section to a parser, if this packet starts one.

        Sections that span packets are skipped rather than reassembled: a
        PAT and the PMTs of a broadcast stream fit in one packet each, and
        they repeat every hundred milliseconds, so waiting for a whole one
        costs nothing and saves carrying reassembly state through errors.
        """
        if not (pkt[1] & 0x40):                # payload_unit_start_indicator
            return
        i = 4
        if pkt[3] & 0x20:                      # adaptation field present
            i += 1 + pkt[4]
        if i >= 188:
            return
        i += 1 + pkt[i]                        # pointer_field
        if i + 3 > 188:
            return
        length = ((pkt[i + 1] & 0x0f) << 8) | pkt[i + 2]
        end = i + 3 + length
        if end > 188:
            return
        parse(pkt[i:end])

    # -- tables ----------------------------------------------------------

    def _parse_pat(self, sec):
        if not sec or sec[0] != 0x00 or len(sec) < 8:
            return
        body = sec[8:-4]                       # past the header, before the CRC
        for k in range(0, len(body) - 3, 4):
            program = (body[k] << 8) | body[k + 1]
            pid = ((body[k + 2] & 0x1f) << 8) | body[k + 3]
            if program == 0:                   # network PID, not a program
                continue
            self._pmt_pids[pid] = program
            self.programs.setdefault(program, {'pmt_pid': pid, 'streams': {}})

    def _parse_pmt(self, sec):
        if not sec or sec[0] != 0x02 or len(sec) < 12:
            return
        program = (sec[3] << 8) | sec[4]
        info_len = ((sec[10] & 0x0f) << 8) | sec[11]
        k = 12 + info_len
        body = sec[:-4]                        # drop the CRC
        streams = {}
        while k + 5 <= len(body):
            stream_type = body[k]
            pid = ((body[k + 1] & 0x1f) << 8) | body[k + 2]
            es_len = ((body[k + 3] & 0x0f) << 8) | body[k + 4]
            streams[pid] = stream_type
            k += 5 + es_len
        entry = self.programs.setdefault(program, {'pmt_pid': None,
                                                   'streams': {}})
        entry['streams'] = streams

    # -- reporting -------------------------------------------------------

    def snapshot(self):
        return {
            'packets': self.packets,
            'errors': self.errors,
            'discontinuities': self.discontinuities,
            'pids': dict(self.pids),
            'programs': {p: dict(v) for p, v in self.programs.items()},
        }

    def describe_programs(self):
        """One human-readable line per program, or an empty list."""
        out = []
        for program in sorted(self.programs):
            streams = self.programs[program]['streams']
            if not streams:
                out.append(f"program {program}: (no PMT yet)")
                continue
            parts = [f"{STREAM_TYPES.get(t, f'type 0x{t:02x}')} "
                     f"on 0x{pid:04x}" for pid, t in sorted(streams.items())]
            out.append(f"program {program}: " + ", ".join(parts))
        return out


def since(before, after):
    """Rates over the interval between two snapshots.

    A receiver's totals are swamped by however long it took to lock - the
    loopback spends its first 5000 packets acquiring - so everything the
    user reads while it runs should be a difference, not a total.
    """
    packets = after['packets'] - before['packets']
    errors = after['errors'] - before['errors']
    return {
        'packets': packets,
        'errors': errors,
        'discontinuities': after['discontinuities'] - before['discontinuities'],
        'error_rate': (errors / packets) if packets else None,
    }


# --- PSIP: what the stations call their channels (FM receiver) --------------
#
# A/65. The PAT names programs by number only; the Terrestrial Virtual
# Channel Table (PID 0x1FFB) gives each its short name and the 32.1 style
# number a viewer knows, and the Event Information Tables, whose PIDs the
# Master Guide Table lists, say what is on now and next. A table can span
# packets, so unlike the PAT these are put back together, and each section's
# CRC-32 is checked: a corrupt name is worse than none.

PID_PSIP = 0x1FFB
GPS_EPOCH_UNIX = 315964800
GPS_UTC_LEAP_S = 18          # leap seconds since 1980, unchanged since 2017


_CRC_TABLE = []
for _i in range(256):
    _c = _i << 24
    for _ in range(8):
        _c = ((_c << 1) ^ 0x04C11DB7) if _c & 0x80000000 else _c << 1
        _c &= 0xFFFFFFFF
    _CRC_TABLE.append(_c)


def section_crc_ok(section):
    """The CRC-32 at the end of a table section checks out."""
    crc = 0xFFFFFFFF
    for b in section:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC_TABLE[((crc >> 24) ^ b) & 0xFF]
    return crc == 0


def _text(data):
    """A multiple_string_structure's first string, as ``(text, readable)``:
    not readable when it is Huffman-compressed (A/65's tables are not
    carried here), which is what a station that compresses its titles sends
    and an empty one is not."""
    try:
        if not data or data[0] < 1:
            return '', True
        k = 1 + 3
        segments = data[k]
        k += 1
        out = ''
        for _ in range(segments):
            compression, mode, size = data[k], data[k + 1], data[k + 2]
            chunk = data[k + 3:k + 3 + size]
            k += 3 + size
            if compression != 0:
                return '', False
            if mode == 0:
                out += chunk.decode('latin-1')
            elif mode == 0x3f:
                out += chunk.decode('utf-16-be', 'replace')
        return out.strip(), True
    except (IndexError, ValueError):
        return '', True


class Psip:
    """Reassembles the PSIP tables of a transport stream and keeps what
    they say: ``channels`` (program number -> name, major, minor, source
    id), ``events`` (source id -> up to the next few events, each
    ``(start unix s, seconds, title)``)."""

    def __init__(self):
        self.pids = {PID_PSIP}
        self.channels = {}
        self.events = {}
        self._eit_pids = {}      # pid -> EIT-k
        self._buf = {}           # pid -> bytearray of a section in progress

    def packet(self, pid, pkt):
        i = 4
        if pkt[3] & 0x20:
            i += 1 + pkt[4]
        if i >= 188 or not (pkt[3] & 0x10):
            return
        payload = pkt[i:]
        buf = self._buf.setdefault(pid, bytearray())
        if pkt[1] & 0x40:                       # a section starts here
            pointer = payload[0]
            tail, fresh = payload[1:1 + pointer], payload[1 + pointer:]
            if buf:
                buf += tail
                self._drain(pid, buf)
            buf.clear()
            buf += fresh
        elif buf:
            buf += payload
        else:
            return
        self._drain(pid, buf)

    def _drain(self, pid, buf):
        while len(buf) >= 3 and buf[0] != 0xFF:
            need = 3 + (((buf[1] & 0x0F) << 8) | buf[2])
            if len(buf) < need:
                return
            section = bytes(buf[:need])
            del buf[:need]
            if section_crc_ok(section):
                self._section(pid, section)
        if buf and buf[0] == 0xFF:
            buf.clear()                         # stuffing

    def _section(self, pid, s):
        try:
            if s[0] == 0xC7:
                self._mgt(s)
            elif s[0] == 0xC8:
                self._tvct(s)
            elif s[0] == 0xCB:
                self._eit(s)
        except IndexError:
            pass

    def _mgt(self, s):
        n = (s[9] << 8) | s[10]
        k = 11
        for _ in range(n):
            kind = (s[k] << 8) | s[k + 1]
            pid = ((s[k + 2] & 0x1F) << 8) | s[k + 3]
            desc = ((s[k + 9] & 0x0F) << 8) | s[k + 10]
            k += 11 + desc
            if 0x0100 <= kind <= 0x0101:        # EIT-0 (now), EIT-1 (next)
                self._eit_pids[pid] = kind - 0x0100
                self.pids.add(pid)

    def _tvct(self, s):
        n = s[9]
        k = 10
        for _ in range(n):
            name = s[k:k + 14].decode('utf-16-be', 'replace').rstrip('\x00 ')
            bits = (s[k + 14] << 16) | (s[k + 15] << 8) | s[k + 16]
            major, minor = (bits >> 10) & 0x3FF, bits & 0x3FF
            program = (s[k + 24] << 8) | s[k + 25]
            source = (s[k + 28] << 8) | s[k + 29]
            desc = ((s[k + 30] & 0x03) << 8) | s[k + 31]
            self.channels[program] = {'name': name, 'major': major,
                                      'minor': minor, 'source': source}
            k += 32 + desc

    def _eit(self, s):
        source = (s[3] << 8) | s[4]
        n = s[9]
        k = 10
        found = []
        for _ in range(n):
            start = (s[k + 2] << 24) | (s[k + 3] << 16) | (s[k + 4] << 8) | s[k + 5]
            length = ((s[k + 6] & 0x0F) << 16) | (s[k + 7] << 8) | s[k + 8]
            tlen = s[k + 9]
            title, readable = _text(s[k + 10:k + 10 + tlen])
            k += 10 + tlen
            desc = ((s[k] & 0x0F) << 8) | s[k + 1]
            k += 2 + desc
            unix = start + GPS_EPOCH_UNIX - GPS_UTC_LEAP_S
            found.append((unix, length, title, readable))
        if found:
            keep = {e[0]: e for e in self.events.get(source, [])}
            for e in found:
                keep[e[0]] = e
            # What has not ended yet, and the one running now: keeping only
            # the newest few dropped the current event on a busy guide.
            horizon = time.time() - 600
            self.events[source] = [e for e in sorted(keep.values())
                                   if e[0] + e[1] > horizon][:24]

    def now_next(self, source, unix_now):
        """(now, next) events of a source, each (start, seconds, title) or
        None, judged against the clock."""
        now = nxt = None
        for e in self.events.get(source, []):
            if e[0] <= unix_now < e[0] + e[1]:
                now = e
            elif e[0] > unix_now and (nxt is None or e[0] < nxt[0]):
                nxt = e
        return now, nxt
