"""T-302 — Continuous Path-B re-evaluation via ``AttentionLayer.tick()``.

Tests pin the behaviour of the pending-wall cache and the ``tick()`` method:

1. A cached wall fires via ``tick()`` once the simulated clock advances past the
   politeness gap — the core behaviour T-302 introduces.
2. ``tick()`` fires **exactly once** across many calls — regression for the
   double-fire bug (NOTES.md T-204 live-run finding; non-deterministic Qwen offer
   would defeat the category::offer back-off key, so the *same* cached verdict must
   be re-evaluated on every tick to keep the signature stable).
3. ``speech_resumed()`` before the gap opens → ``tick()`` does NOT fire
   (abort-on-resume hard-no preserved).
4. No pending wall → ``tick()`` is a no-op (not a crash, not a fire).
5. Engagement (Path A summon, or a Path-B fire) clears ``_pending_wall``.
6. Staleness / replacement policy: a newer wall verdict at ``ingest`` time replaces
   the stale cached one (fresher context wins); a non-wall verdict does NOT replace
   a cached wall verdict (nothing to wait on).

All tests are model-free and deterministic on ``SimulatedClock`` + fakes — no mic,
no model, no real clock.  The fixture helpers follow the same patterns as
``test_attention_layer.py`` and ``test_summon_controller.py``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from jarvis.attention_layer import AttentionLayer
from jarvis.core.turn_taking_gate import TurnTakingGate
from jarvis.types import EngagementHandoff, Interjection, Utterance, WallVerdict
from tests.clock import SimulatedClock
from tests.fakes import FakeResponder, FakeVoice, FakeWallBackend, no_wall, wall

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utt(text: str, ts: float = 0.0, speaker: str = "A") -> Utterance:
    return Utterance(speaker=speaker, text=text, ts=ts)


def _layer(
    clock: SimulatedClock,
    gate: TurnTakingGate,
    *,
    wall_verdict: WallVerdict | None = None,
    on_interjection: Callable[[Interjection], None] | None = None,
    on_engagement: Callable[[EngagementHandoff], None] | None = None,
) -> AttentionLayer:
    """Build a minimal ``AttentionLayer`` wired to the given gate and clock."""
    backend_verdict = wall_verdict or wall("factual_gap", 0.9, offer="I can find that.")
    responder = FakeResponder()
    voice = FakeVoice()
    return AttentionLayer.build(
        gate=gate,
        now=clock.now,
        responder=responder,
        voice=voice,
        wall_backend=FakeWallBackend(verdict=backend_verdict),
        on_interjection=on_interjection,
        on_engagement=on_engagement,
    )


# ---------------------------------------------------------------------------
# 1. tick() fires via the cached wall once the gap opens
# ---------------------------------------------------------------------------


def test_tick_fires_after_gap_opens() -> None:
    """``tick()`` fires a Path-B interjection once the politeness gap opens.

    This is the core behaviour T-302 introduces:
    - At ingest time, the wall is detected but the gap has not yet elapsed →
      ``consider_interjection`` returns None → verdict is cached.
    - We advance the clock past the politeness gap (no speech, so the gate's
      silence timer keeps growing).
    - ``tick()`` is called — the gap is now open, speech has not resumed →
      ``consider_interjection`` fires.
    """
    clock = SimulatedClock()
    gate = TurnTakingGate(clock.now, politeness_gap_seconds=2.0)
    interjections: list[Interjection] = []

    layer = _layer(clock, gate, on_interjection=interjections.append)

    # Simulate a speech segment ending → gate opens the silence clock.
    gate.on_speech_start()
    gate.on_speech_end()

    # Ingest a wall-bearing utterance.  Gap has NOT elapsed (t=0).
    layer.ingest(_utt("What was the date of the conference?", ts=clock.now()))
    assert interjections == [], "No interjection should fire at ingest (gap not open)"
    assert layer._pending_wall is not None, "Wall verdict should be cached"  # noqa: SLF001

    # Advance the clock past the politeness gap.
    clock.advance(2.5)
    assert gate.politeness_gap_elapsed() is True

    # tick() should now fire the interjection.
    layer.tick()
    assert len(interjections) == 1, "tick() should have fired one interjection"
    assert interjections[0].category.value == "factual_gap"
    assert layer._pending_wall is None, "_pending_wall should be cleared after fire"  # noqa: SLF001


# ---------------------------------------------------------------------------
# 2. tick() fires exactly once — double-fire regression test
# ---------------------------------------------------------------------------


def test_tick_fires_exactly_once_across_many_calls() -> None:
    """``tick()`` fires at most once regardless of how many times it is called.

    This is the regression test for the double-fire bug found in the T-204 live run
    (NOTES.md):  the non-deterministic Qwen ``offer`` text means each model call
    produces a different signature, so the ``category::offer`` back-off in
    ``SummonController`` would not de-dupe — and repeated ``layer.ingest()`` calls
    on the *same* verdict each produced a fresh model call (in the Phase-0 flow).

    T-302's fix: ``tick()`` re-evaluates the *same* cached ``WallVerdict`` object on
    every call, so the back-off signature is STABLE regardless of model
    non-determinism.  The ``SummonController`` back-off arms on the first fire, and
    subsequent ticks (with the same object → same signature) are de-duped **by the
    existing back-off logic** — no changes to ``SummonController`` needed.
    But the primary guard is simpler: ``_pending_wall`` is cleared on the first
    fire, so subsequent ticks are no-ops before the back-off even runs.
    """
    clock = SimulatedClock()
    gate = TurnTakingGate(clock.now, politeness_gap_seconds=2.0)
    interjections: list[Interjection] = []

    layer = _layer(clock, gate, on_interjection=interjections.append)

    gate.on_speech_start()
    gate.on_speech_end()
    layer.ingest(_utt("What was the conference date?", ts=clock.now()))

    # Open the gap.
    clock.advance(2.5)

    # Call tick() many times — should fire exactly once.
    for _ in range(20):
        layer.tick()

    assert len(interjections) == 1, (
        f"Expected exactly 1 interjection across 20 tick() calls, got {len(interjections)}"
    )
    assert layer._pending_wall is None  # noqa: SLF001


# ---------------------------------------------------------------------------
# 3. abort-on-resume: speech_resumed() before gap → tick() does not fire
# ---------------------------------------------------------------------------


def test_tick_does_not_fire_when_speech_resumed() -> None:
    """``tick()`` does not fire if speech resumed before the gap opened.

    The abort-on-resume hard-no (PRD) is preserved in ``tick()`` for free:
    ``consider_interjection`` reads ``gate.speech_resumed()`` and returns None if
    True — no new logic required.  The pending wall stays cached (speech_resumed
    clears on the next on_speech_end), so a subsequent silence could still fire it.
    """
    clock = SimulatedClock()
    gate = TurnTakingGate(clock.now, politeness_gap_seconds=2.0)
    interjections: list[Interjection] = []

    layer = _layer(clock, gate, on_interjection=interjections.append)

    gate.on_speech_start()
    gate.on_speech_end()
    layer.ingest(_utt("What was the date?", ts=clock.now()))
    assert layer._pending_wall is not None  # noqa: SLF001

    # Speech resumes before the gap opens — this latches gate.speech_resumed().
    clock.advance(1.0)
    gate.on_speech_start()  # abort latch: speech_resumed() → True
    assert gate.speech_resumed() is True

    # Even if the gap has technically elapsed (it hasn't here, but even if it had),
    # tick() must not fire because speech_resumed is True.
    clock.advance(2.0)  # push past the gap threshold
    layer.tick()

    assert interjections == [], "tick() must not fire when speech has resumed (abort-on-resume)"
    # _pending_wall is NOT cleared on abort — the wall is still pending.
    # (A fresh silence from the next on_speech_end could still fire it.)
    assert layer._pending_wall is not None  # noqa: SLF001


# ---------------------------------------------------------------------------
# 4. No pending wall → tick() is a no-op
# ---------------------------------------------------------------------------


def test_tick_is_noop_with_no_pending_wall() -> None:
    """``tick()`` on a layer with no cached wall is a no-op (not a crash or fire)."""
    clock = SimulatedClock()
    gate = TurnTakingGate(clock.now, politeness_gap_seconds=2.0)
    interjections: list[Interjection] = []
    engagements: list[EngagementHandoff] = []

    layer = _layer(
        clock, gate, on_interjection=interjections.append, on_engagement=engagements.append
    )

    assert layer._pending_wall is None  # noqa: SLF001

    # Advance far past the gap — still a no-op because nothing is pending.
    clock.advance(10.0)
    for _ in range(5):
        layer.tick()

    assert interjections == []
    assert engagements == []


# ---------------------------------------------------------------------------
# 5. Path-A summon clears _pending_wall
# ---------------------------------------------------------------------------


def test_path_a_summon_clears_pending_wall() -> None:
    """Path A engagement (wake-word summon) clears the cached wall verdict.

    Once Jarvis has engaged (on any path), the ambient half is done for this turn.
    The pending wall's context has been consumed so there is nothing left to fire.
    """
    clock = SimulatedClock()
    gate = TurnTakingGate(clock.now, politeness_gap_seconds=2.0)
    interjections: list[Interjection] = []
    engagements: list[EngagementHandoff] = []

    layer = _layer(
        clock, gate, on_interjection=interjections.append, on_engagement=engagements.append
    )

    # First ingest a wall-bearing utterance (gap not yet open → cached).
    gate.on_speech_start()
    gate.on_speech_end()
    layer.ingest(_utt("What was the conference date?", ts=clock.now()))
    assert layer._pending_wall is not None  # noqa: SLF001

    # Now ingest a wake-word utterance → Path A fires → _pending_wall must clear.
    gate.on_speech_start()
    gate.on_speech_end()
    layer.ingest(_utt("Jarvis, add this to my calendar", ts=clock.now()))

    assert layer._pending_wall is None, (  # noqa: SLF001
        "Path A engagement should clear _pending_wall"
    )
    # And a subsequent tick on the open gap should be a no-op.
    clock.advance(3.0)
    layer.tick()
    assert interjections == [], "No Path-B fire after Path A cleared the pending wall"
    # Path A engagement was recorded.
    assert len(engagements) == 1
    assert engagements[0].trigger_reason == "summon"


# ---------------------------------------------------------------------------
# 6. Path-B fire via ingest clears _pending_wall
# ---------------------------------------------------------------------------


def test_path_b_fire_at_ingest_clears_pending_wall() -> None:
    """A Path-B fire at ingest time also clears ``_pending_wall``.

    This covers the edge where the gap is already open at ingest (e.g. a long
    pause before the utterance was transcribed), so ``consider_interjection``
    fires immediately at ingest.  ``_pending_wall`` should not be set in this
    case — the wall was consumed on the spot.
    """
    clock = SimulatedClock()
    gate = TurnTakingGate(clock.now, politeness_gap_seconds=2.0)
    interjections: list[Interjection] = []

    layer = _layer(clock, gate, on_interjection=interjections.append)

    gate.on_speech_start()
    gate.on_speech_end()

    # Advance the clock so the gap is ALREADY open at ingest time.
    clock.advance(3.0)
    assert gate.politeness_gap_elapsed() is True

    layer.ingest(_utt("What was the conference date?", ts=clock.now()))

    # Path B fires immediately at ingest — no tick needed.
    assert len(interjections) == 1
    assert layer._pending_wall is None  # noqa: SLF001

    # Subsequent ticks are no-ops.
    for _ in range(5):
        layer.tick()
    assert len(interjections) == 1, "No second fire from tick() after ingest consumed the wall"


# ---------------------------------------------------------------------------
# 7. Staleness / replacement: newer wall replaces stale cached wall
# ---------------------------------------------------------------------------


def test_newer_wall_at_ingest_replaces_stale_cached_wall() -> None:
    """A new wall verdict from a subsequent ingest replaces the cached one.

    'Fresher context wins' — if two wall utterances arrive in sequence without the
    gap opening in between, the second verdict is the one tick() should evaluate.
    This ensures that stale offer text from an earlier utterance does not fire
    after the conversation has moved on.
    """
    clock = SimulatedClock()
    gate = TurnTakingGate(clock.now, politeness_gap_seconds=2.0)
    interjections: list[Interjection] = []

    first_verdict = wall("factual_gap", 0.85, offer="I can find the date.")
    second_verdict = wall("explicit_ask", 0.90, offer="Want me to help with that?")

    # First ingest with the first verdict.
    gate.on_speech_start()
    gate.on_speech_end()

    detector_returning_verdicts = [first_verdict, second_verdict]

    class SequencedWallBackend:
        def detect_wall(self, transcript: str, summary: str) -> WallVerdict:
            return detector_returning_verdicts.pop(0)

    responder = FakeResponder()
    voice = FakeVoice()
    layer2 = AttentionLayer.build(
        gate=gate,
        now=clock.now,
        responder=responder,
        voice=voice,
        wall_backend=SequencedWallBackend(),  # type: ignore[arg-type]
        on_interjection=interjections.append,
    )

    # First wall-bearing utterance → first verdict cached.
    layer2.ingest(_utt("What was the date?", ts=clock.now()))
    assert layer2._pending_wall is first_verdict  # noqa: SLF001

    # Second wall-bearing utterance before the gap opens → second verdict replaces first.
    clock.advance(0.5)
    gate.on_speech_start()
    gate.on_speech_end()
    clock.advance(0.1)
    layer2.ingest(_utt("Can you help me find the conference room?", ts=clock.now()))
    assert layer2._pending_wall is second_verdict, (  # noqa: SLF001
        "Second wall verdict should replace the first (fresher context wins)"
    )

    # Now open the gap — tick() fires with the second verdict.
    clock.advance(2.5)
    layer2.tick()

    assert len(interjections) == 1
    assert interjections[0].category.value == "explicit_ask", (
        "Interjection should be from the second (fresher) cached verdict"
    )


# ---------------------------------------------------------------------------
# 8. Non-wall verdict does NOT clear a cached wall verdict
# ---------------------------------------------------------------------------


def test_non_wall_verdict_does_not_clear_pending_wall() -> None:
    """A non-wall ingest does not overwrite the cached wall verdict.

    If a wall is cached and then a non-wall utterance comes in, the non-wall
    verdict should NOT replace the cache — there is nothing to wait on for a
    non-wall, and we should not drop the pending wall just because an intervening
    non-wall utterance arrived.
    """
    clock = SimulatedClock()
    gate = TurnTakingGate(clock.now, politeness_gap_seconds=2.0)
    interjections: list[Interjection] = []

    wall_v = wall("factual_gap", 0.9, offer="I can find that.")
    non_wall_v = no_wall()

    class AlternatingBackend:
        def __init__(self) -> None:
            self._count = 0

        def detect_wall(self, transcript: str, summary: str) -> WallVerdict:
            v = wall_v if self._count == 0 else non_wall_v
            self._count += 1
            return v

    responder = FakeResponder()
    voice = FakeVoice()
    layer2 = AttentionLayer.build(
        gate=gate,
        now=clock.now,
        responder=responder,
        voice=voice,
        wall_backend=AlternatingBackend(),  # type: ignore[arg-type]
        on_interjection=interjections.append,
    )

    gate.on_speech_start()
    gate.on_speech_end()
    layer2.ingest(_utt("What was the conference date?", ts=clock.now()))
    assert layer2._pending_wall is wall_v  # noqa: SLF001

    # Non-wall utterance arrives — should NOT clear the cached wall.
    clock.advance(0.3)
    gate.on_speech_start()
    gate.on_speech_end()
    # "Nice weather today" has no wall signal — no detection, no cache change.
    layer2.ingest(_utt("Nice weather today", ts=clock.now()))
    assert layer2._pending_wall is wall_v, (  # noqa: SLF001
        "A non-wall-signal utterance should not clear the pending wall"
    )

    # Open the gap — tick fires with the original wall.
    clock.advance(2.5)
    layer2.tick()
    assert len(interjections) == 1
    assert interjections[0].category.value == "factual_gap"


# ---------------------------------------------------------------------------
# 9. tick() preserves the one-clock invariant (no new monotonic())
# ---------------------------------------------------------------------------


def test_tick_reads_time_only_via_gate_predicates() -> None:
    """``tick()`` uses no clock of its own — only the gate's predicates.

    This is the one-clock invariant check for tick(): the gate predicates
    (``politeness_gap_elapsed()`` / ``speech_resumed()``) are the only
    time-reading paths inside tick().  We verify this structurally by confirming
    that the SimulatedClock controls the fire threshold — the tick does NOT fire
    until the simulated clock advances past the gap, even if wall-clock time has
    elapsed.
    """
    clock = SimulatedClock()
    gate = TurnTakingGate(clock.now, politeness_gap_seconds=2.0)
    interjections: list[Interjection] = []

    layer = _layer(clock, gate, on_interjection=interjections.append)

    gate.on_speech_start()
    gate.on_speech_end()
    layer.ingest(_utt("What was the date?", ts=clock.now()))

    # Wall-clock time may pass during the test, but the gate is on a SimulatedClock.
    # tick() must NOT fire because the simulated clock has NOT advanced past the gap.
    assert gate.politeness_gap_elapsed() is False
    layer.tick()
    assert interjections == [], "tick() must not fire when SimulatedClock gap has not elapsed"

    # Advance the simulated clock — NOW tick should fire.
    clock.advance(2.5)
    layer.tick()
    assert len(interjections) == 1, "tick() should fire after SimulatedClock advances past gap"


# ---------------------------------------------------------------------------
# 10. Abort after gap latches does not prevent future ticks (resume then re-silence)
# ---------------------------------------------------------------------------


def test_tick_fires_after_abort_then_fresh_silence() -> None:
    """A speech-resume abort does not permanently disable the pending wall.

    After speech resumes (aborting a pending interjection), the next
    ``on_speech_end()`` opens a fresh silence.  When the gap opens again on
    that fresh silence, ``tick()`` should fire — the wall is still cached.
    """
    clock = SimulatedClock()
    gate = TurnTakingGate(clock.now, politeness_gap_seconds=2.0)
    interjections: list[Interjection] = []

    layer = _layer(clock, gate, on_interjection=interjections.append)

    # First silence: wall detected, gap not yet open.
    gate.on_speech_start()
    gate.on_speech_end()
    layer.ingest(_utt("What was the conference date?", ts=clock.now()))
    assert layer._pending_wall is not None  # noqa: SLF001

    # Speech resumes before the gap — abort signal latched.
    clock.advance(1.0)
    gate.on_speech_start()
    assert gate.speech_resumed() is True
    layer.tick()  # no-op: speech_resumed() is True
    assert interjections == []

    # Next speech ends → fresh silence opens.  Abort latch clears.
    gate.on_speech_end()  # clears speech_resumed latch
    assert gate.speech_resumed() is False
    # _pending_wall still cached (not cleared by abort)
    assert layer._pending_wall is not None  # noqa: SLF001

    # Let the gap open on the fresh silence.
    clock.advance(2.5)
    layer.tick()
    assert len(interjections) == 1, (
        "tick() should fire on the fresh silence after the abort + new speech_end"
    )


# ---------------------------------------------------------------------------
# 11. Thread-safety: concurrent ingest and tick do not corrupt state
# ---------------------------------------------------------------------------


def test_tick_and_ingest_thread_safety_with_lock() -> None:
    """Simulates the live.py threading model: lock around ingest + tick.

    This is not a test of tick() correctness — it's a race-condition stress test
    for the locking discipline.  We spawn an ingest thread and a tick thread
    that run concurrently (as live.py does), both protected by the same lock.
    After the run, state must be consistent (no AttributeError, no double-fire,
    _pending_wall either None or a WallVerdict).
    """
    clock = SimulatedClock()
    gate = TurnTakingGate(clock.now, politeness_gap_seconds=2.0)
    interjections: list[Interjection] = []
    lock = threading.Lock()

    layer = _layer(clock, gate, on_interjection=interjections.append)

    # Pre-load a wall verdict so tick() has something to evaluate.
    gate.on_speech_start()
    gate.on_speech_end()
    layer.ingest(_utt("What was the date?", ts=clock.now()))
    clock.advance(2.5)  # gap now open

    errors: list[Exception] = []

    def _ingest_thread() -> None:
        try:
            for _ in range(10):
                with lock:
                    layer.ingest(_utt("Some follow-up.", ts=clock.now()))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def _tick_thread() -> None:
        try:
            for _ in range(30):
                with lock:
                    layer.tick()
                time.sleep(0.001)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    t1 = threading.Thread(target=_ingest_thread)
    t2 = threading.Thread(target=_tick_thread)
    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    assert not errors, f"Exceptions during concurrent ingest+tick: {errors}"
    # State is consistent: pending_wall is either None or a valid WallVerdict.
    pw = layer._pending_wall  # noqa: SLF001
    assert pw is None or isinstance(pw, WallVerdict)
    # No double-fire: at most 1 interjection from the initial wall (gap was open).
    # Follow-up ingests may have added more walls (FakeWallBackend always returns
    # the same verdict) but back-off de-dupes them — total fires ≤ small bound.
    assert len(interjections) <= 5, (
        "Unexpected number of interjections — check for races or back-off failure"
    )
