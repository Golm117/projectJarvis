"""``QwenSummarizerBackend`` — the real ``SummarizerBackend`` on Qwen2.5/MLX (T-202).

This is the Phase-2 fill of the frozen ``SummarizerBackend`` seam declared in
:mod:`jarvis.core.living_summary`.  It replaces the ``HeuristicSummarizerBackend``
behind the same ``summarize(transcript, prev) -> str`` signature with **no change**
to ``LivingSummary`` or any other core module.

Design decisions (``docs/ml/working-notes.md`` + DECISIONS.md 2026-06-15):

* The heavy model lives in the injected :class:`~jarvis.ml.qwen.QwenModel`; this
  backend is a thin adapter with no model logic of its own.  T-203 reuses the
  **same** ``QwenModel`` instance for wall detection — single load, no weight
  duplication.
* ``tokenizer.apply_chat_template`` is used via ``QwenModel.generate``; raw string
  prompts are explicitly forbidden (they degrade quality and inflate latency ~2×
  on Qwen2.5-Instruct models).
* The prompt is designed to produce a **delta-update** ("redraw only changed
  pixels"): given the current ``transcript`` and the ``prev`` summary, return a
  concise refreshed summary.  No facts may be added that aren't in the transcript.
* ``max_tokens=80``: the spike measured ~250 ms median latency at this budget;
  it fits a 1–3 sentence summary and leaves headroom within the 2 s offer budget.
"""

from __future__ import annotations

from jarvis.ml.qwen import QwenModel

# ---------------------------------------------------------------------------
# Prompt constants
# ---------------------------------------------------------------------------

# The system prompt positions the model as a concise note-taker and explicitly
# forbids hallucination (the main failure mode for summarize) while encouraging
# delta-style updates from the previous summary.  Kept brief so the model has
# more of the max_tokens budget for the actual summary.
_SYSTEM_PROMPT = (
    "You are a concise meeting note-taker. "
    "Summarize what is being discussed in 1-3 sentences. "
    "Use only facts stated in the transcript — do not add information. "
    "If a previous summary is provided, update it to reflect the latest conversation."
)

# The user message template.  Blank prev is handled gracefully — the model is
# instructed to just summarize the transcript when there's nothing to update.
_USER_TEMPLATE = """\
TRANSCRIPT:
{transcript}

PREVIOUS SUMMARY:
{prev}

Write an updated summary (1-3 sentences, no preamble, no explanation):\
"""

# Max tokens to generate.  The spike found ~250 ms median latency at this budget
# with 3B-Instruct-4bit (docs/ml/qwen-coexistence-spike.md §joint-budget).
# A tighter limit is fine — the model typically finishes a summary in 30–60 tokens.
_MAX_TOKENS = 80


class QwenSummarizerBackend:
    """The real on-device summarizer backed by Qwen2.5-3B/MLX (T-202).

    Implements the frozen ``SummarizerBackend`` Protocol from
    :mod:`jarvis.core.living_summary`:

        ``summarize(transcript: str, prev: str) -> str``

    The backend takes the :class:`~jarvis.ml.qwen.QwenModel` loader via
    constructor injection so the same model instance can be shared with
    :class:`~jarvis.ml.wall.QwenWallBackend` (T-203) and the ~2 GB weights are
    never double-loaded.

    Args:
        model: the shared :class:`~jarvis.ml.qwen.QwenModel` loader.  In tests,
            pass a stub that records calls and returns canned output.  In
            production, pass the single ``QwenModel()`` instance wired at startup.
        max_tokens: the generation budget (default 80 — fits a 1–3 sentence
            summary with margin; callers rarely need to override).
    """

    def __init__(
        self,
        model: QwenModel,
        max_tokens: int = _MAX_TOKENS,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens

    def summarize(self, transcript: str, prev: str) -> str:
        """Produce an updated living summary from the current window + previous text.

        Builds a system/user chat-template message pair and calls
        :meth:`~jarvis.ml.qwen.QwenModel.generate`.  The model is instructed to
        produce a **delta-update**: incorporate the transcript content into (or
        replace) the previous summary, keeping it 1–3 sentences and grounded only
        in what was said.

        Args:
            transcript: the current rolling-window transcript, rendered as
                ``"Speaker: text"`` lines (the format :meth:`RollingWindow.transcript`
                produces).
            prev: the standing summary text from the last ``LivingSummary`` refresh
                (empty string on first call — the model handles this gracefully).

        Returns:
            The updated summary text.
        """
        messages = _build_messages(transcript, prev)
        return self._model.generate(messages, max_tokens=self._max_tokens)


# ---------------------------------------------------------------------------
# Internal helpers (exported for testing — tests assert on the message list
# directly without needing a real model)
# ---------------------------------------------------------------------------


def _build_messages(transcript: str, prev: str) -> list[dict[str, str]]:
    """Build the chat-template message list for the summarize task.

    Separated from :class:`QwenSummarizerBackend` so unit tests can assert on
    the exact messages without instantiating a model.  This is the construction
    that is mandated to use the chat template (via ``QwenModel.generate``) rather
    than a raw string prompt.

    Args:
        transcript: the rolling-window transcript text.
        prev: the previous summary (may be empty on first call).

    Returns:
        A two-element list: ``[{"role": "system", ...}, {"role": "user", ...}]``.
    """
    user_text = _USER_TEMPLATE.format(
        transcript=transcript.strip() or "(no transcript yet)",
        prev=prev.strip() or "(none — this is the first summary)",
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]
