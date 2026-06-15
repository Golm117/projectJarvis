# Working notes — qa

_(scratchpad for in-flight thinking; promote durable findings to topic files)_

## T-007 reviewed (APPROVED) + T-010 done (2026-06-16)

- **T-007 SummonController — APPROVED** (mandatory review, success-metric-critical).
  All six review criteria pass: external-behavior tests only (24 tests assert the
  returned `SummonDecision`/`None` + the *real* injected gate's public predicates
  on the `SimulatedClock`, no private coupling); Path A unconditional; Path B
  drops on any of {¬is_wall, conf<floor, speech_resumed, ¬gap, back-off} with the
  abort check correctly *before* the gap (`test_resume_suppresses_even_if_a_stale_
  gap_reads_elapsed`); back-off de-dupes by `category::offer` and only a fire arms
  it (`test_a_dropped_wall_does_not_arm_backoff`); floor injected + `[0,1]`-guarded
  + inclusive `>=`. `_signature` reads `category.value` only after `is_wall` is
  True (no NONE-category crash). Suite 121 green, ruff clean. `review → done`.
- **T-010 interjection-precision eval spec — DONE.** Full spec promoted to
  `eval-plan.md` §"Interjection-precision eval spec (T-010)": fixture format
  (timeline of utterance/speech_start/speech_end + per-candidate
  wall/category/useful|false labels + a `config` block of the three thresholds),
  precision = useful÷total fires (Path-B `INTERJECTION` decisions only; Path-A
  summons + `None` decisions excluded; match a fire to a candidate by time window,
  right-category required for "useful"), deterministic run model on the
  `SimulatedClock`+fakes harness, T-503 calibration hook, and 5 illustrative
  fixtures (no captured data). No code added → suite stays 121 green, ruff clean.
  **→ Only T-008 (orchestrator + end-to-end mock pipeline) remains in Phase 0.**

## T-009 done (2026-06-15) — harness landed

Durable conventions promoted to `docs/qa/eval-plan.md` §"Test-harness
conventions". Harness lives in `tests/clock.py`, `tests/fakes.py`,
`tests/conftest.py`; self-tests in `tests/test_harness.py` (22 tests). Suite
green (24 total), ruff clean.

### Interface gaps flagged to core-engineer (for T-006/T-005)

1. **`TurnTakingGate` input API is unspecified.** The module map freezes the
   three *output* predicates (`settled` / `politeness_gap_elapsed` /
   `speech_resumed`) but not how VAD/silence/speech events are *fed in*, nor
   the exact clock-injection signature (`now=` callable vs `Clock` object — both
   are mentioned, neither pinned). The harness clock supports both forms, so
   either works; core-engineer should pin one in T-006 so the gate tests target
   a stable input surface.
2. **`WallVerdict` not yet frozen.** Harness uses `WallVerdictLike` (TODO marker
   in `tests/fakes.py`). Field names match the documented shape; swap is
   import-only once T-005 freezes it *with* local-ml-engineer.

### Next for qa-tuning

- **→ MANDATORY REVIEW PENDING: T-007 (SummonController)** — built by core-engineer,
  now in `review` (see the "T-007 handed off for review" block below for what to
  check). Suite 121 green, ruff clean.
- **T-010** — interjection-precision eval spec (fixture format + precision
  computation). Stub section already in `eval-plan.md`. Depends on T-007 — now
  unblocked once T-007 review passes; T-008 (the pipeline) is the other input.
- Mandatory review of ~~T-005 (WallDetector)~~ (approved, below),
  ~~T-006 (TurnTakingGate)~~ (approved, below), ~~T-007 (SummonController)~~
  (handed off, awaiting review — below).

## T-007 SummonController handed off for review (2026-06-16) — AWAITING qa-tuning

core-engineer built `SummonController` (`src/jarvis/core/summon_controller.py`),
now in `review` (mandatory trigger — it carries the success metric). Suite **121
green**, ruff lint+format clean. What to verify:

