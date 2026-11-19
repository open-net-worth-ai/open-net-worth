/** The tags already in use, and the options a tag picker offers for a query. */

import { useCallback } from 'react'
import { authFetch } from '../../../api/client'
import { useDataVersion } from '../../../hooks/useDataVersion'
import { useQueryCache } from '../../../state/queryCache'
import { slugTag } from './categories'

export const TAGS_URL = '/api/transactions/tags'

interface TagUsage {
  tag: string
  count: number
}

/** The tags already on live transactions, most used first. Refreshes on any write. */
export function useTransactionTags(): string[] {
  const anyWriteV = useDataVersion('*')
  const fetchTags = useCallback(async () => {
    const r = await authFetch(TAGS_URL)
    if (!r.ok) throw new Error(await r.text())
    const body = (await r.json()) as { tags?: TagUsage[] }
    return (body.tags ?? []).map((t) => t.tag)
  }, [])
  return useQueryCache(TAGS_URL, fetchTags, { version: anyWriteV }).data ?? []
}

export type TagItem = { type: 'tag' | 'create'; tag: string }

/** Pure: the options for a query — known tags not yet chosen that contain it, then "Create" when
 *  the typed name is new. Exported for tests. */
export function tagItems(known: string[], chosen: string[], query: string): TagItem[] {
  const q = slugTag(query)
  const taken = new Set(chosen)
  const matches = known.filter((t) => !taken.has(t) && (!q || t.includes(q)))
  const items: TagItem[] = matches.map((tag) => ({ type: 'tag', tag }))
  if (q && !taken.has(q) && !known.includes(q)) items.push({ type: 'create', tag: q })
  return items
}
