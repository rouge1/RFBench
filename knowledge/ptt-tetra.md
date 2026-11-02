# TETRA (ETSI EN 300 392) trunked and direct-mode PTT - SDR bench note

Scope: V+D trunked plus DMO for an SDR engineer. Every numbered fact is from the fetched texts. Tool names and workflow opinions are only under "From memory, not checked".
Sources: en_300392-2_AI_v030801.txt (V+D air interface); en_300392-7_security_v030501.txt (security); en_300396-2_DMO_radio_v010401.txt (DMO radio); en_300396-10_MDMO_v010102.txt (managed DMO); ets_300395-2_v010301_speech_codec_2005.txt (ACELP); sdr08_nagel_tetra_sdr_2008.txt; tr_102300-7_TEDS_designers_guide_v010201.txt; usenix_meijer_all_cops_2023.txt; TEA1/2/3/4 management texts; cadzow_etsi_tetra_security_2013.txt.

## 1. What the system is and who uses it

1. TETRA is a TDMA trunked digital land-mobile system with 4 physical
channels per RF carrier. For phase modulation the carrier bandwidth is
25 kHz. For QAM it is 25, 50, 100, or 150 kHz.
[en_300392-2_AI_v030801.txt, clause 4.5.1 / p.68]
2. Modes in the fetched V+D text: Conventional Access (CA, main carrier is
phase modulation, 25 kHz, MCCH on slot 1 of main carrier); Direct Access
(DA, main carrier is QAM); Downlink-Continuous Transmission (D-CT,
mandatory for MSs); Downlink-Carrier Timesharing (D-CTT); Main Control
Channel Timesharing (D-MCCTT); Multiple Slot Transmission (MST);
Normal Control Mode (NCM, mandatory) and Minimum Control Mode (MCM,
CA only). [en_300392-2_AI_v030801.txt, clauses 4.11-4.11.3 / p.70-72]
3. DMO is MS-to-MS direct operation without a base station, plus type 1
repeaters (DM-REP), gateways to V+D (DM-GATE), and combined REP/GATE.
Roles are master (transmitting traffic/control or reservation), slave
(receiving), and idle (monitoring to follow channel state or to pre-empt).
[en_300396-2_DMO_radio_v010401.txt, clauses 3 / p.12-14 and 7.2.1 / p.30]
4. Managed DMO (M-DMO) adds transmit authorization: an M-DM-MS needs
authorization before transmitting on a managed frequency, from a
broadcast presence signal by an M-DM-AUTH or directly from the SwMI /
management station. [en_300396-10_MDMO_v010102.txt, clause 4 / p.10-12]
5. Typical users are public-safety and utility/transport group PTT users; DMO covers out-of-coverage simplex. Roles and channels above are the cited part.
[en_300392-2_AI_v030801.txt, 4.11 / p.70-72; en_300396-2_DMO_radio_v010401.txt, clause 4 / p.12-14]

## 2. RF: spacing, modulation, rate, filter, bands, duplex

