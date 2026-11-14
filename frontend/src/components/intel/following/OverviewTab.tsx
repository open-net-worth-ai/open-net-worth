import { useCallback, useMemo, useState } from 'react'
import { ErrorState, SegmentedControl, Skeleton } from '../../ui'
import { Tooltip } from '../../ui/Tooltip'
import {
  fetchPreviewCompare,
  previewCompareKey,
  type Band,
  type FollowKind,
  type FollowPerformance,
} from '../../../api/followedPortfolios'
import { fetchReturns } from '../../../api/portfolio'
import { useQueryCache } from '../../../state/queryCache'
import { RANGES, rangeFrom } from './activityRows'
import { ToolSelect } from './Controls'
import { ActivityChart } from './ActivityChart'
import { evenSpacing } from './evenSpacing'
import { COPY } from './following.copy'
import { shortDate, signedPct, toneOf } from './following.format'
import { YouVs } from './YouVs'

const RANGE_OPTIONS = [
  ...RANGES.map((r) => ({ value: r.key, label: r.label })),
  { value: 'ALL', label: 'All' },
]
const NO_MARKERS = () => []
const SERVER_WINDOW: Record<string, '1M' | '6M' | '12M'> = { '1M': '1M', '6M': '6M', '1Y': '12M' }

interface Props {
  perf: FollowPerformance
  /** The portfolio's name: its line is labelled with it. */
  name: string
  kind: FollowKind
  sourceRef: string
  onRetry: () => void
  /** The page's one time range, shared with Trading Activity (owner, 2026-11-13). */
  range: string
  onRange: (key: string) => void
}

/** What they hold and how those holdings have done against the benchmark. The return chart
 *  is the page's one chart engine (dates along the bottom); hovering shows a tooltip over the
 *  plot, so nothing on the page moves. */
