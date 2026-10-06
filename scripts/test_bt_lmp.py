#!/usr/bin/env python3
"""Hold the LMP PDU encoder to the Core specification's field tables.

    python scripts/test_bt_lmp.py

``apps/bt_lmp.py`` writes the Link Manager Protocol PDUs that
``scripts/bt_synth_acl.py`` puts in the ACL traffic it makes for
bluey-ox-walker: eleven of them, every one with the exact length and field
layout of Core v6.0 Vol 2 Part C, Table 5.1 (the PDU summary) and Table 5.2
(the parameters).

The decoder below is **written from those tables and not from the encoder**:
it has its own table of (opcode, length, fields with their byte positions as
Table 5.1 prints them, 1 = the opcode byte), and a PDU is read by position.
It checks, for every one of the eleven PDUs:

* opcode, transaction ID and length (the first byte is ``opcode << 1 | TID``,
  Part C 2.3 and 2.4), and the fields at the positions of Table 5.1;
* the ranges of Table 5.2 and of the procedures in Part C 4: a name offset
  that is a fragment start, a name fragment of 14 bytes zero-padded after the
  name's length, an error code of Vol 1 Part F, Max_Slots of 1, 3 or 5, an
  AFH_Instant that is even, AFH_Mode 1, a channel map whose element 79 is
  zero, a features mask with no reserved bit set;
* a handful of PDUs written out by hand from the tables (their bytes are here
  as numbers), so that a table mistake common to the encoder and this decoder
  is still caught;
* if the specification's text is on this machine (``/tmp/sdr-p7/core_v6_full.txt``)
  every opcode and length of the eleven against the rows of Table 5.1 itself;
* a round trip over many seeds: random PDU, decode, same fields back;
* the mutants: a wrong opcode for one PDU, a wrong length, a TID in the wrong
  bit, are each caught.
"""
import importlib.util
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_lmp  # noqa: E402

failures = []
SPEC_TEXT = '/tmp/sdr-p7/core_v6_full.txt'


def check(ok, what):
    ok = bool(ok)
    print('  %s %s' % ('ok  ' if ok else 'FAIL', what))
    if not ok:
        failures.append(what)
    return ok


# --- the specification's tables, as this test's own data -----------------------

#: name: (opcode, length in bytes, [(field, first byte, last byte)]) - Table 5.1,
#: "Position in payload" counted from 1 at the opcode byte.
SPEC = {
    'LMP_name_req': (1, 2, [('name_offset', 2, 2)]),
    'LMP_name_res': (2, 17, [('name_offset', 2, 2), ('name_length', 3, 3), ('name_fragment', 4, 17)]),
    'LMP_accepted': (3, 2, [('opcode', 2, 2)]),
    'LMP_not_accepted': (4, 3, [('opcode', 2, 2), ('error_code', 3, 3)]),
    'LMP_detach': (7, 2, [('error_code', 2, 2)]),
    'LMP_features_req': (39, 9, [('features', 2, 9)]),
    'LMP_features_res': (40, 9, [('features', 2, 9)]),
    'LMP_version_req': (37, 6, [('version', 2, 2), ('company_id', 3, 4), ('subversion', 5, 6)]),
    'LMP_version_res': (38, 6, [('version', 2, 2), ('company_id', 3, 4), ('subversion', 5, 6)]),
    'LMP_max_slot': (45, 2, [('max_slots', 2, 2)]),
    'LMP_set_AFH': (60, 16, [('afh_instant', 2, 5), ('afh_mode', 6, 6), ('afh_channel_map', 7, 16)]),
}

#: Vol 1 Part F, the error codes (0x00 is Success and is no reason).
ERROR_CODES = set(range(0x01, 0x49)) - {0x2B, 0x31, 0x33}

#: Table 3.2, the feature numbers that are not "reserved for future use" or "previously used".
FEATURE_RESERVED = {8, 24, 34, 37, 50, 55, 59, 60, 61, 62}

#: Opcodes Table 5.1 lists in 7 bits, for what LMP_accepted and LMP_not_accepted may name.
KNOWN_OPCODES = {1, 2, 3, 4, 5, 6, 7, 19, 21, 23, 24, 31, 32, 33, 34, 35, 37, 38, 39, 40, 45, 46, 47, 48, 51,
                 53, 54, 60}


