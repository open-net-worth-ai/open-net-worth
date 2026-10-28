---
title: A holding plan lives on the instrument — one per instrument, outside the watchlist
status: Accepted
date: 2026-10-27
deciders: orchestrating Claude agent + repo owner
---

# A holding plan lives on the instrument

## Context

A watched ticker could carry `intent` (thesis, buy zone, fair value) inside its watchlist item.
A holding had nowhere to say why it is owned or what would make the owner sell. Two facts ruled
out extending the watch item:

- A held ticker's watch item is the auto "My holdings" entry, and `sync_from_holdings` deletes it
  on a full sell — a plan stored there dies with the position, exactly when it matters.
- The same instrument can be held in several accounts, so a per-position field would fork one
  reason into several that drift apart.

## Decision

- New book-scoped table `holding_plans` (`db/holding_plan_store.py`): one row per instrument with
  `role` (core / satellite / speculative), `thesis`, `exit_if` (free text — a business condition,
  not a number), and five prices: `stop_below`, `buy_below`, `buy_above`, `fair_value`,
  `trim_above`. Held vs watched is derived from live positions, never stored. Checkpoint-included,
  portable, SQL-readable.
- One writer: `services/holding_plans.apply_plan_change` (merge + `clear`, a blank plan deletes
  itself), rules in `holding_plan_store.validate_plan`. REST `PUT/DELETE /api/holding-plans/{id}`
  replaces; Lucius's `set_holding_plans` merges.
- Watch `intent` becomes a view of the plan: the watch rules, pulse and analyst read
  `holding_plans.intent_for_item` / `with_intents`; the watchlist `intent` PATCH and
  `update_watchlist_items.intent` write the plan's four intent fields (checked before the item is
  written, so a refusal changes neither). Only a ticker with no instrument row yet keeps `intent` on
  the item (no network on that path); revision `202610271200_holding_plans` moves every matchable
  intent and leaves the rest, and the first plan written for that instrument absorbs it. Ticker →
  instrument matching is `price_source_id`, then a unique bare symbol, everywhere (crypto).
- The asset page shows it as the "Your Plan" rail card; the role's meaning sits behind an `i`.

## Alternatives rejected

- **Field on the position** — forks one reason per account, and the watched state has no position.
- **Keep it on the watch item** — deleted on a full sell (above).
- **A numeric `exit_if`** — most theses break on business facts, not prices; prices have their own
  fields so the app can check them without a model.

## Consequences

- Follow-ups not built: alerts on stop / trim crossings, role-aware concentration checks, Lucius
  checking news against `exit_if`.
- Downgrade restores the four intent fields onto watch items and loses role, sell condition, stop
  and trim.
