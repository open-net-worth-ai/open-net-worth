// The stored-memories dialog — the ONE implementation. Settings → Chat & Memory → Review opens it
// inside the Settings shell; the chat header's Memory button opens it through
// components/chat/MemorySheet, which supplies the `.onw-settings` scope these classes need.
import { useLayoutEffect, useRef, useState } from 'react'
import { ArrowUp, Pencil, Trash2 } from '../../../ui/icons'
import {
  MEMORY_SETTINGS_KEY,
  getMemorySettings,
  memorySourceLabel,
  useMemoryFacts,
} from '../../../hooks/useMemoryFacts'
import { useCachedFetchState } from '../../../state/queryCache'
import { fDate } from '../../../utils/format'
import { Btn, Modal } from './ds'
import type { Toast } from './ds'
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

function MemoryFactRow({
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

type PendingForget = { kind: 'one'; fact: MemoryFact } | { kind: 'all'; count: number }

/** "Forget this memory?" — Forget is a REST delete outside an AI turn, so no checkpoint backs it
 *  and the only recovery is a backup: it asks first, like Claude's memory. Esc and the scrim
 *  cancel (Esc via the dialog's own onClose, which owns the key). */
function ForgetConfirm({
  pending,
  onCancel,
  onConfirm,
}: {
  pending: PendingForget
  onCancel: () => void
  onConfirm: () => void
}) {
  const one = pending.kind === 'one'
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
          {one ? 'Forget this memory?' : `Forget all ${pending.count} memories?`}
        </div>
        <p className="rd-confirm-body" id="rd-forget-body">
          {one
            ? 'Lucius will no longer remember this. This can’t be undone.'
            : 'Lucius will start again with an empty memory. This can’t be undone.'}
        </p>
        <div className="rd-confirm-acts">
          <Btn autoFocus onClick={onCancel}>
            Cancel
          </Btn>
          <Btn variant="danger" onClick={onConfirm}>
            {one ? 'Forget' : 'Forget all'}
          </Btn>
        </div>
      </div>
    </div>
  )
}

export function MemoryDialog({ onClose, toast }: { onClose: () => void; toast: Toast }) {
  const { facts, forget, forgetAll, instruct, edit } = useMemoryFacts(toast)
  const [editingId, setEditingId] = useState<string | null>(null)
  const settings = useCachedFetchState(MEMORY_SETTINGS_KEY, getMemorySettings, { staleTime: 0 })
  const off = settings.data?.enabled === false
  const [q, setQ] = useState('')
  const [change, setChange] = useState('')
  const [applying, setApplying] = useState(false)
  const [pendingForget, setPendingForget] = useState<PendingForget | null>(null)
  const needle = q.trim().toLowerCase()
  const shown = needle
    ? facts.filter((f) => (f.content + ' ' + (f.source || '')).toLowerCase().includes(needle))
    : facts

  async function submitChange() {
    const text = change.trim()
    if (!text || applying) return
    setApplying(true)
    if (await instruct(text)) setChange('')
    setApplying(false)
  }

  function confirmForget() {
    const p = pendingForget
    setPendingForget(null)
    if (p?.kind === 'one') void forget(p.fact.id)
    else if (p) void forgetAll()
  }

  return (
    <>
      <Modal
        title={'Stored memories (' + facts.length + ')'}
        size="lg"
        // The dialog owns Escape (and the X and scrim): an open confirm or edit is cancelled first.
        onClose={() =>
          pendingForget ? setPendingForget(null) : editingId ? setEditingId(null) : onClose()
        }
        footer={
          <div className="rd-mem-foot">
            {/* Edit and add by saying it, as in Claude's memory: one sentence becomes one
              memory-model call that adds, rewrites or forgets (POST /api/memory/instruct). */}
            <form
              className="rd-mem-ask"
              onSubmit={(e) => {
                e.preventDefault()
                void submitChange()
              }}
            >
              <input
                className="rd-mem-ask-input"
                value={change}
                disabled={off || applying}
                maxLength={1000}
                placeholder={
                  off ? 'Turn memory on to make changes' : 'Tell Lucius what to remember or change'
                }
                aria-label="Tell Lucius what to remember or change"
                onChange={(e) => setChange(e.target.value)}
              />
              <button
                type="submit"
                className="rd-mem-send"
                title="Update memory"
                aria-label="Update memory"
                aria-busy={applying || undefined}
                disabled={off || applying || !change.trim()}
              >
                {applying ? (
                  <span className="rd-mem-spin" aria-hidden="true" />
                ) : (
                  <ArrowUp size={16} strokeWidth={2.2} aria-hidden="true" />
                )}
              </button>
            </form>
            <div className="rd-mem-actions">
              <Btn
                variant="danger"
                style={{ marginRight: 'auto' }}
                disabled={!facts.length}
                onClick={() => setPendingForget({ kind: 'all', count: facts.length })}
              >
                Forget everything
              </Btn>
              <Btn onClick={onClose}>Done</Btn>
            </div>
          </div>
        }
      >
        <span className="rd-help">
          {off
            ? 'Memory is off — Lucius is not learning new facts or using these. Turn it on in ' +
              'Settings → Chat & Memory.'
            : 'Durable facts Lucius carries between conversations. Editing or forgetting one takes ' +
              'effect on the next turn.'}
        </span>
        <input
          className="rd-input"
          value={q}
          placeholder="Search memories…"
          aria-label="Search memories"
          onChange={(e) => setQ(e.target.value)}
        />
        {shown.length ? (
          <div className="rd-factlist">
            {shown.map((f) => (
              <MemoryFactRow
                key={f.id}
                fact={f}
                editing={editingId === f.id}
                onEdit={() => setEditingId(f.id)}
                onCancel={() => setEditingId(null)}
                onSave={(content) => edit(f.id, content)}
                onForget={() => setPendingForget({ kind: 'one', fact: f })}
              />
            ))}
          </div>
        ) : (
          <span className="rd-help">
            {facts.length
              ? 'Nothing matches “' + q + '”.'
              : 'No memories stored. Tell Lucius something below, or he’ll pick facts up as you talk.'}
          </span>
        )}
      </Modal>
      {pendingForget && (
        <ForgetConfirm
          pending={pendingForget}
          onCancel={() => setPendingForget(null)}
          onConfirm={confirmForget}
        />
      )}
    </>
  )
}
