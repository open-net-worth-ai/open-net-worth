// Overview v2 — Reports widget group (YNAB Reflect / Monarch-style): spend by
// category, spending trends, income vs expense, income & expense table,
// savings rate, top merchants, cash-flow. Ported from the handoff's
// `overview_v2b/reports.jsx`, wired to real endpoints instead of the demo's
// fixed six months of fabricated data.
//
// Data source (no fabricated numbers, and no client-side money math — CLAUDE.md):
//  ONE read, GET /api/overview/reports (backend/services/overview/reports.py). It returns the
//  trailing 6 months of per-category spend/income WITH their totals, the top-N + "Other"
//  stacked series, the category table's current/average/total columns, top merchants for the
//  month, the savings rate and the cash-flow columns — every figure already summed, ranked,
//  averaged and divided server-side (ADR 2026-07-31-overview-aggregates-are-server-computed).
//
//  This replaced 12 parallel per-month fetches (six /api/planning/month + six
//  /api/planning/income) plus a full client-side reporting layer folded on top of them, and a
//  separate raw-transaction pull for merchants. Those folds were a second implementation of
//  the app's money math — the same failure mode that shipped a S$120,000 net-worth headline.
//
// Two documented simplifications vs. the handoff (see the ADR's "Negative
// consequences" list):
//  - `rep-sankey` is now a real Sankey in its own module (cashFlowCard.tsx, reading
//    GET /api/cashflow/flow) — no longer part of this group's rollup read.
//  - `rep-savings`: charts.tsx does not export the handoff's semicircle-gauge
//    helper. Ported directly here (self-contained — a local Chart.js plugin
//    passed through OvChart's own `config.plugins`, never touching charts.tsx)
//    rather than a plain progress bar, since the gauge is only a canvas-arc
//    plugin + an absolutely-positioned label, no new dependency.
//  - `rep-table`: a flat 2-level hierarchy (Income / Spending → categories),
//    not the handoff's 3-level group→subgroup→category tree — Planning's
//    real group taxonomy doesn't cleanly map onto the demo's hand-picked
//    Bills/Living/Lifestyle subgroups, and a fabricated subgroup layer would
//    misrepresent the user's actual category structure.
//  - `rep-incexp` ("By category" mode) and `rep-table` BOTH omit the handoff's
//    synthetic "Saving & investing" transfer row (`invest`/`investTot`) between
//    Spending and Net. `MonthSummary` is built entirely from Planning's own
//    per-category activity (`GET /api/planning/month` + `/income`), which
//    already folds any CATEGORIZED investment/transfer leg into its own
//    category's row (the envelope-drain rule — CLAUDE.md's Planning section) —
//    a second, independently-derived "invest" bucket would double-count that
//    same cash movement, and an UNCATEGORIZED leg is invisible to Planning by
//    design (RTA-neutral). No reliable, non-duplicating signal exists for this
//    bucket, so both widgets omit it rather than approximate it. See
//    PARITY_AND_GAPS.md's Reports section for the full reasoning.
//
// Chart.js registration: `charts.tsx` intentionally registers no controllers
// (tree-shakeable `chart.js`, not `chart.js/auto`), and the app's only other
// registration site (the pre-redesign `tabs/Overview.tsx`) is no longer
// mounted anywhere. Every OvChart consumer needs bar/line/doughnut, so this
// module (guaranteed to load — registry.ts always imports it) registers them
// once, mirroring the old Overview.tsx's own list.

import { useState } from 'react'
import type { ComponentType } from 'react'
import {
  Chart,
  ArcElement,
  BarElement,
  BarController,
  DoughnutController,
  LineElement,
  LineController,
  PointElement,
  CategoryScale,
  LinearScale,
  Tooltip,
  Legend,
  Filler,
} from 'chart.js'
import { cardCaveat } from '../../../../utils/cardCaveat'
import { fBase, fBaseCompact, fMonth } from '../../../../utils/format'
import type { ReportsRollup, StackedSeries } from '../../../../types/aggregates'
import { Text } from '../../../ui'
import { useReportsRollup } from '../../../../hooks/serverState'
import type { WidgetContentProps } from '../types'
import { WidgetShell } from '../WidgetShell'
import { rowCap } from '../widgetDisplayShared'
import { WidgetPlaceholder, WidgetSkeleton } from '../WidgetPlaceholder'
import { RankedList } from '../RankedList'
import { CashFlowCard } from './cashFlowCard'
import { BreakdownBody, type BreakdownRow } from '../Breakdown'
import {
  OvChart,
  GRID,
  TICK,
  amountsHidden,
  kTick,
  moneyTip,
  resolveColor,
  withAlpha,
} from '../charts'
import { SeriesLegend, type LegendGroup } from './seriesLegend'
import { fitMode } from '../grid'
import { vizOf } from '../vizModel'
import { ArrowRight } from '../../../../ui/icons'

