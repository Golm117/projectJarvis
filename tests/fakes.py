"""Fakes for the backend / adapter seams (T-009).

These stand in for the swappable seams documented in
``docs/architecture/module-map.md`` §"The I/O adapter seams". Every fake follows
the same two-part shape:

* **Preset** what it returns (a fixed value, or a per-call script).
* **Record** what it was called with, so a test can assert on the arguments —
  testing *external behavior* (did the module call the seam, and with what)
  rather than the module's internals.

Seam → fake map (kept in lockstep with the module map):

    SummarizerBackend.summarize(transcript, prev) -> str   →  FakeSummarizer
    WallBackend.detect_wall(transcript, summary) -> Verdict →  FakeWallBackend
    EngagedResponder.respond(handoff) -> str               →  FakeResponder
    VoiceOutput.speak(text) -> None                        →  FakeVoice

Sequencing note (T-009 → resolved T-005): ``WallVerdict`` was frozen in T-005
(`jarvis.types.WallVerdict`, with the `WallCategory` enum). ``FakeWallBackend``
now returns the **real** ``WallVerdict`` — the T-009 ``WallVerdictLike`` stand-in
is retired and ``WallVerdictLike`` is kept as a thin alias of ``WallVerdict`` for
any test that still imports the old name. The ``wall``/``no_wall`` helpers build
real verdicts.

Beyond that one real type, the fakes import as little from ``jarvis`` as
possible: they encode the *seam shape*, not any module's internals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jarvis.types import WallCategory, WallVerdict

# ---------------------------------------------------------------------------
# WallVerdict was frozen in T-005 (resolving the T-009 TODO). Keep the old
# ``WallVerdictLike`` name as an alias so any test importing it still works; new
# tests use ``jarvis.types.WallVerdict`` directly via the ``wall``/``no_wall``
# helpers below.
# ---------------------------------------------------------------------------
WallVerdictLike = WallVerdict

WALL_CATEGORIES = tuple(c.value for c in WallCategory)


@dataclass
class _Call:
    """A recorded invocation: positional args and keyword args, as passed."""

    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class _Recorder:
    """Mixin: record every invocation and expose convenient accessors."""

    def __init__(self) -> None:
        self.calls: list[_Call] = []

    def _record(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(_Call(args=args, kwargs=kwargs))

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def called(self) -> bool:
        return bool(self.calls)

    @property
    def last_call(self) -> _Call:
        if not self.calls:
            raise AssertionError("fake was never called")
        return self.calls[-1]

    def reset(self) -> None:
        self.calls.clear()


# ---------------------------------------------------------------------------
# FakeSummarizer  →  SummarizerBackend.summarize(transcript, prev) -> str
# ---------------------------------------------------------------------------
class FakeSummarizer(_Recorder):
    """Stands in for LivingSummary's injected SummarizerBackend.

    Preset behavior in any of three ways (checked in this order):
      * ``returns=[...]``      — a script consumed one item per call.
      * ``return_value="..."`` — a single fixed string for every call.
      * default                — a deterministic echo embedding the call index,
                                 so a test can prove a *new* summary was produced.
    """

    def __init__(
        self,
        return_value: str | None = None,
        returns: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._return_value = return_value
        self._script = list(returns) if returns is not None else None

    def summarize(self, transcript: str, prev: str) -> str:
        self._record(transcript, prev)
        if self._script is not None:
            if not self._script:
                raise AssertionError("FakeSummarizer script exhausted")
            return self._script.pop(0)
        if self._return_value is not None:
            return self._return_value
        return f"summary#{self.call_count}"

    # --- assertion helpers ---
    @property
    def transcripts(self) -> list[str]:
        """The ``transcript`` argument of each call, in order."""
        return [c.args[0] for c in self.calls]

    @property
    def prev_summaries(self) -> list[str]:
        """The ``prev`` argument of each call, in order."""
        return [c.args[1] for c in self.calls]


# ---------------------------------------------------------------------------
# FakeWallBackend  →  WallBackend.detect_wall(transcript, summary) -> WallVerdict
# ---------------------------------------------------------------------------
class FakeWallBackend(_Recorder):
    """Stands in for WallDetector's injected WallBackend.

    Returns canned verdicts you can script per call:
      * ``verdicts=[v1, v2, ...]`` — one verdict per call, in order.
      * ``verdict=v``              — the same verdict for every call.
      * default                    — a 'none' verdict (stay silent).

    Verdicts may be ``WallVerdictLike`` (built via the ``wall``/``no_wall``
    helpers below) or, once T-005 lands, the real ``WallVerdict`` — the backend
    just hands back whatever it was given.
    """

    def __init__(
        self,
        verdict: Any | None = None,
        verdicts: list[Any] | None = None,
    ) -> None:
        super().__init__()
        self._verdict = verdict
        self._script = list(verdicts) if verdicts is not None else None

    def detect_wall(self, transcript: str, summary: str) -> Any:
        self._record(transcript, summary)
        if self._script is not None:
            if not self._script:
                raise AssertionError("FakeWallBackend script exhausted")
            return self._script.pop(0)
        if self._verdict is not None:
            return self._verdict
        return WallVerdict.none()

    # --- assertion helpers ---
    @property
    def transcripts(self) -> list[str]:
        return [c.args[0] for c in self.calls]

    @property
    def summaries(self) -> list[str]:
        return [c.args[1] for c in self.calls]


def wall(
    category: str | WallCategory,
    confidence: float,
    offer: str = "Want me to help with that?",
) -> WallVerdict:
    """Build a positive ``WallVerdict`` for scripting FakeWallBackend.

    ``category`` accepts either a :class:`~jarvis.types.WallCategory` member or
    its wire string (e.g. ``"factual_gap"``) — both are converted to the enum.
    ``"none"`` is rejected: this helper builds *walls* (use ``no_wall()``).
    """
    cat = WallCategory(category)
    if cat is WallCategory.NONE:
        raise ValueError("not a wall category: WallCategory.NONE (use no_wall())")
    return WallVerdict(is_wall=True, category=cat, confidence=confidence, offer=offer)


def no_wall() -> WallVerdict:
    """Build the 'no wall' verdict (stay silent)."""
    return WallVerdict.none()


# ---------------------------------------------------------------------------
# FakeResponder  →  EngagedResponder.respond(handoff) -> str
# ---------------------------------------------------------------------------
class FakeResponder(_Recorder):
    """Stands in for the engaged-path EngagedResponder (the cloud answer seam).

    Returns a canned line; records the handoff it was asked to respond to so a
    test can assert *what context crossed the boundary* (trigger reason, summary,
    excerpt) without invoking Claude.
    """

    def __init__(self, return_value: str = "Yes? I've been following along.") -> None:
        super().__init__()
        self._return_value = return_value

    def respond(self, handoff: Any) -> str:
        self._record(handoff)
        return self._return_value

    @property
    def handoffs(self) -> list[Any]:
        """Each handoff passed to ``respond``, in order."""
        return [c.args[0] for c in self.calls]

    @property
    def last_handoff(self) -> Any:
        return self.last_call.args[0]


# ---------------------------------------------------------------------------
# FakeVoice  →  VoiceOutput.speak(text) -> None
# ---------------------------------------------------------------------------
@dataclass
class FakeVoice(_Recorder):
    """Stands in for the VoiceOutput seam (ElevenLabs in production).

    A no-op that records every line it was asked to speak, so a test can assert
    Jarvis spoke (and what) without producing audio.
    """

    spoken: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _Recorder.__init__(self)

    def speak(self, text: str) -> None:
        self._record(text)
        self.spoken.append(text)

    @property
    def last_spoken(self) -> str:
        if not self.spoken:
            raise AssertionError("FakeVoice was never asked to speak")
        return self.spoken[-1]
