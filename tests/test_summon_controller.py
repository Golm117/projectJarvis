"""Tests for SummonController (T-007) — the asymmetric dual-path machine.

Drives both initiation paths on the T-009 ``SimulatedClock`` + the seam fakes,
asserting only on the public ``SummonDecision`` the controller returns (never on
internals) — per the eval-plan golden rule. Time is driven by advancing the
clock the injected ``TurnTakingGate`` was built on; the controller itself reads
no clock.

Coverage targets (from the T-007 spec + qa-tuning's T-005/T-006 review notes):
  * Path A immediacy — summon engages even with no wall and gap not elapsed.
  * Path B all-conditions gating — drops if ANY of wall / confidence / gap fails.
  * abort-on-resume — a resume suppresses a pending interjection.
  * back-off — the same wall signature does not re-fire.
  * the confidence-floor boundary — ``>=`` is inclusive; just-below drops.
"""

from __future__ import annotations

import pytest

from jarvis.core.summon_controller import (
    DEFAULT_INTERJECTION_CONFIDENCE_FLOOR,
    SummonController,
)
from jarvis.core.turn_taking_gate import TurnTakingGate
from jarvis.types import TriggerReason, WallCategory
from tests.clock import SimulatedClock
from tests.fakes import no_wall, wall

# --- helpers -----------------------------------------------------------------


def _gate(clock: SimulatedClock, **kw) -> TurnTakingGate:
    """A gate on the given clock with the packaged asymmetric defaults unless
    overridden."""
    return TurnTakingGate(now=clock.now, **kw)


def _open_politeness_gap(clock: SimulatedClock, gate: TurnTakingGate) -> None:
    """Drive the gate into a clear opening: speech ended, then the full politeness
    gap of silence elapsed (no resume)."""
    gate.on_speech_start()
    gate.on_speech_end()
    clock.advance(2.0)  # >= DEFAULT_POLITENESS_GAP_SECONDS
    assert gate.politeness_gap_elapsed() is True
    assert gate.speech_resumed() is False


# =============================================================================
# Path A — summon: immediate and unconditional
# =============================================================================


def test_summon_engages_immediately_with_no_wall_and_gap_not_elapsed():
    # The defining Path-A property: it ignores the gate and the wall entirely.
    clock = SimulatedClock()
    gate = _gate(clock)
    ctrl = SummonController(gate=gate)

    # Worst case for Path B: someone is mid-sentence (no gap, speech ongoing),
    # and there is no wall at all. Path A still fires.
    gate.on_speech_start()
    assert gate.politeness_gap_elapsed() is False

    decision = ctrl.on_summon(detail="Jarvis, add that to my calendar")

    assert decision.reason is TriggerReason.SUMMON
    assert decision.interjection is None
    assert decision.detail == "Jarvis, add that to my calendar"


def test_summon_ignores_speech_resumed_and_backoff():
    # Path A never inherits Path B's caution: a resumed-speech state does not
    # abort a summon, and repeated summons always fire (no back-off on Path A).
    clock = SimulatedClock()
    gate = _gate(clock)
    ctrl = SummonController(gate=gate)

    gate.on_speech_end()
    clock.advance(1.0)
    gate.on_speech_start()  # speech resumed → would abort a Path-B offer
    assert gate.speech_resumed() is True

    first = ctrl.on_summon()
    second = ctrl.on_summon()
    assert first.reason is TriggerReason.SUMMON
    assert second.reason is TriggerReason.SUMMON  # fired again, no back-off


def test_summon_handoff_reason_is_summon():
    clock = SimulatedClock()
    ctrl = SummonController(gate=_gate(clock))
    assert ctrl.on_summon().handoff_reason() == "summon"


# =============================================================================
# Path B — interjection: all conditions must hold
# =============================================================================


def test_interjection_fires_when_all_conditions_hold():
    clock = SimulatedClock()
    gate = _gate(clock)
    ctrl = SummonController(gate=gate)
    _open_politeness_gap(clock, gate)

    decision = ctrl.consider_interjection(
        wall("factual_gap", 0.80, offer="I can find that — want me to?")
    )

    assert decision is not None
    assert decision.reason is TriggerReason.INTERJECTION
    assert decision.interjection is not None
    assert decision.interjection.category is WallCategory.FACTUAL_GAP
    assert decision.interjection.offer == "I can find that — want me to?"
    assert decision.interjection.confidence == 0.80
    # The handoff reason carries the category (the module-map convention).
    assert decision.handoff_reason() == "wall:factual_gap"


def test_interjection_drops_when_not_a_wall():
    clock = SimulatedClock()
    gate = _gate(clock)
    ctrl = SummonController(gate=gate)
    _open_politeness_gap(clock, gate)

    # Gap is open, but there is no wall → no offer.
    assert ctrl.consider_interjection(no_wall()) is None


