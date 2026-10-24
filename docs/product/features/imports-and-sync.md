---
title: Imports & sync
description: Bring your history in from another budgeting app, check it against the original, and read the report of every run afterwards.
category: features
order: 170
audience: [user]
status: stable
related: [connectors/bank-brokerage, features/importing, operations/backup-and-restore, concepts/the-ledger]
keywords: [imports, ynab, migrate, migration, move, import history, run history, report, verify, preview, archive, re-check, interrupted]
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

## Run history

Every preview, import and re-check is listed with when it started, how long it took, how it ended
and a one-line outcome. Filter by kind, or open one with **Open**. **Archive** (in the row's menu)
hides a finished run from the list; tick **Show archived** to see them again and **Unarchive** to
bring one back. Nothing is deleted, and a run in progress can't be archived.

If the server stopped while a run was in progress, the run is kept and marked **Interrupted**. Run
it again to finish: everything already booked is recognised.

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

A run that failed shows why at the top, with everything it captured before it stopped.

Run history belongs to the book it describes: **Reset** clears it, and it is not part of an export.
It never contains your YNAB token.
