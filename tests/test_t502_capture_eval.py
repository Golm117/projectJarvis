"""T-502 — capture-and-label tooling + the precision eval runner.

Model-free, mic-free, network-free (the eval posture). Three groups:

* **Fixture schema** — (de)serialization round-trips, validation, labeled-ness.
* **Capture** — the recorder observes a simulated run (the recording gate + the
  wall-backend wrap + the on_* callbacks) and emits a raw fixture whose
  candidates carry the dropped verdicts, not just the fired one.
* **Eval runner** — precision over the seeded fixtures, including a false-positive
  case driving precision < 1.0, plus the per-behavior expectations.
"""

from __future__ import annotations

import pytest

from jarvis.clock import ManualClock
from jarvis.core.summon_controller import SummonController
from jarvis.eval.capture import CaptureRecorder
from jarvis.eval.fixture import (
    Candidate,
    Config,
    Fixture,
    Label,
    Moment,
    MomentKind,
    load_fixture,
    loads_fixture,
)
from jarvis.eval.label import render_candidates, set_label, unlabeled_ids
from jarvis.eval.runner import run_fixture, run_fixtures
from jarvis.eval.seed import seed_fixtures
from jarvis.types import EngagementHandoff, Utterance, WallCategory

# ---------------------------------------------------------------------------
# Fixture schema
# ---------------------------------------------------------------------------


def _useful_fixture() -> Fixture:
    return Fixture(
        fixture_id="t",
        description="d",
        config=Config(politeness_gap_seconds=2.0, interjection_confidence_floor=0.70),
        timeline=[
            Moment(0.0, MomentKind.SPEECH_START),
            Moment(0.0, MomentKind.UTTERANCE, speaker="A", text="what was that?"),
            Moment(2.4, MomentKind.SPEECH_END),
        ],
        candidates=[
            Candidate(
                candidate_id="c1",
                match_from=2.4,
                match_to=8.0,
                wall=True,
                category="factual_gap",
                label=Label.USEFUL,
                observed_confidence=0.90,
                observed_offer="Want me to look that up?",
            )
        ],
    )


def test_fixture_round_trips_through_json():
    fx = _useful_fixture()
    fx.validate()
    back = loads_fixture(fx.to_json())
    assert back.to_dict() == fx.to_dict()


def test_fixture_round_trips_through_disk(tmp_path):
    fx = _useful_fixture()
    path = tmp_path / "fx.json"
    fx.save(path)
    back = load_fixture(path)
    assert back.to_dict() == fx.to_dict()


def test_validate_rejects_non_monotonic_timeline():
    fx = _useful_fixture()
    fx.timeline = [
        Moment(2.0, MomentKind.SPEECH_END),
        Moment(1.0, MomentKind.SPEECH_START),  # goes backwards
    ]
    with pytest.raises(ValueError, match="not monotonic"):
        fx.validate()


def test_validate_rejects_overlapping_candidate_windows():
    fx = _useful_fixture()
    fx.candidates = [
        Candidate("c1", 0.0, 5.0, True, "factual_gap", Label.USEFUL),
        Candidate("c2", 3.0, 9.0, True, "factual_gap", Label.USEFUL),  # overlaps c1
    ]
    with pytest.raises(ValueError, match="overlap"):
        fx.validate()


def test_validate_rejects_wall_without_category():
    c = Candidate("c1", 0.0, 5.0, wall=True, category=None, label=Label.USEFUL)
    with pytest.raises(ValueError, match="must name a WallCategory"):
        c.validate()


def test_validate_rejects_non_wall_with_category():
    c = Candidate("c1", 0.0, 5.0, wall=False, category="factual_gap", label=Label.FALSE)
    with pytest.raises(ValueError, match="non-wall must have category null"):
        c.validate()


def test_is_fully_labeled_tracks_unlabeled_candidates():
    fx = _useful_fixture()
    assert fx.is_fully_labeled()
    fx.candidates[0].label = Label.UNLABELED
    assert not fx.is_fully_labeled()


def test_observed_category_round_trips():
    fx = _useful_fixture()
    fx.candidates[0].observed_category = "stuck_point"
    back = loads_fixture(fx.to_json())
    assert back.candidates[0].observed_category == "stuck_point"


