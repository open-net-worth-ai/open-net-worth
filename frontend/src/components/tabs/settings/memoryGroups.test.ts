import { describe, expect, it } from 'vitest'
import { daysUntilForgotten, topicsOf } from './memoryGroups'

describe('daysUntilForgotten', () => {
  const today = '2026-10-27'

  it.each([
    ['2026-10-27T23:59:00Z', 30],
    ['2026-09-28T00:00:00Z', 1],
    ['2026-09-27T08:00:00Z', 0],
    ['2026-08-01T00:00:00Z', 0],
  ])('%s leaves %i days (whole calendar days, like the purge)', (at, left) => {
    expect(daysUntilForgotten(at, today)).toBe(left)
  })

  it('counts 0, 29 and 30 days since it moved', () => {
    expect(daysUntilForgotten('2026-10-27', today)).toBe(30)
    expect(daysUntilForgotten('2026-09-28', today)).toBe(1)
    expect(daysUntilForgotten('2026-09-27', today)).toBe(0)
  })

  it('shows nothing without a date', () => {
    expect(daysUntilForgotten(null, today)).toBeNull()
    expect(daysUntilForgotten('', today)).toBeNull()
  })
})

describe('topicsOf', () => {
  it('merges spellings that differ in case or spacing, showing the first name seen', () => {
    const topics = topicsOf([
      { id: 'a', content: 'x', topic: 'Housing' },
      { id: 'b', content: 'y', topic: '  housing ' },
    ])
    expect(topics).toHaveLength(1)
    expect(topics[0]!.name).toBe('Housing')
    expect(topics[0]!.facts).toHaveLength(2)
  })
})
