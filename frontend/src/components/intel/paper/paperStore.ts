// PROTOTYPE data layer for Paper Trading (UI prototype branch). An in-browser store, kept in
// localStorage, shaped like the RFC's API (docs/design/paper-trading/RFC.md §8) so the screens
// can later read `/api/paper/*` instead without changing shape. Prices are fixed made-up
// figures; nothing here touches the real book.

import { useSyncExternalStore } from 'react'

export type AssetKind = 'stock' | 'etf' | 'crypto' | 'metal'
export interface Asset {
  symbol: string
  name: string
  kind: AssetKind
  price: number
  prevClose: number
  unit?: string
}

export const UNIVERSE: Asset[] = [
  { symbol: 'NVDA', name: 'NVIDIA', kind: 'stock', price: 187.42, prevClose: 185.12 },
  { symbol: 'AAPL', name: 'Apple', kind: 'stock', price: 228.1, prevClose: 229.4 },
  { symbol: 'MSFT', name: 'Microsoft', kind: 'stock', price: 512.3, prevClose: 508.9 },
  { symbol: 'AMD', name: 'AMD', kind: 'stock', price: 146.18, prevClose: 144.0 },
  { symbol: 'VT', name: 'Vanguard Total World', kind: 'etf', price: 131.06, prevClose: 130.7 },
  { symbol: 'VOO', name: 'Vanguard S&P 500', kind: 'etf', price: 590.2, prevClose: 588.1 },
  { symbol: 'GLD', name: 'SPDR Gold Shares', kind: 'etf', price: 245.1, prevClose: 246.3 },
  { symbol: 'BTC', name: 'Bitcoin', kind: 'crypto', price: 88410, prevClose: 86920 },
  { symbol: 'ETH', name: 'Ethereum', kind: 'crypto', price: 3120, prevClose: 3088 },
  {
    symbol: 'XAU-G',
    name: 'Gold, physical',
    kind: 'metal',
    price: 127.96,
    prevClose: 128.4,
    unit: 'g',
  },
]
export const assetOf = (s: string) => UNIVERSE.find((a) => a.symbol === s)!

export type Side = 'buy' | 'sell'
export type OrderType = 'market' | 'limit' | 'stop'
export type By = 'you' | 'lucius'

export interface Holding {
  symbol: string
  qty: number
  cost: number // average unit cost
}
export interface Order {
  id: string
  side: Side
  symbol: string
  qty: number
  type: OrderType
  price?: number // limit or stop level
  status: 'open' | 'filled' | 'cancelled'
  placedAt: string
  filledAt?: string
  fillPrice?: number
  note?: string
  by: By
}
export interface Activity {
  id: string
  at: string
  text: string
  amount: number
  note?: string
  by: By
}
export interface Portfolio {
  id: string
  name: string
  source: string
  byLucius: boolean
  startedAt: string
  startCash: number
  cash: number
  holdings: Holding[]
  orders: Order[]
  activity: Activity[]
  history: number[] // daily paper value, oldest first
  bench: number[] // VT, same days, rebased to startCash
  book: number[] // your real investments, same days, rebased
  archived?: boolean
}

export const SLIPPAGE_BPS = 5
const KEY = 'onw-proto-paper-v1'
const uid = () => Math.random().toString(36).slice(2, 10)
const nowIso = () => new Date().toISOString()

/** A made-up path from 1 to `end` over `n` days, with some wiggle, for the charts. */
function path(n: number, end: number, seed: number): number[] {
  const out: number[] = []
  for (let i = 0; i < n; i++) {
    const t = i / (n - 1)
    const wiggle = Math.sin(i * 0.9 + seed) * 0.006 + Math.sin(i * 0.37 + seed * 2) * 0.004
    out.push(1 + (end - 1) * t + wiggle * t)
  }
  out[n - 1] = end
  return out
}

