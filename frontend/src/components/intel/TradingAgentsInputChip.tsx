import type { TradingAgentsInput } from '../../types/assetDetail'
import { TA_SIGNAL_LABEL, TA_STALE_AFTER_DAYS } from '../../types/tradingAgentsConstants'
import { runAgeText } from '../../utils/tradingAgents'

/** Under the take's verdict: the research the take was written with, as supporting evidence. Plain
 *  muted text, never a rating pill: the take's own call stays the one verdict. `view` is the
 *  server's reading of the signal for this position; absent, only the signal and age show. Opens
 *  that run on the Analysis tab. Information only: never an order. */
export function TradingAgentsInputChip({
  input,
  onOpen,
}: {
  input: TradingAgentsInput
  onOpen: (runId: string) => void
}) {
  const age = runAgeText(input.age_days)
  const outdated = input.age_days > TA_STALE_AFTER_DAYS
  const label = TA_SIGNAL_LABEL[input.signal]
  const spoken = `Research view: ${label}, ${age}${input.view ? `. ${input.view}` : ''}${outdated ? '. May be outdated' : ''}. Open the full analysis.`
  return (
    <button
      type="button"
      className="ta-chip"
      aria-label={spoken}
      onClick={() => onOpen(input.run_id)}
    >
      <span>
        Research view: {label} · {age}
      </span>
      {input.view && <span> — {input.view}</span>}
      {outdated && (
        <>
          {' '}
          <span className="ta-chip__stale">may be outdated</span>
        </>
      )}
    </button>
  )
}