# ---------------------------------------------------------------------------
# Labeling workflow
# ---------------------------------------------------------------------------


def test_set_label_fills_an_unlabeled_candidate():
    fx = _useful_fixture()
    fx.candidates[0].label = Label.UNLABELED
    assert unlabeled_ids(fx) == ["c1"]
    set_label(fx, "c1", Label.FALSE, rationale="rhetorical")
    assert fx.candidates[0].label is Label.FALSE
    assert fx.candidates[0].rationale == "rhetorical"
    assert unlabeled_ids(fx) == []


def test_set_label_can_correct_category_and_window():
    fx = _useful_fixture()
    set_label(fx, "c1", Label.USEFUL, category="stuck_point", match_from=3.0, match_to=6.0)
    c = fx.candidates[0]
    assert c.category == "stuck_point"
    assert (c.match_from, c.match_to) == (3.0, 6.0)


def test_set_label_unknown_candidate_raises():
    fx = _useful_fixture()
    with pytest.raises(KeyError):
        set_label(fx, "nope", Label.USEFUL)


def test_render_candidates_includes_observed_facts():
    fx = _useful_fixture()
    text = render_candidates(fx)
    assert "c1" in text
    assert "0.90" in text  # observed confidence
    assert "factual_gap" in text


# ---------------------------------------------------------------------------
# Capture — the recorder observes a simulated run
# ---------------------------------------------------------------------------


def _drive_capture_run() -> tuple[CaptureRecorder, ManualClock]:
    """Simulate what run_live does to the recorder, on a ManualClock (no mic/model).

    Two walls are detected: the first is DROPPED (confidence below floor → the
    controller stays silent), the second FIRES after the gap. Capture must record
    BOTH as candidates — proving it sees dropped verdicts, not just fired ones.
    """
    clock = ManualClock()
    rec = CaptureRecorder(fixture_id="cap", description="sim")

    # Build the recording gate + wrap a scripted wall backend (returns a below-
    # floor wall first, an above-floor wall second).
    gate = rec.wrap_gate(clock.now)
    from tests.fakes import FakeWallBackend, wall

    inner = FakeWallBackend(
        verdicts=[
            wall("factual_gap", 0.55, offer="low-conf offer"),  # will be dropped
            wall("unanswered_question", 0.90, offer="high-conf offer"),  # will fire
        ]
    )
    wrapped = rec.wrap_wall_backend(inner, clock.now)
    controller = SummonController(gate)

    # --- candidate 1: a below-floor wall, dropped -----------------------------
    gate.on_speech_start()
    rec.record_utterance(Utterance("A", "what was that number?", clock.now()))
    clock.advance(2.4)
    gate.on_speech_end()
    clock.advance(2.5)  # gap is open
    v1 = wrapped.detect_wall("what was that number?", "")
    d1 = controller.consider_interjection(v1)
    assert d1 is None  # dropped (below floor)

    # --- candidate 2: an above-floor wall, fires ------------------------------
    clock.advance(1.0)
    gate.on_speech_start()
    rec.record_utterance(Utterance("B", "who handled the contract?", clock.now()))
    clock.advance(2.4)
    gate.on_speech_end()
    clock.advance(2.5)  # gap open again
    v2 = wrapped.detect_wall("who handled the contract?", "")
    d2 = controller.consider_interjection(v2)
    assert d2 is not None and d2.interjection is not None
    rec.record_interjection(d2.interjection)

    return rec, clock


def test_capture_records_both_dropped_and_fired_candidates():
    rec, _ = _drive_capture_run()
    fx = rec.build_fixture()
    fx.validate()
    # Two walls were detected → two candidates (the dropped one is NOT lost).
    assert len(fx.candidates) == 2
    cats = {c.category for c in fx.candidates}
    assert cats == {"factual_gap", "unanswered_question"}
    by_cat = {c.category: c for c in fx.candidates}
    assert by_cat["factual_gap"].observed_confidence == 0.55
    assert by_cat["factual_gap"].observed_fired is False
    assert by_cat["unanswered_question"].observed_fired is True


