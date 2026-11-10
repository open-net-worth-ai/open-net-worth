import { authFetch } from './client'

export type FollowKind = 'fund_13f' | 'house_member'
export type FollowStatus = 'ok' | 'stale' | 'error' | 'empty'
export type HoldingChange = 'new' | 'increased' | 'reduced' | 'unchanged'

export interface ModelHolding {
  symbol: string
  name: string
  /** 0 to 1. */
  weight: number
  prev_weight: number | null
  change: HoldingChange
  /** Fund only: share counts at the two quarter-ends, the quarter-end price the filing implies,
   *  and the share change valued at it. A 13F gives no trade date and no trade price. */
  shares?: number
  price?: number | null
  prev_shares?: number
  shares_change?: number
  shares_change_pct?: number | null
  change_value?: number | null
  /** House member only: the newest counted trade in this stock. */
  last_trade?: LastTrade | null
}

export interface LastTrade {
  date: string
  filed_at: string
  side: 'buy' | 'sell' | 'exchange'
  amount_low: number
  amount_high: number
}

export interface FollowModel {
  as_of: string
  /** The previous filing's quarter-end, when there is one. */
  prev_as_of?: string | null
  filed_at: string
  age_days: number | null
  estimated: boolean
  holdings: ModelHolding[]
  exited: {
    symbol: string
    name: string
    prev_shares?: number
    prev_value?: number
    last_trade?: LastTrade | null
  }[]
  unmatched: { name: string; weight: number }[]
  unread_reports: number
}

export interface FollowActivity {
  date: string
  filed_at: string
  symbol: string | null
  name: string
  side: 'buy' | 'sell' | 'exchange'
  amount_low: number
  amount_high: number
  asset_type: string
  counted: boolean
}

export interface Follow {
  id: string
  kind: FollowKind
  source_ref: string
  name: string
  subtitle: string
  status: FollowStatus
  error: string | null
  last_checked_at: string | null
  model: FollowModel | null
  activity: FollowActivity[]
}

export interface FollowList {
  follows: Follow[]
}

export interface DirectoryEntry {
  kind: FollowKind
  source_ref: string
  name: string
  subtitle: string
  featured: boolean
  followed: boolean
}

export interface FollowChange {
  follow: Follow
  new_filing: boolean
}

export const FOLLOWING_KEY = '/api/followed-portfolios'
export const directoryKey = (q: string) =>
  `${FOLLOWING_KEY}/directory?q=${encodeURIComponent(q.trim())}`
async function readJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await authFetch(path, init)
  if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`)
  return (await res.json()) as T
}

export const fetchFollowList = () => readJson<FollowList>(FOLLOWING_KEY)

export async function fetchDirectory(q: string): Promise<DirectoryEntry[]> {
  const body = await readJson<{ results: DirectoryEntry[] }>(directoryKey(q))
  return body.results
}

async function send(
  path: string,
  method: 'POST' | 'DELETE',
  body: unknown,
  fallback: string,
): Promise<FollowChange> {
  const res = await authFetch(path, {
    method,
    ...(body === undefined
      ? {}
      : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  })
  if (!res.ok) {
    let detail = fallback
    try {
      const err = (await res.json()) as { detail?: unknown }
      if (typeof err.detail === 'string' && err.detail) detail = err.detail
    } catch {
      // keep the generic message
    }
    throw new Error(detail)
  }
  return (await res.json()) as FollowChange
}

const followPath = (id: string) => `${FOLLOWING_KEY}/${encodeURIComponent(id)}`

export const followPortfolio = (kind: FollowKind, sourceRef: string) =>
  send(FOLLOWING_KEY, 'POST', { kind, source_ref: sourceRef }, 'The filing could not be read.')

export const unfollowPortfolio = (id: string) =>
  send(followPath(id), 'DELETE', undefined, 'The server refused the change.')

export const refreshFollow = (id: string) =>
  send(`${followPath(id)}/refresh`, 'POST', undefined, 'The refresh did not go through.')

export type PerfPeriod = '1M' | '6M' | '12M' | 'FILING'

export interface PerfWindow {
  status: 'ok' | 'unavailable'
  start?: string
  end?: string
  /** 0-based fractions: 0.12 is +12%. */
  return?: number
  benchmark_return?: number
  covered_weight?: number
  series: { date: string; portfolio: number; benchmark: number | null }[]
  holdings: Record<string, number>
  missing: string[]
}

/** Price return of a follow's CURRENT top holdings at their filed weights, against the
 *  owner's benchmark. Not the fund's or the member's own return. */
export interface FollowPerformance {
  benchmark: string
  periods: Partial<Record<PerfPeriod, PerfWindow>>
  /** "<trade date>|<symbol>" to the return since that trade, for a member's counted trades. */
  trade_returns: Record<string, number>
}

export const performanceKey = (id: string) => `${FOLLOWING_KEY}/${id}/performance`

export const fetchPerformance = (id: string) => readJson<FollowPerformance>(performanceKey(id))
