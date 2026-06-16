# SLM Backend — Design and Contracts

> **Owner:** local-ml-engineer · **Domain:** `docs/ml/`
> **Status:** LIVING DOCUMENT (T-202 baseline; T-203 extends with wall backend).
> **Grounded in:** `docs/ml/qwen-coexistence-spike.md` (T-201 measurements),
> `docs/architecture/module-map.md` (frozen seams), DECISIONS.md 2026-06-15.

This document describes the real on-device SLM (Small Language Model) runtime for
project Jarvis: the model choice, the shared loader, the prompt designs, and the
contracts the backends expose to the core via the frozen seams.

---

## Runtime choice (frozen by T-201)

**Model:** `mlx-community/Qwen2.5-3B-Instruct-4bit`

Rationale in full: `docs/ml/qwen-coexistence-spike.md`. Key facts:
- 1.5B was **eliminated** — non-functional for `detect_wall` (returns `is_wall: false`
  on every input, including unambiguous `explicit_ask` cases).
- 3B produces correct structured output, calibrated confidence (0.8–0.9 on genuine
  walls), and natural-language offers.  3/4 correct on a 4-scenario quality matrix.
- Joint ASR+SLM budget: **657 ms median** (ASR 40 ms + summarize 250 ms + detect_wall
  366 ms) vs the 2 s offer budget → **1,343 ms margin** on this M5 Pro (64 GB).
- 7B was not benchmarked — 3B's quality gaps are prompt-engineering issues, not
  fundamental capability failures.  If T-203 prompt work can't close the precision
  gap, 7B is the escalation path (flag to human — needs a budget measurement).

**Library:** `mlx_lm` (`mlx-lm>=0.31.3`) — promoted to real `[project.dependencies]`
at T-202 (DECISIONS.md 2026-06-15 "mlx-lm promoted from slm-spike group to real deps").

---

## Mandatory: chat template, not raw prompts

These are **Instruct-tuned** models.  Raw string prompts produce repetition,
degraded quality, and ~2× higher latency on both 1.5B and 3B (measured, T-201).

**Always** use:
```python
prompt = tokenizer.apply_chat_template(
    messages,
    tokenize=False,
    add_generation_prompt=True,
)
```

where `messages` is a list of `{"role": "system"|"user", "content": "..."}` dicts.

---

## Package layout

```
src/jarvis/ml/
├── __init__.py        # re-exports QwenModel, QwenSummarizerBackend
├── qwen.py            # QwenModel — shared lazy loader (T-202)
├── summarizer.py      # QwenSummarizerBackend — SummarizerBackend seam (T-202)
└── wall.py            # QwenWallBackend — WallBackend seam (T-203, not yet)
```

Parallel to `src/jarvis/audio/` (the sensing domain package).

---

## Shared loader: `QwenModel`

The `QwenModel` class (`src/jarvis/ml/qwen.py`) is the only place that touches
`mlx_lm.load` and `mlx_lm.generate`.

```python
class QwenModel:
    def __init__(self, model_path: str = DEFAULT_MODEL_PATH) -> None: ...

    def generate(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 128,
    ) -> str: ...
```

**Design decisions:**
- `_ensure_loaded()` defers the `from mlx_lm import load, generate` until the
  first `generate()` call — importing `jarvis.ml` never loads MLX.
- One `QwenModel` instance is constructed at startup and injected into BOTH the
  `QwenSummarizerBackend` (T-202) and the `QwenWallBackend` (T-203).  The ~2 GB
  weights are loaded once.  This is the "shared-loader" design from
  `docs/ml/working-notes.md`.
- The `generate()` method applies the chat template internally (via
  `tokenizer.apply_chat_template`), so callers just pass a message list — the
  template discipline is enforced in one place.
- `verbose=False` suppresses `mlx_lm`'s progress output.

**Injection at startup (T-204 wiring):**
```python
from jarvis.ml import QwenModel, QwenSummarizerBackend
# from jarvis.ml.wall import QwenWallBackend  # T-203

model = QwenModel()  # one instance; lazy; ~300 ms cold load from cache
summarizer = QwenSummarizerBackend(model)
# wall_backend = QwenWallBackend(model)  # T-203 reuses the same model
```

---

## Summarizer backend contract (T-202)

**Seam (frozen, T-004):**
```python
class SummarizerBackend(Protocol):
    def summarize(self, transcript: str, prev: str) -> str: ...
```

Declared in `jarvis.core.living_summary`.  Re-exported in `jarvis.adapters.backends`.

**Implementation:** `QwenSummarizerBackend` (`src/jarvis/ml/summarizer.py`)

```python
class QwenSummarizerBackend:
    def __init__(self, model: QwenModel, max_tokens: int = 80) -> None: ...
    def summarize(self, transcript: str, prev: str) -> str: ...
```

