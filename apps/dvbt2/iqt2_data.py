"""DVB-T2 data path, numpy only (roadmap Round 32, phase 3): from a T2-frame's IQ and its L1
(``iqt2``) to the BBFRAMEs and transport stream of one PLP, offline.

Written from ETSI EN 302 755 V1.4.1, clause by clause; the tables are in ``iqt2_tables``. In
receive order, for one T2-frame:

1. Every symbol after P1 to the frequency domain at the timing and frequency ``iqt2`` found
   from P2 (EN 9.5); each data symbol's common phase from its continual pilots (EN 9.2.4).
2. The channel: each data symbol's scattered and edge pilots (EN 9.2.3, 9.2.5), interpolated
   in time from the neighbouring symbols onto the comb of every Dx-th carrier, smoothed
   within the guard and interpolated in frequency; the frame closing symbol from its own
   pilots (EN 9.2.7); P2 as ``iqt2`` does it. Each cell's noise from the continual pilots'
   residual and the channel's power there.
3. The data cells of each symbol (not pilots; Tables 48, 49 count them), frequency
   de-interleaved (EN 8.5); the frame closing symbol's last NFC - CFC cells are empty
   (EN 8.3.9).
4. The PLP's cells from PLP_START (EN 8.3.6.2, 8.3.6.3.2), its time de-interleaving
   (EN 6.5: TI-blocks, the smaller first, each Ncells / 5 rows by 5 N_FEC columns), cell
   de-interleaving (EN 6.4), the rotation and cyclic Q delay undone (EN 6.3).
5. Soft bits (iqt2.demap_llr, each cell's noise), the bit de-multiplexer and the column-twist
   and parity interleavers undone (EN 6.2.1, 6.1.3; Tables 9, 10, 13).
6. The 64K LDPC (Annex A), many codewords at once: natively (``iqt2_native``, phase 4) when
   ``scripts/build_dvbt2_native.py`` has built it, else layered min-sum in NumPy; BCH not decoded.
7. Each BBFRAME descrambled (EN 5.2.4), its BBHEADER read and CRC-8-checked (EN 5.1.7,
   Annex F), and the TS packets rebuilt from the data fields (EN 5.1.8: High Efficiency or
   Normal Mode, deleted null packets put back, EN 5.1.5).

Limits (refused with a message, not guessed): type 1 or common PLPs, each in one T2-frame
(TIME_IL_TYPE 0, FRAME_INTERVAL 1), 64K FEC blocks, SISO, no tone reservation, 16k and 32k
(``iqt2``'s one P2 symbol). Rotated constellations are demapped after undoing the rotation, one
axis at a time (no two-dimensional demapping yet). Codewords the LDPC leaves unconverged go out
with their packets' transport_error_indicator set; BCH is not decoded.
"""

import functools
import math

import numpy as np

from . import iqt2, iqt2_native
from . import iqt2_tables as T

BITS = {0: 2, 1: 4, 2: 6, 3: 8}            # PLP_MOD -> bits per cell (Table 33)


# ------------------------------------------------------------------ carriers of each symbol

@functools.lru_cache(maxsize=None)
def continual_pilots(size, extended, pp):
    """The continual pilots' carriers (EN 9.2.4: Table 61, Annex G; not shifted by Kext)."""
    ks = []
    for g in T.CP_GROUPS[size]:
        ks += list(T.CP_32K.get(g, {}).get(pp, ()))
    ks = np.array(ks, int)
    if T.KMOD[size]:
        ks = ks % T.KMOD[size]
    if extended:
        ks = np.concatenate([ks, np.array(T.CP_EXT.get(size, {}).get(pp, ()), int)])
    return np.unique(ks)


def scattered(size, extended, pp, l):
    """Scattered pilot carriers of data symbol ``l`` (EN 9.2.3.1)."""
    return _scattered(size, extended, pp, l % T.DXY[pp][1])


