"""Tests for TurnTakingGate (T-006).

Drives the gate through its three transitions deterministically on the T-009
``SimulatedClock`` — no real audio, no real sleep. Per the eval-plan golden
rule, every assertion is on a public predicate (``settled`` /
``politeness_gap_elapsed`` / ``speech_resumed``) after feeding events and
advancing the clock; nothing reaches into internals.

The event-input API under test (designed in T-006):
    on_speech_start()  /  on_speech_end()
with time supplied only by the injected ``now`` (``clock.now``).
"""

from __future__ import annotations

import pytest

from jarvis.core.turn_taking_gate import (
    DEFAULT_POLITENESS_GAP_SECONDS,
    DEFAULT_SETTLE_SECONDS,
    TurnTakingGate,
)
from tests.clock import SimulatedClock

# --- Initial state -----------------------------------------------------------


def test_all_predicates_false_before_any_event():
    clock = SimulatedClock()
    gate = TurnTakingGate(now=clock.now)

    # No speech heard yet → no silence to measure → everything False.
    assert gate.settled() is False
    assert gate.politeness_gap_elapsed() is False
    assert gate.speech_resumed() is False

    # Time passing alone (no on_speech_end) opens no gap.
    clock.advance(10.0)
    assert gate.settled() is False
    assert gate.politeness_gap_elapsed() is False


def test_while_speaking_nothing_is_settled():
    clock = SimulatedClock()
    gate = TurnTakingGate(now=clock.now)

    gate.on_speech_start()
    clock.advance(5.0)  # talking for a while

    assert gate.settled() is False
    assert gate.politeness_gap_elapsed() is False
    assert gate.speech_resumed() is False


# --- Settle (the short endpoint gap, Path A) ---------------------------------


def test_settles_after_the_settle_gap():
    clock = SimulatedClock()
    gate = TurnTakingGate(now=clock.now, settle_seconds=0.6, politeness_gap_seconds=2.0)

    gate.on_speech_start()
    gate.on_speech_end()  # silence begins at t=0

    # Just before the settle threshold: not settled yet.
    clock.advance(0.5)
    assert gate.settled() is False

    # Crossing the threshold: settled.
    clock.advance(0.1)  # now 0.6 s of silence
    assert gate.settled() is True
    # The longer politeness gap has NOT elapsed yet (the asymmetry).
    assert gate.politeness_gap_elapsed() is False


def test_settle_is_inclusive_at_the_boundary():
    clock = SimulatedClock()
    gate = TurnTakingGate(now=clock.now, settle_seconds=0.6, politeness_gap_seconds=2.0)

    gate.on_speech_end()
    clock.advance(0.6)  # exactly the threshold
    assert gate.settled() is True


# --- Politeness gap (the long gap, Path B) -----------------------------------


def test_politeness_gap_elapses_after_the_long_gap():
    clock = SimulatedClock()
    gate = TurnTakingGate(now=clock.now, settle_seconds=0.6, politeness_gap_seconds=2.0)

    gate.on_speech_start()
    gate.on_speech_end()  # silence begins

    # After the settle gap but before the politeness gap: settled, not polite-yet.
    clock.advance(1.0)
    assert gate.settled() is True
    assert gate.politeness_gap_elapsed() is False

    # Cross the politeness gap.
    clock.advance(1.0)  # now 2.0 s of silence
    assert gate.politeness_gap_elapsed() is True
    assert gate.settled() is True  # still settled, naturally


def test_walks_settle_then_politeness_in_one_silence():
    # A single uninterrupted silence crosses settle, then the politeness gap.
    clock = SimulatedClock()
    gate = TurnTakingGate(now=clock.now, settle_seconds=0.5, politeness_gap_seconds=2.0)

    gate.on_speech_end()

    clock.advance(0.4)
    assert (gate.settled(), gate.politeness_gap_elapsed()) == (False, False)
    clock.advance(0.2)  # 0.6 s total → settled
    assert (gate.settled(), gate.politeness_gap_elapsed()) == (True, False)
    clock.advance(1.4)  # 2.0 s total → politeness gap
    assert (gate.settled(), gate.politeness_gap_elapsed()) == (True, True)


# --- Speech resumed (the abort signal) ---------------------------------------


def test_speech_resumed_latches_when_speech_returns_after_a_gap():
    clock = SimulatedClock()
    gate = TurnTakingGate(now=clock.now, settle_seconds=0.6, politeness_gap_seconds=2.0)

    gate.on_speech_start()
    gate.on_speech_end()  # silence begins
    clock.advance(1.0)  # a gap has opened (settled)
    assert gate.settled() is True
    assert gate.speech_resumed() is False

    # Someone starts talking again → abort.
    gate.on_speech_start()
    assert gate.speech_resumed() is True
    # Re-armed: the silence-based predicates fall back to False.
    assert gate.settled() is False
    assert gate.politeness_gap_elapsed() is False


