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
- Work out why exactly four repeated LaCrosse rows decode as
  `TFA-303221` rather than `LaCrosse-TX141THBv2`, when three, five and
  twelve all decode correctly. Needs no hardware - it is reading
  rtl_433's decoder dispatch. See
  [ism](ism.md#the-encoder-appsism_framepy).
- Close the ISM loop on a cable: the VSG60's `ismXmitter` into a HackRF
  running `ismReceiver`, all four profiles, and sweep the VSG's level down
  to find where each stops decoding. Nothing here has been received off a
  real radio yet. See [ism](ism.md#the-receiver-appsismreceiverpy).
