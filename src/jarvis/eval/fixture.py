"""The labeled-conversation fixture schema (T-502).

This is the in-code home of the fixture format defined in
``docs/qa/eval-plan.md`` §"The fixture format — labeled conversations". A
**fixture** is one labeled conversation: a monotonic ``timeline`` of moments
(utterance / speech_start / speech_end) plus a list of ground-truth
``candidates`` (where an interjection *could* be evaluated) plus the ``config``
block of the three thresholds T-503 sweeps.

The same shape is produced two ways:

* **Capture** (:mod:`jarvis.eval.capture`) writes a *raw* fixture from a live
  run: the timeline and the candidates are real (the candidates carry the
  detector's ``WallVerdict`` + whether ``SummonController`` fired or dropped it),
  but the ground-truth label fields are **placeholders** (``label: "unlabeled"``)
  for a human to fill.
* **Hand-authoring / labeling** fills those placeholders, producing a *labeled*
  fixture the eval runner can score.

Why dataclasses + explicit (de)serialization rather than raw dicts: the schema
is the contract between capture, the labeler, and the runner; pinning it in one
place keeps all three honest and lets a test round-trip it. JSON (not YAML) is
the on-disk format — it needs no extra dependency and the editor experience is
fine for a labeler (the doc shows the shape).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from jarvis.core.summon_controller import DEFAULT_INTERJECTION_CONFIDENCE_FLOOR
from jarvis.core.turn_taking_gate import (
    DEFAULT_POLITENESS_GAP_SECONDS,
    DEFAULT_SETTLE_SECONDS,
)
from jarvis.types import WallCategory

# Schema version, bumped if the on-disk shape changes incompatibly. Capture
# stamps it; the loader checks it so an old fixture can't be silently misread.
SCHEMA_VERSION = 1


class MomentKind(StrEnum):
    """The three kinds of timeline entry (eval-plan §fixture format).

    * ``UTTERANCE`` — a transcribed line (feeds RollingWindow + WallDetector).
    * ``SPEECH_START`` — VAD onset → ``gate.on_speech_start()``.
    * ``SPEECH_END`` — VAD offset → ``gate.on_speech_end()`` (a silence opens).
    """

    UTTERANCE = "utterance"
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"


class Label(StrEnum):
    """The per-candidate precision ground truth.

    * ``USEFUL`` — an interjection here is correct/well-timed (true positive).
    * ``FALSE`` — an interjection here is a precision error (false positive):
      a thinking-pause, an off-topic cue, or a moment speech was about to resume.
    * ``UNLABELED`` — a freshly-captured candidate a human has not yet judged.
      The runner refuses to score a fixture that still contains these (so an
      un-reviewed capture can never inflate or deflate a precision number).
    """

    USEFUL = "useful"
    FALSE = "false"
    UNLABELED = "unlabeled"


@dataclass(frozen=True)
class Config:
    """The gate/controller thresholds a fixture was authored/captured against.

    The eval injects these into the gate + controller so the labels and the
    timing line up. T-503 *overrides* them to sweep, re-scoring the same
    fixtures (eval-plan §"Calibration hook"). Defaults mirror the production
    defaults so a fixture written without a config block reproduces the shipped
    behavior.
    """

    settle_seconds: float = DEFAULT_SETTLE_SECONDS
    politeness_gap_seconds: float = DEFAULT_POLITENESS_GAP_SECONDS
    interjection_confidence_floor: float = DEFAULT_INTERJECTION_CONFIDENCE_FLOOR

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Config:
        if not d:
            return cls()
        return cls(
            settle_seconds=float(d.get("settle_seconds", DEFAULT_SETTLE_SECONDS)),
            politeness_gap_seconds=float(
                d.get("politeness_gap_seconds", DEFAULT_POLITENESS_GAP_SECONDS)
            ),
            interjection_confidence_floor=float(
                d.get("interjection_confidence_floor", DEFAULT_INTERJECTION_CONFIDENCE_FLOOR)
            ),
        )


@dataclass(frozen=True)
class Moment:
    """One entry on the monotonic timeline (eval-plan §fixture format).

    Every moment has a ``t`` (seconds, monotonic, non-decreasing). An
    ``UTTERANCE`` carries ``speaker`` + ``text``; the two boundary kinds carry
    only ``t``. The runner advances the ``SimulatedClock`` to each ``t`` and
    dispatches the moment (feed the window/detector, or fire a gate edge).
    """

    t: float
    kind: MomentKind
    speaker: str = ""
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"t": self.t, "kind": self.kind.value}
        if self.kind is MomentKind.UTTERANCE:
            d["speaker"] = self.speaker
            d["text"] = self.text
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Moment:
        return cls(
            t=float(d["t"]),
            kind=MomentKind(d["kind"]),
            speaker=str(d.get("speaker", "")),
            text=str(d.get("text", "")),
        )


@dataclass
class Candidate:
    """One ground-truth candidate interjection moment (eval-plan §fixture format).

    A candidate is a point where a wall plausibly exists and the eval checks what
    ``SummonController`` does. It carries the time window a fire must fall in to
    be attributed here, the ground-truth wall semantics the detector *should*
    see, the precision label, and — for a freshly-captured candidate — the raw
    observed facts (what the detector actually returned + whether the controller
    fired or dropped it + why), kept for the labeling audit trail.

    Mutable (not frozen) so the tiny labeling CLI can set ``label`` /
    ``category`` / the match window in place.
    """

    candidate_id: str
    # The window [match_from, match_to] a fire must fall in to match this
    # candidate (handles the politeness-gap delay between wall and fire).
    match_from: float
    match_to: float
    # Ground truth about the wall at this moment (what WallDetector SHOULD see):
    wall: bool
    category: str | None  # a WallCategory wire string, or None if wall is False
    # The precision label. Freshly captured → UNLABELED; a labeler sets it.
    label: Label
    rationale: str = ""

    # --- raw observed facts from capture (informational; not scored) ---------
    # The confidence the detector returned for this candidate's verdict (the
    # value the floor is applied to). None for a hand-authored fixture.
    observed_confidence: float | None = None
    # The offer text the detector returned (for the labeler's context).
    observed_offer: str = ""
    # The category the detector ACTUALLY surfaced, if it differs from the
    # ground-truth ``category``. None ⇒ the detector named the right category
    # (the common case). When set, the runner builds the fired verdict with THIS
    # category, so a right-moment / wrong-category fire scores false (eval-plan
    # §"Score the matched fire") — this is how a mis-firing detector is modeled.
    observed_category: str | None = None
    # Whether the live SummonController actually fired (Path B) on this
    # candidate, and if it dropped it, the human-readable reason. These are what
    # the live run *did*; the runner re-derives its own fires from the labels +
    # config, so these are audit-trail only.
    observed_fired: bool = False
    observed_drop_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "match_from": self.match_from,
            "match_to": self.match_to,
            "wall": self.wall,
            "category": self.category,
            "label": self.label.value,
            "rationale": self.rationale,
            "observed_confidence": self.observed_confidence,
            "observed_offer": self.observed_offer,
            "observed_category": self.observed_category,
            "observed_fired": self.observed_fired,
            "observed_drop_reason": self.observed_drop_reason,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Candidate:
        return cls(
            candidate_id=str(d["candidate_id"]),
            match_from=float(d["match_from"]),
            match_to=float(d["match_to"]),
            wall=bool(d["wall"]),
            category=d.get("category"),
            label=Label(d.get("label", Label.UNLABELED.value)),
            rationale=str(d.get("rationale", "")),
            observed_confidence=(
                None if d.get("observed_confidence") is None else float(d["observed_confidence"])
            ),
            observed_offer=str(d.get("observed_offer", "")),
            observed_category=d.get("observed_category"),
            observed_fired=bool(d.get("observed_fired", False)),
            observed_drop_reason=str(d.get("observed_drop_reason", "")),
        )

    def validate(self) -> None:
        """Raise ``ValueError`` if this candidate is internally inconsistent."""
        if self.match_to < self.match_from:
            raise ValueError(
                f"candidate {self.candidate_id}: match_to ({self.match_to}) "
                f"< match_from ({self.match_from})"
            )
        if self.wall and self.category in (None, WallCategory.NONE.value):
            raise ValueError(
                f"candidate {self.candidate_id}: wall is True but category is "
                f"{self.category!r} (a wall must name a WallCategory)"
            )
        if not self.wall and self.category not in (None, WallCategory.NONE.value):
            raise ValueError(
                f"candidate {self.candidate_id}: wall is False but category is "
                f"{self.category!r} (a non-wall must have category null)"
            )
        if self.category is not None:
            # Raises ValueError if not a valid wire string.
            WallCategory(self.category)
        if self.observed_category is not None:
            cat = WallCategory(self.observed_category)  # raises if invalid
            if cat is WallCategory.NONE:
                raise ValueError(
                    f"candidate {self.candidate_id}: observed_category is 'none' "
                    "(a fired verdict always carries a real category)"
                )


@dataclass
class Fixture:
    """One labeled conversation — the unit the eval scores (eval-plan §fixture).

    Mutable so the labeler can edit candidates in place. The on-disk form is the
    JSON of :meth:`to_dict`; load with :func:`load_fixture`.
    """

    fixture_id: str
    description: str = ""
    config: Config = field(default_factory=Config)
    timeline: list[Moment] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    # Provenance: how this fixture came to be (e.g. "captured 2026-06-16" /
    # "hand-authored" / "seeded from T-505 live run"). Free-form.
    source: str = ""
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "fixture_id": self.fixture_id,
            "description": self.description,
            "source": self.source,
            "config": self.config.to_dict(),
            "timeline": [m.to_dict() for m in self.timeline],
            "candidates": [c.to_dict() for c in self.candidates],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Fixture:
        version = int(d.get("schema_version", SCHEMA_VERSION))
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"fixture schema_version {version} != supported {SCHEMA_VERSION} "
                f"(fixture_id={d.get('fixture_id')!r})"
            )
        return cls(
            fixture_id=str(d["fixture_id"]),
            description=str(d.get("description", "")),
            config=Config.from_dict(d.get("config")),
            timeline=[Moment.from_dict(m) for m in d.get("timeline", [])],
            candidates=[Candidate.from_dict(c) for c in d.get("candidates", [])],
            source=str(d.get("source", "")),
            schema_version=version,
        )

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def save(self, path: str | Path) -> None:
        """Write this fixture to ``path`` as pretty JSON (local file, the user owns it)."""
        Path(path).write_text(self.to_json() + "\n", encoding="utf-8")

    def validate(self) -> None:
        """Raise ``ValueError`` if the fixture is structurally inconsistent.

        Checks: monotonic (non-decreasing) timeline ``t``; each candidate is
        internally consistent; candidate ``match`` windows are non-overlapping
        (the runner attributes a fire to at most one candidate — eval-plan
        §"Precision computation").
        """
        last_t = float("-inf")
        for m in self.timeline:
            if m.t < last_t:
                raise ValueError(
                    f"fixture {self.fixture_id}: timeline not monotonic ({m.t} < {last_t})"
                )
            last_t = m.t
        for c in self.candidates:
            c.validate()
        ordered = sorted(self.candidates, key=lambda c: c.match_from)
        for a, b in zip(ordered, ordered[1:], strict=False):
            if b.match_from < a.match_to:
                raise ValueError(
                    f"fixture {self.fixture_id}: candidate match windows overlap "
                    f"({a.candidate_id} [{a.match_from},{a.match_to}] vs "
                    f"{b.candidate_id} [{b.match_from},{b.match_to}])"
                )

    def is_fully_labeled(self) -> bool:
        """``True`` iff no candidate is still ``UNLABELED`` (ready to score)."""
        return all(c.label is not Label.UNLABELED for c in self.candidates)


def load_fixture(path: str | Path) -> Fixture:
    """Load a fixture from a JSON file on the local disk."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    fx = Fixture.from_dict(data)
    fx.validate()
    return fx


def loads_fixture(text: str) -> Fixture:
    """Parse a fixture from a JSON string (round-trip companion to ``to_json``)."""
    fx = Fixture.from_dict(json.loads(text))
    fx.validate()
    return fx
