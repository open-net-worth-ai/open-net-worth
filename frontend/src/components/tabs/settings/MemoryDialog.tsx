// The stored-memories dialog, opened from the chat header's Memory button through
// components/chat/MemorySheet (which supplies the `.onw-settings` scope these classes need).
// Settings → Memory shows the same memory inline (MemoryBrowser); both use MemoryParts.
import { useState } from 'react'
import { ArrowUp } from '../../../ui/icons'
import { ForgetConfirm, MemoryFactRow } from './MemoryParts'
import type { PendingForget } from './MemoryParts'
import {
  MEMORY_SETTINGS_KEY,
  getMemorySettings,
  useMemoryFacts,
} from '../../../hooks/useMemoryFacts'
import { useCachedFetchState } from '../../../state/queryCache'
import { Btn, Modal } from './ds'
import type { Toast } from './ds'

export function MemoryDialog({ onClose, toast }: { onClose: () => void; toast: Toast }) {
  const { facts, loading, forget, forgetAll, instruct, edit } = useMemoryFacts(toast)
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
        title={loading ? 'Stored memories' : 'Stored memories (' + facts.length + ')'}
        size="lg"
        // A fixed height: sized to its content, the dialog shrank with every keystroke of a
        // search and all but vanished on a phone when nothing matched.
        className="rd-mem-modal"
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
              'Settings → Memory.'
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
        {needle && !loading && (
          <span className="rd-mem-count" role="status">
            {shown.length} of {facts.length}{' '}
            {facts.length === 1 ? 'memory matches' : 'memories match'}
          </span>
        )}
        {loading ? (
          <div className="rd-factlist" aria-busy="true" aria-label="Loading memories">
            {[0, 1, 2, 3].map((i) => (
              <div className="rd-fact rd-fact-skel" key={i}>
                <span className="rd-set-skelbar" />
                <span className="rd-set-skelbar short" />
              </div>
            ))}
          </div>
        ) : shown.length ? (
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
          <div className="rd-mem-empty">
            {facts.length
              ? 'No memories match “' + q.trim() + '”.'
              : 'No memories stored. Tell Lucius something below, or he’ll pick facts up as you talk.'}
          </div>
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
