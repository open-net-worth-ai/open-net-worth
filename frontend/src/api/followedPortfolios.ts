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
}

export interface FollowModel {
  as_of: string
  filed_at: string
  age_days: number | null
  estimated: boolean
  holdings: ModelHolding[]
  exited: { symbol: string; name: string }[]
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
