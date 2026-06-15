# PRD 01 — Conversation Initiation: The Attention Layer

> **Section status:** Draft
> **Part of:** [Project Jarvis PRD](README.md)
> **Covers:** the *start* of the user-interaction journey — how and when Jarvis
> begins paying attention, and how it decides to speak up.

---

## 1. Overview & Scope

This section specifies the **Attention Layer**: the part of Jarvis that decides
*when it is listening, when it understands enough to act, and when it should
initiate a conversation* — either because it was summoned or because it noticed it
could help.

Everything before "Jarvis is now engaged in a task" is in scope. Everything after
the moment of engagement (how it answers, what tools it calls, what it can do) is
**out of scope** for this section and belongs to later sections of the PRD.

**In scope**

- The rolling transcription window (short-lived active listening)
- The living summary and its delta-update model
- Wall detection (noticing the conversation needs help)
- The two ways a conversation gets initiated (summon vs. self-initiation)
- The handoff signal that marks "Jarvis is now engaged"
- Privacy, compute, and platform constraints for always-on listening

**Out of scope** (deferred to later PRD sections)

- What Jarvis does once engaged (reasoning, tool use, skills)
- Identity, personality, and voice of responses
- Memory across sessions / long-term knowledge
- Multi-user / speaker identification beyond what wall detection needs

---

## 2. Problem & Vision

### The problem with binary wake words

Today's assistants (the Meta / Google trigger-word model) run a tiny, low-power
loop that listens for one thing: a wake word. Until that word fires, the assistant
has **no context**. The moment it fires, the assistant starts from zero — it didn't
hear the conversation that led up to the request, so the user has to restate
everything.

This makes the assistant a *vending machine*: you walk up, insert a command, get one
output, walk away. It never participates.

### The vision

Jarvis (from Iron Man) is not a vending machine. It is *present*. It follows the
thread of what's happening, and it speaks up at the right moment — sometimes because
Tony called it, sometimes because it noticed something worth saying. That presence is
the whole point of this project.

We get there with **graduated attention** instead of a binary switch:

1. A short, continuously-running **rolling transcription window** keeps a sense of
   what's being discussed.
2. A **living summary** distills that window, updating only when the topic actually
   shifts — like a TV that only redraws the pixels that changed, not the whole frame.
3. Jarvis watches that summary for **walls** — an unanswered question, a stuck point,
   a factual gap — and offers to help, in addition to still answering when summoned.

The wake word does not go away. It stops being the *only* door in.

> **North star:** when Jarvis finally speaks, it already knows the backstory — so it
> never starts a conversation cold.

---

## 3. Concepts & Terminology

These terms are used verbatim across the rest of the PRD.

| Term | Definition |
|---|---|
| **Attention Layer** | The subsystem this section specifies. Decides when Jarvis listens, understands, and initiates. |
| **Rolling Transcription Window** | A short, continuously-running speech-to-text buffer covering the most recent span of conversation (a sliding window, not a permanent recording). |
| **Living Summary** | A continuously-maintained, compact summary of the current conversation, derived from the rolling window. |
| **Delta-Update** | The rule that the Living Summary is only recomputed when the conversation shifts to a new topic — not on every utterance. (The "only redraw changed pixels" model.) |
| **Topic Shift** | A detected change in what the conversation is about, which is what triggers a Delta-Update. |
| **Wall** | A moment where the conversation could use help: an unanswered question, a factual gap, a stuck/looping point, or an explicit ask into the air. |
| **Wall Detection** | The process that watches the Living Summary (and recent window) for Walls. |
| **Initiation Path** | One of the two ways a conversation with Jarvis begins (see §4). |
| **Engagement** | The state transition marking "Jarvis is now actively in a task." The output/boundary of this section. |
| **Engagement Handoff** | The signal + context package passed downstream when Engagement occurs. |

---

## 4. The Two Initiation Paths

A conversation with Jarvis can begin in two ways. Both are fed by the same Living
Summary, so Jarvis always has context regardless of how it was triggered.

### Path A — Explicit Summon (wake word)

The classic model, preserved. The user says the wake word ("Jarvis…") or otherwise
explicitly addresses the assistant. This always works and is the highest-precision
trigger. The difference from today: on summon, Jarvis is handed the Living Summary,
so it can answer in context instead of asking the user to repeat themselves.

### Path B — Proactive Self-Initiation (wall)

Jarvis detects a **Wall** in the ongoing conversation and **offers to help by
speaking up** (spoken interjection). This is the Iron-Man-like behavior: Jarvis
volunteers at the right moment.

Because spoken interjection interrupts a human conversation, Path B is governed by
guardrails (see §5.4). Path B is *additive* — it never replaces Path A, and the user
can always suppress it.

