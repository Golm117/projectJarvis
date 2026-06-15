"""project-jarvis — a local, always-on desktop assistant.

This package is the real home of the attention layer: the pure-logic ambient
core (rolling window, living summary, wall detection, turn-taking gate, dual
summon state machine, orchestrator) plus the thin I/O adapter seams the audio,
local-ML, and voice integrations plug into.

Phase 0 stands up the package, its tooling, and the module-boundary contract
(see ``docs/architecture/module-map.md``). The pure-logic modules and the
end-to-end mock pipeline are ported in deliberately in later Phase 0 tasks
(T-002…T-008); the runnable reference lives at ``prototypes/attention-layer/``.

The microphone, the cloud LLM, and the voice service are all *boundaries* — the
core attention logic never touches them directly (PRD 02, "one pipeline, two
halves").
"""

from __future__ import annotations

__version__ = "0.0.0"

__all__ = ["__version__"]
