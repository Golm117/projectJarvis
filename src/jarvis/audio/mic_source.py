"""``MicSource`` — the live ``TranscriptSource``: mic → VAD → ASR → ``Utterance`` (T-104).

This is the Phase-1 replacement for ``ScriptedSource``. It implements the **exact
same frozen seam** (``jarvis.adapters.transcript_source.TranscriptSource`` —
``utterances() -> Iterable[Utterance]``), so it drops straight into the
``AttentionLayer`` orchestrator with **zero change** to the core: the orchestrator
still just consumes ``Utterance`` events, unaware that they now come from a real
microphone instead of a canned list.

## The pipeline (module map §"The audio sensing path")

    AudioSource ──► SileroVad ──► speech segments ──► Transcriber ──► Utterance
                         │
                         └──► TurnTakingGate edges (on_speech_start / on_speech_end)

``MicSource`` ties the T-102 ``AudioSource`` and the T-103 ``SileroVad`` together
and adds the ASR step:

1. Every frame flows through the VAD. The VAD debounces per-frame speech decisions
   into clean **speech-start / speech-end edges** and drives the shared
   ``TurnTakingGate`` (so the same summon/interjection timing the Phase-0
   ``ScriptedSource`` produced now runs on live audio — see T-103).
2. ``MicSource`` watches those same edges (via the VAD's ``on_edge`` callback) to
   know a speech **segment**'s start and end. Between the edges it accumulates the
   segment's frames.
3. On each **speech-end** edge it concatenates the segment's frames into one
   waveform and hands it to the injected :class:`Transcriber`. Non-empty text
   becomes an ``Utterance``.

## ``Utterance.ts`` from the VAD timeline (not a wall clock)

``Utterance.ts`` must be a monotonic-seconds stamp the ``RollingWindow`` can evict
by (module map §"Cross-cutting design constraints" #1; ``Utterance`` is frozen
with ``ts`` *required*, supplied by the producer). The producer here is the VAD
timeline: ``MicSource`` counts the frames it has seen and converts to seconds as
``frames_seen × frame_samples / sample_rate``. So ``ts`` is the audio position of
the **end** of the speech segment — derived purely from the frame stream, with **no
``time.monotonic()``**, preserving the "no hidden clock" invariant all the way out
to the live source. (The ``TurnTakingGate`` keeps its own injected clock for the
politeness/settle gaps; the two timelines are consistent because both advance with
the same audio.)

## The ``Transcriber`` seam (so tests need no real model)

ASR sits behind a tiny :class:`Transcriber` Protocol — ``transcribe(waveform,
sample_rate) -> str`` — exactly mirroring the core's injected-backend discipline
(``SummarizerBackend`` / ``WallBackend``) and the audio path's ``FrameClassifier``.
The default is :class:`MlxWhisperTranscriber` (mlx-whisper ``base.en``, the T-101
spike's choice, loaded lazily so importing this module never pulls the model).
Tests inject a fake transcriber and a ``FakeAudioSource``, so the whole
segment→``Utterance`` logic is exercised deterministically with **no mic and no
model**.

## Speaker label

A fixed placeholder (``DEFAULT_SPEAKER``). Diarization is explicitly out of scope
for v0 (``.pdr.md``): the single English-speaking developer is the user, and
``Utterance.speaker`` only needs *a* label for the transcript rendering. A real
diarizer can fill this later behind the same field.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import Protocol, runtime_checkable

import numpy as np

from jarvis.audio.source import AudioFrame, AudioSource
from jarvis.audio.vad import SileroVad
from jarvis.core.turn_taking_gate import TurnTakingGate
from jarvis.types import Utterance

# The v0 placeholder speaker label (diarization is out of scope — .pdr.md). Every
# Utterance MicSource produces carries this; a real diarizer can replace it behind
# the same Utterance.speaker field without touching anything downstream.
DEFAULT_SPEAKER = "speaker"

# The MLX-converted whisper base.en weights chosen in the T-101 spike (DECISIONS.md
# 2026-06-15 "ASR runtime: mlx-whisper (base.en)"). small.en is the documented
# upgrade lever; this is the default the spike recommended.
DEFAULT_MLX_WHISPER_REPO = "mlx-community/whisper-base.en-mlx"


@runtime_checkable
class Transcriber(Protocol):
    """Turns a speech-segment waveform into text — the ASR seam.

    The one thing that genuinely needs the model, injected so the
    segment→``Utterance`` logic in :class:`MicSource` is testable without it. The
    default :class:`MlxWhisperTranscriber` runs mlx-whisper ``base.en``; tests
    inject a fake.

    ``waveform`` is a 1-D float32 numpy array in ``[-1, 1]`` (the concatenated
    samples of one speech segment); ``sample_rate`` is its rate (16 kHz). Returns
    the transcript text (possibly empty if the segment had no decodable speech).
    """

    def transcribe(self, waveform: np.ndarray, sample_rate: int) -> str: ...


class MlxWhisperTranscriber:
    """The real ASR: mlx-whisper ``base.en`` on Apple Silicon (lazy import).

    Wraps ``mlx_whisper.transcribe(waveform, path_or_hf_repo=...)`` — which accepts
    a float32 waveform directly and returns ``{"text": ...}``. ``mlx_whisper`` is
    imported **lazily** on first ``transcribe`` so merely importing
    :mod:`jarvis.audio.mic_source` never loads MLX or downloads the model; only an
    actual transcription does. (Mirrors :class:`~jarvis.audio.vad.SileroFrameClassifier`.)

    Args:
        repo: the HF repo / local path of the MLX-converted Whisper weights
            (default: ``base.en``, the T-101 choice). Pass the ``small.en`` repo to
            pull the documented upgrade lever.
    """

    def __init__(self, repo: str = DEFAULT_MLX_WHISPER_REPO) -> None:
        self._repo = repo
        self._transcribe_fn: Callable[..., dict] | None = None

    def _ensure_loaded(self) -> None:
        if self._transcribe_fn is None:
            import mlx_whisper  # heavy (MLX/Metal); loaded only on first real use

            self._transcribe_fn = mlx_whisper.transcribe

    def transcribe(self, waveform: np.ndarray, sample_rate: int) -> str:
        if sample_rate != 16_000:
            raise ValueError(f"mlx-whisper base.en expects 16 kHz audio, got {sample_rate}")
        self._ensure_loaded()
        assert self._transcribe_fn is not None
        # mlx-whisper wants float32; the AudioSource already yields float32 frames.
        audio = np.ascontiguousarray(waveform, dtype=np.float32)
        result = self._transcribe_fn(audio, path_or_hf_repo=self._repo)
        return str(result.get("text", "")).strip()


class MicSource:
    """A live ``TranscriptSource``: mic → Silero VAD → mlx-whisper → ``Utterance`` (T-104).

    Implements the frozen ``TranscriptSource`` seam (``utterances()`` yielding
    ``Utterance``), so it replaces ``ScriptedSource`` in the orchestrator with no
    change to the core. It consumes an injected :class:`AudioSource`, segments it
    with a :class:`~jarvis.audio.vad.SileroVad` (which drives the shared
    ``TurnTakingGate`` edges), and transcribes each speech segment via the injected
    :class:`Transcriber`.

    Args:
        source: the audio frames to consume (real ``SoundDeviceMicSource`` live, a
            ``FakeAudioSource`` in tests).
        gate: the orchestrator's ``TurnTakingGate`` — the **same** instance the
            ``SummonController`` reads, so summon/interjection timing runs on the
            live VAD edges. Optional (e.g. a transcribe-only smoke test), but the
            real pipeline always passes it.
        transcriber: the ASR seam (default: :class:`MlxWhisperTranscriber`,
            ``base.en``, lazy). Inject a fake in tests.
        vad: the segmenter (default: a fresh :class:`SileroVad`). ``MicSource``
            installs its own ``on_edge`` hook on it to track segment boundaries, so
            pass a VAD constructed **without** an ``on_edge`` of your own (or accept
            that ``MicSource`` chains onto the gate it already drives).
        speaker: the placeholder speaker label for every produced ``Utterance``
            (diarization is out of scope — :data:`DEFAULT_SPEAKER`).

    ``MicSource`` wires the VAD's ``gate`` to the given ``gate`` if the VAD was not
    already given one, so the single call ``for u in mic_source.utterances(): ...``
    drives the gate, runs ASR, and yields utterances together.
    """

    def __init__(
        self,
        source: AudioSource,
        gate: TurnTakingGate | None = None,
        transcriber: Transcriber | None = None,
        vad: SileroVad | None = None,
        speaker: str = DEFAULT_SPEAKER,
    ) -> None:
        self._source = source
        self._transcriber = transcriber if transcriber is not None else MlxWhisperTranscriber()
        self._speaker = speaker

        # The VAD drives the shared gate's edges. If the caller didn't supply a
        # VAD, build one bound to the gate; if they did but it has no gate, bind it.
        if vad is None:
            vad = SileroVad(gate=gate)
        elif gate is not None and vad._gate is None:  # noqa: SLF001 - intentional wiring
            vad._gate = gate  # noqa: SLF001
        self._vad = vad

        # Segment accumulation state.
        self._segment_frames: list[AudioFrame] = []
        self._in_segment = False
        self._frames_seen = 0  # the VAD timeline, in frames, for Utterance.ts
        self._sample_rate = source.sample_rate
        self._pending: Utterance | None = None  # set by _close_segment, drained per frame

        # Chain our segment-boundary hook onto whatever on_edge the VAD already had
        # (so we don't clobber a caller's instrumentation callback).
        self._prev_on_edge = vad._on_edge  # noqa: SLF001
        vad._on_edge = self._on_edge  # noqa: SLF001

    # -- TranscriptSource seam -----------------------------------------------

    def utterances(self) -> Iterator[Utterance]:
        """Yield an ``Utterance`` per transcribed speech segment.

        Drives every frame from the ``AudioSource`` through the VAD (which fires
        the gate edges + our segment hook). The VAD's ``speech_start`` edge opens a
        segment and ``speech_end`` closes it — between them every frame is
        accumulated; on close the segment is concatenated, transcribed, and (if
        non-empty) yielded as an ``Utterance`` stamped from the VAD timeline. A
        segment still open when the source is exhausted is flushed at the end (so a
        final utterance isn't lost if the stream stops before the silence
        hangover).

        Note the edge timing: the VAD debounces, so ``speech_start`` fires a few
        frames *after* speech actually began and ``speech_end`` a few frames after
        it ended. That hangover-trimmed window is exactly the speech-bearing audio
        we want to hand to ASR.
        """
        for frame in self._source.frames():
            self._frames_seen += 1
            # The VAD classifies this frame and may fire on_speech_start /
            # on_speech_end (→ self._on_edge), flipping _in_segment for *this*
            # frame onward. Process first, then decide whether to keep the frame.
            self._vad.process_frame(frame)
            if self._in_segment:
                self._segment_frames.append(frame)
            # A speech-end edge during process_frame stashed a pending utterance.
            utt = self._take_pending_utterance()
            if utt is not None:
                yield utt

        # Flush a segment still open at end-of-stream (close it as if speech ended).
        if self._in_segment:
            self._close_segment()
            utt = self._take_pending_utterance()
            if utt is not None:
                yield utt

    # -- VAD edge hook --------------------------------------------------------

    def _on_edge(self, edge: str) -> None:
        """Track segment boundaries off the VAD edges (also re-fires any prior hook)."""
        if self._prev_on_edge is not None:
            self._prev_on_edge(edge)
        if edge == "speech_start":
            self._in_segment = True
            self._segment_frames = []
        elif edge == "speech_end":
            self._close_segment()

    def _close_segment(self) -> None:
        """Concatenate + transcribe the current segment, stashing a pending Utterance."""
        self._in_segment = False
        frames = self._segment_frames
        self._segment_frames = []
        if not frames:
            return
        waveform = np.concatenate([f.samples for f in frames]).astype(np.float32)
        text = self._transcriber.transcribe(waveform, self._sample_rate).strip()
        if not text:
            return
        # ts = the audio position of the segment's end, on the VAD timeline (frames
        # seen so far ÷ rate). No wall clock — preserves the no-hidden-clock rule.
        ts = self._frames_seen * self._frame_seconds
        self._pending = Utterance(speaker=self._speaker, text=text, ts=ts)

    # -- pending-utterance plumbing ------------------------------------------

    def _take_pending_utterance(self) -> Utterance | None:
        utt = self._pending
        self._pending = None
        return utt

    @property
    def _frame_seconds(self) -> float:
        """Seconds per frame on this source (``frame_samples / sample_rate``)."""
        return self._source.frame_samples / self._sample_rate


def concatenate_frames(frames: Iterable[AudioFrame]) -> np.ndarray:
    """Concatenate a sequence of frames into one float32 waveform (helper)."""
    parts = [f.samples for f in frames]
    if not parts:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(parts).astype(np.float32)
