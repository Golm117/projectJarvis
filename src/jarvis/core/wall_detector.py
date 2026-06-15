"""WallDetector — notices the conversation needs help (T-005).

A *wall* is a moment the conversation could use help: an unanswered question, an
expressed factual gap, a stuck/looping point, or a wish said into the air. The
WallDetector is the sensor that spots one and returns a structured
:class:`~jarvis.types.WallVerdict`. It does **not** decide whether to speak —
that gate (``confidence >= WALL_CONFIDENCE_TO_SPEAK``) is ``SummonController``
policy (T-007). The detector is a pure sensor; the speak/stay-silent decision
lives in one place downstream. This split is deliberate (module map
§"Cross-cutting design constraints" #3, and the asymmetric-summon decision):
the detector surfaces the confidence, the controller acts on it.

The detector itself is a thin pass-through over a **swappable** ``WallBackend``
seam (module map §"Cross-cutting design constraints" #2): it owns the contract
(``detect(transcript, summary) -> WallVerdict``) and the backend owns the
judgement. In Phase 0 the backend is :class:`HeuristicWallBackend`, a cheap
local heuristic ported from the prototype's ``Backend._mock_detect_wall``. In
Phase 2 (T-203) local-ml-engineer drops a Qwen2.5/MLX backend with structured
output in behind this same seam — the ``WallVerdict`` shape is frozen so that
swap touches neither this module nor ``SummonController``.

Pure logic, no I/O — the only outside call is to the injected backend.
"""

from __future__ import annotations

import re
from typing import Protocol

from jarvis.types import WallCategory, WallVerdict


class WallBackend(Protocol):
    """The swappable wall-detection seam (module map §"The I/O adapter seams").

    ``detect_wall`` takes the recent window ``transcript`` and the current
    living ``summary`` and returns a :class:`~jarvis.types.WallVerdict`. The
    heuristic mock (:class:`HeuristicWallBackend`) is the Phase-0 reference; the
    real model (Qwen2.5/MLX structured output, T-203) drops in behind this same
    signature without touching :class:`WallDetector`. The method name and
    arguments match ``tests/fakes.py::FakeWallBackend.detect_wall`` exactly, so
    the test fake satisfies this protocol directly.
    """

    def detect_wall(self, transcript: str, summary: str) -> WallVerdict: ...


class WallDetector:
    """Surfaces whether the conversation hit a wall, over an injected backend.

    Args:
        backend: the injected ``WallBackend`` — the judgement seam. The
            ``FakeWallBackend`` in tests; :class:`HeuristicWallBackend` as the
            Phase-0 default in production; Qwen2.5/MLX in Phase 2 (T-203).

    The detector is intentionally thin: it owns the public ``detect`` contract
    and delegates the verdict to the backend. Keeping it thin is what lets the
    mock → real swap be invisible to callers.
    """

    def __init__(self, backend: WallBackend) -> None:
        self._backend = backend

    def detect(self, transcript: str, summary: str) -> WallVerdict:
        """Return the backend's :class:`~jarvis.types.WallVerdict` for this context.

        The verdict carries ``is_wall``, ``category``, ``confidence`` and
        ``offer``. The *confidence* is surfaced raw — the decision to speak on it
        belongs to ``SummonController`` (T-007), not here.
        """
        return self._backend.detect_wall(transcript, summary)


# ---------------------------------------------------------------------------
# Heuristic mock backend (Phase 0) — the cheap stand-in for the real SLM.
# ---------------------------------------------------------------------------

# Per-category confidence the heuristic reports. These are the prototype's
# values (Backend._mock_detect_wall). They sit *above* the prototype's
# WALL_CONFIDENCE_TO_SPEAK (0.70) on purpose so a clear cue would clear the
# speak gate — but the gate itself lives in SummonController (T-007), not here.
_CONF_EXPLICIT_ASK = 0.78
_CONF_FACTUAL_GAP = 0.80
_CONF_STUCK_POINT = 0.74
_CONF_UNANSWERED_QUESTION = 0.72

# The single line Jarvis would offer, per category. Phase 0 placeholders; the
# real backend (T-203) composes these from context.
_OFFER_EXPLICIT_ASK = "Want me to look that up for you?"
_OFFER_FACTUAL_GAP = "I can find that — want me to?"
_OFFER_STUCK_POINT = "Want me to suggest a way forward?"
_OFFER_UNANSWERED_QUESTION = "I think I can answer that — shall I?"

# Cue patterns, checked in priority order (most specific intent first). An
# explicit wish and a stated factual gap are stronger signals than a bare
# question mark, so they win when more than one cue is present on the line.
_RE_EXPLICIT_ASK = re.compile(r"\b(i wish|if only|wish i (knew|had))\b")
_RE_FACTUAL_GAP = re.compile(
    r"\b(i (don'?t|do not) (know|remember)|what (was|were)|can'?t recall|no idea)\b"
)
_RE_STUCK_POINT = re.compile(
    r"\b(we'?re stuck|i'?m stuck|going in circles|going round in circles|"
    r"back to square one|spinning our wheels|not getting anywhere)\b"
)


class HeuristicWallBackend:
    """A cheap, deterministic wall heuristic — the Phase-0 ``WallBackend``.

    Looks only at the **last** non-empty line of the transcript (that's where a
    fresh wall surfaces) and matches cue patterns in priority order:

    1. ``explicit_ask`` — a wish said into the air ("I wish I knew…").
    2. ``factual_gap`` — a stated gap ("I don't remember", "what was…").
    3. ``stuck_point`` — the conversation is looping/stalled.
    4. ``unanswered_question`` — the line ends in "?".

    No cue ⇒ a ``none`` verdict (stay silent). Ported from the prototype's
    ``Backend._mock_detect_wall``, extended with the ``stuck_point`` cue so all
    four wall categories are reachable. Phase 2's real backend (T-203) replaces
    this with a model behind the same :class:`WallBackend` seam.
    """

    def detect_wall(self, transcript: str, summary: str) -> WallVerdict:  # noqa: ARG002
        lines = [line for line in transcript.splitlines() if line.strip()]
        if not lines:
            return WallVerdict.none()

        # The body of the most recent line, minus any "Speaker:" prefix.
        last = lines[-1]
        body = last.split(":", 1)[-1].strip().lower()

        if _RE_EXPLICIT_ASK.search(body):
            return WallVerdict(
                is_wall=True,
                category=WallCategory.EXPLICIT_ASK,
                confidence=_CONF_EXPLICIT_ASK,
                offer=_OFFER_EXPLICIT_ASK,
            )
        if _RE_FACTUAL_GAP.search(body):
            return WallVerdict(
                is_wall=True,
                category=WallCategory.FACTUAL_GAP,
                confidence=_CONF_FACTUAL_GAP,
                offer=_OFFER_FACTUAL_GAP,
            )
        if _RE_STUCK_POINT.search(body):
            return WallVerdict(
                is_wall=True,
                category=WallCategory.STUCK_POINT,
                confidence=_CONF_STUCK_POINT,
                offer=_OFFER_STUCK_POINT,
            )
        if body.endswith("?"):
            return WallVerdict(
                is_wall=True,
                category=WallCategory.UNANSWERED_QUESTION,
                confidence=_CONF_UNANSWERED_QUESTION,
                offer=_OFFER_UNANSWERED_QUESTION,
            )
        return WallVerdict.none()
