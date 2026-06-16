"""T-104 — ``MicSource`` (mic → VAD → ASR → ``Utterance``) acceptance tests.

Deterministic, **no real mic and no real model**: a ``FakeAudioSource`` supplies
synthetic silence/tone frames, an ``EnergyFrameClassifier`` (pure RMS) stands in
for the Silero model behind the ``FrameClassifier`` seam, and a ``FakeTranscriber``
stands in for mlx-whisper behind the ``Transcriber`` seam. So these tests exercise
the real segment→``Utterance`` logic and the gate-edge wiring with zero hardware.

What they prove (the T-104 acceptance):
  * a speech segment becomes the right ``Utterance`` (placeholder speaker, the
    transcriber's text),
  * ``Utterance.ts`` is stamped from the **VAD timeline** (frame count ÷ rate),
    not a wall clock,
  * the shared ``TurnTakingGate`` receives the matching speech-start/speech-end
    edges (so summon/interjection timing runs on the live edges),
  * multiple segments → multiple utterances; silence-only → none; empty ASR text
    is dropped.
"""

from __future__ import annotations

import numpy as np

from jarvis.audio.mic_source import DEFAULT_SPEAKER, MicSource, MlxWhisperTranscriber
from jarvis.audio.source import DEFAULT_FRAME_SAMPLES, DEFAULT_SAMPLE_RATE, FakeAudioSource
from jarvis.audio.vad import EnergyFrameClassifier, SileroVad
from jarvis.core.turn_taking_gate import TurnTakingGate
from tests.clock import SimulatedClock

# A debounce that fires fast so short synthetic patterns produce clean segments:
# speech declared after 2 tone frames, ended after 2 silence frames.
_START_FRAMES = 2
_END_FRAMES = 2


class FakeTranscriber:
    """A scripted ``Transcriber`` — returns canned text per segment, records calls.

    No model: returns the next text from ``texts`` for each ``transcribe`` call
    (cycling/repeating the last once exhausted), and records the waveforms it was
    handed so a test can assert the right samples reached ASR.
    """

    def __init__(self, texts: list[str] | None = None) -> None:
        self._texts = list(texts) if texts else ["transcribed text"]
        self.calls: list[tuple[np.ndarray, int]] = []

    def transcribe(self, waveform: np.ndarray, sample_rate: int) -> str:
        self.calls.append((waveform, sample_rate))
        idx = min(len(self.calls) - 1, len(self._texts) - 1)
        return self._texts[idx]


def _vad(gate: TurnTakingGate | None = None) -> SileroVad:
    """A SileroVad with the energy fake classifier + tight debounce (no torch)."""
    return SileroVad(
        classifier=EnergyFrameClassifier(threshold=0.05),
        gate=gate,
        speech_start_frames=_START_FRAMES,
        silence_end_frames=_END_FRAMES,
    )


def _mic_source(
    pattern: list[tuple[str, int]],
    transcriber: FakeTranscriber,
    gate: TurnTakingGate | None = None,
) -> MicSource:
    source = FakeAudioSource.from_pattern(pattern, amplitude=0.3)
    return MicSource(source=source, gate=gate, transcriber=transcriber, vad=_vad(gate))


# -- a speech segment becomes the right Utterance ----------------------------


def test_single_speech_segment_yields_one_utterance() -> None:
    transcriber = FakeTranscriber(["did you book the flights"])
    mic = _mic_source(
        [("silence", 3), ("speech", 10), ("silence", 4)],
        transcriber,
    )

    utts = list(mic.utterances())

    assert len(utts) == 1
    assert utts[0].text == "did you book the flights"
    assert utts[0].speaker == DEFAULT_SPEAKER
    # ASR was called exactly once, on a non-trivial waveform.
    assert len(transcriber.calls) == 1
    waveform, sr = transcriber.calls[0]
    assert sr == DEFAULT_SAMPLE_RATE
    assert waveform.dtype == np.float32
    assert waveform.size > 0
    assert float(np.sqrt(np.mean(waveform**2))) > 0.0  # real (tone) energy reached ASR


