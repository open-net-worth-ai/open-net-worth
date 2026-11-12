import { useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Button, SegmentedControl } from '../../ui'
import { useAssetNav } from '../../../state/assetNav'
import { FilterChips, ToolSelect } from './Controls'
import { NameCell } from './NameCell'
import { LatestMoves } from './LatestMoves'

const SHOW_LATEST = false
import { ArrowUpRight, ChevronLeft, ChevronRight } from '../../../ui/icons'
import { Tooltip } from '../../ui/Tooltip'
import type { Follow, FollowPerformance } from '../../../api/followedPortfolios'
import { COPY } from './following.copy'
import { shortDate, signedPct, toneOf } from './following.format'
import { CopyTradeButton } from './CopyTradeButton'
import {
  exactTrades,
  periodRows,
  rangePeriods,
  filtersFor,
  matches,
  type ActivityFilter,
  type ActivityRow,
  type FilterSet,
  type Names,
} from './activityRows'
import { SortTable, type Column } from './SortTable'
import { TradeChart } from './TradeChart'
import { historyStart } from './activityRows'
import { TradeDetails } from './TradeDetails'
import { PortfolioActivityChart } from './PortfolioActivityChart'
import { useSinceFiled } from './useFollowing'
import { spanReturn } from './spanReturn'

interface Props {
  follow: Follow
  perf: FollowPerformance | undefined
  names: Names
  /** The tickers in your own book, upper-cased. */
  held: Set<string>
  onAskLucius?: (text: string) => void
}

function Spotlight({
  row,
  follow,
  benchmark,
  onAskLucius,
  from,
  rangeWords,
}: {
  row: ActivityRow
  follow: Follow
  benchmark: string
  onAskLucius?: (t: string) => void
  /** The chosen period's first day: the stock's move is measured from it, as the chart is. */
  from: string
  rangeWords: string
}) {
  const since = useSinceFiled(row.symbol, benchmark, from)
  const trades = useMemo(() => exactTrades(follow, row.symbol), [follow, row.symbol])
  const [details, setDetails] = useState(false)
  return (
    <div className="fol-spot">
      {/* The move's figures, as Performance shows its own (a small name over a large value).
          The stock itself is the block's title. */}
      <div className="fol-spot-figrow">
        {since.stock == null ? (
          <p className="fol-spot-sub">{COPY.noPriceSince}</p>
        ) : (
          <div className="fol-ret3">
            {since.price != null && (
              <div className="fol-ret3-item">
                <span className="fol-ret3-l">{COPY.priceLabel}</span>
                <span className="fol-ret3-v">${since.price.toFixed(2)}</span>
              </div>
            )}
            <div className="fol-ret3-item">
              <span className="fol-ret3-l">{row.symbol}</span>
              <span className={`fol-ret3-v ${toneOf(since.stock)}`}>{signedPct(since.stock)}</span>
            </div>
            <div className="fol-ret3-item">
              <span className="fol-ret3-l">{benchmark}</span>
              <span className={`fol-ret3-v ${toneOf(since.bench)}`}>{signedPct(since.bench)}</span>
            </div>
          </div>
        )}
      </div>
      {/* What to do about the move: beside its figures on a wide card, under its chart on a
          narrow one (one grid, see .fol-spot). */}
      <div className="fol-spot-actions">
        <Button size="sm" onClick={() => setDetails(true)}>
          {COPY.tradeDetails}
          <ChevronRight size={14} aria-hidden="true" />
        </Button>
        <CopyTradeButton
          who={follow.name}
          what={`${row.verb.toLowerCase()} ${row.symbol} (${row.name}), ${row.amountText}, ${row.detail}, made public ${shortDate(row.filedAt)}${since.stock != null ? `, ${signedPct(since.stock)} ${rangeWords}` : ''}`}
          onAskLucius={onAskLucius}
        />
      </div>
      <TradeChart row={row} trades={trades} from={from} />
      {details && (
        <TradeDetails
          row={row}
          follow={follow}
          benchmark={benchmark}
          onClose={() => setDetails(false)}
        />
      )}
    </div>
  )
}

