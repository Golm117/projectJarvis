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

## Current state — 2026-06-15 (T-001 scaffold done)

**Phase:** phase_0 — Foundations.

**T-001 (Python project scaffold) is DONE.** The real `jarvis` package now exists:
- **src-layout** `src/jarvis/` + `pyproject.toml` (`requires-python = ">=3.11"`, hatchling).
- **Toolchain: uv** — the machine had no 3.11 (system python is 3.9.6), so uv 0.11.21 was installed via its non-interactive standalone installer to `~/.local/bin` and pins **CPython 3.11.15**. `uv.lock` + `.python-version` are committed.
- **pytest** wired (2 smoke tests pass), **ruff** lint+format clean. `prototypes/` is excluded from ruff (it's reference, not the package).
- First deliverable shipped: **`docs/architecture/module-map.md`** — the seam contract (six core modules + the TranscriptSource / EngagedResponder / VoiceOutput adapter seams) the other agents implement against.
- Decision logged in `DECISIONS.md` (uv + src-layout + pytest + ruff).

**Running the toolchain:** `uv` lives at `~/.local/bin/uv` — either add it to PATH or `export PATH="$HOME/.local/bin:$PATH"`. Then `uv run pytest`, `uv run ruff check .`, `uv run ruff format .`.

The reference prototype at `prototypes/attention-layer/` still runs end-to-end in mock mode and validates the module shapes. It is **reference, not the package** — its logic is ported into `src/jarvis/` deliberately in T-002+ (e.g. RollingWindow must take an injected clock, unlike the prototype's internal `time.monotonic()`).

### What's next

- **T-002 (Core data types + RollingWindow) is the next unblocked task** (depends only on T-001). Freeze `Utterance` there; inject the clock into RollingWindow's time-bound. See the `## Next` section of the module map and `docs/architecture/working-notes.md` for the T-002 prep notes.
- T-003 and T-005 are also unblocked (depend only on T-001). T-009 (qa-tuning's clock+fakes harness) is unblocked too and should ideally land alongside/just after T-002 so the core tests share it.
- Two runtimes are deferred to spikes: ASR (mlx-whisper vs whisper.cpp) in Phase 1, Qwen2.5 size in Phase 2.

### Open questions for the human

- API keys for live mode (Anthropic, ElevenLabs) are not set yet — only needed once Phase 4 (the engaged path) begins; mock mode covers Phases 0–3.
- `Start_Here/` is still an untracked nested git repo (the bootstrap kit). Decide whether to submodule it or leave it out of version control. (Untouched this session.)
