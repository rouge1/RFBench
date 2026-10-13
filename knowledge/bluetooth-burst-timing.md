# Burst start and burst end: how to measure them, and what is the device's and what is the receiver's

Written after bluey-ox-walker asked where its 54-bit "header" window lands against the end of an ID's 68th
bit, and found that the "all ones" after an ID was its slicer's local threshold and not a transmitted tail.
The numbers marked *(Muse)* were measured by a reviewer on the `slotb2` files; the rest is from the
generators' definitions. Nothing here has been measured on a real transmitter by us: bluey's captures are
its own to report.

Read with [bluey-test-signals.md](bluey-test-signals.md) (the `slotb` and `slotb2` files and their keys) and
[bluetooth-core-spec-citations.md](bluetooth-core-spec-citations.md).

## The two times, and which is the better measurement

- **Start.** `start_sample + timing_frac` is the first access-code symbol's nominal start, not where the
  power rises. An access-code correlator (preamble + sync word, 68 symbols of known shape) places it to
  about 0.1 to 0.5 sample (2.5 to 12 ns at 40 MS/s) at high SNR. An energy edge is much worse: the ramp-up
  and the Gaussian filter smear the first symbols, so a power-edge start is early or late by the
  transmitter's own ramp.
- **End of an ID.** An ID is exactly 68 bits at 1 Msym/s, so the end of bit 68 is `start + 68 us`. A
  20 ppm clock error moves it by 1.4 ns. The files carry it as `id_end_sample` / `id_end_us` (`slotb2`,
  before the ramp-down). This is a better reference than any measured edge, because it comes from the
  correlator start.
- **What a power edge is for.** Not for finding the end, but for measuring what the transmitter *does*
  after it: compare the fall-off with `start + 68 us`. That difference is the device's ramp-down, and it is
  the quantity worth measuring.

## Measuring the fall-off accurately

1. Mix to the channel carrier and low-pass it. Use a **zero-phase** filter, or subtract the group delay: a
   causal 4th-order Butterworth at 0.7 MHz adds about 0.6 us, which is the same size as the thing measured.
2. Take power, and **fit a known ramp model** to the fall-off instead of thresholding it (a threshold
   moves with SNR and level; a fitted edge does not).
3. Average over many bursts of the same device and channel: the error falls as 1/sqrt(N). IDs are plentiful
   (87 % of the records in bluey's real case).

In `slotb2` the ID ends with a 2 us raised-cosine ramp-down that starts at the end of the last symbol: the
amplitude half-point is at `id_end + 1 us`, and the power half-point about `id_end + 0.6 us`, seen through a
+-0.8 MHz brick-wall filter *(Muse)*. At 20 dB SNR the fitted ramp start was within 0.1 us of `id_end_sample`
at the median and under 0.5 us at the 95th percentile *(Muse)*. At 10 dB SNR in 1 MHz expect a few tenths
of a us per single burst.

## What is the device's, what is the receiver's, and what to calibrate

| Part | Whose | Same for every device? | How to get it |
|---|---|---|---|
| 68 bits at 1 Msym/s, so end = start + 68 us | the standard | yes | nothing to measure |
| Correlator start offset, filter group delay, decimation delay of the capture chain | **the receiver's** | yes for a given receiver, rate and filter | **once**, locally, on files of known truth (the `slotb`/`slotb2` files) |
| Ramp-up and ramp-down shape, how long the carrier lingers, synthesiser settling | **the transmitter's** | **no**: bluey saw carrier still on after bit 68 in 15 of 64,454 ID records, and raw power back at noise anywhere from +5 to +20 us | per device, from its own bursts |
| Carrier offset (a few tens of kHz here, mostly negative) | the transmitter's | no, and it moves the slicer threshold | per device and channel, from the bursts' own sync words |

So: calibrate the receiver's own constants once with a local experiment on known-truth files, and treat the
transmitter's ramp as per-device. That second half is **our recommendation, not something we measured**: the
spread bluey reports (+5 to +20 us) is the evidence that one device's ramp cannot be assumed for another.

## What follows for a decoder

- Place the window from the **correlator start + 68 us**, never from a detected power drop: then a device's
  ramp cannot move it.
- Make the slicer's threshold not depend on what follows the burst. A local mean taken after the burst
  inherits the ramp and the noise (this is what read noise as ones on bluey's captures); a zero threshold
  after removing each burst's own carrier offset, or a threshold from the burst's own sync word, does not.
- If the ramp is needed (to tell a lingering carrier from noise), estimate it **per device** (LAP and
  carrier-offset group) from the first N IDs, and keep it as a per-device parameter; do not carry one
  device's number to another.
- The grid offset and the 625 us slot index depend on the start only: use the correlator start, and expect
  to be wrong by the correlator's 0.1 to 0.5 sample, not by the ramp.
