# The ISM bands: 433 MHz, and a frame a receiver will believe

One of the notes in `devnotes/`. What applies everywhere, and which
file covers what, is in [CLAUDE.md](../CLAUDE.md).

This is the research that came before any code, written down on
2026-09-24 so the next person does not have to find it again. Nothing
here has been on the air yet: everything marked **measured** was
measured in software, against files, and everything else is sourced or
flagged. The open bench questions are collected at the end.

## The shape of the job

The cheap 433 MHz world - door sensors, weather stations, car remotes,
tyre-pressure monitors - is a *receiving* subject that this repository
has no transmitter for, and a *transmitting* subject whose receiver it
does not have either. Both halves need building, and they are not
symmetric.

**An RTL-SDR cannot transmit.** It is a DVB-T tuner and an 8-bit ADC,
with no DAC and no PA on the board. "An RTL transmitter" is not a
thing, and no amount of driver work makes it one. The dongle is a
receiver, and a good one for this job.

So the pair is a transmitter from the three this repository already
drives - HackRF, USRP, VSG60 - and a receiver that is the dongle, the
HackRF, or the BB60D. The VSG60 being transmit-only and the RTL-SDR
being receive-only makes them exactly complementary, which is a tidier
bench than it sounds: neither can be mistaken for the other end.

## The bands, and what is actually legal

The four bands these devices use, and the rule each one lives under:

| Band | Region | Rule | Real ISM? | Limit |
|---|---|---|---|---|
| 315 MHz | US | FCC §15.231 | **No** | ~6,042 µV/m at 3 m (≈ −20 dBm EIRP) |
| 433.05–434.79 | US | FCC §15.231 | **No** | ~10,997 µV/m at 3 m (≈ −14 dBm EIRP) |
| 433.05–434.79 | EU/CEPT | ERC REC 70-03 band f, EN 300 220 | Yes | 10 mW e.r.p., ≤10 % duty cycle |
| 863–870 | EU/CEPT | ERC REC 70-03, EN 300 220-2 | Yes | 25 mW at 1 %, 500 mW at 10 % in 869.4–869.65 |
| 902–928 | US | FCC §15.249 or §15.247 | Yes | 50 mV/m at 3 m (≈ −1 dBm), or +30 dBm hopping |

**433.92 MHz is not a US ISM band.** This is the thing most worth
knowing, and it surprises people who have read that "433 is ISM" - it
is, in ITU Region 1, under footnote 5.280, and that is where the phrase
comes from. In the US, 420–450 MHz is federal radiolocation and amateur,
and the only thing that makes a 433 MHz remote legal is §15.231,
"periodic operation" - control signals, from a device that is manually
activated and shuts itself off within five seconds. §15.240 separately
authorises 433.5–434.5 MHz, but only for container-identification tags
at ports and rail yards, which is not this.

**The power limits are tiny and the timing limits are tighter.** The
§15.231 field strength works out around −14 dBm EIRP at 433 MHz - tens
of microwatts, three to four orders of magnitude below what a HackRF
puts out with the gain at default. But the power is not what a bench
test fails. §15.231 forbids continuous transmission outright, and
requires manual activation with a five-second cutoff; the low-power
alternative in §15.231(e) caps a transmission at one second and demands
a silent period thirty times as long. **A flowgraph looping a test
pattern for two minutes fails on timing at any power level.** Europe's
1 % and 10 % duty cycles say the same thing in different words.

**Home-built gear needs no certification.** §15.23 exempts a device
built in quantities of five or fewer, for personal use, not from a kit -
which is what every flowgraph here is. That removes the paperwork and
nothing else: §15.5 still applies, so the thing must not cause harmful
interference, must accept interference, and must be switched off if the
FCC asks.

**So the bench runs into a cable, not an antenna.** That sidesteps the
whole framework, because Part 15 governs *radiated* emissions, and it is
the only honest way to run a transmitter for minutes at a time while
developing one.

## The bench: a cable and a pad, not an antenna

Three numbers decide the bench layout, and two of them are damage
thresholds:

- **A HackRF's maximum receive input is −5 dBm.** Great Scott Gadgets
  say so in their own documentation, and say that exceeding it causes
  permanent damage. Up to +10 dBm is survivable with the RX amp
  disabled, and they recommend against relying on that, because one
  software slip re-enables the amp.
- **A HackRF transmits +10 to +15 dBm below 2170 MHz**, decreasing with
  frequency. At 433.92 MHz expect somewhere around +12 dBm, though that
  is interpolation and wants measuring.
- **An RTL-SDR's front end gives out around +10 dBm** - community
  figure, no public Rafael Micro datasheet exists, so treat it as
  indicative. Clamping diodes start conducting somewhere around +7.

**A bare SMA cable from a HackRF's TX to any receiver here is 15 to 20 dB
over the limit.** Put 20–30 dB of fixed attenuation in the line before
TX ever meets RX, or terminate into a 50 Ω dummy load and work off the
leakage. A 2 W pad is ample - these radios put out tens of milliwatts.
A shielded box is worth having as a second layer, not as the control;
the attenuator is what keeps the signal off the air.

Radiating briefly at compliant strength on 433.92 is, in practice, no
worse than the LPD433 walkie-talkies and doorbells already sharing the
channel. The exposure comes from the duty cycle, not the power. Do it
into a cable anyway.

## The carrier never turns off

This is the central problem, it is not obvious, and it is what will
break the first receiver that is pointed at the first transmitter.

**Amplitude zero is not RF off.** All three transmitters here are
direct-conversion, so with I = Q = 0 the local oscillator still leaks to
the output. An OOK receiver therefore sees carrier against carrier
rather than carrier against noise, and every envelope slicer - rtl_433's
included - stops working, because the gaps never fall below threshold.

What each radio does about it:

| Radio | Carrier feedthrough | Can it be nulled? |
|---|---|---|
| **VSG60** | spec < −40 dBc; **−70 to −75 dBc at 400–500 MHz** from the manual's own plot | Yes - I/Q offset trim, typically 60 dB on/off; and `vsgSetRFOutputState` is a real output disable |
| **USRP (WBX)** | ≈ −50 dBm transmitting zeros, measured on an X310 | Yes - `uhd_cal_tx_dc_offset`, applied automatically per LO frequency |
| **HackRF One** | no figure published; GSG issue #1481 reports an unwanted centre carrier that **rises with TX VGA gain** | **No.** There is no DC-offset call anywhere in `libhackrf` |

