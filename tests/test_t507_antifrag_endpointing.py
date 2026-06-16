"""T-507 — Anti-fragmentation endpointing tests.

Proves two properties of the new DEFAULT_SILENCE_END_FRAMES = 15 (~480 ms):

  1. A speech segment with a SHORT (sub-hangover) internal silence gap stays ONE
     segment — with the old 6-frame hangover it would have split into two.

  2. A GENUINE longer silence (well above the hangover) still closes the segment
     — turn-end detection is not broken.

All tests are deterministic and model-free: ``FakeAudioSource`` +
``EnergyFrameClassifier`` + ``FakeTranscriber`` (no mic, no Silero model, no
Whisper).

Acceptance criteria wired to the task (T-507):
  - Short sub-hangover pause → one utterance / one speech_start→speech_end pair.
  - Long silence → segment properly closed.
  - Hangover is configurable (the constructor arg still works).
  - Existing edge/segment/gate/pre-roll tests still pass (guarded by running the
    full suite; these tests add to the green count, not replace it).
"""

from __future__ import annotations

import numpy as np

from jarvis.audio.mic_source import MicSource
from jarvis.audio.source import DEFAULT_FRAME_SAMPLES, DEFAULT_SAMPLE_RATE, FakeAudioSource
from jarvis.audio.vad import (
    DEFAULT_SILENCE_END_FRAMES,
    EnergyFrameClassifier,
    SileroVad,
)
from jarvis.core.turn_taking_gate import TurnTakingGate

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_SPEECH_AMP = 0.3
_ENERGY_THRESHOLD = 0.05
# Use a speech_start_frames of 2 to match test_mic_source / test_t506 conventions
# (keeps synthetic patterns short and edge timing easy to reason about).
_START_FRAMES = 2
_FRAME_SECONDS = DEFAULT_FRAME_SAMPLES / DEFAULT_SAMPLE_RATE  # 0.032 s


class FakeTranscriber:
    """Records every waveform handed to it; returns canned text."""

    def __init__(self, texts: list[str] | None = None) -> None:
        self._texts = list(texts) if texts else ["hello world ok"]
        self.calls: list[np.ndarray] = []

    def transcribe(self, waveform: np.ndarray, sample_rate: int) -> str:  # noqa: ARG002
        self.calls.append(waveform)
        idx = min(len(self.calls) - 1, len(self._texts) - 1)
        return self._texts[idx]


def _vad(silence_end_frames: int = DEFAULT_SILENCE_END_FRAMES) -> SileroVad:
    return SileroVad(
        classifier=EnergyFrameClassifier(threshold=_ENERGY_THRESHOLD),
        speech_start_frames=_START_FRAMES,
        silence_end_frames=silence_end_frames,
    )


def _edges_for(
    pattern: list[tuple[str, int]],
    silence_end_frames: int = DEFAULT_SILENCE_END_FRAMES,
) -> list[str]:
    """Run a (kind, count) pattern through the VAD; return the edge sequence."""
    src = FakeAudioSource.from_pattern(pattern, amplitude=_SPEECH_AMP)
    edges: list[str] = []
    vad = SileroVad(
        classifier=EnergyFrameClassifier(threshold=_ENERGY_THRESHOLD),
        on_edge=edges.append,
        speech_start_frames=_START_FRAMES,
        silence_end_frames=silence_end_frames,
    )
    vad.run(src)
    return edges


def _utterances_for(
    pattern: list[tuple[str, int]],
    silence_end_frames: int = DEFAULT_SILENCE_END_FRAMES,
    texts: list[str] | None = None,
) -> tuple[list, FakeTranscriber]:
    """Run pattern through a MicSource; return (utterances, transcriber)."""
    source = FakeAudioSource.from_pattern(pattern, amplitude=_SPEECH_AMP)
    transcriber = FakeTranscriber(texts or ["spoken sentence"])
    mic = MicSource(
        source=source,
        transcriber=transcriber,
        vad=_vad(silence_end_frames),
        pre_roll_frames=0,  # isolate endpointing; pre-roll tested separately
    )
    utts = list(mic.utterances())
    return utts, transcriber


