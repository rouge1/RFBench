# NXDN push-to-talk (NEXEDGE / IDAS) - SDR bench note

Source set: `docs/ptt/nxdn/SOURCES.md` lists the PDFs and their
`text/` conversions plus `dsd-fme_README.txt`. The Part 1 spec PDFs
here are copies hosted on a personal amateur-radio site, not fetched
from the NXDN Forum guest form; verify version numbers against the
Forum copy before citing one [SOURCES.md, Access note].
Primary specs below are Part 1-A (Type-C/common air interface,
v0103), 1-B (basic operation, v0103), 1-C (Type-C trunking, v0103),
1-D (security, v0103), 1-E (Type-D air interface, v0101), 1-F
(Type-D trunking, v0100). Secondary: Forum overview Rev 1.1 (2014),
Forum white paper Rev 04 (2013), Kenwood NXR-5700/5800 function
reference Ver 5.21 (conventional functions only).

## 1. What it is

- NXDN is a narrowband digital land-mobile protocol, "6.25 kHz and
  12.5 kHz FDMA technology", from a joint Icom / JVC KENWOOD
  alliance [nxdn_forum_overview_2014.txt, sec 1.1/Q&A].
- Kenwood sells it as NEXEDGE; Icom sells it as IDAS. The Kenwood
  repeater manual says NXDN is "a very narrowband protocol employing
  6.25 kHz/12.5 kHz FDMA technology" from that alliance, and
  "NEXEDGE is a generic term to describe the KENWOOD proprietary
  digital communications system which complies with NXDN common air
  interface specifications" [kenwood_nxr5700_5800_function_ref_ver521.txt, About NXDN / About NEXEDGE].
- Users: business/industry and public-safety land-mobile operators;
  the air interface covers trunked repeater sites (TRS),
  conventional repeater sites (CRS), subscriber units (SU), and
  direct subscriber-to-subscriber mode [nxdn_ts_1-a_v0103_qsl.txt, sec 1.1/2.1].
- Two trunking variants exist in one standards suite: Type-C
  (centralized, control-channel based) and Type-D (distributed
  logic, no control channel; all channels can carry traffic)
  [nxdn_forum_overview_2014.txt, sec 1.3/NXDN Trunking].
  Type-D is the IDAS side [dsd-fme_README.txt, credits line].
- PTT style: immediate connection by PTT switch; the layer model
  does not follow OSI because call connection is PTT-driven
  [nxdn_ts_1-a_v0103_qsl.txt, sec 2.4 Table 2.4-1].

## 2. RF parameters

| Item | 6.25 kHz mode | 12.5 kHz mode |
|---|---|---|
| Access | FDMA trunked; SCPC conventional [nxdn_ts_1-a_v0103_qsl.txt, Table 2.3-1] | Same row, second column |
| Channel spacing | 6.25 kHz [nxdn_ts_1-a_v0103_qsl.txt, Table 2.3-1] | 12.5 kHz |
| Transmission rate | 4800 bps [nxdn_ts_1-a_v0103_qsl.txt, Table 2.3-1] | 9600 bps |
| Symbol rate | 2400 symbol/s [nxdn_ts_1-a_v0103_qsl.txt, Table 2.3-1] | 4800 symbol/s |
| Speech codec rate | 3600 bps [nxdn_ts_1-a_v0103_qsl.txt, Table 2.3-1] | 3600 bps (7200 bps option) |
| Modulation | Nyquist 4-level FSK [nxdn_ts_1-a_v0103_qsl.txt, sec 2.3/3.2] | Same |
| Symbol mapping (dibit -> symbol -> deviation) | 01 -> +3 -> +1050 Hz; 00 -> +1 -> +350 Hz; 10 -> -1 -> -350 Hz; 11 -> -3 -> -1050 Hz [nxdn_ts_1-a_v0103_qsl.txt, Table 3.3-1] | 01 -> +3 -> +2400 Hz; 00 -> +1 -> +800 Hz; 10 -> -1 -> -800 Hz; 11 -> -3 -> -2400 Hz |
| Baseband filters | Tx: root-raised-cosine H(f) x sinc P(f), T = 416.7 us, roll-off alpha = 0.2 [nxdn_ts_1-a_v0103_qsl.txt, sec 3.4] | Same formulas, T = 208.3 us, alpha = 0.2 |
| Rx filter | Root-raised-cosine H(f) x inverse-sinc D(f), linear phase [nxdn_ts_1-a_v0103_qsl.txt, sec 3.5] | Same, T = 208.3 us |
| Reference deviation test tone | 4/pi x +/-1050 = +/-1337 Hz [nxdn_ts_1-a_v0103_qsl.txt, sec 3.6] | 4/pi x +/-2400 = +/-3056 Hz |