**Prompt design:**

```
SYSTEM:
You are a concise meeting note-taker. Summarize what is being discussed in
1-3 sentences. Use only facts stated in the transcript — do not add
information. If a previous summary is provided, update it to reflect the
latest conversation.

USER:
TRANSCRIPT:
{transcript}

PREVIOUS SUMMARY:
{prev}

Write an updated summary (1-3 sentences, no preamble, no explanation):
```

Key choices:
- **System** positions the model + enforces no-hallucination ("only facts stated").
- **User** supplies the transcript + prev summary and requests a delta update.
- Empty `transcript` is replaced with `"(no transcript yet)"` to avoid template
  edge cases.
- Empty `prev` is replaced with `"(none — this is the first summary)"`.
- `max_tokens=80`: spike measured ~250 ms median at this budget; fits 1–3 sentences.

The `_build_messages(transcript, prev)` helper is exported for unit tests that
verify message construction without a model.

**Spike-measured latency:** ~250 ms median (warm, in-process, with MLX KV cache).

---

## Wall-detection backend contract (T-203) — DESIGN STUB

**Seam (frozen, T-005):**
```python
class WallBackend(Protocol):
    def detect_wall(self, transcript: str, summary: str) -> WallVerdict: ...
```

Declared in `jarvis.core.wall_detector`.  `WallVerdict` is frozen in `jarvis.types`:
```python
@dataclass(frozen=True)
class WallVerdict:
    is_wall: bool
    category: WallCategory   # NONE iff is_wall is False
    confidence: float        # [0.0, 1.0]; raw — no threshold applied here
    offer: str               # empty iff is_wall is False
```

**Implementation target:** `QwenWallBackend` (`src/jarvis/ml/wall.py`) — T-203.

Key requirements for T-203 (from T-201 spike findings):
1. Parse the model's JSON output into a `WallVerdict` dataclass.  The schema:
   `{"is_wall": bool, "category": str, "confidence": float, "offer": str}`.
2. `category` coerced via `WallCategory(str_value)` — it's a `StrEnum`.
3. **Do NOT apply a confidence threshold** — that's `SummonController` policy
   (T-007, `interjection_confidence_floor=0.70`).  Surface confidence raw.
4. **Favor precision over recall** in the prompt — 3B has a false-positive tendency
   (it flagged a clear decision as `explicit_ask` in 1/4 test scenarios).  Tighten
   with "only flag if confident" + stronger no-wall guidance.
5. Put the JSON schema in the **user message** (not the system message) — the spike
   found this produces more reliable JSON than embedding it in the system prompt.
6. `max_tokens=120` (spike measured ~366 ms at this budget).
7. On JSON parse failure, return `WallVerdict.none()` (never raise to the caller).

**Prototype prompt design (for T-203 to refine):**

```
SYSTEM:
You are an assistant that detects when a conversation hits a wall — an
unanswered question, factual gap, stuck point, or explicit request for help.
Be conservative: only flag a wall when you are confident one exists.

USER:
TRANSCRIPT:
{transcript}

SUMMARY:
{summary}

Reply with ONLY a JSON object:
{"is_wall": bool, "category": "unanswered_question"|"factual_gap"|"stuck_point"|"explicit_ask"|"none", "confidence": 0.0-1.0, "offer": "one sentence Jarvis would say, or empty string"}
```

**Spike-measured latency:** ~366 ms median (warm, in-process).

This stub will be filled in when T-203 implements `QwenWallBackend`.

---

## Latency budget summary

| Step | Median (warm, joint, 5 runs) | Notes |
|---|---:|---|
| ASR (mlx-whisper base.en) | 40 ms | fixed; stays at base.en |
| SLM summarize | 250 ms | max_tokens=80 |
| SLM detect_wall | 366 ms | max_tokens=100–120 |
| **Total pipeline** | **657 ms** | vs. 2,000 ms offer budget |
| **Margin** | **1,343 ms** | absorbs prompt/transcript size variation |

Peak joint RSS: ~3,271 MB (M5 Pro 64 GB — 5.1 % of unified memory).

---

## What I could and couldn't measure (honesty box)

**Measured:** isolated quality + latency (chat template, both model sizes); 4-scenario
wall-detection quality matrix; joint ASR+SLM budget (5 warm runs); sustained drift
(10 runs, flat); isolated + joint peak RSS; thermal state (no warnings).

**Not measured:** 7B-Instruct-4bit joint budget; small.en ASR joint budget; multi-hour
thermal/battery behavior (T-504); prompt variants for T-203 beyond the 4 qualitative
scenarios; real-room (noisy) transcript quality (T-502).

**Nothing fabricated.** All numbers are from real measurements on this M5 Pro (64 GB).
