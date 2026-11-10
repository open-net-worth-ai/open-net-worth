import { fCompact, fCompactAmount, fDate, fPct } from '../../../utils/format'
import type {
  FollowActivity,
  FollowModel,
  LastTrade,
  ModelHolding,
} from '../../../api/followedPortfolios'

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

const usd = (n: number) => fCompactAmount(Math.abs(n), 'USD')

export function tradeLine(t: LastTrade): string {
  const verb = t.side === 'buy' ? 'Bought' : t.side === 'sell' ? 'Sold' : 'Exchanged'
  return `${verb} ${shortDate(t.date)} · ${usd(t.amount_low)} to ${usd(t.amount_high)}`
}

/** What is actually known about a holding's move, in one line, and the full sentence behind it.
 *  A fund's filing gives share counts at two quarter-ends (no trade date, no trade price); a
 *  member's gives a trade date and a dollar range (no shares, no price). */
export function changeDetail(
  h: ModelHolding,
  model: FollowModel,
): { line: string; title: string } | null {
  if (h.last_trade)
    return {
      line: tradeLine(h.last_trade),
      title: `Latest disclosed trade, filed ${shortDate(h.last_trade.filed_at)}. Members report a range, not a share count or a price.`,
    }
  const moved = h.shares_change
  if (moved == null || moved === 0 || h.shares == null || h.prev_shares == null) return null
  const sign = moved > 0 ? '+' : '−'
  const pct =
    h.shares_change_pct == null
      ? ''
      : ` (${fPct(h.shares_change_pct, { fraction: true, sign: true })})`
  const value = h.change_value ? ` · about ${usd(h.change_value)}` : ''
  const period = model.prev_as_of
    ? `Between ${shortDate(model.prev_as_of)} and ${shortDate(model.as_of)}`
    : `By ${shortDate(model.as_of)}`
  const price = h.price
    ? ` Valued at the quarter-end price of ${fCompactAmount(h.price, 'USD')}.`
    : ''
  return {
    line: `${sign}${fCompact(Math.abs(moved))} shares${pct}${value}`,
    title: `${period}: from ${fCompact(h.prev_shares)} to ${fCompact(h.shares)} shares.${price} The filing gives no trade dates or trade prices.`,
  }
}

export function exitedDetail(x: FollowModel['exited'][number]): string {
  if (x.last_trade) return ` (${tradeLine(x.last_trade).toLowerCase()})`
  if (!x.prev_shares) return ''
  const value = x.prev_value ? `, about ${usd(x.prev_value)}` : ''
  return ` (all ${fCompact(x.prev_shares)} shares${value})`
}