So the VSG60 is the best transmitter here by a wide margin, and the
HackRF is the worst - and the HackRF's leak gets worse exactly when you
turn the power up.

**The fix is to move the leak out of the receiver's window.** Tune the
transmitter below the target by more than the receiver's capture
bandwidth, and put the signal back with a complex baseband tone:

```python
tx_lo = 433.92e6 - 400e3
iq    = envelope * np.exp(2j * np.pi * 400e3 * t)
```

rtl_433 captures 250 kHz wide by default, so 400 kHz of offset puts the
LO leak cleanly outside it. This needs no hardware support, works
identically on all three radios, and is Signal Hound's own documented
trick scaled down from the 15–16 MHz they suggest for a 20 MHz receiver.

**Both existing OOK tools get this wrong for this purpose.**
`hackrf_ook` uses 27 kHz of offset and URH's ASK example 3.9 kHz. Those
are sized to dodge the *receiver's* DC spike, not to hide the
*transmitter's* leak from a 250 kHz capture. Do not copy the numbers.

Beyond that: attenuate hard at the receiver so its AGC is not amplifying
the leak into the slicer, and kill the output stage between frames -
`vsgSetRFOutputState` on the VSG, `set_gain(0,'AMP',0)` on the HackRF.
Both are control-path calls taking milliseconds, so they work between
frames and are useless between bits.

## Building the waveform

**Synthesise it in NumPy and hand it to a vector source.** Every timing
is then exact and nothing depends on scheduler behaviour. Stock GNU
Radio blocks are enough; no out-of-tree module is needed, and none of
the ones people suggest actually exists (see Prior art).

**Pick a sample rate that is an integer number of samples per
microsecond**, and express every timing in whole microseconds:

```
sps_per_us = fs / 1e6          # must be an integer
n_samples  = duration_us * sps_per_us
```

8 MS/s gives 8 samples/µs and is the right choice on a HackRF; 2 MS/s
is fine on the USRP and the VSG60. A 64-bit frame at 500/1000 µs plus a
10 ms gap is about 58 ms - 464 000 complex samples at 8 MS/s, 3.7 MB as
`complex64`, which is nothing.

**Shape the edges.** A hard-keyed rectangular envelope has its first
sidelobe only 13.3 dB down and decays as 1/f. A trapezoid with rise
time `t_r` adds a second breakpoint at `1/(π·t_r)` and decays as 1/f²
beyond it. For a 500 µs pulse with a 10 µs ramp, that is about **30 dB
less energy at ±1 MHz** - the knee moves from 637 Hz to 31.8 kHz. A
raised-cosine ramp is better still. Use `t_r ≈ T_min/20` to `T_min/50`:

| Shortest pulse | `t_r` |
|---|---|
| 200 µs | 4–10 µs |
| 500 µs | 10–25 µs |
| 1000 µs | 20–50 µs |

In GNU Radio, build the 0/1 envelope at `fs` and convolve it with a
normalised Hann window of `t_r·fs` taps - a step convolved with a Hann
window *is* a raised-cosine edge. `digital.burst_shaper_cc` only shapes
the two ends of a tagged burst, not every OOK edge inside it, so it is
not a substitute.

A symmetric ramp does not move the 50 % crossing, so measured pulse
widths are unchanged to first order and the decoder does not notice.

**On the VSG60 shaping is mandatory, not optional.** Signal Hound
require the signal stay within 80 % of the sample rate, and a hard-keyed
envelope is wideband by construction.

**For 2-FSK**, `analog.frequency_modulator_fc(sensitivity)` with
`sensitivity = 2π·deviation/fs`, fed a ±1 float. `fskGenerator.py`
already writes `excursion*2000*pi/samp_rate`, which is the same thing
with the deviation in kHz - no change needed. Building the phase
directly with `np.cumsum` works too and is what real devices emit; what
does not work is `exp(j·2π·f[n]·n/fs)`, which is phase-discontinuous at
every symbol boundary and splatters worse than hard OOK.

**Put the inter-frame gap in the vector, not in a gate.** A
`multiply_const` toggled from the Qt thread cannot give microsecond
edges - SoapyHackRF's MTU alone is 131 072 samples, about 16 ms at
8 MS/s. GNU Radio issue #6337 is someone discovering exactly this.

