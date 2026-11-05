// Overview v2 — Net worth widget group: hero, net-worth numbers, net-worth
// chart, asset allocation, reachability, where money is held. Ported from the
// production Overview.tsx (donut/bars/trend Chart.js lifecycles, the
// concentration/liquidity figures) onto the shared WidgetShell/OvChart/
// Breakdown foundation — the ONE Chart.js wrapper this redesign introduces
// (ADR 2026-07-22-overview-tab-redesign-faithful-port.md, decision 4).
//
// Hero/numbers/chart are ported class-by-class from the design handoff's
// `overview_v2b/hero.jsx` (`.ov-hero-nw`/`.ov-delta-chip`/`.ov-kpis`/
// `.ov-nums-grid`/`.ov-bars-legend`, defined in ./netWorth.css) — see the
// design-parity pass's report for the item-by-item mapping. The trend/
// composition MODE toggle intentionally has no inline widget-header control
// (prototype: "range/chart-mode are data controls, hosted in the detail
// panel — no header strip, one place for every widget setting"): both `hero`
// and `networth-chart` declare a `shape` in definitions.tsx, so
// WidgetFullscreen's generic "Show as" control (Appearance rail) already
// surfaces the mode picker with zero extra wiring here; `hero`'s `range`+
// `chart` (mode) and `networth-chart`'s `range` settings[] entries surface
// the same way in the Data rail. Nothing in definitions.tsx needed to change.

import type { ComponentType, ReactNode } from 'react'
import {
  ASSET_CLASS_COLOR,
  ASSET_CLASS_LABEL,
  ASSET_CLASS_ORDER,
  paletteAt,
} from '../../../../utils/constants'
import { fBase, fDate, fMonth, fPct, moneyClass, parseDate } from '../../../../utils/format'
import { Money, Skeleton, Text } from '../../../ui'
import type { WidgetContentProps } from '../types'
import { WidgetShell } from '../WidgetShell'
import { WidgetPlaceholder, WidgetSkeleton } from '../WidgetPlaceholder'
import { useFitContentWhen } from '../gridItemContext'
import { rowCap } from '../widgetDisplayShared'
import { useAnalytics, useNetWorthSeries } from '../useWidgetData'
import { OvChart, areaFill, axes, legend, moneyTip, resolveColor, withAlpha } from '../charts'
import { BreakdownBody, type BreakdownRow } from '../Breakdown'
import type { Breakdown, RangeStats } from '../../../../types/aggregates'
import { vizOf } from '../vizModel'

// Range labels the picker offers. The month counts themselves live on the SERVER
// (services/overview/history.py::RANGE_MONTHS) — these keys just name which precomputed
// range to read, so a range can never be sliced two different ways.
const RANGE_KEYS = ['1M', '3M', '6M', '1Y', 'All'] as const

function attribLabel(range: string, spanMonths: number): string {
  if (range === 'All') return spanMonths <= 0 ? 'tracked' : `${spanMonths}mo`
  return `${spanMonths}mo`
}

function fSigned(n: number): string {
  return (n >= 0 ? '+' : '') + fBase(n)
}

function monthYearLabel(iso: string): string {
  return fMonth(iso, 'short', 'always')
}

/** The server's precomputed stats for a named range — never a client-side slice + subtract. */
function useRangeStats(range: string): RangeStats | null {
  const { stats: networth } = useNetWorthSeries()
  const key = (RANGE_KEYS as readonly string[]).includes(range) ? range : '3M'
  return networth?.ranges?.[key] ?? null
}

/** The 7-month comparison the composition mode's delta chip shows. */
function useCompStats(): { delta: number; pct: number; sinceLabel: string } | null {
  const { stats: networth } = useNetWorthSeries()
  const comparison = networth?.comparison
  if (!comparison) return null
  return {
    delta: comparison.delta,
    pct: Math.abs(comparison.pct ?? 0),
    sinceLabel: monthYearLabel(comparison.from_date),
  }
}

// Which series actually carry data — decided server-side over the same history points, so a
// legend entry can't appear for a band the chart renders as flat zero. ASSET_CLASS_ORDER is
// applied here purely for display ORDER.
function compositionSeries(
  present: string[],
  hasLiab: boolean,
): { present: string[]; hasLiab: boolean } {
  return { present: ASSET_CLASS_ORDER.filter((c) => present.includes(c)), hasLiab }
}

