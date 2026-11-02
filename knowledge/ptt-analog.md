# Analog and legacy PTT bench note: CTCSS, DCS, selective calling, MDC-1200, trunking, FRS/GMRS/MURS/CB, PMR446, MCPTT

Scope: only what the fetched texts state. Citation form is [file, section or table]. For wide pdftotext layouts the line numbers in text/ extracts are given. Anything not in the texts is under "From memory, not checked" or under "What the documents did not give".

Source set used: docs/ptt/analog/SOURCES.md lists ETSI EN 300 086-1 V1.2.1, ETSI EN 300 296-2 V1.2.1 draft, 3GPP TS 22.179 V21.0.0 Stage 1, 47 CFR Part 95 eCFR, plus Wikipedia extracts (CTCSS, squelch/DCS, selective calling, MDC-1200, Motorola Type II, MPT 1327, EDACS, PMR446, TETRAPOL) and project READMEs (trunk-recorder, sdrtrunk, op25 boatbod fork, multimon-ng) [SOURCES.md, table].

## 1. What each system is and who uses it

| System | What it is | Typical users per texts |
|---|---|---|
| CTCSS (PL, Channel Guard, Quiet Channel) | Continuous sub-audible tone 67 to 254 Hz added to voice to mute/unmute receivers on a shared channel [wiki_ctcss.txt, Theory of operation] | Business, industrial, government co-channel users; amateur repeaters; FRS/PMR446 consumer radios [wiki_ctcss.txt, List of tones; wiki_selective_calling.txt, Group calling] |
| DCS / CDCSS (DPL, Digital Channel Guard, DTCS) | Continuous 134.4 bit/s sub-audible digital code instead of a tone [wiki_ctcss.txt, Digital-Coded Squelch] | Same shared-channel groups as CTCSS; PMR446 codes 39-121 are DCS [wiki_dcs_digital_coded_squelch.txt, DCS; wiki_pmr446.txt, Analogue FM] |
| SelCall: 5-tone, 1+1, 2+2, DTMF, single burst | Burst of in-band audio tones at start of transmission to address one radio or group [wiki_dcs_digital_coded_squelch.txt, SelCall] | European mobile fleets (ZVEI/CCIR 5-tone), US fire dispatch (Quik-Call), telephone-interconnect DTMF, repeater access burst [wiki_selective_calling.txt, SelCall; Tone burst] |
| MDC-1200 / MDC-600 (Stat-Alert) | Motorola low-speed AFSK data bursts on the voice channel for unit ID, status, emergency, selective call [wiki_mdc-1200.txt, top] | High-end analog FM public-safety and commercial fleets; also Icom/Kenwood radios with MDC [wiki_mdc-1200.txt, Patents and manufacturers] |
| Motorola Type I / Type II / SmartNet / SmartZone | Motorola analog trunked fleets; Type II replaces fleets/subfleets with talkgroups and IDs; SmartNet adds APCO-16 features; SmartZone networks sites via Zone Controller [wiki_motorola_type_ii.txt, top; SmartZone] | Public safety, large utilities, statewide systems [wiki_motorola_type_ii.txt, SmartZone] |
| EDACS | GE/Ericsson/Harris trunked protocol family with per-channel GETC controller, designed fault-tolerant [wiki_edacs.txt, History] | Public safety and large commercial systems; later replaced by digital voice options [wiki_edacs.txt, Digital Voice Modes] |
| MPT 1327 / 1343 | Open UK DTI standard for analog trunking, Jan 1988; control channel plus traffic channels [wiki_mpt_1327.txt, top] | Utilities, transport, industrial fleets in Europe, Australasia, Africa, parts of Asia/Americas [wiki_mpt_1327.txt, top] |
| FRS / GMRS / MURS / CB (47 CFR Part 95) | US Personal Radio Services: short-distance voice with limited data; rules in Subparts B, D, E, J [ecfr_47cfr_part95.txt, sec 95.503; sec 95.903; sec 95.1763; sec 95.2703] | Individuals, families, business, highway travelers; CB for personal/business/voluntary service [ecfr_47cfr_part95.txt, sec 95.503; sec 95.903] |
| PMR446 | European licence-exempt UHF personal/business radio, 446.0-446.2 MHz, analog FM plus DMR Tier 1 and dPMR options [wiki_pmr446.txt, top] | Recreational and small-site business, same-building and line-of-sight use [wiki_pmr446.txt, top] |
| MCPTT (3GPP TS 22.179 Stage 1) | Mission Critical Push To Talk service over LTE bearers with on-network and off-network (ProSe) modes [text/3gpp_TS_22.179-v21.0.0.txt, sec 4.3-4.5] | Public safety mission-critical users; group and private calls with floor control and priority [text/3gpp_TS_22.179-v21.0.0.txt, sec 5-7] |
| TETRAPOL | Fully digital FDMA closed-user-group system per PAS, with PSTN/data interfaces [wiki_tetrapol.txt, Description] | Public safety, military, industry, transport; e.g. French Gendarmerie RUBIS [wiki_tetrapol.txt, History] |

