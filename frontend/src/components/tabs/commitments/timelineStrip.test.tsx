// Regression test for owner-reported density: many funding-draw markers landing close
// together in time rendered overlapping nowrap labels ("$54K$16.3K in"). First fix attempt
// (hide the label, keep the dot) was itself rejected as looking broken — an unlabeled dot
// reads as an unexplained mark on the rail, not a design choice. Second fix attempt merged
// draws by PIXEL PROXIMITY (a %-of-strip-width threshold) — over a long span that folded
// draws 2-3 calendar months apart into one "5 funding draws" blob (owner: "why are we
// merging all funding draw here? ... only group the ones within the same month"). Final
// fix: group by literal calendar month instead of a proximity threshold — a marker means
// "this month's draws", full stop, never "close enough" by an arbitrary distance.
// @vitest-environment jsdom

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, cleanup, fireEvent } from '@testing-library/react'
import { afterEach } from 'vitest'
import { TimelineStrip, tipAlignStyle, drawStemHeight } from './timelineStrip'
import { monthShortYear } from '../../../utils/commitments'
import type { Commitment, FundingSource, Installment } from '../../../types/commitments'

// The strip windows from "today" and this file's fixtures carry hardcoded 2026-08 dates, so
// its assertions quietly change meaning as the calendar advances — several went red on
// 2026-08-05 with no code change, purely because the fixture dates had become the past.
beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date('2026-07-20T12:00:00Z'))
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

function installment(id: string, due_date: string, amount: number): Installment {
  return {
    id,
    label: id,
    description: '',
    due_date,
    amount,
    status: 'upcoming',
    paid_txn_id: null,
  }
}

function draw(id: string, available_date: string, value_base: number): FundingSource {
  return {
    id,
    type: 'windfall',
    label: id,
    amount: value_base,
    currency: 'SGD',
    ref_position_id: null,
    sell_quantity: null,
    available_date,
    ref_account_id: null,
    repayment_commitment_id: null,
    note: '',
    disabled: false,
    cadence: null,
    status: 'planned',
    realized_txn_id: null,
    value_base,
  }
}

function commitmentWithDenseDraws(): Commitment {
  // 8 draws packed into a 3-week window — reproduces the reported cluster.
  const dates = [
    '2026-08-01',
    '2026-08-04',
    '2026-08-07',
    '2026-08-10',
    '2026-08-13',
    '2026-08-16',
    '2026-08-19',
    '2026-08-22',
  ]
  return {
    id: 'dense',
    name: 'Dense commitment',
    kind: 'purchase',
    currency: 'SGD',
    status: 'active',
    linked_position_id: null,
    linked_account_id: null,
    note: '',
    created_at: '2026-01-01T00:00:00Z',
    installments: [installment('inst-1', '2026-09-30', 1000)],
    funding_plan: dates.map((d, i) => draw(`draw-${i}`, d, 1000 + i)),
  }
}

// How many draws a marker stands for: itself, plus the "+N" the merged badge advertises.
// The tooltip used to hang off each marker; 4d485ca3 hoisted it into ONE floating
// `.c3-tip-float` driven by hover state, so per-marker `.c3-tip-m` counting no longer works.
// The "+N" badge carries the same fact — nothing merged is silently dropped — without
// simulating hover on every marker.
function drawsRepresentedBy(marker: Element): number {
  const cnt = marker.querySelector('.cnt')?.textContent?.trim() ?? ''
  const extra = cnt.startsWith('+') ? Number(cnt.slice(1)) : 0
  return 1 + (Number.isFinite(extra) ? extra : 0)
}

