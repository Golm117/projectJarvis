"""``QwenWallBackend`` — the real ``WallBackend`` on Qwen2.5/MLX (T-203).

This is the Phase-2 fill of the frozen ``WallBackend`` seam declared in
:mod:`jarvis.core.wall_detector`.  It replaces the ``HeuristicWallBackend``
behind the same ``detect_wall(transcript, summary) -> WallVerdict`` signature
with **no change** to ``WallDetector``, ``SummonController``, or any other
core module.

Design decisions (``docs/ml/working-notes.md`` + ``docs/ml/slm-backend.md``
+ ``DECISIONS.md`` 2026-06-15):

* The heavy model lives in the injected :class:`~jarvis.ml.qwen.QwenModel`;
  this backend is a thin adapter with no model logic of its own.  T-202
  reuses the **same** ``QwenModel`` instance for summarization — single load,
  no weight duplication.
* ``tokenizer.apply_chat_template`` is used via ``QwenModel.generate``; raw
  string prompts are explicitly forbidden (they degrade quality and inflate
  latency ~2× on Qwen2.5-Instruct models).
* **Precision over recall** is the core prompt strategy — the success metric is
  interjection precision.  The model is instructed to flag a wall only when
  confident; "when in doubt, return none."  Each category is defined with a
  concrete positive example and a concrete negative example (what is NOT that
  category) to reduce false positives.
* **T-201 false-positive fix:** the 3B model flagged a clear statement/decision
  ("we'll send the PR in 10 minutes") as ``explicit_ask``.  The prompt now
  explicitly states that statements, plans, and decisions are NOT walls, with
  a concrete example.  ``explicit_ask`` requires a *wish* or *desire* expressed
  verbally — not a rhetorical question or a statement of intent.
* **Confidence calibration:** the model is asked to reserve high confidence
  (≥ 0.80) for unambiguous cases and to return low confidence (< 0.70) or
  ``none`` when uncertain.  The downstream ``SummonController.interjection_
  confidence_floor = 0.70`` (T-007) acts as the speak gate — the backend
  never applies that threshold itself (the confidence is surfaced raw).
* **JSON schema in the user message** (not the system message) — the T-201
  spike found this produces more reliable JSON than embedding it in the system
  prompt.  The schema is kept to one line so the model has more token budget
  for reasoning.
* ``max_tokens=120``: the spike measured ~366 ms median latency at this budget
  on this M5 Pro (docs/ml/qwen-coexistence-spike.md §joint-budget).
* **Graceful fallback:** any JSON parse failure (malformed JSON, missing fields,
  invalid enum value, missing ``is_wall``) returns :meth:`~jarvis.types.WallVerdict.none`
  rather than raising to the caller.
"""

from __future__ import annotations

import json
import re

from jarvis.ml.qwen import QwenModel
from jarvis.types import WallCategory, WallVerdict

# ---------------------------------------------------------------------------
# Prompt constants
# ---------------------------------------------------------------------------

# The system prompt:
# - Positions the model as a precise detector (conservative, not trigger-happy).
# - Defines the task output contract (JSON only).
# - States the precision-over-recall mandate explicitly.
# - Explicitly calls out the T-201 false-positive class: plain statements,
#   plans, and decisions are NOT walls.
# - Instructs the model to use high confidence only for unambiguous cases and
#   to prefer ``none`` when uncertain.
_SYSTEM_PROMPT = (
    "You are a precise conversation-wall detector for an ambient AI assistant. "
    "A 'wall' is a moment where the conversation is genuinely stuck or blocked "
    "and the assistant could help. "
    "Be CONSERVATIVE: only flag a wall when you are confident one exists. "
    "Statements, decisions, plans, and declarations are NOT walls even if they "
    "contain question words or wishful language (e.g. 'we'll send the PR in 10 "
    "minutes' is a plan, not a wall; 'I think we should go with option A' is a "
    "decision, not a wall). "
    "When in doubt, return category 'none' with is_wall false. "
    "Reserve confidence >= 0.80 for clear, unambiguous cases. "
    "Reply with ONLY a JSON object — no prose, no markdown fences."
)

