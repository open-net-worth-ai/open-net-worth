import type {
  PositionVerdict,
  PriceAlertCondition,
  VerdictLevel,
  VerdictOrder,
} from '../../types/assetDetail'
import { useChatComposerOptional } from '../../state/chatComposer'
import { fPrice, getCurrencySymbol } from '../../utils/format'

// The take's verdict, under its reasoning: the one call and why, then each thing the user could
// act on as a suggestion that drops a ready-to-send request into Lucius's composer (the dock on
// desktop, the Lucius tab on a phone) with the stock attached as a mention — the mention binds the
// chat to the stock and carries the take and its call, so the request can stay short. The levels
// appear only there, as one plain alert.

const ACTION_LABEL: Record<PositionVerdict['action'], string> = {
  hold: 'Hold',
  add: 'Add',
  trim: 'Trim',
  sell: 'Sell',
  buy: 'Buy',
  wait: 'Wait',
}

const PLAN: Partial<Record<PositionVerdict['action'], [string, string]>> = {
  sell: ['Plan the sale', 'the sale'],
  trim: ['Plan the trim', 'the trim'],
  add: ['Plan adding', 'adding to the position'],
  buy: ['Plan the buy', 'the buy'],
}

interface Suggestion {
  label: string
  prompt: string
}

const units = (q: number) => q.toLocaleString(undefined, { maximumFractionDigits: 8 })

/** "Sell 25 at $270" — the order as one short phrase; without a limit, "Sell 25". */
function orderText(o: VerdictOrder, money: (v: number) => string): string {
  const verb = o.side === 'sell' ? 'Sell' : 'Buy'
  return `${verb} ${units(o.quantity)}${o.limit ? ` at ${money(o.limit)}` : ''}`
}

/** Whether a price alert already watches this level: same direction, within 0.5% of it. */
function alreadyAlerted(level: VerdictLevel, alerts: PriceAlertCondition[]): boolean {
  const falling = level.kind !== 'trim'
  return alerts.some(
    (a) =>
      (a.kind ?? 'price') === 'price' &&
      (falling ? a.op === '<' || a.op === '<=' : a.op === '>' || a.op === '>=') &&
      Math.abs(a.value / level.price - 1) <= 0.005,
  )
}

/** At most two suggestions: the order (or a plan when there is none), then one alert on the
 *  levels — below the stop / buy price, above the trim level — leaving out any level a price
 *  alert already watches. Each is worded as a reply to
 *  Lucius's own call; the stock rides along as a mention, which carries the take itself. */
function suggestionsFor(
  verdict: PositionVerdict,
  money: (v: number) => string,
  alerts: PriceAlertCondition[],
): Suggestion[] {
  const out: Suggestion[] = []
  const plan = PLAN[verdict.action]
  const order = verdict.order
  if (order) {
    const how = order.limit ? `at a ${money(order.limit)} limit` : 'at market'
    out.push({
      label: orderText(order, money),
      prompt: `${order.side === 'sell' ? 'Sell' : 'Buy'} ${units(order.quantity)} ${how}, as you suggested.`,
    })
  } else if (plan) {
    out.push({
      label: plan[0],
      prompt: `Walk me through ${plan[1]} as you suggested: how much, the tax and fees, and what it changes in my portfolio.`,
    })
  }
  const open = verdict.levels.filter((l) => !alreadyAlerted(l, alerts))
  const below = open.find((l) => l.kind !== 'trim')
  const above = open.find((l) => l.kind === 'trim')
  const when = [
    below && `falls to ${money(below.price)}`,
    above && `rises to ${money(above.price)}`,
  ].filter(Boolean)
  if (when.length) {
    const label = [below && `below ${money(below.price)}`, above && `above ${money(above.price)}`]
      .filter(Boolean)
      .join(' or ')
    out.push({
      label: `Alert me ${label}`,
      prompt: `Set price alerts: tell me if it ${when.join(' or ')}.`,
    })
  }
  return out
}

export function PositionVerdictLine({
  verdict,
  priceMaxFrac,
  instrumentId,
  name,
  ticker,
  alerts = [],
}: {
  verdict: PositionVerdict
  priceMaxFrac?: number
  instrumentId: string
  name: string
  ticker?: string | null
  alerts?: PriceAlertCondition[]
}) {
  const composer = useChatComposerOptional()
  const sym = verdict.currency ? getCurrencySymbol(verdict.currency) : ''
  const money = (v: number) => `${sym}${fPrice(v, priceMaxFrac)}`
  const suggestions = composer ? suggestionsFor(verdict, money, alerts) : []
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
