#!/usr/bin/env python3
"""Hold the ACL/LMP writer to what its samples say.

    python scripts/test_bt_synth_acl.py

``scripts/bt_synth_acl.py`` writes DM1/DM3/DM5 ACL traffic carrying LMP PDUs at
five SNR levels, and a DH5 control, for bluey-ox-walker's CRC-assisted bit
correction. The sidecar's per-burst truth (its on-air bits above all) is all that
receiver gets, so this checks the sidecar against the *samples*, from small runs
(24 bursts, 8 of each type, a small block so there are many seams), and against
the Core specification's rules written out again here, never against the
generator's own bookkeeping:

* from the samples alone, at 30 dB: the number of bursts from the envelope, each
  burst's carrier (spectrum, then the phase slope against its own model), its
  start (a model of its bits fitted at a tenth of a sample, within 0.5 sample), its
  level (within 0.3 dB) and its bits (equal to ``air_bits``), and libbtbb decoding
  the header and the payload to ``payload_hex`` with its verdict ok and refusing
  the same bits at another UAP; at 14 dB the start, carrier and level again, from
  the access code and the sidecar's bits as the template;
* the packet rebuilt from the sidecar's fields by an FEC 2/3, a CRC-16, a HEC and
  a whitening written here from the specification's polynomials (by long
  division, not by the encoder's registers and not by ``br.fec23`` /
  ``br.crc16``), and ``air_bits`` rebuilt from ``body_crc_bits_hex``;
* the LMP PDUs decoded by the table-driven decoder of ``test_bt_lmp.py``: opcode,
  TID, name and length equal the sidecar's, every parameter equals ``lmp_params``, and
  every LMP_set_AFH instant is even and at least 96 slots after its packet's own
  clock; every DM1 is LMP, LLID 3; DM3, DM5 and DH5 are LLID 2 only, with a
  20-byte-to-maximum body and no LMP (Table 5.1 lists every PDU as DM1);
* ``air_bits`` and ``body_crc_bits_hex`` canonical: exactly ceil(length / 8) bytes and
  zero unused low bits;
* the full-size plan (204 bursts): 68 of each type, all eleven PDUs in the DM1
  at least six times, the schedule's clocks, gaps and slots, the hop of each
  burst from the clock by ``bt_hop`` directly, the symbol phases and the timing
  fractions;
* the pairing: two levels' files differ only by the signal's scale
  (``(a - noise) / A_a == (b - noise) / A_b``, the noise regenerated here from the
  seed), the control has the same noise, gaps, timing fractions and phases;
* ``n_samples`` against the length of the array and the size of the written file;
* the sweep: the stored table's shape, the levels the generator holds against the
  levels picked from the table here, and the yields at them;
* mutants of the generator, each of which the checks above must catch.
"""
import contextlib
import ctypes
import importlib.util
import json
import os
import sys
import tempfile
from unittest import mock

os.environ.setdefault('OMP_NUM_THREADS', '4')
import numpy as np  # noqa: E402
from scipy.signal import fftconvolve, firwin, lfilter  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from apps import bt_hop, bt_lmp  # noqa: E402
from scripts import bt_ota_check, bt_synth, bt_synth_acl as g  # noqa: E402
from scripts import test_bt_lmp as lmpt  # noqa: E402

FS = 40e6
SPS = 40
CENTER = 2441.0
SLOT = 25000
NOISE = bt_synth.AMPLITUDE ** 2 / 100
NOISE_POWER = NOISE * FS / 1e6                 # mean |iq|^2 of the floor
LAP, UAP = 0x112233, 0x55
ADDRESS = UAP << 24 | LAP
MASK = sum(1 << c for c in range(31, 51))
SEED = 8101
SRC = open(g.__file__).read()
BLOCK = 2 ** 17
SLOTS = {'DM1': 1, 'DM3': 3, 'DM5': 5, 'DH5': 5}
LONGEST = {'DM1': 17, 'DM3': 121, 'DM5': 224, 'DH5': 339}
RESULTS = []
QUIET = [False]
GD = 200
TAPS = firwin(401, 0.7e6, fs=FS)


def check(ok, what):
    ok = bool(ok)
    RESULTS.append((ok, what))
    if not QUIET[0]:
        print('  %s %s' % ('ok  ' if ok else 'FAIL', what))
    return ok


@contextlib.contextmanager
def quiet():
    """Collect the checks of a block without printing, yielding the list of those that failed."""
    QUIET[0] = True
    mark = len(RESULTS)
    failed = []
    try:
        yield failed
    finally:
        QUIET[0] = False
        failed.extend(w for ok, w in RESULTS[mark:] if not ok)
        del RESULTS[mark:]


# --- the specification's rules, written again ----------------------------------------------

def gf2_mod(value, poly):
    """``value`` modulo ``poly``, polynomials over GF(2) as integers."""
    dp = poly.bit_length()
    while value.bit_length() >= dp:
        value ^= poly << (value.bit_length() - dp)
    return value


def msb_int(bits):
    """Bits in transmission order as an integer, the first the highest power: the way a shift register
    fed first-bit-first holds a polynomial."""
    v = 0
    for b in bits:
        v = v << 1 | b
    return v


def int_bits(value, width):
    """``width`` bits of ``value``, the highest power first."""
    return [(value >> (width - 1 - i)) & 1 for i in range(width)]


def my_hec(ten, uap):
    """Part B 7.1.1: g = D^8 + D^7 + D^5 + D^2 + D + 1, the register preset with the UAP (UAP0 in
    position 0), the ten bits fed first-first, read out from the top position: the remainder of
    (UAP * D^10 + bits * D^8) divided by g."""
    return int_bits(gf2_mod((uap << 10) ^ (msb_int(ten) << 8), 0x1A7), 8)


def my_crc(bits, uap):
    """Part B 7.1.2: CRC-CCITT, D^16 + D^12 + D^5 + 1, the low eight positions preset with the UAP, the
    high eight zero."""
    return int_bits(gf2_mod((uap << len(bits)) ^ (msb_int(bits) << 16), 0x11021), 16)


def my_fec23(bits):
    """Part B 7.5: the (15,10) shortened Hamming code, g = (D + 1)(D^4 + D + 1) = D^5 + D^4 + D^2 + 1; ten
    data bits (the last block padded with zeros) then the five parity bits, the remainder of
    data * D^5 divided by g."""
    bits = list(bits) + [0] * (-len(bits) % 10)
    out = []
    for n in range(0, len(bits), 10):
        block = bits[n:n + 10]
        out += block + int_bits(gf2_mod(msb_int(block) << 5, 0x35), 5)
    return out


def my_whitening(clk, n):
    """Part B 7.2: D^7 + D^4 + 1, position 0 = CLK1 up to position 5 = CLK6, a 1 in position 6, the
    output taken from position 6 and fed back into position 0 and, added, position 4."""
    s = (clk >> 1) & 0x3F | 0x40
    out = []
    for _ in range(n):
        o = (s >> 6) & 1
        out.append(o)
        s = ((s << 1) & 0x7F) | o
        if o:
            s ^= 1 << 4
    return out


def lsb(value, width):
    return [(value >> i) & 1 for i in range(width)]


def hex_bits(h, n):
    """The first ``n`` bits of the hex string, the top bit of the first byte first."""
    return [int(c) for c in bin(int(h, 16))[2:].zfill(len(h) * 4)[:n]] if h else []


