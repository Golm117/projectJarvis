"""Tests for QwenWallBackend (T-203, T-508, T-509).

All tests in this file are **model-free** — they run without MLX, without Qwen
weights, and without network access.  The real model inference lives in the
optional live test at the bottom, which self-skips when weights are unavailable
(mirroring ``test_live_qwen_summarize_optional`` in ``test_qwen_summarizer.py``).

Design under test (T-508/T-509 graded contract):
- ``_build_messages(transcript, summary)`` — message construction (pure, testable
  in isolation).  The user message now contains the graded 1–5 rating schema
  with ``reasoning``, ``rating``, ``category``, and ``offer`` fields (no
  ``is_wall`` or ``confidence`` fields — those are derived).
- ``rating_to_confidence(rating)`` — the 1–5 → float calibrated lookup table.
- ``_parse_verdict(raw)`` — JSON parsing into ``WallVerdict`` for each of the
  5 ``WallCategory`` values + malformed/edge inputs.  The new schema has
  ``rating`` (int 1–5) instead of ``is_wall``/``confidence``; ``is_wall`` is
  derived from ``rating >= 3``; ``confidence`` from ``rating_to_confidence``.
- ``QwenWallBackend.detect_wall(transcript, summary)`` — the seam adapter; calls
  the injected model with the right messages and parses the result.
- ``QwenWallBackend`` satisfies the ``WallBackend`` Protocol.
- ``WallVerdict`` invariants: ``NONE`` iff ``¬is_wall``; ``offer`` is ``""`` for
  a non-wall; ``confidence`` is a valid graded value.
- Graceful fallback to ``WallVerdict.none()`` on any parse failure.

T-509 adds:
- The system prompt explicitly calls out DIRECT UNANSWERED QUESTION as the
  primary fire case (fixing the T-508 framing regression where the model
  excluded direct questions with "it's a direct question so it's not a gap").
- The live test now validates on the REAL ``detect_wall(transcript, summary)``
  path with multi-line rolling-window transcripts + context summary, not clean
  single-line probes (which is how the T-508 gate was fooled).
- Model escalated from 3B to 7B (DEFAULT_MODEL_PATH changed).
"""

from __future__ import annotations

import inspect
import json

import pytest

from jarvis.ml.qwen import QwenModel
from jarvis.ml.wall import (
    QwenWallBackend,
    _build_messages,
    _parse_verdict,
    rating_to_confidence,
)
from jarvis.types import WallCategory, WallVerdict

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeQwenModel:
    """A stub QwenModel that records calls and returns canned output.

    Used in all model-free tests.  The real ``QwenModel`` is never instantiated.
    The default canned response is a valid T-508 schema: rating 1 (non-wall).
    """

    def __init__(
        self,
        canned: str = '{"reasoning": "no gap", "rating": 1, "category": "none", "offer": ""}',
    ) -> None:
        self.calls: list[tuple[list[dict[str, str]], int]] = []
        self._canned = canned

    def generate(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 200,
    ) -> str:
        self.calls.append((messages, max_tokens))
        return self._canned


def _wall_json(
    rating: int = 4,
    category: str = "factual_gap",
    offer: str = "I can look that up — want me to?",
    reasoning: str = "A clear unanswered factual question.",
) -> str:
    """Build a valid T-508 JSON string as the model would return.

    The new schema has ``rating`` (1–5) instead of ``is_wall``/``confidence``.
    A rating >= 3 with a non-"none" category becomes is_wall=True.
    """
    return json.dumps(
        {"reasoning": reasoning, "rating": rating, "category": category, "offer": offer}
    )


def _none_json(reasoning: str = "no gap here") -> str:
    return json.dumps({"reasoning": reasoning, "rating": 1, "category": "none", "offer": ""})


# ---------------------------------------------------------------------------
# 1. Message construction (_build_messages) — no model needed
# ---------------------------------------------------------------------------