@functools.lru_cache(maxsize=None)
def _scattered(size, extended, pp, l):
    dx, dy = T.DXY[pp]
    k = np.arange(iqt2.kmax(size, extended) + 1)
    off = iqt2.KEXT[size] if extended else 0
    return np.flatnonzero((k - off) % (dx * dy) == dx * (l % dy))


_LAYOUT = {}


def symbol_layout(size, extended, pp, l, closing=False):
    """Kept per (size, mode, pattern, l mod Dy, closing): see ``_symbol_layout``."""
    key = (size, extended, pp, l % T.DXY[pp][1], closing)
    if key not in _LAYOUT:
        _LAYOUT[key] = _symbol_layout(size, extended, pp, l, closing)
    return _LAYOUT[key]


def _symbol_layout(size, extended, pp, l, closing=False):
    """``(pilots, values' amplitude per pilot, data carriers)`` of data symbol ``l`` (or the
    frame closing symbol): scattered, continual and edge pilots (EN 9.2.3-9.2.5), or the
    frame closing pilots, every Dx-th carrier and the edges (EN 9.2.7)."""
    km = iqt2.kmax(size, extended)
    k = np.arange(km + 1)
    amp = np.zeros(km + 1)
    dx, _ = T.DXY[pp]
    if closing:
        amp[k % dx == 0] = T.ASP[pp]
        if (size, pp) in ((1024, 4), (1024, 5), (2048, 7)):
            amp[km - 1] = T.ASP[pp]                              # EN 9.2.7.1: an extra one at Kmax - 1
    else:
        amp[continual_pilots(size, extended, pp)] = T.ACP[size]
        amp[scattered(size, extended, pp, l)] = T.ASP[pp]           # a scattered pilot's boost wins
    amp[0] = amp[km] = T.ASP[pp]
    pilots = np.flatnonzero(amp > 0)
    return pilots, amp[pilots], np.flatnonzero(amp == 0)


def pilot_values(size, extended, pilots, amps, l):
    """2 A (1/2 - r(l, k)) (EN 9.2.3.3, 9.2.4.4, 9.2.2)."""
    km = iqt2.kmax(size, extended)
    w = iqt2.prbs_w(km + 1 + iqt2.KEXT[size])
    off = 0 if extended else iqt2.KEXT[size]
    r = w[pilots + off] ^ iqt2.pn_bits(l + 1)[l]
    return amps * (1.0 - 2.0 * r)


# ------------------------------------------------------------------ one T2-frame to cells

