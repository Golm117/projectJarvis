"""Tests for ClaudeResponder (T-401).

Design: the Anthropic client is injected via the constructor so no real network
call is needed. The lazy ``import anthropic`` path is NOT triggered by any test
here — the injected mock replaces it entirely.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from jarvis.adapters.claude_responder import (
    ClaudeResponder,
    _build_user_message,
    _describe_trigger,
    _extract_text,
)
from jarvis.types import EngagementHandoff

# ── Test helpers / fixtures ────────────────────────────────────────────────────


def _make_handoff(
    *,
    trigger_reason: str = "summon",
    summary: str = "They were discussing Python type hints.",
    recent_excerpt: str = "Alex: I'm not sure how Optional works.\nBo: Yeah, same.",
    detail: str = "",
) -> EngagementHandoff:
    return EngagementHandoff(
        trigger_reason=trigger_reason,
        summary=summary,
        recent_excerpt=recent_excerpt,
        detail=detail,
    )


def _make_mock_client(response_text: str = "It's a container for an optional value.") -> MagicMock:
    """Return a mock anthropic.Anthropic client whose messages.create returns *response_text*."""
    text_block = SimpleNamespace(text=response_text)
    mock_response = SimpleNamespace(content=[text_block])
    mock_messages = MagicMock()
    mock_messages.create.return_value = mock_response
    mock_client = MagicMock()
    mock_client.messages = mock_messages
    return mock_client


# ── ClaudeResponder.respond() ─────────────────────────────────────────────────


class TestClaudeResponderRespond:
    def test_returns_text_from_response(self) -> None:
        expected = "Optional wraps a value that might be None."
        client = _make_mock_client(expected)
        responder = ClaudeResponder(client=client)
        result = responder.respond(_make_handoff())
        assert result == expected

    def test_calls_correct_model(self) -> None:
        client = _make_mock_client()
        responder = ClaudeResponder(client=client)
        responder.respond(_make_handoff())
        call_kwargs = client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-opus-4-8"

    def test_calls_with_custom_model(self) -> None:
        client = _make_mock_client()
        responder = ClaudeResponder(client=client, model="claude-sonnet-4-6")
        responder.respond(_make_handoff())
        call_kwargs = client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-sonnet-4-6"

    def test_max_tokens_respected(self) -> None:
        client = _make_mock_client()
        responder = ClaudeResponder(client=client, max_tokens=50)
        responder.respond(_make_handoff())
        call_kwargs = client.messages.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 50

    def test_system_prompt_is_set(self) -> None:
        client = _make_mock_client()
        responder = ClaudeResponder(client=client)
        responder.respond(_make_handoff())
        call_kwargs = client.messages.create.call_args.kwargs
        system = call_kwargs["system"]
        assert "1 to 3 sentences" in system
        assert "No preamble" in system
        assert "Plain prose" in system

    def test_handoff_context_in_user_message(self) -> None:
        client = _make_mock_client()
        handoff = _make_handoff(
            summary="Discussion about type safety.",
            recent_excerpt="Carol: This is confusing.",
        )
        responder = ClaudeResponder(client=client)
        responder.respond(handoff)
        call_kwargs = client.messages.create.call_args.kwargs
        messages = call_kwargs["messages"]
        assert len(messages) == 1
        user_content = messages[0]["content"]
        assert "Discussion about type safety." in user_content
        assert "Carol: This is confusing." in user_content

    def test_strips_whitespace_from_response(self) -> None:
        client = _make_mock_client("  Some answer.  \n")
        responder = ClaudeResponder(client=client)
        result = responder.respond(_make_handoff())
        assert result == "Some answer."

    def test_empty_content_returns_empty_string(self) -> None:
        mock_response = SimpleNamespace(content=[])
        mock_messages = MagicMock()
        mock_messages.create.return_value = mock_response
        mock_client = MagicMock()
        mock_client.messages = mock_messages
        responder = ClaudeResponder(client=mock_client)
        result = responder.respond(_make_handoff())
        assert result == ""

    def test_no_lazy_import_when_client_injected(self) -> None:
        """Injecting a client must not trigger 'import anthropic'."""
        client = _make_mock_client()
        responder = ClaudeResponder(client=client)
        # Temporarily remove 'anthropic' from sys.modules to detect any import attempt.
        saved = sys.modules.pop("anthropic", None)
        try:
            pre_modules = set(sys.modules.keys())
            responder.respond(_make_handoff())
            new_modules = set(sys.modules.keys()) - pre_modules
            assert "anthropic" not in new_modules
        finally:
            if saved is not None:
                sys.modules["anthropic"] = saved


# ── Lazy client creation from env ─────────────────────────────────────────────


class TestClaudeResponderLazyClient:
    def test_lazy_client_created_from_env(self) -> None:
        """Without an injected client, a real Anthropic client is created lazily."""
        mock_anthropic_module = MagicMock()
        fake_client = _make_mock_client("Answer from env.")
        mock_anthropic_module.Anthropic.return_value = fake_client

        responder = ClaudeResponder()  # no client injected
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_module}):
            result = responder.respond(_make_handoff())

        assert result == "Answer from env."
        mock_anthropic_module.Anthropic.assert_called_once()


# ── _build_user_message ───────────────────────────────────────────────────────


class TestBuildUserMessage:
    def test_contains_summary(self) -> None:
        handoff = _make_handoff(summary="They discussed caching strategies.")
        msg = _build_user_message(handoff)
        assert "They discussed caching strategies." in msg

    def test_contains_excerpt(self) -> None:
        handoff = _make_handoff(recent_excerpt="Alice: What about Redis?")
        msg = _build_user_message(handoff)
        assert "Alice: What about Redis?" in msg

    def test_contains_detail_when_present(self) -> None:
        handoff = _make_handoff(detail="User specifically asked about Redis.")
        msg = _build_user_message(handoff)
        assert "User specifically asked about Redis." in msg

    def test_detail_absent_when_empty(self) -> None:
        handoff = _make_handoff(detail="")
        msg = _build_user_message(handoff)
        assert "Additional context:" not in msg

    def test_summon_trigger_label(self) -> None:
        handoff = _make_handoff(trigger_reason="summon")
        msg = _build_user_message(handoff)
        assert "summoned" in msg.lower()

    def test_wall_trigger_label(self) -> None:
        handoff = _make_handoff(trigger_reason="wall:factual_gap")
        msg = _build_user_message(handoff)
        assert "factual_gap" in msg

    def test_empty_summary_placeholder(self) -> None:
        handoff = _make_handoff(summary="")
        msg = _build_user_message(handoff)
        assert "no summary yet" in msg

    def test_empty_excerpt_placeholder(self) -> None:
        handoff = _make_handoff(recent_excerpt="")
        msg = _build_user_message(handoff)
        assert "no excerpt yet" in msg


# ── _describe_trigger ─────────────────────────────────────────────────────────


class TestDescribeTrigger:
    def test_summon(self) -> None:
        result = _describe_trigger("summon")
        assert "summoned" in result.lower()

    def test_wall_factual_gap(self) -> None:
        result = _describe_trigger("wall:factual_gap")
        assert "factual_gap" in result

    def test_wall_unanswered_question(self) -> None:
        result = _describe_trigger("wall:unanswered_question")
        assert "unanswered_question" in result

    def test_unknown_reason_passthrough(self) -> None:
        result = _describe_trigger("some_unknown_reason")
        assert result == "some_unknown_reason"


# ── _extract_text ─────────────────────────────────────────────────────────────


class TestExtractText:
    def test_extracts_first_text_block(self) -> None:
        response = SimpleNamespace(content=[SimpleNamespace(text="Hello there.")])
        assert _extract_text(response) == "Hello there."

    def test_strips_text(self) -> None:
        response = SimpleNamespace(content=[SimpleNamespace(text="  Hello.  \n")])
        assert _extract_text(response) == "Hello."

    def test_skips_non_text_blocks(self) -> None:
        # A block without a .text attribute (e.g. a thinking block mock)
        non_text = SimpleNamespace(thinking="internal reasoning")
        text = SimpleNamespace(text="The answer.")
        response = SimpleNamespace(content=[non_text, text])
        assert _extract_text(response) == "The answer."

    def test_empty_content(self) -> None:
        response = SimpleNamespace(content=[])
        assert _extract_text(response) == ""
