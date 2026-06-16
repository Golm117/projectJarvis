"""``python -m jarvis`` — run the Phase-0 mock attention-layer demo (T-008).

Plays a scripted conversation through the real ``AttentionLayer`` in mock mode
(no audio, no model, no network) and prints the events it emits: living-summary
updates, a proactive interjection, and a wake-word summon → ``EngagementHandoff``.
See ``jarvis.demo`` for the conversation and wiring.
"""

from __future__ import annotations

from jarvis.demo import run_demo

if __name__ == "__main__":
    run_demo()