Chart.register(
  ArcElement,
  DoughnutController,
  BarElement,
  BarController,
  LineElement,
  LineController,
  PointElement,
  CategoryScale,
  LinearScale,
  Tooltip,
  Legend,
  Filler,
)

// ── shared month-aggregate data layer ───────────────────────────────────────
//
// ONE server read (`GET /api/overview/reports`) backs every widget in this group. It carries
// the per-month category rows AND their totals, the top-N + "All other" stacked series, the
// category table's current/average/total columns, top merchants, the savings rate and the
// cash-flow columns — all summed, ranked, averaged and divided by backend/services/overview
// (ADR 2026-07-31-overview-aggregates-are-server-computed).
//
// This replaces 12 parallel per-month fetches plus a client-side reporting layer on top of
// them: six `/api/planning/month` + six `/api/planning/income` calls, each folded in the
// browser, then re-folded per widget.

// Small rotation for a category/payee with no stored color (`--lucius` is
// reserved for Lucius/AI identity, per index.css — never used for chart data).
const PALETTE = ['var(--accent)', 'var(--good)', 'var(--warn)', 'var(--recv)', '#2ba39a', '#9a7fd0']
const paletteColor = (i: number): string =>
  PALETTE[((i % PALETTE.length) + PALETTE.length) % PALETTE.length]!

function monthShortLabel(ym: string): string {
  const [y, m] = ym.split('-').map(Number) as [number, number]
  return new Date(y, m - 1, 1).toLocaleDateString(undefined, { month: 'short' })
}

/** Attach a fallback colour to a server series/row that has none. Presentation only. */
function withColor<T extends { color: string }>(rows: T[]): T[] {
  return rows.map((r, i) => (r.color ? r : { ...r, color: paletteColor(i) }))
}

const REPORT_MONTHS = 6

/** The month the spending widgets show. A new month has no spending for its first days, and
 *  "No spending recorded this month yet" over a full September read as a broken import. */
function spendingMonth(data: ReportsRollup | null) {
  const point = data?.points.find((p) => p.ym === data.spending_month) ?? data?.current
  const fellBack = !!data && data.spending_month !== data.current_month
  return {
    point,
    ym: point?.ym,
    fellBack,
    note: fellBack
      ? `${fMonth(data.spending_month, 'long')}. Nothing spent in ${fMonth(data.current_month, 'long')} yet.`
      : null,
  }
}

/** The one report read, with this group's error/loading conventions applied. */
function useReports(merchantLimit = 10) {
  const query = useReportsRollup(REPORT_MONTHS, merchantLimit)
  return {
    data: query.data ?? null,
    loading: query.initialLoading,
    error: query.error && query.data == null ? "Couldn't load the monthly report." : '',
  }
}

function MonthNote({ children }: { children: string }) {
  return (
    <Text as="p" size="xs" tone="muted" className="ov-rep-note">
      {children}
    </Text>
  )
}

// ── 1. spending by category — the shared Breakdown renderer (list/bar/pie),
// same shape as the Net-worth group's alloc/reach/venues widgets ───────────

const SpendCategories: ComponentType<WidgetContentProps> = ({ instance, def, onPatch }) => {
  const { data, loading, error } = useReports()
  const viz = vizOf(instance, def) || 'list'
  const shown = spendingMonth(data)
  // Server-ranked, server-summed; the widget attaches a fallback colour and renders.
  const rows: BreakdownRow[] = withColor(shown.point?.expense_rows ?? []).map((r) => ({
    key: r.key,
    label: r.label,
    value: r.amount,
    color: r.color,
  }))

  if (loading) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <div className="ov-chart-skeleton" aria-hidden />
      </WidgetShell>
    )
  }
  if (error || !rows.length) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetPlaceholder detail={error || 'No spending recorded this month yet.'} />
      </WidgetShell>
    )
  }
  return (
    <WidgetShell
      title={instance.title}
      base={def.title}
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
      {shown.note && <MonthNote>{shown.note}</MonthNote>}
      <BreakdownBody
        rows={rows}
        total={shown.point?.expense_total ?? 0}
        viz={viz}
        limit={rowCap(instance, def)}
      />
    </WidgetShell>
  )
}

