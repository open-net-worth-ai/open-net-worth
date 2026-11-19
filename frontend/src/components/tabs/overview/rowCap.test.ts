// A list widget's row cap: the user's pick, else the widget's own default. "All" is stored as
// 0 so it can't be confused with "never picked" (null) — that ambiguity made "All" on Next 30
// days quietly show 8.

import { describe, expect, it } from 'vitest'
import { WIDGET_DEFINITIONS } from './definitions'
import { ALL_ROWS, rowCap } from './widgetDisplayShared'

const merchants = WIDGET_DEFINITIONS.find((d) => d.defId === 'rep-merchants')!

describe('rowCap', () => {
  it("falls back to the widget's default until the user picks", () => {
    expect(rowCap({ limit: null }, merchants)).toBe(10)
    expect(rowCap({}, merchants)).toBe(10)
  })

  it('honours a picked count and "All"', () => {
    expect(rowCap({ limit: 25 }, merchants)).toBe(25)
    expect(rowCap({ limit: ALL_ROWS }, merchants)).toBeNull()
  })

  it('offers 5 / 10 / 15 / 25 / All on merchant and transaction lists', () => {
    for (const id of ['rep-merchants', 'recent-txns', 'recent-added-txns', 'subscriptions']) {
      const def = WIDGET_DEFINITIONS.find((d) => d.defId === id)!
      expect(def.rows?.options).toEqual([5, 10, 15, 25, 'all'])
    }
  })

  it('lets every list widget show all its rows', () => {
    for (const def of WIDGET_DEFINITIONS.filter((d) => d.rows)) {
      expect(def.rows!.options).toContain('all')
    }
  })

  it("keeps every default among the widget's own choices", () => {
    for (const def of WIDGET_DEFINITIONS.filter((d) => d.rows)) {
      expect(def.rows!.options).toContain(def.rows!.default)
    }
  })
})
