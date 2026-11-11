import { fCompact, fCompactAmount, fDate } from '../../../utils/format'
import type { FollowModel, LastTrade, ModelHolding } from '../../../api/followedPortfolios'

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

const usd = (n: number) => fCompactAmount(Math.abs(n), 'USD')

export function tradeLine(t: LastTrade): string {
  const verb = t.side === 'buy' ? 'Bought' : t.side === 'sell' ? 'Sold' : 'Exchanged'
  return `${verb} ${shortDate(t.date)}, ${usd(t.amount_low)} to ${usd(t.amount_high)}`
}

/** "+12.4%", "−3.1%", or "—" when unknown. `unit` swaps the sign for points ("+3.2 pts"). */
export function signedPct(n: number | null | undefined, unit: '%' | 'pts' = '%'): string {
  if (n == null || Number.isNaN(n)) return '—'
  const body = Math.abs(n * 100).toFixed(1)
  return `${n >= 0 ? '+' : '−'}${body}${unit === '%' ? '%' : ' pts'}`
}

export const toneOf = (n: number | null | undefined): 'up' | 'down' | 'flat' =>
  n == null || Number.isNaN(n) || n === 0 ? 'flat' : n > 0 ? 'up' : 'down'

/** ", 280M shares" for a fund holding; nothing for a member, who reports no share counts. */
export const sharesText = (h: ModelHolding): string =>
  h.shares ? `, ${fCompact(h.shares)} shares` : ''

/** The position's size in dollars at the quarter-end price the filing implies. */
export function sharesTitle(h: ModelHolding, model: FollowModel): string | undefined {
  if (!h.shares || !h.price) return undefined
  return `${fCompact(h.shares)} shares, about ${usd(h.shares * h.price)} at the ${shortDate(model.as_of)} price of ${fCompactAmount(h.price, 'USD')}.`
}

/** A card move's small figure: a share change ("+45%") or a member's trade day ("Jul 28"). */
export function moveDetail(detail: string | number | null): string {
  if (detail == null) return ''
  if (typeof detail === 'number')
    return `${detail >= 0 ? '+' : '−'}${Math.round(Math.abs(detail) * 100)}%`
  return shortDate(detail).replace(/, \d{4}$/, '')
}

/** A filing's issuer name as a person would say it: "Acme Corp. - Class A Common Stock" and
 *  "Acme Corp /New/" are both "Acme Corp". Display only — the stored name is untouched. */
export function companyName(raw: string): string {
  const cleaned = raw
    .replace(/\s+-\s+.*$/, '')
    .replace(/\s*\/[A-Za-z]{1,6}\/?\s*$/, '')
    .replace(/\s+(Class [A-Z]\s+)?(Common|Ordinary) (Stock|Shares)$/i, '')
    .replace(/[.,]+$/, '')
    .trim()
  return cleaned || raw
}

/** Two rows with one company name are two share classes (Alphabet's A and C): name the class
 *  on those rows only, so they never read as a duplicate. */
export function nameClasses<T extends { name: string; shareClass?: string | null }>(
  rows: T[],
): T[] {
  const count = new Map<string, number>()
  for (const r of rows) count.set(r.name, (count.get(r.name) ?? 0) + 1)
  return rows.map((r) =>
    (count.get(r.name) ?? 0) > 1 && r.shareClass
      ? { ...r, name: `${r.name} Class ${r.shareClass}` }
      : r,
  )
}