describe('TimelineStrip — draw marker density', () => {
  it('merges closely-spaced draws into fewer markers, every one carrying a label', () => {
    const { container } = render(
      <TimelineStrip commitment={commitmentWithDenseDraws()} fx={{ SGD: 1 }} />,
    )
    const drawMarkers = container.querySelectorAll('.c2-strip-mark.draw')
    // 8 draws collapse into fewer merged markers — never a bare, unlabeled dot.
    expect(drawMarkers.length).toBeLessThan(8)
    expect(drawMarkers.length).toBeGreaterThan(0)
    expect(container.querySelectorAll('.c2-strip-mark.draw .dot')).toHaveLength(drawMarkers.length)
    expect(container.querySelectorAll('.c2-strip-mark.draw .lbl')).toHaveLength(drawMarkers.length)
    // Every source folded into a merged marker is still accounted for — nothing dropped.
    const totalRepresented = Array.from(drawMarkers).reduce(
      (sum, m) => sum + drawsRepresentedBy(m),
      0,
    )
    expect(totalRepresented).toBe(8)
  })

  it('keeps each draw as its own marker when they are spread far apart', () => {
    const c: Commitment = {
      ...commitmentWithDenseDraws(),
      funding_plan: [
        draw('a', '2026-08-01', 1000),
        draw('b', '2027-02-01', 2000),
        draw('c', '2027-08-01', 3000),
      ],
    }
    const { container } = render(<TimelineStrip commitment={c} fx={{ SGD: 1 }} />)
    expect(container.querySelectorAll('.c2-strip-mark.draw')).toHaveLength(3)
    expect(container.querySelectorAll('.c2-strip-mark.draw .lbl .cnt')).toHaveLength(0)
  })

  it('only merges draws within the SAME calendar month — never across months, no matter how close', () => {
    // The exact reported cluster: Aug 31, Sep 15, Sep 30, Oct 31, Nov 15 — 5 draws spanning
    // 2.5 months that a proximity threshold had folded into one "5 funding draws" marker.
    const c: Commitment = {
      ...commitmentWithDenseDraws(),
      installments: [installment('inst-1', '2026-12-31', 1000)],
      funding_plan: [
        draw('aug', '2026-08-31', 7700),
        draw('sep-1', '2026-09-15', 50000),
        draw('sep-2', '2026-09-30', 7700),
        draw('oct', '2026-10-31', 5400),
        draw('nov', '2026-11-15', 18900),
      ],
    }
    const { container } = render(<TimelineStrip commitment={c} fx={{ SGD: 1 }} />)
    const markers = container.querySelectorAll('.c2-strip-mark.draw')
    // 4 distinct months (Aug, Sep×2 merged, Oct, Nov) — never one blob of 5.
    expect(markers).toHaveLength(4)
    const septemberMarker = [...markers].find(
      (m) => m.querySelector('.cnt')?.textContent?.trim() === '+1',
    )!
    expect(drawsRepresentedBy(septemberMarker)).toBe(2)
    // No single marker should ever stand for all 5 — that was the reported bug exactly.
    for (const m of markers) expect(drawsRepresentedBy(m)).toBeLessThan(5)
  })
})

