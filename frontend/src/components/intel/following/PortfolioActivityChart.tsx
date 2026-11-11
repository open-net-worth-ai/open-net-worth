import { useCallback, useMemo } from 'react'
import type { SeriesMarker, Time } from 'lightweight-charts'
import type { Follow, PerfWindow } from '../../../api/followedPortfolios'
import { toTime } from '../assetPriceChartLwc'
import { ActivityChart, type ChartColors } from './ActivityChart'
import { allMoves, biggestPerQuarter } from './activityRows'
import { evenSpacing } from './evenSpacing'

// Fills of one order can run over a few weeks (a large stake bought in tranches).
const SAME_ORDER_DAYS = 21
const days = (a: string, b: string) =>
  Math.abs(new Date(b).getTime() - new Date(a).getTime()) / 86_400_000

/** Nothing picked: the portfolio's own year (its holdings' return), with every move on it —
 *  each order once, as "B ACME" or "S ACME". Picking a row zooms into that stock. */
export function PortfolioActivityChart({
  follow,
  win,
  range,
}: {
  follow: Follow
  win: PerfWindow
  /** Zooms to this span (a chosen quarter); the whole history when absent. */
  range?: { from: string; to: string }
}) {
  const series = useMemo(() => evenSpacing(win.series), [win.series])
  const points = useMemo(() => series.map((p) => ({ date: p.date, value: p.portfolio })), [series])
  const moves = useMemo(() => allMoves(follow), [follow])
  const quarterly = useMemo(() => biggestPerQuarter(follow), [follow])
  const markers = useCallback(
    (c: ChartColors) => {
      const dates = series.map((p) => p.date)
      const snap = (d: string) => [...dates].reverse().find((x) => x <= d) ?? null
      const placed: { d: string; key: string }[] = []
      const out: SeriesMarker<Time>[] = []
      // A quarter's changes all fall on its last day: its biggest buy and biggest sale are
      // marked there by name (the table lists every one), not a pile of markers or a count.
      for (const p of quarterly) {
        const d = snap(p.date)
        if (!d) continue
        for (const m of p.top)
          out.push({
            time: toTime(d) as Time,
            position: m.up ? 'belowBar' : 'aboveBar',
            shape: m.up ? 'arrowUp' : 'arrowDown',
            color: m.up ? c.good : c.bad,
            text: `${m.up ? 'B' : 'S'} ${m.symbol}`,
          })
      }
      for (const m of moves) {
        const d = snap(m.date)
        if (m.quarter) continue
        const key = `${m.symbol}${m.up}`
        if (!d || placed.some((p) => p.key === key && days(p.d, d) <= SAME_ORDER_DAYS)) continue
        placed.push({ d, key })
        out.push({
          time: toTime(d) as Time,
          position: m.up ? 'belowBar' : 'aboveBar',
          shape: m.quarter ? 'square' : m.up ? 'arrowUp' : 'arrowDown',
          color: m.up ? c.good : c.bad,
          text: `${m.up ? 'B' : 'S'} ${m.symbol}`,
        })
      }
      return out.sort((a, b) => Number(a.time) - Number(b.time))
    },
    [moves, quarterly, series],
  )
  return (
    <ActivityChart
      points={points}
      markers={markers}
      percent
      range={range}
      label={`${follow.name}: its holdings over the past year, with every move marked`}
    />
  )
}