function seed(): Portfolio[] {
  const days = (n: number) => new Date(Date.now() - n * 86400000).toISOString()
  const mk = (
    p: Omit<Portfolio, 'history' | 'bench' | 'book' | 'activity' | 'orders'> & {
      ret: number
      benchRet: number
      bookRet: number
      age: number
      orders?: Order[]
      activity?: Activity[]
    },
  ): Portfolio => ({
    ...p,
    orders: p.orders ?? [],
    activity: p.activity ?? [],
    history: path(p.age, 1 + p.ret, p.age).map((x) => x * p.startCash),
    bench: path(p.age, 1 + p.benchRet, p.age + 3).map((x) => x * p.startCash),
    book: path(p.age, 1 + p.bookRet, p.age + 7).map((x) => x * p.startCash),
  })
  return [
    mk({
      id: 'ppf_buyzone',
      name: 'Buy-zone strategy',
      source: 'Run by Lucius',
      byLucius: true,
      startedAt: days(23),
      startCash: 100000,
      cash: 18204.11,
      holdings: [
        { symbol: 'NVDA', qty: 120, cost: 170.7 },
        { symbol: 'VT', qty: 140, cost: 127.37 },
        { symbol: 'BTC', qty: 0.142, cost: 78516 },
        { symbol: 'XAU-G', qty: 80, cost: 133.67 },
        { symbol: 'MSFT', qty: 40, cost: 489.5 },
      ],
      ret: 0.0741,
      benchRet: 0.0308,
      bookRet: 0.0402,
      age: 23,
      orders: [
        {
          id: uid(),
          side: 'buy',
          symbol: 'AMD',
          qty: 30,
          type: 'limit',
          price: 142,
          status: 'open',
          placedAt: days(1),
          note: 'Entered its buy zone under 145.',
          by: 'lucius',
        },
      ],
      activity: [
        {
          id: uid(),
          at: days(2),
          text: 'Sold 40 INTC at 23.31',
          amount: 932.4,
          note: 'Closed below its stop at 23.50.',
          by: 'lucius',
        },
        {
          id: uid(),
          at: days(9),
          text: 'Bought 40 MSFT at 489.50',
          amount: -19580,
          note: 'Back in its buy zone.',
          by: 'lucius',
        },
        { id: uid(), at: days(23), text: 'Started with 100,000.00', amount: 100000, by: 'you' },
      ],
    }),
    mk({
      id: 'ppf_brk',
      name: 'Berkshire Hathaway',
      source: 'Copies 13F filings',
      byLucius: false,
      startedAt: days(61),
      startCash: 50000,
      cash: 1204.5,
      holdings: [
        { symbol: 'AAPL', qty: 90, cost: 214.0 },
        { symbol: 'VOO', qty: 48, cost: 560.1 },
      ],
      ret: 0.0372,
      benchRet: 0.049,
      bookRet: 0.051,
      age: 61,
      activity: [
        { id: uid(), at: days(61), text: 'Started with 50,000.00', amount: 50000, by: 'you' },
      ],
    }),
  ]
}

let state: Portfolio[] = load()
const listeners = new Set<() => void>()

function load(): Portfolio[] {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) return JSON.parse(raw) as Portfolio[]
  } catch {
    /* fall through to the seed */
  }
  return seed()
}
function commit(next: Portfolio[]) {
  state = next
  localStorage.setItem(KEY, JSON.stringify(state))
  listeners.forEach((l) => l())
}
const subscribe = (l: () => void) => {
  listeners.add(l)
  return () => listeners.delete(l)
}

export function usePaperPortfolios(): Portfolio[] {
  return useSyncExternalStore(subscribe, () => state)
}

// ---- derived figures -------------------------------------------------------------------

export const holdingValue = (h: Holding) => h.qty * assetOf(h.symbol).price
export const portfolioValue = (p: Portfolio) =>
  p.cash + p.holdings.reduce((n, h) => n + holdingValue(h), 0)
export const returnSinceStart = (p: Portfolio) => portfolioValue(p) / p.startCash - 1
export const rebasedEnd = (series: number[], p: Portfolio) =>
  (series[series.length - 1] ?? p.startCash) / p.startCash - 1