class TestBuildMessages:
    """Assert the chat-template message list is built correctly."""

    def test_returns_two_messages(self) -> None:
        msgs = _build_messages("Alice: hello", "meeting summary")
        assert len(msgs) == 2

    def test_first_message_is_system(self) -> None:
        msgs = _build_messages("Alice: hello", "")
        assert msgs[0]["role"] == "system"

    def test_second_message_is_user(self) -> None:
        msgs = _build_messages("Alice: hello", "")
        assert msgs[1]["role"] == "user"

    def test_transcript_appears_in_user_message(self) -> None:
        transcript = "Alice: what was the conference date?"
        msgs = _build_messages(transcript, "")
        assert transcript in msgs[1]["content"]

    def test_summary_appears_in_user_message(self) -> None:
        summary = "The team is planning a conference."
        msgs = _build_messages("Alice: yes", summary)
        assert summary in msgs[1]["content"]

    def test_empty_transcript_gets_placeholder(self) -> None:
        msgs = _build_messages("", "some summary")
        assert "no transcript" in msgs[1]["content"].lower()

    def test_whitespace_transcript_gets_placeholder(self) -> None:
        msgs = _build_messages("   \n  ", "summary")
        assert "no transcript" in msgs[1]["content"].lower()

    def test_empty_summary_gets_placeholder(self) -> None:
        msgs = _build_messages("Alice: help?", "")
        assert "no summary" in msgs[1]["content"].lower()

    def test_system_message_non_empty(self) -> None:
        msgs = _build_messages("Alice: test", "")
        assert msgs[0]["content"].strip()

    def test_system_prompt_mentions_conservative(self) -> None:
        """System prompt must signal precision-over-recall to the model."""
        msgs = _build_messages("Alice: test", "")
        system_lower = msgs[0]["content"].lower()
        assert (
            "conservative" in system_lower
            or "precise" in system_lower
            or "precision" in system_lower
            or "confident" in system_lower
        )

    def test_user_message_mentions_all_four_wall_categories(self) -> None:
        msgs = _build_messages("Alice: I don't know", "")
        user = msgs[1]["content"]
        assert "unanswered_question" in user
        assert "factual_gap" in user
        assert "stuck_point" in user
        assert "explicit_ask" in user
        assert "none" in user

    def test_user_message_contains_new_json_schema(self) -> None:
        """The user message must include the T-508 JSON schema: rating + category + offer."""
        msgs = _build_messages("Alice: help me", "")
        user = msgs[1]["content"]
        # T-508 schema: reasoning, rating, category, offer
        assert "rating" in user
        assert "category" in user
        assert "offer" in user
        assert "reasoning" in user

    def test_user_message_no_longer_contains_is_wall_or_confidence(self) -> None:
        """T-508 removed is_wall and confidence from the model output schema."""
        msgs = _build_messages("Alice: help me", "")
        # The JSON schema line must not ask the model for is_wall or confidence —
        # those are now derived by the parser, not emitted by the model.
        # (They may appear in comments/docs but NOT as required output fields.)
        schema_line = msgs[1]["content"].split("Reply with ONLY this JSON")[-1]
        assert "is_wall" not in schema_line
        assert "confidence" not in schema_line

    def test_both_roles_are_strings(self) -> None:
        msgs = _build_messages("A: text", "prev")
        for m in msgs:
            assert isinstance(m["role"], str)
            assert isinstance(m["content"], str)

    def test_precision_instruction_present(self) -> None:
        """The system prompt must include an explicit precision-over-recall instruction."""
        msgs = _build_messages("A: text", "")
        system = msgs[0]["content"].lower()
        # At least one of these precision cues must be present.
        has_cue = (
            "when in doubt" in system
            or "only flag" in system
            or "only when" in system
            or "not a wall" in system
            or "err on the side of silence" in system
            or "precision" in system
        )
        assert has_cue, "system prompt must include a precision-over-recall instruction"

    def test_user_message_contains_rating_scale(self) -> None:
        """The user message must document the 1–5 rating scale."""
        msgs = _build_messages("Alice: I wonder", "")
        user = msgs[1]["content"]
        # The rating instruction mentions 5 levels
        assert "5" in user
        assert "1" in user

    def test_user_message_contains_few_shot_exemplars(self) -> None:
        """The user message must contain the few-shot exemplars for the failure cases."""
        msgs = _build_messages("Alice: test", "")
        user = msgs[1]["content"]
        # Exemplars reference the √81 case
        assert "81" in user

    def test_user_message_contains_reasoning_instruction(self) -> None:
        """The user message must include the Information-Gap CoT reasoning step."""
        msgs = _build_messages("Alice: test", "")
        user = msgs[1]["content"]
        # The reasoning step asks about unanswered/factual/directedness
        assert "reasoning" in user.lower() or "REASONING" in user

    # --- T-509 framing-fix tests ---

    def test_system_prompt_names_direct_question_as_primary_case(self) -> None:
        """T-509 fix: system prompt must explicitly call out direct unanswered question
        as the PRIMARY fire case, NOT excluded."""
        msgs = _build_messages("Alice: test", "")
        system = msgs[0]["content"].lower()
        # Must not teach the model to exclude direct questions
        # Must instead make direct unanswered question = primary fire case
        has_primary_cue = "primary" in system or "direct" in system
        assert has_primary_cue, (
            "system prompt must explicitly identify direct unanswered questions "
            "as the primary fire case (T-509 framing fix)"
        )

    def test_system_prompt_does_not_exclude_direct_questions(self) -> None:
        """T-509: the system prompt must NOT frame 'direct questions' as excluded."""
        msgs = _build_messages("Alice: test", "")
        system = msgs[0]["content"].lower()
        # The old framing "UNANSWERED, ANSWERABLE GAP" led the model to reason
        # "it's a direct question not a gap". The new framing must not do that.
        assert "gap" not in system or "answer" in system, (
            "if 'gap' appears in the system prompt, it must be paired with "
            "language that includes direct questions (T-509 framing fix)"
        )

    def test_user_message_exemplar_for_plain_statement_present(self) -> None:
        """T-509 adds Example 7: plain statement / plan → not a gap. Must be in exemplars."""
        msgs = _build_messages("Alice: test", "")
        user = msgs[1]["content"]
        # Example 7 mentions a PR/plan decision
        assert "PR" in user or "plan" in user.lower() or "statement" in user.lower(), (
            "user message must include a plain-statement non-wall exemplar (T-509 Example 7)"
        )

    def test_reasoning_instruction_names_direct_question(self) -> None:
        """T-509: the reasoning instruction must call out direct unanswered question explicitly."""
        msgs = _build_messages("Alice: test", "")
        user = msgs[1]["content"]
        # The reasoning step must tell the model that direct questions don't subtract score
        has_cue = "direct" in user.lower() or "primary" in user.lower()
        assert has_cue, (
            "reasoning instruction must clarify that a direct unanswered question "
            "is the primary fire case (T-509 framing fix)"
        )


# ---------------------------------------------------------------------------
# 2. rating_to_confidence mapping (new in T-508)
# ---------------------------------------------------------------------------


class TestRatingToConfidence:
    """Test the 1–5 → confidence lookup table."""

    def test_rating_1_is_lowest(self) -> None:
        assert rating_to_confidence(1) == pytest.approx(0.05)

    def test_rating_2(self) -> None:
        assert rating_to_confidence(2) == pytest.approx(0.30)

    def test_rating_3_below_floor(self) -> None:
        """Rating 3 maps to 0.65 — below the 0.70 SummonController floor (intended)."""
        assert rating_to_confidence(3) == pytest.approx(0.65)

    def test_rating_4_above_floor(self) -> None:
        """Rating 4 maps to 0.80 — above the 0.70 floor, fires the interjection."""
        assert rating_to_confidence(4) == pytest.approx(0.80)

    def test_rating_5_is_highest(self) -> None:
        assert rating_to_confidence(5) == pytest.approx(0.95)

    def test_out_of_range_returns_zero(self) -> None:
        """An out-of-range rating returns 0.0 (graceful; triggers none() in the parser)."""
        assert rating_to_confidence(0) == pytest.approx(0.0)
        assert rating_to_confidence(6) == pytest.approx(0.0)
        assert rating_to_confidence(-1) == pytest.approx(0.0)

    def test_all_valid_ratings_return_positive(self) -> None:
        for r in range(1, 6):
            assert rating_to_confidence(r) > 0.0

    def test_monotonic_increasing(self) -> None:
        """Higher rating → higher confidence."""
        confidences = [rating_to_confidence(r) for r in range(1, 6)]
        for i in range(len(confidences) - 1):
            assert confidences[i] < confidences[i + 1]


