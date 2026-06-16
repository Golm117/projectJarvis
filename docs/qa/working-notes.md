# Working notes — qa

_(scratchpad for in-flight thinking; promote durable findings to topic files)_

## T-302 tick() + continuous Path-B loop — MANDATORY REVIEW: APPROVED + T-303 live validation done (2026-06-15)

Mandatory qa-tuning review of core-engineer's `AttentionLayer.tick()` + the live
ticker (`live.py`). This is the success-metric-critical change: it changes *when
interjections fire live* (continuous re-evaluation during silence, replacing the
T-105/T-204 trailing-re-ingest smoke affordance). **Verdict: APPROVED → T-302
done; T-303 done.** Suite **281 green**, ruff clean. Gated modules
(`TurnTakingGate`/`SummonController`/`WallDetector`) confirmed **byte-for-byte
unchanged** (`git diff cc1162c..HEAD` over the three files = empty). **T-304
unblocked** — last Phase-3 task.

### Review focus items (the brief's 6) — all PASS

1. **Double-fire fix — SOUND, the original live bug is fixed.** The cached-verdict
   design works via a *double guard*: (a) `tick()`/`ingest` clear `_pending_wall`
   on the first fire, so all later ticks hit the `if self._pending_wall is None:
   return` no-op — this is unconditional, independent of offer determinism; (b) the
   *same* `WallVerdict` object is re-evaluated on every tick, so
   `category::offer` is a STABLE signature and `SummonController`'s existing
   back-off de-dupes even if guard (a) were absent. `test_tick_fires_exactly_once_
   across_many_calls` pins guard (a) (20 ticks → 1 fire, `_pending_wall is None`
   after). NOTE: that test's `FakeWallBackend` returns a *fixed* offer, so it does
   not itself reproduce the non-determinism that broke T-204 — guard (a) is what it
   pins. The real non-deterministic-offer case I confirmed **live** with
   `--local-brain` (below): one fire, one Qwen offer. Between the deterministic pin
   of guard (a) and the live confirmation of the end-to-end de-dupe, the fix is
   fully validated.
2. **Clearing / staleness policy — ACCEPTED (precision-safe; one watch-item flagged
   to T-503, not blocking).** Judged against precision (= useful ÷ total Path-B
   fires). The policy: set/replace at ingest, clear on fire + on engagement, NOT
   cleared on speech-resume, replaced by a fresher wall. My judgments:
   - *Replace-with-fresher-wall*: correct. Avoids a stale first wall firing after
     the topic moved — precision-positive.
   - *Fire-on-the-next-fresh-silence-after-an-abort*: correct **for v0**. The gap
     is still genuinely open (the user asked, then talked more, then went quiet) —
     the wall context is still live and the silence is real, so the offer is well-
     timed. Confirmed live (abort-on-resume run): the wall persisted across resumed
     speech and fired only on the eventual clean 2 s silence.
   - *The one watch-item:* a pending wall can sit cached across an arbitrary number
     of intervening non-wall utterances and fire on a much-later silence — IF the
     conversation has genuinely moved on (question since answered, topic shifted)
     that would be a **stale false interjection** (the precision-killer). In
     practice the *replace-with-fresher-wall* rule + the fact that real
     conversations keep producing wall-signal lines bounds this, and the heuristic/
     Qwen backends only cache on a fresh wall-signal line. But there is **no time-
     to-live on `_pending_wall`** and no "topic-shift clears the pending wall"
     rule. I am NOT requiring a change for v0 (no evidence of it firing wrongly in
     the live runs; adding a TTL is a `SummonController`/orchestrator-policy tuning
     question). **Flagged to T-503**: add a staleness fixture (wall cached →
     several off-topic turns → late silence) and decide whether `_pending_wall`
     needs a TTL or a topic-shift clear. Recorded for the orchestrator as a
     non-blocking human/Phase-5 item.
3. **No spurious fires during brief pauses — SOUND.** The ticker only fires when
   `gate.politeness_gap_elapsed()` (≥ 2 s). A routine sub-2 s mid-sentence pause
   keeps the predicate False, so `tick()` is a no-op. Confirmed live: in the abort-
   on-resume run the short inter-clause pauses produced no fire. The 2 s gap + the
   confidence floor are the bar; retuning either is a `SummonController`/threshold
   change → **left to Phase-5 T-503 (qa-gated)**, not touched here.
