import { describe, expect, it } from 'vitest'
import { fPct } from '../../../utils/format'
import { ageText, changeDetail, changeSummary, exitedDetail, holdingsAge } from './following.format'

describe('ageText', () => {
  it.each([
    [0, 'today'],
    [1, 'yesterday'],
    [88, '88 days ago'],
    [119, '119 days ago'],
    [120, '4 months ago'],
    [300, '10 months ago'],
    [800, '2 years ago'],
  ])('%i days reads %s', (days, text) => expect(ageText(days)).toBe(text))

  it('says nothing for an unknown age', () => expect(ageText(null)).toBe(''))
})

describe('weights', () => {
  it('fPct takes a 0-1 weight only with fraction', () => {
    expect(fPct(0.224, { fraction: true })).toBe('22.40%')
    expect(fPct(null, { fraction: true })).toBe('—')
  })
})

describe('changeSummary', () => {
  const model = (changes: string[], exited: number) =>
    ({
      holdings: changes.map((change) => ({ change })),
      exited: Array.from({ length: exited }, () => ({})),
    }) as unknown as Parameters<typeof changeSummary>[0]

  it('counts what moved, in a fixed order, and skips zeros', () => {
    expect(changeSummary(model(['new', 'reduced', 'reduced', 'unchanged'], 1))).toBe(
      '1 new · 2 trimmed · 1 sold out',
    )
  })

  it('is null when nothing moved', () => {
    expect(changeSummary(model(['unchanged'], 0))).toBeNull()
  })
})

describe('changeDetail', () => {
  type H = Parameters<typeof changeDetail>[0]
  type M = Parameters<typeof changeDetail>[1]
  const model = { as_of: '2026-06-30', prev_as_of: '2026-03-31' } as M
  const holding = (over: Partial<H>) =>
    ({ symbol: 'ACME', name: 'Acme Corp', weight: 0.1, prev_weight: 0.1, ...over }) as H

  it('gives a fund move as shares, percent and an approximate value, with the period behind it', () => {
    const d = changeDetail(
      holding({
        change: 'reduced',
        shares: 1_000_000,
        prev_shares: 1_500_000,
        shares_change: -500_000,
        shares_change_pct: -1 / 3,
        price: 30,
        change_value: -15_000_000,
      }),
      model,
    )!
    expect(d.line).toBe('−500K shares (-33.33%) · about $15M')
    expect(d.title).toContain('from 1.5M to 1M shares')
    expect(d.title).toContain('no trade dates or trade prices')
    expect(d.title).toMatch(/^Between .*2026 and .*2026:/)
  })

  it('has no percent for a new holding and nothing for one that did not move', () => {
    const fresh = changeDetail(
      holding({
        change: 'new',
        shares: 200,
        prev_shares: 0,
        shares_change: 200,
        shares_change_pct: null,
      }),
      model,
    )!
    expect(fresh.line).toBe('+200 shares')
    expect(
      changeDetail(
        holding({ change: 'unchanged', shares: 5, prev_shares: 5, shares_change: 0 }),
        model,
      ),
    ).toBeNull()
    expect(changeDetail(holding({ change: 'unchanged', shares: 5 }), model)).toBeNull()
  })

  it('gives a member holding its latest trade: a date and a range, never shares', () => {
    const d = changeDetail(
      holding({
        change: 'unchanged',
        last_trade: {
          date: '2026-07-24',
          filed_at: '2026-08-21',
          side: 'buy',
          amount_low: 1_000_001,
          amount_high: 5_000_000,
        },
      }),
      model,
    )!
    expect(d.line).toMatch(/^Bought .*2026 · \$1M to \$5M$/)
    expect(d.title).toContain('a range, not a share count or a price')
  })
})

describe('exitedDetail', () => {
  it('says how much a fund sold out of', () => {
    expect(
      exitedDetail({
        symbol: 'OXYZ',
        name: 'Oxyz Energy',
        prev_shares: 3_200_000,
        prev_value: 96_000_000,
      }),
    ).toBe(' (all 3.2M shares, about $96M)')
    expect(exitedDetail({ symbol: 'OXYZ', name: 'Oxyz Energy' })).toBe('')
  })
})

describe('holdingsAge', () => {
  it('counts days from the quarter-end the positions date from', () => {
    expect(holdingsAge('2026-06-30', new Date('2026-11-11T12:00:00'))).toBe('134 days old')
    expect(holdingsAge('2026-11-11', new Date('2026-11-11T12:00:00'))).toBe('')
    expect(holdingsAge('nonsense')).toBe('')
  })
})
