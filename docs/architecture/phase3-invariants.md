# Phase 3 — VAD↔Gate Invariants and T-302 Integration Seam

> **Owner:** core-engineer
> **Produced by:** T-301 (verify-only; no logic changes)
> **Date:** 2026-06-15
> **Status:** findings locked; T-302 picks up the integration-seam description below.

---

## 1. One-clock invariant — VERDICT: HOLDS

**Invariant:** In `run_live`, the `TurnTakingGate`, the `RollingWindow`, and
`MicSource`'s `Utterance.ts` all derive from the **same** injected `now`
callable (`time.monotonic`). No module reads `time.monotonic()` on its own.

### Trace through `live.py`

```python
# live.py  run_live()
now = time.monotonic                         # (1) one callable object
gate = TurnTakingGate(now)                   # (2) gate._now = now
layer = AttentionLayer.build(gate=gate,
                             now=now, ...)   # (3) window._now = now  (AttentionLayer.build line 250)
mic_source = MicSource(source=mic,
                       gate=gate,
                       now=now)              # (4) mic._now = now → ts = now() at segment close
```

**Step by step:**

1. `now = time.monotonic` (line 155) — a single function-object reference.
2. `TurnTakingGate(now)` (line 156) — the gate stores `self._now = now`. Every
   call to `on_speech_start()`, `on_speech_end()`, `settled()`,
   `politeness_gap_elapsed()`, and `speech_resumed()` reads from this `now`.
   The gate does **not** call `time.monotonic()` directly anywhere.
3. `AttentionLayer.build(gate=gate, now=now, ...)` (lines 187-197) — the build
   classmethod constructs `RollingWindow(max_utterances, max_seconds, now)` at
   line 250 of `attention_layer.py`. The window stores `self._now = now` and
   uses it for both `add`-time and read-time eviction. No hidden clock.
4. `MicSource(source=mic, gate=gate, now=now)` (line 217) — `MicSource` stores
   `self._now = now`. In `_close_segment()` it stamps
   `ts = self._now() if self._now is not None else frame_derived_ts`, so in the
   live path `Utterance.ts = now()` at the moment the speech segment ends.

**All three are the same `time.monotonic` object. The invariant holds without
exception.**

### Why it was not obvious (the T-105 bug)

The default `MicSource` behaviour (`now=None`) stamps `Utterance.ts` from the
VAD frame timeline: `frames_seen × frame_samples / sample_rate`. For a typical
10-second utterance that is roughly `~0.5 s`. But `time.monotonic` returns
roughly `400,000 s` (seconds since boot on an M5). The `RollingWindow`'s time-
eviction cutoff is `now() - max_seconds ≈ 400000 - 120 = 399880 s`. An
utterance with `ts ≈ 0.5 s` is `399880 s` in the past — immediately evicted.
This is exactly the T-105 integration bug: every live utterance evicted
instantly, Path B never saw any wall line.