const nameColumn = (
  held: Set<string>,
  exchanges: Record<string, string> = {},
): Column<ActivityRow> => ({
  key: 'name',
  label: COPY.colStock,
  left: true,
  sortValue: (r) => r.name,
  cell: (r) => (
    <NameCell
      symbol={r.symbol}
      name={r.name}
      held={held.has(r.symbol)}
      linkTicker
      contracts={r.option ? [r.option] : undefined}
      exchange={exchanges[r.symbol]}
    />
  ),
})
/** The change, with a bar for its size against the period's largest move. */
const amountColumn = (max: number): Column<ActivityRow> => ({
  key: 'amount',
  label: COPY.colChange,
  primary: true,
  sortValue: (r) => r.amount ?? r.magnitude * (r.up ? 1 : -1),
  className: (r) => (r.up ? 'up' : 'down'),
  cell: (r) => (
    <span className="fol-chg">
      <span className="fol-chg-track" aria-hidden="true">
        <span
          className="fol-chg-bar"
          style={{ width: `${max > 0 ? Math.max(2, (r.magnitude / max) * 100) : 0}%` }}
        />
      </span>
      <span className="fol-chg-v">{r.amountText}</span>
    </span>
  ),
})
const sharesColumn: Column<ActivityRow> = {
  key: 'shares',
  label: COPY.colShares,
  wide: true,
  sortValue: (r) => r.shares,
  className: (r) => (r.up ? 'up' : 'down'),
  cell: (r) => r.sharesText,
}
const dateColumn = (label: string): Column<ActivityRow> => ({
  key: 'date',
  label,
  secondary: true,
  sortValue: (r) => r.tradedAt,
  className: () => 'muted',
  cell: (r) => r.dateText ?? '—',
})

/** A dated trade shows when and at what price; a filing's change shows how much moved. */
function columns(
  set: FilterSet,
  member: boolean,
  max: number,
  held: Set<string>,
  exchanges?: Record<string, string>,
): Column<ActivityRow>[] {
  const amountColumnFor = amountColumn(max)
  const nameCol = nameColumn(held, exchanges)
  if (set === 'changes' || set === 'all')
    return [nameCol, dateColumn(COPY.colDate), amountColumnFor, sharesColumn]
  if (member)
    return [
      nameCol,
      dateColumn(COPY.colTraded),
      {
        key: 'filed',
        label: COPY.colFiled,
        sortValue: (r) => r.filedAt,
        className: () => 'muted',
        wide: true,
        cell: (r) => shortDate(r.filedAt).replace(/, \d{4}$/, ''),
      },
      amountColumnFor,
    ]
  return [
    nameCol,
    dateColumn(COPY.colDate),
    amountColumnFor,
    sharesColumn,
    {
      key: 'price',
      label: COPY.colAvgPrice,
      sortValue: (r) => r.avgPrice,
      className: () => 'muted',
      wide: true,
      cell: (r) => (r.avgPrice == null ? '—' : `$${r.avgPrice.toFixed(2)}`),
    },
  ]
}

