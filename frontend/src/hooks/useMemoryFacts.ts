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

async function getFacts(): Promise<{ facts: MemoryFact[]; total: number }> {
  const r = await authFetch(MEMORY_FACTS_KEY)
  if (!r.ok) throw new Error(String(r.status))
  return r.json() as Promise<{ facts: MemoryFact[]; total: number }>
}

/** The stored-memory list and its two deletes, shared by Settings → Chat & Memory and the chat
 *  header's Memory dialog. `notify` gets one short line per outcome. */
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

  return {
    facts: data?.facts ?? [],
    loading: initialLoading,
    forget: (id: string) => remove(`${MEMORY_FACTS_KEY}/${encodeURIComponent(id)}`, 'Forgotten'),
    forgetAll: () => remove(MEMORY_FACTS_KEY, 'All memories forgotten'),
  }
}

export function memorySourceLabel(source?: string): string {
  return source === 'explicit' ? 'You told Lucius' : 'Learned in chat'
}
