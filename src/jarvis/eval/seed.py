"""Seed fixtures — the starter labeled corpus (T-502).

Hand-authored labeled fixtures, in the eval-plan schema, that the precision eval
runs on *today* — before a large captured corpus exists. Two flavors:

* **Real-session fixtures** — distilled from this session's live runs (NOTES.md
  T-505 / T-204 / T-105), so the corpus reflects actual observed behavior:
  - ``seed_useful_factual_gap`` — the recurring **true positive**: "What was the
    date of the conference again?" → ``factual_gap @ 0.95`` fires into a clean
    opening (useful).
  - ``seed_false_what_do_you_need`` — the **borderline false positive**: "What do
    you need?" detected as ``factual_gap @ 0.95`` *during a summon exchange*
    (Jarvis was just addressed; the question is directed AT Jarvis, not an
    unanswered wall) → a fire here is a false interjection. This is exactly the
    case T-503 must tune away (see the module's qa note below).
  - ``seed_summon_excluded`` — a **Path-A summon** ("Jarvis, add that to my
    calendar."): excluded from precision entirely (the eval never counts it).

* **Behavior fixtures** — the five eval-plan illustrations made concrete
  (``ff-useful-unanswered-question`` … ``ff-below-floor``), so the runner
  exercises each scored behavior (a clean fire, an abort-on-resume, a
  wrong-category fire, a back-off suppression, a below-floor miss).

``seed_fixtures()`` returns them all. ``write_seed_fixtures(dir)`` emits each as
a JSON file (the on-disk corpus). These are the yardstick T-503 sweeps thresholds
against.

## qa verdict on "What do you need?" (asked in the T-502 brief)

I label it **FALSE**. Two independent reasons, either sufficient:

1. *Conversational role.* It surfaced inside a summon exchange — the user had
   just engaged Jarvis, and "What do you need?" is a turn *addressed to Jarvis*,
   not a wall hanging in the air between humans. Offering to "look that up" is
   Jarvis answering its own rhetorical-ish question — noise, not help. A
   well-timed interjection requires an *unanswered* gap among the speakers; this
   is the opposite.
2. *Precision cost.* Even granting ambiguity, the success metric is
   precision-first: a false interjection (talking over / offering noise) is the
   costly error, a miss is cheap. When a candidate is this borderline, the
   correct v0 label for the *yardstick* is FALSE, so the sweep is pushed toward
   suppressing it. If T-503 finds the floor/gap can't separate it from the true
   positive (both are ``factual_gap @ 0.95`` — the Qwen near-binary-confidence
   problem from NOTES T-203), that is a real finding: the lever is then the
   detector/context (does the wall sit inside a just-engaged exchange?), not the
   confidence floor. Recorded here so the sweep starts from the honest label.
"""

from __future__ import annotations

from pathlib import Path

from jarvis.core.summon_controller import DEFAULT_INTERJECTION_CONFIDENCE_FLOOR
from jarvis.core.turn_taking_gate import (
    DEFAULT_POLITENESS_GAP_SECONDS,
    DEFAULT_SETTLE_SECONDS,
)
from jarvis.eval.fixture import (
    Candidate,
    Config,
    Fixture,
    Label,
    Moment,
    MomentKind,
)

_DEFAULT_CONFIG = Config(
    settle_seconds=DEFAULT_SETTLE_SECONDS,
    politeness_gap_seconds=DEFAULT_POLITENESS_GAP_SECONDS,
    interjection_confidence_floor=DEFAULT_INTERJECTION_CONFIDENCE_FLOOR,
)


def _utt(t: float, speaker: str, text: str) -> Moment:
    return Moment(t=t, kind=MomentKind.UTTERANCE, speaker=speaker, text=text)


def _start(t: float) -> Moment:
    return Moment(t=t, kind=MomentKind.SPEECH_START)


def _end(t: float) -> Moment:
    return Moment(t=t, kind=MomentKind.SPEECH_END)


def _engaged(t: float) -> Moment:
    return Moment(t=t, kind=MomentKind.ENGAGEMENT)


