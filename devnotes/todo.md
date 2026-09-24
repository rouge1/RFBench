# Open Bench Work

This is the short list of work that still needs a real bench or an off-air
signal. Durable measurements and decoder findings belong in the subject note,
not here.

- Measure the FPV transmitter when it is available.
- Re-test the NTSC receiver over the air after the decoder changes.
- Find out whether a VSG60 and an RTL-SDR will stream at the same time on
  one host. A VSG60 and a BB60D will not, which is why this is worth
  asking. See [ism](ism.md#not-measured-yet).
- Measure a HackRF's actual output power at 433.92 MHz; the figure in
  [ism](ism.md) is interpolated from a whole-band range.
- Confirm whether a HackRF TX underrun can ever be reported through
  gr-soapy. Reading both sources says it cannot, which is a strong claim
  to leave in a note unrun.
- Measure the on/off ratio each transmitter really achieves at the
  receiver, and how much baseband offset it takes to hide the LO leak.
- Measure the FSK deviation of a real 868 MHz device with the BB60D;
  rtl_433 does not record it and no published table turned up.
