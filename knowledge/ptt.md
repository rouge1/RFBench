# Push-to-talk radio systems

Index of the push-to-talk notes. Each `ptt-*.md` was drafted by Muse from public
documents that Haiku fetched into `docs/ptt/<system>/` (git-ignored; each folder
has a `SOURCES.md` with URLs and sha256), and every fact in a note cites its
document. Written 2026-11-02. A section headed "From memory, not checked" is not
from the documents.

| Note | System | Modulation and channel | Primary documents |
|---|---|---|---|
| [ptt-tetra.md](ptt-tetra.md) | TETRA V+D and DMO | pi/4-DQPSK, 18 ksym/s, 4 slots, 25 kHz | ETSI EN 300 392-2, -7; EN 300 396-2 |
| [ptt-p25.md](ptt-p25.md) | APCO P25 Phase 1 and 2 | C4FM / CQPSK 4800 sym/s, 12.5 kHz; H-DQPSK TDMA | secondary only (no TIA-102 text is free) |
| [ptt-dmr.md](ptt-dmr.md) | DMR Tier II and III | 4FSK 4800 sym/s, 2 slots, 12.5 kHz | ETSI TS 102 361-1 to -4 |
| [ptt-nxdn.md](ptt-nxdn.md) | NXDN 4800 and 9600 baud | 4FSK, 6.25 and 12.5 kHz | NXDN Forum TS 1-A, 1-E, 1-F (copies from a ham mirror, not the Forum) |
| [ptt-dpmr.md](ptt-dpmr.md) | dPMR and PMR446 | 4FSK, 6.25 kHz FDMA | ETSI TS 102 490, TS 102 658, EN 300 113 |
| [ptt-analog.md](ptt-analog.md) | CTCSS, DCS, selective calling, trunking, FRS/GMRS/MURS/CB, MCPTT | NBFM | ETSI EN 300 086-1, 47 CFR 95, 3GPP TS 22.179 |

## Not in the documents

No normative P25 text (TIA-102 is paid for); no AMBE+2 or IMBE codec specification
(proprietary, so none of these systems' voice can be decoded from the standards alone);
no TEA4 cipher; no Hytera or Motorola extensions; no Icom IDAS or Kenwood NEXEDGE air
interface; no TIA-603; no MPT 1343, FleetSync, Quik-Call II, LTR or SmartNet manuals.

## Recordings on the 4TB drive

`Joe_Sceptre/More_Recordings/` holds `vegas_ptt.cdif`, `vegas_ptt_460.cdif`,
`PTT_Training_Demo.cdif`, `PTT_Training_Demo_v2.cdif`,
`Prac_Ex_34_35_PTT_training_demo_v2(2).cdif` and `Tetra_Demo.cdif`. They have not
been opened for these notes; the notes are checked against the documents only, not
against a signal.

## Checks made

The sync words and training sequences in the notes were compared with the document
texts by script (hex and bit strings): all found except one TETRA bit string, not
yet looked at. TETRA training sequence y1..y38 and DMR BS sync 755FD7DF75F7 /
DFF57D75DF5D were read in the spec by hand. The rest are as cited, unreviewed by a
second model.
