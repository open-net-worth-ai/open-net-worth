// Paper Trading API (`/api/paper/*`, flag `paper_trading`). Every response says `simulated: true`:
// a paper portfolio is pretend money and never part of the real book (ADR
// 2026-11-15-paper-trading-is-a-simulated-book-never-an-order-path).

import { authFetch } from './client'
import { invalidateQueryCache } from '../state/queryCache'

export type PaperKind = 'listed' | 'fund_nav' | 'crypto' | 'metal' | 'fx' | 'index' | 'manual'
export type PriceBasis = 'quote' | 'nav' | 'modelled' | 'synthetic' | 'yours'
export type OrderSide = 'buy' | 'sell'
export type OrderType = 'market' | 'limit' | 'stop' | 'nav'
export type OrderStatus = 'open' | 'filled' | 'cancelled' | 'rejected' | 'expired'

export interface PaperCard {
  id: string
  name: string
  status: 'active' | 'archived'
  home_currency: string
  benchmark: string
  origin: { kind: 'cash' | 'holdings' | 'followed'; ref: string | null; label: string }
  created_by: string
  source_label: string
  run_no: number
  started_at: string
  archived_at: string | null
  day_count: number
  value: number | null
  cash_home: number | null
  return_pct: number | null
  benchmark_return_pct: number | null
  open_orders: number
  sparkline: { date: string; value: number }[]
  holdings_count: number
  weights: { symbol: string; weight: number }[]
  recent_trades: { label: string; side: OrderSide; symbol: string | null; at: string }[]
}

export interface PaperList {
  portfolios: PaperCard[]
  archived: PaperCard[]
}

export interface PaperHolding {
  asset_key: string
  kind: PaperKind
  kind_label: string
  symbol: string | null
  name: string
  currency: string
  unit: string | null
  quantity: number
  price: number | null
  price_currency: string
  basis: PriceBasis
  basis_label: string | null
  value_home: number | null
  cost_home: number | null
  pnl_home: number | null
  pnl_pct: number | null
  frozen: string | null
  marked_on: string | null
  weight: number | null
}

export interface PaperFill {
  price: number
  quantity: number
  total: number
  currency: string
  basis: PriceBasis
  basis_label: string | null
  source: string
  quote_as_of: string
  filled_at: string
}

export interface PaperOrder {
  id: string
  portfolio_id: string
  asset_key: string
  kind: PaperKind
  symbol: string | null
  name: string
  side: OrderSide
  quantity: number | null
  notional: number | null
  order_type: OrderType
  limit_price: number | null
  stop_price: number | null
  tif: 'day' | 'gtc'
  status: OrderStatus
  state_reason: string | null
  placed_at: string
  note: string | null
  origin: string
  fill: PaperFill | null
}

export interface PaperEvent {
  id: string
  at: string
  kind: string
  symbol: string | null
  name: string | null
  note: string | null
  label: string
  opening: boolean
  thesis: { stance: string; label: string } | null
}

export interface PaperPage {
  portfolio: PaperCard
  figures: {
    value: number | null
    return_pct: number | null
    benchmark_return_pct: number | null
    your_return_pct: number | null
    cash_home: number | null
    cash: { currency: string; amount: number; home: number | null }[]
    open_orders: number
    unrealized_home: number | null
    realized_home: number | null
  }
  holdings: PaperHolding[]
  holding_groups: { kind: PaperKind; label: string; holdings: PaperHolding[] }[]
  orders: PaperOrder[]
  activity: PaperEvent[]
}

export interface PaperPerformance {
  series: {
    date: string
    value: number
    paper: number | null
    benchmark: number | null
    yours: number | null
  }[]
  return_pct: number | null
  benchmark: { symbol: string; return_pct: number | null }
  yours_return_pct: number | null
}

export interface PaperDefaults {
  base_currency: string
  default_cash: number
  benchmark: string
  followed: { id: string; name: string }[]
  max_active: number
  active_count: number
  kinds: Record<PaperKind, string>
}

export interface PaperAsset {
  asset_key: string | null
  kind: PaperKind
  kind_label: string
  symbol: string
  name: string
  currency: string | null
  unit: string | null
  exchange: string | null
  price_source?: string
  price_source_id?: string
  asset_class?: string
}

export interface OrderDraft {
  side: OrderSide
  quantity?: number
  notional?: number
  order_type: OrderType
  limit_price?: number
  stop_price?: number
  tif?: 'day' | 'gtc'
  note?: string
}

