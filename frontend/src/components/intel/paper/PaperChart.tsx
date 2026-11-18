import { useCallback, useMemo, useState } from 'react'
import type { SeriesMarker, Time } from 'lightweight-charts'
import { SegmentedControl, Skeleton } from '../../ui'
import type { PaperOrder } from '../../../api/paperTrading'
import { crosshairTimeFormatterFor, toTime } from '../assetPriceChartLwc'
import { ActivityChart, CHART_BOX, type ChartColors } from '../following/ActivityChart'
import { evenSpacing } from '../following/evenSpacing'
import { shortDate, signedPct, toneOf } from '../following/following.format'
import { usePaperPerformance } from './usePaper'

const RANGE_OPTIONS = [
  { value: '1W', label: '1W' },
  { value: '1M', label: '1M' },
  { value: '3M', label: '3M' },
  { value: '1Y', label: '1Y' },
  { value: 'ALL', label: 'All' },
]
const YOURS = 'Your investments'

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
  const fetched = usePaperPerformance(id, range).data
  // A new period keeps the last chart on screen until its own data arrives: dropping it while
  // loading made the page below jump up and back.
  const [shown, setShown] = useState(fetched)
  if (fetched && fetched !== shown) setShown(fetched)
  const perf = fetched ?? shown
  const intraday = !!perf?.intraday
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
  // Each fill sits on the chart's last point at or before it: its day on the daily line (a fill
  // on a day with no point yet lands on today's live one), its moment on the intraday one.
  const moves = useMemo(() => {
    const out: Move[] = []
    for (const o of orders) {
      if (o.status !== 'filled' || !o.fill) continue
      const at = intraday ? o.fill.filled_at : o.fill.filled_at.slice(0, 10)
      const d = [...series].reverse().find((p) => toTime(p.date) <= toTime(at))
      if (d) out.push({ date: d.date, symbol: o.symbol ?? o.name, up: o.side === 'buy' })
    }
    return out
  }, [orders, series, intraday])
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
      const p = series.find((x) => toTime(x.date) === toTime(date))
      if (!p) return null
      const here = moves.filter((m) => m.date === p.date)
      const when = intraday ? crosshairTimeFormatterFor(undefined)(toTime(p.date)) : shortDate(date)
      return (
        <>
          <div className="asd-chart-tip-date">{p.start ? 'Start' : when}</div>
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
    [series, moves, name, benchmark, intraday],
  )
  const period = (
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
  )
  // The first load is the loaded panel with the chart and figures greyed: the same rows at the
  // same heights, so nothing below it moves when the data arrives.
  if (!perf)
    return (
      <section className="fol-sec fol-act" aria-label={`${name} performance`} aria-busy="true">
        <div className="fol-perf fol-panel">
          <div className="fol-sec-head">
            <h2 className="fol-sec-title">Performance</h2>
          </div>
          <Skeleton height={CHART_BOX} />
          <div className="fol-ret3">
            <Figure label={name} line="main" loading />
            <Figure label={YOURS} line="mine" loading />
            <Figure label={benchmark} line="bench" loading />
          </div>
          {period}
        </div>
      </section>
    )
  if (points.length < 2) return null
  return (
    <section className="fol-sec fol-act" aria-label={`${name} performance`}>
      <div className="fol-perf fol-panel">
        <div className="fol-sec-head">
          <h2 className="fol-sec-title">Performance</h2>
        </div>
        <ActivityChart
          points={points}
          markers={markers}
          percent
          compare={compare.length ? compare : undefined}
          mine={mine.length ? mine : undefined}
          tip={tip}
          intraday={intraday}
          label={`${name}: return since the start, against ${YOURS.toLowerCase()} and ${benchmark}`}
        />
        {/* Below the chart, so the page's top row is only its two amounts. */}
        <div className="fol-ret3">
          <Figure label={name} line="main" value={perf.return_pct} />
          {perf.yours_return_pct != null && (
            <Figure
              label={YOURS}
              line={mine.length > 0 ? 'mine' : undefined}
              value={perf.yours_return_pct}
            />
          )}
          {perf.benchmark?.return_pct != null && (
            <Figure label={benchmark} line="bench" value={perf.benchmark.return_pct} />
          )}
        </div>
        {period}
      </div>
    </section>
  )
}

/** One figure under the chart: its line's swatch, its name and its return; greyed while loading. */
function Figure({
  label,
  line,
  value,
  loading,
}: {
  label: string
  line?: 'main' | 'mine' | 'bench'
  value?: number | null
  loading?: boolean
}) {
  return (
    <div className="fol-ret3-item">
      <span className="fol-ret3-l">
        {line && <i className={`fol-line fol-line--${line}`} aria-hidden="true" />}
        {label}
      </span>
      <span className={`fol-ret3-v ${loading ? '' : toneOf(value)}`}>
        {loading ? <Skeleton width={64} height="1em" /> : signedPct(value)}
      </span>
    </div>
  )
}
