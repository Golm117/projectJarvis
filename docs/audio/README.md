# audio

Domain folder for sensing-engineer. Topic files live here as durable findings accumulate. In-flight thinking goes to `working-notes.md` in this folder.

Owner agent: `.claude/agents/sensing-engineer.md`

## Topic files

- `asr-spike.md` — the T-101 ASR runtime spike (mlx-whisper vs whisper.cpp); chose `mlx-whisper base.en`. Includes the ⚠️ ASR+SLM joint-budget coexistence flag.
- `live-smoke.md` — the T-105 live-transcript smoke test: how to run the real pipeline (`python -m jarvis --live`), the verbatim live results (both summon + interjection fired), and the honesty box. **Phase 1 complete.**
- `working-notes.md` — in-flight scratchpad.
