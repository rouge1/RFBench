# A Logitech 2.4 GHz mouse link from a recording (Prac_Ex_9_Mouse)

What a Sceptre training recording of a "Logitech wireless mouse" turned out
to hold when it was taken apart bit by bit, 2026-11-24. This was a
challenge from the user. skinny-fm made an independent read in parallel, and
the CRC is its find (its scripts are kept on its own side). Each
claim below says how firmly it stands.

## The file

`Prac_EX_9_Mouse.cdif`, in the Sceptre recordings folder on the 4TB drive
(see [4tb-catalog](4tb-catalog.md)). An identical
copy, `Prac_Ex_9_Mouse.cdif`, sits beside it. 24.9 GB.

A Sceptre `.cdif` is a Midas **BLUE** file:

- **Header:** a 512-byte header beginning `BLUE` `EEEI` `EEEI` (little endian).
  It is type 1001, format `CF`: complex float32 interleaved I/Q.
- **Fields:** `data_start` (double at 0x20) is 512 and `data_size` (double at
  0x28) is in bytes. The adjunct `xdelta` (double at 0x108) is 1/fs.
- **Centre frequency:** this is *not* in the main header. It is in the
  extended header, at `ext_start` × 512 bytes (int32 at 0x18), which here
  starts right after the data. Its keywords hold `RF_FREQ`, `COL_RF` and
  `DATA_RF` = 2.463 GHz, `SAMPLE_RATE` = 28 MS/s, and
  `SCEPTRE_SESSION=session-signalhoundbb60-20220727`: a BB60, 2022-07-27,
  111.2 s.

Read it with `np.memmap(f, np.complex64, offset=512)`. The values are
unscaled, in the tens of thousands.

## What is in the band

- **Wi-Fi beacon:** a channel 11 beacon every ~102 ms (the wide bursts in the
  middle).
- **The mouse link:** two narrow GFSK signals, one each side of the Wi-Fi. This
  is what the class notes call the "rocket ship".

## The link (measured, firm)

- **Modulation:** GFSK at **2 Mbit/s**. The symbol clock line is at 2.000 MHz,
  and the deviation is about ±450-500 kHz.
- **Channels:** two at a time, on a 1 MHz raster, alternating every 1 ms, so
  each channel is used every 2 ms. This is the "2 ms frame rate" in the class
  notes.
  - **0-20.3 s:** 2452 and 2473 MHz.
  - **20.3-92.6 s:** 2452 and 2475 MHz. 2473 is dropped as the Wi-Fi edge
    chewed it up.
  - **92.6 s onwards:** 2454 and 2475 MHz.
  
  The channel set changes on interference: this is adaptive, not a fixed hop
  sequence.
- **Exchange:** two devices take turns every 1 ms. Device **A**
  (carrier offset ≈ −112 kHz) sends 28 µs of unmodulated carrier and then
  ~100 µs of data. Device **B** (≈ −40 kHz, 7 dB stronger at the BB60) starts
  its carrier 33 µs after A ends and sends 69 µs of carrier and then ~100 µs of
  data. Both packets are the same length every time: 129 µs and 171 µs
  including the lead-ins.
- **Rate:** exactly 500 exchanges/s per channel for 92 s. A survey of the whole
  file found 46 169 A and 45 967 B bursts on 2452 MHz alone.

## The frame (confirmed by CRC)

```
carrier lead-in | 1010… preamble (~10 bits seen) | 14-bit sync | 152 bits (19 octets) | CRC-24
```

- **Sync:** A uses `11100000010110` and B uses `00011111101001`, which is the
  exact bitwise complement of A's.
- **CRC:** **BLE's polynomial 0x00065B**, MSB first, not reflected, over the
  152 bits after the sync, preset **0x44188E**. The preset is the same for both
  devices and constant across the file, which makes it a per-link value like
  BLE's CRCInit. skinny-fm solved it, and here it checked on 12 814 of 12 837
  complete packets (99.8 %). CRC-16 CCITT, as used by Nordic ESB and Unifying,
  fits nowhere.
- **First octet:** B's low nibble is fixed per channel (`0101` on 2473 and
  `1001` on 2452), and its high nibble cycles through four or five values
  (sequence and ack bits). The `1110` value runs at a steady ~6 % for the whole
  file, so it does not track activity.
- **The other 18 octets:** consecutive packets differ in ~50 % of their bits,
  and nothing repeats. Berlekamp-Massey gives linear complexity ≈ n/2. A
  packet resent on the other channel is bit-identical, so the scrambling does
  not depend on the channel the way BLE whitening does. The payload is
  **encrypted, or scrambled with a per-packet key**. No HID report can be read
  from it.
