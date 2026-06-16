# Eval Plan — test conventions + interjection-precision eval

> **Owner:** qa-tuning · **Domain:** `docs/qa/`
> **Status:** living deliverable. The **test-harness conventions** section
> below landed with T-009; the **interjection-precision eval spec** below
> landed with T-010 (fixture format + precision computation + run model).
> Grounded in `.pdr.md` (§Success metric) and
> `docs/architecture/module-map.md` (the seam contract).

This is qa-tuning's first deliverable: how the core-module tests are written
(the shared simulated clock + fakes) and — once T-010 lands — how interjection
precision, the project's success metric, is measured.

---

## Test-harness conventions (T-009)

The six core modules (T-002…T-008) share one harness instead of each
reinventing a clock and a set of fakes. It lives in:

```
tests/
├── clock.py        # SimulatedClock — the injected, controllable time source
├── fakes.py        # the seam doubles + WallVerdictLike + wall()/no_wall() helpers
├── conftest.py     # pytest fixtures exposing the clock + fakes
└── test_harness.py # self-tests proving the harness itself behaves
```

### The golden rule: test external behavior, not internals

A test asserts on what crosses a module's **public interface** — its return
values, the events it emits, and the seam calls it makes — never on private
fields or internal state. This is a hard constraint from the module map
(§"Cross-cutting design constraints" #3: *decisions are external events, not
private fields*).

Concretely:

* **Do** assert "`LivingSummary.consider_update` returned `True` and the
  injected `FakeSummarizer` was called once with this transcript."
* **Don't** reach into `living_summary._basis_keywords` or
  `summon_controller._state`.

Why it matters here: the local model backend (Phase 2) and the real
mic/VAD/voice (Phases 1, 4) will replace the fakes behind these same seams.
Tests pinned to *behavior* keep passing across that swap; tests pinned to
*internals* break the moment an implementation detail moves, and would block
the very backend swaps the architecture is designed to allow.

### The simulated clock — driving time deterministically

