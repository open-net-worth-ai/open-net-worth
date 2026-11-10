export interface Row {
  category: string
  label: string
  // The two orthogonal axes that replaced the overloaded `bucket`
  // (ADR 2026-07-31-split-position-bucket).
  // ENTITLEMENT — is this the holder's? The ONE thing net worth filters on.
  vesting_state: 'vested' | 'unvested'
  // REACHABILITY — says nothing about entitlement. Property is illiquid AND vested.
  liquidity: 'liquid' | 'semi_liquid' | 'illiquid'
  asset_class:
    | 'equity'
    | 'bond'
    | 'commodity'
    | 'crypto'
    | 'cash'
    | 'property'
    | 'receivable'
    | 'option'
  quantity: string
  price: string
  cost_basis: string
  currency: string
  ticker: string
  // Account and owned-asset rows only — a position has no note.
  note?: string
  color?: string
  // Present on market positions (from positions_service.hydrated); absent on cash/account rows.
  symbol?: string
  asset_subclass?: string // wrapper/form: stock|etf|etc|etp|coin|physical… (asset_class is the exposure)
  sector?: string // GICS sector (equities only; "" = unclassified/crypto/commodity)
  position_id?: string
  instrument_id?: string
  account_id?: string
  account_name?: string
  institution?: string
  // Units layer: holding unit label (e.g. "g" for physical gold) + factor so value =
  // qty × price × quote_per_unit. Defaults: unit="" (native share/coin), quote_per_unit=1.
  unit?: string
  quote_per_unit?: number
  // Currency in which the market price is denominated (from instrument.quote_currency).
  // Distinct from `currency` (the cost/settlement currency). Present on investment rows only.
  quote_currency?: string
  // "Since grant" reference fields — present only on locked positions that are backed by an
  // equity grant with a grant_price_per_unit. Used to show "since grant" P&L in the UI.
  ref_grant_price?: number
  ref_grant_currency?: string
  ref_grant_last_vest?: string | null
  // Day P&L from the backend price poller (quote currency). Absent on cash/account rows.
  prev_price?: number | null
  day_change?: number | null
  day_change_pct?: number | null
  // Display-only pre/post-market quote (services.positions.hydrated's own docstring): present
  // only while a PRE/POST session is active. Never fed into day_change(_pct) above — net worth
  // and unrealized P&L stay anchored to the regular-session close.
  extended_session?: 'pre' | 'post' | null
  extended_price?: number | null
  extended_change_pct?: number | null
  // Bond / fixed-income metadata — present only when asset_class === 'bond'.
  bond_type?: '' | 'market' | 'ssb' | 'tbill' | 'etf' | 'fd'
  face_value?: number
  coupon_rate?: number | null
  coupon_freq?: number
  maturity_date?: string | null
  issue_date?: string | null
  days_to_maturity?: number | null
}

/**
 * A row as `GET /api/portfolio/rows` serves it: every monetary field already computed by
 * `backend/services/portfolio_rows.py`. This is the ONE valuation of a holding
 * (ADR 2026-07-31-overview-aggregates-are-server-computed); `adaptServerRow` renames these
 * onto `ComputedRow` for the components that already read the `_`-prefixed names.
 */
export interface ServerPortfolioRow extends Row {
  /** Value in the base currency: qty × price × quote_per_unit, FX-converted. */
  base_value: number
  cost_base: number | null
  pl: number | null
  pl_pct: number | null
  daily_pl: number | null
  daily_pl_pct: number | null
  /** Share of the vested portfolio total. Always 0 for unvested rows — not owned yet. */
  pct_of_portfolio: number
  /** Currency the native Price/Cost columns render in (crypto reads in its cost currency). */
  display_currency: string
  display_unit_price: number
  display_unit_cost: number | null
  /** "Since grant" P&L — unvested grant units only, and only when the quote currency matches
   *  the grant currency. Never included in totals. */
  ref_pl: number | null
  ref_pl_pct: number | null
  /** True for a row synthesised from an account rather than a market position. */
  is_account_row?: boolean
  account_kind?: string
  account_type?: string
}

export interface ComputedRow extends Row {
  _base: number // value in the base currency
  _pct: number
  _cost_base: number | null
  // Display currency for the native Price/Cost columns: the holder's base currency for crypto
  // (shown as a pair, e.g. BTC/SGD), else the quote/price currency. The two per-unit figures are
  // both expressed in it so they're directly comparable even when cost_currency ≠ quote_currency.
  _disp_ccy: string
  _disp_unit_price: number
  _disp_unit_cost: number | null // null when cost is unknown
  _pl: number | null
  _plpct: number | null
  _daily_pl: number | null
  _daily_plpct: number | null
  _prev?: number | null
  // "Since grant" P&L — only set on locked rows where ref_grant_price is present AND
  // the row's quote_currency matches ref_grant_currency. Never included in totals.
  _ref_pl?: number | null
  _ref_plpct?: number | null
  /** Carried through from `ServerPortfolioRow` by `adaptServerRow`'s spread. */
  is_account_row?: boolean
}

