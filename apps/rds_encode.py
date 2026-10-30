"""RDS / RBDS encoder - the transmit counterpart of ``rds_core``.

Builds the group sequence a receiver expects, serialises it to the 1187.5 bit/s
stream, and renders the 19 kHz pilot plus the 57 kHz RDS subcarrier as baseband
samples ready to be summed into an FM multiplex.

Kept free of GNU Radio and Qt so the whole chain can be checked offline: encode
groups here, hand the samples to ``rds_core`` and confirm what comes back is
what went in.

Two details matter more than they look:

* **The subcarriers must be phase-locked to the pilot.** Receivers recover the
  57 kHz carrier and the bit clock from the 19 kHz pilot (x3 and /16), so both
  are generated here from one sample counter rather than from separate
  oscillators that would drift apart.
* **Blocks carry an offset word, not a plain CRC.** The checkword is the CRC of
  the information bits XORed with a per-position offset word, which is what lets
  a receiver find block boundaries at all.
"""
import threading
import unicodedata
from datetime import date, datetime, timezone

import numpy as np
from scipy import signal

from apps.rds_core import OFFSET, POLY, RTPLUS_AID

BITRATE = 1187.5
SUBCARRIER_HZ = 57000.0
PILOT_HZ = 19000.0
#: Day 0 of the Modified Julian Date that group 4A counts days in.
MJD_EPOCH = date(1858, 11, 17).toordinal()


def system_clock():
    """The computer's local time, carrying its current UTC offset.

    Read afresh for every clock group, so the offset follows daylight saving.
    """
    return datetime.now().astimezone()


def checkword(info):
    """CRC of the 16 information bits, as the 10-bit checkword (no offset)."""
    reg = 0
    msg = (info & 0xFFFF) << 10
    for i in range(25, -1, -1):
        reg = (reg << 1) | ((msg >> i) & 1)
        if reg & 0x400:
            reg ^= (0x400 | POLY)
    return reg & 0x3FF


def make_block(info, offset_name):
    """A 26-bit block: 16 information bits plus checkword XOR offset word."""
    return ((info & 0xFFFF) << 10) | (checkword(info) ^ OFFSET[offset_name])


def _chars(text, size):
    return list(text.ljust(size)[:size])


#: Characters with no plain-ASCII decomposition, spelt out rather than lost.
_SPELT = str.maketrans({
    '\u2018': "'", '\u2019': "'", '\u201a': "'", '\u201b': "'", '\u2032': "'",
    '\u201c': '"', '\u201d': '"', '\u201e': '"', '\u2033': '"',
    '\u2010': '-', '\u2011': '-', '\u2012': '-', '\u2013': '-', '\u2014': '-',
    '\u2026': '...', '\u00a0': ' ', '\t': ' ', '\n': ' ', '\r': ' ',
    '\u00df': 'ss', '\u00e6': 'ae', '\u00c6': 'AE', '\u0153': 'oe', '\u0152': 'OE',
    '\u00f8': 'o', '\u00d8': 'O', '\u0111': 'd', '\u0110': 'D',
    '\u0142': 'l', '\u0141': 'L', '\u00fe': 'th', '\u00de': 'Th',
})


def _fit(text, width):
    """``text`` cut to ``width`` characters, at a word if one is near the end."""
    if len(text) <= width:
        return text
    cut = text[:width + 1].rsplit(' ', 1)[0].rstrip(' ,;:-')
    return cut if len(cut) >= width * 2 // 3 else text[:width].rstrip()


