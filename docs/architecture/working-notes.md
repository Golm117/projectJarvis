# Working notes — architecture

_(scratchpad for in-flight thinking; promote durable findings to topic files)_

## Resolved — clock convention (T-002)

Clock shape is **settled**: `now: Callable[[], float]` is the single
clock-injection convention for every time-bounded module (pinned in
`module-map.md` §"Cross-cutting design constraints" #1). Not a `Clock` object.
The `SimulatedClock` is injected as `clock.now`.

## Done — T-002 (data types + RollingWindow)

`Utterance` is **frozen** (`jarvis/types.py`): `speaker`, `text`, `ts` — `ts`
required, no hidden default (producer stamps it). `RollingWindow`
(`jarvis/core/rolling_window.py`) is bounded by count + time, takes injected
`now`, and **ages on read** (re-evicts against current time even with no `add`)
— the deliberate divergence from the prototype's internal `time.monotonic()` and
newest-ts eviction. Shared `keywords()`/`jaccard()` ported to
`jarvis/core/text.py` (T-003 reuses them).

## Still pending for later tasks

- `WallVerdict` schema must be frozen *with* local-ml-engineer before T-005 — the
  `confidence` field is what `WALL_CONFIDENCE_TO_SPEAK` reads. (Harness uses
  `WallVerdictLike` until then.)
- **T-006 (TurnTakingGate) interface gap** still open: the gate's event-*input*
  API isn't pinned. The clock side is now settled (`now` callable); the
  event-input shape is T-006's to design with qa-tuning (mandatory reviewer).