def derive_packet(e, lt_addr=1):
    """The packet's on-air bits from the sidecar's fields and the specification's rules alone:
    ``(body_crc_bits, air_bits)``."""
    ptype = e['ptype']
    code = {'DM1': 0b0011, 'DM3': 0b1010, 'DM5': 0b1110, 'DH5': 0b1111}[ptype]
    two = ptype != 'DM1'
    body = bytes.fromhex(e['payload_hex'])
    if two:
        head = lsb(e['llid'], 2) + lsb(1, 1) + lsb(len(body), 10) + [0] * 3
    else:
        head = lsb(e['llid'], 2) + lsb(1, 1) + lsb(len(body), 5)
    payload = head + [(byte >> i) & 1 for byte in body for i in range(8)]
    payload += my_crc(payload, e['uap'])
    ten = lsb(lt_addr, 3) + lsb(code, 4) + [e['flow'], e['arqn'], e['seqn']]
    header = ten + my_hec(ten, e['uap'])
    return payload, header


def whiten_and_code(header, payload, clk, fec, lap):
    """Whiten header and payload in one run of the sequence, then FEC 1/3 on the header and, if ``fec``,
    FEC 2/3 on the payload; behind the access code."""
    w = my_whitening(clk, len(header) + len(payload))
    h = [b ^ x for b, x in zip(header, w)]
    p = [b ^ x for b, x in zip(payload, w[len(header):])]
    code = [b for b in h for _ in range(3)] + (my_fec23(p) if fec else p)
    return list(br.access_code(lap)) + code


# --- what the files should be, from the task ------------------------------------------------

