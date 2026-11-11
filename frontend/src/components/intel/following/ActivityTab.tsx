import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Button, SegmentedControl } from '../../ui'
import { CalendarDays, ChevronDown, ChevronRight } from '../../../ui/icons'
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

interface Props {
  follow: Follow
  perf: FollowPerformance | undefined
  names: Names
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
          <b>{row.symbol}</b>
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

const nameColumn: Column<ActivityRow> = {
  key: 'name',
  label: COPY.colStock,
  left: true,
  sortValue: (r) => r.name,
  cell: (r) => (
    <span className="mk-hcell">
      <span className="mk-hsym">{r.symbol}</span>
      <span className="mk-hnm">{r.name}</span>
    </span>
  ),
}
const amountColumn: Column<ActivityRow> = {
  key: 'amount',
  label: COPY.colChange,
  primary: true,
  sortValue: (r) => r.amount ?? r.magnitude * (r.up ? 1 : -1),
  className: (r) => (r.up ? 'up' : 'down'),
  cell: (r) => r.amountText,
}
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
function columns(set: FilterSet, member: boolean): Column<ActivityRow>[] {
  if (set === 'changes' || set === 'all')
    return [nameColumn, dateColumn(COPY.colDate), amountColumn, sharesColumn]
  if (member)
    return [
      nameColumn,
      dateColumn(COPY.colTraded),
      {
        key: 'filed',
        label: COPY.colFiled,
        sortValue: (r) => r.filedAt,
        className: () => 'muted',
        wide: true,
        cell: (r) => shortDate(r.filedAt).replace(/, \d{4}$/, ''),
      },
      amountColumn,
    ]
  return [
    nameColumn,
    dateColumn(COPY.colDate),
    amountColumn,
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
  period,
}: {
  set: FilterSet
  rows: ActivityRow[]
  member: boolean
  label: string
  picked: string | undefined
  onPick: (r: ActivityRow) => void
  period: ReactNode
}) {
  const [filter, setFilter] = useState<ActivityFilter>('all')
  const isMobile = useIsMobile()
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
      {/* As Investments' Activity does: wide, the filter chips LEFT and the period RIGHT; on a
          phone the chips fold into the Filter sheet and the row is [period (fills) | Filter]. */}
      <div className="fol-toolbar">
        {options.length > 2 && !isMobile && (
          <SegmentedControl
            options={options.map((o) => ({ value: o.value as ActivityFilter, label: o.label }))}
            value={filter}
            onChange={setFilter}
            ariaLabel={COPY.filterLabel}
          />
        )}
        {period}
        {options.length > 2 && isMobile && (
          <FilterSheet
            presentation="sheet"
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
          columns={columns(set, member)}
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
export function ActivityTab({ follow, perf, names, onAskLucius }: Props) {
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
          period={
            spans.length > 1 ? (
              <span className="intel-sort fol-period">
                <CalendarDays size={13} className="intel-sort-icon" aria-hidden="true" />
                <select
                  className="intel-sort-select"
                  aria-label={COPY.periodLabel}
                  value={period.key}
                  onChange={(e) => {
                    setPeriodKey(e.target.value)
                    setPickedKey(null)
                  }}
                >
                  {spans.map((p) => (
                    <option key={p.key} value={p.key}>
                      {p.label}
                    </option>
                  ))}
                </select>
                <ChevronDown size={13} className="intel-sort-chev" aria-hidden="true" />
              </span>
            ) : null
          }
        />
      )}
    </div>
  )
}
