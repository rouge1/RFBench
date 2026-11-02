# Push-to-talk beyond the land-mobile standards: ham digital voice, cellular and app PTT, satellite PTT

Taken from the FM receiver project's `knowledge/ptt.md` (a sibling project; research of
2026-11-01 by Haiku and Muse runs with the web on, then a second pass reading the
specs). We did not fetch these documents ourselves, so nothing here is checked against a
document in `docs/ptt/`. Marks: **read** (that project read it in the spec text),
**second-hand** (a wiki, a forum or one run), *unverified* (one source or a snippet).
The land-mobile systems are in [ptt.md](ptt.md).

## Ham digital voice

| Mode | Channel | Modulation | Rate | Sync and frame |
|---|---|---|---|---|
| D-STAR DV | 6 kHz or less (JARL STD v6.0a, 3.1.2) | GMSK, BT 0.5 (QPSK and 4FSK allowed) | 4.8 kbit/s max; header 1200 bit/s MSK (1200 Hz = 1, 1800 Hz = 0) | **read**: 64-bit `1010` bit sync; 15-bit frame sync `111011001010000`; 24-bit resync `101010101011010001101000`; end sync `000100110101111` + 0 (32 bits); scrambler x^7+x^4+1 seeded with ones. GMSK deviation not stated in the JARL text |
| Yaesu System Fusion | 12.5 kHz | C4FM | 4800 sym/s, 9600 bit/s | 40-bit sync, hex *unverified*. Four voice modes: DN, VW, FR and data (from memory) |
| M17 | 9 kHz occupied, 12.5 kHz spacing | RRC alpha 0.5 4FSK, h = 1/3, symbols +-0.8 / +-2.4 kHz | 4800 sym/s, 9600 bit/s | **read** (spec v3.0.0-draft, 2025-11-16): 16-bit syncs LSF 0x55F7, BERT 0xDF55, stream 0xFF5D, packet 0x75FF; 384-bit frame = 40 ms; CRC 0x5935; 40 ms preamble of 192 symbols alternating +-3; LSF = 30 bytes (224 + CRC-16); rate-1/2 K=5 convolutional code punctured to 368 bits; stream LICH = 40-bit LSF chunks, modulo-6 counter |

Documents: JARL STD6_0_E (jarl.org/English/7_dstar/STD6_0_E.pdf), the 2005 ARRL-hosted
D-STAR technical requirements, and the M17 spec (m17project.org). Fusion has no free
specification.

## MPT 1327 (analog-voice trunking)

1200 bit/s FFSK; control-channel codeword sync 0xC4D7, traffic-channel sync 0x3B28
(**read** in the text); preamble of 16 or more bits of `1010`. Crown Copyright,
downloadable from sigidwiki.com; link, do not copy.

## Cellular and app PTT

Cellular PTT is ordinary LTE or NR IP traffic. At the air interface it cannot be told
from video, web or VoLTE: floor control, talkgroups and emergency flags sit above PDCP
and the payload is encrypted.

- **3GPP MCPTT:** stage 1 TS 22.179; architecture TS 23.379 (replaced TS 23.179 from
  Rel-14); call control TS 24.379; floor control TS 24.380; media TS 26.179 (AMR-WB
  mandatory, EVS optional). Voice bearer 5QI 65.
- **FirstNet PTT (AT&T):** described as built on MCPTT; launch date reports conflict
  (2019 and 2020). Runs on AT&T's LTE including Band 14 (758-768 MHz down, 788-798 up),
  but Band 14 carries all FirstNet traffic, not only PTT.
- **Carrier PoC:** AT&T Enhanced PTT and Verizon PTT+ are described as Kodiak-based PoC
  (one run; another called Verizon's a carrier product). No interoperation without gateways.
- **Zello:** an internet app (proprietary protocol, Opus audio); nothing at RF beyond
  generic LTE/NR or Wi-Fi.
- **Iridium PTT:** commercial since 2015, 2.4 kbit/s per the vendor datasheet; phone link
  L-band 1616-1626.5 MHz TDMA/FDMA, DQPSK at 25 ksym/s, 31.5 kHz carriers, 90 ms frames
  (*unverified*).

### The one hook in an LTE or NR cell's broadcast

- LTE SIB13 carries MBMS (eMBMS) control; SIB15 MBMS service-area information; SIB2
  reserves MBSFN subframes.
- LTE SC-PTM (Rel-13): SIB20 gives the SC-MCCH configuration; group data goes to a G-RNTI
  on the PDSCH. SC-RNTI is FFFC (patent text, second-hand).
- NR MBS (Rel-17): SIB20 and SIB21 named by one run; *unverified*.
- No SIB carries an "MCPTT supported" flag (a negative found in no source); MCPTT is
  announced at the IMS/application layer.

Presence of SIB13 or SIB20 shows broadcast capability, never that PTT is in use.

## Telling the systems apart in IQ (inference, not sourced)

1. **Occupied bandwidth:** about 6 kHz = D-STAR, NXDN 6.25 kHz, dPMR; 9-10 kHz = DMR,
   P25, Fusion, M17; about 22-25 kHz = TETRA.
2. **Burst timing:** DMR and P25 Phase 2 chop every 30 ms; TETRA frames are 56.67 ms;
   P25 Phase 1, NXDN, dPMR, D-STAR, Fusion and M17 are continuous while keyed.
3. **Discriminator levels:** 4FSK and C4FM give four levels; GMSK two smooth levels;
   DQPSK and H-DQPSK no staircase.
4. **Sync word** correlated on the symbol stream names the protocol.
5. Analog PTT: a single 11-16 kHz stripe that switches hard on and off; a repeater shows
   a pair 600 kHz or 5 MHz apart with a 1-2 s hang time.

## What Sceptre labels (read from the new-voice-journey code, no captures opened)

- File names: `{proto}_voice_{freq}hz_slot{n}_{timestamp}.wav` for DMR (with a TDMA slot),
  `{proto}_voice_{freq}hz_{timestamp}.wav` for the rest.
- Session database tables `intercepts`, `intercept_files`, `intercept_attachments`,
  `intercept_rssis`: centre frequency, bandwidth, start time (ns), average RSSI, and a
  JSON attachment with `standard` and `technology` strings.
- Protocol values the reader maps: `dmr`, `nxdn`, `ctcss`, `dcs`; any other string passes
  through, so Sceptre may label more (P25, dPMR) than is special-cased there.
- Attachment fields: `encryption` (bool), `source_address`, `destination_address`,
  `call_type` (group, or individual / unit_to_unit / private), `channel_type`,
  `color_code` (DMR), and for analog `tone_code` and `tone_freq`.
- Calls are grouped by source and destination address (DMR, NXDN; talkgroup = destination)
  or by frequency and tone (CTCSS, DCS). There is no trunking-system view (no WACN,
  system or site).

## Encryption and law (US, not legal advice; second-hand)

DMR offers basic privacy (XOR), RC4-40 and AES; P25 ADP (RC4-40), DES-OFB and AES-256;
analog cannot be encrypted. 18 U.S.C. 2511(2)(g)(ii) excepts radio communications readily
accessible to the public, an exception tied to "not scrambled or encrypted"; prohibited
interception carries up to five years (2511(4)(a)). 47 U.S.C. 605 is aimed at divulging
what was intercepted. State scanner laws vary (not checked). The practical line: classify,
never attempt to decrypt.
