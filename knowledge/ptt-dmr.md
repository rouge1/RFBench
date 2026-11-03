# DMR (ETSI TS 102 361) two-slot TDMA push-to-talk - SDR bench note

Scope: Tier II conventional and Tier III trunked voice PTT as defined in the
four ETSI parts. Primary sources are the pdftotext layouts in
`docs/ptt/dmr/text/`; secondary web text is used only where marked.
Citations are `[file, clause or table, ETSI page]`.

Files: `ETSI_TS_102_361-1_...txt` = Part 1 air interface (primary for RF,
bursts, SYNC, FEC); `...-2_...txt` = Part 2 voice services (primary for call
flow); `...-3_...txt` = Part 3 data (headers/blocks); `...-4_...txt` = Part 4
trunking/security (primary for Tier III).

## 1. What the system is and who uses it

- DMR is a digital mobile radio standard from ETSI for licensed land-mobile
  bands, built around 2-slot TDMA in a 12.5 kHz RF carrier, giving 12.5 kHz
  equivalent (12.5e) spectral efficiency per slot pair
  [Part1, 3.1/4.2.1, p.11-21].
- Three tiers: Tier I = licence-free integral-antenna direct mode; Tier II =
  licensed conventional (direct or via a Base Station repeater); Tier III =
  licensed trunking with a controller that regulates communications
  [Part1, clause 1, p.11].
- Typical users named in the texts: licensed PMR users needing spectral
  efficiency, voice plus integrated IP/data services, high-power operation;
  Tier III adds TETRA-like dispatch/voice and packet data (IPv4/IPv6)
  [Part1, clause 1, p.11] [Part4, clause 1/4.9, p.27-28]
  [wikipedia_digital_mobile_radio.txt, DMR Tiers].
- Amateur use (BrandMeister/MMDM hotspots, RadioID numbers) appears only in
  the secondary Wikipedia text, not in the ETSI parts
  [wikipedia_digital_mobile_radio.txt, Amateur radio use].

## 2. RF: channel, modulation, rate, filter, bands, duplex

| Item | Value | Citation |
|---|---|---|
| RF carrier bandwidth | 12.5 kHz | [Part1, 10.1.2, p.112] |
| Access | 2-slot TDMA, 30 ms slots, 60 ms frame | [Part1, 10.2.3, p.115] |
| Modulation | 4FSK, constant envelope | [Part1, 10.2.2.0, p.113] |
| Symbol rate | 4800 symbols/s, 2 bits/symbol = 9600 bit/s gross | [Part1, 10.2.1, p.113] |
| Symbol time | 1/4800 s = 208.33 us | [Part1, 10.2.2.2, p.113] |
| Deviation index h | 0.27 | [Part1, 10.2.2.1, p.113] |
| Symbol deviation | +/-1.944 kHz (+/-3), +/-0.648 kHz (+/-1) | [Part1, Table 10.3, p.113] |
| Dibit mapping | 01 -> +3; 00 -> +1; 10 -> -1; 11 -> -3 | [Part1, Table 10.3, p.113] |
| Test tone deviation | +3 +3 -3 -3 ... gives 1200 Hz FM at 2749 Hz peak +/-10% (2474-3024 Hz) | [Part1, 10.2.2.3, p.113-114] |
| Tx splatter filter | Square-root raised cosine, flat to 1920 Hz, zero above 2880 Hz | [Part1, 10.2.2.2, p.113] |
| Frequency range | 30 MHz to 1 GHz; Tier II/III stated 66-960 MHz in secondary text | [Part1, 10.1.1, p.112] [wikipedia, Specs/DMR Tiers] |
| Duplex | Single frequency (simplex/direct) or duplex-spaced pair; inbound = MS->BS, outbound = BS->MS | [Part1, 3.1 definitions, p.11-17] |
| Normal-burst rate | 264 bits / 30 ms slot = 4.4 kbit/s per slot content | [Part1, 10.2.3.1, p.115] |
| BS freq error | +/-2 ppm (50-300 MHz), +/-1 ppm (300-600), +/-0.75 ppm (600-800), +/-0.3 ppm (800 MHz-1 GHz) | [Part1, Table 10.1, p.112] |
| MS freq error | +/-2 ppm (50-600 MHz), +/-1.5 ppm (600 MHz-1 GHz) | [Part1, Table 10.2, p.112] |
| MS clock drift | +/-2 ppm (repeater); -1.0 to +1.0 ppm (TDMA direct mode) | [Part1, 10.1.4, p.112] |

Note: the ETSI page gives no occupied-bandwidth number beyond the 12.5 kHz
carrier and the filter mask; treat it as a 12.5 kHz channel.

## 3. Frame, burst, and slot structure

- TDMA frame = 60 ms = slot 1 + slot 2 of 30 ms each. One call normally uses
  one slot; the second slot carries an independent call in 2:1 mode
  [Part1, 10.2.3/Fig 10.2, p.115].
- Generic burst = 108 bits + 48-bit centre field (SYNC or signalling) + 108
  bits = 264 bits in 27.5 ms at 4800 sym/s (132 symbols, 66 per side of slot
  centre) [Part1, 4.2.1/Fig 4.3, p.21] [Part1, 10.2.3.1.2, p.116].
- Inbound leaves 2.5 ms guard (PA ramp + propagation); outbound uses the
  2.5 ms for CACH [Part1, 4.2.1/Fig 4.4-4.5, p.22].
- Voice superframe = 6 bursts A-F = 360 ms. Burst A carries voice SYNC;
  B-E carry embedded LC; F carries RC or Null on the forward path
  [Part1, 5.1.2.1/Fig 5.3, p.29].
- Three burst sizes: traffic 264 bits, CACH 24 bits, RC 96 bits
  [Part1, 3.1 burst definition, p.13].

| Burst | Length on air | Content | Citation |
|---|---|---|---|
| Normal (voice/data/control) | 264 bits / 27.5 ms in 30 ms slot | 2x108 payload + 48 centre | [Part1, 4.2.1/6.1/6.2, p.21/59-61] |
| CACH (outbound only) | 24 bits / 2.5 ms gap | TACT 7 + payload 17 | [Part1, 6.3, p.61-62] |
| Standalone inbound RC | 96 bits / 10 ms centred in slot | 32-bit RC field + 48 SYNC + spare | [Part1, 6.4.1/10.2.3.2, p.62-63/118-119] |
| Outbound embedded RC | 48-bit EMB/LC field reuse | 11-bit RC info + FEC in voice/data burst | [Part1, 6.4.2, p.63] |

### 3.1 SYNC patterns (48-bit centre word, hex)

Voice and data patterns of each pair are symbol-wise complements; a single
correlator gives positive for voice, equal negative for data
[Part1, 9.1.1/Table 9.2 note, p.91].

| Use | Hex (12 digits = 48 bits) | Citation |
|---|---|---|
| BS sourced voice | 755FD7DF75F7 | [Part1, Table 9.2, p.91] |
| BS sourced data | DFF57D75DF5D | [Part1, Table 9.2, p.91] |
| MS sourced voice | 7F7D5DD57DFD | [Part1, Table 9.2, p.91] |
| MS sourced data | D5D7F77FD757 | [Part1, Table 9.2, p.91] |
| MS standalone RC | 77D55F7DFD77 | [Part1, Table 9.2, p.91] |
| Direct slot 1 voice | 5D577F7757FF | [Part1, Table 9.2, p.91] |
| Direct slot 1 data | F7FDD5DDFD55 | [Part1, Table 9.2, p.91] |
| Direct slot 2 voice | 7DFFD5F55D5F | [Part1, Table 9.2, p.91] |
| Direct slot 2 data | D7557F5FF7F5 | [Part1, Table 9.2, p.91] |
| Reserved | DD7FF5D757DD | [Part1, Table 9.2, p.91] |

Binary forms are printed beside each hex row in Table 9.2. First burst of
every inbound (two-frequency BS or single-frequency) transmission must carry
SYNC; voice repeats SYNC every superframe (360 ms); data/control can present
SYNC as often as every 60 ms inbound or 30 ms outbound
[Part1, 4.3, p.23-24].

### 3.2 Centre-field PDUs (bit tables)

| PDU | Length | Fields (bits) | Code | Citation |
|---|---|---|---|---|
| EMB | 16 | CC 4, PI 1, LCSS 2, parity 9 | Quadratic Residue (16,7,6) | [Part1, Table 9.3, p.92] |
| SLOT | 20 | CC 4, Data Type 4, parity 12 | Golay (20,8) | [Part1, Table 9.4, p.92] |
| TACT (CACH) | 7 | AT 1, TC 1, LCSS 2, parity 3 | Hamming (7,4) | [Part1, Table 9.5, p.92] |
| RC info | 32 | RC info 4, CRC-7 7, parity 21 | RC single-burst BPTC | [Part1, Table 9.6, p.92] |
| Full LC | 72 + 24 CRC | PF 1, R 1, FLCO 6, FID 8, payload 56 | BPTC(196,96) + RS(12,9)/CRC-CCITT | [Part1, 7.1/Fig 7.1-7.4, p.64-66] |
| Short LC (CACH) | 28 + 8 CRC | SLCO + data | variable BPTC + 8-bit CRC | [Part1, 7.1, p.64] [Part1, B.2.3, p.130-132] |

