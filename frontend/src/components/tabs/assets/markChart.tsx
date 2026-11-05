// The asset page's value chart — the stepped line through every point in `marks` (valuations
// and the derived purchase point), held forward to today at the asset's current value, with the
// purchase price as a dashed line. The domain math is pure and unit-tested (markChartModel.ts);
// the component only draws it with Chart.js.
import { useEffect, useRef } from 'react'
import {
  Chart,
  Filler,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  Tooltip,
  type Scale,
} from 'chart.js'
import type { OwnedAsset } from '../../../types/assets'
import { useTheme } from '../../../theme'
import { cssToken } from '../../../utils/chartTheme'
import { Text } from '../../ui'
import { purchaseCopy } from './assetCategoryRules'
import { amountsHidden, subscribeAmountsHidden } from '../../../utils/amountMask'
import { fmt, fmtK, monthYear } from './assetsData'
import {
  COLOR_TOKEN,
  markChartModel,
  valueColorKey,
  withPinnedTick,
  yearTicks,
  type MarkChartSeriesPoint,
} from './markChartModel'

Chart.register(LineController, LineElement, PointElement, LinearScale, Tooltip, Filler)

// A value's dot, and how far past its point it reaches when hovered (radius + border).
const POINT = { radius: 4, hoverRadius: 6, border: 2 }
const POINT_REACH = POINT.hoverRadius + POINT.border

function rgbaFromToken(tokenName: string, alpha: number): string {
  const hex = cssToken(tokenName).replace('#', '')
  if (hex.length < 6) return `rgba(136,136,136,${alpha})`
  const r = parseInt(hex.slice(0, 2), 16)
  const g = parseInt(hex.slice(2, 4), 16)
  const b = parseInt(hex.slice(4, 6), 16)
  return `rgba(${r},${g},${b},${alpha})`
}

export interface MarkChartProps {
  a: OwnedAsset
}

/** Stepped value-over-time chart: colored above/below purchase price, filled, held forward to
 *  today. Chart chrome needs CONCRETE colors (canvas can't resolve `var(--token)`), so it
 *  rebuilds whenever the resolved theme flips — see TrajectoryChart.tsx for the same pattern. */
