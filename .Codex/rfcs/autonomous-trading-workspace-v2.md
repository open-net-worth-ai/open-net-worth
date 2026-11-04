# RFC: A Delegated, Observable Trading Workspace

**Status**: Redesign implemented for product review in PR #63; funded venue acceptance and owner sign-off remain outstanding

**Author**: Codex, incorporating the owner's clarified requirements

**Date**: 2026-11-04

## Problem

The owner reviewed PR #63 and rejected its product experience as insufficient for Issue #62. The implementation emphasizes saved definitions, separately configured runs, manual strategy execution and financial records. The intended product is a persistent workspace that accepts a long-lived trading mandate, hosts an autonomous executor, and lets the user understand both its work and the resulting market/account activity. Relabeling a workspace as a strategy or adding broker adapters does not close that gap.

The example is: tell Lucius to allocate a hypothetical $1,000 for one month, choose an eligible executor and environment, review the authorization, then let that executor research, develop strategies, trade or wait within its mandate. A user can leave and return to an understandable account of what happened. The user is not required to supply a strategy or repeatedly trigger decisions.

## Goals

- Create the same workspace and mandate through a conversation with Lucius or an expert form.
- Preserve and visibly present the original instruction, current mandate, funding, period, executor, authorization and amendment history.
- Host one logical delegated executor per workspace without prescribing its internal form: single agent, team, coding CLI or external service.
- Provide persistent context, files and artifacts, automatic wake/resume, research and code execution, and direct order tools within the authorized environment.
- Give market/financial observability and agent-work observability equal prominence, with bidirectional links between them.
- Show as much authentic executor output as is safely available, including exposed analysis/reasoning, inputs/outputs, tool records, files and team activity. Explicitly identify unavailable information.
- Preserve the shared backtest/paper/live trading contract, stock/crypto and multi-account/currency requirements, reliable order/fill accounting and comparable agent evaluation from Issue #62.
- Keep all product copy and supplied demonstration content in English. Preserve user-authored original instructions verbatim.

## Non-goals

- This RFC defines the redesigned product implemented after the owner's request to change the actual app directly. It does not authorize real trades or substitute for funded venue acceptance. The owner cancelled the proposed standalone HTML mockup.
- The product does not require the user to author a fixed strategy, and does not prescribe a team's topology or a particular model/provider.
- The product does not promise profitable trading or invent unexposed internal model reasoning. It presents actual provider/executor output according to the capabilities available.
- Automations provides reusable interaction and runtime patterns; this design does not replace its one-task execution semantics with a trading account or build a second generic scheduler.
- Supported markets and broker capabilities must be explicit. Universal venue/order support is not implied. Binance and IBKR remain approved integration targets, with implementation paused during this redesign; they do not replace the core product acceptance requirements.

## Background & prior art

The following findings were checked against the PR branch at `b66b1496` and its paused working changes. They describe code, not proof of the redesigned behavior.

| Existing surface | Reuse / actual gap |
| --- | --- |
| `frontend/src/components/tabs/Automations.tsx`, `automations/AutomationDetail.tsx`, history/result components | Reuse task-first instructions, readable setup review, status, history and expandable results. An automation run is a bounded task execution; a trading workspace lasts through many decisions. |
| `frontend/src/lucius/screenCommands.ts`, `backend/ai/tools/automations.py` | `automations.review_draft` is an existing conversational review pattern. Trading needs its own draft/review surface and shared creation service, rather than requiring form-only creation. |
| `backend/ai/turns.py`, automation runtime adapter and existing scheduler | Keep server-owned execution, cancellation, permission gates, usage and durable scheduling. Logical continuity does not require one model request to remain open for a month. |
| `backend/core/trading_runtime.py`, `backend/ai/trading_runner_adapter.py` | Current bounded Lucius cycles are useful components. The current tool scope is nine trading operations; the prompt forbids other data sources. This is insufficient as the general research/files/code workspace contract. |
| `backend/models/trading.py`, `services/trading/` | Retain account/order/fill/permission/reconciliation machinery where correct. The current Observation is price plus optional volume, not an OHLCV bar. Existing model restrictions and the public run-first workflow need redesign. |
| `frontend/src/components/tabs/trading/TradingActivity.tsx`, trading runner event recording | The UI shows recorded event payloads, but the runner currently journals only selected tool event type/name/turn identifiers. That is not complete tool input/result or provider-trace observability. |
| `frontend/src/components/intel/AssetPriceChart.tsx` and `assetPriceChartLwc` | Reuse chart rendering where possible. Bind it to the workspace's authorized, provenance-bearing market series; do not reuse unrelated current-price fetches in historical workspaces. |
| `backend/db/mcp_client_store.py`, connector catalog/secrets and scoped trading gateway | Reuse connection management and execution authority. Connected/OAuth-complete is not equivalent to funded, isolated or executable. |
| `backend/services/lots.py`, ledger/position write chokes, query cache and data-change SSE | Retain authoritative financial writes and cross-screen refresh. Simulated accounts stay separate from real net worth. |

