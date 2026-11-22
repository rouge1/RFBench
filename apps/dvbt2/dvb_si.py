"""DVB service information (ETSI EN 300 468) out of a transport stream: the services' names
(SDT) and what is on now and next (EIT present/following), for the DVB-T2 tab (Round 32,
phase 5). The standard library only.

``DvbSi`` takes the place of ATSC's ``Psip`` in a ``TsAnalyzer`` (``DvbTsAnalyzer``): the same
section reassembly (``Psip.packet``), its own tables:

- SDT actual (PID 0x11, table 0x42): each service_id (the PAT's program number) to its
  service descriptor (0x48): name, provider, type;
- EIT actual present/following (PID 0x12, table 0x4E): section 0 is the event on now, 1 the
  next, each with its start (MJD and BCD UTC, EN 300 468 Annex C), duration and short event
  descriptor (0x4D): name and text;
- NIT actual (PID 0x10, table 0x40): the network's name (descriptor 0x40).

Text is decoded as Annex A says: a first byte under 0x20 picks the table (0x01-0x0B: ISO/IEC
8859-5 to -15, 0x07 Thai being 8859-11; 0x10: 8859 by the next two bytes; 0x11: UCS-2; 0x13:
GB2312; 0x14: Big5; 0x15: UTF-8; 0x12: KS X 1001), else the default, ISO/IEC 6937 (Figure
A.1; ``_6937``, as iconv reads it), where an accent is a byte of its own sent before its letter (0xC1-0xCF: made a
combining mark, then composed). The control codes are dropped (0x80-0x9F; U+E080-U+E09F in the
two-byte tables, and U+0080-U+009F in UTF-8 too), 0x8A, a line break, becoming a space. 0x1F
(encoding_type_id) is read as the default.
"""

import datetime
import unicodedata

from .atsc_core import Psip, TsAnalyzer

PID_NIT = 0x10
PID_SDT = 0x11
PID_EIT = 0x12

SERVICE_TYPES = {0x01: 'TV', 0x02: 'radio', 0x0C: 'data', 0x11: 'HD TV (MPEG-2)', 0x16: 'TV (H.264 SD)',
                 0x19: 'HD TV', 0x1F: 'HEVC TV', 0x0A: 'radio (advanced codec)'}

# ISO/IEC 6937 above 0x9F, as glibc's iconv ISO_6937 has it (checked cell by cell; 0xA4 and 0xA6,
# '$' and '#' in an older edition, are unassigned there); below 0xA0 it is ASCII. U+FFFD: unassigned.
_6937 = ('\u00a0¡¢£\ufffd¥\ufffd§¤‘“«←↑→↓' '°±²³×µ¶·÷’”»¼½¾¿'
         '\ufffd\u0300\u0301\u0302\u0303\u0304\u0306\u0307\u0308\ufffd\u030a\u0327\ufffd\u030b\u0328\u030c'
         '—¹®©™♪¬¦\ufffd\ufffd\ufffd\ufffd⅛⅜⅝⅞' '\u2126ÆÐªĦ\ufffdĲĿŁØŒºÞŦŊŉ' 'ĸæđðħıĳŀłøœßþŧŋ\u00ad')
assert len(_6937) == 96


def _iso6937(data):
    """ISO/IEC 6937 bytes (controls already taken out) as text: an accent (0xC1-0xCF) comes
    before its letter, so it is moved after it as a combining mark and the pair composed."""
    out, accent = [], None
    for b in data:
        c = chr(b) if b < 0xA0 else _6937[b - 0xA0]
        if 0xC1 <= b <= 0xCF:
            if accent is not None:
                out.append(accent)          # two accents running: the first on nothing
            accent = c
            continue
        out.append(c)
        if accent is not None:
            out.append(accent)
            accent = None
    if accent is not None:
        out.append(accent)
    return unicodedata.normalize('NFC', ''.join(out))


_8859 = {0x01: 5, 0x02: 6, 0x03: 7, 0x04: 8, 0x05: 9, 0x06: 10, 0x07: 11, 0x09: 13, 0x0A: 14, 0x0B: 15}


def dvb_text(data):
    """A DVB string (EN 300 468 Annex A) as text."""
    data = bytes(data)
    if not data:
        return ''
    codec, body, wide = '6937', data, False
    first = data[0]
    if first < 0x20:
        if first in _8859:
            codec, body = f'iso8859_{_8859[first]}', data[1:]
        elif first == 0x10 and len(data) >= 3:
            n = (data[1] << 8) | data[2]
            codec, body = (f'iso8859_{n}' if 1 <= n <= 15 and n != 12 else '6937'), data[3:]
        elif first == 0x11:
            codec, body, wide = 'utf-16-be', data[1:], True
        elif first == 0x12:
            codec, body, wide = 'euc_kr', data[1:], True
        elif first == 0x13:
            codec, body, wide = 'gb2312', data[1:], True
        elif first == 0x14:
            codec, body, wide = 'big5', data[1:], True
        elif first == 0x15:
            codec, body, wide = 'utf-8', data[1:], True
        else:
            body = data[1:]
    if wide:
        text = body.decode(codec, 'replace')
        text = text.replace('\ue08a', ' ').replace('\x8a', ' ')
        text = ''.join(c for c in text if not ('\ue080' <= c <= '\ue09f' or '\x80' <= c <= '\x9f'))
    else:
        keep = bytearray()
        for b in body:
            if b == 0x8A:
                keep.append(0x20)
            elif not 0x80 <= b <= 0x9F:
                keep.append(b)
        if codec == '6937':
            text = _iso6937(keep)
        else:
            try:
                text = bytes(keep).decode(codec, 'replace')
            except LookupError:
                text = _iso6937(keep)
    return ' '.join(text.split())


