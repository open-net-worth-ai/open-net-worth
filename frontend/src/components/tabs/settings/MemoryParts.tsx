// Pieces of the one memory UI (MemoryBrowser, shown in Settings → Memory and in the chat's memory
// dialog): the fact bullets and rows with their in-place editor and actions, the forget confirm,
// and the "Tell Lucius" box.
import { useLayoutEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { ArrowUp, Pencil, Trash2 } from '../../../ui/icons'
import { fDate } from '../../../utils/format'
import { List } from '../../ui/List'
import { SwipeActions } from '../../ui/SwipeActions'
import { useSwipeRows } from '../../ui/useSwipeRows'
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

/** What a row's Edit and Delete/Forget do, and what they're called to a screen reader. */
export type RowActs = {
  editLabel: string
  forgetLabel: string
  word: 'Delete' | 'Forget'
  onEdit: () => void
  onForget: () => void
}

/** A row that, where rows swipe, slides left onto Edit and Delete/Forget. */
export function MemorySwipeRow({ acts, children }: { acts: RowActs; children: ReactNode }) {
  return (
    <SwipeActions
      actions={[
        {
          label: 'Edit',
          ariaLabel: acts.editLabel,
          onClick: acts.onEdit,
          icon: <Pencil size={20} strokeWidth={1.8} aria-hidden="true" />,
        },
        {
          label: acts.word,
          ariaLabel: acts.forgetLabel,
          onClick: acts.onForget,
          tone: 'danger',
          icon: <Trash2 size={20} strokeWidth={1.8} aria-hidden="true" />,
        },
      ]}
    >
      {children}
    </SwipeActions>
  )
}

/** The pencil and bin that surface on hover. Where rows swipe, the row's swipe actions take
 *  their place. */
export function MemoryHoverActs({
  acts,
  className,
  size,
}: {
  acts: RowActs
  className: string
  size: number
}) {
  if (useSwipeRows()) return null
  return (
    <span className={className}>
      <button
        type="button"
        className="rd-fact-ibtn"
        title="Edit"
        aria-label={acts.editLabel}
        onClick={acts.onEdit}
      >
        <Pencil size={size} strokeWidth={1.8} aria-hidden="true" />
      </button>
      <button
        type="button"
        className="rd-fact-ibtn rd-fact-ibtn--forget"
        title={acts.word}
        aria-label={acts.forgetLabel}
        onClick={acts.onForget}
      >
        <Trash2 size={size} strokeWidth={1.8} aria-hidden="true" />
      </button>
    </span>
  )
}

type FactListProps = {
  facts: MemoryFact[]
  editingId: string | null
  onEdit: (id: string) => void
  onCancel: () => void
  onSave: (id: string, content: string) => Promise<boolean>
  onForget: (fact: MemoryFact) => void
}

function factActs(
  f: MemoryFact,
  onEdit: (id: string) => void,
  onForget: (f: MemoryFact) => void,
): RowActs {
  return {
    editLabel: 'Edit: ' + f.content,
    forgetLabel: 'Forget: ' + f.content,
    word: 'Forget',
    onEdit: () => onEdit(f.id),
    onForget: () => onForget(f),
  }
}

/** A topic's facts as one memory, Claude-style: the design system's bullet `List`. A bullet
 *  being edited becomes the editor in place. */
export function MemoryBullets({
  facts,
  editingId,
  onEdit,
  onCancel,
  onSave,
  onForget,
}: FactListProps) {
  const swipe = useSwipeRows()
  return (
    <List>
      {facts.map((f) => {
        if (editingId === f.id)
          return (
            <li key={f.id} className="rd-mem-bullet-edit">
              <MemoryFactEditor
                fact={f}
                onCancel={onCancel}
                onSave={(content) => onSave(f.id, content)}
              />
            </li>
          )
        const acts = factActs(f, onEdit, onForget)
        return (
          <li key={f.id} className={swipe ? 'rd-mem-bullet rd-mem-bullet--swipe' : 'rd-mem-bullet'}>
            <MemorySwipeRow acts={acts}>
              <span className="rd-mem-bullet-t">
                {f.content}
                {f.expires_at && (
                  <span className="rd-mem-bullet-until"> · until {fDate(f.expires_at)}</span>
                )}
              </span>
              <MemoryHoverActs acts={acts} className="rd-mem-bullet-acts" size={14} />
            </MemorySwipeRow>
          </li>
        )
      })}
    </List>
  )
}

/** Facts listed straight under a section (open items, older memories) as rows shaped like the
 *  topic rows: the text where a topic's name and summary go, an open item's end date in the
 *  date column. A row being edited becomes the editor in place. */
export function MemoryFactRows({
  facts,
  editingId,
  onEdit,
  onCancel,
  onSave,
  onForget,
}: FactListProps) {
  return facts.map((f) => {
    if (editingId === f.id)
      return (
        <MemoryFactEditor
          key={f.id}
          fact={f}
          onCancel={onCancel}
          onSave={(content) => onSave(f.id, content)}
        />
      )
    const acts = factActs(f, onEdit, onForget)
    return (
      <MemorySwipeRow key={f.id} acts={acts}>
        <div className="rd-mem-topic">
          <div className="rd-mem-topic-open rd-mem-fact-body">
            <span className="rd-mem-fact-t">{f.content}</span>
            {f.expires_at && <span className="rd-mem-topic-date">Until {fDate(f.expires_at)}</span>}
          </div>
          <MemoryHoverActs acts={acts} className="rd-mem-topic-acts" size={16} />
        </div>
      </MemorySwipeRow>
    )
  })
}

export type PendingForget =
  | { kind: 'one'; fact: MemoryFact }
  | { kind: 'all'; count: number }
  | { kind: 'topic'; name: string; section: string; ids: string[]; named: boolean }

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
  const title =
    pending.kind === 'one'
      ? 'Forget this memory?'
      : pending.kind === 'topic'
        ? `Forget ${pending.name}?`
        : `Forget all ${pending.count} memories?`
  const body =
    pending.kind === 'one'
      ? 'Lucius will no longer remember this. This can’t be undone.'
      : pending.kind === 'topic'
        ? `Lucius will forget its ${pending.ids.length} ${pending.ids.length === 1 ? 'fact' : 'facts'}. This can’t be undone.`
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
            {pending.kind === 'all' ? 'Forget all' : 'Forget'}
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
