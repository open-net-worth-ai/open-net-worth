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
  /** Named on the chart: a report's biggest buy or sale. A dated trade is an arrow only (Latest
   *  moves above names it; so does the tooltip). */
  named: boolean
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
  // A chosen period is drawn on its own: it starts at 0% on its first day (each point the return
  // since then), so the axis and the headline above it are the same figure. Zooming the whole
  // history kept the return since its start (+150% where the headline said +82%).
  const series = useMemo(() => {
    if (!range) return evenSpacing(win.series)
    const all = win.series
    const base = [...all].reverse().find((p) => p.date <= range.from) ?? all[0]
    const inside = all.filter((p) => p.date > (base?.date ?? '') && p.date <= range.to)
    if (!base) return []
    const since = (v: number, b: number) => (1 + v) / (1 + b) - 1
    return evenSpacing(
      [base, ...inside].map((p) => ({
        ...p,
        portfolio: since(p.portfolio, base.portfolio),
        benchmark:
          p.benchmark == null || base.benchmark == null ? null : since(p.benchmark, base.benchmark),
      })),
    )
  }, [win.series, range?.from, range?.to])
  const points = useMemo(() => series.map((p) => ({ date: p.date, value: p.portfolio })), [series])
  const moves = useMemo(() => allMoves(follow), [follow])
  const quarterly = useMemo(() => biggestPerQuarter(follow), [follow])
  const marks = useMemo(() => {
    const dates = series.map((p) => p.date)
    const last = dates[dates.length - 1] ?? ''
    // A move after the chart's last day (or before its first) is not on it.
    const snap = (d: string) =>
      d > last && range ? null : ([...dates].reverse().find((x) => x <= d) ?? null)
    const out: Mark[] = []
    for (const q of quarterly) {
      const d = snap(q.date)
      if (!d) continue
      for (const m of q.top)
        out.push({ date: d, ...m, named: true, why: `${q.during}, disclosed ${shortDate(q.date)}` })
    }
    const placed: { d: string; key: string }[] = []
    for (const m of moves) {
      if (m.quarter) continue
      const d = snap(m.date)
      const key = `${m.symbol}${m.up}`
      if (!d || placed.some((p) => p.key === key && days(p.d, d) <= SAME_ORDER_DAYS)) continue
      placed.push({ d, key })
      out.push({
        date: d,
        symbol: m.symbol,
        up: m.up,
        named: false,
        why: `traded ${shortDate(m.date)}`,
      })
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
            text: m.named ? `${m.up ? 'B' : 'S'} ${m.symbol}` : '',
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
      tip={tip}
      label={`${follow.name}: its holdings over the period, with each move marked`}
    />
  )
}
