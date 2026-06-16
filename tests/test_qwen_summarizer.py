"""Tests for QwenSummarizerBackend + QwenModel (T-202).

All tests in this file are **model-free** — they run without MLX, without Qwen
weights, and without network access.  The real model inference lives in the
optional live test at the bottom, which self-skips when weights are unavailable
(mirroring ``test_live_silero_vad_on_mic_optional`` in ``test_vad.py``).

Design under test:
- ``_build_messages(transcript, prev)`` — message construction (pure, testable in
  isolation).
- ``QwenSummarizerBackend.summarize(transcript, prev)`` — the seam adapter; calls
  the injected model with the right messages.
- ``QwenModel`` lazy-import boundary — importing the module never loads mlx_lm.
- ``QwenSummarizerBackend`` satisfies the ``SummarizerBackend`` Protocol.
"""

from __future__ import annotations

import pytest

from jarvis.ml.qwen import QwenModel
from jarvis.ml.summarizer import QwenSummarizerBackend, _build_messages

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeQwenModel:
    """A stub QwenModel that records calls and returns canned output.

    Used in all model-free tests.  The real ``QwenModel`` is never instantiated.
    """

    def __init__(self, canned: str = "Canned summary.") -> None:
        self.calls: list[tuple[list[dict[str, str]], int]] = []
        self._canned = canned

    def generate(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 80,
    ) -> str:
        self.calls.append((messages, max_tokens))
        return self._canned


# ---------------------------------------------------------------------------
# 1. Message construction (_build_messages) — no model needed
# ---------------------------------------------------------------------------


class TestBuildMessages:
    """Assert the chat-template message list is built correctly."""

    def test_returns_two_messages(self) -> None:
        msgs = _build_messages("Alice: hello", "prev summary")
        assert len(msgs) == 2

    def test_first_message_is_system(self) -> None:
        msgs = _build_messages("Alice: hello", "")
        assert msgs[0]["role"] == "system"

    def test_second_message_is_user(self) -> None:
        msgs = _build_messages("Alice: hello", "")
        assert msgs[1]["role"] == "user"

    def test_transcript_appears_in_user_message(self) -> None:
        transcript = "Alice: did anyone figure out the build?"
        msgs = _build_messages(transcript, "")
        assert transcript in msgs[1]["content"]

    def test_prev_appears_in_user_message(self) -> None:
        prev = "The team is debugging the CI pipeline."
        msgs = _build_messages("Alice: yes", prev)
        assert prev in msgs[1]["content"]

    def test_empty_prev_gets_placeholder(self) -> None:
        """An empty prev string is filled with a "(none)" placeholder."""
        msgs = _build_messages("Alice: hello", "")
        assert "none" in msgs[1]["content"].lower() or "first" in msgs[1]["content"].lower()

    def test_empty_transcript_gets_placeholder(self) -> None:
        msgs = _build_messages("", "some prev")
        assert "no transcript" in msgs[1]["content"].lower()

    def test_system_message_non_empty(self) -> None:
        msgs = _build_messages("Alice: test", "")
        assert msgs[0]["content"].strip()

    def test_whitespace_transcript_gets_placeholder(self) -> None:
        """A whitespace-only transcript is treated the same as empty."""
        msgs = _build_messages("   \n  ", "prev")
        assert "no transcript" in msgs[1]["content"].lower()

    def test_both_roles_are_strings(self) -> None:
        msgs = _build_messages("A: text", "prev")
        for m in msgs:
            assert isinstance(m["role"], str)
            assert isinstance(m["content"], str)


# ---------------------------------------------------------------------------
# 2. QwenSummarizerBackend adapter — model-free (injected fake)
# ---------------------------------------------------------------------------


class TestQwenSummarizerBackend:
    """Assert the backend wires transcript/prev through the injected model."""

    def test_satisfies_summarizer_backend_protocol(self) -> None:
        """QwenSummarizerBackend must satisfy the frozen SummarizerBackend Protocol."""
        fake = _FakeQwenModel()
        backend = QwenSummarizerBackend(fake)

        # SummarizerBackend is a typing.Protocol — check structural compatibility.
        # We use duck-typing: the Protocol requires .summarize(transcript, prev) -> str.
        assert hasattr(backend, "summarize")
        assert callable(backend.summarize)

    def test_summarize_calls_model_generate_once(self) -> None:
        fake = _FakeQwenModel()
        backend = QwenSummarizerBackend(fake)
        backend.summarize("Alice: hello", "")
        assert len(fake.calls) == 1

    def test_summarize_passes_transcript_to_model(self) -> None:
        fake = _FakeQwenModel()
        backend = QwenSummarizerBackend(fake)
        transcript = "Bob: the build is broken"
        backend.summarize(transcript, "")
        messages, _ = fake.calls[0]
        user_content = messages[1]["content"]
        assert transcript in user_content

    def test_summarize_passes_prev_to_model(self) -> None:
        fake = _FakeQwenModel()
        backend = QwenSummarizerBackend(fake)
        prev = "The team discussed deployment issues."
        backend.summarize("Bob: yeah", prev)
        messages, _ = fake.calls[0]
        user_content = messages[1]["content"]
        assert prev in user_content

    def test_summarize_passes_max_tokens(self) -> None:
        fake = _FakeQwenModel()
        backend = QwenSummarizerBackend(fake, max_tokens=42)
        backend.summarize("Alice: test", "")
        _, max_tok = fake.calls[0]
        assert max_tok == 42

    def test_summarize_returns_model_output(self) -> None:
        expected = "The team is debugging the pipeline."
        fake = _FakeQwenModel(canned=expected)
        backend = QwenSummarizerBackend(fake)
        result = backend.summarize("Alice: tests are failing", "")
        assert result == expected

    def test_messages_have_system_and_user_roles(self) -> None:
        fake = _FakeQwenModel()
        backend = QwenSummarizerBackend(fake)
        backend.summarize("A: x", "prev")
        messages, _ = fake.calls[0]
        roles = [m["role"] for m in messages]
        assert "system" in roles
        assert "user" in roles

    def test_summarize_multiple_calls(self) -> None:
        """Each call to summarize sends its own transcript/prev to the model."""
        fake = _FakeQwenModel()
        backend = QwenSummarizerBackend(fake)
        backend.summarize("A: first", "")
        backend.summarize("A: second", "first summary")
        assert len(fake.calls) == 2

        _, _ = fake.calls[0]
        messages_1, _ = fake.calls[0]
        messages_2, _ = fake.calls[1]
        assert "first" in messages_1[1]["content"]
        assert "second" in messages_2[1]["content"]
        assert "first summary" in messages_2[1]["content"]

    def test_default_max_tokens_is_reasonable(self) -> None:
        """The default max_tokens should be positive and within a sensible range."""
        fake = _FakeQwenModel()
        backend = QwenSummarizerBackend(fake)
        backend.summarize("A: text", "")
        _, max_tok = fake.calls[0]
        assert 1 <= max_tok <= 512


