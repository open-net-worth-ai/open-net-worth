// usePortfolio — the app's shared holdings feed. It FETCHES; it does not compute.
//
// Every monetary field on a row (base value, cost basis, unrealized P&L, day P&L, portfolio
// weight, the display-currency per-unit price/cost, "since grant" P&L) is computed by
// `backend/services/portfolio_rows.py` and served by `GET /api/portfolio/rows`. This hook
// renames those fields onto the `ComputedRow` shape the app's components already read, and
// does nothing else.
//
// It used to own a full second implementation of the app's money math: qty x price x
// quote_per_unit, FX-converted per leg, cost basis, P&L, day P&L, and a weight for every row —
// running in the browser beside the backend's own fold. Two implementations of one thing
// drift, and this pair did: a S$120,000 net-worth headline against a true S$98,000, plus
// allocation bands that summed past 100%. See
// ADR 2026-07-31-single-source-net-worth and ADR 2026-07-31-overview-aggregates-are-server-computed.
//
// The `_`-prefixed field names are kept deliberately. They are read in ~40 components; renaming
// them would be a mechanical churn with no correctness benefit, and the rename below is the one
// place the mapping is stated.

import { useCallback, useEffect, useRef, useState } from 'react'
import { getBaseCurrency } from '../utils/format'
import type { ComputedRow, ServerPortfolioRow } from '../types/portfolio'
import {
  fetchCachedQuery,
  getCachedQueryData,
  getCachedQueryUpdatedAt,
  invalidateQueryCache,
  setCachedQueryData,
} from '../state/queryCache'
import {
  NET_WORTH_KEY,
  NETWORTH_STATS_KEY,
  PORTFOLIO_KEY,
  PORTFOLIO_LAST_KNOWN_KEY,
  fetchJson,
} from './serverState'

const NUMERIC = new Set([
  'quantity',
  'price',
  'cost_basis',
  '_base',
  '_pct',
  '_cost_base',
  '_pl',
  '_plpct',
  '_daily_pl',
  '_daily_plpct',
])

/** Rename a server-valued row onto the `ComputedRow` shape. A pure field mapping — every
 *  number is copied across exactly as the backend computed it. Nothing here adds, subtracts,
 *  divides or converts, and nothing here may start to. */
export function adaptServerRow(row: ServerPortfolioRow): ComputedRow {
  return {
    ...row,
    _base: row.base_value,
    _pct: row.pct_of_portfolio,
    _cost_base: row.cost_base,
    _disp_ccy: row.display_currency,
    _disp_unit_price: row.display_unit_price,
    _disp_unit_cost: row.display_unit_cost,
    _pl: row.pl,
    _plpct: row.pl_pct,
    _daily_pl: row.daily_pl,
    _daily_plpct: row.daily_pl_pct,
    _prev: null,
    _ref_pl: row.ref_pl,
    _ref_plpct: row.ref_pl_pct,
  } as ComputedRow
}

export function sortRows(rows: ComputedRow[], key: string, dir: number): ComputedRow[] {
  return [...rows].sort((a, b) => {
    const ar = a as unknown as Record<string, unknown>
    const br = b as unknown as Record<string, unknown>
    let x: number | string = ar[key] as number | string
    let y: number | string = br[key] as number | string
    if (NUMERIC.has(key)) {
      x = parseFloat(x as string) || 0
      y = parseFloat(y as string) || 0
    } else {
      x = String(x).toLowerCase()
      y = String(y).toLowerCase()
    }
    return x < y ? -dir : x > y ? dir : 0
  })
}

