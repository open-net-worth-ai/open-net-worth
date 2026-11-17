import { amountsHidden } from '../../../utils/amountMask'
import { fCurrency, getNumberLocale } from '../../../utils/format'
import type { VisualFormat } from '../../../types/chat'

const MASK = '***'

function trimmed(n: number, maxFrac: number): string {
  return n.toLocaleString(getNumberLocale(), { maximumFractionDigits: maxFrac })
}

/** One raw figure as text, the way the screens write it (S$4,180, 12%). Formatting only — the
 *  client never derives a number from a visual. `mask` is for canvas text, which the DOM
 *  `.money` mask cannot reach. */
export function formatVisualValue(
  value: number,
  format: VisualFormat | null | undefined,
  currency: string | null | undefined,
  mask = false,
): string {
  if (mask && amountsHidden()) return MASK
  if (!Number.isFinite(value)) return '—'
  switch (format) {
    case 'money':
      return fCurrency(
        value,
        currency || '',
        // Cents only where they matter: a whole figure or one of 1,000+ reads as whole units.
        Number.isInteger(value) || Math.abs(value) >= 1000
          ? { maximumFractionDigits: 0, minimumFractionDigits: 0 }
          : undefined,
      )
    case 'percent':
      return `${trimmed(value * 100, 1)}%`
    case 'percent_points':
      return `${trimmed(value, 1)}%`
    default:
      return trimmed(value, 2)
  }
}
