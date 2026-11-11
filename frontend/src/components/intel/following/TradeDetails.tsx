import { useMemo } from 'react'
import { Modal } from '../../ui'
import type { Follow } from '../../../api/followedPortfolios'
import { COPY } from './following.copy'
import { shortDate, signedPct, toneOf } from './following.format'
import { exactTrades, stockHistory, type ActivityRow, type HistoryRow } from './activityRows'
import { SortTable, type Column } from './SortTable'
import { TradeChart } from './TradeChart'
import { historyStart } from './activityRows'
import { useSinceFiled } from './useFollowing'

interface Props {
  row: ActivityRow
  follow: Follow
  benchmark: string
  onClose: () => void
}

const columns: Column<HistoryRow>[] = [
  {
    key: 'when',
    label: COPY.colDate,
    left: true,
    sortValue: (r) => r.sortDate,
    cell: (r) => <span className="mk-hsym">{r.when}</span>,
  },
  {
    key: 'amount',
    label: COPY.colChange,
    primary: true,
    sortValue: (r) => r.amount,
    className: (r) => (r.up ? 'up' : 'down'),
    cell: (r) => r.amountText,
  },
  {
    key: 'shares',
    label: COPY.colShares,
    sortValue: (r) => r.shares,
    className: (r) => (r.up ? 'up' : 'down'),
    cell: (r) => r.sharesText,
  },
]

/** Everything this portfolio did in one stock: the price with every move marked, each move's
 *  size as a bar, and the list — each 13F quarter it changed, and each dated trade. */
export function TradeDetails({ row, follow, benchmark, onClose }: Props) {
  const since = useSinceFiled(row.symbol, benchmark, row.filedAt)
  const trades = useMemo(() => exactTrades(follow, row.symbol), [follow, row.symbol])
  const history = useMemo(() => stockHistory(follow, row.symbol), [follow, row.symbol])
  return (
    <Modal open onClose={onClose} title={COPY.tradeDetails} size="lg">
      <div className="fol-sec">
        <div className="fol-spot-title">
          <b>{row.symbol}</b>
          <span className="fol-spot-sub">{row.name}</span>
          {since.price != null && <span className="fol-spot-price">${since.price.toFixed(2)}</span>}
          {since.stock != null && (
            <>
              <span className={`fol-spot-move ${toneOf(since.stock)}`}>
                {signedPct(since.stock)}
              </span>
              <span className="fol-spot-sub">{COPY.sinceFiled('', shortDate(row.filedAt))}</span>
            </>
          )}
        </div>
        <div className="fol-spot-sub">{follow.name}</div>
        <TradeChart row={row} trades={trades} from={historyStart(follow)} />
        <SortTable
          rows={history}
          columns={columns}
          rowKey={(r) => r.key}
          label={COPY.tradeDetailsOf(row.symbol)}
        />
      </div>
    </Modal>
  )
}
