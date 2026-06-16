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

## T-102 mic capture loop — DONE

- **`AudioSource` abstraction** (`src/jarvis/audio/source.py`): the seam between real
  mic hardware and everything downstream (VAD T-103, ASR T-104, all tests). Yields
  fixed-size `AudioFrame` chunks (16 kHz mono float32, 512 samples/32 ms — Silero's
  geometry). `RingBuffer` = bounded circular frame buffer (overwrites oldest + counts
  `overflows` when full → bounded memory for always-on). `FakeAudioSource` (+ `.silence`
  / `.tone` / `.from_pattern` builders) = hardware-free synthetic-frame stand-in the
  VAD + buffer tests run on.
- **`SoundDeviceMicSource`** (`src/jarvis/audio/mic.py`): the real always-on loop over
  `sounddevice`/PortAudio. PortAudio callback thread pushes each frame to the bounded
  ring (never blocks); consumer `frames()` pops on its own schedule. `sounddevice`
  imported lazily inside `start()` so importing the package never needs PortAudio.
  Typed errors: `MicPermissionError` / `NoInputDeviceError` / `MicCaptureError`
  (classified from the PortAudio open error by message). **Never fabricates audio.**
- **LIVE MIC SMOKE TEST RAN ✅** (2026-06-15, this machine): mic permission was already
  granted to the terminal — a ~1.5 s capture returned **46 frames / 23,552 samples
  (~1.47 s) at 16 kHz mono, 0 overflows, mean RMS 0.0021** (quiet room, real non-zero
  energy). Real capture, not fabricated. The ring-buffer consumer kept up (0 overflows).
- **Tests:** `tests/test_audio_source.py` (18 tests) drive the ring buffer + frame + fake
  source via synthetic frames (no real mic): FIFO/wrap eviction, overflow accounting,
  bounded-memory-under-heavy-push invariant, frame shape/rate/duration/energy, fake
  source geometry + silence/tone/pattern, real `SoundDeviceMicSource` Protocol conformance
  (no hardware) + error classification. Suite 153 green, ruff clean.
- **Deps:** `sounddevice` (+ PortAudio bundled, + cffi/pycparser) and `numpy` added to
  real package `[project.dependencies]` (these ARE the always-on runtime now). DECISIONS.md entry.

## T-103 Silero VAD — DONE

- **`SileroVad`** (`src/jarvis/audio/vad.py`): consumes `AudioSource` frames, debounces a
  per-frame speech decision into clean **speech-start/speech-end edges**, and drives an
  injected `TurnTakingGate` (+ optional `on_edge` callback). Emits **edges, never
  timestamps** — the gate stamps from its own injected clock (aligned to the frozen
  T-006 edge seam, not reshaped). The VAD's own "how long" is measured in *frames* (each
  512 samples / 32 ms), so the whole audio path is clock-free; the gate is the one clock owner.
- **Hysteresis (frame units, configurable):** `speech_start_frames` (default 1) debounces a
  one-frame blip; `silence_end_frames` (default ~6 ≈ 200 ms) is the VAD-side endpoint
  hangover that ignores intra-word pauses — deliberately far shorter than the gate's ~2 s
  politeness gap (VAD = acoustic segmentation; gate = social timing). Plus `threshold`.
- **`FrameClassifier` seam** (the torch-needing part behind a tiny interface, mirroring the
  core's injected-backend discipline): default `SileroFrameClassifier` loads the real Silero
  model lazily (`load_silero_vad()`, scores each 512-sample/16 kHz frame, speech iff prob ≥
  threshold). Tests inject `EnergyFrameClassifier` (pure RMS ≥ threshold) so the
  edge-sequencing logic runs **with no torch, no model load, no mic**.
- **Silero API note:** `silero_vad.VADIterator` emits `{'start': sample_idx}`/`{'end': …}`
  dicts; I wrapped the simpler per-frame `model(tensor, 16000)` prob and do the debounce
  myself (so the same debounce logic is testable via the energy fake). Confirmed the real
  model emits start/end on synthetic tone frames.
- **LIVE checks RAN ✅** (permission granted): `test_live_silero_vad_on_mic_optional`
  **passed (not skipped)** — real Silero model + real mic, >0 frames processed end-to-end.
- **Tests:** `tests/test_vad.py` (14): silence→no edges, single/two segment start-end pairs,
  speech-to-EOF leaves segment open (VAD never invents an end edge), intra-word dip under
  hangover doesn't split, start-debounce ignores a blip, **drives a real `TurnTakingGate`**
  (settled/politeness_gap after ~2.5 s silence; abort-latch on resumed speech after a gap),
  guards. Suite 167 green, ruff clean.
- **Deps:** `silero-vad` (+ `torchaudio`; torch already present from the asr-spike/ASR
  stack) added to real package deps. DECISIONS.md entry (notes the torch weight).

## For T-104 (next)
- `mlx_whisper.transcribe(audio_path_or_array, path_or_hf_repo="mlx-community/whisper-base.en-mlx")`
  returns `{"text": ...}` — wrap behind `TranscriptSource`; stamp `Utterance.ts` from
  the VAD timeline (Silero, T-103). The realistic ASR unit is a VAD-segmented utterance
  (~the 3.8 s clip), transcribed in ~70 ms — ASR is NOT the budget bottleneck; the VAD
  endpoint wait + the SLM are. Feed ASR the concatenated frames of one speech segment
  (start→end edges from T-103's VAD).

## Open (joint with local-ml-engineer)
- Measure **ASR + Qwen2.5 concurrent** always-on budget before freezing model sizes.
  ASR in isolation is a rounding error; the SLM dominates. `base.en` chosen to protect
  that headroom. See the coexistence flag in `asr-spike.md`.
