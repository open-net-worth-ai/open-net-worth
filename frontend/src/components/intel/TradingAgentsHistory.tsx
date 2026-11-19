import { TA_STATUS_LABEL } from '../../types/tradingAgentsConstants'
import type { TaRunSummary } from '../../types/tradingAgents'
import { fCurrency, fDate } from '../../utils/format'
import { Button, Text } from '../ui'
import { TradingAgentsRating } from './TradingAgentsRating'

function costText(run: TaRunSummary): string {
  if (run.cost_usd === 0 && run.plan_included_calls > 0) return 'plan-included'
  return fCurrency(run.cost_usd, 'USD')
}

/** The last runs for a ticker. Picking one makes it the featured run. */
export function TradingAgentsHistory({
  runs,
  total,
  currentId,
  onSelect,
  onShowEarlier,
}: {
  runs: TaRunSummary[]
  total: number
  currentId: string | null
  onSelect: (id: string) => void
  /** Offered while there are more runs than are listed, up to HISTORY_MAX. */
  onShowEarlier: (() => void) | null
}) {
  // One run is the featured one already: a list of it alone would only repeat it.
  if (runs.length < 2) return null
  return (
    <div>
      <div className="ta-history" role="list" aria-label="Earlier analyses">
        {runs.map((run) => (
          <div key={run.id} role="listitem">
            <button
              type="button"
              className={`ta-history__row${run.id === currentId ? ' ta-history__row--current' : ''}`}
              aria-current={run.id === currentId ? 'true' : undefined}
              onClick={() => onSelect(run.id)}
            >
              {run.signal ? (
                <TradingAgentsRating signal={run.signal} compact />
              ) : (
                <Text size="sm" tone="muted">
                  {TA_STATUS_LABEL[run.status]}
                </Text>
              )}
              <Text size="sm">{fDate(run.trade_date)}</Text>
              <Text size="xs" tone="faint">
                {costText(run)}
              </Text>
            </button>
          </div>
        ))}
      </div>
      {onShowEarlier && (
        <Button variant="ghost" size="sm" onClick={onShowEarlier}>
          Show earlier runs
        </Button>
      )}
      {!onShowEarlier && total > runs.length && (
        <Text size="xs" tone="faint">
          Older runs are in Settings → Cost &amp; Performance.
        </Text>
      )}
    </div>
  )
}
