---
title: Paper Trading
description: Try trades with pretend money at real market prices, in Intelligence or through Lucius, without touching your real book or reaching a broker.
category: features
order: 96
audience: [user]
status: beta
related: [features/copy-trading, features/intel, lucius/automations, lucius/permissions-v2]
keywords: [paper trading, paper portfolio, simulated, practice, pretend money, test a trade, buy zone, strategy]
---

# Paper Trading

A paper portfolio is pretend money at real market prices: try a trade before you make it, or
let Lucius run a strategy for a few weeks before it gets a real account. Paper results are never
your money and never part of your net worth, cash, budget or Investments figures, and nothing
here ever sends an order to a broker.

It is the **Paper Trading** tab in **Intelligence**, after Copy Trading. To try it, turn on
**Settings → Developer → Experimental → Paper Trading** (off by default). While it is off, Lucius
has no paper tools at all (he is not shown them and cannot find them), and neither do apps
connected over MCP.

## Start a paper portfolio

Open **Intelligence → Paper Trading** and click **Create manually**. Give it a name, then
start with:

- **Cash**: the same amount as your investments, 10,000, 100,000, or another amount.
- **A copy of my holdings**: today's holdings at today's prices.
- **A portfolio I follow**: its current weights, bought with the cash you choose. On a Copy
  Trading portfolio, **Paper-copy** does this in one click.

Or click **Create with Lucius** and describe the idea you want to test. Lucius suggests the
starting cash and the first trades, then creates the portfolio.

Each paper portfolio is a card with its return against the benchmark, its two
newest trades and a ring of what it holds.

A copy is a snapshot: it buys once, when you create it, and never follows your holdings or the
followed portfolio afterwards. The line under its name says what it copied (**Copies …** opens
that Copy Trading portfolio while you still follow it), or which Automation runs it.

## Trade on paper

Open a portfolio and click **Paper trade** (or **Trade** on a holding). Type a symbol, such as
AAPL, BTC or VWCE.DE, and leave the field: the app looks it up and names it under the field.
Choose **Market**, **Limit** or **Stop**, enter the quantity, and add **Why** if you like. The
preview shows the price it would fill at, the total and your paper cash after, and one line
says what happens next ("Fills now…", "Market closed. Fills at the next open…"). Click
**Place paper buy** or **Place paper sell**.

An order that waits shows under **Orders** with what it waits for, and you can cancel it there.
The page picks up a fill on its own, without a reload.

The page shows your **Paper value** (holdings plus cash) and **Cash left** (not yet invested),
then **Performance**: the return since you started, beside your real investments and the
benchmark. In its first days the chart follows the day as it went, from the benchmark's last
close before you started. Your investments follow the day too: what you held each day, at each
holding's price at that moment (a fund or a price you set stays at its last close). A day's
trades count from that day's start, so this figure can differ a little from Investments'. Pick **1W**, **1M**, **3M**, **1Y** or **All**
under the chart. **By sector** rings what it holds, as on Copy Trading (cash left out); tap a
sector to list only its holdings. Below are **Holdings** (click a row to open that asset's page), **Orders** and
**Activity** (every trade, with its **Why**).

- **Go live** asks Lucius to help you do it for real: he compares it with what you hold and
  drafts the trades for your broker, and asks before placing anything.
- **⋯ → Edit** renames the portfolio and sets its **mandate** (below).
- **⋯ → Start over** keeps the old run under **Archived runs** and starts again with the same
  cash; a copy copies its source again as it is today. A copy of a portfolio you no longer
  follow can't start over until you follow it again. **⋯ → Delete** removes it and its history, and asks first.

## Give it a mandate

A mandate is what a fund writes down before it trades: what the portfolio is for and the limits
every buy keeps. Set it in **⋯ → Edit** (or **Set a mandate** on the **Mandate** tab). Every
field is optional; a blank one is no limit.

- **Goal**: what it is for, in your words ("Beat the S&P 500 over a year with a few AI names").
- **Max per holding**: the most of the portfolio in one holding, in %.
- **Max per sector**: the most of the portfolio in one sector, in %. A fund, crypto and metals
  are each a slice of their own; a stock whose sector isn't known yet is never held back by it.
- **Min cash**: the least cash to keep, in %.
- **Max holdings**: how many different holdings it may have.
- **Stop loss**: a holding down this much from what it cost is flagged.
- **May buy only**: the symbols it may buy; blank buys anything.

Before a buy is placed it is checked against the mandate. If it breaks a limit, the ticket says
which one ("ACME would be 22% of the portfolio; the mandate allows 15%") and the button becomes
**Place anyway**. A sell is never checked: it only moves a portfolio back towards its limits.

The **Mandate** tab shows the goal and each limit against the portfolio as it is now: **Kept**,
or what breaks it. Prices move, so a limit can break without a trade; the tab's badge counts the
broken ones. **Start over** keeps the mandate.

Lucius reads the mandate whenever he works on the portfolio and trades within it. He breaks a
limit only after he tells you which one and you say yes; an Automation never can.

## Autopilot

The **Autopilot** tab's switch sets who trades a paper portfolio:

- **Off** (the default): only you, and Lucius when you ask him in chat.
- **On**: Lucius reviews the portfolio on a schedule (every weekday or every Monday, at 10:00
  your time), places the trades he decides on, and writes a short memo of what he checked, what
  he did and what he is watching next. The portfolio and its card show an **Autopilot** tag with Lucius's star (next to the name, or under it when the name is long), and each order
  he places says **Autopilot**.

Autopilot needs a mandate: it is what you approve in advance. Every review:

- reads the portfolio, then checks the last two weeks' headlines for each holding (the same
  stories its asset page shows), your investment thesis for each one that has it, and the chart
  and company figures of anything that moved a lot or is near a limit, before it decides
  anything;