- Bands named: requirements for 6.25 kHz efficiency under FCC Part 90
  for bands under 512 MHz (mainly 150 and 450 MHz), plus 12.5 kHz
  efficiency including the 800 MHz band
  [nxdn_ts_1-a_v0103_qsl.txt, sec 1.1]. Type-D (Part 1-E) text
  repeats 6.25 kHz efficiency mainly in 150 and 450 MHz bands
  [nxdn_ts_1-e_v0101_qsl.txt, sec 1.1].
- Duplex: subscriber Type values list FDMA Simplex and FDMA Duplex
  as access methods a SU uses; Voice/Data Call Option has a Duplex
  bit (0 = half-duplex/SU simplex, 1 = duplex)
  [nxdn_ts_1-a_v0103_qsl.txt, Tables 6.5-39/6.5-9/6.5-10].
- The Forum white paper gives the same 4800 bps / 4-level FSK /
  1050 Hz deviation mapping for the 6.25 kHz mode
  [nxdn_forum_whitepaper_2013.txt, symbol-mapping figure].
- Occupied-bandwidth mask numbers and Tx power limits are not in
  the excerpts read; see traps.

## 3. Frames, bursts, slots

There are no TDMA slots; one RF carrier carries one 384-bit frame.

| RF channel | Use | Frame time | Frame contents (bits) |
|---|---|---|---|
| RCCH inbound | Trunked control, SU -> TC | 80 ms at 4800 bps; 40 ms at 9600 bps [nxdn_ts_1-a_v0103_qsl.txt, sec 4.3.1] | FSW 20 + LICH 16 + CAC 252 + Guard 96 = 384 [nxdn_ts_1-a_v0103_qsl.txt, Fig 4.6-2] |
| RCCH outbound | Trunked control, TC -> SU | Same | FSW 20 + LICH 16 + CAC 300 + collision-control E 24 + Post 24 = 384 [nxdn_ts_1-a_v0103_qsl.txt, Fig 4.6-2] |
| RTCH / RDCH voice | Traffic/direct voice | Same | FSW 20 + LICH 16 + SACCH 60 + voice/signalling payload 288 = 384 [nxdn_ts_1-a_v0103_qsl.txt, Fig 4.4-2/4.4-3/4.6-2] |
| RTCH / RDCH data | Traffic/direct data | Same | FSW 20 + LICH 16 + UDCH-or-FACCH2 348 = 384 [nxdn_ts_1-a_v0103_qsl.txt, Fig 4.4-4/4.4-5] |
| Type-D RTCH2 | IDAS link setup / voice / data | 80 ms, 384 bit frames [nxdn_ts_1-e_v0101_qsl.txt, sec 4.4] | P + FSW + LICH + SCCH + FACCH1 + Guard; or voice: + 4x VCH 72; or data: + UDCH2/FACCH3 [nxdn_ts_1-e_v0101_qsl.txt, sec 4.4] |

Functional channels:

| Channel | Expansion | Role |
|---|---|---|
| BCCH | Broadcast Control Channel | Outbound RCCH broadcasts (site/system info) [nxdn_ts_1-a_v0103_qsl.txt, sec 6.2.2.1] |
| CCCH | Common Control Channel | Control signalling both directions [nxdn_ts_1-a_v0103_qsl.txt, sec 6.2.2] |
| UPCH | User Packet Channel | Short data on RCCH, multi-frame [nxdn_ts_1-a_v0103_qsl.txt, sec 6.2.2] |
| CAC | Common Access Channel | Coded container on RCCH (BCCH/CCCH/UPCH inside) [nxdn_ts_1-a_v0103_qsl.txt, sec 4.5.1] |
| SACCH | Slow Associated Control Channel | Per-frame call control alongside voice, 9-octet capacity [nxdn_ts_1-a_v0103_qsl.txt, sec 6.2.3.1] |
| FACCH1 | Fast Associated Control Channel 1 | Steals VCH pairs for signalling during voice, 10-octet capacity [nxdn_ts_1-a_v0103_qsl.txt, sec 6.2.3.2] |
| UDCH / FACCH2 | User Data Channel / FACCH 2 | Data call payload / end-of-data signalling, 22-octet capacity each [nxdn_ts_1-a_v0103_qsl.txt, sec 6.2.3.3/6.2.3.4] |
| VCH | Voice Channel | Voice-coding data [nxdn_ts_1-a_v0103_qsl.txt, sec 4.2.2] |
| LICH | Link Information Channel | Per-frame RF/functional-channel ID + steal flags + direction [nxdn_ts_1-a_v0103_qsl.txt, sec 5.2.1] |
| SCCH (Type-D) | Signalling Control Channel | Type-D RTCH2 signalling slot [nxdn_ts_1-e_v0101_qsl.txt, sec 4.4] |