// ── 2. spending trends — stacked columns over the last 6 months ────────────

type TipItem = { dataset: { label?: string; type?: string; side?: string }; raw: unknown }

/** A dashed horizontal line at `avg` with its label ("Avg S$14.9K") on a small backing pill at
 *  the right edge — drawn by the chart so it tracks the axis. The figure is the server's, taken
 *  over complete months only. */
function avgLabelPlugin(avg: number) {
  return {
    id: 'ovAvgLabel',
    afterDatasetsDraw(chart: Chart) {
      const { chartArea: area, ctx } = chart
      const y = chart.scales.y
      if (!y || !area) return
      const py = y.getPixelForValue(avg)
      if (py < area.top || py > area.bottom) return
      const text = `Avg ${amountsHidden() ? '***' : fBaseCompact(avg)}`
      ctx.save()
      ctx.font = `600 11px ${Chart.defaults.font.family}`
      ctx.textAlign = 'right'
      ctx.textBaseline = 'middle'
      const padX = 5
      const w = ctx.measureText(text).width + padX * 2
      const h = 16
      const above = py - h - 2 >= area.top
      const top = above ? py - h - 2 : py + 2
      ctx.fillStyle = withAlpha(resolveColor('var(--surface)'), 0.92)
      ctx.beginPath()
      ctx.roundRect(area.right - w, top, w, h, 4)
      ctx.fill()
      ctx.fillStyle = resolveColor('var(--ink)')
      ctx.fillText(text, area.right - padX, top + h / 2 + 0.5)
      ctx.restore()
    },
  }
}

const SpendingTrends: ComponentType<WidgetContentProps> = ({ instance, def }) => {
  const { data, loading, error } = useReports()
  const points = data?.points ?? []
  if (loading) {
    return (
      <WidgetShell title={instance.title} base={def.title} fill bodyClass="ov-rep-body">
        <div className="ov-chart-skeleton" aria-hidden />
      </WidgetShell>
    )
  }
  if (error || !points.length) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetPlaceholder detail={error || 'Not enough transaction history yet.'} />
      </WidgetShell>
    )
  }
  // Top-N + "All other" folding and the monthly average are the server's — see the header note.
  const series = withColor(data?.expense_series ?? [])
  const avgSpend = data?.expense_totals?.average ?? 0
  const datasets: Array<Record<string, unknown>> = series.map((s) => ({
    label: s.label,
    data: s.data,
    backgroundColor: resolveColor(s.color),
    stack: 's',
    borderWidth: 0,
  }))
  if (avgSpend > 0) {
    datasets.push({
      type: 'line',
      label: 'Monthly average',
      data: points.map(() => avgSpend),
      borderColor: withAlpha(resolveColor('var(--ink)'), 0.7),
      borderDash: [6, 4],
      borderWidth: 1.5,
      pointRadius: 0,
      pointHoverRadius: 0,
      fill: false,
      order: -1,
    })
  }
  const cfg = {
    type: 'bar',
    data: { labels: points.map((p) => monthShortLabel(p.ym)), datasets },
    plugins: avgSpend > 0 ? [avgLabelPlugin(avgSpend)] : [],
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index' as const, intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          itemSort: (a: { datasetIndex: number }, b: { datasetIndex: number }) =>
            a.datasetIndex - b.datasetIndex,
          callbacks: {
            label: (x: TipItem) =>
              x.dataset.type === 'line'
                ? `Monthly average: ${moneyTip(x.raw)} (complete months)`
                : `${x.dataset.label}: ${moneyTip(x.raw)}`,
          },
        },
      },
      scales: {
        x: { stacked: true, ticks: { color: TICK() }, grid: { display: false } },
        y: { stacked: true, ticks: { color: TICK(), callback: kTick }, grid: { color: GRID() } },
      },
    },
  }
  return (
    <WidgetShell
      title={instance.title}
      base={def.title}
      fill
      bodyClass="ov-rep-body"
      meta={
        <>
          avg <span className="money">{fBaseCompact(avgSpend)}</span>/mo
        </>
      }
    >
      <SeriesLegend
        groups={[{ items: series.map((s) => ({ key: s.key, label: s.label, color: s.color })) }]}
      />
      <OvChart
        config={cfg}
        sig={points.map((p) => `${p.ym}:${p.expense_total}`).join(',') + `|${avgSpend}`}
        minHeight={120}
      />
    </WidgetShell>
  )
}

