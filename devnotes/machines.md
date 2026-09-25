# The other machines: TVAdemo and the Windows laptop

One of the notes in `devnotes/`. What applies everywhere, and which
file covers what, is in [CLAUDE.md](../CLAUDE.md).

## The TVAdemo laptop, and why there is a second machine

Anything that needs a transmitter and a receiver at once needs two machines,
because the VSG60 and the BB60D cannot stream together on one (see the [VSG60 notes](radios.md#vsg60-notes)). TVAdemo is the transmitter:

```sh
ssh tvademo        # hostname TVAdemo
```

`tvademo` is an alias in `~/.ssh/config` on the bench machine. Its
address, account and key are kept there and not here, because this
repository is public.

Ubuntu 24.04, i9-11980HK, 62 GB, 3.4 TB free, GNU Radio 3.10.12.0 with gr-dtv
in a conda env called `gnu`, repo at `/data/python/SDR`. There is **no git**
on it, as on the Windows laptop, so it is kept in step by copying files. It is
on WiFi (the wired port is down), which still moves about 8 MB/s - the fourteen
video clips are 322 MB and took 47 seconds.

- **Check where its media folder actually is before copying anything
  there.** `config/window_settings.json` is per-machine and is *not* one of
  the files kept in step, so `media_directory` can differ; it has been
  `/home/user/Documents` and is now `/data/python/media`, the same as here.
  Read the file, do not assume. Anything CC BY goes with its credits -
  `VIDEO-CREDITS.txt` belongs in the same folder, because the licence
  requires the credit wherever the clip is passed on.
- **ffmpeg is there, inside the conda env** (7.1.1, from conda-forge) rather
  than in `/usr/bin`, so `which ffmpeg` from a bare SSH shell finds nothing
  and it looks absent. `have_ffmpeg()` runs inside the env and sees it, so
  the video picker works on that machine like any other. Do not conclude
  from a plain `which` that a tool is missing on a conda machine.
- **The VSG library is vendored, not installed.** `vendor/libvsg_api.so.1`
  sits in the repo and `VSG_API_LIB=/data/python/SDR/vendor` points
  `vsg_sink` at it, so nothing needs Sceptre. The udev rule is already in
  place and the device node comes up mode 0666.
- **Put a timeout on anything run there that could hang.** A test run over
  SSH with its output filtered through `grep` shows nothing until it exits,
  so a hang looks exactly like a slow run: the FM video test hung there on a
  PAL frame-boundary fault (see [PAL](ntsc.md#pal-625-lines)) and ran for ten hours
  with three threads spinning, holding up the off-air job waiting behind
  it. `timeout` on the remote command turns that into a failure within
  minutes. **Give it `-k`**: `timeout -k 5 120 ...`. A plain `timeout`
  sends `SIGTERM` and then waits, and a process that ignores `SIGTERM`
  is never ended - an app run through `apps/_run.py` did, and stayed on
  the air, until 2026-09-19 (see [running one app without the
  launcher](ui.md#running-one-app-without-the-launcher)). `-k 5` follows
  up with `SIGKILL` five seconds later.
- **Launch the transmitter and start the receiver as two commands.** A job
  put in the background inside an SSH command can hold that command open
  until the job ends: with `setsid nohup bash -ic ... > log 2>&1 < /dev/null
  &`, a 20-second job kept `ssh` from returning for 20.7 s. A script that
  launches the transmitter that way and then starts the receiver begins
  listening *after* the transmission is over. Four runs like that read the
  BB60D's bare noise floor, the known-good test pattern included - which
  looks exactly like a disconnected antenna, and was reported as one. The
  two logs gave it away: the transmitter off air at 17:48:26, the first
  reading here at 17:48:31, with the clocks agreeing to 0.3 s. Start the
  transmitter as its own backgrounded command and have the receiver wait on
  its log.
- **Prefer it for transmitting anything that must be timed accurately.** Its
  VSG puts the ATSC pilot within 200 Hz of where the standard says, and most
  of even that is the BB60D measuring it (see the [ATSC transmitter notes](atsc.md#atsc-transmitter));
  a HackRF managed 7063 Hz, and that one number was the difference between
  nothing decoding and 99.7% of packets decoding.

## Running on Windows

The launcher runs natively on Windows - no WSL, no USB/IP passthrough.
conda-forge ships the *same* GNU Radio for win-64 that this repo uses on Linux
(3.10.12.0, py312), so the flowgraphs run against the version they were written
for.

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\bootstrap.ps1
powershell -ExecutionPolicy Bypass -File .\windows\start_app.ps1
```

`windows/bootstrap.ps1` installs Miniforge if there is no conda, builds the
`gnu` environment, then verifies that gnuradio, qtgui, soapy, PyQt5 and
SoapySDR import, reports how many radios Soapy sees, and says whether
ffmpeg is there. It is idempotent -
re-run it after a pull and it updates in place.

- **`linux/environment.yml` cannot solve on Windows at all.** It is a full Linux
  solve: every package pinned to a `linux-64` build string, with `alsa-lib`,
  `pulseaudio-client` and `libgcc` in the list. `windows/environment.yml` names
  only what the code imports and lets conda choose builds, which is also why it
  needs no edit when conda-forge rolls a build number.
- **Activate; do not call the environment's `python.exe` directly.** GNU
  Radio's DLLs live in `envs\gnu\Library\bin`, which only activation puts on
  PATH. Without it the import dies with a bare `DLL load failed` that names
  nothing useful. `windows/start_app.ps1` goes through the conda *shell hook*, so it
  works without `conda init` having been run.
- `windows/start_app.ps1` also has to `Set-Location` to the repo root, one
  above its own folder, for the same reason `linux/start_app.sh` does its
  `cd ..`: the launcher opens `icons/settings.png` and `config/` by relative
  path.
- **ffmpeg comes from `windows/environment.yml` unpinned, and a new major
  version broke the ATSC transmitter.** The laptop had none at all until
  2026-09-18, so its video transmitters offered colour bars and nothing
  else. conda-forge then gave it 9.0.1, where Linux pins 7.1.1 - and 9
  removed `-top`, the option `apps/atsc_source.py` set top field first
  with. ffmpeg refuses the whole command over one unknown option, so every
  clip without its own `.ts` ended before a byte came out:
  `test_atsc_loopback.py` reported "receiver produced nothing" in one
  second. `setfield=tff` at the end of the filter chain does the same job
  on both - byte-identical to `-top 1` on 7.1.1, and 99.57% byte-perfect
  through the loopback on 9.0.1. The NTSC and FM video transmitters
  decode clips through ffmpeg too, and passed on 9.0.1 as they were. So a
  new ffmpeg arriving on one machine is worth a loopback run before it is
  trusted.
- **The laptop is too slow for the video transmitters, and
  `test_ntsc_transmit.py` failing its four speed checks there is the test
  being right.** It is an i5-7200U: two cores, four threads, 2.7 GHz, 8 GB.
  Encoding NTSC colour at 10 MS/s flat out, measured on 2026-09-18 on
  AC power with the machine idle: 0.41x real time on one thread, 0.55x on
  two (the default, with four logical processors), 0.60x on four. The i9
  here does 0.94, 1.36, 1.68 and 1.96. A frame takes 82 ms on one of its
  threads against 35 here, and the extra two threads are hyperthreads,
  worth 9%. Below 1.0x the encoder cannot keep up even with the processor
  to itself, before the modulator and the radio sink take their share, so
  the NTSC transmitter there repeats pictures, and the FM video
  transmitter, which needed 4.3-5.5 cores on TVAdemo, has no chance. It
  is the same machine whose HackRF was silent 45% of the time sending
  ATSC (see the [ATSC transmitter notes](atsc.md#atsc-transmitter)). Transmit video from Linux. What
  the laptop has run well is FM + RDS, both ends, and the generators and
  audio apps are lighter than that. It cannot receive ATSC live either:
  `test_atsc_receiver.py` decoded the built-in bars perfectly there,
  13.3 ppm clock error corrected and all, but at **0.44-0.48x real time**
  against the 1.2x it asks for. At the HackRF's 12 MS/s more than half
  the samples would be lost before the demodulator saw them (measured
  2026-09-18). The NTSC and FM video receivers have not been tried there,
  but they take 2.6-3.8 of the i9's cores.
- **Check whether WinUSB is already bound before sending anyone to Zadig.** The
  HackRF here needed no driver work at all - Windows had already attached its
  own WinUSB. `(Get-PnpDeviceProperty -InstanceId <id>)` showing
  `DEVPKEY_Device_Service = WINUSB` means the driver step is done; Soapy
  enumerating 0 devices is what says it is not.
- **Pillow is newer on Windows than on Linux** (12.3.0 against 11.1.0 here), so
  PIL calls can be silent in one place and warn in the other -
  `getdata()`/`putdata()` are deprecated in 12 and removed in 14. The launcher
  keys the settings icon's light background out with numpy instead, which is
  version-independent and produces a pixel-identical image.
- `vmcircbuf_prefs ... failed to open` on startup is cosmetic. The path it
  prints is malformed - `AppData\Roaming.config\gnuradio`, `%APPDATA%` and
  `.config` run together with no separator - so the preference never gets
  written and the line reprints every run. It does not affect the flowgraph.
- **A GUI started over SSH never reaches the desktop.** Windows OpenSSH runs
  children in session 0; the user's desktop is session 1. Flowgraphs still run
  headless there with `QT_QPA_PLATFORM=offscreen` (font warnings are expected
  and harmless), which is enough to transmit and be measured, but the window is
  invisible. `Get-Process python | Select Id,SessionId` is what tells you whose
  process is whose.
- `gr-audio` is present in the Windows build, so `rdsReceiver.py` can run there
  too, not just the transmitters.

**Verified cross-machine**, Windows HackRF transmitting and a BB60D on the
Linux box receiving: 102.1 MHz at 78 % read 66.3 dB above the noise floor and
decoded 1368/1368 blocks at 0.0 % errors, with PS, RadioText and PTY exactly as
configured. The transmitter was launched from the Windows GUI, not headless.

The one thing that cost time was, again, not the code: with no antenna on the
HackRF the whole chain came up clean - device opened, gains applied, no errors -
and put *nothing* on the air. The bare CW carrier at maximum gain read 2.6 dB
above the floor where the same test on the Linux bench reads 76.8 dB; with the
antenna connected the signal jumped to 66.3 dB and the capture rms rose 44 dB.
Validate the receiver against a known-strong station first - a wideband sweep
that finds 57 FM carriers proves the fault is on the transmit side before you
go looking for it there.

## Building the environment on a new Linux machine

```sh
conda env create -f linux/environment.yml --name gnu
```

`linux/environment.yml` is a full pinned solve, and it has twice been
wrong in a way only a fresh machine shows. Both were found on 2026-09-18.

- **It pins conda-forge builds only, so it solves under
  `channel_priority: strict`**, which is Miniforge's default. It was once
  a `conda env export` holding eleven builds from `defaults` - the
  `h5eee18b` and `h06a4308` build strings - and strict priority refuses
  every `defaults` build of a name conda-forge also carries. The failure is
  `LibMambaUnsatisfiableError: ... excluded by strict repo priority`. It
  had only ever solved on a flexible config. Check a fresh export for
  anything not from conda-forge before committing it:
  `conda list -n gnu --json` gives each package's channel.
- **The HackRF needs `soapysdr-module-hackrf` inside the environment.**
  Built without it, `lsusb` and the system `hackrf_info` both see the
  radio while the launcher reports no HackRF. conda's SoapySDR searches
  only its own `lib/SoapySDR/modules0.8`, where `SoapySDRUtil --info` then
  prints `No modules found!`, and `SoapySDRUtil --find` is the quick check
  that it is fixed. `windows/environment.yml` always had the module. The
  system one under `/usr/lib/x86_64-linux-gnu/SoapySDR` is no substitute:
  it links the system `libSoapySDR` and `libhackrf`, not the environment's.
- **An RTL-SDR needs `soapysdr-module-rtlsdr`, and it went unpinned for
  months.** Both it and `rtl-sdr` were installed in the `gnu` environment
  here and named in neither `environment.yml`, so a machine built from
  those files had no RTL support at all and nothing to say why - the same
  failure as the HackRF one above, and just as quiet. Both are pinned now.
  The lesson is the general one: a package installed by hand into `gnu`
  and not written back into the file is invisible until a new machine is
  built. See [ism](ism.md#the-rtl-sdr-as-a-receiver).
