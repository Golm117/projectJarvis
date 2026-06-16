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
| `AttentionLayer` | orchestrator | core-engineer | — |  ✅ done (T-008)
| `TranscriptSource` (seam) | I/O adapter | core defines · sensing fills (`MicSource`); core ships `ScriptedSource` | — |  ✅ done (T-008)
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

class TriggerReason(StrEnum):  # FROZEN (T-007) — which initiation path fired
    SUMMON       = "summon"        # Path A — wake word
    INTERJECTION = "interjection"  # Path B — a detected wall cleared every gate

@dataclass(frozen=True)
class Interjection:       # FROZEN (T-007) — a Path-B offer that cleared the gate
    category: WallCategory   # never NONE (an interjection exists only for a real wall)
    offer: str
    confidence: float        # >= the controller's interjection floor; surfaced for the eval

@dataclass(frozen=True)
class SummonDecision:    # FROZEN (T-007) — what SummonController emits when Jarvis engages
    reason: TriggerReason
    interjection: Interjection | None = None   # set iff reason is INTERJECTION (Path B)
    detail: str = ""                           # the summon utterance (Path A)
    # .handoff_reason() -> "summon" | "wall:<category>" (the EngagementHandoff wire string)

@dataclass(frozen=True)
class EngagementHandoff:  # THE boundary output (the seam to the engaged half); shape frozen T-007
    trigger_reason: str   # "summon" | "wall:<category>"  (== SummonDecision.handoff_reason())
    summary: str          # LivingSummary.text at engage time   (orchestrator-supplied, T-008)
    recent_excerpt: str   # last few rendered lines             (orchestrator-supplied, T-008)
    detail: str = ""      # e.g. the summon utterance
```

**The decision/handoff boundary (T-007):** `SummonController` is a **pure decision
machine** — it emits a `SummonDecision` (which path + payload) and does **not**
assemble the `EngagementHandoff`. It holds neither the `LivingSummary` nor the
`RollingWindow`, so it *can't* fill `summary`/`recent_excerpt`; the **orchestrator**
(T-008) owns those and assembles the handoff (Path A) or dispatches the offer
(Path B) from the decision. `SummonDecision.handoff_reason()` gives the orchestrator
the `trigger_reason` wire string for free. (Logged in DECISIONS.md 2026-06-15.)

`Utterance` is **frozen as of T-002** (`jarvis/types.py`): immutable, three fields,
and `ts` is **required** — supplied by the producer (the injected clock or the VAD
timeline), never defaulted from a hidden `time.monotonic()`. That keeps the "no
hidden clock" constraint true all the way down to the data type and lets
`RollingWindow` evict by elapsed time deterministically. sensing-engineer's
`MicSource` must stamp `ts` from the VAD timeline when it builds an `Utterance`.

`EngagementHandoff` is the two-way seam with voice-integration-engineer: I own its
shape, they own whether it carries enough context. `WallVerdict` is **frozen as of
T-005** (`jarvis/types.py`, with the `WallCategory` `StrEnum`) — `SummonController`'s
`interjection_confidence_floor` gate reads its `confidence` field. `Interjection`,
`SummonDecision` and `TriggerReason` are **frozen as of T-007** (`jarvis/types.py`),
and `EngagementHandoff`'s shape is frozen there too (the orchestrator assembles it
in T-008 — see the decision/handoff boundary above).

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

### `SummonController` — the asymmetric dual-path state machine (T-007) · **review-gated** · **done (pending qa-tuning review)**
The heart of the MVP and the carrier of the success metric. Turns gate + detector
signals into a `SummonDecision` (it does **not** assemble the handoff — see the
decision/handoff boundary, above). Holds an injected `TurnTakingGate`; reads no
clock of its own (timing comes through the gate's pure predicates).
```
SummonController(gate: TurnTakingGate,
                 interjection_confidence_floor: float = 0.70)   # [0,1], precision over recall
# Path A — summon (immediate, unconditional):
on_summon(detail: str = "") -> SummonDecision                   # reason=SUMMON; ignores gate/wall/floor/back-off
# Path B — interjection (all conditions must hold; else None):
consider_interjection(verdict: WallVerdict) -> SummonDecision | None
interjection_confidence_floor -> float                         # read-only, the injected floor
```

```
LISTENING
  ├─ wake word ──► on_summon() ────────────────────────► SummonDecision(SUMMON)   # Path A: immediate, unconditional
  └─ consider_interjection(verdict):
        is_wall ∧ confidence ≥ floor ────────────────────► (pending — evaluate the gate)
