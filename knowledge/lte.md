# LTE: finding a cell and its operator from IQ - specs, tools, bands, and what is not known yet

Research of 2026-10-16 (Grok and Muse through the swarm, two angles each: specs, tools), then checked
against the 3GPP/ETSI PDFs themselves where marked **checked**. Nothing here has been run on the bench:
no decoder is installed, and no LTE recording has been made. Read with [radios.md](radios.md) and
[signal-hound-specs.md](signal-hound-specs.md) for the BB60D.

**Why.** A BB60D sweep of 30-1000 MHz showed a 9 MHz block at 746-755 MHz (peak -67.8 dBm at 746.4) and
two blocks at 706 and 711 MHz (-50 dBm, 3-5 MHz wide). The first reads as a 10 MHz LTE downlink
carrier in **Band 13** (US, Verizon spectrum); the others as **Band 12/17 uplink** (phones, AT&T-held
spectrum). The question was how to name the cell and its operator.

## The short answer

- **Only a downlink can name a cell.** PSS/SSS give the PCI, the PBCH gives the MIB (bandwidth, PHICH,
  system frame number), and SIB1 (found through a PDCCH with SI-RNTI 0xFFFF, carried on the PDSCH) gives
  the **PLMN (MCC+MNC), tracking area code, 28-bit cell identity and band**. All of it is sent in the clear.
  An uplink burst (706 / 711 MHz) carries none of it: it shows that a phone is transmitting, how wide, how
  often, and nothing else.
- **The 746-755 MHz block is Band 13 downlink.** A 10 MHz carrier is 50 resource blocks = 9.0 MHz occupied,
  which is the 9 MHz seen. Centre 751 MHz = EARFCN 5230. The 706 / 711 MHz blocks are Band 12/17 uplink; their
  downlink partner is 30 MHz higher (729-746 MHz), which the sweep did not report. Band 13's own uplink is
  777-787 MHz, and the sweep notes do not say whether anything was there.
- **Shortest path (untried):** record the 751 MHz carrier on the BB60D at 20 MS/s cf32, resample to the
  decoder's rate, and run srsRAN 4G. See "Tools" and "A recipe".
- **This repo has no LTE decoder** and nothing in `apps/` or `scripts/` touches LTE.

## The documents

PDFs are kept **locally in `docs/lte/`** (gitignored: this repository is public and the standards are not
ours to republish, as with the Bluetooth Core spec). ETSI publishes the 3GPP text free of charge; the
file names below are as downloaded 2026-10-16 from
`https://www.etsi.org/deliver/etsi_ts/<range>/<number>/<version>_60/`.

| Spec | Release / version kept | What to read |
|---|---|---|
| TS 36.211 physical channels and modulation | 19.3.0 (2026-04) | 4.1 frame, 6.6 PBCH, 6.7 PCFICH, 6.8 PDCCH, 6.10 reference signals, **6.11 PSS/SSS**; 5.x uplink (SC-FDMA, PRACH) |
| TS 36.212 multiplexing and coding | 19.3.0 | **5.3.1** BCH and the PBCH CRC mask (Table 5.3.1.1-1), 5.3.2 DL-SCH, 5.3.3 DCI |
| TS 36.213 physical-layer procedures | 19.5.0 (2026-09) | 4.1 cell search, 7 PDSCH, 9.1 PDCCH |
| TS 36.331 RRC | 19.4.0 and 15.23.0 | **5.2.1.2** MIB/SIB1 scheduling, **6.2.2** MIB and SIB1 ASN.1 |
| TS 36.104 base-station RF | 19.2.0 | Table 5.5-1 bands, Table 5.6-1 resource blocks, **5.7.3** EARFCN |
| TS 36.101 UE RF | 19.6.0 | per-band channel bandwidths |
| TS 36.300 stage 2 | 19.2.0 | overall architecture, system information |
| TS 36.321 MAC | 19.3.0 | Table 7.1-1 RNTIs |
| TS 23.003 numbering | 19.8.0 | PLMN = MCC (3 digits) + MNC (2 or 3), clause 12.1; the ECI/ECGI clause |