export interface PortfolioState {
  rows: ComputedRow[]
  positionRows: ComputedRow[]
  /** Sum of every vested portfolio row (positions + accounts), base currency — computed
   *  SERVER-side. This is a PORTFOLIO/ALLOCATION total, the denominator for allocation %,
   *  venue breakdowns and "% of holdings" figures. It is NOT net worth: do not render it as
   *  one. For net worth use `useNetWorth()` / `GET /api/net-worth`. */
  portfolioTotal: number
  /** Unvested grant units, base currency — EXCLUDED from `portfolioTotal` (not owned yet),
   *  reported alongside it. Mirrors the backend's `unvested_base`. */
  unvested: number
  fx: Record<string, number>
  isLive: boolean
  loading: boolean
  /** True once there is content worth rendering — a completed load OR the persisted cold-start
   *  seed. Lets callers keep content mounted during background refreshes instead of unmounting
   *  on every `loading` flip, and lets a reopened PWA render before any request resolves. */
  hasLoaded: boolean
  /** The last read failed and there is nothing to show. Not the same as an empty book: a
   *  screen must say it couldn't load, never offer a long-time owner "add your first
   *  account" (QA 2026-10-13: a failed read drew both new-user screens on a full book). */
  loadFailed: boolean
  lastUpdated: Date | null
  refresh: () => void
}

interface PortfolioRowsResponse {
  base_currency: string
  rows: ServerPortfolioRow[]
  portfolio_total: number
  unvested_base: number
  is_live: boolean
  fx: Record<string, number>
}

// Fire the sector backfill at most once per session (the endpoint itself is idempotent; this
// just avoids re-POSTing every 60s refresh). Module-scoped so it survives re-renders.
let sectorsEnrichTried = false

// ── Cold start: paint first, price second (2026-07-31) ───────────────────────
//
// `GET /api/portfolio` defaults to live=1, which does a real Yahoo/CoinGecko round trip
// (10-15s provider timeouts) BEFORE it responds. Gating the first render on that made every
// open of the installed iOS PWA — where each reopen is a cold start, see the persistence
// section of `state/queryCache.ts` — a multi-second "Loading portfolio…" followed by the whole
// tab tree mounting at once. So a cold start now goes through three stages, each one a strictly
// better answer than the last, and NONE of them blocking a paint:
//
//   1. the cache hydrated from localStorage (instant, possibly a previous session's),
//   2. `live=0` — last-known prices straight off disk, no provider call, ~50ms,
//   3. `live=1` — the real refresh, which replaces everything above when it lands.
//
// Stages 2 and 3 run in SEQUENCE, not in parallel: two concurrent portfolio projections cost
// double the CPU on a single-process GIL-bound backend, which is the exact contention the
// server-state consolidation exists to avoid, and stage 2 is cheap enough that it barely
// delays stage 3.

