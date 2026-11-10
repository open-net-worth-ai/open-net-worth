// Settings → AI usage. Cost and performance are operational telemetry, kept separate from
// provider credentials and routing so the AI section stays focused on configuration.
import { useState } from 'react'
import { authFetch } from '../../../../api/client'
import { useCachedFetchState } from '../../../../state/queryCache'
import { ChevronDown, Check } from '../../../../ui/icons'
import { fDate } from '../../../../utils/format'
import { Panel } from '../ds'
import type { SectionProps } from '../ds'
import { ModelTelemetryPanel } from './ModelTelemetryPanel'
import { SearchFetchCostPanel } from './SearchFetchCostPanel'

interface Totals {
  turns: number
  input_tokens: number
  cache_read_tokens: number
  output_tokens: number
  cost_usd: number
}

interface CostRecord {
  ts: string
  model: string
  provider?: string
  cost_usd: number
}

interface CostSummary {
  total: Totals
  by_model: Record<string, Totals>
  by_provider: Record<string, Totals>
  by_search_fetch: Record<string, Totals>
  by_surface: Record<string, Totals>
  by_day: ({ date: string } & Totals)[]
  by_hour: ({ hour: string } & Totals)[]
  recent: CostRecord[]
  window_days: number
}

type CostWindow = 'today' | 'yesterday' | '7' | '30' | '0'

const COST_WINDOWS: readonly (readonly [CostWindow, string])[] = [
  ['today', 'Today'],
  ['yesterday', 'Yesterday'],
  ['7', '7 days'],
  ['30', '30 days'],
  ['0', 'All time'],
]

async function getJson<T>(path: string): Promise<T> {
  const response = await authFetch(path)
  if (!response.ok) throw new Error(String(response.status))
  return response.json() as Promise<T>
}

const usd = (value: number) => {
  const abs = Math.abs(value)
  return 'US$' + value.toFixed(abs > 0 && abs < 0.01 ? 4 : 2)
}

const tok = (value: number) =>
  value >= 1e6
    ? (value / 1e6).toFixed(1) + 'M'
    : value >= 1e3
      ? Math.round(value / 1e3) + 'k'
      : String(value)

const trendUsd = (value: number) => `US$${value.toFixed(Math.abs(value) < 0.1 ? 4 : 2)}`

