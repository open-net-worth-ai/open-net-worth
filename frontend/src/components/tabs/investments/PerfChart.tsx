import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent, PointerEvent } from 'react'
import type { ReturnSeriesPoint } from '../../../types/portfolio'
import { fDate } from '../../../utils/format'
import { fmtGap, fmtReturn, nearestIndex } from './perfChartFormat'

// SVG line chart of 0-based cumulative return fractions (portfolio + optional benchmark),
// with a crosshair that snaps to the nearest point and a tooltip reading THAT point's own
// date, both returns and their gap. Mouse hovers, touch taps/drags (vertical drags still
// scroll), the keyboard steps with ←/→ (Home/End jump, Esc closes).
// Coordinate math: viewBox 600×200, 10px pad. Fractions display as % (0.15 → +15.0%).

const W = 600
const H = 200
const PAD = 10

function xAt(i: number, N: number): number {
  return PAD + (N > 1 ? i / (N - 1) : 0) * (W - 2 * PAD)
}

function yAt(v: number, min: number, rng: number): number {
  return H - PAD - ((v - min) / rng) * (H - 2 * PAD)
}

function toPath(arr: number[], min: number, rng: number): string {
  return arr
    .map(
      (v, i) => (i ? 'L' : 'M') + xAt(i, arr.length).toFixed(1) + ' ' + yAt(v, min, rng).toFixed(1),
    )
    .join(' ')
}

interface PerfChartProps {
  series: ReturnSeriesPoint[]
  hasBenchmark: boolean
  benchLabel: string
  /** Name the main line in the tooltip (default "Portfolio"). */
  mainLabel?: string
  /** Told which point the pointer is on (null when it leaves), so a headline can follow it. */
  onHover?: (index: number | null) => void
}

