// "Screen awareness" — the transparency peek above the composer. Shows exactly what's
// being shared this turn: the snapshot facts passed inline, and the deep-content handles available
// on demand (tap "+ attach" to force-fetch one, else Lucius fetches as needed). A toggle turns
// screen-awareness off entirely. Design: docs/superpowers/specs/2026-06-24-screen-aware-context-design.md.

import { useState } from 'react'
import type { ScreenSnapshot } from '../../state/screenSnapshot'
import { Switch } from '../ui'
import { Check, Plus } from '../../ui/icons'

interface Props {
  snapshot: ScreenSnapshot | null
  enabled: boolean
  onToggle: () => void
  pinnedIds: string[]
  onPin: (id: string) => void
}

function prettyKey(k: string): string {
  const s = k.replace(/_/g, ' ').trim()
  return s.charAt(0).toUpperCase() + s.slice(1)
}

export function ScreenContextPanel({ snapshot, enabled, onToggle, pinnedIds, onPin }: Props) {
  const [collapsed, setCollapsed] = useState(false)

  // Nothing registered for the current screen and the feature is on → don't show a confusing
  // empty box. When OFF we still show the (collapsed) header so the user can turn it back on.
  if (!snapshot && enabled) return null

  const facts = snapshot?.facts ?? {}
  const handles = snapshot?.handles ?? []
  const summary = [snapshot?.title, ...Object.values(facts).slice(0, 2)].filter(Boolean).join(' • ')

  return (
    <div className={`screen-ctx${enabled ? '' : ' screen-ctx--off'}`}>
      <div className="screen-ctx__head">
        <span
          className={`screen-ctx__indicator${enabled ? ' screen-ctx__indicator--on' : ''}`}
          aria-hidden="true"
        >
          <span className="screen-ctx__dot" />
        </span>
        <div className="screen-ctx__head-text">
          <div className="screen-ctx__title">
            {enabled ? 'Screen awareness' : 'Screen awareness off'}
          </div>
          {enabled && summary && <div className="screen-ctx__sub">{summary}</div>}
        </div>
        {enabled && snapshot && (
          <button
            type="button"
            className="screen-ctx__hide"
            onClick={() => setCollapsed((c) => !c)}
          >
            {collapsed ? 'Peek' : 'Hide'}
          </button>
        )}
        <Switch checked={enabled} onChange={onToggle} aria-label="Toggle screen awareness" />
      </div>

      {enabled && snapshot && !collapsed && (
        <div className="screen-ctx__body">
          <div className="screen-ctx__section">Passed inline now</div>
          <dl className="screen-ctx__facts">
            <div className="screen-ctx__fact">
              <dt>Screen</dt>
              <dd>{snapshot.title}</dd>
            </div>
            {Object.entries(facts).map(([k, v]) => (
              <div className="screen-ctx__fact" key={k}>
                <dt>{prettyKey(k)}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
          <div className="screen-ctx__note">
            + the full visible text — labels, % deltas and figures that aren't @-mentionable.
          </div>

          {handles.length > 0 && (
            <>
              <div className="screen-ctx__section">
                Sources — tap to attach, or Lucius fetches as needed
              </div>
              <div className="screen-ctx__handles">
                {handles.map((h) => {
                  const pinned = pinnedIds.includes(h.id)
                  return (
                    <div className="screen-ctx__handle" key={h.id}>
                      <span className="screen-ctx__handle-icon" aria-hidden="true">
                        {h.icon ?? '◆'}
                      </span>
                      <div className="screen-ctx__handle-text">
                        <div className="screen-ctx__handle-label">{h.label}</div>
                        {h.descriptor && (
                          <div className="screen-ctx__handle-desc">{h.descriptor}</div>
                        )}
                      </div>
                      <button
                        type="button"
                        className={`screen-ctx__attach${pinned ? ' screen-ctx__attach--on' : ''}`}
                        aria-pressed={pinned}
                        onClick={() => onPin(h.id)}
                      >
                        {pinned ? (
                          <>
                            <Check size={12} aria-hidden="true" /> attached
                          </>
                        ) : (
                          <>
                            <Plus size={12} aria-hidden="true" /> attach
                          </>
                        )}
                      </button>
                    </div>
                  )
                })}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
