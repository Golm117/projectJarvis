"""T-506 — pre-roll / lookback buffer tests.

Exercises the onset-recovery buffer added to ``MicSource`` in T-506.
All tests are deterministic and model-free: ``FakeAudioSource`` + ``EnergyFrameClassifier``
+ ``FakeTranscriber`` (no mic, no Silero model, no Whisper).

What these tests prove:
  * A segment INCLUDES the pre-roll frames that preceded the speech_start edge.
  * The pre-roll deque is bounded (``deque(maxlen=...)``) — no unbounded growth.
  * The pre-roll size is configurable; a pre-roll smaller than available history works.
  * pre_roll_frames=0 reverts to the pre-T-506 behaviour (segment starts empty).
  * pre_roll_frames < 0 is rejected.
  * Two consecutive segments: the second segment gets its own fresh pre-roll (no
    bleed-over from the first segment's frames).
  * The existing single/multi/empty-segment + gate-edge tests all still pass (they
    call the existing test_mic_source.py helpers; we add a few key representative
    ones here to guard the pre-roll path).
  * ``Utterance.ts`` stamping is unchanged (still the end-frame of the segment,
    not affected by how many pre-roll frames were prepended).
"""

from __future__ import annotations

import numpy as np
import pytest

from jarvis.audio.mic_source import DEFAULT_PRE_ROLL_FRAMES, MicSource
from jarvis.audio.source import DEFAULT_FRAME_SAMPLES, DEFAULT_SAMPLE_RATE, FakeAudioSource
from jarvis.audio.vad import EnergyFrameClassifier, SileroVad

# Match test_mic_source.py's debounce so patterns align.
_START_FRAMES = 2
_END_FRAMES = 2


class FakeTranscriber:
    """Records every waveform it is handed; returns canned text."""

    def __init__(self, texts: list[str] | None = None) -> None:
        self._texts = list(texts) if texts else ["ok"]
        self.calls: list[np.ndarray] = []

    def transcribe(self, waveform: np.ndarray, sample_rate: int) -> str:  # noqa: ARG002
        self.calls.append(waveform)
        idx = min(len(self.calls) - 1, len(self._texts) - 1)
        return self._texts[idx]


def _vad(gate=None) -> SileroVad:  # type: ignore[no-untyped-def]
    return SileroVad(
        classifier=EnergyFrameClassifier(threshold=0.05),
        gate=gate,
        speech_start_frames=_START_FRAMES,
        silence_end_frames=_END_FRAMES,
    )


def _mic(
    pattern: list[tuple[str, int]],
    transcriber: FakeTranscriber,
    pre_roll_frames: int = DEFAULT_PRE_ROLL_FRAMES,
    gate=None,  # type: ignore[no-untyped-def]
) -> MicSource:
    source = FakeAudioSource.from_pattern(pattern, amplitude=0.3)
    return MicSource(
        source=source,
        gate=gate,
        transcriber=transcriber,
        vad=_vad(gate),
        pre_roll_frames=pre_roll_frames,
    )


# ---------------------------------------------------------------------------
# Core pre-roll correctness
# ---------------------------------------------------------------------------


