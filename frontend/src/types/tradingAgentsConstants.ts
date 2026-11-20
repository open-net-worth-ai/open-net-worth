// TradingAgents (flag `trading_agents`): labels, ranges and the stage map the surfaces share.
// The JSON shapes they key on are in ./tradingAgents.
import type {
  TaAnalyst,
  TaContextLevel,
  TaReportField,
  TaRunStatus,
  TaSignal,
  TaStageId,
} from './tradingAgents'

export const TA_SIGNAL_LABEL: Record<TaSignal, string> = {
  Buy: 'Buy',
  Overweight: 'Overweight',
  Hold: 'Hold',
  Underweight: 'Underweight',
  Sell: 'Sell',
  REVIEW: 'Unclear',
}

/** The five-step scale, lowest to highest; REVIEW is off it. */
export const TA_SIGNAL_ORDER: readonly TaSignal[] = [
  'Sell',
  'Underweight',
  'Hold',
  'Overweight',
  'Buy',
]

/** The providers that run a locally installed CLI on the owner's subscription (backend
 *  `CLI_TRANSPORTS`): their calls are plan-included, in quota rather than dollars. */
export const TA_CLI_PROVIDERS: ReadonlySet<string> = new Set([
  'claude_cli',
  'codex_cli',
  'antigravity_cli',
])

export const TA_CLI_LABEL: Record<string, string> = {
  claude_cli: 'Claude Code',
  codex_cli: 'Codex',
  antigravity_cli: 'Antigravity',
}

export const TA_CONTEXT_LABEL: Record<TaContextLevel, string> = {
  investments: 'Investments',
  position: 'This position',
  ticker: 'Ticker only',
}

export const TA_ANALYST_LABEL: Record<TaAnalyst, string> = {
  market: 'Market',
  social: 'Sentiment',
  news: 'News',
  fundamentals: 'Fundamentals',
}

export const TA_ANALYSTS: readonly TaAnalyst[] = ['market', 'social', 'news', 'fundamentals']

/** The five stages of a run, in order. */
export const TA_STAGES: readonly { id: TaStageId; label: string; active: string }[] = [
  { id: 'analysts', label: 'Analysts', active: 'Reading data…' },
  { id: 'research', label: 'Bull vs Bear', active: 'Debating' },
  { id: 'trader', label: 'Trader', active: 'Drafting a plan' },
  { id: 'risk', label: 'Risk debate', active: 'Debating' },
  { id: 'pm', label: 'Portfolio manager', active: 'Deciding' },
]

/** The ONE graph-node to stage map: the top-level LangGraph nodes of the pinned commit
 *  (tests/fixtures/trading_agents/ta_nodes.json). A node in no group is ignored. */
export const TA_STAGE_OF_NODE: Record<string, TaStageId> = {
  'Market Analyst': 'analysts',
  'Sentiment Analyst': 'analysts',
  'News Analyst': 'analysts',
  'Fundamentals Analyst': 'analysts',
  'Bull Researcher': 'research',
  'Bear Researcher': 'research',
  'Research Manager': 'research',
  Trader: 'trader',
  'Aggressive Analyst': 'risk',
  'Conservative Analyst': 'risk',
  'Neutral Analyst': 'risk',
  'Portfolio Manager': 'pm',
}

export interface TaReportSection {
  id: string
  label: string
  fields: TaReportField[]
}

export const TA_REPORT_SECTIONS: readonly TaReportSection[] = [
  { id: 'market', label: 'Market', fields: ['market_report'] },
  { id: 'sentiment', label: 'Sentiment', fields: ['sentiment_report'] },
  { id: 'news', label: 'News', fields: ['news_report'] },
  { id: 'fundamentals', label: 'Fundamentals', fields: ['fundamentals_report'] },
  { id: 'debate', label: 'Bull vs Bear', fields: ['bull_history', 'bear_history'] },
  { id: 'plan', label: 'Trader plan', fields: ['investment_plan', 'trader_investment_plan'] },
  {
    id: 'risk',
    label: 'Risk debate',
    fields: ['aggressive_history', 'conservative_history', 'neutral_history'],
  },
]

export const TA_FIELD_LABEL: Record<TaReportField, string> = {
  market_report: 'Market',
  sentiment_report: 'Sentiment',
  news_report: 'News',
  fundamentals_report: 'Fundamentals',
  bull_history: 'Bull case',
  bear_history: 'Bear case',
  investment_plan: 'Research manager plan',
  trader_investment_plan: 'Trader plan',
  aggressive_history: 'Aggressive view',
  conservative_history: 'Conservative view',
  neutral_history: 'Neutral view',
}

export const TA_STATUS_LABEL: Record<TaRunStatus, string> = {
  queued: 'Queued',
  running: 'Running',
  done: 'Done',
  failed: 'Failed',
  cancelled: 'Cancelled',
  stopped_budget: 'Stopped: budget',
  stopped_calls: 'Stopped: call limit',
  timed_out: 'Timed out',
  interrupted: 'Interrupted',
}

export const TA_ROLE_LABEL: Record<string, string> = {
  analyst_market: 'Market analyst',
  analyst_sentiment: 'Sentiment analyst',
  analyst_news: 'News analyst',
  analyst_fundamentals: 'Fundamentals analyst',
  research_debate: 'Bull vs Bear debate',
  research_manager: 'Research manager',
  trader: 'Trader',
  risk_debate: 'Risk debate',
  portfolio_manager: 'Portfolio manager',
  memory: 'Memory',
  other: 'Other',
}

/** The setup form's ranges, mirrored from services/trading_agents/settings.py. */
export const TA_LIMITS = {
  max_debate_rounds: { min: 1, max: 3 },
  max_risk_rounds: { min: 1, max: 3 },
  max_tool_rounds: { min: 2, max: 20 },
  max_llm_calls: { min: 20, max: 600 },
  bridge_concurrency: { min: 1, max: 8 },
  per_run_cap_usd: { min: 0.1, max: 50 },
  monthly_cap_usd: { min: 0, max: 1000 },
  output_language: { max: 32 },
  sec_edgar_user_agent: { max: 200 },
} as const

/** A static copy of `context.NEVER_SENT`: a change there prompts a review of the setup copy. */
export const TA_NEVER_SENT: readonly string[] = [
  'account names',
  'institutions and account numbers',
  'transactions and payees',
  'income and budgets',
  'net worth and liabilities',
  'retirement balances',
  'owned assets',
  'investment theses, memory and profile',
]

/** The statuses of a run still in flight. */
export const TA_ACTIVE_STATUSES: readonly TaRunStatus[] = ['queued', 'running']

/** The asset card lists this many runs at most; older ones are in Settings, Cost & Performance. */
export const TA_HISTORY_MAX = 20

/** A run older than this many days is flagged as possibly outdated where the take uses it
 *  (the backend's `take_input.STALE_AFTER_DAYS`). */
export const TA_STALE_AFTER_DAYS = 3