def decode(pdu):
    """A PDU's fields, from Table 5.1's positions. Raises ``ValueError`` on anything the table forbids."""
    pdu = bytes(pdu)
    if not pdu:
        raise ValueError('empty')
    opcode, tid = pdu[0] >> 1, pdu[0] & 1
    for name, (code, length, fields) in SPEC.items():
        if code == opcode:
            break
    else:
        raise ValueError('opcode %d is none of the eleven' % opcode)
    if len(pdu) != length:
        raise ValueError('%s is %d bytes, not %d' % (name, len(pdu), length))
    out = {'name': name, 'opcode': opcode, 'tid': tid, 'length': len(pdu), 'fields': {}}
    for fname, first, last in fields:
        raw = pdu[first - 1:last]
        out['fields'][fname] = bytes(raw) if fname in ('name_fragment', 'afh_channel_map', 'features') \
            else int.from_bytes(raw, 'little')
    return out


def validate(d):
    """The ranges of Table 5.2 and Part C 4 on a decoded PDU; a list of what is wrong."""
    f, bad = d['fields'], []
    n = d['name']
    if n == 'LMP_name_req' and f['name_offset'] % 14:
        bad.append('name offset %d is not a fragment start' % f['name_offset'])
    if n == 'LMP_name_res':
        if not 1 <= f['name_length'] <= 248:
            bad.append('name length %d not 1-248' % f['name_length'])
        if f['name_offset'] % 14 or f['name_offset'] >= f['name_length']:
            bad.append('offset %d for a name of %d' % (f['name_offset'], f['name_length']))
        name = f['name_fragment']
        if len(name) != 14:
            bad.append('fragment %d bytes' % len(name))
        used = max(0, min(14, f['name_length'] - f['name_offset']))
        if any(name[used:]):
            bad.append('fragment is not zero after the name ends')
        if b'\0' in name[:used]:
            bad.append('a NUL inside the name')
        try:
            name[:used].decode('utf-8')
        except UnicodeDecodeError:
            bad.append('fragment is not UTF-8')
    if n == 'LMP_accepted' and f['opcode'] not in KNOWN_OPCODES:
        bad.append('accepts opcode %d' % f['opcode'])
    if n == 'LMP_not_accepted':
        if f['opcode'] not in KNOWN_OPCODES:
            bad.append('refuses opcode %d' % f['opcode'])
        if f['error_code'] not in ERROR_CODES:
            bad.append('error %#x' % f['error_code'])
    if n == 'LMP_detach' and f['error_code'] not in (0x05, 0x13, 0x14, 0x15, 0x1A, 0x29, 0x3B):
        bad.append('detach reason %#x' % f['error_code'])
    if n in ('LMP_features_req', 'LMP_features_res'):
        mask = int.from_bytes(f['features'], 'little')
        if any((mask >> b) & 1 for b in FEATURE_RESERVED):
            bad.append('a reserved feature bit is set')
    if n in ('LMP_version_req', 'LMP_version_res'):
        if not 0 <= f['version'] <= 0x0E:
            bad.append('version %d' % f['version'])
    if n == 'LMP_max_slot' and f['max_slots'] not in (1, 3, 5):
        bad.append('max slots %d' % f['max_slots'])
    if n == 'LMP_set_AFH':
        if f['afh_instant'] % 2 or f['afh_instant'] >= 1 << 27:
            bad.append('instant %d' % f['afh_instant'])
        if f['afh_mode'] != 1:
            bad.append('mode %d' % f['afh_mode'])
        m = int.from_bytes(f['afh_channel_map'], 'little')
        if (m >> 79) & 1:
            bad.append('element 79 set')
        if sum((m >> c) & 1 for c in range(79)) < 20:
            bad.append('fewer than 20 channels')
    return bad


def map_bits(channels):
    """The AFH_Channel_Map as 10 bytes: element n (byte n // 8, bit n % 8) is channel n, element 79 zero."""
    m = 0
    for c in channels:
        m |= 1 << c
    return m.to_bytes(10, 'little')


