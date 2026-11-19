import { Panel, Tag } from '../../ui'
import { useFlag } from '../../../hooks/useFlag'
import { fCurrency, fDate } from '../../../utils/format'
import type { PaperPositionRow, PaperTrade } from '../../../api/paperTrading'
import { signedPct, toneOf } from '../following/following.format'
import { usePaperPositions } from './usePaper'

/** The asset page's paper side of this instrument, under the real "Held at" table and apart
 *  from it: pretend money, so never added into the position's real figures. Where it is held on
 *  paper now, then every paper trade in it — a position sold out keeps its history. Nothing
 *  renders while `paper_trading` is off or it was never traded on paper. */
export function PaperPositionsPanel({ instrumentId }: { instrumentId: string | undefined }) {
  const on = useFlag('paper_trading') === true
  const data = usePaperPositions(instrumentId, on).data
  const positions = data?.positions ?? []
  const trades = data?.trades ?? []
  if (!on || (positions.length === 0 && trades.length === 0)) return null
  return (
    <Panel
      className="asd-pos-accounts"
      title={
        <span className="paper-title">
          On paper <Tag>Paper</Tag>
        </span>
      }
    >
      {positions.length > 0 && <Positions rows={positions} />}
      {trades.length > 0 && <Trades rows={trades} />}
    </Panel>
  )
}

function Positions({ rows }: { rows: PaperPositionRow[] }) {
  return (
    <div className="paper-table-scroll">
      <table className="lots-table lots-table--accts">
        <thead>
          <tr>
            <th>Paper portfolio</th>
            <th>Quantity</th>
            <th>Value</th>
            <th>Return</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.portfolio_id}>
              <td>{r.portfolio_name}</td>
              <td>{r.quantity.toLocaleString(undefined, { maximumFractionDigits: 8 })}</td>
              <td>{r.value_home != null ? fCurrency(r.value_home, r.currency) : '—'}</td>
              <td className={toneOf(r.return_pct)}>{signedPct(r.return_pct)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Trades({ rows }: { rows: PaperTrade[] }) {
  return (
    <div className="paper-table-scroll paper-trades">
      <table className="lots-table" aria-label="Paper trades">
        <thead>
          <tr>
            <th>Date</th>
            <th>Paper portfolio</th>
            <th>Trade</th>
            <th>Why</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((t) => (
            <tr key={t.id}>
              <td>{fDate(t.at, 'short', 'auto')}</td>
              <td>{t.portfolio_name}</td>
              <td>{t.opening ? `${t.label} (at the start)` : t.label}</td>
              <td className="paper-trade-why">{t.note ?? ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
