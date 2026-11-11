import { useState } from 'react'
import { fCompact, fPct } from '../../../utils/format'
import { ActionMenu, Button, TextField } from '../../ui'
import type { FollowModel, PerfPeriod, PerfWindow } from '../../../api/followedPortfolios'
import { COPY } from './following.copy'
import { companyName, nameClasses, signedPct, toneOf, tradeLine } from './following.format'
import { SectorMap } from './SectorMap'
import { NameCell } from './NameCell'
import { useAssetNav } from '../../../state/assetNav'
import { tilesOf, UNCHANGED } from './sectorTiles'
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
  /** The tickers in your own book, upper-cased: their rows say "You own". */
  held: Set<string>
}

interface Row {
  key: string
  symbol: string | null
  name: string
  filedName: string
  weight: number
  /** The map tile it belongs to (its sector, or itself); none for a holding with no ticker. */
  tile: string | null
  shares: string
  ret: number | null
  /** The filing code a correction names; none for a member's holdings. */
  code: string | null
  shareClass?: string | null
}

const pct = (n: number) => fPct(n, { fraction: true })

const OTHER_SECTOR = 'Other'

/** Sectors are known for most of the weight: the map is drawn by sector, else by stock. */
function sectorsKnown(model: FollowModel, sectors: Record<string, string>): boolean {
  return model.holdings.reduce((n, h) => n + (sectors[h.symbol] ? h.weight : 0), 0) >= 0.5
}

function rowsOf(
  model: FollowModel,
  names: Names,
  sliceOf: (symbol: string) => string,
  returns: Record<string, number>,
): Row[] {
  const held: Row[] = model.holdings.map((h) => ({
    key: h.symbol,
    symbol: h.symbol,
    name: names[h.symbol] ?? companyName(h.name),
    filedName: h.name,
    weight: h.weight,
    tile: sliceOf(h.symbol),
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
    tile: null,
    shares: '—',
    ret: null,
    code: u.key || null,
  }))
  return nameClasses([...held, ...missing]).sort((a, b) => b.weight - a.weight)
}

/** The map of what they hold (a tile per sector, coloured by what they did with it), then every
 *  holding, largest first. Tapping a tile narrows the list to it; a holding with no ticker is a
 *  warning row with the fix on it. */
export function HoldingRows({
  model,
  period,
  win,
  names,
  sectors,
  onSetTicker,
  watchesToo,
  held,
}: Props) {
  const { openAsset } = useAssetNav()
  const returns = win?.status === 'ok' ? win.holdings : {}
  const bySector = sectorsKnown(model, sectors)
  const sliceOf = (symbol: string) => (bySector ? (sectors[symbol] ?? OTHER_SECTOR) : symbol)
  const rows = rowsOf(model, names, sliceOf, returns)
  const tiles = tilesOf(model, bySector, sliceOf)
  const [fixing, setFixing] = useState<Row | null>(null)
  const [query, setQuery] = useState('')
  const [tile, setTile] = useState<string | null>(null)
  const pickedTile = tiles.find((t) => t.key === tile) ?? null
  const q = query.trim().toLowerCase()
  const shown = rows.filter(
    (r) =>
      (!tile || r.tile === tile) && (!q || `${r.symbol ?? ''} ${r.name}`.toLowerCase().includes(q)),
  )
  const member = model.estimated
  const columns: Column<Row>[] = [
    {
      key: 'name',
      label: COPY.colStock,
      left: true,
      sortValue: (r) => r.symbol ?? r.name,
      cell: (r) => (
        <NameCell symbol={r.symbol} name={r.name} held={!!r.symbol && held.has(r.symbol)} />
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
              !r.code ? null : (
                // The fix is its own action: it must not also open the row's stock.
                <span onClick={(e) => e.stopPropagation()}>{fixCell(r)}</span>
              ),
          },
        ]
      : []),
  ]
  function fixCell(r: Row) {
    return r.symbol ? (
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
    )
  }
  return (
    <section className="fol-holdings" aria-label={COPY.holdingsTitle}>
      {tiles.length > 1 && (
        <h2 className="fol-sec-title">{bySector ? COPY.bySectorTitle : COPY.bySizeTitle}</h2>
      )}
      {tiles.length > 1 && (
        <SectorMap
          tiles={tiles}
          picked={tile}
          onPick={setTile}
          label={bySector ? COPY.mapBySector : COPY.mapByStock}
        />
      )}
      <div className="fol-sec">
        <h2 className={`fol-sec-title${tiles.length > 1 ? ' is-next' : ''}`}>
          {/* The list is the top holdings: never call a cut list "all". */}
          {model.positions != null && model.positions > model.holdings.length
            ? COPY.topHoldingsTitle(rows.length, model.positions)
            : COPY.allHoldingsTitle(rows.length)}
        </h2>
        {/* A plain row (kit TextField): Investments' .mk-toolbar is sticky and opaque on a phone
            and painted over whatever sat above it. */}
        <div className="fol-htools">
          <TextField
            type="search"
            aria-label={COPY.holdingsSearch}
            placeholder={`${COPY.holdingsSearch}…`}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        {/* A picked tile, however small, is named here with its share and change. */}
        {pickedTile && (
          <div className="fol-picked">
            <span className="fol-picked-name">{pickedTile.label}</span>
            <span className="fol-picked-fig">{(pickedTile.weight * 100).toFixed(1)}%</span>
            {pickedTile.change != null && Math.abs(pickedTile.change) >= UNCHANGED && (
              <span className={pickedTile.change > 0 ? 'up' : 'down'}>
                {signedPct(pickedTile.change)}
              </span>
            )}
            <Button size="sm" variant="ghost" onClick={() => setTile(null)}>
              {COPY.showAll}
            </Button>
          </div>
        )}
        {shown.length === 0 && <p className="fol-empty">{COPY.noHoldingMatch(query.trim())}</p>}
        <SortTable
          rows={shown}
          columns={columns}
          rowKey={(r) => r.key}
          label={COPY.holdingsTitle}
          initialSort="weight"
          // A row opens its stock, as a holding does everywhere else in the app.
          onRowClick={(r) => r.symbol && openAsset({ ticker: r.symbol, label: r.name })}
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
