// The asset page's "Value history" sub-tab — every point in `marks`, newest first, with the
// change from the point before it. Each row is itself the control (owner, 2026-09-26: no
// per-row icon buttons): a recorded value opens "Correct this value", where it can also be
// removed (never the last one); the purchase point opens the Purchase editor. A phone gets a
// two-line list whose amounts line up in one column — one line when there's no note or change.
import { useState } from 'react'
import type { AssetMark, OwnedAsset } from '../../../types/assets'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { ConfirmDialog, EmptyState, Text } from '../../ui'
import * as api from './api'
import { canRemoveMark, fmt, fullDate } from './assetsData'
import { SaveError, useEditorSave } from './editorKit'
import { CorrectValueDialog } from './ValueDialogs'
import { ChevronRight } from '../../../ui/icons'

export interface ValueHistoryProps {
  a: OwnedAsset
  /** Opens the Purchase editor — the purchase point is edited there, never here. */
  onEditPurchase(): void
  onToast(message: string): void
}

interface HistoryRow {
  m: AssetMark
  /** Change from the next-older point; null for the oldest. */
  change: number | null
  /** "Bought", the value's note, "· automation" — empty when there's nothing to say. */
  note: string
  /** The row's name as a control — with its amount, since the name is all a reader hears. */
  label: string
}

function rowsOf(a: OwnedAsset): HistoryRow[] {
  return a.marks.map((m, i) => {
    const older = a.marks[i + 1]
    const purchase = m.kind === 'purchase'
    const said = [purchase ? 'Bought' : m.note, m.by === 'automation' ? 'automation' : '']
    return {
      m,
      change: older ? m.value - older.value : null,
      note: said.filter(Boolean).join(' · '),
      label: `Edit the ${purchase ? 'purchase' : 'value'} from ${fullDate(m.date)}, ${fmt(m.value)}`,
    }
  })
}

/** "+S$150,000" in green, "−S$20,000" in red — and a change too small to show, a plain S$0. */
function Change({ change, className }: { change: number; className?: string }) {
  const shown = fmt(Math.abs(change))
  const none = shown === fmt(0)
  return (
    <Text
      size="sm"
      weight={600}
      tone={none ? 'muted' : change > 0 ? 'good' : 'bad'}
      className={className}
    >
      {(none ? '' : change > 0 ? '+' : '−') + shown}
    </Text>
  )
}

interface HistoryViewProps {
  rows: HistoryRow[]
  onOpen(m: AssetMark): void
}

/** Desktop: the whole row opens its editor; the trailing › is its keyboard/AT control. */
function HistoryTable({ rows, onOpen }: HistoryViewProps) {
  return (
    <div className="as-tbl-wrap">
      <div className="as-tbl-scroll">
        <table className="as-tbl" style={{ minWidth: 640 }}>
          <thead>
            <tr>
              <th className="l">Date</th>
              <th>Value</th>
              <th className="l">Note</th>
              <th>Change</th>
              <th>
                <span className="ui-visually-hidden">Open</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ m, change, note, label }) => (
              <tr key={m.id} className="data" onClick={() => onOpen(m)}>
                <td className="l">{fullDate(m.date)}</td>
                <td className="money">{fmt(m.value)}</td>
                <td className="l dim">{note || '—'}</td>
                <td className="money">
                  {change == null ? (
                    <Text size="sm" tone="faint">
                      —
                    </Text>
                  ) : (
                    <Change change={change} />
                  )}
                </td>
                <td>
                  {/* Its click bubbles to the row, which opens the editor. */}
                  <button type="button" className="as-row-chevron" aria-label={label}>
                    <ChevronRight size="1em" aria-hidden="true" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/** Phone: each row is one button — date and value, then note and change, a › at the end. No
 *  "—" stands in for a missing note or change; with neither, the row is one line. */
function HistoryList({ rows, onOpen }: HistoryViewProps) {
  return (
    <div className="as-history-list">
      {rows.map(({ m, change, note, label }) => (
        <button
          key={m.id}
          type="button"
          className={
            note || change != null ? 'as-history-list-row' : 'as-history-list-row one-line'
          }
          aria-label={label}
          onClick={() => onOpen(m)}
        >
          <Text size="sm" weight={600} className="as-history-when">
            {fullDate(m.date)}
          </Text>
          <Text size="sm" weight={600} className="as-history-amount money">
            {fmt(m.value)}
          </Text>
          <span className="as-row-chevron" aria-hidden="true">
            <ChevronRight size="1em" />
          </span>
          {note && (
            <Text size="xs" tone="faint" truncate title={note} className="as-history-note">
              {note}
            </Text>
          )}
          {change != null && <Change change={change} className="as-history-change money" />}
        </button>
      ))}
    </div>
  )
}

interface RemoveValueConfirmProps {
  a: OwnedAsset
  mark: AssetMark
  onClose(): void
  onRemoved(): void
}

/** "Remove this value?" — it waits for the server and says why a refusal refused. */
function RemoveValueConfirm({ a, mark, onClose, onRemoved }: RemoveValueConfirmProps) {
  const { saving, save, dialogError } = useEditorSave()
  async function confirm() {
    if (await save(() => api.removeMark(a.id, mark.id))) onRemoved()
  }
  return (
    <ConfirmDialog
      data={{
        title: 'Remove this value?',
        body: (
          <>
            This removes the {fullDate(mark.date)} entry — {fmt(mark.value)}. This can&apos;t be
            undone.
            <SaveError message={dialogError([])} />
          </>
        ),
        confirmLabel: saving ? 'Removing…' : 'Remove',
        danger: true,
        confirmDisabled: saving,
      }}
      onConfirm={() => void confirm()}
      onCancel={onClose}
    />
  )
}

export function ValueHistory({ a, onEditPurchase, onToast }: ValueHistoryProps) {
  const isMobile = useIsMobile()
  const [correcting, setCorrecting] = useState<AssetMark | null>(null)
  const [removing, setRemoving] = useState<AssetMark | null>(null)

  if (!a.marks.length)
    return (
      <div className="as-tab-panel">
        <div className="as-tbl-wrap">
          <EmptyState title="No values yet" />
        </div>
      </div>
    )

  const rows = rowsOf(a)
  const open = (m: AssetMark) => (m.kind === 'purchase' ? onEditPurchase() : setCorrecting(m))
  const done = (message: string) => {
    setCorrecting(null)
    setRemoving(null)
    onToast(message)
  }

  return (
    <div className="as-tab-panel">
      {isMobile ? (
        <HistoryList rows={rows} onOpen={open} />
      ) : (
        <HistoryTable rows={rows} onOpen={open} />
      )}
      {correcting && (
        <CorrectValueDialog
          a={a}
          mark={correcting}
          removable={canRemoveMark(a, correcting)}
          onRemove={() => setRemoving(correcting)}
          onClose={() => setCorrecting(null)}
          onSaved={done}
        />
      )}
      {removing && (
        <RemoveValueConfirm
          a={a}
          mark={removing}
          onClose={() => setRemoving(null)}
          onRemoved={() => done('Value removed.')}
        />
      )}
    </div>
  )
}
