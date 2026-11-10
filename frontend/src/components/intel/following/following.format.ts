import { fCompactAmount, fDate } from '../../../utils/format'
import type { FollowActivity, FollowModel } from '../../../api/followedPortfolios'

// The spec's rule says days below 60, but its own acceptance entry and wireframe read
// "88 days ago", so days run to the 120 mark, past a quarter's filing lag.
const DAYS_LIMIT = 120

export function ageText(days: number | null | undefined): string {
  if (days == null) return ''
  if (days <= 0) return 'today'
  if (days === 1) return 'yesterday'
  if (days < DAYS_LIMIT) return `${days} days ago`
  if (days < 730) return `${Math.round(days / 30)} months ago`
  return `${Math.floor(days / 365)} years ago`
}

export const shortDate = (d: string) => fDate(d, 'short', 'always')

/** "1 new · 2 added · 1 trimmed · 1 sold out", or null when nothing moved. */
export function changeSummary(model: FollowModel): string | null {
  const count = (c: string) => model.holdings.filter((h) => h.change === c).length
  const parts = [
    [count('new'), 'new'],
    [count('increased'), 'added'],
    [count('reduced'), 'trimmed'],
    [model.exited.length, 'sold out'],
  ] as const
  const text = parts.filter(([n]) => n > 0).map(([n, label]) => `${n} ${label}`)
  return text.length ? text.join(' · ') : null
}

export function amountRange(a: FollowActivity): string {
  return `${fCompactAmount(a.amount_low, 'USD')} to ${fCompactAmount(a.amount_high, 'USD')}`
}
