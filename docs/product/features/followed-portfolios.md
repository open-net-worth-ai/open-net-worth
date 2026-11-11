---
title: Followed portfolios
description: Follow a famous fund or a US House member and see what they hold, what changed in their latest filing, and their recent trades.
category: features
order: 95
audience: [user]
status: beta
related: [features/intel, features/investments, lucius/overview, connectors/external-mcp]
keywords: [copy trading, follow, 13F, congress, house, politician, fund, autopilot, filings]
---

# Followed portfolios

Follow a public portfolio and see what is in it: its top holdings, what changed since the
previous filing, and when it was filed. This screen is information. It does not read your
accounts and it does not trade.

This feature is off by default. Turn it on in **Settings → Developer → Experimental →
Followed portfolios**. It then appears as the **Famous investors** tab in **Intel**.

> This is not investment advice. The filings behind it are weeks or months old by law.

## What you can follow

| Kind | Source | How fresh |
|---|---|---|
| A fund (for example Berkshire Hathaway) | Its quarterly 13F filing at the SEC | Filed up to 45 days after each quarter ends |
| A US House member | Their trade reports at the House Clerk | Filed up to 45 days after each trade |

Limits you should know:

- A 13F lists US-listed stocks the fund holds long. It leaves out short positions, most
  non-US holdings, bonds and cash.
- A House report lists trades, not holdings, and gives each amount as a range. The portfolio
  shown for a member is **estimated**: purchases add the middle of the range, sales take it
  away, over the last three years. The screen labels these weights as estimated.
- Senators are not available. Options, bonds and anything without a ticker are listed in a
  member's trades but carry no weight. A scanned paper report cannot be read and is counted on
  the screen.

## Follow a portfolio

1. Open **Intel → Famous investors**. The first time, it shows well-known portfolios to pick from.
2. Click **Follow** on one, or search by fund or member name.
3. Fetching the filing takes a few seconds. Later, add more with **Follow a portfolio**.

The portfolios you follow sit in a row at the top, each with its past-year figure. Opening one
shows:

- **How its holdings have done.** A large figure and a chart compare the price return of the
  portfolio's current top holdings, at their filed weights, with your benchmark. Switch between
  **1M**, **6M**, **12M** and **Since filing**.
  - **Since filing** is what copying it on the day it was filed would have returned, before
    costs and dividends.
  - **1M**, **6M** and **12M** apply today's holdings to the past. They are not the fund's own return,
    and they flatter a portfolio that recently bought its winners.
- **Latest moves** (a fund). One bar per stock it bought or sold between its last two filings,
  with the approximate dollar value and the share change. A 13F gives share counts at two
  quarter-ends and nothing else: no trade dates and no trade prices, so the dollar figures use
  the quarter-end price.
- **Recent trades** (a House member). Each disclosed trade with its date, the dollar range
  reported, and how the stock has moved since. Members do not report share counts or prices.
- **Holdings.** The top 20 by weight, each with its own return over the window you chose.

The line under the name says how old the data is: for a fund, when it was filed and how many
days old the positions themselves are (they date from the quarter-end, up to 45 days before
the filing). **View filing at the SEC** (or **View report at the House Clerk**) opens the
source document, and **Checked …** says when the app last looked for a newer one. Each of a
member's trades links to the report it came from.

Filings are checked automatically twice a day while the app is running (the first check is a
few minutes after it starts). A new one updates the portfolio and adds a story to **Intel**.
The chart's prices are separate: they are daily prices that update through the day, whether or
not there is a new filing. The **ⓘ** beside **Checked** says the same on the screen. Use **Refresh** in the **⋯** menu on the card to check now; **Unfollow** is in the same menu. If
a check fails, the last portfolio stays and the screen says what went wrong. A fund whose latest
filing is more than about two quarters old, or a member with no report for a year, is marked
out of date.

## Copy a portfolio

There are two ways.

**Copy one trade now.** Point at a row under **Latest moves** or **Recent trades** and click
**Copy trade**. Lucius gets that one trade, checks your broker account and what you already
hold, suggests a size, and drafts the order for you to approve.

**Copy the whole portfolio, every week.** Click **Start copy trading**, the main button at the
top of the card. It opens a ready-made automation; you can also find it in **Automations** as **Copy a
followed portfolio**.

The automation is a set of instructions for Lucius that you edit before saving:

- **which portfolio**, **which broker account** and **the most to spend**;
- **your own rules**, for example "top 10 holdings only", "no single stock above 20%" or "skip
  trades under 100".

Each week Lucius reads the portfolio, reads your positions and cash from your broker, works out
the trades, judges each one against your other holdings and your investment theses, and places
them with your broker's own order tools. It then tells you what it placed and what it skipped.

Things to know before you turn it on:

- Open Net Worth sends no orders itself. You need a brokerage connected under **Connectors**
  (see [External MCP servers](../connectors/external-mcp.md)).
- Whether Lucius asks before each order is set by your broker tool's permission in
  [Permissions](../lucius/overview.md). **Ask** shows a card per order. **Always allow** places
  orders without asking, including when the automation runs on its own.
- The trades are worked out by Lucius from your instructions, so read the order cards. Use an
  account, or a clearly stated amount, that you are happy to hand over.
- Your holdings update when your broker syncs, the same as any other trade.

## Ask Lucius

- *"What does Berkshire hold now, and what changed last quarter?"*
- *"Follow Pershing Square."*
- *"How do the portfolios I follow overlap with what I own?"*
