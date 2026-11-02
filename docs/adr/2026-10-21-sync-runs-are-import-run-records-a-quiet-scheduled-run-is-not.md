---
title: Sync runs are import-run records; a quiet scheduled run is not one
status: Accepted
date: 2026-10-21
deciders: repo owner + Claude (senior-engineer lane)
---

## Context

ADR `2026-10-21-import-runs-are-persisted-records-archived-not-deleted` made every Import, Verify and
Preview a row of `import_runs`. The two-way sync (ADR
`2026-10-20-ynab-two-way-sync-reconciles-against-per-entity-baselines`) kept only a `last_run`
summary in its own state, so the owner could see that a sync sent three transactions but not which.
It also runs hourly: recording every run would bury the history in rows that say "Nothing changed".

## Decision

- **A sync run is an `import_runs` record of kind `sync`** (trigger `manual` or `scheduled`), with
  its changes itemized in the record's `detail`: transactions sent and pulled with a field-by-field
  before and after, Assigned moves, conflicts filed, deferred items, errors, requests used and
  totals. A "Sync now" opens its record at the start (so the history shows it running and an import
  cannot start beside it); a review item resolved in favour of the ledger, which writes to YNAB, is a
  record too.
- **A scheduled run is stored only when it has something to report**: a change, a review item, a
  deferral, or a failure that differs from the previous run's. A quiet one is not a record; the
  sync's `last_run` (the panel's "last run" line) still says it ran and carries `run_id` when a
  record exists. The same failure every hour is recorded once.
- **`last_run` stays, as the "last checked" line, not a second history.** It is a pointer plus a
  timestamp and summary; the records are the history.
- **Recording is a side channel.** `ynab_sync/recorder.py` is reached through a context variable, does
  nothing without a run and swallows its own failures, so no reconcile signature changed and a
  recording bug cannot fail a sync. Each list is capped at 2,000 items; past that the run keeps
  counting and reports `not_stored`.
- **One run in progress per provider** is shared with the importer: a sync cannot start while an
  import runs and vice versa, since both write the book.

## Consequences

- The Imports & sync history is the one place to see what the sync did, with the same archive and
  report machinery as an import.
- A quiet hour leaves no trace beyond the panel's last-run line; a sync that nothing noticed is
  not auditable from the history (the transaction audit log still records every write it made).
- A run holds up to ~2,000 items per list in one JSON column; the list endpoint never reads it.

## Alternatives considered

- Store every scheduled run — rejected: hourly "Nothing changed" rows drown the real ones.
- Store a quiet run and delete it afterwards — rejected: a record that appears and vanishes breaks
  "archived, not deleted" and the live running row it would need.
- Keep the itemization in the sync's own state — rejected: a second history with its own list,
  archive and report; the run records already do that.
- Thread a recorder argument through the reconcile — rejected: every signature in the hot path would
  change for a side channel; a context variable keeps the correctness path untouched.
