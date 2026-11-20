# SPEC — TradingAgents integration (Open Net Worth)

Status: approved for build (2026-11-19). This file is the canonical copy. Every build agent
reads it before starting a work package (§13).

## 0. Context

**Why.** The owner wants deep, multi-agent stock analysis inside ONW. TradingAgents
(github.com/TauricResearch/TradingAgents, Apache-2.0) runs analysts, a bull/bear debate, a trader,
a risk debate and a portfolio manager. It returns a 5-tier rating plus written reports. ONW has
no equivalent. Today Lucius has `get_asset_technicals`/`get_asset_fundamentals` and the
`position_verdict` from `monitoring/analyst.py`, but no debate-style research.

**Framing.** TradingAgents is a **plugin that makes Lucius stronger at investing**. It plugs
into ONW's own seams: connector (on/off, settings, permissions), ONW's LLM providers (all
transports, CLI included), ONW's cost tracker, ONW's tool registry. It brings no LLM keys or
LLM clients of its own.

**Outcome.**
1. A setting turns the integration on or off (connector `trading_agents`, shipped dark behind
   release flag `trading_agents`).
2. The user can run an analysis from the asset page. Lucius can run one through tools.
3. Every LLM call a run makes goes through ONW's provider layer and lands in the cost tracker.
   Settings → Cost & Performance has a TradingAgents breakdown by run, role, model and ticker,
   with per-run and monthly caps that are **enforced**.
4. TradingAgents gets the **real portfolio amounts it needs** for a meaningful assessment: real
   cash available to invest, the real position in the analyzed ticker, and the other investment
   positions (for concentration and sizing). Data with no investing use (account names,
   institutions, transactions, income, budget, memory, profile) never leaves.
5. **Lucius's take uses it.** "Lucius's take on your position" (asset page, Analysis tab) reads
   the ticker's latest finished run, combined with Lucius's own analysis and never replacing it
   (§16, WP9). The run card is also condensed so the rating and a short decision lead (WP10).

**Decisions (owner, 2026-11-19).**
| Topic | Decision |
|---|---|
| Runtime | Isolated worker: its own venv under the data dir, one subprocess per run, JSON-lines protocol |
| LLM | **Unified with ONW.** The worker never talks to an LLM vendor. It points TradingAgents at a per-run, loopback-only, OpenAI-compatible **LLM bridge** that ONW runs. The bridge calls ONW's own provider (`AIProvider.stream_chat`) — Anthropic, OpenAI-compatible, Claude Code CLI, Codex CLI, Antigravity CLI. No API key reaches the worker. |
| Toggle | Connector `trading_agents` (enable/pause/settings/permissions/Lucius discovery) + release flag `trading_agents` |
| Portfolio context | Real investing amounts (§6). Configurable level, default `investments`. |
| Orders | Never. Out of scope permanently for this integration. |

**Out of scope (v1).** Placing orders (permanent). Writing to paper trading (`paper_trading` may
only be imported by its own router/tools/Automations/scheduler). Scheduled bulk runs over all
holdings (an Automation can do it with the tool, §8). Crypto (`asset_type` other than `stock`).
TradingAgents' own provider clients (ChatGPT login, Bedrock, Azure, direct vendor keys).

## 1. Architecture

```
frontend ──► api/trading_agents.py ──► ai/trading_agents/runner.py ──spawn──► worker (venv python)
Lucius tools (ai/tools/trading_agents.py) ─┘   │      │                       ta_worker.py
                                               │      │  stdin: request JSON   imports tradingagents
                                               │      │◄─ stdout JSON lines ── (progress/llm_call/result/error)
                                               │      │
                                               │      └─ starts per-run LLM bridge (127.0.0.1:<random>, bearer token)
                                               │             ▲  OpenAI /v1/chat/completions
                                               │             │  (TradingAgents llm_provider="openai_compatible")
                                               │   ai/trading_agents/llm_bridge.py
                                               │             └─► ONW AIProvider.stream_chat (any transport)
                                               │                   └─► ai/cost_tracker.record(source="trading_agents")
                                               └─► services/trading_agents/runs.apply_run_change ─► db/trading_agents_run_store.py
```

**Layer placement (dependency rule `api → ai → services → db → core`).**
- `services/` must not import `ai`. The **runner** and the **LLM bridge** use providers and
  `cost_tracker`, so they live in `backend/ai/trading_agents/`.
- Pure logic (context building, settings, caps, estimates, cost aggregation, the run choke)
  lives in `backend/services/trading_agents/`.
- The worker script imports only stdlib + `tradingagents`. Nothing in ONW imports it. Add an
  import-linter `forbidden` contract both ways.

## 2. File map

New:
| Path | Purpose |
|---|---|
| `backend/core/feature_flags.py` (edit) | `Flag(name="trading_agents", title="TradingAgents", default=False, owner="owner", added=date(2026,11,19), remove_by=date(2027,5,19))` |
| `backend/db/alembic/versions/2026111912xx_trading_agents_runs.py` | New table. `down_revision` = current head (re-check; last seen `202611181500_merge_paper_trading_and_mcp_oauth`) |
| `backend/db/tables.py` (edit) | `trading_agents_runs` table (§5) |
| `backend/db/trading_agents_run_store.py` | Data access only |
| `backend/services/trading_agents/__init__.py` | `availability()` — the ONE flag seam (§3) |
| `backend/services/trading_agents/settings.py` | Read/validate connector config, resolve deep/quick model refs (§4) |
| `backend/services/trading_agents/install.py` | Create/verify/remove the venv (§7) |
| `backend/services/trading_agents/context.py` | Build the portfolio context (§6) |
| `backend/services/trading_agents/runs.py` | `apply_run_change` — the write choke |
| `backend/services/trading_agents/budget.py` | Estimate, per-run cap, call cap, monthly cap |
| `backend/services/trading_agents/costs.py` | Aggregations for the cost panel |
| `backend/services/trading_agents/worker/ta_worker.py` | The subprocess entry (§7.3). Shipped as a data file in the wheel |
| `backend/ai/trading_agents/runner.py` | Queue, spawn, parse lines, enforce caps, cancel, timeout, boot recovery |
| `backend/ai/trading_agents/llm_bridge.py` | Per-run loopback OpenAI-compatible server → ONW providers (§4) |
| `backend/ai/trading_agents/openai_shape.py` | Pure translation OpenAI chat format ⇄ ONW provider messages/events (unit-tested without a server) |
| `backend/ai/tools/trading_agents.py` | Lucius tools (§8) |
| `backend/api/trading_agents.py` | REST (§9) |
| `frontend/src/components/intel/TradingAgentsCard.tsx` (+ `TradingAgentsReport.tsx`) | Asset page UI (§10.1) |
| `frontend/src/components/tabs/connectors/…` setup panel for `trading_agents` | §10.2 |
| `frontend/src/components/tabs/settings/sections/TradingAgentsCostPanel.tsx` | §10.3 |
| `frontend/src/types/tradingAgents.ts`, `frontend/src/hooks/useTradingAgents.ts` | Types + data hook via `useQueryCache` |

