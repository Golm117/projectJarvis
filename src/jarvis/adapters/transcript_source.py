"""TranscriptSource seam + ScriptedSource (T-008).

``TranscriptSource`` is the **in** boundary of the ambient pipeline: it yields
``Utterance`` events and nothing else, so the core never knows whether they came
from a microphone or a script (module map §"The I/O adapter seams"). In Phase 0
the only implementation is ``ScriptedSource``; in Phase 1 sensing-engineer's
``MicSource`` (mic → Silero VAD → ASR) drops in behind the same Protocol (T-104).

## Why ScriptedSource carries timing

The orchestrator's Path B (interjection) only fires once the ``TurnTakingGate``'s
**politeness gap** (~2 s of silence) has elapsed — and the gate measures silence
off an **injected clock** (no real ``sleep``, the single-clock constraint). So a
canned conversation that is just a list of lines can never produce an
interjection: no time passes between lines, so the gap never opens.

``ScriptedSource`` closes that by carrying an **inter-line gap** per line and
driving the clock + the gate's speech boundary events as it yields. Each line is
a ``ScriptedLine(speaker, text, gap)``; ``gap`` is the seconds of silence that
follow the line before the next one. As the source plays a line it:

1. fires ``on_speech_start()`` / ``on_speech_end()`` on the gate (the VAD edges a
   real ``MicSource`` would emit),
2. advances the injected clock by ``gap`` seconds (the silence after the line),
3. stamps the ``Utterance.ts`` from the clock.

That makes the whole pipeline — summary refresh, wall detection, and the
politeness-gap-gated interjection — run **deterministically** off the simulated
clock with zero real time elapsed. In tests the clock is a ``SimulatedClock``; in
the runnable demo it is the same simulated clock (the demo is not real-time — it
prints the events a real conversation would produce).

This source is **pure** in the no-I/O sense (it reads no audio); the only effects
are advancing the injected clock and feeding the injected gate, both of which are
explicit constructor dependencies.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Protocol

from jarvis.core.turn_taking_gate import TurnTakingGate
from jarvis.types import Utterance

# The default speech duration stamped for a scripted line — the modeled length of
# the spoken turn itself (between its on_speech_start and on_speech_end), distinct
# from the silence ``gap`` that follows it. Small and fixed; the gap is what the
# politeness logic actually reasons about.
DEFAULT_SPEECH_SECONDS = 0.4


class TranscriptSource(Protocol):
    """The transcript **in** seam — yields ``Utterance`` events.

    A real STT mic adapter (``MicSource``, sensing-engineer) and the Phase-0
    ``ScriptedSource`` both satisfy this. The orchestrator consumes the iterable
    one ``Utterance`` at a time; whether it is canned or live is invisible to it.
    """

    def utterances(self) -> Iterable[Utterance]: ...


@dataclass(frozen=True)
class ScriptedLine:
    """One line of a canned conversation, with the silence that follows it.

    Fields:
        speaker: who spoke.
        text: the transcribed words.
        gap: seconds of silence *after* this line before the next (the
            inter-line timing). This is what lets the ``TurnTakingGate``'s
            politeness gap elapse deterministically: a line followed by a ``gap``
            of >= the politeness-gap threshold opens the window for a Path-B
            interjection. Defaults to ``0.0`` (lines tumble out back-to-back, no
            opening — the conversational-overlap case).
    """

    speaker: str
    text: str
    gap: float = 0.0


# A scripted line may be given as a full ``ScriptedLine`` or as a bare
# ``(speaker, text)`` / ``(speaker, text, gap)`` tuple for convenience.
ScriptedLineInput = ScriptedLine | tuple[str, str] | tuple[str, str, float]


def _coerce(line: ScriptedLineInput) -> ScriptedLine:
    if isinstance(line, ScriptedLine):
        return line
    if len(line) == 2:
        speaker, text = line
        return ScriptedLine(speaker=speaker, text=text)
    speaker, text, gap = line
    return ScriptedLine(speaker=speaker, text=text, gap=gap)


class ScriptedSource:
    """A canned conversation that drives the injected clock + gate as it plays.

    Args:
        lines: the conversation, as ``ScriptedLine`` objects or bare
            ``(speaker, text)`` / ``(speaker, text, gap)`` tuples.
        clock_advance: the injected clock's *advance* hook — a callable taking a
            number of seconds (``SimulatedClock.advance``). Called with each
            line's silence ``gap`` so the gate's silence timer elapses without a
            real ``sleep``.
        now: the injected clock read — a zero-arg callable returning the current
            monotonic seconds (``SimulatedClock.now``). Used to stamp each
            ``Utterance.ts`` so the ``RollingWindow``'s time eviction lines up
            with the same clock the gate runs on.
        gate: the orchestrator's ``TurnTakingGate``. The source feeds it the
            speech-start / speech-end edges a real VAD would, so the gate is
            armed and its silence clock matches the conversation's pacing. (The
            orchestrator and the source share one gate instance.)
        speech_seconds: the modeled duration of each spoken line itself (between
            its start and end edges). Defaults to ``DEFAULT_SPEECH_SECONDS``.

    The orchestrator typically constructs this for you via
    ``AttentionLayer.run_scripted`` — see ``jarvis.attention_layer``.
    """

    def __init__(
        self,
        lines: Iterable[ScriptedLineInput],
        clock_advance: Callable[[float], object],
        now: Callable[[], float],
        gate: TurnTakingGate,
        speech_seconds: float = DEFAULT_SPEECH_SECONDS,
    ) -> None:
        if speech_seconds < 0:
            raise ValueError(f"speech_seconds must be >= 0, got {speech_seconds}")
        self._lines = [_coerce(line) for line in lines]
        self._advance = clock_advance
        self._now = now
        self._gate = gate
        self._speech_seconds = float(speech_seconds)

    def utterances(self) -> Iterator[Utterance]:
        """Yield each scripted ``Utterance``, advancing clock + gate around it.

        Per line: open speech (``on_speech_start``), advance the clock by the
        modeled speech duration, stamp the utterance ``ts`` and yield it, close
        speech (``on_speech_end``), then advance the clock by the line's silence
        ``gap`` so the gate's silence timer reflects the pause before the next
        line. The orchestrator ingests the yielded utterance *after* its speech
        ends — so by the time Path B is evaluated, the post-line silence the
        ``gap`` represents has elapsed and the gate's predicates read correctly.
        """
        for line in self._lines:
            self._gate.on_speech_start()
            if self._speech_seconds:
                self._advance(self._speech_seconds)
            ts = self._now()
            self._gate.on_speech_end()
            # The silence after the line — this is what lets the politeness gap
            # elapse (or not) deterministically before the next line arrives.
            if line.gap:
                self._advance(line.gap)
            yield Utterance(speaker=line.speaker, text=line.text, ts=ts)
