import { useCallback, useMemo } from 'react'
import { Skeleton } from '../../ui'
import { ActivityChart, CHART_HEIGHT, type ChartColors } from './ActivityChart'
import { tradeMarkers } from './tradeMarkers'
import type { ActivityRow, ChartTrade } from './activityRows'
import { usePriceHistory } from './useFollowing'
import { evenSpacing } from './evenSpacing'
import { shortDate } from './following.format'

interface Props {
  row: ActivityRow
  /** Every move in this stock (B / S markers). */
  trades: ChartTrade[]
  bars?: { date: string; value: number; up: boolean }[]
  /** The first day worth drawing: where the portfolio's own history starts. */
  from?: string
}

/** One stock over the past year: every move they made in it, and the price on the day this one
 *  became public (the dashed line). */
export function TradeChart({ row, trades, bars, from }: Props) {
  const hist = usePriceHistory(row.symbol)
  const all = hist.data?.points
  const pts = useMemo(
    () => evenSpacing(from ? (all ?? []).filter((p) => p.date >= from) : (all ?? [])),
    [all, from],
  )
  const points = useMemo(() => (pts ?? []).map((p) => ({ date: p.date, value: p.price })), [pts])
  const filed = useMemo(
    () => [...(pts ?? [])].reverse().find((p) => p.date <= row.filedAt) ?? pts?.[0],
    [pts, row.filedAt],
  )
  const refLine = useMemo(
    () => (filed ? { value: filed.price, title: 'Filed' } : undefined),
    [filed],
  )
  const markers = useCallback(
    (c: ChartColors) =>
      tradeMarkers(row, pts ?? [], trades, { buy: c.good, sell: c.bad, filed: c.accent }),
    [row, pts, trades],
  )
  // Each move lands on the last price point on or before its day, the same spot as its marker.
  const tip = useCallback(
    (date: string) => {
      const p = pts.find((x) => x.date === date)
      const snap = (d: string) => [...pts].reverse().find((x) => x.date <= d)?.date
      const here = trades.filter((t) => snap(t.date) === date)
      return (
        <>
          <div className="asd-chart-tip-date">{shortDate(date)}</div>
          {p && (
            <div className="asd-chart-tip-row">
              <span className="asd-chart-tip-k">{row.symbol}</span>
              <span>${p.price.toFixed(2)}</span>
            </div>
          )}
          {here.map((t, i) => (
            <div key={i} className="fol-tip-move">
              <span className={t.up ? 'up' : 'down'}>{t.up ? 'Bought' : 'Sold'}</span>
              <span className="fol-tip-why">
                {t.during
                  ? `${t.during}, disclosed ${shortDate(t.date)}`
                  : `traded ${shortDate(t.date)}`}
              </span>
            </div>
          ))}
        </>
      )
    },
    [pts, trades, row.symbol],
  )
  if (hist.initialLoading) return <Skeleton height={CHART_HEIGHT} />
  return (
    <ActivityChart
      points={points}
      markers={markers}
      refLine={refLine}
      bars={bars}
      tip={tip}
      label={`${row.symbol} price over the past year`}
    />
  )
}
