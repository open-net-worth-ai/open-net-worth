---
title: Delete permanently erases a transaction's contents; an ordinary delete stays soft
status: Accepted
date: 2026-10-17
deciders: repo owner (chose the explicit verb) + Claude (built it)
---

# 2026-10-17. Delete permanently erases a transaction's contents; an ordinary delete stays soft

Amends [2026-09-06-force-delete-account-hard-removes-its-cascaded-transactions](2026-09-06-force-delete-account-hard-removes-its-cascaded-transactions.md):
its MEDIUM-5 full-row `before` images in `txn_audit` are no longer kept. Leaves
[2026-09-02-a-deleted-identity-is-remembered](2026-09-02-a-deleted-identity-is-remembered.md)
in force, minus its display fields after an erase.

## Context

A privacy audit created a transaction with a sensitive memo, deleted it and force-deleted its
account, then searched the database. The memo, amount, date and account were still in
`txn_audit` (the cascade writes the full row as `before`, on purpose), the payee remained, and
`rejected_identities` kept the date, description and amount for display. An ordinary delete is a
soft delete. The row itself keeps everything behind `deleted`, so checkpoint undo, the re-import
guard and merge tombstones keep working, and 86 readers in `services/` depend on that. The app
had no way for an owner to make a transaction actually go away.

The owner was offered three options: an explicit verb, an automatic purge 30 days after a soft
delete, or erasing only on an account force-delete. They chose the explicit verb, with the
account force-delete erasing the same way. Nothing disappears unless someone asks.

## Decision

- **Two verbs.** **Delete** stays the soft delete, unchanged. **Delete permanently**
  (`services/transaction_purge.purge_transactions`, `POST /api/transactions/purge`) is the only
  erase. It is a REST and UI action, not an AI tool: Lucius can't erase, and it is locked in the
  public demo, where every visitor shares one book.
- **One unit of work** (`locked_unit_of_work` over transactions and `TRACE_TABLE_NAMES`):
  1. The ordinary delete choke runs first on the same conn for live rows, so its refusals still
     apply (a Starting Balance is refused) and the re-import guard is written as a plain delete
     writes it.
  2. Refused outright while an investment or commitment fact names the row or one of its merge
     tombstones (`_BLOCKING_TABLES`: lots, realized/income/vest events, grants, owned assets,
     options, positions, commitments).
     A soft delete leaves the row for them to read; a purge would not.
  3. Merge tombstones folded into the row go with it, transitively: they are the same payment
     from another source.
  4. Hard delete (`TransactionStore.delete`; splits and refs cascade).
  5. `TransactionAuditStore.redact_for_txns` cuts every event to `id/txn_id/op/ts/actor/
     surface/source`, then adds ONE `delete` event noting "Deleted permanently". The trail still
     says that something happened, when, and by whom, never what.
  6. `rejected_identities` drops `date/description/amount/currency`. The `(source, ref,
     movement)` key and `txn_id` stay, so a re-delivery is still refused as `previously_deleted`.
  7. Every `write_log` image of the row, its children and every other row this erase removes
     (step 8) becomes `None` ("did not exist"), so `_replay_write_log` deletes the pk and never
     re-inserts it: an Undo of the turn that booked it must not bring back the email snapshot
     it promoted or the payee it created. A rejected-identity image is scrubbed instead, so a
     restore still restores the guard.
  8. Rows that exist only about it go: `match_reviews`, `match_decisions`, `corrections`, and
     both sides of `merge_journal` (the un-merge before-image). The email snapshot (under its
     internal id, or a pre-internal-id one under its raw Message-ID) and the bank or broker
     snapshot (`connector_snapshots`, `source:ref`) go when no remaining transaction holds that
     reference. A later fetch may stash the provider's copy again, unbooked. The payee goes when nothing else names it and
     the owner never shaped it (rules, extra aliases, hidden, autocategorize off). A learned
     default category doesn't count, because booking one categorised row writes it.
- **After commit:** receipts linked only to it are deleted through `apply_receipt_change`
  (whose file unlink must follow its own commit); shared ones are unlinked. Any that fail are
  returned as `receipts_not_cleaned`, never hidden. SQLite runs the purge with `secure_delete`
  on and then VACUUMs (plus `wal_checkpoint(TRUNCATE)`), so freed pages don't keep the bytes. A
  checkpoint alone is not enough: a page the app freed EARLIER, with `secure_delete` off — the
  automation outbox copies every transaction payload, then frees it after delivery — still
  carries the bytes into the main file; VACUUM rebuilds the file from live pages only.
- **The account force-delete erases its cascade** with the same `erase_traces` on its own conn,
  holding the same locks, and runs `after_commit` too. It reads the rows to erase under the lock,
  and takes the merge tombstones folded into them (another account's copy of the same payment),
  refused like a purge while a fact names one.
- **Cost.** Each table is read once per erase, whatever the number of rows (`_naming` matches
  every quoted value against the id set), so an account of thousands of transactions does not
  hold the ledger lock for one scan per id. `secure_delete` is on before the first write and
  back to its old value after, since the pooled connection outlives the purge.

## Consequences

- A test searches every column of every table, and the raw SQLite file, for a marker after an
  erase (`tests/test_transaction_purge.py`). A new table that copies transaction text fails it.
- Out of scope, and said in the product docs: files the owner uploaded (a statement still shows
  the line), Lucius conversations that mention it, `tool_call_log`, and backups made before the
  erase (until their retention runs out).
- An erased row can't be un-merged or restored from a checkpoint. That is the point.
- `audit.trace` of an erased id shows only stubs and the "Deleted permanently" note.
