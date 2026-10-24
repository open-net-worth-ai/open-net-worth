// The stored-memories dialog — the ONE implementation. Settings → Chat & Memory → Review opens it
// inside the Settings shell; the chat header's Memory button opens it through
// components/chat/MemorySheet, which supplies the `.onw-settings` scope these classes need.
import { useState } from 'react'
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

export function MemoryDialog({ onClose, toast }: { onClose: () => void; toast: Toast }) {
  const { facts, forget, forgetAll, instruct } = useMemoryFacts(toast)
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
      onClose={onClose}
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
              className="rd-input"
              value={change}
              disabled={off || applying}
              maxLength={1000}
              placeholder={
                off ? 'Turn memory on to make changes' : 'Tell Lucius what to remember or change…'
              }
              aria-label="Tell Lucius what to remember or change"
              onChange={(e) => setChange(e.target.value)}
            />
            <Btn type="submit" variant="primary" disabled={off || applying || !change.trim()}>
              {applying ? 'Updating…' : 'Update'}
            </Btn>
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
          : 'Durable facts Lucius carries between conversations. Forgetting one takes effect on ' +
            'the next turn.'}
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
            <div className="rd-fact" key={f.id}>
              <div>
                <div className="rd-fact-t">{f.content}</div>
                <div className="rd-fact-meta">
                  <span>{memorySourceLabel(f.source)}</span>
                  <span>{f.updated_at ? fDate(f.updated_at) : ''}</span>
                </div>
              </div>
              <Btn
                size="xs"
                variant="ghost"
                onClick={() => void forget(f.id)}
                aria-label={'Forget: ' + f.content}
              >
                Forget
              </Btn>
            </div>
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