export const todayMove = (p: Portfolio) => {
  const now = p.holdings.reduce((n, h) => n + holdingValue(h), 0)
  const prev = p.holdings.reduce((n, h) => n + h.qty * assetOf(h.symbol).prevClose, 0)
  return prev ? now / prev - 1 : 0
}
export const dayOf = (p: Portfolio) =>
  Math.max(1, Math.round((Date.now() - Date.parse(p.startedAt)) / 86400000))
export const weights = (p: Portfolio) => {
  const total = portfolioValue(p)
  return p.holdings
    .map((h) => ({
      symbol: h.symbol === 'XAU-G' ? 'Gold' : h.symbol,
      weight: holdingValue(h) / total,
    }))
    .sort((a, b) => b.weight - a.weight)
}
export const openOrders = (p: Portfolio) => p.orders.filter((o) => o.status === 'open')

// ---- the market (prototype) ------------------------------------------------------------

let marketOverride: boolean | null = null
/** Regular US session, New York time; crypto and metals always trade in the prototype. */
export function isOpen(a: Asset): boolean {
  if (a.kind === 'crypto' || a.kind === 'metal') return true
  if (marketOverride != null) return marketOverride
  const ny = new Date(new Date().toLocaleString('en-US', { timeZone: 'America/New_York' }))
  const d = ny.getDay()
  const mins = ny.getHours() * 60 + ny.getMinutes()
  return d >= 1 && d <= 5 && mins >= 570 && mins < 960
}
export const fillPrice = (a: Asset, side: Side) =>
  a.price * (1 + (side === 'buy' ? 1 : -1) * (SLIPPAGE_BPS / 10000))

/** Prototype only: pretend the market opened, and fill what was waiting for it. */
export function openMarketNow() {
  marketOverride = true
  commit(state.map((p) => sweep(p)))
}

function reaches(o: Order, a: Asset): boolean {
  if (o.type === 'market') return isOpen(a)
  if (!isOpen(a) || o.price == null) return false
  if (o.type === 'limit') return o.side === 'buy' ? a.price <= o.price : a.price >= o.price
  return o.side === 'buy' ? a.price >= o.price : a.price <= o.price
}

function applyFill(p: Portfolio, o: Order): Portfolio {
  const a = assetOf(o.symbol)
  const px = o.type === 'limit' && o.price != null ? o.price : fillPrice(a, o.side)
  const amount = px * o.qty * (o.side === 'buy' ? -1 : 1)
  const holdings = [...p.holdings]
  const i = holdings.findIndex((h) => h.symbol === o.symbol)
  const h = i >= 0 ? holdings[i] : undefined
  if (o.side === 'buy') {
    if (h)
      holdings[i] = {
        ...h,
        qty: h.qty + o.qty,
        cost: (h.cost * h.qty + px * o.qty) / (h.qty + o.qty),
      }
    else holdings.push({ symbol: o.symbol, qty: o.qty, cost: px })
  } else if (h) {
    const left = h.qty - o.qty
    if (left <= 1e-9) holdings.splice(i, 1)
    else holdings[i] = { ...h, qty: left }
  }
  const filled: Order = { ...o, status: 'filled', filledAt: nowIso(), fillPrice: px }
  const verb = o.side === 'buy' ? 'Bought' : 'Sold'
  return {
    ...p,
    cash: p.cash + amount,
    holdings,
    orders: p.orders.map((x) => (x.id === o.id ? filled : x)),
    activity: [
      {
        id: uid(),
        at: nowIso(),
        text: `${verb} ${o.qty} ${a.symbol === 'XAU-G' ? 'g gold' : a.symbol} at ${px.toFixed(2)}`,
        amount,
        note: o.note,
        by: o.by,
      },
      ...p.activity,
    ],
  }
}

function sweep(p: Portfolio): Portfolio {
  let next = p
  for (const o of p.orders)
    if (o.status === 'open' && reaches(o, assetOf(o.symbol))) next = applyFill(next, o)
  return next
}

// ---- writes (the RFC's apply_paper_change ops) -------------------------------------------

