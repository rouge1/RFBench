"""DVB-T2 live (Round 32, phase 4): a continuous stream of IQ at the channel's elementary
rate in, the transport stream out, frame by frame in worker processes. Numpy and the
standard library only, like ``iqt2`` and ``iqt2_data``.

``T2Stream`` holds the samples. It finds the first P1 (``iqt2.find_p1``), then expects each
next one a T2-frame later and finds it again within ``TRACK_S`` of where it was expected. The
P1s' spacing against the frame's length says how far our sample clock is from the
transmitter's (``clock_ppm``); past 1 ppm each window is resampled to the transmitter's clock
before it is decoded (``resample``), as a HackRF or an RTL-SDR can be tens of ppm out. Each frame's window (from just
before its P1 to past its end) goes to a pool of processes, each reading L1 and decoding the
PLP to BBFRAMEs (``iqt2_data.decode_bbframes``: no state from frame to frame); the BBFRAMEs
come back in the order the frames were sent and go through one ``TsRebuilder``, so the TS is
one stream. A frame that cannot be decoded, or one dropped because the workers are behind,
breaks the packet that spans it and no more (``TsRebuilder.carry``).

The LDPC is native where ``scripts/build_dvbt2_native.py`` has built it (``iqt2_native``); without it the
decoding runs, far slower than the air. Processes are spawned (not forked), so a window with a
flowgraph and Qt running is safe.
"""

import collections
import multiprocessing
import os
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

import numpy as np

from . import iqt2, iqt2_data

BACK_S = 0.004          # each window starts this long before its P1
TRACK_S = 0.003         # the next P1 is looked for within this of where it was expected
SEARCH_S = 0.30         # the first P1 is looked for in this much (a T2-frame is at most 250 ms)
LOST_AFTER = 3          # frames without their P1 before searching again
SFO_FIX = 1e-6          # sample clocks further apart than this are corrected in the worker,
P1_JITTER = 2.5         # once the estimate is surely past it: a P1 is found within about this
CLOCK_P1S = 20          # many samples (rms, off air), and the clock fitted to this many P1s
_LOST = object()        # in T2Live's queue where samples were dropped


def resample(y, eps):
    """``y`` taken by a clock ``1 + eps`` times the transmitter's, brought to the transmitter's
    (round(len / (1 + eps)) samples), by its spectrum: the bins added or dropped are at half
    the rate, outside the channel's carriers."""
    n = len(y)
    m = int(round(n / (1 + eps)))
    if m == n:
        return y
    spec = np.fft.fft(y)
    out = np.zeros(m, complex)
    keep = min(n, m)
    lo = (keep + 1) // 2
    out[:lo] = spec[:lo]
    out[m - (keep - lo):] = spec[n - (keep - lo):]
    return (np.fft.ifft(out) * (m / n)).astype(np.complex64)


def _decode_window(y, p1, fs, size, guard, extended, plp_id, threads, sfo=0.0):
    """In a worker: one frame's window to packed BBFRAMEs, or the reason it could not be.
    ``sfo``: how much faster our sample clock runs than the transmitter's (from the P1s'
    spacing); past SFO_FIX the window is resampled first, as over a frame it would move the
    last symbols by tens of samples and the outer carriers by a tenth of their spacing (20 ppm,
    16k: the frame decoded at 16 dB SNR on a clean signal, and not a codeword converged)."""
    tic = time.perf_counter()
    if abs(sfo) > SFO_FIX:
        y = resample(y, sfo)
        p1 = int(round(p1 / (1 + sfo)))
    try:
        l1 = iqt2.read_l1(y, fs, size, guard, extended, p1, t_s=1 / fs)
        if l1['post'] is None:
            return {'error': 'L1 not read', 'seconds': time.perf_counter() - tic}
        frames, ok, info = iqt2_data.decode_bbframes(y, fs, l1, size, guard, extended, p1, plp_id,
                                                     t_s=1 / fs, threads=threads)
    except (ValueError, KeyError, IndexError, NotImplementedError) as exc:
        return {'error': f'{type(exc).__name__}: {exc}', 'seconds': time.perf_counter() - tic}
    info.pop('frame', None)
    return {'frames': np.packbits(frames, axis=1), 'ok': ok, 'info': info,
            'snr_db': round(float(10 * np.log10(1 / max(l1.get('noise', 1e-9), 1e-9))), 1),
            'seconds': time.perf_counter() - tic}


