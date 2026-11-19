// Server-computed aggregate reads, one hook per DATA RESOURCE.
//
// Named for what they return, never for the screen that renders them — an endpoint called
// `/api/overview/networth` describes a consumer, which is how you end up with four endpoints
// answering the same question. See ADR 2026-07-31-overview-aggregates-are-server-computed.
//
// Every figure they return was computed by the backend; a component renders a field, it never
// folds rows into a total, divides for a percentage, or differences two points.
//
// **Widgets call these directly.** The dashboard is user-composable, so a widget has to be
// standalone — droppable onto any view and responsible for its own data. It is the CACHE, not a
// context provider, that keeps that cheap: `state/queryCache` dedups by URL, so N widgets
// asking for one resource cost ONE request, and a dashboard that mounts none of them pays
// nothing. A provider that fetched eagerly for the whole tab would charge every dashboard for
// data no mounted widget renders.
//
// ONE SPELLING PER RESOURCE. The cache key IS the URL, so two spellings of the same resource
// are two cache entries and two identical requests. That bit once already: `usePlanningSummary()`
// and `usePlanningSummary(currentMonth)` resolved to the same month but built different keys, so
// a dashboard with the Calendar widget fetched the month twice. Hence `resolveMonth` below —
// the parameter is always present in the key, never sometimes.

import { useDataVersion } from '../useDataVersion'
import { type QueryCacheResult, useQueryCache } from '../../state/queryCache'
import { fetchJson } from './fetchJson'
import {
  CASHFLOW_PROJECTIONS_KEY,
  COMMITMENTS_SUMMARY_KEY,
  commitmentsCoverageKey,
  DIVIDEND_SUMMARY_KEY,
  NETWORTH_STATS_KEY,
  PORTFOLIO_ANALYTICS_KEY,
} from './keys'
import type { PortfolioCoverage } from '../../types/commitments'
import type {
  CashFlowGraph,
  CashflowProjections,
  CommitmentsSummary,
  DividendSummary,
  NetWorthStats,
  PlanningSummary,
  PortfolioAnalytics,
  ReportPeriod,
  ReportsRollup,
  SpendByWeek,
} from '../../types/aggregates'

const PORTFOLIO_SIGNALS = ['portfolio', 'ledger', 'accounts', 'assets'] as const
const PLANNING_SIGNALS = ['ledger', 'accounts', 'budget'] as const

/** Current month as `YYYY-MM`, so an omitted argument and an explicit current month produce
 *  the SAME cache key rather than two entries for one resource. */