export interface OrderDraft {
  side: Side
  symbol: string
  qty: number
  type: OrderType
  price?: number
  note?: string
}

/** What a place would do, without doing it (the ticket's preview). */
export function previewOrder(p: Portfolio, d: OrderDraft) {
  const a = assetOf(d.symbol)
  const px = d.type === 'limit' && d.price ? d.price : fillPrice(a, d.side)
  const total = px * d.qty
  const held = p.holdings.find((h) => h.symbol === d.symbol)?.qty ?? 0
  const reserved = openOrders(p)
    .filter((o) => o.side === 'buy')
    .reduce((n, o) => n + (o.price ?? fillPrice(assetOf(o.symbol), 'buy')) * o.qty, 0)
  const free = p.cash - reserved
  const error =
    !d.qty || d.qty <= 0
      ? 'Enter a quantity'
      : d.side === 'buy' && total > free
        ? `Only ${free.toFixed(2)} paper cash free`
        : d.side === 'sell' && d.qty > held
          ? `You hold ${held}`
          : d.type !== 'market' && !d.price
            ? 'Enter a price'
            : null
  const fillsNow = !error && reaches({ ...d, id: '', status: 'open', placedAt: '', by: 'you' }, a)
  const next = fillsNow
    ? 'Fills now'
    : d.type === 'market'
      ? 'Market closed. Fills at the next open.'
      : `Waits for ${d.price?.toFixed(2)}`
  return {
    price: px,
    total,
    cashAfter: p.cash + (d.side === 'buy' ? -total : total),
    error,
    next,
    fillsNow,
  }
}

export function placeOrder(portfolioId: string, d: OrderDraft): Order {
  const order: Order = { id: uid(), ...d, status: 'open', placedAt: nowIso(), by: 'you' }
  commit(
    state.map((p) => (p.id === portfolioId ? sweep({ ...p, orders: [order, ...p.orders] }) : p)),
  )
  return state.find((p) => p.id === portfolioId)!.orders.find((o) => o.id === order.id)!
}

export function cancelOrder(portfolioId: string, orderId: string) {
  commit(
    state.map((p) =>
      p.id === portfolioId
        ? {
            ...p,
            orders: p.orders.map((o) =>
              o.id === orderId ? { ...o, status: 'cancelled' as const } : o,
            ),
          }
        : p,
    ),
  )
}

export function createPortfolio(name: string, cash: number): string {
  const id = `ppf_${uid()}`
  const p: Portfolio = {
    id,
    name,
    source: 'Started with cash',
    byLucius: false,
    startedAt: nowIso(),
    startCash: cash,
    cash,
    holdings: [],
    orders: [],
    activity: [
      {
        id: uid(),
        at: nowIso(),
        text: `Started with ${cash.toLocaleString()}`,
        amount: cash,
        by: 'you',
      },
    ],
    history: [cash, cash],
    bench: [cash, cash],
    book: [cash, cash],
  }
  commit([p, ...state])
  return id
}

/** Start over: archive this run, begin a fresh one with the same name and cash. */
export function startOver(portfolioId: string): string {
  const old = state.find((p) => p.id === portfolioId)!
  const id = `ppf_${uid()}`
  const fresh: Portfolio = {
    ...old,
    id,
    startedAt: nowIso(),
    cash: old.startCash,
    holdings: [],
    orders: [],
    activity: [
      {
        id: uid(),
        at: nowIso(),
        text: `Started over with ${old.startCash.toLocaleString()}`,
        amount: old.startCash,
        by: 'you',
      },
    ],
    history: [old.startCash, old.startCash],
    bench: [old.startCash, old.startCash],
    book: [old.startCash, old.startCash],
    archived: false,
  }
  commit([fresh, ...state.map((p) => (p.id === portfolioId ? { ...p, archived: true } : p))])
  return id
}

export function deletePortfolio(portfolioId: string) {
  commit(state.filter((p) => p.id !== portfolioId))
}

/** Prototype only: back to the made-up starting data. */
export function resetPrototype() {
  marketOverride = null
  commit(seed())
}
