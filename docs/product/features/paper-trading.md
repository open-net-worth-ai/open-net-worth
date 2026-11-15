---
title: Paper trading
description: Try trades with pretend money at real market prices, for every kind of asset, without touching your real book or reaching a broker.
category: features
order: 96
audience: [user]
status: beta
related: [features/copy-trading, features/intel, features/investments, lucius/automations, lucius/permissions-v2]
keywords: [paper trading, paper portfolio, simulated, practice, pretend money, demo trading, backtest, strategy, test a trade, buy zone]
---

# Paper trading

A paper portfolio is pretend money at real market prices. Try a trade before you make it, let
Lucius run a strategy for a few weeks before it gets a real account, or follow a famous
portfolio forward and see how it does.

It is the **Paper trading** tab in **Intelligence**, right after **Copy Trading**. It is
experimental and off by default: turn on **Settings → Developer → Experimental → Paper
trading**.

> Paper results are not your money and are never part of your net worth, cash, budget or
> Investments figures. Nothing here ever sends an order to a broker.

## Start a paper portfolio

**New paper portfolio** asks:

- **Name.**
- **Start with**, one of:
  - **Cash only.** 10,000, 100,000, the same amount as your investments, or any amount.
  - **A copy of my holdings.** Today's holdings at today's prices: shares, funds, crypto, metals,
    and your assets at their newest valuation. Each starts with a cost of today's price, so its
    profit and loss starts at zero. Cash is not copied unless you add some. Option contracts are
    left out for now.
  - **A followed portfolio.** Its current weights, bought with the cash you choose.
- **Home currency.** Your base currency by default. It cannot change later; you can still hold
  other currencies as cash.
- **Benchmark.** Your preferred benchmark by default.
- **Fill settings** (collapsed): slippage (5 bps), commission (0), FX spread (0.1%), the metal
  dealer premium (+3% to buy, −1% to sell), fractional shares (on), pre/post-market fills (off),
  and converting currency automatically when you buy abroad (on).

You can have up to 20 active paper portfolios.

## What you can trade

| Kind | Priced from | When it trades | How an order fills |
|---|---|---|---|
| Stocks, ETFs, REITs, ADRs | The live quote | That exchange's session, in its own time zone | At the ask (buy) or bid (sell) when the quote has them, else the last price plus or minus slippage |
| Mutual funds | The daily NAV | Orders before 16:00 New York | At that day's NAV, published after the close. Later orders get the next day's |
| Crypto | The live quote | Around the clock | The last price plus or minus slippage, to 8 decimals |
| Physical metals (by the gram, ounce or tael) | Spot, in the asset's unit | Sunday 18:00 to Friday 17:00 New York | Spot plus the dealer premium to buy, less the discount to sell |
| Currencies | The live rate | Sunday 17:00 to Friday 17:00 New York | The rate plus or minus the FX spread |
| Indices (S&P 500, Nikkei 225, …) | The index level | The index's session | The level itself, no fee. Labelled **Synthetic**: no fund tracks it exactly, and a price index pays no dividends |
| Anything else (pre-IPO shares, a flat, art) | The price you enter | Any time | At your price. Labelled **Your price** |

Options, bonds and cash yield come in a later phase; margin, short selling and futures after
that.

## Place an order

**Trade**, or **Trade** on a holding, opens the order ticket: buy or sell, the asset, then what
that kind needs (shares or an amount; market, limit or stop; good for today or until
cancelled). A fund has no order type: it fills at the next NAV. Add an optional **Why** note
(280 characters); it shows on the trade in Activity.

The preview shows the price it would fill at with its source and age, any spread, premium,
slippage or commission, the total, and the cash left. One line under the button says what
happens next — for example "Market closed. Fills at the next open (Mon 09:30 New York), at the
first price we see."

A filled order shows a paper-style message: "Paper buy filled: 20 ACME at 187.51".

## How fills work

