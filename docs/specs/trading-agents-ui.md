# UI SPEC — TradingAgents integration (Open Net Worth)

Status: build-ready design spec (2026-11-19). Companion to `docs/specs/trading-agents.md` §10 and §6.
Authority: ui-designer. Build agents implement this; they make no placement, layout or copy
decision of their own. Where this file and the backend differ, the backend JSON wins and the
mismatch is listed in section 0.2 (those are blocking backend gaps, not UI choices).

Scope: (a) asset-page card + report, (b) connector setup panel, (c) cost panel + bucket label,
(d) approval-card renderer for `trading_agents_run`. Nothing else in the app changes.

---

## 0. Verdicts

### 0.1 Placement

| Surface | Verdict | Why |
|---|---|---|
| Asset card | **Extend** `AssetDetailView.tsx`, Analysis tab. New `TradingAgentsCard`, rendered after the whole Key Issues ternary (issues Panel / generating Panel / "No key issues yet" EmptyState) and before the first `<AnalystConsensus section="estimates">` (today ~:1143-1190). | Spec §10.1. A run is analysis of ONE instrument; it is not a top-level job. No new tab, no new route. |
| Setup | **Extend** the Connectors tab: new connector `trading_agents`, family `Data source`, setup form `TradingAgentsSection` registered in `ConnectorConfig.tsx`. No new tab, no new `App.tsx` wiring (no `TABS`, `TAB_LABELS`, `TAB_DISPLAY_NAMES`, `TAB_SUBTITLES`, `TAB_ICONS` change). | Per the IA model: it is an in/out integration (reads market data, sends context to the AI provider). Models stay separate: its two model pickers only SELECT from Models, they do not configure providers. It is not an Automation and not an Agent. |
| Cost | **Extend** Settings -> Cost & Performance (`AiUsageSection.tsx`): new `TradingAgentsCostPanel` between `SearchFetchCostPanel` and `ModelTelemetryPanel`. | Same period picker, same section. |
| Approval card | **Extend** the existing approval-card system with a dedicated body for preview kind `trading_agents_run`. | The `GenericPreviewBody` fallback would dump `disclosure`/portfolio as folded JSON; the owner must read the shared amounts and the recipient before approving (spec §6 consent). |

Catalog entry (`frontend/src/components/tabs/connectors/catalog.ts`, next to `coingecko`, precedent `voice` for `flag`):

```
id: 'trading_agents'            flag: 'trading_agents'
name: 'TradingAgents'           families: ['Data source']   categories: ['Market data']
icon: 'investments'             hue: 'var(--accent)'
publisher: 'TauricResearch'     verification: 'unverified'
homepageUrl: 'https://github.com/TauricResearch/TradingAgents'
supportUrl: 'https://github.com/TauricResearch/TradingAgents/issues'
mcp: false   popular: false   tools: []
```
(No `privacyUrl`/`service`: data goes to several vendors, named in the panel's Data sources
section instead. If `ConnectorCategory` has no `Market data`, the build agent stops and asks;
do not invent a category.) sub/desc come live from `connector_copy.py` (already written). Then
`npm run sync:connector-directory` and commit the JSON.

### 0.2 Blocking backend gaps (found while mapping UI to real JSON)

The UI below is designed against these. Each is small; none changes a contract the backend
already ships. The build agent raises them to the backend owner before starting (b), (a), (c).

| # | Gap | Needed | Blocks |
|---|---|---|---|
| G1 | No route reads or writes the connector's settings or its two data-vendor keys. `PATCH /api/connectors/{id}` only toggles `enabled`; `GET /api/trading-agents/status` omits rounds, tool rounds, concurrency, language, SEC agent, raw (nullable) model refs, key presence. | `GET /api/trading-agents/config` -> `TaConfig` (section 1.1). `PUT /api/trading-agents/config` PATCH-shaped like CoinGecko's `/api/market-data/config`: absent or blank key = keep, explicit `null` = clear; same validators as `settings.py`; typed `ValidationError` carries `context.field`. Demo lockdown: refused. | (b) |
| G2 | `GET /api/trading-agents/runs/{id}` returns the raw row; the Lucius tool adds `thesis_judgment`, REST does not. The stance is price-derived on the server (`services/thesis_judgment`, the one authority); the browser must never recompute it. | Add `thesis_judgment` (object or `null`) to the run payload. Chip is hidden when absent/null. | (a) chip |
| G3 | `GET /api/cost/trading-agents` accepts `days`/`range`/`tz` only; the section's period picker sends `start`/`end` for 7 and 30 days, and `days=N` here spans N+1 calendar days. | Accept `start`/`end` (pass through to `costs.summary`, already supported). | (c) |
| G4 | Install "log tail" (spec §10.2) has no source; status carries `install.error` (string) only. | None. UI shows `install.error` as the tail. Do not add a log endpoint. | none |

### 0.3 Naming

| Where | Text |
|---|---|
| Connector nav/list/detail name | **TradingAgents** |
| Connector one-liner | from `connector_copy.py` ("Multi-agent stock research with a five-step rating. Research only.") |
| Asset card heading (the `Panel` title) | **TradingAgents · research only** |
| Cost panel title / surface-bucket label | **TradingAgents** |
| Approval card header | "Start TradingAgents analysis" + ticker subject pill (section 5.1); never "Tool permission" |
| Rating `REVIEW` | **Unclear** |

---

## 1. Shared types, hook, constants (new files)

### 1.1 `frontend/src/types/tradingAgents.ts` (mapped to the real JSON)

```ts
export type TaAvailabilityStatus =
  | 'flag_off' | 'connector_off' | 'connector_paused' | 'not_installed'
  | 'installing' | 'model_unavailable' | 'ready'          // services/trading_agents/__init__.py

export type TaInstallStatus = 'absent' | 'installing' | 'ready' | 'failed'
export type TaContextLevel = 'investments' | 'position' | 'ticker'
export type TaAnalyst = 'market' | 'social' | 'news' | 'fundamentals'
export type TaSignal = 'Buy' | 'Overweight' | 'Hold' | 'Underweight' | 'Sell' | 'REVIEW'
export type TaRunStatus =
  | 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
  | 'stopped_budget' | 'stopped_calls' | 'timed_out' | 'interrupted'
export type TaRequester = 'user' | 'lucius' | 'automation'

/** GET /api/trading-agents/status  (queries.status) */
export interface TaStatus {
  availability: { status: TaAvailabilityStatus; message: string }
  install: { status: TaInstallStatus; version: string | null; error: string | null }
  models: { deep: string; quick: string;                       // "provider_key:model"
            transports: { deep: string; quick: string } } | null   // null when unresolvable
  caps: { per_run_cap_usd: number; monthly_cap_usd: number; max_llm_calls: number }
  context_level: TaContextLevel
  analysts: TaAnalyst[]
  month_spend_usd: number
  queue_length: number
  disclaimer_accepted: boolean
}

/** POST /api/trading-agents/estimate  (budget.Estimate.as_dict) */
export interface TaEstimate {
  estimate_usd: number          // metered median; 0 when plan_included
  low_usd: number
  high_usd: number
  calls: number                 // expected model calls
  basis: 'history' | 'static'
  sample_size: number           // finished runs matched; 0 for static
  plan_included: boolean        // CLI subscription: no dollars, quota
  priced: boolean               // false => a metered model has no price, figure understates
}

/** PortfolioContext as built by context.build (cash null when no FX rate). */
export interface TaPortfolio {
  cash: number | null
  currency: string
  positions: { ticker: string; quantity: number; average_price: number | null }[]
}
export interface TaDisclosure {
  level: TaContextLevel
  includes: string[]
  never_sent: string[]
  currency: string | null
  currency_source: 'instrument' | 'held_position' | 'base_currency' | null
  cash_basis: string | null
  fx_converted: { ticker: string | null; from: string; to: string; rate: number; what?: string }[]
  average_price_omitted: string[]
  partial_cost: string[]
  cash_omitted: string | null
}
/** GET /api/trading-agents/context-preview  and  run.context_sent */
export interface TaContext { ticker: string; portfolio: TaPortfolio | null; disclosure: TaDisclosure }

export interface TaCostPart {
  calls: number; input_tokens: number; output_tokens: number
  cost_usd: number; plan_included_calls: number
}

/** The reports a run keeps (runs.REPORT_FIELDS); only present keys are non-empty. */
export type TaReportField =
  | 'market_report' | 'sentiment_report' | 'news_report' | 'fundamentals_report'
  | 'bull_history' | 'bear_history' | 'investment_plan' | 'trader_investment_plan'
  | 'aggressive_history' | 'conservative_history' | 'neutral_history'

/** Row of GET /runs (no context_sent / progress / reports). GET /runs/{id} adds them. */
export interface TaRunSummary {
  id: string                    // tar_<12hex>
  ticker: string
  instrument_id: string | null
  trade_date: string            // YYYY-MM-DD
  status: TaRunStatus
  requested_by: TaRequester
  session_id: string | null
  analysts: TaAnalyst[]
  config_snapshot: { deep?: string; quick?: string;
                     transports?: { deep?: string; quick?: string };
                     rounds?: { debate: number; risk: number; tools: number } }
  context_level: TaContextLevel
  signal: TaSignal | null
  decision_text: string | null
  llm_calls: number
  input_tokens: number; output_tokens: number
  cache_read_tokens: number; cache_creation_tokens: number
  cost_usd: number              // metered only
  plan_included_calls: number
  unpriced_calls: number
  cost_by_role: Record<string, TaCostPart>
  cost_by_model: Record<string, TaCostPart>
  estimate_usd: number | null
  cap_usd: number
  error: string | null          // sanitized, <= 2000 chars
  created_at: string; started_at: string | null; finished_at: string | null
}
export interface TaRun extends TaRunSummary {
  context_sent: TaContext
  progress: { stage: string; status: 'started' | 'finished'; at: string }[]  // stage = graph node name
  reports: { fields: Partial<Record<TaReportField, string>>; truncated: TaReportField[] } | null
  thesis_judgment?: TaThesisJudgment | null      // gap G2
}
export interface TaThesisJudgment { stance: string; held: boolean; [k: string]: unknown }
export interface TaRunList { runs: TaRunSummary[]; total: number }

/** GET/PUT /api/trading-agents/config  (gap G1) */
export interface TaConfig {
  deep_model: string | null; quick_model: string | null       // null = ONW default
  analysts: TaAnalyst[]
  max_debate_rounds: number; max_risk_rounds: number          // 1-3
  max_tool_rounds: number                                     // 2-20
  max_llm_calls: number                                       // 20-600
  bridge_concurrency: number | null                           // 1-8, null = auto
  per_run_cap_usd: number                                     // 0.10-50
  monthly_cap_usd: number                                     // 0-1000, 0 = no metered runs
  context_level: TaContextLevel
  output_language: string                                     // <= 32
  sec_edgar_user_agent: string                                // <= 200
  has_alpha_vantage_api_key: boolean
  has_fred_api_key: boolean
}

/** GET /api/cost/trading-agents  (costs.summary) */
export interface TaCostSummary {
  window: { days: number; start: string | null; end: string | null }
  totals: {
    runs: number; done: number; failed: number; stopped: number; active: number
    metered_usd: number; avg_cost_per_run_usd: number; median_cost_per_run_usd: number
    calls: number; input_tokens: number; output_tokens: number
    cache_read_tokens: number; cache_creation_tokens: number
    plan_included_calls: number; unpriced_calls: number
  }
  month: { spend_usd: number; cap_usd: number; pct_used: number | null;   // null when cap 0
           per_run_cap_usd: number; max_llm_calls: number }
  by_role: Record<string, TaCostPart & { runs: number }>
  by_model: Record<string, TaCostPart & { runs: number; transport: string | null }>
  by_ticker: (TaCostPart & { ticker: string; runs: number })[]            // top 10
  by_day: (TaCostPart & { date: string; runs: number })[]
  recent: {                                                               // newest 20
    id: string; created_at: string; ticker: string; trade_date: string
    signal: TaSignal | null; status: TaRunStatus; calls: number
    input_tokens: number; output_tokens: number; cost_usd: number
    estimate_usd: number | null; plan_included_calls: number; unpriced_calls: number
  }[]
}

/** Approval-card preview data (ai/tools/trading_agents._StartPreview.build) */
export interface TaRunPreviewData {
  ticker: string; trade_date: string; analysts: TaAnalyst[]
  deep_model: string                 // "provider_key:model via transport"  (display string)
  quick_model: string                // same
  estimated_cost: string             // prose, see section 5.3: never parse it
  per_run_cap: string                // "$2.00, at most 150 model calls"
  this_month: string                 // "$3.10 spent of a $20.00 cap"
  context_level: TaContextLevel
  shared_with_the_ai_provider: TaPortfolio | string   // string = "nothing: ticker and date only"
  disclosure: TaDisclosure
  data_vendors: string               // "Receive the ticker and date only, never your amounts."
}
```

