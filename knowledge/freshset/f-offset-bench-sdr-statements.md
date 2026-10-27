# SDR's statements for bluey's f-offset bench

What bluey-ox-walker's `knowledge/f-offset-bench-reg.md` (draft v3, sections 9 to 11) asks SDR to state in one
committed file before its freeze. It covers the pre-freeze checks O1 to O4 and O6, what is known for O3, the decision
file that replaces the level machinery (O2), and the SDR pins. Written 2026-10-26 on branch `foffset-decide`.

No transmitter was used for any of it. The only radio opened was the recorder laptop's BB60D, receive only, for the
counter check of O4. The user approved these checks in the SDR session; the owner's radio approval for the
registration is quoted in the registration's section 14.

## 1. O1: the aligner, against its code

The aligner is `align` in `scripts/bt_bench.py` (sha256 below). Its accept rules, read from the code at that hash,
are as the registration's O1 states them:

- It reads the commanded offset from the tx sidecar's `vsg_center_mhz`.
- It searches ±10 kHz around that offset in 1 kHz steps (`CFO_SEARCH_HZ`, `CFO_STEP_HZ`), then refines the result
  from the matched bursts' phase.
- A burst is found at a normalised correlation of at least 0.4 (`MIN_NORM`).
- At least 99.9 % of the bursts must be found (`MIN_FOUND`), including every one of the first and last 20
  (`STEP_MIN_SIDE`).
- No burst may sit more than 3 samples off the clock line (`OFF_LINE_SAMPLES`), after a second look over ±4000
  samples (`W_WIDE`).
- No timing step of 0.5 samples or more (`STEP_SAMPLES`), and no bend of 0.3 (`BEND_SAMPLES`).
- The timing scatter must be at most 0.8 samples (`MAX_JITTER`).
- The recorder's `lost_sample_overflows` must be 0.

It matches the shot file's own samples against the whole 40 MS/s capture. Nothing filters the capture first, and the
offset comes off as a plain rotation. It knows nothing of P or F.

**The dry run (O1, step 2(b)).** Each run was `bt_bench.py dryrun` on a pinned shot, then `bt_bench.py align --dry`,
with `vsg_offset_hz` set to the commanded offset, the field a tx sidecar gives. Each capture was:

- rotated by the commanded offset plus a planted reference error of +2.5 kHz;
- given noise at 20.0 dB in 1 MHz (the level of −69 dBm in the confirmatory);
- given a clock offset of −0.37 ppm and a start at 0.37 of a sample.

There were 14 runs: each shot at commanded −125, −100, −75, +75, +100 and +125 kHz, and once unrotated (0 kHz,
the planted +2.5 kHz only). All 14 were accepted (exit 0, `problem` null). The table is built from the 14
`NAME.align.json` files; each range runs over a shot's 7 runs.

| | 66C0A6 | 13B6FA |
|---|---|---|
| accepted | 7 / 7 | 7 / 7 |
| bursts found / in the shot | 6524 / 6524 | 4948 / 4948 |
| off the line | 0 | 0 |
| locate_score | 0.808 to 0.810 | 0.809 to 0.811 |
| jitter (samples) | 0.386 to 0.394 | 0.394 to 0.403 |
| step (samples) | 0.065 to 0.236 | 0.081 to 0.158 |
| bend (samples) | 0.0018 to 0.0147 | 0.0015 to 0.0162 |
| ppm (planted −0.37) | −0.3701 to −0.3699 | −0.3701 to −0.3699 |
| f_located_hz − commanded (Hz) | +2489.6 to +2510.0 | +2487.5 to +2502.3 |
| reference_error_hz (planted +2500) | 2499.9 to 2501.5 | 2499.7 to 2501.4 |
| cfo_instrument_spread_hz | 192.4 to 196.6 | 187.1 to 191.6 |

In all 12 rotated runs, the located offset kept the commanded sign.

Not covered by the dry run:
- a real instrument's error near the ±10 kHz edge of the search;
- anything a real VSG retune does at these offsets.

The confirmatory's 20 recordings at ±58 and 0 kHz were all accepted.

## 2. O2: the level and the decision file