| Item | V+D trunked (TMO) | DMO |
|---|---|---|
| Carrier separation | 25 kHz for phase-modulation carriers; 25/50/100/150 kHz for QAM carriers. [en_300392-2, 4.5.1/4.11.1 / p.68-71] | 25 kHz. [en_300396-2, 6.2/6.4 / p.17-18] |
| Modulation | pi/4-DQPSK or pi/8-D8PSK. QAM carriers use 4/16/64-QAM. [en_300392-2, 4.7/cl.5 / p.69-73] | pi/4-DQPSK only, root-raised-cosine, roll-off 0.35. [en_300396-2, 4.5.3/cl.5 / p.14-15] |
| Modulation (bit) rate | 36 kbit/s for pi/4-DQPSK; 54 kbit/s for pi/8-D8PSK. [en_300392-2, 4.7/5.3 / p.69-73] | 36 kbit/s. [en_300396-2, 4.5.3 / p.14] |
| Symbol rate | 18 000 symbols/s for both pi/4 (2 bits/symbol) and pi/8 (3 bits/symbol). Slot = 255 symbols; symbol duration T = 500/9 us (about 55.56 us); modulation bit duration = 250/9 us (about 27.78 us). [en_300392-2, 4.5.3/fig 4.3 / p.68-69] | Same 255-symbol slot, 14.167 ms slot, 36 kbit/s stream. Basic resource: timeslot 85/6 ms = 14.167 ms = 510 bit (255 symbol) durations. [en_300396-2, 4.5.1-4.5.2 / p.14] |
| Pulse shaping | Ideal TX filter is linear-phase with magnitude |H(f)| = G(f), G(f) square-root raised cosine, roll-off alpha = 0.35. Practical windowed version allowed subject to modulation accuracy and adjacent-channel limits. [en_300392-2, 5.5-5.6 eq 5.2-5.4 / p.75-76] | Same root-raised-cosine 0.35 definition referenced for DM measurement. [en_300396-2, 6.4 / p.18] |
| Phase maps | pi/4-DQPSK Table 5.1: bits (B(2k-1),B(2k)): 11 -> -3pi/4; 01 -> +3pi/4; 00 -> +pi/4; 10 -> -pi/4. pi/8-D8PSK Table 5.2: 000 -> +pi/8; 001 -> +3pi/8; 101 -> +5pi/8; 100 -> +7pi/8; 010 -> -pi/8; 011 -> -3pi/8; 111 -> -5pi/8; 110 -> -7pi/8. Differential S(k)=S(k-1)*exp(j*Dphi(k)), S(0)=1. [en_300392-2, 5.4 / p.73-75] | Same Table 1 phase transitions for DM pi/4-DQPSK. [en_300396-2, clause 5 / p.15-16] |
| Frequency bands | Annex F is normative but only points to ETSI TS 100 392-15 for bands, duplex spacings, and channel numbering. No band table is inside the fetched V+D text. [en_300392-2, Annex F / p.1321] DMO sync clause also cites TS 100 392-15 clauses 5-6 for carrier frequencies. [en_300396-2, clause 7.1 / p.30] | Same external reference. Propagation-model text names 380-520 MHz range for DU50/DR50 examples. [en_300396-2, 6.6.3 / p.29-30] This is a test-model range, not a band authorization. |
| Duplex | MSs transmit in uplink band and receive in downlink band on dedicated TETRA frequencies; uplink/downlink centre frequencies and duplex spacing D are nationally allocated. Reverse operation adds instead of subtracts D. Specific D values are in the external TS 100 392-15 scheme. [en_300392-2, 6.1/cl.16.10.43 / p.80-82 and 23.5.4 / p.944] | Type 1A REP/GATE: single DM RF carrier. Type 1B REP/GATE: MS uplink carrier f1 plus duplex-spaced downlink carrier f2. DM-MS to DM-MS: single carrier. [en_300396-2, 6.2 / p.17-18] |
| Power classes (DM) | BS/MS classes are defined in V+D clause 6 (not tabulated here). | DM nominal powers: class 1 30 W / 45 dBm; 1L 17.5 W; 2 10 W; 2L 5.6 W; 3 3 W; 3L 1.8 W; 4 1 W; 4L 0.56 W; 5 0.3 W; 5L 0.18 W. Class 1/1L not valid for DM-MS; class 5/5L not for REP/GATE except test. [en_300396-2, Table 2 / p.18] |

QAM / TEDS (secondary guide + V+D 5.8-5.11): 8 subcarriers per 25 kHz (8/16/32/48 for 25/50/100/150 kHz), 2400 symbols/s per subcarrier, gross 38.4 kbit/s (25 kHz 4-QAM) to 691.2 kbit/s (150 kHz 64-QAM). [en_300392-2, 5.10-5.11 Table 5.3 / p.77; tr_102300-7, 6.4-6.8 / p.33-64]

## 3. Frame, slot, and burst structure (with sync words)

TDMA hierarchy (V+D): hyperframe = 60 multiframes = 61.2 s; multiframe =
18 frames = 1.02 s; frame = 4 slots = 170/3 ms (about 56.67 ms); slot =
85/6 ms (about 14.167 ms) = 255 symbols (phase modulation) or 34 QAM
symbols; uplink slots can split into 2 subslots of 127.5 symbols.
Frame 18 of each multiframe is the control frame.
[en_300392-2_AI_v030801.txt, 4.5.2 fig 4.3 / p.68]

Burst types for phase modulation (Table 9.2): control uplink burst CB
(SNmax 103, delay d 17 and 144.5 symbols for SSN1/SSN2); linearization
uplink/downlink bursts (no useful bits, time-mask only); normal uplink
burst NUB (SNmax 231, d 17); normal continuous downlink burst NDB
(SNmax 255, d 0); synchronization continuous downlink burst SB (SNmax
255, d 0); normal discontinuous downlink burst (SNmax 246, d 5);
synchronization discontinuous downlink burst (SNmax 246, d 5).
pi/8-D8PSK is not used in SB bursts; CB/NUB/NDB may use pi/4 or pi/8.
[en_300392-2_AI_v030801.txt, 9.4.4.1 Table 9.2 fig 9.4 / p.162-164]

V+D burst field tables (modulation-bit numbering BN):

