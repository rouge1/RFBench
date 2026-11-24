"""Watching analogue television: every frame, and the sound in step with it.

``ntsc_frame_sink`` (in ``ntscReceiver``) decodes one picture out of every
1.7 frames of signal, on one thread, and hands it to a player that is told
a frame rate and shows pictures on its own clock while the sound goes to the
speakers by another path. Fine for reading the meters; not television.
Watched off a cable on 2026-11-24 it showed about 14 of PAL's 25 frames a
second, jerkily, and the sound drifted out of step with the picture by a
different amount every time Watch was pressed. That is what this replaces,
for the NTSC and PAL receivers:

- **Every frame is decoded.** Windows of 1.7 frames step forward exactly
  one frame at a time, so the first whole frame in each is the next frame
  of the broadcast. A PAL frame takes about 41 ms to decode against 40 of
  signal, so several worker *processes* decode consecutive frames at once
  and the results are put back in order. Threads were tried first and
  passed a test fed by GNU Radio's own blocks, then lost samples off the
  air: the BB60D's driver block is Python too, and three decoding threads
  holding the interpreter lock kept it from draining the radio - "Sample
  Overrun" every second and 20 frames a second of 25. Spawned processes
  each have their own lock; what comes back is 8-bit RGB, 1.3 MB a frame.
- **The count never slips.** A frame that fails, or whose window had to be
  dropped because the workers were behind, is replaced by the previous
  picture, so exactly one picture goes out per frame of signal - the
  radio's clock, not the computer's, sets the pace.
- **Sound and picture go to one player, from one clock.** The demodulated
  sound is tapped (``AudioTap``) and each picture goes out with the sound
  received during it - located from where the decoder found the frame,
  converted to 48 kHz samples - into one ffmpeg, which muxes them with
  timestamps for ffplay. ffplay then keeps them in step itself, against the
  sound card's clock. While watching, the receiver's own speaker path is
  muted so the sound is not heard twice.
"""

import multiprocessing
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np  # type: ignore
from gnuradio import gr  # type: ignore

from apps.ntsc_decode import NtscDecoder, SYNC_DISCRIMINANT
from apps.ntscReceiver import ntsc_frame_sink

AUDIO_RATE = 48000

# --- the decoding, in a worker process -------------------------------------

_WORKER = {}


def _worker_init(sample_rate, standard_key):
    from apps.ntsc_encode import STANDARDS
    std = STANDARDS[standard_key]
    _WORKER['std'] = std
    _WORKER['rate'] = float(sample_rate)
    _WORKER['decoder'] = NtscDecoder(sample_rate, standard=std)


def _measure(decoder, x, sample_rate, std):
    """Sync and blanking levels and the line rate, as the window shows them."""
    sync, blank = decoder.levels(x)
    starts, widths = decoder.find_pulses(x, sync, blank)
    status = {'sync': sync, 'blank': blank, 'pulses': starts.size,
              'line_rate': 0.0, 'locked': True}
    horizontal = starts[widths > SYNC_DISCRIMINANT]
    if horizontal.size > 20:
        gaps = np.diff(horizontal)
        nominal = sample_rate / std.line_rate
        one = gaps[(gaps > 0.9 * nominal) & (gaps < 1.1 * nominal)]
        if one.size > 10:
            period = float(one.mean()) / sample_rate
            status['line_rate'] = 1.0 / period if period > 0 else 0.0
    return status


def _decode_in_worker(window):
    """One window of composite (float32) to (rgb uint8, start, swapped, status).

    Raises whatever the decoder raises on a window with no picture in it;
    the caller counts that as a failure.
    """
    decoder, std, rate = _WORKER['decoder'], _WORKER['std'], _WORKER['rate']
    x = window.astype(np.float64)
    status = _measure(decoder, x, rate, std)
    frame = decoder.decode_frame(x, color=True)
    rgb = (np.clip(frame, 0, 1) * 255).astype(np.uint8)
    return rgb, decoder.last_frame_start, decoder.last_swapped, status


