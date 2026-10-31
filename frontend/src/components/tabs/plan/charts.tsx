// plan/charts.tsx — hand-built SVG charts (trajectory area + Monte-Carlo fan), interactive.
// Verbatim port of design_handoffs/plan_handoff/prototype/plan/charts.jsx.
// Every prop, SVG path, marker, label, and interaction is reproduced exactly.

import { useState, useRef, useLayoutEffect } from 'react'
import { fBase, fBaseCompact } from '../../../utils/format'
import type { BandRow } from './planData'

const VW = 760 // svg viewBox width (preserveAspectRatio none → x maps linearly to container)
// Vertical px the end-of-line value label needs clear of a y-axis tick before the boxes are
// measured: about half the label's height (10px text + 2px padding) plus half a tick's height
// (8.5px text), with a little buffer. FanChart replaces it with the measured figure.
const END_LABEL_CLEAR_PX = 14

// Shared hover overlay: maps pointer X → nearest data index, draws a crosshair + tooltip.
export function useHover(points: unknown[]) {
  const ref = useRef<HTMLDivElement>(null)
  const [hi, setHi] = useState<number | null>(null)
  function onMove(e: React.MouseEvent) {
    const el = ref.current
    if (!el) return
    const r = el.getBoundingClientRect()
    const frac = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width))
    setHi(Math.round(frac * (points.length - 1)))
  }
  return { ref, hi, onMove, onLeave: () => setHi(null) }
}

// ── Fan chart: p10–p90 bands + median line, optional retirement marker ──
export interface FanChartProps {
  bands: BandRow[]
  height?: number
  retireAge?: number | null
  showDry?: boolean
  depletionPct?: number
  color?: string
  overlay?: BandRow[] | null
  fireNumber?: number | null
  nestEggDist?: BandRow | null
  fragile?: boolean
  onRetireChange?: ((age: number) => void) | null
  retireMin?: number | null
  retireMax?: number | null
  events?: Array<{ id: string; age: number; label: string; amount: number }> | null
  onEventDrag?: ((id: string, age: number) => void) | null
  onEventRemove?: ((id: string) => void) | null
  safeStopAge?: number | null
  shocks?: Array<{
    key: string
    kind: 'point' | 'span'
    age?: number
    from?: number
    to?: number
    label: string
    color?: string
  }> | null
  incomeStreams?: Array<{
    amount: number
    age: number
    label: string
    endAge?: number | null
  }> | null
  focusLayer?: string | null
}

// Shared chart marker dot — filled core + white (surface) ring + faint colored halo. Rendered as
// an HTML overlay, NOT an SVG <circle>: the chart's viewBox uses preserveAspectRatio="none" (x is
// stretched to the container), so an SVG circle would render as an ellipse. leftPct = x/W*100, top
// = the yAt() value in px (the container height equals the H coordinate, so y is 1:1).
function MarkerDot({ leftPct, top, color }: { leftPct: number; top: number; color: string }) {
  return (
    <span
      style={{
        position: 'absolute',
        left: leftPct + '%',
        top: top + 'px',
        transform: 'translate(-50%, -50%)',
        width: 8,
        height: 8,
        borderRadius: '50%',
        background: color,
        border: '1.5px solid var(--surface)',
        boxShadow: `0 0 0 1.2px color-mix(in srgb, ${color} 55%, transparent)`,
        pointerEvents: 'none',
        zIndex: 6,
      }}
    />
  )
}

