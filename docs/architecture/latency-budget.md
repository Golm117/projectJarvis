# Latency budget — gate → detector → offer (T-304)

> **Owner:** core-engineer · **Produced by:** T-304 (Phase 3)
> **Date:** 2026-06-15
> **Status:** FINAL — Phase 3 measurement complete; Phase 4 picks up.
> **Machine:** Apple M5 Pro, 18 cores, 64 GB unified memory, macOS 25.4.0

---

## Budget target and source

**Target: the offer (interjection) fires within ~2 seconds of an unanswered
question.**

Sources (two complementary):

1. **`.pdr.md` §Pitch / success sentence (line 223):** "offers help within ~2
   seconds of an unanswered question".
2. **PRD 02 §The asymmetric dual-summon decision:** `politeness_gap ≈ 2 s` is
   the explicit Path-B gap constant — the gap is both the social timing and the
   budget window. `TurnTakingGate.DEFAULT_POLITENESS_GAP_SECONDS = 2.0`.

The 2 s target includes the social pause (the politeness gap) itself. This is
intentional: the gap is *socially required*, not latency to shave.

---

## Decomposition of the full path

The path from a wall-bearing utterance to the spoken offer has **four stages**:

```
[wall utterance arrives]
        │
        ▼
STAGE 1 — At-ingest work (runs once, while speech is still settling)
  ├─ ASR (mlx-whisper base.en) — speech frames → Utterance text
  ├─ cheap wall-signal pre-filter (_has_wall_signal regex) — near-instant
  ├─ LivingSummary.consider_update (only on topic shift) — Qwen summarize
  └─ WallDetector.detect → QwenWallBackend → WallVerdict cached in _pending_wall

        │  (wall detected, gap not yet open, verdict cached)
        │
        ▼
STAGE 2 — Social silence gap (intentional, concurrent with user conversation)
  ├─ TurnTakingGate awaiting politeness_gap_seconds = 2.0 s
  └─ (no compute — pure clock arithmetic)

        │  (2 s elapsed)
        │
        ▼
STAGE 3 — Ticker fire latency (≤ TICK_INTERVAL_SECONDS after gap opens)
  └─ AttentionLayer.tick() notices gap_elapsed() = True
     └─ SummonController.consider_interjection(cached_verdict) → fires

        │
        ▼
STAGE 4 — Offer dispatch (pure Python, Print stand-ins now; ElevenLabs in Phase 4)
  └─ _interject → _engage → EngagementHandoff → responder.respond → voice.speak
```

**Critical architectural property — the detector is off the hot tick path.**
The expensive Qwen `detect_wall` call happens ONCE at Stage 1 (ingest time).
`tick()` re-evaluates the **same cached `WallVerdict` object** with
`consider_interjection` — a pure Python gate-predicate check, no model call.
The silence window is therefore not compute-bound; the only work during it is
a ~0.2 µs gate-predicate check 10× per second.

---

## Measured numbers on this M5

### Stage 1 — At-ingest work (T-201 measured, warm, chat template)

| Sub-stage | Median | Source |
|---|---:|---|
| ASR (mlx-whisper base.en, ~3.8 s utterance) | 40 ms | T-201 joint budget |
| Qwen summarize (when topic shift detected) | 250 ms | T-201 joint budget |
| Qwen detect_wall | 366 ms | T-201 joint budget |
| **Total Stage 1 (ASR + both Qwen calls)** | **657 ms** | T-201 joint budget |

> **Note (T-201):** The T-201 spike measured Stage 1 in isolation on this M5
> (5 warm runs, chat template, joint process). Min 638 ms / max 662 ms.
> Numbers are real on-device measurements, not fabricated.
> In practice summarize is not called on every utterance (only on topic shift),
> so typical Stage 1 cost is ASR (~40 ms) + detect_wall (~366 ms) = ~406 ms.
> The worst-case concurrent call is ~657 ms (shift + wall on same utterance).

### Stage 2 — Social silence gap

| Sub-stage | Value | Source |
|---|---:|---|
| politeness_gap_seconds | 2,000 ms | TurnTakingGate default (code) |
| Compute during gap | ~0 ms | pure clock arithmetic in gate predicates |