| Burst | Fields (BN, length, content) |
|---|---|
| CB pi/4 (Table 9.3) | 1-4 tail (t1-t4); 5-88 scrambled control cb(1-84); 89-118 extended training x1-x30; 119-202 scrambled control cb(85-168); 203-206 tail. Total 206 bits = 103 symbols. [9.4.4.2.1 / p.165] |
| CB pi/8 (Table 9.4) | 1-6 tail T1-T6; 7-132 control cb(1-126); 133-177 extended training X1-X45; 178-303 control cb(127-252); 304-309 tail. Total 309 bits = 103 symbols. [9.4.4.2.1 / p.165] |
| NUB pi/4 (Table 9.5) | 1-4 tail; 5-220 block1 bkn1(1-216); 221-242 normal training n/p 22 bits; 243-458 block2 bkn2(1-216); 459-462 tail. Total 462 bits = 231 symbols. [9.4.4.2.4 / p.166] |
| NUB pi/8 (Table 9.6) | 1-6 tail; 7-330 block1 324 bits; 331-363 normal training-P8 33 bits; 364-687 block2 324 bits; 688-693 tail. [9.4.4.2.4 / p.166] |
| Normal continuous downlink pi/4 (Table 9.7) | 1-12 normal training 3 q11-q22; 13-14 phase adjust ha; 15-230 block1 216; 231-244 broadcast bb(1-14); 245-266 normal training n/p 22; 267-282 broadcast bb(15-30); 283-498 block2 216; 499-500 phase adjust hb; 501-510 training 3 q1-q10. Total 510 bits = 255 symbols. [9.4.4.2.5 / p.166-167] |
| Sync continuous downlink (Table 9.9) | 1-12 training 3; 13-14 phase hc; 15-94 frequency correction f1-f80; 95-214 sync block sb(1-120); 215-252 sync training y1-y38; 253-282 broadcast bb(1-30); 283-498 block2 216; 499-500 phase hd; 501-510 training 3. All pi/4 only. [9.4.4.2.6 / p.167] |
| Discontinuous variants | Same payloads with 5-symbol delay and guard structure per fig 9.4 and Tables 9.10-9.12 (NDB: guard + phase + 2x108 + training; SB: guard + phase + freq + sync block + sync training). [9.4.4.1 fig 9.4 / p.163] |

Downlink logical blocking: 3 blocks per downlink burst: Broadcast Block
BBK (two scrambled broadcast fields, 30 bits total), Block 1 and Block 2
(BKN1/BKN2, 216 scrambled bits each for pi/4, 324 each for pi/8; sync
burst Block 1 is 120-bit sync block). Uplink NUB has BKN1+BKN2 only.
[en_300392-2_AI_v030801.txt, 9.4.4.2.0 / p.165]

Sync / training words (bits as printed; first bit is sent first):

- Normal training 1 (pi/4, eq 9.4):
(n1..n22) = 1,1,0,1,0,0,0,0,1,1,1,0,1,0,0,1,1,1,0,1,0,0.
[en_300392-2, 9.4.4.3.2 / p.168-169]
- Normal training 2 (pi/4, eq 9.5):
(p1..p22) = 0,1,1,1,1,0,1,0,0,1,0,0,0,0,1,1,0,1,1,1,1,0.
- Normal training 3 / supplementary split across two downlink bursts
(pi/4, eq 9.6):
(q1..q22) = 1,0,1,1,0,1,1,1,0,0,0,0,0,1,1,0,1,0,1,1,0,1.
- pi/8 normal 1-P8 (eq 9.7, 33 bits):
(N1..N33) = 1,1,1,0,0,1,1,0,1,1,1,1,0,0,0,1,1,1,1,0,0,0,1,1,1,1,0,0,0,0,0,0,0.
- pi/8 normal 2-P8 (eq 9.8, 33 bits):
(P1..P33) = 1,0,1,0,1,1,1,1,1,1,0,1,0,1,0,1,0,1,1,1,0,0,0,1,1,0,0,0,1,0,0,1,0.
- Extended training, uplink control (eq 9.9, 30 bits):
(x1..x30) = 1,0,0,1,1,1,0,1,0,0,0,0,1,1,1,0,1,0,0,1,1,1,0,1,0,0,0,0,1,1.
- Extended training pi/8 (eq 9.10, 45 bits):
(X1..X45) = 0,1,1,1,0,0,1,1,0,1,0,0,0,0,1,0,0,0,1,1,1,0,1,1,0,1,0,1,0,1,1,1,1,1,1,0,1,0,0,0,0,0,1,1,1,0.
- Synchronization training, downlink SB (eq 9.11, 38 bits):
(y1..y38) = 1,1,0,0,0,0,0,1,1,0,0,1,1,1,0,0,1,1,1,0,1,0,0,1,1,1,0,0,0,0,0,1,1,0,0,1,1,1.
- Tail pi/4 (eq 9.12): (t1..t4) = 1,1,0,0. Tail pi/8 (eq 9.13):
(T1..T6) = 1,1,1,0,0,0. Frequency-correction field: f1-f8 = 1, f9-f72 = 0,
f73-f80 = 1; radiates unmodulated carrier +2.25 kHz with 4-symbol
pre/post at -6.75 kHz. [en_300392-2, 9.4.4.3.1-9.4.4.3.5 / p.168-171]
- Training-to-channel flag (Table 9.13): sequence 1 means Block1+Block2
carry TCH or SCH/F; sequence 2 means STCH+TCH, STCH+STCH, or
SCH/HD+SCH/HD, or SCH/HD+BNCH. For pi/8 (Table 9.14): 1 means
SCH-P8/F; 2 means SCH-P8/HD+SCH-P8/HD.
[en_300392-2, 9.4.4.3.2 / p.169]