export function usePortfolio(): PortfolioState {
  // The seed. `state/queryCache` hydrates itself from localStorage before React renders, so on
  // a reopened PWA this is last session's response and there is content on screen from the very
  // first frame. Read once per mount — a remount after `clearQueryCache()` (logout) correctly
  // sees nothing.
  const [seed] = useState(() => getCachedQueryData<PortfolioRowsResponse>(PORTFOLIO_KEY) ?? null)
  const [seedAt] = useState(() => getCachedQueryUpdatedAt(PORTFOLIO_KEY))
  const [seedRows] = useState<ComputedRow[]>(() => (seed?.rows ?? []).map(adaptServerRow))

  const [rows, setRows] = useState<ComputedRow[]>(seedRows)
  const [positionRows, setPositionRows] = useState<ComputedRow[]>(() =>
    seedRows.filter((r) => r.ticker),
  )
  const [portfolioTotal, setPortfolioTotal] = useState(seed?.portfolio_total ?? 0)
  const [unvested, setUnvested] = useState(seed?.unvested_base ?? 0)
  const [fx, setFx] = useState<Record<string, number>>(() => {
    const base = getBaseCurrency()
    try {
      return { [base]: 1.0, ...JSON.parse(localStorage.getItem('wealthFX') || '{}') }
    } catch {
      return { [base]: 1.0 }
    }
  })
  // A seeded render is never live: those prices came off disk, at an unknown age.
  const [isLive, setIsLive] = useState(false)
  const [loading, setLoading] = useState(true)
  // Seeded content is real content — `hasLoaded` is what keeps the shell from unmounting the
  // tab tree, and there is nothing to wait for when we already have rows to show.
  const [hasLoaded, setHasLoaded] = useState(!!seed)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(seedAt ? new Date(seedAt) : null)
  const [loadFailed, setLoadFailed] = useState(false)
  // False only for the very first load; see the `force` note in `load` below.
  const hasLoadedOnceRef = useRef(false)
  // The total of the last response applied, to tell when a live refresh actually moved prices.
  const appliedTotalRef = useRef<number | null>(null)

  /** Publish one response to every consumer and persist it as the next cold start's seed. */
  const apply = useCallback((data: PortfolioRowsResponse) => {
    const adapted = data.rows.map(adaptServerRow)
    setRows(adapted)
    setPositionRows(adapted.filter((r) => r.ticker))
    setPortfolioTotal(data.portfolio_total)
    setUnvested(data.unvested_base)
    setIsLive(data.is_live)
    if (data.fx && Object.keys(data.fx).length) {
      setFx(data.fx)
      // Kept for the first-paint seed above, so a reload renders with known rates before the
      // fetch resolves. It is a CACHE, never an input to a calculation any more.
      localStorage.setItem('wealthFX', JSON.stringify(data.fx))
    }
    setLastUpdated(new Date())
    setHasLoaded(true)
    setLoadFailed(false)
  }, [])

  const load = useCallback(
    async (silent = false) => {
      if (!silent) setLoading(true)
      const first = !hasLoadedOnceRef.current
      hasLoadedOnceRef.current = true
      try {
        if (first) {
          // Stage 2. Failure here is not an error path: the live read below is the real one,
          // and a seeded or empty render is what the user sees until it lands.
          try {
            const lastKnown = await fetchJson<PortfolioRowsResponse>(PORTFOLIO_LAST_KNOWN_KEY)
            apply(lastKnown)
            appliedTotalRef.current = lastKnown.portfolio_total
            // Write-through so the OTHER readers of this resource (the Accounts tab's holdings
            // map, commitment funding values) paint from last-known prices too instead of each
            // waiting on the live call. Same freshness class as what the backend has on disk.
            setCachedQueryData(PORTFOLIO_KEY, lastKnown)
          } catch (err) {
            console.warn('Portfolio last-known load failed; waiting on live prices:', err)
          }
        }
        // Stage 3. Always forced: the entry was just written through above, and every later
        // call is an explicit refresh (the 60s interval, a post-mutation `refresh()`, the
        // sector-enrich retry) that must hit the network rather than a warm entry. Dedup is
        // unaffected — `force` bypasses a COMPLETED entry, never an in-flight one.
        const data = await fetchCachedQuery<PortfolioRowsResponse>(
          PORTFOLIO_KEY,
          () => fetchJson<PortfolioRowsResponse>(PORTFOLIO_KEY),
          { force: true },
        )
        const movedPrices =
          data.is_live &&
          appliedTotalRef.current != null &&
          data.portfolio_total !== appliedTotalRef.current
        apply(data)
        appliedTotalRef.current = data.portfolio_total
        // New live prices change net worth too, but a price write sends no data_changed event,
        // so a net worth read made before they landed would stay on screen beside holdings valued
        // after: the headline and its "this month" change then sat on two price sets. Refresh
        // both net worth reads, and only when the prices actually moved.
        if (movedPrices) {
          invalidateQueryCache((key) => key === NET_WORTH_KEY || key === NETWORTH_STATS_KEY)
        }

        // One-shot: backfill GICS sectors for the concentration risk lens, then refresh so the
        // newly-classified rows flow in. Skipped once every equity already carries a sector.
        if (!sectorsEnrichTried && data.rows.some((r) => r.asset_class === 'equity' && !r.sector)) {
          sectorsEnrichTried = true
          const { authFetch } = await import('../api/client')
          authFetch('/api/instruments/enrich-sectors', { method: 'POST' })
            .then((r) => (r.ok ? r.json() : null))
            .then((res) => {
              if (res && res.updated > 0) load(true)
            })
            .catch(() => {})
        }
      } catch (err) {
        console.error('Portfolio load error:', err)
        setLoadFailed(true)
      } finally {
        setLoading(false)
        setHasLoaded(true)
      }
    },
    [apply],
  )

  useEffect(() => {
    load()
    const interval = setInterval(() => load(true), 60_000)
    return () => clearInterval(interval)
  }, [load])

  return {
    rows,
    positionRows,
    portfolioTotal,
    unvested,
    fx,
    isLive,
    loading,
    hasLoaded,
    loadFailed,
    lastUpdated,
    refresh: load,
  }
}
