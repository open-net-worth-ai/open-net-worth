// useInvestmentsBook — adapts the SERVER's investments book
// (GET /api/investments/book, backend/services/investments_book.py) into the `book`/`d` shape
// the prototype components expect.
//
// It computes nothing. The grand total, every row's weight, the per-class subtotals, today's
// and unrealized P&L, single-name concentration, allocation-vs-target and bond YTM all arrive
// computed; this file renames them. It used to derive all of that in the browser from
// `usePortfolio.compute()`'s output — a second implementation of the app's money math, the
// pattern that shipped a S$120,000 net-worth headline
// (ADR 2026-07-31-overview-aggregates-are-server-computed).
//
// Shape contract (mirrors the prototype's derive() + buildBook() output):
//   book.sections: { cls, tgt, rows, subtotal, cur }[]
//   book.all: BookRow[]
//   book.grand: number
//   book.empty: boolean
//   d  — derived view-model: todayPL, todayPct, movers, risk, alloc, topName, flags
//
// BookRow fields: sym, name, val, pct (weight), pl, plpct, day, dayPct,
//   cls, sub, account, ccy, qty, price, cost, wt,
//   coupon, maturity, ytm (bond columns — others null).

import { useMemo } from 'react'
import type { ComputedRow } from '../../../types/portfolio'
import type { InvestedBookRow, UntrackedExcluded } from '../../../types/aggregates'
import { usePortfolioAnalytics } from '../../../hooks/serverState'
import {
  ASSET_CLASS_COLOR,
  ASSET_CLASS_LABEL,
  ASSET_CLASS_ORDER,
  paletteColor,
} from '../../../utils/constants'

/** Display label for an asset class — presentation, which stays on the client. */
function clsLabel(assetClass: string): string {
  return ASSET_CLASS_LABEL[assetClass] ?? assetClass
}

// ── Types ──────────────────────────────────────────────────────────────────

export interface BookRow {
  // Stable unique key for React reconciliation — uses position_id when available,
  // otherwise falls back to sym+account+sub (entitlement-distinguishing: separates vested
  // vs locked positions on the same instrument in the same account).
  rowKey: string
  sym: string
  name: string
  value: number
  wt: number // portfolio weight 0-100
  pl: number | null // unrealized P&L in base ccy; null = cost basis unknown
  plpct: number | null // P&L %; null = cost basis unknown
  day: number // daily change %
  cls: string // asset_class key (e.g. 'equity')
  sub: string // asset_subclass or cls fallback
  account: string
  ccy: string
  qty: number
  price: number
  cost: number // unit cost in display currency
  // bond fields
  coupon: number | null
  maturity: string | null
  ytm: number | null
  // Number of underlying positions folded into this row by mergeByInstrument — 1 for a
  // normal single-position row, >1 when the same instrument is held at multiple accounts
  // and merged under a non-account grouping. Callers use this to disable per-position
  // actions (Sell/Dividend, the mention EntityRow id) on an ambiguous merged row.
  mergedCount: number
  // internal raw ref — for a merged row, the FIRST contributing position (representative
  // instrument-level fields only: instrument_id/ticker/label/asset_class; never its
  // account-specific cost/currency, which the merge already folded properly above).
  _raw: ComputedRow
}

export interface BookSection {
  cls: string
  tgt: number | null
  rows: BookRow[]
  subtotal: number
  cur: number // current % of grand
}

export interface Book {
  sections: BookSection[]
  all: BookRow[]
  grand: number
  empty: boolean
}

