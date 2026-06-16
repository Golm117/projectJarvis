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

Implementation note (T-008): the manual clock now lives in the package as
``jarvis.clock.ManualClock`` so the runnable mock demo (``python -m jarvis``) can
use it without reaching into ``tests/``. ``SimulatedClock`` is kept here as the
test-harness name (qa-tuning's contract — every core-module test imports it from
``tests.clock``) and is an exact alias of that single implementation. The API is
unchanged: ``now`` / ``monotonic`` / ``advance`` / ``set`` / ``__call__``.
"""

from __future__ import annotations

from jarvis.clock import ManualClock

# The harness name for the package's ManualClock — one implementation, two names.
SimulatedClock = ManualClock

__all__ = ["SimulatedClock"]
