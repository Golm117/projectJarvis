"""The interjection-precision eval runner (T-502).

Implements the precision computation from ``docs/qa/eval-plan.md``
§"Precision computation — matching fires to labels" and §"How it runs against
the modules". It is deterministic and fully offline — **no audio, no model, no
network** — the same posture as the unit tests:

* one ``SimulatedClock`` per fixture, advanced to each timeline ``t`` (no real
  ``sleep``);
* the real ``TurnTakingGate`` driven by the fixture's speech_start/speech_end
  moments (so ``politeness_gap_elapsed`` / ``speech_resumed`` reflect the real
  pacing);
* the real ``SummonController`` — the unit under measurement — fed verdicts the
  fixture *labels* describe (built into a ``WallVerdict`` at each candidate's
  moments), so a model is never run.

The runner walks the timeline, and at the moments inside each candidate's match
window it asks ``consider_interjection`` whether to fire (re-evaluating, like the
live ticker, as the gap opens). Every ``INTERJECTION`` decision is a **Path-B
fire**; the runner matches each fire to a candidate by time window and scores it,
then aggregates ``precision = useful ÷ total fires``.

Why drive the *real* gate + controller (not a re-implementation of the rule):
precision is exactly "what would the shipped decision machine do on this labeled
conversation under this config" — so the metric must run the real modules. T-503
sweeps the config and re-scores; because the thresholds are constructor-injected,
the sweep changes only the injected ``Config`` (eval-plan §"Calibration hook").
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jarvis.clock import ManualClock
from jarvis.core.summon_controller import SummonController
from jarvis.core.turn_taking_gate import TurnTakingGate
from jarvis.eval.fixture import Candidate, Fixture, Label, MomentKind
from jarvis.types import SummonDecision, TriggerReason, WallCategory, WallVerdict

# How finely the runner re-evaluates Path B while inside a candidate's match
# window. The live system ticks ~every 200 ms (live.py TICK_INTERVAL_SECONDS);
# the runner mirrors that so a fire lands at a realistic simulated time, which is
# what gets matched against the candidate window. Purely a replay cadence — it
# changes no threshold and is not a tunable knob.
EVAL_TICK_SECONDS = 0.20


@dataclass(frozen=True)
class Fire:
    """A recorded Path-B fire: the offer + the simulated clock time it fired at."""

    t_fire: float
    category: WallCategory
    offer: str
    confidence: float


@dataclass(frozen=True)
class ScoredFire:
    """A fire after matching + scoring against the fixture's candidates."""

    fire: Fire
    matched_candidate_id: str | None  # None == unmatched (fired where no candidate exists)
    useful: bool  # True == counts toward the numerator


@dataclass
class FixtureResult:
    """The per-fixture outcome of a run."""

    fixture_id: str
    scored_fires: list[ScoredFire] = field(default_factory=list)
    # Candidates labeled USEFUL that the controller stayed silent on — a *miss*
    # (a recall datum, recorded for visibility, never in the precision ratio).
    missed_useful_candidate_ids: list[str] = field(default_factory=list)

    @property
    def total_fires(self) -> int:
        return len(self.scored_fires)

    @property
    def useful_fires(self) -> int:
        return sum(1 for s in self.scored_fires if s.useful)

    @property
    def false_fires(self) -> int:
        return self.total_fires - self.useful_fires


@dataclass
class EvalResult:
    """The aggregate result over one or more fixtures (eval-plan §"Aggregate")."""

    per_fixture: list[FixtureResult] = field(default_factory=list)

    @property
    def total_fires(self) -> int:
        return sum(r.total_fires for r in self.per_fixture)

    @property
    def useful_fires(self) -> int:
        return sum(r.useful_fires for r in self.per_fixture)

    @property
    def false_fires(self) -> int:
        return self.total_fires - self.useful_fires

    @property
    def precision(self) -> float | None:
        """``useful ÷ total`` Path-B fires, or ``None`` if no fire occurred.

        Per eval-plan: a run that never interjects has no precision to speak of
        (that is a recall concern, out of scope for this metric) — so it is
        reported as ``None`` (undefined), not ``0``.
        """
        if self.total_fires == 0:
            return None
        return self.useful_fires / self.total_fires

    def precision_by_category(self) -> dict[WallCategory, tuple[int, int]]:
        """Per-category ``(useful, total)`` fire counts — which walls over-fire.

        eval-plan §"Aggregate": a per-category breakdown is reported so
        calibration can see *which* wall types over-fire.
        """
        out: dict[WallCategory, tuple[int, int]] = {}
        for r in self.per_fixture:
            for s in r.scored_fires:
                useful, total = out.get(s.fire.category, (0, 0))
                out[s.fire.category] = (useful + (1 if s.useful else 0), total + 1)
        return out