4. **abort-on-resume preserved — SOUND, confirmed live.** `tick()` adds no new
   logic; `consider_interjection` reads `gate.speech_resumed()` (checked before the
   gap) and returns None. Deterministic pins: `test_tick_does_not_fire_when_speech_
   resumed`, `test_tick_fires_after_abort_then_fresh_silence`. Live: the wall line
   did NOT fire while speech kept resuming; it fired only on the final clean
   silence.
5. **One-clock invariant + no gated-module change — HOLDS.** `tick()` reads time
   ONLY via the gate predicates (no new `time.monotonic()`).
   `test_tick_reads_time_only_via_gate_predicates` pins that the SimulatedClock
   controls the fire threshold. Gated modules unchanged (diff empty).
6. **Threading — sane.** Single `threading.Lock` in `live.py` serialises
   `ingest()` and `tick()`; core stays lock-free. `_pending_wall` is only ever read/
   written under that lock (both `ingest` and `tick` are lock-wrapped at the call
   sites). No obvious race. `test_tick_and_ingest_thread_safety_with_lock` stresses
   it. I/O-layer, not qa-gated, but no defect.

### T-303 — live validation on this M5 (BlackHole 2ch digital loopback, device 5)

All verbatim, nothing fabricated. The real continuous ticker (no `--stop-after`,
no trailing re-ingest) is what's exercised — this is what the old smoke affordance
stood in for.

- **Fires mid-conversation via the ticker, exactly once (heuristic brain):**
  `--say "What was the date of the conference again?"` →
  `>> JARVIS (interjecting, factual_gap @ 0.80): I can find that — want me to?` →
  ENGAGEMENT `wall:factual_gap`. ONE interjection, ONE engagement. The fire came
  from `tick()` on the natural trailing silence, not a re-ingest.
- **abort-on-resume holds:** `--say "What was the date of the conference again?
  Actually wait, I think I remember now, it was the tenth, yes the tenth of next
  month, so we are all set on that."` → the wall line transcribed first, but NO
  fire during the resumed speech; the interjection fired only after the final
  clean 2 s silence ("So we are all set on that."). Speech-resume suppressed the
  ticker every time it landed.
- **Back-off de-dupe with the real non-deterministic QwenWallBackend (`--local-
  brain`):** `--say "What was the date of the conference again?"` →
  `>> JARVIS (interjecting, factual_gap @ 0.95): Could you remind me of the
  conference date?` → ENGAGEMENT. **Exactly one fire / one Qwen offer** — the T-204
  live double-fire (same wall, two different offer phrasings) is FIXED. The cached-
  verdict design held the signature stable across the ~10 ticks/gap.

Loopback caveat (same as T-105/T-204): BlackHole is a *digital* (best-case)
loopback; real-room WER is still T-502. The firing cadence, gate timing, abort,
and de-dupe are all the real live pipeline.

### Human / Phase-5 flags (neither blocks)
- **Pending-wall staleness TTL** (item 2 watch): no TTL / topic-shift clear on
  `_pending_wall`. T-503 should add a staleness fixture and decide if a TTL is
  warranted. A TTL would be a `SummonController`/orchestrator-policy change → qa-
  gated; flagged, not taken.
- **politeness-gap / confidence-floor retune** — unchanged here; Phase-5 T-503
  lever (carry-forward from T-203/T-204).

---

## T-203 QwenWallBackend — MANDATORY REVIEW: APPROVED (2026-06-15)

Mandatory qa-tuning review of local-ml-engineer's `QwenWallBackend`
(`src/jarvis/ml/wall.py`) — the real `WallBackend` on Qwen2.5-3B/MLX, carries the
interjection-precision success metric. **Verdict: APPROVED → done. T-204 unblocked.**
Suite **264 green**, ruff clean. Live test re-run independently on this M5 (4/5,
matching the brief verbatim). Two human-decision flags raised (below), neither blocking.

### What I checked (the brief's 5 items + my own probes)

