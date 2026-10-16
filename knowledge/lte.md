# LTE: finding any downlink cell and its operator from IQ - the procedure, the numbers, the tools, and what is not known yet

Research of 2026-10-16 (Grok and Muse through the swarm, two angles each: specs, tools), then checked
against the 3GPP/ETSI PDFs themselves where marked **checked**. Written for **any LTE downlink** - any band,
1.4 to 20 MHz, FDD or TDD - with one worked example at the end (a 10 MHz Band 13 carrier). Nothing here
has been run on the bench: no decoder is installed, and no LTE recording has been made. Read with
[radios.md](radios.md) and [signal-hound-specs.md](signal-hound-specs.md) for the BB60D.

## The short answer

- **Only a downlink can name a cell.** PSS/SSS give the PCI, the PBCH gives the MIB (bandwidth, PHICH,
  system frame number), and SIB1 (found through a PDCCH with SI-RNTI 0xFFFF, carried on the PDSCH) gives
  the **PLMN (MCC+MNC), tracking area code, 28-bit cell identity and band**. All of it is sent in the clear.
  An uplink burst carries none of it: it shows that a phone is transmitting, how wide, how often, and
  nothing else.
- **You do not need to know the bandwidth, the band or the cell in advance.** Cell search runs on the central
  6 resource blocks (1.08 MHz) whatever the carrier width; the MIB then says how wide the carrier is.
- **You do need the right centre frequency to within the 100 kHz raster** and a recording wide enough for the
  carrier (table below).
- **This repo has no LTE decoder** and nothing in `apps/` or `scripts/` touches LTE.
- **Shortest path (untried):** record the carrier on the BB60D, resample to the decoder's rate, run
  srsRAN 4G. See "Tools" and "A recipe".

## The documents

PDFs are kept **locally in `docs/lte/`** (gitignored: this repository is public and the standards are not
ours to republish, as with the Bluetooth Core spec). ETSI publishes the 3GPP text free of charge; the
files were downloaded 2026-10-16 from
`https://www.etsi.org/deliver/etsi_ts/<range>/<number>/<version>_60/`.

| Spec | Version kept | What to read |
|---|---|---|
| TS 36.211 physical channels and modulation | 19.3.0 (2026-04) | 4.1 and 4.2 frame types, 6.6 PBCH, 6.7 PCFICH, 6.8 PDCCH, 6.10 reference signals, **6.11 PSS/SSS**; 5.x uplink |
| TS 36.212 multiplexing and coding | 19.3.0 | **5.3.1** BCH and the PBCH CRC mask (Table 5.3.1.1-1), 5.3.2 DL-SCH, 5.3.3 DCI |
| TS 36.213 physical-layer procedures | 19.5.0 (2026-09) | 4.1 cell search, 7 PDSCH, 9.1 PDCCH |
| TS 36.331 RRC | 19.4.0 and 15.23.0 | **5.2.1.2** MIB/SIB1 scheduling, **6.2.2** MIB and SIB1 ASN.1 |
| TS 36.104 base-station RF | 19.2.0 | **Table 5.5-1 every band**, Table 5.6-1 resource blocks, **5.7.3 / Table 5.7.3-1 EARFCN** |
| TS 36.101 UE RF | 19.6.0 | per-band channel bandwidths |
| TS 36.300 stage 2 | 19.2.0 | architecture, system information |
| TS 36.321 MAC | 19.3.0 | Table 7.1-1 RNTIs |
| TS 23.003 numbering | 19.8.0 | PLMN = MCC (3 digits) + MNC (2 or 3), clause 12.1 |

SHA-256 (first 16 hex): 36.211 `2e0a9d59af9b5e3e`, 36.212 `e7f62a9a9217470c`, 36.213 `d1aea774eec2f7d6`,
36.331 v19.4.0 `175d27032dbda404`, v15.23.0 `7f254436bba86b4f`, 36.104 `f2875c85adac2e00`,
36.101 `ba3f96ed005a93c0`, 36.300 `df4a13a0d16e0845`, 36.321 `8eb6dada33d788a0`, 23.003 `0e136c8a3d8b80d5`.

## Cell search, step by step (any carrier)

- **Frame.** 10 ms = 10 subframes = 20 slots; 30.72 MHz = 1/Ts. Subcarrier spacing 15 kHz; a resource block is
  12 subcarriers (180 kHz); normal CP has 7 OFDM symbols per slot (extended CP 6).
