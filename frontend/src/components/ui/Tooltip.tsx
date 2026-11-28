import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactElement } from 'react'
import { shortcutKeys } from '../../utils/shortcuts'

// macOS-like: long enough that sweeping past a control doesn't flash a tip.
const SHOW_DELAY_MS = 650
// Moving between adjacent controls shows the next tip at once, like a native toolbar.
const WARM_WINDOW_MS = 300
let lastHiddenAt = 0

interface TooltipProps {
  label: string
  /** Key sequence, `Mod` meaning ⌘ / Ctrl: `['Mod', 'B']`. */
  shortcut?: readonly string[]
  /** Below the control (title-bar buttons) or to its right (sidebar rail icons). */
  side?: 'bottom' | 'right'
  /** Show only while the control's text is cut off (its content overflows) — for a truncated
   *  cell whose full text the tip reveals. */
  onlyWhenClipped?: boolean
  /** The label's `\n`s break lines — for a short list (a figure's parts) rather than a name. */
  multiline?: boolean
  children: ReactElement
}

// A dark pill beside its control with the label and a muted shortcut. Shown after a hover delay
// and on keyboard focus. Positioned against the viewport (position: fixed), so a clipping
// ancestor such as the sidebar can't cut it off, and nudged back inside the window. The anchor is
// `display: contents`, so wrapping a control never changes its layout.
export function Tooltip({
  label,
  shortcut,
  side = 'bottom',
  onlyWhenClipped,
  multiline,
  children,
}: TooltipProps) {
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const anchor = useRef<HTMLSpanElement>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const bubble = useRef<HTMLSpanElement>(null)
  const id = useId()

  const clear = () => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = null
  }
  const show = (immediate: boolean) => {
    clear()
    const target = anchor.current?.firstElementChild
    if (onlyWhenClipped && (!target || target.scrollWidth <= target.clientWidth)) return
    if (immediate || Date.now() - lastHiddenAt < WARM_WINDOW_MS) setOpen(true)
    else timer.current = setTimeout(() => setOpen(true), SHOW_DELAY_MS)
  }
  const hide = () => {
    clear()
    if (open) lastHiddenAt = Date.now()
    setOpen(false)
  }
  useEffect(() => clear, [])

  useLayoutEffect(() => {
    const target = anchor.current?.firstElementChild
    if (!open || !target) {
      setPos(null)
      return
    }
    const r = target.getBoundingClientRect()
    const gap = 8
    if (side === 'right') {
      setPos({ top: r.top + r.height / 2, left: r.right + gap })
      return
    }
    const width = bubble.current?.offsetWidth ?? 0
    const margin = 8
    const centre = r.left + r.width / 2
    const left = Math.min(Math.max(centre - width / 2, margin), window.innerWidth - margin - width)
    setPos({ top: r.bottom + gap, left })
  }, [open, side])

  return (
    <span
      ref={anchor}
      className="tip-anchor"
      onMouseEnter={() => show(false)}
      onMouseLeave={hide}
      // Keyboard focus only: a clicked button keeps focus, and a focus event also fires when the
      // window regains focus, so plain onFocus re-opened the tip with no hover.
      onFocus={(e) => {
        if ((e.target as Element).matches?.(':focus-visible')) show(true)
      }}
      onBlur={hide}
      onPointerDown={hide}
      onKeyDown={(e) => e.key === 'Escape' && hide()}
      onClick={hide}
      aria-describedby={open ? id : undefined}
    >
      {children}
      {open && (
        <span
          ref={bubble}
          id={id}
          role="tooltip"
          className={`tip-bubble tip-bubble--${side}${multiline ? ' tip-bubble--multiline' : ''}`}
          style={pos ? { top: pos.top, left: pos.left } : { visibility: 'hidden' }}
        >
          <span>{label}</span>
          {shortcut && (
            <span className="tip-keys" aria-hidden="true">
              {shortcutKeys(shortcut).map((key) => (
                <span key={key}>{key}</span>
              ))}
            </span>
          )}
        </span>
      )}
    </span>
  )
}
