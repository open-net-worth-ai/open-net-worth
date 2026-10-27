// Settings → Memory, below the settings rows: everything Lucius remembers, laid out like Claude's
// memory — sections, a row per topic, and a topic's own page with a back link. Organising is
// Lucius's job (ADR 2026-10-25-memory-keeps-only-what-has-no-other-home, amendments 3–4): the user
// edits and forgets facts, deletes a topic, or tells Lucius what to change.
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type MutableRefObject,
  type ReactNode,
} from 'react'
import { ChevronLeft, Pencil, Trash2 } from '../../../ui/icons'
import { fDate } from '../../../utils/format'
import { useMemoryFacts } from '../../../hooks/useMemoryFacts'
import type { MemoryFact } from '../../../hooks/useMemoryFacts'
import { Btn } from './ds'
import type { Toast } from './ds'
import { ForgetConfirm, MemoryAskBox, MemoryBullets } from './MemoryParts'
import type { PendingForget } from './MemoryParts'
import { SECTIONS, UNSORTED, groupFacts, matchesFilter, summaryOf, topicsOf } from './memoryGroups'
import type { Topic } from './memoryGroups'

function TopicRow({
  topic,
  onOpen,
  onDelete,
}: {
  topic: Topic
  onOpen: () => void
  onDelete: () => void
}) {
  return (
    <div className="rd-mem-topic">
      <button type="button" className="rd-mem-topic-open" onClick={onOpen}>
        <span className="rd-mem-topic-name">{topic.name}</span>
        <span className="rd-mem-topic-preview">{topic.facts[0]?.content}</span>
        <span className="rd-mem-topic-date">
          {topic.updated ? `Updated ${fDate(topic.updated)}` : ''}
        </span>
      </button>
      <span className="rd-mem-topic-acts">
        <button
          type="button"
          className="rd-fact-ibtn"
          title="Edit"
          aria-label={'Edit ' + topic.name}
          onClick={onOpen}
        >
          <Pencil size={16} strokeWidth={1.8} aria-hidden="true" />
        </button>
        <button
          type="button"
          className="rd-fact-ibtn rd-fact-ibtn--forget"
          title="Delete"
          aria-label={'Delete ' + topic.name}
          onClick={onDelete}
        >
          <Trash2 size={16} strokeWidth={1.8} aria-hidden="true" />
        </button>
      </span>
    </div>
  )
}

/** `header` (the memory settings rows) shows above the list and is hidden while a topic is open —
 *  a topic's detail is its own page, as in Claude's memory. `filter` narrows the list to facts
 *  whose text or topic name contains it; `escapeRef` lets a host dialog ask the browser to back
 *  out of an open confirm or edit first (it returns true when it did). `onTopicOpen` tells a host
 *  when a topic page opens or closes, so it can hide list-wide actions. */