export interface PricesResponse {
  prices: Record<string, number>
  prev_prices: Record<string, number>
  fx: Record<string, number>
  base_currency?: string
  error?: string
}

// ── Tax lots + realized P&L (P2-INV1) ──────────────────────────────────────────
// A lot is one dated acquisition backing a position; the service annotates each with
// effective unit cost, remaining basis, and term-to-date (long/short as of today).
export interface Lot {
  id: string
  position_id: string
  instrument_id: string
  account_id: string
  /** Additive server-chosen label for account_id — present only when hydrated for a merged
   *  multi-account read (asset_detail.py's `_position_block`); absent from the plain
   *  GET /api/positions/{id}/lots response. */
  account_name?: string
  acquired_at: string
  original_qty: number
  remaining_qty: number
  unit_cost: number | null
  cost_currency: string | null
  fees: number
  kind: string
  note?: string
  /** Linked cash debit when the lot was acquired with a funding account. */
  txn_id?: string | null
  // service-computed annotations
  effective_unit_cost: number | null
  remaining_basis: number | null
  term_to_date: 'long' | 'short' | 'unknown'
}

// One consumed-lot line on a disposal — the per-line term lets tax reports split gains.
export interface Consumption {
  lot_id: string
  qty: number
  unit_cost: number | null
  cost_currency: string | null
  acquired_at: string
  term: 'long' | 'short' | 'unknown'
  realized_gain: number | null
}

// A disposal fact. realized_gain is in proceeds_currency; realized_gain_base is
// recomputed into the current base by the service (the stored snapshot can go stale).
export interface RealizedEvent {
  id: string
  position_id: string
  instrument_id: string
  // Hydrated by the realized-P&L read path (services.lots.realized_pnl) so a fully-sold
  // holding that's dropped out of the live portfolio still labels by name, not its UUID.
  instrument_name?: string
  instrument_symbol?: string
  account_id: string
  /** Additive server-chosen label for account_id — present only when hydrated for a merged
   *  multi-account read (asset_detail.py's `_realized_rows`); absent from the plain
   *  GET /api/realized-pnl response. */
  account_name?: string
  disposed_at: string
  quantity: number
  proceeds_per_unit: number
  proceeds_currency: string
  disposal_fees?: number
  method: string
  is_taxable: boolean
  // Set only on a share move between the user's own accounts (a non-taxable disposal that
  // named where the units went). A label, not a second leg.
  transfer_to_account_id?: string | null
  /** Cash account the net proceeds were deposited into, when a sale booked a cash leg. */
  proceeds_account_id?: string | null
  /** Linked proceeds (or transfer) cash txn, when one was booked. */
  txn_id?: string | null
  cost_basis_known: boolean
  consumptions: Consumption[]
  realized_gain: number | null
  realized_gain_base: number | null
  base_currency: string
}

export interface RealizedPnl {
  base_currency: string
  events: RealizedEvent[]
  realized_total_base: number
  by_term: { long: number; short: number; unknown: number }
  by_instrument: Record<string, number>
}

// ── Dividend / income tracking + forward forecast (P2-INV2) ─────────────────────
// Income events are an investment-side ANNOTATION over a single cash inflow (or a DRP lot).
// Base figures are recomputed in the service from the native leg + current FX (never stale).
export interface IncomeEvent {
  id: string
  position_id: string
  instrument_id: string
  account_id: string
  paid_at: string
  income_kind: 'dividend' | 'interest' | 'distribution'
  gross_amount: number
  tax_withheld: number
  currency: string
  is_drp: boolean
  txn_id: string | null
  /** Null when the event's currency has no FX rate; the native net is in `native_net_amount`. */
  base_amount: number | null
  /** False when the event's currency has no FX rate to the base currency. */
  base_amount_available?: boolean
  native_net_amount?: number
  native_currency?: string
  /** Additive server-chosen display labels; absent when an id no longer resolves. */
  instrument_label?: string
  instrument_ticker?: string
  account_name?: string
  created_at?: string | null
}

export interface IncomeSummary {
  base_currency: string
  events: IncomeEvent[]
  total_base: number
  gross_base: number
  tax_withheld_base: number
  trailing_12m_base: number
  cost_basis_base: number
  yield_on_cost_pct: number | null
  by_instrument: Record<string, number>
  by_kind: Record<string, number>
  base_totals_available?: boolean
  yield_on_cost_available?: boolean
  missing_fx_currencies?: string[]
}

