// plan/CategoryKindFilter.tsx — collapses the Kind filter chip row (categoriesManager.tsx)
// into a single icon button + popover (desktop) / bottom sheet (phone), mirroring Investments
// Holdings' HoldingsFilterSheet pattern for the same "row of chips crowds a narrow toolbar"
// problem. A plain fixed-position popover (Kebab's pattern, this component's first version)
// works for a short 3-4 item per-row action menu, but a filter list read as a small floating
// card overlapping the table beneath it on a phone — every OTHER mobile filter/menu surface in
// this app (ActionMenu, HoldingsFilterSheet, Kebab's own sheet siblings) uses a full bottom
// sheet there instead, reusing the shared `kebab-sheet-*` classes and drag-to-dismiss.
//
// On desktop the chips come BACK inline whenever they fit (owner, 2026-10-08: "expand this to
// chips when it's desktop, only when it doesn't have enough space group as filter"). The host
// passes the width it can spare (`inlineWidth`); a hidden copy of the chip row measures its
// own natural width, so the swap tracks real label/count widths, not a guessed breakpoint.
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import { createPortal } from 'react-dom'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { useElementSize } from '../../../hooks/useElementWidth'
import { useSheetDragToDismiss } from '../../../hooks/useSheetDragToDismiss'
import type { SheetDragHandlers } from '../../../hooks/useSheetDragToDismiss'
import { X } from './icons'
import { Check } from '../../../ui/icons'

type PointerHandler = (event: ReactPointerEvent) => void

/** The phone sheet is portaled to <body> yet stays a React descendant of whatever opened it —
 * the Manage categories PlanningModal, whose own root spreads the same drag hook. Synthetic
 * pointer events bubble through portals, so without this a drag on THIS sheet (or a swipe on
 * its scrim) also dragged, and dismissed, the parent sheet underneath. React's stopPropagation
 * also stops the NATIVE event, so a bubble-phase document listener never sees these gestures
 * either — Radix's outside-press check is unaffected only because it flags "inside" in the
 * capture phase, before this runs. */
function isolatePointer(handlers?: SheetDragHandlers): SheetDragHandlers {
  const wrap =
    (h?: PointerHandler): PointerHandler =>
    (e) => {
      h?.(e)
      e.stopPropagation()
    }
  return {
    onPointerDown: wrap(handlers?.onPointerDown),
    onPointerMove: wrap(handlers?.onPointerMove),
    onPointerUp: wrap(handlers?.onPointerUp),
    onPointerCancel: wrap(handlers?.onPointerCancel),
  }
}

export interface KindFilterOption {
  id: string
  label: string
  count: number
}

function FilterIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 18 18"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      aria-hidden="true"
    >
      <path d="M2.5 4.5h13M5 9h8M7.5 13.5h3" strokeLinecap="round" />
    </svg>
  )
}

