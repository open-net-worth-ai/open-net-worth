import { useCallback, useEffect, useRef, useState } from 'react'
import { authFetch } from '../../../api/client'
import { invalidateQueryCache } from '../../../state/queryCache'
import { useDataVersion } from '../../../hooks/useDataVersion'
import { usePreferredBenchmark } from '../../../hooks/usePreferredBenchmark'
import { PROFILE_KEY } from '../../../hooks/serverState'
import { fetchBenchmarkRoster, fetchReturnsWithRisk } from '../../../api/portfolio'
import type {
  BenchmarkRosterItem,
  FallbackPriced,
  PortfolioReturns,
  ReturnPeriod,
  ReturnSeriesPoint,
  RiskMetrics,
} from '../../../types/portfolio'
import { fBase, fPct } from '../../../utils/format'
import { ViewCache } from './viewCache'
import { rememberPerformancePeriod, rememberedPerformancePeriod } from './performancePeriod'

// The picked benchmark is the ONE "vs the market" index: saved to the profile so the Intel
// Holdings brief and the watch pulse (and their server-side stories) compare against it too.
// Fire-and-forget — a failed save only means Intel keeps its previous benchmark.
function persistPreferredBenchmark(key: string): void {
  if (!key) return
  void authFetch('/api/profile', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ preferred_benchmark: key }),
  })
    .then((res) => {
      if (res.ok)
        invalidateQueryCache(
          (k) =>
            k === PROFILE_KEY ||
            k.startsWith('/api/intel/digest') ||
            k.startsWith('/api/intel/watch-pulse') ||
            k.startsWith('/api/returns'),
        )
    })
    .catch(() => undefined)
}

type PerfData = PortfolioReturns & { risk: RiskMetrics | null }

// Module-level caches — survive sub-tab unmount/remount within a session.
const _dataCache = new ViewCache<PerfData>('performance')
const _rosterCache = new ViewCache<BenchmarkRosterItem[]>('benchmark-roster')

// A signed percent string from a fraction (e.g. 0.142 → "+14.20%").
function fReturn(frac: number): string {
  return fPct(frac, { fraction: true, sign: true })
}

export interface PerfStatCard {
  key: string
  label: string
  value: string
  tone: 'pos' | 'neg' | undefined
  help: string
}

export interface PerfRiskMetrics {
  vol: number | null // percentage (e.g. 15.1)
  beta: number | null
  betaSymbol: string // e.g. "BTC" — the symbol beta was computed against
  sharpe: number | null
  mdd: number | null // negative percentage (e.g. -17.2)
  summary: string
}

export interface PerformanceViewModel {
  // State
  period: ReturnPeriod
  setPeriod: (p: ReturnPeriod) => void
  benchKey: string
  setBenchKey: (k: string, added?: BenchmarkRosterItem) => void

  // Status
  loading: boolean
  error: boolean

  // Roster
  benchmarks: BenchmarkRosterItem[]

  // Data: null when insufficient or error
  data: (PortfolioReturns & { risk: RiskMetrics | null }) | null
  sufficient: boolean

  // Derived stat cards for the StatBar
  returnCards: PerfStatCard[]
  benchCards: PerfStatCard[]

  // Chart series (fractions × 100 → %)
  series: ReturnSeriesPoint[]
  hasBenchmark: boolean
  benchLabel: string // e.g. "SPY • S&P 500" for the legend
  benchSymbol: string // e.g. "SPY" for the "Excess vs …" card

  // Clamped-window label
  windowLabel: string
  isClamped: boolean
  /** Which holdings were valued at a manual mark or at cost on days with no price. */
  fallbackNote: string | null

  // Risk metrics (null = not enough data)
  risk: PerfRiskMetrics | null
}

function tone(v: number | null | undefined): 'pos' | 'neg' | undefined {
  if (v == null) return undefined
  if (v > 0) return 'pos'
  if (v < 0) return 'neg'
  return undefined
}