function Table({
  set,
  rows,
  member,
  label,
  picked,
  onPick,
  held,
  exchanges,
}: {
  set: FilterSet
  rows: ActivityRow[]
  member: boolean
  label: string
  picked: string | undefined
  onPick: (r: ActivityRow) => void
  held: Set<string>
  exchanges?: Record<string, string>
}) {
  const [filter, setFilter] = useState<ActivityFilter>('all')
  const options = filtersFor(set)
  const shown = rows.filter((r) => matches(set, r, filter))
  // A filter that shortens the table must not shorten the page: the browser would pull the
  // scroll up under the reader. The table keeps the height it has with every row shown.
  const box = useRef<HTMLDivElement>(null)
  const [fullHeight, setFullHeight] = useState<number | undefined>(undefined)
  useLayoutEffect(() => {
    if (filter === 'all' && box.current) setFullHeight(box.current.offsetHeight)
  }, [filter, rows.length])
  return (
    <div className="fol-sec">
      {/* Investments' filter pills, each with its count (they scroll on a phone). The period is
          with the block's title above the chart. */}
      {options.length > 0 && (
        <div className="fol-toolbar">
          <FilterChips
            label={COPY.filterLabel}
            value={filter}
            onChange={(v) => setFilter(v as ActivityFilter)}
            options={options.map((o) => ({
              value: o.value,
              label: o.label,
              count: rows.filter((r) => matches(set, r, o.value as ActivityFilter)).length,
            }))}
          />
        </div>
      )}
      {rows.length === 0 && <p className="fol-empty">{COPY.noMovesInPeriod}</p>}
      <div ref={box} style={filter === 'all' ? undefined : { minHeight: fullHeight }}>
        <SortTable
          rows={shown}
          columns={columns(
            set,
            member,
            Math.max(0, ...rows.map((r) => r.magnitude)),
            held,
            exchanges,
          )}
          rowKey={(r) => r.key}
          label={label}
          onRowClick={onPick}
          selectedKey={picked}
        />
      </div>
    </div>
  )
}

/** Where a pinned block's top sits: just under the app's top bar, measured from the block's own
 *  scroll area (the page on a desktop, the content pane on a phone), since the bar's height
 *  differs by platform (52px, 48px in the Mac app). */
