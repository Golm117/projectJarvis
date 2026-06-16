# Qwen2.5/MLX runtime spike + joint ASR coexistence budget

> **Owner:** local-ml-engineer · **Domain:** `docs/ml/` · **Task:** T-201 (Phase 2)
> **Status:** DONE — both candidates measured on this machine; model size selected.
> **Grounding:** the joint-budget coexistence flag in `docs/audio/asr-spike.md`
> §"M5-budget coexistence", the frozen `WallVerdict` / `SummarizerBackend` /
> `WallBackend` contracts in `docs/architecture/module-map.md`, and the hard
> constraint that **no ambient audio leaves the device**.

This is the deliverable T-201 requires: the Qwen2.5/MLX size spike, **including
the mandatory joint ASR+SLM always-on budget measurement** that was flagged
repeatedly in the ASR spike and NOTES.md as a prerequisite before either side
freezes model sizes.

---

## TL;DR — recommendation

**Use `Qwen2.5-3B-Instruct-4bit` (mlx-community, 4-bit quantized) as the Phase-2
model for both `summarize()` and `detect_wall()`.** Keep ASR at **`base.en`** — the
joint budget clears with substantial margin (657 ms end-to-end, 1343 ms to spare
vs the 2 s offer budget) and `small.en` would add memory pressure with no
quality justification for the SLM.

**Why not 1.5B:** the 1.5B model is functionally broken for `detect_wall` — it
returns `is_wall: false` with confidence 0.0 on every input tested, including
unambiguous `explicit_ask` cases where someone directly says "can you help me
understand X." The wall detection requirement (the success-metric-critical path)
eliminates 1.5B from contention entirely. Latency and memory would be attractive
(484 ms joint, 1.3 GB RSS) but they are irrelevant when the task can't be
performed.

---

## Method

### Machine
- **Apple M5 Pro**, 18 cores, **64 GB** unified memory, macOS (Darwin 25.4.0).
- Toolchain: **uv** (managed CPython 3.11.15). Spike deps installed into an
  isolated **`slm-spike` uv dependency group** (`uv add --group slm-spike mlx-lm`)
  — same isolation discipline as the ASR spike.
- Benchmark harness: `/tmp/slm-spike/spike.py` (ephemeral, not committed).
  NOT under `tests/` — the default pytest suite never needs Qwen weights.

### Candidates (both 4-bit quantized, MLX-community builds)

| Candidate | HuggingFace repo | Quant | Disk size |
|---|---|---|---:|
| **Qwen2.5-1.5B-Instruct-4bit** | `mlx-community/Qwen2.5-1.5B-Instruct-4bit` | 4-bit (MLX default) | ~1.1 GB |
| **Qwen2.5-3B-Instruct-4bit** | `mlx-community/Qwen2.5-3B-Instruct-4bit` | 4-bit (MLX default) | ~2.0 GB |

Both are **Instruct-tuned** (the `-Instruct` suffix), which is required —
the base models do not follow the structured-output JSON instruction. Both run
via `mlx_lm.load` + `mlx_lm.generate` from `mlx-lm==0.31.3`. Both models were
pulled from HuggingFace hub on first use during this spike (not pre-cached).

**Model loading uses the chat template** (`tokenizer.apply_chat_template`) — not
a raw string — because these are instruction-tuned models with a specific
system/user message structure. Raw-string prompts (without the template) produced
degraded quality and repetition on both sizes; the chat-template results below are
the authoritative numbers.

### Audio sample (stated exactly, with provenance)
Same technique as `docs/audio/asr-spike.md`:
- **Tool:** macOS `say -v Daniel` → AIFF → `ffmpeg -ar 16000 -ac 1` → 16 kHz mono
  PCM WAV. Ground truth is exact (authored text).
- **Text:** *"did anyone figure out whether the build is still failing on the new
  machine"* — a realistic ambient-meeting-style question.
- **Duration:** 3.82 s / 61,153 samples @ 16 kHz.
- **Path:** `/tmp/slm-spike/utterance_16k.wav` (ephemeral, regenerable).
- **Provenance:** synthesized with the same script as the ASR spike; no external
  audio fetched, no fabricated data.

### What was measured

1. **Isolated quality + latency** (no ASR running): the two representative Phase-2
   prompts (summarize and detect_wall), 1 warmup + 3 timed runs per prompt.

2. **Qualitative wall detection coverage** (4 scenarios): tested all four wall
   categories plus a no-wall case using proper chat-template prompts, to reveal
   model capability beyond a single data point.

