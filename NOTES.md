# Notes

Informal session-to-session handoff scratchpad. Read this first when starting a session. Overwrite freely — this is not a log, it's a whiteboard.

**What goes here:**
- What was just worked on
- What's half-done and where it was left
- What's next
- Open questions for the human
- Anything the next session needs to know that isn't obvious from the code

**What does _not_ go here:**
- Permanent decisions → `DECISIONS.md`
- Product spec → `docs/reference-guide.md` (or `.pdr.md`)
- Setup instructions → `README.md`
- Structured task state → `TASKS.md`

---

## Current state — 2026-06-15 (T-101 done → Phase 1 ASR runtime selected)

**Phase:** phase_1 — Real ears (kicked off). **T-101 (ASR runtime spike) is DONE** (sensing-engineer, this session).

**Outcome:** **ASR runtime = `mlx-whisper`, model `base.en`** (English-only; `small.en` is the upgrade lever, `whisper.cpp`/`pywhispercpp` is the documented fallback). Benchmarked both candidates on THIS M5 Pro (64 GB) at `base.en` — full method + comparison table + recommendation in **`docs/audio/asr-spike.md`**; two DECISIONS.md entries (runtime choice + spike-dep policy).

**What the spike found:**
- Both runtimes are **~25–125× faster than real time** and **tie on WER** at `base.en` (0.0 % clean short utterance / 1.7 % on a 17 s paragraph — the lone "error" is a "three"/"3" normalization artifact). A realistic ~3.8 s utterance transcribes in **mlx 73 ms / whisper.cpp 52 ms** — negligible vs the **~2 s offer-to-help budget**. ASR is **not** the budget bottleneck; the VAD endpoint wait + the SLM are.
- **Decided on runtime strategy, not speed/accuracy:** mlx-whisper runs on **MLX/Metal/unified-memory — the same stack Qwen2.5 uses (Phase 2)** — so the ambient half standardizes on one accelerator stack to budget. whisper.cpp was marginally faster/leaner (RSS 326 vs 463 MB, no torch dep) → kept as fallback only.
- **No throttling** over a 40× single-session run (this is NOT a multi-hour soak — that's T-504).
- **Honesty caveats:** accuracy measured on clean *synthesized* audio (best case) — re-measure WER on captured noisy audio in Phase 5 (T-502). Streaming/combined-budget not measured here.

**Dependency policy applied:** spike packages (`mlx-whisper`, `pywhispercpp` + transitive torch/mlx) went into an **isolated `asr-spike` uv group** (`uv add --group asr-spike …`), NOT the package's runtime deps. Run the spike with `uv run --group asr-spike …`. **T-104 (`MicSource`) promotes only `mlx-whisper` into real package deps.**

**⚠️ Coexistence flag for local-ml-engineer (joint spike):** this measured ASR in isolation. The real constraint is **ASR + Qwen2.5 running concurrently always-on** on one M5 — measure combined latency / memory / GPU contention / sustained thermal **before either side freezes model sizes**. `base.en` was chosen to leave the SLM the most headroom. (See `asr-spike.md` §coexistence.)

**→ Phase 1 picks up:** **T-102 (always-on mic capture loop)** and **T-103 (Silero VAD gating)**, then **T-104 (`MicSource`)** wiring `mlx-whisper base.en` behind the **frozen `TranscriptSource` seam** — feeding real `Utterance` events (ts stamped from the VAD timeline) and driving the same `TurnTakingGate` with real VAD edges + a real `time.monotonic` clock. The orchestrator + gate don't change for the swap. Did NOT push (working on `main`).

---

## Prior state — 2026-06-16 (T-008 done → PHASE 0 COMPLETE)

**Phase:** phase_0 — Foundations → **COMPLETE.** Phase 1 (Real ears) is next.

**T-008 (AttentionLayer orchestrator + end-to-end MOCK pipeline) is DONE** (this session). With it, **the ambient → summary → wall → dual-summon pipeline runs end-to-end in mock mode** — the last Phase 0 task. Suite **135 green** (14 new), ruff lint+format clean. The runnable demo (`uv run python -m jarvis`) plays the scripted conversation and prints all four behaviors: two living-summary updates (the 2nd on the Tokyo→ramen topic pivot), a Path-B `factual_gap` interjection → engagement, and a Path-A wake-word summon → engagement — no audio, no model, no network.

**What landed (T-008):**
- **`src/jarvis/adapters/`** — the seam package: `transcript_source.py` (`TranscriptSource` Protocol + `ScriptedSource`), `backends.py` (re-exports `SummarizerBackend`/`WallBackend` from their core homes + `HeuristicSummarizerBackend`, parallel to `HeuristicWallBackend`), `engaged.py` (`EngagedResponder`/`VoiceOutput` Protocols + `PrintResponder`/`PrintVoice` stand-ins).
- **`src/jarvis/attention_layer.py`** — the `AttentionLayer` orchestrator. `ingest(u)` runs the module-map event flow; emits `on_summary_update` / `on_interjection` / `on_engagement`. **It owns handoff assembly** (decision/handoff boundary, T-007): on a `SummonController` decision (either path) it builds the `EngagementHandoff` (`handoff_reason()` + the summary + recent excerpt it owns) and dispatches it through the responder + voice seams. `build(...)` and `run_scripted(...)` classmethods do the common wiring.
- **`ScriptedSource` drives the shared gate + injected clock** so the politeness gap elapses deterministically (DECISIONS.md 2026-06-16): each `ScriptedLine(speaker, text, gap)` carries the silence after it; the source fires the gate's `on_speech_start`/`on_speech_end` edges (the VAD edges `MicSource` will emit) and advances the clock by `gap`. No `time.sleep`, no internal clock — the whole run is deterministic.
- **`src/jarvis/clock.py` (`ManualClock`)** — the deterministic injected clock moved into the package so the demo doesn't import from `tests/`; `tests/clock.py` now re-exports it as `SimulatedClock` (one implementation, harness name unchanged — every existing core-module test still imports `SimulatedClock` from `tests.clock`).
- **`src/jarvis/demo.py` + `__main__.py`** — the runnable entry point (`python -m jarvis`).
- **`tests/test_attention_layer.py`** — 14 acceptance tests on `ScriptedSource` + `FakeResponder`/`FakeVoice` + `SimulatedClock`: the headline all-three-behaviors run, Path A (immediate, builds handoff, ignores gate), Path B (fires after the gap, holds when speech resumes too soon, backs off the repeat offer, drops below the floor), summary delta-update via the injected backend, cold-start silence, determinism, and the source-drives-clock/gate unit check. All assert emitted events / seam calls, no private fields.

**Phase 0 modules: ALL COMPLETE** (T-001…T-010). The six deep core modules + orchestrator + `TranscriptSource` seam are built, unit-tested, and run end-to-end mock-green. module-map.md is current (orchestrator + `TranscriptSource` seam frozen; Phase 0 modules marked complete).

**→ Phase 1 (Real ears) picks up:** the ASR/runtime spike (T-101, mlx-whisper vs whisper.cpp on the M5) and the always-on mic + Silero VAD path (T-102/T-103), then **`MicSource` (T-104) replacing `ScriptedSource`** behind the **frozen `TranscriptSource` seam** — feeding real `Utterance` events and driving the same `TurnTakingGate` with real VAD `on_speech_start`/`on_speech_end` edges + a real `time.monotonic` clock. The orchestrator and gate do not change for the swap. (sensing-engineer owns this; `docs/audio/asr-spike.md` is their first deliverable.) Note for Phase 1: the cheap `_has_wall_signal` pre-filter and the wake-word match are regex over text in `attention_layer.py` — fine for v0, revisit if ASR casing/punctuation differs from the scripted text.

**Open for the human:** API keys (Anthropic, ElevenLabs) still only needed once Phase 4 (engaged path) begins — mock mode covered Phases 0. `Start_Here/` is still an untracked nested git repo (untouched).

---

## Prior state — 2026-06-16 (T-007 SummonController built → in `review`, awaiting qa-tuning)

**Phase:** phase_0 — Foundations.

**T-007 (SummonController) is built and in `review`** (core-engineer, this session) — **awaiting mandatory qa-tuning review** before merge (it carries the success metric). Suite **121 green**, ruff lint+format clean.

- **`SummonController`** (`src/jarvis/core/summon_controller.py`): the asymmetric dual-path machine. **Path A** `on_summon(detail="") -> SummonDecision(SUMMON)` — immediate, unconditional, ignores gate/wall/floor/back-off. **Path B** `consider_interjection(verdict) -> SummonDecision | None` — fires only when ALL hold: `is_wall ∧ confidence ≥ floor ∧ ¬gate.speech_resumed() ∧ gate.politeness_gap_elapsed() ∧ not-already-offered`. Holds an **injected `TurnTakingGate`** and reads **no clock of its own** (timing comes through the gate's pure predicates). **abort-on-resume is checked before the gap** so a latched resume suppresses even a stale-elapsed gap. **Back-off** de-dupes by `category::offer` signature (confidence excluded); only an actual fire arms it.
- **Threshold:** `interjection_confidence_floor=0.70` (default; matches the prototype's `WALL_CONFIDENCE_TO_SPEAK`), constructor-injected + guarded to `[0,1]`, inclusive cut (`>=`). It lives in SummonController, NOT the detector (the detector surfaces confidence raw). This is the one knob Phase-5 (T-503) sweeps.
- **Decision/handoff boundary (structural call, DECISIONS.md):** SummonController is a **pure decision machine** — it emits a `SummonDecision`, it does NOT build the `EngagementHandoff` (it holds neither the summary nor the window). **The orchestrator (T-008) assembles the handoff** from the decision + its summary/excerpt; `SummonDecision.handoff_reason()` gives the `"summon"`/`"wall:<category>"` wire string for free.
- **New frozen types** in `src/jarvis/types.py` (T-007): `TriggerReason` (StrEnum: `summon`/`interjection`), `Interjection` (`category: WallCategory`, `offer`, `confidence`), `SummonDecision` (`reason`, `interjection | None`, `detail`, `.handoff_reason()`). `EngagementHandoff`'s shape is frozen here too; it is *built* at T-008.
- **Tests:** `tests/test_summon_controller.py` (24 tests on the `SimulatedClock` + the real injected gate + `wall()`/`no_wall()` fakes): Path A immediacy (engages with no wall + gap not elapsed, ignores resume/back-off), Path B all-conditions gating (drop-if-any-one-fails), abort-on-resume (incl. the stale-gap precedence + re-arm-after-fresh-silence), back-off (same-signature, per-signature, twice-in-a-row, confidence-excluded, dropped-wall-doesn't-arm), confidence-floor boundary (inclusive, just-below, configurable, range guards).

**→ After T-007 passes review, the last two Phase 0 tasks are: T-008 (orchestrator + end-to-end MOCK run, deps T-002..T-007) and T-010 (interjection-precision eval, qa-tuning, deps T-007).** T-008 wires the modules + `ScriptedSource` + fakes, assembles the `EngagementHandoff` from the `SummonDecision`, and is where the `adapters/` package likely lands.

---

## Prior state — 2026-06-16 (T-005 + T-006 APPROVED by qa-tuning → T-007 unblocked)

**Phase:** phase_0 — Foundations.

**T-005 (WallDetector) and T-006 (TurnTakingGate) are DONE** — qa-tuning reviewed both (mandatory triggers) and **approved both**; moved `review → done`. Suite **97 green**, ruff clean. Full review verdict + non-blocking coverage notes + what-T-010-must-measure are in `docs/qa/working-notes.md` (and `eval-plan.md`).

**→ T-007 (SummonController) is now UNBLOCKED** — both of its deps (T-005, T-006) are done. It consumes `WallDetector.detect(...).confidence` (applies `WALL_CONFIDENCE_TO_SPEAK=0.70` here, NOT in the detector) and the gate's three predicates (Path-B gating on `politeness_gap_elapsed`, abort on `speech_resumed`, plus back-off). T-007 also carries a mandatory qa-tuning review before merge.

**Review highlights (qa-tuning):** WallVerdict schema is sound + complete for downstream and the real-backend contract (module-map §"Contract for the real backend (T-203)") is unambiguous for local-ml-engineer; speak-threshold correctly kept out of the detector; gate abort-on-resume verified (a resume during the gap re-arms and restarts the clock — a stale gap can never fire); single clock source; thresholds injected + guarded. No defects found — review was behavioral-soundness + testability only (the numeric precision eval can't exist until T-008 + T-010).

_(Prior in-review note for T-005/T-006 superseded by the above.)_

- **T-005 — `WallVerdict` FROZEN** (`src/jarvis/types.py`): `is_wall: bool`, `category: WallCategory` (StrEnum: `unanswered_question | factual_gap | stuck_point | explicit_ask | none`), `confidence: float` [0,1], `offer: str`; `WallVerdict.none()` for the non-wall case. **`WallDetector`** (`core/wall_detector.py`) is a thin sensor over the swappable `WallBackend` Protocol seam; **`HeuristicWallBackend`** is the Phase-0 backend. **The detector applies NO confidence threshold** — the speak gate (`WALL_CONFIDENCE_TO_SPEAK`) is SummonController policy (T-007). The T-009 `WallVerdictLike` TODO in `tests/fakes.py` is **resolved** (now returns the real `WallVerdict`; `wall()`/`no_wall()` build real verdicts). **Real-backend contract for local-ml-engineer (T-203)** is written in `module-map.md` §"Contract for the real backend" — they implement `WallBackend.detect_wall` to this exact frozen shape.
- **T-006 — TurnTakingGate event-input API DESIGNED** (the gap qa-tuning flagged): `on_speech_start()` / `on_speech_end()` edge events; events carry no `ts` (gate stamps from injected `now()`); silence measured from the most recent `on_speech_end()`; `speech_resumed()` latches on a gap-interrupting resume, clears on next `on_speech_end()`; the 3 predicates are pure reads. Asymmetric thresholds `settle_seconds=0.6` (Path A) / `politeness_gap_seconds=2.0` (Path B) are constructor-injected. Decision logged in DECISIONS.md.
- **T-007 (SummonController) unblocks once T-005 + T-006 pass qa-tuning review.** It consumes `WallDetector.detect(...).confidence` (applies `WALL_CONFIDENCE_TO_SPEAK` here) and the gate's three predicates (Path B gating + abort-on-resume + back-off).

---

## Prior state — 2026-06-16 (T-002 + T-003 + T-004 done)

**Phase:** phase_0 — Foundations.

**T-004 (LivingSummary delta-update) is DONE** (core-engineer, this session).
- **`LivingSummary`** (`src/jarvis/core/living_summary.py`): holds an injected `TopicShiftDetector` + tracks the summary's basis keyword set; `consider_update(window) -> bool` re-summarizes **only** on a detected shift via the injected `SummarizerBackend`. `text` exposes the current summary.
- **`SummarizerBackend` seam FROZEN** — `summarize(transcript: str, prev: str) -> str`, a `typing.Protocol` in `living_summary.py`. **Reconciled with `FakeSummarizer`: identical, no disagreement.** The real Qwen2.5/MLX backend (T-202) drops in behind it untouched. (Not yet hoisted to a shared `adapters/backends.py` — that consolidates at T-008.)
- **Two policy fences live in LivingSummary, not TopicShiftDetector** (the deliberate scope split): `MIN_UTTERANCES_FOR_SUMMARY=3` (cold start) + `MIN_UTTERANCES_SINCE_UPDATE=2` (debounce). First summary fires when cold-start clears; after that, only on a shift past the debounce.
- **Window-sizing gotcha for T-008:** a shift only registers once the *old* topic ages out of the `RollingWindow` (by count/time) — a wide window holding both topics keeps basis/current overlap above threshold. Correct "actually moved on" behavior; size the orchestrator's window for it. (The T-004 shift tests use a tight window to model the pivot.)
- Tests on the T-009 harness (`SimulatedClock` + `RollingWindow` + `FakeSummarizer`). Suite **60 green**, ruff lint+format clean. Commits `[T-004]` claim + feat on `main`.

### What's left in Phase 0 (after T-004)
Five core tasks remain before the phase closes:
- **T-005 (WallDetector)** — **mandatory qa-tuning review.** Open design item: freeze `WallVerdict` **with local-ml-engineer** first (harness uses `WallVerdictLike` until then; field names already match, swap is import-only).
- **T-006 (TurnTakingGate)** — **mandatory qa-tuning review.** Open design item: the gate's event-*input* API is still undesigned (design it **with qa-tuning**); the clock side is settled (`now: Callable[[], float]`).
- **T-007 (SummonController)** — **mandatory qa-tuning review.** Depends on T-005 + T-006.
- **T-008 (AttentionLayer orchestrator + end-to-end MOCK run)** — depends on T-002..T-007. Where the `SummarizerBackend`/`WallBackend` seams + `ScriptedSource` + fakes get wired and the `adapters/` package likely lands.
- **T-010 (interjection-precision eval)** — qa-tuning, depends on T-007.

All three of T-005/T-006/T-007 carry mandatory qa-tuning review (they are the success-metric-critical timing/precision logic).

---

## Prior state — 2026-06-15 (T-002 + T-003 done)

**Phase:** phase_0 — Foundations.

**T-002 (data types + RollingWindow) and T-003 (TopicShiftDetector) are DONE** (core-engineer, prior session).
- **Clock convention pinned:** `now: Callable[[], float]` is the single clock-injection form for every time-bounded module (module-map.md §"Cross-cutting design constraints" #1) — closes T-009 interface gap #1. Not a `Clock` object.
- **`Utterance` is FROZEN** (`src/jarvis/types.py`): `speaker`, `text`, `ts`; `ts` required and producer-supplied (no hidden `time.monotonic` default). sensing-engineer's `MicSource` must stamp `ts` from the VAD timeline.
- **`RollingWindow`** (`src/jarvis/core/rolling_window.py`): bounded by count AND elapsed time, injected `now`, evicts on add *and* on read so the window ages during silence (divergence from the prototype's internal clock + newest-ts eviction).
- **Shared text helpers** (`src/jarvis/core/text.py`): `keywords()`/`jaccard()` ported from the prototype, reused by RollingWindow and TopicShiftDetector.
- **`TopicShiftDetector`** (`src/jarvis/core/topic_shift.py`): pure decision, `shifted()` = Jaccard < `threshold` (default 0.30, constructor-injected). Cold-start minimum / debounce deliberately deferred to T-004's `LivingSummary` (scope fence in module-map.md).
- Tests use the T-009 harness (`SimulatedClock`). Suite **48 green**, ruff lint+format clean. Commits: `[T-002]`/`[T-003]` claim + feat on `main`. (T-004 has since landed — see current state above for what's left in Phase 0.)

---

## Prior state — 2026-06-15 (T-001 scaffold done)

**Phase:** phase_0 — Foundations.

**T-001 (Python project scaffold) is DONE.** The real `jarvis` package now exists:
- **src-layout** `src/jarvis/` + `pyproject.toml` (`requires-python = ">=3.11"`, hatchling).
- **Toolchain: uv** — the machine had no 3.11 (system python is 3.9.6), so uv 0.11.21 was installed via its non-interactive standalone installer to `~/.local/bin` and pins **CPython 3.11.15**. `uv.lock` + `.python-version` are committed.
- **pytest** wired (2 smoke tests pass), **ruff** lint+format clean. `prototypes/` is excluded from ruff (it's reference, not the package).
- First deliverable shipped: **`docs/architecture/module-map.md`** — the seam contract (six core modules + the TranscriptSource / EngagedResponder / VoiceOutput adapter seams) the other agents implement against.
- Decision logged in `DECISIONS.md` (uv + src-layout + pytest + ruff).

**Running the toolchain:** `uv` lives at `~/.local/bin/uv` — either add it to PATH or `export PATH="$HOME/.local/bin:$PATH"`. Then `uv run pytest`, `uv run ruff check .`, `uv run ruff format .`.

The reference prototype at `prototypes/attention-layer/` still runs end-to-end in mock mode and validates the module shapes. It is **reference, not the package** — its logic is ported into `src/jarvis/` deliberately in T-002+ (e.g. RollingWindow must take an injected clock, unlike the prototype's internal `time.monotonic()`).

## Also done — 2026-06-15 (T-009 test harness)

**T-009 (qa-tuning's simulated-clock + fakes harness) is DONE.** The shared test
scaffolding the core-module tests build on now exists:
- `tests/clock.py` — `SimulatedClock` (inject `clock.now`, drive time with
  `clock.advance(s)`; monotonic-by-construction). No real `sleep`.
- `tests/fakes.py` — `FakeSummarizer`, `FakeWallBackend`, `FakeResponder`,
  `FakeVoice` (each presets returns + records calls), plus a `WallVerdictLike`
  stand-in and `wall()`/`no_wall()` helpers.
- `tests/conftest.py` — fixtures (`clock`, `fake_summarizer`, …).
- `tests/test_harness.py` — 22 self-tests. Suite green (24 total), ruff clean.
- Conventions written to `docs/qa/eval-plan.md` (qa-tuning's first deliverable);
  the T-010 interjection-precision eval spec is stubbed there, written next.

**Two interface gaps for core-engineer to close while building the modules:**
1. **T-006 (TurnTakingGate):** the module map freezes the 3 output predicates
   but not the gate's event-*input* API, and mentions both a `now=` callable and
   a `Clock` object without picking one. The harness clock supports both forms —
   pin one in T-006. (Recommend the `now: Callable[[], float]` form the rest of
   the module map already uses.)
2. **T-005 (WallVerdict):** not frozen yet; harness uses `WallVerdictLike` with
   matching field names (TODO marker in `tests/fakes.py`). Freeze the real type
   *with* local-ml-engineer; the swap is then import-only.

### What's next

- **T-002 (Core data types + RollingWindow) is the next unblocked task** (depends only on T-001). Freeze `Utterance` there; inject the clock into RollingWindow's time-bound (use the T-009 `SimulatedClock` — pass `clock.now`). See the `## Next` section of the module map and `docs/architecture/working-notes.md` for the T-002 prep notes.
- T-003 and T-005 are also unblocked (depend only on T-001). **T-009 harness is now landed** — core-module tests (T-002…T-008) should build on `tests/clock.py` + `tests/fakes.py`, not reinvent.
- Two runtimes are deferred to spikes: ASR (mlx-whisper vs whisper.cpp) in Phase 1, Qwen2.5 size in Phase 2.

### Open questions for the human

- API keys for live mode (Anthropic, ElevenLabs) are not set yet — only needed once Phase 4 (the engaged path) begins; mock mode covers Phases 0–3.
- `Start_Here/` is still an untracked nested git repo (the bootstrap kit). Decide whether to submodule it or leave it out of version control. (Untouched this session.)
