// Overview v2 — the Add-widgets modal: `pickerDefinitions` (WIDGET_DEFINITIONS
// minus Lucius-generated/hidden rows) filterable by group tab (flat, not
// sectioned — prototype `app.jsx` ~L1416-1454) or free search, a pinned
// "Build with Lucius" card, and (empty-dashboard state) a "Build a view"
// prompt. "Build with Lucius" opens the REAL Lucius panel/dock prefilled —
// it never runs a second, mocked generation pipeline (ADR 2026-07-22-
// overview-tab-redesign-faithful-port.md, decision 5).
//
// Two Lucius build modes, mirroring the prototype's `buildWithLucius('html')`
// vs `buildWithLucius()` split (app.jsx ~L836-850, L1410-1411): "Build widget"
// lands a `lux-html` instance and immediately opens the real chat
// panel prefilled with the prompt — content.tsx's `LuxHtml` is the receiving
// end (the same "Create with Lucius" + recent-artifact-pick flow it already
// offers in-widget; this is a second entry point into the identical
// mechanism, never a duplicate one).
//
// Previously this modal's "Ask Lucius" button only called `onAskLucius` and
// closed — it never called `onPick`/`addWidget`, so a Lucius-built widget's
// content renderer was unreachable from the UI (parity item 1). Fixed by
// routing both build actions through `onPickWithConfig` (new prop —
// `useDashboardLayout.addWidgetWithConfig`), which actually lands the
// instance before/alongside asking Lucius to fill it in.

import { useMemo, useState } from 'react'
import { Button, Modal, TextArea, TextField } from '../../ui'
import { WIDGET_GROUPS } from './definitions'
import type { WidgetDefinition } from './types'
import { Spark } from '../../../lucius/identity'
import { ArrowLeft } from '../../../ui/icons'

export interface AddWidgetPickerProps {
  open: boolean
  onClose: () => void
  definitions: WidgetDefinition[]
  onPick: (defId: string) => void
  /** Add a widget pre-seeded with extra config (the Lucius build flow) — kept
   * SEPARATE from `onPick` so its (defId) => void contract stays untouched
   * for every other call site. */
  onPickWithConfig?: (defId: string, config: Record<string, unknown>) => void
  onAskLucius?: (prompt: string) => void
  /** Fired after a widget lands on the dashboard — the caller renders the
   * transient "added" toast (item 8). */
  onAdded?: (message: string) => void
}

const BUILD_SUGGESTIONS = [
  'How much am I spending on coffee?',
  'My USD exposure over time',
  'Days of runway if income stopped',
]

export function AddWidgetPicker({
  open,
  onClose,
  definitions,
  onPick,
  onPickWithConfig,
  onAskLucius,
  onAdded,
}: AddWidgetPickerProps) {
  const [query, setQuery] = useState('')
  const [group, setGroup] = useState<string | null>(null)
  const [buildMode, setBuildMode] = useState(false)
  const [prompt, setPrompt] = useState('')

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return definitions.filter((d) => {
      if (group && d.group !== group) return false
      if (!q) return true
      return d.label.toLowerCase().includes(q) || d.desc.toLowerCase().includes(q)
    })
  }, [definitions, query, group])

  const resetAndClose = () => {
    onClose()
    setQuery('')
    setBuildMode(false)
    setPrompt('')
  }

  const pick = (d: WidgetDefinition) => {
    onPick(d.defId)
    onAdded?.(`${d.label} added`)
    resetAndClose()
  }

  const submitCustom = () => {
    const p = prompt.trim()
    if (!p) return
    onPickWithConfig?.('lux-html', { prompt: p })
    onAskLucius?.(`Build a widget for my Overview dashboard: ${p}`)
    onAdded?.('Lucius custom added — Lucius is building it now')
    resetAndClose()
  }

  return (
    <Modal
      open={open}
      onClose={resetAndClose}
      title={buildMode ? 'Build a widget with Lucius' : 'Add widgets'}
      size="lg"
    >
      <div className="ov-picker">
        {buildMode ? (
          <div className="ov-picker-build">
            <TextArea
              rows={3}
              placeholder="Describe the widget you want — what data, over what period…"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              data-autofocus
            />
            <div className="ov-picker-chips">
              {BUILD_SUGGESTIONS.map((c) => (
                <button
                  key={c}
                  type="button"
                  className="ov-picker-chip"
                  onClick={() => setPrompt(c)}
                >
                  {c}
                </button>
              ))}
            </div>
            <div className="ov-picker-build-actions ov-picker-build-actions--split">
              <button type="button" className="ov-picker-back" onClick={() => setBuildMode(false)}>
                <ArrowLeft size={14} aria-hidden="true" /> All widgets
              </button>
              <span className="ov-picker-build-actions">
                <Button
                  size="sm"
                  variant="primary"
                  className="fi-lucius"
                  iconLeft={<Spark size={15} ariaHidden />}
                  onClick={submitCustom}
                  disabled={!prompt.trim()}
                >
                  Build widget
                </Button>
              </span>
            </div>
            <div className="ov-picker-build-hint">
              Lucius reads your accounts, holdings, budget, and commitments — the widget stays live
              and refreshes with your data.
            </div>
          </div>
        ) : (
          <>
            <div className="ov-picker-toolbar">
              <TextField
                placeholder="Search widgets…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label="Search widgets"
                data-autofocus
              />
              <div className="ov-picker-tabs" role="tablist" aria-label="Widget group">
                <button
                  type="button"
                  className={'ov-picker-tab' + (group === null ? ' active' : '')}
                  onClick={() => setGroup(null)}
                >
                  All
                </button>
                {WIDGET_GROUPS.map((g) => (
                  <button
                    key={g}
                    type="button"
                    className={'ov-picker-tab' + (group === g ? ' active' : '')}
                    onClick={() => setGroup(g)}
                  >
                    {g}
                  </button>
                ))}
              </div>
            </div>

            <div className="ov-picker-grid">
              {onAskLucius && (
                <button
                  type="button"
                  className="ov-picker-lucius-card"
                  onClick={() => setBuildMode(true)}
                >
                  <span className="ov-lucius-spark">
                    <Spark size={15} />
                  </span>
                  <span>
                    <strong>Build with Lucius</strong>
                    <span className="ov-picker-lucius-desc">
                      Describe any view of your money — Lucius builds it as a live widget.
                    </span>
                  </span>
                </button>
              )}
              {filtered.length ? (
                <div className="ov-picker-tiles ov-picker-tiles--flat">
                  {filtered.map((d) => (
                    <button
                      key={d.defId}
                      type="button"
                      className="ov-picker-tile ov-picker-tile--row"
                      onClick={() => pick(d)}
                    >
                      <span className="ov-picker-tile-icon">{d.icon}</span>
                      <span className="ov-picker-tile-main">
                        <span className="ov-picker-tile-label">{d.label}</span>
                        <span className="ov-picker-tile-desc">{d.desc}</span>
                      </span>
                      <span className="ov-picker-tile-plus">
                        <svg
                          width="13"
                          height="13"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2.2"
                          strokeLinecap="round"
                        >
                          <line x1="12" y1="5" x2="12" y2="19" />
                          <line x1="5" y1="12" x2="19" y2="12" />
                        </svg>
                      </span>
                    </button>
                  ))}
                </div>
              ) : (
                <div className="ov-picker-empty">No widgets match "{query}".</div>
              )}
            </div>
          </>
        )}
      </div>
    </Modal>
  )
}
