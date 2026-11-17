import { useQueryCache, type QueryCacheResult } from '../../../state/queryCache'
import { useDataVersion } from '../../../hooks/useDataVersion'
import {
  fetchPaperDefaults,
  fetchPaperList,
  fetchPaperPage,
  fetchPaperPerformance,
  fetchPaperPositions,
  paperDefaultsKey,
  paperListKey,
  paperPageKey,
  paperPerformanceKey,
  paperPositionsKey,
  type PaperDefaults,
  type PaperInAsset,
  type PaperList,
  type PaperPage,
  type PaperPerformance,
} from '../../../api/paperTrading'

/** The engine fills waiting orders on its own every minute; a screen showing open orders asks
 *  again this often so a fill appears without a reload. */
const OPEN_ORDERS_POLL_MS = 30_000

/** Lucius's paper writes arrive as the `paper` data domain. */
const usePaperVersion = () => useDataVersion(['paper'])

export function usePaperList(): QueryCacheResult<PaperList> {
  const version = usePaperVersion()
  return useQueryCache(paperListKey, fetchPaperList, { version })
}

export function usePaperPage(id: string, pollOpenOrders: boolean): QueryCacheResult<PaperPage> {
  const version = usePaperVersion()
  return useQueryCache(paperPageKey(id), () => fetchPaperPage(id), {
    version,
    refetchInterval: pollOpenOrders ? OPEN_ORDERS_POLL_MS : undefined,
  })
}

export function usePaperPerformance(id: string, range: string): QueryCacheResult<PaperPerformance> {
  const version = usePaperVersion()
  return useQueryCache(paperPerformanceKey(id, range), () => fetchPaperPerformance(id, range), {
    version,
  })
}

export function usePaperDefaults(enabled: boolean): QueryCacheResult<PaperDefaults> {
  return useQueryCache(paperDefaultsKey, fetchPaperDefaults, { enabled })
}

/** One instrument on paper — its positions and its trades — for the asset page. */
export function usePaperPositions(instrumentId: string | undefined, enabled: boolean) {
  const version = usePaperVersion()
  return useQueryCache<PaperInAsset>(
    paperPositionsKey(instrumentId ?? ''),
    () => fetchPaperPositions(instrumentId ?? ''),
    { version, enabled: enabled && !!instrumentId },
  )
}