def check_sidecar_truth(side, label, iq_len=None):
    """Every burst of ``side`` rebuilt by the rules above, the LMP PDUs decoded by the table-driven
    decoder, and the sidecar's own consistency."""
    bursts = side['bursts']
    bad = {k: [] for k in ('payload', 'air', 'air_from_body', 'lens', 'hdr', 'lmp', 'lmp_params', 'afh', 'hexcanon', 'llid', 'keys', 'bitsfmt')}
    keys = set(side['per_burst_keys'])
    for i, e in enumerate(bursts):
        if set(e) != keys:
            bad['keys'].append(i)
        payload, header = derive_packet(e)
        for hx, ln in (('air_bits', 'air_bits_length'), ('body_crc_bits_hex', 'body_crc_bits_length')):
            h, n_ = e[hx], e[ln]
            pad = 8 * (len(h) // 2) - n_
            if len(h) != 2 * -(-n_ // 8) or (int(h, 16) & ((1 << pad) - 1)):
                bad['hexcanon'].append(i)
                break
        body_bits = hex_bits(e['body_crc_bits_hex'], e['body_crc_bits_length'])
        if body_bits != payload or len(e['body_crc_bits_hex']) != 2 * -(-len(payload) // 8):
            bad['payload'].append(i)
        fec = e['ptype'] != 'DH5'
        air = whiten_and_code(header, payload, e['clk'], fec, e['lap'])
        if hex_bits(e['air_bits'], e['air_bits_length']) != air:
            bad['air'].append(i)
        # and from the sidecar's own body_crc bits, with the header the sidecar states
        hdr18 = lsb(e['header18'], 18)
        air2 = whiten_and_code(hdr18, body_bits, e['clk'], fec, e['lap'])
        if hex_bits(e['air_bits'], e['air_bits_length']) != air2:
            bad['air_from_body'].append(i)
        want_len = 72 + 54 + (-(-len(payload) // 10) * 15 if fec else len(payload))
        if e['air_bits_length'] != want_len or e['end_sample'] - e['start_sample'] != want_len * SPS:
            bad['lens'].append(i)
        if (e['header18'] != sum(b << j for j, b in enumerate(header)) or e['lt_addr'] != 1 or e['flow'] != 1
                or e['arqn'] != 1 or e['seqn'] != i & 1):
            bad['hdr'].append(i)
        # the LLID, the length and the type rules of the task
        n = e['payload_length']
        is_lmp = e['lmp_name'] is not None
        if (e['llid'] != (3 if is_lmp else 2) or n != len(bytes.fromhex(e['payload_hex']))
                or (e['ptype'] == 'DM1' and not is_lmp)
                or (e['ptype'] != 'DM1' and (is_lmp or not L2MIN <= n <= LONGEST[e['ptype']]))
                or (e['ptype'] == 'DM1' and not 1 <= n <= 17)):
            bad['llid'].append(i)
        if is_lmp:
            try:
                d = lmpt.decode(bytes.fromhex(e['payload_hex']))
                problems = lmpt.validate(d)
                if (d['name'] != e['lmp_name'] or d['opcode'] != e['lmp_opcode'] or d['tid'] != e['lmp_tid']
                        or d['length'] != n or problems):
                    bad['lmp'].append(i)
                # every decoded parameter, integers and byte strings alike, is the sidecar's lmp_params
                if e['lmp_name'] == 'LMP_set_AFH':
                    inst = d['fields']['afh_instant']
                    ahead = (inst - (e['clk'] >> 1)) % (1 << 27)
                    if inst % 2 or not 96 <= ahead < 96 + 2400:
                        bad['afh'].append(i)
                shown = {k: (v.hex() if isinstance(v, bytes) else v) for k, v in d['fields'].items()}
                if shown != e['lmp_params']:
                    bad['lmp_params'].append(i)
            except ValueError:
                bad['lmp'].append(i)
        elif e['lmp_opcode'] is not None or e['lmp_tid'] is not None or e['lmp_params'] is not None:
            bad['lmp'].append(i)
    names = {'payload': 'body_crc_bits_hex is the payload header, body and CRC-16 (UAP seed) written from the spec',
             'air': 'air_bits is that packet whitened, then FEC 1/3 on the header and FEC 2/3 on the payload (DM)',
             'air_from_body': 'air_bits is rebuilt from body_crc_bits_hex and header18 alone',
             'lens': 'air_bits_length and end_sample are the packet\'s bits (126 + payload, with the FEC)',
             'hdr': 'header18, lt_addr 1, FLOW 1, ARQN 1, SEQN alternating',
             'llid': 'LLID, payload_length and the type rules (DM1: LMP 1-17 bytes; DM3/DM5/DH5: LLID 2, 20..max, no LMP)',
             'lmp': 'the LMP PDU decodes (table-driven decoder) to lmp_name, lmp_opcode, lmp_tid and its length',
             'lmp_params': 'every parameter decoded from the PDU bytes (integers and byte strings) is the sidecar\'s lmp_params',
             'afh': 'every LMP_set_AFH instant is even and at least 96 slots (CLK27-1 units) after the packet\'s own clock',
             'hexcanon': 'air_bits and body_crc_bits_hex are ceil(length / 8) bytes with zero unused low bits',
             'keys': 'every burst has the same keys as per_burst_keys',
             'bitsfmt': ''}
    for k, idx in bad.items():
        if k == 'bitsfmt':
            continue
        check(not idx, '%s: %s (%d of %d wrong%s)' % (label, names[k], len(idx), len(bursts), ' - first %s' % idx[:3] if idx else ''))


L2MIN = 20


def check_plan_truth(side, label, kind='dm', bursts=204):
    """The counts, the schedule, the hops and the draws, from the sidecar and the rules of the task."""
    b = side['bursts']
    types = [e['ptype'] for e in b]
    if kind == 'dm':
        n = bursts // 3
        check(all(types.count(t) == n for t in ('DM1', 'DM3', 'DM5')) and len(b) == bursts,
              '%s: %d bursts, %d each of DM1, DM3 and DM5 (%s)' % (label, bursts, n, {t: types.count(t) for t in sorted(set(types))}))
        check(side['equal_counts'] is True and side['type_counts'] == {t: n for t in ('DM1', 'DM3', 'DM5')}
              and 'same number' in side['equal_counts_statement'], '%s: the sidecar states the equal counts' % label)
        check(types != sorted(types) and types[:8] != ['DM1'] * 8, '%s: the order of the types is shuffled' % label)
    else:
        check(types == ['DH5'] * bursts and side['equal_counts'] is False, '%s: the control is %d DH5' % (label, bursts))
    clk, ok_gap, ok_start, ok_hop, ok_phase, ok_master = b[0]['clk'], True, True, True, True, True
    gaps = []
    for i, e in enumerate(b):
        ok_master &= e['clk'] % 4 == 0 and e['uap'] == UAP and e['lap'] == LAP
        ok_hop &= e['channel'] == bt_hop.hop_channel(e['clk'], ADDRESS, MASK) and 31 <= e['channel'] <= 50
        ok_hop &= e['channel_mhz'] == 2402 + e['channel']
        ok_phase &= e['start_sample'] % SPS == (0, 20)[i % 2] and e['symbol_phase'] == (0, 20)[i % 2]
        if i:
            p = b[i - 1]
            steps = (e['clk'] - p['clk']) // 2           # slots
            idle, rem = divmod(steps - SLOTS[p['ptype']] - 1, 2)
            ok_gap &= rem == 0 and 0 <= idle <= 2 and (e['clk'] - p['clk']) % 4 == 0
            gaps.append(idle)
            ok_start &= (e['start_sample'] - p['start_sample'] == steps * SLOT + (0, 20)[i % 2] - (0, 20)[(i - 1) % 2])
            ok_gap &= steps >= SLOTS[p['ptype']] + 1                  # a DM5 is followed by at least a slot
    check(ok_master, '%s: every burst starts on a master slot (CLK1-0 = 0) of the master 0x112233/0x55' % label)
    check(ok_gap and set(gaps) == {0, 1, 2}, '%s: each next burst starts slots + 1 + 2 x idle later, idle in {0,1,2} (all three met: %s)'
          % (label, sorted(set(gaps))))
    check(ok_start, '%s: start_sample follows the clock, 25000 samples a slot, plus the symbol phase' % label)
    check(ok_hop, '%s: every channel is bt_hop.hop_channel of the burst\'s clock over the map 31-50' % label)
    check(ok_phase, '%s: symbol phases alternate 0 and 20 per burst' % label)
    frac = np.array([e['timing_frac'] for e in b])
    check(len(b) < 60 or (0 <= frac.min() and frac.max() < 1 and abs(frac.mean() - 0.5) < 0.08 and frac.std() > 0.25),
          '%s: timing fractions are uniform in [0, 1) (mean %.2f)' % (label, frac.mean()))
    check(b[0]['clk'] == g.CLK0 and side['clk'] == b[0]['clk'], '%s: the first burst is on clock CLK0' % label)


def check_lmp_plan(side, label):
    names = [e['lmp_name'] for e in side['bursts'] if e['ptype'] == 'DM1']
    counts = {n: names.count(n) for n in lmpt.SPEC}
    check(all(c >= 6 for c in counts.values()) and None not in names,
          '%s: all eleven LMP PDUs are in the DM1, each at least six times (%d..%d)' % (label, min(counts.values()), max(counts.values())))
    check(all(e['lmp_name'] is not None for e in side['bursts'] if e['ptype'] == 'DM1'),
          '%s: every DM1 is LMP' % label)
    big = [e for e in side['bursts'] if e['ptype'] in ('DM3', 'DM5')]
    check(all(e['lmp_name'] is None and e['llid'] == 2 for e in big),
          '%s: no LMP in any DM3 or DM5 (Table 5.1: the PDUs are DM1 only); all %d are LLID 2' % (label, len(big)))
    lens = [e['payload_length'] for e in big]
    check(min(lens) >= 20 and max(lens) > 100, '%s: the L2CAP bodies run from 20 bytes to the maximum (%d..%d)' % (label, min(lens), max(lens)))
    check_table_51(side, label)
    l2 = big
    ok = True
    for e in l2:
        body = bytes.fromhex(e['payload_hex'])
        ok &= int.from_bytes(body[:2], 'little') == len(body) - 4 and int.from_bytes(body[2:4], 'little') == 0x40
    check(ok, '%s: an L2CAP body starts with a basic header (length, channel 0x0040)' % label)
    for ptype, cap in (('DM3', 121), ('DM5', 224)):
        got = max(e['payload_length'] for e in side['bursts'] if e['ptype'] == ptype)
        check(got <= cap and got > cap - 25, '%s: the longest %s body is %d, under the packet\'s %d' % (label, ptype, got, cap))


#: Table 5.1's packet type column for the eleven PDUs, written by hand from the table: every one is DM1 (some DM1/DV).
PINNED_TYPES = {'LMP_name_req': 'DM1/DV', 'LMP_name_res': 'DM1', 'LMP_accepted': 'DM1/DV', 'LMP_not_accepted': 'DM1/DV',
                'LMP_detach': 'DM1/DV', 'LMP_features_req': 'DM1/DV', 'LMP_features_res': 'DM1/DV',
                'LMP_version_req': 'DM1/DV', 'LMP_version_res': 'DM1/DV', 'LMP_max_slot': 'DM1/DV', 'LMP_set_AFH': 'DM1'}


def spec_packet_types():
    """``{pdu: 'DM1' or 'DM1/DV'}`` read from the rows of Table 5.1 in the specification text, or None if it is
    not on this machine."""
    import re
    if not os.path.exists(lmpt.SPEC_TEXT):
        return None
    text = open(lmpt.SPEC_TEXT, encoding='utf-8', errors='replace').read()
    start = text.index('5.1   PDU summary')
    lines = text[start:start + 40000].split('\n')
    row = re.compile(r'^ *(LMP_\S+) +(\d+) +(\d+|127/\d+) +(\S+)')
    out = {}
    for i, line in enumerate(lines):
        m = row.match(line)
        if m:
            name = m.group(1)
            if name.endswith('-'):
                name = name[:-1] + lines[i + 1].split()[0]
            out[name] = m.group(4)
    return out


def check_table_51(side, label):
    """Every LMP PDU of the plan is on a packet type its Table 5.1 row allows."""
    rows = spec_packet_types()
    names = {e['lmp_name'] for e in side['bursts'] if e['lmp_name']}
    ok_pin = all(PINNED_TYPES[n].split('/')[0] == 'DM1' for n in names)
    check(ok_pin, '%s: the hand-pinned Table 5.1 rows of the PDUs used are DM1 or DM1/DV' % label)
    if rows is not None:
        table = {n: rows.get(n.upper().replace('LMP_', 'LMP_', 1)) for n in lmpt.SPEC}
        check(all(table[n] == PINNED_TYPES[n] for n in lmpt.SPEC),
              '%s: Table 5.1 in the specification text gives the pinned packet type for all eleven (%s)'
              % (label, sorted(set(table.values()))))
        allowed = {n: set(t for t in (table[n] or '').split('/') if t != 'DV') for n in lmpt.SPEC}
    else:
        allowed = {n: {PINNED_TYPES[n].split('/')[0]} for n in lmpt.SPEC}
    bad = [i for i, e in enumerate(side['bursts']) if e['lmp_name'] and e['ptype'] not in allowed[e['lmp_name']]]
    check(not bad, '%s: every LMP PDU of the plan is on a packet type its Table 5.1 row allows (%d on another, first %s)'
          % (label, len(bad), bad[:3]))


# --- reading the samples the way a receiver has to ------------------------------------------

def rough_bursts(iq):
    """``(start, end)`` of each burst from the 200-sample mean of |iq|^2 against twice the floor."""
    n = len(iq)
    p = np.empty(n)
    for lo in range(0, n, 1 << 22):
        x = iq[lo:lo + (1 << 22)]
        p[lo:lo + len(x)] = x.real.astype(np.float64) ** 2 + x.imag.astype(np.float64) ** 2
    c = np.concatenate([[0.0], np.cumsum(p)])
    w = 200
    up = (c[w:] - c[:-w]) / w > 2 * NOISE_POWER
    edges = np.flatnonzero(np.diff(up.astype(np.int8)))
    starts, ends = edges[::2] + 1 + w // 2, edges[1::2] + 1 + w // 2
    spans = []
    for a, b in zip(starts.tolist(), ends.tolist()):
        if spans and a - spans[-1][1] < 300:
            spans[-1][1] = b
        else:
            spans.append([a, b])
    return [(a, b) for a, b in spans if b - a > 1500]


def carrier_of(iq, s0, s1):
    """The carrier of a burst in MHz from the centroid of its spectrum, and the nearest channel."""
    x = np.asarray(iq[s0 + 250:s1 - 250])
    nfft = 1024
    m = len(x) // nfft
    x = x[:m * nfft].reshape(m, nfft) * np.hanning(nfft)
    spec = np.sum(np.abs(np.fft.fft(x, axis=1)) ** 2, axis=0)
    freq = np.fft.fftfreq(nfft, 1 / FS)
    near = np.abs(freq - freq[int(np.argmax(spec))]) < 500e3
    mhz = CENTER + np.sum(freq[near] * spec[near]) / np.sum(spec[near]) / 1e6
    return mhz, int(round(mhz - 2402))


def shifted(iq, lo, hi, mhz):
    """Samples lo..hi mixed down by a carrier ``mhz``, the oscillator at the absolute sample number."""
    n = np.arange(lo, hi)
    cycles = (mhz - CENTER) * 1e6 / FS * n
    return np.asarray(iq[lo:hi]).astype(np.complex128) * np.exp(-2j * np.pi * (cycles - np.floor(cycles)))


def coarse_demod(iq, lo_hint, hi_hint, mhz, nbits):
    """The access code's position and the bits of a burst, without the sidecar's start: shift down, a
    0.7 MHz low-pass, the frequency from consecutive samples read at the symbol centres, the 68 known
    bits correlated to find the start, the threshold fitted on them. ``(start, bits)``."""
    lo, hi = max(0, lo_hint - 1500), min(len(iq), hi_hint + 2500)
    bb = lfilter(TAPS, 1, shifted(iq, lo, hi, mhz))
    d = np.angle(bb[1:] * np.conj(bb[:-1]))
    dm = d - np.convolve(d, np.ones(2001) / 2001, 'same')
    a68 = np.array(br.access_code(LAP)[:68]) * 2.0 - 1.0
    tmpl = np.repeat(a68, SPS)
    tmpl -= tmpl.mean()
    r = fftconvolve(dm, tmpl[::-1], 'valid')
    # the burst's own access code: the envelope puts its start within a few hundred samples of lo_hint, and a
    # long payload can hold runs that correlate better with the 68 bits than the access code does
    a = max(0, lo_hint - lo - 600 + GD)
    p = a + int(np.argmax(r[a:lo_hint - lo + 600 + GD]))
    centres = p + (np.arange(nbits) + 0.5) * SPS - 0.5
    f = np.interp(centres, np.arange(len(d)), d)
    slope, thr = np.polyfit(a68, f[:68], 1)
    return lo + p - GD, (f > thr).astype(np.uint8)


_MODELS = {}


def model(bits, frac):
    """The burst the generator's modulator makes of ``bits`` at a timing fraction: ``(samples, lead)``."""
    key = (bytes(bits), round(frac, 6))
    if key not in _MODELS:
        if len(_MODELS) > 400:
            _MODELS.clear()
        _MODELS[key] = br.gfsk(np.asarray(bits), FS, h=br.GFSK_H, delay=frac)
    return _MODELS[key]


def fit_burst(iq, e, bits, coarse):
    """Fit the burst's own modulation to the samples: ``start`` (the first preamble sample plus the
    timing fraction, to a fraction of a sample), ``amp`` (|iq| of the unit-amplitude model), the carrier
    error in Hz from the phase slope against the model and the carrier phase.

    ``bits`` are the burst's bits, as decoded (30 dB) or from the sidecar (14 dB); ``coarse`` is a start
    to within a few samples. The score of a start is |<z, model>|, scanned in tenths of a sample over the burst, then refined by a parabola. (All of the burst's bits are in the model: the more of the burst, the less the
    noise moves the peak.)"""
    z = shifted(iq, int(coarse) - 400, int(coarse) + len(bits) * SPS + 800, e['channel_mhz'])
    base = int(coarse) - 400
    head = bits
    scores = {}
    for i in range(-5, 6):
        for tenth in range(10):
            frac = tenth / 10
            m, lead = model(head, frac)
            lo = int(coarse) + i - lead - base
            seg = z[lo:lo + len(m)]
            if lo < 0 or len(seg) < len(m):
                continue
            scores[i + frac] = abs(np.vdot(m, seg))
    keys = sorted(scores)
    best = max(keys, key=scores.get)
    j = keys.index(best)
    delta = 0.0
    if 0 < j < len(keys) - 1:
        y0, y1, y2 = scores[keys[j - 1]], scores[best], scores[keys[j + 1]]
        den = y0 - 2 * y1 + y2
        delta = 0.1 * 0.5 * (y0 - y2) / den if den else 0.0
    start = int(coarse) + best + delta
    # the whole burst at that start: amplitude, phase, carrier slope
    i0 = int(np.floor(start))
    frac = start - i0
    m, lead = br.gfsk(np.asarray(bits), FS, h=br.GFSK_H, delay=frac)
    lo = i0 - lead - base
    seg = z[lo:lo + len(m)]
    gain = np.vdot(m, seg) / np.vdot(m, m)
    prod = seg * np.conj(m)
    lag = 400
    df = np.angle(np.sum(prod[lag:] * np.conj(prod[:-lag]))) / (2 * np.pi * lag) * FS
    return dict(start=start, amp=abs(gain), phase=float(np.angle(gain)), df=float(df))


# --- libbtbb ----------------------------------------------------------------------------

_lib = bt_ota_check.libbtbb()


def btbb(bits, e, uap):
    """What libbtbb makes of a burst's bits at ``uap`` and the burst's CLK6-1: ``(header_ok, verdict,
    payload bytes)``; the verdict of a good payload is 10."""
    raw = bytes(int(b) for b in bits[4:])
    pkt = _lib.btbb_packet_new()
    try:
        _lib.btbb_packet_set_data(pkt, ctypes.cast(ctypes.c_char_p(raw), ctypes.POINTER(ctypes.c_char)), len(raw), 0,
                                  ((e['clk'] >> 1) & 0x3F) << 1)
        _lib.btbb_packet_set_uap(pkt, uap)
        _lib.btbb_packet_set_flag(pkt, 4, 1)
        _lib.btbb_packet_set_flag(pkt, 0, 1)
        if _lib.btbb_decode_header(pkt) != 1:
            return False, None, b''
        hdr = _lib.btbb_packet_get_header_packed(pkt) & 0x3FFFF
        verdict = _lib.btbb_decode_payload(pkt)
        want = bytes.fromhex(e['payload_full_hex'])
        buf = (ctypes.c_char * (len(want) + 32))()
        _lib.btbb_get_payload_packed(pkt, buf)
        return hdr == e['header18'], verdict, bytes(buf[:len(want)])
    finally:
        _lib.btbb_packet_unref(pkt)


# --- the samples of one small file -------------------------------------------------------------

def check_samples(iq, side, label, blind=True, bits_exact=True, limit=None, tol_db=0.3, tol_start=0.5):
    """Burst count, carrier, start, level and (at high SNR) bits and libbtbb, from the samples alone."""
    bursts = side['bursts'][:limit] if limit else side['bursts']
    snr = side['snr_db']
    print('\nThe samples of %s: %d bursts, %.1f dB' % (label, len(side['bursts']), snr))
    check(len(iq) == side['n_samples'], '%s: n_samples %d is the length of the array %d' % (label, side['n_samples'], len(iq)))
    spans = rough_bursts(iq) if blind else None
    if blind:
        check(len(spans) == len(side['bursts']), '%s: the envelope shows %d bursts, the sidecar has %d' % (label, len(spans), len(side['bursts'])))
        if len(spans) != len(side['bursts']):
            return
    ok_ch, ok_df, ok_start, ok_lvl, ok_bits, ok_btbb, ok_wrong = [], [], [], [], [], [], []
    starts, dfs, lvls, phases = [], [], [], []
    for i, e in enumerate(bursts):
        nb = e['air_bits_length']
        want = hex_bits(e['air_bits'], nb)
        if blind:
            s0, s1 = spans[i]
            mhz, ch = carrier_of(iq, s0, s1)
            ok_ch.append(ch == e['channel'] and abs(mhz - e['channel_mhz']) < 0.15)
        else:
            s0, s1 = e['start_sample'] - 300, e['end_sample'] + 300
        coarse_start, got = coarse_demod(iq, s0, s1, e['channel_mhz'], nb)
        template = got if bits_exact else np.array(want)
        if bits_exact:
            ok_bits.append(list(got) == want)
        fit = fit_burst(iq, e, template, coarse_start)
        starts.append(fit['start'] - (e['start_sample'] + e['timing_frac']))
        dfs.append(fit['df'])
        lvls.append(10 * np.log10(fit['amp'] ** 2 / NOISE) - snr)
        phases.append(fit['phase'] - e['burst_phase'])
        if bits_exact:
            hdr_ok, verdict, got_bytes = btbb(got, e, UAP)
            full = bytes.fromhex(e['payload_full_hex'])
            ok_btbb.append(hdr_ok and verdict == 10 and got_bytes == full
                           and got_bytes[(2 if e['ptype'] != 'DM1' else 1):][:e['payload_length']] == bytes.fromhex(e['payload_hex']))
            h2, v2, _ = btbb(got, e, UAP ^ 0x10)
            ok_wrong.append(not (h2 and v2 == 10))
    if blind:
        check(all(ok_ch), '%s: every burst\'s carrier, from the spectrum of the raw samples, is its channel and within 150 kHz of it (%d of %d)'
              % (label, sum(ok_ch), len(ok_ch)))
    starts, dfs, lvls = np.array(starts), np.array(dfs), np.array(lvls)
    check(np.abs(starts).max() <= tol_start, '%s: start_sample + timing_frac is where the model of the burst fits, worst %.3f samples (limit %.1f), mean %+.3f'
          % (label, np.abs(starts).max(), tol_start, starts.mean()))
    check(abs(starts.mean()) < 0.1, '%s: and the fits are not biased (mean %+.3f samples)' % (label, starts.mean()))
    check(np.abs(dfs).max() < 1000, '%s: carrier within 1 kHz of channel_mhz (phase slope against the burst\'s own model), worst %.1f Hz'
          % (label, np.abs(dfs).max()))
    check(np.abs(lvls).max() < tol_db, '%s: the level from the samples is snr_db within %.2f dB (limit %.1f), worst %.3f'
          % (label, tol_db, tol_db, np.abs(lvls).max()))
    pw = np.angle(np.exp(1j * np.array(phases)))
    check(np.abs(pw).max() < 0.3, '%s: the carrier phase fitted is the sidecar\'s burst_phase, worst %.3f rad' % (label, np.abs(pw).max()))
    if bits_exact:
        check(all(ok_bits), '%s: air_bits equal the bits demodulated from the samples, every burst (%d of %d)' % (label, sum(ok_bits), len(ok_bits)))
        check(all(ok_btbb), '%s: libbtbb decodes header and payload to payload_hex, verdict ok (%d of %d)' % (label, sum(ok_btbb), len(ok_btbb)))
        check(all(ok_wrong), '%s: libbtbb refuses the same bits at another UAP' % label)


# --- the pairing ---------------------------------------------------------------------------------

def my_noise(seed, total, block=BLOCK):
    """The floor of a file, from the specification of the task: block i from default_rng([seed, 1, i]),
    per-component sigma sqrt(NOISE * (fs / 1e6) / 2), I then Q."""
    out = np.empty(total, dtype=np.complex64)
    sigma = np.sqrt(NOISE * (FS / 1e6) / 2)
    for i, lo in enumerate(range(0, total, block)):
        hi = min(lo + block, total)
        rng = np.random.default_rng([seed, 1, i])
        out.real[lo:hi] = rng.normal(0, sigma, hi - lo)
        out.imag[lo:hi] = rng.normal(0, sigma, hi - lo)
    return out


def check_pairing(a, b, label, seed=SEED, tol=2e-4):
    """Two levels' files differ by the signal's scale only."""
    (iq_a, side_a), (iq_b, side_b) = a, b
    check(len(iq_a) == len(iq_b), '%s: same length' % label)
    if len(iq_a) != len(iq_b):
        return
    noise = my_noise(seed, len(iq_a))
    amp_a = np.sqrt(NOISE * 10 ** (side_a['snr_db'] / 10))
    amp_b = np.sqrt(NOISE * 10 ** (side_b['snr_db'] / 10))
    ua = (iq_a.astype(np.complex128) - noise) / amp_a
    ub = (iq_b.astype(np.complex128) - noise) / amp_b
    worst = float(np.abs(ua - ub).max())
    check(worst < tol, '%s: (a - noise) / A_a == (b - noise) / A_b to rounding, the noise regenerated from seed %d: worst %.2e'
          % (label, seed, worst))
    check(side_a['seed'] == seed == side_b['seed'], '%s: both sidecars say seed %d' % (label, seed))
    keys = ('clk', 'channel', 'ptype', 'payload_hex', 'air_bits', 'start_sample', 'timing_frac', 'symbol_phase', 'lmp_name', 'burst_phase')
    same = all(all(x[k] == y[k] for k in keys) for x, y in zip(side_a['bursts'], side_b['bursts']))
    check(same and len(side_a['bursts']) == len(side_b['bursts']),
          '%s: schedule, clocks, channels, payload bytes, on-air bits, timing fractions and phases are the same burst by burst' % label)
    check(side_a['snr_db'] != side_b['snr_db'] and all(e['snr_db'] == side_a['snr_db'] for e in side_a['bursts']),
          '%s: only snr_db differs (%.1f and %.1f)' % (label, side_a['snr_db'], side_b['snr_db']))


def check_shipped_block():
    """The noise of a file made with the shipped block size (2**22), not the tests' small one, is the
    regenerated floor wherever there is no burst."""
    iq, side = g.synthesise_acl('dm', 20.0, bursts=3)
    blk = side['noise_block_samples']
    free = np.ones(len(iq), bool)
    for e in side['bursts']:
        free[max(0, e['start_sample'] - 400):e['end_sample'] + 400] = False
    noise = my_noise(side['seed'], len(iq), block=4194304)
    check(blk == 4194304 and len(iq) % blk == 0 and np.array_equal(iq[free], noise[free]),
          'a file at the shipped block size (%d) is the floor regenerated from the seed in blocks of 4194304, off the bursts (%d samples)'
          % (blk, free.sum()))


def check_control(dm, ctl, label):
    """The control has the DM files' noise, gaps, timing fractions and phases, and the same seed."""
    (iq_a, side_a), (iq_c, side_c) = dm, ctl
    n = min(len(side_a['bursts']), len(side_c['bursts']))
    same = all(side_a['bursts'][k]['timing_frac'] == side_c['bursts'][k]['timing_frac']
               and side_a['bursts'][k]['burst_phase'] == side_c['bursts'][k]['burst_phase']
               and side_a['bursts'][k]['symbol_phase'] == side_c['bursts'][k]['symbol_phase'] for k in range(n))
    check(same, '%s: timing fractions, carrier phases and symbol phases are those of the DM files, burst by burst' % label)
    m = min(len(iq_a), len(iq_c))
    noise = my_noise(side_a['seed'], m)
    free = np.ones(m, bool)
    for s in (side_a, side_c):
        for e in s['bursts']:
            free[max(0, e['start_sample'] - 400):min(m, e['end_sample'] + 400)] = False
    check(free.sum() > 1e5 and np.array_equal(iq_c[:m][free], noise[free]) and np.array_equal(iq_a[:m][free], noise[free]),
          '%s: wherever neither file has a burst, both are the regenerated noise exactly (%d samples)' % (label, free.sum()))


# --- the sweep ---------------------------------------------------------------------------------------

def my_pick(table):
    """The four SNRs of 95, 80, 65 and 50 % pooled yield by linear interpolation between the sweep's points,
    to 0.5 dB, and 3 dB below the last."""
    snr = [r['snr_db'] for r in table]
    y = [r['pooled']['yield'] for r in table]
    out = []
    for target in (0.95, 0.80, 0.65, 0.50):
        for k in range(len(snr) - 1, 0, -1):
            if y[k - 1] < target <= y[k]:
                out.append(round((snr[k - 1] + (target - y[k - 1]) / (y[k] - y[k - 1]) * (snr[k] - snr[k - 1])) * 2) / 2)
                break
    return out + [out[-1] - 3.0]


def check_sweep():
    print('\nThe sweep and the levels')
    ok = os.path.exists(g.SWEEP_PATH)
    check(ok, 'the sweep is stored in %s' % os.path.basename(g.SWEEP_PATH))
    if not ok:
        return
    sw = json.load(open(g.SWEEP_PATH))
    table = sw['table']
    steps = {round(b['snr_db'] - a['snr_db'], 6) for a, b in zip(table, table[1:])}
    check(steps == {1.0}, 'SNR in 1 dB steps, %.0f to %.0f dB' % (table[0]['snr_db'], table[-1]['snr_db']))
    check(all(set(r['per_type']) == {'DM1', 'DM3', 'DM5'} and all(v['bursts'] == 204 for v in r['per_type'].values())
              and r['pooled']['bursts'] == 612 for r in table), 'each cell has 204 bursts per type, 612 pooled (three seeds)')
    check(all(r['pooled']['crc_ok'] >= r['pooled']['payload_exact'] for r in table)
          and abs(table[-1]['pooled']['yield'] - 1) < 1e-9 and table[0]['pooled']['yield'] < 0.05,
          'the yield runs from under 5 % to 100 %, and CRC passes are at least the exact payloads')
    y = [r['pooled']['yield'] for r in table]
    check(all(b >= a - 0.03 for a, b in zip(y, y[1:])), 'the pooled yield rises with SNR (to within 3 % of sampling noise)')
    check('ANOTHER RECEIVER' in sw['caveat'] and 'libbtbb' in sw['decoder'], 'the sweep says what decoded it and that another receiver differs')
    check(g.LEVELS_DB == sw['levels_chosen'] == g.pick_levels(sw) == my_pick(table),
          'the generator\'s levels %s are the sweep\'s, picked by the generator and again here from the table' % g.LEVELS_DB)
    check(all(abs(x * 2 - round(x * 2)) < 1e-9 for x in g.LEVELS_DB) and g.LEVELS_DB[4] == g.LEVELS_DB[3] - 3.0,
          'the levels are on a 0.5 dB grid and the fifth is 3 dB below the fourth')
    meas = {r['snr_db']: r['pooled']['yield'] for r in sw['levels_check']['all_seeds']}
    want = dict(zip(g.LEVELS_DB[:4], (0.95, 0.80, 0.65, 0.50)))
    check(all(abs(meas[s] - t) < 0.06 for s, t in want.items()),
          'the yield measured at the four levels is about 95, 80, 65 and 50 %% (within 6 points: 0.5 dB rounding of a steep curve): %s' % {s: round(meas[s], 3) for s in want})
    check('package' in sw['decoder_id'] and sw['decoder_id']['package'] and sw['receiver']['fir_taps'] == 255
          and '20 percentage points' in sw['caveat'], 'the sweep records the decoder (%s), the demodulator parameters and the receiver caveat' % sw['decoder_id'])
    check_file_rows(sw)
    check(meas[g.LEVELS_DB[4]] < 0.25, 'and %.3f at the fifth, 3 dB below the 50 %% point' % meas[g.LEVELS_DB[4]])
    names = [n for n, _ in g.set_files()]
    check(len(set(g.LEVELS_DB)) == 5, 'the five levels are distinct')
    check(names == ['acl_dm_hop20_snr16p5', 'acl_dm_hop20_snr15p0', 'acl_dm_hop20_snr14p0', 'acl_dm_hop20_snr13p5',
                    'acl_dm_hop20_snr10p5', 'acl_dh5_hop20_clean_snr20'], '--set acl is the five level files and the control: %s' % names)
    kinds = [s['kind'] for _, s in g.set_files()]
    check(kinds == ['dm'] * 5 + ['dh5'] and g.set_files()[5][1]['snr_db'] == 20.0, 'the control is DH5 at 20 dB')


def check_file_rows(sw):
    """The stored rows of the files as written are what the reference receiver gives on those files now."""
    rows = {r['snr_db']: r for r in sw['levels_check']['file_seed']}
    for snr in (g.LEVELS_DB[0], g.LEVELS_DB[3]):
        iq, side = g.synthesise_acl('dm', snr)
        got = g.rx_yield(iq, side, _lib)
        del iq
        count = {t: [0, 0, 0] for t in ('DM1', 'DM3', 'DM5')}
        for e, r in zip(side['bursts'], got):
            if r is not None:
                count[e['ptype']][0] += bool(r[0])
                count[e['ptype']][1] += bool(r[1])
                count[e['ptype']][2] += bool(r[2])
        stored = {t: [v['header_ok'], v['crc_ok'], v['payload_exact']] for t, v in rows[snr]['per_type'].items()}
        check(count == stored, 'the stored file rows at %.1f dB (header, CRC, exact per type) are what the receiver gives on the file now: %s'
              % (snr, {t: v[2] for t, v in count.items()}) + ('' if count == stored else ' vs stored %s' % stored))


# --- the command line and the files ---------------------------------------------------------------------

def check_files():
    print('\nThe files')
    with tempfile.TemporaryDirectory(prefix='test_acl_') as tmp:
        g.main(['acl_t_snr12p0', '--snr', '12', '--bursts', '9', '--out', tmp])
        side = json.load(open(os.path.join(tmp, 'synth_acl_t_snr12p0.json')))
        size = os.path.getsize(os.path.join(tmp, 'synth_acl_t_snr12p0.cf32'))
        check(size == 8 * side['n_samples'], 'the file is 8 * n_samples bytes: %d = 8 * %d' % (size, side['n_samples']))
        raw = np.fromfile(os.path.join(tmp, 'synth_acl_t_snr12p0.cf32'), dtype='<c8')
        iq, again = g.synthesise_acl('dm', 12.0, bursts=9, name='acl_t_snr12p0')
        check(np.array_equal(raw, iq) and json.loads(json.dumps(again)) == side, 'the same arguments give the same bytes and sidecar')
        iq2, _ = g.synthesise_acl('dm', 12.0, bursts=9, seed=8102)
        check(not np.array_equal(iq2, iq), 'another seed is another file')
        for bad, what in ((['--set', 'acl', '--snr', '9'], 'a stray option with --set'), (['--set', 'nope'], 'an unknown set'),
                          ([], 'no name'), (['x', '--bursts', '10', '--out', tmp], '10 bursts of three types')):
            try:
                g.main(bad)
                check(False, 'refuses %s' % what)
            except SystemExit:
                check(True, 'refuses %s' % what)
        check(side['air_bits_omitted'] is False and 'exception' in side['air_bits_note'] and 'air_bits' in side['air_bits_note'],
              'the sidecar says air_bits are present, per burst, and how they are packed')


def check_keys(side, label):
    need = ['generator', 'generator_commit', 'lap', 'uap', 'sample_rate', 'center_mhz', 'modulation', 'slot_samples',
            'start_sample_meaning', 'clk_convention', 'snr_bw_hz', 'noise_1mhz', 'bursts', 'symbol_phases', 'per_burst_keys',
            'levels_db', 'n_samples', 'sweep', 'sweep_note', 'type_counts', 'equal_counts_statement', 'air_bits_omitted',
            'air_bits_note', 'seed', 'afh_map', 'afh_instant', 'timing_frac', 'symbol_phase', 'lmp_pdus', 'paired_set']
    miss = [k for k in need if k not in side]
    check(not miss, '%s: the sidecar has every key (%s)' % (label, miss))
    per = ['ptype', 'lap', 'uap', 'channel', 'start_sample', 'timing_frac', 'clk', 'snr_db', 'llid', 'payload_length', 'payload_hex',
           'lmp_opcode', 'lmp_name', 'lmp_tid', 'lt_addr', 'flow', 'arqn', 'seqn', 'air_bits', 'air_bits_length',
           'body_crc_bits_hex', 'body_crc_bits_length', 'channel_mhz', 'symbol_phase']
    miss = [k for k in per if k not in side['per_burst_keys']]
    check(not miss, '%s: per_burst_keys has every per-burst truth key (%s)' % (label, miss))
    check(side['timing_frac'] is None and side['symbol_phase'] is None and side['symbol_phases'] == [0, 20]
          and side['snr_bw_hz'] == 1e6 and side['noise_1mhz'] == NOISE and side['sample_rate'] == FS and side['center_mhz'] == CENTER,
          '%s: top-level timing_frac and symbol_phase are null, the floor and the rate are the repository\'s' % label)
    check(side['lap'] == LAP and side['uap'] == UAP and side['nap'] == 0x1234, '%s: master LAP 0x112233, UAP 0x55, NAP 0x1234' % label)
    check(side['sweep'] is None or (side['sweep']['table'] and 'ANOTHER RECEIVER' in side['sweep']['caveat']),
          '%s: the sweep is in the sidecar with its caveat' % label)


# --- mutants ------------------------------------------------------------------------------------------------

def mutant_module(old, new):
    """The generator with one text replaced once, as a module of its own."""
    assert SRC.count(old) == 1, 'mutation target not unique: %r (%d)' % (old, SRC.count(old))
    spec = importlib.util.spec_from_file_location('bt_synth_acl_mut', g.__file__)
    mod = importlib.util.module_from_spec(spec)
    exec(compile(SRC.replace(old, new), g.__file__, 'exec'), mod.__dict__)
    return mod


def run_mutant_checks(iq, side):
    """What a reader would check on one small file: the keys, the plan and the packets, the length, and the
    first bursts' samples."""
    check_keys(side, 'mutant')
    check_sidecar_truth(side, 'mutant')
    check_plan_truth(side, 'mutant', 'dm', len(side['bursts']))
    check_lmp_plan(side, 'mutant') if len(side['bursts']) >= 60 else None
    check(len(iq) == side['n_samples'], 'mutant: n_samples is the length of the array')
    check_table_51(side, 'mutant')
    check_samples(iq, side, 'mutant', blind=False, bits_exact=side['snr_db'] > 25, limit=4, tol_db=0.3)


def check_mutants():
    print('\nMutants: one mistake each, and what catches it')
    orig_crc, orig_amp, orig_wh, orig_enc = br.crc16, g.amplitude_for, br.whitening, bt_lmp.encode

    orig_rp = bt_lmp.random_pdu

    def short_lead(rng, name, clk=0):
        pdu, p, tid = orig_rp(rng, name, clk)
        if name == 'LMP_set_AFH':
            p = dict(p, afh_instant=((clk >> 1) + 2) & 0x7FFFFFE)
            pdu = orig_enc(name, tid, **p)
        return pdu, p, tid

    def swap_version(name, tid=0, **p):
        if name in ('LMP_version_req', 'LMP_version_res'):
            p = dict(p, company_id=p['subversion'], subversion=p['company_id'])
        return orig_enc(name, tid, **p)

    def swap_refusal(name, tid=0, **p):
        if name == 'LMP_not_accepted':
            p = dict(p, opcode=p['error_code'], error_code=p['opcode'])
        return orig_enc(name, tid, **p)
    cases = [
        ('wrong UAP in the CRC', lambda: mock.patch.object(br, 'crc16', lambda bits, uap: orig_crc(bits, uap ^ 1)), None),
        ('FEC applied to the header', lambda: mock.patch.object(br, 'fec13', br.fec23), None),
        ('a wrong LMP opcode number for one PDU', lambda: mock.patch.dict(bt_lmp.PDUS['LMP_version_res'], opcode=41), None),
        ('the level 3 dB off', lambda: mock.patch.object(g, 'amplitude_for', lambda s: orig_amp(s + 3.0)), None),
        ('air_bits taken before whitening',
         None, ("air_bits=bits_hex(p.bits),",
                "air_bits=bits_hex(br.Packet(lap, uap, clk, ptype, b['body'], lt_addr=LT_ADDR, flow=1, arqn=1, seqn=k & 1, "
                "llid=b['llid'], payload_flow=1, whiten=False).bits),")),
        ('unequal type counts', None, ("types = [t for t in DM_TYPES for _ in range(n)]",
                                        "types = [t for t in DM_TYPES for _ in range(n + (t == 'DM5'))]")),
        ('n_samples off by one', None, ("'n_samples': int(total),", "'n_samples': int(total) + 1,")),
        ('the sidecar\'s integer LMP parameters off by their low bit', None,
         ("key: (v.hex() if isinstance(v, bytes) else v) for key, v in b['lmp']['params'].items()}",
          "key: (v.hex() if isinstance(v, bytes) else v ^ 1) for key, v in b['lmp']['params'].items()}")),
        ('Company_Identifier and Subversion swapped in the encoded version PDUs',
         lambda: mock.patch.object(bt_lmp, 'encode', swap_version), None),
        ('Opcode and Error_Code in the wrong positions of LMP_not_accepted',
         lambda: mock.patch.object(bt_lmp, 'encode', swap_refusal), None),
        ('an LMP_set_AFH instant only 2 slots ahead', lambda: mock.patch.object(bt_lmp, 'random_pdu', short_lead), None),
        ('a set padding bit in air_bits', None, ("s += '0' * (-len(s) % 8)", "s += '1' * (-len(s) % 8)")),
        ('an LMP PDU in a DM3', None,
         ("        else:\n            lmp_name = None\n",
          "        elif ptype == 'DM3':\n            lmp_name = LMP_NAMES[k % len(LMP_NAMES)]\n        else:\n            lmp_name = None\n")),
        ('the clock one slot off in the whitening only', lambda: mock.patch.object(br, 'whitening', lambda clk, n: orig_wh(clk + 2, n)), None),
    ]
    for name, patch, src in cases:
        # a mutant of the LMP PDUs needs every PDU in the file, so it is a full-size one
        full = any(w in name for w in ('LMP', 'opcode', 'Company', 'DM3', 'AFH'))
        with quiet() as failed:
            try:
                if src is not None:
                    mod = mutant_module(*src)
                    gen = mod.synthesise_acl
                    iq, side = gen('dm', 30.0, bursts=204 if full else 9, block_samples=BLOCK)
                else:
                    # the opcode mutant needs every PDU in the file, so it is a full-size one
                    with patch():
                        iq, side = g.synthesise_acl('dm', 30.0, bursts=204 if full else 9,
                                                 block_samples=BLOCK)
                run_mutant_checks(iq, side)
            except Exception as e:                      # a crash is a catch too, said so
                failed.append('raised %s: %s' % (type(e).__name__, str(e)[:60]))
        short = sorted({w.split(':')[0][:40] + ':' + w.split(':')[-1][:50] for w in failed})
        check(len(failed) > 0, 'mutant "%s" is caught by %d checks, e.g. %s' % (name, len(failed), '; '.join(short[:2])))
    # the noise of the wrong seed in two distinct levels: the noise assertion of the pairing itself must fail,
    # and the same pair unmutated is the positive control
    mod = mutant_module("iq[lo:hi] += noise_block(seed, index, hi - lo, fs)", "iq[lo:hi] += noise_block(seed + 1, index, hi - lo, fs)")
    with quiet() as failed:
        check_pairing(mod.synthesise_acl('dm', 30.0, bursts=9, block_samples=BLOCK),
                      mod.synthesise_acl('dm', 20.0, bursts=9, block_samples=BLOCK), 'mutant')
    check(any('(a - noise) / A_a' in w for w in failed),
          'mutant "the noise of seed + 1" in two distinct levels fails the noise/proportionality assertion of the pairing')
    with quiet() as failed:
        check_pairing(g.synthesise_acl('dm', 30.0, bursts=9, block_samples=BLOCK),
                      g.synthesise_acl('dm', 20.0, bursts=9, block_samples=BLOCK), 'control')
    check(not failed, 'the same pair unmutated passes every pairing assertion (positive control)')
    # a different seed for one level file: the pairing must fail
    with quiet() as failed:
        a = g.synthesise_acl('dm', 30.0, bursts=9, block_samples=BLOCK)
        b = g.synthesise_acl('dm', 14.0, bursts=9, seed=8102, block_samples=BLOCK)
        check_pairing(a, b, 'mutant')
    check(len(failed) > 0, 'mutant "a different seed for one level file" is caught by %d checks, e.g. %s'
          % (len(failed), '; '.join(sorted({w.split(':')[0][:40] for w in failed})[:2])))
    # a changed level between the paired files
    with quiet() as failed:
        a = g.synthesise_acl('dm', 30.0, bursts=9, block_samples=BLOCK)
        with mock.patch.object(g, 'amplitude_for', lambda s: orig_amp(s + 3.0)):
            b = g.synthesise_acl('dm', 14.0, bursts=9, block_samples=BLOCK)
        check_pairing(a, b, 'mutant')
    check(len(failed) > 0, 'mutant "one level file 3 dB off its claim" is caught by the pairing: %d checks' % len(failed))
    # a plan with one LMP PDU never sent
    mod = mutant_module("cycle += [LMP_NAMES[i] for i in order_rng.permutation(len(LMP_NAMES))]",
                        "cycle += [LMP_NAMES[i] for i in order_rng.permutation(len(LMP_NAMES) - 1)]")
    with quiet() as failed:
        iq_, side_ = mod.synthesise_acl('dm', 30.0, block_samples=BLOCK)
        check_lmp_plan(side_, 'mutant')
        del iq_
    check(len(failed) > 0, 'mutant "one LMP PDU never sent" is caught by the full-size plan check: %d' % len(failed))


# --- main -----------------------------------------------------------------------------------------------------

def main():
    print('The full-size plan (204 bursts), sidecar only\n')
    full_iq, full = g.synthesise_acl('dm', g.LEVELS_DB[0], name='full')
    del full_iq
    check_keys(full, 'full')
    check_plan_truth(full, 'full', 'dm', 204)
    check_lmp_plan(full, 'full')
    check_sidecar_truth(full, 'full')
    ctl_iq, ctl_full = g.synthesise_acl('dh5', g.CONTROL_SNR_DB, name='control')
    del ctl_iq
    check_plan_truth(ctl_full, 'control', 'dh5', 204)
    check_sidecar_truth(ctl_full, 'control')
    ok = all(e['llid'] == 2 and 20 <= e['payload_length'] <= 339 for e in ctl_full['bursts'])
    check(ok, 'control: every DH5 is LLID 2 with a body of 20 to 339 bytes')
    check_sweep()
    check_files()

    print('\nSmall files: 24 bursts, 8 of each type, noise in blocks of %d' % BLOCK)
    a = g.synthesise_acl('dm', 30.0, bursts=24, block_samples=BLOCK)
    b = g.synthesise_acl('dm', 20.0, bursts=24, block_samples=BLOCK)
    d = g.synthesise_acl('dm', 14.0, bursts=24, block_samples=BLOCK)
    c = g.synthesise_acl('dh5', 20.0, bursts=24, block_samples=BLOCK)
    for side, label in ((a[1], '30 dB'), (b[1], '20 dB'), (d[1], '14 dB'), (c[1], 'control')):
        check_keys(side, label)
    check_plan_truth(a[1], '30 dB', 'dm', 24)
    check_sidecar_truth(a[1], '30 dB')
    check_sidecar_truth(b[1], '20 dB')
    check_sidecar_truth(d[1], '14 dB')
    check_samples(a[0], a[1], '30 dB file', blind=True, bits_exact=True)
    check_samples(b[0], b[1], '20 dB file', blind=False, bits_exact=False)
    # at 14 dB the fit's own noise (the Cramer-Rao bound of a 186-bit burst is a quarter of a sample) is as big as
    # the half sample asked of the clean files: the limit is a sample, and the mean must still be zero
    check_samples(d[0], d[1], '14 dB file', blind=False, bits_exact=False, tol_start=1.0)
    print('\nPairing')
    check_pairing(a, b, '30 dB and 20 dB')
    check_pairing(b, d, '20 dB and 14 dB')
    check_control(a, c, 'control')
    check_shipped_block()
    check_mutants()

    failed = [w for ok, w in RESULTS if not ok]
    print('\nRESULT: %s' % ('FAIL (%d)' % len(failed) if failed else 'PASS'))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