def _bcd(b):
    return (b >> 4) * 10 + (b & 0x0F)


def mjd_utc(b5):
    """EN 300 468 Annex C: 16 bits of Modified Julian Date and six BCD digits of UTC, as unix
    seconds; None when undefined (all ones)."""
    if all(x == 0xFF for x in b5):
        return None
    mjd = (b5[0] << 8) | b5[1]
    day = datetime.date(1858, 11, 17) + datetime.timedelta(days=mjd)
    t = datetime.datetime(day.year, day.month, day.day, tzinfo=datetime.timezone.utc)
    return t.timestamp() + _bcd(b5[2]) * 3600 + _bcd(b5[3]) * 60 + _bcd(b5[4])


def bcd_seconds(b3):
    return _bcd(b3[0]) * 3600 + _bcd(b3[1]) * 60 + _bcd(b3[2])


def _descriptors(data):
    k = 0
    while k + 2 <= len(data):
        tag, n = data[k], data[k + 1]
        yield tag, data[k + 2:k + 2 + n]
        k += 2 + n


class DvbSi(Psip):
    """What a DVB transport stream says about itself: ``services`` (service_id -> name,
    provider, type), ``present`` and ``following`` (service_id -> event: start unix s or None,
    seconds, name, text), ``network`` (its name)."""

    def __init__(self):
        super().__init__()
        self.pids = {PID_NIT, PID_SDT, PID_EIT}
        self.services = {}
        self.present = {}
        self.following = {}
        self.network = ''

    def _section(self, pid, s):
        try:
            if s[0] == 0x42 and pid == PID_SDT:
                self._sdt(s)
            elif s[0] == 0x4E and pid == PID_EIT:
                self._eit_pf(s)
            elif s[0] == 0x40 and pid == PID_NIT:
                self._nit(s)
        except (IndexError, ValueError):
            pass

    def _sdt(self, s):
        k, end = 11, len(s) - 4
        while k + 5 <= end:
            sid = (s[k] << 8) | s[k + 1]
            n = ((s[k + 3] & 0x0F) << 8) | s[k + 4]
            entry = self.services.setdefault(sid, {'name': '', 'provider': '', 'type': ''})
            for tag, d in _descriptors(s[k + 5:k + 5 + n]):
                if tag == 0x48 and len(d) >= 3:
                    stype = d[0]
                    pl = d[1]
                    if 2 + pl >= len(d):            # a short descriptor: skip it, not the
                        continue                    # rest of the section's services
                    provider = d[2:2 + pl]
                    nl = d[2 + pl]
                    name = d[3 + pl:3 + pl + nl]
                    entry.update(name=dvb_text(name), provider=dvb_text(provider),
                                 type=SERVICE_TYPES.get(stype, f'type 0x{stype:02x}'))
            k += 5 + n

    def _eit_pf(self, s):
        sid = (s[3] << 8) | s[4]
        number = s[6]
        k, end = 14, len(s) - 4
        event = None
        if k + 12 <= end:
            start = mjd_utc(s[k + 2:k + 7])
            seconds = bcd_seconds(s[k + 7:k + 10])
            n = ((s[k + 10] & 0x0F) << 8) | s[k + 11]
            name = text = ''
            for tag, d in _descriptors(s[k + 12:k + 12 + n]):
                if tag == 0x4D and len(d) >= 4 and not name:
                    nl = d[3]
                    name = dvb_text(d[4:4 + nl])
                    tl = d[4 + nl] if 4 + nl < len(d) else 0
                    text = dvb_text(d[5 + nl:5 + nl + tl])
            event = {'start': start, 'seconds': seconds, 'name': name, 'text': text}
        (self.present if number == 0 else self.following)[sid] = event

    def _nit(self, s):
        n = ((s[8] & 0x0F) << 8) | s[9]
        for tag, d in _descriptors(s[10:10 + n]):
            if tag == 0x40:
                self.network = dvb_text(d)


class DvbTsAnalyzer(TsAnalyzer):
    """``TsAnalyzer`` reading DVB's service information instead of ATSC's PSIP."""

    def reset(self):
        super().reset()
        self.psip = DvbSi()
