"""Tests for VoiceSession (T-403).

Design: both the Anthropic client (via ClaudeResponder) and the play callable
(via ElevenLabsVoice) are injected, so no network and no audio. We simulate the
Claude streaming API with a fake context manager that yields text tokens
deterministically. The full respond-and-speak pipeline is exercised in tests
without any real API calls.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

from jarvis.adapters.claude_responder import ClaudeResponder
from jarvis.adapters.elevenlabs_voice import ElevenLabsVoice
from jarvis.adapters.voice_session import VoiceSession, _split_on_sentences
from jarvis.types import EngagementHandoff

# ── Test helpers ──────────────────────────────────────────────────────────────


def _make_handoff(
    *,
    trigger_reason: str = "summon",
    summary: str = "Discussion about Python.",
    recent_excerpt: str = "Alice: How does Optional work?",
    detail: str = "",
) -> EngagementHandoff:
    return EngagementHandoff(
        trigger_reason=trigger_reason,
        summary=summary,
        recent_excerpt=recent_excerpt,
        detail=detail,
    )


def _make_streaming_client(tokens: list[str]) -> MagicMock:
    """Return a mock Anthropic client whose messages.stream() yields *tokens*."""

    @contextmanager
    def fake_stream(**kwargs):  # noqa: N802
        stream_ns = SimpleNamespace(text_stream=iter(tokens))
        yield stream_ns

    mock_messages = MagicMock()
    mock_messages.stream = fake_stream
    mock_client = MagicMock()
    mock_client.messages = mock_messages
    return mock_client


def _make_voice_session(tokens: list[str]) -> tuple[VoiceSession, list[str]]:
    """Return a VoiceSession with fake streaming Claude and a recording voice."""
    claude_client = _make_streaming_client(tokens)
    responder = ClaudeResponder(client=claude_client)

    spoken: list[str] = []

    def recording_play(audio_iter):
        for _ in audio_iter:
            pass

    # Fake ElevenLabs client that records what text was sent
    class RecordingTTS:
        def stream(self, voice_id, *, text, model_id):  # noqa: N802
            spoken.append(text)
            return iter([b"audio"])

    fake_el_client = MagicMock()
    fake_el_client.text_to_speech = RecordingTTS()
    voice = ElevenLabsVoice(client=fake_el_client, play=recording_play)
    session = VoiceSession(responder=responder, voice=voice)
    return session, spoken


# ── VoiceSession.respond_and_speak() ─────────────────────────────────────────


class TestRespondAndSpeakBasic:
    def test_returns_full_text(self) -> None:
        tokens = ["Hello", " there.", " This", " is", " Jarvis."]
        session, _ = _make_voice_session(tokens)
        result = session.respond_and_speak(_make_handoff())
        assert "Hello there." in result
        assert "This is Jarvis." in result

    def test_speaks_sentence_chunks(self) -> None:
        """Each complete sentence is sent to voice.speak() as it becomes available."""
        tokens = ["First", " sentence.", " Second", " sentence."]
        session, spoken = _make_voice_session(tokens)
        session.respond_and_speak(_make_handoff())
        assert len(spoken) == 2
        assert spoken[0] == "First sentence."
        assert spoken[1] == "Second sentence."

    def test_single_sentence_speaks_once(self) -> None:
        tokens = ["Just", " one", " sentence."]
        session, spoken = _make_voice_session(tokens)
        session.respond_and_speak(_make_handoff())
        assert len(spoken) == 1
        assert spoken[0] == "Just one sentence."

    def test_no_trailing_empty_speaks(self) -> None:
        tokens = ["Done."]
        session, spoken = _make_voice_session(tokens)
        session.respond_and_speak(_make_handoff())
        assert all(s.strip() for s in spoken)

    def test_question_mark_as_sentence_boundary(self) -> None:
        tokens = ["Can", " you", " help?", " Yes."]
        session, spoken = _make_voice_session(tokens)
        session.respond_and_speak(_make_handoff())
        assert any("Can you help?" in s for s in spoken)

    def test_exclamation_as_sentence_boundary(self) -> None:
        tokens = ["Great", "!", " Now", " proceed."]
        session, spoken = _make_voice_session(tokens)
        session.respond_and_speak(_make_handoff())
        assert any("Great!" in s for s in spoken)

    def test_empty_token_stream_returns_empty_string(self) -> None:
        session, spoken = _make_voice_session([])
        result = session.respond_and_speak(_make_handoff())
        assert result == ""
        assert spoken == []

    def test_whitespace_only_tokens_not_spoken(self) -> None:
        tokens = ["   ", "  "]
        session, spoken = _make_voice_session(tokens)
        session.respond_and_speak(_make_handoff())
        assert spoken == []


class TestRespondAndSpeakBargeIn:
    def test_stop_event_set_before_call_suppresses_all_speech(self) -> None:
        tokens = ["Hello", " there.", " More", " stuff."]
        session, spoken = _make_voice_session(tokens)
        stop = threading.Event()
        stop.set()  # already set before we start
        session.respond_and_speak(_make_handoff(), stop_event=stop)
        assert spoken == []

    def test_stop_event_none_no_barge_in(self) -> None:
        """With stop_event=None, full text is spoken (no barge-in check)."""
        tokens = ["One.", " Two."]
        session, spoken = _make_voice_session(tokens)
        result = session.respond_and_speak(_make_handoff(), stop_event=None)
        assert len(spoken) == 2
        assert "One." in result

    def test_stop_event_set_mid_stream_aborts_remaining(self) -> None:
        """When the stop event is set partway through, no further chunks are spoken."""
        stop = threading.Event()
        spoken_chunks: list[str] = []

        # Use a custom play that sets stop after the first audio chunk
        def recording_play(audio_iter):
            for _ in audio_iter:
                pass
            stop.set()  # set after first speak completes

        tokens = ["First sentence.", " Second sentence.", " Third sentence."]

        claude_client = _make_streaming_client(tokens)
        responder = ClaudeResponder(client=claude_client)

        class RecordingTTS:
            def stream(self, voice_id, *, text, model_id):  # noqa: N802
                spoken_chunks.append(text)
                return iter([b"audio"])

        fake_el_client = MagicMock()
        fake_el_client.text_to_speech = RecordingTTS()
        voice = ElevenLabsVoice(client=fake_el_client, play=recording_play)
        session = VoiceSession(responder=responder, voice=voice)
        session.respond_and_speak(_make_handoff(), stop_event=stop)
        # First chunk may have been spoken before stop was set, but not all 3
        assert len(spoken_chunks) < 3


class TestRespondAndSpeakReturnValue:
    def test_return_value_matches_full_token_stream(self) -> None:
        """The return value is the full concatenation of all tokens."""
        tokens = ["Hello", " world.", " Goodbye", " world."]
        session, _ = _make_voice_session(tokens)
        result = session.respond_and_speak(_make_handoff())
        # All non-empty chunks joined
        assert "Hello world." in result
        assert "Goodbye world." in result

    def test_spoken_chunks_match_returned_text(self) -> None:
        """Every spoken chunk appears in the returned string."""
        tokens = ["Alpha.", " Beta.", " Gamma."]
        session, spoken = _make_voice_session(tokens)
        result = session.respond_and_speak(_make_handoff())
        for chunk in spoken:
            assert chunk in result


# ── _split_on_sentences ───────────────────────────────────────────────────────


class TestSplitOnSentences:
    def test_single_sentence_no_trailing_space(self) -> None:
        parts = _split_on_sentences("Hello.")
        assert parts == ["Hello."]

    def test_two_sentences(self) -> None:
        parts = _split_on_sentences("Hello. World.")
        assert len(parts) == 2
        assert parts[0] == "Hello."
        assert parts[1] == "World."

    def test_trailing_fragment(self) -> None:
        parts = _split_on_sentences("Hello. Wor")
        assert parts[0] == "Hello."
        assert parts[-1] == "Wor"

    def test_question_mark_boundary(self) -> None:
        parts = _split_on_sentences("How are you? Good.")
        assert parts[0] == "How are you?"

    def test_exclamation_boundary(self) -> None:
        parts = _split_on_sentences("Great! Now go.")
        assert parts[0] == "Great!"

    def test_empty_string(self) -> None:
        parts = _split_on_sentences("")
        assert parts == []

    def test_no_boundary(self) -> None:
        parts = _split_on_sentences("No boundary here")
        assert parts == ["No boundary here"]