function localDateKey(daysAgo = 0) {
  const date = new Date()
  date.setHours(12, 0, 0, 0)
  date.setDate(date.getDate() - daysAgo)
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

interface TrendPoint {
  key: string
  label: string
  cost: number
  turns: number
}

function costTrendPoints(summary: CostSummary, window: CostWindow): TrendPoint[] {
  if (window === 'today' || window === 'yesterday') {
    const hourly = new Map((summary.by_hour ?? []).map((row) => [row.hour.slice(-2), row]))
    return Array.from({ length: 24 }, (_, hour) => {
      const key = String(hour).padStart(2, '0')
      const row = hourly.get(key)
      return {
        key,
        label: new Date(2026, 0, 1, hour).toLocaleTimeString(undefined, { hour: 'numeric' }),
        cost: row?.cost_usd ?? 0,
        turns: row?.turns ?? 0,
      }
    })
  }

  const rows = (summary.by_day ?? []).filter((row) => /^\d{4}-\d{2}-\d{2}$/.test(row.date))
  if (!rows.length) return []
  const byDate = new Map(rows.map((row) => [row.date, row]))
  const startKey =
    window === '7' ? localDateKey(6) : window === '30' ? localDateKey(29) : rows[0]!.date
  const endKey = window === '0' ? rows[rows.length - 1]!.date : localDateKey()
  const start = new Date(`${startKey}T12:00:00`)
  const end = new Date(`${endKey}T12:00:00`)
  const dayCount = Math.max(1, Math.round((end.getTime() - start.getTime()) / 86_400_000) + 1)
  const bucketSize = Math.max(1, Math.ceil(dayCount / 30))
  const points: TrendPoint[] = []
  for (let offset = 0; offset < dayCount; offset += bucketSize) {
    let cost = 0
    let turns = 0
    let lastKey = startKey
    for (let inner = 0; inner < bucketSize && offset + inner < dayCount; inner += 1) {
      const date = new Date(start)
      date.setDate(date.getDate() + offset + inner)
      const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
      const row = byDate.get(key)
      cost += row?.cost_usd ?? 0
      turns += row?.turns ?? 0
      lastKey = key
    }
    const first = new Date(start)
    first.setDate(first.getDate() + offset)
    const firstKey = `${first.getFullYear()}-${String(first.getMonth() + 1).padStart(2, '0')}-${String(first.getDate()).padStart(2, '0')}`
    points.push({
      key: firstKey,
      label: bucketSize === 1 ? fDate(firstKey) : `${fDate(firstKey)}–${fDate(lastKey)}`,
      cost,
      turns,
    })
  }
  return points
}

function CostTrendChart({ summary, window }: { summary: CostSummary; window: CostWindow }) {
  const points = costTrendPoints(summary, window)
  const [activePoint, setActivePoint] = useState<string | null>(null)
  if (!points.length || summary.total.turns === 0) return null
  const peak = Math.max(...points.map((point) => point.cost), 0.000001)
  const average = summary.total.cost_usd / summary.total.turns
  const middle = points[Math.floor(points.length / 2)]!

  return (
    <div className="rd-cost-trend" aria-label="Cost trend">
      <div className="rd-cost-trend-head">
        <span className="rd-cost-trend-title">Cost trend</span>
        <span className="rd-cost-trend-values">
          <span>{trendUsd(average)} avg / turn</span>
          <span>{trendUsd(peak)} peak</span>
        </span>
      </div>
      <div
        className="rd-cost-trend-plot"
        style={{ gridTemplateColumns: `repeat(${points.length}, minmax(0, 1fr))` }}
      >
        {points.map((point, index) => {
          const detail = `${point.label} · ${trendUsd(point.cost)} · ${point.turns} turn${point.turns === 1 ? '' : 's'}`
          const tooltipId = `cost-trend-tip-${point.key.replace(/[^a-z0-9]/gi, '-')}`
          const edge = index < 2 ? ' start' : index >= points.length - 2 ? ' end' : ''
          return (
            <span
              className="rd-cost-trend-bar"
              key={point.key}
              role="img"
              tabIndex={point.turns > 0 ? 0 : -1}
              aria-label={detail}
              aria-describedby={activePoint === point.key ? tooltipId : undefined}
              style={{ height: Math.max(2, Math.round((point.cost / peak) * 36)) }}
              onMouseEnter={() => setActivePoint(point.key)}
              onMouseLeave={() => setActivePoint(null)}
              onFocus={() => setActivePoint(point.key)}
              onBlur={() => setActivePoint(null)}
            >
              {activePoint === point.key ? (
                <span id={tooltipId} role="tooltip" className={`rd-cost-trend-tooltip${edge}`}>
                  {detail}
                </span>
              ) : null}
            </span>
          )
        })}
      </div>
      <div className="rd-cost-trend-axis" aria-hidden="true">
        <span>{points[0]!.label}</span>
        <span>{middle.label}</span>
        <span>{points[points.length - 1]!.label}</span>
      </div>
    </div>
  )
}

export function AiUsageSection(props: SectionProps) {
  void props
  const [window, setWindow] = useState<CostWindow>('today')
  const [menuOpen, setMenuOpen] = useState(false)
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone
  const costKey =
    window === 'today' || window === 'yesterday'
      ? `/api/cost/summary?range=${window}&tz=${timezone}`
      : window === '7' || window === '30'
        ? `/api/cost/summary?start=${localDateKey(Number(window) - 1)}&end=${localDateKey()}&tz=${timezone}`
        : `/api/cost/summary?days=0&tz=${timezone}`
  const cost = useCachedFetchState<CostSummary>(costKey, () => getJson<CostSummary>(costKey), {
    staleTime: 30_000,
  })
  const summary = cost.data
  const totalCost = summary?.total.cost_usd ?? 0
  const windowDays =
    window === 'today' || window === 'yesterday'
      ? 1
      : window === '7'
        ? 7
        : window === '30'
          ? 30
          : Math.max(1, summary?.window_days || 1)
  const projected = (totalCost / windowDays) * 30
  const windowLabel = COST_WINDOWS.find(([value]) => value === window)?.[1] ?? 'Today'
  const bars = (totals: Record<string, Totals> | undefined) =>
    Object.entries(totals ?? {})
      .map(([label, value]) => [label, value.cost_usd, value.turns] as [string, number, number])
      .sort((a, b) => b[1] - a[1])
  const searchFetch = bars(summary?.by_search_fetch)

  return (
    <>
      <div className="rd-cost-period-toolbar" aria-label="Cost period">
        <span className="rd-cost-period-label">Period</span>
        <div className="rd-cost-window">
          <div className="rd-seg rd-cost-window-segments" role="tablist" aria-label="Cost window">
            {COST_WINDOWS.map(([value, label]) => (
              <button
                type="button"
                key={value}
                role="tab"
                aria-selected={window === value}
                className={'rd-seg-b' + (window === value ? ' on' : '')}
                onClick={() => setWindow(value)}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="rd-cost-filter">
            <button
              type="button"
              className="rd-cost-filter-button"
              aria-label={`Cost window: ${windowLabel}`}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((open) => !open)}
            >
              <span>{windowLabel}</span>
              <ChevronDown size={14} strokeWidth={1.8} aria-hidden="true" />
            </button>
            {menuOpen ? (
              <div className="rd-cost-filter-menu" role="menu" aria-label="Cost window">
                {COST_WINDOWS.map(([value, label]) => (
                  <button
                    type="button"
                    key={value}
                    role="menuitem"
                    className={'rd-cost-filter-item' + (window === value ? ' on' : '')}
                    onClick={() => {
                      setWindow(value)
                      setMenuOpen(false)
                    }}
                  >
                    <span>{label}</span>
                    {window === value ? <Check size={12} aria-hidden="true" /> : null}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
        </div>
      </div>

      <Panel title="AI spend" sub="Metered per turn across every surface that calls a model">
        <div className="rd-stats rd-cost-stats">
          <div className="rd-stat">
            <span className="rd-stat-lbl">Total spend</span>
            <span className="rd-stat-val">{usd(totalCost)}</span>
          </div>
          <div className="rd-stat">
            <span className="rd-stat-lbl">Turns</span>
            <span className="rd-stat-val">{(summary?.total.turns ?? 0).toLocaleString()}</span>
          </div>
          <div className="rd-stat">
            <span className="rd-stat-lbl">Tokens in</span>
            <span className="rd-stat-val">{tok(summary?.total.input_tokens ?? 0)}</span>
          </div>
          <div className="rd-stat">
            <span className="rd-stat-lbl">Tokens out</span>
            <span className="rd-stat-val">{tok(summary?.total.output_tokens ?? 0)}</span>
          </div>
          <div className="rd-stat">
            <span className="rd-stat-lbl">Cache hits</span>
            <span className="rd-stat-val">{tok(summary?.total.cache_read_tokens ?? 0)}</span>
          </div>
        </div>
        {summary ? <CostTrendChart summary={summary} window={window} /> : null}
        {(
          [
            ['By surface', bars(summary?.by_surface)],
            ['By provider', bars(summary?.by_provider)],
            ['By model', bars(summary?.by_model)],
          ] as [string, [string, number, number][]][]
        ).map(([heading, list]) => (
          <div className="rd-bars" key={heading}>
            <div className="rd-bars-lbl">{heading}</div>
            {list.length ? (
              list.map(([label, value, turns]) => (
                <div className="rd-bar" key={label}>
                  <span className={'rd-bar-k' + (heading === 'By model' ? ' rd-mono' : '')}>
                    {label}
                  </span>
                  <span className="rd-bar-track">
                    <span
                      style={{ width: (totalCost ? (value / totalCost) * 100 : 0) + '%' }}
                    ></span>
                  </span>
                  <span className="rd-bar-v">
                    {usd(value)}
                    <em>
                      {turns} turn{turns === 1 ? '' : 's'}
                    </em>
                  </span>
                </div>
              ))
            ) : (
              <span className="rd-help">Nothing metered in this window.</span>
            )}
          </div>
        ))}
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 160 }}>
            <div style={{ fontSize: 'var(--text-2xs, 12.5px)', fontWeight: 600 }}>Projected</div>
            <div className="rd-help" style={{ marginTop: 3, display: 'block' }}>
              At this window's rate, extrapolated to 30 days.
            </div>
          </div>
          <b
            style={{
              fontSize: 'var(--text-xs, 13px)',
              fontWeight: 600,
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            {usd(projected)} / month est.
          </b>
        </div>
      </Panel>

      <SearchFetchCostPanel rows={searchFetch} formatCost={trendUsd} periodLabel={windowLabel} />

      <ModelTelemetryPanel />
    </>
  )
}