3. **Joint ASR+SLM budget** (the whole point): ASR (`mlx-whisper base.en` via
   `mlx_whisper.transcribe`) then SLM summarize then SLM detect_wall, all in one
   Python process on one M5 GPU. 1 warmup + 5 timed runs. Both models share
   MLX/Metal/unified-memory.

4. **Sustained drift**: 10 consecutive joint pipeline runs; compare first-5 vs
   last-5 median for thermal throttling.

5. **Peak RSS**: isolated (fresh subprocess per model) and joint (same process).

---

## Results

### Isolated quality + latency (3 warm runs, chat template)

| Model | Summarize median | Detect-wall median | Peak RSS (isolated) |
|---|---:|---:|---:|
| **1.5B** | 604 ms | 622 ms | 1,330 MB |
| **3B** | 474 ms | 609 ms | 2,156 MB |

> **Note:** Isolated latency runs include the full KV-cache state from a prior
> summarize call, so detect-wall latency reflects in-session state. This is
> representative of the real pipeline where summarize and detect-wall are called
> sequentially in the same process.

**Summarize quality (isolated):**
- 1.5B: `"Bob suspects the caching layer caused the P95 latency increase, and Alice
  suggests rolling back the change to restore baseline performance."` — adequate
  factual summary.
- 3B: `"Bob mentions P95 latency jumped after Tuesday deploy. Alice suggests rolling
  back caching change first. Bob agrees and offers to create a PR."` — more specific
  and action-oriented.

**Detect-wall quality (isolated, single data point):**
- 1.5B: `{"is_wall": false, "category": "none", "confidence": 0.0, "offer": ""}` —
  **wrong** on a transcript with two unanswered questions.
- 3B: `{"is_wall": true, "category": "factual_gap", "confidence": 0.9,
  "offer": "I can check the exact P95 numbers from Datadog for you."}` — correct,
  with a natural offer.

---

### Wall detection coverage (4 scenarios, chat template, qualitative)

| Scenario | Expected | 1.5B verdict | 3B verdict |
|---|---|---|---|
| `explicit_ask` — "can someone help me understand X?" | wall | `is_wall: false` ❌ | `is_wall: true, unanswered_question` ✅ |
| `unanswered_question` — data not at hand, couldn't answer | wall | `is_wall: false` ❌ | `is_wall: true, factual_gap` ✅ |
| `stuck_point` — "going in circles for an hour" | wall | `is_wall: false` ❌ | `is_wall: true, stuck_point` ✅ |
| `no_wall` — decision reached, PR in 10 min | no wall | `is_wall: false` ✅ | `is_wall: true, explicit_ask` ❌ (false positive) |

**1.5B: 1/4 correct (trivially correct only on the no-wall case — by always outputting
the same `is_wall: false` JSON regardless of input).**

**3B: 3/4 correct.** The one false positive (seeing a decision as an
`explicit_ask`) is a prompt-engineering issue that T-203 will address, not a
fundamental model-capability failure. 3B has a genuine read of the conversation
state; 1.5B does not.

**The quality verdict is unambiguous:** 1.5B cannot perform the `detect_wall` task
at any confidence level. It is eliminated from contention.

---

### Joint ASR+SLM budget (5 warm runs, chat template, both models in same process)

This is the canonical budget measurement. The pipeline for each run:
`ASR (mlx-whisper base.en)` → `SLM summarize` → `SLM detect_wall`

| Metric | 1.5B | 3B |
|---|---:|---:|
| ASR median | 39 ms | 40 ms |
| SLM summarize median | 157 ms | 250 ms |
| SLM detect-wall median | 287 ms | 366 ms |
| **TOTAL pipeline median** | **484 ms** | **657 ms** |
| Total min / max | 482 / 484 ms | 638 / 662 ms |
| **Margin vs 2 s budget** | **+1,516 ms** | **+1,343 ms** |
| Peak joint RSS (same process) | 1,676 MB | 3,271 MB |
| ASR isolated RSS | 463 MB | 463 MB |
| SLM isolated RSS | 1,330 MB | 2,156 MB |

**Both models clear the 2 s budget with very large margin.** The SLM inference
time (250–366 ms for 3B) is dominant over ASR (39–40 ms) by ~6–9×, confirming
the ASR spike's forecast that "ASR is not the budget bottleneck; the SLM is."