# --- hand-written PDUs -----------------------------------------------------------

def check_by_hand():
    print('PDUs written out by hand from Tables 5.1 and 5.2')
    map_31_50 = bytes([0, 0, 0, 0x80, 0xFF, 0xFF, 0x07, 0, 0, 0])      # elements 31-50, worked out by hand
    check(map_bits(range(31, 51)) == map_31_50, 'the hand-worked map of channels 31-50 equals the bit rule')
    cases = [
        ('LMP_name_req TID 0, offset 14', bt_lmp.encode('LMP_name_req', 0, name_offset=14), bytes([0x02, 14])),
        ('LMP_detach TID 0, 0x13', bt_lmp.encode('LMP_detach', 0, error_code=0x13), bytes([0x0E, 0x13])),
        ('LMP_max_slot TID 1, 5 slots', bt_lmp.encode('LMP_max_slot', 1, max_slots=5), bytes([0x5B, 5])),
        ('LMP_accepted TID 1 of opcode 51', bt_lmp.encode('LMP_accepted', 1, opcode=51), bytes([0x07, 51])),
        ('LMP_not_accepted TID 1 of opcode 46, 0x24',
         bt_lmp.encode('LMP_not_accepted', 1, opcode=46, error_code=0x24), bytes([0x09, 46, 0x24])),
        ('LMP_version_req TID 0, v5.2 (0x0B), company 0x000F, sub 0x1234',
         bt_lmp.encode('LMP_version_req', 0, version=0x0B, company_id=0x000F, subversion=0x1234),
         bytes([0x4A, 0x0B, 0x0F, 0x00, 0x34, 0x12])),
        ('LMP_version_res TID 1', bt_lmp.encode('LMP_version_res', 1, version=0x0E, company_id=0x004C, subversion=0x0001),
         bytes([0x4D, 0x0E, 0x4C, 0x00, 0x01, 0x00])),
        ('LMP_features_req TID 0, bits 0,1,35,63',
         bt_lmp.encode('LMP_features_req', 0, features=(1 << 0 | 1 << 1 | 1 << 35 | 1 << 63).to_bytes(8, 'little')),
         bytes([0x4E, 0x03, 0, 0, 0, 0x08, 0, 0, 0x80])),
        ('LMP_features_res TID 1', bt_lmp.encode('LMP_features_res', 1, features=bytes(range(8))),
         bytes([0x51, 0, 1, 2, 3, 4, 5, 6, 7])),
        ('LMP_set_AFH TID 0, instant 0x1234, enabled, 31-50',
         bt_lmp.encode('LMP_set_AFH', 0, afh_instant=0x1234, afh_mode=1, afh_channel_map=map_31_50),
         bytes([0x78, 0x34, 0x12, 0, 0, 1]) + map_31_50),
        ('LMP_name_res TID 1, name "Bluey" at offset 0',
         bt_lmp.encode('LMP_name_res', 1, name_offset=0, name_length=5, name_fragment=b'Bluey'),
         bytes([0x05, 0, 5]) + b'Bluey' + bytes(9)),
    ]
    for what, got, want in cases:
        check(got == want, '%s: %s' % (what, want.hex()) + ('' if got == want else ' (got %s)' % got.hex()))


# --- the rest ----------------------------------------------------------------------

def check_table():
    print('the encoder\'s table against Table 5.1')
    for name, (code, length, _) in SPEC.items():
        row = bt_lmp.PDUS.get(name)
        check(row is not None and row['opcode'] == code and row['length'] == length,
              '%s: opcode %d, %d bytes (the encoder says %s)' % (name, code, length,
                                                                  None if row is None else (row['opcode'], row['length'])))
    check(set(bt_lmp.PDUS) == set(SPEC), 'the encoder has the eleven PDUs and no others')
    if not os.path.exists(SPEC_TEXT):
        print('  (the specification text is not on this machine: Table 5.1 itself is not read)')
        return
    text = open(SPEC_TEXT, encoding='utf-8', errors='replace').read()
    start = text.index('5.1   PDU summary')
    lines = text[start:start + 40000].split('\n')
    rows = {}
    row = re.compile(r'^ *(LMP_\S+) +(\d+) +(\d+|127/\d+) +\S')
    for i, line in enumerate(lines):
        m = row.match(line)
        if not m:
            continue
        name = m.group(1)
        if name.endswith('-'):               # the table breaks a long name at a hyphen: the rest is on the next line
            name = name[:-1] + lines[i + 1].split()[0]
        rows[name] = (int(m.group(2)), m.group(3))
    for name, (code, length, _) in SPEC.items():
        got = rows.get(name.upper().replace('LMP_', 'LMP_', 1))
        check(got == (length, str(code)), 'Table 5.1 lists %s as %d bytes, opcode %d (it says %s)'
              % (name, length, code, got))