- ends with a memo Lucius writes for you, plus what he is watching next. A review that ends
  without one counts as failed;
- can also search the web and X when the stored news doesn't explain a move, read a longer
  price history, check the dividend calendar before buying near an ex-date, and look at your
  watchlist and Intel's latest analysis for ideas. It never opens a link it picks itself, so a
  link planted in a result can't send it anywhere (with **Search reads results** on, a search
  reads the top results' pages, chosen by the search engine). It makes at most 10 searches and
  price-history reads a review, and **History** lists each one;
- trades only this paper portfolio. Your watchlist, Intel's analysis and the dividend calendar
  name stocks you really hold, so it sees those, but it never reads your accounts, never uses a
  connector, and never writes anything outside the paper portfolio;
- can never break the mandate (no **Place anyway** for it), and fixes a broken limit first,
  such as a holding past its stop loss or over its cap.

A review also starts early, between scheduled ones, when a holding moves 5% or more in its last session,
falls past the stop loss, or breaks a mandate limit (checked every few minutes, each reason once a
day). These alerts go to Lucius, not to you; the review says what started it.

A stock you hold on paper is followed like one you watch: Intel collects its news on the same
schedule, and it shows on the stock's page. Only its name is shared; your paper positions and
results never are, and Intel never alerts you about a stock you hold only on paper.

Set **Schedule**, **Model** and **Instructions** (anything Lucius should keep in mind) on the
left. **Model** picks what each review runs on, and its effort: **Default** follows Settings →
AI, so one portfolio can run on a cheaper model and another on a stronger one. **Review now** runs
a review within a minute. **History** lists every review, including one that made no trades;
open one to read its memo, its trades, what it looked up and the model it ran on. Two failed reviews in a row pause
the autopilot; switch it on again to resume. **Start over** keeps the autopilot's settings, not
its history. Each review is a Lucius turn, so it uses that model like a chat does.

On an asset's **Position** tab, **On paper** lists your paper positions in it, apart from your
real ones and never added to them, then every paper trade in it, newest first: the date, the paper
portfolio, the trade and its **Why**. A position you sold out of keeps its trades there.

## Through Lucius

Ask Lucius to:

- **Start a paper portfolio** with cash, as a copy of your holdings at today's prices, or as a
  followed portfolio bought with cash you choose.
- **Place, preview and cancel paper orders** — market, limit and stop, long only — in shares,
  ETFs, mutual funds, crypto, physical metals, currencies, indices (labelled **Synthetic**) and
  assets you price yourself (labelled **Your price**).
- **Read how it did**: return against the benchmark and your real investments, profit and
  loss, worst fall, and how the trades you placed in your buy zone did against the rest.
- **Start over** (the old run stays readable, archived), archive, or delete a paper portfolio.

Every paper result Lucius reads is marked simulated, and he never counts it as your money.

## How fills work

Paper orders fill at the latest price the app sees, with your slippage, spread or premium added:
a buy pays a little more, a sell gets a little less. A real order meets a live order book, so it
can fill differently, partly, or not at all. Prices marked **Synthetic** or **Your price** are
estimates. Paper results are not a track record.

- A **market** order fills at once while its market is open, else at the first price after the
  next open. A **mutual fund** fills at the NAV of the cut-off it met (16:00 New York). Crypto
  fills at any hour.
- A stock or ETF trades in the regular session only, unless you tick **Pre- and after-market
  too** on the ticket. Then it can fill pre- and after-market at that session's latest price.
  Ask Lucius to do the same, for one order or for every order in a portfolio.
- A **limit** fills when the price reaches it; a **stop** becomes a market order when the price
  reaches it. An order good for today expires at its session's close.
- A quote older than 20 minutes in an open market is not used: the order waits and says why.
- A buy can spend only cash not reserved by open orders; a sell is capped at what you hold.
  Buying abroad converts at the live rate plus the FX spread (0.1% by default).
- Dividends are paid gross on their ex-date, and splits adjust quantity and cost on their date.
  Anything else — a spin-off, a merger, a move that looks like an unreported split, or no price
  for 5 sessions — **freezes** the holding instead of guessing.

## Permissions and Automations

Paper orders are **Always allow** by default — no money moves. Deleting a paper portfolio and
its history asks first. Change either in **Settings → Permissions**.

An Automation created as **Paper only** — ask Lucius for one, or tie an Automation to a paper
portfolio — can read your book, trade paper, and save artifacts, and nothing else. It cannot
write to your real book, use any connector (a bank, a broker or a REST API — a broker's order tool
included), search the web or fetch a page, or change Lucius's memory or skills, whatever its
instructions say. One tied to a paper portfolio trades only that portfolio (and its later runs
after **Start over**). This is fixed when the Automation is created.

On the public demo, paper portfolios can be read but not changed, and autopilot is off.
