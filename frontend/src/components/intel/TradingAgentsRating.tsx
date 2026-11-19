import { TA_SIGNAL_LABEL, TA_SIGNAL_ORDER } from '../../types/tradingAgentsConstants'
import type { TaSignal } from '../../types/tradingAgents'

/** The rating pill: the word always printed, beside a five-cell scale showing where it sits
 *  (REVIEW, "Unclear", is off the scale and lights no cell). `compact` drops the scale. */
export function TradingAgentsRating({
  signal,
  compact = false,
}: {
  signal: TaSignal
  compact?: boolean
}) {
  const step = TA_SIGNAL_ORDER.indexOf(signal)
  const label = TA_SIGNAL_LABEL[signal]
  const tone = signal === 'REVIEW' ? 'unclear' : signal.toLowerCase()
  const aria =
    step < 0
      ? 'Rating: Unclear. The run could not settle on a rating.'
      : `Rating: ${label}, ${step + 1} of ${TA_SIGNAL_ORDER.length}`
  return (
    <span className="ta-rating-wrap">
      <span className={`ta-rating ta-rating--${tone}`} role="img" aria-label={aria}>
        <span className="ta-rating__label">{label}</span>
      </span>
      {!compact && (
        <span className={`ta-scale ta-scale--${tone}`} aria-hidden="true">
          {TA_SIGNAL_ORDER.map((s, i) => (
            <span key={s} className={`ta-scale__cell${i === step ? ' ta-scale__cell--on' : ''}`} />
          ))}
        </span>
      )}
    </span>
  )
}
