import { useState } from 'react'
import { ErrorState, Skeleton, StatInfo } from '../../ui'
import { PerfChart } from '../../tabs/investments/PerformanceTab'
import type { Follow, PerfPeriod, PerfWindow } from '../../../api/followedPortfolios'
import { COPY } from './following.copy'
import { signedPct, toneOf } from './following.format'
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

function Headline({ win, benchmark }: { win: PerfWindow; benchmark: string }) {
  const ahead = (win.return ?? 0) - (win.benchmark_return ?? 0)
  return (
    <div className="fol-perf-head">
      <span className={`fol-perf-num ${toneOf(win.return)}`}>{signedPct(win.return)}</span>
      <span className="fol-perf-vs">
        {benchmark} {signedPct(win.benchmark_return)}
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
  const [hovered, setHovered] = useState(false)
  const win = perf.data?.periods[period]
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
    <div
      className="fol-perf"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      data-hovered={hovered || undefined}
    >
      <div className="fol-perf-top">
        <div>
          {win?.status === 'ok' ? (
            <Headline win={win} benchmark={benchmark} />
          ) : (
            <div className="fol-perf-num flat">—</div>
          )}
          <div className="fol-perf-cap">
            {COPY.perfCaption[period]}
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