# ---------------------------------------------------------------------------
# 1.  Default constant is the new T-507 value
# ---------------------------------------------------------------------------


def test_default_silence_end_frames_is_15() -> None:
    """DEFAULT_SILENCE_END_FRAMES must be 15 (~480 ms) after T-507."""
    assert DEFAULT_SILENCE_END_FRAMES == 15, (
        f"Expected DEFAULT_SILENCE_END_FRAMES=15 (~480 ms) after T-507 raise, "
        f"got {DEFAULT_SILENCE_END_FRAMES}"
    )


def test_default_hangover_is_approximately_480ms() -> None:
    """15 frames × 32 ms/frame = 480 ms — confirm the math."""
    hangover_ms = DEFAULT_SILENCE_END_FRAMES * _FRAME_SECONDS * 1000
    assert abs(hangover_ms - 480) < 1, f"Expected ~480 ms hangover, got {hangover_ms:.1f} ms"


# ---------------------------------------------------------------------------
# 2.  Sub-hangover internal silence stays ONE segment
#     (the anti-fragmentation property)
# ---------------------------------------------------------------------------


def test_short_internal_silence_does_not_split_segment_at_new_hangover() -> None:
    """A pause SHORTER than the hangover must NOT split the utterance.

    Pattern: silence → speech → short_pause (< hangover) → speech → long_silence
    With DEFAULT_SILENCE_END_FRAMES=15 a 10-frame internal pause is safely inside
    the hangover.  The segment must stay ONE: one speech_start → one speech_end.
    """
    short_pause = 10  # frames — below the 15-frame hangover
    pattern = [
        ("silence", 5),
        ("speech", 8),
        ("silence", short_pause),  # intra-sentence breath pause
        ("speech", 8),
        ("silence", 20),  # genuine turn-end silence (> hangover)
    ]
    edges = _edges_for(pattern, silence_end_frames=DEFAULT_SILENCE_END_FRAMES)
    assert edges == ["speech_start", "speech_end"], (
        f"Expected one segment (start+end), got {edges!r}. "
        f"A {short_pause}-frame pause should NOT split with hangover="
        f"{DEFAULT_SILENCE_END_FRAMES} frames."
    )


def test_short_internal_silence_yields_one_utterance() -> None:
    """The whole-utterance integration test: one pair → one Utterance."""
    short_pause = 10  # frames — inside the 15-frame hangover
    pattern = [
        ("silence", 4),
        ("speech", 8),
        ("silence", short_pause),
        ("speech", 8),
        ("silence", 20),
    ]
    utts, transcriber = _utterances_for(
        pattern, silence_end_frames=DEFAULT_SILENCE_END_FRAMES, texts=["what does that equal"]
    )
    assert len(utts) == 1, (
        f"Expected 1 utterance (one whole question), got {len(utts)} "
        f"({[u.text for u in utts]}). "
        f"A {short_pause}-frame internal silence with hangover="
        f"{DEFAULT_SILENCE_END_FRAMES} must NOT fragment."
    )
    assert transcriber.calls  # ASR was called


def test_old_hangover_would_have_split_what_new_does_not() -> None:
    """Prove the regression: old 6-frame hangover DOES split, new 15-frame does NOT.

    Pattern has a 10-frame internal silence — above the old hangover (6) but
    below the new one (15).  At 6 frames it would emit two start/end pairs;
    at 15 frames it emits exactly one.
    """
    old_hangover = 6
    new_hangover = DEFAULT_SILENCE_END_FRAMES  # 15

    pause_frames = 10  # > 6, < 15

    pattern = [
        ("silence", 4),
        ("speech", 8),
        ("silence", pause_frames),
        ("speech", 8),
        ("silence", 20),
    ]

    edges_old = _edges_for(pattern, silence_end_frames=old_hangover)
    edges_new = _edges_for(pattern, silence_end_frames=new_hangover)

    # Old hangover splits (two start/end pairs).
    assert edges_old == ["speech_start", "speech_end", "speech_start", "speech_end"], (
        f"Old hangover ({old_hangover} frames) should split at a {pause_frames}-frame "
        f"pause, got {edges_old!r}"
    )

    # New hangover does not split (one start/end pair).
    assert edges_new == ["speech_start", "speech_end"], (
        f"New hangover ({new_hangover} frames) should NOT split at a {pause_frames}-frame "
        f"pause, got {edges_new!r}"
    )