Paper orders fill at the latest price the app sees for that asset, with your slippage, spread or
premium added: a buy pays a little more, a sell gets a little less. A real order meets a live
order book, so it can fill at a different price, partly, or not at all. The difference grows
with a thin market, a large order or a fast move. Prices marked **Synthetic** or **Your price**
are estimates, not quotes. Paper results are not a track record.

- **Market order, market open:** fills at once.
- **Market order, market closed:** waits, then fills at the first regular-session price after
  the open.
- **Limit:** fills when a price the app sees reaches the limit — at the limit, or better only
  when the price gaps through it.
- **Stop:** becomes a market order when a price the app sees reaches the stop. It can fill worse
  than the stop after a gap.
- **No fresh price:** in an open market, a quote older than 20 minutes is not fresh. The order
  waits and says why.
- **Good for today:** an order not filled by its session's close expires.
- **Size:** there is no liquidity model. An order above 5% of the asset's average daily volume
  says "A real order this size would likely move the price", and still fills.
- **Cash and holdings:** a buy can spend only free cash — cash not reserved by your open orders.
  A sell is capped at what you hold. An order the portfolio can't cover is refused and says how
  much is available.
- **Currency:** buying abroad converts the shortfall from your home currency at the live rate
  plus the FX spread, and the rate shows on the fill. With automatic conversion off, a buy needs
  cash in that currency.

## Dividends, splits and freezes

- A **dividend** (or a fund's distribution) is paid in cash on its ex-date, for what you held
  then. It is paid gross: withholding tax is not modelled.
- A **split** adjusts the quantity and cost per share on its effective date; the value does not
  change.
- Anything else — a spin-off, a merger — is never guessed. The holding is **frozen** and says
  why, and so is a holding whose price moved like an unreported split, or that has had no price
  for 5 sessions (it may have been delisted). A frozen holding can't be traded until you settle
  it.

## Watch it play out

A paper portfolio's page has its value, its return since it started beside the benchmark and
your real investments over the same days, its cash in each currency, and its open orders. The
chart draws all three lines. Below it:

- **Holdings**, grouped by kind. A Synthetic or Your price row carries its label.
- **Orders**, open and recent, with Cancel and the reason each open order is waiting.
- **Activity**: every fill, conversion, dividend, split, freeze and price you recorded, each with
  its note.
- **Compare**: how much it overlaps your real book, how it is spread, and how it swings.

## Start over, archive, delete

**Start over** archives the current run and starts a new one the same way it started (the same
cash and settings, the same copy at today's prices). The old run stays readable under
**Archived** with its full history. **Archive** keeps a run without starting a new one. Only
**Delete** removes a portfolio and its history, and it asks first. Nothing expires because it
went unused.

## Lucius on paper

Lucius can create paper portfolios, place and cancel paper orders, record your prices, and read
how they did — "paper trade 20 ACME", "how is my buy-zone portfolio doing against my real
book?", "did the trades I placed in my buy zone beat the rest?". He reads the same figures as
the page, so his numbers match it. Every paper result is marked simulated, and he never counts a
paper figure as your money or in your net worth.

Paper orders are **Always allow** by default, with no approval card, because no money moves.
Deleting a paper portfolio asks first. You can change either in **Settings → Permissions**.

**Do this for real** on a paper trade, a holding or a portfolio opens Lucius with a drafted
request. He places a real order only through your broker's own connector and its own
permission; with no broker connected he offers the matching form instead. Paper trading itself
never sends an order anywhere.

## Automations on paper

Two Automation templates run on paper:

- **Paper-copy a followed portfolio** — each week, brings a paper portfolio to a followed
  portfolio's weights.
- **Run a strategy on paper** — each day after the US close, follows rules you write (for
  example "buy anything that enters its buy zone, sell below its stop").

These Automations are **Paper only**: they can read, place and manage paper orders, and keep
Lucius's own notes, and nothing else. Every other write and every connector tool — a broker's
order tool included — is refused, whatever their instructions say. The setting is fixed when the
Automation is created.

## Public demo

On the public demo you can look at paper portfolios but not change them.