Edited (representative): `services/connector_catalog.py`, `services/connector_copy.py`,
`frontend/src/components/tabs/connectors/catalog.ts` + `services/connector_directory.json`
(`npm run sync:connector-directory`), `services/cost.py`, `api/cost.py`,
`components/intel/AssetDetailView.tsx`, `sections/AiUsageSection.tsx`, `ai/tools/__init__.py`,
`api/demo_lockdown.py`, `main.py` (router + boot recovery), `pyproject.toml` (import-linter +
wheel data file), `CLAUDE.md` (choke row + connector line), docs (§12).

## 3. Toggle: flag + connector

- **Flag** `trading_agents` (release kind). Off ⇒ old code untouched: no catalog entry shown
  (catalog entry carries `flag: "trading_agents"`, precedent `voice` / `voice_calls`), tools
  hidden and refused, API returns typed `FeatureDisabledError` (from `core/errors.py`; add if
  missing). Routers never branch on the flag — the service raises.
- **One seam:** `services/trading_agents.availability() -> Availability` returns one of
  `flag_off | connector_off | connector_paused | not_installed | installing | model_unavailable | ready`
  plus a human message. Every entry point (API, tools via `unavailable=`, runner) calls it.
- **Connector kind** in `services/connector_catalog.py` (`ConnectorKind`, see `coingecko` at
  ~:713 for an optional-key pattern):
  - `kind="trading_agents"`, `label="TradingAgents"`, `pausable=True`, `multi=False`.
  - `secret_fields`: `alpha_vantage_api_key` (optional), `fred_api_key` (optional). Revealable.
    These are **data-vendor** keys only. There are no LLM keys here — LLM goes through ONW (§4).
  - `config_keys` (non-secret; nothing credential-shaped, the config is in sanitized backups):
    | key | default | rule |
    |---|---|---|
    | `deep_model` | `null` (= ONW default model) | `"pk:model"` from any enabled ONW provider, CLI included |
    | `quick_model` | `null` (= ONW `model_for_task("intel")` if set, else default) | same |
    | `analysts` | `["market","social","news","fundamentals"]` | non-empty subset |
    | `max_debate_rounds` | 1 | 1–3 |
    | `max_risk_rounds` | 1 | 1–3 |
    | `max_tool_rounds` | 8 | 2–20 (TA default 20; lower = cheaper) |
    | `max_llm_calls` | 150 | 20–600. Hard stop per run. Protects subscription quota on CLI transports, where $ cost is plan-included |
    | `bridge_concurrency` | 4 (API transports) / 2 (CLI transports) | 1–8. Parallel LLM calls per run |
    | `per_run_cap_usd` | 2.00 | 0.10–50. Counts metered cost only |
    | `monthly_cap_usd` | 20.00 | 0–1000; 0 = no metered runs |
    | `context_level` | `"investments"` | `investments | position | ticker` (§6). Per-run override allowed |
    | `output_language` | `"English"` | free text ≤ 32 chars |
    | `sec_edgar_user_agent` | `"OpenNetWorth TradingAgents"` | user may set own contact. Never auto-fill from profile/email |
    | `disclaimer_accepted_at` | null | must be set before first run |
  - `state` (app-written): `install_status` (`absent|installing|ready|failed`),
    `installed_version` (pinned commit), `install_error`, `last_run_at`.
  - `tools`: the 4 tools of §8. `remove` hook: delete the venv + TA scratch dirs; keep run rows
    (user deletes them from the UI).
- **Catalog sync:** add the native entry to `catalog.ts`, run `npm run sync:connector-directory`,
  commit the JSON (vitest `directorySnapshot.test.ts` fails on drift).
- Setup is human-only (`connectors.open_setup`). Lucius may point to it, never configure it.

## 4. LLM — unified through ONW (the LLM bridge)

**Principle.** TradingAgents never holds a key and never calls an LLM vendor. ONW resolves the
model exactly as it does for Lucius and background jobs, and makes every call through its own
`AIProvider`. One cost path, one provider config, one place to disable a provider.

### 4.1 Model resolution (`services/trading_agents/settings.resolve_models(cfg)`)
1. `deep_ref = cfg.deep_model or ModelConfigStore().resolved()` (ONW default).
   `quick_ref = cfg.quick_model or ModelConfigStore().model_for_task("intel") or deep_ref`.
2. Validate each with `ModelConfigStore.resolve_override("pk:model")`. A disabled provider raises
   `ProviderDisabledError` ⇒ `availability = model_unavailable`. Never silently reroute (same
   rule as `model_for_task`'s docstring).
3. Return `{deep: "pk:model", quick: "pk:model", transports: {...}}`. No keys in the result.

### 4.2 The bridge (`ai/trading_agents/llm_bridge.py`)
- **Lifetime:** started by the runner right before spawning the worker, stopped when the run
  ends. Bound to `127.0.0.1` on a random free port (port 0). A minimal FastAPI/Starlette app
  served by an in-process `uvicorn.Server` task — separate from the main app, so gateway
  middleware, cookie auth and demo routing don't apply and the main port is never exposed to
  the worker.
- **Auth:** per-run bearer token (`secrets.token_urlsafe(32)`), compared with
  `hmac.compare_digest`. Wrong/missing token ⇒ 401. Requests from a non-loopback peer ⇒ 403.
  Token dies with the run.
- **Endpoints:** `POST /v1/chat/completions` (non-streaming; `stream:true` ⇒ also supported as SSE
  chunks if the pinned TA version streams — verify), `GET /v1/models` (returns the two aliases).
- **Model aliases:** the worker sees only `onw-deep` and `onw-quick`. The bridge maps them to the
  resolved `pk:model`. Any other model name ⇒ 400. The worker can't pick a model ONW didn't
  approve.
- **Translation (`openai_shape.py`, pure functions):**
  - OpenAI `messages` (system/user/assistant with `tool_calls`/tool) → ONW `messages` +
    `system` string (same shapes `assistant.py` builds for `stream_chat`).
  - OpenAI `tools` (function schemas) → ONW tool dicts (`name`, `description`, `input_schema`).
  - `response_format` `json_schema` (LangChain `with_structured_output`) → a forced single tool
    named after the schema; the tool input is returned as the message `content` JSON string.
    `json_object` ⇒ same with a permissive schema.
  - ONW events → OpenAI response: `text_delta` ⇒ `message.content`; each `tool_use` ⇒
    `message.tool_calls[{id, type:"function", function:{name, arguments: json}}]`,
    `finish_reason="tool_calls"`; else `"stop"`. Response `id` = `onwcall_<12hex>`.
    `usage` = prompt/completion tokens from the provider's usage event; the provider's usage event
    may arrive AFTER its tool calls (Claude Code, Antigravity), so the stream is read to `done`
    and `usage` is final only on that terminal event.
- **Call:** `provider = get_provider(pk:model)`; consume `provider.stream_chat(messages, tools,
  system, model, effort)` until `done` or the first batch of `tool_use` events, then **close the
  stream without running any tool** (TradingAgents runs its own data tools inside its graph).
  Each request is stateless: the worker re-sends the transcript, as TA does with any OpenAI
  endpoint.
- **CLI transports (Claude Code / Codex / Antigravity):** go through the same `stream_chat`.
  The tool schemas TA sends are passed as normal tools; the provider's own tool protocol
  (native dynamic tools for Codex, `claude_cli_tools` / `cli_tool_protocol` text fallback)
  surfaces them as `tool_use` events. The bridge closes the turn after the call is captured
  (a fresh CLI turn per request — never the Lucius "turn stays open" path, so ADR
  `2026-10-05-codex-turns-stay-open-across-tool-rounds` is not touched). WP2 must prove this
  per CLI with a recorded test (see §15). If a CLI transport can't return a tool call cleanly,
  `availability` reports `model_unavailable` for that model with the reason, instead of
  falling back.
- **Cost:** after each call, `cost_tracker.record(source="trading_agents",
  surface="trading_agents", session_id=run_id, model, provider=provider_label_for(pk),
  input_tokens, output_tokens, cache_*, cost_usd, cost_basis)` — the same fields
  `assistant._record_cost` writes. `cost_basis="plan_included"` for CLI subscription transports
  (`claude_cli_provider.cost_basis_for`). If `stream_chat` already records cost for a transport,
  pass the run's source/surface through instead of recording twice (check each provider; one
  record per call, tested).