def test_silence_only_yields_no_utterances() -> None:
    transcriber = FakeTranscriber(["should not appear"])
    mic = _mic_source([("silence", 12)], transcriber)

    assert list(mic.utterances()) == []
    assert transcriber.calls == []  # ASR never ran on silence


def test_empty_transcript_is_dropped() -> None:
    # The transcriber returns "" (e.g. whisper found no decodable speech) — no
    # Utterance should be emitted even though a segment occurred.
    transcriber = FakeTranscriber(["   "])  # whitespace → stripped to empty
    mic = _mic_source([("silence", 3), ("speech", 10), ("silence", 4)], transcriber)

    assert list(mic.utterances()) == []
    assert len(transcriber.calls) == 1  # ASR did run; its empty result was dropped


# -- multiple segments → multiple utterances ---------------------------------


def test_two_segments_yield_two_utterances() -> None:
    transcriber = FakeTranscriber(["first thing", "second thing"])
    mic = _mic_source(
        [
            ("silence", 3),
            ("speech", 8),
            ("silence", 4),  # end of segment 1
            ("speech", 8),
            ("silence", 4),  # end of segment 2
        ],
        transcriber,
    )

    utts = list(mic.utterances())

    assert [u.text for u in utts] == ["first thing", "second thing"]
    assert len(transcriber.calls) == 2
    # ts is monotonic and increases across segments.
    assert utts[0].ts < utts[1].ts


# -- ts comes from the VAD timeline ------------------------------------------


def test_ts_is_stamped_from_the_vad_timeline_not_a_wall_clock() -> None:
    # 3 silence + 10 speech + 4 silence frames. The speech-end edge fires after
    # _END_FRAMES (=2) consecutive silence frames following the speech, i.e. at
    # frame index 3 (silence) + 10 (speech) + 2 (silence) = 15 frames seen.
    # Use a real two-char word so the T-505 lexical filter doesn't drop it.
    transcriber = FakeTranscriber(["hi"])
    mic = _mic_source([("silence", 3), ("speech", 10), ("silence", 4)], transcriber)

    (utt,) = list(mic.utterances())

    seconds_per_frame = DEFAULT_FRAME_SAMPLES / DEFAULT_SAMPLE_RATE
    expected_frames = 3 + 10 + _END_FRAMES
    assert utt.ts == expected_frames * seconds_per_frame


# -- injected clock for ts (the live-pipeline consistency fix, T-105) --------


def test_injected_now_stamps_ts_from_that_clock() -> None:
    # When a `now` is injected (the live pipeline, where the RollingWindow runs on
    # the same real clock), Utterance.ts comes from that clock — not the frame
    # timeline — so producer ts and window-eviction clock share one timeline.
    clock = SimulatedClock()
    clock.set(1_000.0)  # a large "boot-offset" value, like time.monotonic()
    transcriber = FakeTranscriber(["hi"])
    source = FakeAudioSource.from_pattern([("silence", 3), ("speech", 10), ("silence", 4)])
    mic = MicSource(
        source=source, gate=None, transcriber=transcriber, vad=_vad(None), now=clock.now
    )

    (utt,) = list(mic.utterances())

    # ts is the injected clock's value, not frames × seconds_per_frame (~0.5 s).
    assert utt.ts == 1_000.0


# -- the shared gate receives the matching edges -----------------------------


def test_drives_the_shared_gate_edges() -> None:
    clock = SimulatedClock()
    gate = TurnTakingGate(clock.now)
    edges: list[str] = []
    transcriber = FakeTranscriber(["hello"])
    source = FakeAudioSource.from_pattern([("silence", 3), ("speech", 10), ("silence", 4)])
    vad = SileroVad(
        classifier=EnergyFrameClassifier(threshold=0.05),
        gate=gate,
        on_edge=edges.append,  # MicSource must chain onto this, not clobber it
        speech_start_frames=_START_FRAMES,
        silence_end_frames=_END_FRAMES,
    )
    mic = MicSource(source=source, gate=gate, transcriber=transcriber, vad=vad)

    list(mic.utterances())

    # Exactly one start then one end edge reached both the caller's on_edge and
    # the gate (MicSource chained, did not replace, the instrumentation hook).
    assert edges == ["speech_start", "speech_end"]


