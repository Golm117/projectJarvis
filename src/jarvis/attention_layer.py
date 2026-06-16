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

# Post-engagement cooldown (T-503, the interjection-precision fix). After Jarvis
# engages on EITHER path (a wake-word summon or a fired interjection), the user is
# in a dialogue *with* Jarvis — not hitting a wall *between humans* — so an ambient
# Path-B interjection in the seconds that follow is noise, not help. The classic
# case (NOTES T-502): the user summons Jarvis, then says "What do you need?", which
# the detector flags as factual_gap @ 0.95 and would fire on — a false interjection
# (Jarvis offering to look up its own near-rhetorical question). This window
# suppresses ambient Path-B fires for a short, configurable interval after an
# engagement. Calibrated on the eval (T-503): the seeded "What do you need?" FP
# fires at 5.5 s after its engagement (speech_end 3.5 s + the 2 s politeness gap),
# so the cooldown must exceed 5.5 s to kill it. The orchestrator's empirical sweep
# confirmed 4/5/5.5 s leave the FP in (precision 0.60) while 6/7/8 s suppress it
# (precision 0.75). 6.0 s is the human-chosen value (sign-off 2026-06-16): the most
# responsive setting that works, a 0.5 s margin over the 5.5 s fire, and the same
# 0.75 precision as 8 s — the cooldown only ever touches that one FP, no legitimate
# fire is affected (all seeded TPs are stand-alone walls with no preceding
# engagement). Constructor-injected so qa-tuning owns the value in one place;
# measured on the SimulatedClock, never a real sleep. A value of 0.0 disables the
# rule (no suppression window).
DEFAULT_POST_ENGAGEMENT_COOLDOWN_SECONDS = 6.0