- Data Type values: 0000 PI Hdr, 0001 Voice LC Hdr, 0010 Terminator, 0011
  CSBK, 0100 MBC Hdr, 0101 MBC Cont, 0110 Data Hdr, 0111 Rate-1/2 data,
  1000 Rate-3/4 data, 1001 Idle, 1010 Rate-1 data, 1011 Unified Single Block
  [Part1, Table 9.22, p.103].
- AT: 0 = inbound idle, 1 = busy. TC: slot number of next bursts. LCSS: 00
  single/first CSBK, 01 first LC, 10 last, 11 continuation
  [Part1, Tables 9.20/9.23/9.24, p.102-103].
- CACH throughput: 17 bits / 30 ms = 566.67 bit/s; AT/TC framing plus
  fragmented LC/CSBK; fragment count fixed during a BS transmission
  [Part1, 6.3, p.61-62].
- Colour Code: 4 bits, CC0-CC15, all-site code in TDMA direct mode. It marks
  the site/repeater, is NOT addressing [Part1, 9.3.1, p.102]
  [Part1, 4.2.1, p.21].

## 4. PTT behaviour: setup, floor control, addressing, teardown

### 4.1 Starting a call (PTT press)

- Conventional: PTT -> channel access (impolite / polite-to-own-CC / polite-
  to-all) -> Voice LC Header burst -> voice superframes A,B,C...
  [Part1, Fig 5.4, p.29] [Part2, 5.2.1.1, p.29].
- Group call (BOC): first burst carries Grp_V_Ch_Usr LC in a Voice LC Header
  (Data Type 0001, FLCO 000000). Late entry uses the same PDU embedded over
  4 bursts of each superframe [Part2, 5.2.1.1-5.2.1.2/Tables 5.3-5.4, p.29].
- Individual call has two setups: PATCS (press-and-talk, header-first like
  group) and OACSU (off-air setup: UU_V_Req CSBK presence check, UU_Ans_Rsp
  accept/reject, then PATCS with impolite access)
  [Part2, 5.2.2.1, p.43].
- Trunked (Tier III): no header required; MSs use the TSCC (control channel)
  with random access (ALOHA), AHOY check of the called party, then channel
  GRANT to a payload channel [Part4, 4.9-4.9.1, p.28-29].
- TDMA direct mode elects slot timing with beacons/CT CSBKs rather than a BS
  [Part2, 6.2.3, p.65].

### 4.2 Floor control (who may talk)

- Three politeness levels: impolite (tx regardless), polite-to-own-CC
  (defer only to own CC), polite-to-all (defer to any busy/other energy).
  A party to a call in progress may tx regardless of busy for that call
  [Part1, 5.2.1.6, p.41].
- Outbound CACH AT bit is the floor flag: 1 = next inbound slot busy.
  BS holds AT=busy during traffic, expected responses, and call hangtime
  [Part1, 6.3, p.61-62] [Part1, 5.2.1.4, p.40].
- Reverse Channel gives a low-rate back-channel to the transmitter
  (pre-emption, power control, ack) without taking the slot
  [Part1, 5.1.5/6.4, p.33/62-63].
- Tier III random access is gated by AT: no random access when AT=1, and a
  response to slot C is sent in slot D+1 (slot-shifted)
  [Part4, 4.9/Fig 4.2, p.27-28].

### 4.3 Addressing: talkgroup, colour code, IDs

| Element | Size | Remarks | Citation |
|---|---|---|---|
| Source ID | 24 bits | Individual address of sender | [Part1, Annex A/Table A.1, p.120] |
| Destination ID | 24 bits | Talkgroup or unit address | [Part1, Annex A/Table A.1, p.120] |
| Talkgroup range | 000001-FFFCDF hex | >16M group addresses | [Part1, Table A.1, p.120] |
| Unit/individual range | 000001-FFFCDF hex | >16M unit addresses | [Part1, Table A.1, p.120] |
| All-talkgroup | FFFFF0-FFFFFF hex (partitioned) or FFFFFF (single) | 16 partitions | [Part1, Table A.1, p.120] |
| All-unit | FFFFF0-FFFFFF hex | 16 partitions | [Part1, Table A.1, p.120] |
| Gateway IDs | FFFEE0-FFFEEF (PABX/PSTN/IP) | System devices | [Part1, Table A.1, p.120] |
| Colour Code | 4 bits, 0-15 | Site filter, not an address | [Part1, 9.3.1, p.102] |
| Grp_V_Ch_Usr | FLCO 000000, FID 00000000, Service Options 8, group 24, source 24 | Header + embedded late entry | [Part2, Table 7.1, p.72-73] |
| UU_V_Ch_Usr | FLCO 000011, FID 00000000, Service Options 8, target 24, source 24 | Individual addressing | [Part2, Table 7.2, p.73] |
| Service Options | Emergency 1, Privacy 1, Reserved 2, Broadcast 1, OVCM 1, Priority 2 | Privacy bit undefined (see sec.7) | [Part2, Table 7.11, p.80] |

There is no P25-style NAC: the equivalents are Colour Code (RF site filter)
plus 24-bit DMR IDs carried in LC/CSBK [Part1, Annex A, p.120].

### 4.4 Voice teardown and hangtime

- End of speech item: finish superframe through burst F, then send a data
  burst with data SYNC (any general data burst works as terminator); for
  inbound/direct channels use Terminator with LC carrying the call LC
  [Part1, 5.1.2.3/Fig 5.8, p.30-31].
- Group EOT: Grp_V_Ch_Usr in Terminator with LC (Data Type 0010)
  [Part2, 5.2.1.1, p.29]. Individual EOT: UU_V_Ch_Usr in Terminator with LC
  [Part2, 5.2.2.1, p.43].
- Call hangtime (T_CallHt, may be zero): BS keeps channel busy, repeats
  Terminator LC with the call IDs, AT=busy. Expiry = End of Call
  [Part1, 5.2.1.4, p.40] [Part2, 5.1.1.3, p.23-24].
- Channel hangtime (T_ChHt, optional): BS sends Idle messages, AT=idle
  [Part1, 5.2.1.4, p.40].
- Tier III uses message trunking (channel held for whole call incl. hangtime)
  or transmission trunking (channel per over)
  [Part4, 4.11, p.31-32].

## 5. FEC, interleaving, scrambling, CRC

No energy-dispersal scrambler is specified in the ETSI text; protection is
block/Hamming/Golay/QR/RS/Trellis codes plus interleavers and Data-Type CRC
masks [Part1, Annex B/Table B.1, p.121].

| Field | FEC | Check | Citation |
|---|---|---|---|
| EMB | QR (16,7,6) | none | [Part1, Table B.1, p.121] |
| Slot Type | Golay (20,8) | none | [Part1, Table B.1, p.121] |
| CACH TACT | Hamming (7,4) | none | [Part1, Table B.1, p.121] |
| Embedded LC (voice) | variable BPTC | 5-bit checksum CS | [Part1, Table B.1, p.121] |
| Voice/Data header, CSBK, Data Hdr, Rate-1/2 data | BPTC(196,96) | CRC-CCITT / CRC-9 / 32-bit per row | [Part1, Table B.1, p.121] |
| Rate-3/4 data | Rate-3/4 trellis (196,144), 8-state FSM | CRC-9 per block + 32-bit message CRC on last | [Part1, Table B.1/B.2.4, p.121/133-136] |
| Rate-1 data | uncoded + 4 zero pad bits | CRC-9 per block + 32-bit message CRC on last | [Part1, Table B.1/B.2.5, p.121/137-139] |
| RC signalling | RC single-burst BPTC | 7-bit CRC | [Part1, Table B.1, p.121] |
| Short LC (CACH) | variable BPTC | 8-bit CRC | [Part1, Table B.1, p.121] |
| Voice/Data LC checksum | Reed-Solomon (12,9) over GF(2^8) | 3 parity bytes | [Part1, B.3.6, p.141-142] |

- BPTC(196,96): 96 info bits -> 196 coded bits, interleave index
  `i*181 mod 196`, 196-bit payload split 98/98 around the centre
  [Part1, B.1.1/B.2, p.122-125].
- Embedded BPTC: 72-bit LC + 5-bit CS over 4 bursts (32 bits each); CACH
  Short LC spreads 28 info + 8 CRC over 4 CACH bursts (17 bits each)
  [Part1, B.2.1/B.2.3, p.126-132].
- Trellis: 48 tribits in -> 98 dibits out, init state 0, flush tribit 000,
  constellation table B.7/B.8, dibit interleave Table B.9/B.10
  [Part1, B.2.4, p.133-136].
- CRC polynomials: 8-bit G8(x)=x^8+x^2+x+1 init 00000000
  [Part1, B.3.7, p.143]; 16-bit CRC-CCITT GH(x)=x^16+x^12+x^5+1 with
  inversion IH(x), init 0000 hex [Part1, B.3.8, p.144]; 32-bit
  GM(x)=x^32+x^26+x^23+x^22+x^16+x^12+x^11+x^10+x^8+x^7+x^5+x^4+x^2+x+1
  init 00000000 hex [Part1, B.3.9, p.144-146]; 9-bit CRC per confirmed block
  (80/128/176-bit sequences for rate 1/2, 3/4, 1) [Part1, B.3.10, p.146];
  7-bit CRC for RC [Part1, B.3.13, p.148]; 5-bit CS for embedded LC
  [Part1, B.3.11, p.147].
