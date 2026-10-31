"""RDS / RBDS decoding core — DSP back end and protocol layer.

Kept free of GNU Radio and Qt so it can be exercised offline against a recorded
capture (see scripts/test_rds_core.py) and reused by the live receiver app.

Signal chain (the parts above the FM demodulator):

    MPX ──▶ mix down by the 57 kHz subcarrier ──▶ low-pass ──▶ integrate-and-dump
                    ▲                                              │
              3 × pilot phase                                      ▼
                                                        biphase symbol ──▶ bits

The 57 kHz RDS subcarrier is locked to the third harmonic of the 19 kHz stereo
pilot, and the 1187.5 bit/s clock is the pilot divided by 16. So one PLL lock on
the pilot yields both the carrier phase and the symbol timing, coherently — no
separate carrier or clock recovery loop is needed.

Bits then run through the (26,16) shortened cyclic code: offset words mark which
of the four blocks in a group we are looking at, and the code corrects any
single error burst up to 5 bits long.
"""
import collections
from datetime import datetime, timedelta

import numpy as np
from scipy import signal

# --------------------------------------------------------------------------
# Block-level coding
# --------------------------------------------------------------------------
# Offset words are added into the checkbits; which one a block carries tells us
# its position within the group (and distinguishes C from C').
OFFSET = {'A': 0x0FC, 'B': 0x198, 'C': 0x168, "C'": 0x350, 'D': 0x1B4}
POLY = 0x5B9  # x^10 + x^8 + x^7 + x^5 + x^4 + x^3 + 1


def syndrome(block26):
    """10-bit syndrome of a 26-bit block under the RDS shortened cyclic code."""
    reg = 0
    for i in range(25, -1, -1):
        reg = (reg << 1) | ((block26 >> i) & 1)
        if reg & 0x400:
            reg ^= (0x400 | POLY)
    return reg & 0x3FF


SYN_OFF = {name: syndrome(off) for name, off in OFFSET.items()}


def _build_error_table():
    """syndrome(error) -> error vector, for every burst of length <= 5 bits."""
    table = {}
    for w in range(26):
        for pat in range(1, 32):
            err = 0
            for b in range(5):
                if pat & (1 << b) and w + b < 26:
                    err |= 1 << (w + b)
            if not err:
                continue
            s = syndrome(err)
            if s not in table or bin(err).count('1') < bin(table[s]).count('1'):
                table[s] = err
    return table


ERR_TABLE = _build_error_table()


def correct_block(block26, offset_name):
    """Return (16-bit message, ok). Corrects single bursts up to 5 bits."""
    s = syndrome(block26) ^ SYN_OFF[offset_name]
    if s == 0:
        return (block26 >> 10) & 0xFFFF, True
    err = ERR_TABLE.get(s)
    if err is not None:
        return ((block26 ^ err) >> 10) & 0xFFFF, True
    return (block26 >> 10) & 0xFFFF, False


# --------------------------------------------------------------------------
# Program type tables and the US call-sign mapping
# --------------------------------------------------------------------------
PTY_RBDS = [
    "None", "News", "Information", "Sports", "Talk", "Rock", "Classic Rock",
    "Adult Hits", "Soft Rock", "Top 40", "Country", "Oldies", "Soft",
    "Nostalgia", "Jazz", "Classical", "Rhythm and Blues", "Soft R&B",
    "Foreign Language", "Religious Music", "Religious Talk", "Personality",
    "Public", "College", "Spanish Talk", "Spanish Music", "Hip-Hop",
    "Unassigned", "Unassigned", "Weather", "Emergency Test", "Emergency",
]

PTY_RDS = [
    "None", "News", "Current Affairs", "Information", "Sport", "Education",
    "Drama", "Culture", "Science", "Varied", "Pop Music", "Rock Music",
    "Easy Listening", "Light Classical", "Serious Classical", "Other Music",
    "Weather", "Finance", "Children's Programmes", "Social Affairs",
    "Religion", "Phone In", "Travel", "Leisure", "Jazz Music", "Country Music",
    "National Music", "Oldies Music", "Folk Music", "Documentary",
    "Alarm Test", "Alarm",
]


#: Open Data Application id for RadioText+, and its content type codes.
#: NRSC-G300-C recommends receivers read RT+ StationName.Short rather than
#: back-calculating a call sign from the PI, which frequently does not encode
#: one at all.
RTPLUS_AID = 0x4BD7
TMC_AID = 0xCD46

RTPLUS_CLASSES = {
    1: 'title', 2: 'album', 3: 'tracknumber', 4: 'artist', 5: 'composition',
    6: 'movement', 7: 'conductor', 8: 'composer', 9: 'band', 10: 'comment',
    11: 'genre', 12: 'news', 13: 'news.local', 14: 'stockmarket', 15: 'sport',
    16: 'lottery', 17: 'horoscope', 18: 'daily_diversion', 19: 'health',
    20: 'event', 21: 'scene', 22: 'cinema', 23: 'tv', 24: 'date_time',
    25: 'weather', 26: 'traffic', 27: 'alarm', 28: 'advertisement', 29: 'url',
    30: 'other', 31: 'stationname.short', 32: 'stationname.long',
    33: 'programme.now', 34: 'programme.next', 35: 'programme.part',
    36: 'programme.host', 37: 'programme.editorial_staff',
    38: 'programme.frequency', 39: 'programme.homepage',
    40: 'programme.subchannel', 41: 'phone.hotline', 42: 'phone.studio',
    43: 'phone.other', 44: 'sms.studio', 45: 'sms.other', 46: 'email.hotline',
    47: 'email.studio', 48: 'email.other', 49: 'mms.other', 50: 'chat',
    51: 'chat.centre', 52: 'vote.question', 53: 'vote.centre',
}


