"""I/O adapter seams — where the non-core agents plug in (T-008).

The pure-logic core (``src/jarvis/core/``) never touches a microphone, a model,
a socket, or a speaker. It talks only to the **seams** defined here, so the same
``AttentionLayer`` orchestrator runs in two configurations without a line of core
changing (module map §"The I/O adapter seams"):

* **Phase 0 — mock:** ``ScriptedSource`` feeds a canned conversation,
  ``HeuristicSummarizerBackend`` + ``HeuristicWallBackend`` stand in for the local
  model, and ``PrintResponder`` / ``PrintVoice`` (or the test fakes) stand in for
  the engaged path. No audio, no model, no network.
* **Live:** ``MicSource`` (sensing-engineer, T-104) replaces ``ScriptedSource``,
  the Qwen2.5/MLX backends (local-ml-engineer, T-202/T-203) replace the heuristics,
  and the Claude + ElevenLabs adapters (voice-integration-engineer, T-401/T-402)
  replace the print stand-ins — all behind these same Protocols.

Seam → where it's filled (module map "Ownership" table):

    TranscriptSource.utterances() -> Iterable[Utterance]   core: ScriptedSource · sensing: Mic
    SummarizerBackend.summarize(transcript, prev) -> str   core: heuristic · local-ml: Qwen2.5/MLX
    WallBackend.detect_wall(transcript, summary) -> Verdict core: heuristic · local-ml: Qwen2.5/MLX
    EngagedResponder.respond(handoff) -> str               core: print/fake · voice: Claude
    VoiceOutput.speak(text) -> None                        core: print/fake · voice: ElevenLabs
"""

from __future__ import annotations

from jarvis.adapters.backends import (
    HeuristicSummarizerBackend,
    SummarizerBackend,
    WallBackend,
)
from jarvis.adapters.engaged import (
    EngagedResponder,
    PrintResponder,
    PrintVoice,
    VoiceOutput,
)
from jarvis.adapters.transcript_source import (
    ScriptedLine,
    ScriptedSource,
    TranscriptSource,
)

__all__ = [
    "TranscriptSource",
    "ScriptedSource",
    "ScriptedLine",
    "SummarizerBackend",
    "WallBackend",
    "HeuristicSummarizerBackend",
    "EngagedResponder",
    "VoiceOutput",
    "PrintResponder",
    "PrintVoice",
]
