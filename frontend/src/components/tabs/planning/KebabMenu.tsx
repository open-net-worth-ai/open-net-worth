import type { ReactNode } from 'react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { X, MoreHorizontal } from '../../../ui/icons'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { useSheetDragToDismiss } from '../../../hooks/useSheetDragToDismiss'

// A small overflow (⋯) menu: a trigger button that toggles a token-styled popover of
// actions, closing on outside-click or Escape. Used by the group manager so each group /
// category row collapses its actions into ONE control instead of a cramped button row.
// Design-system tokens only (no new primitive needed — there is no ui/ Menu yet).
//
// Below the phone breakpoint it renders as a bottom sheet instead — the app's other
// mobile-overlay pattern (see MobileTabBar's "More" sheet) — regardless of the `portal`
// prop, since a full-screen sheet portaled to `document.body` can't be clipped by any
// ancestor to begin with.
export interface KebabItem {
  label: string
  onClick: () => void
  danger?: boolean
  disabled?: boolean
}

export function KebabMenu({
  items,
  label = 'More actions',
  icon,
  portal,
}: {
  items: KebabItem[]
  label?: string
  /** Override the trigger's default "⋯" text glyph — e.g. Overview's grid
   * chrome uses a crisp vertical-dots SVG to match its own design handoff
   * exactly. Omitted = the original text glyph, byte-identical for every
   * existing caller. */
  icon?: ReactNode
  /** Desktop only: render the open popover into a `document.body` portal,
   * fixed-positioned from the trigger's own measured rect, instead of a CSS
   * `position: absolute` child of the trigger. Default false (byte-identical
   * for every existing caller). Use when the trigger sits inside a
   * clipped/scrollable ancestor. Below the phone breakpoint this is moot —
   * the mobile bottom sheet always portals to `document.body`. */
  portal?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [dropUp, setDropUp] = useState(false)
  const [portalPos, setPortalPos] = useState<{ top: number; left: number } | null>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const scrimRef = useRef<HTMLDivElement>(null)
  const isMobile = useIsMobile()
  const dragHandlers = useSheetDragToDismiss({
    sheetRef: menuRef,
    overlayRef: scrimRef,
    onDismiss: () => setOpen(false),
    // Currently harmless — the drag handlers are only spread inside the mobile-only JSX
    // branch below — but every other call site (SearchFilter, HoldingsFilterSheet,
    // ActionMenu) gates explicitly, so this stays an implicit invariant a future edit
    // could silently break otherwise.
    enabled: open && isMobile,
  })

  // Flip the menu above the trigger when it would clip the viewport bottom (bottom-row
  // kebabs in a long table). Measured against the trigger so it's stable in both directions.
  // In portal mode this same measurement also derives the fixed top/left the portaled menu
  // renders at, since it can no longer rely on CSS `position: absolute` relative to the trigger.
  // Not needed at all in the mobile sheet, which is bottom-docked, not trigger-anchored.
  useLayoutEffect(() => {
    if (!open || isMobile || !wrapRef.current || !menuRef.current) return
    const trigger = wrapRef.current.getBoundingClientRect()
    const menuH = menuRef.current.offsetHeight
    const menuW = menuRef.current.offsetWidth
    const up = window.innerHeight - trigger.bottom < menuH + 8 && trigger.top > menuH + 8
    setDropUp(up)
    if (portal) {
      setPortalPos({
        top: up ? trigger.top - menuH - 4 : trigger.bottom + 4,
        left: Math.max(4, trigger.right - menuW),
      })
    }
  }, [open, portal, isMobile])

  useEffect(() => {
    if (!open) return
    function onDocClick(e: MouseEvent) {
      const target = e.target as Node
      // menuRef is checked separately (not just wrapRef) because in portal/sheet mode the
      // menu is no longer a DOM descendant of wrapRef — without this, a click on a menu item
      // would register as an "outside" click and close the menu on mousedown, unmounting
      // the item before its own onClick ever fires.
      if (wrapRef.current?.contains(target) || menuRef.current?.contains(target)) return
      setOpen(false)
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  // A portaled menu is fixed to where the trigger WAS — once anything scrolls it would float
  // away from its ⋯, so it closes instead (as a native menu does). Capture phase: the scroll
  // may come from any scrollable ancestor, not just the window. Scrolls inside the menu itself
  // (a long item list) don't count.
  useEffect(() => {
    if (!open || !portal || isMobile) return
    function onScroll(e: Event) {
      // A window scroll's target is the window, not a Node — contains() would throw on it.
      if (e.target instanceof Node && menuRef.current?.contains(e.target)) return
      setOpen(false)
    }
    window.addEventListener('scroll', onScroll, true)
    return () => window.removeEventListener('scroll', onScroll, true)
  }, [open, portal, isMobile])

  // The sheet variant is a full-screen overlay — lock the page behind it, and move focus
  // in, mirroring MobileTabBar's own "More" sheet (MB2.3/MB2.5).
  useEffect(() => {
    if (!open || !isMobile) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    requestAnimationFrame(() => {
      menuRef.current?.querySelector<HTMLElement>('button')?.focus()
    })
    return () => {
      document.body.style.overflow = prev
    }
  }, [open, isMobile])

  if (isMobile) {
    return (
      <div className="kebab-wrap" ref={wrapRef}>
        <button
          type="button"
          className="kebab-trigger"
          aria-label={label}
          aria-haspopup="menu"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
        >
          {icon ?? <MoreHorizontal size={16} aria-hidden="true" />}
        </button>
        {open &&
          createPortal(
            <>
              <div
                ref={scrimRef}
                className="kebab-sheet-scrim"
                onClick={() => setOpen(false)}
                aria-hidden="true"
              />
              <div
                className="kebab-sheet"
                role="menu"
                ref={menuRef}
                aria-label={label}
                {...dragHandlers}
              >
                <div className="kebab-sheet-handle-wrap">
                  <span className="kebab-sheet-handle" />
                </div>
                <div className="kebab-sheet-head">
                  <span className="kebab-sheet-title">{label}</span>
                  <button
                    type="button"
                    className="kebab-sheet-x"
                    onClick={() => setOpen(false)}
                    aria-label="Close"
                  >
                    <X size={18} strokeWidth={1.6} aria-hidden="true" />
                  </button>
                </div>
                <div className="kebab-sheet-body">
                  {items.map((it) => (
                    <button
                      key={it.label}
                      type="button"
                      role="menuitem"
                      className={`kebab-sheet-item${it.danger ? ' kebab-sheet-item--danger' : ''}`}
                      disabled={it.disabled}
                      onClick={() => {
                        setOpen(false)
                        it.onClick()
                      }}
                    >
                      {it.label}
                    </button>
                  ))}
                </div>
              </div>
            </>,
            document.body,
          )}
      </div>
    )
  }

  const menu = open && (
    <div
      className={`kebab-menu${portal ? ' kebab-menu--portal' : dropUp ? ' kebab-menu--up' : ''}`}
      role="menu"
      ref={menuRef}
      style={portal && portalPos ? { top: portalPos.top, left: portalPos.left } : undefined}
    >
      {items.map((it) => (
        <button
          key={it.label}
          type="button"
          role="menuitem"
          className={`kebab-item${it.danger ? ' kebab-item--danger' : ''}`}
          disabled={it.disabled}
          onClick={() => {
            setOpen(false)
            it.onClick()
          }}
        >
          {it.label}
        </button>
      ))}
    </div>
  )

  return (
    <div className="kebab-wrap" ref={wrapRef}>
      <button
        type="button"
        className="kebab-trigger"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        {icon ?? <MoreHorizontal size={16} aria-hidden="true" />}
      </button>
      {portal ? menu && createPortal(menu, document.body) : menu}
    </div>
  )
}
