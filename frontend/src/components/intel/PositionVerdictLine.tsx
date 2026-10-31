import type { PositionVerdict, VerdictLevel } from '../../types/assetDetail'
import { fPrice, getCurrencySymbol } from '../../utils/format'

// The action line on "Lucius's take": its one call, why, and the levels that would change it.
// A level from the user's thesis says so; one Lucius suggested says that instead.

const ACTION_LABEL: Record<PositionVerdict['action'], string> = {
  hold: 'Hold',
  add: 'Add',
  trim: 'Trim',
  sell: 'Sell',
  buy: 'Buy',
  wait: 'Wait',
}

const LEVEL_LABEL: Record<VerdictLevel['kind'], string> = {
  stop: 'Stop',
  trim: 'Trim',
  entry: 'Buy at or below',
}

function distanceText(pct: number | null): string | null {
  if (pct == null) return null
  if (Math.abs(pct) < 0.05) return 'at today’s price'
  return `${Math.abs(pct).toFixed(1)}% ${pct < 0 ? 'below' : 'above'}`
}

export function PositionVerdictLine({
  verdict,
  priceMaxFrac,
}: {
  verdict: PositionVerdict
  priceMaxFrac?: number
}) {
  const sym = verdict.currency ? getCurrencySymbol(verdict.currency) : ''
  return (
    <div className="asd-verdict">
      <div className="asd-verdict-call">
        <span className={`asd-verdict-action asd-verdict-action--${verdict.action}`}>
          {ACTION_LABEL[verdict.action]}
        </span>
        <p className="asd-verdict-why">{verdict.why}</p>
      </div>
      {verdict.levels.length > 0 && (
        <ul className="asd-verdict-levels">
          {verdict.levels.map((l) => {
            const dist = distanceText(l.distance_pct)
            return (
              <li key={l.kind}>
                <span className="asd-verdict-level-name">{LEVEL_LABEL[l.kind]}</span>{' '}
                <span className="asd-verdict-level-price">
                  {sym}
                  {fPrice(l.price, priceMaxFrac)}
                </span>
                {dist && <span className="asd-verdict-level-meta"> · {dist}</span>}
                <span className="asd-verdict-level-meta">
                  {' '}
                  · {l.source === 'thesis' ? 'your thesis' : 'Lucius suggests'}
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