// ── 3. income vs expense — paired bars over the last 6 months ──────────────

const OTHER_KEY = '__other__' // the server's remainder bucket (reports_rollup.OTHER_KEY)

type Side = 'in' | 'out'

// Each side reads as a colour family — income green, spending red/amber — so the two stacks
// per month can never be mistaken for one another; categories within a side differ by tint.
const SIDE_BASES: Record<Side, string[]> = {
  in: ['var(--good)'],
  out: ['var(--bad)', 'var(--warn)'],
}

/** The i-th tint of a side's family: `css` for the legend swatch (follows a theme switch) and
 *  `fill` for the canvas (which needs the resolved colour). */
function sideShade(side: Side, i: number): { css: string; fill: string } {
  const bases = SIDE_BASES[side]
  const base = bases[i % bases.length]!
  const alpha = Math.max(0.28, 1 - Math.floor(i / bases.length) * 0.14)
  return {
    css: `color-mix(in srgb, ${base} ${Math.round(alpha * 100)}%, transparent)`,
    fill: withAlpha(resolveColor(base), alpha),
  }
}

/** A side's server series, recoloured into its family. The remainder bucket keeps a neutral
 *  grey and says which side it belongs to ("All other income" / "All other expenses"). */
function sideSeries(series: StackedSeries[], side: Side) {
  return series.map((s, i) => {
    const other = s.key === OTHER_KEY
    const shade = other
      ? {
          css: 'color-mix(in srgb, var(--muted) 60%, transparent)',
          fill: withAlpha(resolveColor('var(--muted)'), 0.6),
        }
      : sideShade(side, i)
    const label = other ? (side === 'in' ? 'All other income' : 'All other expenses') : s.label
    return { key: s.key, label, data: s.data, ...shade }
  })
}

const IncomeExpense: ComponentType<WidgetContentProps> = ({ instance, def }) => {
  const { data, loading, error } = useReports()
  const points = data?.points ?? []
  if (loading) {
    return (
      <WidgetShell title={instance.title} base={def.title} fill bodyClass="ov-rep-body">
        <div className="ov-chart-skeleton" aria-hidden />
      </WidgetShell>
    )
  }
  if (error || !points.length) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetPlaceholder detail={error || 'Not enough transaction history yet.'} />
      </WidgetShell>
    )
  }
  const mode = instance.config?.detail === 'By category' ? 'cats' : 'net'
  const netTotal = data?.net_total ?? 0
  type Ds = {
    label: string
    side?: 'Income' | 'Expense'
    data: number[]
    backgroundColor: string
    stack: string
    borderWidth: number
  }
  let datasets: Ds[]
  let groups: LegendGroup[]
  if (mode === 'net') {
    const income = sideShade('in', 0)
    const expense = sideShade('out', 0)
    datasets = [
      {
        label: 'Income',
        data: points.map((p) => p.income_total),
        backgroundColor: income.fill,
        stack: 'in',
        borderWidth: 0,
      },
      {
        label: 'Expenses',
        data: points.map((p) => p.expense_total),
        backgroundColor: expense.fill,
        stack: 'out',
        borderWidth: 0,
      },
    ]
    groups = [
      {
        items: [
          { key: 'in', label: 'Income', color: income.css },
          { key: 'out', label: 'Expenses', color: expense.css },
        ],
      },
    ]
  } else {
    const inc = sideSeries(data?.income_series ?? [], 'in')
    const exp = sideSeries(data?.expense_series ?? [], 'out')
    const toDs = (rows: typeof inc, side: Ds['side'], stack: string): Ds[] =>
      rows.map((r) => ({
        label: r.label,
        side,
        data: r.data,
        backgroundColor: r.fill,
        stack,
        borderWidth: 0,
      }))
    datasets = [...toDs(inc, 'Income', 'in'), ...toDs(exp, 'Expense', 'out')]
    const items = (rows: typeof inc) =>
      rows.map((r) => ({ key: r.key, label: r.label, color: r.css }))
    groups = [
      { title: 'Income', side: 'in', items: items(inc) },
      { title: 'Expenses', side: 'out', items: items(exp) },
    ]
  }
  const cfg = {
    type: 'bar',
    data: { labels: points.map((p) => monthShortLabel(p.ym)), datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index' as const, intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (x: TipItem) =>
              `${x.dataset.side ? `${x.dataset.side} · ` : ''}${x.dataset.label}: ${moneyTip(x.raw)}`,
          },
        },
      },
      scales: {
        x: { stacked: true, ticks: { color: TICK() }, grid: { display: false } },
        y: { stacked: true, ticks: { color: TICK(), callback: kTick }, grid: { color: GRID() } },
      },
    },
  }
  return (
    <WidgetShell
      title={instance.title}
      base={def.title}
      fill
      bodyClass="ov-rep-body"
      meta={
        <>
          net{' '}
          <b className={'money ' + (netTotal >= 0 ? 'pos' : 'neg')}>
            {netTotal >= 0 ? '+' : ''}
            {fBase(netTotal)}
          </b>
        </>
      }
    >
      <SeriesLegend groups={groups} />
      <OvChart
        config={cfg}
        sig={
          mode + '|' + points.map((p) => `${p.ym}:${p.income_total}:${p.expense_total}`).join(',')
        }
        minHeight={120}
      />
    </WidgetShell>
  )
}

