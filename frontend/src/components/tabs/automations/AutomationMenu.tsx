import { useEffect, useRef, useState, type ReactNode } from 'react'
import { MoreVertical, Check } from 'lucide-react'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { ComboSheet } from './ComboSheet'

export interface AutomationMenuItem {
  label: string
  onSelect: () => void
  danger?: boolean
  disabled?: boolean
  icon?: ReactNode
  checked?: boolean
}

export function AutomationMenu({
  items,
  label = 'More actions',
  trigger,
  triggerClassName = 'rd-kebab-btn',
  popoverClassName = '',
}: {
  items: AutomationMenuItem[]
  label?: string
  trigger?: ReactNode
  triggerClassName?: string
  popoverClassName?: string
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLSpanElement>(null)
  // On a phone the menu is a bottom sheet (portaled; it closes itself).
  const isMobile = useIsMobile()

  useEffect(() => {
    if (!open || isMobile) return
    const close = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open, isMobile])

  return (
    <span className="rd-kebab" ref={ref}>
      <button
        type="button"
        className={triggerClassName}
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={(event) => {
          event.stopPropagation()
          setOpen((value) => !value)
        }}
      >
        {trigger ?? <MoreVertical size={15} aria-hidden="true" />}
      </button>
      {open && isMobile && (
        // A tap in the sheet must not reach the card under it (React bubbles portal events).
        <span onClick={(event) => event.stopPropagation()}>
          <ComboSheet
            title={label}
            role="menuitem"
            items={items.map((item) => ({
              key: item.label,
              label: item.label,
              icon: item.icon,
              danger: item.danger,
              disabled: item.disabled,
              selected: item.checked,
            }))}
            onPick={(key) => items.find((item) => item.label === key)?.onSelect()}
            onClose={() => setOpen(false)}
          />
        </span>
      )}
      {open && !isMobile && (
        <span
          className={`rd-kebab-pop${popoverClassName ? ` ${popoverClassName}` : ''}`}
          role="menu"
          aria-label={label}
        >
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              disabled={item.disabled}
              className={`rd-kebab-item${item.danger ? ' danger' : ''}${item.icon || item.checked !== undefined ? ' a3-menu-row' : ''}`}
              onClick={(event) => {
                event.stopPropagation()
                setOpen(false)
                item.onSelect()
              }}
            >
              {item.checked !== undefined ? (
                <span className="a3-menu-check">
                  {item.checked ? <Check size={14} aria-hidden="true" /> : null}
                </span>
              ) : null}
              {item.icon}
              {item.label}
            </button>
          ))}
        </span>
      )}
    </span>
  )
}