def test_interjection_drops_when_confidence_below_floor():
    clock = SimulatedClock()
    gate = _gate(clock)
    ctrl = SummonController(gate=gate)
    _open_politeness_gap(clock, gate)

    # A real wall, gap open — but under the 0.70 floor.
    assert ctrl.consider_interjection(wall("unanswered_question", 0.50)) is None


def test_interjection_drops_when_gap_not_elapsed():
    clock = SimulatedClock()
    gate = _gate(clock)
    ctrl = SummonController(gate=gate)

    # A confident wall, but only the settle gap has passed — not the politeness
    # gap. Path B must wait for the long opening.
    gate.on_speech_end()
    clock.advance(0.6)  # settled, but not the 2.0 s politeness gap
    assert gate.settled() is True
    assert gate.politeness_gap_elapsed() is False

    assert ctrl.consider_interjection(wall("factual_gap", 0.90)) is None


def test_interjection_waits_then_fires_as_the_gap_elapses():
    # The pending→offer walk: the same confident wall is rejected before the gap
    # and accepted once the clock crosses it. Drives time via the gate's clock.
    clock = SimulatedClock()
    gate = _gate(clock)
    ctrl = SummonController(gate=gate)
    verdict = wall("factual_gap", 0.85)

    gate.on_speech_end()
    clock.advance(1.0)  # past settle, before politeness gap
    assert ctrl.consider_interjection(verdict) is None  # still waiting

    clock.advance(1.0)  # now 2.0 s of silence → gap elapsed
    decision = ctrl.consider_interjection(verdict)
    assert decision is not None
    assert decision.reason is TriggerReason.INTERJECTION


# =============================================================================
# Abort-on-resume
# =============================================================================


def test_interjection_aborts_when_speech_resumed():
    # Speech resumed while the interjection was pending → yield the floor.
    clock = SimulatedClock()
    gate = _gate(clock)
    ctrl = SummonController(gate=gate)

    gate.on_speech_end()
    clock.advance(1.9)  # almost at the gap
    gate.on_speech_start()  # resumed just before it → latches speech_resumed
    assert gate.speech_resumed() is True

    assert ctrl.consider_interjection(wall("factual_gap", 0.90)) is None


def test_resume_suppresses_even_if_a_stale_gap_reads_elapsed():
    # Defensive ordering: speech_resumed is checked BEFORE the gap, so a resume
    # suppresses the offer regardless of how the gap predicate happens to read.
    # (A latched resume means we must never talk over the new speech.)
    clock = SimulatedClock()
    gate = _gate(clock)
    ctrl = SummonController(gate=gate)

    gate.on_speech_end()
    clock.advance(2.0)  # gap elapsed
    assert gate.politeness_gap_elapsed() is True
    gate.on_speech_start()  # ... but speech just resumed → abort
    assert gate.speech_resumed() is True

    assert ctrl.consider_interjection(wall("factual_gap", 0.90)) is None


def test_can_interject_after_a_resume_clears_on_fresh_silence():
    # After an abort, a fresh silence (new on_speech_end) clears the resume latch
    # and a new full gap can earn an offer.
    clock = SimulatedClock()
    gate = _gate(clock)
    ctrl = SummonController(gate=gate)
    verdict = wall("factual_gap", 0.90)

    gate.on_speech_end()
    clock.advance(1.0)
    gate.on_speech_start()  # resume → abort territory
    assert ctrl.consider_interjection(verdict) is None

    gate.on_speech_end()  # fresh silence, latch cleared
    clock.advance(2.0)  # full new gap
    assert gate.speech_resumed() is False
    decision = ctrl.consider_interjection(verdict)
    assert decision is not None
    assert decision.reason is TriggerReason.INTERJECTION


# =============================================================================
# Back-off — the same wall signature does not re-fire
# =============================================================================


def test_backoff_same_wall_does_not_refire():
    clock = SimulatedClock()
    gate = _gate(clock)
    ctrl = SummonController(gate=gate)
    verdict = wall("factual_gap", 0.80, offer="I can find that — want me to?")

    _open_politeness_gap(clock, gate)
    first = ctrl.consider_interjection(verdict)
    assert first is not None  # fired once

    # Same wall, gap still open → backed off (no nagging), even on a brand-new gap.
    gate.on_speech_start()
    gate.on_speech_end()
    clock.advance(2.0)
    assert ctrl.consider_interjection(verdict) is None