# ---------------------------------------------------------------------------
# Real-session fixtures (distilled from this session's live runs)
# ---------------------------------------------------------------------------
def seed_useful_factual_gap() -> Fixture:
    """TP: the recurring live trigger fires into a clean opening (useful)."""
    return Fixture(
        fixture_id="seed-useful-factual-gap",
        description=(
            "Live trigger (T-105/T-204/T-505): 'What was the date of the conference "
            "again?' detected as factual_gap @ 0.95, fires after a clean ~2 s opening."
        ),
        source="seeded from live runs (NOTES.md T-505)",
        config=_DEFAULT_CONFIG,
        timeline=[
            _start(0.0),
            _utt(0.0, "A", "What was the date of the conference again?"),
            _end(2.4),  # A finishes; a clean silence opens, nobody answers
        ],
        candidates=[
            Candidate(
                candidate_id="c1",
                match_from=2.4,
                match_to=8.0,
                wall=True,
                category="factual_gap",
                label=Label.USEFUL,
                rationale=(
                    "An unanswered factual gap among the speakers, followed by a clean "
                    "2 s opening — a well-timed offer to look it up. Fires once."
                ),
                observed_confidence=0.95,
                observed_offer="Could you remind me of the conference date?",
                observed_fired=True,
            )
        ],
    )


def seed_false_what_do_you_need() -> Fixture:
    """Borderline FP: 'What do you need?' inside a summon exchange (false).

    The user just summoned Jarvis; "What do you need?" is directed at Jarvis, not
    a wall hanging between humans. The heuristic/Qwen detector flags it as
    factual_gap @ 0.95 (a question-form gap) and the gap opens cleanly, so the
    controller WOULD fire — but a fire here is a false interjection (Jarvis
    offering to look up its own rhetorical question). Labeled FALSE; this is the
    case T-503 must learn to suppress.
    """
    return Fixture(
        fixture_id="seed-false-what-do-you-need",
        description=(
            "Borderline FP observed this session: 'What do you need?' detected as "
            "factual_gap @ 0.95 during a summon exchange — a fire here is a false "
            "interjection (the question is addressed to Jarvis, not an unanswered wall). "
            "The timeline encodes the preceding engagement (the user just summoned "
            "Jarvis) so the T-503 post-engagement cooldown can suppress the fire."
        ),
        source="seeded from live runs (qa verdict: FALSE — see jarvis.eval.seed docstring)",
        config=_DEFAULT_CONFIG,
        timeline=[
            # The user summons Jarvis → an engagement at t=0 (Path A; the summon
            # utterance itself is excluded from precision, but the engagement is what
            # arms the cooldown). Then the "What do you need?" turn surfaces ~2 s
            # later — well inside the post-engagement cooldown.
            _start(0.0),
            _utt(0.0, "A", "Jarvis, what's the weather?"),
            _engaged(0.0),  # summon engages immediately (Path A)
            _end(1.0),
            _start(2.0),
            _utt(2.0, "A", "What do you need?"),
            _end(3.5),  # short pause; clean opening follows
        ],
        candidates=[
            Candidate(
                candidate_id="c1",
                match_from=3.5,
                match_to=10.0,
                wall=True,
                category="factual_gap",
                label=Label.FALSE,
                rationale=(
                    "Question is directed AT Jarvis inside a just-engaged exchange, not "
                    "an unanswered gap among speakers. Offering to look it up is noise. "
                    "Precision-first: borderline → FALSE in the yardstick. T-503: the "
                    "post-engagement cooldown (engagement at t=0) suppresses this fire."
                ),
                observed_confidence=0.95,
                observed_offer="Want me to help with that?",
                observed_fired=True,
            )
        ],
    )


def seed_summon_excluded() -> Fixture:
    """Path-A summon — excluded from precision entirely (no candidate to score)."""
    return Fixture(
        fixture_id="seed-summon-excluded",
        description=(
            "Path-A summon ('Jarvis, add that to my calendar.'): an invited engagement, "
            "excluded from the interjection-precision metric (no Path-B candidate)."
        ),
        source="seeded from live runs (NOTES.md T-105/T-204)",
        config=_DEFAULT_CONFIG,
        timeline=[
            _start(0.0),
            _utt(0.0, "A", "Jarvis, add that to my calendar for seven."),
            _end(2.0),
        ],
        candidates=[],  # a summon is never a precision candidate
    )


# ---------------------------------------------------------------------------
# Behavior fixtures (the five eval-plan illustrations, made concrete)
# ---------------------------------------------------------------------------
def ff_useful_unanswered_question() -> Fixture:
    """One useful fire, matched, right category → precision 1.0 (eval-plan #1)."""
    return Fixture(
        fixture_id="ff-useful-unanswered-question",
        description="B asks, A never answers, clean 2 s opening. One useful fire.",
        source="eval-plan illustrative #1",
        config=_DEFAULT_CONFIG,
        timeline=[
            _start(0.0),
            _utt(0.0, "B", "What was the name of that API we used last quarter?"),
            _end(2.4),
        ],
        candidates=[
            Candidate(
                candidate_id="c1",
                match_from=2.4,
                match_to=8.0,
                wall=True,
                category="unanswered_question",
                label=Label.USEFUL,
                rationale="Unanswered question + clean 2 s opening.",
                observed_confidence=0.85,
                observed_offer="Want me to look that up?",
            )
        ],
    )