// ── headline: total + delta chip (item 1) ───────────────────────────────────

function Headline({
  mode,
  range,
  inline,
}: {
  mode: 'trend' | 'comp'
  range: string
  inline?: boolean
}) {
  // The ONE net worth — server-computed (ledger.net_worth()), never a client-side sum of
  // portfolio rows. That duplicate drifted and shipped a S$120,000 headline against a true
  // S$98,000 (ADR 2026-07-31-single-source-net-worth).
  const { netWorth, loading: portfolioLoading, error: portfolioError } = useAnalytics()
  const comp = useCompStats()
  const trend = useRangeStats(range)

  if (portfolioLoading && netWorth == null) {
    const skeleton = <Skeleton height={44} width={200} />
    return inline ? <div className="ov-hero-figure">{skeleton}</div> : skeleton
  }
  if (portfolioError && netWorth == null) {
    const errorBody = <WidgetPlaceholder detail="Couldn't load your net worth — try reloading." />
    return inline ? <div className="ov-hero-figure">{errorBody}</div> : errorBody
  }

  const total = netWorth ?? 0
  const nw = (
    <div className="ov-hero-nw">
      <Money as="span">{fBase(total)}</Money>
    </div>
  )
  let deltaBody: ReactNode = null
  if (mode === 'comp' && comp) {
    const pos = comp.delta >= 0
    deltaBody = (
      <>
        <span className={'ov-delta-chip money' + (pos ? ' pos' : ' neg')}>
          {pos ? '↑' : '↓'} {fPct(comp.pct)}
        </span>
        <span className="ov-hero-attrib">
          since {comp.sinceLabel} — assets by class, net worth is the line
        </span>
      </>
    )
  } else if (trend) {
    const pos = trend.delta >= 0
    deltaBody = (
      <>
        <span className={'ov-delta-chip money' + (pos ? ' pos' : ' neg')}>
          {pos ? '↑' : '↓'} {fPct(trend.pct != null ? Math.abs(trend.pct) : 0)}
        </span>
        <span className="ov-hero-attrib">{attribLabel(range, trend.span_months)}</span>
      </>
    )
  }
  const delta = <div className="ov-hero-delta">{deltaBody}</div>
  if (inline) {
    return (
      <div className="ov-hero-figure">
        {nw}
        {delta}
      </div>
    )
  }
  return (
    <>
      {nw}
      {delta}
    </>
  )
}

// ── composition legend (item 4) ─────────────────────────────────────────────

function CompLegend({ present, hasLiab }: { present: string[]; hasLiab: boolean }) {
  return (
    <div className="ov-bars-legend">
      {present.map((c) => (
        <span key={c} className="ov-leg-item">
          <i style={{ background: ASSET_CLASS_COLOR[c] }} />
          {ASSET_CLASS_LABEL[c] || c}
        </span>
      ))}
      {hasLiab && (
        <span className="ov-leg-item">
          <i style={{ background: 'var(--bad)' }} />
          Liabilities
        </span>
      )}
      <span className="ov-leg-item">
        <i className="ov-leg-line" />
        Net worth
      </span>
    </div>
  )
}

// ── KPI strip (item 2): Today / Unrealized P&L / Cash on hand / Liabilities,
// each clickable → onNavigate.
//
// All four are read straight off the server aggregate. "Cash on hand" in particular is the
// BACKEND's `cash` band — Σ `cash` accounts, the same function and the same number as the
// Accounts sidebar and Plan → Cash Flow (ADR 2026-09-07-seven-account-types-one-axis). A
// rows-based total here would fold settlement cash in and report a different figure under the
// same label, which is the disagreement that ADR replaced. ─────────────────────────────

const EMPTY_KPIS = {
  today: 0,
  today_pct: 0,
  has_market: false,
  upl: 0,
  upl_known: false,
  cash: 0,
  debt: 0,
  debt_accounts: 0,
}

// Cash on hand is bank and wallet cash by design; a new investor's broker cash is not in it.
const CASH_HINT = 'Bank and wallet cash. Cash in investment accounts is under Investments.'

