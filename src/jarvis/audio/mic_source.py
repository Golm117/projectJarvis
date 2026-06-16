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

import re
from collections import deque
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

# The MLX-converted whisper small.en weights — the T-505 upgrade from base.en.
# Documented as the upgrade lever in docs/audio/asr-spike.md; upgraded after
# real-room testing revealed "Jarvis" → "Germans" mishearing and garbage segments
# with base.en on the built-in mic. small.en is still far inside the joint ASR+SLM
# budget (see T-505 re-measurement in asr-spike.md). base.en remains selectable by
# passing its repo to MlxWhisperTranscriber's `repo` constructor arg.
DEFAULT_MLX_WHISPER_REPO = "mlx-community/whisper-small.en-mlx"

# ---------------------------------------------------------------------------
# Segment noise filter — thresholds (configurable constants)
# ---------------------------------------------------------------------------
# A transcribed segment is dropped (never becomes an Utterance) when its text
# passes none of the lexical tests below. This blocks non-lexical noise segments
# like "!", "service.!!!!!!!!!!", "Mm." from reaching the rolling window, living
# summary, wall detector, or Claude.
#
# Design goals:
#   DROP:  empty / whitespace, pure punctuation/symbol strings ("!", "..", "Mm."),
#          extremely short single-character bursts, ultra-short non-word content.
#   KEEP:  wake word ("Jarvis"), short real replies ("Yes.", "No.", "Okay."),
#          any segment containing at least one alphabetic word of a minimum length.
#
# A "word" here means a contiguous run of alphabetic characters (no digits,
# no punctuation) — this intentionally matches the English-word content of a
# real spoken reply while rejecting single-letter noise and pure symbol strings.

# Minimum number of *alphabetic characters* a word must contain to count as a
# real lexical word (filters single-letter noise like isolated "I" transcriptions
# from room noise, while keeping "No", "Yes", "Mm" — wait, "Mm" is exactly the
# non-lexical case we want to drop). Words shorter than this are not counted.
# "No" = 2, "Yes" = 3, "Jarvis" = 6 — all pass MIN_WORD_LENGTH = 2.
MIN_WORD_LENGTH: int = 2

# A segment must contain at least this many qualifying lexical words to be kept.
# With MIN_WORD_CHARS = 1 this effectively means: at least one real word >= 2 chars.
# "Yes." → 1 word → kept. "!" → 0 words → dropped. "Mm." → 0 words (len("Mm")=2
# but we check it separately) → actually "Mm" is 2 chars, so it would pass the
# word-length check. We handle this with the additional STOP_SYLLABLES set below.
MIN_LEXICAL_WORDS: int = 1

# Non-lexical filler syllables that are common ASR noise artefacts. A segment
# whose *only* words are all members of this set is treated as non-lexical noise
# and dropped, even though the words technically have >= MIN_WORD_LENGTH chars.
# Keep this set small and obvious — the goal is noise, not aggressive filtering.
# "Mm" / "Hmm" / "Uh" / "Um" are universal filler-sound transcriptions from
# background audio that carry no information for the pipeline. Real short replies
# ("Yes", "No", "Okay", "Sure", "Jarvis") are explicitly NOT in this set.
STOP_SYLLABLES: frozenset[str] = frozenset(
    {"mm", "mmm", "hmm", "hm", "uh", "um", "mhm", "mhmm", "huh", "ah", "eh"}
)

# Pre-compiled regex: a "word" = one or more ASCII letters (no digits, no punct).
_ALPHA_WORD_RE: re.Pattern[str] = re.compile(r"[a-zA-Z]+")

# ---------------------------------------------------------------------------
# Pre-roll / lookback buffer — onset recovery (T-506)
# ---------------------------------------------------------------------------
# Real-room use revealed that the start of utterances is dropped: the Silero
# VAD threshold is not crossed until after the quiet beginning of a sentence,
# so the onset frames (before the speech_start edge fires) are never included
# in the segment handed to ASR.
#
# Fix: maintain a rolling deque of the most recent K frames (every frame,
# regardless of segment state). When the speech_start edge fires, seed
# _segment_frames from this deque instead of starting from []. This includes
# the ~300-500 ms of audio that preceded the threshold crossing in the
# transcription input.
#
# Size choice:
#   32 ms/frame × 10 frames = 320 ms — covers a normal sentence onset + the
#   speech_start_frames=1 debounce window. At 32 ms/frame, 16 frames = 512 ms
#   is the comfortable upper bound (captures soft starters without pulling in
#   distracting pre-speech silence — Whisper handles leading silence fine, but
#   keeping it short reduces noise exposure).
#   10 frames is the DEFAULT; it is tunable via MicSource(pre_roll_frames=...).
DEFAULT_PRE_ROLL_FRAMES: int = 10


