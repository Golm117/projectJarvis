# PRD 02 — Jarvis v0 (MVP)

> **Section status:** Ready for build
> **Part of:** [Project Jarvis PRD](README.md)
> **Builds on:** [PRD 01 — Conversation Initiation](01-conversation-initiation.md) (terminology and the attention-layer concepts are reused verbatim)
> **Target:** desktop, Apple Silicon (M5 Mac)

This is the first buildable slice of Jarvis: a local, always-on desktop assistant
that ambiently follows a conversation and either answers when summoned or politely
offers help when it notices a wall — speaking back in a natural voice.

---

## Problem Statement

I want a personal Jarvis that is actually *present* in my day, not a vending machine
I have to walk up to and feed a command. Today's assistants only wake on a trigger
word, have no idea what was being discussed, and make me restate everything from
scratch. I also don't want a always-on assistant that ships my conversations to the
cloud just to listen. And when it does answer, I don't want it to read me a
dry encyclopedia paragraph — I want it to talk like a person who was following along.

## Solution

A local desktop program that listens through an always-on microphone, transcribes
the conversation on-device, and keeps a running understanding of what's being said —
all without touching the network. It can be engaged two ways: I say "Jarvis", or it
notices an unanswered question and politely offers to help. Only at the moment it
actually answers does it reach out (to Claude, for now) to compose a reply, which it
speaks back in a natural voice (ElevenLabs). The always-on listening stays on my
machine; the cloud is only involved when Jarvis is actively helping.

## User Stories

1. As a user, I want Jarvis to listen through an always-on microphone, so that I never have to press a button to start it.
2. As a user, I want all ambient listening and transcription to happen on my own machine, so that my everyday conversations are not streamed to the cloud.
3. As a user, I want Jarvis to skip processing silence, so that it stays light on my machine when no one is talking.
4. As a user, I want my speech transcribed into text continuously, so that Jarvis has something to reason over.
5. As a user, I want Jarvis to keep a short rolling memory of the recent conversation, so that it has context without recording everything forever.
6. As a user, I want Jarvis to maintain a running summary of what we're discussing, so that when it engages it already knows the backstory.
7. As a user, I want that summary to refresh only when the topic actually changes, so that my machine isn't constantly doing expensive work.
8. As a user, I want Jarvis to notice when a question goes unanswered or someone is unsure, so that it knows there's a moment it could help.
9. As a user, I want to summon Jarvis by name ("Jarvis…"), so that I can ask for help explicitly whenever I want.
10. As a user, I want Jarvis to respond quickly when I summon it by name, so that being addressed feels immediate.
11. As a user, I want Jarvis to *also* offer help on its own when a question hangs in the air, so that I don't always have to summon it.
12. As a user, I want Jarvis to wait for a clear pause before interjecting, so that it never talks over people mid-sentence.
13. As a user, I want Jarvis to hold back from interjecting unless it's confident, so that it isn't annoying with wrong guesses.
14. As a user, I want Jarvis to abandon an interjection if someone keeps talking, so that it yields the floor like a polite participant.
15. As a user, I want Jarvis to be more cautious about interjecting than about answering a direct summons, so that uninvited input is rare and welcome.
16. As a user, I want Jarvis, once engaged, to have the conversation summary in hand, so that its answer fits what we were actually talking about.
17. As a user, I want Jarvis's answer composed to sound like spoken conversation — short, direct, no preamble — so that it doesn't read me a wiki article.
18. As a user, I want Jarvis to ask a brief clarifying question when needed, so that it behaves like a real assistant rather than guessing.
19. As a user, I want Jarvis to reply in a natural-sounding voice, so that the interaction feels human.
20. As a user, I want Jarvis to start speaking as soon as the first part of its answer is ready, so that there's no long awkward pause.
21. As a user, I want Jarvis to return to quietly listening after it finishes, so that one interaction doesn't end the session.
22. As a user, I want to tune how eager Jarvis is to interject, so that I can dial it to my comfort.
23. As a user, I want to tune how long Jarvis waits before interjecting, so that I can match my own conversational pace.
24. As a developer, I want to run the whole pipeline from scripted text input without a microphone, so that I can develop and test the logic deterministically.
25. As a developer, I want the "when to speak" timing logic driven by a simulated clock in tests, so that I can verify it without real audio or waiting in real time.
26. As a developer, I want the cloud answer and voice steps to be swappable with fakes, so that tests don't depend on live APIs.
27. As a user, I want the rolling transcript to be ephemeral by default, so that nothing is persisted unless I choose to keep it.

