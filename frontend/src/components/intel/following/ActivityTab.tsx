import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useStickTop } from './useStickTop'
import { SegmentedControl } from '../../ui'
import { useAssetNav } from '../../../state/assetNav'
import { FilterChips, ToolSelect } from './Controls'
import { NameCell } from './NameCell'
import { LatestMoves } from './LatestMoves'

const SHOW_LATEST = false
import { ArrowUpRight, ChevronRight } from '../../../ui/icons'
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
import { usePriceHistory, useSinceFiled } from './useFollowing'
import { spanReturn } from './spanReturn'

interface Props {
  follow: Follow
  perf: FollowPerformance | undefined
  names: Names
  /** The tickers in your own book, upper-cased. */
  held: Set<string>
  onAskLucius?: (text: string) => void
  /** The page's one time range, shared with Comparison (owner, 2026-11-13). */
  range: string
  onRange: (key: string) => void
}

function Spotlight({
  row,
  follow,
  benchmark,
  from,
}: {
  row: ActivityRow
  follow: Follow
  benchmark: string
  /** The chosen period's first day: the stock's move is measured from it, as the chart is. */
  from: string
}) {
  const since = useSinceFiled(row.symbol, benchmark, from)
  const trades = useMemo(() => exactTrades(follow, row.symbol), [follow, row.symbol])
  return (
    <div className="fol-spot">
      {/* One headline figure (owner, 2026-11-13: three figures and two buttons were too busy):
          the stock's move large, the benchmark and its price small beside it. The stock is the
          block's title; Copy trade sits on that title's line. */}
      <div className="fol-spot-figrow">
        {since.stock == null ? (
          <p className="fol-spot-sub">{COPY.noPriceSince}</p>
        ) : (
          // One line, as All Moves' own figure is one line, so picking a move never shifts the
          // chart or the table below it: the move large, its context small beside it.
          <div className="fol-spot-headline">
            <span className={`fol-ret3-v ${toneOf(since.stock)}`}>{signedPct(since.stock)}</span>
            <span className="fol-spot-context">
              {/* No benchmark beside it (owner, 2026-11-14): the stock's own move is the point. */}
              {row.symbol}
              {/* The latest close, not what they paid: the move runs from the range's start;
                  their estimated price is in Trade details. */}
              {since.price != null && ` · ${COPY.priceNow(`$${since.price.toFixed(2)}`)}`}
            </span>
          </div>
        )}
      </div>
      <TradeChart row={row} trades={trades} from={from} />
    </div>
  )
}

/** Copy trade for the picked move, on its title's line: the request names the move and its
 *  return over the range, read from the same prices the headline shows. */
