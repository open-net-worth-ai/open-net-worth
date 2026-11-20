// Overview v2 — the per-widget "Display" panel (kebab menu → Display…): row
// cap for list-shaped widgets (the def's own `rows` choices), title visibility, auto-fit vs.
// fixed height for prose widgets, and the accent-color picker (curated
// swatches + the existing HSV custom picker, ported from account creation —
// reused here rather than a second implementation).

import { useState } from 'react'
import { Button, Modal } from '../../ui'
import { ColorPicker } from '../accounts/ds'
import { AUTOH_IDS, fitMode, heightModeFor } from './grid'
import { findDefinition } from './definitions'
import { ALL_ROWS, CURATED_ACCENTS, DEFAULT_CUSTOM_HEX, rowCap } from './widgetDisplayShared'
import type { HeightMode } from './types'

export interface WidgetDisplayPanelProps {
  open: boolean
  onClose: () => void
  defId: string
  hideTitle?: boolean
  heightMode?: HeightMode
  limit?: number | null
  accent?: string | null
  onSave: (patch: {
    hideTitle?: boolean
    heightMode?: HeightMode
    limit?: number | null
    accent?: string | null
  }) => void
}

export function WidgetDisplayPanel({
  open,
  onClose,
  defId,
  hideTitle,
  heightMode,
  limit,
  accent,
  onSave,
}: WidgetDisplayPanelProps) {
  const [draftHideTitle, setDraftHideTitle] = useState(!!hideTitle)
  const [draftFixed, setDraftFixed] = useState(!fitMode(defId, heightMode))
  const [draftLimit, setDraftLimit] = useState<number | null>(limit ?? null)
  const [draftAccent, setDraftAccent] = useState<string | null>(accent ?? null)

  const rows = findDefinition(defId)?.rows
  const isList = !!rows
  const isAutoh = AUTOH_IDS.has(defId)
  const isCurated = draftAccent == null || CURATED_ACCENTS.some((c) => c.key === draftAccent)
  const customHex = isCurated ? DEFAULT_CUSTOM_HEX : (draftAccent as string)

  const save = () => {
    onSave({
      hideTitle: draftHideTitle,
      heightMode: heightModeFor(defId, !draftFixed),
      limit: isList ? draftLimit : undefined,
      accent: draftAccent,
    })
    onClose()
  }

  return (
    <Modal open={open} onClose={onClose} title="Display" size="sm">
      <div className="ov-display-panel">
        <label className="ov-display-row ov-display-checkbox">
          <input
            type="checkbox"
            checked={draftHideTitle}
            onChange={(e) => setDraftHideTitle(e.target.checked)}
          />
          <span>Hide title</span>
        </label>

        {isAutoh && (
          <label className="ov-display-row ov-display-checkbox">
            <input
              type="checkbox"
              checked={draftFixed}
              onChange={(e) => setDraftFixed(e.target.checked)}
            />
            <span>Fixed height (manual resize + inner scroll)</span>
          </label>
        )}

        {isList && (
          <div className="ov-display-row">
            <span className="ov-display-label">Rows shown</span>
            <div className="ov-display-limit-seg" role="group" aria-label="Rows shown">
              {rows?.options.map((o) => {
                const value = o === 'all' ? null : o
                const on = rowCap({ limit: draftLimit }, { rows }) === value
                return (
                  <button
                    key={String(o)}
                    type="button"
                    className={'ov-display-limit-opt' + (on ? ' active' : '')}
                    onClick={() => setDraftLimit(value ?? ALL_ROWS)}
                  >
                    {o === 'all' ? 'All' : o}
                  </button>
                )
              })}
            </div>
          </div>
        )}

        <div className="ov-display-row">
          <span className="ov-display-label">Accent color</span>
          <div className="ov-display-swatches">
            <button
              type="button"
              className={
                'ov-display-swatch ov-display-swatch--none' + (draftAccent == null ? ' active' : '')
              }
              title="None"
              aria-label="No accent"
              onClick={() => setDraftAccent(null)}
            />
            {CURATED_ACCENTS.map((c) => (
              <button
                key={c.key}
                type="button"
                className={'ov-display-swatch' + (draftAccent === c.key ? ' active' : '')}
                style={{ background: c.cssVar }}
                title={c.label}
                aria-label={c.label}
                onClick={() => setDraftAccent(c.key)}
              />
            ))}
            <ColorPicker value={customHex} onChange={(hex) => setDraftAccent(hex)} />
          </div>
        </div>

        <div className="ov-display-actions">
          <Button size="sm" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button size="sm" variant="primary" onClick={save}>
            Save
          </Button>
        </div>
      </div>
    </Modal>
  )
}