def rds_text(text):
    """Text as it can go out: printable ASCII, one byte a character.

    Each character is packed into a byte with ``ord()``, and a song's tags
    are full of characters that do not fit one: the curly apostrophe in
    "Livin\u2019" is U+2019. Sent as they were, measured through the encoder
    and decoder here, it arrived as a space and "M\u00f6tley Cr\u00fce" as
    "M tley Cr e" - the blocks all clean, the letters silently gone. The
    Latin-1 accents do fit a byte, but RDS has its own table above 127 (IEC
    62106 annex E), so a car radio would show them as other letters. Plain
    ASCII reads the same everywhere. So curly quotes and dashes become
    straight ones, accents come off their letters (Motley Crue), a few
    letters with no accent to remove are spelt out, and whatever is left is
    dropped rather than sent wrong.
    """
    text = unicodedata.normalize('NFKD', (text or '').translate(_SPELT))
    return ''.join(c for c in text if ' ' <= c <= '~')


def paginate(text, width=64):
    """Split a long message into RadioText-sized pages, breaking on words."""
    words, pages, line = text.split(), [], ''
    for word in words:
        candidate = f"{line} {word}".strip()
        if len(candidate) <= width:
            line = candidate
        else:
            if line:
                pages.append(line)
            # A single word longer than a page has to be cut somewhere.
            while len(word) > width:
                pages.append(word[:width])
                word = word[width:]
            line = word
    if line:
        pages.append(line)
    return pages or ['']


