// Server-computed aggregates — the payload shapes of the domain read endpoints.
//
// Named for the DATA, not for the screen that renders it.
//
// Every monetary field here was computed by backend/services/overview. A widget renders these
// values; it does not add, subtract, or divide them. See
// ADR 2026-07-31-overview-aggregates-are-server-computed.

import type { UpcomingEvent } from './commitments'
import type { Row } from './portfolio'

/** A portfolio row with its money already valued and converted, server-side. */
export interface PortfolioRowValued extends Row {
  /** Value in the base currency (qty x price x quote_per_unit, FX-converted). */
  base_value: number
  cost_base: number | null
  pl: number | null
  pl_pct: number | null
  daily_pl: number | null
  daily_pl_pct: number | null
  /** Share of `portfolio_total`. Always 0 for unvested rows — they are not owned. */
  pct_of_portfolio: number
  account_name?: string
  account_kind?: string
  account_type?: string
  is_account_row?: boolean
}

export interface BreakdownEntry {
  key: string
  value: number
  pct: number
  color?: string
}

export interface Breakdown {
  total: number
  rows: BreakdownEntry[]
}

export interface PortfolioKpis {
  today: number
  today_pct: number
  has_market: boolean
  upl: number
  upl_known: boolean
  /** Cash on hand — the backend's `cash` band alone (Σ `cash` accounts), the one spendable-cash
   *  figure every surface reads (ADR 2026-09-07-seven-account-types-one-axis). */
  cash: number
  debt: number
  debt_accounts: number
}

export interface PortfolioAttention {
  holdings_total: number
  debt_total: number
  /** The part of that debt already set aside in the cards' payment envelopes. */
  debt_covered_total?: number
  /** Every dollar of the debt is held in those envelopes: say "covered", not "monitor". */
  debt_covered?: boolean
  contingent_total: number
  contingent_pct: number
  contingent_flagged: boolean
  cash_total: number
  thin_cash: boolean
  /** Months of the user's average spending the cash covers; null while spending is unknown.
   *  The Cash Flow runway itself (same cash, same burn), not a separate division. */
  cash_months?: number | null
  /** Open credit cards with no purchases in the burn's window: the runway cannot see them. */
  cards_missing_spending?: string[]
  has_investments: boolean
}

/** One tile: an INSTRUMENT's positions inside one group, already summed. `key` is the largest
 *  contributing position's row key. `day_pl` / `pl` / `pl_pct` are null when unknown (no day
 *  move, or any leg without a recorded cost). */
export interface TreemapLeafData {
  key: string
  value: number
  pct_of_shown: number
  day_pl: number | null
  pl: number | null
  pl_pct: number | null
  positions: number
}

export interface TreemapGroupData {
  key: string
  total: number
  leaves: TreemapLeafData[]
}

export type TreemapFacet = 'sector' | 'asset_class' | 'account' | 'none'

export interface DriftRow {
  key: string
  current_pct: number
  target_pct: number
  drift_pp: number
  /** Base-currency trade that would close the gap — positive = top up, negative = trim. */
  trade_base: number
  actionable: boolean
}

export interface PortfolioAnalytics {
  base_currency: string
  is_live: boolean
  /** No `net_worth` here by design — it has ONE endpoint, `GET /api/net-worth` (`useNetWorth`).
   *  Echoing it here gave two surfaces two paths to one number and they rendered differently. */
  unvested_base: number
  /** Allocation denominator (vested rows). NOT net worth. */
  portfolio_total: number
  bands: {
    classes: Record<string, number>
    liabilities: number
    accounts: number
    net_worth: number
  }
  /** The invested book — market holdings grouped, weighted, ranked. */
  book: InvestedBook
  kpis: PortfolioKpis
  /** Shares of the ASSETS (they sum to 100%); `owed` (<= 0) is the debts, kept out of the shares. */
  allocation: Breakdown & { owed: number }
  reach: Breakdown
  venues: Breakdown
  attention: PortfolioAttention
  debts: {
    total: number
    rows: Array<{ key: string; label: string; value: number; color: string }>
  }
  fx_exposure: Array<{ currency: string; value: number }>
  /** Unrealized P&L per INSTRUMENT (positions summed server-side), biggest |P&L| first. `key` is
   *  the largest position's row key — the caller already holds the rows for display fields. */
  gains: {
    total_pl: number
    rows: Array<{ key: string; value: number; pl: number | null; pl_pct: number | null }>
  }
  /** Today's movers — one entry per INSTRUMENT (positions already summed server-side: total
   *  move, % on the prior close, value), gainers first then losers. `key` is the largest
   *  contributing position's row key; the caller already holds the rows. */
  movers: {
    rows: Array<{ key: string; day_pl: number; day_pct: number; value: number }>
    gainers: number
    losers: number
    max_abs: number
  }
  holdings_map: {
    total: number
    shown_total: number
    shown: number
    holdings: number
    groups: Record<TreemapFacet, TreemapGroupData[]>
  }
  drift: {
    total: number
    has_targets: boolean
    threshold_pp: number
    rows: DriftRow[]
    top_trade: DriftRow | null
  }
}

