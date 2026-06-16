# Notes

Informal session-to-session handoff scratchpad. Read this first when starting a session. Overwrite freely — this is not a log, it's a whiteboard.

**What goes here:**
- What was just worked on
- What's half-done and where it was left
- What's next
- Open questions for the human
- Anything the next session needs to know that isn't obvious from the code

**What does _not_ go here:**
- Permanent decisions → `DECISIONS.md`
- Product spec → `docs/reference-guide.md` (or `.pdr.md`)
- Setup instructions → `README.md`
- Structured task state → `TASKS.md`

---

## Current state — 2026-06-16 (T-507 done → anti-fragmentation endpointing)

**Phase:** phase_5 field-fix. T-507 (anti-fragmentation endpointing + question fidelity) is **DONE** (sensing-engineer). Suite **480 green** (466 + 14 new), ruff clean. On `main`, NOT pushed.

**T-507 — what was built:**
- **`DEFAULT_SILENCE_END_FRAMES = 15`** (raised from 6) in `src/jarvis/audio/vad.py`. At 32 ms/frame: 6 frames = 192 ms → 15 frames = 480 ms.
- Rationale + tradeoff documented in a `vad.py` comment block: 480 ms absorbs a natural breath-length pause; a genuine ~1 s thinking pause is a real turn boundary and still splits (intentional).
- Latency tradeoff: +288 ms delay on turn-end detection. Modest vs the 2 s politeness gap; summon (Path A) also sees the delay but fires on the completed utterance.
- **ASR punctuation investigation:** `mlx_whisper.transcribe` `initial_prompt` was tested and confirmed working. Decision: **leave decoding default**. The structural fix (T-506 onset + T-507 anti-frag) gives Whisper the full prosodic arc it needs to emit `?`. Documented in `MlxWhisperTranscriber` docstring.
- **14 new tests** in `tests/test_t507_antifrag_endpointing.py`.

**Root cause confirmed (from user live log):** "times 7." + "What does that equal?" were two segments because a mid-sentence breath pause > 192 ms closed the first segment. At 480 ms, the same pause is absorbed.

