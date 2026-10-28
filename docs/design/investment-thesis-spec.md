# Investment thesis — spec

**Status:** built (branch `feature/holding-plan`) · **Design:** Claude Design project "Holding plan — asset page" (frames 1a/1b
held, 2a/2b watched; built under the name "thesis") · **Decision:** ADR `2026-10-27-an-investment-thesis-lives-on-the-instrument`

## Problem

A watched ticker can carry *why* you watch it (`intent`: thesis, buy zone, fair value), but a
holding cannot carry *why you own it* or *what would make you sell*. The reason is lost the moment
a watched ticker becomes a holding, and nothing on the asset page tells you whether today's price
is where you said you'd act.

## Name

"Thesis", not "plan": "plan" already means the retirement **Plan** tab (baseline, scenarios) and a
commitment's **funding plan**, so "update my plan" was ambiguous to Lucius and to the user. "Investment
thesis" is the standard term for why you own something and what would break it, and nothing else in
the app uses it.

## What ships

One **thesis per instrument**, shown and edited on the asset page as a "Your Thesis" rail card.

| State | Card shows | Editable |
|---|---|---|
| Held | role chip (an `i` explains it), reason, "Thesis breaks if", price bar + levels with distance from today's price | role, reason, breaks-if, stop, buy zone, fair value, trim |
| Watched / not held | reason, price bar + buy zone and fair value | reason, buy zone, fair value |

Held vs watched is **derived** (a live position exists), never stored. A thesis survives buying,
selling and leaving every watchlist; it is deleted only on purpose.

Out of scope (follow-ups): new alerts for stop/trim crossings, role-aware risk checks, Lucius
checking news against `breaks_if`, a `reviewed_at` for staleness, any holdings-list or
notification change.

## Data model

New book-scoped table `investment_theses` (`book_payload_table`), store
`db/investment_thesis_store.py`. One row per (book, instrument).

| Field | Type | Rule |
|---|---|---|
| `id` | `th_<12hex>` | |
| `instrument_id` | str | required, FK by convention to `instruments.id` |
| `role` | `core` \| `satellite` \| `speculative` \| null | |
| `reason` | str ≤ 280 | why you own it / would buy it; trimmed; "" = unset |
| `breaks_if` | str ≤ 280 | the business fact that would break the thesis; trimmed; "" = unset |
| `stop_below` | float > 0 \| null | must be < `trim_above` when both set |
| `buy_below` | float > 0 \| null | top of the buy zone |
| `buy_above` | float > 0 \| null | needs `buy_below`, must be < it |
| `fair_value` | float > 0 \| null | |
| `trim_above` | float > 0 \| null | |
| `created_at` / `updated_at` | ISO UTC | |

Prices are in the instrument's `quote_currency` (what the asset page shows). A thesis whose every
field is blank is deleted rather than stored. The table is user intent: checkpoint-INCLUDED,
portable (export, import, backup), readable by `run_sql_query`.

## Write choke

`services/investment_theses.apply_thesis_change(change)` — the only writer (REST, Lucius tools,
the watchlist compatibility path):

- `{"op": "set", "instrument_id", "fields": {...}, "clear": [...]}` — merges `fields` over the
  existing thesis (creating it if absent), blanks every name in `clear`, validates the result as a
  whole, deletes the row if the result is empty. Unknown field names raise.
- `{"op": "delete", "instrument_id"}`.

Returns `{"thesis": dict | None}`. Validation lives in the store (`validate_thesis`) so no caller
can bypass it. Errors are `core.errors.ValidationError` with the user's words ("The floor must be
below the top of the buy zone.").

Reads: `thesis_for(instrument_id)`, `theses_by_instrument()`, `intent_for_item(item)` (the
watch-rule view of a thesis: `{thesis, buy_below, buy_above, fair_value}`, where the intent's
`thesis` key carries the stored `reason` — `INTENT_TO_THESIS` is the one mapping).

## Migration from watch intent

Alembic revision `202610290100_investment_theses`:

1. Create `investment_theses`.
2. For each book's `watchlists` payload, for every ticker item with a non-empty `intent`: find the
   instrument by the item's `instrument_id`, else a unique case-insensitive `price_source_id`
   match, else a unique bare `symbol` match (held crypto: "BTC" vs "bitcoin"). Insert a thesis
   with the intent's `thesis` (as `reason`) / `buy_below` / `buy_above` / `fair_value` and drop
   `intent` from the item.
