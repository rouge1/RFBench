# The Signal Hound radios: VSG60 and BB60D

One of the notes in `devnotes/`. What applies everywhere, and which
file covers what, is in [CLAUDE.md](../CLAUDE.md).

## VSG60 notes

- Limits (enforced by clamping in `vsg_sink.py`): 30 MHz – 6 GHz, 12.5 kS/s – 50 MS/s, −120 to +10 dBm.
- `vsgSubmitIQ` blocks when the device queue is full, so it supplies real backpressure — the flowgraph needs no throttle block.
- `vsgGetDeviceList` needs its count argument **primed with the array capacity** or it reports zero devices.
- **The vendor API is not thread safe.** A setter called from the Qt thread while the work thread is inside `vsgSubmitIQ` corrupts the device: the submit fails and every subsequent call returns an error until reopen. `vsg_sink` serialises all API calls on an `RLock`; `vsgAbort` is deliberately called *outside* the lock during shutdown, since its job is to unblock a parked submit.
- The library ships inside the Sceptre install rather than a system prefix, in a
  directory named after the Sceptre version, so the path differs per machine.
  `vsg_sink.py` therefore searches *directories* — `/opt/sceptre/lib` (the
  symlink the installer points at the current install), then
  `/opt/sceptre-installer/*/lib` newest version first, then `/usr/local/lib`
  and `/usr/lib`, then the bare soname for ldconfig'd installs. `VSG_API_LIB`
  overrides and accepts either the library file or the directory holding it.
  When the load fails the error names every directory searched and every path
  tried; the launcher reports a missing library as a *software* problem rather
  than as "no VSG detected on USB", which is a different fix.
- **Running a VSG without installing Sceptre.** Sceptre is not required — it is
  just where the library happens to ship. `libvsg_api.so.1` is self-contained
  (~8 MB, API 1.2.1): standing alone it links only against system
  `libusb-1.0`, `libstdc++`, `libudev`, `libm`, `libgcc_s`, `libc`, and needs
  no glibc newer than 2.17. Copy that one file to the target machine, point
  `VSG_API_LIB` at it (or at its directory), and install the udev rule
  `SUBSYSTEM=="usb", ATTR{idVendor}=="2817", MODE="0666", GROUP="plugdev"` as
  `/etc/udev/rules.d/sh_usb.rules`. Do **not** copy the whole
  `/opt/sceptre/lib` directory: its `RUNPATH` starts with `$ORIGIN`, so the
  siblings there (a bundled libc, libstdc++, libudev) would be picked up ahead
  of the system ones and mixed into the host runtime.
- **A second open aborts the process.** The vendor library enforces single-client access with C `assert()`, which calls `abort()` — `vsgOpenDevice` on a device another process holds raises SIGABRT and core-dumps before Python sees anything, and can leave the unit needing a USB reset. It is uncatchable, and `vsgGetDeviceList` still lists a held device, so discovery cannot detect the condition either. `vsg_sink` therefore keeps an advisory PID lock at `config/.vsg60.lock`: `_acquire_lock()` runs before the open and raises a normal `RuntimeError` instead, `in_use()` lets the launcher show a dialog, and a lock whose PID is dead is treated as stale and cleared. This only sees users that go through this module — an external Signal Hound application holding the device is invisible to it.
- **A VSG60 and a BB60D will not stream at the same time on one host.** Start a
  capture while the VSG is transmitting and the BB60 library reports `GetIQ:
  Device packet framing issues` after exactly three buffers, every time, while
  the VSG reports a USB transfer failure. It is not bandwidth (it fails just as
  readily with the VSG at 2 MS/s as at 12.5), not power, not CPU (71% idle),
  not RF (identical at −113 dBm and at −55), and the kernel logs nothing at
  all — no xhci errors, no over-current, no bandwidth complaint. Separate root
  controllers and removing every hub from both paths changed nothing. Both
  radios are perfect alone. So any measurement that needs one of each has to
  put them on different machines, which is what TVAdemo is for.
