import { describe, it, expect, afterEach } from 'vitest'
import * as PD from './planData'
import { fBaseCompact, setBaseCurrency, setDisplayFormats } from '../../../utils/format'

// Outlook tiles (fCoarse) and Cash Flow figures (fCF) once stopped at M, so a VND plan read
// "₫24946.08M" beside a chart axis saying "₫47.09B". From a million up they are the axis's own
// formatter, in every number format, so the two can never disagree again.
describe('fCoarse / fCF — big amounts read exactly like the chart axis', () => {
  afterEach(() => {
    setBaseCurrency('SGD')
    setDisplayFormats({})
  })

  it('reads billions as B, never thousands of millions', () => {
    setBaseCurrency('VND')
    setDisplayFormats({ number_format: 'comma' })

    expect(PD.fCoarse(24_946_080_000)).toBe('₫24.95B')
    expect(PD.fCF(7_500_000_000)).toBe('₫7.5B')
    expect(PD.fCoarse(1_436_033_610_000)).toBe('₫1.44T')
  })

  it.each(['comma', 'dot', 'space'] as const)(
    'matches fBaseCompact in the %s number format, sign included',
    (number_format) => {
      setBaseCurrency('VND')
      setDisplayFormats({ number_format })
      for (const v of [1_000_000, 24_946_080_000, 7_500_000_000, 999_995_000]) {
        expect(PD.fCoarse(v)).toBe(fBaseCompact(v))
        expect(PD.fCF(v)).toBe(fBaseCompact(v))
        expect(PD.fCoarse(-v)).toBe('−' + fBaseCompact(v))
      }
    },
  )

  it('a figure that rounds up to a million reads as that million, not 1000k / 1000.00K', () => {
    setDisplayFormats({ number_format: 'comma' })
    const million = fBaseCompact(1_000_000)

    expect(PD.fCoarse(996_400)).toBe(million)
    expect(PD.fCoarse(994_000)).toBe(PD.fCoarse(990_000))
    expect(PD.fCoarse(994_000)).toMatch(/990k$/)
    expect(PD.fCF(999_996)).toBe(million)
    expect(PD.fCF(999_994)).toMatch(/999\.99K$/)
  })

  it('keeps the small-amount rules', () => {
    setDisplayFormats({ number_format: 'comma' })

    expect(PD.fCoarse(307_800)).toMatch(/310k$/)
    expect(PD.fCF(12_345)).toMatch(/12\.35K$/)
    expect(PD.fCF(-450)).toMatch(/^−.*450$/)
  })
})

// Every Plan money input parses through this. 50/30/20's cap cell once stripped the decimal
// point, so clicking into an S$83.33 cap and away saved S$8,333 (audit, 2026-09-25).
describe('parseMoneyInput', () => {
  it('keeps the decimal point — "83.33" is 83.33, never 8333', () => {
    expect(PD.parseMoneyInput('83.33')).toBe(83.33)
    expect(PD.parseMoneyInput('12.50')).toBe(12.5)
    expect(PD.parseMoneyInput('S$1,250.5')).toBe(1250.5)
  })

  it('rounds to cents, and blank, garbage or negative input is 0 — never NaN', () => {
    expect(PD.parseMoneyInput('10.005')).toBe(10.01)
    expect(PD.parseMoneyInput('')).toBe(0)
    expect(PD.parseMoneyInput('abc')).toBe(0)
    expect(PD.parseMoneyInput('-40')).toBe(40) // the sign is not a digit: magnitude only
  })
})

// MATH-20 regression: weeksInMonth must count the TARGET's due weekday, not always Monday —
// mirrors backend `_weeks_in_month(month, weekday)` (services/planning.py:1428-1444), which
// counts `date.weekday()` occurrences (0=Monday..6=Sunday, default 0). 2026-01 has 5 Fridays
// but only 4 Mondays, so a Friday-due weekly target and a Monday-due one must disagree.
describe('weeksInMonth — counts the chosen due weekday, not always Monday (MATH-20)', () => {
  it('2026-01: 5 Fridays (due_weekday=4), 4 Mondays (due_weekday=0 / default)', () => {
    expect(PD.weeksInMonth('2026-01', 4)).toBe(5)
    expect(PD.weeksInMonth('2026-01', 0)).toBe(4)
    expect(PD.weeksInMonth('2026-01')).toBe(4) // unset → Monday, matches the server's default
  })
})