export function MemoryBrowser({
  toast,
  disabled,
  header,
  filter = '',
  escapeRef,
  onTopicOpen,
}: {
  toast: Toast
  disabled?: boolean
  header?: ReactNode
  filter?: string
  escapeRef?: MutableRefObject<(() => boolean) | null>
  onTopicOpen?: (open: boolean) => void
}) {
  const { facts, summaries, loading, forget, edit, instruct, deleteTopic } = useMemoryFacts(toast)
  const [open, setOpen] = useState<{ section: string; key: string } | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [pending, setPending] = useState<PendingForget | null>(null)
  const needle = filter.trim().toLowerCase()
  const all = groupFacts(facts)
  const shown = needle ? groupFacts(facts.filter((f) => matchesFilter(f, needle))) : all
  const top = useRef<HTMLDivElement>(null)

  // Opening or leaving a topic starts at its top, not wherever the list was scrolled to.
  const lastOpen = useRef(open)
  useLayoutEffect(() => {
    if (lastOpen.current !== open) top.current?.scrollIntoView({ block: 'start' })
    lastOpen.current = open
  }, [open])

  useEffect(() => {
    if (!escapeRef) return
    escapeRef.current = () => {
      if (pending) setPending(null)
      else if (editingId) setEditingId(null)
      else return false
      return true
    }
    return () => {
      escapeRef.current = null
    }
  }, [escapeRef, pending, editingId])

  function confirm() {
    const p = pending
    setPending(null)
    if (p?.kind === 'one') void forget(p.fact.id)
    if (p?.kind === 'topic') {
      if (p.named) void deleteTopic(p.section, p.name)
      else p.ids.forEach((id) => void forget(id))
      setOpen(null)
    }
  }

  // The filter decides which rows show; a row's preview, date and delete count are the whole topic.
  function whole(section: string, t: Topic): Topic {
    return topicsOf(all.bySection.get(section) ?? []).find((w) => w.key === t.key) ?? t
  }

  function askDelete(section: string, topic: Topic) {
    const t = whole(section, topic)
    setPending({
      kind: 'topic',
      name: t.name,
      section,
      ids: t.facts.map((f) => f.id),
      named: t.named,
    })
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
    ? topicsOf(all.bySection.get(open.section) ?? []).find((t) => t.key === open.key)
    : undefined
  const topicShown = Boolean(open && topic)
  useEffect(() => {
    onTopicOpen?.(topicShown)
  }, [onTopicOpen, topicShown])
  if (open && topic) {
    const summary = summaryOf(summaries, open.section, topic.key)
    return (
      <div className="rd-mem-detail" ref={top}>
        <button type="button" className="rd-mem-back" onClick={() => setOpen(null)}>
          <ChevronLeft size={16} strokeWidth={1.8} aria-hidden="true" />
          Memory
        </button>
        <div className="rd-mem-detail-head">
          <div className="rd-mem-detail-title">{topic.name}</div>
          <Btn size="xs" variant="danger" onClick={() => askDelete(open.section, topic)}>
            Delete
          </Btn>
        </div>
        <div className="rd-mem-field">
          <div className="rd-mem-field-label">Last updated</div>
          <div>{topic.updated ? fDate(topic.updated) : '—'}</div>
        </div>
        {summary && (
          <div className="rd-mem-field">
            <div className="rd-mem-field-label">Summary</div>
            <div>{summary}</div>
          </div>
        )}
        <div className="rd-mem-field">
          <div className="rd-mem-field-label">Details</div>
          {bullets(topic.facts)}
        </div>
        <MemoryAskBox
          disabled={disabled}
          placeholder={`Tell Lucius what to change about ${topic.name}`}
          onSubmit={(text) =>
            instruct(text, { section: open.section, topic: topic.named ? topic.name : undefined })
          }
        />
        {confirmNode}
      </div>
    )
  }

  const sections = [...SECTIONS, UNSORTED].filter((s) =>
    s.key === UNSORTED.key ? shown.unsorted.length : shown.bySection.get(s.key)?.length,
  )
  const nothingShown = sections.length === 0
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
      ) : nothingShown ? (
        <div className="rd-mem-empty">
          {needle
            ? `No memories match “${filter.trim()}”.`
            : 'Nothing remembered yet. Tell Lucius something below, or he’ll pick things up as you talk.'}
        </div>
      ) : (
        <>
          {sections.map((s) => {
            const list =
              s.key === UNSORTED.key ? shown.unsorted : (shown.bySection.get(s.key) ?? [])
            return (
              <section className="rd-mem-sec" key={s.key} aria-label={s.label}>
                <div className="rd-mem-sec-h">{s.label}</div>
                {s.flat
                  ? bullets(list)
                  : topicsOf(list).map((t) => (
                      <TopicRow
                        key={t.key}
                        topic={whole(s.key, t)}
                        onOpen={() => setOpen({ section: s.key, key: t.key })}
                        onDelete={() => askDelete(s.key, t)}
                      />
                    ))}
              </section>
            )
          })}
        </>
      )}
      <MemoryAskBox disabled={disabled} onSubmit={(text) => instruct(text)} />
      {confirmNode}
    </div>
  )
}
