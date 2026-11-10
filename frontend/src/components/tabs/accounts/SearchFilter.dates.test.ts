// Date-search parsing for the Accounts register filter (YNAB-style, smart/unambiguous).
import { describe, it, expect } from 'vitest'
import { parseDateInput, dateSuggestions, parseSegment, buildSuggestions } from './SearchFilter'

const SUGGEST = { categories: [], payees: [], accounts: [], tags: [] }
const Y = new Date().getFullYear()

describe('parseDateInput — accepted forms', () => {
  it('ISO day', () => {
    const d = parseDateInput('2026-06-01')!
    expect(d.kind).toBe('day')
    expect([d.prefix, d.start, d.end]).toEqual(['2026-06-01', '2026-06-01', '2026-06-01'])
    expect(d.display).toBe('Jun 1, 2026')
  })
  it('ISO month spans the whole month', () => {
    const d = parseDateInput('2026-06')!
    expect(d.kind).toBe('month')
    expect([d.prefix, d.start, d.end]).toEqual(['2026-06', '2026-06-01', '2026-06-30'])
    expect(d.display).toBe('Jun 2026')
  })
  it('month name + year', () => {
    expect(parseDateInput('june 2026')!.prefix).toBe('2026-06')
    expect(parseDateInput('Jun 2026')!.display).toBe('Jun 2026')
  })
  it('month name + day (+ optional year)', () => {
    expect(parseDateInput('jun 6')!.prefix).toBe(`${Y}-06-06`)
    expect(parseDateInput('june 6, 2026')!.prefix).toBe('2026-06-06')
    expect(parseDateInput('6 jun 2026')!.prefix).toBe('2026-06-06')
  })
  it('bare year spans Jan 1 – Dec 31', () => {
    const d = parseDateInput('2026')!
    expect([d.kind, d.start, d.end]).toEqual(['year', '2026-01-01', '2026-12-31'])
  })
  it('bare month number → that month of the current year', () => {
    expect(parseDateInput('6')!.prefix).toBe(`${Y}-06`)
    expect(parseDateInput('06')!.prefix).toBe(`${Y}-06`)
  })
})

describe('parseDateInput — rejected / non-date', () => {
  it.each(['hello', 'food', '13', '0', '2026-13', '2026-06-31', 'junx', '1500'])(
    '%s → null',
    (s) => {
      expect(parseDateInput(s)).toBeNull()
    },
  )
})

describe('dateSuggestions', () => {
  it('emits On / After / Before for a month', () => {
    const rows = dateSuggestions(parseDateInput('2026-06')!, 'any')
    expect(rows.map((r) => [r.field, r.value, r.label])).toEqual([
      ['date_on', '2026-06', 'In Jun 2026'],
      ['date_after', '2026-06-01', 'On or after Jun 1, 2026'],
      ['date_before', '2026-06-30', 'On or before Jun 30, 2026'],
    ])
  })
  it('a single mode emits one row', () => {
    expect(dateSuggestions(parseDateInput('2026-06-01')!, 'before')).toEqual([
      expect.objectContaining({
        field: 'date_before',
        value: '2026-06-01',
        label: 'On or before Jun 1, 2026',
      }),
    ])
  })
})

describe('parseSegment — date scopes + bare date', () => {
  it('before: / after: / on: produce the matching token', () => {
    expect(parseSegment('before: 2026-06', SUGGEST, '$')).toMatchObject({
      field: 'date_before',
      value: '2026-06-30',
    })
    expect(parseSegment('after: 2026-06', SUGGEST, '$')).toMatchObject({
      field: 'date_after',
      value: '2026-06-01',
    })
    expect(parseSegment('on: jun 6 2026', SUGGEST, '$')).toMatchObject({
      field: 'date_on',
      value: '2026-06-06',
    })
  })
  it('a bare month-name date commits as an On token', () => {
    expect(parseSegment('June 2026', SUGGEST, '$')).toMatchObject({
      field: 'date_on',
      value: '2026-06',
    })
  })
  it('bare digits stay text (ambiguous) — not a date token', () => {
    expect(parseSegment('06', SUGGEST, '$')).toMatchObject({ field: 'text', value: '06' })
  })
})

describe('buildSuggestions — date rows', () => {
  it('unscoped date-like input offers all three date rows', () => {
    const fields = buildSuggestions('2026-06', SUGGEST, '$').map((r) => r.field)
    expect(fields).toEqual(expect.arrayContaining(['date_on', 'date_after', 'date_before']))
  })
  it('a date scope narrows to that one row', () => {
    const rows = buildSuggestions('after: jun 2026', SUGGEST, '$')
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({ field: 'date_after', value: '2026-06-01' })
  })
  it('non-date text offers no date rows', () => {
    expect(buildSuggestions('hello', SUGGEST, '$').some((r) => r.field.startsWith('date_'))).toBe(
      false,
    )
  })
})

describe('parseSegment — status:hidden', () => {
  it('turns status:hidden into the Hidden token', () => {
    expect(parseSegment('status: hidden', SUGGEST, '$')).toMatchObject({
      field: 'hidden',
      value: true,
      label: 'Hidden',
    })
  })
})
