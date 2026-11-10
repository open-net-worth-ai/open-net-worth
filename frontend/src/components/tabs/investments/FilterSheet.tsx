// FilterSheet — the merged filter for constrained toolbars across the Investments sub-tabs.
//
// On a phone a row of filter chips collapses into ONE icon button (owner, 2026-07-30 for
// Holdings: "merge types filter and all account filters into one filter only", "just show as
// an filter icon"; 2026-09-04 for Activity: "group all of the pills into a filter button like
// we have in other sub tabs"). Chips ate the row that search needs, and none showed a label.
//
// A real phone uses the app's standard bottom sheet. A narrow desktop/Lucius pane uses a
// compact anchored popover: viewport width and pane width are different constraints. The
// trigger carries a dot when anything is filtered, so the collapsed state still tells you a
// filter is active. Generic over its groups — HoldingsFilterSheet composes it with
// Type + Account, ActivityTab with a single Type group.

import { useEffect, useRef, useState } from 'react'
import { X, Check } from '../../../ui/icons'
import { BottomSheet } from '../../ui/BottomSheet'

export interface FilterOption {
  id: string
  label: string
  /** Optional trailing count, e.g. the per-class row counts the chips used to show. */
  count?: number
}

export interface FilterGroup {
  title: string
  options: FilterOption[]
  value: string
  onChange: (id: string) => void
}

interface Props {
  className?: string
  presentation?: 'popover' | 'sheet'
  /** Dialog title, e.g. "Filter holdings". */
  title: string
  groups: FilterGroup[]
  /** Whether anything is filtered away from the default — drives the trigger's dot. */
  active: boolean
}

/** The radio lists inside a filter surface — one titled radiogroup per group. Also the body
 * of the Activity range sheet, so every single-choice sheet in Investments reads the same. */
export function FilterGroupList({
  groups,
  onPick,
}: {
  groups: FilterGroup[]
  /** Called after a group's onChange — the surface closes itself here. */
  onPick: () => void
}) {
  return (
    <>
      {groups.map((g) => (
        <div className="mk-filter-group" role="radiogroup" aria-label={g.title} key={g.title}>
          <div className="mk-filter-group-title">{g.title}</div>
          {g.options.map((o) => (
            <button
              key={o.id}
              type="button"
              role="radio"
              aria-checked={g.value === o.id}
              className={`kebab-sheet-item mk-filter-item${g.value === o.id ? ' mk-filter-item--on' : ''}`}
              onClick={() => {
                g.onChange(o.id)
                onPick()
              }}
            >
              <span className="mk-filter-item-label">{o.label}</span>
              {o.count != null && <span className="mk-filter-item-count">{o.count}</span>}
              {g.value === o.id && (
                <span className="mk-filter-item-tick" aria-hidden>
                  <Check size={14} />
                </span>
              )}
            </button>
          ))}
        </div>
      ))}
    </>
  )
}

export function FilterSheet({ className, presentation = 'popover', title, groups, active }: Props) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const popRef = useRef<HTMLDivElement>(null)

  // The desktop popover must leave the page scrollable and dismiss on Escape or a click
  // anywhere outside the anchored control. (The phone sheet owns its own lifecycle.)
  useEffect(() => {
    if (!open || presentation !== 'popover') return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    const onOutside = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('pointerdown', onOutside)
    requestAnimationFrame(() => {
      popRef.current?.querySelector<HTMLElement>('button')?.focus()
    })
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('pointerdown', onOutside)
    }
  }, [open, presentation])

  const close = () => setOpen(false)
  const body = <FilterGroupList groups={groups} onPick={close} />

  return (
    <div className={`mk-filter-wrap${className ? ` ${className}` : ''}`} ref={rootRef}>
      <button
        type="button"
        className={`mk-filter-btn${active ? ' is-active' : ''}`}
        aria-label={active ? 'Filters (active)' : 'Filters'}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(true)}
      >
        <svg
          width="17"
          height="17"
          viewBox="0 0 18 18"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          aria-hidden="true"
        >
          <path d="M2.5 4.5h13M5 9h8M7.5 13.5h3" strokeLinecap="round" />
        </svg>
      </button>
      {open && presentation === 'popover' && (
        <div className="mk-filter-popover" role="dialog" aria-label={title} ref={popRef}>
          <div className="mk-filter-popover-head">
            <span className="mk-filter-popover-title">{title}</span>
            <button
              type="button"
              className="mk-filter-popover-x"
              onClick={close}
              aria-label="Close"
            >
              <X size={18} strokeWidth={1.6} aria-hidden="true" />
            </button>
          </div>
          <div className="mk-filter-popover-body">{body}</div>
        </div>
      )}
      {open && presentation === 'sheet' && (
        <BottomSheet title={title} onClose={close}>
          {body}
        </BottomSheet>
      )}
    </div>
  )
}
