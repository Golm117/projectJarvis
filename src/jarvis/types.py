"""Core data types that cross the attention-layer seams.

These are the small, frozen value objects that travel between modules and across
the I/O seams documented in ``docs/architecture/module-map.md`` §"Data types".
They are deliberately dumb — no behavior, no I/O, no hidden clock — so every
module and every adapter (the mic source, the local-ml backend, the voice path)
agrees on one shape.

Frozen status (module map):

* ``Utterance`` — **frozen (T-002)**. Depended on project-wide: the whole
  ``RollingWindow`` reads it, and sensing-engineer's ``MicSource`` produces it.
  Its three fields (``speaker``, ``text``, ``ts``) are the contract.

``WallVerdict`` lands with T-005 (frozen *with* local-ml-engineer);
``Interjection`` and ``EngagementHandoff`` land with their tasks (T-007/T-008).
They are documented in the module map and added here as those tasks land so
each type freezes exactly when its first real consumer does.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Utterance:
    """A transcribed chunk of speech — the atom of the ambient pipeline.

    Frozen (T-002): immutable so a single ``Utterance`` can sit in the
    ``RollingWindow``, be rendered into a transcript, and cross to the engaged
    half without any stage mutating it.

    Fields:
        speaker: who spoke (e.g. ``"Alex"``; the ASR/diarization layer supplies
            this — for v0 a single label is fine).
        text: the transcribed words.
        ts: monotonic seconds, supplied by the *producer* (the injected clock or
            the VAD timeline) — **never** filled from a hidden ``time.monotonic()``
            here. Keeping ``ts`` an explicit, required field is what lets the
            ``RollingWindow`` evict by elapsed time deterministically under
            qa-tuning's ``SimulatedClock`` (module map §"Cross-cutting design
            constraints" #1).
    """

    speaker: str
    text: str
    ts: float
