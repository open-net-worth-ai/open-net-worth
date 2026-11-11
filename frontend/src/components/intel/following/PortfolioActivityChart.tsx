import { useCallback, useMemo } from 'react'
import type { SeriesMarker, Time } from 'lightweight-charts'
import type { Follow, PerfWindow } from '../../../api/followedPortfolios'
import { toTime } from '../assetPriceChartLwc'
import { ActivityChart, type ChartColors } from './ActivityChart'
import { allMoves, biggestPerQuarter } from './activityRows'
import { evenSpacing } from './evenSpacing'
import { shortDate, signedPct } from './following.format'

// Fills of one order can run over a few weeks (a large stake bought in tranches).
const SAME_ORDER_DAYS = 21
const days = (a: string, b: string) =>
  Math.abs(new Date(b).getTime() - new Date(a).getTime()) / 86_400_000

interface Mark {
  date: string
  symbol: string
  up: boolean
  /** Why this day: a dated trade, or the day a quarter's change was disclosed. */
  why: string
}

/** Nothing picked: the portfolio's line with its moves on it. A dated trade sits on its trade
 *  day; a quarter's change has no trade day, so it sits on the day it was DISCLOSED (the first
 *  day anyone could act on it) and the tooltip names the quarter it happened in. */
export function PortfolioActivityChart({
  follow,
  win,
  range,
}: {
  follow: Follow
  win: PerfWindow
  /** Zooms to this span (the chosen period); the whole history when absent. */
  range?: { from: string; to: string }
}) {
  const series = useMemo(() => evenSpacing(win.series), [win.series])
  const points = useMemo(() => series.map((p) => ({ date: p.date, value: p.portfolio })), [series])
  const moves = useMemo(() => allMoves(follow), [follow])
  const quarterly = useMemo(() => biggestPerQuarter(follow), [follow])
  const marks = useMemo(() => {
    const dates = series.map((p) => p.date)
    const snap = (d: string) => [...dates].reverse().find((x) => x <= d) ?? null
    const out: Mark[] = []
    for (const q of quarterly) {
      const d = snap(q.date)
      if (!d) continue
      for (const m of q.top)
        out.push({ date: d, ...m, why: `${q.during}, disclosed ${shortDate(q.date)}` })
    }
    const placed: { d: string; key: string }[] = []
    for (const m of moves) {
      if (m.quarter) continue
      const d = snap(m.date)
      const key = `${m.symbol}${m.up}`
      if (!d || placed.some((p) => p.key === key && days(p.d, d) <= SAME_ORDER_DAYS)) continue
      placed.push({ d, key })
      out.push({ date: d, symbol: m.symbol, up: m.up, why: `traded ${shortDate(m.date)}` })
    }
    return out
  }, [moves, quarterly, series])
  // One arrow per day and side: several trades on one day are one marker (the first one's
  // name); the tooltip lists them all.
  const markers = useCallback(
    (c: ChartColors) =>
      marks
        .filter((m, i) => marks.findIndex((o) => o.date === m.date && o.up === m.up) === i)
        .map(
          (m): SeriesMarker<Time> => ({
            time: toTime(m.date) as Time,
            position: m.up ? 'belowBar' : 'aboveBar',
            shape: m.up ? 'arrowUp' : 'arrowDown',
            color: m.up ? c.good : c.bad,
            text: `${m.up ? 'B' : 'S'} ${m.symbol}`,
          }),
        )
        .sort((a, b) => Number(a.time) - Number(b.time)),
    [marks],
  )
  const tip = useCallback(
    (date: string) => {
      const p = series.find((x) => x.date === date)
      const here = marks.filter((m) => m.date === date)
      return (
        <>
          <div className="asd-chart-tip-date">{shortDate(date)}</div>
          {p && (
            <div className="asd-chart-tip-row">
              <span className="asd-chart-tip-k">Holdings</span>
              <span>{signedPct(p.portfolio)}</span>
            </div>
          )}
          {here.map((m) => (
            <div key={`${m.symbol}${m.up}`} className="fol-tip-move">
              <span className={m.up ? 'up' : 'down'}>
                {m.up ? 'Bought' : 'Sold'} {m.symbol}
              </span>
              <span className="fol-tip-why">{m.why}</span>
            </div>
          ))}
        </>
      )
    },
    [marks, series],
  )
  return (
    <ActivityChart
      points={points}
      markers={markers}
      percent
      range={range}
      tip={tip}
      label={`${follow.name}: its holdings over the period, with each move marked`}
    />
  )
}
