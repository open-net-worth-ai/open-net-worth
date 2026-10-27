// How the stored facts are laid out: sections in a fixed order, topics inside them, the facts the
// sort hasn't filed yet, and the ones moved out of memory (kept 30 days, then forgotten).
import type { MemoryFact } from '../../../hooks/useMemoryFacts'

// The fixed sections, in display order. `flat` sections list their facts directly (no topics).
export const SECTIONS: readonly { key: string; label: string; flat?: boolean }[] = [
  { key: 'you', label: 'You' },
  { key: 'money', label: 'Money' },
  { key: 'plans', label: 'Plans' },
  { key: 'open_items', label: 'Open items', flat: true },
  { key: 'other', label: 'Other' },
]
// Facts saved before sections and topics existed, until the sort files them.
export const UNSORTED = { key: 'unsorted', label: 'Older memories — being sorted', flat: true }

const RETIRED_KEEP_DAYS = 30
const DAY_MS = 86_400_000

const REASONS: Record<string, string> = {
  profile: 'Moved to your profile',
  instructions: 'Moved to Instructions for Lucius',
  payee_rule: 'Now a payee rule',
  automation: 'Now an automation',
  ledger: 'Already in your ledger',
  resolved: 'Done',
  stale: 'Out of date',
  unused: 'Not used in 90 days',
}

export function retiredReason(reason?: string | null): string {
  return REASONS[reason ?? ''] ?? 'Out of date'
}

const localDay = (d = new Date()) => d.toLocaleDateString('en-CA')

/** Whole calendar days until a moved-out fact is forgotten for good (30 less the days since it
 *  moved, as the nightly purge counts them), or null when it carries no date. */
export function daysUntilForgotten(retiredAt?: string | null, today = localDay()): number | null {
  const at = Date.parse((retiredAt ?? '').slice(0, 10))
  const now = Date.parse(today)
  if (Number.isNaN(at) || Number.isNaN(now)) return null
  return Math.max(0, RETIRED_KEEP_DAYS - Math.round((now - at) / DAY_MS))
}

/** What two spellings of one topic have in common, as the backend's `topic_key`. */
export function topicKey(name?: string | null): string {
  return (name ?? '').split(/\s+/).filter(Boolean).join(' ').toLowerCase()
}

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
  retired: MemoryFact[]
}

function latest(facts: MemoryFact[]): string {
  return facts.reduce((m, f) => ((f.updated_at ?? '') > m ? (f.updated_at ?? '') : m), '')
}

function isExpired(f: MemoryFact, today: string): boolean {
  return !!f.expires_at && f.expires_at.slice(0, 10) < today
}

export function groupFacts(facts: MemoryFact[], today = localDay()) {
  const known = new Set(SECTIONS.map((s) => s.key))
  const out: Grouped = { bySection: new Map(), unsorted: [], retired: [] }
  for (const f of facts) {
    if (f.state === 'retired') out.retired.push(f)
    else if (isExpired(f, today)) continue
    else if (f.section && known.has(f.section))
      out.bySection.set(f.section, [...(out.bySection.get(f.section) ?? []), f])
    else out.unsorted.push(f)
  }
  out.retired.sort((a, b) => ((a.retired_at ?? '') < (b.retired_at ?? '') ? 1 : -1))
  return out
}

export function topicsOf(facts: MemoryFact[]): Topic[] {
  const byKey = new Map<string, { name: string; facts: MemoryFact[] }>()
  for (const f of facts) {
    const key = topicKey(f.topic) || 'general'
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
