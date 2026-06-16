# Capture-and-label tooling + the precision eval runner (T-502)

> **Owner:** qa-tuning · **Domain:** `docs/qa/`
> **Status:** landed with T-502. Implements the capture side of the
> interjection-precision eval spec (`eval-plan.md` §T-010): turns a real
> conversation into the labeled fixtures the eval runner scores and T-503 sweeps
> thresholds against.

This is the bridge from a live run to a precision number:

```
  live run ──capture──►  raw fixture  ──label──►  labeled fixture  ──runner──►  precision
 (--capture PATH)      (UNLABELED candidates)   (USEFUL/FALSE set)    (useful ÷ total fires)
```

Everything here is in `src/jarvis/eval/`:

| Module | Role |
|---|---|
| `fixture.py` | The fixture **schema** (`Fixture` / `Moment` / `Candidate` / `Config`) + JSON (de)serialization + `validate()`. The one shape capture writes and the runner reads. |
| `capture.py` | The **capture** mechanism — a `CaptureRecorder` that observes a live `run_live` session and emits a raw fixture (labels pending). |
| `label.py` | The **labeling** workflow — functions + a tiny CLI to fill the ground-truth labels. |
| `runner.py` | The deterministic **precision** computation over labeled fixtures. |
| `seed.py` | The hand-authored **seed corpus** (real-session fixtures + the five eval-plan behavior illustrations). |

The committed corpus lives in `docs/qa/fixtures/*.json` (regenerate with
`uv run python -m jarvis.eval.seed docs/qa/fixtures`).

---

## 1. Capture — recording a live session

```
python -m jarvis --live --capture session.json
python -m jarvis --live --local-brain --capture session.json   # real Qwen brain
```

