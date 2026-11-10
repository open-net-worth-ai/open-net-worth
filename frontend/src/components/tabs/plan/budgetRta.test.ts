// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest'
import { createElement } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { RtaHero } from './budgetRta'
import { buildRtaLines } from './rtaLines'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

// The restored identity's worked example (ADR 2026-09-09, synthetic-safe aggregates): cash on
// hand 37,665.52 less 458.17 held in envelopes and 5,582.62 of card spending nothing covered
// this month → 31,624.73.
const BOOK = {
  onBudgetCash: 37665.52,
  heldInEnvelopes: 458.17,
  heldOutsideEnvelopes: 0,
  cardSpendNotCovered: 5582.62,
  baseline: 0,
  assignedInFuture: 0,
  rta: 31624.73,
}

describe('buildRtaLines — the identity, one line per term', () => {
  it('renders cash, held in envelopes and uncovered card spend, in order, summing to RTA', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const lines = buildRtaLines(BOOK)
    expect(lines.map((l) => l.label)).toEqual([
      'Cash on hand',
      'Held in envelopes',
      'Card spending not covered this month',
    ])
    expect(lines.map((l) => l.op)).toEqual(['+', '−', '−'])
    expect(lines.map((l) => l.value)).toEqual([37665.52, -458.17, -5582.62])
    expect(lines.reduce((s, l) => s + l.value, 0)).toBeCloseTo(BOOK.rta, 2)
    expect(lines.some((l) => l.key === 'other')).toBe(false)
    expect(warn).not.toHaveBeenCalled()
  })

  it('adds credit on your cards after cash when a card is in credit, still summing exactly', () => {
    // ADR 2026-11-02: an overpaid or refunded card holds your own money — budget money beside
    // cash, never subtracted. Absent (most books) it never renders.
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const lines = buildRtaLines({ ...BOOK, cardCredit: 1375, rta: BOOK.rta + 1375 })
    expect(lines.map((l) => l.label)).toEqual([
      'Cash on hand',
      'Credit on your cards',
      'Held in envelopes',
      'Card spending not covered this month',
    ])
    expect(lines[1]).toMatchObject({ key: 'cardcredit', value: 1375, op: '+' })
    expect(lines.reduce((s, l) => s + l.value, 0)).toBeCloseTo(BOOK.rta + 1375, 2)
    expect(lines.some((l) => l.key === 'other')).toBe(false)
    expect(warn).not.toHaveBeenCalled()
  })

  it('always shows Held in envelopes; hides card spend, Baseline and future at zero', () => {
    // What the envelopes hold is the rule's other half and reads as "nothing is spoken for" at
    // $0. The other three are terms most books never have — zero rows would only add noise.
    const lines = buildRtaLines({
      ...BOOK,
      heldInEnvelopes: 0,
      heldOutsideEnvelopes: 0,
      cardSpendNotCovered: 0,
      rta: 37665.52,
    })
    expect(lines.map((l) => l.key)).toEqual(['cash', 'held'])
  })

  // ADR 2026-09-09: an envelope HOLDS its own overspend until month-end, so the signed sum can
  // go NEGATIVE — which hands room back and puts Ready to Assign ABOVE cash on hand. The row
  // must then read '+', because that is what the total below it does. (Under the one-day
  // 2026-09-08 identity this state was unreachable: set-aside was Σ max(available, 0).)
  it('renders a NET OVERSPENT envelope sum as a + row, not a hidden or clamped one', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const lines = buildRtaLines({
      ...BOOK,
      heldInEnvelopes: -320,
      heldOutsideEnvelopes: 0,
      cardSpendNotCovered: 0,
      rta: 37985.52, // 37,665.52 − (−320) — above cash on hand, and that is correct
    })
    expect(lines.find((l) => l.key === 'held')).toMatchObject({
      label: 'Held in envelopes',
      value: 320,
      op: '+',
    })
    expect(lines.find((l) => l.key === 'held')!.tone).toBeUndefined()
    expect(lines.reduce((s, l) => s + l.value, 0)).toBeCloseTo(37985.52, 2)
    expect(lines.some((l) => l.key === 'other')).toBe(false)
    expect(warn).not.toHaveBeenCalled()
  })

  it('shows Baseline only when the cutover baseline is nonzero', () => {
    const lines = buildRtaLines({ ...BOOK, baseline: 12000, rta: 19624.73 })
    const baseline = lines.find((l) => l.key === 'baseline')!
    expect(baseline.label).toBe('Baseline')
    expect(baseline.value).toBe(-12000)
    expect(lines.reduce((s, l) => s + l.value, 0)).toBeCloseTo(19624.73, 2)
    expect(lines.some((l) => l.key === 'other')).toBe(false)
  })

  it('a negative term carries its own sign — an overdraft or a negative baseline never reads "+"', () => {
    // Cash is Σ SIGNED balances and the cutover baseline is unclamped; the amount renders
    // unsigned beside the glyph, so a structural '+' on −500 would contradict the total below.
    const lines = buildRtaLines({
      ...BOOK,
      onBudgetCash: -500,
      baseline: -250,
      rta: -500 - 458.17 - 5582.62 + 250,
    })
    expect(lines.find((l) => l.key === 'cash')).toMatchObject({ value: -500, op: '−' })
    expect(lines.find((l) => l.key === 'baseline')).toMatchObject({ value: 250, op: '+' })
    expect(lines.reduce((s, l) => s + l.value, 0)).toBeCloseTo(-6290.79, 2)
    expect(lines.some((l) => l.key === 'other')).toBe(false)
  })

  it('shows Assigned in future months only when money is parked ahead', () => {
    expect(buildRtaLines(BOOK).some((l) => l.key === 'future')).toBe(false)
    const lines = buildRtaLines({ ...BOOK, assignedInFuture: 500, rta: 31124.73 })
    expect(lines.find((l) => l.key === 'future')!.value).toBe(-500)
  })

  // Colour marks STATUS, not arithmetic: every term below cash is a subtraction, but only card
  // spending nothing covered is a PROBLEM. Painting them all `--bad` read as "two problems"
  // when money the envelopes hold is the healthy, intended state.
  it('tones only the actionable terms — a plain subtraction carries no tone at all', () => {
    const lines = buildRtaLines({ ...BOOK, baseline: 12000, assignedInFuture: 500, rta: 19124.73 })
    expect(lines.map((l) => [l.key, l.tone])).toEqual([
      ['cash', undefined],
      ['held', undefined],
      ['cardspend', 'warn'],
      ['baseline', undefined],
      ['future', undefined],
    ])
  })

  // The label has to read as an anomaly, not as one more term of the identity — a user seeing
  // "Other −$40" reads it exactly like "Baseline −$40" and has nothing to act on.
  it('an "Unexplained" line plus a console warning is the ONLY way a stale term can hide', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const lines = buildRtaLines({ ...BOOK, rta: BOOK.rta + 5 })
    const other = lines.find((l) => l.key === 'other')!
    expect(other.label).toBe('Unexplained — refresh')
    expect(other.value).toBeCloseTo(5, 2)
    expect(other.tone).toBe('warn')
    expect(warn).toHaveBeenCalledOnce()
  })

  // Privacy mode and a screen-share must not be bypassed via devtools: the gap is a bug report,
  // the balances that produced it are the user's money.
  it('logs the residual ALONE — never the balances behind it', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    buildRtaLines({ ...BOOK, rta: BOOK.rta + 5 })
    const payload = warn.mock.calls[0]?.[1] as object
    expect(Object.keys(payload)).toEqual(['residual'])
    expect(JSON.stringify(warn.mock.calls)).not.toContain('37665.52')
  })

  it('tolerates sub-cent float dust without inventing a line', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    expect(buildRtaLines({ ...BOOK, rta: BOOK.rta + 0.004 }).some((l) => l.key === 'other')).toBe(
      false,
    )
    expect(warn).not.toHaveBeenCalled()
  })
})