def frame_cells(y, fs, l1, size, guard, extended, p1_start, t_s=iqt2.T_8MHZ):
    """The frame's data cells in frame-builder order, after L1 (address 0, EN 8.3.6.2), and
    each cell's noise: ``(cells, noise, info)``. ``l1`` is ``iqt2.read_l1``'s result."""
    pre = l1['pre']
    pp = pre['PILOT_PATTERN'] + 1
    ldata = pre['NUM_DATA_SYMBOLS']
    np2 = iqt2.NP2[size]
    lf = np2 + ldata
    closing = pp in T.FRAME_CLOSING.get((size, guard), ())
    n = size
    ng = int(round(size * iqt2.GUARD_FRACTION[guard]))
    start = l1['start']                                  # P2's useful part
    cfo = l1['freq_offset_hz'] - l1['carrier_shift'] * fs / size
    shift = l1['carrier_shift']
    km = iqt2.kmax(size, extended)
    centre = km // 2
    allk = np.arange(km + 1)
    end = start + (lf - 1) * (n + ng) + n
    if end > len(y):
        raise ValueError('the frame runs past the samples')
    # every symbol's spectrum on the carriers
    spec = np.empty((lf, km + 1), complex)
    for l in range(lf):
        a = start + l * (n + ng)
        idx = np.arange(a, a + n)
        s = np.fft.fft(y[a:a + n] * np.exp(-2j * np.pi * cfo * idx / fs))
        spec[l] = s[(allk - centre + shift) % n]
    # each data symbol's common phase against the first, from its continual pilots
    cps = continual_pilots(size, extended, pp)
    w = iqt2.prbs_w(km + 1 + iqt2.KEXT[size])
    wcp = 1.0 - 2.0 * w[cps + (0 if extended else iqt2.KEXT[size])]
    pn = iqt2.pn_bits(lf)
    last_normal = lf - 2 if closing else lf - 1
    ref = spec[np2, cps] * wcp * (1 - 2.0 * pn[np2])
    phase = np.zeros(lf)
    for l in range(np2, last_normal + 1):
        z = spec[l, cps] * wcp * (1 - 2.0 * pn[l])
        phase[l] = np.angle(np.sum(z * np.conj(ref)))
    # P2 and the closing symbol: the phases' straight line
    ls = np.arange(np2, last_normal + 1)
    unwrapped = np.unwrap(phase[ls])
    fit = np.polyfit(ls, unwrapped, 1) if len(ls) > 1 else (0.0, unwrapped[0])
    phase[ls] = unwrapped
    if closing:
        phase[lf - 1] = np.polyval(fit, lf - 1)
    spec[np2:] *= np.exp(-1j * phase[np2:])[:, None]
    # scattered (and edge) pilots' channel per data symbol, on the comb of every Dx-th carrier
    dx, dy = T.DXY[pp]
    off = iqt2.KEXT[size] if extended else 0
    comb = np.flatnonzero((allk - off) % dx == 0)
    if comb[-1] != km:
        comb = np.append(comb, km)
    hcomb = np.full((lf, len(comb)), np.nan + 0j)
    for l in range(np2, last_normal + 1):
        sp = np.union1d(scattered(size, extended, pp, l), [0, km])
        vals = pilot_values(size, extended, sp, np.full(len(sp), T.ASP[pp]), l)
        at = np.searchsorted(comb, sp)
        hcomb[l, at] = spec[l, sp] / vals
    # time interpolation along each comb carrier between the symbols that carry it
    for c in range(len(comb)):
        col = hcomb[np2:last_normal + 1, c]
        have = np.flatnonzero(~np.isnan(col.real))
        if len(have) == 0:
            continue
        xs = np.arange(len(col))
        hcomb[np2:last_normal + 1, c] = (np.interp(xs, have, col[have].real)
                                         + 1j * np.interp(xs, have, col[have].imag))
    out_cells, out_noise = [], []
    # P2: iqt2 equalises it; its cells after L1
    p2c, p2n, _ = iqt2.p2_cells(y, fs, size, guard, extended, p1_start, freq_offset=cfo, t_s=t_s)
    x2 = iqt2.deinterleave(p2c, size)
    n2 = iqt2.deinterleave(p2n, size)
    dl1 = 1840 + pre['L1_POST_SIZE']
    out_cells.append(x2[dl1:])
    out_noise.append(n2[dl1:])
    guardf = iqt2.GUARD_FRACTION[guard]
    noise_prev = None
    for l in range(np2, lf):
        is_closing = closing and l == lf - 1
        pilots, amps, data = symbol_layout(size, extended, pp, l, is_closing)
        if is_closing:
            vals = pilot_values(size, extended, pilots, amps, l)
            hp = spec[l, pilots] / vals
            grid = np.isin(pilots, comb)
            h_on = np.full(len(comb), np.nan + 0j)
            h_on[np.searchsorted(comb, pilots[grid])] = hp[grid]
            hrow = h_on
        else:
            hrow = hcomb[l]
        hs, _ = iqt2.smooth_channel(hrow, comb, guardf)
        gaps = np.diff(comb)
        slope = np.angle(np.sum(hs[1:] * np.conj(hs[:-1]) * (gaps == dx))) / dx
        flat = hs * np.exp(-1j * slope * comb)
        h = (np.interp(allk, comb, flat.real) + 1j * np.interp(allk, comb, flat.imag)) * np.exp(1j * slope * allk)
        eq = spec[l] / h
        power = float(np.mean(np.abs(h) ** 2))
        if not is_closing:
            # the continual pilots that are not also scattered ones this symbol (those take the
            # scattered pilots' boost, EN 9.2.4.3, and are in the channel estimate)
            only = np.setdiff1d(cps, scattered(size, extended, pp, l))
            cpv = pilot_values(size, extended, only, np.full(len(only), T.ACP[size]), l)
            resid = np.abs(eq[only] - cpv) ** 2
            cp_noise = float(np.mean(resid * np.abs(h[only]) ** 2 / power))
            noise_prev = max(cp_noise, 1e-5)
        cell_noise = (noise_prev or 1e-2) * power / np.maximum(np.abs(h[data]) ** 2, 1e-3 * power)
        nd = len(data)
        want = (T.NFC if is_closing else T.CDATA)[(size, extended)][pp]
        if nd != want:
            raise ValueError(f'symbol {l}: {nd} data cells, the standard says {want}')
        odd = l % 2 == 1
        xc = iqt2.deinterleave(eq[data], size, odd)
        xn = iqt2.deinterleave(cell_noise, size, odd)
        if is_closing:
            cfc = T.CFC[(size, extended)][pp]
            xc, xn = xc[:cfc], xn[:cfc]
        out_cells.append(xc)
        out_noise.append(xn)
    return (np.concatenate(out_cells), np.concatenate(out_noise),
            {'symbols': lf, 'closing': closing, 'phase_drift_rad': float(fit[0]) if len(ls) > 1 else 0.0})


