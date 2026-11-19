import type { Follow, FollowModel } from '../../../api/followedPortfolios'
import type { Names } from './activityRows'
import { companyName } from './following.format'
import { PROFILES } from './portfolioProfiles'

const TOP = 5
const WORDS = ['', 'one', 'two', 'three', 'four', 'five']
const CONCENTRATED = 0.6
const LEANS = 0.25
const STEADY = 0.5

const list = (items: string[]) =>
  items.length <= 1 ? (items[0] ?? '') : `${items.slice(0, -1).join(', ')} and ${items.at(-1)}`
const pct = (w: number) => `${Math.round(w * 100)}%`

/** A company as people say it: "American Express", not "American Express Company". */
export function shortCompany(name: string): string {
  const short = name
    .replace(/^The\s+/i, '')
    .replace(
      /[,\s]+(Inc|Incorporated|Corp|Corporation|Company|Co|Ltd|Limited|plc|PLC|Holdings|Group|N\.?V|S\.?A|AG|SE)\.?$/i,
      '',
    )
    .replace(/[,\s]+(Inc|Corp|Co|Ltd|plc)\.?$/i, '')
    .trim()
  return short || name
}

/** What this portfolio is, as moomoo's Overview opens: who runs it (a written profile for the
 *  featured ones), then what kind of portfolio it is, read from its own data: how concentrated,
 *  which sectors it leans toward, how much it trades. The report's own numbers (its value, count
 *  and date) are the key figures under it, never repeated here. */
export function describePortfolio(
  follow: Follow,
  model: FollowModel,
  names: Names,
  sectors: Record<string, string> = {},
): string[] {
  const out: string[] = []
  const profile = PROFILES[`${follow.kind}:${follow.source_ref}`]
  const who = follow.subtitle ? `${follow.name} (${follow.subtitle})` : follow.name
  if (profile) out.push(profile)
  else if (follow.kind === 'house_member') out.push(`${who} is a member of Congress.`)
  else if (follow.kind === 'etf_daily') out.push(`${who} publishes its holdings every trading day.`)
  else out.push(`${who} reports its US stock holdings to the SEC every quarter.`)
  if (follow.kind === 'house_member')
    out.push(
      'Members report each trade as a dollar range, up to 45 days after it, so these holdings are estimated from the trades they reported.',
    )

  const held = [...model.holdings].sort((a, b) => b.weight - a.weight)
  const name = (s: string, raw: string) => shortCompany(names[s] ?? companyName(raw))
  const top = held.slice(0, TOP)
  const topShare = top.reduce((sum, h) => sum + h.weight, 0)
  if (top.length > 2) {
    const leaders = list(top.slice(0, 2).map((h) => name(h.symbol, h.name)))
    out.push(
      topShare >= CONCENTRATED
        ? `It is concentrated: its ${WORDS[top.length]} largest holdings, led by ${leaders}, are ${pct(topShare)} of it.`
        : `It is spread out: its ${WORDS[top.length]} largest holdings, led by ${leaders}, are only ${pct(topShare)} of it.`,
    )
  }

  const bySector = new Map<string, number>()
  for (const h of held) {
    const sector = sectors[h.symbol]
    if (sector) bySector.set(sector, (bySector.get(sector) ?? 0) + h.weight)
  }
  const leaning = [...bySector].sort((a, b) => b[1] - a[1]).filter(([, w]) => w >= LEANS)
  if (leaning.length)
    out.push(`It leans toward ${list(leaning.slice(0, 2).map(([s, w]) => `${s} (${pct(w)})`))}.`)

  const calls = held.filter((h) => h.options?.some((o) => o.right === 'call'))
  if (calls.length)
    out.push(
      `Much of its exposure is through call options, in ${list(calls.slice(0, 4).map((h) => h.symbol))}.`,
    )

  if (follow.kind === 'fund_13f' && model.prev_as_of && held.length) {
    const steady = held.filter((h) => h.change === 'unchanged').length
    out.push(
      steady / held.length >= STEADY
        ? `It trades little: ${steady} of its ${held.length} holdings did not change in its latest report.`
        : `It trades actively: only ${steady} of its ${held.length} holdings did not change in its latest report.`,
    )
  }
  return out
}
