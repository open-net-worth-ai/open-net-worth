// Overview v2 — the shared list/bar/pie renderer for every `shape: 'breakdown'`
// widget (allocation, reachability, venues, spend-by-category, top merchants —
// "one data shape, several ways to show it," per the handoff's promotion note).
// Consumed via WidgetShell's `chart` toggle; the caller owns fetching/deriving
// the rows, this owns only the three representations.
//
// Ported from the handoff's `breakdown.jsx` (`Stacked`/`Donut`/`Legend`/`Bars`):
// the list view is a proportional `.ov-breakdown-bar` above legend rows, the
// pie view is a center-labelled donut beside its own legend, and the bar view
// carries a `$Xk (Y%)` tooltip + compact axis ticks — all three read `total`
// (never the visible/limited slice) so a truncated row list never skews the
// percentages.

import { useCallback, useEffect, useRef, useState } from 'react'
import type { ChartEvent } from 'chart.js'
import { useAmountsHidden, useMaskText } from '../../../utils/amountMask'
import { fBase, fBaseCompact, fPct } from '../../../utils/format'
import { OvChart, amountsHidden, kTick, resolveColor } from './charts'

export interface BreakdownRow {
  key: string
  label: string
  value: number
  color: string
  tip?: string
  /** The server's own share-of-total for this row. Set by every Overview caller
   *  (ADR 2026-07-31-overview-aggregates-are-server-computed) so the figure the user reads is
   *  the one the backend computed, not a second division of the same two numbers. Omitted
   *  only by a caller with no server-side percentage, which falls back to `value / total`. */
  pct?: number
}

/** Share of the total for display. Prefers the server's figure; the local division is the
 *  fallback for callers that don't carry one. */
function shareOf(row: Pick<BreakdownRow, 'value' | 'pct'>, total: number): number {
  return row.pct ?? (total ? (row.value / total) * 100 : 0)
}

/** Prototype's `"$Xk (Y%)"` tooltip line — shared by the bar and pie configs.
 *  Reads the row's own `pct` (via `shareOf`) rather than re-deriving it from
 *  value/total, per ADR 2026-07-31-overview-aggregates-are-server-computed. */
function tooltipLine(
  row: Pick<BreakdownRow, 'label' | 'value' | 'pct'> | undefined,
  total: number,
): string {
  if (!row) return ''
  const pct = shareOf(row, total)
  const formattedVal = amountsHidden() ? '***' : fBaseCompact(row.value)
  return `${row.label ?? ''}: ${formattedVal} (${amountsHidden() ? '***' : fPct(pct)})`
}

/** What a caller can make a row do — a tap/click on a bar, a pie slice or a legend row. */
interface RowSelect {
  onSelect: (row: BreakdownRow) => void
  /** A row this returns false for stays inert (nothing to open). */
  canSelect?: (row: BreakdownRow) => boolean
  /** Accessible name of the action, e.g. "show its transactions". */
  label?: string
}

function BreakdownLegend({
  rows,
  total,
  hiddenCount,
  select,
}: {
  rows: BreakdownRow[]
  total: number
  hiddenCount?: number
  select?: RowSelect
}) {
  const maskText = useMaskText()
  return (
    <div className="ov-breakdown-list">
      {rows.map((r) => {
        const body = (
          <>
            <i className="ov-breakdown-dot" style={{ background: r.color }} />
            <span className="ov-breakdown-label">{r.label}</span>
            {/* Both forms render; the legend's own width picks one (netWorth.css) — a money
                figure is never ellipsized, the label gives way instead. */}
            <span className="ov-breakdown-value money">
              <span className="ov-breakdown-value-full">{fBase(r.value)}</span>
              <span className="ov-breakdown-value-compact">{fBaseCompact(r.value)}</span>
            </span>
            <span className="ov-breakdown-pct money">{fPct(shareOf(r, total))}</span>
          </>
        )
        return select && (select.canSelect?.(r) ?? true) ? (
          <button
            key={r.key}
            type="button"
            className="ov-breakdown-row ov-breakdown-row--go"
            title={r.tip}
            onClick={() => select.onSelect(r)}
            aria-label={`${r.label}, ${maskText(fBase(r.value))}${select.label ? ` — ${select.label}` : ''}`}
          >
            {body}
          </button>
        ) : (
          <div key={r.key} className="ov-breakdown-row" title={r.tip}>
            {body}
          </div>
        )
      })}
      {!!hiddenCount && hiddenCount > 0 && <div className="ov-more">+{hiddenCount} more</div>}
    </div>
  )
}

/** A function with a fixed identity that always calls the latest `fn`. */
function useStableCallback<A extends unknown[]>(fn: (...args: A) => void) {
  const ref = useRef(fn)
  useEffect(() => {
    ref.current = fn
  })
  return useCallback((...args: A) => ref.current(...args), [])
}

