# Contributing to project-jarvis

This repo is worked on by three kinds of collaborator:

1. **You** (the human) — product owner, final decision-maker, architect of intent.
2. **Claude Code** — the CLI agent running locally with full file + terminal access. Orchestrates work and delegates to subagents.
3. **Specialized subagents** spawned by Claude Code — the project-specific roster defined in `.claude/agents/`. Each agent is purpose-built for this project's work; the roster was assembled by the grill skill from the task breakdown, not selected from a default menu:
   - `core-engineer` — the pure-logic attention core + dual-summon state machines + mock pipeline
   - `sensing-engineer` — always-on mic, Silero VAD, local ASR, the MicSource adapter
   - `local-ml-engineer` — Qwen2.5/MLX summarize() + detect_wall() backends
   - `voice-integration-engineer` — Claude spoken answer + ElevenLabs streamed voice
   - `qa-tuning` — test harness, interjection-precision eval, threshold calibration (mandatory reviewer of summon/timing/wall behavior)

Any web-chat Claude conversation is a fourth collaborator if used. Claude Code and web-chat Claude **do not share memory** — the repo (files + git history + these docs) is the only thing they have in common. This file exists to make that coordination work.

---

## Golden rules

1. **Git is the source of truth.** If it isn't committed, it doesn't exist to the other collaborator.
2. **Commit in small, focused chunks** with clear messages. One logical change per commit.
3. **Read before you write.** Every session starts by reading recent commits, `NOTES.md`, `TASKS.md`, and anything new in `DECISIONS.md`.
4. **Write down non-obvious choices.** If a decision wasn't forced by the code, it goes in `DECISIONS.md`.
5. **When in doubt, ask the human.** No AI should make irreversible calls (schema changes, dependency additions, deletions of user data paths) without confirmation.

---

## Documents and their purpose

| File | Purpose | Updated how often |
|---|---|---|
| `README.md` | Project front door | Rarely |
| `.pdr.md` | Project design reference (the grill output) | Rarely; on major scope shifts |
| `docs/reference-guide.md` | Expanded product thesis + methodology + roadmap | When phases change |
| `docs/prd/*` | The PRD sections this build derives from | When scope is re-litigated |
| `DECISIONS.md` | Append-only log of structural decisions | When decisions are made |
| `NOTES.md` | Informal session-to-session handoff scratchpad | Every session |
| `TASKS.md` | Shared structured task list for multi-agent coordination | Continuously during work |
| `OPERATING_PROTOCOL.md` | Memory model and checkpoint discipline | Rarely; if process changes |
| `.claude/agents/*.md` | Project-specific subagent definitions | When role scope changes |
| `docs/agents/*.md` | Self-introductions from the all-hands exercise | When all-hands re-runs |
| `docs/<domain>/*.md` | Per-agent domain working memory | Continuously |

`NOTES.md` is a free-form whiteboard. `TASKS.md` is the structured task system with atomic claiming via git commit — read its header for the full protocol.

---

## Session start protocol

**For Claude Code, at the start of a session:**

```bash
git status
git log --oneline -20
```

Then read, in order:
1. `NOTES.md` — what the last session was working on
2. `TASKS.md` — active tasks, blocked tasks, next up
3. Recent entries in `DECISIONS.md`
4. Any files mentioned in the last few commit messages

**For web-chat Claude (if used):**

When starting a new conversation, the human pastes or shares:
- The latest `NOTES.md`
- Relevant portion of `TASKS.md`
- Any new entries in `DECISIONS.md`
- The specific file(s) in question

Web-chat Claude cannot pull from the repo unless connected via filesystem; even then, ask it to look before it acts.

---

## Session end protocol

Before ending a session:

1. **Commit.** No trailing uncommitted work.
2. **Update `NOTES.md`** with: what was done, what's half-done, what's next, blockers.
3. **Update `TASKS.md`** — move statuses forward, add timestamps, write handoff notes.
4. **Add to `DECISIONS.md`** if structural decisions were made.
5. **Flag open questions** for the human if any remain.

See `OPERATING_PROTOCOL.md` for the full discipline including context-capacity awareness.

---

## Using subagents (Claude Code)

The roster lives in `.claude/agents/`. Each file has a `description` Claude Code uses to decide when to invoke it. The roster was assembled by the grill skill at project bootstrap based on the work this project actually requires — not a fixed canonical set.

**To understand a specific agent's scope and how to invoke it well:** read `docs/agents/<agent-name>.md` (the all-hands intro). Section 5 of every intro contains "How to ask me for work well" with good-prompt and bad-prompt examples. Use those as templates.

**Mandatory review:** `qa-tuning` must review before merge any change touching `TurnTakingGate`, `SummonController`, `WallDetector`, or the interjection thresholds — that behavior is the project's success metric.

**Parallel work via worktrees:**

When Claude Code identifies parallelizable work:

```bash
git worktree add ../project-jarvis-wt-<area> feature/<branch>
```

Dispatch a specialist to each worktree. Both agents read and write `TASKS.md` for coordination — they cannot overwrite each other because they're on different branches in different working directories.

**Rules of the road:**

- Worktree tree depth is shallow. Claude Code spawns subagents. Subagents do not spawn further subagents.
- Any task touching mandatory-reviewer triggers (see `qa-tuning`'s intro) routes through that reviewer before merge.
- Any non-trivial change routes through review before merge.

---

## Commit message format

Conventional Commits-lite:

```
<type>: <short description>

<optional longer explanation>
```

Types: `feat`, `fix`, `refactor`, `docs`, `chore`, `wip`.

If a commit is part of claiming a task in `TASKS.md`, start the message with `[T-###]`:
- `[T-014] chore: claim task`
- `[T-014] feat: initial implementation`
- `[T-014] docs: mark T-014 review`

---

## Branching

Default for solo + AI collaborators in early phases: **work on `main`**, commit often, move fast. Introduce feature branches when:
- The project has real users
- Risky refactors are in flight
- A second human joins
- Worktree-based parallel work needs its own branch

When the workflow shifts to PR-based, this file gets updated.

---

## Dependencies

The approved stack for this project is Python 3.11+, Anthropic Claude API, ElevenLabs, Silero VAD, local ASR, and Qwen2.5 via MLX. Anything outside that list requires human approval and at least one alternative being considered.

When installing a new dependency, note it in `DECISIONS.md` with date and one-line rationale.

---

## When things go sideways

- **Merge conflicts between worktrees:** stop, read both versions, route to the appropriate reviewer agent or ask the human.
- **A decision in `DECISIONS.md` seems wrong now:** don't overwrite it. Add a new entry that supersedes and references the old.
- **Something was deleted that shouldn't have been:** `git reflog` is your friend.
- **Two agents claimed the same task:** git-level conflict wins. First commit keeps it; the other re-reads `TASKS.md` and picks something else.
- **A task is stuck in `claimed` with no activity:** the owner's session likely ended without cleanup. Reclaim and note the orphan in `NOTES.md`.