# ------------------------------------------------------------------ PLP cells to soft bits

def ti_blocks(nblocks, nti):
    """N_FEC_TI(n, s) for each TI-block, the smaller first (EN 6.5.2)."""
    if nti <= 1:
        return [nblocks]
    lo, extra = divmod(nblocks, nti)
    return [lo + (1 if s >= nti - extra else 0) for s in range(nti)]


def time_deinterleave(cells, ncells, nfec):
    """One TI-block back to FEC-block order: written column-wise into Ncells / 5 rows by
    5 N_FEC columns, read row-wise (EN 6.5.3)."""
    nr, nc = ncells // 5, 5 * nfec
    return np.asarray(cells).reshape(nr, nc).T.reshape(-1)


_CELL_PERM = {}


def cell_permutation(ncells, r):
    """L_r(q) = (L_0(q) + P(r)) mod Ncells (EN 6.4)."""
    nd = int(math.ceil(math.log2(ncells)))
    if ncells not in _CELL_PERM:
        taps = {11: (0, 3), 12: (0, 2), 13: (0, 1, 4, 6), 14: (0, 1, 4, 5, 9, 11), 15: (0, 1, 2, 12)}[nd]
        reg = np.zeros(nd - 1, np.uint8)
        out = []
        for i in range(2 ** nd):
            if i == 2:
                reg[:] = 0
                reg[0] = 1
            elif i > 2:
                fb = 0
                for tp in taps:
                    fb ^= reg[tp]
                reg[:-1] = reg[1:].copy()
                reg[-1] = fb
            v = (i % 2) << (nd - 1)
            for j in range(nd - 1):
                if reg[j]:
                    v |= 1 << j
            if v < ncells:
                out.append(v)
        _CELL_PERM[ncells] = np.array(out[:ncells])
    k = 0
    shifts = []
    while len(shifts) <= r:
        p = ncells
        while p >= ncells:
            p = int(format(k, f'0{nd}b')[::-1], 2)
            k += 1
        shifts.append(p)
    return (_CELL_PERM[ncells] + shifts[r]) % ncells


