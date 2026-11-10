import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useIsMobile } from '../../hooks/useIsMobile'
import { useSheetDragToDismiss } from '../../hooks/useSheetDragToDismiss'
import { X, MoreHorizontal } from '../../ui/icons'
import { swallowNextClick } from '../../utils/swallowNextClick'

export interface ActionMenuItem {
  label: string
  onSelect: () => void
  tone?: 'default' | 'danger'
  disabled?: boolean
  icon?: ReactNode
}

export interface ActionMenuProps {
  items: ActionMenuItem[]
  label?: string
  /** The phone sheet's heading, when `label` (the trigger's accessible name, e.g. "More actions
   *  for Deed") says more than a heading needs to. Defaults to `label`. */
  title?: string
  trigger?: ReactNode
  className?: string
  /** Fires whenever the menu opens/closes. The dropdown itself is portaled to `document.body`
   *  (below) — outside the trigger's own DOM subtree — so a caller that closes on its OWN
   *  `mouseleave` (e.g. a hover-triggered flyout) sees the cursor "leave" the instant it
   *  travels toward the portaled menu, even while a click inside it is still in flight. Such a
   *  caller uses this to suppress its own close for as long as the menu stays open. */
  onOpenChange?: (open: boolean) => void
}

export function ActionMenu({
  items,
  label = 'More actions',
  title,
  trigger = <MoreHorizontal size={16} aria-hidden="true" />,
  className,
  onOpenChange,
}: ActionMenuProps) {
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const scrimRef = useRef<HTMLDivElement>(null)
  const requestedFocus = useRef<'first' | 'last' | null>(null)
  const isMobile = useIsMobile()
  const dragHandlers = useSheetDragToDismiss({
    sheetRef: menuRef,
    overlayRef: scrimRef,
    onDismiss: () => close(true),
    enabled: open && isMobile,
  })

  // NOT called from inside the setOpen(prev => ...) updater (an earlier version did this) —
  // updater functions must be pure, and calling a DIFFERENT component's setState from inside
  // one is exactly what React's "Cannot update a component while rendering a different
  // component" warning is about. It also made the callback's actual firing order relative to
  // code elsewhere in the same click handler unpredictable in practice: a caller (e.g.
  // ConversationRowMenu) that ALSO calls its own state setter in the same click, expecting
  // both to land in a defined order, could see this one fire later and stomp the other back to
  // a stale value — real-browser only; synchronous jsdom fireEvent tests never exposed it. A
  // plain effect keyed on `open` fires after commit, in normal React ordering, every time.
  useEffect(() => {
    onOpenChange?.(open)
  }, [open, onOpenChange])

  const close = (returnFocus = false) => {
    setOpen(false)
    if (returnFocus) triggerRef.current?.focus()
  }

  useLayoutEffect(() => {
    if (!open || isMobile || !triggerRef.current || !menuRef.current) return
    const triggerBox = triggerRef.current.getBoundingClientRect()
    const menuBox = menuRef.current.getBoundingClientRect()
    const top =
      triggerBox.bottom + menuBox.height + 8 > window.innerHeight
        ? triggerBox.top - menuBox.height - 4
        : triggerBox.bottom + 4
    setPosition({ top, left: Math.max(4, triggerBox.right - menuBox.width) })
  }, [open, isMobile])

  useLayoutEffect(() => {
    if (!open || !requestedFocus.current) return
    const buttons = Array.from(
      menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? [],
    ).filter((button) => !button.disabled)
    const target = requestedFocus.current === 'first' ? buttons[0] : buttons.at(-1)
    requestedFocus.current = null
    target?.focus()
  }, [open, isMobile])

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node
      if (triggerRef.current?.contains(target) || menuRef.current?.contains(target)) return
      // A tap on the phone sheet's backdrop only closes it — never opens what lay beneath.
      // The desktop menu has no backdrop: a click outside it still lands where it points.
      if (scrimRef.current?.contains(target)) swallowNextClick()
      close()
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])

  useEffect(() => {
    if (!open || !isMobile) return
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = overflow
    }
  }, [open, isMobile])

  const onMenuKeyDown = (event: React.KeyboardEvent) => {
    const buttons = Array.from(
      menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? [],
    ).filter((button) => !button.disabled)
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
    if (event.key === 'Escape') {
      event.preventDefault()
      close(true)
    } else if (event.key === 'ArrowDown') {
      event.preventDefault()
      buttons[(index + 1 + buttons.length) % buttons.length]?.focus()
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      buttons[(index - 1 + buttons.length) % buttons.length]?.focus()
    } else if (event.key === 'Home') {
      event.preventDefault()
      buttons[0]?.focus()
    } else if (event.key === 'End') {
      event.preventDefault()
      buttons.at(-1)?.focus()
    }
  }

  const menuItems = items.map((item) => (
    <button
      key={item.label}
      type="button"
      role="menuitem"
      className={`${isMobile ? 'kebab-sheet-item' : 'kebab-item'}${item.tone === 'danger' ? ` ${isMobile ? 'kebab-sheet-item--danger' : 'kebab-item--danger'}` : ''}`}
      disabled={item.disabled}
      onClick={() => {
        close(true)
        item.onSelect()
      }}
    >
      {item.icon}
      {item.label}
    </button>
  ))

  const menu =
    open &&
    (isMobile ? (
      <>
        <div
          ref={scrimRef}
          className="kebab-sheet-scrim"
          data-portal-layer
          aria-hidden="true"
          onClick={() => close(true)}
        />
        <div
          ref={menuRef}
          className="kebab-sheet"
          data-portal-layer
          role="menu"
          aria-label={label}
          onKeyDown={onMenuKeyDown}
          {...dragHandlers}
        >
          <div className="kebab-sheet-handle-wrap">
            <span className="kebab-sheet-handle" />
          </div>
          <div className="kebab-sheet-head">
            <span className="kebab-sheet-title">{title ?? label}</span>
            <button
              type="button"
              className="kebab-sheet-x"
              aria-label="Close"
              onClick={() => close(true)}
            >
              <X size={18} strokeWidth={1.6} aria-hidden="true" />
            </button>
          </div>
          <div className="kebab-sheet-body">{menuItems}</div>
        </div>
      </>
    ) : (
      <div
        ref={menuRef}
        className="kebab-menu kebab-menu--portal"
        // Portaled to <body>: a menu opened inside a Modal is still part of it — neither a tap
        // on it nor its focus counts as "outside" (Modal's isOutsideExempt).
        data-portal-layer
        role="menu"
        aria-label={label}
        style={position ?? undefined}
        onKeyDown={onMenuKeyDown}
      >
        {menuItems}
      </div>
    ))

  return (
    <span className={`kebab-wrap${className ? ` ${className}` : ''}`}>
      <button
        ref={triggerRef}
        type="button"
        className="kebab-trigger"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault()
            requestedFocus.current = event.key === 'ArrowDown' ? 'first' : 'last'
            setOpen(true)
          }
        }}
      >
        {trigger}
      </button>
      {menu ? createPortal(menu, document.body) : null}
    </span>
  )
}