- **Bandwidth** (36.104 Table 5.6-1):

  | Channel | 1.4 MHz | 3 | 5 | 10 | 15 | 20 |
  |---|---|---|---|---|---|---|
  | Resource blocks | 6 | 15 | 25 | 50 | 75 | 100 |
  | Occupied (RB x 180 kHz) | 1.08 MHz | 2.7 | 4.5 | 9.0 | 13.5 | 18.0 |
  | UE sample rate (15 kHz x FFT) | 1.92 MS/s | 3.84 | 7.68 | 15.36 | 23.04 | 30.72 |

- **PSS** (36.211 6.11.1): Zadoff-Chu, root 25 / 29 / 34 for N_ID(2) = 0 / 1 / 2 (**checked**), 62
  subcarriers around DC (72 reserved), the same on any bandwidth. **FDD:** the last OFDM symbol of slots 0 and 10.
  **TDD (frame type 2):** the third OFDM symbol of subframes 1 and 6 (**checked**, 6.11.1.2). Gives 5 ms timing
  and N_ID(2).
- **SSS** (6.11.2): two length-31 sequences, swapped between the two halves of the frame. **FDD:** the symbol
  just before the PSS (symbol N-2 of slots 0 and 10). **TDD:** the last symbol of slots 1 and 11 (**checked**),
  so the PSS-SSS spacing tells FDD from TDD. Gives the 10 ms boundary and N_ID(1) (0..167).
  **PCI = 3 * N_ID(1) + N_ID(2)**, 504 values.
- **PBCH** (6.6): QPSK on the central 72 subcarriers, slot 1 of subframe 0, four OFDM symbols, repeated over
  four frames (40 ms). The 16-bit CRC is masked by the antenna-port count: 1 port all 0, 2 ports all 1,
  4 ports 0101... (**checked**, 36.212 Table 5.3.1.1-1) - a successful decode also tells the ports.
  The MIB (36.331 6.2.2, **checked** in v19.4.0): `dl-Bandwidth` (n6, n15, n25, n50, n75, n100), `phich-Config`,
  the 8 most significant bits of the SFN (the 2 low bits come from which frame of the 40 ms), later releases'
  bits for BL/CE and a part-EARFCN, one spare bit.
- **SIB1** (36.331 5.2.1.2, **checked**): first copy in subframe 5 of frames with SFN mod 8 = 0, repeated in
  subframe 5 of every even frame (80 ms period). A PDCCH whose CRC is scrambled with **SI-RNTI = 0xFFFF**
  (36.321 Table 7.1-1) points at it; it is on the PDSCH (DL-SCH, CRC-24A + turbo code), not the BCH. The PDCCH
  position comes from the PCFICH. SIB1 carries `plmn-IdentityList`, `trackingAreaCode` (16 bits),
  `cellIdentity` (28 bits), `freqBandIndicator`, scheduling of the other SIBs. SIB2 carries `ul-Bandwidth`
  and the uplink frequency.
- **Carrier aggregation and several carriers:** every component carrier is a separate downlink signal; search each at its own
  centre (what a secondary carrier broadcasts was not checked). Frame type 3 (LAA, unlicensed 5 GHz) exists in 36.211 and was not looked at. NB-IoT and LTE-M
  (the `-r13` fields) are different narrowband signals and are not covered here.

## From a frequency to an EARFCN (any band)

36.104 Table 5.7.3-1 gives, for every band, the lowest downlink frequency F_DL_low, an offset N_Offs-DL, and the
range of channel numbers N_DL. **F_DL = F_DL_low + 0.1 (N_DL - N_Offs-DL)** MHz; the uplink is the same with the UL
columns. Channels are on a **100 kHz raster**, and a 10 MHz carrier cannot sit in the first 50 or last 49 channels of
a band. To identify a band from a measured centre: find the rows of Table 5.5-1 whose downlink range contains it
(bands overlap: Band 12 contains 17, Band 85 contains 12), then use the PBCH's bandwidth and SIB1's
`freqBandIndicator` to settle which. FDD downlink and uplink are separated by the duplex spacing in the table
(30 MHz in Bands 12 and 17, 31 MHz below in Band 13); TDD bands share one frequency. The table has every band;
this note gives examples only, from 36.104 v19.2.0:

