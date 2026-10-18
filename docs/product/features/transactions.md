---
title: Transactions and the ledger
description: Add, edit, split, transfer, review and search transactions in an account's register — step by step.
category: features
order: 30
audience: [user]
status: stable
related: [concepts/the-ledger, concepts/categories, features/accounts, features/categories-payees, features/budgeting]
keywords: [transactions, ledger, register, split, transfer, reconcile, needs review, receipts, cleared, approve]
---

# Transactions and the ledger

Every amount that goes in or out of an account is a transaction, listed in that account's
register. The register is the source of truth for your budget's activity, your cash figure in net
worth, and your spending reports.

You can add transactions by hand (below), by [importing a statement](importing.md), from
[email](../connectors/email-ingestion.md) or a [bank connection](../connectors/bank-brokerage.md),
or just by telling Lucius: *"I paid $42.80 for dinner at Nando's on my Visa yesterday."*

## Add a transaction

1. Open **Accounts** and pick the account in the list.
2. Click **Add transaction** (beside the search box above the register).
3. Fill in the row:
   - **Date**.
   - **Payee** — start typing a name. To record a **transfer**, pick **To/From** *another
     account* from the list instead of a payee.
   - **Category** — optional. Leave it blank and it shows as *Uncategorized*.
   - **Description** / **Memo** — optional (which of these show depends on the register's
     columns).
   - **Amount** — click the **−**/**+** button beside it to switch between money out (outflow)
     and money in (inflow).
4. Press **Save**, or **Save and add another** to keep entering.

## Edit, delete or split a transaction

1. Click a row in the register to open its detail drawer.
2. Click **Edit transaction**, change any field, then **Save changes**.
3. To **split** it across categories (for example a supermarket run that is part groceries, part
   household), click **Split into multiple categories** under Category and add a line per
   category. The lines must add up exactly — the editor shows **Balanced** when they do, or the
   amount **Remaining**.
4. To remove it, click **Delete**. To erase it for good, click **Delete permanently** — see
   [Delete or erase](#delete-or-erase-a-transaction).

The drawer also shows the transaction's **Activity** (every change made to it, and by whom) and
its **Receipts** — see [Attach a receipt](importing.md#attach-a-receipt).

To change many at once, tick the rows' checkboxes. A bar appears for setting the category,
marking them cleared, uncleared or reviewed, deleting them, and (under **More**) moving them to
another account or setting a description.

## Mark transactions cleared

A transaction is **cleared** once it has posted at your bank. Clearing is what reconciliation
checks against; it is separate from categorizing.

- Click the cleared mark in the register row, or
- in the detail drawer, tick **Cleared** (ticked reads *Posted*, unticked *Pending*). While
  editing, it saves with your other changes.

The Starting Balance transaction is always cleared.

## Find transactions

Type in the search box above the register to search payee, memo and category. Click into the
empty box for one-click filters — **Uncategorized**, **Uncleared** and **Needs review** — or type
them: `uncategorized`, `status:uncleared`, `status:review`. Cleared and Uncleared are one choice:
picking one replaces the other.

## Review new transactions

A transaction added by an import, by Lucius, from email, or by an Automation can be flagged
**needs review**. It already counts in your balances and reports — review is a check, not a
holding area.

1. In Accounts, click **N to review** to list them, or **Start review** to go through them one
   card at a time.
2. On each card, correct the account, amount, date, payee or category if needed.
3. Click **Approve** to save it and clear the flag, **Skip** to leave it for later, or **Reject**
   to delete it (you confirm first). On a touch screen, swipe right to approve, left to skip.

Hovering the **Review** tag on a row says what Review means.

When transactions are waiting, a new Lucius conversation shows a **Needs attention** item such as
*7 transactions to review*; its **Review** button opens the same cards. The Dashboard's **Needs
attention** lists them too, beside its other flags.

Only you approve. You can ask Lucius to approve rows — *"approve the ones that look right"* — and
he shows you a confirmation card listing each one first. An [Automation](../lucius/automations.md)
can add, edit or re-flag a transaction, but can never clear the flag.

## Transfers, investments and the Starting Balance

- A **transfer** always has two sides, one in each account, and both stay in sync — edit one and
  the other follows. Both accounts must exist (one may be archived).
- An **Invest** transaction is a buy (cash out, **−**) or a sell (cash in, **+**). While editing,
  click the sign beside the amount to switch. Its payee is the broker or platform (for example
  IBKR) — set automatically when the trade is recorded against an account with an institution,
  and editable in the drawer like any other payee. A buy or sale recorded against a holding
  writes what it traded as the description — for example **Sold 60 KSTL @ 150.00 USD · fees
  1.25 USD** — and leaves the memo free for your own note.
- A **balance adjustment** (created when you reconcile) never has a category and is left out of
  spending reports.
- The **Starting Balance** can't be deleted, uncleared, freely edited or merged — change the
  account's opening balance from **Edit account** instead.

## What Lucius can do here

- Add one or many transactions from a plain description, including transfers and splits.
- Find and fix categorization mistakes, or update many transactions at once.
- Trace a transaction's full history.
- Find and merge likely duplicates, keeping the history intact.
- Attach a receipt, or read one that's attached.

## Things that trip people up

- **Category is optional.** No category shows as *Uncategorized* — a blank, not a category.
  Lucius won't invent a category you don't have; he asks or leaves it blank.
- **A foreign-currency amount is converted at the rate on its date**, not today's. Net worth still
  values your current *balances* at today's rate. See [Base currency & FX](../concepts/base-currency-and-fx.md).
- **Split lines must add up exactly** — a cent off is rejected, not rounded.
- **Deleting an account that has transactions deletes them too**, after you confirm. To keep the
  history, archive the account instead. See [Accounts](accounts.md).

## Delete or erase a transaction

The drawer has two buttons, and they do different things:

- **Delete** removes the transaction from your balances, budget and reports. The app still keeps
  a record of it: if Lucius deleted it, [undoing his change](../lucius/permissions.md#undoing-a-change)
  brings it back, and the same bank email or statement line isn't booked again by accident
  (Lucius says *"you already deleted this"*). Use it for ordinary clean-up.
- **Delete permanently** erases it. The transaction, the contents of its **Activity** history,
  every copy kept for undo, the copy of the email or bank record it came from, and receipts
  attached only to it are deleted. Its payee goes too when nothing else uses it and you never
  set it up by hand. Only one line remains in its
  history: *"Deleted permanently. Its details were erased."* There's no undo. The re-import guard
  still works, so the same email or statement line isn't booked again; it keeps only that
  item's reference, not the amount, date or description.

**Delete permanently** doesn't change files you uploaded (a statement still shows the line),
conversations with Lucius that mention it (delete those separately), or backups made before you
erased it — they keep it until they expire, after 30 days by default (see
[Backup and restore](../operations/backup-and-restore.md)). It's refused for a transaction that a
trade, vest, dividend, asset or commitment payment still uses; undo that first, or use **Delete**.
If your bank connection fetches that transaction again, the app stores the bank's copy again, but
doesn't book it.
A Starting Balance can't be deleted either way.

## Related pages

- [Accounts](accounts.md)
- [Categories and payees](categories-payees.md)
- [Importing statements and receipts](importing.md)
- [Budgeting](budgeting.md)
- [The ledger](../concepts/the-ledger.md)
- [Categories](../concepts/categories.md)