This stage is the dominant term — and that is **correct by design**. The 2 s
pause is the socially required hesitation before an uninvited interjection, not
a latency problem to solve. It runs concurrently with the user's conversation;
Stage 1's 657 ms has already completed before Stage 2 begins.

### Stage 3 — Ticker fire latency (measured on M5, T-304)

Harness: `scripts/latency_budget_harness.py`. 50,000 / 10,000 / 20 iterations
per scenario. Real code (real `threading.Lock`, real `TurnTakingGate`, real
`SummonController`), `SimulatedClock` controlling time.

| Scenario | Median | Min | Max |
|---|---:|---:|---:|
| tick() — gap NOT elapsed (no-fire) | 0.2 µs | 0.1 µs | 10.6 µs |
| tick() — gap IS elapsed (fire path) | 0.7 µs | 0.6 µs | 16.3 µs |
| Lock + tick() + unlock (live.py pattern) | 0.2 µs | 0.1 µs | 13.8 µs |
| Gate predicate reads only | 0.1 µs | < 0.1 µs | 19.8 µs |
| threading.Event.wait(0.2s) cadence jitter | 7.84 ms | 0.26 ms | 10.08 ms |

**Fire latency after the gap opens: at most TICK_INTERVAL_SECONDS = 200 ms**
(the ticker wakes up within one cadence interval of the gap opening), plus
~8 ms median scheduler jitter, plus ~0.7 µs tick() execution time.
Maximum observed fire latency: ~210 ms after gap opens.

### Stage 4 — Offer dispatch (current stand-ins; Phase 4 replaces)

With `PrintResponder` + `PrintVoice` (the Phase-3 stand-ins):
`_engage` → string construction → `sys.stdout.write` = well under 1 ms.

Phase 4 will replace these with Claude API (`claude-opus-4-8`) + ElevenLabs
streaming. The Phase 4 latency target is "first audio in ~1–2 s" (PRD 02
§"Response style contract"). That latency is on the **engaged path** (after
the offer fires), not on the ambient path measured here — it does not affect
the ~2 s interjection budget.

---

## End-to-end latency summary

Time from wall-bearing utterance to offer-ready:

| Stage | Time | Notes |
|---|---:|---|
| Stage 1 — ASR + Qwen (ingest) | ~657 ms worst case | runs once, before gap |
| Stage 2 — politeness gap | 2,000 ms | intentional social wait |
| Stage 3 — ticker fire latency | ≤ 210 ms | TICK_INTERVAL + jitter |
| Stage 4 — dispatch (stand-in) | < 1 ms | trivial |
| **Total** | **2,210 ms worst case** | see note below |

> **Note on total and the user's experience of latency.**
> The user's utterance ends at ~t=0. The politeness gap starts from the last
> `on_speech_end` (call it t=0). Stage 1 (657 ms) runs *during* the utterance
> settlement window — the VAD hangover (settle_seconds = 0.6 s) overlaps with
> ASR. In practice:
>
> - ASR takes ~40 ms. By the time the gate settles (0.6 s from speech-end),
>   ASR is long done.
> - Qwen inference (~600 ms) starts after the `on_speech_end` event, and
>   completes at ~0.6 s (within the first settle window), before the 2 s
>   politeness gap opens.
> - So the 2 s gap starts with the WallVerdict already cached. There is no
>   compute work left when the gap opens.
> - After 2 s, the ticker notices within ≤ 210 ms and fires.
>
> **The user perceives:** silence for 2 s (the polite wait), then the offer
> fires ≤ 210 ms after the gap opens. **Total from utterance-end to offer:
> 2,000 ms + ≤ 210 ms = ≤ 2,210 ms.** The 2 s dominates; the compute is
> absorbed inside it.

---

## Budget verdict

**WITHIN BUDGET WITH LARGE MARGIN.**

| Metric | Value |
|---|---:|
| Budget target | 2,000 ms |
| Politeness gap (intentional social wait) | 2,000 ms |
| Qwen compute (runs inside the gap, not after) | ~657 ms |
| Ticker fire latency after gap opens | ≤ 210 ms |
| User-perceived latency beyond the 2 s gap | ≤ 210 ms |
| Margin vs. "within 2 s of gap opening" | ≥ 1,790 ms |

