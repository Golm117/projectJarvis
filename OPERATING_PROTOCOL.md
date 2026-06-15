# OPERATING_PROTOCOL

How agents and sessions handle memory, context, and handoffs.

**Consumed by:**
- Every agent template (`templates/agents/*.md`) — references this for the checkpoint section
- `BOOTSTRAP.md` — scaffolds the domain folder structure described here
- The orchestrator session — applies the session-level protocol
- The deployable folder's `README.md` — points new projects here for the operating model

**Version:** 0.1

---

## The four-layer memory model

| Layer | Lives in | Holds | Lifecycle | Read by |
|---|---|---|---|---|
| 1. Identity | `docs/agents/<agent-name>.md` (Phase A + Phase B intro) | Who the agent is, what it owns, handoffs, prompt templates | Stable; updated only when role scope changes | Every invocation of that agent |
| 2. Domain working memory | `docs/<domain>/<topic>.md` (e.g., `docs/security/threat-model.md`, `docs/architecture/data-model.md`) | Accumulated expert knowledge scoped to the work, not the worker | Grows as work in that domain advances; pruned when superseded | Only when an agent's task touches that domain |
| 3. Shared coordination | `TASKS.md`, `NOTES.md`, `DECISIONS.md` | Cross-agent state (active tasks, session handoff, permanent decisions) | TASKS.md updates continuously; NOTES.md every session; DECISIONS.md on every structural call | Every session, by every agent and the orchestrator |
| 4. Session checkpoint | `NOTES.md` (session-end summary) + git commits | What was done, what's half-done, what's next | Overwritten every session — it's a whiteboard, not a log | First thing the next session reads |

**The principle that makes this work:** memory is bound to the *work*, not the *worker*. The same `docs/security/threat-model.md` is read by the security agent on Tuesday and by the qa agent on Friday when reviewing a security-sensitive diff. Tying memory to the agent identity instead would mean qa has to ask security what it knows. Bound to the work, qa just reads the file.

---

## Per-domain working memory convention

Every domain folder under `docs/` gets a structure like:

```
docs/<domain>/
├── README.md            ← what this folder is for, index of files
├── <topic-1>.md         ← long-lived expert knowledge on a topic
├── <topic-2>.md
└── working-notes.md     ← scratchpad for in-flight work that isn't ready for a topic file yet
```

**Domain folder naming** is derived per-project from each agent's `domain_folder` field in the agent roster (Section 6 of `.pdr.md`). The grill proposes a sensible default per agent (e.g., a `security` agent → `docs/security/`, a `firmware-engineer` → `docs/firmware/`, a `data-scientist` → `docs/data-science/`); the user can override. BOOTSTRAP seeds the folder structure once the roster is approved.

**Illustrative examples** (not a fixed template — actual folders depend on the roster the grill builds):

| If the roster includes... | Likely folder | Typical files |
|---|---|---|
| security | `docs/security/` | `threat-model.md`, access-control review log, secrets handling, dependency audits |
| systems-architect | `docs/architecture/` | data model, module boundaries, API contracts |
| researcher | `docs/research/` | one file per memo, dated |
| marketing | `docs/marketing/` | audience research, positioning, briefs |
| copywriter | `docs/copy/` | voice doc, per-surface copy files |
| integration-engineer | `docs/integrations/` | one file per external system |
| qa | `docs/operations/` | release checklist, incident runbook, known-issue tracking |
| (a project-specific role like `bot-protocol-engineer`) | `docs/bot-protocol/` | gateway events, slash command spec, rate-limit policy |

The convention is *one folder per agent's domain*, named after the work, not the worker — so `docs/security/` not `docs/security-engineer/`. Multiple agents may share a folder if their work overlaps cleanly (e.g., systems-architect and software-engineer both contributing to `docs/architecture/`); otherwise one agent owns one folder.

**Topic-file lifecycle:**
1. Born when an agent finishes work that produces durable findings
2. Updated when later work refines or extends those findings
3. Archived (moved to `docs/<domain>/archive/`) when superseded — never deleted, since `DECISIONS.md` may reference the historical version
4. Read at the start of any task touching that domain

**Working-notes.md vs topic files:**
- `working-notes.md` is a scratchpad — what's in flight, hypotheses, half-formed observations
- Topic files are durable — facts, decisions, tested findings
- The transition from working-notes → topic file happens at task-completion checkpoint (see below)

---

## Subagent checkpoint protocol (end of every invocation)

Every agent template's coordination section contains this rule, parameterized per role:

> **Before you return:**
> 1. Write durable findings to `docs/<your-domain>/<topic>.md`. If the file doesn't exist, create it. If it does, append or refine — don't overwrite without reason.
> 2. Update the TASKS.md entry you worked on: status, progress note, and a one-line handoff for the next agent.
> 3. If you discovered something the next session needs to know that isn't a permanent decision, add it to `NOTES.md`.
> 4. If you made a structural decision, write it to `DECISIONS.md` (with rationale and alternatives).
> 5. Return a summary that the orchestrator can act on — not the full reasoning trace. The trace lives in the files you just wrote.

