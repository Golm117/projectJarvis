# Decisions

Append-only log of architectural, product, and tooling decisions. Newest entries at the top. Never edit or delete old entries — if a decision is superseded, write a new entry that references it.

**Format:**

```
## YYYY-MM-DD — Short title
**Decided by:** <human | Claude Code | discussion>
**Status:** accepted | superseded by [link] | reversed
**Context:** why this came up
**Decision:** what we're doing
**Rationale:** why this over alternatives
**Alternatives considered:** what we looked at and rejected
```

Keep entries short. One paragraph per field is plenty. If it takes more, it probably belongs in a design doc under `docs/<domain>/`.

---

## 2026-06-15 — TurnTakingGate event-input API: VAD boundary events on the injected clock (T-006)

**Decided by:** Claude Code (core-engineer) — T-006
**Status:** accepted
**Context:** The module map froze `TurnTakingGate`'s three *output* predicates (`settled` / `politeness_gap_elapsed` / `speech_resumed`) but left the *input* side undesigned — how speech/silence boundaries are fed in — and qa-tuning (T-009) flagged it as the open interface gap to close in T-006. The clock side was already settled (`now: Callable[[], float]`).
**Decision:** The gate consumes two **edge events** off the VAD timeline — `on_speech_start()` and `on_speech_end()` — and nothing else. The events carry **no timestamp argument**; the gate stamps them from the injected `now()` at delivery, keeping one clock source of truth. Silence is measured from the **most recent** `on_speech_end()`. `on_speech_start()` re-arms the gate (silence predicates fall back to `False`) and latches `speech_resumed()` `True` if it interrupted an already-open gap; the next `on_speech_end()` clears that latch. The three predicates are **pure reads** of (state, now) — idempotent, no consume-on-read. The two thresholds (`settle_seconds` ≈ 0.6, `politeness_gap_seconds` ≈ 2.0, the latter must be ≥ the former) are constructor-injected.
**Rationale:** The gate reasons about *durations of silence*, so the two things it needs are the two transition instants — not a per-frame level stream. An edge API is smaller, has no threshold-crossing/debounce bookkeeping inside the gate, and maps 1:1 onto Silero VAD's segment callbacks in Phase 3 (a VAD segmenter emits exactly speech-start / speech-end). Timestamping from the single injected clock (rather than a `ts` arg per event) preserves the "no hidden clock / one clock convention" constraint and lets `SimulatedClock` drive every transition deterministically. Pure-read predicates let `SummonController` and tests poll freely.
**Alternatives considered:** A per-frame `feed(is_speech: bool)` / `on_vad(level)` poll (rejected — pushes endpoint debouncing and frame-rate coupling into the gate; the gate would have to detect edges itself). A single `feed(event)` with an event enum (rejected — two named methods are clearer at call sites and for the VAD adapter to target; no real expressiveness lost). Passing an explicit `ts` with each event (rejected — a second clock source that could drift from `now()`; the gate already has the injected clock). Making the predicates consume-on-read / event-emitting (rejected — the module map's predicates are *queries*; `SummonController` needs to poll them across ticks without side effects).

## 2026-06-15 — Test harness: shared simulated clock + seam fakes (T-009)

**Decided by:** Claude Code (qa-tuning) — T-009
**Status:** accepted
**Context:** The six core modules (T-002…T-008) all need a deterministic time source and doubles for the swappable backend/adapter seams. Letting each test task reinvent these would drift — different clocks, different fake shapes — and risk tests pinned to internals that break when the real model/audio/voice backends swap in (Phases 1, 2, 4).
**Decision:** One shared harness under `tests/`: `clock.py` (`SimulatedClock` — injected, monotonic-by-construction, driven with `advance`/`set`, no real `sleep`), `fakes.py` (`FakeSummarizer`, `FakeWallBackend`, `FakeResponder`, `FakeVoice` — each presets a return and records calls; plus a `WallVerdictLike` stand-in until T-005 freezes the real `WallVerdict`), `conftest.py` (fixtures), and `test_harness.py` (self-tests). The governing rule for all core-module tests: **assert on external behavior — return values, emitted events, seam calls — never on private fields.** Modules receive time as `now: Callable[[], float]` (pass `clock.now`) and seams via the constructor. Conventions documented in `docs/qa/eval-plan.md`.
**Rationale:** Bind the test infra to the *work* (the seam contract in `module-map.md`), not to each task, so all six modules share one vocabulary and behavior-pinned tests survive the mock→real backend swaps the architecture is built to allow. The injected clock is the mechanism the module map's "no hidden `time.monotonic()`" constraint exists to enable.
**Alternatives considered:** Per-task ad-hoc clocks/fakes (rejected — drift, duplication, no shared conventions). `freezegun`/`unittest.mock` time patching (rejected — patches a hidden global clock, which fights the injected-clock constraint and the "no I/O/hidden state in core" design; explicit injection is more honest and deterministic). `unittest.mock.Mock` for the seams (rejected for the default path — hand-written fakes give readable preset/record APIs and typed verdict helpers; `Mock` is available to tests that want it).

## 2026-06-15 — Phase 0 toolchain: uv + src-layout + pytest + ruff

**Decided by:** Claude Code (core-engineer) — T-001 scaffold
**Status:** accepted
**Context:** T-001 stands up the real `jarvis` package. The stack requires Python 3.11+, but the machine's system `python3` is 3.9.6 and no 3.11+ was installed (no pyenv, no brewed python3.11).
**Decision:** Use **uv** (0.11.x, installed via the official standalone installer to `~/.local/bin` — no sudo, no interactive prompts) to manage a pinned **CPython 3.11.15** and the venv. Package uses a **src-layout** (`src/jarvis/`) with a `pyproject.toml` (`requires-python = ">=3.11"`, hatchling build backend). **pytest** is the test runner (`pythonpath = ["src"]`, `testpaths = ["tests"]`); **ruff** is lint + format (line-length 100, target py311, rule set E/W/F/I/N/UP/B/C4/SIM). `prototypes/` is excluded from ruff — it is reference, not the package. `uv.lock` and `.python-version` are committed to pin the toolchain.
**Rationale:** uv is the fastest, fully non-interactive way to obtain a managed 3.11 without touching the system Python or running a heavy/interactive installer — exactly the environment-note guidance in the task. src-layout prevents accidental imports of the un-built package and keeps the import surface honest. ruff is one tool for lint + format, fast, zero-config-friendly.
**Alternatives considered:** pyenv (rejected — not installed; heavier setup than the uv standalone binary). Homebrew `python3.11` + stdlib `venv` (viable fallback but slower to provision and no lockfile). flat-layout (rejected — src-layout is the project's recommended layout and avoids shadowing). black + flake8 + isort (rejected — ruff subsumes all three in one fast tool).

## 2026-06-15 — Asymmetric dual-summon (fast when called, polite when interjecting)

**Decided by:** discussion (human + research)
**Status:** accepted
**Context:** Jarvis can engage two ways — a wake-word summon and a proactive interjection. Turn-taking/endpointing research (LiveKit VAD+semantic endpointing; RESPOND's turn-claim aggressiveness) shows the agent must distinguish a completion pause from a thinking pause, and that an *uninvited* third party should behave differently from an *addressed* assistant.
**Decision:** Two initiation paths with deliberately opposite timing. Path A (summon) responds fast (~500–700 ms endpoint, low confidence bar). Path B (interjection) hangs back (~2 s politeness gap, confidence ≥ ~0.70, aborts on resumed speech). Completeness/intent is folded into the WallDetector call rather than a separate end-of-turn model. Defaults are conservative and tuned later on captured conversations.
**Rationale:** A false summon is harmless; a false interjection is the assistant talking over people. The asymmetry encodes that social cost directly.
**Alternatives considered:** A single uniform endpoint threshold for both paths (rejected — would make interjection either too eager or summon too slow). A full-duplex end-to-end model like Moshi (rejected for v0 — heavy, not debuggable; documented as future direction).

## 2026-06-15 — Cloud only at the moment of answering; ambient stays local

**Decided by:** human
**Status:** accepted
**Context:** Always-on listening plus a desire to avoid data usage and keep conversations private.
**Decision:** The ambient half (mic → VAD → ASR → summary → wall detection) runs 100% on-device. The cloud (Claude for the answer, ElevenLabs for the voice) is invoked only once Jarvis is actually engaged. The rolling transcript is ephemeral by default.
**Rationale:** Keeps everyday conversation off the network while still getting high-quality answers when it matters. Encoded as hard-nos in `.pdr.md`.
**Alternatives considered:** Cloud transcription/summarization (rejected — privacy + data usage). Fully local answering for v0 (deferred — local large-model answers are a future direction, not the MVP).

---

## 2026-06-15 — Project bootstrapped; agent roster assembled from work analysis

**Decided by:** human (via grill skill)
**Status:** accepted
**Context:** Project bootstrap. The grill skill walked through `.pdr.md`, produced the phase breakdown and Phase 0 task list, then proposed an agent roster derived from the kinds of work the tasks demand.
**Decision:** The roster captured in `.pdr.md` Section 6 (core-engineer, sensing-engineer, local-ml-engineer, voice-integration-engineer, qa-tuning) is the assembled crew for this project. Each agent was scaffolded from the universal blank template using its grill-produced spec. The all-hands exercise ran after scaffolding.
**Rationale:** Agent rosters are purpose-built per project, not selected from a default menu. The sensing/local-ml split was kept (not merged) because real-time audio and MLX inference are genuinely different disciplines.
**Alternatives considered:** A canonical default roster (rejected — wrong fit per project). Merging sensing-engineer + local-ml-engineer into one `edge-ml-engineer` (considered and explicitly rejected by the human — keep them separate).

---

## 2026-06-15 — Approved stack

**Decided by:** human
**Status:** accepted
**Context:** Project bootstrap. The PDR lists the tools committed to from day one. Two runtime choices are deliberately deferred to spikes.
**Decision:** Use Python 3.11+, Anthropic Claude API (`claude-opus-4-8`), ElevenLabs, Silero VAD, local ASR (mlx-whisper vs whisper.cpp — Phase 1 spike), and Qwen2.5 via MLX (size — Phase 2 spike). Anything outside this list requires human approval and at least one alternative considered.
**Rationale:** A short approved-stack list keeps integration surface small. The ASR runtime and SLM size are the two genuine unknowns on the M5, so they're settled empirically via spikes rather than guessed now.
**Alternatives considered:** Locking the ASR/SLM runtimes now without benchmarking (rejected — premature on new M5 hardware). Open-ended dependency policy (rejected — drift).

---

## 2026-06-15 — Compliance posture: none (privacy via hard-nos)

**Decided by:** human
**Status:** accepted
**Context:** The transcript (utterance / rolling-window / living-summary) carries personal/sensitive spoken content, so the schema flags PII. The PDR's PII-without-compliance check was raised explicitly during grill.
**Decision:** No formal compliance regime applies — this is a personal, single-user, on-device, ephemeral tool. The privacy posture is enforced through the hard-nos (no ambient audio to cloud, no persistence by default) rather than a regulatory framework.
**Rationale:** Formal regimes (GDPR/HIPAA) don't map onto a personal tool you run on yourself; encoding privacy as concrete hard-nos is the enforceable, right-sized version. The PII-vs-compliance tension was consciously resolved, not ignored.
**Alternatives considered:** Treating it as privacy-regulated with GDPR-like rigor (rejected — premature for a personal v0). Recording-consent law for third parties is noted as a real consideration to revisit if the tool is ever shared or moved off-device.

---

## 2026-06-15 — Methodology grounding

**Decided by:** human
**Status:** accepted
**Context:** The project is built around a specific conceptual frame; naming it gives every agent shared vocabulary and a way to spot drift.
**Decision:** Graduated attention beyond the binary wake word + turn-taking/endpointing theory (transition-relevance places, VAD+semantic-completeness endpointing, asymmetric summon vs. interjection), with a delta-updated living summary ("redraw only the changed pixels"). Source: Conversation Analysis turn-taking (Sacks/Schegloff/Jefferson); LiveKit end-of-turn detection; RESPOND (arXiv 2603.21682); full-duplex survey (arXiv 2509.14515); internal PRD 01 & 02.
**Rationale:** Without a named frame, agents make divergent implicit modeling choices. The frame is a coordination mechanism.
**Alternatives considered:** No explicit methodology grounding (rejected — vocabulary drift).