// The third YNAB target flavor: 'balance' ("Have a balance of $X by [date]" — the by_date
// savings goal). It measures the Available balance like 'refill', but is NEVER "overfunded"
// (reaching the goal early is just done, not a surplus to move away).
describe('statusOf — the "have a balance of" (balance) flavor', () => {
  it('measures Available, not this-month Assigned: fully funded when available covers the target', () => {
    // assigned this month is 0, but the rolled-over available balance already meets the goal.
    const r = PD.statusOf(0, 0, 1000, 1000, 'balance')
    expect(r.status).toBe('funded')
    expect(r.needed).toBe(0)
  })

  it("underfunded by the shortfall to the target balance (before this month's spending)", () => {
    // available 600, spent 0 this month → funded-this-month 600, target 1000 → 400 short.
    const r = PD.statusOf(0, 0, 600, 1000, 'balance')
    expect(r.status).toBe('underfunded')
    expect(r.needed).toBeCloseTo(400, 5)
  })

  it('is NEVER overfunded — a savings goal at or above target is just funded (unlike refill)', () => {
    expect(PD.statusOf(0, 0, 1500, 1000, 'balance').status).toBe('funded')
    // the same numbers under 'refill' DO read as overfunded — proves the flavors diverge.
    expect(PD.statusOf(0, 0, 1500, 1000, 'refill').status).toBe('overfunded')
  })

  it('a negative available is overspent regardless of flavor', () => {
    expect(PD.statusOf(0, -50, -50, 1000, 'balance').status).toBe('overspent')
  })
})

// MATH-28 regression: a missing date_of_birth reports `current_age: null` from /api/fire
// (never a fabricated 0 — see backend/services/fire.py's `_current_age`). `useOutlookFire`
// degrades that to `undefined` before calling makeFireConfig (FireConfig.currentAge stays a
// definite number throughout the tab's Monte-Carlo/trajectory math) — so the assertion here
// is on the boundary makeFireConfig itself owns: an omitted/undefined field never overwrites
// the persona default with 0 or NaN, whatever produced the gap.
describe('makeFireConfig — an unknown current age never becomes a fabricated 0 (MATH-28)', () => {
  it('an undefined currentAge (the degraded form of an unknown DOB) keeps the default age', () => {
    const cfg = PD.makeFireConfig({ currentAge: undefined, netWorth: 250_000 })
    expect(cfg.currentAge).toBe(PD.FIRE.currentAge)
    expect(cfg.currentAge).not.toBe(0)
  })

  it('a real currentAge from a known date of birth is honored', () => {
    const cfg = PD.makeFireConfig({ currentAge: 45 })
    expect(cfg.currentAge).toBe(45)
  })
})

// Owner, 2026-09-25: the per-envelope "Underfunded" quick-assign takes whichever is bigger —
// what the target still needs, or the overspending — in ONE click.
describe('envelopeUnderfunded', () => {
  it('target bigger than the spend: funds the target (which also clears the spend)', () => {
    // S$300 set-aside target, nothing assigned, S$235.99 spent → S$300 to go, S$235.99 overspent.
    expect(PD.envelopeUnderfunded(300, -235.99)).toBe(300)
  })
  it('spend bigger than the target: clears the whole overspend, not just the target', () => {
    // S$300 target, nothing assigned, S$400 spent → S$300 to go, S$400 overspent.
    expect(PD.envelopeUnderfunded(300, -400)).toBe(400)
  })
  it('no target: covers the overspend; nothing overspent and nothing to go is zero', () => {
    expect(PD.envelopeUnderfunded(0, -64.74)).toBe(64.74)
    expect(PD.envelopeUnderfunded(0, 12)).toBe(0)
  })
  it('on target and in the black: only what the target still needs', () => {
    expect(PD.envelopeUnderfunded(64.01, 0)).toBe(64.01)
  })
})

// The click must use the target's own to-go even when the row is overspent (its `needed` is 0
// then) — the bug behind "it covers the spent part first, then the budget".
describe('targetProgress', () => {
  it('an overspent set-aside row still reports what the target needs', () => {
    const c = {
      target: 300,
      tt: 'set_aside',
      cad: 'monthly',
      assigned: 0,
      activity: -235.99,
      available: -235.99,
    }
    expect(PD.targetProgress(c).toGo).toBe(300)
    expect(PD.envelopeUnderfunded(PD.targetProgress(c).toGo, c.available)).toBe(300)
  })
  it('refill measures the balance funded before spending', () => {
    const c = {
      target: 300,
      tt: 'refill',
      cad: 'monthly',
      assigned: 0,
      activity: -100,
      available: -50,
    }
    // funded = available - activity = 50 (left over from last month) → 250 to go
    expect(PD.targetProgress(c)).toEqual({ monthlyTarget: 300, funded: 50, toGo: 250 })
  })
  it('no target → nothing to go', () => {
    expect(PD.targetProgress({ assigned: 5.5, activity: -5.5, available: 0 }).toGo).toBe(0)
  })
})
