# Signal Hound BB60D and VSG60A: calibration

Whether either instrument can calibrate itself, and what needs a lab. This is from
Signal Hound's manuals and API reference (read 2026-10-21; sources at the end),
with what it means for the bench. The numbers the calibration is held to are in
[signal-hound-specs.md](signal-hound-specs.md).

**Neither instrument has a calibration source inside it.** There is no internal
reference tone or level standard. Both are calibrated at the factory, in an
environmental chamber, and the correction tables are stored on the device and
applied by the API. What the BB60D does for itself is apply its stored
corrections at its present temperature. Anything traceable needs outside
equipment: Signal Hound's field calibration software and a lab's standards, or a
return to Signal Hound.

| | BB60D (receiver) | VSG60A (transmitter) |
|---|---|---|
| Factory | adjusted in a chamber; corrections stored on the device | I/Q balance, DC offset and output level adjusted across frequency and temperature in a chamber, stored in internal flash, applied in real time on the PC |
| Temperature | recalibrates at open and on every `bbInitiate`, at the temperature then; Signal Hound asks for it again after a change of 2 °C | the stored corrections cover temperature; nothing to call |
| Self-calibration call | none needed: `bbSelfCal` is for the BB60A only | none |
| Temperature reading | `bbGetDeviceDiagnostics`, 1/8 °C | none found in the manual |
| Recommended cycle | one year | one year |
| Field calibration needs | two signal generators (an MXG or equivalent), a function generator, a power meter and E9304A sensor, a spectrum analyser to 7.5 GHz, a splitter, a 10 dB pad, two couplers, GPIB | a power meter and E9304A sensor, and a Signal Hound **SM200A/B** as the analyser |
| User adjustment | the 10 MHz timebase, through the calibration software | the 10 MHz timebase, and the DC offset to null LO feedthrough |

## The BB60D and temperature

The API's theory of operation is plain about it: "The device is temperature
sensitive", and it should be "re-calibrated when significant temperature shifts
occur (+/- 2 °C)". The device cannot do that by itself, because it would interrupt
streaming, "Therefore, we leave calibration to the programmer." The `bbSelfCal`
reference adds that 2 °C "measurably" alters the IF's shape and amplitude, and that
a swing of 10 °C or more can put a dB or more of error into the amplitude.

On the BB60D, as on the BB60C, the call is not `bbSelfCal` but `bbInitiate`:
"simply call bbInitiate again which will re-calibrate the device at its current
operating temperature." One calibration is made when the device is opened.

`bbGetDeviceDiagnostics` returns the temperature, updated after each sweep and
"periodically while I/Q streaming", with the USB voltage and current. Below 4.4 V
on the USB the readings "may be out of spec". The skinny FM receiver reads it every
5 s while streaming at about 10 µs a call and nothing dropped. The current comes
back in mA, and Signal Hound gives no temperature limit.

## The VSG60A

The VSG60A manual describes no self-calibration and no temperature call. Its
corrections were measured across temperature at the factory and are applied as it
runs. Its internal 10 MHz is a VCTCXO with a 16-bit tuning DAC: initial accuracy
±1 ppm, ageing ±1 ppm a year, and "periodic adjustments will typically keep the
internal timebase to better than 0.1 ppm" at a stable temperature. Its absolute
level error, ±2.0 dB, is specified only in CW mode from −55 to +7 dBm.

## The 10 MHz adjustment is limited

Both timebases can be adjusted in the field by the calibration software, and the
adjustment is "permanent until the device is returned to Signal Hound". On the
BB60D there is "a limited amount of FLASH memory dedicated to 10 MHz reference
adjustments, so adjustments should not be performed more than once per month",
and adjusting it without a reference within 0.2 ppm "is discouraged". Neither
should be adjusted here without a good reference. An external 10 MHz on the input
corrects the frequency without touching the flash, and does nothing for level.

## Warm-up

No warm-up time is specified for measuring. The field calibration manual asks for
"at minimum 15 minutes of idling time" connected to the PC before a calibration
starts, so that the device reaches a stable operating temperature. In a Signal
Hound forum thread, warming the unit up first was suggested for drifting readings.

## What it means for the bench

- **Every bench recording calibrates the BB60D afresh.** Each `bt_bench.py record`
  is a process of its own that opens the device, and opening calibrates it at the
  temperature then. A 40 s recording is too short to drift 2 °C. Whether the
  SoapySDR module also calls `bbInitiate` when the stream starts has not been
  checked.
- **The BB60D's floor moving about 1 dB from day to day is within what Signal Hound
  describes.** It was seen at the bench: the same −68 dBm shot read 20.1 to 20.3 dB
  SNR on 2026-10-20 and 21.3 dB on 2026-10-21. That is one reason the bench takes
  the level from the SNR in the capture and not from the level set.
- **The VSG60A at −68 dBm is outside its specified level range** (−55 to +7 dBm in
  CW), so its set level has no specified accuracy there either.
- **Nothing here can calibrate either instrument.** The VSG60A's field calibration
  needs an SM200A/B, and the BB60D's needs a lab of Agilent standards. A traceable
  level means a return to Signal Hound, or a calibration certificate bought with the
  unit.
- **The instruments' temperature is not logged.** `bbGetDeviceDiagnostics` could put
  the BB60D's into each recording's `.rec.json`. It has not been built.

## Sources

- BB60 API, theory of operation (calibration and temperature):
  <https://signalhound.com/sigdownloads/SDK/online_docs/bb_api/theory_of_operation.html>
- BB60 API reference (`bbSelfCal`, `bbGetDeviceDiagnostics`, `bbInitiate`, the
  10 MHz options): <https://signalhound.com/sigdownloads/SDK/online_docs/bb_api/bb__api_8h.html>
- BB60D user manual, section 5 "Calibration and Adjustment" and the preliminary
  specifications: <https://signalhound.com/sigdownloads/BB60C/BB60D-User-Manual.pdf>
- VSG60A product manual, sections 4 "Calibration" and 5 "Adjustments", the
  hardware-software interface and the timebase:
  <https://signalhound.com/sigdownloads/VSG60/VSG60A-Product-Manual.pdf>
- Field Calibration Software user manual, v1.13.13, 4 Sep 2024 (the cycle, lab
  standards, the 10 MHz adjustment, the 15 minute idle):
  <https://signalhound.com/sigdownloads/CalSoftware/Field-Calibration-User-Manual.pdf>
- BB60D product page (factory calibration, the optional certificate, temperature
  ranges): <https://signalhound.com/products/bb60d-6-ghz-real-time-spectrum-analyzer/>
- The skinny FM receiver's notes on `bbGetDeviceDiagnostics`, in its
  `knowledge/roadmap.md` and `knowledge/capabilities.md` (a separate project here).
