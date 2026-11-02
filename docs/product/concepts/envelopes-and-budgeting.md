---
title: Envelopes and budgeting
description: How the budget on Plan works — the two methods, envelopes, Ready to Assign, targets, overspending, credit cards and commitments.
category: concepts
order: 40
audience: [user]
status: stable
related: [features/budgeting, concepts/categories, concepts/the-ledger, concepts/net-worth, getting-started/your-first-month]
keywords: [envelope, budget, zero-based, 50/30/20, ready to assign, rta, target, overspend, plan tab, credit card, move money, cover overspending]
---

# Envelopes and budgeting

Your budget lives on **Plan › Budget**. This page explains how it thinks. For click-by-click
setup, see [Budgeting](../features/budgeting.md).

## Two methods, one set of envelopes

The first time you open Plan, you choose a method. You can switch at any time without losing
anything — both use the same envelopes and categories.

- **Zero-Based** — every dollar of cash gets a job. You assign money to envelopes until
  **Ready to Assign** reaches zero, then spend from them. If you've used YNAB, this is the same
  idea. Most of this page is about Zero-Based.
- **50/30/20** — a quick check of your shape. Your monthly income is split into shares for each
  group (for example Needs, Wants, Savings), and the app shows how the month is tracking against
  them. You can give envelopes a monthly cap. There's nothing to assign and nothing to cover —
  overspending is reported, not blocked.

Only your **Cash** and **Credit** accounts are part of the budget. Savings in a fixed deposit,
investments and retirement accounts sit outside it.

## Envelopes and categories

A **category** is a label on a transaction. An **envelope** is a pot of budgeted money. One or
more categories link to an envelope, and their spending comes out of it: Coffee, Takeout and
Restaurants can all draw on one "Dining out" envelope without merging the categories.

Envelopes sit in **groups** (Bills, Needs, Wants and your own). Two groups are managed for you:
**Credit Card Payments** and **Commitments** (see below).

Each envelope row shows three numbers for the month:

- **Assigned** — what you put in this month.
- **Activity** — what was spent from it (or refunded into it).
- **Available** — what's left: last month's leftover, plus assigned, plus activity. Red means
  overspent.

## Ready to Assign

**Ready to Assign** is cash in your budget that hasn't been given to an envelope yet.

- **Income raises it.** New money coming into a Cash account lands here.
- **Assigning lowers it**, dollar for dollar.
- **Spending from an envelope doesn't touch it** — the envelope pays.
- It can go **negative** if you assign more than you have. That means you've promised money you
  don't have; take some back from an envelope.

Click the Ready to Assign figure to see the **Ready to Assign Breakdown** — what was carried in
from last month, what came in, what you assigned, and any overspending charged.

## Spending outside envelopes

Cash spending that no envelope covers — a transaction with no category, or a category you
haven't linked to an envelope — shows under **Outside envelopes** on the Budget tab, as
**Uncategorized** or as the category's own row with **Add to envelope…**.

It doesn't lower Ready to Assign straight away. It's held there until the month ends, so you
have time to label it:

- **Give it a category with an envelope before the month ends** and it moves into that
  envelope like any other spending. Nothing is charged to Ready to Assign.
- **If it's still outside an envelope when the month rolls over**, the amount is taken from
  next month's Ready to Assign, once.

Income on an unlinked category still raises Ready to Assign.

## Assigning, moving and covering

- **Assign** — type an amount in an envelope's **Assigned** cell. It comes out of Ready to
  Assign.
- **Move money** — click a positive **Available** amount to move some of it to another envelope
  or back to Ready to Assign. You can't move more than the envelope has available.
- **Cover overspending** — click a red **Available** amount and choose where to take the money
  from: another envelope, or Ready to Assign.
- **Auto-assign** — fills envelopes from their targets in one step (for example
  **Underfunded**), with a preview before anything changes.

## Overspending

Spend more than an envelope holds and its **Available** goes red. Nothing is quietly borrowed
from another envelope.

- **During the month**, Ready to Assign doesn't change — the envelope holds its own shortfall.
- **To fix it now**, cover it from another envelope or from Ready to Assign. Covering from Ready
  to Assign costs exactly the amount you cover.
- **If it's still red when the month ends**, the envelope resets to zero and the shortfall is
  taken from next month's Ready to Assign.

A positive leftover always rolls forward into next month's Available.

## Targets

A target tells the app how much an envelope needs, so it can show what's **Underfunded** and
fill it with **Auto-assign**. Pick how often (**Weekly**, **Monthly**,
**Yearly** or **Custom**), enter the amount under **I need**, and choose one of:

| Target | Use it for | How progress is measured |
|---|---|---|
| **Set aside** | Bills, subscriptions, saving a fixed amount each period | What you've assigned this period. Spending it doesn't undo the target. |
| **Fill up to** | Groceries, fuel, fun money | Leftover plus this period's assignment, up to the amount. |
| **Have a balance of** (Custom only) | Long-term savings toward a date | The envelope's balance. Spending sets you back. |

With **Custom**, a **Set aside** target can have a date: progress then counts everything you've
put in across months, and spending doesn't set it back.

## Credit cards

Every Credit account gets its own envelope in **Credit Card Payments**, created for you. When
you buy something on the card from a funded envelope — say $50 of groceries — $50 moves from
Groceries into the card's payment envelope. By the time the bill comes, the money to pay it is
already set aside. Paying the card (a transfer from Cash to the card) spends from that envelope.

Card spending never changes Ready to Assign. Spending in a category without a funded envelope,
or debt you already had before you started, isn't covered automatically — the card's row shows
**"$X more needed for this bill"** so you can assign money to its payment envelope.

A card can also hold **credit**: you paid it more than it owed, or a refund arrived after the
bill was paid. That credit is your money, so it counts toward Ready to Assign, and spending on the
card uses it up before any new debt is set aside — the same way YNAB treats it.

## Commitments

A [commitment](../features/commitments.md) — a purchase plan, an installment schedule, a loan
repayment — gets its own envelope in the **Commitments** group while money is still owed. Set
its target yourself to save toward the next payment.

## Starting over

If you'd rather not budget around years of old history, the **⋯** menu beside the method button
offers **Start fresh from ‹this month›…**. Envelopes stop carrying balances from earlier months
and this month's assignments are cleared, so you budget the month again from scratch. No
transactions or envelopes change, and **Undo** right after puts everything back. See
[Budgeting](../features/budgeting.md#start-fresh).

## Related

- [Budgeting](../features/budgeting.md) — set up and use either method, step by step
- [Categories](categories.md)
- [Net worth](net-worth.md)
- [Your first month](../getting-started/your-first-month.md)
