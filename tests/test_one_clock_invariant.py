"""T-301 — One-clock invariant pinning test.

Verifies that in the ``run_live`` wiring, the ``TurnTakingGate``, the
``RollingWindow``, and ``MicSource``'s ``Utterance.ts`` all derive from a single
injected ``now`` callable — no module reads ``time.monotonic()`` on its own.

This is the **one-clock invariant**: gate ≡ window ≡ ``Utterance.ts`` share one
clock. It was discovered to be violated in T-105 (frame-derived ts vs.
``time.monotonic`` window → instant eviction of every live utterance) and fixed by
injecting the shared ``now`` into ``MicSource`` in ``run_live``. These tests pin
that fix so a regression is caught immediately.

No real mic, no real model, no real clock — everything runs on a ``SimulatedClock``
via ``FakeAudioSource`` + ``FakeTranscriber`` + ``EnergyFrameClassifier``.

Design note (why this is not qa-gated):
  These tests add no gate/summon/wall *logic* — they assert the *wiring* in the
  live pipeline. The modules under test (``TurnTakingGate``, ``RollingWindow``,
  ``MicSource``) already have individual tests; this file pins their *integration*
  under a shared clock, which is what T-301 is about.
"""

from __future__ import annotations

from jarvis.attention_layer import AttentionLayer
from jarvis.audio.mic_source import MicSource
from jarvis.audio.source import FakeAudioSource
from jarvis.audio.vad import EnergyFrameClassifier, SileroVad
from jarvis.core.rolling_window import RollingWindow
from jarvis.core.turn_taking_gate import TurnTakingGate
from tests.clock import SimulatedClock
from tests.fakes import FakeResponder, FakeVoice

# Fast debounce so short synthetic patterns produce clean edges.
_START_FRAMES = 2
_END_FRAMES = 2
_AMPLITUDE = 0.3


class FakeTranscriber:
    """Records calls; returns canned text (no model)."""

    def __init__(self, texts: list[str] | None = None) -> None:
        self._texts = list(texts) if texts else ["some speech"]
        self.calls: list[tuple[object, int]] = []

    def transcribe(self, waveform: object, sample_rate: int) -> str:
        self.calls.append((waveform, sample_rate))
        idx = min(len(self.calls) - 1, len(self._texts) - 1)
        return self._texts[idx]


def _make_vad(gate: TurnTakingGate | None = None) -> SileroVad:
    return SileroVad(
        classifier=EnergyFrameClassifier(threshold=0.05),
        gate=gate,
        speech_start_frames=_START_FRAMES,
        silence_end_frames=_END_FRAMES,
    )


# ---------------------------------------------------------------------------
# Core invariant: one shared clock drives gate, window, and Utterance.ts
# ---------------------------------------------------------------------------


def test_shared_now_stamps_utterance_ts_from_that_clock() -> None:
    """``Utterance.ts`` equals ``now()`` at the moment the segment closes.

    When a ``now`` callable is injected into ``MicSource`` (the live-pipeline
    pattern from ``run_live``), ``Utterance.ts`` is ``now()`` — not a frame-derived
    value. This is the fix from T-105: the window's eviction clock and the
    producer's ``ts`` must share one timeline or live utterances evict instantly.
    """
    clock = SimulatedClock()
    # Set a large offset that matches a real monotonic value (e.g. seconds since boot).
    clock.set(400_000.0)

    source = FakeAudioSource.from_pattern(
        [("silence", 3), ("speech", 10), ("silence", 4)], amplitude=_AMPLITUDE
    )
    gate = TurnTakingGate(clock.now)
    transcriber = FakeTranscriber(["test utterance"])
    mic = MicSource(
        source=source,
        gate=gate,
        transcriber=transcriber,
        vad=_make_vad(gate),
        now=clock.now,  # <-- the invariant: the shared clock
    )

    utts = list(mic.utterances())

    assert len(utts) == 1
    # ts comes from the injected clock, not the frame timeline (~0.5 s).
    # The frame-derived default would be 15 × (512/16000) ≈ 0.48 s; the injected
    # clock gives 400000.0 s — these are clearly distinguishable.
    assert utts[0].ts == 400_000.0, (
        f"Utterance.ts={utts[0].ts!r} — expected the injected clock's value 400000.0, "
        "not a frame-derived ts. One-clock invariant is broken."
    )