```
            ┌─────────────────────────┐
            │     Living Summary      │  ← always kept warm by the Attention Layer
            └───────────┬─────────────┘
                        │ feeds both paths
        ┌───────────────┴────────────────┐
        ▼                                 ▼
  Path A: Summon                    Path B: Wall
  (user says wake word)            (Jarvis detects a wall,
        │                            speaks up to offer help)
        └───────────────┬────────────────┘
                        ▼
                   ENGAGEMENT
            (handoff w/ summary + trigger reason)
```

---

## 5. Functional Requirements

### 5.1 Rolling Transcription Window

- **FR-1.1** The system SHALL maintain a continuously-running transcription of recent
  speech as a sliding window of bounded length (by time and/or token count).
- **FR-1.2** The window SHALL be bounded — old content ages out as new content
  arrives. It is not a permanent recording.
- **FR-1.3** Window length SHALL be configurable, with a sensible default (proposed:
  on the order of the last ~60–120 seconds of speech; to be tuned).
- **FR-1.4** Transcription SHALL run on-device in Phase 1 (see NFRs).

### 5.2 Living Summary (Delta-Update model)

- **FR-2.1** The system SHALL maintain a Living Summary of the current conversation.
- **FR-2.2** The summary SHALL be recomputed **only on a detected Topic Shift**, not
  on every utterance (the Delta-Update rule).
- **FR-2.3** A Topic Shift detector SHALL decide when the conversation has moved on
  enough to warrant a summary refresh. Between shifts, the summary is considered
  current and is cheaply appended to, not regenerated.
- **FR-2.4** The summary SHALL be compact enough to serve as immediate context for
  Engagement (so Jarvis never starts cold).
- **FR-2.5** The summary update SHALL be the primary mechanism that keeps idle
  compute low — expensive summarization happens at shift boundaries only.

### 5.3 Wall Detection

- **FR-3.1** The system SHALL evaluate the Living Summary (and recent window) for the
  presence of a **Wall**.
- **FR-3.2** Wall categories SHALL include at least: (a) an unanswered question, (b) a
  factual gap or uncertainty expressed aloud, (c) a stuck/looping point, (d) an
  explicit ask into the air ("I wonder…", "what was that…").
- **FR-3.3** Wall Detection SHALL produce a **confidence score**; only Walls above a
  configurable threshold proceed to Path B.
- **FR-3.4** Wall Detection SHOULD run at Topic-Shift boundaries and/or on a light
  cadence, not on every utterance, to respect the compute budget.

### 5.4 Proactive Spoken Interjection (Path B) — with guardrails

- **FR-4.1** On a high-confidence Wall, Jarvis SHALL offer help via **spoken
  interjection**.
- **FR-4.2** Jarvis SHALL only interject at a natural boundary (a pause / end of
  utterance), never mid-sentence. *(Guardrail: don't talk over people.)*
- **FR-4.3** Interjection SHALL require the Wall confidence to clear a configurable
  threshold. *(Guardrail: precision over recall — better to stay quiet than to be
  wrong.)*
- **FR-4.4** The user SHALL be able to suppress proactive interjection easily and
  immediately ("not now" / a quiet mode), and SHOULD be able to set quiet
  periods/contexts. *(Guardrail: the user is always in control.)*
- **FR-4.5** After a suppressed or ignored interjection, Jarvis SHALL back off (avoid
  repeating the same offer about the same Wall). *(Guardrail: no nagging.)*

### 5.5 Engagement Handoff

- **FR-5.1** When either path triggers, the system SHALL transition to **Engagement**
  and emit an **Engagement Handoff**.
- **FR-5.2** The handoff SHALL include at minimum: the Living Summary, the trigger
  reason (summon vs. wall + which wall), and the recent window excerpt.
- **FR-5.3** The handoff is the boundary of this section — what consumes it is defined
  by later PRD sections.

---

## 6. Non-Functional Requirements

### 6.1 Privacy (on-device first)

- **NFR-1.1** In Phase 1, rolling transcription and summarization SHALL run
  **on-device**. Audio SHALL NOT leave the device during ambient listening.
- **NFR-1.2** The cloud LLM SHALL be invoked **only on Engagement** (once Jarvis is
  actually helping), never for ambient listening, in Phase 1.
- **NFR-1.3** The rolling window SHALL be ephemeral by default (aged-out content is
  discarded, not persisted) unless the user explicitly opts into retention.

### 6.2 Forward-compatibility to Hybrid

- **NFR-2.1** The architecture SHALL place a clean boundary between *local ambient
  processing* and *cloud reasoning*, so a future **Hybrid** mode (local + periodic
  cloud summary sync) can be enabled without re-architecting. *(Per the agreed
  evolution: start fully local, grow to hybrid.)*
- **NFR-2.2** The local→cloud escalation point SHALL be a single, well-defined
  interface (the Engagement Handoff is the natural seam).

### 6.3 Compute, battery & thermal

- **NFR-3.1** Idle (ambient) operation SHALL minimize compute, battery, and heat —
  the Delta-Update model is the primary lever (no continuous summarization).
- **NFR-3.2** The most expensive operations (full summary regeneration, wall
  evaluation) SHALL be gated to Topic-Shift boundaries / light cadence, not per
  utterance.

### 6.4 Latency

- **NFR-4.1** On Engagement, Jarvis SHALL have the Living Summary already available
  (no cold-start summarization on the critical path).
- **NFR-4.2** Proactive interjection SHALL fire close enough to the Wall to feel
  relevant (timeliness is part of correctness for Path B).

### 6.5 Platform

- **NFR-5.1** The Attention Layer SHALL be a **platform-agnostic core** with a
  hardware-abstraction boundary for audio in / audio out / UI cues.
- **NFR-5.2** The reference target is an always-mic'd personal device (phone or
  wearable); no platform-specific assumptions SHALL leak into the core logic.