- Data-Type CRC mask is XORed/applied to the CRC before BPTC encoding; a
  different mask per Data Type value [Part1, B.3.12, p.147].
- Hamming/Golay/QR generators: Golay g(x)=x^11+x^10+x^6+x^5+x^4+x^2+1;
  QR G(x)=x^8+x^5+x^4+x^3+1; Hamming(17,12) g(x)=x^5+x^2+1; (15,11)
  G(x)=x^4+x+1; (7,4) G(x)=x^3+x+1 [Part1, B.3.1-B.3.5, p.139-141].

## 6. Voice codec

- The ETSI parts define only a "vocoder socket": 2x108 = 216 bits per voice
  burst carrying 60 ms of compressed speech; example given is three 72-bit
  frames for a 20 ms-frame vocoder [Part1, 4.2.1/6.1, p.21/59].
- The codec itself is NOT named in the Part 1/2/3/4 texts (SOURCES.md notes
  "AMBE is not named in the text") [SOURCES.md, Contents column].
- Secondary texts state the interoperable codec is the proprietary half-rate
  DVSI AMBE+2 (MOU agreement), ~2450 bit/s per voice channel after FEC/split
  vs 4400 (P25) and 64k (PSTN) [wikipedia, Specs/DMR Association]
  [cryptomuseum_dmr.txt, DMR intro]. Treat AMBE+2 as secondary-source fact.

## 7. Encryption and authentication

Primary (ETSI) facts:

- Every Service Options byte has a Privacy bit, but the note states
  "Privacy is not defined in the present document"
  [Part2, Table 7.11 note 1, p.80].
- PI Header Data Type exists but is "not defined ... reserved for future use"
  [Part1, Table 6.1 note, p.61].
- Outbound superframe reserves burst F for repeated inbound privacy info,
  "not defined in the present document" [Part1, 7.1.3.1, p.66].
- Tier III defines authentication procedures: TSCC authenticating an MS,
  MS authentication, key management, registration with authentication, and
  stun/revive/kill with or without authentication
  [Part4, 6.4/6.4.8, p.60/72-73].

Secondary (non-ETSI) facts - NOT in the primary specs:

- Three practical levels: Basic Privacy (scrambler, short numeric key),
  Enhanced Privacy (ARC4/RC4 ~40-bit), AES-128/256 on professional units;
  vendor interop is not guaranteed
  [dmrhub_encryption_basic_enhanced_aes.txt, Three levels]
  [cryptomuseum_dmr.txt, Encryption].
- DMRA-managed scheme per Wikipedia: 40-bit ARC4, 64-bit DES, 128/256-bit
  AES options; Basic often XOR of AMBE frames with static keys; ARC4/AES
  replace 4 LSBs per 49-bit AMBE frame with a coded 32-bit IV (72 bits over
  the superframe) at some voice-quality cost
  [wikipedia, Encryption].
- Known weak practice cited: fixed IV 0x12345678 on one vendor line (fixed
  in later firmware); short/non-primitive LFSR IV sequence reducing the
  effective IV space [wikipedia, Weaknesses in ARC4 DMRA].

## 8. Detecting and decoding with SDR tools

Tool names below come from the local secondary texts: DSD-FME is an open
DSD-family decoder project for DMR/P25/NXDN etc.
[dsd-fme_README.txt, Information]. DMRlink is a Motorola IPSC network-side
Python bridge (IP transport, not an RF demodulator)
[dmrlink_README.txt, PROJECT/FILES]. MMDVM hotspot hardware/firmware is
mentioned as the amateur hotspot path
[wikipedia, Amateur radio use].

What to look for (all parameters from Part 1 citations above):

- 12.5 kHz channel, 4FSK, 4800 sym/s. Samples per symbol = Fs/4800, so
  48 kHz complex gives 10 samples/symbol; 96 kHz gives 20. (Arithmetic from
  [Part1, 10.2.1, p.113]; no SDR rate is prescribed in the texts.)
- Normal burst = 132 symbols = 27.5 ms RF, centred in a 30 ms slot; decode
  by correlating the 48-bit centre SYNC (Table 9.2 hex above) to find burst
  centres and classify voice/data/BS/MS/slot [Part1, 9.1.1/10.2.3.1, p.91/115].
- Outbound (BS): continuous carrier; expect CACH energy every 30 ms between
  bursts and SYNC opportunities as often as every 30 ms. Inbound (MS):
  bursty with 2.5 ms guard gaps; first burst of a transmission always SYNC
  [Part1, 4.2.1/4.3, p.21-24].
- Voice: 360 ms A-F superframe rhythm with SYNC only in A; embedded LC
  decodable after 4 bursts (B-E) with BPTC + 5-bit CS; F burst Null/RC
  [Part1, 5.1.2.1/7.1.3, p.29/66].
- Control: every data/control burst carries a 20-bit SLOT (Golay) with CC +
  Data Type; CACH TACT gives AT (busy/idle) and TC (slot number) for floor
  state [Part1, Tables 9.4/9.5, p.92].
- Sanity checks: CC 0-15 constant per site; Data Type in 0000-1011 range;
  LC FID 00000000 for standard services; group/individual addresses in
  000001-FFFCDF hex [Part1, 9.3.1/Annex A, p.102/120].
- Voice payload is 216-bit vocoder sockets per burst; without the AMBE+2
  codec (proprietary, secondary source) an open tool can frame-sync and show
  IDs/LC but not render audio [Part1, 6.1, p.59]
  [wikipedia, Specifications].

## 9. Traps

- Voice/data SYNC pairs are complements: a naive correlator fires on both;
  use the sign to separate them [Part1, Table 9.2 note, p.91].
- Reserved SYNC DD7FF5D757DD exists for future use; do not hard-fail on it
  [Part1, Table 9.2, p.91].
- Colour Code is not an address and not a NAC; different-CC traffic on the
  same frequency is co-channel interference to be ignored, not a separate
  call to follow [Part1, 4.2.1 note, p.21].
- Any data burst (e.g. Idle) terminates speech; do not require Terminator LC
  to detect EOT [Part1, 5.1.2.3 notes, p.30-31].
- Terminator LC doubles as call-hangtime filler; AT stays busy through
  T_CallHt, then Idle + AT=idle for channel hangtime - do not log hangtime
  as a new call [Part1, 5.2.1.4, p.40].
- Tier III headers are optional/absent on traffic channels; late entry must
  come from TSCC grants, not from Voice LC headers
  [Part1, 5.1.2.2 notes, p.29-30].
- Privacy bit set does not mean AES/ARC4 specifics; the primary specs define
  neither algorithm nor key transport [Part2, Table 7.11, p.80].
- Basic/Enhanced privacy is vendor-incompatible (Hytera vs Motorola vs
  budget brands); a key number alone does not decode across vendors
  [dmrhub, Compatibility/key pitfalls] [wikipedia, Encryption].
- 40-bit ARC4 + short/constant IVs is brute-forceable; do not treat captured
  "encrypted" voice as unrecoverable by others
  [cryptomuseum_dmr.txt, Enhanced encryption] [wikipedia, Weaknesses].

## Checked against a recording (2026-11-02)

On a wide UHF capture of real DMR (Capacity Plus) traffic, demodulated by the FM
receiver project's session: the BS-sourced 48-bit SYNC of Table 9.2, as written in 3.1,
hit with 0 of 24 symbol errors on every DMR channel tried; hits were 144 symbols
(30 ms, one slot) apart; 4800 sym/s held to better than 0.1 %; the inner deviation read
641-674 Hz for the 648 Hz of h = 0.27 (the filters in that chain soften the levels, so
that is close, not exact). Sceptre's own decode of the same capture labels those
channels DMR, so the sync table and the slot timing are checked against a signal.

The trap that came with it: **each data SYNC in Table 9.2 is the symbol-for-symbol
negation of the voice SYNC beside it** (BS, MS, and both direct-mode pairs). A
correlator that finds "BS voice" in a chain of unknown polarity has equally found "BS
data" in the other polarity. Fix the chain's polarity on a known signal (an NXDN or
dPMR channel, whose syncs are not self-complementary in this way, or a burst whose
slot type decodes) before naming a hit voice or data. Repeating every slot points to
data or control; voice carries the SYNC only in burst A, one burst in six.

