# APCO Project 25 (P25) Push-to-Talk - SDR Bench Note

Scope: what an engineer needs to build or decode P25 Phase 1 (FDMA) and Phase 2 (TDMA) voice push-to-talk. All facts below come from the texts listed in `docs/ptt/p25/SOURCES.md`. Citations are `[file, location]`. Anything not in those texts is under "From memory, not checked" or "What the documents did not give".

Source caution: the set contains no normative TIA-102 text (no TIA-102.AABF, .BAAA, .BABA, .BBAB, .BBAC, .AABB). Protocol detail here is from vendor application notes, government user needs, a security paper, wikis, and decoder READMEs [SOURCES.md, lines 7-9]. The standards indexes list document numbers and dates only [p25_tia_standards_accepted_2025.txt, pp.1-2].

## 1. What the system is and who uses it

- P25 (APCO-25, Project 25) is a set of TIA-102 standards for digital narrowband land mobile radio for public safety, public service, utilities, airports, transit, petroleum and chemical users [kentucky_p25_spun_2020.txt, Introduction / p.2-3] [motorola_p25_tdma_white_paper.txt, p.1].
- Goal: interoperable digital two-way voice and data, spectrum efficiency, competition among vendors, migration from analog FM [kentucky_p25_spun_2020.txt, p.3].
- P25 radios also do analog FM for backwards compatibility; legacy analog radios cannot decode P25 [wavecom_advanced_protocol_apco25.txt, p.1] [blaze_p25sec_usenix2011.txt, p.2].
- Users: police, fire, emergency medical, federal law enforcement and surveillance (examples named include DEA, FBI, Secret Service, ICE), plus state/local public safety [blaze_p25sec_usenix2011.txt, pp.12-13] [kentucky_p25_spun_2020.txt, p.1].
- Two deployments: Phase 1 (FDMA, conventional and trunked) and Phase 2 (TDMA trunked voice, FDMA control) [kentucky_p25_spun_2020.txt, Fig.5-6 / pp.14-15] [motorola_p25_tdma_white_paper.txt, p.2].
- Conventional = user-selected channel, repeater or direct; trunked = controller assigns a channel from a pool after a request on a control channel [kentucky_p25_spun_2020.txt, pp.13-14] [viavi_control_channel_logging_2975.txt, pp.1-2].
- P25 defines 8+ open interfaces; the Common Air Interface (CAI) between subscriber unit (SU) and RF Sub-System (RFSS) is the core radio interface [kentucky_p25_spun_2020.txt, pp.3-4,21].
- Other interfaces: Fixed Station Subsystem Interface (FSSI/FSI, Ef), Inter-RF Subsystem Interface (ISSI, G), Console Subsystem Interface (CSSI, Ec), Key Fill Interface (KFI), Inter-KMF Interface (IKI), Network Management (En), Telephone Interconnect (Et/TII), Packet Data Host, Mobile Data Peripheral [kentucky_p25_spun_2020.txt, Table 1-2 / pp.21-24].
- Consoles can monitor many talkgroups/channels and get high-priority access, regroup, interrupt, discreet listen, takeover [kentucky_p25_spun_2020.txt, pp.27-28].

## 2. RF: channels, modulation, rate, bands, duplex

| Item | Phase 1 FDMA | Phase 2 TDMA |
|---|---|---|
| Channel width | 12.5 kHz [wavecom_advanced_protocol_apco25.txt, p.1] [radioreference_wiki_p25.txt, lines 95-97] | 12.5 kHz containing 2 slots, i.e. 2 x 6.25 kHz-equivalent [radioreference_wiki_p25.txt, lines 103-105] [viavi_p25_phase2_tdma.txt, Table 1 / p.2] |
| Symbol rate | 4800 symbols/s [radioreference_wiki_p25.txt, line 85] [blaze_p25sec_usenix2011.txt, p.3] | 6000 symbols/s [radioreference_wiki_p25.txt, line 85] [viavi_p25_phase2_tdma.txt, Table 1 / p.2] |
| Bits per symbol | 2 (4-level) [blaze_p25sec_usenix2011.txt, p.3] | 2 (4-level / quaternary) [viavi_p25_phase2_tdma.txt, pp.3-4] |
| Gross bit rate | 9600 bps [wavecom_advanced_protocol_apco25.txt, p.1] | 12000 bps [wavecom_advanced_protocol_apco25.txt, p.1] [viavi_p25_phase2_tdma.txt, Table 1] |
| Control channel rate | 4800 baud / 9600 bps [radioreference_wiki_p25.txt, lines 87-89] | control channel is still Phase 1 FDMA 4800 baud / 9600 bps; TDMA extensions only on traffic [kentucky_p25_spun_2020.txt, Fig.11 note / p.19] [motorola_p25_tdma_white_paper.txt, p.2] |
| Outbound (base to mobile) | C4FM or CQPSK/LSM [radioreference_wiki_p25.txt, lines 95-97] | H-DQPSK [viavi_p25_phase2_tdma.txt, Table 1 / p.2] |
| Inbound (mobile to base) | C4FM (subscriber always C4FM even if receiving CQPSK) [radioreference_wiki_p25.txt, lines 95-97] [sigidwiki_p25.txt, line 70] | H-CPM [viavi_p25_phase2_tdma.txt, Table 1 / p.2] |
| Vocoder paired | Full-rate IMBE [wavecom_advanced_protocol_apco25.txt, p.1] | Half-rate AMBE+2 at 3600 bps [viavi_p25_phase2_tdma.txt, Table 1] |
| Slot | n/a (continuous FDMA) | 30 ms slot, 320 info bits per slot, effective 5333.33 bps [viavi_p25_phase2_tdma.txt, Table 1] |

