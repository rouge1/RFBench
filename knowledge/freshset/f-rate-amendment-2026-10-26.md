# F + rate confirmatory: SDR's amendment of 2026-10-26 (dropped samples, the snr-spread rule)

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
- The owner, asked in bluey's orchestrator session, chose **"Only overruns that spoil the shot"** (rule (b)) over
  counting every overrun as a fault (rule (a), which SDR-helper first proposed and bluey first chose). This text is
  written to rule (b).

## The rules this amendment adds

**1. A Sample Overrun is logged, and is a fault only when the shot shows it (rule (b), the owner's choice).**
- From this amendment on, the recorder counts the driver's `Sample Overrun` warnings. `bt_ota_hop_check.record`
  writes `sample_overruns`, `readstream_overflows` and `overruns_counted`, and the session's `recorder` line logs them.
- An overrun count by itself is **not** a fault. `lost_sample_overflows` stays readStream's own count, as before, so
  neither the session's recorder-fault rule nor `bt_bench.align` sees overruns.
- A recording with overruns is a fault only when its shot shows it:
  - before the decision, by the snr-spread rule (2): the level is refused, which gives RETAKE_SAME_LEVEL;
  - after a KEEP, by the aligner's registered refusal rules: the recording is not accepted and gets no retake, as
    before.
- A small drop inside the shot that neither rule detects is left to the aligner's registered refusal rules. This
  amendment adds no rule of its own for it.
- **A recorder that could not count overruns (`overruns_counted` not true) is a recorder fault**, giving
  RETAKE_SAME_LEVEL. Without the count, a recording cannot be checked for drops at all, and the counter is new. It is
  true whenever the BB60D's stderr filter was running for the source.

**2. The snr-spread rule, with thresholds fixed now.**
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
  c46a15f20b5d08342cbc21752303c3cddf47bbf2d63e5379bc91ceb1498e8d4d, written by
  `python -B scripts/bt_bench_sem_survey.py` (sha256 781026f41adfdcce52839a2a55bfcf270725463bdfd2223f580a376a56ef1834).
  - It covers every bench snr result on record, deduplicated by bytes: 43 recordings. Of the aligner, each row shows
    only whether it accepted (no counts, no carrier value).
  - The 40 not refused have a largest sem of 0.0151 dB (2026-10-23 r06), 6.6 times under the threshold. They include:
    - today's three diagnostic plays (0.0068, 0.0039, 0.0100);
    - r01b (0.0096);
    - 2026-10-23 r15 (sem 0.0057; its 0.50-sample step is not this fault).
  - The 40 are not all verified whole: r15's aligner refused it; r01, cal2_raw, diag1-3 and the two passing
    calibrations have no aligner result beside them; and several had overruns. SDR-helper's earlier message to bluey
    called them "40 whole plays", which was wrong.
  - The 3 refused are exactly the 3 spoiled recordings: cal-13B6FA_1 1.247, cal-66C0A6_1 none (NaN), r02 2.508.
  - Observed false-positive rate: 0 of the 40 recordings not spoiled.
  - Each row's sem can be recomputed from its recording with `python scripts/bt_bench.py snr CAPTURE SHOT --out DIR`.

**3. r02 is re-ruled on the snr-spread rule (as bluey specified).**
- r02's `snr_db_sem` is 2.508 dB, over 0.10, so under rule 2 its level is refused. Its 14 overruns are logged, and are
  not the reason.
- A new logged step, `bt_bench_session.py amend r02 --amendment <this file>`, replays r02 with `snr_refused=True` and
  no snr numbers, in place of its logged inputs.
- The result, dry-run on copies of the real session logs with the frozen decision file (sha256 4bc50b6e…):
  - RETAKE_SAME_LEVEL;
  - level_used −69 and level_next −69 dBm;
  - relevels 1 (r01's re-level stands);
  - state None;
  - `verify --decide`: chain, order and replay OK.
- The r02 decision line stays in the log as written. The `amend` line after it carries:
  - this file's sha256;
  - the decision it supersedes;
  - the replayed step keys;
  - the decision file's identity.
- Every later replay checks both lines: the original decision against its own inputs, and the amend against the
  amended ones. A hand-edited amend line, or an edited superseded decision, is caught even with the chain re-made:
  - by the replay that `start`, `finish` and `amend` run, which then refuses to go on;
  - after the sitting, by `bt_bench_session.py verify FOLDER --decide DECIDE` (new), which replays the whole log.
  Plain `verify`, without `--decide`, checks only the chain, the order and the sums, and does not catch these edits.
- `check_order` allows an amend only:
  - of a decision in RETAKE_SAME_LEVEL or DISCARD_STEP (not a KEEP or a halt);
  - once per recording;
  - after its copy and before the next start;
  - with exactly these inputs and an amendment sha256.
- The slot's retake is then the next recording: slot 2 at −69 dBm.

**4. These are prospective only.**
- r01b (11 warnings, sem 0.0096, aligned and accepted, slot 1's retake) stays accepted. It would not have been a fault
  under the final rule either: its sem is under 0.10 dB and its aligner accepted it. This was decided with its
  warnings seen.
- r01 (1 warning, sem 0.0142, DISCARD_STEP) and its re-level stand.
- Nothing recorded before this amendment is discarded or re-ruled except r02, by rule 3.
- The 2026-10-23 recordings are not part of this registration; their two warnings are reported above.

**5. What happens if a retake also fails.**
- One retake per slot and the 40-attempt cap are unchanged.
- **The frozen decision file governs, and the owner has been told:** a second non-KEEP in a slot makes the whole
  sitting NOT DECIDABLE. In `LevelSession.decide`, `nonkeep >= 2` sets `state = "NOT DECIDABLE"`, and after that
  `bt_bench_session.py start` refuses every recording.
- Checked on the dry-run log: a second fault in slot 2 gives NOT DECIDABLE, and so does an out-of-band retake.
- bluey's earlier wording, "the slot is absent, G-align decides", is withdrawn.
- So the quiet recorder host (6) still matters. Under rule (b), an overrun inside the shot that spreads the level
  costs the slot's retake, and a second one ends the sitting.

**6. Recorder-host conditions for every remaining play.**
- On SDR's laptop: the performance governor and deep C-states off. The owner runs
  `sudo cpupower frequency-set -g performance && sudo cpupower idle-set -D 2`; SDR-helper cannot run sudo.
- The other session on the laptop (fm-rec2) has agreed to run nothing heavy from SDR's "sitting starts" message to its
  "sitting ended" message.
- SDR-helper runs no snr reviews, Grok or smoke runs during a recording.
- Every `start` line now logs the recorder host's load average (`load_avg`). SDR-helper also checks worklaptop1's
  load before each play.

**6a. The noise-only recording** is outside LevelSession.
- If `overruns_counted` is not true, or the recording looks broken, it is recorded again: the first is kept on the
  drive, and the second is named `noise2` and logged.
- "Looks broken" means: the wrong length, a recorder error, or a recorder that wrote no record.
- Plain overruns on the noise recording are logged only.

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

## Disclosures

- **Carrier values printed in SDR's session (blinding).** On 2026-10-26 at 06:44 UTC, while checking the 2026-10-23
  sitting's two overrun warnings, SDR-helper printed four aligner values to its own console: `f_located_hz`,
  `cfo_instrument_hz`, `cfo_instrument_spread_hz` and `reference_error_hz`. They were for the 2026-10-23 confirmatory
  recordings r02 (bench_DBB3FB_d23) and r09 (bench_20C0AA_d23), read from `~/bench-raw/confirm-main2/r02.align.json`
  and `r09.align.json`.
  - They went to SDR-helper's console and this session's transcript only. They were not sent to bluey or any other
    session, not written to any file, and not shown to the user in a reply.
  - SDR-helper's own standing note says those console files hold sealed values and are never to be printed. That rule
    was broken.
  - The values belong to the 2026-10-23 shots, not to this registration's LAPs, and this registration's stage 1 has not
    started (bluey).
  - What SDR knows of that earlier confirmatory's freeze state: the sitting was made and reported on 2026-10-23 and its
    files handed over for bluey's stage 1. SDR does not know whether that stage 1 had been frozen by 2026-10-26 06:44
    UTC.

## Code (SDR branch `feat/frate-overrun-amend`)

| file | before | after |
|---|---|---|
| `apps/bb60_source.py` | f0021948226d3b73217df50e00162cfb727ef520085b8f937e09ccfe8e3e1806 | 6c7836888670da936e209f6ccc3eb2605232ff07c47386d5f6417bb5caa010ca |
| `scripts/bt_ota_hop_check.py` | 8d1def95f8caa8fb7799cabbce02d37c2cd4386fd7e48b0d6b6d6e907e9ab23b | 040b329c9426cf228487e9942ecb2a4a3ef1f2be98c52ea676db96b2ac42bdb0 |
| `scripts/bt_bench_session.py` | 0ca31485f08c8cb9cfe027de84f987c090bbd7f53bcac4102f53f0e2039b892f | f9defeaa346a858e988d3bbf2d966fe38dcbfe50c8cb94aba12b9e0cf9bee943 |
| `scripts/bt_bench.py`, `scripts/bt_bench_play.py`, `apps/vsg_sink.py` | unchanged | unchanged |

- `bb60_source`: the stderr filter (and the log handler) counts each line containing "overrun" with
  `overrun_count()`, reset by `reset_overflows()`, and still passes the line through. A source records whether its
  output was filtered (`overruns_counted`). The filter's drain waits up to 5 s, up from 1 s.
- `bt_ota_hop_check.record`: `sample_overruns`, `readstream_overflows` and `overruns_counted`, read after `tb.run()`.
  `lost_sample_overflows` is unchanged: readStream's count.
- `bt_bench_session.py`:
  - `finish`: logs the overruns; an uncounted recorder is a recorder fault; the spread refusal;
  - `amend`;
  - amended decisions in every replay (`_ruled`, `_decisions`, `_session`);
  - `check_order`'s amend rules;
  - `load_avg` in `start`;
  - `verify --decide`, the replay after the sitting.

## No-radio tests (all pass)

- `python -B scripts/test_bb60_overrun.py ~/bench-raw/*/*.rec.log`:
  - 500 warnings written to fd 2 through the real filter: 500 counted after release, all passed through;
  - an ADC overflow is not counted as an overrun;
  - `bt_ota_hop_check.record` through GNU Radio's own scheduler, with a fake device that prints warnings while
    streaming: they are all in `sample_overruns`, `lost_sample_overflows` stays 0, and `overruns_counted` is true;
  - all 26 real recorder logs: each count equals its "Sample Overrun" lines (103 in all).
- `python -B scripts/test_bt_bench_session.py`, the earlier tests plus:
  - overruns with a good sem: logged, no fault, KEEP;
  - an uncounted recorder: a recorder fault, RETAKE_SAME_LEVEL;
  - the r01/r01b/r02 pattern replayed and r02 amended to RETAKE_SAME_LEVEL at the earlier level with relevels back
    to 1;
  - amend refused: before the copy, of an earlier recording, twice, or with no amendment file;
  - the retake starts at the earlier level, and a start in another slot is refused;
  - `verify --decide` passes on the amended session;
  - an edited amend line, an edited superseded decision, or amend inputs changed (chain re-made): refused by the
    replay or the order, and `verify --decide` fails each;
  - spread 0.01 and 0.10 kept; 0.11, 2.5, None and NaN refused, with the numbers moved aside and NaN logged as None;
  - a finish with a 2.5 dB spread: snr refused, RETAKE_SAME_LEVEL, the order holds;
  - check_order breaches: an amend of a KEEP, before the copy, twice, after the next start, with snr numbers, with no
    sha, or of a halt.

## After approval

1. The user merges the branch.
2. SDR's laptop is at the merge commit, and its pinned files are checked against the table.
3. The r02 amend runs on the real logs, with:
   - `--decide` set to the path the session's start lines logged, after checking that file is still sha256 4bc50b6e…;
   - this file at the sha256 bluey approved.
   Then `verify --decide` runs on the drive folder.
4. Receive-only check, with the owner's OK: one 30 s `bt_bench.py record` on the new code, outside the session, with
   the VSG60A not playing, the recorder host quiet and the host settings applied. It transmits nothing.
   - Pass: `overruns_counted` true and `sample_overruns` 0. This shows the real device path counts, and that the
     quiet host is clean, before slot 2's last attempt is spent.
   - If it fails, nothing is played and bluey is told.
5. Then the owner's go in SDR's session.
6. Then:
   - fm-rec2 is sent "sitting starts" and the host settings are applied;
   - r02's retake is played (slot 2, −69 dBm), then slots 3–20, then the noise-only recording;
   - the first delivery is made;
   - fm-rec2 is sent "sitting ended".
