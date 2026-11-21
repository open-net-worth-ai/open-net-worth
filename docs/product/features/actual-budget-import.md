---
title: Import from Actual Budget
description: Bring a whole Actual budget here, reviewed first and checked against Actual afterwards, then keep the two in step on a schedule if you want to.
category: features
order: 171
audience: [user]
status: stable
related: [features/imports-and-sync, connectors/bank-brokerage, operations/backup-and-restore, concepts/envelopes-and-budgeting]
keywords: [actual, actual budget, categories, starter categories, merge categories, tags, hashtags, proxy, proxy headers, reverse proxy, import, migrate, migration, move, review, verify, sync, two-way, conflict, reset sync, tracking, carryover, hold, to budget, starting balances, income categories]
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

Open **Automations → Data Sync → Actual** and click **Connect Actual…**. It's the same panel as
**Connectors → Actual Budget**, and it opens right over the page.

- **Server address** is the address you open Actual at. Plain http to another computer still works,
  but it isn't encrypted: anyone on that network could read your password, token or proxy headers and your budget,
  and change what this app reads and writes. The form warns you under the field, so use https if you
  can. A certificate this computer doesn't trust stops the connection, and there's no switch to skip
  that check: paste your server's own certificate under **Server certificate** instead.
- **Sign in with** is a password or a token, whichever your server takes. Typing a new one replaces
  the other. A new server address asks for it again, so a mistyped address is never sent your saved
  password. That covers the host, the port, and a change between http and https.
  **A server that signs in with OpenID** (an identity provider such as PocketID, Authentik or
  Keycloak, with no Actual password) takes a token. Actual doesn't show one, so take it from a
  browser: sign in to Actual in a private window, and copy the long code after `token=` in the
  address it lands on (`…/openid-cb?token=…`) as it passes, or later from the browser's history.
  In a browser already signed in, the developer tools have it too: under storage, IndexedDB,
  `actual`, `asyncStorage`, the key `user-token`. Then close that window without signing out:
  signing out in Actual ends the token. It lasts while your server keeps tokens (the default never
  expires them); if it ends, the connector says the token expired, and a new one goes in the same
  field. Treat the token like your password: anyone holding it can open your budget. A password
  saved for such a server is refused with a message saying so: each try counts
  towards Actual's limit on failed sign-ins, so the sync doesn't try again sooner.
- **Budget** appears once an address and sign-in are saved. If the sign-in is wrong, this is where it
  says so, and the connector shows **Auth error** until a read works again. The budget you pick here
  is the one the import starts from.
- **Test connection** tries what is saved, end to end: the address, a sign-in proxy, the
  certificate, the sign-in, then the chosen budget downloading and opening. It says the step that
  stopped it, such as a proxy wanting its own sign-in, a certificate this computer doesn't trust, or
  a wrong password. Save first: it doesn't test what you've typed but not saved.
- **Encryption password** is only for a budget with end-to-end encryption on in Actual. An encrypted
  budget says `(encrypted)` in the list, and **Save** waits until it has its password.
- **Proxy headers** are only for a server behind a sign-in proxy, such as Pangolin, Authelia or
  Cloudflare Access, that accepts a header in place of its login page. Write one per line, as
  `Name: value`, up to ten. They go with every request to your server, and nowhere else: the app
  never follows a redirect, and a new server address drops them, as it does a saved password. Headers
  the app sets itself, such as `Host` or Actual's own sign-in header, are refused. The form shows
  only the names of saved headers; **Remove** deletes them when you save.

- **Server certificate** is only for a server with its own certificate, self-signed or from a
  private authority, that this computer doesn't trust. Paste it as text, from
  `-----BEGIN CERTIFICATE-----` to the END line (up to three; never a private key). It is trusted
  for this server only, on top of the usual checks: the address must still match it. The form shows
  its SHA-256 fingerprint, so you can compare it with your server's (for example
  `openssl s_client -connect your.server:443 </dev/null | openssl x509 -noout -fingerprint -sha256`); **Remove** stops trusting it,
  and a new server address drops it.

Passwords, tokens and header values never come back from the server. A saved one shows as dots, and
leaving the field blank keeps it.

