"""Tests for ElevenLabsVoice (T-402).

Design: the ElevenLabs client AND the play callable are injected via the
constructor so no real network call and no real audio playback occurs. The
lazy ``from elevenlabs import ElevenLabs`` path is NOT triggered by any test
here — the injected mock replaces it entirely.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from unittest.mock import MagicMock

from jarvis.adapters.elevenlabs_voice import (
    _DEFAULT_MODEL_ID,
    _DEFAULT_VOICE_ID,
    ElevenLabsVoice,
)

# ── Test helpers / fixtures ────────────────────────────────────────────────────


def _make_audio_iter(chunks: list[bytes] | None = None) -> list[bytes]:
    """Return a list of bytes chunks (used as a fake Iterator[bytes])."""
    return chunks if chunks is not None else [b"chunk1", b"chunk2"]


def _make_mock_client(
    audio_chunks: list[bytes] | None = None,
) -> MagicMock:
    """Return a mock ElevenLabs client whose tts.stream() returns an iterator."""
    chunks = _make_audio_iter(audio_chunks)
    mock_tts = MagicMock()
    mock_tts.stream.return_value = iter(chunks)
    mock_client = MagicMock()
    mock_client.text_to_speech = mock_tts
    return mock_client


def _no_op_play(audio_iter: Iterator[bytes]) -> None:
    """Silence the audio — consume the iterator without playing anything."""
    for _ in audio_iter:
        pass


# ── ElevenLabsVoice.speak() ───────────────────────────────────────────────────


class TestElevenLabsVoiceSpeakCallsStream:
    def test_calls_stream_with_text(self) -> None:
        client = _make_mock_client()
        voice = ElevenLabsVoice(client=client, play=_no_op_play)
        voice.speak("Hello there.")
        client.text_to_speech.stream.assert_called_once()
        call_kwargs = client.text_to_speech.stream.call_args.kwargs
        assert call_kwargs["text"] == "Hello there."

    def test_calls_stream_with_default_voice_id(self) -> None:
        client = _make_mock_client()
        voice = ElevenLabsVoice(client=client, play=_no_op_play)
        voice.speak("Hi.")
        # voice_id is passed as the first positional arg
        call_args = client.text_to_speech.stream.call_args
        assert call_args.args[0] == _DEFAULT_VOICE_ID

    def test_calls_stream_with_custom_voice_id(self) -> None:
        client = _make_mock_client()
        voice = ElevenLabsVoice(client=client, voice_id="custom_voice_xyz", play=_no_op_play)
        voice.speak("Hi.")
        call_args = client.text_to_speech.stream.call_args
        assert call_args.args[0] == "custom_voice_xyz"

    def test_calls_stream_with_default_model_id(self) -> None:
        client = _make_mock_client()
        voice = ElevenLabsVoice(client=client, play=_no_op_play)
        voice.speak("Hi.")
        call_kwargs = client.text_to_speech.stream.call_args.kwargs
        assert call_kwargs["model_id"] == _DEFAULT_MODEL_ID

    def test_calls_stream_with_custom_model_id(self) -> None:
        client = _make_mock_client()
        voice = ElevenLabsVoice(client=client, model_id="eleven_turbo_v2", play=_no_op_play)
        voice.speak("Hi.")
        call_kwargs = client.text_to_speech.stream.call_args.kwargs
        assert call_kwargs["model_id"] == "eleven_turbo_v2"

    def test_stream_positional_voice_id(self) -> None:
        """voice_id is passed as the first positional arg to stream()."""
        client = _make_mock_client()
        voice = ElevenLabsVoice(client=client, play=_no_op_play)
        voice.speak("Test.")
        call_args = client.text_to_speech.stream.call_args
        assert call_args.args[0] == _DEFAULT_VOICE_ID

    def test_empty_text_no_call(self) -> None:
        """Empty text must not call the API at all."""
        client = _make_mock_client()
        voice = ElevenLabsVoice(client=client, play=_no_op_play)
        voice.speak("")
        client.text_to_speech.stream.assert_not_called()


class TestElevenLabsVoicePlayCallable:
    def test_play_called_with_audio_iterator(self) -> None:
        """The play callable receives the iterator returned by stream()."""
        chunks = [b"a", b"b", b"c"]
        client = _make_mock_client(audio_chunks=chunks)
        collected: list[bytes] = []

        def fake_play(it: Iterator[bytes]) -> None:
            collected.extend(it)

        voice = ElevenLabsVoice(client=client, play=fake_play)
        voice.speak("Hello.")
        assert collected == chunks

    def test_injected_play_called_exactly_once(self) -> None:
        client = _make_mock_client()
        play_mock = MagicMock()
        voice = ElevenLabsVoice(client=client, play=play_mock)
        voice.speak("Say this.")
        play_mock.assert_called_once()

    def test_play_receives_the_stream_return_value(self) -> None:
        """play() is called with whatever stream() returned (iterator identity)."""
        fake_iter = iter([b"x"])
        mock_tts = MagicMock()
        mock_tts.stream.return_value = fake_iter
        mock_client = MagicMock()
        mock_client.text_to_speech = mock_tts
        play_mock = MagicMock()
        voice = ElevenLabsVoice(client=mock_client, play=play_mock)
        voice.speak("Yes.")
        play_mock.assert_called_once_with(fake_iter)

    def test_empty_text_play_not_called(self) -> None:
        client = _make_mock_client()
        play_mock = MagicMock()
        voice = ElevenLabsVoice(client=client, play=play_mock)
        voice.speak("")
        play_mock.assert_not_called()


class TestElevenLabsVoiceLazyClient:
    def test_lazy_client_created_from_env(self) -> None:
        """Without an injected client, ElevenLabs() is created lazily from env."""
        mock_el_module = MagicMock()
        fake_client = _make_mock_client()
        mock_el_module.ElevenLabs.return_value = fake_client

        voice = ElevenLabsVoice(play=_no_op_play)  # no client injected
        with __import__("unittest.mock", fromlist=["patch"]).patch.dict(
            "sys.modules", {"elevenlabs": mock_el_module}
        ):
            voice._client = None  # reset any cached client
            voice._get_client()

        mock_el_module.ElevenLabs.assert_called_once()

    def test_no_lazy_import_when_client_injected(self) -> None:
        """Injecting a client must not trigger 'from elevenlabs import ElevenLabs'."""
        client = _make_mock_client()
        voice = ElevenLabsVoice(client=client, play=_no_op_play)
        saved = sys.modules.pop("elevenlabs", None)
        try:
            pre_modules = set(sys.modules.keys())
            voice.speak("Test.")
            new_modules = set(sys.modules.keys()) - pre_modules
            assert "elevenlabs" not in new_modules
        finally:
            if saved is not None:
                sys.modules["elevenlabs"] = saved

    def test_no_lazy_import_play_when_play_injected(self) -> None:
        """Injecting a play callable must not trigger 'from elevenlabs.play import stream'."""
        client = _make_mock_client()
        voice = ElevenLabsVoice(client=client, play=_no_op_play)
        saved = sys.modules.pop("elevenlabs.play", None)
        try:
            pre_modules = set(sys.modules.keys())
            voice.speak("Test.")
            new_modules = set(sys.modules.keys()) - pre_modules
            assert "elevenlabs.play" not in new_modules
        finally:
            if saved is not None:
                sys.modules["elevenlabs.play"] = saved


class TestElevenLabsVoiceDefaults:
    def test_default_voice_id_constant_is_rachel(self) -> None:
        assert _DEFAULT_VOICE_ID == "21m00Tcm4TlvDq8ikWAM"

    def test_default_model_id_is_multilingual_v2(self) -> None:
        assert _DEFAULT_MODEL_ID == "eleven_multilingual_v2"

    def test_voice_id_set_in_constructor(self) -> None:
        voice = ElevenLabsVoice(play=_no_op_play)
        assert voice._voice_id == _DEFAULT_VOICE_ID

    def test_model_id_set_in_constructor(self) -> None:
        voice = ElevenLabsVoice(play=_no_op_play)
        assert voice._model_id == _DEFAULT_MODEL_ID

    def test_play_none_by_default(self) -> None:
        """The _play attribute starts None until first call (lazy resolve)."""
        voice = ElevenLabsVoice()
        assert voice._play is None

    def test_client_none_by_default(self) -> None:
        voice = ElevenLabsVoice()
        assert voice._client is None
