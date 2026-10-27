// How the stored facts are laid out: sections in a fixed order, topics inside them, and the facts
// the sort hasn't filed yet. Facts moved out of memory are kept 30 days but not shown.
import type { MemoryFact, MemoryTopicSummary } from '../../../hooks/useMemoryFacts'

// The fixed sections, in display order. `flat` sections list their facts directly (no topics).
export const SECTIONS: readonly { key: string; label: string; flat?: boolean; note?: string }[] = [
  { key: 'you', label: 'You' },
  { key: 'money', label: 'Money' },
  { key: 'plans', label: 'Plans' },
  { key: 'open_items', label: 'Open items', flat: true },
  { key: 'other', label: 'Other' },
]
// Facts saved before sections and topics existed, until the sort files them.
export const UNSORTED = {
  key: 'unsorted',
  label: 'Older memories',
  flat: true,
  note: 'Saved before memory had sections. Lucius is moving them into the new format in the background — filing each under a section and topic, or out of memory if it belongs elsewhere. You can still edit or forget them.',
}

// A topic name is stored at most this long (the backend's `TOPIC_MAX_CHARS`).
const TOPIC_MAX_CHARS = 60

/** What two spellings of one topic have in common, as the backend's `topic_key`: whitespace
 *  collapsed, cut to 60 characters, trimmed, case folded. */
export function topicKey(name?: string | null): string {
  return (name ?? '')
    .split(/\s+/)
    .filter(Boolean)
    .join(' ')
    .slice(0, TOPIC_MAX_CHARS)
    .trim()
    .toLowerCase()
}

/** A topic's generated summary line, matched by section and topic key (as the facts group), or
 *  '' when it has none yet. */
export function summaryOf(summaries: MemoryTopicSummary[], section: string, key: string): string {
  const hit = summaries.find((s) => s.section === section && topicKey(s.topic) === key)
  return hit?.summary.trim() ?? ''
}

/** The group key for facts filed with no topic. It has a leading space, which `topicKey` always
 *  trims, so no real topic name — not even "General" — can share it. */
export const NO_TOPIC_KEY = ' none'

export type Topic = {
  key: string
  name: string
  facts: MemoryFact[]
  updated: string
  named: boolean
}

export type Grouped = {
  bySection: Map<string, MemoryFact[]>
  unsorted: MemoryFact[]
}

function latest(facts: MemoryFact[]): string {
  return facts.reduce((m, f) => ((f.updated_at ?? '') > m ? (f.updated_at ?? '') : m), '')
}

/** As the backend's `is_expired`: a filed fact expires on its date; a legacy fact not yet filed
 *  only when it was `time_bound` (a `durable` one's stray date never meant an expiry). */
function isExpired(f: MemoryFact, today: string): boolean {
  if (!f.expires_at || f.expires_at.slice(0, 10) >= today) return false
  if (f.state === 'live' || f.section) return true
  return (f.tier ?? 'time_bound') === 'time_bound'
}

const localDay = (d = new Date()) => d.toLocaleDateString('en-CA')

export function groupFacts(facts: MemoryFact[], today = localDay()) {
  const known = new Set(SECTIONS.map((s) => s.key))
  const out: Grouped = { bySection: new Map(), unsorted: [] }
  for (const f of facts) {
    if (f.state === 'retired' || isExpired(f, today)) continue
    else if (f.section && known.has(f.section))
      out.bySection.set(f.section, [...(out.bySection.get(f.section) ?? []), f])
    else out.unsorted.push(f)
  }
  return out
}

export function topicsOf(facts: MemoryFact[]): Topic[] {
  const byKey = new Map<string, { name: string; facts: MemoryFact[] }>()
  for (const f of facts) {
    const key = topicKey(f.topic) || NO_TOPIC_KEY
    const group = byKey.get(key) ?? { name: (f.topic ?? '').trim() || 'General', facts: [] }
    group.facts.push(f)
    byKey.set(key, group)
  }
  return [...byKey.entries()]
    .map(([key, g]) => ({
      key,
      name: g.name,
      facts: g.facts,
      updated: latest(g.facts),
      named: g.facts.some((f) => topicKey(f.topic) !== ''),
    }))
    .sort((a, b) => (a.updated < b.updated ? 1 : -1))
}

/** Case-insensitive: a fact matches by its text or its topic's name. */
export function matchesFilter(f: MemoryFact, needle: string): boolean {
  return (f.content + ' ' + (f.topic ?? '')).toLowerCase().includes(needle)
}
