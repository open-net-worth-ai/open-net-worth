import type { Follow, FollowActivity } from '../../../api/followedPortfolios'
import { fCompact, fCompactAmount } from '../../../utils/format'
import { companyName, nameClasses, shortDate } from './following.format'

/** One row of "Trading activity", whatever the source: a quarter's change in a 13F (or an ETF's
 *  day-over-day change), a fund's exact Form 4 trade, or a House member's disclosed trade. */
export interface ActivityRow {
  key: string
  symbol: string
  name: string
  verb: string
  group: 'new' | 'added' | 'trimmed' | 'soldout' | 'buy' | 'sell'
  up: boolean
  /** The signed dollar change; a member's range has none (only `amountText`). */
  amount: number | null
  amountText: string
  /** For the bar and the sort: dollars, the top of a range for a member. */
  magnitude: number
  /** The signed share change; a member reports none. */
  shares: number | null
  sharesText: string
  /** The day it became public: the first day a copier could act. */
  filedAt: string
  /** Which filing made it public, so its date is never mistaken for another's. */
  source: '13F' | 'Form 4' | 'disclosure' | 'daily holdings'
  /** The exact trade day, when the source gives one. */
  tradedAt: string | null
  /** In words, for a screen reader and for Lucius when asked to copy it. */
  detail: string
  /** A dated trade's day, or its span when one order filled over several days. */
  dateText: string | null
  /** A Form 4 trade's average fill price (a member reports none). */
  avgPrice: number | null
  shareClass?: string | null
}

export type Names = Record<string, string>
const signed = (n: number, text: string) => `${n >= 0 ? '+' : '−'}${text}`
const usd = (n: number) => fCompactAmount(Math.abs(n), 'USD')
const nameOf = (names: Names, symbol: string, raw: string) => names[symbol] ?? companyName(raw)
const day = (d: string) => shortDate(d).replace(/, \d{4}$/, '')

function quarterRows(follow: Follow, names: Names): ActivityRow[] {
  const m = follow.model
  if (!m) return []
  const daily = follow.kind === 'etf_daily'
  const rows: ActivityRow[] = m.holdings
    .filter((h) => h.change !== 'unchanged' && h.change_value)
    .map((h) => {
      const value = h.change_value ?? 0
      const up = value > 0
      const group = h.change === 'new' ? 'new' : up ? 'added' : 'trimmed'
      const shares = h.shares_change ?? 0
      return {
        key: `q-${h.symbol}`,
        symbol: h.symbol,
        name: nameOf(names, h.symbol, h.name),
        verb: group === 'new' ? 'New' : up ? 'Increased' : 'Decreased',
        group,
        up,
        amount: value,
        amountText: signed(value, usd(value)),
        magnitude: Math.abs(value),
        shares,
        sharesText: signed(shares, fCompact(Math.abs(shares))),
        filedAt: m.filed_at,
        source: daily ? 'daily holdings' : '13F',
        tradedAt: daily ? m.as_of : null,
        detail: `${signed(shares, fCompact(Math.abs(shares)))} shares`,
        dateText: null,
        avgPrice: null,
        shareClass: h.share_class,
      }
    })
  for (const x of m.exited) {
    if (!x.prev_value) continue
    const shares = -(x.prev_shares ?? 0)
    rows.push({
      key: `x-${x.symbol}`,
      symbol: x.symbol,
      name: nameOf(names, x.symbol, x.name),
      verb: 'Sold out',
      group: 'soldout',
      up: false,
      amount: -x.prev_value,
      amountText: signed(-1, usd(x.prev_value)),
      magnitude: x.prev_value,
      shares,
      sharesText: signed(shares, fCompact(Math.abs(shares))),
      filedAt: m.filed_at,
      source: daily ? 'daily holdings' : '13F',
      tradedAt: daily ? m.as_of : null,
      detail: `all ${fCompact(x.prev_shares ?? 0)} shares`,
      dateText: null,
      avgPrice: null,
    })
  }
  return nameClasses(rows).sort((a, b) => b.magnitude - a.magnitude)
}

const counted = (follow: Follow) =>
  follow.activity.filter((t) => t.counted && t.symbol && t.side !== 'exchange')

/** A fund often fills one order over several days and Form 4s: back-to-back trades in the same
 *  stock and direction read as ONE move (summed, at their average price), not a wall of rows. */
function runs(trades: FollowActivity[]): FollowActivity[][] {
  const out: FollowActivity[][] = []
  for (const t of trades) {
    const last = out[out.length - 1]
    if (last && last[0]!.symbol === t.symbol && last[0]!.side === t.side) last.push(t)
    else out.push([t])
  }
  return out
}