describe('TimelineStrip — month-axis labels reflect actual activity', () => {
  // Regression for a real screenshot + owner follow-up ("based on the months that things
  // happen, otherwise it will be confusing") — an evenly-spaced "every Nth" scheme had
  // labeled a quiet month while skipping ones that actually had a payment or draw. Uses
  // dates relative to "today" (the component's own `new Date()`) so this doesn't hardcode
  // a specific run date.
  function monthsFromNow(n: number, day = 1): string {
    const d = new Date()
    d.setMonth(d.getMonth() + n)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`
  }
  function monthShortOf(n: number): string {
    const d = new Date()
    d.setMonth(d.getMonth() + n)
    return monthShortYear(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`)
  }

  it('labels every active month across a long, sparsely-spaced schedule (installments every 3rd month)', () => {
    const activeOffsets = [2, 5, 8, 11, 14, 17, 20]
    const c: Commitment = {
      ...commitmentWithDenseDraws(),
      installments: activeOffsets.map((n, i) => installment(`inst-${i}`, monthsFromNow(n), 1000)),
      funding_plan: [],
    }
    const { container } = render(<TimelineStrip commitment={c} fx={{ SGD: 1 }} />)
    const labels = [...container.querySelectorAll('.c2-strip-months span')]
      .map((s) => s.textContent)
      .filter(Boolean)
    for (const n of activeOffsets) expect(labels).toContain(monthShortOf(n))
  })

  it('labels two back-to-back active months (e.g. an Aug set-aside AND a Sep set-aside) — no thinning eats the second one', () => {
    const c: Commitment = {
      ...commitmentWithDenseDraws(),
      installments: [installment('inst-far', monthsFromNow(20), 1000)],
      funding_plan: [
        draw('aug-saving', monthsFromNow(1), 1000),
        draw('sep-saving', monthsFromNow(2), 1000),
      ],
    }
    const { container } = render(<TimelineStrip commitment={c} fx={{ SGD: 1 }} />)
    const labels = [...container.querySelectorAll('.c2-strip-months span')]
      .map((s) => s.textContent)
      .filter(Boolean)
    expect(labels).toContain(monthShortOf(1))
    expect(labels).toContain(monthShortOf(2))
  })

  it('never labels a month with no payment or draw, even between two active months', () => {
    const c: Commitment = {
      ...commitmentWithDenseDraws(),
      installments: [
        installment('inst-a', monthsFromNow(1), 1000),
        installment('inst-b', monthsFromNow(9), 1000),
      ],
      funding_plan: [],
    }
    const { container } = render(<TimelineStrip commitment={c} fx={{ SGD: 1 }} />)
    const labels = [...container.querySelectorAll('.c2-strip-months span')]
      .map((s) => s.textContent)
      .filter(Boolean)
    // Months 1 and 9 are active; nothing happens in between (e.g. month 5) — must stay blank.
    expect(labels).toContain(monthShortOf(1))
    expect(labels).toContain(monthShortOf(9))
    expect(labels).not.toContain(monthShortOf(5))
  })
})

describe('TimelineStrip — payments and draws share ONE rail, nothing below it', () => {
  // Owner, after two rejected attempts (below-the-rail single row, then a below-the-rail
  // SECOND row): "we shouldn't show below the line, either on the line and above the line,
  // not below!" — payment dots and draw dots must sit at the exact same height (the rail),
  // and every stem/label offset must be a non-negative height ABOVE that shared anchor.
  it('positions every dot (payment and draw) at the identical height', () => {
    const c: Commitment = {
      ...commitmentWithDenseDraws(),
      installments: [
        installment('inst-1', '2026-09-30', 1000),
        installment('inst-2', '2027-01-15', 2000),
      ],
      funding_plan: [draw('a', '2026-08-01', 1000), draw('b', '2026-10-01', 2000)],
    }
    const { container } = render(<TimelineStrip commitment={c} fx={{ SGD: 1 }} />)
    const dots = [...container.querySelectorAll('.c2-strip-mark .dot')] as HTMLElement[]
    expect(dots.length).toBeGreaterThan(1)
    // No inline `bottom`/`top` override on any dot — every dot is positioned purely by the
    // shared CSS rule (`.c2-strip-mark .dot`), never per-marker, so they can never drift
    // apart from each other or from the rail.
    for (const dot of dots) {
      expect(dot.style.bottom).toBe('')
      expect(dot.style.top).toBe('')
    }
  })

  it('never gives a stem or label a negative height/offset (nothing extends below the rail)', () => {
    const c: Commitment = {
      ...commitmentWithDenseDraws(),
      installments: [
        installment('inst-1', '2026-08-31', 1000),
        installment('inst-2', '2026-09-15', 2000),
      ],
      funding_plan: [
        draw('a', '2026-08-15', 1000),
        draw('b', '2026-09-01', 2000),
        draw('c', '2026-09-30', 3000),
      ],
    }
    const { container } = render(<TimelineStrip commitment={c} fx={{ SGD: 1 }} />)
    const stemsAndLabels = [
      ...container.querySelectorAll(
        '.c2-strip-mark .stem, .c2-strip-mark .amt, .c2-strip-mark .lbl',
      ),
    ] as HTMLElement[]
    expect(stemsAndLabels.length).toBeGreaterThan(0)
    for (const el of stemsAndLabels) {
      const height = parseFloat(el.style.height || '0')
      const bottom = parseFloat(el.style.bottom || '0')
      expect(height).toBeGreaterThanOrEqual(0)
      expect(bottom).toBeGreaterThanOrEqual(0)
    }
  })
})

