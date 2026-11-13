---
title: Bank sync without AI
description: Book a bank connector's transactions by fixed rules, no AI — history first, then what's new on a schedule; bank-side changes wait for your review.
category: features
order: 172
audience: [user]
status: beta
related: [connectors/bank-brokerage, lucius/automations, features/categories-payees, features/accounts]
keywords: [bank sync, sync without ai, no ai, data sync, gocardless, enable banking, plaid, history, 90 days, starting balance, review, changed transaction, withdrawn transaction, duplicate, read limit, rate limited, sync now, payee default, merchant category code, mcc]
---

# Bank sync without AI

A bank connector can book its transactions into your accounts **without Lucius**: by fixed rules,
on a schedule, with no AI model and nothing to pay for. When an account is first synced it gets
the history your bank serves (90 days for most European banks); after that each run books only
what's new.

> **Experimental.** Turn it on in [Settings → Developer → Experimental](settings.md#experimental-features)
> (**Bank sync without AI**). Until then, only a Lucius Automation books bank transactions, as
> before.

It works with **GoCardless**, **Enable Banking** and **Plaid**.

## Turn it on

1. Set up the connector and link its accounts as usual — see
   [Bank & brokerage connectors](../connectors/bank-brokerage.md).
2. On the connector's page, under **Next: bring in transactions**, click **Set up sync**. It opens the bank's own page in
   **Automations → Data sync**, with three steps:
   1. **Choose accounts.** Every linked account is ticked; its line says which bank account it's
      from. Untick one to leave it out: it's never read from the bank or booked. **Link accounts**
      (or **Link more accounts**) links a bank account here, as a new account or to one you have,
      without going back to the connector.
   2. **Import history.** Books the history your bank serves (90 days for most European banks)
      into the ticked accounts, and moves each account's Starting Balance back to its oldest row,
      so today's balance doesn't change. Safe to run again. **Skip for now** goes straight to step
      3: the first sync brings the history in anyway.
   3. **Turn on.** Pick how often it runs, then **Turn on**: new transactions are booked on that
      schedule from then on.

Once it's set up, the page shows a switch to turn it on or off, the next sync and **Sync now**;
below them, the accounts, the schedule, anything that needs your review, and the run history.
Click a run in the history to see what it did: what it read from the bank, what it booked (each
transaction, with its account and category), what it left pending or sent to review, and any
problems.
The connector page shows one line (on or off, and the next sync) and **Manage**, which opens
that page.

## It's the no-AI way

This sync never uses an AI model. If you'd rather Lucius synced a bank, use a Lucius Automation
such as **Sync GoCardless** instead (see [Automations](../lucius/automations.md)). Only one of
the two can sync a bank: turning this sync on pauses that bank's Lucius sync Automation and says
which one it paused, so nothing is booked twice. While it's on, turning that Automation back on
is refused, with a message telling you to turn this sync off first. The connector page doesn't suggest a
Lucius sync beside this one; **Automate** at the top of the connector's page sets one up.

## What a run does

- **Books new transactions** into the linked account: each bank row once, never twice. Running
  it again books nothing new. Two identical rows (two £3.00 coffees on the same day) are two
  transactions.
- **Leaves pending transactions** until the bank posts them.
- **Never re-books a transaction you deleted.**
- **Keeps foreign charges' original amount and currency** beside the amount your account was
  charged.
- **Categorises without AI:** your [Automations](../lucius/automations.md) rules and each payee's
  default category apply, as they do to any new transaction. Otherwise, if the bank gives a
  merchant category that's exactly the name of one of your categories, that one is used. No
  category is ever created; anything else stays **Uncategorized**.
- **First sync of an account:** books the history your bank serves, then moves the account's
  Starting Balance back to the oldest row, so the history counts and **today's balance doesn't
  change**. (Cash and card accounts only.)

A run books all its new transactions in one go: if something fails, nothing from that run is
booked, and the next run tries again.

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
| GoCardless, Enable Banking | Every 12 hours | Every 6 or 12 hours, or once a day |
| Plaid | Every 6 hours | Every hour, every 6 or 12 hours, or once a day |

Change it under **Schedule** on the sync's page (or in step 3 of setup). A bank that allows 4
reads a day is never offered more than 4 runs a day. The next run moves to your last sync plus
the new interval.

**Sync now** runs it at once. GoCardless and Enable Banking banks usually allow only **4 reads
per account per day**, and every read counts — scheduled, **Sync now**, or Lucius fetching the
bank in chat. Each account shows how
many reads it has left today. Once they're used, the account says when its next read is, **Sync
now** waits, and so does the schedule.

If the bank refuses (its daily limit, expired access), the sync waits instead of retrying: until
the limit resets, or a day for expired access. The connector page shows what the bank said.
Renew the bank's access on the connector page if it expired.

## Related pages

- [Bank & brokerage connectors](../connectors/bank-brokerage.md)
- [Automations](../lucius/automations.md)
- [Categories & payees](categories-payees.md)