def _candidate_verdict(c: Candidate) -> WallVerdict:
    """Build the ``WallVerdict`` the detector *would* surface at candidate ``c``.

    eval-plan §"How it runs": in Phase 0/5 the wall at each moment comes from the
    fixture's labeled ``wall``/``category`` (and the captured/authored confidence
    + offer), **not** from running a model. A non-wall candidate yields the
    canonical ``none()`` verdict (the controller will drop it on ``is_wall``).
    """
    if not c.wall or c.category in (None, WallCategory.NONE.value):
        return WallVerdict.none()
    confidence = 1.0 if c.observed_confidence is None else c.observed_confidence
    offer = c.observed_offer or f"Want me to help with that? ({c.category})"
    # The category the detector ACTUALLY surfaced: observed_category if the
    # detector mis-named it (a wrong-category fire), else the ground-truth
    # category. Scoring compares this fired category to the ground-truth one.
    fired_category = c.observed_category if c.observed_category is not None else c.category
    return WallVerdict(
        is_wall=True,
        category=WallCategory(fired_category),
        confidence=confidence,
        offer=offer,
    )


def run_fixture(fx: Fixture) -> FixtureResult:
    """Replay one labeled fixture and score its Path-B fires.

    Raises ``ValueError`` if the fixture still has ``UNLABELED`` candidates — an
    un-reviewed capture must not be scored (it would produce a meaningless
    number). Hand the capture through the labeling workflow first.
    """
    fx.validate()
    if not fx.is_fully_labeled():
        unlabeled = [c.candidate_id for c in fx.candidates if c.label is Label.UNLABELED]
        raise ValueError(
            f"fixture {fx.fixture_id} has unlabeled candidates {unlabeled}; "
            "label them before scoring (jarvis.eval.label)"
        )

    clock = ManualClock()
    gate = TurnTakingGate(
        clock.now,
        settle_seconds=fx.config.settle_seconds,
        politeness_gap_seconds=fx.config.politeness_gap_seconds,
    )
    controller = SummonController(
        gate, interjection_confidence_floor=fx.config.interjection_confidence_floor
    )

    # Pre-build each candidate's verdict once (the SAME object is re-evaluated as
    # the gap opens, mirroring the live ticker's cached-verdict design, so the
    # back-off signature is stable across re-evaluations — eval-plan / NOTES T-302).
    verdicts = {c.candidate_id: _candidate_verdict(c) for c in fx.candidates}
    # Candidates sorted by their match window, so we know which one (if any) is
    # "open" at a given simulated time.
    candidates = sorted(fx.candidates, key=lambda c: c.match_from)

    fires: list[Fire] = []
    fired_candidate_ids: set[str] = set()

    def _try_fire_open_candidates() -> None:
        """At the current clock time, let any open candidate fire (once)."""
        now = clock.now()
        for c in candidates:
            if c.candidate_id in fired_candidate_ids:
                continue
            if not (c.match_from <= now <= c.match_to):
                continue
            decision = controller.consider_interjection(verdicts[c.candidate_id])
            if decision is not None and decision.reason is TriggerReason.INTERJECTION:
                fired_candidate_ids.add(c.candidate_id)
                fires.append(_fire_from(decision, now))

    # Walk the timeline. Path-A summon utterances are excluded from precision
    # entirely (eval-plan §"Why precision"): the runner never calls on_summon and
    # never counts a summon. Between timeline moments we tick the controller at
    # EVAL_TICK_SECONDS so a fire lands at a realistic simulated time once the gap
    # opens (the live ticker behavior), then we also re-check right after each
    # moment is dispatched.
    prev_t = 0.0
    for m in fx.timeline:
        # Tick from prev_t up to this moment's t, re-evaluating open candidates,
        # so a fire is attributed to the moment the gap actually elapses.
        _tick_between(clock, prev_t, m.t, _try_fire_open_candidates)
        clock.set(m.t)
        if m.kind is MomentKind.SPEECH_START:
            gate.on_speech_start()
        elif m.kind is MomentKind.SPEECH_END:
            gate.on_speech_end()
        # UTTERANCE moments carry no gate effect here (the labeled candidates,
        # not the raw text, drive the verdict — see eval-plan §"How it runs").
        _try_fire_open_candidates()
        prev_t = m.t

    # After the last moment, keep ticking to the end of every still-open match
    # window so a fire that the gap allows but lands after the final timeline
    # entry is still captured.
    last_window_end = max((c.match_to for c in candidates), default=prev_t)
    _tick_between(clock, prev_t, last_window_end, _try_fire_open_candidates)

    return _score(fx, fires, fired_candidate_ids)


