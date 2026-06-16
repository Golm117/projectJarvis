"""Tests for the AudioSource abstraction, RingBuffer, and FakeAudioSource (T-102).

These drive the capture/buffer logic from **synthetic frames via the fake
``AudioSource``** — deterministic, no real microphone, no PortAudio. They prove:

* ``AudioFrame`` shape / rate / duration / energy reads,
* ``RingBuffer`` wrap-around eviction, overflow accounting, and **bounded
  memory** (length never exceeds capacity no matter how much is pushed),
* ``FakeAudioSource`` yields a constant frame geometry and the right counts from
  the silence / tone / pattern builders,
* the real ``SoundDeviceMicSource`` satisfies the ``AudioSource`` Protocol
  without touching hardware (no ``start()``).
"""

from __future__ import annotations

import numpy as np
import pytest

from jarvis.audio import (
    DEFAULT_FRAME_SAMPLES,
    DEFAULT_SAMPLE_RATE,
    AudioFrame,
    AudioSource,
    FakeAudioSource,
    RingBuffer,
)

# -- AudioFrame ---------------------------------------------------------------


def test_frame_reports_shape_rate_and_duration():
    frame = AudioFrame(samples=np.zeros(512, dtype=np.float32), sample_rate=16_000)
    assert frame.num_samples == 512
    assert frame.sample_rate == 16_000
    assert frame.duration == pytest.approx(512 / 16_000)


def test_frame_rms_is_zero_for_silence_and_positive_for_a_tone():
    silence = AudioFrame(samples=np.zeros(512, dtype=np.float32))
    assert silence.rms == 0.0

    t = np.arange(512, dtype=np.float32) / DEFAULT_SAMPLE_RATE
    tone = AudioFrame(samples=(0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32))
    assert tone.rms > 0.0
    # A 0.5-amplitude sine has RMS ~0.5/sqrt(2) ~= 0.354.
    assert tone.rms == pytest.approx(0.354, abs=0.02)


def test_empty_frame_has_zero_rms():
    assert AudioFrame(samples=np.zeros(0, dtype=np.float32)).rms == 0.0


# -- RingBuffer ---------------------------------------------------------------


def _frame(value: float = 0.0, n: int = DEFAULT_FRAME_SAMPLES) -> AudioFrame:
    return AudioFrame(samples=np.full(n, value, dtype=np.float32))


def test_ring_buffer_rejects_bad_capacity():
    with pytest.raises(ValueError, match="capacity"):
        RingBuffer(0)


def test_ring_buffer_fifo_pop_order():
    rb = RingBuffer(4)
    rb.push(_frame(0.1))
    rb.push(_frame(0.2))
    assert len(rb) == 2
    assert rb.pop().samples[0] == pytest.approx(0.1)
    assert rb.pop().samples[0] == pytest.approx(0.2)
    assert rb.pop() is None  # empty


def test_ring_buffer_overwrites_oldest_when_full_and_counts_overflow():
    rb = RingBuffer(3)
    for v in (0.1, 0.2, 0.3):
        rb.push(_frame(v))
    assert len(rb) == 3
    assert rb.overflows == 0
    # Push a 4th into a full buffer: oldest (0.1) is evicted, one overflow.
    rb.push(_frame(0.4))
    assert len(rb) == 3
    assert rb.overflows == 1
    remaining = [f.samples[0] for f in rb.drain()]
    assert remaining == pytest.approx([0.2, 0.3, 0.4])


def test_ring_buffer_memory_is_bounded_under_heavy_push():
    # Push WAY more than capacity; length must never exceed capacity, and the
    # overflow count must equal the excess. This is the no-unbounded-growth proof.
    rb = RingBuffer(8)
    total = 1000
    for i in range(total):
        rb.push(_frame(float(i)))
        assert len(rb) <= rb.capacity  # invariant holds at every step
    assert len(rb) == rb.capacity
    assert rb.pushed == total
    assert rb.overflows == total - rb.capacity
    # The retained frames are the most recent `capacity` ones.
    retained = [f.samples[0] for f in rb.drain()]
    assert retained == pytest.approx([float(i) for i in range(total - rb.capacity, total)])


def test_ring_buffer_drain_empties_it():
    rb = RingBuffer(4)
    rb.push(_frame(0.1))
    rb.push(_frame(0.2))
    assert len(rb.drain()) == 2
    assert len(rb) == 0
    assert rb.drain() == []


# -- FakeAudioSource ----------------------------------------------------------


def test_fake_source_satisfies_audiosource_protocol():
    src = FakeAudioSource.silence(3)
    assert isinstance(src, AudioSource)


def test_fake_source_reports_constant_geometry():
    src = FakeAudioSource.silence(5)
    assert src.sample_rate == DEFAULT_SAMPLE_RATE
    assert src.frame_samples == DEFAULT_FRAME_SAMPLES
    frames = list(src.frames())
    assert len(frames) == 5
    for f in frames:
        assert f.num_samples == DEFAULT_FRAME_SAMPLES
        assert f.sample_rate == DEFAULT_SAMPLE_RATE