- **Run accounting:** each call also returns to the runner (in-process callback, not via the
  worker) `{call_id, alias, pk_model, tokens, cost_usd, cost_basis, latency_ms}`. The runner
  applies caps (§7.4).
- **Concurrency:** an `asyncio.Semaphore(bridge_concurrency)` per run. CLI providers that
  serialize internally still work; they are just slower.
- **Errors:** provider errors map to OpenAI-shape error JSON with the right status (429 rate
  limit, 400 bad request, 502 upstream). Messages are sanitized (no keys, no URLs with creds).
  TA's `llm_max_retries` is set to 1; ONW providers do their own retry.
- **Logging:** never log message bodies (they carry portfolio amounts). Log call id, alias,
  tokens, latency, status.

## 5. Data model — `trading_agents_runs`

Columns (all additive; TIMESTAMP-named Alembic revision):
| column | type | notes |
|---|---|---|
| `id` | text pk | `tar_<12hex>` |
| `ticker` | text | upper-case, validated `^[A-Z0-9.\-^=]{1,15}$` |
| `instrument_id` | text null | when ONW knows the instrument |
| `trade_date` | date | ≤ `user_today()` |
| `status` | text | `queued|running|done|failed|cancelled|stopped_budget|stopped_calls|timed_out|interrupted` |
| `requested_by` | text | `user|lucius|automation` |
| `session_id` | text null | Lucius session when from a tool |
| `analysts` | json | |
| `config_snapshot` | json | deep/quick `pk:model`, transports, rounds, vendors, TA commit. No keys, no tokens |
| `context_level` | text | `investments|position|ticker` |
| `context_sent` | json | exact `PortfolioContext` + ticker/date sent (audit, shown in UI) |
| `progress` | json | `[{stage, status, at}]` |
| `signal` | text null | `Buy|Overweight|Hold|Underweight|Sell|REVIEW` |
| `decision_text` | text null | `final_trade_decision` |
| `reports` | json null | §7.3 subset, each field capped at 40 000 chars with `truncated` flag |
| `llm_calls` | int | |
| `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens` | int | |
| `cost_usd` | numeric | metered cost (priced, not plan-included) |
| `plan_included_calls` | int | calls on CLI subscriptions |
| `unpriced_calls` | int | metered calls whose model has no price |
| `cost_by_role` | json | `{role: {calls, input_tokens, output_tokens, cost_usd, plan_included_calls}}` |
| `cost_by_model` | json | same shape keyed by `pk:model` |
| `estimate_usd` | numeric null | shown before run |
| `cap_usd` | numeric | per-run cap in force |
| `error` | text null | sanitized, ≤ 2 000 chars |
| `created_at`, `started_at`, `finished_at` | ts (UTC) | |

Index `(ticker, created_at)`, `(status)`, `(created_at)`.

**Write choke:** `services/trading_agents/runs.apply_run_change(op, **kw)` with ops
`create | start | progress | usage | finish | fail | cancel | delete`. Each op is ONE DB
transaction (`db.engine.locked_unit_of_work`). Status transitions are validated (terminal
states are final). Publishes `core.data_events.publish({"trading_agents"})` so open screens
refresh (`useQueryCache`). Add the row to the CLAUDE.md choke table. This table is not money —
never part of net worth, budget or the ledger. In normal backups (no secrets). Not in
`SECRET_TABLES`; Lucius may read it via `run_sql_query`.

## 6. Portfolio context — real investing amounts

Goal: TradingAgents' trader, risk team and portfolio manager size advice against the real
book: "you hold 120 shares at 84.10, it is 18 % of your investments, you have 9 400 to deploy".

`services/trading_agents/context.build(ticker, level) -> (PortfolioContext dict, disclosure)`:

| level | Sent |
|---|---|
| `investments` (default) | Real `cash` + all investment positions (below) |
| `position` | Real `cash` + only the analyzed ticker's position (if held) |
| `ticker` | `portfolio=None` — ticker + date only |

**What `investments` contains (all real amounts):**
- `currency`: the analyzed ticker's quote currency.
- `cash`: **investable cash** = cash on hand (the single `Σ cash` function) + settlement cash of
  `investment` accounts, converted to `currency` with `core.fx` current rates. Excludes
  retirement accounts, credit, loans, fixed deposits, receivables. Budget money already assigned
  to envelopes is **not** subtracted (TA can't reason about it; the UI says so).
- `positions`: every live, vested position in `investment` accounts, aggregated by ticker across
  accounts: `{ticker, quantity, average_price}` — `average_price` = quantity-weighted cost per
  share in the ticker's own quote currency. Read from `investments_book` (the one authority);
  never recompute. Unvested equity excluded. Retirement holdings excluded by default.
  Positions without a ticker (owned assets, untracked brokerage totals) are excluded.
- If the pinned `PortfolioContext` has a single `currency`, positions in other currencies get
  `average_price` converted at current FX and are listed in `disclosure.fx_converted` (shown in
  the UI). WP1 confirms the exact schema on the pinned commit and adapts.

**Never sent, at any level:** account names, institutions, account numbers, transactions,
payees, income, budget/envelopes, net worth totals, liabilities, retirement balances, owned
assets, thesis text, memory, profile fields, location.

**Audit + consent:**
- The exact dict is stored in `context_sent` and shown in the UI ("What was shared").
- `/api/trading-agents/context-preview` and the Lucius Ask card show it **before** the run.
- The setup page names who receives it: the chosen ONW model provider (via the bridge) and
  TA's data vendors (yfinance, SEC EDGAR, StockTwits, Reddit, Polymarket, optional Alpha
  Vantage/FRED). Portfolio amounts go only to the LLM, never to data vendors (TA data tools take
  ticker/date only — WP1 verifies on the pinned commit).
