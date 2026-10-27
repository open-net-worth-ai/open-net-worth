import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

const GAP = 4
const EDGE = 8

interface Place {
  top: number
  left: number
  width: number
  above: boolean
}

export interface InfoPopoverProps {
  /** The bubble's content — usually a sentence; a short list where one "i" explains several
   *  figures. */
  text: ReactNode
  /** Pixel width of the bubble. Default 250. */
  width?: number
  /** A custom trigger in place of the circled "i" — e.g. a flag chip that explains itself. */
  children?: ReactNode
  /** Classes for a custom trigger (it keeps its own look; the button skin is reset). */
  triggerClassName?: string
  /** Accessible name for the trigger — a custom one whose visible content doesn't say enough, or
   *  the "i" when "More information" is too vague. */
  label?: string
}

/**
 * Hover-to-reveal info popover, with click/tap and keyboard-focus fallbacks.
 *
 * Renders a 16px circled "i" button. On a mouse, the bubble shows on
 * hover/focus and hides the moment the pointer leaves — no click required.
 * On touch (no hover), tapping PINS the bubble open; a fixed-position scrim
 * then captures the next outside tap to close it (scrim only, no duplicate
 * document listener — same as before). The scrim is never rendered for a
 * hover-only reveal, so briefly hovering the icon never swallows a click
 * elsewhere on the page. The button gets `aria-expanded` + `aria-controls`;
 * bubble gets `role="tooltip"` and an `id`.
 *
 * The bubble is portaled to <body> and fixed to the viewport beside the trigger, clamped inside
 * the screen and flipped above when there's no room below — an ancestor's overflow can't clip it.
 * Inline, a scrolling row (the account balance figures on a phone) swallowed it whole: the tap
 * pinned it open and nothing showed (owner report 2026-10-26). A scroll would leave it behind,
 * so it closes.
 */
export function InfoPopover({
  text,
  width = 250,
  children,
  triggerClassName = '',
  label,
}: InfoPopoverProps) {
  const [revealed, setRevealed] = useState(false)
  const [pinned, setPinned] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const bubbleRef = useRef<HTMLSpanElement>(null)
  const [place, setPlace] = useState<Place | null>(null)
  const bubbleId = useId()
  const open = revealed || pinned

  useLayoutEffect(() => {
    if (!open) return
    const r = triggerRef.current?.getBoundingClientRect()
    if (!r) return
    const w = Math.min(width, window.innerWidth - 2 * EDGE)
    const left = Math.max(EDGE, Math.min(r.left, window.innerWidth - w - EDGE))
    const h = bubbleRef.current?.offsetHeight ?? 0
    const above = r.bottom + GAP + h > window.innerHeight - EDGE && r.top - GAP - h >= EDGE
    setPlace({ top: above ? r.top - GAP : r.bottom + GAP, left, width: w, above })
  }, [open, width])

  useEffect(() => {
    if (!open) return
    const close = () => {
      setPinned(false)
      setRevealed(false)
    }
    window.addEventListener('scroll', close, true)
    window.addEventListener('resize', close)
    return () => {
      window.removeEventListener('scroll', close, true)
      window.removeEventListener('resize', close)
    }
  }, [open])

  function toggle(e: React.MouseEvent) {
    e.stopPropagation()
    setPinned((p) => !p)
  }

  function unpin(e: React.PointerEvent<HTMLSpanElement>) {
    e.preventDefault()
    e.stopPropagation()
    setPinned(false)
    setRevealed(false)
    triggerRef.current?.blur()
  }

  // Real `mouseenter`/`mouseleave` would also fire from a tap — iOS Safari replays a synthetic
  // mouseover/mousemove/mousedown/mouseup/click sequence after touchend on any element with a
  // click handler — and unlike a real mouse, a tap never produces a matching `mouseleave`. That
  // latched `revealed` true forever, so `open` (revealed || pinned) stayed true even after the
  // scrim tap cleared `pinned`: the bubble looked permanently stuck open on phones. Gating on
  // `pointerType === 'mouse'` ignores the touch-replayed events entirely.
  function onPointerEnter(e: React.PointerEvent) {
    if (e.pointerType === 'mouse') setRevealed(true)
  }

  function onPointerLeave(e: React.PointerEvent) {
    if (e.pointerType === 'mouse') setRevealed(false)
  }

  return (
    <span className="info-pop-wrap" onPointerEnter={onPointerEnter} onPointerLeave={onPointerLeave}>
      <button
        ref={triggerRef}
        type="button"
        className={
          children != null
            ? `info-pop-trigger ${triggerClassName}`.trim()
            : open
              ? 'info-pop-btn info-pop-btn--open'
              : 'info-pop-btn'
        }
        onClick={toggle}
        onFocus={() => setRevealed(true)}
        onBlur={() => setRevealed(false)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            setPinned(false)
            setRevealed(false)
            e.currentTarget.blur()
          }
        }}
        aria-label={label ?? (children != null ? undefined : 'More information')}
        aria-expanded={open}
        aria-controls={bubbleId}
      >
        {children ?? 'i'}
      </button>
      {open && (
        <>
          {/* The account editor's mobile sheet owns touch gestures, so this must run on the
              intercepted pointer event rather than waiting for its optional synthetic click. */}
          {pinned && <span className="info-pop-scrim" onPointerDown={unpin} />}
          {createPortal(
            <span
              ref={bubbleRef}
              id={bubbleId}
              role="tooltip"
              className={'info-pop-bubble' + (place?.above ? ' info-pop-bubble--above' : '')}
              style={
                place
                  ? { top: place.top, left: place.left, width: place.width }
                  : { visibility: 'hidden', width }
              }
            >
              {text}
            </span>,
            document.body,
          )}
        </>
      )}
    </span>
  )
}