const HERO_PROPS = {
  monthLabel: 'September 2026',
  heldInEnvelopes: BOOK.heldInEnvelopes,
  heldOutsideEnvelopes: BOOK.heldOutsideEnvelopes,
  cardSpendNotCovered: BOOK.cardSpendNotCovered,
  baseline: 0,
  assignedInFuture: 0,
  underfundedCount: 0,
  onFundUnderfunded: () => {},
}

describe('RtaHero — the breakdown popover', () => {
  it('spells the identity out: the terms in order and the total they reach', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    render(
      createElement(RtaHero, {
        ...HERO_PROPS,
        rta: BOOK.rta,
        onBudgetCash: BOOK.onBudgetCash,
      }),
    )
    fireEvent.click(screen.getByRole('button', { name: /Ready to Assign/ }))
    const panel = document.getElementById('pl-rtapop-panel')!
    expect(panel.textContent).toContain('Cash on hand')
    expect(panel.textContent).toContain('Held in envelopes')
    expect(panel.textContent).toContain('Card spending not covered this month')
    expect(panel.textContent).not.toContain('Inflows')
    expect(panel.textContent).not.toContain('Left over from')
    expect(panel.querySelector('.pl-rtapop-total')!.textContent).toContain('31,624.73')
    expect(warn).not.toHaveBeenCalled()
  })

  // The glyph column carries the sign, so the amount beside it is unsigned — a subtraction used
  // to print "−  Held in envelopes  −S$458.17", two minuses for one fact.
  it('signs each term ONCE, in the glyph column, and leaves the amount unsigned', () => {
    render(
      createElement(RtaHero, { ...HERO_PROPS, rta: BOOK.rta, onBudgetCash: BOOK.onBudgetCash }),
    )
    fireEvent.click(screen.getByRole('button', { name: /Ready to Assign/ }))
    const rows = [...document.querySelectorAll('#pl-rtapop-panel .pl-rtapop-row')]
    expect(rows.map((r) => r.querySelector('.op')!.textContent)).toEqual(['+', '−', '−'])
    expect(rows.map((r) => r.querySelector('.val')!.textContent)).toEqual([
      'S$37,665.52',
      'S$458.17',
      'S$5,582.62',
    ])
  })

  // Only the term the user can act on is tinted; what the envelopes hold is the healthy,
  // intended state and must not read as the same severity as card spending nothing covered.
  it('paints only uncovered card spending as a warning, never the plain subtractions', () => {
    render(
      createElement(RtaHero, { ...HERO_PROPS, rta: BOOK.rta, onBudgetCash: BOOK.onBudgetCash }),
    )
    fireEvent.click(screen.getByRole('button', { name: /Ready to Assign/ }))
    const warned = [...document.querySelectorAll('#pl-rtapop-panel .pl-rtapop-row.warn')]
    expect(warned.map((r) => r.querySelector('.lbl')!.textContent)).toEqual([
      'Card spending not covered this month',
    ])
    expect(document.querySelectorAll('#pl-rtapop-panel .pl-rtapop-row .val.neg')).toHaveLength(0)
  })
})

