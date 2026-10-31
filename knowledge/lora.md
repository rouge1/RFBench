# LoRa: the PHY, and how to send and decode it

Nothing here has been transmitted yet. The rules below come from the
gr-lora_sdr source (EPFL, Tapparel et al.), Semtech's SX1276 datasheet and
the LoRa Alliance documents. They were then **checked against a real LoRa
transmitter** on 2026-10-31: 14 captures from rpp0's gr-lora-samples, 868.1 MHz,
125 kHz, SF7, coding rates 4/5 to 4/8, CRC on and off. A numpy decoder written
from these rules decodes them. A numpy encoder written from the same rules,
given the padding the transmitter put in its last block, reproduces every
symbol of every frame that came through clean: 72 of 72 on the 23-byte frames,
and all of each one-byte frame at 4/5, 4/6, 4/7 and 4/8. See [what the
captures showed](#what-the-captures-showed).

Not checked against anything real: SF other than 7, bandwidths other than
125 kHz, low data rate optimisation, implicit header, and SX126x chips.

The documents, four code repositories and the captures are in `docs/lora/`,
which git ignores; `docs/lora/SOURCES.md` lists each with its URL and sha256.
The numpy encoder and decoder that were checked against the captures are
there too (`docs/lora/numpy/`: `lora_phy.py`, and `rx_capture.py FILE SF` to
decode a capture), as is Grok's cited research brief. They are not in the repo
yet.

The `css` kind in `scripts/signal_mix.py` (the game-night generator) is *not*
LoRa: it sends a LoRa-like preamble and then raw symbol values, with none of
the coding below.

## The chirp

A symbol is one chirp of N = 2^SF chips at one chip per second per hertz:

```text
chip rate  = BW
Tsym       = 2^SF / BW            SF7 at 125 kHz: 1.024 ms; SF12: 32.768 ms
symbol s   = an upchirp whose frequency starts at s*BW/N - BW/2, rises BW/N per chip,
             and wraps from +BW/2 to -BW/2
```

gr-lora_sdr's `build_upchirp` (`include/gnuradio/lora_sdr/utilities.h`), with
`os` samples per chip:

```python
def chirp(s, sf, os=1):
    N = 1 << sf
    n = np.arange(N * os)
    f = np.where(n < N * os - s * os, s / N - 0.5, s / N - 1.5)
    return np.exp(2j * np.pi * (n * n / (2 * N) / os**2 + f * n / os))

down = np.conj(chirp(0, sf, os))
```

Demodulating is one line: multiply a symbol-long window by `conj(chirp(0))`
and take the FFT's peak bin. That bin is the symbol value. At one sample per
chip the chirp's edges alias, so generate at an integer oversampling ratio
(4 or more) and let the radio filter it.

| | SF | Bandwidths |
|---|---|---|
| SX1276/79 | 6-12 (SF6 implicit header only) | 7.8 kHz - 500 kHz |
| SX1261/62 | 5-12 | 7.8 kHz - 500 kHz |

## The frame

```text
preamble     N_pr upchirps, symbol 0           (8 by default)
sync word    two upchirps                      see below
SFD          2.25 downchirps                   2 whole, then the first quarter of one
header       8 symbols at reduced rate         explicit mode only; carries the first SF-2 nibbles
payload      the rest
```

The sync byte splits into two symbols, each nibble times 8:

```text
s0 = (byte >> 4) << 3
s1 = (byte & 0xF) << 3
```

| Use | SX127x byte | Symbols | SX126x registers 0x0740/0x0741 |
|---|---|---|---|
| private (the chips' default) | `0x12` | 8, 16 | `0x1424` |
| LoRaWAN | `0x34` | 24, 32 | `0x3444` |

**Measured**: the captures carry 24 and 32. LoRaWAN downlinks invert IQ
(conjugate the baseband); uplinks do not.

## Sending: the bit chain, in order

1. **Whiten** the payload bytes: XOR byte i with `W[i]`, then split it, low
   nibble first. `W` is an 8-bit LFSR, x^8+x^6+x^5+x^4+1, seeded 0xFF, shifted
   toward the MSB. It reproduces gr-lora_sdr's 255-byte table (`lib/tables.h`)
   exactly. The header and the CRC are not whitened.

   ```python
   reg, W = 0xFF, []
   for _ in range(255):
       W.append(reg)
       reg = ((reg << 1) & 0xFE) | (((reg >> 7) ^ (reg >> 5) ^ (reg >> 4) ^ (reg >> 3)) & 1)
   ```

2. **Header** (explicit mode): five nibbles go in front, high nibble first.
   CR is 1-4 for 4/5-4/8, and C is 1 when a payload CRC follows.

   ```text
   n0 = len >> 4     n1 = len & 0xF     n2 = (CR << 1) | C
   n3 = c4           n4 = c3 c2 c1 c0          (bits of len L7..L0, n2 = CR2 CR1 CR0 C)
   c4 = L7^L6^L5^L4
   c3 = L7^L3^L2^L1^C
   c2 = L6^L3^L0^CR2^CR0
   c1 = L5^L2^L0^CR1^CR0^C
   c0 = L4^L1^CR2^CR1^CR0^C
   ```

   **Measured**: these checksums match the captures' headers.

3. **CRC** (if C = 1): CRC-16, polynomial 0x1021, init 0, MSB first, no final
   XOR, over the **unwhitened** payload *except its last two bytes*. Those two
   bytes are then XORed on: `crc ^= p[-1] ^ (p[-2] << 8)`. It is appended as
   four nibbles, low nibble first, not whitened. **Measured** on the three
   23-byte frames that came through clean (payloads 00 01 02 .., 88 88 ..
   and 12 12 ..). The all-zero payloads prove nothing here, because any CRC
   that starts at 0 gives 0 for them. A one-byte payload of 00 carried
   0x0000, which fits "CRC of nothing, then XOR the byte" but is untested on
   any other byte.

4. **Hamming**: each nibble becomes a codeword, data bits first and
   *least-significant first*. With nibble bits n3..n0:

   ```text
   p0 = n0^n1^n2   p1 = n1^n2^n3   p2 = n0^n1^n3   p3 = n0^n2^n3
   4/8: n0 n1 n2 n3 p0 p1 p2 p3      4/7: drop p3      4/6: drop p2, p3
   4/5: n0 n1 n2 n3 (n0^n1^n2^n3)
   ```

   The first SF-2 nibbles of every frame use 4/8 whatever CR is set to. In
   explicit mode at SF7 those are exactly the five header nibbles.

5. **Interleave**, a block at a time. The first block takes `sf_app = SF-2`
   codewords and makes 8 symbols. Each later block takes `sf_app = SF`
   codewords (SF-2 with LDRO) and makes 4+CR symbols. A short last block is
   padded. Bit j (MSB first) of the SF-bit word for symbol i is:

   ```text
   word[i][j] = codeword[(i - j - 1) mod sf_app][i]       j < sf_app
   word[i][sf_app] = parity of word[i][0..sf_app-1]        first block, or LDRO
   ```

   So a reduced-rate symbol carries its SF-2 bits in the top of the word.

6. **Gray, then plus one**: undo Gray (binary from Gray), then add 1.

   ```python
   y = w
   for j in range(1, sf):
       y ^= w >> j
   symbol = (y + 1) % (1 << sf)
   ```

7. **Modulate** preamble, sync, SFD, symbols with `chirp()`.

**Low data rate optimisation** is required when Tsym exceeds 16 ms, so SF11
and SF12 at 125 kHz and SF12 at 250 kHz. Both ends must agree. It makes every
block reduced rate.

**Implicit header** drops the five header nibbles. Length, CR and CRC are then
set at both ends, and the first SF-2 payload nibbles still go at 4/8 in an
8-symbol block. SX127x SF6 has no other mode.

### Symbols and time on air

From the SX1276 datasheet. PL is the payload in bytes, IH is 1 for implicit,
DE is 1 with LDRO, and CRC is 1 or 0:

```text
Npayload = 8 + max(ceil((8*PL - 4*SF + 28 + 16*CRC - 20*IH) / (4*(SF - 2*DE))) * (CR + 4), 0)
Tpacket  = (N_pr + 4.25 + Npayload) * Tsym
```

10 bytes, CRC on, 4/5, preamble 8, 125 kHz: 41.216 ms at SF7 and 991.232 ms
at SF12 with LDRO.

## Receiving

What decoded the captures, one sample per chip (decimate to BW first):

1. **Find the preamble.** Dechirp windows of N samples by `conj(up)`, FFT,
   peak. Five or more windows in a row with the same bin (±1) are a preamble.
   Call that bin `u`.
2. **Find the SFD.** Dechirp the next few windows by `up` (not its conjugate)
   and take the strongest peak. That is `d`.
3. **Split timing from frequency.** A window that starts τ samples into a
   chirp, with a carrier offset of c bins, reads `u = τ + c` on an upchirp and
   `d = c - τ` on a downchirp:

   ```text
   c = (u + d) / 2          τ = (u - d) / 2          (mod N)
   ```

   (c, τ) and (c + N/2, τ + N/2) both fit; keep the one with the smaller |c|.
   Zero-pad the FFTs to get fractional bins, and derotate by
   `exp(-2j*pi*c*n/N)`. The chirps start τ samples *before* the window.
   Getting that sign wrong is how the first attempt here read the sync word
   as 94, 94.
4. **Re-read the symbols on the corrected grid.** Skip the preamble's zeros,
   read the two sync symbols, and start the payload 4.25 symbols after the
   first sync symbol.
5. **Undo the chain.** For each bin: `v = (bin - 1) mod N`, `//4` on
   reduced-rate symbols, then Gray `v ^ (v >> 1)`. Deinterleave with the same
   index formula, take each codeword's first four bits as the nibble (LSB
   first), read the header from the first block, dewhiten, and check the CRC.
   gr-lora_sdr's hard decoder does the same (`lib/fft_demod_impl.cc:313`).

On a long frame the clocks drift. Preamble-only timing is fine at SF7 and
not at SF12, where gr-lora_sdr tracks the sampling offset through the frame
(`frame_sync`).

## What the captures showed

Each capture holds three or four frames. Re-encoding means sending the decoded
payload back through the encoder, with the padding below, and comparing
symbols:

| Capture | Clean frames: header checksum, CRC, re-encoded | Frames noise hit |
|---|---|---|
| `counting`, 23 bytes, 4/8, CRC | 72/72 symbols, CRC good | one CRC failure (53/72 and 32/72 in two runs) |
| 2-4 bytes of 00, 4/8, CRC on or off | all symbols | a few: one `0000` read `1004`, one 21/24 |
| 1 byte, CRC, at 4/5, 4/6, 4/7, 4/8 | 13/13, 14/14, 15/15, 16/16 | one each at 4/5 and 4/7 read `30`; one at 4/6 failed its header checksum |
| 1 byte, CRC off, 00 and FF | all symbols | one `00` read `01` |
| `00_times_255` | the simple receiver here found no frame | - |

A frame with CRC off, or a one-byte frame, cannot check itself, so a wrong
byte there is caught only by the symbols not re-encoding.

The carrier was 9.5-16 kHz low (HackRF and transmitter together), about 10-16
bins at SF7. The search above finds it without any setting.

**The last block's padding is not zeros.** gr-lora_sdr pads a short last
block with zero codewords. The transmitter in the captures pads with valid
codewords, at the coding rate in force, whose nibbles depend only on their
position in the payload-plus-CRC nibble stream - not on the content, the CRC
or the rate:

| Positions | Nibbles |
|---|---|
| 2-6 | 3, 2, 0, 13, 5 |
| 8-13 | 14, 6, 14, 14, 6, 2 |
| 50-55 | 14, 10, 3, 8, 13, 6 |

These were read off the multi-byte frames. The one-byte frames at 4/5-4/7
were not used to find them, yet they re-encode exactly with nibble 5 at
position 6. The rule behind the sequence is not known, and it is not the
byte whitening in either nibble order. With zero padding the *whole* last
block differs, because the interleaver spreads each codeword across every
symbol of its block. A receiver never reads the padding, so decoding loses
nothing. Whether a chip *receiving* cares is not known.

## On the bench

- **Cable only.** 902-928 MHz is Part 15.247: hopping across at least 50
  channels and at most 0.4 s on one channel in any 20 s for a 125 kHz signal.
  A generator on a fixed channel is not that, so it never goes to an antenna.
  [ism](ism.md#the-bench-a-cable-and-a-pad-not-an-antenna) has the pads.
- **VSG60**: game round 43 played LoRa-style chirps from a 2 MS/s file at
  915 MHz without trouble, which is 16 samples per chip at 125 kHz. Its LO is
  off the set centre ([radios](radios.md#vsg60-notes)), so put the signal
  away from the leak in the file, as the games did.
- **BB60D**: record at 1.25 MS/s or more (decimating 40 MS/s by 32) for
  125 kHz, then decimate to BW in software.
- **US915 LoRaWAN channels**: uplink 902.3 + 0.2·i MHz (125 kHz, SF7-10) and
  903.0 + 1.6·i MHz (500 kHz, SF8); downlink 923.3 + 0.6·i MHz (500 kHz).
  Sync 0x34, preamble 8, explicit header, CRC on uplinks only, downlinks IQ
  inverted (RP002).
- **The independent check still missing** is a real chip on the cable: an
  SX127x or SX126x module, a Meshtastic node or a gateway, set to the same SF,
  BW, CR, sync word and IQ sense, receiving what the VSG sends. The captures
  test the receiver, and the encoder at SF7 and 125 kHz. Only a chip can say
  whether zero padding matters, and nothing has checked the other spreading
  factors, LDRO or implicit header.

## Tools

| Tool | What it does | State |
|---|---|---|
| [gr-lora_sdr](https://github.com/tapparelj/gr-lora_sdr) | TX and RX in GNU Radio 3.10, SF5-12, header, CRC, LDRO | the reference these notes follow; not on conda-forge under `gnuradio-lora_sdr` (searched 2026-10-31) |
| [rpp0/gr-lora](https://github.com/rpp0/gr-lora) | RX, multi-channel | 2018; its captures are the ones used here |
| [BastilleResearch/gr-lora](https://github.com/BastilleResearch/gr-lora) | RX, Knight's 2016 work | idle |
| [LoRaPHY](https://github.com/jkadbear/LoRaPHY) | encode and decode in MATLAB | research code |
| [SDRangel](https://github.com/f4exb/sdrangel) ChirpChat | RX demodulator | active |
| [myriadrf/LoRa-SDR](https://github.com/myriadrf/LoRa-SDR) | early encoder experiments, a different whitener | abandoned |

rtl_433 does not decode LoRa.

## Traps

- **The patent is not the chip.** It describes the sync pair as {x, N-x} and
  the order FEC, interleave, whiten, Gray. Chips send nibble×8, and whiten
  bytes before the Hamming code. The 2020 EPFL paper's whitening matrix,
  which depends on CR, is superseded by its own later code.
- **AN1200.18's whiteners** are for the chips' FSK engine, not LoRa.
- **The ±1.** Plus one after Gray on sending, minus one before Gray on
  receiving. Miss either and every symbol is off by one bin.
- **Two nibble orders.** The header is high nibble first, the payload low
  nibble first.
- **The quarter downchirp.** The payload starts 2.25 symbols after the SFD
  begins, so a receiver that counts whole symbols there is a quarter off.
