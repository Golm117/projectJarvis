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
@dataclass(frozen=True)
class Utterance:
    speaker: str
    text: str
    ts: float            # monotonic seconds (from the injected clock / VAD)

@dataclass(frozen=True)
class WallVerdict:        # what a WallBackend returns
    is_wall: bool
    category: str         # unanswered_question | factual_gap | stuck_point | explicit_ask | none
    confidence: float
    offer: str            # the single sentence Jarvis would say, if it spoke

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

`EngagementHandoff` is the two-way seam with voice-integration-engineer: I own its
shape, they own whether it carries enough context. `WallVerdict` is the seam I
freeze **with** local-ml-engineer before they build the real backend — my
`WALL_CONFIDENCE_TO_SPEAK` gate reads *their* `confidence` field.

---

## Module interfaces (the contract)

### `RollingWindow` — bounded sliding transcript (T-002)
Bounded by **both** `max_utterances` (count) and `max_seconds` (elapsed time);
evicts stale utterances on every `add`. Takes an injected clock for the time
bound.
```
add(u: Utterance) -> None
utterances() -> list[Utterance]
transcript() -> str
keywords() -> set[str]
```

### `TopicShiftDetector` — the delta-update trigger (T-003)
Pure function of current window content vs. the summary's basis content. Drives
"redraw only the changed pixels" — decides *when* a summary refresh is worth it.
```
shifted(current_keywords: set[str], basis_keywords: set[str]) -> bool
```
(Prototype uses Jaccard < `TOPIC_SHIFT_THRESHOLD`; the detector encapsulates that
choice behind a boolean so the metric can change without touching callers.)

### `LivingSummary` — delta-updated summary (T-004)
Holds the running summary; re-summarizes **only** on a detected shift, via the
**injected** `SummarizerBackend`. No refresh below the cold-start minimum.
```
consider_update(window: RollingWindow) -> bool   # True iff it refreshed
text: str                                         # current summary
```
Seam: `SummarizerBackend.summarize(transcript: str, prev: str) -> str`
(mock = heuristic; local-ml = Qwen2.5/MLX).

### `WallDetector` — notices the conversation needs help (T-005) · **review-gated**
Interface over a **swappable** `WallBackend`, plus a heuristic mock backend.
Surfaces confidence so `SummonController`'s gate can apply
`WALL_CONFIDENCE_TO_SPEAK` (precision over recall).
```
detect(transcript: str, summary: str) -> WallVerdict
```
Seam: `WallBackend.detect_wall(transcript: str, summary: str) -> WallVerdict`.
Real backend (local SLM, structured output) arrives Phase 2 behind this interface.

### `TurnTakingGate` — endpoint / gap / abort timing (T-006) · **review-gated**
Consumes VAD/clock events on an **injected clock**; reports the timing predicates
the dual-summon machine needs. No real audio — fed simulated events in tests.
```
settled() -> bool                  # endpoint reached
politeness_gap_elapsed() -> bool   # ~2 s of quiet (Path B)
speech_resumed() -> bool           # someone kept talking → abort
```

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
├── types.py               # Utterance, WallVerdict, Interjection, EngagementHandoff  (T-002)
├── core/
│   ├── rolling_window.py      # T-002
│   ├── topic_shift.py         # T-003
│   ├── living_summary.py      # T-004
│   ├── wall_detector.py       # T-005 (interface + mock backend)
│   ├── turn_taking_gate.py    # T-006
│   └── summon_controller.py   # T-007
├── adapters/
│   ├── transcript_source.py   # TranscriptSource + ScriptedSource  (core)
│   ├── backends.py            # SummarizerBackend / WallBackend protocols + mocks
│   └── engaged.py             # EngagedResponder / VoiceOutput protocols + fakes
└── attention_layer.py     # orchestrator  (T-008)
```

This layout is a proposal, not yet built — the modules land in their tasks. The
seam names and signatures above are the part that other agents should treat as
the contract; the file paths can move.

---

## Next

T-002 (Core data types + RollingWindow) is the first module to land. Freeze
`Utterance` there — sensing-engineer and the whole window depend on its shape.
Coordinate the `WallVerdict` schema with local-ml-engineer before T-005, and the
simulated-clock + fakes harness with qa-tuning (T-009) before T-006/T-007.
