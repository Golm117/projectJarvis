"""ManualClock — a deterministic, manually-advanced monotonic clock (T-008).

The core modules take their time source via injection — no module in
``src/jarvis/`` calls ``time.monotonic()`` directly (module map §"Cross-cutting
design constraints" #1). That injected ``now`` is usually a real
``time.monotonic`` in production, but the **mock pipeline** (the runnable demo and
the acceptance tests) needs a clock a caller can drive explicitly so the
politeness gap elapses without a real ``sleep`` and the run is deterministic.

``ManualClock`` is that clock, living in the package so the runnable demo
(``python -m jarvis``) does not have to reach into ``tests/``. qa-tuning's test
harness (``tests/clock.py``) re-exports it as ``SimulatedClock`` so there is a
single implementation behind both names.

Monotonic by construction: ``advance`` rejects negative deltas and ``set`` rejects
moving backwards, matching the guarantees real ``time.monotonic()`` makes (the
core modules assume time never regresses). Inject it either as the zero-arg
callable ``clock.now`` or — since the instance is itself callable — as ``clock``.
"""

from __future__ import annotations


class ManualClock:
    """A monotonic clock whose time only moves when a caller moves it."""

    def __init__(self, start: float = 0.0) -> None:
        self._t = float(start)

    def now(self) -> float:
        """Current time in seconds. Inject this as the ``now`` callable."""
        return self._t

    def monotonic(self) -> float:
        """``time.monotonic``-compatible alias."""
        return self._t

    def advance(self, seconds: float) -> float:
        """Move time forward by ``seconds`` (must be >= 0). Returns the new time."""
        if seconds < 0:
            raise ValueError(f"clock cannot advance by a negative delta: {seconds}")
        self._t += float(seconds)
        return self._t

    def set(self, t: float) -> float:
        """Jump to an absolute time ``t`` (must be >= current). Returns the new time."""
        t = float(t)
        if t < self._t:
            raise ValueError(f"clock cannot move backwards: {t} < {self._t}")
        self._t = t
        return self._t

    def __call__(self) -> float:
        """Allow the clock instance itself to be used as the ``now`` callable."""
        return self._t

    def __repr__(self) -> str:
        return f"ManualClock(t={self._t!r})"
