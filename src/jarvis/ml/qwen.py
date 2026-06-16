"""Shared Qwen2.5/MLX model loader (T-202).

A single lazily-loaded ``(model, tokenizer)`` instance that both the summarizer
backend (T-202) and the wall-detection backend (T-203) share.  Loading the ~2 GB
weights once and injecting the same :class:`QwenModel` into both backends is the
shared-loader design chosen in ``docs/ml/working-notes.md``.

The model is **not** loaded when this module is imported — only on the first call
to :meth:`QwenModel.generate`.  This mirrors the lazy-import discipline used by
:class:`~jarvis.audio.mic_source.MlxWhisperTranscriber` (``mlx_whisper``) and
:class:`~jarvis.audio.vad.SileroFrameClassifier` (``torch``), so importing
:mod:`jarvis.ml` never pulls MLX or the weights into memory — and the default
``uv run pytest`` suite stays model-free.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Defaults (frozen by T-201 spike — DECISIONS.md 2026-06-15)
# ---------------------------------------------------------------------------

#: The MLX-community repo chosen by the T-201 spike.  Do NOT change without a
#: new spike (7B-Instruct-4bit is the documented escalation path if 3B precision
#: proves insufficient after T-203 prompt work).
DEFAULT_MODEL_PATH = "mlx-community/Qwen2.5-3B-Instruct-4bit"

#: The maximum tokens the loader passes to ``mlx_lm.generate`` when no explicit
#: ``max_tokens`` is given.  Callers (summarizer, wall backend) always supply
#: their own values — this is the safety fallback.
_DEFAULT_MAX_TOKENS = 128


class QwenModel:
    """Lazily-loaded Qwen2.5-3B/MLX instance shared between the SLM backends.

    Construct one instance and inject it into every backend that needs the model
    (``QwenSummarizerBackend`` for T-202, ``QwenWallBackend`` for T-203).  The
    underlying ``(model, tokenizer)`` pair is loaded on the first :meth:`generate`
    call and cached for all subsequent calls — the ~2 GB weights are never loaded
    twice in one process.

    The caller is responsible for construction and injection; the model is never
    instantiated as a hidden global, in keeping with the project's injected-backend
    discipline (module map §"Cross-cutting design constraints" #2).

    Args:
        model_path: the HuggingFace repo or local path for the MLX-converted
            Qwen2.5-3B-Instruct-4bit weights.  Defaults to the T-201-selected
            ``mlx-community/Qwen2.5-3B-Instruct-4bit`` (DECISIONS.md 2026-06-15).
            Override in tests via injection — no monkeypatching needed.
    """

    def __init__(self, model_path: str = DEFAULT_MODEL_PATH) -> None:
        self._model_path = model_path
        # Both loaded lazily on first generate() call.
        self._model: Any | None = None
        self._tokenizer: Any | None = None
        self._generate_fn: Any | None = None  # mlx_lm.generate callable

    # ------------------------------------------------------------------
    # Internal loader
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Load model + tokenizer from ``mlx_lm`` if not yet done.

        The ``import mlx_lm`` lives here — inside the method body — so that
        merely importing :mod:`jarvis.ml.qwen` (or :mod:`jarvis.ml`) never
        triggers an MLX load.  Tests inject a fake loader via constructor
        injection or monkeypatch the private attributes directly.
        """
        if self._model is not None:
            return  # already loaded

        from mlx_lm import generate, load  # heavy; loaded only on first real use

        self._model, self._tokenizer = load(self._model_path)
        self._generate_fn = generate

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def generate(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> str:
        """Apply the chat template and generate a text completion.

        Uses ``tokenizer.apply_chat_template`` with ``add_generation_prompt=True``
        — this is **mandatory** for Qwen2.5-Instruct models (DECISIONS.md
        2026-06-15 / ``docs/ml/working-notes.md``): raw string prompts produce
        repetition/degradation and ~2× higher latency compared with the template.

        Args:
            messages: a list of ``{"role": ..., "content": ...}`` dicts in
                OpenAI-style format.  Typically one ``"system"`` message followed
                by one ``"user"`` message.  The role strings ``"system"`` and
                ``"user"`` are what Qwen2.5-Instruct expects.
            max_tokens: the maximum number of tokens to generate.  Callers
                should pass task-specific values (e.g. 80 for summarize, 120 for
                detect_wall) — the default is a conservative fallback only.

        Returns:
            The generated text with leading/trailing whitespace stripped.
        """
        self._ensure_loaded()

        # Build the prompt string via the chat template.  tokenize=False returns
        # a string (not token ids); add_generation_prompt=True appends the
        # assistant turn opener so the model continues, not echoes the prompt.
        prompt = self._tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )

        result = self._generate_fn(
            self._model,
            self._tokenizer,
            prompt=prompt,
            max_tokens=max_tokens,
            verbose=False,
        )
        # mlx_lm.generate returns the generated text as a string (the new tokens
        # only, not the prompt) — strip whitespace and return.
        return str(result).strip()
