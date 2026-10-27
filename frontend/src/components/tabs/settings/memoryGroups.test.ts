import { describe, expect, it } from 'vitest'
import {
  NO_TOPIC_KEY,
  daysUntilForgotten,
  groupFacts,
  retiredReason,
  summaryOf,
  topicKey,
  topicsOf,
} from './memoryGroups'

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

describe('topicsOf — facts with no topic', () => {
  it('keeps them apart from a real topic named "General"', () => {
    const topics = topicsOf([
      { id: 'a', content: 'x', topic: null },
      { id: 'b', content: 'y', topic: 'General' },
    ])
    expect(topics.map((t) => [t.key, t.named]).sort()).toEqual(
      [
        [NO_TOPIC_KEY, false],
        ['general', true],
      ].sort(),
    )
  })
})

describe('groupFacts — expiry agrees with the backend', () => {
  const today = '2026-10-27'
  const past = '2026-10-01'

  it('hides a filed open item past its date whatever its legacy tier', () => {
    const g = groupFacts(
      [
        {
          id: 'o',
          content: 'Call the bank',
          state: 'live',
          section: 'open_items',
          tier: 'durable',
          expires_at: past,
        },
      ],
      today,
    )
    expect(g.bySection.get('open_items')).toBeUndefined()
  })

  it('keeps an unsorted durable fact with a stray past date, hides a time-bound one', () => {
    const g = groupFacts(
      [
        { id: 'd', content: 'Durable', state: 'unsorted', tier: 'durable', expires_at: past },
        { id: 't', content: 'Dated', state: 'unsorted', tier: 'time_bound', expires_at: past },
      ],
      today,
    )
    expect(g.unsorted.map((f) => f.id)).toEqual(['d'])
  })
})

describe('retiredReason', () => {
  it('labels every backend retire reason', () => {
    expect(retiredReason('identifier')).toBe('Not kept — had a card, account or ID number')
    expect(retiredReason('unused')).toBe('Not used in 90 days')
    expect(retiredReason('merged')).toBe('Merged into another memory')
    expect(retiredReason('something-new')).toBe('Out of date')
  })
})

describe('summaryOf', () => {
  const rows = [
    { section: 'money', topic: 'Cards  &  Payments', summary: ' How they pay ', updated_at: '' },
    { section: 'you', topic: 'Profile', summary: 'Who they are', updated_at: '' },
  ]
  it('matches by section and case-folded topic key', () => {
    expect(summaryOf(rows, 'money', 'cards & payments')).toBe('How they pay')
    expect(summaryOf(rows, 'you', 'profile')).toBe('Who they are')
  })
  it('is empty for another section or an unknown topic', () => {
    expect(summaryOf(rows, 'other', 'profile')).toBe('')
    expect(summaryOf(rows, 'you', 'preferences')).toBe('')
  })
})

describe('topicKey', () => {
  it('cuts a long name to 60 characters, as the backend stores it', () => {
    const long = 'Household  budgeting and long-term savings ' + 'x'.repeat(40)
    const key = topicKey(long)
    expect(key).toHaveLength(60)
    expect(key).toBe(('household budgeting and long-term savings ' + 'x'.repeat(40)).slice(0, 60))
    expect(topicKey('A'.repeat(59) + ' B')).toBe('a'.repeat(59))
  })
})
