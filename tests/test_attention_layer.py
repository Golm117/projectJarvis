"""End-to-end tests for the AttentionLayer orchestrator (T-008).

These are the **acceptance** tests for Phase 0's final task: a scripted
conversation, run through the real ``AttentionLayer`` on the ``SimulatedClock`` +
``ScriptedSource`` + the recording ``FakeResponder`` / ``FakeVoice``, must produce

  * living-summary updates,
  * at least one **correct** Path-B interjection (right category, cleared the
    politeness gap + confidence floor), and
  * a wake-word Path-A summon → ``EngagementHandoff``,

all with **no audio and no network**. Every assertion is on an *emitted event* or
a *seam call* (the module map's external-behavior rule) — never a private field.

The harness drives time deterministically: ``ScriptedSource`` advances the shared
``SimulatedClock`` and feeds the gate's speech-boundary events as it plays each
line's inter-line ``gap``, so the politeness gap opens (or doesn't) exactly as the
pacing dictates — no real ``sleep`` anywhere.
"""

from __future__ import annotations

import pytest

from jarvis.adapters.engaged import PrintResponder, PrintVoice
from jarvis.adapters.transcript_source import ScriptedLine, ScriptedSource
from jarvis.attention_layer import AttentionLayer
from jarvis.core.turn_taking_gate import TurnTakingGate
from jarvis.types import EngagementHandoff, Interjection, WallCategory
from tests.clock import SimulatedClock
from tests.fakes import FakeResponder, FakeSummarizer, FakeVoice, FakeWallBackend, wall

# A gap comfortably past the default 2.0 s politeness gap, so a wall on the
# preceding line clears the gate and Path B can fire.
LONG_GAP = 2.5
# A short gap that does NOT open the politeness window.
SHORT_GAP = 0.5


class _Recorder:
    """Collects the three emitted event streams for assertions."""

    def __init__(self) -> None:
        self.summaries: list[str] = []
        self.interjections: list[Interjection] = []
        self.engagements: list[EngagementHandoff] = []

    def on_summary(self, text: str) -> None:
        self.summaries.append(text)

    def on_interjection(self, i: Interjection) -> None:
        self.interjections.append(i)

    def on_engagement(self, h: EngagementHandoff) -> None:
        self.engagements.append(h)


def _run(
    clock: SimulatedClock,
    lines: list[ScriptedLine],
    responder: FakeResponder,
    voice: FakeVoice,
    *,
    summarizer_backend=None,
    wall_backend=None,
    max_utterances: int = 12,
    rec: _Recorder | None = None,
) -> _Recorder:
    """Run a scripted conversation end-to-end and return the event recorder."""
    rec = rec if rec is not None else _Recorder()
    AttentionLayer.run_scripted(
        lines,
        now=clock.now,
        clock_advance=clock.advance,
        responder=responder,
        voice=voice,
        summarizer_backend=summarizer_backend,
        wall_backend=wall_backend,
        max_utterances=max_utterances,
        on_summary_update=rec.on_summary,
        on_interjection=rec.on_interjection,
        on_engagement=rec.on_engagement,
    )
    return rec