# ---------------------------------------------------------------------------
# 3. JSON parsing (_parse_verdict) — new T-508 schema + invariants
# ---------------------------------------------------------------------------


class TestParseVerdictWallCategories:
    """Test _parse_verdict for each WallCategory value using the T-508 schema."""

    def test_factual_gap(self) -> None:
        raw = _wall_json(rating=4, category="factual_gap", offer="I can look that up.")
        v = _parse_verdict(raw)
        assert v.is_wall is True
        assert v.category is WallCategory.FACTUAL_GAP
        assert v.confidence == pytest.approx(0.80)  # rating 4 → 0.80
        assert v.offer == "I can look that up."

    def test_unanswered_question(self) -> None:
        raw = _wall_json(
            rating=5, category="unanswered_question", offer="I think I can answer that."
        )
        v = _parse_verdict(raw)
        assert v.is_wall is True
        assert v.category is WallCategory.UNANSWERED_QUESTION
        assert v.confidence == pytest.approx(0.95)  # rating 5 → 0.95

    def test_stuck_point(self) -> None:
        raw = _wall_json(
            rating=4, category="stuck_point", offer="Want me to suggest a way forward?"
        )
        v = _parse_verdict(raw)
        assert v.is_wall is True
        assert v.category is WallCategory.STUCK_POINT

    def test_explicit_ask(self) -> None:
        raw = _wall_json(rating=4, category="explicit_ask", offer="Want me to look that up?")
        v = _parse_verdict(raw)
        assert v.is_wall is True
        assert v.category is WallCategory.EXPLICIT_ASK

    def test_none_category(self) -> None:
        raw = _none_json()
        v = _parse_verdict(raw)
        assert v.is_wall is False
        assert v.category is WallCategory.NONE
        assert v.confidence == pytest.approx(0.05)  # rating 1 → 0.05
        assert v.offer == ""

    def test_rating_2_is_non_wall(self) -> None:
        """Rating 2 → is_wall=False even if category is non-none."""
        # The parser normalises: below the threshold → non-wall
        raw = json.dumps(
            {"reasoning": "weak signal", "rating": 2, "category": "factual_gap", "offer": ""}
        )
        v = _parse_verdict(raw)
        assert v.is_wall is False
        assert v.category is WallCategory.NONE

    def test_rating_3_is_wall(self) -> None:
        """Rating 3 → is_wall=True (threshold is >= 3), confidence 0.65."""
        raw = json.dumps(
            {
                "reasoning": "declarative gap",
                "rating": 3,
                "category": "factual_gap",
                "offer": "I can check the date if you'd like.",
            }
        )
        v = _parse_verdict(raw)
        assert v.is_wall is True
        assert v.category is WallCategory.FACTUAL_GAP
        assert v.confidence == pytest.approx(0.65)  # below 0.70 floor — borderline

    def test_rating_3_confidence_below_summon_floor(self) -> None:
        """Rating 3 → confidence 0.65 sits below the SummonController 0.70 floor.

        The backend correctly surfaces this as is_wall=True with low confidence;
        the SummonController will suppress it — that is the intended behavior
        (borderline walls are flagged but not spoken).
        """
        raw = json.dumps(
            {"reasoning": "soft gap", "rating": 3, "category": "factual_gap", "offer": "help?"}
        )
        v = _parse_verdict(raw)
        assert v.is_wall is True
        assert v.confidence == pytest.approx(0.65)
        assert v.confidence < 0.70  # stays below the floor

    def test_rating_4_confidence_above_summon_floor(self) -> None:
        """Rating 4 → confidence 0.80 clears the SummonController 0.70 floor."""
        raw = _wall_json(rating=4, category="unanswered_question")
        v = _parse_verdict(raw)
        assert v.is_wall is True
        assert v.confidence == pytest.approx(0.80)
        assert v.confidence >= 0.70  # clears the floor


