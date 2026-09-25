#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GNU Radio sink block for the Signal Hound VSG60 vector signal generator.

The VSG60 has no SoapySDR module and no native GNU Radio block, so this
wraps the vendor C API (``libvsg_api.so``) with ctypes and exposes it as a
``gr.sync_block``.

The block deliberately mirrors the setter names used by ``uhd.usrp_sink`` and
``soapy.sink`` so it drops into the existing ``if radio_type == ...`` branches
in the app modules without special-casing every call site.

Hardware limits (measured on firmware 6, API 1.0.3):
    frequency    30 MHz .. 6 GHz
    sample rate  12.5 kS/s .. 50 MS/s
    level        -120 .. +10 dBm   (calibrated absolute output power)
"""

import contextlib
import ctypes
import glob
import os
import re
import threading

import numpy as np
from gnuradio import gr  # type: ignore

# Hardware limits - values outside these are clamped by the device itself,
# we clamp first so the reported settings match what is actually applied.
FREQ_MIN_HZ = 30e6
FREQ_MAX_HZ = 6e9
RATE_MIN = 12.5e3
RATE_MAX = 50e6
LEVEL_MIN_DBM = -120.0
LEVEL_MAX_DBM = 10.0

_LIB_NAMES = ('libvsg_api.so.1', 'libvsg_api.so')

# The vendor library ships inside the Sceptre install rather than a system
# prefix, and that directory is named after the Sceptre version, so the path is
# different on every machine. Search directories rather than one hardcoded
# version: /opt/sceptre is the symlink the installer points at the current
# install, and the installer directory may hold several versions side by side.
# VSG_API_LIB overrides for setups that keep the library somewhere else.
# vendor/ sits next to this checkout so a machine without a Sceptre install can
# hold the library with the code it belongs to. It is gitignored: the library is
# proprietary vendor code with no redistribution grant, so it must not be
# committed - see README for how to put it there.
_VENDOR_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'vendor')

_LIB_SEARCH_DIRS = [
    _VENDOR_DIR,
    '/opt/sceptre/lib',
    '/opt/sceptre-installer/*/lib',
    '/usr/local/lib',
    '/usr/lib',
]


def _version_key(path):
    """Order sceptre-5.11.0 above sceptre-5.6.3 - numerically, not lexically."""
    match = re.search(r'sceptre-(\d+(?:\.\d+)*)', path)
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split('.'))


def _candidate_paths():
    """Every path to try, best first, for this machine's layout."""
    candidates = []

    override = os.environ.get('VSG_API_LIB', '')
    if override:
        # Accept the library itself or the directory holding it - pointing the
        # variable at a directory is the easier mistake to make.
        if os.path.isdir(override):
            candidates += [os.path.join(override, name) for name in _LIB_NAMES]
        else:
            candidates.append(override)

    for pattern in _LIB_SEARCH_DIRS:
        matches = []
        for name in _LIB_NAMES:
            matches += glob.glob(os.path.join(pattern, name))
        # Newest version first within one search location; the locations
        # themselves stay in the order listed above.
        candidates += sorted(matches, key=_version_key, reverse=True)

    # Drop duplicates - /opt/sceptre is normally a symlink to one of the
    # versioned directories - while keeping the order above.
    seen = set()
    ordered = []
    for path in candidates:
        key = os.path.realpath(path)
        if key not in seen:
            seen.add(key)
            ordered.append(path)

    # Last resort: let the dynamic loader look on its own search path, for
    # installs that have run ldconfig or that set LD_LIBRARY_PATH.
    return ordered + list(_LIB_NAMES)


_lib = None