DMO bursts (Table 14): DMO linearization burst (no useful bits);
DMO Normal Burst DNB (SNmax 235, d 17); DMO Synchronization Burst DSB
(SNmax 235, d 17). Nominal figure shows 34-bit guard + ramping plus
payload plus 6-bit guard. [en_300396-2_DMO_radio_v010401.txt, 9.4.3
Tables 14-16 fig 12 / p.50-51]

| DMO burst | Fields (BN) |
|---|---|
| DNB (Table 15) | 1-12 preamble j/k (P1/P2); 13-14 phase hk; 15-230 block1 216; 231-252 normal training n/p 22; 253-468 block2 216; 469-470 tail t1-t2. [9.4.3.2.1 / p.51] |
| DSB (Table 16) | 1-12 preamble l (P3); 13-14 phase hl; 15-94 freq correction f1-f80; 95-214 sync block sb(1-120); 215-252 sync training y1-y38; 253-468 block2 216; 469-470 tail. [9.4.3.2.3 / p.51] |
| DLB | No useful bits; time mask only. [9.4.3.2.2 / p.51] |

DMO words (pdftotext line-breaks repaired against surrounding equations):
preamble P2 (k1..k12) = 1,0,0,1,1,0,1,0,1,0,0,1 (eq 62); normal training 1
and 2 are the same 22-bit n/p patterns as V+D above (eq 63-64); sync
training y1..y38 is the same 38-bit V+D pattern (eq 66); tail (t1,t2) =
0,0 (eq 68), unlike V+D 4-bit 1,1,0,0. P1 (j) and P3 (l) 12-bit preambles
are listed as eq 61 and eq 65 but their printed extracts are line-broken
in the text file, so they are not quoted here. Frequency fields: DSB
f1-f8 = 1, f9-f72 = 0, f73-f80 = 1 (same +2.25 kHz tone convention);
interslot g1-g6 = 0, g7-g32 = 1, g33-g40 = 0 (opposite -6.75 kHz tone).
[en_300396-2_DMO_radio_v010401.txt, 9.4.3.3.1-9.4.3.3.6 / p.51-53]
DMO Table 17 flag: preamble P1 + training 1 means TCH or SCH/F;
P2 + training 2 means STCH+TCH or STCH+STCH.
[en_300396-2, Table 17 / p.52]

Nagel implementation cross-check (secondary): one slot carries a normal
burst of 432 data bits plus 78 bits training/sync/guard, 510 bits total
in 14.17 ms; 4 bursts per carrier; receiver downsamples to 36 kHz sample
stream and uses root-raised-cosine 0.35 FIR. Table 1 in that paper
repeats the V+D continuous-downlink layout above.
[sdr08_nagel_tetra_sdr_2008.txt, p.1-3]

## 4. PTT behaviour: start, floor control, addressing, setup/teardown

Call and channel vocabulary: Traffic Physical (TP) carries circuit traffic;
Control Physical (CP) carries MCCH/ECCH; Unallocated Physical (UP) is
idle. Conventional Access main carrier is the 25 kHz phase-modulation
carrier; MCCH is a single-slot pi/4-DQPSK channel on slot 1 in normal
control mode. [en_300392-2_AI_v030801.txt, 4.5.4/4.11.1 / p.69-71]

Logical channels relevant to PTT: TCH/S (speech), TCH/2.4/4.8/7.2 (data),
SCH/F/HU/HD (signalling full/half uplink/downlink), STCH (stolen),
BSCH (sync broadcast), BNCH (network broadcast), AACH (access assign,
carried in broadcast block BBK). Mapping tables list which burst/block
(FN 1-18, TN 1-4) each uses; e.g. TCH DL/UL on NDB/NUB BKN1/BKN2
FN 1-17; AACH DL on NDB/SB BBK; BSCH DL on SB BKN1.
[en_300392-2, 9.5 / p.180-184; 8.3 / p.137-147]

PTT floor control (called transmission control in the text):
- TNCC-TX request/indication/confirm primitives request and grant
transmission permission during the active phase.
[en_300392-2, 11.3.1-11.3.2 / p.193-194]
- Air messages: uplink U-TX DEMAND (request with priority and
encryption mode) and U-TX CEASED (release); downlink D-TX GRANTED,
D-TX CEASED, D-TX CONTINUE, D-TX INTERRUPT, D-TX WAIT.
PDU tables are in 14.7.1-14.7.2.
[en_300392-2, 14.5-14.7 / p.270-299]
- Priorities: access priority low/high/emergency; call priority from
not-defined through lowest, high, highest non-pre-emptive, lowest
pre-emptive, up to emergency pre-emptive; TX demand priority
low/high/pre-emptive/emergency pre-emptive, default low.
[en_300392-2, 11.3.3/14.8 / p.194-317]
- Changeover and pre-emption in DMO transfer the master role at a slot
boundary; new master normally adopts old timing counters unless a timing
change was requested; master aligns to REP/GATE DSB timing within 1/4
symbol steps. [en_300396-2, 7.2.1-7.2.3 / p.30-32]

