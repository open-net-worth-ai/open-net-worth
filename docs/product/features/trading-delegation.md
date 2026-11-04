---
title: Delegating a trading workspace
description: Create a persistent trading mandate and inspect the executor's recorded work.
category: features
order: 86
audience: [user]
status: beta
related: [features/trading-workspace, lucius/permissions, lucius/models-and-cost]
keywords: [trading, delegation, mandate, agent, workspace]
---

# Delegating a trading workspace

A workspace combines a mandate, executor, environment and account allocation. **Create with
Lucius** prepares an unsaved draft for review; **Expert setup** enters the same fields directly.
Review the exact original request, effective limits, period, account scope and model budget
before starting. A failed activation keeps the reviewed workspace and explains the blocker;
it does not silently authorize a different account or environment.

Paper and Live can continue until stopped, or have an end date. Backtests require an end date
and versioned observations. Starting delegates ongoing work: managed Lucius cycles can research,
write files, execute isolated Python analyses or strategies, trade, hold and request a later
wake. The server retains the session and automatically advances the authorized environment.
Current web research is refused in backtests. Model training knowledge can still contaminate
historical evaluations.

You can leave the instrument universe open and let the executor choose within the reviewed
asset classes and allocated account scope. With the Yahoo current-data source, the executor's
**request_trading_market_data** tool subscribes to eligible USD instruments and obtains quotes
and available bars; later scheduler ticks refresh those subscriptions. It cannot widen an
explicit instrument allowlist, change currency/venue/funding, switch a supplied-data mandate
to a current feed, or fetch current data in a backtest. An empty universe gives the executor
a planning turn. A source failure appears as a blocker; genuine historical bars remain visible
even when no usable current quote is available. Recovery clears that source blocker only after
an actual successful environment update.

The Yahoo adapter accepts a quote only when the provider reports its currency as exactly
USD and verifies the selected price's observation time. A retrieval timestamp does not make
an unstamped fallback price fresh. Foreign, minor-unit or unknown currencies are unavailable
on this USD feed. New historical bars also require provider currency evidence and their own
closed, timezone-aware timestamps; the workspace's requested currency is not source proof.
An unresolved environment failure refuses new orders even if an earlier quote remains in
history. Existing order lookups, cancellation and reconciliation remain available.
Source authority is tracked separately for each instrument and must match its exact quote's
currency, observation time and price. A valid quote for one symbol cannot authorize a rejected
symbol. Rejected or unverified marks remain available as raw history for inspection, but do
not value holdings, supply complete equity/P&L, or fill resting Paper orders. Confirming a
repeated legacy quote records new source evidence without rewriting the earlier observation.

The original request is immutable. Pause the workspace before amending instructions or limits;
review the change and its reason. Amendments retain the previous interpretation, author, time
and effective revision. Already recorded orders and work retain the revision under which they
were produced. Earlier workspaces without a recorded original explicitly show that absence.

**Agent work** presents actual records: supplied input, emitted output, provider-exposed reasoning
where available, tool inputs/results, saved files, strategy output and wait/error/recovery
events. Provider-reported reasoning is not a promise of access to an unexposed internal process.
Credentials and secret-shaped fields are redacted before these work records are saved. Large
records have retrievable parts. Workspace files are versioned records, with content, version,
digest and provenance; they do not grant access to the host filesystem.

Lucius is a managed executor. An external agent or team can use scoped trading tools and report
its native work/actor events without adopting Lucius's internal structure. The host records
gateway tool inputs and results; work outside that gateway is visible only if the executor
reports it. Missing output or child activity remains unavailable, rather than an inferred trace.
External executor liveness is not guaranteed by a connected tool endpoint.

Pause stops new decisions and requests cancellation of open orders. Already submitted orders
may still fill; holdings remain. Restart recovery retains the mandate, session, file versions
and execution identities. Unknown broker outcomes must reconcile before new orders. Unknown
model billing blocks another paid turn until reviewed, so an interrupted request cannot
silently spend beyond an unknown budget.

Trading permission and real account authorization remain separate. Connecting Binance or IBKR
alone does not grant trading authority. The archived integrations are not authenticated live
acceptance; verify venue capabilities, isolated account identity and explicit limits before any
real-money validation. Simulated funds and holdings never enter the real book.
