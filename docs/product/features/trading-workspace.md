---
title: Trading workspace
description: Give an agent a defined trading allocation and period, follow its activity, and compare runs across backtest, paper and live environments.
category: features
order: 85
audience: [user]
status: beta
related: [features/investments, connectors/bank-brokerage, lucius/models-and-cost, lucius/permissions]
keywords: [trading, workspace, autonomous, agent, backtest, paper trading, live trading, comparison, strategy, stocks, crypto]
---

# Trading workspace

> **Development archive:** Issue #62 and draft PR #63 are being redesigned around a
> persistent delegated workspace, conversational setup, and linked market/portfolio and
> agent-work views. The interface described below is the earlier implementation, not
> acceptance of that design. Archived Binance integration code is incomplete and is not
> registered as an operational connector; IBKR currently provides preflight/refusal only.
> Neither integration has authenticated trading acceptance. See the
> [revised issue](https://github.com/open-net-worth-ai/open-net-worth/issues/62) and the
> [design RFC](../../../.Codex/rfcs/autonomous-trading-workspace-v2.md).

Open **Trading** to give an agent a trading mandate and follow its actions and results.
A workspace stores the instructions and limits. Each run fixes an environment, period,
funded account scope and starting capital. Changing environment creates a new run.

## Create a workspace and run

1. Select **New workspace**, give it a name and instructions, and choose Lucius, an external
   agent, or a strategy script.
2. Choose the permitted instruments and limits. Model usage has its own budget, separate
   from trading capital. Current public Yahoo quotes are available for USD-quoted stocks
   and crypto pairs; provider delays and quote age still apply. Alternatively supply your
   own observations.
3. Create a run with a start/end period and an allocation for each venue/currency account.
   Backtests require a versioned observation dataset. A synthetic example is available in
   the run form to explore the workflow without funding an account.
4. Review the frozen mandate and select **Start run**. The environment and account bindings
   cannot be changed while the run is operating.

## Choose an environment

**Backtest** replays supplied historical observations. The agent receives data only once
it is available at the simulated time. Advance the run to process subsequent observations.
Fees, slippage and participation limits belong to the frozen mandate. This observation-based
simulation does not establish that an order would have filled at a real exchange; include
the dataset's calendar, liquidity and corporate-action assumptions when interpreting results.
The observation simulator supports GTC orders; DAY orders require a verified session calendar
and are explicitly refused in simulated runs. Capabilities report this distinction.
Data isolation cannot remove historical facts already learned during a model's training.

**Paper** uses simulated funds with current time. Choose a current quote source and instrument
universe for unattended operation, or ingest your own observations. Missing or stale prices
are shown and can prevent orders or a complete valuation. Paper holdings never enter your
personal net worth. Public quotes may be delayed and are not a guarantee of executable prices.

**Live** sends orders to an eligible connected broker. The reviewed Alpaca MCP v2 mapping
requires an explicitly live-configured transport, standing permissions for its required
tools, and an active unleveraged USD cash account. The authorized allocation must match the
entire initially empty funded account; an unrestricted account with additional capital is
not treated as a smaller virtual allocation. Broker identity, order identity and fills must
be available for reconciliation. Unsupported or incompletely configured accounts refuse to
start. Connecting a broker does not authorize a live run: the run requires explicit human
authorization. Fees whose broker metadata is incomplete must not be interpreted as confirmed
zero fees.

The per-order notional limit checks the current quote and limit price before submission;
it is not a guarantee of the eventual execution value during a price move. The funded,
unleveraged account scope remains the capital boundary.

## Participants

**Lucius** works in bounded decision cycles and can wake again while the run remains active.
Its trading tools read the run's environment and submit intents through the same execution
boundary as other participants. Inspect activity and usage to see what actually happened.
An exhausted model budget prevents new decision cycles.

Unattended trading requires explicit **Always allow** permissions for its trading tools,
including order submission and strategy execution. Review these in **Settings → Permissions**;
the run's account and capital limits still apply to every call. A run cannot grant these
permissions to itself. Broker MCP tools also need their own standing permissions on the
connector. Refused calls remain visible in run activity.

For an **external agent**, issue access from the run. Copy the scoped endpoint and one-time
credential to that agent's MCP client. It grants access to this run's trading tools, not your
personal book or broker credentials. Revoke it from the run when finished. Its own unrelated
tools or network access are outside this application's control; a protected comparison also
needs to constrain the external agent's execution environment.

For a **strategy script**, provide Python source in the run. It receives the run snapshot
and returns typed order intents for the parent to validate and execute. It runs in a separate
container without host mounts, host credentials or network access. Docker and the supported
Python execution image must already be provisioned; the application refuses execution if
they are unavailable. The source hash, output, failures and submitted orders are recorded.
Code cannot grant itself another account or promote a simulation to live execution.

## Follow and control a run

Run details show financial summaries, accounts, holdings, orders and fills, activity and
artifacts. Equity, return, drawdown and fees come from the backend. Agent/model costs are
shown separately. Missing marks or FX produce an incomplete valuation, not a zero balance.

**Pause** and **Stop** prevent new trading decisions and apply the run's open-order
cancellation policy. Holdings remain. These actions do not promise that an order already
sent to a venue cannot execute. Cancellation remains pending until confirmed, and an unknown
submission outcome requires reconciliation before safe recovery. **Resume** rechecks the
mandate and eligibility. Expiry also stops new actions. Flattening a position is a separate
trade rather than an implicit effect of Stop.

Live fills are reconciled by broker execution identity and booked through the normal
investment/transaction paths. Importers with overlapping fills must share that identity or
resolve the overlap explicitly; similar amounts and timestamps do not prove duplication.
Application undo cannot undo an external trade or resurrect revoked trading access.

## Compare agents

Open **Compare** and select runs with common starting conditions. The comparison records
period, capital, market access and execution assumptions and shows returns, drawdown, trading
fees and agent costs. Incompatible runs are identified rather than presented as equivalent
competitors. A historical replay, a provider's paper account and an actual funded live run
are different kinds of evidence even when the trading tools have the same interface.
