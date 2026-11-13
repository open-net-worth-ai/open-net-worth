---
title: A bank connector can book its rows without AI, one sync mode at a time
status: Accepted
date: 2026-11-13
deciders: orchestrating agent (Claude) + repo owner
---

# A bank connector can book its rows without AI, one sync mode at a time

AMENDS [2026-09-05-connectors-are-data-sources-lucius-books](2026-09-05-connectors-are-data-sources-lucius-books.md)
on one point — "booking happens only in a Lucius turn". Everything else in it stands: connectors
stay read-only data sources, the fetch tools and the snapshot store are unchanged, and the
Lucius path stays available.

## Context

2026-09-05 made Lucius the only way a bank row reaches the ledger, accepting as a cost that "a
self-hoster with no model configured cannot book provider rows at all". Users now ask for
exactly that: link a bank, have its history and new rows booked, no AI. GoCardless users want
the 90 days their bank serves.

What made Lucius safe to put in the loop also makes a fixed booker safe: the ledger choke, not
the caller, enforces idempotency (the external-ref floor inside the write transaction), refuses
a previously deleted identity, and refuses a semantic duplicate. ADR 2026-11-10 already took
the same step for balances (`account_value_sync`: a no-AI scheduled read, runtime state, an
audit surface). The two 2026-09-05 objections to a scheduler — a clock nobody could see, and
corrections silently dropped — are answered here by a visible schedule and a review queue.

Banks limit reads: GoCardless and other PSD2 banks often allow 4 transaction reads per account
per day, so a sync, its "Sync now" and any Lucius fetch share a small budget.

## Decision

We will add a **data sync** for bank connectors, behind the flag `bank_data_sync`:

- Each data-sync-capable connector has one **sync mode**: Off, **Sync without AI** (`data`), or
  **Lucius**. Only `data` is stored (`bank_sync_settings`); Lucius mode is derived from an
  active Lucius Automation that syncs the connector. Choosing `data` pauses that Automation
  and says which; activating such an Automation while the connector is in `data` mode is
  refused with the reason.
- A run is provider-agnostic (`services/bank_data_sync/`): `connector_fetch.fetch_all` per
  opted-in linked account (one provider read each, no paging), then ONE
  `ledger.bulk_create_transactions(dedup_by_external_ref=True)` under the audit surface
  `bank_data_sync`, source `feed`. Pending rows are not booked. A row the user deleted is never
  forced back.
- Categories without AI: the user's rules and payee defaults, as the choke already applies
  them; else the bank's MCC hint only when it names an existing category. No category is ever
  created.
- An account's first import books the provider's full history and moves the Starting Balance
  back with `tracking_start.align_tracking_start`, so today's balance does not move.
- Each linked account has a Sync switch, on by default, stored as an opt-out keyed by the app
  account id; an unchecked account is never fetched.
- Anything the bank changes after booking (modified, vanished, pending→posted at a different
  amount) and any row the duplicate guard refuses becomes an item in `bank_sync_reviews`. The
  ledger changes only when the user accepts it.
- A per-connector schedule (GoCardless 12 h, others 6 h) and "Sync now", inside a per-account
  rolling 24 h read budget (4 for GoCardless and Enable Banking). Failures back off; nothing
  retries in a loop.
- Optional and off by default: after a run that booked uncategorised rows, a headless Lucius
  turn categorises exactly those rows with `book-transactions`. Booking never waits on it.
- Providers differ only by adapter config on `Provider` (`data_sync`, `sync_interval_hours`,
  `daily_reads`); GoCardless, Enable Banking and Plaid ship on.

## Consequences

### Positive

- Bank sync works with no model configured; the 90-day history arrives in one run.
- Corrections and withdrawals are kept until the user acts, instead of being reported once in a
  tool result a model may ignore.
- The 2026-09-05 follow-up "attach review" (a refused duplicate pairs with the hand-typed row)
  ships as the `duplicate` review item.

### Negative

- A second scheduler entry for bank rows exists again (2026-09-05 deleted the first). It is
  visible (Data sync tab, next run, run history), per connector, and off unless chosen.
- Payees invented from raw bank text are not cleaned without the Lucius follow-up; categories
  stay Uncategorized where no rule, default or hint applies.
- Lucius-mode detection is a heuristic over an Automation's instructions and allowed tools; an
  unusually worded sync Automation can be missed (the duplicate guard and ref floor still
  prevent double booking).

### Follow-ups

- SimpleFIN, Lunch Flow, Lunch Money: `data_sync=True` after each one's own QA.
- Optional pending booking; optional auto-apply of untouched corrections.

## Alternatives considered

- **Keep Lucius-only** — leaves users without a model with no bank sync.
- **Merge into the YNAB/Actual engine** — that engine reconciles two writable books against
  baselines; a bank is a read-only feed.
- **Store "lucius" as a mode** — a second copy of a fact the Automations table holds.
- **Auto-apply bank corrections** — rejected by the owner for v1: review only.
- **Page the existing `fetch`** — each page is a provider read and spends the daily limit.

## Agent provenance

- **Decided by:** repo owner (mode rule, opt-out, review-only, categories, first import,
  provider-agnostic booker), orchestrating Claude agent (tables, budget, backoff, derived Lucius
  mode, `fetch_all`)
- **Lane / branch:** `feat/bank-data-sync` (worktree `.claude/worktrees/bank-data-sync`)
- **Alternatives weighed:** Lucius-only · YNAB engine · stored lucius mode · auto-apply ·
  paged fetch
- **Driving artifacts:** `.claude/rfcs/bank-data-sync.md`, ADR 2026-09-05, ADR 2026-11-10
  (`account_value_sync`), GoCardless rate-limit docs
- **Why this way (one line):** the ledger choke already makes booking safe without a model; what
  users lacked was a caller that doesn't need one.

## Amendment 1 (2026-11-13, pre-ship security review)

- **Enable Banking runs every 12 h**, not 6: each of its reads also reads every account's
  details and balance, which PSD2 banks count against the same daily limit.
- **The read budget is counted at the fetch seam** (`connector_fetch`), so a Lucius fetch in chat
  spends the same per-account budget as the sync; the sync's own reads skip the account probe.
- **A review decision is claimed before it writes** (one decision per item, the version you saw),
  and **every failed run schedules its backoff**. A first-import alignment that didn't happen is
  retried on the next run.
- The `bulk_create_transactions` external-ref floor is checked BEFORE the write transaction, not
  inside it (Context above says otherwise, quoting 2026-09-05). Within this feature one run per
  connector at a time and the claimed decision close the gap; a Lucius booking racing a data-sync
  run on the same rows is the remaining window — a follow-up for the ledger choke.

## Amendment 2 (2026-11-14, owner: the data sync page offers no AI)

Data sync IS the no-AI path. Its page offers no Lucius choice: no Off / Sync without AI / Lucius
control, no "Let Lucius sync it instead", and no "Let Lucius categorise what's left". It is one
on/off switch, laid out as the budget apps' sync page. Someone who wants Lucius to sync a bank uses
a Lucius Automation. The one-mode rule is unchanged: turning this sync on pauses that bank's Lucius
sync Automation, an Automation that would sync a bank already on is refused (its message now
points at Automations → Data sync), and the page says when a Lucius Automation syncs the bank.

The categorising follow-up stays built (its setting, queue, route and tests) but is HIDDEN: the
owner wants to see what the sync categorises on its own first. It is off by default, so nothing
runs while the UI doesn't offer it.

A sync turned on once stays set up (`bank_sync_settings.set_up`): switching it off keeps its page
on the set-up view, as a budget app's does, instead of dropping back into the setup steps.