def pi_to_callsign(pi):
    """Best-effort US call sign from a PI code.

    Only a hint: plenty of US stations transmit a PI that does not follow the
    call-sign formula at all (98.7 WMZQ sends 0x16F2, which decodes to nonsense),
    so callers should always show the PI itself as the real identifier.
    """
    if pi is None:
        return None
    if 0x1000 <= pi <= 0x54A7:
        base, first = pi - 0x1000, 'K'
    elif 0x54A8 <= pi <= 0x994F:
        base, first = pi - 0x54A8, 'W'
    else:
        return None
    letters = []
    for _ in range(3):
        letters.append(chr(ord('A') + base % 26))
        base //= 26
    return first + ''.join(reversed(letters))


def callsign_candidates(pi):
    """Call signs a PI might stand for, including high-nibble variants.

    US stations often transmit a PI whose top nibble has been altered from the
    value the call-sign formula gives - carrying traffic data (TMC) is one
    documented reason, shared-PI simulcasts and factory defaults are others - so
    the direct mapping comes out as nonsense. 98.7 WMZQ sends 0x16F2 where the
    formula gives 0x76F2, differing in exactly that nibble.

    Restoring each possible nibble recovers the real call sign, but *every*
    variant yields some plausible-looking letters, so a candidate is only worth
    believing when something else corroborates it - see
    ``RdsProtocol.confirmed_callsign``, which checks them against the station's
    own PS and RadioText.
    """
    seen, out = set(), []
    for nibble in range(0, 16):
        alt = (pi & 0x0FFF) | (nibble << 12)
        call = pi_to_callsign(alt)
        if call and call not in seen:
            seen.add(call)
            out.append(call)
    return out


def clock_text(clock):
    """A group 4A reading as local time, e.g. '2026-09-12 16:28 (UTC-4)'.

    The group carries UTC and the local offset separately, and the receiver is
    left to add them. Printing the UTC fields beside "(UTC-4)" reads four hours
    out, and the date can differ too - 00:28 UTC is still yesterday evening in
    the US. Returns None when there is no reading.
    """
    if not clock:
        return None
    local = (datetime(clock['year'], clock['month'], clock['day'],
                      clock['hour'], clock['minute'])
             + timedelta(hours=clock['utc_offset_hours']))
    return f"{local:%Y-%m-%d %H:%M} (UTC{clock['utc_offset_hours']:+g})"


class _TextField:
    """The live contents of a character field such as PS or RadioText.

    Characters are taken as they arrive, which is what a car radio does. Two
    real-world behaviours make anything cleverer counterproductive: many US
    stations scroll a message through the 8-character PS field, and stations
    commonly alternate between two RadioText messages without toggling the A/B
    flag that is supposed to announce a change. Averaging over time therefore
    blends separate messages into gibberish, and waiting for two consecutive
    receptions to agree never commits anything at all when the text alternates.

    Only blocks that passed (or were repaired by) the CRC reach this class, but
    a repair is no proof. The (26,16) code maps almost every syndrome to *some*
    correction, so a block whose error burst is too long comes back "corrected"
    and wrong rather than rejected: 'Tyler' as 'Eyler', a space as '&'. Off air
    that happens several times a minute. Characters from a segment that cannot
    be believed yet (see ``RdsProtocol._believed``) are therefore provisional:
    one fills a position nothing has written yet, and stays until a believed
    segment replaces it or its page comes round again. It never replaces a
    believed character, nor another provisional one - a repair that came out
    right must not be overwritten by the next one that came out wrong.
    """

    def __init__(self, size):
        self.size = size
        self.committed = [' '] * size
        # Positions this message has sent in segments that could be believed.
        # Cleared with the text, so it always describes the message on display
        # and never the one before.
        self.written = set()
        self.provisional = set()

    def put(self, pos, char, trusted=True):
        if not 0 <= pos < self.size:
            return
        if trusted:
            self.committed[pos] = char
            self.written.add(pos)
            self.provisional.discard(pos)
        elif pos not in self.written and pos not in self.provisional:
            self.committed[pos] = char
            self.provisional.add(pos)

    def clear(self):
        self.committed = [' '] * self.size
        self.written = set()
        self.provisional = set()

    def drop_provisional(self):
        """Forget every character no believed segment has confirmed.

        Called when a page comes round again under its flag. A short page stops
        at its carriage return, so a wrong repair that landed past it is never
        overwritten: with the page kept from turn to turn, off air that put
        "#    @8" after the song line until the carriage return itself arrived
        believed. Believed characters stay - keeping those is why the page is
        kept at all.
        """
        for pos in self.provisional:
            self.committed[pos] = ' '
        self.provisional = set()

    def differs(self, pos, chars, minimum=2):
        """True if this segment contradicts what the message already sent.

        A segment that repeats carries the same characters, so a disagreement
        means the station has moved on to a different message - which is the
        only warning some of them give (see the A/B flag note in
        ``RdsProtocol._decode_group``).

        Ask it only about a segment whose blocks arrived clean; it compares only
        against characters that did too. Otherwise a wrongly corrected block
        reads as a new message and blanks the display for the four seconds a
        refill takes - and so does the clean pass that repairs it. Measured on
        encoded bursts at 96.6% blocks good, that kept the text wrong on screen
        nearly two thirds of the time, and short or blank a fifth of it.
        ``minimum`` stays at two characters as a second guard, against an error
        the syndrome cannot see.
        """
        n = sum(pos + j in self.written and self.committed[pos + j] != ch
                for j, ch in enumerate(chars))
        return n >= minimum

    def range_written(self, start, end):
        """True if every position in [start, end) came clean from this message."""
        if start < 0 or end > self.size or end <= start:
            return False
        return all(p in self.written for p in range(start, end))

    def text(self):
        """The field as displayable characters, keeping absolute positions.

        RT+ offsets index into this, so nothing may shift.
        """
        return ''.join(c if 32 <= ord(c) < 127 else ' ' for c in self.committed)

    def message(self):
        """Text up to the carriage return that marks the end of a message.

        Stations really do send it - 99.5 puts one at position 45 - and without
        it a short message leaves the tail of a previous longer one on display.
        The split has to happen before unprintables become spaces, or there is
        no carriage return left to find.
        """
        head = ''.join(self.committed).split('\r')[0]
        return ''.join(c if 32 <= ord(c) < 127 else ' ' for c in head).rstrip()