# ---------------------------------------------------------------------------
# 3.  Genuine long silence STILL closes the segment
# ---------------------------------------------------------------------------


def test_long_silence_still_closes_segment() -> None:
    """A silence ABOVE the hangover must end the segment (turn-end still works).

    Pattern: speech → long_silence (> hangover). Confirms the hangover change
    did NOT break turn-end detection.
    """
    long_pause = 20  # frames — comfortably above the 15-frame hangover
    pattern = [
        ("silence", 3),
        ("speech", 8),
        ("silence", long_pause),
    ]
    edges = _edges_for(pattern, silence_end_frames=DEFAULT_SILENCE_END_FRAMES)
    assert "speech_end" in edges, (
        f"A {long_pause}-frame silence must close the segment (speech_end edge), got {edges!r}"
    )
    assert edges == ["speech_start", "speech_end"], f"Expected one clean segment, got {edges!r}"


def test_long_silence_yields_utterance() -> None:
    """Long silence closes the segment and produces an Utterance."""
    pattern = [
        ("silence", 3),
        ("speech", 8),
        ("silence", 25),  # > 15-frame hangover
    ]
    utts, _ = _utterances_for(
        pattern, silence_end_frames=DEFAULT_SILENCE_END_FRAMES, texts=["what is seven times four"]
    )
    assert len(utts) == 1


def test_two_genuine_turn_ends_produce_two_segments() -> None:
    """Two sentences separated by a long (genuine) silence → two utterances.

    Both silences are well above the hangover, so both are real turn ends.
    """
    long_gap = 20  # > 15-frame hangover
    pattern = [
        ("silence", 3),
        ("speech", 8),
        ("silence", long_gap),  # genuine turn end
        ("speech", 8),
        ("silence", long_gap),  # genuine turn end
    ]
    edges = _edges_for(pattern, silence_end_frames=DEFAULT_SILENCE_END_FRAMES)
    assert edges == ["speech_start", "speech_end", "speech_start", "speech_end"]

    utts, _ = _utterances_for(
        pattern,
        silence_end_frames=DEFAULT_SILENCE_END_FRAMES,
        texts=["first sentence", "second sentence"],
    )
    assert len(utts) == 2
    assert utts[0].ts < utts[1].ts


# ---------------------------------------------------------------------------
# 4.  Boundary: exactly at the hangover threshold
# ---------------------------------------------------------------------------


def test_silence_exactly_at_hangover_closes_segment() -> None:
    """A silence of exactly DEFAULT_SILENCE_END_FRAMES frames closes the segment.

    The VAD fires the speech_end edge after silence_run >= silence_end_frames,
    so silence_run == silence_end_frames (>=) → closes.
    """
    pattern = [
        ("silence", 3),
        ("speech", 8),
        ("silence", DEFAULT_SILENCE_END_FRAMES),  # exactly at threshold
    ]
    edges = _edges_for(pattern, silence_end_frames=DEFAULT_SILENCE_END_FRAMES)
    assert "speech_end" in edges


def test_silence_one_below_hangover_does_not_close() -> None:
    """A silence of exactly hangover-1 frames does NOT close the segment."""
    pattern = [
        ("silence", 3),
        ("speech", 8),
        ("silence", DEFAULT_SILENCE_END_FRAMES - 1),  # one below threshold
        # stream ends → segment left open (never gets an end edge)
    ]
    edges = _edges_for(pattern, silence_end_frames=DEFAULT_SILENCE_END_FRAMES)
    # The start fires; the end does NOT.
    assert "speech_start" in edges
    assert "speech_end" not in edges


# ---------------------------------------------------------------------------
# 5.  Configurability: custom hangover value still works
# ---------------------------------------------------------------------------


