# Agent roster — intros and handoff-mesh audit

Each agent in this project wrote their own intro in two phases: Sections 1–3 in Phase A (who they are, what they do well, what they don't do), Sections 4–6 in Phase B (handoffs, prompt templates, surprising things). Full intros are linked below; this README is the index plus the handoff-mesh audit.

The `.claude/agents/*.md` files are the short system prompts that define each agent's voice and scope. These `docs/agents/*.md` files are the agent-written expansions.

## Index

| Agent | Intro | One-line role |
|---|---|---|
| core-engineer | [core-engineer.md](./core-engineer.md) | Owns the pure-logic attention core — the path from an Utterance hitting the RollingWindow to an EngagementHandoff leaving the boundary, and the decision of when the machine may speak. |
| sensing-engineer | [sensing-engineer.md](./sensing-engineer.md) | Owns Jarvis's ears — the on-device path from microphone through Silero VAD and ASR to the first clean Utterance; nothing leaves the M5 during listening. |
| local-ml-engineer | [local-ml-engineer.md](./local-ml-engineer.md) | Runs Qwen2.5 on MLX for the two on-device reasoning pieces — the delta-updated living-summary and the structured wall verdicts. |
| voice-integration-engineer | [voice-integration-engineer.md](./voice-integration-engineer.md) | Owns the engaged path — turns an EngagementHandoff into a spoken-style Claude answer streamed through ElevenLabs; the one cloud lane, open only at the moment of answering. |
| qa-tuning | [qa-tuning.md](./qa-tuning.md) | Owns the success metric (interjection precision) — the test harness, the eval, threshold calibration, and mandatory review of all summon/timing/wall behavior. |

## Handoff-mesh audit

Cross-checked every claimed handoff across the roster. Handoffs to the reserved target `human` are skipped (no intro to reciprocate; trivially valid).

### Tight mesh

- **core-engineer → qa-tuning** (mandatory review of TurnTakingGate / SummonController / WallDetector / thresholds). qa-tuning's Section 4 inbound names core-engineer as a mandatory router on exactly these. Reciprocated.
- **core-engineer ↔ voice-integration-engineer** (the EngagementHandoff seam: `trigger_reason` + living-summary text + recent_excerpt). Both agents describe the same two-way boundary — core owns the dataclass, voice-integration owns whether it carries enough to speak. Reciprocated.
- **core-engineer ↔ local-ml-engineer** (core freezes the LivingSummary/WallDetector interfaces + mock; local-ml implements the real backend behind them). Both name the seam and the shared verdict schema. Reciprocated.
- **sensing-engineer → core-engineer** (Utterance stream + VAD boundary timing via the TranscriptSource seam). core-engineer's Section 3 confirms consuming exactly this. Reciprocated.
- **sensing-engineer ↔ local-ml-engineer** (joint Phase-1/2 M5 runtime spike; ASR budget next to Qwen2.5 size under sustained load). Both name the joint spike and the shared budget. Reciprocated.
- **local-ml-engineer ↔ qa-tuning** (local-ml routes wall-detection behavior changes for precision review; qa-tuning routes precision shortfalls back with a labeled fixture slice). Both describe the bidirectional loop. Reciprocated.
- **qa-tuning → core-engineer** (behavior bug returned as a failing test + minimal scripted fixture). core-engineer owns the fix. Reciprocated.

### Mismatches flagged

1. **sensing-engineer → qa-tuning timing heads-up — not explicitly reciprocated.** Severity: **DEFERRED.** sensing-engineer claims a heads-up to qa-tuning when VAD segmentation timing changes (their abort-on-resume / politeness-gap tests are calibrated against that timing). qa-tuning's inbound list names core-engineer and local-ml-engineer but not sensing-engineer. This is an informational flag, not a merge gate, and qa-tuning broadly accepts any precision-affecting input — it will surface naturally the first time segmentation timing moves. Not worth pre-litigating.
2. **voice-integration-engineer → qa-tuning latency/style flag — not explicitly reciprocated.** Severity: **DEFERRED.** voice-integration claims a flag to qa-tuning when first-audio latency or response phrasing interacts with interjection precision (e.g. an interjection that lands late enough to feel wrong). qa-tuning doesn't name voice-integration as an inbound router. Low-frequency edge interaction; qa-tuning accepts precision-affecting input generally. Will surface on first occurrence.

No PROACTIVE or CRITICAL mismatches. No agent claims a handoff that a receiver's Section 3 forbids. No severity-scale divergence across the roster.

### Specialization quality

All five intros clear the specificity bar — every "what I do well" section names real modules, entities, thresholds, files, or task IDs from this project (RollingWindow eviction bounds, the Jaccard delta-update, `WALL_CONFIDENCE_TO_SPEAK`, the asymmetric dual-path SummonController, the mlx-whisper/whisper.cpp M5 spike, the Qwen2.5/MLX backends behind the frozen interfaces, the EngagementHandoff fields, the interjection-precision eval). No generic bullets; none would transfer unchanged to another project. A recurring, healthy theme across Section 6s: every agent holds a "won't ship on a vibe" working principle (core: testability as a hard interface constraint; sensing: no ASR runtime without a measured sustained-load spike; local-ml: no model swap without re-running core tests; voice: no wiki-readout drift, no cloud during listening; qa: no merge without an interjection-precision impact statement). No re-dos recommended.

## How to use this index

- **Before delegating work**, skim the relevant intro's Section 5 ("How to ask me for work well"). The good-prompt examples are templates.
- **When a handoff feels wrong**, check both agents' Section 4. If they describe the handoff differently, that discrepancy is the friction — sharpen it in `.claude/agents/` or the task note, not ad-hoc.
- **When adding a new agent** (or redefining one), re-run Phase A + Phase B + this audit for the new agent and any closely paired neighbor. Intros decay as the project evolves.
