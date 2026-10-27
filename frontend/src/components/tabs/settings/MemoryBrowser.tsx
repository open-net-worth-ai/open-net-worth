// Settings → Memory, below the settings rows: everything Lucius remembers, laid out like Claude's
// memory — sections, a row per topic, and a topic's own page with a back link. Organising is
// Lucius's job (ADR 2026-10-25-memory-keeps-only-what-has-no-other-home, amendments 3–4): the user
// edits and forgets facts, deletes a topic, or tells Lucius what to change.
import { useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { ChevronLeft, Pencil, Trash2 } from '../../../ui/icons'
import { fDate } from '../../../utils/format'
import { useMemoryFacts } from '../../../hooks/useMemoryFacts'
import type { MemoryFact } from '../../../hooks/useMemoryFacts'
import { Btn } from './ds'
import type { Toast } from './ds'
import { ForgetConfirm, MemoryAskBox, MemoryBullets } from './MemoryParts'
import type { PendingForget } from './MemoryParts'

// The fixed sections, in display order. `flat` sections list their facts directly (no topics).
const SECTIONS: readonly { key: string; label: string; flat?: boolean }[] = [
  { key: 'you', label: 'You' },
  { key: 'money', label: 'Money' },
  { key: 'plans', label: 'Plans' },
  { key: 'open_items', label: 'Open items', flat: true },
  { key: 'other', label: 'Other' },
]
// Facts saved before sections and topics existed, until the one-time sort files them.
const UNSORTED = { key: 'unsorted', label: 'Older memories — being sorted', flat: true }

type Topic = { name: string; facts: MemoryFact[]; updated: string }

function latest(facts: MemoryFact[]): string {
  return facts.reduce((m, f) => ((f.updated_at ?? '') > m ? (f.updated_at ?? '') : m), '')
}

function groupBySection(facts: MemoryFact[]) {
  const known = new Set(SECTIONS.map((s) => s.key))
  const bySection = new Map<string, MemoryFact[]>()
  for (const f of facts) {
    const key = f.section && known.has(f.section) ? f.section : UNSORTED.key
    bySection.set(key, [...(bySection.get(key) ?? []), f])
  }
  return bySection
}

function topicsOf(facts: MemoryFact[]): Topic[] {
  const byName = new Map<string, MemoryFact[]>()
  for (const f of facts) {
    const name = (f.topic ?? '').trim() || 'General'
    byName.set(name, [...(byName.get(name) ?? []), f])
  }
  return [...byName.entries()]
    .map(([name, fs]) => ({ name, facts: fs, updated: latest(fs) }))
    .sort((a, b) => (a.updated < b.updated ? 1 : -1))
}

/** `header` (the memory settings rows) shows above the list and is hidden while a topic is open —
 *  a topic's detail is its own page, as in Claude's memory. */
export function MemoryBrowser({
  toast,
  disabled,
  header,
}: {
  toast: Toast
  disabled?: boolean
  header?: ReactNode
}) {
  const { facts, loading, forget, edit, instruct } = useMemoryFacts(toast)
  const [open, setOpen] = useState<{ section: string; topic: string } | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [pending, setPending] = useState<PendingForget | null>(null)
  const bySection = groupBySection(facts)
  const top = useRef<HTMLDivElement>(null)

  // Opening or leaving a topic starts at its top, not wherever the list was scrolled to.
  useLayoutEffect(() => {
    top.current?.scrollIntoView({ block: 'start' })
  }, [open])

  function confirm() {
    const p = pending
    setPending(null)
    if (p?.kind === 'one') void forget(p.fact.id)
    if (p?.kind === 'topic') {
      p.ids.forEach((id) => void forget(id))
      setOpen(null)
    }
  }

  function bullets(list: MemoryFact[]) {
    return (
      <MemoryBullets
        facts={list}
        editingId={editingId}
        onEdit={setEditingId}
        onCancel={() => setEditingId(null)}
        onSave={edit}
        onForget={(fact) => setPending({ kind: 'one', fact })}
      />
    )
  }

  const confirmNode = pending && (
    <ForgetConfirm pending={pending} onCancel={() => setPending(null)} onConfirm={confirm} />
  )

  // A topic whose last fact was just forgotten no longer exists: fall back to the list.
  const topic = open
    ? topicsOf(bySection.get(open.section) ?? []).find((t) => t.name === open.topic)
    : undefined
  if (topic) {
    return (
      <div className="rd-mem-detail" ref={top}>
        <button type="button" className="rd-mem-back" onClick={() => setOpen(null)}>
          <ChevronLeft size={16} strokeWidth={1.8} aria-hidden="true" />
          Memory
        </button>
        <div className="rd-mem-detail-head">
          <div className="rd-mem-detail-title">{topic.name}</div>
          <Btn
            size="xs"
            variant="danger"
            onClick={() =>
              setPending({ kind: 'topic', name: topic.name, ids: topic.facts.map((f) => f.id) })
            }
          >
            Delete
          </Btn>
        </div>
        <div className="rd-mem-field">
          <div className="rd-mem-field-label">Last updated</div>
          <div>{topic.updated ? fDate(topic.updated) : '—'}</div>
        </div>
        <div className="rd-mem-field">
          <div className="rd-mem-field-label">Details</div>
          {bullets(topic.facts)}
        </div>
        <MemoryAskBox
          disabled={disabled}
          placeholder={`Tell Lucius what to change about ${topic.name}`}
          onSubmit={(text) => instruct(`About ${topic.name}: ${text}`)}
        />
        {confirmNode}
      </div>
    )
  }

  const sections = [...SECTIONS, UNSORTED].filter((s) => bySection.get(s.key)?.length)
  return (
    <div className="rd-mem-browser" ref={top}>
      {header}
      {loading ? (
        <div className="rd-factlist" aria-busy="true" aria-label="Loading memories">
          {[0, 1, 2].map((i) => (
            <div className="rd-fact rd-fact-skel" key={i}>
              <span className="rd-set-skelbar" />
              <span className="rd-set-skelbar short" />
            </div>
          ))}
        </div>
      ) : sections.length === 0 ? (
        <div className="rd-mem-empty">
          Nothing remembered yet. Tell Lucius something below, or he’ll pick things up as you talk.
        </div>
      ) : (
        sections.map((s) => {
          const list = bySection.get(s.key) ?? []
          return (
            <section className="rd-mem-sec" key={s.key} aria-label={s.label}>
              <div className="rd-mem-sec-h">{s.label}</div>
              {s.flat
                ? bullets(list)
                : topicsOf(list).map((t) => {
                    const openIt = () => setOpen({ section: s.key, topic: t.name })
                    return (
                      <div className="rd-mem-topic" key={t.name}>
                        <button type="button" className="rd-mem-topic-open" onClick={openIt}>
                          <span className="rd-mem-topic-name">{t.name}</span>
                          <span className="rd-mem-topic-preview">{t.facts[0]?.content}</span>
                          <span className="rd-mem-topic-date">
                            {t.updated ? `Updated ${fDate(t.updated)}` : ''}
                          </span>
                        </button>
                        <span className="rd-mem-topic-acts">
                          <button
                            type="button"
                            className="rd-fact-ibtn"
                            title="Edit"
                            aria-label={'Edit ' + t.name}
                            onClick={openIt}
                          >
                            <Pencil size={16} strokeWidth={1.8} aria-hidden="true" />
                          </button>
                          <button
                            type="button"
                            className="rd-fact-ibtn rd-fact-ibtn--forget"
                            title="Delete"
                            aria-label={'Delete ' + t.name}
                            onClick={() =>
                              setPending({
                                kind: 'topic',
                                name: t.name,
                                ids: t.facts.map((f) => f.id),
                              })
                            }
                          >
                            <Trash2 size={16} strokeWidth={1.8} aria-hidden="true" />
                          </button>
                        </span>
                      </div>
                    )
                  })}
            </section>
          )
        })
      )}
      <MemoryAskBox disabled={disabled} onSubmit={instruct} />
      {confirmNode}
    </div>
  )
}