export interface IncomeForecastRow {
  position_id: string
  instrument_id: string
  symbol: string
  name: string
  quantity: number
  annual_income_base: number
  horizon_income_base: number
  source: 'declared' | 'coupon' | 'accretion' | 'trailing' | 'none'
}

export interface IncomeForecast {
  base_currency: string
  horizon_months: number
  holdings: IncomeForecastRow[]
  annual_total_base: number
  horizon_total_base: number
  cost_basis_base: number
  market_value_base: number
  // Forward yield on cost basis (legacy) vs on current market value (the standard dividend
  // yield — what the UI shows as "Forward yield"). See ADR 0020.
  implied_forward_yield_pct: number | null
  forward_yield_pct: number | null
}

// ── Period returns: TWR + MWR/XIRR (P2-INV3, Phase 4) ───────────────────────────
// The investment book's PERFORMANCE over a window — separating contributions from genuine
// return. twr/mwr are null when there isn't enough price history to span the window
// (data_quality.sufficient=false); the panel greys those out rather than showing a fake 0.
//
// Phase 4 additions:
//  * '7D' added to ReturnPeriod.
//  * effective_start: the actual window start after clamping to max(inception, price_floor).
//    Fresh books shift forward rather than refusing.
//  * label: human label for the window — "Since 2026-03-01" when clamped, else "Last 1 year".
//  * annualized: true only when effective span ≥ 360 days. Sub-year windows always report
//    cumulative TWR/MWR to avoid noise-amplification from annualizing short windows.
//  * mwr: null for spans < 360 days (XIRR is inherently annualized; sub-year figures are
//    noisy/misleading). The MWR card is hidden entirely when mwr is null.
//  * series: date-aligned list for charting as 0-based cumulative return fractions:
//      portfolio = chain-linked TWR path (contributions stripped; endpoint == twr.period_return)
//      benchmark = price/price_open - 1 per day (pure price return)
//    0.0 at the window open for both; null benchmark = no benchmark or insufficient history.
//  * data_quality gains inception, effective_start, tiers (per-instrument accuracy),
//    has_unrecorded_risk, fx_approximate from the portfolio_history primitive.
export type ReturnPeriod = '7D' | '1M' | '3M' | 'YTD' | '1Y' | 'ALL'

// Both `portfolio` and `benchmark` are 0-based cumulative return fractions:
// 0.0 at the window open, 0.15 = +15%, -0.08 = -8%. The chart renders them as %.
// `benchmark` is null when no benchmark was requested or has insufficient history.
export interface ReturnSeriesPoint {
  date: string
  portfolio: number // TWR cumulative path fraction (contributions stripped)
  benchmark: number | null // price/price_open - 1 fraction; null = absent
}

export interface PortfolioReturns {
  period: ReturnPeriod
  start: string // requested window start (original, pre-clamp)
  end: string
  effective_start: string // actual window start after clamping to inception / price_floor
  label: string // "Since 2026-03-01" when clamped; standard label otherwise
  annualized: boolean // true iff effective span ≥ 360 days
  base_currency: string
  // Fractions (not %); the UI multiplies by 100. null = not enough history.
  twr: { period_return: number; annualized: number } | null
  // null for spans < 30 days or insufficient history; card hidden when null.
  // period_return is de-annualized for spans < 360d: (1+xirr)^(span/365)−1.
  // annualized=true only when the span is ≥ 360 days (xirr == period_return in that case).
  mwr: { xirr: number; period_return: number; annualized: boolean } | null
  period_gain_base: number
  start_value: number
  end_value: number
  // Chart series: 0-based cumulative return fractions (see ReturnSeriesPoint).
  series: ReturnSeriesPoint[]
  data_quality: {
    market_since: string | null
    inception: string | null // earliest book date
    effective_start: string // same as top-level effective_start
    value_points: number
    sufficient: boolean
    has_unknown_cost: boolean
    has_unrecorded_risk: boolean // any contributing instrument has undated lots
    fx_approximate: boolean // any contributing instrument uses non-base currency
    tiers: Record<string, 'exact' | 'approximate'> // per-instrument accuracy
  }
  // Present only when ?benchmark= was passed (P2-INV4). null = the benchmark couldn't be
  // resolved or lacks enough price history over the window — render "no benchmark" gracefully.
  comparison?: BenchmarkComparison | null
  // Present only when ?risk=true is passed (P2-INV3 Wave 2). null = computed but insufficient.
  risk?: RiskMetrics | null
}