class RdsProtocol:
    """Consume a bitstream: sync to groups, correct errors, decode fields."""

    def __init__(self, region='RBDS', drop_after=6):
        self.region = region
        self.drop_after = drop_after
        self._bits = collections.deque()
        self._synced = False
        self._miss = 0
        self.pi_votes = collections.Counter()
        self.ps = _TextField(8)
        # One RadioText buffer per A/B flag value. The flag announces "new
        # message"; keeping a buffer for each means a corrupted flag bit writes
        # into the page nobody is displaying instead of destroying the live one.
        self.rt_pages = {0: _TextField(64), 1: _TextField(64)}
        self.rt = self.rt_pages[0]
        self.ps_history = collections.Counter()
        self._rt_ab = None            # flag of the last group written
        self._rt_show = None          # flag of the buffer being reported
        self._rt_show_pending = None
        self.oda_aids = {}          # AID -> (group type, version) carrying it
        self.rtplus = {}            # RT+ content class -> text
        self._rtplus_group = None
        self._rtplus_toggle = None  # the RT+ item toggle bit last seen
        self._rt_message = 0        # counts the messages put on display
        self._rtplus_message = None  # the message the item fields came from
        self.pty = None
        self.tp = None
        self.ta = None
        self.group_counts = collections.Counter()
        self.blocks_ok = 0
        self.blocks_seen = 0
        self.groups = 0
        # Bits fed so far. RDS runs at exactly 1187.5 bit/s, so this is a clock
        # that behaves the same live as when replaying a capture flat out.
        self.bits_in = 0
        self._group_s = 0.0         # stream second the group in hand began
        self._clock_readings = []   # recent clock readings, trusted or not
        self._clock_sync = None     # (UTC, offset hours, stream second) synced
        self._last_repair = {}      # segment -> its last repaired reception

    # -- bit plumbing ------------------------------------------------------
    def feed(self, bits):
        self.bits_in += len(bits)
        self._bits.extend(int(b) for b in bits)
        self._run()

    def _peek(self, i):
        v = 0
        for k in range(26):
            v = (v << 1) | self._bits[i + k]
        return v

    def _offset_exact(self, v):
        s = syndrome(v)
        for name, so in SYN_OFF.items():
            if s == so:
                return name
        return None

    def _run(self):
        want = [['A'], ['B'], ['C', "C'"], ['D']]
        key = {'A': 'A', 'B': 'B', 'C': 'C', "C'": 'C', 'D': 'D'}
        while True:
            n = len(self._bits)
            if not self._synced:
                if n < 104:
                    return
                # Acquire: find A followed by B, C/C', D (one slip tolerated).
                if self._offset_exact(self._peek(0)) != 'A':
                    self._bits.popleft()
                    continue
                offs = [self._offset_exact(self._peek(26 * j)) for j in range(4)]
                if sum(offs[j] in want[j] for j in range(4)) < 3:
                    self._bits.popleft()
                    continue
                self._synced = True
                self._miss = 0
            if n < 104:
                return
            grp, bad, corrected = {}, 0, set()
            for j, cands in enumerate((['A'], ['B'], ['C', "C'"], ['D'])):
                v = self._peek(26 * j)
                hit = None
                for c in cands:
                    msg, ok = correct_block(v, c)
                    if ok:
                        hit = (c, msg)
                        break
                self.blocks_seen += 1
                if hit:
                    grp[key[hit[0]]] = hit[1]
                    self.blocks_ok += 1
                    # Repaired rather than received intact - see _TextField.
                    if syndrome(v) != SYN_OFF[hit[0]]:
                        corrected.add(key[hit[0]])
                else:
                    bad += 1
            if grp:
                # The group is still at the head of the buffer, so this is
                # where it began in the stream.
                self._decode_group(grp, corrected, at_bit=self.bits_in - n)
            self._miss = self._miss + 1 if bad >= 3 else 0
            if self._miss >= self.drop_after:
                self._synced = False
            for _ in range(104):
                self._bits.popleft()

    # -- group interpretation ---------------------------------------------
    def _decode_group(self, b, corrected=frozenset(), at_bit=None):
        """Interpret one group.

        ``corrected`` names the blocks error correction had to repair, and
        ``at_bit`` is where the group began in the stream. Tests hand groups
        over directly, and the default - everything fed so far - is what they
        mean by it.
        """
        self._group_s = (self.bits_in if at_bit is None else at_bit) / 1187.5
        if 'A' in b:
            self.pi_votes[b['A']] += 1
        if 'B' not in b:
            return
        self.groups += 1
        gtype = (b['B'] >> 12) & 0xF
        ver_b = (b['B'] >> 11) & 1
        # Every group carries them, so taking each as it came let one block B
        # repaired wrongly show a different PTY: off the cable, 5 read 4 and
        # 15 read 14 until the next good group.
        tp_pty = ((b['B'] >> 10) & 1, (b['B'] >> 5) & 0x1F)
        if self._believed(('tp_pty',), tp_pty, 'B' in corrected):
            self.tp, self.pty = tp_pty
        self.group_counts[f"{gtype}{'B' if ver_b else 'A'}"] += 1
        if ver_b and 'C' in b:
            self.pi_votes[b['C']] += 1

        # 3A announces which group carries an Open Data Application, and block D
        # holds the application's id. RT+ is the one worth following.
        if gtype == 3 and not ver_b and 'D' in b:
            carrier = ((b['B'] >> 1) & 0xF, b['B'] & 1)
            self.oda_aids[b['D']] = carrier
            if b['D'] == RTPLUS_AID:
                self._rtplus_group = carrier
        if (self._rtplus_group == (gtype, ver_b) and 'C' in b and 'D' in b
                and self._believed(('rtplus', gtype, ver_b),
                                   (b['B'] & 0x1F, b['C'], b['D']),
                                   corrected.intersection(('B', 'C', 'D')))):
            # A tag group repaired wrongly would slice the wrong words out of
            # RadioText and show them as Now Playing.
            self._parse_rtplus(b)

        if gtype == 0:
            ta = (b['B'] >> 4) & 1
            if self._believed(('ta',), ta, 'B' in corrected):
                self.ta = ta
            if 'D' in b:
                addr = b['B'] & 0x3
                # The same care as RadioText: taking every repair as it came,
                # PS flashed a wrong value 135 times in four minutes off air.
                trusted = self._believed(('ps', addr), b['D'],
                                         corrected.intersection(('B', 'D')))
                self.ps.put(addr * 2, chr((b['D'] >> 8) & 0xFF), trusted)
                self.ps.put(addr * 2 + 1, chr(b['D'] & 0xFF), trusted)
                if addr == 3 and trusted:
                    text = self.ps.text().strip()
                    if text:
                        self.ps_history[text] += 1
        elif gtype == 2:
            # The A/B flag announces "new message, wipe what you have". It is a
            # single bit inside block B, so one bit error would otherwise clear
            # the whole buffer and leave only the last segment received -
            # require the new value twice before believing it.
            ab = (b['B'] >> 4) & 1
            buf = self.rt_pages[ab]
            # A group only gets a say in what message is on air if it can be
            # believed (see _believed) - and that covers the flag and the
            # segment address in block B as much as the characters.
            data = ('D',) if ver_b else ('C', 'D')
            trusted = self._believed(
                ('rt', ver_b, b['B'] & 0x1F), tuple(b.get(k) for k in data),
                corrected.intersection(('B',) + data))
            # The flag this group went out under, once it can be believed. A
            # page is not cleared just because the flag changed: a station
            # rotating two messages under A and B comes back to each page with
            # the same text, and clearing it made the receiver start the page
            # from nothing on every turn - simulated at 88% blocks good, a new
            # song's Now Playing never arrived at all. A page whose text has
            # really changed is still cleared, by the contradiction check below.
            flag_changed = trusted and ab != self._rt_ab
            if trusted:
                self._rt_ab = ab
            if flag_changed:
                # Kept, but only what was believed: see drop_provisional.
                buf.drop_provisional()
            addr = b['B'] & 0xF
            base, chars = None, None
            if not ver_b and 'C' in b and 'D' in b:
                base = addr * 4
                chars = [chr((b['C'] >> 8) & 0xFF), chr(b['C'] & 0xFF),
                         chr((b['D'] >> 8) & 0xFF), chr(b['D'] & 0xFF)]
            elif ver_b and 'D' in b:
                base = addr * 2
                chars = [chr((b['D'] >> 8) & 0xFF), chr(b['D'] & 0xFF)]
            if chars is not None:
                # Plenty of US stations rotate several messages - a slogan, the
                # song, an advert - and never toggle the A/B flag between them.
                # 98.7 does exactly that. With nothing clearing the buffer, each
                # new message overwrites the last one segment by segment and
                # what is on display is a splice of the two: "98.7WMZQBest
                # Country", or a car dealership in the middle of a song title.
                # The contradiction itself is the announcement. Straight after a
                # change of flag, one differing character is enough: the flag
                # says a new message may be starting, as when paragraph page
                # "3/5" follows "1/5". The page is cleared and then written
                # with this very group - clearing without writing would lose
                # the first four characters of every page.
                if trusted and buf.differs(base, chars,
                                           minimum=1 if flag_changed else 2):
                    buf.clear()
                    if self._rt_show is None or ab == self._rt_show:
                        self._rt_message += 1     # a new message on display
                for j, ch in enumerate(chars):
                    buf.put(base + j, ch, trusted)
            # Switch which buffer is reported only once the new flag has been
            # seen twice in believed groups, so one bad bit cannot flash a
            # half-empty page up.
            if not trusted:
                pass
            elif self._rt_show is None or ab == self._rt_show:
                self._rt_show_pending = None
                self._rt_show = ab
            elif ab == self._rt_show_pending:
                self._rt_show = ab
                self._rt_show_pending = None
                # Another page on display. RT+ item fields stay: whether the
                # song has ended is for the item toggle bit to say, not the page.
                self._rt_message += 1
            else:
                self._rt_show_pending = ab
            if self._rt_show is not None:
                self.rt = self.rt_pages[self._rt_show]
        elif gtype == 4 and not ver_b and 'C' in b and 'D' in b:
            self._decode_clock(b)

    def _parse_rtplus(self, b):
        """Pull the two RT+ tags out of a group and slice them from RadioText.

        The 37 bits span block B's five spare bits plus blocks C and D, and each
        tag is a content class with a start offset and length into the current
        RadioText - so "artist" and "title" come out as separate fields rather
        than one run-on string.
        """
        bits = ((b['B'] & 0x1F) << 32) | (b['C'] << 16) | b['D']
        toggle = (bits >> 36) & 1
        if self._rtplus_toggle is not None and toggle != self._rtplus_toggle:
            # The item toggle bit flips when the item - the song - changes,
            # and tells a receiver to purge content types 1 to 11, title and
            # artist among them (NRSC-G300-C section 6.10.1).
            self._drop_item()
        self._rtplus_toggle = toggle
        # The tags describe the page being sent, which can be a group or two
        # ahead of the page on display: that one only switches once its flag
        # has been believed twice.
        page = self.rt_pages[self._rt_ab] if self._rt_ab is not None else self.rt
        text = page.text()
        tags = (((bits >> 29) & 0x3F, (bits >> 23) & 0x3F, (bits >> 17) & 0x3F),
                ((bits >> 11) & 0x3F, (bits >> 5) & 0x3F, bits & 0x1F))
        for ctype, start, length in tags:
            if ctype == 0:                      # 0 is the "no tag here" class
                continue
            end = start + length + 1
            if end > len(text):
                continue
            # The offsets refer to the message being transmitted now. One that
            # arrives while the next message is still filling in would cut
            # across both, welding half of each together - "Dan +" from the new
            # text and "ntry" from the tail of the old "Country". Stations
            # repeat these groups every second or two, so skipping one costs
            # nothing and the tag lands as soon as the text beneath it is real.
            if not page.range_written(start, end):
                continue
            # Stations do ship RT+ offsets that do not match the RadioText they
            # actually sent - 99.5 tags "Injured? Attorney" two characters late,
            # yielding "jured? Attorne". Half a word is worse than no answer, so
            # require the slice to start and end on a word boundary.
            starts_clean = (start == 0 or not text[start - 1].isalnum()
                            or not text[start].isalnum())
            ends_clean = (end >= len(text.rstrip()) or not text[end].isalnum()
                          or not text[end - 1].isalnum())
            value = text[start:end].strip()
            if value and starts_clean and ends_clean:
                if 1 <= ctype <= 11 and self._rtplus_message != self._rt_message:
                    # Item fields outlast the RadioText message that carried
                    # them, as the standard intends, but are never mixed from
                    # two: a title tagged in an advert must not sit beside the
                    # artist of the song before it.
                    self._drop_item()
                    self._rtplus_message = self._rt_message
                self.rtplus[RTPLUS_CLASSES.get(ctype, f"class{ctype}")] = value

    def _drop_item(self):
        """Forget the item fields: title, artist, album and the rest (1-11)."""
        for ctype in range(1, 12):
            self.rtplus.pop(RTPLUS_CLASSES[ctype], None)

    def _believed(self, key, value, repaired):
        """Whether a segment of text can be taken at its word.

        One whose blocks all arrived intact, yes. One that needed error
        correction only if it came out exactly as the last repaired reception
        of the same segment did. Off air at 93% blocks good about six repairs
        in seven were right, yet two thirds of RadioText groups carried at
        least one, so refusing every repair left gaps and stale text on screen
        for most of a minute. A wrong repair almost never comes out the same
        way twice.
        """
        if not repaired:
            self._last_repair.pop(key, None)
            return True
        agrees = self._last_repair.get(key) == value
        self._last_repair[key] = value
        return agrees

    def _decode_clock(self, b):
        """Group 4A clock time, shown only once a second reading agrees with it.

        A station that sends the clock sends it about once a minute, and many
        send none: 98.7 sent no 4A at all in 12 minutes of 99.99% clean blocks.
        So one group earns no trust. A block B that the (26,16) code corrects
        wrongly can turn any group into a 4A, and its C and D then decode as a
        date - which is how the receiver once showed 2168-10-28 01:27 (UTC-9)
        for a station with no clock. The same 12 minutes held one 1B and one
        12B, types that station never sends, so such groups do get through.

        A first reading is accepted only once another recent one carries the
        same offset and a time that has moved on by as much as the bitstream.
        From then on a reading is a sync, the way a car radio treats one: the
        clock runs on by stream time, so a lost group costs nothing, and a
        single reading that agrees with the running clock re-syncs it. A
        station whose clock is simply wrong still shows, consistently wrong; it
        is the one-off garbage that is kept off the screen.
        """
        mjd = ((b['B'] & 0x3) << 15) | ((b['C'] >> 1) & 0x7FFF)
        hour = ((b['C'] & 0x1) << 4) | ((b['D'] >> 12) & 0xF)
        minute = (b['D'] >> 6) & 0x3F
        sign = -1 if (b['D'] >> 5) & 1 else 1
        off_half_hours = b['D'] & 0x1F
        # Local offsets run to +-12 hours, in half hours.
        if not (0 <= hour < 24 and 0 <= minute < 60 and mjd > 15000
                and off_half_hours <= 24):
            return
        # Modified Julian Date -> calendar date.
        yp = int((mjd - 15078.2) / 365.25)
        mp = int((mjd - 14956.1 - int(yp * 365.25)) / 30.6001)
        day = mjd - 14956 - int(yp * 365.25) - int(mp * 30.6001)
        k = 1 if mp in (14, 15) else 0
        year = 1900 + yp + k
        month = mp - 1 - k * 12
        utc = datetime(year, month, day, hour, minute)
        offset = sign * off_half_hours / 2.0
        at = self._group_s
        # 90 s of slack either way: the minute can tick over between two
        # groups, and a station may repeat the same minute's group.
        running = self._clock_at(at)
        agrees_running = (running is not None and running[1] == offset
                          and abs((utc - running[0]).total_seconds()) <= 90)
        # Any recent reading can be the second witness, not only the last one:
        # off air at 93% blocks good garbage clock groups arrived twice in a
        # minute, and each displaced the real reading that came before it.
        # And the witness must be at least a minute earlier. A group the station
        # repeats every few seconds - a RadioText segment - has the same blocks
        # C and D each time, so when its block B is corrected into a 4A the
        # same way twice, the two readings are identical, and a time that has
        # not moved at all is inside the slack. Off air at 87% blocks good that
        # showed "2206-10-30 14:21 (UTC+9)" for 97 s. A station repeating the
        # same minute's group is confirmed by its next minute instead.
        agrees_earlier = any(
            p_offset == offset
            and (utc - p_utc).total_seconds() >= 60
            and abs((utc - p_utc).total_seconds() - (at - p_at)) <= 90
            for p_utc, p_offset, p_at in self._clock_readings)
        self._clock_readings = (self._clock_readings + [(utc, offset, at)])[-8:]
        if agrees_running or agrees_earlier:
            self._clock_sync = (utc, offset, at)

    def _clock_at(self, at):
        """(UTC, offset hours) at stream second ``at``, or None before a sync."""
        if self._clock_sync is None:
            return None
        utc, offset, synced_at = self._clock_sync
        return utc + timedelta(seconds=at - synced_at), offset

    def confirmed_callsign(self, pi):
        """A call sign the station's own text backs up, or None.

        Stations habitually put their call letters in the PS name or RadioText
        ("98.7WMZQ"), which is independent evidence for choosing among the
        high-nibble candidates of an altered PI.
        """
        if pi is None:
            return None
        text = (' '.join(self.ps_history) + ' ' + self.rt.text() + ' '
                + ' '.join(self.rtplus.values())).upper()
        for call in callsign_candidates(pi):
            if call in text:
                return call
        return None

    # -- output ------------------------------------------------------------
    def clock_reading(self):
        """The station clock as it stands now, or None before the first sync.

        UTC fields and offset in the same shape a clock group decodes to, plus
        the second and how long ago (in stream seconds) it was last synced.
        """
        now = self.bits_in / 1187.5
        running = self._clock_at(now)
        if running is None:
            return None
        utc, offset = running
        return {'year': utc.year, 'month': utc.month, 'day': utc.day,
                'hour': utc.hour, 'minute': utc.minute, 'second': utc.second,
                'utc_offset_hours': offset,
                'synced_ago_s': now - self._clock_sync[2]}

    def snapshot(self):
        pi = self.pi_votes.most_common(1)[0][0] if self.pi_votes else None
        table = PTY_RBDS if self.region == 'RBDS' else PTY_RDS
        name = self.ps_history.most_common(1)[0][0] if self.ps_history else ''
        return {
            'pi': pi,
            'pi_hex': f"0x{pi:04X}" if pi is not None else None,
            'callsign': pi_to_callsign(pi),
            'callsign_confirmed': self.confirmed_callsign(pi),
            'ps': self.ps.text(),
            'station_name': name,
            'radiotext': self.rt.message(),
            'rt_ab': self._rt_show,
            'pty': table[self.pty] if self.pty is not None else None,
            'rtplus': dict(self.rtplus),
            'title': self.rtplus.get('title'),
            'artist': self.rtplus.get('artist'),
            'station_short': self.rtplus.get('stationname.short'),
            'oda': {f"0x{a:04X}": g for a, g in self.oda_aids.items()},
            'has_tmc': TMC_AID in self.oda_aids,
            'tp': self.tp,
            'ta': self.ta,
            'clock': self.clock_reading(),
            'groups': self.groups,
            'blocks_ok': self.blocks_ok,
            'blocks_seen': self.blocks_seen,
            'block_error_rate': (1 - self.blocks_ok / self.blocks_seen)
                                if self.blocks_seen else None,
            'group_counts': dict(sorted(self.group_counts.items())),
        }


