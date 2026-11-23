# Inline visuals — design

Status: built and live-tested on Claude Code (Sonnet) against the demo book (2026-11-17). Flag: `inline_visuals`. ADR:
`docs/adr/2026-11-17-inline-visuals-are-data-blocks-filled-from-tool-results.md`.

## Problem

Lucius answers in prose even when the answer is a shape (a trend, a breakdown, a comparison), and
its prose runs long: a 5–15 word question gets a median ~180-word reply. `create_artifact` can
draw, but an artifact is a FILE (stored, downloadable) and the model writes the whole HTML,
copying every number by hand.

## Decision in one paragraph

An inline visual is part of the reply, not a file. Lucius calls `show_visual` with a small spec
made of at most three BLOCKS from a fixed catalog (stats, chart, timeline). A block never holds a
number the model typed: every value is a REFERENCE to a tool result already in this conversation
(`{"tool": "get_spending_summary", "args": {"month": "2026-10"}, "path": "total"}`), and the
server copies the numbers out. Our own React components draw the resolved spec in the message
bubble; Telegram and other channels get a plain-text rendering of the same spec. The model
writes only labels, and labels may not carry amounts.

## Top questions the catalog must answer

| Question | Blocks | Source tool |
|---|---|---|
| Where did my money go this month? | chart `bar` | `get_spending_breakdown` |
| Did I spend more than last month? | stats (now vs then) + chart `bar` with two series | `get_spending_summary` ×2 |
| How is my net worth trending? | stats + chart `line` | `get_net_worth_history` |
| What's my allocation / concentration? | chart `stack` | `get_portfolio` |
| How am I doing vs the market? | chart `line`, 2 series | `get_returns` |
| When do my shares vest? | timeline | `get_upcoming_vests` |
| Will I run short before payday? | chart `line` (+ timeline of bills) | `simulate_cashflow` |
| Am I on track to retire / what if? | stats with compare | `simulate_retirement` (no-arg + what-if) |
| How's my budget this month? | chart `progress` | `get_budget_month` |
| What bills are coming up? | timeline | `get_commitments`, `get_recurring_streams` |

## Model-facing input (`show_visual`)

```jsonc
{
  "title": "Spending, Nov vs Oct",          // optional, ≤ 60 chars, no amounts
  "blocks": [Block, ...]                     // 1–3
}
```

`Ref` — where a value comes from:

```jsonc
{ "tool": "get_spending_summary",   // tool name (or a tool_use id)
  "args": {"month": "2026-10"},      // optional: pick the call whose input has these args
  "path": "categories" }             // dotted path into the JSON result; [n] indexes a list
```

Resolution: newest call of `tool` in this conversation whose input contains every `args`
entry; an explicit `"args": {}` means the call made with NO arguments (a saved-plan
`simulate_retirement`). If `args` is omitted and the conversation holds calls of that tool with
DIFFERENT inputs, the call is refused with the candidates' args listed. A path that lands on a mapping of
scalars becomes rows `{"key": k, "value": v}`.

Blocks:

```jsonc
{"type": "stats", "tiles": [            // 1–4 tiles
  {"label": "This month", "value": Ref, "compare": Ref?,   // compare = the earlier/baseline figure
   "compare_label": "Oct"?, "format": "money", "currency": "SGD"?}]}

{"type": "chart", "kind": "line|bar|diverging|stack|progress",
 "format": "money|number|percent|percent_points", "currency": "SGD"?,
 "rows": Ref,                            // list of records (or mapping → key/value)
 "label": "category",                    // field in each row used as the x/category label
 "series": [{"field": "amount", "name": "Spent", "rows": Ref?}],   // ≤ 3; a series may use its
                                         // own rows, joined to the block's rows by label
 "target": "budgeted"?,                  // progress only: field holding the target
 "top": 10?}                             // bar/stack/diverging/progress: keep the N largest

{"type": "timeline", "rows": Ref, "date": "date", "label": "name",
 "value": "amount"?, "format": "money", "currency": "SGD"?}
```

