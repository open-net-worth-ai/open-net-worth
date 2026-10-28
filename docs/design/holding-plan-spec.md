# Holding plan — spec

**Status:** built (branch `feature/holding-plan`) · **Design:** Claude Design project "Holding plan — asset page" (frames 1a/1b
held, 2a/2b watched) · **Decision:** ADR `2026-10-27-a-holding-plan-lives-on-the-instrument`

## Problem

A watched ticker can carry *why* you watch it (`intent`: thesis, buy zone, fair value), but a
holding cannot carry *why you own it* or *what would make you sell*. The reason is lost the moment
a watched ticker becomes a holding, and nothing on the asset page tells you whether today's price
is inside your plan.

## What ships

One **plan per instrument**, shown and edited on the asset page as a "Your Plan" rail card.

| State | Card shows | Editable |
|---|---|---|
| Held | role chip (an `i` explains it), thesis, "You'd sell if", price bar + levels with distance from today's price | role, thesis, sell condition, stop, buy zone, fair value, trim |
| Watched / not held | thesis, price bar + buy zone and fair value | thesis, buy zone, fair value |

Held vs watched is **derived** (a live position exists), never stored. A plan survives buying,
selling and leaving every watchlist; it is deleted only on purpose.

Out of scope (follow-ups): new alerts for stop/trim crossings, role-aware risk checks, Lucius
checking news against the sell condition, any holdings-list or notification change.

## Data model

New book-scoped table `holding_plans` (`book_payload_table`), store `db/holding_plan_store.py`.
One row per (book, instrument); the store refuses a second row for the same `instrument_id`.

| Field | Type | Rule |
|---|---|---|
| `id` | `hp_<12hex>` | |
| `instrument_id` | str | required, FK by convention to `instruments.id` |
| `role` | `core` \| `satellite` \| `speculative` \| null | |
| `thesis` | str ≤ 280 | trimmed; "" = unset |
| `exit_if` | str ≤ 280 | trimmed; "" = unset |
| `stop_below` | float > 0 \| null | must be < `trim_above` when both set |
| `buy_below` | float > 0 \| null | top of the buy zone |
| `buy_above` | float > 0 \| null | needs `buy_below`, must be < it |
| `fair_value` | float > 0 \| null | |
| `trim_above` | float > 0 \| null | |
| `created_at` / `updated_at` | ISO UTC | |

Prices are in the instrument's `quote_currency` (what the asset page shows). A plan whose every
field is blank is deleted rather than stored. The table is user intent: checkpoint-INCLUDED,
portable (export, import, backup), readable by `run_sql_query`.

## Write choke

`services/holding_plans.apply_plan_change(change)` — the only writer (REST, Lucius tools, the
watchlist compatibility path, the migration's runtime fallback):

- `{"op": "set", "instrument_id", "fields": {...}, "clear": [...]}` — merges `fields` over the
  existing plan (creating it if absent), blanks every name in `clear`, validates the result as a
  whole, deletes the row if the result is empty. Unknown field names raise.
- `{"op": "delete", "instrument_id"}`.

Validation lives in the store (`_validate_plan`, the same place `watchlist_store._validate_intent`
lives today) so no caller can bypass it. Errors are `core.errors.ValidationError` with the user's
words ("The floor must be below the top of the buy zone.").

Reads: `plan_for(instrument_id)`, `plans_by_instrument()`, `intent_for_item(item)` (the watch-rule
view of a plan: `{thesis, buy_below, buy_above, fair_value}`).

## Migration from watch intent

Alembic revision `<timestamp>_holding_plans`:

1. Create `holding_plans`.
2. For each book's `watchlists` payload, for every ticker item with a non-empty `intent`: find the
   instrument by the item's `instrument_id`, else a unique case-insensitive `price_source_id`
   match, else a unique bare `symbol` match (held crypto: "BTC" vs "bitcoin"). Insert a plan with
   the intent's `thesis`/`buy_below`/`buy_above`/`fair_value` and drop `intent` from the item.
3. An item is left untouched, `intent` kept and a warning logged, when no single instrument
   matches, when a stored price is not a number, or when another item already gave that
   instrument its plan (no network in a migration). `intent_for_item` falls back to a kept intent,
   and the first plan written for that instrument (asset page, Lucius, or an `intent` save)
   absorbs it and clears it from the item.

Downgrade copies each plan's four intent fields back onto the matching watch item and drops the
table (held-only fields are lost on downgrade — stated in the revision).

After the migration nothing writes `item["intent"]`: `watchlist_store` keeps accepting the key on
input only to route it (below), never to store it.

## API

| Route | Does |
|---|---|
| `GET /api/holding-plans` | every plan, with `ticker` and `held` joined in |
| `PUT /api/holding-plans/{instrument_id}` | replaces the plan with the body (all fields optional; omitted = blank) — the asset-page form's save |
| `DELETE /api/holding-plans/{instrument_id}` | deletes it |
| `GET /api/intel/assets/{instrument_id}` | gains `plan: HoldingPlan \| null` |

Routers validate → call the choke → shape. Every write fires the usual data-changed event so open
screens refresh. Demo lockdown: plan writes are book edits like any other (classified, allowed).

**Compatibility:** `PATCH /api/watchlist/items/{id}` with `intent`, and `update_watchlist_items`'s
`intent`, keep working — they resolve the item's instrument and call `apply_plan_change` with the
four intent fields. Watch item reads (`get_watchlist`, the pulse, the alerts dialog) project
`intent` from the plan, so the "Why you're watching" section edits the same record.

## Watch readers

`watch_pulse`, `watch_context`/`watch_rules` and `watch_analyst` read intent through
`intent_for_item(item)` instead of `item.get("intent")`, so a plan written on the asset page drives
the existing buy-zone and fair-value findings. `watch_intent` stays pure.

## Lucius tools (`ai/tools/holding_plans.py`)

| Tool | Class | Shape |
|---|---|---|
| `get_holding_plans` | READ_ONLY | `tickers?: string[]` → plans with ticker, name, currency and held |
| `set_holding_plans` | `mutates=("portfolio","watchlist")` | `updates: [{ticker, role?, thesis?, exit_if?, stop_below?, buy_below?, buy_above?, fair_value?, trim_above?, clear?: string[]}]` — merge semantics |
| `delete_holding_plans` | `mutates=("portfolio","watchlist")` | `tickers: string[]` |

Descriptions tell the model the values are the user's call: propose, then ask before saving.
`get_asset_context` includes the instrument's plan.

## UI

`components/intel/YourPlanCard.tsx`, rendered by `AssetDetailView` once and placed like Your
Position (desktop rail under Your Position; the mobile Overview slot). Built from `Panel`, `Badge`
styling, `InfoPopover`, `FormField`, `TextArea`, `TextField`, `SegmentedControl`, `Button`.
Read mode follows design 1a/2a; Edit swaps the body for the form (1b/2b) with Save/Cancel; server
validation errors show under the form. No plan yet → one line ("Write down why you own ACME.")
and an Add action that opens the empty form. Labels stay minimal: the thesis has no label in read
mode, and role explanations live behind the `i`.

## Tests

Store validation (each rule), choke merge/clear/delete/empty-deletes, migration (matched,
unmatched-stays, downgrade), compatibility routing (PATCH and tool `intent`), readers use the plan,
API round trip + asset-detail `plan`, tool schemas/classification goldens, the card (read, edit,
save, error), and an isolated E2E run on the asset page.
