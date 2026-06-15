# Module Map — the Attention Layer

> **Owner:** core-engineer · **Domain:** `docs/architecture/`
> **Status:** living deliverable (T-001). Grounded in `docs/prd/02-jarvis-v0-mvp.md`
> and the reference prototype `prototypes/attention-layer/attention_layer.py`.
> Updated as modules land (T-002…T-008) and the seams freeze.

This is the **seam contract** the other agents implement against. It defines the
module boundaries, the interfaces that cross them, and the event flow from an
`Utterance` entering the rolling window to an `EngagementHandoff` leaving the
boundary. The pure-logic modules are mine (core-engineer); the I/O adapters,
the model backend, and the engaged path are seams that sensing-engineer,
local-ml-engineer, and voice-integration-engineer fill behind these interfaces.

---

## The two halves (PRD 02)

```
                          ┌──────────────────────── AMBIENT HALF ────────────────────────┐
                          │            always on · 100% local · no network                │
  mic ─► VAD ─► ASR ─►  Utterance ─► RollingWindow ─► TopicShiftDetector ─► LivingSummary  │
   (sensing-engineer)      │              │                                      │         │
                          │              └────────────► WallDetector ◄──────────┘         │
                          │                                  │ (local-ml backend)         │
                          │   VAD timing ─► TurnTakingGate ─► SummonController             │
                          └──────────────────────────────────────┬───────────────────────┘
                                                                  │  EngagementHandoff
                          ┌───────────────────────────────────────▼──────── ENGAGED HALF ─┐
                          │     only after a trigger · cloud "for now"                      │
                          │   EngagedResponder (Claude) ─► VoiceOutput (ElevenLabs) ─► 🔊    │
                          │                       (voice-integration-engineer)              │
                          └───────────────────────────────────────────────────────────────┘
```

The microphone, the cloud LLM, and the voice service are **boundaries**. The core
attention logic never touches them directly — it talks to seams.

---

## Ownership at a glance

| Module | Kind | Owner | Mandatory review (qa-tuning) |
|---|---|---|---|
| `Utterance`, `EngagementHandoff`, `Interjection`, `WallVerdict` | data types | core-engineer | — |
| `RollingWindow` | pure logic | core-engineer | — |
| `TopicShiftDetector` | pure logic | core-engineer | — |
| `LivingSummary` | pure logic (+ summarizer seam) | core-engineer | — |
| `WallDetector` | interface + mock backend | core-engineer | **yes** (detector + thresholds) |
| `TurnTakingGate` | state machine (clock-driven) | core-engineer | **yes** |
| `SummonController` | dual-path state machine | core-engineer | **yes** |
| `AttentionLayer` | orchestrator | core-engineer | — |
| `TranscriptSource` (seam) | I/O adapter | core defines · sensing fills (`MicSource`); core ships `ScriptedSource` | — |
| `SummarizerBackend`, `WallBackend` (seams) | model adapters | core defines · local-ml fills | — |
| `EngagedResponder`, `VoiceOutput` (seams) | I/O adapters | core defines · voice fills | — |

Any change to `TurnTakingGate`, `SummonController`, `WallDetector`, or the
interjection thresholds **routes through qa-tuning before merge** — that behavior
is the project's success metric (interjection precision).

---

## Cross-cutting design constraints (apply to every module)

These are non-negotiable so qa-tuning's harness (T-009) can drive the modules and
so the swappable backends actually swap:

1. **Injected clock, never a hidden one.** Anything time-dependent
   (`RollingWindow` eviction, `TurnTakingGate`) takes its time source via the
   constructor as **`now: Callable[[], float]`** — a zero-arg callable returning
   monotonic seconds. No `time.monotonic()` buried inside. This is the **single
   clock-injection convention** for every time-bounded module in this map (pinned
   T-002, closing the T-009 ambiguity: a bare `now` callable, *not* a `Clock`
   object). qa-tuning's `SimulatedClock` is injected by passing `clock.now`; the
   clock instance is itself callable, so it also drops in directly where useful.
2. **Injected backend, never an instantiated one.** `LivingSummary` takes a
   `SummarizerBackend`; `WallDetector` takes a `WallBackend`. The mock is the
   default reference; local-ml replaces it without touching the module.
3. **Decisions are external events, not private fields.** State transitions and
   outputs surface as return values or emitted events (`summary_update`,
   `interjection`, `engagement`) — testable through the public interface, not by
   reaching into internals.
