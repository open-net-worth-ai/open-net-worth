---
title: The ledger and how transactions work
description: What a transaction is in Open Net Worth — the five kinds, how transfers and splits work, and the mistakes the app refuses to let through.
category: concepts
order: 10
audience: [user]
status: stable
related: [concepts/accounts-and-opening-balances, concepts/categories, concepts/base-currency-and-fx, features/transactions]
keywords: [ledger, transaction, transfer, split, kind, expense, income, adjustment, investment, register]
---

# The ledger and how transactions work

Every amount you record — by hand, from a statement, or through Lucius — goes into one
**ledger**: the list of every transaction in every account. Your account balances and your net
worth are always worked out fresh from it, so there's no stored total that can drift out of
date.

You don't need to know any bookkeeping. There are no debits and credits to balance — just
transactions, each with a **kind**.

## The five kinds

| Kind | What it is | Shown in the edit form as |
|---|---|---|
| Expense | Money left an account — a purchase, a bill, a fee. | **Expense** |
| Income | Money came in — salary, interest, a refund. | **Income** |
| Transfer | Money moved between two of **your own** accounts. | **Transfer** |
| Investment | A buy or sell that touches your portfolio. | **Invest** |
| Adjustment | A correction to an account's balance. Every account's Starting Balance is one. | "Balance adjustment" badge |

In an account's register, the **+/−** toggle on a new row makes it income or an expense. To
record a transfer, pick **To/From <account name>** in the Payee field. You can change a
transaction's kind afterwards by opening it.

Expenses, income, transfers and investments can carry a [category](categories.md); an adjustment
never does, because a correction isn't spending.

## Transfers: one transaction, two accounts

"Move $500 from Checking to Savings" is **one** transaction with two ends, not an expense in one
account plus income in another. That's why a transfer never shows up as spending or income, and
why the two accounts can't disagree about it — there's no way to record money leaving Checking
without it arriving in Savings.

The app refuses a transfer that doesn't make sense:

- **Only one account named** — money paid to someone outside your accounts is an expense, not a
  transfer. The message says: *"a transfer needs both from_account_id and to_account_id — for
  money sent to an external party, record an expense instead"*.
- **The same account on both ends** — a fee or interest charged *on* an account is an expense or
  income. Two currency wallets at the same broker need to be two separate accounts
  (e.g. "IBKR USD" and "IBKR SGD").
- **Amounts that imply an impossible exchange rate** — moving 1,000 USD into a VND account
  and entering 1,000 VND on the other side, for example. If one side is more than ten times off
  the known rate, or both sides show the same number across two currencies that aren't worth the
  same, the transfer is refused with a message explaining which side looks wrong. Usually one
  amount was copied across without converting it.

When the two accounts are in different currencies and you only know one amount, enter that one;
the other side is converted for you.

## Splits

One purchase can cover several categories — a $140 supermarket trip that's $90 groceries and
$50 household goods. Open the transaction and click **Split into multiple categories**. Each
line needs a category and an amount, and the lines must add up to the total exactly: the editor
shows **Balanced** when they do, or what's still **Remaining**. Nothing is rounded away for you,
because small silent corrections are exactly how a budget drifts.

Only expenses and income can be split.

In the register a split shows its largest line's category and how many more there are (for
example **Rent +5**), on a blend of every line's colour; tap it to list the lines under the
transaction. The detail drawer lists every line, with its amount, in the **Category** row. An
incoming split shows its lines with a **+**: money that lands on a spending category goes back
into that category's envelope, the same as a refund.

## Cleared

Tick **Cleared** on a transaction once you've confirmed it against your bank or card statement.
Linking a statement row during an import marks the matching transaction cleared for you. It
doesn't change any amounts — it's a way to see what's been checked.

## Related

- [Accounts and opening balances](accounts-and-opening-balances.md) — the special first
  transaction every account has
- [Categories](categories.md) — labelling spending, or leaving it blank
- [Base currency and FX](base-currency-and-fx.md) — how a foreign-currency transaction is valued
- [Transactions](../features/transactions.md) — adding, editing and searching transactions
