---
title: A budget app's rows are kept as the source of what it booked
status: Accepted
date: 2026-11-17
deciders: orchestrating agent (Claude) + repo owner
---

# A budget app's rows are kept as the source of what it booked

## Context

A bank connector keeps the row the bank sent in `connector_snapshots`, so a booked transaction can be
traced to it and the drawer can show it. The YNAB and Actual imports and syncs read a whole budget
and booked from it, and kept only the engine's reshaped copy (`*_sync_mirror`), which drops fields
(Actual's `financial_id`, `reconciled`, raw notes, YNAB's `import_id`) and loses a row deleted in the
app. The one-way YNAB fetch tool already stashes YNAB rows under `ynab` with the same ids.

## Decision

- **The same store.** Every import and sync read stashes one row per app transaction under the app's
  source, keyed by the id the ledger row's `external_refs` carry (`budget_app_snapshots`,
  `connector_snapshots.stash_synced`). The drawer lists them with the bank rows; the backup, the
  sanitised backup and Lucius's SQL access treat them as they treat any snapshot row.
- **The app's own row.** YNAB's is the plan export's row with a split's lines nested under it, and no
  field of ours. Actual's is its `transactions` row as read, before `shape`, with a split's lines under
  `children`. A split is one ledger row, so its lines are not refs of their own.
- **Quiet.** It writes only new or changed rows in one transaction, reports no changes, never calls
  `note_missing` and never prunes, since the sync has its own change and delete handling and its
  mirror already holds the same set. Any difference in the raw replaces it (a note moves no hashed
  field). A failure is logged and the run goes on. The stash happens at the read, before the run's
  writes, so it holds no lock the run needs.
- **Dedup is untouched.** The rows carry no `provider_account_id`, so `emitter_for` still answers
  None for them and no booking gains an `emitter`. The `ynab` fetch tool's rows keep theirs: a row
  without one takes it from the first fetch that has one. `normalized` is built the way the fetch tool
  builds it, so the two never flip each other's `content_hash`.
- **Existing rows.** A re-stash marks every row whose ref is booked as referenced, so books imported
  before this change gain their evidence on the next sync.

## Consequences

A preview stashes nothing; an import and every sync do. A transaction deleted in the app keeps its
snapshot. A YNAB split whose lines changed while its parent did not is not re-stashed until the parent
is sent again.
