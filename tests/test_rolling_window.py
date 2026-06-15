"""Tests for RollingWindow + the frozen Utterance type (T-002).

Drives eviction deterministically with the shared ``SimulatedClock`` (T-009) —
no real ``sleep``. Asserts only on the public interface (``utterances``,
``transcript``, ``keywords``), never on internals (module map §"Cross-cutting
design constraints" #3 / eval-plan "test external behavior").
"""

from __future__ import annotations

import pytest

from jarvis.core.rolling_window import RollingWindow
from jarvis.types import Utterance


def u(speaker: str, text: str, ts: float) -> Utterance:
    return Utterance(speaker=speaker, text=text, ts=ts)


# --- Utterance: frozen value type --------------------------------------------


def test_utterance_is_frozen():
    utt = Utterance(speaker="Alex", text="hi", ts=1.0)
    with pytest.raises(Exception):  # noqa: B017 - FrozenInstanceError is a dataclass detail
        utt.text = "changed"  # type: ignore[misc]


def test_utterance_equality_by_value():
    assert Utterance("Alex", "hi", 1.0) == Utterance("Alex", "hi", 1.0)
    assert Utterance("Alex", "hi", 1.0) != Utterance("Alex", "hi", 2.0)


# --- Eviction by count -------------------------------------------------------


def test_evicts_by_count_keeping_most_recent(clock):
    w = RollingWindow(max_utterances=3, max_seconds=1000, now=clock.now)
    for i in range(5):
        w.add(u("Alex", f"line {i}", ts=float(i)))
    texts = [x.text for x in w.utterances()]
    assert texts == ["line 2", "line 3", "line 4"]  # oldest two dropped


def test_count_bound_of_one(clock):
    w = RollingWindow(max_utterances=1, max_seconds=1000, now=clock.now)
    w.add(u("A", "first", 0.0))
    w.add(u("B", "second", 1.0))
    assert [x.text for x in w.utterances()] == ["second"]


# --- Eviction by time (driven by advancing the simulated clock) --------------


def test_evicts_by_time_on_add(clock):
    # Window holds 10 minutes; advance past it and add — stale entries go.
    w = RollingWindow(max_utterances=100, max_seconds=60, now=clock.now)
    w.add(u("Alex", "old", ts=clock.now()))  # ts = 0
    clock.advance(120)  # 2 minutes pass
    w.add(u("Sam", "new", ts=clock.now()))  # ts = 120
    texts = [x.text for x in w.utterances()]
    assert texts == ["new"]  # "old" aged out (120s > 60s bound)


def test_time_eviction_keeps_boundary_utterance(clock):
    w = RollingWindow(max_utterances=100, max_seconds=60, now=clock.now)
    w.add(u("Alex", "edge", ts=0.0))
    clock.advance(60)  # exactly the bound — age == max_seconds, still kept
    w.add(u("Sam", "now", ts=60.0))
    assert [x.text for x in w.utterances()] == ["edge", "now"]
    clock.advance(0.01)  # one tick past the bound for "edge"
    # A read alone re-evicts against current time (no new add needed).
    assert [x.text for x in w.utterances()] == ["now"]


def test_window_ages_on_read_without_add(clock):
    # Time passing with no new utterance still ages the window out.
    w = RollingWindow(max_utterances=100, max_seconds=30, now=clock.now)
    w.add(u("Alex", "hello", ts=0.0))
    assert len(w.utterances()) == 1
    clock.advance(31)
    assert w.utterances() == []  # aged out purely by elapsed time
    assert w.transcript() == ""


# --- Both bounds interacting -------------------------------------------------


def test_both_bounds_apply(clock):
    # Count bound = 5, time bound = 10s. Add 5 within 2s, then jump 20s and add.
    w = RollingWindow(max_utterances=5, max_seconds=10, now=clock.now)
    for i in range(5):
        clock.set(float(i))
        w.add(u("Alex", f"a{i}", ts=clock.now()))
    assert len(w.utterances()) == 5
    clock.advance(20)  # now t=24; everything so far is older than 10s
    w.add(u("Sam", "fresh", ts=clock.now()))
    assert [x.text for x in w.utterances()] == ["fresh"]


# --- transcript() / keywords() rendering -------------------------------------


def test_transcript_rendering(clock):
    w = RollingWindow(max_utterances=10, max_seconds=1000, now=clock.now)
    w.add(u("Alex", "did you book the flights?", 0.0))
    w.add(u("Sam", "not yet", 1.0))
    assert w.transcript() == "Alex: did you book the flights?\nSam: not yet"


def test_transcript_reflects_eviction(clock):
    w = RollingWindow(max_utterances=2, max_seconds=1000, now=clock.now)
    w.add(u("Alex", "one", 0.0))
    w.add(u("Sam", "two", 1.0))
    w.add(u("Alex", "three", 2.0))
    assert w.transcript() == "Sam: two\nAlex: three"


def test_keywords_union_minus_stopwords(clock):
    w = RollingWindow(max_utterances=10, max_seconds=1000, now=clock.now)
    w.add(u("Alex", "Tokyo flights in October", 0.0))
    w.add(u("Sam", "the conference dates", 1.0))
    ks = w.keywords()
    assert {"tokyo", "flights", "october", "conference", "dates"} <= ks
    assert "the" not in ks and "in" not in ks  # stopwords dropped


def test_empty_window(clock):
    w = RollingWindow(max_utterances=10, max_seconds=1000, now=clock.now)
    assert w.utterances() == []
    assert w.transcript() == ""
    assert w.keywords() == set()


# --- Construction guards -----------------------------------------------------


def test_rejects_bad_bounds():
    with pytest.raises(ValueError):
        RollingWindow(max_utterances=0, max_seconds=10)
    with pytest.raises(ValueError):
        RollingWindow(max_utterances=5, max_seconds=-1)
