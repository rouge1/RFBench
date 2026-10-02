#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Composite NTSC baseband, live, from whatever you point it at.

``ntsc_encode`` turns one frame into one frame's worth of composite video.
This turns a *stream* of frames into a continuous signal a flowgraph can
transmit, and answers the question of what video format this project takes:
**whatever ffmpeg reads**. There is no bespoke format to convert to.

Three things can feed it:

- a **video file** - mp4, mkv, avi, webm, anything ffmpeg decodes - scaled
  to 640x480 and letterboxed if it is not 4:3, looping forever;
- a **still frame**, which is what the instructor's ``.dat`` captures are;
- a **built-in colour-bar pattern**, so the app works with no media at all.

The ``.dat`` files do not come through here. They are already composite
video, sampled at 18 MS/s, so they are played by a plain file source and
resampled - see ``dat_resample_ratio``.

**Frames are encoded on their own thread.** Encoding a frame takes about
20 ms, and a GNU Radio work() call at 10 MS/s covers well under a
millisecond, so encoding inside work() would stall the radio for 20 ms
every 33 ms and underrun it. A small queue between the two is what keeps
the output continuous, and it paces the encoder for free: the queue fills,
the producer blocks, and frames are pulled at exactly the rate the radio
consumes them.
"""

import os
import re
import shutil
import subprocess
import sys
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from queue import Empty, Full, Queue

import numpy as np
from gnuradio import gr  # type: ignore

from apps.media import media_files, picker_name
from apps.ntsc_encode import FRAME, NTSC, NtscEncoder, IRE_BLANK, ire_to_unit

#: The instructor's ``*-18M0FS.dat`` captures are composite at this rate.
DAT_SAMPLE_RATE = 18e6

#: What the encoder samples an active line into. 640x480 is 4:3 and matches
#: the 480 active lines the frame is cut into, so one row is one line.
ACTIVE_WIDTH = 640
ACTIVE_HEIGHT = 480

#: 30000/1001, the frame rate everything in NTSC descends from.
FRAME_RATE = 1.0 / FRAME

#: Extensions worth offering, **best first**. ffmpeg reads far more than
#: this; the list is to keep the dialog sensible, not because anything else
#: would fail. The order is the preference - see ``video_files``.
VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.mov', '.m4v', '.webm', '.avi',
                    '.mpg', '.mpeg', '.ts', '.wmv', '.flv', '.ogv')


def have_ffmpeg():
    return shutil.which('ffmpeg') is not None


def video_files(directory, extensions=VIDEO_EXTENSIONS):
    """Playable video files in a directory, as (display name, full path).

    **One clip, one entry.** The media folder holds each clip twice - a
    ``.mp4`` for here and a ``.ts`` of the same picture and sound for the
    ATSC transmitter, which needs a transport stream. Offering both made the
    picker forty items long with every title in it twice, spelled
    identically, and nothing on screen said which was which. So files that
    share a name are collapsed to one, keeping the extension earliest in
    ``extensions``: by default the ``.mp4``, which is 640x480 with square
    pixels, exactly what the encoder wants, where the ``.ts`` is 704x480 at
    10:11 and would have to be stretched back. The ATSC transmitter asks
    for the ``.ts`` first instead - see ``apps/atsc_source.py``.

    Subfolders are searched too (``apps/media.py``), and "share a name"
    means share a folder as well: ``Prelinger/clip.mp4`` and
    ``Prelinger/clip.ts`` are one clip, but a ``clip.mp4`` in each of two
    folders is two, and both are offered.
    """
    best = {}
    for relative, full in media_files(directory, extensions):
        key, ext = os.path.splitext(relative)
        rank = extensions.index(ext.lower())
        if key not in best or rank < best[key][0]:
            best[key] = (rank, relative, full)
    ordered = sorted(best.items(), key=lambda kv: ('/' in kv[0], kv[0].lower()))
    return [(picker_name(relative), full)
            for _key, (_rank, relative, full) in ordered]


#: The instructor's captures are named ``<subject>-946x486-18M0FS.dat``. The
#: geometry and the sample rate are the same for every one of them, so
#: repeating it down the picker says nothing and hides the subject.
_DAT_SUFFIX = re.compile(r'-\d+x\d+-\d+M\d+FS$', re.IGNORECASE)


def dat_files(directory):
    """The ``.dat`` composite captures, as (display name, full path).

    One still frame each, sampled at 18 MS/s - see ``dat_resample_ratio``.
    Subfolders are searched too, as for the clips.
    """
    found = []
    for relative, full in media_files(directory, ('.dat',)):
        name = relative.rpartition('/')[2]
        stem = _DAT_SUFFIX.sub('', os.path.splitext(name)[0])
        found.append((picker_name(relative, stem), full))
    return found


def dat_resample_ratio(sample_rate):
    """(interpolation, decimation) to play an 18 MS/s ``.dat`` at this rate.

    The captures are one frame of composite video at 18 MS/s - 1144 samples
    a line, 525 lines, 600600 samples. Played out at any other rate without
    resampling, every timing in them is wrong by that ratio: at 10 MS/s a
    line lasts 114.4 us instead of 63.5556, which is a line rate of 8741 Hz
    where NTSC needs 15734.266. No television can lock to that, which is
    what the app used to transmit.
    """
    ratio = Fraction(int(round(sample_rate)), int(DAT_SAMPLE_RATE))
    return ratio.numerator, ratio.denominator


# --- where frames come from -------------------------------------------------

#: 75% colour bars, the standard order, white through blue.
BAR_COLOURS = [(1, 1, 1), (1, 1, 0), (0, 1, 1), (0, 1, 0),
               (1, 0, 1), (1, 0, 0), (0, 0, 1)]


def colour_bars(width=ACTIVE_WIDTH, height=ACTIVE_HEIGHT, level=0.75):
    """The test pattern the app falls back to when there is no media."""
    img = np.zeros((height, width, 3))
    for k, rgb in enumerate(BAR_COLOURS):
        img[:, k * width // 7:(k + 1) * width // 7] = np.array(rgb) * level
    return img


class FrameSource:
    """Something that produces RGB frames in 0..1, forever."""

    def next_frame(self):
        raise NotImplementedError

    def close(self):
        pass

    @property
    def description(self):
        return self.__class__.__name__


class TestPattern(FrameSource):
    """Colour bars. Needs no file and no ffmpeg."""

    def __init__(self, width=ACTIVE_WIDTH, height=ACTIVE_HEIGHT):
        self._frame = colour_bars(width, height)

    def next_frame(self):
        return self._frame

    @property
    def description(self):
        return "colour bars (built in)"


class StillImage(FrameSource):
    """One frame, held. Anything ffmpeg can open as an image."""

    def __init__(self, rgb, name="still image"):
        self._frame = np.asarray(rgb, dtype=np.float64)
        self._name = name

    def next_frame(self):
        return self._frame

    @property
    def description(self):
        return self._name


class VideoFile(FrameSource):
    """Frames decoded by ffmpeg, looping forever.

    Scaled to 640x480 and *letterboxed* rather than stretched, because most
    of the material worth transmitting here - 1950s advertising, the Blender
    open movies - is either already 4:3 or is widescreen that should not be
    squeezed into it.
    """

    def __init__(self, path, width=ACTIVE_WIDTH, height=ACTIVE_HEIGHT,
                 loop=True, frame_rate=FRAME_RATE):
        # ``frame_rate`` is the standard's, not the clip's: ffmpeg drops or
        # repeats frames to make the clip's rate this one - 29.97 for NTSC,
        # 25 for PAL, 768x576 then being PAL's square-pixel picture.
        if not have_ffmpeg():
            raise RuntimeError(
                "Playing video needs ffmpeg on PATH, and it is not installed.")
        self.path = path
        self.width, self.height = int(width), int(height)
        self._bytes = self.width * self.height * 3
        # Square the pixels before fitting. force_original_aspect_ratio
        # works on the stored width and height, not the shape on screen, so
        # a .ts at 704x480 with 10:11 pixels - the ATSC test pattern, or
        # anything the ATSC receiver records - came out 9% too short, with
        # 22 black rows top and bottom. Square pixels pass through untouched.
        scale = ("scale=iw*sar:ih,setsar=1,"
                 f"scale={self.width}:{self.height}"
                 ":force_original_aspect_ratio=decrease,"
                 f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2")
        argv = ['ffmpeg', '-hide_banner', '-loglevel', 'error']
        if loop:
            argv += ['-stream_loop', '-1']
        argv += ['-i', path, '-an', '-vf', scale,
                 '-r', f"{frame_rate:.6f}", '-f', 'rawvideo',
                 '-pix_fmt', 'rgb24', 'pipe:1']
        self._proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL)
        self._last = colour_bars(self.width, self.height)

    def next_frame(self):
        raw = self._read_exactly(self._bytes)
        if raw is None:
            return self._last          # ffmpeg ended or died; hold the picture
        frame = (np.frombuffer(raw, dtype=np.uint8)
                 .reshape(self.height, self.width, 3).astype(np.float64) / 255.0)
        self._last = frame
        return frame

    def _read_exactly(self, count):
        """A pipe read returns what it has, not what was asked for."""
        chunks, got = [], 0
        while got < count:
            try:
                piece = self._proc.stdout.read(count - got)
            except (ValueError, OSError):
                return None
            if not piece:
                return None
            chunks.append(piece)
            got += len(piece)
        return b''.join(chunks)

    def close(self):
        proc, self._proc = getattr(self, '_proc', None), None
        if proc is None:
            return
        try:
            proc.stdout.close()
        except Exception:
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()

    @property
    def description(self):
        return os.path.basename(self.path)


# --- the sound that goes with the picture -----------------------------------

#: What the clip's soundtrack is decoded to. Nothing here needs more: the
#: aural carrier is FM with 25 kHz deviation, and System M sound is mono.
AUDIO_RATE = 48000

#: Conditioning applied to a clip's sound before it modulates the aural
#: carrier, which is the same shape of processing every real station runs.
#:
#: The clips are loudness-normalised to -24 LKFS (ATSC A/85), which is
#: deliberately quiet: measured across all fourteen, peaks ran 0.30 to 1.11
#: and rms 0.038 to 0.088. Fed straight to a modulator whose full deviation
#: is |1.0|, the quietest of them would have swung +-7.5 kHz of the +-25 kHz
#: System M allows - 10 dB down, and it would have been reported as the sound
#: being too quiet rather than as a level fault.
#:
#: A fixed gain alone cannot fix it, because the *loudness* is uniform and
#: the crest factor is not: 8 dB puts speech where it belongs and clips the
#: two music-heavy Blender films on 0.17% of their samples. Compressing
#: first and then lifting gives rms 0.091-0.210 and peaks 0.64-1.54 across
#: the whole set, with the worst clip needing the rail on 0.0017% of samples
#: - one in sixty thousand. Both numbers are measured, not guessed.
#:
#: ffmpeg's own `loudnorm` would be the obvious tool and is the wrong one
#: here: its dynamic mode looks three seconds ahead, and since the picture
#: comes from a *separate* ffmpeg on the same file, that delay would land
#: as three seconds of lip-sync error.
AUDIO_FILTER = ('acompressor=threshold=0.125:ratio=4:attack=5:release=120'
                ':makeup=1,volume=8dB')


def has_audio(path):
    """Whether ffmpeg can find a soundtrack in this file.

    Asked once, of the one file that was chosen, at the moment the flowgraph
    is built - never of every file in the media folder while a dialog is
    opening, which would be fourteen subprocesses on every click.
    """
    if not path or not have_ffmpeg():
        return False
    probe = shutil.which('ffprobe')
    if probe is None:
        return False
    try:
        out = subprocess.run(
            [probe, '-v', 'error', '-select_streams', 'a:0',
             '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return b'audio' in out.stdout


class AudioTrack:
    """A video file's own soundtrack, as mono float at ``AUDIO_RATE``.

    **The picture and the sound must come from the same file.** The NTSC
    transmitter used to take its aural carrier from a ``.wav`` picked
    separately in the dialog, which was the only thing available when the
    only video it could send was a single still frame. Once it could send
    clips that carry their own sound, that left a 1950s car advertisement
    going out with an unrelated cartoon soundtrack over it.

    This is a second ffmpeg on the same file, decoding audio where
    ``VideoFile`` decodes video, and it stays in step for free: GNU Radio
    consumes exactly one audio sample per composite sample once the audio is
    resampled, so both are paced by the radio's own clock. The two loop
    together as long as the file's streams are the same length, which they
    are for anything made the way ``media/VIDEO-CREDITS.txt`` describes.

    The pipe is handed to ``blocks.file_descriptor_source`` rather than
    being read by a Python block: there is nothing to compute, and a Python
    block whose wrapper gets garbage collected takes the process down with
    it (see the FM + RDS notes).
    """

    def __init__(self, path, rate=AUDIO_RATE, loop=True, filters=AUDIO_FILTER):
        if not have_ffmpeg():
            raise RuntimeError(
                "Playing a clip's sound needs ffmpeg on PATH, and it is not "
                "installed.")
        self.path = path
        self.rate = int(rate)
        argv = ['ffmpeg', '-hide_banner', '-loglevel', 'error']
        if loop:
            argv += ['-stream_loop', '-1']
        argv += ['-i', path, '-vn']
        if filters:
            argv += ['-af', filters]
        argv += ['-ac', '1', '-ar', str(self.rate),
                 '-f', 'f32le', '-acodec', 'pcm_f32le', 'pipe:1']
        self._proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL)

    def fileno(self):
        return self._proc.stdout.fileno()

    def descriptor(self):
        """A duplicate of the pipe, for a block that will close what it is given.

        **Never hand `fileno()` straight to `blocks.file_descriptor_source`.**
        That block closes the descriptor in its destructor, and `close`
        above closes it too, so the two of them close one number twice. The
        second close lands on whatever has been opened in between - and
        what opens in between is the *next* run of the same app, whose
        pipe is handed the lowest free number, which is the one just
        released. So: launch, close, launch again, and the second run's
        sound dies the moment Python collects the first run's flowgraph,
        with ``file_descriptor_source: error: [read]: Bad file
        descriptor``. Reproduced exactly that way.

        A duplicate gives each owner its own number to close.
        """
        return os.dup(self.fileno())

    def close(self):
        proc, self._proc = getattr(self, '_proc', None), None
        if proc is None:
            return
        try:
            proc.stdout.close()
        except Exception:
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()

    @property
    def description(self):
        return os.path.basename(self.path) + " (its own sound)"


# --- the GNU Radio source ---------------------------------------------------

def encode_workers(requested=None):
    """How many threads to encode on.

    One is not enough. Encoding a frame at 10 MS/s takes 26-32 ms against a
    33.4 ms budget, measured on both machines here, so a single encoder runs
    at 1.03-1.21x real time - and that is *before* it shares the processor
    with the modulator, the resamplers and the radio sink. Off air it lost,
    and the way it lost was the transmitter going silent (see ``work``).

    numpy releases the interpreter lock inside the large array operations
    this encoder is made of, so plain threads scale: measured 1.03x, 1.62x,
    2.16x, 2.49x real time on one, two, three and four. Three is the
    default - past that the return falls off and the rest of the flowgraph
    needs cores too.
    """
    if requested:
        return max(1, int(requested))
    cores = os.cpu_count() or 1
    return 3 if cores >= 8 else (2 if cores >= 4 else 1)


class ntsc_source(gr.sync_block):
    """Composite NTSC baseband, as a stream of floats at ``sample_rate``.

    Sync tip is 0.0 and white is 1.0, which is what the transmitter expects
    to modulate. See the module docstring for why encoding happens off the
    scheduler's thread, and ``encode_workers`` for why it takes more than
    one of them.
    """

    #: Frames encoded ahead. Six is about 200 ms of slack - enough to ride
    #: out a slow decode or a garbage collection without the picture having
    #: to repeat, small enough that changing source is not delayed. It was
    #: three, which the encoder could drain during the first tenth of a
    #: second before it had built any cushion at all.
    QUEUE_DEPTH = 6

    def __init__(self, frames, sample_rate, color=True, workers=None,
                 standard=None):
        gr.sync_block.__init__(self, name='ntsc_source', in_sig=None,
                               out_sig=[np.float32])
        self.sample_rate = float(sample_rate)
        self.frames = frames
        self.color = bool(color)
        #: NTSC unless told otherwise; PAL frames have to come in at 768x576
        #: and 25 a second, which is the caller's to arrange.
        self.standard = standard or NTSC
        self.workers = encode_workers(workers)
        self.encoder = NtscEncoder(self.sample_rate, color=color,
                                   standard=self.standard)
        self._queue = Queue(maxsize=self.QUEUE_DEPTH)
        self._thread = None
        self._running = threading.Event()
        self._buf = None
        self._pos = 0
        self.frames_encoded = 0
        self.starved = 0
        self.repeats = 0
        #: The last complete frame, replayed when the encoder has not kept
        #: up. A frozen picture holds sync; blanking does not.
        self._last = None
        self._blank = np.float32(self.standard.blank)

    # -- lifecycle -------------------------------------------------------

    def start(self):
        # Encode the first frame before saying the block is ready. Otherwise
        # the ~30 ms it takes is dead air: the scheduler is already pulling
        # samples, there is nothing to give it and nothing yet to repeat, so
        # the transmitter opens with a burst of blanking.
        try:
            first = self.encoder.encode_frame(
                self.frames.next_frame()).astype(np.float32)
            self._last = first
            self._queue.put_nowait(first)
            self.frames_encoded += 1
        except Exception as exc:
            print(f"NTSC source: could not encode the first frame: {exc}",
                  file=sys.stderr)
        self._running.set()
        self._thread = threading.Thread(target=self._produce, daemon=True,
                                        name='ntsc-encode')
        self._thread.start()
        return True

    def stop(self):
        self._running.clear()
        # Unblock the producer if it is parked on a full queue.
        try:
            self._queue.get_nowait()
        except Empty:
            pass
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)
        try:
            self.frames.close()
        except Exception as exc:
            print(f"NTSC source: error closing the video source: {exc}",
                  file=sys.stderr)
        return True

    def _produce(self):
        """Read frames, encode them on several threads, queue them in order.

        Frames are independent given where each one starts, and
        ``frame_bounds`` says that without encoding anything - so the next
        frame can be dispatched while this one is still being computed. The
        results have to go into the queue in order, which is what the
        pending deque is for.
        """
        pool = ThreadPoolExecutor(max_workers=self.workers,
                                  thread_name_prefix='ntsc-encode')
        free = Queue()
        for _ in range(self.workers):
            free.put(NtscEncoder(self.sample_rate, color=self.color,
                                 standard=self.standard))
        pending = deque()
        n = self.encoder._n
        try:
            while self._running.is_set():
                while len(pending) < self.workers and self._running.is_set():
                    try:
                        frame = self.frames.next_frame()
                    except Exception as exc:
                        print(f"NTSC source: reading stopped: {exc}",
                              file=sys.stderr)
                        return
                    start, end = self.encoder.frame_bounds(n)
                    if end <= start:
                        # A frame with no samples in it can only come from a
                        # timing fault, and it must never reach work(): the
                        # queue would fill with them and nothing would ever
                        # come out. Stop instead, and say why.
                        print(f"NTSC source: empty frame at sample {start}; "
                              "stopping", file=sys.stderr)
                        return
                    pending.append(pool.submit(self._encode_one, free, frame,
                                               start))
                    n = end
                if not pending:
                    return
                try:
                    samples = pending.popleft().result()
                except Exception as exc:
                    print(f"NTSC source: encoding stopped: {exc}",
                          file=sys.stderr)
                    return
                # Put with a timeout rather than blocking forever, so stop()
                # is never waiting on a queue nobody is draining.
                while self._running.is_set():
                    try:
                        self._queue.put(samples, timeout=0.2)
                        self.frames_encoded += 1
                        break
                    except Full:
                        continue
        finally:
            for future in pending:
                future.cancel()
            pool.shutdown(wait=False)

    def _encode_one(self, free, frame, start):
        """One frame, on a borrowed encoder, starting at a known sample."""
        encoder = free.get()
        try:
            encoder._n = start
            return encoder.encode_frame(frame).astype(np.float32)
        finally:
            free.put(encoder)

    # -- streaming -------------------------------------------------------

    def work(self, input_items, output_items):
        """Samples out, and **never** a pause.

        This waited a tenth of a second for a frame before giving up, which
        turned a late frame into a *silent transmitter*: nothing came out of
        the block, so nothing reached the radio, and off air the carrier was
        simply absent for up to 100 ms at a time. Measured on the VSG60 with
        a real clip, before the encoder was given more threads: 74 gaps in
        three seconds, the longest 18.6 ms, 25% of the air time missing -
        and the picture still decoded, so only a look at the envelope showed
        it at all. The same trap as the HackRF ATSC case in knowledge/atsc.md.

        So the wait is two milliseconds, long enough to catch a frame that
        is about to arrive and far too short to take the transmitter off
        air, and what fills the gap is the *previous* frame rather than
        blanking. A frozen picture keeps sync pulses and colour burst
        coming; blanking is a level, and a receiver loses lock on it.
        """
        out = output_items[0]
        want = len(out)
        done = 0
        while done < want:
            if self._buf is None or self._pos >= self._buf.size:
                try:
                    buf = self._queue.get(timeout=0.002)
                    if buf.size == 0:
                        # Never adopt an empty frame, and never keep one to
                        # replay: replaying it is a loop that makes no
                        # progress, and the block would never return.
                        continue
                    self._buf, self._pos = buf, 0
                    self._last = buf
                except Empty:
                    if not self._running.is_set():
                        return -1 if done == 0 else done
                    if self._last is not None:
                        self._buf, self._pos = self._last, 0
                        self.repeats += 1
                        continue
                    out[done:want] = self._blank
                    self.starved += want - done
                    return want
            take = min(want - done, self._buf.size - self._pos)
            out[done:done + take] = self._buf[self._pos:self._pos + take]
            self._pos += take
            done += take
        return want
