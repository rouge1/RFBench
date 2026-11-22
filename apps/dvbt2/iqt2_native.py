"""DVB-T2's 64K LDPC decoder in native code (Round 32, phase 4), through ctypes.

``native/t2ldpc.cc`` wraps xdsopl/LDPC's layered offset min-sum decoder (vendored in
``native/xdsopl_ldpc/``, 0BSD), on saturating int8 with one codeword per SIMD lane. It is
built by ``scripts/build_dvbt2_native.py`` for the machine it runs on and never committed; when it is not
built (or older than its sources) ``available()`` is False and ``iqt2_data`` uses its NumPy
decoder instead. Numpy and the standard library only, like ``iqt2_data``.

The LLRs (positive means 0) are scaled to int8 so that their median magnitude is MEDIAN_TO
steps, and clipped to CLIP_Q steps. The scale matters, because the decoder's offset is one
int8 step and its sums saturate at 127: too small and the small LLRs have too few levels,
too large and the sums saturate. And no one scale suits every signal. On BPSK in noise near
the threshold, x2 to x3 the true LLR does best and x6 fails. On gr-dtv's QAM frames near
threshold (64QAM 3/5 at 14 dB, 16QAM 3/5 at 8.5 dB), and on the off-air recordings, x4 to x8
does best and x2 fails. The median puts both inside their range.

The clip matters too, where the scale is at its floor of 1 (strong QPSK: a median LLR of 10).
Over the air from the VSG60 (QPSK 3/5, about 10 dB SNR, 2026-11-21), clipping at 36 steps let
the sums saturate and a third of the codewords stalled a few bits short (every BBHEADER right).
At 12 to 24 steps all 46 converged. On every earlier case 12 to 36 were alike (64QAM at 14 dB,
16QAM at 8.5, the recordings, QPSK at 12 and 25 dB), except BPSK at its threshold, where 12
lost a little. So 20.

A decoder holds state, so each thread gets its own (``threading.local``); ctypes lets go of
the GIL during a call, so threads decode at the same time.
"""

import ctypes
import os
import sys
import threading

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
NATIVE = os.path.join(HERE, 'native')
SOURCE = os.path.join(NATIVE, 't2ldpc.cc')
MEDIAN_TO = 10.0       # the median |LLR| goes to this many int8 steps
CLIP_Q = 20.0          # and every LLR is clipped to this many (36 let QPSK at 10 dB saturate)
SCALE_RANGE = (1.0, 12.0)
TRIALS = 50
N = 64800

_lib = None
_tried = False
_local = threading.local()
_lock = threading.Lock()


def library_path():
    return os.path.join(NATIVE, 'libt2ldpc' + ('.dylib' if sys.platform == 'darwin' else '.so'))


def _sources():
    out = [SOURCE]
    vendored = os.path.join(NATIVE, 'xdsopl_ldpc')
    if os.path.isdir(vendored):
        out += [os.path.join(vendored, f) for f in os.listdir(vendored) if f.endswith('.hh')]
    return out


def sources_hash():
    """SHA-256 of the sources the library is built from (their names and bytes)."""
    import hashlib
    h = hashlib.sha256()
    for s in sorted(_sources()):
        h.update(os.path.basename(s).encode() + b'\0')
        with open(s, 'rb') as f:
            h.update(f.read())
    return h.hexdigest()


def hash_path(path=None):
    return (path or library_path()) + '.sources'


def up_to_date(path=None):
    """Whether the library exists and was built from the sources as they are now: by the hash
    ``scripts/build_dvbt2_native.py`` wrote beside it (a checkout or a pull rewrites the files' times, not
    their bytes)."""
    path = path or library_path()
    if not os.path.exists(path):
        return False
    try:
        with open(hash_path(path)) as f:
            return f.read().strip() == sources_hash()
    except OSError:
        return False


def _load():
    global _lib, _tried
    with _lock:
        if _tried:
            return _lib
        _tried = True
        if os.environ.get('FMRX_NO_NATIVE') or not up_to_date():
            return None
        try:
            lib = ctypes.CDLL(library_path())
        except OSError:
            return None
        lib.t2ldpc_lanes.restype = ctypes.c_int
        lib.t2ldpc_table.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        lib.t2ldpc_new.restype = ctypes.c_void_p
        lib.t2ldpc_new.argtypes = [ctypes.c_int]
        lib.t2ldpc_free.argtypes = [ctypes.c_void_p]
        lib.t2ldpc_decode.restype = ctypes.c_int
        lib.t2ldpc_decode.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_float,
                                      ctypes.c_float, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
        _lib = lib
        return lib


