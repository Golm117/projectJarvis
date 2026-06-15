"""SummonController — the asymmetric dual-path engagement machine (T-007).

The heart of the MVP and the carrier of the project's success metric
(interjection precision). It decides *whether and why* Jarvis engages, turning
the wall signal (from :class:`~jarvis.core.wall_detector.WallDetector`) and the
timing signals (from :class:`~jarvis.core.turn_taking_gate.TurnTakingGate`) into
a :class:`~jarvis.types.SummonDecision`. Two initiation paths with **deliberately
opposite** timing profiles (DECISIONS.md 2026-06-15, "Asymmetric dual-summon"):

    LISTENING
      ├─ wake word ──────────────────────► ENGAGE(reason=summon)  # Path A: immediate, unconditional
      └─ wall ∧ confidence ≥ floor ──► PENDING ──┐
    PENDING_INTERJECTION                          │
      ├─ speech resumed ──────────────────────────┼──► (abort — yield the floor)
      ├─ politeness gap not yet elapsed ──────────┼──► (wait — no decision yet)
      ├─ same wall already offered (back-off) ────┼──► (stay silent — no nagging)
      └─ gap elapsed ∧ not resumed ∧ new offer ───┴──► OFFER(interjection)  # Path B

The asymmetry **is** the contract:

| | Path A — Summon | Path B — Interjection |
|---|---|---|
| Trigger | wake word | a detected wall |
| Endpoint gap | none — fires now | the gate's ~2 s **politeness gap** |
| Confidence bar | none (it was summoned) | ``>= INTERJECTION_CONFIDENCE_FLOOR`` (0.70) |
| If speech resumes | n/a | **abort** (hard-no: never talk over people) |
| De-dupe | n/a | **back-off** — same wall never offered twice in a row |

A false summon is harmless; a false interjection is the assistant talking over
people — so Path A always wins and never inherits Path B's caution, while Path B
hangs back behind every one of its conditions.

## The decision/handoff boundary (a deliberate structural call — DECISIONS.md)

``SummonController`` is a **pure decision machine**. It does **not** assemble the
full :class:`~jarvis.types.EngagementHandoff`: it holds neither the living
summary nor the rolling window (those belong to the orchestrator, T-008). It
returns a :class:`~jarvis.types.SummonDecision` — which path fired plus the
payload — and the orchestrator turns a *summon* decision into an
``EngagementHandoff`` (adding ``summary`` + ``recent_excerpt``) and a
*interjection* decision into a dispatched offer. This keeps the controller a
small, fully unit-testable state machine independent of the summary/window
plumbing, and keeps handoff assembly in the one place that owns that context.

## Timing comes only through the gate (no hidden clock)

The controller never reads a clock. It reads the gate's **pure predicates**
(``politeness_gap_elapsed()`` / ``speech_resumed()``); the gate owns the single
injected ``now`` (module map §"Cross-cutting design constraints" #1). A test
drives time by advancing the ``SimulatedClock`` the gate was built on, then asks
the controller to decide — no ``time.monotonic()`` anywhere in this module.

Pure logic, no I/O.
"""

from __future__ import annotations

from jarvis.core.turn_taking_gate import TurnTakingGate
from jarvis.types import Interjection, SummonDecision, TriggerReason, WallVerdict

# The confidence floor a wall must clear before Path B will offer help —
# precision over recall (.pdr.md §Success metric; PRD FR-4.3). 0.70 by default,
# matching the prototype's WALL_CONFIDENCE_TO_SPEAK. Constructor-injected so
# qa-tuning calibrates it in one place (Phase 5, T-503) — it is NOT magic, and it
# lives here (SummonController policy), not in the WallDetector (which surfaces
# confidence raw). The cut is ``>=`` (inclusive): a wall *at* the floor offers.
DEFAULT_INTERJECTION_CONFIDENCE_FLOOR = 0.70


