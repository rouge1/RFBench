"""DVB-T2 front end and L1 signalling, numpy only (roadmap Round 32, phase 2).

Written from ETSI EN 302 755 V1.4.1 (2015-07); the clause or table each piece comes from
is named beside it, and the tables copied from the standard are pinned by a test
(``test_iqt2.py``). No code from any other receiver.

What it does, for one T2-frame of a recording whose FFT size and guard are known (from
``iqmeasure``'s ``dvbt`` card, which classifies first):

1. P2's position: P1 (2048 T, found as ``iqmeasure._p1_metric`` finds it), then the
   first P2 symbol, its guard and its useful part (EN 8.3, 9.5, 9.7, 9.8).
2. Fine frequency from the guard's correlation, and the integer carrier offset from
   the P2 pilots, which are every third carrier (every sixth in 32k SISO) plus the
   extended edges, valued by the reference sequence (EN 9.2.2, 9.2.6).
3. The channel from those pilots, interpolated in frequency; the data cells are the
   carriers that are neither pilots nor P2's reserved tones (Table H.1, EN 9.3, 9.4);
   CP2 of them (Table 47).
4. Frequency de-interleaving (EN 8.5, Tables 52, 53).
5. L1-pre: the first 1840 cells, BPSK; shortened BCH (Kbch 3072) and the 16K LDPC with
   code identifier 1/4, punctured by 11 488 (EN 7.3.1.1, 7.3.2, Tables 39, 40, 42; LDPC
   addresses Annex B, Table B.1). Its 200 bits hold a CRC-32 (Annex F), which is the
   check: a field is reported only when the CRC holds.
6. L1-post: the next L1_POST_SIZE cells in L1_MOD; demapped, de-multiplexed (Table 13),
   bit de-interleaved (Table 44), then BCH (Kbch 7032) and the 16K LDPC with identifier
   1/2 (Table B.2), shortened and punctured by EN 7.3.1.2's steps (Tables 41, 43),
   descrambled when L1_POST_SCRAMBLED and T2_VERSION is 2 or more (EN 7.3.2.1, 7.2.2);
   its CRC-32 is the check.

BCH is not decoded: the LDPC corrects, and the CRC-32 says whether it did.

Only one P2 symbol is handled (16k and 32k, NP2 = 1, Table 51): the zig-zag over
several P2 symbols (EN 8.3.5) comes with the smaller FFT sizes.
"""

import functools
import math

import numpy as np

# ------------------------------------------------------------------ OFDM (EN 9.5, Table 66)

#: Elementary period T for an 8 MHz channel (Table 65).
T_8MHZ = 7 / 64e6
#: Carriers Kmax in normal carrier mode, and Kext (added each side in extended mode).
KMAX_NORMAL = {1024: 852, 2048: 1704, 4096: 3408, 8192: 6816, 16384: 13632, 32768: 27264}
KEXT = {1024: 0, 2048: 0, 4096: 0, 8192: 48, 16384: 144, 32768: 288}
#: Active cells CP2 in one P2 symbol, SISO (Table 47).
CP2 = {1024: 558, 2048: 1118, 4096: 2236, 8192: 4472, 16384: 8944, 32768: 22432}
#: P2 symbols per frame (Table 51).
NP2 = {1024: 16, 2048: 8, 4096: 4, 8192: 2, 16384: 1, 32768: 1}
#: P2 pilot amplitude AP2 (Table 63: sqrt(37)/5 in 32k SISO, sqrt(31)/5 otherwise).
AP2 = {32768: math.sqrt(37) / 5}
AP2_OTHER = math.sqrt(31) / 5
#: Guard intervals by L1-pre's GUARD_INTERVAL value (Table 22).
GUARDS = {0: '1/32', 1: '1/16', 2: '1/8', 3: '1/4', 4: '1/128', 5: '19/128', 6: '19/256'}
GUARD_FRACTION = {'1/32': 1 / 32, '1/16': 1 / 16, '1/8': 1 / 8, '1/4': 1 / 4, '1/128': 1 / 128,
                  '19/128': 19 / 128, '19/256': 19 / 256}

# Table H.1: the reserved carriers of the P2 symbol(s), normal carrier mode; plus Kext in
# extended mode (EN 9.3).
P2_RESERVED = {
    1024: (116, 130, 134, 157, 182, 256, 346, 478, 479, 532),
    2048: (113, 124, 262, 467, 479, 727, 803, 862, 910, 946, 980, 1201, 1322, 1342, 1396, 1397, 1562, 1565),
    4096: (104, 116, 119, 163, 170, 173, 664, 886, 1064, 1151, 1196, 1264, 1531, 1736, 1951, 1960, 2069, 2098,
        2311, 2366, 2473, 2552, 2584, 2585, 2645, 2774, 2846, 2882, 3004, 3034, 3107, 3127, 3148, 3191,
        3283, 3289),
    8192: (106, 109, 110, 112, 115, 118, 133, 142, 163, 184, 206, 247, 445, 461, 503, 565, 602, 656, 766, 800,
        922, 1094, 1108, 1199, 1258, 1726, 1793, 1939, 2128, 2714, 3185, 3365, 3541, 3655, 3770, 3863, 4066,
        4190, 4282, 4565, 4628, 4727, 4882, 4885, 5143, 5192, 5210, 5257, 5261, 5459, 5651, 5809, 5830, 5986,
        6020, 6076, 6253, 6269, 6410, 6436, 6467, 6475, 6509, 6556, 6611, 6674, 6685, 6689, 6691, 6695, 6698,
        6701),
    16384: (104, 106, 107, 109, 110, 112, 113, 115, 116, 118, 119, 121, 122, 125, 128, 131, 134, 137, 140, 143,
        161, 223, 230, 398, 482, 497, 733, 809, 850, 922, 962, 1196, 1256, 1262, 1559, 1691, 1801, 1819, 1937,
        2005, 2095, 2308, 2383, 2408, 2425, 2428, 2479, 2579, 2893, 2902, 3086, 3554, 4085, 4127, 4139, 4151,
        4163, 4373, 4400, 4576, 4609, 4952, 4961, 5444, 5756, 5800, 6094, 6208, 6658, 6673, 6799, 7208, 7682,
        8101, 8135, 8230, 8692, 8788, 8933, 9323, 9449, 9478, 9868, 10192, 10261, 10430, 10630, 10685, 10828,
        10915, 10930, 10942, 11053, 11185, 11324, 11369, 11468, 11507, 11542, 11561, 11794, 11912, 11974,
        11978, 12085, 12179, 12193, 12269, 12311, 12758, 12767, 12866, 12938, 12962, 12971, 13099, 13102,
        13105, 13120, 13150, 13280, 13282, 13309, 13312, 13321, 13381, 13402, 13448, 13456, 13462, 13463,
        13466, 13478, 13492, 13495, 13498, 13501, 13502, 13504, 13507, 13510, 13513, 13514, 13516),
    32768: (104, 106, 107, 109, 110, 112, 113, 115, 118, 121, 124, 127, 130, 133, 136, 139, 142, 145, 148, 151,
        154, 157, 160, 163, 166, 169, 172, 175, 178, 181, 184, 187, 190, 193, 196, 199, 202, 205, 208, 211,
        404, 452, 455, 467, 509, 539, 568, 650, 749, 1001, 1087, 1286, 1637, 1823, 1835, 1841, 1889, 1898,
        1901, 2111, 2225, 2252, 2279, 2309, 2315, 2428, 2452, 2497, 2519, 3109, 3154, 3160, 3170, 3193, 3214,
        3298, 3331, 3346, 3388, 3397, 3404, 3416, 3466, 3491, 3500, 3572, 4181, 4411, 4594, 4970, 5042, 5069,
        5081, 5086, 5095, 5104, 5320, 5465, 5491, 6193, 6541, 6778, 6853, 6928, 6934, 7030, 7198, 7351, 7712,
        7826, 7922, 8194, 8347, 8350, 8435, 8518, 8671, 8861, 8887, 9199, 9980, 10031, 10240, 10519, 10537,
        10573, 10589, 11078, 11278, 11324, 11489, 11642, 12034, 12107, 12184, 12295, 12635, 12643, 12941,
        12995, 13001, 13133, 13172, 13246, 13514, 13522, 13939, 14362, 14720, 14926, 15338, 15524, 15565,
        15662, 15775, 16358, 16613, 16688, 16760, 17003, 17267, 17596, 17705, 18157, 18272, 18715, 18994,
        19249, 19348, 20221, 20855, 21400, 21412, 21418, 21430, 21478, 21559, 21983, 21986, 22331, 22367,
        22370, 22402, 22447, 22535, 22567, 22571, 22660, 22780, 22802, 22844, 22888, 22907, 23021, 23057,
        23086, 23213, 23240, 23263, 23333, 23369, 23453, 23594, 24143, 24176, 24319, 24325, 24565, 24587,
        24641, 24965, 25067, 25094, 25142, 25331, 25379, 25465, 25553, 25589, 25594, 25655, 25664, 25807,
        25823, 25873, 25925, 25948, 26002, 26008, 26102, 26138, 26141, 26377, 26468, 26498, 26510, 26512,
        26578, 26579, 26588, 26594, 26597, 26608, 26627, 26642, 26767, 26776, 26800, 26876, 26882, 26900,
        26917, 26927, 26951, 26957, 26960, 26974, 26986, 27010, 27013, 27038, 27044, 27053, 27059, 27061,
        27074, 27076, 27083, 27086, 27092, 27094, 27098, 27103, 27110, 27115, 27118, 27119, 27125, 27128,
        27130, 27133, 27134, 27140, 27143, 27145, 27146, 27148, 27149),
}

