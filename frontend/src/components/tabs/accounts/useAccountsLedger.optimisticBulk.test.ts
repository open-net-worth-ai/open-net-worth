// @vitest-environment jsdom
//
// Regression: the bulk status actions (Mark as Cleared / Uncleared / Reviewed) wrote through
// POST /api/transactions/bulk-update and then re-fetched the WHOLE register before anything
// moved on screen — two round-trips of dead time with no spinner, and `setOffset(0)` threw away
// every extra page the user had scrolled in. A failure was worse than slow: no call site had a
// `.catch()`, so a rejected write surfaced as an unhandled promise rejection with zero feedback
// and rows still showing their old state.
//
// The flag flips are now optimistic — painted locally on tap, restored if the write loses (or
// if the server refuses individual rows via not_found/skipped) — while server-derived changes
// (category, move) still take the refetch path because their stored result can't be predicted.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useAccountsLedger, localBulkPatch, normalizeBulkResult } from './useAccountsLedger'
import type { ApiTxn } from './types'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown): Response {
  return { ok: true, json: async () => body } as Response
}

function makeTxn(id: string, over: Partial<ApiTxn> = {}): ApiTxn {
  return {
    id,
    date: '2026-06-15',
    kind: 'expense',
    category: 'food',
    description: '',
    base_amount: 10,
    base_currency: 'SGD',
    cleared: false,
    needs_review: true,
    from_account_id: 'acc-1',
    from_amount: 10,
    from_currency: 'SGD',
    ...over,
  } as ApiTxn
}

const ROWS = [makeTxn('txn-1'), makeTxn('txn-2'), makeTxn('txn-3')]

/** Counts register fetches so a test can prove the optimistic path did NOT re-page. */
let txnListFetches = 0
/** What the register endpoint serves; a test swaps it to model a write landing on the server. */
let serverRows: ApiTxn[] = ROWS

function mockCore(url: string): Promise<Response> | null {
  if (url.startsWith('/api/accounts')) return Promise.resolve(okJson([]))
  if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
  if (url.startsWith('/api/payees')) return Promise.resolve(okJson({ payees: [] }))
  if (url.startsWith('/api/portfolio')) return Promise.resolve(okJson([]))
  if (url.startsWith('/api/prices')) return Promise.resolve(okJson({}))
  if (url.startsWith('/api/transactions?')) {
    txnListFetches++
    return Promise.resolve(
      okJson({ transactions: serverRows, total: serverRows.length, has_more: false }),
    )
  }
  return null
}

beforeEach(() => {
  mockAuthFetch.mockReset()
  txnListFetches = 0
  serverRows = ROWS
})
afterEach(() => vi.restoreAllMocks())

/** Boots the hook with ROWS loaded and a controllable bulk-update responder. */
async function boot(bulk: (body: unknown) => Promise<Response>) {
  mockAuthFetch.mockImplementation((url: string, init?: RequestInit) => {
    const core = mockCore(url)
    if (core) return core
    if (url === '/api/transactions/bulk-update') return bulk(JSON.parse(String(init?.body)))
    throw new Error(`unexpected fetch: ${url}`)
  })
  const { result } = renderHook(() => useAccountsLedger())
  await waitFor(() => expect(result.current.hasLoaded).toBe(true))
  await waitFor(() => expect(result.current.transactions).toHaveLength(3))
  return result
}

describe('localBulkPatch — which changes are safe to paint locally', () => {
  it('maps the status flags onto the client row shape', () => {
    expect(localBulkPatch({ cleared: true })).toEqual({ cleared: true })
    expect(localBulkPatch({ cleared: false })).toEqual({ cleared: false })
    expect(localBulkPatch({ needs_review: false })).toEqual({ needsReview: false })
  })

  it('refuses server-derived changes so they take the refetch path', () => {
    // A category runs the precedence chain, a move re-FXes the leg, a payee resolves an
    // entity — guessing any of these would paint a row the server never stored.
    expect(localBulkPatch({ category: 'cat_abc' })).toBeNull()
    expect(localBulkPatch({ to_account_id: 'acc-2' })).toBeNull()
    expect(localBulkPatch({ payee_id: 'pay-1' })).toBeNull()
    expect(localBulkPatch({ tags: ['x'] })).toBeNull()
    expect(localBulkPatch({ cleared: true, category: 'cat_abc' })).toBeNull()
    expect(localBulkPatch({})).toBeNull()
  })
})