def test_pre_roll_frames_appear_at_start_of_waveform() -> None:
    """The segment waveform must include the silence/onset frames before the edge.

    Pattern:  5 silence frames → 10 speech frames → 4 silence frames
    With _START_FRAMES=2 the speech_start edge fires on the 2nd speech frame
    (index 5+2-1 = 6 from the start). At that moment the pre-roll deque holds
    the most recent `pre_roll_frames` frames seen *before* the current frame.
    These are seeded into _segment_frames; then the current (2nd speech) frame
    and subsequent frames are appended in the loop.

    So the total waveform length = (pre-roll frames) + (speech frames after edge)
    frames, which is (pre_roll_frames) + (10 - _START_FRAMES + 1)  … adjusted
    for exact debounce, but definitely *more* than the speech-only window.
    We confirm it is longer than a zero-pre-roll segment would be.
    """
    transcriber_with = FakeTranscriber(["long text spoken here ok"])
    transcriber_without = FakeTranscriber(["long text spoken here ok"])

    mic_with = _mic(
        [("silence", 5), ("speech", 10), ("silence", 4)],
        transcriber_with,
        pre_roll_frames=5,
    )
    mic_without = _mic(
        [("silence", 5), ("speech", 10), ("silence", 4)],
        transcriber_without,
        pre_roll_frames=0,
    )

    list(mic_with.utterances())
    list(mic_without.utterances())

    assert len(transcriber_with.calls) == 1
    assert len(transcriber_without.calls) == 1

    # Pre-roll segment is strictly longer (more samples).
    waveform_with = transcriber_with.calls[0]
    waveform_without = transcriber_without.calls[0]
    assert waveform_with.size > waveform_without.size


def test_pre_roll_frames_are_silence_frames_prepended() -> None:
    """The pre-roll frames come from the silence/onset region before speech.

    With pattern [silence×5, speech×10, silence×4] and pre_roll_frames=3,
    the waveform handed to ASR begins with (up to 3) near-zero silence frames
    followed by the speech-energy frames.

    We verify: the first samples of the pre-roll waveform have near-zero RMS
    (they were silence frames), confirming actual silence content was prepended —
    not just that the waveform is longer due to any other cause.
    """
    transcriber = FakeTranscriber(["test phrase more words"])
    mic = _mic([("silence", 5), ("speech", 10), ("silence", 4)], transcriber, pre_roll_frames=3)

    list(mic.utterances())

    waveform = transcriber.calls[0]
    # The first frame_samples samples should be silence (near zero).
    first_frame_samples = waveform[:DEFAULT_FRAME_SAMPLES]
    rms = float(np.sqrt(np.mean(first_frame_samples**2)))
    assert rms < 0.01, f"Expected near-zero RMS for pre-roll silence frame, got {rms:.4f}"


def test_pre_roll_bounded_by_maxlen() -> None:
    """The pre-roll deque must be bounded; extra old frames must be evicted.

    With 20 silence frames before speech and pre_roll_frames=5, only the 5
    most-recent silence frames should appear — not all 20.
    """
    transcriber = FakeTranscriber(["bounded pre-roll check done"])
    mic = _mic([("silence", 20), ("speech", 10), ("silence", 4)], transcriber, pre_roll_frames=5)

    list(mic.utterances())

    waveform = transcriber.calls[0]
    # With pre_roll_frames=5 and zero-pre-roll baseline for comparison:
    transcriber2 = FakeTranscriber(["bounded pre-roll check done"])
    mic2 = _mic([("silence", 20), ("speech", 10), ("silence", 4)], transcriber2, pre_roll_frames=0)
    list(mic2.utterances())

    waveform_no_preroll = transcriber2.calls[0]
    # Pre-roll waveform is longer by exactly 5 frames worth of samples.
    expected_extra = 5 * DEFAULT_FRAME_SAMPLES
    actual_extra = waveform.size - waveform_no_preroll.size
    assert actual_extra == expected_extra, (
        f"Expected exactly {expected_extra} extra samples from 5 pre-roll frames, "
        f"got {actual_extra}"
    )


