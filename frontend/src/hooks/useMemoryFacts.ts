import { authFetch } from '../api/client'
import { useCachedFetchState } from '../state/queryCache'

export interface MemoryFact {
  id: string
  content: string
  source?: string
  tier?: string
  updated_at?: string
  created_at?: string
}

export const MEMORY_FACTS_KEY = '/api/memory/facts'
export const MEMORY_SETTINGS_KEY = '/api/memory/settings'

export async function getMemorySettings(): Promise<{ enabled: boolean }> {
  const r = await authFetch(MEMORY_SETTINGS_KEY)
  if (!r.ok) throw new Error(String(r.status))
  return r.json() as Promise<{ enabled: boolean }>
}

async function getFacts(): Promise<{ facts: MemoryFact[]; total: number }> {
  const r = await authFetch(MEMORY_FACTS_KEY)
  if (!r.ok) throw new Error(String(r.status))
  return r.json() as Promise<{ facts: MemoryFact[]; total: number }>
}

/** The stored-memory list and its edits and deletes, used by the one stored-memories dialog
 *  (components/tabs/settings/MemoryDialog). `notify` gets one short line per outcome. */
export function useMemoryFacts(notify: (message: string) => void) {
  const { data, refresh } = useCachedFetchState(MEMORY_FACTS_KEY, getFacts, {
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
      void refresh()
      notify('Saved')
      return true
    } catch {
      notify('Could not reach the server. Try again.')
      return false
    }
  }

  /** "Tell Lucius what to change" — one sentence that adds, rewrites or forgets facts. True
   *  when the server applied it (even if nothing needed changing). */
  async function instruct(instruction: string): Promise<boolean> {
    try {
      const r = await authFetch('/api/memory/instruct', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ instruction }),
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
    facts: data?.facts ?? [],
    forget: (id: string) => remove(`${MEMORY_FACTS_KEY}/${encodeURIComponent(id)}`, 'Forgotten'),
    forgetAll: () => remove(MEMORY_FACTS_KEY, 'All memories forgotten'),
  }
}

interface InstructResult {
  added: number
  updated: number
  removed: number
}

export function instructSummary(r: Partial<InstructResult>): string {
  const parts = [
    r.added ? `Added ${r.added}` : '',
    r.updated ? `Updated ${r.updated}` : '',
    r.removed ? `Forgot ${r.removed}` : '',
  ].filter(Boolean)
  return parts.length ? parts.join(' · ') : 'Nothing needed changing'
}

export function memorySourceLabel(source?: string): string {
  return source === 'explicit' ? 'You told Lucius' : 'Learned in chat'
}
