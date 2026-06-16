"""ClaudeResponder — EngagedResponder backed by claude-opus-4-8 (T-401).

Implements the frozen ``EngagedResponder`` Protocol
(``respond(handoff) -> str``) by calling the Anthropic Messages API with a
tight spoken-style system prompt grounded in the ``EngagementHandoff``.

Design constraints
------------------
* **Lazy import:** ``import anthropic`` is deferred to the first call so
  ``uv run pytest`` and ``python -m jarvis`` (without ``--voice``) never load
  the Anthropic SDK — the test suite stays offline.
* **Injected client:** the ``anthropic.Anthropic`` client is accepted via the
  constructor so unit tests can pass a mock without any network or key.
* **Key from env:** when no client is injected, reads ``ANTHROPIC_API_KEY``
  from the environment (populated by ``load_dotenv()`` at the live entry).
* **Spoken-style contract:** the system prompt enforces 1–3 sentences, no
  preamble, no markdown, plain prose — the voice register from ``.pdr.md`` and
  PRD 02.
* **Thinking disabled:** spoken responses are short; adaptive thinking would
  add latency with no quality benefit for 1–3 sentence replies.  We keep it
  off (`thinking` param omitted → defaults off on Opus 4.8).
"""

from __future__ import annotations

import os
from typing import Any

from jarvis.types import EngagementHandoff

# ── Spoken-style system prompt ────────────────────────────────────────────────
# Enforces the voice register from .pdr.md §voice_register and PRD 02
# §response-style-contract: peer-who-was-listening, 1-3 sentences, no preamble,
# no markdown, plain prose.  The handoff context is injected per-call in the
# user message so this block stays stable (good for prompt caching if we ever
# add it).
_SYSTEM_PROMPT = """\
You are Jarvis, an always-on desktop assistant who has been listening to the \
conversation. When you speak, you respond like a competent peer who was in the \
room — direct, brief, grounded in what was just said.

Rules you MUST follow:
- Answer in 1 to 3 sentences. Never more.
- No preamble. Do not start with "Sure", "Of course", "Great question", \
"Based on the conversation", "According to", or any similar phrase.
- Plain prose only. No markdown, no bullet points, no headers, no lists.
- Spoken aloud — write as you would say it, not as you would write it.
- If you don't know something, say so briefly in one sentence.
- Ask a clarifying question only if genuinely necessary; otherwise just answer.\
"""


class ClaudeResponder:
    """``EngagedResponder`` that calls ``claude-opus-4-8`` to compose the reply.

    Satisfies the frozen ``EngagedResponder`` Protocol:
    ``respond(handoff: EngagementHandoff) -> str``.

    Args:
        client: an ``anthropic.Anthropic`` instance (injected for testing).
            If ``None``, one is created lazily from ``ANTHROPIC_API_KEY`` on
            first call.
        model: the Claude model to call. Defaults to ``claude-opus-4-8`` per
            the approved stack.
        max_tokens: per-response token ceiling. 120 tokens is well above what
            1–3 spoken sentences need; keeps latency tight.
    """

    def __init__(
        self,
        client: Any = None,
        *,
        model: str = "claude-opus-4-8",
        max_tokens: int = 120,
    ) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    # ------------------------------------------------------------------
    # EngagedResponder Protocol
    # ------------------------------------------------------------------

    def respond(self, handoff: EngagementHandoff) -> str:
        """Compose a spoken-style answer grounded in *handoff*.

        Calls ``claude-opus-4-8`` (or the injected client) with the spoken-style
        system prompt and a user message that packages the engagement context.
        Returns the first text content block as a plain string.

        Args:
            handoff: the engagement context from the orchestrator.

        Returns:
            A 1–3 sentence spoken-style answer string.
        """
        client = self._get_client()
        user_msg = _build_user_message(handoff)
        response = client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        return _extract_text(response)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        """Return the injected client, or create one lazily from env."""
        if self._client is not None:
            return self._client
        # Lazy import — only reaches here when running live (--voice flag).
        import anthropic  # noqa: PLC0415

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        self._client = anthropic.Anthropic(api_key=api_key)
        return self._client


# ── Module-level helpers (pure functions — easy to test) ──────────────────────


def _build_user_message(handoff: EngagementHandoff) -> str:
    """Build the user-turn message that packages the engagement context.

    The message is structured so Claude has the trigger reason, the living
    summary, and the recent transcript excerpt — exactly what it needs to give
    a grounded 1–3 sentence answer.
    """
    trigger_label = _describe_trigger(handoff.trigger_reason)
    parts = [
        f"Trigger: {trigger_label}",
        "",
        "Living summary of the conversation so far:",
        handoff.summary if handoff.summary else "(no summary yet)",
        "",
        "Recent conversation excerpt:",
        handoff.recent_excerpt if handoff.recent_excerpt else "(no excerpt yet)",
    ]
    if handoff.detail:
        parts += ["", f"Additional context: {handoff.detail}"]
    parts += ["", "Please respond now."]
    return "\n".join(parts)


def _describe_trigger(trigger_reason: str) -> str:
    """Return a human-readable trigger label for the system message."""
    if trigger_reason == "summon":
        return "The user summoned you by name — engage directly."
    if trigger_reason.startswith("wall:"):
        category = trigger_reason[len("wall:"):]
        return (
            f"You detected a conversational wall ({category}) — "
            "offer brief, helpful context without being asked."
        )
    return trigger_reason


def _extract_text(response: Any) -> str:
    """Extract the first text block from an Anthropic Messages response."""
    for block in response.content:
        if hasattr(block, "text"):
            return block.text.strip()
    return ""
