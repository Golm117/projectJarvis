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

## Current state — 2026-06-15 (T-002 + T-003 done)

**Phase:** phase_0 — Foundations.

**T-002 (data types + RollingWindow) and T-003 (TopicShiftDetector) are DONE** (core-engineer, this session).
- **Clock convention pinned:** `now: Callable[[], float]` is the single clock-injection form for every time-bounded module (module-map.md §"Cross-cutting design constraints" #1) — closes T-009 interface gap #1. Not a `Clock` object.
- **`Utterance` is FROZEN** (`src/jarvis/types.py`): `speaker`, `text`, `ts`; `ts` required and producer-supplied (no hidden `time.monotonic` default). sensing-engineer's `MicSource` must stamp `ts` from the VAD timeline.
- **`RollingWindow`** (`src/jarvis/core/rolling_window.py`): bounded by count AND elapsed time, injected `now`, evicts on add *and* on read so the window ages during silence (divergence from the prototype's internal clock + newest-ts eviction).
- **Shared text helpers** (`src/jarvis/core/text.py`): `keywords()`/`jaccard()` ported from the prototype, reused by RollingWindow and TopicShiftDetector.
- **`TopicShiftDetector`** (`src/jarvis/core/topic_shift.py`): pure decision, `shifted()` = Jaccard < `threshold` (default 0.30, constructor-injected). Cold-start minimum / debounce deliberately deferred to T-004's `LivingSummary` (scope fence in module-map.md).
- Tests use the T-009 harness (`SimulatedClock`). Suite **48 green**, ruff lint+format clean. Commits: `[T-002]`/`[T-003]` claim + feat on `main`.

### Next unblocked tasks
- **T-004 (LivingSummary delta-update) is now UNBLOCKED** (depended on T-002 + T-003, both done). Wire: hold a `TopicShiftDetector`, inject a `SummarizerBackend` (use `FakeSummarizer`), apply `MIN_UTTERANCES_FOR_SUMMARY` + first-time rule around `shifted(window.keywords(), basis)`. Not a mandatory-review trigger.
- **T-005 (WallDetector)** — open, depends only on T-001. **Mandatory qa-tuning review** (detector + thresholds). Freeze `WallVerdict` *with* local-ml-engineer first (harness uses `WallVerdictLike` until then).
- **T-006 (TurnTakingGate)** — open, depends only on T-001. **Mandatory qa-tuning review.** Clock side now settled (`now` callable); the gate's event-*input* API is still T-006's gap to close (with qa-tuning).

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
