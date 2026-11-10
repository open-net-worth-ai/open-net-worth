// Register search: the Uncategorized (category) and Uncleared / Needs review (status) filters.
import { describe, it, expect } from 'vitest'
import { buildSuggestions, parseSegment } from './SearchFilter'

const UNCATEGORIZED_TOKEN = { field: 'uncategorized', value: true, label: 'Uncategorized' }

const SUGGEST = {
  categories: [{ id: 'cat_food', label: 'Food', color: '#f00' }],
  payees: [],
  accounts: [],
  tags: [],
}
const fields = (draft: string) =>
  buildSuggestions(draft, SUGGEST, '$').map((s) => `${s.field}:${String(s.value)}`)

describe('empty search box offers the status filters', () => {
  it('lists Uncategorized, Uncleared and Needs review before anything is typed', () => {
    expect(fields('')).toEqual(['uncategorized:true', 'cleared:false', 'review:true'])
  })
})

describe('status: narrows by what is typed', () => {
  it('a bare status: lists every status', () => {
    expect(fields('status:')).toEqual([
      'review:true',
      'cleared:true',
      'cleared:false',
      'hidden:true',
    ])
  })

  it.each([
    ['status:cleared', 'cleared:true'],
    ['status:uncl', 'cleared:false'],
    ['status:review', 'review:true'],
    ['status:hid', 'hidden:true'],
  ])('%s offers %s first, so Enter adds it', (draft, first) => {
    expect(fields(draft)[0]).toBe(first)
  })
})

describe('typing finds Uncategorized', () => {
  it.each(['uncat', 'Uncategorized', 'unsorted', 'category:none', 'category:uncat', 'category:'])(
    '%s suggests it',
    (draft) => {
      expect(fields(draft)).toContain('uncategorized:true')
    },
  )

  it('a plain search for "none" does not', () => {
    expect(fields('none')).not.toContain('uncategorized:true')
  })

  it('is a category, not a status', () => {
    expect(fields('status:')).not.toContain('uncategorized:true')
    expect(parseSegment('status:uncategorized', SUGGEST, '$')?.field).not.toBe('uncategorized')
    const pick = buildSuggestions('uncat', SUGGEST, '$').find((s) => s.field === 'uncategorized')
    expect(pick?.pre).toBe('Category')
  })

  it('suggests it once, never twice', () => {
    expect(fields('unsorted').filter((f) => f === 'uncategorized:true')).toHaveLength(1)
  })
})

describe('typed category syntax commits the Uncategorized token', () => {
  it.each(['category:none', 'category:Uncategorized', 'category:unsorted'])('%s', (seg) => {
    expect(parseSegment(seg, SUGGEST, '$')).toEqual(UNCATEGORIZED_TOKEN)
  })

  it('a real category named like the word still wins', () => {
    const withNone = { ...SUGGEST, categories: [{ id: 'cat_none', label: 'None', color: '#000' }] }
    expect(parseSegment('category:none', withNone, '$')?.field).toBe('category')
  })
})
