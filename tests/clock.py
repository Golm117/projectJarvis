"""Simulated clock for the core-module tests (T-009).

The core modules take their time source via injection — no module in
``src/jarvis/core/`` is allowed to call ``time.monotonic()`` directly
(see ``docs/architecture/module-map.md`` §"Cross-cutting design constraints").
That constraint exists *so this clock can drive them*: a test advances time
explicitly and the ``RollingWindow`` evicts, and the ``TurnTakingGate`` flips
``settled`` / ``politeness_gap_elapsed`` / ``speech_resumed`` — all without a
real ``sleep`` and all deterministically.

How a module receives the clock:

* As a zero-arg callable returning monotonic seconds — pass ``clock.now``
  (a ``Callable[[], float]``) wherever the contract asks for ``now``.
* As an object — pass the ``SimulatedClock`` itself; it exposes ``.now()``.

Both forms read the *same* mutable time value, so a test can hold the clock,
inject ``clock.now`` into a module, and then call ``clock.advance(2.0)`` to
move the module's notion of time forward.

This utility is fully standalone — it imports nothing from ``jarvis`` — so it
is usable before any core module exists.
"""

from __future__ import annotations


class SimulatedClock:
    """A monotonic clock whose time only moves when a test moves it.

    Monotonic by construction: ``advance`` rejects negative deltas and
    ``set`` rejects moving backwards, matching the guarantees real
    ``time.monotonic()`` makes (the core modules assume time never regresses).
    """

    def __init__(self, start: float = 0.0) -> None:
        self._t = float(start)

    def now(self) -> float:
        """Current simulated time in seconds. Inject this as the ``now`` callable."""
        return self._t

    # ``time.monotonic``-compatible alias so the clock can be dropped in wherever
    # a ``monotonic()``-shaped callable is expected.
    def monotonic(self) -> float:
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
        return f"SimulatedClock(t={self._t!r})"
