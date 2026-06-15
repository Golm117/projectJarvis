"""Tests for TopicShiftDetector (T-003).

Exercises the pure decision through its public interface (``shifted`` /
``similarity`` / ``threshold``) — representative shift and no-shift cases, the
threshold boundary, the empty-set edges, and threshold configurability. No clock
or fakes needed: this module is a pure function of its two keyword sets.
"""

from __future__ import annotations

import pytest

from jarvis.core.text import keywords
from jarvis.core.topic_shift import (
    DEFAULT_TOPIC_SHIFT_THRESHOLD,
    TopicShiftDetector,
)

# --- Representative no-shift: same conversation continues ---------------------


def test_no_shift_when_topic_continues():
    det = TopicShiftDetector()
    basis = keywords("did you book the Tokyo flights for October")
    current = keywords("the Tokyo flights — book them for the second week of October")
    # Strong keyword overlap (tokyo, flights, october, book) → not a shift.
    assert det.shifted(current, basis) is False
    assert det.similarity(current, basis) >= det.threshold


def test_no_shift_on_identical_content():
    det = TopicShiftDetector()
    ks = keywords("ramen on fourth street is incredible")
    assert det.similarity(ks, ks) == 1.0
    assert det.shifted(ks, ks) is False


# --- Representative shift: conversation moves to a new topic ------------------


def test_shift_when_topic_changes():
    det = TopicShiftDetector()
    # Prototype's demo pivot: flights/Tokyo → ramen restaurant.
    basis = keywords("did you book the Tokyo flights for the October conference")
    current = keywords("have you tried that new ramen place on fourth street")
    assert det.shifted(current, basis) is True
    assert det.similarity(current, basis) < det.threshold


def test_shift_with_no_overlap_is_zero_similarity():
    det = TopicShiftDetector()
    basis = {"tokyo", "flights", "october"}
    current = {"ramen", "street", "weekend"}
    assert det.similarity(current, basis) == 0.0
    assert det.shifted(current, basis) is True


# --- Threshold boundary (strictly-below semantics) ---------------------------


def test_boundary_is_strictly_below():
    # similarity exactly == threshold is NOT a shift (strict <).
    det = TopicShiftDetector(threshold=0.5)
    # |a ∩ b| = 2, |a ∪ b| = 4 → jaccard 0.5, exactly the threshold.
    basis = {"alpha", "beta"}
    current = {"alpha", "beta", "gamma", "delta"}
    assert det.similarity(current, basis) == 0.5
    assert det.shifted(current, basis) is False  # 0.5 < 0.5 is False


def test_just_below_threshold_is_a_shift():
    det = TopicShiftDetector(threshold=0.5)
    # |a ∩ b| = 1, |a ∪ b| = 3 → jaccard ≈ 0.333 < 0.5 → shift.
    basis = {"alpha", "beta"}
    current = {"alpha", "gamma", "delta"}
    assert det.similarity(current, basis) < 0.5
    assert det.shifted(current, basis) is True


# --- Empty-set edges (cold start) --------------------------------------------


def test_empty_basis_is_a_shift():
    # Cold start: nothing summarized yet, content present → drift from "nothing".
    det = TopicShiftDetector()
    current = {"tokyo", "flights"}
    assert det.similarity(current, set()) == 0.0
    assert det.shifted(current, set()) is True


def test_both_empty_is_not_a_shift():
    # Two empty sets are identical (similarity 1.0) → no shift.
    det = TopicShiftDetector()
    assert det.similarity(set(), set()) == 1.0
    assert det.shifted(set(), set()) is False


# --- Configurability ---------------------------------------------------------


def test_threshold_is_configurable_and_exposed():
    assert TopicShiftDetector().threshold == DEFAULT_TOPIC_SHIFT_THRESHOLD
    assert TopicShiftDetector(threshold=0.6).threshold == 0.6


def test_threshold_changes_the_verdict():
    basis = {"alpha", "beta", "gamma"}
    current = {"alpha", "delta", "epsilon"}  # jaccard = 1/5 = 0.2
    sim = TopicShiftDetector().similarity(current, basis)
    assert sim == pytest.approx(0.2)
    # A lenient threshold (0.1) tolerates this drift; the default (0.3) doesn't.
    assert TopicShiftDetector(threshold=0.1).shifted(current, basis) is False
    assert TopicShiftDetector(threshold=0.3).shifted(current, basis) is True


def test_rejects_out_of_range_threshold():
    with pytest.raises(ValueError):
        TopicShiftDetector(threshold=-0.1)
    with pytest.raises(ValueError):
        TopicShiftDetector(threshold=1.5)
