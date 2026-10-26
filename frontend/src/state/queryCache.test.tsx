// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  QUERY_CACHE_GC_MS,
  clearQueryCache,
  getCachedQueryData,
  queryCacheSize,
  setCachedQueryData,
  useQueryCache,
} from './queryCache'

describe('useQueryCache', () => {
  beforeEach(() => clearQueryCache())

  it('returns last-good data on a second mount while revalidating in the background', async () => {
    let resolveRefresh!: (value: string) => void
    const fetcher = vi
      .fn<() => Promise<string>>()
      .mockResolvedValueOnce('first')
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveRefresh = resolve
          }),
      )

    const first = renderHook(() => useQueryCache('example', fetcher, { staleTime: 0 }))
    await waitFor(() => expect(first.result.current.data).toBe('first'))
    first.unmount()

    const second = renderHook(() => useQueryCache('example', fetcher, { staleTime: 0 }))
    expect(second.result.current.data).toBe('first')
    expect(second.result.current.initialLoading).toBe(false)
    expect(second.result.current.refreshing).toBe(true)

    await act(async () => resolveRefresh('second'))
    await waitFor(() => expect(second.result.current.data).toBe('second'))
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('keeps query parameters in the key so filtered results cannot cross-contaminate', async () => {
    const all = renderHook(() =>
      useQueryCache('/api/transactions?account_id=all', () => Promise.resolve(['all']), {
        staleTime: 60_000,
      }),
    )
    await waitFor(() => expect(all.result.current.data).toEqual(['all']))
    all.unmount()

    const filtered = renderHook(() =>
      useQueryCache('/api/transactions?account_id=acct_1', () => Promise.resolve(['acct_1']), {
        staleTime: 60_000,
      }),
    )
    expect(filtered.result.current.data).toBeUndefined()
    expect(filtered.result.current.initialLoading).toBe(true)
    await waitFor(() => expect(filtered.result.current.data).toEqual(['acct_1']))
  })

  it('preserves last-good data when a background refresh fails', async () => {
    const fetcher = vi
      .fn<() => Promise<string>>()
      .mockResolvedValueOnce('good')
      .mockRejectedValueOnce(new Error('offline'))

    const first = renderHook(() => useQueryCache('example', fetcher, { staleTime: 0 }))
    await waitFor(() => expect(first.result.current.data).toBe('good'))
    first.unmount()

    const second = renderHook(() => useQueryCache('example', fetcher, { staleTime: 0 }))
    expect(second.result.current.data).toBe('good')
    await waitFor(() => expect(second.result.current.refreshing).toBe(false))
    expect(second.result.current.data).toBe('good')
    expect(second.result.current.error).toEqual(new Error('offline'))
  })

  it('publishes mutation write-through to mounted consumers', async () => {
    const query = renderHook(() =>
      useQueryCache('profile', () => Promise.resolve({ currency: 'SGD' })),
    )
    await waitFor(() => expect(query.result.current.data).toEqual({ currency: 'SGD' }))

    act(() => setCachedQueryData('profile', { currency: 'USD' }))

    expect(query.result.current.data).toEqual({ currency: 'USD' })
  })

  it('patches cached data so a remount cannot resurrect a successfully removed row', async () => {
    const first = renderHook(() =>
      useQueryCache('agents:roster', () => Promise.resolve([{ id: 'a' }, { id: 'b' }])),
    )
    await waitFor(() => expect(first.result.current.data).toHaveLength(2))

    act(() =>
      setCachedQueryData<{ id: string }[]>('agents:roster', (rows) =>
        (rows ?? []).filter((row) => row.id !== 'a'),
      ),
    )
    expect(first.result.current.data).toEqual([{ id: 'b' }])
    first.unmount()

    const second = renderHook(() =>
      useQueryCache('agents:roster', () => new Promise<{ id: string }[]>(() => {}), {
        staleTime: 0,
      }),
    )
    expect(second.result.current.data).toEqual([{ id: 'b' }])
    expect(second.result.current.initialLoading).toBe(false)
  })

  it('updates optimistic data without replacing the registered revalidation fetcher', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce('server').mockResolvedValueOnce('fresh')
    const rendered = renderHook(() => useQueryCache('/api/item', fetcher, { staleTime: 60_000 }))
    await waitFor(() => expect(rendered.result.current.data).toBe('server'))

    act(() => setCachedQueryData('/api/item', 'optimistic'))
    expect(rendered.result.current.data).toBe('optimistic')
    await act(() => rendered.result.current.refresh())
    expect(rendered.result.current.data).toBe('fresh')
  })

  // A key that failed with no prior data used to report initialLoading forever (it was
  // derived from `data === undefined` alone), so a consumer showed its spinner instead of
  // its error state and never recovered.
  it('reports an errored first load as not-loading so the error state can render', async () => {
    const fetcher = vi.fn<() => Promise<string>>().mockRejectedValue(new Error('boom'))
    const rendered = renderHook(() => useQueryCache('/api/broken', fetcher, { staleTime: 0 }))

    await waitFor(() => expect(rendered.result.current.error).toEqual(new Error('boom')))
    expect(rendered.result.current.data).toBeUndefined()
    expect(rendered.result.current.initialLoading).toBe(false)
    expect(rendered.result.current.refreshing).toBe(false)
  })
})

