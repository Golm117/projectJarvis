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

## 7. T-508 — graded confidence: floor RE-VALIDATION (qa-tuning review)

> **Status:** qa-tuning gate of T-508 (graded interjection-worthiness rework of
> `QwenWallBackend`). T-508 makes `WallVerdict.confidence` genuinely graded
> (rating 1→0.05 … 5→0.95; `is_wall = rating>=3`), so the 0.70 floor — **inert**
> under the old near-binary backend (§1) — can now do real work. This section
> records the re-validation and the floor **recommendation** for human sign-off.
> **The floor value is left at 0.70 in code** (a user-visible success-metric
> threshold — same sign-off pattern as the T-503 cooldown). Recommendation only.

### 7.1 Precision on the current fixtures is unchanged: **0.75**

The eval scores **labels + config**, building each verdict from the fixture's
`observed_confidence` — which was captured from the *old* near-binary backend
(~0.85–0.95). Re-running the committed corpus at the shipped config still gives
**precision 0.75** (4 fires, 3 useful; the lone false fire is
`ff-false-wrong-category`). T-508 changes *how the backend produces* confidence,
not the already-labeled fixture values, so the corpus number is stable — as
expected. The floor's *new* leverage only shows once fixtures carry graded
confidence (7.3).

### 7.2 Floor sweep on the **current** (old near-binary) fixtures — still inert

```
 floor | fires | useful | false | precision
  0.00 |     5 |      4 |     1 |   0.800
  0.55 |     5 |      4 |     1 |   0.800
  0.60 |     4 |      3 |     1 |   0.750
  0.70 |     4 |      3 |     1 |   0.750   ← shipped
  0.85 |     4 |      3 |     1 |   0.750
  0.90 |     3 |      2 |     1 |   0.667
  0.95 |     2 |      1 |     1 |   0.500
 ≥0.96 |     0 |      — |     — |     n/a
```

The FP (`ff-false-wrong-category @ 0.95`) and the strongest TPs (`@ 0.95`) still
move **together** at every floor — the §1 finding, unchanged, because the fixtures
still hold near-binary values.

### 7.3 Floor sweep on **modeled graded** confidence — the floor *can* now do work

