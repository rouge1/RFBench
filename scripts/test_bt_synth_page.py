#!/usr/bin/env python3
"""Hold the page and inquiry exchange writer to the Core specification and to its own samples.

    python scripts/test_bt_synth_page.py                      # small runs and the full plan, about a minute
    python scripts/test_bt_synth_page.py --file DIR/synth_page_exch_hop20.cf32 --exchanges 0,1
                                                              # the sample-side checks on a real file's exchanges
    python scripts/test_bt_synth_page.py --file DIR/synth_page_exch_hop20.json --plan
                                                              # the plan checks and the validator on a real sidecar

``scripts/bt_synth_page.py`` writes 40 MS/s files of page exchanges (two IDs a
TX slot, the paged device's response, the FHS, the acknowledgement, a POLL and
110 master packets with the slave's answers) and of inquiry exchanges (GIAC
IDs and an FHS response). bluey-ox-walker validates an FHS by brute-forcing
CLK[27:1] from the master's follow-up, so a wrong truth here is a wrong test
there. Nothing below trusts the generator's bookkeeping.

* **Timing relations**, written here from the Core v6.0 text in microseconds
  and turned to samples by the sample rate: the second ID 312.5 us after the
  first (Vol 2 Part B s2.4.3), a response 625 us after the start of the ID it
  answers (s8.3.3.1, s2.4.4), the FHS 1250 us after the first ID of the slot
  the response was heard in (s2.4.4, Figures 2.8, 2.9), the acknowledgement 625
  us after the FHS (s2.4.4), the POLL 1250 us after the FHS (the text is silent;
  see the generator), a slave answer 625 us after its master's packet
  (s2.2.5.1), an inquiry response 625 us after the ID (s2.5.4). Checked on the
  sidecar's start samples exactly, and on the samples (the correlation peak of
  the access code, a fraction of a sample from the true start).
* **The samples**: each burst's start, carrier, level, length and bits are
  recovered from the samples alone and compared with the sidecar. The access
  code's LAP is found by correlating with each LAP a receiver listening for
  this file would try; IDs are 68 bits long; an FHS decodes through
  ``bt_fhs.parse_fhs_air_bits`` and through libbtbb to the fields the sidecar
  states; POLL, NULL and DH1 headers through libbtbb at the master's UAP and
  the burst's ``clk``.
* **The validator**: bluey's own check rewritten here with no help from the
  generator - from the (slot, channel) pairs of the master packets after an FHS,
  brute-force CLK[27:1] over a window and require the one solution to be the
  FHS's CLK27-2 advanced by the slots since, through ``apps/bt_hop.py`` and the
  map 31-50. It must succeed for every exchange, and fail for the wrong master
  LAP, for a shifted FHS clock, for the wrong map, and for a master LAP whose
  follow-up traffic is removed.
* **Mutants**: ten single mistakes, each made in a copy of the generator, and
  the checks that catch each, reported.
"""
import argparse
import contextlib
import ctypes
import importlib.util
import json
import os
import sys

import numpy as np
from scipy.signal import fftconvolve, firwin, lfilter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps import bt_br_frame as br  # noqa: E402
from apps import bt_fhs  # noqa: E402
from apps import bt_hop  # noqa: E402
from scripts import bt_ota_check, bt_synth, bt_synth_page as gen  # noqa: E402

FS = 40e6
SPS = int(FS / br.SYMBOL_RATE)
CENTER = 2441.0
CENTRE_CHANNEL = 39
MAP = list(range(31, 51))
MASK = sum(1 << c for c in MAP)
PAGED_LAP, PAGED_UAP = 0x6B1D3C, 0x47
M_LAP, M_UAP, M_NAP, M_COD = 0x112233, 0x55, 0x1234, 0x5A020C
GIAC = 0x9E8B33
M_ADDRESS = M_UAP << 24 | M_LAP
NOISE_POWER = bt_synth.AMPLITUDE ** 2 / 100 * FS / 1e6

# the Core v6.0 intervals, in microseconds, and in samples
US = FS * 1e-6
HALF_SLOT = int(round(312.5 * US))
SLOT = int(round(625.0 * US))
TWO_SLOTS = int(round(1250.0 * US))

TAPS = firwin(401, 0.7e6, fs=FS)
GD = len(TAPS) // 2

RESULTS = []
QUIET = [False]


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


# --- what a burst's length is, from the packet type and not from the generator --