Call setup and teardown (Circuit Mode Control Entity, CMCE):
- Call identifier allocated by SwMI at setup, kept for the call in that
location area, same for all members of a group call in one LA.
[en_300392-2, 14.4 / p.270-273]
- Downlink call PDUs: D-SETUP, D-CONNECT, D-CONNECT ACKNOWLEDGE,
D-RELEASE, D-ALERT, plus the D-TX family above; uplink: U-SETUP,
U-CONNECT, U-DISCONNECT, U-RELEASE, U-ALERT, U-TX DEMAND/CEASED.
[en_300392-2, 14.7.1-14.7.2 / p.285-299]
- Basic flow: calling MS U-SETUP; SwMI D-SETUP toward called party,
D-CONNECT when through-connected, U/D-CONNECT ACKNOWLEDGE,
traffic phase with TX arbitration, then D/U-RELEASE and release of the
call identifier and connections. Group calls use called-party GSSI;
individual calls use called-party ISSI; announcements note hook
(direct/on-off-hook) versus no-hook signalling.
[en_300392-2, 14.3-14.5 / p.265-282]
- There are no DMR-style header/terminator frames in these texts. The
TETRA equivalents are the CMCE setup/connect/release PDUs plus MAC
fragments (MAC-FRAG/MAC-END uplink/downlink) and stealing (STCH)
for signalling during traffic.
[en_300392-2, 14.7/21.4.2-21.4.3/23.8.4 / p.285-299 and p.674-692]

Addressing and identity:
- SSI is 24 bits; GSSI is 24 bits; Network (MCC+MNC) code is 14 bits
(MNC element 14 bits); tables reference EN 300 392-1 clause 7 for the
full ITSI/GTSI construction.
[en_300392-2, 16.10/18.5/19.4 / p.373-416 and p.581-603]
- Call-control text directs: individual calls use ISSI, group calls use
GSSI. Visitor identities (V)ASSI/(V)GSSI apply on foreign networks.
[en_300392-2, 14.3.2/17.3.9 / p.265-282 and p.429-430]
- Scrambling / colour: 30-bit extended colour code e(1)..e(30) seeds the
scrambler (BSCH uses all zeros); upper MAC passes a 24-bit MCC+MNC
scrambling code mapped to that 30-bit code; cells sharing channels use
the same code from SYNC/SYSINFO. Wrong code fails CRC and is
discarded. [en_300392-2, 8.2.5.2/18.4/23.4 / p.136-137 and p.806-830]
- AACH: one type-1 block of 14 bits, (30,14) Reed-Muller coded to 30
type-2 bits, no convolutional layer (type-4 = type-2), scrambled to 30
type-5 bits mapped as BBK bb(1-30).
[en_300392-2, 8.3.1.1 eq 8.43-8.44 / p.139-140]

DMO channel states: occupation (call transaction in progress),
reservation (channel-reservation signal holding the channel), plus
frequency-efficient mode with two independent DM channels A and B on
one RF carrier. DW-MS/DU-MS add dual-watch of DM plus TMO control.
[en_300396-2_DMO_radio_v010401.txt, clauses 3-4 / p.12-14]

## 5. FEC, interleaving, scrambling, CRC polynomials

Chain (phase modulation): type-1 MAC bits -> block encode (type-2) ->
convolutional encode (type-3) -> reorder/interleave (type-4) -> scramble
(type-5) -> multiplex (control/broadcast/sync/block1/block2) -> burst
builder -> differential encoder -> modulator. QAM chain replaces the
convolutional step with PCCC turbo coding or uncoded path.
[en_300392-2_AI_v030801.txt, 4.3-4.6 fig 4.1-4.2 / p.67-69 and 8.2.1 / p.126-127]

Block codes and CRC:
- Outer block code is (K1+16, K1) with 16-bit FCS. Generator:
G(X) = X16 + X12 + X5 + 1 (eq 8.16). Encoding rule eq 8.15-8.18 follows
ITU-T X.25 convention.
[en_300392-2, 8.2.3.3 eq 8.14-8.18 / p.130-131]
- AACH uses shortened (30,14) Reed-Muller. BSCH uses (76,60) block +
4 zero tail bits. SCH/HD/BNCH/STCH use (140,124) + 4 tails; SCH/HU
uses (108,92) + 4 tails; SCH/F uses (284,268) + 4 tails; TCH/7.2
uncoded at 432 bits; TCH/4.8 uses 288 + 4 tails; TCH/2.4 uses
144 + 4 tails. pi/8 variants use (212,196), (164,148), (428,412) with
4 tails. [en_300392-2, 8.3.1 fig 8.5-8.7 / p.137-139]

Convolutional codes:
- 16-state Rate-Compatible Punctured Convolutional (RCPC) mother code;
rates used: 2/3 (control/sync/speech signalling), 1/3, 292/432
(TCH/4.8), 148/432 (TCH/2.4). Puncturing tables in 8.2.3.1.3-8.2.3.1.6.
[en_300392-2, 8.2.3.1 / p.128-129 and 8.3.1 / p.140-143]
- QAM uses 8-state PCCC (two RSC encoders, quadratic-congruence inner
interleaver, punctured to rate 1/2 or 2/3) or uncoded, plus (16,5) RM
for headers. [en_300392-2, 8.2.3.4-8.2.3.5 / p.131-135]

