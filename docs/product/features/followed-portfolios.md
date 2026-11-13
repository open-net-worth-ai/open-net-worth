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
- The first time you open a fund, its filings are downloaded from the SEC, which takes about 10
  seconds; the card and the page say so while you wait. A new search asks the SEC and the House
  directly, so it takes a couple of seconds, and the same search again is instant. Each filing is then kept on your computer, in the app's cache folder (roughly 0.1–0.6 MB
  per fund, up to about 10 MB for the very largest), because a filed report never changes. Opening
  the fund again only checks the SEC for a newer filing. Deleting the cache folder is safe: the
  filings are downloaded again when needed.
- A portfolio's page opens with its return and its largest holdings first. Each holding's own
  return fills in a moment later (for a fund of thousands of stocks, up to a few minutes the
  first time), and the tickers of its smallest holdings are looked up in the background.
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
year against your benchmark, its two newest moves, and the date of the newest of them. Its ring shows the
portfolio's eight largest holdings sized against each other (the largest the brightest), with
how many stocks it holds in all in the middle; point at a slice for its ticker and its real share
of the whole portfolio. Where there is a portrait of the person behind it (an
illustration), it sits in the ring's middle instead of the count. A firm with no portrait shows its logo there instead, and the card's last line says when it was last updated ("Updated Aug
14"), the year only when it is not this one. Filter
the cards with **All**, **Watching**, **Funds**, **Politicians** and **ETFs** (each shows how many
there are), sort them, or search by fund or member name. On a phone, or in a narrow pane, the
filter and the sort are in the **Filter** button beside the search.

Click a card to open it. The page's header is one line: **‹** (back to Famous Portfolios), the
portfolio's name and who runs it (dropped on a phone). Under it are three tabs: **Trading
Activity**, **Overview** and **You vs. …** (named for the portfolio, e.g. **You vs. Berkshire
Hathaway**). The range under the charts (1W … All, 1Y at first, the window the gallery card
shows) is one setting for the whole page: change it on one tab and the other follows.

**Overview** starts with the person behind it (or the firm's logo) beside a few sentences about
the portfolio, written from its own data: where its
numbers come from, its largest holdings and how much of it they are, and what changed in its last
report. **More** shows the rest. Then its key figures:

- **Value** and **Stocks**, each with its change since the previous report.
- **Report filed** (a fund): the day its latest quarterly report was filed; point at it to see the
  day it describes (**Holdings as of Jun 30, 2026**). An ARK ETF shows **Holdings on** its latest
  day; a House member shows **Latest trade**, their newest dated trade.
- **Estimated** (a House member) and **Apr – Jun 2026 not disclosed** (a fund whose quarterly
  report is past its 45-day deadline; some funds file only a notice that another manager reports
  for them) appear under them as cautions.

The bell beside **Auto-copy** watches the portfolio: you get an Intel story each time it
files. While you watch, the bell is filled and says **Watching**; a short note under the header
confirms each change. Click it again to stop (it asks first). **Refresh**,
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
one to show it under **All Moves**, with its chart and **Copy trade**.

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

### Overview: what they hold

Under the key figures, a ring of what they hold: one slice per sector (or per stock, when sectors are unknown), sized by
its share of the portfolio. Beside it (under it on a phone) a legend names every slice with its
share of the portfolio and how many more (+) or fewer (−) shares they held there than at the
previous report, blank where it did not move. That change is in plain ink, never green or red:
it is what they bought or sold, not a gain or a loss. Click a slice or a legend row to name it in the
ring's middle and above the table and list only its holdings; **Show all** (or the same slice
again) lists them all.

The table lists every holding in the report, each as its share of the whole portfolio, with the
shares held and its return over the past 12 months. Two share classes of one company (Alphabet's
class A and class C, say) are one row, under the class the fund holds more of. Each row names the company
in full, and under it the ticker and the exchange it trades on (read from that ticker's own
quote; when it can't be confirmed, only the ticker is shown), then **You own** when it is in
your book. Click a row to open the stock's own page. Search it by ticker or name; **Owned by you** (beside
**All**, when you hold any of them) lists only the stocks you hold too. A holding the filing names without a ticker
we could match is a row of its own with **Set ticker**, which searches for the company the filing
names and lets you pick it; on any fund holding, **Wrong ticker?** is
in the row's **⋯** menu (on a touch screen, swipe the holding left and tap the pencil). A correction is kept with a portfolio you watch, so setting one starts
watching it.

### You vs. …

**You vs. …** is the portfolio beside your own. At the top, what copying this portfolio would have
returned, against your benchmark and your own portfolio, with dates along the bottom. Pick the
range under the chart; **All** starts at the oldest report on record (about four years of a fund's
quarterly reports).

- The **Copied** line buys each report on the day it was made public and holds it until the next
  one is filed, before costs and dividends. It never uses a holding before it was public: a stock
  bought last quarter earns nothing for the years before, and one sold since still earns what it
  did while it was held. Point at **Copied** for the rule.
- Each report is copied by its 25 largest holdings. A holding with no price on the day it was filed
  is left out of that stretch and the rest are scaled up.
- It is not the fund's own track record: nobody outside knows its trading between reports.

Point at the chart to read both figures for a day.

The menu beside the range picks **your** side: **All your investments**, or one type you hold
(**Your stocks & ETFs**, **Your crypto**, **Your commodities**). Theirs is always their whole report. A
type counts only its own buys and sells, so money moved from crypto into stocks is neither a gain
nor a loss of either. Bonds are not offered: they are not part of your investment return. If you
own at least one valued asset (a home, a car), **All your assets** adds them to your investments,
cash left out: buying or selling an asset is neither a gain nor a loss, only its change in value
counts. An asset is revalued only when you record a value, so its swings look calmer than they
are; a note under the risk table says so. Under
the chart, for the same range and the same choice:

- **Risk over this range**: first, one sentence on how closely their line and yours move together
  (their correlation) and so whether adding their stocks would spread your risk. Then, for them,
  you and the benchmark, **Swings** (how much the line moves up and down in a typical year),
  **Worst fall** (the deepest drop from a high), **Moves with market** (1.00 moves in step with the
  benchmark) and **Return per risk** (the yearly return for each unit of swing, the Sharpe ratio;
  higher is better). All three lines are read week by week, so a fund's weekly history and your
  daily one compare fairly. Your figures start where your history in the app does; under two
  months is too short to judge.
- **What you share**: **Different from yours** (their active share: how much of one you would
  trade to turn it into the other, 0% the same, 100% nothing in common), **Their stocks you
  own**, then every stock you both own with its share of their portfolio and of yours, and a
  **Total** row for all of them. **N of their stocks you don't own** opens the full list of the
  rest, largest first. Nothing is cut from either list.
- **How each is spread**: first their five largest holdings next to your five largest, rank by
  rank, each with its share (a stock you both hold is in blue). Then, for each side, how many holdings, the top five's share and the largest,
  then its sectors. A sector has the same colour in both columns. Crypto, commodities and
  diversified funds are slices of their own; a stock whose sector isn't known yet is **Other**
  until it is looked up.

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

**Copy the whole portfolio, every week.** Click **Auto-copy** at the top of the page
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