Modulation notes from the texts:

- Phase 1: 4-level signal, 2 bits per symbol at 4800 symbols/s [blaze_p25sec_usenix2011.txt, p.2]. C4FM is continuous 4-level FM; CQPSK is Compatible QPSK, also called Linear Simulcast Modulation (LSM) [sigidwiki_p25.txt, line 70]. Deviation at symbol time is the same for C4FM and CQPSK so FM receivers can still decode CQPSK [sigidwiki_p25.txt, line 70].
- CQPSK needs a linear amplifier and is only sent by fixed equipment (repeaters/sites); portables/mobiles send C4FM [sigidwiki_p25.txt, line 70].
- Simulcast uses CQPSK/LSM; older Motorola ASTRO "WIDE pulse" C4FM simulcast is not P25 compliant [radioreference_wiki_p25.txt, lines 95-97].
- Phase 2 outbound H-DQPSK is pi/4 DQPSK with symbol-to-phase map in Table 2 (00: pi/4, 01: 3pi/4, 10: -pi/4, 11: -3pi/4), 6000 shifts/s, raised-cosine filter with roll-off alpha = 1 [viavi_p25_phase2_tdma.txt, pp.3-4].
- Phase 2 inbound H-CPM is harmonized continuous phase modulation, constant envelope (no linear PA needed), modulation index 1/3, phase = 2*pi*1/3*quaternary-symbol; quaternary map 01:+3, 00:+1, 10:-1, 11:-3 with phases 2pi, 2pi/3, -2pi/3, -2pi [viavi_p25_phase2_tdma.txt, pp.4-5].
- At symbol time through integrate-and-dump, H-DQPSK takes 4 FM-deviation states (2250, 750, -750, -2250 Hz for 135, 45, -45, -135 deg) [viavi_p25_phase2_tdma.txt, Table 6 / p.8]; H-CPM takes 7 states (3000, 2000, 1000, 0, -1000, -2000, -3000 Hz for 180, 120, 60, 0, -60, -120, -180 deg) [viavi_p25_phase2_tdma.txt, Table 7 / p.8].
- Occupied bandwidth / emission designators seen on licenses [sigidwiki_p25.txt, lines 181-189]:

| Signal | Designators listed |
|---|---|
| Phase 1 C4FM | 8K10F1E, 8K10F1D, 8K10F1W |
| Phase 1 CQPSK | 8K70D1W, 9K70F1D, 9K70F1E, 5K76G1E (noted erroneous) |
| Phase 2 H-CPM | 8K10F7W, 8K10F1W, 8K10F1D, 8K10F1E |
| Phase 2 H-DQPSK | 9K80D7W |
| Motorola Wide-Pulse (non-P25) | 20K0F1E |

Frequency bands and duplex:

- Common ranges listed: 136-174, 380-512, 769-824, 851-869, 896-939 MHz; page header lists 136, 174, 380, 512, 769, 824, 851, 859, 896, 939 MHz with 136-939 MHz span [sigidwiki_p25.txt, lines 10-13, 175].
- VHF/UHF IDEN range called out as 136-172 MHz and 380-512 MHz for IDEN_UP_VU [viavi_advanced_p25_control_channel.txt, lines 341-343].
- Federal tactical use described as about 11 MHz in VHF plus 14 MHz in UHF, channels every 12.5 kHz [blaze_p25sec_usenix2011.txt, pp.13-14].
- Phase 2 discussed for meeting 6.25 kHz-equivalence rules in VHF/UHF and 700 MHz band plans [motorola_p25_tdma_white_paper.txt, p.1].
- Duplex: simplex (same frequency both directions), repeater (mobile TX on one frequency, repeater re-TX on second frequency), trunked (pool of pairs) [blaze_p25sec_usenix2011.txt, p.3]. SUs are simplex or half-duplex (not same time); repeaters are full-duplex (receive and transmit simultaneously) [kentucky_p25_spun_2020.txt, p.16]. Example duplex offset: 45 MHz in 800 MHz band [viavi_control_channel_logging_2975.txt, p.2].
- Transmit power caps quoted from TIA-102.CCAB via Viavi: mobile/control 30 W, portable 3 W; must not exceed type-accepted rating by more than 20 percent [viavi_p25_phase2_tdma.txt, p.11].
- Frequency-error limits (max, PPM) quoted from TIA-102.CCAB [viavi_p25_phase2_tdma.txt, Table 8 / p.11]: below 100 MHz 5.0 mobile / 2.5 base; 138-174 MHz 2.5 / 1.5; 406-512 MHz 2.0 / 0.5; 769-806 MHz 0.41/1.52 notes for AFC locked/not locked and 0.1 base; 806-869 MHz 1.5 / 0.15; 896-941 MHz 1.5 / 0.1.
- Symbol clock accuracy: 10 PPM (60 mHz at 6000 symbols/s); sensitivity test uses 1031 Hz pattern with 5 percent or less BER; example sensitivity -116 dBm Class A, -113 dBm Class B for mobile/portable/base [viavi_p25_phase2_tdma.txt, pp.10-11].

## 3. Frame, burst and slot structure

Voice message skeleton (Phase 1, conventional or trunked voice channel):

