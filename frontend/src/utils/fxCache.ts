import { getBaseCurrency } from './format'

/** The last FX table the portfolio fetched (`usePortfolio` keeps it as a first-paint seed).
 *  Good for an "about" figure in a hint; never an input to a stored amount — the server
 *  converts with its own rates. */
export function readCachedFxRates(): Record<string, number> {
  const base = getBaseCurrency()
  try {
    return { [base]: 1.0, ...JSON.parse(localStorage.getItem('wealthFX') || '{}') }
  } catch {
    return { [base]: 1.0 }
  }
}