4. **Pure core, no I/O.** No module in `src/jarvis/core/` reads audio, opens a
   socket, or calls a network. I/O lives only in the adapter seams.

---

## Data types (the things that cross seams)

```python
@dataclass(frozen=True)        # FROZEN (T-002) — see jarvis/types.py
class Utterance:
    speaker: str
    text: str
    ts: float            # monotonic seconds, supplied by the producer (injected clock / VAD)

class WallCategory(StrEnum):   # FROZEN (T-005) — jarvis/types.py
    UNANSWERED_QUESTION = "unanswered_question"
    FACTUAL_GAP         = "factual_gap"
    STUCK_POINT         = "stuck_point"
    EXPLICIT_ASK        = "explicit_ask"
    NONE                = "none"

@dataclass(frozen=True)
class WallVerdict:        # FROZEN (T-005) — what a WallBackend returns
    is_wall: bool
    category: WallCategory   # NONE iff is_wall is False
    confidence: float        # [0.0, 1.0]; surfaced raw — the speak gate is SummonController's
    offer: str               # the single sentence Jarvis would say, if it spoke
    # WallVerdict.none() builds the common (False, NONE, 0.0, "") result.

@dataclass(frozen=True)
class Interjection:       # a Path-B offer that cleared the gate
    category: str
    offer: str
    confidence: float

@dataclass(frozen=True)
class EngagementHandoff:  # THE boundary output (the seam to the engaged half)
    trigger_reason: str   # "summon" | "wall:<category>"
    summary: str          # LivingSummary.text at engage time
    recent_excerpt: str   # last few rendered lines
    detail: str = ""      # e.g. the summon utterance
```

`Utterance` is **frozen as of T-002** (`jarvis/types.py`): immutable, three fields,
and `ts` is **required** — supplied by the producer (the injected clock or the VAD
timeline), never defaulted from a hidden `time.monotonic()`. That keeps the "no
hidden clock" constraint true all the way down to the data type and lets
`RollingWindow` evict by elapsed time deterministically. sensing-engineer's
`MicSource` must stamp `ts` from the VAD timeline when it builds an `Utterance`.

`EngagementHandoff` is the two-way seam with voice-integration-engineer: I own its
shape, they own whether it carries enough context. `WallVerdict` is **frozen as of
T-005** (`jarvis/types.py`, with the `WallCategory` `StrEnum`) — `SummonController`'s
`WALL_CONFIDENCE_TO_SPEAK` gate reads its `confidence` field. (`Interjection` and
`EngagementHandoff` are documented above but land in `types.py` with their own
tasks — T-007 / T-008 — so each freezes when its first real consumer does.)

#### Contract for the real backend (T-203, local-ml-engineer)

The `WallVerdict` shape above is **frozen** — the real Qwen2.5/MLX backend (T-203)
must produce exactly this, behind the `WallBackend.detect_wall(transcript, summary)
-> WallVerdict` seam, so it drops in with **zero change** to `WallDetector` or
`SummonController`. Concretely:

- **Return the frozen `WallVerdict`** (`jarvis.types.WallVerdict`), not a dict. The
  prototype's structured-output JSON (`{is_wall, category, confidence, offer}`,
  `prototypes/attention-layer/attention_layer.py::Backend.detect_wall`) maps 1:1 —
  the JSON-schema `enum` is exactly `WallCategory`'s five values, so parse the
  model's JSON and construct the dataclass.
- **`category` is a `WallCategory` member**, and it is `NONE` **iff** `is_wall` is
  `False`. (`WallCategory(str_value)` coerces the wire string; `WallVerdict.none()`
  is the canonical non-wall result.)