# Table 56: the frame-level PN sequence (hexadecimal, MSB first; EN 9.2.2.2).
PN_HEX = ('4DC2AF7BD8C3C9A1E76C9A090AF1C3114F07FCA2808E9462E9AD7B712D6F4AC8A59BB069CC50BF1149927E6B'
          'B1C9FC8C18BB949B30CD09DDD749E704F57B41DEC7E7B176E12C5657432B51B0B812DF0E14887E24D80C97F09'
          '374AD76270E58FE1774B2781D8D3821E393F2EA0FFD4D24DE20C05D0BA1703D10E52D61E013D837AA62D007CC'
          '2FD76D23A3E125BDE8A9A7C02A98B70251C556F6341EBDECB801AAD5D9FB8CBEA80BB619096527A8C475B3D8'
          'DB28AF8543A00EC3480DFF1E2CDA9F985B523B879007AA5D0CE58D21B18631006617F6F769EB947F924EA5161E'
          'C2C0488B63ED7993BA8EF4E552FA32FC3F1BDB19923902BCBBE5DDABB824126E08459CA6CFA0267E5294A98C6'
          '32569791E60EF659AEE9518CDF08D87833690C1B79183ED127E53360CD86514859A28B5494F51AA4882419A25A2'
          'D01A5F47AA27301E79A5370CCB3E197F')

# Frequency interleaver (EN 8.5): the feedback taps of R' per FFT size, and Table 53's bit
# permutations, given as the R bit position of each R' bit position Nr-2 .. 0.
FI_TAPS = {1024: (0, 4), 2048: (0, 3), 4096: (0, 2), 8192: (0, 1, 4, 6), 16384: (0, 1, 4, 5, 9, 11),
           32768: (0, 1, 2, 12)}
FI_PERM = {
    1024: ((4, 3, 2, 1, 0, 5, 6, 7, 8), (3, 2, 5, 0, 1, 4, 7, 8, 6)),
    2048: ((0, 7, 5, 1, 8, 2, 6, 9, 3, 4), (3, 2, 7, 0, 1, 5, 8, 4, 9, 6)),
    4096: ((7, 10, 5, 8, 1, 2, 4, 9, 0, 3, 6), (6, 2, 7, 10, 8, 0, 3, 4, 1, 9, 5)),
    8192: ((5, 11, 3, 0, 10, 8, 6, 9, 2, 4, 1, 7), (8, 10, 7, 6, 0, 5, 2, 1, 3, 9, 4, 11)),
    16384: ((8, 4, 3, 2, 0, 11, 1, 5, 12, 10, 6, 7, 9), (7, 9, 5, 3, 11, 1, 4, 0, 2, 12, 10, 8, 6)),
    32768: ((6, 5, 0, 10, 8, 1, 11, 12, 2, 9, 4, 3, 13, 7),),
}

# ------------------------------------------------------------------ L1 coding (EN 7.3)

#: Annex B, Table B.1 (code identifier 1/4) and B.2 (1/2): the parity accumulator
#: addresses of the first bit of each group of 360 information bits; and Q (Table 8(b)).
LDPC_B1 = ((6295, 9626, 304, 7695, 4839, 4936, 1660, 144, 11203, 5567, 6347, 12557),
           (10691, 4988, 3859, 3734, 3071, 3494, 7687, 10313, 5964, 8069, 8296, 11090),
           (10774, 3613, 5208, 11177, 7676, 3549, 8746, 6583, 7239, 12265, 2674, 4292),
           (11869, 3708, 5981, 8718, 4908, 10650, 6805, 3334, 2627, 10461, 9285, 11120),
           (7844, 3079, 10773), (3385, 10854, 5747), (1360, 12010, 12202), (6189, 4241, 2343),
           (9840, 12726, 4977))
LDPC_B2 = ((20, 712, 2386, 6354, 4061, 1062, 5045, 5158), (21, 2543, 5748, 4822, 2348, 3089, 6328, 5876),
           (22, 926, 5701, 269, 3693, 2438, 3190, 3507), (23, 2802, 4520, 3577, 5324, 1091, 4667, 4449),
           (24, 5140, 2003, 1263, 4742, 6497, 1185, 6202), (0, 4046, 6934), (1, 2855, 66), (2, 6694, 212),
           (3, 3439, 1158), (4, 3850, 4422), (5, 5924, 290), (6, 1467, 4049), (7, 7820, 2242),
           (8, 4606, 3080), (9, 4633, 7877), (10, 3884, 6868), (11, 8935, 4996), (12, 3028, 764),
           (13, 5988, 1057), (14, 7411, 3450))