1. **factual_gap recall — ACCEPTED for v0 (option a).** I probed 6 genuine
   factual_gap phrasings against the real model (not just the one live scenario):
   - FIRE (is_wall=True, factual_gap, 0.95): "What was the date of the conference
     again?" (the **exact T-105 live Path-B trigger**), "who was the contact? I forget".
   - MISS (is_wall=False, ~0.90–0.95): "I don't remember the date", "I can't recall
     the vendor", "what was the API name?", "no idea what the budget was".
   The split is **question-form gaps fire / declarative gaps miss**. So the category
   is *partially reachable, not dead* — and crucially the T-105 live-smoke trigger
   still fires, so swapping the backend at T-204 does NOT silence the demonstrated
   Path-B path. Grounded in the metric: **precision = useful ÷ total Path-B fires**;
   a missed factual_gap is *silence* (a recall cost), never a false fire (a precision
   cost). Precision-first is the explicitly chosen, DECISIONS.md-logged strategy and
   the project's success metric. So conservative-on-declarative-factual_gap is an
   acceptable v0 tradeoff. **Recall tuning deferred to Phase-5 T-503** (the calibration
   sweep can add a stronger declarative factual_gap example or lower the floor and
   re-score). Not option (b) (don't bounce — the impl is sound and the tradeoff is
   deliberate); option (c) 7B is a human decision already DEFERRED — flagged, not taken.

2. **Confidence calibration vs the 0.70 floor — raw contract honored; floor sound
   but INERT for this backend.** Verified the backend applies NO threshold
   (`test_detect_wall_returns_confidence_raw_no_threshold`: 0.45 wall passes through;
   `test_confidence_zero_point_seven_boundary_preserved_raw`). My probe: every *fired*
   wall (all 4 categories) lands at **0.95**, well above the 0.70 floor; non-walls sit
   0.90–1.00. The model emits **near-binary confidence reflecting certainty about its
   own answer regardless of is_wall sign** — exactly as the brief flagged. Consequence:
   for this backend the **binary `is_wall` is the real gate; the 0.70 floor never
   decides** (no observed `is_wall=True ∧ conf<0.70`). The floor stays correct (admits
   the 0.95 fires; would still filter a hypothetical low-confidence wall) but does no
   work here. **This is a Phase-5 T-503 recalibration note, NOT a blocker — and
   changing the floor is itself a qa-gated change, so flag to orchestrator, do not
   touch.** FLAGGED.

3. **WallVerdict contract conformance — PASS, pinned.** `_parse_verdict` enforces and
   tests pin all invariants: NONE iff ¬is_wall (`test_non_wall_always_has_none_category`,
   `test_wall_with_none_category_becomes_no_wall`); confidence clamped [0,1]
   (clamp-above/below tests); offer="" for non-wall (`test_non_wall_offer_is_empty_string`);
   returns the **frozen `WallVerdict` dataclass** not a dict (`@dataclass(frozen=True)`
   in types.py; `test_detect_wall_returns_wall_verdict_dataclass` asserts isinstance);
   **graceful fallback to `none()` on any malformed output, never raises** (12 fallback
   tests: empty/whitespace/prose/malformed/array/null/missing-field/unknown-enum/fences/
   embedded-in-prose). Robust extras beyond the contract: markdown-fence stripping +
   first-`{...}`-block extraction.

4. **Offer phrasing — minor, non-blocking note.** Model offers are correct but a touch
   formal ("Would you like some assistance in determining the flight duration?") vs the
   heuristic's spoken-style "Want me to look that up for you?". The PRD wants spoken-style.
   Not a wall-detection-behavior defect; the offer is post-processable on the engaged
   path / tunable in the prompt. Recorded for Phase-4/5 polish, not bounced.

5. **Test quality — PASS.** 57 model-free tests assert the **external contract**
   (message construction, JSON parsing per all 5 categories, invariants, Protocol
   conformance, `plugs_into_wall_detector`) — no reach into internals (golden rule).
   `test_lazy_import_boundary` pins that importing the backend never loads mlx_lm.
   Genuinely model-free (the one live test self-skips) + green. The brief's "test gap"
   (end-to-end conf==0.70 boundary) correctly lives in T-007 SummonController tests, not
   the backend (backend just surfaces raw); no new backend test needed for it.

### Human-decision flags raised to orchestrator (neither blocks the approval)
- **7B escalation (factual_gap recall):** already DEFERRED; needs a joint-budget
  measurement + human latency call. Not taken here.
- **interjection_confidence_floor recalibration:** the model's near-binary confidence
  means the floor is inert for this backend. Whether to keep/adjust 0.70 is a Phase-5
  T-503 question AND a qa-gated change — flagged, not decided unilaterally.

### Note for T-204 (orchestrator swap) + T-503 (calibration)
- T-204 swap is safe re: the live-smoke demo: the T-105 Path-B trigger line still
  fires factual_gap under the Qwen backend.
- T-503 fixtures should add **declarative factual_gap** cases ("I don't remember…",
  "I can't recall…") as *recall* data points — they currently miss under Qwen and are
  the lever to tune. Precision is unaffected by these misses by construction.

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