## 2. RF: spacing, modulation, deviation, filter, occupied bandwidth, bands, duplex

Narrowband FM reference (ETSI):

| Item | Value in text |
|---|---|
| Service and bands | Angle modulation, land mobile, 30-1000 MHz [text/en_300_086-1_v1.2.1_nbfm.txt, sec 1 / line 396] |
| Channel separations | 12.5, 20 and 25 kHz [text/en_300_086-1_v1.2.1_nbfm.txt, sec 1 / line 397] |
| Modulation | Angle modulation: phase (G3) or frequency (F3) [text/en_300_086-1_v1.2.1_nbfm.txt, line 497] |
| Max deviation (Table 2) | 12.5 kHz ch: +/-2.5 kHz; 20 kHz ch: +/-4.0 kHz; 25 kHz ch: +/-5.0 kHz [text/en_300_086-1_v1.2.1_nbfm.txt, Table 2 / lines 671-677] |
| Audio roll-off above 3 kHz | Deviation at 3.0 kHz (or 2.55 kHz for 12.5 kHz ch) is reference; at 6.0 kHz not more than 30.0 percent of max; 6.0 kHz to channel separation follows -14.0 dB/octave line [text/en_300_086-1_v1.2.1_nbfm.txt, sec 5.1.4.2 / lines 681-690] |
| Normal test modulation | 1 kHz tone at 60 percent of max deviation [text/en_300_086-1_v1.2.1_nbfm.txt, sec 7.1 / lines 1107-1110] |
| Adjacent-channel selectivity | 70.0 dB for 20/25 kHz; 60.0 dB for 12.5 kHz [text/en_300_086-1_v1.2.1_nbfm.txt, sec 5.2.4 / lines 870-873] |
| Integral-antenna voice scope | 30-1000 MHz, 12.5/20/25 kHz, primarily analogue speech [text/en_300_296-2_v1.2.1_pmr446.txt, sec 1 / lines 250-252] |

FRS (all channels 12.5 kHz bandwidth):

| Item | Value |
|---|---|
| Channels | 22 channels, all shared with GMRS; ch 1-7: 462.5625-462.7125 step 25 kHz; ch 8-14: 467.5625-467.7125 step 25 kHz; ch 15-22: 462.5500-462.7250 step 25 kHz [ecfr_47cfr_part95.txt, sec 95.563] |
| Emission types | F3E, G3E, F2D, G2D only [ecfr_47cfr_part95.txt, sec 95.571] |
| Authorized bandwidth | Occupied bandwidth not more than 12.5 kHz [ecfr_47cfr_part95.txt, sec 95.573] |
| Deviation/audio | Peak deviation not more than 2.5 kHz; highest audio not more than 3.125 kHz [ecfr_47cfr_part95.txt, sec 95.575] |
| Power | ERP 0.5 W on ch 8-14; ERP 2.0 W on ch 1-7 and 15-22 [ecfr_47cfr_part95.txt, sec 95.567] |
| Frequency accuracy | Within +/-2.5 ppm [ecfr_47cfr_part95.txt, sec 95.565] |
| Duplex | Simplex handheld; no repeater rule in FRS subpart; digital data limited to 1 per 30 s, no store-and-forward packet [ecfr_47cfr_part95.txt, sec 95.587] |

GMRS:

| Item | Value |
|---|---|
| Channels | 30 channels: 8x 462 MHz main (462.5500-462.7250); 7x 462 MHz interstitial (462.5625-462.7125); 8x 467 MHz main (467.5500-467.7250); 7x 467 MHz interstitial (467.5625-467.7125) [ecfr_47cfr_part95.txt, sec 95.1763] |
| Who transmits where | 462 main: mobile/portable/repeater/base/fixed; 462 interstitial: mobile/portable/base; 467 main: mobile/portable/control/fixed only via repeater or brief test; 467 interstitial: handheld portable only [ecfr_47cfr_part95.txt, sec 95.1763] |
| Bandwidth | 20 kHz on 462 main, 462 interstitial and 467 main; 12.5 kHz on 467 interstitial [ecfr_47cfr_part95.txt, sec 95.1773] |
| Deviation | +/-5 kHz on main and 462 interstitial; +/-2.5 kHz plus 3.125 kHz max audio on 467 interstitial [ecfr_47cfr_part95.txt, sec 95.1775] |
| Power | 50 W TX output mobile/repeater/base on main; 15 W fixed on main; 5 W ERP on 462 interstitial; 0.5 W ERP handheld on 467 interstitial [ecfr_47cfr_part95.txt, sec 95.1767] |
| Emissions | Must carry F3E or G3E; authorized set A1D F1D G1D H1D J1D R1D A3E F3E G3E H3E J3E R3E F2D G2D [ecfr_47cfr_part95.txt, sec 95.1771] |

MURS:

| Item | Value |
|---|---|
| Channels | 5 VHF: 151.820, 151.880, 151.940, 154.570, 154.600 MHz [ecfr_47cfr_part95.txt, sec 95.2763] |
| Bandwidth | 11.25 kHz on 151.820/151.880/151.940; 20.0 kHz on 154.570/154.600; A3E not more than 8.0 kHz [ecfr_47cfr_part95.txt, sec 95.2773] |
| Power | TX output not more than 2 W [ecfr_47cfr_part95.txt, sec 95.2767] |
| Emissions | A1D A2B A2D A3E F2B F1D F2D F3E G3E; A3E/F3E/G3E may include selective calling or tone squelch [ecfr_47cfr_part95.txt, sec 95.2771] |
| Duplex | No repeaters or signal boosters, no store-and-forward packet; no continuous carrier; no aircraft in flight; no PSTN interconnection [ecfr_47cfr_part95.txt, sec 95.2707; sec 95.2733; sec 95.2749; sec 95.2757] |

CB (CBRS, 26-27 MHz):

| Item | Value |
|---|---|
| Channels | 40 channels 26.965-27.405 MHz per table (ch 1 at 26.965 through ch 40 at 27.405) [ecfr_47cfr_part95.txt, sec 95.963] |
| Emissions | Must carry A3E or J3E/R3E/H3E; may also carry F3E; SSB normally upper sideband [ecfr_47cfr_part95.txt, sec 95.971] |
| Bandwidth | 8 kHz for A3E/F3E; 4 kHz for J3E/R3E/H3E [ecfr_47cfr_part95.txt, sec 95.973] |
| Power | 4 W mean carrier for AM/FM; 12 W PEP for SSB [ecfr_47cfr_part95.txt, sec 95.967] |
| Deviation/modulation | A3E 85-100 percent with auto-modulation control above 2.5 W; F3E peak not more than +/-2 kHz [ecfr_47cfr_part95.txt, sec 95.975] |
| Operating | Max 5 min conversation then 1 min silence except emergency/traveler assist; ch 9 emergency/traveler only; no external RF amplifier [ecfr_47cfr_part95.txt, sec 95.931; sec 95.937; sec 95.957] |

PMR446 and trunking RF:

| Item | Value |
|---|---|
| PMR446 band | 446.0-446.2 MHz; analog uses 16 FM channels spaced 12.5 kHz; max 500 mW ERP, mobile use, integral antenna [wiki_pmr446.txt, Technical information; Analogue FM] |
| PMR446 digital | DMR Tier I: 16 channels, 12.5 kHz, 4-FSK 3.6 kbit/s; dPMR: 32 channels, 6.25 kHz, 4-FSK 3.6 kbit/s; color code 0-16 replaces CTCSS role [wiki_pmr446.txt, Analogue FM; Digital PMR] |
| MPT 1327 RF | One or more channels per site, typically 6-10; 12.5 or 25 kHz bandwidth; 6.25 or 12.5 kHz spacing typical; at least one control channel, rest traffic; non-dedicated CCH can become traffic when busy [wiki_mpt_1327.txt, MPT1327 advantages] |
| MPT speech/data | Speech narrowband FM; control data 1200 bit/s FFSK; traffic-channel modem call for free-form 1200 baud, up to 19200 bit/s with special modems [wiki_mpt_1327.txt, Speech and data] |
| Motorola/EDACS RF | Texts give system/channel counts, not RF tables: Type II SmartNet up to 28 system channels [wiki_motorola_type_ii.txt, SmartNet]; EDACSards per-channel GETC architecture [wiki_edacs.txt, History]; digital voice rates in sec 7 |
| MCPTT RF | No analog RF table; runs on LTE bearers with QoS (QCI/bandwidth scheduling notes) and ProSe for off-network [text/3gpp_TS_22.179-v21.0.0.txt, line 480; sec 4.5; Abbreviations ProSe/GCSE_LTE] |

## 3. Frame, burst, slot, sync words, field tables

CTCSS tone layer (no frames):

| Parameter | Value |
|---|---|
| Tone range | 67 to 254.1 Hz; tones present whenever PTT pressed [wiki_ctcss.txt, Theory of operation] |
| Level | Usually 15 percent of system deviation, e.g. 750 Hz on 5 kHz system; engineered 500 Hz-1 kHz (10-20 percent) [wiki_ctcss.txt, Theory of operation] |
| End-of-TX reverse burst | Phase shift about 200 ms at release; 180 deg first used, then +/-120 to 135 deg found optimal to stop reeds; GE name Squelch Tail Elimination [wiki_ctcss.txt, Reverse CTCSS] |

DCS codeword (continuous, not burst):

| Parameter | Value |
|---|---|
| Bit rate | 134.4 bit/s [wiki_ctcss.txt, Digital-Coded Squelch]; also stated 134.5 baud in selective-calling extract [wiki_selective_calling.txt, DCS] |
| Code | 23-bit Golay (23,12): 12 data bits plus 11 check bits; last 3 data bits fixed 001; remaining 9 bits give 512 possibilities as 3-digit octal; first transmitted bit is LSB so code looks backwards [wiki_dcs_digital_coded_squelch.txt, DCS] |
| Usable subset | Only 83 of 512 codes used, to avoid falsing from alignment collisions; standardized by TIA [wiki_dcs_digital_coded_squelch.txt, DCS] |
| Turn-off | Some equipment sends 136 Hz square-wave for 100-300 ms at end to mute before squelch crash [wiki_selective_calling.txt, DCS] |