class AudioTap(gr.sync_block):
    """Keeps the last few seconds of demodulated sound, by absolute sample.

    Fed at 48 kHz from the sound chain's un-muted output. ``read(start,
    count)`` gives back samples ``start`` to ``start + count`` of the whole
    run - zeros for any that have not arrived or have already gone.
    """

    SECONDS = 8
    #: Taken in vectors of 20 ms, fed by a stream_to_vector in front of it:
    #: a Python block called with whatever GNU Radio has - a few samples at
    #: a time - spent a third of a core on the interpreter lock that the
    #: BB60D's own Python block needs to drain the radio.
    VLEN = 960

    def __init__(self, rate=AUDIO_RATE):
        gr.sync_block.__init__(self, name='audio_tap',
                               in_sig=[(np.float32, self.VLEN)], out_sig=None)
        self.rate = int(rate)
        self._size = self.rate * self.SECONDS
        self._ring = np.zeros(self._size, np.float32)
        self._total = 0
        self._lock = threading.Lock()

    def work(self, input_items, output_items):
        data = input_items[0].reshape(-1)
        n = len(data)
        with self._lock:
            if n >= self._size:
                self._ring[:] = data[-self._size:]
            else:
                at = self._total % self._size
                first = min(n, self._size - at)
                self._ring[at:at + first] = data[:first]
                self._ring[:n - first] = data[first:]
            self._total += n
        return len(input_items[0])

    def total(self):
        return self._total

    def read(self, start, count):
        out = np.zeros(count, np.float32)
        with self._lock:
            lo = max(start, self._total - self._size)
            hi = min(start + count, self._total)
            if hi > lo:
                idx = np.arange(lo, hi) % self._size
                out[lo - start:hi - start] = self._ring[idx]
        return out


