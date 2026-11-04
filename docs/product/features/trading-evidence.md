---
title: Trading evidence and statements
description: Inspect the recorded market data, account activity and decision links in a trading workspace.
category: features
order: 87
audience: [user]
status: beta
related: [features/trading-workspace, features/investments]
keywords: [trading, candles, OHLCV, statement, evidence, fees, performance]
---

# Trading evidence and statements

Market and account views read the workspace's execution records. They do not substitute
today's public chart for the information available in a historical environment.

## Market records

An observation can contain a quote, or an actual bar with open, high, low, close and volume.
A bar must provide all four prices together, its close must equal its quoted price, and its
open and close must lie between its low and high. A quote is never expanded into an invented
candle. Source, source version, interval, receipt time and adjustment information are shown
when provided; unavailable metadata remains unavailable.

Both the observation time and its availability time must have reached the environment clock
before a record appears. Backtests cannot fetch current data. Current Yahoo feeds also
capture bars returned by the app's existing history provider. Only timezone-aware intraday
records at least five minutes old are admitted, avoiding a still-forming one/five-minute
bucket. Their exact provider interval and adjustment basis are not guaranteed, so the records
explicitly say `provider_intraday` and `provider_default_unspecified`. The original provider
timestamp is preserved; the workspace receipt time is when the data became available here.

The first recorded bar is immutable. A later provider correction is counted in the feed
activity as a retained-original revision, rather than silently rewriting the evidence under
an earlier decision. Missing or stale data can prevent an order or a complete valuation.

## Statements and results

A workspace statement covers its opening allocation through the displayed valuation time.
It includes account opening/closing cash, execution cash movements, positions, fills and fees.
It is a projection of workspace records, not a broker-issued statement.

Realized results use the same average-cost execution accounting as the workspace: cash fees
on purchases enter cost basis, while sale cash fees reduce proceeds. Base-asset commissions
reduce acquired units or increase disposed units. Displayed realized plus unrealized results
therefore already include these fees; do not subtract the fees a second time. Model usage is
reported separately from trading P&L. Results in the base currency use the workspace's explicit
fixed FX rates, not a claim of historical currency conversion.

If execution records do not explain the stored cash or positions, cost-basis results are
unavailable and the statement identifies the discrepancy. Missing price or fee evidence also
prevents a confirmed complete valuation. An unavailable value is not zero.

## Linked evidence

Selecting an order, fill or decision uses its recorded IDs to find related records. A decision
can record a bounded availability context, including the data version and observation IDs.
The inspector restricts those observations to the decision's clock boundary. This proves
availability, not that the agent consumed every item; actual tool inputs/results establish
what it read. Older records without those links explicitly say that context was not recorded.
Nearby timestamps never manufacture a decision link or a rationale.

The market API pages recorded observations, and the evidence inspector reports the total for
its bounded 500-record data view. Files and execution records shown in evidence come only
from explicit artifact and turn/order links. Exposed analysis and provider summaries remain
distinct from information an executor did not provide.