Interleaving:
- (K,a) block interleaver: b4(k) = b3(i), k = 1 + ((a*i) mod K).
Examples: BSCH (120,11); SCH/HD (216,101); SCH/HU (168,13);
SCH/F (432,103); pi/8 SCH-P8/F (648,23), SCH-P8/HD (324,19),
SCH-P8/HU (252,17). [en_300392-2, 8.2.4.1 eq 8.34 / p.135 and 8.3.1 / p.140-147]
- N-block diagonal interleaving over N = 1, 4, or 8 blocks of 432 bits
for TCH/4.8/2.4 (eq 8.35-8.36 with inner (103,k) reordering).
[en_300392-2, 8.2.4.2 / p.135]
- QAM tables 8.2-8.6 give K/a per bandwidth and modulation (e.g.
SICH-Q/U K=16 a=5; SCH-Q/D 25 kHz 4-QAM K=408 a=23).
[en_300392-2, 8.2.4.3 / p.135-136]

Scrambling:
- b5(k) = b4(k) + p(k) mod 2 (eq 8.38). Sequence from 30-bit extended
colour code via LFSR with c(x) = 1 + X + X2 + X4 + X5 + X7 + X8 + X10
+ X11 + X12 + X16 + X22 + X23 + X26 + X32 (eq 8.39-8.40), recurrence
p(k) = sum c(i)*p(k-i) (eq 8.41), init p(-31)=p(-30)=1 and
p(k)=e(1-k) for k=-29..0 (eq 8.42). BSCH/Q uses e = all zero.
[en_300392-2, 8.2.5 / p.136-137]

DMO coding (EN 300 396-2 clause 8): same structure (block + RCPC +
interleave + scramble) with DMO tables; e.g. BSCH-type and SCH
constructions mirror V+D with 4 zero tails; scrambling clause 8.2.5 is
the DMO instance. [en_300396-2_DMO_radio_v010401.txt, clause 8 / p.35-45]

## 6. Voice codec

- Mandatory codec is the TETRA ACELP codec (EN 300 395-2 clauses 4-8);
AMR is optional (clauses 9-12). C code is bit-exact; conformance is by
that code. [ets_300395-2_v010301_speech_codec_2005.txt, clauses 1-4 / p.10-12]
- Model: Code-Excited Linear Prediction with long-term (pitch/adaptive
codebook) plus short-term (LP, order p = 10) synthesis filters;
analysis-by-synthesis with perceptual weighting W(z) = A(z)/A(z/gamma),
gamma1 = 0.85 for weighting (0.75/0.85 in shaping filter F(z)).
[ets_300395-2, 4.1-4.2 eq 1-4 / p.10-13]
- Framing: 30 ms speech frames, 4 subframes of 7.5 ms (60 samples).
137 bits per frame, i.e. 4567 bit/s. Bit allocation Table 1: LP filter 26;
pitch delay 8+5+5+5 = 23; algebraic code 16x4 = 64; VQ gains 6x4 = 24;
total 137. [ets_300395-2, 4.1 Table 1 / p.12-13]
- Channel coding for speech is defined in the codec text clause 5 and
referenced from V+D 8.3.1.3.6-8.3.1.3.7: full-slot TCH/S carries 432
type-4 bits C4(1-432) split as BKN1/BKN2 of 216 each; half-slot TCH/S
carries 216 type-3 bits C3(1-216) through (216,101) interleave to one
216-bit block in BKN2. Frame stealing replaces block bits with STCH
after coding/interleaving/scrambling. [en_300392-2, 8.3.1.3.6-8.3.1.3.7
eq 8.58-8.60 / p.143]
- Quality reference: full-rate ACELP with pi/4-DQPSK reaches threshold
at C/Ic 19 dB and dynamic reference sensitivity -106 dBm (BS) / -103 dBm
(mobile) under typical urban fading (delay spread up to 5 us).
[en_300392-2, 4.10 / p.70]

## 7. Encryption and authentication facts

Security classes (EN 300 392-7): each cell supports class 1 only,
class 2 only, class 2+1, class 3 only, or class 3+1. Class 2 and class 3
are never simultaneous in one cell. MS may support one, several, or all.
[en_300392-7_security_v030501.txt, clause 4 / p.20-21]

| Class | Meaning in the text |
|---|---|
| 1 | No air-interface encryption (authentication and key management still defined). |
| 2 | Air-interface encryption with Static Cipher Key (SCK); SCK association also covers DMO. [4.2.4-4.5.4 / p.33-42 and 4.5.4 / p.79-86] |
| 3 | Air-interface encryption with Derived/Common keys: DCK (per-MS after authentication), CCK (per location area), plus GCK/MGCK for group-addressed traffic. [4.2.1-4.2.3 / p.29-33] |

Keys and lifetime (Table 4.4): K (authentication key, per ITSI, long
life); DCK (after authentication, authentication session); CCK (after
authentication, lifetime not defined, one per LA at a time); SCK (up to
32 per MNI, lifetime not defined, one in use at a time except
changeover); GCK/MGCK/GSKO/GCK0 (group keys, generally long life).
[en_300392-7, 4.2.8 Table 4.4 / p.41]

