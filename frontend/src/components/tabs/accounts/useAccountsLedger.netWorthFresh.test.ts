// @vitest-environment jsdom
//
// Regression (hand UAT, 2026-11-10): deleting an account left the sidebar's Net worth at the
// old figure until a reload. A REST write is never broadcast, and the writes here refreshed the
// account list but not the cached net worth. Every write now marks it stale too.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useAccountsLedger } from './useAccountsLedger'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

const invalidated: Array<(key: string) => boolean> = []
vi.mock('../../../state/queryCache', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../state/queryCache')>()
  return {
    ...actual,
    invalidateQueryCache: (matcher: string | ((key: string) => boolean)) => {
      invalidated.push(typeof matcher === 'string' ? (k) => k.startsWith(matcher) : matcher)
      return actual.invalidateQueryCache(matcher)
    },
  }
})

function okJson(body: unknown): Response {
  return { ok: true, json: async () => body } as Response
}

beforeEach(() => {
  invalidated.length = 0
  mockAuthFetch.mockReset()
  mockAuthFetch.mockImplementation((url: string, init?: RequestInit) => {
    if (url.startsWith('/api/accounts/acc-old') && init?.method === 'DELETE')
      return Promise.resolve(okJson({ ok: true }))
    if (url.startsWith('/api/accounts')) return Promise.resolve(okJson([]))
    if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
    if (url.startsWith('/api/payees')) return Promise.resolve(okJson({ payees: [] }))
    if (url.startsWith('/api/portfolio')) return Promise.resolve(okJson([]))
    if (url.startsWith('/api/prices')) return Promise.resolve(okJson({}))
    if (url.startsWith('/api/net-worth')) return Promise.resolve(okJson({ net_worth: 0 }))
    if (url.startsWith('/api/transactions'))
      return Promise.resolve(okJson({ transactions: [], total: 0, has_more: false }))
    return Promise.resolve(okJson({}))
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useAccountsLedger — net worth after an account write', () => {
  it('marks the cached net worth stale when an account is deleted', async () => {
    const { result } = renderHook(() => useAccountsLedger())
    await waitFor(() => expect(result.current.hasLoaded).toBe(true))
    invalidated.length = 0

    await act(async () => {
      await result.current.deleteAccount('acc-old')
    })

    expect(invalidated.some((matches) => matches('/api/net-worth'))).toBe(true)
  })
})
