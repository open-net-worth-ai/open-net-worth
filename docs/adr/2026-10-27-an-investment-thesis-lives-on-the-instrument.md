---
title: An investment thesis lives on the instrument — one per instrument, outside the watchlist
status: Accepted
date: 2026-10-27
deciders: orchestrating Claude agent + repo owner
---

# An investment thesis lives on the instrument

## Context

A watched ticker could carry `intent` (thesis, buy zone, fair value) inside its watchlist item.
A holding had nowhere to say why it is owned or what would make the owner sell. Two facts ruled
out extending the watch item:

- A held ticker's watch item is the auto "My holdings" entry, and `sync_from_holdings` deletes it
  on a full sell — a thesis stored there dies with the position, exactly when it matters.
- The same instrument can be held in several accounts, so a per-position field would fork one
  reason into several that drift apart.

## Decision

- New book-scoped table `investment_theses` (`db/investment_thesis_store.py`): one row per
  instrument with `role` (core / satellite / speculative), `reason`, `breaks_if` (free text — the
  business fact that would break the thesis, not a number), and five prices: `stop_below`,
  `buy_below`, `buy_above`, `fair_value`, `trim_above`. Held vs watched is derived from live
  positions, never stored. Checkpoint-included, portable, SQL-readable.
- One writer: `services/investment_theses.apply_thesis_change` (merge + `clear`, a blank thesis
  deletes itself), rules in `investment_thesis_store.validate_thesis`. REST
  `PUT/DELETE /api/investment-theses/{id}` replaces; Lucius's `set_investment_theses` merges.
- Watch `intent` becomes a view of the thesis: the watch rules, pulse and analyst read
  `investment_theses.intent_for_item` / `with_intents`; the watchlist `intent` PATCH and
  `update_watchlist_items.intent` write the thesis's four intent fields (checked before the item is
  written, so a refusal changes neither). The intent's `thesis` key is the stored `reason`
  (`INTENT_TO_THESIS`). Only a ticker with no instrument row yet keeps `intent` on the item (no
  network on that path); revision `202610290100_investment_theses` moves every matchable intent
  and leaves the rest, and the first thesis written for that instrument absorbs it. Ticker →
  instrument matching is `price_source_id`, then a unique bare symbol, everywhere (crypto).
- The asset page shows it as the "Your Thesis" rail card; the role's meaning sits behind an `i`.
- **Named "thesis", not "plan".** It was built as a "holding plan", but "plan" already names the
  retirement Plan tab (`get_plan_baseline`, `create_plan_scenario`) and a commitment's funding plan
  (`set_funding_plan`), so "update my plan" was ambiguous. Renamed before merge, while tool names
  were still free to change.

## Alternatives rejected

- **Field on the position** — forks one reason per account, and the watched state has no position.
- **Keep it on the watch item** — deleted on a full sell (above).
- **A numeric `breaks_if`** — most theses break on business facts, not prices; prices have their
  own fields so the app can check them without a model.
- **Keep "plan" with a longer card title** — the tool names were already qualified, but what the
  user types ("my plan") was not.

## Consequences

- Follow-ups not built: alerts on stop / trim crossings, role-aware concentration checks, Lucius
  checking news against `breaks_if`, a `reviewed_at` so a stale thesis can be flagged.
- Downgrade restores the four intent fields onto watch items and loses role, `breaks_if`, stop
  and trim.
