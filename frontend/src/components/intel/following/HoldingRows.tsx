import { useState } from 'react'
import { fCompact, fPct } from '../../../utils/format'
import { ActionMenu, Button } from '../../ui'
import { Search } from '../../../ui/icons'
import type { FollowModel, PerfPeriod, PerfWindow } from '../../../api/followedPortfolios'
import { COPY } from './following.copy'
import { companyName, nameClasses, signedPct, toneOf, tradeLine } from './following.format'
import { Donut } from './Donut'
import { donutParts, TOP_SEGMENTS } from './donutParts'
import { SortTable, type Column } from './SortTable'
import { TickerDialog } from './TickerDialog'
import type { Names } from './activityRows'

interface Props {
  model: FollowModel
  period: PerfPeriod
  win: PerfWindow | undefined
  names: Names
  sectors: Record<string, string>
  /** Present for a fund: sets the ticker for a filing code ("" clears it). */
  onSetTicker?: (key: string, symbol: string) => Promise<void>
  /** Saving a ticker also starts watching (the portfolio is only open, not watched). */
  watchesToo?: boolean
}

interface Row {
  key: string
  symbol: string | null
  name: string
  filedName: string
  weight: number
  color: string
  shares: string
  ret: number | null
  /** The filing code a correction names; none for a member's holdings. */
  code: string | null
  shareClass?: string | null
}

const pct = (n: number) => fPct(n, { fraction: true })

const OTHER_SECTOR = 'Other'

/** The ring's slices: by sector when the sectors are known for most of the weight (as every
 *  holdings tracker shows the mix), else by stock. */
export function mixOf(model: FollowModel, sectors: Record<string, string>) {
  const known = model.holdings.reduce((n, h) => n + (sectors[h.symbol] ? h.weight : 0), 0)
  if (known < 0.5)
    return {
      bySector: false,
      weights: model.holdings.map((h) => ({ symbol: h.symbol, weight: h.weight })),
    }
  const sum = new Map<string, number>()
  for (const h of model.holdings) {
    const s = sectors[h.symbol] ?? OTHER_SECTOR
    sum.set(s, (sum.get(s) ?? 0) + h.weight)
  }
  return {
    bySector: true,
    weights: [...sum.entries()]
      .map(([symbol, weight]) => ({ symbol, weight }))
      .sort((a, b) => b.weight - a.weight),
  }
}

function rowsOf(
  model: FollowModel,
  names: Names,
  sectors: Record<string, string>,
  returns: Record<string, number>,
): Row[] {
  const mix = mixOf(model, sectors)
  const parts = donutParts(mix.weights, mix.weights.length)
  const others = parts.find((p) => !p.symbol)?.color ?? 'var(--line-strong)'
  const colorOf = (h: FollowModel['holdings'][number], i: number) => {
    const slice = mix.bySector ? (sectors[h.symbol] ?? OTHER_SECTOR) : h.symbol
    const at = mix.weights.findIndex((w) => w.symbol === slice)
    return at >= 0 && at < TOP_SEGMENTS ? parts[at]!.color : i < 0 ? others : others
  }
  const held: Row[] = model.holdings.map((h, i) => ({
    key: h.symbol,
    symbol: h.symbol,
    name: names[h.symbol] ?? companyName(h.name),
    filedName: h.name,
    weight: h.weight,
    color: colorOf(h, i),
    shares: h.last_trade ? tradeLine(h.last_trade) : h.shares ? fCompact(h.shares) : '—',
    ret: returns[h.symbol] ?? null,
    code: h.keys?.[0] ?? null,
    shareClass: h.share_class,
  }))
  // A holding with no ticker is a row like any other, never a footnote: copying the portfolio
  // without it would silently drop a position, so it says so and offers the fix.
  const missing: Row[] = model.unmatched.map((u) => ({
    key: `u-${u.key}`,
    symbol: null,
    name: companyName(u.name),
    filedName: u.name,
    weight: u.weight,
    color: 'var(--warn)',
    shares: '—',
    ret: null,
    code: u.key || null,
  }))
  return nameClasses([...held, ...missing]).sort((a, b) => b.weight - a.weight)
}

