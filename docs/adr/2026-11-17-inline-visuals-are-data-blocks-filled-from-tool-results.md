---
title: Inline visuals are data blocks filled from tool results, drawn by our own components
status: Accepted
date: 2026-11-17
deciders: repo owner + Claude
---

# Inline visuals are data blocks filled from tool results, drawn by our own components

## Context

Lucius answered in prose even when the answer was a shape, and the prose ran long: over the
owner's own transcripts since September a 5–15 word question got a median ~180-word reply.
`create_artifact` could draw, but an artifact is a FILE — stored, downloadable, its own card — and
the model writes the whole HTML, copying every number by hand. The owner asked for visuals that
are part of the reply, not files.

What others do (researched 2026-11-17): Claude.ai and Gemini have the model write HTML/SVG into a
sandbox; ChatGPT runs code for charts and lets apps render widgets from structured data; OpenUI,
Google's A2UI, Vercel json-render and assistant-ui have the model pick from a HOST-DEFINED
component catalog. Every product keeps inline visuals separate from files. Model-written markup
costs tokens, misdraws, and — for money — can misstate a figure.

## Decision

- A new core tool `show_visual` takes up to three blocks from a fixed catalog: `stats` (tiles,
  optional before/after), `chart` (`line`, `bar`, `diverging`, `stack`, `progress`) and
  `timeline`. It is separate from `create_artifact`; nothing is stored apart from the transcript.
- **No number in a visual is typed by the model.** Every value is a reference
  `{tool, args?, path}` to a tool result already in the conversation; `ai.visual_spec` copies the
  number out (`ai.visual_sources` reads the turn's live history). Labels may not carry an amount.
  This is A2UI's split between components and data, bound to our own tool results.
- The server owns the words that matter for trust: a reference to `simulate_retirement` adds a
  caption naming every lever the what-if moved and its saved value; `simulate_cashflow` adds
  "Projection — not booked"; a breakdown longer than the chart allows keeps its largest rows and
  says "Largest N of M" (never sums an "Other").
- Our React components draw the resolved spec in the message bubble; the client formats and
  scales but never derives a figure. Channels (Telegram, webhooks) get `services.visual_text`.
- What-ifs keep ONE engine (ADR `2026-10-14-one-retirement-engine-saving-is-a-fact`): a visual
  shows `simulate_*` results only and has no sliders; exploring levers stays on Plan › Outlook,
  reached through `run_screen_command plan.stage_whatif`.
- Answer length: the prompt gains ANSWER LENGTH rules (lead with the answer; a fact = 1–3
  sentences; an analysis ≤ ~6 one-line bullets), repeated in one closing line, and a person's own
  `profile.chat_response_detail` (brief | normal | detailed) adds a per-turn style line.
- Behind flag `inline_visuals` (default on): off, the tool refuses and the prompt paragraph is
  absent.

## Consequences

- `show_visual` joins CORE on structural grounds (the model can't decide a reply is better drawn
  without holding the tool); the default offer stays under its 55K budget, the full catalog's
  budget rises by ~2.7K.
- A model that types a number into a label, references a call that isn't there, or picks between
  two runs ambiguously gets an error it can act on; in live testing (Claude Code, Sonnet) it
  corrected each on the next call.
- Whether to draw stays the model's judgment, steered by a question → visual mapping in the
  prompt; it will sometimes not draw where it could (a budget answer whose point was one
  overspent envelope).
- No generative-UI protocol (OpenUI, A2UI, MCP Apps) is adopted; the block format is shaped so a
  later conversion to A2UI stays cheap.

## Amendment 2026-11-18 — Lucius may also draw freeform

The owner judged the fixed catalog too inflexible and chose freeform drawing as well:
`show_visual` also takes `code`, SVG or HTML the model writes itself, with its figures typed in
and **not** checked by the server (owner's choice: a drawing is the model's own, like its text).
The block catalog stays for the shapes it covers, for channels and for weaker models.

Same day, the owner removed every limit beyond a minimal set. A drawing
now runs as written — scripts, styles, CDN libraries, images, links — in a sandboxed iframe
without `allow-same-origin`, which is the one boundary kept: its scripts never run as the
signed-in app. It may sit beside blocks; its title is uncapped and unchecked and is not shown above it;
it grows to fit; hidden amounts do not mask it. The prompt keeps two conventions only: theme
colours and a 680-wide canvas. The earlier allowlist sanitiser was removed. Accepted risk:
a prompt-injected drawing can load from or send to the internet.

Design: `docs/design/inline-visuals.md`.