/** Chart.js `onClick`/`onHover` pair for a chart whose marks open something: a click on mark
 *  `i` calls `pick(i)`, and the pointer shows it is clickable. Mark order is `rows` order. */
function pickHandlers(pick: (index: number) => void) {
  return {
    onClick: (_e: ChartEvent, elements: Array<{ index: number }>) => {
      const hit = elements[0]
      if (hit) pick(hit.index)
    },
    onHover: (e: ChartEvent, elements: unknown[]) => {
      const target = e.native?.target
      if (target instanceof HTMLElement) target.style.cursor = elements.length ? 'pointer' : ''
    },
  }
}

/** The proportional, colored, tooltipped stacked bar above the list's legend
 * rows — prototype's `Stacked`. Each segment's `title` doubles as its tooltip
 * since this is plain HTML, not a chart. */
function BreakdownBar({ rows, total }: { rows: BreakdownRow[]; total: number }) {
  useAmountsHidden() // its tooltips read the flag at render
  return (
    <div className="ov-breakdown-bar" aria-hidden="true">
      {rows.map((r) => {
        const pct = shareOf(r, total)
        const valStr = amountsHidden() ? '***' : fBase(r.value)
        return (
          <i
            key={r.key}
            style={{ width: `${Math.max(0, pct)}%`, background: r.color }}
            title={`${r.label}\n${valStr} • ${amountsHidden() ? '***' : fPct(pct)}`}
          />
        )
      })}
    </div>
  )
}

/** Center-total doughnut — prototype's `Donut`. Sizes itself to the available
 * space via ResizeObserver (CSS container units can't track height without
 * breaking the list mode's height:auto layout), matching the handoff exactly:
 * measures the nearest `.ov-fit` ancestor's real box and fits within it
 * (`min(width * 0.42, height - 16)`, clamped 120-320px) — so it scales up in
 * a large/fullscreen widget instead of staying pinned to a grid-card size.
 * A fixed constant was tried first and reverted: it was a workaround for
 * `.ov-fit`/`.panel` themselves not yet having a real, stable height
 * (`display:flex` was missing on `.panel`, so a flex child had nothing to
 * grow within) — now that that's fixed at the source, this ancestor
 * measurement is reliable again and correctly fills a fullscreen panel. */
// The chart's own `cutout` below — the inner hole's diameter is this
// fraction of the donut's outer size, and the center-total text has to fit
// INSIDE that hole, not the full donut box.
const CUTOUT_PCT = 0.62

// Bold tabular-nums average glyph width, as a fraction of font-size — used
// to size the center-total text DOWN to the hole it actually has to fit in,
// not just up proportionally with the donut. A fixed size-ratio alone (the
// previous approach) only accounts for the donut getting bigger/smaller; it
// doesn't account for the VALUE STRING getting longer/shorter ("S$0" vs
// "S$422.66K" vs "S$12.4M"), so a long total on a small donut still
// overflowed the hole and visually crowded the ring. Padding/inset around
// the text was tried and reverted — it shrinks the AVAILABLE box instead of
// the text, which still clips/truncates long values rather than genuinely
// fitting them.
const AVG_CHAR_WIDTH_EM = 0.62
const CENTER_FONT_MARGIN = 0.86 // breathing room inside the hole, not flush to its edge

function centerFontSize(size: number, valueText: string): number {
  const holeWidth = size * CUTOUT_PCT * CENTER_FONT_MARGIN
  const fitToText = Math.floor(holeWidth / (valueText.length * AVG_CHAR_WIDTH_EM))
  const fitToDonut = Math.round(size * 0.12) // cosmetic cap so a short value on a big donut doesn't look oversized
  return Math.max(11, Math.min(fitToDonut, fitToText))
}

