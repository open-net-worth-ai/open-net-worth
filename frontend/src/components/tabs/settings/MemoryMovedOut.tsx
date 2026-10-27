// "Moved out of memory (N)": facts that now live somewhere better (or went stale), kept 30 days
// in case that was wrong. Collapsed by default; Forget is the only action.
import { useState } from 'react'
import { ChevronDown, ChevronRight, Trash2 } from '../../../ui/icons'
import type { MemoryFact } from '../../../hooks/useMemoryFacts'
import { daysUntilForgotten, retiredReason } from './memoryGroups'

export function MemoryMovedOut({
  facts,
  onForget,
}: {
  facts: MemoryFact[]
  onForget: (fact: MemoryFact) => void
}) {
  const [open, setOpen] = useState(false)
  const Chevron = open ? ChevronDown : ChevronRight
  return (
    <section className="rd-mem-sec" aria-label="Moved out of memory">
      <button
        type="button"
        className="rd-mem-sec-h rd-mem-sec-toggle"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <Chevron size={16} strokeWidth={1.8} aria-hidden="true" />
        Moved out of memory ({facts.length})
      </button>
      {open &&
        facts.map((f) => {
          const days = daysUntilForgotten(f.retired_at)
          return (
            <div className="rd-mem-topic" key={f.id}>
              <div className="rd-mem-moved">
                <span className="rd-mem-topic-name">{retiredReason(f.retired_reason)}</span>
                <span className="rd-mem-topic-preview">{f.content}</span>
                <span className="rd-mem-topic-date">
                  {days === null ? '' : `Forgotten in ${days} ${days === 1 ? 'day' : 'days'}`}
                </span>
              </div>
              <span className="rd-mem-topic-acts">
                <button
                  type="button"
                  className="rd-fact-ibtn rd-fact-ibtn--forget"
                  title="Forget"
                  aria-label={'Forget: ' + f.content}
                  onClick={() => onForget(f)}
                >
                  <Trash2 size={16} strokeWidth={1.8} aria-hidden="true" />
                </button>
              </span>
            </div>
          )
        })}
    </section>
  )
}