Selective calling bursts:

| Scheme | Burst and timing per texts |
|---|---|
| 5-tone SelCall | Burst of up to five in-band audio tones at start; speaker opens on fifth valid tone; stays open until carrier squelch drops, then resets [wiki_dcs_digital_coded_squelch.txt, SelCall; wiki_selective_calling.txt, SelCall] |
| Single tone burst | 0.5-1.5 s audio tone at start; e.g. 1750 Hz amateur repeaters; German public networks 1750 Hz Tone I and 2135 Hz Tone II, short 1000 ms vs long over 2000 ms; repeater-select tones 1800/2000/2200/2400/2552 Hz noted [wiki_selective_calling.txt, Tone burst] |
| Two-tone 1+1 (Quik-Call II / Type 99) | Tone, 50-1000 ms silence, second tone; decoder window e.g. up to 2 s for second tone; tones sent 500 ms-3 s; level at two-thirds system deviation (3.3 kHz on +/-5 kHz) [wiki_selective_calling.txt, Two-tone sequential] |
| Quik-Call I 2+2 | Tone pair, 50-1000 ms silence, second pair; pair detectors reduce falsing [wiki_selective_calling.txt, Quik-Call I] |
| DTMF | 2-7 digits; row+column pairs at similar levels; two-thirds deviation (3.3 kHz on +/-5 kHz); min 55 ms tone plus 55 ms gap [wiki_selective_calling.txt, DTMF] |
| Modat | 7-tone sequence like 5-tone SelCall; per-press unit ID or emergency; described as 6-digit or 7-char string e.g. 698R124 or 6988124 where R means repeat last digit; TX start delayed for CTCSS/voting path [wiki_selective_calling.txt, Modat] |

MDC-1200 packet (only scheme with bit table in texts):

| Field | Bits |
|---|---|
| On-air expansion | 32-bit packet plus 16-bit CRC = 48-bit inner word, half-rate convolutional coded, plus 24-bit preamble plus 40-bit sync = 176 bits total [wiki_mdc-1200.txt, Signal format] |
| Timing | About 290 ms at 600 bit/s rate [wiki_mdc-1200.txt, Signal format] |
| Preamble | 24 bits alternating ones and zeros for clock recovery [wiki_mdc-1200.txt, Signal format] |
| Sync | 40-bit fixed sync word; preferred match 35 of 40 bits (exact hex not stated) [wiki_mdc-1200.txt, Signal format] |
| Address | Bits 0-15, four hex digits, up to 65536 addresses; inbound = sender ID [wiki_mdc-1200.txt, Signal format; Unit ID] |
| Opcode | Bits 24-28 [wiki_mdc-1200.txt, Signal format] |
| Direction | Bit 19 inbound/outbound [wiki_mdc-1200.txt, Signal format] |
| Ack request | Bit 30 [wiki_mdc-1200.txt, Signal format] |
| Command vs free-format | Bit 31; free-format carries 31 user data bits each, concatenated after header [wiki_mdc-1200.txt, Signal format] |

Motorola Type II addressing/status:

| Item | Value |
|---|---|
| Limits | Up to 28 system channels; up to 65534 radio IDs; up to 4094 talkgroups; odd-numbered talkgroups; Type II IDs appear as even 4-5 digit numbers e.g. 2160 [wiki_motorola_type_ii.txt, top] |
| System ID | 4-digit ID unique per system; radio needs matching System Key to program [wiki_motorola_type_ii.txt, top] |
| Status bits | Talkgroup ID +0 normal, +1 all, +2 emergency, +3 patch, +4 emergency patch, +5 emergency multi-group, +6 not assigned, +7 multiselect, +8 DES encryption, +9 DES all, +10 DES emergency, +11 DES patch, +12 DES emergency patch, +13 DES emergency multi-group, +14 not assigned, +15 multiselect DES TG; example voice on 1808 appears as 1815 when multiselect [wiki_motorola_type_ii.txt, Status bits] |
| Voice-channel sub-audible data | Carries talkgroup activity for Priority Monitor; drops last digit so only odd talkgroups usable [wiki_motorola_type_ii.txt, Status bits] |
| SmartZone | Up to 28 channels per site; up to 64 sites (older 48); Dynamic Site Assignment via affiliation table in Zone Controller; Ambassador Electronics Bank routes audio; Omnilink up to 4 zones [wiki_motorola_type_ii.txt, SmartZone] |

MPT 1327 addressing/data:

| Item | Value |
|---|---|
| Call number | Prefix (3 digits) + fleet + unit; converted to 20-bit address in mobile; Zetron first 2 digits fleet, last 2 unit; 6000-6999 typically group calls [wiki_mpt_1327.txt, Numbering and addressing] |
| Control data | 1200 bit/s FFSK; status messages 5-bit; short data 186-bit; transparent data on traffic channel [wiki_mpt_1327.txt, Data communication; Speech and data] |
| Call kinds | Point-to-point, group with/without talk entitlement, broadcast without PTT [wiki_mpt_1327.txt, Calls] |

