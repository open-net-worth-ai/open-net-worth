---
title: Bank sync without AI
description: Book a bank connector's transactions by fixed rules, no AI — history first, then what's new on a schedule; bank-side changes wait for your review.
category: features
order: 172
audience: [user]
status: beta
related: [connectors/bank-brokerage, lucius/automations, features/categories-payees, features/accounts]
keywords: [bank sync, sync without ai, no ai, data sync, gocardless, enable banking, plaid, history, 90 days, starting balance, review, changed transaction, withdrawn transaction, duplicate, read limit, rate limited, sync now, categorise, payee default, merchant category code, mcc]
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
2. Under **Sync** on the connector's page, click **Set up sync**. It opens the bank's own page in
   **Automations → Data sync**, with three steps:
   1. **Choose the accounts to sync.** Every linked account is ticked. Untick one to leave it
      out: it's never read from the bank or booked.
   2. **Bring in your history.** **Import history** books the history your bank serves (90
      days for most European banks) into the ticked accounts, and moves each account's Starting
      Balance back to its oldest row, so today's balance doesn't change. It shows what came in.
      Safe to run again.
   3. **Keep it in sync.** **Turn on sync without AI** books new transactions on a schedule from
      then on, or choose **Let Lucius sync it instead**.

You can turn the sync on without step 2: its first run brings the history in. Afterwards the
connector page shows one line — who books the bank's transactions, the next run, anything to
review — and **Manage** opens the bank's page: the controls, **To review** and the run history.

## One way to sync at a time

**Sync** has three choices:

| Choice | Who books this bank's transactions |
|---|---|
| **Off** | Nobody. |
| **Sync without AI** | Fixed rules, on a schedule. |
| **Lucius** | A Lucius Automation, such as **Sync GoCardless**. |

Only one can be on. Choosing **Sync without AI** turns off that bank's Lucius sync Automation and
says which one it turned off, so nothing is booked twice. While it's on, turning that Automation
back on is refused, with a message telling you to choose **Lucius** on the connector instead.
Choosing **Lucius** turns the Automation back on, or opens the template if you don't have one.

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

## Let Lucius categorise what's left (optional)

Tick **Let Lucius categorise what's left** to have Lucius look at the transactions a run booked
**Uncategorized** — only those — and pick one of **your existing categories** for each, by the
same rules the book-transactions skill uses. It never creates a category, and leaves a row
Uncategorized when nothing fits. It runs within a minute of the sync, uses your AI model, and the
run's history says how many it categorised. Booking never waits for it and doesn't depend on it.

Lucius gets no tools for this: it can only suggest a category for those rows, so text in a bank
transaction can't make it do anything else.

## When the bank changes something

A bank sometimes changes a transaction after it's booked, or stops listing it. A run never
applies that on its own. It waits under **To review**, on the sync's page:

| What happened | Your choices |
|---|---|
| The bank changed the amount, date or wording | **Use the bank's version** or **Keep mine** |
| The bank no longer lists it | **Delete it** or **Keep it** |
| A pending transaction posted at a different amount | **Use the posted amount** or **Keep mine** |
| It looks like a transaction already in your book | **Same transaction** (link them), **Book it separately**, or **Ignore it** |

If you edited a transaction by hand, the review says so before you replace your edit. Nothing
in the ledger changes until you choose.

## How often it runs

| Bank | Every |
|---|---|
| GoCardless, Enable Banking | 12 hours |
| Plaid | 6 hours |

**Sync now** runs it at once. GoCardless and Enable Banking banks usually allow only **4 reads
per account per day**, and every read counts — scheduled, **Sync now**, or Lucius fetching the
bank in chat. Each account shows how
many reads it has left today. Once they're used, **Sync now** says when the next read is
possible, and the schedule waits too.

If the bank refuses (its daily limit, expired access), the sync waits instead of retrying: until
the limit resets, or a day for expired access. The connector page shows what the bank said.
Renew the bank's access on the connector page if it expired.

## Related pages

- [Bank & brokerage connectors](../connectors/bank-brokerage.md)
- [Automations](../lucius/automations.md)
- [Categories & payees](categories-payees.md)