export function FanChart({
  bands,
  height = 210,
  retireAge = null,
  color = 'var(--good)',
  overlay = null,
  fireNumber = null,
  nestEggDist = null,
  fragile = false,
  onRetireChange = null,
  retireMin = null,
  retireMax = null,
  events = null,
  onEventDrag = null,
  onEventRemove = null,
  safeStopAge = null,
  shocks = null,
  incomeStreams = null,
  focusLayer = null,
}: FanChartProps) {
  const W = VW,
    H = height,
    padL = 8,
    padR = 8,
    padT = 14,
    padB = 22
  const maxY =
    Math.max(
      ...bands.map((b) => b.p90),
      ...(overlay ? overlay.map((b) => b.p50) : [0]),
      fireNumber ? fireNumber * 1.04 : 0,
    ) * 1.04
  const minAge = bands[0]!.age,
    maxAge = bands[bands.length - 1]!.age
  const xAt = (age: number) => padL + ((age - minAge) / (maxAge - minAge)) * (W - padL - padR)
  const yAt = (v: number) => padT + (1 - v / maxY) * (H - padT - padB)
  const bandPath = (loKey: keyof BandRow, hiKey: keyof BandRow) => {
    const up = bands.map((b) => `${xAt(b.age).toFixed(1)} ${yAt(b[hiKey] as number).toFixed(1)}`)
    const down = [...bands]
      .reverse()
      .map((b) => `${xAt(b.age).toFixed(1)} ${yAt(b[loKey] as number).toFixed(1)}`)
    return 'M' + up.join(' L ') + ' L ' + down.join(' L ') + ' Z'
  }
  const medLine = bands
    .map((b, i) => (i ? 'L' : 'M') + xAt(b.age).toFixed(1) + ' ' + yAt(b.p50).toFixed(1))
    .join(' ')
  const ovLine = overlay
    ? overlay
        .map((b, i) => (i ? 'L' : 'M') + xAt(b.age).toFixed(1) + ' ' + yAt(b.p50).toFixed(1))
        .join(' ')
    : null
  const retInRange = retireAge != null && retireAge >= minAge && retireAge <= maxAge
  const retX = retInRange ? xAt(retireAge!) : null
  const fireY = fireNumber != null ? yAt(fireNumber) : null
  // age where the TYPICAL (p50) path first reaches the FIRE number — the financial-independence milestone
  let fireCrossAge: number | null = null
  if (fireNumber != null) {
    for (let i = 1; i < bands.length; i++) {
      if (bands[i]!.p50 >= fireNumber && bands[i - 1]!.p50 < fireNumber) {
        const t = (fireNumber - bands[i - 1]!.p50) / (bands[i]!.p50 - bands[i - 1]!.p50)
        fireCrossAge = bands[i - 1]!.age + t * (bands[i]!.age - bands[i - 1]!.age)
        break
      }
    }
  }
  const fireCrossX = fireCrossAge != null ? xAt(fireCrossAge) : null
  // age where the TYPICAL (p50) path first RUNS DRY — the median depletes to ~0 (paths are
  // floored at 0 in the engine). Only marked if it happens within the plotted range.
  let dryAge: number | null = null
  for (let i = 1; i < bands.length; i++) {
    if (bands[i]!.p50 <= 0 && bands[i - 1]!.p50 > 0) {
      dryAge = bands[i]!.age
      break
    }
  }
  const dryX = dryAge != null ? xAt(dryAge) : null
  const safeInRange = safeStopAge != null && safeStopAge >= minAge && safeStopAge <= maxAge
  const safeX = safeInRange ? xAt(safeStopAge!) : null
  // when the safe-stop marker sits within ~4 yrs of the retire pill, drop its label to a 2nd row so they don't overlap
  const safeLabelLow = safeX != null && retX != null && Math.abs(safeStopAge! - retireAge!) <= 4
  // fragile-decade window: ±5 yrs around the retirement line (clamped to the plotted range)
  const fragLo = fragile && retInRange ? Math.max(minAge, retireAge! - 5) : null
  const fragHi = fragile && retInRange ? Math.min(maxAge, retireAge! + 5) : null
  const dist = retInRange ? nestEggDist : null
  const { ref, hi, onMove, onLeave } = useHover(bands)
  const endLabelY = yAt(bands[bands.length - 1]!.p50)
  // The end label and a y tick overlap when their centres are closer than half of each box's
  // height. Measured from the rendered boxes so a font/padding change or browser text zoom keeps
  // it right; END_LABEL_CLEAR_PX stands in until the first layout (and where nothing lays out).
  const endLabelRef = useRef<HTMLDivElement | null>(null)
  const yTickRef = useRef<HTMLDivElement | null>(null)
  const [endLabelClear, setEndLabelClear] = useState(END_LABEL_CLEAR_PX)
  useLayoutEffect(() => {
    const labelH = endLabelRef.current?.getBoundingClientRect().height ?? 0
    const tick = yTickRef.current
    const tickH = tick?.isConnected ? tick.getBoundingClientRect().height : 0
    if (!labelH || !tickH) return
    const clear = Math.ceil((labelH + tickH) / 2) + 2
    setEndLabelClear((prev) => (prev === clear ? prev : clear))
  })
  const [drag, setDrag] = useState(false)
  const chipRefs = useRef<Record<string, HTMLDivElement | null>>({})
  const [chipShift, setChipShift] = useState<Record<string, number>>({})
  const [chipRow, setChipRow] = useState<Record<string, number>>({})
  const draggable = typeof onRetireChange === 'function' && retInRange
  function ageAtClientX(clientX: number) {
    const el = ref.current
    if (!el) return retireAge!
    const r = el.getBoundingClientRect()
    const frac = Math.max(0, Math.min(1, (clientX - r.left) / r.width))
    return Math.round(minAge + frac * (maxAge - minAge))
  }
  function clampRet(a: number) {
    const lo = retireMin != null ? retireMin : minAge + 1
    const hi = retireMax != null ? retireMax : maxAge - 1
    return Math.max(lo, Math.min(hi, a))
  }
  function onGripDown(e: React.MouseEvent) {
    if (typeof onRetireChange !== 'function') return
    e.preventDefault()
    e.stopPropagation()
    setDrag(true)
    document.body.style.cursor = 'ew-resize'
    const move = (ev: MouseEvent) => onRetireChange(clampRet(ageAtClientX(ev.clientX)))
    const up = () => {
      setDrag(false)
      document.body.style.cursor = ''
      document.removeEventListener('mousemove', move)
      document.removeEventListener('mouseup', up)
    }
    document.addEventListener('mousemove', move)
    document.addEventListener('mouseup', up)
  }
  function onEventDown(id: string, e: React.MouseEvent) {
    if (typeof onEventDrag !== 'function') return
    e.preventDefault()
    e.stopPropagation()
    document.body.style.cursor = 'ew-resize'
    const move = (ev: MouseEvent) => onEventDrag(id, clampRet(ageAtClientX(ev.clientX)))
    const up = () => {
      document.body.style.cursor = ''
      document.removeEventListener('mousemove', move)
      document.removeEventListener('mouseup', up)
    }
    document.addEventListener('mousemove', move)
    document.addEventListener('mouseup', up)
  }
  // ── overlay annotations: market-shock windows + guaranteed-income onset, relevance-dimmed ──
  // Three grammars, three lanes: spans → faint background tint (behind the fan); a point shock
  // (crash) and each income onset → a marker on the bottom baseline rail. Everything sits quiet
  // by default; whichever layer the user is touching in the panel (focusLayer) brightens, the
  // rest dim — so the chart never shows all of it at full strength.
  const shockList = (shocks || []).filter((s) =>
    s.kind === 'point' ? s.age! > minAge && s.age! <= maxAge : s.to! > minAge && s.from! < maxAge,
  )
  const incomeList = (incomeStreams || []).filter(
    (s) => s.amount > 0 && s.age > minAge && s.age <= maxAge,
  )
  const focus = focusLayer || null
  const dimmed = (key: string) => focus != null && focus !== key // something ELSE is in focus
  const spanOp = (key: string) => (dimmed(key) ? 0.025 : focus === key ? 0.17 : 0.06)
  const floorOp = () => (dimmed('income') ? 0.04 : focus === 'income' ? 0.22 : 0.09)

  const hb = !drag && hi != null ? bands[hi] : null
  const hxPct = hb ? (xAt(hb.age) / W) * 100 : 0
  // stagger event flags into a TOP track so events at the same / nearby year don't hide each other.
  // row 0 is kept clear around the Retire pill; nearer-than-evGap neighbours bump to the next row down.
  const evInRange = (events || [])
    .filter((ev) => ev.age >= minAge && ev.age <= maxAge)
    .slice()
    .sort((a, b) => a.age - b.age)
  const evGap = Math.max(2, Math.round((maxAge - minAge) * 0.1))
  const evRow: Record<string, number> = {}
  {
    const laneLast: number[] = []
    for (const ev of evInRange) {
      let r =
        (retInRange && Math.abs(ev.age - retireAge!) < evGap) ||
        (safeInRange && Math.abs(ev.age - safeStopAge!) < evGap)
          ? 1
          : 0
      while (r < laneLast.length && Math.abs(ev.age - laneLast[r]!) < evGap) r++
      evRow[ev.id] = r
      laneLast[r] = ev.age
    }
  }
  // The row estimate above is age-based (evGap is a year threshold), so two chips whose ages
  // are far enough apart to share a row can still be wide enough in PIXELS to visually collide —
  // most visible while dragging, where the row only flips once the age gap crosses evGap even
  // though the boxes were already touching well before that. Once refs are mounted, re-derive
  // row placement from real measured widths instead: a pixel-based version of the same greedy
  // lane sweep, keyed on actual box overlap (+ a small buffer) rather than an age threshold.
  // The same pass also clamps each chip the minimum px needed to stay inside the wrap, since
  // .pl-panel clips overflow at its rounded corners and a chip centered near either edge of the
  // plot can otherwise get its off-screen half cut off.
  const evKey = evInRange.map((ev) => ev.id + ':' + ev.age).join(',')
  useLayoutEffect(() => {
    const wrap = ref.current
    if (!wrap) return
    function measure() {
      const containerWidth = wrap!.getBoundingClientRect().width
      if (!containerWidth) return
      const info = evInRange
        .map((ev) => ({
          ev,
          el: chipRefs.current[ev.id],
          centerPx: (xAt(ev.age) / W) * containerWidth,
        }))
        .filter((d): d is { ev: typeof d.ev; el: HTMLDivElement; centerPx: number } => !!d.el)
        .map((d) => ({ ...d, width: d.el.getBoundingClientRect().width }))
        .sort((a, b) => a.centerPx - b.centerPx)
      if (!info.length) return
      const GAP = 6 // px buffer kept between two chips sharing a row
      const laneRight: number[] = []
      const nextShift: Record<string, number> = {}
      const nextRow: Record<string, number> = {}
      for (const d of info) {
        let left = d.centerPx - d.width / 2,
          right = d.centerPx + d.width / 2
        const shift = left < 0 ? -left : right > containerWidth ? containerWidth - right : 0
        left += shift
        right += shift
        let r =
          (retInRange && Math.abs(d.ev.age - retireAge!) < evGap) ||
          (safeInRange && Math.abs(d.ev.age - safeStopAge!) < evGap)
            ? 1
            : 0
        while (r < laneRight.length && left < laneRight[r]! + GAP) r++
        laneRight[r] = right
        nextShift[d.ev.id] = shift
        nextRow[d.ev.id] = r
      }
      const changed = (next: Record<string, number>, prev: Record<string, number>) => {
        const keys = new Set([...Object.keys(prev), ...Object.keys(next)])
        for (const k of keys) if (Math.abs((next[k] ?? 0) - (prev[k] ?? 0)) > 0.5) return true
        return false
      }
      setChipShift((prev) => (changed(nextShift, prev) ? nextShift : prev))
      setChipRow((prev) => (changed(nextRow, prev) ? nextRow : prev))
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(wrap)
    return () => ro.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [evKey])
  return (
    <div
      className="pl-chart-wrap"
      ref={ref}
      onMouseMove={onMove}
      onMouseLeave={onLeave}
      style={{ cursor: 'crosshair' }}
    >
      <svg
        className="pl-chart"
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        style={{ height }}
        aria-hidden="true"
      >
        {Array.from({ length: 5 }).map((_, i) => {
          const y = padT + (i / 4) * (H - padT - padB)
          return (
            <line
              key={i}
              x1={padL}
              x2={W - padR}
              y1={y}
              y2={y}
              stroke="var(--line)"
              strokeWidth="1"
              vectorEffect="non-scaling-stroke"
              opacity="0.6"
            />
          )
        })}
        {fragLo != null && fragHi != null && (
          <rect
            x={xAt(fragLo)}
            width={Math.max(0, xAt(fragHi) - xAt(fragLo))}
            y={padT}
            height={H - padT - padB}
            fill="var(--warn)"
            opacity="0.06"
          />
        )}
        {/* market-shock WINDOWS: tinted spans behind the fan (a span is a window, not a moment — never a line) */}
        {shockList
          .filter((s) => s.kind === 'span')
          .map((s) => {
            const x0 = xAt(Math.max(minAge, s.from!)),
              x1 = xAt(Math.min(maxAge, s.to!))
            const c = s.color || 'var(--bad)'
            return (
              <g key={s.key}>
                <rect
                  x={x0}
                  width={Math.max(0, x1 - x0)}
                  y={padT}
                  height={H - padT - padB}
                  fill={c}
                  opacity={spanOp(s.key)}
                  style={{ transition: 'opacity .15s' }}
                />
                {focus === s.key && (
                  <>
                    <line
                      x1={x0}
                      x2={x0}
                      y1={padT}
                      y2={H - padB}
                      stroke={c}
                      strokeWidth="1"
                      strokeDasharray="3 3"
                      vectorEffect="non-scaling-stroke"
                      opacity="0.7"
                    />
                    <line
                      x1={x1}
                      x2={x1}
                      y1={padT}
                      y2={H - padB}
                      stroke={c}
                      strokeWidth="1"
                      strokeDasharray="3 3"
                      vectorEffect="non-scaling-stroke"
                      opacity="0.7"
                    />
                  </>
                )}
              </g>
            )
          })}
        {/* guaranteed-income ONSET: a faint "supported from here" strip hugging the baseline from the start age on */}
        {incomeList.map((s, i) => {
          const x0 = xAt(s.age)
          return (
            <rect
              key={'incf' + i}
              x={x0}
              width={Math.max(0, W - padR - x0)}
              y={H - padB - 7}
              height="7"
              fill="var(--good)"
              opacity={floorOp()}
              style={{ transition: 'opacity .15s' }}
            />
          )
        })}
        <path d={bandPath('p10', 'p90')} fill={color} opacity="0.10" />
        <path d={bandPath('p25', 'p75')} fill={color} opacity="0.16" />
        <path
          d={medLine}
          fill="none"
          stroke={color}
          strokeWidth="2"
          vectorEffect="non-scaling-stroke"
          strokeLinejoin="round"
        />
        {ovLine && (
          <path
            d={ovLine}
            fill="none"
            stroke="var(--muted)"
            strokeWidth="1.8"
            strokeDasharray="5 4"
            vectorEffect="non-scaling-stroke"
            strokeLinejoin="round"
            opacity="0.85"
          />
        )}
        {fireY != null && (
          <line
            x1={padL}
            x2={W - padR}
            y1={fireY}
            y2={fireY}
            stroke="var(--warn)"
            strokeWidth="1.5"
            strokeDasharray="5 4"
            opacity="0.8"
            vectorEffect="non-scaling-stroke"
          />
        )}
        {fireCrossX != null && fireY != null && (
          <g>
            <line
              x1={fireCrossX}
              x2={fireCrossX}
              y1={fireY}
              y2={H - padB}
              stroke="var(--warn)"
              strokeWidth="1"
              strokeDasharray="3 3"
              opacity="0.4"
              vectorEffect="non-scaling-stroke"
            />
          </g>
        )}
        {/* Typical (p50) runs dry — full-height red dashed line + a dot on the baseline (same
            circle style as the FIRE marker, red instead of amber). */}
        {dryX != null && (
          <g>
            <line
              x1={dryX}
              x2={dryX}
              y1={padT}
              y2={H - padB}
              stroke="var(--bad)"
              strokeWidth="1.3"
              strokeDasharray="4 4"
              vectorEffect="non-scaling-stroke"
              opacity="0.85"
            />
          </g>
        )}
        {safeX != null && (
          <g>
            <line
              x1={safeX}
              x2={safeX}
              y1={padT}
              y2={H - padB}
              stroke="var(--good)"
              strokeWidth="1.3"
              strokeDasharray="4 4"
              vectorEffect="non-scaling-stroke"
              opacity="0.85"
            />
            <rect
              x={safeX - 3.2}
              y={padT - 3.2}
              width="6.4"
              height="6.4"
              fill="var(--good)"
              stroke="var(--surface)"
              strokeWidth="1.4"
              transform={`rotate(45 ${safeX} ${padT})`}
            />
          </g>
        )}
        {retX != null && (
          <line
            x1={retX}
            x2={retX}
            y1={padT}
            y2={H - padB}
            stroke="var(--muted)"
            strokeWidth="1.3"
            strokeDasharray="4 4"
            vectorEffect="non-scaling-stroke"
            opacity="0.8"
          />
        )}
        {events &&
          events.map((ev) =>
            ev.age > minAge && ev.age < maxAge ? (
              <line
                key={ev.id}
                x1={xAt(ev.age)}
                x2={xAt(ev.age)}
                y1={padT + 12}
                y2={H - padB}
                stroke={ev.amount < 0 ? 'var(--bad)' : 'var(--good)'}
                strokeWidth="1"
                strokeDasharray="3 3"
                vectorEffect="non-scaling-stroke"
                opacity="0.5"
              />
            ) : null,
          )}
        {/* point shock (crash): full-height line only while it's the focused layer */}
        {shockList
          .filter((s) => s.kind === 'point' && focus === s.key)
          .map((s) => (
            <line
              key={'pl' + s.key}
              x1={xAt(s.age!)}
              x2={xAt(s.age!)}
              y1={padT}
              y2={H - padB}
              stroke={s.color || 'var(--bad)'}
              strokeWidth="1.2"
              strokeDasharray="3 3"
              vectorEffect="non-scaling-stroke"
              opacity="0.7"
            />
          ))}
        {dist != null && retX != null && (
          <g>
            <line
              x1={retX}
              x2={retX}
              y1={yAt(dist.p90)}
              y2={yAt(dist.p10)}
              stroke={color}
              strokeWidth="1.5"
              vectorEffect="non-scaling-stroke"
              opacity="0.95"
            />
            <line
              x1={retX - 4}
              x2={retX + 4}
              y1={yAt(dist.p90)}
              y2={yAt(dist.p90)}
              stroke={color}
              strokeWidth="1.5"
              vectorEffect="non-scaling-stroke"
              opacity="0.95"
            />
            <line
              x1={retX - 4}
              x2={retX + 4}
              y1={yAt(dist.p10)}
              y2={yAt(dist.p10)}
              stroke={color}
              strokeWidth="1.5"
              vectorEffect="non-scaling-stroke"
              opacity="0.95"
            />
            <rect
              x={retX - 5}
              width="10"
              y={yAt(dist.p75)}
              height={Math.max(2, yAt(dist.p25) - yAt(dist.p75))}
              fill={color}
              opacity="0.9"
            />
            <line
              x1={retX - 7}
              x2={retX + 7}
              y1={yAt(dist.p50)}
              y2={yAt(dist.p50)}
              stroke="var(--surface)"
              strokeWidth="2.2"
              vectorEffect="non-scaling-stroke"
            />
          </g>
        )}
        {hb && (
          <line
            x1={xAt(hb.age)}
            x2={xAt(hb.age)}
            y1={padT}
            y2={H - padB}
            stroke="var(--muted)"
            strokeWidth="1"
            vectorEffect="non-scaling-stroke"
            opacity="0.7"
          />
        )}
      </svg>
      {hb && (
        <span className="pl-chart-dot" style={{ left: hxPct + '%', top: yAt(hb.p50) + 'px' }} />
      )}
      {hb && (
        <div
          className="pl-chart-tip"
          style={{
            left: hxPct + '%',
            top: 32,
            transform: `translateX(${hxPct > 60 ? '-100%' : '0'})`,
          }}
        >
          <div className="d">Age {Math.round(hb.age)}</div>
          <div className="r">
            <i style={{ background: color }} />
            Typical{' '}
            <b className="mask" title={fBase(hb.p50)}>
              {fBaseCompact(hb.p50)}
            </b>
          </div>
          <div className="r">
            <i style={{ background: `color-mix(in srgb, ${color} 45%, transparent)` }} />
            Range{' '}
            <b className="mask" title={`${fBase(hb.p10)}–${fBase(hb.p90)}`}>
              {fBaseCompact(hb.p10)}–{fBaseCompact(hb.p90)}
            </b>
          </div>
          {overlay && overlay[hi!] && (
            <div className="r">
              <i style={{ background: 'var(--muted)', opacity: 0.55 }} />
              Your plan{' '}
              <b className="mask" title={fBase(overlay[hi!]!.p50)}>
                {fBaseCompact(overlay[hi!]!.p50)}
              </b>
            </div>
          )}
        </div>
      )}
      {retX != null && draggable && (
        <div
          onMouseDown={onGripDown}
          style={{
            position: 'absolute',
            left: (retX / W) * 100 + '%',
            top: 14,
            height: height - 36,
            width: 20,
            transform: 'translateX(-50%)',
            cursor: 'ew-resize',
            zIndex: 4,
          }}
        />
      )}
      {evInRange.map((ev) => {
        const evXPct = (xAt(ev.age) / W) * 100
        const shift = chipShift[ev.id] ?? 0
        const row = chipRow[ev.id] ?? evRow[ev.id]!
        return (
          <div
            key={ev.id}
            ref={(el) => {
              chipRefs.current[ev.id] = el
            }}
            className={'pl-chart-event' + (ev.amount > 0 ? ' windfall' : '')}
            style={{
              left: evXPct + '%',
              top: 4 + row * 24 + 'px',
              bottom: 'auto',
              transform: `translateX(calc(-50% + ${shift}px))`,
            }}
            onMouseDown={(e) => onEventDown(ev.id, e)}
          >
            {/* Signed amount beside the label — a S$1M purchase and a S$5k one look identical
                without it. − = one-off spend, + = windfall; masked under Hide amounts. */}
            <span className="ce-lbl">
              {ev.label} •{' '}
              <span className="mask">
                {ev.amount < 0 ? '−' : '+'}
                {fBaseCompact(Math.abs(ev.amount))}
              </span>
            </span>
            {onEventRemove && (
              <button
                className="ce-x"
                onMouseDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation()
                  onEventRemove(ev.id)
                }}
                aria-label="Remove"
              >
                <svg
                  width="10"
                  height="10"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.6"
                  strokeLinecap="round"
                >
                  <path d="M6 6l12 12M18 6L6 18" />
                </svg>
              </button>
            )}
          </div>
        )
      })}
      {/* span label (only while focused), crash point marker + income onset ticks — bottom baseline rail */}
      {shockList
        .filter((s) => s.kind === 'span' && focus === s.key)
        .map((s) => (
          <div
            key={'sl' + s.key}
            className="pl-chart-shocklbl"
            style={{ left: (xAt(Math.max(minAge, s.from!)) / W) * 100 + '%' }}
          >
            {s.label} • {Math.round(s.from!)}–{Math.round(s.to!)}
          </div>
        ))}
      {shockList
        .filter((s) => s.kind === 'point')
        .map((s) => (
          <div
            key={'sp' + s.key}
            className="pl-chart-shockpt"
            style={{ left: (xAt(s.age!) / W) * 100 + '%', opacity: dimmed(s.key) ? 0.4 : 1 }}
          >
            <span className="cs-lbl">
              {s.label} • {Math.round(s.age!)}
            </span>
          </div>
        ))}
      {incomeList.map((s, i) => (
        <div
          key={'ift' + i}
          className="pl-chart-floor"
          style={{ left: (xAt(s.age) / W) * 100 + '%', opacity: dimmed('income') ? 0.4 : 1 }}
        >
          <span className="cf-tri" />
          <span className="cf-lbl">
            {s.label} • <span className="mask">{fBaseCompact(s.amount)}/mo</span> •{' '}
            {Math.round(s.age)}
            {s.endAge != null ? `–${Math.round(s.endAge)}` : ''}
          </span>
        </div>
      ))}
      {retX != null && draggable && (
        <button
          onMouseDown={onGripDown}
          style={{
            position: 'absolute',
            left: (retX / W) * 100 + '%',
            top: 2,
            transform: 'translateX(-50%)',
            fontSize: 10,
            fontWeight: 700,
            color: drag ? 'var(--bg)' : 'var(--ink)',
            background: drag ? 'var(--ink)' : 'var(--surface)',
            border: '1px solid ' + (drag ? 'var(--ink)' : 'var(--line-strong)'),
            padding: '2px 8px',
            borderRadius: 'var(--radius-md)',
            cursor: 'ew-resize',
            zIndex: 6,
            whiteSpace: 'nowrap',
            boxShadow: '0 1px 3px rgba(0,0,0,.3)',
          }}
        >
          ⠿ Retire • {retireAge}
        </button>
      )}
      {retX != null && !draggable && !hb && (
        <div
          style={{
            position: 'absolute',
            left: (retX / W) * 100 + '%',
            top: 4,
            transform: 'translateX(-50%)',
            fontSize: 10,
            fontWeight: 700,
            color: 'var(--muted)',
            background: 'var(--surface)',
            padding: '1px 6px',
            borderRadius: 5,
            pointerEvents: 'none',
            whiteSpace: 'nowrap',
          }}
        >
          Retire • age {retireAge}
        </div>
      )}
      {dist != null && retX != null && !hb && (
        <div
          style={{
            position: 'absolute',
            left: (retX / W) * 100 + '%',
            top: yAt(dist.p10) + 'px',
            transform: `translate(${retX / W > 0.62 ? '-100%' : retX / W < 0.12 ? '0' : '-50%'}, 7px)`,
            fontSize: 9.5,
            fontWeight: 700,
            color: color,
            background: 'color-mix(in srgb, var(--surface) 92%, transparent)',
            padding: '1px 6px',
            borderRadius: 5,
            pointerEvents: 'none',
            whiteSpace: 'nowrap',
          }}
        >
          pot {fBaseCompact(dist.p10)}–{fBaseCompact(dist.p90)}
        </div>
      )}
      {fireY != null && !hb && (
        <div
          style={{
            position: 'absolute',
            left: 10,
            top: fireY + 'px',
            transform: 'translateY(-50%)',
            fontSize: 10,
            fontWeight: 700,
            color: 'var(--warn)',
            background: 'var(--surface)',
            padding: '1px 6px',
            borderRadius: 5,
            pointerEvents: 'none',
            whiteSpace: 'nowrap',
          }}
        >
          FIRE target {fBaseCompact(fireNumber!)}
        </div>
      )}
      {fireCrossX != null && fireY != null && (
        <MarkerDot leftPct={(fireCrossX / W) * 100} top={fireY} color="var(--warn)" />
      )}
      {fireCrossX != null && fireY != null && !hb && (
        <div
          style={{
            position: 'absolute',
            left: (fireCrossX / W) * 100 + '%',
            top: fireY + 'px',
            transform: `translate(${fireCrossX / W > 0.72 ? '-100%' : fireCrossX / W < 0.08 ? '0' : '-50%'}, -165%)`,
            fontSize: 10,
            fontWeight: 700,
            color: 'var(--warn)',
            background: 'var(--surface)',
            padding: '1px 6px',
            borderRadius: 5,
            pointerEvents: 'none',
            whiteSpace: 'nowrap',
            border: '1px solid color-mix(in srgb, var(--warn) 30%, transparent)',
          }}
        >
          ◆ FIRE number reached • ~{Math.round(fireCrossAge!)}
        </div>
      )}
      {dryX != null && <MarkerDot leftPct={(dryX / W) * 100} top={yAt(0)} color="var(--bad)" />}
      {/* Typical (p50) runs dry — label pill above the baseline dot (the line is drawn in the SVG). */}
      {dryX != null && !hb && (
        <div
          style={{
            position: 'absolute',
            left: (dryX / W) * 100 + '%',
            top: yAt(0) + 'px',
            transform: `translate(${dryX / W > 0.72 ? '-100%' : dryX / W < 0.08 ? '0' : '-50%'}, -185%)`,
            fontSize: 10,
            fontWeight: 700,
            color: 'var(--bad)',
            background: 'var(--surface)',
            padding: '1px 6px',
            borderRadius: 5,
            pointerEvents: 'none',
            whiteSpace: 'nowrap',
            border: '1px solid color-mix(in srgb, var(--bad) 30%, transparent)',
            zIndex: 7,
          }}
        >
          ✕ Typical runs dry • ~{Math.round(dryAge ?? 0)}
        </div>
      )}
      {safeX != null && !hb && (
        <div
          style={{
            position: 'absolute',
            left: (safeX / W) * 100 + '%',
            top: (safeLabelLow ? 26 : 4) + 'px',
            transform: `translate(${safeX / W > 0.72 ? '-100%' : safeX / W < 0.08 ? '0' : '-50%'}, 0)`,
            fontSize: 10,
            fontWeight: 700,
            color: 'var(--good)',
            background: 'var(--surface)',
            padding: '1px 6px',
            borderRadius: 5,
            pointerEvents: 'none',
            whiteSpace: 'nowrap',
            border: '1px solid color-mix(in srgb, var(--good) 30%, transparent)',
            zIndex: 7,
          }}
        >
          ◇ Safe to retire • ~{Math.round(safeStopAge!)}
        </div>
      )}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          fontSize: 10,
          color: 'var(--faint)',
          marginTop: 4,
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        <span>Age {Math.round(minAge)}</span>
        <span>Age {Math.round((minAge + maxAge) / 2)}</span>
        <span>Age {Math.round(maxAge)}</span>
      </div>
      {!hb && (
        <div
          ref={endLabelRef}
          data-testid="fan-end-label"
          style={{
            position: 'absolute',
            right: 10,
            top: endLabelY + 'px',
            transform: 'translateY(-50%)',
            fontSize: 10,
            fontWeight: 700,
            color: color,
            background: 'var(--surface)',
            padding: '1px 5px',
            borderRadius: 5,
            pointerEvents: 'none',
          }}
        >
          {fBaseCompact(bands[bands.length - 1]!.p50)}
        </div>
      )}
      {/* Y-axis money scale — overlaid at the RIGHT edge, ON TOP of the plot (does NOT reserve/push
          space). Faint text + a translucent chip so it stays readable over the fan. A tick whose
          row the end-of-line value label occupies is dropped: both sit at the right edge, and the
          line's own value is the more useful number there. */}
      {!hb &&
        [1, 2, 3].map((i) => {
          const y = padT + (i / 4) * (H - padT - padB)
          if (Math.abs(y - endLabelY) < endLabelClear) return null
          return (
            <div
              key={'yax' + i}
              ref={(el) => {
                // every tick is the same size; keep whichever is still drawn (a dropped one nulls)
                if (el) yTickRef.current = el
              }}
              data-testid="fan-y-tick"
              style={{
                position: 'absolute',
                right: 4,
                top: y + 'px',
                transform: 'translateY(-50%)',
                fontSize: 8.5,
                fontWeight: 600,
                color: 'var(--faint)',
                pointerEvents: 'none',
                fontVariantNumeric: 'tabular-nums',
                opacity: 0.7,
                letterSpacing: '-0.02em',
                whiteSpace: 'nowrap',
                background: 'color-mix(in srgb, var(--surface) 55%, transparent)',
                padding: '0 2px',
                borderRadius: 2,
              }}
            >
              {fBaseCompact(maxY * (1 - i / 4))}
            </div>
          )
        })}
    </div>
  )
}