The core modules never call `time.monotonic()` internally; they take their time
source via the constructor (module map §"Cross-cutting design constraints" #1).
The harness exploits that: a test holds a `SimulatedClock`, injects it, and
advances time by hand — no real `sleep`, fully deterministic.

```python
from tests.clock import SimulatedClock

def test_politeness_gap_elapses(clock):          # `clock` fixture = SimulatedClock at t=0
    gate = TurnTakingGate(now=clock.now)         # inject the time source
    gate.on_silence()                            # ... feed whatever events the gate takes
    assert not gate.politeness_gap_elapsed()     # gap hasn't passed yet
    clock.advance(2.0)                            # jump 2 s forward — no real wait
    assert gate.politeness_gap_elapsed()         # now it has
```

Injection forms (both read the same mutable time value):

* **As a callable** — pass `clock.now` (a `Callable[[], float]`) wherever the
  module's constructor asks for `now`. This is the expected form per the module
  map. The clock instance is itself callable (`clock()`) and exposes
  `clock.monotonic()`, so it drops in wherever a `monotonic`-shaped callable is
  wanted.
* **As an object** — pass the `SimulatedClock` if a module prefers a clock
  object.

Driving transitions: `clock.advance(seconds)` (relative, must be ≥ 0) or
`clock.set(t)` (absolute, must be ≥ current). The clock is **monotonic by
construction** — it refuses to move backwards, matching real `time.monotonic()`
— so a test can't accidentally rewind time. This is what lets a single test walk
a `TurnTakingGate` through `settled → politeness_gap_elapsed → speech_resumed`
in sequence, and what lets `RollingWindow` eviction-by-time be tested without
waiting.

### The fakes — one per seam

Each fake **presets** a return value (fixed or per-call script) and **records**
what it was called with, so a test can assert on the call. They import nothing
from `jarvis` — usable before any core module exists.

| Fake | Seam (module-map.md) | Used by | Preset / record |
|---|---|---|---|
| `FakeSummarizer` | `SummarizerBackend.summarize(transcript, prev) -> str` | `LivingSummary` (T-004) | `return_value=` / `returns=[...]`; default = distinct `summary#N` per call. Records `.transcripts`, `.prev_summaries`. |
| `FakeWallBackend` | `WallBackend.detect_wall(transcript, summary) -> WallVerdict` | `WallDetector` (T-005) | `verdict=` / `verdicts=[...]`; default = `none`. Build verdicts with `wall(category, confidence, offer)` / `no_wall()`. Records `.transcripts`, `.summaries`. |
| `FakeResponder` | `EngagedResponder.respond(handoff) -> str` | engaged path (T-008) | `return_value=`; records every `.handoffs` / `.last_handoff` — assert *what context crossed the boundary*. |
| `FakeVoice` | `VoiceOutput.speak(text) -> None` | engaged path (T-008) | no-op; records `.spoken` / `.last_spoken`. |

Shared recorder accessors on every fake: `.called`, `.call_count`,
`.last_call`, `.reset()`.

`conftest.py` exposes default-configured instances as fixtures (`clock`,
`fake_summarizer`, `fake_wall_backend`, `fake_responder`, `fake_voice`). When a
test needs to preset returns or scripts, construct the class directly from
`tests.fakes` instead of using the fixture.

Example — scripting the wall backend per call:

```python
from tests.fakes import FakeWallBackend, wall, no_wall

def test_detector_surfaces_confidence():
    backend = FakeWallBackend(verdicts=[no_wall(), wall("factual_gap", 0.80)])
    detector = WallDetector(backend=backend)         # inject the seam
    assert detector.detect("small talk", "").is_wall is False
    v = detector.detect("I don't remember", "")
    assert v.category == "factual_gap" and v.confidence == 0.80
    assert backend.summaries == ["", ""]              # assert on what it was called with
```

### Type sequencing (until the real types land)

Some real types don't exist yet. The harness conforms to the *documented shape*
in the module map and marks the swap point:

* **`WallVerdict`** (lands with T-005, frozen *with* local-ml-engineer) — the
  harness ships `WallVerdictLike` with the documented fields (`is_wall`,
  `category`, `confidence`, `offer`). Field names already match the real type,
  so swapping is import-only. Marked `# TODO(T-005): swap to real type` in
  `tests/fakes.py`.
* **`EngagementHandoff` / `Utterance`** (T-002+) — the fakes treat the handoff
  opaquely (record-and-return), so no stand-in type is needed; tests pass a
  lightweight stub until the real `EngagementHandoff` lands.

When a real type lands, repoint the helper/import; call sites don't change.

---

## Interjection-precision eval spec (T-010)

> **Status:** written with T-010 (`Depends on: T-007`, now done). This is the
> full spec — the labeled-conversation **fixture format** and the **precision
> computation** — that Phase-5 calibration (T-503) runs against. No live data is
> needed yet; the schema is designed now so capture-and-label tooling (T-502) and
> the calibration sweep (T-503) target a stable shape. The end-to-end pipeline it
> runs through is `AttentionLayer` (T-008), still to be built — until then the
> spec is exercisable in the small against the modules directly (see
> §"How it runs").

### Why precision, and what counts as a fire

Interjection precision is the project's success metric (`.pdr.md` §Success
metric):

> **precision = useful interjections ÷ total interjections fired**

A *false* interjection — Jarvis talking over people or offering noise — is the
costly error; a *missed* one is cheap (the user can always summon). So precision,
not recall, is the yardstick. **Starting target: ≥ 70 % of fired interjections
useful/well-timed, with false interjections rare.** This is a starting line to be
tuned on captured conversations, not a fixed pass bar.

**A "fire" is a Path-B interjection only.** Precision is measured over the
proactive offers `SummonController.consider_interjection(...)` emits — i.e. the
`SummonDecision`s with `reason == INTERJECTION`. **Path-A summons are excluded
entirely**: a summon is invited (the user said the wake word), so it is never a
precision error and never enters the numerator or denominator. The eval ignores
every `SummonDecision` whose `reason is TriggerReason.SUMMON` (and never calls
`on_summon` itself). Concretely the denominator counts only `INTERJECTION`
decisions; `None` returns from `consider_interjection` (waited / aborted / backed
off / sub-threshold) are *not* fires and never counted.

### The fixture format — labeled conversations