SHA-256 (first 16 hex): 36.211 `2e0a9d59af9b5e3e`, 36.212 `e7f62a9a9217470c`, 36.213 `d1aea774eec2f7d6`,
36.331 v19.4.0 `175d27032dbda404`, v15.23.0 `7f254436bba86b4f`, 36.104 `f2875c85adac2e00`,
36.101 `ba3f96ed005a93c0`, 36.300 `df4a13a0d16e0845`, 36.321 `8eb6dada33d788a0`, 23.003 `0e136c8a3d8b80d5`.

## Cell search, step by step (numbers from the specs)

- **Frame.** 10 ms = 10 subframes = 20 slots, 30.72 MHz = 1/Ts. Subcarrier spacing 15 kHz; a resource block is
  12 subcarriers (180 kHz). Channel 1.4 / 3 / 5 / 10 / 15 / 20 MHz = 6 / 15 / 25 / 50 / 75 / 100 blocks
  (36.104 Table 5.6-1). A 10 MHz carrier occupies 9.0 MHz.
- **PSS** (36.211 6.11.1): Zadoff-Chu, root 25 / 29 / 34 for N_ID(2) = 0 / 1 / 2 (**checked**, Table
  6.11.1.1-1); last OFDM symbol of slots 0 and 10 in FDD; 62 subcarriers around DC (72 reserved). Gives
  5 ms timing and N_ID(2).
- **SSS** (6.11.2): the symbol before PSS in the same slots, two length-31 sequences swapped between the
  two halves of the frame; gives the 10 ms boundary and N_ID(1) (0..167).
  **PCI = 3 * N_ID(1) + N_ID(2)**, 504 values.
- **PBCH** (6.6): QPSK on the central 72 subcarriers, slot 1 of subframe 0, four OFDM symbols, repeated over
  four frames (40 ms). The 16-bit CRC is masked by the antenna-port count: 1 port all 0, 2 ports all 1,
  4 ports 0101... (**checked**, 36.212 Table 5.3.1.1-1) - so a successful decode also tells the ports.
  The MIB (36.331 6.2.2, **checked** in v19.4.0): `dl-Bandwidth` (n6..n100), `phich-Config`, the 8 most
  significant bits of the SFN (the 2 low bits come from which frame of the 40 ms), then later releases'
  bits for BL/CE and a part-EARFCN, one spare bit.
- **SIB1** (36.331 5.2.1.2, **checked**): first copy in subframe 5 of frames with SFN mod 8 = 0, repeated in
  subframe 5 of every even frame (80 ms period). A PDCCH whose CRC is scrambled with **SI-RNTI = 0xFFFF**
  (36.321 Table 7.1-1) points at it; it is on the PDSCH (DL-SCH, CRC-24A + turbo code), not the BCH.
  Carries `plmn-IdentityList`, `trackingAreaCode` (16 bits), `cellIdentity` (28 bits), `freqBandIndicator`,
  scheduling of the other SIBs. SIB2 carries `ul-Bandwidth` and the uplink frequency.
- **Sample rates.** A UE processes at 15 kHz x FFT size: 1.92 / 3.84 / 7.68 / 15.36 / 23.04 / 30.72 MS/s for
  1.4 ... 20 MHz. Not a table in the specs; it follows from the numerology. srsRAN uses shorter variants by
  default (below).

## The bands in play (US, FDD)

3GPP Table 5.5-1 and 5.7.3-1 (**checked** for Band 13 in 36.104 v19.2.0). EARFCN: F = F_low + 0.1 (N - N_offs).

| Band | Uplink (phone) | Downlink (cell) | Notes |
|---|---|---|---|
| 12 | 699-716 | 729-746 | 1.4 / 3 / 5 / 10 MHz; DL EARFCN 5010-5179 |
| 17 | 704-716 | 734-746 | subset of 12; 5 / 10 MHz; DL EARFCN 5730-5849 |
| 13 | 777-787 | 746-756 | 5 / 10 MHz; DL EARFCN 5180-5279; **751 MHz = 5230** |
| 14 | 788-798 | 758-768 | FirstNet (AT&T operates it); 5 / 10 MHz |

