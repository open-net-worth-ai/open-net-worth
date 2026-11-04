export type TradingMode = 'backtest' | 'paper' | 'live'
export type AgentKind = 'lucius' | 'external' | 'script'
export type TradingState =
  | 'ready'
  | 'running'
  | 'paused'
  | 'stopping'
  | 'stopped'
  | 'completed'
  | 'failed'
  | 'needs_reconciliation'
export type Amount = string | number
export interface Collection<T> {
  items: T[]
  total: number
}
export interface Mandate {
  allowed_asset_classes: ('stock' | 'crypto')[]
  max_order_notional?: Amount | null
  max_model_cost_usd: Amount
  fee_bps: Amount
  slippage_bps: Amount
  max_participation: Amount
  max_quote_age_seconds: number
  market_data_source?: 'supplied' | 'yahoo'
  instruments?: TradingInstrument[]
}
export interface TradingWorkspace {
  id: string
  name: string
  instructions: string
  agent_kind: AgentKind
  agent_label: string
  mandate: Mandate
  revision: number
  created_at: string
  updated_at: string
  strategy_source?: string | null
}
export interface TradingInstrument {
  symbol: string
  venue: string
  asset_class: 'stock' | 'crypto'
  currency: string
}
export interface TradingObservation {
  current_quote_authorized?: boolean
  instrument: TradingInstrument
  timestamp: string
  available_at?: string | null
  price: Amount
  volume?: Amount
  id?: string
  received_at?: string
  open?: Amount | null
  high?: Amount | null
  low?: Amount | null
  close?: Amount | null
  interval?: string | null
  source?: string | null
  source_version?: string | null
  adjustment?: string | null
}
export interface Allocation {
  name: string
  currency: string
  capital: Amount
  venue: string
  connector_id?: string
  broker_account_id?: string
  book_account_id?: string
}
export interface TradingRun {
  id: string
  workspace_id: string
  workspace_snapshot: TradingWorkspace
  mode: TradingMode
  state: TradingState
  start_at: string
  end_at: string | null
  current_time: string
  created_at: string
  agent_state: string
  base_currency: string
  model_cost_usd: Amount
  data_version: string
  agent_version: string
  initial_capital: Record<string, Amount>
  next_wake_at?: string
  last_agent_error?: string
  agent_session_id?: string
  last_agent_at?: string
}
export interface TradingPosition {
  instrument: TradingInstrument
  quantity: Amount
  cost_basis?: Amount
  average_cost?: Amount
  market_price?: Amount | null
  market_value?: Amount | null
  unrealized_pnl?: Amount | null
}
export interface TradingAccount extends Allocation {
  id: string
  run_id: string
  cash: Amount
  available_cash?: Amount
  reserved_cash?: Amount
  positions: Record<string, TradingPosition>
  reconciliation_status: string
}
export interface TradingOrder {
  id: string
  run_id: string
  account_id: string
  instrument: TradingInstrument
  side: 'buy' | 'sell'
  quantity: Amount
  filled_quantity?: Amount
  order_type: string
  limit_price?: Amount | null
  status: string
  created_at: string
  request_id: string
  broker_order_id?: string
  reserved_cash?: Amount
  decision_id?: string
  mandate_revision?: number
}
export interface TradingFill {
  id: string
  order_id: string
  account_id: string
  quantity: Amount
  price: Amount
  fees: Amount
  timestamp: string
  instrument?: TradingInstrument
  side?: string
}
export interface TradingEvent {
  sequence?: number
  id: string
  kind: string
  created_at?: string
  received_at?: string
  environment_time?: string
  payload: Record<string, unknown>
}
export interface Performance {
  base_currency: string
  initial_equity?: Amount
  equity?: Amount | null
  return_pct?: Amount | null
  max_drawdown_pct?: Amount | null
  cash?: Amount
  available_cash?: Amount
  reserved_cash?: Amount
  fees?: Amount
  model_cost_usd?: Amount
  valuation_complete?: boolean
  valuation_status?: string
  missing_marks?: string[]
  realized_pnl?: Amount | null
  unrealized_pnl?: Amount | null
  history?: { timestamp: string; equity: Amount | null }[]
}
export interface TradingComparison {
  id: string
  name: string
  run_ids: string[]
  comparable?: boolean
  reasons?: string[]
  limitations?: string[]
  scenario?: {
    capital_by_currency: Record<string, Amount>
    base_currency: string
    data_version: string
    instruments: TradingInstrument[]
    fee_bps: Amount
    slippage_bps: Amount
    max_participation: Amount
    period: { start_at: string; end_at: string }
  }
  results?: (Performance & {
    run_id: string
    workspace_id?: string
    agent_label?: string
    mode?: string
    start_at?: string
    end_at?: string
    current_time?: string
    agent_version?: string
    state?: TradingState
    eligible?: boolean
    reasons?: string[]
  })[]
}
export interface RunCreate {
  mode: TradingMode
  start_at: string
  end_at: string | null
  accounts: Allocation[]
  observations: TradingObservation[]
  data_version: string
  agent_version: string
  base_currency: string
  fx_rates: Record<string, Amount>
  live_authorized: boolean
}