export function CategoryKindFilter({
  options,
  value,
  onChange,
  label = 'Filter categories',
  inlineWidth = 0,
}: {
  options: KindFilterOption[]
  value: string
  onChange: (id: string) => void
  label?: string
  /** Width the host can give an inline chip row (desktop only). 0 = always the icon. */
  inlineWidth?: number
}) {
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<{ left: number; top?: number; bottom?: number } | null>(null)
  const isMobile = useIsMobile()
  const rootRef = useRef<HTMLSpanElement>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const scrimRef = useRef<HTMLDivElement>(null)
  const active = value !== 'all'
  const [measureRef, chips] = useElementSize<HTMLDivElement>()
  const inline = !isMobile && chips.w > 0 && inlineWidth > 0 && chips.w <= inlineWidth

  const dragHandlers = useSheetDragToDismiss({
    sheetRef: panelRef,
    overlayRef: scrimRef,
    onDismiss: () => setOpen(false),
    enabled: open && isMobile,
  })

  useLayoutEffect(() => {
    if (!open || isMobile || !btnRef.current) {
      setPos(null)
      return
    }
    const r = btnRef.current.getBoundingClientRect()
    const W = 200
    const left = Math.round(Math.max(8, Math.min(r.right - W, window.innerWidth - W - 8)))
    const up = window.innerHeight - r.bottom < 260
    setPos(
      up
        ? { left, bottom: Math.round(window.innerHeight - r.top + 6) }
        : { left, top: Math.round(r.bottom + 6) },
    )
  }, [open, isMobile])

  useEffect(() => {
    if (!open) return
    // Same "inside" check as Kebab: the panel is portaled OUT of `rootRef`'s subtree, so a
    // plain ref.contains() would treat every click inside it as "outside" and close it
    // immediately. Harmless overlap with the mobile scrim's own onClick below — both just call
    // the same idempotent setOpen(false).
    const inside = (t: Node) => !!(rootRef.current?.contains(t) || panelRef.current?.contains(t))
    const onDown = (e: MouseEvent) => {
      if (!inside(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.preventDefault()
      setOpen(false)
      btnRef.current?.focus()
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey, true)
    // Popover mode only: it's anchored to the button's own rect, which a scroll/resize can
    // leave stale. The sheet is fixed to the viewport bottom regardless.
    // A popover anchored to its trigger's rect must close when the PAGE scrolls (the anchor
    // moves), but `capture: true` also delivers scrolls from INSIDE the popover's own
    // scroller — so opening the list and reaching for it dismissed it instantly
    // (owner-reported, 2026-09-15). Same guard budgetActivity/budgetMove already use.
    const onScroll = (e: Event) => {
      if (inside(e.target as Node)) return
      setOpen(false)
    }
    const close = () => setOpen(false)
    if (!isMobile) {
      window.addEventListener('resize', close)
      window.addEventListener('scroll', onScroll, true)
    }
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey, true)
      window.removeEventListener('resize', close)
      window.removeEventListener('scroll', onScroll, true)
    }
  }, [open, isMobile])

  useEffect(() => {
    if (!open || !isMobile) return
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = overflow
    }
  }, [open, isMobile])

  const list = options.map((o) => (
    <button
      key={o.id}
      type="button"
      role="option"
      aria-selected={value === o.id}
      className={
        (isMobile ? 'kebab-sheet-item' : 'pl-menu-item') +
        ' pl-catfilter-item' +
        (value === o.id ? ' on' : '')
      }
      onClick={() => {
        setOpen(false)
        onChange(o.id)
      }}
    >
      <span className="pl-catfilter-item-label">{o.label}</span>
      {/* Always present, just before the count: a fixed-width slot keeps the counts flush right
          in one column whether or not the row is the selected one. */}
      <span className="pl-catfilter-item-tick" aria-hidden="true">
        {value === o.id ? <Check size={12} aria-hidden="true" /> : null}
      </span>
      <span className="pl-catfilter-item-count">{o.count}</span>
    </button>
  ))

  // While the phone sheet is up, mark the host modal (PlanningModal / ui/Modal query their own
  // subtree for this) so its Escape handler yields — the sheet lives on <body>, so the marker
  // has to be on this wrapper, not on the sheet. The desktop popover carries it itself: it is
  // portaled INTO the modal.
  const escapeLayer = open && isMobile ? '' : undefined

  const chipRow = (measure: boolean) => (
    <div
      ref={measure ? measureRef : undefined}
      className={'pl-filter-chips pl-catfilter-chips' + (measure ? ' pl-catfilter-measure' : '')}
      role={measure ? undefined : 'radiogroup'}
      aria-label={measure ? undefined : label}
      aria-hidden={measure || undefined}
    >
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          role={measure ? undefined : 'radio'}
          aria-checked={measure ? undefined : value === o.id}
          tabIndex={measure ? -1 : undefined}
          className={'pl-fchip' + (value === o.id ? ' on' : '')}
          onClick={measure ? undefined : () => onChange(o.id)}
        >
          {o.label}
          <span className="c">{o.count}</span>
        </button>
      ))}
    </div>
  )
  const measurer = !isMobile && chipRow(true)

  if (inline)
    return (
      <span className="pl-catfilter-wrap">
        {chipRow(false)}
        {measurer}
      </span>
    )

  return (
    <span
      className={'pl-catfilter-wrap' + (open ? ' open' : '')}
      ref={rootRef}
      data-modal-escape-layer={escapeLayer}
    >
      <button
        ref={btnRef}
        type="button"
        className={'pl-catfilter-btn' + (active ? ' is-active' : '')}
        aria-label={active ? `${label} (active)` : label}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <FilterIcon />
      </button>
      {measurer}
      {open &&
        isMobile &&
        createPortal(
          <>
            <div
              ref={scrimRef}
              className="kebab-sheet-scrim"
              aria-hidden="true"
              onClick={() => setOpen(false)}
              {...isolatePointer()}
            />
            <div
              ref={panelRef}
              className="kebab-sheet"
              role="listbox"
              aria-label={label}
              {...isolatePointer(dragHandlers)}
            >
              <div className="kebab-sheet-handle-wrap">
                <span className="kebab-sheet-handle" />
              </div>
              <div className="kebab-sheet-head">
                <span className="kebab-sheet-title">{label}</span>
                <button
                  type="button"
                  className="kebab-sheet-x"
                  aria-label="Close"
                  onClick={() => setOpen(false)}
                >
                  <X size={18} />
                </button>
              </div>
              <div className="kebab-sheet-body">{list}</div>
            </div>
          </>,
          document.body,
        )}
      {open &&
        !isMobile &&
        pos &&
        createPortal(
          <div
            ref={panelRef}
            data-modal-escape-layer
            className="pl-menu pl-menu--kebab pl-catfilter-menu"
            role="listbox"
            aria-label={label}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                e.stopPropagation()
                setOpen(false)
                btnRef.current?.focus()
              }
            }}
            style={{
              position: 'fixed',
              left: pos.left + 'px',
              top: pos.top != null ? pos.top + 'px' : 'auto',
              bottom: pos.bottom != null ? pos.bottom + 'px' : 'auto',
              right: 'auto',
            }}
          >
            {list}
          </div>,
          rootRef.current?.closest('.pl-modal') ??
            document.querySelector('.app-shell') ??
            document.body,
        )}
    </span>
  )
}