### 1.2 `frontend/src/types/tradingAgents.ts` constants (same file, below the types)

- `TA_SIGNAL_LABEL: Record<TaSignal,string>` = Buy / Overweight / Hold / Underweight / Sell / **Unclear**.
- `TA_SIGNAL_ORDER = ['Sell','Underweight','Hold','Overweight','Buy']` (REVIEW is off the scale).
- `TA_CLI_PROVIDERS = new Set(['claude_cli','codex_cli','antigravity_cli'])` (same names as
  backend `CLI_TRANSPORTS`; provider keys and transports share these ids).
- `TA_STAGE_GROUPS` — the ONE node-name -> stage map (mirrors spec §7.5; exact names are read from
  the pinned commit's `graph/setup.py`, the same list the backend test pins). Five groups, in order:
  `analysts` ("Analysts": Market/Social/News/Fundamentals Analyst + their tool nodes),
  `research` ("Bull vs Bear": Bull Researcher, Bear Researcher, Research Manager),
  `trader` ("Trader"), `risk` ("Risk debate": Aggressive/Conservative/Neutral Analyst),
  `pm` ("Portfolio manager": Portfolio Manager, Risk Judge). A node in no group is ignored
  (never throws, never renders).
- `TA_REPORT_SECTIONS`: ordered accordion rows -> `{ id, label, fields: TaReportField[] }`:
  Market (`market_report`) · Sentiment (`sentiment_report`) · News (`news_report`) ·
  Fundamentals (`fundamentals_report`) · Bull vs Bear (`bull_history`,`bear_history`) ·
  Trader plan (`investment_plan`,`trader_investment_plan`) · Risk debate (`aggressive_history`,
  `conservative_history`,`neutral_history`). Portfolio manager = the run's `decision_text`
  (shown above the accordion as the decision, and also as the last row "Portfolio manager").
  A row whose fields are all missing is not rendered.
- `TA_CONTEXT_LABEL: Record<TaContextLevel,string>` = Investments · This position · Ticker only
  (long forms for the radio: see 2.4).

### 1.3 `frontend/src/hooks/useTradingAgents.ts`

All reads through `useQueryCache` (revalidates on the `trading_agents` data event; no local
fetched state, so no `useDataVersion` needed).

| Hook | Key | Fetch | Options |
|---|---|---|---|
| `useTaStatus()` | `/api/trading-agents/status` | GET | `staleTime: 15_000` |
| `useTaRuns(ticker, limit=5)` | `/api/trading-agents/runs?ticker=${t}&limit=${limit}` | GET | `enabled: ready-or-has-history` |
| `useTaRun(id)` | `/api/trading-agents/runs/${id}` | GET | `enabled: !!id`; `refetchInterval: 4000` only while status is `queued`/`running` |
| `useTaEstimate(ticker, analysts)` | `/api/trading-agents/estimate` | POST (read-only; wrap in `useQueryCache` with the body in the key) | `enabled: ready && analysts.length>0`; `staleTime: 60_000` |
| `useTaContextPreview(ticker, level)` | `/api/trading-agents/context-preview?ticker=&context_level=` | GET | `enabled: the "What will be shared" expander is open` |
| `useTaConfig()` | `/api/trading-agents/config` | GET | used only by the setup panel (G1) |
| `useTaCost(window)` | per 4.2 | GET | `enabled: flag on` |

Mutations are plain `authFetch` helpers in the same file (`startTaRun`, `cancelTaRun`,
`deleteTaRun`, `acceptTaDisclaimer`, `installTa`, `saveTaConfig`), each followed by
`invalidateQueryCache` of the keys above (the data event covers other surfaces). A mutation
failure surfaces `body.detail.message` (the typed `{code,message,context}` shape) — never a
generic string.

Gate: every consumer calls `useFlag('trading_agents')` first; `undefined` (flags not loaded) renders
nothing (card/panel) — never guesses `true`.

---

## 2. (a) Asset page — `TradingAgentsCard` + `TradingAgentsReport`

### 2.1 Files

| Create | Purpose |
|---|---|
| `frontend/src/components/intel/TradingAgentsCard.tsx` | Container: gating, state machine, owns the featured-run id, renders one of the states below |
| `frontend/src/components/intel/TradingAgentsRunForm.tsx` | Ready/no-run + "Run new analysis" form (analysts, context level, estimate, caps, shared preview, Start) |
| `frontend/src/components/intel/TradingAgentsProgress.tsx` | Running state: stage list, elapsed, calls, cost, Cancel |
| `frontend/src/components/intel/TradingAgentsReport.tsx` | Done state: rating, thesis chip, decision, report accordion, footer |
| `frontend/src/components/intel/TradingAgentsRating.tsx` | Rating pill + 5-step scale (shared by Report, History rows, cost table) |
| `frontend/src/components/intel/TradingAgentsShared.tsx` | "What was/will be shared": recipient line + context table + disclosure notes |
| `frontend/src/components/intel/TradingAgentsHistory.tsx` | Last 5 runs for the ticker |
| `frontend/src/types/tradingAgents.ts`, `frontend/src/hooks/useTradingAgents.ts` | section 1 |

| Edit | Change |
|---|---|
| `frontend/src/components/intel/AssetDetailView.tsx` | In the `activeTab === 'analysis'` fragment, immediately after the Key Issues ternary and before the first `<AnalystConsensus … section="estimates">`: `{data.has_ticker && asset.asset_class === 'equity' && asset.ticker && (<TradingAgentsCard instrumentId={instrumentId} ticker={asset.ticker} />)}`. Nothing else in the file changes. Crypto, cash, property, bonds and tickerless holdings never render the card (spec: stock only). |
| `frontend/src/index.css` | the `ta-*` block (section 7) |
| `docs/product/` | the asset-page doc page that `docs-map.json` maps to `AssetDetailView` (a new short "TradingAgents" section). Doc rule, not UI. |

Reuse (no re-creation): `Panel`, `Stack`, `Cluster`, `Text`, `Button`, `SegmentedControl`,
`CheckboxGroup`, `Meter`, `Tag`, `Badge`, `EmptyState`/`ErrorState`, `Skeleton`, `FormField`,
`safeMarkdown` (`SAFE_MARKDOWN_COMPONENTS`) + `react-markdown`, `fCurrency`, `fQty`, `fUnitPrice`,
`fDuration`, `fDateTime`, `fDate`, `timeAgo` (`utils/format`), `providerLabelFor` (`api/models`),
`LuciusAskButton` is NOT used (the card is not a Lucius surface). The `asd-section-head` /
`asd-section-title` pair that Key Issues uses is NOT reused: the card uses `Panel title` so the
heading rhythm is the kit's.

### 2.2 Wireframe — desktop (Analysis tab column, ~720-960 wide; card = one `Panel`)

State A: ready, no runs yet (also the "Run new analysis" form).

```
┌ Panel title="TradingAgents · research only" ─────────────────────────────────────┐
│ Text sm faint: Analysts debate this stock and a portfolio manager gives a        │
│ five-step rating. Research, not financial advice. It never places an order.      │
│                                                                                  │
│ Stack gap --space-5                                                              │
│ ┌ FormField "Analysts" ───────────────────────────────────────────────────────┐ │
│ │ CheckboxGroup columns=2: [x] Market  [x] Sentiment  [x] News  [x] Fundamentals│ │
│ └──────────────────────────────────────────────────────────────────────────────┘ │
│ ┌ FormField "What it may know about your holdings" ───────────────────────────┐ │
│ │ SegmentedControl: [ Investments | This position | Ticker only ]              │ │
│ │ Text xs faint: Your investable cash and every investment position…           │ │
│ └──────────────────────────────────────────────────────────────────────────────┘ │
│ ▸ What will be shared            (ta-disclose; lazy useTaContextPreview)        │
│ ──────────────────────────────────────────────────────────────────────────────  │
│ Text sm: About $0.42 (range $0.30 – $0.60) · about 38 model calls               │
│ Text xs faint: Based on your last 10 runs. Stops at $2.00 or 150 calls.         │
│ Meter sm "Monthly spend"   ▓▓▓░░░░░░░  $3.10 of $20.00 this month               │
│ Cluster justify=end:  [ Run multi-agent analysis ]  (Button primary)             │
│ Text xs faint ta-disclaimer: Research only. Ratings come from AI models reading  │
│ public data and can be wrong. Not financial advice. Never an order.              │
└──────────────────────────────────────────────────────────────────────────────────┘
```

State B: running.

```
┌ Panel title="TradingAgents · research only"            actions: [ Cancel ] ───────┐
│ Cluster: Text sm 600 "ACME · analysing 2026-11-19"   Tag muted "Running"          │
│ ┌ ta-stages (role=list) ──────────────────────────────────────────────────────┐  │
│ │ ✓ Analysts            Market · Sentiment · News · Fundamentals     1m 12s    │  │
│ │ ● Bull vs Bear        Debating                                     0m 31s    │  │
│ │ ○ Trader                                                                      │  │
│ │ ○ Risk debate                                                                 │  │
│ │ ○ Portfolio manager                                                           │  │
│ └──────────────────────────────────────────────────────────────────────────────┘  │
│ Cluster text xs faint: Elapsed 1m 52s · 14 model calls · $0.11 so far (of $2.00) │
│ (CLI plan: "14 calls on your Claude Code plan"; unpriced>0: "… · 2 calls unpriced")│
│ queued: Text sm "Queued behind 1 other run" instead of the stage list           │
└──────────────────────────────────────────────────────────────────────────────────┘
```

State C: done (`TradingAgentsReport`).

```
┌ Panel title="TradingAgents · research only"      actions: [ Run new analysis ] ───┐
│ Cluster justify=between wrap:                                                    │
│   ┌ ta-result head ────────────────────────────────────────────────────────┐    │
│   │ [ Overweight ]  ta-scale  Sell · Under · Hold · [Over] · Buy             │    │
│   │ Badge neutral: "Your thesis: …" (only when thesis_judgment, see 2.6)      │    │
│   └──────────────────────────────────────────────────────────────────────────┘    │
│ Text xs faint: ACME · as of 2026-11-19 · run 6 min ago                            │
│ ta-decision (prose, ReactMarkdown skipHtml): decision_text                        │
│ ▸ Market        ▸ Sentiment   ▸ News   ▸ Fundamentals  (ta-disclose rows, stacked) │
│ ▸ Bull vs Bear  ▸ Trader plan ▸ Risk debate ▸ Portfolio manager                    │
│ ▸ What was shared  (TradingAgentsShared from run.context_sent)                    │
│ ta-footer Text xs faint: claude-x via API · gpt-y via Codex plan · $0.38 metered ·│
│   12 plan-included calls · 31 calls · 212k tokens in / 31k out · [Delete run]    │
│ ta-disclaimer (always visible, same line as State A)                              │
│ ── History (TradingAgentsHistory) ──                                              │
│ ta-history rows: [Buy] 2026-11-12 · $0.41     [Hold] 2026-10-30 · plan-included   │
└──────────────────────────────────────────────────────────────────────────────────┘
```

State D: failed / stopped.

```
│ Cluster: Badge neg|warn "Stopped: budget"      [ Retry ] (Button primary sm)      │
│ Text sm: {reason sentence}   — table in 2.5                                       │
│ Text xs faint: {n} calls · $0.52 spent before it stopped · {when}                 │
│ (partial reports are NOT shown on a stopped run: signal is null)                  │
```

State E: not available (one line, no form).

```
┌ Panel title="TradingAgents · research only" ───────────────────────────────────┐
│ Cluster justify=between: Text sm faint: {message}        [ Button secondary sm ] │
└────────────────────────────────────────────────────────────────────────────────┘
```

Phone (< 640): everything is a single column. Panel padding is the kit's. SegmentedControl
wraps to two lines (the kit control already does); CheckboxGroup `columns={1}`; Start button
`fullWidth`; stage list rows drop the elapsed column to a second line (`ta-stage__sub`
wraps); the rating scale hides labels and keeps only the five cells (the pill carries the
word); history rows stack signal/date/cost on two lines; the footer is a wrapped faint
paragraph. Cancel moves from Panel `actions` to a full-width `Button variant="secondary"`
under the stage list (Panel `actions` wraps below the title at phone; both are acceptable to
the kit, but a thumb target at the bottom is the spec). 1024 / 1440: same layout; the card
never exceeds the Analysis column (there is no second column inside it).

### 2.3 State machine (priority order; first match wins)

| # | Condition | Renders |
|---|---|---|
| 1 | `useFlag` is `undefined` | nothing |
| 2 | flag `false` | nothing (no line, no link: the spec's "not enabled" line is only for flag-on) |
| 3 | status loading (no data yet) | `Panel` + 3 `Skeleton` rows (heights 13/13/36, same as Key Issues skeleton), `aria-busy` |
| 4 | status error | `ErrorState title="Couldn't load TradingAgents" message="Check the connection and try again." onRetry={refresh}` inside the Panel |
| 5 | latest run is `queued`/`running` | State B (even when availability later degrades: the run is still going) |
| 6 | availability != `ready` OR `!disclaimer_accepted` | State E (copy in 2.5) + below it, if the ticker has runs, History and the latest Report stay readable (reading needs only the flag); the form is not shown |
| 7 | ready, no runs for this ticker | State A |
| 8 | latest run `done` | State C |
| 9 | latest run in any other terminal status | State D |

"Run new analysis" (Panel `actions`, State C/D) swaps the body to State A with a trailing
`Button variant="ghost"` "Cancel" that returns to the previous state. Starting adds the run
to the cache (`invalidateQueryCache`) and State B appears at once (optimistic: the POST
returns `{run_id}` 202; the list refetches).

### 2.4 Region spec

| Region | ui/ primitives | Reuse | Data | Empty / loading / error |
|---|---|---|---|---|
| Card shell | `Panel title="TradingAgents · research only"`, `actions` slot | — | — | see 2.3 rows 3-4 |
| Intro line | `Text size="sm" tone="faint"` | — | static | — |
| Analysts | `FormField label="Analysts"` + `CheckboxGroup columns={2}` (phone `1`), labels Market / Sentiment / News / Fundamentals (value `social` is labelled "Sentiment") | — | default `status.analysts` | Zero checked: Start disabled + `FormField error="Pick at least one analyst."` |
| Context level | `FormField label="What it may know about your holdings"` + `SegmentedControl` (Investments / This position / Ticker only), default `status.context_level`; selected-level explanation in `Text xs faint` | — | static copy; "This position" when the preview shows no position for the ticker: "You don't hold ACME, so only your cash is shared." | — |
| Shared preview | `ta-disclose` `<details>` "What will be shared" -> `TradingAgentsShared` | new `TradingAgentsShared` (nothing in the kit renders a portfolio context; approval-card tables are chat-only) | `useTaContextPreview(ticker, level)`, only when opened | loading: `Loading label="Reading your holdings…"` compact; error: if the preview cannot load, Start is DISABLED (consent requires the list): `Text xs tone="bad"` "Couldn't load what would be shared, so the run can't start yet." + `Button ghost sm` Retry. |
| Estimate + caps | `Text`, `Meter size="sm"` | — | `useTaEstimate`; caps + `month_spend_usd` from status | loading: `Skeleton` 1 line; error: `Text xs faint` "Estimate unavailable." (Start stays enabled; the server still enforces caps) |
| Start | `Button variant="primary" loading` | — | `POST /runs {ticker, analysts, context_level}` (trade_date omitted = today) | server refusal (`BudgetExceededError`, `TooManyRunsError`, `ModelUnavailableError`, `NotInstalledError`): show `detail.message` as `Text xs tone="bad"` under the button; do not toast |
| Stage list | `ta-stages` rows (`role="list"`), `Text` | new `TradingAgentsProgress` (no kit stepper exists) | `useTaRun(id).progress` + `TA_STAGE_GROUPS`; analysts sub-label from `run.analysts` | `queued`: no list, "Queued behind N other run(s)" from `status.queue_length - 1` (min 0 -> "Starting…") |
| Live meter line | `Cluster`, `Text xs faint` | — | `llm_calls`, `cost_usd`, `plan_included_calls`, `unpriced_calls`, `cap_usd`, `started_at` (elapsed ticks locally each 1s, `fDuration`) | — |
| Cancel | `Button variant="secondary" size="sm"` | — | `POST /runs/{id}/cancel` | disabled + label "Cancelling…" until status leaves running |
| Result head | `Cluster`, `TradingAgentsRating`, `Badge` (stance) | new `TradingAgentsRating` | `run.signal`, `thesis_judgment` | `signal === null` on a `done` run cannot happen; render State D defensively |
| Decision | `Text size="sm"` wrapper `ta-decision`, `ReactMarkdown skipHtml components={SAFE_MARKDOWN_COMPONENTS}` | — | `decision_text` | empty: "No decision text was recorded." faint |
| Report accordion | `ta-disclose` rows per `TA_REPORT_SECTIONS` | new (see note below) | `reports.fields`, `reports.truncated` | missing section: row omitted. `truncated` includes the field: footer line inside the row "Shortened at 40,000 characters." faint |
| History | `ta-history` rows as `<button>`s inside a `role="list"`, `TradingAgentsRating` compact | new `TradingAgentsHistory` | `useTaRuns(ticker, 5)` (list rows omit reports; selecting one switches the featured run id and `useTaRun` loads it) | none: section hidden (never an empty box); >5 total: `Button variant="ghost" size="sm"` "Show earlier runs" raises `limit` to 20 (the API's practical page for this card); beyond 20 the line reads "Older runs are in Settings → Cost & Performance." |
| Footer | `Text xs faint`, `Button variant="ghost" size="sm"` "Delete run" (opens existing kit `ConfirmDialog`, copy 2.5) | — | `config_snapshot`, tokens, costs | — |
| Disclaimer line | `Text xs faint` class `ta-disclaimer` | — | static | always visible in A, C, D, E-with-history; never collapsible |

Note on the accordion: `ui/CollapsibleSection` renders a whole nested `Panel`; inside the card
that is a card-in-a-card with a mounted-only-when-open body. No kit accordion exists. Therefore
a thin `ta-disclose` class on native `<details>/<summary>` (keyboard and screen-reader
semantics free, several can be open) is the one new disclosure style. It is a promotion
candidate for `ui/` later; do not build a `ui/Accordion` in this change.

Rating pill (`TradingAgentsRating`), 5-tier colors, tokens only, REVIEW neutral:

| Signal | Label | Class | Color rule (all via `color-mix` on tokens, same recipe as `.ui-badge--*`) |
|---|---|---|---|
| Buy | Buy | `ta-rating--buy` | `--good` text, 22% `--good` fill (strongest) |
| Overweight | Overweight | `ta-rating--overweight` | `--good` text, 10% `--good` fill |
| Hold | Hold | `ta-rating--hold` | `--muted` text on `--surface2` |
| Underweight | Underweight | `ta-rating--underweight` | `--warn` text, 15% `--warn` fill |
| Sell | Sell | `ta-rating--sell` | `--bad` text, 22% `--bad` fill |
| REVIEW | **Unclear** | `ta-rating--unclear` | `--muted` text, transparent fill, dashed `--line` border (distinct from Hold; no scale position) |

Green = value-up, red = value-down is this app's rule; blue (action) is never used for a
rating. Color is never the only signal: the word is always printed, and `ta-scale` (five cells,
the active one filled in the pill's color, others `--surface2`) shows the position; Unclear
shows no active cell. The scale is `aria-hidden`; the pill's text is the accessible name. The
pill has `aria-label="Rating: Overweight, 4 of 5"` (Unclear: `"Rating: Unclear. The run could not
settle on a rating."`).

### 2.5 Copy

| Where | Text |
|---|---|
| Intro | "Analysts debate this stock and a portfolio manager gives a five-step rating. Research, not financial advice. It never places an order." |
| Disclaimer line | "Research only. Ratings come from AI models reading public data and can be wrong. Not financial advice, never an order." |
| Start button | "Run multi-agent analysis" |
| Run again | "Run new analysis" |
| Cancel | "Cancel" / "Cancelling…" |
| Estimate (metered, history) | "About {fCurrency(est,'USD')} (range {low} – {high}) · about {calls} model calls" + faint "Based on your last {sample_size} runs." |
| Estimate (metered, static) | same + faint "Rough estimate: no run history yet." |
| Estimate (plan) | "Uses your {Claude Code\|Codex\|Antigravity} plan · about {calls} model calls · no dollars" + faint "Counts against your plan's quota. Runs take longer on a plan." |
| Estimate (mixed) | "About {est} metered, plus calls on your {CLI} plan · about {calls} model calls" |
| Estimate unpriced | append warn `Text`: "A model has no known price, so this understates." |
| Caps line | "Stops at {per_run_cap} or {max_llm_calls} calls per run." |
| Month meter label | "{month_spend} of {monthly_cap} this month" (cap 0: "Metered runs are off (monthly cap is $0)" and Start is disabled unless `plan_included`) |
| Over-cap | "This run would pass your {cap} monthly cap. Raise it in setup, or wait until next month." + link "Open setup" |
| Not available: `connector_off` | "TradingAgents isn't set up yet." / button "Set up" |
| `connector_paused` | "The TradingAgents connector is paused." / "Resume" |
| `not_installed` | `availability.message` / "Open setup" |
| `installing` | spinner + "Installing TradingAgents…" (no button) |
| `model_unavailable` | `availability.message` / "Choose models" |
| disclaimer not accepted | "Read and accept the research-only notice to run your first analysis." / "Open setup" |
| All six buttons | navigate `to: '/connectors', search: { connector: 'trading_agents' }` (the exact call `ConnectorPanel` makes) |
| Stage labels | Analysts · Bull vs Bear · Trader · Risk debate · Portfolio manager; active sublabels: "Reading data…" (analysts), "Debating" (research, risk), "Drafting a plan" (trader), "Deciding" (pm) |
| Failed: `failed` | Badge neg "Failed" + `run.error` (already sanitized) |
| `stopped_budget` | Badge warn "Stopped: budget" + "It reached its {cap_usd} per-run cap after {llm_calls} calls." |
| `stopped_calls` | Badge warn "Stopped: call limit" + "It reached the limit of {max} model calls." (max from `config_snapshot`? not stored: use "its model-call limit" without a number) |
| `timed_out` | Badge warn "Timed out" + "It ran out of time before it finished." |
| `cancelled` | Badge muted "Cancelled" + "You cancelled this run." (Lucius/Automation requester: "Cancelled.") |
| `interrupted` | Badge warn "Interrupted" + "The app restarted while it was running." |
| Retry | "Retry" (re-POSTs ticker + same analysts + same context_level; a fresh run, the old one stays in History) |
| Delete confirm | title "Delete this run?" body "The report, its cost record and the list of what was shared are removed." confirm "Delete run" (danger). True to the backend: the cost panel reads run rows, so a deleted run leaves its totals. |
| Stance chip | see 2.6 |
| Requested-by tag | `Tag tone="lucius"` "Asked by Lucius" / `Tag tone="muted"` "Automation" beside the date when `requested_by !== 'user'` |

### 2.6 Thesis stance chip (gap G2)

Shown only when `run.thesis_judgment` is non-null. It reports ONLY what the server computed
(`stance`), never a client comparison of rating vs thesis. Render `Badge tone="neutral"` with
text from a map of stance -> words that already exists for the thesis UI (reuse
`pulseHelp.ts`/`YourThesisCard` wording for `intact`, `below_stop`, `above_trim`, …; build agent
reads those and reuses the strings, adds none). Label prefix: "Your thesis: ". Unknown stance
string -> chip omitted (never a raw identifier on screen).

### 2.7 Accessibility

- Panel heading is the kit `h2`; accordion `<summary>` are focusable, `Enter`/`Space` toggle, visible focus ring from the global focus token (do not remove `outline`).
- Stage list: `role="list"`, each row `role="listitem"`; state is text-first: a visually-hidden prefix ("Done: ", "In progress: ", "Waiting: ") and the glyph `aria-hidden`. Only the active stage's text sits in an `aria-live="polite"` region (`role="status"`); the elapsed timer and cost line are NOT live (no per-second announcements).
- Cancel button label includes the ticker for screen readers: `aria-label="Cancel analysis of ACME"`.
- Rating: text + position, never color alone; contrast of each pill >= the existing `.ui-badge--*` recipes (same tokens).
- `SegmentedControl` and `CheckboxGroup` carry `ariaLabel`s ("Portfolio context", "Analysts").
- Skeleton + loading states set `aria-busy`; `Loading` already has `role="status"`.
- Report markdown is untrusted web-derived text: `skipHtml`, images via `SAFE_MARKDOWN_COMPONENTS`, links forced `target="_blank" rel="noopener noreferrer"`; never `dangerouslySetInnerHTML`.
- Reduced motion: the active-stage pulse is a CSS animation wrapped in `@media (prefers-reduced-motion: no-preference)`.
- History rows are real `<button>`s (`aria-current="true"` on the featured one), min 44px high on touch (use the kit's tap-target token, not a px).

---

## 3. (b) Connector setup panel — `TradingAgentsSection`

### 3.1 Files

| Create | Purpose |
|---|---|
| `frontend/src/components/tabs/connectors/TradingAgentsSection.tsx` | The form (sections below). Config-view body inside `ConnectorDetail`; publishes ONE footer Save through `useConnectorSave` |
| `frontend/src/components/tabs/connectors/TradingAgentsRecipients.tsx` | The "who receives what" explainer (static layout, live provider names) |
| `frontend/src/components/tabs/connectors/TradingAgentsInstall.tsx` | The install block (button, status, version, error) — keeps the Section under the ~400-line file rule |

| Edit | Change |
|---|---|
| `connectors/ConnectorConfig.tsx` | add `'trading_agents'` to `CONFIGURABLE_KINDS`; `case 'trading_agents': return <TradingAgentsSection />`; import |
| `connectors/catalog.ts` | the entry in 0.1; then `npm run sync:connector-directory` (JSON committed) |
| `frontend/src/index.css` | `ta-*` block (section 6) |

No `App.tsx` change. The connector is `pausable` (Pause/Resume/Remove come from `ConnectorDetail`;
the form has NO enable switch, per the ConnectorConfig contract). Its permission ladder (tools
list, "Needs approval" default for `start_trading_agents_analysis`) is rendered by
`ConnectorDetail` from `connector_copy.py`; the form only points at it.

### 3.2 Wireframe (config view; one column inside the connector detail body)

```
Stack gap --space-7  (sections separated by ta-section: 1px --line top border, padding-top --space-7)

[0] Cluster justify=between:  Badge(Ready|Installing|Needs install|Paused|Model unavailable)
                              Text xs faint: availability.message

[1] RESEARCH-ONLY NOTICE      Text as=h3 md 600 + ta-notice block (--surface2, --radius-md, padding --space-5)
    "TradingAgents is research software. …"   (copy 3.4)
    not accepted:  Checkbox "I understand this is research only, not financial advice."
                   Button primary "Accept and continue"            (disabled until ticked)
    accepted:      Text sm tone=good "Notice accepted."

[2] INSTALL                   Text sm faint: runs in its own Python environment in your data folder
    Cluster: Badge(status)  Text xs mono "commit 1a2b3c4"  ·········  Button (Install | Reinstall | Try again)
    failed: ta-log block (role=alert, max-height, scroll) = install.error
    installing: Button loading disabled "Installing…" + "A few minutes. You can leave this page."

[3] MODELS                    Text sm faint: every model call goes through your own AI provider…
    Grid min=280 gap --space-5
      FormField "Deep-thinking model"  ModelPicker size=field  + help (default/CLI hint)
      FormField "Quick model"          ModelPicker size=field  + help
    Text sm tone=warn (only model_unavailable): availability.message

[4] ANALYSTS AND DEPTH
    FormField "Analysts"   CheckboxGroup columns=2
    Grid min=220 gap --space-5
      FormField "Debate rounds (1–3)"      NumberField
      FormField "Risk rounds (1–3)"        NumberField
      FormField "Tool rounds per analyst (2–20)" NumberField
      FormField "Max model calls per run (20–600)" NumberField
      FormField "Parallel model calls (1–8)"  NumberField  blank = automatic
      FormField "Report language"          TextField (≤ 32)

[5] BUDGET
    Grid min=220 gap --space-5
      FormField "Per-run cap (USD)"     NumberField step=0.1 min=0.1 max=50
      FormField "Monthly cap (USD)"     NumberField step=1  min=0 max=1000   help "0 = no metered runs"
    Meter md "Spend this month"  + Text xs faint "$3.10 of $20.00 this month · {queue_length} queued"
    Text xs faint: plan-included note, USD note (copy 3.4)

[6] DATA SOURCES
    Text sm faint: intro + List of the 5 default sources (name — what — receives)
    Grid min=280 gap --space-5
      FormField "Alpha Vantage API key (optional)"  PasswordField + useSecretReveal  [Clear saved key]
      FormField "FRED API key (optional)"           PasswordField + useSecretReveal  [Clear saved key]
    FormField "SEC EDGAR contact (user agent)"      TextField (≤ 200)

[7] PORTFOLIO CONTEXT
    FormField "Default for each run"  RadioGroup (3 options with descriptions)
    TradingAgentsRecipients: Grid min=260 gap --space-5 of 4 ta-recipient blocks
        Your AI provider · Data vendors · Open Net Worth · Nobody
    Text xs faint: cash basis, FX, exclusions, thesis line

[8] PERMISSIONS               Text sm faint one-liner pointing to the connector's Tool permissions
```

Phone (< 640): same order, one column. Both `Grid`s collapse to one column by their own
`min()` guard; `ModelPicker` becomes its bottom sheet (kit behavior, no work); the
`ta-recipient` blocks stack; Install and Accept buttons `fullWidth`; the footer Save is the
modal's pinned footer (unchanged). 1024/1440: the detail body width is fixed by
`ConnectorDetail`; nothing here widens it.

### 3.3 Region spec

| Section | ui/ primitives | Reuse | Data / endpoint | Empty / loading / error |
|---|---|---|---|---|
| Frame | `Stack gap="--space-7"`; section = `Stack gap="--space-4"` with `Text as="h3" size="md" weight={600}` + `Text size="sm" tone="faint"` | `data-ctx-secret` wrapper is already added by `ConnectorConfig` | `useTaConfig()` (G1) + `useTaStatus()` | loading: `Loading label="Loading TradingAgents settings…"`; error: `ErrorState title="Couldn't load TradingAgents" message="…" onRetry` — the form is not rendered |
| Status strip | `Cluster justify="between"`, `Badge`, `Text xs faint` | — | `status.availability` | tones: `ready`->pos, `installing`->warn, `model_unavailable`->warn, `not_installed`->neutral, `connector_paused`->muted, `connector_off`->neutral |
| Notice | `ta-notice` block, `Checkbox`, `Button primary` | `useConnectorWrote()` after the write | `POST /api/trading-agents/disclaimer {accepted:true}`; `status.disclaimer_accepted` | write error: `Text xs tone="bad"` under the button with `detail.message`. The acceptance is an immediate write, NOT part of the footer Save (it is a record, not a setting). Nothing else on the form is disabled by it, but the connector shows "Needs setup" until both install and notice are done (`_trading_agents_configured`). |
| Install | `Badge`, `Button`, `Text` | new `TradingAgentsInstall` | `status.install` ({status,version,error}); `POST /install` (202, returns status); poll via `useTaStatus` `refetchInterval: 3000` only while `installing`, plus the data event | absent: "Not installed" + Install; installing: spinner; ready: "Installed" + Reinstall (secondary); failed: `ta-log` with `install.error` (this IS the log tail; G4); version: first 7 chars in `Text xs` with `className="ta-mono"`, `title` = full commit |
| Models | `FormField`, `ModelPicker size="field"` x2, `Text`, `Tag` | `ModelPicker` + `fetchModels(true)` through `useQueryCache('/api/models?all=1')` (same list Dedup/Task-models use: every enabled provider, CLI included); resolve the default the same way `DedupSection` does (`defaultModelId`) | `config.deep_model`, `config.quick_model` (`null` -> `value=""`) | picker `defaultLabel` "Your Lucius model" (deep) / "Intel model, else deep" (quick); `isUnavailable` for a model whose provider is disabled (never selectable); a saved ref whose provider is gone shows the ref verbatim + `Text xs tone="warn"` "This provider is turned off. Runs will refuse until you pick another." CLI selected (`TA_CLI_PROVIDERS`) -> `Text xs faint` "Uses your {CLI} subscription: calls count against your plan, not dollars, and run slower." |
| Analysts + depth | `CheckboxGroup`, `NumberField`, `TextField`, `FormField`, `Grid` | — | `config.*` | Each `FormField` shows the range in its label and `error` from a local check (ranges in `TA_LIMITS`, mirrored from `settings.py`); a server `ValidationError` with `context.field` maps to that field's `error`. Help lines: rounds "More rounds mean a longer debate and a higher cost."; tool rounds "Lower is cheaper."; max calls "A hard stop per run. Protects your plan's quota on a subscription."; concurrency "Leave blank for automatic (4 on API models, 2 on a subscription)." |
| Budget | `NumberField` x2, `Meter` | `Meter size="md"` tone: `good` < 80%, `warn` 80-99%, `bad` >= 100% of cap; cap 0 -> no meter, `Text` "Metered runs are off." | `config.per_run_cap_usd`, `monthly_cap_usd`; `status.month_spend_usd`, `status.queue_length` | — |
| Data sources | `List`, `FormField`, `PasswordField`, `TextField`, `Button ghost` "Clear saved key" | exactly the CoinGecko key pattern: `useSecretReveal` + `revealConnectorSecret('trading_agents', '<field>')` from `./secrets`, `unrevealed()` for the dirty baseline, a `clearRequested` flag per key, blank = keep, `null` = clear (G1 contract) | `config.has_alpha_vantage_api_key`, `has_fred_api_key`, `sec_edgar_user_agent` | key help: saved -> "A key is saved. Leave blank to keep it."; none -> "Optional. TradingAgents works without it." |
| Portfolio context | `RadioGroup name="ta-context-level"`, `Text`, `Grid` | new `TradingAgentsRecipients` | `config.context_level` | — |
| Permissions | `Text sm faint` | the ladder is `ConnectorDetail`'s | — | — |

Save behavior: `useConnectorSave({ ready, values, baseline, saving, error, save })` — `values`
= all editable fields (keys through `unrevealed`), `baseline` = what `GET /config` last
returned, so the footer Save arms only when something changed. `save()` PUTs the changed
fields; success re-fetches config and `invalidateQueryCache` status + `CONNECTORS_KEY`
(`ConnectorPanel` does the latter via `onSaved`). A failed save sets `saveErr` and renders it
under the Budget or offending field, never only in a toast.

### 3.4 Copy (verbatim)

| Where | Text |
|---|---|
| Section titles | Research-only notice · Install · Models · Analysts and depth · Budget · Data sources · Portfolio context · Permissions |
| Notice body | "TradingAgents is research software. A team of AI analysts reads public data about a stock, debates it, and a portfolio manager gives a rating from Buy to Sell. The rating is generated by AI models, can be wrong, incomplete or out of date, and is not financial advice. Open Net Worth never places an order, and neither does TradingAgents. Any decision is yours." |
| Notice checkbox / button | "I understand this is research only, not financial advice." / "Accept and continue" |
| Install intro | "TradingAgents runs in its own isolated Python environment inside your Open Net Worth data folder. It is installed once and does not touch the rest of the app." |
| Install states | "Not installed" · "Installing…" · "Installed" · "Install failed" ; buttons "Install" · "Reinstall" · "Try again"; installing helper "This takes a few minutes. You can leave this page." |
| Models intro | "Every model call goes through your own AI provider in Open Net Worth, so there are no extra API keys and every call shows in your usage totals. TradingAgents never talks to an AI vendor directly." |
| Deep / quick labels | "Deep-thinking model" help "Used for the research manager and the portfolio manager, the two decisions that matter most." · "Quick model" help "Used for the analysts, the debates, the trader and the risk panel. Most calls go here, so a cheaper model saves the most." |
| Budget intro | "Caps are enforced: a run stops when it reaches its cap, and a new run is refused if it would pass your monthly cap." |
| Budget notes | "Runs on a subscription model (Claude Code, Codex, Antigravity) are not counted in dollars and are not blocked by the monthly cap. Their call limit still applies." · "TradingAgents spend is tracked in US dollars, whatever your base currency." |
| Data sources intro | "TradingAgents reads public data. These services receive the ticker and the date, never your amounts." |
| Data source list | **Yahoo Finance** (prices, fundamentals, news) · **SEC EDGAR** (filings, insider activity; also receives the contact line below) · **StockTwits** and **Reddit** (social sentiment) · **Polymarket** (prediction markets). Optional with a key: **Alpha Vantage** (extra fundamentals and news), **FRED** (economic data). |
| SEC field | label "SEC EDGAR contact (user agent)" help "Sent to SEC EDGAR with every request, as the SEC asks. If you put your name or email here, SEC EDGAR receives it. Never filled from your profile." |
| Context intro | "A run can be given your real investing amounts so the trader and risk team can size advice against what you hold, for example: you hold 120 shares, that is 18% of your investments, and you have 9,400 to deploy. You choose how much it sees; you see the exact list before every run." |
| Level options | **Investments** — "Your investable cash and every investment position (ticker, quantity, average price)." · **This position** — "Your investable cash and only the position in the stock being analysed." · **Ticker only** — "Nothing about your holdings. Ticker and date only." Badge "Default" on `investments`. |
| Recipient 1 title / body | "Your AI provider" / "{Anthropic, OpenAI-compatible endpoint, Claude Code on this computer, …}: names resolved from the two chosen models. **Receives:** the ticker and date, and at the level above your investable cash and positions in the stock's currency, plus everything TradingAgents writes during the run. A subscription CLI runs on this computer and sends it under your plan." |
| Recipient 2 | "Data vendors" / "Yahoo Finance, SEC EDGAR, StockTwits, Reddit, Polymarket{, Alpha Vantage, FRED when you add a key}. **Receive:** the ticker and date only. **Never** your amounts." |
| Recipient 3 | "Open Net Worth (this computer)" / "Keeps a record of what was shared on every run (shown as What was shared) in your local data and backups." |
| Recipient 4 | "Nobody" / "Never leaves, at any level: account names, institutions and account numbers, transactions and payees, income and budgets, net worth and liabilities, retirement balances, owned assets, your investment theses, memory and profile." |
| Cash basis line | "Cash is the cash in your cash accounts plus the cash inside your investment accounts, before money set aside in budget envelopes. Amounts are converted to the stock's currency at today's rates. Retirement accounts, unvested equity, owned assets and untracked brokerage totals are not included." |
| Thesis line | "Your thesis is never sent. Open Net Worth compares the rating to it afterwards, on this computer." |
| Permissions line | "Which tools Lucius may use, and whether starting a run asks you first, is set under Tool permissions on this connector. Starting a run asks every time by default." |

`TA_NEVER_SENT` (Recipient 4) is a static copy of `context.NEVER_SENT`; a vitest asserts it has 8
entries so a backend change prompts a UI review.

### 3.5 Accessibility

- Each section is a `<section aria-labelledby>` with its `h3`. Status `Badge` text carries the state (no color-only).
- Install progress is a `role="status"` line; the failure block is `role="alert"`.
- Accept checkbox is a real `Checkbox` with its label; the button's disabled reason is in `aria-describedby` text ("Tick the box to continue").
- Range errors are `FormField error` (associated via the kit's `aria-describedby`); invalid inputs get `invalid`.
- Secret fields follow `useSecretReveal` semantics (eye button labelled by the kit).
- The recipients blocks are a `<dl>`-style pair inside each block (term = recipient, definition = what it receives) so a screen reader reads recipient -> data.

---

## 4. (c) Cost: `TradingAgentsCostPanel` and the "TradingAgents" bucket label

### 4.1 Files

| Create | Purpose |
|---|---|
| `frontend/src/components/tabs/settings/sections/TradingAgentsCostPanel.tsx` | The panel |
| `frontend/src/components/tabs/settings/sections/TradingAgentsCostParts.tsx` | `RoleBars`, `DayBars`, `RecentRunsTable` (keeps the panel under ~400 lines) |

| Edit | Change |
|---|---|
| `sections/AiUsageSection.tsx` | (1) mount `<TradingAgentsCostPanel window={window} windowLabel={windowLabel} formatCost={trendUsd} formatTokens={tok} />` between `SearchFetchCostPanel` and `ModelTelemetryPanel` (the same prop-injection pattern `SearchFetchCostPanel` uses, so `usd`/`tok` are not duplicated). (2) **Bucket label:** add `const SURFACE_LABELS: Record<string, string> = { trading_agents: 'TradingAgents' }` and let `bars()` take an optional label map; ONLY the "By surface" call passes it (`bars(summary?.by_surface, SURFACE_LABELS)`). Fallback is the raw key, so every other bucket renders exactly as today (this change relabels nothing else). By provider / By model need nothing: the backend already includes the TradingAgents rows. |
| `frontend/src/index.css` | `ta-daybars` classes (section 6) |

`window` is the section's existing `CostWindow` ('today' | 'yesterday' | '7' | '30' | '0'). The panel
does NOT render its own period control: it sits under the section's one picker.

### 4.2 Data + gating

- `useFlag('trading_agents')`: `undefined`/`false` -> render nothing (and the cost query is `enabled: false`).
- Key: same window logic as `costKey` but path `/api/cost/trading-agents`: `today`/`yesterday` -> `?range=…&tz=`, `7`/`30` -> `?start=…&end=…&tz=` (needs G3; ship G3 first, because `days=N` on this endpoint spans N+1 calendar days and would disagree with the rest of the section), `0` -> `?days=0&tz=`. `staleTime: 30_000`, via `useCachedFetchState` exactly like the main summary.
- Shown when the flag is on AND (connector enabled, i.e. `useTaStatus().availability.status` is not `flag_off|connector_off|connector_paused`, OR `totals.runs > 0` OR `totals.calls > 0`). While the cost query is loading and the status says "enabled", show the panel shell with its loading state; when the status says not enabled, wait for the cost query before deciding (no flash).

### 4.3 Wireframe (settings idiom: `Panel` from `../ds` + `rd-*` classes, like `SearchFetchCostPanel`)

```
┌ ds Panel title="TradingAgents" sub="Multi-agent stock research · runs, spend and limits" ──────┐
│                                                  actions: Metered spend · {periodLabel}  $1.84 │
│                                                                                                │
│ [ Meter md  "TradingAgents spend this month" ▓▓▓▓░░░░░░ ]                                      │
│ Text xs: This month  $3.10 of $20.00  ·  Per run  $2.00 cap  ·  Max 150 calls per run          │
│                                                                                                │
│ rd-stats rd-cost-stats (tiles, reflow 6 -> 3 -> 2):                                            │
│  Metered spend | Runs | Average per run | Median per run | Model calls | Tokens in / out        │
│  (+ tile "Plan-included calls" only when > 0)                                                  │
│ rd-help line:  9 done · 2 failed · 1 stopped · 1 running                                       │
│ Text sm tone=warn (only unpriced > 0):  3 calls had no price; real spend is higher.            │
│                                                                                                │
│ rd-bars "By role"       Market analyst  ▓▓▓▓▓░░  $0.61 · 41 calls                              │
│ rd-bars "By model"      claude-x · API  ▓▓▓▓▓▓░  $1.60 · 88 calls · 410k tokens                 │
│                         gpt-y · Codex plan ░░░░  plan-included · 52 calls · 190k tokens        │
│ rd-bars "By ticker"     ACME ▓▓▓▓▓  $0.92 · 3 runs      (top 10)                               │
│ By day  ta-daybars  ▁▂▁▁▅▂▁▁▇▁▁▂  Text xs faint: Peak $0.74 on Nov 12                          │
│                                                                                                │
│ rd-service-cost-table  Recent runs                                                             │
│  Date | Ticker | Rating | Status | Calls | Tokens | Estimate | Actual                         │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

Phone (< 640): `rd-stats` tiles wrap two per row (existing behavior); `rd-bar` rows keep
label / track / value (existing); the Meter and caps line are full width; `ta-daybars` keeps all
bars (min-width collapses, max 60 days shown); the recent-runs table scrolls horizontally inside
`rd-service-cost-table-wrap` (existing wrapper, nothing new). 1024/1440: same panel in the settings
column; tiles stay six across.

### 4.4 Region spec

| Region | Primitives / classes | Reuse | Data | States |
|---|---|---|---|---|
| Shell | ds `Panel title sub actions` | `rd-service-cost-total` for the actions slot (`<span>Metered spend · {periodLabel}</span><strong>{formatCost(totals.metered_usd)}</strong>`) | `summary.totals.metered_usd` | loading: `Loading label="Loading TradingAgents spend…"` in the body; error: `ErrorState title="Couldn't load TradingAgents spend" message="Try again." onRetry`; empty (no runs in window): the month Meter + caps stay, the rest replaced by `rd-service-cost-empty` "No TradingAgents runs in this period." |
| Month vs cap | `Meter size="md"` (`ariaLabel="TradingAgents spend this month"`, `valueText="$3.10 of $20.00"`), `Text xs faint` | kit `Meter`; tone `good` < 80%, `warn` 80-99%, `bad` >= 100% of `month.pct_used` (x100); `pct_used === null` (cap 0): no Meter, text "Metered runs are off (monthly cap is $0)." | `summary.month` — current month whatever the window; label says "This month" so it is not read as the window | — |
| Totals tiles | `rd-stats rd-cost-stats` / `rd-stat` / `rd-stat-lbl` / `rd-stat-val` (as the main panel) | `formatCost`, `formatTokens` props | `totals`: Metered spend, Runs, Average per run (`avg_cost_per_run_usd`), Median per run, Model calls, "Tokens in / out" (`{formatTokens(in)} / {formatTokens(out)}`) | Zero -> "$0.00" / "0", never blank |
| Plan-included | one extra `rd-stat` "Plan-included calls" (only when `plan_included_calls > 0`) value `{n}`, with `rd-help` "{in} in / {out} out tokens on a subscription. Counted, not billed." | tokens = sum over `by_model` rows whose `transport` is in the CLI set (`claude_cli|codex_cli|antigravity_cli`) | `totals.plan_included_calls`, `by_model[*].input/output_tokens` | A plan-only user sees "$0.00" metered and this tile: that is correct, not an error |
| Run outcome line | `rd-help` | — | `totals.done/failed/stopped/active` ("1 running" only when active > 0) | — |
| Unpriced warning | `Text size="sm" tone="warn"` (`role="status"`) | — | `totals.unpriced_calls > 0` | text: "{n} calls had no price; real spend is higher." |
| By role | `rd-bars` / `rd-bar` / `rd-bar-k` / `rd-bar-track` / `rd-bar-v` | the same markup `AiUsageSection` uses for By surface | `by_role` (keys are role ids; labels from `TA_ROLE_LABEL`: analyst_market "Market analyst", analyst_sentiment "Sentiment analyst", analyst_news "News analyst", analyst_fundamentals "Fundamentals analyst", research_debate "Bull vs Bear debate", research_manager "Research manager", trader "Trader", risk_debate "Risk debate", portfolio_manager "Portfolio manager", memory "Memory", other "Other"; an unknown id shows humanized) | track width = `cost / totals.metered_usd`; when `metered_usd === 0` the heading becomes "By role (share of calls)" and width = `calls / totals.calls`. Value text `$0.61 · 41 calls`, or `plan-included · 41 calls` when cost is 0 and plan calls > 0. Empty: `rd-help` "Nothing recorded in this period." |
| By model | same | `rd-mono` on the label | `by_model` (key = `provider_key:model`, `transport`) | label `{bare model} · {API | Claude Code plan | Codex plan | Antigravity plan}`; `title` = full ref. Value `$ · calls · tokens`. |
| By ticker | same | — | `by_ticker` (already top 10, sorted) | value `$0.92 · 3 runs` |
| By day | new `DayBars` (`ta-daybars`) | — | `by_day` (last 60 entries max) | `role="img"`, `aria-label="Spend by day. Peak $0.74 on Nov 12."` (all-zero: "No metered spend in this period."). Bars scale to the peak; a zero day renders a 1-step stub so the timeline stays continuous. Hover/focus is not required (it is a sparkline equivalent; the numbers are in the table below). |
| Recent runs | `rd-service-cost-table-wrap` / `rd-service-cost-table`, `<th scope="col">` | `TradingAgentsRating compact` (the same pill as the asset page), `fDate`, `formatCost`, `formatTokens`, `TA_STATUS_LABEL` | `recent` (newest 20) | Date `fDate(created_at)`; Rating pill or "—" when `signal` null; Status text (Done / Failed / Cancelled / Stopped: budget / Stopped: call limit / Timed out / Interrupted / Queued / Running); Calls; Tokens `in/out`; Estimate `formatCost(estimate_usd)` or "—"; Actual `formatCost(cost_usd)`, or "Plan" with `title="Ran on a subscription: no dollars"` when `cost_usd === 0 && plan_included_calls > 0`, and a trailing "!" `title="No price for {n} calls"` when `unpriced_calls > 0`. No row link (there is no run page; reports live on the asset page). |

### 4.5 Copy

Panel title "TradingAgents" · sub "Multi-agent stock research · runs, spend and limits" · actions
"Metered spend · {period}" · month line "This month {spend} of {cap} · Per run {cap} cap · Max {n}
calls per run" · empty "No TradingAgents runs in this period." · plan tile "Plan-included calls"
· unpriced "{n} calls had no price; real spend is higher." · table heads Date, Ticker, Rating,
Status, Calls, Tokens, Estimate, Actual. Surface-bucket label "TradingAgents".

### 4.6 Accessibility

`Meter` is a `progressbar` with `aria-label`/`aria-valuetext`; `DayBars` is an `img` with a text
summary and the data is repeated in the table; tables have `scope="col"` heads; the unpriced line
is plain text (not color-only: it states the consequence); status uses words, not only the pill
tone; all costs are tabular-nums (inherit from `rd-stat-val`/table).

---

## 5. (d) Approval card — dedicated body for `trading_agents_run`

Today the kind falls to `GenericPreviewBody`: scalars as fact rows with raw key names ("Deep model",
"Per run cap", "Shared with the ai provider"), the portfolio dict and the `disclosure` folded as JSON
behind "Shared with the ai provider · 3 fields". That hides the consent-critical fact (real cash and
position sizes leave for the model provider). The dedicated body keeps the card anatomy of
`docs/APPROVAL_CARDS.md` (lead -> meta -> facts -> details), uses only `ApprovalCard.tsx` primitives
and adds NO new className or font size.

### 5.1 Files

| Action | File |
|---|---|
| Create | `frontend/src/components/chat/approval/bodies/TradingAgentsBodies.tsx` exporting `TradingAgentsRunPreview({ preview }: { preview: ToolPreview })` |
| Edit | `bodies/GenericBodies.tsx`: import it and add one entry to `PREVIEW_RENDERERS`: `trading_agents_run: (preview) => <TradingAgentsRunPreview preview={preview} />`. `CardBody.tsx` and `CardBodyV2.tsx` both call `ToolPreviewBody`, so both and the read-only diff are covered by this one line. |
| Create | `approval/fixtures/tradingAgentsRun.ts`: three payloads (investments level with 4 positions + one FX-converted + one omitted average price; position level for a ticker not held; ticker-only on a subscription CLI with `estimated_cost` "plan-included…"). The gallery (`/?gallery=cards`) and `anatomy.test.tsx` pick them up. |
| Create | `bodies/TradingAgentsBodies.test.tsx` (assertions in 5.5) |
| Check (edit only if needed) | `CardHeader.tsx`/`CardHeaderV2.tsx` and `cardTypes.ts`: confirm the header reads "Start TradingAgents analysis" with the ticker as the subject pill, not a bare tool id; if a tool-title map exists, add this tool there. No other header change. |

### 5.2 Layout (chat-panel width; anatomy order)

```
Start TradingAgents analysis   ACME                      ← header (CardHeader, unchanged)
Research as of 2026-11-19 with 4 analysts                ← ApprovalLead  (no figure: the estimate is prose)
Research only · never places an order                    ← ApprovalMeta
Estimated cost       About $0.42 ($0.30 to $0.60)        ← ApprovalFact tone=strong  (estimated_cost, capitalised, verbatim)
Run limit            $2.00, at most 150 model calls      ← per_run_cap
This month           $3.10 spent of a $20.00 cap         ← this_month
Deep model           claude-x · Anthropic                ← split of "ref via transport"
Quick model          claude-y · Anthropic
Shared with          Anthropic                           ← unique provider labels (title = full strings)
Data sources         Receive the ticker and date only…   ← data_vendors verbatim
⚠ Your real cash and position sizes go to Anthropic.     ← ApprovalNote tone=warn (portfolio is an object)
▸ Shared: cash $9,400.00 + 4 positions                   ← ApprovalDetails (closed), summary carries the decision fact
    Cash                       $9,400.00   (title = disclosure.cash_basis)
    ApprovalTable  Ticker | Quantity | Avg price   (numeric cols 1,2; analysed ticker row tone strong)
    ApprovalMeta   Converted at today's rates: EUR→USD 1.08 (cash) · …   (only when fx_converted)
    ApprovalMeta   Average price left out for XYZ (no cost basis or no rate)  (only when listed)
▸ Never shared · account names, institutions…            ← ApprovalDetails (closed): ApprovalMeta(never_sent)
▸ Raw arguments                                           ← ApprovalDetails + ApprovalCode(raw_arguments)
[Allow once] [Always allow]                  Deny         ← GrantActions (unchanged)
```

Ticker-only (`shared_with_the_ai_provider` is a string): no warn note, no "Shared" details;
instead `ApprovalNote tone="muted"`: "Nothing about your holdings is shared. Ticker and date only."
and the Shared-with fact still names the provider ("Anthropic receives the ticker and date").

Phone / narrow dock: facts wrap, value right-aligned (primitive behavior); `ApprovalTable`
scrolls sideways with its edge shadow; nothing here sets a width. `GrantActions` collapses to the
`[Allow once ▾]` split on its own.

### 5.3 Data mapping (`TaRunPreviewData`, real backend keys)

| Key | Rendered as |
|---|---|
| `ticker`, `trade_date`, `analysts` | Lead: "Research as of {trade_date} with {n} analysts" (n = `analysts.length`; names in the lead only when <= 2: "Market and News"). The ticker is the header's subject: never repeated in the lead. |
| `estimated_cost` (prose from `_cost_line`) | Fact "Estimated cost", first letter capitalised, otherwise verbatim — never parsed into numbers. `tone="warn"` when it contains "no known price"; plan text ("plan-included: …") shown as written. |
| `per_run_cap`, `this_month` | Facts "Run limit", "This month", verbatim |
| `deep_model`, `quick_model` ("`<provider_key>:<model> via <transport>`") | Split on " via ": model = `bareModelId(ref)`, provider = `providerLabelFor(key) ?? key`, transport -> "Claude Code plan" / "Codex plan" / "Antigravity plan" for the three CLI ids, otherwise omitted (an API provider is just its name). Row text `"{model} · {provider}"` (+ " · {plan}" for CLI); `title` = the raw string. If deep and quick strings are identical, ONE row labelled "Model". A string that does not match the pattern renders verbatim. |
| `context_level` | not shown as a row (the Shared details summary states it); used only to choose the ticker-only branch together with the type of the next key |
| `shared_with_the_ai_provider` | object -> warn note + Shared details; string -> muted note (above) |
| `disclosure` | `cash_basis` -> `title` on the Cash fact; `fx_converted` / `average_price_omitted` / `partial_cost` / `cash_omitted` -> the meta lines in 5.2 ("Cash left out: {cash_omitted}" when set); `never_sent` -> the second Details |
| `data_vendors` | Fact "Data sources", verbatim |
| `raw_arguments` | "Raw arguments" details |

Money: `fCurrency(amount, portfolio.currency)`; quantity `fQty`; average price
`fUnitPrice(price, …)`/currency, `null` -> "n/a". Cash `null` -> the summary reads "cash unavailable".

Summary strings: investments -> "Shared: cash {fCurrency} + {n} position(s)" (n = 0 -> "Shared: cash {x}, no
positions"); position -> "Shared: cash {x} + your {ticker} position" or "+ no position (you don't hold {ticker})".

Defensive: every access goes through `isRecord`/type guards. A payload missing `ticker` or
`disclosure` renders `ApprovalNote tone="muted"` "No preview available" plus Raw arguments
(the same safety net `GenericPreviewBody` gives: a card is never blank, never throws).

Empty/loading/error: none (the payload is complete at stage time; approve/deny states belong to
`ChangePreview`). Backend refusals (`BudgetExceededError`, `disclaimer_required`, unavailable)
arrive before a card exists as the tool's failure text; nothing to render here.

### 5.4 Accessibility and style rules

- Only `--ac-text` / `--ac-caps` sizes; emphasis by `tone`. No inline `style`, no legacy class (`anatomy.test.tsx` fails otherwise).
- The warn note names the recipient in words ("go to Anthropic"), so the consent fact is text, not color.
- `ApprovalDetails` is native `<details>`: keyboard and screen-reader semantics are free. The summary is the decision-relevant sentence, not a label.
- Table: `ApprovalTable` already emits `<th>`; numeric columns right-aligned, never wrapped.
- `title` on facts carries explanations (hover/long-press); nothing essential lives only in a `title`: the cash basis also appears in the setup page and the docs.

### 5.5 Tests the build adds

Renders: investments fixture shows the warn note with the provider label, the Shared summary with
cash and count, every position row, the FX line; ticker-only shows no table and the muted note;
estimate string appears verbatim (capitalised); deep/quick collapse to "Model" when equal; a
payload with `disclosure` missing shows "No preview available"; `anatomy.test.tsx` and
`approvalCardCss.test.ts` pass with the new fixtures.

---

## 6. New `className` list (every one defined in `frontend/src/index.css`; `npm run lint:classes` must pass)

One block, `/* ── TradingAgents ── */`, in the same `@layer`/nesting style as the `asd-*` and `ui-*` rules.
Tokens only: spacing `--space-*`, type `--text-*`, radius `--radius-*`, colors `--good --bad --warn
--muted --faint --ink --accent --line --surface --surface2` (the same names `.ui-badge--*`, `.panel`
use). No raw px, hex or font-size. Dynamic widths/heights (`%`) are set inline from data, the way
`Meter` and `rd-bar-track` do.

| Class | Used by | Definition (intent) |
|---|---|---|
| `ta-stages` | Progress | vertical list, `gap: var(--space-3)` |
| `ta-stage` | Progress | grid `auto 1fr auto`, `gap: var(--space-4)`, align center; at phone the time column wraps under the label |
| `ta-stage--pending` / `--active` / `--done` | Progress | text `--faint` / `--ink` / `--muted`; mark color `--faint` / `--accent` / `--good` |
| `ta-stage__mark` | Progress | fixed square glyph box (`inline-size`/`block-size: var(--space-6)`), centered |
| `ta-stage__label` | Progress | `--text-sm`, weight 600 on `--active` |
| `ta-stage__sub` | Progress | `--text-xs`, `--faint`, wraps |
| `ta-stage__time` | Progress | `--text-xs`, `--faint`, `font-variant-numeric: tabular-nums` |
| `ta-rating` | Rating pill | inline-flex, `padding: var(--space-1) var(--space-4)`, `border-radius: var(--radius-md)`, `--text-sm`, weight 600 (same metrics as `.ui-badge`) |
| `ta-rating--buy` `--overweight` `--hold` `--underweight` `--sell` `--unclear` | Rating pill | colors per the table in 2.4 (`color-mix` of the token, as `.ui-badge--pos`); `--unclear`: `border: 1px dashed var(--line)`, no fill, `--muted` |
| `ta-rating__label` | Rating pill | the word; never hidden |
| `ta-scale` | Rating | five-cell strip beside the pill, `gap: var(--space-0)`, `aria-hidden` |
| `ta-scale__cell` / `ta-scale__cell--on` | Rating | `--surface2` cell / `currentColor` of the parent rating when on; `--compact` variant not needed: the scale is omitted when `compact` |
| `ta-result` | Report | flex row wrap, `gap: var(--space-5)`, align center |
| `ta-decision` | Report | prose block: `--text-sm`, `line-height` from the body token, `max-inline-size: 70ch`, markdown children spaced with `--space-3` |
| `ta-disclose` | Report accordion, "What will be shared" | `<details>` with `border-top: 1px solid var(--line)`; `> summary` list-style none, padding `var(--space-4) 0`, chevron via `::before`, min tap height from the kit tap token, visible focus outline; `[open] > summary::before` rotates 90 deg (only under `prefers-reduced-motion: no-preference`) |
| `ta-disclose__body` | same | `padding-bottom: var(--space-5)` |
| `ta-report` | Report accordion body | markdown typography (p, ul/ol, h3-h5 at `--text-sm`/`--text-base`, code), `table { display:block; overflow-x:auto }`, `min-width:0` so it cannot widen the card on a phone |
| `ta-footer` | Report | `--text-xs`, `--faint`, wrapped paragraph |
| `ta-disclaimer` | A/C/D/E | `--text-xs`, `--faint`, `margin-top: var(--space-5)`; never collapsible |
| `ta-history` | History | list, `border-top: 1px solid var(--line)` |
| `ta-history__row` | History | full-width `<button>`: grid `auto 1fr auto`, gap `--space-4`, padding `var(--space-3) 0`, transparent bg, tap-target min height, focus ring |
| `ta-history__row--current` | History | `--surface2` background |
| `ta-shared` | Shared | `Stack`-like column, `gap: var(--space-4)` |
| `ta-shared__table` | Shared | compact `<table>`, `--text-xs`, `border-collapse`, rules `--line`, wrapped in `overflow-x:auto` |
| `ta-shared__num` | Shared | right-aligned, tabular-nums, `white-space: nowrap` |
| `ta-shared__self` | Shared | weight 600 for the analysed ticker's row |
| `ta-section` | Setup | `border-top: 1px solid var(--line)`, `padding-top: var(--space-7)`; the first section has none (`:first-child`) |
| `ta-notice` | Setup | `--surface2` block, `border-radius: var(--radius-md)`, `padding: var(--space-5)`, `--text-sm` |
| `ta-log` | Setup install error | monospace (`var(--font-mono)`), `--text-xs`, `max-block-size` with `overflow:auto`, `white-space: pre-wrap`, `--surface2`, `--radius-md`, padding `--space-4` |
| `ta-mono` | Setup (commit id) | monospace face only |
| `ta-recipient` | Setup recipients | bordered block (`1px solid var(--line)`, `--radius-md`, padding `--space-5`), equal height inside `Grid` |
| `ta-recipient__who` | same | `--text-sm`, weight 600 |
| `ta-recipient__what` | same | `--text-xs`, `--muted`, spacing `--space-2` between receives / never lines |
| `ta-daybars` | Cost | flex row, `align-items:flex-end`, `gap: var(--space-0)`, `block-size: var(--space-9)`, `role="img"` |
| `ta-daybars__bar` | Cost | `flex:1`, `min-inline-size: 0`, `background: var(--accent)` at reduced opacity via `color-mix`, `border-radius: var(--radius-sm)`; height inline `%` |

Not new (existing, reused as-is): `rd-stats`, `rd-cost-stats`, `rd-stat*`, `rd-bars`, `rd-bar*`,
`rd-help`, `rd-mono`, `rd-service-cost-total`, `rd-service-cost-table*`, `rd-service-cost-empty`
(`settings.css`, scoped under `.onw-settings`: the cost panel mounts inside it), every `ac-*` class (approval
card), `ui-*` and `panel` (kit).

---

## 7. What NOT to do

- **No hand-rolled controls.** Selects/pickers are `ModelPicker`, `SegmentedControl`, `RadioGroup`, `CheckboxGroup`, `Checkbox`, `NumberField`, `PasswordField`; buttons are `Button`; confirm is the kit `ConfirmDialog`; states are `EmptyState`/`Loading`/`ErrorState`/`Skeleton`. The only new interactive markup is `<details>`/history `<button>` (justified in 2.4).
- **No second page heading.** The card's heading is a `Panel title` (`h2`); the setup form's section headings are `h3` inside the connector detail's own `h1/h2`. The asset view's `h1` is untouched.
- **No new tab, route, nav item or `App.tsx` change.** No `Container`/`PageHeader` in any of these (they are all embedded surfaces).
- **No raw px / hex / font-size.** Not even in the cost panel's bars; no copy of the `12px` literals in `settings.css`. Approval card: no new size, no inline `style`.
- **No client-side rating-vs-thesis logic** (stance comes from `thesis_judgment`, G2), **no client math on money** (every figure shown is a backend number; only formatting happens in the browser), **no parsing of `estimated_cost`/`this_month` prose**.
- **No second implementation of a shared fact**: month spend is `status.month_spend_usd` / `summary.month.spend_usd`, caps are `status.caps`; the card and the cost panel read them, never sum runs themselves.
- **No new component that duplicates a shared one**: do not re-create `SummaryBar`/`StatGrid` for the cost tiles (the settings panel uses `rd-stat`, its peers' idiom), `Meter` for bars, `Sparkline` (its up/down green/red coloring is wrong for spend, which is why `DayBars` is a plain strip), or an approval table.
- **No second place to accept the notice, install, or set caps**: the card and the cost panel only link to the connector page.
- **No `localStorage` for anything** (run selection is component state; data is `useQueryCache`).
- **No unsanitised report HTML.** Markdown only, `skipHtml`, `SAFE_MARKDOWN_COMPONENTS`.
- **No crypto, property, bond or tickerless card.** Equity with a ticker only.
- **No card when the flag is off, and no blank/`undefined` flag guess** (`useFlag` `undefined` renders nothing).
- **Never offer "Buy"/"Sell" as an action.** The rating is a label, not a button; there is no order UI anywhere in this feature.
- **No PII in fixtures/tests:** ticker ACME, synthetic amounts only.

---

## 8. Files the build touches + build order

Create: `types/tradingAgents.ts`, `hooks/useTradingAgents.ts`; `components/intel/TradingAgents{Card,RunForm,Progress,Report,Rating,Shared,History}.tsx`; `components/tabs/connectors/TradingAgents{Section,Install,Recipients}.tsx`; `components/tabs/settings/sections/TradingAgentsCost{Panel,Parts}.tsx`; `components/chat/approval/bodies/TradingAgentsBodies.tsx` (+ `.test.tsx`) and `approval/fixtures/tradingAgentsRun.ts`; a vitest per new component (both flag values via `overrideFlagsForTests`, every state of 2.3, `TA_NEVER_SENT` length).

Edit: `components/intel/AssetDetailView.tsx` (one insertion), `connectors/ConnectorConfig.tsx`, `connectors/catalog.ts` + `services/connector_directory.json` (via the sync script), `settings/sections/AiUsageSection.tsx` (mount + `SURFACE_LABELS`), `approval/bodies/GenericBodies.tsx` (one map entry), `frontend/src/index.css` (`ta-*` block), docs under `docs/product/` per the docs-impact rule.

Backend (G1-G3): `GET/PUT /api/trading-agents/config`, `thesis_judgment` on the run payload, `start`/`end` on `/api/cost/trading-agents`. No other backend change.

Build order: (1) raise G1-G3 with the backend owner and get G1 first (setup cannot be built without it); (2) `types/tradingAgents.ts` + `hooks/useTradingAgents.ts` with the constants (stage groups, role/status labels, limits) and the `ta-*` CSS block; (3) approval renderer + fixtures + test (smallest, fully independent, reviewable in `/?gallery=cards`); (4) connector entry + `TradingAgentsSection` so the owner can accept the notice, install and set models (this also makes real runs possible for QA); (5) `TradingAgentsRating`, `Shared`, then the card states in this order: not-available line, form + estimate + shared preview, progress, report, history, failed/stopped; make every card component presentational (props in, no hooks) except `TradingAgentsCard` so fixtures can render each state; (6) the cost panel and the By-surface label; (7) docs; (8) `npm run lint:classes`, typecheck, vitest, then the BLOCKING gate in `docs/UI_QUALITY_GATE.md`: ui-designer build-conformance verify (visual-qa at 640/1024/1440, chat open and closed, flag on in the disposable instance) plus qa-engineer interaction verify (accept notice, install, save settings, start/cancel/retry a run against the fake worker, delete a run, approve and deny the card).

## 9. Conformance checklist (what VERIFY-BUILD scores, region by region)

| Region | PASS means |
|---|---|
| Asset card placement | Analysis tab only, after Key Issues block, before Estimates; absent on Overview/Position tabs, crypto, flag off |
| Card states | each row of 2.3 reachable from fixtures; exactly one primary action per state; disclaimer line visible in A/C/D |
| Rating | six variants distinct without color (word + scale position); Unclear is neutral/dashed and shows no scale cell |
| Phone (640) | no horizontal overflow; Start and Cancel full width; report accordion tables scroll inside `ta-report` |
| Setup | eight sections in order; footer Save arms only on change; notice/install are immediate; recipients names the provider(s) and the vendors; no LLM key field anywhere |
| Cost | panel between Search & fetch and Model telemetry; "TradingAgents" in By surface only; plan-only user sees $0.00 + plan tile; unpriced warning when > 0; no own period control |
| Approval card | Dedicated body (no "Shared with the ai provider · 3 fields" fold); warn note names the recipient; Shared summary carries cash + count; only `ac-*` classes; passes `anatomy.test.tsx` |
| Cross-cutting | no raw px/hex; `lint:classes` clean; one page heading; kit primitives only (list in section 7); empty/loading/error present in every data region |

---

## 10. WP10 — condensed card + the take's TradingAgents chip

Status: added 2026-11-20 (owner: the card is too verbose; the take now uses a run, spec §16).
Frontend only. Backend contract: `trading_agents_input` on the asset detail (below); no other
backend change.

### 10.1 Condensed card (State C, done)

Default view is the rating and a short decision; everything else is behind one control.

```
┌ Panel title="TradingAgents · research only"      actions: [ Run new analysis ] ───┐
│ [ Overweight ]  ta-scale                                                         │
│ Text xs faint: ACME · as of 2026-11-19 · 6 min ago · Your thesis chip (if any)   │
│ ta-decision (≤3 sentences / ≤400 chars of decision_text)  [ Show more ]          │
│ Button ghost sm: [ Show full analysis ]                                          │
│ ta-disclaimer (one line, always visible)                                         │
└──────────────────────────────────────────────────────────────────────────────────┘
```

- **Condensed decision.** First three sentences of `decision_text` (split on `. `, `? `, `! `
  and newlines), at most 400 characters, cut at a word boundary with "…". Pure helper in
  `utils/` with a vitest (no sentence ⇒ hard cut at 400). "Show more" shows the full markdown
  decision in place; it is a `Button variant="ghost" size="sm"` with `aria-expanded`.
- **"Show full analysis"** is the one control that reveals everything else: the reports
  accordion, "What was shared", History and the models/cost footer. Collapsed by default,
  state is kept in memory per ticker for the page load (no `localStorage`); the take's chip opens it.
  Expanded, those regions render exactly as in 2.4 (no redesign of their contents).
- **Kept visible always:** rating pill + scale, as-of/age line, thesis chip, disclaimer line,
  "Run new analysis".
- **Footer line moves inside** the expanded area; the only cost shown collapsed is none.
- States A, B, D, E are unchanged except the disclaimer stays one line. Running (B) keeps the
  stage list; it is not condensed.
- Phone: same order; "Show more" / "Show full analysis" are full width.
- Copy: "Show more" / "Show less"; "Show full analysis" / "Hide full analysis".
- Accessibility: both controls are buttons with `aria-expanded` and `aria-controls`; the
  expanded region is a labelled `section`.

### 10.2 The take's chip

`PositionVerdictLine.tsx` / the narrative block gain a small chip beside "Lucius's take":

`Includes TradingAgents · Hold · 3 days ago`

- Source: `data.trading_agents_input` (`{run_id, signal, trade_date, age_days}`), read-only.
  **Hidden when null** and when the TradingAgents flag is off (`useFlag` `undefined`/`false`).
- Text: `Includes TradingAgents · {label} · {age}`; label is the rating label map of 2.4
  (`TradingAgentsRating`'s words, never the raw REVIEW); age is `0 → "today"`,
  `1 → "yesterday"`, else `"{n} days ago"`. A run older than 3 days adds a faint
  "(may be stale)".
- It is a link (`Tag`/`Badge` kit primitive, no new styling beyond a `ta-chip` class) that
  selects that run in the card: scrolls to `TradingAgentsCard` and sets its featured run id to
  `run_id`. If the run was deleted the card shows the latest run and the chip stays a plain
  label.
- Never offers an order or a rating-as-action; never shown on non-equity or tickerless pages
  (the field is null there).

### 10.3 Types

`frontend/src/types/assetDetail.ts`:

```ts
export interface TradingAgentsInput {
  run_id: string
  signal: TaSignal              // 'Buy' | 'Overweight' | 'Hold' | 'Underweight' | 'Sell' | 'REVIEW'
  trade_date: string            // ISO date
  age_days: number
}
// AssetDetail gains:  trading_agents_input: TradingAgentsInput | null
```

### 10.4 States and tests

| State | Renders |
|---|---|
| Done, collapsed (default) | rating, as-of line, condensed decision, two buttons, disclaimer |
| Done, decision expanded | full decision markdown, "Show less" |
| Done, full analysis open | + reports, shared, history, footer |
| Decision shorter than 400 chars / 3 sentences | no "Show more" |
| Chip with input | chip + link; without (`null`) nothing, no gap |

Vitest: condensing helper (3 sentences, 400 cap, no punctuation, markdown stripped of headings),
card collapsed/expanded states, chip present/absent/stale, both flag values, `npm run lint:classes`
for `ta-chip`.

## 11. WP11: one view, and the way to run it from the take (frontend)

Owner feedback after 10: the take did not offer a run (the Analysis tab was the only door), and
"Lucius's take: Hold" beside "TradingAgents: Sell" read as two opinions. TradingAgents is the
stock research; Lucius's take is the position overlay on it. One view.

### 11.1 The take's row

`TradingAgentsTakeRow` replaces the chip in the take section (under `PositionVerdictLine`, above
the not-advice line). Shown only for an equity with a ticker and the flag on; one row, at most two
lines on a phone. Reads `useTaStatus`, `useTaRuns(ticker, 5)` (the card's own cache entries), and
`trading_agents_input`. First match wins:

| | State | Row |
|---|---|---|
| a | newest run for the ticker is queued/running | "TradingAgents research · {stage} · {elapsed}", **Cancel**, **View** (focuses the card). Cancel failure shows the server sentence |
| b | a usable run exists (done, rated, trade date at most 14 days) and the take used it (`trading_agents_input.run_id`) | the research line, 11.2 |
| c | a usable run exists the take has not used | "TradingAgents result ready — updating this take…" (status, no button); after 2 minutes **Refresh this take**, which only revalidates the asset detail |
| d | availability ready, notice accepted, no usable run | **Add TradingAgents research** (secondary, small), cost hint from `/estimate` ("about $0.42", "uses your Claude Code plan", "cost unknown"), a **What's shared** disclosure (`TradingAgentsShared`, `context-preview`). The click calls `startTaRun(ticker, status.analysts, status.context_level)`: the configured defaults, never a level chosen in the browser. A refusal (budget, queue, unpriced model) shows inline; success shows (a) |
| e | not ready (connector off/paused, not installed, needs reinstall, model unavailable, notice not accepted) | one quiet line from the availability, with **Set up TradingAgents** to `/connectors?connector=trading_agents`; installing has no link |

Usable and "used" are the server's: the browser only compares ids and the 14-day window.

### 11.2 One view, not two verdicts

- State (b) is `Research view: {Label} · {age}`, then ` — {view}` when the server sent `view`, then
  "may be outdated" past 3 days. Muted small text on the take, no rating pill, no colour per
  rating; the take's own call (Hold, Wait, Trim) stays the one large element. It opens the card.
- `trading_agents_input` gains `view?`, `held?`, `allowed_actions?`. `view` is the server's
  one-line reading of the signal for this position ("negative outlook, not a buy now"). Absent, the
  browser derives nothing: the line is `Research view: Sell · today`.
- The card's title is "Research view · TradingAgents". Under the rating it shows `view` (held or
  not) when the take used that run. The full rating pill lives on the card only; the take never
  repeats it, so nothing shouts the rating twice.

### 11.3 Tests

`TradingAgentsTakeRow.test.tsx` (states a to e, flag on/off, start called once with the configured
analysts and level and not before the click, Cancel, inline errors, stale and 14-day windows, the
two-minute refresh, one row only), `AssetDetailView.taInput.test.tsx` (the line with/without `view`,
stale suffix, ages, crypto and no-ticker hidden, `view` reaching the card),
`TradingAgentsCard.test.tsx` (the reading under the rating).
