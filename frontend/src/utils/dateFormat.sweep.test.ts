import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { fMonthDay, fShortDate, setDisplayFormats } from './format'
import { renderMaskableSublabel } from './mentionable'

afterEach(() => setDisplayFormats({ date_format: null }))

describe('compact dates follow the Date-format setting', () => {
  it.each([
    ['dmy', '02/06'],
    ['mdy', '06/02'],
  ])('fMonthDay in %s', (fmt, expected) => {
    setDisplayFormats({ date_format: fmt })
    expect(fMonthDay('2026-06-02')).toBe(expected)
  })

  it('fMonthDay is empty for nothing', () => {
    expect(fMonthDay(undefined)).toBe('')
  })

  it('fShortDate orders day and month by the setting', () => {
    setDisplayFormats({ date_format: 'dmy' })
    expect(fShortDate('2026-06-02')).toBe('2 Jun')
    setDisplayFormats({ date_format: 'mdy' })
    expect(fShortDate('2026-06-02')).toBe('Jun 2')
    expect(fShortDate('not a date')).toBe('not a date')
  })

  it('a picker sublabel shows its ISO date in the app format', () => {
    setDisplayFormats({ date_format: 'dmy' })
    const nodes = renderMaskableSublabel('Acme • Checking • 2026-06-02 • −10.00 SGD') as unknown[]
    expect(nodes).toContain('2 Jun 2026')
    expect(nodes).not.toContain('2026-06-02')
  })
})

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) return sources(p)
    return /\.(ts|tsx)$/.test(name) && !/\.test\./.test(name) ? [p] : []
  })
}

describe('no screen hand-formats a date', () => {
  const files = sources(join(__dirname, '..'))
  const bad: [string, RegExp][] = [
    ['date.slice(5) month-day', /[Dd]ate\w*[!?]?\.slice\(5\)/],
    ["m + '/' + d", /\bm \+ '\/' \+ d\b/],
  ]
  it.each(bad)('no %s', (_label, re) => {
    const hits = files.filter((f) => re.test(readFileSync(f, 'utf8')))
    expect(hits).toEqual([])
  })
})