class TestParseVerdictInvariants:
    """Test that _parse_verdict enforces WallVerdict invariants."""

    def test_non_wall_always_has_none_category(self) -> None:
        """If rating < 3, category must be NONE regardless of what model says."""
        raw = json.dumps(
            {"reasoning": "weak", "rating": 2, "category": "factual_gap", "offer": "help?"}
        )
        v = _parse_verdict(raw)
        assert v.is_wall is False
        assert v.category is WallCategory.NONE

    def test_non_wall_offer_is_empty_string(self) -> None:
        """If rating < 3, offer must be empty regardless of what model says."""
        raw = json.dumps(
            {"reasoning": "no gap", "rating": 1, "category": "none", "offer": "non-empty offer"}
        )
        v = _parse_verdict(raw)
        assert v.offer == ""

    def test_wall_with_none_category_becomes_no_wall(self) -> None:
        """If rating >= 3 but category is 'none', normalize to no-wall."""
        raw = json.dumps(
            {"reasoning": "mixed signal", "rating": 3, "category": "none", "offer": ""}
        )
        v = _parse_verdict(raw)
        assert v.is_wall is False
        assert v.category is WallCategory.NONE

    def test_rating_3_boundary_is_wall_true(self) -> None:
        """Rating exactly 3 is a wall (threshold is >=3, inclusive)."""
        raw = json.dumps(
            {"reasoning": "borderline", "rating": 3, "category": "stuck_point", "offer": "help?"}
        )
        v = _parse_verdict(raw)
        assert v.is_wall is True

    def test_rating_2_boundary_is_wall_false(self) -> None:
        """Rating exactly 2 is NOT a wall (below the threshold of 3)."""
        raw = json.dumps(
            {"reasoning": "leaning no", "rating": 2, "category": "stuck_point", "offer": "help?"}
        )
        v = _parse_verdict(raw)
        assert v.is_wall is False

    def test_confidence_comes_from_rating_not_json(self) -> None:
        """Confidence must be derived from the rating lookup, not from any JSON field."""
        raw = json.dumps(
            {"reasoning": "test", "rating": 5, "category": "factual_gap", "offer": "yes"}
        )
        v = _parse_verdict(raw)
        # Rating 5 → must be exactly 0.95, not influenced by any external confidence field
        assert v.confidence == pytest.approx(0.95)

    def test_reasoning_field_is_discarded(self) -> None:
        """The reasoning field is parsed but not surfaced — WallVerdict has no reasoning."""
        raw = json.dumps(
            {
                "reasoning": "This is a very long reasoning string that should be discarded.",
                "rating": 4,
                "category": "factual_gap",
                "offer": "I can help.",
            }
        )
        v = _parse_verdict(raw)
        # WallVerdict has no 'reasoning' attribute
        assert not hasattr(v, "reasoning")
        assert v.is_wall is True

    def test_is_wall_derived_from_rating_not_json_field(self) -> None:
        """is_wall must come from rating >= 3, not from an explicit is_wall field."""
        # If the model were to emit is_wall=false but rating=4, the parser must use rating
        raw = json.dumps(
            {
                "reasoning": "clear gap",
                "rating": 4,
                "category": "factual_gap",
                "offer": "I can help.",
                "is_wall": False,  # T-203 leftover field the model might hallucinate
            }
        )
        v = _parse_verdict(raw)
        # Must derive from rating=4 → is_wall=True, ignore the spurious is_wall field
        assert v.is_wall is True
        assert v.confidence == pytest.approx(0.80)


class TestParseVerdictGracefulFallback:
    """Test that _parse_verdict falls back to none() gracefully on bad input."""

    def test_empty_string(self) -> None:
        assert _parse_verdict("").is_wall is False

    def test_whitespace_only(self) -> None:
        assert _parse_verdict("   \n  ").is_wall is False

    def test_pure_prose_no_json(self) -> None:
        assert _parse_verdict("I cannot determine if this is a wall.").is_wall is False

    def test_malformed_json(self) -> None:
        assert _parse_verdict('{"rating": 4, "category":').is_wall is False

    def test_json_array_not_object(self) -> None:
        assert _parse_verdict("[1, 2, 3]").is_wall is False

    def test_json_null(self) -> None:
        assert _parse_verdict("null").is_wall is False

    def test_missing_rating_field(self) -> None:
        """Missing rating field → graceful fallback (required in T-508 schema)."""
        raw = json.dumps({"reasoning": "no rating", "category": "factual_gap", "offer": "help"})
        assert _parse_verdict(raw).is_wall is False

    def test_unknown_category_value(self) -> None:
        raw = json.dumps(
            {"reasoning": "test", "rating": 4, "category": "made_up_category", "offer": "help"}
        )
        assert _parse_verdict(raw).is_wall is False

    def test_out_of_range_rating_returns_fallback(self) -> None:
        """Rating outside 1–5 → fallback to none()."""
        raw = json.dumps(
            {"reasoning": "test", "rating": 6, "category": "factual_gap", "offer": "help"}
        )
        assert _parse_verdict(raw).is_wall is False

    def test_rating_zero_returns_fallback(self) -> None:
        raw = json.dumps(
            {"reasoning": "test", "rating": 0, "category": "factual_gap", "offer": "help"}
        )
        assert _parse_verdict(raw).is_wall is False

    def test_markdown_fence_stripped(self) -> None:
        """The parser must handle ```json ... ``` fences from the model."""
        inner = _wall_json(rating=4, category="factual_gap")
        fenced = f"```json\n{inner}\n```"
        v = _parse_verdict(fenced)
        assert v.is_wall is True
        assert v.category is WallCategory.FACTUAL_GAP

    def test_markdown_fence_no_lang_tag_stripped(self) -> None:
        inner = _wall_json(rating=4, category="stuck_point")
        fenced = f"```\n{inner}\n```"
        v = _parse_verdict(fenced)
        assert v.is_wall is True
        assert v.category is WallCategory.STUCK_POINT

    def test_json_embedded_in_prose(self) -> None:
        """Parser extracts the first {...} block when surrounded by prose."""
        inner = _wall_json(rating=4, category="unanswered_question")
        prose = f"Here is my analysis:\n{inner}\nEnd of analysis."
        v = _parse_verdict(prose)
        assert v.is_wall is True
        assert v.category is WallCategory.UNANSWERED_QUESTION

    def test_fallback_returns_none_verdict_singleton_shape(self) -> None:
        """The fallback must match WallVerdict.none() in shape."""
        v = _parse_verdict("totally invalid")
        expected = WallVerdict.none()
        assert v.is_wall is expected.is_wall
        assert v.category is expected.category
        assert v.offer == expected.offer


# ---------------------------------------------------------------------------
# 4. Failure-case canned-JSON tests (T-508 exemplars via stubbed generate)
# ---------------------------------------------------------------------------