**Live re-test honesty:** `--say` loopback produced 0 utterances (known limitation: TTS digital loopback doesn't reliably produce long-enough audio in the short run window when the mic is device 6 built-in). Unit tests prove the mechanism deterministically. **User should verify with natural voice** (`~/.local/bin/uv run python -m jarvis --live --local-brain --device 6 --seconds 30` — speak a question with a natural breath pause and confirm one utterance arrives with `?`).

**→ Remaining Phase 5:** T-504 (thermal/battery soak) only — deferred to real-world use.

---

## Prior state — 2026-06-16 (T-506 done → VAD pre-roll onset fix)

**Phase:** phase_5 field-fix. T-506 (VAD pre-roll / lookback buffer) is **DONE** (sensing-engineer). Suite **466 green** (454 + 12 new), ruff clean. On `main`, NOT pushed.

**T-506 — what was built:**
- **`DEFAULT_PRE_ROLL_FRAMES = 10`** in `src/jarvis/audio/mic_source.py` (~320 ms at 32 ms/frame).
- **`_pre_roll: deque[AudioFrame]`** in `MicSource.__init__` (`deque(maxlen=pre_roll_frames)`).
- In `utterances()` loop: frame appended to `_pre_roll` **after** `process_frame()` (so the triggering frame is not in the deque when `speech_start` fires; it enters via the normal loop append — no duplication).
- In `_on_edge("speech_start")`: `_segment_frames = list(self._pre_roll); self._pre_roll.clear()` — seeds the segment from the lookback deque, then clears it for the next segment.
- `pre_roll_frames` constructor arg (default `DEFAULT_PRE_ROLL_FRAMES`; 0 = disabled; < 0 raises).
- **12 new tests** in `tests/test_t506_pre_roll.py`.

**Root cause confirmed:** `MicSource._on_edge` initialized `_segment_frames = []` on `speech_start`. Sub-threshold onset (the quiet beginning of a human sentence, before Silero's debounce threshold is crossed) was never captured.

**Live re-test honesty:** The `--say` loopback is not suitable for demonstrating soft-onset recovery (TTS has no soft onset; ambient room audio causes Whisper hallucinations through speakers → mic). Unit tests prove the mechanism. **User should verify by speaking naturally at the built-in mic** (`~/.local/bin/uv run python -m jarvis --live --local-brain --device 6 --seconds 30`).

**→ Remaining Phase 5:** T-504 (thermal/battery soak) only — deferred to real-world use.

---

## Prior state — 2026-06-16 (v0 FEATURE-COMPLETE → Phases 0–5 done bar the deferred T-504 soak; pushed to origin/main)

**Phase:** phase_5 → **v0 FEATURE-COMPLETE.** The whole v0 MVP (Phases 0–5) is built, tuned, and validated: real-room ASR (`small.en`), local Qwen2.5-3B brain, continuous live interjection loop, Claude→ElevenLabs voice, always-on `--forever`, and interjection precision tuned **0.60 → 0.75**. T-503 (tune interjection precision, qa-tuning) is **DONE** — independent core-engineer review **APPROVED**; **human sign-off on post-engagement cooldown = 6.0 s** (down from 8.0 s). Suite **454 green**, ruff clean.

**The only open task is T-504 (thermal/battery soak) — DEFERRED to real-world use** per human decision 2026-06-16: v0 declared feature-complete; the real multi-hour soak is normal always-on use. The always-on loop already ships **bounded memory** (`deque(maxlen=1000)`) + **graceful shutdown** by design (T-501). Run a bounded soak / reopen T-504 if instability surfaces in use.

**Pushed to `origin/main`** 2026-06-16 (human push-gate cleared). Known v1 levers (deferred, evidence-logged): declarative-`factual_gap` recall + the detector's lone wrong-category FP (`ff-false-wrong-category`) — both wall-prompt work, not orchestrator/threshold levers.

**T-503 finalization (this session):** the cooldown was lowered 8.0 → **6.0 s** and applied coherently everywhere — the `DEFAULT_POST_ENGAGEMENT_COOLDOWN_SECONDS` constant + docstring (`attention_layer.py`), the T-503 tests, the regenerated fixtures (`docs/qa/fixtures/*.json` `config.post_engagement_cooldown_seconds`), `docs/qa/threshold-tuning.md`, and a new superseding `DECISIONS.md` entry. 6.0 s is the minimum that suppresses the 5.5 s "What do you need?" FP (speech_end 3.5 s + 2 s gap), chosen for responsiveness over the 8.0 s robustness margin; **precision is identical at 0.75** (6 and 8 s both score it — the cooldown touches only that one FP). The unrelated match-window `8.0` (`DEFAULT_MATCH_WINDOW_SECONDS` / `match_to=8.0`) was deliberately left untouched.

**T-503 — what was built (the success-metric task; precision 0.60 → 0.75 on the seeded eval):**

- **Post-engagement cooldown (the FP fix)** in `AttentionLayer` (`src/jarvis/attention_layer.py`): after any engagement (summon OR fired interjection), ambient Path-B is suppressed for `DEFAULT_POST_ENGAGEMENT_COOLDOWN_SECONDS = 6.0` (finalized value; was 8.0 in review). `_engage()` stamps `_last_engagement_at`; `ingest()` + `tick()` check `_in_post_engagement_cooldown()`. Kills the "What do you need?" FP (a turn addressed AT Jarvis inside a just-engaged exchange — the live FP from T-502). **Placed in the orchestrator, NOT `SummonController`** (the pure decision machine has no notion of recent engagement) → qa-gated modules byte-for-byte unchanged.
- **Pending-wall TTL (staleness fix, the T-302/T-303 carry-forward)** in `AttentionLayer`: `tick()` drops `_pending_wall` once `now() - _pending_wall_cached_at >= ttl`. `DEFAULT_PENDING_WALL_TTL_SECONDS = 12.0`. New fixture `ff-false-stale-pending-wall` (wall cached t=0, opening only t=15) confirms it; a fresh wall (opening within TTL) still fires.
- **Threshold sweep → NO default changed.** Confidence floor is **inert** (the FP and TP are BOTH `factual_gap @ 0.95` → move together at every floor value; raising the floor kills the TP before isolating the FP). Politeness gap 2.0 sits on its precision plateau (1.5–3.0 all = 0.60; <1.5 admits the thinking-pause FP; >4.0 kills useful fires). Settle is Path-A only. **The lever is context, not a threshold** — exactly as T-502 flagged. Full sweep tables in `docs/qa/threshold-tuning.md`.
- **Carry-forward items — both DEFERRED with evidence:** (1) declarative `factual_gap` recall → defer to v1 (more fires can only hold/lower a precision-first metric; the corpus is FP-limited not recall-limited; it's a `local-ml-engineer`-lane WallBackend prompt change anyway). (2) confidence-floor recalibration → keep 0.70 (inert for the Qwen near-binary backend).
- **Eval modeling (schema v2, `src/jarvis/eval/fixture.py`):** `MomentKind.ENGAGEMENT` (marks when Jarvis engaged), `Candidate.wall_detected_at` (the pending-wall TTL anchor), `Config.{post_engagement_cooldown_seconds, pending_wall_ttl_seconds}`. Loader reads v1 + v2. The runner (`runner.py`) applies both rules in parity with `AttentionLayer`. Regenerate fixtures: `uv run python -m jarvis.eval.seed docs/qa/fixtures`.
- **Tests:** `tests/test_t503_precision_tuning.py` (13 new, deterministic on `SimulatedClock`): cooldown suppresses-within / allows-after / zero-disables / fire-arms-cooldown; TTL drops-stale / keeps-fresh / zero-disables; precision-0.75; committed-corpus-0.75; pre-T-503-baseline-below-target; shipped-constants; negative-guards. Updated 3 T-502 tests to the new behavior (WDYN now suppressed, corpus 0.75, per-category 2-of-3).

**Achievable ceiling reached:** 0.75 is the ceiling on this seeded set — the lone remaining false fire is `ff-false-wrong-category` (a detector mis-naming `factual_gap` on a real `stuck_point`), which no orchestrator/threshold lever can fix (it's a detector-correctness issue, correctly scored false).

**Independent review (core-engineer): APPROVED** — blessed the mechanism and the 5–6 s cooldown range, noting the value is one eval-testable constant. Human then signed off on **6.0 s**. The reviewer's scrutiny points (cooldown/TTL parity between `AttentionLayer` and the eval `runner`; a fired interjection arming the cooldown; values vs. real pacing — revisit on a captured corpus; qa-gated modules unchanged) all cleared.

**→ Remaining Phase 5:** T-504 (thermal/battery soak, sensing-engineer) only. T-503 is done.

---

## Prior state — 2026-06-16 (T-502 done → capture-and-label tooling + precision eval runner)

**Phase:** phase_5 (active). T-502 (capture-and-label tooling, qa-tuning, this session) is **DONE**. Suite **439 green** (407 baseline + 32 new), ruff clean. On `main`, not pushed. **NOT qa-gated** (tooling + fixtures only; no gate/summon/wall/threshold change).

**T-502 — what was built (`src/jarvis/eval/` + `docs/qa/fixtures/` + `docs/qa/capture-and-label.md`):**

- **The fixture schema is now code** (`eval/fixture.py`): `Fixture`/`Moment`/`Candidate`/`Config` + JSON (de)serialization + `validate()`. Matches the eval-plan (T-010) spec: monotonic timeline (utterance/speech_start/speech_end) + per-candidate ground truth (wall, category, useful|false, match-window) + the `config` block of the 3 thresholds T-503 sweeps. New `observed_category` field models a wrong-category fire.
- **Capture** (`eval/capture.py`, `--capture PATH` in live.py + __main__.py): a `CaptureRecorder` that **only observes** a `run_live` session — a recording-`TurnTakingGate` subclass (records edges, delegates timing unchanged) + a pass-through wrap of the `WallBackend` (records every verdict, returns it unchanged) + the existing `on_*` callbacks. **Every wall verdict becomes a Path-B candidate, including the ones SummonController DROPPED** (which `on_interjection` alone never reveals). No core-internal access. **Opt-in / ephemeral / local-only / never raw audio / nothing uploaded** (PRD privacy hard-nos). Emits raw fixtures with `UNLABELED` candidates.
- **Labeling** (`eval/label.py`): functions + a tiny CLI (`python -m jarvis.eval.label show|set|validate FIXTURE.json`) to fill the placeholder ground truth. Or edit the pretty JSON directly (each candidate carries the observed facts).
- **Eval runner** (`eval/runner.py`): `precision = useful ÷ total Path-B fires`, deterministic on a `SimulatedClock` through the **real** gate + controller (verdicts built from labels, no model/audio/network). Per-category breakdown; refuses unlabeled fixtures; Path-A summons excluded; abort/back-off correctly remove would-be fires.
- **Seeded corpus** (`eval/seed.py` → `docs/qa/fixtures/*.json`, regenerate with `python -m jarvis.eval.seed docs/qa/fixtures`): real-session fixtures (the `factual_gap` TP "What was the date of the conference again?", the borderline FP "What do you need?" **labeled FALSE**, a Path-A summon) + the 5 eval-plan behavior illustrations. **Scores precision 0.60 on shipped defaults** (5 fires, 3 useful — FPs present → < 1.0).

**qa verdict on "What do you need?" (asked in the brief): FALSE.** It surfaced inside a summon exchange — the question is directed AT Jarvis, not an unanswered wall between humans — so a fire is noise; and precision-first means a borderline case is FALSE in the yardstick. **Note for T-503:** both the TP and this FP are `factual_gap @ 0.95` (the Qwen near-binary-confidence problem, T-203), so the confidence floor **cannot** separate them — the real lever is context (is the wall inside a just-engaged exchange?), a detector/orchestrator signal, not a threshold. Recorded in `docs/qa/capture-and-label.md`.

**→ What T-503 (next, qa-gated) tunes against:** the eval runner `jarvis.eval.runner.run_fixtures(...)` over the labeled `docs/qa/fixtures/*.json`, overriding each fixture's `config` block (politeness_gap_seconds / interjection_confidence_floor / settle_seconds) to sweep, picking the operating point clearing ≥ 70 %-useful with false interjections rare. Baseline precision 0.60. **Carry-forward:** add the `_pending_wall` staleness fixture (T-302/T-303 watch-item) and decide whether a TTL / topic-shift clear is warranted (a SummonController/orchestrator-policy change → qa-gated).

**⚠️ Found a pre-existing T-501 bug (spawned as a separate task, not fixed here — scope fence):** in `src/jarvis/__main__.py` the `--forever` arg passes `const="1000"` to a `store_true` action, which argparse rejects → **`python -m jarvis --live ...` and `--help` crash on startup** with `TypeError: _StoreTrueAction.__init__() got an unexpected keyword argument 'const'`. This blocks actually *running* `--capture` (and every live invocation) until fixed. The fix is one line (drop `const=`, inline the `1000` in the help text). The T-502 capture/label/eval logic is fully tested model-free regardless.

**→ Remaining Phase 5 tasks:** T-503 (threshold tuning, qa-tuning, qa-gated — harness now ready), T-504 (thermal/stability soak, sensing-engineer).

---

## Prior state — 2026-06-16 (T-501 done → always-on mode, graceful shutdown, bounded memory)

**Phase:** phase_5 (active). T-501 (always-on end-to-end run) is **DONE** (core-engineer, this session). Suite **407 green** (398 baseline + 9 new), ruff clean. On `main`, not pushed.

**T-501 — what was built (files: `src/jarvis/live.py`, `src/jarvis/__main__.py`, `tests/test_t501_always_on.py`):**

- **`--forever` flag + `seconds=0` alias:** `python -m jarvis --live --forever` activates always-on mode. `seconds=0` is an alias. The existing bounded `--seconds N` (default 12) path is unchanged — returns `list[Utterance]`, all smoke tests green.
- **Graceful shutdown:** SIGINT + SIGTERM signal handlers installed (and restored on exit); a daemon watchdog thread (`jarvis-shutdown-watchdog`) waits on `_shutdown_event` and calls `mic.stop()` to unblock the `MicSource.utterances()` generator (which blocks on `frames()` during silence). `KeyboardInterrupt` is also caught explicitly in the utterance loop. Exit is clean (code 0, no traceback). All threads (ticker + watchdog + say) joined in the finally block. Signal handlers restored before return.
- **Bounded memory:** In always-on mode, `transcribed: list[Utterance]` (previously unbounded) replaced with `collections.deque(maxlen=FOREVER_DEQUE_MAXLEN)` where `FOREVER_DEQUE_MAXLEN=1000`. Always-on mode returns `None` (no accumulation contract). Bounded mode keeps the `list` and return contract unchanged.
- **Injectable `_shutdown_event`:** `run_live` accepts a pre-created `threading.Event` so tests can trigger shutdown without sending real OS signals.
- **9 new tests in `tests/test_t501_always_on.py`:** shutdown-event triggers clean exit, ticker joined, bounded deque cap, bounded mode returns list, `seconds=0` alias, `KeyboardInterrupt` handled, mic stop called, signal handlers restored, stopper timer cancelled.

**Live validation:** Only via deterministic unit tests (injected shutdown event + fake `_FakeMicSource`). Real Ctrl-C on the full pipeline was not validated in this session — the agent cannot send SIGINT to a foreground process it runs. The shutdown *mechanism* (watchdog thread + mic.stop() + finally block) is fully tested; the OS signal path is thin wrapper that sets the same event.

**How to run always-on:**
```
# Run through uv — system python is 3.9 and lacks the project env (`uv` is at ~/.local/bin/uv, not on PATH).
~/.local/bin/uv run python -m jarvis --live --forever                        # heuristic brain, no voice
~/.local/bin/uv run python -m jarvis --live --forever --local-brain          # Qwen2.5 brain
~/.local/bin/uv run python -m jarvis --live --forever --local-brain --voice  # full pipeline
```
Stop: **Ctrl-C** → clean exit 0, "stopping gracefully…" message, ticker + watchdog + mic joined.

**→ Remaining Phase 5 tasks:** T-502 (capture/label tooling, qa-tuning), T-503 (threshold tuning, qa-tuning, qa-gated), T-504 (thermal/stability soak, sensing-engineer).

---

## Prior state — 2026-06-16 (T-505 done → real-room ASR quality pass complete)

**Phase:** phase_5 (active). T-505 (real-room ASR quality pass) is **DONE** (sensing-engineer, this session). Suite **398 green** (347 baseline + 51 new), ruff clean. On `main`, not pushed.

**T-505 — what was built:**
- **ASR upgraded: `base.en` → `small.en`** (`DEFAULT_MLX_WHISPER_REPO` in `mic_source.py`). The `MlxWhisperTranscriber(repo=...)` arg was already constructor-injectable (T-104); `base.en` stays selectable by passing its repo. `small.en` weights (~466 MB) downloaded and cached.
- **Lexical segment filter:** `_is_lexical()` in `mic_source.py`, applied in `MicSource._close_segment()`. Drops: empty/whitespace, pure-punctuation/symbol, filler-syllable-only ("Mm.", "Hmm", "Uh"). Keeps: "Jarvis", "Yes.", "No.", all normal speech. Module-level constants: `MIN_WORD_LENGTH=2`, `MIN_LEXICAL_WORDS=1`, `STOP_SYLLABLES` frozenset.
- **51 new model-free unit tests** in `tests/test_t505_asr_quality.py`.

**Joint budget re-measurement (M5 Pro, 5 warm runs):**
- small.en ASR: **80 ms** median (vs base.en 40 ms — +40 ms, ~2×)
- Qwen2.5-3B summarize: 305 ms, detect_wall: 392 ms (minor variance from T-201's 657 ms total, likely model warm vs cold)
- **Joint total: 775 ms** — 1,225 ms margin vs 2 s budget. **Clears comfortably.**

**Live test results on built-in mic (device 6, `--say` loopback → speaker → built-in mic):**
- "Hey Jarvis, can you hear me?" → transcript: **"Hey Jarvis, can you hear me?"** → Path A fired (summon).
- "What was the date of the conference again?" → transcript exact → **factual_gap @ 0.95** → Path B fired.
- "Yes Jarvis" → transcript: **"Yes Jarvis."** — short reply kept by filter.

**Honest caveat on before/after:** The "Germans" mishearing and garbage segments happened with the user's natural voice at room distance + ambient noise. The `--say` loopback produces cleaner audio than that scenario. Both `base.en` and `small.en` handled the loopback correctly in isolation — the regression is environment-dependent. small.en has meaningfully more parameters at the `.en` size and provides better accuracy in noisy/far-field conditions; the filter is confirmed working end-to-end in the pipeline.

**→ Remaining Phase 5 tasks:** T-501 (always-on loop, core-engineer), T-502 (capture/label tooling, qa-tuning), T-503 (threshold tuning, qa-tuning), T-504 (thermal/stability, sensing-engineer).

---

## Prior state — 2026-06-16 (T-401→T-404 done → PHASE 4 COMPLETE)

**Phase:** phase_4 → **COMPLETE.** All four Phase-4 tasks done: T-401 (ClaudeResponder), T-402 (ElevenLabsVoice), T-403 (VoiceSession streaming pipeline), T-404 (wire + live test). Suite **347 green**, ruff clean. On `main`, not pushed.

**Phase 4 — what was built:**

- **T-401 — `ClaudeResponder`** (`src/jarvis/adapters/claude_responder.py`): `EngagedResponder` via `claude-opus-4-8`. Frozen spoken-style system prompt: 1–3 sentences, no preamble, plain prose, peer-who-was-listening register. Lazy `import anthropic`; injected client for offline tests. 26 unit tests. `anthropic>=0.109.2` + `python-dotenv>=1.2.2` added to real deps.

- **T-402 — `ElevenLabsVoice`** (`src/jarvis/adapters/elevenlabs_voice.py`): `VoiceOutput` via ElevenLabs. `text_to_speech.stream(voice_id, text=..., model_id=...)` → `Iterator[bytes]` → `elevenlabs.play.stream()` for real-time streaming playback via `mpv`. Lazy imports; injected client + play callable for offline tests. Default voice: Rachel (`21m00Tcm4TlvDq8ikWAM`), model: `eleven_multilingual_v2`. 20 unit tests. `elevenlabs>=2.53.0` added. `mpv` installed via brew.

- **T-403 — `VoiceSession`** (`src/jarvis/adapters/voice_session.py`): sentence-chunked streaming pipeline. `client.messages.stream()` + `stream.text_stream` for token iteration. Tokens buffered to sentence boundaries (`_SENTENCE_END_RE`) or `_MAX_CHUNK_CHARS=200` force-flush. Each chunk sent to `ElevenLabsVoice.speak()` while Claude generates the next. Stop event checked before each chunk (barge-in safety at sentence granularity). `respond()` method satisfies `EngagedResponder` Protocol via the streaming path. 20 unit tests.

- **T-404 — wired into `--live --voice`** (`live.py`, `__main__.py`): `load_dotenv()` at live entry. `_build_voice_session()` lazy-builds `VoiceSession(ClaudeResponder(), ElevenLabsVoice())`. `_SilentVoice` no-op suppresses the second `voice.speak()` call (since `VoiceSession.respond()` already speaks). `--voice` / `--real-voice` flags in `__main__.py`. Default stays print stand-ins.

**Live test results on M5 (2026-06-16, BlackHole loopback + Shure MV7+ mic, verbatim):**

Run 1 (heuristic brain + voice): "Jarvis" wake word → ENGAGEMENT (trigger: summon). "What time is it right now?" → `unanswered_question @ 0.72` → ENGAGEMENT (wall:unanswered_question). Both fired `VoiceSession.respond_and_speak()`. ElevenLabs audio confirmed heard.

Run 2 (isolated VoiceSession timing test): "Jarvis, what is a Python decorator used for?"
- Claude response: "A decorator is a function that wraps another function to extend or modify its behavior without changing the original code. You apply it with the @ syntax right above a function definition, and it's commonly used for things like logging, timing, or access control."
- **First-audio latency: 2.14 s** (within the ~2 s target from the latency budget).
- Total time (2-sentence response + full TTS playback): 20.3 s.
- Voice register: 2 sentences, plain prose, no preamble — correct spoken-style.

**Response quality sample (live):** "useEffect lets you run side effects in a function component — things like fetching data, setting up subscriptions, or manually touching the DOM after render. You pass it a function and a dependency array, and it reruns whenever those dependencies change." — exact spoken-style, peer-who-was-listening register.

**→ Phase 5 (Make it live & tune) picks up:**
- T-501: always-on end-to-end run on the M5 (core-engineer)
- T-502: capture-and-label tooling for real conversations (qa-tuning)
- T-503: threshold tuning against interjection-precision metric (qa-tuning)
- T-504: stability / thermal / battery pass for sustained always-on (sensing-engineer)

**Human decisions needed before Phase 5:**
1. ElevenLabs voice ID — Rachel (default) is fine; if a different voice is wanted, it is a product decision. See `docs/voice/response-contract.md`.
2. API costs — `claude-opus-4-8` @ $5/M input + $25/M output; `eleven_multilingual_v2` standard pricing. Acceptable for always-on interjection cadence? (Each engagement is ~1–3 sentences.)
3. Always-on loop design (T-501) — Phase 5 removes the `--seconds` window and runs indefinitely; needs a graceful shutdown signal.

---

## Prior state — 2026-06-15 (T-304 done → PHASE 3 COMPLETE)

**Phase:** phase_3 → **COMPLETE.** All four Phase-3 tasks done: T-301 (one-clock invariant), T-302 (continuous ticker, qa-approved), T-303 (live validation, qa-approved), T-304 (latency budget). Suite **281 green**, ruff clean. On `main`, not pushed.

**T-304 — latency budget pass — DONE.** Key findings:
- **Budget target:** ~2 s from wall utterance to offer (`.pdr.md` line 223 + PRD 02 §asymmetric-summon).
- **Stage 1 — at-ingest Qwen work:** 657 ms worst case (T-201 measured: ASR 40 ms + summarize 250 ms + detect_wall 366 ms). Absorbed inside the 2 s gap — Stage 1 completes at ~0.6 s, before the gap opens.
- **Stage 2 — politeness gap:** 2,000 ms intentional social wait. Dominant term; deliberate.
- **Stage 3 — ticker fire latency:** ≤ 210 ms after gap opens (200 ms cadence + ~8 ms jitter, measured live on M5 with `scripts/latency_budget_harness.py`).
- **Net margin vs 2 s:** ≥ 1,790 ms. **Wall detector confirmed OFF the tick path** — tick() costs 0.7 µs (fire path); detector costs ~366 ms. The cached-verdict design means no model call per tick.
- **No constant change made.** `TICK_INTERVAL_SECONDS = 0.20` is adequate. No gated threshold proposed.
- **Deliverable:** `docs/architecture/latency-budget.md` (target + source + decomposition + measured numbers + verdict).

**→ Phase 4 (The voice) is next** (voice-integration-engineer): replace `PrintResponder`/`PrintVoice` stand-ins with real Claude `claude-opus-4-8` + ElevenLabs streaming TTS. **Needs API keys not yet set:** `ANTHROPIC_API_KEY` + `ELEVENLABS_API_KEY`. Phase 4 tasks: T-401 (EngagedResponder via Claude), T-402 (VoiceOutput via ElevenLabs), T-403 (token-stream piping), T-404 (full engaged path on live audio).

**T-302/T-303 qa-tuning verdict: APPROVED.** The continuous ticker is the success-metric-critical change (it changes *when* interjections fire live). Gated modules (TurnTakingGate/SummonController/WallDetector) confirmed **byte-for-byte unchanged** (diff empty). Three deliverables:
1. **Double-fire fix — SOUND, the T-204 live bug is FIXED.** Double guard: `_pending_wall` cleared on first fire (later ticks no-op, offer-determinism-independent) + the same `WallVerdict` object re-evaluated each tick (stable signature → existing back-off de-dupes). The deterministic test pins guard (a) with a *fixed-offer* fake; the real non-deterministic-offer de-dupe I confirmed **live** with `--local-brain` (one fire, one Qwen offer).
2. **Staleness policy — ACCEPTED for v0.** Replace-with-fresher-wall + fire-on-next-fresh-silence-after-abort are both precision-safe (confirmed live). **One non-blocking watch-item flagged to T-503:** `_pending_wall` has no TTL / topic-shift clear → a wall cached across many off-topic turns *could* fire late as a stale false interjection. Bounded in practice; no misfire observed live. Adding a TTL is a qa-gated SummonController/orchestrator-policy change → T-503 should add a staleness fixture and decide. NOT taken unilaterally.
3. **Live validation (T-303, M5, BlackHole device 5, verbatim):** (a) fired **mid-conversation via the ticker, exactly once**, no `--stop-after`/re-ingest; (b) **abort-on-resume HELD** (no fire during resumed speech; fired only on the final clean silence); (c) **back-off de-dupe HELD with the real `QwenWallBackend`** (one Qwen offer — T-204 double-fire fixed). Details in `docs/qa/working-notes.md` §T-302/T-303.

**→ Phase 3 picks up:** **T-304 (latency budget pass)** — gate → detector → offer within the ~2 s target on the M5; the ticker adds ≤ 0.20 s. NOT qa-gated unless it proposes a threshold change (politeness-gap), which would route back to qa-tuning. After T-304, Phase 3 is complete and Phase 4 (the voice) begins.

**Human / Phase-5 flags (neither blocks):** (1) the `_pending_wall` staleness TTL above (T-503); (2) politeness-gap / confidence-floor retune (T-503 lever, carry-forward from T-203/T-204).

---

## Prior state — 2026-06-15 (T-302 in review → continuous Path-B loop built)

**Phase:** phase_3 — Knowing when to speak (ACTIVE). T-302 (continuous real-time SummonController re-evaluation) is **IN REVIEW** (core-engineer, this session). Suite **281 green** (270 baseline + 11 new), ruff clean. On `main`, not pushed.

**T-302 — what was built:**

1. **`AttentionLayer._pending_wall: WallVerdict | None`** (new field) — caches the wall verdict from the most recent ingest that returned None from consider_interjection while `verdict.is_wall` is True. Non-wall verdicts never cached. Cleared on any engagement (Path A or Path B) and on fire. Replaced by newer walls at next ingest.
2. **`AttentionLayer.tick()`** (new method) — pure re-evaluation hook. If `_pending_wall` is not None, calls `self._controller.consider_interjection(self._pending_wall)`. Fires and clears on success. No-op otherwise. Reads time exclusively through the gate predicates — one-clock invariant preserved.
3. **`live.py` — daemon ticker thread + lock** — replaces the old trailing re-check smoke-test affordance (removed). A `threading.Lock` (`_layer_lock`) serialises `layer.ingest()` and `layer.tick()` from their respective threads (utterance-consumer + ticker). `TICK_INTERVAL_SECONDS = 0.20` gives ~10 ticks per 2 s gap.
4. **11 new tests** in `tests/test_tick_continuous_path_b.py` — deterministic on SimulatedClock, no mic/model/real clock. Pins: fire-after-gap, fire-exactly-once (double-fire regression), abort-on-resume, no-op when idle, Path-A clears cache, Path-B-at-ingest clears cache, fresher wall replaces stale, non-wall does not clear, one-clock (SimulatedClock controls fire), abort-then-resume fires on fresh silence, thread-safety stress test.

**Gated modules:** `TurnTakingGate`, `SummonController`, `WallDetector` — **unchanged**.

**→ qa-tuning: review T-302.** The qa brief is in the T-302 TASKS.md Notes field. This review folds in T-303's live validation (abort-on-resume + back-off on live audio).

---

## Prior state — 2026-06-15 (T-301 DONE → Phase 3 integration seam documented)

**Phase:** phase_3 — Knowing when to speak (ACTIVE). T-301 (verify VAD↔gate one-clock invariant) is **DONE** (core-engineer, this session). Suite **270 green** (264 baseline + 6 new), ruff clean. On `main`, not pushed.

**T-301 — findings (all three items confirmed):**

1. **One-clock invariant: HOLDS.** In `run_live`, the same `time.monotonic` function-object flows into:
   - `TurnTakingGate(now)` → gate stamps every edge from it
   - `AttentionLayer.build(now=now)` → `RollingWindow` evicts against it
   - `MicSource(now=now)` → `Utterance.ts = now()` at segment-close
   No module calls `time.monotonic()` on its own. This was broken in T-104 (frame-derived ts vs. large-offset window clock → instant eviction of every live utterance) and fixed in T-105 by injecting the shared clock into `MicSource`. 6 pinning tests in `tests/test_one_clock_invariant.py` lock this in.

2. **Blocking-generator silence gap: CONFIRMED as the T-302 integration point.** The `MicSource.utterances()` generator is blocked inside `source.frames()` during silence — no yield, so `AttentionLayer.ingest` never runs, so `SummonController.consider_interjection` (which reads `gate.politeness_gap_elapsed()`) is never called as the gap grows. The window during which `politeness_gap_elapsed()` is `True` is entirely missed. The v0 smoke-test trailing re-check (`time.sleep + re-ingest`) is a one-shot affordance, not the continuous loop T-302 must build.

3. **T-302 integration seam — recommended design.** Add `AttentionLayer.tick()` that re-evaluates `consider_interjection` with a **cached** `_pending_wall` verdict (from the most recent `ingest` call that returned None). Threading: a timer in `live.py` calls `layer.tick()` periodically during silence. No changes to `TurnTakingGate`, `SummonController`, or `WallDetector` — all qa-gated modules are untouched. Full design in `docs/architecture/phase3-invariants.md` §3.

4. **Non-deterministic back-off — T-302 must use cached verdict.** `SummonController._signature()` keys on `category::offer`. The `QwenWallBackend` offers are non-deterministic (same wall, different phrasing each model call) → signature never matches → back-off never fires → duplicate offers spam. Fix: `tick()` re-evaluates the *same* cached `WallVerdict` from ingest time, not a fresh model call. No qa-gated change needed.

5. **No defects in qa-gated modules.** Nothing to flag to the orchestrator.

**→ T-302 picks up:** implement `AttentionLayer.tick()` + the background timer in `live.py` using the design in `docs/architecture/phase3-invariants.md` §3. T-302 is NOT qa-gated if it only adds `tick()` to the orchestrator and a timer in `live.py` without changing gate/summon/wall logic.

---

## Prior state — 2026-06-15 (T-204 DONE → PHASE 2 COMPLETE)

**Phase:** phase_2 — Local understanding → **COMPLETE.** All four Phase-2 tasks done: T-201 (spike), T-202 (summarizer backend), T-203 (wall-detection backend, qa-tuning approved), T-204 (backend swap + live verification). Suite **264 green**, ruff clean. On `main`, not pushed.

**T-204 (backend swap) — DONE.** Wired the real Qwen2.5/MLX backends behind the frozen seams in the `--live` path. No core module changes. Backend selection:
- **Default (mock/heuristic):** `python -m jarvis` and `uv run pytest` remain model-free. No change.
- **Local brain:** `python -m jarvis --live --local-brain` constructs ONE shared `QwenModel()` and injects it into both `QwenSummarizerBackend` and `QwenWallBackend`. Weights loaded once on first inference, shared across both seams.

**Live verification on M5 with `--local-brain` (verbatim, not fabricated):**
- Path-B: "What was the date of the conference again?" → `QwenWallBackend` returned **`factual_gap @ 0.90`** → ENGAGEMENT `wall:factual_gap` fired. Living summary updated via `QwenSummarizerBackend`.
- Path-A: "Jarvis add this to my calendar" (ASR: "Jarvis said this to my calendar") → wake word detected → **ENGAGEMENT `summon`** fired immediately.
- The full live transcript + context is in `docs/audio/live-smoke.md` (T-204 addendum section).

**Honest notes (same qa carry-forwards as T-203):**
1. The question-form T-105 trigger ("What was the date of the conference again?") fires `factual_gap @ 0.90–0.95` reliably. When surrounded by the full T-105 context (declarative "I keep forgetting the details" + ASR artifacts), the model returned `is_wall=False` — the **declarative factual_gap miss** documented in T-203. Tested bare question → fires; question + minimal context → fires; full T-105 script with ASR artifacts → misses. Accepted v0 tradeoff (T-503 lever).
2. The `interjection_confidence_floor` was NOT changed (qa carry-forward; T-503 lever).
3. The Path-B re-check in `run_live` is still the trailing re-ingest affordance — the continuous real-time SummonController re-evaluation is Phase 3 (T-302).

**→ Phase 3 picks up** (core-engineer): T-301/T-302 — wire `TurnTakingGate` to real Silero VAD timing events + build the continuous real-time Path-B SummonController re-evaluation. **Also note:** the one-clock invariant (gate ≡ window ≡ Utterance.ts) must be re-verified in Phase 3 (see memory index T-301/T-302).

**⚠️ New finding for T-302 (live `--local-brain` test, 2026-06-15):** the same `factual_gap` wall fired the interjection **twice** (ingest + trailing re-check) with **different `offer` text each time**, because `SummonController`'s back-off de-dupes on the `category::offer` signature (T-007) and the **Qwen backend produces non-deterministic offers** for the same wall → the signature never matches → no de-dupe. Harmless in plain v0 (Path B evaluated once at ingest), but **T-302's continuous re-evaluation will spam duplicate offers** unless back-off keys on a **stable** signature (`category` alone, or wall identity) or the offer is generated once and cached per wall. Changing the back-off signature is a `SummonController` change → **qa-tuning-gated**. (Captured in memory: `summon-backoff-nondeterministic-offer`.)

**T-203 verdict (qa-tuning): APPROVED.** Contract conformance fully pinned by the 57 model-free tests (frozen `WallVerdict`, NONE iff ¬is_wall, clamp, offer="" non-wall, graceful `none()` fallback, raw confidence). The `factual_gap` recall miss is **accepted as a deliberate precision-first tradeoff for v0** — grounded in the success metric (a miss costs *recall*/silence, not *precision*; precision = useful ÷ total Path-B fires). I independently re-ran the live test (4/5) and probed 6 factual_gap phrasings: **question-form gaps fire (incl. the exact T-105 live trigger), declarative gaps miss** — category is partially reachable, not dead, and the live-smoke Path-B path survives the swap.

**Two items flagged to the orchestrator (neither blocks; both Phase-5 T-503 / human):**
1. **7B escalation** for factual_gap recall — already deferred, needs joint-budget + a human latency call.
2. **`interjection_confidence_floor` recalibration** — the Qwen backend emits near-binary confidence (~0.95 on fires), so the 0.70 floor is *inert* for it (the binary `is_wall` is the real gate). Floor stays sound; whether to retune is a Phase-5 T-503 question AND a qa-gated change — not decided unilaterally.

**→ T-204 (swap mock→local in orchestrator) is now UNBLOCKED** (local-ml-engineer). Carry-forward in the T-204 Notes: the swap preserves the T-105 live Path-B trigger; don't change the floor in T-204 (qa-gated); declarative-factual_gap recall is the T-503 lever.

---

## Prior state — 2026-06-15 (T-202 done → local summarizer backend shipped; Phase 2 in progress)

**Phase:** phase_2 — Local understanding (ACTIVE). T-201 (spike) and T-202 (summarizer backend) are **DONE** (local-ml-engineer). Suite **207 green**, ruff clean. On `main`, not pushed.

**What landed (T-202):**
- **`src/jarvis/ml/`** — new Phase-2 SLM package. Three files: `__init__.py`, `qwen.py` (`QwenModel` shared lazy loader), `summarizer.py` (`QwenSummarizerBackend`).
- **`tests/test_qwen_summarizer.py`** — 25 tests: 24 model-free (message construction, backend adapter, lazy-import boundary, Protocol conformance) + 1 optional live test (self-skips when weights unavailable; PASSED on this M5 with cached weights).
- **`docs/ml/slm-backend.md`** — SLM runtime doc (model choice, shared-loader design, summarize + detect_wall contracts).
- **`mlx-lm>=0.31.3` promoted** from `slm-spike` group to real `[project.dependencies]` (same pattern as `mlx-whisper` at T-104).
- **DECISIONS.md** — new entry for dep promotion + shared-loader design.

**Shared-loader design (critical for T-203):**
- `QwenModel` in `src/jarvis/ml/qwen.py` is the ONE model loader. It lazily calls `from mlx_lm import load, generate` on the first `generate()` call, then caches `(model, tokenizer)`.
- `QwenSummarizerBackend(model)` takes it via injection.
- T-203's `QwenWallBackend(model)` will reuse THE SAME `QwenModel` instance — **construct once at startup, inject into both**.
- Chat template is applied inside `QwenModel.generate()` — callers just pass a message list.

**→ T-203 (local wall-detection backend) is NEXT:**
- Implement `QwenWallBackend` in `src/jarvis/ml/wall.py`.
- Reuse the same `QwenModel` loader.
- Return `WallVerdict` dataclass (parse model JSON; on failure return `WallVerdict.none()`).
- DO NOT threshold confidence — that's SummonController policy.
- **T-203 IS qa-tuning-gated** (wall behavior = the success metric). Submit for qa-tuning review before marking done.
- Prompt design stub in `docs/ml/slm-backend.md` §wall-detection.
- Then T-204 wires mock→local backend in the orchestrator.

---

## Prior state — 2026-06-15 (T-201 done → Qwen2.5 size frozen; Phase 2 active)

**Phase:** phase_2 — Local understanding (ACTIVE). T-201 (Qwen2.5/MLX runtime spike + joint ASR coexistence budget) is **DONE** (local-ml-engineer, this session). Suite **182 green**, ruff clean. On `main`, not pushed.

**What landed:**
- **`docs/ml/qwen-coexistence-spike.md`** — full spike doc: methodology, exact models/quant used, audio clip provenance, isolated + joint + sustained measurements, 4-scenario wall-detection quality matrix, recommendation + honesty box. Matches the rigor of `docs/audio/asr-spike.md`.
- **Two DECISIONS.md entries** — Qwen2.5-3B selected + dep-group policy for mlx-lm.
- **`pyproject.toml`** — `mlx-lm` added to `slm-spike` uv dependency group (isolated; NOT yet in real deps).
- **`uv.lock`** updated (mlx-lm + transformers/tokenizers/sentencepiece/safetensors/protobuf).

**Key findings:**
- **1.5B eliminated.** Returns `is_wall: false` with confidence 0.0 on every input — including unambiguous `explicit_ask` cases. Non-functional for detect_wall regardless of latency/memory.
- **3B selected: `mlx-community/Qwen2.5-3B-Instruct-4bit`.** 3/4 correct in 4-scenario quality test. Joint ASR+SLM budget: **657 ms median** (ASR 40 ms + summarize 250 ms + detect_wall 366 ms) vs 2,000 ms → **1,343 ms margin**. Peak joint RSS 3,271 MB on 64 GB machine. No thermal throttling.
- **ASR stays `base.en`** — SLM already dominates at ~600 ms; ASR is only ~40 ms; `small.en` saves nothing meaningful while adding memory pressure.
- **MUST use `tokenizer.apply_chat_template`** (system/user messages) — NOT raw string prompts. Raw prompts caused repetition/degradation on both models AND inflated latency (~2× slower on 3B summarize).
- **One known false positive** in 3B wall detection: it flags a clear decision ("PR in 10 min") as `explicit_ask`. This is a prompt-engineering gap T-203 will close — not a fundamental model capability failure.

**⚠️ For T-202/T-203 (next tasks):**
- Load the model once at startup (not per-call) — model loading takes ~300 ms (cold, from cache).
- Share one loaded model+tokenizer instance between `summarize()` and `detect_wall()` (they run sequentially).
- Use `mlx_lm.generate(model, tokenizer, prompt=..., max_tokens=..., verbose=False)`.
- `mlx-lm` must be promoted from `slm-spike` group into real `[project.dependencies]` at T-202 time.
- `docs/ml/slm-backend.md` (the role spec's "first task" doc) still needs to be written — do it at T-202 time before implementing.

**→ Phase 2 continues:** T-202 (local summarizer backend) is **UNBLOCKED** — pick it up next.

---

## Prior state — 2026-06-15 (hotfix: live-run mic teardown race)

**`python -m jarvis --live` shutdown crash fixed.** It raised `AttributeError: 'NoneType' object has no attribute 'close'` in `mic.py` `stop()`: the live countdown timer and the main-thread context-manager teardown both called `stop()`, both passed the `is not None` check, and one nulled `self._stream` mid-flight → the other dereferenced `None`; the double stop/close also emitted the PaMacCore `-50`. **Fix:** `stop()` now atomically *claims* the stream under a new `self._lock` (exactly one caller stops/closes it) and suppresses teardown errors. 3 regression tests (idempotent stop, suppressed teardown error, 8-thread concurrent stop). Suite **182 green**, ruff clean. The residual `PaMacCore … err='-50'` line that may still print is **C-level CoreAudio stderr from PortAudio on AUHAL teardown — not a Python error** (the run exits 0); deliberately NOT fd-suppressed (redirecting fd 2 is thread-unsafe here and would mask real audio errors). Fixed directly by the orchestrator (not delegated) — `mic.py` is sensing-engineer's lane but not a review-gated module. On `main`, not pushed.

---

## Prior state — 2026-06-15 (T-104 + T-105 done → PHASE 1 COMPLETE)

**Phase:** phase_1 — Real ears → **COMPLETE.** Phase 2 (Local understanding) is next. **T-104 (`MicSource`) and T-105 (live-transcript smoke test) are DONE** (sensing-engineer, this session). Suite **179 green** (167 baseline + 12 MicSource tests), ruff lint+format clean. Worked on `main`, did NOT push.

**The ambient half now runs on real audio end-to-end.** Live on this M5: mic → Silero VAD → mlx-whisper `base.en` → `Utterance` → rolling window → living summary → wall detection → dual-summon. **Both engagement paths fired live** (verbatim in `docs/audio/live-smoke.md`).

**What landed:**
- **`src/jarvis/audio/mic_source.py` (T-104)** — **`MicSource(TranscriptSource)`**: the live fill of the frozen `TranscriptSource` seam — drops into `AttentionLayer.run()` with **zero core change**, replacing `ScriptedSource`. Consumes an `AudioSource` through `SileroVad`, brackets each speech segment off the VAD edges, concatenates its frames, transcribes once at the `speech_end` edge, yields `Utterance(speaker, text, ts)`; drives the shared `TurnTakingGate`; flushes an open segment at end-of-stream; drops empty ASR text. **`Transcriber` Protocol seam** (audio-path analogue of `SummarizerBackend`/`WallBackend`/`FrameClassifier`): default `MlxWhisperTranscriber` (mlx-whisper `base.en`, lazy import) / test `FakeTranscriber`. **`mlx-whisper` promoted from the `asr-spike` uv group into real `[project.dependencies]`** (demoted out of the spike group, which now lists only the `pywhispercpp` fallback).
- **`src/jarvis/live.py` + `python -m jarvis --live` (T-105)** — the real pipeline on live mic audio (mic/MLX imports **lazy** so `uv run pytest` never touches a mic; default suite stays green, CI mic-free). Flags: `--seconds`, `--say "TEXT"` (macOS `say` loopback), `--device` (BlackHole digital loopback), `--stop-after`.
- **LIVE SMOKE TEST RAN on this M5 (verbatim, not fabricated — `docs/audio/live-smoke.md`):**
  - **Path A:** "Jarvis, add that to my calendar for 7." → **ENGAGEMENT (trigger: `summon`)**.
  - **Path B:** "What was the date of the conference again?" → `WallDetector` **`factual_gap @ 0.80`** → after the politeness gap → **ENGAGEMENT (trigger: `wall:factual_gap`)**, offer "I can find that — want me to?" + a living-summary update.
- **Found + fixed a real T-104↔orchestrator integration bug** (DECISIONS.md): `MicSource` stamped `ts` from the VAD frame timeline (~0-based) but the live `RollingWindow` evicts against `time.monotonic` (~1.2 M s since boot) → **every live utterance evicted instantly** → Path B never saw the wall line. Fix: `MicSource` accepts an optional injected `now`; `run_live` passes the **same** real clock to gate + window + `MicSource` so `ts` and eviction share one timeline. The frame-derived default is unchanged (T-104 unit tests still assert it) — only the live case injects the real clock. New regression test.

**Honest caveats:** BlackHole is a *digital* (best-case) loopback — real-room WER is still Phase 5 (T-502). The Path-B *fire cadence* used a `run_live` trailing re-check standing in for the not-yet-built **continuous real-time Path-B loop (T-302, Phase 3)** — detection/confidence/gate-timing are all the real live pipeline; only the re-poll cadence is a smoke-test affordance (the v0 orchestrator only evaluates Path B at utterance-ingest, before the ~2 s gap opens).

**⚠️ Still pending with local-ml-engineer:** the **ASR + Qwen2.5 concurrent always-on M5 joint budget** (combined latency / memory / GPU contention / sustained thermal) must be measured *before either side freezes model sizes* — see `asr-spike.md` §coexistence. `base.en` was chosen to protect SLM headroom.

**→ Phase 2 (Local understanding) picks up** (local-ml-engineer): T-201 Qwen2.5/MLX size spike, then T-202/T-203 the real `summarize()` / `detect_wall()` backends behind the **frozen `SummarizerBackend` / `WallBackend` seams** (replacing the heuristic mocks), then T-204 swap mock→local and re-run core tests green. The orchestrator + the frozen seams don't change for the swap. Also note for Phase 3 (T-302): the live Path-B needs the continuous real-time SummonController re-evaluation the smoke test stubbed.

---

## Prior state — 2026-06-15 (T-102 + T-103 done → mic capture + Silero VAD live)

**Phase:** phase_1 — Real ears (in progress). **T-102 (always-on mic capture loop + `AudioSource`) and T-103 (Silero VAD) are DONE** (sensing-engineer, this session). Suite **167 green** (135 baseline + 18 audio-source + 14 VAD), ruff clean. Worked on `main`, did NOT push.

**What landed:**
- **`src/jarvis/audio/` package** — the always-on ears in front of the frozen `TranscriptSource` seam.
  - **`AudioSource` abstraction** (`source.py`, T-102): a `Protocol` yielding fixed-size `AudioFrame` (16 kHz mono float32, 512-sample/32 ms — Silero's geometry), so the VAD + all tests consume *frames*, never real hardware (the audio-path analogue of the core's injected-backend discipline). Bounded `RingBuffer` (overwrites oldest + counts `overflows` when full → no unbounded growth, the always-on memory invariant). `FakeAudioSource` (.silence/.tone/.from_pattern) = the hardware-free synthetic-frame stand-in.
  - **`SoundDeviceMicSource`** (`mic.py`, T-102): real PortAudio always-on loop; callback→ring-buffer→consumer; lazy `sounddevice` import; typed `MicPermissionError`/`NoInputDeviceError` (never fabricates audio).
  - **`SileroVad`** (`vad.py`, T-103): consumes `AudioSource` frames, debounces per-frame decisions into clean speech-start/speech-end **edges**, drives an injected `TurnTakingGate` (the frozen T-006 edge seam) — **emits edges, never timestamps; the gate owns the clock**, so the same gate + `SummonController` logic the Phase-0 `ScriptedSource` drove is now driven by real audio. Configurable threshold + hysteresis. `FrameClassifier` seam isolates torch: default `SileroFrameClassifier` (real model, lazy) / test `EnergyFrameClassifier` (RMS) so the gate-driving logic is testable with no torch/mic.
- **LIVE MIC SMOKE TESTS RAN ✅ (permission already granted to this terminal — not fabricated):**
  - T-102 raw capture: ~1.47 s, 46 frames / 23,552 samples @ 16 kHz mono, **0 overflows**, mean RMS 0.0021 (quiet room, real energy).
  - T-103 `test_live_silero_vad_on_mic_optional` **PASSED (not skipped):** real Silero model + real mic, >0 frames processed end-to-end. (The optional live test self-skips if a future run has no device/permission.)
- **New real deps** (not spike groups — this is the shipped always-on runtime now): `sounddevice` (+PortAudio bundled), `numpy`, `silero-vad` (+`torchaudio`; torch already present from the ASR/MLX stack). Two DECISIONS.md entries (mic capture + `AudioSource`; Silero VAD + `FrameClassifier` seam).
- **module-map.md current:** new §"The audio sensing path (Phase 1)" documents the `AudioSource` abstraction + the VAD→gate **edge wiring**; package-layout + ownership reflect `audio/`.

**→ T-104 (MicSource) is NEXT** (sensing-engineer): wire the VAD + `mlx-whisper base.en` into `Utterance` events behind the frozen `TranscriptSource` seam — feed ASR the concatenated frames of each speech segment (start→end window), stamp `Utterance.ts` from the VAD timeline, drive the orchestrator's shared gate with the same edges. Promotes only `mlx-whisper` from the `asr-spike` uv group into real deps. Then **T-105** (live-transcript smoke test). The orchestrator + gate do NOT change for the swap.

**⚠️ Still pending with local-ml-engineer:** the **M5 ASR + Qwen2.5 concurrent always-on joint budget** (combined latency / memory / GPU contention / sustained thermal) must be measured *before either side freezes model sizes* — see the coexistence flag in `asr-spike.md` and the prior-state note below. `base.en` was chosen to protect SLM headroom.

---

## Prior state — 2026-06-15 (T-101 done → Phase 1 ASR runtime selected)

**Phase:** phase_1 — Real ears (kicked off). **T-101 (ASR runtime spike) is DONE** (sensing-engineer, this session).

**Outcome:** **ASR runtime = `mlx-whisper`, model `base.en`** (English-only; `small.en` is the upgrade lever, `whisper.cpp`/`pywhispercpp` is the documented fallback). Benchmarked both candidates on THIS M5 Pro (64 GB) at `base.en` — full method + comparison table + recommendation in **`docs/audio/asr-spike.md`**; two DECISIONS.md entries (runtime choice + spike-dep policy).

**What the spike found:**
- Both runtimes are **~25–125× faster than real time** and **tie on WER** at `base.en` (0.0 % clean short utterance / 1.7 % on a 17 s paragraph — the lone "error" is a "three"/"3" normalization artifact). A realistic ~3.8 s utterance transcribes in **mlx 73 ms / whisper.cpp 52 ms** — negligible vs the **~2 s offer-to-help budget**. ASR is **not** the budget bottleneck; the VAD endpoint wait + the SLM are.
- **Decided on runtime strategy, not speed/accuracy:** mlx-whisper runs on **MLX/Metal/unified-memory — the same stack Qwen2.5 uses (Phase 2)** — so the ambient half standardizes on one accelerator stack to budget. whisper.cpp was marginally faster/leaner (RSS 326 vs 463 MB, no torch dep) → kept as fallback only.
- **No throttling** over a 40× single-session run (this is NOT a multi-hour soak — that's T-504).
- **Honesty caveats:** accuracy measured on clean *synthesized* audio (best case) — re-measure WER on captured noisy audio in Phase 5 (T-502). Streaming/combined-budget not measured here.

**Dependency policy applied:** spike packages (`mlx-whisper`, `pywhispercpp` + transitive torch/mlx) went into an **isolated `asr-spike` uv group** (`uv add --group asr-spike …`), NOT the package's runtime deps. Run the spike with `uv run --group asr-spike …`. **T-104 (`MicSource`) promotes only `mlx-whisper` into real package deps.**

**⚠️ Coexistence flag for local-ml-engineer (joint spike):** this measured ASR in isolation. The real constraint is **ASR + Qwen2.5 running concurrently always-on** on one M5 — measure combined latency / memory / GPU contention / sustained thermal **before either side freezes model sizes**. `base.en` was chosen to leave the SLM the most headroom. (See `asr-spike.md` §coexistence.)

**→ Phase 1 picks up:** **T-102 (always-on mic capture loop)** and **T-103 (Silero VAD gating)**, then **T-104 (`MicSource`)** wiring `mlx-whisper base.en` behind the **frozen `TranscriptSource` seam** — feeding real `Utterance` events (ts stamped from the VAD timeline) and driving the same `TurnTakingGate` with real VAD edges + a real `time.monotonic` clock. The orchestrator + gate don't change for the swap. Did NOT push (working on `main`).

---

## Prior state — 2026-06-16 (T-008 done → PHASE 0 COMPLETE)

**Phase:** phase_0 — Foundations → **COMPLETE.** Phase 1 (Real ears) is next.

**T-008 (AttentionLayer orchestrator + end-to-end MOCK pipeline) is DONE** (this session). With it, **the ambient → summary → wall → dual-summon pipeline runs end-to-end in mock mode** — the last Phase 0 task. Suite **135 green** (14 new), ruff lint+format clean. The runnable demo (`uv run python -m jarvis`) plays the scripted conversation and prints all four behaviors: two living-summary updates (the 2nd on the Tokyo→ramen topic pivot), a Path-B `factual_gap` interjection → engagement, and a Path-A wake-word summon → engagement — no audio, no model, no network.

**What landed (T-008):**
- **`src/jarvis/adapters/`** — the seam package: `transcript_source.py` (`TranscriptSource` Protocol + `ScriptedSource`), `backends.py` (re-exports `SummarizerBackend`/`WallBackend` from their core homes + `HeuristicSummarizerBackend`, parallel to `HeuristicWallBackend`), `engaged.py` (`EngagedResponder`/`VoiceOutput` Protocols + `PrintResponder`/`PrintVoice` stand-ins).
- **`src/jarvis/attention_layer.py`** — the `AttentionLayer` orchestrator. `ingest(u)` runs the module-map event flow; emits `on_summary_update` / `on_interjection` / `on_engagement`. **It owns handoff assembly** (decision/handoff boundary, T-007): on a `SummonController` decision (either path) it builds the `EngagementHandoff` (`handoff_reason()` + the summary + recent excerpt it owns) and dispatches it through the responder + voice seams. `build(...)` and `run_scripted(...)` classmethods do the common wiring.
- **`ScriptedSource` drives the shared gate + injected clock** so the politeness gap elapses deterministically (DECISIONS.md 2026-06-16): each `ScriptedLine(speaker, text, gap)` carries the silence after it; the source fires the gate's `on_speech_start`/`on_speech_end` edges (the VAD edges `MicSource` will emit) and advances the clock by `gap`. No `time.sleep`, no internal clock — the whole run is deterministic.
- **`src/jarvis/clock.py` (`ManualClock`)** — the deterministic injected clock moved into the package so the demo doesn't import from `tests/`; `tests/clock.py` now re-exports it as `SimulatedClock` (one implementation, harness name unchanged — every existing core-module test still imports `SimulatedClock` from `tests.clock`).
- **`src/jarvis/demo.py` + `__main__.py`** — the runnable entry point (`python -m jarvis`).
- **`tests/test_attention_layer.py`** — 14 acceptance tests on `ScriptedSource` + `FakeResponder`/`FakeVoice` + `SimulatedClock`: the headline all-three-behaviors run, Path A (immediate, builds handoff, ignores gate), Path B (fires after the gap, holds when speech resumes too soon, backs off the repeat offer, drops below the floor), summary delta-update via the injected backend, cold-start silence, determinism, and the source-drives-clock/gate unit check. All assert emitted events / seam calls, no private fields.

**Phase 0 modules: ALL COMPLETE** (T-001…T-010). The six deep core modules + orchestrator + `TranscriptSource` seam are built, unit-tested, and run end-to-end mock-green. module-map.md is current (orchestrator + `TranscriptSource` seam frozen; Phase 0 modules marked complete).

**→ Phase 1 (Real ears) picks up:** the ASR/runtime spike (T-101, mlx-whisper vs whisper.cpp on the M5) and the always-on mic + Silero VAD path (T-102/T-103), then **`MicSource` (T-104) replacing `ScriptedSource`** behind the **frozen `TranscriptSource` seam** — feeding real `Utterance` events and driving the same `TurnTakingGate` with real VAD `on_speech_start`/`on_speech_end` edges + a real `time.monotonic` clock. The orchestrator and gate do not change for the swap. (sensing-engineer owns this; `docs/audio/asr-spike.md` is their first deliverable.) Note for Phase 1: the cheap `_has_wall_signal` pre-filter and the wake-word match are regex over text in `attention_layer.py` — fine for v0, revisit if ASR casing/punctuation differs from the scripted text.

**Open for the human:** API keys (Anthropic, ElevenLabs) still only needed once Phase 4 (engaged path) begins — mock mode covered Phases 0. `Start_Here/` is still an untracked nested git repo (untouched).

---

## Prior state — 2026-06-16 (T-007 SummonController built → in `review`, awaiting qa-tuning)

**Phase:** phase_0 — Foundations.

**T-007 (SummonController) is built and in `review`** (core-engineer, this session) — **awaiting mandatory qa-tuning review** before merge (it carries the success metric). Suite **121 green**, ruff lint+format clean.

- **`SummonController`** (`src/jarvis/core/summon_controller.py`): the asymmetric dual-path machine. **Path A** `on_summon(detail="") -> SummonDecision(SUMMON)` — immediate, unconditional, ignores gate/wall/floor/back-off. **Path B** `consider_interjection(verdict) -> SummonDecision | None` — fires only when ALL hold: `is_wall ∧ confidence ≥ floor ∧ ¬gate.speech_resumed() ∧ gate.politeness_gap_elapsed() ∧ not-already-offered`. Holds an **injected `TurnTakingGate`** and reads **no clock of its own** (timing comes through the gate's pure predicates). **abort-on-resume is checked before the gap** so a latched resume suppresses even a stale-elapsed gap. **Back-off** de-dupes by `category::offer` signature (confidence excluded); only an actual fire arms it.
- **Threshold:** `interjection_confidence_floor=0.70` (default; matches the prototype's `WALL_CONFIDENCE_TO_SPEAK`), constructor-injected + guarded to `[0,1]`, inclusive cut (`>=`). It lives in SummonController, NOT the detector (the detector surfaces confidence raw). This is the one knob Phase-5 (T-503) sweeps.
- **Decision/handoff boundary (structural call, DECISIONS.md):** SummonController is a **pure decision machine** — it emits a `SummonDecision`, it does NOT build the `EngagementHandoff` (it holds neither the summary nor the window). **The orchestrator (T-008) assembles the handoff** from the decision + its summary/excerpt; `SummonDecision.handoff_reason()` gives the `"summon"`/`"wall:<category>"` wire string for free.
- **New frozen types** in `src/jarvis/types.py` (T-007): `TriggerReason` (StrEnum: `summon`/`interjection`), `Interjection` (`category: WallCategory`, `offer`, `confidence`), `SummonDecision` (`reason`, `interjection | None`, `detail`, `.handoff_reason()`). `EngagementHandoff`'s shape is frozen here too; it is *built* at T-008.
- **Tests:** `tests/test_summon_controller.py` (24 tests on the `SimulatedClock` + the real injected gate + `wall()`/`no_wall()` fakes): Path A immediacy (engages with no wall + gap not elapsed, ignores resume/back-off), Path B all-conditions gating (drop-if-any-one-fails), abort-on-resume (incl. the stale-gap precedence + re-arm-after-fresh-silence), back-off (same-signature, per-signature, twice-in-a-row, confidence-excluded, dropped-wall-doesn't-arm), confidence-floor boundary (inclusive, just-below, configurable, range guards).

**→ After T-007 passes review, the last two Phase 0 tasks are: T-008 (orchestrator + end-to-end MOCK run, deps T-002..T-007) and T-010 (interjection-precision eval, qa-tuning, deps T-007).** T-008 wires the modules + `ScriptedSource` + fakes, assembles the `EngagementHandoff` from the `SummonDecision`, and is where the `adapters/` package likely lands.

---

## Prior state — 2026-06-16 (T-005 + T-006 APPROVED by qa-tuning → T-007 unblocked)

**Phase:** phase_0 — Foundations.

**T-005 (WallDetector) and T-006 (TurnTakingGate) are DONE** — qa-tuning reviewed both (mandatory triggers) and **approved both**; moved `review → done`. Suite **97 green**, ruff clean. Full review verdict + non-blocking coverage notes + what-T-010-must-measure are in `docs/qa/working-notes.md` (and `eval-plan.md`).

**→ T-007 (SummonController) is now UNBLOCKED** — both of its deps (T-005, T-006) are done. It consumes `WallDetector.detect(...).confidence` (applies `WALL_CONFIDENCE_TO_SPEAK=0.70` here, NOT in the detector) and the gate's three predicates (Path-B gating on `politeness_gap_elapsed`, abort on `speech_resumed`, plus back-off). T-007 also carries a mandatory qa-tuning review before merge.

**Review highlights (qa-tuning):** WallVerdict schema is sound + complete for downstream and the real-backend contract (module-map §"Contract for the real backend (T-203)") is unambiguous for local-ml-engineer; speak-threshold correctly kept out of the detector; gate abort-on-resume verified (a resume during the gap re-arms and restarts the clock — a stale gap can never fire); single clock source; thresholds injected + guarded. No defects found — review was behavioral-soundness + testability only (the numeric precision eval can't exist until T-008 + T-010).

_(Prior in-review note for T-005/T-006 superseded by the above.)_

- **T-005 — `WallVerdict` FROZEN** (`src/jarvis/types.py`): `is_wall: bool`, `category: WallCategory` (StrEnum: `unanswered_question | factual_gap | stuck_point | explicit_ask | none`), `confidence: float` [0,1], `offer: str`; `WallVerdict.none()` for the non-wall case. **`WallDetector`** (`core/wall_detector.py`) is a thin sensor over the swappable `WallBackend` Protocol seam; **`HeuristicWallBackend`** is the Phase-0 backend. **The detector applies NO confidence threshold** — the speak gate (`WALL_CONFIDENCE_TO_SPEAK`) is SummonController policy (T-007). The T-009 `WallVerdictLike` TODO in `tests/fakes.py` is **resolved** (now returns the real `WallVerdict`; `wall()`/`no_wall()` build real verdicts). **Real-backend contract for local-ml-engineer (T-203)** is written in `module-map.md` §"Contract for the real backend" — they implement `WallBackend.detect_wall` to this exact frozen shape.
- **T-006 — TurnTakingGate event-input API DESIGNED** (the gap qa-tuning flagged): `on_speech_start()` / `on_speech_end()` edge events; events carry no `ts` (gate stamps from injected `now()`); silence measured from the most recent `on_speech_end()`; `speech_resumed()` latches on a gap-interrupting resume, clears on next `on_speech_end()`; the 3 predicates are pure reads. Asymmetric thresholds `settle_seconds=0.6` (Path A) / `politeness_gap_seconds=2.0` (Path B) are constructor-injected. Decision logged in DECISIONS.md.
- **T-007 (SummonController) unblocks once T-005 + T-006 pass qa-tuning review.** It consumes `WallDetector.detect(...).confidence` (applies `WALL_CONFIDENCE_TO_SPEAK` here) and the gate's three predicates (Path B gating + abort-on-resume + back-off).

---

## Prior state — 2026-06-16 (T-002 + T-003 + T-004 done)

**Phase:** phase_0 — Foundations.

**T-004 (LivingSummary delta-update) is DONE** (core-engineer, this session).
- **`LivingSummary`** (`src/jarvis/core/living_summary.py`): holds an injected `TopicShiftDetector` + tracks the summary's basis keyword set; `consider_update(window) -> bool` re-summarizes **only** on a detected shift via the injected `SummarizerBackend`. `text` exposes the current summary.
- **`SummarizerBackend` seam FROZEN** — `summarize(transcript: str, prev: str) -> str`, a `typing.Protocol` in `living_summary.py`. **Reconciled with `FakeSummarizer`: identical, no disagreement.** The real Qwen2.5/MLX backend (T-202) drops in behind it untouched. (Not yet hoisted to a shared `adapters/backends.py` — that consolidates at T-008.)
- **Two policy fences live in LivingSummary, not TopicShiftDetector** (the deliberate scope split): `MIN_UTTERANCES_FOR_SUMMARY=3` (cold start) + `MIN_UTTERANCES_SINCE_UPDATE=2` (debounce). First summary fires when cold-start clears; after that, only on a shift past the debounce.
- **Window-sizing gotcha for T-008:** a shift only registers once the *old* topic ages out of the `RollingWindow` (by count/time) — a wide window holding both topics keeps basis/current overlap above threshold. Correct "actually moved on" behavior; size the orchestrator's window for it. (The T-004 shift tests use a tight window to model the pivot.)
- Tests on the T-009 harness (`SimulatedClock` + `RollingWindow` + `FakeSummarizer`). Suite **60 green**, ruff lint+format clean. Commits `[T-004]` claim + feat on `main`.

### What's left in Phase 0 (after T-004)
Five core tasks remain before the phase closes:
- **T-005 (WallDetector)** — **mandatory qa-tuning review.** Open design item: freeze `WallVerdict` **with local-ml-engineer** first (harness uses `WallVerdictLike` until then; field names already match, swap is import-only).
- **T-006 (TurnTakingGate)** — **mandatory qa-tuning review.** Open design item: the gate's event-*input* API is still undesigned (design it **with qa-tuning**); the clock side is settled (`now: Callable[[], float]`).
- **T-007 (SummonController)** — **mandatory qa-tuning review.** Depends on T-005 + T-006.
- **T-008 (AttentionLayer orchestrator + end-to-end MOCK run)** — depends on T-002..T-007. Where the `SummarizerBackend`/`WallBackend` seams + `ScriptedSource` + fakes get wired and the `adapters/` package likely lands.
- **T-010 (interjection-precision eval)** — qa-tuning, depends on T-007.

All three of T-005/T-006/T-007 carry mandatory qa-tuning review (they are the success-metric-critical timing/precision logic).

---

## Prior state — 2026-06-15 (T-002 + T-003 done)

**Phase:** phase_0 — Foundations.

**T-002 (data types + RollingWindow) and T-003 (TopicShiftDetector) are DONE** (core-engineer, prior session).
- **Clock convention pinned:** `now: Callable[[], float]` is the single clock-injection form for every time-bounded module (module-map.md §"Cross-cutting design constraints" #1) — closes T-009 interface gap #1. Not a `Clock` object.
- **`Utterance` is FROZEN** (`src/jarvis/types.py`): `speaker`, `text`, `ts`; `ts` required and producer-supplied (no hidden `time.monotonic` default). sensing-engineer's `MicSource` must stamp `ts` from the VAD timeline.
- **`RollingWindow`** (`src/jarvis/core/rolling_window.py`): bounded by count AND elapsed time, injected `now`, evicts on add *and* on read so the window ages during silence (divergence from the prototype's internal clock + newest-ts eviction).
- **Shared text helpers** (`src/jarvis/core/text.py`): `keywords()`/`jaccard()` ported from the prototype, reused by RollingWindow and TopicShiftDetector.
- **`TopicShiftDetector`** (`src/jarvis/core/topic_shift.py`): pure decision, `shifted()` = Jaccard < `threshold` (default 0.30, constructor-injected). Cold-start minimum / debounce deliberately deferred to T-004's `LivingSummary` (scope fence in module-map.md).
- Tests use the T-009 harness (`SimulatedClock`). Suite **48 green**, ruff lint+format clean. Commits: `[T-002]`/`[T-003]` claim + feat on `main`. (T-004 has since landed — see current state above for what's left in Phase 0.)

---

## Prior state — 2026-06-15 (T-001 scaffold done)

**Phase:** phase_0 — Foundations.

**T-001 (Python project scaffold) is DONE.** The real `jarvis` package now exists:
- **src-layout** `src/jarvis/` + `pyproject.toml` (`requires-python = ">=3.11"`, hatchling).
- **Toolchain: uv** — the machine had no 3.11 (system python is 3.9.6), so uv 0.11.21 was installed via its non-interactive standalone installer to `~/.local/bin` and pins **CPython 3.11.15**. `uv.lock` + `.python-version` are committed.
- **pytest** wired (2 smoke tests pass), **ruff** lint+format clean. `prototypes/` is excluded from ruff (it's reference, not the package).
- First deliverable shipped: **`docs/architecture/module-map.md`** — the seam contract (six core modules + the TranscriptSource / EngagedResponder / VoiceOutput adapter seams) the other agents implement against.
- Decision logged in `DECISIONS.md` (uv + src-layout + pytest + ruff).

**Running the toolchain:** `uv` lives at `~/.local/bin/uv` — either add it to PATH or `export PATH="$HOME/.local/bin:$PATH"`. Then `uv run pytest`, `uv run ruff check .`, `uv run ruff format .`.

The reference prototype at `prototypes/attention-layer/` still runs end-to-end in mock mode and validates the module shapes. It is **reference, not the package** — its logic is ported into `src/jarvis/` deliberately in T-002+ (e.g. RollingWindow must take an injected clock, unlike the prototype's internal `time.monotonic()`).

## Also done — 2026-06-15 (T-009 test harness)

**T-009 (qa-tuning's simulated-clock + fakes harness) is DONE.** The shared test
scaffolding the core-module tests build on now exists:
- `tests/clock.py` — `SimulatedClock` (inject `clock.now`, drive time with
  `clock.advance(s)`; monotonic-by-construction). No real `sleep`.
- `tests/fakes.py` — `FakeSummarizer`, `FakeWallBackend`, `FakeResponder`,
  `FakeVoice` (each presets returns + records calls), plus a `WallVerdictLike`
  stand-in and `wall()`/`no_wall()` helpers.
- `tests/conftest.py` — fixtures (`clock`, `fake_summarizer`, …).
- `tests/test_harness.py` — 22 self-tests. Suite green (24 total), ruff clean.
- Conventions written to `docs/qa/eval-plan.md` (qa-tuning's first deliverable);
  the T-010 interjection-precision eval spec is stubbed there, written next.

**Two interface gaps for core-engineer to close while building the modules:**
1. **T-006 (TurnTakingGate):** the module map freezes the 3 output predicates
   but not the gate's event-*input* API, and mentions both a `now=` callable and
   a `Clock` object without picking one. The harness clock supports both forms —
   pin one in T-006. (Recommend the `now: Callable[[], float]` form the rest of
   the module map already uses.)
2. **T-005 (WallVerdict):** not frozen yet; harness uses `WallVerdictLike` with
   matching field names (TODO marker in `tests/fakes.py`). Freeze the real type
   *with* local-ml-engineer; the swap is then import-only.

### What's next

- **T-002 (Core data types + RollingWindow) is the next unblocked task** (depends only on T-001). Freeze `Utterance` there; inject the clock into RollingWindow's time-bound (use the T-009 `SimulatedClock` — pass `clock.now`). See the `## Next` section of the module map and `docs/architecture/working-notes.md` for the T-002 prep notes.
- T-003 and T-005 are also unblocked (depend only on T-001). **T-009 harness is now landed** — core-module tests (T-002…T-008) should build on `tests/clock.py` + `tests/fakes.py`, not reinvent.
- Two runtimes are deferred to spikes: ASR (mlx-whisper vs whisper.cpp) in Phase 1, Qwen2.5 size in Phase 2.

### Open questions for the human

- API keys for live mode (Anthropic, ElevenLabs) are not set yet — only needed once Phase 4 (the engaged path) begins; mock mode covers Phases 0–3.
- `Start_Here/` is still an untracked nested git repo (the bootstrap kit). Decide whether to submodule it or leave it out of version control. (Untouched this session.)
