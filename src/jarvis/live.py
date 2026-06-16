"""``run_live`` — the real ambient pipeline on live audio (T-105, ``python -m jarvis --live``).

This is the Phase-1 live smoke test wiring: the **real** ``AttentionLayer`` driven
by a **real** ``MicSource`` — real microphone (``SoundDeviceMicSource``) → real
Silero VAD → real mlx-whisper ``base.en`` — feeding ``Utterance`` events through
the same orchestrator the Phase-0 mock demo used. The summarizer / wall backends
stay the Phase-0 heuristics (Qwen2.5/MLX is Phase 2, behind those seams); the
engaged path is the print stand-ins.

Unlike the mock demo (``jarvis.demo``), this runs in **real time** on a real
``time.monotonic`` clock — the gate's settle / politeness gaps elapse against the
wall clock, and the VAD stamps each ``Utterance.ts`` from the audio timeline. It
captures for a bounded window (``seconds``) and then stops, so it is a *smoke
test*, not a forever-loop (that's Phase 5, T-501).

## Generating speech without a human (the `say` loopback)

For a reproducible run with no human, call ``run_live(say_text=...)``: it speaks
the text through the macOS ``say`` command (out the speakers) while the mic
captures it, so the pipeline transcribes real acoustic audio — the same loopback
the T-101 ASR spike used. A human "speak and watch" run is the same entry point
with ``say_text=None``: start it, talk (say "Jarvis ..." to test a summon, or ask
an unanswered question to test an interjection), watch the printed events.

**This module is never imported by the test suite** and the mic deps are imported
lazily, so ``uv run pytest`` stays green and CI never touches a microphone.
"""

from __future__ import annotations

import contextlib
import subprocess
import threading
import time
from collections.abc import Callable

from jarvis.adapters.engaged import PrintResponder, PrintVoice
from jarvis.attention_layer import AttentionLayer
from jarvis.core.turn_taking_gate import DEFAULT_POLITENESS_GAP_SECONDS, TurnTakingGate
from jarvis.types import EngagementHandoff, Interjection, Utterance

DEFAULT_LISTEN_SECONDS = 12.0

# How long the Path-B trailing re-check waits for real silence to open the gate's
# politeness gap (the gate's default ~2 s, used by run_live's smoke-test affordance).
POLITENESS_GAP_SETTLE = DEFAULT_POLITENESS_GAP_SECONDS


