"""AttentionLayer — the orchestrator wiring the ambient half (T-008).

This is where the six pure-logic core modules and the I/O adapter seams become a
running pipeline. It is the *real-package* successor to the prototype's monolithic
``AttentionLayer`` (``prototypes/attention-layer/attention_layer.py``): same
observable behavior, but built on the real modules and the **decision/handoff
boundary** — ``SummonController`` emits a ``SummonDecision``, and *this*
orchestrator assembles the ``EngagementHandoff`` from it (it owns the summary and
the window; the controller does not — DECISIONS.md 2026-06-15).

## What it wires (module map §"Event flow")

    ScriptedSource ─► [is summon?] ─yes─► engage(Path A) ─► EngagementHandoff
          │                                                       │
          └─no─► RollingWindow.add
                     │
                     ├─► LivingSummary.consider_update ─► on_summary_update
                     │
                     └─[cheap wall signal?]─► WallDetector.detect
                                                   │
                                                   ▼
                                       SummonController.consider_interjection
                                          (reads TurnTakingGate predicates)
                                                   │ Path B fires
                                                   ▼
                                  on_interjection  +  EngagementHandoff dispatch

## The three emitted events (module map §AttentionLayer)

The orchestrator emits exactly three things, as injected callbacks — so a test
asserts on emitted events and the demo prints them:

* ``on_summary_update(text)`` — the living summary refreshed.
* ``on_interjection(Interjection)`` — a Path-B offer cleared every gate.
* ``on_engagement(EngagementHandoff)`` — Jarvis engaged (either path); the handoff
  crossed to the engaged half.

On **either** engagement path the orchestrator also dispatches the assembled
``EngagementHandoff`` through the injected ``EngagedResponder`` → ``VoiceOutput``
seams (the spoken answer), so the full ambient→engaged round-trip runs in mock
mode with no audio and no network.

## Timing (no hidden clock)

The orchestrator holds the ``TurnTakingGate``, and the ``TranscriptSource`` drives
that same gate's speech-boundary events + the shared injected clock as it plays
(see ``ScriptedSource``). So when ``SummonController.consider_interjection`` reads
the gate's politeness-gap / speech-resumed predicates, they reflect the
conversation's pacing — all on the one injected clock, no ``time.monotonic()``.

Pure orchestration, no I/O — every boundary is an injected seam.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from jarvis.adapters.backends import (
    HeuristicSummarizerBackend,
    HeuristicWallBackend,
)
from jarvis.adapters.engaged import EngagedResponder, VoiceOutput
from jarvis.adapters.transcript_source import (
    ScriptedLineInput,
    ScriptedSource,
    TranscriptSource,
)
from jarvis.core.living_summary import LivingSummary, SummarizerBackend
from jarvis.core.rolling_window import RollingWindow
from jarvis.core.summon_controller import SummonController
from jarvis.core.turn_taking_gate import TurnTakingGate
from jarvis.core.wall_detector import WallBackend, WallDetector
from jarvis.types import EngagementHandoff, Interjection, SummonDecision, Utterance, WallVerdict

# The wake word that fires Path A (summon). Ported from the prototype's WAKE_WORD.
WAKE_WORD = "jarvis"

# Window sizing for the orchestrator (ported from the prototype's tunables). Note
# the T-004 window-sizing gotcha: a topic shift only registers once the *old*
# topic's utterances roll out of the window. These bounds are wide enough to hold
# a real recent context yet let a genuine pivot age the old topic out.
DEFAULT_WINDOW_MAX_UTTERANCES = 12
DEFAULT_WINDOW_MAX_SECONDS = 120.0

# How many trailing window lines go into the EngagementHandoff's recent_excerpt.
EXCERPT_LINES = 4

_RE_WAKE_WORD = re.compile(rf"\b{WAKE_WORD}\b", re.IGNORECASE)

# Cheap wall-signal gate (module map §"Event flow" step 5): only run the (in
# Phase 2: expensive) WallDetector when the latest line carries one of these
# surface cues. Ported from the prototype's ``_has_wall_signal``. This is a
# *cheap pre-filter*, not the wall decision — the detector + SummonController make
# that. It keeps the brain from running on every utterance (PRD FR-3.4).
_RE_WALL_SIGNAL = re.compile(
    r"\b(i wish|if only|i (don'?t|do not) (know|remember)|what (was|were)|"
    r"can'?t recall|no idea|stuck|going in circles|not sure)\b",
    re.IGNORECASE,
)


def _has_wall_signal(text: str) -> bool:
    """Whether a line carries a cheap surface cue worth running detection on."""
    if text.rstrip().endswith("?"):
        return True
    return bool(_RE_WALL_SIGNAL.search(text))


class AttentionLayer:
    """Orchestrates the ambient half end-to-end over injected seams (T-008).

    Args:
        window: the ``RollingWindow`` (bounded by count + time on the injected
            clock).
        summary: the ``LivingSummary`` (delta-updated via its summarizer backend).
        detector: the ``WallDetector`` (over its wall backend).
        controller: the ``SummonController`` — holds the injected ``TurnTakingGate``
            (the same gate the ``TranscriptSource`` drives).
        responder: the ``EngagedResponder`` seam — composes the engaged line.
        voice: the ``VoiceOutput`` seam — emits the line.
        on_summary_update / on_interjection / on_engagement: optional event
            callbacks. The demo prints them; a test records them.

    Prefer the ``build`` / ``run_scripted`` classmethods for the common wiring;
    the explicit constructor is for tests that inject specific module instances.
    """

    def __init__(
        self,
        window: RollingWindow,
        summary: LivingSummary,
        detector: WallDetector,
        controller: SummonController,
        responder: EngagedResponder,
        voice: VoiceOutput,
        on_summary_update: Callable[[str], None] | None = None,
        on_interjection: Callable[[Interjection], None] | None = None,
        on_engagement: Callable[[EngagementHandoff], None] | None = None,
    ) -> None:
        self._window = window
        self._summary = summary
        self._detector = detector
        self._controller = controller
        self._responder = responder
        self._voice = voice
        self._on_summary_update = on_summary_update
        self._on_interjection = on_interjection
        self._on_engagement = on_engagement

        # T-302: the pending wall verdict from the most recent ingest that returned
        # None (gap not yet open) and whose verdict.is_wall is True.  tick() re-
        # evaluates this verdict periodically during the silence that follows.
        #
        # Clearing / staleness policy (rationale in docs/architecture/phase3-invariants.md §3):
        #   - Set when: consider_interjection(verdict) returns None AND verdict.is_wall is True.
        #   - Cleared when: the verdict fires (Path B engaged) — fire-at-most-once.
        #   - Replaced when: a newer wall verdict arrives at ingest (fresher context wins).
        #   - Cleared when: Path A (summon) fires via _engage — engagement ended the ambient half.
        #   - NOT cached: non-wall verdicts (verdict.is_wall is False); nothing to wait for.
        #
        # The same WallVerdict object is reused across all tick() calls so the
        # back-off signature (category::offer) is STABLE — solving the non-deterministic
        # Qwen offer double-fire found in NOTES.md T-204 live run.  No new model call
        # is made during the silence window; the cached verdict is what is re-evaluated.
        self._pending_wall: WallVerdict | None = None

    # -- the one public ingest path ------------------------------------------

    def ingest(self, u: Utterance) -> None:
        """Run one utterance through the layer (module map §"Event flow").

        Path A (summon) short-circuits everything: the wake word is its own
        permission to speak, so it engages immediately and unconditionally. Any
        other utterance flows through window → summary → (cheap-signal-gated)
        wall detection → the Path-B interjection arbitration.
        """
        if _RE_WAKE_WORD.search(u.text):
            # Path A — summon. Add to the window first so the excerpt includes the
            # summoning line, then engage immediately (no gate, no wall, no floor).
            self._window.add(u)
            decision = self._controller.on_summon(detail=u.text)
            self._engage(decision)
            return

        self._window.add(u)

        # Living summary, delta-updated (only redraws on a topic shift).
        if self._summary.consider_update(self._window) and self._on_summary_update:
            self._on_summary_update(self._summary.text)

        # Path B — proactive interjection. Gated by a cheap surface cue first so
        # the (Phase-2 model) detector doesn't run on every utterance.
        if _has_wall_signal(u.text):
            verdict = self._detector.detect(self._window.transcript(), self._summary.text)
            decision = self._controller.consider_interjection(verdict)
            if decision is not None:
                self._pending_wall = None  # consumed by Path B fire
                self._interject(decision)
            elif verdict.is_wall:
                # Gap not yet open (or speech resumed), but a real wall was detected.
                # Cache it so tick() can re-evaluate once the politeness gap opens.
                # A newer wall at the next ingest will replace this one (fresher wins).
                self._pending_wall = verdict
            # Non-wall verdicts (is_wall=False) are not cached — nothing to wait for.

    # -- T-302: continuous Path-B re-evaluation during silence ----------------

    def tick(self) -> None:
        """Re-evaluate Path B with the cached wall verdict (called during silence).

        The ``MicSource.utterances()`` generator blocks during silence — ``ingest``
        never runs, so ``SummonController.consider_interjection`` is never called as
        the politeness gap grows.  A background thread in ``live.py`` calls
        ``tick()`` periodically (~200 ms) to give ``consider_interjection`` a chance
        to fire once the gap opens.

        Design properties:

        * **Pure reads only.** ``tick()`` reads time *exclusively* through the gate
          predicates (``politeness_gap_elapsed()`` / ``speech_resumed()``) — the same
          path ``consider_interjection`` always uses.  No new ``time.monotonic()``,
          no new clock ownership.  The one-clock invariant is preserved.
        * **No-op when idle.** If ``_pending_wall`` is ``None`` (no wall was detected
          at the last ingest, or the wall already fired / was cleared by an
          engagement), ``tick()`` returns immediately.
        * **Fires at most once.** The first ``tick()`` that succeeds clears
          ``_pending_wall`` so subsequent ticks are no-ops — solving the double-fire
          bug seen in the T-204 live run with the non-deterministic Qwen offer text.
          The *same* ``WallVerdict`` object is re-evaluated on every tick, so the
          ``category::offer`` back-off signature is stable across all ticks — the
          existing ``SummonController`` back-off de-dupes correctly with no change to
          the qa-gated module.
        * **Abort-on-resume is free.** If speech resumes during the tick loop,
          ``gate.speech_resumed()`` is ``True`` and ``consider_interjection`` returns
          ``None`` — the hard-no is preserved without any new logic.
        * **Thread-safety is the caller's responsibility.** ``tick()`` is a pure
          method with no locking.  The caller (``live.py``'s daemon thread) wraps
          both ``ingest`` and ``tick`` calls in a shared ``threading.Lock`` so the
          two threads never interleave.
        """
        if self._pending_wall is None:
            return
        decision = self._controller.consider_interjection(self._pending_wall)
        if decision is not None:
            self._pending_wall = None  # consumed — fire at most once
            self._interject(decision)

    # -- engagement: assemble the handoff + dispatch to the engaged half ------

    def _interject(self, decision: SummonDecision) -> None:
        """Handle a fired Path-B interjection: emit it, then dispatch the handoff.

        ``decision.interjection`` is the offer (set on every Path-B decision).
        The orchestrator emits it (``on_interjection``) *and* assembles the
        ``EngagementHandoff`` from it and dispatches the engaged answer — a fired
        interjection is an engagement, so it crosses the boundary like a summon.
        """
        assert decision.interjection is not None  # invariant: Path B carries an offer
        if self._on_interjection:
            self._on_interjection(decision.interjection)
        self._engage(decision)

    def _engage(self, decision: SummonDecision) -> None:
        """Assemble the ``EngagementHandoff`` and run the engaged round-trip.

        This is the orchestrator's half of the decision/handoff boundary: the
        ``SummonController`` produced the *decision* (which path + payload); the
        orchestrator adds the ``summary`` and ``recent_excerpt`` it owns and turns
        it into the boundary ``EngagementHandoff``, then dispatches it through the
        ``EngagedResponder`` → ``VoiceOutput`` seams.

        T-302: Clears ``_pending_wall`` on any engagement (Path A or Path B).
        Once Jarvis has engaged on any path, the ambient half is done for this turn
        and there is no wall worth waiting on — the wall's context has been consumed.
        """
        self._pending_wall = None  # consumed — ambient half done for this turn
        handoff = EngagementHandoff(
            trigger_reason=decision.handoff_reason(),
            summary=self._summary.text,
            recent_excerpt=self._recent_excerpt(),
            detail=decision.detail,
        )
        if self._on_engagement:
            self._on_engagement(handoff)
        reply = self._responder.respond(handoff)
        self._voice.speak(reply)

    def _recent_excerpt(self) -> str:
        """The last few rendered window lines — the handoff's ``recent_excerpt``."""
        utts = self._window.utterances()[-EXCERPT_LINES:]
        return "\n".join(f"{u.speaker}: {u.text}" for u in utts)

    # -- convenience builders -------------------------------------------------

    @classmethod
    def build(
        cls,
        *,
        gate: TurnTakingGate,
        now: Callable[[], float],
        responder: EngagedResponder,
        voice: VoiceOutput,
        summarizer_backend: SummarizerBackend | None = None,
        wall_backend: WallBackend | None = None,
        max_utterances: int = DEFAULT_WINDOW_MAX_UTTERANCES,
        max_seconds: float = DEFAULT_WINDOW_MAX_SECONDS,
        interjection_confidence_floor: float | None = None,
        on_summary_update: Callable[[str], None] | None = None,
        on_interjection: Callable[[Interjection], None] | None = None,
        on_engagement: Callable[[EngagementHandoff], None] | None = None,
    ) -> AttentionLayer:
        """Wire a full ``AttentionLayer`` from a gate + clock + the two out seams.

        The model backends default to the Phase-0 heuristics
        (``HeuristicSummarizerBackend`` / ``HeuristicWallBackend``) — pass real
        backends to go live. The ``SummonController`` is built on the **given
        gate**, which the caller (e.g. ``run_scripted`` / a ``MicSource``) is
        responsible for driving with speech-boundary events on the same ``now``.
        """
        window = RollingWindow(max_utterances, max_seconds, now)
        summary = LivingSummary(summarizer_backend or HeuristicSummarizerBackend())
        detector = WallDetector(wall_backend or HeuristicWallBackend())
        if interjection_confidence_floor is None:
            controller = SummonController(gate)
        else:
            controller = SummonController(gate, interjection_confidence_floor)
        return cls(
            window=window,
            summary=summary,
            detector=detector,
            controller=controller,
            responder=responder,
            voice=voice,
            on_summary_update=on_summary_update,
            on_interjection=on_interjection,
            on_engagement=on_engagement,
        )

    def run(self, source: TranscriptSource) -> None:
        """Drive every ``Utterance`` from a ``TranscriptSource`` through ``ingest``."""
        for u in source.utterances():
            self.ingest(u)

    @classmethod
    def run_scripted(
        cls,
        lines: Iterable[ScriptedLineInput],
        *,
        now: Callable[[], float],
        clock_advance: Callable[[float], object],
        responder: EngagedResponder,
        voice: VoiceOutput,
        gate: TurnTakingGate | None = None,
        summarizer_backend: SummarizerBackend | None = None,
        wall_backend: WallBackend | None = None,
        max_utterances: int = DEFAULT_WINDOW_MAX_UTTERANCES,
        max_seconds: float = DEFAULT_WINDOW_MAX_SECONDS,
        interjection_confidence_floor: float | None = None,
        on_summary_update: Callable[[str], None] | None = None,
        on_interjection: Callable[[Interjection], None] | None = None,
        on_engagement: Callable[[EngagementHandoff], None] | None = None,
        on_utterance: Callable[[Utterance], None] | None = None,
    ) -> AttentionLayer:
        """Build + run the orchestrator over a scripted conversation, end-to-end.

        Constructs a ``TurnTakingGate`` (if not given), a ``ScriptedSource`` that
        drives that gate + the injected clock as it plays, wires a full
        ``AttentionLayer``, and runs it. This is the one-call entry point the demo
        and the acceptance tests use: clock + gate + source + orchestrator all
        share the single injected ``now`` / ``clock_advance``, so the whole
        pipeline is deterministic with zero real time elapsed.

        ``on_utterance`` (optional) is called as each line is played, before it is
        ingested — the demo uses it to print the transcript line.

        Returns the constructed ``AttentionLayer`` (so tests can inspect it).
        """
        if gate is None:
            gate = TurnTakingGate(now)
        layer = cls.build(
            gate=gate,
            now=now,
            responder=responder,
            voice=voice,
            summarizer_backend=summarizer_backend,
            wall_backend=wall_backend,
            max_utterances=max_utterances,
            max_seconds=max_seconds,
            interjection_confidence_floor=interjection_confidence_floor,
            on_summary_update=on_summary_update,
            on_interjection=on_interjection,
            on_engagement=on_engagement,
        )
        source = ScriptedSource(lines, clock_advance=clock_advance, now=now, gate=gate)
        for u in source.utterances():
            if on_utterance is not None:
                on_utterance(u)
            layer.ingest(u)
        return layer
