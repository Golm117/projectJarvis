"""Labeling workflow — fill a raw capture's ground truth (T-502).

A captured fixture (:mod:`jarvis.eval.capture`) is *raw*: real timeline + real
candidates, but every candidate's ``label`` is ``UNLABELED``. Before the runner
can score it, a human must judge each candidate: was this a real wall, and is an
interjection *here* useful or false?

The intended workflow is intentionally lightweight — there are two equally valid
ways to label, and which you use is taste:

1. **Edit the JSON directly.** The captured file is pretty-printed JSON. For each
   entry in ``candidates``, set ``"label"`` to ``"useful"`` or ``"false"``,
   optionally fix ``"category"`` (if the detector got it wrong), tighten
   ``"match_from"`` / ``"match_to"``, and add a ``"rationale"``. Each candidate
   carries ``observed_confidence`` / ``observed_offer`` / ``observed_fired`` /
   ``observed_drop_reason`` so you can see exactly what the live run did. This is
   the primary path; the captured file is self-describing.

2. **The tiny CLI in this module** — for a guided pass without hand-editing JSON:

       uv run python -m jarvis.eval.label show    FIXTURE.json
       uv run python -m jarvis.eval.label set      FIXTURE.json c2 useful --rationale "..."
       uv run python -m jarvis.eval.label validate FIXTURE.json

   ``show`` prints each candidate + its observed facts + current label;
   ``set`` writes one candidate's label (and optional rationale / category /
   window) back to the file; ``validate`` confirms the fixture is structurally
   sound and fully labeled (ready for the runner).

The functions are the API; the ``__main__`` block is the thin CLI over them.
"""

from __future__ import annotations

import argparse
import sys

from jarvis.eval.fixture import Candidate, Fixture, Label, load_fixture
from jarvis.types import WallCategory


def set_label(
    fx: Fixture,
    candidate_id: str,
    label: Label,
    *,
    rationale: str | None = None,
    category: str | None = None,
    match_from: float | None = None,
    match_to: float | None = None,
) -> Candidate:
    """Set the ground truth on one candidate in place; return the updated candidate.

    ``label`` is the precision verdict (``USEFUL`` / ``FALSE``). The optional
    overrides let a labeler correct a captured candidate: fix the ``category``
    the detector mis-named, tighten the match window, or record why. Raises
    ``KeyError`` if no candidate has that id, ``ValueError`` on an invalid
    category.
    """
    c = _find(fx, candidate_id)
    c.label = label
    if rationale is not None:
        c.rationale = rationale
    if category is not None:
        WallCategory(category)  # validate the wire string
        c.category = category
    if match_from is not None:
        c.match_from = match_from
    if match_to is not None:
        c.match_to = match_to
    c.validate()
    return c


def unlabeled_ids(fx: Fixture) -> list[str]:
    """The ids of candidates still ``UNLABELED`` (what's left to do)."""
    return [c.candidate_id for c in fx.candidates if c.label is Label.UNLABELED]


def _find(fx: Fixture, candidate_id: str) -> Candidate:
    for c in fx.candidates:
        if c.candidate_id == candidate_id:
            return c
    raise KeyError(f"no candidate {candidate_id!r} in fixture {fx.fixture_id!r}")


def render_candidates(fx: Fixture) -> str:
    """A human-readable summary of every candidate + its observed facts + label."""
    lines = [f"fixture: {fx.fixture_id}  ({len(fx.candidates)} candidate(s))"]
    if fx.description:
        lines.append(f"  {fx.description}")
    for c in fx.candidates:
        conf = "?" if c.observed_confidence is None else f"{c.observed_confidence:.2f}"
        fired = "FIRED" if c.observed_fired else "dropped"
        lines.append(
            f"  [{c.candidate_id}] {c.label.value.upper():9} "
            f"window=[{c.match_from:.1f},{c.match_to:.1f}]s "
            f"wall={c.wall} category={c.category} "
            f"observed: {fired} @ {conf}"
        )
        if c.observed_offer:
            lines.append(f"        offer: {c.observed_offer!r}")
        if c.rationale:
            lines.append(f"        rationale: {c.rationale}")
    left = unlabeled_ids(fx)
    lines.append(f"  unlabeled: {left if left else 'none — ready to score'}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jarvis.eval.label",
        description="Label a captured interjection-precision fixture.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_show = sub.add_parser("show", help="print every candidate + observed facts + label")
    p_show.add_argument("fixture")

    p_set = sub.add_parser("set", help="set one candidate's ground-truth label")
    p_set.add_argument("fixture")
    p_set.add_argument("candidate_id")
    p_set.add_argument("label", choices=[Label.USEFUL.value, Label.FALSE.value])
    p_set.add_argument("--rationale", default=None)
    p_set.add_argument("--category", default=None, help="override the detector's category")
    p_set.add_argument("--match-from", type=float, default=None)
    p_set.add_argument("--match-to", type=float, default=None)

    p_val = sub.add_parser("validate", help="check the fixture is sound + fully labeled")
    p_val.add_argument("fixture")

    args = parser.parse_args(argv)

    if args.cmd == "show":
        fx = load_fixture(args.fixture)
        print(render_candidates(fx))
        return 0

    if args.cmd == "set":
        fx = load_fixture(args.fixture)
        c = set_label(
            fx,
            args.candidate_id,
            Label(args.label),
            rationale=args.rationale,
            category=args.category,
            match_from=args.match_from,
            match_to=args.match_to,
        )
        fx.save(args.fixture)
        print(f"set [{c.candidate_id}] -> {c.label.value}")
        left = unlabeled_ids(fx)
        print(f"unlabeled remaining: {left if left else 'none — ready to score'}")
        return 0

    if args.cmd == "validate":
        fx = load_fixture(args.fixture)
        fx.validate()
        left = unlabeled_ids(fx)
        if left:
            print(f"NOT ready: unlabeled candidates {left}", file=sys.stderr)
            return 1
        print(f"OK: fixture {fx.fixture_id} is sound and fully labeled.")
        return 0

    return 2  # unreachable (subparser is required)


if __name__ == "__main__":
    raise SystemExit(_main())
