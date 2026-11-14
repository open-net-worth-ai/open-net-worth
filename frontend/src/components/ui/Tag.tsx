import type { ReactNode } from 'react'
import { X } from '../../ui/icons'

/** `warn`: a caution about what the chip sits beside (an estimate, a missing report). */
export type TagTone = 'accent' | 'muted' | 'warn'

export interface TagProps {
  children: ReactNode
  onRemove?: () => void
  /** The remove button's accessible name — say what goes ("Remove Grab"). */
  removeLabel?: string
  tone?: TagTone
}

/** Tag — the pill chip (token-driven mirror of .acct-txn-tag). */
export function Tag({ children, onRemove, removeLabel = 'Remove tag', tone = 'accent' }: TagProps) {
  return (
    <span className={`ui-tag ui-tag--${tone}`}>
      {children}
      {onRemove && (
        <button
          type="button"
          className="ui-tag__remove"
          onClick={onRemove}
          aria-label={removeLabel}
        >
          {/* An icon, not the "×" character: fonts set that glyph at the lowercase middle, so it
              sat below the label's centre (owner: "why is the x button not centered"). */}
          <X size={12} strokeWidth={2.2} aria-hidden="true" />
        </button>
      )}
    </span>
  )
}
