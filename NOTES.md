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

## Current state — 2026-06-16 (T-005 + T-006 in review)

**Phase:** phase_0 — Foundations.

**T-005 (WallDetector) and T-006 (TurnTakingGate) are in `review`** (core-engineer, this session) — both are mandatory qa-tuning-review triggers; **NOT merged until qa-tuning passes them.** Suite **97 green**, ruff clean. Commits `[T-005]`/`[T-006]` claim + feat on `main` (not pushed).

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
