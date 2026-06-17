# Wall-detector known limits (v1 levers)

> Status: accepted as known limits in v0 (model: `mlx-community/Qwen2.5-7B-Instruct-4bit`).
> These are 7B reasoning-reliability ceilings, NOT prompt bugs — documented here so we
> don't re-litigate them, and so the fine-tuning path is scoped.

## 1. Answered-question over-fire (the T-511 limit) — NOT promptable

**The bug:** when a factual question is BOTH asked AND answered inside the same rolling
window, the 7B sometimes fails to read the answer line and fires
`unanswered_question @ 0.95` — a false interjection on already-resolved content (a
**precision** bug). Example: `"…Alice: What's 4 times 7?\nBob: It's 28."` → fires, with
reasoning "Nobody answered" (and it even confabulates `offer: "That's 28."`).

**Why it is NOT fixable by prompting (the decisive evidence).** Two prompt iterations
(T-511 r1: an answered-question exemplar; r2: swap the conflicting unanswered exemplar +
enumerate answer phrasings + a second exemplar) each fixed ONLY the exact strings baked
into the exemplars and broke on the next phrasing. Direct real-path probe on the r2
prompt (7B, 5 runs each, greedy decode on the one M5 — same machine throughout):

| Transcript (multi-line window) | Fires |
|---|---|
| `What's 4 times 7?` → `It's 28.` (exact exemplar string) | **0/5 — "fixed"** |
| `What is 4 times 7?` → `It's 28.` (only "What's"→"What is") | **5/5 over-fire** |
| `What's the square root of 81?` → `It's 9.` | **5/5 over-fire** |
| `What is the square root of 81?` → `It's 9.` | **5/5 over-fire** |

The model **pattern-matches the exact question string** in the exemplars rather than
reasoning about whether the question was answered. Real speech has unbounded phrasings,
so exemplars are infinite whack-a-mole — and r2's changes even regressed the √81-answered
case qa had seen silent. Conclusion (matches the prior-art research, `interjection-prior-art.md`):
this is a **3B/7B reading-reliability limit**, fixable only by **fine-tuning** a classifier
on labeled answered/unanswered examples, not by prompting.

**Why it's acceptable for v0:** it requires a question to be *asked and answered within
one window AND then re-evaluated* — the live runs never hit it (real questions you ask are
genuinely unanswered). Narrow edge case; the working system (direct questions fire, confab
trap handled, resolved-by-different-content suppressed) is unchanged.

**The v1 fix (scoped, not done):** fine-tune the wall classifier on labeled
answered/unanswered conversation windows; the `--capture` tooling (T-502) seeds the
dataset. Per the research, the human-human corpus must be self-built.

**Decision:** stop iterating; revert T-511 to the T-510 baseline (`9ac28c5`); document
here. T-511's discarded iteration commits (for reflog recovery if ever needed):
`4d72de9 8a4af08 4f76769 a2ce5e4 c8d2e42`.

## 2. (Pre-existing, lower priority) wh-form declarative recall

"I wonder what X is" style gaps can rate borderline (rating-3 / below the 0.70 floor) and
stay silent — a *recall* miss (cheap on a precision-first metric), already noted in
`threshold-tuning.md`. Also a fine-tuning lever, not a prompt fix.
