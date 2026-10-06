"""Link Manager Protocol PDUs - fields to the bytes of an ACL payload body.

The LMP half of the ACL traffic made for bluey-ox-walker
([knowledge/bluey-test-signals.md](../knowledge/bluey-test-signals.md)): the
bytes a DM1 carries with the payload header's LLID set to 3. Everything here
is from the Core specification v6.0, Vol 2 Part C, and is held to it by::

    python scripts/test_bt_lmp.py

whose decoder is written from the specification's tables and not from this
file.

A PDU is its bytes, nothing else (Part C 2.3, Figure 2.2): byte 1 is the
opcode's 7 bits above the transaction ID, which is the least significant bit
(2.4: 0 when the Central started the transaction, 1 when the Peripheral did),
and the parameters follow at byte 2, each as its type in Vol 1 Part E 2.9 -
integers least significant byte first. Every PDU here has a 7-bit opcode, so
the escape opcodes 124-127 are not needed, and every one is of fixed length
and fits the 17 bytes of a DM1 (2.8).

The eleven PDUs, with the Table 5.1 opcode and length:

=================  ======  ======  ==========================================
LMP_name_req           1       2   Name_Offset
LMP_name_res           2      17   Name_Offset, Name_Length, Name_Fragment (14)
LMP_accepted           3       2   Opcode
LMP_not_accepted       4       3   Opcode, Error_Code
LMP_detach             7       2   Error_Code
LMP_features_req      39       9   Features (8)
LMP_features_res      40       9   Features (8)
LMP_version_req       37       6   Version, Company_Identifier, Subversion
LMP_version_res       38       6   Version, Company_Identifier, Subversion
LMP_max_slot          45       2   Max_Slots
LMP_set_AFH           60      16   AFH_Instant (4), AFH_Mode, AFH_Channel_Map (10)
=================  ======  ======  ==========================================

Where the specification leaves a parameter to the sender, ``random_pdu`` draws
one that is valid: a name of 1-248 UTF-8 bytes cut into 14-byte fragments
(Part C 4.3.5), a detach reason that HCI_Disconnect (Vol 4 Part E 7.1.6) allows a link to
end with, a features mask with the bits Table 3.2 defines and none it reserves,
an LMP version and company identifier of a device that exists, and for
LMP_set_AFH the map of channels 31-50 these test files hop on, with an instant
that is even and 96 slots or more after the packet's own clock (4.1.4).

The transaction ID follows who started the transaction. These are the Central's
packets: the PDUs a Central starts carry 0, and its answers to a transaction
the Peripheral started carry 1 (``ANSWERS``).
"""

import numpy as np

#: name: opcode and length, Table 5.1 (the length counts the opcode byte).
PDUS = {
    'LMP_name_req': dict(opcode=1, length=2),
    'LMP_name_res': dict(opcode=2, length=17),
    'LMP_accepted': dict(opcode=3, length=2),
    'LMP_not_accepted': dict(opcode=4, length=3),
    'LMP_detach': dict(opcode=7, length=2),
    'LMP_features_req': dict(opcode=39, length=9),
    'LMP_features_res': dict(opcode=40, length=9),
    'LMP_version_req': dict(opcode=37, length=6),
    'LMP_version_res': dict(opcode=38, length=6),
    'LMP_max_slot': dict(opcode=45, length=2),
    'LMP_set_AFH': dict(opcode=60, length=16),
}

#: The PDUs a Central sends as the answer to a Peripheral's transaction (TID 1).
ANSWERS = ('LMP_name_res', 'LMP_accepted', 'LMP_not_accepted', 'LMP_features_res', 'LMP_version_res')

#: Which Error_Code values an LMP_detach carries: the Reasons HCI_Disconnect
#: allows (Vol 4 Part E 7.1.6), which are the ones a link is ended with.
DETACH_REASONS = (0x05, 0x13, 0x14, 0x15, 0x1A, 0x29, 0x3B)

#: Error codes of Vol 1 Part F that an LMP_not_accepted gives for a refused request.
REFUSAL_REASONS = (0x06, 0x0D, 0x11, 0x19, 0x1E, 0x1F, 0x20, 0x21, 0x23, 0x24, 0x28, 0x2A, 0x34)