`--capture PATH` is **off by default**. When given, a `CaptureRecorder` observes
the run and writes a fixture to `PATH` on exit. It **only observes** — it never
changes a single decision the pipeline makes. It hooks three already-public seams
(no reach into any core module's internals):

1. **The gate edges** — a recording `TurnTakingGate` subclass records every
   `on_speech_start()` / `on_speech_end()` with the clock time → the timeline's
   `speech_start` / `speech_end` moments (the timing the metric needs to place a
   resume relative to the politeness gap).
2. **The wall backend** — a pass-through wrapper records every `WallVerdict` the
   detector returned. **Every wall verdict becomes a Path-B candidate — including
   the ones `SummonController` dropped** (below floor / no gap / resumed /
   backed-off). This is the key property: `on_interjection` alone only reveals
   *fires*; the dropped candidates are exactly what a precision sweep needs.
3. **The event callbacks** — `on_utterance` (→ utterance moments),
   `on_interjection` (marks the matching candidate "fired"), `on_engagement` (a
   summon → recorded as Path-A, **excluded** from precision).

A raw capture has every candidate's `label` set to `unlabeled` — the runner
refuses to score until they're filled (see §2).

### Privacy / ephemerality / retention (the hard-no)

Grounded in PRD 01 NFR-1.* and PRD 02 §Privacy (the "no ambient audio/transcript
to cloud; ephemeral by default" hard-nos):

- **Opt-in.** Nothing is captured unless `--capture PATH` is explicitly passed.
  The default `--live` path records nothing.
- **Transcripts + events, not audio.** A fixture holds transcribed text + timing
  + wall verdicts. **No raw audio is ever written.** (Audio capture, if ever
  wanted, must be a separate explicit local-only opt-in — out of scope here.)
- **Local-only, nothing auto-persists.** The fixture is written to the local path
  you named and **nowhere else**. Nothing is uploaded — capture only *observes*
  the in-process pipeline; it opens no socket. There is no background retention,
  no default file, no rolling log.
- **You own the file.** Retention is entirely manual: the file exists because you
  asked for it at a path you chose; you delete it when you're done. The rolling
  window itself remains ephemeral (T-002) — capture does not change that; it
  snapshots only what the pipeline already surfaced as events during the run.

---

## 2. Labeling — filling the ground truth

A raw capture's candidates are `unlabeled`. A human judges each: **was this a
real wall, and is an interjection *here* useful or false?** Two equally valid
ways:

### a) Edit the JSON directly (primary path)

The captured file is pretty JSON. Each `candidates[]` entry carries the observed
facts so you can judge it cold:

```jsonc
{
  "candidate_id": "c1",
  "match_from": 2.4, "match_to": 8.0,   // the window a fire must fall in to match
  "wall": true, "category": "factual_gap",
  "label": "unlabeled",                  // ← set to "useful" or "false"
  "rationale": "",                        // ← optionally say why
  "observed_confidence": 0.95,            // what the detector returned
  "observed_offer": "Could you remind me of the conference date?",
  "observed_category": null,              // set if the detector named the WRONG category
  "observed_fired": true,                 // what the live controller did (audit only)
  "observed_drop_reason": ""
}
```

Set `label` to `"useful"` (a fire here is correct) or `"false"` (a fire here is a
precision error). Optionally correct `category` (if the detector mis-named the
wall), tighten `match_from`/`match_to`, or set `observed_category` to model a
right-moment / wrong-category fire.

### b) The tiny CLI (guided, no hand-editing)

```
python -m jarvis.eval.label show     session.json
python -m jarvis.eval.label set      session.json c2 useful --rationale "clean opening"
python -m jarvis.eval.label set      session.json c3 false  --category stuck_point
python -m jarvis.eval.label validate session.json     # OK only when sound + fully labeled
```

`show` prints every candidate + its observed facts + current label; `set` writes
one label (and optional rationale / category / window) back to the file;
`validate` confirms the fixture is structurally sound and has no `unlabeled`
candidates left (ready for the runner).

---

## 3. Scoring — the precision computation

```python
from jarvis.eval.fixture import load_fixture
from jarvis.eval.runner import run_fixtures

result = run_fixtures([load_fixture(p) for p in fixture_paths])
print(result.precision)                 # useful ÷ total Path-B fires, or None if no fire
print(result.precision_by_category())   # which wall types over-fire
```

The runner is deterministic and fully offline (no audio/model/network — the same
posture as the unit tests). Per `eval-plan.md` §"How it runs", it replays each
fixture on a `SimulatedClock` through the **real** `TurnTakingGate` +
`SummonController`, feeding verdicts built from the labels (not a model), collects
every Path-B `INTERJECTION` fire, matches each to a candidate by time window, and
scores:

- **useful** iff the matched candidate is `useful` **and** the fired category
  equals the candidate's `category` (right wall, right moment);
- **false** otherwise (a `false`-labeled candidate, an unmatched fire, or a
  right-moment / wrong-category fire);
- a `useful` candidate the controller stayed silent on is a **miss** (a recall
  datum — recorded, never in the precision ratio).

`precision = useful ÷ total fires`; `None` (undefined) if nothing fired. Path-A
summons are excluded entirely.

It refuses to score a fixture that still has `unlabeled` candidates — an
un-reviewed capture can never silently produce a precision number.

---

## 4. The seeded corpus (the T-503 yardstick)

`docs/qa/fixtures/` holds the starter corpus, scoring as follows on the shipped
defaults (politeness gap 2.0 s, floor 0.70):

| Fixture | Behavior | Fires | Useful | Notes |
|---|---|---:|---:|---|
| `seed-useful-factual-gap` | live TP: "What was the date of the conference again?" | 1 | 1 | clean opening |
| `seed-false-what-do-you-need` | live FP: "What do you need?" @ 0.95 in a summon exchange | 0 | — | **labeled FALSE; T-503 cooldown suppresses it** |
| `seed-summon-excluded` | Path-A summon | 0 | — | excluded from precision |
| `ff-useful-unanswered-question` | clean useful fire | 1 | 1 | |
| `ff-false-thinking-pause` | resumes before the gap | 0 | — | abort removes a would-be FP |
| `ff-false-wrong-category` | real `stuck_point`, fires `factual_gap` | 1 | 0 | wrong-category → false (not a tunable FP) |
| `ff-backoff-no-nag` | same wall twice | 1 | 1 | repeat suppressed (c2 a miss) |
| `ff-below-floor` | wall at 0.55 < floor | 0 | — | a miss; lower the floor to make it fire |
| `ff-false-stale-pending-wall` | wall cached, opening only after 12 s TTL | 0 | — | **T-503 TTL drops the stale wall** |

**Aggregate after T-503: 4 fires, 3 useful → precision 0.75** (up from the pre-tune
0.60). The post-engagement cooldown suppresses the "What do you need?" FP and the
pending-wall TTL drops the stale-wall FP; the one remaining false fire is the
wrong-category case (a detector mis-naming no orchestrator/threshold lever can fix
— 0.75 is the achievable ceiling on this set). See `docs/qa/threshold-tuning.md`
for the full sweep + the suppression/TTL design.

> **Pre-T-503 baseline (for the record): 5 fires, 3 useful → 0.60.** The
> `seed-false-what-do-you-need` timeline gained an `engagement` moment (schema v2)
> at T-503 so the cooldown can model the just-engaged context, and the
> `ff-false-stale-pending-wall` fixture was added for the TTL.

### qa verdict on "What do you need?" → **FALSE**

The T-502 brief asked for my call on this borderline case. I label it **false**,
for two independent reasons (either sufficient):

1. **Conversational role.** It surfaced inside a summon exchange — the user had
   just engaged Jarvis, and "What do you need?" is a turn *addressed to Jarvis*,
   not an unanswered wall hanging between humans. Offering to "look that up" is
   Jarvis answering its own near-rhetorical question — noise, not help. A
   well-timed interjection requires an *unanswered gap among the speakers*; this
   is the opposite.
2. **Precision cost.** The success metric is precision-first: a false
   interjection is the costly error, a miss is cheap. When a candidate is this
   borderline, the correct label for the *yardstick* is FALSE, so the sweep is
   pushed toward suppressing it.

**Note for T-503:** both this FP and the true positive are `factual_gap @ 0.95`
(the Qwen near-binary-confidence problem, NOTES T-203). So the confidence floor
**cannot** separate them — lowering or raising 0.70 moves both together. The real
lever is context: does the wall sit inside a *just-engaged* exchange? That is a
detector/orchestrator signal, not a threshold. Recorded so the sweep starts
honest rather than chasing a floor that can't win.

---

## 5. What T-503 tunes against

The runner is the harness T-503 sweeps. It holds the labeled fixtures fixed,
overrides each fixture's `config` block (the three knobs:
`politeness_gap_seconds`, `interjection_confidence_floor`, `settle_seconds`),
re-scores precision per setting, and picks the operating point clearing the
≥ 70 %-useful target with false interjections rare. Because all three thresholds
are constructor-injected (verified in the T-006/T-007 reviews), the sweep changes
only the injected `Config` — no code edit. `test_config_sweep_changes_the_outcome`
pins this lever (lowering the floor turns the below-floor miss into a fire).

**Carry-forward staleness fixture (from the T-302/T-303 review, NOTES) — DONE in
T-503.** Added `ff-false-stale-pending-wall` (a wall cached at t=0, opening only at
t=15) + a configurable `_pending_wall` TTL on `AttentionLayer` (default 12 s) that
drops a cached wall once it has waited past the TTL. The eval models it via the
candidate's `wall_detected_at` anchor + the `config.pending_wall_ttl_seconds`
knob. Full design + the sweep in `docs/qa/threshold-tuning.md`.
