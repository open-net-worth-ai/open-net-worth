import { authFetch } from './client'

export type FollowKind = 'fund_13f' | 'house_member' | 'etf_daily'
export type FollowStatus = 'ok' | 'stale' | 'error' | 'empty'
export type HoldingChange = 'new' | 'increased' | 'reduced' | 'unchanged'

/** An option contract a House member's report names: the right, strike, expiry and count. */
export interface OptionContract {
  right: 'call' | 'put'
  strike: number
  expiry: string
  contracts: number
}

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
  /** House member only: the live option contracts on this stock (calls count toward it). */
  options?: OptionContract[]
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
  /** The contract, when the trade was in options. */
  option?: OptionContract | null
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
  /** An option trade's contract, as its report describes it. */
  option?: OptionContract | null
  /** The contract a stock purchase came from, when it was an exercise. */
  exercised?: OptionContract | null
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
  /** True when the card is answered from what the server keeps: only a card that is not goes
   *  to its source, and only those count as "loading" in the gallery's banner. */
  ready?: boolean
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
  /** ``copied``: rebalanced to each report on its filing day; absent: today's holdings. */
  method?: 'copied'
  series: { date: string; portfolio: number; benchmark: number | null }[]
  holdings: Record<string, number>
  missing: string[]
}

/** Price return of a follow's CURRENT top holdings at their filed weights, against the
 *  owner's benchmark. Not the fund's or the member's own return. */
export interface FollowPerformance {
  benchmark: string
  /** ``ALL`` runs over the portfolio's whole history (Trading Activity's chart zooms into it). */
  periods: Partial<Record<PerfPeriod | 'ALL', PerfWindow>>
  /** "<trade date>|<symbol>" to the return since that trade, for a member's counted trades. */
  trade_returns: Record<string, number>
  /** Each ticker's exchange, from its own quote; a ticker missing here shows none. */
  exchanges?: Record<string, string>
}

export const performanceKey = (id: string) => `${FOLLOWING_KEY}/${id}/performance`

export const fetchPerformance = (id: string) => readJson<FollowPerformance>(performanceKey(id))

/** One Discover card: how it has done, what it just did, what is inside. */
export interface PreviewSummary {
  /** Its last figures, shown while the server makes a fresh card: ask again shortly. */
  stale?: boolean
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
  /** False while a fund's older filings are still being read in the background: the page
   *  opened on the newest five quarters and asks again until this is true. */
  history_complete?: boolean
}

const previewQuery = (kind: FollowKind, ref: string, full: boolean) =>
  `${FOLLOWING_KEY}/preview?kind=${kind}&ref=${encodeURIComponent(ref)}${full ? '&full=true' : ''}`

export const previewSummaryKey = (kind: FollowKind, ref: string) => previewQuery(kind, ref, false)
export const previewPageKey = (kind: FollowKind, ref: string) => previewQuery(kind, ref, true)
export const fetchPreviewSummary = (kind: FollowKind, ref: string) =>
  readJson<PreviewSummary>(previewSummaryKey(kind, ref))
export const fetchPreviewPage = (kind: FollowKind, ref: string) =>
  readJson<PreviewPage>(previewPageKey(kind, ref))
/** One asset type of your book (the server's market bands), or `all_assets`: your
 *  investments plus every owned asset (a home, a car), cash left out. */
export type Band = 'equity' | 'crypto' | 'commodity' | 'all_assets'

export interface LineRisk {
  /** Annualised swings of the weekly return (0.12 = 12%). */
  volatility: number | null
  /** The deepest fall from a high over the range (≤ 0). */
  max_drawdown: number | null
  /** How much the line moves with the benchmark (1 = in step). */
  beta: number | null
  /** Return per unit of swing (Sharpe): higher is more return for the ride. */
  sharpe: number | null
}

export interface SectorShare {
  sector: string
  weight: number
  holdings?: { symbol: string; weight: number }[]
}

export interface PreviewCompare {
  band: Band | null
  /** The asset types you hold today, largest first: what "Compare with" offers. */
  bands_held: Band[]
  overlap: {
    /** How much of one you would trade to turn it into the other (0 same, 1 nothing shared). */
    active_share: number
    shared_count: number
    their_count: number
    their_weight_held: number
    your_weight_shared: number
    shared: { symbol: string; theirs: number; yours: number }[]
    /** Their positions with no ticker yet, and their share of the portfolio: counted in
     *  their_count, but not comparable with yours until matched. */
    unmatched_count: number
    unmatched_weight: number
  }
  spread: Record<'theirs' | 'yours', Spread>
  /** Each sector's share, largest first, with every stock in it (largest first). */
  sectors: Record<'theirs' | 'yours', SectorShare[]>
  risk: {
    since: string
    benchmark: string | null
    theirs: LineRisk | null
    yours: LineRisk | null
    spy: LineRisk | null
    yours_since: string | null
    /** Your weekly returns against theirs: 1 moves in step with you, 0 unrelated. */
    correlation: number | null
  }
}

export interface Spread {
  count: number
  top5: number
  largest: { symbol: string; weight: number } | null
  /** The five largest holdings, each with its share: the side-by-side table. */
  top: { symbol: string; weight: number; sector: string }[]
}

export const previewCompareKey = (
  kind: FollowKind,
  ref: string,
  since: string | null,
  band: Band | null,
) =>
  `${FOLLOWING_KEY}/preview/compare?kind=${kind}&ref=${encodeURIComponent(ref)}` +
  `${since ? `&since=${since}` : ''}${band ? `&band=${band}` : ''}`
export const fetchPreviewCompare = (
  kind: FollowKind,
  ref: string,
  since: string | null,
  band: Band | null,
) => readJson<PreviewCompare>(previewCompareKey(kind, ref, since, band))

/** Every listed holding's own return and exchange: priced after the page opens, since a fund
 *  of thousands of stocks costs a price fetch for each. */
export const previewReturnsKey = (kind: FollowKind, ref: string) =>
  `${FOLLOWING_KEY}/preview/returns?kind=${kind}&ref=${encodeURIComponent(ref)}`
export const fetchPreviewReturns = (kind: FollowKind, ref: string) =>
  readJson<FollowPerformance>(previewReturnsKey(kind, ref))

export interface PriceHistory {
  symbol: string
  points: { date: string; price: number }[]
}

export const priceHistoryKey = (symbol: string) =>
  `${FOLLOWING_KEY}/price-history?symbol=${encodeURIComponent(symbol)}`
export const fetchPriceHistory = (symbol: string) => readJson<PriceHistory>(priceHistoryKey(symbol))
