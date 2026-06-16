"""``python -m jarvis`` — run the attention layer (mock demo, or ``--live``).

Default (``python -m jarvis``): plays a scripted conversation through the real
``AttentionLayer`` in **mock** mode (no audio, no model, no network) and prints the
events it emits — living-summary updates, a proactive interjection, and a wake-word
summon → ``EngagementHandoff``. See ``jarvis.demo``.

``python -m jarvis --live`` (T-105 / T-204): runs the **real** ambient pipeline on
live mic audio — real microphone → Silero VAD → mlx-whisper ``base.en`` →
``Utterance`` — through the same orchestrator. Flags:

* ``--seconds N`` — how long to listen (default 12).
* ``--say "TEXT"`` — speak TEXT through the macOS ``say`` command (the human-free
  loopback used in the ASR spike) so the mic captures it; omit to speak yourself.
* ``--local-brain`` — use the real Qwen2.5-3B/MLX backends (one shared model
  instance) for summarization and wall detection instead of the heuristic mocks.
  Loads the ~2 GB weights on the first inference call.
* ``--mock-brain`` — explicitly select the heuristic mock backends (the default).
  Useful to confirm the flag is ignored in test contexts.

The ``--live`` path touches a microphone and loads MLX; it is never exercised by
``uv run pytest`` (the live wiring lives in ``jarvis.live`` with lazy mic imports),
so the default test suite stays green and CI never needs a mic.
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jarvis")
    parser.add_argument(
        "--live",
        action="store_true",
        help="run the real ambient pipeline on live mic audio (T-105) instead of the mock demo",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=12.0,
        help="(--live) how long to listen before stopping (default 12)",
    )
    parser.add_argument(
        "--say",
        type=str,
        default=None,
        help="(--live) speak this text via macOS `say` so the mic captures it (loopback)",
    )
    parser.add_argument(
        "--device",
        type=str,
        default=None,
        help="(--live) PortAudio input device id/name to capture from "
        "(e.g. a 'BlackHole 2ch' index for a clean say-loopback); default = system input",
    )
    parser.add_argument(
        "--stop-after",
        type=str,
        default=None,
        help="(--live) stop capturing once a transcribed line contains this text, then "
        "re-check Path B after the politeness gap (used to demo a live interjection)",
    )
    # Backend selection (T-204): --local-brain uses Qwen2.5/MLX; --mock-brain (default)
    # uses the heuristic stand-ins. Mutually exclusive; mock is the default so that
    # --live alone stays model-free for quick sanity-checking.
    brain_group = parser.add_mutually_exclusive_group()
    brain_group.add_argument(
        "--local-brain",
        action="store_true",
        default=False,
        help="(--live) use the real Qwen2.5-3B/MLX summarizer + wall backends "
        "(one shared model; loads ~2 GB weights on first inference call)",
    )
    brain_group.add_argument(
        "--mock-brain",
        action="store_true",
        default=False,
        help="(--live) explicitly select the heuristic mock backends (default; no model load)",
    )
    args = parser.parse_args(argv)

    if not args.live:
        from jarvis.demo import run_demo

        run_demo()
        return 0

    # Live mode: import lazily so the mock demo + the test suite never touch the mic.
    from jarvis.audio.mic import MicCaptureError
    from jarvis.live import run_live

    # --device accepts an int index or a device-name substring.
    device: int | str | None = args.device
    if isinstance(device, str) and device.isdigit():
        device = int(device)

    # local_brain=True iff --local-brain was passed (--mock-brain or default = False).
    local_brain: bool = getattr(args, "local_brain", False)

    try:
        run_live(
            seconds=args.seconds,
            say_text=args.say,
            device=device,
            stop_after_text=args.stop_after,
            local_brain=local_brain,
        )
    except MicCaptureError as exc:
        print(f"[live] could not open the microphone: {exc}", file=sys.stderr)
        print(
            "[live] grant mic permission to this terminal (System Settings → Privacy "
            "→ Microphone) and retry.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
