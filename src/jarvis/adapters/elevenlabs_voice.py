"""ElevenLabsVoice — VoiceOutput backed by ElevenLabs streaming TTS (T-402).

Implements the frozen ``VoiceOutput`` Protocol
(``speak(text) -> None``) by streaming audio from the ElevenLabs API and
playing it back in real time via ``elevenlabs.play.stream``.

Design constraints
------------------
* **Lazy import:** ``from elevenlabs import ElevenLabs`` is deferred to the
  first call so ``uv run pytest`` and ``python -m jarvis`` (without ``--voice``)
  never load the ElevenLabs SDK — the test suite stays offline.
* **Injected client:** the ``ElevenLabs`` client is accepted via the constructor
  so unit tests can pass a mock without any network or key.
* **Key from env:** when no client is injected, reads ``ELEVENLABS_API_KEY``
  from the environment (populated by ``load_dotenv()`` at the live entry).
* **Streaming for first-audio latency:** ``text_to_speech.stream()`` returns an
  ``Iterator[bytes]`` that ElevenLabs begins yielding as soon as the first audio
  chunk is ready — we pipe it directly into ``elevenlabs.play.stream()`` so
  audio starts playing before the full TTS is generated. Target: first audio
  within ~1–2 s of the call.
* **Configurable voice + model:** ``voice_id`` and ``model_id`` are
  constructor-injected with sensible defaults (Rachel / eleven_multilingual_v2).
  The caller (T-404) can override these at startup.
* **No audio playback in tests:** the play callable is constructor-injected
  (default ``elevenlabs.play.stream``) so unit tests can pass a no-op to
  prevent any audio output.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from typing import Any

# ── Defaults ──────────────────────────────────────────────────────────────────
# Rachel — ElevenLabs' well-known high-quality English voice.
# Swappable via the voice_id constructor arg (T-404 will expose a CLI flag for
# voice choice, which is a human/product decision per the escalation protocol).
_DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"

# eleven_multilingual_v2 supports streaming and covers the English spoken-style
# register well; it's ElevenLabs' recommended streaming model as of 2026.
_DEFAULT_MODEL_ID = "eleven_multilingual_v2"


class ElevenLabsVoice:
    """``VoiceOutput`` that streams TTS from ElevenLabs and plays it locally.

    Satisfies the frozen ``VoiceOutput`` Protocol:
    ``speak(text: str) -> None``.

    Args:
        client: an ``elevenlabs.ElevenLabs`` instance (injected for testing).
            If ``None``, one is created lazily from ``ELEVENLABS_API_KEY`` on
            first call.
        voice_id: the ElevenLabs voice to use. Defaults to Rachel.
        model_id: the ElevenLabs TTS model. Defaults to
            ``eleven_multilingual_v2``.
        play: a callable that consumes an ``Iterator[bytes]`` and plays audio.
            Defaults to ``elevenlabs.play.stream`` (the real player). Inject a
            no-op (e.g. ``lambda _: None``) in unit tests to suppress audio.
    """

    def __init__(
        self,
        client: Any = None,
        *,
        voice_id: str = _DEFAULT_VOICE_ID,
        model_id: str = _DEFAULT_MODEL_ID,
        play: Callable[[Iterator[bytes]], Any] | None = None,
    ) -> None:
        self._client = client
        self._voice_id = voice_id
        self._model_id = model_id
        self._play = play  # None → lazily resolved to elevenlabs.play.stream

    # ------------------------------------------------------------------
    # VoiceOutput Protocol
    # ------------------------------------------------------------------

    def speak(self, text: str) -> None:
        """Stream TTS from ElevenLabs and play it locally.

        Calls ``text_to_speech.stream()`` which returns an ``Iterator[bytes]``
        that starts yielding chunks as soon as ElevenLabs produces them. We
        pipe this iterator directly into the play callable so audio begins
        before the full generation completes (streaming first-audio latency).

        Args:
            text: the spoken text to synthesise and play.
        """
        if not text:
            return
        client = self._get_client()
        audio_iter: Iterator[bytes] = client.text_to_speech.stream(
            self._voice_id,
            text=text,
            model_id=self._model_id,
        )
        play_fn = self._get_play()
        play_fn(audio_iter)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        """Return the injected client, or create one lazily from env."""
        if self._client is not None:
            return self._client
        # Lazy import — only reaches here when running live (--voice flag).
        from elevenlabs import ElevenLabs  # noqa: PLC0415

        api_key = os.environ.get("ELEVENLABS_API_KEY")
        self._client = ElevenLabs(api_key=api_key)
        return self._client

    def _get_play(self) -> Callable[[Iterator[bytes]], Any]:
        """Return the play callable, resolving the default lazily."""
        if self._play is not None:
            return self._play
        # Lazy import — only reaches here when running live (--voice flag).
        from elevenlabs.play import stream as el_stream  # noqa: PLC0415

        self._play = el_stream
        return self._play
