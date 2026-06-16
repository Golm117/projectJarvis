"""``run_live`` — the real ambient pipeline on live audio (T-105 / T-204 / T-302).

Phase 1 (T-105): the **real** ``AttentionLayer`` driven by a **real** ``MicSource``
— real microphone (``SoundDeviceMicSource``) → real Silero VAD → real mlx-whisper
``base.en`` — feeding ``Utterance`` events through the same orchestrator the
Phase-0 mock demo used.

Phase 2 (T-204): the local **Qwen2.5/MLX backends** are now wired behind the
frozen seams for the ``--live`` path.  Backend selection:

* **Default (mock/heuristic) — ``--mock-brain``:** the heuristic
  ``HeuristicSummarizerBackend`` / ``HeuristicWallBackend`` are used (no model
  load).  This is still the default so that ``python -m jarvis`` (the mock demo)
  and ``uv run pytest`` remain model-free.
* **Local Qwen brain — ``--local-brain``:** one shared ``QwenModel()`` is
  constructed at startup and injected into both ``QwenSummarizerBackend`` and
  ``QwenWallBackend``; the ~2 GB weights are loaded once and shared.  This is the
  real Phase-2 backend.

Phase 3 (T-302): **continuous real-time Path-B re-evaluation** during silence.
The ``MicSource.utterances()`` generator blocks on ``source.frames()`` during
silence — ``AttentionLayer.ingest`` never runs, so Path B's
``SummonController.consider_interjection`` is never called while the politeness gap
grows.  A **daemon ticker thread** calls ``layer.tick()`` every
``TICK_INTERVAL_SECONDS`` (~200 ms) throughout the listen window, allowing the wall
interjection to fire mid-conversation once the gate's politeness gap opens.

**Thread-safety model (T-302):**
A single ``threading.Lock`` (``_layer_lock``) serialises all access to ``layer``:
* The utterance-consumer thread (the ``for u in mic_source.utterances()`` loop)
  holds the lock around each ``layer.ingest(u)`` call.
* The ticker thread holds the lock around each ``layer.tick()`` call.
``AttentionLayer`` and ``SummonController`` stay single-threaded pure logic — the
lock lives here in ``live.py``, not in the core modules.

The ``AttentionLayer.build`` signature already accepts ``summarizer_backend`` and
``wall_backend`` keyword arguments; this module passes the right pair based on the
flag.  Zero core module changes.

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
from jarvis.core.turn_taking_gate import TurnTakingGate
from jarvis.types import EngagementHandoff, Interjection, Utterance

DEFAULT_LISTEN_SECONDS = 12.0

# T-302: cadence of the background ticker thread that calls layer.tick() during
# silence.  200 ms gives ~10 ticks per politeness gap (2 s) — responsive without
# busy-polling.  Chosen to be well under the gap yet not tight enough to spin-waste
# a core; the actual fire latency is at most TICK_INTERVAL_SECONDS after the gap
# opens, which is negligible vs the 2 s gap itself.
TICK_INTERVAL_SECONDS = 0.20


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


def _build_local_brain_backends() -> tuple[object, object]:
    """Construct one shared ``QwenModel`` and return both Qwen backends.

    The ~2 GB weights are loaded **once** on the first inference call; both
    backends share the same model instance so the weights are never double-loaded.
    All MLX imports are lazy (inside ``QwenModel._ensure_loaded``) — calling this
    function before any inference never triggers a model load.

    Returns:
        A ``(summarizer_backend, wall_backend)`` pair ready to inject into
        ``AttentionLayer.build``.
    """
    # Local imports keep the top-level module free of unconditional ML imports;
    # importing jarvis.ml.* is safe (no MLX load), but we keep the import here
    # to mirror the lazy-import discipline of the audio side.
    from jarvis.ml.qwen import QwenModel
    from jarvis.ml.summarizer import QwenSummarizerBackend
    from jarvis.ml.wall import QwenWallBackend

    shared_model = QwenModel()  # one instance — shared by both backends
    summarizer = QwenSummarizerBackend(shared_model)
    wall = QwenWallBackend(shared_model)
    return summarizer, wall


def run_live(
    *,
    seconds: float = DEFAULT_LISTEN_SECONDS,
    say_text: str | None = None,
    device: int | str | None = None,
    stop_after_text: str | None = None,
    now: Callable[[], float] | None = None,
    mic_factory: Callable[[], object] | None = None,
    local_brain: bool = False,
) -> list[Utterance]:
    """Run the real ambient pipeline on live mic audio for a bounded window.

    Wires ``AttentionLayer`` to a real ``MicSource`` over a
    ``SoundDeviceMicSource``, capturing for ``seconds`` and printing every
    transcript line + emitted event. If ``say_text`` is given, it is spoken
    through the macOS ``say`` command (the human-free loopback) while the mic
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
        local_brain: if ``True``, wire the real Qwen2.5/MLX summarizer and wall
            backends (one shared ``QwenModel`` instance, weights loaded once on the
            first inference call).  If ``False`` (default), use the heuristic mock
            backends — no model load, suitable for the quick sanity-check use of
            ``--live`` without the SLM.

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

    # Backend selection (T-204): default = heuristic mock (model-free);
    # local_brain=True = one shared QwenModel → both Qwen backends.
    summarizer_backend = None  # None → AttentionLayer.build uses HeuristicSummarizerBackend
    wall_backend = None  # None → AttentionLayer.build uses HeuristicWallBackend
    if local_brain:
        summarizer_backend, wall_backend = _build_local_brain_backends()

    layer = AttentionLayer.build(
        gate=gate,
        now=now,
        responder=PrintResponder(),
        voice=PrintVoice(prefix="      jarvis  : "),
        summarizer_backend=summarizer_backend,
        wall_backend=wall_backend,
        on_summary_update=on_summary,
        on_interjection=on_interjection,
        on_engagement=on_engagement,
    )

    brain_label = "Qwen2.5-3B/MLX (local brain)" if local_brain else "MOCK heuristics"
    print("=" * 70)
    print("  Project Jarvis — LIVE ambient pipeline (real mic · Silero · mlx-whisper)")
    print(f"  Backends: {brain_label} · engaged path: print")
    print(f"  Listening for {seconds:.0f}s …")
    if say_text:
        print(f'  Loopback: speaking via `say`: "{say_text}"')
    print("=" * 70)

    mic = mic_factory() if mic_factory is not None else SoundDeviceMicSource(device=device)
    say_thread: threading.Thread | None = None

    # T-302: one lock serialises layer.ingest() (utterance thread) and
    # layer.tick() (ticker thread) so AttentionLayer stays single-threaded.
    _layer_lock = threading.Lock()

    ticker_stop = threading.Event()

    def _ticker() -> None:
        """Background daemon: calls layer.tick() at TICK_INTERVAL_SECONDS cadence.

        Runs for the entire listen window.  A separate stop event (``ticker_stop``)
        lets the main thread halt it cleanly before the function returns.  The lock
        ensures tick() and ingest() never interleave.
        """
        while not ticker_stop.is_set():
            ticker_stop.wait(TICK_INTERVAL_SECONDS)
            if ticker_stop.is_set():
                break
            with _layer_lock:
                layer.tick()

    ticker_thread = threading.Thread(target=_ticker, daemon=True, name="jarvis-ticker")

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

        # T-302: start the ticker *after* the mic is up so the first tick sees a
        # live gate (not the cold initial state).
        ticker_thread.start()

        target = stop_after_text.lower() if stop_after_text else None
        for u in mic_source.utterances():
            on_utterance(u)
            with _layer_lock:
                layer.ingest(u)
            if target is not None and target in u.text.lower():
                break  # clean stop on the wall-bearing line (leave the gate armed)
            if time.monotonic() >= deadline:
                break

        # T-302: the real continuous loop keeps ticking after the utterance loop
        # ends (e.g. stop_after_text hit) so Path B can still fire during the
        # trailing silence.  We let it run until the listen window expires.
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

        mic.stop()  # type: ignore[attr-defined]  # no more capture → no trailing junk

    finally:
        # Signal the ticker to stop and wait for it to exit cleanly.
        ticker_stop.set()
        if ticker_thread.is_alive():
            ticker_thread.join(timeout=1.0)
        mic.stop()  # type: ignore[attr-defined]
        if say_thread is not None:
            say_thread.join(timeout=1.0)

    print("\n" + "=" * 70)
    print(f"  Live run complete — {len(transcribed)} utterance(s) transcribed.")
    print("=" * 70)
    return transcribed
