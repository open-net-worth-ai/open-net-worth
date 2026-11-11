import { useEffect, useRef, useState, type ReactNode } from 'react'
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
import { formatAxisPrice, hexAlpha, niceTicks, tipPlacement, toTime } from '../assetPriceChartLwc'

export const CHART_HEIGHT = 200
const BARS_HEIGHT = 80
const LABEL_GAP_PX = 60
// A label is centred on its arrow: this close to an edge it would be cut off.
const EDGE_PX = 28
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
  /** What the hover tooltip says for a day; it floats over the plot, so the layout never moves. */
  tip?: (date: string) => ReactNode
  label: string
}

const isoOf = (t: Time) =>
  typeof t === 'number'
    ? new Date(t * 1000).toISOString().slice(0, 10)
    : typeof t === 'string'
      ? t
      : `${t.year}-${String(t.month).padStart(2, '0')}-${String(t.day).padStart(2, '0')}`

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
  tip,
  label,
}: Props) {
  // The chart is built once per data change; it reads the newest tooltip through this ref.
  const tipRef = useRef(tip)
  useEffect(() => {
    tipRef.current = tip
  }, [tip])
  const [hover, setHover] = useState<{
    date: string
    x: number
    y: number
    w: number
    h: number
  } | null>(null)
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
      // The tooltip says the day and the figures: the engine's own axis labels would repeat it.
      crosshair: {
        mode: CrosshairMode.Magnet,
        vertLine: { labelVisible: false },
        horzLine: { visible: false, labelVisible: false },
      },
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
    const markerSet = createSeriesMarkers(line, [])
    // Labels that would collide keep their arrow and lose their words (the tooltip still names
    // every move): measured in pixels on the chart as drawn, so a phone shows fewer.
    const label = () => {
      const all = markers(colors)
      const kept: { x: number; pos: string | undefined }[] = []
      markerSet.setMarkers(
        all.map((m) => {
          const x = chart.timeScale().timeToCoordinate(m.time)
          if (x == null || !m.text) return m
          if (x < EDGE_PX || x > el.clientWidth - EDGE_PX) return { ...m, text: '' }
          if (kept.some((k) => k.pos === m.position && Math.abs(k.x - x) < LABEL_GAP_PX))
            return { ...m, text: '' }
          kept.push({ x, pos: m.position })
          return m
        }),
      )
    }
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
    label()
    const place = () => {
      label()
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
    chart.subscribeCrosshairMove((p) => {
      if (!tipRef.current || p.point == null || p.time == null) return setHover(null)
      setHover({
        date: isoOf(p.time),
        x: p.point.x,
        y: p.point.y,
        w: el.clientWidth,
        h: el.clientHeight,
      })
    })
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
      {hover && tip && (
        <div className="asd-chart-tip" style={tipPlacement(hover.x, hover.y, hover.w, hover.h)}>
          {tip(hover.date)}
        </div>
      )}
      {refTag && (
        <span className="asd-chart-prevclose-tag" style={{ top: refTag.y }} aria-hidden="true">
          {refTag.label}
        </span>
      )}
    </div>
  )
}