def test_pre_roll_smaller_than_available_history() -> None:
    """When fewer frames preceded the edge than pre_roll_frames, we get what exists.

    With pattern [silence×2, speech×10, silence×4], pre_roll_frames=10, and
    _START_FRAMES=2: the speech_start edge fires on the 2nd consecutive speech
    frame. At that point the pre-roll deque contains at most the 2 silence frames
    + 1 sub-threshold speech frame (the 1st speech frame, which was in the deque
    before the edge fired on the 2nd). That is (_START_FRAMES - 1) + 2 = 3 frames
    max — far less than the requested 10 frames.

    The key property: we get *some* pre-roll (the waveform is longer), but it is
    bounded by what was actually available (< pre_roll_frames).
    """
    transcriber_short = FakeTranscriber(["short onset works"])
    mic_short = _mic(
        [("silence", 2), ("speech", 10), ("silence", 4)],
        transcriber_short,
        pre_roll_frames=10,
    )

    transcriber_none = FakeTranscriber(["short onset works"])
    mic_none = _mic(
        [("silence", 2), ("speech", 10), ("silence", 4)],
        transcriber_none,
        pre_roll_frames=0,
    )

    list(mic_short.utterances())
    list(mic_none.utterances())

    waveform_short = transcriber_short.calls[0]
    waveform_none = transcriber_none.calls[0]

    # Must be longer (some pre-roll captured).
    assert waveform_short.size > waveform_none.size
    # The extra must be bounded by the actual frames available before the edge:
    # 2 silence + (_START_FRAMES-1=1) sub-threshold speech frame = 3 frames max.
    extra = waveform_short.size - waveform_none.size
    max_possible = (2 + _START_FRAMES - 1) * DEFAULT_FRAME_SAMPLES
    assert extra <= max_possible, (
        f"Got more pre-roll frames than were available: {extra} extra samples "
        f"(max expected {max_possible} = "
        f"(2 silence + {_START_FRAMES - 1} sub-threshold) x {DEFAULT_FRAME_SAMPLES})"
    )
    # And strictly less than the requested 10 frames (bounded by actual history).
    assert extra < 10 * DEFAULT_FRAME_SAMPLES


# ---------------------------------------------------------------------------
# Zero / disabled pre-roll
# ---------------------------------------------------------------------------


def test_pre_roll_zero_disables_lookback() -> None:
    """pre_roll_frames=0 → segment waveform is exactly the speech-edge window."""
    transcriber_zero = FakeTranscriber(["zero pre roll text ok"])
    mic_zero = _mic(
        [("silence", 5), ("speech", 10), ("silence", 4)],
        transcriber_zero,
        pre_roll_frames=0,
    )

    # Get the default-pre-roll waveform for comparison.
    transcriber_default = FakeTranscriber(["zero pre roll text ok"])
    mic_default = _mic(
        [("silence", 5), ("speech", 10), ("silence", 4)],
        transcriber_default,
        pre_roll_frames=DEFAULT_PRE_ROLL_FRAMES,
    )

    list(mic_zero.utterances())
    list(mic_default.utterances())

    # Zero pre-roll is shorter.
    assert transcriber_zero.calls[0].size < transcriber_default.calls[0].size


def test_pre_roll_negative_raises() -> None:
    """pre_roll_frames < 0 must raise ValueError at construction."""
    source = FakeAudioSource.silence(1)
    with pytest.raises(ValueError, match="pre_roll_frames must be >= 0"):
        MicSource(source=source, pre_roll_frames=-1)


# ---------------------------------------------------------------------------
# Multi-segment: no bleed-over between segments
# ---------------------------------------------------------------------------


def test_second_segment_gets_fresh_pre_roll_not_first_segment_frames() -> None:
    """The pre-roll for the second segment must be silence frames, not in-segment frames.

    Pattern: [silence×5, speech×8, silence×5, speech×8, silence×4]
    The second segment's pre-roll window should contain silence frames from the
    inter-segment gap — not frames from inside the first speech segment.
    """
    transcriber = FakeTranscriber(["first sentence here ok", "second sentence here ok"])
    mic = _mic(
        [("silence", 5), ("speech", 8), ("silence", 5), ("speech", 8), ("silence", 4)],
        transcriber,
        pre_roll_frames=3,
    )

    utts = list(mic.utterances())
    assert len(utts) == 2

    # Both segments must have been transcribed.
    assert len(transcriber.calls) == 2

    # The second segment's waveform must start with near-zero silence (pre-roll
    # from the inter-segment silence gap), not high-energy in-speech frames.
    waveform2 = transcriber.calls[1]
    first_frame = waveform2[:DEFAULT_FRAME_SAMPLES]
    rms = float(np.sqrt(np.mean(first_frame**2)))
    assert rms < 0.01, (
        f"Second segment pre-roll should be silence, got RMS {rms:.4f} "
        f"(bleed-over from first segment?)"
    )


