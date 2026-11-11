import { useCallback, useMemo, useState } from 'react'
import { Badge, ErrorState, SegmentedControl } from '../../ui'
import type { FollowModel, FollowPerformance } from '../../../api/followedPortfolios'
import { fetchReturns } from '../../../api/portfolio'
import { useQueryCache } from '../../../state/queryCache'
import { RANGES, rangeFrom } from './activityRows'
import { ToolSelect } from './Controls'
import { fCompactAmount } from '../../../utils/format'
import { ActivityChart } from './ActivityChart'
import { evenSpacing } from './evenSpacing'
import { COPY } from './following.copy'
import { shortDate, signedPct, toneOf } from './following.format'

const RANGE_OPTIONS = [
  ...RANGES.map((r) => ({ value: r.key, label: r.label })),
  { value: 'ALL', label: 'All' },
]
const NO_MARKERS = () => []
const SERVER_WINDOW: Record<string, '1M' | '6M' | '12M'> = { '1M': '1M', '6M': '6M', '1Y': '12M' }

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
  // The same ranges as Trading Activity, all read from the one whole-history series and
  // started at 0% on the range's first day (as that tab's chart is).
  const [period, setPeriod] = useState<string>('1Y')
  // 1M, 6M and 1Y are the server's own windows, the figures the cards show; the other ranges are
  // cut from the whole history and started at 0% on their first day.
  const own_window = SERVER_WINDOW[period] ? perf.periods[SERVER_WINDOW[period]] : undefined
  const win =
    own_window?.status === 'ok'
      ? own_window
      : perf.periods.ALL?.status === 'ok'
        ? perf.periods.ALL
        : perf.periods['12M']
  const from = own_window?.status === 'ok' ? undefined : rangeFrom(period)
  const series = useMemo(() => {
    const all = win?.status === 'ok' ? win.series : []
    const base = from ? ([...all].reverse().find((p) => p.date <= from) ?? all[0]) : all[0]
    if (!base) return []
    const since = (v: number | null, b: number | null) =>
      v == null || b == null ? null : (1 + v) / (1 + b) - 1
    return evenSpacing(
      all
        .filter((p) => p.date >= base.date)
        .map((p) => ({
          date: p.date,
          portfolio: since(p.portfolio, base.portfolio) ?? 0,
          benchmark: since(p.benchmark, base.benchmark),
        })),
    )
  }, [win, from])
  const points = useMemo(() => series.map((p) => ({ date: p.date, value: p.portfolio })), [series])
  const compare = useMemo(
    () =>
      series.flatMap((p) => (p.benchmark == null ? [] : [{ date: p.date, value: p.benchmark }])),
    [series],
  )
  // Your own book over the same days: the Investments Performance tab's series, rebased to the
  // chart's first day (the same 0% start), only on the days the chart draws.
  const own = useQueryCache('/api/returns?period=ALL', () => fetchReturns('ALL'))
  const mine = useMemo(() => {
    const all = own.data?.series ?? []
    const first = series[0]?.date
    if (!first || all.length < 2) return []
    const base = [...all].reverse().find((p) => p.date <= first)
    if (!base) return []
    const days = new Set(series.map((p) => p.date))
    return all
      .filter((p) => p.date >= base.date && (days.has(p.date) || p === base))
      .map((p) => ({ date: p.date, value: (1 + p.portfolio) / (1 + base.portfolio) - 1 }))
  }, [own.data, series])
  const end = series[series.length - 1]
  const mineEnd = mine[mine.length - 1]?.value
  const tip = useCallback(
    (date: string) => {
      const p = series.find((x) => x.date === date)
      if (!p) return null
      const m = mine.find((x) => x.date === date)
      return (
        <>
          <div className="asd-chart-tip-date">{shortDate(date)}</div>
          <div className="asd-chart-tip-row">
            <span className="asd-chart-tip-k">{COPY.perfLine}</span>
            <span className={toneOf(p.portfolio)}>{signedPct(p.portfolio)}</span>
          </div>
          {m && (
            <div className="asd-chart-tip-row">
              <span className="asd-chart-tip-k">{COPY.yourPortfolio}</span>
              <span className={toneOf(m.value)}>{signedPct(m.value)}</span>
            </div>
          )}
          <div className="asd-chart-tip-row">
            <span className="asd-chart-tip-k">{perf.benchmark}</span>
            <span>{signedPct(p.benchmark)}</span>
          </div>
        </>
      )
    },
    [series, mine, perf.benchmark],
  )
  const ok = points.length >= 2
  const value = model.total_value
  const prevValue = model.prev_total_value
  const count = model.positions ?? model.holdings.length
  return (
    <section className="fol-sec fol-act" aria-label={COPY.tabOverview}>
      <div className="fol-figs fol-panel">
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
      <div className="fol-perf fol-panel">
        {/* Laid out like All moves: the title with its range on the left, the timeframe
            control on the right; then the three lines' figures across the card's full width,
            each under its own line style and name, so they compare at a glance. */}
        <div className="fol-sec-head">
          <h2 className="fol-sec-title">{COPY.returnTitleFor(period)}</h2>
          <div className="fol-tf">
            <div className="asd-tf-tabs">
              <SegmentedControl
                ariaLabel={COPY.perfPeriodLabel}
                options={RANGE_OPTIONS}
                value={period}
                onChange={setPeriod}
              />
            </div>
            <ToolSelect
              className="fol-tf-menu"
              label={COPY.perfPeriodLabel}
              value={period}
              options={RANGE_OPTIONS}
              onChange={setPeriod}
            />
          </div>
        </div>
        <div className="fol-ret3">
          <div className="fol-ret3-item">
            <span className="fol-ret3-l">
              <i className="fol-line fol-line--main" aria-hidden="true" />
              {COPY.perfLineToday}
            </span>
            <span className={`fol-ret3-v ${toneOf(end?.portfolio)}`}>
              {ok ? signedPct(end?.portfolio) : '—'}
            </span>
          </div>
          {mineEnd != null && (
            <div className="fol-ret3-item">
              <span className="fol-ret3-l">
                <i className="fol-line fol-line--mine" aria-hidden="true" />
                {COPY.yourPortfolio}
              </span>
              <span className={`fol-ret3-v ${toneOf(mineEnd)}`}>{signedPct(mineEnd)}</span>
            </div>
          )}
          <div className="fol-ret3-item">
            <span className="fol-ret3-l">
              <i className="fol-line fol-line--bench" aria-hidden="true" />
              {perf.benchmark}
            </span>
            <span className={`fol-ret3-v ${toneOf(end?.benchmark)}`}>
              {ok ? signedPct(end?.benchmark) : '—'}
            </span>
          </div>
        </div>
        {ok ? (
          <ActivityChart
            points={points}
            markers={NO_MARKERS}
            percent
            compare={compare}
            mine={mine}
            tip={tip}
            label={COPY.perfChartLabel}
          />
        ) : null}
        {ok ? null : win ? (
          <p className="fol-foot">{COPY.perfUnavailable}</p>
        ) : (
          <ErrorState title={COPY.perfErrorTitle} message={COPY.perfErrorBody} onRetry={onRetry} />
        )}
      </div>
    </section>
  )
}
