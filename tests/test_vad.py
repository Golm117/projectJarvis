"""Tests for SileroVad speech/silence segmentation → gate edges (T-103).

These feed **synthetic frames** (silence vs. speech-energy) through ``SileroVad``
with an injected ``EnergyFrameClassifier`` — deterministic, **no torch, no real
mic** — and assert the correct *sequence* of speech-start / speech-end edges, both
as a raw edge list and as drives onto a real ``TurnTakingGate`` (the frozen edge
seam). The heavy Silero model sits behind the ``FrameClassifier`` seam; only the
optional live-mic check (skipped without a device/permission) touches it.
"""

from __future__ import annotations

import numpy as np
import pytest

from jarvis.audio import (
    DEFAULT_SAMPLE_RATE,
    AudioFrame,
    EnergyFrameClassifier,
    FakeAudioSource,
    FrameClassifier,
    SileroVad,
)
from jarvis.audio.vad import SileroFrameClassifier
from jarvis.core.turn_taking_gate import TurnTakingGate

# A loud-enough tone clears the energy classifier's default 0.05 threshold;
# silence frames are exactly 0.0 RMS. Use start=1 / silence_end small so the
# synthetic patterns stay short and the edge timing is easy to reason about.
SPEECH_AMP = 0.3


def _edges_for(pattern, *, start_frames=1, silence_frames=3, threshold=0.05):
    """Run a (kind,count) pattern through the VAD; return the edge sequence."""
    src = FakeAudioSource.from_pattern(pattern, amplitude=SPEECH_AMP)
    edges: list[str] = []
    vad = SileroVad(
        classifier=EnergyFrameClassifier(threshold=threshold),
        on_edge=edges.append,
        speech_start_frames=start_frames,
        silence_end_frames=silence_frames,
    )
    vad.run(src)
    return edges, vad


# -- seams --------------------------------------------------------------------


def test_energy_classifier_satisfies_frameclassifier_protocol():
    assert isinstance(EnergyFrameClassifier(), FrameClassifier)


def test_energy_classifier_splits_silence_from_tone():
    clf = EnergyFrameClassifier(threshold=0.05)
    silence = AudioFrame(samples=np.zeros(512, dtype=np.float32))
    t = np.arange(512, dtype=np.float32) / DEFAULT_SAMPLE_RATE
    tone = AudioFrame(samples=(0.3 * np.sin(2 * np.pi * 200 * t)).astype(np.float32))
    assert clf.is_speech(silence) is False
    assert clf.is_speech(tone) is True


# -- edge sequencing ----------------------------------------------------------


def test_silence_only_produces_no_edges():
    edges, vad = _edges_for([("silence", 20)])
    assert edges == []
    assert vad.in_speech is False


def test_single_speech_segment_produces_start_then_end():
    # silence → speech → enough trailing silence to close the segment.
    edges, vad = _edges_for(
        [("silence", 5), ("speech", 10), ("silence", 5)],
        silence_frames=3,
    )
    assert edges == ["speech_start", "speech_end"]
    assert vad.in_speech is False


def test_two_segments_produce_two_start_end_pairs():
    edges, _ = _edges_for(
        [
            ("silence", 4),
            ("speech", 8),
            ("silence", 5),  # >= silence_frames → closes segment 1
            ("speech", 8),
            ("silence", 5),  # closes segment 2
        ],
        silence_frames=3,
    )
    assert edges == ["speech_start", "speech_end", "speech_start", "speech_end"]


def test_speech_running_to_end_of_stream_leaves_segment_open():
    # No trailing silence: the start fires but no end (the stream ran out
    # mid-speech). The consumer / a final flush would close it; the VAD itself
    # never invents an end edge from missing audio.
    edges, vad = _edges_for([("silence", 4), ("speech", 10)], silence_frames=3)
    assert edges == ["speech_start"]
    assert vad.in_speech is True


def test_brief_silence_under_hangover_does_not_split_a_segment():
    # A 2-frame silence dip inside speech, with silence_end_frames=3, must NOT
    # produce an end edge — it's an intra-word pause, not a turn boundary.
    edges, vad = _edges_for(
        [("silence", 4), ("speech", 6), ("silence", 2), ("speech", 6), ("silence", 4)],
        silence_frames=3,
    )
    assert edges == ["speech_start", "speech_end"]  # one segment, not two
    assert vad.in_speech is False


def test_speech_start_debounce_ignores_a_one_frame_blip():
    # A single speech frame with speech_start_frames=2 is a blip → no start edge.
    edges, vad = _edges_for(
        [("silence", 4), ("speech", 1), ("silence", 6)],
        start_frames=2,
        silence_frames=3,
    )
    assert edges == []
    assert vad.in_speech is False


def test_speech_start_fires_after_required_consecutive_frames():
    edges, _ = _edges_for(
        [("silence", 4), ("speech", 5), ("silence", 5)],
        start_frames=3,
        silence_frames=3,
    )
    assert edges == ["speech_start", "speech_end"]