def test_fake_source_silence_frames_are_zero_energy():
    frames = list(FakeAudioSource.silence(4).frames())
    assert all(f.rms == 0.0 for f in frames)


def test_fake_source_tone_frames_carry_energy():
    frames = list(FakeAudioSource.tone(4, amplitude=0.3).frames())
    assert len(frames) == 4
    assert all(f.rms > 0.1 for f in frames)


def test_fake_source_from_pattern_concatenates_runs_in_order():
    src = FakeAudioSource.from_pattern([("silence", 3), ("speech", 5), ("silence", 2)])
    frames = list(src.frames())
    assert len(frames) == 10
    energies = [f.rms for f in frames]
    assert energies[0:3] == [0.0, 0.0, 0.0]  # leading silence
    assert all(e > 0.1 for e in energies[3:8])  # speech run
    assert energies[8:10] == [0.0, 0.0]  # trailing silence


def test_fake_source_pattern_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown pattern kind"):
        FakeAudioSource.from_pattern([("noise", 3)])


def test_fake_source_is_reusable_via_fresh_iterators():
    src = FakeAudioSource.silence(3)
    assert len(list(src.frames())) == 3
    # Calling frames() again yields a fresh iterator over the same scripted frames.
    assert len(list(src.frames())) == 3


# -- Real mic source: Protocol conformance only (no hardware) -----------------


def test_sounddevice_mic_source_conforms_without_starting():
    # Import here so a machine without PortAudio still collects the rest of the
    # module; constructing the object must NOT open the device.
    from jarvis.audio.mic import SoundDeviceMicSource

    mic = SoundDeviceMicSource()
    assert isinstance(mic, AudioSource)
    assert mic.sample_rate == DEFAULT_SAMPLE_RATE
    assert mic.frame_samples == DEFAULT_FRAME_SAMPLES
    assert mic.overflows == 0


def test_mic_frames_before_start_raises():
    from jarvis.audio.mic import MicCaptureError, SoundDeviceMicSource

    mic = SoundDeviceMicSource()
    with pytest.raises(MicCaptureError, match="start"):
        next(mic.frames())


def test_mic_open_error_classification():
    from jarvis.audio.mic import (
        MicPermissionError,
        NoInputDeviceError,
        SoundDeviceMicSource,
    )

    classify = SoundDeviceMicSource._classify_open_error
    assert isinstance(classify(Exception("Permission denied")), MicPermissionError)
    assert isinstance(classify(Exception("not authorized to capture")), MicPermissionError)
    assert isinstance(classify(Exception("No default input device")), NoInputDeviceError)
    # An unrecognized error stays a generic capture error.
    from jarvis.audio.mic import MicCaptureError

    err = classify(Exception("some other portaudio failure"))
    assert isinstance(err, MicCaptureError)
    assert not isinstance(err, (MicPermissionError, NoInputDeviceError))


# -- Real mic source: thread-safe, idempotent teardown ------------------------
# Regression for the `--live` run: the countdown timer calls stop() on its own
# thread while the main thread also tears down, so both passed the not-None
# check and one nulled _stream mid-flight -> the other hit `NoneType.close()`
# (the AttributeError), and the double stop/close produced the PaMacCore -50.


class _FakeStream:
    """Stand-in for a PortAudio stream that records stop/close calls."""

    def __init__(self, raise_on_teardown: bool = False) -> None:
        self.stop_calls = 0
        self.close_calls = 0
        self._raise = raise_on_teardown

    def stop(self) -> None:
        self.stop_calls += 1
        if self._raise:
            raise RuntimeError("PortAudio err -50 on stop")

    def close(self) -> None:
        self.close_calls += 1
        if self._raise:
            raise RuntimeError("PortAudio err -50 on close")


def _mic_with_fake_stream(stream: _FakeStream):
    from jarvis.audio.mic import SoundDeviceMicSource

    mic = SoundDeviceMicSource()
    mic._stream = stream  # inject a fake so no real PortAudio stream is opened
    mic._running = True
    return mic


def test_stop_is_idempotent_and_closes_once():
    stream = _FakeStream()
    mic = _mic_with_fake_stream(stream)
    mic.stop()
    mic.stop()  # second call must not touch an already-closed stream
    assert stream.stop_calls == 1
    assert stream.close_calls == 1
    assert mic._stream is None
    assert mic._running is False


def test_stop_suppresses_teardown_errors():
    # A PortAudio error during teardown (the benign CoreAudio -50) must not
    # propagate — we are shutting down and nothing downstream cares.
    mic = _mic_with_fake_stream(_FakeStream(raise_on_teardown=True))
    mic.stop()  # must not raise
    assert mic._stream is None


def test_concurrent_stop_calls_close_exactly_once_without_error():
    import threading

    stream = _FakeStream()
    mic = _mic_with_fake_stream(stream)

    errors: list[Exception] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()  # maximise overlap of the concurrent stop() calls
        try:
            mic.stop()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []  # no AttributeError, no double-close error
    assert stream.stop_calls == 1  # exactly one caller owned the teardown
    assert stream.close_calls == 1
    assert mic._stream is None
