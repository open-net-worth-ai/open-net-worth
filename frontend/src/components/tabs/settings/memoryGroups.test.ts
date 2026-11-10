import { describe, expect, it } from 'vitest'
import { NO_TOPIC_KEY, groupFacts, summaryOf, topicKey, topicsOf } from './memoryGroups'

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

  it('keeps a fact filed outside Recent whatever date it still carries', () => {
    const g = groupFacts(
      [
        {
          id: 'p',
          content: 'Prefers short answers',
          state: 'live',
          section: 'you',
          topic: 'Preferences',
          expires_at: past,
        },
      ],
      today,
    )
    expect(g.bySection.get('you')).toBeDefined()
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