Mechanisms cited:
- Authentication is symmetric challenge-response (2-pass confirmed)
between MS (ITSI) and SwMI authentication centre; session key KS
protects K; standard set is TAA1 (restricted distribution, TS 101 052);
no signalling mechanism for proprietary sets.
[en_300392-7, 4.1-4.1.1a / p.21-22]
- Over-the-air rekeying (OTAR) distributes SCK/GCK/CCK; CCK OTAR is
mandatory, others optional; key-change and security-class-change PDUs
(D-CK CHANGE DEMAND etc.) carry hyperframe/slot timing for changeover.
[en_300392-7, 4.3-4.5 / p.42-89 and Annex A / p.144-184]
- Air-interface cipher: keystream segment (KSS) XORed with plaintext.
Effective cipher key ECK is derived from CK with TB5, CN, LA-id, CC,
and IV (figure 4.16) to block replay and repeat-period reuse. IV is
built from slot/frame/multiframe/hyperframe numbers plus direction bit,
so uplink and downlink keystreams differ. KSS allocation to logical
channels (with and without PDU association) is in 6.4.1-6.4.2 for phase
and QAM channels. [en_300392-7, 4.2/6.4 / p.29-42 and p.118-122]
- Cipher algorithms TEA1-TEA4: the fetched texts are management rules
only (distribution, confidentiality, roles). TEA1 doc (TS 101 053-1),
TEA3 doc (TS 101 053-3 x2, one actually TEA4-titled), TEA2 report
(TR 101 053-2) all withhold the algorithm description; what is published
is who may hold and distribute keys and equipment.
[ts_101053-1_TEA1_v020101.txt; ts_101053-3_TEA3_v020101.txt;
ts_101053-3_TEA3_v030101.txt (TEA4-titled); tr_101053-2_TEA2_v020204.txt]
- Public analyses (secondary): Midnight Blue / USENIX 2023 reports TEA1
deliberately weakened key-register initialization reducing effective
entropy, plus uplink/downlink keystream-recovery and re-use scenarios
tied to IV/timestamp sync (18 frames/multiframe, 60 multiframes/
hyperframe) and FCS-oracle expansion; TEA2/TEA3 firmware also
recovered in that work. Treat as paper claims, not ETSI normative text.
[usenix_meijer_all_cops_2023.txt, sections 3-7 / p.4-12]
- ETSI workshop slides (secondary) summarize TEA roles and
standardisation status in 9 pages; no cipher detail.
[cadzow_etsi_tetra_security_2013.txt, p.1-9]

## 8. How to detect and decode it with SDR

What the texts predict on the air (citeable):
- Look for 25 kHz channels with continuous pi/4-DQPSK on the downlink
in D-CT mode; discontinuous carriers and uplink subslots are burstier.
Main-carrier MCCH is slot 1 in CA normal mode; frame 18 of every
multiframe is a control frame; sync bursts carry the 80-bit F field
(+2.25 kHz unmodulated tone marker) plus 120-bit sync block and 38-bit
sync word above. [en_300392-2, 4.11/9.4.4 / p.70-72 and p.162-171]
- Symbol timing to lock: 18 000 symbols/s, slot 14.167 ms = 255
symbols, frame 56.67 ms = 4 slots, multiframe 1.02 s = 18 frames.
Correlate against the 22-bit normal words (uplink/traffic) and the
38-bit sync word plus 30-bit extended word (control/sync) listed in
section 3; training type also flags TCH vs STCH/SCH content per Tables
9.13-9.14. [en_300392-2, 4.5.2/9.4.4.3 / p.68 and p.168-171]
- After demodulation: differential decode per Table 5.1/5.2, de-scramble
with the 30-bit extended colour from SYNC/SYSINFO (BSCH try all-zero
first), de-interleave per (K,a) for the logical channel, Viterbi-decode
RCPC, then check the 16-bit CRC G(X) = X16+X12+X5+1. AACH (30,14 RM,
30 bits in BBK) decodes first for slot-access state; then BSCH/BNCH/
SCH; traffic blocks need the N = 1/4/8 diagonal de-interleave context.
[en_300392-2, clauses 5/8 / p.73-76 and p.126-147]
- DMO: single 25 kHz carrier with DSB sync bursts (80-bit F field +
120-bit SCH/S + 38-bit sync word + 216-bit SCH/H) then DNB traffic;
master/slave timing per clause 7; preamble+training flag per Table 17.
In frequency-efficient mode expect two DM channels A and B on one
carrier. [en_300396-2, 7.2/9.4.3 / p.30-32 and p.50-53]
- Recording bandwidth: a 25 kHz TETRA carrier plus RRC skirts fits in a
50-100 kHz span; at 250 ksps complex the 18 ksym/s signal is about
13.9 samples/symbol; at 1 Msps it is about 55.6 samples/symbol, and at
2 Msps about 111 samples/symbol. These ratios follow directly from the
18 000 symbols/s rate above. Capture I/Q with enough headroom for the
RRC 0.35 shoulders and adjacent carriers; a 1-2 Msps span covering
several adjacent 25 kHz channels is a convenient bench choice.

