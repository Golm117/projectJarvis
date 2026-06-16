"""Model-backend seams + the Phase-0 heuristic stand-ins (T-008).

The two **in** seams that feed the ambient brain — summarization and wall
detection — are swappable so the same orchestrator runs on a cheap heuristic in
Phase 0 and on a local Qwen2.5/MLX model in Phase 2 with no core change (module
map §"Cross-cutting design constraints" #2). This module is the one place that
consolidates both seams + their Phase-0 mock backends, so the orchestrator and
the demo import them from a single home.

* ``SummarizerBackend`` / ``WallBackend`` — the Protocols. These are **re-exported**
  from the modules that froze them (``core.living_summary`` and
  ``core.wall_detector``) — this module does not redefine them, so there is a
  single source of truth for each signature. local-ml-engineer implements against
  these (T-202 / T-203).
* ``HeuristicSummarizerBackend`` — the Phase-0 ``SummarizerBackend``, parallel to
  ``HeuristicWallBackend`` (which already lives in ``core.wall_detector``). Ported
  from the prototype's ``Backend._mock_summarize``. Replaced by the real model in
  T-202 behind the same seam.

``HeuristicWallBackend`` itself is re-exported here too so the demo wires both
mock backends from one import, even though it physically lives next to the
``WallDetector`` it backs.
"""

from __future__ import annotations

from jarvis.core.living_summary import SummarizerBackend
from jarvis.core.text import keywords as _keywords
from jarvis.core.wall_detector import HeuristicWallBackend, WallBackend

__all__ = [
    "SummarizerBackend",
    "WallBackend",
    "HeuristicWallBackend",
    "HeuristicSummarizerBackend",
]


class HeuristicSummarizerBackend:
    """A cheap, deterministic summarizer — the Phase-0 ``SummarizerBackend``.

    The mock stand-in for the on-device Qwen2.5/MLX summarizer (T-202): no model,
    no network. It renders a one-line "Discussing <topics>. Latest: <last line>"
    summary from the window transcript, exactly as the prototype's
    ``Backend._mock_summarize`` did. It satisfies the ``SummarizerBackend``
    Protocol (``summarize(transcript, prev) -> str``), so ``LivingSummary`` drives
    it untouched and the real backend swaps in behind the same seam.

    Args:
        max_topics: how many content keywords to surface as the topic list.
        latest_chars: how much of the most recent line to echo.
    """

    def __init__(self, max_topics: int = 6, latest_chars: int = 80) -> None:
        self._max_topics = max_topics
        self._latest_chars = latest_chars

    def summarize(self, transcript: str, prev: str) -> str:  # noqa: ARG002
        lines = [line for line in transcript.splitlines() if line.strip()]
        topics = sorted(_keywords(transcript))[: self._max_topics]
        topic = ", ".join(topics) if topics else "general chat"
        last = lines[-1] if lines else ""
        return f"Discussing {topic}. Latest: {last[: self._latest_chars]}"