describe('normalizeBulkResult', () => {
  it('reads the per-id summary', () => {
    const r = normalizeBulkResult(
      { updated: ['a'], not_found: ['b'], skipped: [{ id: 'c', reason: 'adjustment' }], count: 1 },
      ['a', 'b', 'c'],
    )
    expect(r.count).toBe(1)
    expect(r.not_found).toEqual(['b'])
    expect(r.skipped.map((s) => s.id)).toEqual(['c'])
  })

  it('treats a body with no `updated` as "all of them" rather than reverting every row', () => {
    expect(normalizeBulkResult({}, ['a', 'b'])).toEqual({
      updated: ['a', 'b'],
      not_found: [],
      skipped: [],
      count: 2,
    })
  })
})

describe('useAccountsLedger.bulkUpdate — optimistic status flips', () => {
  it('paints cleared on the rows BEFORE the request resolves', async () => {
    let release!: () => void
    const gate = new Promise<void>((r) => (release = r))
    const result = await boot(async () => {
      await gate
      return okJson({ updated: ['txn-1', 'txn-2'], not_found: [], skipped: [], count: 2 })
    })

    let pending!: Promise<unknown>
    await act(async () => {
      pending = result.current.bulkUpdate(['txn-1', 'txn-2'], { cleared: true })
    })

    // The write has NOT resolved, yet the register already shows the new state.
    const byId = Object.fromEntries(result.current.transactions.map((t) => [t.id, t]))
    expect(byId['txn-1']!.cleared).toBe(true)
    expect(byId['txn-2']!.cleared).toBe(true)
    expect(byId['txn-3']!.cleared).toBe(false)

    await act(async () => {
      release()
      await pending
    })
    expect(result.current.transactions.every((t) => t.id === 'txn-3' || t.cleared)).toBe(true)
  })

  it('clears needsReview optimistically and reports the server count', async () => {
    // A review flip refetches the register, so the server's copy has to carry the write too.
    const result = await boot(async () => {
      serverRows = ROWS.map((t) =>
        t.id === 'txn-1' ? ({ ...t, needs_review: false } as ApiTxn) : t,
      )
      return okJson({ updated: ['txn-1'], not_found: [], skipped: [], count: 1 })
    })

    let res!: Awaited<ReturnType<typeof result.current.bulkUpdate>>
    await act(async () => {
      res = await result.current.bulkUpdate(['txn-1'], { needs_review: false })
    })

    expect(res.count).toBe(1)
    expect(result.current.transactions.find((t) => t.id === 'txn-1')!.needsReview).toBe(false)
    expect(result.current.transactions.find((t) => t.id === 'txn-2')!.needsReview).toBe(true)
  })

  it('does NOT re-page the register on the optimistic path', async () => {
    const result = await boot(async () =>
      okJson({ updated: ['txn-1'], not_found: [], skipped: [], count: 1 }),
    )
    const before = txnListFetches

    await act(async () => {
      await result.current.bulkUpdate(['txn-1'], { cleared: true })
    })

    // A refetch here would reset offset to 0 and drop any scrolled-in pages.
    expect(txnListFetches).toBe(before)
  })

  it('reverts every row and rejects when the write fails', async () => {
    const result = await boot(async () => ({ ok: false, text: async () => 'boom' }) as Response)

    await expect(
      act(async () => {
        await result.current.bulkUpdate(['txn-1', 'txn-2'], { cleared: true })
      }),
    ).rejects.toThrow('boom')

    expect(result.current.transactions.every((t) => t.cleared === false)).toBe(true)
  })

  it('reverts only the rows the server refused, keeping the ones it wrote', async () => {
    const result = await boot(async () =>
      okJson({
        updated: ['txn-1'],
        not_found: ['txn-2'],
        skipped: [{ id: 'txn-3', reason: 'adjustment rows take no category' }],
        count: 1,
      }),
    )

    await act(async () => {
      await result.current.bulkUpdate(['txn-1', 'txn-2', 'txn-3'], { cleared: true })
    })

    const byId = Object.fromEntries(result.current.transactions.map((t) => [t.id, t]))
    expect(byId['txn-1']!.cleared).toBe(true)
    expect(byId['txn-2']!.cleared).toBe(false)
    expect(byId['txn-3']!.cleared).toBe(false)
  })

  it('still refetches for a server-derived change (category)', async () => {
    const result = await boot(async () =>
      okJson({ updated: ['txn-1'], not_found: [], skipped: [], count: 1 }),
    )
    const before = txnListFetches

    await act(async () => {
      await result.current.bulkUpdate(['txn-1'], { category: 'cat_abc' })
    })

    expect(txnListFetches).toBeGreaterThan(before)
  })
})
