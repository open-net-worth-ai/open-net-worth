// The stored-memories dialog — the ONE implementation. Settings → Chat & Memory → Review opens it
// inside the Settings shell; the chat header's Memory button opens it through
// components/chat/MemorySheet, which supplies the `.onw-settings` scope these classes need.
import { useState } from 'react'
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
  const [draft, setDraft] = useState(fact.content)
  const [saving, setSaving] = useState(false)

  if (editing) {
    const unchanged = draft.trim() === fact.content.trim()
    return (
      <form
        className="rd-fact rd-fact-edit"
        onSubmit={async (e) => {
          e.preventDefault()
          if (!draft.trim() || saving) return
          if (unchanged) return onCancel()
          setSaving(true)
          if (await onSave(draft.trim())) onCancel()
          setSaving(false)
        }}
      >
        <input
          className="rd-input"
          value={draft}
          autoFocus
          maxLength={1000}
          disabled={saving}
          aria-label={'Edit: ' + fact.content}
          onChange={(e) => setDraft(e.target.value)}
        />
        <Btn size="xs" type="submit" variant="primary" disabled={saving || !draft.trim()}>
          {saving ? 'Saving…' : 'Save'}
        </Btn>
        <Btn size="xs" variant="ghost" onClick={onCancel} disabled={saving}>
          Cancel
        </Btn>
      </form>
    )
  }

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
          onClick={() => {
            setDraft(fact.content)
            onEdit()
          }}
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

export function MemoryDialog({ onClose, toast }: { onClose: () => void; toast: Toast }) {
  const { facts, forget, forgetAll, instruct, edit } = useMemoryFacts(toast)
  const [editingId, setEditingId] = useState<string | null>(null)
  const settings = useCachedFetchState(MEMORY_SETTINGS_KEY, getMemorySettings, { staleTime: 0 })
  const off = settings.data?.enabled === false
  const [q, setQ] = useState('')
  const [change, setChange] = useState('')
  const [applying, setApplying] = useState(false)
  // "Forget everything" is destructive and has no undo: a REST delete outside an AI turn
  // takes no checkpoint, so the only recovery is yesterday's backup. Arm it like every other
  // destructive control in Settings rather than firing on one click.
  const [armedAll, setArmedAll] = useState(false)
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

  return (
    <Modal
      title={'Stored memories (' + facts.length + ')'}
      size="lg"
      // The dialog owns Escape (and the X and scrim): mid-edit, they cancel the edit first.
      onClose={() => (editingId ? setEditingId(null) : onClose())}
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
              variant={armedAll ? 'danger-solid' : 'danger'}
              style={{ marginRight: 'auto' }}
              disabled={!facts.length}
              onClick={() => {
                if (!armedAll) {
                  setArmedAll(true)
                  setTimeout(() => setArmedAll(false), 4000)
                } else {
                  setArmedAll(false)
                  void forgetAll()
                }
              }}
            >
              {armedAll ? `Confirm — forget all ${facts.length}` : 'Forget everything'}
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
              onForget={() => void forget(f.id)}
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
  )
}
