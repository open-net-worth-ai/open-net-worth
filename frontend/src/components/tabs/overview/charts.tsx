// Overview v2 — the shared Chart.js wrapper + theme every widget chart renders
// through, so title/axis/legend/tooltip theming is consistent whether the
// widget is hand-built or Lucius-generated (mirrors the handoff's `OvCharts`).
//
// NOTE (ADR 2026-07-22-overview-tab-redesign-faithful-port.md, decision 4): the
// handoff's "reused from production (frontend/src/components/tabs/Overview.tsx)"
// claim did not hold — no such wrapper existed; `Overview.tsx` hand-rolled three
// separate Chart.js lifecycles inline. This module is the real, new, single
// wrapper: `cssToken()` (utils/chartTheme.ts) is reused for CSS-var resolution
// rather than re-derived, and Overview.tsx's own three charts (net-worth trend,
// allocation donut, accessibility bar) are ported onto this wrapper as part of
// the Net-worth widget group so there is exactly one Chart.js wrapper app-wide.

import { useEffect, useRef } from 'react'
import {
  Chart,
  ArcElement,
  DoughnutController,
  BarElement,
  BarController,
  LineElement,
  LineController,
  PointElement,
  CategoryScale,
  LinearScale,
  Tooltip,
  Legend,
  Filler,
  type ChartConfiguration,
} from 'chart.js'
import { amountsHidden, subscribeAmountsHidden } from '../../../utils/amountMask'
import { cssToken } from '../../../utils/chartTheme'
import { fBase, fBaseCompact } from '../../../utils/format'

// Registered ONCE, here — the single Chart.js registration site for the whole
// Overview tab (every widget group imports `OvChart` from this module, so this
// runs before any chart mounts). The old, now-unmounted `tabs/Overview.tsx`
// used to be the only registration site in the app; this module replaces it
// rather than leaving registration to whichever widget group happens to
// import Chart.js first.
Chart.register(
  ArcElement,
  DoughnutController,
  BarElement,
  BarController,
  LineElement,
  LineController,
  PointElement,
  CategoryScale,
  LinearScale,
  Tooltip,
  Legend,
  Filler,
)

// Frontend palette — axis ticks, gridlines. Resolved live (not hardcoded) so
// charts follow the active theme; see resolveColor()/cssToken() below.
export const TICK = () => cssToken('--muted')
export const GRID = () => cssToken('--line')

if (Chart.defaults) {
  // Frontend charts draw no axis border lines or tick dashes — gridlines and
  // labels only, applied globally so every chart (inline scales included)
  // complies. Cast to `any`: Chart.js's scale-defaults type is a union across
  // every scale kind (cartesian/radial/…), and `border`/`grid`/`ticks` aren't
  // valid on all of them — the actual runtime object accepts them fine.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const scaleDefaults = Chart.defaults.scale as any
  scaleDefaults.border = { ...scaleDefaults.border, display: false }
  scaleDefaults.grid = { ...scaleDefaults.grid, drawTicks: false }
  scaleDefaults.ticks = { ...scaleDefaults.ticks, padding: 6 }
  Chart.defaults.elements.bar.borderRadius = 3
  Chart.defaults.elements.bar.borderSkipped = 'start'
}

export { amountsHidden }

// Canvas charts can't inherit the `body.hide-amounts .money` CSS mask (ticks/tooltips are pixels,
// not nodes), so every canvas-side money formatter consults amountsHidden() at draw time, and a
// flip redraws whatever is on screen: its ticks were painted under the old state.
subscribeAmountsHidden(() => {
  Object.values(Chart.instances).forEach((chart) => chart?.update('none'))
})

/** Compact currency tick: S$2k, S$14k. Masks to `***` in privacy mode — the
 * same glyph the DOM `.money` convention overlays, so canvas and DOM read as
 * one mask. */
export function kTick(v: unknown): string {
  if (amountsHidden()) return '***'
  const n = Number(v)
  if (!isFinite(n)) return String(v)
  if (Math.abs(n) < 1000) return fBase(n).replace(/\.\d+$/, '')
  return fBaseCompact(n)
}

/** Tooltip money label: the exact figure normally, `***` in privacy mode. A
 * hover tooltip leaks MORE than a tick (full precision), so it goes through
 * the same live check kTick uses. */
export function moneyTip(v: unknown): string {
  return amountsHidden() ? '***' : fBase(Number(v))
}

/** Standard cartesian axes (frontend styling). Pass per-axis overrides. */
export function axes(o?: { x?: object; y?: object }) {
  return {
    x: { ticks: { color: TICK(), maxTicksLimit: 8 }, grid: { display: false }, ...(o?.x || {}) },
    y: { ticks: { color: TICK(), callback: kTick }, grid: { color: GRID() }, ...(o?.y || {}) },
  }
}

export function legend(on: boolean) {
  return {
    display: !!on,
    position: 'top' as const,
    align: 'end' as const,
    labels: {
      color: TICK(),
      boxWidth: 12,
      boxHeight: 8,
      font: { size: 11 },
      padding: 10,
      usePointStyle: false,
    },
  }
}

/** Vertical fade fill for area lines — use as `backgroundColor` with `fill:true`. */
export function areaFill(hex: string, top?: string) {
  return (ctx: { chart: Chart }) => {
    const ca = ctx.chart.chartArea
    if (!ca) return 'transparent'
    const g = ctx.chart.ctx.createLinearGradient(0, ca.top, 0, ca.bottom)
    g.addColorStop(0, hex + (top || '26'))
    g.addColorStop(1, hex + '00')
    return g
  }
}

