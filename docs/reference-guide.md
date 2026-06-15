# project-jarvis — Reference guide

This document is the expanded narrative version of `.pdr.md`. The PDR is the structured contract; this is the human-readable explanation of *what we're building*, *why*, *for whom*, and *in what order*.

If something in this document conflicts with `.pdr.md`, the PDR wins (it's the authoritative source for machine consumers like the agent roster). Update both together when changing direction.

---

## Part 1 — The product

### One-line pitch

A local, always-on desktop assistant that follows your conversation and answers when summoned — or offers help when it hears an unanswered question — speaking back in a natural voice.

### The wedge

A single power user goes from *summon-and-re-explain* to *Jarvis already has the context and offers help within ~2 seconds of an unanswered question* — with no ambient audio ever leaving the device.

### Why this matters

The narrowest, sharpest version of the value claim above is what every Phase 0 design decision should be tested against. If a design choice doesn't move the wedge, it's probably premature.

### Success metric

Interjection precision: of the times Jarvis proactively offers help, the share that land at a genuinely useful, well-timed moment is high enough to keep the feature enabled (starting target ≥70% useful, with false interjections rare — to be calibrated on captured conversations).

This is the primary signal we'll use to decide whether the product is working — not the only metric we'll watch, but the one that decides "is this thing earning its existence."

---

## Part 2 — The audience

### Target user

Me — a technical builder who wants a present, Jarvis-like assistant on my own desktop and is comfortable running local models. Single user (the developer) for v0.

### What they do today (alternatives)

- Trigger-word assistants (Siri/Alexa/Google) that wake cold with no context
- Manually opening Claude/ChatGPT and re-typing the question
- Looking it up myself / living with the friction

Each of these is a real competitor for the user's attention. Designs that ignore them produce "another assistant" rather than "the better answer."

### Voice and register

Spoken and conversational — like a competent peer who was listening. 1–3 sentences, no preamble, no "According to…", never encyclopedic/wiki, plain prose (it is spoken aloud).

Every user-facing surface — here, primarily Jarvis's spoken responses — speaks in this register. When in doubt, read it aloud. If it sounds wrong, rewrite.

---

## Part 3 — The methodology

### Conceptual frame

Graduated attention beyond the binary wake word, combined with turn-taking / endpointing theory: transition-relevance places, VAD + semantic-completeness endpointing, and an asymmetric summon-vs-interjection timing model. The conversation understanding is a delta-updated living summary that "redraws only the changed pixels."

**Source:** Conversation Analysis turn-taking (Sacks/Schegloff/Jefferson); LiveKit end-of-turn detection; RESPOND (arXiv 2603.21682); full-duplex survey (arXiv 2509.14515); internal PRD 01 & 02.

### How the methodology shapes the product

The frame is not decorative. It dictates:
- The domain vocabulary (see Part 4)
- The shape of the attention pipeline (window → delta-summary → wall detection → dual-summon)
- The order of phases (see Part 6)
- What "good" looks like — particularly the asymmetric timing of summon vs. interjection

When a design proposal departs from the methodology without naming why, push back. Deviations belong in `DECISIONS.md` with rationale.

---

## Part 4 — Scope

### Domain entities

The core nouns of this product:

- **utterance** — a transcribed chunk of speech (speaker, text, timestamp)
- **rolling-window** — bounded sliding buffer of the most recent utterances
- **living-summary** — continuously maintained, delta-updated summary of the current conversation
- **wall** — a moment the conversation could use help (unanswered question, factual gap, stuck point, explicit ask)
- **engagement-handoff** — the context package passed downstream when Jarvis engages
- **interjection** — a proactive offer to help on a detected wall

These are the things the system creates, reads, and reasons about. Module boundaries and interfaces are organized around these entities.

### PII entities

**utterance, rolling-window, living-summary** — they hold spoken conversation content, potentially about third parties. Handling: kept ephemeral and on-device by default; never sent to the cloud during ambient listening; never persisted without explicit opt-in. `qa-tuning` and the human gate any change that would weaken this.

### Surfaces

What the user actually interacts with: a **local always-on desktop voice process** (`cli`). No web, dashboard, or marketing surface in v0 — which is why the roster carries no UI/marketing/copy agents.

---

## Part 5 — Constraints

### Approved stack

- **Python 3.11+** — orchestration language for the whole pipeline
- **Anthropic Claude API (`claude-opus-4-8`)** — engaged-path answer generation (spoken-style, grounded)
- **ElevenLabs** — streamed text-to-speech voice output
- **Silero VAD** — voice-activity detection / silence gating
- **Local ASR** (mlx-whisper or whisper.cpp — selected in Phase 1 spike) — on-device speech-to-text
- **Qwen2.5 via MLX** (size selected in Phase 2 spike) — local ambient reasoning (summary + wall detection)

Anything outside this list requires human approval and at least one alternative being considered. The dependency policy is in `CONTRIBUTING.md`.

### Compliance regime

None — personal, single-user, on-device, ephemeral tool. The privacy posture is enforced through the hard-nos rather than a regulatory framework. See `DECISIONS.md`.

### Hard nos

Things this product will explicitly never do:

- Never send ambient audio or transcripts to the cloud during listening — the cloud is touched only at the moment of answering.
- Never persist the transcript by default — it is ephemeral; retention only on explicit opt-in.
- Never interject mid-sentence or below the confidence threshold; always abort an interjection if speech resumes.
- Never let v0 drift into out-of-scope items (phone port, off-grid, wearables, fine-tuning, diarization, full-duplex, local TTS, local-query routing, cross-session memory).

Every agent treats these as boundaries. If a design proposal pushes against a hard no, it's a human-level decision to revisit, not an agent-level call.

---

## Part 6 — Phased roadmap

### phase_0: Foundations

**Goal:** Python scaffold, the six deep core modules with unit tests, and an end-to-end MOCK pipeline running green.

### phase_1: Real ears

**Goal:** Always-on mic + Silero VAD + local ASR producing a live transcript on the M5 (ASR runtime selected via spike).

### phase_2: Local understanding

**Goal:** Living Summary and Wall Detection backed by a local Qwen2.5 (MLX) model, replacing the mock backend.

### phase_3: Knowing when to speak

**Goal:** TurnTakingGate + SummonController wired to real VAD timing — fast wake-word summon, conservative polite interjection, abort-on-resume.

### phase_4: The voice

**Goal:** Engaged path — Claude composes a spoken-style grounded answer streamed into ElevenLabs voice output.

### phase_5: Make it live & tune

**Goal:** Full always-on loop on the M5; tune interjection thresholds against the precision metric on captured conversations.

Tasks for the current phase live in `TASKS.md`. That file is the source of truth for *what's being worked on right now*; this section is the source of truth for *what each phase is for*.

---

## Part 7 — Operating model

This project uses a specific operating model for agent collaboration:

- **Agent roster** — purpose-built per project from the work, not selected from defaults. See `docs/agents/README.md` for the self-introductions and handoff-mesh audit.
- **Memory model** — four layers (identity, domain working memory, shared coordination, session checkpoint). See `OPERATING_PROTOCOL.md`.
- **Checkpoint discipline** — agents write durable findings to their domain folder before returning. The orchestrator checkpoints at session boundaries.
- **Coordination** — `TASKS.md` is the shared task list with atomic git-claim. `NOTES.md` is the session-to-session whiteboard. `DECISIONS.md` is the append-only ADR log.

Read `CONTRIBUTING.md` for the practical day-to-day collaboration protocol.
