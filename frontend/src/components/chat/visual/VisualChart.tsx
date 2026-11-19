import { useEffect, useMemo, useRef, useState } from 'react'
import { useTheme } from '../../../theme'
import { paletteAt } from '../../../utils/constants'
import { OvChart, TICK, axes, kTick, legend } from '../../tabs/overview/charts'
import type { VisualBlock } from '../../../types/chat'
import { fDate, fMonth } from '../../../utils/format'
import { formatVisualValue } from './format'

type ChartBlock = Extract<VisualBlock, { type: 'chart' }>

/** Below this width (a phone, or a narrow side panel) a chart lays itself out for the space. */
const NARROW_PX = 480

/** The width the chart actually has, kept current as the column resizes. */
function useWidth() {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setWidth(Math.round(e.contentRect.width)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, width] as const
}

/** Line and grouped-bar charts: the shared Chart.js wrapper, so axes, legend, theme and the
 *  privacy mask are the ones every other screen has. Null values are gaps.
 *
 *  Laid out for the room it has, not shrunk: bars turn horizontal — names in a column on the
 *  left, read straight across — when the names are long or the column is narrow, and the chart
 *  grows taller on a phone instead of flattening (owner, 2026-11-18). */
export function VisualChart({ block }: { block: ChartBlock }) {
  const { resolved } = useTheme()
  const { kind, format, currency, labels, series } = block
  const [box, width] = useWidth()
  const narrow = width > 0 && width < NARROW_PX
  const isLine = kind === 'line'
  const shown = useMemo(() => displayLabels(labels), [labels])
  const longest = Math.max(0, ...shown.map((l) => l.length))
  const across = !isLine && (longest > 12 || (narrow && shown.length > 4))
  const height = across
    ? Math.max(160, shown.length * (series.length * 12 + 22) + 48)
    : narrow
      ? 260
      : 220
  const sig = useMemo(() => JSON.stringify([block, resolved, across]), [block, resolved, across])
  const fmt = (v: unknown) => formatVisualValue(Number(v), format, currency, true)
  const grid = resolved === 'dark' ? 'rgba(0,0,0,0.6)' : 'rgba(0,0,0,0.07)'
  const lines = {
    grid: { color: grid, lineWidth: 0.3, drawTicks: false },
    border: { display: true, color: grid, width: 0.3 },
  }
  const valueTicks = { color: TICK(), callback: format === 'money' ? kTick : fmt }
  const nameTicks = isLine
    ? { color: TICK(), maxTicksLimit: 6, maxRotation: 0 }
    : { color: TICK(), autoSkip: false, maxRotation: across ? 0 : 45 }

  const config = {
    type: isLine ? 'line' : 'bar',
    data: {
      labels: shown,
      datasets: series.map((s, i) => {
        const color = paletteAt(i)
        return isLine
          ? {
              label: s.name,
              data: s.values,
              borderColor: color,
              backgroundColor: color,
              borderWidth: 2,
              pointRadius: labels.length > 24 ? 0 : 3,
              tension: 0.25,
              spanGaps: false,
            }
          : {
              label: s.name,
              data: s.values,
              backgroundColor: color,
              maxBarThickness: across ? 18 : 28,
              borderRadius: 3,
              borderSkipped: 'start' as const,
            }
      }),
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: across ? ('y' as const) : ('x' as const),
      interaction: { mode: 'index' as const, intersect: false, axis: across ? 'y' : 'x' },
      // A bar is unreadable without its name, so bars label every category; a line's dates
      // thin out on their own. A 0.3px grid both ways, closed into a box, a shade darker than
      // the page in dark mode (owner, 2026-11-18) — the same lines as Lucius's own drawings.
      scales: axes(
        across
          ? { x: { ...lines, ticks: valueTicks }, y: { ...lines, ticks: nameTicks } }
          : { x: { ...lines, ticks: nameTicks }, y: { ...lines, ticks: valueTicks } },
      ),
      plugins: {
        legend: legend(series.length > 1),
        tooltip: {
          callbacks: {
            label: (c: {
              dataset: { label?: string }
              parsed: { x: number | null; y: number | null }
            }) => {
              const v = across ? c.parsed.x : c.parsed.y
              return v == null ? '' : `${c.dataset.label}: ${fmt(v)}`
            },
          },
        },
      },
    },
  }

  return (
    <div
      ref={box}
      className="ivis-chart"
      style={{ height }}
      role="img"
      aria-label={series.map((s) => s.name).join(', ')}
    >
      <OvChart config={config} sig={sig} minHeight={160} />
    </div>
  )
}

/** Dates as the screens write them ("Mar", "Mar 2025", "6 Oct"); any other label as given.
 *  Display only: the label's order and the value under it never change. */
function displayLabels(labels: string[]): string[] {
  if (labels.every((l) => /^\d{4}-\d{2}$/.test(l))) return labels.map((l) => fMonth(l))
  if (!labels.every((l) => /^\d{4}-\d{2}-\d{2}/.test(l))) return labels
  const monthEnds =
    labels.length > 1 &&
    labels.every((l, i) => i === 0 || l.slice(0, 7) !== labels[i - 1].slice(0, 7))
  return labels.map((l) => (monthEnds ? fMonth(l.slice(0, 7)) : fDate(l.slice(0, 10))))
}