class SummonController:
    """Decides whether/why Jarvis engages, over an injected gate (T-007).

    Args:
        gate: the :class:`~jarvis.core.turn_taking_gate.TurnTakingGate` — the
            source of the Path-B timing predicates (``politeness_gap_elapsed`` /
            ``speech_resumed``). Injected, so the controller carries no clock of
            its own. Tests pass a gate built on the ``SimulatedClock``.
        interjection_confidence_floor: the minimum wall confidence Path B requires
            (precision over recall). Must be in ``[0.0, 1.0]``. Defaults to
            ``DEFAULT_INTERJECTION_CONFIDENCE_FLOOR`` (0.70). The cut is inclusive.

    State carried between utterances is intentionally tiny: the back-off
    signature of the last wall actually offered, so the same offer never fires
    twice in a row. Everything else is a fresh read of the injected gate + the
    verdict handed in.
    """

    def __init__(
        self,
        gate: TurnTakingGate,
        interjection_confidence_floor: float = DEFAULT_INTERJECTION_CONFIDENCE_FLOOR,
    ) -> None:
        if not 0.0 <= interjection_confidence_floor <= 1.0:
            raise ValueError(
                "interjection_confidence_floor must be in [0.0, 1.0], got "
                f"{interjection_confidence_floor}"
            )
        self._gate = gate
        self._floor = float(interjection_confidence_floor)
        # Back-off (PRD FR-4.5): the signature of the last wall we actually
        # OFFERED, so we don't nag with the same offer twice in a row. Set only
        # when an interjection fires; not touched by aborts or sub-threshold walls.
        self._last_offered_signature: str | None = None

    @property
    def interjection_confidence_floor(self) -> float:
        """The injected Path-B confidence floor (read-only)."""
        return self._floor

    # -- Path A: summon -------------------------------------------------------

    def on_summon(self, detail: str = "") -> SummonDecision:
        """Engage immediately and unconditionally — the wake-word path.

        Path A **always wins**: it ignores the wall verdict, the gate, the
        confidence floor, and the back-off entirely. Being summoned is itself the
        permission to speak, so there is nothing to wait for and nothing to
        abort. Returns a ``SUMMON`` :class:`~jarvis.types.SummonDecision`; the
        orchestrator (T-008) turns it into an ``EngagementHandoff``.

        Args:
            detail: the summon utterance text, carried through on the decision so
                the orchestrator can include it in the handoff. Optional.
        """
        return SummonDecision(reason=TriggerReason.SUMMON, detail=detail)

    # -- Path B: interjection -------------------------------------------------

    def consider_interjection(self, verdict: WallVerdict) -> SummonDecision | None:
        """Decide whether a detected wall earns a proactive offer right now.

        Returns an ``INTERJECTION`` :class:`~jarvis.types.SummonDecision` **only**
        when every Path-B condition holds, else ``None`` (stay silent — wait,
        abort, or back off). The conditions, checked in order:

        1. ``verdict.is_wall`` — there is a wall at all.
        2. ``verdict.confidence >= interjection_confidence_floor`` — confident
           enough to risk speaking (precision over recall).
        3. ``not gate.speech_resumed()`` — speech did **not** resume after the
           gap opened. This is the **abort**: if someone started talking again
           while the interjection was pending, yield the floor (hard-no: never
           talk over resumed speech). Checked before the gap so a resume always
           suppresses, even if the (stale) gap technically reads elapsed.
        4. ``gate.politeness_gap_elapsed()`` — the long ~2 s opening actually
           arrived; we only interject into a clear silence.
        5. **Back-off** — this exact wall (category + offer) is not the one we
           offered last; the same offer never fires twice in a row (no nagging).

        Any failure ⇒ ``None`` and no state change, **except** a successful fire,
        which records the back-off signature. A sub-threshold or aborted wall
        does not arm back-off — only an offer that was actually made does.

        The controller reads the gate's predicates fresh on each call (they are
        pure reads), so it is the caller's cadence — typically the orchestrator
        polling per utterance / tick — that walks the pending interjection toward
        either an offer or an abort as the ``SimulatedClock`` advances.
        """
        if not verdict.is_wall:
            return None
        if verdict.confidence < self._floor:
            return None
        # Abort-on-resume takes precedence over the gap: a resume must suppress
        # the offer even if the gap latched elapsed before the resume landed.
        if self._gate.speech_resumed():
            return None
        if not self._gate.politeness_gap_elapsed():
            return None

        signature = self._signature(verdict)
        if signature == self._last_offered_signature:
            return None  # back-off: don't repeat the same offer

        self._last_offered_signature = signature
        return SummonDecision(
            reason=TriggerReason.INTERJECTION,
            interjection=Interjection(
                category=verdict.category,
                offer=verdict.offer,
                confidence=verdict.confidence,
            ),
        )

    # -- internals ------------------------------------------------------------

    @staticmethod
    def _signature(verdict: WallVerdict) -> str:
        """The back-off identity of a wall — its category + offer.

        Two verdicts with the same category *and* the same offer are 'the same
        offer' for de-dupe purposes (matching the prototype's
        ``f"{category}::{offer}"``). A different offer text, or a different
        category, is a new wall worth surfacing — confidence is deliberately not
        part of the signature (a re-detection of the same wall at a slightly
        different confidence is still the same offer).
        """
        return f"{verdict.category.value}::{verdict.offer}"
