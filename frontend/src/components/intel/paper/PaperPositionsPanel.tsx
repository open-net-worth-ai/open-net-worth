import { Panel, Tag } from '../../ui'
import { useFlag } from '../../../hooks/useFlag'
import { fCurrency } from '../../../utils/format'
import { signedPct, toneOf } from '../following/following.format'
import { usePaperPositions } from './usePaper'

/** The asset page's paper positions in this instrument, under the real "Held at" table and
 *  apart from it: pretend money, so never added into the position's real figures. Nothing
 *  renders while `paper_trading` is off or nothing is held on paper. */
export function PaperPositionsPanel({ instrumentId }: { instrumentId: string | undefined }) {
  const on = useFlag('paper_trading') === true
  const rows = usePaperPositions(instrumentId, on).data ?? []
  if (!on || rows.length === 0) return null
  return (
    <Panel
      className="asd-pos-accounts"
      title={
        <span className="paper-title">
          On paper <Tag>Paper</Tag>
        </span>
      }
    >
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
    </Panel>
  )
}