## Implementation Decisions

### Architecture (one pipeline, two halves)

- **Ambient half — always on, 100% local, no network:** mic → VAD → ASR → rolling window → delta-updated summary → wall detection → summon arbitration.
- **Engaged half — only after a trigger, cloud "for now":** Engagement Handoff → Claude → spoken-style text → ElevenLabs → speaker → return to ambient.

### Modules

The system is decomposed into **deep, pure-logic core modules** behind a thin set of
I/O adapters. The hardware (microphone), the cloud LLM, and the voice service are all
boundaries — the core "attention" logic never touches them directly.

**Core (pure logic, no I/O):**

- `RollingWindow` — bounded sliding transcript, by utterance count and elapsed time. Interface: `add(utterance)`, `utterances()`, `transcript()`.
- `TopicShiftDetector` — decides whether the conversation has moved to a new topic (the trigger for a summary refresh). Pure function of current vs. summary-basis content.
- `LivingSummary` — holds the running summary; `consider_update(window) -> bool` re-summarizes **only** on a detected topic shift (the Delta-Update rule), returning whether it refreshed.
- `WallDetector` — `(transcript, summary) -> { is_wall, category, confidence, offer }`. Categories: `unanswered_question`, `factual_gap`, `stuck_point`, `explicit_ask`, `none`. Backend-swappable (local model vs. fake).
- `TurnTakingGate` — consumes VAD/clock events and reports `settled?`, `politeness_gap_elapsed?`, `speech_resumed?`. Encapsulates endpoint + gap + abort timing.
- `SummonController` — the dual-path state machine that turns gate + detector signals into either an `EngagementHandoff` (summon) or an `Interjection` offer.

**I/O boundaries (thin adapters; fakes for tests):**

- `TranscriptSource` — produces `Utterance` events. Adapters: `MicSource` (mic → Silero VAD → ASR), `ScriptedSource` (canned lines, for dev/tests).
- `EngagedResponder` — `EngagementHandoff -> spoken-style text` via Claude.
- `VoiceOutput` — `text -> streamed audio` via ElevenLabs.
- `AttentionLayer` — orchestrator that wires the above and emits the three events (`summary_update`, `interjection`, `engagement`).

### The asymmetric dual-summon decision (the heart of the MVP)

Jarvis can engage two ways, and they have **deliberately opposite timing profiles**,
because being summoned and barging in are socially different acts:

| | Path A — Summon ("Jarvis…") | Path B — Interjection (uninvited) |
|---|---|---|
| Who is addressed | Jarvis | the other speakers, not Jarvis |
| Behavior | respond **fast** | hang back, be **polite** |
| Endpoint gap | ~500–700 ms | **~2 s politeness gap** (confirm nobody else answers) |
| Confidence bar | low (it was summoned) | **high (~0.70+)** |
| If speech resumes | n/a | **abort** — yield the floor |

