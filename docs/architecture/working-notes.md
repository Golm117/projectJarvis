# Working notes — architecture

_(scratchpad for in-flight thinking; promote durable findings to topic files)_

## In flight — T-002 prep (after T-001 scaffold)

Module map is written (`module-map.md`). The package layout there under
"Planned package layout" is a **proposal**, not built. When T-002 lands, the
things to nail down:

- **Freeze `Utterance` first.** Its shape (`speaker`, `text`, `ts`) is depended on
  by the whole window AND by sensing-engineer's MicSource. Make it frozen.
- **Inject the clock into `RollingWindow`** for the `max_seconds` time-bound — do
  NOT call `time.monotonic()` inside. The prototype does call it inside (line
  ~70/161); that's the deliberate divergence when porting. qa-tuning's harness
  needs the injected clock.
- Open question to settle with qa-tuning (T-009): clock shape — a bare
  `now: Callable[[], float]` vs a small `Clock` protocol with `.now()`. Lean
  toward the callable for simplicity unless their harness wants the object.
- `WallVerdict` schema must be frozen *with* local-ml-engineer before T-005 — the
  `confidence` field is what `WALL_CONFIDENCE_TO_SPEAK` reads.

