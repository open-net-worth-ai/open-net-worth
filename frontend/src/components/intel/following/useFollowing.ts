import { useEffect, useState } from 'react'
import { useQueryCache, type QueryCacheResult } from '../../../state/queryCache'
import { useDataVersion } from '../../../hooks/useDataVersion'
import {
  FOLLOWING_KEY,
  directoryKey,
  fetchDirectory,
  fetchFollowList,
  type DirectoryEntry,
  type FollowList,
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