// ── 4. income & expense table — a simplified 2-level hierarchy ─────────────

const IncExpTable: ComponentType<WidgetContentProps> = ({ instance, def }) => {
  const { data, loading, error } = useReports()
  const points = data?.points ?? []
  const [open, setOpen] = useState<{ income: boolean; expense: boolean }>({
    income: true,
    expense: true,
  })
  const toggle = (k: 'income' | 'expense') => setOpen((o) => ({ ...o, [k]: !o[k] }))

  if (loading) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <div className="ov-chart-skeleton" aria-hidden />
      </WidgetShell>
    )
  }
  if (error || !data || !points.length) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetPlaceholder detail={error || 'Not enough transaction history yet.'} />
      </WidgetShell>
    )
  }
  const n = points.length
  const latest = points[n - 1]!
  // Per-category current/average/total columns AND the three summary rows are all server-
  // computed — the widget only formats and indents them.
  const incRows = withColor(data?.income_table ?? [])
  const expRows = withColor(data?.expense_table ?? [])
  const incTot = data.income_totals
  const spendTot = data.expense_totals
  const net = data.net_totals
  const fmt = (v: number, kind: 'in' | 'out' | 'net') =>
    kind === 'net'
      ? (v >= 0 ? '+' : '') + fBase(v)
      : (kind === 'out' ? '−' : '') + fBase(Math.abs(v))
  const cls = (v: number, kind: 'in' | 'out' | 'net') =>
    kind === 'net' ? (v >= 0 ? ' pos' : ' neg') : kind === 'out' ? ' neg' : ''
  const numCells = (
    r: { current: number; average: number; total: number },
    kind: 'in' | 'out' | 'net',
  ) => (
    <>
      <span className={'ov-iet-n ov-iet-cur num' + cls(r.current, kind)}>
        {fmt(r.current, kind)}
      </span>
      <span className={'ov-iet-n ov-iet-avg num' + cls(r.average, kind)}>
        {fmt(r.average, kind)}
      </span>
      <span className={'ov-iet-n ov-iet-tot num' + cls(r.total, kind)}>{fmt(r.total, kind)}</span>
    </>
  )
  const chev = (k: 'income' | 'expense') => (
    <span className={'ov-iet-chev' + (open[k] ? ' open' : '')} />
  )
  return (
    <WidgetShell title={instance.title} base={def.title}>
      <div className="ov-iet">
        <div className="ov-iet-head">
          <span>Category</span>
          <span className="num ov-iet-cur">{monthShortLabel(latest.ym)}</span>
          <span className="num ov-iet-avg">Avg / mo</span>
          <span className="num ov-iet-tot">{n}-mo total</span>
        </div>
        <div className="ov-iet-row grp" onClick={() => toggle('income')}>
          <span className="ov-iet-lab">{chev('income')}Income</span>
          {numCells(incTot, 'in')}
        </div>
        {open.income &&
          incRows.map((r) => (
            <div className="ov-iet-row ind1" key={r.key}>
              <span className="ov-iet-lab">
                <i style={{ background: resolveColor(r.color) }} />
                {r.label}
              </span>
              {numCells(r, 'in')}
            </div>
          ))}
        <div className="ov-iet-row grp" onClick={() => toggle('expense')}>
          <span className="ov-iet-lab">{chev('expense')}Spending</span>
          {numCells(spendTot, 'out')}
        </div>
        {open.expense &&
          expRows.map((r) => (
            <div className="ov-iet-row ind1" key={r.key}>
              <span className="ov-iet-lab">
                <i style={{ background: resolveColor(r.color) }} />
                {r.label}
              </span>
              {numCells(r, 'out')}
            </div>
          ))}
        <div className="ov-iet-row net">
          <span className="ov-iet-lab">Net income</span>
          {numCells(net, 'net')}
        </div>
      </div>
    </WidgetShell>
  )
}