def check_roundtrip(n=400):
    print('round trip: encoder, then the decoder written from the tables (%d PDUs of each kind)' % n)
    rng = np.random.default_rng(31)
    bad = []
    for name in SPEC:
        for i in range(n):
            clk = int(rng.integers(0, 1 << 26)) * 4
            pdu, params, tid = bt_lmp.random_pdu(rng, name, clk)
            try:
                d = decode(pdu)
            except ValueError as e:
                bad.append('%s: %s' % (name, e))
                continue
            problems = validate(d)
            if d['name'] != name or d['tid'] != tid or d['opcode'] != SPEC[name][0] or d['length'] != SPEC[name][1]:
                problems.append('opcode, TID or length differs from what was asked')
            for k, v in params.items():
                if k in d['fields'] and d['fields'][k] != v:
                    problems.append('field %s: %r back for %r' % (k, d['fields'][k], v))
            if problems:
                bad.append('%s: %s' % (name, '; '.join(problems)))
    check(not bad, 'every PDU decodes and validates (%d of %d wrong%s)' % (len(bad), n * len(SPEC),
                                                                          ': ' + bad[0] if bad else ''))


def check_content():
    print('what the random PDUs hold')
    rng = np.random.default_rng(32)
    seen = {k: set() for k in ('offsets', 'errors', 'slots', 'tid_by', 'versions')}
    for _ in range(600):
        for name in ('LMP_name_req', 'LMP_name_res', 'LMP_detach', 'LMP_max_slot', 'LMP_version_res'):
            pdu, _, _ = bt_lmp.random_pdu(rng, name, 0x1000)
            d = decode(pdu)
            f = d['fields']
            seen['tid_by'].add((name, d['tid']))
            if 'name_offset' in f:
                seen['offsets'].add(f['name_offset'])
            if 'error_code' in f:
                seen['errors'].add(f['error_code'])
            if 'max_slots' in f:
                seen['slots'].add(f['max_slots'])
            if 'version' in f:
                seen['versions'].add(f['version'])
    check(seen['slots'] == {1, 3, 5}, 'LMP_max_slot takes 1, 3 and 5: %s' % sorted(seen['slots']))
    check(len(seen['errors']) >= 5, 'LMP_detach reasons vary: %s' % sorted(hex(e) for e in seen['errors']))
    check(len(seen['offsets']) >= 4 and 0 in seen['offsets'], 'name offsets are 0 and other fragment starts: %d values'
          % len(seen['offsets']))
    check(len(seen['versions']) >= 4, 'the LMP version varies: %d values' % len(seen['versions']))
    # the transaction ID: a PDU the Central starts has 0, an answer it gives to the Peripheral's request has 1
    tid = {}
    rng = np.random.default_rng(33)
    for name in SPEC:
        tid[name] = {decode(bt_lmp.random_pdu(rng, name, 8)[0])['tid'] for _ in range(30)}
    check(all(tid[n] == {0} for n in ('LMP_name_req', 'LMP_features_req', 'LMP_version_req', 'LMP_max_slot',
                                      'LMP_set_AFH', 'LMP_detach')),
          'PDUs the Central starts carry TID 0 (Part C 2.4)')
    check(all(tid[n] == {1} for n in ('LMP_name_res', 'LMP_features_res', 'LMP_version_res', 'LMP_accepted',
                                      'LMP_not_accepted')),
          'the Central\'s answers, to a transaction the Peripheral started, carry TID 1')
    # set_AFH: the instant is 96 slots or more ahead of the packet's clock, even
    ok = True
    for _ in range(200):
        clk = int(rng.integers(0, 1 << 26)) * 4
        d = decode(bt_lmp.random_pdu(rng, 'LMP_set_AFH', clk)[0])
        ahead = (d['fields']['afh_instant'] - (clk >> 1)) % (1 << 27)
        ok &= 96 <= ahead < 96 + 2000 and d['fields']['afh_instant'] % 2 == 0
    check(ok, 'LMP_set_AFH instants are even and 96 or more slots after the packet\'s clock (Part C 4.1.4)')
    check(decode(bt_lmp.random_pdu(rng, 'LMP_set_AFH', 4)[0])['fields']['afh_channel_map'] == map_bits(range(31, 51)),
          'LMP_set_AFH carries the map of channels 31-50, element 79 zero')


