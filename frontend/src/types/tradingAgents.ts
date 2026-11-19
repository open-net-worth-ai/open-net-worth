// TradingAgents (flag `trading_agents`): the JSON of /api/trading-agents/* and
// /api/cost/trading-agents, typed from backend/services/trading_agents/{queries,costs,budget,
// context,settings}.py. A run is research, never an order.

export type TaAvailabilityStatus =
  | 'flag_off'
  | 'connector_off'
  | 'connector_paused'
  | 'not_installed'
  | 'needs_reinstall'
  | 'installing'
  | 'model_unavailable'
  | 'ready'

export type TaInstallStatus = 'absent' | 'installing' | 'ready' | 'failed'
export type TaContextLevel = 'investments' | 'position' | 'ticker'
export type TaAnalyst = 'market' | 'social' | 'news' | 'fundamentals'
export type TaSignal = 'Buy' | 'Overweight' | 'Hold' | 'Underweight' | 'Sell' | 'REVIEW'
export type TaRunStatus =
  | 'queued'
  | 'running'
  | 'done'
  | 'failed'
  | 'cancelled'
  | 'stopped_budget'
  | 'stopped_calls'
  | 'timed_out'
  | 'interrupted'
export type TaRequester = 'user' | 'lucius' | 'automation'

/** GET /api/trading-agents/status */
export interface TaStatus {
  availability: { status: TaAvailabilityStatus; message: string }
  install: { status: TaInstallStatus; version: string | null; error: string | null }
  /** "provider_key:model" refs and their transports; null when they cannot be resolved. */
  models: { deep: string; quick: string; transports: { deep: string; quick: string } } | null
  caps: { per_run_cap_usd: number; monthly_cap_usd: number; max_llm_calls: number }
  context_level: TaContextLevel
  analysts: TaAnalyst[]
  month_spend_usd: number
  queue_length: number
  disclaimer_accepted: boolean
}

/** POST /api/trading-agents/estimate */
export interface TaEstimate {
  estimate_usd: number
  low_usd: number
  high_usd: number
  calls: number
  basis: 'history' | 'static'
  sample_size: number
  plan_included: boolean
  /** False when a metered model has no known price, so the figure understates. */
  priced: boolean
  /** Models with no known price: a metered run on one is refused. */
  unpriced_models: string[]
}

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
  cash_floored: string | null
}

/** GET /api/trading-agents/context-preview, and `run.context_sent`. */
export interface TaContext {
  ticker: string
  portfolio: TaPortfolio | null
  disclosure: TaDisclosure
}

export interface TaCostPart {
  calls: number
  input_tokens: number
  output_tokens: number
  cost_usd: number
  plan_included_calls: number
}

export type TaReportField =
  | 'market_report'
  | 'sentiment_report'
  | 'news_report'
  | 'fundamentals_report'
  | 'bull_history'
  | 'bear_history'
  | 'investment_plan'
  | 'trader_investment_plan'
  | 'aggressive_history'
  | 'conservative_history'
  | 'neutral_history'

/** A row of GET /runs (no context_sent, progress or reports). */
export interface TaRunSummary {
  id: string
  ticker: string
  instrument_id: string | null
  trade_date: string
  status: TaRunStatus
  requested_by: TaRequester
  session_id: string | null
  analysts: TaAnalyst[]
  config_snapshot: {
    deep?: string
    quick?: string
    transports?: { deep?: string; quick?: string }
    rounds?: { debate: number; risk: number; tools: number }
  }
  context_level: TaContextLevel
  signal: TaSignal | null
  decision_text: string | null
  llm_calls: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_creation_tokens: number
  cost_usd: number
  plan_included_calls: number
  unpriced_calls: number
  cost_by_role: Record<string, TaCostPart>
  cost_by_model: Record<string, TaCostPart>
  estimate_usd: number | null
  cap_usd: number
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

/** What the server computed about the owner's thesis against the live price. Present only
 *  while a thesis exists; the browser never recomputes the stance. */
export interface TaThesisJudgment {
  role: string | null
  reason: string
  holding_period: string
  breaks_if: string
  levels: Record<string, unknown>
  stance: string
  position: string
  /** The stance in the owner's words ("inside your buy zone"). */
  read: string
  /** The thesis cited in one line. */
  cite: string
}

export interface TaRun extends TaRunSummary {
  context_sent: TaContext
  progress: { stage: string; status: 'started' | 'finished'; at: string }[]
  reports: { fields: Partial<Record<TaReportField, string>>; truncated: TaReportField[] } | null
  thesis_judgment?: TaThesisJudgment
}

export interface TaRunList {
  runs: TaRunSummary[]
  total: number
}

/** GET/PUT /api/trading-agents/config */
export interface TaConfig {
  deep_model: string | null
  quick_model: string | null
  analysts: TaAnalyst[]
  max_debate_rounds: number
  max_risk_rounds: number
  max_tool_rounds: number
  max_llm_calls: number
  bridge_concurrency: number | null
  per_run_cap_usd: number
  monthly_cap_usd: number
  context_level: TaContextLevel
  output_language: string
  sec_edgar_user_agent: string
  has_alpha_vantage_api_key: boolean
  has_fred_api_key: boolean
}

/** The PUT body: every key optional; a blank secret keeps, `null` clears or resets. */
export type TaConfigPatch = Partial<
  Omit<TaConfig, 'has_alpha_vantage_api_key' | 'has_fred_api_key'>
> & { alpha_vantage_api_key?: string | null; fred_api_key?: string | null }

/** GET /api/cost/trading-agents */
export interface TaCostSummary {
  window: { days: number; start: string | null; end: string | null }
  totals: {
    runs: number
    done: number
    failed: number
    stopped: number
    active: number
    metered_usd: number
    avg_cost_per_run_usd: number
    median_cost_per_run_usd: number
    calls: number
    input_tokens: number
    output_tokens: number
    cache_read_tokens: number
    cache_creation_tokens: number
    plan_included_calls: number
    unpriced_calls: number
  }
  month: {
    spend_usd: number
    cap_usd: number
    pct_used: number | null
    per_run_cap_usd: number
    max_llm_calls: number
  }
  by_role: Record<string, TaCostPart & { runs: number }>
  by_model: Record<string, TaCostPart & { runs: number; transport: string | null }>
  by_ticker: (TaCostPart & { ticker: string; runs: number })[]
  by_day: (TaCostPart & { date: string; runs: number })[]
  recent: {
    id: string
    created_at: string
    ticker: string
    trade_date: string
    signal: TaSignal | null
    status: TaRunStatus
    calls: number
    input_tokens: number
    output_tokens: number
    cost_usd: number
    estimate_usd: number | null
    plan_included_calls: number
    unpriced_calls: number
  }[]
}

/** The approval card's preview data (ai/tools/trading_agents._StartPreview.build). */
export interface TaRunPreviewData {
  ticker: string
  trade_date: string
  analysts: TaAnalyst[]
  /** "provider_key:model via transport", a display string. */
  deep_model: string
  quick_model: string
  /** Prose from `_cost_line`; never parsed. */
  estimated_cost: string
  per_run_cap: string
  this_month: string
  context_level: TaContextLevel
  /** The portfolio, or "nothing: ticker and date only". */
  shared_with_the_ai_provider: TaPortfolio | string
  disclosure: TaDisclosure
  data_vendors: string
}

export type TaStageId = 'analysts' | 'research' | 'trader' | 'risk' | 'pm'
