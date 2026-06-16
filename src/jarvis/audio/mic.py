"""``SoundDeviceMicSource`` — the real always-on mic capture loop (T-102).

The **only** place real audio I/O happens. Everything downstream (the VAD in
T-103, ASR in T-104, the whole test suite) consumes the hardware-free
``AudioSource`` seam; this module is the one implementation backed by a physical
microphone, via ``sounddevice`` (a PortAudio binding).

## How the loop works

``sounddevice.RawInputStream`` runs a **real-time callback thread** that delivers
small blocks of captured PCM. That thread must never block (if it does, PortAudio
drops audio), so the callback does the absolute minimum: wrap each block as an
:class:`~jarvis.audio.source.AudioFrame` and ``push`` it into a bounded
:class:`~jarvis.audio.source.RingBuffer`. The consumer side — ``frames()`` —
``pop``-s from that ring buffer on its own schedule and yields frames. When the
consumer keeps up, the buffer stays near-empty; if it ever falls behind, the ring
buffer overwrites the oldest frame and counts an overflow rather than growing
without bound. That is the **bounded-memory, no-unbounded-growth** guarantee an
always-on process needs.

Capture is **16 kHz mono float32** in fixed ``frame_samples`` blocks — exactly the
geometry Silero VAD wants — so no resampling or reblocking is needed between
capture and the VAD.

## Mic permission (macOS)

Opening the input device triggers the **macOS microphone-permission prompt** for
the terminal process the first time. If the user has not granted permission (or
there is no input device), ``start()`` raises :class:`MicPermissionError` /
:class:`NoInputDeviceError`. Callers (and the T-102 smoke test) treat that as a
*documented, expected* outcome — not a fabricated capture — and fall back to the
``FakeAudioSource``. This module never invents audio data.

``sounddevice`` is imported lazily inside the methods so that merely importing the
audio package (e.g. during test collection on a machine without a working
PortAudio) never fails — only actually *starting* capture touches the hardware.
"""

from __future__ import annotations

import contextlib
import queue
from collections.abc import Iterator

import numpy as np

from jarvis.audio.source import (
    DEFAULT_FRAME_SAMPLES,
    DEFAULT_SAMPLE_RATE,
    AudioFrame,
    RingBuffer,
)

# Default ring-buffer depth: ~2 s of 32 ms frames. Big enough to ride out a brief
# consumer stall (a GC pause, a VAD inference spike), small enough that an
# always-on process never accumulates audio without bound.
DEFAULT_RING_CAPACITY = 64


class MicCaptureError(RuntimeError):
    """Base error for the mic capture path."""


class NoInputDeviceError(MicCaptureError):
    """No usable audio input device is available."""


class MicPermissionError(MicCaptureError):
    """The OS denied microphone access (e.g. macOS privacy permission not granted).

    This is an **expected** outcome on a fresh machine, not a bug: the caller
    documents it and falls back to a fake source. It is never papered over with
    synthetic audio.
    """


