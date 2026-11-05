---
title: Import from Actual Budget
description: Bring a whole Actual budget here once. You review its accounts, categories, transactions and Assigned first, and the result is checked against Actual.
category: features
order: 171
audience: [user]
status: stable
related: [features/imports-and-sync, connectors/bank-brokerage, operations/backup-and-restore, concepts/envelopes-and-budgeting]
keywords: [actual, actual budget, import, migrate, migration, move, review, verify, tracking, carryover, hold, to budget, starting balances, income categories]
---

# Import from Actual Budget

If you budget in **Actual Budget**, you can bring a whole budget here in one go: its accounts,
categories, transactions and each month's Assigned amounts. You see everything before anything is
written, and afterwards the figures are checked against Actual's. Keeping the two in step on a
schedule isn't available yet; this is an import you run when you choose.

It needs your own Actual server, and it isn't available on a hosted instance. Your budget in Actual
is only read: nothing here changes it.

## Before you start

- **Turn off the YNAB sync** if it's on. A book syncs with one app at a time, so an import from
  Actual is refused while YNAB's sync is on, or while a YNAB import or sync is running. You can
  still look at the review.
- **Pick the right budget.** The server can hold several, and importing the wrong one writes
  someone else's money into your book.

## What the review asks

The review shows what would come across. Nothing is written until you import. It works like the
[YNAB review](imports-and-sync.md#set-it-up): each account can go to a new account or one you
already have, and each category to a new category or one of yours. Actual leaves two things for
you to settle.

**A type for every account.** Actual only knows whether an account is on budget or off budget, so
it can't tell a credit card from a current account. A card imported as cash would inflate your cash
on hand and Ready to Assign, so the review shows a type for each new account:

| In Actual | Types you can choose | Starts as |
|---|---|---|
| On budget | Cash, Credit | Credit if the balance is negative and the name looks like a card; otherwise Cash |
| Off budget | Fixed deposit, Investment, Retirement, Receivable, Loan | Investment |

An account you link to one already here keeps the type it has, and must fit the same choices.

**A currency, if the budget doesn't state one.** The review starts from your book's base currency.
A budget that states a currency keeps it.

## What comes across, and how

- **Income categories** come in as income categories, with no envelope. Salary raises Ready to
  Assign when it arrives. They pair only with your income categories, and your spending categories
  only with spending ones.
- **Starting Balances** isn't a category here. A row in it stays the account's Starting Balance,
  with no second row beside it.
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
  says so.

## Importing and checking

**Import** takes a [backup](../operations/backup-and-restore.md) first and names it in the report; if
the backup fails, nothing is imported. The import runs on the server, so you can close the tab.
Running it again is safe: nothing is booked twice, and no category is made twice.

Afterwards it compares these with Actual, row by row:

- each account's balance, cleared balance and number of transactions;
- each envelope's Assigned and available for the current month.

A tracking budget compares its accounts only. **Verify** runs the same comparison again whenever you
like, against Actual's figures at that moment.