Earlier tests and real-provider synthetic executions establish useful component behavior. They do not establish this revised product's natural-language setup, research freedom, continuous autonomous operation, complete work trace, candles or joined financial/work inspection.

## Proposed design

### 1. User-facing model and creation

The primary object is a **Workspace** containing a **Mandate**, an authorized **Environment**, an **Executor**, persistent working state, and financial/work records.

The original user instruction is retained verbatim. A normalized mandate makes the objective, period, accounts, capital, allowed markets, risk/permission limits, agent cost budget and end policy understandable. The original version remains immutable. Amendments record author, time, old/new values and effective revision; later changes must not rewrite the authority under earlier decisions or orders.

Two entry points lead to the same draft, validation, persistence and authorization service:

1. **Create with Lucius**: converse, resolve missing material information, produce an editable mandate review, and show what is ready or blocked. Do not invent account identity, data availability or an authorization the user did not give.
2. **Expert form**: enter the same fields directly, with optional execution/data settings progressively disclosed.

Creation and permission to place real orders remain distinct. Reviewing a draft does not silently fund an account or grant live authority. The normal product flow is **Describe mandate → Review → Start workspace → Observe / control**. Backend execution attempts, decision cycles and existing run identifiers may remain implementation records; they are not mandatory concepts a new user must configure before delegating.

### 2. Continuous autonomous work

The executor can inspect authorized market/account information, perform research, write/test/run code, place orders directly, revise its approach, or choose to wait. A workspace must not restrict all executors to a prewritten strategy or the current narrow quote/order-only tool offer.

Persistence covers the mandate/revision, session/task handles, working files, code versions, research references, artifacts, decisions, orders/fills, wake conditions, usage and interruption/recovery state. Credentials are outside the workspace filesystem and observable payloads. Only explicitly selected project-owned credentials reach the runtime through its existing approved mechanism.

Use the existing server-owned turn and scheduler infrastructure. Extend the formal trading runtime seam with executor lifecycle/capability adapters, not ad hoc launch/monitor scripts or a separate scheduler. Managed adapters start/resume/observe/stop their executor; external participants use scoped tools plus an event/artifact ingestion contract. Recovery resumes the same logical mandate and reconciles external orders before issuing new ones. A process attempt is not a fresh capital allocation.

Waiting is a valid state, with the actual reason and known next wake/condition visible. A disconnected external executor is **Disconnected / status unknown**, not falsely presented as working. Researching, coding, submitting, waiting, awaiting user input, blocked, recovering, paused and ended must be distinguishable. Paper/live operation cannot depend on repeated **Run agent now** clicks. Backtest progression is environment-owned and automatic during normal execution, not a mandatory manual **Advance** workflow.

### 3. Agent-neutral capability contract

Separate trading authority from the executor's internal topology. An executor descriptor records its identity/version, runtime type and actual capabilities: persistent-session resume, files/artifacts, input/output capture, tool trace, exposed reasoning or summaries, actor/subagent identity, wake requests and cancellation support.

One workspace has one logical delegated executor. A team can emit actor and parent-child events without forcing every executor into a Lucius-specific team schema. Unsupported visibility or control capabilities remain explicit. An independent agent can have activity outside the gateway; gateway observation alone must not be labeled as its complete internal trace or a contained historical evaluation.

Every research/data capability must respect its environment. Historical work receives only information available as of its environment time, with known model-training contamination limits. Paper/live can use authorized current research capabilities. A broad research interface must not give access to unrelated personal-book data, broker credentials or a second order path.

### 4. Two linked observability layers

**Market & Portfolio** shows instrument/venue/timeframe selection, real OHLCV candles and volume where available, source/delay/availability times, buy/sell fill markers, open orders, cash/reservations, holdings, account activity/statements, realized/unrealized P&L, equity/return/drawdown and fees. Agent/model costs are separate. If only quotes exist, show a clearly labeled quote series and an explicit unavailable candle state; do not synthesize OHLCV candles from unrelated or future data.