Sync and training words (Type-C values; Type-D Part 1-E repeats them):

| Word | Symbols (transmit order) | Hex printed in spec | Notes |
|---|---|---|---|
| Frame Sync Word (FSW) | -3, +1, -3, +3, -3, -3, +3, +3, -1, +3; 10 symbols / 20 bits [nxdn_ts_1-a_v0103_qsl.txt, Table 4.4-2] | CDF59 [nxdn_ts_1-a_v0103_qsl.txt, Table 4.4-2] | Same table in Type-D [nxdn_ts_1-e_v0101_qsl.txt, Table 4.4-2] |
| Preamble | 24 bits minimum (longer allowed); repeats +3,+3,-3,-3; tail 12 symbols: +3,+3,+3 then -3,+3,-3,+3,+3,-3,-3,-3,+3 [nxdn_ts_1-a_v0103_qsl.txt, Table 4.4-1] | 5775FD [nxdn_ts_1-a_v0103_qsl.txt, Table 4.4-1] | Sent at start of RDCH transmission for initial sync [nxdn_ts_1-a_v0103_qsl.txt, sec 4.4.3] |
| Post field | First 3 symbols +3,+3,+3; last 9 symbols -3,+3,-3,+3,+3,-3,-3,-3,+3 (3 + 9 symbols = 6 + 18 bits) [nxdn_ts_1-a_v0103_qsl.txt, Table 4.4-3] | Not printed as hex | Receiver can combine last 9 post symbols with FSW for sync [nxdn_ts_1-a_v0103_qsl.txt, sec 4.4.5] |

LICH coding:

| Item | Value |
|---|---|
| LICH size | 7 control bits + 1 even parity over the 4 MSB; no FEC, no interleave [nxdn_ts_1-a_v0103_qsl.txt, sec 4.5.3] |
| Dibit mapping | 0 -> 01 (+3); 1 -> 11 (-3); 8 bits become 8 dibits = 16 on-air bits [nxdn_ts_1-a_v0103_qsl.txt, Fig 4.5-7] |
| Bit 6-5 | RF channel: 00 RCCH, 01 RTCH, 10 RDCH, 11 RTCH_C composite [nxdn_ts_1-a_v0103_qsl.txt, Table 5.2-1] |
| Bit 4-3 | CAC type on RCCH, or USC type on traffic (00 SACCH non-superframe, 01 UDCH, 10 SACCH superframe, 11 SACCH-superframe/Idle) [nxdn_ts_1-a_v0103_qsl.txt, Table 5.2-1] |
| Bit 2-1 | Data flag on RCCH; steal flag on traffic (11 no steal, 10 second-half FACCH1, 01 first-half FACCH1, 00 both FACCH1, or FACCH2 for data) [nxdn_ts_1-a_v0103_qsl.txt, Table 5.2-1] |
| Bit 0 | Direction: 0 inbound, 1 outbound [nxdn_ts_1-a_v0103_qsl.txt, Table 5.2-1] |

Layer-3 framing on traffic channels:

| Container | Capacity | SR + message split |
|---|---|---|
| SACCH non-superframe | 1 message, 9 octets [nxdn_ts_1-a_v0103_qsl.txt, Fig 6.2-6] | 8-bit SR + 18-bit message [nxdn_ts_1-a_v0103_qsl.txt, sec 6.3.3] |
| SACCH superframe | 72-bit message over 4 frames [nxdn_ts_1-a_v0103_qsl.txt, sec 6.3.3] | 72 bits cut into 4x 18 bits, each with its own SR; SR Structure field = 11 head ... 00 tail [nxdn_ts_1-a_v0103_qsl.txt, Tables 6.3-2/Fig 6.3-3] |
| FACCH1 | 10 octets [nxdn_ts_1-a_v0103_qsl.txt, Fig 6.2-7] | No SR added (exception to the SR rule) [nxdn_ts_1-a_v0103_qsl.txt, sec 6.3.3] |
| UDCH / FACCH2 | 22 octets [nxdn_ts_1-a_v0103_qsl.txt, Fig 6.2-8/6.2-9] | Header format first block, User-Data format after [nxdn_ts_1-a_v0103_qsl.txt, sec 6.4.1.3] |
| RCCH outbound CAC | 18 octets: dual-message (2x 9-octet) or single-message format; UPCH single only [nxdn_ts_1-a_v0103_qsl.txt, sec 6.2.2.1] | Structure field: bit1 = head-of-superframe, bit0 = single/dual [nxdn_ts_1-a_v0103_qsl.txt, Table 6.3-1] |
| RCCH inbound CAC | Short 12 octets, Long 16 octets; UPCH uses Long [nxdn_ts_1-a_v0103_qsl.txt, sec 6.2.2.2] | Same Structure use where applicable |

SR carries an 8-bit field: 2-bit Structure + 6-bit RAN
[ nxdn_ts_1-a_v0103_qsl.txt, Fig 6.3-1/6.3-2].

## 4. PTT behaviour, addressing, call setup/teardown

Conventional (RDCH) clear-voice PTT sequence at 4800 bps/EHR
(9600/EFR differs only in VCH count; 9600/EHR steals whole
even superframe frames - see below)
[ nxdn_ts_1-b_v0103_qsl.txt, sec 4.1.3/Fig 4.1-7]:

| Step | Event | Channels sent |
|---|---|---|
| 1-2 | User presses PTT; SU starts TX | - |
| 3 | Preamble | Preamble then LICH = RDCH / single-SACCH / both FACCH1 / inbound |
| 4 (header frame) | First frame, non-superframe | SACCH: IDLE; FACCH1 x2: VCALL (at least one FACCH1 carries VCALL); SR = "00" |
| 5-8 | Talk frames, superframe SACCH | SACCH: VCALL quarter 1/4, 2/4, 3/4, 4/4 with SR 11,10,01,00; VCH 1-4: voice; LICH = RDCH / multi-SACCH / VCH-only / inbound |
| 9 | Still talking (PTT released in example here) | Repeats VCALL 1/4 cycle |
| 10 (terminator) | Last frame, non-superframe | SACCH: IDLE; FACCH1 x2: TX_REL; receiver mutes/terminates on TX_REL |
| 11 | TX ends | - |

Notes from the same procedure:

- Header FACCH1 lets the receiver decide from frame 1 and unmute
  if VCALL matches; late entry without frame 1 must collect all 4
  SACCH quarters in order using SR [nxdn_ts_1-b_v0103_qsl.txt, sec 4.1.6].
- TX_REL may follow immediately after PTT release even mid-superframe;
  remaining vocoder data may be sent first
  [nxdn_ts_1-b_v0103_qsl.txt, sec 4.1.3 steps 10-11].
- DES/AES voice adds a third message, VCALL_IV: header carries
  VCALL + VCALL_IV in the two FACCH1s, then superframes alternate
  VCALL quarters and VCALL_IV quarters while PTT is held
  [nxdn_ts_1-b_v0103_qsl.txt, sec 4.1.5/Fig 4.1-8].
- Scramble-encrypted voice uses exactly the clear-voice channel
  plan [nxdn_ts_1-b_v0103_qsl.txt, sec 4.1.4].
- Status during voice steals one VCH pair into FACCH1 carrying
  STAT_REQ; at 9600/EHR dedicated FACCH1 frames exist so no VCH
  is lost [nxdn_ts_1-b_v0103_qsl.txt, sec 4.1.8.1].
- Simultaneous data during voice replaces VCHs with FACCH1s
  carrying SDCALL_REQ header + user-data blocks, then ends with
  IDLE + TX_REL [nxdn_ts_1-b_v0103_qsl.txt, sec 4.1.9.1].

VCH/FACCH packing:

| Mode | Packing per 384-bit frame |
|---|---|
| EHR at 4800 or 9600 | 4x VCH of 72 bits (= 80 ms voice); any VCH pair can be stolen by one FACCH1 of 144 bits [nxdn_ts_1-b_v0103_qsl.txt, sec 4.1.2/Fig 4.1-4] |
| EFR at 9600 only | 2x VCH of 144 bits; any VCH can be replaced by one FACCH1 [nxdn_ts_1-b_v0103_qsl.txt, sec 4.1.2/Fig 4.1-4] |
| 9600 bps/EHR superframe | Alternates VCH frame / FACCH1 frame (VCH, FACCH1, VCH, FACCH1); even frames fixed FACCH1; idle FACCH1 frames allow intermittent TX shutdown [nxdn_ts_1-b_v0103_qsl.txt, sec 4.1.2.1/Fig 4.1-5/4.1-6] |

Addressing and squelch:

- RAN is 6 bits. Conventional: 00 = unmute on any RAN (carrier
  squelch); 01-3F user definable [nxdn_ts_1-a_v0103_qsl.txt, Table 6.3-3].
  Trunked: RAN = (Site Code) mod 64
  [nxdn_ts_1-a_v0103_qsl.txt, Table 6.3-4]. RAN 00 in a receiver
  means accept any RAN [nxdn_ts_1-b_v0103_qsl.txt, sec 4.1.7.1].
- Unit ID 16 bits: 0000 null, 0001-FFEF standard (incl. visitor and
  temporary), FFF0 TRS TC, FFF1 TRS PSTN, FFF5 CR PSTN, FFFF all
  units [nxdn_ts_1-a_v0103_qsl.txt, Table 6.5-2].
- Group ID 16 bits: 0000 null, 0001-FFEF standard, FFF0 reserved,
  FFFF all groups [nxdn_ts_1-a_v0103_qsl.txt, Table 6.5-3].
- VCALL (octets 1-7): CC Option, Call Type + Voice Call Option,
  Source Unit ID, Destination Group-or-Unit ID, Cipher Type + Key
  ID [nxdn_ts_1-a_v0103_qsl.txt, Fig 6.4-1]. Per call type the ID
  octets hold caller/group/called/PSTN IDs
  [nxdn_ts_1-a_v0103_qsl.txt, Table 6.4-5].
- Call Type (3 bits): 000 broadcast group (trunk only), 001
  conference group, 010 unspecified (used only in TX_REL), 100
  individual, 110 interconnect/PSTN, 111 speed-dial; 011/101
  reserved [nxdn_ts_1-a_v0103_qsl.txt, Table 6.5-8].
- Voice Call Option: Duplex bit + Transmission Mode 000 =
  4800/EHR, 010 = 9600/EHR, 011 = 9600/EFR
  [nxdn_ts_1-a_v0103_qsl.txt, Table 6.5-9].
- Receiver unmute table (Part 1-B Table 4.1-1): RAN-only needs RAN
  (+ cipher/key if encrypted); group call needs RAN + Group ID;
  individual needs RAN + Destination Unit ID; cipher handling per
  mode, with scramble Key ID 00 meaning use the preconfigured key
  [nxdn_ts_1-b_v0103_qsl.txt, sec 4.1.7].
- CC Option octet carries Emergency bit, Intra/Inter-system bit,
  and paging Priority [nxdn_ts_1-a_v0103_qsl.txt, Table 6.5-7].

Trunked (Type-C) call control:

- SU requests on inbound CCCH with VCALL_REQ / DCALL_REQ; TC
  pages with VCALL_REC_REQ / DCALL_REC_REQ; called SU answers;
  TC assigns channel with VCALL_ASSGN / DCALL_ASSGN and repeats
  it with VCALL_ASSGN_DUP for late entry
  [nxdn_ts_1-a_v0103_qsl.txt, Tables 6.4-3/6.4-1 and sec 6.4.1.25/6.4.1.26].
- Late entry is an explicit design point: the TC keeps announcing
  the in-use traffic channel (ASSGN_DUP recommended) so joiners
  find it [nxdn_ts_1-c_v0103_qsl.txt, basic-discipline section].
- Release: TX_REL (both directions, on FA1/FA2, type 00 1000)
  ends a traffic-channel transmission; TX_REL_EX (trunk outbound
  only, type 00 0111) adds an octet-7 TX flag where 0 = inhibited,
  1 = permitted; HEAD_DLY (type 00 1111) announces remaining idle
  frames before a valid data frame via 16-bit Delay Count
  [nxdn_ts_1-a_v0103_qsl.txt, sec 6.4.1.6/6.4.1.7/6.4.1.8 and Table 6.4-1].
