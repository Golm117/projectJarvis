"""TopicShiftDetector — the delta-update trigger (T-003).

The "redraw only the changed pixels" gate. The expensive `LivingSummary`
refresh (T-004) should fire only when the conversation has actually moved on,
not on every utterance. This module is the pure decision that says whether it
moved on: it compares the *current* window content against the content the
standing summary was built on, and returns a single boolean.

Pure by construction — it is a function of its two keyword-set arguments and the
configured threshold, with no hidden state. The caller (LivingSummary) owns
*what* the two sets are (`window.keywords()` vs. the summary's basis) and *what*
to do on a shift; this module owns only the metric and the threshold, so that
metric can change (a different similarity, an embedding distance later) without
touching any caller.

The similarity is Jaccard over keyword sets (shared `jarvis.core.text.jaccard`),
matching the prototype. The threshold is the prototype's `TOPIC_SHIFT_THRESHOLD`,
exposed as a constructor argument so it is tunable in one place.
"""

from __future__ import annotations

from jarvis.core.text import jaccard

# Jaccard similarity strictly below this counts as a topic shift. Ported from the
# prototype's TOPIC_SHIFT_THRESHOLD. Lower = more tolerant of drift before
# re-summarizing (fewer refreshes); higher = more eager to refresh.
DEFAULT_TOPIC_SHIFT_THRESHOLD = 0.30


class TopicShiftDetector:
    """Decides whether the current content has drifted from the summary's basis.

    Args:
        threshold: the Jaccard-similarity floor. ``shifted`` returns ``True`` when
            similarity is **strictly below** this value. Must be in ``[0.0, 1.0]``.
            Defaults to ``DEFAULT_TOPIC_SHIFT_THRESHOLD`` (0.30).
    """

    def __init__(self, threshold: float = DEFAULT_TOPIC_SHIFT_THRESHOLD) -> None:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"threshold must be in [0.0, 1.0], got {threshold}")
        self._threshold = threshold

    @property
    def threshold(self) -> float:
        """The configured similarity floor (read-only)."""
        return self._threshold

    def similarity(self, current_keywords: set[str], basis_keywords: set[str]) -> float:
        """Jaccard similarity of the current content vs. the summary's basis.

        Exposed alongside ``shifted`` so a caller (or a test) can inspect *how
        far* the content drifted, not just whether it crossed the threshold.
        """
        return jaccard(current_keywords, basis_keywords)

    def shifted(self, current_keywords: set[str], basis_keywords: set[str]) -> bool:
        """``True`` iff the topic shifted — similarity strictly below threshold.

        Encapsulates the metric + threshold behind a boolean so callers gate on
        "did the topic shift?" without knowing it is Jaccard underneath (module
        map §`TopicShiftDetector`).
        """
        return self.similarity(current_keywords, basis_keywords) < self._threshold
