"""``QwenWallBackend`` — the real ``WallBackend`` on Qwen2.5/MLX (T-203, T-508).

This is the Phase-2 fill of the frozen ``WallBackend`` seam declared in
:mod:`jarvis.core.wall_detector`.  It replaces the ``HeuristicWallBackend``
behind the same ``detect_wall(transcript, summary) -> WallVerdict`` signature
with **no change** to ``WallDetector``, ``SummonController``, or any other
core module.

Design decisions (``docs/ml/working-notes.md`` + ``docs/ml/slm-backend.md``
+ ``docs/ml/interjection-prior-art.md`` + ``DECISIONS.md`` 2026-06-15/16):

* The heavy model lives in the injected :class:`~jarvis.ml.qwen.QwenModel`;
  this backend is a thin adapter with no model logic of its own.  T-202
  reuses the **same** ``QwenModel`` instance for summarization — single load,
  no weight duplication.
* ``tokenizer.apply_chat_template`` is used via ``QwenModel.generate``; raw
  string prompts are explicitly forbidden (they degrade quality and inflate
  latency ~2× on Qwen2.5-Instruct models).
* **Graded 1–5 rating (T-508).** The model now outputs a 1–5
  *interjection-worthiness* rating (Inner-Thoughts style; prior-art research
  doc) instead of a near-binary ``is_wall`` + ~0.95 confidence.  The rating
  is mapped into the frozen ``WallVerdict.confidence`` via a calibrated
  lookup table so confidence is now genuinely graded, making the
  ``SummonController`` 0.70 floor a *meaningful* gate (it was inert before
  because every fire landed at ~0.95).  ``is_wall`` is derived from
  ``rating >= 3``.  The ``WallVerdict`` shape is FROZEN — this change is
  entirely within the prompt and the parse logic.
* **Information-Gap CoT (T-508).** A compact structured reasoning step
  precedes the rating: the model briefly reasons about (a) whether there is
  an unanswered question/expressed uncertainty, (b) whether it is
  factual/answerable, (c) whether it is directed at the *group* vs.
  Jarvis/self/rhetorical, and (d) whether a brief offer would actually help.
  The reasoning is surfaced in the JSON as ``"reasoning"`` and is discarded
  after parse (not surfaced in ``WallVerdict``).  Short CoT at this scale
  consistently improves structured-output compliance with no significant
  latency cost (the JSON budget grows slightly; ``max_tokens`` raised to 200).
* **Few-shot exemplars (T-508).** Six exemplars anchoring the failure cases
  from real capture:
  - "What's the square root of 81?" → factual_gap, rating 5 (the MISS to fix)
  - "I wonder what the square root of 81 is." → factual_gap, rating 4 (wh-form)
  - "What's 4 times 7?" → factual_gap, rating 5 (the consistent fire — keep it)
  - "What do you need?" (post-summon, directed at Jarvis) → none, rating 1
  - "I wonder if my volume is too loud." (self-musing) → none, rating 1
  - "I don't remember the date." (declarative gap) → factual_gap, rating 3
  These are placed as assistant-turn style examples *inside* the user message
  (not as full chat turns — Qwen2.5-3B handles in-message examples reliably
  and this avoids chat-template complications with assistant prefills).
* **Rating → confidence mapping (T-508).**  Calibrated to preserve the
  semantics the downstream ``SummonController`` floor expects:
    rating 1 → 0.05   (clear non-wall / irrelevant)
    rating 2 → 0.30   (possible but weak; below the 0.70 floor)
    rating 3 → 0.65   (borderline; still below floor — prompts caution)
    rating 4 → 0.80   (good candidate; clears the 0.70 floor with margin)
    rating 5 → 0.95   (unambiguous gap; high confidence)
  ``is_wall`` is derived from ``rating >= 3`` (ratings 3/4/5 are potential
  interjections; 1/2 are non-walls).  This means a rating-3 wall with
  confidence 0.65 is a wall the detector sees BUT the ``SummonController``
  floor (0.70) will suppress — exactly the intended behavior for borderline
  cases.  qa-tuning should re-calibrate the floor value on the eval now that
  confidence is graded.
* **Precision-first framing.** The system prompt continues to emphasise
  precision over recall (a false fire is costly; a miss is cheap — the
  success metric is precision = useful ÷ total fires).
* **JSON schema in the user message** (not the system message) — the T-201
  spike found this produces more reliable JSON than embedding it in the
  system prompt.
* ``max_tokens=200``: raised from 120 to give the CoT reasoning field
  sufficient budget (~50 tokens for reasoning + ~60 tokens for the structured
  fields).  At ~2–3 tokens/ms on the M5 Pro this adds ≤ ~80 ms over the
  previous budget — still within the ~1.2 s joint headroom (T-505 measured
  775 ms total; see ``docs/ml/qwen-coexistence-spike.md``).
* **Graceful fallback:** any JSON parse failure (malformed JSON, missing
  fields, invalid enum value, out-of-range rating) returns
  :meth:`~jarvis.types.WallVerdict.none` rather than raising to the caller.
"""