describe('TimelineStrip — every payment is drawn, under its own month', () => {
  // A 24-month loan drew only its first 14 payments and scrolled on through empty rail, and a
  // payment due on the 30th sat beside the NEXT month's label, so the whole row read a month late.
  function monthlyPayments(count: number, day = 30): Installment[] {
    return Array.from({ length: count }, (_, i) => {
      const lastDay = new Date(2026, 7 + i + 1, 0).getDate()
      const d = new Date(2026, 7 + i, Math.min(day, lastDay))
      const due = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
      return installment(`inst-${i}`, due, 1000)
    })
  }
  const withPayments = (
    installments: Installment[],
    extra: Partial<Commitment> = {},
  ): Commitment => ({
    ...commitmentWithDenseDraws(),
    installments,
    funding_plan: [],
    ...extra,
  })
  const leftOf = (el: Element): number => parseFloat((el as HTMLElement).style.left)
  const payMarkers = (container: HTMLElement) => [
    ...container.querySelectorAll('.c2-strip-mark.pay'),
  ]
  const monthLabels = (container: HTMLElement) =>
    [...container.querySelectorAll('.c2-strip-months span')].filter((s) => s.textContent)

  it('draws all 24 payments of a 24-month plan and labels the last month', () => {
    const { container } = render(
      <TimelineStrip commitment={withPayments(monthlyPayments(24))} fx={{ SGD: 1 }} />,
    )
    expect(payMarkers(container)).toHaveLength(24)
    expect(monthLabels(container).map((s) => s.textContent)).toContain(monthShortYear('2028-07'))
  })

  it('puts a month-end payment exactly on its own month label, never the next one', () => {
    const { container } = render(
      <TimelineStrip commitment={withPayments(monthlyPayments(24))} fx={{ SGD: 1 }} />,
    )
    const labels = monthLabels(container)
    expect(labels).toHaveLength(24)
    payMarkers(container).forEach((marker, i) => {
      expect(leftOf(marker)).toBe(leftOf(labels[i]!))
    })
  })

  it('leaves room for the first month label instead of clipping it at the left edge', () => {
    const c = withPayments([installment('now', '2026-07-30', 1000)])
    const { container } = render(<TimelineStrip commitment={c} fx={{ SGD: 1 }} />)
    // Labels are centred on their tick and run ~50px wide, so half of that has to fit.
    expect(leftOf(monthLabels(container)[0]!)).toBeGreaterThanOrEqual(25)
  })

  it('does not scroll on through empty months after the last payment', () => {
    const { container } = render(
      <TimelineStrip commitment={withPayments(monthlyPayments(24))} fx={{ SGD: 1 }} />,
    )
    const strip = container.querySelector('.c2-strip') as HTMLElement
    const lastTick = leftOf(payMarkers(container).at(-1)!)
    const monthPx = leftOf(payMarkers(container)[1]!) - leftOf(payMarkers(container)[0]!)
    expect(parseFloat(strip.style.width)).toBeLessThanOrEqual(lastTick + monthPx)
  })

  it('keeps a busy month inside its own slot, so later months stay on their labels', () => {
    // Weekly payments for a year: pushing markers apart inside fixed-width months would walk
    // every later marker further and further from its label.
    const weekly = Array.from({ length: 52 }, (_, i) => {
      const d = new Date(2026, 7, 3 + i * 7)
      const due = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
      return installment(`week-${i}`, due, 100)
    })
    const { container } = render(
      <TimelineStrip commitment={withPayments(weekly)} fx={{ SGD: 1 }} />,
    )
    const markers = payMarkers(container)
    expect(markers).toHaveLength(52)
    const labels = monthLabels(container)
    weekly.forEach((inst, i) => {
      const idx = labels.findIndex(
        (l) => l.textContent === monthShortYear(inst.due_date.slice(0, 7)),
      )
      expect(leftOf(markers[i]!)).toBeGreaterThanOrEqual(leftOf(labels[idx]!))
      if (labels[idx + 1]) expect(leftOf(markers[i]!)).toBeLessThan(leftOf(labels[idx + 1]!))
    })
    const strip = container.querySelector('.c2-strip') as HTMLElement
    expect(leftOf(markers.at(-1)!)).toBeLessThan(parseFloat(strip.style.width))
  })

  it('does not drift when every month has both a payment and a funding draw', () => {
    const installments = monthlyPayments(24, 15)
    const c = withPayments(installments, {
      funding_plan: installments.map((inst, i) => draw(`d-${i}`, inst.due_date, 500)),
    })
    const { container } = render(<TimelineStrip commitment={c} fx={{ SGD: 1 }} />)
    const labels = monthLabels(container)
    const pays = payMarkers(container)
    pays.forEach((marker, i) => expect(leftOf(marker)).toBe(leftOf(labels[i]!)))
  })

  it('starts the shortfall shade at the first uncovered payment', () => {
    const installments = monthlyPayments(3)
    const c = withPayments(installments, {
      coverage: { at_risk: true, earliest_shortfall_date: installments[1]!.due_date },
    } as Partial<Commitment>)
    const { container } = render(<TimelineStrip commitment={c} fx={{ SGD: 1 }} />)
    const shade = container.querySelector('.c2-strip-shade')!
    expect(leftOf(shade)).toBe(leftOf(payMarkers(container)[1]!))
    expect(container.querySelectorAll('.c2-strip-mark.pay.bad')).toHaveLength(2)
  })
})