**Loop the vector; do not stop the flowgraph to stop transmitting.**
`vector_source_c(frame_plus_gap, repeat=True)` gives exact, jitter-free
spacing. A source that ends leaves the HackRF keyed and emitting zeros -
which is to say, leaking carrier - until `hackrf_stop_tx`; on older
firmware it repeats its last buffer instead (issue #660), and issues
#354 and #840 are the device staying in transmit after a flowgraph
stops. If you want exactly N frames, tile the array, append a generous
zero tail to flush the pipeline, and tear the sink down explicitly.

## What each radio does

| | HackRF One | USRP (WBX) | VSG60 |
|---|---|---|---|
| Power control | `set_gain(0,'VGA',v)` 0–47 dB, `'AMP'` 0/11 dB | `set_gain(v,0)`, 0–31.5 dB | `vsgSetLevel(dBm)` |
| Calibrated? | No | No | **Yes, ±2.0 dB** |
| Carrier off | No control | `set_tx_dc_offset` | `vsgSetRFOutputState` |
| Timed bursts | **No** | Yes, `tx_sob`/`tx_eob`/`tx_time` | Device-side repeat |
| Sample rate floor | 8 MS/s recommended | flexible | 12.5 kS/s |

**The VSG60 is the one to build against.** Its level control is absolute
dBm, and the mapping is documented exactly: a sample's output power is
the set level plus `20·log10(|sample|)`. So a full-scale OOK pulse at
`vsgSetLevel(0.0)` leaves the connector at 0 dBm ±2 dB. Two things fall
out of that, and both are worth having - you can measure a receiver's
sensitivity threshold in real dBm by winding the level down until
decoding stops, and given the legal limits here are in microwatts, you
can be deliberately, knowably small instead of guessing. Neither the
HackRF nor the USRP can tell you their output power without a meter.

**`vsgRepeatWaveform` is bound now.** `vsg_sink.py` had fifteen calls and
that was not one of them; it, `vsgOutputWaveform` and
`vsgIsWaveformActive` are the three this work wants, and they are there
as `repeat_waveform()`, `send_waveform()`, `waveform_active()` and
`stop_waveform()`. The device loops the buffer out of its own memory -
no host jitter, no underrun risk - which for an ISM frame that repeats
forever is the right call, and was the single best upgrade available
here. One thing written down first turned out to be wrong: a plain
`vsgSubmitIQ` does **not** stop a running repeat. Only `vsgAbort` and the
two waveform calls do, which is why `start()` ends a repeat before it
streams. The signatures, read out of the library for want of a header,
are in [radios](radios.md#vsg60-notes).

**A HackRF's TX underruns are completely silent through gr-soapy.**
SoapyHackRF's `writeStream` never returns `SOAPY_SDR_UNDERFLOW`, and
gr-soapy's sink never calls `readStreamStatus`, so the 'U' that
SoapyHackRF is perfectly willing to print never gets asked for. A
starved transmitter emits zeros - which inside an OOK frame reads as an
*extra gap*, so the failure mode is a wrong decode, not an error
message. `gr-osmosdr` does its own detection and prints 'U'; gr-soapy
does not.

**Tagged-stream bursts do not work on a HackRF.** gr-soapy's sink does
set `SOAPY_SDR_END_BURST` on the last sample of a tagged burst, but
SoapyHackRF only reads that flag in `activateStream` and never in
`writeStream`. Do not design around them. The USRP is the only one of
the three that can do a genuinely timed, properly terminated burst.

**Run a HackRF at 8 MS/s, not 2.** libhackrf advertises 2–20 MHz and
SoapyHackRF advertises 1–20, but Great Scott Gadgets say below 8 MHz is
not recommended: the MAX5864 is not specified below 8 MHz, and the
MAX2837's minimum filter bandwidth is 1.75 MHz, giving only 4 dB of
rejection at ±1 MHz at a 2 MHz rate. 8 MS/s is also exactly 8 samples
per microsecond.

**433.92 MHz is not a multiple of 4, 5 or 10 MHz**, so the VSG60 will
show fractional-N spurs; its low-spur mode keeps them below −55 dBc and
at least 2 MHz away under 3.7 GHz.

## The RTL-SDR as a receiver

**It is already installed.** Verified on this machine: the `gnu`
environment has `rtl-sdr 2.0.2` and `soapysdr-module-rtlsdr 0.3.3`, and
`SoapySDRUtil --info` lists `librtlsdrSupport.so (0.3.3)` with
`Available factories... hackrf, rtlsdr`. `rtl_test` and `rtl_sdr` are on
the path inside the environment. Nothing needs installing for the
receive path.

**Neither package was pinned, and both are now.** They had arrived in
this environment without ever being recorded: `linux/environment.yml` is
meant to be a full solve and named neither, and `windows/environment.yml`
named neither, so a machine built from either file got no RTL support and
no sign of why. `rtl-sdr=2.0.2=hb9d3cd8_3` and
`soapysdr-module-rtlsdr=0.3.3=h403070d_3` are in the Linux solve now, and
`soapysdr-module-rtlsdr` in the Windows list, which pulls `rtl-sdr` in
with it. Nothing else had to move: the module depends on
`soapysdr >=0.8.1,<0.9.0a0`, which is exactly what was already pinned, and
conda-forge carries a win-64 build of it.

**It will not load in a process that has already loaded the VSG60's
library.** `libvsg_api.so` links the libusb in its own install folder
(`/opt/sceptre/lib/libusb-1.0.so.0`), older than the environment's 1.0.28,
and once that is loaded it answers for `libusb-1.0.so.0` for everything
loaded after it. `librtlsdr` then needs `libusb_wrap_sys_device`, which the
old one lacks, and SoapySDR prints `dlopen() failed ... undefined symbol:
libusb_wrap_sys_device` and carries on with no RTL-SDR - no exception,
just a device that is not there. SoapySDR alone loads the module fine;
`vsg_sink.find_devices()` first, and it fails. Every app the launcher
opens runs inside the launcher's one process, so a single VSG app earlier
in a session is enough. The
HackRF is not affected: every libusb call `libhackrf` makes is in the old
library too, so a VSG60 into a HackRF on one host is fine. Loading the
environment's libusb before the VSG's, `RTLD_GLOBAL`, is the obvious fix and
is not yet tried - it would put the VSG on a libusb its vendor did not ship.

It constructs exactly like the HackRF - every app here already writes
`soapy.source('driver=hackrf', 'fc32', 1, '', '', [''], [''])`, and
`'driver=rtlsdr'` is a drop-in. The `driver=` prefix is not optional:
SoapySDR parses a bare `'rtlsdr'` as a key with an empty value, not as a
driver name.

Four things about it that are not like the other radios:

- **The sample rate has a hole in it.** librtlsdr rejects anything in
  `(300 kHz, 900 kHz]` outright with `-EINVAL` - not untested, refused.
  Legal rates are 225–300 kHz and 900 kHz–3.2 MS/s, and only about
  2.4 MS/s is reliable over USB 2.0. rtl_433's own default of 250 kS/s
  sits just inside the lower window.
- **`offset_tune` is a silent no-op on almost every dongle sold.**
  librtlsdr returns −2 immediately for R820T and R828D, and SoapyRTLSDR
  swallows the code - so it reports success and does nothing. The DC
  spike has to be handled by tuning off-centre and mixing down in
  software, or with `set_dc_offset_mode`.
- **Frequency error is large enough to matter.** A stock dongle runs
  20–100 ppm; at 433.92 MHz, 50 ppm is about 21.7 kHz, a real fraction
  of a narrowband channel. A TCXO dongle is ~1 ppm, or 434 Hz.
  `set_frequency_correction(channel, ppm)` needs exposing rather than
  assuming zero.
- **Gain is one element, `TUNER`**, 0–49.6 dB, quantised to a 29-step
  table that SoapySDR presents as a continuous range. `IF1`–`IF6` appear
  only on an E4000. SoapyRTLSDR's `setGain` reportedly casts to `int`
  before multiplying by 10, truncating fractional dB - worth confirming
  before relying on a fractional setting.

Host setup: the conda `rtl-sdr` package ships its own udev rule and
blacklist inside the prefix, so it is a symlink rather than a hand-written
file -

```sh
sudo ln -s $CONDA_PREFIX/lib/udev/rules.d/rtl-sdr.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
```

- and `dvb_usb_rtl28xxu` must be blacklisted along with `rtl2832` and
`e4000`, or the kernel DVB driver claims the device first. A blacklist
does not unload a resident module; `modprobe -r dvb_usb_rtl28xxu rtl2832`
does. On Windows the dongle needs Zadig to bind WinUSB, the same chore
the HackRF already has.

Against a HackRF at 433.92 MHz the dongle wins on the things that matter
here: a tuned, filtered front end rather than a wide-open one, a much
higher input tolerance, and a tenth of the price. Both are 8-bit. The
HackRF's wide tuning and bandwidth buy nothing at a fixed 433.92 MHz.

## The frames themselves

**rtl_433 is the specification.** Its 331 device structs and 70 `conf/`
flex specs are complete encoder descriptions in microseconds; there is
simply no encoder anywhere in the project, so the timings have to be
read out of the decoders and inverted.

### The codings, and what a 1 and a 0 look like

Six of them cover nearly the whole corpus. The field names below are the
`r_device` struct's, all in microseconds:

- **`OOK_PULSE_PPM`** - the pulse width is fixed and ignored; the *gap
  after* each pulse carries the bit. Gap ≈ `short_width` is 0, ≈
  `long_width` is 1.
- **`OOK_PULSE_PWM`** - the gap is ignored; **short pulse = 1, long
  pulse = 0**, which is the opposite of most people's intuition. A pulse
  matching `sync_width` breaks the row without adding a bit.
- **`OOK_PULSE_PCM`** - `short_width` is the pulse, `long_width` the bit
  period. Equal means NRZ, shorter means RZ. The slicer self-calibrates
  off a preamble of at least 12 toggles, so a `0xAAAA` preamble makes
  clock error stop mattering.
- **`OOK_PULSE_MANCHESTER_ZEROBIT`** - `short_width` is the clock half
  period, rising edge 0, falling edge 1. **A zero bit is hardcoded onto
  the front of every row** and cannot be turned off, so every offset
  shifts by one.
- **`OOK_PULSE_DMC`** - two consecutive shorts are 1, one long is 0.
  `tolerance` is mandatory.
- **`FSK_PULSE_PCM` / `PWM` / `MANCHESTER_ZEROBIT`** reuse the very same
  slicers; only the front end differs, turning mark into "pulse" and
  space into "gap". Every timing rule carries over unchanged.

**`tolerance = 0` does not mean zero tolerance - it changes the
algorithm.** With a tolerance set, the slicer uses symmetric windows
around each nominal and rejects anything outside them. With it at zero
it switches to midpoint thresholds with no upper bound on the long
symbol. Most stock decoders leave it at zero, which is why a synthesized
signal with ±10 % timing error decodes happily. For PCM, zero means ±25 %
of a bit period.

### What the pulse detector demands, before any decoder runs

These bind first, and they are the ones that catch people:

- **Minimum 10 samples per pulse and per gap** - 40 µs at 250 kS/s.
  Anything shorter is invisible.
- **A package ends on a gap longer than 10× the longest pulse *and*
  longer than 10 ms**, or unconditionally at 100 ms. So `reset_limit`
  alone is not enough: **every burst needs at least 10 ms of real
  silence after it.**
- **Nothing is detected until 1024 idle samples have passed**, so
  prepend at least 4.1 ms of lead-in at 250 kS/s or the first frame is
  lost.
- **Level matters.** Measured on clean synthetic files: detection starts
  around peak |I−128| ≈ 24 counts and never works at 22 or below. Aim
  for 25–50 % of full scale, peak 32–64 counts off 128.

### Four protocols, worked out

All four were synthesized from the field specification and **verified to
decode** - see the next section.

**Generic Remote / EV1527 / PT2262**, protocol 30. `OOK_PULSE_PWM`,
464/1404 µs, `tolerance` 200, `reset_limit` 1800. Sync is one short
pulse then about 31 short-periods of silence; a 0 is short-high plus
long-low and a 1 the reverse; 24 bits of 20-bit ID and 4-bit button. No
checksum at all. **You must transmit at least two frames**: the sync gap
ends the package, so the row rtl_433 sees is 24 data bits plus the
*next* frame's sync pulse, and the decoder insists on exactly 25.

**Nexus-TH**, protocol 19. `OOK_PULSE_PPM`, 1000/2000 µs, `gap_limit`
3000, `reset_limit` 5000. Measured off the corpus: 496 µs pulses, 964 µs
gap for 0, 1944 µs for 1, 36 bits, 12 repeats. ID, battery, channel,
12-bit signed temperature ×10, a constant `0b1111` nibble, 8-bit
humidity. No integrity check beyond the constant. Three traps: at least
three identical rows are required; a frame whose CRC-8 (poly 0x31, init
0x6c) over a rearranged five bytes comes to zero is *rejected* as a
Rubicson, about a 1-in-256 chance worth testing for at generation time;
and **the 37th pulse is essential** - PPM encodes in the gap after a
pulse, so without a trailing pulse the last bit is swallowed by the
frame gap.

**Acurite 609TXC**, protocol 11. `OOK_PULSE_PPM`, 1000/2000 µs,
`reset_limit` 10000. 504 µs pulses, 992/1988 µs gaps, 40 bits: ID, a
status nibble whose 0x8 bit is battery-low, 12-bit signed temperature
×10, humidity, and an 8-bit additive checksum of the first four bytes.
No repeats required. The leading sync burst is **not** needed - but a
sync burst *without* its following ~8.9 ms gap breaks the decode
entirely, because the sync bits merge into the data row and the length
comes out wrong. Simplest correct encoder: omit the sync.

**LaCrosse TX141TH-Bv2**, protocol 73. `OOK_PULSE_PWM`, 208/417 µs,
`sync_width` 833, `gap_limit` 625, `reset_limit` 1700. Four sync cycles,
then 40 bits at a fixed 625 µs period, 12 packets back to back.
**Watch the inversion**: the decoder's first statement inverts the whole
bitbuffer, so the bits on the wire are the complement of the logical
frame. Temperature is 12-bit unsigned with an offset of 500 and a scale
of 10. The check is `lfsr_digest8_reflect(b, 4, gen=0x31, key=0xf4)` -
not a CRC.

## rtl_433 as the referee

**Install it** - `sudo apt install rtl-433`, which pulls only
`librtlsdr2` alongside it. Ubuntu noble ships 23.11, from November 2023,
so the decoder set is a couple of years stale; the protocols above are
long-established and present. It lands in `/usr/bin`, outside the `gnu`
environment, so it does not touch the conda solve and is not a
dependency - it is a bench tool like `ffmpeg` on the path. Decoding in
this repository belongs in GNU Radio blocks beside `rds_core` and
`atsc_rx_core`, and the launcher should never shell out to it.

**It has no transmitter.** The README says so in its third line, and
`src/sdr.c` opens `SOAPY_SDR_RX` only. There is no encode entry point
anywhere in the tree.

**The point of it is that it decodes files.** No dongle is needed, so
the encoder can be developed and tested today, and "is my frame
correct?" stops being a circular check against our own decoder. It
grades at several levels, cheapest first:

```sh
rtl_433 -y '{40}ca21064c3d'      # a bitbuffer code - field packing and checksum only
rtl_433 -r out.cu8               # the real thing, through the DSP
rtl_433 -r out.cu8 -A            # read the timings back out and compare
```

`-y` is where to live while getting a frame right: it bypasses all DSP
and feeds the bits straight to the decoders, so field packing and
checksums can be settled before a single sample is generated.

**Four protocols were synthesized from scratch and all four decode.**
Built rtl_433 at HEAD locally (`cmake -DENABLE_RTLSDR=OFF
-DENABLE_SOAPYSDR=OFF` - no radio needed for file replay), wrote the
frames in Python with NumPy, rendered `.cu8`, and confirmed
independently:

| synthetic file | decodes as |
|---|---|
| `synth_nexus_…cu8` | `Nexus-TH  House Code: 181  Ch 2  19.00 C  71 %` |
| `synth_tx141th_…cu8` | `LaCrosse-TX141THBv2  id e7  ch 1  −20.00 C  10 %` |
| `synth_acurite609_…cu8` | `Acurite-609TXC  id 202  26.2 C  76 %` |
| `synth_ev1527_…cu8` | `Generic-Remote  House Code: 4660  Command: 8` |

So the encoder work is tractable and the feedback loop is tight. That is
the single most useful result here.

**`-A` guesses the modulation and often guesses wrong** - it called a
PPM file `OOK_PWM`. Its suggested `reset_limit` is computed from a
histogram that deliberately excludes the final gap, so it comes out far
too small and always needs widening by hand. Run it as `-R 0 -A` or the
decoders run too.

### File formats

`.cu8` is unsigned 8-bit, interleaved I,Q, zero at `0x80 0x80`. `.cs16`
is signed 16-bit little-endian. `.cf32` is IEEE-754 - **and a plain
NumPy `complex64` array, which is exactly what a GNU Radio file sink
writes, is read directly with no conversion.** That is the cleanest
interchange between a flowgraph and the referee.

Write `128 + round(127·x)`. The source carries two DC biases - 127 in
the default amplitude estimator, 128 in the magnitude estimator and the
FM demodulator - which is unreconciled upstream and harmless in
practice.

**The filename carries the metadata.** A numeric token followed by
exactly `M` is the centre frequency in MHz and exactly `k` is the sample
rate in kHz, which is why the corpus convention is
`g001_433.92M_250k.cu8`. Defaults are 250 kS/s and 433.92 MHz. Note that
`433920k` parses as a *sample rate*.

**The `.ook` text format did not work here and the claim should not be
repeated without testing.** A hand-written `.ook` was rejected with
`Input format invalid "Unknown"`, with and without a `ook:` prefix and
with the frequency and rate tokens in the name; `-w ook:` ignored the
prefix and wrote raw IQ instead. The parser exists in the source and
`PULSE_OOK` is accepted by the input path, so something about how the
format is set from the filename is not doing what it looks like it does.
Worth another look, because timings-without-DSP would be a genuinely
useful test harness.

### The corpus

`rtl_433_tests` is about 1.26 GB - 3,620 `.cu8` captures, each beside a
`.json` of expected output. Two warnings if it is ever used as a
waveform source rather than as decoder fixtures:

- **The captures are deliberately tuned off-frequency** and the offset
  is nowhere in the filename. Measured: +76.6 kHz on a Nexus file,
  −94.0 kHz on an EV1527 one. The project tells contributors to offset
  by 50 kHz or so to dodge the dongle's DC spike. Replay one at the
  frequency its name claims and you transmit tens of kHz away.
- **It has no licence at all.** No LICENSE file, GitHub reports none.
  Fine for reading and for regression tests; legally unclear for
  redistribution or for shipping a waveform derived from one.

They also peak around 181 counts, at or past the dongle's clip point,
and carry its DC spike, its image, and whatever else was on the air.
Synthesising from timings is cheaper and parameterisable, and once the
clean-up needed to make replay reliable is done, it has become
regeneration anyway.

## The encoder: `apps/ism_frame.py`

Written against `rtl_433` 23.11 from Ubuntu noble, and every claim below is
something the referee said, not something that was reasoned out. Run
`python scripts/test_ism_frame.py` to repeat the lot; it needs no radio.

**All four protocols reproduce here**, both through `-y` and rendered to
`.cu8` and read back through the real pulse detector:

| profile | `-y` code | comes back as |
|---|---|---|
| `nexus_th` | `{36}b580bef47` x4 | `Nexus-TH  id 181  Ch 1  19.00 C  71 %` |
| `acurite_609txc` | `{40}ca21064c3d` x3 | `Acurite-609TXC  id 202  26.2 C  76 %  CHECKSUM` |
| `lacrosse_tx141th_bv2` | `{40}18fed3f559` x6 | `LaCrosse-TX141THBv2  id 231  -20.00 C  10 %  CRC` |
| `ev1527` | `{25}edcbf78` | `Generic-Remote  House Code 4660  Command 8` |

Two corrections to what was written down before any of it was run. The
Generic-Remote decoder in 23.11 takes a **16-bit house code and an 8-bit
command**, not a 20-bit ID and a 4-bit button, and it inverts the whole
bitbuffer, so the wire carries their complement. And LaCrosse's channel
field reads back as `0`, not `1`.

**A row gap of zero must not overwrite the row's own trailing gap.** The
helper that lays out repeats first *replaced* the last element's gap with the
row gap, which is right for the two PPM profiles, whose rows end on a
trailing pulse with no gap of its own. LaCrosse's rows are broken by their
own sync pulses and want no added gap at all, so it got zero - and the last
data pulse ran straight into the next row's sync pulse. The two merged into
one 1041 us pulse, the row came out **39 bits instead of 40**, and that
length matched `LaCrosse-TX141Bv2`, a different sensor in the same family.
It decoded. It reported the right id and the right temperature and no
humidity at all. Nothing anywhere said it was wrong. The helper adds now.

**Exactly four repeated rows is the one count that fails.** Three decode,
five decode, twelve - what the real sensor sends - decode. At four, the
frame comes back as `TFA-303221` and `LaCrosse-TX141THBv2` never appears.
The bits are identical in every case; only the row count changes. This
turned up because a test trimmed the repeats to keep the file small, which
is the sort of thing one does without thinking. Why four is special is not
explained - see [not measured yet](#not-measured-yet).

**The Rubicson collision is real and measured at 1 in 234.** Swept 703 Nexus
frames across ids and temperatures: 3 of them satisfy Rubicson's CRC-8
(poly 0x31, init 0x6c, over `b0 b1 b2 b3&0xf0 (b3&0x0f)<<4|(b4&0xf0)>>4`),
and Rubicson is tried first, so Nexus-TH never sees them. The reading is not
garbled and no error is reported - a different model simply comes out, which
is much harder to notice than a failure. `rubicson_collides()` implements the
check and agrees with rtl_433 on all 703, so anything choosing a sensor id
can step past a bad one instead of shipping it.

**Nexus with humidity 0 is reported as `Nexus-T`,** the temperature-only
model, not `Nexus-TH`. That accounted for 19 of the 22 sweep frames that did
not come back as Nexus-TH, and it is correct behaviour rather than a
collision - but it will look like one.

**A PCM/NRZ row always comes back long.** Nothing distinguishes a trailing
zero from silence, so the slicer keeps counting bit periods into the frame
gap and adds about `reset_limit / long_width` zero bits. A 32-bit
`{32}aaaa1234` comes back as `{40}aaaa123400`. Either end the frame on a 1
or have the decoder take the length it wants from the front.

**Manchester comes back one bit longer than it went in**, always, because
the zero bit rtl_433 hardcodes onto the front cannot be turned off:
`{16}1234` in gives `{17}091a0` out.

**The LO offset survives the decode**, which is the point of it. Nexus
rendered at 1 MS/s with +100 kHz and at 8 MS/s with +400 kHz - the real
transmit configuration - both decode unchanged. 400 kHz is well outside
rtl_433's 250 kHz capture, so the leaked carrier is nowhere near the slicer.

**Rendering does not need a whole number of samples per microsecond.** It
helps - 8 MS/s places every edge exactly - but edges are laid down by
accumulating the position in whole microseconds and rounding only at each
boundary, so the error is half a sample at worst and never accumulates. At
250 kS/s, which is 0.25 samples per microsecond, LaCrosse's 417 us pulse
cannot land exactly and decodes anyway. What the renderer does refuse is a
frame whose shortest interval falls under ten samples, because the pulse
detector cannot see one and says nothing about it.

## The transmitter: `apps/ismXmitter.py`

A device from `ism_frame`, put on a radio. `scripts/test_ism_transmit.py`
runs the app exactly as the launcher builds it with the radio swapped for a
file sink, then hands what it wrote to rtl_433; all four profiles decode,
which is what proves the flowgraph and not just the encoder.

**The whole burst is rendered up front and looped out of a vector source.**
Nothing is modulated live. Every timing is then exact and none of it depends
on when the scheduler ran - which matters here more than in most apps,
because the gaps between the pulses *are* the bits. SoapyHackRF's MTU alone
is 131072 samples, about 16 ms at 8 MS/s, so a gate toggled from the Qt
thread could not place an edge inside a frame if it tried.

**The silence between bursts comes from a null source, not from the
vector.** A `stream_mux` splices `[len(burst), interval * fs]` between the
looping vector source and a `null_source`, so the idle costs no memory at
all. In the vector it would: a minute at 8 MS/s is 3.8 GB.

**The radio is tuned `offset` below the frequency asked for** and the signal
is put back as a baseband tone by `ism_frame.render`, 400 kHz by default.
The test measures where the energy actually lands - +200.0 kHz from the
radio's centre, on a file named at the radio's centre - so the arithmetic is
checked in both directions rather than assumed.

**Standby is a baseband gate, and that is enough only because of the
offset.** With the radio 400 kHz low, the carrier it leaks at I=Q=0 sits
outside a 433.92 MHz receiver's window instead of on top of the signal. The
gate is useless between bits - the edges it can place are milliseconds wide
- and perfectly good between bursts, which is all it is asked for. Measured
with the gate off: peak exactly 0, nothing decodes.

**The display that matters is the envelope in time, and it shows one repeat,
not the burst.** At a whole burst's span twelve repeats of a 496 us pulse
are a solid block and the gaps - the entire message - are invisible. It
triggers in normal mode rather than auto, so the last frame stays on screen
through the minute of silence before the next one instead of the trace going
flat. The spectrum display below it is honest about being nearly useless
here, for the reason in [atsc](atsc.md#atsc-transmitter): an average cannot
see a transmitter that is off almost all the time.

**Measuring the offset needs a *contiguous* block.** Gathering only the loud
samples splices across the gaps, breaks the tone's phase and shifts the
measured peak by about a kilohertz - the first version of that check read
+199.1 kHz for a tone that is exactly +200. The keying is amplitude on a
phase-continuous tone, so one contiguous window puts the carrier in a single
bin with the keying sidebands either side.

**A HackRF gets 8 MS/s and the other two get 2.** Great Scott Gadgets say
not to run a HackRF below 8, and 8 MS/s is exactly 8 samples per
microsecond; 2 MS/s is still a whole 2 and costs a quarter of the memory,
which is worth having when the burst is held in RAM. A Nexus burst at
twelve repeats is 941 ms - 60 MB of `complex64` at 8 MS/s.

**The dialog builds itself out of `PROFILE_INFO`,** so a fifth protocol is
one edit in the module that knows about protocols and none in the app. It
also builds the frame as the fields are typed, which is cheap and catches
the two hazards that would otherwise only appear as a decode under the wrong
model name: a Nexus id that collides with Rubicson, and a humidity of zero.
`lacrosse_tx141th_bv2` refuses four repeats outright for the same reason.

**Measured on the VSG60** (serial 26050377, at −120 dBm): the app streams
at 1.99-2.01 MS/s, keeps streaming through power, frequency and standby
changes, releases the device lock on stop and opens it again cleanly. That
proves the sink path and not the RF - there was nothing to receive with.

## The receiver: `apps/ismReceiver.py`

**rtl_433 is the decoder, and it never touches a radio.** The app runs it
as `rtl_433 -r cf32:- -f <freq> -s 250k -F json -M level -M protocol` and
writes the channel into its stdin; a thread reads the JSON lines back and
the window shows them in a table. So it works with whichever radio Settings
name - HackRF, USRP or BB60D - and needs no RTL-SDR at all, which is what
let it be built and tested before one arrived. Fed a live stream, rtl_433
prints a decode within about half a second of the burst ending: it reads
its stdin in 262144-byte blocks, 0.13 s of `cf32` at 250 kS/s.

**What it is fed is raised to an RTL-SDR's level, and that is worth 10 dB.**
rtl_433 judges its samples on an 8-bit RTL-SDR's scale, where the noise is a
few steps of 8 bits. At the noise level a HackRF at 40 % gives the channel,
0.003 of full scale, a burst needed a peak of 0.05 to decode at all however
clean it was - the same samples decoded at their own scale and gave nothing
at a third of it. With the noise raised to 0.03 the same burst decodes down
to 14 dB SNR rather than 24. Over the air the loop went from decoding to
−30 dBm (Nexus once at −40) to decoding all four at −40 and two at −50, at
13-15 dB SNR. The gain puts the noise - the 20th percentile of the last two
seconds, so bursts do not count - at 0.03, moves a fifth of the way there
each 8192 samples, never turns anything down, and holds each block's peak
under 0.9: rtl_433 lost an on-off burst driven past full scale, too. Found
in fm-receiver, and ported from its `rtl433.py`, as is the next paragraph.
The test's weak burst - peak 0.016, 17 dB over the noise - decodes with it
and not without.

**Raised marginal bursts also decode as the wrong model, sometimes.** At the
edge the loop gave `Secplus-v1` once beside EV1527's own decodes, and
`TFA-303221` once beside LaCrosse's - the model the four-repeat puzzle below
produces. The right model was decoded at every level where either
appeared, but near the limit the table can carry a stray row. Each decode's
SNR is in the table for exactly this.

**The pipe never holds up the radio.** rtl_433's stdin is non-blocking and
1 MB deep, half a second. A write that would block is left pending, and
while it is, whole blocks of samples are dropped and counted - never part
of one, so what rtl_433 does get keeps its timing. A blocking write would
stall the flowgraph instead and overflow the radio. The status line says
how much was dropped; the offline test, at twice real time, drops nothing.

**`-f` comes before `-s`, and a retune starts a new rtl_433.** The frequency
only labels what is decoded - rtl_433 tunes nothing here - but without it
every decode said 433.92 MHz wherever it came from. Above 800 MHz a `-f`
*after* `-s` puts the rate back to rtl_433's own default there and nothing
decodes (found in fm-receiver); rtl_433 then prints "New defaults active",
which is harmless with the `-s` after it. rtl_433 cannot be told a new
frequency, so a retune starts another on it and points the pipe there.

**It can log every decode**, as a line of JSON with the time it was heard,
to `ism-<date>-<time>.jsonl` in the media folder - a box in the dialog. A
level sweep can then be graded from the file rather than read off a
screen.

**250 kS/s is what it is handed**, rtl_433's own default and the rate every
decoder is tested at. The radio runs at 2 MS/s (2.5 on a BB60D) and a
`freq_xlating_fir_filter` shifts, filters to ±110 kHz and decimates by a
whole number, as the RDS receiver's front end does.

**The radio is tuned 300 kHz low** and the channel is shifted back, so the
radio's own DC spike lands outside the filter. The transmitter's leak, at
its 400 kHz default, then sits 100 kHz below this radio's centre - 400 kHz
from the channel after the shift, and filtered out too. The two offsets are
independent and neither app needs to know the other's.

**A carrier inside the channel is fatal, and a decode alone does not prove
there isn't one.** `scripts/test_ism_receive.py` runs the real app on
synthetic samples carrying both carriers at 0.6, louder than the 0.4 signal.
All four profiles decode at about 46 dB SNR, and noise with the carriers
decodes as nothing. Moved inside the channel, the leak did two different
things: 20 kHz from the signal, where it beats against it, nothing decoded
at all; exactly on the signal, one burst of two still decoded - at 4 dB. So
the test also demands 20 dB of SNR, which is why `-M level` is on. Clean
decodes measure 32-47 dB; EV1527 is lowest, since its rows are shorter than
the blocks the level into rtl_433 is set on.

**Copies of one frame are one row.** A sensor sends its frame several times
and some decoders report every copy - EV1527 came back eight times from two
bursts, Acurite six. The same reading within 2 s of the last is counted in
the row's Copies column rather than added as a row. Two transmissions less
than 2 s apart count as one too; a sensor sends once a minute, so that is
the right way round to be wrong.

**The envelope's trigger follows the noise floor**: four times the lowest
slow average of the envelope in the last few seconds, which a burst cannot
raise. A fixed level would be right at one gain and wrong at every other.
In normal trigger mode the plot draws nothing until the first burst, and
until then its time axis says 16 ms: only a capture corrects it, and
setting the sample rate again does not.

**Over the air, into a BB60D and into a HackRF, all four decode and every
field is right.** Antenna to antenna on one bench, not a cable - the level
where decoding stops is therefore the path between two antennas as much as
either radio, and is only worth comparing within a run.

*Into the BB60D* on `worklaptop1`, with rtl_433 there inside fm-receiver (a
separate app, in its rtl_433 mode, logging each decode as JSON).
`scripts/test_ism_loop.py --transmit-only` stepped each device from −20 to
−110 dBm, 8 s a level, and the log was graded against its timetable - the
two clocks agree to a few tens of milliseconds. 150 decodes, every one the
right model, id and reading; no other model appeared. Bursts decoded, of
about six a level (four for Nexus), and rtl_433's best SNR:

| VSG | Acurite | EV1527 | LaCrosse | Nexus |
|---|---|---|---|---|
| −20 dBm | 6, 33 dB | 6, 41 dB | 5, 41 dB | 4, 41 dB |
| −30 dBm | 7, 33 dB | 7, 40 dB | 6, 40 dB | 4, 38 dB |
| −40 dBm | 0 | 6, 20 dB | 6, 28 dB | 4, 29 dB |
| −50 dBm | 0 | 1, 19 dB | 1, 18 dB | 1, 20 dB |
| −60 and below | 0 | 0 | 0 | 0 |

Decoding stops at about 19 dB of rtl_433's SNR, −74 dBFS at that receiver.
The BB60D's gain moved 10 dB during the run (EV1527 at −40 dBm arrived at
−74 dBFS, LaCrosse and Nexus at −64), which is why Acurite, sent first,
failed at −40 while the others decoded; at −20 and −30 dBm the level sat at
−53 dBFS both times, and below that it tracked the VSG dB for dB.

*Into the HackRF* on this machine, `ismReceiver` itself at 40 % gain - the
most this antenna allows at 433 MHz: 50 % peaks at half scale on noise and
broadcast FM alone, 70 % clips. `scripts/test_ism_loop.py`:

| VSG | Acurite | EV1527 | LaCrosse | Nexus |
|---|---|---|---|---|
| −20 dBm | 4, 37 dB | 5, 37 dB | 3, 37 dB | 3, 35 dB |
| −30 dBm | 3, 27 dB | 5, 36 dB | 5, 27 dB | 3, 35 dB |
| −40 dBm | 0 | 0 | 0 | 1, 26 dB |
| −50 and below | 0 | 0 | 0 | 0 |

That is before the samples were raised to rtl_433's level. After, the same
bench, antennas and gain:

| VSG | Acurite | EV1527 | LaCrosse | Nexus |
|---|---|---|---|---|
| −20 dBm | 4, 36 dB | 5, 36 dB | 3, 35 dB | 3, 36 dB |
| −30 dBm | 3, 26 dB | 5, 36 dB | 4, 35 dB | 2, 33 dB |
| −40 dBm | 4, 15 dB | 3, 15 dB | 1, 14 dB | 2, 24 dB |
| −50 dBm | 1, 14 dB | 1, 15 dB | 0 | 0 |
| −60 and below | 0 | 0 | 0 | 0 |

**Only with the VSG60 in a process of its own.** Every first attempt at the
HackRF loop decoded nothing, at any level and any gain, and the reason was
not RF: a VSG60 opened in a process where a HackRF is already streaming
takes every sample at its full 2 MS/s and transmits none of them - not the
signal, not even its own LO leak. The HackRF carries on receiving
normally. Opened the other way round, VSG first, a −20 dBm carrier came in
64 dB over the noise; with the VSG in a second process, both orders work.
So the loop script runs its transmitter as a subprocess. Why is not known;
the VSG's library bringing its own libusb (see the RTL-SDR section) is the
obvious suspect, and nothing more than that. See
[radios](radios.md#vsg60-notes).

**The two radios disagree about frequency by 5.8 kHz** at 433.92 MHz, about
13 ppm, the HackRF's crystal almost certainly. It is nothing to rtl_433's
±110 kHz channel, but it cost an afternoon: a check that looked for the
carrier within ±5 kHz of where it should be read the sidelobe beside it and
called a 64 dB carrier absent.

**Tables had no place in the flowgraph theme**, since no flowgraph had one;
`QTableView` and `QHeaderView` are now in `_FLOWGRAPH_BASE_QSS`, a well
with a panel-coloured header.

## Prior art

**Synthesise; do not replay, and do not go looking for a module.**

- **`gr-ook` does not exist.** Nor does `gr-rtl433`, `gr-433` or
  `gr-weather`. `gr-ism` exists but is an iSmartAlarm decoder, archived
  in 2017. The searches that suggest otherwise are finding flowgraphs
  and stubs.
- **`tx_tools`** (triq-org, GPL-2.0, by the rtl_433 maintainer) is the
  real thing for synthesis - `pulse_gen` and `code_gen` turn a text
  description into IQ in exactly the formats rtl_433 reads. Its README
  says "mostly untested pre-release quality code", and `tx_sdr` claims
  HackRF but is documented as tested only on LimeSDR.
- **`txtpms`** (MIT, 2020) is the closest precedent: four rtl_433 TPMS
  decoders hand-ported into encoders, rendered through a GNU Radio FSK
  modulator to `.cu8`. GNU Radio 3.7/3.8-era `.grc`, so it needs
  regenerating, but it is a working existence proof.
- **`hackrf_ook`** (maintained, 2025) is the most useful reference for
  the HackRF path - 8 MS/s, all timings in µs multiplied by 8, the whole
  frame including the trailing pause in one buffer, looped in the
  callback. It does no pulse shaping at all, which is the gap worth
  closing.
- **URH** is archived read-only as of December 2025; `urh-ng` continues
  it and claims native BB60 support, which is unverified and directly
  interesting here.
- **`PiCode`** and pilight carry real microsecond timing tables for
  about 60 OOK protocols, with no name mapping to rtl_433's 386.

The architectural model to copy is **`gr-rds`**: one module with matched
encoder and decoder blocks, which is exactly the shape this repository
already has for RDS and ATSC.

## Not measured yet

Everything above is from source, documentation, or software-only tests.
These need a bench:

- Whether a VSG60 and an RTL-SDR will stream at the same time on one
  host. A VSG60 and a BB60D will not - see
  [radios](radios.md#vsg60-notes) - and that finding is the reason to
  ask. Different USB chipset entirely, so there is reason for optimism.
- A HackRF's actual output power at 433.92 MHz, which is interpolated
  from a band figure above.
- Whether gr-soapy's underrun marker can ever fire on a HackRF. Reading
  both sources says no; that is a strong claim to put in a note without
  running it.
- The real on/off ratio each transmitter achieves at the receiver, and
  therefore how much baseband offset is actually needed.
- Typical FSK deviations for real 868 MHz devices. rtl_433 does not
  record deviation per device, and no published table turned up. The
  BB60D can measure one.
- Whether the `.ook` text path can be made to work, per the note above.
- Why **exactly four** repeated LaCrosse rows decode as `TFA-303221`
  while three, five and twelve all give `LaCrosse-TX141THBv2`. Both
  decoders invert the shared bitbuffer and both run
  `bitbuffer_find_repeated_row` with a threshold that steps at five
  rows, so an interaction between the two is the obvious suspect - but
  that is a guess, and it needs reading the dispatch order rather than
  more experiments. Software only; no bench needed.