from __future__ import annotations

import json
import re

from jarvis.ml.qwen import QwenModel
from jarvis.types import WallCategory, WallVerdict

# ---------------------------------------------------------------------------
# Rating → confidence mapping (T-508)
# ---------------------------------------------------------------------------

# Calibrated 1–5 → [0, 1] confidence.  See module docstring for rationale.
# The SummonController floor is 0.70 — ratings 1/2 fall below it, rating 3
# also falls below it (borderline is flagged but not spoken), ratings 4/5
# clear it.
_RATING_TO_CONFIDENCE: dict[int, float] = {
    1: 0.05,
    2: 0.30,
    3: 0.65,
    4: 0.80,
    5: 0.95,
}

# Minimum rating that sets is_wall=True.  Ratings below this are non-walls.
_WALL_RATING_THRESHOLD = 3


# ---------------------------------------------------------------------------
# Prompt constants
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a precision-first interjection gatekeeper for an ambient AI assistant. "
    "Your job: score how clearly the conversation shows an UNANSWERED, ANSWERABLE GAP "
    "that Jarvis could briefly help with. "
    "A false interjection (firing when not needed) is costly — it interrupts a conversation. "
    "A miss (staying silent on a real gap) is cheap — the conversation continues. "
    "So: err on the side of silence. Only score high when you are confident there is "
    "a genuine, answerable, group-directed gap. "
    "Statements, decisions, plans, and self-directed musings are NOT gaps. "
    "Questions or remarks directed at Jarvis (e.g. just after a wake-word summon) are NOT gaps. "
    "Rhetorical questions and thinking-aloud are NOT gaps. "
    "Reply with ONLY a JSON object — no prose, no markdown fences."
)

# The six few-shot exemplars anchoring the failure/edge cases.
# Format: a short transcript snippet, then the expected JSON output.
# These are embedded in the user message so the model sees them as part of the task
# description (Qwen2.5-3B handles in-message examples well).
_EXEMPLARS = (
    "EXAMPLES (study these before scoring):\n"
    "\n"
    "Example 1 — clear factual question to the group:\n"
    '  Transcript: "Alice: What\'s the square root of 81?"\n'
    '  → {"reasoning": "A direct, specific factual question nobody answered.'
    ' Answerable and group-directed.",'
    ' "rating": 5, "category": "unanswered_question",'
    ' "offer": "That\'s 9 — want me to confirm?"}\n'
    "\n"
    "Example 2 — wh-form gap phrasing (no question mark):\n"
    '  Transcript: "Alice: I wonder what the square root of 81 is."\n'
    '  → {"reasoning": "Expressed uncertainty about a specific fact.'
    " Answerable and group-directed even without '?'.\","
    ' "rating": 4, "category": "factual_gap",'
    ' "offer": "The square root of 81 is 9, if that helps."}\n'
    "\n"
    "Example 3 — arithmetic question (consistent with Example 1):\n"
    '  Transcript: "Alice: What\'s 4 times 7?"\n'
    '  → {"reasoning": "Direct arithmetic question nobody answered. Answerable.",'
    ' "rating": 5, "category": "unanswered_question", "offer": "That\'s 28."}\n'
    "\n"
    "Example 4 — question directed at Jarvis after a summon (NOT a gap):\n"
    '  Transcript: "[Jarvis just engaged] Alice: What do you need?"\n'
    '  → {"reasoning": "Alice is speaking TO Jarvis, not hitting a wall'
    ' between humans. Not an ambient gap.",'
    ' "rating": 1, "category": "none", "offer": ""}\n'
    "\n"
    "Example 5 — self-musing about own situation (NOT a gap):\n"
    '  Transcript: "Alice: I wonder if my volume is too loud."\n'
    '  → {"reasoning": "Alice is musing to herself about her own subjective'
    ' situation. Not a factual gap Jarvis can answer. Not group-directed.",'
    ' "rating": 1, "category": "none", "offer": ""}\n'
    "\n"
    "Example 6 — declarative gap (no question mark, medium confidence):\n"
    '  Transcript: "Alice: I don\'t remember the date we picked."\n'
    '  → {"reasoning": "Expressed uncertainty about a fact. Answerable but'
    ' subtle — no explicit question. Moderate confidence.",'
    ' "rating": 3, "category": "factual_gap",'
    ' "offer": "I can check the date if you\'d like."}\n'
)