def test_window_does_not_evict_live_utterance_when_clock_is_shared() -> None:
    """The window retains a live utterance when ``ts`` and eviction share one clock.

    This is the direct regression test for the T-105 bug: if ``Utterance.ts`` is
    frame-derived (~0.48 s) but ``RollingWindow.now`` returns ``time.monotonic``
    (~400000 s), the window's time-bound evicts every live utterance immediately
    (0.48 < 400000 - 120). With a shared clock, ``ts ≈ now()`` so the utterance
    is fresh and is kept.
    """
    clock = SimulatedClock()
    clock.set(400_000.0)  # simulate a large monotonic value

    window = RollingWindow(max_utterances=12, max_seconds=120.0, now=clock.now)
    source = FakeAudioSource.from_pattern(
        [("silence", 3), ("speech", 10), ("silence", 4)], amplitude=_AMPLITUDE
    )
    gate = TurnTakingGate(clock.now)
    transcriber = FakeTranscriber(["window retention check"])
    mic = MicSource(
        source=source,
        gate=gate,
        transcriber=transcriber,
        vad=_make_vad(gate),
        now=clock.now,  # shared clock — ts and window share a timeline
    )

    for utt in mic.utterances():
        window.add(utt)

    # With the shared clock the utterance's ts (~400000 s) is within the window's
    # max_seconds (120 s) of now (~400000 s) — so it should survive.
    retained = window.utterances()
    assert len(retained) == 1, (
        f"Window has {len(retained)} utterances — expected 1. "
        "One-clock invariant failure: ts and eviction clock diverged."
    )
    assert retained[0].text == "window retention check"


def test_frame_derived_ts_would_cause_eviction_with_large_clock_offset() -> None:
    """Negative control: demonstrates WHY the shared clock is needed.

    If ``MicSource`` is constructed WITHOUT an injected ``now`` (the T-104 default,
    frame-derived ts), but the ``RollingWindow`` runs on a large-offset clock
    (``time.monotonic``-like), the utterance evicts immediately. This is the T-105
    regression the fix addresses.
    """
    clock = SimulatedClock()
    clock.set(400_000.0)  # large offset — like seconds since boot

    window = RollingWindow(max_utterances=12, max_seconds=120.0, now=clock.now)
    source = FakeAudioSource.from_pattern(
        [("silence", 3), ("speech", 10), ("silence", 4)], amplitude=_AMPLITUDE
    )
    gate = TurnTakingGate(clock.now)
    transcriber = FakeTranscriber(["should evict"])
    mic = MicSource(
        source=source,
        gate=gate,
        transcriber=transcriber,
        vad=_make_vad(gate),
        # now=None → frame-derived ts (the T-105 bug path)
    )

    for utt in mic.utterances():
        window.add(utt)

    # Frame-derived ts ≈ 0.48 s; window.now() = 400000 s; cutoff = 400000 - 120 =
    # 399880 s; 0.48 < 399880 → evicted. The window is empty.
    retained = window.utterances()
    assert len(retained) == 0, (
        "Expected 0 retained utterances with a diverged clock — the negative "
        "control shows the T-105 bug that the shared-clock fix corrects."
    )


# ---------------------------------------------------------------------------
# Gate reads the same clock as the window and ts (end-to-end wiring check)
# ---------------------------------------------------------------------------


def test_gate_window_micsource_all_use_the_same_now() -> None:
    """The gate, window, and MicSource.ts all run on one injected now callable.

    This is the structural invariant from ``run_live``:
      gate   = TurnTakingGate(now)          → gate._now is now
      window = RollingWindow(..., now)       → window._now is now
      mic    = MicSource(..., now=now)       → mic._now is now

    Python bound methods produce a new wrapper object on each attribute access,
    so we capture the ``clock.now`` bound method once and verify all three modules
    received that exact object (same id). This confirms no module substituted a
    different callable (e.g. a captured ``time.monotonic``).
    """
    clock = SimulatedClock()
    now = clock.now  # capture once — same object passed to all three modules

    gate = TurnTakingGate(now)
    window = RollingWindow(12, 120.0, now)
    source = FakeAudioSource.from_pattern(
        [("silence", 3), ("speech", 8), ("silence", 4)], amplitude=_AMPLITUDE
    )
    transcriber = FakeTranscriber(["shared clock check"])
    mic = MicSource(
        source=source,
        gate=gate,
        transcriber=transcriber,
        vad=_make_vad(gate),
        now=now,
    )

    # Assert all three components reference the same callable object.
    assert gate._now is now, "TurnTakingGate._now is not the shared clock"  # noqa: SLF001
    assert window._now is now, "RollingWindow._now is not the shared clock"  # noqa: SLF001
    assert mic._now is now, "MicSource._now is not the shared clock"  # noqa: SLF001

    # Smoke: the pipeline actually produces an utterance without eviction.
    for utt in mic.utterances():
        window.add(utt)

    assert len(window.utterances()) == 1