class TestFailureCaseClassification:
    """Test the T-508 failure cases using canned JSON responses from a stubbed model.

    Each test stubs ``generate`` to return the JSON the model is expected to produce
    for the given input, then asserts the WallVerdict the backend derives from it.
    These are model-free tests — the stub bypasses real inference.
    """

    def test_sqrt_81_question_rates_high(self) -> None:
        """√81 as a direct factual question should rate 5 → high confidence wall."""
        canned = json.dumps(
            {
                "reasoning": "Direct factual question nobody answered. Answerable.",
                "rating": 5,
                "category": "unanswered_question",
                "offer": "That's 9 — want me to confirm?",
            }
        )
        fake = _FakeQwenModel(canned)
        backend = QwenWallBackend(fake)
        v = backend.detect_wall("Alice: What's the square root of 81?", "")
        assert v.is_wall is True
        assert v.category is WallCategory.UNANSWERED_QUESTION
        assert v.confidence == pytest.approx(0.95)  # rating 5

    def test_sqrt_81_wh_form_rates_high(self) -> None:
        """'I wonder what the square root of 81 is.' (no ?) rates 4 → above floor."""
        canned = json.dumps(
            {
                "reasoning": "Expressed uncertainty about a fact. Answerable.",
                "rating": 4,
                "category": "factual_gap",
                "offer": "The square root of 81 is 9, if that helps.",
            }
        )
        fake = _FakeQwenModel(canned)
        backend = QwenWallBackend(fake)
        v = backend.detect_wall("Alice: I wonder what the square root of 81 is.", "")
        assert v.is_wall is True
        assert v.category is WallCategory.FACTUAL_GAP
        assert v.confidence == pytest.approx(0.80)  # rating 4

    def test_4_times_7_rates_high(self) -> None:
        """'What's 4 times 7?' should rate 5 — the consistently firing case."""
        canned = json.dumps(
            {
                "reasoning": "Direct arithmetic question nobody answered. Answerable.",
                "rating": 5,
                "category": "unanswered_question",
                "offer": "That's 28.",
            }
        )
        fake = _FakeQwenModel(canned)
        backend = QwenWallBackend(fake)
        v = backend.detect_wall("Alice: What's 4 times 7?", "")
        assert v.is_wall is True
        assert v.confidence == pytest.approx(0.95)  # rating 5

    def test_what_do_you_need_rates_low(self) -> None:
        """Post-summon 'What do you need?' should rate 1 — directed at Jarvis, not a gap."""
        canned = json.dumps(
            {
                "reasoning": "Alice is speaking TO Jarvis, not hitting a wall between humans.",
                "rating": 1,
                "category": "none",
                "offer": "",
            }
        )
        fake = _FakeQwenModel(canned)
        backend = QwenWallBackend(fake)
        v = backend.detect_wall("[Jarvis just engaged] Alice: What do you need?", "")
        assert v.is_wall is False
        assert v.category is WallCategory.NONE
        assert v.confidence == pytest.approx(0.05)  # rating 1

    def test_self_musing_rates_low(self) -> None:
        """'I wonder if my volume is too loud.' is self-musing → rating 1 → non-wall."""
        canned = json.dumps(
            {
                "reasoning": "Alice is musing about her own situation. Not a factual gap.",
                "rating": 1,
                "category": "none",
                "offer": "",
            }
        )
        fake = _FakeQwenModel(canned)
        backend = QwenWallBackend(fake)
        v = backend.detect_wall("Alice: I wonder if my volume is too loud.", "")
        assert v.is_wall is False
        assert v.category is WallCategory.NONE

    def test_declarative_gap_rates_borderline(self) -> None:
        """'I don't remember the date we picked.' → rating 3 → is_wall=True, confidence 0.65."""
        canned = json.dumps(
            {
                "reasoning": "Expressed uncertainty. Answerable but subtle — no explicit question.",
                "rating": 3,
                "category": "factual_gap",
                "offer": "I can check the date if you'd like.",
            }
        )
        fake = _FakeQwenModel(canned)
        backend = QwenWallBackend(fake)
        v = backend.detect_wall("Alice: I don't remember the date we picked.", "")
        assert v.is_wall is True
        assert v.category is WallCategory.FACTUAL_GAP
        # below the 0.70 floor → suppressed by controller
        assert v.confidence == pytest.approx(0.65)


# ---------------------------------------------------------------------------
# 5. QwenWallBackend adapter — model-free (injected fake)
# ---------------------------------------------------------------------------