def _load_library():
    """Load libvsg_api once and cache it. Raises RuntimeError if unavailable."""
    global _lib
    if _lib is not None:
        return _lib

    failures = []
    for path in _candidate_paths():
        try:
            lib = ctypes.CDLL(path)
        except OSError as e:
            failures.append((path, e))
            continue

        # Signatures. Everything returns VsgStatus (int); <0 is an error,
        # >0 is a warning (e.g. a clamped setting).
        lib.vsgGetAPIVersion.restype = ctypes.c_char_p
        lib.vsgGetErrorString.restype = ctypes.c_char_p
        lib.vsgGetErrorString.argtypes = [ctypes.c_int]
        lib.vsgOpenDevice.argtypes = [ctypes.POINTER(ctypes.c_int)]
        lib.vsgOpenDeviceBySerial.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int]
        lib.vsgCloseDevice.argtypes = [ctypes.c_int]
        lib.vsgAbort.argtypes = [ctypes.c_int]
        lib.vsgPreset.argtypes = [ctypes.c_int]
        lib.vsgGetDeviceList.argtypes = [ctypes.POINTER(ctypes.c_int),
                                         ctypes.POINTER(ctypes.c_int)]
        lib.vsgSetFrequency.argtypes = [ctypes.c_int, ctypes.c_double]
        lib.vsgSetLevel.argtypes = [ctypes.c_int, ctypes.c_double]
        lib.vsgSetSampleRate.argtypes = [ctypes.c_int, ctypes.c_double]
        lib.vsgSetRFOutputState.argtypes = [ctypes.c_int, ctypes.c_int]
        lib.vsgGetSerialNumber.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        lib.vsgSubmitIQ.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_float),
                                    ctypes.c_int]
        lib.vsgFlushAndWait.argtypes = [ctypes.c_int]
        # Waveform mode - see the section of that name below. Bound in a
        # try, unlike everything above it, because an older vendor library
        # need not export these three, and an AttributeError here would
        # take the whole VSG down rather than just this one feature.
        try:
            # The length is in complex samples, as vsgSubmitIQ's is, and
            # both calls copy the buffer before returning.
            lib.vsgRepeatWaveform.argtypes = [ctypes.c_int,
                                              ctypes.POINTER(ctypes.c_float),
                                              ctypes.c_int]
            lib.vsgOutputWaveform.argtypes = [ctypes.c_int,
                                              ctypes.POINTER(ctypes.c_float),
                                              ctypes.c_int]
            # This one writes a 4-byte int, not a C++ bool: a ctypes.c_bool
            # would leave three of those bytes in whatever sits after it.
            lib.vsgIsWaveformActive.argtypes = [ctypes.c_int,
                                                ctypes.POINTER(ctypes.c_int)]
        except AttributeError:
            pass
        _lib = lib
        return _lib

    # Report where we looked and every path tried, not just the last failure.
    # When nothing is installed the only candidates are the bare sonames, whose
    # "cannot open shared object file" says nothing about which directories
    # were searched - which is exactly what someone debugging a new machine
    # needs to know.
    tried = "\n".join("  %s\n    %s" % (path, error) for path, error in failures)
    override = os.environ.get('VSG_API_LIB', '')
    raise RuntimeError(
        "Signal Hound VSG API library (libvsg_api.so) not found. Install the "
        "Signal Hound / Sceptre software, or set VSG_API_LIB to the full path "
        "of libvsg_api.so.\n\n"
        "VSG_API_LIB: %s\n"
        "Searched: %s\n\n"
        "Tried:\n%s" % (override or "(not set)",
                        ", ".join(_LIB_SEARCH_DIRS),
                        tried or "  (nothing)"))


def _err(lib, status):
    return lib.vsgGetErrorString(status).decode('utf-8', 'replace')


def is_available():
    """True if the vendor library can be loaded (does not touch hardware)."""
    try:
        _load_library()
        return True
    except RuntimeError:
        return False


def library_error():
    """The reason the library could not be loaded, or '' if it loaded fine.

    Lets a caller tell "the software is not installed" apart from "no device is
    plugged in", which are the same message to the user otherwise.
    """
    try:
        _load_library()
        return ''
    except RuntimeError as e:
        return str(e)


def find_devices():
    """Return the serial numbers of attached VSG units.

    Does not open the device, so it is safe to call for pre-launch validation
    while nothing else is using the hardware.
    """
    lib = _load_library()
    serials = (ctypes.c_int * 8)()
    # The count is in/out: it must be primed with the array capacity or the
    # API reports zero devices.
    count = ctypes.c_int(8)
    status = lib.vsgGetDeviceList(serials, ctypes.byref(count))
    if status < 0:
        raise RuntimeError("vsgGetDeviceList failed: %s" % _err(lib, status))
    return [serials[i] for i in range(max(0, count.value))]


