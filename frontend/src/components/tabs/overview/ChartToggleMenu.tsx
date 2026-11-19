// Overview v2 — widget header chart-type switcher. Compact button + dropdown,
// never wraps. Popover portals to document.body (mirrors KebabMenu's `portal`
// mode) since the trigger sits inside a clipped/scrollable `.panel`.

import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown } from '../../../ui/icons'
import type { WidgetChartToggle } from './WidgetShell'

export function ChartToggleMenu<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
}: WidgetChartToggle<T> & { ariaLabel?: string }) {
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  useLayoutEffect(() => {
    if (!open || !wrapRef.current) return
    const trigger = wrapRef.current.getBoundingClientRect()
    const menuW = menuRef.current?.offsetWidth ?? 0
    setPos({ top: trigger.bottom + 4, left: Math.max(4, trigger.right - menuW) })
  }, [open])

  useEffect(() => {
    if (!open) return
    function onDocClick(e: MouseEvent) {
      const target = e.target as Node
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

  const current = options.find((o) => o.value === value)

  return (
    <div className="kebab-wrap" ref={wrapRef}>
      <button
        type="button"
        className="ov-charttoggle__trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={ariaLabel}
        onClick={(e) => {
          e.stopPropagation()
          setOpen((o) => !o)
        }}
      >
        <span className="ov-charttoggle__label">{current?.label ?? value}</span>
        <ChevronDown size={11} strokeWidth={2.2} aria-hidden="true" />
      </button>
      {open &&
        createPortal(
          <div
            className="kebab-menu kebab-menu--portal"
            role="menu"
            ref={menuRef}
            // Inside a modal (the widget editor) <body> has pointer-events: none; the menu
            // portals there, so it takes its own clicks back.
            style={
              pos
                ? { top: pos.top, left: pos.left, pointerEvents: 'auto' }
                : { visibility: 'hidden' }
            }
          >
            {options.map((o) => {
              const active = o.value === value
              return (
                <button
                  key={o.value}
                  type="button"
                  role="menuitemradio"
                  aria-checked={active}
                  className={`kebab-item ov-charttoggle__item${active ? ' ov-charttoggle__item--active' : ''}`}
                  onClick={(e) => {
                    e.stopPropagation()
                    setOpen(false)
                    onChange(o.value)
                  }}
                >
                  <span>{o.label}</span>
                  {active && <Check size={13} strokeWidth={2.4} aria-hidden="true" />}
                </button>
              )
            })}
          </div>,
          document.body,
        )}
    </div>
  )
}