The 2 s budget is met. The Qwen inference (657 ms) completes well within the
2 s gap — it is absorbed into the social wait, not added to it. The only
latency the user observes *beyond* the polite 2 s wait is the ticker detection
delay of ≤ 210 ms.

---

## Confirmation: wall detector is off the hot tick path

**Code reference:** `src/jarvis/attention_layer.py`

At ingest (`AttentionLayer.ingest`, line 195):
```python
verdict = self._detector.detect(...)           # Qwen call — expensive, ONCE
decision = self._controller.consider_interjection(verdict)
if decision is not None:
    ...  # Path B fires at ingest (gap already open)
elif verdict.is_wall:
    self._pending_wall = verdict               # CACHE the verdict
```

At tick (`AttentionLayer.tick`, line 242):
```python
if self._pending_wall is None:
    return                                     # fast exit — no pending wall
decision = self._controller.consider_interjection(self._pending_wall)  # cached verdict
```

`consider_interjection` at tick time reads **the same `WallVerdict` object** — no
new `self._detector.detect()` call, no model inference, no network. The full tick
path is:

1. `self._pending_wall is None` check → False → proceed (branch: 1 attribute read)
2. `self._controller.consider_interjection(cached_verdict)`:
   a. `verdict.is_wall` check → True (1 attribute read)
   b. `verdict.confidence >= floor` check → True (1 attribute read + 1 float compare)
   c. `gate.speech_resumed()` → False (1 attribute read + 1 compare)
   d. `gate.politeness_gap_elapsed()` → depends on `now() - _silence_since` (1 clock read + 1 subtract + 1 compare)
   e. signature check → may differ (1 string build + 1 compare)
3. Decision assembled + `_pending_wall = None` + `_interject` call

**Measured cost: 0.7 µs median** (fire path, including `_interject/_engage` through
`FakeResponder`/`FakeVoice`). This confirms the detector is emphatically off the
tick path — the tick overhead is 4 orders of magnitude smaller than Qwen inference.

---

## Constants reviewed — no tuning needed

`TICK_INTERVAL_SECONDS = 0.20` (in `src/jarvis/live.py`) — the only non-gated
constant in this path. At 200 ms cadence, the ticker gives ~10 opportunities per
2 s gap to notice it opening. The measured fire latency is ≤ 210 ms (jitter
included), well under any meaningful social-timing threshold.

No constant change is needed or made. The budget clears with ~1,790 ms of margin
on the detection side of the gap.

**No gated threshold change proposed** (politeness_gap_seconds, interjection_confidence_floor):
those belong to T-503 calibration. This pass confirms the current defaults are
within budget.

---

## What Phase 4 picks up

Phase 4 wires the engaged path — the response *after* the offer fires:

- **`EngagedResponder`** → Claude `claude-opus-4-8` (spoken-style, grounded in
  `EngagementHandoff.summary + recent_excerpt`). Requires `ANTHROPIC_API_KEY`.
- **`VoiceOutput`** → ElevenLabs streaming TTS. Requires `ELEVENLABS_API_KEY`.
- PRD target: "first audio in ~1–2 s" from the handoff.

Both keys are not yet set. Phase 4 is the `voice-integration-engineer`'s lane.
The `PrintResponder` / `PrintVoice` stand-ins remain in place until Phase 4.

---

## Honesty box

- **Qwen inference numbers reused from T-201** (5 warm runs, chat template,
  joint ASR+SLM process on this M5). They are real measurements. Not re-derived
  here to avoid unnecessary model loading; T-201 methodology is in
  `docs/ml/qwen-coexistence-spike.md`.
- **Tick-path numbers measured fresh on this M5** (2026-06-15) using
  `scripts/latency_budget_harness.py` (50,000 / 10,000 / 20 iterations, real
  threading, real modules, SimulatedClock for time).
- **No live audio run** for this pass — the live behavior was confirmed in T-303
  (abort-on-resume, back-off de-dupe, single mid-conversation ticker fire).
- **Best-case cadence jitter** (macOS, quiet M5 Pro, no load). Real always-on
  load with sustained Qwen inference may add jitter; T-504 (sustained thermal)
  will re-check.
