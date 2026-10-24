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
  const { facts, forget, forgetAll } = useMemoryFacts(toast)
  const settings = useCachedFetchState(MEMORY_SETTINGS_KEY, getMemorySettings, { staleTime: 0 })
  const [q, setQ] = useState('')
  // "Forget everything" is destructive and has no undo: a REST delete outside an AI turn
  // takes no checkpoint, so the only recovery is yesterday's backup. Arm it like every other
  // destructive control in Settings rather than firing on one click.
  const [armedAll, setArmedAll] = useState(false)
  const needle = q.trim().toLowerCase()
  const shown = needle
    ? facts.filter((f) => (f.content + ' ' + (f.source || '')).toLowerCase().includes(needle))
    : facts

  return (
    <Modal
      title={'Stored memories (' + facts.length + ')'}
      size="lg"
      onClose={onClose}
      footer={
        <>
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
        </>
      }
    >
      <span className="rd-help">
        {settings.data?.enabled === false
          ? 'Memory is off — Lucius is not learning new facts or using these. Turn it on in ' +
            'Settings → Chat & Memory.'
          : 'Durable facts Lucius carries between conversations. Forgetting one takes effect on ' +
            'the next turn — it does not change your ledger or profile.'}
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
            : 'No memories stored. Lucius will add facts as you talk.'}
        </span>
      )}
    </Modal>
  )
}