- Sequence: Header Data Unit (HDU), then alternating Logical Link Data Units LDU1 / LDU2, then Terminator Data Unit (TDU) [wavecom_advanced_protocol_apco25.txt, p.2] [blaze_p25sec_usenix2011.txt, Fig.2 / p.4].
- Two LDUs (one LDU1 + one LDU2) form a "superframe" of 360 ms [blaze_p25sec_usenix2011.txt, Fig.5-2 / p.4].
- All data units start with a frame sync word and a Network ID (NID) [wavecom_advanced_protocol_apco25.txt, p.2].
- Frame start: special 24-symbol (48-bit) frame sync pattern, then a 64-bit field holding 16 bits of information plus 48 bits of error correction [blaze_p25sec_usenix2011.txt, p.3].
- NID = 12-bit Network Access Code (NAC) + 4-bit Data Unit ID (DUID) [blaze_p25sec_usenix2011.txt, p.4].
- DUID values name the frame type: voice header, voice superframe pieces, voice trailer/terminator, data packet, trunked frame [blaze_p25sec_usenix2011.txt, p.3]. Full DUID table is not in the texts (see missing list).
- Status symbols: every 70 bits a 2-bit status symbol is inserted [wavecom_advanced_protocol_apco25.txt, p.2]; LDU diagrams show 24 status symbols with "2 bits after every 70 bits" [blaze_p25sec_usenix2011.txt, Figs.8-3/8-4 / p.4].
- Voice LDU length: 864 symbols (1728 bits) per LDU frame including headers/metadata/voice/status [blaze_p25sec_usenix2011.txt, p.4]. LDU1/LDU2 differ only in metadata carried [blaze_p25sec_usenix2011.txt, p.4].
- Observed on-air bytes (lab capture, not a normative table): Figure 8 labels "P25 FrameSync LDU NID" with hex `55 75 F5 FF 77 FF 29 3A B8 A4 ...` for a normal frame and `55 75 F5 FF 77 FF 01 20 75 85 ...` for a jammed NID case [blaze_p25sec_usenix2011.txt, p.12]. Treat as an example capture, not the sync constant.

Header Data Unit (voice header):

- Carries destination talkgroup ID (TGID), plus encryption fields: Message Indicator (MI, 72 bits wide but effectively 64), Algorithm ID (8 bits), Key ID (16 bits); clear calls set these to zero [blaze_p25sec_usenix2011.txt, p.4].
- Also "initializes link control functions and encryption keys" [wavecom_advanced_protocol_apco25.txt, p.2].

LDU1 vs LDU2 payload:

- Each voice LDU carries 9 x 144-bit IMBE voice subframes (each 20 ms, so 180 ms audio per LDU) plus headers/metadata and a little low-speed data [blaze_p25sec_usenix2011.txt, p.4] [wavecom_advanced_protocol_apco25.txt, p.2].
- LDU1 metadata: source unit ID (24-bit radio ID) plus either 24-bit destination unit ID (unit call) or 16-bit TGID (group call) [blaze_p25sec_usenix2011.txt, p.4].
- LDU2 metadata: fresh MI plus Algorithm ID and Key ID [blaze_p25sec_usenix2011.txt, p.4].
- Custom low-speed data: 2 x 16 bits at 88.89 bps can be inserted into LDU1/LDU2 [wavecom_advanced_protocol_apco25.txt, p.2].
- Link Control Word (LCW) is in every other LDU voice frame (i.e. the LDU1 frames); it carries the transmitter unit Link ID and can be optionally protected/encrypted via a flag [blaze_p25sec_usenix2011.txt, pp.6-7].

Terminator:

- Simple TDU = frame sync + NID only; more elaborate TDU adds a Link Control word; TDU may follow either LDU1 or LDU2 and ends the call; transmitter sends silence-filled LDU to complete the unit before the TDU [blaze_p25sec_usenix2011.txt, p.4].
- Wavecom calls the same unit "TDU terminates the voice message" [wavecom_advanced_protocol_apco25.txt, p.2].

Packet data units:

- PDU has arbitrary length: frame sync + NID + at least one Header Block (HB) + zero or more Data Blocks (DB); HB and last DB protected by CRC with ARQ on error [wavecom_advanced_protocol_apco25.txt, p.2].
- Data message is split into fragments, then packets, then data blocks protected by a trellis code and sent as one packet [wavecom_advanced_protocol_apco25.txt, p.2].

Trunking signaling blocks (control channel and embedded LC):

- Single TSBK: first 2 octets are LB (last block), P (protected), Opcode; next 8 octets arguments/message; final 2 octets TSBK CRC [viavi_advanced_p25_control_channel.txt, Fig.1 / p.5].
- Single TSBKs can be sent as 1, 2 or 3 consecutive blocks after one FS+NID; do not confuse stacked single TSBKs with multi-block messages [viavi_advanced_p25_control_channel.txt, p.4].
- Multi-block: header block says how many data blocks follow (1-3); confirmed header for data (needs ACK), unconfirmed/MBT or Alternative MBT header for trunking voice control; alternative header carries embedded opcodes and trunking parameters [viavi_control_channel_logging_2975.txt, pp.4-5] [viavi_advanced_p25_control_channel.txt, pp.5-6].
- Example logged octets show the 12-octet TSBK with `[Valid single CRC]` per block [viavi_control_channel_logging_2975.txt, pp.8-9].
- Control/voice message names by TIA designation are long lists of OSP/ISP/LC opcodes (GRP_V_CH_GRANT, UU_ANS_REQ, TELE_INT_CH_GRANT, IDEN_UP, NET_STS_BCST, RFSS_STS_BCST, U_REG_REQ, GRP_AFF_REQ, etc.) [viavi_control_channel_logging_2975.txt, pp.5-6]. Bit layouts for those opcodes are not in the texts beyond the octet examples and a few field decodes below.