export interface DerivedBook extends Book {
  /** The analytics read has not answered yet, so `empty` means "nothing KNOWN yet", not
   *  "nothing owned". A caller must skeleton on this rather than render an empty state — an
   *  empty state shown over a pending read is a lie about the user's money. */
  loading: boolean
  todayPL: number
  todayPct: number
  movers: BookRow[]
  risk: Array<{ nm: string; pct: number; val: number; hot: boolean }>
  // `cur`/`tgt` are ROUNDED, for display only. `driftPp`/`tradeBase` are the server's unrounded
  // figures (MATH-38) — a caller that sizes a trade off the rounded `cur - tgt` compounds the
  // rounding into real money (book 1,000,000, current 32.6%, target 29.4% → the true trade is
  // -32,000 but rounded percents give -40,000). Never re-derive a trade amount from cur/tgt.
  alloc: Array<{ cls: string; cur: number; tgt: number; driftPp: number; tradeBase: number }>
  /** Drift (pp) at/above which a class is "off target" — the server's ONE constant
   *  (`rebalance.DRIFT_FLAG_PP`), echoed here so no surface hardcodes its own number (MATH-37).
   *  Falls back to 5 only while the analytics read is in flight or absent. */
  thresholdPp: number
  topName: { nm: string; pct: number; val: number; hot: boolean } | null
  /** Unrealized P&L across the book, and as a return on what was PAID (not on today's value).
   *  Server-computed — the Masthead used to derive the percentage itself. */
  totalPL: number
  totalPLPct: number
  /** The book's share of everything owned, and the denominator itself. Numerator and
   *  denominator come from the SAME live-priced projection, so this can never compare two
   *  price snapshots — which is why it is not a separate prop any more. */
  pctOfPortfolio: number
  portfolioTotal: number
  /** One row per instrument, cross-account sums already done by the server. */
  mergedRows?: BookRow[]
  /** Brokerage total accounts' holdings not entered yet — invested, but left out of every
   *  figure above (the server says how much). Null when there is none, or the flag is off. */
  untracked: UntrackedExcluded | null
}

/** The server's `untracked_excluded`, or null when nothing was left out. */
function untrackedOf(server: { untracked_excluded?: UntrackedExcluded } | null) {
  const u = server?.untracked_excluded
  return u && u.accounts.length > 0 ? u : null
}

// ── Server → BookRow ───────────────────────────────────────────────────────

/** Rename one server book row onto `BookRow`. `_raw` is looked up from the shared portfolio
 *  rows so modals/asset-detail still get the full holding; a merged row has no single
 *  position, so it carries its first contributor purely for instrument-level fields. */
function toBookRow(r: InvestedBookRow, rawByKey: Map<string, ComputedRow>): BookRow {
  return {
    rowKey: r.row_key,
    sym: r.symbol,
    name: r.name,
    value: r.value,
    wt: r.weight,
    pl: r.pl,
    plpct: r.pl_pct,
    day: r.day_pct,
    cls: r.cls,
    sub: r.sub,
    account: r.account,
    ccy: r.currency,
    qty: r.qty,
    price: r.price,
    cost: r.cost,
    coupon: r.coupon,
    maturity: r.maturity,
    ytm: r.ytm,
    mergedCount: r.merged_count,
    _raw: (rawByKey.get(r.position_id ?? '') ??
      rawByKey.get(r.instrument_id ?? '') ??
      ({ label: r.name, symbol: r.symbol, asset_class: r.cls } as unknown)) as ComputedRow,
  }
}

// ── Public hook ───────────────────────────────────────────────────────────

/** The book, server-computed. `invRows` is used ONLY to attach each row's underlying
 *  `ComputedRow` (`_raw`) for modals and asset-detail — never to derive a figure. */
