"""Tests for LivingSummary (T-004).

Proves the "redraw only the changed pixels" contract through the public
interface (``consider_update`` / ``text``):

* **refresh on shift** — a topic change triggers exactly one re-summarize;
* **no refresh below the cold-start minimum** — a too-short conversation is left
  alone;
* **no refresh when there's no shift** — a continuing topic doesn't re-summarize;
* **the injected fake summarizer is what gets called** — asserted on
  ``FakeSummarizer``'s recorded calls (no live model anywhere).

Everything runs on the T-009 harness: a ``RollingWindow`` fed by a
``SimulatedClock``, and the injected ``FakeSummarizer`` seam.
"""

from __future__ import annotations

import pytest

from jarvis.core.living_summary import (
    MIN_UTTERANCES_FOR_SUMMARY,
    MIN_UTTERANCES_SINCE_UPDATE,
    LivingSummary,
)
from jarvis.core.rolling_window import RollingWindow
from jarvis.core.topic_shift import TopicShiftDetector
from jarvis.types import Utterance
from tests.clock import SimulatedClock
from tests.fakes import FakeSummarizer

# --- Test helpers ------------------------------------------------------------

# A run of lines on one topic, then a hard pivot to a second — modeled on the
# prototype's demo (flights/Tokyo → ramen restaurant). Worded so each topic's
# keyword sets barely overlap, well past the default 0.30 shift threshold.
_TOPIC_A = [
    ("Alex", "did you book the Tokyo flights for the October conference"),
    ("Sam", "not yet, the Tokyo flights keep slipping my mind"),
    ("Alex", "the October conference dates drive the Tokyo flight booking"),
    ("Sam", "right, book those Tokyo conference flights this week"),
]
_TOPIC_B = [
    ("Alex", "totally different — that new ramen place on fourth street"),
    ("Sam", "the tonkotsu ramen spot? incredible, fourth street is great"),
    ("Alex", "let's get ramen on fourth street this weekend, tonkotsu bowls"),
    ("Sam", "saturday ramen on fourth street, tonkotsu, sounds perfect"),
]


def _make_window(now, max_utterances: int = 12, max_seconds: float = 120.0) -> RollingWindow:
    return RollingWindow(max_utterances=max_utterances, max_seconds=max_seconds, now=now)


def _feed(
    window: RollingWindow,
    summary: LivingSummary,
    clock: SimulatedClock,
    lines: list[tuple[str, str]],
) -> list[bool]:
    """Add each line to the window and call ``consider_update``; return the
    per-line refresh verdicts."""
    verdicts: list[bool] = []
    for speaker, text in lines:
        window.add(Utterance(speaker=speaker, text=text, ts=clock.now()))
        verdicts.append(summary.consider_update(window))
        clock.advance(1.0)
    return verdicts


# --- Cold-start minimum: no refresh below it ---------------------------------


def test_no_refresh_below_cold_start_minimum():
    clock = SimulatedClock()
    window = _make_window(clock.now)
    fake = FakeSummarizer()
    summary = LivingSummary(fake)

    # Feed exactly one fewer utterance than the cold-start minimum.
    below = _TOPIC_A[: MIN_UTTERANCES_FOR_SUMMARY - 1]
    verdicts = _feed(clock=clock, window=window, summary=summary, lines=below)

    assert verdicts == [False] * (MIN_UTTERANCES_FOR_SUMMARY - 1)
    assert fake.called is False  # the model was never asked
    assert summary.text == ""


def test_first_refresh_fires_exactly_when_cold_start_clears():
    clock = SimulatedClock()
    window = _make_window(clock.now)
    fake = FakeSummarizer(return_value="SUMMARY-1")
    summary = LivingSummary(fake)

    verdicts = _feed(clock=clock, window=window, summary=summary, lines=_TOPIC_A)

    # First MIN-1 lines: cold start, no refresh. The MIN-th line clears it → first
    # refresh. Lines after that continue the same topic → no further refresh.
    assert verdicts[: MIN_UTTERANCES_FOR_SUMMARY - 1] == [False] * (MIN_UTTERANCES_FOR_SUMMARY - 1)
    assert verdicts[MIN_UTTERANCES_FOR_SUMMARY - 1] is True
    assert all(v is False for v in verdicts[MIN_UTTERANCES_FOR_SUMMARY:])
    assert fake.call_count == 1
    assert summary.text == "SUMMARY-1"


# --- No refresh when the topic continues -------------------------------------


def test_no_refresh_while_topic_continues():
    clock = SimulatedClock()
    window = _make_window(clock.now)
    fake = FakeSummarizer()
    summary = LivingSummary(fake)

    # An entire conversation on one topic refreshes exactly once (the cold-start
    # first summary) and never again — there's no shift to redraw.
    verdicts = _feed(clock=clock, window=window, summary=summary, lines=_TOPIC_A)

    assert verdicts.count(True) == 1
    assert fake.call_count == 1


# --- Refresh on a topic shift ------------------------------------------------