## 4. PTT behaviour, floor control, addressing, setup and teardown

Conventional PTT:

- User selects a channel; access is by NAC, not CTCSS/DCS; NAC is a 12-bit code prefixing every packet including voice [radioreference_wiki_p25.txt, lines 187-189].
- Call = HDU + LDU1/LDU2 sequence + TDU as above; receiver unmutes only if NAC matches (unless promiscuous) [blaze_p25sec_usenix2011.txt, p.4] [sigidwiki_p25.txt, lines 88-94].
- Group vs individual addressed at the header/LC level; emergency, broadcast, encrypted/clear variants exist [kentucky_p25_spun_2020.txt, p.7].

Trunked PTT (voice granted on a traffic channel by the controller):

- Mobile idles on the control channel; to call, it sends an inbound request (ISP); the fixed network (FNE/RFSS) answers on the outbound channel (OSP) with a channel grant telling the radio where to go [viavi_control_channel_logging_2975.txt, pp.1-2] [viavi_advanced_p25_control_channel.txt, pp.8-9].
- Typical group call flow after registration/affiliation: Group Voice Request (GRP_V_REQ, ISP) from source SU, then Group Voice Channel Grant (GRP_V_CH_GRANT, OSP) from RFSS; radios move to the assigned traffic channel; voice LDUs carry the call with embedded LC; end with cancel/terminator [viavi_advanced_p25_control_channel.txt, Fig.14 / pp.13-14].
- Unit-to-unit flow: UU_V_REQ (ISP), UU_ANS_REQ (OSP to target), UU_ANS_RSP (ISP accept/deny/wait: $20 proceed, $21 deny, $22 wait), then UU_V_CH_GRANT (OSP) assigning both units a channel; teardown by cancel service request [viavi_advanced_p25_control_channel.txt, pp.16-18].
- Telephone interconnect: TELE_INT_DIAL_REQ (explicit digits, up to 10 or more with multi-block) or TELE_INT_PSTN_REQ (index), then TELE_INT_CH_GRANT after PSTN negotiation [viavi_advanced_p25_control_channel.txt, pp.17-19].
- Floor control is controller-driven: the grant carries the channel; priority and emergency flags in Service Options select handling; controllers can queue, deny, preempt, or assign priority [viavi_advanced_p25_control_channel.txt, Service Options octet / p.15] [kentucky_p25_spun_2020.txt, pp.13-14].
- Registration/affiliation must precede grants: Unit Registration Request (U_REG_REQ) then Unit Registration Response assigning a Working Unit ID (WUID), with return codes REG_ACCEPT/FAIL/DENY/REFUSED; then Group Affiliation Request (GRP_AFF_REQ) then Group Affiliation Response assigning a Working Group ID (WGID), with AFF_ACCEPT/FAIL/DENY/REFUSED; local vs global via LG bit [viavi_advanced_p25_control_channel.txt, pp.10-12].
- Keep-alive/status overhead always present: RFSS_STS_BCST, NET_STS_BCST, IDEN_UP/IDEN_UP_VU are the minimum on an explicit channel [viavi_advanced_p25_control_channel.txt, p.6].

Addressing and IDs:

| Name | Size | Notes and defaults from texts |
|---|---|---|
| NAC (Network Access Code) | 12 bits, 3 hex digits | Like CTCSS for digital; per site/repeater; $293 default/interop/ham, $F7E receive-any, $F7F repeat-any [sigidwiki_p25.txt, lines 88-94]. On trunked sites NAC often built from System ID + Site ID digits [sigidwiki_p25.txt, line 108]. |
| Talkgroup ID | 16 bits, 4 hex digits, shown decimal | 1 ($1) default/untagged, 65535 ($FFFF) all-call; 16-bit channel-number field also carries channel grants [sigidwiki_p25.txt, line 96] [radioreference_wiki_p25.txt, lines 189-191]. |
| Unit / Source ID / Radio ID | 24 bits | Over 16M radios; in LDU1 and LCW; WUID is the 24-bit working alias assigned at registration ($000001-$FFFFFD) [radioreference_wiki_p25.txt, lines 189-191] [viavi_advanced_p25_control_channel.txt, p.11]. |
| WACN ID | 20 bits hex | System-of-systems ID; default $BEE00; unique across systems [sigidwiki_p25.txt, line 100] |
| System ID (SYSID) | 12 bits hex | With WACN identifies a system; default $001 but avoided in real systems [sigidwiki_p25.txt, line 102] |
| RFSS ID | 8 bits | Cluster of sites; default 1; valid $01-$FE [sigidwiki_p25.txt, line 104] [viavi_advanced_p25_control_channel.txt, p.8] |
| Site ID | 8 bits | Site within RFSS; default 1; $01-$FE [sigidwiki_p25.txt, line 106] [viavi_advanced_p25_control_channel.txt, p.9] |
| Channel field | 4-bit ID + 12-bit number | ID selects bandplan, number computes frequency [radioreference_wiki_p25.txt, lines 189-191] [viavi_advanced_p25_control_channel.txt, p.15] |

Channel computation (explicit mode):