# --------------------------------------------------------------------------
# DSP back end
# --------------------------------------------------------------------------
def count_offset_hits(bits):
    """How many 26-bit windows in a bitstream carry a valid offset word.

    Used to choose the symbol timing offset. At the right timing about one
    window in 26 is a real block boundary and matches; at the wrong timing only
    the ~5 syndromes out of 1024 that correspond to offset words come up by
    chance, so the two cases differ by roughly an order of magnitude.
    """
    n = len(bits)
    if n < 26:
        return 0
    valid = set(SYN_OFF.values())
    v = 0
    for i in range(26):
        v = (v << 1) | int(bits[i])
    hits = 1 if syndrome(v) in valid else 0
    for i in range(26, n):
        v = ((v << 1) | int(bits[i])) & 0x3FFFFFF
        if syndrome(v) in valid:
            hits += 1
    return hits


class RdsDemod:
    """MPX + pilot reference in, RDS bits out. Streams in arbitrary chunks.

    ``pilot_ref`` is a unit-amplitude complex tone locked to the 19 kHz pilot
    (from GNU Radio's PLL block live, or a software PLL offline). Its cubed
    phase is the 57 kHz subcarrier and its phase/16 is the bit clock.

    The offset between the pilot phase and the symbol boundary is *not* a
    constant: the bit grid is anchored at whatever pilot phase the first sample
    happened to have, so it differs every run and with any filter delay ahead of
    this class. It is therefore measured from the signal (see ``_pick_tau``)
    rather than hard-coded - getting it wrong costs every single bit.
    """

    #: Bits to buffer before measuring the symbol timing offset.
    CAL_BITS = 1800
    #: Candidate timing offsets tried across one bit period.
    TAU_STEPS = 16

    def __init__(self, mpx_rate, cutoff_hz=2400.0, ntaps=201, tau=None):
        self.fs = float(mpx_rate)
        self.tau = tau              # None until measured from the signal
        self._lp = signal.firwin(ntaps, cutoff_hz, fs=self.fs)
        self._zi = np.zeros(ntaps - 1, dtype=np.complex128)
        self._phase_cont = 0.0      # cumulative pilot phase carried across calls
        self._last_angle = None
        self._bit_origin = None     # cumulative phase at bit index 0
        self._carry_bb = np.zeros(0, dtype=np.complex128)
        self._carry_k = np.zeros(0, dtype=np.float64)
        self._axis = 1.0 + 0j       # running BPSK principal-axis phasor
        self._last_bit = None       # previous biphase symbol, for diff decoding

    def _symbols(self, bb, kt):
        """Integrate-and-dump each whole bit of ``bb`` on the grid ``kt``.

        First half minus second half is the matched filter for a biphase
        symbol. Returns the symbols and how much of the input they consumed.
        """
        first, last = int(np.ceil(kt[0])), int(np.floor(kt[-1]))
        if last - first < 1:
            return np.zeros(0, dtype=np.complex128), 0
        idx = np.arange(first, last)
        csum = np.concatenate([[0], np.cumsum(bb)])
        e0 = np.searchsorted(kt, idx)
        em = np.searchsorted(kt, idx + 0.5)
        e1 = np.searchsorted(kt, idx + 1.0)
        ok = (e1 > em) & (em > e0)
        syms = np.where(ok, (csum[em] - csum[e0]) - (csum[e1] - csum[em]), 0)
        return syms[ok], (e0[-1] if len(e0) else 0)

    @staticmethod
    def _bits_from(syms):
        """Symbols -> differentially decoded bits, with no carried state."""
        if len(syms) < 2:
            return np.zeros(0, dtype=np.uint8)
        axis = np.angle(np.sum(syms ** 2)) / 2
        hard = ((syms * np.exp(-1j * axis)).real > 0).astype(np.uint8)
        return np.bitwise_xor(hard[1:], hard[:-1])

    def _pick_tau(self, bb, k):
        """Measure the symbol timing offset by trying a bit period's worth."""
        best_score, best_tau = -1, 0.0
        for i in range(self.TAU_STEPS):
            tau = i / self.TAU_STEPS
            syms, _ = self._symbols(bb, k - tau)
            score = count_offset_hits(self._bits_from(syms))
            if score > best_score:
                best_score, best_tau = score, tau
        return best_tau

    def feed(self, mpx, pilot_ref):
        """Return the bits recovered from this chunk (possibly empty)."""
        mpx = np.asarray(mpx, dtype=np.float64)
        ref = np.asarray(pilot_ref, dtype=np.complex128)
        n = min(len(mpx), len(ref))
        if n == 0:
            return np.zeros(0, dtype=np.uint8)
        mpx, ref = mpx[:n], ref[:n]

        # Cumulative pilot phase, continuous across chunk boundaries.
        ang = np.angle(ref)
        if self._last_angle is not None:
            ang = np.concatenate([[self._last_angle], ang])
            phase = np.unwrap(ang)[1:] + (self._phase_cont - self._last_angle)
        else:
            phase = np.unwrap(ang)
        self._last_angle = float(np.angle(ref[-1]))
        self._phase_cont = float(phase[-1])

        # Coherent mix down from 57 kHz (= 3x pilot) and low-pass.
        bb, self._zi = signal.lfilter(self._lp, 1.0, mpx * np.exp(-3j * phase),
                                      zi=self._zi)

        if self._bit_origin is None:
            self._bit_origin = phase[0]
        k = (phase - self._bit_origin) / (16.0 * 2 * np.pi)

        bb = np.concatenate([self._carry_bb, bb])
        k = np.concatenate([self._carry_k, k])

        if self.tau is None:
            # Buffer until there is enough signal to measure the timing on.
            if k[-1] - k[0] < self.CAL_BITS:
                self._carry_bb, self._carry_k = bb, k
                return np.zeros(0, dtype=np.uint8)
            self.tau = self._pick_tau(bb, k)

        syms, keep = self._symbols(bb, k - self.tau)
        # Keep the tail that belongs to the next, still-incomplete bit.
        self._carry_bb, self._carry_k = bb[keep:], k[keep:]
        if not len(syms):
            return np.zeros(0, dtype=np.uint8)

        # Track the BPSK axis with a slow phasor so it never flips between
        # chunks (a flip would corrupt one differentially decoded bit).
        self._axis = 0.9 * self._axis + 0.1 * np.mean(syms ** 2)
        if self._axis != 0:
            self._axis /= abs(self._axis)
        proj = (syms * np.exp(-0.5j * np.angle(self._axis))).real
        hard = (proj > 0).astype(np.uint8)

        # Differential decode, carrying the last symbol across chunks.
        if self._last_bit is not None:
            hard = np.concatenate([[self._last_bit], hard])
        self._last_bit = hard[-1]
        return np.bitwise_xor(hard[1:], hard[:-1]).astype(np.uint8)


