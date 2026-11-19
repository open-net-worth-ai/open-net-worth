import { describe, expect, it } from 'vitest'
import { roundToWhole } from './roundToWhole'

const sum = (xs: number[]) => Math.round(xs.reduce((a, b) => a + b, 0) * 10) / 10

describe('roundToWhole', () => {
  it('rounds shares so they add up to exactly 100.0, never 100.2', () => {
    // Rounded one by one these read 56 + 34 + 5.0 + 4.1 + 0.8 + 0.3 = 100.2.
    const out = roundToWhole([0.562, 0.336, 0.05, 0.041, 0.008, 0.003])
    expect(sum(out)).toBe(100)
    expect(out).toEqual([56.2, 33.6, 5, 4.1, 0.8, 0.3])
  })

  it('gives the leftover tenths to the shares that lost the most', () => {
    const out = roundToWhole([1 / 3, 1 / 3, 1 / 3])
    expect(sum(out)).toBe(100)
    expect(out).toEqual([33.4, 33.3, 33.3])
  })

  it('scales a part of a whole up to 100, and is empty-safe', () => {
    expect(sum(roundToWhole([0.2, 0.2]))).toBe(100)
    expect(roundToWhole([])).toEqual([])
    expect(roundToWhole([0, 0])).toEqual([0, 0])
  })
})