class SoundDeviceMicSource:
    """Always-on mic capture over PortAudio, exposed as an ``AudioSource`` (T-102).

    Args:
        sample_rate: capture rate; defaults to 16 kHz (Silero VAD's rate).
        frame_samples: samples per delivered frame; defaults to 512 (~32 ms at
            16 kHz — Silero's window).
        ring_capacity: bounded ring-buffer depth in frames (``DEFAULT_RING_CAPACITY``,
            ~2 s). The hard ceiling on retained audio.
        device: optional PortAudio device id/name; ``None`` = system default input.

    Lifecycle: ``start()`` opens the stream (this is where a permission prompt
    appears), ``frames()`` yields captured frames until ``stop()``, and the object
    is a context manager (``with SoundDeviceMicSource() as mic: ...``) that starts
    and stops for you. ``overflows`` exposes the ring buffer's dropped-frame count.
    """

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        frame_samples: int = DEFAULT_FRAME_SAMPLES,
        ring_capacity: int = DEFAULT_RING_CAPACITY,
        device: int | str | None = None,
    ) -> None:
        self._sample_rate = int(sample_rate)
        self._frame_samples = int(frame_samples)
        self._ring = RingBuffer(ring_capacity)
        self._device = device
        self._stream = None  # set in start()
        self._running = False
        # A small signalling queue so the consumer can wait for the callback to
        # deposit frames without busy-spinning. The audio data itself lives in the
        # bounded ring buffer; this only carries "a frame arrived" tokens.
        self._signal: queue.Queue[None] = queue.Queue()

    # -- AudioSource interface -----------------------------------------------

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def frame_samples(self) -> int:
        return self._frame_samples

    @property
    def overflows(self) -> int:
        """Frames the ring buffer dropped because the consumer fell behind."""
        return self._ring.overflows

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Open the PortAudio input stream and begin capturing.

        On macOS this is what triggers the microphone-permission prompt. Raises
        :class:`NoInputDeviceError` if there is no input device, or
        :class:`MicPermissionError` if the OS denies access.
        """
        try:
            import sounddevice as sd
        except (ImportError, OSError) as exc:  # PortAudio library missing/broken
            raise MicCaptureError(f"sounddevice/PortAudio unavailable: {exc}") from exc

        try:
            default_input = sd.default.device[0]
            if default_input is None and self._device is None and not self._has_input_device(sd):
                raise NoInputDeviceError("no audio input device available")
        except NoInputDeviceError:
            raise
        except Exception:  # noqa: BLE001 - probing defaults shouldn't be fatal
            # Fall through to letting the stream open surface the real error.
            pass

        def _callback(indata, frames, time_info, status) -> None:  # noqa: ANN001, ARG001
            # Real-time thread: do the minimum. Copy out of PortAudio's buffer
            # (it reuses it), wrap, push to the bounded ring, signal the consumer.
            samples = np.frombuffer(bytes(indata), dtype=np.float32).copy()
            self._ring.push(AudioFrame(samples=samples, sample_rate=self._sample_rate))
            with contextlib.suppress(queue.Full):  # unbounded queue, never full
                self._signal.put_nowait(None)

        try:
            self._stream = sd.RawInputStream(
                samplerate=self._sample_rate,
                blocksize=self._frame_samples,
                channels=1,
                dtype="float32",
                device=self._device,
                callback=_callback,
            )
            self._stream.start()
        except Exception as exc:  # noqa: BLE001 - classify PortAudio errors below
            raise self._classify_open_error(exc) from exc
        self._running = True

    def stop(self) -> None:
        """Stop and close the stream. Safe to call more than once."""
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None

    def frames(self) -> Iterator[AudioFrame]:
        """Yield captured frames until ``stop()`` is called.

        Pops from the bounded ring buffer; blocks (without busy-spinning) on the
        signal queue when the buffer is momentarily empty. Drains any buffered
        frames after a stop so nothing captured is silently lost.
        """
        if not self._running:
            raise MicCaptureError("call start() before frames()")
        while self._running:
            frame = self._ring.pop()
            if frame is not None:
                yield frame
                continue
            try:
                self._signal.get(timeout=0.1)
            except queue.Empty:
                continue
        # Drain whatever the callback left after the stop.
        for frame in self._ring.drain():
            yield frame

    # -- context manager -----------------------------------------------------

    def __enter__(self) -> SoundDeviceMicSource:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _has_input_device(sd) -> bool:  # noqa: ANN001
        try:
            return any(d.get("max_input_channels", 0) > 0 for d in sd.query_devices())
        except Exception:  # noqa: BLE001 - if probing fails, assume a device may exist
            return True

    @staticmethod
    def _classify_open_error(exc: Exception) -> MicCaptureError:
        """Map a raw PortAudio open error onto the typed capture errors.

        macOS surfaces a denied mic permission as a PortAudio error when opening
        the input stream; we classify by message so the caller can distinguish
        "permission needed" (expected, fall back to fake) from a real failure.
        """
        msg = str(exc).lower()
        if any(
            term in msg
            for term in ("permission", "access", "not authoriz", "not authoris", "denied")
        ):
            return MicPermissionError(f"microphone access denied by the OS: {exc}")
        if any(term in msg for term in ("no default", "no input", "invalid device", "no device")):
            return NoInputDeviceError(f"no usable audio input device: {exc}")
        return MicCaptureError(f"failed to open the input stream: {exc}")