function useKpiFigures() {
  const { analytics: portfolio, netWorth, loading } = useAnalytics()
  const k = portfolio?.kpis ?? EMPTY_KPIS
  // An all-cash book's cash on hand IS its net worth — the same figure twice says nothing.
  const showCash = netWorth == null || Math.abs(k.cash - netWorth) >= 0.005
  return { ...k, showCash, loading }
}

function Kpi({
  label,
  value,
  tone,
  sub,
  hint,
  onClick,
}: {
  label: string
  value: string
  tone?: 'pos' | 'neg'
  sub?: string
  /** Hover text only: it adds no height to a card that is already full. */
  hint?: string
  onClick?: () => void
}) {
  const inner = (
    <>
      <span className="ov-kpi-l">{label}</span>
      <span className={moneyClass('ov-kpi-v', tone)}>{value}</span>
      {sub && <span className="ov-kpi-s">{sub}</span>}
    </>
  )
  if (onClick) {
    return (
      <button type="button" className="ov-kpi" title={hint} onClick={onClick}>
        {inner}
      </button>
    )
  }
  return (
    <div className="ov-kpi" title={hint}>
      {inner}
    </div>
  )
}

function KpiStrip({ onNavigate }: { onNavigate?: (tab: string) => void }) {
  const k = useKpiFigures()
  // Zeros under the headline while the figures load would read as real values.
  if (k.loading) return null
  if (!k.has_market && !k.showCash) return null
  return (
    <div className="ov-kpis">
      {k.has_market && (
        <Kpi
          label="Today"
          value={fSigned(k.today)}
          tone={k.today >= 0 ? 'pos' : 'neg'}
          onClick={onNavigate ? () => onNavigate('investments') : undefined}
        />
      )}
      {k.showCash && (
        <Kpi
          label="Cash on hand"
          value={fBase(k.cash)}
          hint={CASH_HINT}
          onClick={onNavigate ? () => onNavigate('accounts') : undefined}
        />
      )}
    </div>
  )
}

function NumsCell({
  label,
  value,
  tone,
  hint,
  onClick,
}: {
  label: string
  value: string
  tone?: 'pos' | 'neg'
  hint?: string
  onClick?: () => void
}) {
  const inner = (
    <>
      <span className="ov-nums-k">{label}</span>
      <span className={moneyClass('ov-nums-v', tone)}>{value}</span>
    </>
  )
  if (onClick) {
    return (
      <button type="button" className="ov-nums-cell" title={hint} onClick={onClick}>
        {inner}
      </button>
    )
  }
  return (
    <div className="ov-nums-cell" title={hint}>
      {inner}
    </div>
  )
}

function NumsGrid({ onNavigate }: { onNavigate?: (tab: string) => void }) {
  const k = useKpiFigures()
  // Zeros under the headline while the figures load would read as real values.
  if (k.loading) return null
  if (!k.has_market && !k.showCash) return null
  return (
    <div className="ov-nums-grid">
      {k.has_market && (
        <NumsCell
          label="Today"
          value={fSigned(k.today)}
          tone={k.today >= 0 ? 'pos' : 'neg'}
          onClick={onNavigate ? () => onNavigate('investments') : undefined}
        />
      )}
      {k.showCash && (
        <NumsCell
          label="Cash on hand"
          value={fBase(k.cash)}
          hint={CASH_HINT}
          onClick={onNavigate ? () => onNavigate('accounts') : undefined}
        />
      )}
    </div>
  )
}

// ── chart body: trend (colored/filled, item 5) or composition (item 4 bars +
// legend), both with index-mode hover + formatted tooltip/axes (item 6). ───

