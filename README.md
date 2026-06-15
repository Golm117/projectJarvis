# project-jarvis

A local, always-on desktop assistant that follows your conversation and answers when summoned — or offers help when it hears an unanswered question — speaking back in a natural voice.

> **The wedge:** A single power user goes from *summon-and-re-explain* to *Jarvis already has the context and offers help within ~2 seconds of an unanswered question* — with no ambient audio ever leaving the device.

## Project documentation

Start here before writing any code:

- **[`.pdr.md`](./.pdr.md)** — the project design reference. Source of truth for *what* we're building and *why*.
- **[`docs/reference-guide.md`](docs/reference-guide.md)** — expanded product thesis, methodology, and phased roadmap.
- **[`docs/prd/`](docs/prd/)** — the PRD sections (01 attention layer, 02 the locked v0 MVP) that fed this bootstrap.
- **[`DECISIONS.md`](DECISIONS.md)** — append-only log of architectural and product decisions. Read this before proposing anything structural.
- **[`CONTRIBUTING.md`](CONTRIBUTING.md)** — how humans, Claude Code, and any web-chat Claude collaborator coordinate on this repo.
- **[`OPERATING_PROTOCOL.md`](OPERATING_PROTOCOL.md)** — memory model, checkpoint discipline, session protocol.
- **[`NOTES.md`](NOTES.md)** — informal session-to-session handoff notes. Scratchpad, not spec.
- **[`TASKS.md`](TASKS.md)** — shared structured task list with atomic git-claim protocol.
- **[`docs/agents/README.md`](docs/agents/README.md)** — the project-specific agent roster with self-written intros and the handoff-mesh audit. Read this before delegating work to a subagent.

## Stack

Approved tools for this project: Python 3.11+ · Anthropic Claude API (`claude-opus-4-8`) · ElevenLabs · Silero VAD · local ASR (mlx-whisper or whisper.cpp — selected in Phase 1 spike) · Qwen2.5 via MLX (size selected in Phase 2 spike).

Anything beyond this requires human approval with at least one alternative considered. See `CONTRIBUTING.md` for the dependency policy.

## Status

**Current phase:** phase_0 — Foundations.

See `docs/reference-guide.md` for the full phased roadmap.

## Getting started (local dev)

_To be filled in once Phase 0 scaffolding is done._

```bash
# placeholder
```

## License

_TBD._