- SU RX = Base Frequency + Channel Number x Channel Spacing [viavi_advanced_p25_control_channel.txt, p.16].
- Base Frequency register x 0.000005 MHz; Channel Spacing register x 0.125 kHz; receiver BW register x 0.125 kHz (IDEN_UP) or coded 6.25/12.5 kHz (IDEN_UP_VU); TX offset register x 0.250 MHz (IDEN_UP) with High/Low sign bit, or 14-bit signed VU offset computed as Sign(Transmit Offset x Channel Spacing x 12500) added to SU RX [viavi_advanced_p25_control_channel.txt, pp.8-10,16].
- Example decoded values (VHF multi-block capture): IDEN_UP_VU OPCODE 0x34, BW 0x5, XMITOFF 0x2140, CHANSPACE 0x32, BASEFREQ 0x01C9C380; NET_STS_BCST with SYSID 0x722, WACNID 0xAF652, SYSSRVCLASS 0x60; ADJ_STS_BCST with SYSID 0x544, RFSS 0x23, SITE 0x19 [viavi_control_channel_logging_2975.txt, pp.8-9]. Values are capture examples, not defaults.

## 5. FEC, interleaving, scrambling, CRC

- Claimed code families: BCH, Golay, Reed-Solomon, Trellis and Hamming protect the various data units; status symbols interleaved every 70 bits [wavecom_advanced_protocol_apco25.txt, p.2].
- NID: 16 info bits + 48 error-correction bits in a 64-bit field [blaze_p25sec_usenix2011.txt, p.3].
- Voice LDUs use unequal protection matched to IMBE bit importance; least important bits get no protection; this is why stream ciphers are required (see Security) [blaze_p25sec_usenix2011.txt, pp.4-5].
- Reed-Solomon described as over GF(2^6) (64-element extension field, 6-bit hex symbols); systematic codes denoted (n,k,d), e.g. "(24,12,8) Golay" style triplet; RS n/k scaled by 6 to get bit dimensions [blaze_p25sec_usenix2011.txt, p.4].
- Packet/Header/Data blocks: HB and last DB carry CRC with ARQ (confirmed delivery); fragments to packets to trellis-coded data blocks [wavecom_advanced_protocol_apco25.txt, p.2].
- TSBK: 2-octet CRC per single block; logger validates `[Valid single CRC]` [viavi_control_channel_logging_2975.txt, pp.8-9] [viavi_advanced_p25_control_channel.txt, Fig.1 / p.5]. Multi-block headers carry a header CRC; user does not set it [viavi_advanced_p25_control_channel.txt, p.6].
- Interleaving + error correction sit between "raw symbols" and "octets" in the test-set logger: symbols are pre-deinterleave, octets are post-correction [viavi_control_channel_logging_2975.txt, p.7].
- Polynomials, interleaver depths, scrambler taps: not given in these texts (see missing list).

## 6. Voice codec

- Phase 1: full-rate IMBE (Improved Multi-Band Excitation) by DVSI [wavecom_advanced_protocol_apco25.txt, p.1] [kentucky_p25_spun_2020.txt, p.22].
- Phase 2: half-rate AMBE+2 (Advanced MBE) at 3600 bps to fit two voice paths in one 12.5 kHz channel [viavi_p25_phase2_tdma.txt, Table 1] [kentucky_p25_spun_2020.txt, Fig.6 / p.15].
- DVSI 7.2 kbps IMBE figure: 4.4 kbps speech + 2.8 kbps error correction; selected by TIA after tests against VSELP/STC/CELP [dvsinc_iambe_overview.txt, lines 24-35].
- Coding: 144-bit IMBE subframe = 20 ms audio; 9 subframes = 180 ms per LDU [blaze_p25sec_usenix2011.txt, p.4].
- MBE principle: per-band voiced/unvoiced decisions, no codebook/residual search like CELP, scalable below 8 kbps down to 2.4 kbps, robust in noise and channel errors [dvsinc_iambe_overview.txt, lines 59-105].
- Newer radios use AMBE/AMBE+2 and stay backwards compatible with IMBE packaging [radioreference_wiki_p25.txt, lines 71-73].
- Bit allocation / packing order: not in these texts.

## 7. Encryption and authentication facts

- Algorithms named: AES-256 and DES (56-bit) as the P25 mandatory options [kentucky_p25_spun_2020.txt, p.9]; DES/3DES/AES plus null/clear in the standard, plus vendor proprietary such as 40-bit RC4/ADP for export [blaze_p25sec_usenix2011.txt, pp.5-6].
- OP25 fork documents real-time decryption support for ADP/RC4, DES-OFB, AES-256 when the key is known and entered; no decryption with unknown keys [op25_boatbod_readme.txt, lines 49-55].
- Signaling: header and LDU2 carry MI (72 bits, effectively 64), ALGID (8 bits), KID/Key ID (16 bits); clear = zeros; key table maps KeyID+AlgID to stored keys; group must share the same labeled key [blaze_p25sec_usenix2011.txt, pp.4-5] [wavecom_advanced_protocol_apco25.txt, p.2].
- Voice encryption uses stream mode: block ciphers run in Output Feedback (OFB) to make a keystream XORed with voice, because voice tolerates bit errors and cannot use block chaining; native stream ciphers (RC4) also used; some vendors support AES-GCM but not standard and it does not authenticate the talker [blaze_p25sec_usenix2011.txt, pp.5-6].
- No message authentication codes (MACs) on voice: any bit error would fail a MAC, so MACs are omitted; this allows bit-flip forgery/replay if keys are known or traffic is clear [blaze_p25sec_usenix2011.txt, pp.5-6].
- Key management: manual Key Fill Device (KFD) over Key Fill Interface (KFI), Over-The-Air-Rekeying (OTAR) via Key Management Facility (KMF), Inter-KMF Interface (IKI) for cross-system keys; KFD-to-KMF over KFI in development [kentucky_p25_spun_2020.txt, pp.10] [p25_tia_standards_accepted_2025.txt, lines 39-44: OTAR TIA-102.AACA, KFD TIA-102.AACD, Link Layer Auth TIA-102.AACE].
- Control-channel protection exists as options: Protected flag (P bit), protected vs non-protected SAP (61 vs 63), Link Layer Encryption (LLE) to hide control and IDs (listed as upcoming), plus AUTH_CMD/AUTH_Q/AUTH_RSP and P_PARM messages; test set can optionally do AES/DES on the control channel [viavi_advanced_p25_control_channel.txt, pp.4-6] [kentucky_p25_spun_2020.txt, p.11] [viavi_control_channel_logging_2975.txt, pp.2-6].
- Field observations reported in the security paper (single-vendor lab plus over-air federal monitoring; not a standard): LCW protection/encryption flag never set even when voice encrypted, so sender Link ID always clear; all radios receive clear even in secure mode with no mute option; OTAR/key-sync failures push users to clear; UI makes accidental clear easy [blaze_p25sec_usenix2011.txt, pp.6-9,13-14]. Treat as implementation/usage findings, not protocol requirements.
- DES warning: NIST withdrew DES approval in 2005; federal use only for legacy interop; AES recommended [kentucky_p25_spun_2020.txt, p.9].

