# RFC: One query path for dashboard widgets, shared with Lucius's inline visuals

Status: **Draft for owner review** (2026-11-18). Nothing here is built.
Related: `docs/design/inline-visuals.md` + ADR `2026-11-17-inline-visuals-are-data-blocks-filled-from-tool-results`
(both on `feat/inline-visuals`, uncommitted), ADR `2026-07-22-overview-tab-redesign-faithful-port`,
ADR `2026-07-31-overview-aggregates-are-server-computed` (superseded in part by this RFC, see §10).

## 1. Problem

**Cost.** Each widget fetches its own resource. `state/queryCache` dedups by URL, so a full
dashboard is ~12–15 distinct reads, not one per widget, but:
- every read re-reads the whole ledger (`ledger.read_book()` per request) — ~15 full reads per load;
- `/api/portfolio` and `/api/portfolio/analytics` each run `projected_rows(live=True)`: two live
  price round-trips per load;
- every write revalidates EVERY cached read (`invalidateQueryCache(() => true)`,
  `frontend/src/state/queryCache.ts`), so a Lucius turn with 10 writes triggers ~150 heavy reads.
  `data_changed` already carries `domains`; nothing uses them.

**Consistency.** Three fetch paths bypass the cache (`useJson` and `useReturnsData` in
`widgets/investing.tsx`, the artifact picker in `widgets/content.tsx`). Comments cite
`/api/overview/*` and `OverviewDataContext`, which no longer exist. Group files run 700–1,060
lines against a ~400 limit.

**A feature that looks real and isn't.** `WidgetInstance.alert` lets a user set a threshold; its own
type comment says "front-end-only threshold mock — no evaluation engine". It never fires.

**Lucius can't build dashboards.** The only Lucius widget, `lux-html`, pins an artifact whose numbers
were typed when it was made — stale the next day. Lucius has no tool to add or arrange widgets,
and the dashboard's screen handles are hard-coded (`Overview.tsx`), not the widgets mounted.

**Two chart systems are about to exist.** `feat/inline-visuals` adds `show_visual`: chat blocks
(stats, line, bar, diverging, stack, progress, timeline) whose numbers are references into tool
results, drawn by new React components. The dashboard has its own `charts.tsx`. Without a decision,
the app gets two vocabularies, two renderers and two palettes for the same charts.

## 2. Goals and non-goals

Goals
1. One server path that answers every widget's data, cheaply, in batches.
2. One spec format for a chart, whether it lives in a chat reply or on the dashboard.
3. Lucius can build, preview and change **live** dashboard widgets.
4. Users' saved widgets survive app upgrades: the data contract is versioned and tested.

