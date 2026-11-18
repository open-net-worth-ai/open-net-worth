import { useMemo } from 'react'
import { useTheme } from '../../../theme'
import { paletteAt } from '../../../utils/constants'
import { OvChart, TICK, axes, kTick, legend } from '../../tabs/overview/charts'
import type { VisualBlock } from '../../../types/chat'
import { fDate, fMonth } from '../../../utils/format'
import { formatVisualValue } from './format'

type ChartBlock = Extract<VisualBlock, { type: 'chart' }>

/** Line and grouped-bar charts: the shared Chart.js wrapper, so axes, legend, theme and the
 *  privacy mask are the ones every other screen has. Null values are gaps. */
export function VisualChart({ block }: { block: ChartBlock }) {
  const { resolved } = useTheme()
  const { kind, format, currency, labels, series } = block
  const sig = useMemo(() => JSON.stringify([block, resolved]), [block, resolved])
  const fmt = (v: unknown) => formatVisualValue(Number(v), format, currency, true)
  const isLine = kind === 'line'
  const grid = resolved === 'dark' ? 'rgba(0,0,0,0.6)' : 'rgba(0,0,0,0.07)'
  const shown = useMemo(() => displayLabels(labels), [labels])

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
          : { label: s.name, data: s.values, backgroundColor: color, maxBarThickness: 28 }
      }),
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index' as const, intersect: false },
      scales: axes({
        // A bar is unreadable without its name, so bars label every category; a line's dates
        // thin out on their own.
        x: {
          grid: { color: grid, lineWidth: 0.3, drawTicks: false },
          border: { display: true, color: grid, width: 0.3 },
          ticks: isLine
            ? { color: TICK(), maxTicksLimit: 6, maxRotation: 0 }
            : { color: TICK(), autoSkip: false, maxRotation: 45 },
        },
        // A 0.3px grid both ways, closed into a box, a shade darker than the page in dark mode
        // (owner, 2026-11-18) — the same lines as Lucius's own drawings (drawingStyle.ts).
        y: {
          ticks: { color: TICK(), callback: format === 'money' ? kTick : fmt },
          grid: { color: grid, lineWidth: 0.3, drawTicks: false },
          border: { display: true, color: grid, width: 0.3 },
        },
      }),
      plugins: {
        legend: legend(series.length > 1),
        tooltip: {
          callbacks: {
            label: (c: { dataset: { label?: string }; parsed: { y: number | null } }) =>
              c.parsed.y == null ? '' : `${c.dataset.label}: ${fmt(c.parsed.y)}`,
          },
        },
      },
    },
  }

  return (
    <div className="ivis-chart" role="img" aria-label={series.map((s) => s.name).join(', ')}>
      <OvChart config={config} sig={sig} minHeight={180} />
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