## 8. How to detect and decode it with SDR tools

### 8a. Key TIA-102 document numbers (index only, not text)

The 2025 accepted-standards index names the documents; it gives no protocol bits. Useful numbers to quote when asking for or ordering the normative text [p25_tia_standards_accepted_2025.txt, pp.1-2]:

| Area | TIA number | Title as listed |
|---|---|---|
| Trunking overview | TIA-102.AABA-C | Project 25 Trunking Overview |
| Control formats | TIA-102.AABB-B | Trunking Control Channel Formats |
| Control messages | TIA-102.AABC-E (+ E-1, E-3) | Trunking Control Channel Messages |
| Trunking procedures | TIA-102.AABD-C (+ B-1/B-2/B-3) | Trunking Procedures |
| Link control | TIA-102.AABF-D (+ D-1, D-2) | Link Control Word Formats and Messages |
| Conventional control | TIA-102.AABG | Conventional Control Messages |
| OTAR | TIA-102.AACA-D | Over-the-Air Rekeying Protocol |
| KFD interface | TIA-102.AACD-A | Key Fill Device Interface Protocol |
| Link layer auth | TIA-102.AACE-A | Link Layer Authentication |
| Phase 1 CAI | TIA-102.BAAA-B | FDMA Common Air Interface |
| Vocoder | TIA-102.BABA-A | Vocoder Description |
| Phase 2 physical | TIA-102.BBAB (per white paper) | Two-Slot TDMA Physical Layer (publ. Jul 2009) [motorola_p25_tdma_white_paper.txt, p.4] |
| Phase 2 MAC | TIA-102.BBAC (per white paper) | Two-Slot TDMA MAC Layer (trunked voice) [motorola_p25_tdma_white_paper.txt, p.4] |
| Phase 2 tests | TIA-102.CCAA / CCAB | Transceiver Measurement Methods / Performance Recommendations [viavi_p25_phase2_tdma.txt, pp.10-11] [motorola_p25_tdma_white_paper.txt, p.4] |
| C4FM/CQPSK tests | TIA-102.CAAB-E (+ E-1, E-2) | C4FM/CQPSK Modulation measurement [p25_tia_standards_accepted_2025.txt, p.5] |

### 8b. How to detect and decode it with SDR tools

Tool names that appear in the texts:

- Hobby: DSD+, SDRTrunk, OP25 (osmocom and boatbod fork `rx.py` / `multi_rx.py`), Trunk Recorder (uses OP25), Unitrunker/UniTrunker, Digital Speech Decoder (DSD), DSD-FME, Simple APCO plugin for SDR#, Pro96Com [sigidwiki_p25.txt, lines 205-219] [radioreference_wiki_p25.txt, lines 154-165].
- Professional / lab: go2DECODE, go2MONITOR, Rohde and Schwarz CA100 [sigidwiki_p25.txt, lines 223-230]; Wavecom W-CODE decoder (Phase 1 conventional only, saves .wav, auto polarity, needs center-frequency offset set) [wavecom_advanced_protocol_apco25.txt, p.3]; Aeroflex/Viavi 2975 with Option 6 control-channel logging and 3920 with P25 Phase 1/2 parametric tiles [viavi_control_channel_logging_2975.txt, pp.1-7] [viavi_p25_phase2_tdma.txt, pp.8-11].
- OP25 specifics: P25 conventional + Phase 1/2 trunking + TDMA control, single-dongle tuning, TGID blacklist/whitelist/priority, NAC 0x000 auto-detect, ESS logging at `-v 10`, `--nocrypt` to silence encrypted audio, symbol capture/replay, FLL fine tracking, Icecast metadata [op25_boatbod_readme.txt, lines 3-42,69-98].
- DSD-FME specifics: P25 Phase 1/2 plus EDACS/YSF/M17, trunking, LRRP/GPS mapping, OP25 capture-bin compatible replay [dsd_fme_readme.txt, lines 4-14].
- Scanner/receiver names for cross-check (not SDR): Uniden BCDx36HP/SDS100/SDS200, Whistler TRX-1/2, GRE/RS models, AOR/Icom/BlueTail P25RX [radioreference_wiki_p25.txt, lines 111-183].

