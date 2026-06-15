# Working notes — qa

_(scratchpad for in-flight thinking; promote durable findings to topic files)_

## T-009 done (2026-06-15) — harness landed

Durable conventions promoted to `docs/qa/eval-plan.md` §"Test-harness
conventions". Harness lives in `tests/clock.py`, `tests/fakes.py`,
`tests/conftest.py`; self-tests in `tests/test_harness.py` (22 tests). Suite
green (24 total), ruff clean.

### Interface gaps flagged to core-engineer (for T-006/T-005)

1. **`TurnTakingGate` input API is unspecified.** The module map freezes the
   three *output* predicates (`settled` / `politeness_gap_elapsed` /
   `speech_resumed`) but not how VAD/silence/speech events are *fed in*, nor
   the exact clock-injection signature (`now=` callable vs `Clock` object — both
   are mentioned, neither pinned). The harness clock supports both forms, so
   either works; core-engineer should pin one in T-006 so the gate tests target
   a stable input surface.
2. **`WallVerdict` not yet frozen.** Harness uses `WallVerdictLike` (TODO marker
   in `tests/fakes.py`). Field names match the documented shape; swap is
   import-only once T-005 freezes it *with* local-ml-engineer.

### Next for qa-tuning

- **T-010** — interjection-precision eval spec (fixture format + precision
  computation). Stub section already in `eval-plan.md`. Depends on T-007.
- Mandatory review of T-005 (WallDetector), T-006 (TurnTakingGate),
  T-007 (SummonController) before merge.
