// Fresh-start copy — the labels the kebab item, the state line and the undo pill share. Pure,
// so the menu can never disagree with the line under the hero about which month is the floor.
import { describe, it, expect, vi, afterEach } from 'vitest'
import {
  freshStartConsequencesLabel,
  freshStartLedger,
  freshStartMenuLabel,
  freshStartToastLabel,
  monthName,
  resumeHistoryLabel,
  shortMonthLabel,
  type FreshStartPreview,
} from './freshStart'

afterEach(() => vi.restoreAllMocks())

describe('freshStartMenuLabel', () => {
  it('offers the CURRENT month, formatted like the month nav', () => {
    expect(freshStartMenuLabel('2026-09', null)).toBe('Start fresh from September 2026…')
  })

  it('reads past tense (no ellipsis) once THIS month is already the floor', () => {
    expect(freshStartMenuLabel('2026-09', '2026-09')).toBe('Started fresh from September 2026')
  })

  it('still offers a fresh start when the floor is an EARLIER month', () => {
    expect(freshStartMenuLabel('2026-09', '2026-06')).toBe('Start fresh from September 2026…')
  })
})

describe('the state line + dialog copy', () => {
  it('abbreviates the month for the muted one-liner', () => {
    expect(shortMonthLabel('2026-09')).toBe('Sep 2026')
  })

  it("names the month for the dialog's copy", () => {
    expect(monthName('2026-09')).toBe('September')
  })

  it('states every consequence of confirming in one closing paragraph', () => {
    expect(freshStartConsequencesLabel('2026-09')).toBe(
      "Envelopes start over from September — this month's assignments are cleared. This month's " +
        'spending still shows in its envelopes. Earlier months hide until you resume history. ' +
        'No transactions change — undo any time.',
    )
  })

  // Resume restores the FLOOR only. Undo (7s after confirming) is what brings cleared
  // assignments back — a user who resumes a week later must not expect them.
  it('warns that resuming history does NOT bring cleared assignments back', () => {
    expect(resumeHistoryLabel('2026-09')).toBe(
      'Envelopes carry their balances from before September 2026 again and Ready to Assign is ' +
        'recomputed — no transactions change. Assignments cleared by the fresh start are not ' +
        'restored.',
    )
  })
})