| Band | Uplink (MHz) | Downlink (MHz) | DL F_low / N_Offs / N_DL | Duplex |
|---|---|---|---|---|
| 12 | 699-716 | 729-746 | 729 / 5010 / 5010-5179 | FDD |
| 13 | 777-787 | 746-756 | 746 / 5180 / 5180-5279 (**checked**) | FDD |
| 14 | 788-798 | 758-768 | 758 / 5280 / 5280-5379 | FDD |
| 17 (subset of 12) | 704-716 | 734-746 | 734 / 5730 / 5730-5849 | FDD |

Band 17's offsets were read from an older release (14.12.0); the Band 13 row from 19.2.0.

## What to record, for any carrier

The BB60D's native IQ is 40 MS/s, decimated by halves; the calibrated bandwidth is 27 MHz at 40 MS/s, **17.8 MHz
at 20 MS/s** and 8.0 MHz at 10 MS/s ([signal-hound-specs.md](signal-hound-specs.md)). Occupied bandwidth must fit:

| LTE channel | Occupied | BB60D setting | Why |
|---|---|---|---|
| 1.4, 3, 5 MHz | 1.08, 2.7, 4.5 MHz | 10 MS/s (8.0 MHz) or higher | fits |
| 10 MHz | 9.0 MHz | **20 MS/s** (17.8 MHz) | 10 MS/s is too narrow |
| 15 MHz | 13.5 MHz | 20 MS/s (17.8 MHz) | fits |
| 20 MHz | 18.0 MHz | **40 MS/s** (27 MHz) | 18.0 > 17.8: 20 MS/s clips the edges |

Centre on the carrier (the EARFCN's frequency; an off-centre capture still works but the decoder needs
a frequency offset). 40 MS/s cf32 is 320 MB/s and 20 MS/s is 160 MB/s; SIB1 repeats every 80 ms, so a second or
two of recording is enough. Check the input level (a weak carrier or an ADC overload both fail).

**Resampling ratios** `scipy.signal.resample_poly(x, up, down)`: the ratio is always
(target rate) / (source rate); all of these reduce to a denominator of 125 or 25:

| Target rate (MS/s) | from 40 MS/s | from 20 MS/s | from 10 MS/s |
|---|---|---|---|
| 1.92 (cell search) | 6/125 | 12/125 | 24/125 |
| 3.84 | 12/125 | 24/125 | 48/125 |
| 5.76 | 18/125 | 36/125 | 72/125 |
| 7.68 | 24/125 | 48/125 | 96/125 |
| 11.52 | 36/125 | 72/125 | 144/125 |
| 15.36 | 48/125 | 96/125 | 192/125 |
| 23.04 | 72/125 | 144/125 | 288/125 |
| 30.72 | 96/125 | 192/125 | 384/125 |
| 19.2 (LTE-Cell-Scanner HackRF files) | 12/25 | 24/25 | 48/25 |

Keep the output complex64.

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

**srsRAN sample-rate trap** (from `phy_common.c`): by default each bandwidth runs at a shorter rate than the UE
rate in the table above, and at the UE rate with the standard-rate build option (`-DUSE_LTE_RATES=ON`,
`--expert.lte_sample_rates=true`, or `-Q` in the examples):

| PRB | 6 | 15 | 25 | 50 | 75 | 100 |
|---|---|---|---|---|---|---|
| srsRAN default (MS/s) | 1.92 | 3.84 | 5.76 | 11.52 | 15.36 | 23.04 |
| standard (MS/s) | 1.92 | 3.84 | 7.68 | 15.36 | 23.04 | 30.72 |

A file radio's `base_srate` has to be an integer multiple of every rate the stack asks for, including 1.92 MS/s
for the cell search. Pick the row that matches the carrier's PRB count, and do not reuse a rate from another row
(23.04 is a multiple of 11.52 but not of 15.36).

## A recipe (untried, any carrier)

1. Find the carrier's centre from a sweep (the middle of the occupied block, snapped to the 100 kHz raster); note
   its width and compute the EARFCN from the table above.
2. Record 1-2 s at that centre with the BB60D setting from the "What to record" table, cf32.
3. Resample to the srsRAN rate for the PRB count you expect (a first guess from the occupied width; if unsure,
   resample to 1.92 MS/s for the PCI and MIB first, since the cell search needs only the central 6 blocks).
4. Get the PCI from the PSS/SSS (`synch_file -i <file> -l <frame length> -s <FFT size>`), then the MIB's
   bandwidth and ports from the PBCH; redo the resample for the real bandwidth if the guess was wrong.
