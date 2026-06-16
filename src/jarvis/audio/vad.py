"""``SileroVad`` — speech/silence segmentation driving the gate edges (T-103).

The second stage of the always-on ears: consume :class:`~jarvis.audio.source.AudioFrame`
frames from an ``AudioSource`` (T-102), decide speech vs. silence, and emit the two
**boundary edges** the turn-taking machine runs on —
:meth:`~jarvis.core.turn_taking_gate.TurnTakingGate.on_speech_start` and
``on_speech_end``. Those are the *exact* edges the Phase-0 ``ScriptedSource`` synthesized
by hand; here they come from a real VAD on live audio, so the same ``TurnTakingGate`` +
``SummonController`` logic the core already has is now driven by the microphone with no
change to the core.

## Edges, not timestamps — aligning to the frozen gate seam

The ``TurnTakingGate`` is an **edge API** that stamps time from *its own* injected clock
(DECISIONS.md 2026-06-15 "TurnTakingGate event-input API"). So this VAD emits *edges*,
never timestamps: on the frame where speech begins it calls ``on_speech_start()``; on the
frame where speech ends it calls ``on_speech_end()``. The gate decides how long the
silence has been from the moment ``on_speech_end()`` arrived. The VAD does **not** reshape
that seam — it drives it.

The VAD's own internal sense of "how long" is measured in **frames** (each frame is a
fixed ``frame_samples / sample_rate`` seconds), so it needs no clock either: the audio path
is entirely clock-free, and the gate is the single clock owner.

## Hysteresis (debounce) so edges are clean

A raw per-frame speech/silence decision flickers at segment boundaries. ``SileroVad``
debounces it the way Silero's own ``VADIterator`` does, in frame units:

* a **speech-start** edge fires only after ``speech_start_frames`` consecutive speech
  frames (ignore a one-frame blip),
* a **speech-end** edge fires only after ``silence_end_frames`` consecutive silence
  frames (a brief intra-word pause is not the end of a turn — this is the VAD-side
  endpoint hangover, distinct from and shorter than the gate's politeness gap).

Both, plus the speech **threshold**, are constructor-injected so qa-tuning can calibrate
sensitivity (Phase 5) in one place.

## The frame classifier seam (so tests need no torch)

The one thing that genuinely needs the model is "is *this* frame speech?". That is
injected as a ``FrameClassifier`` callable. The default is :class:`SileroFrameClassifier`,
which loads the real Silero VAD model (torch) and scores each frame. Tests inject
:class:`EnergyFrameClassifier` (a pure RMS-threshold decision) instead, so the
**edge-sequencing logic** — the part that drives the gate — is exercised deterministically
on synthetic frames with **no torch and no real mic**. This mirrors the core's
injected-backend discipline (``SummarizerBackend``/``WallBackend``): the heavy model sits
behind a tiny seam, and the logic around it is tested against a fake.

torch is a heavy dependency (already present from the Phase-2 MLX/ASR stack); the real
classifier is the only thing that pulls it, and it's loaded lazily.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol, runtime_checkable

from jarvis.audio.source import DEFAULT_SAMPLE_RATE, AudioFrame, AudioSource
from jarvis.core.turn_taking_gate import TurnTakingGate

# Silero's own defaults, expressed in frames at 512-sample/32 ms granularity.
# Speech is declared after ~1 confident frame.
#
# Endpoint hangover (silence_end_frames) — tuned from the upstream Silero default
# of 6 frames (~192 ms) to 15 frames (~480 ms) in T-507:
#
#   WHY 15 frames?  A natural breath-length mid-sentence pause is typically
#   200–400 ms.  At 6 frames (192 ms) those pauses close the segment and split a
#   single utterance into fragments — the live log showed "times 7." and "What
#   does that equal?" arriving as two separate segments when they were one spoken
#   question.  At 15 frames (480 ms) a normal breath pause is absorbed; the
#   segment stays whole; Whisper has the full prosodic context to emit punctuation
#   (including "?") correctly.
#
#   WHY NOT longer?  A genuine ~1 s thinking pause between sentences IS a real
#   turn boundary — merging those would add ~1 s+ of extra latency before the
#   segment is emitted, delaying the politeness-gap clock.  480 ms is well below
#   that; it is the sweet spot between "holds a breath" and "holds a thought".
#
#   TRADEOFF to be aware of:  raising the hangover delays turn-end detection by
#   ~+288 ms (480 ms − 192 ms) relative to the old value.  The politeness gap is
#   2 s; a Path-B offer therefore starts ~288 ms later.  The summon (Path A) fires
#   on the completed utterance and is similarly delayed by ~288 ms.  Both delays
#   are modest and well within any user-perceptible threshold.  They are the
#   correct tradeoff for fragmentation-free transcription.
#
#   The constant is constructor-injectable in SileroVad (speech_start_frames /
#   silence_end_frames) so Phase-5 tuning can revisit from one place.
DEFAULT_THRESHOLD = 0.5
DEFAULT_SPEECH_START_FRAMES = 1
DEFAULT_SILENCE_END_FRAMES = 15  # ~480 ms at 32 ms/frame (raised from 6 in T-507)


@runtime_checkable
class FrameClassifier(Protocol):
    """Decides whether a single :class:`AudioFrame` contains speech.

    The seam behind which the real Silero model (torch) lives. The default
    :class:`SileroFrameClassifier` scores each frame with the model; tests inject
    :class:`EnergyFrameClassifier` so the VAD's edge logic runs without torch.
    """

    def is_speech(self, frame: AudioFrame) -> bool:
        """``True`` if this frame is speech, ``False`` if silence."""
        ...


class EnergyFrameClassifier:
    """A pure RMS-energy speech decision — the hardware/model-free test classifier.

    ``is_speech`` is simply ``frame.rms >= threshold``. Deterministic, no torch, no
    model load: this is what the VAD tests inject so the start/end edge sequencing
    is exercised on synthetic silence/tone frames. It is *not* the production VAD
    (it cannot tell speech from any loud sound) — it is the fake behind the
    ``FrameClassifier`` seam.
    """

    def __init__(self, threshold: float = 0.05) -> None:
        self._threshold = float(threshold)

    def is_speech(self, frame: AudioFrame) -> bool:
        return frame.rms >= self._threshold


class SileroFrameClassifier:
    """The real Silero VAD per-frame decision (loads the torch model lazily).

    Wraps Silero's ``load_silero_vad()`` model and scores each frame; a frame is
    speech if the model's probability ``>= threshold``. Imported lazily so merely
    importing :mod:`jarvis.audio.vad` never requires torch / the model download —
    only constructing this classifier does. Expects 16 kHz frames (Silero's rate);
    the model wants 512-sample windows at 16 kHz, which is exactly the
    ``AudioSource`` frame geometry.

    Args:
        threshold: speech-probability cut in ``[0, 1]`` (default 0.5).
        sample_rate: must be 16000 (Silero's supported rate here).
    """

    def __init__(
        self,
        threshold: float = DEFAULT_THRESHOLD,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
    ) -> None:
        if sample_rate != 16_000:
            raise ValueError(f"Silero VAD expects 16 kHz audio, got {sample_rate}")
        self._threshold = float(threshold)
        self._sample_rate = sample_rate
        self._model = None  # lazily loaded torch model
        self._torch = None

    def _ensure_model(self) -> None:
        if self._model is None:
            import torch  # heavy; loaded only when the real classifier is used
            from silero_vad import load_silero_vad

            self._torch = torch
            self._model = load_silero_vad()

    def is_speech(self, frame: AudioFrame) -> bool:
        self._ensure_model()
        assert self._torch is not None and self._model is not None
        tensor = self._torch.from_numpy(frame.samples)
        prob = float(self._model(tensor, self._sample_rate).item())
        return prob >= self._threshold


class SileroVad:
    """Segments an ``AudioSource`` into speech/silence and drives the gate edges (T-103).

    Holds an injected :class:`FrameClassifier` (the real Silero model by default,
    an energy fake in tests) and debounces its per-frame decisions into clean
    speech-start / speech-end **edges**, which it delivers to an injected
    :class:`~jarvis.core.turn_taking_gate.TurnTakingGate` (and/or a generic
    ``on_edge`` callback). The gate stamps the edges from its own clock — this VAD
    emits edges only, never timestamps.

    Args:
        classifier: the per-frame speech decision (default: a real
            :class:`SileroFrameClassifier`, which loads torch lazily). Inject an
            :class:`EnergyFrameClassifier` in tests.
        gate: the ``TurnTakingGate`` to drive. Optional — if ``None``, only the
            ``on_edge`` callback fires (useful for tests that assert the raw edge
            sequence without a gate).
        on_edge: optional callback invoked with ``"speech_start"`` /
            ``"speech_end"`` as each edge fires (for instrumentation/tests).
        speech_start_frames: consecutive speech frames required before a
            speech-start edge (debounce; default 1).
        silence_end_frames: consecutive silence frames required before a
            speech-end edge (the VAD-side endpoint hangover; default ~15 ≈ 480 ms,
            raised from 6 in T-507 to absorb natural breath-length mid-sentence
            pauses without splitting utterances — see ``DEFAULT_SILENCE_END_FRAMES``
            for the full rationale and tradeoff).

    Use :meth:`process_frame` to feed one frame at a time, or :meth:`run` to
    consume an entire ``AudioSource`` to exhaustion.
    """

    def __init__(
        self,
        classifier: FrameClassifier | None = None,
        gate: TurnTakingGate | None = None,
        on_edge: Callable[[str], None] | None = None,
        speech_start_frames: int = DEFAULT_SPEECH_START_FRAMES,
        silence_end_frames: int = DEFAULT_SILENCE_END_FRAMES,
    ) -> None:
        if speech_start_frames < 1:
            raise ValueError(f"speech_start_frames must be >= 1, got {speech_start_frames}")
        if silence_end_frames < 1:
            raise ValueError(f"silence_end_frames must be >= 1, got {silence_end_frames}")
        self._classifier = classifier if classifier is not None else SileroFrameClassifier()
        self._gate = gate
        self._on_edge = on_edge
        self._speech_start_frames = int(speech_start_frames)
        self._silence_end_frames = int(silence_end_frames)

        # Debounce state.
        self._in_speech = False  # whether we are currently inside a speech segment
        self._speech_run = 0  # consecutive speech frames while not yet in speech
        self._silence_run = 0  # consecutive silence frames while in speech

    @property
    def in_speech(self) -> bool:
        """Whether the VAD currently considers us inside a speech segment."""
        return self._in_speech

    def process_frame(self, frame: AudioFrame) -> None:
        """Classify one frame and fire a speech-start/speech-end edge if warranted.

        Debounced: a speech-start edge fires only after ``speech_start_frames``
        consecutive speech frames; a speech-end edge only after
        ``silence_end_frames`` consecutive silence frames. Each edge re-arms the
        opposite run counter so the next boundary is measured cleanly.
        """
        speech = self._classifier.is_speech(frame)
        if not self._in_speech:
            if speech:
                self._speech_run += 1
                if self._speech_run >= self._speech_start_frames:
                    self._enter_speech()
            else:
                self._speech_run = 0
        else:
            if speech:
                self._silence_run = 0
            else:
                self._silence_run += 1
                if self._silence_run >= self._silence_end_frames:
                    self._exit_speech()

    def run(self, source: AudioSource) -> None:
        """Consume an entire ``AudioSource``, driving edges until it is exhausted."""
        self.process_frames(source.frames())

    def process_frames(self, frames: Iterable[AudioFrame]) -> None:
        """Feed an iterable of frames through :meth:`process_frame`."""
        for frame in frames:
            self.process_frame(frame)

    # -- edge emission -------------------------------------------------------

    def _enter_speech(self) -> None:
        self._in_speech = True
        self._speech_run = 0
        self._silence_run = 0
        if self._gate is not None:
            self._gate.on_speech_start()
        if self._on_edge is not None:
            self._on_edge("speech_start")

    def _exit_speech(self) -> None:
        self._in_speech = False
        self._speech_run = 0
        self._silence_run = 0
        if self._gate is not None:
            self._gate.on_speech_end()
        if self._on_edge is not None:
            self._on_edge("speech_end")
