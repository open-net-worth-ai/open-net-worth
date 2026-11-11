import type { SeriesMarker, Time } from 'lightweight-charts'
import { toTime } from '../assetPriceChartLwc'
import type { ActivityRow } from './activityRows'

const SAME_WEEK_DAYS = 7

const days = (a: string, b: string) =>
  Math.abs(new Date(b).getTime() - new Date(a).getTime()) / 86_400_000
const at = (pts: { date: string }[], d: string) =>
  [...pts].reverse().find((p) => p.date <= d)?.date ?? null

/** Markers on the price line: a "B" or "S" on every day the source dates a trade in this stock
 *  (a member's report, a fund's Form 4s), and "Filed" on the day it became public — dropped
 *  when a trade marker sits within a week of it, since two labels on one spot is noise. */
export function tradeMarkers(
  row: ActivityRow,
  pts: { date: string }[],
  trades: { date: string; up: boolean; quarter?: boolean }[],
  color: { buy: string; sell: string; filed: string },
): SeriesMarker<Time>[] {
  const out: SeriesMarker<Time>[] = []
  const seen = new Set<string>()
  const placed: { d: string; up: boolean }[] = []
  for (const t of [...trades].sort((a, b) => a.date.localeCompare(b.date))) {
    const d = at(pts, t.date)
    // One marker per order: fills in the same direction within a week are one B (or S).
    if (!d || placed.some((m) => m.up === t.up && days(m.d, d) <= SAME_WEEK_DAYS)) continue
    placed.push({ d, up: t.up })
    seen.add(`${d}${t.up}`)
    // A 13F change has no trade day: a square at the quarter's end says "during this quarter".
    out.push({
      time: toTime(d) as Time,
      position: t.up ? 'belowBar' : 'aboveBar',
      shape: t.quarter ? 'square' : t.up ? 'arrowUp' : 'arrowDown',
      color: t.up ? color.buy : color.sell,
      text: t.up ? 'B' : 'S',
    })
  }
  const filed = at(pts, row.filedAt)
  const near = [...seen].some((k) => filed && days(k.slice(0, 10), filed) <= SAME_WEEK_DAYS)
  if (filed && !near)
    out.push({
      time: toTime(filed) as Time,
      position: 'aboveBar',
      shape: 'circle',
      color: color.filed,
      text: 'Filed',
    })
  return out.sort((a, b) => Number(a.time) - Number(b.time))
}
