"""RollingWindow — the bounded sliding transcript (T-002).

The first stage of the ambient pipeline: every ``Utterance`` that isn't a summon
lands here. The window is bounded **two ways at once** so the transcript the rest
of the pipeline reads is always the *live recent* conversation, never an
unbounded log:

* **By count** — at most ``max_utterances`` entries (the most recent win).
* **By elapsed time** — nothing older than ``max_seconds`` relative to *now*.

"Now" comes from an **injected** ``now: Callable[[], float]`` — the single
clock-injection convention (module map §"Cross-cutting design constraints" #1).
This is the deliberate divergence from the prototype, which called
``time.monotonic()`` internally and evicted relative to the newest utterance's
timestamp. Injecting the clock lets qa-tuning's ``SimulatedClock`` drive
eviction-by-time deterministically, and evicting relative to *now* (not the last
utterance) means a window queried after a long silence correctly reflects that
the old context has aged out — even with no new utterance to trigger it.

Pure logic, no I/O.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

from jarvis.core.text import keywords as _keywords
from jarvis.types import Utterance


class RollingWindow:
    """A sliding buffer of recent utterances, bounded by count and elapsed time.

    Args:
        max_utterances: the count bound — at most this many utterances are kept
            (the oldest are dropped first). Must be >= 1.
        max_seconds: the time bound — utterances older than this many seconds
            (relative to ``now()``) are evicted. Must be >= 0.
        now: the injected time source — a zero-arg callable returning monotonic
            seconds. Defaults to ``0.0`` only as a degenerate "no time bound in
            effect" source; real callers always inject one (the clock in
            production, ``SimulatedClock.now`` in tests).
    """

    def __init__(
        self,
        max_utterances: int,
        max_seconds: float,
        now: Callable[[], float] = lambda: 0.0,
    ) -> None:
        if max_utterances < 1:
            raise ValueError(f"max_utterances must be >= 1, got {max_utterances}")
        if max_seconds < 0:
            raise ValueError(f"max_seconds must be >= 0, got {max_seconds}")
        self._max_utterances = max_utterances
        self._max_seconds = float(max_seconds)
        self._now = now
        # The count bound is enforced for free by deque(maxlen): appending the
        # (max+1)th item silently drops the oldest. The time bound is enforced by
        # _evict_stale on every add.
        self._buf: deque[Utterance] = deque(maxlen=max_utterances)

    def add(self, u: Utterance) -> None:
        """Append an utterance, then evict anything that fell outside either bound.

        The count bound is applied by the deque on append; the time bound is
        applied here against the current ``now()``.
        """
        self._buf.append(u)
        self._evict_stale()

    def _evict_stale(self) -> None:
        """Drop utterances older than ``max_seconds`` relative to ``now()``."""
        cutoff = self._now() - self._max_seconds
        while self._buf and self._buf[0].ts < cutoff:
            self._buf.popleft()

    def utterances(self) -> list[Utterance]:
        """The live window, oldest-first.

        Re-evicts against the current ``now()`` first, so a read after a long
        silence reflects time that passed without any ``add`` — the window ages
        even when nothing new is said.
        """
        self._evict_stale()
        return list(self._buf)

    def transcript(self) -> str:
        """The window rendered as ``"Speaker: text"`` lines, oldest-first."""
        return "\n".join(f"{u.speaker}: {u.text}" for u in self.utterances())

    def keywords(self) -> set[str]:
        """Union of the content keywords across the live window.

        This is what ``TopicShiftDetector`` (T-003) compares against the
        summary's basis to decide whether a refresh is worth it.
        """
        ks: set[str] = set()
        for u in self.utterances():
            ks |= _keywords(u.text)
        return ks