The sitting runs with `scripts/bt_bench_session.py` (unchanged, pinned below) and a new decision file,
`scripts/bt_foffset_decide.py`, with its allowed-keys file `scripts/bt_foffset_allowed_keys.py`. Tests:
`scripts/test_bt_foffset_decide.py`, all passing.

The decision file keeps the interface the session replays: `FIRST_COMMAND_DBM` and `LevelSession` with `decide`,
`halt`, `resume`, `level`, `relevels`, `capped` and `state`. Its rules follow the registration's section 2:

- **Level.** −69 dBm for every play. The level never changes, `relevels` stays 0 and `capped` stays false. The SNR of
  every recording is logged by the session (`snr_db`, its min and max, `snr_db_sem`, the noise) and is never used
  to re-level or to refuse. Any median and any spread over the channels is KEEP.
- **Faults.** These are the session's closed list, as the amendment of 2026-10-26 (rule (b)) left it:
  - the recorder lost samples, overflowed or could not count the Sample Overruns;
  - the player's sidecar is missing, not sent or in another state;
  - a fault was logged before `finish`;
  - `snr` refused the recording, by its own problem or by `snr_db_sem` over 0.10 dB or none (`SNR_MAX_SEM_DB`, in
    the session script).

  An overrun counted is not a fault.
- **Retakes.** A first fault in a slot is `RETAKE_SAME_LEVEL`. A second leaves the slot absent (`FAULT_ABSENT`): it
  is not played again, and the sitting moves on to the next slot. A fault never makes the sitting NOT DECIDABLE.
- **Aligner refusals.** A refusal after a KEEP is the session's `close`. The recording is not aligned into a crop
  and is not retaken. It never reaches the decision file.
- **Halts.** An UNDECIDED halt (a player sidecar of another shot or level, or the decision file raising) works as in
  the confirmatory: it counts as an attempt and as the slot's one non-KEEP. The owner may resume that slot once, at
  the same level with the same decision file. A fault after that resume leaves the slot absent. Any other resume, a
  second halt in a slot, or a decision for another slot while a halt waits, is NOT DECIDABLE. The session then stops
  and the owner is asked.
- **Slots.** Slots 0 to 14 are accepted. Slot 0 is the start play.