- TX_REL format: CC Option, Call Type, Source Unit ID,
  Destination Group-or-Unit ID
  [nxdn_ts_1-a_v0103_qsl.txt, Fig 6.4-6].
- Random access on RCCH uses collision-control fields I/B
  (idle/busy), R/N (receive normal/error), P/D (proceed to
  continue/disconnect), plus PE parity-error count and CK checks;
  success needs R/N = R with PE within 2 bits of own CRC, then
  continuous frames while P/D = P
  [nxdn_ts_1-a_v0103_qsl.txt, sec 5.6.2.2].
- Repeater hold: after a SU ends voice, the conventional repeater
  keeps outbound alive for a Hold Time using unlimited
  non-superframe SACCH frames
  [nxdn_ts_1-b_v0103_qsl.txt, sec 4.1.1/Fig 4.1-3].

## 5. FEC, interleaving, scrambling, CRC

All convolutional codes below are K = 5, R = 1/2,
G1 = 1 + D3 + D4, G2 = 1 + D + D2 + D4, outputs read G1,G2
alternately, 4 zero tail bits appended first.

| Channel | CRC | Puncture matrix | Interleave |
|---|---|---|---|
| CAC outbound | 16-bit, X16+X12+X5+1 [nxdn_ts_1-a_v0103_qsl.txt, sec 4.5.1.1] | rows 1111111 / 1011101 [nxdn_ts_1-a_v0103_qsl.txt, sec 4.5.1.1] | 155 info bits, depth 25, no cross-frame interleave [nxdn_ts_1-a_v0103_qsl.txt, sec 4.5.1.1] |
| CAC long inbound | 16-bit, same polynomial [nxdn_ts_1-a_v0103_qsl.txt, sec 4.5.1.2] | rows 1111111111111 / 0110001110111 [nxdn_ts_1-a_v0103_qsl.txt, sec 4.5.1.2] | 136 info bits, depth 21 [nxdn_ts_1-a_v0103_qsl.txt, sec 4.5.1.2] |
| CAC short inbound | 16-bit, same polynomial [nxdn_ts_1-a_v0103_qsl.txt, sec 4.5.1.3] | None [nxdn_ts_1-a_v0103_qsl.txt, sec 4.5.1.3] | 106 info bits, depth 21 [nxdn_ts_1-a_v0103_qsl.txt, sec 4.5.1.3] |
| SACCH | 6-bit, X6+X5+X2+X+1 [nxdn_ts_1-a_v0103_qsl.txt, sec 4.5.2.1] | rows 111111 / 110110 [nxdn_ts_1-a_v0103_qsl.txt, sec 4.5.2.1] | 26 info bits, depth 5 [nxdn_ts_1-a_v0103_qsl.txt, sec 4.5.2.1] |
| FACCH1 | 12-bit, X12+X11+X3+X2+X+1 [nxdn_ts_1-a_v0103_qsl.txt, sec 4.5.2.2] | rows 11 / 01 [nxdn_ts_1-a_v0103_qsl.txt, sec 4.5.2.2] | 80 info bits, depth 9, interleaved across the two FACCH1 halves [nxdn_ts_1-a_v0103_qsl.txt, sec 4.5.2.2] |
| UDCH / FACCH2 | 15-bit, X15+X14+X11+X10+X7+X6+X2+1 [nxdn_ts_1-a_v0103_qsl.txt, sec 4.5.2.3] | rows 1111111 / 1011101 [nxdn_ts_1-a_v0103_qsl.txt, sec 4.5.2.3] | 184 info bits, depth 29 [nxdn_ts_1-a_v0103_qsl.txt, sec 4.5.2.3] |
| Type-D SCCH | 7-bit, X7+X3+1 [nxdn_ts_1-e_v0101_qsl.txt, sec 4.5.1.1] | matrix rows 111111 / 110110 [nxdn_ts_1-e_v0101_qsl.txt, sec 4.5.1.1] | 25 info bits, depth 5 [nxdn_ts_1-e_v0101_qsl.txt, sec 4.5.1.1] |
| Packet Message CRC | 32-bit over user data of one packet: X32+X26+X23+X22+X16+X12+X11+X10+X8+X7+X5+X4+X2+X1+1, shift register default all-1 [nxdn_ts_1-a_v0103_qsl.txt, sec 9.3] | - | - |