The slot type then settled it. The 20 bits either side of the SYNC (5 symbols each,
dibits +3 = 01, +1 = 00, -1 = 10, -3 = 11), decoded as Golay(20,8) by minimum distance
with the generator of Annex B (Table B.11), accepted at 2 bit errors or fewer, gave
colour code + data type (clause 9.3, Table 9.22). On 9 channels the dominant colour code
held in 95-100 % of bursts, and on the 5 that Sceptre also decoded it agreed with
Sceptre's colour code every time. Every dominant decode came from the BS **data** SYNC in
normal polarity (CSBK, Idle, Terminator with LC, Voice LC Header, Data Header); the
"voice, inverted" reading gave only reserved or nonsense types. So the Golay(20,8)
generator, the slot-type layout and the data-type table are checked against a signal
too, and a correct chain keeps polarity. A BS channel that is nearly all CSBK fits a
Capacity Plus control channel (a guess; CSBK opcodes were not decoded). Not checked:
CSBK contents. (BPTC(196,96), the LC and its RS(12,9) were checked later; see
[A transmitter](#a-transmitter-dmr_synthpy-2026-11-02).)

Voice bursts were then checked bit for bit against Sceptre's own `.ambe` file of the same
call (see [ptt.md](ptt.md)). Demodulated with numpy alone (mix to the channel, 1 MS/s,
48 kS/s, 6 kHz low-pass, FM discriminator, one-symbol boxcar, 10 samples a symbol), each
voice SYNC found by correlation, the four levels scaled from the 24 known SYNC symbols
by least squares, and bursts B-F taken 288, 576, ... symbols after burst A: every one of
the call's 162 AMBE+2 frames (9 superframes) came out identical to Sceptre's, in order,
with no bit errors. So the voice burst layout is checked: 108 payload bits, 48 SYNC or
EMB bits, 108 payload bits; frame 1 = payload bits 0-71, frame 2 = bits 72-143 (36 each
side of the centre field), frame 3 = bits 144-215; dibits +3 = 01, +1 = 00, -1 = 10,
-3 = 11, first bit sent first; and the same slot every 288 symbols. Sceptre's `.ambe`
holds the frames in that on-air form, before any deinterleaving or FEC.

The demodulator is `scripts/dmr_demod.py` (tested on synthetic bursts by
`scripts/test_dmr_demod.py`): `voice_calls`, `data_bursts`, the Golay(20,8) code, burst
builders and a 4FSK modulator. A chance correlation with a SYNC is common in a busy
capture; it is told from a real one by how closely the 24 SYNC symbols fit four levels
(RMS residual about 0.33 of a level unit for real SYNCs, over 4 for chance ones), and a
level scale far from 648 Hz a unit. Table B.11's Golay(20,8) code has a minimum
distance of 8 by count, one more than the (20,8,7) the text names; it still corrects 3.
A superframe whose burst A was missed, and the root-raised-cosine symbol filter, are
in [Reading weak and cut calls](#reading-weak-and-cut-calls-recover-and-matched).

## A transmitter: `dmr_synth.py` (2026-11-02)

`scripts/dmr_synth.py` writes one PTT press as a cf32 file: Voice LC Header, voice
superframes, Terminator with LC, from a handheld (MS-sourced SYNCs, or TDMA direct mode
slot 1 or 2), one 27.5 ms burst every 60 ms with the transmitter ramped off in between.
`--sync bs` writes a repeater instead; that path is
[The repeater stream and the CACH](#the-repeater-stream-and-the-cach).
Beside it go a JSON sidecar (every burst's sample, the LC, the colour code) and the
`.ambe` of the voice frames sent. Voice is replayed AMBE+2 frames from a Sceptre `.ambe`,
or the silence frame: the codec cannot be encoded from the standards. The FEC is in
`scripts/dmr_fec.py`, written from the Annex B tables, and `scripts/test_dmr_synth.py`
checks it.

What is checked, and against what:

- **Table E.1.** The BPTC(196,96) matrix and the `x 181 mod 196` interleave give the
  table's transmit order for all 196 bits. The test reads the table out of the spec text
  in `docs/ptt/dmr/text/` when it is there.
- **A real call.** On the BS-sourced call whose voice frames matched Sceptre's `.ambe`
  (above):
  - every Voice LC Header and the Terminator decode with no failing BPTC row or column,
    and pass the RS(12,9) check after unmasking with 969696 and 999999 (Table B.21);
  - in every superframe, bursts B-E carry the embedded LC with no failing Hamming(16,11)
    row or column parity and a correct 5-bit checksum;
  - the EMB's QR(16,7) decodes with no errors, LCSS running 01, 11, 11, 10, then 00 in
    burst F, and burst F carries the Null embedded message.

  Re-encoding the decoded content reproduced all six headers and terminators and all
  nine superframes' centre fields bit for bit. So the RS(12,9) generator matrix, the
  parity byte order (p2, p1, p0 after LC_0..LC_8) and the masks, the embedded-LC layout
  and read-out (Figure B.3) and the EMB generator (Table B.12) are all right as written.
- **Round trip.** Whole synthetic calls go through `dmr_demod.py`: clean, direct mode, and
  at 1 MS/s with an offset, a 300 Hz carrier error and 18 dB SNR in 12.5 kHz. Every voice
  frame, slot type, LC and EMB comes back.

Two traps found on the way:

- **A header's LC need not be the standard one.** On that Capacity Plus call the Voice LC
  Headers carried a manufacturer-specific FLCO with a nonzero FID, while the embedded LC
  and the Terminator carried the standard Grp_V_Ch_Usr (FLCO 0, FID 0). A decoder that
  reads the IDs only from the header will report the wrong ones.
- **Squelch a bursty signal before `find_syncs`.** With a handheld off half the time,
  the FM discriminator on noise alone swings by kilohertz. `find_syncs` sets its
  threshold relative to the largest correlation in the whole span, so at 18 dB it found
  only 3 of 5 superframes. Zeroing the discriminator where the channel power is low
  found all 5. `dmr_demod.discriminate(x, fs, squelch=0.25)` (`--squelch 0.25` on the
  command line) does this; it is off by default. Ask for it on a synthetic file whose
  transmitter is off half the time. On a real recording leave it off: it collapsed the
  handheld calls. See
  [Reading weak and cut calls](#reading-weak-and-cut-calls-recover-and-matched).

The BS-sourced (repeater) stream is built (`--sync bs`): the CACH, TACT Hamming(7,4),
Short LC, and Idle in the other slot. See
[The repeater stream and the CACH](#the-repeater-stream-and-the-cach).
Not yet decoded by any decoder outside this code (no DSD-FME here). The voice frames
still have no FEC here.

**Grok's review (2026-11-02, of `eea4771`).** Headless under `sdr-review`, 63 turns,
$1.71, about 50 minutes, with no recordings readable (see
[grok-worker-and-reviewer](grok-worker-and-reviewer.md#the-dmr-transmitter-review-2026-11-02)).
Grok wrote its own encoders from the spec tables, and they agreed with `dmr_fec.py` on
everything: RS(12,9) (Tables B.18 and B.19), the masks, every generator matrix, the Idle
message (Figure D.1 and Table D.3), the embedded LC, the EMB, the Full LC, the SYNCs, the
dibits, the deviation (a +3 +3 -3 -3 pattern peaks at 2767 Hz against 2749 Hz +-10 %), the
SNR and the CFO sign. It also reproduced the squelch's 3 then 5 of 5 superframes. Its
findings, each checked here:

- **Real and fixed:**
  - Symbol centres were half a symbol early. Clause 10.2.3.1.2 puts the first centre
    65.5 symbol times before the slot centre, and the code put it at 66. The waveform
    and the sidecar's samples now move 5 samples later at 48 kS/s.
  - An empty `.ambe` crashed with `ZeroDivisionError`; it now raises `ValueError`.
  - The sidecar called every call MS-sourced; it now names the sync set it used.
  - `discriminate` crashed on input shorter than a symbol, with or without squelch.
  - The tests passed broken encoders: swapped masks, swapped Hamming parity columns, a
    mod-32 checksum and a wrong FLCO, because each round trip builds and accepts a word
    with the same function. `test_dmr_synth.py` now pins the masks, the checksum, two
    Full LC vectors, Tables B.11-B.16 and B.18, the Idle message (D.2 to D.3) and
    Table 9.2's SYNCs against the spec text. All four mutations now fail the test.
    The sync-timing check is tightened from one symbol to one 48 kS/s sample.
    `test_dmr_demod.py` now asks for Golay distance 8, not 7 or more.
- **Left alone:** `load_frames(None, 0)` gives one superframe while the command line's
  default is 10. A file of fewer than 18 frames is repeated to fill a superframe.
- **Not a defect:** the rest of its checker's FAIL lines, which it explained itself.

Still self-confirming: the whole-call round trips use this code's own modulator and
demodulator, so a mistake made in both comes back clean. The spec pins cover the
tables, but only an outside decoder would cover the rest.

**Opus's review of the recover, matched and CACH work (2026-11-02, of `fc38e91`).** An
independent model, on a `git archive` copy with the spec text and no recordings. Its own
TACT, CRC-8, Short LC (Figure B.6), CACH (Figure B.9) and Activity Update, written from the
polynomials and figure names, agreed with `dmr_fec.py` on 5000 random inputs; it could not
make `recover` accept a wrong superframe (two slots of voice with one colour code, slot 2
shifted by 0, 1, 3 and 7 bursts, every burst A blanked, extra headers and terminators, 12 dB
noise); the BS stream's geometry measured right (first symbol centre 65.5 symbols from the
slot centre, the CACH straddling the slot boundary, a flat carrier). Its findings, each
checked here:

- **Real and fixed:**
  - `discriminate(matched=True, squelch=...)` gated after the 161-tap RRC, so noise from the
    transmitter-off time reached the first and last symbol of every burst. That, not the RRC,
    was the 9 to 17 bit errors at 18 dB. The gate now comes first (30 seeds, MS 48 kS/s, 18 dB:
    53 bit errors to 3; 14 dB 180 to 41); the sweep above was redone.
  - `matched=True` on 11 to 161 samples returned 161 samples (and raised with a squelch), and
    `find_syncs`, so `voice_calls`, crashed on 0 and 2 to 2000 samples: `np.convolve(..., 'same')`
    returns max(len) samples. A `_same()` helper keeps the length.
  - `read_cach` and `decode_cachs` sat after `sys.exit(main())`, citations pointed at a file
    that is not in the repository, a docstring named the wrong test, and the RRC was said to add
    no delay (its SYNC peak lands up to 0.89 of a sample earlier than the boxcar's).
  - `dmr_synth` accepted negative `--superframes`, `--headers`, `--terminators`, `--lead` and
    `--tail`, an `--fs` that aliases, and gave a traceback for a bs `--lead` under 1.25 ms.
  - Test gaps, each shown by a mutation that passed: with no spec text `test_dmr_cach.py` ran
    zero checks and printed PASS (now it counts the skipped pins on its last line); the BS
    timing was never compared with the waveform (removing the half-symbol shift, or moving
    every sidecar position 7 samples, passed); five of the six recover gates and the RRC's
    alpha (0.2 to 0.15) were unpinned; one 18 dB check rested on one seed. All are pinned now
    and each mutation fails a test, and a regression test fails if the squelch moves back
    after the RRC.
  - Notes: the burst-letter counts of the wrong symbols (they are A 3, B 2, C 2, D 1, E 1,
    F 3, and the frames 5, 4 and 3; both sum to 12) and the inner to outer ratio (2.9).
- **Checked here, which the review could not:** the MS, direct1 and direct2 output (cf32 and
  sidecar) and the default demodulator's output are byte-identical to the commit before this
  work, on four synthetic calls (clean, both direct slots, 1 MS/s with offset, CFO and 18 dB).
  The 26-call real test gives the same 3131 of 3150 on the fixed code (it used no squelch).
- **Left as it is:** `matched=True` is not a textbook matched filter (RRC on a frequency
  discriminator output gives a raised-cosine response with no ISI at the symbol centres; the
  docstring now says so). A half-sample 160-tap RRC would trim a few bits more at 14 and 12 dB;
  not built. The recover residual gate cannot be separated from a gate of 99 by Gaussian noise
  on the symbol centres alone, so it is pinned by a crafted fit, not a statistical one.

## Reading weak and cut calls: `recover` and `matched`

Both default off. `voice_calls(..., recover=False)` returns a superframe only when
burst A's voice SYNC is found and all 18 frames lie inside the array.
`recover=True` (`--recover`) also tries:

- the sample one superframe before and after each SYNC that was kept: 1728
  symbols, 17280 samples at 10 samples a symbol. A recovered position is not a
  new anchor, so this fills a hole from each end by one superframe only;
- a burst A timed from a voice LC header or a PI header one burst later, or
  from a terminator six bursts earlier (`anchor_delta_bursts` is +1 and -6,
  clause 5.1.2.2). The colour code is the one Golay decoded on that control
  burst. The walk continues one superframe at a time while each candidate
  passes, so a header can fill more than the one neighbour step. Any other found
  SYNC is an anchor of last resort: see "Recovery from any SYNC" below.

The candidate still has to pass the EMB and LCSS rule below. `fit_levels` is
unchanged; `discriminate` gained `matched` (its default output is unchanged), and
`discriminate` and `find_syncs` no longer crash on very short input (0 to 2000
samples), where `np.convolve(..., 'same')` had returned more samples than it was given.

`discriminate(..., matched=False)` is the length-10 boxcar, and omitting the
keyword is that same array. `matched=True` (`--matched`) replaces the boxcar
with `rrc()` after the same 101-tap low-pass: alpha 0.2, span 8, 161 taps,
clause 10.2.2.2. The filter is odd and symmetric, so it adds no delay of its own
on top of the 101-tap low-pass (50 samples at 48 kS/s). A SYNC correlation peak
still lands up to 0.89 of a sample before sidecar position + 50, against 0.40
for the boxcar (a review's measurement): inside the one-sample tolerance of the
sidecar test, but using most of it. Filtering the transmitter's RRC pulse with the
same RRC gives a raised-cosine response with no intersymbol interference at the
symbol centres; on a frequency-modulated signal it is not a textbook matched
filter. With a squelch the gate is applied BEFORE the RRC (see below).
`voice_calls`, `data_bursts` and `read_burst` read whichever array came back. On a clean call, MS and direct mode, at 48 kS/s
and at 1 MS/s with an offset and a 300 Hz error, both filters put every SYNC
within that one sample (`scripts/test_dmr_matched.py`). The FFT of `rrc()`,
normalised, runs from 0.991 to 1.005 through 1920 Hz and stays under 0.0081
from 3400 Hz up. Equation (14)'s cosine did not survive extraction
(`cos(pi f / 1920)` is -1 at 1920 Hz), and clause 10.2.2.3 prints no h(t), so
the taps are pinned to those edges, not to a printed impulse response.

### How recover accepts a superframe

`superframe_candidate` tries the predicted sample and 4 samples either side
(`RECOVER_TIMING_RADIUS`). On the clean 200-superframe call, two found SYNCs
sat 34559 samples apart, which is `2*17280 - 1`, so the peak wanders by a
sample. The search keeps the alignment whose B-F symbols sit closest to the
four levels after a two-pass decision-directed refit, seeded from the anchor.
The refit must land in `300 < scale < 1000` (positive; nominal is 648 Hz a
unit). Then, at that alignment, each of bursts B-F:

- the 16 EMB bits are `centre[:8] + centre[-8:]` of the 48-bit centre
  (Table E.6);
- `emb_decode` is within 1 bit (`RECOVER_MAX_EMB_ERR`) and the colour code
  equals the anchor's;
- LCSS follows B-F = 01, 11, 11, 10, 00 (Table 9.20, clause 7.1.3: first,
  continuation, continuation, last, single fragment).

A missing burst, a failed decode, a wrong colour code, or a wrong LCSS each
counts as one mismatch. Accept when at least 4 of the 5 EMBs decode
(`RECOVER_MIN_EMB`) and there is at most one mismatch
(`RECOVER_MAX_LCSS_MISMATCH`). One missing burst uses that one miss. The
residual of the refit must be at most 0.75 symbol units
(`RECOVER_MAX_LEVEL_RMS`). Symbol centres closer than 160 samples to either
end of the discriminator are not returned (`RECOVER_EDGE`): the 101-tap
filter and the 8-symbol RRC have no history there.

Residuals on synthetic MS calls, squelch 0.25, which is why 0.75 is loose:

- Clean, 6 superframes: global residual min/median/max 0.214 / 0.228 / 0.232.
  Per-frame max 0.295.
- The one 12 dB superframe recover adds at 48 kS/s has a frame at 0.483. A
  gate of 0.45 would drop that superframe.
- Squelched noise, the same 200 positions as the false-accept count: 196 of
  200 refit inside the scale window, residual min/median/max
  0.298 / 0.377 / 0.473. All 196 are under 0.75, and 99.5% are under 0.45.

Noise is rejected by the EMB and LCSS checks, not by the residual gate.

False accept. `scripts/test_dmr_recover.py` prints this for one clean
200-superframe MS call and 200 squelched-noise stretches:

```
false-accept 0/200 noise, false-reject 0/200 real, wrong-cc accept 0/200, off-grid accept 0/200, sync missed [23] and recover=True returned all 200
```

Every one of the 200 came back bit-exact. The same position with the colour
code flipped was rejected, and so was the position one burst (288 symbols)
off that slot. `find_syncs` missed superframe 23; recover put it back.
`find_syncs` was left as it is. With the matched filter that same call misses
superframes 23 and 26, and recover still returns all 200, bit-exact. The same
200 noise positions give 0/200 with the boxcar and 0/200 with the RRC, and
`voice_calls(..., recover=True)` returns nothing on that noise either way.

`return_info=False`, which is what `--recover` uses, keeps a recovered
superframe only when all 18 frames were read. `return_info=True` puts `None`
where a frame was outside the array, inside the 160-sample edge, or over the
residual gate. A candidate with fewer than 4 decodable B-F EMBs is not a
superframe. There is no voice-frame FEC on this path.

### What it changes

Synthetic, from `scripts/test_dmr_recover.py`:

- Blanking burst A's SYNC on the discriminator, 6 superframes, MS and direct
  mode, at 48 kS/s and at 1 MS/s with an offset: the old decoder keeps 5, and
  recover returns all 6, bit-exact against the `.ambe`.
- One superframe and no neighbour. Blanking A, with the header and the
  terminator still in the file, used to return nothing and is now bit-exact.
  Header alone (terminator cut off) is the same. Cutting the file at the
  start of burst B, so the header and A are gone, is recovered from the
  terminator: frames 4 through 17, and the default output drops the partial.
- 12 dB, 20 seeds, 5 superframes. At 48 kS/s: 99 superframes, then 100; the
  extra one has 4 bit errors. At 1 MS/s with an offset: 97, then 100; the
  three extras have at most 18 bit errors. Where the old decoder returned a
  frame, the new one matches it (0 mismatches). Recover never returned fewer.
  Those extras are channel errors in frames the old path did not return.
- A clean call with a noise lead and tail does not grow an invented
  superframe. Complex gaussian noise, squelched and unsquelched, returns 0.
- Cuts of one 5-superframe call, at both rates: frames that are present are
  bit-exact. A cut through the middle of burst D leaves fewer than 4 EMBs,
  so that superframe is dropped. The command line omits a partial rather
  than writing a hole.

The matched filter on synthetic calls, 20 seeds, 2 superframes, 720 voice
frames, squelch 0.25, recover off (a missed superframe stays missed). Exact is the
number of the 720 frames that are bit exact; bit errors are those in the frames
that were returned (the 18 frames of a missed superframe count in neither). Clean
calls stay 36/36 for both filters.

| call | SNR | boxcar exact | boxcar bit errors | RRC exact | RRC bit errors |
|---|---|---|---|---|---|
| MS, 48 kS/s | 18 dB | 719 | 1 | 720 | 0 |
| MS, 48 kS/s | 14 dB | 672 | 51 | 718 | 2 |
| MS, 48 kS/s | 12 dB | 490 (2 superframe(s) missed) | 256 | 688 | 35 |
| MS, 1 MS/s, offset, 300 Hz | 18 dB | 719 | 1 | 720 | 0 |
| MS, 1 MS/s, offset, 300 Hz | 14 dB | 655 (1 superframe(s) missed) | 50 | 719 | 1 |
| MS, 1 MS/s, offset, 300 Hz | 12 dB | 518 (2 superframe(s) missed) | 207 | 698 | 28 |
| direct, 48 kS/s | 18 dB | 718 | 2 | 720 | 0 |
| direct, 48 kS/s | 14 dB | 675 | 52 | 720 | 0 |
| direct, 48 kS/s | 12 dB | 502 (1 superframe(s) missed) | 267 | 693 | 28 |
| direct, 1 MS/s, offset, 300 Hz | 18 dB | 719 | 1 | 720 | 0 |
| direct, 1 MS/s, offset, 300 Hz | 14 dB | 656 (1 superframe(s) missed) | 47 | 720 | 0 |
| direct, 1 MS/s, offset, 300 Hz | 12 dB | 511 (2 superframe(s) missed) | 220 | 694 | 29 |

With the RRC the bit errors are 0 to 2 at 18 and 14 dB and about 30 at 12 dB,
against 1, about 50 and 210 to 270 for the boxcar, and it keeps superframes the
boxcar missed at 12 dB. This sweep is the measured synthetic run, made after the
review fix below. It is not asserted inside `test_dmr_matched.py`.

The squelch has to come before the RRC. The first version gated the output after
the 161-tap filter, so the noise of the transmitter-off time on either side leaked
into the first and last symbol of every burst. That looked like the RRC costing
9 to 17 bit errors in 720 frames at 18 dB. An independent review found the cause
(MS, 48 kS/s, 18 dB, 60 seeds: 53 bit errors, 48 of them on symbols 0 and 131,
against 0 for the boxcar), and gating the discriminator output first brought it to
3 over 30 seeds. Gating again after the filter is worse at 12 dB (8254 against
2129 bit errors over 30 seeds, a missing frame counted as 72). The boxcar path is
unchanged.

On a real recording (about two minutes, many channels): 26 voice calls, 3150
voice frames, 7 on one slot and 19 on the other, 23 repeater-sourced and 3
handheld. Each logged frame was looked for among the decoded frames. Exact
frames of 3150:

| | exact frames | calls with every frame exact |
|---|---|---|
| decoder as it was | 3014 (95.7%) | 20 of 26 |
| `recover=True` | 3101 (98.4%) | 21 of 26 |
| `recover=True` and `matched=True` | 3131 (99.4%) | 25 of 26 |

Nothing got worse at any step. The one call still short is a weak handheld:
89 of 108 exact, 107 of 108 within 2 bits. Six clips from that recording,
exact frames as old / recover / both: a call at the file start 0/18, 17/18,
18/18; the weak handheld 40/108, 74/108, 89/108; a call missing a whole
superframe 54/72, 72/72, 72/72; scattered bit errors 134/144, 134/144,
144/144; a clean repeater call 270/270; a clean handheld call 72/72. The
three handheld calls are the first real check of the MS SYNCs.

What to run on a real signal: `--recover --matched`, and do not pass
`--squelch`. The command line's `voice_calls` searches the BS voice SYNC
only. A handheld call is `voice_calls(d, voice='MS_VOICE', recover=True)`
after `discriminate(..., matched=True)`.

### Traps

- **`sync_ok` still gates a found burst A.** The gate is a residual under
  0.6 on the 24 SYNC symbols, and a scale between 300 and 1000 Hz a unit.
  On the call that begins within the first 0.1 s of its file, `find_syncs`
  found burst A and `sync_ok` rejected it: residual 0.676. There is no
  second superframe, so the neighbour step has nothing to use. Recover
  timed it from the terminator and returned 17 of 18 frames, one frame off
  by 1 bit. The matched filter brings that opening SYNC under the gate
  (residual 0.333) and all 18 frames are exact. Do not lower the gate. On
  the weak handheld the extra peaks `find_syncs` already sees have SYNC
  residuals from about 2.7 to 5.9, which is what the gate is there to drop,
  and the frames that are still wrong differ by 1 to 5 bits. A lower
  threshold does not make those bits match.
- **The logger's timestamp sits about 0.55 s after the call's first
  superframe.** A read that starts 0.5 s before that timestamp misses a
  superframe of every call (85.4% that way). Read at least 2 s early.
- **Squelch 0.25 is the wrong setting on a real handheld.** It is what the
  synthetic bursty files need. On the real clips it collapsed the decode:
  the weak handheld fell to 1 of 108 and 14 of 108 with the boxcar, or 3 of
  108 and 19 of 108 with the matched filter, and a clean handheld went to
  0 of 72 either way. Leave it off.

### Recovery from any SYNC

`recover=True` ends with a grid step (`_recover_from_grid`). Every burst of one slot
is a multiple of 288 symbols from every other burst of that slot, and on a repeater's
outbound carrier the other slot's bursts are 144 symbols away, so ANY found SYNC
fixes the burst grid: a CSBK, an Idle, a data header, an MBC burst, or a voice A whose
neighbours are not superframes. What a data burst does not give is the phase, which
of the grid positions a superframe starts on. Each position within 11 bursts of an
anchor (two superframes, every phase twice; 144 symbols off as well for `BS_VOICE`)
is tried and kept only if the existing `superframe_candidate` rule accepts it, with
the anchor's colour code and no threshold changed: only the true phase has the LCSS
sequence 01 11 11 10 00 in B-F. A hit is then walked a superframe at a time in both
directions. Two predictions closer than `RECOVER_TIMING_RADIUS` are one position.

Checked on synthetic streams only, and the real recording has no call it would
recover (its one imperfect call is bit errors, not a missing position), so its value
on real signals is unmeasured; on the 26-call real test it changes nothing (3131 of
3150 with and without it). Recovered bit-exact where the older steps returned
nothing: a repeater stream with `--headers 0 --terminators 0` and burst A blanked,
the other slot's Idle bursts as the only anchors (both slots, both rates, clean and
18 dB); two superframes with both A SYNCs blanked; a CSBK as the only anchor in MS and
direct streams, before A or after F. Refused, with no false accept: a wrong phase
(shifted 1 to 5 bursts), 300 noise stretches after a real CSBK (5170 candidates, none
accepted), a different call on the same slot, the other slot carrying voice with the
same colour code at shifted LCSS phases (exactly the two real superframes came back,
each on its true slot, no duplicates). Cost: 0.9 s against 0.17 s for 15 s of
repeater stream with 40 superframes and 240 Idle anchors.

Limit: nothing in the discriminator array separates the two slots of a repeater's
stream except position, so a superframe of either slot that passes is returned, as
`voice_calls` already does when A's SYNC is found for either; the CACH slot bit (TC)
says which slot a burst belongs to. `--recover` writes both slots into one `.ambe`.
The 144-symbol offset is not tried for `MS_VOICE`, although a repeater's inbound
channel has the same layout: a missed recovery, not a wrong one.

**Opus's review of the grid step (2026-11-03).** Same method as before: a `git archive` copy
with the spec text and no recordings. It found no false accept: both slots carrying voice
with one colour code, the other slot shifted 0 to 5 bursts, A blanked in one or both, clean,
14 and 11 dB (72 runs: exactly the real superframes, each on its true slot, no duplicates);
every A blanked on a repeater stream at 12, 10, 8 and 6 dB (45 of 45, 45 of 45, 11 of 45
and 0 of 45 recovered, none a wrong one, 1 to 15 bit errors per superframe at 12 dB); 288
comparison runs where `recover=False` matched the commit before exactly and `recover=True`
never dropped or changed a superframe the older steps found; no SYNC read as an EMB (none
comes within 1 bit of a QR(16,7) codeword); inputs of 0 to 3000 samples. Findings:

- **Real and fixed:** the dedupe skipped any prediction within 40 samples of one already tried,
  while `superframe_candidate` searches only +-4, so a same-colour-code CSBK copy 5 to 40
  samples off the grid suppressed the recovery the real CSBK makes (0 superframes returned
  instead of 1). The tolerance is `RECOVER_TIMING_RADIUS` now. `data_bursts` ran twice per
  call; it runs once (a 58 s stream was 1.4 s before the grid step, 3.5 s after it).
- **Test gaps, each shown by a mutation that passed:** the walk (removed, or forward only) was never
  needed because the window reached every superframe; the same-slot case on a repeater stream;
  the file-edge guard; the dedupe. Cases added for each, and each mutation fails a test now. Two
  mutations still pass, correctly: `RECOVER_MIN_EMB` 4 to 2 (the mismatch limit already forces 4
  of 5) and a wider window (harmless); `_inside_have` only saves candidate calls.
- **Left as it is:** the slot limit above, and the 144-symbol offset for `MS_VOICE`.

**A half-sample matched filter did not help.** The RRC's SYNC peak lands up to 0.89 of
a sample before sidecar + 50 (the boxcar's, 0.40), and an even-length 160-tap RRC
centred half a sample earlier puts it where the boxcar does. Built, and measured: equal
to `matched=True` at 18 and 14 dB on synthetic calls, worse at 12 dB (direct mode and
the repeater stream at 1 MS/s, 66 and 79 bit errors over 40 seeds against 42 and 55,
about 4.5 to 5 standard errors in direct mode), and worse on the real recording (3124
of 3150 against 3131: the weak handheld 84 against 89 of 108). It was removed. The
peak offset is cosmetic for the bit decision.

### Still not recovered

- The weak handheld's remaining 19 frames. The superframes are already
  returned. The wrong symbols are one level off, inside the burst. 107 of
  the 108 frames are within 2 bits. No voice FEC was added; the Golay
  layouts that were tried are not in these frames (see
  [What the voice-frame bit errors were](#what-the-voice-frame-bit-errors-were)).
- A call whose only surviving SYNC is further than 11 bursts away from every
  burst A, when nothing walks across: the grid step reaches 11 bursts either side
  of an anchor and then walks a superframe at a time from each hit.
- A superframe that is only partly in the file, or that has fewer than 4
  decodable B-F EMBs. `--recover` omits it. Burst A by itself, or a tail
  that dies in burst C, is dropped. A lone superframe whose A, header and
  terminator are all gone has nothing to time it from.

## The repeater stream and the CACH

`--sync ms`, `direct1` and `direct2` are unchanged: one slot, the transmitter
off in the other. `--sync bs` is the repeater. `--short-lc` applies only
there; the default is `null`.

A repeater outbound channel (clause 4.6.1) is a continuous carrier: two 30 ms
slots, transmitted without gaps while the BS is on. Clause 10.2.3.1.2 keeps
the 132-symbol burst centred in the slot (the first symbol centre is 65.5
symbol times before the slot centre). The CACH is the 12 symbols / 2.5 ms
immediately before that burst, so it straddles the slot boundary, 6 symbols
in the previous slot and 6 in this one (Figure 4.5)
[Part1, Fig 4.5, p.22]. The burst ends where the next CACH starts. The
envelope is 1 from the first CACH symbol through the last burst symbol. The
0.5 ms ramp is only the key-up and the key-down. Lead must be at least 6
symbols (1.25 ms) or the first CACH has nowhere to sit. Same-slot bursts stay
288 symbols / 60 ms apart, so `voice_calls` did not change.
`read_cach(d, burst_pos, scale, off)` reads the 12 symbols before the burst's
first symbol; the SYNC is 54 symbols after that.

The stream starts with slot 1, then slot 2, for as many call bursts as the
MS path would have emitted. The call occupies `--slot` and uses `BS_VOICE` /
`BS_DATA`, with the same header, embedded LC and terminator as the MS
builder. The other slot is an Idle burst: data type Idle, `BS_DATA`, the
96 information bits of Table D.2 packed `FF83DF1732094ED1E7CD8A91`, then the
existing BPTC(196,96). On a clean 48 kS/s file the IQ magnitude between the
outer ramps stays above 0.45 and below 0.55, which is the 0.5 scale `render`
applies, and it does not drop between slots.

### TACT and Short LC

TACT is 7 bits, Table 9.5 order AT, TC, LCSS, then Hamming(7,4,3) parity
(B.3.5, Table B.17, G(x) = x^3 + x + 1) [Part1, Table 9.5, p.92]
[Part1, B.3.5, p.139-141]. TC follows Table 9.24: 0 means the following
burst is channel 1, 1 means channel 2, so the bits alternate 0, 1 from the
first slot [Part1, Table 9.24, p.102-103]. AT describes the inbound slot
whose number is TC. This generator sets AT busy on the call's slot and idle
on the other. The Table 9.5 remark that sets AT to 1, and the sentence that
an MS in continuous transmission sets AT busy and alternates TC, are the
Tier I MS rule. They are a different transmitter.

A Short LC is 28 information bits plus the CRC-8 (Table 9.8, B.3.7).
G8(x) = x^8 + x^2 + x + 1, initial remainder 0, no inversion
[Part1, B.3.7, p.143]. Three Hamming(17,12,3) rows (Table B.13,
g(x) = x^5 + x^2 + 1) and one even column-parity row, then columns
top-to-bottom, left-to-right, into four 17-bit fragments (Figure B.6).
Each fragment is interleaved with its TACT by Figure B.9 (B.4.1). One
fragment per CACH, so one Short LC every 4 x 30 ms = 120 ms (clause 7.1.4)
[Part1, B.2.3, p.130-132]. LCSS on those four fragments is 01, 11, 11, 10
(Table 9.20). Clause 7.1.4 and Figure 7.7 say the message takes four CACH
bursts. They do not print that LCSS sequence. Table 9.20's note says there
is no single-fragment LC for CACH signalling, which leaves the three
multi-fragment values, in that order, for a four-burst message.
`decode_cachs` returns one TACT per word and a Short LC wherever four LCSS
values are 01, 11, 11, 10. It steps one word at a time, so a stream that
starts mid-message still locks.

Null, `--short-lc null`: the repeater has nothing else to put in the CACH.
Clause 4.6.1 says to send Null message Short LCs as defined in part 2.
Part 2 Table 7.9: SLCO 0000, 24 reserved bits 0. Part 2 Table B.3 is the
opcode list (0000 Null, 0001 Activity Update). Part 1 Table 9.27 only says
the coding is in part 2. The all-zero 28 bits have CRC 0, and the BPTC of a
zero message is 68 zeros, so each payload fragment is 17 zeros. The LCSS
cycle in the TACT still runs. The unused slot's Idle message is a different
fill (clause 7.3, Tables D.2 and D.3).

Activity Update, `--short-lc activity`: part 2 Table 7.10, MSB first, which
is the B.3.7 octet mapping. SLCO 0001, slot 1 activity, slot 2 activity,
hash of slot 1, hash of slot 2. The call's slot gets group voice 1000 or
individual voice 1001 from `--call`, and the hash of the destination. The
other slot gets activity 0000 and hash 0. The hash is the same CRC-8 of the
24-bit address, MSB = x^23. There is no bit diagram and no numerical hash
in the text. The field order rests on the table row order plus that octet
mapping.

The sidecar, on the `bs` branch only, adds `cach_sample`, `slot`, `tact`
and `short_lc_fragment` on each burst, and `cach_length_s` 0.0025,
`burst_period_s` 0.03, `same_slot_period_s` 0.06, and `short_lc`.

### What the spec text does not give

These parts rest on the tables, the polynomials and the bit-name figures.
The text has no numerical vector to pin them to. `scripts/test_dmr_cach.py`
says so at the top of its output, and it reads the pins out of
`docs/ptt/dmr/text/` when that text is there.

- No worked example of a TACT codeword, a CRC-8 value, a Short LC fragment,
  or a 24-bit CACH word. Annex D's worked bits are the Idle message
  (Tables D.2 and D.3) and the Null embedded message.
- Annex E is tables E.1 through E.12. The annex body does not contain the
  string CACH. The CACH bit order is Figure B.9 (TACT plus payload, names)
  and Figure B.6 (fragment names). Both figures are names, not 0/1 vectors.
- Clause 7.1.4 does not define the Null bit pattern. It says a Short LC is
  28 bits plus a CRC, FEC as in B.2.3, delivered in four CACH bursts, one
  message per 120 ms. Table 9.27 has no opcode values. The Null pattern is
  part 2 Table 7.9, and the opcode list is part 2 Table B.3.
- The LCSS sequence 01, 11, 11, 10 is not printed on the CACH. It is
  Table 9.20 applied to the four bursts of clause 7.1.4.
- Activity Update has no bit diagram and no example hash. The usual CRC-8
  check value of the ASCII string "123456789" is not in the spec, and the
  test does not pin it.
- Hamming(7,4) and Hamming(17,12) are pinned to the printed matrices and to
  G(x) / g(x). There is still no numerical TACT or Short LC word in the text.

With neither part's text in the tree, `test_dmr_cach.py` skips the pins and
the call round-trips and still prints PASS. The round trip itself, driven
from the same cases, does pass: both slots, the Null Short LC, an Activity
Update on a group call and on a unit call, 48 kS/s clean, and 1 MS/s with
an offset, a carrier error and 18 dB SNR. Voice frames, header and
terminator LC, embedded LC, every TACT, the reassembled Short LC and its
CRC, and the Idle bursts come back.

### The six mutations

Each one was a copy of the library. The worktree file was not the copy.
`scripts/test_dmr_cach.py` was run against that copy, and every run exited 1.
Several of them still round-trip, which is the failure the last review
called out: the same table builds the word and accepts it. The spec pin is
what fails. The shared tables are visible in the code: `tact` and
`tact_decode` use `H743_G`, `cach` and `cach_split` use `CACH_TX_NAMES`,
`decode_cachs` groups with `SHORT_LC_LCSS`, and `crc8` uses `CRC8_POLY`.

| mutation | what failed | what still passed |
|---|---|---|
| TACT parity. H(0) of the first row of `H743_G` flipped (`1 0 0 0 1 0 1` to `1 0 0 0 1 0 0`) | generator matrix Table B.17, and the TACT parity of all 16 infos | "every TACT is a Hamming(7,4) codeword", because encode and decode share the matrix. One 1 MS/s Idle BPTC check also moved: the mutated CACH sits in the demod filter's memory just before the burst, and at 18 dB that one Idle word changed. The two spec pins failed on their own |
| Hamming(17,12) column. Last parity bit of row 0 of `H17123_G` flipped | generator matrix Table B.13, and the Short LC BPTC check (library encode against a matrix parsed from Table B.13) | no call-level check |
| CRC-8. `CRC8_POLY` `0x107` to `0x105` | the CRC-8 check against G8(x) parsed from B.3.7, the Figure B.6 BPTC check, and the Activity Update CRC octet on the air | `crc_ok` on those words, because the decoder uses the same poly. A Null message CRCs to 0 for any poly, so the Null cases did not catch it |
| CACH interleave. `AT` and `P(16)` swapped in `CACH_TX_NAMES` | Figure B.9 transmit order, and `cach()` placing TACT and payload where Figure B.9 names them | every call-level check, because `cach` and `cach_split` share the list |
| LCSS. `SHORT_LC_LCSS` first entry changed from 01 to 10 | every call, LCSS 01, 11, 11, 10 against Table 9.20 (got 10, 11, 11, 10) | reassembly, because `decode_cachs` groups with the same list |
| TC. `TC_OF_SLOT` swapped to channel 2 then channel 1 | every call, TC alternating 0, 1 from the first slot (got 1, 0, 1, 0; Table 9.24 wants 0, 1, 0, 1) | the recorded failure is that TC line |

### Against four real repeater clips

Four repeater clips from a real recording, read with the matched filter.
721 CACH words on a 30 ms grid. The 24-bit word re-encoded from its decoded
TACT and payload matched on 710 of the 710 words whose TACT had no error.
170 Short LC cycles: all 170 pass the Hamming rows, the column parity and
the CRC-8, and all 170 re-encode bit for bit from the decoded 28
information bits. 136 are non-zero and 34 are the Null Short LC.
TC alternates with the slot grid. AT is a status bit that changes in runs
on busy slots. LCSS follows 01, 11, 11, 10. Without the matched filter, 2
cycles of the noisiest clip failed by one payload bit each.

Still unverified on those clips: the Activity Update field order against a
known address, which opcode the non-zero messages are, and voice bytes
against an outside decoder. The spec still prints no numerical CACH word.

## What the voice-frame bit errors were

The frames that were one or two bits off, on a real recording, before the
matched filter. The demodulator at its defaults: the 101-tap low-pass, the
discriminator, the length-10 boxcar, a hard slice at +/-2 and 0, levels
fitted once on the 24 SYNC symbols and reused for all six bursts. Dibits
are Gray, so a step of one level is one wrong bit. No decoded address or
voice byte is in this account.

On the call whose coverage count is 134 of 144 exact, aligning whole
superframes counted 133 exact: 11 frames, 12 wrong symbols. Ten of those
frames differ by 1 bit. One differs by 2 bits, and those are two adjacent
symbols, one bit each. Every wrong symbol is a step of exactly one level.
No dibit is wrong in both bits. They are not clustered: of the 12, one is
within 2 symbols of the centre field and one is within 2 symbols of a burst
edge; burst letters run A 3, B 2, C 2, D 1, E 1, F 3; the three frames
inside the burst carry 5, 4 and 3 of them. Over the 5184 compared symbols,
inner levels (+/-1) failed about 2.9 times as often as outer levels (+/-3):
9 of 2656 inner, 3 of 2528 outer. Nine of the 12 margins are under 0.224,
the 1st percentile of the correct symbols on that call; the median error
margin is 0.082. One adjacent pair is the length-10 boxcar exchanging the
two levels (margins 1.726 and 0.580). The root-raised-cosine samples at
those two instants slice back to the truth.

The SYNC fit on that call absorbed a scale below the nominal 648 Hz a unit
(537 to 571, median 557). The errors are not a per-burst gain error.

The weak handheld is the same picture, noisier, plus superframes that never
clear `sync_ok`. On the three superframes that do, every wrong symbol is
again one level. Inner symbols fail about 1.9 times as often as outer ones.

What was tried on the same recording, without looking at the truth frames
while choosing the sample:

| change | symbols fixed, of the 12 | cost elsewhere |
|---|---|---|
| correlation-peak timing, one offset per superframe | 0 | none. The peak is already on the integer sample (median -0.004 sample) |
| per-burst decision-directed timing, +/-1.5 samples | 3, and 1 new wrong symbol | 1 exact frame lost on that call, and 1 on the weak handheld |
| per-burst decision-directed scale and offset | 2 | nothing on the clean calls. On the weak handheld, 7 symbols fixed and 10 new ones, 2 exact frames lost |
| RRC alpha 0.2, span 8, 161 taps, in place of the boxcar, 101-tap kept | 12 | no exact frame lost on the clean calls. On the weak handheld, 33 of 37 wrong symbols fixed and 3 previously exact frames lost |
| the 101-tap low-pass removed, boxcar kept | 4 | 118 previously exact frames lost on that call |

The RRC is the one that removes every one of those 12 symbols, and it is
the filter the modulator already uses. The 101-tap stays: dropping it is a
loss on the weaker audio. The SYNC level fit and the integer sync sample
are not the defect. On synthetic calls the same swap cuts the bit errors to
from 207 to 267 down to 28 to 35 bit errors in 720 frames at 12 dB and to 0 to 2 at
14 and 18 dB, with the squelch applied before the filter (the table in
[Reading weak and cut calls](#reading-weak-and-cut-calls-recover-and-matched)).

Voice-frame FEC was not built. Two guessed layouts for the 72-bit AMBE+2
frame, an extended Golay(24,12) and a Golay(23,12) with a constant
syndrome, over the deinterleaves that were tried (identity, reversal, byte
reversal, stride 3, and every multiplicative step coprime to 72), matched
none of 666 logged frames: no 24-bit window is a codeword on every frame,
and no 23-bit window has one syndrome on every frame. It is still not known
whether the logger writes corrected frames or raw ones. A correction toward
a codeword would move toward the file only for a code the file obeys, and
that code was not found.

## 10. What the documents did not give

- Occupied/emission bandwidth number beyond "12.5 kHz carrier" + RRC shape;
  no adjacent-channel or mask figures in the extracted text
  (points to EN 300 113/390) [Part1, 10.1.2/10.2.2.2, p.112-113].
- Vocoder bit layout and rate adaptation (only the 216-bit/60 ms socket and
  a 20 ms example) [Part1, 6.1, p.59].
- Any SDR sample rate, filter taps, or demodulator design guidance.
- Privacy/encryption algorithms, key lengths, or IV transport in the ETSI
  parts (explicitly out of scope) [Part1, Table 6.1, p.61]
  [Part4, 7.2.30 notes, p.190].
- Tier III TSCC broadcast parameter and CSBK opcode details beyond the
  ALOHA/AHOY/GRANT examples (full tables live in Part 4 clauses 6-7).
- Vendor extensions (Motorola IPSC/XNL, Hytera XPT) - DMRlink README covers
  only the IPSC bridge side [dmrlink_README.txt, PURPOSE/METHOD].

## From memory, not checked

- Practical chain used in the field: SDR front end (e.g. SDR++, SDR#, GNU
  Radio, rtl-sdr-class dongle or similar) FM/4FSK discriminator or coherent
  demod at 48-96 kHz complex, symbol timing recovery at 4800 sym/s, SYNC
  correlation per Table 9.2, then DSD-FME or equivalent for LC/CSBK display
  and AMBE+2 synthesis.
- Outbound DMR is a continuous 4FSK carrier with a 30 ms slot cadence and a
  CACH in the gap. That stream is built, and the CACH was checked on real
  repeater clips, in
  [The repeater stream and the CACH](#the-repeater-stream-and-the-cach).
  Inbound is 27.5 ms bursts with short guards.
- Amateur talkgroup/ID databases (e.g. RadioID/BrandMeister) are handy label
  sources but are outside the fetched texts; do not treat them as protocol
  truth.