def test_gate_and_window_built_by_attention_layer_build_share_the_same_now() -> None:
    """``AttentionLayer.build`` passes the same ``now`` to the gate and window.

    The ``build`` classmethod constructs both the ``RollingWindow`` and wires the
    given ``gate`` — both should share the caller's ``now`` callable. This mirrors
    exactly what ``run_live`` does. We capture ``clock.now`` once to get a stable
    object reference for identity comparison.
    """
    clock = SimulatedClock()
    now = clock.now  # capture once — same reference passed everywhere
    gate = TurnTakingGate(now)

    layer = AttentionLayer.build(
        gate=gate,
        now=now,
        responder=FakeResponder(),
        voice=FakeVoice(),
    )

    # The window inside the layer must use the same clock.
    assert layer._window._now is now, (  # noqa: SLF001
        "AttentionLayer.build did not pass the shared now to RollingWindow"
    )
    # The gate in the controller must be the one we gave build().
    assert layer._controller._gate is gate, (  # noqa: SLF001
        "AttentionLayer.build did not wire the given gate into SummonController"
    )


# ---------------------------------------------------------------------------
# Silence-gap: generator blocks during silence, Path B is never re-evaluated
# ---------------------------------------------------------------------------


def test_path_b_not_re_evaluated_during_silence_between_utterances() -> None:
    """Confirms the T-302 gap: Path B is evaluated once per utterance, at ingest.

    The ``MicSource.utterances()`` generator blocks on ``source.frames()`` during
    silence — it yields nothing until the VAD detects the next speech segment. So
    ``AttentionLayer.ingest`` doesn't run during silence, and
    ``SummonController.consider_interjection`` (which reads
    ``gate.politeness_gap_elapsed()``) is never called as the gap grows.

    Here we confirm this directly: we feed one wall-bearing utterance, advance the
    clock past the politeness gap, then feed a second (non-wall) utterance. The
    wall-check opportunity at gate.politeness_gap_elapsed() == True was *missed*
    because ingest didn't run during the silence — the controller returns None
    both at the first ingest (gap not yet elapsed) and is not called again until
    the next utterance, by which point the gate has a fresh speech_start.

    This test documents the exact gap T-302 must fill: a re-evaluation driver that
    calls consider_interjection() while silence accumulates (between utterances),
    not just at each ingest.
    """
    from jarvis.core.summon_controller import SummonController
    from jarvis.core.turn_taking_gate import TurnTakingGate
    from jarvis.core.wall_detector import WallDetector
    from tests.fakes import FakeWallBackend, wall

    # Build a wall verdict with the helper signature: wall(category, confidence, offer=...).
    wall_verdict = wall("factual_gap", 0.9, offer="I can find that.")

    clock = SimulatedClock()
    gate = TurnTakingGate(clock.now, politeness_gap_seconds=2.0)

    # -- Simulate the speech-end that opens the silence after a wall utterance.
    gate.on_speech_start()
    gate.on_speech_end()  # silence starts at t=0

    # -- Gap has NOT elapsed yet (t=0); controller should return None.
    backend = FakeWallBackend(verdict=wall_verdict)
    detector = WallDetector(backend)
    controller = SummonController(gate)

    verdict = detector.detect("transcript", "summary")
    assert verdict == wall_verdict
    result_before_gap = controller.consider_interjection(verdict)
    assert result_before_gap is None, "Expected None before gap elapsed"
    assert gate.politeness_gap_elapsed() is False

    # -- Advance the clock: the politeness gap opens (t=2.5 s).
    clock.advance(2.5)
    assert gate.politeness_gap_elapsed() is True

    # -- The gap is now open, BUT ingest is NOT running (generator is blocked).
    # This is the silence-gap: consider_interjection is never called here in the
    # v0 pipeline. We verify by NOT calling it — and then simulating what happens
    # at the next utterance: speech resumes, which re-arms the gate.
    gate.on_speech_start()  # next speech → gate re-arms; speech_resumed() latches
    gate.on_speech_end()  # next utterance ends; clears the resumed latch

    # -- Now at the next ingest, the gap is not elapsed (it was reset by speech_start).
    assert gate.politeness_gap_elapsed() is False
    # So even though the gap was open during silence, consider_interjection was
    # never called — and now the gap is closed again.
    result_at_next_utterance = controller.consider_interjection(verdict)
    assert result_at_next_utterance is None, (
        "Expected None: gap closed again after speech resumed and ended. "
        "The silence-gap window was missed — confirming the T-302 integration point."
    )
