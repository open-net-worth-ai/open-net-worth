// What each Dashboard panel query answers — backend/services/dashboard_panels.py.

import type {
  CashflowProjections,
  CategoryAmount,
  EnvelopeRow,
  ReportsRollup,
} from '../../../../types/aggregates'

export interface UpcomingPanelRow {
  date: string
  /** Days from today on the profile's clock (0 = today). */
  days: number
  kind: string
  direction: 'in' | 'out'
  label: string
  sub: string
  amount_base: number | null
}

export interface UpcomingPanel {
  base_currency: string
  days: number
  rows: UpcomingPanelRow[]
  going_out_total_base: number
  coming_in_total_base: number
  overdue_count: number
  overdue_total_base: number
  no_dated_bills: boolean
}

export interface FxPanelRow {
  /** The unflipped "B/Q" key the flip setting stores. */
  key: string
  from: string
  to: string
  held: boolean
  held_state: 'held' | 'owed' | null
  held_amount_base: number | null
  /** Units of `to` per one `from`; null with no live quote. */
  rate: number | null
}

export interface FxPanel {
  base_currency: string
  rows: FxPanelRow[]
  error: string | null
}

export interface ReportPanel extends ReportsRollup {
  spending: {
    from: string | undefined
    to: string | undefined
    fell_back: boolean
    spending_month: string
    current_month: string
    expense_rows: Array<CategoryAmount & { pct: number }>
    expense_total: number
    merchants: ReportsRollup['merchants']
  }
  savings_view: {
    ym?: string
    income: number
    spent: number
    saved: number
    rate_pct: number
    rate_pct_rounded: number
    known: boolean
    verdict: 'saved' | 'overspent'
    /** How much more went out than came in; 0 when not overspent. */
    overspent: number
    trailing?: {
      months: number
      from: string
      to: string
      monthly: number
      rate_pct: number | null
      rate_pct_rounded?: number
    } | null
  } | null
}

export interface CrossoverPanel extends Omit<CashflowProjections['crossover'], never> {
  coverage_pct_rounded: number
  coverage_bar_pct: number
  headline: 'now' | 'year' | 'not_yet'
  year: number | null
  projected: boolean
}

export interface SpendingPacePanel {
  month: string
  day_of_month: number
  days_in_month: number
  pace_pct: number
  pace_pct_rounded: number
  tick_pct: number
  rows: Array<
    EnvelopeRow & {
      state: 'on_pace' | 'ahead' | 'over'
      fill_pct_rounded: number
      bar_pct: number
    }
  >
  unmatched: string[]
}