def software_pilot_pll(mpx, fs, f_pilot=19000.0, loop_bw=150.0):
    """Software 19 kHz pilot PLL — for offline use where no GNU Radio PLL runs.

    Returns a unit-amplitude complex reference locked to the pilot.
    """
    bp = signal.firwin(401, [f_pilot - 800, f_pilot + 800], fs=fs,
                       pass_zero=False)
    pilot = signal.lfilter(bp, 1.0, mpx)
    bw = loop_bw / fs
    damp = 0.707
    denom = 1 + 2 * damp * bw + bw * bw
    alpha = 4 * damp * bw / denom
    beta = 4 * bw * bw / denom
    phase = 0.0
    freq = 2 * np.pi * f_pilot / fs
    fmin, fmax = 2 * np.pi * (f_pilot - 200) / fs, 2 * np.pi * (f_pilot + 200) / fs
    out = np.empty(len(pilot), dtype=np.complex128)
    for i in range(len(pilot)):
        out[i] = np.cos(phase) + 1j * np.sin(phase)
        err = pilot[i] * (-np.sin(phase))
        freq = min(max(freq + beta * err, fmin), fmax)
        phase += freq + alpha * err
    return out


def fm_demodulate(iq, rate, offset_hz, mpx_rate, channel_bw=100e3):
    """IQ -> MPX baseband: shift the station to DC, filter, decimate, discriminate."""
    decim = int(round(rate / mpx_rate))
    n = np.arange(len(iq))
    bb = iq * np.exp(-2j * np.pi * (offset_hz / rate) * n)
    taps = signal.firwin(201, channel_bw, fs=rate)
    ch = signal.upfirdn(taps, bb, down=decim)
    mpx = np.angle(ch[1:] * np.conj(ch[:-1]))
    return mpx.astype(np.float64), rate / decim
