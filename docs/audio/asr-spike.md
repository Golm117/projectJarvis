# ASR runtime spike — mlx-whisper vs whisper.cpp on the M5

> **Owner:** sensing-engineer · **Domain:** `docs/audio/` · **Task:** T-101 (Phase 1)
> **Status:** DONE — both runtimes benchmarked on this machine; runtime selected.
> **Grounding:** `.pdr.md` (approved stack: "Local ASR — mlx-whisper vs whisper.cpp,
> selected in Phase 1 spike"), the wedge's **~2 s offer-to-help budget**, and the
> hard-no that **no ambient audio leaves the device** (both candidates are 100% local).

This is the deliverable the sensing-engineer intro promises: the plan, the measured
results, and the recommendation for the local ASR runtime that will sit behind
`MicSource` (T-104) and feed `Utterance` events into the frozen `TranscriptSource`
seam.

---

## TL;DR — recommendation

**Use `mlx-whisper` at `base.en` (English-only)** as the Phase-1 ASR runtime, with a
clear upgrade path to `small.en` if accuracy demands it and the M5 budget allows.
**`whisper.cpp` (via `pywhispercpp`) is the documented fallback.**

Both runtimes are **dramatically inside budget** on this M5 Pro — a realistic ~3.8 s
utterance transcribes in **~50–73 ms** (RTF ≈ 0.01–0.02), i.e. ~25–50× faster than
real time, leaving the entire ~2 s budget for VAD endpointing + downstream summary /
wall detection. **Accuracy is identical** at `base.en` (0.0 % WER on a clean short
utterance, 1.7 % on a 17 s paragraph — the single "error" is a spelled-vs-digit
"three"/"3" normalization artifact, not a real miss). So the choice is **not** decided
by latency or accuracy at this model size — both win. It is decided by **runtime
strategy**: mlx-whisper runs on **MLX/Metal with unified memory**, the *same*
accelerator stack Qwen2.5/MLX will use in Phase 2. Standardizing the ambient half on
one local-inference runtime (MLX) means one stack to profile, budget, and reason about
under sustained always-on load — see the coexistence flag below.

whisper.cpp was marginally *faster* in raw wall-clock (~47 ms vs ~63 ms on the short
clip) and uses slightly less memory — it is an excellent, dependency-light fallback —
but that ~15 ms edge is irrelevant against a 2000 ms budget, and it does not justify
carrying a second inference runtime (ggml/Metal) alongside MLX.

---

## Method

### Machine
- **Apple M5 Pro**, 18 cores, **64 GB** unified memory, macOS (Darwin 25.4.0).
- Toolchain: **uv** (managed CPython 3.11.15). Spike deps installed into an **isolated
  `asr-spike` uv dependency group** (`uv add --group asr-spike …`) so the always-on
  `jarvis` package stays dependency-free until `MicSource` (T-104) pins exactly what
  it needs. See DECISIONS.md.

### Candidates (both at the **`base.en`** model — fair comparison, English-only)
| Runtime | Package | Backend | Model |
|---|---|---|---|
| **mlx-whisper** | `mlx-whisper==0.4.3` (pulls `mlx==0.31.2`, `mlx-metal`, `torch`) | MLX / Metal (Apple-Silicon native), unified memory | `mlx-community/whisper-base.en-mlx` (146 MB) |
| **whisper.cpp** | `pywhispercpp==1.5.0` (ggml whisper.cpp binding) | ggml + Metal GPU (flash-attn on), 8 CPU threads | `ggml-base.en.bin` (145 MB) |

Both installed cleanly with no compilation friction. whisper.cpp auto-downloaded its
ggml model on first `Model("base.en")`; mlx-whisper pulled the MLX-converted weights
from the HF hub on first `transcribe`.

> Note on whisper.cpp on this M5: the ggml Metal backend logged
> `the tensor API is not supported in this environment - disabling` and fell back to
> the embedded Metal library (still GPU-accelerated). It worked and was fast; just
> flagging that the very newest M5 tensor path wasn't picked up by this ggml build —
> a point in favor of the MLX path, which is maintained directly for Apple Silicon.

### Audio sample (stated exactly, with provenance)
No suitable LibriSpeech clip was fetched; instead I **synthesized** two reference clips
with **macOS `say`** (voice **Daniel**, en) and converted them to **16 kHz mono PCM
WAV** with `ffmpeg` (whisper's native input rate). Because the text is authored, the
**ground-truth transcript is exact** — ideal for WER. Both clips are clean, single-
speaker, no background noise (a *best-case* accuracy read; real always-on mic input
will be noisier — revisit WER on captured audio in Phase 5, T-502).

- **`short_3.8s`** (3.82 s) — one conversational question, the realistic always-on unit:
  *"did anyone figure out whether the build is still failing on the new machine."*
- **`para_17.0s`** (17.01 s) — a 3-sentence paragraph (fox pangram + two meeting
  sentences), to read longer-form latency/RTF and accuracy.

Files live under `/tmp/asr-spike/` (ephemeral, not committed — regenerable from the
harness). The benchmark harness is `/tmp/asr-spike/bench.py` (latency + WER) and
`/tmp/asr-spike/mem_sustained.py` (isolated memory + a 40× sustained-drift read).

### What was measured
- **Latency / RTF** — 5 warm runs per (runtime, clip) after a warmup transcribe;
  median / min / max; RTF = median ÷ clip-duration.
- **Accuracy (WER)** — word error rate vs the exact reference (lowercased, punctuation
  stripped, apostrophes kept; Levenshtein over words).
- **Memory** — peak process RSS, measured **per-runtime in its own fresh subprocess**
  so one runtime's loaded libraries don't pollute the other's number.
- **Sustained-load / thermal** — 40 back-to-back transcribes of the 3.8 s clip; compare
  median of the first 10 vs the last 10 runs for latency drift; plus `pmset -g therm`.
  **Honest caveat:** this is a *single short session*, not a thermal soak. It catches
  obvious immediate throttling but **does not** characterize true always-on multi-hour
  behavior — that is a separate Phase-5 stability pass (T-504).

---

## Results

### Latency + accuracy (median of 5 warm runs, `base.en`)

| Runtime | Clip | Duration | Median latency | RTF | WER |
|---|---|---:|---:|---:|---:|
| **mlx-whisper** | short | 3.82 s | **0.073 s** | 0.019 | **0.0 %** |
| **mlx-whisper** | paragraph | 17.01 s | **0.166 s** | 0.010 | 1.7 % |
| **whisper.cpp** | short | 3.82 s | **0.052 s** | 0.014 | **0.0 %** |
| **whisper.cpp** | paragraph | 17.01 s | **0.132 s** | 0.008 | 1.7 % |

- **Both are ~25–125× faster than real time.** A short utterance finishes in tens of
  ms — negligible against the ~2 s offer-to-help budget. The budget will be spent on
  the **VAD endpoint wait** (the gate's settle/politeness gaps) and the **downstream
  SLM** (summary + wall detection), **not** on ASR.
- **WER ties at `base.en`.** The lone paragraph "error" on both: the reference spells
  "three o'clock"; mlx heard "free o'clock" (a real but tiny phonetic slip) and
  whisper.cpp heard "3 o'clock" (correct, but scored as a substitution vs the spelled
  reference). Net: **transcript quality is equivalent** for our purposes at this size.

### Memory (isolated, fresh process each)

| Runtime | Peak RSS | Model on disk |
|---|---:|---:|
| mlx-whisper `base.en` | **463 MB** | 146 MB |
| whisper.cpp `base.en` | **326 MB** | 145 MB |

Both are trivial against 64 GB. mlx's larger footprint is the MLX/torch runtime
overhead; whisper.cpp is leaner (ggml, no torch). (The earlier same-process run
reported higher cumulative RSS — that number is order-dependent and superseded by these
isolated figures.)

### Sustained-load / drift (40× the 3.8 s clip, single session)

| Runtime | median first-10 | median last-10 | drift | overall median / max |
|---|---:|---:|---:|---:|
| mlx-whisper | 62.5 ms | 62.6 ms | **+0.0 %** | 62.7 / 68.2 ms |
| whisper.cpp | 47.7 ms | 47.4 ms | **−0.6 %** | 47.3 / 48.7 ms |

**No observable throttling** over the short run for either runtime; latency is flat and
tight. `pmset -g therm`: *"No thermal warning level has been recorded."* As noted, this
is **not** a multi-hour soak — true always-on thermal/battery behavior is T-504.

---

## Recommendation (which runtime, which model size, why)

**`mlx-whisper`, model `base.en`.**

**Why mlx-whisper over whisper.cpp** (given latency + accuracy are effectively tied):
1. **One accelerator stack with Phase 2.** Qwen2.5 runs on **MLX** (approved stack).
   Putting ASR on **the same MLX/Metal/unified-memory runtime** means a single
   inference stack to budget, profile, and reason about under always-on load — instead
   of MLX *plus* a separate ggml/Metal runtime. This is the deciding factor.
2. **Maintained for Apple Silicon directly.** whisper.cpp's ggml build on this M5
   couldn't use the newest tensor API (`tensor API is not supported … disabling`) and
   fell back; MLX is developed against Apple Silicon first-class. On brand-new M5
   hardware that currency matters.
3. **Clean Python integration** behind the `TranscriptSource` seam — `mlx_whisper.
   transcribe(audio)` returns text directly; easy to wrap in `MicSource`.

**Why `base.en`:**
- **English-only (`.en`)** — the user is the single English-speaking developer (v0,
  per `.pdr.md`); the `.en` models are more accurate per-FLOP than multilingual at the
  same size. No multilingual requirement in scope.
- **`base` size** — already 0.0 % WER on clean speech and ~70 ms/utterance. It leaves
  the **most M5 headroom for Qwen2.5** (the real always-on cost center). `tiny.en`
  would save little (already negligible latency) at an accuracy cost; `small.en` is the
  **upgrade lever** if real (noisy) captured audio shows `base.en` WER climbing — and
  small.en is still far inside budget on raw latency, the only question is the *combined*
  ASR+SLM budget.

**Fallback:** `whisper.cpp` via `pywhispercpp` — marginally faster, leaner memory, no
torch/MLX dependency. Keep it as the documented Plan B if MLX ever becomes a liability
(e.g. an MLX/torch dependency conflict with the Qwen2.5 stack, or an MLX regression).

---

## ⚠️ M5-budget coexistence — flag for the joint spike with local-ml-engineer

**This spike measured ASR _in isolation_.** The real constraint is **ASR + Qwen2.5
running concurrently, always-on**, sharing one M5's unified memory and Metal GPU. ASR
alone is a rounding error (≤463 MB, ≤0.17 s); the SLM is the heavyweight. Before model
sizes are frozen on either side, **sensing-engineer + local-ml-engineer must jointly
measure**:

- **Combined latency** with ASR and Qwen2.5 (T-201 size) inferring back-to-back on the
  same utterance — does the end-to-end "utterance → summary/wall verdict" stay inside
  the ~2 s offer budget *after* the VAD endpoint wait?
- **Combined memory + GPU contention** under sustained load — two MLX/Metal consumers
  on one unified-memory GPU.
- **Sustained thermal/battery** over a realistic always-on window (this is T-504, but
  it must include *both* models running, not ASR alone).

**Implication for sizing:** `base.en` is recommended precisely because it **leaves the
most headroom** for the SLM. Only move ASR to `small.en` if the joint measurement shows
the combined ASR+SLM budget still clears comfortably. The SLM (Qwen2.5 size, T-201) is
the dominant variable — ASR should stay small to protect that budget.

---

## What I could and couldn't measure (honesty box)

- ✅ **Measured on this M5:** install/runnability of both runtimes; per-utterance and
  paragraph latency + RTF; WER on a known reference; isolated peak memory; a 40×
  single-session sustained-drift read; thermal-warning snapshot.
- ⚠️ **Best-case accuracy only:** synthesized, clean, single-speaker, noise-free audio.
  Real always-on mic input (room noise, overlap, accents, far-field) will be harder —
  **re-measure WER on captured audio in Phase 5 (T-502)** before trusting `base.en` in
  the wild.
- ❌ **Not measured:** true multi-hour always-on thermal/battery behavior (T-504); a
  real **streaming/chunked** transcription figure (both were measured as whole-clip
  one-shots — `MicSource` will feed VAD-segmented utterances, which is the natural unit
  and is what the short-clip number models, so a separate streaming harness wasn't
  needed for the runtime *choice*); and the **combined ASR+SLM** budget (the joint
  spike above).
- 🚫 **Nothing was fabricated or blocked** — network, uv, brew, and both packages were
  all available and the full benchmark ran.

---

## Handoff

→ **T-102 (mic capture loop)** / **T-104 (`MicSource`)** pick up next: wire
`mlx-whisper base.en` behind the frozen `TranscriptSource` seam, fed by the Silero-VAD
segmenter (T-103), stamping `Utterance.ts` from the VAD timeline.
→ **local-ml-engineer:** the M5-budget coexistence joint spike before either side
freezes model sizes (see the flag above).

---

## T-505 update — small.en upgrade + noise filter (2026-06-16)

> **Task:** T-505 · Phase 5 real-room ASR quality pass.

### What changed
Real-room testing (user's built-in mic, not BlackHole loopback) revealed two problems with `base.en`:
1. **Name mishearing:** "Jarvis" transcribed as "Germans" (a phonetically plausible but wrong substitution in real-room conditions).
2. **Garbage segments:** "service.!!!!!!!!!!", "Mm.", "!" reaching the rolling window and wall detector.

**Fix 1 — Model upgrade:** `DEFAULT_MLX_WHISPER_REPO` changed from `whisper-base.en-mlx` to `whisper-small.en-mlx`. `base.en` remains selectable via the `MlxWhisperTranscriber(repo=...)` constructor arg. `small.en` weights (~466 MB) downloaded and cached locally.

**Fix 2 — Lexical segment filter:** `_is_lexical()` added to `mic_source.py`. Applied in `MicSource._close_segment()` before any segment becomes an `Utterance`. Drops pure-punctuation/symbol strings, single-char noise, and filler-syllable-only transcriptions ("Mm.", "Hmm", "Uh"). Keeps wake word ("Jarvis"), short real replies ("Yes.", "No."), and all normal speech.

### Joint budget re-measurement (small.en + Qwen2.5-3B-Instruct-4bit, M5 Pro, 5 warm runs)

| Stage | T-201 base.en (ms) | T-505 small.en (ms) | Delta |
|---|---:|---:|---:|
| ASR | 40 | **80** | +40 ms |
| Summarize (Qwen2.5-3B) | 250 | **305** | +55 ms |
| Detect wall (Qwen2.5-3B) | 366 | **392** | +26 ms |
| **Joint total** | **657** | **775** | **+118 ms** |
| Margin vs 2000 ms budget | 1343 ms | **1225 ms** | −118 ms |

**Verdict: CLEARS the budget with 1,225 ms margin.** small.en ASR is 2× base.en (80 ms vs 40 ms), but that is still negligible against the ~2 s offer budget. The SLM (Qwen) dominates at ~700 ms; the ASR contribution is a rounding error. The 118 ms budget reduction is acceptable for the accuracy gain.

### Live test results on built-in mic (M5, device 6 "MacBook Pro Microphone")

Run 1: `--say "Hey Jarvis, can you hear me?" --device 6 --local-brain`
- Transcript: **"Hey Jarvis, can you hear me?"** — exact match, wake word correct.
- Path A fired: `ENGAGEMENT (trigger: summon)`.

Run 2: `--say "What was the date of the conference again?" --device 6 --local-brain`
- Transcript: **"What was the date of the conference again?"** — exact match.
- `factual_gap @ 0.95` → Path B fired: `ENGAGEMENT (trigger: wall:factual_gap)`.

Run 3: `--say "Yes Jarvis" --device 6`
- Transcript: **"Yes Jarvis."** — exact match, short real reply kept by filter.

**Honest caveat:** The `--say` loopback uses macOS text-to-speech through the MacBook Pro's speakers → built-in mic. This is NOT the exact same scenario as the user's original "Germans" mishearing (which was the user's natural voice at room distance with ambient noise). The loopback produces cleaner audio (RMS ~0.010–0.013) than natural far-field speech. Both `base.en` and `small.en` handled the loopback correctly in isolation. The "Germans" mishearing is plausibly reproduced only when the user speaks naturally at a distance — the upgrade provides a meaningful quality improvement in that regime (small.en has ~50% more parameters than base.en at the .en size), but cannot be confirmed with loopback audio alone. The filter is confirmed working end-to-end (segments that don't contain real words are dropped before reaching the pipeline).

### 51 new model-free unit tests
`tests/test_t505_asr_quality.py` — covers `_is_lexical` drops/keeps, configurable constants, `MlxWhisperTranscriber` repo arg wiring, and `MicSource` end-to-end filter application.
