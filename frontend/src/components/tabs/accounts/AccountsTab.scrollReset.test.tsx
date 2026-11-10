// @vitest-environment jsdom
//
// REG2 regression: `.acct-main` (the register's vertical scroll container, ref'd by
// AccountsTab's mainRef) must reset scrollTop to 0 whenever a FRESH (non-append) fetch is
// triggered by a filter/account/date change — otherwise a shrunken result set can leave the
// onScroll "near bottom" check immediately true, spuriously firing loadMore() right after the
// user changes the view. It must NOT reset on a loadMore (append) fetch — that's the same view,
// just more rows appended at the bottom, and the user's scroll position is still meaningful.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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

function makeTxn(id: string): ApiTxn {
  return {
    id,
    date: '2026-06-01',
    kind: 'expense',
    category: 'food',
    description: '',
    base_amount: 5,
    base_currency: 'USD',
    cleared: true,
    from_account_id: 'acc-1',
    from_amount: 5,
    from_currency: 'USD',
  }
}

function setupMocks() {
  let txnCallCount = 0
  mockAuthFetch.mockImplementation((url: string) => {
    if (url.startsWith('/api/accounts')) return Promise.resolve(okJson([ACCOUNT]))
    if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
    if (url.startsWith('/api/payees')) return Promise.resolve(okJson({ payees: [] }))
    if (url.startsWith('/api/portfolio')) return Promise.resolve(okJson([]))
    if (url.startsWith('/api/prices')) return Promise.resolve(okJson({}))
    if (url.startsWith('/api/transactions')) {
      // Unique id per call (a loadMore append reuses the same list — a repeated id would collide
      // React keys). total > the loaded page, has_more true — loadMore is a real, useful fetch.
      txnCallCount += 1
      return Promise.resolve(
        okJson({ transactions: [makeTxn(`t${txnCallCount}`)], total: 200, has_more: true }),
      )
    }
    return Promise.resolve(okJson({}))
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  setupMocks()
  // AccountsTab auto-collapses the account sidebar when the viewport is too narrow to fit both
  // it and the register (see the COLLAPSE_AT check in AccountsTab.tsx) — jsdom's default
  // window.innerWidth (1024) is below that threshold, which would hide the Sidebar entirely and
  // make the account row unreachable. Widen it so the Sidebar (and its account rows) render.
  Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1400 })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function transactionsCallCount(): number {
  return mockAuthFetch.mock.calls.filter(([u]) => String(u).startsWith('/api/transactions')).length
}

describe('AccountsTab — .acct-main scroll reset on fresh vs. append fetches (REG2)', () => {
  it('resets scrollTop to 0 when selecting an account (a fresh, non-append fetch)', async () => {
    render(<AccountsTab />)

    await waitFor(() => expect(screen.getAllByText('Checking').length).toBeGreaterThanOrEqual(1))

    const main = document.querySelector('.acct-main') as HTMLElement
    expect(main).toBeTruthy()
    main.scrollTop = 500
    expect(main.scrollTop).toBe(500)

    const row = screen
      .getAllByText('Checking')
      .map((el) => el.closest('.acct-row'))
      .find(Boolean)
    expect(row).toBeTruthy()
    act(() => {
      fireEvent.click(row!)
    })

    // The scroll reset happens synchronously inside the same effect that applies the account
    // selection — no need to wait for any fetch to resolve.
    expect(main.scrollTop).toBe(0)
  })

  it('does NOT reset scrollTop when loadMore (an append fetch) fires', async () => {
    render(<AccountsTab />)

    await waitFor(() => expect(screen.getAllByText('Checking').length).toBeGreaterThanOrEqual(1))
    // Wait for the register's own txn fetch to land too (not just the account list) — the
    // count footer only renders once transactions/total/hasMore are all in state, which is what
    // loadMore's guard (`!hasMore`) actually depends on.
    await waitFor(() => expect(document.querySelector('.acct-reg-count')).toBeTruthy())

    const main = document.querySelector('.acct-main') as HTMLElement
    main.scrollTop = 500

    const callsBefore = transactionsCallCount()

    // Fire a scroll event and let the 160ms debounce settle so the onScroll "near bottom" check
    // runs and calls loadMore() (jsdom reports scrollHeight/clientHeight as 0, so the "within
    // 800px of the bottom" check is trivially true — exactly the failure mode REG2 describes).
    await act(async () => {
      fireEvent.scroll(main)
      await new Promise((r) => setTimeout(r, 250))
    })

    // Confirm loadMore's append fetch actually fired — otherwise this test would trivially pass.
    await waitFor(() => expect(transactionsCallCount()).toBeGreaterThan(callsBefore))

    expect(main.scrollTop).toBe(500)
  })

  it('does NOT reset scrollTop on an unrelated payees/refreshSignal-triggered reload (code-review follow-up)', async () => {
    // Code-review finding: the filter/account/date effect used to also list `payees` as a
    // dependency (a REG1 leftover, needed only so `resolvePayeeId`'s closure stayed current) —
    // but `payees` gets a brand-new array reference on every loadCore() run, including one fired
    // by `refreshSignal` (bumped by ANY AI/portfolio/ledger mutation anywhere in the app, wholly
    // unrelated to the register). That made the new scrollTop-reset line fire on every such
    // refresh too. Reproduces the reviewer's manual repro: bump refreshSignal with no
    // filter/account/date change and confirm scrollTop survives.
    const payeesCallCount = () =>
      mockAuthFetch.mock.calls.filter(([u]) => String(u).startsWith('/api/payees')).length

    const { rerender } = render(<AccountsTab refreshSignal={0} />)
    await waitFor(() => expect(screen.getAllByText('Checking').length).toBeGreaterThanOrEqual(1))

    const main = document.querySelector('.acct-main') as HTMLElement
    main.scrollTop = 500

    const payeesCallsBefore = payeesCallCount()

    // Bump refreshSignal — mirrors an unrelated AI mutation elsewhere triggering L.refresh().
    await act(async () => {
      rerender(<AccountsTab refreshSignal={1} />)
      await Promise.resolve()
      await Promise.resolve()
    })

    // Confirm the refresh actually re-ran loadCore (a fresh `payees` fetch/reference) — otherwise
    // this test would trivially pass without exercising the bug at all.
    await waitFor(() => expect(payeesCallCount()).toBeGreaterThan(payeesCallsBefore))

    expect(main.scrollTop).toBe(500)
  })
})

describe('AccountsTab — refreshSignal on remount', () => {
  // Owner report 2026-10-27: on mobile, Accounts remounts on every return from Lucius while the
  // shell's refreshSignal is already nonzero; reacting to that stale value ran a second full
  // reload (and, with the data-changed bar, would flash it) on every return.
  const registerFetches = () =>
    mockAuthFetch.mock.calls.filter(([u]) => String(u).startsWith('/api/transactions?')).length

  it('mounting with an old nonzero refreshSignal loads once; only a later bump reloads', async () => {
    const { rerender } = render(<AccountsTab refreshSignal={5} />)
    await waitFor(() => expect(registerFetches()).toBe(1))
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50))
    })
    expect(registerFetches()).toBe(1)

    await act(async () => {
      rerender(<AccountsTab refreshSignal={6} />)
    })
    await waitFor(() => expect(registerFetches()).toBe(2))
  })
})