MCPTT call structure (requirements, not bits):

| Item | Value |
|---|---|
| Talk burst | Each granted PTT request starts a transmission or talk burst; group call is one or more bursts separated by configurable hang time; resources stay assigned during hang except pre-emption [text/3gpp_TS_22.179-v21.0.0.txt, sec 4.3 / lines 399-411] |
| Floor control | Deterministic arbitration among contending requests by priority; higher priority can override current talker; talk-time limits allow same/lower priority a chance [text/3gpp_TS_22.179-v21.0.0.txt, sec 4.4 / line 386] |
| Modes | On-network via server; off-network via ProSe discovery and direct UE-UE path; experience intended similar [text/3gpp_TS_22.179-v21.0.0.txt, sec 4.4 / line 384; Abbreviations] |

## 4. PTT behaviour: start, floor, addressing, setup/teardown

| System | PTT and call flow per texts |
|---|---|
| CTCSS conventional | Press PTT always encodes tone with voice; receiver unmutes only on carrier plus correct tone; monitor/MON button or hang-up-box switch forces carrier squelch before TX; Busy Channel Lockout option blocks TX while channel busy [wiki_ctcss.txt, Theory of operation] |
| CTCSS teardown | Release PTT sends ~200 ms phase-reversed tone; mutes receivers before squelch crash; without it reed decoders coast up to 1.5 s [wiki_ctcss.txt, Reverse CTCSS] |
| DCS conventional | Same group-code idea as CTCSS; 100-300 ms 136 Hz turn-off at end; 131.8/136.5 Hz CTCSS decoders may false on DCS bitstream [wiki_ctcss.txt, Digital-Coded Squelch; wiki_selective_calling.txt, DCS] |
| 5-tone / burst / 2-tone / DTMF | Leading tone burst opens addressed speaker; group gets short announcement tone; private gets receipt tone back then open audio; latched until carrier drops [wiki_selective_calling.txt, SelCall; Tone burst; Two-tone sequential; DTMF] |
| Monitor rule (US) | FCC requires monitor before TX on selective-call channels: split PTT with monitor segment and interlock, or mic hang-up switch, or LED/busy indication [wiki_selective_calling.txt, Group calling] |
| MDC PTT ID | Leading: burst at PTT press with header tone and programmed delay (hundreds of ms) for CTCSS/voting path, speaker plays wait tone; trailing: burst at release on open path [wiki_mdc-1200.txt, Unit ID] |
| MDC ack/handshake | Bit 30 requests ack; called unit replies; encoder beeps on success; on busy channels CAD can pass busy-channel acks to mark receipt; costs air time [wiki_mdc-1200.txt, top] |
| MDC features on PTT | Unit ID, status buttons (generic status 1/8 etc.), emergency button (silent, 3x burst, optionally forced to ch 2), selective inhibit/stun, radio check ping with silent-ack option, selective call pages CALL flash [wiki_mdc-1200.txt, Emergency; Status; Selective inhibit; Radio check; Selective calling] |
| SmartNet/SmartZone trunking | Press PTT -> radio messages system -> Channel Grant on control channel tells frequency; radios follow grant; recorder logic watches control channel continuously and spawns voice-channel decoder per grant; no over-the-air end message, end inferred by activity timeout [github_trunk-recorder_README.txt, How Trunking Works; wiki_motorola_type_ii.txt, SmartZone] |
| MPT 1327 trunking | Subscriber exclusively allocated a traffic channel per call from pool; if none free, call queued (radio-queued if CCH in traffic mode, losing first-come order); group calls with or without talk entitlement; broadcast calls deny PTT [wiki_mpt_1327.txt, Numbering and addressing; Calls] |
| EDACS note | Per-channel GETC trunking gives fault tolerance; texts give no PTT message flow [wiki_edacs.txt, History] |
| FRS/GMRS/MURS/CB PTT | Half-duplex push-to-talk voice; FRS brief end-of-TX tones allowed; GMRS repeater access tones and ID tones allowed; CB 5-min limit with 1-min pause; MURS no continuous carrier, must monitor before TX [ecfr_47cfr_part95.txt, sec 95.577; sec 95.1777; sec 95.957; sec 95.2725] |
| MCPTT floor | Request permission to talk; floor arbitrates by priority; emergency can pre-empt; private calls with or without floor control; group affiliation/selection governs who hears; late entry, multi-talker, override and audio cut-in options listed [text/3gpp_TS_22.179-v21.0.0.txt, sec 4.6; sec 5.6; sec 6.2.3] |

## 5. FEC, interleaving, scrambling, CRC, polynomials

