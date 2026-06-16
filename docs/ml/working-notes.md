# Working notes — ml

_(scratchpad for in-flight thinking; promote durable findings to topic files)_

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