Non-goals
- Freeform drawings on the dashboard (§11 — waits on the owner's limits for freeform in chat).
- New chart kinds, dashboard-wide filters, annotations, templates (Phase 4, separate RFCs).
- Card earn rules / miles (a data-model feature; it only needs to fit this contract, §12).

## 3. Decisions already made (owner, 2026-11-18)

| # | Decision |
|---|---|
| D1 | Widget data sources **are Lucius's read-only tools** — no parallel query registry. |
| D2 | Only tools that cannot change anything back a widget (`not registry.may_change_something(name)`): no `mutates=`, no `external_effect=`, not an unknown external-MCP tool. |
| D3 | Read tools gain **relative dates** (`this_month`, `last_12m`, …). Pinning converts an absolute period to a relative one; absolute dates stay allowed. |
| D4 | **No row caps on the dashboard.** A widget shows everything its tool returns (the tool's own model-facing bound still applies). Chat keeps its caps. |
| D5 | Lucius's add/update-widget tools follow the **normal permission rules** (Ask by default, per-tool level, undoable). |
| D6 | The widget spec is the inline-visuals **unresolved block**, as-is — one vocabulary for the model. |
| D7 | One set of chart components and one palette for chat and dashboard. |

## 4. The widget spec

A dashboard widget stores, in `WidgetInstance.config`, the same block Lucius passes to
`show_visual` (contract: `docs/design/inline-visuals.md`, "Model-facing input"), plus a version:

```jsonc
{
  "v": 1,
  "block": {
    "type": "chart", "kind": "bar", "format": "money", "currency": "SGD",
    "rows":   { "tool": "get_spending_breakdown", "args": { "month": "this_month" }, "path": "categories" },
    "label":  "category",
    "series": [ { "field": "amount", "name": "Spent" } ],
    "top": 10
  }
}
```

Differences from chat, all at resolution time, none in the format:
- **Source.** In chat a `Ref` picks the newest matching call *in the conversation*. On the dashboard
  it is executed: `tool(args)` runs now. A widget `Ref` must therefore name its `args` in full
  (an omitted `args` is refused at save; `{}` means "no arguments", as in chat). A chat `Ref` may
  omit `args` (resolve takes the newest matching call), so pinning writes the args of the call
  actually matched (`visual_spec`'s `used` list of `ToolCall`), never the ref as the model typed it.
- **Caps.** `resolve(..., caps=None)` (D4): no "Largest N of M" trimming unless the spec sets `top`.
- **No follow-ups / captions from conversation context.** Server-written trust captions
  ("Projection — not booked", what-if levers) still apply — they come from the tool, not the chat.

Built-in widgets keep their own React components (a net-worth headline is not a generic chart),
but their **data** moves to the same path: each declares a `source` that is a tool + args (§6).

## 5. The query endpoint

```
POST /api/dashboard/query
{ "queries": [
    { "id": "w1", "tool": "get_net_worth_history", "args": { "range": "last_12m" } },
    { "id": "w2", "tool": "get_spending_breakdown", "args": { "month": "this_month" } }
] }
→ 200 { "as_of": "…", "results": {
          "w1": { "data": { … }, "as_of": "…" },
          "w2": { "error": { "code": "unknown_category", "message": "…" } } } }
```

- The endpoint returns the **raw tool result**, and the block's `path`/`label`/`series` is applied
  afterwards. Raw results let two widgets on the same tool+args share one execution. Whether
  that resolution step runs on the server or the client is open (O6).
- **Per-query errors.** One failing widget never fails the batch.
- **Dedup.** Identical `(tool, canonical args)` in a batch execute once.
- **Eligibility, checked per query, server-side:** D2's test; the tool exists; it is not in
  `ai.tools.registry._DEMO_REFUSED_TOOLS` under demo mode; a connector-scoped tool's connector is
  enabled. A refused query returns `error.code = "not_pinnable"`, never a 4xx for the batch.
- **Layering.** `api/dashboard.py` validates and calls ONE function, `ai/widget_query.run_batch`
  (`api → ai` is allowed; `services` may not import `ai`, so the runner cannot live in
  `services/dashboard.py`). The runner invokes the tool's handler directly with validated input —
  **not** through `dispatch`, the confirm gate or the turn machinery: there is no model and no
  approval for a read the owner's own screen makes. Open question O2 asks whether a tool the owner
  set to *Blocked for Lucius* should still feed their dashboard.

### 5.1 One ledger read per batch

The runner opens a **batch scope** (a ContextVar) in which `ledger.read_book()` returns one
snapshot and `portfolio_rows.projected_rows(live=True)` runs at most once (keyed by base and
`live`). Outside a batch scope both behave exactly as today. This is the change that turns ~15
book reads + 2 price fetches into 1 + 1. The snapshot is read-only and shared, which
`LedgerBook`'s contract already requires.

## 6. Built-in widgets on the same path

Each built-in widget definition gains `source: { tool, args }` (and may read several). Where the
data a widget needs is not exposed by any read tool today (e.g. the analytics views behind
`/api/portfolio/analytics`), we add a read tool rather than keep a REST-only read.

**Catalogue budget.** The full tool catalogue is at its size limit (`tests/test_tool_schema_budget.py`,
481,000 chars). A data source that only the dashboard needs must not cost every Lucius turn, so the
registry gains `model_visible=False`: registered, typed, versioned and pinnable, but excluded from
`build_tool_schemas()` and `find_tools`. It is the same registry and the same contract (D1); it is
simply not offered to the model. (Owner may prefer to expose them all — O5.)

## 7. Relative dates (D3)

- A tool that takes a period opts in by typing that arg as `Period`: an absolute value (`"2026-11"`,
  `"2026-01-01..2026-06-30"`) or a relative one: `this_month`, `last_month`, `this_year`,
  `last_year`, `ytd`, `last_3m`, `last_6m`, `last_12m`.
- Resolution happens once, server-side, in `core.user_time` terms (the profile's timezone), before
  the handler sees the args — the handler still receives absolute dates, so tool code is unchanged
  beyond the type.
- **Pinning** a chat visual: if its `Ref` args name the current period, the pin offers
  "Roll forward (this month)" (default) or "Keep November 2026"; an older period defaults to keep.

## 8. Refresh after writes

- Tools gain an optional `reads=("ledger", "budget", …)`, the read twin of `mutates=`, using the same
  domain names `data_changed` already emits.
- The client revalidates only the widget queries whose tool reads a changed domain; a tool with no
  `reads=` refreshes on any change (today's behaviour, so the default is safe).
- Revalidation is **debounced** (trailing, ~300 ms) so a burst of writes in one Lucius turn costs
  one batch, not one per write.
- Price-driven widgets keep their market-hours refetch interval.

## 9. Frontend

- `useWidgetQuery(tool, args)` replaces the per-resource hooks for widgets. Calls made in the same
  tick are queued and sent as one `POST /api/dashboard/query`; results are cached per
  `(tool, canonical args)` in `state/queryCache`, so the existing stale-while-revalidate and
  cross-session persistence keep working.
- **Shared renderers (D7):** move `frontend/src/components/chat/visual/` to
  `frontend/src/components/visual/` — including `VisualDrawing.tsx` and `drawingFrame.ts` (which now
  carries the drawing `iv-*` classes injected into the frame) — and the remaining `.ivis-*` CSS
  (e.g. `.ivis-frame`) out of `chat.css` into a shared file; chat keeps only the chat parts
  (follow-up chips, conversation captions). The dashboard's generic charts
  use these components; `charts.tsx` keeps only what a bespoke built-in needs.
- `WidgetShell` owns loading / empty / error / stale states for every widget; every result carries
  `as_of`.
- One file per widget (+ its test); the registry collects them, as `registry.ts` does per group today.

## 10. The contract: keeping saved widgets working

A saved widget depends on: the tool name, its input schema, and the `path`/fields it reads from the
result. That makes those three a public contract, enforced like this:

1. **Names are already FINAL** (no aliases; a rename is a data migration via
   `backend/scripts/tool_renames.py`). That script also rewrites saved widget specs.
2. **Additive changes by default:** new optional args with defaults, new result fields.
3. **Breaking changes ship a migration.** The spec carries `v`; a tool that changes an arg or a result
   path registers `migrate_widget_spec(old) -> new`. Specs are upgraded on read and written back
   through the layout choke (§12).
4. **CI guards.**
   - *Schema snapshot:* a committed snapshot of every pinnable tool's input schema and of the result
     paths saved widgets may reference. Removing or renaming one without a migration fails CI (same
     idea as the `connector_directory.json` drift test).
   - *Golden specs:* every spec shape we have shipped (built-in sources + representative Lucius
     specs) lives in `tests/fixtures/widget_specs/`; CI migrates, validates and runs each against a
     fixture book.
5. **Deprecate before removing:** a deprecated tool still runs, Lucius stops being offered it, the
   widget shows "Update".
6. **Fail visibly.** A spec that still cannot resolve (newer spec on an older install, a deleted
   category id) keeps its config and renders "This widget needs updating" with "Ask Lucius to fix" —
   never a zero, never a deleted widget.

A change in what a number *means* (a bug fix) is not a contract break; it ships in release notes.

ADR to write when Phase 1 lands: "Dashboard widgets read through Lucius's read-only tools", superseding
the per-resource-hook decision of `2026-07-31-overview-aggregates-are-server-computed` (its rule that
every figure is server-computed stands unchanged).

## 11. Lucius

Tools (all via the `add-tool` skill, write-classified explicitly):

| Tool | Kind | Purpose |
|---|---|---|
| `list_dashboard_widgets` | read | Views and widgets (ids, titles, specs) — also feeds screen handles |
| `add_dashboard_widget` | write, `mutates=("dashboard_layouts",)` | Add a widget from a block spec to a view, optional size/position |
| `update_dashboard_widget` | write | Patch a widget's spec, title or size |
| `remove_dashboard_widget` | write | Remove one widget |

- **Preview is `show_visual`.** Lucius runs the read tool, shows the block in chat, then adds it with
  a second call carrying the same block. No separate preview tool. (`show_visual` can only preview a
  tool that has already run in that conversation.)
- **"Pin to dashboard"** on any chat visual whose sources are all pinnable (D2): the owner's click,
  not a model call; the period choice of §7 applies.
- **Screen handles** come from the mounted widgets, so "what is this widget saying?" works for any widget.
- **Missing data is said, not faked.** The tool descriptions and the VISUALS prompt paragraph state it:
  when no read tool returns what a widget needs, Lucius says so and offers to record the missing fact.
- **SQL.** `run_sql_query` is read-only, so it is pinnable under D2 with no extra rule. Its widget shows
  "Custom SQL" and its spec's query on hover.
- **Freeform drawings** (`show_visual` with `code`, alongside or instead of `blocks`). Their chat limits are
  settled (2026-11-18): a sandboxed iframe (`allow-scripts`, no `allow-same-origin`), theme colours, a
  680-wide canvas; scripts and CDN libraries allowed, no masking under hidden amounts. Renderer:
  `chat/visual/VisualDrawing.tsx` + `drawingFrame.ts`. Pinned as-is, a drawing's figures are the ones the
  model typed, so it is a snapshot. A live path exists: the parent posts resolved `Ref` data to the
  drawing's script. Whether drawings go on the dashboard at all is O4.

## 12. Layout writes

`services/dashboard.apply_dashboard_change(op, …)` becomes the one write path for layouts (the UI's
`PUT /api/dashboard/layout` and the Lucius tools both go through it): validation as today plus spec
validation (`v`, block shape, pinnable sources), checkpointed (`dashboard_layouts` is already in
`ai/checkpoint/_policy.py`), and a `data_changed` event so every open dashboard updates. Rejecting an
unknown `defId` stays the client's job (unchanged); rejecting an unpinnable source is the server's.

## 13. Phases

| Phase | Ships | Done when |
|---|---|---|
| **1. Query path** | `POST /api/dashboard/query`, batch scope (§5.1), `reads=`, debounced scoped refresh, `useWidgetQuery`; fold the three cache-bypassing fetches in; delete stale `/api/overview/*` comments | A full default dashboard loads with 1 book read and ≤1 price fetch (measured on a fixture book); a write refreshes only affected widgets; no visible change |
| **2. Contract + standardization** | `Period` + relative dates, `model_visible`, per-widget sources for built-ins (group by group), spec `v` + migrations, schema snapshot + golden specs, shared renderers moved out of `chat/`, `WidgetShell` states, one file per widget | Every widget reads through the query path; CI guards live; group files under ~400 lines |
| **3. Lucius widgets** | The four tools, Pin to dashboard, generated screen handles, layout choke | Lucius builds a live widget from chat on Sonnet and Haiku (opt-in live test set, like `make cli-smoke`) |
| **4. Features** | Dashboard-wide filters, period comparison, drill-down, annotations, templates/export, real alerts | Separate RFCs |

Depends on `feat/inline-visuals` landing first (Phase 2's renderer move and Phase 3's spec).

## 14. Risks

- **Tools assume a turn.** Some handlers read turn ContextVars (`active_call_input`, session). Phase 1
  starts with a spike running every candidate read tool outside a turn; a tool that can't is not
  pinnable until fixed.
- **Model-shaped results.** Tool results are bounded and shaped for the model ("filter before you cap").
  With D4 a widget may still be trimmed by the tool itself; the widget says so when the result reports
  a truncation. Tools that bound too hard for a widget get a pagination or `model_visible=False` twin.
- **Contract drag.** Every change to a pinnable tool's args/results now needs a compatibility thought;
  the CI guards make it visible rather than silent.
- **Batch head-of-line blocking.** A live-price query slows its batch. Mitigation if measured: the
  client sends price-dependent queries as their own batch.

## 15. Open questions for the owner

Each of these would add a limit or change behaviour, so none is decided here.

| # | Question | Options |
|---|---|---|
| O1 | Read tools that call the network (web search, page fetch, market data) pass D2. Can they back a widget, re-running on every dashboard load? | Allow / allow market data only / exclude network reads |
| O2 | If the owner sets a tool to *Blocked* for Lucius, should their dashboard still read through it? | Dashboard ignores Lucius permissions / honours Blocked |
| O3 | The alert threshold on widgets never fires. | Remove now and rebuild in Phase 4 / keep until Phase 4 |
| O4 | Freeform drawings on the dashboard (chat limits now settled, §11). Until decided, `add_dashboard_widget` takes `blocks` only. | Not pinnable / pinnable as a labelled snapshot / pinnable only when its figures come from `Ref` data posted to the frame |
| O5 | Dashboard-only data sources. | `model_visible=False` (no prompt cost) / expose every source to Lucius (raises the catalogue budget) |
| O6 | Where a block's `path`/`label`/`series` is applied for widgets. | Server (`visual_spec.resolve`, one implementation, chat parity — works today by binding a one-call synthetic history `[tool_use, tool_result]` to `ai/visual_sources`; captions such as what-if levers come along; needs a new `caps=None`) / client (lets two widgets share one raw result) |