#: Opcodes a Central answers with LMP_accepted or LMP_not_accepted: LMP_AUTO_RATE
#: 35, LMP_MAX_SLOT_REQ 46, LMP_HOST_CONNECTION_REQ 51, LMP_INCR_POWER_REQ 31,
#: LMP_DECR_POWER_REQ 32, LMP_PAGE_MODE_REQ 53, LMP_PAGE_SCAN_MODE_REQ 54,
#: LMP_TIMING_ACCURACY_REQ 47 (which is answered by its RES, but may be refused) and
#: LMP_UNSNIFF_REQ 24.
ANSWERED_OPCODES = (35, 46, 51, 31, 32, 53, 54, 47, 24)

#: Table 3.2's feature numbers that are neither "reserved for future use" nor
#: "previously used", the page-0 mask of an LMP_features_req or _res.
DEFINED_FEATURES = tuple(n for n in range(64)
                         if n not in (8, 24, 34, 37, 50, 55, 59, 60, 61, 62))

#: Features every device here has: 3 and 5 slot packets (bits 0 and 1), which
#: the DM3 and DM5 of the same files need, and 63, "extended features".
ALWAYS_FEATURES = (0, 1, 63)

#: LMP versions 4.0 to 6.0 (Bluetooth Assigned Numbers, LMP version: 0x06 to 0x0E).
VERSIONS = tuple(range(0x06, 0x0F))

#: A few companies from the Assigned Numbers' company identifiers.
COMPANIES = (0x0002, 0x0006, 0x000A, 0x000D, 0x000F, 0x001D, 0x004C, 0x0046, 0x0059, 0x0075)

#: The channels of the test files' adapted hop sequence.
AFH_MAP = tuple(range(31, 51))

#: 4.1.4: an AFH_Instant is at least 96 slots ahead; the draw is 96 to 96 + 2 x 600.
AFH_MIN_AHEAD_SLOTS = 96


def channel_map(channels):
    """The 10-byte AFH_Channel_Map (Table 5.2): element n, channel n, is bit
    n % 8 of byte n // 8; element 79 is reserved and stays zero."""
    value = 0
    for c in channels:
        if not 0 <= c <= 78:
            raise ValueError('channel %d is not 0-78' % c)
        value |= 1 << c
    return value.to_bytes(10, 'little')


def _int(value, nbytes, what):
    value = int(value)
    if not 0 <= value < 1 << (8 * nbytes):
        raise ValueError('%s %d does not fit in %d bytes' % (what, value, nbytes))
    return value.to_bytes(nbytes, 'little')


def _fixed(value, nbytes, what):
    value = bytes(value)
    if len(value) != nbytes:
        raise ValueError('%s is %d bytes, not %d' % (what, len(value), nbytes))
    return value


def encode(name, tid=0, **p):
    """One PDU's bytes. ``name`` is a key of ``PDUS``, ``tid`` 0 or 1, and the
    parameters are keyword arguments named for Table 5.2's, lower case; a value
    outside the field, or a name of a PDU that is not here, is a ``ValueError``."""
    if name not in PDUS:
        raise ValueError('no PDU %r; one of %s' % (name, ', '.join(PDUS)))
    if tid not in (0, 1):
        raise ValueError('a transaction ID is 0 or 1, not %r' % (tid,))
    opcode = PDUS[name]['opcode']
    body = b''
    if name == 'LMP_name_req':
        body = _int(p['name_offset'], 1, 'Name_Offset')
    elif name == 'LMP_name_res':
        fragment = bytes(p['name_fragment'])
        if len(fragment) > 14:
            raise ValueError('a Name_Fragment is 14 bytes, not %d' % len(fragment))
        if not 1 <= p['name_length'] <= 248:
            raise ValueError('Name_Length %d is not 1-248' % p['name_length'])
        body = (_int(p['name_offset'], 1, 'Name_Offset') + _int(p['name_length'], 1, 'Name_Length')
                + fragment.ljust(14, b'\0'))
    elif name == 'LMP_accepted':
        body = _int(p['opcode'], 1, 'Opcode')
    elif name == 'LMP_not_accepted':
        body = _int(p['opcode'], 1, 'Opcode') + _int(p['error_code'], 1, 'Error_Code')
    elif name == 'LMP_detach':
        body = _int(p['error_code'], 1, 'Error_Code')
    elif name in ('LMP_features_req', 'LMP_features_res'):
        body = _fixed(p['features'], 8, 'Features')
    elif name in ('LMP_version_req', 'LMP_version_res'):
        body = (_int(p['version'], 1, 'Version') + _int(p['company_id'], 2, 'Company_Identifier')
                + _int(p['subversion'], 2, 'Subversion'))
    elif name == 'LMP_max_slot':
        if p['max_slots'] not in (1, 3, 5):
            raise ValueError('Max_Slots is 1, 3 or 5, not %r' % (p['max_slots'],))
        body = _int(p['max_slots'], 1, 'Max_Slots')
    elif name == 'LMP_set_AFH':
        body = (_int(p['afh_instant'], 4, 'AFH_Instant') + _int(p['afh_mode'], 1, 'AFH_Mode')
                + _fixed(p['afh_channel_map'], 10, 'AFH_Channel_Map'))
    pdu = bytes([(opcode << 1) | tid]) + body
    if len(pdu) != PDUS[name]['length']:
        raise ValueError('%s came to %d bytes, Table 5.1 says %d' % (name, len(pdu), PDUS[name]['length']))
    return pdu


