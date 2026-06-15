# Attention Layer — prototype

A runnable foundation for [PRD 01 — Conversation Initiation](../../docs/prd/01-conversation-initiation.md).
It implements the **platform-agnostic core** of the attention layer: the rolling
window, the delta-updated living summary, wall detection, both initiation paths,
and the engagement handoff.

The microphone is intentionally absent. Audio in/out is the hardware-abstraction
boundary (PRD NFR-5), so the core is fed from text via a `TranscriptSource`. A real
speech-to-text mic adapter slots in later by subclassing `TranscriptSource` — no
change to the core.

## Run it

No install needed for the default (MOCK) mode:

```bash
python3 attention_layer.py --demo          # scripted conversation
python3 attention_layer.py --demo --pace 1 # ...with a 1s beat between lines
python3 attention_layer.py                 # interactive: type "Speaker: text"
```

In the interactive mode, type lines like:

```
Alex: what was the name of that restaurant?
Sam: jarvis, remind me to call the dentist
```

- A line ending in `?`, or containing uncertainty ("I don't know", "what was…",
  "I wish…"), can trigger a **proactive spoken interjection** (Path B).
- A line containing the wake word **jarvis** triggers an **explicit summon**
  (Path A) and prints the engagement handoff.

## Two modes

| Mode | When | Summary + Wall Detection |
|------|------|--------------------------|
| **MOCK** | default — no key needed | local heuristics (keyword topic-shift, regex wall signals) |
| **LIVE** | `ANTHROPIC_API_KEY` set **and** `pip install -r requirements.txt` | produced by Claude |

The pipeline, data flow, and event boundaries are identical in both modes — only
the "brain" swaps. That's the point: you can develop and demo the whole attention
layer offline, then flip on the real model.

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python3 attention_layer.py --demo
```

## How it maps to the PRD

| PRD element | Where |
|-------------|-------|
| Rolling Transcription Window (FR-1) | `RollingWindow` (count- and time-bounded) |
| Living Summary + Delta-Update (FR-2) | `LivingSummary.consider_update` — re-summarizes **only** on a detected topic shift ("redraw the changed pixels") |
| Wall Detection (FR-3) | `Backend.detect_wall`, gated by `_has_wall_signal` so the brain isn't run every utterance |
| Path A — Summon (FR §4) | `AttentionLayer._is_summon` |
| Path B — Spoken Interjection + guardrails (FR-4) | `_maybe_interject` — confidence threshold (FR-4.3) + back-off (FR-4.5) |
| Engagement Handoff (FR-5) | `EngagementHandoff`, emitted at the boundary |
| On-device-first / low idle compute (NFR-1, NFR-3) | ambient work uses a cheap model; expensive summarize runs only on shift |
| Platform-agnostic core (NFR-5) | `TranscriptSource` is the only seam to hardware |

## Tunable knobs (the PRD's open questions, as config)

At the top of `attention_layer.py`:

- `TOPIC_SHIFT_THRESHOLD` — how different the conversation must get to re-summarize (OQ-3)
- `WALL_CONFIDENCE_TO_SPEAK` — precision/recall dial for interjecting (OQ-1, OQ-2)
- `WINDOW_MAX_UTTERANCES` / `WINDOW_MAX_SECONDS` — window length (OQ-5)
- `AMBIENT_MODEL` / `ENGAGED_MODEL` — cheap on-device stand-in vs. capable engaged model

## Not implemented (deferred, per the PRD)

- Real audio / STT (the mic adapter behind `TranscriptSource`)
- Barge-in / natural-boundary detection from audio (OQ-4)
- Anything past the Engagement Handoff (what Jarvis *does* once engaged)
- Hybrid local↔cloud summary sync (Phase-1 is local-only; the seam is the handoff)