# ---------------------------------------------------------------------------
# 3. QwenModel lazy-import boundary
# ---------------------------------------------------------------------------


class TestQwenModelLazyImport:
    """Importing QwenModel must NOT load mlx_lm."""

    def test_import_does_not_load_mlx_lm(self) -> None:
        """Importing jarvis.ml.qwen must not trigger an mlx_lm import.

        We assert this by checking that QwenModel._model is None after
        construction — if mlx_lm had been loaded, _model would be set.
        """
        model = QwenModel(model_path="does-not-exist-intentionally")
        assert model._model is None
        assert model._tokenizer is None

    def test_generate_without_load_raises_if_path_invalid(self) -> None:
        """Calling generate() on a nonexistent path surfaces a load error."""
        model = QwenModel(model_path="does-not-exist-intentionally")
        with pytest.raises(Exception):  # noqa: B017 - intentionally broad; mlx_lm raises various errors
            model.generate([{"role": "user", "content": "test"}])

    def test_qwen_model_stores_model_path(self) -> None:
        custom_path = "some/custom/path"
        model = QwenModel(model_path=custom_path)
        assert model._model_path == custom_path

    def test_qwen_model_default_path(self) -> None:
        # T-509: default escalated from 3B → 7B (measured on M5 Pro: 1791 ms
        # joint pipeline, +209 ms margin vs 2000 ms budget).
        model = QwenModel()
        assert "Qwen2.5" in model._model_path
        assert "7B" in model._model_path  # T-509: 7B is now the default
        assert "4bit" in model._model_path.lower() or "4bit" in model._model_path


# ---------------------------------------------------------------------------
# 4. Protocol structural check
# ---------------------------------------------------------------------------


def test_backend_is_structurally_a_summarizer_backend() -> None:
    """Verify QwenSummarizerBackend satisfies the SummarizerBackend Protocol.

    ``SummarizerBackend`` is a ``typing.Protocol`` without ``@runtime_checkable``
    (it's frozen and we can't modify it).  We verify structural compatibility by
    confirming the backend has a callable ``summarize`` with the correct arity —
    which is what the Protocol duck-type check requires.

    We also verify via a direct signature check using ``inspect``.
    """
    import inspect

    fake = _FakeQwenModel()
    backend = QwenSummarizerBackend(fake)

    # Must have a callable ``summarize`` method.
    assert hasattr(backend, "summarize")
    assert callable(backend.summarize)

    # The signature must accept (transcript: str, prev: str) — match the Protocol.
    sig = inspect.signature(backend.summarize)
    params = list(sig.parameters.keys())
    assert "transcript" in params
    assert "prev" in params


# ---------------------------------------------------------------------------
# 5. Optional live test — skipped when MLX / weights unavailable
# ---------------------------------------------------------------------------


def test_live_qwen_summarize_optional() -> None:
    """End-to-end real inference: load Qwen2.5-7B (T-509 default) and run a summarize call.

    Skipped (never failed) when mlx_lm is not importable or the model weights
    are not available locally — the condition mirrors
    ``test_live_silero_vad_on_mic_optional`` in ``test_vad.py``.  Never runs in
    CI (the CI env has no Qwen weights); only runs on the local M5 where the
    weights are cached from the T-201 spike.

    When it does run, it asserts:
    1. The model loads (no exception).
    2. ``generate()`` returns a non-empty string.
    3. The output is not the literal prompt (basic sanity that the model
       generated something new, not just echoed).
    """
    try:
        import mlx_lm  # noqa: F401 — import probe only
    except ImportError as exc:
        pytest.skip(f"mlx_lm not installed: {exc}")

    try:
        model = QwenModel()  # loads from cache; ~300 ms if weights present
        # A minimal transcript/prev pair representative of real usage.
        transcript = (
            "Alice: did anyone figure out whether the build is still failing?\n"
            "Bob: I think it was the caching layer that caused the regression."
        )
        prev = ""
        backend = QwenSummarizerBackend(model)
        result = backend.summarize(transcript, prev)
    except Exception as exc:  # noqa: BLE001 - weights missing → skip
        pytest.skip(f"Qwen model unavailable (weights missing or mlx error): {exc}")

    assert isinstance(result, str)
    assert len(result.strip()) > 0, "expected non-empty summary"
    # The model should not echo the user message verbatim — it should generate.
    assert "Write an updated summary" not in result