# The user message template.
# - Supplies transcript + summary.
# - Defines each wall category with a positive example and a negative example
#   (the negative example is the key precision tool — it tells the model what
#   does NOT qualify).
# - Puts the JSON schema on a single line immediately before the output
#   instruction (proximity to the instruction improves compliance on 3B).
# The user message is assembled from parts to keep source lines ≤ 100 chars
# while preserving the exact prompt the model receives (the line-break in the
# Python source does not add a newline to the rendered prompt string).
_CATEGORY_DEFINITIONS = (
    "Detect whether the conversation has hit a wall. A wall is ONE of:\n"
    "- unanswered_question: someone asked a question that nobody answered"
    ' (e.g. "what time does the keynote start?" with no answer).'
    " NOT a wall if the question is rhetorical or the speaker is thinking aloud.\n"
    "- factual_gap: someone expressed that they don't know or can't remember a fact"
    ' (e.g. "I don\'t remember which date we picked", "what was the conference name?").'
    " NOT a wall if the speaker is summarising what they do know.\n"
    "- stuck_point: the conversation is looping, stalled, or going in circles"
    ' (e.g. "we keep coming back to the same problem").'
    " NOT a wall if they're just reviewing a completed decision.\n"
    "- explicit_ask: someone expressed a wish or desire for information they don't have"
    ' (e.g. "I wish I knew how long the flight is", "if only we knew the budget").'
    " Must be an expressed WISH or DESIRE, not a statement or plan.\n"
    "- none: no wall — the conversation is proceeding normally,"
    " making a decision, or discussing something clearly."
)

_JSON_SCHEMA_LINE = (
    'Reply with ONLY this JSON (no other text): {"is_wall": bool, "category":'
    ' "unanswered_question"|"factual_gap"|"stuck_point"|"explicit_ask"|"none",'
    ' "confidence": 0.0-1.0,'
    ' "offer": "one natural sentence Jarvis would say to offer help,'
    ' or empty string if no wall"}'
)

# The header/footer parts of the user message use {transcript} / {summary}
# placeholders, but _JSON_SCHEMA_LINE contains literal JSON braces — so the
# full user text is assembled with direct string concatenation in
# _build_messages rather than via a single .format() call (which would mis-
# interpret the JSON braces as format placeholders and raise KeyError).
_USER_HEADER = "TRANSCRIPT (most recent lines):\n{transcript}\n\nCURRENT SUMMARY:\n{summary}\n\n"

# Max tokens to generate. The T-201 spike measured ~366 ms median latency at
# max_tokens=100–120 on this M5 Pro. The JSON output is small (typically
# 30–50 tokens) so 120 gives enough headroom for any verbosity in the offer.
_MAX_TOKENS = 120

# Regex that strips markdown code fences if the model wraps its JSON output.
# Handles ```json ... ``` and ``` ... ``` variants.
_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class QwenWallBackend:
    """The real on-device wall detector backed by Qwen2.5-3B/MLX (T-203).

    Implements the frozen ``WallBackend`` Protocol from
    :mod:`jarvis.core.wall_detector`:

        ``detect_wall(transcript: str, summary: str) -> WallVerdict``

    The backend takes the :class:`~jarvis.ml.qwen.QwenModel` loader via
    constructor injection so the same model instance can be shared with
    :class:`~jarvis.ml.summarizer.QwenSummarizerBackend` (T-202) and the ~2 GB
    weights are never double-loaded.

    Args:
        model: the shared :class:`~jarvis.ml.qwen.QwenModel` loader.  In tests,
            pass a stub that records calls and returns canned JSON strings.  In
            production, pass the single ``QwenModel()`` instance wired at
            startup (the same one given to ``QwenSummarizerBackend``).
        max_tokens: the generation budget (default 120 — fits the JSON output
            and offer sentence with margin; callers rarely need to override).
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
        fields, unknown ``category`` value, out-of-range ``confidence`` — is
        caught and returns :meth:`~jarvis.types.WallVerdict.none` rather than
        raising.  The caller (``WallDetector``) never sees an exception from
        the model path.

        Args:
            transcript: the current rolling-window transcript, rendered as
                ``"Speaker: text"`` lines (the format
                :meth:`~jarvis.core.rolling_window.RollingWindow.transcript`
                produces).
            summary: the current living summary text (from
                :attr:`~jarvis.core.living_summary.LivingSummary.text`).

        Returns:
            A :class:`~jarvis.types.WallVerdict` with ``is_wall``,
            ``category``, ``confidence`` (raw, not thresholded), and ``offer``.
            Returns :meth:`~jarvis.types.WallVerdict.none` on any error.
        """
        messages = _build_messages(transcript, summary)
        raw = self._model.generate(messages, max_tokens=self._max_tokens)
        return _parse_verdict(raw)