**Behind a sign-in proxy?** If the proxy answers instead of Actual, with a redirect or its own login
page, the connector says so. Either add the header your proxy accepts under **Proxy headers**, or let
these paths through to Actual without the proxy's login: `/account`, `/info`, `/sync`, `/data` and
`/data-file-index.txt`. The sync doesn't use `/api`. Letting paths through means anyone can reach
Actual's own sign-in on them, guarded only by Actual's password or token: a header is the safer of
the two. If the proxy turns the app away, the connector
says the proxy wants its own sign-in, rather than blaming your Actual password.

**Pangolin**, with its own sign-in or an identity provider such as PocketID, takes either header,
and keeps its sign-in for your browser:

- A **share link** for the Actual resource: Pangolin shows it as two headers,
  `P-Access-Token-Id: …` and `P-Access-Token: …`. Paste both lines under **Proxy headers**. Pangolin
  offers 30 days by default; tick never-expire, or make a new link when it runs out.
- **Header authentication** on the resource, with a user name and password you choose: write it as
  `Authorization: Basic …`, the two joined as `name:password` and base64-encoded.

Pangolin's rules can instead let the paths above through without a sign-in, but then anyone can
reach your Actual sign-in from the internet, guarded only by Actual's own password.

On a hosted instance the form says Actual isn't available, and **Data Sync** doesn't list it.

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
| Off budget | Fixed deposit, Investment, Retirement, Receivable, Loan | Loan if the balance is negative or the name says loan, mortgage or debt; otherwise Investment |

Each one starts on a suggestion, marked `(suggested)`, and **Import** stays off until every account
has been looked at. Pick one in its dropdown, even the one it suggests, or press **Accept
suggestions** to take them all at once. You can change a type while the review checks another one.

An account you link to one already here keeps the type it has, and must fit the same choices. It
says **Keeps its own**.

**A currency, if the budget doesn't state one.** **Budget currency** appears above the notes and
starts from your book's base currency. A budget that states a currency keeps it, and the dropdown
doesn't appear.

The notes above the tabs show what would stop the import first, then the first three things worth
knowing, or the first one on a phone. The starter categories it removes come first among those, so
you always see them. **Show more notes** opens the rest.

## What comes across, and how

- **Income categories** come in as income categories, with no envelope; the review's **Envelope**
  column says so. Salary raises Ready to
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
- **What Actual held** is kept with each transaction. Open one and its drawer's **Source** section
  shows Actual's own row, with a split's lines under it. Each sync refreshes it, and transactions
  imported before this was kept get theirs on the next sync.
- **Tags** in a note, written `#groceries`, become the transaction's tags here. The note itself is
  kept as it is. When a note changes in Actual, its new tags are added and the ones you added here
  stay. A tag you remove here comes back only if the note changes again in Actual. Transactions
  imported before tags were read get theirs on the next sync.
- **A budget in tracking mode** brings its accounts, categories and transactions but no Assigned
  amounts, because its figures aren't envelopes. Its cash starts in Ready to Assign. On an empty
  book the budget method is still set to zero-based, and you can change it afterwards.

## What the review warns you about

These never stop an import. The review says each one, and the run's log says it again.

- **Ready to Assign won't match To Budget.** The two are worked out differently. Once the sync
  is on, its page shows both side by side for this month: Actual's To Budget worked out here the
  way Actual's own engine does (checked against it), Ready to Assign, the gap, and the part of it
  held in card payment envelopes, which Actual has no place for. The rest comes from how each
  treats overspending and spending with no category.
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
A run that fails for a reason that may pass, such as the server being down, tries again sooner:
after 5 minutes, then 15, then an hour, never later than the schedule. A refused sign-in waits for
you instead. After three failed runs in a row, **Worth checking** says so, with the date of the
last run that went through and the latest error, until a run succeeds.

