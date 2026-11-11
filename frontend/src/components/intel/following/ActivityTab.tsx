import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Button, SegmentedControl } from '../../ui'
import { PickSelect } from './PickSelect'
import { NameCell } from './NameCell'
import { LatestMoves } from './LatestMoves'
import { AssetLink } from '../AssetLink'
import { ChevronRight } from '../../../ui/icons'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { FilterSheet } from '../../tabs/investments/FilterSheet'
import type { Follow, FollowPerformance } from '../../../api/followedPortfolios'
import { COPY } from './following.copy'
import { shortDate, signedPct, toneOf } from './following.format'
import { CopyTradeButton } from './CopyTradeButton'
import {
  exactTrades,
  periodRows,
  periods,
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
import { useNarrow } from './useNarrow'

// The filter (five options) and the period fit side by side from here.
const WIDE_TOOLBAR_PX = 640

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
}: {
  row: ActivityRow
  follow: Follow
  benchmark: string
  onAskLucius?: (t: string) => void
}) {
  const since = useSinceFiled(row.symbol, benchmark, row.filedAt)
  const trades = useMemo(() => exactTrades(follow, row.symbol), [follow, row.symbol])
  const [details, setDetails] = useState(false)
  return (
    <div className="fol-spot">
      <div className="fol-spot-head">
        <div className="fol-spot-title">
          {/* The stock's own page, as a ticker opens everywhere else in the app. */}
          <AssetLink ticker={row.symbol} label={row.name} className="fol-spot-open">
            <b>{row.symbol}</b>
            <ChevronRight size={14} aria-hidden="true" />
          </AssetLink>
          {since.price != null && <span className="fol-spot-price">${since.price.toFixed(2)}</span>}
          {since.stock == null ? (
            <span className="fol-spot-sub">{COPY.noPriceSince}</span>
          ) : (
            <>
              <span className={`fol-spot-move ${toneOf(since.stock)}`}>
                {signedPct(since.stock)}
              </span>
              <span className="fol-spot-sub">
                {COPY.sinceFiled(row.source, shortDate(row.filedAt))}
              </span>
              <span className="fol-spot-sub">
                {benchmark} {signedPct(since.bench)}
              </span>
            </>
          )}
        </div>
        <div className="fol-spot-actions">
          <Button size="sm" onClick={() => setDetails(true)}>
            {COPY.tradeDetails}
            <ChevronRight size={14} aria-hidden="true" />
          </Button>
          <CopyTradeButton
            who={follow.name}
            what={`${row.verb.toLowerCase()} ${row.symbol} (${row.name}), ${row.amountText}, ${row.detail}, made public ${shortDate(row.filedAt)}${since.stock != null ? `, ${signedPct(since.stock)} since then` : ''}`}
            onAskLucius={onAskLucius}
          />
        </div>
      </div>
      <TradeChart row={row} trades={trades} from={historyStart(follow)} />
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

const nameColumn = (held: Set<string>): Column<ActivityRow> => ({
  key: 'name',
  label: COPY.colStock,
  left: true,
  sortValue: (r) => r.name,
  cell: (r) => <NameCell symbol={r.symbol} name={r.name} held={held.has(r.symbol)} />,
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
  sortValue: (r) => r.shares,
  className: (r) => (r.up ? 'up' : 'down'),
  cell: (r) => r.sharesText,
}
const dateColumn = (label: string): Column<ActivityRow> => ({
  key: 'date',
  label,
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
): Column<ActivityRow>[] {
  const amountColumnFor = amountColumn(max)
  const nameCol = nameColumn(held)
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
  period,
}: {
  set: FilterSet
  rows: ActivityRow[]
  member: boolean
  label: string
  picked: string | undefined
  onPick: (r: ActivityRow) => void
  held: Set<string>
  period: ReactNode
}) {
  const [filter, setFilter] = useState<ActivityFilter>('all')
  const isMobile = useIsMobile()
  // [period (fills) | Filter sheet] whenever the row is too narrow for the filter and the
  // period side by side (a tablet, a pane beside Lucius), as on a phone.
  const [barRef, narrow] = useNarrow<HTMLDivElement>(WIDE_TOOLBAR_PX)
  const compact = isMobile || narrow
  const options = filtersFor(set, rows)
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
      {/* One row at every width, kit controls at one height. Wide: the filter LEFT, the
          period RIGHT. Phone (Investments' pattern): [period (fills) | Filter sheet]. */}
      <div ref={barRef} className="fol-toolbar">
        {options.length > 2 && !compact && (
          <SegmentedControl
            options={options.map((o) => ({ value: o.value as ActivityFilter, label: o.label }))}
            value={filter}
            onChange={setFilter}
            ariaLabel={COPY.filterLabel}
          />
        )}
        {period}
        {options.length > 2 && compact && (
          <FilterSheet
            presentation={isMobile ? 'sheet' : 'popover'}
            title={COPY.filterTitle}
            active={filter !== 'all'}
            groups={[
              {
                title: COPY.filterLabel,
                options: options.map((o) => ({ id: o.value, label: o.label })),
                value: filter,
                onChange: (id) => setFilter(id as ActivityFilter),
              },
            ]}
          />
        )}
      </div>
      {rows.length === 0 && <p className="fol-empty">{COPY.noMovesInPeriod}</p>}
      <div ref={box} style={filter === 'all' ? undefined : { minHeight: fullHeight }}>
        <SortTable
          rows={shown}
          columns={columns(set, member, Math.max(0, ...rows.map((r) => r.magnitude)), held)}
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
export function ActivityTab({ follow, perf, names, held, onAskLucius }: Props) {
  const member = follow.kind === 'house_member'
  const spans = useMemo(() => periods(follow), [follow])
  const [periodKey, setPeriodKey] = useState<string | null>(null)
  const period = spans.find((p) => p.key === periodKey) ?? spans[0]
  const rows = period ? periodRows(follow, names, period) : []
  const [pickedKey, setPickedKey] = useState<string | null>(null)
  // Nothing is picked until the reader picks: the chart then shows every move. Picking the
  // same row again goes back to that overview.
  const picked = rows.find((r) => r.key === pickedKey)
  const benchmark = perf?.benchmark ?? 'SPY'
  const pick = (r: ActivityRow) => setPickedKey((k) => (k === r.key ? null : r.key))
  // The overview runs over the whole history and zooms to the chosen period; its headline is
  // the return over exactly the span on screen.
  const year = perf?.periods.ALL?.status === 'ok' ? perf.periods.ALL : perf?.periods['12M']
  const span = period && period.key !== 'all' ? { from: period.from, to: period.to } : undefined
  const shown = year?.status === 'ok' ? spanReturn(year.series, span) : null
  return (
    <div className="fol-sec">
      {follow.kind === 'fund_13f' && (
        <LatestMoves
          follow={follow}
          names={names}
          held={held}
          onOpen={(periodKey, rowKey) => {
            setPeriodKey(periodKey)
            setPickedKey(rowKey)
          }}
        />
      )}
      {/* The chart follows the table's period, so the two are one titled block. */}
      <h2 className={`fol-sec-title${follow.kind === 'fund_13f' ? ' is-next' : ''}`}>
        {COPY.allMovesSection}
      </h2>
      {picked ? (
        <Spotlight row={picked} follow={follow} benchmark={benchmark} onAskLucius={onAskLucius} />
      ) : (
        year?.status === 'ok' && (
          <div className="fol-spot">
            <div className="fol-spot-head">
              <div className="fol-spot-title">
                <b>{COPY.allMovesTitle}</b>
                <span className={`fol-spot-move ${toneOf(shown?.portfolio)}`}>
                  {signedPct(shown?.portfolio)}
                </span>
                <span className="fol-spot-sub">
                  {benchmark} {signedPct(shown?.benchmark)}
                </span>
              </div>
            </div>
            <PortfolioActivityChart follow={follow} win={year} range={span} />
          </div>
        )
      )}
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
          period={
            spans.length > 1 ? (
              <PickSelect
                className="fol-period"
                aria-label={COPY.periodLabel}
                value={period.key}
                options={spans.map((p) => ({ value: p.key, label: p.label }))}
                onChange={(e) => {
                  setPeriodKey(e.target.value)
                  setPickedKey(null)
                }}
              />
            ) : null
          }
        />
      )}
    </div>
  )
}
