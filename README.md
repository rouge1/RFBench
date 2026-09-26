# RF Bench Toolkit

A launcher for a suite of GNU Radio transmitters and receivers. Pick a tile, set
it up in its dialog, and the flowgraph runs on whichever radio is selected.

---

## Features

- 16 apps: signal generators, AM and FM audio, FM broadcast with RDS, and ATSC,
  NTSC and FM video - transmitters and receivers, with the two ends of each
  standard sharing one tile
- Four radios: **HackRF One** (USB via SoapySDR), **Ettus USRP** (network via
  UHD), **Signal Hound VSG60** (transmit only) and **Signal Hound BB60D**
  (receive only). The grid arranges itself around whichever is selected
- Three themes - **Slate** (dark), **Reading Room** (light) and **Walnut**
  (brown and tan) - picked with the dot by "Themes" in the launcher's
  header. The dialogs and the app windows follow, a tile under the pointer
  lifts off the page - lit from behind in the dark themes, casting a deep
  shadow on paper - and a pulse runs down the line beside each row of
  tiles
- Runs on Linux and Windows
- Needs no network connection - the fonts ship with the repository
- Remembers each app's settings, and where every window was left

---

## Prerequisites

### System Requirements

- Linux (X11 or Wayland display required), or Windows 10/11 - see
  [On Windows](#on-windows)
- [Miniconda](https://docs.conda.io/en/latest/miniconda.html) or [Anaconda](https://www.anaconda.com/)
- Audio: **PipeWire** (ALSA audio source is not supported)

### Hardware (one of the following)

- **HackRF One** — connected via USB; SoapySDR HackRF driver must be available in the conda environment
- **Ettus USRP** — reachable over the network via UHD 4.x; IP address configured in the Settings dialog
- **Signal Hound VSG60** — transmit only, Linux only; connected via USB; requires the vendor `libvsg_api.so`, which is **not** included in this repository — see [Signal Hound VSG60 library](#signal-hound-vsg60-library) below. 30 MHz – 6 GHz, up to 50 MS/s, calibrated output from −120 to +10 dBm
- **Signal Hound BB60D** — receive only; connected via USB; needs Signal Hound's SoapySDR module — see [Signal Hound BB60D](#signal-hound-bb60d) below

---

## Installation

### 0. Install Miniconda (skip if already installed)

```sh
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh
```

Follow the prompts and accept the default install path (`~/miniconda3`). When asked to initialize conda, choose **yes**. Then reload your shell:

```sh
source ~/.bashrc
```

### 1. Clone the repository

```sh
git clone https://github.com/rouge1/SDR.git
cd SDR
```

### 2. Create the conda environment

The Linux environment, `linux/environment.yml`, contains a hardcoded `prefix` for the original machine. Override it with `--name` so it installs correctly on any system:

```sh
conda env create -f linux/environment.yml --name gnu
```

> If conda reports that the environment already exists:
> ```sh
> conda env update -f linux/environment.yml --name gnu --prune
> ```

### 3. Verify the environment

```sh
conda activate gnu
python -c "from gnuradio import gr; print(gr.version())"
```

Expected output: `3.10.12.0`

### On Windows

The same GNU Radio (3.10.12) runs natively on Windows - no WSL. From the
repository folder in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\bootstrap.ps1
powershell -ExecutionPolicy Bypass -File .\windows\start_app.ps1
```

The first installs Miniforge if there is no conda, builds the `gnu`
environment from `windows/environment.yml` (the Linux one cannot solve on
Windows), and checks that GNU Radio, SoapySDR and PyQt5 import and that
`ffmpeg`, which the video transmitters need for clips, is there. It is
safe to re-run after an update. The second starts the launcher; launch it from
the Windows desktop, since a window started over SSH never appears. A HackRF
often needs no driver work - Windows binds WinUSB to it by itself - and if
SoapySDR finds no device, bind WinUSB with Zadig. The VSG60 is not supported on
Windows yet.

---

## Signal Hound VSG60 library

Only needed if you are using a VSG60, and only on Linux. The vendor library `libvsg_api.so.1` is
proprietary Signal Hound code with no redistribution grant, so it is **not
committed to this repository** and cannot be installed with pip or conda. You
supply it from your own licensed copy.

It is a single self-contained ~8 MB file. It links only against standard system
libraries (`libusb-1.0`, `libstdc++`, `libudev`) and requires nothing newer than
glibc 2.17, so one file is all you need — no Sceptre install on the target
machine.

```sh
# From a machine that has Sceptre installed locally:
./linux/setup_vsg.sh

# From an explicit path (a Signal Hound SDK download, a USB stick, ...):
./linux/setup_vsg.sh /path/to/libvsg_api.so.1

# Copied from another machine that has it:
./linux/setup_vsg.sh user@host
```

The script puts the library in `vendor/` (gitignored — do not commit it),
installs the udev rule below, and verifies that the library loads and whether a
VSG is detected.

**Where to get the library:** any Sceptre install has it at
`/opt/sceptre/lib/libvsg_api.so.1`, or download the standalone VSG60 SDK from
Signal Hound, which is the same library without the rest of the Sceptre payload.

**Doing it by hand instead.** Drop the file anywhere the loader looks —
`vendor/` in this checkout, `/usr/local/lib` (run `ldconfig` afterwards), or a
path of your choosing exported as `VSG_API_LIB` (the file itself or the
directory holding it). Then add the udev rule, or the API cannot claim the
device even though `lsusb` lists it:

```sh
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="2817", MODE="0666", GROUP="plugdev"' \
  | sudo tee /etc/udev/rules.d/sh_usb.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Replug the VSG60 afterwards.

> **Do not copy the whole `/opt/sceptre/lib` directory.** The library's `RUNPATH`
> starts with `$ORIGIN`, so it would load Sceptre's bundled `libc`, `libstdc++`
> and `libudev` from alongside it instead of the system ones. Copy the single
> file.

---

## Signal Hound BB60D

Receive only. It is driven through SoapySDR, but needs Signal Hound's SoapySDR
module for it installed on the system: the apps look for
`libSignalHoundBB60.so` in `/usr/local/lib/SoapySDR/modules0.8` (or the
distribution's own SoapySDR module folder) and point the conda environment's
SoapySDR at it themselves. Nothing in this repository replaces that module.
The receive gain is one 0-100% slider; 60% - the attenuator open and no RF
gain - is the default and usually the best setting.

---

## Running the Application

On Linux:

```sh
./linux/start_app.sh
```

On Windows, from PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\start_app.ps1
```

Or activate the environment manually first:

```sh
conda activate gnu
python RFbenchToolkit.py
```

> `linux/start_app.sh` assumes Miniconda is installed at `~/miniconda3`. If your installation is elsewhere (e.g., `/opt/miniconda3`), edit the `source` line in that script accordingly.

### One app, without the launcher

```sh
conda activate gnu
python apps/_run.py amSineGenerator                        # its own dialog first
python apps/_run.py amSineGenerator --config values.json   # no dialog
QT_QPA_PLATFORM=offscreen timeout -k 5 120 \
    python apps/_run.py amSineGenerator --config values.json   # headless, 2 min
```

Useful on a bench machine over SSH - one machine transmitting, another
receiving. `--config` holds what the app's dialog would return, and skips the
dialog; `QT_QPA_PLATFORM=offscreen` runs it with no screen. Ctrl+C, `kill` or
`timeout` stops it, and a stop that sticks ends the process 5 s later anyway.

---

## First-Run Configuration

On first launch, open the **Settings** dialog (gear icon, top-right) and configure:

| Setting | Description |
|---------|-------------|
| Media Directory | Folder of audio and video clips. WAV and MP3 both play; subfolders are searched too, and `.WAV` counts as well as `.wav`; any video `ffmpeg` can read works |
| Radio | **HackRF One (USB)**, **Ettus USRP (Network)**, **Signal Hound VSG60 (USB, transmit only)** or **Signal Hound BB60D (USB, receive only)**. With a one-way radio every tile turns to the side it can run and the rest are dimmed |
| Ettus IP Address | Ettus USRP only, the one radio on the network rather than USB — its IP address (the factory default is 192.168.10.2). Greyed out for the other radios |

The repository ships no audio or video, so a fresh clone has nothing to pick
until **Media Directory** points at a folder: the audio lists are empty, the video
transmitters offer only their built-in colour bars, and every app still runs on
its tone or pattern. Any WAV or MP3 and any video `ffmpeg` reads will do - MP3
and video need `ffmpeg`, which both environments install.

Settings are saved to `config/window_settings.json` (created automatically on first run),
the theme among them - so the dialogs and app windows all open in the one
chosen.
Nothing in `config/` is committed - window positions, radio choice and the
media folder are per machine - so a pull never overwrites them.
The launcher opens at a size it works out for itself - the widest bank's tiles
in one row, every bank in view - centred on the screen, and after that comes
back wherever it was left - maximized, if it was left maximized.

---

## Project Structure

```
SDR/
├── apps/
│   ├── *.py                      # One module per app, plus the radio blocks
│   ├── utils.py                  # Shared settings, dialog layout and window helpers
│   ├── theme.py                  # Colours, type and fonts for every window
│   ├── _run.py                   # Runs one app without the launcher
│   ├── media.py                  # How every picker finds files in the media folder
│   ├── settings_dialog.py        # Global settings UI
│   ├── fonts/                    # Barlow and each theme's faces, each with its SIL OFL licence
│   └── icons/                    # Tile pictures and interface glyphs
├── scripts/
│   └── test_*.py                 # Tests - most need no radio and no display
├── linux/                        # Only what differs on Linux
│   ├── start_app.sh              # Starts the launcher
│   ├── environment.yml           # Conda environment
│   └── setup_vsg.sh              # Installs the VSG60 vendor library + udev rule
├── windows/                      # Only what differs on Windows
│   ├── start_app.ps1             # Starts the launcher
│   ├── environment.yml           # Conda environment
│   └── bootstrap.ps1             # Installs conda and builds the environment
├── config/                       # Auto-created; gitignored
│   └── window_settings.json      # Global settings (radio type, IPs, media dir)
├── vendor/                       # Gitignored; holds libvsg_api.so.1 if used
└── RFbenchToolkit.py             # The desktop launcher
```

---

## Available Applications

| App | Does | Status |
|-----|------|--------|
| **Signal generators** | | |
| AM Sine Generator | AM with a sinewave (DSB/SSB, full or suppressed carrier) | Tested |
| ASK Generator | Amplitude Shift Keying | Tested |
| FSK Signal Generator | Frequency Shift Keying | Tested |
| PSK Signal Generator | Phase Shift Keying | Tested |
| PPM-OOK Generator | Pulse Position Modulation OOK audio | Tested |
| **Audio** | | |
| AM Audio Generator | AM with live or recorded WAV or MP3 audio | Tested |
| FM Audio Generator | FM with recorded WAV or MP3 audio | Tested |
| FM Subcarrier Generator | FM subcarrier carrying recorded WAV or MP3 audio | Tested |
| FM + RDS Transmitter | FM broadcast in stereo with RDS: station name, RadioText, now playing, clock | Verified off air |
| FM + RDS Receiver | Decodes a station's RDS: call sign, station name, RadioText, now playing, clock | Verified off air |
| **Video** | | |
| ATSC Video Transmitter | ATSC 8VSB digital television on a 6 MHz channel | Verified off air |
| ATSC Video Receiver | Demodulates 8VSB and recovers the transport stream, to watch or record | Verified off air |
| NTSC Video Transmitter | Analog television, picture and sound, from any video `ffmpeg` reads | Verified off air |
| NTSC Video Receiver | Decodes the picture and demodulates the sound | Verified off air |
| FM Video Transmitter | Analog FPV on 5.8 GHz or ITU-R F.405 relay, in NTSC or PAL | Verified off air |
| FM Video Receiver | Pictures and sound, and measures what the transmitter actually sends | Verified off air |

Each transmitter and its receiver share one tile; the badge in the tile's corner
flips between them. Apps that play recorded material take it from the **Media
Directory** set in Settings.

---

## Configuration Details

- **Global settings** — `config/window_settings.json` (media directory, radio type, the Ettus's IP address, theme, window geometry)
- **Per-app settings** — `config/<module_name>_config.json` (last-used parameter values, and where its dialog and window were left)
- Both files are created automatically and are excluded from version control

---

## Adding a New Application

1. Create `apps/<module_name>.py` implementing:
   - `ConfigDialog(QDialog)` — configuration UI; must implement `get_values()` returning a dict
   - `main(top_block_cls=..., options=None, app=None, config_values=None)` — creates and starts the GNU Radio flowgraph, returns the `top_block` instance
2. Add an icon to `apps/icons/`
3. Add a row to `APP_TILES` near the top of `RFbenchToolkit.py` -
   `(row, column, [(label, module, icon, 'tx' or 'rx')])`. To give an existing
   app its other end, such as a receiver for a transmitter, add a second face
   to that tile's list instead of a new row. The launcher and
   `scripts/test_launcher_gui.py` both read this table, so a row added there
   appears in both.

Refer to `apps/amSineGenerator.py` as a reference implementation.

---

## Troubleshooting

**`conda env create` fails with prefix conflict**
Add `--name gnu` to override the hardcoded prefix in `linux/environment.yml`.

**App launches but no RF output (HackRF)**
Ensure the HackRF is connected before starting. Run `SoapySDRUtil --find` to confirm it is detected. The power slider is a 0–100% control mapped onto the HackRF's VGA range, so 100% is 47 dB of VGA gain.

**App launches but no RF output (USRP)**
Confirm the USRP IP is reachable (`ping <ip>`) and matches what is configured in Settings.

**Signal Hound VSG60: "VSG API library not found" / "Software Not Found"**
The vendor library is missing on this machine — the device itself is fine, and `lsusb | grep 2817` will still list it. The dialog names every directory searched and every path tried. See [Signal Hound VSG60 library](#signal-hound-vsg60-library); usually `./linux/setup_vsg.sh` is enough. Note the Sceptre install directory is named after its version, so it differs from machine to machine.

**Signal Hound VSG60 detected by `lsusb` but not by the launcher**
The udev rule is missing, so the API cannot claim the device. Install it as shown in [Signal Hound VSG60 library](#signal-hound-vsg60-library) and replug the unit.

**App launches but no RF output (Signal Hound VSG60)**
Confirm the unit is detected with `lsusb | grep 2817`. The power slider maps 0–100% onto −120 to +10 dBm, so 100% is the VSG's maximum calibrated output. Requested frequencies outside 30 MHz – 6 GHz are clamped to the nearest limit and the clamp is logged to the console.

**Signal Hound VSG60 crashes with an assertion**
The vendor library calls `abort()` rather than returning an error if the device is already open in another process, which kills the app and can leave the VSG wedged. Launching twice from this launcher is caught and refused with a dialog, but an external Signal Hound application holding the device cannot be detected. If it does happen: close the other user of the device, then reset it — unplug/replug, or issue a `USBDEVFS_RESET` ioctl on the node shown by `lsusb | grep 2817`.

**Audio apps produce no sound / error on launch**
ALSA audio source is not supported. The system must use PipeWire. Verify with `pactl info | grep "Server Name"`.

**Launcher window appears off-screen after moving between display configurations**
It should come back by itself: a saved position no screen reaches any more is
ignored and the window is centred instead. To reset the launcher's size and
position by hand, remove just the `window_position` entry from
`config/window_settings.json`. Do not delete the whole file for this - it also
holds the media folder, the radio and the USRP addresses, and they go with it.

**A file in the media folder does not appear in an app's list**
The list is built when the app's dialog opens, so close the dialog and open it
again. Hidden files are skipped - including the `._` copies macOS leaves beside
files it writes to a USB stick - and so are folders reached through a symbolic
link.

---

## Environment Details

| Component | Version |
|-----------|---------|
| Python | 3.12 |
| GNU Radio | 3.10.12 |
| PyQt5 | 5.15 |
| UHD | 4.8 |
| SoapySDR | 0.8.1 |
| Conda environment name | `gnu` |

---

## Acknowledgments

- GNU Radio community
- PyQt5 developers
- Gary Schafer
- Rick Astley