export interface HistoryPoint {
  date: string
  classes: Record<string, number>
  liabilities: number
  accounts: number
  net_worth: number
  unvested_base?: number
  market_tracked: boolean
  /** True when this point had any non-base-currency exposure (a foreign account balance
   * or a non-base market holding) — it was revalued at TODAY's FX, not the date's own
   * rate (no as-of FX history yet). See MATH-4 / `ledger.net_worth_history`. */
  fx_approximate: boolean
}

export interface RangeStats {
  delta: number
  pct: number | null
  /** The account-only move over the range — what you saved, as opposed to what markets did. */
  saved: number
  market: number
  span_months: number
  from_date: string
  to_date: string
}

export interface NetWorthStats {
  base_currency: string
  net_worth: number
  history: HistoryPoint[]
  ranges: Record<string, RangeStats | null>
  comparison: RangeStats | null
  month_to_date: {
    delta: number
    from_date: string
    from_net_worth: number
    to_date: string
    to_net_worth: number
    basis: string
  } | null
  composition: { classes: string[]; has_liabilities: boolean }
  latest: HistoryPoint | null
}

export interface EnvelopeRow {
  key: string
  label: string
  color: string
  assigned: number
  spent: number
  fill_pct: number
  over: boolean
  /** assigned - spent, signed (negative once over). Server-computed — ADR
   * 2026-07-31-overview-aggregates-are-server-computed. */
  remaining: number
  /** spent / day_of_month elapsed (whole `days_in_month` once the month isn't current). */
  avg_per_day: number
  /** remaining / days_left, inclusive of today; 0 once over budget or the month is done. */
  left_per_day: number
}

export interface PlanningSummary {
  month: string
  base_currency: string
  days_in_month: number
  day_of_month: number
  days_left: number
  pace_pct: number
  envelopes: {
    rows: EnvelopeRow[]
    budgeted_rows: EnvelopeRow[]
    total_spent: number
    budgeted_spent: number
    budgeted_total: number
    total_budget: number
    pct_of_budget: number
  }
  goals: Array<{
    key: string
    label: string
    have: number
    target: number
    pct: number
    by: string | null
    risk: boolean
    /** Pace verdict: on_track|ahead|behind|past_due|completed (an envelope target is on_track|behind). */
    status: string
  }>
  movers: {
    rows: Array<{ key: string; label: string; now: number; prev: number; delta: number }>
    max_abs: number
  }
  calendar: {
    month: string
    days_in_month: number
    first_weekday: number
    days: Array<{ day: number; in: number; out: number; net: number }>
    total_in: number
    total_out: number
    max_out: number
    max_flow: number
  }
  recurring: {
    rows: Array<{
      id: string
      label: string
      amount: number
      currency: string
      amount_base: number
      cadence: string
      next_run_at: string | null
    }>
    monthly_total_base: number
  }
  cashflow: CashflowMonth
  /** The newest earlier month with money in it — only while this month has none. */
  cashflow_latest: (CashflowMonth & { month: string }) | null
  safe_to_spend: {
    ready_to_assign: number
    per_day: number
    assigned: number
  }
}

export interface CashflowMonth {
  total_in: number
  total_out: number
  net: number
  in_count: number
  out_count: number
  max: number
}

export interface CategoryAmount {
  key: string
  label: string
  color: string
  amount: number
}

export interface MonthPoint {
  ym: string
  expense_rows: CategoryAmount[]
  expense_total: number
  income_rows: CategoryAmount[]
  income_total: number
}