def plp_llrs(cells, noise, plp, nblocks):
    """The PLP's FEC blocks' soft bits, in LDPC codeword order: ``(nblocks, Nldpc)`` LLRs
    (positive means 0), from its ``nblocks`` x Ncells cells (EN 6.5, 6.4, 6.3, 6.2, 6.1.3)."""
    bits = BITS[plp['PLP_MOD']]
    nldpc = 64800 if plp['PLP_FEC_TYPE'] == 1 else 16200
    ncells = T.NCELLS[(nldpc, bits)]
    tl = plp['TIME_IL_LENGTH']
    nti = tl if (plp['TIME_IL_TYPE'] == 0 and tl > 0) else 1
    sizes = ti_blocks(nblocks, nti) if tl > 0 else [nblocks]
    fec_cells, fec_noise = [], []
    at = 0
    for nfec in sizes:
        blk = cells[at:at + nfec * ncells]
        bn = noise[at:at + nfec * ncells]
        at += nfec * ncells
        if tl > 0:
            blk, bn = time_deinterleave(blk, ncells, nfec), time_deinterleave(bn, ncells, nfec)
        for r in range(nfec):
            perm = cell_permutation(ncells, r if tl > 0 else 0)
            d, dn = blk[r * ncells:(r + 1) * ncells], bn[r * ncells:(r + 1) * ncells]
            fec_cells.append(d[perm])                      # g_q = d_{L_r(q)}
            fec_noise.append(dn[perm])
    g = np.array(fec_cells)
    gn = np.array(fec_noise)
    if plp['PLP_ROTATION']:
        # g_q = Re(R f_q) + j Im(R f_{q-1}): Im(R f_q) is in cell q + 1 (cyclically)
        z = g.real + 1j * np.roll(g.imag, -1, axis=1)
        zn = 0.5 * (gn + np.roll(gn, -1, axis=1))
        g = z * np.exp(-1j * math.radians(T.ROTATION_DEG[bits]))
        gn = zn
    llr = iqt2.demap_llr(g.reshape(-1), bits, gn.reshape(-1)).reshape(len(g), ncells, bits)
    return deinterleave_bits(llr, plp, nldpc, bits)


def deinterleave_bits(y, plp, nldpc, bits):
    """Cell words' soft bits (blocks, cells, bits) back to codeword order (EN 6.2.1, 6.1.3)."""
    rate = plp['PLP_COD']
    nb = len(y)
    if bits == 2:
        nsub = 2
        b = y.reshape(nb, -1, nsub)
    elif bits == 8 and nldpc == 16200:
        nsub = 8
        b = y.reshape(nb, -1, nsub)
    else:
        nsub = 2 * bits
        b = np.concatenate([y[:, 0::2, :], y[:, 1::2, :]], axis=2)     # (blocks, cells / 2, nsub)
    demux = (T.DEMUX_35 if rate == 1 else T.DEMUX_23 if rate == 2 else T.DEMUX_A)[(bits, nldpc)]
    v = b[:, :, list(demux)].reshape(nb, -1)                           # v[di] = b[e(di mod nsub)][di div nsub]
    if bits == 2:
        u = v
    else:
        twist = T.TWIST[(bits, nldpc)]
        nc = len(twist)
        nr = nldpc // nc
        # u_i went to column c = i div Nr, row (i + t_c) mod Nr; v_j is row j div Nc, column j mod Nc
        i = np.arange(nldpc)
        c = i // nr
        r = (i + np.array(twist)[c]) % nr
        u = v[:, r * nc + c]
    # parity de-interleaving: u[K + 360 t + s] = lambda[K + Q s + t]
    kldpc = T.KLDPC_NORMAL[rate] if nldpc == 64800 else None
    q = T.Q_NORMAL[rate] if nldpc == 64800 else None
    if kldpc is None:
        raise NotImplementedError('16K data FEC blocks: not yet')
    lam = u.copy()
    if bits != 2:
        t = np.arange(q)[:, None]
        s = np.arange(360)[None, :]
        lam[:, kldpc + (q * s + t).reshape(-1)] = u[:, kldpc + (360 * t + s).reshape(-1)]
    return lam


# ------------------------------------------------------------------ LDPC, many codewords at once

_BATCH_LAYERS = {}


