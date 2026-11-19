// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { emitDataChanged } from '../lucius/bus'
import {
  clearQueryCache,
  getCachedQueryData,
  invalidateQueryCache,
  setCachedQueryData,
  useQueryCache,
} from './queryCache'

describe('query cache live refresh', () => {
  beforeEach(() => clearQueryCache())

  it('revalidates every mounted read on a Lucius write, whatever domain it names', async () => {
    let n = 0
    const fetcher = vi.fn(async () => `v${++n}`)
    const view = renderHook(() => useQueryCache('/api/commitments', fetcher, { staleTime: 60_000 }))
    await waitFor(() => expect(view.result.current.data).toBe('v1'))

    // No key→domain map: a domain the key "doesn't know about" still refreshes it.
    act(() => emitDataChanged(['automations']))

    await waitFor(() => expect(view.result.current.data).toBe('v2'))
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('marks unmounted reads stale so the next mount refetches instead of trusting staleTime', async () => {
    const fetcher = vi.fn(async () => 'fresh')
    setCachedQueryData('/api/payees', 'old')

    act(() => emitDataChanged(['payees']))
    expect(getCachedQueryData('/api/payees')).toBe('old')

    const view = renderHook(() => useQueryCache('/api/payees', fetcher, { staleTime: 60_000 }))
    expect(view.result.current.data).toBe('old')
    await waitFor(() => expect(view.result.current.data).toBe('fresh'))
  })

  it('leaves a liveRefresh:false read alone on a write, but an explicit invalidation still reloads it', async () => {
    let n = 0
    const fetcher = vi.fn(async () => `v${++n}`)
    const view = renderHook(() =>
      useQueryCache('/api/priced-read', fetcher, { staleTime: 60_000, liveRefresh: false }),
    )
    await waitFor(() => expect(view.result.current.data).toBe('v1'))

    act(() => emitDataChanged(['anything']))
    expect(fetcher).toHaveBeenCalledTimes(1)

    act(() => invalidateQueryCache('/api/priced'))
    await waitFor(() => expect(view.result.current.data).toBe('v2'))
  })

  it("derives refetchInterval from the read's own data", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      let n = 0
      const fetcher = vi.fn(async () => ({ moving: n++ < 1 }))
      const view = renderHook(() =>
        useQueryCache('/api/own-interval', fetcher, {
          refetchInterval: (d: { moving: boolean } | undefined) => (d?.moving ? 1000 : undefined),
        }),
      )
      await waitFor(() => expect(view.result.current.data).toEqual({ moving: true }))
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1100)
      })
      await waitFor(() => expect(view.result.current.data).toEqual({ moving: false }))
      const calls = fetcher.mock.calls.length
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000)
      })
      expect(fetcher).toHaveBeenCalledTimes(calls)
    } finally {
      vi.useRealTimers()
    }
  })
})
