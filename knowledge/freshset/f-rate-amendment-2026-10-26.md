# F + rate confirmatory: SDR's amendment of 2026-10-26 (recorder fault rule)

A post-hoc amendment to SDR's part of bluey-ox-walker's F + rate confirmatory (its registration on branch
`feat/f-rate-confirm`, freeze A commit 3d2a9b93aab3e5b77017078b68cedf3746d0ac9c), next to SDR's statements in
`knowledge/freshset/f-rate-confirm-sdr-statements.md` (sha256
185644d19f650f4e4eb5cb830871eb4d2301605edf036042b45961cb04905bdf), which this does not edit. Written on 2026-10-26 by
SDR-helper, after the scored plays r01, r01b and r02. **The fault, r02 and everything below were seen before this text
was written.** It takes effect only once bluey's orchestrator approves it and the owner gives the go in SDR's session.

## What was seen

- Three recordings from the 2026-10-25 sitting were spoiled:
  - cal-13B6FA_1: 4142 of 4948 bursts aligned, cut part way;
  - cal-66C0A6_1: the shot was not found;
  - r02: SNR 17.77 dB, with a spread of 2.51 dB over the shot's parts.
  r02 was decided DISCARD_STEP: relevels 2, next level −67 dBm.
- Each recording's `NAME.cf32.rec.json` said `lost_sample_overflows: 0`.
- The cause is the BB60D recorder on SDR's laptop dropping samples, not the VSG60A. The SignalHound module prints
  `[WARNING] Sample Overrun` when it drops samples and never returns readStream's overflow code. `bb60_source` counted
  only that code, so every recording reported 0.
- The warnings are in each recorder's `NAME.rec.log`:
  - 2026-10-25: cal-13B6FA_1 9, cal-13B6FA_2 12, cal-66C0A6_1 29, cal-66C0A6_2 0, diag1 20, diag2 0, diag3 5, r01 1,
    r01b 11, r02 14;
  - 2026-10-23: r02 1 and r09 1, of 16 recordings;
  - 2026-10-20: 0 in 12 recordings.
- SDR's laptop is the recorder host. During the sitting it was at load average 6 to 10: another session's tests, plus
  SDR-helper's own snr, review and smoke runs. It ran the powersave governor with C-states down to C10.
- worklaptop1, the player host, had no relevant change.
- bluey's heavy jobs ran on worklaptop1, never on the recorder host. They were not running at the first calibration
  (load 0.00 at 23:34Z), and SDR-helper waited for them to finish before diag1.
- The recorder host's load was **not logged** before any play. worklaptop1's load was checked by hand before some plays
  but not logged.
- Earlier reports from SDR-helper and from the Grok review blamed USB streaming on the VSG side. Both rested on the
  "0 lost" and are withdrawn. Moving the VSG60A to USB 4-3 and disabling C-states on worklaptop1 did no harm, but
  neither is shown to have fixed anything.
- An overrun does not always spoil a recording. r01b and cal-13B6FA_2 had warnings and aligned whole, and so did the
  two 2026-10-23 recordings: r02 5050 of 5050 bursts, r09 5559 of 5559, no sample-sized step, sems 0.0082 and
  0.0083 dB. A drop outside the 10 s shot window of the 30 s capture is harmless, but nothing in the samples says
  where it fell.

## The rules this amendment adds