# ---------------------------------------------------------------------------
# Internal helpers (exported for testing — tests assert on the message list
# and JSON parsing directly without needing a real model)
# ---------------------------------------------------------------------------


def _build_messages(transcript: str, summary: str) -> list[dict[str, str]]:
    """Build the chat-template message list for the detect_wall task.

    Separated from :class:`QwenWallBackend` so unit tests can assert on the
    exact messages without instantiating a model.  This is the construction
    that is mandated to use the chat template (via ``QwenModel.generate``)
    rather than a raw string prompt.

    Args:
        transcript: the rolling-window transcript text (may be empty).
        summary: the current living summary (may be empty).

    Returns:
        A two-element list: ``[{"role": "system", ...}, {"role": "user", ...}]``.
    """
    # Build the user text with direct string concatenation — NOT .format() on
    # the whole template, because _JSON_SCHEMA_LINE contains literal JSON
    # braces that would be misinterpreted as format placeholders.
    header = _USER_HEADER.format(
        transcript=transcript.strip() or "(no transcript yet)",
        summary=summary.strip() or "(no summary yet)",
    )
    user_text = header + _CATEGORY_DEFINITIONS + "\n\n" + _JSON_SCHEMA_LINE
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]


def _parse_verdict(raw: str) -> WallVerdict:
    """Parse the model's raw text output into a :class:`~jarvis.types.WallVerdict`.

    Handles:
    - Clean JSON output (the expected case).
    - JSON wrapped in markdown code fences (````` ```json ... ``` `````)
    - Prose before/after the JSON object (extracts the first ``{...}`` block).
    - Malformed JSON, missing fields, invalid enum values → falls back to
      :meth:`~jarvis.types.WallVerdict.none`.

    The confidence value is clamped to ``[0.0, 1.0]`` defensively.  The
    ``is_wall`` / ``category`` / ``offer`` invariants (``NONE`` iff
    ``¬is_wall``; ``offer`` is ``""`` for a non-wall) are enforced here —
    if the model returns ``is_wall: false`` with a non-``none`` category (or
    vice-versa), the output is normalized to maintain them.

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

    # Extract and validate fields — any missing or wrong-typed field → none.
    try:
        is_wall = bool(data["is_wall"])
        raw_category = str(data.get("category", "none"))
        confidence = float(data.get("confidence", 0.0))
        offer = str(data.get("offer", ""))
    except (KeyError, TypeError, ValueError):
        return WallVerdict.none()

    # Clamp confidence to [0.0, 1.0] defensively.
    confidence = max(0.0, min(1.0, confidence))

    # Coerce the category string into the StrEnum. Unknown values → none.
    try:
        category = WallCategory(raw_category)
    except ValueError:
        return WallVerdict.none()

    # Enforce the invariants:
    #   - NONE iff ¬is_wall
    #   - offer is "" for a non-wall
    if not is_wall:
        return WallVerdict(
            is_wall=False,
            category=WallCategory.NONE,
            confidence=confidence,
            offer="",
        )

    # is_wall is True but model said category "none" — treat as no wall.
    if category is WallCategory.NONE:
        return WallVerdict.none()

    return WallVerdict(
        is_wall=True,
        category=category,
        confidence=confidence,
        offer=offer,
    )