The fix (T-105 / DECISIONS.md 2026-06-15 "Live Utterance.ts must share the
orchestrator's clock") injects `now` into `MicSource` from `run_live`. The unit-
test default (`now=None`, frame-derived ts) is intentionally left unchanged so
T-104's unit tests still assert the frame-derived contract — only the live wiring
in `run_live` injects the real clock.

### Pinning tests

`tests/test_one_clock_invariant.py` (T-301, 6 tests, 0 mocks touching gate/summon/wall logic):

| Test | What it pins |
|---|---|
| `test_shared_now_stamps_utterance_ts_from_that_clock` | `ts = now()` when `now` injected (not frame-derived) |
| `test_window_does_not_evict_live_utterance_when_clock_is_shared` | retention positive: shared clock → utterance kept |
| `test_frame_derived_ts_would_cause_eviction_with_large_clock_offset` | retention negative control: frame ts + large offset clock → instant eviction |
| `test_gate_window_micsource_all_use_the_same_now` | object-identity check: all three modules got the same `now` reference |
| `test_gate_and_window_built_by_attention_layer_build_share_the_same_now` | `AttentionLayer.build` wires the same `now` into `RollingWindow` and the given `gate` into `SummonController` |
| `test_path_b_not_re_evaluated_during_silence_between_utterances` | documents the T-302 gap (see §2) |

---

## 2. Blocking-generator silence gap — CONFIRMED as the T-302 integration point

### What the gap is

`MicSource.utterances()` is a Python generator. Its main loop is:

```python
for frame in self._source.frames():      # blocks here while source yields nothing
    ...
    if self._in_segment:
        self._segment_frames.append(frame)
    utt = self._take_pending_utterance()
    if utt is not None:
        yield utt
```

During silence the `AudioSource.frames()` iterator simply yields silence frames
(or, in a real mic loop, blocks on `ring_buffer.pop()`). The generator is
**inside** that `for` loop. It does not yield between frames — it only yields
an `Utterance` when a speech segment ends.

`AttentionLayer.run()` is:

```python
def run(self, source: TranscriptSource) -> None:
    for u in source.utterances():
        self.ingest(u)
```

So `ingest` — and therefore `SummonController.consider_interjection` and its
read of `gate.politeness_gap_elapsed()` — is called **exactly once per
utterance**, at the moment the utterance is yielded. At that instant, the VAD's
endpoint hangover (~200 ms, `silence_end_frames × 32 ms`) has just elapsed, but
the gate's `politeness_gap_seconds` (~2 s) has not. So:

- **At ingest time:** `gate.politeness_gap_elapsed()` is `False`. `consider_interjection` returns `None`.
- **During the subsequent silence:** the gate's `politeness_gap_elapsed()` becomes `True`, but `ingest` is never called — the generator is blocked inside `source.frames()` waiting for the next speech frame.
- **At the next utterance:** `on_speech_start()` fires (re-arming the gate), then `on_speech_end()` (resetting the silence clock). `politeness_gap_elapsed()` is `False` again.

**The window during which `politeness_gap_elapsed()` is `True` and a Path-B fire would be correct is entirely missed.**

This is confirmed by `test_path_b_not_re_evaluated_during_silence_between_utterances` (T-301 pinning test).

### What the v0 smoke-test affordance did instead

`run_live` has a "trailing re-check" (lines 238-257 of `live.py`) that is
explicitly labelled a smoke-test affordance:

1. It stops capturing on the wall-bearing utterance (`stop_after_text` match).
2. It calls `time.sleep(POLITENESS_GAP_SETTLE + 0.3)` to let real wall-clock time elapse.
3. It re-ingests the last utterance once the gate reports `politeness_gap_elapsed()`.

This is **not** the real continuous re-evaluation loop — it is a single re-ingest
after a real sleep, used in T-105 to demonstrate that the detection + gate timing
path is correct on live audio. The comment in `live.py` says this explicitly:
"Re-evaluating Path B continuously as silence accumulates is the Phase-3 real-time
SummonController, T-302."

---

## 3. T-302 integration seam — cleanest pure hook

### What T-302 needs to add

A caller that can **re-evaluate Path B periodically during silence**, i.e. call
`controller.consider_interjection(last_wall_verdict)` while the
`MicSource.utterances()` generator is blocked, without touching the `utterances()`
generator or the gate.

### Recommended approach: `tick()` on `AttentionLayer` + a background timer in `live.py`

**What to add to `AttentionLayer` (the pure hook):**

```python
def tick(self) -> None:
    """Re-evaluate Path B with the last cached wall verdict (called during silence).

    The MicSource generator blocks during silence so ingest() never runs.
    A caller (a background thread or timer in live.py) calls tick() periodically
    to give consider_interjection() a chance to fire once the politeness gap opens.
    tick() is a no-op if there is no pending wall verdict or if the gate
    predicates haven't changed since the last call.
    """
    if self._pending_wall is None:
        return
    decision = self._controller.consider_interjection(self._pending_wall)
    if decision is not None:
        self._pending_wall = None          # consumed
        self._interject(decision)
```

The orchestrator would also need to cache the last wall verdict at `ingest` time
(when Path B conditions were not yet met) so `tick()` can re-evaluate it:

```python
# in ingest(), after consider_interjection returns None:
self._pending_wall = verdict   # cache for tick()
# clear on engagement:
# in _engage(): self._pending_wall = None
```

**Key properties of this design:**

1. **Pure reads only.** `tick()` reads the injected clock via the gate
   predicates — `gate.politeness_gap_elapsed()` and `gate.speech_resumed()` —
   exactly as `consider_interjection` does. No new clock ownership, no new
   `time.monotonic()`.
2. **Threading isolated to `live.py`.** The timer that calls `tick()` is
   `live.py`'s concern (e.g. a `threading.Timer` or a background thread). The
   `AttentionLayer` and `SummonController` stay single-threaded pure logic; the
   timer simply calls `layer.tick()` from a background thread. A lock on
   `_pending_wall` access is the only threading concern and lives in `live.py`
   or as a single `threading.Lock` on the layer.
3. **No change to `TurnTakingGate`, `SummonController`, or `WallDetector`.** Those
   modules are qa-gated. `tick()` calls the same public `consider_interjection`
   method the existing code already calls.
4. **The pending-wall pattern is correct for abort-on-resume.** If speech resumes
   during the tick loop, `gate.speech_resumed()` is `True` and
   `consider_interjection` returns `None` — the abort-on-resume hard-no is
   preserved without any new logic.

### Non-deterministic back-off finding (from NOTES.md) — T-302 must account for it

The `SummonController` back-off key is `f"{category.value}::{offer}"` (i.e.
`category + offer`). When the `QwenWallBackend` is used, the `offer` text is
**non-deterministic**: the same wall situation can produce different phrasing each
time the model is called. So if `tick()` calls `consider_interjection` on a
**freshly-fetched** verdict (by re-running wall detection), the offer text will
likely differ from the previous call, the back-off signature will not match, and
a duplicate offer will fire.

Options for T-302 to choose from (all require qa-tuning review since they touch
`SummonController` back-off policy):

- **Cache the verdict at ingest and re-use it** — `tick()` re-evaluates the
  *same* `WallVerdict` object, so the offer text is stable across ticks. No model
  re-call during the gap. This is the simplest approach and is what the
  recommended `tick()` design above does (`self._pending_wall` caches the verdict
  from `ingest` time).
- **Key back-off on `category` alone** — change `_signature` in `SummonController`
  to use only `verdict.category.value`. This is a qa-gated change to
  `SummonController`; requires a review brief.
- **Generate the offer text once at detection time and freeze it** — the
  `QwenWallBackend` generates the offer once; `tick()` re-uses it. Equivalent to
  the cached-verdict approach but documented differently.

The cached-verdict approach (option 1) avoids any qa-gated change and is
recommended for T-302.

---

## 4. No defects found in qa-gated modules

The trace found no bugs in `TurnTakingGate`, `SummonController`, or `WallDetector`.
The one-clock invariant holds in the live wiring. The silence-gap is a correct and
expected consequence of the generator model; it is not a bug, it is the
acknowledged T-302 integration point. No defect to report to the orchestrator.