// ── Rich / Broke / Dead: a per-age 100%-stacked band of outcomes (mortality + depletion) ──
export interface OutcomeDataRow {
  age: number
  alive: number
  dead: number
  broke: number
  rich1: number
  rich2: number
  rich3: number
  cond: { broke: number; rich1: number; rich2: number; rich3: number }
}

export function RbdChart({
  data,
  height = 250,
  conditional = false,
}: {
  data: OutcomeDataRow[]
  height?: number
  conditional?: boolean
}) {
  const W = VW,
    H = height,
    padL = 8,
    padR = 8,
    padT = 14,
    padB = 22
  const minAge = data[0]!.age,
    maxAge = data[data.length - 1]!.age
  const xAt = (a: number) => padL + ((a - minAge) / (maxAge - minAge)) * (W - padL - padR)
  const yAt = (f: number) => padT + (1 - f) * (H - padT - padB)
  // stack bottom→top: richest first, then broke, then the mortality wedge on top
  const keys: Array<[string, string]> = conditional
    ? [
        ['rich3', '#1d9e75'],
        ['rich2', '#46c08d'],
        ['rich1', '#bfe3cf'],
        ['broke', 'var(--bad)'],
      ]
    : [
        ['rich3', '#1d9e75'],
        ['rich2', '#46c08d'],
        ['rich1', '#bfe3cf'],
        ['broke', 'var(--bad)'],
        ['dead', '#5b626e'],
      ]
  const val = (d: OutcomeDataRow, k: string) =>
    conditional
      ? d.cond[k as keyof typeof d.cond] || 0
      : (d as unknown as Record<string, number>)[k] || 0
  const cum = data.map(() => 0)
  const layers: Array<{ k: string; col: string; d: string }> = []
  for (const [k, col] of keys) {
    const lo = data.map((_, i) => cum[i]!)
    data.forEach((d, i) => {
      cum[i] = (cum[i] ?? 0) + val(d, k)
    })
    const hi = data.map((_, i) => cum[i]!)
    const up = data.map((d, i) => `${xAt(d.age).toFixed(1)} ${yAt(hi[i]!).toFixed(1)}`)
    const dn = data
      .map((_, i) => i)
      .reverse()
      .map((i) => `${xAt(data[i]!.age).toFixed(1)} ${yAt(lo[i]!).toFixed(1)}`)
    layers.push({ k, col, d: 'M' + up.join(' L ') + ' L ' + dn.join(' L ') + ' Z' })
  }
  const { ref, hi, onMove, onLeave } = useHover(data)
  const hd = hi != null ? data[hi] : null
  const hxPct = hd ? (xAt(hd.age) / W) * 100 : 0
  const pct = (x: number) => Math.round(x * 100)
  const haveMoney = (d: OutcomeDataRow) =>
    conditional ? d.cond.rich1 + d.cond.rich2 + d.cond.rich3 : d.rich1 + d.rich2 + d.rich3
  return (
    <div
      className="pl-chart-wrap"
      ref={ref}
      onMouseMove={onMove}
      onMouseLeave={onLeave}
      style={{ cursor: 'crosshair' }}
    >
      <svg
        className="pl-chart"
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        style={{ height }}
        aria-hidden="true"
      >
        {layers.map((L) => (
          <path key={L.k} d={L.d} fill={L.col} opacity={L.k === 'dead' ? 0.5 : 0.92} />
        ))}
        {hd && (
          <line
            x1={xAt(hd.age)}
            x2={xAt(hd.age)}
            y1={padT}
            y2={H - padB}
            stroke="var(--ink)"
            strokeWidth="1"
            vectorEffect="non-scaling-stroke"
            opacity="0.5"
          />
        )}
      </svg>
      {hd && (
        <div
          className="pl-chart-tip"
          style={{ left: hxPct + '%', transform: `translateX(${hxPct > 60 ? '-100%' : '0'})` }}
        >
          <div className="d">Age {Math.round(hd.age)}</div>
          {!conditional && (
            <div className="r">
              <i style={{ background: '#5b626e' }} />
              No longer living <b>{pct(hd.dead)}%</b>
            </div>
          )}
          <div className="r">
            <i style={{ background: 'var(--bad)' }} />
            Money ran out <b>{pct(conditional ? hd.cond.broke : hd.broke)}%</b>
          </div>
          <div className="r">
            <i style={{ background: '#46c08d' }} />
            Still have money <b>{pct(haveMoney(hd))}%</b>
          </div>
        </div>
      )}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          fontSize: 10,
          color: 'var(--faint)',
          marginTop: 4,
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        <span>Age {Math.round(minAge)}</span>
        <span>Age {Math.round((minAge + maxAge) / 2)}</span>
        <span>Age {Math.round(maxAge)}</span>
      </div>
    </div>
  )
}