def test_capture_emits_unlabeled_candidates():
    rec, _ = _drive_capture_run()
    fx = rec.build_fixture()
    # A raw capture is UNLABELED — the labeler fills the ground truth.
    assert all(c.label is Label.UNLABELED for c in fx.candidates)
    assert not fx.is_fully_labeled()


def test_capture_records_speech_edges_in_timeline():
    rec, _ = _drive_capture_run()
    fx = rec.build_fixture()
    kinds = [m.kind for m in fx.timeline]
    assert kinds.count(MomentKind.SPEECH_START) == 2
    assert kinds.count(MomentKind.SPEECH_END) == 2
    assert kinds.count(MomentKind.UTTERANCE) == 2


def test_capture_rebases_timeline_to_zero():
    clock = ManualClock(start=123456.0)  # a boot-relative monotonic start
    rec = CaptureRecorder(fixture_id="cap")
    gate = rec.wrap_gate(clock.now)
    gate.on_speech_start()
    rec.record_utterance(Utterance("A", "hi", clock.now()))
    clock.advance(1.0)
    gate.on_speech_end()
    fx = rec.build_fixture()
    # Earliest moment re-based to ~0 so the fixture is readable.
    assert fx.timeline[0].t == pytest.approx(0.0)


def test_capture_records_summon_as_path_a_not_candidate():
    clock = ManualClock()
    rec = CaptureRecorder(fixture_id="cap")
    rec.wrap_gate(clock.now)
    rec.record_engagement(EngagementHandoff(trigger_reason="summon", summary="", recent_excerpt=""))
    fx = rec.build_fixture()
    # A summon is excluded from precision — it never becomes a candidate.
    assert fx.candidates == []


def test_capture_wall_backend_is_pure_passthrough():
    """The wrapped backend returns the inner verdict unchanged — capture only observes."""
    clock = ManualClock()
    rec = CaptureRecorder(fixture_id="cap")
    rec.wrap_gate(clock.now)
    from tests.fakes import FakeWallBackend, wall

    inner_verdict = wall("explicit_ask", 0.88, offer="x")
    inner = FakeWallBackend(verdict=inner_verdict)
    wrapped = rec.wrap_wall_backend(inner, clock.now)
    out = wrapped.detect_wall("t", "s")
    assert out is inner_verdict  # exact object, untouched


def test_captured_then_labeled_fixture_scores():
    """Round-trip: capture → label → score (the full T-502 → T-503 path)."""
    rec, _ = _drive_capture_run()
    fx = rec.build_fixture()
    # Label both candidates: the dropped below-floor one is a real useful wall
    # (a miss), the fired one is useful.
    by_cat = {c.category: c for c in fx.candidates}
    set_label(fx, by_cat["factual_gap"].candidate_id, Label.USEFUL)
    set_label(fx, by_cat["unanswered_question"].candidate_id, Label.USEFUL)
    assert fx.is_fully_labeled()
    result = run_fixture(fx)
    # The fired high-conf wall → one useful fire; the dropped below-floor wall →
    # a miss (precision unaffected).
    assert result.total_fires == 1
    assert result.useful_fires == 1


# ---------------------------------------------------------------------------
# Eval runner — precision over the seeded corpus
# ---------------------------------------------------------------------------


def test_runner_refuses_unlabeled_fixture():
    fx = _useful_fixture()
    fx.candidates[0].label = Label.UNLABELED
    with pytest.raises(ValueError, match="unlabeled candidates"):
        run_fixture(fx)


def test_useful_fixture_scores_precision_one():
    fx = _useful_fixture()
    result = run_fixtures([fx])
    assert result.total_fires == 1
    assert result.useful_fires == 1
    assert result.precision == 1.0


def test_false_positive_drives_precision_below_one():
    """A labeled-false candidate that fires → a false fire → precision < 1.0."""
    fx = _useful_fixture()
    fx.fixture_id = "fp"
    fx.candidates[0].label = Label.FALSE  # the fire is now a false positive
    result = run_fixtures([fx])
    assert result.total_fires == 1
    assert result.false_fires == 1
    assert result.precision == 0.0