- Thesis: ONW judges TA's rating against the owner's thesis **after** the run, via
  `services/thesis_judgment` (the one authority). The thesis text never goes to TA.

Tests: each level sends exactly its fields; values equal the book (FX-converted where flagged);
property test over random books that no never-sent field (account name, institution, payee,
transaction id, retirement balance) appears anywhere in `context_sent`.

## 7. Install + worker + runner

### 7.1 Paths
Reuse the existing data-dir helper in `core/`:
`<DATA_ROOT>/integrations/trading_agents/{venv,cache,runs/<run id>/{home,results,memory}}`; the
shared `cache` is the only folder two runs share, and a run's folder is deleted when the run ends or
its row is deleted. Honors `ONW_ROOT`.
Windows: `venv\Scripts\python.exe`.

### 7.2 Install (`services/trading_agents/install.py`)
- Pinned source: `TA_PIN = "git+https://github.com/TauricResearch/TradingAgents@<commit-sha>"`
  in one constant. Bump = code review + release note. Pick the sha at build time; record it in
  the ADR.
- Steps (user-triggered only, from the connector page): `uv venv <venv> --python <sys.executable>`
  (fallback `python -m venv`), then `uv pip install --python <venv-python> <TA_PIN>`
  (fallback venv pip). Run via `core/process_tree.py` with a 15 min timeout that kills the process
  tree. Stream log tail into connector `state.install_error` on failure.
- No LLM vendor SDK extras are needed beyond what TA's `openai_compatible` path uses.
- Verify: run `<venv-python> -I ta_worker.py --selftest` → prints `{"ok":true,"ta_version":…}`.
- Refused in `DEMO_MODE` and `ONW_QA_MODE` (tests inject a fake worker, §11).
- Uninstall = delete the dirs (connector `remove` hook or "Reinstall" button).

### 7.3 Worker protocol (`ta_worker.py`, stdlib + tradingagents only)
- Started as `<venv-python> -I <path>/ta_worker.py` with cwd = the TA scratch dir.
- **Env:** `core.child_env.scrubbed_environ()` + only: `OPENAI_COMPATIBLE_API_KEY=<bridge run
  token>`, `TRADINGAGENTS_RESULTS_DIR`, `TRADINGAGENTS_CACHE_DIR`,
  `TRADINGAGENTS_MEMORY_LOG_PATH`, `TRADINGAGENTS_NO_UPDATE_CHECK=1`, `SEC_EDGAR_USER_AGENT`,
  optional `ALPHA_VANTAGE_API_KEY`, `FRED_API_KEY`. **No LLM vendor key, ever.**
- **stdin:** one JSON object:
  `{"run_id","ticker","trade_date","analysts","portfolio"|null,"config":{llm_provider:"openai_compatible",
  backend_url:"http://127.0.0.1:<port>/v1", deep_think_llm:"onw-deep", quick_think_llm:"onw-quick",
  llm_max_retries:1, max_debate_rounds, max_risk_discuss_rounds, max_tool_rounds, output_language,
  data_vendors}}`. The worker deep-copies `DEFAULT_CONFIG` (nested dicts are shared otherwise)
  and overlays.
- **stdout:** one JSON object per line, nothing else (TA logging goes to stderr, drained into a
  bounded tail like `StderrTail`):
  - `{"type":"progress","stage":"<langgraph node>","status":"started|finished","at":iso}`
  - `{"type":"llm_call","node":"<langgraph_node>","call_id":"onwcall_…"}` — from a LangChain
    `BaseCallbackHandler.on_llm_end`: node from `metadata["langgraph_node"]`, `call_id` from the
    response id. This only attributes a bridge call to a role. Tokens and cost come from the
    bridge, never from the worker (the worker is untrusted for accounting).
  - `{"type":"result","signal":"…","final_state":{company_of_interest, trade_date, market_report,
    sentiment_report, news_report, fundamentals_report, investment_plan, trader_investment_plan,
    final_trade_decision, investment_debate_state:{bull_history,bear_history,judge_decision?},
    risk_debate_state:{aggressive_history,conservative_history,neutral_history,judge_decision?}}}`
  - `{"type":"error","kind":"config|llm|data|internal","message":"…"}` then exit 1.
- Call: `TradingAgentsGraph(selected_analysts=…, debug=False, config=cfg, callbacks=[h]).propagate(ticker, trade_date, asset_type="stock", portfolio=PortfolioContext.model_validate(p) if p else None)`.
- Do not call `save_reports` (ONW stores the subset). TA still writes its own
  `full_states_log` + memory log into the scratch dirs — acceptable, inside the data dir. These
  contain portfolio amounts; they stay under the data dir and are deleted by the `remove` hook.

### 7.4 Runner (`ai/trading_agents/runner.py`)
- `submit(request) -> run_id`: checks `availability()`, disclaimer, monthly cap, then
  `apply_run_change("create")` and enqueues. **One run at a time**, queue max 3 (4th ⇒ typed
  `TooManyRunsError`). Runs on the server event loop via a tracked task (like
  `services/automations/runs.py` `_tasks_by_run`), never an untracked `asyncio.create_task`.
- Per run: start the bridge (§4.2), then spawn the worker with `core/process_tree.py`; read stdout
  lines async.
  - `progress` ⇒ `apply_run_change("progress")`.
  - Bridge call callback ⇒ `apply_run_change("usage")` (totals, `cost_by_model`;
    `cost_by_role` once the matching `llm_call` line arrives, else `other` at finish). Unknown
    model price ⇒ `unpriced_calls += 1`. CLI subscription ⇒ `plan_included_calls += 1`.
  - Caps, checked after every bridge call: metered `cost_usd > cap_usd` ⇒ kill tree,
    `stopped_budget`. `llm_calls > max_llm_calls` ⇒ kill tree, `stopped_calls`. The bridge
    refuses further calls for the run once a cap trips.
  - `result` ⇒ cap each report field, `apply_run_change("finish")`.
- Wall-clock timeout 30 min (CLI transports are slower; constant, not a setting) ⇒ kill tree,
  `timed_out`.
- `cancel(run_id)` ⇒ kill tree, stop bridge, `cancelled`. Queued run ⇒ removed, `cancelled`.
- Boot: `recover()` marks `queued|running` rows `interrupted` (call from `main.py` lifespan).
- Skipped when `ONW_QA_MODE` unless a test worker is injected (`runner.set_worker_cmd_for_tests`).
- On finish: optional app notification "TradingAgents finished ACME: Hold" through the existing
  notify path (reuse; no new channel).

