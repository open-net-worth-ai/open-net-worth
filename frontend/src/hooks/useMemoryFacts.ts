import { authFetch } from '../api/client'
import { useCachedFetchState } from '../state/queryCache'

export interface MemoryFact {
  id: string
  content: string
  source?: string
  tier?: string
  /** `live` (filed), `unsorted` (waiting for the sort) or `retired` (moved out, kept 30 days).
   *  Section key (you, money, plans, open_items, other) and topic name — the memory redesign,
   *  ADR 2026-10-25-memory-keeps-only-what-has-no-other-home amendment 4. Absent on legacy facts. */
  state?: string
  section?: string | null
  topic?: string | null
  retired_reason?: string | null
  retired_at?: string | null
  expires_at?: string | null
  last_used_at?: string | null
  updated_at?: string
  created_at?: string
}

export interface MemorySettings {
  enabled: boolean
  search_chats: boolean
  sensitive_topics: boolean
}

export const MEMORY_FACTS_KEY = '/api/memory/facts'
export const MEMORY_SETTINGS_KEY = '/api/memory/settings'

export async function getMemorySettings(): Promise<MemorySettings> {
  const r = await authFetch(MEMORY_SETTINGS_KEY)
  if (!r.ok) throw new Error(String(r.status))
  return r.json() as Promise<MemorySettings>
}

const REJECTED_REASONS: Record<string, string> = {
  empty: 'A memory can’t be empty.',
  too_long: 'That memory is too long.',
  bad_section: 'That section doesn’t exist.',
  bad_expiry: 'That date has passed.',
  ledger_figure: 'Balances, prices and share counts are read live, so they aren’t remembered.',
  identifier: 'Full card, account or ID numbers and passwords are never remembered.',
  sensitive_off: 'Sensitive topics are off. Turn them on in Settings → Memory to save this.',
  unknown_id: 'That memory no longer exists.',
}

/** A sentence for the first thing the server refused in a write, or '' when nothing was. */
export function rejectedNote(body: { rejected?: { reason?: string }[] }): string {
  const first = body.rejected?.[0]
  if (!first) return ''
  return REJECTED_REASONS[first.reason ?? ''] ?? 'Part of that couldn’t be remembered.'
}

async function getFacts(): Promise<{ facts: MemoryFact[]; total: number }> {
  const r = await authFetch(MEMORY_FACTS_KEY)
  if (!r.ok) throw new Error(String(r.status))
  return r.json() as Promise<{ facts: MemoryFact[]; total: number }>
}

/** The stored-memory list and its edits and deletes, used by the one memory browser
 *  (components/tabs/settings/MemoryBrowser). `notify` gets one short line per outcome. */
export function useMemoryFacts(notify: (message: string) => void) {
  const { data, refresh, initialLoading } = useCachedFetchState(MEMORY_FACTS_KEY, getFacts, {
    staleTime: 0,
  })

  async function remove(path: string, done: string) {
    try {
      const r = await authFetch(path, { method: 'DELETE' })
      if (!r.ok) {
        notify(`Could not forget (HTTP ${r.status})`)
        return
      }
      void refresh()
      notify(done)
    } catch {
      notify('Could not reach the server. Try again.')
    }
  }

  /** Rewrite one fact's text as typed. True when saved. */
  async function edit(id: string, content: string): Promise<boolean> {
    try {
      const r = await authFetch(`${MEMORY_FACTS_KEY}/${encodeURIComponent(id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      })
      if (!r.ok) {
        const body = (await r.json().catch(() => ({}))) as { detail?: unknown }
        notify(typeof body.detail === 'string' ? body.detail : `Could not save (HTTP ${r.status})`)
        return false
      }
      const saved = (await r.json().catch(() => ({}))) as { rejected?: { reason?: string }[] }
      void refresh()
      notify(rejectedNote(saved) || 'Saved')
      return true
    } catch {
      notify('Could not reach the server. Try again.')
      return false
    }
  }

  /** Forget every fact filed under one topic. */
  function deleteTopic(section: string, topic: string) {
    const query = new URLSearchParams({ section, topic }).toString()
    return remove(`/api/memory/topics?${query}`, 'Forgotten')
  }

  /** "Tell Lucius what to change" — one sentence that adds, rewrites or forgets facts, scoped to
   *  `topic` when given. True when the server applied it (even if nothing needed changing). */
  async function instruct(instruction: string, topic?: string): Promise<boolean> {
    try {
      const r = await authFetch('/api/memory/instruct', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(topic ? { instruction, topic } : { instruction }),
      })
      const body = (await r.json().catch(() => ({}))) as Partial<InstructResult> & {
        detail?: string
      }
      if (!r.ok) {
        notify(
          typeof body.detail === 'string' ? body.detail : `Could not update (HTTP ${r.status})`,
        )
        return false
      }
      void refresh()
      notify(instructSummary(body))
      return true
    } catch {
      notify('Could not reach the server. Try again.')
      return false
    }
  }

  return {
    edit,
    instruct,
    deleteTopic,
    facts: data?.facts ?? [],
    loading: initialLoading,
    forget: (id: string) => remove(`${MEMORY_FACTS_KEY}/${encodeURIComponent(id)}`, 'Forgotten'),
    forgetAll: () => remove(MEMORY_FACTS_KEY, 'All memories forgotten'),
  }
}

interface InstructResult {
  added: number
  updated: number
  removed: number
  rejected: { reason?: string }[]
}

export function instructSummary(r: Partial<InstructResult>): string {
  const parts = [
    r.added ? `Added ${r.added}` : '',
    r.updated ? `Updated ${r.updated}` : '',
    r.removed ? `Forgot ${r.removed}` : '',
  ].filter(Boolean)
  const note = rejectedNote(r)
  if (note) parts.push(note)
  return parts.length ? parts.join(' · ') : 'Nothing needed changing'
}

export function memorySourceLabel(source?: string): string {
  return source === 'explicit' ? 'You told Lucius' : 'Learned in chat'
}