export function useInvestmentsBook(invRows: ComputedRow[]): DerivedBook {
  // The book is a VIEW of the portfolio (`/api/portfolio/analytics` → `book`), not its own
  // tab-named resource — so mounting the Investments tab and the Overview costs one request.
  const query = usePortfolioAnalytics()
  const server = query.data?.book ?? null
  const loading = query.initialLoading

  return useMemo<DerivedBook>(() => {
    const rawByKey = new Map<string, ComputedRow>()
    for (const r of invRows) {
      if (r.position_id) rawByKey.set(r.position_id, r)
      if (r.instrument_id && !rawByKey.has(r.instrument_id)) rawByKey.set(r.instrument_id, r)
    }

    if (!server || server.empty) {
      return {
        sections: [],
        all: [],
        grand: 0,
        empty: true,
        loading,
        todayPL: 0,
        todayPct: 0,
        movers: [],
        risk: [],
        alloc: [],
        thresholdPp: server?.threshold_pp ?? 5,
        topName: null,
        totalPL: 0,
        totalPLPct: 0,
        pctOfPortfolio: 0,
        portfolioTotal: 0,
        untracked: untrackedOf(server),
      }
    }

    const all = server.rows.map((r) => toBookRow(r, rawByKey))
    const byKey = new Map(all.map((r) => [r.rowKey, r]))
    const sections: BookSection[] = server.sections.map((s) => ({
      cls: s.cls,
      tgt: s.target_pct || null,
      rows: s.row_keys.map((k) => byKey.get(k)).filter((r): r is BookRow => !!r),
      subtotal: s.subtotal,
      cur: Math.round(s.current_pct),
    }))

    return {
      sections,
      all,
      grand: server.grand,
      empty: false,
      loading,
      todayPL: server.today_pl,
      todayPct: server.today_pct,
      totalPL: server.total_pl,
      totalPLPct: server.total_pl_pct,
      pctOfPortfolio: server.pct_of_portfolio,
      portfolioTotal: server.portfolio_total,
      movers: server.movers.map((k) => byKey.get(k)).filter((r): r is BookRow => !!r),
      risk: server.concentration.map((c) => ({ nm: c.name, pct: c.pct, val: c.value, hot: c.hot })),
      alloc: server.allocation.map((a) => ({
        cls: a.cls,
        cur: Math.round(a.current_pct),
        tgt: Math.round(a.target_pct),
        driftPp: a.drift_pp,
        tradeBase: a.trade_base,
      })),
      thresholdPp: server.threshold_pp ?? 5,
      topName: server.top_name
        ? {
            nm: server.top_name.name,
            pct: server.top_name.pct,
            val: server.top_name.value,
            hot: server.top_name.hot,
          }
        : null,
      /** One row per instrument — the cross-account sum happens on the server. */
      mergedRows: server.merged_rows.map((r) => toBookRow(r, rawByKey)),
      untracked: untrackedOf(server),
    } as DerivedBook
  }, [server, invRows, loading])
}

// ── mergeByInstrument ─────────────────────────────────────────────────────
//
// The custodian model is one position per instrument x account, so the SAME instrument at two
// brokers is two rows. When the active grouping does not itself distinguish accounts they must
// read as ONE row — but merging SUMS value, P&L, quantity and cost basis, so it happens in
// `backend/services/investments_book.py` and arrives as `merged_rows`. This is the lookup.
//
// Grouping BY account is exempt: each group is already scoped to one account. So is an explicit
// account FILTER (the "Filter by account" control) — `rows` is already narrowed to one account
// before this runs, so `mergeByInstrument` would substitute back the GLOBAL cross-account sum
// the instant that account also holds a fraction of the same instrument elsewhere, reporting
// e.g. "JP Morgan: 37% of book" when JP Morgan alone holds a much smaller share. Both callers
// (grouping and filtering) must ask `shouldMergeAcrossAccounts` before calling this — never call
// `mergeByInstrument` directly against an account-scoped `rows`.
export function shouldMergeAcrossAccounts(groupBy: GroupByKey, accountFilter: string): boolean {
  return groupBy !== 'account' && !accountFilter
}