describe('drawStemHeight — fixed two-height alternation by chronological order', () => {
  // An earlier (rejected) design used an unbounded greedy lane-packing shared between
  // payments AND draws — it never collided, but pooling both marker types into one
  // unpredictable stack produced a jumbled, hard-to-read skyline (owner: "this now looks so
  // weird"). Simpler fix, owner-specified: draws alternate strictly by position in the
  // (already date-sorted) list between exactly two fixed heights — "on the line" and "above
  // the line, at half the payment's height" — so two draws next to each other are NEVER at
  // the same height, with no unbounded stacking and no cross-type interleaving.
  it('alternates on-line/above-line strictly by index parity', () => {
    const heights = [0, 1, 2, 3, 4, 5].map(drawStemHeight)
    expect(heights[0]).toBe(heights[2])
    expect(heights[2]).toBe(heights[4])
    expect(heights[1]).toBe(heights[3])
    expect(heights[3]).toBe(heights[5])
    expect(heights[0]).not.toBe(heights[1])
  })

  it('never gives two adjacent draws the same height', () => {
    for (let i = 0; i < 10; i++) {
      expect(drawStemHeight(i)).not.toBe(drawStemHeight(i + 1))
    }
  })
})

describe('tipAlignStyle — edge-aware tooltip anchoring', () => {
  // Regression for a real screenshot bug: a marker near the strip's left edge (e.g. the
  // very first month) centered its tooltip via the default CSS (left:50% + translateX(-50%)),
  // pushing a wide multi-line tooltip off the left of the page, clipped by the sidebar.
  //
  // 2026-08-23: the guard was a PERCENTAGE of the visible strip (20%), but what it protects
  // against is a fixed-width box overflowing — `.c3-tip-float` is `max-width: 280px` and
  // centred, so it can overhang by at most 140px. On a wide strip 20% is far more than that:
  // at ~1150px visible the dead zone was 230px, so a marker 225px in had its tooltip thrown
  // to the section's far-left corner, ~180px from its own dot (owner: "why is it shown like
  // nowhere?"). The test is now a pixel distance, which is the quantity that actually matters.
  it('left-anchors only when the tooltip would really overhang the left edge', () => {
    expect(tipAlignStyle(0, 1150)).toEqual({ left: 0, transform: 'translateY(2px)' })
    expect(tipAlignStyle(60, 1150)).toEqual({ left: 0, transform: 'translateY(2px)' })
  })

  it('leaves a marker alone once it clears the tooltip half-width, however wide the strip', () => {
    // The exact case from the screenshot: 225px into a ~1150px strip. Under the old 20% rule
    // this was pinned to the corner; the tooltip needs only ~140px of room and has 225.
    expect(tipAlignStyle(225, 1150)).toBeUndefined()
    expect(tipAlignStyle(575, 1150)).toBeUndefined()
  })

  it('right-anchors a tooltip that would overhang the right edge', () => {
    expect(tipAlignStyle(1150, 1150)).toEqual({
      left: 'auto',
      right: 0,
      transform: 'translateY(2px)',
    })
    expect(tipAlignStyle(1090, 1150)).toEqual({
      left: 'auto',
      right: 0,
      transform: 'translateY(2px)',
    })
  })

  it('still guards both edges on a narrow strip, where the dead zones nearly meet', () => {
    expect(tipAlignStyle(100, 300)).toEqual({ left: 0, transform: 'translateY(2px)' })
    expect(tipAlignStyle(200, 300)).toEqual({
      left: 'auto',
      right: 0,
      transform: 'translateY(2px)',
    })
  })
})