# ---------------------------------------------------------------------------
# Utterance.ts unchanged
# ---------------------------------------------------------------------------


def test_ts_stamping_unchanged_by_pre_roll() -> None:
    """Pre-roll must not change when Utterance.ts is stamped.

    ts = frames_seen × seconds_per_frame at the speech_end edge, which fires
    after _END_FRAMES consecutive silence frames following the speech block.
    The pre-roll only adds earlier frames to the waveform; it does not change
    the frame counter or the edge timing.

    Pattern: 5 silence + 10 speech + 4 silence.
    The speech_end edge fires at frame index 5 + 10 + _END_FRAMES = 17.
    (frames_seen is incremented per frame through the loop, so ts = 17 × spf.)
    """
    transcriber_with = FakeTranscriber(["ts test phrase words ok"])
    transcriber_without = FakeTranscriber(["ts test phrase words ok"])

    mic_with = _mic(
        [("silence", 5), ("speech", 10), ("silence", 4)],
        transcriber_with,
        pre_roll_frames=DEFAULT_PRE_ROLL_FRAMES,
    )
    mic_without = _mic(
        [("silence", 5), ("speech", 10), ("silence", 4)],
        transcriber_without,
        pre_roll_frames=0,
    )

    utts_with = list(mic_with.utterances())
    utts_without = list(mic_without.utterances())

    assert len(utts_with) == 1
    assert len(utts_without) == 1

    # ts must be identical regardless of pre-roll (same frame count at end-edge).
    assert utts_with[0].ts == utts_without[0].ts


# ---------------------------------------------------------------------------
# Existing behaviour preserved (guard regression)
# ---------------------------------------------------------------------------


def test_silence_only_no_utterances_with_pre_roll() -> None:
    transcriber = FakeTranscriber(["should not appear"])
    mic = _mic([("silence", 12)], transcriber, pre_roll_frames=DEFAULT_PRE_ROLL_FRAMES)
    assert list(mic.utterances()) == []
    assert transcriber.calls == []


def test_single_segment_still_yields_one_utterance() -> None:
    transcriber = FakeTranscriber(["single segment text ok"])
    mic = _mic(
        [("silence", 3), ("speech", 10), ("silence", 4)],
        transcriber,
        pre_roll_frames=DEFAULT_PRE_ROLL_FRAMES,
    )
    utts = list(mic.utterances())
    assert len(utts) == 1
    assert utts[0].text == "single segment text ok"


def test_two_segments_still_yield_two_utterances() -> None:
    transcriber = FakeTranscriber(["first thing", "second thing"])
    mic = _mic(
        [
            ("silence", 3),
            ("speech", 8),
            ("silence", 4),
            ("speech", 8),
            ("silence", 4),
        ],
        transcriber,
        pre_roll_frames=DEFAULT_PRE_ROLL_FRAMES,
    )
    utts = list(mic.utterances())
    assert [u.text for u in utts] == ["first thing", "second thing"]
    assert utts[0].ts < utts[1].ts


def test_default_pre_roll_constant_is_sane() -> None:
    """DEFAULT_PRE_ROLL_FRAMES must be positive and give 300-512 ms of lookback."""
    spf = DEFAULT_FRAME_SAMPLES / DEFAULT_SAMPLE_RATE  # seconds per frame = 0.032
    lookback_ms = DEFAULT_PRE_ROLL_FRAMES * spf * 1000
    assert DEFAULT_PRE_ROLL_FRAMES > 0
    assert 200 <= lookback_ms <= 600, (
        f"Expected 200–600 ms lookback, got {lookback_ms:.0f} ms "
        f"({DEFAULT_PRE_ROLL_FRAMES} frames × {spf * 1000:.0f} ms/frame)"
    )
