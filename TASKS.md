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
- **Status:** review
- **Priority:** P0
- **Role:** core-engineer
- **Owner:** core-engineer
- **Phase:** 0
- **Created:** 2026-06-15T00:00:00Z
- **Claimed:** 2026-06-15T00:00:00Z
- **Depends on:** T-001
- **Description:** Define `WallDetector` returning `{ is_wall, category, confidence, offer }` over a swappable backend, and ship the heuristic mock backend.
- **Acceptance:** Tests cover each category (`unanswered_question`, `factual_gap`, `stuck_point`, `explicit_ask`) and `none`, with confidence surfaced, via a fake/mock backend.
- **Progress:**
  - 2026-06-15 — claimed; froze `WallVerdict` + `WallCategory` (StrEnum) in `jarvis/types.py`.
  - 2026-06-15 — shipped `core/wall_detector.py` (`WallDetector` over the frozen `WallBackend` Protocol seam + `HeuristicWallBackend` Phase-0 backend). Resolved the T-009 `WallVerdictLike` TODO in `tests/fakes.py` (now returns the real `WallVerdict`; `wall()`/`no_wall()` build real verdicts). 21 new tests in `test_wall_detector.py`. Suite 81 green, ruff clean.
- **Notes:** **AWAITING qa-tuning REVIEW** (mandatory: WallDetector + thresholds). Completed-pending. **`WallVerdict` is FROZEN** — `is_wall: bool`, `category: WallCategory` (enum), `confidence: float [0,1]`, `offer: str`; `WallVerdict.none()` for the non-wall case. Real-backend contract note for local-ml-engineer (T-203) is in `module-map.md` §"Contract for the real backend". **Detector applies NO confidence threshold — the speak gate is SummonController policy (T-007).** Real backend (Qwen2.5/MLX, T-203) drops in behind the same seam.

### T-006 — TurnTakingGate on a simulated clock (with tests)
- **Status:** claimed
- **Priority:** P0
- **Role:** core-engineer
- **Owner:** core-engineer
- **Phase:** 0
- **Created:** 2026-06-15T00:00:00Z
- **Claimed:** 2026-06-15T00:00:00Z
- **Depends on:** T-001
- **Description:** Implement the endpoint/gap/abort timing logic — `settled?`, `politeness_gap_elapsed?`, `speech_resumed?` — driven by injected VAD/clock events (no real audio).
- **Acceptance:** Tests drive a simulated clock through settle, politeness-gap-elapsed, and speech-resumed transitions deterministically.
- **Progress:**
  - 2026-06-15 — claimed; designing the event-input API (the gap qa-tuning flagged) then implementing on the injected clock.
- **Notes:** This is half of the success-metric-critical timing; qa-tuning is a mandatory reviewer.

### T-007 — SummonController dual-path state machine (with tests)
- **Status:** open
- **Priority:** P0
- **Role:** core-engineer
- **Phase:** 0
- **Created:** 2026-06-15T00:00:00Z
- **Depends on:** T-005, T-006
- **Description:** Implement the asymmetric dual-summon machine: Path A (wake word) fires immediately; Path B (interjection) fires only on `wall ∧ confidence ≥ threshold ∧ politeness gap`, aborts on resumed speech, and backs off on a repeated identical offer.
- **Acceptance:** Tests prove Path A immediacy, Path B all-conditions gating, abort-on-resume, and back-off — all on the simulated clock.
- **Progress:**
- **Notes:** Mandatory review by qa-tuning before merge (it carries the success metric).

### T-008 — AttentionLayer orchestrator + end-to-end MOCK run
- **Status:** open
- **Priority:** P0
- **Role:** core-engineer
- **Phase:** 0
- **Created:** 2026-06-15T00:00:00Z
- **Depends on:** T-002, T-003, T-004, T-005, T-006, T-007
- **Description:** Wire the core modules into `AttentionLayer` with `ScriptedSource` and fake responder/voice so a scripted conversation runs end-to-end in mock mode. Formalizes the prototype's behavior in the real package.
- **Acceptance:** A scripted conversation produces summary updates, at least one correct interjection, and a wake-word summon → EngagementHandoff, all without audio or network.
- **Progress:**
- **Notes:**

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
- **Status:** open
- **Priority:** P1
- **Role:** qa-tuning
- **Phase:** 0
- **Created:** 2026-06-15T00:00:00Z
- **Depends on:** T-007
- **Description:** Define how the success metric is measured: a fixture format for labeled conversations and the precision computation (well-timed/useful interjections vs. false ones). No live data needed yet.
- **Acceptance:** A written eval spec (`docs/qa/eval-plan.md`) plus a fixture schema that Phase 5 calibration will use.
- **Progress:**
- **Notes:** This is the yardstick the whole MVP is judged against.

---

## Planned tasks (Phase 1+ — one-liners, expanded to full entries when the phase becomes active)

### Phase 1 — Real ears
- (planned T-101) ASR/runtime spike — benchmark mlx-whisper vs whisper.cpp on the M5 (latency, accuracy, CPU/thermal); pick one. [sensing-engineer + local-ml-engineer]
- (planned T-102) Always-on mic capture loop — ring-buffered, low-latency. [sensing-engineer]
- (planned T-103) Silero VAD gating — speech/silence segmentation feeding the gate and ASR. [sensing-engineer]
- (planned T-104) MicSource adapter — wire VAD + ASR into `Utterance` events behind `TranscriptSource`. [sensing-engineer]
- (planned T-105) Live-transcript smoke test on the M5 — speak, see the transcript. [sensing-engineer]

### Phase 2 — Local understanding
- (planned T-201) Qwen2.5/MLX runtime spike — pick model size (e.g. 1.5B vs 3B) by latency + quality. [local-ml-engineer]
- (planned T-202) Local summarizer backend — implement `summarize()` on Qwen2.5/MLX. [local-ml-engineer]
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