Scrambler:

- Scrambles everything except FSW, Guard, Preamble, Post
  [nxdn_ts_1-a_v0103_qsl.txt, sec 4.6].
- Per-symbol multiply by PN mapped 0 -> +1, 1 -> -1; LFSR
  re-initialized every frame to S8..S0 = 0,1,1,1,0,0,1,0,0;
  generator X9+X4+1 [nxdn_ts_1-a_v0103_qsl.txt, sec 4.6/Table 4.6-1].

## 6. Voice codec

- AMBE+2 (DVSI). Rates: EHR 3600 bps channel rate, EFR 7200 bps
  channel rate [nxdn_ts_1-a_v0103_qsl.txt, sec 7.1].
- EHR: 49-bit voice frame every 20 ms at 2450 bps net, + 23
  redundancy bits = 72-bit VCH. EFR: 88-bit voice frame every
  20 ms at 4400 bps net, + 56 redundancy bits = 144-bit VCH
  [nxdn_ts_1-a_v0103_qsl.txt, sec 7.1].
- Forum overview agrees: vocoder AMBE+2; codec rate 3600 bps at
  4800 bps air rate, 7200 bps at 9600 bps air rate
  [nxdn_forum_overview_2014.txt, sec 1.3 table].
- Spec details beyond that are DVSI-proprietary; the document
  points to DVSI and gives only test vectors, e.g. EHR tone
  (1031 Hz, -22 dBm0): 49-bit FEE2121212100..., 72-bit
  CEA8FE83ACC458200A...; EHR silence: 49-bit F801A99F8CE08...,
  72-bit B9E881526173002A6B...
  [nxdn_ts_1-a_v0103_qsl.txt, sec 7.2.1].
- Kenwood repeater manual notes its firmware carries AMBE+2
  under DVSI license
  [kenwood_nxr5700_5800_function_ref_ver521.txt, firmware note].

## 7. Encryption and authentication

- Scope: only user information (voice, user data) is encrypted;
  control info such as Group ID is not
  [nxdn_ts_1-d_v0103_qsl.txt, security-services section].
- Cipher Type (2 bits): 00 non-ciphered, 01 scramble, 10 DES,
  11 AES [nxdn_ts_1-a_v0103_qsl.txt, Table 6.5-24]. Key ID (6
  bits): 00 = default/unspecified, 01-3F user definable
  [nxdn_ts_1-a_v0103_qsl.txt, Table 6.5-25].
- Scramble encryption: bit-wise XOR with a PN from a 15-stage
  register, 32767 keys excluding all-zero
  [nxdn_ts_1-d_v0103_qsl.txt, scramble section]. Forum overview
  matches: "Digital Scrambling Yes (15-bit/32,000 keys)"
  [nxdn_forum_overview_2014.txt, sec 1.3 table].
- DES: 64-bit block, 56-bit key, OFB mode adopted so one air bit
  error does not multiply on decrypt
  [nxdn_ts_1-d_v0103_qsl.txt, sec 5.3.2/5.3.2.1]. 64-bit IV sent
  via VCALL_IV / SDCALL_IV
  [nxdn_ts_1-d_v0103_qsl.txt, sec 5.3.2.2; nxdn_ts_1-a_v0103_qsl.txt, sec 6.4.1.2].
- AES: 128-bit block, 256-bit key, OFB mode; 128-bit IV
  [nxdn_ts_1-d_v0103_qsl.txt, sec 5.3.3].
- Encryption sits above layer 3, end to end between SUs, applied
  between voice coding and FEC coding on TX (reverse on RX)
  [nxdn_ts_1-d_v0103_qsl.txt, encryption-outline section].
- Authentication: 48-bit factory ESN per unit
  [nxdn_ts_1-a_v0103_qsl.txt, sec 6.5.5]; SU computes an
  authentication value from its ESN for the network to verify
  eligibility [nxdn_ts_1-d_v0103_qsl.txt, authentication section].
  No key values are in these documents.

## 8. Detecting and decoding with SDR

Only decoder fact in the texts: the DSD-FME README credits
remote testing of "NXDN Type-C, 'Idas' Type-D, and XPT decoding
and trunking" [dsd-fme_README.txt, credits paragraph]. Use that
build's NXDN/IDAS modes and its example/trunking notes; the
README points to its examples folder for usage
[dsd-fme_README.txt, Information section]. No other SDR program
is named in the fetched texts (the OP25 and SDRTrunk READMEs
were not kept because they do not mention NXDN [SOURCES.md]).