PENDING_INTERJECTION  (re-evaluated each call as time advances)
  ├─ gate.speech_resumed() ────────────────────────────► None   (abort, yield floor — checked first)
  ├─ ¬gate.politeness_gap_elapsed() ───────────────────► None   (wait for the ~2 s opening)
  ├─ same wall already offered (back-off) ─────────────► None   (no nagging)
  └─ else ─────────────────────────────────────────────► SummonDecision(INTERJECTION)
```

**Path-B conditions (ALL must hold), checked in this order:** `is_wall` →
`confidence ≥ floor` → `¬speech_resumed` (**abort takes precedence over the gap** —
a latched resume suppresses even a stale-elapsed gap) → `politeness_gap_elapsed` →
back-off (the wall's `category::offer` signature ≠ the last *offered* one). Only a
fire arms back-off; a dropped/aborted wall does not. The back-off signature
deliberately excludes confidence (a re-detection of one wall at a different
confidence is the same offer).

**The asymmetry is the contract** (PRD 02, DECISIONS.md 2026-06-15):

| | Path A — Summon | Path B — Interjection |
|---|---|---|
| Endpoint gap | none — fires now | the gate's **~2 s politeness gap** |
| Confidence bar | none (it was summoned) | **≥ 0.70** (`interjection_confidence_floor`, inclusive) |
| If speech resumes | n/a | **abort** |
| De-dupe | n/a | **back-off** (same wall never offered twice in a row) |

Path A is **never** gated by Path B's conditions. The gate is injected (qa-tuning
builds it on the `SimulatedClock`); the `WallVerdict` is passed in per call (the
orchestrator runs the `WallDetector` and hands the verdict over), so the
controller stays a pure state machine over (gate, verdict). The
`interjection_confidence_floor` is constructor-injected, guarded to `[0,1]`, and
is the one knob Phase-5 (T-503) sweeps against the precision metric.

### `AttentionLayer` — orchestrator (T-008) · **done**
Wires the six core modules + the seams and emits exactly three events. Runs
end-to-end against `ScriptedSource` + fake responder/voice with zero hardware and
zero network (`python -m jarvis` / `jarvis.demo.run_demo`).
```
AttentionLayer(window, summary, detector, controller, responder, voice,
               on_summary_update=?, on_interjection=?, on_engagement=?)
ingest(u: Utterance) -> None
# emits: on_summary_update(text) | on_interjection(Interjection) | on_engagement(EngagementHandoff)
# builders: AttentionLayer.build(...) | AttentionLayer.run_scripted(lines, ...)
```
**Event flow (as built):** a wake-word line is **Path A** — add to window, then
`controller.on_summon(detail=u.text)` and engage immediately. Any other line:
`window.add` → `summary.consider_update` (emit `on_summary_update` on a refresh) →
*if* a cheap surface cue (`_has_wall_signal`) → `detector.detect(...)` →
`controller.consider_interjection(verdict)`; a returned decision emits
`on_interjection` **and** engages (Path B). **Engagement (either path)** is the
orchestrator's half of the decision/handoff boundary: it assembles the
`EngagementHandoff` from the `SummonDecision` (`handoff_reason()` + the `summary`
and `recent_excerpt` it owns), emits `on_engagement`, then dispatches it through
`EngagedResponder.respond` → `VoiceOutput.speak`. The `SummonController` holds the
**same** `TurnTakingGate` the `TranscriptSource` drives, so the Path-B predicates
read the conversation's real pacing — all on one injected clock.

### `TranscriptSource` (seam) + `ScriptedSource` (T-008) · **done · frozen**
The transcript **in** seam — `utterances() -> Iterable[Utterance]` (a `Protocol`).
`ScriptedSource` is the Phase-0 fill; sensing-engineer's `MicSource` (T-104) drops
in behind it. `ScriptedSource` carries **inter-line timing** so the politeness gap
elapses deterministically: each `ScriptedLine(speaker, text, gap)`'s `gap` is the
silence after the line, and as the source plays a line it drives the injected
clock (`clock_advance`) and the shared `TurnTakingGate`'s `on_speech_start`/
`on_speech_end` edges — the VAD edges `MicSource` will emit live. (DECISIONS.md
2026-06-16.) No real `sleep`; runs on the `ManualClock`/`SimulatedClock`.

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

## The audio sensing path (Phase 1, sensing-engineer) — `jarvis.audio`

Everything *in front of* the `TranscriptSource` seam — the always-on ears that
turn a microphone into the `Utterance` events `MicSource` (T-104) will yield. This
is sensing-engineer's package (`src/jarvis/audio/`), built bottom-up in Phase 1.

```
mic ──► AudioSource ──► SileroVad ──► TurnTakingGate edges (on_speech_start/end)
   (T-102 SoundDeviceMicSource)  (T-103)   └► segment frames ──► ASR (T-104) ──► Utterance ──► TranscriptSource