function NetWorthChartBody({ mode, range }: { mode: 'trend' | 'comp'; range: string }) {
  const { stats: networth, history, loading: historyLoading } = useNetWorthSeries()
  const rangeStats = useRangeStats(range)
  if (historyLoading) return <div className="ov-chart-skeleton" aria-hidden />
  if (history.length < 2) {
    return (
      <Text as="p" size="sm" tone="faint">
        Net worth history builds up as your account balances are tracked over time.
      </Text>
    )
  }

  if (mode === 'comp') {
    const { present, hasLiab } = compositionSeries(
      networth?.composition.classes ?? [],
      networth?.composition.has_liabilities ?? false,
    )
    const stackOrder = [...present].reverse()
    const datasets: object[] = stackOrder.map((cls) => ({
      type: 'bar' as const,
      label: ASSET_CLASS_LABEL[cls] || cls,
      data: history.map((p) => p.classes[cls] || 0),
      // resolveColor, always: canvas cannot read a CSS variable, and one unparseable colour
      // inside Chart's shared animator blanks EVERY chart on the page (see charts.tsx).
      backgroundColor: resolveColor(ASSET_CLASS_COLOR[cls] ?? 'var(--accent)'),
      stack: 'assets',
      borderWidth: 0,
      categoryPercentage: 0.8,
      barPercentage: 1.0,
      order: 1,
    }))
    if (hasLiab) {
      datasets.push({
        type: 'bar' as const,
        label: 'Liabilities',
        data: history.map((p) => Math.abs(p.liabilities || 0)),
        // Softened: at full strength the liabilities stack visually outweighs the asset bars
        // it sits beside, reading as the headline of the chart rather than the counterweight.
        backgroundColor: withAlpha(resolveColor('var(--bad)'), 0.55),
        stack: 'liabilities',
        borderWidth: 0,
        categoryPercentage: 0.8,
        barPercentage: 1.0,
        order: 1,
      })
    }
    datasets.push({
      type: 'line' as const,
      label: 'Net worth',
      data: history.map((p) => p.net_worth),
      borderColor: resolveColor('var(--ink)'),
      backgroundColor: 'transparent',
      borderWidth: 2,
      pointRadius: history.map((_, i) => (i === history.length - 1 ? 4 : 0)),
      pointBackgroundColor: resolveColor('var(--ink)'),
      tension: 0.3,
      fill: false,
      stack: 'nw',
      order: 0,
    })
    const last = history[history.length - 1]!
    return (
      <>
        <CompLegend present={present} hasLiab={hasLiab} />
        <OvChart
          sig={'comp|' + history.length + '|' + Math.round(last.net_worth)}
          config={{
            type: 'bar',
            data: { labels: history.map((p) => monthYearLabel(p.date)), datasets },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              interaction: { mode: 'index', intersect: false },
              plugins: {
                // Off — `<CompLegend>` above (the colored-dot legend + "Net worth" line
                // swatch) is the ONE legend for this chart; Chart.js's own built-in legend
                // was rendering a SECOND, duplicate legend below it.
                legend: legend(false),
                tooltip: {
                  callbacks: {
                    label: (x: { dataset: { label?: string }; raw: unknown }) =>
                      `${x.dataset.label}: ${moneyTip(x.raw)}`,
                  },
                },
              },
              scales: axes({ x: { stacked: true }, y: { stacked: true } }),
            },
          }}
          minHeight={120}
        />
      </>
    )
  }

  // trend (default) — the plotted window and its sign both come from the server's own range
  // stats (`from_date`/`to_date`/`delta`), so the line, the delta chip above it and the
  // attribution note can never describe different windows.
  const hasMarket = history.some((p) => p.market_tracked)
  const comparable = hasMarket ? history.filter((p) => p.market_tracked) : history
  const windowed = rangeStats
    ? comparable.filter((p) => p.date >= rangeStats.from_date && p.date <= rangeStats.to_date)
    : comparable
  const slice = windowed.length >= 2 ? windowed : comparable
  const pos = (rangeStats?.delta ?? 0) >= 0
  const color = resolveColor(pos ? 'var(--good)' : 'var(--bad)')
  const long = range === '1Y' || range === 'All'
  return (
    <OvChart
      sig={
        'trend|' +
        range +
        '|' +
        slice.length +
        '|' +
        Math.round(slice[slice.length - 1]?.net_worth ?? 0)
      }
      config={{
        type: 'line',
        data: {
          labels: slice.map((p) => (long ? monthYearLabel(p.date) : fDate(p.date))),
          datasets: [
            {
              label: 'Net worth',
              data: slice.map((p) => p.net_worth),
              borderColor: color,
              backgroundColor: areaFill(color, '3a'),
              borderWidth: 2,
              tension: 0.3,
              pointRadius: slice.map((_, i) => (i === slice.length - 1 ? 3.5 : 0)),
              pointBackgroundColor: color,
              fill: true,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: 'index', intersect: false },
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                title: (items: { dataIndex: number }[]) => {
                  const date = slice[items[0]!.dataIndex]!.date
                  const weekday = parseDate(date)?.toLocaleDateString(undefined, {
                    weekday: 'short',
                  })
                  return `${weekday}, ${fDate(date, 'short', 'always')}`
                },
                label: (x: { raw: unknown }) => moneyTip(x.raw),
              },
            },
          },
          // axes()'s x/y overrides merge at the SCALE level (siblings of
          // `ticks`/`grid`, e.g. `stacked`), not inside `ticks` — so a tick
          // COUNT override isn't expressible here without touching
          // charts.tsx (out of scope); the shared default (maxTicksLimit 8)
          // applies, which is close enough to the prototype's 6.
          scales: axes(),
        },
      }}
      minHeight={120}
    />
  )
}