def test_resume_aborts_a_pending_politeness_gap():
    # The core Path-B abort: silence almost reached the politeness gap, then
    # speech resumed — the gap must NOT count as elapsed.
    clock = SimulatedClock()
    gate = TurnTakingGate(now=clock.now, settle_seconds=0.6, politeness_gap_seconds=2.0)

    gate.on_speech_end()
    clock.advance(1.9)  # almost there
    assert gate.politeness_gap_elapsed() is False

    gate.on_speech_start()  # resumed just before the gap
    gate.on_speech_end()  # ... then quiet again, fresh silence at t=1.9
    assert gate.speech_resumed() is False  # cleared by the new on_speech_end

    # The gap clock restarted: 0.2 s more is NOT yet 2.0 s from the new silence.
    clock.advance(0.2)
    assert gate.politeness_gap_elapsed() is False
    clock.advance(1.8)  # now 2.0 s since the *new* silence onset
    assert gate.politeness_gap_elapsed() is True


def test_resume_latch_clears_on_next_speech_end():
    clock = SimulatedClock()
    gate = TurnTakingGate(now=clock.now)

    gate.on_speech_end()
    clock.advance(1.0)
    gate.on_speech_start()  # resume → latch
    assert gate.speech_resumed() is True

    gate.on_speech_end()  # fresh silence clears the latch
    assert gate.speech_resumed() is False


def test_first_speech_start_is_not_a_resume():
    # The very first onset (no prior silence) is normal speech, not a resumption.
    clock = SimulatedClock()
    gate = TurnTakingGate(now=clock.now)

    gate.on_speech_start()
    assert gate.speech_resumed() is False


def test_silence_measured_from_the_most_recent_end():
    # Two ends in a row (e.g. a blip of speech between them) → the gap is measured
    # from the latest end, not the first.
    clock = SimulatedClock()
    gate = TurnTakingGate(now=clock.now, settle_seconds=0.6, politeness_gap_seconds=2.0)

    gate.on_speech_end()  # t=0
    clock.advance(1.5)
    gate.on_speech_start()  # blip of speech
    gate.on_speech_end()  # new silence onset at t=1.5
    clock.advance(1.0)  # only 1.0 s since the new onset
    assert gate.politeness_gap_elapsed() is False
    clock.advance(1.0)  # 2.0 s since the new onset
    assert gate.politeness_gap_elapsed() is True


# --- Predicates are pure reads (idempotent) ----------------------------------


def test_predicates_do_not_mutate_state():
    clock = SimulatedClock()
    gate = TurnTakingGate(now=clock.now, settle_seconds=0.6, politeness_gap_seconds=2.0)

    gate.on_speech_end()
    clock.advance(2.0)

    # Reading repeatedly yields the same answer (no consume-on-read).
    assert [gate.politeness_gap_elapsed() for _ in range(3)] == [True, True, True]
    assert gate.settled() is True
    assert gate.speech_resumed() is False


# --- Configuration / guards --------------------------------------------------


def test_defaults_encode_the_asymmetry():
    # The packaged defaults are the asymmetric pair from the decision log.
    assert DEFAULT_SETTLE_SECONDS < DEFAULT_POLITENESS_GAP_SECONDS
    clock = SimulatedClock()
    gate = TurnTakingGate(now=clock.now)  # all defaults

    gate.on_speech_end()
    clock.advance(DEFAULT_SETTLE_SECONDS)
    assert gate.settled() is True
    assert gate.politeness_gap_elapsed() is False
    clock.advance(DEFAULT_POLITENESS_GAP_SECONDS - DEFAULT_SETTLE_SECONDS)
    assert gate.politeness_gap_elapsed() is True


def test_thresholds_are_configurable():
    clock = SimulatedClock()
    gate = TurnTakingGate(now=clock.now, settle_seconds=0.2, politeness_gap_seconds=0.5)

    gate.on_speech_end()
    clock.advance(0.2)
    assert gate.settled() is True
    clock.advance(0.3)  # 0.5 s total
    assert gate.politeness_gap_elapsed() is True


def test_rejects_negative_settle():
    clock = SimulatedClock()
    with pytest.raises(ValueError):
        TurnTakingGate(now=clock.now, settle_seconds=-0.1)


def test_rejects_politeness_gap_shorter_than_settle():
    clock = SimulatedClock()
    with pytest.raises(ValueError):
        TurnTakingGate(now=clock.now, settle_seconds=2.0, politeness_gap_seconds=1.0)