- **Locking a running flowgraph closes the VSG unless it is held open.** GNU
  Radio calls `stop()` and then `start()` on every block when a flowgraph is
  locked and unlocked, which is how the FM + RDS transmitter's Next Track once
  swapped its audio chain. `stop()` closes the device, since it is also the
  only notice of a real shutdown, and the sink once had no `start()`: after
  Next Track the VSG stayed closed, `work()` reported done, and the whole
  broadcast ended with no error - a receiver just saw the station stop.
  `start()` now reopens it, but opening a VSG60 takes 4.7 s (measured), whereas
  `vsgAbort` takes 0.1 s and the open device accepts samples again straight
  after. So a rebuild wraps its `lock()`/`unlock()` in `sink.held_open()`,
  inside which `stop()` leaves the device open; the reopen in `start()` is only
  the fallback. Nothing in the apps locks a running flowgraph any more - Next
  Track now swaps files in place, because a rebuild glitches the pilot and RDS
  whatever the radio (see the [FM + RDS notes](rds.md#fm--rds-transmitter)) - so this is the rule for
  anything that does.
- **Waveform mode: hand the device one buffer and let it play it.**
  `vsgRepeatWaveform` loops a buffer out of device memory with nothing
  streaming over USB, which is what a short burst wants - a 433 MHz frame
  and the silence after it in one array repeats at the VSG's own clock
  rather than at whatever USB and the host scheduler did that second, and
  there is nothing left to underrun. `vsg_sink` exposes it as
  `repeat_waveform()`, with `send_waveform()` for one shot,
  `waveform_active()` and `stop_waveform()`. Build the block on its own for
  this, with no top block around it; `work()` never runs.

  The vendor ships no header, so the three signatures were read out of
  `libvsg_api.so.1` itself, and two of them are not what a guess would give.
  `vsgRepeatWaveform(device, iq, len)` takes **three** arguments, not a
  fourth repeat count - the repeat is unbounded until something stops it -
  and `len` counts complex samples, as `vsgSubmitIQ`'s does; the call
  `ippsCopy_32fc`s the buffer into its own storage before returning, so the
  caller's array need not outlive it. `vsgIsWaveformActive` writes a
  **4-byte int**, not a C++ `bool`: a `ctypes.c_bool` there leaves three of
  those bytes in whatever sits after it. `vsgOutputWaveform` is only
  `vsgSubmitIQ` followed by `vsgFlushAndWait`.

  **Only `vsgAbort` and the two waveform calls stop a running repeat** -
  a plain `vsgSubmitIQ` does not, so a flowgraph started over one left
  playing would have both feeding the modulator. `vsg_sink.start()`
  therefore ends a repeat before streaming, and `_shutdown()` already
  aborts, so closing the block stops it too. The three calls are bound in a
  `try`, unlike the other fifteen, because an older vendor library need not
  export them and an `AttributeError` at bind time would take the whole VSG
  down rather than just this feature. Verified on the unit here at
  −120 dBm: repeat starts, `waveform_active()` reads True, `stop_waveform()`
  clears it, `send_waveform()` returns only when the burst has gone.

- **Its library brings its own libusb, and it wins.** `libvsg_api.so`
  links `/opt/sceptre/lib/libusb-1.0.so.0`, older than the environment's,
  and whatever loads after it in the same process gets that one. The
  RTL-SDR's module then fails to load - see
  [ism](ism.md#the-rtl-sdr-as-a-receiver). The HackRF is unaffected.

## Signal Hound BB60D as a receiver

The BB60D works well for RDS (0.0 % block errors on a strong station) but is
**not** driven through `gr-soapy`. It is a SoapySDR device - the module is a
system one at `/usr/local/lib/SoapySDR/modules0.8/libSignalHoundBB60.so`, so
`SOAPY_SDR_PLUGIN_PATH` must point there - and while `soapy.source(...)`
constructs fine, *every* gr-soapy setter (`set_frequency`, `set_gain`,
`set_sample_rate`) then fails with `setupStream: Invalid format ''`. Its sample
rates are also a ladder - 40/20/10/5/2.5 MSps and on down in halves - with
nothing near the 2 MS/s the HackRF path uses, so 2.5 MSps (decimate by 10) is
the one to use there. At 10 MSps the analog filter is 8 MHz, which is what
makes it usable for a 6 MHz television channel.

`apps/bb60_source.py` drives it live, wrapping the **raw** SoapySDR Python
binding as a `gr.sync_block` in the spirit of `apps/vsg_sink.py`. Measured
streaming 10 MS/s into the ATSC receiver in real time with zero overflows.
Five things about this device that are not like the others:

- **`setupStream` comes before configuration, not after.** Set the rate or
  the frequency on a device whose stream has not been set up and the module
  reports the format as empty and nothing works afterwards.
  `/data/python/bluey-ox-walker/bin/record_iq.py` on the **system** Python
  has always done it in this order.
- **It is opened by driver name alone**, `driver=SignalHoundBB60`. The very
  arguments `enumerate()` hands back are refused: driver plus serial with
  `Device::make() no match`, and the whole dict with `device_id is not a
  number`.
- **The conda binding loads the system module quite happily** once
  `SOAPY_SDR_PLUGIN_PATH` points at it - both are ABI 0.8.
  `ensure_plugin_path()` finds and sets it.
- **`enumerate()` returns `SoapySDRKwargs`, which has no `.get`** - a SWIG
  map proxy, not a dict. Reading it like one raises `AttributeError`, and
  behind a broad `except` that looks *exactly* like no device being plugged
  in. That cost an afternoon; `find_devices()` now converts first and
  prints anything that goes wrong.
- **SoapySDR loads its modules once per process, on first use**, reading
  `SOAPY_SDR_PLUGIN_PATH` then and never again. Anything that touches
  SoapySDR before `ensure_plugin_path()` runs - the launcher's HackRF
  check before a HackRF app, or any gr-soapy block, even one that fails -
  leaves the BB60 module out, and a BB60D on USB enumerates as nothing
  until the process restarts: launch one HackRF app, switch Settings to
  the BB60D, and the launcher said it was not connected. `find_devices()`
  now loads the module file itself with `SoapySDR.loadModule` when no
  BB60 turns up, and enumerates again. `listModules()` is no help in
  spotting this: it lists the module *files* on the search path, not the
  ones loaded, so `is_available()` says True throughout.

**A BB60D another program has open is still listed**, so `find_devices()`
passing says nothing about whether it will open; the open fails afterwards
with `Unable to open BB60 device -1`. The kernel knows the holder: libusb
keeps the device's `/dev/bus/usb` node open for as long as it has the
device, and `holders()` finds whichever process has that node among its
file descriptors - verified against a second process streaming from it.
The launcher refuses with "Signal Hound BB60D In Use", naming the program
and its PID, and `start()` checks again. It only sees this user's
processes, and nothing off Linux.

**That failed open froze the whole launcher.** GNU Radio calls a block's
`start()` on the block's own thread, and `tb.start()` waits for every block
to check in. One that raises never does, so `tb.start()` never returns -
measured with a bare Python block, while one that returns `False` lets the
flowgraph end by itself. A receiver's `main()` calls `tb.start()` on the
launcher's thread. So `bb60_source.start()` never raises: it prints why and
returns `False`, and the flowgraph ends with nothing received.

Gain is two elements, `ATT` (−30…0 dB) and `RF` (0…20 dB), presented as one
0–100 % slider: the attenuator comes out first, because attenuation costs
noise figure outright, and only then does RF gain go in. So 0 % is −30 dB,
60 % is 0 dB and 100 % is +20 dB.

**The input level falls as the slider rises, and that is correct.** Measured
against a live broadcaster on RF 36 with an empty channel for reference,
because raw level says the opposite of the truth:

| ATT | RF | level | SNR |
|-----|----|-------|-----|
| −30 | 0 | −56.5 dBFS | **−0.1 dB** |
| −20 | 0 | −61.4 dBFS | 0.0 dB |
| −10 | 0 | −69.9 dBFS | 1.2 dB |
| 0 | 0 | −74.0 dBFS | 4.7 dB |
| 0 | 20 | −74.5 dBFS | **6.6 dB** |

Winding the attenuator stage negative adds 18 dB of level and *all* of it is
noise. So `gain_plan` opens the attenuator toward 0 first and only then adds
RF, which makes the slider monotone in signal-to-noise even though the level
meter goes the other way. **An earlier note here claimed the opposite** — 
that 20 dB of RF gain "got the signal clear of the converter" and turned
0 dB SNR into 10 dB. That was reading level instead of SNR, and it was
wrong; the table above is the measurement.

**The last 20 dB of RF is what overdrives the converter.** It is worth under
2 dB of SNR and it is front-end amplification, so on a strong local signal
it overflows the ADC — which is what an 85 % default did the first time the
receiver was run against the bench transmitter. The default is 60 % (the
attenuator open, no RF), within 2 dB of the best this device can do.

**An overdriven converter is invisible in the samples.** They arrive
filtered and decimated, so nothing clips; the only sign is the driver
saying `GetIQ: ADC overflow`. `bb60_source` counts those, and the receiver
shows "Input overloaded — turn the RF gain down" instead of it scrolling
past in a terminal. It also drops the module's `ConfigureIQCenter` /
`ConfigureIO` / `Using format` chatter, which is harmless and otherwise
prints several lines per retune.

**A SoapySDR log handler does not catch any of it, and this said it did.**
`SoapySDR.registerLogHandler` works - a message logged from Python arrives
- and `install_log_handler` now registers through the C API on *every*
copy of the library in the process, because there are two: the BB60 module
is a system module linking `/lib/x86_64-linux-gnu/libSoapySDR.so.0.8`
(318 kB) while the conda binding carries its own (629 kB). A message logged
through either copy's own C API reaches the handler, both checked. And yet
during a real open and two retunes the handler was called **zero times**
while the module printed fifteen lines, whose `[INFO] %s` formatting comes
out of libSoapySDR's *default* handler - so the module does log through the
library, and the level is consulted somewhere the handler is not
(`SoapySDR_setLogLevel(FATAL)` silences it completely, and so does
`SOAPY_SDR_LOG_LEVEL=fatal`).

That cost more than a tidy terminal. `adc_overflows` counted only what the
handler saw, so it stayed at zero however hard the front end was driven and
the ATSC receiver's "Input overloaded" could never fire. Raising the log
level would have hidden the overflow line too, since it is at the same
level as the chatter. So `_DriverOutput` takes file descriptor 2 for as
long as a BB60 is streaming, drops the chatter, counts the overflows and
passes everything else — GNU Radio's warnings, Python's tracebacks —
straight through to the real stderr. Verified: fifteen chatter lines to
none, `GetIQ: ADC overflow` counted with its text kept, an unrecognised
error passed through, and stderr restored when the last source stops.
**The separate `overflows` counter, for samples actually lost, comes from
`readStream`'s return code and was never affected** — the off-air figures
quoted in this file are that one.

Recording with raw SoapySDR and decoding offline still works too, and is
still the right thing for anything that does not need to be live -
`record_iq.py --driver SignalHoundBB60` writes complex float32, which
`scripts/test_rds_core.py` reads directly given a JSON sidecar.

**Verified off-air, HackRF transmitting into the BB60D** at 102.1 MHz and 78 %
power: the signal read 63.6 dB above the noise floor and decoded 912/912 blocks
with 0.0 % errors - PI, PS, RadioText, RT+ and PTY all as sent. Channel
separation measured 33.7 dB and 32.9 dB with the 38 kHz phase fitted to 144
degrees, matching the software chain. A RadioText edit typed into the running
app arrived intact on the next capture. Its RT+ tags were cleared then, which
is no longer the design: a typed message now takes turns with the song, and
Now Playing stays on the song (see the [FM + RDS Transmitter notes](rds.md#fm--rds-transmitter)).

Three things cost time on the way there, all in the *measuring*, not the radio:

- **Flat SNR across gain means the antenna, not the gain.** With the BB60D's
  antenna off, a local FM station sat ~16 dB above the noise floor at every one
  of 0/20/30/40 dB of gain - more gain lifts signal and noise together.
  Connecting it raised the capture rms by 23 dB and the station to 27.6 dB.
  (`record_iq.py --gain 0` also genuinely zeroes the RF stage; its default is
  30.) A bare CW carrier is the quickest way to split "the RF path is dead"
  from "the app is not radiating" - one read 76.8 dB out of the noise.
- **Redirect a capture harness's stdout and Python block-buffers it.** A script
  that waits on a log line to know the transmitter is up will start recording
  long after it should, or not while it is running at all, and the capture
  comes back as pure noise with nothing wrong anywhere. Run it with `python
  -u`.
- **Slice well clear of a live change.** Measuring a field that was edited
  mid-capture, a window that straddles the transition catches an RT+ tag
  belonging to the *outgoing* message and reads exactly like a stale-tag bug.
  Decode a slice safely after the change before believing one.