# Pending-wall staleness TTL (T-503, carry-forward from the T-302/T-303 review).
# tick() caches the wall verdict from the last ingest and re-evaluates it during
# the silence that follows, fire-at-most-once. The replace-with-fresher-wall and
# clear-on-engagement policies bound it in practice, but a wall cached while the
# conversation quietly moves on (no fresh wall to replace it, no engagement to
# clear it) could in theory fire *late*, once a silence finally opens, as a stale
# false interjection about a topic that has passed. This TTL ages a cached pending
# wall out: once it has waited this long without firing, tick() drops it. Sized
# (12.0 s) well beyond the 2 s politeness gap a legitimate wall fires within, so a
# real wall always fires long before its TTL — the TTL only ever catches a wall
# that has genuinely gone stale. Constructor-injected (qa-tuning owns it); measured
# on the SimulatedClock. A value of 0.0 disables the TTL (no staleness clear).
DEFAULT_PENDING_WALL_TTL_SECONDS = 12.0

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
        now: Callable[[], float] | None = None,
        post_engagement_cooldown_seconds: float = DEFAULT_POST_ENGAGEMENT_COOLDOWN_SECONDS,
        pending_wall_ttl_seconds: float = DEFAULT_PENDING_WALL_TTL_SECONDS,
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

        if post_engagement_cooldown_seconds < 0:
            raise ValueError(
                "post_engagement_cooldown_seconds must be >= 0, got "
                f"{post_engagement_cooldown_seconds}"
            )
        if pending_wall_ttl_seconds < 0:
            raise ValueError(
                f"pending_wall_ttl_seconds must be >= 0, got {pending_wall_ttl_seconds}"
            )
        # The single injected clock (the same time.monotonic / SimulatedClock the
        # gate + window already share — the one-clock invariant). Used ONLY to age
        # the two T-503 timers below (cooldown + pending-wall TTL); the speak/abort
        # timing still comes through the gate's predicates, never from here. None ⇒
        # both T-503 timers are inert (legacy callers that pass no clock keep the
        # pre-T-503 behavior); both default to a real value via build()/run_scripted.
        self._now = now
        self._post_engagement_cooldown = float(post_engagement_cooldown_seconds)
        self._pending_wall_ttl = float(pending_wall_ttl_seconds)
        # When Jarvis last engaged (either path), on the injected clock. None ⇒ never
        # engaged yet. The post-engagement cooldown suppresses ambient Path-B fires
        # while now() - _last_engagement_at < _post_engagement_cooldown (T-503).
        self._last_engagement_at: float | None = None

        # T-302: the pending wall verdict from the most recent ingest that returned
        # None (gap not yet open) and whose verdict.is_wall is True.  tick() re-
        # evaluates this verdict periodically during the silence that follows.
        #
        # Clearing / staleness policy (rationale in docs/architecture/phase3-invariants.md §3):
        #   - Set when: consider_interjection(verdict) returns None AND verdict.is_wall is True.
        #   - Cleared when: the verdict fires (Path B engaged) — fire-at-most-once.
        #   - Replaced when: a newer wall verdict arrives at ingest (fresher context wins).
        #   - Cleared when: Path A (summon) fires via _engage — engagement ended the ambient half.
        #   - Cleared when: the pending wall's TTL elapses (T-503 staleness clear) — see tick().
        #   - NOT cached: non-wall verdicts (verdict.is_wall is False); nothing to wait for.
        #
        # The same WallVerdict object is reused across all tick() calls so the
        # back-off signature (category::offer) is STABLE — solving the non-deterministic
        # Qwen offer double-fire found in NOTES.md T-204 live run.  No new model call
        # is made during the silence window; the cached verdict is what is re-evaluated.
        self._pending_wall: WallVerdict | None = None
        # When _pending_wall was cached, on the injected clock — the start of its
        # TTL. None whenever _pending_wall is None. tick() drops a pending wall once
        # now() - _pending_wall_cached_at >= _pending_wall_ttl, so a wall cached
        # across many off-topic turns can't fire late as a stale false interjection
        # after the conversation has moved on (T-503 carry-forward from T-302/T-303).
        self._pending_wall_cached_at: float | None = None

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
            # T-503 post-engagement cooldown: inside the window after Jarvis engaged,
            # an ambient wall is the user talking *to* Jarvis, not a wall *between*
            # humans — suppress it. Checked before consider_interjection so a
            # suppressed wall is neither fired nor cached (it does not arm tick()).
            if verdict.is_wall and self._in_post_engagement_cooldown():
                return
            decision = self._controller.consider_interjection(verdict)
            if decision is not None:
                self._clear_pending_wall()  # consumed by Path B fire
                self._interject(decision)
            elif verdict.is_wall:
                # Gap not yet open (or speech resumed), but a real wall was detected.
                # Cache it so tick() can re-evaluate once the politeness gap opens.
                # A newer wall at the next ingest will replace this one (fresher wins).
                self._cache_pending_wall(verdict)
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

        T-503 additions (both gated on the injected clock; inert if no clock /
        the knob is 0.0, preserving the pre-T-503 behavior):

        * **Staleness TTL.** A pending wall that has waited longer than
          ``pending_wall_ttl_seconds`` without firing is dropped here, so a wall
          cached while the conversation quietly moved on can't fire late as a stale
          false interjection.
        * **Post-engagement cooldown.** A pending wall is not fired while still
          inside the cooldown after an engagement (the same suppression ``ingest``
          applies) — it is held (not dropped), so it can still fire once the
          cooldown passes, unless its TTL drops it first.
        """
        if self._pending_wall is None:
            return
        if self._pending_wall_is_stale():
            self._clear_pending_wall()  # T-503: aged out — drop the stale wall
            return
        if self._in_post_engagement_cooldown():
            return  # T-503: hold (don't fire) during the post-engagement window
        decision = self._controller.consider_interjection(self._pending_wall)
        if decision is not None:
            self._clear_pending_wall()  # consumed — fire at most once
            self._interject(decision)

    # -- T-503 timers (post-engagement cooldown + pending-wall TTL) ------------

    def _in_post_engagement_cooldown(self) -> bool:
        """Whether an ambient Path-B fire is currently suppressed by the cooldown.

        ``True`` when Jarvis engaged within the last ``post_engagement_cooldown``
        seconds on the injected clock. Inert (always ``False``) when no clock was
        injected, the cooldown is 0.0, or no engagement has happened yet.
        """
        if self._now is None or self._post_engagement_cooldown <= 0.0:
            return False
        if self._last_engagement_at is None:
            return False
        return (self._now() - self._last_engagement_at) < self._post_engagement_cooldown

    def _pending_wall_is_stale(self) -> bool:
        """Whether the cached pending wall has outlived its TTL (T-503).

        ``True`` once the wall has been cached for at least ``pending_wall_ttl``
        seconds on the injected clock. Inert when no clock was injected, the TTL is
        0.0, or nothing is cached.
        """
        if self._now is None or self._pending_wall_ttl <= 0.0:
            return False
        if self._pending_wall_cached_at is None:
            return False
        return (self._now() - self._pending_wall_cached_at) >= self._pending_wall_ttl

    def _cache_pending_wall(self, verdict: WallVerdict) -> None:
        """Cache a wall for tick() re-evaluation, stamping its TTL start (T-503)."""
        self._pending_wall = verdict
        self._pending_wall_cached_at = None if self._now is None else self._now()

    def _clear_pending_wall(self) -> None:
        """Clear the cached pending wall + its TTL stamp (single clearing point)."""
        self._pending_wall = None
        self._pending_wall_cached_at = None

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

        T-503: Stamps ``_last_engagement_at`` from the injected clock so the
        post-engagement cooldown can suppress ambient Path-B fires in the window
        that follows. Both a wake-word summon and a fired interjection are
        engagements, so both arm the cooldown — after either, the user is in a
        dialogue *with* Jarvis.
        """
        self._clear_pending_wall()  # consumed — ambient half done for this turn
        if self._now is not None:
            self._last_engagement_at = self._now()  # T-503: arm the cooldown
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
        post_engagement_cooldown_seconds: float = DEFAULT_POST_ENGAGEMENT_COOLDOWN_SECONDS,
        pending_wall_ttl_seconds: float = DEFAULT_PENDING_WALL_TTL_SECONDS,
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

        The same ``now`` is injected into the layer (alongside the window + gate)
        so the T-503 post-engagement cooldown + pending-wall TTL age on the one
        shared clock (the one-clock invariant). Their defaults are the calibrated
        T-503 values; pass 0.0 to either to disable that rule.
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
            now=now,
            post_engagement_cooldown_seconds=post_engagement_cooldown_seconds,
            pending_wall_ttl_seconds=pending_wall_ttl_seconds,
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
        post_engagement_cooldown_seconds: float = DEFAULT_POST_ENGAGEMENT_COOLDOWN_SECONDS,
        pending_wall_ttl_seconds: float = DEFAULT_PENDING_WALL_TTL_SECONDS,
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
            post_engagement_cooldown_seconds=post_engagement_cooldown_seconds,
            pending_wall_ttl_seconds=pending_wall_ttl_seconds,
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