def available():
    """True when the native decoder is built, up to date and loads (``FMRX_NO_NATIVE`` set: False)."""
    return _load() is not None


def lanes():
    lib = _load()
    return lib.t2ldpc_lanes() if lib else 0


def table(rate):
    """The library's parity address table for a rate (0..5 as PLP_COD): rows of addresses."""
    lib = _load()
    buf = np.zeros(2 * 300000, np.int32)
    n = lib.t2ldpc_table(int(rate), buf.ctypes.data, len(buf))
    if n < 0:
        raise ValueError(f'no table for rate {rate}')
    rows = {}
    for r, x in buf[:2 * n].reshape(-1, 2).tolist():
        rows.setdefault(r, []).append(x)
    return [tuple(rows[r]) for r in range(len(rows))]


class _Handles:
    def __init__(self, lib):
        self.lib = lib
        self.by_rate = {}

    def get(self, rate):
        h = self.by_rate.get(rate)
        if h is None:
            h = self.lib.t2ldpc_new(int(rate))
            if not h:
                raise ValueError(f'no LDPC code for rate {rate}')
            self.by_rate[rate] = h
        return h

    def __del__(self):
        for h in self.by_rate.values():
            try:
                self.lib.t2ldpc_free(h)
            except Exception:
                pass


def auto_scale(llr):
    """The int8 scale for these LLRs: their median magnitude (from a sample) to MEDIAN_TO steps,
    within SCALE_RANGE."""
    flat = np.asarray(llr).reshape(-1)
    sample = np.abs(flat[::97]) if len(flat) > 9700 else np.abs(flat)
    med = float(np.median(sample)) if len(sample) else 1.0
    return float(np.clip(MEDIAN_TO / max(med, 1e-6), *SCALE_RANGE))


def decode(llr, rate, trials=TRIALS, scale=None, clip=None, threads=None):
    """``(bits, iterations used, ok per codeword)`` for rows of 64 800 LLRs (positive means 0),
    as ``iqt2_data.ldpc_decode_batch`` gives them. ``threads``: how many decode batches of
    ``lanes()`` codewords at once (None: up to 4, fewer for fewer batches). ``scale`` and
    ``clip`` (in LLR units) default to ``auto_scale`` and CLIP_Q steps."""
    lib = _load()
    if lib is None:
        raise RuntimeError('the native LDPC decoder is not built: scripts/build_dvbt2_native.py')
    llr = np.ascontiguousarray(llr, np.float32)
    if llr.ndim != 2 or llr.shape[1] != N:
        raise ValueError(f'expected (blocks, {N}) LLRs, got {llr.shape}')
    scale = scale or auto_scale(llr)
    clip = clip or CLIP_Q / scale
    bits = np.empty(llr.shape, np.uint8)
    ok = np.zeros(len(llr), np.uint8)
    w = lib.t2ldpc_lanes()
    batches = -(-len(llr) // w)
    threads = max(1, min(batches, threads or 4))
    per = -(-batches // threads) * w
    spans = [(a, min(a + per, len(llr))) for a in range(0, len(llr), per)]

    def run(span):
        a, b = span
        handles = getattr(_local, 'handles', None)
        if handles is None:
            handles = _local.handles = _Handles(lib)
        return lib.t2ldpc_decode(handles.get(rate), llr[a:b].ctypes.data, b - a, float(clip), float(scale),
                                 int(trials), bits[a:b].ctypes.data, ok[a:b].ctypes.data)

    if len(spans) == 1:
        used = [run(spans[0])]
    else:
        used = list(_pool().map(run, spans))
    return bits, max(used), ok.astype(bool)


_executor = None


def _pool():
    global _executor
    with _lock:
        if _executor is None:
            from concurrent.futures import ThreadPoolExecutor
            _executor = ThreadPoolExecutor(4, thread_name_prefix='t2ldpc')
        return _executor