- **`confidence ∈ [0.0, 1.0]`, surfaced raw.** Do **not** apply any speak threshold
  in the backend — calibrate the model's confidence, but the
  `WALL_CONFIDENCE_TO_SPEAK` cut is `SummonController` policy (T-007), downstream.
  Favor precision over recall in the prompt (only flag a wall you're confident in),
  per the success metric.
- **`offer`** is the single spoken-style sentence Jarvis would say; empty (`""`) for
  a non-wall verdict.
- **Phase-0 reference:** `HeuristicWallBackend` (`jarvis/core/wall_detector.py`) is
  the behavior to match/exceed — same seam, same return type. Tests in
  `tests/test_wall_detector.py` pin the per-category contract; the real backend
  should keep them green when swapped in (T-204).

---

## Module interfaces (the contract)

### `RollingWindow` — bounded sliding transcript (T-002) · **done**
Bounded by **both** `max_utterances` (count) and `max_seconds` (elapsed time);
evicts stale utterances on every `add` **and on every read**, so the window ages
even during silence. Takes the injected `now: Callable[[], float]` for the time
bound — eviction is relative to *now*, not the newest utterance's `ts` (the
deliberate divergence from the prototype).
```
RollingWindow(max_utterances: int, max_seconds: float, now: Callable[[], float])
add(u: Utterance) -> None
utterances() -> list[Utterance]   # oldest-first; re-evicts against now()
transcript() -> str               # "Speaker: text" lines
keywords() -> set[str]            # union of content keywords (jarvis.core.text)
```
The keyword extraction and Jaccard similarity live in `jarvis/core/text.py`
(shared with `TopicShiftDetector`, T-003).

### `TopicShiftDetector` — the delta-update trigger (T-003) · **done**
Pure function of current window content vs. the summary's basis content. Drives
"redraw only the changed pixels" — decides *when* a summary refresh is worth it.
No hidden state; takes the two keyword sets, returns a boolean.
```
TopicShiftDetector(threshold: float = 0.30)
shifted(current_keywords: set[str], basis_keywords: set[str]) -> bool
similarity(current_keywords: set[str], basis_keywords: set[str]) -> float  # inspect the drift
threshold -> float   # read-only, the configured floor
```
Jaccard (`jarvis.core.text.jaccard`) strictly below `threshold` → shift. The
metric is encapsulated behind the boolean so it can change (embedding distance,
etc.) without touching callers; the threshold is constructor-injected so it tunes
in one place. **Scope fence:** this is the pure shift decision only. The
cold-start minimum (`MIN_UTTERANCES_FOR_SUMMARY`) and the "≥2 utterances since
last update" debounce stay in `LivingSummary` (T-004) — they are *policy* about
when to bother asking, not part of the shift metric itself.

### `LivingSummary` — delta-updated summary (T-004) · **done**
Holds the running summary; re-summarizes **only** on a detected shift, via the
**injected** `SummarizerBackend`. Holds a `TopicShiftDetector` (injectable;
default-constructed) and tracks the basis keyword set the standing summary was
built on. No refresh below the cold-start minimum.
```
LivingSummary(backend: SummarizerBackend,
              detector: TopicShiftDetector | None = None,
              min_utterances: int = MIN_UTTERANCES_FOR_SUMMARY)
consider_update(window: RollingWindow) -> bool   # True iff it refreshed
text: str                                         # current summary
```
Seam (**frozen, T-004**): `SummarizerBackend.summarize(transcript: str, prev: str) -> str`
— a `typing.Protocol` declared in `core/living_summary.py`; mock = heuristic,
local-ml = Qwen2.5/MLX (T-202) drops in behind it untouched. The signature
matches `tests/fakes.py::FakeSummarizer.summarize` **exactly** — reconciled at
T-004, no disagreement found, so the test fake satisfies the protocol directly.

**Two policy fences live here, not in `TopicShiftDetector`** (the detector is the
pure metric): `MIN_UTTERANCES_FOR_SUMMARY` (cold-start: 3) and
`MIN_UTTERANCES_SINCE_UPDATE` (debounce: ≥2 new utterances since the last refresh
before a shift may re-trigger). Both are ported from the prototype and are module
constants in `core/living_summary.py`. The first real summary fires as soon as the
cold-start fence clears (even with no basis yet); after that, refresh only on a
detected shift past the debounce. Note (T-004): a topic shift only registers once
the old topic's utterances roll out of the `RollingWindow` (by count or time) — a
large window holding both topics keeps the basis/current keyword overlap above
threshold. This is correct "the conversation actually moved on" behavior, not a
brief tangent; it's what `AttentionLayer` (T-008) wiring must size the window for.