function resolveMonth(ym?: string): string {
  if (ym) return ym
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

/**
 * Views derived from the portfolio: allocation, reachability, venues, KPI figures, risk flags,
 * liabilities, FX exposure, unrealized gains, today's movers, the holdings treemap, allocation
 * drift, and the invested `book`.
 *
 * Carries no rows — those are `GET /api/portfolio` (`usePortfolio`). A widget needing both gets
 * two cache-deduped reads instead of the row set twice on the wire.
 */
export function usePortfolioAnalytics(): QueryCacheResult<PortfolioAnalytics> {
  const version = useDataVersion([...PORTFOLIO_SIGNALS])
  return useQueryCache<PortfolioAnalytics>(
    PORTFOLIO_ANALYTICS_KEY,
    () => fetchJson<PortfolioAnalytics>(PORTFOLIO_ANALYTICS_KEY),
    { version },
  )
}

/** The monthly net-worth series plus the range deltas and month-to-date change over it. */
export function useNetWorthStats(): QueryCacheResult<NetWorthStats> {
  const version = useDataVersion([...PORTFOLIO_SIGNALS])
  return useQueryCache<NetWorthStats>(
    NETWORTH_STATS_KEY,
    () => fetchJson<NetWorthStats>(NETWORTH_STATS_KEY),
    { version },
  )
}

/** The upcoming-commitments feed, its next-30-days total, and the coverage verdict. */
export function useCommitmentsSummary(): QueryCacheResult<CommitmentsSummary> {
  const version = useDataVersion(['ledger', 'accounts', 'commitments', 'assets'])
  return useQueryCache<CommitmentsSummary>(
    COMMITMENTS_SUMMARY_KEY,
    () => fetchJson<CommitmentsSummary>(COMMITMENTS_SUMMARY_KEY),
    { version },
  )
}

/** The portfolio funding verdict for a look-ahead window. Every reader of the default window
 *  shares one cache entry (see `commitmentsCoverageKey`), so the Dashboard's "Funding shortfall"
 *  flag and the Commitments masthead can never quote two fetches of it. */
export function useCommitmentsCoverageRead(
  months?: number,
  floor?: number,
): QueryCacheResult<PortfolioCoverage> {
  const version = useDataVersion(['ledger', 'accounts', 'commitments', 'assets'])
  const key = commitmentsCoverageKey(months, floor)
  return useQueryCache<PortfolioCoverage>(key, () => fetchJson<PortfolioCoverage>(key), {
    version,
  })
}

/** One month rolled up: envelopes, goals, month-over-month moves, spend calendar, recurring,
 *  cash flow, Ready-to-Assign. */
export function usePlanningSummary(ym?: string): QueryCacheResult<PlanningSummary> {
  const version = useDataVersion([...PLANNING_SIGNALS])
  const key = `/api/planning/summary?ym=${resolveMonth(ym)}`
  return useQueryCache<PlanningSummary>(key, () => fetchJson<PlanningSummary>(key), { version })
}

/** Trailing-window income/expense rollups. */
/** A report's scope: a period (omitted: the widget's long-standing default window), and the
 *  tags and categories (ids) it is narrowed to. */
export interface ReportScope {
  /** A named period, or a custom range "YYYY-MM:YYYY-MM". */
  period?: ReportPeriod | string
  tags?: string[]
  categories?: string[]
}

function scopeQuery({ period, tags, categories }: ReportScope = {}): string {
  const parts = period ? [`period=${period}`] : []
  for (const tag of [...(tags ?? [])].sort()) parts.push(`tag=${encodeURIComponent(tag)}`)
  for (const id of [...(categories ?? [])].sort()) parts.push(`category=${encodeURIComponent(id)}`)
  return parts.length ? `&${parts.join('&')}` : ''
}

export function useReportsRollup(
  months = 6,
  merchantLimit = 10,
  scope: ReportScope = {},
): QueryCacheResult<ReportsRollup> {
  const version = useDataVersion([...PLANNING_SIGNALS])
  const key = `/api/reports/rollup?months=${months}&merchant_limit=${merchantLimit}${scopeQuery(scope)}`
  return useQueryCache<ReportsRollup>(key, () => fetchJson<ReportsRollup>(key), { version })
}

/** One month's money-flow (Sankey) graph — or a whole period's, given `scope.period`. `month` is
 *  YYYY-MM; the key always carries it. */
export function useCashFlowGraph(
  month: string,
  compact = false,
  scope: ReportScope = {},
): QueryCacheResult<CashFlowGraph> {
  const version = useDataVersion([...PLANNING_SIGNALS])
  const key = `/api/cashflow/flow?month=${month}&compact=${compact}${scopeQuery(scope)}`
  return useQueryCache<CashFlowGraph>(key, () => fetchJson<CashFlowGraph>(key), { version })
}

/** Dividends received YTD, the next 90 days of payments, and the 12-month forecast. */
export function useDividendSummary(): QueryCacheResult<DividendSummary> {
  const version = useDataVersion(['portfolio', 'ledger'])
  return useQueryCache<DividendSummary>(
    DIVIDEND_SUMMARY_KEY,
    () => fetchJson<DividendSummary>(DIVIDEND_SUMMARY_KEY),
    { version },
  )
}

/** Passive-income coverage and the cash-runway depletion curve. */
export function useCashflowProjections(): QueryCacheResult<CashflowProjections> {
  const version = useDataVersion([...PORTFOLIO_SIGNALS])
  return useQueryCache<CashflowProjections>(
    CASHFLOW_PROJECTIONS_KEY,
    () => fetchJson<CashflowProjections>(CASHFLOW_PROJECTIONS_KEY),
    { version },
  )
}

/** A free-text spend query summed into Monday-start weeks. */
export function useSpendByWeek(query: string, weeks = 6): QueryCacheResult<SpendByWeek> {
  const version = useDataVersion(['ledger'])
  const key = `/api/transactions/spend-by-week?q=${encodeURIComponent(query)}&weeks=${weeks}`
  return useQueryCache<SpendByWeek>(key, () => fetchJson<SpendByWeek>(key), { version })
}
