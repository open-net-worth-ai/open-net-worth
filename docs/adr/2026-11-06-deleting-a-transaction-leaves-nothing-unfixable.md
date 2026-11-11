---
title: Deleting a transaction leaves nothing unfixable
status: Accepted
date: 2026-11-06
deciders: Claude (fix session) + repo owner
---

# Deleting a transaction leaves nothing unfixable

## Context

Duplicates can't be fully prevented: an automation can book one purchase through two tools, and the
guard can't catch every case. So users will delete rows, and a delete must be safe.

Each feature handled a deleted row on its own, or not at all. What a user could get stuck with:

- A lot or sale that refused every correction as a duplicate.
- A sale whose edit booked the deleted cash again.
- A commitment installment still "paid" by a row no longer in the ledger, and refused removal.
- An asset that stayed sold with its money missing, where selling it again did nothing or was
  refused.

A database cascade can't do this. A delete here is a soft delete, an `UPDATE`, so no foreign key
fires; most of these links are in JSON; a cascade's writes aren't checkpointed, so undo wouldn't
restore them; and it can't report what it changed.

## Decision

We will hold one rule in one place, `services/transaction_links.py`: **deleting a transaction never
leaves something the user can't fix.**

1. **`release` runs in every delete choke's own transaction** and returns what changed as
   `released_links`. A lot or sale loses its cash link, an installment goes back to due or upcoming,
   funding goes back to planned, an asset stays sold with no proceeds row, and an income record
   loses its cash row. If the cleanup fails, the delete rolls back.
   The app reversing its own cash row follows the same rule: undoing a sale, removing a lot or a
   position, and undoing a vest all release, because other records can point at that cash too.
2. **A merge keeps the money, so `move` sends links to the surviving row.** Undoing it returns only
   the rows it moved and leaves any link made since. Clearing them would mark a real payment as
   unpaid.
3. **An import rollback releases nothing.** It isn't a decision about the event, and a rollforward
   brings the row back.
4. **Corrections treat a dead link as no link**, because rollbacks and older data can still hold
   one. Editing a sale whose cash row is gone doesn't book it again unless a different account is
   chosen. Undo and removal don't delete a dead row a second time.
5. **An asset whose proceeds row is gone can be sold again** to rebook them, which brings the
   remembered identity back on purpose. With a live row it is still refused. A sold asset has no
   screen, so this goes through Lucius's `dispose_asset`.
6. **You see the effect first.** `linked_to` is the same walk with no write. It feeds the Lucius
   delete card and the register's confirm dialogs through `POST /api/transactions/delete-preview`.
   This is a warning, never a block, and a failed read shows the plain dialog.
7. **A new link can't skip this.** A guard test scans the stores for fields that point at a
   transaction and fails unless each is in `_LINKS` or in `NOT_RELEASED` with its reason.

## Consequences

### Positive

- The owner's stuck case can't recur for any link type, including ones added later.
- A user learns what a delete will change before confirming, and Lucius can repeat it afterwards.

### Negative

- Each delete and merge now takes five more table locks and scans those tables. This is fine at
  personal-book size.
- A book with an installment or funding source already pointing at a deleted row is not repaired,
  because a migration shouldn't silently turn "paid" into "due". The owner's book has none, and
  those can still be unlinked by hand.
- An installment marked paid and also linked goes back to unpaid when its row is deleted, since a
  hand-marked status can't be told apart from the one linking set.

### Follow-ups

- None from this decision. Why a correction edits its row is in the amendment below.

## Amendment: a correction edits the cash row it already has

A lot or sale correction used to delete its cash row and book a new one. That dropped the row's
category, cleared and reconciled state, source and external refs, so a re-delivered email could
book the purchase again, and the delete in the open transaction was invisible to the duplicate scan,
which refused the correction against the very row it was replacing.

We will edit the row in place through `ledger.update_transaction`, which now accepts `conn=` so the
record and its cash commit together. A base value moves only when the amount does, and at the row's
own exchange rate. A row in another currency than the lot keeps its own conversion. A row other
records share moves by the difference. A row is deleted only when the correction leaves no cash at
all, and a new one is booked, with the duplicate guard on, only when a record has none and an account
was chosen. A row that no longer has that leg, or was merged into a transfer, is left alone and the
correction still saves, so the correction and that row can disagree until the user fixes the row.
The description is rewritten only while it is still the app's own trade text: one the user or a
broker's email wrote stays when the figures change (found in hand UAT, 2026-11-10).

The amount on a reconciled row can now change, where before it was replaced. Nothing refuses that,
and the row stays marked reconciled. No linked row in the owner's book is reconciled.

The owner chose this on 2026-11-10: a corrected row stays reconciled. The alternatives were
un-reconciling the row on a correction, or refusing the correction.

## Alternatives considered

- **Database cascades or triggers** — rejected for the reasons in Context. The foreign key on
  `lots.txn_id` stays as a backstop for true removals.
- **Refuse the delete while something is linked** — rejected as the main path because it adds steps,
  and the owner asked for no dead ends. The warning keeps its benefit without the block.
- **Never delete, only reverse** — rejected as too heavy for a personal book.
- **Real deletes plus an archive table** — rejected as a large rewrite that wouldn't fix JSON links
  or reporting.

## Agent provenance

- **Decided by:** Claude fix session, with the rule set by the repo owner
- **Lane / branch:** `fix/lot-cash-link-foreign-key`
- **Alternatives weighed:** database cascade; refuse while linked; reverse only; archive table
- **Driving artifacts:** ADR `2026-11-06-a-lots-cash-link-is-a-real-foreign-key`;
  `tests/test_transaction_links.py`
- **Why this way (one line):** the app already owns the soft delete, so it owns the cleanup, in one
  place a test can hold every link to.
