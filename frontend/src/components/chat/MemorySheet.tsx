import { useState } from 'react'
import { authFetch } from '../../api/client'
import { memorySourceLabel, useMemoryFacts } from '../../hooks/useMemoryFacts'
import { useCachedFetchState } from '../../state/queryCache'
import { fDate } from '../../utils/format'
import { Button } from '../ui/Button'
import { Modal } from '../ui/Modal'
import { TextField } from '../ui/TextField'

async function getMemorySettings(): Promise<{ enabled: boolean }> {
  const r = await authFetch('/api/memory/settings')
  if (!r.ok) throw new Error(String(r.status))
  return r.json() as Promise<{ enabled: boolean }>
}

/** What Lucius remembers, one click from the chat header — memory shapes every reply, so it
 *  should not live only three levels into Settings. Same list and deletes as Settings → Chat &
 *  Memory → Review (`useMemoryFacts`); the on/off switch stays in Settings. */
export function MemorySheet({ onClose }: { onClose: () => void }) {
  const [status, setStatus] = useState<string | null>(null)
  const { facts, loading, forget, forgetAll } = useMemoryFacts(setStatus)
  const settings = useCachedFetchState('/api/memory/settings', getMemorySettings, { staleTime: 0 })
  const [q, setQ] = useState('')
  // No undo behind "Forget everything" (a REST delete takes no checkpoint), so it arms first.
  const [armedAll, setArmedAll] = useState(false)
  const needle = q.trim().toLowerCase()
  const shown = needle
    ? facts.filter((f) => (f.content + ' ' + (f.source || '')).toLowerCase().includes(needle))
    : facts
  const off = settings.data?.enabled === false

  return (
    <Modal
      open
      onClose={onClose}
      title={loading ? 'Memory' : `Memory (${facts.length})`}
      subtitle="What Lucius carries from one conversation to the next"
      size="lg"
      className="mem-sheet"
      footer={
        <>
          <Button
            variant="danger"
            className="mem-sheet-forget-all"
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
          </Button>
          <Button onClick={onClose}>Done</Button>
        </>
      }
    >
      {off && (
        <p className="mem-sheet-note">
          Memory is off — Lucius isn't learning new facts or using these. Turn it on in Settings →
          Chat &amp; Memory.
        </p>
      )}
      <TextField
        value={q}
        placeholder="Search memories…"
        aria-label="Search memories"
        onChange={(e) => setQ(e.target.value)}
      />
      {status && (
        <p className="mem-sheet-status" role="status">
          {status}
        </p>
      )}
      {shown.length ? (
        <ul className="mem-sheet-list">
          {shown.map((f) => (
            <li className="mem-sheet-fact" key={f.id}>
              <div className="mem-sheet-fact-body">
                <div className="mem-sheet-fact-t">{f.content}</div>
                <div className="mem-sheet-fact-meta">
                  <span>{memorySourceLabel(f.source)}</span>
                  <span>{f.updated_at ? fDate(f.updated_at) : ''}</span>
                </div>
              </div>
              <Button
                size="dense"
                variant="ghost"
                onClick={() => void forget(f.id)}
                aria-label={'Forget: ' + f.content}
              >
                Forget
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mem-sheet-empty">
          {loading
            ? 'Loading…'
            : facts.length
              ? 'Nothing matches “' + q + '”.'
              : 'Nothing remembered yet. Tell Lucius “remember that…”, or he’ll pick facts up as you talk.'}
        </p>
      )}
    </Modal>
  )
}