// The owner report of 2026-09-08: the OLD dialog promised a figure S$207.68 below the cash line
// it printed, and nothing on screen accounted for the gap. The fix — kept when the YNAB identity
// was restored (ADR 2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category) — is
// that the ledger renders the SAME terms the RTA popover does: cash on hand, held in envelopes,
// card spending not covered this month, baseline. What it MUST do is add up, EXACTLY, on every
// book, with no residual row (coordinator ruling, 2026-09-08: an earlier version dropped the
// envelope term on the assumption it was always 0, which happened to hold on the books it was
// tried against but was not true by construction — a same-month refund, covered card swipe, or
// overspend breaks it).
describe('freshStartLedger', () => {
  const PREVIEW: FreshStartPreview = {
    month: '2026-09',
    ready_to_assign_after: 44776.13,
    cash_now: 44776.13,
    card_credit: 0,
    held_in_envelopes: 0,
    held_outside_envelopes: 0,
    card_spend_not_covered: 0,
    baseline: 0,
  }

  // ONE builder, shared with the popover: the ledger used to hide the envelope term at zero
  // while the popover always showed it, sign the terms with `fBaseCents`' own minus while the
  // popover used a glyph column, and skip colour entirely — three ways to render one identity.
  // It now IS the popover's rows.
  it('opens on the two backbone terms, signed by the glyph column, values unsigned', () => {
    expect(freshStartLedger(PREVIEW)).toEqual([
      { key: 'cash', label: 'Cash on hand', value: 44776.13, op: '+' },
      { key: 'held', label: 'Held in envelopes', value: 0, op: '−' },
    ])
  })

  it('names what the envelopes hold and subtracts it, right after cash on hand', () => {
    const rows = freshStartLedger({
      ...PREVIEW,
      held_in_envelopes: 30,
      held_outside_envelopes: 0,
      ready_to_assign_after: 44746.13,
    })
    expect(rows.map((r) => [r.label, r.op, r.value])).toEqual([
      ['Cash on hand', '+', 44776.13],
      ['Held in envelopes', '−', -30],
    ])
  })

  // ADR 2026-09-09: a same-month overspend the envelope HOLDS makes the term negative even at
  // the hypothetical floor, and the row then ADDS — putting the promised figure above the cash
  // line it sits under. That is the truth of a held overspend, so the ledger states it.
  it('flips the envelope row to + when the month is left net overspent', () => {
    const rows = freshStartLedger({
      ...PREVIEW,
      held_in_envelopes: -30,
      held_outside_envelopes: 0,
      ready_to_assign_after: 44806.13,
    })
    expect(rows.map((r) => [r.label, r.op, r.value])).toEqual([
      ['Cash on hand', '+', 44776.13],
      ['Held in envelopes', '+', 30],
    ])
    expect(rows.some((r) => r.key === 'other')).toBe(false)
  })

  it('names uncovered card spending, subtracts it, and marks it the actionable term', () => {
    const rows = freshStartLedger({
      ...PREVIEW,
      card_spend_not_covered: 207.68,
      ready_to_assign_after: 44568.45,
    })
    expect(rows.map((r) => [r.label, r.value])).toEqual([
      ['Cash on hand', 44776.13],
      ['Held in envelopes', 0],
      ['Card spending not covered this month', -207.68],
    ])
    expect(rows.map((r) => r.tone)).toEqual([undefined, undefined, 'warn'])
  })

  it('names the baseline and subtracts it too, in order after the card term', () => {
    const rows = freshStartLedger({
      ...PREVIEW,
      held_in_envelopes: 30,
      held_outside_envelopes: 0,
      card_spend_not_covered: 207.68,
      baseline: 1000,
      ready_to_assign_after: 43538.45,
    })
    expect(rows.map((r) => r.label)).toEqual([
      'Cash on hand',
      'Held in envelopes',
      'Card spending not covered this month',
      'Baseline',
    ])
    expect(rows.find((r) => r.label === 'Baseline')?.value).toBe(-1000)
  })

  it('names money assigned in future months ONLY when the server sends it', () => {
    const rows = freshStartLedger({
      ...PREVIEW,
      assigned_in_future: 10,
      ready_to_assign_after: 44766.13,
    })
    expect(rows.map((r) => [r.label, r.value])).toEqual([
      ['Cash on hand', 44776.13],
      ['Held in envelopes', 0],
      ['Assigned in future months', -10],
    ])
  })

  // A fresh start CLEARS the month's OWN assignments, so there is nothing assigned FOR THIS
  // MONTH left to subtract — that row would be a lie about what the promised figure is made
  // of. "Assigned in future months" is a different fact (a LATER month's own assignments) and
  // is legitimately named above.
  it('never shows a this-month "assigned" row', () => {
    const rows = freshStartLedger(PREVIEW)
    expect(rows.some((r) => r.label === 'Already assigned')).toBe(false)
  })

  // Cash and what the envelopes hold are the identity's backbone and always render; the other
  // three are terms most books never have, and rows of "− S$0.00" are noise, not transparency.
  it('hides the optional zero terms — a plain fresh start is two rows, not five', () => {
    expect(freshStartLedger(PREVIEW).map((r) => r.key)).toEqual(['cash', 'held'])
  })

  // The dialog promises a figure; these rows are its proof. A gap can only be staleness, and it
  // gets the same named, warned defect row the popover shows — never a silent rounding fudge.
  it('adds up to the promised figure, or says so in a row the user can see', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const rows = freshStartLedger({ ...PREVIEW, ready_to_assign_after: 44776.13 - 12 })
    expect(rows.at(-1)).toEqual({
      key: 'other',
      label: 'Unexplained — refresh',
      value: -12,
      op: '−',
      tone: 'warn',
    })
    expect(rows.reduce((s, r) => s + r.value, 0)).toBeCloseTo(44764.13, 2)
    expect(warn).toHaveBeenCalledOnce()
  })
})

describe('freshStartToastLabel', () => {
  // Full month name, both directions: the pill widens on a phone (`.pl-undo` @ ≤640px) rather
  // than the sentence being abbreviated to fit — owner decision.
  it('names the month a fresh start begins', () => {
    expect(freshStartToastLabel('2026-09', '2026-09')).toBe(
      'Budget starts fresh from September 2026',
    )
  })

  it('reports the inverse write plainly — clearing the floor is not "fresh from null"', () => {
    expect(freshStartToastLabel('2026-09', null)).toBe('Carrying earlier history again')
  })
})
