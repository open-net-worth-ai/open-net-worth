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

/** "+12.4%", "−3.1%", or "—" when unknown. `unit` swaps the sign for points ("+3.2 pts"). */
export function signedPct(n: number | null | undefined, unit: '%' | 'pts' = '%'): string {
  if (n == null || Number.isNaN(n)) return '—'
  const body = Math.abs(n * 100).toFixed(1)
  return `${n >= 0 ? '+' : '−'}${body}${unit === '%' ? '%' : ' pts'}`
}

export const toneOf = (n: number | null | undefined): 'up' | 'down' | 'flat' =>
  n == null || Number.isNaN(n) || n === 0 ? 'flat' : n > 0 ? 'up' : 'down'

export interface MoveRow {
  symbol: string
  name: string
  /** Approximate dollars, signed: a buy is positive, a sale negative. */
  value: number
  label: string
  detail: string
  title: string
}

/** A fund's moves between its last two filings, biggest first. */
export function moveRows(model: FollowModel): MoveRow[] {
  const rows: MoveRow[] = []
  for (const h of model.holdings) {
    const detail = changeDetail(h, model)
    if (h.change === 'unchanged' || !detail || h.shares_change == null) continue
    const sign = h.shares_change > 0 ? '+' : '−'
    rows.push({
      symbol: h.symbol,
      name: h.name,
      value: h.change_value ?? 0,
      label: `${h.change === 'new' ? 'New · ' : ''}${sign}${usd(h.change_value ?? 0)}`,
      detail: `${sign}${fCompact(Math.abs(h.shares_change))} shares${
        h.shares_change_pct == null
          ? ''
          : ` (${fPct(h.shares_change_pct, { fraction: true, sign: true })})`
      }`,
      title: detail.title,
    })
  }
  for (const x of model.exited) {
    if (!x.prev_shares) continue
    rows.push({
      symbol: x.symbol,
      name: x.name,
      value: -(x.prev_value ?? 0),
      label: `Sold out · −${usd(x.prev_value ?? 0)}`,
      detail: `all ${fCompact(x.prev_shares)} shares`,
      title: 'Held in the previous filing, gone from this one.',
    })
  }
  return rows.sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
}
