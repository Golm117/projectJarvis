# Interjection-precision tuning (T-503)

> **Owner:** qa-tuning · **Domain:** `docs/qa/`
> **Status:** landed with T-503 (the success-metric task). The durable record of
> what was tuned, the eval evidence behind each decision, and what was deliberately
> *not* changed. Grounded in `.pdr.md` (§Success metric) and the T-502 eval
> (`docs/qa/capture-and-label.md`).

The success metric is **interjection precision = useful ÷ total Path-B fires**.
T-503 raised it on the seeded corpus from **0.60 → 0.75** by killing the two
*tunable* false positives, without dropping a single legitimate fire.

```
                                fires  useful  false  precision
pre-T-503 (both rules off)        6      3       3       0.50*   (* new corpus incl. stale fixture)
+ post-engagement cooldown        5      3       2       0.60
+ pending-wall TTL                4      3       1       0.75
```

The one remaining false fire (`ff-false-wrong-category`) is a *detector mis-naming*
(it fires `factual_gap` on a real `stuck_point`) — no orchestrator or threshold
lever can fix it, so **0.75 is the achievable ceiling on this set**, and the tune
reaches it.

---

## 1. The headline finding: the lever is context, not a threshold

The T-502 eval flagged that the live FP ("What do you need?") and the live TP
("What was the date of the conference again?") are **both `factual_gap @ 0.95`**
(the Qwen near-binary-confidence problem, NOTES T-203). The threshold sweep
confirmed this decisively — **no confidence floor can separate them**:

| floor | fires | useful | precision |
|---:|---:|---:|---:|
| 0.50 | 6 | 4 | 0.667 |
| 0.60–0.80 | 5 | 3 | 0.60 |
| 0.90 | 4 | 2 | 0.50 |
| 0.95 | 3 | 1 | 0.333 |
| ≥ 0.96 | 0 | — | undefined |

The FP and TP move **together** at every floor: raising the floor kills the TP
before it isolates the FP; lowering it just admits a below-floor wall (more recall,
not better precision). The floor is **inert** for this corpus.

So the FP fix had to be **context**: *does the wall sit inside an exchange the
user is having with Jarvis?* That is the post-engagement cooldown (§3).

### Politeness gap + settle sweeps — no change warranted

| gap (s) | precision | | settle (s) | precision |
|---:|---:|---|---:|---:|
| 0.6–1.0 | 0.50 (admits the thinking-pause FP) | | 0.0–2.0 | 0.60 (flat — Path A only) |
| 1.5–3.0 | 0.60 (plateau) | | | |
| 4.0 | 0.50 (starts killing useful fires) | | | |

`politeness_gap_seconds = 2.0` already sits on the precision plateau, and
`settle_seconds` is Path-A only (it never touches Path-B precision). **No
threshold default changed.** Don't change a threshold just to change it — the
eval doesn't support moving any of the three.

---

## 2. What changed (and where)

All three knobs are constructor-injected so qa-tuning owns the value in one place,
and all are clock-driven (measured on the `SimulatedClock`, never a real sleep).

| Knob | Default | Lives in | Eval models via |
|---|---:|---|---|
| `post_engagement_cooldown_seconds` | **6.0** | `AttentionLayer` | `engagement` timeline moment + `config.post_engagement_cooldown_seconds` |
| `pending_wall_ttl_seconds` | **12.0** | `AttentionLayer` | candidate `wall_detected_at` anchor + `config.pending_wall_ttl_seconds` |
| `interjection_confidence_floor` | 0.70 (unchanged) | `SummonController` | `config.interjection_confidence_floor` |
| `politeness_gap_seconds` | 2.0 (unchanged) | `TurnTakingGate` | `config.politeness_gap_seconds` |
| `settle_seconds` | 0.6 (unchanged) | `TurnTakingGate` | `config.settle_seconds` |

**Placement rationale.** Both new rules live in the orchestrator
(`AttentionLayer`), not `SummonController`. The controller is a *pure decision
machine* over a single verdict + the gate's predicates — it has no notion of
"recent engagement" or "how long this wall has been cached" (that is conversation
state the orchestrator already owns: `_last_engagement_at`, `_pending_wall`). This
keeps the qa-gated `SummonController` byte-for-byte unchanged (verify with
`git diff` — it was not touched), exactly as the T-302 cached-verdict design did
for the back-off problem.

---

## 3. Post-engagement cooldown — the FP fix

**Rule.** After Jarvis engages on **either** path (a wake-word summon or a fired
interjection), ambient Path-B interjections are suppressed for
`post_engagement_cooldown_seconds`. While the user is in a dialogue *with* Jarvis,
a wall the detector spots is the user talking *to* Jarvis, not a wall hanging
*between humans*.

**Mechanics (`AttentionLayer`):**
- `_engage()` stamps `_last_engagement_at = now()` on any engagement (both paths).
- `ingest()` and `tick()` check `_in_post_engagement_cooldown()` before firing
  Path B. A suppressed wall at ingest is **not cached** (it does not arm `tick()`);
  in `tick()` the pending wall is **held, not dropped** (it can still fire once the
  cooldown passes, unless its TTL drops it first).

