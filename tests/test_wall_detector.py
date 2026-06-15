"""Tests for WallDetector + the heuristic mock backend (T-005).

Two surfaces, tested separately through their public interfaces:

* **WallDetector** — the thin seam wrapper. Proves it surfaces whatever the
  injected backend returns (each category and ``none``, with confidence carried
  through unchanged) and passes the transcript/summary it was given to the
  backend. Driven with the harness ``FakeWallBackend`` — no real heuristic, no
  model — so it pins the *contract*, not the judgement.
* **HeuristicWallBackend** — the Phase-0 backend's own behavior: which cue maps
  to which category, the priority order, and the ``none`` default.

Per the eval-plan golden rule, assertions are on the returned ``WallVerdict``
and on what the backend was called with — never on internals.
"""

from __future__ import annotations

import pytest

from jarvis.core.wall_detector import HeuristicWallBackend, WallDetector
from jarvis.types import WallCategory, WallVerdict
from tests.fakes import FakeWallBackend, no_wall, wall

# ---------------------------------------------------------------------------
# WallDetector — surfaces the backend's verdict (each category + none)
# ---------------------------------------------------------------------------

# One scripted verdict per wall category, plus the non-wall verdict — every
# value a WallBackend can return, each with a distinct confidence so the test
# proves the *confidence is surfaced* (not defaulted/clamped by the detector).
_CATEGORY_CASES = [
    (WallCategory.UNANSWERED_QUESTION, 0.72),
    (WallCategory.FACTUAL_GAP, 0.80),
    (WallCategory.STUCK_POINT, 0.74),
    (WallCategory.EXPLICIT_ASK, 0.78),
]


@pytest.mark.parametrize(("category", "confidence"), _CATEGORY_CASES)
def test_detector_surfaces_each_wall_category_and_confidence(category, confidence):
    backend = FakeWallBackend(verdict=wall(category, confidence, offer="an offer"))
    detector = WallDetector(backend=backend)

    v = detector.detect("some transcript", "the summary")

    assert v.is_wall is True
    assert v.category is category
    assert v.confidence == confidence  # surfaced unchanged
    assert v.offer == "an offer"


def test_detector_surfaces_the_none_verdict():
    detector = WallDetector(backend=FakeWallBackend(verdict=no_wall()))

    v = detector.detect("just small talk", "")

    assert v.is_wall is False
    assert v.category is WallCategory.NONE
    assert v.confidence == 0.0
    assert v.offer == ""


def test_detector_default_backend_verdict_is_none():
    # FakeWallBackend with no script returns a 'none' verdict.
    detector = WallDetector(backend=FakeWallBackend())
    assert detector.detect("anything", "").is_wall is False


def test_detector_passes_transcript_and_summary_to_backend():
    backend = FakeWallBackend()
    detector = WallDetector(backend=backend)

    detector.detect("the transcript", "the summary")

    # Assert on what crossed the seam, not on internals.
    assert backend.transcripts == ["the transcript"]
    assert backend.summaries == ["the summary"]
    assert backend.call_count == 1


def test_detector_does_not_apply_a_confidence_threshold():
    # A low-confidence wall is surfaced as a wall — the decision to *speak* on the
    # confidence belongs to SummonController (T-007), not the detector. The
    # detector must not silently downgrade a wall it judges low-confidence.
    backend = FakeWallBackend(verdict=wall(WallCategory.UNANSWERED_QUESTION, 0.10))
    detector = WallDetector(backend=backend)

    v = detector.detect("q?", "")

    assert v.is_wall is True
    assert v.confidence == 0.10


def test_detector_surfaces_a_scripted_sequence_in_order():
    backend = FakeWallBackend(verdicts=[no_wall(), wall(WallCategory.FACTUAL_GAP, 0.80), no_wall()])
    detector = WallDetector(backend=backend)

    assert detector.detect("a", "").category is WallCategory.NONE
    assert detector.detect("b", "").category is WallCategory.FACTUAL_GAP
    assert detector.detect("c", "").category is WallCategory.NONE


# ---------------------------------------------------------------------------
# HeuristicWallBackend — the Phase-0 backend's own cue → category behavior
# ---------------------------------------------------------------------------


@pytest.fixture
def heuristic() -> HeuristicWallBackend:
    return HeuristicWallBackend()


def _detect(backend: HeuristicWallBackend, line: str) -> WallVerdict:
    """Detect over a one-line transcript (with a speaker prefix, as the window
    renders it)."""
    return backend.detect_wall(f"Alex: {line}", "")


def test_heuristic_unanswered_question(heuristic):
    v = _detect(heuristic, "so what time does the keynote start?")
    assert v.is_wall is True
    assert v.category is WallCategory.UNANSWERED_QUESTION
    assert 0.0 <= v.confidence <= 1.0
    assert v.offer  # non-empty offer line


def test_heuristic_factual_gap(heuristic):
    v = _detect(heuristic, "honestly I don't remember which week we picked")
    assert v.is_wall is True
    assert v.category is WallCategory.FACTUAL_GAP


def test_heuristic_factual_gap_what_was(heuristic):
    v = _detect(heuristic, "what was the conference date again")
    assert v.is_wall is True
    assert v.category is WallCategory.FACTUAL_GAP


def test_heuristic_stuck_point(heuristic):
    v = _detect(heuristic, "we're just going in circles on this")
    assert v.is_wall is True
    assert v.category is WallCategory.STUCK_POINT


def test_heuristic_explicit_ask(heuristic):
    v = _detect(heuristic, "I wish I knew how long the flight is")
    assert v.is_wall is True
    assert v.category is WallCategory.EXPLICIT_ASK


def test_heuristic_none_on_plain_statement(heuristic):
    v = _detect(heuristic, "the weather is lovely today")
    assert v.is_wall is False
    assert v.category is WallCategory.NONE
    assert v.confidence == 0.0
    assert v.offer == ""


def test_heuristic_none_on_empty_transcript(heuristic):
    v = heuristic.detect_wall("", "")
    assert v.is_wall is False
    assert v.category is WallCategory.NONE


def test_heuristic_none_on_blank_lines_only(heuristic):
    v = heuristic.detect_wall("\n   \n\n", "")
    assert v.is_wall is False


def test_heuristic_looks_at_the_last_line_only(heuristic):
    # An earlier question is not the *fresh* wall — only the last line is judged.
    transcript = "Alex: what time is it?\nSam: it's three o'clock."
    v = heuristic.detect_wall(transcript, "")
    assert v.is_wall is False  # last line is a plain answer, not a wall


def test_heuristic_priority_explicit_ask_beats_question_mark(heuristic):
    # A line that is both a wish and a question resolves to the stronger intent
    # (explicit_ask), not the bare unanswered_question — the priority order.
    v = _detect(heuristic, "I wish I knew when the flight leaves?")
    assert v.category is WallCategory.EXPLICIT_ASK


def test_heuristic_priority_factual_gap_beats_question_mark(heuristic):
    v = _detect(heuristic, "what was the gate number?")
    assert v.category is WallCategory.FACTUAL_GAP


# ---------------------------------------------------------------------------
# HeuristicWallBackend satisfies the WallBackend seam (plugs into WallDetector)
# ---------------------------------------------------------------------------


def test_heuristic_backend_plugs_into_the_detector():
    detector = WallDetector(backend=HeuristicWallBackend())

    silent = detector.detect("Alex: nice day", "")
    asked = detector.detect("Alex: what was the date again?", "")

    assert silent.is_wall is False
    assert asked.is_wall is True
    assert asked.category is WallCategory.FACTUAL_GAP
