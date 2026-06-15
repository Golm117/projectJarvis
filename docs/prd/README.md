# Project Jarvis — Product Requirements (PRD)

A living, sectioned PRD for a personal assistant inspired by the fictional Jarvis
from Iron Man. The goal is to start small and build a foundation we can expand on.

This folder holds the PRD as a set of ordered, independently-readable sections.
Each file covers one subject. The PRD grows by adding sections, not by rewriting a
single monolithic document.

## Sections

| # | Section | Status | Summary |
|---|---------|--------|---------|
| 01 | [Conversation Initiation — The Attention Layer](01-conversation-initiation.md) | Draft | How and when Jarvis starts paying attention and speaks up. The start of the user-interaction journey. |
| 02 | [Jarvis v0 (MVP)](02-jarvis-v0-mvp.md) | Ready for build | The first buildable slice: local always-on listening, dual-summon (wake word + polite interjection), Claude answer, ElevenLabs voice. |

## How this PRD feeds the build

This PRD is being written *ahead of* the `Start_Here/` bootstrap (`/pdr-grill` →
`.pdr.md` → `BOOTSTRAP`). The product/scope decisions captured here will later feed
the `.pdr.md` and the per-domain design docs the bootstrap scaffolds. Until then,
this is the canonical place for requirements thinking.

## Conventions

- One subject per file. Numbered prefixes keep reading order stable.
- Each section opens with **Scope** (what it covers) and closes with **Out of scope /
  future sections** (what it defers).
- Terminology defined in one section is reused verbatim across the others.
