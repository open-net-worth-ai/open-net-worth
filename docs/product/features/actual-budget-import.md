---
title: Import from Actual Budget
description: Bring a whole Actual budget here, reviewed first and checked against Actual afterwards, then keep the two in step on a schedule if you want to.
category: features
order: 171
audience: [user]
status: stable
related: [features/imports-and-sync, connectors/bank-brokerage, operations/backup-and-restore, concepts/envelopes-and-budgeting]
keywords: [actual, actual budget, import, migrate, migration, move, review, verify, sync, two-way, conflict, reset sync, tracking, carryover, hold, to budget, starting balances, income categories]
---

# Import from Actual Budget

> **Experimental.** Actual Budget is off by default. Turn it on in **Settings → Developer →
> Experimental**. While the switch is off, nothing on this page appears in the app. Anything you've
> already imported stays here, and the sync waits until you turn it back on.

If you budget in **Actual Budget**, you can bring a whole budget here in one go: its accounts,
categories, transactions and each month's Assigned amounts. You see everything before anything is
written, and afterwards the figures are checked against Actual's. After that you can stop, or
[keep the two in step](#keep-the-two-in-step) on a schedule.

It needs your own Actual server, and it isn't available on a hosted instance. An import only reads
your budget in Actual. Nothing changes there unless you turn the sync on, in a direction that writes
to Actual.

## Connect your server

Open **Automations → Data sync → Actual** and click **Connect Actual…**. It's the same panel as
**Connectors → Actual Budget**, and it opens right over the page.

- **Server address** is the address you open Actual at. Plain http to another computer still works,
  but it isn't encrypted: anyone on that network could read your password or token and your budget,
  and change what this app reads and writes. The form warns you under the field, so use https if you
  can. A certificate this computer doesn't trust stops the connection, and there's no switch to skip
  that check.
- **Sign in with** is a password or a token, whichever your server takes. Typing a new one replaces
  the other. A new server address asks for it again, so a mistyped address is never sent your saved
  password. That covers the host, the port, and a change between http and https.
- **Budget** appears once an address and sign-in are saved. There's no test button: if the sign-in is
  wrong, this is where it says so, and the connector shows **Auth error** until a read works again.
  The budget you pick here is the one the import starts from.
- **Encryption password** is only for a budget with end-to-end encryption on in Actual. An encrypted
  budget says `(encrypted)` in the list, and **Save** waits until it has its password.

Passwords and tokens never come back from the server. A saved one shows as dots, and leaving the
field blank keeps it.

On a hosted instance the form says Actual isn't available, and **Data sync** doesn't list it.

**Pausing the connector stops all contact with your server.** No scheduled run starts, a run under way
stops at its next batch, and **Sync now**, the review, the import, a re-check and the budget list all
wait. Your sign-in stays saved, and the form still saves a new address or password, but it doesn't
list budgets until you press **Resume** on the connector. **Worth checking** on the sync's page says
it's paused, with a link to the connection. The sync's own switch stays as you left it, so a run the
pause held back goes as soon as you resume. A run the pause stopped partway waits for its next
scheduled time, or for **Sync now**. The YNAB sync still can't be turned on beside it.

## Before you start

- **Turn off the YNAB sync** if it's on. A book syncs with one app at a time, so an import from
  Actual is refused while YNAB's sync is on, or while a YNAB import or sync is running. You can
  still look at the review. It says so at the top and keeps **Import** off, so you know before you
  settle anything.
- **Pick the right budget.** The server can hold several, and importing the wrong one writes
  someone else's money into your book.

## What the review asks

Click **Review…** to see what would come across. Nothing is written until you import. It works like
the [YNAB review](imports-and-sync.md#set-it-up): each account can go to a new account or one you
already have, and each category to a new category or one of yours. Actual leaves two things for you
to settle.

**A type for every account.** Actual only knows whether an account is on budget or off budget, so
it can't tell a credit card from a current account. A card imported as cash would inflate your cash
on hand and Ready to Assign, so the **Accounts** tab groups the new accounts by band, with a **Type**
dropdown for each:

| In Actual | Types you can choose | Starts as |
|---|---|---|
| On budget | Cash, Credit | Credit if the balance is negative and the name looks like a card; otherwise Cash |
| Off budget | Fixed deposit, Investment, Retirement, Receivable, Loan | Investment |

Each one starts on a suggestion, marked `(suggested)`, and **Import** stays off until every account
has been looked at. Pick one in its dropdown, even the one it suggests, or press **Accept
suggestions** to take them all at once. You can change a type while the review checks another one.

An account you link to one already here keeps the type it has, and must fit the same choices. It
says **Keeps its own**.

**A currency, if the budget doesn't state one.** **Budget currency** appears above the notes and
starts from your book's base currency. A budget that states a currency keeps it, and the dropdown
doesn't appear.

The notes above the tabs show what would stop the import first, then the first three things worth
knowing, or the first one on a phone. **Show more notes** opens the rest.

## What comes across, and how

- **Income categories** come in as income categories, with no envelope. Salary raises Ready to
  Assign when it arrives. They pair only with your income categories, and your spending categories
  only with spending ones. An income category of yours that sits in an envelope can't be matched,
  because what comes in would stay in the envelope; the import makes a new one beside it.
- **Starting Balances** isn't a category here, whatever you've renamed it to. An opening balance
  stays the account's Starting Balance, with no second row beside it, even if Actual filed it under
  another income category such as Salary. An opening balance in a spending category funds that
  category.
- **A group called "Credit Card Payments"** is an ordinary group in Actual, so it comes in as one,
  named "Credit Card Payments (Actual)". The name itself is kept for this app's own group. Actual
  has no card payment categories, and a card payment there is a plain transfer.
- **Future-dated transactions** come across with their dates.
- **A budget in tracking mode** brings its accounts, categories and transactions but no Assigned
  amounts, because its figures aren't envelopes. Its cash starts in Ready to Assign. On an empty
  book the budget method is still set to zero-based, and you can change it afterwards.

## What the review warns you about

These never stop an import. The review says each one, and the run's log says it again.

- **Ready to Assign won't match To Budget.** The two are worked out differently, so it isn't
  compared.
- **Carryover.** A category with carryover on keeps its overspending in Actual. Here that clears
  at the end of the month and comes out of the next month's Ready to Assign. The review names each
  category this affects, and its check reads as a known difference.
- **Held money.** Money held for later in Actual shows in Ready to Assign here. The review names the
  month and the amount.
- **A split that doesn't add up.** Actual allows a split whose lines don't add up to its total, and
  counts the lines, so that's what is booked. The review and the report name each one, with both
  figures, so you can correct it in Actual if the total was the right one.
- **Plain http.** If your server is reached over unencrypted http on another computer, the review
  says so, and what that exposes: your password or token, your budget, and what this app reads and
  writes.

## Importing and checking

The dropdown at the bottom of the review says what happens afterwards: keep in sync both ways, sync
from Actual only, sync to Actual only, or import once and stop.

**Import** takes a [backup](../operations/backup-and-restore.md) first and names it in the report; if
the backup fails, nothing is imported. The import runs on the server, so you can close the tab.
Running it again is safe: nothing is booked twice, and no category is made twice.

Afterwards it compares these with Actual, row by row:

- each account's balance, cleared balance and number of transactions;
- each envelope's Assigned and available for the current month.

A tracking budget compares its accounts only. **Re-check against Actual**, in the page's menu, runs
the same comparison again whenever you like, against Actual's figures at that moment.

## Keep the two in step

The sync runs on a schedule, every 15 or 30 minutes, hourly, every 6 hours, daily or only when you
ask. It covers transactions, each month's Assigned, categories and groups. No AI model is involved.

Once it's set up, **Automations → Data sync → Actual** has the switch and **Sync now** at the top,
then **Needs your review**, **Worth checking** (only when there's something to say), **Direction**,
**Schedule**, the budget, how it decides, and a history of its runs. The history leaves out previews
that went fine, since each re-check of the review would add one; a failed preview stays. It reads the
same as YNAB's page. **Actual connection…** in the menu opens the connector's form.

**Which way changes go.** You choose a direction at import or later:

- **Both ways**: a change on either side reaches the other.
- **From Actual**: Actual's changes come here, and nothing is written to Actual.
- **To Actual**: this book's changes go to Actual, and Actual's don't change the book.

What one direction held back isn't lost. Switch back to both ways and the next sync moves it.

**When both sides changed the same thing.** It waits in the review list, whichever direction you
chose, and you pick the version to keep. The same goes for a row someone edits in Actual during
the moments a sync is writing it: the sync checks each row just before writing and leaves one that
moved. That narrows the gap to milliseconds, plus the time a request takes to reach your server. It
can't close it, because an Actual server has no way to refuse a write over a newer one: if your edit
lands inside that gap, on a field the sync is writing, the sync's value wins and nothing is filed. A
device whose clock runs more than ten minutes behind can lose the same way.

**What differs from YNAB's sync:**

- Future-dated transactions go across.
- A split can be edited here, and an existing row split: its date, amount and lines are all sent. A
  split whose lines don't add up to its total is refused and goes to the review list. Changing a
  transfer's accounts or category, or turning a row into a transfer or back, stays in the review list.
- A category you clear in Actual stays cleared here: no rule or payee default fills it back in,
  until you edit that row here.
- A change in only the letter case of a payee in Actual doesn't come across on its own. It arrives
  with the next other change to that row in Actual.
- Income rows keep their category both ways. A new income category here goes into Actual's income
  group. An inflow with no category stays blank in Actual, where it doesn't count towards To Budget;
  the run says so once for each payee.
- **Hidden is hidden.** A category hidden here is hidden in Actual, and the other way round.
- **A category is never deleted in Actual.** One you delete here is hidden there. One deleted in
  Actual is hidden here.
- Your Assigned changes leave a dated line in that category's note in Actual, as in
  `ONW 2026-11: assigned 450.00 (was 400.00)`. A note you changed during the run, or one over 500
  characters, is left as it stands. Transactions carry no marker, because Actual has no flag for it
  and a note would read as a change.
- Rules in Actual don't run on what the sync writes, so a row arrives as it is here.

**A budget in tracking mode** syncs everything except Assigned, and the page says why, under the
budget's name. If a budget
changes mode while the sync is on, the sync follows it. Going to tracking stops Assigned. Coming back
to envelopes compares every category afresh: any difference goes to the review list and no figure
moves.

**An income category you later put in an envelope** keeps syncing, but what it receives stays in
that envelope and doesn't raise Ready to Assign. **Worth checking** warns you, and its Assigned stays
here.

**What stays local.** Actual's rules, schedules, tags, goals, account groups, dashboards and bank
sync are never touched. Card payment envelopes are this book's own: Actual has none, so their
Assigned is never sent or taken. That's one reason Ready to Assign won't match To Budget.

**One app at a time.** A book syncs with YNAB or Actual, not both, since two apps writing Assigned to
the same envelopes would fight. Turning one on is refused while the other's sync is on, or while it
is running an import or a sync, and so is **Sync now**. The refusal says which, wherever you pressed.
Looking at the review or re-checking against the other app reads only, and gets in nothing's way.

**A very large run** does part of its work and says the rest is waiting for the next run. An Actual
server takes 500 requests a minute from one address, so a run stops itself well short of that: it
writes about 200 batches, and a figure in Assigned with its note line is two of them. If you press
**Sync now** again straight after, the server can turn the run away with `HTTP 429`. Nothing is lost,
and a later run carries on.

**If something goes wrong.** A server that's down, a wrong password or a budget that's gone stops the
run with a plain message. Nothing is lost. What was already confirmed stays recorded, and the next
run works out the rest. A run that's cut off partway can't send a row twice: the next run links what
the server already took. The one exception is a row you edit in the moments before that next run,
which can come across twice. A row you delete in Actual never comes back. What changed in Actual
during a run that failed is looked at again on the next run, in whichever direction you have chosen,
so it isn't missed. A run stops itself if it goes on too long, and the next one carries on.

If the app stops partway through a run and a row it had just linked is then gone from Actual, the
sync can't tell who removed it, so it asks. The review list says the row is no longer in Actual, and
you choose: send it to Actual as new, or remove it here. In a one-way sync, the answer that would
write to the protected side takes effect when the sync goes both ways, and the page says how many
are waiting. If you undo the deletion in Actual while an answer waits and also edit that row here,
the row can end up there twice.

**If a budget reads as empty,** or many rows seem to vanish at once, nothing is deleted here. Each
deletion waits in the review list for you to confirm, and if the rows come back, the items close on
their own. Rewinding to a checkpoint here can bring rows back that Actual no longer has; the sync
then asks about each of them, one at a time, rather than deleting them again on its own.

**Moving the sync to another budget** starts in the connector: **Budget** in its form is the one
place to choose. Pick the other budget and save, and **Worth checking** says the connection and the
sync no longer agree. Turn the sync off, press **Review…** there, and import. The sync moves to that
budget in the direction it already had, and on the schedule it already kept. A sync you turned off to
make the move comes back on when the import finishes with a way of syncing chosen in the review's
footer; choose **Don’t sync, import once** there and it stays off.

Moving deletes nothing here. Rows on the first budget's accounts stay in this book and the sync leaves
them alone. **Worth checking** says how many there are once a run on the new budget has counted them,
so it says nothing about them until then. The first sync after the move holds back every row
the new budget doesn't have, however few, and asks about each one in the review list: a budget
restored from an export, or duplicated in Actual, keeps the same row ids under a new name, so a row it
lacks may be one you added here since. The same goes the first time a sync follows a budget this book
wasn't imported from, or when this book was imported from more than one budget. Importing another budget is refused while the sync is on, which is why you turn
it off first. If a move is refused, the sync stays on the first budget with everything it had,
including its review list.

**A budget you haven't imported** isn't synced. If the sync follows one that has no account in this
book, each run stops, and nothing is written, paired or filed on either side. **Worth checking** says
so and offers the import right there. It keeps the direction you have, and a sync you've turned off
stays off. Import it, the warning goes, and the next run carries on as normal. If the connection is on
a different budget and the sync is off, the import starts from the connection's budget, so choose the
sync's budget in the list if that's the one you want.

**A budget deleted in Actual** stops each run, which says the budget is gone and nothing here changes.
Choose another in the connection and import it, as for a move.

**Reset sync in Actual** is fine. Row ids don't change, so the next run carries on as before, with no
pairing lost and nothing read as deleted. A run going at the moment of the reset stops, and the next
one carries on.

**A budget last refreshed long ago.** Budgets from older versions of Actual open normally. One whose
copy on the server dates from before September 2021, or that needs a change this app hasn't reviewed
yet, is refused with a message saying so. **Settings → Reset sync** in Actual refreshes it.

**What your server can reach.** The server you chose is trusted with your budget, not with the rest of
your book. A change to the budget that it sends runs only on the temporary copy. One that tries to
reach anything else, or a download holding more than a budget, stops with a message saying so, and
your book and other files aren't touched.

**The limits.** The sync reads a fresh copy of the budget on every run and deletes it afterwards, or
within about an hour of a later run if a crash stops that, so your decrypted budget doesn't stay on
disk. It's tested against a real Actual server for the sync cases on this page, including end-to-end
encrypted and tracking budgets. It hasn't been tested on Windows,
with OpenID-only servers, or on large real budgets.

## Guides and comparisons

Longer write-ups on the Open Net Worth blog:

- [Actual Budget Alternative: Open Net Worth vs Actual Budget](https://opennetworth.ai/blog/open-net-worth-vs-actual-budget/): Actual Budget and Open Net Worth side by side, and how to move over
