// @vitest-environment jsdom
//
// Regression (2026-11-18): the Dashboard showed a net worth headline and a "this month" change
// that didn't reconcile — the change ran to a figure $1,509 off the headline. Live prices land
// with `GET /api/portfolio`, but a price write sends no data_changed event, so a net worth read
// made before they landed stayed on screen beside holdings valued after. When a live refresh
// moves the portfolio total, both net worth reads must refresh; when it doesn't, neither may.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, renderHook, waitFor } from '@testing-library/react'

vi.mock('../api/client', () => ({ authFetch: vi.fn() }))
const invalidated = vi.hoisted(() => ({ keys: [] as string[] }))
vi.mock('../state/queryCache', async (importActual) => {
  const actual = await importActual<typeof import('../state/queryCache')>()
  return {
    ...actual,
    invalidateQueryCache: (matcher: string | ((key: string) => boolean)) => {
      for (const key of ['/api/net-worth', '/api/net-worth/stats', '/api/portfolio/analytics']) {
        if (typeof matcher === 'string' ? key.startsWith(matcher) : matcher(key)) {
          invalidated.keys.push(key)
        }
      }
    },
  }
})

import { authFetch } from '../api/client'
import { clearQueryCache } from '../state/queryCache'
import { usePortfolio } from './usePortfolio'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function body(total: number, isLive: boolean) {
  return {
    base_currency: 'USD',
    rows: [{ ticker: 'ACME' }],
    portfolio_total: total,
    unvested_base: 0,
    holdings_by_account: {},
    is_live: isLive,
    fx: { USD: 1 },
  }
}

function serve(lastKnownTotal: number, liveTotal: number) {
  mockAuthFetch.mockImplementation((url: string) =>
    Promise.resolve({
      ok: true,
      json: async () =>
        url === '/api/portfolio?live=0' ? body(lastKnownTotal, false) : body(liveTotal, true),
    } as Response),
  )
}

beforeEach(() => {
  mockAuthFetch.mockReset()
  invalidated.keys = []
  localStorage.clear()
  clearQueryCache()
})

afterEach(() => cleanup())

describe('usePortfolio — live prices refresh the net worth reads', () => {
  it('refreshes net worth and its history when live prices move the total', async () => {
    serve(1000, 1012.5)
    const { result } = renderHook(() => usePortfolio())
    await waitFor(() => expect(result.current.isLive).toBe(true))
    expect(invalidated.keys.sort()).toEqual(['/api/net-worth', '/api/net-worth/stats'])
  })

  it('leaves them alone when the live prices change nothing', async () => {
    serve(1000, 1000)
    const { result } = renderHook(() => usePortfolio())
    await waitFor(() => expect(result.current.isLive).toBe(true))
    expect(invalidated.keys).toEqual([])
  })
})
