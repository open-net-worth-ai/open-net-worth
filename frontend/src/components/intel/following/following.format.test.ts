import { describe, expect, it } from 'vitest'
import { fPct } from '../../../utils/format'
import { ageText, companyName } from './following.format'

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

describe('companyName', () => {
  it('drops the share-class tail and EDGAR suffixes', () => {
    expect(companyName('Acme Corp. - Class A Common Stock')).toBe('Acme Corp')
    expect(companyName('Acme Holdings, Inc. - Common Stock')).toBe('Acme Holdings, Inc')
    expect(companyName('Acme Corp /New/')).toBe('Acme Corp')
    expect(companyName('Acme Class A Common Stock')).toBe('Acme')
    expect(companyName('Acme Inc.')).toBe('Acme Inc')
    expect(companyName('-')).toBe('-')
  })
})