def test_gate_politeness_gap_opens_after_segment_for_path_b() -> None:
    # After the speech-end edge, advancing the injected clock past the politeness
    # gap makes the gate report the gap elapsed — i.e. the live edges drive the
    # same Path-B timing the ScriptedSource did in Phase 0.
    clock = SimulatedClock()
    gate = TurnTakingGate(clock.now, politeness_gap_seconds=2.0)
    transcriber = FakeTranscriber(["question?"])
    source = FakeAudioSource.from_pattern([("silence", 3), ("speech", 10), ("silence", 4)])
    mic = MicSource(source=source, gate=gate, transcriber=transcriber, vad=_vad(gate))

    list(mic.utterances())

    assert gate.politeness_gap_elapsed() is False  # right after speech ended (t=0)
    clock.advance(2.5)
    assert gate.politeness_gap_elapsed() is True
    assert gate.speech_resumed() is False


def test_works_without_a_gate() -> None:
    # gate=None is allowed (a transcribe-only smoke path): utterances still flow.
    transcriber = FakeTranscriber(["no gate needed"])
    source = FakeAudioSource.from_pattern([("silence", 3), ("speech", 10), ("silence", 4)])
    mic = MicSource(source=source, gate=None, transcriber=transcriber, vad=_vad(None))

    (utt,) = list(mic.utterances())
    assert utt.text == "no gate needed"


# -- it satisfies the frozen TranscriptSource seam ---------------------------


def test_micsource_drops_into_attention_layer_like_a_transcript_source() -> None:
    # The frozen TranscriptSource Protocol is just utterances() -> Iterable[Utterance].
    # Rather than an isinstance check (the Protocol isn't runtime_checkable, by
    # design), prove MicSource drops into AttentionLayer.run() exactly where
    # ScriptedSource did: the orchestrator ingests its utterances unchanged.
    from jarvis.attention_layer import AttentionLayer
    from jarvis.core.turn_taking_gate import TurnTakingGate
    from tests.fakes import FakeResponder, FakeVoice

    clock = SimulatedClock()
    gate = TurnTakingGate(clock.now)
    transcriber = FakeTranscriber(["hey did you book the flights"])
    source = FakeAudioSource.from_pattern([("silence", 3), ("speech", 10), ("silence", 4)])
    mic = MicSource(source=source, gate=gate, transcriber=transcriber, vad=_vad(gate))

    responder, voice = FakeResponder(), FakeVoice()
    layer = AttentionLayer.build(gate=gate, now=clock.now, responder=responder, voice=voice)
    layer.run(mic)  # MicSource satisfies the TranscriptSource seam run() expects

    # The live utterance reached ASR and flowed through the orchestrator with no
    # change to the core: MicSource plugged in exactly where ScriptedSource did.
    assert len(transcriber.calls) == 1


# -- a segment still open at end-of-stream is flushed ------------------------


def test_open_segment_is_flushed_at_end_of_stream() -> None:
    # The stream ends while still in speech (no trailing silence to fire the
    # speech-end edge). MicSource must flush the open segment so the final
    # utterance isn't lost.
    transcriber = FakeTranscriber(["cut off mid sentence"])
    mic = _mic_source([("silence", 3), ("speech", 10)], transcriber)

    utts = list(mic.utterances())

    assert len(utts) == 1
    assert utts[0].text == "cut off mid sentence"


# -- the real transcriber's contract (no model loaded) -----------------------


def test_mlx_whisper_transcriber_rejects_wrong_sample_rate() -> None:
    # Guards the 16 kHz contract before ever touching the (lazy) model — so this
    # raises without loading mlx-whisper.
    t = MlxWhisperTranscriber()
    import pytest

    with pytest.raises(ValueError, match="16 kHz"):
        t.transcribe(np.zeros(512, dtype=np.float32), sample_rate=44_100)