A fixture is one labeled conversation: an ordered list of **moments** on a single
monotonic timeline (seconds), each moment being either an utterance or a
speech/silence boundary event, plus a ground-truth label wherever an interjection
*could* be evaluated. The schema carries exactly the two things the metric
consumes (recorded during the T-005/T-006 review, below): **(a)** per-candidate
wall-category + useful/false ground truth, and **(b)** speech/silence boundary
timing fine enough to place a resume relative to the politeness gap.

Top-level shape (JSON/YAML; illustrative field names):

```yaml
fixture_id: "ff-001-unanswered-question"
description: "Two speakers; B asks a question A never answers, then a clean pause."
# Asymmetric-summon defaults the fixture was authored against. The eval injects
# these into the gate/controller so the labels and the timing line up. Phase-5
# (T-503) sweeps these and re-scores the SAME fixtures.
config:
  settle_seconds: 0.6
  politeness_gap_seconds: 2.0
  interjection_confidence_floor: 0.70

# The conversation, as a flat timeline. Every entry has a `t` (seconds, monotonic,
# non-decreasing). Three kinds:
#   - utterance:     a transcribed line (feeds RollingWindow + WallDetector)
#   - speech_start:  VAD onset  -> gate.on_speech_start()
#   - speech_end:    VAD offset -> gate.on_speech_end()  (a silence opens here)
timeline:
  - { t: 0.0,  kind: speech_start }
  - { t: 0.0,  kind: utterance, speaker: "B", text: "What was the name of that API we used last quarter?" }
  - { t: 2.4,  kind: speech_end }          # B finishes; silence begins
  # ... 2 s of silence elapses; this is a clean opening (no resume)

# Ground-truth labels: one per CANDIDATE interjection moment. A candidate is a
# point in the timeline where a wall plausibly exists and the eval should check
# what the controller does. Each label is anchored to a time window and carries
# the expected wall semantics + the useful/false verdict.
candidates:
  - candidate_id: "c1"
    # The window (seconds) within which a fire is considered "at this moment".
    # A fire whose decision time falls in [match_from, match_to] matches c1.
    match_from: 2.4
    match_to:   6.0
    # Ground truth about the wall at this moment (what WallDetector SHOULD see):
    wall: true
    category: "unanswered_question"     # a WallCategory wire string, or null if wall:false
    # The precision label: is an interjection HERE useful/well-timed, or false?
    label: "useful"                     # "useful" | "false"
    # Optional human note for why (kept for the calibration audit trail).
    rationale: "B's question went unanswered; a clean 2 s opening followed."
```

Field reference:

| Field | Meaning |
|---|---|
| `config` | The gate/controller thresholds the fixture was authored against. The eval injects them; T-503 overrides them to sweep, re-scoring the same fixtures. |
| `timeline[].t` | Monotonic seconds. Drives the `SimulatedClock` — the eval advances the clock to each `t`. |
| `timeline[].kind` | `utterance` \| `speech_start` \| `speech_end`. The two boundary kinds map 1:1 onto `gate.on_speech_start()` / `gate.on_speech_end()`; `utterance` feeds the window + detector. |
| `candidates[].match_from/to` | The time window a fire must fall in to be attributed to this candidate (handles the politeness-gap delay between the wall and the fire). |
| `candidates[].wall` / `category` | Ground truth: is there a wall here, and which `WallCategory`. Lets a right-category vs. wrong-category fire be scored distinctly. |
| `candidates[].label` | `useful` (a fire here is correct) or `false` (a fire here is a precision error — a thinking-pause, an off-topic cue, or a moment speech was about to resume). |

A fixture needs **no** captured audio or model output — it is the labeled
*ground truth*. In Phase 0 fixtures are hand-authored (a few per wall category +
the abort/back-off cases below). In Phase 5, T-502's capture-and-label tooling
emits this same schema from real opt-in conversations.