### `WallDetector` — notices the conversation needs help (T-005) · **review-gated** · **done (pending qa-tuning review)**
A **thin** interface over a **swappable** `WallBackend`, plus the Phase-0
heuristic backend. Surfaces confidence **raw** so `SummonController`'s gate can
apply `WALL_CONFIDENCE_TO_SPEAK` (precision over recall) — the detector itself
applies **no** threshold (it's a pure sensor; the speak decision is T-007 policy,
kept in one place).
```
WallDetector(backend: WallBackend)
detect(transcript: str, summary: str) -> WallVerdict   # delegates to backend.detect_wall
```
Seam (**frozen, T-005**): `WallBackend.detect_wall(transcript: str, summary: str) -> WallVerdict`
— a `typing.Protocol` in `core/wall_detector.py`; the method/args match
`tests/fakes.py::FakeWallBackend.detect_wall` exactly. Phase-0 backend:
`HeuristicWallBackend` (cue-pattern match on the last line → category, ported from
the prototype's `_mock_detect_wall` + a `stuck_point` cue so all four wall
categories are reachable). Real backend (Qwen2.5/MLX, structured output) arrives
Phase 2 (T-203) behind this interface — see "Contract for the real backend" above.

### `TurnTakingGate` — endpoint / gap / abort timing (T-006) · **review-gated** · **done (pending qa-tuning review)**
Consumes VAD speech/silence **boundary events** on an **injected clock**; reports
the timing predicates the dual-summon machine needs. No real audio — fed simulated
events in tests.

**Event-input API (designed T-006 — the gap qa-tuning flagged):** the gate takes
two edge events off the VAD timeline; time comes *only* from the injected `now`
(events carry no timestamp — the gate stamps them from `now()` on delivery). The
three predicates are **pure reads** of (state, now) — idempotent, no consume-on-read.
```
TurnTakingGate(now: Callable[[], float],
               settle_seconds: float = 0.6,            # short endpoint gap (Path A)
               politeness_gap_seconds: float = 2.0)    # long politeness gap (Path B); must be >= settle
# input (edge events; silence is measured from the most recent on_speech_end):
on_speech_start() -> None          # VAD speech onset; re-arms; latches speech_resumed if it interrupts a gap
on_speech_end()   -> None          # VAD speech offset; starts the silence/gap clock; clears the resume latch
# output predicates:
settled() -> bool                  # >= settle_seconds of silence (endpoint reached)
politeness_gap_elapsed() -> bool   # >= politeness_gap_seconds of silence (Path B)
speech_resumed() -> bool           # speech returned after a gap had opened → abort (latched until next on_speech_end)
```
**Why edge events, not a per-frame `feed(is_speech)` poll:** the gate reasons about
*durations of silence*, so it needs the two transition instants, not a level stream;
an edge API has no threshold-crossing bookkeeping and maps 1:1 onto Silero VAD's
segment callbacks in Phase 3 (T-301). The two thresholds are constructor-injected
(the asymmetry from DECISIONS.md), so qa-tuning tunes them in one place (Phase 5).
See DECISIONS.md 2026-06-15 "TurnTakingGate event-input API".

### `SummonController` — the asymmetric dual-path state machine (T-007) · **review-gated**
The heart of the MVP. Turns gate + detector signals into either an
`EngagementHandoff` (summon) or an `Interjection` offer.

```
LISTENING
  ├─ wake word in utterance ───────────────────────────► ENGAGE(reason="summon")   # Path A: immediate, unconditional
  └─ gap ≥ settle ──► run WallDetector
        └─ is_wall ∧ confidence ≥ THRESH ──────────────► PENDING_INTERJECTION
PENDING_INTERJECTION
  ├─ speech resumes ───────────────────────────────────► LISTENING   (abort, yield floor)
  ├─ silence reaches politeness_gap (~2 s) ────────────► OFFER(interjection)
  └─ same wall already offered (back-off) ─────────────► LISTENING   (no nagging)
```

**The asymmetry is the contract** (PRD 02, DECISIONS.md 2026-06-15):

| | Path A — Summon | Path B — Interjection |
|---|---|---|
| Endpoint gap | ~500–700 ms | **~2 s politeness gap** |
| Confidence bar | low (it was summoned) | **≥ ~0.70** |
| If speech resumes | n/a | **abort** |

Path A is **never** gated by Path B's conditions. Both paths take the clock and
the `WallDetector` via the constructor (qa-tuning injects fakes for both).

### `AttentionLayer` — orchestrator (T-008)
Wires the above and emits exactly three events. Runs end-to-end against
`ScriptedSource` + fake responder/voice with zero hardware and zero network.
```
ingest(u: Utterance) -> None
# emits: on_summary_update(text) | on_interjection(Interjection) | on_engagement(EngagementHandoff)
```

---

## The I/O adapter seams (where the other agents plug in)