Once it's set up, **Automations → Data Sync → Actual** has the switch and **Sync now** at the top,
then **Needs your review**, **Worth checking** (only when there's something to say), **Direction**,
**Schedule**, the budget, how it decides, and a history of its runs. The history leaves out previews
that went fine, since each re-check of the review would add one; a failed preview stays. It reads the
same as YNAB's page. **Actual connection…** in the menu opens the connector's form. While a sync
runs, the top of the page says the step it's on, and on a long one how far it has got, for example
"Syncing now: sending transactions to Actual: 400 of 9,000".

**Undoing a sync here.** **Undo the sync from …** on the sync page puts back what the last sync
that changed this book did to its transactions: a row it added is removed, a row it changed gets
its values back, a row it deleted comes back. A row anyone has changed since stays as it is, and
the result names it, as it does one that couldn't be put back in full because a category or account
it used is gone. Only the latest sync can be undone, and only if it changed a transaction here. The sync is switched off afterwards and Actual isn't changed then. When you
turn the sync on again, what was put back here is sent to Actual like any change made here, so it
undoes Actual's side too, edits made in Actual included. Until a run sends them, **Worth checking** says how many
are waiting and what turning the sync on will do. To keep Actual as it is instead, import a
restore point there (below) before turning the sync back on. Assigned, categories and Starting
Balances aren't part of it.

**Undoing a sync in Actual.** Each sync that writes to Actual first keeps Actual's budget as it
was, and the page lists the last five under **Before each sync that wrote**. Download one and, in
Actual, **Import file → Actual**: it comes back as a separate budget, as it was before that sync, and
nothing is overwritten. To sync with that restored budget from then on, move the sync to it, as
**Moving the sync to another budget** below explains: rows it doesn't have are asked about, not
deleted. A run that writes nothing keeps nothing. An end-to-end encrypted budget gets
none, and the page says so where they would be: its copy here is decrypted, and keeping it would undo that encryption on disk, so export it
from Actual yourself (**Settings → Export data**) before a sync that writes. The import's own backup
covers this book, not Actual.

**Preview** beside **Sync now** is a dry run: it reads Actual as a sync does and decides every
transaction and month's Assigned by the same rules, then shows what would change and where (new,
changed or deleted, here or in Actual, what would keep both sides' changes and what would wait for
review), with a few of the rows. Categories are in it too: renamed, moved, hidden or shown on
either side, new on either side, and one you deleted here that would be hidden in Actual with its
Assigned set to 0. Nothing is written on either side, and nothing is filed.

**Many answers at once.** With two or more transactions in **Needs your review**, **Keep all Open
Net Worth's** and **Keep all Actual's** answer them together, after asking once more how many rows
change and where. Categories, groups and Starting Balances still ask one at a time.

**Which way changes go.** You choose a direction at import or later:

- **Both ways**: a change on either side reaches the other.
- **From Actual**: Actual's changes come here, and nothing is written to Actual.
- **To Actual**: this book's changes go to Actual, and Actual's don't change the book.

What one direction held back isn't lost. Switch back to both ways and the next sync moves it.

**When both sides changed the same transaction.** With the sync both ways, changes to different
fields both stand: a note you wrote here and a category picked in Actual end up on both sides. A
category and a split's lines count as one field, and so do a row's amount, accounts and kind. When
both sides changed the same field to different values, it waits in the review list, whichever
direction you chose, and you pick the version to keep. In one direction, any change on both sides
waits there. The same goes for a row someone edits in Actual during
the moments a sync is writing it: the sync checks each row just before writing and leaves one that
moved. That narrows the gap to milliseconds, plus the time a request takes to reach your server. It
can't close it, because an Actual server has no way to refuse a write over a newer one: if your edit
lands inside that gap, on a field the sync is writing, the sync's value wins and nothing is filed. A
device whose clock runs more than ten minutes behind can lose the same way.

**Devices that were offline.** Actual's apps keep working without a connection and send their
edits to the server when they're back. The sync sees those edits on its first run after they reach
the server, and treats them like any other change made in Actual. Actual keeps the latest edit of each
field, by when it was made. So if a phone edits a row's category offline, and before it reconnects the
sync changes that same category, the sync's later edit wins and the phone's is lost.
Two Actual devices behave the same way with each other. Different fields don't clash: the
phone's note and the sync's category both stand.

**What differs from YNAB's sync:**

- Future-dated transactions go across.
- A split can be edited here, and an existing row split: its date, amount and lines are all sent. A
  split whose lines don't add up to its total is refused and goes to the review list. Changing a
  transfer's accounts, or turning a row into a transfer or back, stays in the review list. So does
  changing a transfer's category, except on a transfer between a budget account and an off-budget
  one: Actual keeps that category on the budget side, and it's sent.
