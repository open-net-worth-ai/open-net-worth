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
Followed portfolios**. It then appears as the **Following** tab in **Intel**.

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

1. Open **Intel → Following** and click **Follow a portfolio**.
2. Pick one from the list, or search by fund or member name.
3. Click **Follow**. Fetching the filing takes a few seconds.

Each portfolio you follow gets a chip at the top of the screen. Pick one to see when it was
filed and how old that is, its top 20 holdings with their weights, and what changed since the
previous filing (new, added, trimmed, sold out). A member also shows their recent trades.

Filings are checked twice a day. A new one updates the portfolio and adds a story to **Intel**.
Use **Refresh** to check now. If a check fails, the last portfolio stays and the screen says
what went wrong. A fund whose latest filing is more than about two quarters old, or a member
with no report for a year, is marked out of date.

## Copy a portfolio

Copy trading is an automation, not a button on this screen. Open a follow and click **Copy in
Automations**, or open **Automations** and pick **Copy a followed portfolio**.

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