# ---------------------------------------------------------------------------
# The headline acceptance: the full conversation produces all three behaviors.
# ---------------------------------------------------------------------------
def test_scripted_conversation_produces_all_three_behaviors() -> None:
    """One run yields: summary update(s) + a correct interjection + a summon handoff."""
    clock = SimulatedClock()
    responder = FakeResponder()
    voice = FakeVoice()
    lines = [
        ScriptedLine("Alex", "Hey, did you book the flights for the Tokyo trip?", gap=SHORT_GAP),
        ScriptedLine("Sam", "Not yet, I keep forgetting which week we settled on.", gap=SHORT_GAP),
        ScriptedLine("Alex", "I think it was the second week of October.", gap=SHORT_GAP),
        # factual gap + a long silence → Path B interjection fires here.
        ScriptedLine("Sam", "Wait, what was the conference date again?", gap=LONG_GAP),
        ScriptedLine("Alex", "Let's do Saturday. Jarvis, add it to my calendar.", gap=0.0),
    ]
    rec = _run(clock, lines, responder, voice, max_utterances=5)

    # 1) Summary updated at least once.
    assert rec.summaries, "expected at least one living-summary update"

    # 2) At least one CORRECT interjection (Path B): right category, surfaced offer.
    assert len(rec.interjections) >= 1
    interjection = rec.interjections[0]
    assert interjection.category is WallCategory.FACTUAL_GAP
    assert interjection.confidence >= 0.70  # cleared the floor
    assert interjection.offer  # carries the line it would say

    # 3) A wake-word summon → EngagementHandoff (Path A).
    summons = [h for h in rec.engagements if h.trigger_reason == "summon"]
    assert len(summons) == 1
    assert "Jarvis" in summons[0].detail  # the summon utterance carried through

    # Both engagement paths crossed the boundary to the engaged half (responder
    # + voice), with no audio and no network: one for the interjection, one for
    # the summon.
    assert responder.call_count == 2
    assert voice.call_count == 2


# ---------------------------------------------------------------------------
# Path A — summon — in isolation: immediate, unconditional, builds the handoff.
# ---------------------------------------------------------------------------
def test_wake_word_summons_immediately_and_builds_handoff() -> None:
    clock = SimulatedClock()
    responder = FakeResponder(return_value="On it.")
    voice = FakeVoice()
    lines = [
        ScriptedLine("Alex", "We were chatting about the roadmap.", gap=SHORT_GAP),
        ScriptedLine("Alex", "Jarvis, what's next on the list?", gap=0.0),
    ]
    rec = _run(clock, lines, responder, voice)

    assert len(rec.engagements) == 1
    handoff = rec.engagements[0]
    assert handoff.trigger_reason == "summon"
    assert "Jarvis" in handoff.detail
    # The orchestrator assembled the handoff from the decision (the controller
    # does not): it filled the recent excerpt from the window it owns.
    assert "Jarvis, what's next" in handoff.recent_excerpt
    # ...and dispatched it through the engaged seams.
    assert responder.last_handoff is handoff
    assert voice.last_spoken == "On it."


def test_summon_fires_even_with_no_politeness_gap() -> None:
    """Path A ignores the gate entirely — back-to-back lines, no silence."""
    clock = SimulatedClock()
    responder = FakeResponder()
    voice = FakeVoice()
    lines = [
        ScriptedLine("Alex", "Talking fast here.", gap=0.0),
        ScriptedLine("Alex", "Jarvis help.", gap=0.0),
    ]
    rec = _run(clock, lines, responder, voice)
    assert [h.trigger_reason for h in rec.engagements] == ["summon"]


# ---------------------------------------------------------------------------
# Path B — interjection — timing gate behavior through the orchestrator.
# ---------------------------------------------------------------------------
def test_interjection_holds_when_speech_resumes_before_the_gap() -> None:
    """A wall whose silence never opens the gap (next line lands too soon) stays silent."""
    clock = SimulatedClock()
    responder = FakeResponder()
    voice = FakeVoice()
    lines = [
        ScriptedLine("Alex", "Setting some context for the window.", gap=SHORT_GAP),
        ScriptedLine("Sam", "More context to clear cold-start.", gap=SHORT_GAP),
        # A real factual-gap wall, but only a short pause after it — the next
        # speaker comes back before the politeness gap elapses.
        ScriptedLine("Sam", "What was the date again?", gap=SHORT_GAP),
        ScriptedLine("Alex", "Oh right, it was Tuesday.", gap=SHORT_GAP),
    ]
    rec = _run(clock, lines, responder, voice)
    assert rec.interjections == []  # never interjected
    assert rec.engagements == []  # and so never engaged