N16K = 16200
L1_PRE = dict(table=LDPC_B1, q=36, kldpc=3240, kbch=3072, ksig=200, npunc=11488,
              pad=(7, 3, 6, 5, 2, 4, 1, 8, 0),                                         # Table 40
              punc=(27, 13, 29, 32, 5, 0, 11, 21, 33, 20, 25, 28, 18, 35, 8, 3, 9, 31,  # Table 42
                    22, 24, 7, 14, 17, 4, 2, 26, 16, 34, 19, 10, 12, 23, 1, 6, 30, 15))
#: L1-post (Kbch 7032, Kldpc 7200); Tables 41 and 43 by modulation order (1 BPSK, 2 QPSK, 4, 6).
L1_POST_PAD = {1: (18, 17, 16, 15, 14, 13, 12, 11, 4, 10, 9, 8, 3, 2, 7, 6, 5, 1, 19, 0),
               4: (18, 17, 16, 15, 14, 13, 12, 11, 4, 10, 9, 8, 7, 3, 2, 1, 6, 5, 19, 0),
               6: (18, 17, 16, 4, 15, 14, 13, 12, 3, 11, 10, 9, 2, 8, 7, 1, 6, 5, 19, 0)}
L1_POST_PUNC = {1: (6, 4, 18, 9, 13, 8, 15, 20, 5, 17, 2, 24, 10, 22, 12, 3, 16, 23, 1, 14, 0, 21, 19, 7, 11),
                4: (6, 4, 13, 9, 18, 8, 15, 20, 5, 17, 2, 22, 24, 7, 12, 1, 16, 23, 14, 0, 21, 10, 19, 11, 3),
                6: (6, 15, 13, 10, 3, 17, 21, 8, 5, 19, 2, 23, 16, 24, 7, 18, 1, 12, 20, 0, 4, 14, 9, 11, 22)}
L1_POST_PAD[2], L1_POST_PUNC[2] = L1_POST_PAD[1], L1_POST_PUNC[1]
#: L1_MOD (Table 24): the modulation order.
L1_MOD = {0: 1, 1: 2, 2: 4, 3: 6}
#: Table 13(a): the substream e of input bit di mod Nsubstreams (QPSK, 16-QAM, 64-QAM).
DEMUX = {2: (0, 1), 4: (7, 1, 4, 2, 5, 3, 6, 0), 6: (11, 7, 3, 10, 6, 2, 9, 5, 1, 8, 4, 0)}

#: Figure 25: the L1-pre fields and their widths.
L1_PRE_FIELDS = (('TYPE', 8), ('BWT_EXT', 1), ('S1', 3), ('S2', 4), ('L1_REPETITION_FLAG', 1),
                 ('GUARD_INTERVAL', 3), ('PAPR', 4), ('L1_MOD', 4), ('L1_COD', 2), ('L1_FEC_TYPE', 2),
                 ('L1_POST_SIZE', 18), ('L1_POST_INFO_SIZE', 18), ('PILOT_PATTERN', 4),
                 ('TX_ID_AVAILABILITY', 8), ('CELL_ID', 16), ('NETWORK_ID', 16), ('T2_SYSTEM_ID', 16),
                 ('NUM_T2_FRAMES', 8), ('NUM_DATA_SYMBOLS', 12), ('REGEN_FLAG', 3), ('L1_POST_EXTENSION', 1),
                 ('NUM_RF', 3), ('CURRENT_RF_IDX', 3), ('T2_VERSION', 4), ('L1_POST_SCRAMBLED', 1),
                 ('T2_BASE_LITE', 1), ('RESERVED', 4), ('CRC_32', 32))
#: Figure 27, one PLP of the configurable L1-post.
PLP_FIELDS = (('PLP_ID', 8), ('PLP_TYPE', 3), ('PLP_PAYLOAD_TYPE', 5), ('FF_FLAG', 1), ('FIRST_RF_IDX', 3),
              ('FIRST_FRAME_IDX', 8), ('PLP_GROUP_ID', 8), ('PLP_COD', 3), ('PLP_MOD', 3), ('PLP_ROTATION', 1),
              ('PLP_FEC_TYPE', 2), ('PLP_NUM_BLOCKS_MAX', 10), ('FRAME_INTERVAL', 8), ('TIME_IL_LENGTH', 8),
              ('TIME_IL_TYPE', 1), ('IN_BAND_A_FLAG', 1), ('IN_BAND_B_FLAG', 1), ('RESERVED_1', 11),
              ('PLP_MODE', 2), ('STATIC_FLAG', 1), ('STATIC_PADDING_FLAG', 1))
#: Kbch by code rate (Table 6(a), 64K; 6(b), 16K): a BBFRAME's bits, its 80-bit header included.
KBCH = {1: {0: 32208, 1: 38688, 2: 43040, 3: 48408, 4: 51648, 5: 53840},
        0: {0: 7032, 1: 9552, 2: 10632, 3: 11712, 4: 12432, 5: 13152}}
PLP_TYPE_NAMES = {0: 'common', 1: 'data type 1', 2: 'data type 2'}
T2_VERSION_NAMES = {0: '1.1.1', 1: '1.2.1', 2: '1.3.1'}
#: Table 32 (base profile), 33, 34, 57 (by PILOT_PATTERN, Table 27): for reading the fields.
PLP_COD_NAMES = {0: '1/2', 1: '3/5', 2: '2/3', 3: '3/4', 4: '4/5', 5: '5/6'}
PLP_MOD_NAMES = {0: 'QPSK', 1: '16QAM', 2: '64QAM', 3: '256QAM'}
PLP_FEC_NAMES = {0: '16K LDPC', 1: '64K LDPC'}
PILOT_NAMES = {i: f'PP{i + 1}' for i in range(8)}


# ------------------------------------------------------------------ sequences

_PRBS = np.ones(0, np.uint8)


def prbs_w(n):
    """The symbol-level PRBS w_0..w_{n-1} (EN 9.2.2.1): X^11 + X^2 + 1, all ones at the
    start, so w = 1 (eleven times), 0, 0, ... (kept once made: it is asked for every symbol)"""
    global _PRBS
    if len(_PRBS) < n:
        w = np.ones(n + 11, np.uint8)
        for i in range(11, n + 11):
            w[i] = w[i - 11] ^ w[i - 9]
        _PRBS = w[:n]
    return _PRBS[:n]


def pn_bits(n):
    """The frame-level PN chips pn_0..pn_{n-1} (Table 56, MSB of each hex digit first)."""
    bits = np.unpackbits(np.frombuffer(bytes.fromhex(PN_HEX), np.uint8))
    return bits[:n]


def crc32(bits):
    """Annex F's CRC-32 of a bit array (MSB first, register all ones, no final inversion)."""
    poly, reg = 0x04C11DB7, 0xFFFFFFFF
    for b in bits:
        top = (reg >> 31) & 1
        reg = (reg << 1) & 0xFFFFFFFF
        if top ^ int(b):
            reg ^= poly
    return reg


def _uint(bits):
    v = 0
    for b in bits:
        v = (v << 1) | int(b)
    return v


# ------------------------------------------------------------------ P2 layout

def kmax(size, extended):
    return KMAX_NORMAL[size] + (2 * KEXT[size] if extended else 0)