function PickedCopy({
  row,
  follow,
  benchmark,
  from,
  rangeWords,
  onAskLucius,
}: {
  row: ActivityRow
  follow: Follow
  benchmark: string
  from: string
  rangeWords: string
  onAskLucius?: (t: string) => void
}) {
  const since = useSinceFiled(row.symbol, benchmark, from)
  const moved = since.stock != null ? `, ${signedPct(since.stock)} ${rangeWords}` : ''
  const [details, setDetails] = useState(false)
  const copy = (
    <CopyTradeButton
      who={follow.name}
      what={`${row.verb.toLowerCase()} ${row.symbol} (${row.name}), ${row.amountText}, ${row.detail}, made public ${shortDate(row.filedAt)}${moved}`}
      onAskLucius={onAskLucius}
    />
  )
  // Trade details beside Copy trade, on the title's line (owner, 2026-11-14): a line of its own
  // under the chart made a picked move's card taller than All moves', so picking one moved
  // everything below it.
  return (
    <div className="fol-picked-actions">
      <button type="button" className="fol-spot-link" onClick={() => setDetails(true)}>
        {COPY.tradeDetails}
        <ChevronRight size={14} aria-hidden="true" />
      </button>
      {/* On a phone Copy trade lives in Trade details, so the title's line keeps room for the
          stock's name (owner, 2026-11-14); a wide screen has it here too. */}
      <span className="fol-copy-inline">{copy}</span>
      {details && (
        <TradeDetails
          row={row}
          follow={follow}
          benchmark={benchmark}
          onClose={() => setDetails(false)}
          action={copy}
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
  tools,
}: {
  set: FilterSet
  rows: ActivityRow[]
  member: boolean
  label: string
  picked: string | undefined
  onPick: (r: ActivityRow) => void
  held: Set<string>
  exchanges?: Record<string, string>
  /** At the toolbar's right end, on the chips' line: the page's range (owner, 2026-11-13). */
  tools?: ReactNode
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
      {(options.length > 0 || tools) && (
        <div className="fol-toolbar fol-toolbar--range">
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
          {tools}
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

/** What they did: one move in focus with its chart (every move when none is picked), then
 *  the chosen period's moves as one table — a quarter's changes, or the dated trades since. */
/** Renders nothing; tells its parent once this stock's prices are in, so the card can switch
 *  to it whole instead of showing an empty chart while they load. */
function WhenPriced({ symbol, onReady }: { symbol: string; onReady: () => void }) {
  const hist = usePriceHistory(symbol)
  const ready = !hist.initialLoading
  useEffect(() => {
    if (ready) onReady()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per symbol, when it is priced
  }, [ready, symbol])
  return null
}

function scrollerOf(el: HTMLElement): HTMLElement {
  let node: HTMLElement | null = el.parentElement
  while (node && !/(auto|scroll)/.test(getComputedStyle(node).overflowY)) node = node.parentElement
  return node ?? (document.scrollingElement as HTMLElement)
}

/** Keeps a tapped row where the finger left it while the card above it changes height (a new
 *  stock's figures and chart): iPhone Safari does not re-anchor the scroll as Chromium does, so
 *  the row slid away under the reader (owner, 2026-11-14). */
function useKeepRowInPlace(card: React.RefObject<HTMLElement | null>) {
  const anchor = useRef<{ key: string; top: number; until: number } | null>(null)
  const find = (key: string) =>
    [...(card.current?.parentElement?.querySelectorAll<HTMLElement>('[data-row-key]') ?? [])].find(
      (el) => el.dataset.rowKey === key,
    ) ?? null
  const hold = () => {
    const a = anchor.current
    if (!a || performance.now() > a.until) return
    const row = find(a.key)
    if (!row) return
    const moved = row.getBoundingClientRect().top - a.top
    if (Math.abs(moved) >= 1) scrollerOf(row).scrollTop += moved
  }
  useLayoutEffect(hold)
  useEffect(() => {
    const el = card.current
    if (!el) return
    const observer = new ResizeObserver(hold)
    observer.observe(el)
    return () => observer.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- hold reads only refs and the DOM
  }, [])
  return (key: string) => {
    const row = find(key)
    anchor.current = row
      ? { key, top: row.getBoundingClientRect().top, until: performance.now() + 2000 }
      : null
  }
}

export function ActivityTab({ follow, perf, names, held, onAskLucius, range, onRange }: Props) {
  const member = follow.kind === 'house_member'
  const spans = useMemo(() => rangePeriods(follow), [follow])
  const { openAsset } = useAssetNav()
  // The page's range; one this portfolio has no moves for (a short history) reads as All.
  const period =
    spans.find((p) => p.key === range) ?? spans.find((p) => p.key === 'ALL') ?? spans[0]
  const rows = period ? periodRows(follow, names, period) : []
  const [pickedKey, setPickedKey] = useState<string | null>(null)
  // Nothing is picked until the reader picks: the chart then shows every move. Picking the
  // same row again goes back to that overview.
  // The card shows a picked move once its prices are in; until then it keeps what it showed.
  const [shownKey, setShownKey] = useState<string | null>(null)
  const pending = rows.find((r) => r.key === pickedKey)
  const picked = pickedKey == null ? undefined : (rows.find((r) => r.key === shownKey) ?? undefined)
  const benchmark = perf?.benchmark ?? 'SPY'
  const panelRef = useRef<HTMLDivElement>(null)
  // Picking a move shows it in the card above the table: on a phone that card is usually off
  // screen by then, so it is brought into view.
  // The card stays pinned under the app's top bar while the table scrolls beneath it, so
  // picking a move shows its chart in place: the page never jumps to it.
  const stickTop = useStickTop(panelRef)
  const keepInPlace = useKeepRowInPlace(panelRef)
  const pick = (r: ActivityRow) => {
    keepInPlace(r.key)
    const next = pickedKey !== r.key ? r.key : null
    setPickedKey(next)
    if (next == null) setShownKey(null)
  }
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
            onRange('ALL')
            setPickedKey(rowKey)
            setShownKey(rowKey)
          }}
        />
      )}
      {pending && pending.key !== shownKey && (
        <WhenPriced
          key={pending.key}
          symbol={pending.symbol}
          onReady={() => setShownKey(pending.key)}
        />
      )}
      {/* All moves: its title, period, return and chart are one raised card. */}
      {/* Pinned under the page's sticky tabs (their height is --fol-tabs-h) with the same gap
          it has before scrolling (the page's section gap), so sticking never moves it closer. */}
      <div
        className="fol-panel fol-act-card"
        ref={panelRef}
        style={{ top: `calc(${stickTop}px + var(--fol-tabs-h, 0px) + var(--space-6))` }}
      >
        {/* The chart follows the table's period, so the two are one titled block. */}
        <div className="fol-sec-head">
          {/* The block's title is what it shows: every move, or the picked stock (its page one
              click away). Clicking the picked row again goes back to every move. */}
          {picked ? (
            <h2 className="fol-sec-title fol-spot-titleline">
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
          {picked && (
            <PickedCopy
              row={picked}
              follow={follow}
              benchmark={benchmark}
              from={span?.from ?? historyStart(follow)}
              rangeWords={COPY.rangeWords(period?.key ?? 'ALL')}
              onAskLucius={onAskLucius}
            />
          )}
        </div>
        {picked ? (
          <Spotlight
            row={picked}
            follow={follow}
            benchmark={benchmark}
            from={span?.from ?? historyStart(follow)}
          />
        ) : (
          year?.status === 'ok' && (
            <div className="fol-spot">
              {/* The same line as a picked move's figures, so picking one never moves the table. */}
              <div className="fol-spot-figrow">
                {/* The holdings' return over the period, large: the chart draws only their line. */}
                {/* One line chart, so no label naming it (owner, 2026-11-13): just its return. */}
                <div className="fol-spot-headline">
                  <span className={`fol-ret3-v ${toneOf(shown?.portfolio)}`}>
                    {signedPct(shown?.portfolio)}
                  </span>
                </div>
              </div>
              <PortfolioActivityChart follow={follow} win={year} range={span} />
            </div>
          )
        )}
      </div>
      {period && (
        <Table
          tools={
            spans.length > 1 ? (
              <div className="fol-tf">
                <div className="asd-tf-tabs">
                  <SegmentedControl
                    ariaLabel={COPY.periodLabel}
                    options={spans.map((p) => ({ value: p.key, label: p.label }))}
                    value={period.key}
                    onChange={(v) => {
                      onRange(v)
                      setPickedKey(null)
                      setShownKey(null)
                      setShownKey(null)
                    }}
                  />
                </div>
                <ToolSelect
                  className="fol-tf-menu"
                  label={COPY.periodLabel}
                  value={period.key}
                  options={spans.map((p) => ({ value: p.key, label: p.label }))}
                  onChange={(v) => {
                    onRange(v)
                    setPickedKey(null)
                  }}
                />
              </div>
            ) : undefined
          }
          key={period.key}
          set={member ? 'trades' : 'all'}
          rows={rows}
          member={member}
          label={period.label}
          picked={pending?.key}
          onPick={pick}
          held={held}
          exchanges={perf?.exchanges}
        />
      )}
    </div>
  )
}
