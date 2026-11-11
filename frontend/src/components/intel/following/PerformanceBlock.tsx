import { useState } from 'react'
import { ErrorState, Skeleton, StatInfo } from '../../ui'
import { PerfChart } from '../../tabs/investments/PerformanceTab'
import type { Follow, PerfPeriod } from '../../../api/followedPortfolios'
import { COPY } from './following.copy'
import { shortDate, signedPct, toneOf } from './following.format'
import { usePerformance } from './useFollowing'

const PERIODS: { key: PerfPeriod; label: string }[] = [
  { key: '1M', label: '1M' },
  { key: '6M', label: '6M' },
  { key: '12M', label: '12M' },
  { key: 'FILING', label: 'Since filing' },
]

interface Props {
  follow: Follow
  period: PerfPeriod
  onPeriod: (p: PerfPeriod) => void
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
        <b className={toneOf(ahead)}>
          {' '}
          {signedPct(ahead, 'pts')} {ahead >= 0 ? 'ahead' : 'behind'}
        </b>
      </span>
    </div>
  )
}

/** The reason to copy a portfolio, or not: how its current holdings have done. */
export function PerformanceBlock({ follow, period, onPeriod }: Props) {
  const perf = usePerformance(follow.id)
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
          <div className="fol-perf-cap">
            {point
              ? COPY.perfOn(shortDate(point.date))
              : win?.end
                ? `${COPY.perfCaption[period]}, to ${shortDate(win.end)}`
                : COPY.perfCaption[period]}
            <StatInfo text={COPY.perfInfo[period]} />
          </div>
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
        </>
      ) : (
        <p className="fol-foot">{COPY.perfUnavailable}</p>
      )}
    </div>
  )
}