describe('useQueryCache eviction', () => {
  beforeEach(() => {
    clearQueryCache()
    vi.useFakeTimers()
  })
  afterEach(() => vi.useRealTimers())

  it('evicts an entry once it has been listener-less for the grace period', async () => {
    const rendered = renderHook(() =>
      useQueryCache('/api/evict-me', () => Promise.resolve('v'), { staleTime: 60_000 }),
    )
    await act(async () => {})
    expect(getCachedQueryData('/api/evict-me')).toBe('v')

    rendered.unmount()
    // Still retained immediately after unmount — a remount within the window is free.
    expect(queryCacheSize()).toBe(1)

    await act(async () => {
      vi.advanceTimersByTime(QUERY_CACHE_GC_MS + 1)
    })
    expect(queryCacheSize()).toBe(0)
    expect(getCachedQueryData('/api/evict-me')).toBeUndefined()
  })

  it('cancels eviction when a component remounts inside the grace period', async () => {
    // staleTime far exceeds the GC window so advancing the clock cannot trigger a
    // revalidation — this test is about eviction only, not staleness.
    const staleTime = QUERY_CACHE_GC_MS * 10
    const first = renderHook(() =>
      useQueryCache('/api/keep-me', () => Promise.resolve('v'), { staleTime }),
    )
    await act(async () => {})
    first.unmount()

    await act(async () => {
      vi.advanceTimersByTime(QUERY_CACHE_GC_MS / 2)
    })
    const second = renderHook(() =>
      useQueryCache('/api/keep-me', () => Promise.resolve('v2'), { staleTime }),
    )
    // Remount serves the cached value immediately, and the pending eviction is cancelled.
    expect(second.result.current.data).toBe('v')

    await act(async () => {
      vi.advanceTimersByTime(QUERY_CACHE_GC_MS + 1)
    })
    expect(queryCacheSize()).toBe(1)
    expect(getCachedQueryData('/api/keep-me')).toBe('v')
  })

  it('does not evict while a fetch is still in flight', async () => {
    let resolve!: (v: string) => void
    const rendered = renderHook(() =>
      useQueryCache('/api/inflight', () => new Promise<string>((r) => (resolve = r)), {
        staleTime: 0,
      }),
    )
    rendered.unmount()

    await act(async () => {
      vi.advanceTimersByTime(QUERY_CACHE_GC_MS + 1)
    })
    // Eviction deferred: dropping this entry would strand the pending request.
    expect(queryCacheSize()).toBe(1)

    await act(async () => {
      resolve('done')
    })
    await act(async () => {
      vi.advanceTimersByTime(QUERY_CACHE_GC_MS + 1)
    })
    expect(queryCacheSize()).toBe(0)
  })
})

