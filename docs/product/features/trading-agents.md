---
title: TradingAgents
description: Multi-agent research on a stock, from its page or through Lucius, ending in a five-step rating. Research only, with spend caps and a list of what is shared.
category: features
order: 97
audience: [user]
status: beta
related: [features/intel, features/investments, features/paper-trading, lucius/permissions-v2, features/settings]
keywords: [tradingagents, multi-agent, research, rating, analysts, debate, portfolio context, spend cap, third party]
---

# TradingAgents

TradingAgents is an open-source project (TauricResearch, Apache-2.0) where AI analysts study one
stock, a bull and a bear argue it, a trader proposes a plan, a risk panel challenges it and a
portfolio manager gives a five-step rating: **Buy, Overweight, Hold, Underweight, Sell**, with the
written reports behind it. Open Net Worth runs it for you from a stock's page or through Lucius.

**It is research only.** Ratings come from AI models reading public data and can be wrong. A
rating is not financial advice and never an order: nothing here places a trade, and a run never
changes your net worth, cash, budget or Investments figures.

It is off by default. Turn it on in **Settings → Developer → Experimental → TradingAgents**. While
it is off, the connector, the card and Lucius's tools are hidden, and runs you already made are
kept.

## Set it up

Open **Connectors → TradingAgents**. The sections, in order:

- **Research-only notice**: tick the box and accept once. Nothing runs before you do.
- **Install**: one click builds TradingAgents in its own environment inside your data folder, from
  a fixed, reviewed version. It is a large download and can take several minutes. It never installs
  by itself. Removing the TradingAgents connector deletes the environment and everything it wrote (your runs stay).
  After an Open Net Worth update that changes the pinned version, the section says it needs
  reinstalling and the card on Intel points you here; click **Reinstall**. When a chosen model has
  no known price, the run form names it and a run on it is refused.
- **Models**: a **Deep-thinking model** (the research manager and the portfolio manager, the two
  decisions that matter most; default your Lucius model) and a **Quick model** (everything else,
  which is most of the calls; default your Intel model, else the deep one). Both are models from
  your own AI providers in Open Net Worth. A provider you have turned off is refused, never
  swapped for another.
- **Analysts and depth**: which analysts run (market, social, news, fundamentals), debate rounds
  (1–3), risk rounds (1–3), tool rounds per analyst (2–20), the most model calls per run (20–600),
  parallel model calls (1–8) and the report language. More depth costs more.