def test_no_fire_reports_undefined_precision():
    """A fixture that never fires has undefined (None) precision, not 0."""
    fx = _useful_fixture()
    fx.candidates[0].observed_confidence = 0.10  # below the 0.70 floor → no fire
    result = run_fixtures([fx])
    assert result.total_fires == 0
    assert result.precision is None


def test_abort_on_resume_removes_would_be_false_fire():
    """Speech resumes before the gap → the controller aborts → no fire counted."""
    fx = Fixture(
        fixture_id="abort",
        config=Config(politeness_gap_seconds=2.0),
        timeline=[
            Moment(0.0, MomentKind.SPEECH_START),
            Moment(0.6, MomentKind.SPEECH_END),
            Moment(1.5, MomentKind.SPEECH_START),  # resumes before the 2 s gap
            Moment(3.0, MomentKind.SPEECH_END),
        ],
        candidates=[
            Candidate(
                "c1",
                0.6,
                1.5,
                wall=True,
                category="factual_gap",
                label=Label.FALSE,
                observed_confidence=0.90,
            )
        ],
    )
    result = run_fixture(fx)
    assert result.total_fires == 0  # aborted → would-be false fire removed


def test_wrong_category_fire_scores_false():
    """A right-moment but wrong-category fire scores false (eval-plan #3)."""
    fx = _useful_fixture()
    fx.candidates[0].category = "stuck_point"  # ground truth
    fx.candidates[0].observed_category = "factual_gap"  # what the detector fired
    result = run_fixture(fx)
    assert result.total_fires == 1
    assert result.false_fires == 1


def test_seeded_corpus_scores_with_false_positives_present():
    """The full seeded set: precision < 1.0 (the FP cases are present and counted)."""
    result = run_fixtures(seed_fixtures())
    assert result.total_fires == 5
    assert result.useful_fires == 3
    assert result.false_fires == 2
    assert result.precision == pytest.approx(0.6)


def test_seeded_what_do_you_need_is_a_false_fire():
    """The borderline 'What do you need?' case scores as a false fire (qa verdict)."""
    from jarvis.eval.seed import seed_false_what_do_you_need

    result = run_fixture(seed_false_what_do_you_need())
    assert result.total_fires == 1
    assert result.false_fires == 1


def test_seeded_summon_is_excluded_entirely():
    """A Path-A summon contributes no fire to precision (numerator and denominator)."""
    from jarvis.eval.seed import seed_summon_excluded

    result = run_fixture(seed_summon_excluded())
    assert result.total_fires == 0
    assert result.missed_useful_candidate_ids == []


def test_seeded_backoff_fires_once_and_misses_the_repeat():
    """Back-off: the same wall surfaces twice → one fire, the repeat suppressed."""
    from jarvis.eval.seed import ff_backoff_no_nag

    result = run_fixture(ff_backoff_no_nag())
    assert result.total_fires == 1
    assert result.useful_fires == 1
    assert "c2" in result.missed_useful_candidate_ids


def test_precision_by_category_breakdown():
    result = run_fixtures(seed_fixtures())
    by_cat = result.precision_by_category()
    # factual_gap over-fires (the FP cases live there): 2 useful of 4 fires.
    assert by_cat[WallCategory.FACTUAL_GAP] == (2, 4)
    assert by_cat[WallCategory.UNANSWERED_QUESTION] == (1, 1)


def test_config_sweep_changes_the_outcome():
    """T-503's lever: lowering the floor turns a below-floor miss into a fire."""
    from jarvis.eval.seed import ff_below_floor

    fx = ff_below_floor()
    # At the default 0.70 floor, the 0.55 wall does not fire.
    assert run_fixture(fx).total_fires == 0
    # Lower the floor below 0.55 (the sweep) and re-score: it now fires.
    fx.config = Config(politeness_gap_seconds=2.0, interjection_confidence_floor=0.50)
    assert run_fixture(fx).total_fires == 1


def test_all_seed_fixtures_validate_and_are_labeled():
    for fx in seed_fixtures():
        fx.validate()
        assert fx.is_fully_labeled()
