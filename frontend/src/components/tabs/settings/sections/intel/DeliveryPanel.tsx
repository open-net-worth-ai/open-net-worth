// Settings › Intelligence → What Intel should tell me: the person's own words that Lucius reads
// when deciding which news reaches them now, which waits for a roundup and which stays in the
// Intel tab. Shown only while the `intel_delivery_decider` experiment is on.
import { Panel } from '../../ds'
import type { PanelProps } from './fields'

export const DELIVERY_INSTRUCTIONS_MAX = 1000

export function DeliveryPanel({ cfg, save }: PanelProps) {
  const saved = cfg.delivery_instructions ?? ''
  return (
    <Panel
      title="What Intel should tell me"
      sub="In your own words — e.g. 'Don't tell me about analyst upgrades. Not at night unless it's serious.' Lucius reads this each time it decides what reaches you."
    >
      <textarea
        className="rd-textarea"
        rows={4}
        maxLength={DELIVERY_INSTRUCTIONS_MAX}
        aria-label="What Intel should tell me"
        placeholder="Don't tell me about analyst upgrades. Not at night unless it's serious."
        defaultValue={saved}
        key={saved}
        onBlur={(e) => {
          const next = e.target.value.trim()
          if (next !== saved) save({ delivery_instructions: next }, 'Saved')
        }}
      />
    </Panel>
  )
}
