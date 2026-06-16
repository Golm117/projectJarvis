# Working notes — audio

_(scratchpad for in-flight thinking; promote durable findings to topic files)_

## T-101 ASR spike — DONE (durable findings promoted to `asr-spike.md`)

- **Chosen runtime:** `mlx-whisper` `base.en` (fallback: `whisper.cpp`/`pywhispercpp`).
  Full method + numbers + recommendation in `docs/audio/asr-spike.md`.
- **Spike deps:** isolated `asr-spike` uv group (`uv run --group asr-spike …`) — not in
  package deps yet. T-104 promotes only `mlx-whisper` into real deps.
- **Benchmark harness (regenerable, ephemeral under `/tmp/asr-spike/`, not committed):**
  - `bench.py` — latency (5 warm runs) + WER on two synthesized clips.
  - `mem_sustained.py` — isolated per-runtime peak RSS + 40× sustained-drift.
  - Reference clips: `say -v Daniel` → `ffmpeg -ar 16000 -ac 1` (16 kHz mono WAV);
    `short.wav` (3.8 s, one question) + `ref.wav` (17 s paragraph), exact ground truth.
  - To re-run: regenerate the clips (the `say` + `ffmpeg` pipeline in `asr-spike.md`),
    then `uv run --group asr-spike python /tmp/asr-spike/bench.py`.

## For T-102 / T-104 (next)
- `mlx_whisper.transcribe(audio_path_or_array, path_or_hf_repo="mlx-community/whisper-base.en-mlx")`
  returns `{"text": ...}` — wrap behind `TranscriptSource`; stamp `Utterance.ts` from
  the VAD timeline (Silero, T-103). The realistic ASR unit is a VAD-segmented utterance
  (~the 3.8 s clip), transcribed in ~70 ms — ASR is NOT the budget bottleneck; the VAD
  endpoint wait + the SLM are.

## Open (joint with local-ml-engineer)
- Measure **ASR + Qwen2.5 concurrent** always-on budget before freezing model sizes.
  ASR in isolation is a rounding error; the SLM dominates. `base.en` chosen to protect
  that headroom. See the coexistence flag in `asr-spike.md`.