Formats: `money` (needs `currency`, an ISO code), `number`, `percent` (a fraction, 0.12 = 12%),
`percent_points` (12.0 = 12%).

Rules enforced server-side (each failure returns an error the model can fix):
- every value resolves to a finite number (or null where a tool returned null); text in a value
  field is refused;
- labels (title, tile labels, series names, compare_label) ≤ 40 chars (title 60) and contain no
  amount (`S$1,200`, `4,180`, `12.5`) — numbers belong in values;
- caps: stats ≤ 4 tiles, line ≤ 120 points (refused beyond — a trend is never thinned), ≤ 3
  series; `stack`, `diverging`, `progress` take exactly 1 series. A breakdown (bar, diverging,
  stack, progress) drops empty rows and keeps its 24 (or `top`) largest by the first series,
  noted "Largest N of M" — nothing is summed into an "Other". A timeline keeps its first 20
  by date, noted "First 20 of M". These notes are the block's own `note`, drawn under that
  block, never in the visual's `caption` (which can't say which chart it means);
- `format: "percent"` is a fraction (0.12 = 12%); a value beyond ±10 (±1,000%) is refused as
  already in points — use `percent_points` (12 = 12%);
- a reference to `simulate_retirement` adds a server-written caption naming every lever the
  what-if moved and its saved value ("What-if — monthly saving 3,000 (your plan: 2,000)"); a
  reference to `simulate_cashflow` adds "Projection — not booked".

## Resolved spec (the frontend + channel contract)

```jsonc
{
  "id": "vis_1a2b3c4d5e6f",
  "title": "Spending, Nov vs Oct" | null,
  "as_of": "2026-11-17T09:30:00Z",
  "caption": ["What-if — monthly saving 3,000 (your plan: 2,000)"],   // may be []
  "blocks": [
    {"type": "stats", "tiles": [
      {"label": "This month", "value": 4180.0, "compare": 3640.0 | null,
       "compare_label": "Oct" | null, "format": "money", "currency": "SGD" | null}]},
    {"type": "chart", "kind": "bar", "format": "money", "currency": "SGD" | null,
     "labels": ["Dining", "Travel"],
     "series": [{"name": "Nov", "values": [310.0, 420.0]}, {"name": "Oct", "values": [280.0, null]}],
     "targets": [400.0, 500.0] | null},
    {"type": "timeline", "format": "money" | null, "currency": "SGD" | null,
     "items": [{"date": "2026-12-01", "label": "ACME vest", "value": 5200.0 | null}]}
  ]
}
```

Numbers are raw; the client formats them (`money` with the screens' currency notation, e.g.
S$4,180). The client never adds, subtracts or derives a figure — the only arithmetic it does is
geometry (bar widths, axis scales).

## Freeform drawings (`code`, added 2026-11-18)

`show_visual` takes `code` — SVG or HTML the model writes itself, figures included — alone or
beside `blocks`. The owner chose this over growing the catalog, chose **no** server check of the
figures, and then kept **a minimal set of limits**: a sandboxed frame,
the theme's colours, a standard width. Everything else was removed on request.

- **Runs as written** in an `<iframe sandbox="allow-scripts allow-popups
  allow-popups-to-escape-sandbox allow-forms allow-modals">` with no `allow-same-origin`
  (`visual/VisualDrawing.tsx`, document built by `visual/drawingFrame.ts`): scripts, `<style>`,
  CDN libraries, images and links all work. The opaque origin is what keeps a script from
  acting as the signed-in app.
- **Its own CSP, not the page's:** the frame loads `GET /api/visuals/frame`
  (`backend/api/visuals.py`), an empty loader served with `sandbox allow-scripts …;
  frame-ancestors 'self'`; the chat posts the drawing's document in and the loader writes it.
  A `srcdoc` frame inherits the app page's `script-src 'self'` and ran nothing on a served
  build — not the drawing's scripts, not the height report (found live on 8080, 2026-11-18;
  the dev server sends no CSP). Same trap `/api/artifacts/{id}/frame` exists for.
