# Eval Plan — test conventions + interjection-precision eval

> **Owner:** qa-tuning · **Domain:** `docs/qa/`
> **Status:** living deliverable. The **test-harness conventions** section
> below landed with T-009. The **interjection-precision eval spec** is
> **T-010** (stubbed here, written next).
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

## Interjection-precision eval spec (T-010 — stub, written next)

> **This section is intentionally a stub.** The full spec — the labeled-
> conversation **fixture format** and the **precision computation** — is task
> **T-010** (`Depends on: T-007`), to be written immediately after this. It is
> *not* fully specified here.

What T-010 will define (placeholders, not commitments):

* **The metric.** Interjection precision = of the interjections Jarvis fires,
  the share that land at a genuinely useful, well-timed moment. Starting target
  **≥ 70% useful, with false interjections rare** (`.pdr.md` §Success metric).
  Precision (not recall) is the yardstick — a false interjection (talking over
  people) is the costly error; a missed one is cheap.
* **The fixture format.** A schema for labeled conversations: a sequence of
  utterances with timing, plus per-moment ground-truth labels marking where an
  interjection *would* be useful/well-timed vs. where firing would be a false
  positive. No live data needed yet (Phase 0); real captured conversations
  arrive in Phase 5 (T-502).
* **The computation.** How a run's emitted interjections are scored against the
  labels (true/false interjections → precision), driven through the
  `AttentionLayer` on `ScriptedSource` + the fakes above, on the simulated clock
  so timing is deterministic.
* **Calibration hook.** The eval is the harness Phase 5 (T-503) uses to tune the
  politeness-gap and `WALL_CONFIDENCE_TO_SPEAK` threshold against this metric.

This stub will be replaced by the full spec under T-010.

### What T-010 must measure from WallDetector + TurnTakingGate (recorded during the T-005/T-006 review, 2026-06-15)

The full precision eval (T-010) and the end-to-end pipeline (T-008) **don't exist
yet**, so no numeric interjection-precision impact can be stated for these two
modules now. They were reviewed for **behavioral soundness and testability** only
(both passed — see `working-notes.md`). What the T-010 eval will need to measure
from each, so the fixture schema is designed for it up front:

* **From `WallDetector` (the *what*):** the `category` and `confidence` of every
  fired `WallVerdict`. A true vs. false interjection is scored per category, and
  `confidence` is the variable Phase-5 sweeps against `WALL_CONFIDENCE_TO_SPEAK`.
  ⇒ The fixture schema must label, **per moment**, both *whether* a wall exists
  **and which `WallCategory`** — so a right-category and a wrong-category fire can
  be scored distinctly.
* **From `TurnTakingGate` (the *when*):** `politeness_gap_elapsed` (did the ~2 s
  opening actually arrive) and `speech_resumed` (was the fire aborted). A false
  interjection in the metric is exactly a fire into a thinking-pause or just
  before speech resumes. ⇒ The fixture schema must carry **speech/silence boundary
  timestamps** fine enough to place a resume relative to the gap — not just
  utterance text.
* **Net fixture requirement:** per-moment (wall-category + useful/false ground
  truth) **and** speech/silence boundary timing. Both modules already expose
  exactly these signals through their public API; nothing in either blocks the
  eval.