// ── the three net-worth defIds ──────────────────────────────────────────────

const Hero: ComponentType<WidgetContentProps> = ({ instance, def, onNavigate }) => {
  const chosen = vizOf(instance, def) || 'full'
  const { history, loading } = useNetWorthSeries()
  // A monthly series needs two months before there is a line to draw. Until then the
  // number+chart card shows its numbers alone — not a chart-sized hole with a caption.
  const heroViz = chosen === 'full' && !loading && history.length < 2 ? 'numbers' : chosen
  useFitContentWhen(heroViz === 'numbers')
  const mode: 'trend' | 'comp' = instance.config?.chart === 'Composition' ? 'comp' : 'trend'
  const range = (instance.config?.range as string) || '3M'
  return (
    <WidgetShell title={instance.title} base={def.title} fill={heroViz !== 'numbers'}>
      <div className="ov-hero">
        {heroViz !== 'chart' && <Headline mode={mode} range={range} inline />}
        {heroViz !== 'numbers' && <NetWorthChartBody mode={mode} range={range} />}
        {heroViz !== 'chart' && <KpiStrip onNavigate={onNavigate} />}
      </div>
    </WidgetShell>
  )
}

const NetWorthNumbers: ComponentType<WidgetContentProps> = ({ instance, def, onNavigate }) => (
  <WidgetShell title={instance.title} base={def.title}>
    <div className="ov-hero ov-hero--nums">
      <Headline mode="trend" range="3M" inline />
      <NumsGrid onNavigate={onNavigate} />
    </div>
  </WidgetShell>
)

const NetWorthChart: ComponentType<WidgetContentProps> = ({ instance, def }) => {
  const mode = (vizOf(instance, def) || 'trend') as 'trend' | 'comp'
  const range = (instance.config?.range as string) || '3M'
  return (
    <WidgetShell title={instance.title} base={def.title} fill>
      <div className="ov-hero">
        <NetWorthChartBody mode={mode} range={range} />
      </div>
    </WidgetShell>
  )
}

// ── breakdown widgets: alloc / reach / venues ───────────────────────────────

// Each of the three reads a server-computed Breakdown ({total, rows:[{key,value,pct}]}) and
// only attaches its LABEL, COLOUR and ORDER — presentation the backend has no business owning.
// The values and the denominator are the backend's, which is why the allocation bands now
// reconcile with `net_worth_history`'s own `classes` instead of summing past 100%.

const EMPTY_BREAKDOWN: Breakdown = { total: 0, rows: [] }

interface BreakdownRows {
  rows: BreakdownRow[]
  total: number
  /** What is owed, apart from the shares (a negative figure) — never a negative percentage. */
  owed?: number
}

function useAllocRows(): BreakdownRows {
  const { analytics: portfolio } = useAnalytics()
  const alloc = portfolio?.allocation ?? { ...EMPTY_BREAKDOWN, owed: 0 }
  const byKey = new Map(alloc.rows.map((r) => [r.key, r]))
  const ordered = ASSET_CLASS_ORDER.filter((c) => byKey.get(c)?.value)
  return {
    rows: ordered.map((c) => ({
      key: c,
      label: ASSET_CLASS_LABEL[c] ?? c,
      value: byKey.get(c)?.value ?? 0,
      pct: byKey.get(c)?.pct,
      color: ASSET_CLASS_COLOR[c] ?? 'var(--accent)',
    })),
    total: alloc.total,
    owed: alloc.owed,
  }
}