### From memory, not checked (tools and workflow - not in the texts)
The ETSI texts name no SDR tools. Unchecked bench practice: telive, osmocom-tetra / osmo-tetra with wireshark dissector, fed from GNURadio, GQRX, SDR#, or SDRangel via loopback or file. Flow: find continuous downlink, record I/Q, RRC filter, timing recovery at 18 ksym/s, differential demap, sync-word correlate (38-bit y, 30-bit x, 22-bit n/p), de-scramble/de-interleave/Viterbi/CRC. Encrypted traffic stays protected when class 2/3 keys are unknown. Start at 1.024 Msps (about 56.9 samples/symbol) or 2.048 Msps to cover adjacent carriers.

## 9. Traps (ways to misread the signal or the docs)

1. pi/8 looks like noise if demodulated as pi/4: training words N1/N2/P
are 33 bits (not 22) and blocks are 324 bits (not 216); CB extended word
is 45 bits (not 30). Check Tables 9.4/9.6/9.8 and eq 9.7-9.10 before
assuming pi/4. [en_300392-2, 9.4.4 / p.165-171]
2. Training sequence value is signalling: n=1 vs n=2 selects TCH/SCH/F
vs STCH/SCH/HD content. Do not hard-code one word. [Table 9.13 / p.169]
3. Downlink BBK is only 30 bits split 14+16 around the training field;
it is not a full 216-bit block. Expecting 216 bits there breaks the
burst parser. [Tables 9.7-9.9 / p.166-167]
4. SB Block 1 is a 120-bit sync block, not a 216-bit traffic block;
NUB has no BBK at all. [9.4.4.2.0 / p.165]
5. Discontinuous downlink bursts start 5 symbols late (d = 5) with
different guard/phase layout; correlating at d = 0 misses them.
[Table 9.2 fig 9.4 / p.162-164]
6. DMO tail is 2 bits 0,0, not the V+D 4-bit 1,1,0,0; DMO SNmax is 235,
not 231/255/246. Reusing V+D burst code verbatim fails DMO sync.
[en_300396-2, 9.4.3 / p.50-53]
7. Scrambling is keyed by extended colour code, not fixed: BSCH uses
all-zero seed, everything else uses the cell code from SYNC/SYSINFO;
a CRC failure after correct Viterbi often means wrong colour, not bad
signal. [en_300392-2, 8.2.5.2/23.4 / p.136-137]
8. TCH interleaving depth N (1, 4, or 8) is set at call setup; single-block
decode of TCH/4.8/2.4 without accumulating (M+N-1) blocks yields
garbage. [en_300392-2, 8.2.4.2/8.3.1.3 / p.135 and p.140-143]
9. Class 2 and class 3 never coexist in one cell; seeing both key types
advertised on one frequency means two cells or a mis-decode.
[en_300392-7, clause 4 / p.20-21]
10. Annex F has no band table in the fetched text; any centre-frequency
formula must come from the external TS 100 392-15, not from EN 300
392-2 V3.8.1 itself. [Annex F / p.1321]

## 10. What the documents did not give
- Frequency tables, duplex spacings, channel-number formulas (deferred to TS 100 392-15). [Annex F / p.1321]
- TEA1/2/3/4 and TAA1 internals (management rules only). [TS/TR 101 053; EN 300 392-7 4.1.1a]
- Current EN 300 395-2 V1.3.2 draft; fetched codec is V1.3.1 (2005). TEDS normative detail (only TR 102 300-7 guide). No SDR HOWTOs; no IPs, keys, or account names in the normative texts.
## 11. Minimal field tables beside the receiver
AACH: 14 info -> (30,14) RM -> scramble -> bb(1-30). [8.3.1.1 / p.139-140] BSCH: 60 info -> (76,60) block + 4 tails = 80 -> RCPC 2/3 -> 120 -> (120,11) -> scramble -> sb(1-120). [8.3.1.2 / p.140] SCH/HD/BNCH/STCH: 124 -> (140,124)+4 = 144 -> 2/3 -> 216 -> (216,101) -> scramble. [8.3.1.4.1 / p.143-144] SCH/HU: 92 -> (108,92)+4 = 112 -> 2/3 -> 168 -> (168,13). [8.3.1.4.3 / p.144] SCH/F: 268 -> (284,268)+4 = 288 -> 2/3 -> 432 -> (432,103). [8.3.1.4.4] TCH/S full: 432 type-4 -> BKN1+BKN2; half: 216 type-3 -> (216,101) -> BKN2. [8.3.1.3.6-7 / p.143] Scrambler c(x) above; CRC G(X) above; pi/4 Table 5.1, pi/8 Table 5.2, alpha 0.35, slot 255 symbols / 14.167 ms. [clause 5 / p.73-77] DMO DNB/DSB layouts and 22/38-bit words per section 3. [DMO 9.4.3 / p.50-53]
