import { CreditCard } from 'lucide-react'

// Actual's tile mark: a plain glyph in the same tile the Automation templates use, since this
// connector carries no brand logo.

export function ActualMark() {
  return (
    <span className="a3-tpl-ic">
      <CreditCard size={15} strokeWidth={1.7} aria-hidden="true" />
    </span>
  )
}
