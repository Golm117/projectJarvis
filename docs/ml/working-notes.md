# Working notes — ml

_(scratchpad for in-flight thinking; promote durable findings to topic files)_

---

## T-203 done (2026-06-15) — QwenWallBackend shipped (in review for qa-tuning)

**What landed:**
- `src/jarvis/ml/wall.py` — `QwenWallBackend` (thin adapter over shared `QwenModel`).
  `detect_wall(transcript, summary) -> WallVerdict`. Precision-over-recall prompt.
- `tests/test_qwen_wall_backend.py` — 57 model-free unit tests + 1 live test.
- `src/jarvis/ml/__init__.py` — now re-exports `QwenWallBackend`.
- `docs/ml/slm-backend.md` — wall-detection section updated from stub to done.

**Suite: 264 green (207 baseline + 57 new), ruff clean.**

**Live run results (5 scenarios):** 4/5 PASS.
- T-201 false positive (clear decision falsely flagged): FIXED, PASS.
- stuck_point, explicit_ask, plain_statement: PASS.
- factual_gap: FAIL — is_wall=False despite confidence=0.90. The model returned
  high confidence but still chose not to flag it as a wall. This is unusual and
  qa-tuning should scrutinize it — it may indicate that the precision prompt
  over-corrects for factual_gap. The HeuristicWallBackend correctly catches this
  pattern; the Qwen backend's factual_gap recall is lower than the heuristic.

**For T-204:** T-204 should be aware that swapping the mock→real backend will
change behavior for factual_gap — the real backend may be less sensitive than
the heuristic. qa-tuning must evaluate before T-204 merges.

**Task status:** `review` (qa-tuning gated). Do NOT merge until qa-tuning signs off.

---

## T-202 done (2026-06-15) — local summarizer backend shipped

**What landed:**
- `src/jarvis/ml/__init__.py` — package; re-exports `QwenModel`, `QwenSummarizerBackend`.
- `src/jarvis/ml/qwen.py` — `QwenModel` shared loader. Lazy `mlx_lm` import inside `_ensure_loaded()`. `generate(messages, max_tokens)` applies `tokenizer.apply_chat_template` internally. One instance shared between T-202 + T-203 backends.
- `src/jarvis/ml/summarizer.py` — `QwenSummarizerBackend` (thin adapter). `_build_messages(transcript, prev)` exported for model-free tests. System prompt: concise note-taker + no-hallucination instruction. User prompt: transcript + prev + "write updated summary" instruction. `max_tokens=80`.
- `tests/test_qwen_summarizer.py` — 25 tests (24 model-free unit tests + 1 live test that self-skips when weights unavailable). The live test PASSED on this M5 (weights cached from T-201).
- `docs/ml/slm-backend.md` — SLM runtime choice, shared-loader design, both seam contracts (summarize done; detect_wall stubbed for T-203).
- `mlx-lm>=0.31.3` promoted from `slm-spike` to real `[project.dependencies]`.
- `DECISIONS.md` entry for the dep promotion + shared-loader design.

**Suite: 207 green (182 baseline + 25 new), ruff clean.**

---

## T-203 prep notes (next task) — QwenWallBackend

T-203 adds `src/jarvis/ml/wall.py` with `QwenWallBackend`. Key notes:

1. **Reuse `QwenModel`** — import from `jarvis.ml.qwen`; no new model loading.
2. **Return `WallVerdict` dataclass**, not a dict.  Parse the model's JSON via `json.loads()`; construct `WallVerdict(is_wall=..., category=WallCategory(...), confidence=..., offer=...)`.
3. **On JSON parse failure**, return `WallVerdict.none()` — never raise to the caller.
4. **Do NOT threshold `confidence`** — that's `SummonController.interjection_confidence_floor=0.70` policy.  Surface raw.
5. **Prompt design:** put the JSON schema in the **user** message (not system). Strong "only flag if confident" instruction (3B has a false-positive bias — flags clear decisions as `explicit_ask`).
6. **`max_tokens=120`** (spike: ~366 ms at this budget).
7. **T-203 IS qa-tuning-gated** (wall behavior is the success metric). Submit for qa-tuning review before marking done.

Prompt stub in `docs/ml/slm-backend.md` §wall-detection-backend-contract.

---

## T-201 spike findings (2026-06-15) — now in qwen-coexistence-spike.md

**Key facts to carry into T-202/T-203:**

1. **Model:** `mlx-community/Qwen2.5-3B-Instruct-4bit` via `mlx_lm.load` + `mlx_lm.generate`.
2. **Chat template required:** `tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)` — messages = list of `{"role": "system"|"user", "content": "..."}`.
3. **Latency budget (warm, in-process):**
   - summarize: ~250 ms median (max_tokens=80)
   - detect_wall: ~366 ms median (max_tokens=100)
   - Both well inside the 2 s offer budget (657 ms total joint pipeline including ASR).
4. **Loading pattern:** `model, tokenizer = mlx_lm.load(model_path)` once at startup. Share the instance between summarize and detect_wall (same process, sequential calls). Cold load from cache: ~300 ms.
5. **JSON structured output:** 3B reliably produces valid JSON when instructed, but put the schema right in the user message, not the system message. Keep the schema short (one line, not multi-line) — the shorter the schema description, the more tokens available for reasoning.
6. **False-positive tendency:** 3B has a recall bias — it flags walls even in low-ambiguity cases. T-203 prompt must reinforce "only flag if confident" with concrete examples or a temperature setting (lower temp → less hallucinated confidence).
7. **Wall detection categories the 3B handles well:** factual_gap, unanswered_question, stuck_point. `explicit_ask` is the hardest — model sometimes confuses a rhetorical question with an explicit ask.
8. **mlx-lm version:** 0.31.3 (in slm-spike group). At T-202, promote to real deps — check if a newer version is available.

**Open design question for T-202/T-203:**
- Should summarize and detect_wall share the same model instance in one class, or be two separate classes? The T-008 orchestrator holds separate `SummarizerBackend` and `WallBackend` injections — two separate classes is cleaner for the seam discipline. But they can both lazily load the same underlying model from a module-level singleton or an injected shared cache to avoid double-loading.
- The simplest approach: a `QwenBackend` class that holds `(model, tokenizer)`, implements both `SummarizerBackend` and `WallBackend` protocols, and is constructed once and injected twice (or a shared factory). Decide at T-202 and record in DECISIONS.md.