> **T-502 landed (2026-06-16).** This schema is now code:
> `src/jarvis/eval/fixture.py` (`Fixture`/`Moment`/`Candidate`/`Config` +
> JSON (de)serialization), `capture.py` (the `--capture PATH` recorder),
> `label.py` (the labeling CLI), `runner.py` (the precision computation), and
> `seed.py` (the seeded corpus → `docs/qa/fixtures/*.json`). The full
> capture→label→score workflow + the privacy/retention model + the qa verdict on
> the live "What do you need?" false positive are in
> `docs/qa/capture-and-label.md`. The seeded set scored **precision 0.60** on the
> T-502 defaults; **T-503 raised it to 0.75** (post-engagement cooldown +
> pending-wall TTL) — see `docs/qa/threshold-tuning.md`.
>
> **T-503 schema additions (v2):** an `engagement` moment kind (marks when Jarvis
> engaged, so the runner applies the post-engagement cooldown), a candidate
> `wall_detected_at` field (the pending-wall TTL anchor), and two `config` knobs
> (`post_engagement_cooldown_seconds`, `pending_wall_ttl_seconds`). The loader
> reads both v1 and v2 fixtures.

### Precision computation — matching fires to labels

The eval replays a fixture's timeline through the modules (next section),
collecting every Path-B **fire** (`INTERJECTION` decision) together with the
clock time at which it was emitted. Each fire is then matched to a candidate and
scored:

1. **Match.** A fire at decision-time `t_fire` is attributed to the candidate
   whose `[match_from, match_to]` contains `t_fire`. (Windows are authored
   non-overlapping; if a fire falls in none, it is an **unmatched fire** —
   counted as *false*, since the controller spoke where no candidate exists.)
2. **Score the matched fire:**
   * **useful (true positive)** iff the matched candidate has `label: useful`
     **and** the fired `Interjection.category` equals the candidate's `category`
     (right wall, right moment). A right-moment / wrong-category fire is scored
     **false** — surfacing it as a precision error is deliberate, since the
     offer would be about the wrong thing.
   * **false (false positive)** otherwise — the candidate is `label: false`, or
     it was an unmatched fire.
3. **Aggregate.**

   ```
   precision = useful_fires / total_fires          # total_fires = useful + false
   ```

   over all fires across all fixtures (micro-average; a per-category breakdown is
   also reported so calibration can see *which* wall types over-fire). If
   `total_fires == 0`, precision is reported as `undefined` (not 0) — a run that
   never interjects has no precision to speak of; that is a recall concern, out of
   scope for this metric.

What is **not** counted: Path-A summons (excluded as above); `None` decisions
(waited/aborted/backed-off — these are the controller *correctly* staying
silent, the opposite of a false fire); and `useful`-labeled candidates that the
controller stayed silent on (a *miss* — a recall datum, recorded for visibility
but never in the precision ratio).

This makes the metric robust the way the design intends: the abort-on-resume and
back-off behaviors (which produce `None`, not a fire) can only ever *help*
precision — they remove would-be false fires from the denominator — and the
confidence floor + politeness gap are exactly the knobs that move a borderline
candidate between "fired (and scored)" and "stayed silent (uncounted)".

### How it runs against the modules

The eval is deterministic and offline — **no audio, no model, no network** — the
same posture as the unit tests, on the same harness:

* **Clock:** one `SimulatedClock` per fixture. The runner walks the `timeline`,
  `clock.advance(...)`-ing to each entry's `t`, so every gate transition and
  every fire happens at a known simulated time. No real `sleep`.
* **Signals it reads, per module (all public API — nothing private):**
  * **`TurnTakingGate` (the *when*):** fed the fixture's `speech_start` /
    `speech_end` events via `on_speech_start()` / `on_speech_end()`; the runner
    reads `politeness_gap_elapsed()` (did the opening arrive) and the controller
    internally reads `speech_resumed()` (was the fire aborted). A false
    interjection in the metric is precisely a fire into a thinking-pause or just
    before speech resumes — the gate is what the controller consults to avoid it.
  * **`WallDetector` (the *what*):** in Phase 0 the wall at each moment comes from
    the fixture's labeled `wall`/`category` (or a `FakeWallBackend` scripted from
    them) — *not* from running a model. The `category` and `confidence` of each
    `WallVerdict` are what the controller gates on and what scoring compares to
    the candidate. In Phase 2+ the real backend can be swapped in behind the same
    seam to *also* measure detector precision, but the interjection-precision
    metric itself only needs the labeled verdict.
  * **`SummonController` (the *decision*):** the unit under measurement. The
    runner calls `consider_interjection(verdict)` at each candidate's moments and
    records every `INTERJECTION` `SummonDecision` (its `Interjection.category` /
    `.offer` / `.confidence`) with the clock time — exactly the fields scoring
    needs. `on_summon` is never called; Path-A is out of scope.
