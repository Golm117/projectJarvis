"""Shared pytest fixtures for the core-module suite (T-009).

These expose the simulated clock and the seam fakes so the core-module test
tasks (T-002…T-008) build on one harness instead of each reinventing a clock
and a set of fakes. Import the underlying classes directly from ``tests.clock``
/ ``tests.fakes`` when a test needs to preset return values or scripts at
construction time; use these fixtures for the common, default-configured case.

See ``docs/qa/eval-plan.md`` §"Test-harness conventions" for usage and the
seam → fake map.
"""

from __future__ import annotations

import pytest

from tests.clock import SimulatedClock
from tests.fakes import (
    FakeResponder,
    FakeSummarizer,
    FakeVoice,
    FakeWallBackend,
)


@pytest.fixture
def clock() -> SimulatedClock:
    """A simulated clock starting at t=0. Inject ``clock.now`` (or ``clock``)
    wherever a module asks for its time source, then drive transitions with
    ``clock.advance(seconds)``."""
    return SimulatedClock()


@pytest.fixture
def fake_summarizer() -> FakeSummarizer:
    """Default FakeSummarizer (deterministic ``summary#N`` echo). For a fixed
    string or a per-call script, construct ``FakeSummarizer(...)`` directly."""
    return FakeSummarizer()


@pytest.fixture
def fake_wall_backend() -> FakeWallBackend:
    """Default FakeWallBackend (returns a 'none' verdict). For scripted verdicts,
    construct ``FakeWallBackend(verdicts=[...])`` with the ``wall``/``no_wall``
    helpers."""
    return FakeWallBackend()


@pytest.fixture
def fake_responder() -> FakeResponder:
    """Default engaged-path FakeResponder (canned line, records handoffs)."""
    return FakeResponder()


@pytest.fixture
def fake_voice() -> FakeVoice:
    """Default FakeVoice (no-op that records spoken lines)."""
    return FakeVoice()
