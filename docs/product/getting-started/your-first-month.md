---
title: Your first month
description: A worked example of a first month — accounts on day one, a zero-based budget, spending, an overspend, and what happens when the month rolls over.
category: getting-started
order: 60
audience: [user]
status: stable
related: [concepts/envelopes-and-budgeting, features/budgeting, concepts/net-worth, concepts/the-ledger, getting-started/your-first-account-and-transactions]
keywords: [walkthrough, example, ready to assign, budget, zero-based, envelope, overspend, rollover, net worth]
---

# Your first month

Here's what a first month typically looks like, with example numbers, so you know what to expect
on screen. Your own figures will differ.

## Day 1: accounts and starting balances

You add four accounts, by hand or by sending Lucius screenshots
([how](your-first-account-and-transactions.md)):

| Account | Type | Today's balance |
|---|---|---|
| Everyday checking | Cash | $4,250.00 |
| Savings | Cash | $18,000.00 |
| Visa card | Credit | $612.40 owed |
| Brokerage | Investment | holdings added on **Investments** |

Each one starts from today's balance — no years of history needed. As soon as they're in, the
**Dashboard** shows your net worth: everything you own minus the $612.40 you owe.

## Day 1: pick a budget method

Open **Plan** → **Budget**. It asks **How do you want to budget?** with two methods:

- **50/30/20** — a quick monthly check of needs, wants and savings. Nothing to maintain.
- **Zero-Based** — every dollar gets a job, envelope by envelope (like YNAB).

This walkthrough uses Zero-Based. Select it and click **Start with Zero-Based**. Then choose
**Suggest from my spending**, **Set up manually**, or **Set up with Lucius** to create your
envelopes (for example Rent, Groceries, Dining Out, Transport). Not sure which method fits? Click
**Help me choose**. You can switch later.

## Day 1: give your money jobs

**Ready to Assign** shows money in your **Cash** accounts that has no job yet — here about
$22,250.00 (checking plus savings). Assign some:

| Envelope | Assigned |
|---|---|
| Rent | $1,600.00 |
| Groceries | $500.00 |
| Dining Out | $150.00 |
| Transport | $200.00 |
| New laptop (a goal six months out) | $300.00 |

Ready to Assign drops by $2,750.00. What's left isn't wasted — it's simply not assigned yet.
Leaving it unassigned is better than forcing it into a category it doesn't belong in.

The card: each credit card gets its own payment envelope in the budget. The $612.40 you already
owe shows there as not yet set aside. If you'll pay it off this month, assign money to it.

## Week 1: spending

You buy groceries for $63.40 and log it against Groceries (or Lucius logs it from your bank
email). Groceries' **Available** drops from $500.00 to $436.60. You eat out for $28.00 against
Dining Out.

**Ready to Assign doesn't move when you spend** from a funded envelope — the money already had a
job. It only changes when money comes in, when you assign, or when you move money in or out of
it.

## Week 2: an overspend

You spend $580.00 on flights, categorized to Travel — an envelope you never funded. Travel goes
red: −$580.00.

Nothing is moved behind your back. Ready to Assign doesn't change, and no other envelope is
quietly drained. Travel just sits red so you can see it. You have two choices:

- **Cover it now.** Click **Cover** on Travel and take $580.00 from Ready to Assign or from
  another envelope. Covering from Ready to Assign lowers it by $580.00 right away.
- **Leave it.** At month-end, Travel resets to $0.00 and the $580.00 is charged to next month's
  Ready to Assign instead — next month starts with $580.00 less to assign.

Either way the overspend is paid for exactly once.

## Month-end: the rollover

Money left in an envelope carries forward. Groceries starts next month with $436.60 already in
it, and New laptop with $300.00 — they don't reset to zero. Only overspent (red) envelopes reset,
as above.

## All month: net worth keeps itself up to date

Every transaction, price update and currency change flows straight into your net worth. If your
brokerage gains $340.00 overnight and you pay $200.00 off the card, both show on the Dashboard the
next time you look — no "recalculate" button. [Net worth](../concepts/net-worth.md) explains
exactly what counts.

## Setup suggestions in the corner

Until you've set them up, a small card in the bottom-left corner (on a phone, just above the tab
bar) suggests the things that make
Lucius useful away from the app: [Telegram](../connectors/telegram.md), connecting your email
([Gmail](../connectors/email-ingestion.md)), a daily briefing, an email digest (once email is
connected), and weekly and monthly reports. Each button opens the setup screen or the ready-made
Automation for it. If Telegram or email stops working, the card says so and offers to fix it.

It is meant to be easy to ignore:

- None show in your first 3 days, while Lucius is still helping you set up your book — except a
  card saying Telegram or email has stopped working.
- It waits its turn behind other guides: if a screen's introduction or tour has already shown
  during a visit, no card appears until your next visit, and it never shows over an open
  dialog. On a phone it stays off the Lucius screen, where you type.
- Only one card shows at a time, and a card disappears as soon as that thing is set up.
- After you close a card or follow one, no card shows for 3 days.
- A card you close comes back once, two weeks later. Close it again and it's gone for good.
- What you've closed is remembered on the server, so it stays closed on every device.

## Where to go from here

- [Envelopes and budgeting](../concepts/envelopes-and-budgeting.md) — the full picture behind
  Ready to Assign, targets and overspending.
- [Budgeting](../features/budgeting.md) — every control on the Budget screen.
- [The ledger and how transactions work](../concepts/the-ledger.md) — what's recorded underneath
  the register.
- [Positions, lots, and cost basis](../concepts/positions-lots-and-cost-basis.md) — how the
  investment side of net worth works.
