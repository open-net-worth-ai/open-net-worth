---
title: What's new
description: Dated notes on changes that move a figure you already track — read this when a number looks different after an update.
category: operations
order: 90
audience: [user]
status: stable
related: [operations/upgrading, concepts/net-worth]
keywords: [changelog, release notes, what's new, upgrade, version, figures changed]
---

# What's new

New versions ship often — usually a small update every day or two — so this page is not a list
of every change. It covers only the changes that can make **a figure you already rely on** read
differently after you [update](upgrading.md): net worth, its history, returns, Ready to Assign
and the like. If a number moved after an update and nothing here explains it, see
[Net worth looks wrong](../troubleshooting/net-worth-looks-wrong.md).

The full list of versions and their downloads is on the
[releases page](https://github.com/open-net-worth-ai/open-net-worth-releases/releases). To see
which version you have: in the desktop app, **Open Net Worth → About Open Net Worth** on a Mac or
**Help → Check for Updates…** on Windows and Linux; on a server, run `onw version`.

## 2026-11-05 — Actual Budget can be imported and kept in step

Turn it on in **Settings → Developer → Experimental**, and **Automations → Data Sync** has an
[Actual](../features/actual-budget-import.md) card beside YNAB: connect your own Actual server, review what would come across, import it, and keep the two in step if
you want to. Nothing changes for a book that doesn't use it, and YNAB's sync reads and behaves as it
did. Once imported, Ready to Assign won't match Actual's To Budget, because the two are worked out
differently; the review says so.

## 2026-11-02 — Credit on a card now counts in Ready to Assign

If you paid a card more than it owed, or a refund landed after you'd paid the bill, the extra
used to disappear from Ready to Assign at the end of the month, even though it was still sitting
on the card. It now stays in your budget, the way YNAB keeps it:

- **Ready to Assign** goes up by the credit on your cards. The breakdown (click the figure) shows
  it as **Credit on your cards**.
- **Spending on a card in credit** comes out of the credit first: your envelope goes down, but the
  card's payment envelope doesn't, since nothing new is owed.
- Cards that owe money work exactly as before. **Cash on hand** on the Dashboard still means your
  Cash accounts only.

## 2026-10-07 — Shares waiting to be released now count in your history and returns

If you have stock that vested but is still waiting to be released to your brokerage, those
shares were already in today's net worth but missing from every past month. Now:

- **Net-worth history and the dashboard chart** include them from their vest date, so the chart
  ends at the same figure as your headline net worth.
- **Returns** treat the vest as shares you received at their vest-date value, not as a market
  gain — a month with a vest no longer shows an inflated return.

## 2026-10-01 — Removing a holding removes its purchase too

Removing a position you added with a purchase date used to leave its purchase behind, so the
holding still counted in your net-worth history and returns. Removing one now also removes the
purchase (if none of it was ever sold). A position that has been **partly** sold can no longer
be removed this way — sell or adjust the remainder instead. Your history and returns can change
if you had removed such a position before.

## 2026-09-29 — Retirement holdings count under Retirement

Holdings inside retirement accounts now count in the **Retirement** totals, and the Retirement
card on Assets splits into **Positions** and **Cash**, like Investments. Your total net worth
does not change; how it's grouped does.

## 2026-09-28 — Budget fixes that can move Ready to Assign

- **Card spending on an archived card** (or a card-funded transfer or investment buy under a
  budgeted category) no longer inflates Ready to Assign. If you had such spending, Ready to
  Assign can read lower after updating — the new figure is the correct one.
- **Auto-assign → Average Assigned / Average Spent** now average only the months since your
  budget began (up to 12), instead of always dividing by 12.
- **Auto-assign → Underfunded** now also funds each card's payment shortfall, last, matching the
  total it shows.

## 2026-09-26 — Assets: step-by-step adding, editing on the asset's page

Adding an asset is now four short steps, and each part of an asset — ownership, purchase,
details, notes — is edited on its own page. Your **net worth today does not change**. What can
look different:

- **Past net-worth history** can shift for an asset whose purchase price or date you edited after
  adding it (the purchase point now follows your edit), and for **insurance & endowment** policies
  (a policy no longer shows its purchase price as an early value).
- **Asset Composition** now counts your share of a jointly-owned asset, so its percentages add up
  to 100%.
- A sold asset no longer holds on to its loan, and a loan secures one asset at a time.

## 2026-09-07 — Seven account types, one definition of cash on hand

Cash on hand, runway and Ready to Assign no longer subtract brokerage margin; margin now reduces
your Investments value instead. Your runway reads higher by the margin amount. Property and
vehicle accounts became owned assets — link their loan on the Assets tab.

## Related pages

- [Upgrading](upgrading.md) — how to update, and what the app does to protect your data
- [Net worth](../concepts/net-worth.md) — what counts and how it's calculated

## Guides and comparisons

Longer write-ups on the Open Net Worth blog:

- [Open Net Worth Release Notes: What Changed in October 2026](https://opennetworth.ai/blog/open-net-worth-october-2026/): the October 2026 changes explained
