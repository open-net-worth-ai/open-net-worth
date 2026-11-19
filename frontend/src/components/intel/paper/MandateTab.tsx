import { Button, EmptyState } from '../../ui'
import type { MandateStatus } from '../../../api/paperTrading'

/** A paper portfolio's mandate against the book as it stands: its goal, then each limit, kept
 *  or not. Prices move, so a limit no trade broke can still be broken now. */
export function MandateTab({
  status,
  onEdit,
}: {
  status: MandateStatus | null
  onEdit?: () => void
}) {
  if (!status)
    return (
      <EmptyState
        title="No mandate yet"
        body="Write down what this portfolio is for and the limits every buy keeps: the most in one holding, the least cash, a stop loss. Buys that break them ask first, and Lucius trades within them."
        action={
          onEdit && (
            <Button size="sm" variant="primary" onClick={onEdit}>
              Set a mandate
            </Button>
          )
        }
      />
    )
  return (
    <div className="fol-sec paper-mandate">
      {status.goal && <p className="paper-mandate-goal">{status.goal}</p>}
      {status.rules.length > 0 && (
        <table className="lots-table" aria-label="Mandate limits">
          <thead>
            <tr>
              <th>Limit</th>
              <th>Set to</th>
              <th>Now</th>
            </tr>
          </thead>
          <tbody>
            {status.rules.map((r) => (
              <tr key={r.rule}>
                <td>{r.label}</td>
                <td>{r.limit}</td>
                <td className={r.ok ? 'paper-mandate-ok' : 'paper-mandate-broken'}>
                  {r.ok ? 'Kept' : r.detail}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {onEdit && (
        <div>
          <Button size="sm" variant="secondary" onClick={onEdit}>
            Edit mandate
          </Button>
        </div>
      )}
    </div>
  )
}