- **Decision/handoff boundary (structural call):** the controller is a *pure
  decision machine* — it emits a frozen `SummonDecision`, NOT the
  `EngagementHandoff` (it holds neither summary nor window). The orchestrator
  (T-008) assembles the handoff. Logged in DECISIONS.md 2026-06-16; documented in
  module-map §SummonController. Confirm this split is sound for the eval (T-010
  scores the emitted `Interjection.category`/`.confidence`, which the decision
  carries directly).
- **Threshold:** `interjection_confidence_floor=0.70` (matches the prototype's
  `WALL_CONFIDENCE_TO_SPEAK`), constructor-injected + `[0,1]`-guarded, inclusive
  (`>=`). Kept in SummonController, not the detector (consistent with the T-005
  review finding that the speak gate is controller policy).
- **Path B condition order:** `is_wall → confidence ≥ floor → ¬speech_resumed →
  politeness_gap_elapsed → back-off`. Note **abort precedes the gap** on purpose:
  a latched resume must suppress even if the gap reads stale-elapsed
  (`test_resume_suppresses_even_if_a_stale_gap_reads_elapsed`). Worth confirming
  this ordering matches the precision intent (never talk over resumed speech).
- **Back-off** de-dupes by `category::offer` (confidence excluded — a re-detection
  of one wall at a different confidence is the same offer); only a *fire* arms it
  (`test_a_dropped_wall_does_not_arm_backoff`); semantics are "twice in a row"
  (`test_a_new_wall_fires_after_an_intervening_different_wall`).
- **Tests** (`tests/test_summon_controller.py`, 24): assert only on the returned
  `SummonDecision`/`None` and public predicates of the *real* injected gate driven
  by the `SimulatedClock` — no reach into controller internals (golden rule). The
  coverage items recorded for T-007 in the T-005/T-006 review are detector-/gate-
  level (multi-cue priority, confidence-ordering) and unaffected here.

## T-005 + T-006 review (2026-06-15) — APPROVED both

Mandatory qa-tuning review of core-engineer's WallDetector (T-005) and
TurnTakingGate (T-006), both placed in `review`. Verdict: **both pass**.
Suite **97 green**, ruff clean (ran locally).

### T-005 WallDetector — PASS

- **Tests assert external behavior, not internals.** Every assertion is on the
  returned `WallVerdict` or on `backend.transcripts` / `.summaries` / `.call_count`
  (what crossed the seam). No reach into `_backend`. Detector tests are driven by
  the `FakeWallBackend` (scripted verdicts), heuristic tests by the real backend
  over one-line transcripts — deterministic, no clock needed (the detector is
  time-independent, correctly).
- **WallVerdict schema is sound + complete for downstream.** `is_wall` /
  `category` (`NONE` iff `is_wall` False) / `confidence` [0,1] raw / `offer`. The
  `WallVerdict.none()` constructor pins the canonical non-wall result. The
  real-backend contract note (module-map §"Contract for the real backend (T-203)")
  is unambiguous and actionable for local-ml-engineer: return the dataclass not a
  dict, `category` coerces from the wire string, confidence raw, offer empty for
  non-wall, and the prototype JSON maps 1:1.
- **Speak-threshold correctly kept OUT of the detector.** `test_detector_does_not
  _apply_a_confidence_threshold` proves a 0.10 wall is still surfaced as a wall —
  the `WALL_CONFIDENCE_TO_SPEAK=0.70` cut is SummonController policy (T-007).
  Confirmed against the prototype: there the threshold lives in `should_speak`
  (attention_layer.py:418), the SummonController analog — not in `detect_wall`.
- **Faithful port + correct extension.** The heuristic mirrors the prototype's
  `_mock_detect_wall` cues/confidences and *adds* the `stuck_point` cue, which the
  prototype's mock omitted even though its JSON schema listed the category. Now all
  four wall categories are reachable from the Phase-0 backend.
- **Hard-nos honored:** no persistence, no cloud — pure regex over the passed-in
  transcript; the `summary` arg is accepted but unused by the heuristic (the real
  backend will use it).

### T-006 TurnTakingGate — PASS