def _batch_layers(table, q, kldpc, n):
    """Layers of checks c mod Q, each split so that no variable appears twice (an information
    bit can reach two checks of one layer: rows whose addresses share a residue mod Q)."""
    key = (id(table), q, kldpc, n)
    if key not in _BATCH_LAYERS:
        chk, var, m = iqt2.ldpc_graph(table, q, kldpc, n)
        layers = []
        for r in range(q):
            sel = np.flatnonzero(chk % q == r)
            groups, used = [], []
            # greedy: each check's edges join the first sub-layer that has none of its variables
            order = np.argsort(chk[sel], kind='stable')
            sel = sel[order]
            cs = chk[sel]
            starts = np.flatnonzero(np.r_[True, cs[1:] != cs[:-1]])
            ends = np.r_[starts[1:], len(sel)]
            for a, b in zip(starts, ends):
                vs = set(var[sel[a:b]].tolist())
                for gi in range(len(groups)):
                    if not (used[gi] & vs):
                        groups[gi].append(sel[a:b])
                        used[gi] |= vs
                        break
                else:
                    groups.append([sel[a:b]])
                    used.append(set(vs))
            for gr in groups:
                e = np.concatenate(gr)
                c = chk[e]
                st = np.flatnonzero(np.r_[True, c[1:] != c[:-1]])
                row_of = np.repeat(np.arange(len(st)), np.diff(np.r_[st, len(e)]))
                layers.append((e, var[e], st, row_of))
        _BATCH_LAYERS[key] = (layers, chk, var)
    return _BATCH_LAYERS[key]


def ldpc_decode_batch(llr, table, q, kldpc, n=64800, iterations=30, alpha=0.8):
    """``(bits, iterations used, ok per codeword)`` for many codewords (rows of ``llr``):
    layered normalised min-sum, all rows together; a row stops changing once its checks
    hold."""
    layers, chk, var = _batch_layers(table, q, kldpc, n)
    starts_all = np.flatnonzero(np.r_[True, chk[1:] != chk[:-1]])
    total = np.asarray(llr, float).copy()
    nb = len(total)
    c2v = np.zeros((nb, len(chk)))
    active = np.ones(nb, bool)
    ok = np.zeros(nb, bool)
    used = 0
    for it in range(1, iterations + 1):
        rows = np.flatnonzero(active)
        if len(rows) == 0:
            break
        used = it
        t = total[rows]
        cv = c2v[rows]
        for e, v, st, row_of in layers:
            old = cv[:, e]
            v2c = t[:, v] - old
            mag = np.abs(v2c)
            neg = v2c < 0
            parity = np.add.reduceat(neg.astype(np.int8), st, axis=1) % 2
            min1 = np.minimum.reduceat(mag, st, axis=1)
            m1 = min1[:, row_of]
            is_min = mag == m1
            # the first edge at the minimum, per check (cumulative count along each check)
            cnt = np.cumsum(is_min, axis=1)
            base = np.concatenate([np.zeros((len(rows), 1), int), cnt[:, st[1:] - 1]], axis=1)
            first = is_min & (cnt - base[:, row_of] == 1)
            min2 = np.minimum.reduceat(np.where(first, np.inf, mag), st, axis=1)
            outm = np.where(first, min2[:, row_of], m1)
            outm = np.minimum(outm, 1e30)
            new = alpha * outm * np.where(neg ^ (parity[:, row_of] == 1), -1.0, 1.0)
            cv[:, e] = new
            t[:, v] = v2c + new
        total[rows] = t
        c2v[rows] = cv
        hard = (t < 0)
        bad = np.any(np.add.reduceat(hard[:, var].astype(np.int8), starts_all, axis=1) % 2, axis=1)
        ok[rows[~bad]] = True
        active[rows[~bad]] = False
    return (total < 0).astype(np.uint8), used, ok


# ------------------------------------------------------------------ BBFRAMEs to TS (EN 5)

def crc8(bits):
    """Annex F's CRC-8 (G8 = x^8 + x^7 + x^6 + x^4 + x^2 + 1), register zero, MSB first."""
    reg = 0
    for b in bits:
        top = (reg >> 7) & 1
        reg = (reg << 1) & 0xFF
        if top ^ int(b):
            reg ^= 0xD5
    return reg