def test_interjection_fires_after_the_politeness_gap() -> None:
    clock = SimulatedClock()
    responder = FakeResponder()
    voice = FakeVoice()
    lines = [
        ScriptedLine("Alex", "Some opening chatter to warm up.", gap=SHORT_GAP),
        ScriptedLine("Sam", "A second line to clear the cold-start fence.", gap=SHORT_GAP),
        ScriptedLine("Sam", "I don't remember what the budget was.", gap=LONG_GAP),
    ]
    rec = _run(clock, lines, responder, voice)
    assert len(rec.interjections) == 1
    assert rec.interjections[0].category is WallCategory.FACTUAL_GAP
    # A fired interjection IS an engagement: it crossed the boundary.
    assert len(rec.engagements) == 1
    assert rec.engagements[0].trigger_reason == "wall:factual_gap"


def test_back_off_suppresses_the_same_offer_twice_in_a_row() -> None:
    """Two identical walls, both past the gap → only the first interjects (no nagging)."""
    clock = SimulatedClock()
    responder = FakeResponder()
    voice = FakeVoice()
    # Same factual-gap offer twice via a scripted backend (so both walls are
    # byte-identical category+offer); both lines open the politeness gap.
    backend = FakeWallBackend(
        verdicts=[
            wall("factual_gap", 0.82, offer="I can find that — want me to?"),
            wall("factual_gap", 0.82, offer="I can find that — want me to?"),
        ]
    )
    lines = [
        ScriptedLine("Alex", "Warm-up line one for the window.", gap=SHORT_GAP),
        ScriptedLine("Sam", "Warm-up line two for cold-start.", gap=SHORT_GAP),
        # Both of these carry a wall signal ("what was" / "no idea") so the
        # detector runs; the scripted backend returns the same offer for each.
        ScriptedLine("Alex", "What was the figure?", gap=LONG_GAP),
        ScriptedLine("Sam", "No idea either.", gap=LONG_GAP),
    ]
    rec = _run(clock, lines, responder, voice, wall_backend=backend)
    assert len(rec.interjections) == 1  # the repeat was backed off


def test_low_confidence_wall_does_not_interject() -> None:
    """A wall below the confidence floor stays silent even with the gap wide open."""
    clock = SimulatedClock()
    responder = FakeResponder()
    voice = FakeVoice()
    backend = FakeWallBackend(verdict=wall("factual_gap", 0.40, offer="Maybe I can help?"))
    lines = [
        ScriptedLine("Alex", "Warm-up one.", gap=SHORT_GAP),
        ScriptedLine("Sam", "Warm-up two.", gap=SHORT_GAP),
        ScriptedLine("Sam", "What was it again?", gap=LONG_GAP),
    ]
    rec = _run(clock, lines, responder, voice, wall_backend=backend)
    assert rec.interjections == []


# ---------------------------------------------------------------------------
# Summary delta-update behavior through the orchestrator.
# ---------------------------------------------------------------------------
def test_summary_refreshes_on_a_topic_shift_via_the_injected_backend() -> None:
    """The injected summarizer is what produces the summary; a pivot drives a 2nd update."""
    clock = SimulatedClock()
    responder = FakeResponder()
    voice = FakeVoice()
    summarizer = FakeSummarizer(returns=["first summary", "second summary"])
    # Two distinct topics; a tight window so the first topic ages out and the
    # pivot registers as a shift.
    lines = [
        ScriptedLine("Alex", "The database migration plan needs review.", gap=SHORT_GAP),
        ScriptedLine("Sam", "Right, the migration schema and the rollout.", gap=SHORT_GAP),
        ScriptedLine("Alex", "The migration cutover window is tight.", gap=SHORT_GAP),
        ScriptedLine("Sam", "Totally separate — lunch plans for Friday?", gap=SHORT_GAP),
        ScriptedLine("Alex", "Friday lunch sushi sounds great honestly.", gap=SHORT_GAP),
        ScriptedLine("Sam", "Sushi Friday lunch, let's book a table.", gap=SHORT_GAP),
    ]
    rec = _run(clock, lines, responder, voice, summarizer_backend=summarizer, max_utterances=3)
    # The orchestrator emitted exactly what the injected backend returned, in
    # order — proving the summary text comes from the seam, not the orchestrator.
    assert rec.summaries == ["first summary", "second summary"]
    assert summarizer.call_count == 2