| System | Coding per texts |
|---|---|
| CTCSS | Narrow bandpass + rectify to DC; no FEC; adjacent tones (e.g. 97.4/100.0 Hz) can false; 50 Hz mains can false 100.0 Hz [wiki_ctcss.txt, Theory of operation; List of tones] |
| DCS | 23-bit Golay (23,12), corrects 3 or fewer bit errors per word [wiki_dcs_digital_coded_squelch.txt, DCS] |
| MDC | 16-bit CRC appended (polynomial not stated) then half-rate convolutional code; sync tolerance 35/40; error-correcting decode recovers 32-bit packet from corrupted burst [wiki_mdc-1200.txt, Signal format] |
| Trunking/digital voice | No polynomial, interleave or scrambler values in these extracts; Type II status-bit signalling and MPT 1200 bit/s FFSK described functionally only [wiki_motorola_type_ii.txt, Status bits; wiki_mpt_1327.txt, Speech and data] |
| MCPTT | Requirements text only; no FEC/CRC/scrambling constants [text/3gpp_TS_22.179-v21.0.0.txt, TOC and sec 4-7] |
| Filters as coding-adjacent | GMRS audio low-pass 60 log(f/3) dB 3-20 kHz then 50 dB above 20 kHz [ecfr_47cfr_part95.txt, sec 95.1775]; MURS audio filter 40 log(f/3) dB 3-15 kHz then 28 dB above 15 kHz [ecfr_47cfr_part95.txt, sec 95.2775]; ETSI 6 kHz/30 percent/-14 dB/oct mask [text/en_300_086-1_v1.2.1_nbfm.txt, sec 5.1.4.2] |

Polynomials, interleavers and scramblers are not stated in the fetched texts; do not invent them. See missing list.

## 6. Voice codec

| System | Codec per texts |
|---|---|
| Analog FM (CTCSS/DCS/SelCall/MDC/FRS/GMRS/MURS/CB/PMR446 analog/MPT speech) | Analogue speech on FM, uncompressed; MPT explicitly uncompressed FM claimed more sensitive than TETRA [wiki_mpt_1327.txt, Advantages; wiki_pmr446.txt, Analogue FM] |
| EDACS digital voice | Voice Guard sub-band coding at 9600 bit/s; AEGIS adaptive multiband coding (claimed 10x Voice Guard); ProVoice IMBE (same IMBE vocoder family as P25 but different error/modulation) [wiki_edacs.txt, Digital Voice Modes] |
| PMR446 digital | DMR Tier I and dPMR 4-FSK 3.6 kbit/s noted without vocoder name [wiki_pmr446.txt, Analogue FM; Digital FDMA] |
| MCPTT | Stage 1 states voice/audio and mouth-to-ear KPIs but names no vocoder in the extracted requirements [text/3gpp_TS_22.179-v21.0.0.txt, sec 6.15] |
| TETRAPOL | Digital FDMA system; vocoder not named in short extract [wiki_tetrapol.txt, Description] |

## 7. Encryption and authentication facts

| System | What texts actually say |
|---|---|
| CTCSS/DCS/SelCall | No security: any receiver without tone hears all; tones/codes only hide annoyance, not interception or jamming [wiki_ctcss.txt, Theory of operation] |
| Motorola Type II | System ID plus System Key required to program a radio on a system; DES/DVP status bits (+8 to +13, +15) mark encrypted/multiselect traffic [wiki_motorola_type_ii.txt, top; Status bits] |
| SmartZone | Analog and/or digital voice and/or analog/digital encryption listed as capability [wiki_motorola_type_ii.txt, SmartZone] |
| EDACS | ProVoice uses IMBE but is not APCO-25 compliant in error-correction/modulation [wiki_edacs.txt, Digital Voice Modes] |
| MPT 1327 | No encryption described; noted lack of CTCSS and high squelch engineering [wiki_mpt_1327.txt, Advantages] |
| MCPTT | UE authenticates with MCPTT User identity via Identity Management; User Profiles govern groups/priority/QoS/emergency; app-layer security on UE-to-service connection; temporary identities for default-UE login; gateway/relay roles listed [text/3gpp_TS_22.179-v21.0.0.txt, sec 4.5 / lines 428-434] |
| OP25 note (tool, not target) | Decrypts P25 ADP/RC4, DES-OFB, AES-256 only with known key; skips unknown-key traffic [github_op25_README.txt, Encryption capabilities] |

No keys, key-fill, or bypass material appears in the sources; none is given here.

## 8. How to detect and decode with SDR tools

Tool coverage stated in READMEs:

| Tool | What README claims |
|---|---|
| trunk-recorder | 1+ SDRs plus GNU Radio and OP25 libraries; follows control channel, spawns per-grant voice decoder; supports trunked P25 and SmartNet, conventional P25/DMR/analog with per-talkgroup channel [github_trunk-recorder_README.txt, Overview; How Trunking Works] |
| sdrtrunk | Cross-platform Java app for decoding/monitoring/recording trunked and related protocols with SDRs [github_sdrtrunk_README.txt, sdrtrunk] |
| OP25 boatbod fork | P25 Phase 1/2 and TDMA control, SmartZone/SmartNet and Connect+ TRBO (multi_rx, 2 dongles), DMR BS, NBFM analog conventional or SmartZone trunked; FLL tuning, squelch from PA3FWM quieting method [github_op25_README.txt, rx.py; multi_rx.py] |
| multimon-ng | Decodes POCSAG, FLEX, EAS, UFSK/CLIPFSK/FMSFSK/AFSK1200-2400, HAPN4800, FSK9600, DTMF, DUALTONE, ZVEI1-3/DZVEI/PZVEI, EEA/EIA/CCIR, MORSE, GSC Golay; pipe via rtl_fm at 22050 Hz or sox/wav/flac [github_multimon-ng_README.txt, top; Examples] |

