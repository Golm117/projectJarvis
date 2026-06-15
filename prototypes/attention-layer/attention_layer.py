"""
Project Jarvis — Attention Layer prototype.

A runnable foundation for PRD section 01 (Conversation Initiation). It implements
the *platform-agnostic core* of the attention layer described in the PRD:

  - a Rolling Transcription Window (bounded, sliding)
  - a Living Summary updated on a delta basis (only when the topic shifts)
  - Wall Detection (notices the conversation needs help)
  - two Initiation Paths:
        A) explicit wake word ("jarvis ...")  -> Summon
        B) proactive spoken interjection on a detected Wall
  - an Engagement Handoff (summary + trigger reason) at the boundary

The microphone is deliberately NOT here. Audio in / out is the hardware-abstraction
boundary (PRD NFR-5). This prototype feeds the core from text (a TranscriptSource)
so a real STT mic adapter can be swapped in later without touching the core logic.

Two runtime modes, auto-detected:
  - LIVE  : if ANTHROPIC_API_KEY is set AND the `anthropic` SDK is installed, the
            Living Summary and Wall Detection are produced by Claude.
  - MOCK  : otherwise, cheap local heuristics stand in, so the whole pipeline runs
            with zero setup. The architecture is identical; only the brain differs.

Run it:
    python3 attention_layer.py --demo          # scripted conversation, no typing
    python3 attention_layer.py                 # interactive: type "Speaker: text"

Model note: the ambient loop (summary + wall detection) uses a cheap, fast model
because the PRD requires low idle compute (NFR-3) — this is the cloud stand-in for
the on-device model of Phase 1. The engaged path uses the most capable model.
Both are constants below; change them in one line.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Iterable, Optional

# --- Models (see module docstring) ------------------------------------------
# Ambient = cheap/fast: stands in for the on-device Phase-1 model. Wall detection
# is essentially classification, which is exactly Haiku's lane.
AMBIENT_MODEL = "claude-haiku-4-5"
# Engaged = most capable: only invoked once Jarvis is actually helping.
ENGAGED_MODEL = "claude-opus-4-8"

# --- Tunable knobs (the PRD's "open questions" live here as config) ----------
WINDOW_MAX_UTTERANCES = 12          # rolling window size (count-bounded)
WINDOW_MAX_SECONDS = 120            # rolling window size (time-bounded)
TOPIC_SHIFT_THRESHOLD = 0.30        # Jaccard similarity below this => topic shift
MIN_UTTERANCES_FOR_SUMMARY = 3      # don't summarize a cold conversation
WALL_CONFIDENCE_TO_SPEAK = 0.70     # PRD FR-4.3: precision over recall
WAKE_WORD = "jarvis"


# ============================================================================
# Data types
# ============================================================================
@dataclass
class Utterance:
    speaker: str
    text: str
    ts: float = field(default_factory=time.monotonic)


@dataclass
class EngagementHandoff:
    """The boundary output of this section (PRD FR-5)."""
    trigger_reason: str          # "summon" | "wall:<category>"
    summary: str
    recent_excerpt: str
    detail: str = ""


# ============================================================================
# Hardware-abstraction boundary: where audio would enter
# ============================================================================
class TranscriptSource:
    """Yields Utterances. A real STT mic adapter would subclass this."""

    def utterances(self) -> Iterable[Utterance]:  # pragma: no cover - interface
        raise NotImplementedError


class ScriptedSource(TranscriptSource):
    """A canned conversation, for --demo. Lines are (speaker, text)."""

    def __init__(self, lines: list[tuple[str, str]], pace: float = 0.0):
        self.lines = lines
        self.pace = pace

    def utterances(self) -> Iterable[Utterance]:
        for speaker, text in self.lines:
            if self.pace:
                time.sleep(self.pace)
            yield Utterance(speaker=speaker, text=text)


class StdinSource(TranscriptSource):
    """Interactive: read 'Speaker: text' lines from the terminal."""

    def utterances(self) -> Iterable[Utterance]:
        print("Type lines as 'Speaker: text'. Say the wake word to summon. Ctrl-D to quit.\n")
        for raw in sys.stdin:
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            if ":" in line:
                speaker, text = line.split(":", 1)
                yield Utterance(speaker=speaker.strip() or "Speaker", text=text.strip())
            else:
                yield Utterance(speaker="Speaker", text=line.strip())


# ============================================================================
# Text helpers (shared by the mock heuristics and the shift detector)
# ============================================================================
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "so", "to", "of", "in",
    "on", "for", "with", "at", "by", "from", "is", "are", "was", "were", "be",
    "been", "it", "its", "this", "that", "these", "those", "i", "you", "we",
    "they", "he", "she", "do", "does", "did", "have", "has", "had", "will",
    "would", "can", "could", "should", "what", "how", "why", "when", "about",
    "just", "like", "really", "thing", "going", "get", "got",
}


def keywords(text: str) -> set[str]:
    words = re.findall(r"[a-z']+", text.lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ============================================================================
# Rolling Transcription Window (PRD FR-1)
# ============================================================================
class RollingWindow:
    def __init__(self, max_utterances: int, max_seconds: float):
        self._buf: Deque[Utterance] = deque(maxlen=max_utterances)
        self._max_seconds = max_seconds

    def add(self, u: Utterance) -> None:
        self._buf.append(u)
        self._evict_old(u.ts)

    def _evict_old(self, now: float) -> None:
        while self._buf and (now - self._buf[0].ts) > self._max_seconds:
            self._buf.popleft()

    def utterances(self) -> list[Utterance]:
        return list(self._buf)

    def transcript(self) -> str:
        return "\n".join(f"{u.speaker}: {u.text}" for u in self._buf)

    def keywords(self) -> set[str]:
        ks: set[str] = set()
        for u in self._buf:
            ks |= keywords(u.text)
        return ks


# ============================================================================
# LLM backend (live) with a mock fallback. Same interface either way.
# ============================================================================
class Backend:
    """Produces the Living Summary and Wall verdicts. Live or mock."""

    def __init__(self) -> None:
        self.live = False
        self._client = None
        key = os.environ.get("ANTHROPIC_API_KEY")
        if key:
            try:
                import anthropic  # noqa: F401
                self._anthropic = anthropic
                self._client = anthropic.Anthropic()
                self.live = True
            except Exception as exc:  # SDK missing or init failed
                print(f"[backend] anthropic SDK unavailable ({exc}); using MOCK mode.")
        # else: no key -> mock

    @property
    def mode(self) -> str:
        return "LIVE (Claude)" if self.live else "MOCK (heuristics)"

    # -- Living Summary (delta-updated by the caller; this just produces text) --
    def summarize(self, transcript: str, prev_summary: str) -> str:
        if not self.live:
            return self._mock_summarize(transcript, prev_summary)
        prompt = (
            "You maintain a single-paragraph living summary of an ongoing spoken "
            "conversation. Update it from the recent transcript. Keep it under 60 "
            "words. Capture the current topic, any open question, and anything "
            "unresolved. Output ONLY the summary, no preamble.\n\n"
            f"Previous summary:\n{prev_summary or '(none yet)'}\n\n"
            f"Recent transcript:\n{transcript}"
        )
        try:
            resp = self._client.messages.create(
                model=AMBIENT_MODEL,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            return next((b.text for b in resp.content if b.type == "text"), "").strip()
        except Exception as exc:
            print(f"[backend] summarize failed ({exc}); falling back to mock.")
            return self._mock_summarize(transcript, prev_summary)

    # -- Wall Detection (PRD FR-3) --
    def detect_wall(self, transcript: str, summary: str) -> dict:
        if not self.live:
            return self._mock_detect_wall(transcript)
        prompt = (
            "You are the attention layer of an assistant that may proactively offer "
            "help. Decide whether the conversation has hit a WALL that the assistant "
            "could help with right now. Wall categories:\n"
            "  - unanswered_question: someone asked something nobody answered\n"
            "  - factual_gap: an expressed uncertainty / 'I don't know' / 'what was...'\n"
            "  - stuck_point: the conversation is looping or stalled\n"
            "  - explicit_ask: a wish said into the air ('I wish I knew...')\n"
            "  - none: no wall; stay silent\n"
            "Favor precision over recall — only flag a wall you are confident about. "
            "If you would help, write the single sentence the assistant should say.\n\n"
            f"Living summary:\n{summary or '(none)'}\n\n"
            f"Recent transcript:\n{transcript}"
        )
        schema = {
            "type": "object",
            "properties": {
                "is_wall": {"type": "boolean"},
                "category": {
                    "type": "string",
                    "enum": [
                        "unanswered_question", "factual_gap",
                        "stuck_point", "explicit_ask", "none",
                    ],
                },
                "confidence": {"type": "number"},
                "offer": {"type": "string"},
            },
            "required": ["is_wall", "category", "confidence", "offer"],
            "additionalProperties": False,
        }
        try:
            resp = self._client.messages.create(
                model=AMBIENT_MODEL,
                max_tokens=300,
                output_config={"format": {"type": "json_schema", "schema": schema}},
                messages=[{"role": "user", "content": prompt}],
            )
            text = next((b.text for b in resp.content if b.type == "text"), "{}")
            return json.loads(text)
        except Exception as exc:
            print(f"[backend] wall detection failed ({exc}); falling back to mock.")
            return self._mock_detect_wall(transcript)

    # -- Engaged first response (PRD: out of scope downstream, stubbed here) --
    def engaged_reply(self, handoff: EngagementHandoff) -> str:
        if not self.live:
            return ("Yes? I've been following along — "
                    f"we were on: {handoff.summary or 'your conversation'}")
        prompt = (
            "You are Jarvis, just engaged in a conversation you were ambiently "
            "following. Greet briefly and show you have the context. One or two "
            "sentences, no preamble.\n\n"
            f"Why you engaged: {handoff.trigger_reason}\n"
            f"Conversation summary: {handoff.summary}\n"
            f"Most recent lines:\n{handoff.recent_excerpt}"
        )
        try:
            resp = self._client.messages.create(
                model=ENGAGED_MODEL,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            return next((b.text for b in resp.content if b.type == "text"), "").strip()
        except Exception as exc:
            print(f"[backend] engaged reply failed ({exc}); using canned line.")
            return f"Yes? We were on: {handoff.summary}"

    # ---- mock heuristics -------------------------------------------------
    @staticmethod
    def _mock_summarize(transcript: str, prev_summary: str) -> str:
        lines = [l for l in transcript.splitlines() if l.strip()]
        topic = ", ".join(sorted(keywords(transcript))[:6]) or "general chat"
        last = lines[-1] if lines else ""
        return f"Discussing {topic}. Latest: {last[:80]}"

    @staticmethod
    def _mock_detect_wall(transcript: str) -> dict:
        lines = [l for l in transcript.splitlines() if l.strip()]
        if not lines:
            return {"is_wall": False, "category": "none", "confidence": 0.0, "offer": ""}
        last = lines[-1]
        body = last.split(":", 1)[-1].strip().lower()
        # explicit ask
        if re.search(r"\b(i wish|if only|wish i (knew|had))\b", body):
            return {"is_wall": True, "category": "explicit_ask", "confidence": 0.78,
                    "offer": "Want me to look that up for you?"}
        # factual gap
        if re.search(r"\b(i (don'?t|do not) (know|remember)|what (was|were)|can'?t recall|no idea)\b", body):
            return {"is_wall": True, "category": "factual_gap", "confidence": 0.80,
                    "offer": "I can find that — want me to?"}
        # unanswered question: this line is a question and prior line wasn't an answer
        if body.endswith("?"):
            return {"is_wall": True, "category": "unanswered_question", "confidence": 0.72,
                    "offer": "I think I can answer that — shall I?"}
        return {"is_wall": False, "category": "none", "confidence": 0.0, "offer": ""}


# ============================================================================
# Living Summary with delta-update (PRD FR-2)
# ============================================================================
class LivingSummary:
    def __init__(self, backend: Backend):
        self._backend = backend
        self.text = ""
        self._basis_keywords: set[str] = set()
        self._utterances_since_update = 0

    def consider_update(self, window: RollingWindow) -> bool:
        """Returns True iff a topic shift was detected and the summary refreshed.

        This is the 'only redraw the changed pixels' rule: the expensive
        summarize() runs only on a topic shift, not on every utterance.
        """
        utts = window.utterances()
        self._utterances_since_update += 1
        if len(utts) < MIN_UTTERANCES_FOR_SUMMARY:
            return False

        current = window.keywords()
        # First summary, or the topic drifted away from what the summary was built on.
        first_time = not self.text
        sim = jaccard(current, self._basis_keywords)
        shifted = sim < TOPIC_SHIFT_THRESHOLD and self._utterances_since_update >= 2

        if first_time or shifted:
            self.text = self._backend.summarize(window.transcript(), self.text)
            self._basis_keywords = current
            self._utterances_since_update = 0
            return True
        return False


# ============================================================================
# The Attention Layer: orchestrates everything (PRD §4, §7)
# ============================================================================
class AttentionLayer:
    def __init__(
        self,
        backend: Backend,
        on_summary_update: Optional[Callable[[str], None]] = None,
        on_interjection: Optional[Callable[[dict], None]] = None,
        on_engagement: Optional[Callable[[EngagementHandoff], None]] = None,
    ):
        self.backend = backend
        self.window = RollingWindow(WINDOW_MAX_UTTERANCES, WINDOW_MAX_SECONDS)
        self.summary = LivingSummary(backend)
        self._last_wall_signature = ""        # back-off (PRD FR-4.5)
        self.on_summary_update = on_summary_update
        self.on_interjection = on_interjection
        self.on_engagement = on_engagement

    def ingest(self, u: Utterance) -> None:
        # Path A: explicit summon. Highest precision, always wins.
        if self._is_summon(u):
            self.window.add(u)
            self._engage(trigger="summon", detail=u.text)
            return

        self.window.add(u)

        # Living summary, delta-updated.
        if self.summary.consider_update(self.window) and self.on_summary_update:
            self.on_summary_update(self.summary.text)

        # Path B: proactive wall detection — gated so we don't run the brain on
        # every utterance (PRD FR-3.4). Only evaluate when the latest line carries
        # a cheap wall signal.
        if self._has_wall_signal(u.text):
            verdict = self.backend.detect_wall(self.window.transcript(), self.summary.text)
            self._maybe_interject(verdict)

    # ---- Path A helpers ----
    @staticmethod
    def _is_summon(u: Utterance) -> bool:
        return bool(re.search(rf"\b{WAKE_WORD}\b", u.text, re.IGNORECASE))

    # ---- Path B helpers ----
    @staticmethod
    def _has_wall_signal(text: str) -> bool:
        t = text.lower()
        if t.rstrip().endswith("?"):
            return True
        return bool(re.search(
            r"\b(i wish|if only|i (don'?t|do not) (know|remember)|what (was|were)|"
            r"can'?t recall|no idea|stuck|not sure)\b", t))

    def _maybe_interject(self, verdict: dict) -> None:
        if not verdict.get("is_wall"):
            return
        if float(verdict.get("confidence", 0)) < WALL_CONFIDENCE_TO_SPEAK:
            return  # PRD FR-4.3: precision over recall
        sig = f"{verdict.get('category')}::{verdict.get('offer')}"
        if sig == self._last_wall_signature:
            return  # PRD FR-4.5: don't repeat the same offer
        self._last_wall_signature = sig
        if self.on_interjection:
            self.on_interjection(verdict)

    # ---- Engagement boundary ----
    def _engage(self, trigger: str, detail: str = "") -> None:
        utts = self.window.utterances()
        excerpt = "\n".join(f"{x.speaker}: {x.text}" for x in utts[-4:])
        handoff = EngagementHandoff(
            trigger_reason=trigger,
            summary=self.summary.text,
            recent_excerpt=excerpt,
            detail=detail,
        )
        if self.on_engagement:
            self.on_engagement(handoff)


# ============================================================================
# Demo wiring
# ============================================================================
DEMO_CONVERSATION = [
    ("Alex", "Hey, did you book the flights for the Tokyo trip yet?"),
    ("Sam", "Not yet, I keep forgetting which week we settled on."),
    ("Alex", "I think it was the second week of October, but I'm honestly not sure."),
    ("Sam", "Yeah... what was the date of the conference again? I can't remember."),
    ("Alex", "Good question. Anyway, totally different thing —"),
    ("Alex", "have you tried that new ramen place on 4th street?"),
    ("Sam", "Oh the one with the tonkotsu? It's incredible. We should go this weekend."),
    ("Alex", "Let's do Saturday. Jarvis, add that to my calendar for Saturday at 7."),
]


def _print_banner(backend: Backend) -> None:
    print("=" * 70)
    print("  Project Jarvis — Attention Layer prototype")
    print(f"  Backend: {backend.mode}")
    print(f"  Wake word: '{WAKE_WORD}'   |   speak-threshold: {WALL_CONFIDENCE_TO_SPEAK}")
    print("=" * 70)


def run(source: TranscriptSource) -> None:
    backend = Backend()
    _print_banner(backend)

    def on_summary(text: str) -> None:
        print(f"\n   📝 [living summary updated] {text}\n")

    def on_interjection(v: dict) -> None:
        print(f"\n   🔊 JARVIS (interjecting, {v['category']} "
              f"@ {float(v['confidence']):.2f}): {v['offer']}\n")

    def on_engagement(h: EngagementHandoff) -> None:
        print("\n   " + "-" * 60)
        print(f"   ⚡ ENGAGEMENT  (trigger: {h.trigger_reason})")
        print(f"      summary : {h.summary or '(none yet)'}")
        reply = backend.engaged_reply(h)
        print(f"      jarvis  : {reply}")
        print("   " + "-" * 60 + "\n")

    layer = AttentionLayer(
        backend,
        on_summary_update=on_summary,
        on_interjection=on_interjection,
        on_engagement=on_engagement,
    )

    for u in source.utterances():
        print(f"{u.speaker}: {u.text}")
        layer.ingest(u)


def main() -> None:
    ap = argparse.ArgumentParser(description="Jarvis attention-layer prototype")
    ap.add_argument("--demo", action="store_true", help="run the scripted conversation")
    ap.add_argument("--pace", type=float, default=0.0,
                    help="seconds between demo lines (default 0)")
    args = ap.parse_args()

    if args.demo:
        run(ScriptedSource(DEMO_CONVERSATION, pace=args.pace))
    else:
        run(StdinSource())


if __name__ == "__main__":
    main()