export interface DelegationCreate {
  workspace: Pick<
    TradingWorkspace,
    'name' | 'instructions' | 'agent_kind' | 'agent_label' | 'mandate' | 'strategy_source'
  >
  environment: RunCreate
  start: boolean
}
export interface DelegationReview extends DelegationCreate {
  ready: boolean
  issues: string[]
  executor?: { kind: string; label: string; capabilities?: Record<string, unknown> }
}
export interface DelegationResult {
  workspace: TradingWorkspace
  run: TradingRun
  started: boolean
  activation_error?: { code: string; message: string }
}
export interface MandateAmendment {
  id: string
  revision: number
  reason: string
  created_at: string
  author?: string
  before?: Record<string, unknown>
  after?: Record<string, unknown>
  [key: string]: unknown
}
export interface WorkspaceOverview {
  workspace: TradingWorkspace
  current_run: TradingRun | null
  original_mandate: Pick<
    TradingWorkspace,
    'instructions' | 'mandate' | 'agent_kind' | 'agent_label' | 'created_at' | 'revision'
  > | null
  original_availability?: string
  amendments: MandateAmendment[]
  current_work: {
    state: string
    summary: string | null
    last_activity_at?: string | null
    next_wake_at?: string | null
    blocker?: string | null
    executor?: string | Record<string, unknown>
  }
  visibility?: Record<string, unknown>
}
export interface TradingMarketSeries extends Collection<TradingObservation> {
  current_time: string
  data_version: string
  candle_count: number
  quote_count: number
}
export interface TradingStatement {
  period: { start_at: string; end_at: string | null; as_of: string }
  base_currency: string
  initial_equity: Amount
  equity: Amount | null
  pnl: Amount | null
  realized_pnl: Amount | null
  unrealized_pnl: Amount | null
  fees: Amount | null
  fees_complete: boolean
  valuation_complete: boolean
  model_cost_usd: Amount
  accounts: {
    id: string
    name: string
    currency: string
    opening_cash: Amount
    closing_cash: Amount
    cash_change: Amount
    realized_pnl: Amount | null
    unrealized_pnl: Amount | null
    positions: Record<string, TradingPosition>
    movements: {
      id: string
      timestamp: string
      kind: string
      amount: Amount
      order_id?: string
      fill_id?: string
    }[]
  }[]
  fills: TradingFill[]
}
export type EvidenceSelection = { kind: 'order' | 'fill' | 'event'; id: string }
export interface TradingEvidence {
  selection: EvidenceSelection
  order: TradingOrder | null
  orders?: TradingOrder[]
  fills: TradingFill[]
  decision: TradingEvent | null
  events: TradingEvent[]
  observations: TradingObservation[]
  data_context: { label: string; cutoff: string; data_version: string }
  files: Record<string, unknown>[]
  missing_links: string[]
}
export interface TradingFile {
  name: string
  version: number
  sha256: string
  content: string | null
  event_id: string
  redacted: boolean
}