// ── 5. savings rate — semicircle gauge + figure ─────────────────────────────
// Ported directly from the handoff's `OC.gauge()` (a doughnut chart with a
// hand-drawn arc plugin) since charts.tsx doesn't export it — self-contained
// via OvChart's own `config.plugins`, no change to the shared wrapper.

function gaugeConfig(value: number, total: number, color: string) {
  return {
    type: 'doughnut',
    data: {
      labels: ['Value', 'Rest'],
      datasets: [
        {
          data: [value, Math.max(0, total - value)],
          backgroundColor: ['transparent', 'transparent'],
          borderWidth: 0,
        },
      ],
    },
    plugins: [
      {
        id: 'ovGaugeArcs',
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        afterDatasetsDraw(chart: any) {
          const el = chart.getDatasetMeta(0).data[0]
          if (!el) return
          const { x, y, innerRadius, outerRadius } = el.getProps(
            ['x', 'y', 'innerRadius', 'outerRadius'],
            true,
          )
          const ctx = chart.ctx
          const mid = (innerRadius + outerRadius) / 2
          const w = outerRadius - innerRadius
          const frac = total > 0 ? Math.max(0, Math.min(1, value / total)) : 0
          const stroke = (from: number, to: number, col: string) => {
            ctx.beginPath()
            ctx.arc(x, y, mid, from, to)
            ctx.lineWidth = w
            ctx.lineCap = 'round'
            ctx.strokeStyle = col
            ctx.stroke()
          }
          ctx.save()
          // The empty track is the theme's own line colour, read at draw time — a fixed
          // white-at-7% only showed on a dark card and vanished in light mode.
          stroke(Math.PI, 2 * Math.PI, resolveColor('var(--line)'))
          if (frac > 0) stroke(Math.PI, Math.PI * (1 + frac), resolveColor(color))
          ctx.restore()
        },
      },
    ],
    options: {
      rotation: -90,
      circumference: 180,
      cutout: '82%',
      responsive: true,
      maintainAspectRatio: true,
      aspectRatio: 1.9,
      layout: { padding: { bottom: 12 } },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
    },
  }
}

const SavingsRate: ComponentType<WidgetContentProps> = ({ instance, def }) => {
  // Saved amount and rate come from the server's own savings figure — the same month totals
  // the rest of this group renders, so the gauge can't disagree with the table beside it.
  const { data, loading, error } = useReports()
  const savings = data?.savings
  if (loading) {
    return (
      <WidgetShell title={instance.title} base={def.title} bodyAlign="center">
        <div className="ov-chart-skeleton" aria-hidden />
      </WidgetShell>
    )
  }
  if (error || !savings?.known) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetPlaceholder detail={error || 'No income recorded in the last few months.'} />
      </WidgetShell>
    )
  }
  const rate = Math.round(savings.rate_pct)
  const caveat = cardCaveat(savings.cards_missing_spending)
  const trailing = savings.trailing
  return (
    <WidgetShell title={instance.title} base={def.title} bodyAlign="center">
      <div className="ov-savings">
        <div className="ov-savings-gauge-wrap">
          <OvChart
            config={gaugeConfig(savings.saved, savings.income, 'var(--good)')}
            sig={`${data?.current?.ym}:${savings.saved}:${savings.income}`}
            minHeight={80}
          />
          <div className="ov-savings-pct money">{rate}%</div>
        </div>
        <Text as="p" size="xs" tone="faint" className="ov-savings-sub">
          <span className="money">{fBase(savings.saved)}</span> saved of{' '}
          <span className="money">{fBase(savings.income)}</span> earned
          {savings.ym !== data?.current?.ym
            ? ` in ${fMonth(`${savings.ym}-01`, 'long')}`
            : ' so far this month'}
        </Text>
        {trailing && (
          <Text as="p" size="xs" tone="faint" className="ov-savings-sub">
            {trailing.months}-month average ({fMonth(`${trailing.from}-01`, 'short')} to{' '}
            {fMonth(`${trailing.to}-01`, 'short')}):{' '}
            <span className="money">{fBaseCompact(trailing.monthly)}</span> a month
            {trailing.rate_pct != null ? `, ${Math.round(trailing.rate_pct)}%` : ''}
          </Text>
        )}
        {caveat && (
          <Text as="p" size="xs" tone="faint" className="ov-savings-sub">
            {caveat}
          </Text>
        )}
      </div>
    </WidgetShell>
  )
}