def test_cold_start_no_summary_below_minimum() -> None:
    """Fewer than the cold-start minimum of utterances → no summary, no events."""
    clock = SimulatedClock()
    responder = FakeResponder()
    voice = FakeVoice()
    lines = [
        ScriptedLine("Alex", "Just one line.", gap=SHORT_GAP),
        ScriptedLine("Sam", "And a second.", gap=SHORT_GAP),
    ]
    rec = _run(clock, lines, responder, voice)
    assert rec.summaries == []
    assert rec.engagements == []


# ---------------------------------------------------------------------------
# Wiring / determinism guards.
# ---------------------------------------------------------------------------
def test_no_events_for_silent_non_wall_chatter() -> None:
    """Ordinary statements with no wall cue and no wake word emit no interjections."""
    clock = SimulatedClock()
    responder = FakeResponder()
    voice = FakeVoice()
    lines = [
        ScriptedLine("Alex", "The weather is nice today.", gap=LONG_GAP),
        ScriptedLine("Sam", "It really is a lovely afternoon.", gap=LONG_GAP),
        ScriptedLine("Alex", "We should sit outside later on.", gap=LONG_GAP),
    ]
    rec = _run(clock, lines, responder, voice)
    assert rec.interjections == []
    assert rec.engagements == []
    assert not responder.called
    assert not voice.called


def test_run_is_deterministic() -> None:
    """Same script, two runs → identical emitted-event streams (no hidden clock)."""
    lines = [
        ScriptedLine("Alex", "Warm-up one for the window.", gap=SHORT_GAP),
        ScriptedLine("Sam", "Warm-up two for cold-start.", gap=SHORT_GAP),
        ScriptedLine("Sam", "I can't recall the room number.", gap=LONG_GAP),
        ScriptedLine("Alex", "Jarvis, look it up.", gap=0.0),
    ]
    rec_a = _run(SimulatedClock(), lines, FakeResponder(), FakeVoice())
    rec_b = _run(SimulatedClock(), lines, FakeResponder(), FakeVoice())
    assert rec_a.summaries == rec_b.summaries
    assert [i.category for i in rec_a.interjections] == [i.category for i in rec_b.interjections]
    assert [h.trigger_reason for h in rec_a.engagements] == [
        h.trigger_reason for h in rec_b.engagements
    ]


def test_scripted_source_drives_clock_and_gate() -> None:
    """ScriptedSource advances the clock and arms the gate as it plays (unit-level)."""
    clock = SimulatedClock()
    gate = TurnTakingGate(clock.now)
    source = ScriptedSource(
        [ScriptedLine("A", "hi", gap=LONG_GAP)],
        clock_advance=clock.advance,
        now=clock.now,
        gate=gate,
    )
    utts = list(source.utterances())
    assert len(utts) == 1
    # Time advanced (speech duration + the gap), and the post-line silence opened
    # the politeness gap on the shared gate.
    assert clock.now() >= LONG_GAP
    assert gate.politeness_gap_elapsed() is True


def test_print_stand_ins_satisfy_the_seams() -> None:
    """The demo's PrintResponder/PrintVoice are drop-in for the engaged seams."""
    clock = SimulatedClock()
    lines = [ScriptedLine("Alex", "Jarvis, status please.", gap=0.0)]
    # Should run without error using the print stand-ins (smoke for the demo path).
    rec = _run(clock, lines, PrintResponder(), PrintVoice())  # type: ignore[arg-type]
    assert [h.trigger_reason for h in rec.engagements] == ["summon"]


def test_speech_seconds_must_be_non_negative() -> None:
    clock = SimulatedClock()
    gate = TurnTakingGate(clock.now)
    with pytest.raises(ValueError, match="speech_seconds"):
        ScriptedSource(
            [], clock_advance=clock.advance, now=clock.now, gate=gate, speech_seconds=-1.0
        )