---

## 7. Architecture Sketch

A logical decomposition — implementation-agnostic, platform-agnostic core.

```
        Hardware Abstraction Boundary
   ┌──────────────────────────────────────────────┐
   │  Audio In   │   Audio Out   │   UI / Cues     │  ← platform adapters
   └──────┬──────────────┬───────────────┬─────────┘
          │              │               │
   ┌──────▼──────────────────────────────────────────────────┐
   │                    ATTENTION LAYER (core)                │
   │                                                          │
   │   ┌────────────────┐      ┌──────────────────────┐       │
   │   │  Rolling       │ ───▶ │  Topic-Shift          │      │
   │   │  Transcription │      │  Detector             │      │
   │   │  Window        │      └──────────┬────────────┘      │
   │   └────────────────┘                 │ on shift          │
   │            │                         ▼                   │
   │            │              ┌──────────────────────┐       │
   │            └────────────▶ │   Living Summary      │      │
   │                           │   (delta-updated)     │      │
   │                           └──────────┬────────────┘      │
   │                                      │                   │
   │            ┌─────────────────────────┼─────────────┐     │
   │            ▼                         ▼              │     │
   │   ┌────────────────┐      ┌──────────────────────┐ │     │
   │   │  Wake-Word /   │      │   Wall Detector       │ │     │
   │   │  Summon (A)    │      │   (B, w/ confidence)  │ │     │
   │   └───────┬────────┘      └──────────┬────────────┘ │     │
   │           └────────────┬─────────────┘              │     │
   │                        ▼                            │     │
   │                  ┌───────────┐                      │     │
   │                  │ ENGAGEMENT│ ── Engagement Handoff │────┼──▶ (later PRD sections:
   │                  └───────────┘   (summary + reason)  │     │     cloud reasoning, tools…)
   │                                                      │     │
   └──────────────────────────────────────────────────────────┘
                                            │
                  Phase 1: local only ──────┘
                  Hybrid (future): summary may sync to cloud at this seam
```

**Evolution path:** Phase 1 keeps everything left of the Engagement seam on-device.
Hybrid mode later allows the Living Summary to sync to the cloud on a cadence for
richer reasoning — enabled at the same seam, no core rewrite (NFR-2.1).

---

## 8. Design Tensions & Open Questions

These are known risks to resolve during design/build, not blockers for this section.

- **OQ-1 — Interjection intrusiveness.** Spoken interjection is the chosen modality,
  but it interrupts humans. The guardrails (§5.4) manage this; the open question is
  *tuning* — confidence threshold, back-off behavior, and quiet-context defaults.
- **OQ-2 — False-positive Walls.** What looks like an unanswered question may be
  rhetorical. Precision must be favored over recall (FR-3.3). Needs real-world tuning.
- **OQ-3 — Topic-Shift sensitivity.** Too sensitive → constant resummarization (defeats
  the compute goal). Too dull → stale summary. The threshold is a core tuning knob.
- **OQ-4 — Barge-in etiquette.** Detecting a "natural boundary" (FR-4.2) reliably from
  audio alone is non-trivial; may need pause-length + prosody heuristics.
- **OQ-5 — Window length vs. memory.** This section bounds the window deliberately;
  cross-conversation memory is a separate, later concern.
- **OQ-6 — Multi-speaker.** Wall detection in a group conversation may need basic
  speaker turn-taking awareness. Deferred unless required.

---

## 9. Out of Scope / Future Sections

The following are explicitly deferred to later sections of the PRD:

- **Post-engagement behavior** — reasoning, tool/skill invocation, what Jarvis can
  actually *do* once engaged.
- **Personality & voice** — how responses sound and feel.
- **Long-term memory** — knowledge that persists across conversations.
- **Hybrid & cloud reasoning details** — this section only guarantees the *seam*
  exists; the hybrid design is its own section.
- **Speaker identification / multi-user** — beyond the minimum wall detection needs.

---

*This is section 01 of a living PRD. Subsequent sections continue the user-interaction
journey from the Engagement Handoff onward.*