- **Budget**: a per-run cap and a monthly cap in US dollars (see [Caps](#caps)).
- **Data sources**: optional Alpha Vantage and FRED keys, and a contact line SEC EDGAR asks for.
  These are the only keys here. There is no AI key: see [Where the models run](#where-the-models-run).
- **Portfolio context**: the default level for each run (see [What is shared](#what-is-shared-and-with-whom)).

## Run an analysis

Open a stock in **Intelligence** and go to its **Analysis** tab: the **TradingAgents · research
only** card.

1. Pick the **Analysts** and **What it may know about your holdings**.
2. Open **What will be shared** to see the exact cash and positions, the currency, and every
   conversion.
3. The card shows the **estimate** (a range once there are earlier runs), the cap it stops at and
   the month's spend against your monthly cap.
4. Start it. A run takes minutes. The card shows it **Queued** or **Running**, which analysts are
   in progress or waiting, and a **Cancel** button. One run goes at a time and up to three can
   wait.

A finished run shows the rating, the portfolio manager's decision, each written report, what it
cost and **What was shared**. If you have an investment thesis on the stock, the rating is set
beside it and where the price sits against it. The thesis is compared on your computer afterwards
and never sent to TradingAgents. **Earlier analyses** lists past runs; you can delete one.

A run that stops early says why and keeps what it spent:

| Shown | Meaning |
|---|---|
| Stopped: budget | It reached its per-run spend cap |
| Stopped: call limit | It reached its model-call limit |
| Timed out | It ran past 30 minutes |
| Interrupted | The app stopped while it ran |
| Cancelled | You stopped it |
| Failed | It ended with an error, shown on the card |

A rating the models could not settle on shows as **Unclear**.

## Ask Lucius

Lucius has four tools for it, available only while it is on and set up:

- **`start_trading_agents_analysis`**: queues a run and returns at once, so a chat turn never waits
  for it. It asks you first with an approval card that names the models, the estimate, the caps
  and the exact portfolio context that would be shared. He starts one only when you ask, or when an
  Automation you set up does.
- **`get_trading_agents_run`**: status, rating, decision, reports (each capped at 6,000
  characters, with what was left out), cost and what was shared. With a thesis on the stock he
  judges the rating against it and cites it.
- **`list_trading_agents_runs`**: your runs, newest first, filterable by ticker or status.
- **`cancel_trading_agents_run`**: stops a queued or running run.

Reading, listing and cancelling still work after you pause the connector.

## What is shared, and with whom

You choose how much of your holdings a run may know. The default is **Investments**.

| Level | The models see |
|---|---|
| **Investments** | Your investable cash and every investment position: ticker, quantity, average price |
| **Position** | Your investable cash and your position in this stock only |
| **Ticker** | Nothing about your holdings, only the stock and the date |

Amounts are in the stock's own currency, converted at today's rates (each conversion is listed
under **What will be shared**). Cash is the cash in your cash accounts plus the cash inside your
investment accounts, before money set aside in budget envelopes. A figure with no known rate is
left out and named, never converted one-for-one. Retirement accounts, unvested equity, owned
assets, untracked brokerage totals and option contracts are not included.

| Who | Receives |
|---|---|
| **Your AI provider** (the one behind each model you chose) | The stock and the date, the amounts of the chosen level, and everything the agents write during the run. A subscription CLI runs on your computer and sends it under your plan |
| **Data vendors** (Yahoo Finance, SEC EDGAR, StockTwits, Reddit, Polymarket, and Alpha Vantage or FRED if you added a key) | The stock and the date only. Never your amounts |
| **This computer** | A record of what was shared on every run, in your local data and backups |
| **Nobody** | Account names, institutions and account numbers, transactions and payees, income and budgets, net worth and liabilities, retirement balances, owned assets, and your theses, memory and profile |

TradingAgents keeps one decision log in its own folder inside your data folder and may read what
earlier runs wrote back into a later one, so text from an earlier run's context can resurface even
at a lower level. It never leaves your computer except in the model calls above, and removing the
connector deletes it.

## Where the models run

TradingAgents never holds an AI key and never calls an AI vendor itself. Every model call goes
through Open Net Worth to the provider you chose, so the call shows in your usage totals, obeys
your provider switches and counts against your caps. On a subscription CLI (Claude Code, Codex,
Antigravity) each call is a separate short CLI turn on your computer, counted as a call and never
as dollars.

Claude Code has been run end to end. Codex and Antigravity are tested against recorded fixtures
only, so expect rough edges and report them.

## Caps

- **Per-run cap** (default $2, $0.10–$50) and **maximum model calls** (default 150): a run is
  stopped as soon as it reaches either, and it can overshoot only by the calls already in flight.
- **Monthly cap** (default $20, $0–$1,000): a run is refused up front if its estimate would take
  the month past it. It is checked when you start, not during the run. **0** means no metered
  runs.
- **Estimate**: the median and range of your last ten finished runs with the same models,
  analysts and rounds, once you have three; before that a rough guess from the settings.
- Runs on a subscription CLI are not counted in dollars and are not blocked by the dollar caps.
  The call limit still applies.
- All TradingAgents spend is in US dollars, whatever your base currency.

## See what it cost

**Settings → Cost & Performance** has a **TradingAgents** panel: metered spend for the window, runs, average
and median per run, model calls, tokens in and out, plan-included calls, spend by day, by role
(which analyst or manager) and by model, and a run-by-run list. A call with no known price is
marked, and the figure then understates.

## Third-party software

TradingAgents is © its authors, licensed under Apache-2.0
(github.com/TauricResearch/TradingAgents). It is downloaded when you click Install and is not
part of Open Net Worth. See `NOTICE`.

The installed TradingAgents code and every package it depends on run with your computer user's
permissions. They can read everything Open Net Worth stores on this machine, including the
database and your saved keys, and can use your network. TradingAgents also keeps local analysis
logs inside your data folder; they are deleted when you remove the connector or the run. This is
shown on the connector's setup page, under Install.

On the public demo, TradingAgents' spend panel and Lucius's four TradingAgents tools are turned off.