function useStickTop(ref: React.RefObject<HTMLElement | null>): number {
  const [top, setTop] = useState(0)
  useLayoutEffect(() => {
    const measure = () => {
      const el = ref.current
      const bar = document.querySelector<HTMLElement>('.topbar')
      if (!el || !bar) return setTop(0)
      let scroller: HTMLElement | null = el.parentElement
      while (scroller && !/(auto|scroll)/.test(getComputedStyle(scroller).overflowY))
        scroller = scroller.parentElement
      const from = scroller ? scroller.getBoundingClientRect().top : 0
      setTop(Math.max(0, Math.round(bar.getBoundingClientRect().bottom - from)))
    }
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [ref])
  return top
}

/** What they did: one move in focus with its chart (every move when none is picked), then
 *  the chosen period's moves as one table — a quarter's changes, or the dated trades since. */
export function ActivityTab({ follow, perf, names, held, onAskLucius }: Props) {
  const member = follow.kind === 'house_member'
  const spans = useMemo(() => rangePeriods(follow), [follow])
  const { openAsset } = useAssetNav()
  const [periodKey, setPeriodKey] = useState<string | null>(null)
  // All by default; the ranges (1 week … 3 years) narrow it.
  const period =
    spans.find((p) => p.key === periodKey) ?? spans.find((p) => p.key === 'ALL') ?? spans[0]
  const rows = period ? periodRows(follow, names, period) : []
  const [pickedKey, setPickedKey] = useState<string | null>(null)
  // Nothing is picked until the reader picks: the chart then shows every move. Picking the
  // same row again goes back to that overview.
  const picked = rows.find((r) => r.key === pickedKey)
  const benchmark = perf?.benchmark ?? 'SPY'
  const panelRef = useRef<HTMLDivElement>(null)
  // Picking a move shows it in the card above the table: on a phone that card is usually off
  // screen by then, so it is brought into view.
  // The card stays pinned under the app's top bar while the table scrolls beneath it, so
  // picking a move shows its chart in place: the page never jumps to it.
  const stickTop = useStickTop(panelRef)
  const pick = (r: ActivityRow) => setPickedKey(pickedKey !== r.key ? r.key : null)
  // The overview runs over the whole history and zooms to the chosen period; its headline is
  // the return over exactly the span on screen.
  const year = perf?.periods.ALL?.status === 'ok' ? perf.periods.ALL : perf?.periods['12M']
  const span = period && period.key !== 'ALL' ? { from: period.from, to: period.to } : undefined
  const shown = year?.status === 'ok' ? spanReturn(year.series, span) : null
  return (
    <div className="fol-sec fol-act">
      {/* Latest moves is off for now (owner, 2026-11-12): flip SHOW_LATEST to bring it back. */}
      {SHOW_LATEST && follow.kind === 'fund_13f' && (
        <LatestMoves
          follow={follow}
          names={names}
          held={held}
          onOpen={(_, rowKey) => {
            // All holds every move under the same key: show it there, picked.
            setPeriodKey('ALL')
            setPickedKey(rowKey)
          }}
        />
      )}
      {/* All moves: its title, period, return and chart are one raised card. */}
      <div className="fol-panel fol-act-card" ref={panelRef} style={{ top: stickTop }}>
        {/* The chart follows the table's period, so the two are one titled block. */}
        <div className="fol-sec-head">
          {/* The block's title is what it shows: every move, or the picked stock (its page one
              click away) with the way back to every move before it. */}
          {picked ? (
            <h2 className="fol-sec-title fol-spot-titleline">
              <Tooltip label={COPY.backToAllMoves}>
                <button
                  type="button"
                  className="fol-sec-back"
                  aria-label={COPY.backToAllMoves}
                  onClick={() => setPickedKey(null)}
                >
                  <ChevronLeft size={16} aria-hidden="true" />
                </button>
              </Tooltip>
              <Tooltip label={COPY.openStock(picked.symbol)}>
                <button
                  type="button"
                  className="fol-spot-name"
                  aria-label={COPY.openStock(picked.symbol)}
                  onClick={() => openAsset({ ticker: picked.symbol, label: picked.name })}
                >
                  <span className="mk-hsym">{picked.symbol}</span>
                  <span className="mk-hnm">{picked.name}</span>
                  <ArrowUpRight size={14} aria-hidden="true" />
                </button>
              </Tooltip>
            </h2>
          ) : (
            <h2 className="fol-sec-title">{COPY.allMovesSection}</h2>
          )}
          {/* The period sets the chart AND the table, so it sits with the block's title: the stock
              page's own timeframe chips with room, its compact dropdown without (as it does). */}
          {period && spans.length > 1 && (
            <div className="fol-tf">
              <div className="asd-tf-tabs">
                <SegmentedControl
                  ariaLabel={COPY.periodLabel}
                  options={spans.map((p) => ({ value: p.key, label: p.label }))}
                  value={period.key}
                  onChange={(v) => {
                    setPeriodKey(v)
                    setPickedKey(null)
                  }}
                />
              </div>
              <ToolSelect
                className="fol-tf-menu"
                label={COPY.periodLabel}
                value={period.key}
                options={spans.map((p) => ({ value: p.key, label: p.label }))}
                onChange={(v) => {
                  setPeriodKey(v)
                  setPickedKey(null)
                }}
              />
            </div>
          )}
        </div>
        {picked ? (
          <Spotlight
            row={picked}
            follow={follow}
            benchmark={benchmark}
            onAskLucius={onAskLucius}
            from={span?.from ?? historyStart(follow)}
            rangeWords={COPY.rangeWords(period?.key ?? 'ALL')}
          />
        ) : (
          year?.status === 'ok' && (
            <div className="fol-spot">
              {/* The same line as a picked move's figures, so picking one never moves the table. */}
              <div className="fol-spot-figrow">
                {/* The holdings' return over the period, large: the chart draws only their line. */}
                <div className="fol-ret3">
                  <div className="fol-ret3-item">
                    <span className="fol-ret3-l">
                      <i className="fol-line fol-line--main" aria-hidden="true" />
                      {follow.name}
                    </span>
                    <span className={`fol-ret3-v ${toneOf(shown?.portfolio)}`}>
                      {signedPct(shown?.portfolio)}
                    </span>
                  </div>
                </div>
              </div>
              <PortfolioActivityChart follow={follow} win={year} range={span} />
            </div>
          )
        )}
      </div>
      {period && (
        <Table
          key={period.key}
          set={member ? 'trades' : 'all'}
          rows={rows}
          member={member}
          label={period.label}
          picked={picked?.key}
          onPick={pick}
          held={held}
          exchanges={perf?.exchanges}
        />
      )}
    </div>
  )
}
