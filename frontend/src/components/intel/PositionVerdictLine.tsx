import type { PositionVerdict, VerdictAction } from '../../types/assetDetail'
import { useChatComposerOptional } from '../../state/chatComposer'
import { fPrice, getCurrencySymbol } from '../../utils/format'

// The take's verdict, under its reasoning: the one call and why, then each thing the user could
// act on as a suggestion that drops a ready-to-send request into Lucius's composer (the dock on
// desktop, the Lucius tab on a phone) with the stock attached as a mention — the mention binds the
// chat to the stock and carries the take and its call, so the request can stay short. Every
// button is an action Lucius itself proposed, with its own prices; usually there are none.

const ACTION_LABEL: Record<PositionVerdict['action'], string> = {
  hold: 'Hold',
  add: 'Add',
  trim: 'Trim',
  sell: 'Sell',
  buy: 'Buy',
  wait: 'Wait',
}

interface Suggestion {
  label: string
  prompt: string
}

const units = (q: number) => q.toLocaleString(undefined, { maximumFractionDigits: 8 })

/** One button per action Lucius proposed — its label, and the request it puts in the chat. */
function suggestionFor(a: VerdictAction, money: (v: number) => string): Suggestion {
  if (a.type === 'order') {
    const verb = a.side === 'sell' ? 'Sell' : 'Buy'
    const at = a.limit ? ` at ${money(a.limit)}` : ' at market'
    const how = a.limit ? `at a ${money(a.limit)} limit` : 'at market'
    return {
      label: `${verb} ${units(a.quantity)}${at}`,
      prompt: `${verb} ${units(a.quantity)} ${how}, as you suggested.`,
    }
  }
  const way = a.when === 'below' ? 'falls to' : 'rises to'
  return {
    label: `Alert me ${a.when} ${money(a.price)}`,
    prompt: `Set a price alert: tell me if it ${way} ${money(a.price)}.`,
  }
}

export function PositionVerdictLine({
  verdict,
  priceMaxFrac,
  instrumentId,
  name,
  ticker,
}: {
  verdict: PositionVerdict
  priceMaxFrac?: number
  instrumentId: string
  name: string
  ticker?: string | null
}) {
  const composer = useChatComposerOptional()
  const sym = verdict.currency ? getCurrencySymbol(verdict.currency) : ''
  const money = (v: number) => `${sym}${fPrice(v, priceMaxFrac)}`
  const suggestions = composer ? verdict.actions.map((a) => suggestionFor(a, money)) : []
  const send = (prompt: string) => {
    composer?.addMention(
      { type: 'instrument', ref: instrumentId, label: ticker || name },
      { open: false },
    )
    composer?.setPendingText(prompt, { keepChips: true })
    window.dispatchEvent(new Event('lx-dock-open'))
  }
  return (
    <div className="asd-verdict">
      <p className="asd-verdict-why">
        <span className={`asd-verdict-action asd-verdict-action--${verdict.action}`}>
          {ACTION_LABEL[verdict.action]}
        </span>
        {verdict.why}
      </p>
      {suggestions.length > 0 && (
        <div className="asd-verdict-suggestions">
          {suggestions.map((c) => (
            <button
              key={c.label}
              type="button"
              className="asd-verdict-chip"
              onClick={() => send(c.prompt)}
            >
              {c.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
