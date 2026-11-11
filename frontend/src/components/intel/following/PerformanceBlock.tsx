import { useState, type ReactNode } from 'react'
import { ErrorState, Skeleton } from '../../ui'
import { PerfChart } from '../../tabs/investments/PerformanceTab'
import type { FollowPerformance, PerfPeriod } from '../../../api/followedPortfolios'
import { COPY } from './following.copy'
import { shortDate, signedPct, toneOf } from './following.format'

const PERIODS: { key: PerfPeriod; label: string }[] = [
  { key: '1M', label: '1M' },
  { key: '6M', label: '6M' },
  { key: '12M', label: '12M' },
  { key: 'FILING', label: 'Since filed' },
]

interface Props {
  perf: FollowPerformance | undefined
  loading: boolean
  onRetry: () => void
  period: PerfPeriod
  onPeriod: (p: PerfPeriod) => void
  /** Sits at the right of the legend row (the source link and the last check). */
  foot?: ReactNode
}

interface HeadlineProps {
  /** The window's own figures, or one day's when the pointer is on the chart. */
  value: number | undefined
  bench: number | null | undefined
  benchmark: string
}

function Headline({ value, bench, benchmark }: HeadlineProps) {
  const ahead = (value ?? 0) - (bench ?? 0)
  return (
    <div className="fol-perf-head">
      <span className={`fol-perf-num ${toneOf(value)}`}>{signedPct(value)}</span>
      <span className="fol-perf-vs">
        {benchmark} {signedPct(bench)}
      </span>
      <span className={`fol-perf-vs ${toneOf(ahead)}`}>{signedPct(ahead, 'pts')}</span>
    </div>
  )
}

/** The reason to copy a portfolio, or not: how its current holdings have done. */
export function PerformanceBlock({ perf: data, loading, onRetry, period, onPeriod, foot }: Props) {
  const perf = { data, initialLoading: loading, refresh: onRetry }
  const [at, setAt] = useState<number | null>(null)
  const win = perf.data?.periods[period]
  // The headline is the window's end unless the pointer is on the chart: then it is that day,
  // so the big figure and the tooltip can never seem to disagree.
  const point = at !== null ? win?.series[at] : undefined
  const benchmark = perf.data?.benchmark ?? ''

  if (perf.initialLoading) return <Skeleton height={248} />
  if (!perf.data)
    return (
      <ErrorState
        title={COPY.perfErrorTitle}
        message={COPY.perfErrorBody}
        onRetry={() => void perf.refresh()}
      />
    )
  return (
    <div className="fol-perf">
      <div className="fol-perf-top">
        <div>
          {win?.status === 'ok' ? (
            <Headline
              value={point ? point.portfolio : win.return}
              bench={point ? point.benchmark : win.benchmark_return}
              benchmark={benchmark}
            />
          ) : (
            <div className="fol-perf-num flat">—</div>
          )}
          {point && <div className="fol-perf-cap">{shortDate(point.date)}</div>}
        </div>
        <div className="mk-seg" role="radiogroup" aria-label={COPY.perfPeriodLabel}>
          {PERIODS.map((p) => (
            <button
              key={p.key}
              type="button"
              role="radio"
              aria-checked={period === p.key}
              className={period === p.key ? 'on' : ''}
              onClick={() => onPeriod(p.key)}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>
      {win?.status === 'ok' && win.series.length >= 2 ? (
        <>
          <PerfChart
            series={win.series}
            hasBenchmark
            benchLabel={benchmark}
            mainLabel={COPY.perfLine}
            period="1Y"
            exactDates
            onHover={setAt}
          />
          <div className="fol-legend-row">
            <div className="mk-legend">
              <span>
                <i style={{ background: 'var(--good)' }} />
                {COPY.perfLine}
              </span>
              <span>
                <i style={{ background: 'var(--muted)' }} />
                {benchmark}
              </span>
            </div>
            {foot}
          </div>
        </>
      ) : (
        <>
          <p className="fol-foot">{COPY.perfUnavailable}</p>
          {foot}
        </>
      )}
    </div>
  )
}