export interface StackedSeries {
  key: string
  label: string
  color: string
  data: number[]
}

export interface ReportsRollup {
  base_currency: string
  months: string[]
  points: MonthPoint[]
  current: MonthPoint
  expense_series: StackedSeries[]
  income_series: StackedSeries[]
  expense_table: Array<{
    key: string
    label: string
    color: string
    current: number
    average: number
    total: number
  }>
  income_table: ReportsRollup['expense_table']
  /** Current month / window average / window total, per side and net. */
  expense_totals: { current: number; average: number; total: number }
  income_totals: { current: number; average: number; total: number }
  net_totals: { current: number; average: number; total: number }
  avg_monthly_spend: number
  net_total: number
  /** The month Spending by category and Top merchants describe: `current_month`, or the newest
   *  earlier month in the window with spending while this one has none yet. */
  spending_month: string
  current_month: string
  merchants: Array<{ key: string; label: string; amount: number; pct: number }>
  /** The newest month in the window with income (`ym`) — mid-month that is usually last month. */
  savings: {
    income: number
    spent: number
    saved: number
    rate_pct: number
    known: boolean
    ym: string
    /** Open credit cards with no purchases in `ym`: their spending is not in `spent`. */
    cards_missing_spending: string[]
    /** The average over the last complete months (`reports_rollup.trailing_saving`) — the very
     *  figure the Outlook checks its saving lever against; `null` with under two active months. */
    trailing: {
      monthly: number
      rate_pct: number | null
      months: number
      from: string
      to: string
    } | null
  }
}

export interface DividendSummary {
  base_currency: string
  received_ytd: number
  upcoming: Array<{
    position_id: string
    symbol: string
    name: string
    pay_date: string
    amount_base: number
    status: string
    kind: string
  }>
  forecast: Array<{ month: string; amount: number }>
  forecast_total: number
}

export interface CommitmentsSummary {
  base_currency: string
  events: UpcomingEvent[]
  strip_days: number
  /** Everything going OUT within the next `strip_days` (today through +30, inclusive). Funding
   *  inflows are not part of it. The same figure the Commitments masthead and the briefing read. */
  strip_total_base: number
  /** Past-due items going OUT (funding inflows excluded), with their summed base amount. */
  overdue: UpcomingEvent[]
  overdue_count: number
  overdue_total_base: number
  /** Recurring income (a detected or confirmed stream, e.g. a salary) landing inside the same
   *  window, soonest first. */
  expected_income: ExpectedIncome[]
  expected_income_total_base: number
  /** Recurring outflows (rent, a subscription) due in the window, minus any a commitment
   *  already lists. */
  bills: ExpectedIncome[]
  bills_total_base: number
  /** Each card's unpaid statement due in the window. */
  card_bills: CardBill[]
  card_bills_total_base: number
  /** Unpaid card statements due within a week or overdue: Needs attention's card flag. */
  cards_due_soon?: CardBill[]
  /** Commitments due + recurring bills + card bills: everything going out in the window. */
  going_out_total_base: number
}

export interface CardBill {
  date: string
  label: string
  account_id: string
  amount_base: number
  /** The due date and amount are estimated from the card's cycle, not a printed statement. */
  estimated: boolean
  /** Days from today to the due date; negative when it is past due. */
  days_until_due?: number
}

export interface ExpectedIncome {
  date: string
  label: string
  cadence: string
  amount_base: number
}

export interface CashflowProjections {
  crossover: {
    passive_monthly: number
    annual_passive: number
    expenses_monthly: number
    /** Share of the monthly bill already covered by passive income, capped at 100. */
    coverage_pct: number
    to_go_monthly: number
    known: boolean
  }
  runway: {
    base_currency: string
    cash: number
    burn: number
    runway_months: number | null
    horizon_months: number
    /** Liquid cash at +0, +1, +2 … months, floored at zero. */
    trajectory: number[]
    known: boolean
    /** Open credit cards with no purchases in the burn's window. */
    cards_missing_spending: string[]
  }
}

export interface SpendByWeek {
  base_currency: string
  query: string
  weeks: Array<{ key: string; start: string; end: string; amount: number }>
  total: number
  average: number
  max: number
}

