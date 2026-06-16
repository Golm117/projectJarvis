# Interjection prior-art & SLM material (research, 2026-06-16)

> Deep-research pass (108 agents, 25 sources, 24/25 claims adversarially verified) on:
> "When should an ambient AI proactively interject?" and "What material can we reuse
> to make the SLM a precision-first interjection gatekeeper?" Prompted by the live
> finding that the wall detector is inconsistent on factual questions (fires on
> "what's 4 times 7?" but not "what's the square root of 81?").

## Verdict

- The **architecture and decision theory are validated** by recent peer-reviewed work.
- There is **no turnkey model or dataset to drop in**: almost all corpora/taxonomies
  concern clarifying a query aimed **at** a search/QA system, **not** interjecting into
  a **human-human** conversation. Prior art transfers as **scaffolding** (taxonomy,
  labels, exemplars, thresholding ideas), not a precision corpus. The human-human gap
  corpus must be **self-built** — our `--capture` tooling (T-502) seeds exactly that.

## Key prior art

| Work | Venue | Relevance to Jarvis |
|---|---|---|
| **LlamaPIE** ([acl](https://aclanthology.org/2025.findings-acl.710/) · [arxiv](https://arxiv.org/abs/2505.04066)) | ACL Findings 2025 | Validates our exact design: small **on-device (Apple Silicon M2)** "when-to-respond" model cascaded with a larger generator; background, non-interrupting. |
| **Horvitz, Mixed-Initiative** ([pdf](http://erichorvitz.com/chi99horvitz.pdf)) | CHI'99 | Act iff p(goal\|E) > computed threshold **p\***; a costly false interjection **raises p\*** → threshold should be **context-dependent**. Our fixed 0.70 floor is the wrong shape; the post-engagement cooldown is a crude p\* move. |
| **Inner Thoughts** ([arxiv](https://arxiv.org/abs/2501.00383)) | CHI 2025 | "Do I have something worth saying now?" — score 8 heuristics (incl. **Information Gap** = our `factual_gap`) into **1–5**, silent below threshold. Beat baseline, preferred 82%. Distillable into the wall prompt. |
| **Clarification-Need Prediction (T1)** ([IJCAI-23](https://www.ijcai.org/proceedings/2023/0738.pdf) · [ACL-23 survey](https://aclanthology.org/2023.acl-long.152/)) | surveys | Formalizes "should I ask/interject at all?" as a binary gate — our exact decision, with a taxonomy. |
| Turn-taking / backchannel (VAP [arxiv](https://arxiv.org/pdf/2205.09812), ICASSP'24, ACL'25) | — | Solve the **timing** axis; acoustic, not "needs-help" detection. We already own timing via the gate → lower priority. |

## Datasets (scaffolding, not drop-in)

`ClariQ` (graded **1–4** clarification-need label — [github](https://github.com/aliannejadi/ClariQ)),
`MIMICS`, `Qulac`, `Abg-CoQA`, `PACIFIC`, and `SWDA` dialogue-act
([hf](https://huggingface.co/datasets/cgpotts/swda)). Use as a **labeling scheme +
few-shot exemplar pool**, not training data (search/QA-query scope, not human-human).

## Recommended next tuning task (qa-gated; measured on the T-502/T-503 eval)

Rework the `QwenWallBackend` prompt, grounded in the above:

1. **Graded interjection-worthiness score** (1–5 Inner-Thoughts / 1–4 ClariQ) replacing
   the inert near-binary `is_wall` + ~0.95 confidence — a graded scale thresholds far
   more consistently (this is the root of both the firing inconsistency and the
   "confidence floor is inert" finding from T-503).
2. **A small structured "Information Gap" reasoning step** before scoring (is there an
   unanswered question? is it factual/answerable? would an offer help?).
3. **Few-shot exemplars from our own `--capture` data**, incl. the failure cases
   (√81, "what do you need?") — the only way to close the human-human scope gap.
4. **Evolve the threshold toward a Horvitz context-aware p\*** (the cooldown is a start).

**Honest caveats:** Inner Thoughts / LlamaPIE evals are human-preference/demos, not
precision/recall benchmarks; no verified 3B-specific exemplar-selection or
calibration recipe was found; the clarification datasets carry a real domain gap.
