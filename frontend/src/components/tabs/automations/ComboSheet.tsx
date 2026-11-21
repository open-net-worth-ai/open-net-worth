// The phone presentation of the Rule editor's dropdowns and menus: the app's standard bottom
// sheet (drag to dismiss, scrim, page scroll lock) instead of a popover squeezed under a field.
// A long list gets a search box at the top; a multi-pick stays open with a Done button.
import { useState, type ReactNode } from 'react'
import { Check, Plus } from '../../../ui/icons'
import { BottomSheet } from '../../ui/BottomSheet'

export interface SheetItem {
  key: string
  label: string
  color?: string
  selected?: boolean
  icon?: ReactNode
  danger?: boolean
  disabled?: boolean
}

export function ComboSheet({
  title,
  items,
  searchable,
  multiple,
  role = 'option',
  onPick,
  onCreate,
  onClose,
}: {
  title: string
  items: SheetItem[]
  searchable?: boolean
  multiple?: boolean
  /** `menuitem` for an action menu, `option` for a value list. */
  role?: 'option' | 'menuitem'
  onPick: (key: string) => void
  /** Offer "Create “…”" for a typed name nothing matches. */
  onCreate?: (name: string) => void
  onClose: () => void
}) {
  const [q, setQ] = useState('')
  const ql = q.trim().toLowerCase()
  const shown = items.filter((item) => item.label.toLowerCase().includes(ql))
  const canCreate =
    Boolean(onCreate) && ql !== '' && !items.some((i) => i.label.toLowerCase() === ql)
  return (
    <BottomSheet
      title={title}
      onClose={onClose}
      footer={
        multiple ? (
          <button type="button" className="kebab-sheet-item rl-sheet-done" onClick={onClose}>
            Done
          </button>
        ) : undefined
      }
    >
      {searchable && (
        <input
          className="rl-sheet-search"
          type="search"
          placeholder="Search…"
          aria-label={`Search ${title.toLowerCase()}`}
          value={q}
          onChange={(event) => setQ(event.target.value)}
        />
      )}
      <div role={role === 'menuitem' ? 'menu' : 'listbox'} aria-label={title}>
        {shown.map((item) => (
          <button
            key={item.key}
            type="button"
            role={role}
            aria-selected={role === 'option' ? Boolean(item.selected) : undefined}
            disabled={item.disabled}
            className={`kebab-sheet-item rl-sheet-item${item.selected ? ' on' : ''}${item.danger ? ' danger' : ''}`}
            onClick={() => {
              onPick(item.key)
              if (!multiple) onClose()
            }}
          >
            {item.icon}
            {item.color && <i className="acct-payf-cdot" style={{ background: item.color }} />}
            <span className="rl-sheet-label">{item.label}</span>
            {item.selected && <Check size={15} aria-hidden="true" />}
          </button>
        ))}
        {canCreate && (
          <button
            type="button"
            role={role}
            className="kebab-sheet-item rl-sheet-item rl-combo-create"
            onClick={() => {
              onCreate?.(q.trim())
              setQ('')
              if (!multiple) onClose()
            }}
          >
            <Plus size={14} aria-hidden="true" /> Create “{q.trim()}”
          </button>
        )}
        {shown.length === 0 && !canCreate && <p className="a3-muted rl-sheet-empty">No matches</p>}
      </div>
    </BottomSheet>
  )
}
