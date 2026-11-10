// Overview v2 — the multi-view dashboard tab bar. Each tab holds an
// independent named `DashboardView` (its own widgets array + layout);
// switching tabs swaps the whole grid. `+` adds a new empty view. Drag a tab
// onto another to reorder them (item 11); the delete (×) affordance is
// visible on the active tab in EITHER mode (mirrors the handoff's `.ov-view-x`
// — it has no `editLayout` gate), guarded by `onRequestDelete`'s caller
// (Overview.tsx's confirm dialog) plus `useDashboardLayout.deleteView` itself
// refusing to remove the default/first or last-remaining view (item 12). Not
// built on `ui/PageTabs` — that primitive is for a FIXED set of page
// sections, while this bar's tab set is dynamic (user-added/renamed/deleted/
// reordered) with a trailing add affordance, which PageTabs' generic
// contract doesn't fit.

import { useState } from 'react'
import type { DashboardView } from './types'
import { X, Plus } from '../../../ui/icons'

export interface ViewTabsProps {
  views: DashboardView[]
  activeViewId: string | null
  onSelect: (viewId: string) => void
  onAdd: (name: string) => void
  onRename: (viewId: string, name: string) => void
  onRequestDelete: (viewId: string) => void
  onReorder: (fromViewId: string, toViewId: string) => void
  editing: boolean
}

export function ViewTabs({
  views,
  activeViewId,
  onSelect,
  onAdd,
  onRename,
  onRequestDelete,
  onReorder,
  editing,
}: ViewTabsProps) {
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [dragId, setDragId] = useState<string | null>(null)

  return (
    <div className="ov-view-tabs" role="tablist" aria-label="Dashboard views">
      {views.map((v, idx) => {
        const active = v.viewId === activeViewId
        if (renamingId === v.viewId) {
          return (
            <input
              key={v.viewId}
              className="ov-view-tab-input"
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={() => {
                if (draft.trim()) onRename(v.viewId, draft.trim())
                setRenamingId(null)
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') e.currentTarget.blur()
                if (e.key === 'Escape') setRenamingId(null)
              }}
            />
          )
        }
        return (
          <button
            key={v.viewId}
            type="button"
            role="tab"
            aria-selected={active}
            className={
              'ov-view-tab' +
              (active ? ' ov-view-tab--active' : '') +
              (dragId === v.viewId ? ' ov-view-tab--dragging' : '')
            }
            draggable
            onDragStart={(e) => {
              setDragId(v.viewId)
              e.dataTransfer.effectAllowed = 'move'
            }}
            onDragOver={(e) => {
              if (dragId && dragId !== v.viewId) e.preventDefault()
            }}
            onDrop={(e) => {
              e.preventDefault()
              if (dragId) onReorder(dragId, v.viewId)
              setDragId(null)
            }}
            onDragEnd={() => setDragId(null)}
            onClick={() => onSelect(v.viewId)}
            onDoubleClick={() => {
              if (!editing) return
              setDraft(v.name)
              setRenamingId(v.viewId)
            }}
            title={active ? 'Double-click to rename • drag to reorder' : v.name}
          >
            {v.name}
            {active && views.length > 1 && idx !== 0 && (
              <span
                className="ov-view-tab-close"
                role="button"
                tabIndex={0}
                aria-label={`Delete view ${v.name}`}
                onClick={(e) => {
                  e.stopPropagation()
                  onRequestDelete(v.viewId)
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    e.stopPropagation()
                    onRequestDelete(v.viewId)
                  }
                }}
              >
                <X size={12} aria-hidden="true" />
              </span>
            )}
          </button>
        )
      })}
      {editing && (
        <button
          type="button"
          className="ov-view-tab ov-view-tab--add"
          aria-label="Add view"
          onClick={() => onAdd('New view')}
        >
          <Plus size={14} aria-hidden="true" />
        </button>
      )}
    </div>
  )
}
