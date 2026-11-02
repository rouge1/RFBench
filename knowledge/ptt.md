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
| [ptt-other.md](ptt-other.md) | D-STAR, Fusion, M17, MPT 1327, cellular and app PTT (MCPTT, FirstNet, Zello, Iridium), telling systems apart in IQ, Sceptre's labels, the US legal line | GMSK, C4FM, 4FSK, LTE/NR | second-hand: taken from the FM receiver project's note, with JARL and M17 specs read there |
| [ptt-analog.md](ptt-analog.md) | CTCSS, DCS, selective calling, trunking, FRS/GMRS/MURS/CB, MCPTT | NBFM | ETSI EN 300 086-1, 47 CFR 95, 3GPP TS 22.179 |

The FM receiver project (a sibling repository) keeps its own `knowledge/ptt.md`, a research note
written on 2026-11-01 for classifying signals; [ptt-other.md](ptt-other.md) carries over what it has
that these notes do not. Where both cover a system the values agree (DMR syncs, dPMR FS1, NXDN
6.25 kHz mapping, TETRA training sequences, P25 Phase 2 at 6000 sym/s). These notes add what it
marks unverified: the P25 frame sync 5575F5FF77FF (from a lab capture in the Blaze paper) and the
NXDN 12.5 kHz mode (4800 sym/s, +-800 and +-2400 Hz, TS 1-A).

## Not in the documents

No normative P25 text (TIA-102 is paid for); no AMBE+2 or IMBE codec specification
(proprietary, so none of these systems' voice can be decoded from the standards alone);
no TEA4 cipher; no Hytera or Motorola extensions; no Icom IDAS or Kenwood NEXEDGE air
interface; no TIA-603; no MPT 1343, FleetSync, Quik-Call II, LTR or SmartNet manuals.

## Recordings on the 4TB drive

`Joe_Sceptre/More_Recordings/` holds `vegas_ptt.cdif`, `vegas_ptt_460.cdif`,
`PTT_Training_Demo.cdif`, `PTT_Training_Demo_v2.cdif`,
`Prac_Ex_34_35_PTT_training_demo_v2(2).cdif` and `Tetra_Demo.cdif`. The notes above are
checked against the documents only, not against a signal; a spectrogram of the
recordings was asked of the FM receiver project's session on 2026-11-02 and is not
in yet. Recording contents, centre frequencies and tags stay out of this public
repository.

### The `.cdif` format (Sceptre's recordings)

Standard BLUE / X-Midas files, little-endian although the tag says `EEEI`
(read from the headers by this session and by the FM receiver project, which has a
numpy reader, `sceptre_blue.py`, and its own notes on it):

- 512-byte header, then the samples, then a 2048-byte keyword block (`KEY=value`,
  among them the sample rate, the RF centre, the gain to convert counts to dBm, and
  the time of the capture). So file size = 512 + data size + 2048, and the float64 at
  0x28 (data size) is the file size minus 2560. The data start is the float64 `512.0`
  at 0x20. Magic `BLUEEEEIEEEI` at 0; the writer string `IO=Sceptre`, `VER=2.0`.
- The two format letters at byte 52 give the samples: `CI` = complex int16, I then Q;
  `CF` = complex float32. The float64 at 0x38 is the capture time in seconds since
  1950-01-01 (the X-Midas epoch), and the float64 pair at 0x100 is the start time and
  the sample interval (5e-08 s = 20 MS/s for the training demo).
- Checked on the data: the first 64 MB of `PTT_Training_Demo.cdif` read as int16 I/Q have
  equal I and Q spread, no DC, and narrow spectral lines over a flat floor at
  20 MS/s; read as int8 or float32 they are not plausible. A file in `CF` form can be a
  processed channel whose gain keyword is 0, in which case the counts-to-dBm
  conversion does not apply.
- Sceptre's decoded sessions keep, beside each voice WAV, an `.ambe` file of the voice
  frames as they came off the air, before the vocoder. For DMR they are packed: 9 bytes
  (72 bits, FEC included) per AMBE+2 frame, 18 frames (162 bytes) per 360 ms
  superframe, and every file is a whole number of superframes; the most common frame
  is the AMBE+2 silence frame `B9 E8 81 52 61 73 00 2A 6B`. For NXDN they are one byte
  per bit (0 or 1), 72 bits per voice channel (VCH) frame. They are a bit-exact
  reference for a demodulator: the payload of a DMR voice burst, read on the same
  channel, slot and time, must equal them.
- Read them in chunks (a memmap) and cap memory with `systemd-run` `MemoryMax`: the
  out-of-memory killer has taken sessions that read whole recordings.

## Checks made

The sync words and training sequences in the notes were compared with the document
texts by script (hex and bit strings): all found except one TETRA bit string, not
yet looked at. TETRA training sequence y1..y38 and DMR BS sync 755FD7DF75F7 /
DFF57D75DF5D were read in the spec by hand. The rest are as cited, unreviewed by a
second model.