def test_refresh_on_topic_shift():
    clock = SimulatedClock()
    # Window of 4: once topic B's four lines are in, topic A has fully aged out by
    # count, so the live keyword set is B's — the basis (A) and current (B) no
    # longer overlap and the shift registers. This is how a window models "the
    # conversation has actually moved on" (vs. a brief tangent that rolls back).
    window = _make_window(clock.now, max_utterances=len(_TOPIC_B))
    fake = FakeSummarizer(returns=["SUMMARY-A", "SUMMARY-B"])
    summary = LivingSummary(fake)

    # Topic A establishes the first summary; topic B is a hard pivot → a shift
    # that triggers a second refresh.
    verdicts_a = _feed(clock=clock, window=window, summary=summary, lines=_TOPIC_A)
    verdicts_b = _feed(clock=clock, window=window, summary=summary, lines=_TOPIC_B)

    assert verdicts_a.count(True) == 1  # cold-start first summary
    assert verdicts_b.count(True) == 1  # the shift redraw
    assert fake.call_count == 2
    assert summary.text == "SUMMARY-B"


def test_shift_passes_new_transcript_and_prior_summary_to_backend():
    clock = SimulatedClock()
    window = _make_window(clock.now, max_utterances=len(_TOPIC_B))
    fake = FakeSummarizer(returns=["SUMMARY-A", "SUMMARY-B"])
    summary = LivingSummary(fake)

    _feed(clock=clock, window=window, summary=summary, lines=_TOPIC_A)
    _feed(clock=clock, window=window, summary=summary, lines=_TOPIC_B)

    # The injected fake is exactly what was called — assert on its recorded args.
    # Call 1 (cold start): no prior summary. Call 2 (shift): prior summary = A.
    assert fake.prev_summaries == ["", "SUMMARY-A"]
    # Each summarize() saw the window transcript at that moment.
    assert "Tokyo" in fake.transcripts[0]
    assert "ramen" in fake.transcripts[1]


# --- The injected fake summarizer is what gets called ------------------------


def test_uses_only_the_injected_backend_no_live_model():
    clock = SimulatedClock()
    window = _make_window(clock.now)
    fake = FakeSummarizer()
    summary = LivingSummary(fake)

    _feed(clock=clock, window=window, summary=summary, lines=_TOPIC_A + _TOPIC_B)

    # Every summary text came from the fake (its default "summary#N" echo); the
    # number of refreshes equals the number of backend calls — nothing else
    # produced a summary.
    assert fake.called is True
    assert summary.text == f"summary#{fake.call_count}"


def test_backend_not_called_at_all_when_conversation_stays_cold():
    clock = SimulatedClock()
    window = _make_window(clock.now)
    fake = FakeSummarizer()
    summary = LivingSummary(fake)

    _feed(clock=clock, window=window, summary=summary, lines=_TOPIC_A[:1])

    assert fake.call_count == 0


# --- Debounce: ≥2 utterances since last update -------------------------------


def test_shift_within_debounce_window_does_not_refresh():
    clock = SimulatedClock()
    window = _make_window(clock.now)
    fake = FakeSummarizer()
    # A detector that flags *every* comparison as a shift, to isolate the debounce
    # (without it, every post-cold-start line would refresh).
    always_shift = TopicShiftDetector(threshold=1.0)
    summary = LivingSummary(fake, detector=always_shift)

    verdicts = _feed(clock=clock, window=window, summary=summary, lines=_TOPIC_A)

    # Cold start clears on line MIN and refreshes (resetting the since-update
    # counter). The very next line is a shift but only +1 since update → debounced.
    # The line after that is +2 → allowed to refresh again.
    first = MIN_UTTERANCES_FOR_SUMMARY - 1
    assert verdicts[first] is True  # cold-start refresh
    assert verdicts[first + 1] is False  # debounced (only 1 since update)
    # With MIN_UTTERANCES_SINCE_UPDATE == 2, the following line is allowed again.
    assert MIN_UTTERANCES_SINCE_UPDATE == 2
    if first + 2 < len(verdicts):
        assert verdicts[first + 2] is True


# --- Configuration / guards --------------------------------------------------


def test_custom_min_utterances_moves_the_cold_start_fence():
    clock = SimulatedClock()
    window = _make_window(clock.now)
    fake = FakeSummarizer()
    summary = LivingSummary(fake, min_utterances=2)

    verdicts = _feed(clock=clock, window=window, summary=summary, lines=_TOPIC_A)

    # Fence at 2: first line cold, second line clears it → first refresh on line 2.
    assert verdicts[0] is False
    assert verdicts[1] is True


def test_default_detector_is_constructed_when_not_injected():
    summary = LivingSummary(FakeSummarizer())
    # Smoke: it built a usable detector and starts with no summary / empty basis.
    assert summary.text == ""


def test_rejects_bad_min_utterances():
    with pytest.raises(ValueError):
        LivingSummary(FakeSummarizer(), min_utterances=0)


def test_text_starts_empty():
    assert LivingSummary(FakeSummarizer()).text == ""
