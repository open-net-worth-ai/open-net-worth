import { useCallback, useMemo, useState } from 'react'
import type { SeriesMarker, Time } from 'lightweight-charts'
import { SegmentedControl } from '../../ui'
import type { PaperOrder } from '../../../api/paperTrading'
import { toTime } from '../assetPriceChartLwc'
import { ActivityChart, type ChartColors } from '../following/ActivityChart'
import { evenSpacing } from '../following/evenSpacing'
import { shortDate, signedPct, toneOf } from '../following/following.format'
import { usePaperPerformance } from './usePaper'

const RANGE_OPTIONS = [
  { value: '1M', label: '1M' },
  { value: '3M', label: '3M' },
  { value: '1Y', label: '1Y' },
  { value: 'ALL', label: 'All' },
]
const YOURS = 'Your investments'
const MIN_POINTS = 3

interface Move {
  date: string
  symbol: string
  up: boolean
}

/** Return since the start, on Copy Trading's chart: this portfolio, your investments and the
 *  benchmark, each fill marked with an arrow. Same panel, legend and tooltip as Copy Trading's
 *  Performance, so the two read alike. */
export function PaperChart({
  id,
  name,
  benchmark,
  orders,
}: {
  id: string
  name: string
  benchmark: string
  orders: PaperOrder[]
}) {
  const [range, setRange] = useState('ALL')
  const perf = usePaperPerformance(id, range).data
  const series = useMemo(() => evenSpacing(perf?.series ?? []), [perf?.series])
  const points = useMemo(() => series.map((p) => ({ date: p.date, value: p.paper ?? 0 })), [series])
  const compare = useMemo(
    () =>
      series
        .filter((p) => p.benchmark != null)
        .map((p) => ({ date: p.date, value: p.benchmark as number })),
    [series],
  )
  const mine = useMemo(
    () =>
      series
        .filter((p) => p.yours != null)
        .map((p) => ({ date: p.date, value: p.yours as number })),
    [series],
  )
  // Each fill sits on the chart's last day on or before it (a fill on a day with no point yet,
  // e.g. today before the close, lands on today's live point).
  const moves = useMemo(() => {
    const dates = series.map((p) => p.date)
    const out: Move[] = []
    for (const o of orders) {
      if (o.status !== 'filled' || !o.fill) continue
      const d = [...dates].reverse().find((x) => x <= o.fill!.filled_at.slice(0, 10))
      if (d) out.push({ date: d, symbol: o.symbol ?? o.name, up: o.side === 'buy' })
    }
    return out
  }, [orders, series])
  const markers = useCallback(
    (c: ChartColors) =>
      moves
        .filter((m, i) => moves.findIndex((o) => o.date === m.date && o.up === m.up) === i)
        .map(
          (m): SeriesMarker<Time> => ({
            time: toTime(m.date) as Time,
            position: m.up ? 'belowBar' : 'aboveBar',
            shape: m.up ? 'arrowUp' : 'arrowDown',
            color: m.up ? c.good : c.bad,
            text: '',
          }),
        )
        .sort((a, b) => Number(a.time) - Number(b.time)),
    [moves],
  )
  const tip = useCallback(
    (date: string) => {
      const p = series.find((x) => x.date === date)
      if (!p) return null
      const here = moves.filter((m) => m.date === date)
      return (
        <>
          <div className="asd-chart-tip-date">{p.start ? 'Start' : shortDate(date)}</div>
          <div className="asd-chart-tip-row">
            <span className="asd-chart-tip-k">{name}</span>
            <span className={toneOf(p.paper)}>{signedPct(p.paper)}</span>
          </div>
          {p.yours != null && (
            <div className="asd-chart-tip-row">
              <span className="asd-chart-tip-k">{YOURS}</span>
              <span className={toneOf(p.yours)}>{signedPct(p.yours)}</span>
            </div>
          )}
          {p.benchmark != null && (
            <div className="asd-chart-tip-row">
              <span className="asd-chart-tip-k">{benchmark}</span>
              <span>{signedPct(p.benchmark)}</span>
            </div>
          )}
          {here.map((m) => (
            <div key={`${m.symbol}${m.up}`} className="fol-tip-move">
              <span className={m.up ? 'up' : 'down'}>
                {m.up ? 'Bought' : 'Sold'} {m.symbol}
              </span>
            </div>
          ))}
        </>
      )
    },
    [series, moves, name, benchmark],
  )
  if (!perf || !series.length) return null
  // Two points (the start and now) draw a stub, not a chart: the figures above say it until
  // there is a few days' line to show.
  const drawn = points.length >= MIN_POINTS
  return (
    <section className="fol-sec fol-act" aria-label={`${name} performance`}>
      <div className="fol-perf fol-panel">
        <div className="fol-sec-head">
          <h2 className="fol-sec-title">Performance</h2>
        </div>
        <div className="fol-ret3">
          <div className="fol-ret3-item">
            <span className="fol-ret3-l">
              <i className="fol-line fol-line--main" aria-hidden="true" />
              {name}
            </span>
            <span className={`fol-ret3-v ${toneOf(perf.return_pct)}`}>
              {signedPct(perf.return_pct)}
            </span>
          </div>
          {perf.yours_return_pct != null && (
            <div className="fol-ret3-item">
              <span className="fol-ret3-l">
                <i className="fol-line fol-line--mine" aria-hidden="true" />
                {YOURS}
              </span>
              <span className={`fol-ret3-v ${toneOf(perf.yours_return_pct)}`}>
                {signedPct(perf.yours_return_pct)}
              </span>
            </div>
          )}
          {perf.benchmark?.return_pct != null && (
            <div className="fol-ret3-item">
              <span className="fol-ret3-l">
                <i className="fol-line fol-line--bench" aria-hidden="true" />
                {benchmark}
              </span>
              <span className={`fol-ret3-v ${toneOf(perf.benchmark.return_pct)}`}>
                {signedPct(perf.benchmark.return_pct)}
              </span>
            </div>
          )}
        </div>
        {drawn ? (
          <>
            <ActivityChart
              points={points}
              markers={markers}
              percent
              compare={compare.length ? compare : undefined}
              mine={mine.length ? mine : undefined}
              tip={tip}
              label={`${name}: return since the start, against ${YOURS.toLowerCase()} and ${benchmark}`}
            />
            <div className="fol-tf fol-tf--under">
              <div className="asd-tf-tabs">
                <SegmentedControl
                  ariaLabel="Period"
                  options={RANGE_OPTIONS}
                  value={range}
                  onChange={setRange}
                />
              </div>
            </div>
          </>
        ) : (
          range === 'ALL' && (
            <p className="fol-foot">The chart starts after a few days of trading.</p>
          )
        )}
      </div>
    </section>
  )
}
