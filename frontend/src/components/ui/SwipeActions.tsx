// SwipeActions — iOS-style row actions for touch screens: the row slides left to reveal its
// buttons behind it at the right edge. Where there is a hover pointer it renders its children
// untouched, so the row keeps whatever hover controls it draws itself.
//
// The gesture is decided ONCE, at its first real movement (useSwipeBack's rule): a leftward
// horizontal pull — or any horizontal pull on a row already open — is ours, and every later
// move is kept from the ancestors' own gestures (Settings' swipe-back, the sheet's
// drag-to-dismiss); anything else, a vertical scroll above all, stays the browser's. The
// release is never stopped, so those ancestors still let go of the pointer.
import { useEffect, useMemo, useRef, useState } from 'react'
import type {
  FocusEvent as ReactFocusEvent,
  MouseEvent as ReactMouseEvent,
  PointerEvent as ReactPointerEvent,
  ReactNode,
  RefObject,
} from 'react'
import { useSwipeRows } from './useSwipeRows'

const CATCH_PX = 4
const OPEN_FRACTION = 0.4
const FLICK_PX_PER_MS = 0.5
// Before layout (or under jsdom) the actions have no width yet.
const FALLBACK_ACTION_PX = 72

export interface SwipeAction {
  label: string
  ariaLabel: string
  onClick: () => void
  tone?: 'danger'
  /** Drawn instead of the label, matching the row's own hover icons elsewhere. */
  icon?: ReactNode
}

// One open row in the whole app: opening another closes it.
let openRow: { close: () => void } | null = null

interface Drag {
  id: number
  x0: number
  y0: number
  t0: number
  base: number
  width: number
  x: number
  phase: 'pending' | 'drag' | 'native'
}

export function SwipeActions({
  actions,
  className,
  children,
}: {
  actions: SwipeAction[]
  className?: string
  children: ReactNode
}) {
  if (!useSwipeRows()) return <>{children}</>
  return (
    <SwipeRow actions={actions} className={className}>
      {children}
    </SwipeRow>
  )
}

function SwipeRow({
  actions,
  className,
  children,
}: {
  actions: SwipeAction[]
  className?: string
  children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const acts = useRef<HTMLDivElement>(null)
  const handle = useMemo(() => ({ close: () => setOpen(false) }), [])
  const gesture = useSwipeGesture({ root, acts, open, setOpen, count: actions.length })

  useEffect(() => {
    const el = root.current
    if (!el) return
    el.style.setProperty(
      '--ui-swipe-x',
      open ? `${-actionsWidth(acts.current, actions.length)}px` : '0px',
    )
    if (!open) return
    if (openRow && openRow !== handle) openRow.close()
    openRow = handle
    const onDown = (e: PointerEvent) => {
      if (!el.contains(e.target as Node)) setOpen(false)
    }
    const onScroll = () => {
      if (!el.contains(document.activeElement)) setOpen(false)
    }
    document.addEventListener('pointerdown', onDown, true)
    document.addEventListener('scroll', onScroll, true)
    return () => {
      document.removeEventListener('pointerdown', onDown, true)
      document.removeEventListener('scroll', onScroll, true)
      if (openRow === handle) openRow = null
    }
  }, [open, handle, actions.length])

  return (
    <div
      ref={root}
      className={className ? `ui-swipe ${className}` : 'ui-swipe'}
      data-open={open || undefined}
    >
      <div className="ui-swipe-face" {...gesture}>
        {children}
      </div>
      <div
        ref={acts}
        className="ui-swipe-acts"
        onFocus={() => setOpen(true)}
        onBlur={(e: ReactFocusEvent) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOpen(false)
        }}
      >
        {actions.map((a) => (
          <button
            key={a.label}
            type="button"
            className={a.tone === 'danger' ? 'ui-swipe-act ui-swipe-act--danger' : 'ui-swipe-act'}
            aria-label={a.ariaLabel}
            onClick={() => {
              setOpen(false)
              a.onClick()
            }}
          >
            {a.icon ?? a.label}
          </button>
        ))}
      </div>
    </div>
  )
}

function actionsWidth(el: HTMLElement | null, count: number): number {
  return el?.offsetWidth || FALLBACK_ACTION_PX * count
}

function useSwipeGesture({
  root,
  acts,
  open,
  setOpen,
  count,
}: {
  root: RefObject<HTMLDivElement | null>
  acts: RefObject<HTMLDivElement | null>
  open: boolean
  setOpen: (open: boolean) => void
  count: number
}) {
  const drag = useRef<Drag | null>(null)
  const swallowClick = useRef(false)

  const place = (x: number, dragging: boolean) => {
    const el = root.current
    if (!el) return
    el.style.setProperty('--ui-swipe-x', `${x}px`)
    if (dragging) el.setAttribute('data-dragging', '')
    else el.removeAttribute('data-dragging')
  }

  const end = (e: ReactPointerEvent, cancelled: boolean) => {
    const d = drag.current
    if (!d || d.id !== e.pointerId) return
    drag.current = null
    if (d.phase !== 'drag') return
    swallowClick.current = true
    const dt = e.timeStamp - d.t0
    const velocity = dt > 0 ? (d.x - d.base) / dt : 0
    const flick = !cancelled && Math.abs(velocity) > FLICK_PX_PER_MS
    const next = flick ? velocity < 0 : d.x < -d.width * OPEN_FRACTION
    place(next ? -d.width : 0, false)
    setOpen(next)
  }

  return {
    onPointerDown: (e: ReactPointerEvent) => {
      swallowClick.current = false
      if (e.pointerType === 'mouse' && e.button !== 0) return
      const width = actionsWidth(acts.current, count)
      const base = open ? -width : 0
      drag.current = {
        id: e.pointerId,
        x0: e.clientX,
        y0: e.clientY,
        t0: e.timeStamp,
        base,
        width,
        x: base,
        phase: 'pending',
      }
    },
    onPointerMove: (e: ReactPointerEvent) => {
      const d = drag.current
      if (!d || d.id !== e.pointerId || d.phase === 'native') return
      const dx = e.clientX - d.x0
      const dy = e.clientY - d.y0
      if (d.phase === 'pending') {
        if (Math.max(Math.abs(dx), Math.abs(dy)) < CATCH_PX) return
        const sideways = Math.abs(dx) > Math.abs(dy) && (dx < 0 || d.base < 0)
        d.phase = sideways ? 'drag' : 'native'
        if (!sideways) return
        try {
          e.currentTarget.setPointerCapture?.(e.pointerId)
        } catch {
          // a pointer the browser no longer tracks: the drag still works off plain bubbling
        }
      }
      e.stopPropagation()
      d.x = Math.min(0, Math.max(-d.width, d.base + dx))
      place(d.x, true)
    },
    onPointerUp: (e: ReactPointerEvent) => end(e, false),
    onPointerCancel: (e: ReactPointerEvent) => end(e, true),
    // The click a drag ends with is not a tap; a tap on an open row only closes it.
    onClickCapture: (e: ReactMouseEvent) => {
      if (!swallowClick.current && !open) return
      e.preventDefault()
      e.stopPropagation()
      if (swallowClick.current) swallowClick.current = false
      else setOpen(false)
    },
  }
}
