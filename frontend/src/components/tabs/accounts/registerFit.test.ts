import { describe, expect, it } from 'vitest'
import { REG_COLS } from './Register'
import { fitRegisterColumns } from './registerFit'

const widthsOf = () => Object.fromEntries(REG_COLS.map((c) => [c.key, c.w]))

describe('fitRegisterColumns', () => {
  it('leaves the widths alone before the container is measured', () => {
    const fit = fitRegisterColumns(REG_COLS, widthsOf(), 0)
    expect(fit.widths).toEqual(widthsOf())
    expect(fit.overflow).toBe(false)
  })

  it('keeps every width when the container is wide', () => {
    const fit = fitRegisterColumns(REG_COLS, widthsOf(), 1600)
    expect(fit.widths).toEqual(widthsOf())
    expect(fit.overflow).toBe(false)
  })

  it('squeezes the other columns toward their minimums before it overflows', () => {
    const fit = fitRegisterColumns(REG_COLS, widthsOf(), 892)
    expect(fit.overflow).toBe(false)
    expect(fit.widths.category).toBeLessThan(widthsOf().category!)
    for (const c of REG_COLS) expect(fit.widths[c.key]).toBeGreaterThanOrEqual(c.min ?? c.w)
  })

  it('overflows at the minimums rather than dropping a column, so scrolling reaches all of them', () => {
    const fit = fitRegisterColumns(REG_COLS, widthsOf(), 500)
    expect(fit.overflow).toBe(true)
    for (const c of REG_COLS) expect(fit.widths[c.key]).toBe(c.min ?? c.w)
  })

  it('stops squeezing exactly where it starts overflowing — no jump between the two', () => {
    let last = fitRegisterColumns(REG_COLS, widthsOf(), 1600)
    for (let w = 1600; w >= 300; w -= 4) {
      const fit = fitRegisterColumns(REG_COLS, widthsOf(), w)
      for (const c of REG_COLS) expect(fit.widths[c.key]).toBeLessThanOrEqual(last.widths[c.key]!)
      last = fit
    }
  })
})