// ── Investments book (GET /api/investments/book) ──────────────────────────────
// Every book-level figure computed by backend/services/investments_book.py on top of the one
// row projection: the grand total, per-row weights, per-class sections, today's and unrealized
// P&L, single-name concentration, allocation vs target, and bond YTM.

export interface InvestedBookRow {
  row_key: string
  position_id: string | null
  instrument_id: string | null
  symbol: string
  name: string
  value: number
  /** Share of `grand`, computed server-side. */
  weight: number
  /** Unrealized P&L in base; `null` when the cost basis is unknown — never 0 for "unknown". */
  pl: number | null
  pl_pct: number | null
  day_pct: number
  day_pl: number
  cls: string
  sub: string
  sector: string
  account: string
  account_id: string
  currency: string
  cost_currency: string
  cost_base: number | null
  qty: number
  /** Per-unit price/cost in the row's display currency. */
  price: number
  cost: number
  vesting_state: string
  coupon: number | null
  maturity: string | null
  ytm: number | null
  /** >1 when positions of one instrument across accounts were summed into this row. */
  merged_count: number
}

/** What a book of broken-down holdings leaves out: each Brokerage total account's untracked
 *  part (backend/services/untracked_holdings.py). Invested, in no known class — so it is NOT in
 *  `grand`, the weights, concentration or today's move. Present only with the
 *  `allow_brokerage_total_valuation` flag on. */
export interface UntrackedExcluded {
  total: number
  /** Its share of everything invested (`grand` + `total`) — the "Not broken down" slice. */
  pct_of_invested: number
  accounts: Array<{ account_id: string; name: string; type: string; value: number }>
}

export interface InvestedBook {
  base_currency: string
  is_live: boolean
  grand: number
  empty: boolean
  portfolio_total: number
  pct_of_portfolio: number
  rows: InvestedBookRow[]
  /** One row per instrument — positions across accounts already summed. */
  merged_rows: InvestedBookRow[]
  sections: Array<{
    cls: string
    subtotal: number
    current_pct: number
    target_pct: number
    drift_pp: number
    /** Base-currency trade that would close the gap — positive = top up, negative = trim. */
    trade_base: number
    row_keys: string[]
  }>
  today_pl: number
  today_pct: number
  total_pl: number
  total_pl_pct: number
  /** `row_key`s of the biggest movers by |day %|, in order. */
  movers: string[]
  concentration: Array<{ name: string; value: number; pct: number; hot: boolean }>
  concentration_guideline_pct: number
  top_name: { name: string; value: number; pct: number; hot: boolean } | null
  /** Drift (pp) at/above which a class is flagged "off target" — the SAME constant
   *  `portfolio_analytics().drift.threshold_pp` carries (rebalance.DRIFT_FLAG_PP). Every
   *  frontend surface reads this field rather than hardcoding a number of its own. */
  threshold_pp: number
  allocation: Array<{
    cls: string
    current_pct: number
    target_pct: number
    drift_pp: number
    /** Base-currency trade that would close the gap — positive = top up, negative = trim. */
    trade_base: number
    subtotal: number
  }>
  untracked_excluded?: UntrackedExcluded
}

/** GET /api/cashflow/flow — one month's money-flow (Sankey) graph. Every amount and share is
 *  server-computed (services/cash_flow_graph.py); each column sums to `flow_total`. */
export type CashFlowNodeKind = 'income' | 'total' | 'shortfall' | 'spend_group' | 'spend' | 'saved'

export interface CashFlowNode {
  id: string
  label: string
  column: number
  kind: CashFlowNodeKind
  amount: number
  pct: number
  /** Spending group's stable colour slot (its Planning order). */
  color_slot?: number
  /** Ledger categories behind a spending row (several for a merged envelope) — the register
   *  drill-down target. Absent on "Other" rows. */
  category_ids?: string[]
  /** The Uncategorized row: drills to uncategorized spending. */
  uncategorized?: boolean
  /** Spending category's share of ITS group (server-computed) — the phone drill-in view. */
  group_pct?: number
}

export interface CashFlowGraph {
  month: string
  base_currency: string
  income_total: number
  spend_total: number
  saved: number
  shortfall: number
  flow_total: number
  savings_rate_pct: number | null
  columns: number
  nodes: CashFlowNode[]
  links: Array<{ source: string; target: string; amount: number }>
}