describe('fetchCachedQuery (one-shot callers)', () => {
  beforeEach(() => clearQueryCache())

  it('awaits FRESH data when the entry is stale — never resolves with one-visit-behind rows', async () => {
    // Regression (owner report 2026-08-21): the Transactions tab showed old data on open.
    // A one-shot caller takes the resolved value into its own setState and is not
    // subscribed to the cache, so the old "return stale + refresh in background" contract
    // delivered the refresh to a cache nobody re-read — every fetchCachedQuery screen ran
    // permanently one visit behind (one SESSION behind once the localStorage mirror landed).
    const { fetchCachedQuery } = await import('./queryCache')
    const fetcher = vi
      .fn<() => Promise<string>>()
      .mockResolvedValueOnce('rows-v1')
      .mockResolvedValueOnce('rows-v2')

    expect(await fetchCachedQuery('txns', fetcher)).toBe('rows-v1')
    // Default staleTime 0 ⇒ the entry is immediately stale: the next open must WAIT for
    // the refetch and hand back v2, not instantly resolve with v1.
    expect(await fetchCachedQuery('txns', fetcher)).toBe('rows-v2')
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('returns the cached value without fetching inside an explicit staleTime window', async () => {
    const { fetchCachedQuery } = await import('./queryCache')
    const fetcher = vi.fn<() => Promise<string>>().mockResolvedValue('rows-v1')

    expect(await fetchCachedQuery('txns2', fetcher, { staleTime: 60_000 })).toBe('rows-v1')
    expect(await fetchCachedQuery('txns2', fetcher, { staleTime: 60_000 })).toBe('rows-v1')
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('collapses concurrent one-shots for the same key into one request', async () => {
    const { fetchCachedQuery } = await import('./queryCache')
    let resolve!: (v: string) => void
    const fetcher = vi.fn<() => Promise<string>>().mockImplementation(
      () =>
        new Promise((r) => {
          resolve = r
        }),
    )

    const a = fetchCachedQuery('txns3', fetcher)
    const b = fetchCachedQuery('txns3', fetcher)
    resolve('rows-v1')
    expect(await a).toBe('rows-v1')
    expect(await b).toBe('rows-v1')
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  // Regression (owner report 2026-10-26): a txn's cleared box toggled five times in five
  // seconds. The reload after the last toggle was forced, but joined the accounts GET already
  // in flight from the toggle BEFORE it — so the header kept the pre-toggle cleared balance,
  // Reconcile asked about that figure, and "Yes, it matches" booked a phantom adjustment.
  function deferredFetcher() {
    const pending: Array<(v: string) => void> = []
    const fetcher = vi
      .fn<() => Promise<string>>()
      .mockImplementation(() => new Promise((r) => pending.push(r)))
    return { fetcher, pending }
  }

  it('a forced fetch never joins a request that started before it', async () => {
    const { fetchCachedQuery } = await import('./queryCache')
    const { fetcher, pending } = deferredFetcher()

    const before = fetchCachedQuery('accts', fetcher, { force: true })
    const after = fetchCachedQuery('accts', fetcher, { force: true })
    expect(fetcher).toHaveBeenCalledTimes(2)

    pending[1]('post-write')
    pending[0]('pre-write')
    expect(await after).toBe('post-write')
    expect(await before).toBe('post-write')
    expect(getCachedQueryData('accts')).toBe('post-write')
  })

  it('a superseded request that lands LAST resolves with the newest data, not its own', async () => {
    const { fetchCachedQuery } = await import('./queryCache')
    const { fetcher, pending } = deferredFetcher()

    const before = fetchCachedQuery('accts2', fetcher, { force: true })
    const after = fetchCachedQuery('accts2', fetcher, { force: true })

    pending[1]('post-write')
    expect(await after).toBe('post-write')
    pending[0]('pre-write')
    // Its awaiter applies whatever it resolves with to its own state, AFTER `after`'s
    // awaiter did — so handing back its own stale body would win the race.
    expect(await before).toBe('post-write')
    expect(getCachedQueryData('accts2')).toBe('post-write')
  })

  it('a superseded request still in flight follows the newer one', async () => {
    const { fetchCachedQuery } = await import('./queryCache')
    const { fetcher, pending } = deferredFetcher()

    const before = fetchCachedQuery('accts3', fetcher, { force: true })
    const after = fetchCachedQuery('accts3', fetcher, { force: true })

    pending[0]('pre-write')
    pending[1]('post-write')
    expect(await before).toBe('post-write')
    expect(await after).toBe('post-write')
  })
})