// UI-design lane, 2026-09-09: "Held in envelopes" is the one term that can flip sign, and when
// it does the popover shows a '+' beside a label that reads like a subtraction, with nothing on
// screen to explain it. One inline line under that row — never a hover tooltip, which would hide
// the reason from exactly the reader who needs it.
describe('RtaHero — the net-overspent credit explains itself', () => {
  const OVERSPENT = {
    ...HERO_PROPS,
    heldInEnvelopes: -240.5,
    heldOutsideEnvelopes: 0,
    cardSpendNotCovered: 0,
    onBudgetCash: 1000,
    rta: 1240.5,
  }

  function openPanel(props: Record<string, unknown>) {
    render(createElement(RtaHero, props as never))
    fireEvent.click(screen.getByRole('button', { name: /Ready to Assign/ }))
    return document.getElementById('pl-rtapop-panel')!
  }

  it('explains the + on Held in envelopes, right under that row', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const panel = openPanel(OVERSPENT)
    const held = [...panel.querySelectorAll('.pl-rtapop-row')].find(
      (r) => r.querySelector('.lbl')!.textContent === 'Held in envelopes',
    )!
    expect(held.querySelector('.op')!.textContent).toBe('+')
    const note = panel.querySelector('.pl-rtapop-note')!
    expect(note.textContent).toBe(
      'Envelopes are net overspent this month — that lends Ready to Assign room back until the roll charges the hole to next month.',
    )
    // Immediately after the row it explains, not parked at the bottom of the panel.
    expect(held.nextElementSibling).toBe(note)
    expect(warn).not.toHaveBeenCalled()
  })

  it('explains nothing per-row when the envelopes are holding money, or holding exactly nothing', () => {
    // Scoped to the PER-ROW explanatory notes (HELD_CREDIT_NOTE/HELD_OUTSIDE_NOTE) — the
    // always-on coaching line/info banner (owner ask, 2026-09-14) are separate, unconditional
    // `.pl-rtapop-note`/`.pl-rtapop-banner` elements that legitimately render whenever
    // RTA > 0 regardless of what the envelopes are holding, so this no longer asserts zero
    // notes total.
    const panelA = openPanel({ ...HERO_PROPS, rta: BOOK.rta, onBudgetCash: BOOK.onBudgetCash })
    expect(panelA.textContent).not.toContain('Envelopes are net overspent this month')
    expect(panelA.textContent).not.toContain('Spending with no envelope is held')
    cleanup()
    const panelB = openPanel({
      ...HERO_PROPS,
      heldInEnvelopes: 0,
      heldOutsideEnvelopes: 0,
      cardSpendNotCovered: 0,
      onBudgetCash: 1000,
      rta: 1000,
    })
    expect(panelB.textContent).not.toContain('Envelopes are net overspent this month')
    expect(panelB.textContent).not.toContain('Spending with no envelope is held')
  })
})

// MATH-22 regression: the RTA hero used the whole-dollar `fBase` while the breakdown popover
// (and the Overview's "Safe to spend" widget, reading the same server field) keeps cents — a
// sub-dollar RTA like 0.40 read as "S$0" here, i.e. fully assigned, and "S$0.40" everywhere else.
describe('RtaHero — cents-accurate headline (MATH-22)', () => {
  it('shows cents for a sub-dollar RTA in the plain figure (cash data not ready)', () => {
    render(
      createElement(RtaHero, {
        ...HERO_PROPS,
        rta: 0.4,
        onBudgetCash: null,
        heldInEnvelopes: 0,
        heldOutsideEnvelopes: 0,
      }),
    )
    expect(screen.getByText(/0\.40/)).toBeTruthy()
  })

  it('shows cents for a sub-dollar RTA on the interactive trigger button', () => {
    render(
      createElement(RtaHero, {
        ...HERO_PROPS,
        rta: 0.4,
        onBudgetCash: 0.4,
        heldInEnvelopes: 0,
        heldOutsideEnvelopes: 0,
        cardSpendNotCovered: 0,
      }),
    )
    const btn = screen.getByRole('button', { name: /Ready to Assign/ })
    expect(btn.textContent).toMatch(/0\.40/)
  })
})
