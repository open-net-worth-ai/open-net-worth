import { describe, expect, it } from 'vitest'
import { fPct } from '../../../utils/format'
import { ageText, changeSummary } from './following.format'

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
