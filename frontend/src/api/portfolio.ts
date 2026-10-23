// Typed fetch wrappers for portfolio read endpoints.
//
// fetchReturns hits GET /api/returns?period= (P2-INV3): the investment book's TWR +
// MWR/XIRR + period $ gain over a window. The backend computes everything (computed-
// not-stored); this wrapper only marshals the request + types the response.

import type { IncomeCalendar } from '../types/income'
import type {
  BenchmarkRosterItem,
  FixedIncomeSummary,
  IncomeForecast,
  IncomeSummary,
  InstrumentOption,
  PortfolioReturns,
  ReturnPeriod,
  RiskMetrics,
} from '../types/portfolio'
import { authFetch } from './client'

async function asJson<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const detail = await r.text().catch(() => '')
    throw new Error(detail || `HTTP ${r.status}`)
  }
  return r.json() as Promise<T>
}

/**
 * Period returns (TWR/MWR) for the whole investment book over `period` (default 1Y).
 * Pass a `benchmark` (instrument id or symbol) to also get a vs-benchmark `comparison` block
 * over the same window (P2-INV4). Pass `options.risk=true` to request risk-adjusted metrics
 * (volatility, beta, Sharpe, max drawdown) appended under `data.risk` (P2-INV3 Wave 2).
 * Existing callers that omit `options` are unaffected — the risk key is absent from the response.
 */
export async function fetchReturns(
  period: ReturnPeriod = '1Y',
  benchmark?: string,
  options?: { risk?: boolean },
): Promise<PortfolioReturns> {
  const q = new URLSearchParams({ period })
  if (benchmark) q.set('benchmark', benchmark)
  if (options?.risk) q.set('risk', 'true')
  return asJson<PortfolioReturns>(await authFetch(`/api/returns?${q.toString()}`))
}

/** The instrument master (for the benchmark picker). Returns id/symbol/name/price_source_id. */
export async function fetchInstruments(): Promise<InstrumentOption[]> {
  return asJson<InstrumentOption[]>(await authFetch('/api/instruments'))
}

/** Curated benchmark roster (GET /api/returns/benchmarks) — server-authoritative. */
export async function fetchBenchmarkRoster(): Promise<BenchmarkRosterItem[]> {
  const res = await asJson<{ benchmarks: BenchmarkRosterItem[] }>(
    await authFetch('/api/returns/benchmarks'),
  )
  return res.benchmarks
}

/**
 * Period returns + risk-adjusted metrics in one call (GET /api/returns?risk=true).
 * Returns PortfolioReturns with an extra `risk` key of type RiskMetrics | null.
 */
export async function fetchReturnsWithRisk(
  period: ReturnPeriod = '1Y',
  benchmark?: string,
): Promise<PortfolioReturns & { risk: RiskMetrics | null }> {
  const q = new URLSearchParams({ period, risk: 'true' })
  if (benchmark) q.set('benchmark', benchmark)
  return asJson<PortfolioReturns & { risk: RiskMetrics | null }>(
    await authFetch(`/api/returns?${q.toString()}`),
  )
}

/** Investment income summary (P2-INV2): trailing-12m, by-instrument/kind, yield-on-cost. */
export async function fetchIncome(): Promise<IncomeSummary> {
  return asJson<IncomeSummary>(await authFetch('/api/income'))
}

/** Income summary scoped to ONE position (asset detail's Position tab / "Your Position" rail) —
 *  same shape as fetchIncome, filtered server-side to just this holding's events. */
/** A position's received income, its sold-out-then-rebought predecessors included. */
export async function fetchPositionIncome(positionId: string): Promise<IncomeSummary> {
  return asJson<IncomeSummary>(
    await authFetch(`/api/positions/${positionId}/income?with_predecessors=true`),
  )
}

/** Annualized forward-income estimate (P2-INV2). An estimate, not a dated schedule. */
export async function fetchIncomeForecast(horizonMonths = 12): Promise<IncomeForecast> {
  return asJson<IncomeForecast>(
    await authFetch(`/api/income/forecast?horizon_months=${horizonMonths}`),
  )
}

/**
 * Upcoming dividend payments within `days` (ADR 0020). Each row is "scheduled" (a Yahoo-declared
 * ex-date) or "estimated" (projected from the holding's dividend history) — the UI labels them.
 */
export async function fetchIncomeCalendar(days = 90): Promise<IncomeCalendar> {
  return asJson<IncomeCalendar>(await authFetch(`/api/income/calendar?days=${days}`))
}

/** Bond / fixed-income summary with maturity ladder (GET /api/fixed-income). */
export async function fetchFixedIncome(): Promise<FixedIncomeSummary> {
  return asJson<FixedIncomeSummary>(await authFetch('/api/fixed-income'))
}

/** Record a dividend (cash or DRP). Returns the apply result {ok, event_id, ...}. */
export async function recordIncome(body: {
  position_id: string
  gross_amount: number
  tax_withheld?: number
  currency?: string
  income_kind?: string
  paid_at?: string
  account_id?: string
  is_drp?: boolean
  drp_quantity?: number
  drp_unit_cost?: number
}): Promise<{ ok: boolean; event_id: string }> {
  return asJson(
    await authFetch('/api/income', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
}