**Why 6.0 s (human sign-off 2026-06-16).** The seeded FP fires at **t = 5.5 s**
after the engagement (the "What do you need?" turn ends at t=3.5, + the 2.0 s
politeness gap). The cooldown must exceed 5.5 s; it flips clean at 6.0 s. The
orchestrator's empirical sweep confirmed 4/5/5.5 s leave the FP in (0.60) and
6/7/8 s suppress it (0.75) — every value ≥ 6.0 s gives the same 0.75, because the
cooldown only ever touches this one FP (no legitimate fire has a preceding
engagement). **6.0 s is the human-chosen value:** the most responsive setting that
works, with a 0.5 s margin over the 5.5 s fire. It was chosen over the earlier 8.0 s
default (a larger robustness margin) for responsiveness — precision is identical at
both. core-engineer's independent review blessed the 5–6 s range and confirmed the
value is one eval-testable constant.

| cooldown (s) | precision (ttl=12) |
|---:|---:|
| 0.0–5.5 | 0.60 (FP still fires) |
| **6.0** | **0.75** (FP suppressed — shipped value, human sign-off) |
| 6.0–10.0 | 0.75 (FP suppressed; 8.0 was the prior default) |

---

## 4. Pending-wall TTL — the staleness fix (carry-forward from T-302/T-303)

**Rule.** A wall cached by `tick()` during silence is dropped once it has waited
longer than `pending_wall_ttl_seconds` without firing — so a wall cached while the
conversation quietly moved on can't fire *late* as a stale false interjection.

**Mechanics (`AttentionLayer`):**
- `_cache_pending_wall()` stamps `_pending_wall_cached_at = now()`.
- `tick()` drops the pending wall when `now() - _pending_wall_cached_at >= ttl`
  (`_pending_wall_is_stale()`), before attempting to fire.
- The replace-with-fresher-wall and clear-on-engagement policies (T-302) still
  bound it; the TTL closes the one remaining gap (no fresh wall, no engagement,
  conversation drifts).

**Why 12.0 s.** A legitimate wall fires within the 2 s politeness gap of its
opening, so 12 s is far above any real fire latency — the TTL only ever catches a
wall that has genuinely gone stale. The staleness fixture
(`ff-false-stale-pending-wall`) caches a wall at t=0 whose only clean opening lands
at t=15; the valid TTL range that drops it while sparing real fires is ~(8, 15) s,
and 12 s sits in the middle.

| ttl (s) | precision (cooldown=6, shipped) |
|---:|---:|
| 0.0 | 0.60 (stale wall fires late) |
| 5.0–16.0 | **0.75** (stale wall dropped) |
| 20.0 | 0.60 (TTL now past the stale opening) |

**Eval modeling.** The runner ages the TTL from the candidate's `wall_detected_at`
(when the wall was cached), falling back to `match_from` when unset — the common
case, where a wall fires into the opening right where it surfaced, so the two
coincide. Only a staleness case sets `wall_detected_at` explicitly (the wall is
cached well before its late opening).

---

## 5. Carried-forward items — both DEFERRED, with evidence

### Declarative `factual_gap` recall → **defer to v1**

The WallBackend prompt misses declarative gaps ("I don't remember…", "I can't
recall…"); question-form gaps fire (T-203). Improving recall means **more fires**,
which on a precision-first metric can only **hold or lower** precision — and the
eval shows the corpus is **FP-limited, not recall-limited** (every below-target
point is a false fire, not a miss). A declarative-recall prompt change is also a
`local-ml-engineer`-lane change to the WallBackend, not a qa threshold. **Decision:
do not touch the prompt for v0.** A missed factual_gap costs *recall* (silence,
cheap), never *precision* (the metric). Revisit in v1 if a captured corpus shows
declarative gaps are a common, useful, well-timed wall — and re-measure precision
when adding them.

### `interjection_confidence_floor` recalibration → **keep 0.70**

The floor sweep (§1) shows it is **inert** for the Qwen backend (near-binary
confidence, ~0.95 on fires) — the binary `is_wall` is the real gate, and no floor
value separates the live TP from the live FP. Lowering it only admits below-floor
walls (recall, not precision); raising it kills the TP before the FP. **Decision:
keep 0.70.** It remains the right *shape* (a precision-over-recall guard a future,
better-calibrated backend can lean on); it is simply not the lever for *this*
corpus. The lever was context (the cooldown), and that is where the work went.

---

## 6. How to re-run

```bash
# Score the committed corpus (expect precision 0.75):
uv run python -c "from pathlib import Path; from jarvis.eval.fixture import load_fixture; \
from jarvis.eval.runner import run_fixtures; \
print(run_fixtures([load_fixture(p) for p in sorted(Path('docs/qa/fixtures').glob('*.json'))]).precision)"

# Regenerate the seeded fixtures after editing jarvis/eval/seed.py:
uv run python -m jarvis.eval.seed docs/qa/fixtures

# The T-503 behavior + value tests:
uv run pytest tests/test_t503_precision_tuning.py -q
```

To sweep a knob, override the fixture `config` block in-memory (no code edit) —
see `tests/test_t503_precision_tuning.py::test_pre_t503_baseline_was_below_target`
and `dataclasses.replace(fx.config, ...)`.