def p2_layout(size, extended, siso=True):
    """``(pilots, data)``: the carrier indices k of P2's pilots and of its data cells, in
    increasing k (EN 9.2.6.1, 9.3, 9.4). There are CP2 data cells (Table 47)."""
    km = kmax(size, extended)
    k = np.arange(km + 1)
    step = 6 if size == 32768 and siso else 3
    pilot = k % step == 0
    if extended:
        ext = KEXT[size]
        pilot |= (k < ext) | (k > km - ext)
    reserved = np.zeros(km + 1, bool)
    reserved[np.array(P2_RESERVED[size]) + (KEXT[size] if extended else 0)] = True
    data = ~pilot & ~reserved
    return np.flatnonzero(pilot), np.flatnonzero(data)


def p2_pilot_values(size, extended, pilots, l=0):
    """The P2 pilots' values: Re = 2 AP2 (1/2 - r(l, k)) (EN 9.2.6.3), r(l, k) = w(k [+ Kext
    in normal mode]) XOR pn(l) (EN 9.2.2)."""
    km = kmax(size, extended)
    w = prbs_w(km + 1 + KEXT[size])
    off = 0 if extended else KEXT[size]
    r = w[pilots + off] ^ pn_bits(l + 1)[l]
    return AP2.get(size, AP2_OTHER) * (1.0 - 2.0 * r)


_FREQ_PERM = {}


def freq_permutation(size, ndata, odd=False):
    """H(p) for p = 0..ndata-1 (EN 8.5): H0 for even symbols, H1 for odd (32k: its one H)."""
    key = (size, ndata, bool(odd and size != 32768))
    if key not in _FREQ_PERM:
        _FREQ_PERM[key] = _freq_permutation(size, ndata, odd)
    return _FREQ_PERM[key]


def _freq_permutation(size, ndata, odd=False):
    nr = int(math.log2(size))
    perm = FI_PERM[size][1 if odd and size != 32768 else 0]
    taps = FI_TAPS[size]
    reg = np.zeros(nr - 1, np.uint8)                      # R'[0..Nr-2]
    out = []
    for i in range(size):
        if i == 2:
            reg[:] = 0
            reg[0] = 1
        elif i > 2:
            fb = 0
            for t in taps:
                fb ^= reg[t]
            reg[:-1] = reg[1:].copy()
            reg[-1] = fb
        r = 0
        for src in range(nr - 1):                         # R'[src] -> R[perm position]
            if reg[src]:
                r |= 1 << perm[nr - 2 - src]
        h = (i % 2) * (1 << (nr - 1)) + r
        if h < ndata:
            out.append(h)
    return np.array(out[:ndata])


# ------------------------------------------------------------------ LDPC (EN 6.1.2, Annex B)

_GRAPHS = {}


def ldpc_graph(table, q, kldpc, n=N16K):
    """The parity-check edges (check, variable) of a 16K IRA code: information bit m of
    group i = m // 360 reaches checks (x + (m mod 360) q) mod (N - K) for each address x of
    the table's row i; parity bit p_j reaches checks j and j + 1 (the accumulator)."""
    key = (id(table), q, kldpc, n)
    if key not in _GRAPHS:
        m = n - kldpc
        chk, var = [], []
        for i, row in enumerate(table):
            j = np.arange(360)
            for x in row:
                chk.append((x + j * q) % m)
                var.append(360 * i + j)
        p = np.arange(m)
        chk += [p, p[1:]]
        var += [kldpc + p, kldpc + p[:-1]]
        chk, var = np.concatenate(chk), np.concatenate(var)
        order = np.argsort(chk, kind='stable')
        _GRAPHS[key] = (chk[order], var[order], m)
    return _GRAPHS[key]


def ldpc_encode(info, table, q, kldpc, n=N16K):
    """The codeword [info, parity] of ``kldpc`` information bits (EN 6.1.2.1)."""
    m = n - kldpc
    p = np.zeros(m, np.uint8)
    for i, row in enumerate(table):
        bits = info[360 * i:360 * (i + 1)]
        j = np.flatnonzero(bits)
        for x in row:
            np.bitwise_xor.at(p, (x + j * q) % m, 1)
    p = np.bitwise_xor.accumulate(p)
    return np.concatenate([info.astype(np.uint8), p])


_LAYERS = {}


def _layers(table, q, kldpc, n=N16K):
    """The graph's edges grouped into Q layers, checks c with c mod Q = r, in order: no two
    checks of a layer share a parity bit (p_j is in checks j and j + 1), and a sweep carries
    information along the accumulator chain (c, c + 1, ...) in one iteration."""
    key = (id(table), q, kldpc, n)
    if key not in _LAYERS:
        chk, var, m = ldpc_graph(table, q, kldpc, n)
        edge = np.arange(len(chk))
        layers = []
        for r in range(q):
            sel = edge[chk % q == r]                      # already sorted by check
            c = chk[sel]
            starts = np.flatnonzero(np.r_[True, c[1:] != c[:-1]])
            row_of = np.repeat(np.arange(len(starts)), np.diff(np.r_[starts, len(sel)]))
            layers.append((sel, var[sel], starts, row_of))
        _LAYERS[key] = layers
    return _LAYERS[key]


def _min_sum(v2c, starts, row_of, alpha):
    """Normalised min-sum check-to-variable messages for edges grouped by check."""
    mag = np.abs(v2c)
    neg = v2c < 0
    parity = np.add.reduceat(neg.astype(np.int64), starts) % 2
    min1 = np.minimum.reduceat(mag, starts)
    is_min = mag == min1[row_of]
    idx = np.flatnonzero(is_min)
    _, first = np.unique(row_of[idx], return_index=True)
    first_min = np.zeros(len(mag), bool)
    first_min[idx[first]] = True
    min2 = np.minimum.reduceat(np.where(first_min, np.inf, mag), starts)
    out = np.where(first_min, min2[row_of], min1[row_of])
    out = np.minimum(out, 1e30)                            # a check of degree 1 has no second minimum
    return alpha * out * np.where(neg ^ (parity[row_of] == 1), -1.0, 1.0)


def _sum_product(v2c, starts, row_of, alpha=None):
    """Exact (tanh rule) check-to-variable messages for edges grouped by check."""
    t = np.tanh(np.clip(v2c, -40, 40) / 2)
    t = np.where(np.abs(t) < 1e-12, 1e-12, t)
    neg = t < 0
    mag = np.log(np.abs(t))
    total = np.add.reduceat(mag, starts)
    parity = np.add.reduceat(neg.astype(np.int64), starts) % 2
    m = np.exp(total[row_of] - mag)
    return 2 * np.arctanh(np.clip(m, 0, 1 - 1e-12)) * np.where(neg ^ (parity[row_of] == 1), -1.0, 1.0)