def ff_false_thinking_pause() -> Fixture:
    """Abort-on-resume → None, no fire → would-be FP removed from denominator (#2)."""
    return Fixture(
        fixture_id="ff-false-thinking-pause",
        description=(
            "A pauses mid-thought (a factual_gap cue) but resumes before the 2 s gap "
            "elapses. The controller aborts on resume → no fire (FP removed)."
        ),
        source="eval-plan illustrative #2",
        config=_DEFAULT_CONFIG,
        timeline=[
            _start(0.0),
            _utt(0.0, "A", "What was that number... hold on, I don't remember."),
            _end(0.6),  # brief pause
            _start(1.8),  # resumes BEFORE the 2 s politeness gap → abort
            _utt(1.8, "A", "Oh right, it was forty-two."),
            _end(3.0),
        ],
        candidates=[
            Candidate(
                candidate_id="c1",
                match_from=0.6,
                match_to=1.8,
                wall=True,
                category="factual_gap",
                label=Label.FALSE,
                rationale="A thinking-pause; A resumes before the gap. A fire here would be false.",
                observed_confidence=0.80,
                observed_offer="Want me to look that up?",
            )
        ],
    )


def ff_false_wrong_category() -> Fixture:
    """Right moment, wrong category → scored false (#3)."""
    return Fixture(
        fixture_id="ff-false-wrong-category",
        description=(
            "A real stuck_point wall with a clean opening, but the (mis-firing) verdict "
            "carries category factual_gap → a fire matches the moment but mismatches "
            "category → scored FALSE."
        ),
        source="eval-plan illustrative #3",
        config=_DEFAULT_CONFIG,
        timeline=[
            _start(0.0),
            _utt(0.0, "A", "We keep going in circles on this, I'm stuck."),
            _end(2.4),
        ],
        candidates=[
            Candidate(
                candidate_id="c1",
                match_from=2.4,
                match_to=8.0,
                wall=True,
                # Ground-truth category is stuck_point; the detector mis-fires
                # factual_gap (observed_category), so the fire carries the wrong
                # category and scores false despite matching the moment.
                category="stuck_point",
                label=Label.USEFUL,
                rationale="Real stuck_point, but a factual_gap fire is the wrong offer → false.",
                observed_confidence=0.95,
                observed_offer="Want me to look that up?",
                observed_category="factual_gap",
            )
        ],
    )


def ff_backoff_no_nag() -> Fixture:
    """Same wall surfaces twice; the repeat is suppressed by back-off (#4).

    Two openings, the SAME offer both times. Fire #1 is useful; fire #2 is
    suppressed by ``SummonController`` back-off (``category::offer`` de-dupe) → it
    never fires → only one fire counted. The repeat candidate is labeled USEFUL
    (the wall is still real) but the controller correctly stays silent, so it
    records as a *miss*, not a false fire — demonstrating back-off improving
    precision (it can only remove would-be fires, never add false ones).
    """
    return Fixture(
        fixture_id="ff-backoff-no-nag",
        description=(
            "The same wall (identical offer) surfaces across two openings. Fire #1 "
            "useful; fire #2 suppressed by back-off → only one fire counted."
        ),
        source="eval-plan illustrative #4",
        config=_DEFAULT_CONFIG,
        timeline=[
            _start(0.0),
            _utt(0.0, "A", "What was the vendor's name again?"),
            _end(2.4),  # opening 1 — fire #1
            _start(6.0),
            _utt(6.0, "A", "Hmm, still can't recall the vendor."),
            _end(8.4),  # opening 2 — same wall, back-off suppresses
        ],
        candidates=[
            Candidate(
                candidate_id="c1",
                match_from=2.4,
                match_to=5.5,
                wall=True,
                category="factual_gap",
                label=Label.USEFUL,
                rationale="First surfacing of the wall + clean opening → useful fire.",
                observed_confidence=0.90,
                observed_offer="Want me to find the vendor?",
            ),
            Candidate(
                candidate_id="c2",
                match_from=8.4,
                match_to=12.0,
                wall=True,
                category="factual_gap",
                label=Label.USEFUL,
                rationale=(
                    "Same wall, same offer, second opening — back-off suppresses the "
                    "repeat (no nagging). Stays silent → recorded as a miss, not a fire."
                ),
                observed_confidence=0.90,
                observed_offer="Want me to find the vendor?",  # identical signature → backed off
            ),
        ],
    )


