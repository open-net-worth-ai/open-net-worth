// ctxMenu.tsx — the "Ask Lucius about this" context menu, portaled to document.body.
//
// Opened by dragRightClick.tsx from a right-click (contextmenu) or a touch long-press on a
// [data-entity-id] row. Owns two behaviours the opener must not have to think about:
//
//   Placement — the requested point is where the pointer/finger was; the menu is clamped so
//   it stays fully inside the viewport with an 8px margin (a press near the right or bottom
//   edge used to render half off-screen).
//
//   Dismissal — on the next pointerdown OUTSIDE the menu (immediate: the press that opened
//   it has already happened, so no arming delay is needed and the row tap that navigates to
//   a detail screen closes it before it navigates), on scroll, resize, Escape, history
//   navigation, and when the row it was opened from leaves the DOM (state-driven screen
//   change with no pointer event to catch).

import { LuciusMark } from '../../../ui/LuciusMark'
import { useLayoutEffect, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { MentionRef } from '../../../types/mentions'

export interface CtxMenuState {
  x: number
  y: number
  ref: MentionRef
  /** The [data-entity-id] element the menu was opened from — dismisses when it unmounts. */
  anchor?: Element | null
}

const VIEWPORT_MARGIN = 8

/** Clamp a requested top-left so a `w`×`h` box stays inside the viewport. */
function clampToViewport(
  x: number,
  y: number,
  w: number,
  h: number,
  vw: number,
  vh: number,
): { x: number; y: number } {
  const maxX = Math.max(VIEWPORT_MARGIN, vw - w - VIEWPORT_MARGIN)
  const maxY = Math.max(VIEWPORT_MARGIN, vh - h - VIEWPORT_MARGIN)
  return {
    x: Math.min(Math.max(VIEWPORT_MARGIN, x), maxX),
    y: Math.min(Math.max(VIEWPORT_MARGIN, y), maxY),
  }
}

function useDismiss(state: CtxMenuState, onClose: () => void) {
  useEffect(() => {
    const dismiss = () => onClose()
    const onPointerDown = (e: Event) => {
      const target = e.target as Element | null
      if (target?.closest?.('.lx-ctxmenu')) return
      onClose()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('pointerdown', onPointerDown, true)
    document.addEventListener('keydown', onKey)
    document.addEventListener('scroll', dismiss, true)
    window.addEventListener('resize', dismiss)
    window.addEventListener('popstate', dismiss)
    window.addEventListener('hashchange', dismiss)

    const anchor = state.anchor ?? null
    const observer =
      anchor && typeof MutationObserver !== 'undefined'
        ? new MutationObserver(() => {
            if (!anchor.isConnected) onClose()
          })
        : null
    observer?.observe(document.body, { childList: true, subtree: true })

    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true)
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('scroll', dismiss, true)
      window.removeEventListener('resize', dismiss)
      window.removeEventListener('popstate', dismiss)
      window.removeEventListener('hashchange', dismiss)
      observer?.disconnect()
    }
  }, [state, onClose])
}

export function CtxMenu({
  state,
  onAsk,
  onClose,
}: {
  state: CtxMenuState
  onAsk: (ref: MentionRef) => void
  onClose: () => void
}) {
  const menuRef = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState({ x: state.x, y: state.y })

  // Measure once mounted (before paint) and pull the box back inside the viewport.
  useLayoutEffect(() => {
    const el = menuRef.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    const next = clampToViewport(
      state.x,
      state.y,
      rect.width,
      rect.height,
      window.innerWidth,
      window.innerHeight,
    )
    setPos((cur) => (cur.x === next.x && cur.y === next.y ? cur : next))
  }, [state])

  useDismiss(state, onClose)

  const style: React.CSSProperties = {
    position: 'fixed',
    top: pos.y,
    left: pos.x,
    // Deliberately its own tier above --z-modal, not a match with it — this menu is
    // reachable from INSIDE an open modal (e.g. right-clicking a payee row in
    // PayeesModal), so tying it to the same layer real modals use left the winner a
    // coin flip on DOM paint order. Full rationale: the --z-lucius-ctxmenu comment on
    // .lx-ctxmenu in lucius.css.
    zIndex: 'var(--z-lucius-ctxmenu)' as unknown as number,
  }

  return createPortal(
    <div
      ref={menuRef}
      className="lx-ctxmenu"
      style={style}
      onMouseDown={(e) => e.preventDefault()}
      data-ctx-ignore
      // Portaled straight to document.body, so NOT a DOM descendant of .lx-dock even though
      // it's a Lucius-owned surface. ui/Modal's outside-click guard matches on this marker too
      // — see isLuciusOwned.
      data-lucius-portal
    >
      <button
        onClick={(e) => {
          e.stopPropagation()
          onAsk(state.ref)
          onClose()
        }}
      >
        <span className="lx-ctxmenu-ic">
          <LuciusMark size="1em" aria-hidden="true" />
        </span>
        Ask Lucius about this
      </button>
    </div>,
    document.body,
  )
}