export function OverviewTab({ perf, name, kind, sourceRef, onRetry, range, onRange }: Props) {
  // Your side: the whole invested book, or one asset type of it (owner, 2026-11-13).
  const [band, setBand] = useState<Band | null>(null)
  const yourLabel = band ? COPY.yourBand(COPY.compareBand[band]) : COPY.yourPortfolio
  // What "Compare with" offers: the asset types your whole book holds today.
  const whole = useQueryCache('/api/returns?period=ALL', () => fetchReturns('ALL'))
  const compareOptions = [
    { value: 'all', label: COPY.compareAll },
    // Your investments plus what you own: offered only when you own something it would add.
    ...(whole.data?.has_owned_assets
      ? [{ value: 'all_assets', label: COPY.compareAllAssets }]
      : []),
    ...(whole.data?.bands_held ?? []).map((b) => ({
      value: b,
      label: COPY.yourBand(COPY.compareBand[b]),
    })),
  ]
  // The same ranges as Trading Activity, all read from the one whole-history series and
  // started at 0% on the range's first day (as that tab's chart is).
  const period = RANGE_OPTIONS.some((o) => o.value === range) ? range : '1Y'
  const setPeriod = onRange
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
  // chart's first day (the same 0% start), only on the days the chart draws. When your history
  // starts later than the range (a long All), your line starts on your first day instead of
  // vanishing, and its label says from when.
  const own = useQueryCache(`/api/returns?period=ALL${band ? `&band=${band}` : ''}`, () =>
    fetchReturns('ALL', undefined, { band }),
  )
  const mine = useMemo(() => {
    const all = own.data?.series ?? []
    const first = series[0]?.date
    if (!first || all.length < 2) return []
    const base = [...all].reverse().find((p) => p.date <= first) ?? all[0]!
    if (base.date > series[series.length - 1]!.date) return []
    const days = new Set(series.map((p) => p.date))
    return all
      .filter((p) => p.date >= base.date && (days.has(p.date) || p === base))
      .map((p) => ({ date: p.date, value: (1 + p.portfolio) / (1 + base.portfolio) - 1 }))
  }, [own.data, series])
  const end = series[series.length - 1]
  const mineEnd = mine[mine.length - 1]?.value
  // Your line starts after the range does: say from when, beside your figure.
  const mineFrom = mine[0] && series[0] && mine[0].date > series[0].date ? mine[0].date : null
  const tip = useCallback(
    (date: string) => {
      const p = series.find((x) => x.date === date)
      if (!p) return null
      const m = mine.find((x) => x.date === date)
      return (
        <>
          <div className="asd-chart-tip-date">{shortDate(date)}</div>
          <div className="asd-chart-tip-row">
            <span className="asd-chart-tip-k">{name}</span>
            <span className={toneOf(p.portfolio)}>{signedPct(p.portfolio)}</span>
          </div>
          {m && (
            <div className="asd-chart-tip-row">
              <span className="asd-chart-tip-k">{yourLabel}</span>
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
    [series, mine, perf.benchmark, name, yourLabel],
  )
  const ok = points.length >= 2
  const copied = win?.status === 'ok' && win.method === 'copied'
  // Overlap, spread, sectors and risk over the chart's own range and your chosen slice.
  const since = series[0]?.date ?? null
  const cmp = useQueryCache(previewCompareKey(kind, sourceRef, since, band), () =>
    fetchPreviewCompare(kind, sourceRef, since, band),
  )

  return (
    <section className="fol-sec fol-act" aria-label={COPY.vsTitle(name)}>
      <div className="fol-perf fol-panel">
        {/* Laid out like All moves: the title with its range on the left, the timeframe
            control on the right; then the three lines' figures across the card's full width,
            each under its own line style and name, so they compare at a glance. */}
        <div className="fol-sec-head">
          {/* The tab is already "You vs. …": the chart's own title says what it draws. */}
          <h2 className="fol-sec-title">{COPY.returnTitle}</h2>
        </div>
        <div className="fol-ret3">
          <div className="fol-ret3-item">
            {copied ? (
              <Tooltip label={COPY.perfLineCopiedTip}>
                <span className="fol-ret3-l" tabIndex={0}>
                  <i className="fol-line fol-line--main" aria-hidden="true" />
                  {name}
                </span>
              </Tooltip>
            ) : (
              <span className="fol-ret3-l">
                <i className="fol-line fol-line--main" aria-hidden="true" />
                {COPY.perfLineToday}
              </span>
            )}
            <span className={`fol-ret3-v ${toneOf(end?.portfolio)}`}>
              {ok ? signedPct(end?.portfolio) : '—'}
            </span>
          </div>
          {/* Your figure keeps its place while your own returns load: its name, a placeholder
              for the number, so the line never jumps when it arrives. */}
          {(mineEnd != null || own.initialLoading) && (
            <div className="fol-ret3-item">
              {mineFrom ? (
                <Tooltip label={COPY.yourSince(shortDate(mineFrom))}>
                  <span className="fol-ret3-l" tabIndex={0}>
                    <i className="fol-line fol-line--mine" aria-hidden="true" />
                    {yourLabel}
                  </span>
                </Tooltip>
              ) : (
                <span className="fol-ret3-l">
                  <i className="fol-line fol-line--mine" aria-hidden="true" />
                  {yourLabel}
                </span>
              )}
              {mineEnd != null ? (
                <span className={`fol-ret3-v ${toneOf(mineEnd)}`}>{signedPct(mineEnd)}</span>
              ) : (
                <span className="fol-ret3-v" aria-label={COPY.loadingYours}>
                  <Skeleton width={64} height="0.9em" />
                </span>
              )}
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
            label={COPY.perfChartLabelFor(copied ? name : COPY.perfLineToday)}
          />
        ) : null}
        {/* The range, under the chart it sets (owner, 2026-11-13); shared with Trading Activity. */}
        <div className="fol-tf fol-tf--under">
          {/* Whose figures you see beside theirs, then the range: the chart's two controls,
              together under it (owner, 2026-11-13). */}
          {compareOptions.length > 1 && (
            <ToolSelect
              label={COPY.compareWith}
              value={band ?? 'all'}
              options={compareOptions}
              onChange={(v) => setBand(v === 'all' ? null : (v as Band))}
            />
          )}
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
        {ok ? null : win ? (
          <p className="fol-foot">{COPY.perfUnavailable}</p>
        ) : (
          <ErrorState title={COPY.perfErrorTitle} message={COPY.perfErrorBody} onRetry={onRetry} />
        )}
      </div>
      {/* Outside the chart's card: the tables and stat cards carry their own borders. */}
      {cmp.error != null && !cmp.data ? (
        <p className="fol-foot">{COPY.vsUnavailable}</p>
      ) : (
        <YouVs
          data={cmp.data}
          name={name}
          yourLabel={yourLabel}
          ownedAssets={band === 'all_assets'}
        />
      )}
    </section>
  )
}