// ── 6. top merchants — ranked list (default), bar or pie ────────────────────

/** Height of one merchant's bar when the card fits its content. */
const MERCHANT_BAR_PX = 30

const TopMerchants: ComponentType<WidgetContentProps> = ({
  instance,
  def,
  onPatch,
  onNavigate,
  onOpenRegister,
}) => {
  const cap = rowCap(instance, def) ?? 25
  // Grouped by payee and ranked server-side (no endpoint does per-month payee spend — see the
  // header note), so the browser no longer pulls the month's raw expense legs to fold them.
  const { data, loading, error } = useReports(cap)
  const rows = (data?.merchants ?? []).slice(0, cap)
  const shown = spendingMonth(data)
  const month = shown.ym
  const when = shown.fellBack && month ? `in ${fMonth(month, 'long')}` : 'this month'
  const viz = vizOf(instance, def) || 'list'
  // A card that fits its content cannot also fill it: the chart gets a height of its own.
  const fit = fitMode(def.defId, instance.heightMode)
  if (loading) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetSkeleton rows={5} />
      </WidgetShell>
    )
  }
  if (error || !rows.length) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetPlaceholder detail={error || 'No spending recorded this month yet.'} />
      </WidgetShell>
    )
  }
  // Ranked by amount; a row opens that merchant's transactions for the month, whichever way it
  // is shown. A description-grouped row (no payee) has nothing to filter the register by, so it
  // stays inert.
  const ranked = rows.map((m, i) => ({
    key: m.key,
    label: m.label,
    value: m.amount,
    pct: m.pct,
    color: paletteColor(i),
  }))
  const canSelect = (r: { key: string }) => !r.key.startsWith('d:')
  const open = onOpenRegister
    ? (r: { label: string }) => onOpenRegister({ payee: r.label, month })
    : undefined
  return (
    <WidgetShell
      title={instance.title}
      base={def.title}
      meta={shown.fellBack && month ? fMonth(month, 'long') : 'this month'}
      fill={viz !== 'list' && !fit}
      chart={{
        value: viz,
        options: [
          { value: 'list', label: 'List' },
          { value: 'bar', label: 'Bar' },
          { value: 'pie', label: 'Pie' },
        ],
        onChange: (v) => onPatch({ config: { ...instance.config, viz: v } }),
      }}
      footer={
        onNavigate && (
          <button
            type="button"
            className="ov-rep-link"
            onClick={() => onNavigate(def.source?.tab || 'accounts')}
          >
            All transactions <ArrowRight size={12} aria-hidden="true" />
          </button>
        )
      }
    >
      {shown.note && <MonthNote>{shown.note}</MonthNote>}
      {viz === 'list' ? (
        <RankedList
          rows={ranked}
          selectLabel={`show its transactions ${when}`}
          canSelect={canSelect}
          onSelect={open}
        />
      ) : (
        // The shares are the server's (`pct` of the whole month's spend), so a cut list never
        // inflates them; the donut's centre is the month's total, as in Spending by category.
        <BreakdownBody
          rows={ranked}
          total={shown.point?.expense_total ?? 0}
          viz={viz}
          limit={cap}
          barRowHeight={fit ? MERCHANT_BAR_PX : undefined}
          canSelect={canSelect}
          onSelect={open}
          selectLabel={`show its transactions ${when}`}
        />
      )}
    </WidgetShell>
  )
}

export const reportsWidgetComponents: Record<string, ComponentType<WidgetContentProps>> = {
  'rep-spendcat': SpendCategories,
  'rep-trends': SpendingTrends,
  'rep-incexp': IncomeExpense,
  'rep-table': IncExpTable,
  'rep-savings': SavingsRate,
  'rep-merchants': TopMerchants,
  'rep-sankey': CashFlowCard,
}