class T2Stream:
    """Feed it IQ (complex64, the channel at 0 Hz, ``fs`` = 1/T); it calls ``on_ts(bytes)`` with
    the transport stream as frames are decoded, in order, and ``on_frame(dict)`` once a frame.

    ``size``, ``guard``, ``extended`` and ``frame_s`` come from the signal's L1 (``iq-measure``'s
    dvbt card: ``fft``, ``guard``, ``l1.extended``, ``l1.frame_s``). ``workers`` processes decode
    at once, each with ``threads`` LDPC threads; with more than ``3 * workers`` frames waiting,
    new ones are dropped (counted) rather than falling further behind the air."""

    def __init__(self, fs, size, guard, extended, frame_s, plp_id=None, workers=4, threads=2,
                 on_ts=None, on_frame=None, pool=None):
        self.fs = float(fs)
        self.size, self.guard, self.extended = size, guard, extended
        self.frame = int(round(frame_s * self.fs))
        self.plp_id = plp_id
        self.threads = threads
        self.on_ts = on_ts or (lambda data: None)
        self.on_frame = on_frame or (lambda info: None)
        self.back = int(BACK_S * self.fs)
        self.track = int(TRACK_S * self.fs)
        self.window = self.back + self.frame + int(0.002 * self.fs)
        self.buf = np.zeros(0, np.complex64)
        self.chunks = []                # samples fed since buf was last joined
        self.held = 0                   # len(buf) + the chunks'
        self.base = 0                   # the stream's sample number of buf[0]
        self.next_p1 = None             # where the next P1 is expected (stream samples)
        self.p1s = collections.deque(maxlen=CLOCK_P1S)   # (frame since the lock, sample) found
        self.frames_since_lock = 0
        self.clock = 0.0                # our sample clock against the transmitter's, less 1
        self.sfo = 0.0                  # what the worker corrects: clock once it is sure, else 0
        self.sfo_frames = 0
        self.first = None               # the first frame's window, until the clock is measured
        self.missed = 0
        self.ts = iqt2_data.TsRebuilder()
        self.pending = collections.deque()
        self.max_pending = 3 * workers
        self.stats = {'frames': 0, 'decoded': 0, 'dropped': 0, 'failed': 0, 'p1_lost': 0,
                      'codewords': 0, 'codewords_ok': 0, 'bbheaders_ok': 0, 'bytes': 0,
                      'seconds_in': 0.0, 'decode_s': 0.0}
        self.started = time.monotonic()
        self.own_pool = pool is None
        self.pool = pool or make_pool(workers)

    # ---------------------------------------------------------------- input

    def feed(self, x):
        """Add samples; send off every frame they complete, and pass on what has come back."""
        x = np.array(x, np.complex64)            # a copy: the caller's buffer is reused
        self.stats['seconds_in'] += len(x) / self.fs
        self.chunks.append(x)
        self.held += len(x)
        if self.held >= self._needed():          # join the chunks only when a frame is complete
            self.buf = np.concatenate([self.buf] + self.chunks)
            self.chunks, self.held = [], len(self.buf)
            self._frames()
            self.held = len(self.buf)
        self._collect(block=False)

    def _needed(self):
        if self.next_p1 is None:
            return int(SEARCH_S * self.fs)
        return self.next_p1 + self.track + self.window - self.base

    def _frames(self):
        while True:
            if self.next_p1 is None:
                if not self._search():
                    return
            start = self.next_p1 - self.back - self.track
            end = self.next_p1 + self.track + self.window
            if end - self.base > len(self.buf):
                return
            seg = self.buf[start - self.base:start - self.base + 2 * self.track + 2 * self.back]
            found = iqt2.find_p1(seg, self.fs, t_s=1 / self.fs)
            if found:
                p1 = start + found[0]
                self.missed = 0
                self._clock(p1)
            else:
                self.missed += 1
                self.stats['p1_lost'] += 1
                if self.missed >= LOST_AFTER:
                    self.next_p1 = None
                    self._gap()
                    continue
                p1 = self.next_p1                     # coast a frame on the last timing
            a = p1 - self.back - self.base
            window = self.buf[a:a + self.window]
            if self.sfo_frames == 0 and self.first is None and len(self.p1s) == 1 and self.p1s[-1][1] == p1:
                self.first = window.copy()              # held until the clock is measured
            else:
                if self.first is not None:
                    self._send(self.first, self.back)
                    self.first = None
                self._send(window, self.back)
            self.next_p1 = p1 + int(round(self.frame * (1 + self.clock)))
            self.frames_since_lock += 1
            self._trim(self.next_p1 - self.back - self.track)

    def _clock(self, p1):
        """Follow our sample clock against the transmitter's: a straight line through the last
        CLOCK_P1S P1s (frame number, sample). Its slope's error falls fast with more P1s (one
        spacing: about 1.5 ppm; five: 0.3), so the correction is applied only once the estimate
        is past SFO_FIX by three times its own error: a wrong one is worse than none (3 ppm
        applied to a frame that needed none left 28 of 139 codewords)."""
        k = self.frames_since_lock
        self.p1s.append((k, p1))
        if len(self.p1s) < 2:
            return
        ks = np.array([a for a, _ in self.p1s], float)
        ps = np.array([b for _, b in self.p1s], float)
        skk = float(np.sum((ks - ks.mean()) ** 2))
        if skk <= 0:
            return
        slope = float(np.sum((ks - ks.mean()) * (ps - ps.mean())) / skk)
        eps = slope / self.frame - 1
        if abs(eps) > 2e-4:                     # a wrong P1 would say more: start again
            self.p1s.clear()
            self.p1s.append((k, p1))
            return
        self.clock = eps                        # for where the next P1 is
        err = P1_JITTER / (self.frame * skk ** 0.5)
        self.sfo = eps if abs(eps) > SFO_FIX + 3 * err else 0.0
        self.sfo_frames += 1

    def _search(self):
        n = int(SEARCH_S * self.fs)
        if len(self.buf) < n:
            return False
        found = iqt2.find_p1(self.buf[:n], self.fs, t_s=1 / self.fs)
        if not found:
            self._trim(self.base + len(self.buf) - self.track)
            return False
        p1 = self.base + found[0]
        if found[0] < self.back + self.track:           # too near the start for a window: the next
            p1 += self.frame
        self.next_p1 = p1
        self.p1s.clear()
        self.frames_since_lock = 0
        self.missed = 0
        return True

    def _trim(self, keep_from):
        cut = max(0, min(len(self.buf), keep_from - self.base))
        if cut:
            self.buf = self.buf[cut:]
            self.base += cut

    # ---------------------------------------------------------------- decoding

    def _send(self, window, p1):
        self.stats['frames'] += 1
        if len(self.pending) >= self.max_pending:
            self.stats['dropped'] += 1
            self._gap()
            return
        fut = self.pool.submit(_decode_window, np.ascontiguousarray(window), p1, self.fs, self.size,
                               self.guard, self.extended, self.plp_id, self.threads, self.sfo)
        self.pending.append(fut)

    def _gap(self):
        if not self.pending or self.pending[-1] is not None:   # one gap is as good as many
            self.pending.append(None)       # the packet spanning it is lost

    def _collect(self, block):
        while self.pending:
            head = self.pending[0]
            if head is not None and not head.done() and not block:
                return
            self.pending.popleft()
            if head is None:
                self.ts.carry = None
                continue
            try:
                r = head.result()
            except Exception as exc:            # a worker died: say so, carry on
                r = {'error': f'{type(exc).__name__}: {exc}', 'seconds': 0.0}
            self.stats['decode_s'] += r.get('seconds', 0.0)
            if 'error' in r:
                self.stats['failed'] += 1
                self.ts.carry = None
                self.on_frame({'error': r['error']})
                continue
            info = r['info']
            frames = np.unpackbits(r['frames'], axis=1, count=info['kbch'])
            data, headers = iqt2_data.feed_bbframes(self.ts, frames, r['ok'], info['kbch'])
            self.stats['decoded'] += 1
            self.stats['codewords'] += info['blocks']
            self.stats['codewords_ok'] += info['ldpc_ok']
            self.stats['bbheaders_ok'] += headers
            self.stats['bytes'] += len(data)
            info.update(bbheaders_ok=headers, packets=len(data) // 188, snr_db=r['snr_db'],
                        seconds=round(r['seconds'], 3))
            if data:
                self.on_ts(data)
            self.on_frame(info)

    def flush(self):
        """Wait for every frame sent off, and pass on what they give."""
        if self.first is not None:
            self._send(self.first, self.back)
            self.first = None
        self._collect(block=True)

    def reset(self):
        """The samples stopped being one stream (some were lost): search for a P1 again. Frames
        already sent still come back; the packet spanning the gap is lost."""
        self.base += self.held
        self.buf = np.zeros(0, np.complex64)
        self.chunks, self.held = [], 0
        self.next_p1 = None
        self.first = None
        self.p1s.clear()
        self._gap()

    def cancel(self):
        """Forget every frame sent off, without waiting for them or passing on what they give
        (a retune, or the window closing)."""
        while self.pending:
            fut = self.pending.popleft()
            if fut is not None:
                fut.cancel()
        self.first = None
        self.ts.carry = None

    def close(self):
        self.flush()
        if self.own_pool:
            self.pool.shutdown(wait=True, cancel_futures=True)

    def summary(self):
        s = dict(self.stats)
        s.update(packets=self.ts.packets, packets_errored=self.ts.errored,
                 wall_s=round(time.monotonic() - self.started, 2), pending=len(self.pending),
                 clock_ppm=round(self.clock * 1e6, 2), clock_corrected=self.sfo != 0.0)
        return s

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _warm():
    """In a new worker: import the decoder and load the native LDPC, before the first frame."""
    from . import iqt2_native
    return iqt2_native.available()