// A benchmark instrument's pure price/TWR return over the window (an index has no cash flows).
// twr is null when there isn't enough benchmark price history to span the window.
export interface BenchmarkReturn {
  symbol: string
  instrument_id: string
  twr: { period_return: number; annualized: number } | null
  data_quality: {
    market_since: string | null
    value_points: number
    sufficient: boolean
  }
}

// Portfolio-vs-benchmark comparison over the SAME window (period/cumulative fractions).
// excess_return is null unless BOTH sides have a sufficient TWR.
export interface BenchmarkComparison {
  benchmark: BenchmarkReturn
  portfolio_twr: number | null
  benchmark_twr: number | null
  excess_return: number | null
}

// Minimal instrument shape for the benchmark picker (GET /api/instruments returns more).
export interface InstrumentOption {
  id: string
  symbol: string
  name: string
  price_source_id: string
}

// ── Benchmark roster (GET /api/returns/benchmarks) ───────────────────────────
// Server-authoritative; the FE never hard-codes this list.
export interface BenchmarkRosterItem {
  key: string // roster key (e.g. "SPY") or "" for "no benchmark"
  label: string // display label (e.g. "SPY • S&P 500")
  symbol: string // raw ticker symbol (same as key for these instruments)
}

// ── Risk-adjusted metrics block (GET /api/returns?risk=true) ─────────────────
// Attached to the PortfolioReturns response when ?risk=true is passed.
// All values are fractions (volatility 0.15 = 15%, max_drawdown -0.17 = -17%).
// Null when there is insufficient price history for the window.
export interface RiskMetrics {
  period: string
  volatility: number | null // annualized, fraction
  beta_vs_spy: number | null // cov/var vs the selected benchmark (or SPY fallback)
  beta_symbol: string // the symbol beta was actually computed against (e.g. "BTC")
  sharpe: number | null // (ann. return - 3.5%) / ann. vol, clamped
  max_drawdown: number | null // negative fraction (worst peak-to-trough)
  summary: string // plain-English one-liner
}

// ── Fixed income / bond summary (GET /api/fixed-income) ─────────────────────
export interface FixedIncomeHolding {
  instrument_id: string
  symbol: string
  name: string
  account_name: string
  subtype: string
  coupon_rate: number | null
  maturity_date: string | null
  days_to_maturity: number | null
  value_base: number
  next_coupon: { date: string; amount_per_unit: number } | null
}

export interface FixedIncomeLadderRow {
  instrument_id: string
  name: string
  subtype: string
  maturity_date: string | null
  proceeds_base: number
}

export interface FixedIncomeSummary {
  base_currency: string
  total_base: number
  count: number
  by_subtype: Record<string, number>
  maturity_buckets: { d30: number; d90: number; y1: number }
  maturity_ladder: FixedIncomeLadderRow[]
  holdings: FixedIncomeHolding[]
  /** Count of held bonds excluded from the ladder because they have no maturity date (e.g. bond ETFs). */
  ladder_excluded_count?: number
}

// ── Options book (GET /api/options) ─────────────────────────────────────────
// An option position enriched by the service with derived fields (DTE, mark,
// status).  Amounts are in base currency, signed per the `side` convention:
//   premium > 0  = received (short side); premium < 0 = paid (long side)
//   mark    > 0  = unrealised gain;       mark    < 0 = unrealised loss
export type OptionType = 'CALL' | 'PUT'
export type OptionSide = 'long' | 'short'
export type OptionStrategyGroup = 'income' | 'hedge'
export type OptionStatus = 'ITM' | 'OTM'

export interface OptionContract {
  id: string
  option_type: OptionType
  side: OptionSide
  underlying_symbol: string
  strategy_tag: string // human label, e.g. "Covered call"
  strategy_group: OptionStrategyGroup
  contracts: number
  strike: number
  expiry: string // ISO date
  dte: number // days to expiry (derived by service)
  premium: number // signed base-currency amount
  mark: number // open P&L in base currency (derived)
  status: OptionStatus // ITM / OTM (derived)
  collateral: number // capital committed (base currency)
  note: string // e.g. "Covers 100 NVDA shares"
}

export interface OptionsBookSummary {
  open_positions: number
  premium_collected: number // Σ positive premium (base currency)
  capital_committed: number // Σ collateral (base currency)
  nearest_expiry_date: string | null
  nearest_expiry_dte: number | null
  nearest_expiry_underlying: string | null
}

export interface OptionsBook {
  base_currency: string
  summary: OptionsBookSummary
  positions: OptionContract[]
}

export interface ScheduleItem {
  n: string
  date: string
  vndM?: number
  sgd?: number
  desc: string
}

export interface Property {
  id: string
  name: string
  subtitle: string
  currency: string
  schedule: ScheduleItem[]
}
