// @vitest-environment jsdom
//
// Owner report 2026-10-27: on mobile, returning to Accounts from Lucius remounts the tab, which
// paints the cached rows and revalidates the SAME query — and the filter-refetch bar fired for
// it, so every return looked like a reload. `reloadingRows` is true only while a DIFFERENT query
// is about to replace the rows on screen.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { buildTxnQueryString, useAccountsLedger } from './useAccountsLedger'
import { clearQueryCache, setCachedQueryData } from '../../../state/queryCache'
import type { ApiTxn, TxnFilters } from './types'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown): Response {
  return { ok: true, json: async () => body } as Response
}

const txn = (id: string): ApiTxn => ({
  id,
  date: '2026-10-01',
  kind: 'expense',
  category: null,
  description: '',
  base_amount: 5,
  base_currency: 'SGD',
  cleared: true,
  from_account_id: 'acc-1',
  from_amount: 5,
  from_currency: 'SGD',
})

const DEFAULTS: TxnFilters = {
  accountId: null,
  accountIds: [],
  search: '',
  qField: 'any',
  kind: [],
  category: [],
  payeeIds: [],
  tags: [],
  source: [],
  dateFrom: '',
  dateTo: '',
  cleared: null,
  needsReview: false,
  amounts: [],
  uncategorized: false,
  batchId: null,
}

let releaseTxns: Array<() => void> = []

beforeEach(() => {
  clearQueryCache()
  releaseTxns = []
  mockAuthFetch.mockReset()
  mockAuthFetch.mockImplementation((url: string) => {
    if (url.startsWith('/api/accounts')) return Promise.resolve(okJson([]))
    if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
    if (url.startsWith('/api/payees')) return Promise.resolve(okJson({ payees: [] }))
    if (url.startsWith('/api/portfolio')) return Promise.resolve(okJson({}))
    if (url.startsWith('/api/transactions')) {
      return new Promise<Response>((resolve) =>
        releaseTxns.push(() => resolve(okJson({ transactions: [txn('t2')], total: 1 }))),
      )
    }
    return Promise.resolve(okJson({}))
  })
})

afterEach(() => vi.restoreAllMocks())

describe('useAccountsLedger — reloadingRows', () => {
  it('a remount over cached rows revalidates silently; a filter change shows reloadingRows', async () => {
    // What a previous visit to the tab left behind.
    setCachedQueryData('/api/accounts', [])
    setCachedQueryData(`/api/transactions?${buildTxnQueryString(DEFAULTS, 0)}`, {
      transactions: [txn('t1')],
      total: 1,
    })

    const { result } = renderHook(() => useAccountsLedger())
    expect(result.current.transactions.map((t) => t.id)).toEqual(['t1'])
    await waitFor(() => expect(result.current.loadingTxns).toBe(true))
    expect(result.current.reloadingRows).toBe(false)
    act(() => releaseTxns.shift()!())
    await waitFor(() => expect(result.current.loadingTxns).toBe(false))

    act(() => result.current.setFilters({ search: 'grab' }))
    await waitFor(() => expect(result.current.reloadingRows).toBe(true))
    act(() => releaseTxns.shift()!())
    await waitFor(() => expect(result.current.reloadingRows).toBe(false))
    expect(result.current.loadingTxns).toBe(false)
  })
  it('a reload because the data changed (Lucius wrote) shows reloadingRows for the same query', async () => {
    const { result } = renderHook(() => useAccountsLedger())
    await waitFor(() => expect(releaseTxns.length).toBe(1))
    act(() => releaseTxns.shift()!())
    await waitFor(() => expect(result.current.loadingTxns).toBe(false))

    let done!: Promise<void>
    act(() => {
      done = result.current.refresh()
    })
    await waitFor(() => expect(result.current.reloadingRows).toBe(true))
    act(() => releaseTxns.shift()!())
    await act(() => done)
    expect(result.current.reloadingRows).toBe(false)
  })
})
