// The one reusable "Ask Lucius about this" affordance. Generalizes the Cash transaction-row
// ✦ icon so EVERY datum can become a one-tap entry into chat. On activation it pushes a
// MentionRef into the chat-composer store (slides the panel out + stages a chip).
//
// Two forms, sharing one mechanism (no parallel code paths):
//   <LuciusAskButton type refId label className /> — a real <button> the widgets drop into their
//       own actions slot; the per-widget CSS class (.hold-ask-btn, .sumcard-ask-btn, …) owns the
//       hover-reveal / touch-visible visuals per the design spec.
//   useLuciusMention() — for elements that build the button themselves.
//
// Accessibility: a real <button type="button"> with aria-label/title "Ask Lucius about <label>",
// reachable in tab order, activated on Enter/Space. stopPropagation so a click inside a clickable
// row (drill-down, select) doesn't also toggle the row.

import type { ReactNode } from 'react'
import type { MentionType } from '../../types/mentions'
import { LuciusMark } from '../../ui/LuciusMark'
import { useLuciusMention } from '../../state/chatComposer'
import { chipMetaFromDom } from '../../lucius/chipMeta'

export { useLuciusMention } from '../../state/chatComposer'

interface LuciusAskButtonProps {
  type: MentionType
  refId: string
  label: string
  className: string
  glyph?: ReactNode // defaults to Lucius's mark
}

export function LuciusAskButton({
  type,
  refId,
  label,
  className,
  glyph = <LuciusMark size="1em" aria-hidden="true" />,
}: LuciusAskButtonProps) {
  const mention = useLuciusMention()
  const aria = `Ask Lucius about ${label}`
  return (
    <button
      type="button"
      className={className}
      aria-label={aria}
      title={aria}
      onClick={(e) => {
        e.stopPropagation()
        // Enrich the staged chip with date/amount from the live DOM row so the ✦ chip
        // matches the @-picker and drag chips (e.g. a transaction shows its date + signed
        // amount, not just the label). Off-screen → {} → graceful label-only.
        mention({ type, ref: refId, label, ...chipMetaFromDom(type, refId) })
      }}
    >
      {glyph}
    </button>
  )
}