_WORDS = ('bluey', 'walker', 'speaker', 'phone', 'headset', 'watch', 'laptop', 'keyboard', 'mouse', 'car',
          'kitchen', 'ox', 'sdr', 'bench', 'lab', 'tv', 'tag', 'sensor', 'band', 'earbuds')


def random_name(rng):
    """A device name of 1-248 bytes of ASCII (which is UTF-8): mostly short, some of the long ones that
    need many fragments."""
    n = int(rng.integers(40, 249)) if rng.random() < 0.3 else int(rng.integers(1, 40))
    out = ''
    while len(out) < n:
        out += _WORDS[int(rng.integers(0, len(_WORDS)))] + ' '
    return out[:n].rstrip(' ').encode('ascii') or b'x'


def random_pdu(rng, name, clk=0):
    """``(pdu, params, tid)``: a valid PDU of ``name`` with parameters drawn from ``rng``.

    ``clk`` is the native clock of the master slot the PDU will go in, which only
    LMP_set_AFH reads, for an AFH_Instant that is in the future (4.1.4). ``params``
    are what ``encode`` was given, and what a grader should find in the bytes."""
    if name not in PDUS:
        raise ValueError('no PDU %r; one of %s' % (name, ', '.join(PDUS)))
    tid = 1 if name in ANSWERS else 0
    p = {}
    if name == 'LMP_name_req':
        p['name_offset'] = 14 * int(rng.choice([0, 0, 0, 1, 2, 3, 5, 8, 12, 17]))
    elif name == 'LMP_name_res':
        full = random_name(rng)
        fragments = -(-len(full) // 14)
        offset = 14 * int(rng.integers(0, fragments))
        p.update(name_offset=offset, name_length=len(full), name_fragment=full[offset:offset + 14].ljust(14, b'\0'))
    elif name == 'LMP_accepted':
        p['opcode'] = int(rng.choice(ANSWERED_OPCODES))
    elif name == 'LMP_not_accepted':
        p.update(opcode=int(rng.choice(ANSWERED_OPCODES)), error_code=int(rng.choice(REFUSAL_REASONS)))
    elif name == 'LMP_detach':
        p['error_code'] = int(rng.choice(DETACH_REASONS))
    elif name in ('LMP_features_req', 'LMP_features_res'):
        mask = sum(1 << b for b in ALWAYS_FEATURES)
        for b in DEFINED_FEATURES:
            if rng.random() < 0.55:
                mask |= 1 << b
        p['features'] = mask.to_bytes(8, 'little')
    elif name in ('LMP_version_req', 'LMP_version_res'):
        p.update(version=int(rng.choice(VERSIONS)), company_id=int(rng.choice(COMPANIES)),
                 subversion=int(rng.integers(0, 1 << 16)))
    elif name == 'LMP_max_slot':
        p['max_slots'] = int(rng.choice([1, 3, 5]))
    elif name == 'LMP_set_AFH':
        # CLK27-1 of a master slot is even; an even step keeps the instant even (Table 5.2)
        ahead = AFH_MIN_AHEAD_SLOTS + 2 * int(rng.integers(0, 600))
        p.update(afh_instant=((clk >> 1) + ahead) & 0x7FFFFFE, afh_mode=1, afh_channel_map=channel_map(AFH_MAP))
    return encode(name, tid, **p), p, tid
