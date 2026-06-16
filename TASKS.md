# Tasks

Shared task list. Any agent (Claude Code or a spawned subagent) reads this before starting work and updates it as they progress. This is the coordination substrate — it's how parallel agents avoid stepping on each other.

## How to use this file

**Before starting work:**
1. Read this file top to bottom.
2. Check for tasks marked `claimed` — someone else is on it, leave it alone.
3. Check `blocked` tasks — a blocker might have cleared.
4. Pick a task marked `open` that fits your role, or add a new one.

**When claiming a task:**
- Change status from `open` → `claimed`.
- Fill in `owner` with your agent name.
- Add a `claimed_at` timestamp (UTC ISO 8601, e.g., `2026-04-22T01:30:00Z`).
- **Commit this change immediately** before starting actual work. This is the atomic claim.

**While working:**
- Update `progress` with brief notes as milestones are hit.
- If you discover the task needs to split, mark the original `split` and add the new tasks below.
- If you're spawned in a worktree, note the worktree path and branch in `notes`.

**When finishing:**
- Change status to `review` (if a reviewer is needed) or `done`.
- Add `completed_at` timestamp on the `review` → `done` transition.
- Write a one-line handoff in `notes` for whoever picks up the next related task.
- Commit.

**If blocked:**
- Change status to `blocked`.
- Write what's blocking in `notes`.
- Flag it in `NOTES.md` if human input is needed.

## Status values

- `open` — unclaimed, ready for someone to pick up
- `claimed` — actively being worked on
- `blocked` — waiting on something (human input, another task, external)
- `review` — code written, awaiting review before merge
- `done` — complete and merged
- `split` — was replaced by smaller tasks, see entries below it
- `cancelled` — decided not to do it, with reason in notes

## Priority values

- `P0` — blocker for current phase, drop other things
- `P1` — core work for current phase
- `P2` — nice-to-have / next phase prep

## Task entry format

```
### T-### — Short title
- **Status:** open | claimed | blocked | review | done | split | cancelled
- **Priority:** P0 | P1 | P2
- **Role:** which agent role is best suited
- **Owner:** <agent name, only when claimed+>
- **Phase:** 0 | 1 | 2 | ...
- **Created:** YYYY-MM-DDTHH:MM:SSZ
- **Claimed:** YYYY-MM-DDTHH:MM:SSZ
- **Completed:** YYYY-MM-DDTHH:MM:SSZ
- **Depends on:** T-### (if any)
- **Description:** what needs doing and why
- **Acceptance:** how we know it's done
- **Progress:**
  - timestamp — note
- **Notes:** handoff info, worktree path if applicable, blockers, etc.
```

## Conventions for parallel work

- **Atomic claim = the commit that changes `open` → `claimed`.** If two agents try to claim the same task, git surfaces the conflict — first commit wins. Loser re-reads and picks a different task.
- **One owner per task.** If a task needs two roles, split it into two tasks with a `depends on` link.
- **Worktrees get a task per worktree.** Don't have one task span multiple worktrees.
- **Always commit the task file update before starting the actual work.** Otherwise a concurrent agent can't see you've claimed it.

---

## Phases (from `.pdr.md`)

- **phase_0: Foundations** — Python scaffold, the six deep core modules with unit tests, and an end-to-end MOCK pipeline running green.
- **phase_1: Real ears** — Always-on mic + Silero VAD + local ASR producing a live transcript on the M5 (ASR runtime selected via spike).
- **phase_2: Local understanding** — Living Summary and Wall Detection backed by a local Qwen2.5 (MLX) model, replacing the mock backend.
- **phase_3: Knowing when to speak** — TurnTakingGate + SummonController wired to real VAD timing — fast wake-word summon, conservative polite interjection, abort-on-resume.
- **phase_4: The voice** — Engaged path: Claude composes a spoken-style grounded answer streamed into ElevenLabs voice output.
- **phase_5: Make it live & tune** — Full always-on loop on the M5; tune interjection thresholds against the precision metric on captured conversations.

---

## Active tasks

### T-001 — Python project scaffold
- **Status:** done
- **Priority:** P0
- **Role:** core-engineer
- **Owner:** core-engineer
- **Phase:** 0
- **Created:** 2026-06-15T00:00:00Z
- **Claimed:** 2026-06-15T22:55:20Z
- **Completed:** 2026-06-15T22:58:46Z
- **Depends on:** —
- **Description:** Stand up the `jarvis` Python package (3.11+): package layout, dependency/venv management, pytest, and lint/format. Establish the home the core modules and adapters live in, distinct from `prototypes/`.
- **Acceptance:** `pytest` runs (zero tests is fine); the package imports; lint/format configured and passing; a `.gitignore` covers Python artifacts, `.DS_Store`, and any audio/model caches.
- **Progress:**
  - 2026-06-15T22:55Z — claimed; installed uv 0.11.21 (standalone, non-interactive) to get managed CPython 3.11.15.
  - 2026-06-15T22:58Z — src-layout `src/jarvis/` + pyproject (requires-python >=3.11, hatchling); pytest + ruff wired; 2 smoke tests pass; ruff lint+format clean; package imports. Module map written.
- **Notes:** DONE (no reviewer needed for scaffolding). Toolchain: uv + src-layout + pytest + ruff (see DECISIONS.md). `uv.lock` + `.python-version` committed; `prototypes/` excluded from ruff (reference, not package). **Next unblocked: T-002 (RollingWindow + core data types)** — freeze `Utterance` there and inject the clock into RollingWindow's time-bound (do NOT use `time.monotonic()` internally; qa-tuning's T-009 harness needs the injected clock). Seam contract is in `docs/architecture/module-map.md`.

