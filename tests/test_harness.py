"""Self-tests for the T-009 harness: the simulated clock and the seam fakes.

These prove the *harness itself* behaves — the clock advances deterministically
and is monotonic, and each fake both returns presets and records its calls.
They depend on nothing in ``jarvis`` (the core modules don't exist yet); they
keep the suite green and guard the scaffolding the T-002…T-008 tests rely on.
"""

from __future__ import annotations

import pytest

from tests.clock import SimulatedClock
from tests.fakes import (
    FakeResponder,
    FakeSummarizer,
    FakeVoice,
    FakeWallBackend,
    WallVerdictLike,
    no_wall,
    wall,
)


# --- SimulatedClock ---------------------------------------------------------
def test_clock_starts_at_zero_by_default() -> None:
    assert SimulatedClock().now() == 0.0


def test_clock_honors_explicit_start() -> None:
    assert SimulatedClock(start=100.0).now() == 100.0


def test_clock_advance_is_deterministic_and_cumulative() -> None:
    clock = SimulatedClock()
    clock.advance(0.5)
    clock.advance(2.0)
    assert clock.now() == 2.5
    # No real time passed — the value moved only because we moved it.


def test_clock_callable_and_monotonic_alias_read_same_value() -> None:
    clock = SimulatedClock()
    clock.advance(3.0)
    # The clock can be injected as a bare callable or via .now()/.monotonic().
    assert clock() == 3.0
    assert clock.now() == 3.0
    assert clock.monotonic() == 3.0


def test_injected_now_callable_sees_later_advances() -> None:
    # A module captures `clock.now` at construction; advancing later must be
    # visible through that captured reference (the whole point of injection).
    clock = SimulatedClock()
    now = clock.now
    assert now() == 0.0
    clock.advance(2.0)
    assert now() == 2.0


def test_clock_rejects_backwards_motion() -> None:
    clock = SimulatedClock(start=5.0)
    with pytest.raises(ValueError):
        clock.advance(-1.0)
    with pytest.raises(ValueError):
        clock.set(4.0)
    assert clock.now() == 5.0  # unchanged after rejected moves


def test_clock_set_jumps_forward() -> None:
    clock = SimulatedClock()
    clock.set(10.0)
    assert clock.now() == 10.0


# --- FakeSummarizer ---------------------------------------------------------
def test_fake_summarizer_default_returns_distinct_summaries() -> None:
    fs = FakeSummarizer()
    first = fs.summarize("a: hi", "")
    second = fs.summarize("a: hi\nb: yo", "prev")
    assert first != second  # a new summary each call → callers can detect refresh


def test_fake_summarizer_fixed_return_value() -> None:
    fs = FakeSummarizer(return_value="FIXED")
    assert fs.summarize("x", "") == "FIXED"
    assert fs.summarize("y", "FIXED") == "FIXED"


def test_fake_summarizer_scripted_returns() -> None:
    fs = FakeSummarizer(returns=["one", "two"])
    assert fs.summarize("a", "") == "one"
    assert fs.summarize("b", "one") == "two"
    with pytest.raises(AssertionError):
        fs.summarize("c", "two")  # script exhausted


def test_fake_summarizer_records_arguments() -> None:
    fs = FakeSummarizer()
    fs.summarize("transcript-1", "")
    fs.summarize("transcript-2", "summary#1")
    assert fs.call_count == 2
    assert fs.transcripts == ["transcript-1", "transcript-2"]
    assert fs.prev_summaries == ["", "summary#1"]


# --- FakeWallBackend --------------------------------------------------------
def test_fake_wall_backend_default_is_none_verdict() -> None:
    fb = FakeWallBackend()
    v = fb.detect_wall("a: weather's nice", "")
    assert v.is_wall is False
    assert v.category == "none"
    assert v.confidence == 0.0


def test_fake_wall_backend_single_verdict() -> None:
    v = wall("unanswered_question", 0.72, offer="I can answer that — shall I?")
    fb = FakeWallBackend(verdict=v)
    assert fb.detect_wall("q?", "") is v
    assert fb.detect_wall("q?", "") is v  # same verdict every call


def test_fake_wall_backend_scripted_per_call() -> None:
    fb = FakeWallBackend(
        verdicts=[
            no_wall(),
            wall("factual_gap", 0.80),
        ]
    )
    assert fb.detect_wall("a: small talk", "").is_wall is False
    second = fb.detect_wall("a: I don't remember", "")
    assert second.is_wall is True
    assert second.category == "factual_gap"
    assert second.confidence == 0.80
    with pytest.raises(AssertionError):
        fb.detect_wall("a: more", "")  # script exhausted


def test_fake_wall_backend_records_transcript_and_summary() -> None:
    fb = FakeWallBackend()
    fb.detect_wall("the transcript", "the summary")
    assert fb.transcripts == ["the transcript"]
    assert fb.summaries == ["the summary"]


def test_wall_helper_rejects_non_wall_category() -> None:
    with pytest.raises(ValueError):
        wall("none", 0.9)
    with pytest.raises(ValueError):
        wall("not_a_real_category", 0.9)


def test_wall_verdict_like_none_constructor() -> None:
    v = WallVerdictLike.none()
    assert (v.is_wall, v.category, v.confidence, v.offer) == (False, "none", 0.0, "")


# --- FakeResponder ----------------------------------------------------------
def test_fake_responder_returns_canned_line_and_records_handoff() -> None:
    fr = FakeResponder(return_value="Following along.")
    # A handoff stand-in; the real EngagementHandoff arrives with the core.
    handoff = {"trigger_reason": "summon", "summary": "Tokyo trip planning"}
    assert fr.respond(handoff) == "Following along."
    assert fr.called
    assert fr.last_handoff is handoff
    assert fr.handoffs == [handoff]


# --- FakeVoice --------------------------------------------------------------
def test_fake_voice_records_spoken_lines() -> None:
    fv = FakeVoice()
    assert fv.called is False
    fv.speak("Want me to look that up?")
    fv.speak("On it.")
    assert fv.spoken == ["Want me to look that up?", "On it."]
    assert fv.last_spoken == "On it."
    assert fv.call_count == 2


def test_fake_voice_last_spoken_before_any_call_raises() -> None:
    fv = FakeVoice()
    with pytest.raises(AssertionError):
        _ = fv.last_spoken


# --- Recorder reset ---------------------------------------------------------
def test_reset_clears_recorded_calls() -> None:
    fs = FakeSummarizer()
    fs.summarize("a", "")
    assert fs.called
    fs.reset()
    assert fs.called is False
    assert fs.call_count == 0


# --- Fixtures are wired correctly ------------------------------------------
def test_fixtures_provide_configured_doubles(
    clock: SimulatedClock,
    fake_summarizer: FakeSummarizer,
    fake_wall_backend: FakeWallBackend,
    fake_responder: FakeResponder,
    fake_voice: FakeVoice,
) -> None:
    assert clock.now() == 0.0
    assert isinstance(fake_summarizer.summarize("t", ""), str)
    assert fake_wall_backend.detect_wall("t", "s").category == "none"
    assert isinstance(fake_responder.respond({}), str)
    fake_voice.speak("hi")
    assert fake_voice.spoken == ["hi"]