/** Canvas can't read CSS vars directly — resolve `var(--x)`/`var(--x, fallback)`
 * to a computed value for Chart.js. An unparseable color here is dangerous: one
 * bad color thrown inside Chart's shared animator blanks EVERY chart on the page. */
export function resolveColor(c: string): string {
  if (typeof c !== 'string' || c.indexOf('var(') < 0) return c
  const m = /var\(\s*(--[\w-]+)\s*(?:,\s*([^)]+))?\)/.exec(c)
  const token = m?.[1]
  if (!token) return c
  const v = getComputedStyle(document.body).getPropertyValue(token).trim()
  return v || (m[2] ? m[2].trim() : c)
}

/** Soften a resolved colour for a canvas fill. Handles the hex tokens this app ships and the
    rgb() form a browser may hand back, so a caller never has to care which it got. */
export function withAlpha(color: string, alpha: number): string {
  const c = color.trim()
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(c)
  if (hex) {
    const h = hex[1]!
    const full =
      h.length === 3
        ? h
            .split('')
            .map((ch) => ch + ch)
            .join('')
        : h
    const a = Math.round(Math.max(0, Math.min(1, alpha)) * 255)
    return `#${full}${a.toString(16).padStart(2, '0')}`
  }
  const rgb = /^rgba?\(([^)]+)\)$/i.exec(c)
  if (rgb) {
    const [r, g, bl] = rgb[1]!.split(',').map((v) => v.trim())
    return `rgba(${r}, ${g}, ${bl}, ${alpha})`
  }
  return c
}

export interface OvChartProps {
  // `any` type args: callers build configs for whichever chart `type` they
  // need (bar/line/doughnut mixed charts included) — Chart.js's own generic
  // ChartConfiguration can't express "one of several types" without the
  // per-type options intersection rejecting valid per-type options (`cutout`,
  // `indexAxis`, …). This wrapper is intentionally type-permissive at the
  // config boundary; Chart.js itself validates at runtime.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  config: ChartConfiguration<any, any, any>
  /** Rebuild only when this changes — parent re-renders (hover, etc.) don't thrash the canvas. */
  sig: unknown
  minHeight?: number
}

/** Canvas wrapper: builds the Chart on mount, rebuilds only when `sig`
 * changes, resizes with its container (Chart.js misses shrinkage), and cleans
 * up on unmount. Every chart in the Overview widget catalog renders through this. */
/** How long a tapped chart's tooltip stays after the finger lifts — long enough to read. */
const TOUCH_TOOLTIP_MS = 2500

export function OvChart({ config, sig, minHeight }: OvChartProps) {
  const ref = useRef<HTMLCanvasElement>(null)
  const inst = useRef<Chart | null>(null)
  const wrap = useRef<HTMLDivElement>(null)
  const cfgRef = useRef(config)
  cfgRef.current = config

  useEffect(() => {
    if (!ref.current) return
    inst.current?.destroy()
    inst.current = new Chart(ref.current, cfgRef.current)
    // Chart.js often measures the container before grid layout resolves width,
    // locking the canvas narrow. Force a re-measure once layout settles.
    let r1 = 0
    let r2 = 0
    r1 = requestAnimationFrame(() => {
      r2 = requestAnimationFrame(() => inst.current?.resize())
    })
    return () => {
      cancelAnimationFrame(r1)
      cancelAnimationFrame(r2)
      inst.current?.destroy()
      inst.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig])

  useEffect(() => {
    const el = wrap.current
    if (!el) return
    const ro = new ResizeObserver(() => inst.current?.resize())
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // On a touch screen Chart.js leaves the tooltip up after the finger lifts — and on a phone it
  // covers most of the chart, with nothing obvious to tap to get rid of it. It goes away on a
  // tap anywhere outside the chart, on any scroll, and a moment after the finger lifts.
  useEffect(() => {
    const el = wrap.current
    if (!el) return
    let timer = 0
    const hide = () => {
      const chart = inst.current
      if (!chart || !chart.getActiveElements().length) return
      chart.tooltip?.setActiveElements([], { x: 0, y: 0 })
      chart.setActiveElements([])
      chart.update('none')
    }
    const onDown = (e: PointerEvent) => {
      window.clearTimeout(timer)
      if (!el.contains(e.target as Node)) hide()
    }
    const onUp = (e: PointerEvent) => {
      if (e.pointerType !== 'touch' || !el.contains(e.target as Node)) return
      window.clearTimeout(timer)
      timer = window.setTimeout(hide, TOUCH_TOOLTIP_MS)
    }
    document.addEventListener('pointerdown', onDown, true)
    document.addEventListener('pointerup', onUp, true)
    window.addEventListener('scroll', hide, true)
    return () => {
      window.clearTimeout(timer)
      document.removeEventListener('pointerdown', onDown, true)
      document.removeEventListener('pointerup', onUp, true)
      window.removeEventListener('scroll', hide, true)
    }
  }, [])

  // Privacy toggles change what kTick()/moneyTip() should draw but not `sig`,
  // so Chart.js would keep the last-rendered (unmasked) pixels until the next
  // unrelated rebuild. Watch the body class and force a redraw when it flips.
  // A theme switch (`data-theme` on <html>) does the same for colours read at draw time.
  useEffect(() => {
    const mo = new MutationObserver(() => inst.current?.update())
    mo.observe(document.body, { attributes: true, attributeFilter: ['class'] })
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => mo.disconnect()
  }, [])

  return (
    <div
      className="ov-cjs"
      ref={wrap}
      style={{
        position: 'relative',
        flex: '1 1 auto',
        minHeight: minHeight || 0,
        height: '100%',
        maxHeight: '100%',
        width: '100%',
        overflow: 'hidden',
      }}
    >
      <canvas ref={ref} />
    </div>
  )
}