// Regression for a real owner-caught commitment: 39 funding sources (recurring monthly
// set-asides plus a handful of asset sales/loans) merge, on the desktop lollipop chart, into a
// few dozen compact markers — fine. But the phone-only list reflow (.c2-strip-list) rendered
// EVERY one of them as a full-height row with no cap, turning "payments on the timeline" —
// meant to be a compact summary — into the single longest, most dominant section on the
// entire detail screen, 50+ rows deep with no end in sight.
// PENDING the phone-list cap itself: `.c2-strip-li` / `.c2-strip-more` appear nowhere in
// src/ — these were written ahead of the component (4d485ca3, "wip(approvals): in-progress
// work snapshot"). Kept as the spec for that work, skipped so they do not read as a
// regression in an unrelated change. Un-skip with the implementation.
describe.skip('TimelineStrip — phone list is capped, not an uncapped wall of rows', () => {
  function commitmentWithManyDraws(n: number): Commitment {
    const dates = Array.from({ length: n }, (_, i) => {
      const d = new Date(2026, 7 + i, 1) // Aug 2026 onward, one per month
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-01`
    })
    return {
      ...commitmentWithDenseDraws(),
      installments: [installment('inst-1', '2026-09-30', 1000)],
      funding_plan: dates.map((d, i) => draw(`draw-${i}`, d, 1000 + i)),
    }
  }

  it('shows only the first 8 (nearest-term) events and a "show more" affordance', () => {
    const { container } = render(
      <TimelineStrip commitment={commitmentWithManyDraws(39)} fx={{ SGD: 1 }} />,
    )
    expect(container.querySelectorAll('.c2-strip-li')).toHaveLength(8)
    const more = container.querySelector('.c2-strip-more')
    expect(more).not.toBeNull()
    expect(more!.textContent).toMatch(/Show 32 more/)
  })

  it('expanding "show more" reveals every event; "show less" re-collapses it', () => {
    const { container } = render(
      <TimelineStrip commitment={commitmentWithManyDraws(39)} fx={{ SGD: 1 }} />,
    )
    fireEvent.click(container.querySelector('.c2-strip-more') as HTMLButtonElement)
    // 39 draws + the fixture's 1 installment = 40 total events.
    expect(container.querySelectorAll('.c2-strip-li')).toHaveLength(40)
    expect(container.querySelector('.c2-strip-more')!.textContent).toBe('Show less')
    fireEvent.click(container.querySelector('.c2-strip-more') as HTMLButtonElement)
    expect(container.querySelectorAll('.c2-strip-li')).toHaveLength(8)
  })

  it('does not render the expand affordance when everything already fits', () => {
    const { container } = render(
      <TimelineStrip commitment={commitmentWithManyDraws(3)} fx={{ SGD: 1 }} />,
    )
    expect(container.querySelectorAll('.c2-strip-li')).toHaveLength(4) // 3 draws + 1 installment
    expect(container.querySelector('.c2-strip-more')).toBeNull()
  })

  it('the desktop lollipop chart is unaffected — still one marker per merged month, no cap', () => {
    const { container } = render(
      <TimelineStrip commitment={commitmentWithManyDraws(39)} fx={{ SGD: 1 }} />,
    )
    // 39 monthly draws, one per calendar month → 39 distinct month groups, never truncated.
    expect(container.querySelectorAll('.c2-strip-mark.draw').length).toBe(39)
  })
})
