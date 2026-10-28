// "Tell Lucius what to remember or change" — one sentence, one memory-model call that adds,
// rewrites or forgets (POST /api/memory/instruct). The pill with the round send arrow, pinned to
// the bottom of the list, and above it what the last sentence changed: a count alone ("Updated 2")
// left the person guessing which facts moved (owner report, 2026-10-28).
import { useState } from 'react'
import { ArrowUp, X } from '../../../ui/icons'
import type { InstructChange } from '../../../hooks/useMemoryFacts'

const KIND: Record<InstructChange['change'], string> = {
  added: 'Added',
  updated: 'Updated',
  removed: 'Forgot',
}

function MemoryChanges({ changes, onClose }: { changes: InstructChange[]; onClose: () => void }) {
  return (
    <div className="rd-mem-changes" role="status">
      <div className="rd-mem-changes-head">
        <span>{changes.length ? 'What changed' : 'Nothing needed changing'}</span>
        <button
          type="button"
          className="rd-mem-changes-x"
          aria-label="Dismiss what changed"
          onClick={onClose}
        >
          <X size={14} aria-hidden="true" />
        </button>
      </div>
      {changes.length > 0 && (
        <ul className="rd-mem-changes-list">
          {changes.map((c, i) => (
            <li key={i}>
              <div className="rd-mem-change-meta">
                <span className={`rd-mem-change-kind is-${c.change}`}>{KIND[c.change]}</span>
                <span className="rd-mem-change-where">{c.where}</span>
              </div>
              <div
                className={c.change === 'removed' ? 'rd-mem-change-t is-gone' : 'rd-mem-change-t'}
              >
                {c.text}
              </div>
              {c.was && <div className="rd-mem-change-was">Was: {c.was}</div>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function MemoryAskBox({
  disabled,
  placeholder = 'Tell Lucius what to remember or change',
  onSubmit,
}: {
  disabled?: boolean
  placeholder?: string
  /** What changed, or null when the change was refused or failed (the input keeps its text). */
  onSubmit: (text: string) => Promise<InstructChange[] | null>
}) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [changes, setChanges] = useState<InstructChange[] | null>(null)
  return (
    <div className="rd-mem-dock">
      {changes && <MemoryChanges changes={changes} onClose={() => setChanges(null)} />}
      <form
        className="rd-mem-ask"
        onSubmit={async (e) => {
          e.preventDefault()
          const t = text.trim()
          if (!t || busy) return
          setBusy(true)
          setChanges(null)
          const out = await onSubmit(t)
          if (out) {
            setText('')
            setChanges(out)
          }
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
    </div>
  )
}