The budget includes only the **pure inference time** (ASR → summarize → detect_wall).
The VAD endpoint wait (the gate's `politeness_gap_seconds ≈ 2.0 s`) runs in
parallel with conversation — the SLM is only called *after* the gap has elapsed
and the speech has settled. The 657 ms joint inference time is therefore
**subtracted from headroom, not from the politeness gap**.

---

### Sustained drift (10 consecutive joint pipeline runs)

| Model | First-5 median | Last-5 median | Drift | pmset thermal |
|---|---:|---:|---:|---|
| 1.5B | 1,013 ms | 1,026 ms | **+1.3%** | No warning |
| 3B | 1,842 ms | 1,777 ms | **−3.5%** | No warning |

> Note: the sustained-check harness used raw-string prompts (not chat template),
> so absolute times are higher than the chat-template joint numbers above. The
> **drift direction/magnitude** is what matters: essentially flat on both. The
> slight 3B decrease (−3.5%) likely reflects MLX's cache warming after the first
> few runs, not a negative trend. No thermal throttling signal.

No thermal warnings recorded (`pmset -g therm`). This is a single-session
sustained check (~10 pipeline cycles), not a multi-hour soak — that is T-504.

---

## Recommendation

### Qwen2.5 size: **3B-Instruct-4bit**

Freeze `mlx-community/Qwen2.5-3B-Instruct-4bit` as the Phase-2 model for both
`summarize()` and `detect_wall()`.

**Why 3B over 1.5B:**
1. **1.5B is non-functional for wall detection.** It produces `is_wall: false`
   for every input tested — including unambiguous `explicit_ask` cases. Wall
   detection is the success-metric-critical path. A model that can't distinguish
   a wall from no-wall is disqualified regardless of latency or memory.
2. **3B clears the joint budget with 1,343 ms to spare** (657 ms total pipeline
   vs 2,000 ms budget). The margin is large enough to absorb prompt-engineering
   overhead, longer real-world transcripts, and minor latency drift.
3. **3B produces qualitatively correct output**: proper JSON structure, correct
   wall category, calibrated confidence (0.8–0.9 on genuine walls), and
   natural-language offers. This is the baseline T-202/T-203 will refine.
4. **Memory is manageable**: 2,156 MB isolated / 3,271 MB joint on a 64 GB
   machine — well within the M5 Pro's unified-memory budget alongside ASR
   (463 MB) and system overhead.

**Why not a larger model (7B):** 7B-Instruct-4bit (~4+ GB) was not benchmarked
in this spike — the 3B joint pipeline already passes with 1.3 s margin, and 3B's
quality issues (one false positive in 4 scenarios) are prompt-engineering problems,
not fundamental model-capability gaps. If T-203 prompt work can't close the
precision gap on 3B, escalating to 7B is the next option (flag to human — it
would likely be within budget but needs measurement).

### ASR: **keep `base.en`**

Do not move ASR to `small.en`. Rationale:
- The joint budget has 1,343 ms margin at 3B. The SLM already dominates at
  ~600 ms; ASR contributes only ~40 ms. `small.en` would reduce ASR by maybe
  10–20 ms while adding memory pressure — not a worthwhile trade.
- `small.en` remains the documented upgrade lever if real-world WER at `base.en`
  climbs on noisy room audio (T-502 Phase 5 re-measure). The joint budget as
  measured at `base.en` + 3B is the right baseline to carry forward.
- The asr-spike's principle holds: "the SLM is the dominant variable; ASR
  should stay small to protect that budget."

### Chat-template discipline (flag for T-202/T-203)

Both Phase-2 backend tasks **must** use `tokenizer.apply_chat_template` with
proper system/user message structure — **not** raw string prompts. Raw prompts
produced repetition/degradation on both model sizes. The chat template roughly
halves latency compared to raw prompts (3B summarize: 1,108 ms raw → 474 ms with
template) because the model is following its training format.

---

## What I could and couldn't measure (honesty box)

- **Measured on this M5:** isolated quality + latency (both models, chat template);
  4-scenario qualitative wall-detection coverage; joint ASR+SLM pipeline latency
  (5 warm runs, both models); sustained drift (10 runs, both models); isolated peak
  RSS; thermal state.
- **Best-case audio only:** synthesized, clean, single-speaker, noise-free. Real
  always-on mic audio will be noisier; WER and SLM quality may differ. Re-measure
  on captured audio in Phase 5 (T-502).
- **Not measured:** true multi-hour always-on thermal/battery behavior (T-504);
  Qwen2.5-7B (not needed — 3B passes; flag to human if 3B prompt work fails);
  `small.en` joint budget (not needed — margin is sufficient at `base.en`);
  prompt variants for T-202/T-203 (that is the T-203 task, not this spike).
- **Nothing was fabricated.** Network was available; both model downloads succeeded;
  all numbers are real measurements on this M5 Pro (64 GB). Harness ran without
  errors.

---

## Handoff

→ **T-202 (local summarizer backend):** implement `SummarizerBackend.summarize`
using `mlx-community/Qwen2.5-3B-Instruct-4bit` via `mlx_lm`. Use chat template
(system/user messages). The frozen seam is `summarize(transcript: str, prev: str)
-> str` in `jarvis/core/living_summary.py`. Load the model once at startup
(not per-call). Budget: ~250 ms per call at warm inference.

→ **T-203 (local wall-detection backend):** implement `WallBackend.detect_wall`
returning the frozen `WallVerdict` from `jarvis.types`. Use the same 3B model
(shared instance with T-202). Chat template with structured-output JSON constraint.
The 3B shows a false-positive tendency on low-ambiguity decisions; the T-203
prompt must tighten precision (stronger "only flag if confident" constraint,
possibly asking the model to reason step-by-step before committing to the JSON).
Budget: ~366 ms per call at warm inference.

→ **mlx-lm** must be promoted from the `slm-spike` uv group into real
`[project.dependencies]` when T-202/T-203 implement the live backends (same
pattern as mlx-whisper at T-104). Record in DECISIONS.md at T-202/T-203 time.

---

## T-509 — Model escalation 3B → 7B (2026-06-16)

**Context:** T-508 was approved on clean single-line probes (e.g.
`"Alice: What's the square root of 81?"` → rating 5). A live `--capture` run
showed **0 Path-B candidates** on clear factual questions in the real pipeline.
Root cause: the "GAP" framing in T-508 caused the 3B model to reason "it is a
direct question from the group, therefore it is not a gap." The 7B model
(WITHOUT the T-509 prompt fix) showed the same regression in the spike below.

**Re-measurement (small.en + 7B, M5 Pro / 64 GB, 2026-06-16):**

| Step | Median | Notes |
|------|--------|-------|
| ASR (small.en) | 103 ms | 5 warm runs; 585 ms on first (cold) |
| 7B summarize | 693 ms | same QwenModel instance |
| 7B detect_wall | 987 ms | 200 max_tokens (CoT + JSON) |
| Joint total | **1791 ms** | median of 5 warm runs |
| Min / Max | 1785 / 1847 ms | stable |
| Margin vs 2000 ms | **+209 ms** | CLEARS — proceed |
| Peak RSS | 5591 MB | vs 3B at 3199 MB |

**Budget verdict: CLEARS.** Margin is +209 ms — tighter than 3B's +1225 ms but
still within budget. 7B is now the default (`DEFAULT_MODEL_PATH` in `qwen.py`).

**7B sample output WITHOUT T-509 prompt fix (from spike):**
```json
{
  "reasoning": "Alice's question is a distraction from the latency issue and does not require an answer from Jarvis to resolve the current conversation.",
  "rating": 2,
  "category": "none",
  "offer": ""
}
```
This confirms the framing regression also affects 7B: the model sees a direct
factual question as "not a gap" under the old T-508 framing. T-509 prompt fix
is mandatory regardless of model size.

**T-509 prompt-fix real-path validation results (6 scenarios, 3 runs, deterministic):**

| Scenario | Expected | Result | Notes |
|----------|----------|--------|-------|
| A: sqrt81 in multi-line context | fire (≥4) | PASS — rating 5 | direct unanswered question |
| B: 4×7 in multi-line context | fire (≥4) | PASS — rating 5 | direct arithmetic question |
| C: wh-form "I wonder what 10×4 is" | fire (≥3) | PASS — rating 5 | wh-form fires correctly |
| D: WDYN after `[Jarvis engaged]` | no-fire | **FAIL — rating 5** | open qa issue (see below) |
| E: self-musing about volume | no-fire | PASS — rating 1 | resolved by multi-line exemplar |
| F: plain statement/plan | no-fire | PASS — rating 1 | stable |

**Open qa issue — Scenario D (WDYN after summon):** The model consistently rates
"What do you need?" (after `[Jarvis engaged]` in the transcript) as an
unanswered_question at rating 5. The exemplar now exactly mirrors this structure
but the 7B model still fires. Root cause analysis: `[Jarvis engaged]` is an
annotation marker, not a speaker turn, and the model treats the subsequent
"What do you need?" as a group-directed question. Two possible fixes outside
local-ml-engineer scope: (1) `core-engineer` could suppress `detect_wall` calls
while Jarvis is in engaged state — the `AttentionLayer` knows when Jarvis is
active; (2) further prompt engineering to treat bracketed system annotations as
conversation-state context. Flagged for qa-tuning evaluation.

**3B selectable:** pass `model_path="mlx-community/Qwen2.5-3B-Instruct-4bit"` to
`QwenModel()`. The 3B has +1225 ms budget margin but the framing regression
affects it equally.
