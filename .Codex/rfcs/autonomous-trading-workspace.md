# RFC: Autonomous Agent Trading Workspace

**Status**: Draft

**Author**: Codex, for the Jack/TD product discussion

**Date**: 2026-11-04

## Problem

[Issue #62](https://github.com/open-net-worth-ai/open-net-worth/issues/62) requests a persistent environment in which a user delegates capital and a trading period to an agent, observes its decisions and results, and can compare different agents. Broker connections and individual automations provide execution primitives but do not yet provide a shared account/order model across historical backtesting, paper trading, and live execution. The core design question is how to make agents interchangeable while keeping time, account authority, execution, and financial results authoritative in the environment.

## Goals

- Run the example mandate: $1,000 for one month, autonomous decisions within user authorization, with visible activity and results.
- Support Lucius, external tool-using agents, and agent-authored scripts through one versioned trading contract.
- Preserve the same operation names and schemas across backtest, paper, and live modes. Mode affects execution/data adapters, not strategy calls.
- Support stock and crypto paths and multiple venue accounts with explicit capabilities and currency accounting.
- Record orders, fills, artifacts, agent activity, costs, and execution assumptions for inspection and comparison.
- Enforce limits and revocation independently of the agent's prompt, including restart and concurrency cases.
- Keep simulated portfolios outside real net worth, and reconcile live fills with the existing book without double booking.

## Non-goals

- This design task does not implement the feature, select a production broker, launch an agent, or authorize real-money execution.
- Universal support for every broker, market, and order type is not implied. Stock, crypto, and multi-venue support remain required delivery outcomes; unsupported capabilities must be explicit.
- The workspace does not promise profitable trading, exact simulation of live execution, or disclosure of private model reasoning. It records agent-authored decision summaries and observable actions.
- A public hosted tournament service is not required to support local multi-agent comparison. Agent comparison itself remains in scope.

## Background & prior art

The following are static findings at checkout `8677a121c9752a1da6c78f63fe1328dfdf213aee` on `main`; no application, broker, or agent execution was performed during this design task.

| Existing surface | Reuse and constraint |
| --- | --- |
| `backend/ai/turns.py` (`spawn`, `run_inline`) | Server-owned turn lifecycle, cancellation, durable turn records. Lucius execution must continue through this seam. |
| `backend/ai/automation_runner_adapter.py`, `backend/core/automation_runner.py` | Existing example of dependency-inverted execution with sessions, progress, permissions, and usage. Do not make services import AI runtime code. |
| `backend/services/automations/`, `backend/db/automation_store.py` | Scheduling, triggers, run ownership and recovery. The canonical store currently accepts only `execution={kind: lucius}`; general agent participation cannot be achieved by inserting an arbitrary agent kind. |
| `backend/ai/tools/registry.py`, `backend/ai/permissions.py`, `backend/mcp/client_manager.py` | Existing tool permission/confirmation authority and outbound MCP transport. Current MCP permissions can allow a trade without per-call confirmation; old ADR language suggesting an unconditional money-movement confirmation floor is historical. |
| `backend/mcp/adapter.py`, `backend/mcp/server.py` | Inbound MCP already exposes registry tools. Its instance bearer authenticates the operator, without a workspace/account capability boundary. Do not give that credential to a trading contestant. |
| `backend/ai/tools/code.py`, `backend/sandbox/runner.py` | Existing code execution accepts model-authored scripts, but the runner explicitly documents same-user filesystem access and conditional network isolation. It is not a sufficient security boundary for unattended trading scripts or protected historical evaluation. |
| `backend/services/lots.py` (`apply_lot_change`, `_apply_acquire`, `_apply_dispose`) | Acquisition/disposal, funding/proceeds legs and idempotency already exist. Extend their verified identity/transaction contract rather than inventing separate real-book accounting. |
| `backend/services/positions.py`, `backend/services/ledger.py` | Existing write chokes for holdings and transactions. |
| `backend/db/payload_store.py`, `backend/db/engine.py`, `backend/db/alembic/` | DB-backed storage, book scope and locked units of work; new indexed records and timestamp-named migrations fit here. |
| `backend/api/data_events_api.py`, frontend query cache | Existing data-change SSE and invalidation mechanism. Publish trading changes through it, including background/broker changes that do not originate in tool dispatch. |
| `frontend/src/components/tabs/connectors/catalog.ts`, `backend/services/connector_catalog.py` | Broker discovery and connector configuration. Credentials remain in connector secrets; no second broker directory or credential store. |

The July Alpaca trading removal (`docs/adr/2026-07-19-remove-alpaca-trading-half.md`) rejected an unintegrated order RPC that never booked fills. The October retirement of built-in read connectors (`docs/adr/2026-10-29-retire-ibkr-flex-for-ibkrs-own-mcp-server.md`) favors provider MCPs. This proposal preserves that direction by adapting existing broker transports behind a normalized trading environment; it does not restore the retired Alpaca tools or routes. A broker transport that cannot reliably expose order identity and fills cannot claim support for managed live execution.

External references inform adapter semantics, not a dependency choice: the [official Alpaca MCP server](https://github.com/alpacahq/alpaca-mcp-server) offers account/data/trading capabilities and paper/live configuration; QuantConnect documents [fill models](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/trade-fills/key-concepts) and [live reconciliation](https://www.quantconnect.com/docs/v2/writing-algorithms/live-trading/reconciliation). These support separating an agent's order intent from simulated or broker-confirmed execution. Neither provider acceptance nor library suitability has been validated here.

## Proposed design

### 1. A trading domain inside the existing application

Introduce a bounded `services/trading/` domain, rather than putting trading state in prompts or turning the entire automation model into a general agent framework. Keep the existing FastAPI, DB, connector, session, and frontend event infrastructure.

```text
Lucius turn / external agent / strategy script
                    |
        versioned workspace trading tools
                    |
        authenticated run + scoped authority
                    |
       trading service: validate, reserve, journal
                    |
           environment bound by run
          /             |             \
 historical replay   paper execution   live broker adapter
          \             |             /
           normalized orders and fills
                    |
      workspace account projection + metrics
                    |
          REST/SSE dashboard and comparison
                    |
       live-only idempotent book integration
```

Suggested ownership, with names provisional until the tool/API naming review:

- `models/trading.py`: normalized instrument, account, order, fill, capability and event schemas; no runtime/DB imports.
- `services/trading/workspaces.py`: mandate and lifecycle choke, `apply_workspace_change`.
- `services/trading/execution.py`: order/cancellation choke, `apply_order_change`; account reservations and submission intents.
- `services/trading/environments/`: replay, paper, and live implementations of a common interface. Clock/data and execution are separate interfaces composed by an environment.
- `services/trading/reconciliation.py`: broker execution ingestion, unknown outcomes, balance/holding checks, and canonical execution identity.
- `services/trading/book_projection.py`: live-fill bridge to existing lots/positions/ledger chokes; no simulated writes to those stores.
- `services/trading/performance.py`: one authority for equity, return, drawdown, fees and FX calculations. Reuse existing financial primitives where their scope fits; never run a second calculation in the browser.
- `db/trading_*_store.py`, indexed tables and a timestamp-named Alembic revision: persisted domain state.
- `ai/trading_runner_adapter.py`: Lucius-specific prompting, tool scope and turn execution through `turns.py`.
- `core/trading_runtime.py`: small dependency-inverted runner/request/event seam, following the existing automation pattern; no business logic or credentials here.
- `api/trading_api.py`, `ai/tools/trading.py`: thin wrappers calling the same services.
- A dedicated scoped trading MCP route, implemented alongside `mcp/server.py`/`adapter.py`, exposes only the trading contract to external agents. It does not reuse the operator-wide bearer.
- Frontend trading workspace and comparison views read backend projections through `useQueryCache`. Final navigation and layout need a UI design pass before implementation.

The current automation runner remains Lucius-specific. Reuse trigger/time handling and ownership primitives only where their contracts fit; introduce a formal trading wake-up handler in the existing scheduler when needed, not a parallel scheduler or ad-hoc launch script. Services request agent turns through the registered runtime seam, never by importing `ai`.

### 2. Workspace, run, and turn are different lifetimes

A **workspace** is a saved experiment/mandate definition. A **run** binds an immutable definition to a particular mode, participant, starting state, and period. A **turn** is one bounded decision cycle within that run. A month-long run consists of event/time-driven turns and reconciliation work; it is not a month-long open model request.

Changing mode creates a new run with explicit authorization. An agent cannot select `mode=live`, substitute a broker account, change its allocation, or authorize itself through a tool argument. The server supplies execution context; a workspace/run ID is an address, not proof of authority.

An environment presents `now`, market data and account state to the agent. Historical time is run-local and never changes the user's profile timezone or the application's `core.user_time`. Stored operational timestamps remain UTC; records carry both operational time and environment/event time.

Lucius receives a dedicated trading prompt and restricted tool scope, not the ordinary prompt embedding the operator's current net worth. Historical runs cannot call unrestricted web, current prices, personal SQL, connectors, or read later run artifacts. External evaluation agents and coding agents need equivalent process/network/data containment for a protected benchmark; merely exposing scoped tools cannot constrain an independently operated agent that has other data sources. Record that distinction in comparison eligibility.

### 3. One agent-facing contract

Proposed operations (final names must follow `docs/ai/TOOL_CONVENTIONS.md`):

| Operation | Stable meaning in every mode |
| --- | --- |
| `get_trading_environment` | Current environment time, mandate, capabilities, sessions/calendars and observation freshness. |
| `get_trading_account` | Cash by currency, settled/available funds, reservations, positions and valuation quality for authorized account bindings. |
| `get_trading_market_data` | Quotes/history available as of environment time, with source and availability timestamps. |
| `submit_trading_order` | Submit a normalized intent with instrument/venue, side, quantity or notional, order type, time in force and client request ID. Return an order acknowledgement, never an assumed fill. |
| `cancel_trading_order` | Request cancellation of an owned order and report its actual known state. |
| `list_trading_orders`, `list_trading_fills` | Cursor-based, bounded authoritative execution records. |
| `record_trading_decision` | Persist an agent-authored summary and artifact references linked to an intent/turn. |

An instrument identity includes asset class, venue and quote currency; a ticker string alone is insufficient. Results include a schema version, observation time, source and freshness/reconciliation status. Structured failures distinguish unsupported capability, policy refusal, market closed, insufficient funds, stale observation, venue error, and unknown submission outcome. All wrappers share these semantics.

Backtest advances time through a server-owned event driver; paper/live advance with their clock. An agent may request its next wake-up through the same scheduling contract, but does not control the clock or receive future data. Script libraries wrap these operations; no strategy needs a provider SDK or broker secret.

### 4. Environments and historical execution

- **Backtest**: replay versioned market events using a run-local virtual clock, historical FX and instrument metadata, and a declared fill/fee/slippage model. Data must be filtered by when it became available, not just the date it describes. Corporate actions, calendars, delistings and symbol changes must be supported or identified as explicit run limitations. A close-based decision cannot receive a fill at an already-observed bar's earlier price. Freeze simulation time while awaiting a decision, record a declared decision-latency model, then process subsequent events in a reproducible order.
- **Paper**: current market observations and real-time wake-ups with the same account/order/fill model and configurable simulation assumptions. Broker-hosted paper can be another execution adapter; it is not interchangeable with a deterministic local simulator for reproducibility claims.
- **Live**: use verified broker acknowledgements, trade events and reconciliation. Normalize provider MCP tools behind a reviewed mapping for account identity, instrument identity, order submission/query/cancellation, client ID behavior and fill retrieval. Detect schema drift and fail closed. Arbitrary MCP text is not a normalized fill. Consider a direct transport only where a demonstrated MCP gap requires it and document the departure from the existing ADR direction.

Do not choose a new backtest framework solely for this RFC. First specify the contract and stock/crypto fixture cases, then assess a focused simulator versus adapting an established engine against calendars, corporate actions, fill models, deployment footprint and data licensing. The environment interface keeps that engine choice local. Do not label a simple candle replay as validated execution realism.

Version datasets, universe, adjustment policy, execution model, seed and event order. A recorded action stream can have deterministic replay; a fresh LLM run need not make identical decisions. Tool-level historical isolation also cannot erase facts memorized in a model's training. Historical competitions must disclose that residual contamination; prospective paper/live comparisons avoid hindsight about their future period.

### 5. Authority, capital and script execution

The human authorizes a run against a named account binding and mandate. The existing permission engine remains the authority for tool approval. Trading services additionally validate domain facts (ownership, run status, capital, reservations, capabilities and limits) on every order, including approved/redeemed calls. This is business validation, not a second confirm-card mechanism. Workspace grants do not silently widen global tool permissions; global permission or mandate revocation takes effect on subsequent dispatch. Cover both `permissions_v2` paths without changing ordinary conversations.

Use scoped, revocable, expiring run credentials for external clients, with a narrow operation set, server-bound run/account identity, rate/usage limits, and auditable principal. Raw credentials belong in the existing secret-storage boundary; public tables contain only grant IDs and non-secret metadata. Agents receive no operator bearer or broker credential. Do not enable arbitrary outbound broker MCP tools for the trading runner alongside its normalized execution tools.

In live mode, use a dedicated funded account/subaccount or an account wholly mediated by this boundary. A virtual $1,000 counter cannot constrain an unrestricted broker key or human trades against the same pool. Detect outside activity and pause for reconciliation. Across multiple accounts, record a capital allocation per venue/currency and value aggregate exposure consistently; idle cash is not instantly transferable to another venue. Funding/withdrawals are human-controlled changes, not implicit agent permissions.

An allocation bounds the funded scope and authorized exposure; it cannot promise an exact maximum loss through gaps, leverage, or venue failures. Any loss/drawdown trigger must state valuation freshness and resulting action. Enforce allowed leverage/shorting/order size as actual venue-aware constraints, not prompt instructions. Model spend has its own budget and reporting, separate from investable capital.

The current same-user script runner is insufficient. Add a formally supported isolated execution backend before treating autonomous scripts as contained: no host data/credential mounts, least-privilege OS identity, bounded CPU/memory/process/time/storage, and network restricted to the run's tool gateway plus explicitly allowed resources. The `sandbox` package remains a secret-free leaf; parent-side AI/services handle authenticated gateway traffic. Refuse managed script execution when the chosen platform cannot provide the required isolation, and visibly declare supported execution hosts. Windows remains supported by the app; Docker/VM-backed isolated execution or another verified platform backend must be designed instead of silently assuming POSIX primitives work there.

Scripts can either emit typed order intents from a data snapshot or use a scoped SDK to interact with the gateway in a longer-lived strategy. Both paths enter `apply_order_change`; neither can directly mutate the real book or call a broker. Script source, dependencies and artifact hashes are versioned per turn/run. A coding agent's outer process needs the same containment, not only its generated scripts.

### 6. Data model and execution reliability

Proposed DB records, all belonging to the current book and constrained by run/account ownership:

| Record | Main fields / invariants |
| --- | --- |
| `trading_workspaces` | Name, mandate, participant configuration, allowed account bindings, definition revision. Human edits create a new revision. |
| `trading_runs` | Frozen definition, mode, participant/model/version, period, initial balances, state, clock cursor, data/model manifests and usage budget. |
| `trading_accounts` | Run, venue/account identity, connector reference (no secrets), currencies, allocation, capabilities, reconciliation cursor/status. |
| `trading_orders` | Run/account, canonical instrument, normalized intent, request ID, broker IDs, state and reserved funds/quantity. Unique `(run, account, request_id)` with an immutable payload hash. |
| `trading_fills` | Broker/simulator execution identity, order, quantity, price, currencies, fees, execution time, correction/reversal references. Unique identity scoped to venue/account. |
| `trading_events` | Ordered lifecycle/activity records with run/turn/order/fill links, environment time, received time and bounded sanitized payload. |
| `trading_portfolio_snapshots` | Rebuildable balances/positions/equity, valuation timestamps, missing marks and historical FX provenance. |
| Run grant metadata | Principal, run, permitted operations, expiry/revocation; secret material stays in the secret boundary. |

Persist order intent and reserve funds/position quantity together under an account-scoped locked unit of work, considering all outstanding orders and all agents sharing the account. Submission is dispatched from a durable intent; never hold a DB transaction open over network I/O. Recheck run status, revocation and reservation at the dispatch claim boundary. Serialize conflicting submits, stop and revoke transitions through that same boundary.

A request ID reused with a different payload is an error. A timeout after sending becomes `submission_unknown`, not an automatic new order: reconcile by broker client ID/order history before any safe retry. A venue lacking the necessary identity/query behavior is not eligible for autonomous managed live submission. Workers recover durable intents/leases after restart; a process-memory lock alone is insufficient.

Process acknowledgements, rejects, partial fills, cancellation requests/confirmations, expiration and late fills as distinct events. Ingest duplicate/out-of-order broker events idempotently, retain correction history and rebuild projections. A cancel request does not release reserves as if cancellation were already confirmed. Account balances/holdings from the broker are reconciliation evidence, not extra transactions to add on top of booked fills.

For real-book integration, use one canonical broker execution identity across workspace ingestion and all relevant broker imports. Book an acquisition/disposal through existing lot chokes, which already handle funding/proceeds; do not also mint an independent cash transaction. Verify or extend transaction composition so execution-identity claim, bookkeeping result and projection marker commit atomically. Repeated ingestion after a crash must return the prior booking. Do not deduplicate by similar price/quantity/time: identical trades can be legitimate, and an order can contain several fills. An importer without compatible identity must be updated or blocked from overlapping managed ingestion until it can reconcile explicitly.

External order/fill records cannot be undone by restoring an application checkpoint. Operational identities, revocations and submission history need explicit backup/checkpoint treatment: local undo must not delete evidence of a sent order, re-enable an expired grant, or cause resubmission. After a restore, require live reconciliation before new execution. Keep the restorable real book reconstructible from broker-confirmed fills without re-sending orders.

### 7. Lifecycle, observation and comparison

Run states: `draft -> ready -> running`, with `paused`, `stopping`, `stopped`, `completed`, `failed`, and `needs_reconciliation` as explicit outcomes/transitions. Actor turns have their own statuses and can fail without pretending that no trade occurred.

Pause/stop/expiry immediately revoke authority to claim new order intents, stop future agent wake-ups, and continue observing outstanding broker orders. Proposed default is to request cancellation of open orders and retain positions; flattening is a distinct explicitly authorized action, because it submits new trades. The mandate fixes this policy at start. In-flight sends may already have reached the venue and are reconciled. Show pending cancellation/unknown outcomes until resolved; do not report a cleanly completed run while orders are unresolved. Expiry initiates the same transition on the server even when no browser is open.

The workspace view should answer: what mandate/mode is running; what the agent last did and plans next; what orders actually executed; what is owned; what changed financially; and whether observations are current. Display decisions, code/artifacts and tool receipts with linked order/fill records, along with errors, pauses and usage. Agent-authored summaries are not broker evidence.

Use paginated REST snapshots/events plus the existing data-event SSE to invalidate cached projections; events carry a monotonic cursor so clients can backfill after disconnect. Financial values come from `services/trading/performance.py`. Missing/stale prices or FX produce an incomplete/stale valuation, never a misleading zero or ranking.

A comparison groups separate participant runs against a frozen scenario: starting capital, tradable universe, period, data access, wake-up/latency assumptions, execution model, costs and agent/model/strategy versions. Participants have separate simulated accounts; independent live comparisons need separated account scopes. Report returns, drawdown, exposure and fees, and show model costs separately with an optional explicitly defined net-of-model-cost view. Define return treatment for deposits/withdrawals before ranking; forbid them in a fixed-capital scenario or use an agreed flow-adjusted method. Persist comparison manifests with run references; do not compare unlike assumptions as equivalent. Shared fairness does not require every participant to use the same strategy or write code.

### 8. HTTP and tool surfaces

Proposed REST surface under `/api/trading`:

- `workspaces` and `workspaces/{id}`: create/read/update a saved definition.
- `workspaces/{id}/runs`: create a run from a frozen definition; mode/account binding are fixed here.
- `runs/{id}` and `runs/{id}/actions`: get status and perform human lifecycle/authorization actions, using the app's normal authentication and CSRF protections.
- `runs/{id}/accounts`, `/orders`, `/fills`, `/events`, `/performance`: bounded reads of authoritative projections; submit/cancel routes call the same execution service as AI tools.
- `comparisons`: create/read a scenario manifest and eligible participant results.

Workspace/run authorization, capital changes, live activation and any flatten instruction remain human control-plane actions. Agent operations are only the scoped contract above. The agent gateway cannot invoke lifecycle/control-plane endpoints or arbitrary registry tools.

## Alternatives considered

**Compose existing automations and broker MCP tools only.** This is useful for prototyping an individual trading workflow, but leaves provider-specific schemas, unrestricted account access, fill ingestion and comparison state scattered across prompts. It cannot satisfy the shared environment contract. Reuse automations' existing primitives without treating the automation record as the account/order authority.

**Make Lucius and its tools the entire platform.** This provides one convenient first participant, but couples historical time and account state to its personal-finance prompt and excludes independent agents. Keep Lucius as an adapter to the same environment other participants use.

**Create a separate trading service/product immediately.** Process isolation can be valuable for execution security and heavy simulations, but a separate product would duplicate application setup, DB/API/event wiring and connector configuration before that need is established. Keep a clear domain boundary inside this application and isolate agent/script processes; the environment contract allows later deployment separation without changing agent tools.

**Adopt a full quant engine before defining the contract.** An established engine may eventually save substantial simulation work, but its deployment/data assumptions and integration cost need assessment against actual stock/crypto acceptance cases. Keep engine selection behind the environment adapter and resolve it during the simulation milestone.

## Implementation plan

These are ordered delivery milestones covering the full issue, not permission to omit later modes, scripts, external agents or comparison. Each milestone is reviewable; overall completion requires all applicable issue criteria.

1. **Specify the contract and state model.** Freeze normalized schemas, capability/error semantics, run-local clock, order transitions, request identity, lifecycle defaults and comparison manifest. Define stock/crypto contract fixtures and examples that drive all adapters. Resolve the initial venues/data sources and isolated execution hosts before committing to their implementation. Record accepted cross-cutting decisions in ADRs after agreement, not while this RFC remains a draft.
2. **Persist the domain and enforce the execution boundary.** Add additive timestamp migrations, stores, service chokes, scoped grants, account reservations, durable intents, replayable event records and checkpoint/recovery rules. Add focused authorization/concurrency/idempotency/restart tests. Review money movement, grants and external I/O before enabling them. No live external effect in this milestone.
3. **Implement backtest and paper environments.** Select the simulation backend from the contract fixtures; add historical availability filtering, clock drivers, fill/fee/slippage models, current-data paper mode, valuation/currency calculations and scenario provenance. Cover stock, crypto and multiple account bindings. Verify no simulated writes reach real-book stores, and include adversarial future-data and bar-fill tests. Prove recorded-action replay separately from LLM rerun behavior.
4. **Integrate autonomous participants and scripts.** Add Lucius turns through `turns.py`, formal scheduler wake-ups, dedicated prompts/tool scopes, isolated script execution and the external scoped MCP gateway. Exercise the same strategy tool calls across both simulated environments, an agent-authored script path, and a real external tool-using agent. Include real provider-backed agent receipts/artifacts/costs rather than only mocked model outputs. Prove grant revocation, host-data isolation and escape-path refusal on each claimed execution host.
5. **Deliver the workspace dashboard.** Obtain a focused UI placement/layout spec; implement configuration, lifecycle controls, activity/artifacts, orders/fills, account state, performance and costs with the existing cache/SSE. Update product docs in the behavior change. Use isolated QA ports 8090-8099 and synthetic data to verify the one-month/$1,000 story, reconnect, stale data, partial fills, failures and stop behavior. UI can be developed alongside participant integration once backend projections stabilize.
6. **Integrate live adapters and real-book reconciliation.** Verify selected broker transports with funded-scope enforcement, actual paper/sandbox APIs first, then separately authorized real-money acceptance. Confirm client-order ID lookup, partial fills/cancels/reconnect behavior, multi-currency accounting and canonical fill identity against imports. Reconcile restarts and restores. This milestone introduces externally irreversible trades; do not run it against a real account without the user's specific authorization and funded-account setup. Provider paper evidence must be labeled as such and cannot substitute for live evidence.
7. **Complete agent comparison and final acceptance.** Add frozen comparison scenarios, independent participant runs, eligibility/provenance checks, backend-derived metrics and the comparison view. Demonstrate Lucius and an external agent under comparable conditions, including a script-based strategy and stock/crypto paths. Map evidence to every issue checkbox; distinguish simulator tests, actual agent/provider runs, broker paper execution, live execution, and UI acceptance. Run proportionate focused checks throughout and broader integration/architecture checks for the assembled cross-cutting feature.

## Open questions

- Which stock/crypto venues and historical/current data sources are available to intended users, and what data retention/licensing constraints apply? Alpaca MCP is a candidate to investigate, not a selected dependency or a claim of regional account eligibility.
- For live allocation, can the selected venue provide a dedicated funded account/subaccount, or must the whole account be mediated? How should outside manual trading be handled beyond pausing for reconciliation?
- Which isolated execution hosts/backends will be supported for coding agents and scripts, including Windows? The current bare subprocess is not sufficient.
- What historical resolution, order types, liquidity/latency assumptions and corporate-action coverage are required for the initial stock/crypto acceptance scenarios? This determines simulator selection and data needs.
- Should the user choose cancel-and-hold versus an explicitly authorized liquidation policy at run creation? This RFC proposes cancel-and-hold as the default; that product decision is not yet accepted.
- Which live broker imports can share a verified canonical execution ID from the first managed venue? Any unmappable overlap blocks safe automatic book projection for that combination.
- What common information access and model-compute budgets define a comparison category? Historical results need explicit disclosure of model-training hindsight and external-agent containment status.

## References

- [Feature issue #62](https://github.com/open-net-worth-ai/open-net-worth/issues/62)
- `BACKEND_ARCHITECTURE.md`, `CLAUDE.md`, `docs/INVARIANTS.md`
- `docs/adr/2026-07-19-remove-alpaca-trading-half.md`
- `docs/adr/2026-10-29-retire-ibkr-flex-for-ibkrs-own-mcp-server.md`
- `backend/ai/turns.py`, `backend/ai/automation_runner_adapter.py`, `backend/core/automation_runner.py`
- `backend/services/automations/`, `backend/db/automation_store.py`
- `backend/mcp/client_manager.py`, `backend/mcp/adapter.py`, `backend/core/mcp_external.py`
- `backend/ai/tools/code.py`, `backend/sandbox/runner.py`
- `backend/services/lots.py`, `backend/services/positions.py`, `backend/db/payload_store.py`
- [Alpaca official MCP server](https://github.com/alpacahq/alpaca-mcp-server)
- [QuantConnect fill-model concepts](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/trade-fills/key-concepts)
- [QuantConnect live reconciliation](https://www.quantconnect.com/docs/v2/writing-algorithms/live-trading/reconciliation)