```

### `AudioSource` (seam) + `AudioFrame` + `RingBuffer` + `FakeAudioSource` (T-102) · **done**
The **audio-path analogue of the injected-backend discipline**: a `Protocol`
yielding fixed-size `AudioFrame` chunks (**16 kHz mono float32, 512-sample/32 ms**
— Silero VAD's native geometry), so the VAD (T-103) and the whole test suite
consume *frames*, never a PortAudio stream — nothing downstream depends on a
working mic. Mirrors how `ScriptedSource` keeps the core off real hardware.
```
AudioFrame(samples: np.ndarray, sample_rate: int = 16_000)   # frozen; .num_samples/.duration/.rms
class AudioSource(Protocol):
    sample_rate: int            # constant for the source's life
    frame_samples: int          # constant frame geometry
    frames() -> Iterable[AudioFrame]
```
- **`SoundDeviceMicSource`** (`audio/mic.py`) — the real always-on capture loop
  over `sounddevice`/PortAudio. The real-time PortAudio callback `push`-es each
  frame into a **bounded `RingBuffer`** (never blocks); the consumer `frames()`
  `pop`-s on its own schedule. Bounded memory: a full ring overwrites the oldest
  frame and counts `overflows` rather than growing without bound. `sounddevice`
  is imported **lazily** inside `start()` (importing the package never needs
  PortAudio). Permission/no-device failures are typed errors
  (`MicPermissionError`/`NoInputDeviceError`) — **never** fabricated audio.
- **`FakeAudioSource`** (`audio/source.py`) — the hardware-free stand-in
  (`.silence(n)` / `.tone(n)` / `.from_pattern([(kind,count),…])`); the VAD and
  ring-buffer tests run on it deterministically.

The `AudioFrame` carries **no clock** — its only "time" is `num_samples /
sample_rate`. The VAD (T-103) derives speech/silence *durations* by counting
frames and emits **edges** (`on_speech_start`/`on_speech_end`) onto the
`TurnTakingGate`, which stamps them from *its* injected clock — so the audio path
never reads a wall clock, and the same gate the Phase-0 `ScriptedSource` drove is
driven live. (Live mic smoke-tested on the M5 at T-102: ~1.47 s real capture, 0
overflows.) DECISIONS.md 2026-06-15 "Mic capture".

### `SileroVad` segmenter (T-103) · **done**
Consumes `AudioSource` frames, segments speech vs. silence, and **drives the
`TurnTakingGate`'s `on_speech_start()` / `on_speech_end()` edges** (+ an optional
generic `on_edge` callback). It emits **edges, never timestamps** — the gate
stamps them from *its own* injected clock, so this module aligns to the frozen
T-006 edge seam without reshaping it. The VAD's own timing is in **frames** (each
512 samples / 32 ms), so the audio path stays clock-free and the gate is the one
clock owner. This is what replaces the by-hand `on_speech_start`/`on_speech_end`
edges the Phase-0 `ScriptedSource` synthesized — the *same* gate + controller
logic, now driven by real audio.
```
SileroVad(classifier: FrameClassifier | None = None,   # default = real Silero (lazy torch)
          gate: TurnTakingGate | None = None,           # the edges are delivered here
          on_edge: Callable[[str], None] | None = None, # "speech_start"/"speech_end"
          speech_start_frames: int = 1,                 # debounce: ignore a 1-frame blip
          silence_end_frames: int = 6)                  # endpoint hangover (~200 ms)