What to look for:

- 12.5 kHz channel, NFM-like spectrum with 4-level transitions; simulcast CQPSK looks flatter/linearized; Phase 2 traffic is bursty TDMA (24 ms power window inside a 28 ms burst for inbound H-CPM; continuous complex AM/PM for outbound H-DQPSK) [sigidwiki_p25.txt, lines 20-27,70,80] [viavi_p25_phase2_tdma.txt, pp.10-11].
- Control channel: continuous 9600-bps C4FM data, about 40 trunking commands/s average, carrying IDs (see addressing table) [radioreference_wiki_p25.txt, lines 187-191] [viavi_control_channel_logging_2975.txt, p.2].
- Voice channel: repeating HDU/LDU/TDU pattern with 48-bit FS + 64-bit NID per unit; NAC mismatch = mute; TGID/Unit filters decide audio [blaze_p25sec_usenix2011.txt, pp.3-4] [sigidwiki_p25.txt, lines 88-96].
- Parametric checks used in labs: modulation fidelity 5 percent or less (deviation-error RMS), symbol deviation normalized to 2250 Hz (H-DQPSK) or 3000 Hz (H-CPM) maxima, constellation/distribution/eye plots, frequency error, symbol-rate error, BER on STD IB1031/OB1031 patterns, burst power [viavi_p25_phase2_tdma.txt, pp.8-11].
- Sample-rate guidance: not stated in these texts. See "From memory" for practical numbers.

### Control message inventory (names only, from the logger)

Voice Service OSPs logged: GRP_V_CH_GRANT, GRP_V_CH_GRANT_UPDT, GRP_V_CH_GRANT_UPDT_EXP (explicit), UU_ANS_REQ, UU_V_CH_GRANT, UU_V_CH_GRANT_UPDT, TELE_INT_CH_GRANT, TELE_INT_ANS_REQ, TELE_INT_CH_GRANT_UPDT [viavi_control_channel_logging_2975.txt, p.5].
Data Service OSPs: IND_DATA_CH_GRANT, GRP_DATA_CH_GRANT, GRP_DATA_CH_ANN, GRP_DATA_CH_ANN_EXP [viavi_control_channel_logging_2975.txt, p.5].
Control/Status OSPs: ACK_RSP_FNE, ADJ_STS_BCST, AUTH_CMD, CALL_ALRT, DENY_RSP, EXT_FNCT_CMD, GRP_AFF_Q, GRP_AFF_RSP, IDEN_UP, MSG_UPDT, NET_STS_BCST, P_PARM_BCST, P_PARM_UPDT, QUE_RSP, RFSS_STS_BCST, SCCB, STS_Q, STS_UPDT, SYS_SRV_BCST, U_REG_CMD, U_REG_RSP, U_DE_REG_ACK, LOC_REG_RSP, RAD_MON_CMD, ROAM_ADDR_CMD, ROAM_ADDR_UPDT [viavi_control_channel_logging_2975.txt, pp.5-6].
Voice Service ISPs: GRP_V_REQ, UU_V_REQ, UU_ANS_RSP, TELE_INT_DIAL_REQ, TELE_INT_PSTN_REQ, TELE_INT_ANS_RSP [viavi_control_channel_logging_2975.txt, p.6].
Data Service ISPs: IND_DATA_REQ, GRP_DATA_REQ [viavi_control_channel_logging_2975.txt, p.5].
Control/Status ISPs: ACK_RSP_U, AUTH_Q, AUTH_RSP, CALL_ALRT_REQ, CAN_SRV_REQ, EMRG_ALRM_REQ, EXT_FNCT_RSP, GRP_AFF_Q_RSP, GRP_AFF_REQ, IDEN_UP_REQ, MSG_UPDT_REQ, P_PARM_REQ, STS_Q_REQ, STS_Q_RSP, STS_UPDT_REQ, U_REG_REQ, U_DE_REG_REQ, LOC_REG_REQ, RAD_MON_REQ, ROAM_ADDR_REQ, ROAM_ADDR_RSP [viavi_control_channel_logging_2975.txt, pp.5-6].
Embedded LC messages: Group Voice Channel User, Group Voice Channel Update, Unit-to-Unit Voice Channel User, Group Voice Channel Update Explicit, Unit-to-Unit Answer Request, Telephone Interconnect Voice Channel User, Telephone Interconnect Answer Request, Call Termination/Cancellation, Group Affiliation Query, Unit Registration Command, Unit Authentication Command, Status Query, System Service Broadcast, Secondary Control Channel Broadcast, Adjacent Site Status Broadcast, RFSS Status Broadcast, Network Status Broadcast, Status Update, Message Update, Call Alert, Extended Function Command, Channel Identifier Update, Protection Parameter Broadcast [viavi_control_channel_logging_2975.txt, p.6].
Service Options octet (in voice requests/grants): bit 7 emergency (0 normal, 1 emergency); bit 6 protected (0 clear, 1 protected); bit 5 duplex (0 half, 1 full); bit 4 mode (0 circuit, 1 packet); bit 3 reserved 0; bits 2-0 priority (111 top, 100 default, 001 bottom, 000 reserved, others system-definable) [viavi_advanced_p25_control_channel.txt, p.15].
System Service Class octet values: $01 composite control, $02 no voice (update only), $04 backup control only, $10 data only, $20 voice only, $40 registration only, $80 authentication only, $F0 all but backup, ORed as needed [viavi_advanced_p25_control_channel.txt, p.7].
Coverage helpers (not in the standard, but widely deployed): simulcast (same frequency, time-synced), multicast (different frequencies per site), voting (pick best inbound by RSSI/BER); simulcast always includes voting [kentucky_p25_spun_2020.txt, pp.17-18].

