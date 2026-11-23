// The Performance chart's range, remembered on this device: a viewing preference for one chart,
// not book data, so it lives in localStorage (like the ticker and theme) rather than the profile.
import type { ReturnPeriod } from '../../../types/portfolio'

export const PERFORMANCE_PERIODS: ReturnPeriod[] = [
  '1M',
  '3M',
  '6M',
  'YTD',
  '1Y',
  '3Y',
  '5Y',
  'ALL',
]
export const DEFAULT_PERFORMANCE_PERIOD: ReturnPeriod = '1Y'
const KEY = 'onw:investments:performance-period'

export function rememberedPerformancePeriod(): ReturnPeriod {
  try {
    const stored = localStorage.getItem(KEY)
    return PERFORMANCE_PERIODS.find((p) => p === stored) ?? DEFAULT_PERFORMANCE_PERIOD
  } catch {
    return DEFAULT_PERFORMANCE_PERIOD
  }
}

export function rememberPerformancePeriod(period: ReturnPeriod): void {
  try {
    localStorage.setItem(KEY, period)
  } catch {
    /* storage unavailable — the choice still holds for this visit */
  }
}
