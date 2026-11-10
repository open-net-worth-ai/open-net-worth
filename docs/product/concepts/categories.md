---
title: Categories
description: Categories are optional labels on transactions — what a new book starts with, how a category gets filled in, and how categories relate to envelopes.
category: concepts
order: 30
audience: [user]
status: stable
related: [concepts/the-ledger, concepts/envelopes-and-budgeting, features/categories-payees, getting-started/your-first-account-and-transactions]
keywords: [category, uncategorized, label, payee default, starter categories, envelope]
---

# Categories

A category is a label on a transaction — "Groceries", "Rent", "Dining out". It groups your
spending for reports and the budget. It never changes a balance or your net worth, and a
transaction is complete without one.

## What a new book starts with

A new book comes with 11 spending categories, each with a matching budget envelope in three
groups:

- **Bills** — Rent/Mortgage, Phone & Internet, Utilities
- **Needs** — Groceries, Medical expenses, Emergency fund
- **Wants** — Subscriptions, Dining out, Entertainment, Vacation, Stuff I forgot to plan for

Rename, delete or add to them freely in **Manage categories** (from the Accounts header, or the
**⋯** menu on **Plan › Budget**). See [Categories and payees](../features/categories-payees.md)
for the steps.

Each category has a **kind** — Expense, Inflow (income), Transfer or Investment — so income
categories stay out of your spending reports.

## Leaving it blank is fine

A transaction with no category shows as **Uncategorized**. That's a normal, permanent state, not
an error to fix. "Uncategorized" isn't a category in your list — you can't pick or rename it;
it's simply what a blank label looks like. Accounts, balances and net worth are complete without
any categories at all.

On **Plan › Budget**, uncategorized spending appears under **Outside envelopes** until you label
it — see [Envelopes and budgeting](envelopes-and-budgeting.md#spending-outside-envelopes).

## How a category gets filled in

When a transaction is saved:

1. **The category you chose** is used (or the one Lucius chose, or the import review
   suggested).
2. If it's still blank, **the payee's default category** for that kind of transaction fills it
   in. Set one in **Manage payees → Default category by kind** — e.g. "Trader Joe's" →
   Groceries — and you never categorize that payee by hand again. An expense default only
   applies to expenses, an income default only to income. A kind with no default picks one up
   from the first transaction of that kind you categorize for the payee, unless you set it to
   **Uncategorized**, which keeps it blank.
3. Otherwise it stays blank.

An [Automation](../lucius/automations.md) that runs on new transactions can also set the
category as each one arrives — useful for rules like "anything from GRAB under $20 is
Transport".

## A category that doesn't exist is refused, never invented

If a transaction names a category you don't have — a typo, or a name Lucius guessed — the save
is refused with *"Unknown category '…'"* and a list of the closest matches. A misspelling never
quietly becomes a new category. Create the category first (in **Manage categories**, or with
**+ New category "…"** in the category picker), then save again.

## Renaming is safe

Each category has a hidden, permanent ID behind its name. Rename "Dining" to "Restaurants" and
every past transaction, budget and report follows — nothing is re-labelled one by one. You (and
Lucius) always refer to a category by its name.

## Categories and envelopes

A category is only a label; the money in your budget lives in **envelopes**. One or more
categories link to an envelope, and their spending comes out of it — Coffee, Takeout and
Restaurants can all draw on one "Dining out" envelope. A spending category that isn't linked to
any envelope shows **Not budgeted** in Manage categories. Link it with **Add to envelope…**. See
[Envelopes and budgeting](envelopes-and-budgeting.md).

## Related

- [Categories and payees](../features/categories-payees.md) — adding, renaming, deleting and
  defaults, step by step
- [Envelopes and budgeting](envelopes-and-budgeting.md)
- [The ledger and how transactions work](the-ledger.md)
