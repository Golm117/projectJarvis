"""LivingSummary — the delta-updated running summary (T-004).

The ambient pipeline's "redraw only the changed pixels" rule made concrete. A
naive design would re-summarize the rolling window on every utterance; that burns
the on-device model (Phase 2's Qwen2.5/MLX) for no gain when the conversation
hasn't moved. ``LivingSummary`` instead keeps a standing summary and refreshes it
**only when the topic has actually shifted** away from what the standing summary
was built on — so the expensive ``summarize`` call fires on a delta, not a tick.

Three collaborators, all injected (none instantiated here — module map
§"Cross-cutting design constraints" #2):

* a :class:`~jarvis.core.topic_shift.TopicShiftDetector` — the pure shift metric
  (T-003). This module owns *what* to compare (the live window's keywords vs. the
  basis the summary was built on) and *what to do* on a shift; the detector owns
  the metric and threshold.
* a ``SummarizerBackend`` — the swappable model seam. The ``FakeSummarizer`` in
  tests; the real Qwen2.5/MLX backend lands in T-202 behind this same signature.

This module also owns the two *policy* fences that the module map deliberately
kept out of ``TopicShiftDetector`` (it's the pure metric, not the policy):

* **The cold-start minimum** (``MIN_UTTERANCES_FOR_SUMMARY``) — don't summarize a
  conversation that's barely started; there's nothing worth a paragraph yet.
* **The ≥2-utterances-since-update debounce** — after a refresh, require at least
  two new utterances before another shift can trigger again, so a single noisy
  line can't ping-pong the summary.

Ported from ``prototypes/attention-layer/attention_layer.py`` (the prototype's
``LivingSummary``), with the prototype's inlined ``jaccard < threshold`` and
monolithic ``Backend`` replaced by the injected ``TopicShiftDetector`` and the
narrow ``SummarizerBackend`` seam.

Pure logic, no I/O — the only outside call is to the injected backend.
"""

from __future__ import annotations

from typing import Protocol

from jarvis.core.rolling_window import RollingWindow
from jarvis.core.topic_shift import TopicShiftDetector

# Don't summarize a conversation shorter than this — there's not enough yet to be
# worth a paragraph (the "cold-start minimum"). Ported from the prototype's
# MIN_UTTERANCES_FOR_SUMMARY. This is LivingSummary *policy*, deliberately not
# part of the pure shift metric (module map §TopicShiftDetector "Scope fence").
MIN_UTTERANCES_FOR_SUMMARY = 3

# After a refresh, require at least this many new utterances before another
# detected shift may trigger again — a debounce so one noisy line can't bounce the
# summary back and forth. Also LivingSummary policy, ported from the prototype.
MIN_UTTERANCES_SINCE_UPDATE = 2


class SummarizerBackend(Protocol):
    """The swappable summarizer seam (module map §"The I/O adapter seams").

    ``summarize`` takes the current window ``transcript`` and the ``prev`` summary
    and returns the new summary text. The mock heuristic is the reference; the
    local model (Qwen2.5/MLX, T-202) drops in behind this same signature without
    touching ``LivingSummary``. The field names match ``FakeSummarizer`` exactly
    (``tests/fakes.py``), so the test fake satisfies this protocol directly.
    """

    def summarize(self, transcript: str, prev: str) -> str: ...


class LivingSummary:
    """A running summary that re-summarizes only on a detected topic shift.

    Args:
        backend: the injected ``SummarizerBackend`` — the model seam that turns a
            transcript into summary text. ``FakeSummarizer`` in tests; Qwen2.5/MLX
            in production (T-202).
        detector: the injected ``TopicShiftDetector`` (T-003) that decides whether
            the live content has drifted from the summary's basis. Defaults to a
            fresh detector with the default threshold; inject one to tune it.
        min_utterances: the cold-start minimum — no summary until the window holds
            at least this many utterances. Defaults to ``MIN_UTTERANCES_FOR_SUMMARY``.

    The current summary is exposed as the ``text`` attribute (empty until the
    first refresh).
    """

    def __init__(
        self,
        backend: SummarizerBackend,
        detector: TopicShiftDetector | None = None,
        min_utterances: int = MIN_UTTERANCES_FOR_SUMMARY,
    ) -> None:
        if min_utterances < 1:
            raise ValueError(f"min_utterances must be >= 1, got {min_utterances}")
        self._backend = backend
        self._detector = detector if detector is not None else TopicShiftDetector()
        self._min_utterances = min_utterances
        self.text = ""
        # The keyword set the standing summary was built on — what the detector
        # compares the live window against to decide "has the topic shifted?".
        self._basis_keywords: set[str] = set()
        # Utterances seen since the last refresh, for the debounce.
        self._utterances_since_update = 0

    def consider_update(self, window: RollingWindow) -> bool:
        """Refresh the summary iff the topic shifted; return whether it refreshed.

        The "only redraw the changed pixels" rule: the expensive
        ``backend.summarize`` runs only on a topic shift past the cold-start fence,
        not on every utterance.

        Returns:
            ``True`` iff the summary was refreshed this call, ``False`` otherwise
            (cold-start not yet cleared, or no shift worth a refresh).
        """
        self._utterances_since_update += 1

        utts = window.utterances()
        if len(utts) < self._min_utterances:
            # Cold start: too little conversation to be worth summarizing.
            return False

        current = window.keywords()

        # The first real summary fires as soon as the cold-start fence clears, even
        # though there's no basis to "shift" away from yet. After that, refresh only
        # on a detected shift that has also cleared the debounce.
        first_time = not self.text
        shifted = (
            self._detector.shifted(current, self._basis_keywords)
            and self._utterances_since_update >= MIN_UTTERANCES_SINCE_UPDATE
        )

        if first_time or shifted:
            self.text = self._backend.summarize(window.transcript(), self.text)
            self._basis_keywords = current
            self._utterances_since_update = 0
            return True
        return False
