---
title: Budgeting
description: Set up a budget on Plan › Budget — Zero-Based envelopes (Ready to Assign, targets, card payments) or a simple 50/30/20 split — plus the Cash Flow projection.
category: features
order: 60
audience: [user]
status: stable
related: [concepts/envelopes-and-budgeting, concepts/categories, concepts/the-ledger, features/categories-payees, features/commitments, features/retirement-planning]
keywords: [budget, envelope, ready to assign, rta, target, assign, zero-based, 50/30/20, overspending, credit card payment, fresh start, cash flow]
---

# Budgeting

Budgeting lives on **Plan › Budget**. You choose one of two methods:

- **Zero-Based** — every dollar of cash gets a job. You assign money to envelopes (Rent,
  Groceries, Vacation…) until nothing is left in **Ready to Assign**, then spend from them. This
  is the YNAB-style method, and the one with the most control.
- **50/30/20** — a quick health check. Your income is split into shares for each group (for
  example Needs, Wants, Savings) and the app shows how your month is tracking. Nothing to assign.

Both use the same envelopes and categories, so you can switch at any time without losing
anything.

Your budget is built from your **Cash** and **Credit** accounts only — see
[Accounts](accounts.md#which-type-to-pick). Add those accounts first.

## Choose your method (first time)

1. Open **Plan**. It opens on the **Budget** tab.
2. Under **How do you want to budget?**, click the **50/30/20** or **Zero-Based** card. Not sure?
   Use the picker beside the heading (**Just check my shape** / **Account for every dollar**),
   or click **Help me choose** to talk it through with Lucius.
3. Click **Start with 50/30/20** or **Start with Zero-Based**, then follow the matching section
   below.

To change method later, use the method button on the Budget tab row (it shows the current
method's name) → pick the other one → **Switch**. On a phone it's in the **⋯** menu at the top of
Plan. **Compare methods…** in the same menu shows them side by side.

## Set up Zero-Based

A new book starts with envelopes in three groups — **Bills**, **Needs** and **Wants** — each
linked to a matching category (Rent/Mortgage, Groceries, Dining out…). Change them as you like.

### 1. Give each envelope a monthly target

1. On the Budget tab, click **Set up Zero-Based** in the banner at the top.
2. Choose how to start:
   - **Set up with Lucius** — Lucius looks at your spending and proposes a target for each
     envelope, explaining why.
   - **Suggest from my spending** — the app proposes each target from your recent spending
     (the typical amount you spent). Envelopes with too little history are flagged
     *check amount*.
   - **Set up manually** — the same list with nothing ticked, so you choose each one.
3. Tick the envelopes you want a target for, adjust the amounts, and click **Apply**.

You can skip targets, but they're what makes **Underfunded** and **Auto-assign** useful.

### 2. Assign your money

**Ready to Assign** at the top is cash in your Cash accounts that no envelope holds yet.

1. Click an envelope's **Assigned** amount and type how much to put in it.
2. Repeat until Ready to Assign reaches zero — or use **Auto-assign** → **Underfunded** to fill
   every target in one go.
3. Every **Auto-assign** option shows a preview of each envelope's change first. Nothing
   changes until you confirm.

Each envelope shows **Assigned** (put in this month), **Activity** (spent or refunded this
month) and **Available** (what's left). Available carries into next month.

### 3. Shape your envelopes (optional)

- **Add an envelope** — the **+** on a group's header (or the group's menu → **Add envelope**).
  Tick the categories it should fund, enter an **Envelope name**, and click **Create**. Pick none to
  **Create empty envelope**.
- **Envelope menu** (the **⋯** on its row) — **Add categories…**, **Add target…** /
  **Edit target…**, **Rename envelope**, **Move to group**, **Merge into…**, **Hide**,
  **Delete envelope…**.
- **Add a group** — **+ Add group** in the table header. Deleting a group moves its envelopes to
  **Others**, so nothing is lost.
- **Targets** — **Monthly**, **Weekly**, **Yearly** or **Custom**, either "set aside" a fixed
  amount or "fill up to" / "have a balance of" an amount by a date.
- To add, rename, hide or delete the categories themselves, use **Manage categories** — see
  [Categories and payees](categories-payees.md).

## Day to day with Zero-Based

**Move money between envelopes** — click a green **Available** amount, choose where to send it
(including back to **Ready to Assign**), and click **Move**.

**Cover overspending** — an envelope that spent more than it had turns red. Click its red
**Available** amount, pick an envelope (or **Ready to Assign**) under **Cover overspending
from**, and confirm. Or use **Auto-assign** → **Underfunded**.

**Filter the table** — **All**, **Underfunded**, **Overspent**, **Overfunded**,
**Money available**.

**See where Ready to Assign comes from** — click the Ready to Assign figure. **Ready to Assign
Breakdown** lists each part: cash added, what envelopes hold, spending held outside envelopes,
and so on.

### How overspending and Ready to Assign behave

- **Overspending doesn't change Ready to Assign during the month.** A red envelope holds its
  own hole until the month ends. Cover it before then and the money comes from wherever you
  choose. If it's still red when the month rolls over, the hole is taken from **next month's**
  Ready to Assign, once, and the envelope starts the new month at zero.
- **Cash spending with no envelope is held the same way.** A transaction with no category, or
  in a category not linked to any envelope, appears under **Outside envelopes** at the bottom of
  the table. Ready to Assign doesn't move; if you categorize it (or click **Add to envelope…**)
  before the month ends, the hold simply disappears. Otherwise it's charged to next month's
  Ready to Assign.
- **Income with no category goes straight to Ready to Assign** — that's how new money arrives.
- **Ready to Assign can shift as exchange rates move** if you hold cash in more than one
  currency.

### Credit cards

Each Credit account gets its own payment envelope, named after the card, in the
**Credit Card Payments** group.

- When you buy something on the card from an envelope that has money, that money is set aside
  for the card: the payment envelope's **Available for payment** goes up.
- If the envelope didn't have enough, the card row shows **"$X more needed for this bill"**.
  Assign money to the payment envelope to cover it — **Auto-assign** → **Underfunded** includes
  card shortfalls too.
- Card spending never changes Ready to Assign by itself — card debt isn't cash you've spent yet.
- Paying the card (a transfer from a Cash account) uses the payment envelope. Paying more than
  it holds turns it red, like any overspent envelope.

## Start fresh

If your book has years of imported history you don't want to budget around, open the **⋯**
beside the method button on the Budget tab (on a phone: the **⋯** at the top of Plan) and choose
**Start fresh from ‹this month›…**.

- Envelopes stop carrying balances from earlier months, and **this month's Assigned amounts are
  cleared** so you can budget the month again from scratch.
- No transactions or envelopes change. This month's spending still shows, so some envelopes will
  look overspent until you assign to them.
- The dialog shows your new Ready to Assign with the full sum written out before you confirm.
- Budget won't page back past that month. A small **Fresh start · since ‹month›** line under
  Ready to Assign reminds you.

Click **Undo** right after confirming to put everything back, including the cleared
assignments. Later, the same menu item offers **Resume earlier history**, which goes back to
carrying everything but does **not** restore assignments the fresh start cleared.

## Set up 50/30/20

1. Pick **50/30/20** (see [Choose your method](#choose-your-method-first-time)) — or click
   **Set up 50/30/20** if you switched to it later.
2. **What's your monthly net income?** — enter your usual monthly take-home pay. It's filled in
   from your profile if you've set one. Click **Continue**.
3. **How much should each group get?** — each budget group (Bills, Needs, Wants…) starts at
   what it spent over the last three months. Use **−** / **+** to change its share of income, or
   untick it to leave it out of the split. Whatever isn't given to a group is **Savings**. A group
   left without a target reads **no target set**, and its spending reduces what you save. Spending
   in a category that is in no envelope is listed under **Not in an envelope**; it is not planned
   for, so it reduces your savings too.
4. Click **Start budgeting**.

Or click **Let Lucius set it up** in the same window.

**Using it:** the top of the page shows how your month is tracking against your usual income
(**On pace to spend** / **On pace to save** during the month, **You spent** / **You saved** for a
finished one). The line names its basis underneath: a running month is measured against your
**usual monthly income** (hover for how much of this month's has arrived), a closed month against
the income that actually arrived. The **spending vs Sep by the 14th** chip says what it compares:
spending so far against last month by the same day. A group is called "over" in the headline only
when it spent past its caps, or past its share with no caps to judge it by; one that spent
exactly its caps (S$0.00 left) is not. The headline never says **On track** while an envelope is
past its own cap: it names the envelopes that are over, with how far (for example *"Rent (S$600)
and Entertainment (S$150) are over their caps"*), even when other envelopes in the same group
have room to spare. Each envelope row can have a monthly **Cap**. Overspending is reported, not
blocked — there's nothing to cover. Use **Configure** to change your income, each group's share,
or which income categories count (deleting or merging one of them carries the choice to the
category its transactions move to). **Let Lucius suggest shares** and **Let Lucius suggest caps**
ask Lucius to propose figures from your spending.

Credit card payment envelopes don't appear in 50/30/20: the purchases already count in their
own envelopes.

## Cash Flow

The **Cash Flow** tab projects your cash month by month: actual months first, then a projection
built from your regular income and bills.

- **What-if** tries a scenario — **Pay raise**, **Lose income**, **Lean spending**,
  **High inflation**, or your own adjustments — without changing anything real.
- **Projection settings** sets which assets count and your **Safety floor**: the minimum cash
  cushion to keep. Months below it are marked tight. The same floor drives the shortfall warning
  on [Commitments](commitments.md).
- **Savings rate** here is the average over the projected months ahead (net change divided by
  projected income). It is a different period from the Dashboard's last-month rate and 3-month
  average and the Budget screen's month-in-progress pace; hover the label to see which.
- A credit card with no purchases in the period shows **Card spending not included yet** under
  the hero: the runway and the projection don't see that spending, so they read better than
  your real situation.
- **Export CSV** downloads the projection.
- **Money flow** opens a diagram of one month: income flowing to spending groups and savings,
  with a **Flow** / **Table** switch (on a phone, tap **Breakdown** under the diagram for the
  table) and a month stepper. It starts on the current month, which
  shows what has happened so far ("to date"); step back for a full month. Each actual month's
  row, and the current month's, has its own money-flow button.
  Within a group, several categories under 2% are folded into one "Other" row; a single small
  category, or a group where every category is small, is shown by name. Spending on categories
  with no envelope sits under **Not in budget**, each category named. An overspent month adds a
  "From savings" source.

A bill counts as recurring once it has arrived on a steady rhythm a few times, even if the
amount changes each month. One-off expenses you haven't entered won't be in the projection.

Lucius cannot add a recurring payment by hand, so a new salary or a raise shows up once it has
arrived on a steady rhythm a few times. If one has only arrived twice, you can ask Lucius to
confirm it so the projection counts it now, or to dismiss a payment that only looked regular. To
see a change before it happens, such as a raise, ask Lucius to simulate it.

**In** and **Out** are gross: a refund or reimbursement counts as money in, and the purchase it
undoes still counts as money out. [Reports](dashboard.md) nets refunds against their categories
instead, so the same month can look smaller there, by about the refunds on each side. A past
month before any of your cash accounts was tracked (before the first account's starting balance)
shows its flow but **Before tracking** in place of a balance, because nothing was counted yet.

The **Outlook** tab (long-term net worth and FIRE) is covered in
[Retirement and FIRE outlook](retirement-planning.md).

## What Lucius can do here

- Assign or move money between envelopes, run auto-assign and explain what it proposes.
- Create, rename, merge or delete envelopes and groups; set or change targets.
- Explain why Ready to Assign changed or an envelope is red.
- Set up 50/30/20 shares or caps from your spending.
- Talk through a what-if cash flow scenario.

## Troubleshooting

**Ready to Assign is negative** — you've assigned more than the cash you have. Lower some
**Assigned** amounts, or move money from an envelope back to Ready to Assign.

**Ready to Assign looks far too high or low** — check that every bank account is a **Cash**
account and every card a **Credit** account; other types aren't in the budget. If years of old
history are distorting it, use [Start fresh](#start-fresh).

**Total available seems bigger than what's really free** — look at **Outside envelopes**:
spending there hasn't been taken from any envelope yet. Categorize it or add its category to an
envelope.

**"No envelopes to target yet — create one in your budget first."** — every envelope was
deleted. Add one with the **+** on a group header, then try again.

**I can't page back to earlier months** — a fresh start is active. Use **Resume earlier
history** from the same menu.

## Related pages

- [Categories and payees](categories-payees.md)
- [Commitments](commitments.md)
- [Retirement and FIRE outlook](retirement-planning.md)
- [Envelopes & budgeting](../concepts/envelopes-and-budgeting.md)
- [Categories](../concepts/categories.md)
