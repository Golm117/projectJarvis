"""jarvis.eval — the interjection-precision eval + capture/label tooling (T-502).

This package turns the interjection-precision eval spec
(``docs/qa/eval-plan.md`` §"Interjection-precision eval spec (T-010)") into
runnable code:

* :mod:`jarvis.eval.fixture` — the fixture **schema** (the labeled-conversation
  dataclasses) + JSON (de)serialization. The one shape capture writes and the
  runner reads.
* :mod:`jarvis.eval.capture` — the **capture** mechanism: a recorder that
  observes a live ``run_live`` session (via the existing event callbacks + the
  gate edges + a verdict-observing wrap of the wall backend) and emits a fixture
  with placeholder ground-truth fields for a labeler to fill. Opt-in, ephemeral,
  local-only (PRD privacy contract).
* :mod:`jarvis.eval.runner` — the deterministic **precision** computation:
  replays a labeled fixture through ``(TurnTakingGate, SummonController)`` on the
  ``SimulatedClock`` + a ``FakeWallBackend`` scripted from the labels, collects
  every Path-B fire, matches it to a candidate, and reports
  ``precision = useful ÷ total Path-B fires``.

Nothing here loads a model, opens a mic, or touches the network — the runner is
as offline as the unit tests, and the capture recorder only *observes* a live
run (the live run itself is what carries the audio/model, behind the same lazy
imports ``run_live`` already uses).
"""

from __future__ import annotations