FCC blocks (47 CFR 27.5, eCFR): Lower 700 A/B/C = 698-704/728-734, 704-710/734-740, 710-716/740-746;
D and E unpaired 716-728; Upper 700 C = 746-757 / 776-787 (Band 13 is its inner 10+10 MHz). Holders (secondary):
Auction 73 gave Verizon seven of twelve Upper C licences; AT&T bought most Lower B/C. **Who holds the
spectrum at the bench is a licence search by county; SIB1's PLMN is the on-air answer.** Operator codes are not
assigned by 3GPP: 310-410 AT&T Mobility and 311-480 Verizon appear on Wikipedia and mcc-mnc.org only - confirm
against a current national list before naming one. 706 and 711 MHz are both inside the 704-716 uplink; a
3-5 MHz blob there can be a 5 MHz carrier (4.5 MHz occupied), a 3 MHz one (2.7), or part of a 10 MHz allocation;
the waveform does not say which.

## Tools

None is installed here. Licences and dates are as the researchers read them on 2026-10-16.

| Tool | Takes a recorded IQ file? | Prints PLMN? | Notes |
|---|---|---|---|
| **srsRAN 4G** (`srsran/srsRAN_4G`, AGPL-3.0, tag 25.10, commits to Sep 2026) | `pdsch_ue -i` and `synch_file -i`: cf32 binary. `cell_search`: **no**, live RF only. `srsue` with a file radio: **disputed**, see below | `srsue` logs `PLMN Selected` after SIB1; `pdsch_ue` decodes SIB1 bits at SI-RNTI but does not print the PLMN | the active project; build deps `build-essential cmake libfftw3-dev libmbedtls-dev libboost-program-options-dev libconfig++-dev libsctp-dev`; Ubuntu PPA exists |
| **LTE-Cell-Scanner** (`JiaoXianjun/`, fork of `Evrytania/`, AGPL-3.0) | `CellSearch --recbin a.bin` / `--loadbin a.bin`; MATLAB/Octave `LTE_DL_receiver('f1815.3_s19.2_bw20_..._hackrf-1.bin')` | the fork's MATLAB path decodes SIB ASN.1 (BCCH-DL-SCH) | file format is the tool's own, not cf32; RTL-SDR search at 1.92 MS/s, HackRF at 19.2 MS/s; the Evrytania repo is stale (2019) |
| **OpenLTE** (SourceForge, AGPL, alpha, last update 2021-02-14) | `LTE_fdd_dl_file_scan` (GNU Radio) | not confirmed | unmaintained; an algorithm reference |
| **gr-lte** (`kit-cel`, GPL-3.0) | GNU Radio blocks | MIB only, no SIB1 | needs GNU Radio 3.7; this repo uses 3.10 |
| FALCON, LTESniffer, OWL | their own capture formats | no | PDCCH/DCI sniffers, not what is wanted first |
| QCSuper, SCAT | no: a Qualcomm/Samsung modem's diagnostic port | SIBs from the modem | not SDR tools |
| OpenAirInterface | IQ replay exists, 40 MHz only | not for LTE file decode | full stack; heavy |
| MATLAB LTE Toolbox | "Cell Search, MIB and SIB1 Recovery" example | yes | licensed; the algorithm reference |

No maintained Python package was found that takes cf32 and prints a SIB1 PLMN (`py3gpp` is 5G NR).

**Disputed between the two researchers, not settled.** Grok read `rf_file_imp.c` in srsRAN and says
`srsue` accepts `rf.device_name=file` with `rf.device_args="rx_file=<path>,base_srate=<Hz>"` (from an issue
report in the repository). Muse says `srsue` has no file mode. What was **checked**: the file device exists
in the source (`rf_file_imp.c`, device name "file", args `rx_file`, `tx_file`, `base_srate`, cf32), and the
generic device table in `rf_imp.c` was not shown to list it. Whether `srsue` can select it is open - try it,
and fall back to `pdsch_ue`.

**srsRAN sample-rate trap** (from `phy_common.c`): 50 PRB runs at **11.52 MS/s** by default and at 15.36 MS/s
with the standard-rate build option (`-DUSE_LTE_RATES=ON`, or `--expert.lte_sample_rates=true`, or `-Q` in the
examples). 25 PRB: 5.76 / 7.68; 75 PRB: 15.36 / 23.04; 100 PRB: 23.04 / 30.72. A file radio's `base_srate` has to be
an integer multiple of every rate the stack asks for, including 1.92 MS/s for the cell search; 11.52 is, 23.04 is,
15.36 is a multiple of 1.92 too, but 23.04 is not a multiple of 15.36.

