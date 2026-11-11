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
Followed portfolios**. It then appears as the **Famous Portfolios** tab in **Intel**.

> This is not investment advice. The filings behind it are weeks or months old by law.

## What you can follow

| Kind | Source | How fresh |
|---|---|---|
| A fund (for example Berkshire Hathaway) | Its quarterly 13F filing at the SEC, plus its Form 4 trades | The 13F is filed up to 45 days after each quarter ends. A Form 4 is filed within 2 business days of a trade |
| A US House member | Their trade reports at the House Clerk | Filed up to 45 days after each trade |
| An ARK ETF (for example ARK Innovation) | The fund's own daily holdings file | Daily: each trading day's full holdings |

Limits you should know:

- A 13F lists US-listed stocks the fund holds long. It leaves out short positions, most
  non-US holdings, bonds and cash.
- A fund that owns more than 10% of a company must file a Form 4 within 2 business days of each
  open-market purchase or sale, with the exact date, shares and price. These are shown as the
  fund's recent trades. Only a fund's largest stakes appear this way, so most of its book still
  moves only when the next 13F arrives.
- An ARK ETF publishes its whole portfolio every day, so its changes are day over day moves, not
  quarterly ones. Only the ARK ETFs listed in the directory can be followed.
- A House report lists trades, not holdings, and gives each amount as a range. The portfolio
  shown for a member is **estimated**: purchases add the middle of the range, sales take it
  away, over the last three years. The screen labels these weights as estimated.
- Senators are not available. Options, bonds and anything without a ticker are listed in a
  member's trades but carry no weight. A scanned paper report cannot be read and is counted on
  the screen.

## Open a portfolio

Open **Intel → Famous Portfolios**. It starts with a hand-picked list: 28 well-known funds that
still file their quarterly report, NVIDIA (which reports its own stakes in other companies), 5
House members whose reports can be read and who traded in the last six months, and ARK's 6 ETFs.
Search by the start of a name (**nv** finds NVIDIA) for any other company or fund that files a
quarterly holdings report, and any House member with a trade report this year or last.

Every portfolio in the directory is a card with its past
year against your benchmark, its two newest moves, and the date of the newest of them. Filter
the cards with **All**, **Watching**, **Funds**, **Politicians** and **ETFs** (each shows how many
there are), sort them, or search by fund or member name. On a phone, or in a narrow pane, the
filter and the sort are in the **Filter** button beside the search.

Click a card to open it. **‹ Famous Portfolios** takes you back.

Under the name are who runs it and two dates, each its own chip:

- **Quarterly report filed Aug 14, 2026** (an example): the fund's latest quarterly report, which
  lists everything it held at the quarter's end. Holdings and Overview are built on it; point at
  it to see that day (**Holdings as of Jun 30, 2026**). An ARK ETF shows **Holdings on** its
  latest day instead. A House member has no such list, so neither is shown for one.
- **Latest trade Nov 3, 2026**: their newest dated trade, when it is newer than the holdings.
- **Apr – Jun 2026 not disclosed** appears when a quarter's 45-day deadline has passed with no
  holdings report from the fund (some funds file only a notice that another manager reports for
  them).

The bell beside **Copy with Lucius** watches the portfolio: you get an Intel story each time it
files. A filled bell means you are watching; click it again to stop (it asks first). **Refresh**,
the source document (**SEC filing** or **House Clerk report**) and **Ask Lucius** are in the
**⋯** menu.

Every table on the page marks a stock you hold yourself with **You own**, so you can see at a
glance where a famous portfolio touches yours.

### Trading Activity

A fund's page opens on **Latest moves**, newest first: **Recent trades, filed within 2 days** (a
fund that owns more than 10% of a company must file each trade in it within two business days,
with the exact day, shares and price), then the three biggest purchases and the three biggest
sales in its latest quarterly report (**Bought, in quarterly report Aug 14**). A quarterly report
shows what changed since the one before, not the day or the price of each trade. Click
one to show it under **All moves**, with its chart and **Copy trade**.

Under them is everything they did, newest first, over the period you pick. The period starts on the whole range
(for example **Jul 2024 – Now**); you can also pick the days since the last quarter (**Since
Jun 30**) or any single quarter. Filter a fund's moves by **New**, **Increase**, **Decrease** and
**Sold out**, or a member's by **Bought** and **Sold**. Each row shows the date, the change in
dollars with a bar for its size, and the change in shares.

Above the table, with no row picked, a chart shows the portfolio over the same period, with **B**
and **S** markers on its moves. A quarter's change has no trade day (a fund reports its holdings
at each quarter-end, not when it traded), so its marker sits on the day it was **disclosed**,
the first day anyone could act on it. Each quarter marks its biggest purchase and its biggest
sale. Point at a marker to see what moved and why it sits on that day.

Click a row to see that stock's price with every move the portfolio made in it, and a dashed line
at its price on the day it was made public. Click the ticker above that chart to open the stock's
own page. **Trade details ›** lists every quarter and every
dated trade in that stock. **Copy trade** hands that one trade to Lucius (see below).

### Holdings

A map of what they hold: one tile per sector (or per stock, when sectors are unknown), sized by its
share of the portfolio. Its colour is what they did with it last quarter: green where they
added shares, red where they sold, grey where it did not move. Click a tile, even one too small
for its name, to see its name, share and change above the table and list only its holdings;
**Show all** (or the tile again) lists them all.

The table lists every holding in the report, each as its share of the whole portfolio, with the
shares held and its return over the past 12 months. Click a row to open the stock's own page. Search it by ticker or name. A holding the filing names without a ticker
we could match is a row of its own with **Set ticker**, which searches for the company the filing
names and lets you pick it; on any fund holding, **Wrong ticker?** is
in the row's menu. A correction is kept with a portfolio you watch, so setting one starts
watching it.

### Overview

The holdings' total value and the number of stocks, each with its change since the previous
report when there is one. Then the price return of these holdings against your benchmark, with
dates along the bottom. The periods are under the chart: **1M**, **6M**, **12M** or **Since
filed**. The dashed line is your benchmark:

- **Since filed** is what copying it on the day it was made public would have returned, before
  costs and dividends.
- Every return on these screens, the cards' **1Y** included, is the return of the stocks they
  hold today, at today's weights. It is not the fund's own track record.
- **1M**, **6M** and **12M** apply today's holdings to the past. They are not the fund's own
  return, and they flatter a portfolio that recently bought its winners.

Point at the chart to read both figures for a day.

### When it updates

Filings are checked automatically twice a day while the app is running (the first check is a few
minutes after it starts). A new one updates the portfolio and, if you watch it, adds a story to
**Intel**. Prices are separate: they are daily prices that update through the day. Use
**Refresh** in the **⋯** menu to check now. If a check fails, the last portfolio stays and the
screen says what went wrong.

## Copy a portfolio

There are two ways.

**Copy one trade now.** In **Trading Activity**, click a row, then **Copy trade**. Lucius gets that one trade, checks your broker account and what you already
hold, suggests a size, and drafts the order for you to approve.

**Copy the whole portfolio, every week.** Click **Copy with Lucius** at the top of the page
(on a phone it is in the **⋯** menu). The automation opens already naming the portfolio; you
fill in the account and the amount. It opens a ready-made automation; you can also find it in **Automations** as **Copy a
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
