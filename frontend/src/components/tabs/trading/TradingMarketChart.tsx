import { useEffect, useMemo, useRef, useState } from 'react'
import {
  createSeriesMarkers,
  type CandlestickData,
  type LineData,
  type SeriesMarker,
  type Time,
} from 'lightweight-charts'
import { buildChart, toTime } from '../../intel/assetPriceChartLwc'
import { useTheme } from '../../../theme'
import type {
  EvidenceSelection,
  TradingFill,
  TradingObservation,
  TradingOrder,
} from '../../../types/trading'
import { Button, Cluster, EmptyState, Select, Stack, Text } from '../../ui'
import { tradingMoney, tradingTime } from './presentation'

const keyOf = (item: TradingObservation['instrument']) =>
  `${item.venue}:${item.asset_class}:${item.symbol}:${item.currency}`
export function TradingMarketChart({
  observations,
  orders,
  fills,
  highlightedFillIds = [],
  onSelect,
}: {
  observations: TradingObservation[]
  orders: TradingOrder[]
  fills: TradingFill[]
  highlightedFillIds?: string[]
  onSelect: (selection: EvidenceSelection) => void
}) {
  const [selectedKey, setSelectedKey] = useState('')
  const instruments = [
    ...new Map(observations.map((item) => [keyOf(item.instrument), item.instrument])).entries(),
  ]
  const key = instruments.some(([id]) => id === selectedKey)
    ? selectedKey
    : (instruments[0]?.[0] ?? '')
  const rows = useMemo(
    () => [
      ...new Map(
        observations
          .filter((item) => keyOf(item.instrument) === key)
          .sort((a, b) => a.timestamp.localeCompare(b.timestamp))
          .map((item) => [toTime(item.timestamp), item]),
      ).values(),
    ],
    [observations, key],
  )
  const bars = useMemo(
    () =>
      rows.filter(
        (row) => row.open != null && row.high != null && row.low != null && row.close != null,
      ),
    [rows],
  )
  const hasCandles = bars.length > 0
  const plottedRows = hasCandles ? bars : rows
  const linkedFills = useMemo(
    () =>
      fills.flatMap((fill) => {
        const order = orders.find((item) => item.id === fill.order_id)
        return order && keyOf(order.instrument) === key ? [{ fill, order }] : []
      }),
    [fills, orders, key],
  )
  const root = useRef<HTMLDivElement>(null)
  const theme = useTheme()
  const [chartError, setChartError] = useState('')
  useEffect(() => {
    const container = root.current
    if (!container || !rows.length) return
    let dispose = () => {}
    let frame = 0
    try {
      const style = getComputedStyle(document.documentElement)
      const css = (name: string) => style.getPropertyValue(name).trim()
      const handle = buildChart(
        container,
        hasCandles ? 'candle' : 'line',
        {
          goodColor: css('--good'),
          badColor: css('--bad'),
          mutedColor: css('--muted'),
          gridColor: css('--line'),
          lineColor: css('--primary'),
          timeVisible: true,
          fontFamily: style.fontFamily,
          timezone: 'UTC',
        },
        true,
      )
      dispose = () => handle.chart.remove()
      const points = plottedRows.map((row) =>
        hasCandles
          ? ({
              time: toTime(row.timestamp),
              open: Number(row.open),
              high: Number(row.high),
              low: Number(row.low),
              close: Number(row.close),
            } as CandlestickData)
          : ({ time: toTime(row.timestamp), value: Number(row.price) } as LineData),
      )
      handle.mainSeries.setData(points)
      handle.volumeSeries.setData(
        plottedRows
          .filter((row) => row.volume != null)
          .map((row) => ({
            time: toTime(row.timestamp),
            value: Number(row.volume),
            color:
              Number(row.close ?? row.price) >= Number(row.open ?? row.price)
                ? css('--good')
                : css('--bad'),
          })),
      )
      const markers: SeriesMarker<Time>[] = linkedFills
        .flatMap(({ fill, order }) => {
          const timestamp = new Date(fill.timestamp).getTime()
          const nearest = plottedRows
            .filter((row) => new Date(row.timestamp).getTime() <= timestamp)
            .at(-1)
          return nearest
            ? [
                {
                  id: fill.id,
                  time: toTime(nearest.timestamp),
                  position: order.side === 'buy' ? ('belowBar' as const) : ('aboveBar' as const),
                  color: css(order.side === 'buy' ? '--good' : '--bad'),
                  shape: order.side === 'buy' ? ('arrowUp' as const) : ('arrowDown' as const),
                  text: `${highlightedFillIds.includes(fill.id) ? 'Selected · ' : ''}${order.side} ${fill.quantity}`,
                },
              ]
            : []
        })
        .sort((a, b) => Number(a.time) - Number(b.time))
      createSeriesMarkers(handle.mainSeries, markers)
      handle.chart.subscribeClick((event) => {
        const id = event.hoveredObjectId
        if (typeof id === 'string' && fills.some((fill) => fill.id === id))
          onSelect({ kind: 'fill', id })
      })
      handle.chart.timeScale().fitContent()
      frame = requestAnimationFrame(() => setChartError(''))
    } catch {
      dispose()
      dispose = () => {}
      frame = requestAnimationFrame(() =>
        setChartError(
          'The chart could not be rendered. Recorded data and fills remain available below.',
        ),
      )
    }
    return () => {
      cancelAnimationFrame(frame)
      dispose()
    }
  }, [plottedRows, rows, hasCandles, theme, linkedFills, fills, onSelect, highlightedFillIds])
  if (!rows.length)
    return (
      <EmptyState
        title="No market data received yet"
        body="Source status and historical availability must be established before prices or candles can be shown."
      />
    )
  const latest = rows.at(-1)!
  return (
    <Stack gap="--space-4">
      <Cluster justify="between">
        <Select
          aria-label="Chart instrument"
          value={key}
          onChange={(e) => setSelectedKey(e.target.value)}
          options={instruments.map(([id, instrument]) => ({
            value: id,
            label: `${instrument.symbol} · ${instrument.venue}`,
          }))}
        />
        <Text weight={600}>{tradingMoney(latest.price, latest.instrument.currency)}</Text>
      </Cluster>
      <Text size="xs" tone="muted">
        {hasCandles
          ? `OHLCV candles · ${bars.length} recorded bars`
          : 'Quote series · OHLCV unavailable'}{' '}
        · {latest.interval ?? 'Interval not supplied'} · {latest.source ?? 'Source not recorded'} ·
        UTC
      </Text>
      <div
        ref={root}
        className="trading-market-chart"
        role="img"
        aria-label={`${latest.instrument.symbol} ${hasCandles ? 'candlestick and volume' : 'quote'} chart`}
      />
      {chartError && <Text tone="warn">{chartError}</Text>}
      <Text size="xs" tone="muted">
        Observed {tradingTime(latest.timestamp)} · Available {tradingTime(latest.available_at)} ·
        Received {tradingTime(latest.received_at)} ·{' '}
        {latest.source_version ?? 'Source version not recorded'}
      </Text>
      <Cluster>
        {linkedFills.map(({ fill, order }) => (
          <Button
            key={fill.id}
            size="sm"
            variant="ghost"
            onClick={() => onSelect({ kind: 'fill', id: fill.id })}
          >
            {order.side === 'buy' ? 'Buy' : 'Sell'} {String(fill.quantity)}{' '}
            {order.instrument.symbol} @ {tradingMoney(fill.price, order.instrument.currency)}
          </Button>
        ))}
      </Cluster>
      {hasCandles && rows.length > bars.length && (
        <Text size="xs" tone="muted">
          {rows.length - bars.length} quote updates are retained in recorded data. They are not
          converted into candles. Missing intervals remain gaps.
        </Text>
      )}
      <details>
        <summary>Recorded market data</summary>
        <div className="trading-table-wrap">
          <table className="trading-table">
            <thead>
              <tr>
                <th>Time (UTC)</th>
                <th>Open</th>
                <th>High</th>
                <th>Low</th>
                <th>Close / quote</th>
                <th>Volume</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id ?? row.timestamp}>
                  <td>{new Date(row.timestamp).toISOString()}</td>
                  <td>{row.open ?? 'Unavailable'}</td>
                  <td>{row.high ?? 'Unavailable'}</td>
                  <td>{row.low ?? 'Unavailable'}</td>
                  <td>{row.close ?? row.price}</td>
                  <td>{row.volume ?? 'Unavailable'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </Stack>
  )
}