export function usePerformanceView(): PerformanceViewModel {
  const [period, setPeriodState] = useState<ReturnPeriod>(rememberedPerformancePeriod)
  const setPeriod = useCallback((p: ReturnPeriod) => {
    setPeriodState(p)
    rememberPerformancePeriod(p)
  }, [])
  // The benchmark is the profile's `preferred_benchmark`; `picked` is this view's explicit
  // choice, shown at once while the save lands. Only an explicit pick is written back.
  const preferred = usePreferredBenchmark()
  const [picked, setPicked] = useState<string | null>(null)
  const benchKey = picked ?? preferred
  const [benchmarks, setBenchmarks] = useState<BenchmarkRosterItem[]>(
    () => _rosterCache.get('roster') ?? [],
  )
  // `added`: a ticker just picked from the market search — it heads the list as "Your benchmark"
  // (replacing an earlier one), as the server's roster lists it from now on.
  const setBenchKey = useCallback((key: string, added?: BenchmarkRosterItem) => {
    setPicked(key)
    persistPreferredBenchmark(key)
    if (added) {
      setBenchmarks((rows) => {
        const next = [
          added,
          ...rows.filter((b) => b.group !== 'Your benchmark' && b.key !== added.key),
        ]
        _rosterCache.set('roster', next)
        return next
      })
    }
  }, [])

  const dataKey = `${period}|${benchKey}`
  const [data, setData] = useState<PerfData | null>(() => _dataCache.get(dataKey) ?? null)
  const [loading, setLoading] = useState(() => _dataCache.get(dataKey) == null)
  const [error, setError] = useState(false)

  // Load the curated roster once (use cache on remount).
  useEffect(() => {
    if (_rosterCache.get('roster') != null) return
    let alive = true
    fetchBenchmarkRoster()
      .then((rows) => {
        if (!alive) return
        _rosterCache.set('roster', rows)
        setBenchmarks(rows)
      })
      .catch(() => {
        /* roster stays empty — benchmark picker will be disabled */
      })
    return () => {
      alive = false
    }
  }, [])

  // Load returns + risk whenever period or benchmark changes, or Lucius writes anything.
  const anyWriteV = useDataVersion('*')
  const seenWriteRef = useRef(0)
  const aliveRef = useRef(true)
  useEffect(() => {
    aliveRef.current = true
    if (anyWriteV !== seenWriteRef.current) {
      seenWriteRef.current = anyWriteV
      _dataCache.invalidate(dataKey)
    }
    const cached = _dataCache.get(dataKey)
    if (cached != null) {
      setData(cached)
      setLoading(false)
      setError(false)
    } else {
      setLoading(true)
    }
    fetchReturnsWithRisk(period, benchKey || undefined)
      .then((d) => {
        if (aliveRef.current) {
          _dataCache.set(dataKey, d)
          setData(d)
          setError(false)
          setLoading(false)
        }
      })
      .catch(() => {
        if (aliveRef.current) {
          setData(null)
          setError(true)
          setLoading(false)
        }
      })
    return () => {
      aliveRef.current = false
    }
  }, [period, benchKey, dataKey, anyWriteV])

  const sufficient = !!data?.data_quality.sufficient
  const annualized = data?.annualized ?? false
  const twr = data?.twr ?? null
  const mwr = data?.mwr ?? null
  const twrValue = sufficient && twr ? (annualized ? twr.annualized : twr.period_return) : null

  // MWR: show whenever mwr is non-null (gate lowered to 30d). Use period_return so the
  // value is de-annualized for sub-year spans. Label differs: "MWR (ann.)" when the span
  // is ≥ 360d (the IRR is inherently annual), else "MWR (your IRR)" for shorter spans.
  const mwrValue = mwr != null ? mwr.period_return : null
  const mwrAnnualized = mwr?.annualized ?? false

  const comparison = benchKey ? (data?.comparison ?? null) : null
  const benchTwr = comparison?.benchmark_twr ?? null
  const excess = comparison?.excess_return ?? null
  const benchMeta = benchmarks.find((b) => b.key === benchKey)
  const benchLabel = benchMeta?.label ?? benchKey
  const benchSymbol = comparison?.benchmark.symbol ?? benchKey

  const windowLabel = data?.label ?? ''
  const isClamped = windowLabel.startsWith('Since')
  const fallbackNote = fallbackPricedNote(data?.data_quality.fallback_priced ?? [])

  // Return stat cards (matches prototype: TWR, MWR, Period gain)
  const returnCards: PerfStatCard[] = data
    ? [
        {
          key: 'twr',
          label: annualized ? 'TWR (ann.)' : 'TWR',
          value: twrValue != null ? fReturn(twrValue) : '—',
          tone: tone(twrValue),
          help:
            "Time-weighted return. The portfolio's performance stripped of the timing of your deposits and withdrawals" +
            (annualized ? ', shown annualized' : '') +
            ' — the standard for comparing against a benchmark.',
        },
        ...(mwr !== null
          ? [
              {
                key: 'mwr',
                label: mwrAnnualized ? 'MWR (ann.)' : 'MWR (your IRR)',
                value: mwrValue != null ? fReturn(mwrValue) : '—',
                tone: tone(mwrValue),
                help: mwrAnnualized
                  ? 'Money-weighted return, annualized — your personal internal rate of return over a full year. Unlike TWR it counts the size and timing of your cash flows.'
                  : 'Money-weighted return — your personal internal rate of return over this period. Unlike TWR it counts the size and timing of your cash flows, so it answers "how did my actual money do?"',
              } satisfies PerfStatCard,
            ]
          : []),
        {
          key: 'pg',
          label: 'Period gain',
          value: (data.period_gain_base >= 0 ? '+' : '') + fBase(data.period_gain_base),
          tone: tone(data.period_gain_base),
          help: "The change in your portfolio's value over this period in dollars — market moves plus income earned.",
        },
      ]
    : []

  // Benchmark cards (appended when a comparison is available)
  const benchCards: PerfStatCard[] =
    benchKey && comparison && benchTwr !== null
      ? [
          {
            key: 'be',
            label: benchSymbol,
            value: fReturn(benchTwr),
            tone: tone(benchTwr),
            help: "The benchmark's total return over the same period, for a like-for-like comparison.",
          },
          {
            key: 'ex',
            label: `Excess vs ${benchSymbol}`,
            value: excess !== null ? fReturn(excess) : '—',
            tone: tone(excess),
            help: "Your TWR minus the benchmark's — how many points you beat (or trailed) the index by. Positive means you're ahead.",
          },
        ]
      : []

  const hasBenchmark = !!(benchKey && comparison && benchTwr !== null)

  // Risk metrics: convert fractions → percentages for display
  const rawRisk = data?.risk ?? null
  const risk: PerfRiskMetrics | null = rawRisk
    ? {
        vol: rawRisk.volatility != null ? rawRisk.volatility * 100 : null,
        beta: rawRisk.beta_vs_spy,
        betaSymbol: rawRisk.beta_symbol ?? 'SPY',
        sharpe: rawRisk.sharpe,
        mdd: rawRisk.max_drawdown != null ? rawRisk.max_drawdown * 100 : null,
        summary: rawRisk.summary,
      }
    : null

  return {
    period,
    setPeriod,
    benchKey,
    setBenchKey,
    loading,
    error,
    benchmarks,
    data,
    sufficient,
    returnCards,
    benchCards,
    series: data?.series ?? [],
    hasBenchmark,
    benchLabel,
    benchSymbol,
    windowLabel,
    isClamped,
    fallbackNote,
    risk,
  }
}

/** One sentence naming the holdings valued without a recorded price, or null. */
export function fallbackPricedNote(rows: FallbackPriced[]): string | null {
  if (rows.length === 0) return null
  const parts = rows.map((r) => {
    const label = r.symbol || r.name
    return r.source === 'cost'
      ? `${label} is valued at what you paid from ${r.from} to ${r.to}`
      : `${label} is valued at its manual price`
  })
  return `Some holdings have no price history for part of this range: ${parts.join('; ')}.`
}