def _watch_parent():
    """In a new worker: end it when the process that made it ends. A pool's workers wait on a
    queue whose writing end they hold themselves, so a parent that dies without shutting the
    pool down (killed, or ``os._exit``) left them running for ever, holding its stdout open."""
    import threading
    parent = multiprocessing.parent_process()
    if parent is None:
        return

    def watch():
        parent.join()
        os._exit(0)
    threading.Thread(target=watch, name='parent-watch', daemon=True).start()


def make_pool(workers, warm=False):
    """Worker processes for the frames, spawned (not forked: safe beside a running flowgraph
    and Qt; ``python -m fm_receiver``'s ``__main__`` is not run again in them), each ending
    itself when its parent does (``_watch_parent``). ``warm``: start them all now (a second
    or two), not at the first frames, which would then wait."""
    pool = ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context('spawn'),
                               initializer=_watch_parent)
    if warm:
        for f in [pool.submit(_warm) for _ in range(workers)]:
            f.result()
    return pool


def default_workers():
    """Decoding processes for this machine: a 64QAM frame takes a worker about four times its own
    length with the native LDPC, so six keep up (1.18 times the air here); never more than half
    the cores."""
    return max(1, min(6, (os.cpu_count() or 2) // 2))


# ------------------------------------------------------------------ live, on a thread of its own

ACQUIRE_S = 1.2         # samples measured to learn the signal (a T2-frame is at most 250 ms)
QUEUE_S = 3.0           # samples queued for the thread before new ones are dropped (a lock
                        # drains 1.2 s learnt and what came meanwhile: 2 s dropped some)
KEEP_S = 1.0            # of the samples queued while learning, the newest kept at a lock (a
                        # P1 search and whole frames need about that; the player paces the rest)
RELOCK_S = 3.0          # this long with no frame decoded: learn the signal again


GUARD_ORDER = ('19/128', '1/8', '1/16', '19/256', '1/32', '1/128', '1/4')   # the commoner first


def _fft_size(y, fs):
    """16k or 32k by the cyclic prefix: the normalised correlation at the useful length over
    the smallest guard (1/128, inside any guard), the larger wins; None when neither stands out."""
    best, size = 0.0, None
    seg = np.asarray(y[:int(0.08 * fs)], np.complex64)
    power = float(np.mean(np.abs(seg) ** 2)) or 1.0
    for n in (16384, 32768):
        ng = n // 128
        if len(seg) < 2 * n + ng:
            continue
        m = iqt2._cp_metric(seg, n, ng)
        score = float(np.max(np.abs(m))) / (ng * power)
        if score > best:
            best, size = score, n
    return size if best > 0.3 else None


def learn(y, fs):
    """The signal's settings from its own samples (complex64 at the elementary rate, the channel
    at 0 Hz, at least ACQUIRE_S): ``{'standard', 'fft', 'guard', 'l1'}`` as iq-measure's dvbt card
    has them, or None. A P1 in the first 0.3 s; the FFT size by the cyclic prefix; then L1 read
    with each guard and carrier mode in turn until L1-pre's CRC holds, and again with the guard
    and mode L1-pre itself says. About a second, where the card's full measurement took eight."""
    t_s = 1 / fs
    p1s = iqt2.find_p1(y[:int(0.3 * fs)], fs, t_s=t_s)
    if not p1s:
        return None
    p1 = p1s[0]
    size = _fft_size(y[p1 + 2048 + 4096:], fs)
    if size is None:
        return None
    for guard in GUARD_ORDER:
        for extended in (True, False):
            try:
                r = iqt2.read_l1(y, fs, size, guard, extended, p1, t_s=t_s)
            except (ValueError, IndexError, KeyError):
                continue
            pre = r.get('pre')
            if not pre:
                continue
            g = iqt2.GUARDS.get(pre['GUARD_INTERVAL'], guard)
            ext = bool(pre['BWT_EXT'])
            if (g, ext) != (guard, extended) or r.get('post') is None:
                try:
                    r = iqt2.read_l1(y, fs, size, g, ext, p1, t_s=t_s)
                except (ValueError, IndexError, KeyError):
                    continue
            if not r.get('pre') or r.get('post') is None:
                continue
            return {'standard': 'DVB-T2', 'fft': size, 'guard': g,
                    'l1': iqt2.summary(r, size, g, t_s=t_s)}
    return None


class T2Live:
    """The live receiver: ``put`` samples from any thread (a flowgraph's sink: it only queues
    them); a thread of its own learns the signal (``learn`` on ACQUIRE_S of them: DVB-T2 at 16k
    or 32k with its L1 read), then runs a ``T2Stream`` on what comes next. If the queue
    overflows (the thread fell behind), what does not fit is dropped and the stream searches
    for a P1 again from where it was (a marker in the queue); with no frame decoded for
    RELOCK_S it learns the signal again. A worker process that dies (the pool is broken) is
    replaced at the next learn. A lock starts near the samples arriving then: those learnt from,
    and all but the newest KEEP_S of those queued while learning, are thrown away, not counted
    as dropped (decoded first, they were seconds stale, and the picture raced to catch up). ``restart`` (a retune)
    empties the queue and forgets the frames
    still being decoded: nothing from the old channel reaches ``on_ts`` after it returns.

    Memory: the queue holds up to ``queue_s`` of samples (3 s at 64/7 MS/s is about 220 MB of
    complex64); catching up from a full queue copies it about twice more, and up to
    ``3 * workers`` frame windows (about 18 MB each at 32k) wait for the workers.

    ``fs`` is the elementary rate (the samples at it, the channel at 0 Hz). ``on_ts(bytes)`` and
    ``on_frame(dict)`` are called on its thread. ``state()`` is for a window to show."""

    def __init__(self, fs, workers=None, plp_id=None, on_ts=None, on_frame=None, queue_s=QUEUE_S):
        import queue
        import threading
        self.fs = float(fs)
        self.plp_id = plp_id
        self.workers = workers or default_workers()
        self.on_ts = on_ts or (lambda data: None)
        self.on_frame = on_frame or (lambda info: None)
        self.queue = queue.Queue()
        self.queued = 0                 # samples in the queue
        self.qlock = threading.Lock()
        self.cap = int(queue_s * self.fs)
        self.dropped_samples = 0
        self.drops_at_lock = 0          # dropped_samples when the stream last locked
        self._marked = False            # the queue ends in a LOST marker (no samples since)
        self.gen = 0                    # counts restarts: data from an older one is not passed on
        self.pool = None
        self.stream = None
        self.phase = 'idle'             # idle, starting, learning, no signal, receiving
        self.card = None
        self.error = None
        self.last_frame = None
        self.last_decoded_at = None
        self.totals = collections.Counter()
        self._stop = threading.Event()
        self._restart = threading.Event()
        self.thread = threading.Thread(target=self._run, name='t2-live', daemon=True)
        self.thread.start()

    # ---------------------------------------------------------------- from any thread

    def put(self, x):
        """Queue samples (copied); dropped, and counted, when the thread is QUEUE_S behind."""
        n = len(x)
        x = np.array(x, np.complex64)
        with self.qlock:
            if self.queued + n > self.cap:
                self.dropped_samples += n
                if not self._marked:        # the stream breaks here, after what is queued
                    self._marked = True
                    self.queue.put(_LOST)
                return
            self.queued += n
            self._marked = False
            self.queue.put(x)

    def restart(self):
        """Learn the signal again (a retune): what is queued is thrown away now, and nothing
        decoded from it, or from frames already sent off, is passed on."""
        with self.qlock:
            self.gen += 1
            self._empty_queue()
        self._restart.set()

    def _trim_queue(self, keep):
        """Throw away the oldest of what is queued until no more than ``keep`` samples are left
        (with qlock held); not counted as dropped. The stop marker stays."""
        import queue
        items = []
        while True:
            try:
                items.append(self.queue.get_nowait())
            except queue.Empty:
                break
        stop = any(y is None for y in items)
        chunks = [y for y in items if y is not None and y is not _LOST]
        while chunks and self.queued - len(chunks[0]) >= keep:
            self.queued -= len(chunks.pop(0))
        if self.queued > keep and chunks:                   # cut into the oldest one left
            cut = self.queued - keep
            chunks[0] = chunks[0][cut:]
            self.queued -= cut
        for y in chunks:
            self.queue.put(y)
        self._marked = False
        if stop:
            self.queue.put(None)

    def _empty_queue(self):
        """Throw away what is queued (with qlock held); the stop marker stays."""
        import queue
        stop = False
        while True:
            try:
                y = self.queue.get_nowait()
            except queue.Empty:
                break
            if y is None:
                stop = True
            elif y is not _LOST:
                self.queued -= len(y)
        self._marked = False
        if stop:
            self.queue.put(None)

    def state(self):
        stream = self.stream                # the thread may drop it at any moment
        st = stream.summary() if stream is not None else {}
        card = self.card or {}
        l1 = card.get('l1') or {}
        return {'phase': self.phase, 'error': self.error,
                'fft': card.get('fft'), 'guard': card.get('guard'), 'plps': l1.get('plps'),
                'frame_s': l1.get('frame_s'), 'extended': l1.get('extended'),
                'pilot_pattern': l1.get('pilot_pattern'), 'version': l1.get('version'),
                'frames': st.get('frames', 0), 'decoded': st.get('decoded', 0),
                'codewords': st.get('codewords', 0), 'codewords_ok': st.get('codewords_ok', 0),
                'packets': st.get('packets', 0), 'packets_errored': st.get('packets_errored', 0),
                'clock_ppm': st.get('clock_ppm'), 'snr_db': (self.last_frame or {}).get('snr_db'),
                'dropped_frames': st.get('dropped', 0), 'dropped_samples': self.dropped_samples,
                'dropped_since_lock': self.dropped_samples - self.drops_at_lock if stream is not None else 0,
                'queued_s': round(self.queued / self.fs, 2), 'workers': self.workers,
                'totals': dict(self.totals)}

    def close(self):
        """Stop: the thread itself forgets the frames being decoded and shuts the workers down
        as it ends (so a pool it is still making when the wait runs out is shut too), and
        nothing is passed on from here."""
        with self.qlock:
            self.gen += 1
        self._stop.set()
        self.queue.put(None)
        self.thread.join(timeout=10)

    # ---------------------------------------------------------------- the thread

    def _take(self, timeout=0.1):
        """``(samples, lost)``: everything queued up to a drop, as one array (None when nothing
        came within ``timeout``), and whether samples were dropped right after it. Taken a
        flowgraph's chunk at a time, a thousand passes a second, each waiting its turn for the
        GIL behind the window's other Python, did not keep up in the app (it dropped most of
        the samples and decoded nothing)."""
        import queue
        try:
            x = self.queue.get(timeout=timeout)
        except queue.Empty:
            return None, False
        if x is None:
            return None, False
        if x is _LOST:
            return None, True
        parts, lost = [x], False
        while True:
            try:
                y = self.queue.get_nowait()
            except queue.Empty:
                break
            if y is None:
                self.queue.put(None)
                break
            if y is _LOST:
                lost = True
                break
            parts.append(y)
        n = sum(len(p) for p in parts)
        with self.qlock:
            self.queued -= n
        return (parts[0] if len(parts) == 1 else np.concatenate(parts)), lost

    def _run(self):
        try:
            self._loop()
        finally:                                # the thread owns the stream and the workers
            if self.stream is not None:
                self.stream.cancel()
                self.stream = None
            if self.pool is not None:
                self.pool.shutdown(wait=False, cancel_futures=True)
                self.pool = None

    def _loop(self):
        learn = []
        have = 0
        self.phase = 'starting'
        try:
            self.pool = make_pool(self.workers, warm=True)
        except Exception as exc:
            self.error = f'worker processes: {type(exc).__name__}: {exc}'
        self.phase = 'learning'
        while not self._stop.is_set():
            if self._restart.is_set():
                self._restart.clear()
                self._drop_stream(cancel=True)
                learn, have = [], 0
                self.phase = 'learning'
            x, lost = self._take()
            if self._stop.is_set():
                break
            try:
                if self.stream is None:
                    if x is not None:
                        learn.append(x)
                        have += len(x)
                    if lost:                            # the next samples do not follow these
                        learn, have = [], 0
                    if have >= ACQUIRE_S * self.fs:
                        samples = np.concatenate(learn)
                        learn, have = [], 0
                        if self._learn(samples):
                            # start near the air as it is now: the samples learnt from, and all
                            # but the newest KEEP_S of those queued while learning (seconds, on
                            # a busy machine), are stale; decoded first, they made the picture
                            # race to catch up
                            with self.qlock:
                                self._trim_queue(int(KEEP_S * self.fs))
                    continue
                if x is not None:
                    self.stream.feed(x)
                else:
                    self.stream._collect(block=False)
                if lost:                                # after the samples from before the drop
                    self.totals['resets'] += 1
                    self.stream.reset()
            except Exception as exc:                    # never let the thread die silently
                self.error = f'{type(exc).__name__}: {exc}'
                if isinstance(exc, BrokenProcessPool) and self.pool is not None:
                    self.pool.shutdown(wait=False, cancel_futures=True)
                    self.pool = None                    # _learn makes a new one
                    self.totals['pool_restarts'] += 1
                self._drop_stream(cancel=True)
                learn, have = [], 0
                self.phase = 'learning'
                continue
            if (self.last_decoded_at is not None and time.monotonic() - self.last_decoded_at > RELOCK_S) \
                    or (self.last_decoded_at is None and time.monotonic() - self.started_at > RELOCK_S + 2):
                self.totals['relearns'] += 1
                self._drop_stream()
                self.phase = 'learning'

    def _learn(self, samples):
        try:
            card = learn(samples, self.fs) or {}
        except Exception as exc:
            card = {}
            self.error = f'{type(exc).__name__}: {exc}'
        l1 = card.get('l1') or {}
        if card.get('standard') != 'DVB-T2' or not l1 or card.get('fft') not in (16384, 32768):
            self.phase = 'no signal'
            self.card = card or None
            return False
        self.card = card
        self.error = None
        if self.pool is None:
            self.pool = make_pool(self.workers, warm=True)
        gen = self.gen

        def on_ts(data):                    # nothing from before a restart is passed on
            if gen == self.gen:
                self.on_ts(data)

        def on_frame(info):
            if gen == self.gen:
                self._frame(info)
        self.stream = T2Stream(self.fs, card['fft'], card['guard'], l1['extended'], l1['frame_s'],
                               plp_id=self.plp_id, workers=self.workers, on_ts=on_ts,
                               on_frame=on_frame, pool=self.pool)
        self.phase = 'receiving'
        self.drops_at_lock = self.dropped_samples
        self.started_at = time.monotonic()
        self.last_decoded_at = None
        self.totals['locks'] += 1
        return True

    def _frame(self, info):
        self.last_frame = info
        if 'error' not in info:
            self.last_decoded_at = time.monotonic()
        self.on_frame(info)

    def _drop_stream(self, cancel=False):
        """Stop using the stream: pass on what its frames give (a relearn on the same
        channel), or with ``cancel`` forget them (a retune, a broken pool, an error)."""
        if self.stream is not None:
            try:
                self.stream.cancel() if cancel else self.stream.flush()
            except Exception:
                pass
            self.stream = None