What to look for on air (all from the specs above, not from a
capture guide):

- Narrow 4FSK, 6.25 kHz raster at 2400 sym/s or 12.5 kHz raster
  at 4800 sym/s; continuous repeater output vs. short PTT bursts
  with preamble on RDCH.
- Correlate the 10-symbol FSW (-3,+1,-3,+3,-3,-3,+3,+3,-1,+3)
  every 80 ms (4800) or 40 ms (9600); each frame is 384 bits.
- Read LICH right after FSW: RF type, SACCH/UDCH/steal flags,
  direction. Then read SR at each SACCH: Structure quarter +
  6-bit RAN, which repeats every frame and identifies the
  site/conventional access code.
- A PTT start shows: preamble (direct), then a header frame with
  SACCH = IDLE and FACCH1 = VCALL (clear/scramble) or
  VCALL + VCALL_IV (DES/AES); PTT end shows SACCH = IDLE and
  FACCH1/FACCH2 = TX_REL.
- Trunked control: persistent RCCH outbound with CAC + E + Post
  fields; inbound bursts use shorter CAC + guard; watch
  VCALL_ASSGN / ASSGN_DUP to find voice channels, then follow
  late entry the same way.

## 9. Traps

- 6.25 kHz vs 12.5 kHz is an air-rate choice (4800 vs 9600),
  not a different sync word: FSW, preamble tail, and post tail
  bit patterns are identical in the Type-C and Type-D texts.
- RAN is not a NAC and not a talkgroup: 6-bit access code on
  every frame; 00 means carrier squelch. Group/individual
  selection is a separate 16-bit ID in VCALL.
- FACCH1 has no SR octets while SACCH does; a decoder that
  expects SR on every block will misalign stolen halves.
- 9600/EHR superframe alternates whole VCH and FACCH1 frames,
  and idle FACCH1 frames may go intermittent/dark; do not
  mistake a scheduled idle frame for call end - only TX_REL
  (or TX_REL_EX on trunk outbound) ends it.
- DES/AES superframes alternate VCALL and VCALL_IV quarters;
  a 4-frame assembler that assumes VCALL-only will mix IV
  octets into IDs.
- Scrambler (LFSR per frame, X9+X4+1) is not the "scramble
  encryption" (15-stage PN, keyed); both run, at different
  layers.
- Type-D has its own channel names (RTCH2, SCCH, UDCH2/FACCH3)
  and 7-bit CRC on SCCH; Type-C tables do not apply blindly.
- Kenwood NXR manual here covers conventional repeater operation
  (RAN decode/encode lists, Group/Unit ID lists, conventional IP
  network); NEXEDGE trunking is in a separate reference not
  fetched [kenwood_nxr5700_5800_function_ref_ver521.txt, scope pages].

## 10. What the documents did not give

- Occupied-bandwidth / emission-mask numbers, Tx power, and
  frequency tolerance (only filter shapes and symbol timing).
- VCH FEC generator details (only net/gross rates and test
  vectors; full AMBE+2 is DVSI-proprietary).
- Type-D full message/operation coverage (only Part 1-E/F were
  fetched; Part 2 suite and DVSI vocoder spec missing
  [SOURCES.md, Not found]).
- Kenwood NEXEDGE trunking and Icom IDAS air-interface
  specifics beyond the Type-D CAI.
- Any SDR capture guide, sample rate, or discriminator/baseband
  procedure.

## From memory, not checked

- For an SDR front end, reviewers typically capture 2-4x the
  symbol rate of complex baseband (e.g. ~12.5-25 ksps for the
  2400-sym/s mode, ~24-48 ksps for 4800-sym/s) or a wider FM
  voice channel (e.g. 48-96 kHz) and perform 4FSK symbol-
  clock recovery offline; verify against the filter and
  deviation numbers above, which are the only checked facts.
- Decoder names beyond DSD-FME (e.g. DSD+, SDRTrunk, OP25,
  inspectrumbaudline-style constellation/FM tools) are not in
  these texts and are listed here only as starting points, not
  as verified NXDN support claims.
- If a bit-error pattern looks periodic every 384 bits, suspect
  the per-frame scrambler init was missed rather than weak
  signal; the default register value above is the checked fact.