This asymmetry is grounded in turn-taking / endpointing research (LiveKit-style
VAD + semantic-completeness layering; RESPOND's tunable "turn-claim aggressiveness").
Defaults are intentionally conservative for Path B; the thresholds are config knobs to
be tuned on real captured conversations.

The completeness/question-intent judgment is folded into the `WallDetector` call
(rather than a separate end-of-turn model): VAD provides the *timing*, the local model
provides *wall + completeness + intent* in one shot.

`SummonController` state machine (from the prototype design — the decision-rich part):

```
LISTENING
  └─ wake word in utterance ──────────────────────────────► ENGAGE(reason="summon")        # Path A, immediate
  └─ gap ≥ settle(~700ms) ──► run WallDetector
        └─ is_wall && confidence ≥ THRESH ──► PENDING_INTERJECTION
PENDING_INTERJECTION
  └─ speech resumes ───────────────────────────────────────► LISTENING   (abort, yield floor)   # Path B guardrail
  └─ silence reaches politeness_gap(~2s) ──────────────────► OFFER(interjection)                # Path B fires
  └─ same wall already offered (back-off) ─────────────────► LISTENING                            # no nagging
```

### Models & services

- **ASR:** local, on-device (whisper.cpp / Metal-accelerated on Apple Silicon).
- **VAD:** Silero (local, tiny) — gates ASR and feeds `TurnTakingGate`.
- **Ambient reasoning** (summary + wall detection): **local Qwen2.5 via MLX** on the M5. Chosen because ambient work must be cheap and never leave the machine.
- **Engaged answer:** **Claude API** (`claude-opus-4-8`). All engaged queries route to Claude in the MVP.
- **Voice out:** **ElevenLabs**, streamed.
- **Wake word:** transcript keyword match ("Jarvis") — no separate wake-word model, since the transcript already exists.

### Response style contract (the "human, not wiki" requirement)

- Claude is system-prompted as Jarvis: answer **aloud as a person**, 1–3 sentences, conversational, no preamble, no "According to…", plain prose (no markdown/lists, since it is spoken), and ask a brief clarifying question when useful.
- Claude is given the **Living Summary + recent transcript + the request**, so the answer is grounded in the actual conversation rather than generic.
- Claude's output is **streamed** into ElevenLabs' streaming input so Jarvis starts speaking on the first sentence (latency target: first audio in ~1–2 s).

### Dev/degraded mode

- A **mock mode** runs the entire pipeline with local heuristics (no API key, no models) so the architecture and event flow can be exercised offline. Live mode swaps in the real ASR/model/Claude/ElevenLabs behind the same interfaces.

### Privacy

- The rolling transcript is **ephemeral by default** (aged-out content discarded). Cloud (Claude/ElevenLabs) is invoked **only** during the engaged half, never during ambient listening.

## Testing Decisions

**What makes a good test here:** exercise a module's *external behavior* through its
public interface, not its internals. The core modules are designed so this is possible
without audio hardware or live APIs — timing logic is driven by a **simulated clock**,
and model/cloud calls are replaced with **fakes** that return canned verdicts/answers.

**Modules to be unit-tested (the six pure-logic core modules):**

1. `RollingWindow` — eviction by count and by time; transcript rendering.
2. `TopicShiftDetector` — shift vs. no-shift across representative content changes.
3. `LivingSummary` — refreshes only on shift; no refresh below the cold-start minimum; uses the injected (fake) summarizer.
4. `WallDetector` — each wall category and the `none` case, via a fake backend; confidence surfaced.
5. `TurnTakingGate` — `settled`, `politeness_gap_elapsed`, and `speech_resumed` transitions under a simulated clock.
6. `SummonController` — Path A fires immediately on wake word; Path B fires only on (wall ∧ confidence ≥ threshold ∧ politeness gap) and **aborts on resumed speech**; back-off suppresses a repeated identical offer.

**I/O boundaries** (`TranscriptSource`, `EngagedResponder`, `VoiceOutput`) are **not**
unit-tested; they are exercised through the orchestrator with fakes. The `ScriptedSource`
adapter plus fake responder/voice make a full ambient→engage→respond run testable end
to end without a microphone or network.

**Prior art:** the existing attention-layer prototype already structures
`RollingWindow`, `LivingSummary`, `WallDetector`, and a backend seam this way and runs
end-to-end in mock mode — the tests formalize behavior the prototype demonstrated.

## Out of Scope

The following were discussed but are **deliberately excluded** from v0 to avoid scope
drift. They are recorded as future directions, not commitments:

- **Phone / mobile port** (Qwen2.5 on-device on a phone), and the always-on-mic OS restrictions that come with it.
- **Off-grid / fully-local operation** — v0 uses Claude and ElevenLabs in the cloud for the engaged half "for now."
- **Wearables / dedicated hardware / robotics** as the always-on device.
- **Fine-tuning** a model for conversation cues — v0 uses prompting only; the captured conversations are what a future fine-tune would train on.
- **Speaker diarization** / knowing *who* is speaking, and any direct-vs-third-party speaker distinction.
- **Full-duplex / end-to-end speech models** (e.g. Moshi-style simultaneous listen-think-speak).
- **Local TTS** (e.g. Piper) replacing ElevenLabs.
- **Routing trivial queries to the local model** instead of Claude ("answer locally if needed").
- **Persistent / cross-session memory** beyond the ephemeral rolling window.
- **Barge-in detection from raw audio prosody** beyond the VAD-gap + abort approximation.

## Further Notes

- The conservative Path B defaults (politeness gap, confidence threshold) are expected
  to need **calibration on real conversations** — the turn-taking research is unanimous
  that completion-vs-thinking-pause has no universal fixed threshold. Running v0
  produces exactly the data that calibration (and any future fine-tune) would use.
- The Engagement Handoff is the clean seam where a future Hybrid (periodic local→cloud
  summary sync) would attach, per PRD 01 — no v0 work required to keep that option open.
