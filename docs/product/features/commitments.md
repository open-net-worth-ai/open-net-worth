---
title: Commitments
description: Plan big dated payments — a property, a loan, school fees, a savings goal — see how each is funded, and get warned before your cash runs short.
category: features
order: 80
audience: [user]
status: stable
related: [features/budgeting, features/transactions, concepts/the-ledger]
keywords: [commitments, loan, installments, goal, funding, reminders, big purchase, shortfall, safety floor]
---

# Commitments

**Commitments** are big payments you've signed up for, or are saving toward, that don't fit a
monthly budget line: a property paid in installments, a car loan, a year of school fees, a
wedding fund. For each one the tab shows the schedule, how much is funded and how much still
isn't, and it warns you when your household cash is projected to fall too low.

You create and change commitments **with Lucius** — he drafts the schedule and funding plan and
you confirm every number. So [connect an AI provider](../getting-started/connecting-an-ai-provider.md)
first.

## Add a commitment

1. Open **Commitments** and click **New commitment** (on an empty tab, **Draft a commitment with
   Lucius**, or one of the types under **Start with**).
2. Pick a type:
   - **Purchase** — property, a car, a big-ticket buy paid in installments.
   - **Loan repayment** — paying down financing on a fixed schedule.
   - **Scheduled payments** — a fixed series of dated payments, like school fees or insurance
     installments.
   - **Savings & investing goal** — building toward a target, such as a savings fund or a
     monthly investing plan.
3. Lucius opens with that request. Answer his questions — amounts, dates, which account or
   envelope will pay — and confirm what he proposes.

The commitment appears in **Active commitments**.

## Set your safety floor

The funding warning is measured against your **safety floor** — the least cash you want to keep
on hand. Set it in either place:

- **Plan › Cash Flow** → **Projection settings** → **Safety floor**, or
- **Settings › Profile** → **Low balance threshold**.

With no floor set, the warning fires only when cash is projected to go below zero.

## Read the overview

- The headline is **Cash stays above your $X safety floor** (with no floor set, **Cash stays
  above zero**), or **Short $X** (whole dollars, and "at
  today's exchange rates" when your book spans more than one currency) — the deepest your cash is
  projected to fall below your floor within the timeframe you picked (12 months to start). The
  line underneath names the month cash first dips below the floor and, if different, the month
  it's lowest, and wraps onto a second line rather than being cut off. The suggestion beside
  **Fix with Lucius** reads as plain sentences: what is short and when, then why (for example, the
  sale that is meant to cover it is planned for a date after the payment, in quotes with the
  number of weeks), then what would fix it. Change the window with the **Timeframe** control on the funding outlook.
- **Fix with Lucius** asks Lucius to suggest how to close a gap.
- The four figures under the headline: **Committed remaining**, **Due in 30 days**, **Recurring
  funding / mo** and **Cash earmarked**. **Due in 30 days** is everything going out from today
  through day 30, auto-debits and goal set-asides included, the same total as the Dashboard's
  **Next 30 days**. A to-do that repeats one of your funding sources (same date and amount, such
  as "loan disbursed") is money coming in, so it is not counted. **Recurring funding / mo** counts only the monthly funding sources (budget
  envelope top-ups) feeding these commitments, so it can read S$0.00 next to a scheduled
  set-aside: a one-off or scheduled set-aside on a goal counts under **Due in 30 days** instead.
- **Needs your attention** lists payments overdue, payments that look paid, and actions coming
  due soon. Its **overdue** count and total are the same ones the Dashboard and the daily
  briefing quote: unpaid or not-done items dated before today, funding that hasn't arrived
  excluded. **Mark paid** works straight from here.
- Funding you have marked as arrived (or linked to the transaction that brought it in) counts as
  received: it no longer shows as late or as coming in on **Next 30 days**, in the daily briefing,
  or in the funding outlook, so the money is not counted twice.
- The list shows each commitment's progress, what's left and whether it's on track. Switch
  between **Active**, **At risk**, **Completed** and **Goals**, and sort with
  **Sort: Most at risk**, **Sort: Next payment** or **Sort: Remaining**.

The Dashboard's **Funding shortfall** flag is the same 12-month figure: both screens read one
shared fetch of it, so they can't differ because they asked at different moments.

## Manage a commitment

Click a commitment to open it. It has four tabs: **Overview**, **Schedule** (**Contributions**
for a goal), **Funding** and **Actions & events**.

**Record a payment**

1. In **Schedule**, find the installment and click **Mark paid** (**Mark done** for a goal).
2. Check **Amount paid**. Entering less records a partial payment: the rest stays due as a
   separate row.
3. Confirm. Or click **Link txn** to tie the installment to the real transaction in your ledger
   — it has to be a transaction that exists there; a deleted or made-up one is refused.
   An overdue installment also offers **Reschedule**.

**Change the funding** — in **Funding**, click **Edit** on a funding source to change its amount
or note. A source can be an account, an envelope in your budget, or income.

**Reminders** — in **Actions & events**, click the bell (**Remind me**) on a step to get a
reminder before it's due. Click it again (**Cancel reminder**) to turn it off. Each reminder is
an [Automation](../lucius/automations.md), so it also shows on the Automations page. Mark a step
**Mark done**, or **Not happening** to drop the funding it would have unlocked.

**Anything else** — adding a new funding source, adding a step, or changing the schedule — click
**Manage with Lucius** in that tab and describe the change.

**Finish or remove** — **Mark completed** when it's done (**Reactivate** undoes it). The **⋯**
menu has **Cancel commitment** and **Delete commitment**.

## What Lucius can do here

- Create a commitment of any type from a plain description, with its schedule and funding.
- Add, change or remove funding sources, installments and steps.
- Record a loan repayment, add reminders, and suggest how to fix a shortfall.

## Troubleshooting

**Creating a commitment just opens Lucius** — that's how commitments are made. If Lucius can't
answer, check your AI provider under [Connecting an AI provider](../getting-started/connecting-an-ai-provider.md).

**A new commitment shows as unfunded** — creating one doesn't fund it. Coverage rises only once
a real funding source (an account, an envelope or income) is linked to it.

**"Short" on Commitments differs from what I expected** — it depends on the timeframe you
picked; a longer window can find a deeper, later low point. The Dashboard always uses 12 months.

**A loan commitment doesn't match my bank** — it follows the schedule you gave it. It doesn't
check your rate or amortization with the lender.

## Related pages

- [Budgeting](budgeting.md)
- [Transactions and the ledger](transactions.md)
- [Automations](../lucius/automations.md)
- [The ledger](../concepts/the-ledger.md)
