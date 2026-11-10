// useOutlookFire.test.tsx — regression: net worth resolving AFTER mount must not let the
// Outlook seed/settle off the 0 placeholder in between. Before the fix, `totalLoading` didn't
// exist: a mount with `total=0` (net worth still loading upstream) fetched and settled
// /api/fire?current_nw=0 immediately, flipping `loading` false and letting the Outlook run a
// real Monte-Carlo pass — and show a real-looking odds % — off a net worth of $0, which then
// jumped once the real net worth landed.
// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { clearQueryCache, fetchCachedQuery } from '../../../state/queryCache'
import { useOutlookFire } from './useOutlookFire'

const authFetch = vi.fn<typeof fetch>()
vi.mock('../../../api/client', () => ({
  authFetch: (...args: Parameters<typeof fetch>) => authFetch(...args),
}))

const fireRow = (netWorth: number) => ({
  current_age: 38,
  retirement_target_age: 55,
  monthly_retirement_spending: 6000,
  withdrawal_rate_pct: 4,
  expected_return_pct: 6.5,
  volatility_pct: 14,
  monthly_savings: 4200,
  life_expectancy: 90,
  fire_number: 1800000,
  progress_pct: netWorth > 0 ? 50 : 0,
  years_to_fire: 10,
  projected_fire_age: 48,
  monthly_savings_needed: 0,
  on_track: true,
  retirement_duration_years: 35,
})

const response = (body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status: 200 }))

describe('useOutlookFire', () => {
  beforeEach(() => {
    clearQueryCache()
    localStorage.clear()
    authFetch.mockReset()
  })

  it('stays loading — and never fetches current_nw=0 — while net worth is still resolving', async () => {
    authFetch.mockImplementation((input) => response(fireRow(Number(String(input).split('=')[1]))))
    const { result, rerender } = renderHook(
      ({ total, totalLoading }) => useOutlookFire(total, 1, totalLoading),
      { initialProps: { total: 0, totalLoading: true } },
    )
    expect(result.current.loading).toBe(true)
    expect(authFetch).not.toHaveBeenCalled()

    // Net worth resolves to its real, non-zero value.
    rerender({ total: 500000, totalLoading: false })
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(authFetch).toHaveBeenCalledWith('/api/fire?current_nw=500000')
    expect(authFetch).not.toHaveBeenCalledWith('/api/fire?current_nw=0')
    expect(result.current.config.netWorth).toBe(500000)
  })

  it('fetches immediately when net worth is already known on mount', async () => {
    authFetch.mockImplementation((input) => response(fireRow(Number(String(input).split('=')[1]))))
    const { result } = renderHook(() => useOutlookFire(250000, 1, false))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.config.netWorth).toBe(250000)
    expect(result.current.config.monthlyRetirementSpend).toBe(6000)
  })

  // MATH-28: a missing date_of_birth reports current_age/on_track as null from /api/fire
  // (never a fabricated 0/age-0 trajectory). The Outlook's own Monte-Carlo/trajectory math
  // (FireConfig.currentAge) needs SOME definite age, so a null degrades to the persona
  // default (38) here rather than the previous silent 0.
  it('a null current_age from the API degrades to the default age, never 0', async () => {
    authFetch.mockImplementation(() =>
      response({
        ...fireRow(500000),
        current_age: null,
        projected_fire_age: null,
        on_track: null,
      }),
    )
    const { result } = renderHook(() => useOutlookFire(500000, 1, false))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.config.currentAge).not.toBe(0)
    expect(result.current.config.currentAge).toBeGreaterThan(0)
  })

  it('does not mark a new fire version applied until its fresh response arrives', async () => {
    authFetch.mockImplementation(() => response(fireRow(500000)))
    const { result, rerender } = renderHook(
      ({ fireVersion }) => useOutlookFire(500000, fireVersion, false),
      { initialProps: { fireVersion: 1 } },
    )
    await waitFor(() => expect(result.current.configVersion).toBe(1))

    let finishRefresh!: (value: Response) => void
    authFetch.mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          finishRefresh = resolve
        }),
    )
    rerender({ fireVersion: 2 })
    await waitFor(() => expect(authFetch).toHaveBeenCalledTimes(2))
    expect(result.current.configVersion).toBe(1)

    await act(async () => {
      finishRefresh(
        new Response(
          JSON.stringify({ ...fireRow(500000), monthly_retirement_spending: 25_000_000 }),
          { status: 200 },
        ),
      )
    })
    await waitFor(() => expect(result.current.configVersion).toBe(2))
    expect(result.current.config.monthlyRetirementSpend).toBe(25_000_000)
  })

  it('never promotes stale cached FIRE data after a failed version refresh', async () => {
    authFetch.mockImplementation(() => response(fireRow(500000)))
    const { result, rerender } = renderHook(
      ({ fireVersion }) => useOutlookFire(500000, fireVersion, false),
      { initialProps: { fireVersion: 1 } },
    )
    await waitFor(() => expect(result.current.configVersion).toBe(1))

    authFetch.mockRejectedValue(new Error('offline'))
    rerender({ fireVersion: 2 })
    await waitFor(() => expect(result.current.error).toBe(true))
    expect(result.current.configVersion).toBe(1)
    expect(result.current.config.monthlyRetirementSpend).toBe(6_000)

    authFetch.mockImplementation(() =>
      response({ ...fireRow(500000), monthly_retirement_spending: 25_000_000 }),
    )
    act(() => result.current.retry())
    await waitFor(() => expect(result.current.configVersion).toBe(2))
    expect(result.current.config.monthlyRetirementSpend).toBe(25_000_000)
  })
  it('a version refresh never joins a fetch that left before the write', async () => {
    // A write elsewhere started a refetch with the OLD row; a profile edit then lands and
    // advances fireVersion while that request is still out. Joining it would hand back the
    // pre-edit row, which the Outlook then treats as "nothing changed" and drops the edit.
    const url = '/api/fire?current_nw=500000'
    authFetch.mockImplementation(() => response(fireRow(500000)))
    const { result, rerender } = renderHook(
      ({ fireVersion }) => useOutlookFire(500000, fireVersion, false),
      { initialProps: { fireVersion: 1 } },
    )
    await waitFor(() => expect(result.current.configVersion).toBe(1))

    let finishStale!: (value: Response) => void
    authFetch.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          finishStale = resolve
        }),
    )
    const stale = fetchCachedQuery(url, async () => (await authFetch(url)).json())
    authFetch.mockImplementation(() =>
      response({ ...fireRow(500000), monthly_retirement_spending: 25_000_000 }),
    )
    rerender({ fireVersion: 2 })
    await waitFor(() => expect(result.current.configVersion).toBe(2))
    expect(result.current.config.monthlyRetirementSpend).toBe(25_000_000)

    await act(async () => {
      finishStale(new Response(JSON.stringify(fireRow(500000)), { status: 200 }))
      await stale
    })
    expect(result.current.config.monthlyRetirementSpend).toBe(25_000_000)
  })
})
