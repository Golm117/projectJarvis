"""The local audio sensing package (Phase 1) — the always-on ears.

This is sensing-engineer's home for the on-device audio path that sits *in front
of* the frozen ``TranscriptSource`` seam (``docs/architecture/module-map.md``).
The pipeline is:

    mic ──► AudioSource ──► (T-103 Silero VAD) ──► TurnTakingGate edges
                       └──► (T-104 ASR) ──► Utterance ──► TranscriptSource

Phase 1 builds it bottom-up:

* **T-102 (this):** the ``AudioSource`` abstraction + a real ``sounddevice``
  (PortAudio) **mic capture loop** that is continuous, ring-buffered, fixed
  16 kHz mono frames, with bounded memory and no dropped frames; plus a
  ``FakeAudioSource`` so the VAD and the buffer logic can be driven from
  synthetic frames with **no real hardware**.
* **T-103:** Silero VAD consuming ``AudioSource`` frames, emitting
  ``on_speech_start`` / ``on_speech_end`` edges onto the ``TurnTakingGate``.
* **T-104:** ``MicSource`` — wires the VAD + ``mlx-whisper base.en`` into
  ``Utterance`` events behind ``TranscriptSource``.

The design constraint mirrors the core's: **no hidden global state, deps
injected.** The capture loop is the one place real I/O (PortAudio) lives; the
``AudioSource`` Protocol keeps everything downstream hardware-free and testable.
"""

from __future__ import annotations

from jarvis.audio.source import (
    DEFAULT_FRAME_SAMPLES,
    DEFAULT_SAMPLE_RATE,
    AudioFrame,
    AudioSource,
    FakeAudioSource,
    RingBuffer,
)

__all__ = [
    "DEFAULT_FRAME_SAMPLES",
    "DEFAULT_SAMPLE_RATE",
    "AudioFrame",
    "AudioSource",
    "FakeAudioSource",
    "RingBuffer",
]