def ldpc_decode(llr, table, q, kldpc, n=N16K, iterations=100, rule='sum-product', alpha=0.8):
    """``(bits, iterations, ok)``: layered belief propagation (Q layers of checks c mod Q,
    see ``_layers``); ``llr`` positive means 0 (large for known bits, 0 for punctured). Stops
    when every check holds. ``rule`` is 'sum-product' (exact) or 'min-sum' (normalised by
    ``alpha``). L1's codes are small and mostly known or punctured (L1-post: 350 information
    bits in 7032, 8018 of 9000 parity bits punctured): most checks are a step along the
    accumulator chain, where min-sum's factor decays the message each hop. On a 64QAM
    L1-post with known noise, sum-product decoded from 8 dB, min-sum at 1.0 from 10 dB and
    at 0.8 not by 18 dB; layered rather than flooding carries the chain in one sweep."""
    update = _sum_product if rule == 'sum-product' else _min_sum
    chk, var, m = ldpc_graph(table, q, kldpc, n)
    starts_all = np.flatnonzero(np.r_[True, chk[1:] != chk[:-1]])
    layers = _layers(table, q, kldpc, n)
    c2v = np.zeros(len(chk))
    total = np.asarray(llr, float).copy()
    for it in range(1, iterations + 1):
        for sel, v, starts, row_of in layers:
            old = c2v[sel]
            v2c = total[v] - old
            new = update(v2c, starts, row_of, alpha)
            c2v[sel] = new
            total += np.bincount(v, weights=new - old, minlength=n)
        hard = (total < 0).astype(np.uint8)
        if not np.any(np.add.reduceat(hard[var].astype(np.int64), starts_all) % 2):
            return hard, it, True
    return (total < 0).astype(np.uint8), iterations, False


# ------------------------------------------------------------------ L1 shortening and puncturing

def _shortening(kbch, ksig, pad_order):
    """The positions in the Kbch BCH information bits that carry the Ksig bits (EN 7.3.2.2);
    the rest are zero padding."""
    ngroup = len(pad_order)
    sizes = [360] * (ngroup - 1) + [kbch - 360 * (ngroup - 1)]      # the last group is short
    padded = np.zeros(kbch, bool)
    npad = ngroup - 1 if 0 < ksig <= 360 else (kbch - ksig) // 360
    for g in pad_order[:npad]:
        padded[360 * g:360 * g + sizes[g]] = True
    extra = (360 - ksig) if npad == ngroup - 1 else (kbch - ksig - 360 * npad)
    if extra > 0:
        g = pad_order[ngroup - 1] if npad == ngroup - 1 else pad_order[npad]
        end = 360 * g + sizes[g]
        padded[end - extra:end] = True
    return np.flatnonzero(~padded)


def _punctured(nparity, q, npunc, punc_order):
    """The parity bit indices that are punctured (EN 7.3.2.5): group j is p_k, k mod Q = j."""
    groups = npunc // 360
    out = []
    for g in punc_order[:groups]:
        out.append(np.arange(g, nparity, q))
    if npunc - 360 * groups > 0:
        g = punc_order[groups]
        out.append(np.arange(g, nparity, q)[:npunc - 360 * groups])
    return np.sort(np.concatenate(out)) if out else np.empty(0, int)


def l1_codeword_llr(tx_llr, kbch, kldpc, ksig, npunc, q, pad_order, punc_order, n=N16K, sure=None):
    """The ``n`` LLRs of the LDPC codeword from the transmitted ones (Ksig information bits,
    168 BCH parity bits, the unpunctured LDPC parity bits in order; EN 7.3.2.6), with the
    zero padding known and the punctured bits unknown. Returns ``(llr, info_positions)``."""
    info_pos = _shortening(kbch, ksig, pad_order)
    nparity = n - kldpc
    punct = _punctured(nparity, q, npunc, punc_order)
    keep = np.setdiff1d(np.arange(nparity), punct)
    if sure is None:
        # the zero padding is known: far above any channel LLR, or the checks' messages
        # (which grow with the channel's) outvote it (seen off air at 15-19 dB)
        sure = 1e3 * max(1.0, float(np.max(np.abs(tx_llr))))
    llr = np.zeros(n)
    llr[:kbch] = sure                                       # padding: known zeros
    llr[info_pos] = tx_llr[:ksig]
    llr[kbch:kldpc] = tx_llr[ksig:ksig + (kldpc - kbch)]
    llr[kldpc + keep] = tx_llr[ksig + (kldpc - kbch):ksig + (kldpc - kbch) + len(keep)]
    return llr, info_pos