def test_backoff_is_per_signature_a_different_wall_fires():
    clock = SimulatedClock()
    gate = _gate(clock)
    ctrl = SummonController(gate=gate)

    _open_politeness_gap(clock, gate)
    first = ctrl.consider_interjection(wall("factual_gap", 0.80, offer="A?"))
    assert first is not None

    # A different category+offer is a different wall → it fires despite back-off
    # on the previous one.
    gate.on_speech_start()
    gate.on_speech_end()
    clock.advance(2.0)
    second = ctrl.consider_interjection(wall("unanswered_question", 0.80, offer="B?"))
    assert second is not None
    assert second.interjection.category is WallCategory.UNANSWERED_QUESTION


def test_backoff_signature_ignores_confidence():
    # The same category+offer at a different confidence is still 'the same offer'
    # — a re-detection of one wall, not a new one. So it backs off.
    clock = SimulatedClock()
    gate = _gate(clock)
    ctrl = SummonController(gate=gate)

    _open_politeness_gap(clock, gate)
    assert ctrl.consider_interjection(wall("factual_gap", 0.80, offer="Same?")) is not None

    gate.on_speech_start()
    gate.on_speech_end()
    clock.advance(2.0)
    assert ctrl.consider_interjection(wall("factual_gap", 0.95, offer="Same?")) is None


def test_a_new_wall_fires_after_an_intervening_different_wall():
    # Back-off is "twice in a ROW": signature A → B → A should let the second A
    # fire, because the last *offered* signature was B, not A.
    clock = SimulatedClock()
    gate = _gate(clock)
    ctrl = SummonController(gate=gate)

    def fire(v):
        gate.on_speech_start()
        gate.on_speech_end()
        clock.advance(2.0)
        return ctrl.consider_interjection(v)

    a = wall("factual_gap", 0.80, offer="A?")
    b = wall("unanswered_question", 0.80, offer="B?")
    assert fire(a) is not None
    assert fire(b) is not None
    assert fire(a) is not None  # A again — last offered was B, so not a repeat


def test_a_dropped_wall_does_not_arm_backoff():
    # A sub-threshold wall that never fired must not poison back-off: once a
    # qualifying wall arrives, it should still be allowed to fire.
    clock = SimulatedClock()
    gate = _gate(clock)
    ctrl = SummonController(gate=gate)
    _open_politeness_gap(clock, gate)

    # Below floor → dropped, no back-off armed.
    assert ctrl.consider_interjection(wall("factual_gap", 0.40, offer="X?")) is None
    # Same wall, now confident enough → fires (back-off was never armed).
    decision = ctrl.consider_interjection(wall("factual_gap", 0.80, offer="X?"))
    assert decision is not None


# =============================================================================
# Confidence-floor boundary
# =============================================================================


def test_confidence_floor_is_inclusive_at_the_boundary():
    clock = SimulatedClock()
    gate = _gate(clock)
    ctrl = SummonController(gate=gate, interjection_confidence_floor=0.70)
    _open_politeness_gap(clock, gate)

    # Exactly at the floor → fires (the cut is >=).
    assert ctrl.consider_interjection(wall("factual_gap", 0.70)) is not None


def test_just_below_the_floor_drops():
    clock = SimulatedClock()
    gate = _gate(clock)
    ctrl = SummonController(gate=gate, interjection_confidence_floor=0.70)
    _open_politeness_gap(clock, gate)

    assert ctrl.consider_interjection(wall("factual_gap", 0.69)) is None


def test_floor_is_configurable():
    # A stricter floor changes what clears: 0.80 rejects a 0.75 wall the default
    # would have accepted.
    clock = SimulatedClock()
    gate = _gate(clock)
    ctrl = SummonController(gate=gate, interjection_confidence_floor=0.80)
    _open_politeness_gap(clock, gate)

    assert ctrl.consider_interjection(wall("factual_gap", 0.75)) is None
    assert ctrl.interjection_confidence_floor == 0.80


def test_default_floor_matches_the_prototype():
    assert DEFAULT_INTERJECTION_CONFIDENCE_FLOOR == 0.70
    clock = SimulatedClock()
    ctrl = SummonController(gate=_gate(clock))
    assert ctrl.interjection_confidence_floor == 0.70


# =============================================================================
# Construction guards
# =============================================================================


@pytest.mark.parametrize("bad", [-0.1, 1.1, 2.0])
def test_rejects_out_of_range_floor(bad):
    clock = SimulatedClock()
    with pytest.raises(ValueError):
        SummonController(gate=_gate(clock), interjection_confidence_floor=bad)


def test_floor_boundaries_zero_and_one_are_allowed():
    clock = SimulatedClock()
    SummonController(gate=_gate(clock), interjection_confidence_floor=0.0)
    SummonController(gate=_gate(clock), interjection_confidence_floor=1.0)