def test_custom_hangover_overrides_default() -> None:
    """A caller can still override silence_end_frames via the SileroVad constructor.

    Pattern:  silence → speech × 8 → silence × 4 (> custom=3, < default=15)
              → speech × 8 → silence × 20 (closes both with enough trailing silence).

    With custom_hangover=3: the 4-frame internal silence is above 3 → splits → 2 pairs.
    With default_hangover=15: the 4-frame internal silence is below 15 → absorbed → 1 pair.
    The trailing silence of 20 is above both thresholds, so it closes the open segment
    in both cases (guaranteeing speech_end fires in both).
    """
    custom_hangover = 3
    pattern = [
        ("silence", 3),
        ("speech", 8),
        ("silence", 4),  # > custom(3) but < default(15)
        ("speech", 8),
        ("silence", 20),  # > default(15) — closes the segment in both cases
    ]
    edges_custom = _edges_for(pattern, silence_end_frames=custom_hangover)
    edges_default = _edges_for(pattern, silence_end_frames=DEFAULT_SILENCE_END_FRAMES)

    assert edges_custom == ["speech_start", "speech_end", "speech_start", "speech_end"], (
        "Custom hangover=3 must split at a 4-frame pause."
    )
    assert edges_default == ["speech_start", "speech_end"], (
        "Default hangover=15 must NOT split at a 4-frame pause."
    )


# ---------------------------------------------------------------------------
# 6.  Gate integration: speech_end edge drives the gate with new hangover
# ---------------------------------------------------------------------------


def test_vad_drives_gate_speech_end_with_new_hangover() -> None:
    """The gate receives the speech_end edge even with the new 15-frame hangover.

    Drives a real TurnTakingGate through a simulated clock to confirm that
    the enlarged hangover still delivers the expected gate predicates.
    """
    from jarvis.clock import ManualClock

    clock = ManualClock()
    gate = TurnTakingGate(now=clock.now, settle_seconds=0.1, politeness_gap_seconds=2.0)

    src = FakeAudioSource.from_pattern(
        [
            ("silence", 3),
            ("speech", 10),
            ("silence", 80),  # 80 × 32 ms = 2.56 s — well past the politeness gap
        ],
        amplitude=_SPEECH_AMP,
    )

    edges: list[str] = []
    vad = SileroVad(
        classifier=EnergyFrameClassifier(threshold=_ENERGY_THRESHOLD),
        gate=gate,
        on_edge=edges.append,
        speech_start_frames=_START_FRAMES,
        silence_end_frames=DEFAULT_SILENCE_END_FRAMES,
    )

    for frame in src.frames():
        vad.process_frame(frame)
        clock.advance(_FRAME_SECONDS)

    assert "speech_start" in edges
    assert "speech_end" in edges

    # After 2.56 s of post-segment silence, the gate should be settled and
    # the politeness gap should have elapsed.
    assert gate.settled() is True
    assert gate.politeness_gap_elapsed() is True
    assert gate.speech_resumed() is False


# ---------------------------------------------------------------------------
# 7.  Silence-only input: no edges, no utterances
# ---------------------------------------------------------------------------


def test_silence_only_no_edges_no_utterances_with_new_hangover() -> None:
    """Pure silence produces no edges and no utterances regardless of hangover."""
    edges = _edges_for([("silence", 30)], silence_end_frames=DEFAULT_SILENCE_END_FRAMES)
    assert edges == []

    utts, _ = _utterances_for([("silence", 30)], silence_end_frames=DEFAULT_SILENCE_END_FRAMES)
    assert utts == []


# ---------------------------------------------------------------------------
# 8.  Old test-patterns still work (regression guards at the new default)
# ---------------------------------------------------------------------------


def test_existing_test_vad_brief_silence_still_does_not_split() -> None:
    """The original test_vad brief-silence test pattern still passes at new default.

    test_vad.py uses silence_frames=3 explicitly in _edges_for.  The new default
    is 15, but the VAD is configurable — the explicit pass-in of 3 is unchanged.
    This guard confirms the VAD constructor still honours an override.
    """
    # Exactly the pattern from test_vad.test_brief_silence_under_hangover_does_not_split:
    # 2-frame silence inside speech with silence_end_frames=3 → one segment.
    edges = _edges_for(
        [("silence", 4), ("speech", 6), ("silence", 2), ("speech", 6), ("silence", 4)],
        silence_end_frames=3,
    )
    assert edges == ["speech_start", "speech_end"]