Projecting each candidate's confidence to what the T-508 backend would emit
(strong group-directed question → rating 5/0.95; declarative borderline → rating
3/0.65; the post-summon FP → rating 1/0.05; the thinking-pause FP → rating 2/0.30;
the wrong-category FP modeled at the model's confidence in its *wrong* read):

```
 floor | fires | useful | false | precision | residual false fire
  0.00 |     5 |      4 |     1 |   0.800   | ff-false-wrong-category
  0.65 |     5 |      4 |     1 |   0.800   | ff-false-wrong-category
  0.70 |     4 |      3 |     1 |   0.750   | ff-false-wrong-category   ← shipped
  0.80 |     4 |      3 |     1 |   0.750   | ff-false-wrong-category
  0.85 |     3 |      3 |     0 |   1.000   | (none)
```

Two things this shows:
1. Graded confidence makes the floor a **real gate** between rating tiers — the
   §1 "inert" verdict is now *backend-version-specific*, and T-508 lifts it.
2. The precision=1.0 at floor ≥ 0.85 is a **modeling artifact, not a
   recommendation.** It only appears because I modeled the wrong-category FP's
   confidence at 0.80. That FP is a **detector-correctness** failure (a real
   `stuck_point` mis-named `factual_gap`), and the model's confidence in its
   *wrong* read is unknowable — it could just as easily be 0.95. **You cannot
   threshold your way out of a wrong-category fire**, and chasing a 0.85 floor to
   suppress this one fixture would (a) ride on an assumption about the model's
   self-confidence and (b) suppress every legitimate rating-4 fire (0.80) in real
   use — a large recall cost for one mislabeled corpus row.

### 7.4 The borderline question: should rating-3 (0.65) fire?

This is the substantive floor decision now that confidence is graded. The live
backend (8/8 repeated probes, 2026-06-16, this review) is **stable**:

| probe | live rating → confidence | at floor 0.70 |
|---|---|---|
| "What's the square root of 81?" (question) | 5 → **0.95** ×8/8 | **fires** |
| "I wonder what the square root of 81 is." (wh-form) | 3 → **0.65** ×8/8 | **suppressed** |
| "What's 4 times 7?" | 5 → **0.95** ×8/8 | fires |
| "What do you need?" (post-summon) | 1 → 0.05 | suppressed (✓) |
| "I wonder if my volume is too loud." | 1 → 0.05 | suppressed (✓) |
| "I don't remember the date we picked." | 3 → 0.65 | suppressed |

Rating 3 = 0.65 sits **just below** 0.70. The decision is whether the bar admits
rating-3 borderlines:

- **Keep 0.70 (precision-first) — RECOMMENDED.** Only rating-4/5 fire; rating-3
  ("I wonder what X is", declarative "I don't remember Y") stays silent. This is
  faithful to the success metric (a false fire is costly, a miss is cheap) and to
  the explicit exemplar design: rating 3 is the *borderline / weak-signal* tier the
  prompt itself flags with caution. The two suppressed cases above are genuinely
  marginal — a wh-form musing and a declarative gap with no explicit question.
  Suppressing them is the correct precision-first call.
- **Lower to ~0.65 (admit rating-3).** Would let the wh-form √81 and declarative
  gaps fire. This is a **recall** move on a **precision** metric, and the corpus
  is FP-limited not recall-limited (§5). It also narrows the floor's margin against
  any future rating-3-confident FP. Not recommended for v0.
- **Raise to ~0.75 (require rating-4+).** No effect vs 0.70 on the live tiers
  (nothing lands in (0.70, 0.80)), so it buys no precision while looking stricter.
  Cosmetic; not recommended.

**Recommendation: keep `interjection_confidence_floor = 0.70`** — but now for a
*sound* reason, not because it is inert. Under graded confidence it cleanly admits
rating-4/5 (0.80/0.95) and suppresses rating-1/2/3 (0.05/0.30/0.65), which is
exactly the precision-first boundary we want: only strong, group-directed,
clearly-answerable gaps fire. The √81 *question* form fires reliably (0.95×8/8);
the √81 *wh-form* (0.65) staying silent is acceptable precision-first behavior, not
a regression. **Human sign-off requested to confirm 0.70 (keep) vs lowering to
0.65** — the only judgement call is whether wh-form/declarative rating-3 gaps
should speak. I recommend keep.

### 7.5 `ff-false-wrong-category` still scores FALSE under graded confidence ✓

Verified in isolation: the fixture fires `factual_gap @ 0.95` against a ground-truth
`stuck_point` candidate; the runner scores by **category match** (independent of
confidence — `runner.py` `_score`), so the fired `factual_gap` ≠ ground-truth
`stuck_point` → **false**, unchanged. Graded confidence does not alter this; it
remains the correctly-scored, irreducible detector-mis-naming FP that caps the set
at 0.75. (Live note: the current 3B no longer reproduces this mis-fire on the
fixture's phrasing — it rates "going in circles, I'm stuck" a 2 → `none @ 0.30`,
i.e. it now *misses* rather than mis-names. The fixture is kept as a regression
guard for the historically-observed wrong-category fire, which is the correct eval
posture.)

### 7.6 √81 reliability — honest assessment (this review)

The brief reported a single non-deterministic run (√81 rated 3, wh-form rated 2).
**Repeated live probing (8×8, this review) found the opposite — the backend is
now stable**, not flaky: the √81 *question* form is 8/8 rating-5 (0.95) and fires;
the wh-form is 8/8 rating-3 (0.65). The earlier "non-determinism" looks like one
unlucky draw, not a persistent reliability gap. The pre-filter miss (the actual
T-508 root cause) is **definitively fixed** — the wh-form now reaches the model
every time.

**Is the residual wh-form 0.65 fixable in qa's lane?** The remaining gap is purely
that the 3B rates the *wh-form* √81 a 3 not a 4. Whether to lift that is a
WallBackend **prompt** change (more/stronger exemplars for wh-form gaps), which is
**local-ml-engineer's lane**, not a qa threshold — and it is recall work on a
precision-first metric, so it is **not** a v0 blocker. If a captured corpus later
shows wh-form gaps are a common, useful, well-timed wall, revisit with a prompt
change (and re-measure precision when adding them). **Flagged to the orchestrator
as a possible v1 lever; 7B escalation / fine-tuning is NOT warranted by this
evidence** — the model is consistent and the question form already fires reliably.

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

---

## 7. T-509 qa gate — 7B real-path validation + floor re-read (2026-06-16)

The T-509 gate validated the 7B switch on the **real `detect_wall(transcript,
summary)` path** with **multi-line rolling-window transcripts** (as production
feeds it), not the clean single-line probes that fooled the T-508 gate. Verdict:
**APPROVED** with one documented recall caveat.

### 7.1 Real-path `detect_wall` results (7B, multi-line, 4 runs each)

| Scenario | Transcript (multi-line) | Expected | 7B result |
|---|---|---|---|
| A — √81 question | 4-line geometry-homework window ending in "What's the square root of 81?" | fire | **0/4 fire** (rating 1 — *miss*, see §7.2) |
| B — 4×7 question | 4-line bill-split window ending in "What's 4 times 7?" | fire | **4/4 fire** (rating 5, `unanswered_question @ 0.95`) |
| C — conference date | 3-line travel window ending in "What was the date of the conference again?" | fire | **4/4 fire** (rating 5 @ 0.95) |
| D — WDYN post-summon | "[Jarvis engaged] … What do you need?" | no fire | **0/4 fire** (rating 1) ✓ |
| E — self-musing | "I wonder if my volume is too loud." | no fire | **0/4 fire** (rating 1) ✓ |
| F — plain statements | "Let's send the PR … grab lunch." | no fire | **0/4 fire** (rating 1) ✓ |

The T-508 prompt-framing regression (direct questions reasoned away as "not a
gap") **is fixed**: B and C — direct unanswered questions — fire reliably in
multi-line context. All three no-fire cases (D/E/F) correctly stay silent.

### 7.2 The Scenario-A miss — model confabulation, context-sensitive, recall-only

Scenario A (√81 in a *dense* 4-line homework window) misses deterministically
(5/5 runs, identical reasoning). Root cause, from the captured `reasoning` field:

> "Bob asked a direct arithmetic question, **but Alice answered it.** No gap."

Alice did **not** answer it — the 7B *confabulates a non-existent answer* to
justify silence, but only when prior lines give it enough conversational
scaffolding. Isolating the cause:

- √81 **single-line** → rating 5, fires.
- √81 **two-line** (one prior line) → rating 5, fires.
- √81 **dense 4-line** homework context → rating 1, confabulated-answered.
- 4×7 in the **same 4-line** density → rating 5, fires.

So it is **not** a phrasing regression and **not** a general "direct questions
miss" — it is a narrow, content-triggered confabulation on a trivially-knowable
fact inside a dense prior context.

**Crucially, it does NOT reproduce on the live production path.** Two live
`--capture` runs (real mic · Silero VAD · mlx-whisper · 7B brain · BlackHole
loopback) fired √81 correctly: `unanswered_question @ 0.95`, offer "That's 9."
The live rolling window at the moment of the question was 2 lines ("geometry
homework" + "√81?"), which matches the firing two-line probe — the live window
does not accumulate the dense 4-line scaffolding the synthetic probe used.

**Why it is not a blocker:** a miss is *silence on a real gap* → a **recall**
cost, never a **precision** cost. The success metric is precision (= useful ÷
total fires); precision-first is the DECISIONS.md-logged strategy. The miss
removes a would-be fire from the numerator AND denominator equally is false — it
removes nothing from the denominator (no fire happens); it is simply an
un-serviced gap the user can still summon for. Recorded as a recall datum, not a
precision regression. **Flagged to local-ml-engineer as a v1 prompt lever**
(an exemplar that says "do NOT assume a question was answered unless an answer
appears verbatim in the transcript"); not a v0 blocker.

### 7.3 Floor re-read — keep 0.70

The 7B confidence distribution on real inputs is still **near-binary** but not
purely so: observed ratings on real-path probes were 1 (→0.05), 4 (→0.80, e.g.
"I wish I knew how many hours it is"), and 5 (→0.95). No rating-3 (→0.65)
appeared in the probes, but the mapping places it just below the floor by design.
The 0.70 floor cleanly separates the tiers the 7B actually emits: 1 (suppressed),
4/5 (fire). It is **sound and does real work** between rating 3 (0.65, suppressed)
and rating 4 (0.80, fires). **Recommendation: keep 0.70** for the 7B backend — no
recalibration warranted by this evidence. (Not finalized here — qa-gated change
surfaced to orchestrator; the recommendation is keep.)

### 7.4 Scenario D (WDYN) — resolved as a non-issue for the production case

Two independent guards suppress the WDYN false positive:
1. **Detector level (7B):** in the multi-line `[Jarvis engaged]` framing the 7B
   rated WDYN rating 1 / none (4/4) — it does not even reach the controller.
2. **Orchestrator level (T-503 post-engagement cooldown, 6 s):** a WDYN-style
   wall landing within 6 s of a summon is suppressed *regardless* of the
   detector's read — the load-bearing guarantee, since it does not depend on the
   model's (non-deterministic) judgment.

Pinned deterministically in `tests/test_t503_precision_tuning.py`
(`test_scenario_d_wdyn_within_cooldown_is_suppressed`). WDYN is uttered seconds
after the summon (well inside 6 s), so the cooldown covers the production case.
**Residual** (documented, `test_scenario_d_residual_wall_after_cooldown_is_not_suppressed`):
a wall arriving *after* the 6 s cooldown is not suppressed by this mechanism — the
cooldown is time-bounded, not a permanent engaged-state suppression. A permanent
suppression would be a SummonController/orchestrator change (qa-gated) and is
**not** built here; flagged to core-engineer as the optional parallel
investigation the builder raised. Not a v0 blocker (no evidence WDYN recurs > 6 s
post-summon).

### 7.5 Precision unchanged at 0.75

The eval over the committed corpus scores **precision 0.75** (3 useful / 4 fires),
identical to the T-503/T-508 baseline — **not regressed**. The eval scores labels
+ config (the fixtures carry confidence values, not live 7B output), so the prompt
change does not move the committed number; the lone false fire remains the
deliberate `ff-false-wrong-category` test, and the WDYN seed is correctly not
fired.