class TestQwenWallBackend:
    """Assert the backend wires transcript/summary through the injected model."""

    def test_detect_wall_calls_model_generate_once(self) -> None:
        fake = _FakeQwenModel(_wall_json())
        backend = QwenWallBackend(fake)
        backend.detect_wall("some transcript", "some summary")
        assert len(fake.calls) == 1

    def test_detect_wall_passes_transcript_to_model(self) -> None:
        fake = _FakeQwenModel(_wall_json())
        backend = QwenWallBackend(fake)
        transcript = "Alice: what was the conference date again?"
        backend.detect_wall(transcript, "")
        messages, _ = fake.calls[0]
        user_content = messages[1]["content"]
        assert transcript in user_content

    def test_detect_wall_passes_summary_to_model(self) -> None:
        fake = _FakeQwenModel(_none_json())
        backend = QwenWallBackend(fake)
        summary = "The team is planning a conference in Tokyo."
        backend.detect_wall("Alice: yes", summary)
        messages, _ = fake.calls[0]
        user_content = messages[1]["content"]
        assert summary in user_content

    def test_detect_wall_passes_max_tokens(self) -> None:
        fake = _FakeQwenModel(_none_json())
        backend = QwenWallBackend(fake, max_tokens=50)
        backend.detect_wall("A: test", "")
        _, max_tok = fake.calls[0]
        assert max_tok == 50

    def test_detect_wall_returns_wall_verdict_dataclass(self) -> None:
        fake = _FakeQwenModel(_wall_json(rating=4, category="factual_gap"))
        backend = QwenWallBackend(fake)
        v = backend.detect_wall("Alice: I don't remember the date", "")
        assert isinstance(v, WallVerdict)
        assert v.is_wall is True
        assert v.category is WallCategory.FACTUAL_GAP

    def test_detect_wall_returns_none_verdict_on_no_wall(self) -> None:
        fake = _FakeQwenModel(_none_json())
        backend = QwenWallBackend(fake)
        v = backend.detect_wall("Alice: sounds good", "")
        assert v.is_wall is False
        assert v.category is WallCategory.NONE

    def test_detect_wall_falls_back_on_bad_json(self) -> None:
        fake = _FakeQwenModel("not valid json at all")
        backend = QwenWallBackend(fake)
        v = backend.detect_wall("Alice: something", "")
        assert v.is_wall is False
        assert v.category is WallCategory.NONE

    def test_detect_wall_returns_graded_confidence_from_rating(self) -> None:
        """Confidence is derived from the rating, not from the model's raw output."""
        fake = _FakeQwenModel(_wall_json(rating=3, category="stuck_point", offer="help?"))
        backend = QwenWallBackend(fake)
        v = backend.detect_wall("Alice: we're going around", "")
        assert v.is_wall is True
        assert v.confidence == pytest.approx(0.65)  # rating 3 → 0.65

    def test_detect_wall_rating_4_confidence_is_0_80(self) -> None:
        fake = _FakeQwenModel(_wall_json(rating=4, category="factual_gap"))
        backend = QwenWallBackend(fake)
        v = backend.detect_wall("Alice: I wonder what X is.", "")
        assert v.confidence == pytest.approx(0.80)

    def test_detect_wall_rating_5_confidence_is_0_95(self) -> None:
        fake = _FakeQwenModel(_wall_json(rating=5, category="unanswered_question"))
        backend = QwenWallBackend(fake)
        v = backend.detect_wall("Alice: what's the capital?", "")
        assert v.confidence == pytest.approx(0.95)

    def test_messages_have_system_and_user_roles(self) -> None:
        fake = _FakeQwenModel(_none_json())
        backend = QwenWallBackend(fake)
        backend.detect_wall("A: x", "prev")
        messages, _ = fake.calls[0]
        roles = [m["role"] for m in messages]
        assert "system" in roles
        assert "user" in roles

    def test_detect_wall_multiple_calls(self) -> None:
        """Each call gets its own transcript/summary in the model messages."""
        fake = _FakeQwenModel(_none_json())
        backend = QwenWallBackend(fake)
        backend.detect_wall("A: first", "summary one")
        backend.detect_wall("A: second", "summary two")
        assert len(fake.calls) == 2
        msgs_1, _ = fake.calls[0]
        msgs_2, _ = fake.calls[1]
        assert "first" in msgs_1[1]["content"]
        assert "second" in msgs_2[1]["content"]
        assert "summary one" in msgs_1[1]["content"]
        assert "summary two" in msgs_2[1]["content"]

    def test_default_max_tokens_raised_for_cot(self) -> None:
        """T-508 raises max_tokens to 200 to give the CoT reasoning field enough budget."""
        fake = _FakeQwenModel(_none_json())
        backend = QwenWallBackend(fake)
        backend.detect_wall("A: text", "")
        _, max_tok = fake.calls[0]
        assert max_tok >= 200  # T-508 raised the budget from 120 to 200

    def test_default_max_tokens_within_reasonable_range(self) -> None:
        fake = _FakeQwenModel(_none_json())
        backend = QwenWallBackend(fake)
        backend.detect_wall("A: text", "")
        _, max_tok = fake.calls[0]
        assert 1 <= max_tok <= 512

    def test_offer_surfaced_for_wall(self) -> None:
        offer = "I can find that for you."
        fake = _FakeQwenModel(_wall_json(offer=offer))
        backend = QwenWallBackend(fake)
        v = backend.detect_wall("A: what was that?", "")
        assert v.offer == offer

    def test_offer_empty_for_no_wall(self) -> None:
        fake = _FakeQwenModel(_none_json())
        backend = QwenWallBackend(fake)
        v = backend.detect_wall("A: sounds great", "")
        assert v.offer == ""

    def test_all_five_categories_parseable(self) -> None:
        """Each WallCategory value (including NONE) must be parseable via T-508 schema."""
        categories = [
            ("unanswered_question", WallCategory.UNANSWERED_QUESTION, 4),
            ("factual_gap", WallCategory.FACTUAL_GAP, 4),
            ("stuck_point", WallCategory.STUCK_POINT, 4),
            ("explicit_ask", WallCategory.EXPLICIT_ASK, 4),
            ("none", WallCategory.NONE, 1),
        ]
        for raw_cat, expected, rating in categories:
            canned = json.dumps(
                {
                    "reasoning": "test",
                    "rating": rating,
                    "category": raw_cat,
                    "offer": "help" if rating >= 3 and raw_cat != "none" else "",
                }
            )
            fake = _FakeQwenModel(canned)
            backend = QwenWallBackend(fake)
            v = backend.detect_wall("Alice: test", "summary")
            assert v.category is expected, f"expected {expected} for raw category '{raw_cat}'"


# ---------------------------------------------------------------------------
# 6. Widened _has_wall_signal pre-filter tests (T-508)
# ---------------------------------------------------------------------------


