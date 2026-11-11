import { useCallback, useMemo, useState } from 'react'
import { Badge, ErrorState, SegmentedControl } from '../../ui'
import type { FollowModel, FollowPerformance, PerfPeriod } from '../../../api/followedPortfolios'
import { fCompactAmount } from '../../../utils/format'
import { ActivityChart } from './ActivityChart'
import { evenSpacing } from './evenSpacing'
import { COPY } from './following.copy'
import { shortDate, signedPct, toneOf } from './following.format'

const PERIODS: { value: PerfPeriod; label: string }[] = [
  { value: '1M', label: '1M' },
  { value: '6M', label: '6M' },
  { value: '12M', label: '12M' },
  { value: 'FILING', label: 'Since filed' },
]
const NO_MARKERS = () => []

const signedUsd = (n: number) => `${n >= 0 ? '+' : '−'}${fCompactAmount(Math.abs(n), 'USD')}`
const signedCount = (n: number) => `${n >= 0 ? '+' : '−'}${Math.abs(n)}`

interface Props {
  model: FollowModel
  perf: FollowPerformance
  onRetry: () => void
}

/** A figure with its change since the previous report beside it: no box, no caption. */
function Figure({
  label,
  value,
  change,
}: {
  label: string
  value: string
  change?: { text: string; n: number }
}) {
  return (
    <div className="fol-fig">
      <span className="fol-fig-l">{label}</span>
      <span className="fol-fig-row">
        <span className="fol-fig-v">{value}</span>
        {/* An unchanged figure carries no badge: "+0" is noise. */}
        {change && change.n !== 0 && (
          <Badge tone={change.n > 0 ? 'pos' : 'neg'}>{change.text}</Badge>
        )}
      </span>
    </div>
  )
}

/** What they hold and how those holdings have done against the benchmark. The return chart
 *  is the page's one chart engine (dates along the bottom); hovering shows a tooltip over the
 *  plot, so nothing on the page moves. */
export function OverviewTab({ model, perf, onRetry }: Props) {
  const [period, setPeriod] = useState<PerfPeriod>('12M')
  const win = perf.periods[period]
  const series = useMemo(() => evenSpacing(win?.series ?? []), [win?.series])
  const points = useMemo(() => series.map((p) => ({ date: p.date, value: p.portfolio })), [series])
  const compare = useMemo(
    () =>
      series.flatMap((p) => (p.benchmark == null ? [] : [{ date: p.date, value: p.benchmark }])),
    [series],
  )
  const tip = useCallback(
    (date: string) => {
      const p = series.find((x) => x.date === date)
      if (!p) return null
      return (
        <>
          <div className="asd-chart-tip-date">{shortDate(date)}</div>
          <div className="asd-chart-tip-row">
            <span className="asd-chart-tip-k">{COPY.perfLine}</span>
            <span className={toneOf(p.portfolio)}>{signedPct(p.portfolio)}</span>
          </div>
          <div className="asd-chart-tip-row">
            <span className="asd-chart-tip-k">{perf.benchmark}</span>
            <span>{signedPct(p.benchmark)}</span>
          </div>
        </>
      )
    },
    [series, perf.benchmark],
  )
  const ok = win?.status === 'ok' && points.length >= 2
  const value = model.total_value
  const prevValue = model.prev_total_value
  const count = model.positions ?? model.holdings.length
  return (
    <section className="fol-sec" aria-label={COPY.tabOverview}>
      <div className="fol-figs">
        {value != null && (
          <Figure
            label={COPY.kpiValue}
            value={fCompactAmount(value, 'USD')}
            change={
              prevValue != null
                ? { text: signedUsd(value - prevValue), n: value - prevValue }
                : undefined
            }
          />
        )}
        <Figure
          label={COPY.kpiCount}
          value={String(count)}
          change={
            model.prev_positions != null && model.positions != null
              ? {
                  text: signedCount(model.positions - model.prev_positions),
                  n: model.positions - model.prev_positions,
                }
              : undefined
          }
        />
      </div>
      <div className="fol-perf">
        <h2 className="fol-sec-title">{COPY.returnTitle}</h2>
        {/* As Google Finance: one headline with its period in words after it, the benchmark
            on a small line under it (its dashed mark before the NAME, never before a number),
            and the periods as quiet tabs under the chart. */}
        <div className="fol-ret">
          <span className="fol-ret-row">
            <span className={`fol-fig-v ${toneOf(win?.return)}`}>
              {ok ? signedPct(win?.return) : '—'}
            </span>
            <span className="fol-ret-when">{COPY.returnWhen[period]}</span>
          </span>
          <span className="fol-ret-bench">
            <i className="fol-line fol-line--bench" aria-hidden="true" />
            {perf.benchmark} {ok ? signedPct(win?.benchmark_return) : '—'}
          </span>
        </div>
        {ok ? (
          <ActivityChart
            points={points}
            markers={NO_MARKERS}
            percent
            compare={compare}
            tip={tip}
            label={COPY.perfCaption[period]}
          />
        ) : null}
        <div className="fol-periods">
          <SegmentedControl
            options={PERIODS}
            value={period}
            onChange={setPeriod}
            ariaLabel={COPY.perfPeriodLabel}
          />
        </div>
        {ok ? null : win ? (
          <p className="fol-foot">{COPY.perfUnavailable}</p>
        ) : (
          <ErrorState title={COPY.perfErrorTitle} message={COPY.perfErrorBody} onRetry={onRetry} />
        )}
      </div>
    </section>
  )
}
