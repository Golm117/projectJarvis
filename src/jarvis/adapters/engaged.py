"""Engaged-path seams + print stand-ins (T-008).

The **out** boundary: once the orchestrator decides Jarvis engages (either path),
it crosses into the engaged half — compose an answer, speak it. The core only
knows these two Protocols (module map §"The I/O adapter seams"); the real Claude
+ ElevenLabs adapters (voice-integration-engineer, T-401/T-402) drop in behind
them, and the tests use ``FakeResponder`` / ``FakeVoice`` (``tests/fakes.py``).

* ``EngagedResponder.respond(handoff) -> str`` — turns an ``EngagementHandoff``
  into the spoken-style line Jarvis says. Phase-0 stand-in: ``PrintResponder``
  (a canned, context-showing line, ported from the prototype's
  ``Backend.engaged_reply`` mock branch). Real: Claude ``claude-opus-4-8``.
* ``VoiceOutput.speak(text) -> None`` — emits the line. Phase-0 stand-in:
  ``PrintVoice`` (prints it). Real: ElevenLabs streamed TTS.

The print stand-ins exist for the **runnable demo** (``python -m jarvis``); the
tests use the recording fakes instead so they can assert on what crossed the
boundary. Both satisfy the same Protocols, so the orchestrator treats them
identically.
"""

from __future__ import annotations

from typing import Protocol

from jarvis.types import EngagementHandoff


class EngagedResponder(Protocol):
    """The engaged-answer seam — compose the line Jarvis says on engagement."""

    def respond(self, handoff: EngagementHandoff) -> str: ...


class VoiceOutput(Protocol):
    """The voice-output seam — emit a line of speech."""

    def speak(self, text: str) -> None: ...


class PrintResponder:
    """A canned ``EngagedResponder`` that shows it had the context — for the demo.

    Ported from the prototype's mock ``engaged_reply``: a brief greeting that
    references the living summary, proving Jarvis was following along. No model,
    no network. The real Claude responder (T-401) replaces it behind the seam.
    """

    def respond(self, handoff: EngagementHandoff) -> str:
        context = handoff.summary or "your conversation"
        return f"Yes? I've been following along — we were on: {context}"


class PrintVoice:
    """A ``VoiceOutput`` that prints instead of speaking — for the demo.

    Records nothing and produces no audio; it just writes the line to stdout so
    the runnable demo shows what Jarvis would say. The real ElevenLabs adapter
    (T-402) replaces it behind the seam.

    Args:
        prefix: printed before each spoken line (so the demo output reads as
            speech, e.g. ``"   🗣  "``).
    """

    def __init__(self, prefix: str = "") -> None:
        self._prefix = prefix

    def speak(self, text: str) -> None:
        print(f"{self._prefix}{text}")