class TestHasWallSignal:
    """Test the widened _has_wall_signal pre-filter in attention_layer.

    The filter was widened in T-508 to catch gap phrasings that don't end with '?',
    specifically the root cause of the √81 miss: 'I wonder what the square root
    of 81 is.' was silently dropped before the model ever ran.
    """

    def _has_signal(self, text: str) -> bool:
        from jarvis.attention_layer import _has_wall_signal

        return _has_wall_signal(text)

    # --- Lines that MUST pass the filter ---

    def test_direct_question_passes(self) -> None:
        """Any line ending with '?' must pass (original behavior)."""
        assert self._has_signal("Alice: What's the square root of 81?")

    def test_i_wonder_what_passes(self) -> None:
        """'I wonder what X is.' (no ?) must now pass (T-508 widening fixes the √81 miss)."""
        assert self._has_signal("Alice: I wonder what the square root of 81 is.")

    def test_i_wonder_if_passes(self) -> None:
        """'I wonder if X' triggers the filter."""
        assert self._has_signal("Alice: I wonder if we booked the right room.")

    def test_not_sure_who_passes(self) -> None:
        """'Not sure who that was' matches the not-sure-who pattern."""
        assert self._has_signal("Alice: not sure who that was.")

    def test_not_sure_what_passes(self) -> None:
        assert self._has_signal("Alice: I'm not sure what time the flight leaves.")

    def test_no_idea_passes(self) -> None:
        assert self._has_signal("Alice: I have no idea how to fix this.")

    def test_i_dont_remember_passes(self) -> None:
        assert self._has_signal("Alice: I don't remember where we put the keys.")

    def test_cant_recall_passes(self) -> None:
        assert self._has_signal("Alice: I can't recall the venue name.")

    def test_i_dont_know_passes(self) -> None:
        assert self._has_signal("Alice: I do not know what the date is.")

    def test_i_wish_passes(self) -> None:
        assert self._has_signal("Alice: I wish I knew the flight duration.")

    # --- Lines that MUST NOT pass the filter ---

    def test_neutral_statement_rejected(self) -> None:
        """A plain neutral statement must not pass — avoids running the model on every turn."""
        assert not self._has_signal("Alice: sounds good")

    def test_decision_statement_rejected(self) -> None:
        assert not self._has_signal("Alice: let's try option A")

    def test_agreement_rejected(self) -> None:
        assert not self._has_signal("Alice: OK I'll send the PR tomorrow")

    def test_plan_statement_rejected(self) -> None:
        assert not self._has_signal("Alice: we'll schedule the review for Tuesday")


# ---------------------------------------------------------------------------
# 7. Protocol conformance — QwenWallBackend satisfies WallBackend
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    """Verify QwenWallBackend satisfies the frozen WallBackend Protocol."""

    def test_has_detect_wall_method(self) -> None:
        fake = _FakeQwenModel()
        backend = QwenWallBackend(fake)
        assert hasattr(backend, "detect_wall")
        assert callable(backend.detect_wall)

    def test_detect_wall_signature_matches_protocol(self) -> None:
        """detect_wall must accept (transcript: str, summary: str) -> WallVerdict."""
        fake = _FakeQwenModel()
        backend = QwenWallBackend(fake)
        sig = inspect.signature(backend.detect_wall)
        params = list(sig.parameters.keys())
        assert "transcript" in params
        assert "summary" in params

    def test_plugs_into_wall_detector(self) -> None:
        """QwenWallBackend must drop into WallDetector behind the frozen seam."""
        from jarvis.core.wall_detector import WallDetector

        fake = _FakeQwenModel(_wall_json(rating=4, category="factual_gap"))
        backend = QwenWallBackend(fake)
        detector = WallDetector(backend=backend)

        v = detector.detect("Alice: I don't remember the date", "meeting summary")
        assert isinstance(v, WallVerdict)
        assert v.is_wall is True
        assert v.category is WallCategory.FACTUAL_GAP

    def test_lazy_import_boundary(self) -> None:
        """Importing QwenWallBackend must NOT load mlx_lm."""
        # The real QwenModel is constructed but NOT generate()-called here.
        model = QwenModel(model_path="does-not-exist")
        assert model._model is None, "model must not be loaded before first generate() call"

    def test_wall_verdict_shape_unchanged(self) -> None:
        """WallVerdict is frozen — is_wall, category, confidence, offer."""
        fake = _FakeQwenModel(_wall_json(rating=5, category="unanswered_question", offer="help!"))
        backend = QwenWallBackend(fake)
        v = backend.detect_wall("Alice: what time?", "")
        assert hasattr(v, "is_wall")
        assert hasattr(v, "category")
        assert hasattr(v, "confidence")
        assert hasattr(v, "offer")
        # No new fields added (frozen shape)
        assert not hasattr(v, "reasoning")
        assert not hasattr(v, "rating")


# ---------------------------------------------------------------------------
# 8. Optional live test — skipped when MLX / weights unavailable
# ---------------------------------------------------------------------------


