import { useEffect, useState } from 'react'
import { useQueryCache, type QueryCacheResult } from '../../../state/queryCache'
import { useDataVersion } from '../../../hooks/useDataVersion'
import {
  FOLLOWING_KEY,
  directoryKey,
  fetchDirectory,
  fetchFollowList,
  fetchPerformance,
  fetchPreviewPage,
  fetchPreviewReturns,
  fetchPreviewSummary,
  fetchPriceHistory,
  performanceKey,
  previewPageKey,
  previewReturnsKey,
  previewSummaryKey,
  priceHistoryKey,
  type DirectoryEntry,
  type FollowKind,
  type FollowList,
  type FollowPerformance,
  type PreviewPage,
  type PreviewSummary,
  type PriceHistory,
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

// A preview is fetched and priced server-side and kept for hours; the screen need not ask often.
const PREVIEW_STALE_MS = 15 * 60 * 1000

export function usePreviewSummary(kind: FollowKind, ref: string): QueryCacheResult<PreviewSummary> {
  return useQueryCache(previewSummaryKey(kind, ref), () => fetchPreviewSummary(kind, ref), {
    staleTime: PREVIEW_STALE_MS,
  })
}

export function usePreviewPage(kind: FollowKind, ref: string): QueryCacheResult<PreviewPage> {
  const version = useDataVersion(['portfolio'])
  return useQueryCache(previewPageKey(kind, ref), () => fetchPreviewPage(kind, ref), {
    staleTime: PREVIEW_STALE_MS,
    version,
  })
}

/** The page's every-holding returns, asked for once the page itself has arrived (the page
 *  opens on the copy's line; each row's own return fills in when this lands). */
export function usePreviewReturns(
  kind: FollowKind,
  ref: string,
  ready: boolean,
): QueryCacheResult<FollowPerformance> {
  const version = useDataVersion(['portfolio'])
  return useQueryCache(previewReturnsKey(kind, ref), () => fetchPreviewReturns(kind, ref), {
    staleTime: PREVIEW_STALE_MS,
    version,
    enabled: ready,
  })
}

export function usePriceHistory(symbol: string): QueryCacheResult<PriceHistory> {
  return useQueryCache(priceHistoryKey(symbol), () => fetchPriceHistory(symbol), {
    staleTime: PERFORMANCE_STALE_MS,
  })
}

/** The stock's and the benchmark's move from the day it became public to the newest close. */
export function useSinceFiled(symbol: string, benchmark: string, filedAt: string) {
  const s = usePriceHistory(symbol).data?.points ?? []
  const b = usePriceHistory(benchmark).data?.points ?? []
  const move = (pts: { date: string; price: number }[]) => {
    const start = [...pts].reverse().find((p) => p.date <= filedAt)
    const end = pts[pts.length - 1]
    // No price after the filing (a delisted or renamed stock) is no move, not a 0% one.
    return start && end && end.date > start.date ? end.price / start.price - 1 : null
  }
  const stock = move(s)
  return { price: stock == null ? null : s[s.length - 1]!.price, stock, bench: move(b) }
}
