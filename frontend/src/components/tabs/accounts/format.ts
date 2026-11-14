/**
 * format.ts — Money/FX formatting helpers for the Accounts tab.
 *
 * Ported from prototype/accounts/data.jsx. `toBase` defers to the app's
 * utils/format.ts (which uses the live FX rate table from /api/prices).
 * The prototype's static FX_TO_SGD is replaced by a `rates` parameter.
 */

import { toBase as appToBase, getBaseCurrency } from '../../../utils/format'
import type { FxRates } from './types'

// Currencies with no minor unit — never show cents.
export const ZERO_DEC = new Set([
  'JPY',
  'KRW',
  'VND',
  'IDR',
  'CLP',
  'ISK',
  'HUF',
  'TWD',
  'XAF',
  'XOF',
  'XPF',
  'PYG',
  'RWF',
  'UGX',
  'BIF',
  'DJF',
  'GNF',
  'KMF',
  'VUV',
])

// Explicit symbols for common currencies; others fallback to Intl.
const CCY_SYMBOL: Record<string, string> = {
  SGD: 'S$',
  USD: '$',
  EUR: '€',
  GBP: '£',
  HKD: 'HK$',
  JPY: '¥',
  AUD: 'A$',
  VND: '₫',
  THB: '฿',
  MYR: 'RM',
  IDR: 'Rp',
}

/** Currency symbol string, e.g. "S$", "$", "Rp " */
export function sym(ccy: string): string {
  return CCY_SYMBOL[ccy] ?? ccy + ' '
}

/**
 * Convert `amount` from `ccy` to `base` using the live rate table.
 * Takes a `rates` map so callers remain pure (hook supplies real rates).
 */
export function toBase(amount: number, ccy: string, rates: FxRates): number {
  const base = getBaseCurrency()
  return appToBase(amount, ccy, rates, base)
}

/** Thousands-grouped integer or decimal number. `cents` = true → 2dp. */
export function grp(n: number, cents: boolean): string {
  return Math.abs(n).toLocaleString('en-US', {
    minimumFractionDigits: cents ? 2 : 0,
    maximumFractionDigits: cents ? 2 : 0,
  })
}

/**
 * Native-currency amount: "S$1,240.50", "¥840", "US$12.00"
 * `opts.cents`: override decimal display; defaults to smart (no cents if whole; zero-dec ccy never).
 */
export function fCur(n: number, ccy: string, opts: { cents?: boolean } = {}): string {
  const noCents = ZERO_DEC.has(ccy)
  const cents = noCents ? false : (opts.cents ?? Math.round(n * 100) % 100 !== 0)
  return (n < 0 ? '-' : '') + sym(ccy) + grp(Math.abs(n), cents)
}

/**
 * The other side of a cross-currency transfer, under the row's own amount: `$200.00 →` when it
 * sent the money here, `→ $100.00` when it received it. `approx` marks a figure that isn't booked
 * yet (the import review's estimate); the register and the review otherwise read the same.
 */
export function otherSideFlow(
  amount: number,
  ccy: string,
  sent: boolean,
  opts: { approx?: boolean } = {},
): string {
  const other = (opts.approx ? '≈' : '') + fCur(Math.abs(amount), ccy, { cents: true })
  return sent ? `${other} →` : `→ ${other}`
}

/**
 * Base-currency total — sidebar/overview totals. Defaults to no cents;
 * `opts.cents` forces 2dp (zero-decimal base currencies always stay whole).
 * Uses the app's currently-configured base currency (e.g. "SGD").
 */
export function fBase(n: number, opts: { cents?: boolean } = {}): string {
  const ccy = getBaseCurrency()
  const cents = ZERO_DEC.has(ccy) ? false : (opts.cents ?? false)
  return (n < 0 ? '-' : '') + sym(ccy) + grp(Math.abs(n), cents)
}

/**
 * Compute FX info strings for the transaction detail drawer.
 * Returns `fact` (bank-settled rate for the orig→posted conversion) and/or
 * `est` (today's floating reporting rate for the posted currency → base).
 */
export function fxInfo(
  amount: number,
  ccy: string,
  origAmount: number | null | undefined,
  origCurrency: string | null | undefined,
  rates: FxRates,
): { fact?: string; est?: string } {
  const base = getBaseCurrency()
  const r: { fact?: string; est?: string } = {}

  if (origCurrency && origCurrency !== ccy && origAmount) {
    const rate = Math.abs(amount) / Math.abs(origAmount)
    r.fact =
      rate >= 0.01
        ? `1 ${origCurrency} ≈ ${sym(ccy)}${grp(rate, true)}`
        : `1 ${ccy} ≈ ${sym(origCurrency)}${grp(1 / rate, 1 / rate < 1000)}`
  }

  if (ccy !== base) {
    const rate = appToBase(1, ccy, rates, base)
    r.est = `1 ${ccy} ≈ ${sym(base)}${rate < 0.1 ? rate.toPrecision(3) : grp(rate, true)}`
  }

  return r
}