Practical SDR setups (derived only from numbers above; sample rates are operator choice, not source mandates):

| Target | What to capture and look for |
|---|---|
| CTCSS | NBFM voice channel (12.5 or 25 kHz per secs 2); audio FFT 60-260 Hz shows continuous tone during voice; 15 percent deviation rule gives weak line (e.g. ~750 Hz deviation on 5 kHz system); check 200 ms phase flip at release [wiki_ctcss.txt, Theory of operation; Reverse CTCSS] |
| DCS | Same NBFM channel; look for 134.4 bit/s low-rate stream under voice; decode 23-bit Golay words, LSB first, 83-code subset; watch 136 Hz turn-off burst 100-300 ms [wiki_dcs_digital_coded_squelch.txt, DCS; wiki_selective_calling.txt, DCS] |
| 5-tone/2-tone/DTMF | FM-demodulated audio to multimon-ng ZVEI/EEA/EIA/CCIR/DTMF decoders; expect 0.5-3 s tones, 50-1000 ms gaps, DTMF min 55/55 ms [wiki_selective_calling.txt, Two-tone sequential; DTMF; github_multimon-ng_README.txt, top] |
| MDC-1200 | FM audio burst ~290 ms at 600 bit/s rate (1200 bit/s variant uses 1200/1800 Hz AFSK); record at 22050-48000 Hz audio or 250 kHz-1 MHz RF with NBFM demod; expect 1200/1800 Hz pair and optional chirp-masking on radios [wiki_mdc-1200.txt, Signal format; wiki_selective_calling.txt, MDC-600 and MDC-1200] |
| Motorola/EDACS trunk | Wideband SDR covering control plus voice pool (e.g. 1-2.4 MHz for a UHF site); trunk-recorder/sdrtrunk/OP25 follow grants; SmartNet up to 28 channels; scanner shows ID+status offsets [wiki_motorola_type_ii.txt, Status bits; github_trunk-recorder_README.txt, How Trunking Works] |
| MPT 1327 | Dedicated 12.5/25 kHz control channel with continuous 1200 bit/s FFSK; allocate one SDR channel to CCH and hop/stare for traffic; non-dedicated CCH disappears into traffic when busy [wiki_mpt_1327.txt, Advantages; Speech and data] |
| FRS/GMRS/MURS/CB/PMR446 | Single-channel NBFM (CB also AM/SSB): FRS 462/467 MHz 12.5 kHz 2.5 kHz dev; GMRS main 20 kHz 5 kHz dev; MURS VHF 11.25/20 kHz; CB 26 MHz 8/4 kHz; PMR446 446 MHz 12.5 kHz 500 mW [ecfr_47cfr_part95.txt, sec 95.563-95.575; sec 95.1763-95.1775; sec 95.2763-95.2773; sec 95.963-95.975; wiki_pmr446.txt, Analogue FM] |
| MCPTT | Not an SDR waveform in Stage 1; verify via LTE bearer logs and KPI clocks: access under 300 ms for 95 percent (99 percent emergency), mouth-to-ear under 300 ms for 95 percent voice bursts, 0 ms trailing loss [text/3gpp_TS_22.179-v21.0.0.txt, sec 6.15.3.2 / lines 1247-1253] |

Example pipes named in multimon-ng README (use center frequency of target channel):

```
rtl_fm -f 462.5625M -s 22050 | multimon-ng -t raw -a DTMF -a ZVEI2 /dev/stdin
rtl_fm -f 446.00625M -s 22050 | multimon-ng -t raw -a FMSFSK -a AFSK1200 /dev/stdin
```

## 9. Traps

