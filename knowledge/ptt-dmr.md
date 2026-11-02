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
BPTC(196,96), CSBK and LC contents, FID, addresses.

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
- Expect outbound DMR to look like a continuous 4FSK carrier with 30 ms
  slot cadence; inbound like 27.5 ms bursts with short guards; idle BS sends
  Idle bursts + CACH rather than going quiet.
- Amateur talkgroup/ID databases (e.g. RadioID/BrandMeister) are handy label
  sources but are outside the fetched texts; do not treat them as protocol
  truth.
