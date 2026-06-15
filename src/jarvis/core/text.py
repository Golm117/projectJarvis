"""Shared text helpers for the ambient core.

Two pure functions used by more than one core module — the keyword extraction
that ``RollingWindow.keywords`` (T-002) and ``TopicShiftDetector`` (T-003) both
read, and the Jaccard similarity the shift detector compares with. Ported from
``prototypes/attention-layer/attention_layer.py`` (the ``keywords``/``jaccard``
helpers) so the real package shares one definition instead of duplicating it.

Pure, deterministic, no I/O — safe to call anywhere in the core.
"""

from __future__ import annotations

import re

# Common words carry no topic signal; dropping them keeps the keyword set (and
# therefore the topic-shift comparison) focused on content words.
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "if",
        "then",
        "so",
        "to",
        "of",
        "in",
        "on",
        "for",
        "with",
        "at",
        "by",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "i",
        "you",
        "we",
        "they",
        "he",
        "she",
        "do",
        "does",
        "did",
        "have",
        "has",
        "had",
        "will",
        "would",
        "can",
        "could",
        "should",
        "what",
        "how",
        "why",
        "when",
        "about",
        "just",
        "like",
        "really",
        "thing",
        "going",
        "get",
        "got",
    }
)

_WORD_RE = re.compile(r"[a-z']+")


def keywords(text: str) -> set[str]:
    """Content words of ``text``: lowercased tokens >2 chars, minus stopwords."""
    return {w for w in _WORD_RE.findall(text.lower()) if len(w) > 2 and w not in _STOPWORDS}


def jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity of two keyword sets, in ``[0.0, 1.0]``.

    Two empty sets are identical (``1.0``); one empty and one non-empty share
    nothing (``0.0``). Otherwise ``|a ∩ b| / |a ∪ b|``.
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
