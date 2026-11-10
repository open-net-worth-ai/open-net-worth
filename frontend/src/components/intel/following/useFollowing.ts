import { useEffect, useState } from 'react'
import { useQueryCache, type QueryCacheResult } from '../../../state/queryCache'
import { useDataVersion } from '../../../hooks/useDataVersion'
import {
  FOLLOWING_KEY,
  directoryKey,
  fetchDirectory,
  fetchFollowList,
  fetchPerformance,
  performanceKey,
  type DirectoryEntry,
  type FollowList,
  type FollowPerformance,
} from '../../../api/followedPortfolios'

const DEBOUNCE_MS = 250

export function useFollowList(): QueryCacheResult<FollowList> {
  const version = useDataVersion(['portfolio'])
  return useQueryCache(FOLLOWING_KEY, fetchFollowList, { version })
}

/** A search calls SEC and can take seconds, so typing settles before it is asked. */
export function useDirectory(typed: string): QueryCacheResult<DirectoryEntry[]> & { q: string } {
  const [q, setQ] = useState(typed.trim())
  useEffect(() => {
    const id = setTimeout(() => setQ(typed.trim()), DEBOUNCE_MS)
    return () => clearTimeout(id)
  }, [typed])
  const query = useQueryCache(directoryKey(q), () => fetchDirectory(q))
  return { ...query, q }
}

// One answer costs a price-history fetch per holding, and closes move once a day.
const PERFORMANCE_STALE_MS = 10 * 60 * 1000

export function usePerformance(id: string): QueryCacheResult<FollowPerformance> {
  return useQueryCache(performanceKey(id), () => fetchPerformance(id), {
    staleTime: PERFORMANCE_STALE_MS,
  })
}
