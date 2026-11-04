---
title: Trading workspace
description: Delegate a persistent trading mandate and follow market, portfolio and agent work together.
category: features
order: 85
audience: [user]
status: beta
related: [features/investments, connectors/bank-brokerage, lucius/models-and-cost, lucius/permissions]
keywords: [trading, workspace, autonomous, mandate, backtest, paper trading, live trading, comparison, stocks, crypto]
---

# Trading workspace

**Trading** is a persistent workspace for a delegated trading mandate. Describe an objective,
funds and period to Lucius, or use **Expert setup**. Review the same workspace contract before
starting. Trading's navigation icon is a candlestick chart; Investments keeps its trend arrow.

## Delegate through Lucius or a form

**Create with Lucius** starts a conversation. Explain what you want, for example managing a
synthetic allocation of 1,000 USD for a month. Discuss markets, environment, executor, account
scope, trading limits and agent usage budget. Lucius validates an unsaved draft and opens
it for review; that preview does not create a workspace or grant trading authority.

**Expert setup** opens the same contract directly. Review original instructions, effective
limits, allocation, environment, duration and executor. Starting can fail if execution
prerequisites are missing; the saved workspace and its blocker remain inspectable.

Original instructions are preserved for new workspaces. Amendments retain before/after
values, reason, author and effective revision. Pause active work before changing the mandate.
Earlier decisions and orders retain their recorded revision. An older workspace whose original
instruction was not preserved says **Not recorded** rather than inventing an original.

## Autonomous work and executor choice

A workspace has one logical executor. It may be Lucius, a coding agent, a team, another
external tool-using agent, or a strategy adapter. Internal organization is the executor's
choice. Available control and visibility depend on the integration.

Lucius can inspect workspace data, research public current information in Paper/Live, save
versioned research/code/test files, run isolated Python strategies, trade directly, or wait.
The server scheduler continues across bounded decision cycles and resumes saved session/files.
Normal operation does not require repeatedly pressing an agent or historical-advance button.
The browser can close while server-owned work continues.

Unattended calls require explicit **Always allow** permissions for scoped tools in
**Settings → Permissions**. A workspace cannot grant itself permissions, change funding or
switch to live trading. Trading capital and model usage budget are separate. Unknown billing
or an exhausted budget blocks further paid cycles until resolved.

External agents use a revocable, expiring workspace MCP credential. Issue it from executor
controls and copy it to the selected agent. It grants workspace tools, not your personal
book or broker secrets. Gateway tool calls/results are observed by the host; other inputs,
outputs, reasoning and child activity are visible only if the executor reports them. An
external process with unrelated tools remains outside this application's containment.

Strategy source runs in a separate Docker container without host mounts, credentials or
network access. The pinned Python image and Docker must be provisioned. File versions,
source, execution output, errors and order/decision identities remain inspectable.
Saving a file does not execute it.

## Two views of the same workspace

**Market & portfolio** shows recorded market data, positions, orders/fills, cash, valuation,
returns/drawdown, fees and statements. OHLCV candles and volume are rendered only when bar
fields were supplied by the data source. A quote-only dataset stays a quote series; missing
bars are not fabricated. Source, observation time, availability and dataset version help
distinguish current data from what was available at a historical decision.

**Agent work** shows actual emitted inputs/outputs, available analysis, provider-exposed
reasoning or summaries, tool arguments/results, files and execution history. Expand records
for details. Large work payloads can be retrieved separately. Provider output, host observations
and executor-reported content carry different provenance. **Not provided**, **Not recorded**,
**Pending** and **Redacted** describe different states. Hidden reasoning and unreported child
work are not reconstructed.

Both regions are equally visible on wide layouts; compact layouts let you switch between
them while retaining mandate and current status above. Selecting a fill, order or decision
opens its linked evidence. Relationships use recorded identities, not timestamp guesses.
Missing decision/data/file links remain visible as missing.

The statement reconciles opening allocation, execution cash movements, fees, closing cash
and positions, and known realized/unrealized results. It is an application projection, not
an official broker statement. Missing marks, basis, FX, fees or reconciliation can make
figures unavailable. Agent/model costs appear separately from trading P&L.

## Environments and data boundaries

**Backtest** replays a versioned historical dataset. The scheduler moves its environment
clock; tools receive only observations available then. Current web research is refused.
Fees/slippage/liquidity assumptions belong to the reviewed scenario. Simulation cannot
establish exchange fillability or eliminate a model's learned historical facts.

**Paper** uses simulated funds and current time. Select an explicit instrument universe and
supported current data source, or supply observations. Missing/stale quotes can prevent
orders or complete valuation. Public bars/quotes may be delayed. Paper holdings never enter
personal net worth.

**Live** requires separately reviewed human authorization and a verified eligible account.
Connection or OAuth success alone is insufficient. The reviewed Alpaca mapping requires an
unleveraged USD cash account with its entire initially empty funded allocation isolated,
stable order/fill identity, lookup/cancellation capabilities and standing MCP permissions.
A small virtual slice of an unrestricted larger account is not isolated funding.

Binance and IBKR remain approved integration targets. Archived Binance code is incomplete;
the current IBKR trading adapter performs preflight/refusal. Neither is presented as an
authenticated, funded execution path. Real-order acceptance requires a specified account,
scope and authorization; no real funds are needed to inspect a synthetic workspace.

## Observe, pause and recover

Current work distinguishes working, waiting, blocked, recovery, pause and end states. Waiting
can include the next automatic wake. Unknown external status stays unknown. An interrupted
browser update does not imply the executor stopped.

**Pause** and **End workspace** prevent new decisions and request cancellation under the
execution policy. Sent orders may still execute; cancellation is pending until confirmed.
Unknown outcomes require reconciliation before resubmission. Holdings remain. **Resume**
rechecks eligibility and keeps allocation/history. Expiry also stops new work; ending does
not silently liquidate positions.

Live fills use canonical execution identity and existing financial write chokes. Undo in
this application cannot undo an external trade. Simulations remain separate from the real ledger.

## Compare workspaces

**Compare** uses recorded environments with common period, capital, data and execution
assumptions. Results show return, drawdown, trading fees and separate agent costs. Incompatible
scenarios and incomplete valuations are disclosed rather than ranked as equivalent. Historical,
paper and funded live execution remain different evidence, despite a common tool contract.