class RdsEncoder:
    """An endless, live-editable RDS group sequence.

    Setters are safe to call from a UI thread while the flowgraph pulls groups.

    ``clock`` is a callable returning the current time as a timezone-aware
    datetime (``system_clock`` for the computer's own). With one, a group 4A
    goes out as soon as the stream starts and then at the start of every
    minute; without one, no clock time is sent at all, which is what the NRSC
    asks of a station that has no reliable time source.
    """

    def __init__(self, pi=0x4CA1, ps='GNURADIO', radiotext='', pty=0,
                 tp=0, ta=0, ms=1, station_name=None, clock=None):
        self._lock = threading.Lock()
        self.clock = clock
        self.bits_sent = 0                # stream position, at 1187.5 bit/s
        self._ct_minute = None            # the minute last sent as group 4A
        self._ct_sent = None              # that group, as rds_core reads it
        self.pi = pi
        self.pty = pty
        self.tp = tp
        self.ta = ta
        self.ms = ms                      # 1 = music, 0 = speech
        self._ps = _chars(ps, 8)
        self._rt = _chars(radiotext, 64)
        self._rt_ab = 0
        self._rtplus = None               # tags into the RadioText on air now
        # The song on air as (its RadioText line, the tags into that line). Its
        # RT+ fields are an "item": receivers keep them across other RadioText
        # messages until the item toggle bit flips, which happens only when the
        # song changes (NRSC-G300-C section 6.10).
        self._item = None
        self._item_toggle = 0
        # RadioText typed in by hand. What the dialog gave counts as typed,
        # or set_now_playing would put the song's line in its place unsent.
        self._message = rds_text(radiotext)[:64] if radiotext.strip() else None
        self.station_name = station_name
        self._seq_index = 0
        self._ps_seg = 0
        self._rt_seg = 0
        self._rt_sent = -1                # furthest segment of the text sent
        self._pages = []            # RadioText pages in rotation: (text, tags)
        self._page_i = 0
        self._page_repeats = 1
        self._page_cycles = 0

    #: Complete passes of each page before the next takes its turn.
    ROTATE_REPEATS = 3

    # -- live edits --------------------------------------------------------
    def set_ps(self, text):
        with self._lock:
            self._ps = _chars(rds_text(text), 8)

    def set_radiotext(self, text):
        """Put a message in RadioText.

        While a song is on air the message takes turns with the song's own
        line, so a receiver tuning in at any moment still learns what is
        playing, and Now Playing stays on the song throughout. Sending the
        song's line itself, or nothing, goes back to the song alone.
        """
        text = rds_text(text)[:64]
        with self._lock:
            song = self._item[0] if self._item else None
            if not text.strip() or text.rstrip() == song:
                self._message = None
                if self._item:
                    self._set_rt_locked(*self._item)
                else:
                    self._set_rt_locked(text)
                return
            self._message = text
            if self._item:
                self._rotate_locked([(text, None), self._item])
            else:
                self._set_rt_locked(text)

    def set_paragraph(self, text, repeats=1, number_pages=True):
        """Send a message longer than RadioText as a sequence of pages.

        RadioText holds 64 characters, so anything longer is paged: each page is
        transmitted complete, then the A/B flag toggles to tell receivers to
        clear before the next arrives. Pages advance on *segments sent* rather
        than a clock, so a page is never replaced halfway out.

        Pages are numbered "2/5 " by default. The cycle repeats forever, so
        anyone tuning in mid-paragraph receives the pages rotated - every page
        arrives, but not in reading order. The numbering is what lets them be
        reassembled without waiting to spot where the cycle wraps.
        """
        text = rds_text(text)
        with self._lock:
            pages = paginate(text, 64)
            if number_pages and len(pages) > 1:
                # The prefix eats into the page width, which can add a page,
                # which can widen the prefix. Settle it rather than assume.
                total = len(pages)
                for _ in range(4):
                    pages = paginate(text, 64 - len(f"{total}/{total} "))
                    if len(pages) == total:
                        break
                    total = len(pages)
                pages = [f"{i+1}/{total} {p}" for i, p in enumerate(pages)]
            self._message = None
            # A song on air keeps its line in the rotation, as with a message.
            self._rotate_locked([(p, None) for p in pages]
                                + ([self._item] if self._item else []),
                                repeats=max(1, int(repeats)))

    def _rotate_locked(self, pages, repeats=None):
        """Send ``pages`` - (text, tags) pairs - in turn, for as long as asked."""
        self._pages = list(pages)
        self._page_i = 0
        self._page_repeats = repeats or self.ROTATE_REPEATS
        self._page_cycles = 0
        self._rt_seg = 0
        self._load_page_locked()
        self._rt_ab ^= 1                  # receivers clear before the first page

    def _load_page_locked(self):
        page, self._rtplus = self._pages[self._page_i]
        # A carriage return ends a short page, so receivers do not leave the
        # tail of a longer previous page on screen.
        self._rt = _chars(page + '\r' if len(page) < 64 else page, 64)
        self._rt_sent = -1

    def _set_rt_locked(self, text, tags=None):
        text = text[:64]
        self._pages = []            # a single message ends any rotation
        self._rt = _chars(text, 64)
        # Start the new text from its first segment. Carrying on mid-pass sent
        # padding first, identical to the old message's, so a receiver saw no
        # change at all while tags for the new text were already arriving.
        self._rt_seg = 0
        self._rt_sent = -1
        # Tags must be offsets into this very string. Any others would slice
        # it at the wrong points and advertise half a word as the title -
        # exactly the fault a local station ships.
        self._rtplus = tags
        # Toggling the A/B flag is how a receiver is told to clear the old
        # message rather than overwrite it character by character.
        self._rt_ab ^= 1
        self._rt_len = len(text)

    def set_now_playing(self, artist, title):
        """Start a new song: its RadioText line and the RT+ tags into it.

        RT+ tags are offsets into the RadioText, so they are computed from the
        very string that gets sent, or a receiver slices the wrong characters -
        exactly the bug seen off-air on a local station. A new song also flips
        the RT+ item toggle bit, which tells receivers to drop the last song's
        title and artist. A typed message stays, taking turns with the new line.
        """
        # Converted before the tags are counted, since they are offsets.
        artist, title = rds_text(artist).strip(), rds_text(title).strip()
        # And fitted into RadioText's 64 characters, for the same reason. A
        # longer line was cut to 64 while its tags still pointed past the
        # end, and a receiver took neither: measured, an 84-character line
        # of artist and title arrived as no artist and no title at all, while
        # the On Air box here claimed the whole title.
        if artist and title:
            artist = _fit(artist, 30)
            title = _fit(title, 64 - len(artist) - 3)
        else:
            title = _fit(title, 64)
        with self._lock:
            if artist and title:
                # Title first: an RT+ group's second tag has a 5-bit length,
                # 32 characters at most, and the first a 6-bit one. With the
                # artist first, any title over 32 characters went out with
                # its length cut to the low five bits - a 39-character title
                # arrived as "Symphon", and the receiver rightly refused half
                # a word. Unseen while the artist was always empty. An artist
                # is fitted to 30 above, so it always fits the short one.
                item = (f"{artist} - {title}",
                        ((1, len(artist) + 3, len(title) - 1),
                         (4, 0, len(artist) - 1)))
            elif title:
                item = (title, ((1, 0, len(title) - 1), (0, 0, 0)))
            else:
                item = None
            self._item = item
            self._item_toggle ^= 1
            if item and self._message:
                self._rotate_locked([(self._message, None), item])
            elif item:
                self._set_rt_locked(*item)
            elif self._message:
                self._set_rt_locked(self._message)
            else:
                self._pages = []
                self._rtplus = None

    def snapshot(self):
        with self._lock:
            now_playing = None
            if self._item:
                text, tags = self._item
                names = {1: 'title', 4: 'artist'}
                now_playing = {names[c]: text[s:s + l + 1]
                               for c, s, l in tags if c in names}
            return {
                'pi': self.pi, 'pi_hex': f"0x{self.pi:04X}",
                'ps': ''.join(self._ps), 'radiotext': ''.join(self._rt).rstrip(),
                'pty': self.pty, 'tp': self.tp, 'ta': self.ta,
                'rtplus': self._rtplus,           # tags into the page on air
                'now_playing': now_playing,       # the song, whatever page is up
                'message': self._message,
                'item_toggle': self._item_toggle,
                'clock': self._ct_sent,
            }

    # -- group construction ------------------------------------------------
    def _common_b(self, gtype, version_b=0):
        return ((gtype & 0xF) << 12 | (version_b & 1) << 11
                | (self.tp & 1) << 10 | (self.pty & 0x1F) << 5)

    def _group_0a(self):
        seg = self._ps_seg & 0x3
        self._ps_seg = (self._ps_seg + 1) & 0x3
        b = self._common_b(0) | (self.ta & 1) << 4 | (self.ms & 1) << 3 | seg
        # 0xE0E0 is the "no alternative frequencies" filler.
        c = 0xE0E0
        d = (ord(self._ps[seg * 2]) << 8) | ord(self._ps[seg * 2 + 1])
        return [make_block(self.pi, 'A'), make_block(b, 'B'),
                make_block(c, 'C'), make_block(d, 'D')]

    def _group_2a(self):
        seg = self._rt_seg & 0xF
        # Stop at the segment holding the carriage return: the rest of a short
        # page is padding, and skipping it gets each page across sooner.
        end = ''.join(self._rt).find('\r')
        last = end // 4 if end >= 0 else 15
        self._rt_seg = 0 if seg >= last else seg + 1
        b = self._common_b(2) | (self._rt_ab & 1) << 4 | seg
        c = (ord(self._rt[seg * 4]) << 8) | ord(self._rt[seg * 4 + 1])
        d = (ord(self._rt[seg * 4 + 2]) << 8) | ord(self._rt[seg * 4 + 3])
        group = [make_block(self.pi, 'A'), make_block(b, 'B'),
                 make_block(c, 'C'), make_block(d, 'D')]
        # How far into this text has gone out - noted before a page change
        # below starts the count again for the next page.
        self._rt_sent = max(self._rt_sent, seg)
        if self._rt_seg == 0 and self._pages:
            # A whole page has just gone out; move on once it has been sent
            # the requested number of times, toggling A/B so receivers clear.
            self._page_cycles += 1
            if self._page_cycles >= self._page_repeats:
                self._page_cycles = 0
                self._page_i = (self._page_i + 1) % len(self._pages)
                self._load_page_locked()
                self._rt_ab ^= 1
        return group

    def _group_3a(self):
        """Announce that group 12A carries RadioText+."""
        b = self._common_b(3) | ((12 << 1) | 0)
        return [make_block(self.pi, 'A'), make_block(b, 'B'),
                make_block(0x0000, 'C'), make_block(RTPLUS_AID, 'D')]

    def _group_12a(self):
        # While the song's own line is on air the tags point into it - but only
        # once every segment they point into has gone out, so no receiver
        # meets a tag before the text it slices. While a typed message takes
        # its turn the tags are empty, and the unchanged item toggle tells
        # receivers to keep the song they already have.
        tags = self._rtplus
        if tags and any(c and (s + l) // 4 > self._rt_sent for c, s, l in tags):
            tags = None
        (t1, s1, l1), (t2, s2, l2) = tags or ((0, 0, 0), (0, 0, 0))
        running = 1
        toggle = self._item_toggle & 1
        bits = ((toggle & 1) << 36 | (running & 1) << 35
                | (t1 & 0x3F) << 29 | (s1 & 0x3F) << 23 | (l1 & 0x3F) << 17
                | (t2 & 0x3F) << 11 | (s2 & 0x3F) << 5 | (l2 & 0x1F))
        b = self._common_b(12) | ((bits >> 32) & 0x1F)
        c = (bits >> 16) & 0xFFFF
        d = bits & 0xFFFF
        return [make_block(self.pi, 'A'), make_block(b, 'B'),
                make_block(c, 'C'), make_block(d, 'D')]

    def _group_4a(self, now):
        """Clock time and date.

        The hour and minute are UTC, not local time, with the local offset
        alongside in half hours: the receiver adds the two. The date is a
        Modified Julian Date, 17 bits split across blocks B and C.
        """
        utc = now.astimezone(timezone.utc)
        offset = now.utcoffset().total_seconds() / 3600
        half_hours = int(round(abs(offset) * 2))
        mjd = utc.toordinal() - MJD_EPOCH
        b = self._common_b(4) | ((mjd >> 15) & 0x3)
        c = ((mjd & 0x7FFF) << 1) | (utc.hour >> 4)
        d = ((utc.hour & 0xF) << 12 | utc.minute << 6
             | (offset < 0) << 5 | (half_hours & 0x1F))
        self._ct_sent = {
            'year': utc.year, 'month': utc.month, 'day': utc.day,
            'hour': utc.hour, 'minute': utc.minute,
            'utc_offset_hours': (-1 if offset < 0 else 1) * half_hours / 2.0,
        }
        return [make_block(self.pi, 'A'), make_block(b, 'B'),
                make_block(c, 'C'), make_block(d, 'D')]

    #: Roughly the mix a real station sends: PS often, RadioText steadily, and
    #: the RT+ announcement and tags sprinkled in.
    SEQUENCE = ('0A', '0A', '2A', '0A', '2A', '0A', '2A', '3A', '0A', '2A',
                '0A', '12A')

    def next_group(self):
        with self._lock:
            group = self._clock_group_due()
            if group is None:
                group = self._scheduled_group()
            self.bits_sent += 104
            return group

    def _clock_group_due(self):
        """Group 4A if the minute has turned since the last one, else None.

        Checked ahead of every group, so the clock leaves the encoder within
        one group (88 ms) of the minute edge, which is when the standard wants
        it - the flowgraph and the radio's buffers then add their own delay.
        """
        if self.clock is None:
            return None
        now = self.clock()
        if now.tzinfo is None:
            now = now.astimezone()        # naive: the computer's local time
        minute = now.replace(second=0, microsecond=0)
        if minute == self._ct_minute:
            return None
        self._ct_minute = minute
        return self._group_4a(now)

    def _scheduled_group(self):
        for _ in range(len(self.SEQUENCE)):
            kind = self.SEQUENCE[self._seq_index % len(self.SEQUENCE)]
            self._seq_index += 1
            if kind in ('3A', '12A') and not self._item:
                continue                          # no song to tag
            return {'0A': self._group_0a, '2A': self._group_2a,
                    '3A': self._group_3a, '12A': self._group_12a}[kind]()
        return self._group_0a()

    def next_bits(self):
        """104 bits: one group of four 26-bit blocks, most significant first."""
        out = np.empty(104, dtype=np.uint8)
        for i, blk in enumerate(self.next_group()):
            for j in range(26):
                out[i * 26 + j] = (blk >> (25 - j)) & 1
        return out


class RdsSubcarrier:
    """Renders pilot + RDS subcarrier samples at an arbitrary sample rate.

    Both come from one sample counter, so the 57 kHz carrier stays exactly three
    times the pilot and the bit clock exactly a sixteenth of it - which is what
    a receiver relies on to demodulate coherently.
    """

    def __init__(self, encoder, rate, rds_injection=0.05, pilot_level=0.09,
                 shaping_hz=2400.0, ntaps=127):
        self.enc = encoder
        self.fs = float(rate)
        self.rds_injection = rds_injection
        self.pilot_level = pilot_level
        self._n = 0                       # absolute sample index
        self._bits = np.zeros(0, dtype=np.uint8)
        self._bit0 = 0                    # absolute index of self._bits[0]
        self._last_tx = 0                 # differential encoder state
        self._shape = signal.firwin(ntaps, shaping_hz, fs=self.fs)
        self._zi = np.zeros(ntaps - 1)

    def _fill_to(self, abs_index):
        """Ensure the buffer holds every bit up to and including ``abs_index``."""
        while self._bit0 + len(self._bits) <= abs_index:
            data = self.enc.next_bits()
            # Differential encoding: send the running XOR, so a receiver that
            # is 180 degrees out still recovers the right data.
            tx = np.empty_like(data)
            state = self._last_tx
            for i, bit in enumerate(data):
                state ^= int(bit)
                tx[i] = state
            self._last_tx = state
            self._bits = np.concatenate([self._bits, tx])

    def generate(self, count):
        """Just the pilot + RDS sum, for callers that need nothing else."""
        return self.generate_all(count)[0]

    def generate_all(self, count):
        """Return (pilot + RDS, 38 kHz stereo carrier) for the next samples.

        The stereo carrier comes from the same sample counter as the pilot, so
        it stays at exactly twice the pilot frequency just as RDS stays at
        exactly three times it. Separate oscillators would drift apart, and a
        receiver recovering both from the pilot would slowly lose stereo.
        """
        n = np.arange(self._n, self._n + count, dtype=np.float64)
        t = n / self.fs

        # Manchester: each bit is a positive half followed by a negative half
        # (or the reverse), which puts no energy at the subcarrier frequency.
        bit_pos = t * BITRATE
        # Track the buffer's absolute bit index rather than inferring it from
        # the sample arithmetic: a chunk boundary landing exactly on a bit
        # boundary otherwise slips the whole stream by one bit, which corrupts
        # blocks even with no noise anywhere.
        first_bit = int(np.floor(bit_pos[0]))
        last_bit = int(np.floor(bit_pos[-1]))
        drop = first_bit - self._bit0
        if drop > 0:
            self._bits = self._bits[drop:]
            self._bit0 += drop
        self._fill_to(last_bit + 1)
        idx = np.floor(bit_pos).astype(np.int64) - self._bit0
        idx = np.clip(idx, 0, len(self._bits) - 1)
        half = (bit_pos - np.floor(bit_pos)) < 0.5
        symbol = np.where(self._bits[idx] > 0, 1.0, -1.0)
        manchester = np.where(half, symbol, -symbol)

        shaped, self._zi = signal.lfilter(self._shape, 1.0, manchester,
                                          zi=self._zi)
        rds = shaped * np.cos(2 * np.pi * SUBCARRIER_HZ * t)
        pilot = np.cos(2 * np.pi * PILOT_HZ * t)

        carrier38 = np.cos(2 * (2 * np.pi * PILOT_HZ) * t)
        self._n += count
        subcarriers = (self.pilot_level * pilot
                       + self.rds_injection * rds).astype(np.float32)
        return subcarriers, carrier38.astype(np.float32)