def l1_post_npunc(ksig, mod, np2):
    """``(npunc, npost)`` for an L1-post block (EN 7.3.1.2, steps 1-4)."""
    kbch = 7032
    mult = 2 * mod if np2 == 1 else np2 * mod
    npunc_temp = max(mult - 1, (6 * (kbch - ksig)) // 5)
    npost_temp = ksig + 168 + 9000 - npunc_temp
    if np2 == 1:
        npost = -(-npost_temp // (2 * mod)) * 2 * mod
    else:
        npost = -(-npost_temp // (mod * np2)) * mod * np2
    return npunc_temp - (npost - npost_temp), npost


def scrambled(pre):
    """Whether L1-post is scrambled (EN 7.3.2.1): L1_POST_SCRAMBLED, but only from V1.3.1
    (T2_VERSION 2 or more). Under V1.2.1 the bit is reserved and "sometimes used for bias
    balancing" (EN 7.2.2, under Table 28): four of five multiplexes off air set it, with
    T2_BASE_LITE and RESERVED, and sent L1-post unscrambled."""
    return bool(pre['L1_POST_SCRAMBLED']) and pre['T2_VERSION'] >= 2


def bb_scramble(n):
    """The BBFRAME / L1-post scrambling sequence (EN 5.2.4, 7.3.2.1): 1 + X^14 + X^15,
    loaded with 100101010000000."""
    reg = [1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 0, 0, 0, 0, 0]
    out = np.empty(n, np.uint8)
    for i in range(n):
        b = reg[13] ^ reg[14]
        out[i] = b
        reg = [b] + reg[:-1]
    return out


# ------------------------------------------------------------------ demapping (EN 6.2.2)

def _axis_levels(bits_per_axis):
    """Gray levels of one axis as Table 14: for each amplitude level, its bits."""
    if bits_per_axis == 1:
        return np.array([1.0, -1.0]), np.array([[0], [1]])
    # Table 14(d)/(f)/(h): first bit is the sign (1 negative); the rest Gray-coded.
    m = bits_per_axis
    levels = np.arange(-(2 ** m) + 1, 2 ** m, 2, dtype=float)
    bits = np.zeros((2 ** m, m), np.uint8)
    for idx, lv in enumerate(levels):
        a = int((abs(lv) - 1) // 2)                       # 0 for +-1, ... outward
        # Table 14: the magnitude bits are all 0 on the outermost level and Gray-coded
        # inward (64-QAM: 7 -> 00, 5 -> 01, 3 -> 11, 1 -> 10)
        code = (2 ** (m - 1) - 1) - a
        code = code ^ (code >> 1)
        bits[idx, 0] = 1 if lv < 0 else 0
        for b in range(m - 1):
            bits[idx, 1 + b] = (code >> (m - 2 - b)) & 1
    return levels, bits


_NORM = {1: math.sqrt(2), 2: math.sqrt(10), 3: math.sqrt(42), 4: math.sqrt(170)}


@functools.lru_cache(maxsize=None)
def _axis_segments(m):
    """One axis's max-log LLRs as straight lines: ``(A, B)``, each (2 * 2^m, m), so that for an
    unnormalised amplitude u in [k, k + 1) bit b's LLR times the noise is A[k + 2^m, b] u +
    B[k + 2^m, b], up to 1 / norm^2. Each bit's min over its 1-levels of (u - l)^2 less the min
    over its 0-levels is a difference of two parabolas whose nearest level changes only at a
    level or halfway between two (both whole numbers), so it is a straight line on every
    [k, k + 1); beyond the outermost levels the nearest stay put, so the end lines go on."""
    levels, bits = _axis_levels(m)
    top = 2 ** m

    def f(u):
        d = (u - levels) ** 2
        return np.array([d[bits[:, b] == 1].min() - d[bits[:, b] == 0].min() for b in range(m)])

    a = np.empty((2 * top, m))
    b = np.empty((2 * top, m))
    for i, k in enumerate(range(-top, top)):
        f1, f2 = f(k + 0.25), f(k + 0.75)
        a[i] = (f2 - f1) / 0.5
        b[i] = f1 - a[i] * (k + 0.25)
    return a, b


def demap_llr(cells, mod, noise):
    """Max-log LLRs (positive means 0) of cells of unit mean power for BPSK (1), QPSK (2),
    16-QAM (4), 64-QAM (6) or 256-QAM (8), in Table 14's bit order y0, y1, ...: even bits from
    the real part, odd from the imaginary (BPSK: the real part only). float32; exact (each
    axis's LLR is piecewise linear: ``_axis_segments``)."""
    cells = np.asarray(cells)
    noise = np.broadcast_to(np.asarray(noise, np.float32), cells.shape)
    if mod == 1:
        return (4 * cells.real / noise).astype(np.float32)
    m = mod // 2
    norm = _NORM[m]
    a, b = _axis_segments(m)
    top = 2 ** m
    a = (a / norm ** 2).astype(np.float32)
    b = (b / norm ** 2).astype(np.float32)
    inv = (1.0 / noise).astype(np.float32)
    out = np.empty((len(cells), mod), np.float32)
    for part, x in ((0, cells.real), (1, cells.imag)):
        u = (x * norm).astype(np.float32)
        k = np.clip(np.floor(u).astype(np.int32), -top, top - 1) + top
        out[:, part::2] = (a[k] * u[:, None] + b[k]) * inv[:, None]
    return out


# ------------------------------------------------------------------ the receiver

def _cp_metric(y, size, ng):
    """The guard correlation at every start: sum over ng of y[n] conj(y[n + size])."""
    c = np.concatenate([[0], np.cumsum(y[:-size] * np.conj(y[size:]))])
    return c[ng:] - c[:-ng]


def find_p2(y, fs, size, guard, start_hint):
    """``(start of P2's useful part, fractional frequency offset in Hz)`` near
    ``start_hint`` (P1's start + 2048 T + the guard), by the guard's correlation."""
    ng = int(round(size * GUARD_FRACTION[guard]))
    lo = max(0, start_hint - ng - size // 8)
    seg = y[lo:start_hint + size + ng + size // 8]
    m = _cp_metric(seg, size, ng)
    i = int(np.argmax(np.abs(m)))
    cfo = -np.angle(m[i]) / (2 * np.pi * size / fs)
    return lo + i + ng, cfo


def p2_cells(y, fs, size, guard, extended, p1_start, freq_offset=None, t_s=T_8MHZ):
    """The equalised data cells of the first P2 symbol (in carrier order, before frequency
    de-interleaving), their noise variance, and what was found: the useful part's start,
    the frequency offset and the integer carrier shift."""
    hint = p1_start + int(round(2048 * t_s * fs)) + int(round(size * GUARD_FRACTION[guard]))
    start, frac = find_p2(y, fs, size, guard, hint)
    cfo = frac if freq_offset is None else freq_offset
    n = np.arange(start, start + size)
    sym = y[start:start + size] * np.exp(-2j * np.pi * cfo * n / fs)
    spec = np.fft.fft(sym)
    km = kmax(size, extended)
    centre = km // 2
    pilots, data = p2_layout(size, extended)
    ref = p2_pilot_values(size, extended, pilots)
    # The integer carrier offset, within 6 % of the carriers either side (about 470 kHz in
    # 8 MHz): a band centred on its power centroid sat up to 300 kHz (540 carriers of 16k)
    # from the channel's centre off air. The wrong alignments score about sqrt(N) of N.
    reach = max(64, int(0.06 * km))
    best = None
    for shift in range(-reach, reach + 1):
        bins = (pilots - centre + shift) % size
        z = spec[bins] * ref
        score = abs(np.sum(z[1:] * np.conj(z[:-1])))
        if best is None or score > best[0]:
            best = (score, shift)
    shift = best[1]
    allk = np.arange(km + 1)
    vals = spec[(allk - centre + shift) % size]
    h_p, h_noise = smooth_channel(vals[pilots] / ref, pilots, GUARD_FRACTION[guard])
    # The channel's mean delay turns its phase along the carriers (echoes within a long
    # guard: up to a radian a carrier); taken out before interpolating between pilots and
    # put back after, or the straight lines cut the corners (8 dB MER off air, before).
    gaps = np.diff(pilots)
    slope = np.angle(np.sum(h_p[1:] * np.conj(h_p[:-1]) * (gaps == np.median(gaps)))) / np.median(gaps)
    turn = np.exp(-1j * slope * pilots)
    flat = h_p * turn
    h = (np.interp(allk, pilots, flat.real) + 1j * np.interp(allk, pilots, flat.imag)) * np.exp(1j * slope * allk)
    eq = vals / h
    # noise from the pilots against their interpolated neighbours
    power = float(np.mean(np.abs(h_p) ** 2))
    if h_noise is None:                                   # no smoothing (the guard fills the grid)
        resid = flat[1:-1] - 0.5 * (flat[:-2] + flat[2:])
        h_noise = float(np.mean(np.abs(resid) ** 2)) / 1.5
    # a pilot's estimate is Y / (AP2 x +-1): its noise is a data cell's over AP2^2, so a data
    # cell's noise after equalising is the estimates' times AP2^2, over the channel's power
    # (read 8-11 dB low before, from the smoothed pilots and without AP2^2)
    noise = max(h_noise * AP2.get(size, AP2_OTHER) ** 2 / power, 1e-5)
    # Each cell's own noise after equalising: a carrier in a deep fade of the channel is
    # far noisier than the mean (channel state information); without it, faded cells give
    # the LDPC confident wrong bits and it stalls (off air, 2026-11-21).
    cell_noise = noise * power / np.maximum(np.abs(h[data]) ** 2, 1e-3 * power)
    return eq[data], cell_noise, {'start': start, 'freq_offset_hz': cfo + shift * fs / size,
                                  'carrier_shift': shift, 'noise': noise}


def smooth_channel(h_p, pilots, guard, margin=48):
    """The pilots' channel estimates with the noise outside the guard taken out.

    The channel's impulse response is no longer than the guard (or the symbols would run
    into each other), but each pilot's estimate is as noisy as a data cell. On the regular
    pilot grid (every 3rd or 6th carrier) the estimates go to the time domain, where the
    response covers guard x step x (grid length) taps; the circular window
    of that length (plus ``margin`` each side) holding most energy is kept, the rest set to
    0, and they come back; the irregular pilots (the extended edges) are left as they were.
    Measured: about 1 dB at the 64QAM L1-post threshold (16k, 19/128: at 8 dB 2 of 4 frames
    decoded against 0 without; at 9 dB 4 of 4 against 3).
    ``(estimates, their noise variance or None)``: the noise from what was taken out."""
    gaps = np.diff(pilots)
    step = int(np.median(gaps))
    grid = (pilots % step == 0)
    k = pilots[grid]
    if len(k) < 64 or np.any(np.diff(k) != step):
        return h_p, None
    m = len(k)
    imp = np.fft.ifft(h_p[grid])
    # m taps span Tu / step, so one tap is Tu / (step m) and the guard g Tu is g step m taps
    width = min(m, int(math.ceil(guard * step * m)) + 2 * margin)
    e = np.abs(imp) ** 2
    csum = np.concatenate([[0], np.cumsum(np.concatenate([e, e]))])
    start = int(np.argmax(csum[width:width + m] - csum[:m]))
    keep = np.zeros(m, bool)
    keep[(start + np.arange(width)) % m] = True
    out = h_p.copy()
    out[grid] = np.fft.fft(np.where(keep, imp, 0))
    # what was taken out is the noise outside the window: its share (m - width) / m of the
    # estimates' noise, so their noise variance is the residual's scaled back up
    noise = None
    if width < m:
        noise = float(np.mean(np.abs(h_p[grid] - out[grid]) ** 2)) * m / (m - width)
    return out, noise


def _fields(bits, layout):
    out, i = {}, 0
    for name, width in layout:
        out[name] = _uint(bits[i:i + width])
        i += width
    return out, i


def decode_l1_pre(cells, noise):
    """L1-pre from its 1840 cells: ``(fields or None, info)`` (EN 7.3.1.1)."""
    p = L1_PRE
    tx = demap_llr(cells[:1840], 1, np.broadcast_to(np.asarray(noise, float), np.shape(cells))[:1840])
    llr, info_pos = l1_codeword_llr(tx, p['kbch'], p['kldpc'], p['ksig'], p['npunc'], p['q'],
                                    p['pad'], p['punc'])
    bits, its, ok = ldpc_decode(llr, p['table'], p['q'], p['kldpc'])
    sig = bits[info_pos][:p['ksig']]
    crc_ok = crc32(sig) == 0
    info = {'ldpc_iterations': its, 'ldpc_ok': ok, 'crc_ok': crc_ok}
    if not crc_ok:
        return None, info
    fields, _ = _fields(sig, L1_PRE_FIELDS)
    return fields, info


def decode_l1_post(cells, noise, pre, np2=1):
    """L1-post (configurable and dynamic) from its cells, after L1-pre: ``(fields or None,
    info)`` (EN 7.3.1.2, 7.2.3)."""
    mod = L1_MOD[pre['L1_MOD']]
    k_ex_pad = pre['L1_POST_INFO_SIZE'] + 32
    nblocks = -(-k_ex_pad // 7032)
    kpost = -(-k_ex_pad // nblocks) * nblocks
    ksig = kpost // nblocks
    npunc, npost = l1_post_npunc(ksig, mod, np2)
    ncell = npost // mod
    out_bits, its_all, ok_all = [], [], True
    for b in range(nblocks):
        c = cells[b * ncell:(b + 1) * ncell]
        nb = np.broadcast_to(np.asarray(noise, float), np.shape(cells))[b * ncell:(b + 1) * ncell]
        y = demap_llr(c, mod, nb)                           # (cells, mod): y0..y(mod-1)
        if mod in (4, 6):
            nsub = 2 * mod
            bsub = np.concatenate([y[0::2], y[1::2]], axis=1)   # (cells/2, nsub): b_e,do
            v = bsub[:, DEMUX[mod]].reshape(-1)             # v[di] = b[e(di mod nsub)][di div nsub]
            nc = 2 * mod                                    # Table 44: 8 or 12 columns
            nr = npost // nc
            v = v.reshape(nr, nc).T.reshape(-1)             # written column-wise, read row-wise
        else:
            v = y.reshape(-1)
        llr, info_pos = l1_codeword_llr(v, 7032, 7200, ksig, npunc, 25, L1_POST_PAD[mod],
                                        L1_POST_PUNC[mod])
        bits, its, ok = ldpc_decode(llr, LDPC_B2, 25, 7200)
        its_all.append(its)
        ok_all &= ok
        sig = bits[info_pos][:ksig]
        if scrambled(pre):
            sig = sig ^ bb_scramble(ksig)
        out_bits.append(sig)
    allbits = np.concatenate(out_bits)[:k_ex_pad]
    crc_ok = crc32(allbits) == 0
    info = {'blocks': nblocks, 'ldpc_iterations': its_all, 'ldpc_ok': bool(ok_all), 'crc_ok': crc_ok}
    if not crc_ok:
        return None, info
    return parse_l1_post(allbits, pre), info


def parse_l1_post(bits, pre):
    """The configurable and dynamic L1-post fields (Figures 27, 28)."""
    i = 0

    def take(w):
        nonlocal i
        v = _uint(bits[i:i + w])
        i += w
        return v

    conf = {'SUB_SLICES_PER_FRAME': take(15), 'NUM_PLP': take(8), 'NUM_AUX': take(4),
            'AUX_CONFIG_RFU': take(8)}
    conf['RF'] = [{'RF_IDX': take(3), 'FREQUENCY': take(32)} for _ in range(pre['NUM_RF'])]
    if pre['S2'] & 1:
        conf['FEF'] = {'FEF_TYPE': take(4), 'FEF_LENGTH': take(22), 'FEF_INTERVAL': take(8)}
    plps = []
    for _ in range(conf['NUM_PLP']):
        f, used = _fields(bits[i:], PLP_FIELDS)
        i += used
        plps.append(f)
    conf['PLP'] = plps
    take(2)
    take(30)
    for _ in range(conf['NUM_AUX']):
        take(32)
    dyn = {'FRAME_IDX': take(8), 'SUB_SLICE_INTERVAL': take(22), 'TYPE_2_START': take(22),
           'L1_CHANGE_COUNTER': take(8), 'START_RF_IDX': take(3)}
    take(8)
    dyn['PLP'] = []
    for _ in range(conf['NUM_PLP']):
        dyn['PLP'].append({'PLP_ID': take(8), 'PLP_START': take(22), 'PLP_NUM_BLOCKS': take(10)})
        take(8)
    return {'configurable': conf, 'dynamic': dyn}


def _p1_metric(y, rate, t_s):
    """The P1 correlation at each start in ``y`` (both shift signs, the larger). Copied from
    skinny-fm-receiver's ``iqmeasure._p1_metric``, the one thing this module took from it."""
    l_c, l_a, l_b = (int(round(k * t_s * rate)) for k in (542, 1024, 482))
    n = np.arange(len(y))
    best = None
    for sign in (1, -1):
        z = y * np.exp(-sign * 2j * np.pi * n / (1024 * t_s * rate)).astype(np.complex64)
        k1 = np.concatenate([[0], np.cumsum(y[l_c:] * np.conj(z[:-l_c]))])
        m1 = np.abs(k1[l_c:] - k1[:-l_c])
        del k1
        k2 = np.concatenate([[0], np.cumsum(z[l_b:] * np.conj(y[:-l_b]))])
        m2 = np.abs(k2[l_b:] - k2[:-l_b])
        del k2, z
        # B's source, A's last 482 T, starts C + A - B after P1's start.
        off = l_c + l_a - l_b
        size = min(len(m1), len(m2) - off)
        m = m1[:size] + m2[off:off + size]
        best = m if best is None else np.maximum(best, m)
    return best


def find_p1(y, fs, peak=8.0, gap_s=0.01, t_s=T_8MHZ):
    """The starts (samples) of the P1 symbols in ``y``, by the C-A-B correlation that
    ``iqmeasure`` uses to classify (EN 9.8.2): peaks ``peak`` times the median, apart."""
    m = _p1_metric(np.asarray(y, np.complex64), fs, t_s)
    med = float(np.median(m))
    gap = int(gap_s * fs)
    found = []
    for i in np.flatnonzero(m > peak * med):
        if found and i - found[-1] < gap:
            if m[i] > m[found[-1]]:
                found[-1] = int(i)
        else:
            found.append(int(i))
    return found


def read_l1(y, fs, size, guard, extended, p1_start, t_s=T_8MHZ):
    """L1-pre and L1-post of the frame whose P1 starts at ``p1_start`` in ``y`` (complex IQ at
    the channel's elementary rate ``fs``, the channel centred): a dict with ``pre``,
    ``post`` (None where the CRC failed) and how they were found."""
    cells, noise, found = p2_cells(y, fs, size, guard, extended, p1_start, t_s=t_s)
    x, nx = deinterleave(cells, size), deinterleave(noise, size)
    pre, pre_info = decode_l1_pre(x, nx)
    post, post_info = (None, None)
    if pre is not None:
        n = pre['L1_POST_SIZE']
        post, post_info = decode_l1_post(x[1840:1840 + n], nx[1840:1840 + n], pre)
    return {'pre': pre, 'post': post, 'pre_info': pre_info, 'post_info': post_info, **found}


def deinterleave(cells, size, odd=False):
    """The frame builder's order of one symbol's data cells (EN 8.5): 1k-16k write
    a[p] = x[H0(p)] (H1 on odd symbols); 32k a[H(p)] = x[p] on even symbols and
    a[p] = x[H(p)] on odd ones."""
    perm = freq_permutation(size, len(cells), odd)
    if size == 32768 and not odd:
        return cells[perm]
    x = np.empty_like(cells)
    x[perm] = cells
    return x


def summary(r, size, guard, t_s=T_8MHZ):
    """What L1 says, for the card: None without a good L1-pre. The frame's length from
    L1-pre (P1, then NP2 + NUM_DATA_SYMBOLS symbols) and each PLP's payload rate,
    PLP_NUM_BLOCKS_MAX BBFRAMEs of Kbch - 80 bits a frame (Table 6, EN 5.1.7); real
    identifiers (cell, network, system) included, as the cells' PCIs are."""
    pre, post = r['pre'], r['post']
    if not pre:
        return None
    g = GUARD_FRACTION[GUARDS.get(pre['GUARD_INTERVAL'], guard)]
    frame_s = (2048 + (NP2[size] + pre['NUM_DATA_SYMBOLS']) * size * (1 + g)) * t_s
    plps = None
    if post:
        plps = []
        for p in post['configurable']['PLP']:
            kbch = KBCH.get(p['PLP_FEC_TYPE'], {}).get(p['PLP_COD'])
            plps.append({'id': p['PLP_ID'], 'type': PLP_TYPE_NAMES.get(p['PLP_TYPE'], p['PLP_TYPE']),
                         'modulation': PLP_MOD_NAMES.get(p['PLP_MOD']), 'code_rate': PLP_COD_NAMES.get(p['PLP_COD']),
                         'rotated': bool(p['PLP_ROTATION']), 'fec': PLP_FEC_NAMES.get(p['PLP_FEC_TYPE']),
                         'blocks': p['PLP_NUM_BLOCKS_MAX'], 'time_il_length': p['TIME_IL_LENGTH'],
                         'time_il_type': p['TIME_IL_TYPE'],
                         'payload_bps': (p['PLP_NUM_BLOCKS_MAX'] * (kbch - 80) / frame_s) if kbch else None})
    return {'version': T2_VERSION_NAMES.get(pre['T2_VERSION'], pre['T2_VERSION']),
            'extended': bool(pre['BWT_EXT']), 'guard': GUARDS.get(pre['GUARD_INTERVAL']),
            'pilot_pattern': PILOT_NAMES.get(pre['PILOT_PATTERN']), 'papr': pre['PAPR'],
            'l1_post_modulation': {1: 'BPSK', 2: 'QPSK', 4: '16QAM', 6: '64QAM'}.get(L1_MOD.get(pre['L1_MOD'])),
            'data_symbols': pre['NUM_DATA_SYMBOLS'], 'frames': pre['NUM_T2_FRAMES'], 'frame_s': frame_s,
            'cell_id': pre['CELL_ID'], 'network_id': pre['NETWORK_ID'], 't2_system_id': pre['T2_SYSTEM_ID'],
            'l1_post_ok': post is not None, 'plps': plps}


def _channel_centre(spec, rate, bw):
    """The centre (Hz from the samples' 0) of the window 0.97 of ``bw`` wide holding the most
    power in ``spec`` (an fftshifted spectrum of samples at ``rate``)."""
    n = len(spec)
    width = int(round(0.97 * bw * n / rate))
    if width >= n:
        return 0.0
    freqs = np.fft.fftshift(np.fft.fftfreq(n, 1 / rate))
    csum = np.concatenate([[0], np.cumsum(np.abs(spec) ** 2)])
    first = int(np.argmax(csum[width:] - csum[:-width]))
    return float(freqs[first + width // 2])


def channel_centre(x, rate, bw):
    """Where the channel ``bw`` wide is in samples ``x`` at ``rate`` (Hz from their 0), as
    ``to_elementary`` finds it, for a receiver that shifts it itself (``iqt2_live``)."""
    x = np.asarray(x)
    return _channel_centre(np.fft.fftshift(np.fft.fft(x.astype(np.complex128))), rate, bw)


def to_elementary(x, rate, bw, t_s=T_8MHZ):
    """Samples ``x`` at ``rate`` brought to the channel's elementary rate 1/T, the channel
    moved to 0 Hz, by their spectrum: the channel is the window 0.97 of ``bw`` wide holding
    the most power (a notch or a tilt hardly moves it; a power centroid sat 1.06 MHz off
    and an edge search stopped in 20 dB multipath notches, off air); bins past the samples'
    own band are 0. None when the samples are too short. What it leaves (under 0.1 MHz)
    the carrier search in ``p2_cells`` takes."""
    x = np.asarray(x)
    fe = 1 / t_s
    k = int(round(len(x) * fe / rate))
    if k < 4096:
        return None
    spec = np.fft.fftshift(np.fft.fft(x.astype(np.complex128)))
    centre = _channel_centre(spec, rate, bw)
    shift = int(round(centre * len(x) / rate))
    idx = len(spec) // 2 + shift - k // 2 + np.arange(k)
    inside = (idx >= 0) & (idx < len(spec))
    part = np.zeros(k, complex)
    part[inside] = spec[idx[inside]]
    return np.fft.ifft(np.fft.ifftshift(part)).astype(np.complex64)
