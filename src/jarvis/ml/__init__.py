"""``jarvis.ml`` — local SLM inference package (Phase 2).

Provides the Qwen2.5/MLX-backed implementations of the ambient-half model seams:

* :class:`~jarvis.ml.qwen.QwenModel` — shared loader; loads ``(model, tokenizer)``
  **once**, lazily, from ``mlx-community/Qwen2.5-3B-Instruct-4bit``.  Designed to
  serve both :class:`~jarvis.ml.summarizer.QwenSummarizerBackend` (T-202) and the
  forthcoming :class:`~jarvis.ml.wall.QwenWallBackend` (T-203) so the ~2 GB weights
  are never double-loaded.

* :class:`~jarvis.ml.summarizer.QwenSummarizerBackend` — a thin adapter that takes
  the injected ``QwenModel`` and implements the frozen
  ``SummarizerBackend.summarize(transcript, prev) -> str`` seam.

Importing this package — or either submodule — **never** loads MLX or downloads
model weights.  The heavy ``mlx_lm`` import lives inside :meth:`QwenModel.load`,
called only on the first real inference call.  This mirrors the lazy-import
discipline in :mod:`jarvis.audio.mic_source` (``mlx_whisper``) and
:mod:`jarvis.audio.vad` (``torch``).
"""

from jarvis.ml.qwen import QwenModel
from jarvis.ml.summarizer import QwenSummarizerBackend

__all__ = ["QwenModel", "QwenSummarizerBackend"]
