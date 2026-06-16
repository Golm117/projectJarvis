"""Runnable mock demo — the ambient pipeline end-to-end, no model, no network (T-008).

``python -m jarvis`` (or ``jarvis.demo.run_demo()``) plays a scripted conversation
through the **real** ``AttentionLayer`` in mock mode and prints the events the
pipeline emits: living-summary updates, a proactive interjection (Path B), and a
wake-word summon → ``EngagementHandoff`` (Path A). It mirrors the reference
prototype's ``--demo`` output, but on the real package's modules and the
decision/handoff boundary rather than the prototype's monolithic ``Backend``.

It runs on a ``SimulatedClock`` — the demo is **not** real-time; it prints the
events a real conversation *would* produce, deterministically and instantly. The
``ScriptedSource`` drives that clock + the ``TurnTakingGate``, so the politeness
gap before the interjection elapses exactly as it would live.

No audio, no network, no API key: the summarizer + wall backends are the Phase-0
heuristics and the engaged path is the print stand-ins.
"""

from __future__ import annotations

from jarvis.adapters.engaged import PrintResponder, PrintVoice
from jarvis.adapters.transcript_source import ScriptedLine
from jarvis.attention_layer import WAKE_WORD, AttentionLayer
from jarvis.clock import ManualClock
from jarvis.core.summon_controller import DEFAULT_INTERJECTION_CONFIDENCE_FLOOR
from jarvis.core.turn_taking_gate import DEFAULT_POLITENESS_GAP_SECONDS
from jarvis.types import EngagementHandoff, Interjection, Utterance

# The scripted conversation, ported from the prototype's DEMO_CONVERSATION and
# annotated with inter-line silence ``gap``s so the timing-gated paths fire
# deterministically. The gap on the "what was the conference date" line is >= the
# politeness gap, so the factual_gap wall there clears the gate and Path B fires;
# the conversation then pivots topic (ramen) — driving a second living-summary
# update — and ends on the wake word (Path A summon).
_GAP = DEFAULT_POLITENESS_GAP_SECONDS + 0.5  # comfortably clears the politeness gap

DEMO_CONVERSATION: list[ScriptedLine] = [
    ScriptedLine("Alex", "Hey, did you book the flights for the Tokyo trip yet?", gap=0.5),
    ScriptedLine("Sam", "Not yet, I keep forgetting which week we settled on.", gap=0.5),
    ScriptedLine("Alex", "I think it was the second week of October.", gap=0.5),
    # A clear factual gap, followed by a long enough silence to open the
    # politeness gap → Path B interjection fires here.
    ScriptedLine("Sam", "Wait, what was the date of the conference again?", gap=_GAP),
    ScriptedLine("Alex", "Good question. Anyway, totally different thing —", gap=0.4),
    ScriptedLine("Alex", "have you tried that new ramen place on 4th street?", gap=0.5),
    ScriptedLine("Sam", "Oh the tonkotsu spot? Incredible. We should go this weekend.", gap=0.5),
    # Wake word → Path A summon, immediate and unconditional.
    ScriptedLine("Alex", "Let's do Saturday. Jarvis, add that to my calendar for 7.", gap=0.0),
]


def _banner() -> None:
    print("=" * 70)
    print("  Project Jarvis — Attention Layer (real package, MOCK mode)")
    print("  Backend: MOCK (heuristics)   |   no audio · no model · no network")
    print(
        f"  Wake word: '{WAKE_WORD}'   |   "
        f"speak-threshold: {DEFAULT_INTERJECTION_CONFIDENCE_FLOOR}   |   "
        f"politeness gap: {DEFAULT_POLITENESS_GAP_SECONDS}s"
    )
    print("=" * 70)


def run_demo() -> None:
    """Play the scripted conversation through the real ``AttentionLayer`` (mock)."""
    # The deterministic clock the whole demo runs on — manually advanced by the
    # ScriptedSource as it plays, so the politeness gap elapses with no real sleep.
    clock = ManualClock()
    _banner()

    def on_utterance(u: Utterance) -> None:
        print(f"{u.speaker}: {u.text}")

    def on_summary(text: str) -> None:
        print(f"\n   [living summary updated] {text}\n")

    def on_interjection(i: Interjection) -> None:
        print(
            f"\n   >> JARVIS (interjecting, {i.category.value} @ {i.confidence:.2f}): {i.offer}\n"
        )

    def on_engagement(h: EngagementHandoff) -> None:
        print("\n   " + "-" * 60)
        print(f"   ** ENGAGEMENT  (trigger: {h.trigger_reason})")
        print(f"      summary : {h.summary or '(none yet)'}")
        if h.detail:
            print(f"      detail  : {h.detail}")

    AttentionLayer.run_scripted(
        DEMO_CONVERSATION,
        now=clock.now,
        clock_advance=clock.advance,
        responder=PrintResponder(),
        voice=PrintVoice(prefix="      jarvis  : "),
        # A tighter window than production's default so the Tokyo-trip → ramen
        # pivot ages the old topic out and registers as a shift — the demo then
        # shows the living summary genuinely *redrawing on a shift* (two updates),
        # not just the cold-start one. (The T-004 window-sizing gotcha made
        # visible: a wide window holding both topics would keep overlap high and
        # mask the pivot.) The interjection still fires regardless.
        max_utterances=5,
        on_utterance=on_utterance,
        on_summary_update=on_summary,
        on_interjection=on_interjection,
        on_engagement=on_engagement,
    )
    print("\n" + "=" * 70)
    print("  Demo complete — ambient → summary → wall → dual-summon ran end-to-end.")
    print("=" * 70)