# The vendor library guards single-client access with C assert(), which calls
# abort() - a second vsgOpenDevice on a held device kills the process outright
# with SIGABRT before Python sees anything, and can leave the unit needing a USB
# reset. It cannot be caught, only avoided, and vsgGetDeviceList still lists a
# device another process is holding, so discovery cannot tell us either. Hence
# this advisory PID lock: it turns the abort into a normal Python exception for
# anything that goes through this module.
_LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          os.pardir, 'config', '.vsg60.lock')


def _lock_holder():
    """PID of the process holding the VSG, or None if free or stale."""
    try:
        with open(_LOCK_PATH) as handle:
            pid = int(handle.read().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)  # liveness probe only, sends no signal
    except OSError:
        return None      # holder died without cleaning up
    return pid


def in_use():
    """True if another live process holds the VSG through this module.

    Only sees users that go through this code - an external Signal Hound
    application holding the device is invisible here.
    """
    return _lock_holder() is not None


def _acquire_lock():
    """Claim the device, or raise RuntimeError naming the current holder."""
    for _ in range(2):
        try:
            fd = os.open(_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            holder = _lock_holder()
            if holder is not None:
                raise RuntimeError(
                    "Signal Hound VSG is already in use by process %d. Close "
                    "that flowgraph before starting another one." % holder)
            # Stale lock from a crashed run - clear it and retry once.
            try:
                os.unlink(_LOCK_PATH)
            except OSError:
                pass
            continue
        except OSError as e:
            print("Warning: could not create VSG lock (%s); continuing" % e)
            return
        with os.fdopen(fd, 'w') as handle:
            handle.write(str(os.getpid()))
        return


def _release_lock():
    if _lock_holder() == os.getpid():
        try:
            os.unlink(_LOCK_PATH)
        except OSError:
            pass


class vsg_sink(gr.sync_block):
    """Complex baseband sink driving a Signal Hound VSG60.

    ``vsgSubmitIQ`` blocks once the device's internal queue is full, which
    gives the flowgraph real hardware backpressure - no throttle block is
    needed. ctypes releases the GIL for the call, so the Qt GUI stays live
    while the work thread is parked in the driver.

    The vendor API is not thread safe: a setter called from the Qt thread
    while the work thread sits in ``vsgSubmitIQ`` corrupts the device state
    and every later call fails. All API calls are therefore serialised on
    ``_lock``. A setter waits at most one buffer (~100 ms) for an in-flight
    submit, which is imperceptible on a slider.
    """

    def __init__(self, center_freq=915e6, sample_rate=1e6, level_dbm=-40.0,
                 serial=None):
        gr.sync_block.__init__(self, name="vsg_sink",
                               in_sig=[np.complex64], out_sig=None)

        self._lib = _load_library()
        self._device = ctypes.c_int(-1)
        self._closed = True
        self._holds_lock = False
        self._hold = 0                  # held_open() depth
        self._lock = threading.RLock()
        self._serial = int(serial) if serial else None
        # What the device should be set to. Kept while it is closed, so a
        # reopen programs it exactly as it was.
        self._center_freq = float(center_freq)
        self._sample_rate = float(sample_rate)
        self._level_dbm = float(level_dbm)
        self._open()

        print("Signal Hound VSG %d: %.6f MHz, %.4f MS/s, %.1f dBm"
              % (self._serial, self._center_freq / 1e6,
                 self._sample_rate / 1e6, self._level_dbm))

    def _open(self):
        """Claim the device, open it and program the settings held here."""
        # Must happen before the open: a second open would abort the process.
        _acquire_lock()
        self._holds_lock = True
        try:
            if self._serial:
                status = self._lib.vsgOpenDeviceBySerial(
                    ctypes.byref(self._device), int(self._serial))
            else:
                status = self._lib.vsgOpenDevice(ctypes.byref(self._device))
            if status < 0:
                raise RuntimeError("Could not open Signal Hound VSG: %s"
                                   % _err(self._lib, status))
        except Exception:
            _release_lock()
            self._holds_lock = False
            raise
        self._closed = False

        sn = ctypes.c_int(0)
        self._lib.vsgGetSerialNumber(self._device, ctypes.byref(sn))
        self._serial = sn.value

        # Bring the level up only after frequency and rate are programmed, so
        # the first thing emitted is the intended signal.
        level_dbm = self._level_dbm
        self.set_level(LEVEL_MIN_DBM)
        self.set_sample_rate(0, self._sample_rate)
        self.set_frequency(0, self._center_freq)
        self.set_level(level_dbm)
        self._lib.vsgSetRFOutputState(self._device, 1)

    # --- streaming -------------------------------------------------------
    def start(self):
        # GNU Radio calls stop() and then start() on every block whenever a
        # running flowgraph is locked and unlocked, which is how the FM + RDS
        # transmitter's Next Track swaps its audio chain. stop() must close the
        # device, as it is also the only notice of a real shutdown, so reopen
        # it here. Without this the VSG stayed closed after Next Track, work()
        # reported done, and the whole broadcast ended with no error at all.
        if self._closed:
            self._open()
        elif self.waveform_active():
            # vsgSubmitIQ does not stop a repeating waveform - only
            # vsgAbort and the two waveform calls do - so a flowgraph
            # started over one left playing would have both feeding the
            # modulator at once.
            self.stop_waveform()
        return True

    def work(self, input_items, output_items):
        if self._closed:
            return -1

        samples = input_items[0]
        # complex64 is already interleaved float32 I/Q, so the view is free.
        buf = np.ascontiguousarray(samples).view(np.float32)
        with self._lock:
            if self._closed:
                return -1
            status = self._lib.vsgSubmitIQ(
                self._device,
                buf.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                len(samples))
        if status < 0:
            print("VSG submit error: %s" % _err(self._lib, status))
            return -1
        return len(samples)

    def stop(self):
        if self._hold:
            # A lock() inside held_open(): the work thread has already left the
            # driver, so keep the device open and streaming resumes the moment
            # start() runs.
            return True
        self._shutdown()
        return True

    @contextlib.contextmanager
    def held_open(self):
        """Keep the device open across a flowgraph lock() and unlock().

        Reopening a VSG60 takes 4.7 s, measured - that much dead air, and the
        caller of unlock() frozen for all of it - while the open device takes
        samples again straight away. Wrap a rebuild of a running flowgraph in
        this. A stop() outside it still closes the device and frees it, which
        the launcher relies on to open it again later in the same process.
        """
        self._hold += 1
        try:
            yield
        finally:
            self._hold -= 1

    # --- waveform mode ---------------------------------------------------
    # Streaming through work() is the normal path and needs a flowgraph
    # feeding it. A short burst - a 433 MHz frame and the silence after it -
    # is better handed over whole instead: the device plays it out of its own
    # memory, so the timing between the pulses is the VSG's clock rather than
    # whatever USB and the host scheduler did that second, and there is
    # nothing left to underrun. Build the block on its own for this, with no
    # top block around it; work() never runs. The two modes do not mix, and
    # start() ends a repeat rather than stream on top of it.

    def _waveform_call(self, name, samples):
        """Hand one complex64 buffer to whichever waveform call `name` is."""
        func = getattr(self._lib, name, None)
        if func is None:
            raise RuntimeError(
                "This libvsg_api does not export %s. The installed Signal "
                "Hound software is too old for waveform mode." % name)
        iq = np.ascontiguousarray(samples, dtype=np.complex64)
        if iq.size == 0:
            raise ValueError("waveform is empty")
        with self._lock:
            if self._closed:
                raise RuntimeError("Signal Hound VSG is closed")
            # complex64 is already interleaved float32 I/Q, so the view is
            # free; the length stays a count of complex samples. The library
            # copies into its own buffer before it returns, so iq does not
            # have to outlive the call.
            buf = iq.view(np.float32)
            status = func(self._device,
                          buf.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                          iq.size)
        if status < 0:
            raise RuntimeError("%s failed: %s"
                               % (name, _err(self._lib, status)))

    def send_waveform(self, samples):
        """Play one buffer once, and return when the device has sent it."""
        self._waveform_call('vsgOutputWaveform', samples)

    def repeat_waveform(self, samples):
        """Play one buffer over and over, and return straight away.

        The gap between repeats is whatever silence the buffer ends with, so
        a frame and its own inter-frame gap in one array loops at exactly the
        interval the real remote sends at, with no host in the loop at all.
        stop_waveform() ends it; so does closing the block, starting a
        flowgraph on it, or handing over another waveform.
        """
        self._waveform_call('vsgRepeatWaveform', samples)

    def waveform_active(self):
        """True while a repeat_waveform() is still playing."""
        func = getattr(self._lib, 'vsgIsWaveformActive', None)
        if func is None:
            return False
        active = ctypes.c_int(0)
        with self._lock:
            if self._closed:
                return False
            if func(self._device, ctypes.byref(active)) < 0:
                return False
        return bool(active.value)

    def stop_waveform(self):
        """End a repeating waveform.

        vsgAbort is the only exported way to stop one - the library's
        StopWaveform is internal - and it is what _shutdown() already uses,
        so closing the block stops a repeat too.
        """
        if self._closed:
            return
        # Outside _lock for the same reason _shutdown()'s abort is: its job
        # is to unblock the driver, so waiting on a call parked inside the
        # driver would deadlock.
        self._lib.vsgAbort(self._device)

    def _shutdown(self):
        if self._closed:
            return
        self._closed = True
        # Abort deliberately runs outside the lock: its whole purpose is to
        # unblock a submit parked in the driver, so waiting for that submit
        # to release the lock first would deadlock.
        try:
            self._lib.vsgAbort(self._device)
        except Exception:
            pass
        with self._lock:
            try:
                self._lib.vsgSetRFOutputState(self._device, 0)
                self._lib.vsgSetLevel(self._device, ctypes.c_double(LEVEL_MIN_DBM))
                self._lib.vsgCloseDevice(self._device)
            except Exception as e:
                print("Error closing Signal Hound VSG: %s" % e)
        if getattr(self, '_holds_lock', False):
            _release_lock()
            self._holds_lock = False

    def __del__(self):
        self._shutdown()

    # --- settings --------------------------------------------------------
    # Names mirror uhd.usrp_sink / soapy.sink so the app modules can call
    # whichever idiom they already use.
    def set_frequency(self, channel, freq_hz):
        freq_hz = float(min(max(freq_hz, FREQ_MIN_HZ), FREQ_MAX_HZ))
        with self._lock:
            if self._closed:
                self._center_freq = freq_hz       # applied on reopen
                return
            status = self._lib.vsgSetFrequency(self._device, ctypes.c_double(freq_hz))
            if status < 0:
                print("VSG set frequency failed: %s" % _err(self._lib, status))
                return
            self._center_freq = freq_hz

    def set_center_freq(self, freq_hz, channel=0):
        self.set_frequency(channel, freq_hz)

    def set_sample_rate(self, channel, rate):
        rate = float(min(max(rate, RATE_MIN), RATE_MAX))
        with self._lock:
            if self._closed:
                self._sample_rate = rate          # applied on reopen
                return
            status = self._lib.vsgSetSampleRate(self._device, ctypes.c_double(rate))
            if status < 0:
                print("VSG set sample rate failed: %s" % _err(self._lib, status))
                return
            self._sample_rate = rate

    def set_samp_rate(self, rate):
        self.set_sample_rate(0, rate)

    def set_level(self, level_dbm):
        """Absolute output power in dBm - the VSG is calibrated, so this is
        the real level at the port rather than a relative gain index."""
        level_dbm = float(min(max(level_dbm, LEVEL_MIN_DBM), LEVEL_MAX_DBM))
        with self._lock:
            if self._closed:
                self._level_dbm = level_dbm       # applied on reopen
                return
            status = self._lib.vsgSetLevel(self._device, ctypes.c_double(level_dbm))
            if status < 0:
                print("VSG set level failed: %s" % _err(self._lib, status))
                return
            self._level_dbm = level_dbm

    def set_gain(self, *args):
        """Accepts the USRP ``set_gain(value, chan)`` and HackRF
        ``set_gain(chan, name, value)`` shapes; both are treated as dBm.
        The HackRF 'AMP' stage has no VSG equivalent and is ignored."""
        if len(args) == 3:
            _, name, value = args
            if str(name).upper() == 'AMP':
                return
            self.set_level(value)
        elif args:
            self.set_level(args[0])

    def set_antenna(self, *args):
        """No-op: the VSG60 has a single fixed RF output port."""

    def set_time_now(self, *args):
        """No-op: accepted for USRP call-site compatibility."""

    def get_serial(self):
        return self._serial