/** Every holding, largest first, beside the ring that shows the mix. Each row's dot is its colour
 *  in the ring; a holding with no ticker is a warning row with the fix on it. */
export function HoldingRows({
  model,
  period,
  win,
  names,
  sectors,
  onSetTicker,
  watchesToo,
}: Props) {
  const returns = win?.status === 'ok' ? win.holdings : {}
  const rows = rowsOf(model, names, sectors, returns)
  const [fixing, setFixing] = useState<Row | null>(null)
  const [query, setQuery] = useState('')
  const q = query.trim().toLowerCase()
  const shown = q
    ? rows.filter((r) => `${r.symbol ?? ''} ${r.name}`.toLowerCase().includes(q))
    : rows
  const mix = mixOf(model, sectors)
  const member = model.estimated
  const columns: Column<Row>[] = [
    {
      key: 'name',
      label: COPY.colStock,
      left: true,
      sortValue: (r) => r.symbol ?? r.name,
      cell: (r) => (
        <span className="mk-hcell">
          <span className="mk-row-dot" style={{ background: r.color }} aria-hidden="true" />
          {r.symbol ? (
            <span className="mk-hsym">{r.symbol}</span>
          ) : (
            <span className="fol-noticker">{COPY.noTicker}</span>
          )}
          <span className="mk-hnm">{r.name}</span>
        </span>
      ),
    },
    {
      key: 'weight',
      label: COPY.colWeight,
      sortValue: (r) => r.weight,
      cell: (r) => pct(r.weight),
    },
    {
      key: 'shares',
      label: member ? COPY.colLastTrade : COPY.colSharesHeld,
      className: () => 'muted',
      cell: (r) => r.shares,
    },
    {
      key: 'ret',
      label: COPY.perfColumn[period],
      sortValue: (r) => r.ret,
      className: (r) => toneOf(r.ret),
      cell: (r) => signedPct(r.ret),
    },
    ...(onSetTicker
      ? [
          {
            key: 'fix',
            label: '',
            action: true,
            className: () => 'fol-fix-cell',
            cell: (r: Row) =>
              !r.code ? null : r.symbol ? (
                <span className="fol-row-menu">
                  <ActionMenu
                    label={COPY.holdingActions(r.symbol)}
                    items={[{ label: COPY.tickerFixAction, onSelect: () => setFixing(r) }]}
                  />
                </span>
              ) : (
                <Button size="sm" onClick={() => setFixing(r)}>
                  {COPY.tickerSetAction}
                </Button>
              ),
          },
        ]
      : []),
  ]
  return (
    <section className="fol-holdings" aria-label={COPY.holdingsTitle}>
      <div className="fol-mix">
        <Donut weights={mix.weights} count={model.holdings.length} size={148} />
        <ul className="fol-legend">
          {donutParts(mix.weights, mix.weights.length).map((p) => (
            <li key={p.label}>
              <i style={{ background: p.color }} aria-hidden="true" />
              <span>{p.label}</span>
              <span className="fol-num">{pct(p.weight)}</span>
            </li>
          ))}
        </ul>
      </div>
      <div className="fol-sec">
        <label className="fol-search">
          <Search size={15} strokeWidth={1.6} aria-hidden="true" />
          <span className="ui-visually-hidden">{COPY.holdingsSearch}</span>
          <input
            type="search"
            placeholder={COPY.holdingsSearch}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        {shown.length === 0 && <p className="fol-empty">{COPY.noHoldingMatch(query.trim())}</p>}
        <SortTable
          rows={shown}
          columns={columns}
          rowKey={(r) => r.key}
          label={COPY.holdingsTitle}
          initialSort="weight"
        />
      </div>
      {fixing && onSetTicker && (
        <TickerDialog
          filedName={fixing.filedName}
          current={fixing.symbol}
          watchesToo={!!watchesToo}
          onSave={(symbol) => onSetTicker(fixing.code!, symbol)}
          onClose={() => setFixing(null)}
        />
      )}
    </section>
  )
}
