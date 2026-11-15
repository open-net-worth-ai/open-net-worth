---
title: Bank sync without AI
description: Book a bank connector's transactions by fixed rules, no AI — history first, then what's new on a schedule; bank-side changes wait for your review.
category: features
order: 172
audience: [user]
status: beta
related: [connectors/bank-brokerage, lucius/automations, features/categories-payees, features/accounts]
keywords: [bank sync, sync without ai, no ai, data sync, gocardless, enable banking, plaid, history, 90 days, starting balance, review, changed transaction, withdrawn transaction, duplicate, read limit, rate limited, sync now, payee default, merchant category code, mcc, undo, card payment]
---

# Bank sync without AI

A bank connector can book its transactions into your accounts **without Lucius**: by fixed rules,
on a schedule, with no AI model and nothing to pay for. When an account is first synced it gets
the history your bank serves (90 days for most European banks); after that each run books only
what's new.

> **Experimental, on by default.** Turn it off in [Settings → Developer → Experimental](settings.md#experimental-features)
> (**Bank sync without AI**); while it is off, only a Lucius Automation books bank transactions, as
> before.

It works with **GoCardless**, **Enable Banking** and **Plaid**.

## Turn it on

1. Open **Automations → Data Sync**. GoCardless, Enable Banking and Plaid are there beside YNAB
   and Actual: under **Add a data sync** until their connector is set up, then as a card. Or, on a
   connector's page, click **Set up sync** under its **Data import and sync** heading.
2. The bank's own page walks you through it. A bank not connected yet starts with **Connect
   GoCardless…** (or Enable Banking, or Plaid), which opens the connector's setup form — see
   [Bank & brokerage connectors](../connectors/bank-brokerage.md). Then three steps:
   1. **Choose accounts.** Each bank has a card listing its accounts in two columns: **At the bank**
      (the bank's account and its last digits) and **In your book** (the account it books into;
      click it to open it). Every one is ticked. Untick one to leave it out: it's never read from the bank or booked. **Link accounts**
      (under the list) links a bank account here, as a new account or to one you have, without
      going back to the connector. **Link** lists only accounts in the bank account's currency,
      the ones not linked to another connector first; **Unlink** undoes a wrong pick.
   2. **Import history.** Books the history your bank serves (90 days for most European banks)
      into the ticked accounts, and moves each account's Starting Balance back to its oldest row,
      so today's balance doesn't change. While it runs, the step names the account it's reading
      ("2 of 3" with several) and then how many transactions it's booking; a bank can take a
      minute to send a long history. Once it's in, the step lists each account with what came in and
      from when, or that the bank had no transactions for it (that account is done too); click an
      account to open its register.
      **Import history** comes back only for an account added after the import. **Skip for now**
      goes straight to step 3: the first sync brings the history in anyway.
   3. **Turn on.** Pick how often it runs, then **Turn on**: new transactions are booked on that
      schedule from then on.

Once it's set up, the page shows a switch to turn it on or off, the next sync and **Sync now**;
below them, the accounts, the schedule, anything that needs your review, and the run history.
Each bank has its own card, its accounts inside. Its top right says how it stands: a green dot
and when it last synced (hover for until when the bank lets the app read it). European banks
grant that access for a set time (often 90 or 180 days; Enable Banking links ask for the
bank's own longest), and so do some US and Canadian banks through Plaid: two weeks before it
ends the dot turns amber with **Renew** (**Reconnect** for Plaid), and once it has ended, or the
bank wants you to sign in again, it turns red
with **Renew** or **Reconnect**. Either opens the connector's own setup.

In **Accounts**, every account the sync keeps has a link icon after its name: green while it
syncs, amber when the bank's access ends within two weeks, red once the sync has stopped (access
ended, the bank wants you to sign in again, or the last sync failed). Hover it to see which, or
when it last synced.
Click a run in the history to see what it did: what it read from the bank, what it booked (each
transaction, with its account and category), what it left pending or sent to review, and any
problems. Click a transaction there to open its details, or **View in register** to see
everything that run booked in the Accounts register. When some of what it booked has no
category, **Categorize with Lucius** puts a request for exactly those transactions (how many,
their dates and accounts) in Lucius's box, in a new conversation, for you to send.

**Undo this run** (in the same footer) takes back a sync that went wrong, such as one that
booked duplicates. It removes what the run booked, brings back the pending transactions the bank
dropped (a pending one that a settled transaction replaced stays gone, so it isn't counted
twice), puts a card payment it folded into a transaction you'd typed back to what that was,
and returns an account's start to where it was before a first import moved it, so your balances
are what they were. The bank's rows are not booked again by the next sync: they wait together, as one item under
**Needs your review**, with **Book all again** and **Forget them** (which keeps them out for
good); **Show the transactions** lists them, each with its own **Book again**. Only
the most recent sync that changed your book can be undone, and a pending transaction the bank
settled stays settled. A transaction you changed since is left as you changed it, and the run
says so, as it does when a sync couldn't be saved for Undo or an account's start couldn't be put
back.

The connector page shows one line (on or off, and the next sync) and **Manage**, which opens
that page.

## It's the no-AI way

This sync never uses an AI model. If you'd rather Lucius synced a bank, use a Lucius Automation
such as **Sync GoCardless** instead (see [Automations](../lucius/automations.md)). On a bank's
connector page, **Automate** offers both: **Sync transactions (no AI)** opens this sync (it
reads **· On** once it's on), and **Lucius Automation** starts a new Automation. The two are
independent: neither pauses or blocks the other. A transaction the bank lists once is never
booked twice, but if both run, a pending payment that settles under a new reference at the bank
can be booked by Lucius as well as by this sync, so running one per bank is simplest.

## What a run does

- **Books new transactions** into the linked account: each bank row once, never twice. Running
  it again books nothing new. Two identical rows (two £3.00 coffees on the same day) are two
  transactions.
- **Books pending transactions as uncleared.** They count in your working balance (Cash on
  hand), not your cleared one. When the bank settles one, the same transaction becomes cleared
  and takes the settled date; if it settles at a different amount (a tip added, a hold settled
  lower), the settled one replaces it and keeps your category, payee and memo. If the bank drops
  a pending one (a released hold, a cancelled payment), it comes off your book.
- **Books settled transactions as cleared**, so an account's **Cleared** balance is what the
  bank has settled.
- **Never re-books a transaction you deleted.**
- **Never books the history again when a bank account is linked again.** Renew an expired
  sign-in with **Reconnect**: it keeps the bank's accounts as they are. If a bank was linked
  afresh instead (its accounts get new identities at the bank) and you **Unlink** the old
  account and **Link** the new one, the next sync matches what the bank lists to the
  transactions already in your book (same amount, same day or within three days) and books
  only what's really new. The run says how many it matched.
- **Keeps foreign charges' original amount and currency** beside the amount your account was
  charged.
- **Files a credit-card payment as one transfer,** not as income on the card plus spending in
  checking (which would inflate both reports). When the card gets a payment and one of your
  checking-type accounts has an outflow of the same amount within three days, and either one is
  worded like a payment, they become a single transfer, whichever the sync read first, and
  whether the other side came from another bank connector or you typed it (a row you typed is only used once you have cleared it). A match must be
  unique: if two rows could be the other side, nothing is paired. Merchant refunds, and
  anything you deleted before, are never paired. A payment whose other side isn't in your book
  stays income on the card until it is; then the two pair. The run says how many it filed.
- **Puts each transaction in your review queue,** as YNAB books a bank's transactions
  unapproved: it counts in your balances straight away, and waits in **Review** until you
  approve it. Settling a pending one later doesn't undo an approval.
- **Categorises without AI:** your [Automations](../lucius/automations.md) rules and each payee's
  default category apply, as they do to any new transaction. Otherwise, if the bank gives a
  merchant category that's exactly the name of one of your categories, that one is used. No
  category is ever created; anything else stays **Uncategorized**. When you give a synced
  transaction a category in **Review**, its payee keeps it as its default (if the payee has none
  yet; an existing default is never overwritten), so the next sync files that payee by itself.
  A bank that puts a store or reference number in the wording ("ACME GROCER 1234") gives each
  number its own payee: merge those in **Payees** and the default follows.
- **First sync of an account:** books the history your bank serves, then moves the account's
  Starting Balance back to the oldest row, so the history counts and **today's balance doesn't
  change**. A new account is then set to the bank's own balance (below). (Cash and card accounts
  only.) If the bank sends only part of the history, nothing is booked yet: the next sync reads
  it all again, so no history is left out.

A run books all its new transactions in one go: if something fails, nothing from that run is
booked, and the next run tries again.

## The bank's balance

Every sync also reads each account's balance from the bank, at no extra read (GoCardless's
balance is its own request, which banks limit separately, about as often as transactions).