The session needed no change. It aligns only a KEEP. It holds open for a retake only after `RETAKE_SAME_LEVEL` (or
the confirmatory's `DISCARD_STEP`). `check_order` passes a log with a slot left absent and the next slot started
after it.

## 3. O3: the retune

The player, `scripts/bt_bench_play.py`, takes `--vsg-offset-khz`, accepted within ±200 kHz (`MAX_OFFSET_KHZ`). It
sets the VSG60A's centre frequency to 2439.0 MHz plus that offset, a synthesiser retune through `vsgSetFrequency`.
The waveform file is unchanged.

The tx sidecar records `vsg_center_mhz` and `vsg_offset_hz`, which the aligner reads. The level, spurs and flatness
of the VSG60A at ±75 to ±125 kHz have not been characterised here. The start play's SNR will be the first measurement.

The reference clocks are not locked. The confirmatory's recordings put the instruments about −0.4 ppm apart.

## 4. O4: the overrun counter

The check ran on the sitting's recorder laptop, with the sitting's recorder: `bt_bench.py record`, which calls
`bt_ota_hop_check.record`, which reads `apps/bb60_source.py`'s counter. Code as pinned below; Python 3.12.9,
numpy 2.2.4, SoapySDR API 0.8.0. Each recording was 10 s, not scored, and deleted afterwards. The recorder laptop was in the registration's O7 arrangement: the
powersave governor on every CPU, and every idle state enabled (POLL, C1E, C6, C8 and C10). No other process held a
radio: a process search before the check found none.

| | sample_overruns | "Sample Overrun" lines in the rec.log | lost_sample_overflows | readstream_overflows | overruns_counted |
|---|---|---|---|---|---|
| host quiet | 0 | 0 | 0 | 0 | true |
| host loaded on purpose (a busy loop per CPU, and a direct 9.6 GB write to the same disk) | 46 | 46 | 0 | 0 | true |

The counter reads above zero and equals the driver's own warnings. readStream reported none of them, as the
amendment found.

## 5. O6: the located carrier offset

The aligner stores it for each recording:

- `NAME.align.json` holds `f_located_hz` (the commanded offset plus the located residual), `cfo_instrument_hz`,
  `cfo_instrument_spread_hz`, `reference_error_hz` and `vsg_offset_hz_set`.
- The crop's sidecar `NAME.json` carries the same in its `cfo` block.
- The tx sidecar has `vsg_offset_hz`.

These are carrier values. They stay out of the console and the session log until the stage-1 freeze (the session's
`CARRIER_KEYS`, and the allowed-keys file).

## 6. The play order

A play order of 15 slots, made with `secrets.SystemRandom` (os.urandom) and kept private on the recorder laptop's
4 TB drive:

- slot 0 is the unscored start play, 66C0A6 at +125 kHz, fixed;
- slots 1 to 14 hold the 12 offset cells (66C0A6 at +125 among them) and one 0 kHz anchor per LAP, shuffled.

Its sha256 is `225f188e2709ec4c6d76cc86f8d8ef5b35e15243eff8bc822d4fed7d5f2f365c`.

It replaces an earlier order of 14 slots (sha256 `286de307…`), which counted the start play as a scored cell. That
order is kept beside it, unused.

## 7. Pins

SDR repository: these files as committed on `foffset-decide` (the commit is given with this file). `main` was at
`ce2f091` when the branch was made.

| File | sha256 |
|---|---|
| `scripts/bt_bench.py` (the aligner, `snr`, `record`) | `e0cd0501fbe275674fb95453da07764f3b28f266d8f23410100310f12ed8cc19` |
| `scripts/bt_bench_play.py` (the player) | `7ca87aeb5064505d7e13a7cf8c36ccc9830c608df04605f340ab6758dafee761` |
| `scripts/bt_bench_session.py` | `f9defeaa346a858e988d3bbf2d966fe38dcbfe50c8cb94aba12b9e0cf9bee943` |
| `apps/bb60_source.py` | `6c7836888670da936e209f6ccc3eb2605232ff07c47386d5f6417bb5caa010ca` |
| `scripts/bt_ota_hop_check.py` | `040b329c9426cf228487e9942ecb2a4a3ef1f2be98c52ea676db96b2ac42bdb0` |
| `scripts/bt_foffset_decide.py` (the decision file) | `680175a363e61c1dec77698b750ea0c54cec226315e0973012494bd1c967ff03` |
| `scripts/bt_foffset_allowed_keys.py` | `713fbb9b308198b513705675e669631c77669b25e093b9e4b9535e48067e483a` |
| `scripts/test_bt_foffset_decide.py` | `c8d988ca17001c8fa7e606fdffb1ccae3c2d0c6acddc65a86107f1ba1cdcc74a` |
| `knowledge/freshset/f-rate-confirm-sdr-statements.md` | `185644d19f650f4e4eb5cb830871eb4d2301605edf036042b45961cb04905bdf` |
| `knowledge/freshset/f-rate-amendment-2026-10-26.md` | `b3c573a9b569d24327d4e1aa4cfcf83dec48c7fcc68e99424d35170937cc6ddd` |

The shots, the same bytes on the recorder's drive and on the player:

| File | sha256 |
|---|---|
| `bench_13B6FA_d23.cf32` | `d692b5a9f88f4539a2ad14e2e2cc05cdbdd92d5bed03278a86b4eb6810fd59b3` |
| `bench_66C0A6_d23.cf32` | `0eeae1cdb46bf1b239b5cda3c97aae26c3bbc12dd9b93d2ec5882c837811e654` |
| `bench_13B6FA_d23.json` | `88f3642055f36f2d28c8a463362eb9b374188b3fdeef8d971644ae6f2053227b` |
| `bench_66C0A6_d23.json` | `b54efdb38b22f8d07e78af6a40e2e8bc8b4bfce9d6946d3c6f5d6f85892ed840` |

The player's checkout (`ssh worklaptop1-fast`) is at `d7999d7`, one commit behind `ce2f091`. That commit changed only
`bt_synth_hop.py` and a note. Its `bt_bench_play.py` has the sha256 above, and its `apps/vsg_sink.py` is
`ea43542928bea3ea990b37f595e49ef087aea5469c616394caabee4dbcf99cc7`.