## 9. Traps (things that waste time)

- C4FM vs CQPSK vs wide-pulse: all called "P25" colloquially, but only C4FM/CQPSK-LSM fit 12.5 kHz P25; Motorola wide-pulse (20K0F1E) and X2-TDMA (9600-bps TDMA pre-standard) are not P25 compliant; X2 is now retired/upgraded [radioreference_wiki_p25.txt, lines 77-105] [sigidwiki_p25.txt, lines 72-76].
- Phase 2 still uses a Phase 1 FDMA control channel in every deployed system described; do not expect a 6000-baud / H-DQPSK control channel unless a newer TDMA control option is in use [kentucky_p25_spun_2020.txt, Fig.11 note / p.19] [sigidwiki_p25.txt, line 80].
- Implicit vs explicit: 800 MHz can be implicit (radio looks up channel ID in a preprogrammed bandplan); VHF/UHF needs explicit (IDEN_UP_VU + grant carry full TX/RX math). A radio that finds the control channel can still fail grants if the bandplan is wrong [viavi_advanced_p25_control_channel.txt, pp.3-4].
- NAC wildcards: $F7E (monitor any) and $F7F (repeat any) hide the real NAC; $293 is the common default/interop value, not proof of a specific system [sigidwiki_p25.txt, lines 90-94].
- Talkgroup 1 and WACN $BEE00 / SYS $001 / RFSS 1 / SITE 1 are defaults; real trunked System IDs avoid $001 [sigidwiki_p25.txt, lines 96-106].
- Dynamic Dual Mode (DDM): an ASTRO 25 talkgroup falls back to FDMA if any Phase 1-only radio affiliates, then returns to TDMA; capacity measurements swing with affiliation [sigidwiki_p25.txt, line 82] [radioreference_wiki_p25.txt, line 107].
- Voice protection is unequal: corrupting only the 64-bit NID (3.7 percent duty of a 1728-bit LDU frame) kills the whole frame at the receiver; this is a lab-demonstrated selective-jamming fragility, not just theory [blaze_p25sec_usenix2011.txt, pp.10-12]. For decoding, it means a few bad NID bits lose the whole LDU even with good voice bits.
- Encrypted audio still yields clear metadata (NAC/DUID every frame, LCW unit IDs observed always clear in the studied fleet) [blaze_p25sec_usenix2011.txt, pp.6-7]. Do not assume "encrypted" means "anonymous" on these captures.
- Control-channel opcode coverage in test gear is partial by group (voice/data/control-status, OSP/ISP/LC); multi-block VHF/UHF overhead (IDEN_UP_VU, NET/RFSS status, adjacent-site) must all parse before grants make sense [viavi_control_channel_logging_2975.txt, pp.5-6] [viavi_advanced_p25_control_channel.txt, p.6].

## 10. What the documents did not give (do not guess)

- No TIA-102 field-bit tables: exact DUID enumeration, HDU/LDU1/LDU2/TDU bit positions, Link Control formats (TIA-102.AABF), trunking formats/procedures detail (TIA-102.AABB/AABC/AABD), CAI conformance (TIA-102.BAAA/BBAB/BBAC), vocoder packing (TIA-102.BABA) [SOURCES.md, lines 7-9] [p25_tia_standards_accepted_2025.txt, lines 17-52].
- No normative Frame Sync / NID codewords or polynomials: only the 24-symbol/48-bit + 64-bit (16+48) sizes and one captured byte snippet are stated [blaze_p25sec_usenix2011.txt, pp.3,12].
- No BCH/Golay/RS/Trellis/Hamming polynomials, interleaver maps, or scrambler/descrambler definition.
- No IMBE/AMBE+2 bit allocation, quantization, or error-class mapping beyond total rates and 144-bit/20-ms framing [dvsinc_iambe_overview.txt, lines 24-35] [blaze_p25sec_usenix2011.txt, p.4].
- No raised-cosine tap values or H-CPM phase-pulse coefficients beyond alpha = 1, modulation index 1/3, and the phase/state tables quoted [viavi_p25_phase2_tdma.txt, pp.4-6].
- No SDR sample-rate, filter-bandwidth, or AGC settings; no discriminator-vs-coherent demodulator recommendation.
- No Phase 2 burst/slot bit map (sync, SACCH/FACCH placement, guard times) beyond slot time, info bits, and power-window notes [viavi_p25_phase2_tdma.txt, Table 1 / p.11].
- No authentication/key-exchange message bit layouts; only names, interface labels, and high-level flows [kentucky_p25_spun_2020.txt, pp.9-10] [viavi_control_channel_logging_2975.txt, pp.5-6].

## From memory, not checked

- Practical SDR starting point often used for P25: capture at least 48 ksps complex around the 12.5 kHz channel (e.g. 96-240 ksps to include neighbours for trunking), FM-demodulate Phase 1 C4FM to 48 kHz audio-rate symbols then clock-recover at 4800 baud; expect a clean 4-level eye and steady NAC/DU streams on the control channel. Phase 2 needs coherent/differential PSK handling, not a plain FM discriminator, and slot-sync before voice decode. Verify against the tools above; not from the provided texts.
- If a signal looks like P25 but is 20 kHz wide FM with P25 voice, suspect non-standard wide-pulse; if it is TDMA at 9600 bps, suspect X2-TDMA. Both need non-P25 decoder paths.

