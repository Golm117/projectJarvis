"""TurnTakingGate — endpoint / politeness-gap / abort timing (T-006).

The timing half of "knowing when to speak". It watches the speech/silence
boundary stream (from VAD in production, scripted events in tests) and answers
three questions the dual-summon machine (``SummonController``, T-007) asks:

* ``settled()`` — has the short *endpoint* gap passed? (someone finished a turn)
* ``politeness_gap_elapsed()`` — has the longer ~2 s *politeness* gap passed?
  (a clear opening for an uninvited interjection)
* ``speech_resumed()`` — did someone start talking again after falling silent?
  (the **abort** signal — yield the floor, never talk over people)

The asymmetry between the short settle and the long politeness gap **is** the
contract (DECISIONS.md 2026-06-15, "Asymmetric dual-summon"): a false summon is
harmless, a false interjection is the assistant talking over people, so Path B
hangs back far longer than Path A. Both thresholds are constructor-injected, not
hard-coded magic, so qa-tuning can calibrate them (Phase 5) in one place.

## The event-input API (designed in T-006 — the gap qa-tuning flagged)

The module map froze the three *output* predicates but left the *input* side
open: how do speech/silence boundaries get fed in? This module pins it.

The gate consumes two boundary events off the VAD timeline:

    on_speech_start()   # VAD detected speech onset  (someone is talking)
    on_speech_end()     # VAD detected speech offset (silence begins here)

Design rationale (see DECISIONS.md):

* **Boundary events, not a per-frame `feed(is_speech)` poll.** The gate's whole
  job is about *durations of silence*, so the two things it needs are the two
  transition instants. An edge API is smaller, has no "did the level cross?"
  bookkeeping, and maps cleanly onto Silero VAD's segment callbacks in Phase 3
  (`on_speech_start`/`on_speech_end` are exactly the events a VAD segmenter
  emits). A level-poll API would push debouncing into the gate and couple it to
  a frame rate.
* **Time comes only from the injected ``now``** (`Callable[[], float]`, the
  single clock convention, module map §"Cross-cutting design constraints" #1).
  The events carry *no* timestamp argument — the gate stamps them from ``now()``
  at the moment they're delivered. That keeps one clock source of truth and lets
  qa-tuning's ``SimulatedClock`` drive every transition: call ``on_speech_end()``,
  ``clock.advance(2.0)``, then read ``politeness_gap_elapsed()`` — no real sleep.
* **The predicates are pure reads of (state, now).** They never mutate; calling
  one twice gives the same answer until time advances or an event arrives. So a
  caller can poll them freely, and a test can assert them at any instant.

Silence is measured from the **most recent** ``on_speech_end()``. A fresh
``on_speech_start()`` re-arms the gate: silence is no longer elapsing, ``settled``
and ``politeness_gap_elapsed`` go back to ``False``, and (if it interrupted a
silence we'd already entered) ``speech_resumed()`` latches ``True`` until the
next ``on_speech_end()`` clears it.

Pure logic, no I/O — the only outside call is the injected clock.
"""

from __future__ import annotations

from collections.abc import Callable

# The short endpoint gap (Path A / summon territory): enough quiet to call a turn
# "done" for a fast, low-risk response. ~500–700 ms per the asymmetric-summon
# decision; 0.6 s sits in the middle. Tunable via the constructor.
DEFAULT_SETTLE_SECONDS = 0.6

# The long politeness gap (Path B / interjection): the assistant waits this long
# before taking an *uninvited* turn, so it only speaks into a clear opening.
# ~2 s per the decision. Tunable via the constructor.
DEFAULT_POLITENESS_GAP_SECONDS = 2.0