* **Fakes:** `FakeWallBackend` (scripted from the fixture's labeled verdicts) and,
  once T-008 lands, `ScriptedSource` + `FakeResponder` / `FakeVoice` so the whole
  `AttentionLayer` can be driven end-to-end and the fires read off the dispatched
  offers. Until T-008, the spec is exercisable directly against
  `(TurnTakingGate, SummonController)` + scripted verdicts — the same way
  `tests/test_summon_controller.py` already drives them.

### Calibration hook (Phase 5, T-503)

The eval is the harness T-503 uses to tune the two Path-B knobs against this
metric: `politeness_gap_seconds` (TurnTakingGate) and
`interjection_confidence_floor` (SummonController, default 0.70 — the prototype's
`WALL_CONFIDENCE_TO_SPEAK`). T-503 holds the labeled fixtures fixed, sweeps these
via the fixture `config` block, re-scores precision per setting, and picks the
operating point that clears the ≥ 70 %-useful target with false interjections
rare. Because both knobs are constructor-injected (verified in the T-006 and
T-007 reviews), the sweep changes only the `config` block — no code edit.

### Illustrative fixture set (schema examples — no captured data)

A starter set covering each scored behavior. These are *schema illustrations*,
not committed data; T-502 produces the real corpus.

1. **`ff-useful-unanswered-question`** — B asks, A never answers, clean 2 s
   opening. One candidate, `wall: true`, `category: unanswered_question`,
   `label: useful`. Expect: one fire, matched, useful → precision 1.0.
2. **`ff-false-thinking-pause`** — A pauses mid-thought (a factual_gap cue) but
   resumes before the gap elapses: `speech_end` at t, `speech_start` at t+1.2
   (< 2 s). Candidate `label: false`. Expect: the controller aborts on
   `speech_resumed` → **`None`, no fire** → this would-be false positive is
   correctly *removed* from the denominator (demonstrates abort improving
   precision).
3. **`ff-false-wrong-category`** — a real `stuck_point` wall, clean opening, but
   the (hypothetical mis-firing) verdict carries `category: factual_gap`.
   Candidate `label: useful`, `category: stuck_point`. Expect: a fire that
   matches the moment but mismatches category → scored **false** (right moment,
   wrong offer).
4. **`ff-backoff-no-nag`** — the same wall surfaces twice across two openings.
   Candidate 1 `label: useful`; candidate 2 marks the repeat. Expect: fire #1
   useful, fire #2 suppressed by back-off → `None` → only one fire counted
   (demonstrates back-off improving precision).
5. **`ff-below-floor`** — a real wall but `confidence` 0.55 (< 0.70 floor),
   clean opening. Candidate `wall: true`, `label: useful`. Expect: **no fire**
   (sub-threshold) → a *miss* recorded (recall datum), precision unaffected. T-503
   can lower the floor and re-score to see this become a fire.

### What this measures from WallDetector + TurnTakingGate (recorded during the T-005/T-006 review, 2026-06-15)

Both modules were reviewed for behavioral soundness and testability only (both
passed — `working-notes.md`); the numeric impact is what this eval produces once
fixtures + T-008 exist. The fixture schema above is designed against exactly the
signals each module exposes:

* **From `WallDetector` (the *what*):** `category` + `confidence` of every fired
  `WallVerdict` — scored per category, and `confidence` is the variable T-503
  sweeps against the floor. ⇒ schema labels per-candidate `wall` + `category`.
* **From `TurnTakingGate` (the *when*):** `politeness_gap_elapsed` (opening
  arrived) and `speech_resumed` (fire aborted). ⇒ schema carries `speech_start` /
  `speech_end` boundary timing fine enough to place a resume relative to the gap.
* **Net:** per-candidate (wall-category + useful/false) **and** speech/silence
  boundary timing. Both modules expose exactly these through their public API;
  nothing in either blocks the eval.