- **Grows to fit:** a bridge script in the frame posts its height; no cap, no clip. The frame
  hides its own page scroll bar (it always grows to the full drawing).
- **Title:** shown above blocks only; a drawing carries its own heading, so for a drawing the
  title just names the frame.
- **Theme:** the frame gets the app's CSS variables and the `iv-` classes at build time and
  live theme changes by `postMessage`.
- **Server** (`ai/visual_code.py`) only requires a non-empty drawing. A drawing's title is
  its own words: no amount guard, no length cap. It is not shown above the drawing (the
  drawing carries its own heading); it names the frame.
- **Hidden amounts do not mask a drawing** (owner's choice).
- **Style guide** (owner, 2026-11-18: a full design guide, not two lines). The
  VISUALS paragraph tells the model how to draw — flat, no title inside, two font weights,
  text in ink colours, colour by meaning (two hues plus grey), a fixed series order, thin
  bars, hairline grids, labels that never collide, Chart.js notes — and the frame supplies the
  matching classes and variables in light and dark (`visual/drawingStyle.ts`: `t ts th tm`,
  `s1…s8`, `good warn serious bad muted`, `grid axis`, `c-{ramp}`). Guidance, never enforced.
- **Channels** get the title and a note to open the app.

## Wiring

- Tool: `backend/ai/tools/visuals.py` (`show_visual`, read-only, `mutates=()`), resolver and
  validation in `backend/ai/visual_spec.py`. Conversation access: `backend/ai/visual_sources.py`,
  a ContextVar set by `assistant.run` to the live session history list.
- Tool result (what the model sees and what a reload re-derives the block from):
  `{"shown": true, "visual": <resolved spec>, "note": "Already on screen — don't repeat its numbers."}`.
  A drawing's `code` is left out of the result (the model would pay for its own markup again
  on every later round); the live event and a reload put
  it back from the call's arguments (`assistant._visual_from_result(result, args)`).
- Stream: `_post_execute_events` emits `{"type": "visual", "tool_use_id", "visual"}`; the
  transcript rebuild appends `{"type": "visual", "visual": <spec>}` to the owning message's
  blocks (same pattern as artifacts).
- Channels: `connectors/processor.py` turns a `visual` event into text via
  `services/visual_text.render_text(spec)`.
- Frontend: `frontend/src/components/chat/visual/` renders a `visual` block. There are no
  follow-up chips (removed 2026-11-18: they duplicated the app's own suggested follow-ups).
- Flag `inline_visuals` (default on). Off: the handler refuses and the prompt paragraph is not
  added — the old reply path is unchanged.

## Answer length (same change)

- Prompt: an ANSWER LENGTH section (lead with the answer; a fact = 1–3 sentences; an analysis
  ≤ ~6 one-line bullets; headers only for 3+ sections; no closing recap; never restate a
  visual's numbers), repeated as one line at the end of the static prompt.
- Profile `chat_response_detail`: `brief | normal | detailed` (null = normal). `brief` and
  `detailed` add a per-turn RESPONSE STYLE line; normal adds none (smalltalk keeps its own).

## What-ifs and screen commands

- One engine: a visual shows `simulate_*` results only; it never projects anything itself, and
  has no sliders. Exploring levers is Plan › Outlook's job.
- On the Outlook screen: stage the what-if with `run_screen_command plan.stage_whatif` and
  answer in a sentence — no visual of what the screen already shows.
- Elsewhere: `simulate_retirement` (saved plan) + `simulate_retirement` (what-if) → one stats
  block comparing them, plus `plan.stage_whatif` so the reply carries the "open in Outlook" chip.
- Follow-up chips only send a message. A visual never writes data and never runs a command.