def check_refusals():
    print('what the encoder refuses')
    for what, call in (
            ('max slots 2', lambda: bt_lmp.encode('LMP_max_slot', 0, max_slots=2)),
            ('an unknown PDU', lambda: bt_lmp.encode('LMP_hold', 0)),
            ('a TID of 2', lambda: bt_lmp.encode('LMP_detach', 2, error_code=0x13)),
            ('an error code of 0x100', lambda: bt_lmp.encode('LMP_detach', 0, error_code=0x100)),
            ('a 15-byte name fragment', lambda: bt_lmp.encode('LMP_name_res', 0, name_offset=0, name_length=15,
                                                               name_fragment=bytes(15))),
            ('a map of 9 bytes', lambda: bt_lmp.encode('LMP_set_AFH', 0, afh_instant=0, afh_mode=1,
                                                       afh_channel_map=bytes(9)))):
        try:
            call()
            check(False, 'refuses %s' % what)
        except (ValueError, KeyError, TypeError):
            check(True, 'refuses %s' % what)


SRC = open(bt_lmp.__file__).read()


def mutant(old, new):
    assert SRC.count(old) == 1, 'mutation target not unique: %r (%d)' % (old, SRC.count(old))
    spec = importlib.util.spec_from_file_location('bt_lmp_mut', bt_lmp.__file__)
    mod = importlib.util.module_from_spec(spec)
    exec(compile(SRC.replace(old, new), bt_lmp.__file__, 'exec'), mod.__dict__)
    return mod


def caught(mod):
    """Whether the decoder written from the tables finds a fault in ``mod``'s PDUs."""
    rng = np.random.default_rng(34)
    for name in SPEC:
        for _ in range(20):
            try:
                pdu, params, tid = mod.random_pdu(rng, name, 0x2000)
                d = decode(pdu)
            except (ValueError, KeyError, TypeError, IndexError):
                return True
            if (d['name'] != name or d['tid'] != tid or d['length'] != SPEC[name][1] or validate(d)
                    or any(k in d['fields'] and d['fields'][k] != v for k, v in params.items())):
                return True
    return False


def check_mutants():
    print('mutants of the encoder, each caught')
    table = SRC[SRC.index('PDUS = {'):]
    first = re.search(r"'LMP_version_res': dict\(opcode=(\d+)", SRC)
    muts = [
        ('LMP_version_res with opcode 39', "'LMP_version_res': dict(opcode=38", "'LMP_version_res': dict(opcode=39"),
        ('LMP_name_req with opcode 2', "'LMP_name_req': dict(opcode=1", "'LMP_name_req': dict(opcode=2"),
        ('LMP_set_AFH one byte too long', "'LMP_set_AFH': dict(opcode=60, length=16", "'LMP_set_AFH': dict(opcode=60, length=17"),
        ('TID in bit 7, not bit 0', "(opcode << 1) | tid", "(opcode << 1) | (tid << 7)"),
    ]
    assert first and table
    for what, old, new in muts:
        try:
            mod = mutant(old, new)
            ok = caught(mod)
        except AssertionError:
            raise
        except Exception:
            ok = True
        check(ok, 'mutant "%s" is caught' % what)


def main():
    check_by_hand()
    check_table()
    check_roundtrip()
    check_content()
    check_refusals()
    check_mutants()
    print('\nRESULT: %s' % ('FAIL (%d)' % len(failures) if failures else 'PASS'))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