def _say_async(text: str, delay: float = 1.0) -> threading.Thread:
    """Speak ``text`` via macOS ``say`` on a background thread (after ``delay`` s).

    The short delay lets the capture loop start before audio plays, so the opening
    words aren't clipped. Returns the thread (so the caller can join it).
    """

    def _run() -> None:
        time.sleep(delay)
        with contextlib.suppress(FileNotFoundError):  # not macOS / no `say`
            subprocess.run(["say", text], check=False)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def run_live(
    *,
    seconds: float = DEFAULT_LISTEN_SECONDS,
    say_text: str | None = None,
    device: int | str | None = None,
    stop_after_text: str | None = None,
    now: Callable[[], float] | None = None,
    mic_factory: Callable[[], object] | None = None,
) -> list[Utterance]:
    """Run the real ambient pipeline on live mic audio for a bounded window.

    Wires ``AttentionLayer`` (heuristic backends + print engaged path) to a real
    ``MicSource`` over a ``SoundDeviceMicSource``, capturing for ``seconds`` and
    printing every transcript line + emitted event. If ``say_text`` is given, it is
    spoken through the macOS ``say`` command (the human-free loopback) while the mic
    listens.

    Args:
        seconds: how long to capture before stopping.
        say_text: text to speak via ``say`` for the loopback (``None`` = a human
            speaks).
        device: the PortAudio input device id/name to capture from (``None`` =
            system default input). For the human-free ``say`` loopback a virtual
            audio cable (e.g. ``BlackHole 2ch``) gives a clean digital path with no
            acoustic echo — pass its device index here and route ``say``'s output to
            it (the smoke-test doc explains the wiring).
        stop_after_text: if given, stop capturing the moment a transcribed
            utterance contains this substring (case-insensitive). Used by the
            Path-B smoke test to end capture cleanly on the wall-bearing line, so
            the trailing-silence re-check (below) sees that line as the window's
            last line instead of a later stray segment.
        now: the gate's clock (default ``time.monotonic`` — real time).
        mic_factory: builds the real mic source (default a ``SoundDeviceMicSource``);
            injectable so a harness could substitute one. The mic deps are imported
            lazily here, never at module import.

    Returns the list of ``Utterance`` the pipeline transcribed (for the caller to
    report on). Raises the typed mic errors (``MicPermissionError`` /
    ``NoInputDeviceError``) if the mic can't be opened — never fabricates audio.
    """
    from jarvis.audio.mic import SoundDeviceMicSource
    from jarvis.audio.mic_source import MicSource

    if now is None:
        now = time.monotonic
    gate = TurnTakingGate(now)

    transcribed: list[Utterance] = []

    def on_utterance(u: Utterance) -> None:
        transcribed.append(u)
        print(f"[transcript @ {u.ts:6.2f}s] {u.speaker}: {u.text}")

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
        print("   " + "-" * 60 + "\n")

    layer = AttentionLayer.build(
        gate=gate,
        now=now,
        responder=PrintResponder(),
        voice=PrintVoice(prefix="      jarvis  : "),
        on_summary_update=on_summary,
        on_interjection=on_interjection,
        on_engagement=on_engagement,
    )

    print("=" * 70)
    print("  Project Jarvis — LIVE ambient pipeline (real mic · Silero · mlx-whisper)")
    print("  Backends: MOCK heuristics (Qwen2.5 is Phase 2) · engaged path: print")
    print(f"  Listening for {seconds:.0f}s …")
    if say_text:
        print(f'  Loopback: speaking via `say`: "{say_text}"')
    print("=" * 70)

    mic = mic_factory() if mic_factory is not None else SoundDeviceMicSource(device=device)
    say_thread: threading.Thread | None = None
    try:
        mic.start()  # type: ignore[attr-defined]  # opens the device (permission prompt)
        if say_text:
            say_thread = _say_async(say_text)
        # Stamp Utterance.ts from the SAME real clock the gate + window run on, so
        # the RollingWindow doesn't evict every live utterance (a frame-derived ts
        # of ~9 s would look ~400000 s stale against time.monotonic). See MicSource.
        mic_source = MicSource(source=mic, gate=gate, now=now)  # type: ignore[arg-type]

        deadline = time.monotonic() + seconds
        # Stop the mic from a watchdog thread when the window elapses, which ends
        # the frames() generator so the loop below returns.
        stopper = threading.Timer(seconds, mic.stop)  # type: ignore[attr-defined]
        stopper.daemon = True
        stopper.start()

        target = stop_after_text.lower() if stop_after_text else None
        last: Utterance | None = None
        for u in mic_source.utterances():
            on_utterance(u)
            layer.ingest(u)
            last = u
            if target is not None and target in u.text.lower():
                break  # clean stop on the wall-bearing line (leave the gate armed)
            if time.monotonic() >= deadline:
                break
        mic.stop()  # type: ignore[attr-defined]  # no more capture → no trailing junk

        # --- Path-B trailing re-check (smoke-test affordance) ----------------
        # The v0 orchestrator evaluates Path B once, at an utterance's ingest —
        # but at that instant only the VAD's ~200 ms endpoint hangover of silence
        # has passed, never the gate's ~2 s politeness gap, so a live interjection
        # can't fire from a *single* per-utterance pass. (Re-evaluating Path B
        # continuously as silence accumulates is the Phase-3 real-time
        # SummonController, T-302.) To show the interjection path firing on *live*
        # audio here, we stop capturing on the wall line, let real silence elapse
        # (no new speech, so the gate's silence timer keeps growing from that
        # line's speech-end), and re-ingest it once the politeness gap has opened.
        # This uses only the public ``ingest`` and the same gate — it does not
        # reach into orchestrator internals or fabricate anything.
        from jarvis.attention_layer import WAKE_WORD

        last_was_summon = last is not None and WAKE_WORD in last.text.lower()
        if last is not None and not last_was_summon and not gate.speech_resumed():
            time.sleep(POLITENESS_GAP_SETTLE + 0.3)  # let the ~2 s gap open for real
            if gate.politeness_gap_elapsed():
                print("   [trailing silence >= politeness gap — re-checking Path B]")
                layer.ingest(last)
    finally:
        mic.stop()  # type: ignore[attr-defined]
        if say_thread is not None:
            say_thread.join(timeout=1.0)

    print("\n" + "=" * 70)
    print(f"  Live run complete — {len(transcribed)} utterance(s) transcribed.")
    print("=" * 70)
    return transcribed
