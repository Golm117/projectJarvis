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

## Current state — 2026-06-15 (project bootstrapped)

**Phase:** phase_0 — Foundations.

**Just bootstrapped.** The grill skill produced `.pdr.md`, the agent roster, and the initial Phase 0 task list in `TASKS.md`. The all-hands exercise has run — every agent has self-introduced (Phase A) and refined their handoff sketch after seeing the others (Phase B). The handoff-mesh audit is committed at `docs/agents/README.md`.

There is a reference prototype at `prototypes/attention-layer/` (runs end-to-end in mock mode) that validates the shape of the core modules. It is **reference, not the package** — Phase 0 work ports its logic into a real `jarvis` package deliberately, with tests.

### What's next

- Pick up the first `open` Phase 0 task in `TASKS.md` — **T-001 (Python project scaffold)** is the unblocked starting point; most other Phase 0 tasks depend on it.
- Two runtimes are deferred to spikes: ASR (mlx-whisper vs whisper.cpp) in Phase 1, Qwen2.5 size in Phase 2.
- If anything in the all-hands handoff-mesh audit was flagged PROACTIVE, address it before merging the first feature work.

### Open questions for the human

- API keys for live mode (Anthropic, ElevenLabs) are not set yet — only needed once Phase 4 (the engaged path) begins; mock mode covers Phases 0–3.
- `Start_Here/` is an untracked nested git repo (the bootstrap kit). Decide whether to submodule it or leave it out of version control.