### T-002 — Core data types + RollingWindow (with tests)
- **Status:** done
- **Priority:** P0
- **Role:** core-engineer
- **Owner:** core-engineer
- **Phase:** 0
- **Created:** 2026-06-15T00:00:00Z
- **Claimed:** 2026-06-15T23:20:00Z
- **Completed:** 2026-06-15T23:30:00Z
- **Depends on:** T-001
- **Description:** Implement `Utterance` and `RollingWindow` (bounded by utterance count AND elapsed time), with the `add` / `utterances` / `transcript` interface.
- **Acceptance:** Unit tests prove eviction by count and by time, and transcript rendering; tests green.
- **Progress:**
  - 2026-06-15T23:20Z — claimed. Pinned the clock-injection convention to `now: Callable[[], float]` in module-map.md first (closes T-009 gap #1).
  - 2026-06-15T23:30Z — shipped `jarvis/types.py` (frozen `Utterance`, `ts` required/producer-supplied), `jarvis/core/text.py` (shared `keywords`/`jaccard`, ported from prototype), `jarvis/core/rolling_window.py` (count+time bound, injected `now`, ages on read). `tests/test_rolling_window.py` (15 tests on `SimulatedClock`): count eviction, time eviction, boundary, age-on-read-without-add, both-bounds, transcript/keywords rendering, frozen-Utterance, bad-bounds guards. Suite 37 green, ruff lint+format clean.
- **Notes:** DONE (not a mandatory-review trigger). **`Utterance` is FROZEN** — `speaker`, `text`, `ts` (required). sensing-engineer's `MicSource` must stamp `ts` from the VAD timeline. RollingWindow evicts relative to *now()* (not newest ts) and re-evicts on read, so it ages during silence — divergence from the prototype, documented in module-map.md. Shared text helpers in `jarvis/core/text.py` are ready for T-003 to reuse. **Unblocks T-004 (LivingSummary)** once T-003 also lands.

### T-003 — TopicShiftDetector (with tests)
- **Status:** done
- **Priority:** P1
- **Role:** core-engineer
- **Owner:** core-engineer
- **Phase:** 0
- **Created:** 2026-06-15T00:00:00Z
- **Claimed:** 2026-06-15T23:32:00Z
- **Completed:** 2026-06-15T23:40:00Z
- **Depends on:** T-001
- **Description:** Implement the pure topic-shift decision used to gate summary refresh ("redraw only changed pixels").
- **Acceptance:** Tests cover representative shift and no-shift cases through the public interface.
- **Progress:**
  - 2026-06-15T23:32Z — claimed.
  - 2026-06-15T23:40Z — shipped `jarvis/core/topic_shift.py` (`TopicShiftDetector`: pure, no hidden state; Jaccard < threshold; constructor-injected `threshold`, default 0.30; `shifted`/`similarity`/`threshold` interface). `tests/test_topic_shift.py` (12 tests): representative shift + no-shift, strict-below boundary, empty-set edges (cold start, both-empty), configurability, range guard. Suite 48 green, ruff clean.
- **Notes:** DONE (not a mandatory-review trigger). Pure decision over keyword sets — reuses `jarvis/core/text.jaccard`. **Scope fence:** cold-start minimum + the ≥2-since-update debounce belong to `LivingSummary` (T-004), NOT here. **T-002 + T-003 done → T-004 (LivingSummary) is now UNBLOCKED.**

### T-004 — LivingSummary delta-update (with tests)
- **Status:** done
- **Priority:** P0
- **Role:** core-engineer
- **Owner:** core-engineer
- **Phase:** 0
- **Created:** 2026-06-15T00:00:00Z
- **Claimed:** 2026-06-15T23:50:00Z
- **Completed:** 2026-06-16T00:05:00Z
- **Depends on:** T-002, T-003
- **Description:** Implement `LivingSummary.consider_update(window) -> bool` that re-summarizes only on a detected topic shift, using an injected summarizer (fake in tests).
- **Acceptance:** Tests prove: refresh on shift, no refresh below the cold-start minimum, no refresh when there's no shift; uses the injected fake summarizer (no live model).
- **Progress:**
  - 2026-06-15T23:50Z — claimed.
  - 2026-06-16T00:05Z — shipped `jarvis/core/living_summary.py` (`LivingSummary`: holds an injected `TopicShiftDetector` + tracks basis keywords; `consider_update(window) -> bool`; cold-start `MIN_UTTERANCES_FOR_SUMMARY=3` + debounce `MIN_UTTERANCES_SINCE_UPDATE=2`; `text` exposed) + the frozen `SummarizerBackend` Protocol. `tests/test_living_summary.py` (12 tests on `SimulatedClock`+`RollingWindow`+`FakeSummarizer`): refresh-on-shift, no-refresh-below-cold-start, no-refresh-no-shift, asserts the injected fake is what's called (recorded transcripts/prev_summaries), debounce, first-summary timing, config guards. Suite 60 green, ruff lint+format clean.
- **Notes:** DONE (not a mandatory-review trigger). **Seam reconciliation:** `SummarizerBackend.summarize(transcript, prev) -> str` matches `FakeSummarizer` exactly — no disagreement; declared as a `typing.Protocol` in `living_summary.py` (not yet a shared `adapters/backends.py` — that consolidates at T-008). The real Qwen2.5/MLX backend (T-202) drops in behind it untouched. **Window-sizing note for T-008:** a topic shift only registers once the old topic's utterances roll out of the `RollingWindow` (by count/time); a wide window holding both topics keeps overlap above threshold. Size the window in the orchestrator accordingly. **T-004 done → T-008 (orchestrator) is one step closer; its remaining deps are T-005, T-006, T-007.**

### T-005 — WallDetector interface + mock backend (with tests)
- **Status:** done
- **Priority:** P0
- **Role:** core-engineer
- **Owner:** core-engineer
- **Phase:** 0
- **Created:** 2026-06-15T00:00:00Z
- **Claimed:** 2026-06-15T00:00:00Z
- **Completed:** 2026-06-15T00:00:00Z
- **Depends on:** T-001
- **Description:** Define `WallDetector` returning `{ is_wall, category, confidence, offer }` over a swappable backend, and ship the heuristic mock backend.
- **Acceptance:** Tests cover each category (`unanswered_question`, `factual_gap`, `stuck_point`, `explicit_ask`) and `none`, with confidence surfaced, via a fake/mock backend.
- **Progress:**
  - 2026-06-15 — claimed; froze `WallVerdict` + `WallCategory` (StrEnum) in `jarvis/types.py`.
  - 2026-06-15 — shipped `core/wall_detector.py` (`WallDetector` over the frozen `WallBackend` Protocol seam + `HeuristicWallBackend` Phase-0 backend). Resolved the T-009 `WallVerdictLike` TODO in `tests/fakes.py` (now returns the real `WallVerdict`; `wall()`/`no_wall()` build real verdicts). 21 new tests in `test_wall_detector.py`. Suite 81 green, ruff clean.
- **Notes:** **qa-tuning: APPROVED** — verdict schema sound + complete for the downstream gate/summon, speak-threshold correctly kept out of the detector (it's SummonController policy), tests assert external behavior over the FakeWallBackend, all four wall categories + `none` covered, faithful prototype port (adds the `stuck_point` cue the mock had omitted). Suite 97 green, ruff clean. Non-blocking coverage notes (multi-cue priority matrix, confidence-ordering) recorded in `docs/qa/working-notes.md` for T-007/T-010. **`WallVerdict` is FROZEN** — `is_wall`, `category` (enum, `NONE` iff `is_wall` False), `confidence [0,1]` raw, `offer`; `WallVerdict.none()`. Real-backend contract note for local-ml-engineer (T-203) is in `module-map.md` §"Contract for the real backend". **Detector applies NO confidence threshold — the speak gate is SummonController policy (T-007).**

### T-006 — TurnTakingGate on a simulated clock (with tests)
- **Status:** done
- **Priority:** P0
- **Role:** core-engineer
- **Owner:** core-engineer
- **Phase:** 0
- **Created:** 2026-06-15T00:00:00Z
- **Claimed:** 2026-06-15T00:00:00Z
- **Completed:** 2026-06-15T00:00:00Z
- **Depends on:** T-001
- **Description:** Implement the endpoint/gap/abort timing logic — `settled?`, `politeness_gap_elapsed?`, `speech_resumed?` — driven by injected VAD/clock events (no real audio).
- **Acceptance:** Tests drive a simulated clock through settle, politeness-gap-elapsed, and speech-resumed transitions deterministically.
- **Progress:**
  - 2026-06-15 — claimed; designed the event-input API (`on_speech_start()`/`on_speech_end()` edge events on the injected clock).
  - 2026-06-15 — shipped `core/turn_taking_gate.py` (`TurnTakingGate`: edge events + the 3 frozen predicates, asymmetric `settle_seconds`/`politeness_gap_seconds` thresholds injected, no internal `time.monotonic()`). 16 new tests in `test_turn_taking_gate.py` driving `SimulatedClock` through settle → politeness-gap → resume(abort). Suite 97 green, ruff clean. DECISIONS.md entry for the event-input API.
- **Notes:** **qa-tuning: APPROVED** — event-input API is harness-drivable and single-clock-source (events stamped from injected `now()`, no `ts` arg); asymmetric thresholds constructor-injected + guarded (`politeness_gap >= settle >= 0`), not magic; abort-on-resume verified correct (`test_resume_aborts_a_pending_politeness_gap`: a resume at t=1.9 re-arms and the new `on_speech_end` restarts the gap clock — a stale gap can never fire); predicates are pure reads (idempotent). Tests assert public predicates only over the `SimulatedClock`. Suite 97 green, ruff clean. Non-blocking coverage notes (double-`on_speech_start`, equal-thresholds boundary) recorded in `docs/qa/working-notes.md`. **Event-input API + thresholds documented in module-map.md + DECISIONS.md. T-005 + T-006 both approved → T-007 (SummonController) is UNBLOCKED.**

### T-007 — SummonController dual-path state machine (with tests)
- **Status:** done
- **Priority:** P0
- **Role:** core-engineer
- **Owner:** core-engineer
- **Phase:** 0
- **Created:** 2026-06-15T00:00:00Z
- **Claimed:** 2026-06-15T00:00:00Z
- **Completed:** 2026-06-16T00:00:00Z
- **Depends on:** T-005, T-006
- **Description:** Implement the asymmetric dual-summon machine: Path A (wake word) fires immediately; Path B (interjection) fires only on `wall ∧ confidence ≥ threshold ∧ politeness gap`, aborts on resumed speech, and backs off on a repeated identical offer.
- **Acceptance:** Tests prove Path A immediacy, Path B all-conditions gating, abort-on-resume, and back-off — all on the simulated clock.
- **Progress:**
  - 2026-06-15 — claimed.
  - 2026-06-15 — shipped `core/summon_controller.py` (`SummonController`: Path A `on_summon`, Path B `consider_interjection`; injected `TurnTakingGate`, no own clock; `interjection_confidence_floor=0.70` constructor-injected + `[0,1]`-guarded; back-off by `category::offer` signature). Added frozen `Interjection` / `SummonDecision` / `TriggerReason` + `EngagementHandoff` shape to `types.py`. 24 new tests in `test_summon_controller.py`. Suite **121 green**, ruff lint+format clean. DECISIONS.md entry for the decision/handoff boundary.
- **Notes:** **qa-tuning: approved — pure decision machine; Path A unconditional, Path B gates on all five conditions with abort-before-gap precedence, back-off de-dupes by category::offer and only a fire arms it, confidence floor injected + [0,1]-guarded + inclusive; 24 tests assert only the returned SummonDecision/None and the real injected gate's public predicates over the SimulatedClock (no internal coupling). Suite 121 green, ruff clean.** _(prior review brief retained below.)_ **→ qa-tuning: MANDATORY REVIEW** (carries the success metric). What changed + what to check: (1) **Path A immediacy** — `on_summon` ignores gate/wall/floor/back-off, always returns `SUMMON`. (2) **Path B all-conditions gating** — `consider_interjection` returns a decision only if `is_wall ∧ confidence ≥ floor ∧ ¬speech_resumed ∧ politeness_gap_elapsed ∧ not-already-offered`, else `None`; covers the drop-if-any-one-fails matrix. (3) **abort-on-resume** checked *before* the gap (a latched resume suppresses even a stale-elapsed gap). (4) **back-off** by `category::offer` (confidence excluded; only a fire arms it; "twice in a row" semantics tested). (5) **confidence-floor boundary** — `>=` inclusive; just-below drops; floor configurable + range-guarded. **Threshold chosen: `interjection_confidence_floor=0.70`** (matches the prototype's `WALL_CONFIDENCE_TO_SPEAK`; kept in SummonController, not the detector). **Decision/handoff boundary:** controller emits a `SummonDecision`, the orchestrator (T-008) assembles the `EngagementHandoff` — see module-map §SummonController + DECISIONS.md. Coverage notes qa-tuning recorded for T-007 (`docs/qa/working-notes.md`): multi-cue priority + confidence-ordering are detector-level (untouched here); the gate's politeness-gap/resume timings are exercised through the injected real gate.

### T-008 — AttentionLayer orchestrator + end-to-end MOCK run
- **Status:** done
- **Priority:** P0
- **Role:** core-engineer
- **Owner:** core-engineer
- **Phase:** 0
- **Created:** 2026-06-15T00:00:00Z
- **Claimed:** 2026-06-16T00:30:00Z
- **Completed:** 2026-06-16T01:00:00Z
- **Depends on:** T-002, T-003, T-004, T-005, T-006, T-007
- **Description:** Wire the core modules into `AttentionLayer` with `ScriptedSource` and fake responder/voice so a scripted conversation runs end-to-end in mock mode. Formalizes the prototype's behavior in the real package.
- **Acceptance:** A scripted conversation produces summary updates, at least one correct interjection, and a wake-word summon → EngagementHandoff, all without audio or network.
- **Progress:**
  - 2026-06-16T00:30Z — claimed.
  - 2026-06-16T01:00Z — shipped `adapters/` (TranscriptSource + ScriptedSource; backends re-export + HeuristicSummarizerBackend; EngagedResponder/VoiceOutput + Print stand-ins), `attention_layer.py` (orchestrator + `build`/`run_scripted`), `clock.py` (ManualClock), `demo.py` + `__main__.py`. 14 acceptance tests in `test_attention_layer.py`. Suite **135 green**, ruff lint+format clean. Demo runs end-to-end. DECISIONS.md entry for the ScriptedSource timing design.
- **Notes:** **DONE — Phase 0 COMPLETE.** NOT a mandatory-review trigger (wires existing modules; gate/summon/wall internals untouched). The orchestrator owns handoff assembly (decision/handoff boundary, T-007); `ScriptedSource` drives the shared gate + injected clock so the politeness gap elapses deterministically (no `sleep`, no network). The `adapters/` package landed here; `SummarizerBackend`/`WallBackend` protocols stay in their core homes and are re-exported. **Phase 1 picks up:** the sensing spike + `MicSource` replacing `ScriptedSource` behind the frozen `TranscriptSource` seam.

### T-009 — Test harness: simulated clock + fakes
- **Status:** done
- **Priority:** P0
- **Role:** qa-tuning
- **Owner:** qa-tuning
- **Phase:** 0
- **Created:** 2026-06-15T00:00:00Z
- **Claimed:** 2026-06-15T23:03:13Z
- **Completed:** 2026-06-15T23:07:03Z
- **Depends on:** T-001
- **Description:** Build the reusable test scaffolding: a simulated clock utility and fakes (FakeSummarizer, FakeWallBackend, FakeResponder, FakeVoice) the core-module tests share.
- **Acceptance:** Fakes and clock are reusable and documented; the core-module test tasks (T-002…T-008) build on them rather than reinventing.
- **Progress:**
  - 2026-06-15T23:03Z — claimed.
  - 2026-06-15T23:07Z — shipped `tests/clock.py` (SimulatedClock), `tests/fakes.py` (FakeSummarizer/FakeWallBackend/FakeResponder/FakeVoice + WallVerdictLike + wall()/no_wall() helpers), `tests/conftest.py` fixtures, `tests/test_harness.py` (22 self-tests). Suite green (24), ruff clean. Conventions in `docs/qa/eval-plan.md`.
- **Notes:** DONE (test infra, no separate reviewer). **Harness is ready for T-002 (RollingWindow — inject `clock.now`), T-003, T-005 (WallDetector — `FakeWallBackend`, `WallVerdictLike`), T-006 (TurnTakingGate — drive transitions via `clock.advance`), T-007/T-008.** Inject the clock as `now: Callable[[], float]` (pass `clock.now`) and seams via constructor. **Interface gaps for core-engineer to close:** (1) **T-006** — the module map freezes TurnTakingGate's three output predicates but NOT its event-*input* API nor a single pinned clock-injection signature (`now=` callable vs `Clock` object — both mentioned, neither chosen); pick one in T-006. (2) **T-005** — `WallVerdict` isn't frozen yet; harness uses `WallVerdictLike` (matching field names; TODO marker in `tests/fakes.py`), freeze the real type *with* local-ml-engineer and the swap is import-only.

### T-010 — Interjection-precision eval definition
- **Status:** done
- **Priority:** P1
- **Role:** qa-tuning
- **Owner:** qa-tuning
- **Phase:** 0
- **Created:** 2026-06-15T00:00:00Z
- **Claimed:** 2026-06-16T00:00:00Z
- **Completed:** 2026-06-16T00:00:00Z
- **Depends on:** T-007
- **Description:** Define how the success metric is measured: a fixture format for labeled conversations and the precision computation (well-timed/useful interjections vs. false ones). No live data needed yet.
- **Acceptance:** A written eval spec (`docs/qa/eval-plan.md`) plus a fixture schema that Phase 5 calibration will use.
- **Progress:**
  - 2026-06-16 — claimed (T-007 now done → dep cleared).
  - 2026-06-16 — full spec written into `eval-plan.md` (fixture format, precision computation, run model, T-503 hook, 5 illustrative fixtures). Doc-only; suite stays 121 green, ruff clean.
- **Notes:** **DONE — interjection-precision eval spec landed.** Fixture = a monotonic timeline (utterance / speech_start / speech_end) + per-candidate ground-truth (wall, WallCategory, useful|false, match-window) + a `config` block of the 3 thresholds T-503 sweeps. **precision = useful ÷ total Path-B fires**; Path-A summons and `None` decisions excluded; a fire matches a candidate by time window and must be the right category to score "useful". Runs deterministically on the `SimulatedClock` + `FakeWallBackend` (and `ScriptedSource`/fakes once T-008 lands). The yardstick the MVP is judged against. **Phase 0 now has one task left: T-008.**

### T-101 — ASR runtime spike: mlx-whisper vs whisper.cpp on the M5
- **Status:** done
- **Priority:** P1
- **Role:** sensing-engineer (+ local-ml-engineer for the joint M5-budget read)
- **Owner:** sensing-engineer
- **Phase:** 1
- **Created:** 2026-06-15T00:00:00Z
- **Claimed:** 2026-06-15T00:00:00Z
- **Completed:** 2026-06-15T00:00:00Z
- **Depends on:** —
- **Description:** Empirically pick the local ASR runtime for the always-on ambient path. Benchmark the two approved candidates — **mlx-whisper** (Apple-Silicon/MLX native) and **whisper.cpp** (via the `pywhispercpp` binding / Core ML) — on THIS machine (Apple M5 Pro, 18 cores, 64 GB) at a comparable small/base model size. Measure (1) transcription latency — wall-clock per representative clip and, if feasible, a chunked/streaming figure — related to the wedge's ~2 s offer-to-help budget; (2) accuracy — WER (or a qualitative transcript comparison) against a known reference clip; (3) CPU/memory and, as far as observable in one session, sustained-load/thermal behavior (a cold one-shot lies — note any throttling over a short repeated run). The runtime feeds `MicSource` (T-104) behind the frozen `TranscriptSource` seam and must leave M5 headroom for Qwen2.5 (Phase 2) under always-on load.
- **Acceptance:** `docs/audio/asr-spike.md` contains the methodology, the audio sample used (stated exactly, with provenance), a measured comparison table (latency / accuracy / CPU-mem / sustained behavior), and a clear recommendation (runtime + model size + why). A `DECISIONS.md` entry records the choice (or "deferred — blocked"), evidence, and alternatives. Any new dep recorded per the dependency policy. If both runtimes are genuinely un-runnable (no network / install blocked), the task is `blocked` with a clear note — no fabricated numbers.
- **Progress:**
  - 2026-06-15 — claimed; expanded from the Phase-1 one-liner placeholder.
  - 2026-06-15 — env confirmed runnable (network up, uv + brew present, M5 Pro / 64 GB). Installed `mlx-whisper` + `pywhispercpp` into an isolated `asr-spike` uv group. Synthesized two reference clips (macOS `say` → 16 kHz mono WAV; exact ground truth). Benchmarked both at `base.en`: latency/RTF (5 warm runs), WER, isolated peak RSS, 40× sustained-drift. Both ran; nothing blocked, nothing fabricated.
  - 2026-06-15 — wrote `docs/audio/asr-spike.md` (method + comparison table + recommendation), two DECISIONS.md entries (runtime choice + dep-group policy). DONE.
- **Notes:** **DONE.** Recommendation: **mlx-whisper, `base.en`** (English-only; `small.en` = upgrade lever; whisper.cpp/`pywhispercpp` = fallback). Both runtimes are ~25–125× faster than real time and **tie on WER** at `base.en` — choice decided by runtime strategy: mlx-whisper shares the **MLX/Metal/unified-memory** stack Qwen2.5 will use (Phase 2), so one accelerator stack to budget. Short ~3.8 s utterance: mlx 73 ms / whisper.cpp 52 ms — both negligible vs the ~2 s offer budget. Isolated RSS: mlx 463 MB / whisper.cpp 326 MB. No throttling over a 40× single-session run (NOT a multi-hour soak — that's T-504). **⚠️ Coexistence flag:** measured ASR in isolation — the **ASR + Qwen2.5 concurrent always-on budget** must be measured jointly with local-ml-engineer before either side freezes model sizes. **Phase 1 picks up:** T-102 (mic capture loop) / T-104 (`MicSource`) — wire `mlx-whisper base.en` behind `TranscriptSource`. Spike deps live in the `asr-spike` uv group; T-104 promotes only `mlx-whisper` into real package deps. See `docs/audio/asr-spike.md`.

### T-102 — Always-on mic capture loop + AudioSource abstraction
- **Status:** done
- **Priority:** P1
- **Role:** sensing-engineer
- **Owner:** sensing-engineer
- **Phase:** 1
- **Created:** 2026-06-15T00:00:00Z
- **Claimed:** 2026-06-15T00:00:00Z
- **Completed:** 2026-06-15T00:00:00Z
- **Depends on:** T-101
- **Description:** Stand up the always-on microphone capture path that feeds the VAD (T-103) and, ultimately, ASR (T-104). Define a small **`AudioSource`** abstraction (the seam the VAD + tests consume) so nothing downstream depends on real hardware, and implement a real **mic capture loop** over `sounddevice` (PortAudio): continuous, ring-buffered, fixed frame size / sample rate suited to Silero VAD (**16 kHz mono**, fixed frame). No dropped frames; bounded memory (a fixed-size ring buffer, not unbounded growth). A **fake `AudioSource`** feeds synthetic frames in tests so the buffer/loop logic is exercised deterministically with no real mic. Opening the input device triggers a macOS mic-permission prompt for the terminal process; attempt a brief live smoke capture and, **if permission is denied or no device is available, document it — do NOT fail the task or fabricate** a result.
- **Acceptance:** An `AudioSource` Protocol + a real `SoundDeviceMicSource` (16 kHz mono, fixed frame) + a fake `AudioSource`; a bounded ring buffer with proven no-unbounded-growth behavior. Tests drive the capture/buffer logic via the fake source and assert frame shape/rate, ring-buffer wrap/eviction, and bounded memory — deterministic, no real mic. `uv run pytest -q` green (135 baseline + new), ruff clean. `sounddevice` (+ PortAudio) recorded in DECISIONS.md. Live-mic smoke test either runs (report exactly what happened) or is documented as needing the user to grant mic permission (no fabricated capture).
- **Progress:**
  - 2026-06-15 — claimed; expanded from the Phase-1 one-liner.
  - 2026-06-15 — shipped `src/jarvis/audio/` package: `source.py` (`AudioSource` Protocol + frozen `AudioFrame` (16 kHz mono float32, 512-sample/32 ms) + bounded `RingBuffer` + `FakeAudioSource` with silence/tone/pattern builders) and `mic.py` (`SoundDeviceMicSource` — real PortAudio always-on loop, callback→ring-buffer→consumer, lazy import, typed permission/no-device errors). 18 tests in `test_audio_source.py` (synthetic frames, no real mic): ring FIFO/wrap/overflow, bounded-memory-under-heavy-push, frame geometry/energy, fake source, Protocol conformance + error classification. Suite **153 green**, ruff clean. `sounddevice`+`numpy` added to real package deps; DECISIONS.md entry.
  - 2026-06-15 — **LIVE MIC SMOKE TEST RAN** (permission already granted to this terminal): ~1.47 s real capture, 46 frames / 23,552 samples @ 16 kHz mono, **0 overflows**, mean RMS 0.0021 (quiet room, real non-zero energy). Real capture, not fabricated.
- **Notes:** **DONE** (not a mandatory-review trigger). Frozen seams aligned to (not reshaped): T-103 VAD drives `TurnTakingGate.on_speech_start`/`on_speech_end` (edge API; gate stamps time from its injected clock); `MicSource` (T-104) feeds `Utterance` behind `TranscriptSource`. New abstraction introduced: **`AudioSource`** (the audio-path analogue of the injected-backend discipline) — documented in module-map.md §"The audio sensing path". **T-102 done → T-103 (Silero VAD) is UNBLOCKED:** consume `AudioSource` frames, emit gate edges. Live mic works here — T-103's optional live check can use it; full live-transcript smoke is T-105.

### T-103 — Silero VAD speech/silence segmentation
- **Status:** done
- **Priority:** P1
- **Role:** sensing-engineer
- **Owner:** sensing-engineer
- **Phase:** 1
- **Created:** 2026-06-15T00:00:00Z
- **Claimed:** 2026-06-15T00:00:00Z
- **Completed:** 2026-06-15T00:00:00Z
- **Depends on:** T-102
- **Description:** Integrate **Silero VAD** (prefer the lightweight `silero-vad` pip package; torch is acceptable on the M5 — note the dep weight). Consume audio frames from the `AudioSource` (T-102), segment speech vs. silence, and **emit boundary events that drive the `TurnTakingGate`'s `on_speech_start()` / `on_speech_end()` edge API** — the gate stamps time from its injected clock, so the VAD emits *edges*, not timestamps. Keep VAD sensitivity/threshold configurable (constructor-injected). Aligns the live audio path to the same gate the Phase-0 `ScriptedSource` drove.
- **Acceptance:** A `SileroVad` segmenter that consumes `AudioSource` frames and emits speech-start/speech-end edges onto an injected `TurnTakingGate` (and/or a generic edge callback). Tests feed synthetic frames (silence vs. speech-energy) and assert the correct *sequence* of speech-start/speech-end edges — deterministic, no real mic. Threshold/sensitivity configurable. Optional live-mic check skipped when no device/permission. `uv run pytest -q` green, ruff clean. `silero-vad` (+ torch) recorded in DECISIONS.md.
- **Progress:**
  - 2026-06-15 — claimed; expanded from the Phase-1 one-liner. Depends on T-102's `AudioSource`.
  - 2026-06-15 — shipped `src/jarvis/audio/vad.py`: `SileroVad` (consumes `AudioSource` frames, debounces per-frame decisions into clean speech-start/speech-end **edges**, drives an injected `TurnTakingGate` + optional `on_edge` callback; emits edges not timestamps — gate owns the clock; timing in frame units so the audio path is clock-free). `FrameClassifier` seam: default `SileroFrameClassifier` (real Silero model, lazy torch) / test `EnergyFrameClassifier` (RMS). Configurable threshold + hysteresis (`speech_start_frames`/`silence_end_frames`). 14 tests in `test_vad.py` (synthetic frames; assert edge *sequences*; drive a real `TurnTakingGate` through settled/politeness-gap + abort-on-resume). Suite **167 green**, ruff clean. `silero-vad`+`torchaudio` added to real deps; DECISIONS.md entry.
  - 2026-06-15 — **LIVE check RAN** (`test_live_silero_vad_on_mic_optional` PASSED, not skipped): real Silero model loaded + real mic, >0 frames processed end-to-end (permission granted on this M5).
- **Notes:** **DONE** (not a mandatory-review trigger — VAD/audio path; the gate/summon/wall internals are untouched, only *driven* via the frozen edge seam). Edge API aligned to (not reshaped): `TurnTakingGate.on_speech_start`/`on_speech_end` (DECISIONS.md 2026-06-15). New seam introduced: **`FrameClassifier`** (isolates torch so the gate-driving logic is testable without it). **→ T-104 (MicSource) is next:** wire this VAD + `mlx-whisper base.en` into `Utterance` events behind the frozen `TranscriptSource` seam — feed ASR the concatenated frames of each speech segment (the start→end window), stamp `Utterance.ts` from the VAD timeline, and drive the orchestrator's shared gate with the same edges. Promotes `mlx-whisper` from the `asr-spike` uv group into real deps. Then T-105 (live-transcript smoke test). **⚠️ still pending with local-ml-engineer:** the M5 ASR+SLM joint coexistence budget (see `asr-spike.md` + NOTES.md) before model sizes freeze.

### T-104 — MicSource adapter (VAD + mlx-whisper → Utterance behind TranscriptSource)
- **Status:** done
- **Priority:** P1
- **Role:** sensing-engineer
- **Owner:** sensing-engineer
- **Phase:** 1
- **Created:** 2026-06-15T00:00:00Z
- **Claimed:** 2026-06-15T00:00:00Z
- **Completed:** 2026-06-15T00:00:00Z
- **Depends on:** T-102, T-103
- **Description:** Implement **`MicSource`** as a `TranscriptSource` (the same frozen seam `ScriptedSource` implements, so it drops into `AttentionLayer` unchanged). It runs the mic → Silero VAD pipeline; on each **speech segment** (the `on_speech_start`→`on_speech_end` window) it concatenates that segment's frames and transcribes them via **mlx-whisper `base.en`**, producing an `Utterance(speaker, text, ts)` with `ts` stamped from the VAD timeline (sample count ÷ sample rate). It drives the orchestrator's **shared `TurnTakingGate`** with the same speech-start/speech-end edges (so summon/interjection timing works on live audio). Speaker label is a fixed placeholder (diarization out of scope for v0). ASR sits behind a small **`Transcriber` Protocol** seam so a fake transcriber can be injected in unit tests — the real model is never required in unit tests. Promotes **`mlx-whisper`** from the `asr-spike` uv group into real `[project.dependencies]` (now shipped runtime); recorded in DECISIONS.md.
- **Acceptance:** A `MicSource(TranscriptSource)` + a `Transcriber` Protocol seam (default `MlxWhisperTranscriber`, lazy import) + a `FakeTranscriber`. Deterministic tests (no real mic/model) on `FakeAudioSource` + `FakeTranscriber` prove: a speech segment becomes the right `Utterance`; `ts` comes from the VAD timeline; the shared gate receives the matching `on_speech_start`/`on_speech_end` edges; multiple segments yield multiple utterances; silence-only yields none. `uv run pytest -q` green (167 baseline + new), ruff clean. `mlx-whisper` promoted to real deps + DECISIONS.md entry. Optional live check skipped when no device/model.
- **Progress:**
  - 2026-06-15 — claimed; expanded from the Phase-1 one-liner.
  - 2026-06-15 — shipped `src/jarvis/audio/mic_source.py`: `MicSource(TranscriptSource)` (consumes an `AudioSource` through `SileroVad`, brackets each speech segment off the VAD edges, concatenates its frames, transcribes once at `speech_end`, yields `Utterance(speaker, text, ts)` with `ts` from the VAD timeline; drives the shared `TurnTakingGate`; flushes an open segment at end-of-stream; drops empty ASR text). `Transcriber` Protocol seam: default `MlxWhisperTranscriber` (mlx-whisper `base.en`, lazy import) / test `FakeTranscriber`. 11 tests in `test_mic_source.py` (synthetic frames + energy classifier + fake transcriber, no mic/model): single/empty/multi segment, `ts`-from-timeline, gate edges chained + politeness-gap opens, gate=None path, drops-into-AttentionLayer, open-segment flush, 16 kHz guard. **`mlx-whisper` promoted from the `asr-spike` uv group into real `[project.dependencies]`** (demoted out of the spike group, which now lists only the `pywhispercpp` fallback). Suite **178 green** (167 baseline + 11), ruff clean. DECISIONS.md entry; module-map.md updated (MicSource + Transcriber seam, Phase-1 sensing path complete).
- **Notes:** **DONE** (not a mandatory-review trigger — audio path; the gate/summon/wall internals are untouched, only *driven* via the frozen edge + `TranscriptSource` seams). The orchestrator + gate do NOT change for the swap — `MicSource` satisfies the frozen `TranscriptSource` Protocol (`AttentionLayer.run(mic_source)`). **→ T-105 (live-transcript smoke test) completes Phase 1.**

### T-105 — Live-transcript smoke test on the M5 (completes Phase 1)
- **Status:** done
- **Priority:** P1
- **Role:** sensing-engineer
- **Owner:** sensing-engineer
- **Phase:** 1
- **Created:** 2026-06-15T00:00:00Z
- **Claimed:** 2026-06-15T00:00:00Z
- **Completed:** 2026-06-15T00:00:00Z
- **Depends on:** T-104
- **Description:** Run the **real ambient pipeline live on the M5**: `AttentionLayer` wired with `MicSource` (real mic + real Silero VAD + real mlx-whisper `base.en` + the heuristic mock summarizer/wall backends — Qwen2.5 is Phase 2). Confirm end-to-end: spoken audio → transcript → rolling window → living-summary updates, and that a wake-word ("Jarvis") summon and/or a wall interjection can fire on live speech. Generate speech without a human via the macOS `say` loopback (say → speakers → mic → pipeline). Report exactly what the pipeline transcribed and which events fired — never fabricate; if the loopback audio is too quiet/echoey to transcribe cleanly, say so and capture what actually happened. Write the smoke-test method + result into `docs/audio/working-notes.md` (or `docs/audio/live-smoke.md`). If a `--live` demo entry point is added, keep the default `uv run pytest` green and don't make CI depend on a mic.
- **Acceptance:** A documented live run (method + verbatim transcript + which events fired, or an honest note if loopback was poor) in the audio docs; a runnable `--live` path that doesn't break the default test suite; Phase 1 marked COMPLETE in NOTES.md with what Phase 2 picks up.
- **Progress:**
  - 2026-06-15 — claimed; expanded from the Phase-1 one-liner.
  - 2026-06-15 — added `src/jarvis/live.py` (`run_live`) + `python -m jarvis --live` (mic/MLX imports lazy → default `uv run pytest` never touches a mic). Ran the real pipeline live on this M5 via a macOS `say` → **BlackHole 2ch** digital loopback (`--device 5`, clean no-echo PCM): real mic → Silero VAD → mlx-whisper `base.en` → `Utterance` → orchestrator. **Both engagement paths fired live, verbatim captured:** Path-A summon ("Jarvis, add that to my calendar for 7." → ENGAGEMENT trigger `summon`) and Path-B interjection ("What was the date of the conference again?" → `WallDetector` `factual_gap @ 0.80` → after the politeness gap → ENGAGEMENT trigger `wall:factual_gap`, offer "I can find that — want me to?"), plus a living-summary update. Method + verbatim transcripts + honesty box in `docs/audio/live-smoke.md`.
  - 2026-06-15 — **found + fixed a real T-104↔orchestrator integration bug:** `MicSource` stamped `ts` from the VAD frame timeline (~0-based) but the live `RollingWindow` evicts against `time.monotonic` (~1.2 M s) → every live utterance evicted instantly → Path B never saw the wall line. Fix: `MicSource` accepts an optional injected `now`; `run_live` passes the same real clock to gate + window + `MicSource` so `ts` and eviction share one timeline (frame-derived default unchanged → T-104 tests still assert it). New regression test. DECISIONS.md entry. Suite **179 green** (12 MicSource tests), ruff lint+format clean.
- **Notes:** **DONE — Phase 1 COMPLETE.** The ambient half runs on real audio end-to-end; both summon + interjection verified live. Honest caveats recorded: BlackHole is a *digital* (best-case) loopback (real-room WER is T-502), and the Path-B *fire cadence* used a `run_live` trailing re-check standing in for the not-yet-built continuous real-time Path-B loop (T-302, Phase 3) — detection/confidence/gate-timing are all the real live pipeline. **Phase 2 picks up:** Qwen2.5/MLX behind the `SummarizerBackend`/`WallBackend` seams (replacing the heuristic mocks) + the pending **ASR+SLM joint M5 budget** with local-ml-engineer before model sizes freeze.

---

## Planned tasks (Phase 2+ — one-liners, expanded to full entries when the phase becomes active)

_(Phase 1 — Real ears: all tasks T-101…T-105 are full entries above; the phase is complete once T-105 lands.)_

### Phase 2 — Local understanding

### T-201 — Qwen2.5/MLX runtime spike + joint ASR coexistence budget
- **Status:** done
- **Priority:** P0
- **Role:** local-ml-engineer
- **Owner:** local-ml-engineer
- **Phase:** 2
- **Created:** 2026-06-15T00:00:00Z
- **Claimed:** 2026-06-15T12:00:00Z
- **Completed:** 2026-06-15T14:00:00Z
- **Depends on:** T-101
- **Description:** Empirically select the Qwen2.5/MLX model size (candidates: 1.5B vs 3B, 4-bit quantized, MLX-community builds) for the always-on summarize/detect_wall backends. This spike folds in the mandatory joint M5 budget measurement that was flagged repeatedly in `docs/audio/asr-spike.md`: measure ASR (mlx-whisper base.en) + Qwen2.5 running concurrently on the same utterance, both as MLX/Metal consumers on the unified-memory GPU. Deliver a model-size recommendation and an ASR base.en-vs-small.en verdict. NOT an implementation — T-202/T-203 pick up the real backends.
- **Acceptance:**
  - `docs/ml/qwen-coexistence-spike.md` with: exact model repos/quant used, audio clip provenance, per-candidate quality+latency (isolated), joint budget numbers (combined latency/memory/contention/sustained), recommendation, and honesty box. ✅
  - `DECISIONS.md` entry for the Qwen2.5 size choice and the ASR base.en/small.en verdict. ✅
  - `mlx-lm` added via `uv add --group slm-spike` (isolated group, not core deps yet). ✅
  - Benchmark harness kept out of default pytest path; suite stays 182 green; ruff clean. ✅
  - Real measured numbers on the real M5 — nothing fabricated. ✅
- **Progress:**
  - 2026-06-15T12:00Z — claimed; expanded from Phase-2 one-liner; reading orientation docs before work.
  - 2026-06-15T13:00Z — `mlx-lm` added to `slm-spike` group; both model downloads complete; isolated + joint + sustained measurements done on this M5 (real numbers, nothing fabricated).
  - 2026-06-15T14:00Z — spike doc written; DECISIONS.md entries added; TASKS.md updated; suite 182 green; ruff clean.
- **Notes:** DONE (not a mandatory-review trigger — spike only, no qa-tuning-gated module changed). **Recommendation frozen:** `mlx-community/Qwen2.5-3B-Instruct-4bit`; ASR stays `base.en`. **1.5B eliminated:** returns `is_wall: false` on every test including unambiguous `explicit_ask` — non-functional for detect_wall. **3B joint budget: 657 ms median** (ASR 40 ms + summarize 250 ms + detect_wall 366 ms) vs 2,000 ms offer budget → 1,343 ms margin. **MUST use chat template** (tokenizer.apply_chat_template) — raw prompts degrade quality and inflate latency. Peak joint RSS 3,271 MB (64 GB machine). No thermal throttling. `mlx-lm` promoted from `slm-spike` group to real deps at T-202. **→ T-202 (local summarizer backend) is UNBLOCKED.** Also produce `docs/ml/slm-backend.md` (per role spec "first task") at T-202 time.

### T-202 — Local summarizer backend (Qwen2.5/MLX)
- **Status:** done
- **Priority:** P0
- **Role:** local-ml-engineer
- **Owner:** local-ml-engineer
- **Phase:** 2
- **Created:** 2026-06-15T00:00:00Z
- **Claimed:** 2026-06-15T15:00:00Z
- **Completed:** 2026-06-15T16:00:00Z
- **Depends on:** T-201
- **Description:** Implement the real `SummarizerBackend.summarize(transcript, prev) -> str` behind the frozen seam declared in `jarvis/core/living_summary.py`, backed by `mlx-community/Qwen2.5-3B-Instruct-4bit` via `mlx_lm`. Introduce a `src/jarvis/ml/` package containing: (1) a reusable `QwenModel` loader that loads `(model, tokenizer)` once lazily and exposes a small `generate()` helper; and (2) a thin `QwenSummarizerBackend` that takes the loader via injection and implements `summarize()`. The loader is designed to serve both T-202 (summarize) and T-203 (detect_wall) — same loader instance, no double-load. Must use `tokenizer.apply_chat_template` with proper system/user messages (NOT raw string prompts). Promote `mlx-lm` from the `slm-spike` uv group into real `[project.dependencies]` (same pattern as `mlx-whisper` at T-104). Also produce `docs/ml/slm-backend.md` (the role spec's "first deliverable" per the agent spec). T-202 is NOT qa-tuning-gated (the summarizer is not a gate/summon/wall module).
- **Acceptance:**
  - `src/jarvis/ml/` package exists with `QwenModel` loader + `QwenSummarizerBackend`.
  - `QwenSummarizerBackend` satisfies `SummarizerBackend` Protocol (runtime-checkable).
  - `mlx_lm` imported lazily inside the loader — importing `jarvis.ml` never loads MLX.
  - Unit tests (model-free): test prompt/message construction; test backend satisfies protocol; assert `transcript`/`prev` thread into the chat-template message correctly via a stub generate call.
  - One optional live test that self-skips when MLX/weights are unavailable (mirrors `test_live_silero_vad_on_mic_optional`).
  - `~/.local/bin/uv run pytest -q` green (currently 182) and model-free.
  - `ruff check` + `ruff format` clean.
  - `mlx-lm` promoted to real `[project.dependencies]`; old `slm-spike` group retained for the spike dep; DECISIONS.md entry added.
  - `docs/ml/slm-backend.md` written (SLM runtime choice, prompt designs, summarize/detect_wall contracts, shared loader design).
- **Progress:**
  - 2026-06-15T15:00Z — claimed; reading orientation docs.
  - 2026-06-15T16:00Z — shipped `src/jarvis/ml/` package (`__init__.py`, `qwen.py`, `summarizer.py`); 25 new tests in `tests/test_qwen_summarizer.py` (24 model-free + 1 live); promoted `mlx-lm` to real deps; wrote `docs/ml/slm-backend.md` and DECISIONS.md entry. Suite 207 green (182 baseline + 25), ruff clean.
- **Notes:** DONE (not qa-tuning-gated — summarizer is not a gate/summon/wall module). **Handoff to T-203 (QwenWallBackend):** reuse `QwenModel` from `src/jarvis/ml/qwen.py` — construct once, inject into both `QwenSummarizerBackend` AND `QwenWallBackend`. The loader is ready; just add `src/jarvis/ml/wall.py` with a `QwenWallBackend` that parses the model's JSON into `WallVerdict`. Prompt design stub in `docs/ml/slm-backend.md` §wall. T-203 IS qa-tuning-gated (wall behavior is the success-metric-critical path).
- (planned T-203) Local wall-detection backend — implement `detect_wall()` with structured output. [local-ml-engineer]
- (planned T-204) Swap mock backend → local backend behind existing interfaces; re-run core tests green. [local-ml-engineer]

### Phase 3 — Knowing when to speak
- (planned T-301) Wire TurnTakingGate to real Silero VAD timing events. [core-engineer + sensing-engineer]
- (planned T-302) Real-time SummonController — wake word + interjection on live audio. [core-engineer]
- (planned T-303) Validate abort-on-resume and back-off on live audio. [core-engineer + qa-tuning]
- (planned T-304) Latency budget pass — gate → detector → offer within target. [core-engineer]

### Phase 4 — The voice
- (planned T-401) EngagedResponder — Claude spoken-style answer, grounded in the handoff, streamed. [voice-integration-engineer]
- (planned T-402) VoiceOutput — ElevenLabs streaming TTS; first audio in ~2s. [voice-integration-engineer]
- (planned T-403) Token-stream Claude → ElevenLabs input; barge-safe playback. [voice-integration-engineer]
- (planned T-404) Full engaged path on live audio — summon → spoken answer. [voice-integration-engineer]

### Phase 5 — Make it live & tune
- (planned T-501) Always-on end-to-end run on the M5. [core-engineer]
- (planned T-502) Capture-and-label tooling for real conversations (ephemeral, opt-in). [qa-tuning]
- (planned T-503) Tune politeness-gap + confidence threshold against the interjection-precision metric. [qa-tuning]
- (planned T-504) Stability / thermal / battery pass for sustained always-on. [sensing-engineer]
