---
title: Imports & sync
description: Bring your history in from another budgeting app, check it against the original, and read the report of every run afterwards.
category: features
order: 170
audience: [user]
status: stable
related: [connectors/bank-brokerage, features/importing, operations/backup-and-restore, concepts/the-ledger]
keywords: [imports, sync, two-way sync, schedule, ynab, migrate, migration, move, import history, run history, report, verify, preview, archive, re-check, interrupted]
---

# Imports & sync

**Imports** (in the sidebar, under Connectors) is where you bring your history in from another
budgeting app. Today that is **YNAB**: accounts, categories, envelopes, every transaction and each
month's Assigned amounts, checked against YNAB's own figures. Every run is saved, so you can come
back to its report whenever you like, from any device.

This is separate from [importing a statement](importing.md), which reads one bank or card file.

## Connection

Your YNAB access token stays on the YNAB connector (**Connectors → YNAB**). This page only shows
whether a token is saved and links there; without one, the import is disabled. To save a token, see
[Bank & brokerage connectors](../connectors/bank-brokerage.md#ynab).

## Import a plan

1. Choose your plan and click **Preview**. Nothing is written. You see how many accounts,
   transactions, categories and budget months would come across, any warnings, whether the base
   currency would change (only in a book with no money data yet), and which unused starter
   categories would be removed.
2. Click **Import**. If the book already holds accounts that didn't come from YNAB you are asked to
   confirm first. The app takes a [backup](../operations/backup-and-restore.md) before it writes
   anything and names it in the report; if the backup fails, nothing is imported. Progress shows on
   the page, and **View live report** opens the run while it works. You can close the tab: the work
   continues on the server.
3. **Re-check** compares the book with YNAB's current figures at any time, without writing.

Running again is safe: nothing is booked twice.

## Sync

The **Sync** panel keeps this book and your migrated YNAB plan in step on a schedule: transactions
and each month's Assigned, in both directions. Pick the plan, choose a schedule (every 15 or 30
minutes, hourly, every 6 hours, daily, or manual only), switch **Sync automatically** on, or click
**Sync now**. The panel shows the last run, the next run and the last result, and lists what needs
your review: where both sides changed the same thing, with **Keep Open Net Worth's** and **Keep
YNAB's**. How the sync decides, what it sends and its limits are in
[Bank & brokerage connectors](../connectors/bank-brokerage.md#ynab).

## Run history

Every preview, import, re-check and sync is listed with when it started, how long it took, how it ended
and a one-line outcome. Filter by kind (All, Import, Verify, Preview, Sync), or open one with **Open**. **Archive** (in the row's menu)
hides a finished run from the list; tick **Show archived** to see them again and **Unarchive** to
bring one back. Nothing is deleted, and a run in progress can't be archived.

If the server stopped while a run was in progress, the run is kept and marked **Interrupted**. Run
it again to finish: everything already booked is recognised.

A sync you start with **Sync now** is always a run in the history, and so is a scheduled sync that
has something to report: a change, a review item, a deferral or a new failure. A scheduled sync that
finds nothing to do (most hourly runs) is not added; the panel's last-run line shows it ran. The same
failure repeated every hour is recorded once. Choosing **Keep Open Net Worth's** on a review item
that sends a change to YNAB is a run too.

## Reading a report

- **Setup** (imports): whether this was a first import or a re-import, and the backup taken first.
- **Results**: per item (accounts, categories, envelopes, transactions, assigned months), how many
  were created, were already there, were refused or failed. A refused transaction is one the book
  treats as a duplicate or that you deleted earlier.
- **Needs attention**: each problem with the reason and what you can do about it.
- **Verification**: how many checks were made and how many match. The ones that differ come first,
  with YNAB's figure, the book's figure and the difference; **Show all** lists those that match. A
  transaction count that differs usually means a row wasn't booked; an envelope or Ready to Assign
  difference means the two apps' budget rules gave different numbers for your history.
- **Log**: a timestamped account of what the run did, open while it runs and folded once it ends.

### Sync runs

A sync run reports what it did, leaving out empty sections (a run that changed
nothing says **Nothing changed**):

- **Counts**: sent to YNAB, pulled in, Assigned changes, conflicts filed, deferred, errors, and how
  many YNAB requests the run used.
- **Transactions**: **Sent** and **Pulled** tabs with date, payee, account, amount and the change:
  *Created*, *Deleted*, or one line per field such as `Amount -60.00 → -62.50`.
- **Assigned**: category, month, before and after, and the direction.
- **Conflicts filed**: both versions; the ones still open carry the Keep buttons.
- **Deferred**: what waits for the next run because of YNAB's request limit. **Errors**: what YNAB
  or this book refused, with the reason.

Lists show 50 at a time. A run keeps at most 2,000 items of each kind; if there were more, the report
says how many were not stored (the counts are always complete).

A run that failed shows why at the top, with everything it captured before it stopped.

Run history belongs to the book it describes: **Reset** clears it, and it is not part of an export.
It never contains your YNAB token.