- **Can it be decrypted? Not from this file (2026-11-24).**
  - **What is public:** LOGITacker (Marcus Mengs) describes LIGHTSPEED as
    AES-CTR with a per-device link key set at pairing. It is in the Unifying
    family but uses its own scheme, with two AES-CTR keys per frame.
  - **What the capture shows:** it is *consistent* with that, but cannot
    identify the algorithm itself.
  - **The two published ways to the key:**
    - capture the over-the-air pairing or re-pairing (CVE-2019-13052);
    - dump the key from the receiver dongle over USB (CVE-2019-13054/13055,
      the `munifying` tool).
  - **Why neither works here:** this capture is in steady state from t=0, with
    no pairing frames, and there is no dongle.
  - **Second check:** sdr_grok measured packets that share the same first
    octet and found they still differ by a median of ~65 of 144 bits, so that
    octet is not a nonce repeating over a stable report.
  - **One loose end:** the first two payload octets flip only ~2-3 bits of 8,
    against ~4 for the other 16. They may be a counter or another field that
    is not encrypted. That does not reach the report itself.

## The make and model

- **Make, Logitech:** this comes from the class notes, not from the RF.
- **Family, LIGHTSPEED (inference, fairly strong):**
  - It is not Unifying. Unifying is Nordic ESB: a 3-5 octet address, CRC-16,
    unencrypted mouse reports and ~8 ms reports.
  - It is not Bolt. Bolt is BLE with a 7.5 ms connection interval, a 32-bit
    access address and channel-dependent whitening.
  - LIGHTSPEED is Logitech's "2.4 GHz (custom)" protocol with a 1 ms
    (1000 Hz) report rate, and that is what a constant 1 kHz exchange looks
    like. The G305's spec page, for example, gives mouse M-R0071, receiver
    C-U0008, "2.4 GHz (Custom)", 1 ms report rate.
- **Model, not provable from this recording:** any LIGHTSPEED mouse sold
  before July 2022 fits (G305, G502 LIGHTSPEED, G703, G903, G Pro Wireless,
  PRO X SUPERLIGHT ...). An FCC test report of each receiver, giving the
  channels and emission, would be the way to narrow it.
- **LOGITacker's channel tables don't help:** its LIGHTSPEED tables
  (`{1,49,56,41,79,80,81,24,89,78,26,3}`) do not contain 52/71/73/75 either, so
  they cannot pick a model.

## What the mouse is doing

The content cannot be read, so the answer comes from the traffic:

- **0-92.6 s:** the link is up and both sides send fresh, fixed-length
  packets every 1 ms. Because the payload is encrypted with what behaves like
  a counter, an idle mouse would look the same. Moving, clicking and scrolling
  **cannot be told apart** in this capture: no packet length, rate or header
  bit was found to change with activity.
- **~20.3 s:** an adaptive channel change, 2473 → 2475 MHz.
- **5-12 s and 88-90 s:** both devices' levels swing by several dB
  (B by up to ±4 dB). Something moved near them, such as a hand on the mouse.
- **92.6 s:** B stops answering. A carries on alone at 1 kHz, sending each
  packet on both channels (2454 and 2475) and changing its header nibble:
  it is retrying with no reply.
- **~108 s:** B returns, sending on both channels while A drops away. It
  is reconnecting.

**Which device is the mouse is not settled.**

- **For B:** a USB receiver cannot vanish unless it is unplugged, while
  a mouse switched off at 92.6 s and back on at 108 s would give exactly this.
- **For A:** skinny-fm first read it the other way. A transmits first and
  retries without ACKs, which is what a mouse that has lost its receiver would
  do. It later withdrew this, because a receiver that polls also starts every
  exchange. Both reads now put B as the mouse first, though it is still not
  proven.

Either way, the event is **the link dropping at 92.6 s and coming back at
~108 s**, most simply the mouse being switched off and on, or taken out of
range.

## Tools

The scripts are in the session scratchpad and are not kept in the repo:

- **Whole-file survey:** a 64-bin FFT per 2.3 µs, with power per channel.
- **Tracker:** detects bursts, finds the channel by FFT peak, applies an FM
  discriminator, and slices at 14 samples per bit.
- **CRC-24 check.**

The tracker's burst-edge threshold cuts the end off many weaker packets (only
~13 k of ~180 k were complete enough for the CRC). Counts of "good" packets
over time are therefore an artefact, not activity.