export function PerfChart({
  series,
  hasBenchmark,
  benchLabel,
  mainLabel,
  onHover,
}: PerfChartProps) {
  const [hi, setHiState] = useState<number | null>(null)
  // Announced only when the keyboard moved the crosshair, so a hovering mouse stays quiet.
  const [fromKeys, setFromKeys] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)
  const setHi = (index: number | null) => {
    setHiState(index)
    onHover?.(index)
  }

  const port = series.map((p) => p.portfolio)
  const bench = hasBenchmark ? series.map((p) => p.benchmark ?? 0) : null
  const N = port.length
  const mainName = mainLabel ?? 'Portfolio'

  // A new series (period or benchmark change) can be shorter than the old one.
  const at = hi != null && hi < N ? hi : null

  // A tap leaves the tooltip up after the finger lifts; a tap anywhere else closes it.
  const touchOpen = useRef(false)
  useEffect(() => {
    if (at == null || !touchOpen.current) return
    const close = (e: Event) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        touchOpen.current = false
        setHiState(null)
        onHover?.(null)
      }
    }
    document.addEventListener('pointerdown', close)
    return () => document.removeEventListener('pointerdown', close)
  }, [at, onHover])

  const allValues = bench ? [...port, ...bench, 0] : [...port, 0]
  const minV = Math.min(...allValues)
  const maxV = Math.max(...allValues)
  const rng = maxV - minV || 1

  const portPath = toPath(port, minV, rng)

  function indexFromPointer(e: PointerEvent<HTMLDivElement>): number {
    const r = e.currentTarget.getBoundingClientRect()
    return nearestIndex(r.width ? (e.clientX - r.left) / r.width : NaN, N)
  }

  function onPointerDown(e: PointerEvent<HTMLDivElement>) {
    setFromKeys(false)
    touchOpen.current = e.pointerType !== 'mouse'
    setHi(indexFromPointer(e))
  }

  function onPointerMove(e: PointerEvent<HTMLDivElement>) {
    // A touch only scrubs while the finger is down; a mouse or pen hovers.
    if (e.pointerType === 'touch' && e.buttons === 0) return
    setFromKeys(false)
    setHi(indexFromPointer(e))
  }

  function onPointerLeave(e: PointerEvent<HTMLDivElement>) {
    if (e.pointerType === 'mouse') setHi(null)
  }

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (N === 0) return
    let next: number | null
    if (e.key === 'ArrowLeft') next = at == null ? N - 1 : Math.max(0, at - 1)
    else if (e.key === 'ArrowRight') next = at == null ? N - 1 : Math.min(N - 1, at + 1)
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = N - 1
    else if (e.key === 'Escape' && at != null) next = null
    else return
    e.preventDefault()
    setFromKeys(true)
    setHi(next)
  }

  const xPct = (i: number) => (xAt(i, N) / W) * 100
  const yPct = (v: number) => (yAt(v, minV, rng) / H) * 100
  const tipLeft = at != null ? Math.min(88, Math.max(12, xPct(at))) : 0

  const y0 = yAt(0, minV, rng)

  const point = at != null ? series[at] : undefined
  const pVal = point?.portfolio
  // The tooltip reads the series' own benchmark, so a missing reading says so instead of 0%.
  const bVal = hasBenchmark && point && point.benchmark != null ? point.benchmark : null
  const gap = pVal != null && bVal != null ? pVal - bVal : null
  const dateText = point ? fDate(point.date, 'short', 'always') : ''
  const spoken =
    point && fromKeys
      ? [
          dateText,
          `${mainName} ${fmtReturn(pVal!)}`,
          hasBenchmark ? `${benchLabel} ${bVal != null ? fmtReturn(bVal) : 'no data'}` : null,
          gap != null ? `difference ${fmtGap(gap)}` : null,
        ]
          .filter(Boolean)
          .join(', ')
      : ''

  return (
    <div
      ref={wrapRef}
      className="mk-chart-wrap"
      tabIndex={N > 0 ? 0 : -1}
      role="group"
      aria-roledescription="chart"
      aria-label={`Cumulative return by date${hasBenchmark ? `, ${mainName.toLowerCase()} against ${benchLabel}` : ''}. Use the left and right arrow keys to read each date.`}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerLeave={onPointerLeave}
      onKeyDown={onKeyDown}
      onBlur={() => {
        if (!touchOpen.current) setHi(null)
      }}
    >
      <svg
        className="mk-chart"
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <defs>
          <linearGradient id="perf-grad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="var(--good)" stopOpacity="0.2" />
            <stop offset="1" stopColor="var(--good)" stopOpacity="0" />
          </linearGradient>
        </defs>
        {/* Area fill under the portfolio line */}
        <path
          d={portPath + ` L ${W - PAD} ${H - PAD} L ${PAD} ${H - PAD} Z`}
          fill="url(#perf-grad)"
        />
        {/* 0% baseline rule */}
        <line
          x1={PAD}
          x2={W - PAD}
          y1={y0}
          y2={y0}
          stroke="var(--line-strong)"
          strokeWidth="1"
          strokeDasharray="3 3"
          opacity="0.7"
          vectorEffect="non-scaling-stroke"
        />
        {/* Benchmark (dashed) */}
        {bench && (
          <path
            d={toPath(bench, minV, rng)}
            fill="none"
            stroke="var(--muted)"
            strokeWidth="1.4"
            strokeDasharray="4 4"
            opacity="0.7"
            vectorEffect="non-scaling-stroke"
          />
        )}
        {/* Portfolio line */}
        <path
          d={portPath}
          fill="none"
          stroke="var(--good)"
          strokeWidth="2"
          vectorEffect="non-scaling-stroke"
        />
        {/* Crosshair */}
        {at != null && (
          <line
            x1={xAt(at, N)}
            x2={xAt(at, N)}
            y1={PAD}
            y2={H - PAD}
            stroke="var(--muted)"
            strokeWidth="1"
            opacity="0.45"
            vectorEffect="non-scaling-stroke"
          />
        )}
      </svg>

      {/* 0% axis label */}
      <span className="mk-chart-axis" style={{ top: yPct(0) + '%' }}>
        0%
      </span>

      {/* Tooltip */}
      {at != null && pVal != null && (
        <>
          <span
            className="mk-chart-dot good"
            style={{ left: xPct(at) + '%', top: yPct(pVal) + '%' }}
          />
          {bVal != null && (
            <span
              className="mk-chart-dot mut"
              style={{ left: xPct(at) + '%', top: yPct(bVal) + '%' }}
            />
          )}
          <div className="mk-chart-tip" style={{ left: tipLeft + '%' }} aria-hidden="true">
            <div className="mk-chart-tip-d">{dateText}</div>
            <div className="mk-chart-tip-r">
              <i className="good" />
              <span>{mainName}</span>
              <b className={(pVal >= 0 ? 'pos' : 'neg') + ' money'}>{fmtReturn(pVal)}</b>
            </div>
            {hasBenchmark && (
              <div className="mk-chart-tip-r">
                <i className="mut" />
                <span>{benchLabel}</span>
                {bVal != null ? (
                  <b className={bVal >= 0 ? 'pos' : 'neg'}>{fmtReturn(bVal)}</b>
                ) : (
                  <b>—</b>
                )}
              </div>
            )}
            {gap != null && (
              <div className="mk-chart-tip-r mk-chart-tip-gap">
                <span>Difference</span>
                <b className={gap >= 0 ? 'pos' : 'neg'}>{fmtGap(gap)}</b>
              </div>
            )}
          </div>
        </>
      )}
      <span className="sr-only" aria-live="polite">
        {spoken}
      </span>
    </div>
  )
}
