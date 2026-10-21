---
title: Import runs are persisted records, archived not deleted
status: Accepted
date: 2026-10-21
deciders: repo owner + Claude (senior-engineer lane)
---

## Context

ADR `2026-10-19-ynab-migration-is-a-one-shot-job-beside-the-importer` kept the migration job in
memory: one job at a time, polled by the connector card, lost on a restart. A real import takes
minutes and its report (what was booked, what was refused, which checks differ from YNAB) is the
evidence the owner uses to decide whether to trust the book. Reloading the page, opening it on
another device, or a server restart threw that evidence away, and the next run replaced it.

## Decision

Every Import / Verify / Preview of a provider's importer is a **run record**: a row of `import_runs`
(`services/import_runs.py`, `db/import_run_store.py`), created the moment the run starts and
updated by the worker as it goes.

- **The stored report is shaped for reading**, not a dump of internals: counts per item
  (`created / existing / refused / failed`), problems and warnings as `{item, reason, hint}` with a
  plain-language hint, verification split into mismatches and matches with a pass summary, a
  timestamped log, the backup name, and the book state the run started from. A record is generic
  over `provider`, `kind` and `trigger`, so a later two-way sync adds a kind rather than a table.
- **A run is never deleted; it is archived.** `archived_at` hides a finished run from the default
  list and `Unarchive` brings it back. A run in progress cannot be archived.
- **A restart leaves an honest record.** At boot a row still `running` becomes `interrupted`
  (`recover_interrupted`), keeping everything it captured. Re-running finishes the work, because
  every write is idempotent by YNAB id.
- **At most one run per provider is `running`**, enforced when the record is created (a second start
  is a 409), as the in-memory job did.
- **No credential is stored.** The token is read from `YnabConfigStore` inside the worker and never
  enters a record.
- **The table is operational, not book data.** Checkpoint-excluded (the run is written outside any
  turn), non-restorable (restoring the backup an import took must not erase the record of that
  import), wiped by Reset (it describes a book that no longer exists), not portable.

## Consequences

- The Imports & sync screen lists history and opens any run as a report from any device; the
  connector card only links there.
- A run holds its whole verification (a long plan has ~1,000 checks), so a row can reach a few
  hundred KB. The lookup fields are real columns and the heavy parts sit in a `detail` column, so
  the list and the polls are SQL over columns and never read it; only the detail route does.
- Records accumulate until archived. Archiving is a view filter, not a size bound: a bounded
  retention policy is left until the volume says it is needed.
- The detail is rewritten whole on each update (the worker's log and counts grow during a run);
  fine at one worker and one run, but a run that logged per transaction would need appends.

## Alternatives considered

- Keep the in-memory job and add only the page — rejected: the report is lost on reload or restart,
  which is exactly what the page exists to fix.
- A file per run under the data directory — rejected: all queryable data goes through the
  repository layer, and Reset, backup and the lock would each need their own handling.
- Delete a run instead of archiving it — rejected: the report is evidence; hiding it is enough to
  declutter, and a mistaken click should be reversible.
- A bare payload table (`id, payload`) — rejected: every poll would load every run's log and
  verification to filter by status. Columns for what is looked up, JSON for the rest of the report,
  whose shape is still moving (a sync kind is next).