class StreamingFrameSink(ntsc_frame_sink):
    """``ntsc_frame_sink`` that decodes every frame and feeds a synced player.

    Same readout as the block it extends - ``frame``, ``frames_decoded``,
    ``frames_dropped``, ``failures``, ``status`` - so the receivers' windows
    need nothing new, except that the frame rate to expect is now the
    standard's own (``FRAMES_PER_PICTURE`` is 1).
    """

    FRAMES_PER_PICTURE = 1.0
    #: Input in vectors of 50,000 samples, 4 ms of PAL - a few hundred calls
    #: a second instead of thousands, for the interpreter lock's sake (see
    #: AudioTap.VLEN). The receiver puts a stream_to_vector in front.
    VLEN = 50000
    #: How many frames may be decoding at once before a window is dropped.
    BACKLOG_PER_WORKER = 2

    def __init__(self, sample_rate, standard=None, workers=None, tap=None):
        super().__init__(sample_rate, standard=standard)
        std = self.standard
        self.frame_len = std.frame * self.sample_rate        # samples, maybe fractional
        cost = 1.1 * (std.width * std.height) / (640 * 480)   # PAL ~1.6x NTSC
        self.workers = workers or (3 if cost > 1.2 else 2)
        self.tap = tap
        # A ring of signal several windows long, by absolute sample.
        self._ring_size = int(4 * self._need)
        self._ring = np.zeros(self._ring_size, np.float32)
        self._total = 0
        self._next_start = 0.0
        self._seq = 0
        self._results = {}
        self._results_lock = threading.Lock()
        self._results_ready = threading.Condition(self._results_lock)
        self._pending = 0
        self._pool = None
        self._out_seq = 0
        self._last_rgb = None
        self._audio_cursor = None
        self._player_procs = []
        self._video_pipe = None
        self._audio_pipe = None
        self._video_q = None
        self._audio_q = None
        self._emitter = None
        self.frames_repeated = 0
        self._aligned = False
        self._realign = 0.0
        #: Frames whose sound had still not arrived a second later.
        self.audio_short = 0

    # -- lifecycle -------------------------------------------------------

    # The newest picture, kept as 8-bit RGB as it comes back from a worker
    # and turned into the 0..1 floats the window's readout expects only
    # when something asks - twice a second, not 25 times.
    @property
    def frame(self):
        rgb = getattr(self, '_rgb', None)
        return None if rgb is None else rgb.astype(np.float64) / 255.0

    @frame.setter
    def frame(self, value):
        self._rgb = None if value is None else value

    def start(self):
        self._running.set()
        # Spawned, not forked: the parent has GNU Radio and Qt threads
        # running, which a fork would copy in whatever state they were in.
        self._pool = ProcessPoolExecutor(
            max_workers=self.workers,
            mp_context=multiprocessing.get_context('spawn'),
            initializer=_worker_init,
            initargs=(self.sample_rate, self.standard.key))
        self._emitter = threading.Thread(target=self._emit_loop, daemon=True,
                                         name='tv-emit')
        self._emitter.start()
        return True

    def stop(self):
        self._running.clear()
        with self._results_ready:
            self._results_ready.notify_all()
        pool, self._pool = self._pool, None
        if pool is not None:
            # **End the workers, do not just ask them to finish.** Shut down
            # without waiting while they were still being spawned - a
            # window closed within a second or two of opening - and they
            # could be left running, holding the parent's stdout open: the
            # window test sat for its whole three-minute timeout on that.
            procs = list(getattr(pool, '_processes', {}).values())
            pool.shutdown(wait=False, cancel_futures=True)
            for proc in procs:
                try:
                    proc.terminate()
                except Exception:
                    pass
            for proc in procs:
                try:
                    proc.join(timeout=2.0)
                except Exception:
                    pass
        self.stop_player()
        return True

    # -- streaming -------------------------------------------------------

    def work(self, input_items, output_items):
        items = len(input_items[0])
        data = input_items[0].reshape(-1)
        n = len(data)
        at = self._total % self._ring_size
        first = min(n, self._ring_size - at)
        self._ring[at:at + first] = data[:first]
        if n > first:
            self._ring[:n - first] = data[first:]
        self._total += n
        if self._realign:
            self._next_start += self._realign
            self._realign = 0.0
        while self._total >= int(self._next_start) + self._need:
            start = int(self._next_start)
            seq = self._seq
            self._seq += 1
            self._next_start += self.frame_len
            if start < self._total - self._ring_size:
                self._finish(seq, None, start)        # overwritten already
                continue
            with self._results_lock:
                busy = self._pending >= self.workers * self.BACKLOG_PER_WORKER
                if not busy:
                    self._pending += 1
            if busy or self._pool is None:
                self.frames_dropped += 1
                self._finish(seq, None, start)
                continue
            a = start % self._ring_size
            if a + self._need <= self._ring_size:
                window = self._ring[a:a + self._need].copy()
            else:
                window = np.concatenate((self._ring[a:],
                                         self._ring[:a + self._need - self._ring_size]))
            try:
                future = self._pool.submit(_decode_in_worker, window)
            except RuntimeError:                     # pool shut down
                return items
            future.add_done_callback(
                lambda f, seq=seq, start=start: self._decoded(seq, start, f))
        return items

    def _decoded(self, seq, start, future):
        rgb, frame_start = None, None
        try:
            rgb, offset, swapped, status = future.result()
            frame_start = start + offset
            if swapped and not self._aligned:
                # The window opened on field two, so this picture is field
                # two of one frame woven with field one of the next - and
                # since windows step a whole frame, so would every picture
                # after it. Moving the windows on by half a frame, once,
                # makes each picture one broadcast frame, field one then
                # field two, as a television shows it.
                self._realign = 0.5 * self.frame_len
            self._aligned = True
            self.status = status
            self.frame = rgb
            self.frames_decoded += 1
        except Exception:
            self.failures += 1
        with self._results_lock:
            self._pending -= 1
        self._finish(seq, rgb, frame_start)

    def _finish(self, seq, frame, frame_start):
        with self._results_ready:
            self._results[seq] = (frame, frame_start)
            self._results_ready.notify_all()

    # -- in order, one picture per frame ---------------------------------

    def _emit_loop(self):
        """Take results in sequence and send each to the player, if any.

        Waits for the next frame in order; one still missing after a frame
        and a half beyond when the next would have arrived is given up on
        and the previous picture goes out in its place.
        """
        timeout = 3 * self.standard.frame
        while self._running.is_set():
            with self._results_ready:
                if self._out_seq not in self._results:
                    self._results_ready.wait(timeout)
                if self._out_seq not in self._results:
                    later = [s for s in self._results if s > self._out_seq]
                    if not later:
                        continue
                    result = (None, None)                # late: give up on it
                else:
                    result = self._results.pop(self._out_seq)
                for s in [s for s in self._results if s < self._out_seq]:
                    self._results.pop(s)
                seq = self._out_seq
                self._out_seq += 1
            self._emit(seq, *result)

    def _emit(self, seq, frame, frame_start):
        if frame is None:
            if self._last_rgb is None:
                return
            rgb = self._last_rgb
            self.frames_repeated += 1
        else:
            rgb = frame.tobytes()
            self._last_rgb = rgb
        if self._video_q is None:
            return
        self._put(self._video_q, rgb)
        if self._audio_q is not None and self.tap is not None:
            per_frame = AUDIO_RATE * self.standard.frame
            if self._audio_cursor is None:
                if frame_start is None:
                    return
                # The sound that was on the air when this picture was.
                self._audio_cursor = frame_start / self.sample_rate * AUDIO_RATE
            start = int(round(self._audio_cursor))
            self._audio_cursor += per_frame
            count = int(round(self._audio_cursor)) - start
            # **Wait for this frame's sound to arrive.** The sound chain
            # decimates 20 MS/s by 100 and then resamples, and GNU Radio
            # moves it in buffers, so it reaches the tap a good deal after
            # the picture of the same instant reaches the decoder. Read at
            # once, the newest frame's sound was not there yet and came back
            # as silence: off a test signal the beeps arrived 199 ms after
            # their flashes, a frame at a time chopped into dropouts. The
            # picture waits instead - a fixed delay, the same for both.
            deadline = time.time() + 1.0
            while self.tap.total() < start + count and time.time() < deadline \
                    and self._running.is_set():
                time.sleep(0.005)
            if self.tap.total() < start + count:
                self.audio_short += 1
            audio = self.tap.read(start, count) * self.volume
            self._put(self._audio_q, audio.astype(np.float32).tobytes())

    def _put(self, q, item):
        with q[1]:
            q[0].append(item)
            if len(q[0]) > 50:          # a player that stopped reading
                q[0].pop(0)
                self.queue_dropped += 1
            q[2].set()

    queue_dropped = 0

    # -- the player ------------------------------------------------------

    volume = 0.5

    def player_argv(self, title='TV'):
        """ffmpeg (muxing picture and sound) piped into ffplay, or None."""
        if not (shutil.which('ffmpeg') and shutil.which('ffplay')):
            return None
        std = self.standard
        return std, title

    #: What plays the muxed stream from stdin. A test swaps it for an
    #: ffmpeg that records what Watch would have shown.
    play_cmd = None

    def start_player(self, argv):
        """Start ffmpeg and ffplay; ``argv`` is ``player_argv``'s answer."""
        self.stop_player()
        std, title = argv
        size = f"{std.width}x{std.height}"
        rate = f"{std.frame_rate:.6f}"
        video_r, video_w = os.pipe()
        # **No probing.** By default ffmpeg reads seconds of each input to
        # analyse it before it starts, and opens the inputs one after the
        # other - so it sat reading sound while the picture pipe filled, and
        # the queue to it dropped 55 pictures in the first six seconds of a
        # test, which put every picture 0.8 s ahead of its sound for the
        # rest of the session. Both formats are given in full here, so there
        # is nothing to find out.
        quick = ['-probesize', '32', '-analyzeduration', '0',
                 '-fflags', 'nobuffer']
        cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error'] + quick + [
               '-f', 'rawvideo', '-pixel_format', 'rgb24', '-video_size', size,
               '-framerate', rate, '-i', f'pipe:{video_r}']
        fds = [video_r]
        audio_w = None
        if self.tap is not None:
            audio_r, audio_w = os.pipe()
            cmd += quick + ['-f', 'f32le', '-ar', str(AUDIO_RATE), '-ac', '1',
                            '-i', f'pipe:{audio_r}']
            fds.append(audio_r)
        cmd += ['-c:v', 'rawvideo', '-c:a', 'pcm_f32le', '-f', 'nut', 'pipe:1']
        mux = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, pass_fds=fds)
        for fd in fds:
            os.close(fd)
        play = subprocess.Popen(
            self.play_cmd or
            ['ffplay', '-hide_banner', '-loglevel', 'error',
             '-window_title', title, '-f', 'nut', '-i', 'pipe:0'],
            stdin=mux.stdout, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        mux.stdout.close()
        self._player_procs = [mux, play]
        self._player = play
        self._video_pipe = os.fdopen(video_w, 'wb', buffering=0)
        self._video_q = ([], threading.Lock(), threading.Event())
        threads = [threading.Thread(target=self._pipe_loop,
                                    args=(self._video_q, self._video_pipe),
                                    daemon=True, name='tv-video-out')]
        if audio_w is not None:
            self._audio_pipe = os.fdopen(audio_w, 'wb', buffering=0)
            self._audio_q = ([], threading.Lock(), threading.Event())
            threads.append(threading.Thread(
                target=self._pipe_loop, args=(self._audio_q, self._audio_pipe),
                daemon=True, name='tv-audio-out'))
        self._audio_cursor = None
        self._writers = threads
        for t in threads:
            t.start()
        return play

    def _pipe_loop(self, q, pipe):
        items, lock, wake = q
        while True:
            wake.wait(0.2)
            wake.clear()
            while True:
                with lock:
                    if not items:
                        break
                    item = items.pop(0)
                try:
                    pipe.write(item)
                except (OSError, ValueError, BrokenPipeError):
                    return
            if q is not self._video_q and q is not self._audio_q:
                return

    def stop_player(self):
        vq, aq = self._video_q, self._audio_q
        self._video_q = self._audio_q = None
        for q in (vq, aq):
            if q is not None:
                q[2].set()
        for pipe in (self._video_pipe, self._audio_pipe):
            if pipe is not None:
                try:
                    pipe.close()
                except Exception:
                    pass
        self._video_pipe = self._audio_pipe = None
        for proc in self._player_procs:
            if proc.poll() is None:
                proc.terminate()
        self._player_procs = []
        self._player = None
        self._audio_cursor = None

    def player_alive(self):
        return self._player is not None and self._player.poll() is None
