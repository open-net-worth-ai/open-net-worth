// @vitest-environment jsdom

import { readFileSync, readdirSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { maskFigures, splitFigures, subscribeAmountsHidden } from './amountMask'

const figures = (text: string) =>
  splitFigures(text)
    .filter((p) => p.figure)
    .map((p) => p.text)

describe('finding the figures in a sentence', () => {
  it.each([
    ['NET vests in 40 days ($13,723.50 indicative).', ['$13,723.50']],
    ['Your book is −$4,244.48 (-1.64%) today', ['−$4,244.48', '-1.64%']],
    ['Visa: S$6,276.57 due in 3 days', ['S$6,276.57']],
    ['HK$12.5K left', ['HK$12.5K']],
    ['€2.3K left', ['€2.3K']],
    ['1.234,56 € held', ['1.234,56 €']],
    ['CHF 1,200.00 owed', ['CHF 1,200.00']],
    ['ACME is 44% of investments', ['44%']],
    ['equity 89.5% → trim toward 68%', ['89.5%', '68%']],
    ['NET • 82 units', ['82 units']],
    ['1,200.5 shares held', ['1,200.5 shares']],
  ])('%s', (text, expected) => {
    expect(figures(text)).toEqual(expected)
  })

  it.each(['in 40 days', '3 asset classes off target', 'Q3 2026', 'retiring at 60', 'S&P 500'])(
    'leaves "%s" alone',
    (text) => {
      expect(figures(text)).toEqual([])
    },
  )

  it('keeps every word, in order', () => {
    const text = 'Your book is −$4,244.48 (-1.64%) today'
    expect(
      splitFigures(text)
        .map((p) => p.text)
        .join(''),
    ).toBe(text)
  })

  it('masks a native attribute down to its words', () => {
    expect(maskFigures('Ready to Assign, $600.00 — click for breakdown')).toBe(
      'Ready to Assign, *** — click for breakdown',
    )
  })
})

describe('the toggle', () => {
  it('tells a subscriber when it flips, and only then', async () => {
    let flips = 0
    const stop = subscribeAmountsHidden(() => flips++)
    document.body.classList.add('some-other-class')
    await Promise.resolve()
    expect(flips).toBe(0)
    document.body.classList.add('hide-amounts')
    await Promise.resolve()
    expect(flips).toBe(1)
    document.body.classList.remove('hide-amounts')
    await Promise.resolve()
    expect(flips).toBe(2)
    stop()
  })
})

describe('one mask', () => {
  // Three stylesheets once carried their own `.mask`, drawing `••••` where the rest of the app
  // drew `***`, and which one won depended on the screens opened so far.
  it('is defined only in index.css', () => {
    const src = resolve(__dirname, '..')
    const defining: string[] = []
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const path = join(dir, entry.name)
        if (entry.isDirectory()) walk(path)
        else if (
          entry.name.endsWith('.css') &&
          /body\.hide-amounts \.(mask|money)\b[^\n]*[,{]\n/.test(readFileSync(path, 'utf8'))
        )
          defining.push(path.slice(src.length + 1))
      }
    }
    walk(src)
    expect(defining).toEqual(['index.css'])
  })
})
