import { useEffect, useRef, useState } from 'react'
import {
  AreaSeries,
  LineSeries,
  HistogramSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  LineStyle,
  type SeriesMarker,
  type Time,
} from 'lightweight-charts'
import { useTheme } from '../../../theme'
import { formatAxisPrice, hexAlpha, niceTicks, toTime } from '../assetPriceChartLwc'

export const CHART_HEIGHT = 200
const BARS_HEIGHT = 80
const compact = (n: number) =>
  new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(n)

export interface ChartColors {
  good: string
  bad: string
  accent: string
  faint: string
}

interface Props {
  points: { date: string; value: number }[]
  markers: (c: ChartColors) => SeriesMarker<Time>[]
  /** A dashed reference line with its axis label, e.g. the price on the day it was filed. */
  refLine?: { value: number; title: string }
  /** Values are returns (0.12 = 12%), shown as percent on the axis. */
  percent?: boolean
  /** A second pane of bars under the line: each move's size, green for a buy, red for a sale. */
  bars?: { date: string; value: number; up: boolean }[]
  /** A second, dashed line to compare against (the benchmark), on the same scale. */
  compare?: { date: string; value: number }[]
  /** Show only this span (the line keeps its full history behind it). */
  range?: { from: string; to: string }
  label: string
}

/** One neutral mountain with markers on it, on the same chart engine and theme as Intel's
 *  price chart (see TechnicalsMiniChart). */
export function ActivityChart({
  points,
  markers,
  refLine,
  percent,
  bars,
  compare,
  range,
  label,
}: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const { resolved: themeName } = useTheme()
  // The axis is drawn INSIDE the plot, as on Intel's price chart: no gutter pushing it in.
  const [ticks, setTicks] = useState<{ y: number; label: string }[]>([])
  const [refTag, setRefTag] = useState<{ y: number; label: string } | null>(null)
  const fmt = (n: number) =>
    percent ? `${n >= 0 ? '+' : '−'}${Math.abs(n * 100).toFixed(0)}%` : formatAxisPrice(n, 2)

  useEffect(() => {
    const el = hostRef.current
    if (!el || points.length < 2) return
    const css = getComputedStyle(document.documentElement)
    const v = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback
    const colors: ChartColors = {
      good: v('--good', '#2a7a4b'),
      bad: v('--bad', '#b33'),
      accent: v('--accent', '#4a90d9'),
      faint: v('--faint', '#999'),
    }
    // autoSize follows the box both ways; a fixed width made a narrowing page clip the chart.
    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: colors.faint,
        // The chart draws on a canvas: it takes the type size from the container's text token.
        fontSize: parseFloat(getComputedStyle(el).fontSize) || undefined,
        attributionLogo: false,
      },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      rightPriceScale: { visible: false, borderVisible: false },
      // The line runs to the right edge: the axis labels sit inside the plot, nothing needs room.
      timeScale: { borderVisible: false, fixLeftEdge: true, fixRightEdge: true, rightOffset: 0 },
      crosshair: { mode: CrosshairMode.Magnet },
      handleScroll: false,
      handleScale: false,
    })
    const line = chart.addSeries(AreaSeries, {
      lineColor: colors.accent,
      topColor: hexAlpha(colors.accent, 0.22),
      bottomColor: hexAlpha(colors.accent, 0.02),
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      ...(percent
        ? {
            priceFormat: {
              type: 'custom' as const,
              minMove: 0.0001,
              formatter: (n: number) => `${n >= 0 ? '+' : '−'}${Math.abs(n * 100).toFixed(1)}%`,
            },
          }
        : {}),
    })
    line.setData(points.map((p) => ({ time: toTime(p.date) as Time, value: p.value })))
    if (compare?.length) {
      const bench = chart.addSeries(LineSeries, {
        color: colors.faint,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        priceScaleId: 'right',
      })
      bench.setData(compare.map((p) => ({ time: toTime(p.date) as Time, value: p.value })))
    }
    if (refLine)
      line.createPriceLine({
        price: refLine.value,
        color: colors.faint,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: false,
        title: '',
      })
    createSeriesMarkers(line, markers(colors))
    if (bars?.length) {
      const hist = chart.addSeries(
        HistogramSeries,
        {
          priceLineVisible: false,
          lastValueVisible: false,
          priceFormat: {
            type: 'custom',
            minMove: 1,
            formatter: (n: number) => compact(n),
          },
        },
        1,
      )
      hist.setData(
        [...bars]
          .sort((a, b) => a.date.localeCompare(b.date))
          .map((b) => ({
            time: toTime(b.date) as Time,
            value: Math.abs(b.value),
            color: b.up ? colors.good : colors.bad,
          })),
      )
      chart.panes()[0]?.setStretchFactor(3)
      chart.panes()[1]?.setStretchFactor(1)
    }
    const fit = () => {
      const last = points[points.length - 1]!.date
      if (range)
        chart.timeScale().setVisibleRange({
          from: toTime(range.from) as Time,
          to: toTime(range.to < last ? range.to : last) as Time,
        })
      else chart.timeScale().fitContent()
    }
    fit()
    const place = () => {
      const range = line.priceScale().getVisibleRange()
      if (!range) return
      setTicks(
        niceTicks(range.from, range.to, 4).flatMap((value) => {
          const y = line.priceToCoordinate(value)
          return y == null ? [] : [{ y, label: fmt(value) }]
        }),
      )
      const y = refLine ? line.priceToCoordinate(refLine.value) : null
      setRefTag(
        y == null || !refLine ? null : { y, label: `${refLine.title} ${fmt(refLine.value)}` },
      )
    }
    const frame = requestAnimationFrame(place)
    // A box that grows or shrinks re-fits the line to its new width, then re-places the labels.
    const resized = () => {
      fit()
      place()
    }
    chart.timeScale().subscribeSizeChange(resized)
    return () => {
      cancelAnimationFrame(frame)
      chart.timeScale().unsubscribeSizeChange(resized)
      chart.remove()
    }
  }, [points, markers, refLine, percent, bars, compare, range?.from, range?.to, themeName])

  if (points.length < 2) return null
  return (
    <div className="fol-tc-wrap" role="img" aria-label={label}>
      <div
        ref={hostRef}
        className="fol-tc"
        style={{ height: bars?.length ? CHART_HEIGHT + BARS_HEIGHT : CHART_HEIGHT }}
      />
      {ticks.map((t) => (
        <span
          key={t.label}
          className="asd-chart-axis-label"
          style={{ top: t.y }}
          aria-hidden="true"
        >
          {t.label}
        </span>
      ))}
      {refTag && (
        <span className="asd-chart-prevclose-tag" style={{ top: refTag.y }} aria-hidden="true">
          {refTag.label}
        </span>
      )}
    </div>
  )
}
