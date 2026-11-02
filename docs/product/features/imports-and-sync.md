---
title: YNAB data sync
description: A data sync that brings your YNAB history in, keeps the two in step from YNAB, to YNAB or both ways, and keeps a report of every run.
category: features
order: 170
audience: [user]
status: stable
related: [lucius/automations, connectors/bank-brokerage, features/importing, operations/backup-and-restore, concepts/the-ledger]
keywords: [imports, sync, data sync, automations, two-way sync, one-way sync, direction, schedule, ynab, migrate, migration, move, import history, run history, report, verify, preview, archive, re-check, interrupted]
---

# YNAB data sync

The **YNAB** data sync brings your history in from YNAB and then keeps the two in step: accounts,
categories, envelopes, every transaction and each month's Assigned amounts, checked against YNAB's
own figures. After the import, **transactions**, **categories and category groups** (both ways) and
the **Assigned of an envelope that holds one category** stay in step. Changes can flow **Both ways**, only **From YNAB**, or only **To YNAB**. It is a
**data sync**: open **Automations**, switch to **Data sync** at the top and pick **YNAB**. A data
sync follows fixed rules rather
than a Lucius prompt, so no AI is involved and it costs nothing to run. Every run is
saved, so you can come back to its report whenever you like, from any device.

> **Experimental, off by default.** Turn it on in [Settings → Developer → Experimental](settings.md#experimental-features)
> (the **Ynab import and sync** switch). Until then there is no **Data sync** tab. Turning it off
> again stops the sync and hides the tab, but keeps its settings, review list
> and history, so turning it back on carries on where it left off. The YNAB connector itself works
> the same either way.

This is separate from [importing a statement](importing.md), which reads one bank or card file, and
from the one-way **Sync YNAB** template, where Lucius fetches new YNAB transactions on a schedule.

## Set it up

Until it is set up, its page is two steps, the second unlocked by the first:

1. **Connect YNAB.** Click **Connect YNAB…**: the YNAB connector opens right over this page, the
   same panel as **Connectors → YNAB**. Paste your access token and **Save**. Once a token is
   saved, **Change** beside "Token saved" opens it again — use it if step 2 says YNAB refused the
   token. Where to find a token:
   [Bank & brokerage connectors](../connectors/bank-brokerage.md#ynab).
2. **Bring your plan in and keep it in sync.** Choose your plan and click **Review…**. It reads
   the plan afresh and opens it in one window; nothing is written. Under the title, one line says
   what the plan holds (currency, accounts, transactions, categories, budget months), then a note
   for anything the import would change in this book: a base-currency change (only in a book with
   no money data yet), unused starter categories it would remove, accounts here that didn't come
   from YNAB (they stay as they are and the plan is added beside them), and any warnings. A plan
   in another currency than a book that already holds data can't be imported, and the note says
   so. Three tabs show the plan itself:
   - **Accounts**: each account with its type, where it goes in this book, how many
     transactions it has and its opening balance;
   - **Categories**: each group with its categories, and where each one goes in this book (below);
   - **Transactions**: newest first (date, account, payee, category, amount; a long history shows
     its newest 500, and the import brings them all).

   **Already have some of these accounts here?** In the Accounts tab, **In this book** says
   where each YNAB account goes: **New account** (the default), or **Use** one of your accounts
   that fits it — open, in the plan's currency, and of a matching type (a budget account goes into
   an account of its own type: cash into cash, a card into a card; a tracking account into any
   off-budget asset account, a tracking loan into a loan). A closed YNAB account always gets a new
   one. Choosing an account checks which of its transactions YNAB also has, and the line under
   it says how many are **already there** (matched, so they are not booked twice), how many are
   **added**, and how many **to check**. A matched transaction keeps your payee, category and
   memo; YNAB only fills the ones you left blank. The account keeps its name, opening balance and
   history. Transactions to check are ones that may be the same but the app isn't sure (the same
   amount on the same day, worded differently, say): each is listed beside your one and is added
   as a new transaction unless you tick **Same as…**. The line also says how many of the
   account's own transactions YNAB doesn't have; a sync to YNAB sends those there. An account a
   previous import linked shows as linked and stays so.

   **Already have some of these categories here?** In the Categories tab, **In this book** says
   where each YNAB category goes, and the dim line under it says what that means for its budget.
   A YNAB category has a **label** (what its transactions are tagged with) and a **budget**; here
   the label is a *category* and the budget belongs to an *envelope* that can hold several
   categories. Names are compared ignoring emoji, symbols, capitals and extra spaces, so YNAB's
   "🛒 Groceries" goes with your "Groceries". The choices:
   - **Matches your…** (pre-selected when one of your categories reads the same). It keeps its name,
     envelope and group; nothing is renamed or moved. If it is alone in its envelope the line says
     **Budget syncs with envelope X**; if the envelope holds other categories too, **its Assigned
     won't sync** (the labels still do); in no envelope, **Not budgeted here** (it gets an envelope
     of its own if YNAB has money assigned to it).
   - **Into envelope…** when an envelope of that name exists. An empty one is pre-selected and the
     category is created inside it; one that already holds categories is offered but not
     pre-selected, because adding to it makes it a shared envelope.
   - **New category**: a new category with an envelope of its own, in your group of the same name
     (or a new group).
   - **Use…** any other category of yours that no YNAB category has taken.

   If a YNAB category matches more than one of yours, nothing is guessed: it asks you to pick, and
   **Import** waits. A YNAB group goes with the group of yours that reads the same, else a new one.

   **After import** sets the sync (**Keep in sync both ways**, **Sync from YNAB only**, **Sync to
   YNAB only**, or **Don't sync, import once**); then **Import**. The app takes a
   [backup](../operations/backup-and-restore.md) before it writes anything and names it in the
   report; if the backup fails, nothing is imported. **View live report** opens the run while it
   works. You can close the tab: the work continues on the server, and the sync turns on by
   itself when the import succeeds (every hour by default). If it can't turn on, the report says
   why and step 2 offers **Turn on**. Running again is safe: nothing is booked twice.

Once it has been imported it appears under **Automations → Data sync** with its schedule and an
**N to review** tag when something needs you (the **Data sync** switch shows the same count). Its menu has **Sync now**, **Turn on** /
**Turn off** and **Open**.

## Once it's running

The page reads like any Automation: the switch turns it on or off (off keeps the plan it is paired
with), the next sync is next to it, and **Sync now** runs one on demand. Only one run happens at a
time.

- **Needs your review** comes first when something does: where both sides changed the same thing
  (a transaction, a month's Assigned, a category's name, hidden state or group), with **Keep Open
  Net Worth's** and **Keep YNAB's**; a YNAB category that could go with several of yours, or with
  an envelope that already holds others, which asks you to pick; and an account with a synced transaction
  dated before its Starting Balance, which moves only if you confirm **Move the Starting Balance**
  (or choose **Keep as history**).
- **Settings**: the direction, and how often: every 15 or 30 minutes, hourly, every 6 hours, daily,
  or manual only. Below them, the last sync and what it did ("3 sent to YNAB, 1 pulled, 1 needs
  review"), or why it failed.
- **Direction.** **From YNAB** brings YNAB's changes in and never writes to YNAB; **To YNAB** sends
  this book's changes and never changes the book from YNAB. Either way, a transaction or a month's
  Assigned changed on *both* sides still goes to **Needs your review**, and keeping the side the
  direction protects leaves the other one untouched. What one way held back is not lost: switch
  back to **Both ways** and the next sync sends or pulls it.
- **Categories and groups.** Each YNAB category stays paired with the category it was imported
  into, even when either is renamed. A **new** category on one side is created on the other (a new
  YNAB category goes with one of yours that reads the same, else is created here with an envelope
  of its own, in the group paired with its YNAB group; a new category here is created in YNAB, in
  the group paired with its envelope's group). A **rename**, a **move** to another group (for a
  category alone in its envelope) and **hiding** reach the other side. Nothing is ever deleted by
  the sync: a category hidden or deleted in YNAB is **hidden** here, and one deleted or merged here
  is **hidden** in YNAB (its API cannot delete), with its transactions relabelled as usual. A
  category that sits in no envelope is created in a YNAB group called **Not budgeted**; if YNAB
  later assigns money to it, it gets an envelope here. A group renamed on one side is renamed on
  the other. Income categories are not sent: their transactions go to YNAB as Ready to Assign
  inflows.
- **Assigned only for one-category envelopes.** A category's Assigned syncs with its envelope only
  while the envelope holds just that category. If you put another category in, the run says so
  once ("Food holds several categories, so its Assigned isn't synced with YNAB."); take it out
  again and the Assigned syncs again, with any difference listed for your review.
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

- **Counts**: sent to YNAB, pulled in, Assigned changes, category changes, conflicts filed,
  deferred, errors, and how many YNAB requests the run used. **Categories** lists each category or
  group change in a sentence, and **Worth knowing** notes anything not synced and why.
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
