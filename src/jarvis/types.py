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

``Interjection`` and ``EngagementHandoff`` land with their tasks (T-007/T-008).
They are documented in the module map and added here as those tasks land so
each type freezes exactly when its first real consumer does.
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
