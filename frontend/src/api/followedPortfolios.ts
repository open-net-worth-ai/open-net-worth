import { authFetch } from './client'

export type FollowKind = 'fund_13f' | 'house_member' | 'etf_daily'
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
  /** Fund only: the filing codes (CUSIPs) behind it — what a ticker correction names. */
  keys?: string[]
  /** The share class the filing names ("A", "C"), when it names one. */
  share_class?: string | null
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
  /** The filing's total and its count of positions with a ticker, now and in the previous
   *  filing. A member's reports carry no totals. */
  total_value?: number | null
  prev_total_value?: number | null
  positions?: number
  prev_positions?: number | null
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
  /** Holdings large enough to show that no ticker was found for: the owner can set one. */
  unmatched: { key: string; name: string; weight: number }[]
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
  /** The Clerk's PDF this trade was disclosed in. */
  report_url?: string | null
  /** Form 4 (a fund's >10% stake): the exact trade. */
  shares?: number | null
  price?: number | null
  value?: number | null
  source?: 'house' | 'form4'
}

export interface Follow {
  /** A fund's 13F quarters, newest first (about a year), for the chart's quarter markers. */
  history?: Quarter[]
  id: string
  kind: FollowKind
  source_ref: string
  name: string
  subtitle: string
  status: FollowStatus
  error: string | null
  last_checked_at: string | null
  /** The newest filing at its source (SEC EDGAR, or the House Clerk's PDF). */
  source_url?: string | null
  model: FollowModel | null
  activity: FollowActivity[]
}

export interface Quarter {
  as_of: string
  filed_at: string
  /** Shares held per symbol at that quarter's end. */
  qty: Record<string, number>
  /** Their value at that quarter's end, in dollars. */
  value?: Record<string, number>
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
  /** Present once the card has been priced — what Discover sorts by. */
  return_12m?: number | null
  latest_at?: string | null
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

/** The owner's correction: ``symbol`` for the holding filed under ``key`` ("" clears it). */
export const setFollowTicker = (id: string, key: string, symbol: string) =>
  send(`${followPath(id)}/ticker`, 'POST', { key, symbol }, 'The ticker was not saved.')

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

/** One Discover card: how it has done, what it just did, what is inside. */
export interface PreviewSummary {
  kind: FollowKind
  source_ref: string
  name: string
  subtitle: string
  follow_id: string | null
  estimated: boolean
  style: string | null
  holdings_count: number
  weights: { symbol: string; weight: number }[]
  as_of: string | null
  latest_at: string | null
  filed_at: string | null
  benchmark: string | null
  return_12m: number | null
  benchmark_12m: number | null
  spark: number[]
  moves: { symbol: string; verb: string; detail: string | number | null; up: boolean }[]
}

export interface PreviewPage {
  follow: Follow
  follow_id: string | null
  performance: FollowPerformance
  /** Plain company names by symbol, where the price service knows one. */
  names: Record<string, string>
  /** Each holding's sector, where known (the Holdings ring groups by it). */
  sectors?: Record<string, string>
}

const previewQuery = (kind: FollowKind, ref: string, full: boolean) =>
  `${FOLLOWING_KEY}/preview?kind=${kind}&ref=${encodeURIComponent(ref)}${full ? '&full=true' : ''}`

export const previewSummaryKey = (kind: FollowKind, ref: string) => previewQuery(kind, ref, false)
export const previewPageKey = (kind: FollowKind, ref: string) => previewQuery(kind, ref, true)
export const fetchPreviewSummary = (kind: FollowKind, ref: string) =>
  readJson<PreviewSummary>(previewSummaryKey(kind, ref))
export const fetchPreviewPage = (kind: FollowKind, ref: string) =>
  readJson<PreviewPage>(previewPageKey(kind, ref))

export interface PriceHistory {
  symbol: string
  points: { date: string; price: number }[]
}

export const priceHistoryKey = (symbol: string) =>
  `${FOLLOWING_KEY}/price-history?symbol=${encodeURIComponent(symbol)}`
export const fetchPriceHistory = (symbol: string) => readJson<PriceHistory>(priceHistoryKey(symbol))
