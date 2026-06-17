"""``run_live`` — the real ambient pipeline on live audio (T-105 / T-204 / T-302 / T-404 / T-501).

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

Phase 4 (T-404): **real voice** — Claude ``claude-opus-4-8`` + ElevenLabs streaming
TTS behind the ``--voice`` flag.  ``load_dotenv()`` is called at the live entry so
``ANTHROPIC_API_KEY`` and ``ELEVENLABS_API_KEY`` are picked up from ``.env``.  The
``VoiceSession`` acts as the ``EngagedResponder`` (streaming respond + speak in one
call); a no-op ``PrintVoice`` is passed as the ``VoiceOutput`` (since speaking is
already done inside ``respond()``).  Default stays ``PrintResponder``/``PrintVoice``
(no keys needed) so ``--live`` without ``--voice`` remains model-free.

Phase 5 (T-501): **always-on mode + graceful shutdown + bounded memory.**
Two run modes exist:

* **Bounded (default):** ``--seconds N`` (default 12) — the original smoke-test
  window.  A ``threading.Timer`` stops the mic after ``N`` seconds.  The function
  returns the full list of transcribed ``Utterance`` objects (the existing smoke-test
  contract).  Unchanged from pre-T-501.
* **Always-on:** ``--forever`` (or ``seconds=0``) — no timer, no deadline.  The
  loop runs until a shutdown signal (SIGINT / SIGTERM / ``KeyboardInterrupt``).  A
  shutdown event (``_shutdown_event``) is set by the signal handler; the utterance
  loop checks it and exits; the finally block joins the ticker and mic.
  Utterances are accumulated in a bounded ``deque`` (``FOREVER_DEQUE_MAXLEN``)
  rather than a growing list, so memory is capped even over a multi-hour run.
  The function returns ``None`` (no accumulation contract for the always-on path).

**Graceful shutdown (always-on path):**
SIGINT and SIGTERM are caught in a signal handler that sets ``_shutdown_event``.
``KeyboardInterrupt`` (a Ctrl-C before the signal handler fires in the utterance
loop) is caught explicitly in ``run_live`` and treated the same way.  The
shutdown sequence — set event → ticker stop → mic stop → say thread join — runs
inside the ``finally`` block whether shutdown came from a signal, a KeyboardInterrupt,
or the normal window end.  The process exits 0 with a clean ``[live] stopping …``
message and no traceback.

**Thread-safety model (T-302, unchanged):**
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
wall clock, and the VAD stamps each ``Utterance.ts`` from the audio timeline.

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

import collections
import contextlib
import signal
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

# T-501: bounded accumulation cap for the always-on path.
# In always-on mode the `transcribed` list is replaced with a bounded deque of this
# size.  Only the most recent N utterances are retained — enough to reconstruct the
# tail of any conversation session without growing without bound.
# At a generous 1 utterance / 5 s cadence, 1000 utterances ≈ ~83 minutes of tail.
# The bounded `--seconds` path is unaffected (still uses a plain list).
FOREVER_DEQUE_MAXLEN = 1000


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


def _build_voice_session() -> object:
    """Construct a ``VoiceSession`` (ClaudeResponder + ElevenLabsVoice).

    Called only when ``--voice`` is passed.  ``load_dotenv()`` must have been
    called before this so ``ANTHROPIC_API_KEY`` and ``ELEVENLABS_API_KEY`` are
    in the environment.  The clients are created lazily inside the adapters on
    first call — this function just assembles the session object.

    Returns:
        A ``VoiceSession`` instance (satisfies ``EngagedResponder`` via its
        ``respond()`` method, which streams Claude → ElevenLabs internally).
    """
    from jarvis.adapters.claude_responder import ClaudeResponder
    from jarvis.adapters.elevenlabs_voice import ElevenLabsVoice
    from jarvis.adapters.voice_session import VoiceSession

    return VoiceSession(
        responder=ClaudeResponder(),  # client lazy-created from ANTHROPIC_API_KEY
        voice=ElevenLabsVoice(),  # client lazy-created from ELEVENLABS_API_KEY
    )


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
    forever: bool = False,
    say_text: str | None = None,
    device: int | str | None = None,
    stop_after_text: str | None = None,
    now: Callable[[], float] | None = None,
    mic_factory: Callable[[], object] | None = None,
    local_brain: bool = False,
    real_voice: bool = False,
    capture_path: str | None = None,
    _shutdown_event: threading.Event | None = None,
) -> list[Utterance] | None:
    """Run the real ambient pipeline on live mic audio.

    Two modes:

    **Bounded mode** (default, ``forever=False``): captures for ``seconds`` and
    stops.  Returns the list of ``Utterance`` objects transcribed.  The smoke tests
    and quick checks use this mode.  ``seconds=0`` is treated as ``forever=True``.

    **Always-on mode** (``forever=True`` or ``seconds=0``): runs indefinitely until
    a shutdown signal (SIGINT / SIGTERM / ``KeyboardInterrupt``).  Installs a signal
    handler for the duration of the call that sets a shutdown event, then uninstalls
    it on exit.  Utterances are kept in a bounded ``deque`` of
    ``FOREVER_DEQUE_MAXLEN`` entries to cap memory for multi-hour runs.  Returns
    ``None`` (the caller has no meaningful accumulation to inspect).

    Args:
        seconds: how long to capture before stopping (bounded mode).  ``0`` means
            always-on (treated as ``forever=True``).
        forever: if ``True``, run until signalled — no timer.
        say_text: text to speak via ``say`` for the loopback (``None`` = a human
            speaks).
        device: the PortAudio input device id/name to capture from (``None`` =
            system default input).
        stop_after_text: if given, stop capturing the moment a transcribed
            utterance contains this substring (case-insensitive). Used by the
            Path-B smoke test to end capture cleanly on the wall-bearing line.
        now: the gate's clock (default ``time.monotonic`` — real time).
        mic_factory: builds the real mic source (default a ``SoundDeviceMicSource``);
            injectable so a harness could substitute one.
        local_brain: if ``True``, wire the real Qwen2.5/MLX backends.
        real_voice: if ``True``, wire the real Claude + ElevenLabs voice adapters.
        capture_path: if given (the ``--capture PATH`` flag, T-502), record this
            session into an interjection-precision **fixture** written to that
            local path on exit — the labeled-conversation schema the precision
            eval (T-503) tunes against.  **Opt-in, ephemeral, local-only:** off
            by default (``None`` = no capture); records transcripts + events +
            wall verdicts, **never raw audio**; writes only to the named local
            file; nothing is uploaded (capture only observes the pipeline).  The
            emitted fixture's ground-truth labels are placeholders for the
            ``jarvis.eval.label`` workflow to fill.  See ``jarvis.eval.capture``.
        _shutdown_event: injectable shutdown event for tests.  In production this is
            created internally; in tests a caller can pre-create one and set it to
            trigger shutdown without sending a real OS signal.

    Returns:
        In bounded mode: the list of ``Utterance`` transcribed.
        In always-on mode: ``None``.
    """
    # T-404: load .env so ANTHROPIC_API_KEY / ELEVENLABS_API_KEY are in the
    # environment before the voice session is built.  No-op if .env is absent.
    from dotenv import load_dotenv  # noqa: PLC0415

    from jarvis.audio.mic import SoundDeviceMicSource
    from jarvis.audio.mic_source import MicSource

    load_dotenv()

    # T-501: seconds=0 is an alias for forever=True.
    if seconds == 0:
        forever = True

    if now is None:
        now = time.monotonic

    # T-502: opt-in capture.  When capture_path is given, a CaptureRecorder
    # observes the run (the recording gate + a verdict-observing wrap of the wall
    # backend + the on_* callbacks) and writes a fixture on exit.  Off by default;
    # records text + events + verdicts, never raw audio; local file only.
    capture: object | None = None
    if capture_path is not None:
        from jarvis.eval.capture import CaptureRecorder

        capture = CaptureRecorder(
            fixture_id="capture",
            description=f"captured live session ({'local-brain' if local_brain else 'mock-brain'})",
        )
        gate = capture.wrap_gate(now)  # type: ignore[attr-defined]
    else:
        gate = TurnTakingGate(now)

    # T-501: bounded mode uses a plain list (return contract for smoke tests);
    # always-on mode uses a bounded deque so memory is capped.
    if forever:
        _transcribed_deque: collections.deque[Utterance] = collections.deque(
            maxlen=FOREVER_DEQUE_MAXLEN
        )
        transcribed_list: list[Utterance] | None = None
    else:
        transcribed_list = []
        _transcribed_deque = collections.deque()  # unused in bounded mode

    def _record(u: Utterance) -> None:
        """Append utterance to whichever accumulator is active."""
        if forever:
            _transcribed_deque.append(u)
        else:
            assert transcribed_list is not None
            transcribed_list.append(u)

    def on_utterance(u: Utterance) -> None:
        _record(u)
        if capture is not None:
            capture.record_utterance(u)  # type: ignore[attr-defined]
        print(f"[transcript @ {u.ts:6.2f}s] {u.speaker}: {u.text}")

    def on_summary(text: str) -> None:
        print(f"\n   [living summary updated] {text}\n")

    def on_interjection(i: Interjection) -> None:
        if capture is not None:
            capture.record_interjection(i)  # type: ignore[attr-defined]
        print(
            f"\n   >> JARVIS (interjecting, {i.category.value} @ {i.confidence:.2f}): {i.offer}\n"
        )

    def on_engagement(h: EngagementHandoff) -> None:
        if capture is not None:
            capture.record_engagement(h)  # type: ignore[attr-defined]
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

    # T-502: when capturing, wrap the wall backend so the recorder observes EVERY
    # verdict the detector returns — including the ones SummonController drops
    # (below-floor / no-gap / resumed / backed-off), which on_interjection alone
    # would never reveal.  If the heuristic default would be used (wall_backend is
    # None), construct it explicitly here so it can be wrapped.
    if capture is not None:
        from jarvis.adapters.backends import HeuristicWallBackend

        inner_wall = wall_backend if wall_backend is not None else HeuristicWallBackend()
        wall_backend = capture.wrap_wall_backend(inner_wall, now)  # type: ignore[attr-defined]

    # Voice selection (T-404): default = PrintResponder/PrintVoice (no keys);
    # real_voice=True = VoiceSession (Claude streaming → ElevenLabs TTS).
    # VoiceSession.respond() streams Claude tokens directly to ElevenLabs, so
    # _SilentVoice is passed as the VoiceOutput to suppress the second speak() call.
    class _SilentVoice:
        """VoiceOutput that does nothing — speaking already done in VoiceSession.respond()."""

        def speak(self, text: str) -> None:
            pass

    if real_voice:
        voice_session = _build_voice_session()
        responder: object = voice_session
        voice: object = _SilentVoice()
    else:
        responder = PrintResponder()
        voice = PrintVoice(prefix="      jarvis  : ")

    layer = AttentionLayer.build(
        gate=gate,
        now=now,
        responder=responder,
        voice=voice,
        summarizer_backend=summarizer_backend,
        wall_backend=wall_backend,
        on_summary_update=on_summary,
        on_interjection=on_interjection,
        on_engagement=on_engagement,
    )

    if local_brain:
        # Derive the label from the actual model so the banner can't go stale
        # (T-509 switched 3B→7B but the old banner kept saying "3B").
        from jarvis.ml.qwen import DEFAULT_MODEL_PATH

        _model_name = DEFAULT_MODEL_PATH.rsplit("/", 1)[-1].replace("-Instruct-4bit", "")
        brain_label = f"{_model_name}/MLX (local brain)"
    else:
        brain_label = "MOCK heuristics"
    voice_label = "Claude claude-opus-4-8 + ElevenLabs" if real_voice else "print stand-ins"
    if forever:
        mode_label = "always-on (no time limit — Ctrl-C to stop)"
    else:
        mode_label = f"{seconds:.0f}s window"
    print("=" * 70)
    print("  Project Jarvis — LIVE ambient pipeline (real mic · Silero · mlx-whisper)")
    print(f"  Backends: {brain_label} · voice: {voice_label}")
    print(f"  Mode: {mode_label}")
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

    # T-501: shutdown event — set by the signal handler or by a test harness.
    # In always-on mode this is how the utterance loop learns to exit.
    if _shutdown_event is None:
        _shutdown_event = threading.Event()
    shutdown_event = _shutdown_event  # local alias (mypy narrowing)

    # T-501: signal handlers for graceful shutdown in always-on mode.
    # We install them only for the duration of run_live and restore the originals on exit.
    _prev_sigint = signal.getsignal(signal.SIGINT)
    _prev_sigterm = signal.getsignal(signal.SIGTERM)

    def _handle_signal(signum: int, frame: object) -> None:  # noqa: ARG001
        """Set the shutdown event; the utterance loop will notice and exit cleanly."""
        print(f"\n[live] received signal {signum} — stopping gracefully …")
        shutdown_event.set()

    # stopper is used only in bounded mode; declare here so finally block can
    # reference it even if it was never created.
    stopper: threading.Timer | None = None

    # T-501 always-on shutdown watchdog: a daemon thread that waits on the
    # shutdown_event and then calls mic.stop() to unblock the frames() generator.
    # Without this, setting the shutdown event while the utterance loop is blocked
    # inside MicSource.utterances() (waiting for the next speech segment) would
    # never be noticed — the event is only checked between utterances.  Stopping
    # the mic causes frames() to return, which causes utterances() to return, which
    # causes the for-loop to exit naturally, and THEN the shutdown_event check after
    # the loop is irrelevant (the loop already exited via natural termination).
    # In bounded mode the stopper timer already plays this role.
    _shutdown_watchdog: threading.Thread | None = None

    try:
        if forever:
            # Always-on: install signal handlers.
            signal.signal(signal.SIGINT, _handle_signal)
            signal.signal(signal.SIGTERM, _handle_signal)

        mic.start()  # type: ignore[attr-defined]  # opens the device (permission prompt)
        if say_text:
            say_thread = _say_async(say_text)
        # Stamp Utterance.ts from the SAME real clock the gate + window run on, so
        # the RollingWindow doesn't evict every live utterance (a frame-derived ts
        # of ~9 s would look ~400000 s stale against time.monotonic). See MicSource.
        mic_source = MicSource(source=mic, gate=gate, now=now)  # type: ignore[arg-type]

        if not forever:
            # Bounded mode: set a deadline and a watchdog timer to stop the mic.
            deadline = time.monotonic() + seconds
            stopper = threading.Timer(seconds, mic.stop)  # type: ignore[attr-defined]
            stopper.daemon = True
            stopper.start()
        else:
            # Always-on: start the shutdown watchdog that unblocks the mic on signal.
            def _shutdown_watcher() -> None:
                shutdown_event.wait()  # blocks until set by signal handler or test
                mic.stop()  # type: ignore[attr-defined]  # unblocks frames() → utterances() → loop exits

            _shutdown_watchdog = threading.Thread(
                target=_shutdown_watcher, daemon=True, name="jarvis-shutdown-watchdog"
            )
            _shutdown_watchdog.start()

        # T-302: start the ticker *after* the mic is up so the first tick sees a
        # live gate (not the cold initial state).
        ticker_thread.start()

        target = stop_after_text.lower() if stop_after_text else None
        try:
            for u in mic_source.utterances():
                on_utterance(u)
                with _layer_lock:
                    layer.ingest(u)
                if target is not None and target in u.text.lower():
                    break  # clean stop on the wall-bearing line (leave the gate armed)
                if forever:
                    # Always-on: check the shutdown event on every utterance.
                    if shutdown_event.is_set():
                        break
                else:
                    if time.monotonic() >= deadline:  # type: ignore[possibly-undefined]
                        break
        except KeyboardInterrupt:
            # T-501: Ctrl-C before the signal handler fires (e.g. in tests or
            # when signal handlers aren't installed).  Treat identically.
            print("\n[live] KeyboardInterrupt — stopping gracefully …")
            shutdown_event.set()

        if not forever:
            # T-302: the real continuous loop keeps ticking after the utterance loop
            # ends (e.g. stop_after_text hit) so Path B can still fire during the
            # trailing silence.  We let it run until the listen window expires.
            remaining = deadline - time.monotonic()  # type: ignore[possibly-undefined]
            if remaining > 0:
                time.sleep(remaining)

        mic.stop()  # type: ignore[attr-defined]  # no more capture → no trailing junk

    finally:
        # T-501: restore original signal handlers before anything else so a second
        # Ctrl-C during teardown raises KeyboardInterrupt normally.
        if forever:
            signal.signal(signal.SIGINT, _prev_sigint)
            signal.signal(signal.SIGTERM, _prev_sigterm)

        # Signal the ticker to stop and wait for it to exit cleanly.
        ticker_stop.set()
        if ticker_thread.is_alive():
            ticker_thread.join(timeout=1.0)

        # Ensure the mic is stopped (idempotent — safe to call again).
        mic.stop()  # type: ignore[attr-defined]

        # Cancel the bounded-mode stopper timer if it's still pending.
        if stopper is not None:
            stopper.cancel()

        # Join the always-on shutdown watchdog (it exits once mic.stop() returns,
        # which has already been called above).
        if _shutdown_watchdog is not None and _shutdown_watchdog.is_alive():
            _shutdown_watchdog.join(timeout=1.0)

        if say_thread is not None:
            say_thread.join(timeout=1.0)

    if forever:
        n = len(_transcribed_deque)
    else:
        n = len(transcribed_list) if transcribed_list is not None else 0
    print("\n" + "=" * 70)
    print(f"  Live run complete — {n} utterance(s) transcribed.")
    print("=" * 70)

    # T-502: write the captured fixture (opt-in; local file only; no audio).
    if capture is not None and capture_path is not None:
        fx = capture.save(capture_path)  # type: ignore[attr-defined]
        n_candidates = len(fx.candidates)
        print(
            f"  [capture] wrote {n_candidates} Path-B candidate(s) to {capture_path} "
            f"(labels pending — run `python -m jarvis.eval.label show {capture_path}`)"
        )

    # Always-on mode: no accumulation contract; return None.
    # Bounded mode: return the list for smoke-test inspection.
    return None if forever else transcribed_list