**The discipline this enforces:** compression happens at the task boundary, where the agent has the most clarity about what's worth keeping. Trying to compress mid-work is unreliable; compressing at the end is mechanical.

---

## Orchestrator session protocol (start and end of every session)

**Session start:**
1. `git status` and `git log --oneline -20` — what's the repo state, what landed recently
2. Read `NOTES.md` — what was the last session doing, what's half-done
3. Read active entries in `TASKS.md` — claimed tasks (someone else is on it), blocked tasks (might have cleared)
4. Skim recent entries in `DECISIONS.md` — anything new since last session
5. Read any files mentioned in the last few commit messages
6. Then start work

**Session end (or when approaching context capacity):**
1. Commit all in-flight work (no trailing uncommitted changes)
2. Update `NOTES.md` — what was done, what's half-done, what's next, blockers, open questions for the human
3. Update `TASKS.md` — move statuses forward, add timestamps, write handoff notes
4. Add to `DECISIONS.md` if structural decisions were made
5. Flag anything that needs human input
6. If the next session should hard-restart context (because this one is bloated), say so explicitly in NOTES.md

**Context capacity awareness for the orchestrator:**
- Optimal range is roughly 100k–150k tokens. Past that, retrieval and recall degrade.
- The orchestrator can't see its own token count exactly, but it can sense bloat (long tool result chains, accumulated file reads).
- When sensing bloat: do an explicit checkpoint, then either (a) continue if work fits in remaining capacity, or (b) tell the user "I'd like to checkpoint and start fresh."
- **Never** treat context capacity as a runtime emergency. Treat it as a planned-for boundary.

---

## How to keep the orchestrator's context light

Six habits, in priority order:

1. **Delegate research and exploration to subagents.** They start fresh, do the heavy reads, return a summary. The bulk of token-spend dies with the subagent context.
2. **Don't load whole docs into the orchestrator if a subagent can summarize.** "Read `docs/security/threat-model.md` and tell me whether the new RLS policy on `leads` aligns with it" beats "load the threat model into my context so I can decide myself."
3. **Use file paths in conversation, not file contents.** When referencing prior work, link to `docs/architecture/data-model.md:42`, don't quote the file inline.
4. **Compose subagent prompts so the return message is small.** "Report under 200 words" matters.
5. **Keep TASKS.md and NOTES.md tight.** They're loaded every session start. Bloated coordination files = bloated startup context. Prune completed tasks to a `done/` archive when the active list grows long.
6. **Hard-restart deliberately, not reactively.** End-of-session checkpoint → new session → fresh context. The files do the remembering.

---

## What this protocol does NOT include

Three explicit non-goals — calling them out so they don't get reintroduced later:

1. **No per-agent persistent memory file.** Agents don't carry state between invocations. The work-memory layer (per-domain) replaces this.
2. **No automatic compression at a token threshold during runtime.** Subagents can't observe their own token count, and the orchestrator's compaction is handled by Claude Code, not by us. Compression is a *behavior at boundaries*, not a runtime alarm.
3. **No global "agent memory index."** No file lists "what each agent has remembered." Memory is the union of all domain files; if you want to know what's been documented in security, read `docs/security/`.

---

## Anti-patterns

- **Per-agent state files** (`docs/agents/<name>/state.md`) — drifts toward bloat, ties memory to worker not work, fights the subagent fresh-context architecture.
- **Inline accumulating logs** — appending forever to a single `working-notes.md` without ever promoting findings to topic files. Read cost grows linearly forever.
- **Reading all of `docs/` into the orchestrator at session start** — exactly the bloat the protocol is designed to avoid. Read TASKS, NOTES, recent DECISIONS, and the specific files relevant to the active task. That's it.
- **Subagents that re-derive what's already in a domain file** — wasted work and risk of contradicting prior findings. Every agent prompt must list which domain files it should read first.
- **Treating NOTES.md as a log** — it's a whiteboard. Overwrite freely. Permanent things go in DECISIONS.md.

---

## What BOOTSTRAP scaffolds from this protocol

When BOOTSTRAP runs, it produces (one entry per agent in the project's grill-derived roster):

1. The `docs/<domain>/` folder for each agent, with a seed `README.md`
2. An empty `working-notes.md` placeholder per domain
3. The checkpoint protocol section embedded in every agent file (scaffolded from `templates/agent-blank.md`)
4. The orchestrator session-protocol section in the project's `CONTRIBUTING.md`
5. A pointer in the project README to `OPERATING_PROTOCOL.md` (the project gets its own copy, since this protocol may evolve per project)
