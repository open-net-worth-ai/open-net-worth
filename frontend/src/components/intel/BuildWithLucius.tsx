import { Sparkles } from 'lucide-react'
import { useChatComposerOptional } from '../../state/chatComposer'

// "Build with Lucius" — hands Lucius a detailed brief, with the stock attached as a mention when
// its id is known, in the composer (the dock on desktop, the Lucius tab on a phone). Nothing is
// sent until the user presses send. Renders nothing outside a chat (tests, embeds).

export function BuildWithLucius({
  sym,
  instrumentId,
  prompt,
}: {
  sym: string
  instrumentId?: string
  prompt: string
}) {
  const composer = useChatComposerOptional()
  if (!composer) return null
  const start = () => {
    if (instrumentId) {
      composer.addMention({ type: 'instrument', ref: instrumentId, label: sym }, { open: false })
    }
    composer.setPendingText(prompt, { keepChips: true })
    window.dispatchEvent(new Event('lx-dock-open'))
  }
  return (
    <button type="button" className="asd-thesis-lucius" onClick={start}>
      <Sparkles size={13} aria-hidden="true" />
      Build with Lucius
    </button>
  )
}

/** The brief for alerts: the few levels that would matter for THIS investor, each with a reason,
 *  never repeating an alert already set, created only once the user confirms. */
export function alertsBuildPrompt(sym: string): string {
  return (
    `Help me set price alerts for ${sym}. First look at my position (cost, weight, gain or ` +
    'loss), my thesis if I have one, your latest take and its call, the recent price range and ' +
    'any upcoming events such as earnings. Then propose the few alerts that would actually ' +
    'matter to me — usually two or three — each with its price and one line on why that level ' +
    'matters (for example where you would want me to reconsider, or where taking profit makes ' +
    'sense). Skip any level my existing alerts already cover. Set them once I confirm.'
  )
}
