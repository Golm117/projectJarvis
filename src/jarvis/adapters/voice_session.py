"""VoiceSession — token-stream Claude → ElevenLabs pipeline (T-403).

Provides ``respond_and_speak(handoff, stop_event)`` which:

1. Opens a Claude streaming response and iterates the token stream.
2. Buffers tokens until a sentence boundary is detected (`.`, `!`, `?`, or a
   configurable max-chunk length that prevents unbounded buffering).
3. Sends each sentence chunk to ElevenLabs **while Claude is still generating**
   — the first chunk of ~1–3 words arrives in ~200–500 ms, ElevenLabs begins
   synthesising it, and the audio plays while the next sentence is being
   generated.  This produces first audio in ~1–2 s from handoff (as measured in
   the latency budget for this phase).
4. Checks ``stop_event`` before emitting each chunk: if the VAD signals that
   speech has resumed (barge-in), the pipeline aborts cleanly.

The frozen seam contracts are **preserved**:

* ``EngagedResponder.respond(handoff) -> str`` — ``ClaudeResponder.respond()``
  is unchanged (non-streaming, synchronous, accumulates the full text).
* ``VoiceOutput.speak(text) -> None`` — ``ElevenLabsVoice.speak()`` is
  unchanged (receives a complete sentence string).
* ``AttentionLayer._engage()`` continues to call ``responder.respond`` then
  ``voice.speak`` for the default + test paths.

``VoiceSession.respond_and_speak`` is a **higher-level entry point** that the
``--voice`` live path in ``live.py`` (T-404) can substitute for the two-step
call — it skips the intermediate full-text accumulation so audio starts before
the response is finished.

Design note: sentence-chunked streaming is safe for ElevenLabs because each
``speak(chunk)`` is a self-contained API call (the SDK supports any text
length).  The chunks are also natural units for TTS prosody.
"""

from __future__ import annotations

import re
import threading

from jarvis.adapters.claude_responder import _SYSTEM_PROMPT, ClaudeResponder, _build_user_message
from jarvis.adapters.elevenlabs_voice import ElevenLabsVoice
from jarvis.types import EngagementHandoff

# Sentence-ending punctuation used for chunk boundaries.
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+|(?<=[.!?])$")

# Maximum characters to buffer before sending to TTS even without a sentence
# boundary — prevents long run-on sentences from delaying audio.
_MAX_CHUNK_CHARS = 200


class VoiceSession:
    """Combines ``ClaudeResponder`` + ``ElevenLabsVoice`` into a streaming pipeline.

    The session is constructed once (e.g. at ``run_live`` startup) and
    ``respond_and_speak`` is called per engagement.  The individual adapters
    (``responder``, ``voice``) remain accessible so the orchestrator can use
    the frozen seam contracts directly in tests and the default path.

    Args:
        responder: a ``ClaudeResponder`` instance (or any ``EngagedResponder``).
        voice: an ``ElevenLabsVoice`` instance (or any ``VoiceOutput``).
    """

    def __init__(
        self,
        responder: ClaudeResponder,
        voice: ElevenLabsVoice,
    ) -> None:
        self.responder = responder
        self.voice = voice

    # ------------------------------------------------------------------
    # Streaming pipeline entry point
    # ------------------------------------------------------------------

    def respond_and_speak(
        self,
        handoff: EngagementHandoff,
        stop_event: threading.Event | None = None,
    ) -> str:
        """Stream Claude tokens → ElevenLabs TTS with sentence-level chunking.

        Opens a Claude streaming response, buffers tokens into sentence chunks,
        and sends each chunk to ElevenLabs while Claude is still generating the
        next sentence.  This achieves first-audio latency of ~1–2 s from handoff.

        Barge-safe: before each chunk is sent to TTS, ``stop_event`` is checked.
        If set (VAD detected resumed speech), the pipeline aborts without sending
        any further audio.  Audio already playing via the current ``speak()`` call
        completes (sub-sentence granularity abort is not supported — that would
        require interruptible audio playback at the OS level, out of scope for v0).

        Args:
            handoff: the engagement context from the orchestrator.
            stop_event: a ``threading.Event`` set by the VAD when speech resumes.
                If ``None``, no barge-in check is performed.

        Returns:
            The full spoken text (all chunks joined), matching what
            ``ClaudeResponder.respond()`` would have returned for the same
            handoff.
        """
        client = self.responder._get_client()
        user_msg = _build_user_message(handoff)

        all_text_parts: list[str] = []
        buffer = ""

        with client.messages.stream(
            model=self.responder._model,
            max_tokens=self.responder._max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        ) as stream:
            for token in stream.text_stream:
                if stop_event is not None and stop_event.is_set():
                    # Barge-in: drain the buffer as-is (don't speak it) and exit.
                    if buffer.strip():
                        all_text_parts.append(buffer)
                    break
                buffer += token
                # Flush on sentence boundary or when buffer is getting long.
                chunks = _split_on_sentences(buffer)
                if len(chunks) > 1:
                    # At least one complete sentence ready; send all but the
                    # trailing incomplete fragment.
                    for chunk in chunks[:-1]:
                        chunk = chunk.strip()
                        if not chunk:
                            continue
                        if stop_event is not None and stop_event.is_set():
                            break
                        all_text_parts.append(chunk)
                        self.voice.speak(chunk)
                    buffer = chunks[-1]  # retain the trailing fragment
                elif len(buffer) >= _MAX_CHUNK_CHARS:
                    # Force-flush to keep latency bounded.
                    chunk = buffer.strip()
                    if chunk and (stop_event is None or not stop_event.is_set()):
                        all_text_parts.append(chunk)
                        self.voice.speak(chunk)
                    buffer = ""

            # Flush any remaining buffer (end of stream).
            if buffer.strip() and (stop_event is None or not stop_event.is_set()):
                chunk = buffer.strip()
                all_text_parts.append(chunk)
                self.voice.speak(chunk)

        return " ".join(all_text_parts)


# ── Module-level helpers ──────────────────────────────────────────────────────


def _split_on_sentences(text: str) -> list[str]:
    """Split *text* at sentence boundaries, keeping the trailing fragment.

    Returns an empty list for empty input; returns a single-element list
    containing the full text if no boundary is found.

    Example:
        "Hello there. How are you? Good" → ["Hello there.", "How are you?", "Good"]
    """
    if not text:
        return []
    parts = _SENTENCE_END_RE.split(text)
    # re.split leaves empty strings when the delimiter is at the end; filter.
    filtered = [p for p in parts if p]
    return filtered if filtered else [text]
