// @vitest-environment jsdom
import { afterEach, expect, it } from 'vitest'
import {
  PERFORMANCE_PERIODS,
  rememberPerformancePeriod,
  rememberedPerformancePeriod,
} from './performancePeriod'

afterEach(() => localStorage.clear())

it('offers the ranges a brokerage chart does, in order', () => {
  expect(PERFORMANCE_PERIODS).toEqual(['1M', '3M', '6M', 'YTD', '1Y', '3Y', '5Y', 'ALL'])
})

it('defaults to 1Y and remembers a pick on this device', () => {
  expect(rememberedPerformancePeriod()).toBe('1Y')
  rememberPerformancePeriod('3Y')
  expect(rememberedPerformancePeriod()).toBe('3Y')
})

it('ignores a stored value that is no longer a range', () => {
  localStorage.setItem('onw:investments:performance-period', '10Y')
  expect(rememberedPerformancePeriod()).toBe('1Y')
})
