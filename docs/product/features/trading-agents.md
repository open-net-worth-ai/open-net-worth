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
  no known price in the price table (for an OpenRouter model, the table's own rows for that model),
  the run form names it and a run on it is refused; pick a model the table knows or a local one. A
  dated snapshot the table lacks (for example `model-1017`) is priced as the same model without the
  date, says so in the estimate, and reserves twice that price per call. The real cost OpenRouter
  reports for each call is what counts against the caps.
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

A finished run opens short: the rating, the date it covers, and the first few sentences of the
portfolio manager's decision (**Show more** reads the rest). If you have an investment thesis on the
stock, the rating is set beside it and where the price sits against it. The thesis is compared on
your computer afterwards and never sent to TradingAgents. One line of research-only disclaimer is
always in view.

**Show full analysis** opens everything else: each written report, **What was shared**,
**Earlier analyses** (past runs; you can delete one), and what the run cost. It stays open or closed
for that stock until you reload the page.

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

## Lucius's take uses it

While TradingAgents is on, **Lucius's take on your position** (the asset page's Analysis tab) also
reads the stock's latest finished analysis, so you get one combined view. Lucius uses it as the
research on the stock, adds what it knows about your position, and names it once ("TradingAgents
rated Hold on 2026-11-17"). The rating is an input: it never becomes an order. The take never starts an analysis for you, because that costs money; it offers one in a
single row under the take, and the run starts only when you click it.

- **One view, not two.** See [How the take and TradingAgents fit together](#how-the-take-and-tradingagents-fit-together).
- **Which analysis.** The most recent finished run for the stock, at most 14 days old, with a
  rating. A run that could not settle on one (Unclear) is not used. With the feature off, or the
  connector paused, there is nothing to include and the take is exactly what it was.
- **Stale runs.** Past 3 days the take says the analysis may be out of date. Past 14 it is dropped.
- **It updates itself.** When a run finishes, the take for that stock is rewritten with it in the
  background, if you already have a take for it. This is one background call to the AI provider
  that writes the take, once per run (a run the take has already been shown is not asked again,
  and a retry for the same run waits at least 12 hours). If a take was being written at that
  moment, the next time you open the page it is rewritten. Only the take's own refresh reads the
  analysis, so a price or news alert never includes it.
- **You can see it.** Under the take's verdict, one row shows the TradingAgents research as
  supporting evidence, not a second verdict: "Research view: Sell · today", then a plain-language
  reading of that rating for your position (held or not), plus "may be outdated" past 3 days. Select
  it to open that run's full analysis on the Analysis tab, where the same reading sits under the
  rating. The row is hidden while TradingAgents is off, and for crypto and holdings with no ticker.
- **Add it from the take.** With no usable analysis, the row offers **Add TradingAgents research**
  with what it would cost ("about $0.42", or "uses your Claude Code plan") and a **What's shared**
  disclosure. It uses your configured analysts and context level and starts only when you click.
  While it runs the row shows the stage, the time and **Cancel**, with **View** to open the card;
  when it finishes the take updates by itself (**Refresh this take** appears if that takes more than
  two minutes). If TradingAgents is not ready, the row says why and links **Set up TradingAgents**.
- **What is sent.** The rating, the date, the analysis's decision text (up to 600 characters),
  how much of your holdings that run could see (not the amounts) and the model names go to the AI
  provider that writes the take, the same one that already receives your position for it. The
  reports and the amounts shared with the run are not sent. The decision text may itself restate
  figures the run was shown (for example a position size or a price), and those figures go with it.

## How the take and TradingAgents fit together

They do different jobs. **TradingAgents is the research on the stock**: price action, fundamentals,
sentiment, news and a bull/bear debate. **Lucius's take is the view for your position**: how big it
is, how concentrated, your thesis, your levels and alerts, and how old the research is. The take
uses the research and adds your position, so you get one recommendation instead of two verdicts.

TradingAgents rates the stock, not what to do with it, so its rating is read differently depending
on whether you hold it. The take's call follows the research unless something about your position
says otherwise:

| TradingAgents says | If you hold it, the take calls | If you do not hold it, the take calls |
|---|---|---|
| Buy / Overweight | Add or Hold | Buy or Wait |
| Hold | Hold | Wait |
| Underweight | Trim or Hold | Wait |
| Sell | Sell or Trim | Wait ("negative outlook, not a buy now") |

A Sell on a stock you do not own is never an instruction to sell. When the take chooses something
else, for example Hold on a stock the research rates Sell because it is most of your portfolio,
it begins its reason with that: "Despite TradingAgents' Sell: …". The reasons it may use are
concentration, your thesis, your levels, your alerts, research older than 3 days, or research that
did not see your position. If it gives none, the call is adjusted to the nearest one the research
supports. The take's text stays short: no extra AI call, and a smaller prompt than before.

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
  The call limit still applies. A CLI counts as a subscription only when it has no API key saved
  in Open Net Worth and reports that you are signed in; otherwise it is metered like an API model.
- Before each call Open Net Worth reserves the most it could cost, using the model's own longest
  possible reply. If even one such call is more than the run has left, the run stops there. A
  premium model can need a higher per-run cap than the $2 default.
- A model on your own computer (localhost or a loopback address) may have no known price and runs
  anyway, but the call limit applies to it, and any cost it reports is counted. A proxy on your
  computer that forwards to a paid service and reports no cost cannot be seen by the dollar caps.
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

TradingAgents is not sandboxed. Open Net Worth gives it a separate home folder and a clean
environment, installs it from hash-locked dependencies and masks any key that appears in its error
text, but those reduce risk and do not confine it: install only if you trust the pinned release.
The install refuses to continue if its dependency lock file is missing.

On the public demo, every TradingAgents route (settings, runs and reports, spend) and Lucius's four
TradingAgents tools are turned off.