function tradeRows(follow: Follow, names: Names): ActivityRow[] {
  return runs(counted(follow)).map((run, i) => {
    const t = run[0]!
    const first = run[run.length - 1]!
    const up = t.side === 'buy'
    const exact = run.every((x) => x.source === 'form4' && x.value)
    const value = run.reduce((n, x) => n + (x.value ?? 0), 0)
    const shares = run.reduce((n, x) => n + (x.shares ?? 0), 0)
    const high = run.reduce((n, x) => n + x.amount_high, 0)
    const low = run.reduce((n, x) => n + x.amount_low, 0)
    const fills = run.length > 1 ? `, ${run.length} trades` : ''
    return {
      key: `t-${t.date}-${t.symbol}-${i}`,
      symbol: t.symbol!,
      name: nameOf(names, t.symbol!, t.name),
      verb: up ? 'Bought' : 'Sold',
      group: up ? 'buy' : 'sell',
      up,
      amount: exact ? (up ? value : -value) : null,
      amountText: exact ? signed(up ? 1 : -1, usd(value)) : `${usd(low)}–${usd(high)}`,
      magnitude: exact ? value : high,
      shares: exact ? (up ? shares : -shares) : null,
      sharesText: exact ? signed(up ? 1 : -1, fCompact(shares)) : '',
      filedAt: first.filed_at,
      source: exact ? 'Form 4' : 'disclosure',
      tradedAt: first.date,
      detail: exact
        ? `${fCompact(shares)} shares at $${(value / (shares || 1)).toFixed(2)}${fills}`
        : `traded ${day(first.date)}, filed ${day(first.filed_at)}${fills}`,
      dateText: first.date === t.date ? day(t.date) : `${day(first.date)} – ${day(t.date)}`,
      avgPrice: exact && shares ? value / shares : null,
    }
  })
}

export interface ActivitySections {
  /** Exact, dated trades: a fund's Form 4s since its 13F, or a member's whole report. */
  trades: ActivityRow[]
  /** The latest filing's changes (a fund's 13F quarter, an ETF's last day). */
  changes: ActivityRow[]
}

export function activitySections(follow: Follow, names: Names = {}): ActivitySections {
  return {
    trades: tradeRows(follow, names),
    changes: follow.kind === 'house_member' ? [] : quarterRows(follow, names),
  }
}

export const hasActivity = (s: ActivitySections) => s.trades.length + s.changes.length > 0

// A share count within 1% of the quarter before is the same holding, as the model counts it.
const QUARTER_TOLERANCE = 0.01

export interface ChartTrade {
  date: string
  up: boolean
  /** A 13F change is dated by its quarter's end, not the trade day: it is drawn differently. */
  quarter?: boolean
}

/** Every move the source shows in one stock, for the chart's markers: each dated trade (a
 *  member's report, a fund's Form 4s) and the latest filing's change in it, at its quarter end. */
export function exactTrades(follow: Follow, symbol: string): ChartTrade[] {
  const out: ChartTrade[] = counted(follow)
    .filter((t) => t.symbol === symbol)
    .map((t) => ({ date: t.date, up: t.side === 'buy' }))
  // Each quarter the 13F shows the holding grew (B) or shrank (S), at that quarter's end.
  const quarters = [...(follow.history ?? [])].reverse()
  for (let i = 1; i < quarters.length; i++) {
    const before = quarters[i - 1]!.qty[symbol] ?? 0
    const now = quarters[i]!.qty[symbol] ?? 0
    const moved = before > 0 ? now / before - 1 : now > 0 ? 1 : 0
    if (Math.abs(moved) > QUARTER_TOLERANCE)
      out.push({ date: quarters[i]!.as_of, up: moved > 0, quarter: true })
  }
  return out
}

/** Every move in every stock, for the overview chart: dated trades, then the latest filing's
 *  changes at its quarter's end. */
export function allMoves(follow: Follow): (ChartTrade & { symbol: string })[] {
  const out: (ChartTrade & { symbol: string })[] = counted(follow).map((t) => ({
    symbol: t.symbol!,
    date: t.date,
    up: t.side === 'buy',
  }))
  const m = follow.model
  if (m && follow.kind !== 'house_member') {
    const date = follow.kind === 'etf_daily' ? m.as_of : m.as_of
    for (const h of m.holdings)
      if (h.change !== 'unchanged' && h.change_value)
        out.push({ symbol: h.symbol, date, up: h.change_value > 0, quarter: true })
    for (const x of m.exited) out.push({ symbol: x.symbol, date, up: false, quarter: true })
  }
  return out
}

