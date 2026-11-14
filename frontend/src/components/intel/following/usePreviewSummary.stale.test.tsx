// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { clearQueryCache } from '../../../state/queryCache'

const fetchPreviewSummary = vi.hoisted(() => vi.fn())
vi.mock('../../../api/followedPortfolios', async (orig) => ({
  ...(await orig<typeof import('../../../api/followedPortfolios')>()),
  fetchPreviewSummary,
}))

import { usePreviewSummary } from './useFollowing'
import { fund, summary } from './followingTestKit'

describe('a card shown from its last figures', () => {
  beforeEach(() => {
    clearQueryCache()
    fetchPreviewSummary.mockReset()
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })
  afterEach(() => vi.useRealTimers())

  it('asks again until the fresh figures arrive, then stops asking', async () => {
    fetchPreviewSummary
      .mockResolvedValueOnce({ ...summary(fund()), stale: true, return_12m: 0.1 })
      .mockResolvedValue({ ...summary(fund()), return_12m: 0.2 })
    const { result } = renderHook(() => usePreviewSummary('fund_13f', '0000000001'))
    await waitFor(() => expect(result.current.data?.stale).toBe(true))
    expect(result.current.data?.return_12m).toBe(0.1)
    // Not at once: the server is still making the fresh card.
    expect(fetchPreviewSummary).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(5000)
    await waitFor(() => expect(result.current.data?.return_12m).toBe(0.2))
    expect(result.current.data?.stale).toBeUndefined()
    const asked = fetchPreviewSummary.mock.calls.length
    await vi.advanceTimersByTimeAsync(20_000)
    expect(fetchPreviewSummary.mock.calls.length).toBe(asked)
  })
})
