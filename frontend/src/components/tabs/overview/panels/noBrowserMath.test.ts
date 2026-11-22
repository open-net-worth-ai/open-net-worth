// A panel view draws what its query answers; it never works a figure out. This fails the build
// when a view under panels/ adds, subtracts, multiplies or divides, rounds, sums, sorts, or reads
// the clock — that work belongs in backend/services/dashboard_panels.py. ADR
// 2026-11-22-a-dashboard-widget-is-a-server-query-and-a-view.

import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const DIR = __dirname
// The plumbing (query hook, flag seam, registry, types) holds no figures.
const PLUMBING = new Set(['panel.tsx', 'index.ts', 'types.ts'])

const FORBIDDEN: Array<[RegExp, string]> = [
  [/\bMath\.\w+/, 'Math.* (rounding, min/max: the server rounds and clamps)'],
  [/\bnew Date\(|\bDate\.now\(/, "the clock (the server knows the user's today)"],
  [/\.reduce\(/, 'a fold (the server sums)'],
  [/\.sort\(/, 'a sort (the server orders)'],
  [/\btoFixed\(/, 'rounding (format with utils/format)'],
  [/[\w)\]]\s*[-+*/%]\s*[\w(]/, 'arithmetic'],
]

/** Code with strings, template literals, comments and JSX text taken out — what is left is
 *  what executes. JSX text between tags ("/mo", "+3 more") is prose, not arithmetic. */
function executable(source: string): string {
  return (
    source
      .replace(/\/\*[\s\S]*?\*\//g, ' ')
      .replace(/\/\/.*$/gm, ' ')
      .replace(/`(?:\\.|[^`\\])*`/g, '""')
      .replace(/'(?:\\.|[^'\\\n])*'/g, '""')
      .replace(/"(?:\\.|[^"\\\n])*"/g, '""')
      .replace(/>[^<>{}]*</g, '><')
      .replace(/^import .*$/gm, ' ')
      // JSX attribute names (aria-label, data-od-id). Prettier spaces every binary operator, so
      // an unspaced hyphen is never subtraction.
      .replace(/\b[a-z]+(?:-[a-z]+)+\b/g, 'attr')
  )
}

const views = readdirSync(DIR).filter(
  (f) => /\.tsx?$/.test(f) && !f.includes('.test.') && !PLUMBING.has(f),
)

describe('panel views do no money math', () => {
  it('finds the panel views', () => {
    expect(views.length).toBeGreaterThan(0)
  })

  it.each(views)('%s', (file) => {
    const code = executable(readFileSync(join(DIR, file), 'utf8'))
    const found = FORBIDDEN.flatMap(([re, what]) => {
      const m = code.match(re)
      return m ? [`${what}: "${m[0]}"`] : []
    })
    expect(found).toEqual([])
  })
})