export interface HistoryRow {
  key: string
  /** "2026 Q2" for a 13F quarter, the trade day or span for a Form 4 / member trade. */
  when: string
  sortDate: string
  up: boolean
  amount: number | null
  amountText: string
  shares: number | null
  sharesText: string
  source: string
}

/** One stock's history in this portfolio: each 13F quarter it grew or shrank (valued at that
 *  quarter's end price) and each dated trade, newest first. Also feeds the chart's bars. */
export function stockHistory(follow: Follow, symbol: string): HistoryRow[] {
  const out: HistoryRow[] = []
  const quarters = [...(follow.history ?? [])].reverse()
  for (let i = 1; i < quarters.length; i++) {
    const q = quarters[i]!
    const before = quarters[i - 1]!.qty[symbol] ?? 0
    const now = q.qty[symbol] ?? 0
    const moved = now - before
    const base = Math.max(before, now)
    if (!base || Math.abs(moved) / base <= QUARTER_TOLERANCE) continue
    const held = now || before
    const value = now ? q.value?.[symbol] : quarters[i - 1]!.value?.[symbol]
    const price = value && held ? value / held : null
    const amount = price ? moved * price : null
    out.push({
      key: `q-${q.as_of}`,
      when: quarterSpan(q.as_of),
      sortDate: q.as_of,
      up: moved > 0,
      amount,
      amountText: amount == null ? '—' : signed(amount, usd(amount)),
      shares: moved,
      sharesText: signed(moved, fCompact(Math.abs(moved))),
      source: '13F',
    })
  }
  for (const r of tradeRows(follow, {}).filter((t) => t.symbol === symbol))
    out.push({
      key: r.key,
      when: r.dateText ?? '',
      sortDate: r.tradedAt ?? r.filedAt,
      up: r.up,
      amount: r.amount,
      amountText: r.amountText,
      shares: r.shares,
      sharesText: r.sharesText || '—',
      source: r.source,
    })
  return out.sort((a, b) => b.sortDate.localeCompare(a.sortDate))
}

/** The filters a table offers — a 13F's own words for a fund (as every 13F tracker uses), buys
 *  and sells for a member's trades. One with no rows is not offered. */
export const FILTERS = {
  changes: [
    { value: 'all', label: 'All', groups: ['new', 'added', 'trimmed', 'soldout'] },
    { value: 'new', label: 'New', groups: ['new'] },
    { value: 'increase', label: 'Increase', groups: ['added'] },
    { value: 'decrease', label: 'Decrease', groups: ['trimmed'] },
    { value: 'soldout', label: 'Sold out', groups: ['soldout'] },
  ],
  // Every period of a fund speaks a holdings tracker's words: a dated buy is an Increase (or
  // New), a dated sale a Decrease (or Sold out) — see `withHoldingWords`.
  all: [
    { value: 'all', label: 'All', groups: ['new', 'added', 'trimmed', 'soldout'] },
    { value: 'new', label: 'New', groups: ['new'] },
    { value: 'increase', label: 'Increase', groups: ['added'] },
    { value: 'decrease', label: 'Decrease', groups: ['trimmed'] },
    { value: 'soldout', label: 'Sold out', groups: ['soldout'] },
  ],
  trades: [
    { value: 'all', label: 'All', groups: ['buy', 'sell'] },
    { value: 'buy', label: 'Bought', groups: ['buy'] },
    { value: 'sell', label: 'Sold', groups: ['sell'] },
  ],
} as const satisfies Record<
  string,
  readonly { value: string; label: string; groups: readonly ActivityRow['group'][] }[]
>

export type FilterSet = keyof typeof FILTERS
export type ActivityFilter = (typeof FILTERS)[FilterSet][number]['value']

export function filtersFor(set: FilterSet, rows: ActivityRow[]) {
  return FILTERS[set].filter((f) =>
    rows.some((r) => (f.groups as readonly string[]).includes(r.group)),
  )
}

export function matches(set: FilterSet, row: ActivityRow, filter: ActivityFilter): boolean {
  const f = FILTERS[set].find((x) => x.value === filter) ?? FILTERS[set][0]
  return (f.groups as readonly string[]).includes(row.group)
}