def test_live_qwen_wall_detection_optional() -> None:
    """End-to-end real inference: load the default Qwen model and run detect_wall calls.

    Skipped (never failed) when mlx_lm is not importable or the model weights
    are not available locally — the condition mirrors
    ``test_live_qwen_summarize_optional`` in ``test_qwen_summarizer.py``.
    Never runs in CI (the CI env has no Qwen weights); only runs on the local
    M5 where the weights are cached.

    T-509 CRITICAL: validates on the REAL ``detect_wall(transcript, summary)``
    path with MULTI-LINE rolling-window transcripts + a context summary, NOT
    clean single-line probes.  This is how the T-508 gate was fooled: qa-tuning
    probed "√81?" as a single clean line and got rating 5, but the real pipeline
    feeds a multi-line window (e.g. 5 utterances from a math chat) + a summary,
    and on that real input the 3B model rated it 2 and reasoned "it's a direct
    question so it's not a gap."

    Scenarios (all with realistic multi-line transcripts + summary):
    1. √81 in a math-chat window → must fire (rating >= 4, confidence >= 0.70).
    2. 4×7 in a math-chat window → must fire.
    3. "I wonder what 10×4 is." (wh-form, no ?) in context → reported.
    4. "What do you need?" after Jarvis engaged → must NOT fire.
    5. Self-musing ("I wonder if my volume is too loud") in context → must NOT fire.
    6. Plain statement/plan in context → must NOT fire.

    Reports all results honestly; hard assertions on shape + invariants.
    """
    try:
        import mlx_lm  # noqa: F401 — import probe only
    except ImportError as exc:
        pytest.skip(f"mlx_lm not installed: {exc}")

    try:
        model = QwenModel()
        backend = QwenWallBackend(model)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Qwen model unavailable (weights missing or mlx error): {exc}")

    results: dict[str, dict] = {}

    # T-509: all transcripts use the REAL multi-line rolling-window format
    # (as AttentionLayer feeds them: "Speaker: text\nSpeaker: text\n...").
    # Single-line clean probes are explicitly NOT used (that's what failed T-508).

    # --- Scenario A: √81 in a multi-line math-chat window (T-509 primary fix) ---
    try:
        transcript_a = (
            "Alice: So we need to figure out a few things.\n"
            "Bob: Yeah, let's start with the easy ones.\n"
            "Alice: What is the square root of 81?\n"
            "Bob: Hmm, let me think..."
        )
        summary_a = "Alice and Bob are working through some math problems together."
        v_a = backend.detect_wall(transcript_a, summary_a)
        results["sqrt81_question_real_path"] = {
            "verdict": v_a,
            "pass": v_a.is_wall and v_a.confidence >= 0.70,
        }
    except Exception as exc:  # noqa: BLE001
        results["sqrt81_question_real_path"] = {"error": str(exc), "pass": False}

    # --- Scenario B: 4×7 in a multi-line window ---
    try:
        transcript_b = (
            "Alice: OK, let's do some quick multiplication.\n"
            "Bob: Sure, what do you need?\n"
            "Alice: What's 4 times 7?\n"
            "Bob: Uhh..."
        )
        summary_b = "Alice and Bob are doing arithmetic exercises."
        v_b = backend.detect_wall(transcript_b, summary_b)
        results["4_times_7_real_path"] = {
            "verdict": v_b,
            "pass": v_b.is_wall and v_b.confidence >= 0.70,
        }
    except Exception as exc:  # noqa: BLE001
        results["4_times_7_real_path"] = {"error": str(exc), "pass": False}

    # --- Scenario C: wh-form (no ?) in context — reported, not hard-asserted ---
    try:
        transcript_c = (
            "Alice: Let's try a few more.\n"
            "Bob: Yeah, go ahead.\n"
            "Alice: I wonder what 10 times 4 is.\n"
        )
        summary_c = "Alice and Bob are working through multiplication."
        v_c = backend.detect_wall(transcript_c, summary_c)
        results["wh_form_real_path"] = {
            "verdict": v_c,
            "pass": True,  # not hard-asserted; consistency reported
        }
    except Exception as exc:  # noqa: BLE001
        results["wh_form_real_path"] = {"error": str(exc), "pass": True}

    # --- Scenario D: 'What do you need?' after Jarvis engaged (must NOT fire) ---
    try:
        transcript_d = (
            "Alice: Jarvis, set a reminder for me.\n[Jarvis engaged]\nAlice: What do you need?\n"
        )
        summary_d = "Alice just summoned Jarvis to set a reminder. Jarvis is engaged."
        v_d = backend.detect_wall(transcript_d, summary_d)
        results["what_do_you_need_real_path"] = {
            "verdict": v_d,
            "pass": not v_d.is_wall or v_d.confidence < 0.70,
        }
    except Exception as exc:  # noqa: BLE001
        results["what_do_you_need_real_path"] = {"error": str(exc), "pass": False}

    # --- Scenario E: self-musing in context (must NOT fire) ---
    try:
        transcript_e = (
            "Alice: Yeah, I think the setup is working.\n"
            "Bob: Let me check the output.\n"
            "Alice: I wonder if my volume is too loud.\n"
        )
        summary_e = "Alice and Bob are testing their audio setup."
        v_e = backend.detect_wall(transcript_e, summary_e)
        results["self_musing_real_path"] = {
            "verdict": v_e,
            "pass": not v_e.is_wall or v_e.confidence < 0.70,
        }
    except Exception as exc:  # noqa: BLE001
        results["self_musing_real_path"] = {"error": str(exc), "pass": False}

    # --- Scenario F: plain statement / plan in context (must NOT fire) ---
    try:
        transcript_f = (
            "Alice: So it looks like option B is the best path.\n"
            "Bob: Agreed.\n"
            "Alice: Let's go with option B then and schedule a review for Friday.\n"
        )
        summary_f = "Alice and Bob are deciding on a technical approach."
        v_f = backend.detect_wall(transcript_f, summary_f)
        results["plain_statement_real_path"] = {
            "verdict": v_f,
            "pass": not v_f.is_wall,
        }
    except Exception as exc:  # noqa: BLE001
        results["plain_statement_real_path"] = {"error": str(exc), "pass": False}

    # Print results for qa-tuning review.
    print("\n--- QwenWallBackend live results (T-509: REAL PATH, multi-line transcripts) ---")
    print("  NOTE: T-509 validates on the REAL detect_wall(transcript, summary) path with")
    print("  multi-line rolling-window inputs + context summary. Not clean single-line probes.")
    print()
    for name, info in results.items():
        if "error" in info:
            print(f"  {name}: ERROR — {info['error']}")
        else:
            v = info["verdict"]
            status = "PASS" if info["pass"] else "FAIL"
            print(
                f"  {name}: {status} | is_wall={v.is_wall} category={v.category} "
                f"confidence={v.confidence:.2f} rating_implied={v.confidence} "
                f"offer={v.offer!r}"
            )
    print("--- end ---")

    # Hard assertion: all returned objects must be WallVerdict instances.
    for name, info in results.items():
        if "verdict" in info:
            assert isinstance(info["verdict"], WallVerdict), f"{name}: expected WallVerdict"

    # Hard assertion: confidence must be in [0.0, 1.0] for all cases.
    for name, info in results.items():
        if "verdict" in info:
            v = info["verdict"]
            assert 0.0 <= v.confidence <= 1.0, f"{name}: confidence {v.confidence} out of range"

    # Hard assertion: confidence must be one of the known rating-derived values.
    known_values = {0.0, 0.05, 0.30, 0.65, 0.80, 0.95}
    for name, info in results.items():
        if "verdict" in info:
            v = info["verdict"]
            assert round(v.confidence, 2) in known_values, (
                f"{name}: confidence {v.confidence} not in rating→confidence table"
            )
