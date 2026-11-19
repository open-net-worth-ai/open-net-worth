---
title: A lot's cash link is a real foreign key
status: Accepted
date: 2026-11-06
deciders: Claude (fix session) + repo owner
---

# A lot's cash link is a real foreign key

## Context

A lot's link to its purchase cash row, `txn_id`, lived only inside the lot's JSON `payload`. The
investment tables are deliberately loosely coupled ("opaque ids Python-filtered, never a SQL
JOIN"), so nothing kept the link true.

The scar: an automation booked one purchase twice. The owner deleted the lot-linked row as the
duplicate, and the lot kept pointing at it. Correcting that lot's fee then soft-deleted the dead
row again and tried to book a new one, and the duplicate guard refused it against the surviving
row. The refusal also escaped `update_lots`, which ended the batch and never told Lucius why.

The owner asked for the link to become a real column so it can be a foreign key. A foreign key
alone cannot fix this: a delete here is a soft delete, an UPDATE setting `deleted`, so the row
still exists and no key fires.

## Decision

We will make `lots.txn_id` a real nullable column, indexed, with a foreign key to
`transactions.id` `ON DELETE SET NULL`, and keep the link true in application code as well:

1. **The column is the authority.** `LotStore` writes it with every payload write and every read
   takes it. The payload keeps a copy for raw JSON readers, which `SET NULL` can leave stale.
2. **A deliberate delete clears the link** in its own transaction: `bulk_delete_transactions`,
   `delete_transactions_where`, and the account force-delete sweep (explicitly, so the change is
   checkpointed, where the FK's own write would not be). This is one case of the wider rule in ADR
   `2026-11-06-deleting-a-transaction-leaves-nothing-unfixable`, which also moves links on a merge.
3. **An amend treats a link to a deleted or merged row as no link.** It saves the correction,
   clears the link, and leaves cash alone unless the caller names a funding account. This stays
   after (2) because an import rollback keeps its link on purpose, and because the migration does
   not rewrite existing soft-deleted links.
4. **`update_transaction` never deletes and re-inserts a row.** `TransactionStore.replace_at_end`
   rewrites it under its id and keeps the same move-to-end ordering. The old delete + upsert would
   have fired `SET NULL` on every edit of a cash row.
5. **`update_lots` returns a choke's refusal as that item's result**, single or batched. Each item
   is its own unit of work, so a refused one rolls back whole and the rest still apply.
6. **A correction doesn't re-book a live cash row.** Doing so was refused whenever that row came
   from a different source, because the duplicate scan couldn't see the delete in the open
   transaction: every fee or cost correction on an automation-booked lot failed. ADR
   `2026-11-06-deleting-a-transaction-leaves-nothing-unfixable` (amendment) edits the row in place.

A backup bundle is untrusted and can be partial, so `investments_io.import_lots` imports a link to
a row the book doesn't hold as no link instead of failing the restore. The migration does the same
for links to rows that no longer exist.

## Consequences

### Positive

- A lot can no longer point at a row that doesn't exist, and "which lots use this row" is an
  indexed query.
- A correction to a lot whose cash row was deleted now works and says what it did.
- A refusal reaches Lucius as a reason, instead of a failed turn and a half-applied batch.

### Negative

- `lots` is now the one payload table with a promoted column, so `txn_id` exists twice and only the
  column counts.
- `metadata.sorted_tables` now orders `transactions` before `lots`, and writing a link to a row
  that doesn't exist raises `IntegrityError`.
- `services/returns` reads the link as "funded": an `opening` lot without one is priced at market,
  and a `transfer_in` without one is not an external flow. Deleting such a lot's cash row now
  changes its returns from then on, which matches what the ledger says. A `buy` is unaffected.
- Rewinding to a checkpoint from before this migration restores a lot image with no column value,
  so that lot reads as having no link. A later edit to it then leaves its cash alone.

### Follow-ups

- `realized_events.txn_id` and `vest_events.txn_id` are the same kind of link and stay
  payload-only. Move them only if they bite.

## Alternatives considered

- **Column and FK only** — rejected because a soft delete never fires a foreign key, so the bug
  would remain.
- **Application fixes only, no column** — simpler, and it would have fixed the bug. The owner chose
  the column, and it also gives the database's guarantee and an indexed reverse lookup.
- **`ON DELETE RESTRICT` or a deferred key** — rejected because each `_apply_bridged_import` commits
  per file, so a restore's wipe of `transactions` would fail while `lots` still pointed at it.
- **A payload-authoritative mirror column** — rejected because a stale payload would then be written
  back into the column on the next `touch` and trip the key.

## Agent provenance

- **Decided by:** Claude fix session, with the column and foreign key asked for by the repo owner
- **Lane / branch:** `fix/lot-cash-link-foreign-key`
- **Alternatives weighed:** column + FK alone; application fixes alone; RESTRICT or deferred key;
  payload-authoritative mirror
- **Driving artifacts:** the duplicate refusal on an approved `update_lots` card;
  `tests/test_lot_cash_link.py`
- **Why this way (one line):** the key guards against true removal and the delete chokes guard
  against soft delete, so the link holds on both paths.
