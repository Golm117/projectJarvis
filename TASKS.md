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
- **Status:** claimed
- **Priority:** P0
- **Role:** core-engineer
- **Owner:** core-engineer
- **Phase:** 0
- **Created:** 2026-06-15T00:00:00Z
- **Claimed:** 2026-06-15T22:55:20Z
- **Depends on:** —
- **Description:** Stand up the `jarvis` Python package (3.11+): package layout, dependency/venv management, pytest, and lint/format. Establish the home the core modules and adapters live in, distinct from `prototypes/`.
- **Acceptance:** `pytest` runs (zero tests is fine); the package imports; lint/format configured and passing; a `.gitignore` covers Python artifacts, `.DS_Store`, and any audio/model caches.
- **Progress:**
- **Notes:** The `prototypes/attention-layer/` code is reference, not the package — port logic into the real package deliberately.

### T-002 — Core data types + RollingWindow (with tests)
- **Status:** open
- **Priority:** P0
- **Role:** core-engineer
- **Phase:** 0
- **Created:** 2026-06-15T00:00:00Z
- **Depends on:** T-001
- **Description:** Implement `Utterance` and `RollingWindow` (bounded by utterance count AND elapsed time), with the `add` / `utterances` / `transcript` interface.
- **Acceptance:** Unit tests prove eviction by count and by time, and transcript rendering; tests green.
- **Progress:**
- **Notes:**

### T-003 — TopicShiftDetector (with tests)
- **Status:** open
- **Priority:** P1
- **Role:** core-engineer
- **Phase:** 0
- **Created:** 2026-06-15T00:00:00Z
- **Depends on:** T-001
- **Description:** Implement the pure topic-shift decision used to gate summary refresh ("redraw only changed pixels").
- **Acceptance:** Tests cover representative shift and no-shift cases through the public interface.
- **Progress:**
- **Notes:**

### T-004 — LivingSummary delta-update (with tests)
- **Status:** open
- **Priority:** P0
- **Role:** core-engineer
- **Phase:** 0
- **Created:** 2026-06-15T00:00:00Z
- **Depends on:** T-002, T-003
- **Description:** Implement `LivingSummary.consider_update(window) -> bool` that re-summarizes only on a detected topic shift, using an injected summarizer (fake in tests).
- **Acceptance:** Tests prove: refresh on shift, no refresh below the cold-start minimum, no refresh when there's no shift; uses the injected fake summarizer (no live model).
- **Progress:**
- **Notes:**

### T-005 — WallDetector interface + mock backend (with tests)
- **Status:** open
- **Priority:** P0
- **Role:** core-engineer
- **Phase:** 0
- **Created:** 2026-06-15T00:00:00Z
- **Depends on:** T-001
- **Description:** Define `WallDetector` returning `{ is_wall, category, confidence, offer }` over a swappable backend, and ship the heuristic mock backend.
- **Acceptance:** Tests cover each category (`unanswered_question`, `factual_gap`, `stuck_point`, `explicit_ask`) and `none`, with confidence surfaced, via a fake/mock backend.
- **Progress:**
- **Notes:** Real backend (local SLM) arrives in Phase 2 behind this same interface.

### T-006 — TurnTakingGate on a simulated clock (with tests)
- **Status:** open
- **Priority:** P0
- **Role:** core-engineer
- **Phase:** 0
- **Created:** 2026-06-15T00:00:00Z
- **Depends on:** T-001
- **Description:** Implement the endpoint/gap/abort timing logic — `settled?`, `politeness_gap_elapsed?`, `speech_resumed?` — driven by injected VAD/clock events (no real audio).
- **Acceptance:** Tests drive a simulated clock through settle, politeness-gap-elapsed, and speech-resumed transitions deterministically.
- **Progress:**
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
- **Status:** open
- **Priority:** P0
- **Role:** qa-tuning
- **Phase:** 0
- **Created:** 2026-06-15T00:00:00Z
- **Depends on:** T-001
- **Description:** Build the reusable test scaffolding: a simulated clock utility and fakes (FakeSummarizer, FakeWallBackend, FakeResponder, FakeVoice) the core-module tests share.
- **Acceptance:** Fakes and clock are reusable and documented; the core-module test tasks (T-002…T-008) build on them rather than reinventing.
- **Progress:**
- **Notes:** Coordinate interfaces with core-engineer so fakes match the real seams.

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
