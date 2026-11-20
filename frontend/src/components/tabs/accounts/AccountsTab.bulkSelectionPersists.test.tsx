// @vitest-environment jsdom
//
// Two owner-reported bugs from the same flow (2026-07-31), fixed together:
//
//  1. "after i tap on mark all as reviewed, and it's completed, when i select all again it's
//     still showing mark as reviewed option" — the menu item was rendered unconditionally, so
//     it stayed on offer with nothing left to review. Tapping it was a no-op that still cost a
//     round trip.
//  2. "the bulk selection bar shouldn't disappear though? after completing one process, cause
//     user might want to do other things" — every bulk action cleared the selection, dropping
//     the bar and forcing a full re-select to do the next thing.
//
// The rows are still on screen and still the set being worked on, so a status/label change now
// KEEPS the selection; only an action that removes rows from this view (move, delete) clears
// it. And an action that would change nothing is disabled with a reason rather than hidden —
// hiding it makes the menu change height between selections.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react'
import { AccountsTab } from './AccountsTab'
import type { ApiAccount, ApiTxn } from './types'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown): Response {
  return { ok: true, json: async () => body } as Response
}

const ACCOUNT: ApiAccount = {
  id: 'acc-1',
  name: 'Checking',
  kind: 'asset',
  type: 'cash',
  currency: 'USD',
  balance: 1000,
  balance_base: 1000,
  cleared_balance: 1000,
  cleared_balance_base: 1000,
}

function makeTxn(id: string, needsReview: boolean): ApiTxn {
  return {
    id,
    date: '2026-06-01',
    kind: 'expense',
    category: null,
    description: 'Coffee ' + id,
    base_amount: 5,
    base_currency: 'USD',
    cleared: false,
    needs_review: needsReview,
    from_account_id: 'acc-1',
    from_amount: 5,
    from_currency: 'USD',
  } as ApiTxn
}

/** Ids the bulk-update endpoint was last asked to change. */
let lastBulkIds: string[] | null = null
let bulkCalls = 0

function setupMocks(initial: ApiTxn[]) {
  lastBulkIds = null
  bulkCalls = 0
  // What the server holds: a review flip refetches the register, so the write has to land here.
  let rows = initial
  mockAuthFetch.mockImplementation((url: string, init?: RequestInit) => {
    if (url === '/api/transactions/bulk-update') {
      bulkCalls += 1
      const body = JSON.parse(String(init?.body)) as { ids: string[]; needs_review?: boolean }
      lastBulkIds = body.ids
      if (body.needs_review !== undefined)
        rows = rows.map((t) =>
          body.ids.includes(t.id) ? ({ ...t, needs_review: body.needs_review } as ApiTxn) : t,
        )
      return Promise.resolve(
        okJson({ updated: body.ids, not_found: [], skipped: [], count: body.ids.length }),
      )
    }
    if (url.startsWith('/api/accounts')) return Promise.resolve(okJson([ACCOUNT]))
    if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
    if (url.startsWith('/api/payees')) return Promise.resolve(okJson({ payees: [] }))
    if (url.startsWith('/api/portfolio')) return Promise.resolve(okJson([]))
    if (url.startsWith('/api/prices')) return Promise.resolve(okJson({}))
    if (url.startsWith('/api/transactions'))
      return Promise.resolve(okJson({ transactions: rows, total: rows.length, has_more: false }))
    return Promise.resolve(okJson({}))
  })
}

beforeEach(() => vi.clearAllMocks())
afterEach(() => cleanup())

const selectAll = () => fireEvent.click(document.querySelector('.acct-reg-head .acct-cb')!)
const bulkBar = () => document.querySelector('.acct-bulk') as HTMLElement | null
// The account header carries its own "More"/review controls — scope every query to the bulk bar.
const inBar = () => within(bulkBar()!)
const openMore = () => fireEvent.click(inBar().getByRole('button', { name: /More/ }))
const item = (name: RegExp) => inBar().getByRole('button', { name })
const reviewItem = () => item(/Mark as Reviewed/i)

async function boot(rows: ApiTxn[]) {
  setupMocks(rows)
  render(<AccountsTab />)
  await waitFor(() => expect(document.querySelector('.acct-reg-head .acct-cb')).toBeTruthy())
  await waitFor(() => expect(document.querySelectorAll('.acct-tr').length).toBe(rows.length))
}

describe('Bulk bar — the selection survives a completed action', () => {
  it('keeps the bar up after Mark as Reviewed so the next action needs no re-select', async () => {
    await boot([makeTxn('t1', true), makeTxn('t2', true)])

    selectAll()
    expect(bulkBar()).toBeTruthy()
    openMore()
    fireEvent.click(reviewItem())

    await waitFor(() => expect(bulkCalls).toBe(1))
    // The bar used to vanish here, taking the selection with it.
    expect(bulkBar()).toBeTruthy()
    expect(inBar().getByText(/2 Transactions/)).toBeTruthy()
  })

  it('disables Mark as Reviewed once nothing in the selection needs review', async () => {
    await boot([makeTxn('t1', true), makeTxn('t2', true)])

    selectAll()
    openMore()
    expect(reviewItem()).toHaveProperty('disabled', false)
    fireEvent.click(reviewItem())
    await waitFor(() => expect(bulkCalls).toBe(1))

    // Re-open the menu on the SAME still-selected rows — the action is now a no-op.
    openMore()
    const review = reviewItem()
    expect(review).toHaveProperty('disabled', true)
    expect(review.getAttribute('title')).toMatch(/already reviewed/i)

    // And it cannot fire a second, pointless round trip.
    fireEvent.click(review)
    expect(bulkCalls).toBe(1)
  })

  it('sends only the rows that still need review, not the whole selection', async () => {
    await boot([makeTxn('t1', true), makeTxn('t2', false), makeTxn('t3', true)])

    selectAll()
    openMore()
    fireEvent.click(reviewItem())

    await waitFor(() => expect(lastBulkIds).toBeTruthy())
    expect(lastBulkIds!.sort()).toEqual(['t1', 't3'])
  })

  it('leaves Mark as Reviewed enabled when only SOME of the selection is reviewed', async () => {
    await boot([makeTxn('t1', true), makeTxn('t2', false)])

    selectAll()
    openMore()
    expect(reviewItem()).toHaveProperty('disabled', false)
  })

  it('disables Mark as Uncleared when no selected row is cleared', async () => {
    await boot([makeTxn('t1', true), makeTxn('t2', true)])

    selectAll()
    openMore()
    // Both rows are uncleared: "Cleared" is live, its inverse is dead.
    expect(item(/Mark as Cleared/i)).toHaveProperty('disabled', false)
    const unclear = item(/Mark as Uncleared/i)
    expect(unclear).toHaveProperty('disabled', true)
    expect(unclear.getAttribute('title')).toMatch(/no selected transaction is cleared/i)
  })
})
