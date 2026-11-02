---
title: Two-way sync with YNAB
description: A data sync that brings your YNAB history in, keeps the two in step both ways, and keeps a report of every run.
category: features
order: 170
audience: [user]
status: stable
related: [lucius/automations, connectors/bank-brokerage, features/importing, operations/backup-and-restore, concepts/the-ledger]
keywords: [imports, sync, data sync, automations, two-way sync, schedule, ynab, migrate, migration, move, import history, run history, report, verify, preview, archive, re-check, interrupted]
---

# Two-way sync with YNAB

**Two-way sync with YNAB** brings your history in from YNAB and then keeps the two in step: accounts,
categories, envelopes, every transaction and each month's Assigned amounts, checked against YNAB's
own figures. It is a **data sync**: open **Automations**, switch to **Data sync** at the top and
pick it, or click **Two-way sync…** on the YNAB connector. A data sync follows fixed rules rather
than a Lucius prompt, so no AI is involved and it costs nothing to run. Every run is
saved, so you can come back to its report whenever you like, from any device.

> **Experimental, off by default.** Turn it on in [Settings → Developer → Experimental](settings.md#experimental-features)
> (the **Ynab import and sync** switch). Until then there is no **Data sync** tab and no **Two-way sync…**
> button. Turning it off again stops the sync and hides both, but keeps its settings, review list
> and history, so turning it back on carries on where it left off. The YNAB connector itself works
> the same either way.

This is separate from [importing a statement](importing.md), which reads one bank or card file, and
from the one-way **Sync YNAB** template, where Lucius fetches new YNAB transactions on a schedule.

## Set it up

Until it is set up, its page is three steps, each unlocked by the one before:

1. **Connect YNAB.** Click **Connect YNAB…** and paste your YNAB access token in the dialog that
   opens over this page, then **Save**. The token is kept on the YNAB connector (**Connectors →
   YNAB**), so you can change it from either place; **More on the Connectors page** in the
   dialog opens the full connector, for pausing or removing it. Where to find a token:
   [Bank & brokerage connectors](../connectors/bank-brokerage.md#ynab).
2. **Bring your YNAB history in.** Choose your plan and click **Preview**. Nothing is written. You
   see how many accounts, transactions, categories and budget months would come across, any
   warnings, whether the base currency would change (only in a book with no money data yet), and
   which unused starter categories would be removed. Then click **Import**. If the book already
   holds accounts that didn't come from YNAB you are asked to confirm first. The app takes a
   [backup](../operations/backup-and-restore.md) before it writes anything and names it in the
   report; if the backup fails, nothing is imported. **View live report** opens the run while it
   works. You can close the tab: the work continues on the server. Running again is safe: nothing
   is booked twice.
3. **Turn on the sync.** From then on it runs every hour by default. You can stop after step 2 if
   you only want your history.

Once it has been imported it appears under **Automations → Data sync** with its schedule and an
**N to review** tag when something needs you (the **Data sync** switch shows the same count). Its menu has **Sync now**, **Turn on** /
**Turn off** and **Open**.

## Once it's running

The page reads like any Automation: the switch turns it on or off (off keeps the plan it is paired
with), the next sync is next to it, and **Sync now** runs one on demand. Only one run happens at a
time.

- **Needs your review** comes first when something does: where both sides changed the same thing,
  with **Keep Open Net Worth's** and **Keep YNAB's**; and an account with a synced transaction
  dated before its Starting Balance, which moves only if you confirm **Move the Starting Balance**
  (or choose **Keep as history**).
- **Schedule**: every 15 or 30 minutes, hourly, every 6 hours, daily, or manual only, and the last
  sync and what it did ("3 sent to YNAB, 1 pulled, 1 needs review"), or why it failed.
- **YNAB plan**: the plan it pairs with and when it was imported.
- **⋯ → Re-check against YNAB** compares the book with YNAB's current figures, without writing.

How the sync decides, what it sends and its limits are in
[Bank & brokerage connectors](../connectors/bank-brokerage.md#ynab).

## History

Every preview, import, re-check and sync is listed newest first with when it started and a one-line
outcome; **Needs attention** shows only the ones that failed or were interrupted. Click one to open
its report. **Archive** (on the report) hides a finished run from the list; nothing is deleted, and
a run in progress can't be archived.

If the server stopped while a run was in progress, the run is kept and marked **Interrupted**. Run
it again to finish: everything already booked is recognised.

A sync you start with **Sync now** is always a run in the history, and so is a scheduled sync that
has something to report: a change, a review item, a deferral or a new failure. A scheduled sync that
finds nothing to do (most hourly runs) is not added; the **Schedule** section's last-sync line shows
it ran. The same failure repeated every hour is recorded once. Choosing **Keep Open Net Worth's** on
a review item that sends a change to YNAB is a run too.

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