- **Tests assert external behavior, not internals.** Every assertion is on a
  public predicate (`settled` / `politeness_gap_elapsed` / `speech_resumed`) after
  feeding edge events + advancing the `SimulatedClock`. No reach into `_silence_since`
  / `_resumed`. `test_predicates_do_not_mutate_state` pins the pure-read contract.
- **Single clock source, harness-drivable.** Time comes only from injected `now`;
  events carry no `ts` (gate stamps from `now()` at delivery). `SimulatedClock`
  drives every transition. Asymmetric thresholds (`settle_seconds=0.6` /
  `politeness_gap_seconds=2.0`) are constructor-injected, not magic, and guarded
  (`politeness_gap >= settle >= 0`), so Phase-5 calibration has one knob each.
- **abort-on-resume is correct — the load-bearing behavior.**
  `test_resume_aborts_a_pending_politeness_gap` proves a resume at t=1.9 (just
  before the 2.0 gap) does NOT let the stale gap fire: `on_speech_start` re-arms
  (silence predicates → False), and the *new* `on_speech_end` restarts the gap
  clock from the fresh silence onset. `speech_resumed` latches on the
  gap-interrupting resume and clears on the next `on_speech_end`. First onset
  (no prior silence) is correctly NOT a resume.
- **Hard-nos honored:** no persistence, no cloud, no audio — pure timing logic
  over the injected clock.

### Coverage gaps flagged (non-blocking — recorded for T-007/T-010, not bounced)

These are *not* defects in T-005/T-006; they are behaviors the suites don't yet
exercise and that the next consumer (SummonController, T-007) or the eval (T-010)
must cover. None justify a bounce — the modules' own contracts are fully tested.

1. **Gate: `on_speech_start` mid-speech (double-start, no intervening end).** No
   test for two `on_speech_start()` in a row. Current behavior is benign
   (idempotent: `_silence_since` already None, no spurious resume latch), but the
   VAD adapter (Phase 3) could emit it — worth a one-line test so the contract is
   pinned, not incidental.
2. **Gate: `politeness_gap == settle` (equal thresholds).** The guard allows it
   (`>=`), and both predicates would flip together. Untested boundary; harmless
   but undocumented as intended.
3. **Detector: heuristic confidence ordering is untested as a contract.** The four
   confidences (0.72–0.80) are asserted only as "in [0,1]", not their relative
   order. If T-010 ever leans on category→confidence ordering for precision, pin
   it then.
4. **Detector: multi-cue priority only partially covered.** Tests cover
   explicit_ask > "?" and factual_gap > "?". The stuck_point-vs-question-mark and
   explicit_ask-vs-factual_gap orderings aren't directly asserted. Low risk (the
   regex priority chain is linear and obvious) but a full priority matrix would
   harden it.

### What T-010 (interjection-precision eval) will need to measure — recorded

The precision eval can't produce a number yet (T-008 pipeline + T-010 don't exist).
What it will need to instrument once they do, from these two modules:

- **From WallDetector:** the `category` and `confidence` of each fired verdict.
  Precision scoring must distinguish a *true* useful interjection from a false one
  per category, and the confidence is the variable Phase-5 calibration sweeps
  against `WALL_CONFIDENCE_TO_SPEAK`. The eval fixtures must therefore label, per
  moment, both *whether* a wall exists and *which category* — so a right-category
  / wrong-category fire can be scored differently if needed.
- **From TurnTakingGate:** the two timings that gate a Path-B fire —
  `politeness_gap_elapsed` (did the ~2 s opening actually arrive) and
  `speech_resumed` (was it aborted). A "false interjection" in the precision
  metric is precisely a fire where the gap fired but speech was about to resume,
  or fired mid-thinking-pause. So the eval fixtures need utterance timing fine
  enough to place a resume relative to the gap — i.e. the labeled-conversation
  schema must carry speech/silence boundary timestamps, not just utterance text.
- **Net:** T-010's fixture schema must carry (a) per-moment wall-category +
  useful/false ground truth, and (b) speech/silence boundary timing. Both modules
  expose exactly the signals the metric consumes; nothing in their API blocks the
  eval. (No numeric precision-impact statement is possible at this stage — stated
  honestly per the review gate.)