def bbheader(bits):
    """The BBHEADER's fields (EN 5.1.7) from a descrambled BBFRAME's first 80 bits, with
    ``crc_ok`` and ``mode`` (0 Normal, 1 High Efficiency) from its CRC-8 / MODE byte."""
    by = np.packbits(bits[:80]).tolist()
    m1 = by[0]
    crc = crc8(bits[:72])
    mode = by[9] ^ crc
    return {'ts_gs': m1 >> 6, 'sis': (m1 >> 5) & 1, 'ccm': (m1 >> 4) & 1, 'issyi': (m1 >> 3) & 1,
            'npd': (m1 >> 2) & 1, 'isi': by[1], 'upl': (by[2] << 8) | by[3], 'dfl': (by[4] << 8) | by[5],
            'sync': by[6], 'syncd': (by[7] << 8) | by[8], 'mode': mode, 'crc_ok': mode in (0, 1)}


class TsRebuilder:
    """TS packets from successive BBFRAMEs' data fields (EN 5.1.8, 5.1.5): High Efficiency
    Mode sends each packet without its sync byte (187 bytes, then DNP when null packets are
    deleted); Normal Mode sends the CRC-8 of the previous packet in the sync byte's place.
    SYNCD points to the first packet that starts in the data field; a frame that fails its
    CRC-8 drops the partial packet carried over."""

    def __init__(self):
        self.carry = None                         # bytes of a packet in progress, or None (lost)
        self.packets = 0
        self.nulls = 0
        self.errored = 0                          # packets marked with transport_error_indicator
        self.good = True                          # the current BBFRAME's codeword converged

    def feed(self, frame_bits, kbch, good=True):
        """The packets completed by this BBFRAME. ``good`` False (its LDPC codeword did not
        converge) sets their transport_error_indicator, so a demultiplexer knows."""
        self.good = good
        h = bbheader(frame_bits)
        if not h['crc_ok'] or h['ts_gs'] != 3:
            self.carry = None
            return b'', h
        field = frame_bits[80:80 + h['dfl']]
        field = field[:len(field) // 8 * 8]
        data = np.packbits(field).tobytes()
        if h['mode'] == 1:
            uplen = 187 + (1 if h['npd'] else 0)
            lead = 0
        else:
            uplen = h['upl'] // 8
            lead = 1                                # the previous packet's CRC-8
        out = bytearray()
        syncd = h['syncd']
        if syncd == 0xFFFF:
            if self.carry is not None:
                self.carry += data
            return b'', h
        first = syncd // 8
        if self.carry is not None:
            head = self.carry + data[:first]
            if len(head) == uplen:
                out += self._packet(head, h, lead)
        pos = first
        while pos + uplen <= len(data):
            out += self._packet(data[pos:pos + uplen], h, lead)
            pos += uplen
        self.carry = bytearray(data[pos:])
        return bytes(out), h

    def _packet(self, up, h, lead):
        body = up[lead:lead + 187]
        out = bytearray()
        if h['npd']:
            dnp = up[-1]
            null = b'\x47\x1f\xff\x10' + b'\xff' * 184
            out += null * dnp
            self.nulls += dnp
        hdr = bytearray(body[:1])
        if not self.good and hdr:
            hdr[0] |= 0x80                          # transport_error_indicator
            self.errored += 1
        out += b'\x47' + bytes(hdr) + bytes(body[1:])
        self.packets += 1
        return out


def decode_bbframes(y, fs, l1, size, guard, extended, p1_start, plp_id=None, iterations=None,
                    t_s=iqt2.T_8MHZ, native=None, threads=None):
    """One T2-frame's PLP to its BBFRAMEs: ``(frames, ok, info)``, ``frames`` (blocks, Kbch)
    bits descrambled (EN 5.2.4), ``ok`` whether each codeword's LDPC checks all held. No state
    from frame to frame (``decode_frame`` adds the TsRebuilder), so frames can be decoded in
    any order, in other processes (``iqt2_live``). ``native``: the LDPC in native code
    (``iqt2_native``; None: when it is built), else NumPy's; ``iterations`` defaults to 50
    native, 30 NumPy."""
    post = l1['post']
    plps = post['configurable']['PLP']
    dyn = {p['PLP_ID']: p for p in post['dynamic']['PLP']}
    found = [p for p in plps if plp_id is None or p['PLP_ID'] == plp_id]
    if not found:
        raise ValueError(f'no PLP {plp_id} (there are {[p["PLP_ID"] for p in plps]})')
    plp = found[0]
    if plp['TIME_IL_TYPE'] == 1 or plp['FRAME_INTERVAL'] > 1:
        raise ValueError('a PLP interleaved over several T2-frames (TIME_IL_TYPE 1 or FRAME_INTERVAL > 1): not yet')
    if plp['PLP_FEC_TYPE'] != 1:
        raise ValueError('16K FEC blocks for data: not yet')
    if plp['PLP_TYPE'] == 2:
        raise ValueError('type 2 PLPs (sub-slices): not yet')
    d = dyn[plp['PLP_ID']]
    bits = BITS[plp['PLP_MOD']]
    nldpc = 64800 if plp['PLP_FEC_TYPE'] == 1 else 16200
    ncells = T.NCELLS[(nldpc, bits)]
    cells, noise, finfo = frame_cells(y, fs, l1, size, guard, extended, p1_start, t_s)
    a = d['PLP_START']
    nblocks = d['PLP_NUM_BLOCKS']
    span = cells[a:a + nblocks * ncells]
    if len(span) < nblocks * ncells:
        raise ValueError('the frame holds fewer cells than the PLP signals')
    llr = plp_llrs(span, noise[a:a + nblocks * ncells], plp, nblocks)
    rate = plp['PLP_COD']
    kldpc = T.KLDPC_NORMAL[rate]
    if native is None:
        native = iqt2_native.available()
    if native:
        hard, its, ok = iqt2_native.decode(llr, rate, trials=iterations or iqt2_native.TRIALS, threads=threads)
    else:
        hard, its, ok = ldpc_decode_batch(llr, T.LDPC_A[rate], T.Q_NORMAL[rate], kldpc, iterations=iterations or 30)
    kbch = iqt2.KBCH[plp['PLP_FEC_TYPE']][rate]
    frames = hard[:, :kbch] ^ iqt2.bb_scramble(kbch)
    return frames, ok, {'blocks': nblocks, 'ldpc_ok': int(ok.sum()), 'ldpc_iterations': its,
                        'ldpc': 'native' if native else 'numpy', 'kbch': kbch, 'frame': finfo}


def feed_bbframes(ts, frames, ok, kbch):
    """BBFRAMEs (``decode_bbframes``) through a TsRebuilder: ``(ts_bytes, BBHEADERs whose
    CRC-8 held)``."""
    out = bytearray()
    headers = 0
    for row, good in zip(frames, ok):
        chunk, h = ts.feed(row, kbch, bool(good))
        headers += h['crc_ok']
        out += chunk
    return bytes(out), headers


def decode_frame(y, fs, l1, size, guard, extended, p1_start, plp_id=None, ts=None, iterations=None,
                 t_s=iqt2.T_8MHZ, native=None):
    """One T2-frame's PLP to TS bytes: ``(ts_bytes, info)``. ``ts`` is a ``TsRebuilder``
    carried from frame to frame. ``native`` and ``iterations`` as ``decode_bbframes``."""
    frames, ok, info = decode_bbframes(y, fs, l1, size, guard, extended, p1_start, plp_id, iterations,
                                       t_s, native)
    data, headers = feed_bbframes(ts or TsRebuilder(), frames, ok, info['kbch'])
    info['bbheaders_ok'] = headers
    return data, info
