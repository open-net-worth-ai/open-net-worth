---
name: can-i-afford-it
description: "Use this skill when the user asks whether a purchase or a change fits their money — a big buy, a trip, a new monthly cost, a pay cut, losing their job, how long their cash would last. Use it for 'can I afford X', 'should I wait on the car', 'how many months of runway do I have', even when no price is given yet. Not for progress toward savings goals or retirement (on-track), not for which investment to buy, not for setting budget targets (budget-review)."
---

Answer whether the user can afford something, with the trade-off spelled out. Read-only. It is
a projection of their own numbers, never a recommendation to buy or not.

## Gotchas

- Cash on hand is the `cash` accounts only — simulate_cashflow's starting balance. Investments,
  fixed deposits and retirement are a second line: mention them separately, with what touching
  them costs (selling, penalties, tax), never as spendable cash.
- simulate_cashflow models monthly income and expenses; it has no one-off purchase. A one-off is
  cash on hand minus the price, worked with the calculate tool.
- Money already spoken for is not free: active commitments' installments (get_commitments) and
  what savings goals need each month (get_goal_allocation).
- Every figure comes from a tool or the calculate tool — never mental arithmetic.

## Everyday purchase: ask the advice tool first

A purchase about to be paid for — a gift, dinner, headphones, a booking, a subscription — is
one call: get_spending_advice_before_payment(amount, currency, merchant, category, pay_with).
It reads the envelope, Ready to Assign, bills due before the next income and the card, and
returns `verdict`, `reasons` and `options`. Answer from it:

```
<Yes / Yes, if you move money / Wait until <date> / Not this month> — <its first reason>.
<Each other reason, one line.>
Options: <its options in words — always including "buy anyway">
```

Never shame; talk about the money, not the person. Offer a cover as a move the user
approves, never make it. If the question is a big one-off, a new monthly cost, or how long cash
would last, use the steps below instead.

## Steps

1. Pin down the question: amount, one-off or monthly, when, and how it is paid. Ask only for what
   is missing — "how long would my money last" needs nothing.
2. Load the tools: simulate_cashflow, get_commitments, get_goal_allocation.
3. Baseline: simulate_cashflow with no overrides — cash on hand, the monthly income and expenses
   it uses, income_runway_months. Then get_commitments(status="active") and get_goal_allocation.
4. Scenario:
   - One-off: cash after the purchase, the months of expenses that leaves, and the months of
     surplus it takes to rebuild.
   - New monthly cost: simulate_cashflow(expense_override=<monthly expenses + the cost>).
   - Less or no income: simulate_cashflow(income_override=<new figure, 0 for none>,
     include_commitments=true).
5. Answer in this shape:

```
<Yes / Yes, but / Not yet> — <one-line reason>.
Leaves: <months of expenses in cash> · <monthly surplus after>
Pushes back: <goal or installment it collides with, or "nothing">
<If not yet: what would make it work — months of saving at today's surplus, or which goal to slow>
```

Flag it when fewer than 3 months of expenses would remain in cash.
