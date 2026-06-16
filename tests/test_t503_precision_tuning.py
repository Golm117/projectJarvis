"""T-503 — interjection-precision tuning.

Pins the two orchestrator-policy behaviors the success-metric tune adds, plus the
eval outcome and the chosen threshold values. Model-free, mic-free, network-free,
deterministic on ``SimulatedClock`` — the eval/test posture.

Two behaviors under test (both in ``AttentionLayer``, both clock-driven):

1. **Post-engagement cooldown** — after Jarvis engages (a summon or a fired
   interjection), ambient Path-B interjections are suppressed for a short window.
   Kills the "What do you need?" FP (a turn addressed AT Jarvis inside a
   just-engaged exchange). Pins: a wall within the window is suppressed; the same
   wall after the window fires.
2. **Pending-wall TTL** — a wall cached by ``tick()`` during silence is dropped
   once it has waited longer than the TTL, so a stale wall can't fire late as a
   false interjection. Pins: a fresh wall (opening within the TTL) still fires; a
   stale wall (opening past the TTL) is dropped.

Plus: the eval precision rises 0.60 → 0.75 on the seeded corpus, and the chosen
default constants are what the module ships.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from jarvis.attention_layer import (
    DEFAULT_PENDING_WALL_TTL_SECONDS,
    DEFAULT_POST_ENGAGEMENT_COOLDOWN_SECONDS,
    AttentionLayer,
)
from jarvis.core.turn_taking_gate import TurnTakingGate
from jarvis.eval.fixture import load_fixture
from jarvis.eval.runner import run_fixtures
from jarvis.eval.seed import seed_fixtures
from jarvis.types import EngagementHandoff, Interjection, Utterance, WallVerdict
from tests.clock import SimulatedClock
from tests.fakes import FakeResponder, FakeVoice, FakeWallBackend, wall

# ---------------------------------------------------------------------------
# Helpers (mirroring test_tick_continuous_path_b.py)
# ---------------------------------------------------------------------------


def _utt(text: str, ts: float = 0.0, speaker: str = "A") -> Utterance:
    return Utterance(speaker=speaker, text=text, ts=ts)


def _layer(
    clock: SimulatedClock,
    gate: TurnTakingGate,
    *,
    wall_verdict: WallVerdict | None = None,
    post_engagement_cooldown_seconds: float = DEFAULT_POST_ENGAGEMENT_COOLDOWN_SECONDS,
    pending_wall_ttl_seconds: float = DEFAULT_PENDING_WALL_TTL_SECONDS,
    on_interjection: Callable[[Interjection], None] | None = None,
    on_engagement: Callable[[EngagementHandoff], None] | None = None,
) -> AttentionLayer:
    backend_verdict = wall_verdict or wall("factual_gap", 0.9, offer="I can find that.")
    return AttentionLayer.build(
        gate=gate,
        now=clock.now,
        responder=FakeResponder(),
        voice=FakeVoice(),
        wall_backend=FakeWallBackend(verdict=backend_verdict),
        post_engagement_cooldown_seconds=post_engagement_cooldown_seconds,
        pending_wall_ttl_seconds=pending_wall_ttl_seconds,
        on_interjection=on_interjection,
        on_engagement=on_engagement,
    )


def _open_gap(gate: TurnTakingGate) -> None:
    gate.on_speech_start()
    gate.on_speech_end()


# ---------------------------------------------------------------------------
# 1. Post-engagement cooldown — suppresses within the window
# ---------------------------------------------------------------------------


def test_cooldown_suppresses_path_b_within_window() -> None:
    """A wall that would fire is suppressed when it lands inside the cooldown after
    an engagement — the 'What do you need?' FP fix."""
    clock = SimulatedClock()
    gate = TurnTakingGate(clock.now, politeness_gap_seconds=2.0)
    interjections: list[Interjection] = []
    layer = _layer(
        clock, gate, post_engagement_cooldown_seconds=6.0, on_interjection=interjections.append
    )

    # Jarvis engages first (a summon).
    layer.ingest(_utt("Jarvis, what's the weather?", ts=clock.now()))
    assert interjections == []  # a summon is not a Path-B interjection

    # A wall surfaces shortly after, with a clean opening — but still inside the
    # 6 s cooldown. It must NOT fire.
    clock.advance(2.0)
    _open_gap(gate)
    layer.ingest(_utt("What do you need?", ts=clock.now()))
    clock.advance(2.5)  # open the politeness gap
    layer.tick()
    assert interjections == [], "Path-B must be suppressed inside the cooldown"
    assert layer._pending_wall is None  # noqa: SLF001  (suppressed wall is not cached)


def test_cooldown_allows_path_b_after_window() -> None:
    """The same wall fires once the cooldown has elapsed since the engagement."""
    clock = SimulatedClock()
    gate = TurnTakingGate(clock.now, politeness_gap_seconds=2.0)
    interjections: list[Interjection] = []
    layer = _layer(
        clock, gate, post_engagement_cooldown_seconds=6.0, on_interjection=interjections.append
    )

    layer.ingest(_utt("Jarvis, what's the weather?", ts=clock.now()))  # engage at t=0

    # Move well past the cooldown, then a wall + clean opening → it fires.
    clock.advance(7.0)  # > 6 s cooldown
    _open_gap(gate)
    layer.ingest(_utt("What was the conference date?", ts=clock.now()))
    clock.advance(2.5)
    layer.tick()
    assert len(interjections) == 1, "Path-B should fire once the cooldown has passed"


def test_cooldown_zero_disables_suppression() -> None:
    """Cooldown 0.0 → no suppression (a wall right after an engagement fires)."""
    clock = SimulatedClock()
    gate = TurnTakingGate(clock.now, politeness_gap_seconds=2.0)
    interjections: list[Interjection] = []
    layer = _layer(
        clock, gate, post_engagement_cooldown_seconds=0.0, on_interjection=interjections.append
    )

    layer.ingest(_utt("Jarvis, hello.", ts=clock.now()))
    clock.advance(1.0)
    _open_gap(gate)
    layer.ingest(_utt("What was that number?", ts=clock.now()))
    clock.advance(2.5)
    layer.tick()
    assert len(interjections) == 1


def test_a_fired_interjection_also_arms_the_cooldown() -> None:
    """A Path-B fire is itself an engagement, so it arms the cooldown: a second
    wall right after the first interjection is suppressed."""
    clock = SimulatedClock()
    gate = TurnTakingGate(clock.now, politeness_gap_seconds=2.0)
    interjections: list[Interjection] = []
    layer = _layer(
        clock, gate, post_engagement_cooldown_seconds=6.0, on_interjection=interjections.append
    )

    # First wall fires (no prior engagement).
    _open_gap(gate)
    layer.ingest(_utt("What was the conference date?", ts=clock.now()))
    clock.advance(2.5)
    layer.tick()
    assert len(interjections) == 1  # the fire arms the cooldown

    # A second wall right after → suppressed by the cooldown the fire armed.
    clock.advance(1.0)
    _open_gap(gate)
    layer.ingest(_utt("And what was the vendor name?", ts=clock.now()))
    clock.advance(2.5)
    layer.tick()
    assert len(interjections) == 1, "Second wall must be suppressed inside the cooldown"


# ---------------------------------------------------------------------------
# 2. Pending-wall TTL — drops a stale wall, keeps a fresh one
# ---------------------------------------------------------------------------


def test_ttl_drops_stale_pending_wall() -> None:
    """A cached wall whose opening arrives past the TTL is dropped, never fired."""
    clock = SimulatedClock()
    gate = TurnTakingGate(clock.now, politeness_gap_seconds=2.0)
    interjections: list[Interjection] = []
    layer = _layer(clock, gate, pending_wall_ttl_seconds=12.0, on_interjection=interjections.append)

    # Wall cached at t=0 (gap not open yet).
    _open_gap(gate)
    layer.ingest(_utt("What was the vendor name?", ts=clock.now()))
    assert layer._pending_wall is not None  # noqa: SLF001

    # No opening arrives until past the TTL.
    clock.advance(13.0)  # > 12 s TTL
    layer.tick()
    assert interjections == [], "A stale wall (past TTL) must not fire"
    assert layer._pending_wall is None  # noqa: SLF001  (dropped by the TTL)


def test_ttl_keeps_a_fresh_wall_that_fires_in_time() -> None:
    """A wall whose opening arrives within the TTL still fires — the TTL never
    drops a legitimate wall."""
    clock = SimulatedClock()
    gate = TurnTakingGate(clock.now, politeness_gap_seconds=2.0)
    interjections: list[Interjection] = []
    layer = _layer(clock, gate, pending_wall_ttl_seconds=12.0, on_interjection=interjections.append)

    _open_gap(gate)
    layer.ingest(_utt("What was the conference date?", ts=clock.now()))
    clock.advance(2.5)  # opening well within the 12 s TTL
    layer.tick()
    assert len(interjections) == 1, "A fresh wall within the TTL must still fire"


def test_ttl_zero_disables_staleness_drop() -> None:
    """TTL 0.0 → no staleness drop (the late wall fires)."""
    clock = SimulatedClock()
    gate = TurnTakingGate(clock.now, politeness_gap_seconds=2.0)
    interjections: list[Interjection] = []
    layer = _layer(clock, gate, pending_wall_ttl_seconds=0.0, on_interjection=interjections.append)

    _open_gap(gate)
    layer.ingest(_utt("What was the vendor name?", ts=clock.now()))
    clock.advance(13.0)
    layer.tick()
    assert len(interjections) == 1, "With TTL disabled the late wall fires"


# ---------------------------------------------------------------------------
# 3. The eval outcome + the shipped threshold values
# ---------------------------------------------------------------------------


def test_seeded_precision_improves_to_0_75() -> None:
    """The success metric: the seeded corpus scores 0.75 (up from the 0.60
    pre-T-503 baseline) — the WDYN + stale FPs removed, useful fires preserved."""
    result = run_fixtures(seed_fixtures())
    assert result.precision == 0.75
    assert result.total_fires == 4
    assert result.useful_fires == 3
    assert result.false_fires == 1  # the wrong-category fire (not a tunable FP)


def test_committed_fixtures_score_0_75() -> None:
    """The on-disk corpus (what the orchestrator regenerates) scores 0.75 too."""
    from pathlib import Path

    paths = sorted(Path("docs/qa/fixtures").glob("*.json"))
    result = run_fixtures([load_fixture(p) for p in paths])
    assert result.precision == 0.75


def test_pre_t503_baseline_was_below_target() -> None:
    """Disabling both T-503 rules reproduces the pre-tune behavior — the FPs fire
    again and precision drops below the 0.75 the tune achieves."""
    fxs = [
        replace(
            fx,
            config=replace(
                fx.config,
                post_engagement_cooldown_seconds=0.0,
                pending_wall_ttl_seconds=0.0,
            ),
        )
        for fx in seed_fixtures()
    ]
    result = run_fixtures(fxs)
    assert result.precision < 0.75  # both FPs present again


def test_shipped_threshold_constants() -> None:
    """The chosen, eval-calibrated default values are what the module ships.

    Cooldown 6.0 s: clears the 5.5 s mark the seeded FP fires at (a 0.5 s margin),
    with no legitimate fire affected — the human-chosen value (sign-off 2026-06-16),
    most responsive setting that works; 6 and 8 s both score 0.75, 6.0 chosen for
    responsiveness. TTL 12.0 s: above the ~2 s politeness gap a real wall fires
    within, below the 15 s stale opening — only catches a genuinely stale wall.
    Unchanged thresholds: gap 2.0 / floor 0.70 / settle 0.6 (the sweep showed no
    precision-improving change — see the T-503 review brief)."""
    assert DEFAULT_POST_ENGAGEMENT_COOLDOWN_SECONDS == 6.0
    assert DEFAULT_PENDING_WALL_TTL_SECONDS == 12.0


def test_cooldown_and_ttl_reject_negative_values() -> None:
    """The two knobs are guarded >= 0 at construction."""
    import pytest

    clock = SimulatedClock()
    gate = TurnTakingGate(clock.now)
    with pytest.raises(ValueError, match="post_engagement_cooldown_seconds"):
        _layer(clock, gate, post_engagement_cooldown_seconds=-1.0)
    with pytest.raises(ValueError, match="pending_wall_ttl_seconds"):
        _layer(clock, gate, pending_wall_ttl_seconds=-1.0)
