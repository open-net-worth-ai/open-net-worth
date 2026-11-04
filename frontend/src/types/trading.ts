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
}
export interface TradingInstrument {
  symbol: string
  venue: string
  asset_class: 'stock' | 'crypto'
  currency: string
}
export interface TradingObservation {
  instrument: TradingInstrument
  timestamp: string
  available_at?: string | null
  price: Amount
  volume?: Amount
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
  end_at: string
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
  end_at: string
  accounts: Allocation[]
  observations: TradingObservation[]
  data_version: string
  agent_version: string
  base_currency: string
  fx_rates: Record<string, Amount>
  live_authorized: boolean
}
