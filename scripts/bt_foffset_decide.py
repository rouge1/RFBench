"""The f-offset bench's decision file: a fixed level and the fault rule (bluey-ox-walker's f-offset-bench-reg.md, section 2 and O2).

    python scripts/bt_bench_session.py finish NAME --decide scripts/bt_foffset_decide.py --dir LOCAL --drive DRIVE

``bt_bench_session.py`` runs it by path in ``python -I -B`` and replays ``LevelSession`` over every attempt in its log,
so it imports only the standard library and keeps the interface of the confirmatory's ``diag_s4_bench_decide.py``:
``FIRST_COMMAND_DBM``, and a ``LevelSession`` with ``decide``, ``halt``, ``resume`` and ``level``, ``relevels``,
``capped`` and ``state``. What it rules is not the confirmatory's:

* **The level never changes.** Every play is at -69 dBm; the SNR of every recording is logged by the session and is
  never used to re-level or to refuse. ``relevels`` stays 0 and ``capped`` False.
* **A fault takes the slot's one retake**, in the same slot at the same level (``RETAKE_SAME_LEVEL``). The faults are
  the session's closed list, as the amendment of 2026-10-26 (rule (b)) left it: the recorder lost samples, overflowed or
  could not count the driver's Sample Overruns (``recorder_fault``); the player's sidecar missing, not sent or in
  another state (``player_fault``); a fault logged before ``finish`` (``exit_fault``); ``snr`` refused, its own
  problem or the spread rule's ``snr_db_sem`` over 0.10 dB or none (``snr_refused``). An overrun counted is not a fault.
* **A second non-KEEP in a slot leaves it absent** (``FAULT_ABSENT``): no third play, and the sitting goes on to the
  next slot. It is never NOT DECIDABLE for a fault.
* **Otherwise KEEP**, whatever the SNR's median or its spread over the channels.

An UNDECIDED halt (a player sidecar of another shot or level, or this file raising) is as before: an attempt and the
slot's one non-KEEP. The owner may resume that slot once, at the same level with the same decision file
(``RESUME_AS_RETAKE``); a fault after the resume leaves the slot absent. A resume of anything else, a second halt in a
slot, a halt in a slot that already had its retake, or a decision for another slot while a halt waits, is NOT
DECIDABLE: the session stops and the owner is asked, since the registration's rules do not cover it.

An aligner refusal after a KEEP is the session's ``close`` (the recording is ``refused``, not retaken); it never
reaches this file. Tests: ``scripts/test_bt_foffset_decide.py``.
"""
from __future__ import annotations

import math

FIRST_COMMAND_DBM = -69.0
#: The start play (slot 0, unscored) and the 14 scored slots.
SLOTS = range(0, 15)


class LevelSession:
    """The fixed-level rule as a state machine: one call per recording attempt, in the order recorded.

    ``decide(slot, recorder_fault=..., player_fault=..., exit_fault=..., snr_refused=..., median=..., lo=..., hi=...)``
    takes either one or more fault flags and no snr numbers, or no fault and all three numbers (finite): anything else
    is a caller error (ValueError, no attempt counted), as is a slot outside ``SLOTS`` or one already closed (kept or
    absent). It returns KEEP, RETAKE_SAME_LEVEL or FAULT_ABSENT."""

    def __init__(self):
        self.level = FIRST_COMMAND_DBM
        self.relevels = 0
        self.capped = False
        self.state = None
        self.attempts = 0
        self.slot_nonkeep: dict = {}
        self.closed: dict = {}                    # slot -> 'KEEP' or 'FAULT_ABSENT'
        self.halts: dict = {}
        self.halt_info: dict = {}
        self.pending_halt = None

    def _check_slot(self, slot):
        if isinstance(slot, bool) or not isinstance(slot, int) or slot not in SLOTS:
            raise ValueError(f"slot {slot!r} is not one of {SLOTS.start}..{SLOTS.stop - 1}")
        if slot in self.closed:
            raise ValueError(f"slot {slot} is already closed ({self.closed[slot]})")

    def decide(self, slot, *, recorder_fault: bool = False, player_fault: bool = False, exit_fault: bool = False,
               snr_refused: bool = False, median: float | None = None, lo: float | None = None,
               hi: float | None = None) -> str:
        if self.state == "NOT DECIDABLE":
            return "NOT DECIDABLE"
        if self.pending_halt is not None:
            if slot != self.pending_halt:
                self.state = "NOT DECIDABLE"
                return "NOT DECIDABLE"
            raise ValueError("an UNDECIDED halt is pending for this slot: call resume() first")
        self._check_slot(slot)
        fault = bool(recorder_fault or player_fault or exit_fault or snr_refused)
        numbers = (median, lo, hi)
        if fault and any(v is not None for v in numbers):
            raise ValueError("a fault flag and snr numbers together: snr is not given to the decision on a fault")
        if not fault and not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                                 for v in numbers):
            raise ValueError("median, lo and hi must all be finite numbers when no fault flag is set")
        self.attempts += 1
        if not fault:
            self.closed[slot] = "KEEP"
            return "KEEP"
        nonkeep = self.slot_nonkeep.get(slot, 0) + 1
        self.slot_nonkeep[slot] = nonkeep
        if nonkeep >= 2:
            self.closed[slot] = "FAULT_ABSENT"
            return "FAULT_ABSENT"
        return "RETAKE_SAME_LEVEL"

    def halt(self, slot, level: float, decide_sha: str) -> str:
        if self.state == "NOT DECIDABLE":
            return "NOT DECIDABLE"
        self._check_slot(slot)
        self.attempts += 1
        prior = self.slot_nonkeep.get(slot, 0)
        self.halts[slot] = self.halts.get(slot, 0) + 1
        self.slot_nonkeep[slot] = prior + 1
        self.halt_info[slot] = (level, decide_sha, prior >= 1)
        if prior >= 1 or self.halts[slot] >= 2:
            self.state = "NOT DECIDABLE"
            return "NOT DECIDABLE"
        self.pending_halt = slot
        return "HALT"

    def resume(self, slot, level: float, decide_sha: str) -> str:
        if self.state == "NOT DECIDABLE":
            return "NOT DECIDABLE"
        if self.pending_halt is None:
            raise ValueError("no UNDECIDED halt is pending")
        halted = self.pending_halt
        lvl, sha, retake_used = self.halt_info[halted]
        if (slot != halted or level != lvl or decide_sha != sha or retake_used or self.halts.get(halted) != 1
                or level != self.level):
            self.state = "NOT DECIDABLE"
            return "NOT DECIDABLE"
        self.pending_halt = None
        return "RESUME_AS_RETAKE"