_CATEGORY_DEFINITIONS = (
    "WALL CATEGORIES:\n"
    "- unanswered_question: someone asked a clear question that nobody answered"
    ' (e.g. "what time does the keynote start?"). NOT if rhetorical or directed at Jarvis.\n'
    "- factual_gap: someone expressed they don't know/can't remember a fact"
    ' (e.g. "I don\'t remember which date", "what was the conference name?").'
    " Includes wh-form phrasings like 'I wonder what X is' and 'I can't recall Y'.\n"
    "- stuck_point: the conversation is looping, stalled, or going in circles"
    ' (e.g. "we keep coming back to the same problem").'
    " NOT if they're reviewing a completed decision.\n"
    "- explicit_ask: someone expressed a wish or desire for info they don't have"
    ' (e.g. "I wish I knew how long the flight is").'
    " Must be an expressed WISH or DESIRE — not a statement, plan, or musing.\n"
    "- none: no gap — conversation is proceeding normally, self-directed, or Jarvis-directed."
)

_REASONING_INSTRUCTION = (
    "REASONING STEP — before scoring, briefly answer:\n"
    "1. Is there an unanswered question or expressed uncertainty?\n"
    "2. Is it factual/answerable by Jarvis?\n"
    "3. Is it directed at the GROUP (not at Jarvis, not at self, not rhetorical)?\n"
    "4. Would a brief offer actually help?\n"
    "Then assign a RATING 1–5:\n"
    "  5 = unambiguous gap, clearly answerable, group-directed\n"
    "  4 = strong candidate with minor uncertainty\n"
    "  3 = plausible gap but weak/indirect signal\n"
    "  2 = probably not a gap (leaning no)\n"
    "  1 = clearly not a gap (self-directed, rhetorical, Jarvis-directed, statement)"
)

_JSON_SCHEMA_LINE = (
    'Reply with ONLY this JSON (no other text): {"reasoning": "brief reasoning",'
    ' "rating": 1|2|3|4|5,'
    ' "category": "unanswered_question"|"factual_gap"|"stuck_point"|"explicit_ask"|"none",'
    ' "offer": "one natural sentence Jarvis would say, or empty string if rating <= 2"}'
)

_USER_HEADER = "TRANSCRIPT (most recent lines):\n{transcript}\n\nCURRENT SUMMARY:\n{summary}\n\n"

# Max tokens to generate.  Raised from 120 (T-203) to 200 to give the CoT
# reasoning field enough token budget.  Joint latency budget analysis:
# T-505 measured pipeline at 775 ms (ASR 80 ms + summarize 305 ms +
# detect_wall 392 ms) with 1,225 ms margin vs 2 s.  Adding ~80 ms for the
# extra 80 tokens leaves ~1,145 ms of margin — still comfortable.
_MAX_TOKENS = 200

