# Voice Response Contract

This document defines: (1) the spoken-style system prompt that governs how Claude answers when Jarvis engages; (2) the Claude-to-ElevenLabs streaming design that achieves first audio within ~2 s.

## The spoken-style system prompt

```
You are Jarvis, an always-on desktop assistant who has been listening to the
conversation. When you speak, you respond like a competent peer who was in the
room — direct, brief, grounded in what was just said.

Rules you MUST follow:
- Answer in 1 to 3 sentences. Never more.
- No preamble. Do not start with "Sure", "Of course", "Great question",
  "Based on the conversation", "According to", or any similar phrase.
- Plain prose only. No markdown, no bullet points, no headers, no lists.
- Spoken aloud — write as you would say it, not as you would write it.
- If you don't know something, say so briefly in one sentence.
- Ask a clarifying question only if genuinely necessary; otherwise just answer.
```

### What each rule enforces

- **1–3 sentences**: Jarvis is interjecting into a live conversation. Three sentences is the maximum before it becomes a lecture. One sentence is often right.
- **No preamble**: Preamble is the verbal equivalent of clearing your throat. A peer does not say "Great question — according to my knowledge..." before answering. They answer.
- **Plain prose**: Markdown does not render in audio. Bullet points produce a stilted rhythm when synthesised. The text must be speakable as-is.
- **Spoken aloud**: Contractions, direct address, natural clause length. Not the register of a written API response.
- **No fabrication hedge**: "If you don't know, say so briefly" — keeps the one-sentence limit even on uncertainty.
- **No unnecessary clarifying questions**: Jarvis has the conversation context. It should answer from that context, not deflect.

### Voice register (from .pdr.md §voice_register)

> "Spoken and conversational — like a competent peer who was listening. 1–3 sentences, no preamble, no 'According to…', never encyclopedic/wiki, plain prose."

### Example responses

Trigger: summon — "Jarvis, what is useEffect?"
> "useEffect lets you run side effects in a function component — things like fetching data or setting up subscriptions after render. You pass it a function and a dependency array, and it reruns whenever those dependencies change."

Trigger: wall:unanswered_question — "What was the conference date again?"
> "I can find that — want me to look it up?"

Trigger: wall:factual_gap — "I don't remember the exact formula"
> "It's standard deviation divided by the square root of n — that's the standard error."

---

## Claude-to-ElevenLabs streaming design

### Goal

First audio within ~2 s of the engagement handoff. The full response should not need to be generated before audio begins.

### Architecture

```
EngagementHandoff
       |
       v
  ClaudeResponder._get_client()
       |
       v  client.messages.stream(...)
  Anthropic SDK — token stream
       |
  stream.text_stream — Iterator[str]
       |
  VoiceSession — sentence buffering
       |  _SENTENCE_END_RE splits on [.!?]
       |  _MAX_CHUNK_CHARS=200 force-flush
       |
       v  per-sentence chunk
  ElevenLabsVoice.speak(chunk)
       |
       v  client.text_to_speech.stream(voice_id, text=chunk, model_id=...)
  ElevenLabs API — audio Iterator[bytes]
       |
       v  elevenlabs.play.stream(audio_iter)
  mpv — real-time audio playback
```

### Sentence-chunked streaming

Tokens from Claude are buffered until a sentence boundary (`.`, `?`, `!`). The first sentence is typically 10–25 words, arriving in ~1–2 s. As soon as the first sentence boundary is detected, that chunk is sent to ElevenLabs — ElevenLabs begins generating audio immediately, and `mpv` starts playing before ElevenLabs has even finished generating it (the SDK streams the audio bytes too).

This means:
- **First audio latency**: ~2 s from handoff (Claude TTFT ~1 s + ElevenLabs TTS TTFA ~0.5–1 s)
- **Subsequent sentences**: play while Claude generates the next one
- **No full-text intermediate**: the text does not need to be complete before audio starts

Force-flush at `_MAX_CHUNK_CHARS=200` prevents a pathological long sentence from holding audio hostage.

### Barge-in safety

A `stop_event: threading.Event` is checked before each sentence chunk is sent to ElevenLabs. If the VAD signals that speech has resumed (the user started talking), the pipeline aborts cleanly without sending further chunks. Audio already playing for the current chunk completes (sub-sentence abort requires OS-level audio interruption, out of scope for v0).

### Frozen seam contracts

`VoiceSession.respond_and_speak()` is an internal higher-level method. The frozen seam contracts are preserved:

- `EngagedResponder.respond(handoff) -> str` — `ClaudeResponder.respond()` is unchanged (non-streaming, synchronous, full-text)
- `VoiceOutput.speak(text) -> None` — `ElevenLabsVoice.speak()` is unchanged (complete text input)
- `AttentionLayer._engage()` — unchanged; the streaming path is activated by injecting `VoiceSession` as the responder and `_SilentVoice` as the voice in `live.py`

When `VoiceSession` is used as the `EngagedResponder`, its `respond(handoff) -> str` method calls `respond_and_speak()` internally (both streaming and TTS happen inside `respond()`), and the returned string is passed to `_SilentVoice.speak()` which is a no-op.

### Live-measured latency (M5 Pro, 2026-06-16)

- First audio latency: **2.14 s** (Claude TTFT ~1.8 s + ElevenLabs chunk send ~0.3 s)
- Response quality (T-404 live run): 2 sentences, plain prose, correct register, no preamble
- Total time (2-sentence response, full TTS playback): ~20 s

### Configuration

- Model: `claude-opus-4-8` (approved stack, per `.pdr.md`)
- `max_tokens`: 120 (well above 1–3 spoken sentences; keeps latency tight)
- `thinking`: omitted (defaults off on Opus 4.8 — correct for short spoken replies)
- ElevenLabs voice: Rachel (`21m00Tcm4TlvDq8ikWAM`) — default; swappable via `ElevenLabsVoice(voice_id=...)` at startup (human/product decision)
- ElevenLabs model: `eleven_multilingual_v2` — streaming-capable, natural English prosody
- Audio player: `mpv` (required by `elevenlabs.play.stream` on macOS)

---

## Files

- `src/jarvis/adapters/claude_responder.py` — `ClaudeResponder` (T-401)
- `src/jarvis/adapters/elevenlabs_voice.py` — `ElevenLabsVoice` (T-402)
- `src/jarvis/adapters/voice_session.py` — `VoiceSession` streaming pipeline (T-403)
- `src/jarvis/live.py` — `_build_voice_session()`, `--voice` wiring (T-404)
- `src/jarvis/__main__.py` — `--voice` / `--real-voice` flags (T-404)