export function MarkChart({ a }: MarkChartProps) {
  // Every figure the chart draws. Correcting an older value, or a Purchase edit, changes neither
  // the current value nor the number of points — the chart must still redraw.
  const drawn = [a.value, a.cost, ...a.marks.flatMap((m) => [m.id, m.date, m.value])].join('|')
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const instRef = useRef<Chart | null>(null)
  const { resolved: theme } = useTheme()
  const themeAtBuild = useRef(theme)

  useEffect(() => {
    if (!canvasRef.current) return

    if (instRef.current && themeAtBuild.current !== theme) {
      instRef.current.destroy()
      instRef.current = null
    }
    themeAtBuild.current = theme

    const model = markChartModel(a)
    const { series, marksAsc, x0, xMax, yMin, yMax, costLine, single } = model
    const colorFor = (y: number) => cssToken(COLOR_TOKEN[valueColorKey(y, a.cost)])

    const datasets: Chart['data']['datasets'] = [
      {
        label: 'Value',
        data: series,
        borderColor: cssToken(COLOR_TOKEN[model.colorKey]),
        backgroundColor: rgbaFromToken(COLOR_TOKEN[model.colorKey], 0.14),
        borderWidth: 2,
        stepped: 'before',
        // One point is no trend — a filled flat band under it read as a grey block.
        fill: !single,
        segment: {
          // with stepped 'before' the segment between p0 and p1 holds p0's value
          borderColor: (ctx) => colorFor(ctx.p0.parsed.y ?? 0),
          backgroundColor: (ctx) =>
            rgbaFromToken(COLOR_TOKEN[valueColorKey(ctx.p0.parsed.y ?? 0, a.cost)], 0.14),
        },
        pointRadius: series.map((p) => (p.i >= 0 ? POINT.radius : 0)),
        pointBackgroundColor: cssToken('--bg'),
        pointBorderColor: series.map((p) => colorFor(p.y)),
        pointBorderWidth: POINT.border,
        pointHoverRadius: POINT.hoverRadius,
        // The newest value usually sits on the plot's right edge (it's today), the oldest on
        // its left: clipped to the plot, each drew as half a dot. The canvas keeps room for them.
        clip: false,
      },
    ]
    if (costLine) {
      datasets.push({
        label: purchaseCopy(a.category).chartLabel,
        data: [
          { x: costLine.x0, y: costLine.value },
          { x: costLine.xMax, y: costLine.value },
        ],
        borderColor: cssToken('--faint'),
        borderWidth: 1,
        borderDash: [4, 4],
        pointRadius: 0,
        fill: false,
      })
    }

    const existing = instRef.current
    if (existing) {
      existing.data.datasets = datasets
      if (existing.options.scales) {
        const xScale = existing.options.scales.x
        const yScale = existing.options.scales.y
        if (xScale) {
          xScale.min = x0
          xScale.max = xMax
        }
        if (yScale) {
          yScale.min = yMin
          yScale.max = yMax
        }
      }
      existing.update()
      return
    }

    instRef.current = new Chart(canvasRef.current, {
      type: 'line',
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        // Both edge x-ticks (yearTicks' x0/xMax) sit exactly at the plot area's own left/
        // right edge, and a tick's label is center-anchored on its own point — with zero
        // padding, roughly the outer half of each 4-digit year's text draws past the canvas
        // and gets clipped ("2024" → "24"). A little room on both sides is enough at this
        // font size — and on the right, room for a whole dot on the edge, hovered or not.
        layout: { padding: { left: 4, right: POINT_REACH } },
        interaction: { mode: 'nearest', intersect: false, axis: 'x' },
        plugins: {
          legend: { display: false },
          tooltip: {
            filter: (it) => it.datasetIndex === 0 && (it.raw as MarkChartSeriesPoint).i >= 0,
            position: 'nearest',
            yAlign: 'bottom',
            caretSize: 0,
            caretPadding: 12,
            backgroundColor: cssToken('--bg-2'),
            borderColor: cssToken('--line-strong'),
            borderWidth: 1,
            titleColor: cssToken('--faint'),
            bodyColor: cssToken('--ink'),
            titleFont: { size: 11, weight: 600 },
            bodyFont: { size: 12.5, weight: 700 },
            titleMarginBottom: 3,
            padding: { top: 7, bottom: 7, left: 10, right: 10 },
            cornerRadius: 8,
            displayColors: false,
            callbacks: {
              title: (items) => {
                const first = items[0]
                if (!first) return ''
                const m = marksAsc[(first.raw as MarkChartSeriesPoint).i]
                return m ? monthYear(m.date) : ''
              },
              label: (it) => {
                const m = marksAsc[(it.raw as MarkChartSeriesPoint).i]
                if (!m) return ''
                if (amountsHidden()) return '***'
                if (a.cost == null || m.value === a.cost) return fmt(m.value)
                const d = m.value - a.cost
                return `${fmt(m.value)}   ${d >= 0 ? '+' : '−'}${Math.abs((d / a.cost) * 100).toFixed(1)}%`
              },
            },
          },
        },
        scales: {
          x: {
            type: 'linear',
            min: x0,
            max: xMax,
            // Chart.js's own auto-ticker skips the domain's real start year whenever a
            // mark isn't dated Jan 1st — see yearTicks()'s own comment.
            afterBuildTicks: (axis: Scale) => {
              axis.ticks = yearTicks(x0, xMax).map((value) => ({ value }))
            },
            grid: { color: cssToken('--line'), drawTicks: false },
            border: { color: cssToken('--line') },
            ticks: {
              color: cssToken('--faint'),
              font: { size: 11 },
              // Floor, not round — yearTicks() keeps the two edge ticks at the EXACT
              // fractional x0/xMax (for correct, non-expanding pixel placement) and relies
              // on this callback to display each one's whole year; Math.round(2024.75)
              // would show "2025" for a tick that's genuinely still in 2024.
              callback: (v) => String(Math.floor(v as number)),
              // The edge tick and its neighbor can land under a year apart (e.g. x0 at
              // 2024.75, next tick 2025) — autoSkip drops whichever label doesn't fit rather
              // than overlapping "2024"/"2025" into unreadable mush.
              autoSkip: true,
            },
          },
          y: {
            min: yMin,
            max: yMax,
            // Forces an explicit tick at the exact purchase price so it's readable straight
            // off the axis — otherwise the dashed "Purchase price" line has no number
            // attached to it at all, only wherever it happens to fall between the generic
            // auto-generated ticks.
            afterBuildTicks: (axis: Scale) => {
              let ticks = axis.ticks.map((t) => t.value)
              if (costLine) ticks = withPinnedTick(ticks, costLine.value)
              axis.ticks = ticks.map((value) => ({ value }))
            },
            grid: { color: cssToken('--line'), drawTicks: false },
            border: { display: false },
            ticks: {
              // Back outside the plot on every width (owner's call) — labels reserve their
              // own column to the left again, same on mobile and desktop.
              color: cssToken('--faint'),
              font: { size: 11 },
              maxTicksLimit: 5,
              padding: 8,
              callback: (v) => (amountsHidden() ? '***' : fmtK(v as number)),
            },
          },
        },
      },
    })
  }, [a.id, drawn, theme])

  // Its axis and tooltip read the flag when drawn, so a flip has to redraw what's on screen.
  useEffect(
    () =>
      subscribeAmountsHidden(() => {
        instRef.current?.update('none')
      }),
    [],
  )

  useEffect(() => {
    return () => {
      instRef.current?.destroy()
      instRef.current = null
    }
  }, [])

  return (
    <div className="as-chart">
      <div className="as-chart-canvas">
        <canvas ref={canvasRef} />
      </div>
      <div className="as-chart-lg">
        <span>
          {/* The swatch is the line's own colour: good above the purchase price, bad below. */}
          <i style={{ background: `var(${COLOR_TOKEN[valueColorKey(a.value, a.cost)]})` }} />
          Value
        </span>
        {/* The dashed horizontal line IS this dataset (pushed above only when a.cost != null)
            — it had no legend entry at all, so it read as an unexplained line on the chart. */}
        {a.cost != null && (
          <span>
            <i
              style={{
                backgroundImage:
                  'repeating-linear-gradient(to right, var(--faint) 0 4px, transparent 4px 8px)',
              }}
            />
            {purchaseCopy(a.category).chartLabel}
          </span>
        )}
        {a.marks.length === 1 && (
          <Text size="xs" tone="muted">
            One value so far — update it to see a trend
          </Text>
        )}
      </div>
    </div>
  )
}