- A category you clear in Actual stays cleared here: no rule or payee default fills it back in,
  until you edit that row here.
- A Starting Balance changes both ways, by difference: change it in Actual and your opening
  balance here moves by the same amount; change your opening balance here and Actual's Starting
  Balance moves by that amount. Changed on both sides, each takes the other's difference, so
  neither change is lost. Actual's date follows here when it still matched yours; a date you set
  here stays here. **To Actual** holds Actual's changes back, and **To this book** holds yours.
  The first sync after this arrived compares the two once: if they already differ, because one
  was changed before, nothing moves on its own. The review list shows both and you keep one.
- A payee you clear here is cleared in Actual too.
- Actual has no category on an off-budget row, or on a transfer between two budget accounts. A
  category you give one here stays here and isn't sent; Actual's blank doesn't clear it.
- Actual stores whole cents. An amount finer than that, such as 12.345, goes as the nearest cent
  (12.35) and stays as you wrote it here.
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

**The categories this app came with.** A new book starts with a few
[starter categories](../concepts/categories.md), such as Groceries, Dining out and Rent/Mortgage.
The import removes the ones you never used, so your Actual categories don't sit beside a second,
half-matching set. The review lists them in its notes, which may be under **Show more notes**. A
starter you renamed, funded or put a transaction on is always kept. To keep another, pick it for
one of Actual's categories in the **Categories** tab: Actual's "Food" can go to Groceries, and the
two are paired from then on. To keep them all, press **Keep them** beside that note. The sync then
adds them to Actual, ready for you to fold Actual's categories into, as below.

**Simplifying your categories.** Each Actual category pairs with one category here, so the review
can't fold several into one. Do that after the import, here: in **Manage categories**, delete the
category you're folding away and pick the one to keep under **Reassign activity to**. With the sync
on, both ways or To Actual, Actual follows: its transactions move to the category you kept, which
is created in Actual if it's new there, and the folded one is hidden in Actual, never deleted. Its
Assigned in Actual is set to 0 in every month, as it holds nothing here any more, so Actual's To
Budget doesn't count it twice. With the sync set to To this book, Actual isn't changed. While a
sync that writes to Actual is on, the delete dialog says so before you confirm.
Renaming a category or moving it to another group here reaches Actual too. See
[Categories and payees](categories-payees.md#manage-categories).

**Starting your budget over here.** To budget afresh with a simpler set of categories, keeping
your accounts and transactions, fold the categories as above, then use
[Start fresh](budgeting.md#start-fresh) from the month you want to begin. It clears that month's
Assigned and starts Ready to Assign from your cash then, with Undo. With the sync on, both ways or
To Actual, the cleared Assigned reaches Actual too; set it to To this book first to leave Actual's
budget as it is.

**An income category you later put in an envelope** keeps syncing, but what it receives stays in
that envelope and doesn't raise Ready to Assign. **Worth checking** warns you, and its Assigned stays
here.

**What stays local.** Actual's rules, schedules, tag colours, goals, account groups, dashboards and bank
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
disk. The one copy kept is a restore point, for a run that wrote to a budget that isn't encrypted, in
this instance's backups folder (see Undoing a sync in Actual). It's tested against a real Actual
server for the sync cases on this page, including end-to-end encrypted and tracking budgets, and a
budget of 10,000 transactions: its import and check took about half a minute, and a sync about 5
seconds. It's tested with a server that signs in only with OpenID, through PocketID and a token. The sync's
tests also run on Windows; the live server tests run on macOS and Linux.

**Which Actual versions.** The live tests run against Actual's server and official client
26.11.0, pinned so every run sees the same thing. Other versions work as long as their budget
looks the way this app expects: before it reads or writes a budget, it checks the budget's
database for every table and column it uses, and for any upgrade step Actual still has to run on
it. A budget that needs an upgrade step nobody here has reviewed, or lacks something this app
uses, is refused with a message saying what to do, and nothing is read or written. If a new
Actual release is refused this way, it's a gap here to report, not a problem with your budget.

## Guides and comparisons

Longer write-ups on the Open Net Worth blog:

- [Actual Budget Alternative: Open Net Worth vs Actual Budget](https://opennetworth.ai/blog/open-net-worth-vs-actual-budget/): Actual Budget and Open Net Worth side by side, and how to move over