| Seam | Direction | Core provides | Filled by |
|---|---|---|---|
| `TranscriptSource.utterances() -> Iterable[Utterance]` | in | `ScriptedSource` (dev/tests) | sensing-engineer → `MicSource` (mic → Silero VAD → ASR) |
| `SummarizerBackend.summarize(...) -> str` | in | mock heuristic | local-ml-engineer → Qwen2.5/MLX |
| `WallBackend.detect_wall(...) -> WallVerdict` | in | mock heuristic | local-ml-engineer → Qwen2.5/MLX structured output |
| `EngagedResponder.respond(handoff) -> str` | out | fake (canned line) | voice-integration-engineer → Claude `claude-opus-4-8` |
| `VoiceOutput.speak(text) -> None` | out | fake (no-op/record) | voice-integration-engineer → ElevenLabs streamed |

The `AttentionLayer` only knows the seams. Swapping `ScriptedSource` for
`MicSource`, the mock backend for the local model, and the fakes for Claude +
ElevenLabs turns the mock pipeline into the live one with no change to the core.

---

## Event flow (one utterance through the layer)

1. `TranscriptSource` yields an `Utterance`.
2. If it contains the wake word → **Path A**: `AttentionLayer` engages immediately,
   builds the `EngagementHandoff`, emits `on_engagement`. Done.
3. Otherwise `RollingWindow.add(u)` (evicting by count/time).
4. `LivingSummary.consider_update(window)`: `TopicShiftDetector` decides; on a
   shift the `SummarizerBackend` runs and `on_summary_update` fires.
5. If the utterance carries a cheap wall signal, `WallDetector.detect(...)` runs.
6. `TurnTakingGate` + `SummonController` arbitrate **Path B**: only on
   `wall ∧ confidence ≥ THRESH ∧ politeness-gap-elapsed ∧ no resumed speech ∧
   not-already-offered` does `on_interjection` fire.
7. On engage (either path) the `EngagementHandoff` crosses to the engaged half.

---

## Planned package layout (filled by T-002+)

```
src/jarvis/
├── __init__.py            # version + package docstring  (T-001, done)
├── types.py               # Utterance (FROZEN, T-002); WallVerdict + WallCategory (FROZEN, T-005); Interjection/EngagementHandoff land w/ their tasks
├── core/
│   ├── __init__.py            # core package docstring  (T-002, done)
│   ├── text.py                # shared keywords()/jaccard() helpers  (T-002, done)
│   ├── rolling_window.py      # T-002  ✅ done
│   ├── topic_shift.py         # T-003  ✅ done
│   ├── living_summary.py      # T-004  ✅ done (+ SummarizerBackend Protocol)
│   ├── wall_detector.py       # T-005  ✅ done (WallDetector + WallBackend Protocol + HeuristicWallBackend)
│   ├── turn_taking_gate.py    # T-006  ✅ done (on_speech_start/end events + 3 predicates on injected clock)
│   └── summon_controller.py   # T-007
├── adapters/
│   ├── transcript_source.py   # TranscriptSource + ScriptedSource  (core)
│   ├── backends.py            # SummarizerBackend / WallBackend protocols + mocks
│   └── engaged.py             # EngagedResponder / VoiceOutput protocols + fakes
└── attention_layer.py     # orchestrator  (T-008)
```

The seam names and signatures above are the part other agents treat as the
contract; the file paths can still move. **Landed so far (through T-004):**
`types.py` (`Utterance`), `core/text.py` (shared `keywords`/`jaccard`, ported from
the prototype), `core/rolling_window.py` (T-002), `core/topic_shift.py` (T-003),
and `core/living_summary.py` (T-004, with the `SummarizerBackend` Protocol). The
`SummarizerBackend` Protocol lives in `living_summary.py` rather than a shared
`adapters/backends.py` for now (the `adapters/` package lands when T-008 wires the
orchestrator and consolidates the seams); the signature is frozen regardless. The
remaining core files (`wall_detector.py`, `turn_taking_gate.py`,
`summon_controller.py`) land in their tasks.

---

## Next

T-002 (Core data types + RollingWindow) is the first module to land. Freeze
`Utterance` there — sensing-engineer and the whole window depend on its shape.
Coordinate the `WallVerdict` schema with local-ml-engineer before T-005, and the
simulated-clock + fakes harness with qa-tuning (T-009) before T-006/T-007.
