// "Payments & funding" timeline strip — a lollipop rail of unpaid installments (merged when
// they share a due date) vs. dated funding draws, with a shaded region + gap-month label when
// the commitment carries a real coverage shortfall.
//
// Layout: payment AND funding-draw dots both sit ON the ONE shared rail line, distinguished
// only by dot style (filled vs hollow, matching the legend); nothing renders below it.
// Payments keep one fixed full-height stem. Draws alternate between exactly TWO fixed heights
// by chronological order — "on the line" (a short stem) and "above the line, at half the
// payment's height" — so two draws that land next to each other are never at the same height
// and can't collide, without an unbounded/unpredictable stack.
//
// Geometry: each calendar month gets one equal slot (at least MONTH_PX) rather than squeezing
// the whole span into the container's width — a long span just renders wider than the
// viewport, and the strip scrolls horizontally (.c2-strip-scroll) instead of cramming markers
// and month labels into unreadable density. Every marker starts at its month's tick, directly
// above that month's label, whatever its day of the month: placed by literal date, a payment
// due on the 30th sat beside the NEXT month's label.
//
// Tooltip: rendered OUTSIDE the scrolling element, driven by JS hover state instead of a
// per-marker CSS :hover .c3-tip. `overflow-x: auto` on .c2-strip-scroll forces the used value
// of its OWN overflow-y to `auto` too, per the CSS Overflow spec — there is no CSS-only way to
// let a descendant escape that clipping while the scroll axis stays scrollable. A tooltip
// living inside that box either gets clipped, or the box needs permanent padding tall enough
// to fit it — which reserves that space even when nothing is hovered. Rendering it as a
// sibling of the scroll container sidesteps the whole problem: zero reserved space at rest,
// full room to render when something is actually hovered.
import { useEffect, useRef, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import type { Commitment, FundingSource } from '../../../types/commitments'
import { toBase } from '../../../utils/format'
import { fCompactCurrency, fmtDate, monthShortYear } from '../../../utils/commitments'

// Edge-aware anchoring for the floating tooltip: a marker near either edge of the CURRENTLY
// VISIBLE scroll window would otherwise center a wide multi-line tooltip half off-screen.
// Both arguments are PIXELS within that visible window, computed at hover time in
// TimelineStrip. The default (undefined) case relies on .c3-tip-float's own CSS for centering
// — this only overrides it when the box would actually overhang.
//
// This was a PERCENTAGE (20% of the visible width) until 2026-08-23, which is the wrong
// quantity: the thing being protected is a fixed-size box. `.c3-tip-float` is
// `max-width: 280px` and centred on its marker, so it can overhang by at most half that. On a
// wide strip 20% is far more room than it ever needs — at ~1150px visible the dead zone was
// 230px, so a marker 225px in had its tooltip flung to the section's far-left corner, ~180px
// from the dot it describes (owner: "why is it shown like nowhere?"). Worse, the dead zone
// grew with the strip, so the wider the pane the more detached the tooltip became.
const TIP_MAX_HALF_PX = 140
export function tipAlignStyle(leftPx: number, visibleWidth: number): CSSProperties | undefined {
  if (leftPx < TIP_MAX_HALF_PX) return { left: 0, transform: 'translateY(2px)' }
  if (leftPx > visibleWidth - TIP_MAX_HALF_PX) {
    return { left: 'auto', right: 0, transform: 'translateY(2px)' }
  }
  return undefined
}

const monthKey = (date: string): string => (date || '').slice(0, 7)
const monthOrdinal = (key: string): number =>
  Number(key.slice(0, 4)) * 12 + Number(key.slice(5, 7)) - 1

// Minimum pixel width per calendar month — the strip's total width is one slot per month plus
// LEFT_PAD_PX (floored at MIN_STRIP_PX), never compressed to fit the viewport. Guarantees a
// minimum distance between month ticks and between markers regardless of how many months the
// plan spans; a long plan just scrolls further instead of cramming.
const MONTH_PX = 80
const MIN_STRIP_PX = 320
// Month labels ("Nov 2026") and amount labels are centred on their tick and run ~50px wide, so
// the first tick needs half of that clear of the strip's left edge.
const LEFT_PAD_PX = 28
// Amount labels ("$27.4K") run ~40-46px wide at this font size — two markers closer than this
// have their labels touch or overlap regardless of which month(s) they fall in.
const MIN_MARKER_GAP_PX = 46

// One slot width for the whole strip, wide enough for its busiest month: markers in the same
// month spread right from its tick, and a slot too narrow to hold them would push every later
// marker further and further from its own month's label.
function monthSlotPx(markerMonths: string[]): number {
  const counts = new Map<string, number>()
  for (const key of markerMonths) counts.set(key, (counts.get(key) ?? 0) + 1)
  return Math.max(MONTH_PX, Math.max(0, ...counts.values()) * MIN_MARKER_GAP_PX)
}

// Crowded draw labels merge by literal CALENDAR MONTH, never by pixel/percentage proximity —
// a marker represents "this month's draws", full stop, so a Aug-31 set-aside and a Sep-15 loan
// draw are never folded together just because they happen to land close on the rail.
interface DrawGroup {
  id: string
  posPx: number
  total: number
  items: FundingSource[]
}

function groupDrawsByMonth(
  draws: FundingSource[],
  posForMonth: (key: string) => number,
): DrawGroup[] {
  const byMonth = new Map<string, FundingSource[]>()
  for (const f of draws) {
    const key = monthKey(f.available_date as string)
    const bucket = byMonth.get(key)
    if (bucket) bucket.push(f)
    else byMonth.set(key, [f])
  }
  const groups: DrawGroup[] = [...byMonth.entries()].map(([key, items]) => ({
    id: key,
    posPx: posForMonth(key),
    total: items.reduce((sum, f) => sum + (f.value_base ?? 0), 0),
    items,
  }))
  return groups.sort((a, b) => a.posPx - b.posPx)
}

// Draws alternate strictly by chronological order (index parity) between two fixed stem
// heights — simpler and more predictable than a greedy min-gap/lane-packing scheme. Two
// adjacent draws always land on DIFFERENT heights by construction, so they can never collide,
// and the visual has exactly two clean rows to read instead of an unpredictable stack.
const PAY_STEM = 32
const DRAW_STEM_ON_LINE = 12
const DRAW_STEM_ABOVE = PAY_STEM / 2
export function drawStemHeight(index: number): number {
  return index % 2 === 0 ? DRAW_STEM_ON_LINE : DRAW_STEM_ABOVE
}

interface PayMark {
  id: string
  due_date: string
  amount: number
  labels: string[]
  posPx: number
}

interface HoverTip {
  id: string
  leftPx: number
  bottomPx: number
  visibleWidth: number
  content: ReactNode
}

interface Props {
  commitment: Commitment
  fx: Record<string, number>
}

export function TimelineStrip({ commitment: c, fx }: Props) {
  const sectionRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const stripRef = useRef<HTMLDivElement>(null)
  const [hover, setHover] = useState<HoverTip | null>(null)

  // A tap has no "pointer moved away", so onMouseLeave never fires on touch and an opened
  // tooltip would otherwise stay pinned until another marker is tapped. Dismiss on any
  // pointer-down outside the strip's own section, and on Escape for keyboard/desktop parity.
  // Declared before the early returns below so this hook is always called, never
  // conditionally.
  useEffect(() => {
    if (hover == null) return
    const onDown = (e: PointerEvent) => {
      if (!(e.target as HTMLElement | null)?.closest?.('.c2-strip-section')) setHover(null)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setHover(null)
    }
    document.addEventListener('pointerdown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [hover])

  const unpaid = c.installments.filter((i) => i.status !== 'paid')
  if (!unpaid.length) return null
  const draws = (c.funding_plan || []).filter((f) => f.available_date && !f.disabled)
  const gapDate = c.coverage?.at_risk ? c.coverage.earliest_shortfall_date : null
  const today = new Date()
  const firstOrdinal = today.getFullYear() * 12 + today.getMonth()
  const allDates = [
    ...unpaid.map((i) => i.due_date),
    ...draws.map((f) => f.available_date as string),
    ...(gapDate ? [gapDate] : []),
  ]
  // The month a date falls in, counted from this month. An overdue date has no slot of its
  // own, so it shares this month's.
  const monthIndex = (key: string): number => Math.max(0, monthOrdinal(key) - firstOrdinal)
  const lastIndex = Math.max(...allDates.map((d) => monthIndex(monthKey(d))))
  const months = Array.from({ length: lastIndex + 1 }, (_, i) => {
    const d = new Date(today.getFullYear(), today.getMonth() + i, 1)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
  })

  // One entry per marker the strip will draw, keyed by the slot it lands in.
  const slotOf = (date: string): string => months[monthIndex(monthKey(date))]!
  const slotPx = monthSlotPx([
    ...[...new Set(unpaid.map((i) => i.due_date))].map(slotOf),
    ...[...new Set(draws.map((f) => monthKey(f.available_date as string)))].map(slotOf),
  ])
  const W = Math.max(MIN_STRIP_PX, LEFT_PAD_PX + months.length * slotPx)
  const posForMonth = (key: string): number => LEFT_PAD_PX + monthIndex(key) * slotPx

  // merge installments that share a due date into one marker
  const payMarks: PayMark[] = []
  for (const i of unpaid) {
    const instBase = toBase(i.amount, c.currency, fx)
    const g = payMarks.find((x) => x.due_date === i.due_date)
    if (g) {
      g.amount += instBase
      g.labels.push(i.label)
    } else {
      payMarks.push({
        id: i.id,
        due_date: i.due_date,
        amount: instBase,
        labels: [i.label],
        posPx: posForMonth(monthKey(i.due_date)),
      })
    }
  }

  const drawGroups = groupDrawsByMonth(draws, posForMonth)

  // Markers in the same month all start on its tick, so their amount labels would overlap.
  // Greedy left-to-right minimum-gap pass across payments AND draws TOGETHER (not each series
  // separately, since a payment marker and a draw marker crowd each other just as much as two
  // of the same kind): walking markers in chronological order, each one is pushed right only
  // as far as needed to clear the previous one's label width — never left, so nothing at the
  // very start of the strip moves. slotPx leaves room for this inside the month.
  const combined = [
    ...payMarks.map((g) => ({ ref: g as PayMark | DrawGroup, pos: g.posPx })),
    ...drawGroups.map((g) => ({ ref: g as PayMark | DrawGroup, pos: g.posPx })),
  ].sort((a, b) => a.pos - b.pos)
  let lastPos = -Infinity
  for (const m of combined) {
    const adjusted = Math.max(m.pos, lastPos + MIN_MARKER_GAP_PX)
    m.ref.posPx = adjusted
    lastPos = adjusted
  }

  // Label a month tick because something REAL happens that month — a payment or a funding
  // draw — never an arbitrary evenly-spaced sample. Every month with real activity gets its
  // label, full stop, so a real event is never silently skipped in favor of an evenly-spaced
  // but quiet neighbor.
  const shownMonths = new Set<string>()
  for (const g of payMarks) shownMonths.add(monthKey(g.due_date))
  for (const group of drawGroups)
    for (const f of group.items) shownMonths.add(monthKey(f.available_date as string))
  if (gapDate) shownMonths.add(monthKey(gapDate))

  // The shade opens at the first payment it covers, so a covered payment earlier in the same
  // month stays outside it; a gap with no payment on it opens at its month's tick.
  const firstBadPay = gapDate ? payMarks.find((g) => g.due_date >= gapDate) : undefined
  const shadeLeft = gapDate ? (firstBadPay?.posPx ?? posForMonth(monthKey(gapDate))) : 0

  // Position + show the shared floating tooltip for a marker at `posPx` (the strip's own
  // coordinate system), `stemPx` above its own stem+label (same `stemH + 24` offset the
  // tooltip always used). `leftPx` converts to "visible window" coordinates by subtracting the
  // scroll container's current scrollLeft, so the tooltip lands correctly regardless of how far
  // the strip has been scrolled. `bottomPx` is measured against `.c2-strip`'s OWN bottom edge
  // (not the scroll wrapper's) — `bottom: stemPx` on the stem/label is relative to `.c2-strip`,
  // and `.c2-strip-months` sits BELOW it inside the same scroll wrapper, so measuring the
  // wrapper's edge instead put the tooltip one month-row too low, level with the amount labels
  // instead of above them.
  const showTip = (id: string, posPx: number, stemPx: number, content: ReactNode) => {
    const scrollEl = scrollRef.current
    const sectionEl = sectionRef.current
    const stripEl = stripRef.current
    const scrollLeft = scrollEl?.scrollLeft ?? 0
    const visibleWidth = scrollEl?.clientWidth || 1
    const leftPx = posPx - scrollLeft
    const stripRect = stripEl?.getBoundingClientRect()
    const sectionRect = sectionEl?.getBoundingClientRect()
    const extraBelowStrip = stripRect && sectionRect ? sectionRect.bottom - stripRect.bottom : 0
    setHover({ id, leftPx, bottomPx: extraBelowStrip + stemPx + 24, visibleWidth, content })
  }
  const hideTip = () => setHover(null)
  // A tap fires a synthetic mouseenter but the browser never sustains a "hover" once the
  // finger lifts, so mouseleave (or nothing at all) follows almost immediately — the tooltip
  // opened and closed in the same instant. onPointerDown covers mouse AND touch in one
  // handler and TOGGLES (tapping the already-open marker closes it), which is the only
  // dismiss touch has, since there is no pointer to move away afterward.
  const toggleTip = (id: string, posPx: number, stemPx: number, content: ReactNode) => {
    if (hover?.id === id) {
      hideTip()
    } else {
      showTip(id, posPx, stemPx, content)
    }
  }

  return (
    <div className="c2-exp-sec c2-strip-section" ref={sectionRef}>
      <div className="c2-exp-sec-h">
        <span className="c2-exp-sec-t">Payments &amp; funding</span>
      </div>
      <div className="c2-strip-legend">
        <i className="c2-strip-key out" /> payment
        {gapDate && (
          <>
            &nbsp; <i className="c2-strip-key bad" /> not covered yet
          </>
        )}
        &nbsp; <i className="c2-strip-key in" /> funding draw
      </div>
      <div className="c2-strip-scroll" ref={scrollRef} onScroll={hideTip}>
        <div className="c2-strip" style={{ width: W }} ref={stripRef}>
          {gapDate && <div className="c2-strip-shade" style={{ left: shadeLeft, right: 0 }} />}
          <div className="c2-strip-rail" />
          {payMarks.map((g) => {
            const bad = !!gapDate && g.due_date >= gapDate
            const tip = (
              <>
                <b>{g.labels.join(' + ')}</b>
                <span className="c3-tip-m">
                  {fmtDate(g.due_date)} •{' '}
                  <span className="money">{fCompactCurrency(g.amount)}</span>
                </span>
                {bad && (
                  <span className="c3-tip-w">Not covered yet — lands after the projected gap</span>
                )}
              </>
            )
            return (
              <div
                key={g.id}
                className={'c2-strip-mark pay' + (bad ? ' bad' : '')}
                style={{ left: g.posPx }}
                onMouseEnter={() => showTip('p:' + g.id, g.posPx, PAY_STEM, tip)}
                onMouseLeave={hideTip}
                onPointerDown={() => toggleTip('p:' + g.id, g.posPx, PAY_STEM, tip)}
              >
                <span className="amt money" style={{ bottom: PAY_STEM + 'px' }}>
                  {fCompactCurrency(g.amount)}
                </span>
                <span className="stem" style={{ height: PAY_STEM + 'px' }} />
                <span className="dot" />
              </div>
            )
          })}
          {drawGroups.map((group, i) => {
            const stemH = drawStemHeight(i)
            const tip = (
              <>
                <b>
                  {group.items.length > 1
                    ? `${group.items.length} funding draws`
                    : group.items[0]!.label}
                </b>
                {group.items.map((f) => (
                  <span key={f.id} className="c3-tip-m">
                    {group.items.length > 1 ? `${f.label} • ` : ''}
                    {fmtDate(f.available_date as string)} •{' '}
                    <span className="money">{fCompactCurrency(f.value_base ?? 0)}</span>
                  </span>
                ))}
              </>
            )
            return (
              <div
                key={group.id}
                className="c2-strip-mark draw"
                style={{ left: group.posPx }}
                onMouseEnter={() => showTip('d:' + group.id, group.posPx, stemH, tip)}
                onMouseLeave={hideTip}
                onPointerDown={() => toggleTip('d:' + group.id, group.posPx, stemH, tip)}
              >
                <span className="lbl" style={{ bottom: stemH + 'px' }}>
                  <span className="money">{fCompactCurrency(group.total)}</span>
                  {group.items.length > 1 && (
                    <span className="cnt"> +{group.items.length - 1}</span>
                  )}
                </span>
                <span className="stem" style={{ height: stemH + 'px' }} />
                <span className="dot" />
              </div>
            )
          })}
        </div>
        <div className="c2-strip-months" style={{ width: W }}>
          {months.map((ym) => (
            <span
              key={ym}
              className={gapDate && monthKey(gapDate) === ym ? 'gap' : ''}
              style={{ left: posForMonth(ym) }}
            >
              {shownMonths.has(ym) ? monthShortYear(ym) : ''}
            </span>
          ))}
        </div>
      </div>
      {hover && (
        <div
          className="c3-tip-float"
          style={{
            left: hover.leftPx,
            bottom: hover.bottomPx,
            ...tipAlignStyle(hover.leftPx, hover.visibleWidth),
          }}
        >
          {hover.content}
        </div>
      )}
    </div>
  )
}
