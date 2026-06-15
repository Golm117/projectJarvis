"""Core data types that cross the attention-layer seams.

These are the small, frozen value objects that travel between modules and across
the I/O seams documented in ``docs/architecture/module-map.md`` §"Data types".
They are deliberately dumb — no behavior, no I/O, no hidden clock — so every
module and every adapter (the mic source, the local-ml backend, the voice path)
agrees on one shape.

Frozen status (module map):

* ``Utterance`` — **frozen (T-002)**. Depended on project-wide: the whole
  ``RollingWindow`` reads it, and sensing-engineer's ``MicSource`` produces it.
  Its three fields (``speaker``, ``text``, ``ts``) are the contract.

* ``WallVerdict`` — **frozen (T-005)**. The structured result a ``WallBackend``
  returns and ``WallDetector`` surfaces. The heuristic mock backend produces it
  in Phase 0; local-ml-engineer's real Qwen2.5/MLX backend produces this **exact
  shape** via structured output in Phase 2 (T-203). See the "Contract for the
  real backend (T-203)" note below.

``Interjection``, ``SummonDecision`` and ``EngagementHandoff`` land with their
tasks (T-007/T-008). They are documented in the module map and added here as
those tasks land so each type freezes exactly when its first real consumer does.

* ``Interjection`` / ``SummonDecision`` — **added (T-007)**. ``SummonController``
  is a *pure decision* machine: it emits a ``SummonDecision`` (which path fired,
  the trigger reason, and — for a Path-B fire — the ``Interjection`` offer). It
  does **not** assemble the full ``EngagementHandoff`` (it holds neither the
  living summary nor the rolling window — those live in the orchestrator). The
  orchestrator (T-008) turns a summon ``SummonDecision`` into an
  ``EngagementHandoff`` by adding the summary + recent excerpt it owns. See the
  module map §"SummonController" (the decision/handoff boundary) and DECISIONS.md.

* ``EngagementHandoff`` — documented here (T-007) so the boundary shape is frozen
  for voice-integration-engineer; the orchestrator assembles it in T-008.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True)
class Utterance:
    """A transcribed chunk of speech — the atom of the ambient pipeline.

    Frozen (T-002): immutable so a single ``Utterance`` can sit in the
    ``RollingWindow``, be rendered into a transcript, and cross to the engaged
    half without any stage mutating it.

    Fields:
        speaker: who spoke (e.g. ``"Alex"``; the ASR/diarization layer supplies
            this — for v0 a single label is fine).
        text: the transcribed words.
        ts: monotonic seconds, supplied by the *producer* (the injected clock or
            the VAD timeline) — **never** filled from a hidden ``time.monotonic()``
            here. Keeping ``ts`` an explicit, required field is what lets the
            ``RollingWindow`` evict by elapsed time deterministically under
            qa-tuning's ``SimulatedClock`` (module map §"Cross-cutting design
            constraints" #1).
    """

    speaker: str
    text: str
    ts: float


class WallCategory(StrEnum):
    """The kind of wall a conversation hit — why Jarvis might offer help.

    A ``str`` enum so a value is both a typed member (``WallCategory.FACTUAL_GAP``)
    and its wire string (``"factual_gap"``) — the latter is what the real backend
    (T-203) emits via a JSON-schema ``enum`` and what crosses into
    ``EngagementHandoff.trigger_reason`` as ``"wall:<category>"``. Ported from the
    prototype's wall taxonomy (``Backend._mock_detect_wall``).

    Members:
        UNANSWERED_QUESTION: someone asked something nobody answered.
        FACTUAL_GAP: an expressed uncertainty — "I don't know", "what was…".
        STUCK_POINT: the conversation is looping or stalled.
        EXPLICIT_ASK: a wish said into the air — "I wish I knew…".
        NONE: no wall; stay silent. The category of every non-wall verdict.
    """

    UNANSWERED_QUESTION = "unanswered_question"
    FACTUAL_GAP = "factual_gap"
    STUCK_POINT = "stuck_point"
    EXPLICIT_ASK = "explicit_ask"
    NONE = "none"


@dataclass(frozen=True)
class WallVerdict:
    """What a ``WallBackend`` returns and ``WallDetector`` surfaces (T-005).

    Frozen (T-005): the structured verdict that says whether the conversation hit
    a wall Jarvis could help with, how sure we are, and the single line it would
    say. It carries the signal; it does **not** decide whether to speak — the
    confidence threshold (``WALL_CONFIDENCE_TO_SPEAK``) is ``SummonController``
    policy (T-007), not part of this type. That keeps the detector a pure sensor
    and the speak/stay-silent decision in one place.

    Fields:
        is_wall: whether a wall was detected at all. ``False`` ⇒ stay silent.
        category: which :class:`WallCategory`. ``NONE`` iff ``is_wall`` is False.
        confidence: the backend's confidence in ``[0.0, 1.0]``. Surfaced raw;
            ``SummonController`` applies the speak threshold to it downstream.
        offer: the single sentence Jarvis would say if it spoke. Empty for a
            non-wall verdict.

    Construct the common non-wall result with :meth:`none`.
    """

    is_wall: bool
    category: WallCategory
    confidence: float
    offer: str

    @classmethod
    def none(cls) -> WallVerdict:
        """The 'no wall, stay silent' verdict — the common default."""
        return cls(is_wall=False, category=WallCategory.NONE, confidence=0.0, offer="")


class TriggerReason(StrEnum):
    """Why ``SummonController`` decided Jarvis should engage (T-007).

    A ``str`` enum so a value is both a typed member and its wire string. It
    distinguishes the two initiation paths of the asymmetric dual-summon model
    (DECISIONS.md 2026-06-15, "Asymmetric dual-summon"):

    Members:
        SUMMON: Path A — a wake word fired. Immediate, unconditional engagement.
        INTERJECTION: Path B — a detected wall cleared the confidence floor and
            the politeness gap with no resumed speech. A proactive offer.
    """

    SUMMON = "summon"
    INTERJECTION = "interjection"


@dataclass(frozen=True)
class Interjection:
    """A Path-B proactive offer that cleared every gate (T-007).

    Frozen (T-007): the offer ``SummonController`` emits when a detected wall
    survived the confidence floor, the politeness gap, the abort-on-resume check,
    and the back-off de-dupe. It carries just enough for the orchestrator to act
    and for the precision eval (T-010) to score a fire by category + confidence.

    Fields:
        category: the :class:`WallCategory` of the wall being offered help on
            (never ``NONE`` — an interjection only exists for a real wall).
        offer: the single spoken-style sentence Jarvis would say.
        confidence: the wall's confidence in ``[0.0, 1.0]`` (already ``>=`` the
            controller's interjection floor — surfaced so the eval can sweep it).
    """

    category: WallCategory
    offer: str
    confidence: float


@dataclass(frozen=True)
class EngagementHandoff:
    """The boundary output crossing into the engaged half (T-007 shape; T-008 assembles).

    The context package the engaged path (voice-integration-engineer's
    ``EngagedResponder`` → ``VoiceOutput``) receives when Jarvis engages. Its
    *shape* is frozen here so the seam is stable; the orchestrator (T-008) is what
    actually *builds* it, because it owns the living summary and the rolling
    window. ``SummonController`` does not — it emits a :class:`SummonDecision`,
    and the orchestrator adds ``summary`` + ``recent_excerpt``.

    Fields:
        trigger_reason: the wire string for why Jarvis engaged —
            ``"summon"`` (Path A) or ``"wall:<category>"`` (Path B), built from
            the :class:`SummonDecision` (see :meth:`SummonDecision.handoff_reason`).
        summary: ``LivingSummary.text`` at engage time (orchestrator-supplied).
        recent_excerpt: the last few rendered transcript lines (orchestrator-supplied).
        detail: free-form extra, e.g. the summon utterance text. Empty by default.
    """

    trigger_reason: str
    summary: str
    recent_excerpt: str
    detail: str = ""


@dataclass(frozen=True)
class SummonDecision:
    """What ``SummonController`` emits when Jarvis should engage (T-007).

    Frozen (T-007): the *pure engagement decision* — which path fired and the
    payload the orchestrator needs to finish the job. ``SummonController`` is a
    decision machine, not a handoff assembler: it holds neither the living summary
    nor the rolling window, so it cannot build the full
    :class:`EngagementHandoff`. It returns this instead, and the orchestrator
    (T-008) assembles the handoff (Path A) or dispatches the offer (Path B) from
    it. This decision/handoff split is logged in DECISIONS.md.

    Exactly one of the two paths is represented:

    * **Path A — summon** (``reason is TriggerReason.SUMMON``): ``interjection``
      is ``None``; ``detail`` carries the summon utterance text. The orchestrator
      builds an :class:`EngagementHandoff` (adding summary + excerpt).
    * **Path B — interjection** (``reason is TriggerReason.INTERJECTION``):
      ``interjection`` carries the :class:`Interjection` offer; ``detail`` is empty.

    Fields:
        reason: the :class:`TriggerReason` — which path fired.
        interjection: the :class:`Interjection` for a Path-B decision; ``None``
            for a Path-A summon.
        detail: free-form extra (the summon utterance text for Path A). Empty for
            Path B.
    """

    reason: TriggerReason
    interjection: Interjection | None = None
    detail: str = ""

    def handoff_reason(self) -> str:
        """The ``EngagementHandoff.trigger_reason`` wire string for this decision.

        ``"summon"`` for Path A; ``"wall:<category>"`` for Path B — the exact
        ``trigger_reason`` convention the module map froze for
        :class:`EngagementHandoff` (so the orchestrator doesn't re-derive it).
        """
        if self.reason is TriggerReason.SUMMON:
            return TriggerReason.SUMMON.value
        assert self.interjection is not None  # invariant: Path B carries an offer
        return f"wall:{self.interjection.category.value}"