const REACH_META: Record<string, { label: string; color: string; tip: string }> = {
  liquid: {
    label: 'Liquid now',
    color: 'var(--liquid)',
    tip: 'Cash and freely sellable holdings — reachable this week.',
  },
  semi_liquid: {
    label: 'Delayed',
    color: 'var(--contingent)',
    tip: 'Yours, but not reachable yet — e.g. vested shares your employer has not delivered.',
  },
  illiquid: {
    label: 'Illiquid',
    color: 'var(--locked)',
    tip: 'Property, CPF, fixed deposits before maturity, money owed to you.',
  },
}

function useReachRows(): { rows: BreakdownRow[]; total: number } {
  const { analytics: portfolio } = useAnalytics()
  const reach = portfolio?.reach ?? EMPTY_BREAKDOWN
  return {
    rows: reach.rows
      .filter((r) => r.value > 0)
      .map((r) => ({
        key: r.key,
        label: REACH_META[r.key]?.label ?? r.key,
        value: r.value,
        pct: r.pct,
        color: REACH_META[r.key]?.color ?? 'var(--accent)',
        tip: REACH_META[r.key]?.tip,
      })),
    total: reach.total,
  }
}

function useVenueRows(): { rows: BreakdownRow[]; total: number } {
  const { analytics: portfolio } = useAnalytics()
  const venues = portfolio?.venues ?? EMPTY_BREAKDOWN
  return {
    rows: [...venues.rows]
      .sort((a, b) => b.value - a.value)
      // One color per venue, by rank. The rows' own color is their ACCOUNT's, which nearly
      // every account leaves at the default blue — so every slice of the donut was one color.
      .map((r, i) => ({
        key: r.key,
        label: r.key,
        value: r.value,
        pct: r.pct,
        color: paletteAt(i),
      })),
    total: venues.total,
  }
}

// What each total ADDS UP, in the reader's words. Three cards that sit side by side quote three
// different totals (net worth, gross assets, assets by institution); unlabelled they read as
// three answers to one question.
const BASIS = {
  alloc: 'Each class as a share of your assets. Debts are listed apart.',
  reach: 'Everything you own, before debts, owned assets included.',
  venues: 'By institution, each net of its own debts. Owned assets are not held at one.',
}

function makeBreakdownWidget(
  useRows: () => BreakdownRows,
  basis: string,
): ComponentType<WidgetContentProps> {
  return ({ instance, def, onPatch }) => {
    const { rows, total, owed } = useRows()
    const { loading } = useAnalytics()
    const viz = vizOf(instance, def) || 'list'
    if (loading) {
      return (
        <WidgetShell title={instance.title} base={def.title}>
          <WidgetSkeleton rows={3} />
        </WidgetShell>
      )
    }
    if (!rows.length) {
      return (
        <WidgetShell title={instance.title} base={def.title}>
          <WidgetPlaceholder
            detail={<>Nothing to show yet — add accounts or holdings and this fills in.</>}
          />
        </WidgetShell>
      )
    }
    return (
      <WidgetShell
        title={instance.title}
        base={def.title}
        subtitle={basis}
        fill={viz !== 'list'}
        chart={{
          value: viz,
          options: [
            { value: 'list', label: 'List' },
            { value: 'bar', label: 'Bar' },
            { value: 'pie', label: 'Pie' },
          ],
          onChange: (v) => onPatch({ config: { ...instance.config, viz: v } }),
        }}
      >
        <BreakdownBody rows={rows} total={total} viz={viz} limit={rowCap(instance, def)} />
        {!!owed && (
          <div className="ov-breakdown-owed">
            <span className="ov-breakdown-label">Debts</span>
            <span className="ov-breakdown-value money">
              <Money as="span">{fBase(owed)}</Money>
            </span>
          </div>
        )}
      </WidgetShell>
    )
  }
}

export const netWorthWidgetComponents: Record<string, ComponentType<WidgetContentProps>> = {
  hero: Hero,
  'networth-nums': NetWorthNumbers,
  'networth-chart': NetWorthChart,
  alloc: makeBreakdownWidget(useAllocRows, BASIS.alloc),
  reach: makeBreakdownWidget(useReachRows, BASIS.reach),
  venues: makeBreakdownWidget(useVenueRows, BASIS.venues),
}