process_frame(frame) -> None      # classify + fire a debounced edge if warranted
run(source: AudioSource) -> None  # consume an AudioSource to exhaustion
in_speech -> bool
```
**Hysteresis (configurable, in frame units):** a speech-start edge fires only
after `speech_start_frames` consecutive speech frames; a speech-end edge only
after `silence_end_frames` consecutive silence frames (an intra-word pause is not
a turn boundary). The VAD-side hangover is deliberately **far shorter** than the
gate's ~2 s politeness gap: the VAD owns *acoustic* segmentation, the gate owns
*social* timing.

**The `FrameClassifier` seam** (the audio-path analogue of `SummarizerBackend` /
`WallBackend`): the one thing that truly needs the model — "is *this* frame
speech?" — is injected. Default `SileroFrameClassifier` loads the real Silero VAD
(torch) lazily and scores each frame; tests inject `EnergyFrameClassifier` (pure
RMS ≥ threshold) so the **edge-sequencing logic that drives the gate** is tested
deterministically with no torch and no mic. (Live-mic + real-model check ran at
T-103 and passed.) `silero-vad`/torch recorded in DECISIONS.md.

Feeds **T-104** (`MicSource`) the frames of each speech segment for ASR
(`mlx-whisper base.en`), and stamps `Utterance.ts` from the VAD timeline.

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
├── __main__.py            # `python -m jarvis` → runs the mock demo  (T-008, done)
├── clock.py               # ManualClock (deterministic injected clock for the demo)  (T-008, done)
├── demo.py                # run_demo() — scripted conversation through the real layer  (T-008, done)
├── types.py               # Utterance (FROZEN, T-002); WallVerdict + WallCategory (FROZEN, T-005); Interjection/SummonDecision/EngagementHandoff (FROZEN, T-007)
├── core/
│   ├── __init__.py            # core package docstring  (T-002, done)
│   ├── text.py                # shared keywords()/jaccard() helpers  (T-002, done)
│   ├── rolling_window.py      # T-002  ✅ done
│   ├── topic_shift.py         # T-003  ✅ done
│   ├── living_summary.py      # T-004  ✅ done (+ SummarizerBackend Protocol)
│   ├── wall_detector.py       # T-005  ✅ done (WallDetector + WallBackend Protocol + HeuristicWallBackend)
│   ├── turn_taking_gate.py    # T-006  ✅ done (on_speech_start/end events + 3 predicates on injected clock)
│   └── summon_controller.py   # T-007  ✅ done (SummonController; emits SummonDecision, not the handoff)
├── adapters/                  # T-008  ✅ done (the seam package landed here)
│   ├── __init__.py            #   re-exports the seams + mocks
│   ├── transcript_source.py   #   TranscriptSource Protocol + ScriptedSource (drives clock + gate)
│   ├── backends.py            #   re-exports SummarizerBackend/WallBackend + HeuristicSummarizerBackend (+ HeuristicWallBackend)
│   └── engaged.py             #   EngagedResponder / VoiceOutput Protocols + PrintResponder/PrintVoice stand-ins
├── audio/                     # T-102+  ✅ T-102, T-103 done (sensing-engineer, Phase 1)
│   ├── __init__.py            #   re-exports AudioSource/AudioFrame/RingBuffer/FakeAudioSource + SileroVad/FrameClassifier
│   ├── source.py             #   AudioSource Protocol + AudioFrame + RingBuffer + FakeAudioSource  (T-102)
│   ├── mic.py                #   SoundDeviceMicSource — real PortAudio always-on capture loop  (T-102)
│   └── vad.py                #   SileroVad + FrameClassifier seam (SileroFrameClassifier/EnergyFrameClassifier)  (T-103)
└── attention_layer.py     # orchestrator  (T-008)  ✅ done
```

**Phase 0 modules: COMPLETE.** All six core modules + the orchestrator + the
`TranscriptSource` seam are built, tested, and run end-to-end in mock mode. The
`SummarizerBackend` Protocol stayed in `core/living_summary.py` and `WallBackend`
in `core/wall_detector.py` (their frozen homes); `adapters/backends.py`
*re-exports* both so the orchestrator/demo import from one place — a single source
of truth per protocol, no redefinition. Phase 1 replaces `ScriptedSource` with
`MicSource` behind the frozen `TranscriptSource` seam.

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