**1. A Sample Overrun is a recorder fault (rule (a), chosen by bluey's orchestrator).**
- From this amendment on, a recording during which the driver printed any `Sample Overrun` is a recorder fault, and so
  is a recording whose recorder could not count them (`overruns_counted` not true).
- A recorder fault was already a rule of `bt_bench_session.py` (`lost_sample_overflows` or `adc_overflows` non-zero).
  What changes is that the count now sees the drops:
  - `bt_ota_hop_check.record` sets `lost_sample_overflows` = readStream overflows + overruns;
  - `sample_overruns`, `readstream_overflows` and `overruns_counted` are written beside it;
  - all four are logged in the session's `recorder` line.
- A recorder fault goes to the decision file as `recorder_fault=True` with no snr numbers. `snr` is not run.
  `LevelSession.decide` returns RETAKE_SAME_LEVEL, which uses the slot's one retake and no re-level.
- The rule does not depend on any F or P result, and needs no judgement about where a drop fell.
- It costs retakes on recordings that would have aligned: r01b, with 11 warnings, would have been a fault under it.

**2. The snr spread is a backstop, with thresholds fixed now.**
- When `snr` has no problem but its `snr_db_sem` is over **0.10 dB**, or is None or not a number, `finish` refuses the
  level:
  - the snr line is logged with `problem` set, and `snr_db`, `snr_db_min` and `snr_db_max` set to None;
  - the measured numbers are kept under `refused_snr_db`, `refused_snr_db_min` and `refused_snr_db_max`;
  - the decision gets `snr_refused=True` and no numbers, which gives RETAKE_SAME_LEVEL.
- The decision file takes a refusal or numbers, never both: a fault flag with numbers raises, which would be a halt.
- `snr_db_sem` is now logged in every snr line.
- How sem is computed: `bt_bench.snr` takes the median channel SNR over each of `SNR_PARTS` = 4 quarters of the shot;
  sem is their standard deviation over √3.
  - The code is in `scripts/bt_bench.py`: `snr`, with `_cells`, `_block_cells`, `_refine` and `_xcorr_valid`.
  - It is unchanged since ad81b63 (2026-10-19); this branch does not touch `bt_bench.py`, sha256
    e0cd0501fbe275674fb95453da07764f3b28f266d8f23410100310f12ed8cc19.
  - It reads the recording, the shot's samples and the shot sidecar's `center_mhz` and per-burst `channel_mhz` and
    `in_shot`. These are the inputs snr has always read before the decision. No aligner output and no carrier value
    is read, so nothing that reaches stage 1.
- **Evidence for the threshold:** `knowledge/freshset/f-rate-sem-survey.txt`, sha256
  8057340fdaeae18f07529f3fbee52e639180934321236da43e908a32e384d922, written by
  `python -B scripts/bt_bench_sem_survey.py` (sha256 37e312661b995d1dc73203be89344547bf3adb64d33e59ee182e445123a6e14c).
  - It covers every bench snr result on record, deduplicated by bytes: 43 recordings.
  - The 40 that are not refused have a largest sem of 0.0151 dB (2026-10-23 r06), 6.6 times under the threshold. They
    include today's three diagnostic plays (0.0068, 0.0039, 0.0100), r01b and 2026-10-23 r15 (sem 0.0057; its
    0.50-sample step is not this fault).
  - The 3 refused are exactly the 3 spoiled recordings: cal-13B6FA_1 1.247, cal-66C0A6_1 none (NaN), r02 2.508.
  - Observed false-positive rate: 0 of 40.
  - Each row's sem can be recomputed from its recording with `python scripts/bt_bench.py snr CAPTURE SHOT --out DIR`.

**3. r02 is re-ruled as a recorder fault (as bluey specified).**
- A new logged step, `bt_bench_session.py amend r02 --amendment <this file>`, replays r02 with
  `recorder_fault=True` and no snr numbers, in place of its logged inputs.
