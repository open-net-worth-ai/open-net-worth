---
title: What Open Net Worth is
description: A private, open-source app for your net worth, budget and investments, with an AI assistant, Lucius, who works your real ledger — on a machine you control.
category: getting-started
order: 10
audience: [user]
status: stable
related: [getting-started/installation, getting-started/first-run, lucius/permissions, concepts/how-your-data-is-stored]
keywords: [about, overview, agent harness, self-hosted, privacy, lucius, what is, requirements]
---

# What Open Net Worth is

Open Net Worth keeps your whole financial picture in one place — bank accounts, cards, loans,
investments, property — in any mix of currencies, and adds it up into one net worth number. It
includes a budget and investment tracking, and an AI assistant, **Lucius**, who does the
bookkeeping with you.

It's private by design: it runs on your own computer (or a server you control), your data stays
in one database there, and there's no account to create, no subscription and no telemetry.

## What makes it different: Lucius works your real ledger

Many finance apps with AI let it *describe* your data. Lucius can *act* on it, with the same
tools the app's own screens use:

- **Reads** your net worth, balances, spending by category, holdings and budget.
- **Writes** — records and edits transactions, imports statements and screenshots, assigns budget
  money, manages payees and categories, records vests.
- **Runs on a schedule** — Automations let him log transactions from your bank emails, send a
  daily briefing, or watch your holdings while you're away.
- **Learns new tricks** — connect banks and brokers, add your own skills, or plug in external
  tools (MCP servers).

You stay in charge. Lucius reads freely, but anything that changes your book shows you a card
with exactly what he'll do, and waits for you to approve it. Each tool can be set to **Always
allow**, **Ask** or **Block** — read [How Lucius's permissions work](../lucius/permissions.md)
before you set anything to Always allow, because that applies to scheduled runs with nobody
watching too.

## What it tracks

- **Accounts and transactions** — a real ledger, in as many currencies as you hold,
  converted into one base currency for reporting. See
  [The ledger](../concepts/the-ledger.md).
- **Budgeting** — a quick 50/30/20 check, or a zero-based envelope budget where every dollar gets
  a job (like YNAB). See [Envelopes and budgeting](../concepts/envelopes-and-budgeting.md).
- **Investments** — holdings, lots, cost basis and gains across stocks, funds, crypto and metals.
  See [Positions, lots, and cost basis](../concepts/positions-lots-and-cost-basis.md).
- **Assets** — a home, a car, anything else you own, valued as you update it.
- **Planning** — cash flow for the weeks ahead and a retirement outlook with what-ifs.

## What you need

- **A computer to run it on.** The desktop app runs on macOS (Apple Silicon), Windows 10/11 and
  Linux, with nothing else to install. You can also run it as a server — with one terminal
  command or Docker — and open it from a browser or the desktop app on other devices. See
  [Installing Open Net Worth](installation.md).
- **An AI model for Lucius.** A Claude or ChatGPT subscription you already have, an API key from
  a provider, or a free model running on your own computer. See
  [Connecting an AI provider](connecting-an-ai-provider.md).

## How finished is it, honestly

It's in beta, built by one person. The ledger and net worth are the most tested; budgeting and
investment returns are still being checked against real accounts.

So for now: compare its numbers with your bank and broker for a while before you trust them like
a statement. Treat it as a capable tool you supervise. If a number looks wrong and you can't
explain it, please report it — see [Reporting a problem](../troubleshooting/reporting-a-problem.md).

Some areas work but haven't had the same real-money checking yet: market intelligence (Intel),
equity compensation (RSUs and vests), retirement projections, and bank connectors. Pages about
them are marked **Experimental** and say what to watch for.

## Who it's for

You, if you want your real net worth in one place without handing your bank logins or spending
history to a company — and you'd like an assistant that reads your statements, sorts your
spending, and answers "what did I spend on groceries last month?" from your own data.

It's built for one person. There are no family or shared accounts and no mobile app yet (the web
interface works on a phone browser).

## Where to go next

1. [Install it](installation.md).
2. [Your first run](first-run.md) — three setup screens, then Lucius sets up your accounts with you.
3. Curious how your money is stored? [How your data is stored](../concepts/how-your-data-is-stored.md).