def _is_lexical(text: str) -> bool:
    """Return True iff *text* contains enough real spoken content to keep.

    A segment is kept when:
    1. After stripping whitespace it is non-empty.
    2. It contains at least ``MIN_LEXICAL_WORDS`` alphabetic words each of
       length >= ``MIN_WORD_LENGTH``.
    3. Not ALL of those qualifying words are in ``STOP_SYLLABLES``.

    This keeps "Jarvis", "Yes.", "No.", "Okay.", "What was the date again?"
    and drops "", "!", "service.!!!!!!!!!!", "Mm.", "Hmm", "Uh".
    """
    stripped = text.strip()
    if not stripped:
        return False
    words = [
        m.group() for m in _ALPHA_WORD_RE.finditer(stripped) if len(m.group()) >= MIN_WORD_LENGTH
    ]
    if len(words) < MIN_LEXICAL_WORDS:
        return False
    # If every qualifying word is a stop syllable, drop it.
    return not all(w.lower() in STOP_SYLLABLES for w in words)


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
        now: optional clock for stamping ``Utterance.ts``. **Default (``None``):**
            stamp from the **VAD timeline** — ``frames_seen × frame_samples /
            sample_rate`` — a clock-free, deterministic position (what the unit
            tests assert, and the module-map contract). **When the orchestrator's
            ``RollingWindow`` is driven by a *real* clock** (the live pipeline, where
            the window's ``now`` and the gate's ``now`` are ``time.monotonic``), pass
            that **same** ``now`` here so ``Utterance.ts`` and the window's eviction
            clock share one timeline — otherwise the window (whose ``now()`` reads,
            say, ~400000 s) would evict every utterance instantly because a
            frame-derived ``ts`` of ~9 s looks ~400000 s stale. The two timelines
            differ only by the boot offset; injecting the shared clock removes it.
        pre_roll_frames: number of frames to look back before the ``speech_start``
            edge when opening a segment (the pre-roll / onset-recovery buffer,
            T-506). At 32 ms/frame, the default of 10 frames gives ~320 ms of
            lookback — enough to capture a sentence onset that sat below the Silero
            threshold. Pass ``0`` to disable (reverts to pre-T-506 behaviour). Must
            be ``>= 0``.

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
        now: Callable[[], float] | None = None,
        pre_roll_frames: int = DEFAULT_PRE_ROLL_FRAMES,
    ) -> None:
        if pre_roll_frames < 0:
            raise ValueError(f"pre_roll_frames must be >= 0, got {pre_roll_frames}")
        self._source = source
        self._transcriber = transcriber if transcriber is not None else MlxWhisperTranscriber()
        self._speaker = speaker
        self._now = now  # None → frame-derived ts; else stamp ts = now()

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

        # Pre-roll / onset-recovery buffer (T-506): a rolling deque of the last K
        # frames, maintained regardless of segment state. When speech_start fires,
        # its contents seed _segment_frames so the sub-threshold onset audio that
        # preceded the edge is included in what goes to ASR.
        # pre_roll_frames=0 disables the pre-roll (empty deque, maxlen=1 avoids
        # the ValueError Python raises for deque(maxlen=0); the flag gates reads).
        self._pre_roll_frames = pre_roll_frames
        self._pre_roll: deque[AudioFrame] = (
            deque(maxlen=pre_roll_frames) if pre_roll_frames > 0 else deque(maxlen=1)
        )

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
            # frame onward. Process first so that when speech_start fires,
            # _on_edge seeds _segment_frames from the pre-roll — which at this
            # point contains the frames that came *before* this frame (the onset
            # audio below the VAD threshold). The current frame enters the
            # segment through the normal append below.
            self._vad.process_frame(frame)
            if self._in_segment:
                self._segment_frames.append(frame)
            # After VAD, add this frame to the pre-roll so it is available as
            # lookback context for the *next* speech_start edge. Inside a
            # segment the deque fills with in-segment frames, but _on_edge
            # clears it when the next speech_start fires, so no bleed-over.
            if self._pre_roll_frames > 0:
                self._pre_roll.append(frame)
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
            # Seed the segment with pre-roll frames (T-506: onset recovery).
            # The deque holds the K frames immediately before this edge, which
            # are the sub-threshold onset audio the VAD hadn't confirmed as
            # speech yet. Including them restores the dropped sentence start.
            # After seeding, clear the deque so mid-segment frames don't bleed
            # into the next segment's pre-roll window.
            if self._pre_roll_frames > 0:
                self._segment_frames = list(self._pre_roll)
                self._pre_roll.clear()
            else:
                self._segment_frames = []
        elif edge == "speech_end":
            self._close_segment()

    def _close_segment(self) -> None:
        """Concatenate + transcribe the current segment, stashing a pending Utterance.

        The segment is dropped (no Utterance produced) when:
        - frames is empty,
        - the transcriber returned empty/whitespace text, or
        - the text fails the lexical noise filter (``_is_lexical``): pure
          punctuation/symbol strings, ultra-short non-word content, or filler
          syllables like "Mm." / "Uh" that are common ASR artefacts on noisy
          built-in mics. Short real replies ("Yes.", "No.", "Jarvis") pass.
        """
        self._in_segment = False
        frames = self._segment_frames
        self._segment_frames = []
        if not frames:
            return
        waveform = np.concatenate([f.samples for f in frames]).astype(np.float32)
        text = self._transcriber.transcribe(waveform, self._sample_rate).strip()
        if not _is_lexical(text):
            return
        # ts: by default the audio position of the segment's end on the VAD
        # timeline (frames seen ÷ rate) — clock-free and deterministic. If a `now`
        # was injected (the live pipeline, where the window runs on the same real
        # clock), stamp from it instead so ts shares the window's timeline.
        ts = self._now() if self._now is not None else self._frames_seen * self._frame_seconds
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