- Tone-number confusion: GMRS/FRS/PMR446 "code 12" varies by vendor; verify Hz or octal, not menu number [wiki_ctcss.txt, Interference and CTCSS].
- Adjacent CTCSS tones false: avoid pairs like 97.4/100.0 Hz on same channel; aged radios drift [wiki_ctcss.txt, Theory of operation].
- 50 Hz mains vs 100.0 Hz tone in UK/50 Hz regions [wiki_ctcss.txt, List of tones].
- DCS vs CTCSS clash: DCS stream falses 131.8/136.5 Hz decoders; DCS turn-off is 136 Hz [wiki_ctcss.txt, Digital-Coded Squelch; wiki_selective_calling.txt, DCS].
- Inverted DCS: 413 equals 054 inverted; check polarity before declaring mismatch [wiki_selective_calling.txt, DCS].
- Missed syllables: low tones (e.g. 67.0 Hz) decode slowly; use audio delay in repeater controller or higher tone 127.3-162.2 Hz [wiki_ctcss.txt, Theory of operation].
- Selective-call latch: speaker stays open until carrier drops; co-channel overlap still interferes even when muted [wiki_selective_calling.txt, top; SelCall].
- Deviation overdrive: DTMF/2-tone above two-thirds deviation clips, distorts sine, causes falsing; keep 3.3 kHz on 5 kHz system and preserve row/column balance [wiki_selective_calling.txt, DTMF; Two-tone sequential].
- MDC timing: leading ID needs header/delay for CTCSS/voting path; use trailing ID or extend delay if first syllable clipped [wiki_mdc-1200.txt, Unit ID; wiki_selective_calling.txt, Modat].
- SmartNet odd-talkgroup rule: priority sub-audible data drops last digit; assigning even talkgroups breaks Priority Monitor [wiki_motorola_type_ii.txt, Status bits].
- Scanner status offsets: +1/+2/+7/+8 etc. look like different talkgroups (e.g. 1808 vs 1815); log raw ID plus offset [wiki_motorola_type_ii.txt, Status bits].
- MPT non-dedicated CCH: channel vanishes into traffic use; queuing order lost; time-shared CCH slows registration [wiki_mpt_1327.txt, Spectrum efficiency].
- FRS/GMRS overlap: all 22 FRS channels shared with GMRS; power/bandwidth/role differ by main vs interstitial [ecfr_47cfr_part95.txt, sec 95.563; sec 95.1763].
- CB/antenna/legal traps: 5-min/1-min rule, ch 9 restriction, no amplifier, 60 ft/20 ft antenna cap [ecfr_47cfr_part95.txt, sec 95.937; sec 95.931; sec 95.941].
- MCPTT KPIs conditional: under 70 percent load, affiliated/selected group, same network; backhaul adds to budget [text/3gpp_TS_22.179-v21.0.0.txt, sec 6.15.3.2 / lines 1240-1244].

## 10. What the documents did not give

- CTCSS standard tone table values (only 67.0, 97.4, 100.0, 123.0, 127.3-162.2, 131.8, 136.5, 150.0, 203.5, 254.1 Hz mentioned as examples); full EIA/TIA RS-220A list not in extracts [wiki_ctcss.txt, List of tones].
- DCS 83-code table, Golay generator polynomial, and exact 40-bit MDC sync word (only 35/40 match rule stated) [wiki_dcs_digital_coded_squelch.txt, DCS; wiki_mdc-1200.txt, Signal format].
- ZVEI/CCIR/EEA/EIA tone-frequency tables, 5-tone frame layout, FleetSync/Quik-Call full plans [wiki_selective_calling.txt, SelCall].
- MPT 1327/Aloha frame format, codewords, CRC, MPT 1343 numbering map, 1343/Regionet/CNET/Multiax/Gong An variants beyond names [wiki_mpt_1327.txt, Numbering; Regional variants; SOURCES.md, Not found].
- LTR, SmartNet/SmartZone manuals, FleetSync, Quik-Call II manuals, TIA-603, EN 300 086 V2.1.2 / EN 300 296 final, TS 23.179 stage 2 (zip holds legacy .doc, not extracted) [SOURCES.md, Not found].
- EDACS control-channel rate/format, LTR slot/area bits, Motorola outbound/inbound word layout [wiki_edacs.txt; wiki_motorola_type_ii.txt].
- FRS/GMRS/MURS/CB receiver sensitivity, CTCSS/DCS encode tables, repeater input/output pairs beyond channel lists [ecfr_47cfr_part95.txt].
- MCPTT stage 2 procedures, floor-control message codes, codecs, crypto suites, off-network sync [text/3gpp_TS_22.179-v21.0.0.txt; SOURCES.md, Not found].
- Unitrunker project page, OMA PoC spec, MCPTT wiki page (rate-limited or missing) [SOURCES.md, Not found].

## From memory, not checked

Do not use for builds without finding a primary text. Kept separate per task rule.

- LTR trunking reportedly uses 300 bit/s sub-audible data with area/home/repeater/channel bits and idle/busy words; no LTR text was fetched.
- Common MDC sync-word and Golay polynomial values seen online are unverified here; the extracts give lengths and match rule only.
- CTCSS 38/39-tone and DCS 83/104-code consumer tables differ by vendor; the extracts warn numbers are inconsistent.
- MPT 1327 control messages (ALOHA invites, AHYQ acknowledgements, ADDR codewords) and MAP27 data formats need the MPT specs.
- Quik-Call II and single-tone frequency plans (1350/1500/1650/1800/1950 Hz) appear in the selective-calling extract but without a full plan document.
- EDACS ESK/AEGIS/ProVoice framing and LTR/EDACS scanner talkgroup formats need vendor or decoder-project docs.
- TETRAPOL air-interface rate/FEC/voice codec need the PAS; not in the short extract.
- MCPTT floor-control protocol (TS 24.379/24.380), ProSe discovery timers, and RAN QoS bearer mapping need stage 2/3 specs.
