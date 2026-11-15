---
title: Paper Trading
description: Try trades with pretend money at real market prices, through Lucius, without touching your real book or reaching a broker. The screens are coming.
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

> **Coming soon.** The Paper Trading screens in Intelligence are still being designed. Today
> paper trading works through Lucius, behind **Settings → Developer → Experimental → Paper
> Trading** (off by default).

## What you can do now, through Lucius

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

On the public demo, paper portfolios can be read but not changed.