class TurnTakingGate:
    """Reports endpoint / politeness-gap / abort timing from VAD boundary events.

    Args:
        now: the injected time source — a zero-arg callable returning monotonic
            seconds (the single clock convention). The gate stamps every event
            from this and measures silence against it; no internal
            ``time.monotonic()``. In tests, pass ``SimulatedClock.now``.
        settle_seconds: the short endpoint gap (Path A). Silence at least this
            long ⇒ ``settled()``. Must be ``>= 0``. Defaults to
            ``DEFAULT_SETTLE_SECONDS`` (0.6 s).
        politeness_gap_seconds: the long politeness gap (Path B). Silence at
            least this long ⇒ ``politeness_gap_elapsed()``. Must be ``>=
            settle_seconds`` (the gap is the *more* patient of the two — the
            asymmetry). Defaults to ``DEFAULT_POLITENESS_GAP_SECONDS`` (2.0 s).

    Initial state: no speech seen yet, no silence started — every predicate is
    ``False`` until the first ``on_speech_end()`` opens a silence to measure.
    """

    def __init__(
        self,
        now: Callable[[], float],
        settle_seconds: float = DEFAULT_SETTLE_SECONDS,
        politeness_gap_seconds: float = DEFAULT_POLITENESS_GAP_SECONDS,
    ) -> None:
        if settle_seconds < 0:
            raise ValueError(f"settle_seconds must be >= 0, got {settle_seconds}")
        if politeness_gap_seconds < settle_seconds:
            raise ValueError(
                "politeness_gap_seconds must be >= settle_seconds "
                f"({politeness_gap_seconds} < {settle_seconds}) — the politeness "
                "gap is the more patient of the two"
            )
        self._now = now
        self._settle = float(settle_seconds)
        self._politeness_gap = float(politeness_gap_seconds)

        # Whether VAD currently reports speech (between a start and its end).
        self._speaking = False
        # When the current silence began (the last on_speech_end), or None if no
        # silence is in effect (we've never heard an end, or we're mid-speech).
        self._silence_since: float | None = None
        # Latches True when speech resumes after a silence had begun; cleared on
        # the next on_speech_end. This is the abort signal for SummonController.
        self._resumed = False

    # -- event input (from VAD / scripted) -----------------------------------

    def on_speech_start(self) -> None:
        """Feed a speech-onset event — someone started talking.

        Re-arms the gate: the silence timer stops (``settled`` /
        ``politeness_gap_elapsed`` go ``False``). If a silence had already begun,
        this onset is a *resumption* — ``speech_resumed()`` latches ``True`` until
        the next ``on_speech_end()``.
        """
        if self._silence_since is not None:
            # Speech came back after a gap had opened → abort signal.
            self._resumed = True
        self._speaking = True
        self._silence_since = None

    def on_speech_end(self) -> None:
        """Feed a speech-offset event — silence begins now.

        Stamps the silence-onset time from ``now()`` and starts the gap clock.
        Clears any previously latched ``speech_resumed`` — a fresh silence is a
        fresh opening to evaluate.
        """
        self._speaking = False
        self._silence_since = self._now()
        self._resumed = False

    # -- output predicates (pure reads of state + now) -----------------------

    def _silence_elapsed(self) -> float | None:
        """Seconds of silence so far, or ``None`` if no silence is in effect."""
        if self._speaking or self._silence_since is None:
            return None
        return self._now() - self._silence_since

    def settled(self) -> bool:
        """``True`` once the short endpoint gap has passed (Path A).

        Quiet for at least ``settle_seconds`` since the last ``on_speech_end()``
        and not currently speaking. ``False`` while speaking or before any speech
        has ended.
        """
        elapsed = self._silence_elapsed()
        return elapsed is not None and elapsed >= self._settle

    def politeness_gap_elapsed(self) -> bool:
        """``True`` once the long politeness gap has passed (Path B).

        Quiet for at least ``politeness_gap_seconds`` since the last
        ``on_speech_end()`` and not currently speaking — a clear opening for an
        uninvited interjection.
        """
        elapsed = self._silence_elapsed()
        return elapsed is not None and elapsed >= self._politeness_gap

    def speech_resumed(self) -> bool:
        """``True`` if speech resumed after a silence had begun (the abort signal).

        Latches on the ``on_speech_start()`` that interrupts a gap and stays
        ``True`` until the next ``on_speech_end()`` opens a fresh silence. This is
        what ``SummonController`` reads to abort a pending interjection and yield
        the floor (hard-no: never talk over resumed speech).
        """
        return self._resumed