5. `pdsch_ue -i <file> -p <PRB> -P <ports> -c <PCI> -r ffff` decodes the SI-RNTI blocks (for TDD add the
   `-T`/`-G` configuration options). Low PDSCH BLER means the SIB1 bits are good; the PLMN still has to be read
   from the transport block (Wireshark, a MAC-LTE pcap, or the ASN.1), since `pdsch_ue` does not print it. Or try
   `srsue` with the file radio and look for `PLMN Selected` in its log.
6. Fallback: JiaoXianjun's `CellSearch --loadbin` after converting to its file format; MATLAB if licensed.

## What passive reception can and cannot show, and the law

- **Downlink:** PCI, cell timing, CP, bandwidth, PHICH config, SFN, antenna ports, PLMN, TAC, cell identity,
  band, uplink bandwidth and frequency (SIB2). Names the cell and the operator; no subscriber.
- **Uplink:** presence, bandwidth and burst timing of SC-FDMA transmissions. To follow a phone you would need
  its scheduling from the downlink PDCCH (C-RNTI, a temporary radio identity, not an IMSI); user data is
  ciphered (TS 33.401, not opened).
- **Law (US only, not legal advice, from Cornell's text of 18 USC 2510/2511 and 47 USC 605, not any case):**
  a radio communication is "readily accessible to the general public" only if it is not scrambled or encrypted
  and not carried on a common-carrier system; US cellular licences are marked common carrier. No
  bench-research exception for decoding LTE broadcast was found in what was read, and no court opinion was
  opened. Other countries were not looked at. **Decoding broadcast MIB/SIB1 is what every UE does, but whether
  a passive bench receiver doing it is lawful is for the owner to settle before recording.** Never transmit.
- **Operator codes** are not assigned by 3GPP. Wikipedia and mcc-mnc.org list 310-410 as AT&T Mobility and
  311-480 as Verizon (US); confirm any name against a current national list. The on-air PLMN is the answer,
  not the band's usual holder.

## Worked example: a US Band 13 carrier (the sweep that started this)

A BB60D sweep of 30-1000 MHz showed a 9 MHz block at 746-755 MHz (peak -67.8 dBm at 746.4) and two blocks at
706 and 711 MHz (-50 dBm, 3-5 MHz wide).
- **746-755 MHz is Band 13 downlink:** 9.0 MHz occupied = 50 resource blocks = a 10 MHz carrier; centre
  751 MHz = EARFCN 5180 + (751 - 746)/0.1 = **5230** (750.5 MHz would be 5225). Record at **20 MS/s**,
  resample by **72/125** to 11.52 MS/s (srsRAN default rate for 50 PRB) or **96/125** to 15.36 MS/s.
- **706 and 711 MHz are Band 12/17 uplink** (both inside 704-716): phones. Their downlink partner is 30 MHz higher
  (729-746 MHz), which the sweep did not report. Band 13's own uplink, 777-787 MHz, was not reported either.
  A 3-5 MHz blob there can be a 5 MHz carrier (4.5 MHz occupied), a 3 MHz one (2.7), or part of a wider allocation.
- **US blocks** (47 CFR 27.5): Lower 700 A/B/C = 698-704/728-734, 704-710/734-740, 710-716/740-746; Upper 700 C =
  746-757 / 776-787 (Band 13 is its inner 10+10 MHz). Auction 73 gave Verizon seven of twelve Upper C licences;
  AT&T bought most Lower B/C. Who holds the spectrum at the bench is a licence search by county.

## Not verified

- Anything run: no recording, no build, no decode.
- `srsue` with `device_name=file` (disputed above); the file format `CellSearch --loadbin` reads; the
  BB60D file-writing route (the repo reads it through `apps/bb60_source.py`; a SoapySDR module and a GNU Radio
  OOT exist at `SignalHound/`; which writes a decoder-readable file was not checked); a conda-forge or
  Ubuntu 24.04 package for srsRAN.
- The 36.101 per-band bandwidth tables (downloaded, not read); the full band table was not transcribed here;
  Band 14's and Band 17's EARFCN rows come from an older release or a researcher's reading.
- The PBCH on a TDD carrier, extended CP, and any non-US band were not exercised; only the symbol positions
  above were read from the spec.
- A sample-rate table in the specs themselves (derived from the numerology); operator-to-MNC pairs beyond
  Wikipedia and mcc-mnc.org; holder maps beyond the FCC documents the researchers opened.