3. An item is left untouched, `intent` kept and a warning logged, when no single instrument
   matches, when a stored price is not a number, or when another item already gave that
   instrument its thesis (no network in a migration). `intent_for_item` falls back to a kept
   intent, and the first thesis written for that instrument (asset page, Lucius, or an `intent`
   save) absorbs it and clears it from the item.

Downgrade copies each thesis's four intent fields back onto the matching watch item (`reason` as
the intent's `thesis`) and drops the table (held-only fields are lost on downgrade — stated in
the revision).

After the migration nothing writes `item["intent"]` for a ticker that has an instrument:
`watchlist_store` keeps accepting the key on input only to route it (below).

## API

| Route | Does |
|---|---|
| `GET /api/investment-theses` | `{"theses": [...]}`, each with `ticker` and `held` joined in |
| `GET /api/investment-theses/for` | `?instrument_id=` or `?ticker=` → `{instrument_id, currency, held, thesis}` for an editor off the asset page; 404 when no instrument |
| `PUT /api/investment-theses/{instrument_id}` | replaces the thesis with the body (all fields optional; omitted = blank) — the asset-page form's save |
| `DELETE /api/investment-theses/{instrument_id}` | deletes it |
| `GET /api/intel/assets/{instrument_id}` | gains `thesis: InvestmentThesis \| null` |

Routers validate → call the choke → shape. Every write fires the usual data-changed event so open
screens refresh. Demo lockdown: thesis writes are book edits like any other (classified, allowed).

**Compatibility:** `PATCH /api/watchlist/items/{id}` with `intent`, and `update_watchlist_items`'s
`intent`, keep working — they resolve the item's instrument and call `apply_thesis_change` with the
four intent fields. Watch item reads (`get_watchlist`, the pulse, the alerts dialog) project
`intent` from the thesis. The alerts dialog's "Why you're watching" section edits the thesis itself:
it loads it with `GET /api/investment-theses/for` (by instrument id, else ticker — plus currency and
held) and shows the same `ThesisEditor` component as the card, so both places offer every field.

## Watch readers

`watch_pulse`, `watch_context`/`watch_rules` and `watch_analyst` read intent through
`intent_for_item(item)` instead of `item.get("intent")`, so a thesis written on the asset page
drives the existing buy-zone and fair-value findings. `watch_intent` stays pure.

## Lucius tools (`ai/tools/investment_theses.py`)

| Tool | Class | Shape |
|---|---|---|
| `get_investment_theses` | READ_ONLY | `tickers?: string[]` → theses with ticker, name, currency and held |
| `set_investment_theses` | `mutates=("portfolio","watchlist")` | `updates: [{ticker, role?, reason?, breaks_if?, stop_below?, buy_below?, buy_above?, fair_value?, trim_above?, clear?: string[]}]` — merge semantics |
| `delete_investment_theses` | `mutates=("portfolio","watchlist")` | `tickers: string[]` |

Descriptions say the values are the user's own views, never the model's estimates. Whether a write
needs approval is `tool_permissions`' decision alone, never the description's.
`get_asset_context` includes the instrument's `thesis`.

## UI

`components/intel/YourThesisCard.tsx`, rendered by `AssetDetailView` once and placed like Your
Position (desktop rail under Your Position; the mobile Overview slot). Built from `Panel`, `Badge`
styling, `InfoPopover`, `FormField`, `TextArea`, `TextField`, `SegmentedControl`, `Button`.
Read mode follows design 1a/2a; Edit swaps the body for the form (1b/2b) with Save/Cancel; server
validation errors show under the form. No thesis yet → one line ("Write down why you own ACME.")
and an Add action that opens the empty form. Labels stay minimal: the reason has no label in read
mode, and role explanations live behind the `i`.

## Tests

Store validation (each rule), choke merge/clear/delete/empty-deletes, migration (matched,
unmatched-stays, downgrade, `thesis` → `reason`), compatibility routing (PATCH and tool `intent`),
readers use the thesis, API round trip + asset-detail `thesis`, tool schemas/classification
goldens, the card (read, edit, save, error), and an isolated E2E run on the asset page.
