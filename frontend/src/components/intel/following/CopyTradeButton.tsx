import { Button } from '../../ui'
import { Spark } from '../../../lucius/identity'
import { COPY, copyTradePrompt } from './following.copy'

interface Props {
  /** Whose trade it is, e.g. "Acme Capital". */
  who: string
  /** The trade in words, e.g. "bought ACME (Acme Corp) on Jul 28, 2026, reported as $1M to $5M". */
  what: string
  onAskLucius?: (text: string) => void
}

/** Copy ONE trade: writes the request for Lucius (size it, draft the order at the broker) into
 *  his message box, where you read it, change it if you like, and send it yourself. It never
 *  sends on its own (owner, 2026-11-13), as every "Ask Lucius" in the app waits in the box.
 *  The app's Lucius button, in Lucius's colour with its Spark. */
export function CopyTradeButton({ who, what, onAskLucius }: Props) {
  if (!onAskLucius) return null
  return (
    <Button
      size="sm"
      variant="primary"
      className="fi-lucius"
      iconLeft={<Spark size={15} ariaHidden />}
      aria-label={COPY.copyTradeLabel(what)}
      onClick={() => onAskLucius(copyTradePrompt(who, what))}
    >
      {COPY.copyTrade}
    </Button>
  )
}