### 7.5 Node → role map (for the cost breakdown)
`Market Analyst→analyst_market`, `Social Analyst→analyst_sentiment`, `News Analyst→analyst_news`,
`Fundamentals Analyst→analyst_fundamentals`, tool nodes → the owning analyst,
`Bull Researcher|Bear Researcher→research_debate`, `Research Manager→research_manager`,
`Trader→trader`, `Aggressive|Conservative|Neutral Analyst→risk_debate`,
`Portfolio Manager|Risk Judge→portfolio_manager`, memory/reflection nodes → `memory`,
anything else → `other`. Read exact node names from the pinned commit's `graph/setup.py` and
keep the map in one dict with a test that fails on an unmapped node seen in a fixture.

## 8. Lucius tools (`backend/ai/tools/trading_agents.py`)

Follow `.claude/skills/add-tool/SKILL.md` and `docs/ai/TOOL_CONVENTIONS.md` (Pydantic `schema_of`,
no top-level `oneOf`, names final). All carry `connector="trading_agents"` and
`unavailable=_unavailable` (wraps `availability()`).

| Tool | Class | Input | Behaviour |
|---|---|---|---|
| `start_trading_agents_analysis` | `external_effect=True`, `mutates=("trading_agents",)` → seeds **Ask** | `ticker`, `trade_date?`, `analysts?`, `context_level?` | `precheck`: availability, disclaimer, monthly cap, queue. Card shows ticker, deep/quick models + transport, estimate (or "plan-included" for CLI), caps, month spend/cap, and the exact portfolio context that will be shared. Returns `{run_id, status:"queued", estimate_usd, eta_minutes}` at once. |
| `get_trading_agents_run` | read-only | `run_id` | Status, progress, signal, decision text, reports (each capped to 6 000 chars in the tool result, `truncated:true` + `full_chars`), cost, `context_sent`, and `thesis_judgment` (stance vs the owner's thesis, if one exists) |
| `list_trading_agents_runs` | read-only | `ticker?`, `status?`, `limit≤20` | Bounded via `ai/tools/_bounded.cap`; returns true total |
| `cancel_trading_agents_run` | `mutates=("trading_agents",)` | `run_id` | Cancel queued/running |

- Descriptions say: research only, not financial advice; costs money (or subscription quota)
  and takes minutes; only start when the user asks or an Automation was set up for it; never
  treat the rating as an order (no order placement exists); judge it against the thesis via the
  returned `thesis_judgment`.
- Lucius flow: start → tell the user it is running → user (or Lucius on a later turn) calls
  `get_trading_agents_run`. No blocking wait inside a turn.
- Automations: works if the owner puts `start_trading_agents_analysis` on the Automation's
  Always-allowed list (permissions_v2). Cost still capped by §7.4.
- Register module in `ai/tools/__init__.py`; update `tests/test_char_tool_dispatch.py`
  snapshot and `tests/test_tool_domains.py`; add tools to the CLAUDE.md tool list per the skill.
- Add `"trading_agents"` as a data-event domain if domains are enumerated.

## 9. REST API (`backend/api/trading_agents.py`)

Thin router: validate → one service/runner call → shape. All behind `require_auth`.
| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/api/trading-agents/status` | — | `availability`, install state/version, deep/quick model + transport, caps, month spend, queue length, disclaimer accepted |
| GET | `/api/trading-agents/config` | — | the §3 settings (model refs as saved, `null` = default) + `has_alpha_vantage_api_key` / `has_fred_api_key` (presence only, never a value) |
| PUT | `/api/trading-agents/config` | any §3 setting + `alpha_vantage_api_key`, `fred_api_key` | the new config; `null` resets a setting / clears a key, blank keeps a key; `ValidationError` names `context.field` |
| POST | `/api/trading-agents/install` | — | 202; progress via status + data events |
| POST | `/api/trading-agents/disclaimer` | `{accepted:true}` | sets `disclaimer_accepted_at` |
| POST | `/api/trading-agents/estimate` | `{ticker, analysts?}` | `{estimate_usd, low_usd, high_usd, calls, basis:"history|static", sample_size, plan_included:bool, priced:bool, unpriced_models:[ref]}` |
| GET | `/api/trading-agents/context-preview` | `ticker, context_level` | the exact dict that would be sent + `disclosure` |
| POST | `/api/trading-agents/runs` | `{ticker, trade_date?, analysts?, context_level?}` | 202 `{run_id}` |
| GET | `/api/trading-agents/runs` | `ticker?, status?, limit, offset` | page + total |
| GET | `/api/trading-agents/runs/{id}` | — | full run (reports uncapped) + `thesis_judgment` (absent without a thesis) |
| POST | `/api/trading-agents/runs/{id}/cancel` | — | run |
| DELETE | `/api/trading-agents/runs/{id}` | — | 204 (terminal runs only) |
| GET | `/api/cost/trading-agents` | `days`, `range`, `start`/`end` (win over both), `tz` | §10.3 aggregation |

- The LLM bridge is **not** a route of the main app (§4.2).
- `api/demo_lockdown.py`: every mutating route above is **refused** in demo; read routes allowed.
  `tests/test_demo_route_classification.py` must pass.
- Errors: typed exceptions from `core/errors.py` (`FeatureDisabledError`, `BudgetExceededError`,
  `TooManyRunsError`, `ModelUnavailableError`, `NotInstalledError`) mapped through the existing
  error handler.

## 10. Frontend

Read through `useQueryCache` (revalidates on the `trading_agents` data event). Gate everything with
`useFlag('trading_agents')` + status `availability`. Every new `className` defined in
`frontend/src/index.css` (`npm run lint:classes`). Use `ui/` design-system primitives. Run the
`ui-designer` agent first for a wireframe of 10.1–10.3.

### 10.1 Asset page — `AssetDetailView.tsx` Analysis tab
New `TradingAgentsCard` placed after `KeyIssues`, before `AnalystConsensus` (~:1143–1204).
Separate from "Lucius's take" (`PositionVerdictLine`) — different source, labelled
"TradingAgents · research only".
States:
- Not enabled → one line + link to the connector page (only when the flag is on).
- Ready, no runs → "Run multi-agent analysis" button; analyst checkboxes (defaults from
  config); context level selector (Investments / This position / Ticker only); estimate (or
  "Uses your <CLI> plan") + cap + month spend; "What will be shared" expander from
  `/context-preview`.
- Running → stage list (analysts → debate → trader → risk → PM) with ticks, elapsed time,
  calls and cost so far, Cancel.
- Done → rating badge (5-tier colors; `REVIEW` = neutral "Unclear"), thesis stance chip if a
  thesis exists, decision text, accordion: Market / Sentiment / News / Fundamentals /
  Bull vs Bear / Trader plan / Risk debate / Portfolio manager. Footer: models, run cost
  (+ plan-included calls), tokens, date, "What was shared". Disclaimer line always visible.
- Failed/stopped → reason + Retry.
- History: last 5 runs for this ticker (signal, date, cost), link to more.

### 10.2 Connector setup page
Sections: Disclaimer (must accept once) · Install (button, status, version, Reinstall, log tail on
failure) · Models (deep / quick pickers listing every enabled ONW provider model, CLI included,
with "uses your subscription" hint for CLI; default = ONW default) · Analysts + rounds + tool
rounds + max calls · Budget (per-run cap, monthly cap, month spend bar) · Data sources
(yfinance/SEC EDGAR default, optional Alpha Vantage + FRED keys, SEC user agent) · Portfolio
context (default level + plain explainer of §6 and who receives it) · Permissions (standard
per-connector permission list).

### 10.3 Cost tracker — Settings → Cost & Performance
- `services/cost.py`: `_source_to_bucket` maps `trading_agents` → new bucket `trading_agents`
  (label "TradingAgents"). It then shows in "By surface" automatically; By provider / By model
  already include its rows. Add a unit test for the bucket.
- New `TradingAgentsCostPanel` in `AiUsageSection.tsx`, shown when the flag is on and (connector
  enabled or spend/calls > 0 in period). Data: `GET /api/cost/trading-agents` from
  `services/trading_agents/costs.py` (reads run rows — permanent; `cost_log` raw rows roll up after
  15 days and drop `session_id`):
  - Totals: metered spend, runs (done / failed / stopped), avg and median cost per run, LLM calls,
    tokens, plan-included calls (CLI subscription usage, shown as calls + tokens, not $),
    unpriced calls (warning when > 0: "N calls had no price; real spend is higher").
  - Month spend vs monthly cap bar; per-run cap and max-calls values.
  - By role (bars from `cost_by_role`), by model/transport, by ticker (top 10), by day (sparkline).
  - Recent runs table: date, ticker, signal, status, calls, tokens, cost, estimate vs actual.
- Shared period picker with the rest of the section.

### 10.4 Budget logic (`services/trading_agents/budget.py`)
- **Estimate:** median and p10–p90 of the last 10 finished runs with the same models, analysts
  count and rounds. Fewer than 3 ⇒ static heuristic: `calls ≈ analysts×(max_tool_rounds+1) +
  2×debate_rounds + 1 + 1 + 3×risk_rounds + 1`, × assumed tokens (in 6 000 / out 800 quick,
  in 12 000 / out 1 500 deep) × pricing. Mark `basis:"static"`. CLI transports ⇒
  `estimate_usd=0`, `plan_included=true`, and an estimated call count — but only for a CLI with
  NO stored API key (a keyed CLI is billed, so it is metered); an unknown transport is metered.
  A metered model with no known price (no table price, none reported, not a local endpoint) is
  refused by name at start: it cannot be held to a cap.
- **Per-run cap / call cap:** enforced live in the runner (§7.4).
- **Monthly cap:** month = `core.user_time` month. Refuse `create` when
  `month_committed + estimate.median > monthly_cap_usd` (`BudgetExceededError`, message with
  numbers), where `month_committed` is finished runs' actual cost plus, for queued and running
  runs, the larger of their cost so far and their estimate. A cap of 0 refuses every run that is
  not entirely on subscriptions. What is left of the month when a run starts is also a live limit
  in the bridge. Plan-included runs are not blocked by the $ cap.
- Global `AppConfigStore` AI budget stays unenforced (out of scope; do not change).

## 11. Tests (focused; use `write-test` skill; synthetic data only, ticker ACME)

- `tests/fixtures/trading_agents/fake_worker.py`: a scripted worker that calls the bridge with
  canned OpenAI requests (plain text, tool call, structured output) and emits JSON lines
  (normal, crash, hang, over-cap). Runner tests use it with a **fake ONW provider** — no
  network, no venv.
- Bridge: `openai_shape` round-trips (messages, tool_calls, tool results, `response_format`);
  alias mapping and unknown-model 400; token auth 401; non-loopback 403; one cost record per
  call with `source="trading_agents"`, `session_id=run_id`; `cost_basis="plan_included"` for a
  CLI provider; refuses after a cap trips; never logs bodies.
- CLI transports: a recorded-fixture test per CLI provider (Claude Code, Codex, Antigravity)
  proving `stream_chat` with TA-style tools yields `tool_use` and the bridge closes the turn.
- Runner: progress/role attribution; per-run cap ⇒ `stopped_budget` + process killed; call cap ⇒
  `stopped_calls`; timeout ⇒ `timed_out`; cancel; queue limit; boot recovery ⇒ `interrupted`.
- Worker env: contains no LLM vendor key (assert on the spawn env).
- Context: each level's fields; real values equal the book; FX conversion flagged; never-sent
  property test; retirement + unvested + owned assets excluded.
- Settings: model resolution, disabled provider ⇒ `model_unavailable`, no reroute.
- Budget: estimate basis switch; plan-included estimate; monthly refusal.
- Choke: status transitions; data event published.
- Flag: `@pytest.mark.flag_both` on availability, API and tools (off ⇒ untouched/refused).
- Tools: dispatch snapshot, tool domains, Ask seed for start tool, result bounding.
- API: route classification in demo, error shapes.
- Cost: `_source_to_bucket` bucket; `/api/cost/trading-agents` aggregation incl. plan-included.
- Frontend vitest: card states, cost panel, `describe.each` flag on/off, connector directory
  drift test.
- Import-linter: new contracts pass (`make imports`).
- Opt-in live smoke `make ta-smoke` (never in `make check`): installs into a scratch `ONW_ROOT`,
  runs one analysis on a liquid ticker with 1 analyst, 1 round, once per configured transport
  the owner selects (API key and/or CLI).

## 12. Docs + records (same commits as the behaviour)

- `docs/specs/trading-agents.md` (this spec).
- ADR `docs/adr/2026-11-19-tradingagents-is-a-plugin-that-calls-llms-only-through-onw.md`
  (worker isolation, pinned commit, the LLM bridge and why no key reaches the worker, CLI
  support, real-amount context levels, enforced caps).
- `docs/product/` page for the feature + connector page; `docs/docs-map.json` mapping.
- `docs/DATA_MODEL.md` (`trading_agents_runs`), `docs/ai/` tool list, CLAUDE.md choke row +
  one Connectors bullet, `docs/FEATURE_FLAGS.md` if it lists flags.
- Third-party notice: TradingAgents is Apache-2.0, installed at runtime, not vendored.
- §16 (the take): ADR amendment to the TA ADR, `docs/product/features/trading-agents.md` (how the
  take uses a run, staleness, what is sent), the position-verdict / asset-page product page, and
  one CLAUDE.md line only if a new invariant or seam lands.

## 13. Build order (work packages for agents)

| WP | Scope | Depends | Agent |
|---|---|---|---|
| WP1 | Flag, connector kind + copy, `availability()`, model resolution, migration + store + choke, context builder (confirm `PortfolioContext` schema on pinned commit) | — | senior-engineer |
| WP2 | LLM bridge + `openai_shape` + per-transport tests (CLI included) | WP1 | senior-engineer |
| WP3 | Install module, `ta_worker.py`, runner, node→role map, fake worker, runner tests | WP1, WP2 | senior-engineer |
| WP4 | Budget + costs services, `services/cost.py` bucket, `/api/cost/trading-agents` | WP1 | junior/senior |
| WP5 | Lucius tools + snapshot/domain tests | WP1, WP3 | senior-engineer (`add-tool` skill) |
| WP6 | REST router, demo lockdown, `main.py` wiring + boot recovery | WP1–WP4 | junior-engineer |
| WP7 | ui-designer wireframe → TradingAgentsCard, connector setup page, cost panel, catalog sync | WP6 | ui-designer then senior-engineer |
| WP8 | Docs, ADR, CLAUDE.md, product pages | all | junior-engineer |
| WP9 | Take integration (§16): `take_input` digest, analyst prompt + rule, provenance on the brief, `trading_agents_input` in the asset detail, refresh on run finish, tests, ADR amendment, product docs | WP1–WP6 | senior-engineer |
| WP10 | Condensed card + take chip, frontend only (`docs/specs/trading-agents-ui.md` §10) | WP7, WP9 | senior-engineer |
| WP11 | One view (§17): vocabulary bridge, overlay rule, correction at `normalize`, leaner digest and rule, `trading_agents_input` view/held/allowed_actions, tests, docs | WP9 | senior-engineer |
| Gate | `security-reviewer` (bridge auth + loopback, subprocess, third-party code, portfolio data in prompts and logs, external I/O) + `code-reviewer`; `make check` before merge | all | — |

## 14. Verification (end to end)

1. `make check` green (lint, imports, types, tests, build).
2. `pytest tests -k trading_agents --flag trading_agents=on` and `=off` both green.
3. Scratch instance: `ONW_ROOT=<scratch> ONW_QA_MODE=1` + fake worker + fake provider on ports
   8090–8099 (`make qa-e2e`): enable flag in Settings → Developer → Experimental, enable
   connector, accept disclaimer, check "What will be shared" shows real amounts for ACME, run it
   from the asset Analysis tab, watch stages, see result, cancel a second run, confirm Cost &
   Performance shows the TradingAgents bucket and panel with role/model breakdown, and that an
   over-cap run stops with `stopped_budget`.
4. Ask Lucius "run TradingAgents on ACME" → Ask card with models, estimate and shared context →
   approve → `get_trading_agents_run` returns result + thesis stance.
5. Flag off ⇒ no connector entry, no card, tools absent, API refuses.
6. Real runs (owner, optional): `make ta-smoke` with an API-key model and with a CLI model;
   compare metered `cost_usd` with the provider dashboard (±5 %); confirm CLI run shows
   plan-included calls.

## 15. Risks / open items to settle during WP1–WP3

- Exact TA node names, `PortfolioContext` fields, whether TA streams to the OpenAI endpoint, and
  whether `run_rating` makes an extra LLM call: read from the pinned commit (the web fetch of
  `pyproject.toml` failed — confirm deps and `requires-python` ≥3.11 vs ONW's 3.12).
- CLI transports returning a tool call cleanly in a one-shot turn is the main technical risk.
  WP2 proves it per CLI. A CLI that fails is marked `model_unavailable` with the reason — no
  silent fallback to another provider.
- CLI runs are slower and use subscription quota. `max_llm_calls`, lower `bridge_concurrency`
  and the 30 min timeout bound them.
- Portfolio amounts are in prompts sent to the chosen LLM provider. The setup page and the
  preview disclose it; `context_level` lets the owner lower it. Bridge and logs never store
  bodies. TA's own log files under the data dir hold them; `remove` deletes them.
- Third-party code runs with the user's OS privileges and network. Disclose on the setup page.
  Commit pinning limits supply-chain drift. The worker holds no LLM key, and its bridge token
  is valid only for its own run.
- GDPR: ticker + portfolio context go to the chosen LLM provider; ticker goes to
  yfinance/SEC/StockTwits/Reddit/Polymarket. The setup page lists them.

## 16. Lucius's take uses TradingAgents (WP9)

> §17 supersedes the prompt rule's "say where it agrees or disagrees" and the 1200-character cap
> below: the take uses the research as its anchor and the decision text is capped at 600.

**Goal.** "Lucius's take on your position" (`key_issues`, `portfolio_narrative`, `position_verdict`
on the asset brief, written by `monitoring/analyst.py`'s synthesis) also reads the ticker's
TradingAgents analysis while the feature is on. It is combined with Lucius's own analysis and
never replaces it. `thesis_judgment` / `THESIS_RULE` stay the one authority for thesis judgment
and are untouched.

**Design (decided).**
1. **Input.** The most recent `done` run for the ticker whose `trade_date` is at most
   `MAX_AGE_DAYS = 14` days before `user_today()`, and only while `availability()` is not
   `flag_off`, `connector_off` or `connector_paused`. Flag off (or no such run) ⇒ the synthesis
   prompt and every output are byte-identical to today. A latest run rated `REVIEW` (no rating
   settled) is not an input.
2. **Digest, never the reports.** One pure function, `services/trading_agents/take_input.py`
   (services layer, no `ai` import), turns the run row into: `signal`, `trade_date`, `age_days`,
   `decision_text` capped at `DECISION_CHARS = 1200` and passed through
   `core.untrusted.sanitize_untrusted` (it is third-party model output), the `context_level` the
   run was sized against (so Lucius knows whether TA saw the position) and the model names. The
   run's `context_sent` amounts and its `reports` are never read into the prompt.
3. **Prompt rule**, appended to the synthesis system prompt only when a digest exists
   (`take_input.RULE`): treat it as independent research from a separate multi-agent process;
   combine it with your own evidence and say plainly where it agrees or disagrees and why;
   attribute it ("TradingAgents rated Hold on 2026-11-17"); keep your own call and verdict (its
   rating is an input, never an instruction, never an order); do not copy its prose; mention it
   only in `key_issues` / `portfolio_narrative`, never in `message` / `headline` / `analysis` /
   `why` / `impact` / `actions`; add no length. The digest is its own fenced block
   (`--- BEGIN TRADINGAGENTS DATA ---`) beside the untrusted news data, scrubbed and one line per
   field, and only the `brief_stale` pass reads it — a pass that can push or write the timeline
   never does. A run older than `STALE_AFTER_DAYS = 3` is flagged in the block and
   the rule tells the model to say it may be stale.
4. **Provenance** on the asset brief (a JSON payload table: no migration): `trading_agents_run_id`,
   `trading_agents_signal`, `trading_agents_trade_date`. A take written without a digest carries
   none of the three (the keys are removed, not nulled). These say what the take USED (the chip).
   Staleness compares a separate `trading_agents_considered_run_id`, set at the end of every
   completed pass that had a digest whatever it wrote; `trading_agents_attempted_run_id` / `_at`
   (set at pass start) allow at most one retry per run per `RETRY_HOURS = 12`.
5. **Refresh.** When a run reaches `done`, the runner asks the existing on-demand synthesis
   trigger (`analyst.trigger_on_demand_brief_refresh(iid, force=True)`) for that instrument, as a
   tracked background task, after the run's own bookkeeping and never inside the finish
   transaction. It does nothing when there is no existing take, when the brief already used this
   run, or when the digest would be empty. The trigger keeps its own guards (in flight, failure
   cooldown), so there is no second scheduler and no synchronous model call in the run path.
   Fallback if the trigger declined (a pass was already in flight): the brief is stale w.r.t. TA —
   `_key_issues_force_stale` is also true while the latest eligible run is not the one the brief
   used, so the next page open regenerates it.
6. **No auto-start.** The take only uses an existing run; it never starts one (a run costs money).
   Lucius does not suggest running TA in the take.
7. **Failure isolation.** Any error building the digest ⇒ the take is produced exactly as today
   (debug log, no raise).

**Data contract.** `GET /api/intel/assets/{id}` gains
`trading_agents_input: {run_id, signal, trade_date, age_days} | null` (`trade_date` ISO date,
`age_days` recomputed on every read; null with the flag off, before any run was used, or when the
take was written without one).

**Staleness.** Age uses `trade_date`. Past 14 days a run is not an input, the next take drops it
(provenance cleared) and the page-open staleness check regenerates the take once.

**Privacy.** The digest goes to the take's own model provider, the same provider that already
receives the position block. This is a new disclosure: the TradingAgents decision text (up to
1200 chars, sanitized) is now also read by the take's model. Stated in the ADR amendment and the
product page.

**Tests.** `tests/test_trading_agents_take.py` (synthetic, ACME): digest contents, caps and
sanitization; age window; no-run / old-run / failed-run / REVIEW; flag-off identity
(`flag_both`: prompt and written brief identical to a no-TA run); connector paused; the rule is in
the prompt only with a digest; provenance written, cleared and served; refresh requested exactly
once per finished run; error isolation; nothing from `context_sent` or `reports` in the prompt.

**Cost.** A finished run costs one background call on the take's provider, once per run, only
with an existing take. On-demand passes are `done` rows in `pending_analysis` and count toward the
sweep's rolling 24h cap (`_DAILY_ANALYSIS_CAP`); the on-demand path itself is not gated by it.

**Out of scope.** Starting a run from the take, TA in the Intel alert text, other take surfaces
(chat, briefing), the card and chip UI (WP10).

## 17. One view: the take and TradingAgents (WP11)

**Problem (owner, real example).** A held stock at 85% of the portfolio: the take said Hold, the
TradingAgents card said Sell, and the take's text said it was "disagreeing with that Hold". A stock
not held: the take said WAIT, TradingAgents said SELL. Two verdicts side by side read as two
analysts competing.

**Principle.** They fill each other's gaps. TradingAgents is the STOCK research (technicals,
fundamentals, sentiment, news, bull/bear debate). Lucius is the POSITION overlay (size,
concentration, thesis, levels, alerts, pending orders, the run's age, whether the run saw the
position). The take is ONE recommendation that uses the research; it is never written as a
disagreement. The rating is still an input, not an order: the take decides.

**Vocabulary bridge** (`services/trading_agents/vocabulary.py`, pure; `allowed_actions(signal, held)`
and `view(signal, held)`):

| Rating | Held: allowed | Held: reads as | Not held: allowed | Not held: reads as |
|---|---|---|---|---|
| Buy | add, hold | research is positive — adding is supported | buy, wait | positive outlook — a buy is supported |
| Overweight | add, hold | research leans positive — adding is supported | buy, wait | leans positive — a buy is supported |
| Hold | hold | research says keep the position as it is | wait | neutral outlook — no reason to buy now |
| Underweight | trim, hold | research leans negative — reducing is supported | wait | leans negative — not a buy now |
| Sell | sell, trim | research says exit or reduce | wait | negative outlook — not a buy now |

A Sell on a stock the user does not hold is never shown as an instruction to sell.

**Prompt.** With a digest the take anchors on the research and does not re-derive the stock view.
`take_input.RULE` (shorter than WP9's) says so, asks for ONE recommendation naming the research in
one clause, asks `verdict.action` to be an allowed action, and lets it differ ONLY for an overlay
reason: `concentration`, `thesis`, `levels`, `alerts` (Lucius's read of the position) or `stale_run`
(run older than 3 days), `position_unseen` (run sized at `ticker`) — facts the server checks. The
verdict carries `ta_overlay_reason` (one of those six; absent or `none` is no exception); `why`
starts with the reason.

**Correction (checkable, not only prompted).** `position_verdict.normalize(..., ta_signal=,
ta_facts=)`: an action outside `allowed_actions` with no valid reason is REPLACED by the nearest
allowed action along sell < trim < hold < add (wait < buy), `why` becomes a plain line from the
bridge (the model's was written for the action it lost), and `ta_aligned: true` is stored. Orders
that no longer fit the action drop through the existing side check; alerts, levels and the L1 buy
cap are unchanged. A kept exception stores `ta_overlay_reason`. Without a digest `normalize` is
byte-identical to before.

**Token budget (must not rise).** The digest block carries rating, one-line reading, allowed
actions, age, context level and a decision text capped at `DECISION_CHARS = 600` (was 1200); the
models line is gone from the block. With a digest the model writes `portfolio_narrative` in at most
2 sentences and `why` in 1. No extra model call. Measured on the worst-case fixture (decision at the
cap, stale): `RULE` 964 -> 928 chars; block 1613 -> 984 chars (about 246 tokens); added to the
prompt 2577 -> 1912 chars (about 644 -> 478 tokens). `key_issues` and the news evidence are NOT
trimmed: the page shows 3-5 issues and the model needs the evidence for them.

**Data contract.** `trading_agents_input` gains `view`, `held`, `allowed_actions`; `run_id`,
`signal`, `trade_date`, `age_days` are unchanged. `position_verdict` gains `ta_aligned` /
`ta_overlay_reason` only when set.

**What TradingAgents sees** (so the research is not mistaken for portfolio-blind). The default
`context_level` is `investments`; every start path (tool, REST, card form) uses the configured
level unless the caller names one. The worker passes `PortfolioContext` to `propagate`, which
renders it into prompts (per ONW's reading of the pinned commit, the trader, risk team and
portfolio manager; the analysts' data tools take ticker and date only).
`investments`: cash and every investment position (ticker, quantity, average price); `position`:
cash and the analysed ticker's own position; `ticker`: nothing (`None`, not a flat book). A held
ticker at `investments` or `position` is seen as held with its quantity; a not-held ticker at either
is seen with no position, and at `investments` beside the other holdings. Weights are not sent, so
TradingAgents sees the quantity and cash, not "85% of the portfolio" — the overlay reasons exist for
that gap.

**Tests.** `tests/test_trading_agents_take.py`: table completeness (5 ratings x held/not held), the
correction (replaced, kept with reason, run-fact reasons checked, no signal untouched, orders
dropped), the rule in the prompt and the old duplication gone, token growth bound, flag-off identity,
`served` carries `view` / `held`.

**Out of scope.** A second model pass to reconcile, changing the `context_level` default, trimming
`key_issues`.
