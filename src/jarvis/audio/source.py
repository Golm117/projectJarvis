"""``AudioSource`` abstraction, ``AudioFrame``, ``RingBuffer``, ``FakeAudioSource`` (T-102).

The seam between *real microphone hardware* and everything downstream (VAD in
T-103, ASR in T-104). Defining it here means the Silero VAD segmenter and the
whole test suite consume **frames**, never a PortAudio stream — so nothing below
this line depends on a working mic, and the buffer/segmentation logic is driven
deterministically from synthetic frames.

## What an AudioSource yields

A continuous stream of fixed-size ``AudioFrame`` chunks at a fixed sample rate,
**16 kHz mono float32** by default — the format Silero VAD wants (it operates on
512-sample / 32 ms windows at 16 kHz). The producer (real or fake) guarantees a
constant frame shape and rate; the consumer (the VAD) can therefore reason about
time purely from the frame count (``frame_samples / sample_rate`` seconds per
frame) without reading any clock.

## The two implementations

* ``SoundDeviceMicSource`` (``jarvis.audio.mic``) — the real always-on capture
  loop over ``sounddevice`` (PortAudio). Continuous, ring-buffered, bounded
  memory, no dropped frames. This is the *only* place real audio I/O happens.
* ``FakeAudioSource`` (here) — replays a scripted list of frames (or
  silence/tone generators built by the helpers below). Deterministic, no
  hardware, no threads. The VAD tests and the ring-buffer tests run on it.

## The ring buffer

``RingBuffer`` is the bounded hand-off between the PortAudio callback thread
(which *must not block* or it drops audio) and the consumer. It is a fixed-size
circular buffer of frames: when full, the oldest frame is overwritten (and
counted as an overflow) rather than growing without bound. That bounds memory for
an *always-on* process — the invariant the capture loop depends on — and the
overflow counter surfaces back-pressure honestly instead of hiding a leak.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np

# Silero VAD operates on 16 kHz mono audio; 512 samples == 32 ms per frame is the
# window size the model expects at 16 kHz. Fixed here so every producer and the
# VAD agree on one frame geometry (and one "seconds per frame" conversion).
DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_FRAME_SAMPLES = 512


@dataclass(frozen=True)
class AudioFrame:
    """One fixed-size chunk of mono PCM audio.

    Immutable so a frame can sit in the ring buffer, be handed to the VAD, and be
    concatenated for ASR without any stage mutating it.

    Fields:
        samples: a 1-D ``float32`` numpy array of length ``len(samples)`` in
            ``[-1.0, 1.0]`` (the normalized PCM convention both sounddevice and
            Silero use). Mono — a single channel.
        sample_rate: samples per second (16 kHz by default).

    ``duration`` (samples ÷ rate) is the only "time" a frame carries; the VAD
    derives speech/silence durations by counting frames, so the audio path never
    needs a wall clock.
    """

    samples: np.ndarray
    sample_rate: int = DEFAULT_SAMPLE_RATE

    @property
    def num_samples(self) -> int:
        """Number of PCM samples in this frame."""
        return int(self.samples.shape[0])

    @property
    def duration(self) -> float:
        """Frame length in seconds (``num_samples / sample_rate``)."""
        return self.num_samples / self.sample_rate

    @property
    def rms(self) -> float:
        """Root-mean-square amplitude — a cheap energy read (used by the fake VAD)."""
        if self.num_samples == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(self.samples, dtype=np.float64))))


@runtime_checkable
class AudioSource(Protocol):
    """A stream of fixed-size :class:`AudioFrame` chunks — the mic seam.

    Both the real ``SoundDeviceMicSource`` and the test ``FakeAudioSource``
    satisfy this. A consumer iterates ``frames()`` one ``AudioFrame`` at a time;
    whether they come from a microphone or a script is invisible to it. The
    ``sample_rate`` and ``frame_samples`` are constant for the life of the source
    so the consumer can rely on a uniform frame geometry.
    """

    @property
    def sample_rate(self) -> int:
        """Samples per second of every frame this source yields."""
        ...

    @property
    def frame_samples(self) -> int:
        """Number of samples in every frame this source yields."""
        ...

    def frames(self) -> Iterable[AudioFrame]:
        """Yield audio frames until the source is exhausted or stopped."""
        ...


class RingBuffer:
    """A bounded circular buffer of :class:`AudioFrame` — the capture hand-off.

    The PortAudio callback runs on a real-time thread that must never block; it
    ``push``-es each captured frame here and returns immediately. The consumer
    ``pop``-s frames on its own schedule. The buffer holds at most ``capacity``
    frames: a ``push`` into a full buffer overwrites the **oldest** frame and
    increments ``overflows`` — bounding memory for an always-on process instead of
    growing without limit if the consumer ever falls behind.

    Args:
        capacity: the maximum number of frames retained (``>= 1``). At 512-sample
            16 kHz frames (~32 ms each), e.g. 64 frames ≈ 2 s of audio.

    This is a plain in-memory structure; the real loop wraps the ``push`` side in
    the audio callback and the ``pop`` side in the consumer. (CPython's GIL makes
    the individual ``deque`` ops atomic, which is enough for the single
    producer / single consumer use here; if the loop ever needs cross-thread
    ordering guarantees beyond that it would add a lock — flagged in T-102 notes.)
    """

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError(f"capacity must be >= 1, got {capacity}")
        self._capacity = int(capacity)
        self._frames: deque[AudioFrame] = deque(maxlen=self._capacity)
        self._overflows = 0
        self._pushed = 0

    @property
    def capacity(self) -> int:
        """The fixed maximum number of frames retained."""
        return self._capacity

    def __len__(self) -> int:
        """Frames currently buffered (``0 <= len <= capacity``)."""
        return len(self._frames)

    @property
    def overflows(self) -> int:
        """Count of frames dropped because the buffer was full when pushed.

        Stays ``0`` when the consumer keeps up; a rising count is honest
        back-pressure (the consumer is too slow), not a silent leak.
        """
        return self._overflows

    @property
    def pushed(self) -> int:
        """Total frames ever pushed (for tests / instrumentation)."""
        return self._pushed

    def push(self, frame: AudioFrame) -> None:
        """Append a frame; if full, evict the oldest and count an overflow.

        Never blocks and never grows past ``capacity`` — safe to call from the
        real-time audio callback.
        """
        if len(self._frames) == self._capacity:
            # deque(maxlen=...) will drop the oldest on append; count it.
            self._overflows += 1
        self._frames.append(frame)
        self._pushed += 1

    def pop(self) -> AudioFrame | None:
        """Remove and return the oldest buffered frame, or ``None`` if empty."""
        if not self._frames:
            return None
        return self._frames.popleft()

    def drain(self) -> list[AudioFrame]:
        """Remove and return all buffered frames, oldest first."""
        out = list(self._frames)
        self._frames.clear()
        return out


class FakeAudioSource:
    """A deterministic :class:`AudioSource` that replays scripted frames (T-102).

    The hardware-free stand-in the VAD tests and the ring-buffer tests run on: no
    PortAudio, no threads, no real time. Construct it from an explicit list of
    frames, or from the ``silence`` / ``tone`` helpers to build energy patterns
    (silence vs. speech-energy) for the VAD.

    Args:
        frames: the frames to yield, in order.
        sample_rate: the constant sample rate reported (must match the frames).
        frame_samples: the constant frame length reported (must match the frames).

    Use the classmethod builders for the common cases:

    * ``FakeAudioSource.silence(n)`` — ``n`` zero-energy frames.
    * ``FakeAudioSource.tone(n, amplitude=...)`` — ``n`` frames of a sine tone
      (a crude "speech-energy" stand-in for the energy-threshold fake VAD).
    * ``FakeAudioSource.from_pattern([...])`` — alternate silence/speech runs from
      a list of ``(kind, count)`` pairs, e.g. ``[("silence", 5), ("speech", 10)]``.
    """

    def __init__(
        self,
        frames: Iterable[AudioFrame],
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        frame_samples: int = DEFAULT_FRAME_SAMPLES,
    ) -> None:
        self._frames = list(frames)
        self._sample_rate = int(sample_rate)
        self._frame_samples = int(frame_samples)

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def frame_samples(self) -> int:
        return self._frame_samples

    def frames(self) -> Iterator[AudioFrame]:
        """Yield each scripted frame in order, then stop."""
        yield from self._frames

    # -- builders (synthetic energy patterns for the VAD) --------------------

    @staticmethod
    def _silence_frame(
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        frame_samples: int = DEFAULT_FRAME_SAMPLES,
    ) -> AudioFrame:
        return AudioFrame(
            samples=np.zeros(frame_samples, dtype=np.float32),
            sample_rate=sample_rate,
        )

    @staticmethod
    def _tone_frame(
        amplitude: float = 0.3,
        freq: float = 220.0,
        phase: float = 0.0,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        frame_samples: int = DEFAULT_FRAME_SAMPLES,
    ) -> AudioFrame:
        t = (np.arange(frame_samples, dtype=np.float32) + phase) / sample_rate
        samples = (amplitude * np.sin(2.0 * math.pi * freq * t)).astype(np.float32)
        return AudioFrame(samples=samples, sample_rate=sample_rate)

    @classmethod
    def silence(
        cls,
        n: int,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        frame_samples: int = DEFAULT_FRAME_SAMPLES,
    ) -> FakeAudioSource:
        """A source of ``n`` zero-energy (silence) frames."""
        frames = [cls._silence_frame(sample_rate, frame_samples) for _ in range(n)]
        return cls(frames, sample_rate=sample_rate, frame_samples=frame_samples)

    @classmethod
    def tone(
        cls,
        n: int,
        amplitude: float = 0.3,
        freq: float = 220.0,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        frame_samples: int = DEFAULT_FRAME_SAMPLES,
    ) -> FakeAudioSource:
        """A source of ``n`` sine-tone (speech-energy stand-in) frames."""
        frames = [
            cls._tone_frame(amplitude, freq, 0.0, sample_rate, frame_samples) for _ in range(n)
        ]
        return cls(frames, sample_rate=sample_rate, frame_samples=frame_samples)

    @classmethod
    def from_pattern(
        cls,
        pattern: Iterable[tuple[str, int]],
        amplitude: float = 0.3,
        freq: float = 220.0,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        frame_samples: int = DEFAULT_FRAME_SAMPLES,
    ) -> FakeAudioSource:
        """Build a source from alternating silence/speech runs.

        Args:
            pattern: ``(kind, count)`` pairs where ``kind`` is ``"silence"`` or
                ``"speech"`` and ``count`` is the number of frames. The frames are
                concatenated in order — e.g.
                ``[("silence", 5), ("speech", 10), ("silence", 5)]`` is a clean
                speech segment bracketed by silence.
        """
        frames: list[AudioFrame] = []
        for kind, count in pattern:
            if kind == "silence":
                frames.extend(cls._silence_frame(sample_rate, frame_samples) for _ in range(count))
            elif kind == "speech":
                frames.extend(
                    cls._tone_frame(amplitude, freq, 0.0, sample_rate, frame_samples)
                    for _ in range(count)
                )
            else:  # pragma: no cover - guard against typos
                raise ValueError(f"unknown pattern kind {kind!r} (want 'silence' or 'speech')")
        return cls(frames, sample_rate=sample_rate, frame_samples=frame_samples)