def _fire_from(decision: SummonDecision, t_fire: float) -> Fire:
    assert decision.interjection is not None  # invariant: Path B carries an offer
    i = decision.interjection
    return Fire(t_fire=t_fire, category=i.category, offer=i.offer, confidence=i.confidence)


def _tick_between(clock: ManualClock, start: float, end: float, try_fire) -> None:  # type: ignore[no-untyped-def]
    """Advance the clock from ``start`` to ``end`` in EVAL_TICK_SECONDS steps.

    At each step ``try_fire`` is called so an open candidate fires the moment the
    politeness gap elapses (rather than only at the next timeline moment). Mirrors
    the live ticker. No-op if ``end <= start``.
    """
    t = start
    while t + EVAL_TICK_SECONDS < end:
        t += EVAL_TICK_SECONDS
        clock.set(t)
        try_fire()


def _score(fx: Fixture, fires: list[Fire], fired_candidate_ids: set[str]) -> FixtureResult:
    """Match each fire to a candidate and score it (eval-plan §"Precision computation")."""
    candidates = sorted(fx.candidates, key=lambda c: c.match_from)
    scored: list[ScoredFire] = []
    for fire in fires:
        matched = _match(fire, candidates)
        if matched is None:
            # Unmatched fire — the controller spoke where no candidate exists.
            # Counted as false (eval-plan §"Match" #1).
            scored.append(ScoredFire(fire=fire, matched_candidate_id=None, useful=False))
            continue
        useful = (
            matched.label is Label.USEFUL
            and matched.category is not None
            and fire.category is WallCategory(matched.category)
        )
        scored.append(
            ScoredFire(fire=fire, matched_candidate_id=matched.candidate_id, useful=useful)
        )

    # Misses: USEFUL candidates the controller never fired on (recall datum only).
    missed = [
        c.candidate_id
        for c in fx.candidates
        if c.label is Label.USEFUL and c.candidate_id not in fired_candidate_ids
    ]
    return FixtureResult(
        fixture_id=fx.fixture_id, scored_fires=scored, missed_useful_candidate_ids=missed
    )


def _match(fire: Fire, candidates: list[Candidate]) -> Candidate | None:
    """The candidate whose ``[match_from, match_to]`` contains ``fire.t_fire``.

    Windows are authored non-overlapping (``Fixture.validate``), so at most one
    matches; ``None`` if the fire fell in no window (an unmatched fire).
    """
    for c in candidates:
        if c.match_from <= fire.t_fire <= c.match_to:
            return c
    return None


def run_fixtures(fixtures: list[Fixture]) -> EvalResult:
    """Score a set of fixtures and aggregate (micro-average over all fires)."""
    return EvalResult(per_fixture=[run_fixture(fx) for fx in fixtures])