**Agent Work** shows current task/progress, emitted analysis, provider-exposed reasoning content or reasoning summaries, model/task inputs and outputs, tool names/arguments/results/errors, code and file changes, artifacts, actor/subagent relationships, wake/wait/blocking/recovery records, and runtime usage. A readable timeline and an expandable source/structured view expose the same evidence. Do not replace detailed emitted records with only a short generated summary.

Capture what the integration actually provides. Label direct executor output, host-observed facts and derived summaries distinctly. Where the provider exposes only a summary, label it **Reasoning summary**; where it provides no reasoning, show **Not provided by this executor**. Do not request, reconstruct or fabricate hidden internal reasoning. Redact credentials and restricted private material before durable recording; disclose that redaction occurred. Large safe records should be paginated or stored as retrievable artifacts rather than silently truncated. Unknown/new event types can retain a safe native payload view without pretending to understand their semantics.

Both layers share explicit correlation: workspace, mandate revision, executor/actor, decision/task/tool call, input/data snapshot, code/artifact version, order, execution fill and valuation. Records carry source/emitted time, observed time, environment time, sequence and provenance. A chart fill opens the actual order/fill and related decision, data/tool/code records; a decision opens its resulting order or its recorded reason for no trade. A missing relationship is shown as missing, never inferred as a fact. P&L is an authoritative portfolio projection, not causally attributed to a decision by a language model.

### 5. Page structure

All supplied UI and demo copy is English. Reference Automations' task description, review, execution state, chronological history and detail disclosure. Do not rename Workspace to Strategy or make the user choose a fixed strategy first.

```text
Trading                         [Create with Lucius] [Expert form]
Workspace list: mandate excerpt · executor · state · capital · period

Workspace: Monthly trading mandate                   [Pause] [End]
Original mandate: "Manage $1,000 for one month ..." [View mandate]
Current revision · mode/funds · period · executor · amendments
Current work: actual task/state · last activity · next condition/blocker

+---------------------------------+--------------------------------+
| Market & Portfolio              | Agent Work                     |
| Instrument / timeframe          | Current work and work timeline |
| Candles + volume + fills        | Analysis / provided reasoning  |
| Equity, P&L, cash, fees          | Tools / inputs / outputs       |
| Positions / orders / statements | Code / files / team activity   |
+---------------------------------+--------------------------------+
Selected trade <-> selected decision/data/tool/artifact detail
```

Both layers are first-class on desktop. Narrow layouts use clearly named **Market & Portfolio / Agent Work** tabs with mandate, overall state and controls still visible above them. The selected record preserves time and cross-links when switching layers. Files/source/raw record detail is discoverable from the relevant work event. Technical provenance and raw payloads can be expanded; current work, account results and important blockers cannot be buried in an advanced panel.

Empty workspaces invite delegation; draft review names missing setup; running/waiting/blocked/recovering and ended views show truthful states. Stale market data, incomplete valuation and missing trace coverage are distinct. Ending does not pretend to undo fills or liquidate holdings without the mandate's explicit policy and authority.

### 6. Shared environments and financial boundary

Keep one agent-facing account/data/order/fill interface across backtest, paper and live. The environment owns time, data availability, order execution and accounting. Mode/authorization is server-bound. Session calendars, order types, liquidity, fees, slippage, latency and partial fills remain declared capabilities and recorded assumptions.

Live funding must be enforceable through an eligible dedicated account/subaccount/partition or another fully mediated allocation. All native/team/CLI/script/external paths use the same execution boundary. Broker identity and client order/execution identities must survive retries, ambiguous submissions, cancellation, restarts and imports. Use existing financial chokes and canonical fill identity; paper/backtest funds never enter real net worth. Stop/revoke blocks new actions but cannot guarantee an already-sent order will not fill. Show reconciliation and remaining holdings truthfully.

Binance and IBKR support still requires verified provider schemas, eligible account authority, fees, data and real runtime evidence. Connector implementation is not the definition of the product, and account availability is not authority to submit a real order.

### 7. Proposed modules and interfaces