export function mergeByInstrument(rows: BookRow[], merged: BookRow[]): BookRow[] {
  const wanted = new Set(rows.map((r) => r.rowKey))
  // A merged row stands in for its contributors, so keep it when ANY of them is in `rows`.
  const contributors = new Map<string, BookRow>()
  for (const m of merged)
    contributors.set(`${m._raw?.instrument_id ?? m.sym}\0${m._raw?.vesting_state}`, m)
  const out: BookRow[] = []
  const seen = new Set<string>()
  for (const r of rows) {
    const key = `${r._raw?.instrument_id ?? r.sym}\0${r._raw?.vesting_state}`
    const m = contributors.get(key)
    const pick = m ?? r
    if (seen.has(pick.rowKey)) continue
    seen.add(pick.rowKey)
    out.push(pick)
  }
  return out.filter((r) => r.mergedCount > 1 || wanted.has(r.rowKey))
}

// ── buildGroups: group filtered rows for FeedCards/FeedTable ──────────────

export type GroupByKey = 'asset_class' | 'account' | 'currency' | 'none'

export interface BookGroup {
  key: string
  label: string | null
  color: string | null
  rows: BookRow[]
  subtotal: number
  cur: number
  tgt: number | null
  cls: string | null
}

export function buildGroups(rows: BookRow[], by: GroupByKey, grand: number): BookGroup[] {
  if (by === 'none') {
    const sub = rows.reduce((s, r) => s + r.value, 0)
    return [
      {
        key: 'all',
        label: null,
        color: null,
        rows,
        subtotal: sub,
        cur: grand ? Math.round((sub / grand) * 100) : 0,
        tgt: null,
        cls: null,
      },
    ]
  }

  const keyOf = (r: BookRow): string =>
    by === 'account' ? r.account : by === 'currency' ? r.ccy : r.cls

  const map = new Map<string, BookRow[]>()
  rows.forEach((r) => {
    const k = keyOf(r)
    if (!map.has(k)) map.set(k, [])
    map.get(k)!.push(r)
  })

  let entries = [...map.entries()]
  if (by === 'asset_class') {
    entries.sort((a, b) => {
      const ia = ASSET_CLASS_ORDER.indexOf(a[0])
      const ib = ASSET_CLASS_ORDER.indexOf(b[0])
      return (
        (ia === -1 ? ASSET_CLASS_ORDER.length : ia) - (ib === -1 ? ASSET_CLASS_ORDER.length : ib)
      )
    })
  } else {
    entries.sort(
      (a, b) => b[1].reduce((s, r) => s + r.value, 0) - a[1].reduce((s, r) => s + r.value, 0),
    )
  }

  return entries.map(([key, rs]) => {
    const sub = rs.reduce((s, r) => s + r.value, 0)
    const isClass = by === 'asset_class'
    const color = isClass ? (ASSET_CLASS_COLOR[key] ?? paletteColor(key)) : paletteColor(key)
    const label = isClass ? clsLabel(key) : key
    return {
      key,
      label,
      color,
      rows: rs,
      subtotal: sub,
      cur: grand ? Math.round((sub / grand) * 100) : 0,
      tgt: null,
      cls: isClass ? key : null,
    }
  })
}

// ── sortRows ───────────────────────────────────────────────────────────────

export function sortRows(rows: BookRow[], key: string, dir: number): BookRow[] {
  const v = (r: BookRow): string | number | null => {
    switch (key) {
      case 'sym':
        return r.sym
      case 'account':
        return r.account
      case 'ccy':
        return r.ccy
      case 'wt':
        return r.wt
      case 'pl':
        return r.pl
      case 'plpct':
        return r.plpct
      case 'day':
        return r.day
      case 'qty':
        return r.qty
      case 'price':
        return r.price
      case 'cost':
        return r.cost
      default:
        return r.value
    }
  }
  return [...rows].sort((a, b) => {
    const x = v(a)
    const y = v(b)
    // An unknown figure (null P&L) sinks to the bottom in either direction.
    if (x == null || y == null) return x == null ? (y == null ? 0 : 1) : -1
    return typeof x === 'string'
      ? dir * x.localeCompare(y as string)
      : dir * ((x as number) - (y as number))
  })
}