def nbits(b):
    """Bits on air: an ID is 68, an FHS 366, POLL and NULL 126, a DH1 its 126
    plus the payload (header, body, CRC) at 8 bits a byte, no FEC; a DM1 the
    same bits with FEC 2/3 (15 bits for every 10, the last block padded)."""
    if b['ptype'] == 'ID':
        return 68
    if b['ptype'] == 'FHS':
        return 366
    if b['ptype'] in ('POLL', 'NULL'):
        return 126
    n = 8 * len(bytes.fromhex(b['payload_full_hex']))
    if b['ptype'] == 'DM1':
        return 126 + -(-n // 10) * 15
    return 126 + n


# --- the validator, bluey's check, written here ------------------------------

def validate_clock(obs, expected, address, mask, span=300):
    """Every CLK[27:1] ``c`` for the first observation, within ``span`` of
    ``expected``, for which every ``(slot offset, channel)`` of ``obs`` is the
    hop of the clock ``c`` advanced by the offset (one CLK[27:1] a slot).

    ``obs`` is every packet on the master's access code, the slave's answers
    included. On an adapted sequence the kernel takes CLK1 as 0 (``bt_hop``
    note 4): master packets alone cannot tell ``c`` from ``c + 1``, and it is
    the slave packets, in the odd slots, that do: at ``c + 1`` each lands on
    the next master slot's hop."""
    sols = []
    for c in range(expected - span, expected + span + 1):
        if all(bt_hop.hop_channel(((c + off) << 1) & 0x0FFFFFFF, address, mask) == ch for off, ch in obs):
            sols.append(c)
    return sols


def validates(obs, fhs_c27_2, slots_since_fhs, address=M_ADDRESS, mask=MASK, min_obs=16):
    """bluey's rule: enough packets on the master's LAP after the FHS, and the
    one CLK[27:1] they give is the FHS's CLK27-2 (as CLK27-1) advanced by the
    slots between the FHS and the first of them."""
    if len(obs) < min_obs:
        return False
    expected = (fhs_c27_2 << 1) + slots_since_fhs
    return validate_clock(obs, expected, address, mask) == [expected]


def observations(starts, channels):
    """(slot offset from the first, channel) from start samples, rounded to a slot."""
    s0 = starts[0]
    return [(int(round((s - s0) / SLOT)), c) for s, c in zip(starts, channels)]


# --- the plan checks, on a sidecar ------------------------------------------------

def by_exchange(side):
    out = {}
    for i, b in enumerate(side['bursts']):
        out.setdefault(b['exchange'], []).append((i, b))
    return out


def check_plan(side, kind, exchanges, followup, label, controls=True):
    print('\nThe plan of %s: %d %s exchanges' % (label, exchanges, kind))
    bursts = side['bursts']
    ex = by_exchange(side)
    check(side['file_kind'] == kind and side['n_exchanges'] == exchanges == len(side['exchanges']) == len(ex),
          '%d exchanges, kind %s' % (exchanges, kind))
    check(side['sample_rate'] == FS and side['center_mhz'] == CENTER and side['snr_bw_hz'] == 1e6
          and side['noise_1mhz'] == bt_synth.AMPLITUDE ** 2 / 100, '40 MS/s, 2441.0 MHz, the one noise floor')
    # no overlap anywhere, and every start on the grid the exchange sits on
    ends_ok = all(b2['start_sample'] >= b1['start_sample'] + nbits(b1) * SPS
                  for b1, b2 in zip(bursts, bursts[1:]))
    check(ends_ok, 'no burst overlaps the next in time (every start is at or after the previous burst\'s end)')
    check(all(b['symbol_phase'] == b['start_sample'] % SPS for b in bursts), 'symbol_phase is start_sample % 40')
    phases = [side['exchanges'][e]['symbol_phase'] for e in range(exchanges)]
    check(phases == [0 if e % 2 == 0 else 20 for e in range(exchanges)] and
          all(bursts[side['exchanges'][e]['first_burst']]['start_sample'] % SPS == phases[e]
              for e in range(exchanges)),
          'symbol phase alternates 0, 20 per exchange, the first burst of each at its exchange\'s phase')
    check(all(0 <= b['timing_frac'] < 1 for b in bursts) and len({round(b['timing_frac'], 6) for b in bursts})
          > 0.9 * len(bursts), 'timing_frac is in [0, 1) and differs per burst')
    check(all(b['channel'] in MAP and b['channel_mhz'] == 2402 + b['channel'] for b in bursts),
          'every channel of every burst is in 31-50, channel_mhz is 2402 + channel')
    check(all('air_bits' not in b for b in bursts) and side['air_bits_omitted'] is True, 'no air_bits, air_bits_omitted true')
    per = side['per_burst_keys']
    check(all(side[k] is None for k in per if k in side) and all(k in b for b in bursts for k in per)
          and all(b['snr_db'] == side['master_snr_db'] for b in bursts),
          'per_burst_keys (%d) are null at the top and present on every burst' % len(per))
    need = ['generator', 'generator_commit', 'sample_rate', 'center_mhz', 'modulation', 'slot_samples',
            'start_sample_meaning', 'clk_convention', 'snr_bw_hz', 'noise_1mhz', 'bursts', 'exchanges',
            'identities', 'spec_notes', 'page_channels_note', 'followup_note', 'n_bursts_on_centre_channel',
            'kind_counts', 'symbol_phases', 'per_burst_keys', 'air_bits_omitted']
    check(all(k in side for k in need) and side['slot_samples'] == SLOT, 'the sidecar has its keys: %s' % ', '.join(
        k for k in need if k not in side) or 'all of them')
    check(isinstance(side['spec_notes'], list) and len(side['spec_notes']) >= 8
          and any('SILENT' in n for n in side['spec_notes']) and 'NOT a specification page train' in side['page_channels_note'],
          'spec_notes (%d) cite the text and say where it is silent; page_channels_note says it is not a page train'
          % len(side['spec_notes']))
    cnt = {}
    for b in bursts:
        cnt[b['kind']] = cnt.get(b['kind'], 0) + 1
    check(cnt == side['kind_counts'] and sum(cnt.values()) == side['n_bursts'] == len(bursts),
          'kind_counts are the bursts\' own kinds: %s' % cnt)
    centre = [b for b in bursts if b['channel'] == CENTRE_CHANNEL]
    check(len(centre) == side['n_bursts_on_centre_channel'], 'n_bursts_on_centre_channel %d' % len(centre))
    ids = [b for b in bursts if b['kind'] in ('id_page', 'id_inquiry')]
    check(sum(b['channel'] == CENTRE_CHANNEL for b in ids) >= 0.08 * len(ids),
          '%d of %d IDs (%.1f %%) are on the centre channel 39 (at least 8 %%)'
          % (sum(b['channel'] == CENTRE_CHANNEL for b in ids), len(ids),
             100.0 * sum(b['channel'] == CENTRE_CHANNEL for b in ids) / len(ids)))
    fhss = [b for b in bursts if b['ptype'] == 'FHS']
    check(any(b['channel'] == CENTRE_CHANNEL for b in fhss), '%d of %d FHS are on channel 39'
          % (sum(b['channel'] == CENTRE_CHANNEL for b in fhss), len(fhss)))
    c0s = [e['c0'] for e in side['exchanges']]
    check(len(set(c0s)) == exchanges and all(c % 4 == 0 and c < (1 << 28) - 4000 for c in c0s),
          'the exchanges\' starting clocks are distinct multiples of 4, far from CLK27 wrapping')
    check(max(b['time_clk'] for b in bursts) < (1 << 28) and
          all(b['time_clk'] == side['exchanges'][b['exchange']]['c0'] + b['tick'] for b in bursts),
          'time_clk is c0 + tick, below 2**28: no clock wraps')
    check(sorted(side['hop_channels']) == sorted({b['channel'] for b in bursts}), 'hop_channels lists the channels used')

    if kind == 'page':
        check_conformance(side, 'page', exchanges)
        check_plan_page(side, ex, exchanges, followup, controls)
        check_whitening(side, 'page', exchanges)
    else:
        check_conformance(side, 'inquiry', exchanges)
        check_plan_inquiry(side, ex, exchanges)
        check_whitening(side, 'inquiry', exchanges)


def rebuild_fhs_ok(b, header_uap, access_lap, lt_header=0):
    """The FHS's packet rebuilt from the sidecar's fields by the encoder: the
    header18 and the payload bytes it records."""
    f = b['fhs']
    p = bt_fhs.FHS(access_lap, f['lap'], f['uap'], f['nap'], f['class_of_device'], f['am_addr'], f['clk27_2'] << 2,
                   whitening_x=f['whitening_x'], lt_addr=lt_header, header_uap=header_uap, sr=f['sr'], sp=f['sp'], eir=f['eir'])
    return p.header18 == b['header18'] and p.payload_full.hex() == b['payload_full_hex']


def xprc_independent(clke, koff, nudge, n):
    """EQ 6 (s2.6.4.4) from bit strings; CLKE4-2,0 is bits 4, 3, 2, 0."""
    b = format(clke, '028b')[::-1]
    c16_12 = int(b[16] + b[15] + b[14] + b[13] + b[12], 2)
    c4_2_0 = int(b[4] + b[3] + b[2] + b[0], 2)
    return (c16_12 + koff + nudge + ((c4_2_0 - c16_12) % 16) + n) % 32


def check_whitening(side, kind, exchanges):
    """The X-input rule of s7.2 against the recorded inputs, with the equations written here."""
    bursts = side['bursts']
    fh = [b for b in bursts if b['ptype'] == 'FHS']
    bad = []
    for e, b in enumerate(fh):
        f, E = b['fhs'], side['exchanges'][b['exchange']]['fhs_whitening']
        x = f['whitening_x']
        if not isinstance(x, int):                    # not whitened from an X-input at all
            bad.append(b['exchange'])
            continue
        reg = ''.join(str((x >> i) & 1) for i in range(5)) + '11'
        if kind == 'page':
            ok = (E['xprc'] == x == xprc_independent(E['clke_frozen'], E['koffset'], E['knudge'], E['N'])
                  and E['N'] == 1 and E['train'] in 'AB' and E['koffset'] == (24 if E['train'] == 'A' else 8)
                  and E['knudge'] == 0 and 0 <= E['clke_frozen'] < 1 << 28)
        else:
            ok = E['xir'] == x == ((b['clk'] >> 12) & 0x1F) + E['N'] & 0x1F and E['clkn16_12'] == (b['clk'] >> 12) & 0x1F
        ok = ok and 0 <= x < 32 and f['whitening_register'] == reg and f['implied_clk6_1'] == 32 + x \
            and f['tx_clk'] == (32 + x) << 1 and bool(f['whitening_rule'])
        if not ok:
            bad.append(b['exchange'])
    check(not bad, 'every FHS\'s X-input is %s computed here from the recorded inputs, its register is [X0..X4 1 1] and '
          'implied_clk6_1 is 32 + X (%d wrong)' % ('Xprc, EQ 6, with N 1 and k_offset 24 (A) or 8 (B)' if kind == 'page'
                                                  else 'Xir, EQ 8,', len(bad)))
    if kind == 'page':
        check(len({side['exchanges'][e]['fhs_whitening']['train'] for e in range(exchanges)}) == min(2, exchanges)
              or exchanges < 6, 'both trains occur')
        check(any(f['fhs']['implied_clk6_1'] != (b['clk'] >> 1) & 0x3F for f, b in ((x, x) for x in fh)),
              'the X-input differs from the plain CLK6-1 of the FHS clock in some exchanges')
    else:
        # N of one scanning device runs +1 each time it answers
        last = {}
        ok = True
        for E in side['exchanges']:
            w = E['fhs_whitening']
            if w['scanner_index'] in last:
                ok = ok and w['N'] == last[w['scanner_index']] + 1
            last[w['scanner_index']] = w['N']
        check(ok, 'a scanning device that answers again has its counter N one higher (%d devices)' % len(last))


def check_conformance(side, kind, exchanges):
    c = side['conformance']
    txt = ' '.join(c['not_followed'])
    check(set(c) >= {'followed', 'not_followed', 'silent_in_text'} and c['followed'] and c['silent_in_text']
          and all(w in txt for w in ('page response', 'inquiry response', 'basic channel hopping sequence', 's8.3.3.2'))
          and all('SILENT / ambiguous' not in n for n in side['spec_notes'])
          and any('NOT FOLLOWED' in n and 'basic' in n.lower() for n in side['spec_notes']),
          'conformance lists what is followed, NOT followed (page, page response, inquiry, inquiry response, basic '
          'sequence, with sections) and silent; spec_notes no longer calls the sequences silent')
    E = side['exchanges']
    check(all('31-50' in e['spec_channels_note'] and 'conformance' in e['spec_channels_note'] for e in E),
          'every exchange has a spec_channels_note')
    if kind == 'page':
        # what the BASIC kernel (79 channels) gives for the first POLL's clock: recorded, not used
        ok = all(e['spec_basic_channel_first_poll'] == bt_hop.hop_channel(e['master_clk_at_connection'], M_ADDRESS, None)
                 for e in E)
        poll_ch = [side['bursts'][e['fhs_burst'] + 2]['channel'] for e in E]
        check(ok and all(0 <= e['spec_basic_channel_first_poll'] < 79 for e in E),
              'spec_basic_channel_first_poll is bt_hop\'s BASIC kernel (79 channels) for the first POLL\'s clock '
              '(%d of %d exchanges: it differs from the channel actually used)'
              % (sum(1 for e, c in zip(E, poll_ch) if e['spec_basic_channel_first_poll'] != c), exchanges))


def check_plan_page(side, ex, exchanges, followup, controls):
    bursts = side['bursts']
    ids = [b for b in bursts if b['kind'] == 'id_page']
    check(all(b['lap'] == PAGED_LAP and b['uap_for_hec'] is None and b['ptype'] == 'ID' for b in ids)
          and not 0x9E8B00 <= PAGED_LAP <= 0x9E8B3F and PAGED_LAP not in (0x654903, 0xC52DA1),
          'the page IDs carry the paged LAP 0x6B1D3C (outside 0x9E8B00-0x9E8B3F, not 0x654903 or 0xC52DA1), no header')
    check(all(b['lap'] == PAGED_LAP for b in bursts if b['kind'] in ('id_response', 'id_ack', 'fhs')),
          'the response, the FHS and the acknowledgement carry the paged LAP too')
    fh = [b for b in bursts if b['kind'] == 'fhs']
    check(len(fh) == exchanges and all(
        b['fhs']['lap'] == M_LAP and b['fhs']['uap'] == M_UAP and b['fhs']['nap'] == M_NAP
        and b['fhs']['class_of_device'] == M_COD and b['fhs']['am_addr'] == 1 and b['fhs']['lt_addr_header'] == 0
        and b['fhs']['sp'] == 2 and b['fhs']['eir'] == 0 and b['fhs']['reserved'] == 0 and b['fhs']['previously_used'] == 0 and b['fhs']['header_uap'] == PAGED_UAP
        and b['uap_for_hec'] == PAGED_UAP for b in fh),
          'each FHS: master 0x112233 / 0x55 / 0x1234, CoD 0x5A020C, am_addr 1, header LT_ADDR 0, SP 2, EIR 0, '
          'HEC and CRC from the paged UAP 0x47, whitened from the X-input (checked separately)')
    check(all(rebuild_fhs_ok(b, PAGED_UAP, PAGED_LAP) for b in fh),
          'the FHS\'s header and payload bytes are what the encoder gives for the sidecar\'s own fields')
    check(all(b['clk'] % 4 == 0 and b['fhs']['clk27_2'] == b['clk'] >> 2 for b in fh),
          'FHS clk27_2 is the master clock at the start of the FHS >> 2, which is a master slot (CLK1-0 = 0)')
    clks = [b['fhs']['clk27_2'] for b in fh]
    check(len(set(clks)) == exchanges, 'the FHS clock differs in every exchange (%d distinct of %d)' % (len(set(clks)), exchanges))
    check(side['identities']['paged']['lap'] == PAGED_LAP and side['identities']['master']['lap'] == M_LAP
          and side['identities']['master']['uap'] == M_UAP and side['identities']['master']['nap'] == M_NAP,
          'identities in the sidecar')
    failed = 0
    for e in range(exchanges):
        E = side['exchanges'][e]
        items = ex[e]
        by = {}
        for i, b in items:
            by.setdefault(b['kind'], []).append((i, b))
        kinds = [b['kind'] for _, b in items]
        order_ok = kinds[:len(by['id_page'])] == ['id_page'] * len(by['id_page']) and \
            kinds[len(by['id_page']):len(by['id_page']) + 3] == ['id_response', 'fhs', 'id_ack'] and \
            set(kinds[len(by['id_page']) + 3:]) == {'followup_master', 'followup_slave'}
        idp = [b for _, b in by['id_page']]
        s_id = [b['start_sample'] for b in idp]
        n = len(idp)
        # two IDs a slot at the half slot, a slot every 1250 us, distinct channels within the slot
        slots_ok = n % 2 == 0 and 3 <= n // 2 <= 8 and all(
            s_id[2 * k + 1] - s_id[2 * k] == HALF_SLOT and idp[2 * k]['channel'] != idp[2 * k + 1]['channel']
            for k in range(n // 2)) and all(s_id[2 * k + 2] - s_id[2 * k] == TWO_SLOTS for k in range(n // 2 - 1))
        # the heard ID: the one 625 us before the response
        resp = by['id_response'][0][1]
        heard = [b for b in idp if resp['start_sample'] - b['start_sample'] == SLOT]
        last_first = s_id[n - 2]
        fhs = by['fhs'][0][1]
        ack = by['id_ack'][0][1]
        fm = [b for _, b in by['followup_master']]
        fs_ = [b for _, b in by['followup_slave']]
        t_ok = (len(heard) == 1 and heard[0] in idp[-2:] and
                fhs['start_sample'] - last_first == TWO_SLOTS and
                ack['start_sample'] - fhs['start_sample'] == SLOT and
                fm[0]['start_sample'] - fhs['start_sample'] == TWO_SLOTS and fm[0]['ptype'] == 'POLL'
                and resp['start_sample'] - heard[0]['start_sample'] < TWO_SLOTS)
        pairs_ok = len(fm) == len(fs_) == followup and all(
            s['start_sample'] - m['start_sample'] == SLOT and s['channel'] == m['channel']
            and s['clk'] == m['clk'] + 2 and m['clk'] % 4 == 0 for m, s in zip(fm, fs_))
        # master packets are on master slots, in order, with idle slots between
        ms = [m['start_sample'] for m in fm]
        grid_ok = all((b - a) % TWO_SLOTS == 0 and b > a for a, b in zip(ms, ms[1:]))
        # clocks run on from the FHS clock, two ticks a slot, and the kernel gives the channels
        clk_ok = fm[0]['clk'] == fhs['clk'] + 4 == E['master_clk_at_connection'] and all(
            m['clk'] == fhs['clk'] + 4 + 2 * ((m['start_sample'] - fm[0]['start_sample']) // SLOT) for m in fm)
        hop_ok = all(bt_hop.hop_channel(m['clk'], M_ADDRESS, MASK) == m['channel'] for m in fm)
        types_ok = {m['ptype'] for m in fm} <= {'POLL', 'NULL', 'DH1'} and {s['ptype'] for s in fs_} <= {'NULL', 'DM1', 'DH1'}
        hdr_ok = all(m['lap'] == M_LAP and m['uap_for_hec'] == M_UAP and m['lt_addr'] == 1 for m in fm + fs_)
        res = (order_ok, slots_ok, t_ok, pairs_ok, grid_ok, clk_ok, hop_ok, types_ok, hdr_ok)
        if not all(res):
            failed += 1
            if failed <= 3:
                print('      exchange %d: order %s slots %s timing %s pairs %s grid %s clocks %s hops %s types %s headers %s'
                      % ((e,) + res))
    check(failed == 0, 'every exchange has the order IDs, response, FHS, ack, POLL, follow-up; two IDs per slot 312.5 us '
          'apart on distinct channels; the response 625 us after the heard ID; the FHS 1250 us after the first ID of '
          'its slot; the ack 625 us and the POLL 1250 us after the FHS; each slave answer one slot later on the master\'s '
          'channel; the clocks run on from the FHS; the kernel gives the channels (%d of %d exchanges wrong)' % (failed, exchanges))
    fm_all = [b for b in bursts if b['kind'] == 'followup_master']
    check(all(len([1 for _, b in ex[e] if b['kind'] == 'followup_master']) >= min(100, followup) for e in ex),
          'at least %d master follow-up packets after every FHS (%d per exchange)' % (min(100, followup), followup))
    sl_all = [b for b in bursts if b['kind'] == 'followup_slave']
    check({s['ptype'] for s in sl_all} == {'NULL', 'DM1', 'DH1'} and min(sum(s['ptype'] == t for s in sl_all) for t in ('NULL', 'DM1', 'DH1')) > 0.2 * len(sl_all),
          'the slave answers are a mix of NULL, DM1 and DH1 (s8.3.3.2 allows all three): %s'
          % {t: sum(s['ptype'] == t for s in sl_all) for t in ('NULL', 'DM1', 'DH1')})
    types = {t: sum(m['ptype'] == t for m in fm_all) for t in ('POLL', 'NULL', 'DH1')}
    check(all(v > 0.2 * len(fm_all) for v in list(types.values())[1:]) and types['POLL'] >= exchanges,
          'a mix of POLL, NULL and DH1: %s' % types)
    heards = [(side['exchanges'][e]['heard_id']) for e in range(exchanges)]
    if exchanges >= 6:
        check(set(heards) == {0, 1}, 'both the first and the second ID of a slot are the heard one (%s)' % sorted(set(heards)))
    gaps = [E['gap_before_ms'] for E in side['exchanges'][1:]]
    real = []
    for e in range(1, exchanges):
        prev = side['exchanges'][e - 1]
        last = bursts[prev['last_burst']]
        first = bursts[side['exchanges'][e]['first_burst']]
        real.append((first['start_sample'] - (last['start_sample'] + nbits(last) * SPS)) / FS * 1e3)
    check(all(5.0 <= g <= 40.5 for g in real) and all(abs(g - r) < 0.1 for g, r in zip(gaps, real)),
          'the idle gap between exchanges is %.1f to %.1f ms from the samples (5 to 40)' % (min(real), max(real))
          if real else 'single exchange')

    # the validator, on every master-LAP burst after the FHS: master packets and slave answers
    valid = 0
    neg = dict(lap=0, clock=0, slot=0, mapw=0, removed=0, nextexch=0)
    n_next = 0
    for e in range(exchanges):
        items = ex[e]
        fhs = next(b for _, b in items if b['kind'] == 'fhs')
        after = [b for _, b in items if b['kind'] in ('followup_master', 'followup_slave')]
        starts = [b['start_sample'] for b in after]
        obs = observations(starts, [b['channel'] for b in after])
        since = (starts[0] - fhs['start_sample']) // SLOT
        c27 = fhs['fhs']['clk27_2']
        valid += validates(obs, c27, since)
        if controls:
            neg['lap'] += not validates(obs, c27, since, address=M_UAP << 24 | 0x112234)
            neg['clock'] += not validates(obs, c27 + 1, since)
            neg['slot'] += not validates(obs, c27, since + 1)
            neg['mapw'] += not validates(obs, c27, since, mask=sum(1 << c for c in range(30, 50)))
            neg['removed'] += not validates([], c27, since) and not validates(obs[:3], c27, since)
            # bluey's false positive: this exchange's follow-up removed, so the packets on the master's LAP
            # after its FHS are the NEXT exchange's, on another clock
            if e + 1 < exchanges:
                n_next += 1
                nxt = [b for _, b in ex[e + 1] if b['kind'] in ('followup_master', 'followup_slave')]
                ns = [b['start_sample'] for b in nxt]
                neg['nextexch'] += not validates(observations(ns, [b['channel'] for b in nxt]), c27,
                                                 (ns[0] - fhs['start_sample']) // SLOT)
    check(valid == exchanges, 'the validator (CLK27-1 brute force over the 31-50 map, master packets and slave answers) '
          'finds each exchange\'s own FHS clock from its follow-up, unique: %d of %d' % (valid, exchanges))
    if controls:
        check(neg == dict(lap=exchanges, clock=exchanges, slot=exchanges, mapw=exchanges, removed=exchanges,
                          nextexch=n_next),
              'negative controls fail for every exchange: wrong master LAP, FHS clock off by one CLK27-2 step, off by '
              'one slot, wrong map, follow-up removed, and the next exchange\'s packets after this FHS (%s)' % neg)


def check_plan_inquiry(side, ex, exchanges):
    bursts = side['bursts']
    ids = [b for b in bursts if b['kind'] == 'id_inquiry']
    check(all(b['lap'] == GIAC and b['ptype'] == 'ID' and b['uap_for_hec'] is None for b in ids),
          'the inquiry IDs carry the GIAC 0x9E8B33 and have no header')
    fh = [b for b in bursts if b['kind'] == 'fhs_inquiry_response']
    check(len(fh) == exchanges and len(bursts) == len(ids) + len(fh),
          'one FHS response per exchange and nothing after it: no follow-up burst of any kind')
    check(all(b['lap'] == GIAC and b['uap_for_hec'] == 0 and b['fhs']['header_uap'] == 0 and b['fhs']['am_addr'] == 0
              and b['fhs']['lt_addr_header'] == 0 and b['lt_addr'] == 0 and b['fhs']['eir'] == 0 and b['fhs']['sp'] == 2 and b['fhs']['reserved'] == 0 and b['fhs']['previously_used'] == 0
              and b['fhs']['clk27_2'] == b['clk'] >> 2 for b in fh),
          'each FHS response: the GIAC access code, HEC and CRC from the DCI 0x00, LT_ADDR 0, EIR 0, SP 2, whitened '
          'and clocked by the scanner\'s own clock')
    check(all(rebuild_fhs_ok(b, 0, GIAC) for b in fh),
          'its header and payload bytes are what the encoder gives for the sidecar\'s fields with the DCI')
    laps = [b['fhs']['lap'] for b in fh]
    check(len(set(laps)) == min(exchanges, gen.SCANNER_POOL) and all(not 0x9E8B00 <= l <= 0x9E8B3F and l not in (PAGED_LAP, M_LAP) for l in laps),
          'the scanning devices differ (%d LAPs, a pool of 50), none reserved' % len(set(laps)))
    check(all(b['clk'] % 4 in (2, 3) for b in fh) and len({b['fhs']['clk27_2'] for b in fh}) == exchanges,
          'the scanner\'s own clock is in an odd slot, and its CLK27-2 differs per exchange')
    failed = 0
    for e in range(exchanges):
        idp = [b for _, b in ex[e] if b['kind'] == 'id_inquiry']
        f = [b for _, b in ex[e] if b['ptype'] == 'FHS'][0]
        s_id = [b['start_sample'] for b in idp]
        n = len(idp)
        slots_ok = n % 2 == 0 and 3 <= n // 2 <= 8 and all(
            s_id[2 * k + 1] - s_id[2 * k] == HALF_SLOT and idp[2 * k]['channel'] != idp[2 * k + 1]['channel']
            for k in range(n // 2)) and all(s_id[2 * k + 2] - s_id[2 * k] == TWO_SLOTS for k in range(n // 2 - 1))
        heard = [b for b in idp if f['start_sample'] - b['start_sample'] == SLOT]
        ok = slots_ok and len(heard) == 1 and heard[0] in idp[-2:] and ex[e][-1][1] is f
        failed += not ok
    check(failed == 0, 'every exchange: two IDs per slot 312.5 us apart, the FHS response 625 us after the heard '
          'ID, last in the exchange (%d wrong)' % failed)
    # the FHS decoded with the DCI verifies; with the sender's UAP it does not
    bad = 0
    for b in fh[:50]:
        want = bt_fhs.inquiry_response(b['fhs']['lap'], b['fhs']['uap'], b['fhs']['nap'], b['fhs']['class_of_device'],
                                       b['clk'], b['fhs']['whitening_x'], access_lap=GIAC, sr=b['fhs']['sr'])
        got = bt_fhs.parse_fhs_air_bits(want.bits, 0, tx_clk=b['clk'], whitening_x=b['fhs']['whitening_x'])
        wrong = bt_fhs.parse_fhs_air_bits(want.bits, b['fhs']['uap'], tx_clk=b['clk'], whitening_x=b['fhs']['whitening_x']) if b['fhs']['uap'] else None
        bad += not (got['crc_ok'] and got['header_ok'] and got['lap'] == b['fhs']['lap']
                    and (wrong is None or not (wrong['crc_ok'] and wrong['header_ok'])))
    check(bad == 0, 'the inquiry FHS passes its HEC and CRC with the DCI and fails them with the scanner\'s own UAP')
    check(side['followup_per_exchange'] == 0 and 'no follow-up' in side['followup_note'].lower(),
          'the sidecar says the inquiry has no follow-up traffic')


# --- reading a recording the way a receiver has to ----------------------------


def rough_bursts(iq):
    """Where the power envelope says the bursts are: ``(start, end)`` pairs, the
    200-sample mean of |iq|^2 against twice the planted noise power."""
    n = len(iq)
    p = np.empty(n)
    for lo in range(0, n, 1 << 22):
        x = iq[lo:lo + (1 << 22)]
        p[lo:lo + len(x)] = x.real.astype(np.float64) ** 2 + x.imag.astype(np.float64) ** 2
    c = np.concatenate([[0.0], np.cumsum(p)])
    del p
    w = 200
    mean = (c[w:] - c[:-w]) / w
    up = mean > 2 * NOISE_POWER
    edges = np.flatnonzero(np.diff(up.astype(np.int8)))
    starts, ends = edges[::2] + 1 + w // 2, edges[1::2] + 1 + w // 2
    spans = []
    for a, b in zip(starts.tolist(), ends.tolist()):
        if spans and a - spans[-1][1] < 300:
            spans[-1][1] = b
        else:
            spans.append([a, b])
    return [(a, b) for a, b in spans if b - a > 1500]          # an ID is 2,720 samples


def carrier_of(iq, s0, s1):
    """The carrier of a burst, MHz, from the middle of its rough span: the
    centroid of its spectrum within 500 kHz of the peak, and the nearest channel."""
    x = np.asarray(iq[s0 + 250:s1 - 250])
    nfft = 1024
    m = len(x) // nfft
    if m < 1:
        return float('nan'), -1
    x = x[:m * nfft].reshape(m, nfft) * np.hanning(nfft)
    spec = np.sum(np.abs(np.fft.fft(x, axis=1)) ** 2, axis=0)
    freq = np.fft.fftfreq(nfft, 1 / FS)
    near = np.abs(freq - freq[int(np.argmax(spec))]) < 500e3
    mhz = CENTER + np.sum(freq[near] * spec[near]) / np.sum(spec[near]) / 1e6
    return mhz, int(round(mhz - 2402))


def shifted_down(iq, lo, hi, channel):
    n = np.arange(lo, hi)
    cycles = (2402.0 + channel - CENTER) * 1e6 / FS * n
    return lfilter(TAPS, 1, np.asarray(iq[lo:hi]) * np.exp(-2j * np.pi * (cycles - np.floor(cycles))))


def demod(iq, rough_start, rough_end, channel, laps, nb=380):
    """Shift a burst down from ``channel``, correlate with the 68 bits of each
    candidate LAP's preamble and sync word (an ID has no more), take the best,
    and slice ``nb`` bits. Returns ``(start, lap, r, bits)``; ``start`` is the
    correlation's first-preamble-sample, or ``None`` if no LAP matches."""
    lo = max(0, rough_start - 1500)
    hi = min(len(iq), rough_end + 2500)               # this burst only: the next may carry the same access code
    bb = shifted_down(iq, lo, hi, channel)
    d = np.angle(bb[1:] * np.conj(bb[:-1]))
    dm = d - np.convolve(d, np.ones(2001) / 2001, 'same')
    best = None
    for lap in laps:
        a68 = np.array(bt_fhs.id_bits(lap)) * 2 - 1
        tmpl = np.repeat(a68, SPS).astype(float)
        tmpl -= tmpl.mean()
        c = fftconvolve(dm, tmpl[::-1], 'valid')
        norm = np.sqrt(fftconvolve(dm ** 2, np.ones(len(tmpl)), 'valid')) * np.linalg.norm(tmpl)
        r = c / np.maximum(norm, 1e-12)
        p = int(np.argmax(r))
        if best is None or r[p] > best[1]:
            best = (lap, float(r[p]), p, a68)
    lap, r, p, a68 = best
    if r < 0.5:
        return None, None, r, None
    nb = min(nb, (len(d) - p) // SPS - 1)
    centres = p + (np.arange(nb) + 0.5) * SPS - 0.5
    f = np.interp(centres, np.arange(len(d)), d)
    _, thr = np.polyfit(a68, f[:68], 1)
    return lo + p - GD, lap, r, (f > thr).astype(int)


def burst_power(iq, start, n):
    x = np.asarray(iq[start + 300:start + n * SPS - 300])
    return float(np.mean(x.real.astype(np.float64) ** 2 + x.imag.astype(np.float64) ** 2))


def measure(iq, laps):
    out = []
    for s0, s1 in rough_bursts(iq):
        mhz, channel = carrier_of(iq, s0, s1)
        start, lap, r, bits = (None, None, 0, None) if channel < 0 else demod(iq, s0, s1, channel, laps)
        out.append(dict(rough=(s0, s1), mhz=mhz, channel=channel, start=start, lap=lap, bits=bits))
    return out


# --- libbtbb ----------------------------------------------------------------------

_lib = bt_ota_check.libbtbb()
if _lib is not None:
    vp = ctypes.c_void_p
    for _n, _r, _a in (('lap_from_fhs', ctypes.c_uint32, [vp]), ('uap_from_fhs', ctypes.c_uint8, [vp]),
                       ('nap_from_fhs', ctypes.c_uint16, [vp]), ('clock_from_fhs', ctypes.c_uint32, [vp])):
        _f = getattr(_lib, _n)
        _f.restype, _f.argtypes = _r, _a


def btbb_fhs(bits, uap, clk):
    """What libbtbb makes of an FHS's air bits taken with a UAP and a clock:
    None if it refuses the header, else its payload verdict (1000 good) and
    the LAP, UAP, NAP and CLK27-2 it reads."""
    raw = bytes(int(b) for b in bits[4:366])
    pkt = _lib.btbb_packet_new()
    try:
        _lib.btbb_packet_set_data(pkt, ctypes.cast(ctypes.c_char_p(raw), ctypes.POINTER(ctypes.c_char)),
                                  len(raw), 0, ((clk >> 1) & 0x3F) << 1)
        _lib.btbb_packet_set_uap(pkt, uap)
        _lib.btbb_packet_set_flag(pkt, 4, 1)
        _lib.btbb_packet_set_flag(pkt, 0, 1)
        if _lib.btbb_decode_header(pkt) != 1:
            return None
        out = {'verdict': _lib.btbb_decode_payload(pkt)}
        if out['verdict'] == 1000:
            out.update(lap=_lib.lap_from_fhs(pkt), uap=_lib.uap_from_fhs(pkt), nap=_lib.nap_from_fhs(pkt),
                       clk27_2=_lib.clock_from_fhs(pkt))
        return out
    finally:
        _lib.btbb_packet_unref(pkt)


def expected_bits(b):
    """The burst's air bits rebuilt from the sidecar's fields by the encoder."""
    if b['ptype'] == 'ID':
        return bt_fhs.id_bits(b['lap'])
    if b['ptype'] == 'FHS':
        f = b['fhs']
        return bt_fhs.FHS(f['access_lap'], f['lap'], f['uap'], f['nap'], f['class_of_device'], f['am_addr'],
                          f['clk27_2'] << 2, whitening_x=f['whitening_x'], lt_addr=f['lt_addr_header'], header_uap=f['header_uap'],
                          sr=f['sr'], sp=f['sp'], eir=f['eir']).bits
    return br.Packet(b['lap'], b['uap_for_hec'], b['clk'], b['ptype'], bytes.fromhex(b['payload_hex']),
                     lt_addr=b['lt_addr'], flow=b['flow'], arqn=b['arqn'], seqn=b['seqn']).bits


def check_samples(iq, side, label, bits_exact=True, tol=2):
    """Every burst of ``side`` against the samples: count, LAP, carrier, start,
    level, length, bits, and the timing relations of the specification."""
    print('\nThe samples of %s: %d bursts, %.0f dB' % (label, len(side['bursts']), side['master_snr_db']))
    bursts = side['bursts']
    laps = sorted({b['lap'] for b in bursts if b['ptype'] == 'ID'} | {b['lap'] for b in bursts})
    laps = [l for l in laps if l in (PAGED_LAP, M_LAP, GIAC)]
    got = measure(iq, laps)
    check(len(got) == len(bursts), 'the envelope shows %d bursts, the sidecar has %d' % (len(got), len(bursts)))
    if len(got) != len(bursts):
        return got
    check(all(g['start'] is not None for g in got), 'an access code (of the LAPs %s) is found in every burst'
          % ', '.join('%#x' % l for l in laps))
    if any(g['start'] is None for g in got):
        return got
    check([g['lap'] for g in got] == [b['lap'] for b in bursts],
          'the LAP the samples\' access code correlates best with is the sidecar\'s, for all %d bursts' % len(bursts))
    check([g['channel'] for g in got] == [b['channel'] for b in bursts],
          'every burst\'s carrier, measured on the raw samples, is its channel (%d of %d)'
          % (sum(g['channel'] == b['channel'] for g, b in zip(got, bursts)), len(bursts)))
    resid = np.array([g['mhz'] - b['channel_mhz'] for g, b in zip(got, bursts)]) * 1e3
    check(np.abs(resid).max() < 150, 'and within %.0f kHz of it (a channel is 1000)' % np.abs(resid).max())
    off = np.array([g['start'] - b['start_sample'] for g, b in zip(got, bursts)])
    check(np.abs(off).max() <= tol + (1 if side['master_snr_db'] < 30 else 0),
          'start_sample is where the access code is: worst %d samples off' % np.abs(off).max())
    want_db = np.array([b['snr_db'] for b in bursts])
    p = np.array([burst_power(iq, b['start_sample'], nbits(b)) for b in bursts]) - NOISE_POWER
    err = 10 * np.log10(p / (bt_synth.AMPLITUDE ** 2 / 100 * 10 ** (want_db / 10)))
    check(np.abs(err).max() < 0.6, 'levels from the samples (power less the noise) are within %.2f dB of snr_db '
          'over the noise in 1 MHz' % np.abs(err).max())
    length = np.array([g['rough'][1] - g['rough'][0] for g in got]) - np.array([nbits(b) * SPS for b in bursts])
    check(np.abs(length).max() < 700, 'each burst\'s length in the envelope is its packet\'s (68 bits for an ID, 366 for an FHS, '
          '126, 126 + payload): worst %d samples off' % np.abs(length).max())
    ids = [i for i, b in enumerate(bursts) if b['ptype'] == 'ID']
    check(all(abs(length[i]) < 700 for i in ids), 'an ID is 68 bits on air - no trailer, no header: %d IDs, worst %d samples off'
          % (len(ids), np.abs(length[ids]).max()))
    # the bits
    bad_ac = [i for i, (g, b) in enumerate(zip(got, bursts))
              if list(g['bits'][:68]) != list(expected_bits(b)[:68])]
    check(not bad_ac, 'every burst\'s 68 preamble and sync bits are those of its LAP (%d wrong)' % len(bad_ac))
    if bits_exact:
        bad = [i for i, (g, b) in enumerate(zip(got, bursts)) if list(g['bits'][:nbits(b)]) != list(expected_bits(b))]
        check(not bad, 'every burst\'s bits, ID, FHS, POLL, NULL and DH1, are the packet the encoder builds from the '
              'sidecar\'s fields (%d of %d differ%s)' % (len(bad), len(bursts), ': first %s' % bad[:3] if bad else ''))
        check_decodes(got, bursts)
    # the relations of the text, measured on the samples
    relations(got, bursts, side, tol=1.6 if side['master_snr_db'] >= 30 else 4)
    return got


def check_decodes(got, bursts):
    """FHS through our parser and libbtbb; headers through libbtbb."""
    fhs_i = [i for i, b in enumerate(bursts) if b['ptype'] == 'FHS']
    bad_ours = bad_btbb = bad_wrong = 0
    for i in fhs_i:
        b, g = bursts[i], got[i]
        f = b['fhs']
        r = bt_fhs.parse_fhs_air_bits(g['bits'][:366], f['header_uap'], f['tx_clk'])
        ok = (r['crc_ok'] and r['header_ok'] and r['parity_ok'] and r['lap'] == f['lap'] and r['uap'] == f['uap']
              and r['nap'] == f['nap'] and r['cod'] == f['class_of_device'] and r['am_addr'] == f['am_addr']
              and r['clk27_2'] == f['clk27_2'] and r['sr'] == f['sr'] and r['sp'] == f['sp'] and r['eir'] == f['eir']
              and r['lt_addr'] == f['lt_addr_header'] and r['type'] == 2)
        bad_ours += not ok
        if _lib is not None:
            d = btbb_fhs(g['bits'], f['header_uap'], f['tx_clk'])
            bad_btbb += not (d and d['verdict'] == 1000 and d['lap'] == f['lap'] and d['uap'] == f['uap']
                             and d['nap'] == f['nap'] and d['clk27_2'] == f['clk27_2'])
            other = f['uap'] if f['header_uap'] != f['uap'] else (f['uap'] ^ 0x10)
            bad_wrong += btbb_fhs(g['bits'], other, f['tx_clk']) is not None
    if fhs_i:
        check(not bad_ours, 'the %d FHS decode through bt_fhs.parse_fhs_air_bits, at their HEC/CRC UAP and whitening clock, '
              'to the sidecar\'s fields (%d wrong)' % (len(fhs_i), bad_ours))
        if _lib is not None:
            check(not bad_btbb and not bad_wrong, 'and through libbtbb (header, CRC, LAP, UAP, NAP, CLK27-2); libbtbb refuses '
                  'each at the other UAP (%d wrong, %d accepted at the wrong UAP)' % (bad_btbb, bad_wrong))
    if fhs_i and _lib is not None:
        diff = [i for i in fhs_i if (bursts[i]['clk'] >> 1) & 0x3F != bursts[i]['fhs']['implied_clk6_1']]
        plain = sum(1 for i in diff if (lambda d: d is not None and d['verdict'] == 1000)(
            btbb_fhs(got[i]['bits'], bursts[i]['fhs']['header_uap'], bursts[i]['clk'])))
        check(plain == 0, 'libbtbb refuses the FHS at the plain CLK6-1 of its own clock where that differs from 32 + X '
              '(%d of %d accepted)' % (plain, len(diff)))
    hdr = [i for i, b in enumerate(bursts) if b['ptype'] in ('POLL', 'NULL', 'DH1', 'DM1')]
    if hdr and _lib is not None:
        bad = 0
        for i in hdr:
            b = bursts[i]
            bad += not all(bt_ota_check.check_btbb(_lib, got[i]['bits'][:nbits(b)], b, b['uap_for_hec']))
        check(bad == 0, 'the %d POLL, NULL, DM1 and DH1 decode through libbtbb at the master UAP and their own clk: header, CRC '
              'and payload (%d wrong)' % (len(hdr), bad))
        wrong = sum(bt_ota_check.check_btbb(_lib, got[i]['bits'][:nbits(bursts[i])], bursts[i],
                                            bursts[i]['uap_for_hec'] ^ 1)[0] for i in hdr[:40])
        check(wrong == 0, 'and libbtbb refuses their headers at another UAP (%d of 40 accepted)' % wrong)


def relations(got, bursts, side, tol):
    """The timing relations of the specification, measured on the samples:
    each is ``(name, [(i, j, nominal)])`` meaning start_j - start_i should be
    the nominal samples plus the timing-fraction difference."""
    ex = by_exchange(side)
    rel = {'second ID 312.5 us after the first': [], 'response 625 us after the heard ID': [],
           'next slot\'s first ID 1250 us after': [], 'FHS 1250 us after the first ID of the heard slot': [],
           'acknowledgement 625 us after the FHS': [], 'POLL 1250 us after the FHS': [],
           'slave answer 625 us after its master': [], 'inquiry response 625 us after the heard ID': []}
    for e, items in ex.items():
        idx = [i for i, _ in items]
        ids = [i for i in idx if bursts[i]['kind'] in ('id_page', 'id_inquiry')]
        for k in range(0, len(ids) - 1, 2):
            rel['second ID 312.5 us after the first'].append((ids[k], ids[k + 1], HALF_SLOT))
        for k in range(0, len(ids) - 2, 2):
            rel['next slot\'s first ID 1250 us after'].append((ids[k], ids[k + 2], TWO_SLOTS))
        for i in idx:
            b = bursts[i]
            if b['kind'] in ('id_response', 'fhs_inquiry_response'):
                heard = [j for j in ids if b['start_sample'] - bursts[j]['start_sample'] == SLOT][0]
                name = 'response 625 us after the heard ID' if b['kind'] == 'id_response' else \
                    'inquiry response 625 us after the heard ID'
                rel[name].append((heard, i, SLOT))
                if b['kind'] == 'id_response':
                    f = next(j for j in idx if bursts[j]['kind'] == 'fhs')
                    rel['FHS 1250 us after the first ID of the heard slot'].append((ids[-2], f, TWO_SLOTS))
            if b['kind'] == 'id_ack':
                rel['acknowledgement 625 us after the FHS'].append((i - 1, i, SLOT))
                rel['POLL 1250 us after the FHS'].append((i - 1, i + 1, TWO_SLOTS))
            if b['kind'] == 'followup_master':
                rel['slave answer 625 us after its master'].append((i, i + 1, SLOT))
    for name, rows in rel.items():
        if not rows:
            continue
        d = np.array([got[j]['start'] - got[i]['start'] - nominal - (bursts[j]['timing_frac'] - bursts[i]['timing_frac'])
                      for i, j, nominal in rows])
        check(np.abs(d).max() <= tol and (len(d) < 10 or abs(d.mean()) <= 0.6),
              '%s: %d measured, worst %+.2f samples off, mean %+.2f (within %.1f)'
              % (name, len(rows), d[np.argmax(np.abs(d))], d.mean(), tol))
    pairs = [(i, i + 1) for i, b in enumerate(bursts) if b['kind'] == 'followup_master']
    if pairs:
        check(all(got[i]['channel'] == got[j]['channel'] for i, j in pairs),
              'each slave answer is on its master\'s carrier, measured (%d pairs)' % len(pairs))


def check_validator_on_samples(got, side):
    """The validator on the MEASURED starts and carriers: every exchange validates."""
    ex = by_exchange(side)
    ok = 0
    n = 0
    for e, items in ex.items():
        if not any(b['kind'] == 'fhs' for _, b in items):
            continue
        n += 1
        fi = next(i for i, b in items if b['kind'] == 'fhs')
        after = [i for i, b in items if b['kind'] in ('followup_master', 'followup_slave')]
        starts = [got[i]['start'] for i in after]
        obs = observations(starts, [got[i]['channel'] for i in after])
        since = int(round((starts[0] - got[fi]['start']) / SLOT))
        # the FHS clock comes from decoding the measured FHS bits, not from the sidecar
        r = bt_fhs.parse_fhs_air_bits(got[fi]['bits'][:366], side['bursts'][fi]['fhs']['header_uap'],
                                      side['bursts'][fi]['fhs']['tx_clk'])
        lo = min(16, len(obs))
        good = validates(obs, r['clk27_2'], since, min_obs=lo)
        bad = (validates(obs, r['clk27_2'], since, address=M_UAP << 24 | 0x112234, min_obs=lo)
               or validates(obs, r['clk27_2'] + 1, since, min_obs=lo))
        ok += good and not bad
    check(n > 0 and ok == n, 'from the MEASURED starts and carriers and the FHS clock decoded from the measured bits, '
          'the validator finds every exchange\'s clock (%d of %d), and not with the wrong master LAP or a clock off by one' % (ok, n))


def check_noise_and_idle(iq, side):
    print('\nThe noise floor and the idle gaps')
    x = np.asarray(iq[:20000])
    power = float(np.mean(x.real.astype(np.float64) ** 2 + x.imag.astype(np.float64) ** 2))
    check(abs(10 * np.log10(power / NOISE_POWER)) < 0.2,
          'noise from sample 0: %+.2f dB against the one floor' % (10 * np.log10(power / NOISE_POWER)))
    bursts = side['bursts']
    lv = []
    for e in range(1, side['n_exchanges']):
        last = bursts[side['exchanges'][e - 1]['last_burst']]
        a = last['start_sample'] + nbits(last) * SPS + 3000
        b = bursts[side['exchanges'][e]['first_burst']]['start_sample'] - 3000
        y = np.asarray(iq[a:b])
        lv.append(np.mean(y.real.astype(np.float64) ** 2 + y.imag.astype(np.float64) ** 2))
    lv = np.array(lv)
    check(len(lv) > 0 and np.abs(lv / NOISE_POWER - 1).max() < 0.03,
          '%d gaps between exchanges hold the noise floor and nothing else (%.4f to %.4f of %.4f)'
          % (len(lv), lv.min(), lv.max(), NOISE_POWER))


# --- determinism -------------------------------------------------------------------

def check_determinism():
    print('\nDeterminism and the command line')
    a, sa = gen.synthesise_page(kind='page', exchanges=2, followup=8, seed=7301)
    b, sb = gen.synthesise_page(kind='page', exchanges=2, followup=8, seed=7301)
    c, _ = gen.synthesise_page(kind='page', exchanges=2, followup=8, seed=7302)
    check(a.tobytes() == b.tobytes() and json.dumps(sa) == json.dumps(sb), 'the same arguments give the same samples and sidecar')
    check(a.tobytes() != c.tobytes(), 'another seed gives another file')
    z1, _ = gen.synthesise_page(kind='page', exchanges=2, followup=8, seed=7301, noise=False)
    z2, _ = gen.synthesise_page(kind='page', exchanges=2, followup=8, seed=7301, noise=False, block=1 << 19)
    check(z1.tobytes() == z2.tobytes() and z1.any(), 'the bursts alone are the same bytes however the blocks fall '
          '(blocks of 2**19, not 2**22; the noise is drawn per block, so it is the block size\'s own)')
    check(a.dtype == np.complex64, 'complex64')
    # the plan of the delivered set: names and seeds
    check([n for n, _ in gen.PAGE_SET] == ['page_exch_hop20', 'inq_exch_hop20']
          and [s['seed'] for _, s in gen.PAGE_SET] == [7001, 7002], '--set page is the two files, seeds 7001 and 7002')
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        orig = gen.PAGE_SET[:]
        gen.SETS['page'][:] = [(n, dict(s, exchanges=2, followup=6)) for n, s in orig]
        try:
            gen.main(['--set', 'page', '--out', d])
        finally:
            gen.SETS['page'][:] = orig
        names = sorted(os.listdir(d))
        check(names == ['synth_inq_exch_hop20.cf32', 'synth_inq_exch_hop20.json', 'synth_page_exch_hop20.cf32',
                        'synth_page_exch_hop20.json'], '--set page writes exactly the two names: %s' % names)
        gen.main(['single', '--kind', 'page', '--exchanges', '2', '--followup', '6', '--seed', '7001', '--out', d])
        one = open(os.path.join(d, 'synth_single.cf32'), 'rb').read()
        two = open(os.path.join(d, 'synth_page_exch_hop20.cf32'), 'rb').read()
        check(one == two, 'a single run with the same arguments is byte-identical to the set\'s file (one code path)')
    rc = 0
    try:
        with contextlib.redirect_stderr(open(os.devnull, 'w')):
            gen.main(['--set', 'nonsense'])
    except SystemExit as e:
        rc = e.code
    check(rc not in (0, None), 'an unknown set exits non-zero (%s)' % rc)
    for kw in (dict(kind='sideways'), dict(exchanges=0), dict(followup=0), dict(start_phase=7),
               dict(channels=[40, 70]), dict(idle_probs=[0.5, 0.5, 0.5])):
        try:
            gen.plan_exchanges(**dict(dict(exchanges=2, followup=4), **kw))
            check(False, 'bad argument %r is refused' % kw)
        except ValueError:
            check(True, 'bad argument %r is refused with ValueError' % kw)


# --- mutants -----------------------------------------------------------------------

SRC = open(gen.__file__).read()


def mutant_module(old, new):
    """The generator with one text replaced once, as a module of its own."""
    assert SRC.count(old) == 1, 'mutation target not unique: %r (%d)' % (old, SRC.count(old))
    spec = importlib.util.spec_from_file_location('bt_synth_page_mut', gen.__file__)
    mod = importlib.util.module_from_spec(spec)
    code = compile(SRC.replace(old, new), gen.__file__, 'exec')
    exec(code, mod.__dict__)
    return mod


PAGE_CALL = ("f = bt_fhs.page_response(PAGED['lap'], PAGED['uap'], MASTER['lap'], MASTER['uap'], MASTER['nap'],\n"
             "                                     MASTER['cod'], AM_ADDR, fhs_clk, x, sr=sr)")
INQ_CALL = "f = bt_fhs.inquiry_response(s_lap, s_uap, s_nap, s_cod, s_clk, x, access_lap=GIAC,"
PAGE_FHS = "f = bt_fhs.FHS(PAGED['lap'], MASTER['lap'], MASTER['uap'], MASTER['nap'], MASTER['cod'], AM_ADDR, fhs_clk, lt_addr=0, %s, sr=sr)"
TRAIN = "train, koffset, knudge, n_ctr = ('A', 24, 0, 1) if rng.integers(0, 2) else ('B', 8, 0, 1)"
XPRC = "x = bt_fhs.xprc(clke, koffset, knudge, n_ctr)"

MUTANTS = [
    ('FHS clock off by one slot-pair (clk27_2 + 1)', "AM_ADDR, fhs_clk, x, sr=sr)", "AM_ADDR, fhs_clk + 4, x, sr=sr)", 'page', False),
    ('follow-up hopping from the wrong clock', 'channel = int(hop_fn(clk_m))', 'channel = int(hop_fn(clk_m + 4))', 'page', False),
    ('follow-up on the wrong map', "hop_fn = hop.afh_hop_fn([(0, channels)], MASTER['lap'], MASTER['uap'])",
     "hop_fn = hop.afh_hop_fn([(0, list(range(30, 50)))], MASTER['lap'], MASTER['uap'])", 'page', False),
    ('response at 312.5 us instead of 625 us', 'resp_tick = heard_burst_tick + 2', 'resp_tick = heard_burst_tick + 1', 'page', False),
    ('one ID per slot', 'for j in (0, 1):\n                page_channels.append', 'for j in (0,):\n                page_channels.append', 'page', False),
    ('slave answer on the next channel', 'evs.append(dict(tick=t_m + off, kind=kk, role=role, channel=channel,',
     'evs.append(dict(tick=t_m + off, kind=kk, role=role, channel=(channel if not off else (channel + 1 if channel < 50 else 31)),', 'page', False),
    ('wrong LAP in the ID', "packet=Raw(bt_fhs.id_bits(id_lap), 'ID')", "packet=Raw(bt_fhs.id_bits(id_lap ^ 1), 'ID')", 'page', True),
    ('FHS header_uap = master UAP', PAGE_CALL, PAGE_FHS % "header_uap=MASTER['uap'], whitening_x=x", 'page', True),
    ('HEC init wrong (follow-up headers from UAP ^ 1)', "p = br.Packet(MASTER['lap'], MASTER['uap'], clk, ptype, body",
     "p = br.Packet(MASTER['lap'], MASTER['uap'] ^ 1, clk, ptype, body", 'page', True),
    ("inquiry FHS with the sender's UAP instead of the DCI", INQ_CALL,
     'f = bt_fhs.FHS(GIAC, s_lap, s_uap, s_nap, s_cod, 0, s_clk, lt_addr=0, header_uap=s_uap, whitening_x=x,', 'inquiry', True),
    ('page FHS whitened from CLK6-1 of the clock, not the X-input', PAGE_CALL, PAGE_FHS % "header_uap=PAGED['uap']", 'page', True),
    ('page FHS whitened from X without its two MSBs of 1', PAGE_CALL, PAGE_FHS % "header_uap=PAGED['uap'], tx_clk=x << 1", 'page', True),
    ('inquiry FHS whitened from CLK6-1 of the clock', INQ_CALL,
     'f = bt_fhs.FHS(GIAC, s_lap, s_uap, s_nap, s_cod, 0, s_clk, lt_addr=0, header_uap=bt_fhs.DCI,', 'inquiry', True),
    ('page FHS with SP = 0 instead of 0b10', PAGE_CALL, PAGE_CALL.replace('x, sr=sr)', 'x, sr=sr, sp=0)'), 'page', True),
    ('page FHS with EIR = 1', PAGE_CALL, PAGE_CALL.replace('x, sr=sr)', 'x, sr=sr, eir=1)'), 'page', True),
    ('page FHS with the reserved bit = 1', PAGE_CALL, PAGE_CALL.replace('x, sr=sr)', 'x, sr=sr, reserved=1)'), 'page', True),
    ('N starts at 0 (recorded honestly)', TRAIN, "train, koffset, knudge, n_ctr = ('A', 24, 0, 0) if rng.integers(0, 2) else ('B', 8, 0, 0)", 'page', False),
    ('N starts at 0 (recorded as 1)', XPRC, "x = bt_fhs.xprc(clke, koffset, knudge, n_ctr - 1)", 'page', False),
    ('k_offset 8 where the train is A (recorded as 24)', XPRC, "x = bt_fhs.xprc(clke, 8 if koffset == 24 else 24, knudge, n_ctr)", 'page', False),
    ('Xir without the scanner\'s CLKN16-12', "x = bt_fhs.xir(s_clk, n_ctr)", "x = bt_fhs.xir(0, n_ctr)", 'inquiry', False),
]


def check_mutants(lib_ok):
    print('\nMutants: one mistake each, in a copy of the generator, and what catches it')
    for name, old, new, kind, samples in MUTANTS:
        mod = mutant_module(old, new)
        with quiet() as failed:
            try:
                if samples:
                    iq, side = mod.synthesise_page(kind=kind, exchanges=3, followup=8, snr_db=40.0, seed=7401)
                    # the sidecar's own claims are checked on the plan first, as a reader would
                    check_plan(side, kind, 3, 8, 'mutant', controls=False)
                    check_samples(iq, side, 'mutant')
                else:
                    side, _, _ = mod.plan_exchanges(kind=kind, exchanges=6, followup=30, seed=7401)
                    check_plan(side, kind, 6, 30, 'mutant', controls=True)
            except Exception as e:                              # a crash is also a catch, said so
                failed.append('raised %s: %s' % (type(e).__name__, str(e)[:60]))
        # the checks are named by their first words
        short = sorted({w.split(':')[0][:70] for w in failed})
        check(len(failed) > 0, 'mutant "%s" is caught by %d checks, e.g. %s' % (name, len(failed), '; '.join(short[:3])))


# --- a real file --------------------------------------------------------------------

def crop(side, exchanges, iq_all):
    """The samples and a sidecar of the chosen exchanges, rebased to the crop."""
    lo = min(side['bursts'][side['exchanges'][e]['first_burst']]['start_sample'] for e in exchanges) - 20000
    hi = max(side['bursts'][side['exchanges'][e]['last_burst']]['start_sample'] + 30000 for e in exchanges)
    lo = max(lo, 0)
    iq = np.array(iq_all[lo:hi])
    out = dict(side)
    bs = []
    for e in exchanges:
        for i in range(side['exchanges'][e]['first_burst'], side['exchanges'][e]['last_burst'] + 1):
            b = dict(side['bursts'][i])
            b['start_sample'] -= lo
            bs.append(b)
    out['bursts'] = bs
    out['n_bursts'] = len(bs)
    new = []
    for k, e in enumerate(exchanges):
        E = dict(side['exchanges'][e])
        n = E['last_burst'] - E['first_burst'] + 1
        base = sum(side['exchanges'][x]['last_burst'] - side['exchanges'][x]['first_burst'] + 1 for x in exchanges[:k])
        E.update(first_burst=base, last_burst=base + n - 1)
        E['start_sample'] -= lo
        new.append(E)
        for b in bs[base:base + n]:
            b['exchange'] = k
    out['exchanges'] = new
    out['n_exchanges'] = len(new)
    return iq, out


def run_file(path, exch):
    side_path = os.path.splitext(path)[0] + '.json'
    side = json.load(open(side_path))
    mm = np.memmap(path, dtype='<c8', mode='r')
    print('%s: %d samples, %d bursts, %d exchanges' % (path, len(mm), side['n_bursts'], side['n_exchanges']))
    check(len(mm) == int(side['exchanges'][-1]['start_sample'] + 1) or len(mm) > side['bursts'][-1]['start_sample'],
          'the file is long enough for its sidecar')
    for e in exch:                                   # one exchange at a time: the idle gap between them is not in the crop
        iq, cs = crop(side, [e], mm)
        got = check_samples(iq, cs, 'exchange %d of %s' % (e, os.path.basename(path)),
                            bits_exact=True, tol=3)
        if side['file_kind'] == 'page' and got is not None and len(got) == len(cs['bursts']):
            check_validator_on_samples(got, cs)
    return side


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--file', help='a real .cf32 (or, with --plan, its .json)')
    ap.add_argument('--exchanges', default='0,1', help='the exchanges to read from the file, default 0,1')
    ap.add_argument('--plan', action='store_true', help='only the plan checks and the validator, on the sidecar')
    args = ap.parse_args()
    if args.file:
        if args.plan:
            side = json.load(open(args.file))
            followup = side['followup_per_exchange']
            check_plan(side, side['file_kind'], side['n_exchanges'], followup, os.path.basename(args.file))
        else:
            run_file(args.file, [int(x) for x in args.exchanges.split(',')])
    else:
        # 1. the delivered plans, no samples
        page, _, _ = gen.plan_exchanges('page', 200, 110, seed=7001)
        check_plan(page, 'page', 200, 110, 'the page file as delivered (seed 7001)')
        inq, _, _ = gen.plan_exchanges('inquiry', 200, 0, seed=7002)
        check_plan(inq, 'inquiry', 200, 0, 'the inquiry file as delivered (seed 7002)')
        # 2. small runs, samples
        iq, side = gen.synthesise_page(kind='page', exchanges=10, followup=20, snr_db=40.0, seed=7101)
        check_plan(side, 'page', 10, 20, 'a small page run at 40 dB', controls=False)
        got = check_samples(iq, side, 'a small page run (10 exchanges, 20 follow-up)')
        if got and len(got) == len(side['bursts']) and all(g['start'] is not None for g in got):
            check_validator_on_samples(got, side)
        check_noise_and_idle(iq, side)
        del iq
        iq, side = gen.synthesise_page(kind='page', exchanges=3, followup=20, snr_db=20.0, seed=7102)
        got = check_samples(iq, side, 'a page run at the delivered 20 dB (3 exchanges)', bits_exact=False)
        if got and len(got) == len(side['bursts']) and all(g['start'] is not None for g in got):
            check_validator_on_samples(got, side)
        del iq
        iq, side = gen.synthesise_page(kind='inquiry', exchanges=10, followup=0, snr_db=40.0, seed=7103)
        check_plan(side, 'inquiry', 10, 0, 'a small inquiry run at 40 dB', controls=False)
        check_samples(iq, side, 'a small inquiry run (10 exchanges)')
        check_noise_and_idle(iq, side)
        del iq
        iq, side = gen.synthesise_page(kind='inquiry', exchanges=3, followup=0, snr_db=20.0, seed=7104)
        check_samples(iq, side, 'an inquiry run at 20 dB (3 exchanges)', bits_exact=False)
        del iq
        check_determinism()
        check_mutants(_lib is not None)
    bad = [w for ok, w in RESULTS if not ok]
    print('\nRESULT: %s' % ('PASS' if not bad else 'FAIL (%d)' % len(bad)))
    for w in bad:
        print('  failed:', w[:150])
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
