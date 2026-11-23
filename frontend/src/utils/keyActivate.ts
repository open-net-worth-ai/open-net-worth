import type { KeyboardEvent } from 'react'

// Keyboard parity for a clickable non-button element (a table row, a group header):
// focusable, and Enter/Space activate it like a click. Keys from a nested control are ignored.
export function keyActivate(onActivate: () => void) {
  return {
    tabIndex: 0,
    onClick: onActivate,
    onKeyDown: (e: KeyboardEvent<HTMLElement>) => {
      if (e.target !== e.currentTarget) return
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        onActivate()
      }
    },
  }
}
