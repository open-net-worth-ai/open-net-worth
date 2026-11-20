import { useFlag } from '../../hooks/useFlag'
import type { TradingAgentsInput } from '../../types/assetDetail'
import { TA_SIGNAL_LABEL, TA_STALE_AFTER_DAYS } from '../../types/tradingAgentsConstants'
import { runAgeText } from '../../utils/tradingAgents'
import { TradingAgentsRating } from './TradingAgentsRating'

/** Under Lucius's take: the take was written with a TradingAgents run. Names the rating and the
 *  run's age (older than the staleness window says so), and opens that run on the Analysis tab.
 *  Information only: never an order. Hidden while the `trading_agents` flag is off. */
export function TradingAgentsInputChip({
  input,
  onOpen,
}: {
  input: TradingAgentsInput
  onOpen: (runId: string) => void
}) {
  const enabled = useFlag('trading_agents')
  if (enabled !== true) return null
  const age = runAgeText(input.age_days)
  const outdated = input.age_days > TA_STALE_AFTER_DAYS
  const label = TA_SIGNAL_LABEL[input.signal]
  return (
    <button
      type="button"
      className="ta-chip"
      aria-label={`Includes TradingAgents analysis: ${label}, ${age}${outdated ? ', may be outdated' : ''}. Open the full analysis.`}
      onClick={() => onOpen(input.run_id)}
    >
      <span>Includes TradingAgents</span>
      <TradingAgentsRating signal={input.signal} compact />
      <span>{age}</span>
      {outdated && <span className="ta-chip__stale">may be outdated</span>}
    </button>
  )
}