# Regex that strips markdown code fences if the model wraps its JSON output.
_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class QwenWallBackend:
    """The real on-device wall detector backed by Qwen2.5-3B/MLX (T-203, T-508).

    Implements the frozen ``WallBackend`` Protocol from
    :mod:`jarvis.core.wall_detector`:

        ``detect_wall(transcript: str, summary: str) -> WallVerdict``

    T-508 rework: the model now outputs a graded 1–5 interjection-worthiness
    rating instead of near-binary confidence.  The rating is mapped to a
    calibrated ``WallVerdict.confidence`` value so the ``SummonController``
    0.70 floor is a meaningful gate (it was inert before when every fire
    landed at ~0.95).  ``WallVerdict`` shape is unchanged (frozen).

    The backend takes the :class:`~jarvis.ml.qwen.QwenModel` loader via
    constructor injection so the same model instance can be shared with
    :class:`~jarvis.ml.summarizer.QwenSummarizerBackend` (T-202) and the
    ~2 GB weights are never double-loaded.

    Args:
        model: the shared :class:`~jarvis.ml.qwen.QwenModel` loader.  In
            tests, pass a stub that records calls and returns canned JSON
            strings.  In production, pass the single ``QwenModel()`` instance
            wired at startup (the same one given to ``QwenSummarizerBackend``).
        max_tokens: the generation budget (default 200 — fits the reasoning
            field + structured output with margin; callers rarely override).
    """

    def __init__(
        self,
        model: QwenModel,
        max_tokens: int = _MAX_TOKENS,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens

    def detect_wall(self, transcript: str, summary: str) -> WallVerdict:
        """Detect whether the conversation hit a wall and return a verdict.

        Builds a system/user chat-template message pair, calls the injected
        :class:`~jarvis.ml.qwen.QwenModel`, and parses the JSON output into a
        :class:`~jarvis.types.WallVerdict`.

        **Robustness:** any failure in parsing — non-JSON output, missing
        fields, unknown ``category`` value, out-of-range ``rating`` — is
        caught and returns :meth:`~jarvis.types.WallVerdict.none` rather than
        raising.  The caller (``WallDetector``) never sees an exception from
        the model path.

        Args:
            transcript: the current rolling-window transcript, rendered as
                ``"Speaker: text"`` lines.
            summary: the current living summary text.

        Returns:
            A :class:`~jarvis.types.WallVerdict` with ``is_wall``,
            ``category``, ``confidence`` (graded from the 1–5 rating),
            and ``offer``.  Returns :meth:`~jarvis.types.WallVerdict.none`
            on any error.
        """
        messages = _build_messages(transcript, summary)
        raw = self._model.generate(messages, max_tokens=self._max_tokens)
        return _parse_verdict(raw)


# ---------------------------------------------------------------------------
# Public helpers (exported for testing — tests assert on message structure
# and JSON parsing without needing a real model)
# ---------------------------------------------------------------------------


def _build_messages(transcript: str, summary: str) -> list[dict[str, str]]:
    """Build the chat-template message list for the detect_wall task.

    Separated from :class:`QwenWallBackend` so unit tests can assert on the
    exact messages without instantiating a model.

    Args:
        transcript: the rolling-window transcript text (may be empty).
        summary: the current living summary (may be empty).

    Returns:
        A two-element list: ``[{"role": "system", ...}, {"role": "user", ...}]``.
    """
    header = _USER_HEADER.format(
        transcript=transcript.strip() or "(no transcript yet)",
        summary=summary.strip() or "(no summary yet)",
    )
    # The user message assembles: header + exemplars + category defs +
    # reasoning instruction + JSON schema line.
    # Direct concatenation (NOT .format on the whole string) because
    # _JSON_SCHEMA_LINE contains literal JSON braces.
    user_text = (
        header
        + _EXEMPLARS
        + "\n"
        + _CATEGORY_DEFINITIONS
        + "\n\n"
        + _REASONING_INSTRUCTION
        + "\n\n"
        + _JSON_SCHEMA_LINE
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]


def rating_to_confidence(rating: int) -> float:
    """Map a 1–5 interjection-worthiness rating to a calibrated confidence.

    The mapping is calibrated so the ``SummonController`` 0.70 floor is
    meaningful:
      - ratings 1/2 → below floor (non-wall)
      - rating 3 → slightly below floor (borderline: is_wall=True but
        SummonController suppresses it — the "I don't remember the date"
        case deserves a wall flag but not necessarily a spoken offer)
      - ratings 4/5 → above floor (fires the interjection)

    Args:
        rating: integer in [1, 5]; out-of-range values return 0.0.

    Returns:
        A float in [0.0, 1.0].
    """
    return _RATING_TO_CONFIDENCE.get(rating, 0.0)


def _parse_verdict(raw: str) -> WallVerdict:
    """Parse the model's raw text output into a :class:`~jarvis.types.WallVerdict`.

    T-508 changes vs T-203:
    - Extracts ``"rating"`` (int 1–5) and maps it through
      :func:`rating_to_confidence`.
    - ``is_wall`` is derived from ``rating >= _WALL_RATING_THRESHOLD`` (3),
      NOT from the JSON ``is_wall`` field (which the new prompt no longer emits).
    - Extracts ``"reasoning"`` (str) but discards it — it is not surfaced in
      ``WallVerdict`` (the frozen shape has no reasoning field).
    - ``"offer"`` is still extracted and surfaced.
    - All fallback behaviour is unchanged: any parse failure →
      :meth:`~jarvis.types.WallVerdict.none`.

    Args:
        raw: the raw string returned by ``QwenModel.generate``.

    Returns:
        A :class:`~jarvis.types.WallVerdict`, or
        :meth:`~jarvis.types.WallVerdict.none` on any parse failure.
    """
    if not raw or not raw.strip():
        return WallVerdict.none()

    text = raw.strip()

    # Strip markdown code fences if present.
    fence_match = _FENCE_RE.match(text)
    if fence_match:
        text = fence_match.group(1)

    # If there's prose around the JSON, extract the first {...} block.
    if not text.startswith("{"):
        brace_start = text.find("{")
        if brace_start == -1:
            return WallVerdict.none()
        text = text[brace_start:]
    # Trim after the matching closing brace.
    brace_end = text.rfind("}")
    if brace_end != -1:
        text = text[: brace_end + 1]

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return WallVerdict.none()

    if not isinstance(data, dict):
        return WallVerdict.none()

    # Extract and validate the rating field (required; must be int 1–5).
    try:
        rating_raw = data.get("rating")
        if rating_raw is None:
            return WallVerdict.none()
        rating = int(rating_raw)
        if rating not in _RATING_TO_CONFIDENCE:
            return WallVerdict.none()
    except (TypeError, ValueError):
        return WallVerdict.none()

    # Extract category and offer.
    try:
        raw_category = str(data.get("category", "none"))
        offer = str(data.get("offer", ""))
        # reasoning is extracted but discarded (not in WallVerdict shape).
        # _ = data.get("reasoning", "")
    except (TypeError, ValueError):
        return WallVerdict.none()

    # Map rating → confidence.
    confidence = rating_to_confidence(rating)

    # Derive is_wall from rating threshold.
    is_wall = rating >= _WALL_RATING_THRESHOLD

    # Coerce the category string into the StrEnum. Unknown values → none.
    try:
        category = WallCategory(raw_category)
    except ValueError:
        return WallVerdict.none()

    # Enforce invariants:
    #   - NONE iff ¬is_wall
    #   - offer is "" for a non-wall
    if not is_wall:
        return WallVerdict(
            is_wall=False,
            category=WallCategory.NONE,
            confidence=confidence,
            offer="",
        )

    # is_wall is True (rating >= 3) but model said category "none" —
    # normalise: treat as a borderline non-wall with the derived confidence.
    if category is WallCategory.NONE:
        return WallVerdict(
            is_wall=False,
            category=WallCategory.NONE,
            confidence=confidence,
            offer="",
        )

    return WallVerdict(
        is_wall=True,
        category=category,
        confidence=confidence,
        offer=offer,
    )
