"""Capture-and-label tooling — record a live run into a fixture (T-502).

Records a live ``run_live`` session into the eval-plan fixture schema
(:mod:`jarvis.eval.fixture`) **by observation only** — it never alters the
pipeline's decisions, and it never reaches into a core module's internals. It
hangs off three already-public seams:

1. **The gate edges** — a thin :class:`_GateRecorder` wraps the real
   ``TurnTakingGate`` and records every ``on_speech_start()`` / ``on_speech_end()``
   (with the clock time) before delegating. These become the timeline's
   ``speech_start`` / ``speech_end`` moments — the timing the precision metric
   needs to place a resume relative to the politeness gap.
2. **The wall backend** — a :class:`_WallBackendRecorder` wraps the real
   ``WallBackend`` and records every ``WallVerdict`` the detector got back (with
   the clock time + the transcript it ran on). Every *wall* verdict (``is_wall``)
   becomes a Path-B **candidate** — including the ones ``SummonController``
   *dropped* (below floor / no gap / resumed / backed-off), which the
   ``on_interjection`` callback alone would never reveal.
3. **The event callbacks** — ``on_utterance`` / ``on_interjection`` /
   ``on_engagement``, which ``run_live`` already exposes. Utterances become
   ``utterance`` moments; an interjection marks the matching candidate as
   "fired"; a summon engagement is recorded as a Path-A event (excluded from
   precision, per eval-plan).

The recorder emits a **raw** fixture: real timeline + real candidates, but the
ground-truth ``label`` of every candidate is ``UNLABELED`` (and ``match_from`` /
``match_to`` are seeded to a sensible default window). A human then runs the
labeling workflow (:mod:`jarvis.eval.label`) to fill the labels, after which the
runner can score it.

## Privacy / ephemerality (PRD 01 NFR-1.* / PRD 02 §Privacy — the hard-no)

* **Opt-in.** Capture only happens when ``run_live(capture_path=...)`` is given
  (the ``--capture PATH`` flag). Off by default — the default ``--live`` path
  records nothing.
* **Transcripts + events, not audio.** A fixture holds transcribed text +
  timing + verdicts. **No raw audio is ever written** by this module. (If audio
  capture is ever wanted it must be a separate, explicit, local-only opt-in —
  out of scope here.)
* **Local-only, nothing auto-persists.** The fixture is written to the local
  path the user named, and nowhere else. Nothing is uploaded — capture touches
  no network (it only observes the pipeline). The user owns the file and deletes
  it when done; there is no background retention.

This module imports nothing heavy (no mic, no model) — it only wraps objects the
caller already built, so importing it never pulls in audio/ML deps.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from jarvis.core.turn_taking_gate import TurnTakingGate
from jarvis.core.wall_detector import WallBackend
from jarvis.eval.fixture import (
    Candidate,
    Config,
    Fixture,
    Label,
    Moment,
    MomentKind,
)
from jarvis.types import EngagementHandoff, Interjection, Utterance, WallVerdict

# How far past a candidate's detection time its default match window extends, so
# a labeler doesn't have to compute it: the fire (if any) lands after the
# politeness gap, so the window runs from detection to detection + this many
# seconds. The labeler can tighten it. Generous by design (a captured fire must
# fall inside it).
DEFAULT_MATCH_WINDOW_SECONDS = 8.0


@dataclass
class _ObservedVerdict:
    """A WallVerdict the detector returned, with the time + transcript it ran on."""

    t: float
    verdict: WallVerdict
    transcript: str


class _GateRecorder(TurnTakingGate):
    """A ``TurnTakingGate`` that records its edges before delegating to the real logic.

    Subclassing keeps it a drop-in for everything that holds a gate (the
    ``SummonController``, the ``MicSource``) — the recorded edges are pure
    observation; the timing logic is the unmodified parent. It records ``(t,
    kind)`` for every ``on_speech_start`` / ``on_speech_end`` using the same
    injected ``now`` the gate already owns (one clock, no second time source).
    """

    def __init__(self, now: Callable[[], float], **kwargs: object) -> None:
        super().__init__(now, **kwargs)  # type: ignore[arg-type]
        self._now_fn = now
        self.edges: list[tuple[float, MomentKind]] = []

    def on_speech_start(self) -> None:
        self.edges.append((self._now_fn(), MomentKind.SPEECH_START))
        super().on_speech_start()

    def on_speech_end(self) -> None:
        self.edges.append((self._now_fn(), MomentKind.SPEECH_END))
        super().on_speech_end()


class _WallBackendRecorder:
    """Wraps a ``WallBackend`` and records every verdict it returns (with the time).

    A pure pass-through: ``detect_wall`` delegates to the wrapped backend and
    returns its verdict unchanged — the detector and the controller see exactly
    what they would without the recorder. It only *observes*.
    """

    def __init__(self, inner: WallBackend, now: Callable[[], float]) -> None:
        self._inner = inner
        self._now = now
        self.observed: list[_ObservedVerdict] = []

    def detect_wall(self, transcript: str, summary: str) -> WallVerdict:
        verdict = self._inner.detect_wall(transcript, summary)
        self.observed.append(
            _ObservedVerdict(t=self._now(), verdict=verdict, transcript=transcript)
        )
        return verdict


@dataclass
class CaptureRecorder:
    """Accumulates a live run's observations and assembles a raw :class:`Fixture`.

    Construct one per session, wire its hooks into ``run_live`` (the
    ``wrap_gate`` / ``wrap_wall_backend`` helpers + the ``on_*`` recording
    callbacks), let the run play, then call :meth:`build_fixture` to get the raw
    fixture and :meth:`save` to write it.

    Construction args are just identity/provenance; the live wiring is done via
    the methods below.
    """

    fixture_id: str
    description: str = ""
    config: Config = field(default_factory=Config)

    _utterances: list[tuple[float, Utterance]] = field(default_factory=list, init=False)
    _interjections: list[tuple[float, Interjection]] = field(default_factory=list, init=False)
    _summons: list[tuple[float, EngagementHandoff]] = field(default_factory=list, init=False)
    _gate_recorder: _GateRecorder | None = field(default=None, init=False)
    _wall_recorder: _WallBackendRecorder | None = field(default=None, init=False)
    _now: Callable[[], float] | None = field(default=None, init=False)

    # -- wiring helpers: wrap the seams the live run builds -------------------

    def wrap_gate(self, now: Callable[[], float], **gate_kwargs: object) -> TurnTakingGate:
        """Build the recording gate the live run should use (drop-in for ``TurnTakingGate``)."""
        self._now = now
        self._gate_recorder = _GateRecorder(now, **gate_kwargs)
        return self._gate_recorder

    def wrap_wall_backend(self, inner: WallBackend, now: Callable[[], float]) -> WallBackend:
        """Wrap the live wall backend so every verdict is observed (drop-in for ``WallBackend``)."""
        self._now = now
        self._wall_recorder = _WallBackendRecorder(inner, now)
        return self._wall_recorder

    # -- recording callbacks: tee off run_live's existing on_* events --------

    def record_utterance(self, u: Utterance) -> None:
        """Tee ``on_utterance`` — record the transcribed line as a timeline moment."""
        assert self._now is not None, "wrap_gate/wrap_wall_backend must be called first"
        self._utterances.append((self._now(), u))

    def record_interjection(self, i: Interjection) -> None:
        """Tee ``on_interjection`` — record that a Path-B offer actually fired."""
        assert self._now is not None
        self._interjections.append((self._now(), i))

    def record_engagement(self, h: EngagementHandoff) -> None:
        """Tee ``on_engagement`` — record a Path-A summon (Path B via record_interjection)."""
        assert self._now is not None
        if h.trigger_reason == "summon":
            self._summons.append((self._now(), h))

    # -- assembly ------------------------------------------------------------

    def build_fixture(self) -> Fixture:
        """Assemble the raw fixture from everything observed during the run.

        The timeline is the merge of the utterance moments + the gate edges,
        sorted by time (stable). The candidates are derived from the *wall*
        verdicts the detector returned: each becomes an ``UNLABELED`` candidate
        carrying the observed confidence/offer, a default match window, and
        whether the live controller fired on it (matched by category + time).
        """
        timeline = self._build_timeline()
        candidates = self._build_candidates()
        fx = Fixture(
            fixture_id=self.fixture_id,
            description=self.description,
            config=self.config,
            timeline=timeline,
            candidates=candidates,
            source="captured (raw — labels pending)",
        )
        return fx

    def save(self, path: str) -> Fixture:
        """Build the raw fixture and write it to ``path`` (local file only)."""
        fx = self.build_fixture()
        fx.save(path)
        return fx

    # -- internals -----------------------------------------------------------

    def _build_timeline(self) -> list[Moment]:
        moments: list[Moment] = []
        for t, u in self._utterances:
            moments.append(Moment(t=t, kind=MomentKind.UTTERANCE, speaker=u.speaker, text=u.text))
        if self._gate_recorder is not None:
            for t, kind in self._gate_recorder.edges:
                moments.append(Moment(t=t, kind=kind))
        # Stable sort by time; ties keep insertion order (utterances before the
        # edges recorded at the same instant, which matches the live ordering
        # closely enough for a labeler — exact ordering is not load-bearing for
        # the metric, the candidate match windows are).
        moments.sort(key=lambda m: m.t)
        # Re-base to t=0 so the fixture timeline is readable (live monotonic
        # clock starts at ~boot seconds). Shift every moment AND every candidate
        # window by the same offset (done in _build_candidates via _t0).
        return self._rebased(moments)

    def _t0(self) -> float:
        """The earliest observed time, used to re-base the timeline to t=0."""
        times: list[float] = [t for t, _ in self._utterances]
        if self._gate_recorder is not None:
            times.extend(t for t, _ in self._gate_recorder.edges)
        if self._wall_recorder is not None:
            times.extend(o.t for o in self._wall_recorder.observed)
        return min(times) if times else 0.0

    def _rebased(self, moments: list[Moment]) -> list[Moment]:
        t0 = self._t0()
        if t0 == 0.0:
            return moments
        return [
            Moment(t=max(0.0, m.t - t0), kind=m.kind, speaker=m.speaker, text=m.text)
            for m in moments
        ]

    def _build_candidates(self) -> list[Candidate]:
        if self._wall_recorder is None:
            return []
        t0 = self._t0()
        # Detection times (re-based) of every WALL verdict, in order — used to
        # clamp each candidate's default match window so it never overlaps the
        # next candidate (a raw capture must be structurally valid; the labeler
        # only refines the windows, never has to de-overlap them).
        wall_obs = [o for o in self._wall_recorder.observed if o.verdict.is_wall]
        det_times = [max(0.0, o.t - t0) for o in wall_obs]

        candidates: list[Candidate] = []
        for n, obs in enumerate(wall_obs, start=1):
            v = obs.verdict
            t = max(0.0, obs.t - t0)
            # Window runs from detection to detection + DEFAULT, but clamped to
            # just before the NEXT detection so windows stay non-overlapping.
            window_end = t + DEFAULT_MATCH_WINDOW_SECONDS
            if n < len(det_times):
                next_det = det_times[n]
                if next_det > t:  # avoid a zero/negative window on co-timed detections
                    window_end = min(window_end, next_det)
            fired, _ = self._fired_for(obs)
            candidates.append(
                Candidate(
                    candidate_id=f"c{n}",
                    match_from=t,
                    match_to=window_end,
                    wall=True,
                    category=v.category.value,
                    label=Label.UNLABELED,
                    rationale="",
                    observed_confidence=v.confidence,
                    observed_offer=v.offer,
                    observed_fired=fired,
                    observed_drop_reason="" if fired else "no interjection fired for this verdict",
                )
            )
        return candidates

    def _fired_for(self, obs: _ObservedVerdict) -> tuple[bool, float | None]:
        """Did an interjection fire for this observed verdict? Match by category + time.

        A fired ``Interjection`` whose category matches this verdict's category
        and whose fire time is at or after this verdict's detection time (and is
        the nearest such) is taken to be this verdict's fire. Best-effort
        attribution for the audit trail — the runner re-derives fires from the
        labels, so a mis-attribution here never affects a precision number.
        """
        for ti, i in self._interjections:
            if i.category is obs.verdict.category and ti >= obs.t - 1e-6:
                return True, ti
        return False, None
