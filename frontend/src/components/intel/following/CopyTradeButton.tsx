import { Button } from '../../ui'
import { Spark } from '../../../lucius/identity'
import { sendNextPrefill } from '../../../state/luciusSendIntent'
import { COPY, copyTradePrompt } from './following.copy'

interface Props {
  /** Whose trade it is, e.g. "Acme Capital". */
  who: string
  /** The trade in words, e.g. "bought ACME (Acme Corp) on Jul 28, 2026, reported as $1M to $5M". */
  what: string
  onAskLucius?: (text: string) => void
}

/** Copy ONE trade now: hands it to Lucius, who sizes it and drafts the order at the broker.
 *  The message sends at once, in a fresh conversation — pressing the button is the request.
 *  The app's Lucius button (as "Copy with Lucius" above it), the same kit Button as its
 *  neighbour "Trade details", so the two share one shape and height. */
export function CopyTradeButton({ who, what, onAskLucius }: Props) {
  if (!onAskLucius) return null
  return (
    <Button
      size="sm"
      variant="primary"
      className="fi-lucius"
      iconLeft={<Spark size={15} ariaHidden />}
      aria-label={COPY.copyTradeLabel(what)}
      onClick={() => {
        sendNextPrefill()
        onAskLucius(copyTradePrompt(who, what))
      }}
    >
      {COPY.copyTrade}
    </Button>
  )
}