def ff_below_floor() -> Fixture:
    """Real wall, confidence below the floor → no fire → a miss, precision unaffected (#5)."""
    return Fixture(
        fixture_id="ff-below-floor",
        description=(
            "A real wall but confidence 0.55 (< 0.70 floor), clean opening. No fire "
            "(sub-threshold) → a miss (recall datum); precision unaffected."
        ),
        source="eval-plan illustrative #5",
        config=_DEFAULT_CONFIG,
        timeline=[
            _start(0.0),
            _utt(0.0, "A", "I'm not totally sure what the budget was."),
            _end(2.4),
        ],
        candidates=[
            Candidate(
                candidate_id="c1",
                match_from=2.4,
                match_to=8.0,
                wall=True,
                category="factual_gap",
                label=Label.USEFUL,
                rationale="Real useful wall, but below the floor → stays silent (a miss).",
                observed_confidence=0.55,
                observed_offer="Want me to look that up?",
            )
        ],
    )


def ff_false_stale_pending_wall() -> Fixture:
    """A wall cached during silence, but no clean opening arrives until *after* the
    pending-wall TTL — a late fire here would be a stale false interjection (T-503).

    Models the T-302/T-303 carry-forward: the continuous loop caches the wall and
    re-evaluates it during silence, but the conversation has genuinely moved on, so
    the politeness gap only opens long after the wall is fresh. The first clean
    opening lands past the 12 s TTL → ``AttentionLayer.tick()`` drops the stale
    wall → no fire. Without the TTL this would fire late as noise about a passed
    topic. Labeled FALSE: a fire in this late window is a precision error.
    """
    return Fixture(
        fixture_id="ff-false-stale-pending-wall",
        description=(
            "A factual_gap wall is cached, but no clean ~2 s opening arrives until "
            "after the 12 s pending-wall TTL (the conversation moved on). A late fire "
            "would be a stale false interjection — the T-503 TTL drops it first."
        ),
        source="T-503 staleness fixture (carry-forward from T-302/T-303 review)",
        config=_DEFAULT_CONFIG,
        timeline=[
            # The wall surfaces, but speech keeps going (chatter, no clean gap) so the
            # wall is cached and never gets its opening while fresh.
            _start(0.0),
            _utt(0.0, "A", "What was that vendor's name? Anyway, moving on —"),
            _end(0.5),  # too short to fire (< politeness gap); wall is cached
            _start(0.8),
            _utt(0.8, "A", "let's talk about the launch plan instead."),
            _end(1.3),
            _start(1.6),
            _utt(1.6, "B", "Right, the launch. I think we ship Tuesday."),
            _end(2.1),
            _start(2.4),
            _utt(2.4, "B", "And we should line up the press release too."),
            # A clean opening finally arrives — but only at t=15, past the 12 s TTL.
            _end(15.0),
        ],
        candidates=[
            Candidate(
                candidate_id="c1",
                # The only clean opening is the late one (>= TTL from the wall at t=0).
                match_from=15.0,
                match_to=20.0,
                wall=True,
                category="factual_gap",
                label=Label.FALSE,
                # The wall was cached when it surfaced at t=0; its only opening is at
                # t=15, so the TTL (12 s, aged from here) expires first → stale drop.
                wall_detected_at=0.0,
                rationale=(
                    "The wall went stale: its only clean opening lands ~15 s after it "
                    "surfaced, long after the conversation moved to the launch plan. A "
                    "fire here is a late false interjection about a passed topic — the "
                    "T-503 pending-wall TTL (12 s) drops it before it can fire."
                ),
                observed_confidence=0.90,
                observed_offer="Want me to find the vendor?",
                observed_fired=False,
                observed_drop_reason="pending-wall TTL expired (stale)",
            )
        ],
    )


def seed_fixtures() -> list[Fixture]:
    """Every seeded fixture (real-session + behavior illustrations)."""
    return [
        seed_useful_factual_gap(),
        seed_false_what_do_you_need(),
        seed_summon_excluded(),
        ff_useful_unanswered_question(),
        ff_false_thinking_pause(),
        ff_false_wrong_category(),
        ff_backoff_no_nag(),
        ff_below_floor(),
        ff_false_stale_pending_wall(),
    ]


def write_seed_fixtures(directory: str | Path) -> list[Path]:
    """Emit every seeded fixture as a JSON file under ``directory``; return the paths."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for fx in seed_fixtures():
        fx.validate()
        p = d / f"{fx.fixture_id}.json"
        fx.save(p)
        paths.append(p)
    return paths


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "docs/qa/fixtures"
    written = write_seed_fixtures(target)
    for p in written:
        print(p)