## Recording on the BB60D

- Native IQ is 40 MS/s, decimated by halves; **decimation 2 gives 20 MS/s with 17.8 MHz bandwidth**, enough for
  a 10 MHz carrier (9 MHz occupied). Decimation 4 (10 MS/s, 8.0 MHz) is too narrow. The 27 MHz window is at
  40 MS/s only ([signal-hound-specs.md](signal-hound-specs.md)).
- 20 MS/s cf32 is 160 MB/s; a second or two is plenty for SIB1 (80 ms period). Centre on the carrier
  (751.0 MHz for the whole Band 13 downlink; 750.5 MHz would put it at EARFCN 5225).
- The repo reads the BB60D through `apps/bb60_source.py`. A Signal Hound SoapySDR module and a GNU Radio OOT
  exist (`SignalHound/soapy-bb60`, `SignalHound/gr-signal-hound`); Spike is the vendor recorder. Which of these
  writes a file this decoder can read was not checked.
- **Resampling ratios** from 20 MS/s (`scipy.signal.resample_poly(x, up, down)`): to 11.52 MS/s **72/125**,
  to 15.36 **96/125**, to 1.92 **12/125**, to 23.04 **144/125**, to 19.2 **24/25**. Keep the output complex64.

## A recipe (untried)

1. Record 1-2 s at centre 751.0 MHz, 20 MS/s, cf32.
2. `y = resample_poly(x, 72, 125).astype(np.complex64); y.tofile("band13.cf32")` (11.52 MS/s, srsRAN default
   rates).
3. Get the PCI, ports and PRB count with `synch_file -i band13.cf32 -l <frame length> -s 768` (then check the
   PCI against the MIB), or with `pdsch_ue` once the PCI is known.
4. `pdsch_ue -i band13.cf32 -p 50 -P <1|2|4> -c <PCI> -r ffff` decodes the SI-RNTI blocks. Low PDSCH BLER
   means the SIB1 bits are good; the PLMN still has to be read from the transport block (Wireshark, a MAC-LTE
   pcap, or the ASN.1) because `pdsch_ue` does not print it. Or try `srsue` with the file radio and look
   for `PLMN Selected` in the log.
5. If rates cause trouble, rebuild with `-DUSE_LTE_RATES=ON` and resample with 96/125 to 15.36 MS/s.
6. Fallback: JiaoXianjun's `CellSearch --loadbin`, after converting to its file format; MATLAB if licensed.

## What passive reception can and cannot show, and the law

- **Downlink:** PCI, cell timing, CP, bandwidth, PHICH config, SFN, antenna ports, PLMN, TAC, cell identity,
  band, uplink bandwidth and frequency (SIB2). Names the cell and the operator; no subscriber.
- **Uplink:** presence, bandwidth and burst timing of SC-FDMA transmissions. To follow a phone you would need
  its scheduling from the downlink PDCCH (C-RNTI, a temporary radio identity, not an IMSI); user data is
  ciphered (TS 33.401, not opened).
- **Law (US only, not legal advice, from Cornell's text of 18 USC 2510/2511 and 47 USC 605, not any case):**
  a radio communication is "readily accessible to the general public" only if it is not scrambled or encrypted
  and not carried on a common-carrier system; the licences for these bands are marked common carrier. No
  bench-research exception for decoding LTE broadcast was found in what was read, and no court opinion was
  opened. Other countries were not looked at. **Decoding broadcast MIB/SIB1 is what every UE does, but whether
  a passive bench receiver doing it is lawful is for the owner to settle before recording.** Never transmit.

## Not verified

- Anything run: no recording, no build, no decode.
- `srsue` with `device_name=file` (disputed above); the file format `CellSearch --loadbin` reads; the
  BB60D file-writing route; a conda-forge or Ubuntu 24.04 package for srsRAN.
- The 36.101 per-band bandwidth tables (downloaded, not read); the clause 19.6 ECI text of TS 23.003.
- Operator-to-MNC pairs beyond Wikipedia and mcc-mnc.org.
- A sample-rate table in the specs themselves (derived from the numerology).
- Holder maps for Lower 700 A and for every Upper C area.
