// Pieces of the stored-memories UI shared by the memory dialog and Settings → Memory's inline
// browser: one fact row (with its in-place editor), the forget confirm, and the "Tell Lucius" box.
import { useLayoutEffect, useRef, useState } from 'react'
import { ArrowUp, Pencil, Trash2 } from '../../../ui/icons'
import { memorySourceLabel } from '../../../hooks/useMemoryFacts'
import { fDate } from '../../../utils/format'
import { List } from '../../ui/List'
import { Btn } from './ds'
import type { MemoryFact } from '../../../hooks/useMemoryFacts'

/** A fact being edited: a box that grows to show the whole fact (facts run to several lines),
 *  Save and Cancel under it. Enter saves, Shift+Enter breaks a line; Esc is the dialog's. */
function MemoryFactEditor({
  fact,
  onCancel,
  onSave,
}: {
  fact: MemoryFact
  onCancel: () => void
  onSave: (content: string) => Promise<boolean>
}) {
  const [draft, setDraft] = useState(fact.content)
  const [saving, setSaving] = useState(false)
  const box = useRef<HTMLTextAreaElement>(null)

  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight + 2}px`
  }, [draft])

  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    el.focus()
    el.setSelectionRange(el.value.length, el.value.length)
  }, [])

  async function save() {
    const text = draft.trim()
    if (!text || saving) return
    if (text === fact.content.trim()) return onCancel()
    setSaving(true)
    if (await onSave(text)) onCancel()
    setSaving(false)
  }

  return (
    <form
      className="rd-fact rd-fact-edit"
      onSubmit={(e) => {
        e.preventDefault()
        void save()
      }}
    >
      <textarea
        ref={box}
        className="rd-textarea rd-fact-edit-text"
        rows={1}
        value={draft}
        maxLength={1000}
        disabled={saving}
        aria-label={'Edit: ' + fact.content}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault()
            void save()
          }
        }}
      />
      <div className="rd-fact-edit-acts">
        <Btn size="xs" variant="ghost" onClick={onCancel} disabled={saving}>
          Cancel
        </Btn>
        <Btn size="xs" type="submit" variant="primary" disabled={saving || !draft.trim()}>
          {saving ? 'Saving…' : 'Save'}
        </Btn>
      </div>
    </form>
  )
}

export function MemoryFactRow({
  fact,
  editing,
  onEdit,
  onCancel,
  onSave,
  onForget,
}: {
  fact: MemoryFact
  editing: boolean
  onEdit: () => void
  onCancel: () => void
  onSave: (content: string) => Promise<boolean>
  onForget: () => void
}) {
  if (editing) return <MemoryFactEditor fact={fact} onCancel={onCancel} onSave={onSave} />

  return (
    <div className="rd-fact">
      <div>
        <div className="rd-fact-t">{fact.content}</div>
        <div className="rd-fact-meta">
          <span>{memorySourceLabel(fact.source)}</span>
          <span>{fact.updated_at ? fDate(fact.updated_at) : ''}</span>
        </div>
      </div>
      <div className="rd-fact-acts">
        <button
          type="button"
          className="rd-fact-ibtn"
          title="Edit"
          aria-label={'Edit: ' + fact.content}
          onClick={onEdit}
        >
          <Pencil size={15} strokeWidth={1.8} aria-hidden="true" />
        </button>
        <button
          type="button"
          className="rd-fact-ibtn rd-fact-ibtn--forget"
          title="Forget"
          aria-label={'Forget: ' + fact.content}
          onClick={onForget}
        >
          <Trash2 size={15} strokeWidth={1.8} aria-hidden="true" />
        </button>
      </div>
    </div>
  )
}

/** A topic's facts as one memory, Claude-style: the design system's bullet `List`. The pencil and bin surface on
 *  hover (always on touch); a bullet being edited becomes the editor in place. */
export function MemoryBullets({
  facts,
  editingId,
  onEdit,
  onCancel,
  onSave,
  onForget,
}: {
  facts: MemoryFact[]
  editingId: string | null
  onEdit: (id: string) => void
  onCancel: () => void
  onSave: (id: string, content: string) => Promise<boolean>
  onForget: (fact: MemoryFact) => void
}) {
  return (
    <List>
      {facts.map((f) =>
        editingId === f.id ? (
          <li key={f.id} className="rd-mem-bullet-edit">
            <MemoryFactEditor
              fact={f}
              onCancel={onCancel}
              onSave={(content) => onSave(f.id, content)}
            />
          </li>
        ) : (
          <li key={f.id} className="rd-mem-bullet">
            <span className="rd-mem-bullet-t">{f.content}</span>
            <span className="rd-mem-bullet-acts">
              <button
                type="button"
                className="rd-fact-ibtn"
                title="Edit"
                aria-label={'Edit: ' + f.content}
                onClick={() => onEdit(f.id)}
              >
                <Pencil size={14} strokeWidth={1.8} aria-hidden="true" />
              </button>
              <button
                type="button"
                className="rd-fact-ibtn rd-fact-ibtn--forget"
                title="Forget"
                aria-label={'Forget: ' + f.content}
                onClick={() => onForget(f)}
              >
                <Trash2 size={14} strokeWidth={1.8} aria-hidden="true" />
              </button>
            </span>
          </li>
        ),
      )}
    </List>
  )
}

export type PendingForget =
  | { kind: 'one'; fact: MemoryFact }
  | { kind: 'all'; count: number }
  | { kind: 'topic'; name: string; ids: string[] }

/** "Forget this memory?" — Forget is a REST delete outside an AI turn, so no checkpoint backs it
 *  and the only recovery is a backup: it asks first, like Claude's memory. Esc and the scrim
 *  cancel (Esc via the dialog's own onClose, which owns the key). */
export function ForgetConfirm({
  pending,
  onCancel,
  onConfirm,
}: {
  pending: PendingForget
  onCancel: () => void
  onConfirm: () => void
}) {
  const one = pending.kind === 'one'
  const title =
    pending.kind === 'one'
      ? 'Forget this memory?'
      : pending.kind === 'topic'
        ? 'Delete this topic?'
        : `Forget all ${pending.count} memories?`
  const body =
    pending.kind === 'one'
      ? 'Lucius will no longer remember this. This can’t be undone.'
      : pending.kind === 'topic'
        ? `Lucius will forget the ${pending.ids.length} things he remembers under ${pending.name}. This can’t be undone.`
        : 'Lucius will start again with an empty memory. This can’t be undone.'
  return (
    <div
      className="rd-confirm-scrim"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onCancel()
      }}
    >
      <div
        className="rd-confirm"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="rd-forget-title"
        aria-describedby="rd-forget-body"
      >
        <div className="rd-confirm-title" id="rd-forget-title">
          {title}
        </div>
        <p className="rd-confirm-body" id="rd-forget-body">
          {body}
        </p>
        <div className="rd-confirm-acts">
          <Btn autoFocus onClick={onCancel}>
            Cancel
          </Btn>
          <Btn variant="danger" onClick={onConfirm}>
            {one ? 'Forget' : pending.kind === 'topic' ? 'Delete' : 'Forget all'}
          </Btn>
        </div>
      </div>
    </div>
  )
}

/** "Tell Lucius what to remember or change" — one sentence, one memory-model call that adds,
 *  rewrites or forgets (POST /api/memory/instruct). The pill with the round send arrow. */
export function MemoryAskBox({
  disabled,
  placeholder = 'Tell Lucius what to remember or change',
  onSubmit,
}: {
  disabled?: boolean
  placeholder?: string
  onSubmit: (text: string) => Promise<boolean>
}) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  return (
    <form
      className="rd-mem-ask"
      onSubmit={async (e) => {
        e.preventDefault()
        const t = text.trim()
        if (!t || busy) return
        setBusy(true)
        if (await onSubmit(t)) setText('')
        setBusy(false)
      }}
    >
      <input
        className="rd-mem-ask-input"
        value={text}
        disabled={disabled || busy}
        maxLength={1000}
        placeholder={disabled ? 'Turn memory on to make changes' : placeholder}
        aria-label="Tell Lucius what to remember or change"
        onChange={(e) => setText(e.target.value)}
      />
      <button
        type="submit"
        className="rd-mem-send"
        title="Update memory"
        aria-label="Update memory"
        aria-busy={busy || undefined}
        disabled={disabled || busy || !text.trim()}
      >
        {busy ? (
          <span className="rd-mem-spin" aria-hidden="true" />
        ) : (
          <ArrowUp size={16} strokeWidth={2.2} aria-hidden="true" />
        )}
      </button>
    </form>
  )
}