/** A span of time the table shows: a calendar quarter, or the days since the last quarter. */
export interface Period {
  key: string
  label: string
  /** Exclusive start, inclusive end (ISO dates). */
  from: string
  to: string
  /** Rows are each holding's change over the span, the dated trades in it, or every move. */
  shows: 'changes' | 'trades' | 'all'
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "Apr – Jun 2026" for the quarter ending on ``end`` (a quarter-end date). */
export function quarterSpan(end: string): string {
  const [y, m] = end.split('-').map(Number)
  const last = (m ?? 3) - 1
  return `${MONTHS[last - 2]} – ${MONTHS[last]} ${y}`
}

function quarterEnd(day: string): string {
  const [y, m] = day.split('-').map(Number)
  const q = Math.ceil((m ?? 1) / 3) * 3
  const last = new Date(Date.UTC(y!, q, 0)).getUTCDate()
  return `${y}-${String(q).padStart(2, '0')}-${String(last).padStart(2, '0')}`
}

const minusQuarter = (end: string) => {
  const [y, m] = end.split('-').map(Number)
  return quarterEnd(new Date(Date.UTC(y!, (m ?? 3) - 3, 1)).toISOString().slice(0, 10))
}

/** The spans a reader can pick, newest first. A fund: the days since its last quarter (when it
 *  has dated trades there), then each quarter its filings cover. A member: each quarter they
 *  traded in. An ETF: its latest day. */
export function periods(follow: Follow): Period[] {
  const trades = counted(follow)
  if (follow.kind === 'etf_daily') {
    const d = follow.model?.as_of ?? ''
    return [{ key: 'latest', label: COPY_LATEST_DAY, from: '', to: d, shows: 'changes' }]
  }
  if (follow.kind === 'house_member') {
    const ends = [...new Set(trades.map((t) => quarterEnd(t.date)))].sort().reverse()
    const kept = ends.slice(0, HISTORY_PERIODS)
    const all: Period[] =
      kept.length > 1
        ? [
            {
              key: 'all',
              label: allLabel(minusQuarter(kept[kept.length - 1]!)),
              from: minusQuarter(kept[kept.length - 1]!),
              to: '9999-12-31',
              shows: 'trades',
            },
          ]
        : []
    return [
      ...all,
      ...kept.map((end) => ({
        key: end,
        label: quarterSpan(end),
        from: minusQuarter(end),
        to: end,
        shows: 'trades' as const,
      })),
    ]
  }
  const quarters = follow.history ?? []
  const out: Period[] = []
  const latest = quarters[0]?.as_of
  const oldest = quarters[quarters.length - 1]?.as_of
  if (latest && trades.some((t) => t.date > latest))
    out.push({
      key: 'since',
      label: `Since ${shortDate(latest).replace(/, \d{4}$/, '')}`,
      from: latest,
      to: '9999-12-31',
      shows: 'trades',
    })
  // The whole range first, and by default: every move there is, newest first.
  if (oldest && latest && oldest !== latest)
    out.unshift({
      key: 'all',
      label: allLabel(oldest),
      from: oldest,
      to: '9999-12-31',
      shows: 'all',
    })
  for (let i = 0; i + 1 < quarters.length; i++)
    out.push({
      key: quarters[i]!.as_of,
      label: quarterSpan(quarters[i]!.as_of),
      from: quarters[i + 1]!.as_of,
      to: quarters[i]!.as_of,
      shows: 'changes',
    })
  return out
}

const HISTORY_PERIODS = 8
/** "Jul 2024 – Now": the whole range, named by when it starts (the month after ``from``). */
function allLabel(from: string): string {
  const [y, m] = from.split('-').map(Number)
  const start = new Date(Date.UTC(y!, m ?? 1, 1))
  return `${MONTHS[start.getUTCMonth()]} ${start.getUTCFullYear()} – Now`
}
const COPY_LATEST_DAY = 'Latest day'

/** Each holding's change between the two quarter-ends that bound ``period``, valued at the
 *  closing quarter's price, with the exact days of any dated trades in it. */
function changesBetween(follow: Follow, names: Names, period: Period): ActivityRow[] {
  const q = follow.history ?? []
  const now = q.find((x) => x.as_of === period.to)
  const before = q.find((x) => x.as_of === period.from)
  if (!now || !before) return []
  const dated = counted(follow).filter((t) => t.date > period.from && t.date <= period.to)
  const out: ActivityRow[] = []
  for (const symbol of new Set([...Object.keys(now.qty), ...Object.keys(before.qty)])) {
    const a = before.qty[symbol] ?? 0
    const b = now.qty[symbol] ?? 0
    const moved = b - a
    if (!Math.max(a, b) || Math.abs(moved) / Math.max(a, b) <= QUARTER_TOLERANCE) continue
    const held = b || a
    const value = b ? now.value?.[symbol] : before.value?.[symbol]
    const amount = value && held ? (moved * value) / held : null
    const up = moved > 0
    const group = a === 0 ? 'new' : b === 0 ? 'soldout' : up ? 'added' : 'trimmed'
    const days = dated
      .filter((t) => t.symbol === symbol)
      .map((t) => t.date)
      .sort()
    out.push({
      key: `p-${period.key}-${symbol}`,
      symbol,
      name: names[symbol] ?? symbol,
      verb: { new: 'New', soldout: 'Sold out', added: 'Increased', trimmed: 'Decreased' }[group],
      group,
      up,
      amount,
      amountText: amount == null ? '—' : signed(amount, usd(amount)),
      magnitude: Math.abs(amount ?? 0),
      shares: moved,
      sharesText: signed(moved, fCompact(Math.abs(moved))),
      filedAt: now.filed_at,
      source: '13F',
      tradedAt: days[0] ?? null,
      detail: `${signed(moved, fCompact(Math.abs(moved)))} shares`,
      dateText: days.length
        ? days.length === 1
          ? day(days[0]!)
          : `${day(days[0]!)} – ${day(days[days.length - 1]!)}`
        : null,
      avgPrice: null,
    })
  }
  return out.sort((x, y) => y.magnitude - x.magnitude)
}

/** A fund's dated trade in a holdings tracker's words: a buy into a stock it held none of at
 *  the last quarter-end is New, else an Increase; a sale that leaves none is Sold out, else a
 *  Decrease. A member's trades stay Bought / Sold (their reports carry no holdings). */
function withHoldingWords(follow: Follow, rows: ActivityRow[]): ActivityRow[] {
  if (follow.kind === 'house_member') return rows
  const quarters = follow.history ?? []
  return rows.map((r) => {
    if (r.group !== 'buy' && r.group !== 'sell') return r
    const before = quarters.find((q) => q.as_of < (r.tradedAt ?? ''))?.qty[r.symbol] ?? 0
    const after = before + (r.shares ?? 0)
    const group =
      r.group === 'buy' ? (before > 0 ? 'added' : 'new') : after > 0.5 ? 'trimmed' : 'soldout'
    const verb = { new: 'New', added: 'Increased', trimmed: 'Decreased', soldout: 'Sold out' }[
      group
    ]
    return { ...r, group, verb }
  })
}

/** The rows ``period`` shows. */
export function periodRows(follow: Follow, names: Names, period: Period): ActivityRow[] {
  return withHoldingWords(follow, rowsIn(follow, names, period))
}

function rowsIn(follow: Follow, names: Names, period: Period): ActivityRow[] {
  if (follow.kind === 'etf_daily') return quarterRows(follow, names)
  if (period.shows === 'all') {
    // Every quarter's changes and every dated trade since the last quarter; a quarter's row
    // says which quarter when it has no exact days.
    return periods(follow)
      .filter((p) => p.shows !== 'all')
      .flatMap((p) =>
        rowsIn(follow, names, p).map((r) => ({
          ...r,
          dateText: r.dateText ?? (p.shows === 'changes' ? p.label : null),
          tradedAt: r.tradedAt ?? p.to,
        })),
      )
      .sort((a, b) => (b.tradedAt ?? '').localeCompare(a.tradedAt ?? ''))
  }
  if (period.shows === 'trades')
    return tradeRows(follow, names).filter(
      (r) => (r.tradedAt ?? '') > period.from && (r.tradedAt ?? '') <= period.to,
    )
  return changesBetween(follow, names, period)
}

/** Where the charts start: the portfolio's oldest quarter on record, else a year back. */
export function historyStart(follow: Follow): string {
  const oldest = follow.history?.[follow.history.length - 1]?.as_of
  if (oldest) return oldest
  const d = new Date()
  d.setFullYear(d.getFullYear() - 1)
  return d.toISOString().slice(0, 10)
}

/** Each quarter's largest buy and largest sale by dollars, for the overview chart's markers. */
export function biggestPerQuarter(
  follow: Follow,
): { date: string; top: { symbol: string; up: boolean }[] }[] {
  return periods(follow)
    .filter((p) => p.shows === 'changes')
    .map((p) => {
      const rows = changesBetween(follow, {}, p)
      const buy = rows.filter((r) => r.up).sort((a, b) => b.magnitude - a.magnitude)[0]
      const sale = rows.filter((r) => !r.up).sort((a, b) => b.magnitude - a.magnitude)[0]
      return {
        date: p.to,
        top: [buy, sale].filter(Boolean).map((r) => ({ symbol: r!.symbol, up: r!.up })),
      }
    })
}
