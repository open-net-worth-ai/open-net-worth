---
title: A card in credit is budget money
status: Accepted
date: 2026-11-02
deciders: repo owner + Claude
---

# A card in credit is budget money

Amends `2026-07-09-ynab-engine-credit-card-payment-envelopes` (a card is never on the money side
of Ready to Assign) and `2026-09-28-iterative-available-walk-and-uniform-card-coverage` (the
coverage cap is a month aggregate per envelope).

## Context

`cashflow.is_cash_account` keeps every card out of Ready to Assign's money side, because a card's
debt is already set aside in its payment envelope; counting the card too would count that money
twice. The rule was written for a card that owes money. It never considered a card that owes
nothing and holds credit, which happens when a card is paid more than it owes or a refund lands
after the bill is paid.

Paying a card that owes nothing pulls its payment envelope below zero, the envelope resets at the
roll, and the hole is charged to next month's Ready to Assign, exactly as YNAB does. YNAB then
counts the card's credit as money, so the two cancel. This app did not count it, so the overpaid
money left Ready to Assign for good while it still existed as card credit. A YNAB import of a test
plan exposed it: Test Visa had been paid 1,475 against 100 owed, every envelope matched YNAB to the
cent, and Ready to Assign was 1,375 short.

Four transactions were then added to Test Visa (1,375 in credit) on a YNAB test plan, read back,
and deleted, on 2026-11-02:

| On Test Visa | Ready to Assign | Spending category | Payment category | Card balance |
|---|---|---|---|---|
| spend 30 under Groceries | unchanged | −30 | unchanged (−100) | 1,375 → 1,345 |
| refund 30 under Groceries | unchanged | +30 | unchanged (−100) | 1,375 → 1,405 |
| spend 1,400 under Rent | −100 | −1,400 | −100 → +25 | 1,375 → −25 |

and the plan's own months showed Ready to Assign in August above the balance identity by exactly
that month's 450 overpayment, i.e. the negative payment category held in-month like any other.

## Decision

1. **A card's balance above zero is budget money.** `planning._card_credit_as_of(m)` = Σ over every
   payment-tracked card of `max(0, balance at month end)`, added to cash in the identity:

       ready_to_assign = cash + card_credit − baseline − held_in_envelopes
                         − held_outside_envelopes − card_spend_not_covered

   It is a separate term on the wire (`card_credit`, beside `on_budget_cash`), and the Ready to
   Assign breakdown and the fresh-start dialog show it as "Credit on your cards". Dashboard cash on
   hand is not this and still means `cash` accounts only. A card that owes money adds nothing.
2. **A card in credit spends its credit first.** Each card's balance and payment-envelope Available
   are followed through the month in (date, created_at, id) order. A spend of `amt` pays
   `min(amt, free)` from credit and only the rest becomes debt, which is covered from the spending
   envelope exactly as before. `free = max(0, balance + min(0, payment_available))` for a spend on a
   linked envelope (the envelope's money first fills a payment envelope that is negative this month,
   so the payment envelope ends holding exactly the new debt — YNAB's −100 → +25 above), and
   `max(0, balance)` when there is no envelope to move money from. The credit-paid part behaves like
   cash spend: it draws the envelope down and is not cancelled, and with no envelope it holds until
   the roll like uncategorized cash spend.
3. **A refund to a card drains its payment envelope only by the debt it shrinks**,
   `min(refund, owed)`. The rest is new credit, and cancels its own category's hold like a cash
   refund.
4. **Paying a card is unchanged.** It still drains the payment envelope uncapped and may push it
   negative; the credit an overpayment creates now counts under 1, so the charge at the roll and
   the credit cancel.

## Consequences

- An overpayment or a refund past zero stays in Ready to Assign. In the month of an overpayment
  Ready to Assign reads higher by the overpaid amount, because the negative payment envelope is
  held in-month like any overspent envelope; YNAB shows the same.
- A card that owes money behaves exactly as before: `free` is 0, so every spend and refund on it
  takes the old path.
- The coverage cap is no longer order-invariant: how much of a spend is paid from credit depends
  on what came before it in the month, so the date order is part of the rule.
- The identity property test re-derives the no-bucket hold from the raw rows with its own running
  card balance, and the flow-terms property test re-derives Δ card credit the same way, so neither
  check agrees with the engine by construction.
- Not covered: money arriving at a card by a categorized transfer still folds as Activity without
  touching the payment envelope, and a Ready-to-Assign inflow landing on a card that owes money
  still leaves Ready to Assign unchanged. Neither was observed in YNAB here.