# -- driving the real TurnTakingGate (the frozen edge seam) -------------------


def test_vad_drives_turntakinggate_edges_and_gate_times_off_its_own_clock():
    from jarvis.clock import ManualClock

    clock = ManualClock()
    gate = TurnTakingGate(now=clock.now, settle_seconds=0.6, politeness_gap_seconds=2.0)
    # 512 samples / 16 kHz = 32 ms per frame; advance the clock per frame so the
    # gate's silence measurement reflects real elapsed time. The VAD emits edges;
    # the GATE stamps them from this clock — the VAD passes no timestamps.
    frame_dt = 512 / DEFAULT_SAMPLE_RATE

    src = FakeAudioSource.from_pattern(
        [("silence", 3), ("speech", 10), ("silence", 80)],  # 80 frames ≈ 2.56 s of silence
        amplitude=SPEECH_AMP,
    )
    seen: list[str] = []
    vad = SileroVad(
        classifier=EnergyFrameClassifier(threshold=0.05),
        gate=gate,
        on_edge=seen.append,
        silence_end_frames=3,
    )
    for frame in src.frames():
        vad.process_frame(frame)
        clock.advance(frame_dt)

    # The VAD drove a start then an end onto the gate.
    assert seen == ["speech_start", "speech_end"]
    # After ~2.5 s of post-segment silence the gate's predicates read correctly —
    # proving the VAD's on_speech_end armed the gate's clock-based timing.
    assert gate.settled() is True
    assert gate.politeness_gap_elapsed() is True
    assert gate.speech_resumed() is False


def test_resumed_speech_after_gap_latches_gate_abort():
    from jarvis.clock import ManualClock

    clock = ManualClock()
    gate = TurnTakingGate(now=clock.now, settle_seconds=0.6, politeness_gap_seconds=2.0)
    frame_dt = 512 / DEFAULT_SAMPLE_RATE

    # speech → long silence (gap opens) → speech resumes → the gate must latch the
    # abort (speech_resumed) when the VAD fires the second on_speech_start.
    src = FakeAudioSource.from_pattern(
        [("silence", 2), ("speech", 8), ("silence", 70), ("speech", 8), ("silence", 5)],
        amplitude=SPEECH_AMP,
    )
    seen: list[str] = []
    vad = SileroVad(
        classifier=EnergyFrameClassifier(threshold=0.05),
        gate=gate,
        on_edge=seen.append,
        silence_end_frames=3,
    )
    resumed_after_gap = False
    for frame in src.frames():
        vad.process_frame(frame)
        clock.advance(frame_dt)
        if seen[-1:] == ["speech_start"] and len(seen) >= 3:
            # The second speech_start (a resumption after the gap) latched the abort.
            resumed_after_gap = gate.speech_resumed()
    assert seen == ["speech_start", "speech_end", "speech_start", "speech_end"]
    assert resumed_after_gap is True


# -- guards -------------------------------------------------------------------


def test_vad_rejects_bad_debounce_params():
    with pytest.raises(ValueError, match="speech_start_frames"):
        SileroVad(classifier=EnergyFrameClassifier(), speech_start_frames=0)
    with pytest.raises(ValueError, match="silence_end_frames"):
        SileroVad(classifier=EnergyFrameClassifier(), silence_end_frames=0)


def test_silero_classifier_rejects_non_16k_rate():
    with pytest.raises(ValueError, match="16 kHz"):
        SileroFrameClassifier(sample_rate=44_100)


# -- optional live-mic check (skipped without a device / permission) ----------


def test_live_silero_vad_on_mic_optional():
    """End-to-end real check: real Silero model + real mic for a brief window.

    Skipped (not failed) when there is no input device or mic permission is not
    granted — never fabricates a result. When it runs, it just asserts the VAD
    processed real frames without error; speech content is not required (a quiet
    room is a valid outcome).
    """
    from jarvis.audio.mic import (
        MicCaptureError,
        MicPermissionError,
        NoInputDeviceError,
        SoundDeviceMicSource,
    )

    try:
        clf = SileroFrameClassifier()  # loads torch + model
    except Exception as exc:  # noqa: BLE001 - model/torch unavailable → skip
        pytest.skip(f"Silero model/torch unavailable: {exc}")

    mic = SoundDeviceMicSource()
    try:
        mic.start()
    except (MicPermissionError, NoInputDeviceError, MicCaptureError) as exc:
        pytest.skip(f"no usable mic for live VAD check: {exc}")

    import time

    edges: list[str] = []
    vad = SileroVad(classifier=clf, on_edge=edges.append)
    processed = 0
    t0 = time.monotonic()
    try:
        for frame in mic.frames():
            vad.process_frame(frame)
            processed += 1
            if time.monotonic() - t0 > 1.0:
                break
    finally:
        mic.stop()

    assert processed > 0  # we actually fed real frames through the real model