- **An account you've just added** (holding only its Starting Balance) is set to the bank's
  settled balance from that same read when its history comes in, so it matches from the start —
  whatever was or wasn't in the balance you typed or the app filled in when you added it.
- **Any other account** is never changed for you. When its cleared balance and the bank's
  disagree, its row says **Off by €12.50**. Click it to see both figures ("Bank €1,500.00 · Your
  book €1,487.50") and **Match the bank**, which moves the account's Starting Balance by the
  difference.

A card or loan's balance is money owed, so it's compared as a negative figure whichever way the
bank reports it. GoCardless and Enable Banking say which figure is settled, and that's the one
compared with your cleared balance. Plaid sends a single figure that leaves out pending
payments, so it's compared with your cleared balance too. Plaid may send a figure that's a few
hours old: if it's behind, the row shows the difference rather than your balance silently
changing.

Banks report their balances in the same terms your accounts use, both on the connector's
**Accounts** list and when Lucius lists a bank's accounts:

| Name | What the bank means |
|---|---|
| **Cleared balance** | Settled transactions only |
| **Balance** | Settled plus pending, like your working balance |
| **Available to spend** | What you can spend now, overdraft or credit line included |

A figure the bank doesn't send is left empty, never filled in from another.

## When the bank changes something

A bank sometimes changes a transaction after it's booked, or stops listing it. A run never
applies that on its own. It waits under **Needs your review**, on the sync's page:

| What happened | Your choices |
|---|---|
| The bank changed the amount, date or wording | **Use the bank's version** or **Keep mine** |
| The bank no longer lists it | **Delete it** or **Keep it** |
| A pending transaction posted at a different amount | **Use the posted amount** or **Keep mine** |
| It looks like a transaction already in your book | **Same transaction** (link them), **Book it separately**, or **Ignore it** |

If you edited a transaction by hand, the review says so before you replace your edit. Nothing
in the ledger changes until you choose.

## How often it runs

| Bank | Default | You can choose |
|---|---|---|
| GoCardless, Enable Banking | Every 12 hours | Every 6 or 12 hours, once a day, every 2 days, or once a week |
| Plaid | Every 6 hours | Every hour, every 6 or 12 hours, once a day, every 2 days, or once a week |

Change it under **Schedule** on the sync's page (or in step 3 of setup). A bank that allows 4
reads a day is never offered more than 4 runs a day. The next run moves to your last sync plus
the new interval. A run never misses rows however long the gap: each account reads from its
newest booked transaction.

**Sync now** runs it at once. GoCardless and Enable Banking banks usually allow only **4 reads
per account per day**, and every read counts — scheduled, **Sync now**, or Lucius fetching the
bank in chat. Once an account's reads
are used, it says when its next read is, **Sync now** waits, and so does the schedule.

If the bank refuses (its daily limit, expired access), the sync waits instead of retrying: until
the limit resets, or until you renew expired access. The connector page shows what the bank
said. Renew (or Reconnect) the bank on the connector page, and the sync runs again right away. A bank whose access ended or was
revoked only holds up its own accounts: your other banks still sync. If the bank refuses just
one account, that account is tried again later and the rest are read as usual.

A bank that couldn't be read is left exactly as it was: nothing of it is booked, its pending
transactions stay on your book, and its history isn't marked as imported. Its card says
**Sign-in needed** (with **Reconnect**) or **Last sync failed** (hover for why), and so does the
mark beside its accounts. When one connector holds several banks, the others still sync, and
the run says how many accounts it couldn't read; one bank's problem never shows on another's
accounts. With several Plaid banks failing at once, each account shows the reason of its own
bank.

Right after you link a Plaid bank, Plaid is still gathering its older transactions. The first
import waits for that: nothing is booked and the account says it couldn't be read in full, then
the sync tries again, until Plaid has the whole history.

An account the bank no longer lists (it was closed, or the bank was linked again and its
accounts have new identities) isn't read either. Its card says **Account not at the bank** with
**Relink**, which opens the connector's setup: **Unlink** the old account and **Link** the bank's
current one to the same account in your book. The next sync matches what's already booked.

## Related pages

- [Bank & brokerage connectors](../connectors/bank-brokerage.md)
- [Automations](../lucius/automations.md)
- [Categories & payees](categories-payees.md)