export interface PaperPreview {
  decision: 'fill' | 'wait' | 'reject'
  reason: string | null
  price: number | null
  basis: PriceBasis | null
  basis_label: string | null
  source: string | null
  quote_as_of: string | null
  session_open: boolean | null
  next_line: string | null
  estimate: {
    quantity: number
    total: number
    total_home: number
    currency: string
    commission: number
  } | null
  cash_after_home: number | null
  thesis: { stance: string; label: string } | null
  warnings: string[]
}

export interface PaperPositionRow {
  portfolio_id: string
  portfolio_name: string
  quantity: number
  value_home: number | null
  currency: string
  return_pct: number | null
}

export const PAPER_KEY = '/api/paper'
export const paperListKey = `${PAPER_KEY}/portfolios`
export const paperPageKey = (id: string) => `${paperListKey}/${encodeURIComponent(id)}`
export const paperPerformanceKey = (id: string, range: string) =>
  `${paperPageKey(id)}/performance?range=${range}`
export const paperDefaultsKey = `${PAPER_KEY}/defaults`
export const paperSearchKey = (q: string) =>
  `${PAPER_KEY}/assets/search?q=${encodeURIComponent(q.trim())}`
export const paperPositionsKey = (instrumentId: string) =>
  `${PAPER_KEY}/positions?instrument_id=${encodeURIComponent(instrumentId)}`

async function readJson<T>(path: string): Promise<T> {
  const res = await authFetch(path)
  if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`)
  return (await res.json()) as T
}

/** A write: its server error is shown as is (the API words its refusals for the owner), and
 *  every paper read is refetched after it, so each open paper screen shows the new state. */
async function send<T>(
  path: string,
  method: 'POST' | 'PATCH' | 'DELETE',
  body?: unknown,
): Promise<T> {
  const res = await authFetch(path, {
    method,
    ...(body === undefined
      ? {}
      : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  })
  if (!res.ok) {
    let detail = 'The paper portfolio could not be changed.'
    try {
      const err = (await res.json()) as { detail?: unknown }
      if (typeof err.detail === 'string' && err.detail) detail = err.detail
    } catch {
      // keep the generic message
    }
    throw new Error(detail)
  }
  const out = (await res.json()) as T
  invalidateQueryCache((k) => k.startsWith(PAPER_KEY))
  return out
}

export const fetchPaperList = () => readJson<PaperList>(paperListKey)
export const fetchPaperPage = (id: string) => readJson<PaperPage>(paperPageKey(id))
export const fetchPaperPerformance = (id: string, range: string) =>
  readJson<PaperPerformance>(paperPerformanceKey(id, range))
export const fetchPaperDefaults = () => readJson<PaperDefaults>(paperDefaultsKey)
export const fetchPaperSearch = async (q: string) =>
  (await readJson<{ results: PaperAsset[] }>(paperSearchKey(q))).results
export const fetchPaperPositions = async (instrumentId: string) =>
  (await readJson<{ positions: PaperPositionRow[] }>(paperPositionsKey(instrumentId))).positions

/** An order names its asset by `asset_key` when the app knows it, else by the search result
 *  (the server resolves it). */
const assetRef = (a: PaperAsset) =>
  a.asset_key
    ? { asset_key: a.asset_key }
    : {
        asset: {
          symbol: a.symbol,
          name: a.name,
          kind: a.kind,
          price_source: a.price_source,
          price_source_id: a.price_source_id,
          asset_class: a.asset_class,
        },
      }

export const createPaperPortfolio = (body: {
  name: string
  start_with: 'cash' | 'holdings' | 'followed'
  cash?: number
  followed_id?: string
}) => send<{ portfolio: PaperCard }>(paperListKey, 'POST', body)

export const startOverPaperPortfolio = (id: string) =>
  send<{ portfolio: PaperCard }>(`${paperPageKey(id)}/start-over`, 'POST')

export const deletePaperPortfolio = (id: string) => send<unknown>(paperPageKey(id), 'DELETE')

export const previewPaperOrder = async (id: string, asset: PaperAsset, d: OrderDraft) => {
  // A preview writes nothing, so it does not refresh any screen.
  const res = await authFetch(`${paperPageKey(id)}/orders/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...assetRef(asset), ...d }),
  })
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: unknown }
    throw new Error(typeof err.detail === 'string' ? err.detail : 'No preview for this order.')
  }
  return (await res.json()) as PaperPreview
}

export const placePaperOrder = (id: string, asset: PaperAsset, d: OrderDraft) =>
  send<{ order: PaperOrder; toast: string }>(`${paperPageKey(id)}/orders`, 'POST', {
    ...assetRef(asset),
    ...d,
  })

export const cancelPaperOrder = (orderId: string) =>
  send<unknown>(`${PAPER_KEY}/orders/${encodeURIComponent(orderId)}`, 'DELETE')