function BreakdownDonut({
  rows,
  total,
  pick,
}: {
  rows: BreakdownRow[]
  total: number
  pick?: (index: number) => void
}) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState(150)
  useEffect(() => {
    const wrap = wrapRef.current
    const box = wrap?.closest<HTMLElement>('.ov-fit') ?? wrap?.parentElement ?? null
    if (!box) return
    const measure = () => {
      const r = box.getBoundingClientRect()
      setSize(Math.round(Math.max(120, Math.min(r.width * 0.42, r.height - 16, 320))))
    }
    const ro = new ResizeObserver(measure)
    measure()
    ro.observe(box)
    return () => ro.disconnect()
  }, [])
  const valueText = fBaseCompact(total)
  return (
    <div className="ov-breakdown-donut-wrap" ref={wrapRef} style={{ width: size, height: size }}>
      <OvChart
        sig={'donut|' + total + '|' + rows.map((r) => r.key + r.value).join(',')}
        config={{
          type: 'doughnut',
          data: {
            labels: rows.map((r) => r.label),
            datasets: [
              {
                data: rows.map((r) => r.value),
                backgroundColor: rows.map((r) => resolveColor(r.color)),
                borderColor: resolveColor('var(--surface)'),
                borderWidth: 3,
              },
            ],
          },
          options: {
            responsive: true,
            // `aspectRatio: 1` is a hard guarantee, not just a hint — the
            // wrapper is deliberately sized to a square (`width/height:
            // size`, same value both axes) already, but a container
            // measured mid-layout-settle could still hand Chart.js a
            // non-square box for one resize pass; `maintainAspectRatio:
            // false` (the prior config) would let THAT pass render a
            // squished/clipped half-circle instead of a full one.
            maintainAspectRatio: true,
            aspectRatio: 1,
            cutout: `${CUTOUT_PCT * 100}%`,
            ...(pick ? pickHandlers(pick) : {}),
            plugins: {
              legend: { display: false },
              tooltip: {
                callbacks: {
                  label: (x: { dataIndex: number }) => tooltipLine(rows[x.dataIndex], total),
                },
              },
            },
          },
        }}
      />
      <div
        className="ov-breakdown-donut-center"
        style={{ fontSize: centerFontSize(size, valueText) }}
      >
        <span className="ov-breakdown-donut-value money">{valueText}</span>
        <span className="ov-breakdown-donut-label">total</span>
      </div>
    </div>
  )
}

/** Room under a sized bar chart for its value axis. */
const BAR_AXIS_PX = 28

export function BreakdownBody({
  rows,
  total,
  viz,
  limit,
  onSelect,
  canSelect,
  selectLabel,
  barRowHeight,
}: {
  rows: BreakdownRow[]
  total: number
  viz: string
  limit?: number | null
  /** Bar view in a card that fits its content: px per bar. The chart is sized to its rows and
   *  every label is drawn, instead of filling a box that is itself sized by the content. */
  barRowHeight?: number
  /** Makes a bar, a pie slice and a legend row open something. Absent → display only. */
  onSelect?: (row: BreakdownRow) => void
  canSelect?: (row: BreakdownRow) => boolean
  selectLabel?: string
}) {
  const visible = limit ? rows.slice(0, limit) : rows
  const hiddenCount = rows.length - visible.length
  const select: RowSelect | undefined = onSelect
    ? { onSelect, canSelect, label: selectLabel }
    : undefined
  // The chart is built once per `sig`, so its click handler must not close over this render's
  // rows and callback — it calls through a stable wrapper that always sees the latest.
  const pickRow = useStableCallback((index: number) => {
    const row = visible[index]
    if (row && select && (select.canSelect?.(row) ?? true)) select.onSelect(row)
  })
  const pick = select ? pickRow : undefined

  if (viz === 'bar') {
    return (
      <OvChart
        sig={visible.map((r) => r.key + r.value).join(',') + (select ? '|go' : '')}
        minHeight={barRowHeight ? visible.length * barRowHeight + BAR_AXIS_PX : undefined}
        config={{
          type: 'bar',
          data: {
            labels: visible.map((r) => r.label),
            datasets: [
              {
                data: visible.map((r) => r.value),
                backgroundColor: visible.map((r) => resolveColor(r.color)),
                borderRadius: 4,
              },
            ],
          },
          options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            // A clickable bar answers a tap anywhere in its row, not only on the bar itself.
            ...(pick ? { interaction: { mode: 'nearest', axis: 'y', intersect: false } } : {}),
            ...(pick ? pickHandlers(pick) : {}),
            plugins: {
              legend: { display: false },
              tooltip: {
                callbacks: {
                  label: (x: { dataIndex: number }) => tooltipLine(visible[x.dataIndex], total),
                },
              },
            },
            scales: {
              x: { ticks: { callback: kTick }, grid: { display: false } },
              y: {
                grid: { display: false },
                ...(barRowHeight ? { ticks: { autoSkip: false } } : {}),
              },
            },
          },
        }}
      />
    )
  }

  if (viz === 'pie') {
    return (
      <div className="ov-breakdown-pie">
        <BreakdownDonut rows={visible} total={total} pick={pick} />
        <BreakdownLegend rows={visible} total={total} hiddenCount={hiddenCount} select={select} />
      </div>
    )
  }

  // list (default)
  return (
    <div className="ov-breakdown-stack">
      <BreakdownBar rows={visible} total={total} />
      <BreakdownLegend rows={visible} total={total} hiddenCount={hiddenCount} />
    </div>
  )
}