- Keep `api → ai → services → db → core`; financial math stays in services.
- Extend the workspace/mandate services with original text, revisions and one shared draft/review/start lifecycle. Lucius tools and forms call those services with the same gates. Reuse the existing Automations screen-command review pattern, not its business entities.
- Extend the formal runtime seam and executor adapters for persistent workspace state, resume/wake and capability declarations, reusing `turns.py` and the existing scheduler.
- Extend trading events/artifact storage for complete safe executor records, actor hierarchy, provenance and correlation. Keep conversation blocks/provider traces as authoritative sources where available; do not derive full inputs/results from the current event-name-only records.
- Extend the market-data contract with actual bars and availability metadata; serve the chart and executor from the same environment-scoped source. Reuse the chart renderer with a new trading data seam.
- Add thin REST/tool operations for mandate drafts/review, lifecycle, work history/artifact detail and market bars. Exact route/tool schemas are implementation design work, not final API names promised here.
- Redesign the Trading frontend around the joined workspace, reusing query-cache invalidation, shared primitives and Automations interaction patterns. Keep backend execution IDs as deep-link/provenance details rather than the normal onboarding workflow.
- Resolve additive schema/migration mappings explicitly before implementation. Preserve current operational identities and history; do not delete existing order/fill records to simplify the redesign.

## Alternatives considered

**Keep the run console and improve labels/disclosure.** This retains useful infrastructure but leaves users assembling the agent's job and manually driving an execution-oriented UI. It does not deliver natural-language delegation, a persistent work environment or complete observability.

**Put trading entirely inside an ordinary Automation.** This reuses familiar authoring and scheduling, but a sequence of independent one-task runs does not by itself own a funded, multi-decision mandate, continuous workspace state, market clock, financial ledger or two-layer observability. Reuse its execution and interaction primitives without treating those lifetimes as equivalent.

**Make the workspace own the mandate/environment/records and adapt interchangeable executors.** This matches the owner's intent and preserves the financial execution boundary. It requires a real persistence/capability/event contract and joined UX, rather than a new broker form. This is the proposed direction.

## Implementation plan

1. Complete product/UI design and update Issue #62/PR #63 to make the acceptance reset explicit. This document is this phase; no implementation completion is implied.
2. Map the existing schema/services to the workspace-owned mandate lifecycle, revisions and one shared conversational/form draft flow. Preserve existing execution identities and history.
3. Implement persistent executor state, automatic wake/resume/recovery, research/code capabilities and complete safe work recording through the existing runtime/scheduler seams. Validate real Lucius plus an independent executor.
4. Implement the two linked UI layers, actual bars/data provenance, original mandate/amendments, record detail and cross-layer navigation; keep all supplied UI/demo text English.
5. Carry existing simulation and financial safety into the new lifecycle. Complete supported stock/crypto/multi-account/currency paths and the approved broker adapters under the same interface. Obtain separate account/data/order authorization only when real provider validation needs it.
6. Validate the complete delegation experience, unattended operation, restart/revocation/partial-fill/ambiguous-submit cases, visibility capability differences, candle/time correctness, financial identity and comparable agent workspaces. A successful single order, screenshot, schema test or connector login is not a substitute for this product acceptance.

Phases sequence the full scope; they do not make requested features optional or waive prior financial/live validation criteria. UI/permission/model changes require proportionate code, security and running-app review before delivery. No merge/trusted claim without the owner's acceptance.

## Open questions

- For each selected executor, which lifecycle, native event, file and exposed-reasoning capabilities are actually available? Discover and record them; do not require a proprietary internal agent shape.
- Which market provider/dataset supplies the required historical/current bars and research data, with what availability/session/delay guarantees? Missing sources need visible setup or unavailable states.
- What concrete broker account/subaccount/partition and order authority is available for funded validation? No real-money action is authorized by this design.
- Final storage/API/migration details need an implementation RFC or revision after the owner reviews this product/interaction design. They must preserve the approved behavior, not redefine it around current tables.

## References

- [Issue #62](https://github.com/open-net-worth-ai/open-net-worth/issues/62)
- [Product redesign update](https://github.com/open-net-worth-ai/open-net-worth/issues/62#issuecomment-6641709097)
- [Draft PR #63](https://github.com/open-net-worth-ai/open-net-worth/pull/63)
- [Historical first RFC discussion](https://github.com/open-net-worth-ai/open-net-worth/issues/62#issuecomment-6639145270) (superseded product model)
- Existing source seams named above; `BACKEND_ARCHITECTURE.md`, `docs/INVARIANTS.md`, `docs/ai/TOOL_CONVENTIONS.md`, `docs/UI_QUALITY_GATE.md`.