- r02 had 14 overruns, so it is a recorder fault under rule 1.
- The result, dry-run on copies of the real session logs with the frozen decision file (sha256 4bc50b6e…):
  - RETAKE_SAME_LEVEL;
  - level_used −69 and level_next −69 dBm;
  - relevels 1 (r01's re-level stands);
  - state None;
  - check_order passes and the chain holds.
- The r02 decision line stays in the log as written. The `amend` line after it carries:
  - this file's sha256;
  - the decision it supersedes;
  - the replayed step keys;
  - the decision file's identity.
- Every later replay checks both lines: the original decision against its own inputs, and the amend against the
  amended ones. A hand-edited amend line, or an edited superseded decision, is refused even with the chain re-made.
- `check_order` allows an amend only:
  - of a decision in RETAKE_SAME_LEVEL or DISCARD_STEP (not a KEEP or a halt);
  - once per recording;
  - after its copy and before the next start;
  - with exactly these inputs and an amendment sha256.
- The slot's retake is then the next recording: slot 2 at −69 dBm.

**4. These are prospective only.**
- r01b (11 warnings, aligned whole, slot 1's retake) stays accepted. This was decided with its warnings seen.
- r01 (1 warning, DISCARD_STEP) and its re-level stand.
- Nothing recorded before this amendment is discarded or re-ruled except r02, by rule 3.
- The 2026-10-23 recordings are not part of this registration; their two warnings are reported above.

**5. What happens if a retake also faults.**
- One retake per slot and the 40-attempt cap are unchanged.
- **Under the frozen decision file, a second non-KEEP in a slot does not just leave the slot absent: it makes the
  whole session NOT DECIDABLE.** `LevelSession.decide`: `nonkeep >= 2` sets `state = "NOT DECIDABLE"`. After that,
  `bt_bench_session.py start` refuses every recording.
- Checked on the dry-run log: a second recorder fault in slot 2 gives NOT DECIDABLE, and so does an out-of-band retake.
- So "the slot is absent, no third attempt, G-align decides" is **not** what the code does. Under rule (a), one slot
  with two recordings hit by overruns ends the sitting. **bluey to confirm which wording stands**; the code is frozen
  and SDR does not change it.
- This is why a quiet recorder host is required, not just preferred.

**6. Recorder-host conditions for every remaining play.**
- On SDR's laptop: the performance governor and deep C-states off. The owner runs
  `sudo cpupower frequency-set -g performance && sudo cpupower idle-set -D 2`; SDR-helper cannot run sudo.
- The other session on the laptop (fm-rec2) has agreed to run nothing heavy from SDR's "sitting starts" message to its
  "sitting ended" message.
- SDR-helper runs no snr reviews, Grok or smoke runs during a recording.
- Every `start` line now logs the recorder host's load average (`load_avg`). SDR-helper also checks worklaptop1's
  load before each play.

**7. Naming in the first delivery** (bluey's ruling):
- Crops are named rNN by **slot**: slot 1's accepted crop, r01b, is delivered as `r01.cf32`.
- The registration's "NN = play index" is read as slot index.
- The attempt-to-slot map goes in the second delivery, with the play order.

**Dropped.** The VSG60A items (`vsgGetUSBStatus` in the player's sidecar, `vsgEnablePowerSavingCpuMode`) are left out.
A USB-status fault would change a decision input, and the cause is not on the VSG side.
- `bt_bench_play.py` (7ca87aeb…) and `apps/vsg_sink.py` (493e0c19…) are unchanged, so worklaptop1 needs no update.
- The wrong "plays from its own memory" comment in `vsg_sink.py` will be fixed after the sitting.

**Unchanged.** The SNR band (20 ± 1 dB), the range rule, the aligner and its refusal rules (an aligner refusal after a
KEEP means not accepted, with no retake), the 40-attempt cap, one retake per slot, the play order (sha256 f0d4727f…),
the shots and their hashes, and the decision and allowed-keys files.

## Code (SDR branch `feat/frate-overrun-amend`)

| file | before | after |
|---|---|---|
| `apps/bb60_source.py` | f0021948226d3b73217df50e00162cfb727ef520085b8f937e09ccfe8e3e1806 | 6c7836888670da936e209f6ccc3eb2605232ff07c47386d5f6417bb5caa010ca |
| `scripts/bt_ota_hop_check.py` | 8d1def95f8caa8fb7799cabbce02d37c2cd4386fd7e48b0d6b6d6e907e9ab23b | e96819b46dc327ca765ec79a2f281fc97bbedd7c6fcbf3e78f5a7d4412ba1254 |
| `scripts/bt_bench_session.py` | 0ca31485f08c8cb9cfe027de84f987c090bbd7f53bcac4102f53f0e2039b892f | 726a5de816fc600009ce4213c7dfd26c5187356b669802b223b9a8e9c243a820 |
| `scripts/bt_bench.py`, `scripts/bt_bench_play.py`, `apps/vsg_sink.py` | unchanged | unchanged |

- `bb60_source`: the stderr filter (and the log handler) counts each line containing "overrun" with
  `overrun_count()`, reset by `reset_overflows()`, and still passes the line through. A source records whether its
  output was filtered (`overruns_counted`). The filter's drain waits up to 5 s, up from 1 s.
- `bt_ota_hop_check.record`: the four keys above, read after `tb.run()`.
- `bt_bench_session.py`:
  - `finish`: the overrun and uncounted-recorder faults and the spread refusal;
  - `amend`;
  - amended decisions in every replay (`_ruled`, `_decisions`, `_session`);
  - `check_order`'s amend rules;
  - `load_avg` in `start`.

## No-radio tests (all pass)

- `python -B scripts/test_bb60_overrun.py ~/bench-raw/*/*.rec.log`:
  - 500 warnings written to fd 2 through the real filter: 500 counted after release, all passed through;
  - an ADC overflow is not counted as an overrun;
  - `bt_ota_hop_check.record` through GNU Radio's own scheduler, with a fake device that prints warnings while
    streaming: they are all in `sample_overruns` and `lost_sample_overflows`;
  - all 26 real recorder logs: each count equals its "Sample Overrun" lines (103 in all).
- `python -B scripts/test_bt_bench_session.py`, the earlier tests plus:
  - overruns → recorder fault, no snr, RETAKE_SAME_LEVEL;
  - an uncounted recorder → fault;
  - the r01/r01b/r02 pattern replayed and r02 amended to RETAKE_SAME_LEVEL at the earlier level with relevels back
    to 1;
  - amend refused: before the copy, of an earlier recording, twice, or with no amendment file;
  - the retake starts at the earlier level, and a start in another slot is refused;
  - verify passes on the amended session;
  - an edited amend line, an edited superseded decision, or amend inputs changed (chain re-made): refused by the
    replay or the order;
  - spread 0.01 and 0.10 kept; 0.11, 2.5, None and NaN refused, with the numbers moved aside and NaN logged as None;
  - a finish with a 2.5 dB spread: snr refused, RETAKE_SAME_LEVEL, the order holds;
  - check_order breaches: an amend of a KEEP, before the copy, twice, after the next start, with snr numbers, with no
    sha, or of a halt.

## After approval

1. The user merges the branch.
2. SDR's laptop is at the merge commit. Its pinned files are checked against the table, and the r02 amend runs on the
   real logs.
3. Then the owner's go in SDR's session.
4. Then fm-rec2 is sent "sitting starts", the host settings are applied, and r02's retake (slot 2, −69 dBm) is played,
   followed by slots 3–20, the noise-only recording, and the first delivery.
